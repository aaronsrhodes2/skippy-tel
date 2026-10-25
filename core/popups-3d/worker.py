"""One image_to_3d job in its own process, so ROCm hands every byte back when it exits (app.py runs it; so can a person):

    python3 worker.py image_to_3d --image in.png --out out.glb [--shape full|mini] [--no-texture] [--seed N]

Hunyuan3D-2 (Tencent): the background comes off (hy3dgen's remover), the shape DiT (2.0-turbo, or 2mini-turbo) makes a mesh,
floaters and degenerate faces go, the mesh is cut to FACES faces, then Paint (2.0-turbo, with the delight model) paints a
texture from six views and bakes it into the .glb. The last stdout line is JSON: seconds per stage, faces, peak GiB of GTT
(the iGPU's share of the 128 GB) above where it started.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import threading
import time

MODELS = os.environ.get("MODELS", "/models")
HF = os.path.join(MODELS, "huggingface")
HY3D_CODE = os.environ.get("HY3D_CODE", "/opt/hy3d")
STEPS = int(os.environ.get("SHAPE_STEPS", "5"))           # the turbo DiTs are distilled for ~5 steps
OCTREE = int(os.environ.get("OCTREE", "380"))
FACES = int(os.environ.get("FACES", "40000"))              # enough for a mini or a prop; Paint's UV unwrap slows past ~50k
FLASHVDM = os.environ.get("FLASHVDM", "1") == "1"         # Hunyuan's fast decoder: the volume decode is most of the shape time
SHAPES = {"full": ("tencent/Hunyuan3D-2", "hunyuan3d-dit-v2-0-turbo"),
          "mini": ("tencent/Hunyuan3D-2mini", "hunyuan3d-dit-v2-mini-turbo")}


def model_dir() -> str:
    """hy3dgen finds weights under $HY3DGEN_MODELS/<org>/<repo>; the archive keeps them as huggingface/<org>__<repo>."""
    root = os.environ.get("HY3DGEN_MODELS") or "/tmp/hy3d-models"
    for repo in ("tencent/Hunyuan3D-2", "tencent/Hunyuan3D-2mini"):
        link = os.path.join(root, repo)
        if not os.path.exists(link):
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(os.path.join(HF, repo.replace("/", "__")), link)
    os.environ["HY3DGEN_MODELS"] = root
    return root


class Peak:
    """Tracks the highest GTT use above the starting point, from the driver's sysfs file, twice a second."""

    def __init__(self):
        paths = sorted(glob.glob("/sys/class/drm/card*/device/mem_info_gtt_used"))
        self.path = paths[0] if paths else None
        self.base = self.read()
        self.top = 0
        threading.Thread(target=self._run, daemon=True).start()

    def read(self) -> int:
        try:
            with open(self.path, encoding="ascii") as f:
                return int(f.read())
        except (TypeError, OSError, ValueError):
            return 0

    def _run(self):
        while True:
            self.top = max(self.top, self.read() - self.base)
            time.sleep(0.5)

    def gib(self) -> float:
        return round(self.top / 2**30, 1)


def log(msg: str) -> None:
    print(f"[worker] {msg}", file=sys.stderr, flush=True)


def image_to_3d(a) -> dict:
    import torch
    # MIOpen's convolutions on gfx1151 ran single kernels so long that the language models' Vulkan work on the same iGPU timed
    # out (amdgpu ring resets in llama-server). PyTorch's own convolutions (GEMMs, short kernels) leave room for them.
    torch.backends.cudnn.enabled = os.environ.get("MIOPEN", "0") == "1"
    from PIL import Image
    sys.path.insert(0, HY3D_CODE)
    from hy3dgen.rembg import BackgroundRemover
    from hy3dgen.shapegen import DegenerateFaceRemover, FaceReducer, FloaterRemover, Hunyuan3DDiTFlowMatchingPipeline

    model_dir()
    peak, stats, t = Peak(), {}, time.time()
    img = Image.open(a.image)
    if img.mode != "RGBA" or img.getextrema()[3][0] == 255:            # no real alpha: take the background off
        img = BackgroundRemover()(img.convert("RGB"))
    stats["rembg_s"] = round(time.time() - t, 1)
    log(f"background off in {stats['rembg_s']} s")

    repo, sub = SHAPES[a.shape]
    t = time.time()
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(repo, subfolder=sub, use_safetensors=True, device="cuda")
    if FLASHVDM:
        pipe.enable_flashvdm()
    mesh = pipe(image=img, num_inference_steps=STEPS, octree_resolution=OCTREE, num_chunks=20000,
                generator=torch.manual_seed(a.seed), output_type="trimesh")[0]
    del pipe
    torch.cuda.empty_cache()
    mesh = FloaterRemover()(mesh)
    mesh = DegenerateFaceRemover()(mesh)
    mesh = FaceReducer()(mesh, max_facenum=FACES)
    stats["shape_s"] = round(time.time() - t, 1)
    stats["shape_peak_gib"] = peak.gib()
    log(f"shape in {stats['shape_s']} s, {len(mesh.faces)} faces, peak {stats['shape_peak_gib']} GiB")

    if a.texture:
        from hy3dgen.texgen import Hunyuan3DPaintPipeline
        t = time.time()
        paint = Hunyuan3DPaintPipeline.from_pretrained("tencent/Hunyuan3D-2", subfolder="hunyuan3d-paint-v2-0-turbo")
        mesh = paint(mesh, image=img)
        stats["paint_s"] = round(time.time() - t, 1)
        log(f"painted in {stats['paint_s']} s, peak {peak.gib()} GiB")
    mesh.export(a.out)
    stats.update(faces=int(len(mesh.faces)), peak_gib=peak.gib())
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="job", required=True)
    p = sub.add_parser("image_to_3d")
    p.add_argument("--image", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--shape", choices=sorted(SHAPES), default="full")
    p.add_argument("--no-texture", dest="texture", action="store_false")
    p.add_argument("--seed", type=int, default=12345)
    a = ap.parse_args(argv)
    stats = image_to_3d(a)
    print(json.dumps(stats), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
