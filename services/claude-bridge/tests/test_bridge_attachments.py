"""Pictures pasted into the private Skippy's chat: saved where Claude Code can open them, named in the message it is given,
and refused unless they really are pictures (the bridge trusts nothing it is handed, whatever the portal checked)."""
import base64
import os

import pytest

import workshop_engine as engine
from bridge_testkit import finish  # noqa: F401  (the `shop` fixture: conftest.py)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20


def pic(data):
    return {"mime": "image/x", "b64": base64.b64encode(data).decode()}


def test_pictures_are_saved_under_the_task_and_named_in_the_message(shop):
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "the pill is cut off, see this", images=[pic(PNG), pic(JPG), pic(WEBP)]))
    folder = os.path.join(shop.attach_dir, "aaaaaaaa")
    assert sorted(os.listdir(folder)) == ["image-1.png", "image-2.jpg", "image-3.webp"]
    assert open(os.path.join(folder, "image-1.png"), "rb").read() == PNG
    assert run.message.startswith("the pill is cut off, see this\n\n[The Captain attached 3 pictures. Open each with the Read tool")
    for name in os.listdir(folder):
        assert os.path.join(folder, name) in run.message


def test_a_picture_with_no_words_still_starts_a_run(shop):
    run = finish(shop.start("bbbbbbbb", "conv-12345678", "", images=[pic(PNG)]))
    assert "just the picture" in run.message and "1 picture." in run.message and run.done


def test_what_is_not_a_picture_is_refused_and_nothing_is_written(shop):
    for bad in ([pic(b"#!/bin/sh\nrm -rf /\n")], [{"b64": "not base64!!"}], ["data:image/png;base64,AAAA"], [pic(PNG)] * 5,
                [pic(PNG + b"\x00" * engine.MAX_IMAGE_BYTES)], "image", [{"mime": "image/png"}]):
        with pytest.raises(ValueError):
            shop.start("cccccccc", "conv-12345678", "look", images=bad)
    assert not os.path.exists(os.path.join(shop.attach_dir, "cccccccc")) and not shop.record.exists()   # nothing written, nothing run


def test_a_reattach_never_takes_pictures_and_never_starts_anything(shop):
    with pytest.raises(engine.UnknownTask):
        shop.start("dddddddd", "conv-12345678", "", attach=True, images=[pic(PNG)])
    assert not os.path.exists(os.path.join(shop.attach_dir, "dddddddd"))


def test_old_picture_folders_are_swept(shop, tmp_path):
    old = os.path.join(shop.attach_dir, "eeeeeeee")
    os.makedirs(old)
    open(os.path.join(old, "image-1.png"), "wb").write(PNG)
    long_ago = os.path.getmtime(old) - engine.ATTACH_KEEP_S - 60
    os.utime(old, (long_ago, long_ago))
    finish(shop.start("ffffffff", "conv-12345678", "new one", images=[pic(PNG)]))
    assert not os.path.exists(old) and os.path.isdir(os.path.join(shop.attach_dir, "ffffffff"))


# ── a Core-brain run: its coder is text-only, so Skippy Core's vision slot looks at the pictures first ──────────────────────

def _core(monkeypatch):
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example/")
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "sk-core-test")
    monkeypatch.setattr(engine.Workshop, "core_ready", lambda self: True)


def test_a_core_run_gets_the_vision_models_description_and_is_told_not_to_open_the_picture(shop, monkeypatch):
    _core(monkeypatch)
    seen = []

    def describe(self, path, post=None):
        seen.append(path)
        return "A white smart plug label. Text: 'Model: XYZ-123', 'FCC ID: 2AB12-PLUG'."
    monkeypatch.setattr(engine.Workshop, "describe_picture", describe)
    run = finish(shop.start("dddddddd", "conv-12345678", "read the back of the plug", images=[pic(JPG)], brain="core"))
    assert seen == [os.path.join(shop.attach_dir, "dddddddd", "image-1.jpg")]
    assert run.message.startswith("read the back of the plug\n\n[The Captain attached 1 picture. You can't see pictures")
    assert "Model: XYZ-123" in run.message and "Do NOT open the picture files with the Read tool" in run.message
    assert "Open each with the Read tool" not in run.message


def test_when_the_vision_model_fails_the_run_still_goes_and_says_why(shop, monkeypatch):
    _core(monkeypatch)

    def broken(self, path, post=None):
        raise RuntimeError("vision slot is loading")
    monkeypatch.setattr(engine.Workshop, "describe_picture", broken)
    run = finish(shop.start("eeeeeeee", "conv-12345678", "", images=[pic(PNG)], brain="core"))
    assert "couldn't look at it: RuntimeError: vision slot is loading" in run.message and run.done


def test_describe_picture_asks_the_vision_slot_with_the_image_and_the_key(shop, monkeypatch, tmp_path):
    import json
    _core(monkeypatch)
    f = tmp_path / "image-1.jpg"
    f.write_bytes(JPG)
    asked = []

    def post(req):
        asked.append(req)
        return {"content": [{"type": "text", "text": "Label text: 'Model: XYZ-123'"}]}
    assert shop.describe_picture(str(f), post=post) == "Label text: 'Model: XYZ-123'"
    (req,) = asked
    body = json.loads(req.data)
    assert req.full_url == "https://core.example/v1/messages" and req.get_header("X-api-key") == "sk-core-test"
    assert body["model"] == "vision"
    image = body["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg" and base64.b64decode(image["source"]["data"]) == JPG
    assert "copied exactly as printed" in body["messages"][0]["content"][1]["text"]
