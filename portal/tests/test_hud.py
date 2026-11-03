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
