"""Show me the result, the portal's half: a screenshot from the bridge is accepted only as a real PNG or JPEG of at most
400 KB, kept in memory for its owner, served as exactly that type with nosniff (never as HTML), and a refused one
still takes its place in the event list so the reporter's count stays in step."""
import base64

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402
from test_workshop import CONV, TEXT  # noqa: E402

import app as portal  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 300
JPEG = b"\xff\xd8\xff\xe0" + b"\x02" * 300


def img(data=PNG, mime="image/png", name="me.png"):
    return {"t": "image", "mime": mime, "name": name, "b64": base64.b64encode(data).decode()}


@pytest.mark.parametrize("event", [
    img(b"<html><script>alert(1)</script></html>"),                     # not a picture at all
    img(JPEG, "image/png"),                                              # a JPEG claiming to be a PNG
    img(PNG, "text/html"), img(PNG, "image/svg+xml"),                    # only the two types
    img(PNG + b"\x00" * workshop_mod.MAX_IMAGE_BYTES),                   # too big
    {**img(), "b64": "not base64!!"}, {**img(), "b64": 7}, {**img(), "extra": 1}, {**img(), "name": None},
])
def test_anything_but_a_real_png_or_jpeg_is_refused(event):
    assert workshop_mod.image_event(event) is None


def test_a_real_one_is_accepted_and_its_name_cleaned():
    event, data = workshop_mod.image_event(img(name="../../etc/<passwd>.png"))
    assert data == PNG and event == {"t": "image", "mime": "image/png", "name": ".._.._etc__passwd_.png"}
    assert workshop_mod.image_event(img(JPEG, "image/jpeg"))[1] == JPEG


def test_a_pushed_screenshot_becomes_a_small_event_and_a_refused_one_keeps_its_place():
    ws = workshop_mod.Workshop(home_mod.Home(FakeVm()))
    reply = ws.push("abcd1234", ADMIN, CONV, 0, [TEXT, img(), img(b"nope"), TEXT], "running", None)
    assert reply["have"] == 4                                            # every event has a place: the count stays in step
    events = ws.read(ADMIN, "abcd1234")["events"]
    assert events[1] == {"t": "image", "mime": "image/png", "name": "me.png", "n": 0}
    assert events[2] == {"t": "text", "text": "(a screenshot couldn't be shown)"}
    assert "b64" not in str(events)                                      # the picture itself never rides in the poll
    assert ws.image(ADMIN, "abcd1234", 0) == ("image/png", PNG)
    assert ws.image(FRIEND, "abcd1234", 0) is None and ws.image(ADMIN, "abcd1234", 1) is None


def test_three_per_task_and_forty_in_all():
    ws = workshop_mod.Workshop(home_mod.Home(FakeVm()))
    ws.push("abcd1234", ADMIN, CONV, 0, [img()] * 5, "running", None)
    assert [e["t"] for e in ws.read(ADMIN, "abcd1234")["events"]] == ["image"] * 3 + ["text"] * 2
    for n in range(20):
        ws.push(f"abcd{n:04d}ff", ADMIN, CONV, 0, [img()] * 3, "running", None)
    assert len(ws._images) == workshop_mod.MAX_IMAGES and ws.image(ADMIN, "abcd1234", 0) is None   # the oldest went


@pytest.fixture
def env():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    app.extensions["workshop"].push("abcd1234", ADMIN, CONV, 0, [img(), img(JPEG, "image/jpeg", "x.jpg")], "done", None)
    return app.test_client()


def test_it_is_served_as_its_type_with_nosniff_and_only_to_its_owner(env):
    client = env
    assert client.get("/me/workshop/image?task=abcd1234&n=0").status_code == 302          # signed out: sign in
    login(client, FRIEND)
    assert client.get("/me/workshop/image?task=abcd1234&n=0").status_code == 403
    login(client, ADMIN)
    resp = client.get("/me/workshop/image?task=abcd1234&n=0")
    assert resp.status_code == 200 and resp.data == PNG and resp.mimetype == "image/png"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert resp.headers["Content-Disposition"] == "inline; filename=skippy-0.png"
    assert client.get("/me/workshop/image?task=abcd1234&n=1").mimetype == "image/jpeg"
    for bad in ("task=abcd1234&n=2", "task=nope&n=0", "task=abcd1234&n=x", "task=abcd1234"):
        assert client.get(f"/me/workshop/image?{bad}").status_code == 404


