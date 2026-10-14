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
    shipped = sorted(f"scenarios/{p.name}" for p in (GAME / "scenarios").glob("*.js"))
    assert sorted(scripts) == shipped and len(scripts) >= 19              # every scenario file shipped is loaded, none is missing (19 at 4edeb4f)
    for src in scripts:
        assert (GAME / src).is_file(), src
        assert client.get(f"/static/orion-trail/{src}").status_code == 200
    assert "http://" not in html and "https://" not in html.replace("https://github.com", "")   # nothing from elsewhere


def test_the_game_plays_on_a_phone_every_key_has_a_tap_and_the_page_gives_it_the_screen(client):
    html = (GAME / "orion-trail.html").read_text(encoding="utf-8")
    assert "max-width: 319px" in html and "at least 1024 pixels" not in html                # the wall is only for something narrower than a phone
    assert "viewport-fit=cover" in html and "@media (max-width: 900px)" in html and "(hover: none)" in html
    for tap in ('onclick="UI.dismiss()"', 'onclick="UI.dismiss(true)"', 'onclick="UI.back()"', "function dismiss(close)", "function back()"):   # CONTINUE, CLOSE, BACK and their keys
        assert tap in html, tap
    assert "typeTimer && modalState && modalState.fullText != null" in html                  # a tap on the typing text shows it all
    login(client, ADMIN)
    page = client.get("/play/orion-trail").data.decode()
    assert "Needs a window at least 1024 px wide" not in page and "Works on a phone" in page and "@media (max-width: 700px)" in page
