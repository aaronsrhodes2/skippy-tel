"""image-studio's HTTP contract with the model faked out (no GPU, no torch): the home reporter's make_image relies on it.
Run: python -m pytest services/image-studio/tests (needs flask + pillow)."""
import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app.py"     # loaded by path: CI's one pytest run also has portal/app.py as `app`


class FakeGenerator:
    def __init__(self, device):
        self.seed = None

    def manual_seed(self, seed):
        self.seed = seed
        return self


@pytest.fixture
def studio(monkeypatch, tmp_path):
    cuda = types.SimpleNamespace(reset_peak_memory_stats=lambda: None, max_memory_allocated=lambda: 6 * 2**30,
                                 empty_cache=lambda: None, memory_allocated=lambda: 0, memory_reserved=lambda: 0)
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=cuda, Generator=FakeGenerator))
    monkeypatch.setenv("WEIGHTS", str(tmp_path))
    spec = importlib.util.spec_from_file_location("quick_studio_app", APP)
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    calls = []

    def pipe(**kw):
        calls.append(kw)
        return types.SimpleNamespace(images=[Image.new("RGB", (kw["width"], kw["height"]), "black")])
    monkeypatch.setattr(app, "_load", lambda: pipe)
    yield app, app.app.test_client(), calls, tmp_path


def test_health_is_ready_only_once_the_weights_are_seeded(studio):
    app, client, _, weights = studio
    assert client.get("/health").get_json() == {"ready": False, "loaded": False, "model": "flux2-klein-4b"}
    (weights / "model_index.json").write_text("{}")
    assert client.get("/health").get_json()["ready"] is True


def test_generate_returns_a_png_with_the_models_own_settings(studio):
    app, client, calls, _ = studio
    r = client.post("/generate", json={"prompt": "a cat", "negative": "dogs", "width": 1024, "height": 768, "seed": 7})
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(r.data)).size == (1024, 768)
    kw = calls[0]
    assert kw["num_inference_steps"] == 4 and kw["guidance_scale"] == 1.0      # klein: 4 steps, distilled guidance
    assert "negative_prompt" not in kw                                          # accepted, ignored (no CFG)
    assert kw["generator"].seed == 7
    assert float(r.headers["X-Render-Seconds"]) >= 0 and r.headers["X-Peak-VRAM-GiB"] == "6.00"


def test_sizes_are_clamped_to_multiples_of_64(studio):
    _, client, calls, _ = studio
    client.post("/generate", json={"prompt": "x", "width": 5000, "height": 100})
    client.post("/generate", json={"prompt": "x", "width": "junk", "height": 1000})
    assert (calls[0]["width"], calls[0]["height"]) == (1344, 512)
    assert (calls[1]["width"], calls[1]["height"]) == (1024, 960)


def test_empty_prompt_is_a_400_and_a_failed_render_a_500_with_the_reason(studio, monkeypatch):
    app, client, _, _ = studio
    assert client.post("/generate", json={"prompt": "  "}).status_code == 400

    def broken():
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(app, "_load", broken)
    r = client.post("/generate", json={"prompt": "x"})
    assert r.status_code == 500 and "CUDA out of memory" in r.get_json()["error"]
