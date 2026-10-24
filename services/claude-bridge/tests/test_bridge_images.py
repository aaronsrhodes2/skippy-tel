"""Show me the result, the bridge's half: the agent prints `SKIPPY_IMAGE: <path>` and the run ends with an `image` event
(base64) under its answer, but only for a real PNG/JPEG of at most 400 KB inside the project's own clone; the line itself
never reaches the chat."""
import base64
from pathlib import Path

import pytest

import workshop_engine as engine
from bridge_testkit import finish

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def run_saying(shop, monkeypatch, text, task="aaaaaaaa"):
    monkeypatch.setenv("FAKE_MODE", "say")
    monkeypatch.setenv("FAKE_SAY", text)
    return finish(shop.start(task, "conv-12345678", "show me"))


def images(run):
    return [e for e in run.events if e["t"] == "image"]


def texts(run):
    return [e["text"] for e in run.events if e["t"] == "text" and not e["text"].startswith("(my clone has")]


def test_a_screenshot_in_the_clone_is_attached_and_its_line_is_not_shown(shop, monkeypatch):
    shots = Path(shop.repo) / ".skippy-shots"
    shots.mkdir()
    (shots / "me.png").write_bytes(PNG)
    run = run_saying(shop, monkeypatch, "Live, and here it is.\nSKIPPY_IMAGE: .skippy-shots/me.png\n")
    [img] = images(run)
    assert img == {"t": "image", "mime": "image/png", "name": "me.png", "b64": base64.b64encode(PNG).decode()}
    assert texts(run) == ["Live, and here it is."] and run.events[-1] == img
    assert run.ok is True


def test_an_absolute_path_and_a_jpeg_work_too(shop, monkeypatch):
    (Path(shop.repo) / "x.jpg").write_bytes(JPEG)
    run = run_saying(shop, monkeypatch, f"`SKIPPY_IMAGE: {Path(shop.repo) / 'x.jpg'}`")
    assert [i["mime"] for i in images(run)] == ["image/jpeg"] and texts(run) == []


@pytest.mark.parametrize("make, line", [
    (lambda clone, tmp: (tmp / "outside.png").write_bytes(PNG), "SKIPPY_IMAGE: ../outside.png"),       # outside the clone
    (lambda clone, tmp: (clone / "notes.png").write_bytes(b"secret text, not a picture"), "SKIPPY_IMAGE: notes.png"),
    (lambda clone, tmp: (clone / "big.png").write_bytes(PNG + b"\x00" * engine.MAX_SHOT_BYTES), "SKIPPY_IMAGE: big.png"),
    (lambda clone, tmp: None, "SKIPPY_IMAGE: missing.png"),
])
def test_anything_else_is_refused_with_a_note_and_nothing_is_sent(shop, monkeypatch, tmp_path, make, line):
    make(Path(shop.repo), tmp_path)
    run = run_saying(shop, monkeypatch, "Done.\n" + line)
    assert images(run) == []
    assert texts(run)[0] == "Done." and "couldn't attach the picture" in texts(run)[1]
    assert "secret text" not in str(run.events)


def test_at_most_three_per_run(shop, monkeypatch):
    for n in range(5):
        (Path(shop.repo) / f"s{n}.png").write_bytes(PNG)
    run = run_saying(shop, monkeypatch, "\n".join(f"SKIPPY_IMAGE: s{n}.png" for n in range(5)))
    assert [i["name"] for i in images(run)] == ["s0.png", "s1.png", "s2.png"]


def test_split_images_only_takes_whole_lines():
    said, paths = engine.split_images("see SKIPPY_IMAGE: inline.png here\nSKIPPY_IMAGE: real.png\nend")
    assert paths == ["real.png"] and said == "see SKIPPY_IMAGE: inline.png here\n\nend"
    assert engine.image_mime(PNG) == "image/png" and engine.image_mime(JPEG) == "image/jpeg"
    assert engine.image_mime(b"<html>") is None and engine.image_mime(b"") is None


def test_a_big_picture_up_to_12_mb_is_attached(shop, monkeypatch):
    (Path(shop.repo) / "cat.png").write_bytes(PNG + b"\x00" * 2_000_000)          # the cat that was refused at 400 KB
    run = run_saying(shop, monkeypatch, "Here.\nSKIPPY_IMAGE: cat.png")
    assert [i["name"] for i in images(run)] == ["cat.png"]


def test_a_picture_to_make_becomes_an_event_with_its_own_id_and_its_line_is_not_shown(shop, monkeypatch):
    run = run_saying(shop, monkeypatch, "Queued it.\nSKIPPY_PICTURE: a grey tabby cat asleep on a windowsill, soft light\n")
    [pic] = [e for e in run.events if e.get("t") == "picture"]
    assert pic["prompt"] == "a grey tabby cat asleep on a windowsill, soft light" and len(pic["id"]) == 16
    assert texts(run) == ["Queued it."]


def test_split_pictures_takes_whole_lines_and_at_most_two_are_asked_for(shop, monkeypatch):
    said, prompts = engine.split_pictures("say SKIPPY_PICTURE: inline\n`SKIPPY_PICTURE: a fox`\nend")
    assert prompts == ["a fox"] and said == "say SKIPPY_PICTURE: inline\n\nend"
    run = run_saying(shop, monkeypatch, "\n".join(f"SKIPPY_PICTURE: cat {i}" for i in range(4)))
    assert [e["prompt"] for e in run.events if e.get("t") == "picture"] == ["cat 0", "cat 1"]
