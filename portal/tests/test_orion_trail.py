"""Orion Trail on Browser Games: a card that opens the portal page, the page frames the vendored game, and every scenario
file the game loads is in the image."""
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import play  # noqa: E402
from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402

import app as portal  # noqa: E402

GAME = Path(__file__).resolve().parents[1] / "static" / "orion-trail"


@pytest.fixture
def client():
    return portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy()).test_client()


def test_the_card_links_to_the_page_and_the_page_frames_the_game(client):
    assert any(g.slug == "orion-trail" and g.endpoint == "orion_trail" for g in play.GAMES)
    login(client, ADMIN)
    assert 'href="/play/orion-trail"' in client.get("/play").data.decode()
    page = client.get("/play/orion-trail").data.decode()
    assert '<iframe class="orion"' in page and "/static/orion-trail/orion-trail.html" in page
    assert '<h1 class="apptitle">Orion Trail</h1>' in page                       # the app bar, titled from <title>


def test_every_script_the_game_loads_is_vendored_and_served(client):
    html = (GAME / "orion-trail.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert len(scripts) == 12
    for src in scripts:
        assert (GAME / src).is_file(), src
        assert client.get(f"/static/orion-trail/{src}").status_code == 200
    assert "http://" not in html and "https://" not in html.replace("https://github.com", "")   # nothing from elsewhere
