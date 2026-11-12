"""Skippy in the chat corner: the pods' own face (static/skippy-face.js, a port of services/satellite/face_render.py, tick tested under
Node in tests/js/face.test.js), driven by static/skippy-3d.js from the page's events (templates/_skippy3d.html)."""

import json
import re
from pathlib import Path

from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402
import app as portal  # noqa: E402

PORTAL = Path(__file__).resolve().parents[1]
STATIC = PORTAL / "static"
PAL = "aaron.s.rhodes@gmail.com"


def make():
    vm = FakeVm()
    vm.metadata["portal-friends"] = json.dumps({PAL: {"portals": ["valheim", "skippy"], "added_by": ADMIN}})
    app = portal.create_app({**SETTINGS, "ALLOWED_EMAILS": frozenset({ADMIN}), "TELEMETRY_ENABLED": True},
                            vm=vm, skippy=FakeSkippy())
    client = app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
    return app, client


def test_the_corner_carries_the_face_canvas_its_still_picture_and_the_cans_frames():
    _, client = make()
    html = client.get("/chat").get_data(as_text=True)
    assert 'class="s3-canvas"' in html and 'class="s3-still"' in html
    assert re.search(r'data-frames="[^"]*/static/skippy/face/"', html)
    face, ctl = html.index('skippy-face.js"></script>'), html.index('skippy-3d.js"></script>')
    assert face < ctl                                            # the face class is defined before the controller uses it
    assert "importmap" not in html and "three" not in html.split('id="skippy3d"')[1][:3000]     # no 3D model any more
    for i in range(5):
        assert (STATIC / "skippy" / "face" / f"0{i}.png").is_file()          # the hub's five lean images of the can


def test_nothing_reaches_home_for_the_face_and_no_page_audio_is_rewired():
    js = (STATIC / "skippy-3d.js").read_text(encoding="utf-8") + (STATIC / "skippy-face.js").read_text(encoding="utf-8")
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "EventSource" not in js
    assert "createMediaElementSource" not in js                  # (that cut Skippy's voice after the first reply)


def test_the_page_tells_the_face_what_is_happening():
    voice = (STATIC / "skippy-voice.js").read_text(encoding="utf-8")
    assert 'face("skippy-speaking"' in voice and 'face("skippy-listening"' in voice
    chat = (PORTAL / "templates" / "_chat.html").read_text(encoding="utf-8")
    assert 'new CustomEvent("skippy-thinking"' in chat


def test_face_state_endpoint_is_gone():
    _, client = make()
    assert client.post("/face/state", json={"state": "thinking"}).status_code == 404
