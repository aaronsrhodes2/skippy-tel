"""The reporter's picture job against a fake image-studio: unload Ollama first, render, hand the PNG to the portal with
the pairing token as image/png, and report every failure plainly."""
import json

import pytest

import reporter
from test_music import Fake, _json

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def home(monkeypatch):
    seen = {"studio": [], "portal": [], "ollama": []}

    def studio(method, path, headers, body):
        seen["studio"].append((method, path, json.loads(body or b"{}")))
        if path == "/health":
            return _json({"ready": True, "loaded": False})
        if path == "/generate":
            return 200, {"Content-Type": "image/png"}, studio.reply
        return _json({}, 404)
    studio.reply = PNG

    def ollama(method, path, headers, body):
        seen["ollama"].append(path)
        return _json({"models": [{"name": "qwen3:8b"}]} if path == "/api/ps" else {})

    def portal(method, path, headers, body):
        seen["portal"].append((method, path, headers.get("Content-Type"), headers.get("Authorization"), body))
        return _json({"ok": True})

    fakes = [Fake(studio), Fake(ollama), Fake(portal)]
    monkeypatch.setattr(reporter, "IMAGES", fakes[0].url)
    monkeypatch.setattr(reporter, "OLLAMA", fakes[1].url)
    monkeypatch.setattr(reporter, "PORTAL", fakes[2].url)
    yield seen, studio
    for f in fakes:
        f.close()


def test_a_picture_is_rendered_after_ollama_unloads_and_handed_to_the_portal_as_png(home):
    seen, _ = home
    out = reporter.make_image({"id": "job1", "prompt": "a dog in sunflowers", "width": 832, "height": 1216}, "tok")
    assert out == {"result": {"title": "a dog in sunflowers", "bytes": len(PNG)}}
    assert "/api/ps" in seen["ollama"]                                       # the GPU was freed first
    (_, path, ask), = [c for c in seen["studio"] if c[1] == "/generate"]
    assert ask["prompt"] == "a dog in sunflowers" and (ask["width"], ask["height"]) == (832, 1216)
    (method, ppath, ctype, auth, body), = seen["portal"]
    assert method == "PUT" and ppath.startswith("/home/media/job1") and ctype == "image/png" and auth == "Bearer tok" and body == PNG


def test_not_a_png_or_no_prompt_is_an_error_not_a_crash(home):
    seen, studio = home
    studio.reply = b"<html>oops</html>"
    assert "PNG" in reporter.make_image({"id": "j", "prompt": "x"}, "tok")["error"]
    assert "description" in reporter.make_image({"id": "j", "prompt": "  "}, "tok")["error"]
    assert seen["portal"] == []


def test_the_capability_is_ready_only_when_the_studio_says_so(home):
    assert reporter._capability_probes()["make_image"]() == {"ready": True, "box": reporter.IMAGE_BOX, "loaded": False}   # (which machine: the patient queue waits for it)


def test_without_a_studio_it_says_so(monkeypatch):
    monkeypatch.setattr(reporter, "IMAGES", "")
    assert "aren't set up" in reporter.make_image({"id": "j", "prompt": "x"}, "tok")["error"]
    assert reporter._capability_probes()["make_image"]() == {"ready": False, "box": reporter.IMAGE_BOX, "loaded": False}


def test_the_core_studios_gpu_figures_ride_up_with_the_capability(monkeypatch):
    monkeypatch.setattr(reporter, "IMAGES", "http://core-studio:8002")
    monkeypatch.setattr(reporter, "IMAGE_BOX", "core")
    monkeypatch.setattr(reporter, "fetch", lambda url, *a, **k: {"ready": True, "gpu_free_pct": 47, "gpu_util_pct": 3.4,
                                                                 "gpu_free_gib": 58.25, "gpu_free_note": "x", "evil": True, "loaded": True})
    assert reporter.image_capability() == {"ready": True, "box": "core", "loaded": True, "gpu_free_pct": 47.0, "gpu_util_pct": 3.4, "gpu_free_gib": 58.2}
