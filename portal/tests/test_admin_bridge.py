"""Admin on the bridge: a deck with a control per job, a pane per control, panes that are pages you flip. What is checked without a
browser: the markup (every control opens a pane that exists, every section is still there with its words), the stylesheet and script
(nothing scrolls, nothing is fetched), and that the logs page is a bridge page too."""
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from test_app import ADMIN, env, login  # noqa: E402,F401

PORTAL = Path(__file__).resolve().parents[1]
STATIC = PORTAL / "static"


def test_admin_is_a_bridge_page_with_a_control_for_each_pane(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/admin").data.decode()
    assert '<body class="bridge"' in page and 'href="/static/bridge-panes.css"' in page and "/static/bridge-panes.js" in page
    controls = re.findall(r'data-pane="([\w-]+)"', page)
    panes = re.findall(r'<section id="([\w-]+)" class="apane"', page)
    assert controls and sorted(controls) == sorted(panes), (controls, panes)
    assert {"graveyard", "server", "recovery-pane", "setup", "settings", "friends", "discord"} == set(panes)
    for pane in panes:                                                                    # each pane has its way back, its page flow and its bar
        part = page.split(f'<section id="{pane}" class="apane"', 1)[1]
        part = part.split('<section id="', 1)[0] if pane != "recovery-pane" else part.split('<section id="setup"', 1)[0].split('<script', 1)[0]
        assert "data-close" in part and 'class="pg-flow"' in part and 'class="pips"' in part, pane
    assert 'name="portals"' in page and "Restart &amp; update" in page and "Save settings" in page    # the sentences and forms are all still here


def test_the_deck_says_what_needs_the_captain(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/admin").data.decode()
    server = page.split('data-pane="server"', 1)[0].rsplit('<div class="unit', 1)[1]
    assert re.search(r'data-s="(on|idle|boot|off)"', server)                              # the server's lamp


def test_nothing_on_the_admin_pages_scrolls_or_fetches():
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "bridge-panes.css").read_text(encoding="utf-8"), flags=re.S)
    js = (STATIC / "bridge-panes.js").read_text(encoding="utf-8")
    assert not re.search(r"overflow(?:-y)?\s*:\s*(?:auto|scroll)", css) and "gradient" not in css
    assert not re.search(r"\b(?:fetch|sendBeacon|XMLHttpRequest|WebSocket)\b|setInterval\s*\(", js)
    assert "Bridge.pager" in js and "sessionStorage" in js                              # pages, and the pane survives a form post


def test_a_field_in_a_later_page_flips_to_its_page_instead_of_scrolling_the_clipped_panel():
    js = (STATIC / "bridge.js").read_text(encoding="utf-8")
    assert 'addEventListener("focusin"' in js and "scrollLeft = 0" in js


def test_the_logs_page_is_a_bridge_page(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/admin/logs").data.decode()
    assert '<body class="bridge"' in page and 'class="pg"' in page and "Bridge.pager" in page
    assert "pre.log {" not in page and "overflow: auto" not in page.split("</head>", 1)[1].split("<body", 1)[0] + page.split('<section class="adm"', 1)[1]   # no scrolled box
    assert "Game containers" in page and "Last boot" in page


def test_the_dashboard_is_a_bridge_page_of_widgets_and_the_private_skippy_has_moved_to_the_front_page(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/me").data.decode()
    assert '<body class="bridge"' in page and "/static/bridge-panes.js" in page and 'href="/static/bridge-panes.css"' in page
    controls = re.findall(r'data-pane="([\w-]+)"', page)
    panes = re.findall(r'<section id="([\w-]+)" class="apane"', page)
    assert controls[:2] == ["mcp", "home"] and sorted(controls) == sorted(panes), (controls, panes)
    assert 'id="private-skippy"' not in page and "skippy" not in controls                 # the chat is the front page now (front.html)
    for widget in ("Server", "Core · skippy-tel", "Home · skippy-pc", "Watcher", "Spend (estimate)", "Friends", "Discord, lately", "Home links"):
        assert f"<h3>{widget}</h3>" in page, widget                                      # every widget is still there
    front = client.get("/chat").data.decode()
    assert 'id="ps-flow"' in front and 'class="wlog pg"' not in front                  # the chat (the front page) is a scrolling transcript, not pages


def test_the_experiments_inventory_is_a_public_bridge_page_of_tiles_shown_a_screen_at_a_time(env):
    client, _, _ = env
    page = client.get("/experiments").data.decode()                                      # signed out: it is public
    assert '<body class="bridge"' in page and 'href="/static/experiments.css"' in page and "Bridge.PList" in page
    assert 'aria-label="Sign in"' in page                                                # the strip says how to sign in
    for need in ('id="find"', 'id="kind-key"', 'id="scrub"', 'id="about"'):
        assert need in page, need
    assert len(re.findall(r'<a class="tile( featured)?"', page)) == 5 and "data-tel=\"demo:nbody\"" in page    # the tiles are the page's data and its tap beacons
    glyphs = set(re.findall(r'<symbol id="g-([\w-]+)"', (PORTAL / "templates" / "_glyphs.html").read_text(encoding="utf-8")))
    drawn = set(re.findall(r'(?:live|recorded|results|tool|local): "(\w+)"', page)) | {"tools", "atom", "info", "search", "x"}
    assert drawn <= glyphs, drawn - glyphs                                               # every kind has a drawn glyph
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "experiments.css").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"overflow(?:-y)?\s*:\s*(?:auto|scroll)", css) and "gradient" not in css