def test_a_3d_model_is_accepted_by_its_own_bytes_and_served_as_a_model():
    glb = b"glTF\x02\x00\x00\x00" + b"\x00" * 100
    event, data = workshop_mod.image_event(img(glb, "model/gltf-binary", "train.glb"))
    assert event["mime"] == "model/gltf-binary" and data == glb
    assert workshop_mod.image_event(img(PNG, "model/gltf-binary", "fake.glb")) is None        # a PNG claiming to be a model
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    app.extensions["workshop"].push("abcd1234", ADMIN, CONV, 0, [img(glb, "model/gltf-binary", "train.glb")], "done", None)
    client = app.test_client()
    login(client, ADMIN)
    got = client.get("/me/workshop/image?task=abcd1234&n=0")
    assert got.mimetype == "model/gltf-binary" and got.headers["Content-Disposition"] == "inline; filename=skippy-0.glb" and got.data == glb


def test_the_agents_transcript_draws_a_model_with_the_chats_3d_card():
    from pathlib import Path
    page = (Path(portal.__file__).parent / "templates" / "_workshop.html").read_text(encoding="utf-8")
    assert 'p.mime === "model/gltf-binary"' in page and "SkippyArtifacts.card(spec)" in page and "mime: e.mime" in page



@pytest.mark.parametrize("data, mime, how", [
    (b"ID3" + b"\x00" * 60, "audio/mpeg", "inline"), (b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 40, "audio/wav", "inline"),
    (b"OggS" + b"\x00" * 60, "audio/ogg", "inline"), (b"fLaC" + b"\x00" * 60, "audio/flac", "inline"),
    (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 60, "video/mp4", "inline"),
    (b"MThd\x00\x00\x00\x06" + b"\x00" * 40, "audio/midi", "attachment"), (b"PK\x03\x04" + b"\x00" * 60, "application/zip", "attachment"),
])
def test_sound_video_and_files_come_back_into_the_chat_never_as_a_path(data, mime, how):
    """The browser is only a window (the Captain, 2026-10-25): what the agent makes on skippy-pc is played or saved from the chat."""
    assert workshop_mod.image_event(img(data, mime, "x"))[0]["mime"] == mime
    assert workshop_mod.image_event(img(data, "image/png", "x")) is None              # claiming another type: refused
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    app.extensions["workshop"].push("abcd1234", ADMIN, CONV, 0, [img(data, mime, "x")], "done", None)
    client = app.test_client()
    login(client, ADMIN)
    got = client.get("/me/workshop/image?task=abcd1234&n=0")
    assert got.mimetype == mime and got.headers["Content-Disposition"].startswith(how) and got.headers["X-Content-Type-Options"] == "nosniff"


def test_the_bridge_and_the_portal_sniff_the_same_way():
    """The bridge decides what may be sent, the portal what may be served: one rule, or something made is lost on the way."""
    import ast
    from pathlib import Path
    src = (Path(portal.__file__).parents[1] / "services" / "claude-bridge" / "workshop_engine.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "image_mime")
    space: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "image_mime", "exec"), space)   # just that function, without the bridge's imports
    samples = [b"glTF\x02\x00\x00\x00", b"ID3\x04", b"\xff\xfb\x90", b"RIFF\x00\x00\x00\x00WAVE", b"OggS", b"fLaC",
               b"\x00\x00\x00\x18ftypisom", b"MThd", b"PK\x03\x04", PNG, JPEG, b"<html>", b"glTF\x01\x00\x00\x00"]
    assert [space["image_mime"](s) for s in samples] == [workshop_mod.sniff(s) for s in samples]


def test_a_screenshot_in_the_agents_chat_opens_the_annotator_and_comes_back_as_a_change_request():
    """Tap a screenshot, mark it up, and the copy plus a legend (colour, shape, exact pixels) go into the message (2026-10-25)."""
    from pathlib import Path
    root = Path(portal.__file__).parent
    page = (root / "templates" / "_workshop.html").read_text(encoding="utf-8")
    chat = (root / "templates" / "_chat.html").read_text(encoding="utf-8")
    front = (root / "templates" / "front.html").read_text(encoding="utf-8")
    js = (root / "static" / "skippy-annotate.js").read_text(encoding="utf-8")
    assert "SkippyAnnotate.open(" in page and "name: e.name" in page and "window.SkippyCompose" in chat
    line = next(i for i, l in enumerate(front.splitlines()) if "skippy-annotate.js" in l)
    assert "{% if captain %}" in front.splitlines()[line - 1] and "{% endif %}" in front.splitlines()[line]      # the Captain's chat only
    assert all(c in js for c in ("#FF4444", "#FFAA00", "#00FF88", "#00CCFF"))                       # the house palette
