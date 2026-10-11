"""Full screen on phones and tablets: the site manifest (Add to Home Screen opens with no browser bars), the full-screen key for browsers
that have the Fullscreen API, and the iPhone tip; the music and video apps keep their own manifests."""
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def client():
    return portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy()).test_client()


def test_the_site_manifest_asks_for_fullscreen_falls_back_to_standalone_and_needs_no_sign_in(client):
    r = client.get("/manifest.webmanifest")                                               # nobody is signed in: browsers fetch it without cookies
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    m = json.loads(r.data)
    assert m["display"] == "fullscreen" and m["display_override"] == ["fullscreen", "standalone"]
    assert m["start_url"] == "/" and m["scope"] == "/" and m["id"] == "/" and m["name"] and m["short_name"] == "Skippy"
    assert m["background_color"] == m["theme_color"] == "#000000"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes                                                # what Android's install criteria want
    for icon in m["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]
    assert b"private" not in r.data and b"admin" not in r.data.lower()                    # it names the site and nothing else


def test_the_icons_are_real_pngs_of_the_sizes_they_claim():
    PIL = pytest.importorskip("PIL.Image")
    for size in (192, 512):
        with PIL.open(STATIC / f"icon-{size}.png") as img:
            assert img.size == (size, size) and img.format == "PNG"


def manifest_links(page: str) -> list[str]:
    return re.findall(r'<link rel="manifest" href="([^"]+)"', page)


def test_every_page_offers_the_site_manifest_except_the_apps_that_bring_their_own(client):
    login(client, ADMIN)
    for path in ("/", "/apps", "/me", "/games", "/experiments"):
        page = client.get(path).data.decode()
        assert manifest_links(page) == ["/manifest.webmanifest"], path
        assert 'name="apple-mobile-web-app-capable" content="yes"' in page and 'name="mobile-web-app-capable"' in page, path
    assert manifest_links(client.get("/music").data.decode()) == ["/music/manifest.webmanifest"]          # Bilby keeps its own
    assert manifest_links(client.get("/video").data.decode()) == ["/video/manifest.webmanifest"]          # and so does Nagatha
    signed_out = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy()).test_client().get("/").data.decode()
    assert manifest_links(signed_out) == ["/manifest.webmanifest"]


def test_the_fullscreen_key_is_a_glyph_in_the_top_strip_hidden_until_the_browser_can_do_it(client):
    login(client, ADMIN)
    page = client.get("/").data.decode()
    assert re.search(r'<button type="button" class="key" id="fs-key" hidden aria-label="Full screen"', page)
    glyphs = (Path(__file__).resolve().parents[1] / "templates" / "_glyphs.html").read_text(encoding="utf-8")
    assert 'id="g-fs"' in glyphs and 'id="g-fsx"' in glyphs
    for need in ("document.fullscreenEnabled", "webkitFullscreenEnabled", "requestFullscreen", "exitFullscreen", "(display-mode: fullscreen)",
                 "navigator.standalone", "fullscreenchange", "Add to Home Screen"):
        assert need in page, need
    assert 'id="fs-tip"' in page
