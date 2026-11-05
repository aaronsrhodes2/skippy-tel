"""The direct front door, the page's half (static/direct.js; its logic is tested under Node in tests/js/direct.test.js and
tests/js/voice/direct-door.test.js). What is held here is the wiring, which no Node test sees:

  1. base.html loads direct.js in the head of every page, ahead of the page's own scripts, so a script that fetches a gate finds
     window.SkippyDirect (and tolerates its absence anyway);
  2. every script that turns a gate pass into `<url>/t/<pass>/...` addresses is known to this test, and the one that READS the pass
     asks SkippyDirect.pick for the door right after parsing it. A new gate user fails here until it is classified, so a page cannot
     quietly keep going through the Funnel (or go through a door nobody looked at).
"""
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

PORTAL = Path(__file__).resolve().parents[1]
STATIC, TEMPLATES = PORTAL / "static", PORTAL / "templates"

# `${url}/t/${pass}` in a template string, or "/t/" joined to a pass: how a page turns a gate pass into an address.
GATE_URL = re.compile(r"""\}/t/\$\{|["'`]/t/["'`]""")
# Each file that builds those addresses -> the file that reads the pass from the portal and so picks the door. (nagatha-core.js only holds the
# pure URL builders; nagatha-app.js reads the pass.)
BUILDS_GATE_URLS = {
    "static/audiobooks-app.js": "static/audiobooks-app.js",
    "static/bilby-app.js": "static/bilby-app.js",
    "static/nagatha-core.js": "static/nagatha-app.js",
    "static/skippy-voice.js": "static/skippy-voice.js",
    "templates/_chat.html": "templates/_chat.html",             # the pop-up upload's one-file pass
    "static/drive-app.js": "static/drive-app.js",
}
# A script that reads a gate pass and does not pick the door yet is listed here rather than failing the build. (Drive's page, the Tesla's and the glasses', was the last: wired 2026-11-05.)
PENDING: set[str] = set()


def _files_that_build_gate_urls() -> set[str]:
    found = set()
    for kind, suffix in (("static", ".js"), ("templates", ".html")):
        for path in (PORTAL / kind).rglob("*" + suffix):
            if "vendor" in path.parts or path.name == "direct.js":           # (the door itself only mentions the shape in its header)
                continue
            if GATE_URL.search(path.read_text(encoding="utf-8", errors="replace")):
                found.add(path.relative_to(PORTAL).as_posix())
    return found


def test_base_loads_direct_js_in_the_head_ahead_of_everything_a_page_adds():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    at = base.index("filename='direct.js'")
    assert at < base.index("{% block head %}") < base.index("</head>")
    assert at < base.index("{% block content %}")
    tag = re.search(r"<script\b[^>]*filename='direct\.js'[^>]*>", base).group(0)
    assert "defer" not in tag and "async" not in tag and "module" not in tag      # a plain, synchronous script: it has run before any page script starts


def test_every_page_that_fetches_a_gate_gets_direct_js_before_its_own_script():
    app = portal.create_app({**SETTINGS, "CHAT_ENABLED": True}, vm=FakeVm(), skippy=FakeSkippy())
    client = app.test_client()
    login(client, ADMIN)
    first_script = {"/": "skippy-voice.js", "/music": "bilby-player.js", "/video": "nagatha-core.js", "/audiobooks": "audiobooks-app.js",
                    "/drive": "skippy-voice.js", "/hud": "skippy-voice.js"}
    for page, script in first_script.items():
        resp = client.get(page)
        assert resp.status_code == 200, page
        html = resp.get_data(as_text=True)
        assert "/static/direct.js" in html, page
        assert html.index("/static/direct.js") < html.index(script), f"{page}: direct.js must come before {script}"


def test_every_script_that_builds_gate_addresses_is_known_here():
    assert _files_that_build_gate_urls() == set(BUILDS_GATE_URLS), (
        "a script builds `<url>/t/<pass>` addresses and this test does not know it (or no longer does): decide how it uses the direct door "
        "(SkippyDirect.pick right after the pass is parsed), then list it in BUILDS_GATE_URLS")


