"""core/popups-3d/app.py with a fake worker, sd-cli, ffmpeg and amdgpu sysfs files: the HTTP contract skippy-pc's home reporter
and the portal's patient queue rely on (per-pop-up readiness, the gpu_* figures, the bytes back, errors in plain words)."""
import base64
import importlib
import json
import sys
import textwrap
from pathlib import Path

import pytest

POPUPS = Path(__file__).resolve().parents[1] / "popups-3d"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 16
shebang = pytest.mark.skipif(sys.platform == "win32", reason="the fakes are shebang scripts")


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


@pytest.fixture
def popups(monkeypatch, tmp_path):
    models = tmp_path / "models" / "huggingface"
    for rel in ("tencent__Hunyuan3D-2/hunyuan3d-dit-v2-0-turbo/model.fp16.safetensors",
                "tencent__Hunyuan3D-2/hunyuan3d-paint-v2-0-turbo/unet/x.safetensors",
                "tencent__Hunyuan3D-2/hunyuan3d-delight-v2-0/unet/x.safetensors",
                "Comfy-Org__Wan_2.2_ComfyUI_Repackaged/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
                "Comfy-Org__Wan_2.2_ComfyUI_Repackaged/split_files/vae/wan2.2_vae.safetensors",
                "city96__umt5-xxl-encoder-gguf/umt5-xxl-encoder-Q8_0.gguf"):
        (models / rel).parent.mkdir(parents=True, exist_ok=True)
        (models / rel).write_bytes(b"w")
    drm = tmp_path / "drm" / "card1" / "device"
    drm.mkdir(parents=True)
    (drm / "mem_info_gtt_total").write_text(str(124 * 2**30))
    (drm / "mem_info_gtt_used").write_text(str(78 * 2**30))
    (drm / "gpu_busy_percent").write_text("4")
    log = tmp_path / "args.txt"
    worker = _script(tmp_path / "worker.py", f"""\
        import json, sys
        args = sys.argv[1:]
        open({str(log)!r}, 'w').write('\\n'.join(args))
        if open(args[args.index('--image') + 1], 'rb').read().startswith(b'\\xff\\xd8BAD'):
            print('HIP out of memory', file=sys.stderr); sys.exit(2)
        open(args[args.index('--out') + 1], 'wb').write({GLB!r})
        print(json.dumps({{"faces": 40000, "peak_gib": 21.5}}))
        """)
    cli = _script(tmp_path / "sd-cli", f"""\
        import sys
        args = sys.argv[1:]
        open({str(log)!r}, 'w').write('\\n'.join(args))
        if 'FAIL' in args[args.index('-p') + 1]:
            print('ggml_vulkan: out of device memory', file=sys.stderr); sys.exit(3)
        open(args[args.index('-o') + 1], 'wb').write(b'webm')
        """)
    ffmpeg = _script(tmp_path / "ffmpeg", """\
        import sys
        open(sys.argv[-1], 'wb').write(b'\\x00\\x00\\x00\\x18ftypmp42')
        """)
    monkeypatch.setenv("MODELS", str(tmp_path / "models"))
    monkeypatch.setenv("SD_CLI", str(cli))
    monkeypatch.setenv("FFMPEG", str(ffmpeg))
    monkeypatch.setenv("DRM_DEVICE", str(tmp_path / "drm" / "card*" / "device"))
    monkeypatch.syspath_prepend(str(POPUPS))
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    monkeypatch.setattr(app, "WORKER", [str(worker)])
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr(app, "_studio_up", lambda: True)
    yield app, app.app.test_client(), log
    sys.modules.pop("app", None)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_health_says_which_popups_are_ready_and_carries_the_gpu_figures(popups, monkeypatch):
    app, client, _ = popups
    h = client.get("/health").get_json()
    assert h["ready"] is True and h["busy"] is False
    assert {k: v["ready"] for k, v in h["popups"].items()} == {"image_to_3d": True, "text_to_3d": True, "make_video": True}
    assert all(v["peak_gib"] > 0 for v in h["popups"].values())
    assert (h["gpu_free_gib"], h["gpu_free_pct"], h["gpu_util_pct"]) == (46.0, 37.1, 4.0)
    monkeypatch.setattr(app, "_studio_up", lambda: False)
    h = client.get("/health").get_json()
    assert h["ready"] is True                                           # the other two still work
    assert h["popups"]["text_to_3d"] == {"ready": False, "peak_gib": app.PEAK_GIB["text_to_3d"],
                                          "why": "the core image-studio is not answering"}


