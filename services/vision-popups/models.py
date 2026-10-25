"""The models vision-popups can load, and the GPU bookkeeping around them: one entry per model (where its pinned weights
live, its licence, what it needs on the card), loaded on the first request and unloaded after IDLE_UNLOAD_S idle seconds
or when another tool needs the card, like services/image-studio.

Weights: the nightly archive's own pinned copies (D:/models/huggingface/<Org>__<Repo>, mounted read-only at /archive)
are copied once into the vision-popups-weights volume (/weights, on the C: SSD), because Docker reads D: at ~8 MB/s.
Nothing is ever downloaded: HF_HUB_OFFLINE is on and the container has no route it needs."""
from __future__ import annotations

import gc
import importlib
import os
import shutil
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ARCHIVE = Path(os.environ.get("ARCHIVE_ROOT", "/archive"))
WEIGHTS = Path(os.environ.get("WEIGHTS_ROOT", "/weights"))
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "300"))
HEADROOM_MB = int(os.environ.get("HEADROOM_MB", "600"))   # what stays free on the card beyond a model's own peak
SEEDED = ".vision-popups-seeded"


@dataclass
class Model:
    name: str
    folder: str               # under /archive and /weights (the archive's <Org>__<Repo> naming)
    licence: str
    need_mb: int              # peak on the card while it runs: bench.py's torch peak + ~400 MB of CUDA context, rounded up
    loader: Callable | None = None
    files: tuple = ()          # only these files are seeded (empty = the whole folder)
    note: str = ""
    extra: dict = field(default_factory=dict)


class CardBusy(RuntimeError):
    """Not enough free memory on the card right now: a 503 the patient queue retries later, never a crash."""


MODELS: dict[str, Model] = {}
_loaded: dict[str, object] = {}
_last_used = 0.0
lock = threading.RLock()          # one tool at a time on the card
_seed_lock = threading.Lock()


def register(model: Model) -> Model:
    MODELS[model.name] = model
    return model


def path(name: str) -> Path:
    """The model's weights folder on the fast volume, seeding it from the archive the first time."""
    model = MODELS[name]
    dest = WEIGHTS / model.folder
    if (dest / SEEDED).exists():
        return dest
    src = ARCHIVE / model.folder
    if not src.is_dir():
        raise FileNotFoundError(f"{model.folder} isn't in the models archive (python tools/models_archive.py hf ...)")
    with _seed_lock:
        if not (dest / SEEDED).exists():
            started = time.time()
            for f in src.rglob("*"):
                rel = f.relative_to(src)
                if f.is_dir() or rel.parts[0] in (".cache", "assets") or (model.files and str(rel.as_posix()) not in model.files):
                    continue
                target = dest / rel
                if target.exists() and target.stat().st_size == f.stat().st_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, str(target) + ".part")
                os.replace(str(target) + ".part", target)
            (dest / SEEDED).write_text(str(time.time()))
            print(f"[vision-popups] seeded {model.folder} in {time.time() - started:.0f}s", flush=True)
    return dest


def seeded(name: str) -> bool:
    return (WEIGHTS / MODELS[name].folder / SEEDED).exists() or (ARCHIVE / MODELS[name].folder).is_dir()


