"""Tests for the Skippy 2D face in the chat corner (templates/_skippy3d.html, static/skippy-3d.js)."""

import json

import pytest

from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402
import app as portal  # noqa: E402

PAL = "aaron.s.rhodes@gmail.com"


def make():
    vm = FakeVm()
    vm.metadata["portal-friends"] = json.dumps({PAL: {"portals": ["valheim", "skippy"], "added_by": ADMIN}})
    app = portal.create_app({**SETTINGS, "ALLOWED_EMAILS": frozenset({ADMIN}), "TELEMETRY_ENABLED": True},
                            vm=vm, skippy=FakeSkippy())
    client = app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
    return app, client, app.extensions["telemetry"]


def test_skippy3d_template_has_canvas():
    """The skippy3d template carries a canvas element, not an MJPEG img."""
    _, client, _ = make()
    resp = client.get("/chat")
    assert resp.status_code == 200
    assert b'<canvas' in resp.data
    assert b's3-face-canvas' in resp.data
    assert b'/face/stream' not in resp.data


def test_face_state_endpoint_gone():
    """POST /face/state is gone — face rendering moved to the browser."""
    app, client, _ = make()
    resp = client.post("/face/state", json={"state": "thinking"})
    assert resp.status_code == 404
