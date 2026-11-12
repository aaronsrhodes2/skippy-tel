"""The glasses view (hud.py): /hud/test says what the S23's browser can do; /hud is Drive's page in its hud mode, with purple Skippy
(the agent) behind the voice key, a hands-off hub mode like the pods, and the apps one key away."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")
import app as portal  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402


@pytest.fixture
def client():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    c = app.test_client(); login(c, ADMIN)
    return c


def test_hud_is_drive_with_purple_skippy_a_hub_key_and_the_apps(client):
    page = client.get("/hud").get_data(as_text=True)
    assert 'id="drive" class="hud"' in page and 'data-app="hud"' in page
    assert 'data-ws-send="/me/workshop/send"' in page and 'data-ws-events="/me/workshop/events"' in page   # purple behind the voice
    assert 'id="k-hub" aria-pressed="false"' in page and 'data-tel="hud:apps"' in page and 'id="said"' in page
    assert 'aria-label="Purple Skippy"' in page and 'id="k-play"' in page and 'id="k-house"' in page       # Drive's keys, all of them


def test_hud_has_the_camera_key_and_a_forget_key_that_starts_hidden_and_the_vision_addresses(client):
    page = client.get("/hud").get_data(as_text=True)
    assert 'id="k-flash"' in page and 'data-tel="hud:flash"' in page and "#g-camera" in page
    assert 'id="k-forget" hidden' in page and 'data-tel="hud:forget"' in page            # offered for 10 s after a flash
    assert 'data-vision="/vision/flash"' in page and 'data-vision-delete="/vision/moment/_MID_/delete"' in page
    assert 'data-vision-keep="/vision/moment/_MID_/keep"' in page and 'data-vision-add="/vision/moment/_MID_/file"' in page
    assert "vendor/html2canvas.min.js" in page and "static/vision.js" in page


def test_hud_has_the_battery_lamp_and_the_sign_in_key_that_the_script_shows_only_in_a_normal_browser(client):
    page = client.get("/hud").get_data(as_text=True)
    assert 'id="k-batt"' in page and "#g-battery" in page
    assert 'id="k-link" hidden' in page and 'data-tel="hud:sign-glasses-in"' in page and 'data-link-new="/auth/link/new"' in page
    assert 'id="k-batt"' not in client.get("/drive").get_data(as_text=True)


def test_the_tesla_drive_page_has_no_camera_key_but_joins_a_moment_by_adding_its_own_picture(client):
    page = client.get("/drive").get_data(as_text=True)
    assert 'id="k-flash"' not in page and 'data-vision="' not in page
    assert 'data-vision-moments="/vision/moments"' in page and 'data-vision-add="/vision/moment/_MID_/file"' in page


def test_drive_itself_is_unchanged(client):
    page = client.get("/drive").get_data(as_text=True)
    assert 'id="drive"' in page and 'id="drive" class="hud"' not in page and "data-ws-send" not in page and 'id="k-hub"' not in page


def test_the_test_page_and_the_view_are_the_captains_alone():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    friend = app.test_client(); login(friend, FRIEND)
    for path in ("/hud", "/hud/test"):
        assert friend.get(path).status_code in (302, 403)


def test_the_glasses_are_a_launcher_tile_for_the_captain(client):
    assert 'data-tel="tile:hud"' in client.get("/apps").get_data(as_text=True)


def test_hud_has_a_captions_strip_and_the_car_does_not(client):
    assert 'id="heard"' in client.get("/hud").get_data(as_text=True)
    assert 'id="heard"' not in client.get("/drive").get_data(as_text=True)         # in the car, foreign speech stays a message to Skippy


def test_the_note_purple_gets_about_what_was_heard_is_the_chats_own_text():
    """drive-pure.js heardNote and chat.heard_note must say the same: purple reads both the same way."""
    import chat
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "drive-pure.js").read_text(encoding="utf-8")
    note = chat.heard_note(["- (es) x"])
    assert note.split("\n")[0] in js                                   # the header line, word for word
    assert note.endswith("]\n\n") and r'+ "]\n\n"' in js              # and the closing bracket and blank line
    app_js = (Path(__file__).resolve().parents[1] / "static" / "drive-app.js").read_text(encoding="utf-8")
    assert "heardBox ? { onHeard, wantOriginal: true } : {}" in app_js  # only /hud turns foreign speech into captions
