"""The official art on each game's page (portal/static/games, provenance in docs/portal/game-art.md): the files are there
and light enough for a phone, every one has its provenance entry, and the page uses them without breaking the admin's own
banner setting."""
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

import catalog  # noqa: E402
from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402

import app as portal  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "portal" / "static"
DOC = (ROOT / "docs" / "portal" / "game-art.md").read_text(encoding="utf-8")
MAX_BG_KB, MAX_HERO_KB = 400, 300           # a phone downloads both on every visit to a game's page


@pytest.fixture
def client():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    c = app.test_client()
    login(c, ADMIN)
    return c, app.extensions.get("vm") or app


def themes():
    return [(g.id, g.theme) for g in catalog.GAMES.values()]


@pytest.mark.parametrize("gid,theme", themes())
def test_every_game_has_its_two_images_light_and_wide_enough(gid, theme):
    for role, rel, limit in (("bg", theme.bg_image, MAX_BG_KB), ("hero", theme.hero_image, MAX_HERO_KB)):
        assert rel == f"games/{gid}-{role}.jpg", f"{gid}: the {role} image should be games/{gid}-{role}.jpg"
        path = STATIC / rel
        assert path.is_file(), f"{path} is missing"
        assert path.stat().st_size <= limit * 1024, f"{rel} is {path.stat().st_size // 1024} KB, over {limit} KB"
        with Image.open(path) as im:
            assert im.format == "JPEG" and im.width >= 1400, f"{rel}: {im.format} {im.size}"


def test_every_image_on_disk_has_a_provenance_entry_and_nothing_stray_ships():
    on_disk = sorted(p.name for p in (STATIC / "games").iterdir())
    assert on_disk == sorted(f"{gid}-{role}.jpg" for gid, _ in themes() for role in ("bg", "hero"))   # no orphans
    for name in on_disk:
        assert f"| {name} |" in DOC, f"{name} has no row in docs/portal/game-art.md"
    for row in re.findall(r"^\| ([a-z]+-(?:bg|hero)\.jpg) \| (\S+)", DOC, re.M):
        assert row[1].startswith("https://"), f"{row[0]}: the source must be a URL"


@pytest.mark.parametrize("gid,theme", themes())
def test_the_page_shows_the_backdrop_and_the_shipped_banner(client, gid, theme):
    c, _ = client
    html = c.get(f"/games/{gid}").get_data(as_text=True)
    assert f"/static/{theme.bg_image}" in html and 'classList.add("game-art")' in html     # the dimmed full-page backdrop (bridge.css: body.game-art)
    assert f'<img src="/static/{theme.hero_image}"' in html                          # the banner behind the title
    assert f"object-position: {theme.hero_focus}" in html
    assert c.get(f"/static/{theme.bg_image}").status_code == 200 and c.get(f"/static/{theme.hero_image}").status_code == 200


def test_an_admin_set_banner_still_wins_over_the_shipped_one(client):
    c, _ = client
    c.post("/admin/settings", data={"DRAGONWILDS_BANNER_URL": "https://img.example/mine.jpg"})
    html = c.get("/games/dragonwilds").get_data(as_text=True)
    assert 'src="https://img.example/mine.jpg"' in html and "dragonwilds-hero.jpg" not in html
    assert "/static/games/dragonwilds-bg.jpg" in html                                # the backdrop is not a setting