def test_a_missing_model_says_what_is_missing(popups, monkeypatch, tmp_path):
    app, client, _ = popups
    monkeypatch.setattr(app, "UMT5", str(tmp_path / "models" / "huggingface" / "nope.gguf"))
    h = client.get("/health").get_json()
    assert h["popups"]["make_video"]["ready"] is False and h["popups"]["make_video"]["why"] == "missing huggingface/nope.gguf"
    assert h["ready"] is False


@shebang
def test_image_to_3d_runs_the_worker_and_returns_the_glb(popups):
    _, client, log = popups
    r = client.post("/image_to_3d", json={"args": {"seed": 7, "shape": "mini"}, "input": {"type": "image/png", "b64": _b64(PNG)}})
    assert r.status_code == 200 and r.mimetype == "model/gltf-binary" and r.data == GLB
    assert r.headers["X-Faces"] == "40000" and r.headers["X-Peak-GiB"] == "21.5"
    args = log.read_text().splitlines()
    assert args[0] == "image_to_3d" and args[args.index("--seed") + 1] == "7" and args[args.index("--shape") + 1] == "mini"
    assert "--no-texture" not in args
    client.post("/image_to_3d", json={"args": {"texture": False, "shape": "anything"}, "input": {"b64": _b64(PNG)}})
    args = log.read_text().splitlines()
    assert "--no-texture" in args and args[args.index("--shape") + 1] == "full"


@shebang
def test_text_to_3d_draws_a_prop_picture_first(popups, monkeypatch):
    app, client, log = popups
    asked = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return PNG

    def fake_urlopen(req, timeout):
        asked.update(url=req.full_url, body=json.loads(req.data))
        return Reply()

    monkeypatch.setattr(app.urllib.request, "urlopen", fake_urlopen)
    r = client.post("/text_to_3d", json={"args": {"prompt": "a dwarf warrior mini with an axe"}})
    assert r.status_code == 200 and r.data == GLB
    assert asked["url"].endswith(":7347/generate")
    assert "a dwarf warrior mini with an axe" in asked["body"]["prompt"] and "plain white background" in asked["body"]["prompt"]
    assert log.read_text().splitlines()[0] == "image_to_3d"


@shebang
def test_video_runs_wan_and_returns_an_mp4(popups):
    app, client, log = popups
    r = client.post("/make_video", json={"args": {"prompt": "a lighthouse in a storm", "seconds": 2, "width": 5000}})
    assert r.status_code == 200 and r.mimetype == "video/mp4" and r.data.endswith(b"ftypmp42")
    args = log.read_text().splitlines()
    val = lambda flag: args[args.index(flag) + 1]  # noqa: E731
    assert val("-M") == "vid_gen" and val("--video-frames") == "49" and r.headers["X-Frames"] == "49"   # 4n+1
    assert (val("-W"), val("-H")) == ("1280", "704")
    assert "--mmap" in args and "-i" not in args
    client.post("/make_video", json={"args": {"prompt": "the same, moving", "seconds": 99}, "input": {"type": "image/png", "b64": _b64(PNG)}})
    args = log.read_text().splitlines()
    assert "-i" in args and args[args.index("--video-frames") + 1] == "97"                            # 4 s at most
    assert "--vae-tile-size" not in args and "--temporal-tiling" not in args                         # those reset the GPU


@shebang
def test_failures_come_back_in_words(popups):
    _, client, _ = popups
    assert client.post("/image_to_3d", json={}).status_code == 400
    assert client.post("/image_to_3d", json={"input": {"b64": _b64(b"not a picture")}}).status_code == 400
    assert client.post("/text_to_3d", json={"args": {"prompt": " "}}).status_code == 400
    assert client.post("/make_video", json={"args": {"prompt": "x"}, "input": {"b64": "%%%"}}).status_code == 400
    r = client.post("/image_to_3d", json={"input": {"b64": _b64(b"\xff\xd8BAD")}})
    assert r.status_code == 500 and "HIP out of memory" in r.get_json()["error"]
    r = client.post("/make_video", json={"args": {"prompt": "FAIL"}})
    assert r.status_code == 500 and "out of device memory" in r.get_json()["error"]


def test_only_the_allowed_callers_get_an_answer(popups, monkeypatch):
    app, client, _ = popups
    monkeypatch.setattr(app, "ALLOW_FROM", {"100.122.71.14"})
    assert client.get("/health").status_code == 403
    assert client.get("/health", environ_base={"REMOTE_ADDR": "100.122.71.14"}).status_code == 200
