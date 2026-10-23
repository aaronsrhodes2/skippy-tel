"""The camera key in Skippy's chat (static/skippy-camera.js): the Captain photographs another screen from his iPad and the photo goes
with the message exactly like a pasted picture. Here: only he gets the key and the script, the photo joins the same list pasted
pictures join, its size budget stays inside what the agent (workshop.clean_images) and the chat (attachments.clean) accept, and the
camera's own behaviour passes its node tests (tests/js/camera)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import attachments  # noqa: E402
import workshop  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

PORTAL = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    app.extensions["home"].accept({"capabilities": {"workshop": {"ready": True, "model": "claude-sonnet-5-5"}}})
    return app.test_client()


def test_the_captain_gets_the_camera_key_and_its_script(client):
    login(client, ADMIN)
    page = client.get("/").data.decode()
    assert 'class="key cam"' in page and "#g-camera" in page
    assert "skippy-camera.js" in page
    assert 'id="g-camera"' in page and 'id="g-flip"' in page          # its glyphs exist


def test_nobody_else_gets_the_camera(client):
    login(client, FRIEND)
    page = client.get("/").data.decode()
    assert 'class="key cam"' not in page
    assert "skippy-camera.js" not in page


def test_a_photo_goes_where_a_pasted_picture_goes(client):
    login(client, ADMIN)
    page = client.get("/").data.decode()
    wiring = page[page.index('const camKey = form.querySelector("button.cam")'):]
    wiring = wiring[:wiring.index("\n    }\n")]
    assert "pending_files.push(" in wiring and "paintFiles()" in wiring  # the same list the clip key, paste and drop fill
    assert "MAX_FILES" in wiring                                         # and its count limit


def test_the_photo_budget_fits_both_servers_limits(client):
    login(client, ADMIN)
    page = client.get("/").data.decode()
    each = int(re.search(r"PHOTO_MAX = (\d+)", page).group(1))
    total = int(re.search(r"PHOTOS_MAX = (\d+)", page).group(1))
    prefix = len("data:image/jpeg;base64,")
    assert each - prefix <= workshop.MAX_IMAGE_B64 and each - prefix <= attachments.MAX_B64_EACH
    assert total <= workshop.MAX_IMAGES_B64 and total <= attachments.MAX_B64_TOTAL


def _node():
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        return exe if int(out.lstrip("v").split(".")[0]) >= 18 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@pytest.mark.skipif(_node() is None, reason="Node 18+ isn't installed here")
def test_the_camera_passes_its_node_tests():
    d = PORTAL / "tests" / "js" / "camera"
    run = subprocess.run([_node(), "--test", *sorted(str(p) for p in d.glob("*.test.js"))], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300, cwd=str(d))
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1500:]
