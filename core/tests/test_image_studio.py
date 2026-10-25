"""core/image-studio/app.py with a fake sd-cli and fake amdgpu sysfs files: the HTTP contract skippy-pc's home reporter and the
portal's patient queue rely on (ready, busy, gpu_free_gib / gpu_free_pct / gpu_util_pct, PNG back, errors in plain words)."""
import importlib
import sys
import textwrap
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parents[1] / "image-studio"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def studio(monkeypatch, tmp_path):
    models = tmp_path / "models"
    for rel in ("huggingface/unsloth__Qwen-Image-2512-GGUF/qwen-image-2512-Q8_0.gguf",
                "huggingface/unsloth__Qwen2.5-VL-7B-Instruct-GGUF/Qwen2.5-VL-7B-Instruct-Q8_0.gguf",
                "huggingface/Comfy-Org__Qwen-Image/split_files/vae/qwen_image_vae.safetensors"):
        (models / rel).parent.mkdir(parents=True, exist_ok=True)
        (models / rel).write_bytes(b"w")
    drm = tmp_path / "drm" / "card1" / "device"
    drm.mkdir(parents=True)
    (drm / "mem_info_gtt_total").write_text(str(124 * 2**30))
    (drm / "mem_info_gtt_used").write_text(str(84 * 2**30))
    (drm / "gpu_busy_percent").write_text("10")
    cli = tmp_path / "sd-cli"
    cli.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        args = sys.argv[1:]
        open(tmp := {str(tmp_path / 'args.txt')!r}, 'w').write('\\n'.join(args))
        if 'FAIL' in args[args.index('-p') + 1]:
            print('ggml_vulkan: out of device memory', file=sys.stderr); sys.exit(3)
        open(args[args.index('-o') + 1], 'wb').write({PNG!r})
        """))
    cli.chmod(0o755)
    monkeypatch.setenv("MODELS", str(models))
    monkeypatch.setenv("SD_CLI", str(cli))
    monkeypatch.setenv("DRM_DEVICE", str(tmp_path / "drm" / "card*" / "device"))
    monkeypatch.syspath_prepend(str(STUDIO))
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    yield app, app.app.test_client(), tmp_path
    sys.modules.pop("app", None)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake sd-cli is a shebang script")
def test_health_carries_what_the_patient_queue_reads(studio):
    _, client, _ = studio
    h = client.get("/health").get_json()
    assert h["ready"] is True and h["busy"] is False
    assert (h["gpu_free_gib"], h["gpu_free_pct"], h["gpu_util_pct"]) == (40.0, 32.3, 10.0)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake sd-cli is a shebang script")
def test_generate_runs_sd_cli_with_the_quality_settings_and_returns_the_png(studio):
    _, client, tmp = studio
    r = client.post("/generate", json={"prompt": "a lighthouse", "width": 900, "height": 5000, "seed": 7})
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == PNG
    args = (tmp / "args.txt").read_text().splitlines()
    val = lambda flag: args[args.index(flag) + 1]  # noqa: E731
    assert val("-p") == "a lighthouse" and val("-s") == "7" and val("--steps") == "40" and val("--cfg-scale") == "4.0"
    assert (val("-W"), val("-H")) == ("896", "1664")                   # clamped, multiples of 64
    assert "--mmap" in args                                             # else the weights are held twice and the OOM killer fires
    assert val("--backend") == "vae=cpu"                                 # the GPU decode faulted once (ring reset)


def test_only_the_allowed_callers_get_an_answer(studio, monkeypatch):
    app, client, _ = studio
    monkeypatch.setattr(app, "ALLOW_FROM", {"100.122.71.14"})
    assert client.get("/health").status_code == 403                    # (the test client calls from 127.0.0.1)
    assert client.get("/health", environ_base={"REMOTE_ADDR": "100.122.71.14"}).status_code == 200


@pytest.mark.skipif(sys.platform == "win32", reason="the fake sd-cli is a shebang script")
def test_failures_come_back_in_words(studio):
    app, client, tmp = studio
    assert client.post("/generate", json={"prompt": " "}).status_code == 400
    r = client.post("/generate", json={"prompt": "FAIL please"})
    assert r.status_code == 500 and "out of device memory" in r.get_json()["error"]
    (tmp / "models" / "huggingface" / "Comfy-Org__Qwen-Image" / "split_files" / "vae" / "qwen_image_vae.safetensors").unlink()
    assert client.get("/health").get_json()["ready"] is False