def test_the_scripts_that_read_a_gate_pass_pick_the_door_and_do_without_direct_js():
    for built_in, reads_the_pass in sorted(BUILDS_GATE_URLS.items()):
        text = (PORTAL / reads_the_pass).read_text(encoding="utf-8")
        if "SkippyDirect" not in text:
            assert reads_the_pass in PENDING, f"{reads_the_pass} reads a gate pass (it builds addresses in {built_in}) and never asks SkippyDirect.pick"
            continue
        assert re.search(r"SkippyDirect\.pick\(\w+", text), reads_the_pass
        assert re.search(r"(?:window|root)\.SkippyDirect\b|typeof\s+SkippyDirect\b", text), f"{reads_the_pass} must tolerate a page without direct.js"


def test_the_drive_page_picks_the_door_for_all_three_of_its_gates_and_goes_on_by_the_funnel_when_the_door_gives_out():
    """The Tesla's /drive and the glasses' /hud (one script): the book, the music and the wake-word gates each go through `door(...)`, a song or a
    book that fails on the door resumes from the Funnel where it stopped, and a call with no answer is made once more through the Funnel."""
    text = (STATIC / "drive-app.js").read_text(encoding="utf-8")
    for gate in ("bookGate", "musicGate", "voiceGate"):
        assert re.search(r"door\(await getJSON\(cfg\." + gate + r"\)\)", text), f"{gate} is read and not put through the door"
    assert "function resumeOnFunnel" in text and re.search(r'au\.addEventListener\("error",\s*\(\)\s*=>\s*\{\s*if \(resumeOnFunnel\(\)\) return;', text)
    assert "onceMore(S.music" in text and "onceMore(S.book" in text
    assert "leaveDoor(g)" in text                                  # the wake word's retry through the Funnel


def test_every_player_has_a_way_back_to_the_funnel_when_the_door_it_picked_gives_out():
    """Review 2026-11-05: a door that dies AFTER the pick must not leave a page retrying a dead address for the life of its pass."""
    bilby = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    assert re.search(r"onDoor\s*=\s*!!\(gate\.info && gate\.info\.funnel && gate\.info\.url !== gate\.info\.funnel\)", bilby)
    assert re.search(r"\(!gate\.fresh\(\) \|\| onDoor\)\) \{ try \{ await gate\.ensure\(true\)", bilby)       # Bilby's signal-loss loop looks at the door again
    books = (STATIC / "audiobooks-app.js").read_text(encoding="utf-8")
    assert "function leaveDoor" in books and re.search(r'au\.addEventListener\("error",.{0,700}?leaveDoor\(\)', books, re.S)
    assert "boot(!left)" in books                                                    # a catalog fetch with no answer goes on by the Funnel at once
    films = (STATIC / "nagatha-app.js").read_text(encoding="utf-8")
    assert "gate.ensure(!!again)" in films and "() => loadCatalog(true)" in films and "SkippyDirect.demote(g)) continue" in films
    chat = (TEMPLATES / "_chat.html").read_text(encoding="utf-8")
    assert "const put = () => fetch(g.url" in chat and "SkippyDirect.demote(g)))" in chat    # the upload: once more through the Funnel
    assert "RENEW_S" in (STATIC / "direct.js").read_text(encoding="utf-8")           # a pass that came with no door is asked for again soon


def test_the_portal_warms_the_door_probe_at_start_so_the_first_pass_after_a_deploy_already_knows():
    text = (PORTAL / "app.py").read_text(encoding="utf-8")
    assert re.search(r'made\.extensions\["edge"\]\.alive\(\)', text)


def test_a_pass_that_is_picked_is_picked_before_anything_stores_or_uses_its_url():
    """The pick sits between 'the pass was read and is ok' and 'its url is used', in each script that reads one."""
    def order(path, first, then):
        text = (PORTAL / path).read_text(encoding="utf-8")
        a, b = text.index(first), text.index(then, text.index(first))
        assert a < b, f"{path}: {first!r} must come before {then!r}"
    order("static/audiobooks-app.js", "SkippyDirect.pick(g", "S.url = g.url")
    order("static/bilby-app.js", "SkippyDirect.pick(g", "const changed = !this.info || this.info.pass !== g.pass || this.info.url !== g.url")
    order("static/nagatha-app.js", "SkippyDirect.pick(g", "this.info = g;")
    order("static/skippy-voice.js", "SkippyDirect.pick(d", "pass = d;")
    order("templates/_chat.html", "SkippyDirect.pick(g", 'g.url + "/t/" + g.pass')