def free_mb() -> int:
    import torch
    free, _total = torch.cuda.mem_get_info()
    return int(free // 2**20)


def use(*names: str, alongside: tuple = ()) -> list:
    """The named models, loaded (everything else unloaded first, so one tool owns the card). Raises CardBusy when what they
    need isn't free: loading anyway would push the card into shared system memory, which froze Docker's VM on 2026-10-23."""
    global _last_used
    with lock:
        for other in [n for n in _loaded if n not in names and n not in alongside]:
            unload(other)
        missing = [n for n in names if n not in _loaded]
        if missing:
            import torch
            need = sum(MODELS[n].need_mb for n in missing) + HEADROOM_MB
            have = free_mb()
            if have < need:
                raise CardBusy(f"the card has {have} MB free and this needs {need} MB")
            for n in missing:
                started = time.time()
                _loaded[n] = MODELS[n].loader(path(n))
                torch.cuda.synchronize()
                print(f"[vision-popups] loaded {n} in {time.time() - started:.1f}s", flush=True)
        _last_used = time.time()
        return [_loaded[n] for n in names]


def unload(name: str) -> None:
    with lock:
        if _loaded.pop(name, None) is not None:
            import torch
            gc.collect()
            torch.cuda.empty_cache()
            print(f"[vision-popups] unloaded {name}", flush=True)


def loaded() -> list[str]:
    return list(_loaded)


def touch() -> None:
    global _last_used
    _last_used = time.time()


def unload_when_idle() -> None:
    while True:
        time.sleep(15)
        with lock:
            if _loaded and time.time() - _last_used > IDLE_UNLOAD_S:
                for n in list(_loaded):
                    unload(n)


def import_package(folder: Path, module: str, alias: str):
    """A model repo's own Python (BiRefNet's birefnet.py, reviewed: plain PyTorch, no network) imported as a package so
    its relative imports work, without trust_remote_code."""
    if alias not in sys.modules:
        pkg = types.ModuleType(alias)
        pkg.__path__ = [str(folder)]
        sys.modules[alias] = pkg
    return importlib.import_module(f"{alias}.{module}")


# ── loaders ───────────────────────────────────────────────────────────────────

def _birefnet(folder: Path):
    import torch
    from safetensors.torch import load_file
    code = import_package(folder, "birefnet", "birefnet_" + folder.name.replace("-", "_").replace(".", "_"))
    cfg = importlib.import_module(code.__name__.rsplit(".", 1)[0] + ".BiRefNet_config").BiRefNetConfig()
    with torch.device("meta"):         # no random init of 220M weights that are overwritten at once (it took minutes)
        net = code.BiRefNet(bb_pretrained=False, config=cfg)
    net.load_state_dict(load_file(str(folder / "model.safetensors"), device="cuda"), assign=True)
    return net.to(dtype=torch.float16).eval()


def _esrgan(folder: Path):
    import spandrel
    import torch
    desc = spandrel.ModelLoader().load_from_file(str(folder / "RealESRGAN_x4plus.safetensors"))
    return desc.model.to("cuda", dtype=torch.float16).eval()


def _hf(model_cls: str, processor_cls: str, dtype: str = "float16"):
    def load(folder: Path):
        import torch
        import transformers
        model = getattr(transformers, model_cls).from_pretrained(folder, dtype=getattr(torch, dtype), device_map="cuda").eval()
        processor = getattr(transformers, processor_cls).from_pretrained(folder)
        return model, processor
    return load


register(Model("birefnet", "ZhengPeng7__BiRefNet", "MIT", 3200, _birefnet, extra={"size": 1024}))
register(Model("birefnet_matting", "ZhengPeng7__BiRefNet-matting", "MIT", 3400, _birefnet, extra={"size": 1024}))
# BiRefNet_HR and BiRefNet_HR-matting (2048 px) were benchmarked and left out: 9.7 GB peak, more than the card has beside the voices.
register(Model("realesrgan_x4", "Comfy-Org__Real-ESRGAN", "BSD-3-Clause", 3500, _esrgan))
register(Model("depth_anything_v2_large", "depth-anything__Depth-Anything-V2-Large-hf", "CC-BY-NC-4.0 (personal use only)", 1300,
               _hf("AutoModelForDepthEstimation", "AutoImageProcessor")))
register(Model("depth_anything_v2_small", "depth-anything__Depth-Anything-V2-Small-hf", "Apache-2.0", 500,
               _hf("AutoModelForDepthEstimation", "AutoImageProcessor")))
register(Model("sam2.1_large", "facebook__sam2.1-hiera-large", "Apache-2.0", 2300, _hf("Sam2Model", "Sam2Processor", "float32")))
register(Model("sam2.1_base_plus", "facebook__sam2.1-hiera-base-plus", "Apache-2.0", 1900, _hf("Sam2Model", "Sam2Processor", "float32")))
register(Model("grounding_dino_base", "IDEA-Research__grounding-dino-base", "Apache-2.0", 2700,
               _hf("AutoModelForZeroShotObjectDetection", "AutoProcessor", "float32")))
register(Model("grounding_dino_tiny", "IDEA-Research__grounding-dino-tiny", "Apache-2.0", 2400,
               _hf("AutoModelForZeroShotObjectDetection", "AutoProcessor", "float32")))
register(Model("paddleocr_vl", "PaddlePaddle__PaddleOCR-VL-1.6", "Apache-2.0", 2400,
               _hf("AutoModelForImageTextToText", "AutoProcessor", "bfloat16")))
register(Model("surya_ocr_2", "datalab-to__surya-ocr-2", "OpenRAIL-M (modified; free for personal use)", 1900,
               _hf("AutoModelForImageTextToText", "AutoProcessor", "bfloat16"),
               extra={"prompt": "OCR this image to HTML. Each block is a div with data-label and data-bbox (x0 y0 x1 y1, normalized 0-1000)."}))
register(Model("rtdetr_person", "PekingU__rtdetr_r50vd_coco_o365", "Apache-2.0", 400,
               _hf("RTDetrForObjectDetection", "RTDetrImageProcessor", "float32")))
register(Model("vitpose_plus_base", "usyd-community__vitpose-plus-base", "Apache-2.0", 600,
               _hf("VitPoseForPoseEstimation", "AutoProcessor", "float32")))
