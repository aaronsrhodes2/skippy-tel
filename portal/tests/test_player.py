"""Bilby's Audio Service (the browser player at /music): the portal signs passes the home music-gate accepts (same key from the
home token, no new secret), only for a gate address on the tailnet, and keeps each device's place so another can pick up or act as
its remote, and knows when a device's browser still needs a tap before it will make sound. Admin only.

The player itself (static/bilby-app.js) runs in a browser: what can be checked without one is checked here (the routes, the page's
markup against the ids the script looks up, that it is a page of the bridge: one screen, glyphs, no scrolling; the stylesheet's use of
the portal's tokens); its pure logic is run under Node by test_player_js.py when Node is installed."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import player as player_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

TOKEN = "the-home-token"
GATE = "https://skippy-pc.tailab2cc7.ts.net"
PORTAL = Path(__file__).resolve().parents[1]
STATIC = PORTAL / "static"
NAME = "Bilby's Audio Service"


def _gate_module():
    """services/music-gate/gate.py, loaded by path: the other half of the handshake."""
    root = Path(__file__).resolve().parents[2] / "services" / "music-gate"
    import sys
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("music_gate_gate", root / "gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def paired_home(clock, caps=None):
    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    home = home_mod.Home(vm, clock=clock)
    home.accept({"capabilities": caps if caps is not None else {"music_gate": {"ready": True, "url": GATE}}})
    return home


# ── the pass ──────────────────────────────────────────────────────────────────
def test_the_pass_opens_the_home_gate_and_nothing_else():
    clock = Clock()
    g = player_mod.Player(paired_home(clock), clock=clock).gate()
    assert g["ok"] and g["url"] == GATE
    assert player_mod.PASS_S <= g["exp"] - clock.now <= player_mod.PASS_S + player_mod.PASS_BUCKET_S
    gate = _gate_module()
    assert gate.valid(gate.gate_key(TOKEN), g["pass"], clock.now)              # the same key on both sides
    assert not gate.valid(gate.gate_key("another-token"), g["pass"], clock.now)
    assert not gate.valid(gate.gate_key(TOKEN), g["pass"], g["exp"] + 1)       # and it runs out


def test_a_pass_is_never_shorter_than_a_listening_session_and_never_longer_than_the_gate_takes():
    """exp sits on a six-hour grid (so the URL, which carries the pass, can be cached by the browser across page loads); whatever
    the time of day, a pass handed out has at least PASS_S left and is not one the gate refuses for being too long."""
    gate = _gate_module()
    key = gate.gate_key(TOKEN)
    clock = Clock()
    home = paired_home(clock)
    p = player_mod.Player(home, clock=clock)
    base = clock.now
    for step in range(0, 3 * player_mod.PASS_BUCKET_S, 997):
        clock.now = base + step
        home.accept({"capabilities": {"music_gate": {"ready": True, "url": GATE}}})        # home keeps reporting
        g = p.gate()
        assert g["exp"] - clock.now >= player_mod.PASS_S, step
        assert gate.valid(key, g["pass"], clock.now), step
        assert g["exp"] - clock.now <= gate.MAX_PASS_S


def test_every_pass_inside_a_bucket_is_the_same_string_and_the_next_bucket_has_a_new_one():
    clock = Clock()
    home = paired_home(clock)
    p = player_mod.Player(home, clock=clock)
    caps = {"capabilities": {"music_gate": {"ready": True, "url": GATE}}}
    start = clock.now - clock.now % player_mod.PASS_BUCKET_S
    clock.now = start
    home.accept(caps)
    first = p.gate()
    clock.now = start + player_mod.PASS_BUCKET_S - 1
    home.accept(caps)
    assert p.gate()["pass"] == first["pass"]
    clock.now = start + player_mod.PASS_BUCKET_S
    home.accept(caps)
    assert p.gate()["pass"] != first["pass"]


@pytest.mark.parametrize("caps, why", [
    ({"music_gate": {"ready": False, "url": GATE}}, "isn't answering"),
    ({"music_gate": {"ready": True, "url": "https://evil.example.com"}}, "isn't answering"),   # never sent off the tailnet
    ({"music_gate": {"ready": True, "url": "http://skippy-pc.tailab2cc7.ts.net"}}, "isn't answering"),
    ({"music_gate": {"ready": True, "url": "https://skippy-pc.tailab2cc7.ts.net.evil.example.com"}}, "isn't answering"),
    ({}, "isn't answering"),
])
def test_no_pass_unless_the_gate_is_up_on_the_tailnet(caps, why):
    clock = Clock()
    g = player_mod.Player(paired_home(clock, caps), clock=clock).gate()
    assert not g["ok"] and why in g["why"] and "pass" not in g


def test_a_local_gate_is_signed_for_only_when_a_dev_tool_hands_in_the_pattern():
    """tests/dev_music.py runs the player against a gate on this machine. That is a constructor argument nothing in production
    passes; the default still refuses anything that is not on the tailnet."""
    clock = Clock()
    caps = {"music_gate": {"ready": True, "url": "http://127.0.0.1:8766"}}
    assert not player_mod.Player(paired_home(clock, caps), clock=clock).gate()["ok"]
    dev = player_mod.Player(paired_home(clock, caps), clock=clock, gate_url_re=re.compile(r"http://127\.0\.0\.1:\d+"))
    assert dev.gate()["ok"] and dev.gate()["url"] == "http://127.0.0.1:8766"
    assert "gate_url_re" not in (PORTAL / "app.py").read_text(encoding="utf-8")                     # create_app never sets it


def test_no_pass_when_home_is_silent_or_unpaired():
    clock = Clock()
    home = paired_home(clock)
    clock.now += home_mod.STALE_AFTER + 1
    assert "isn't reporting" in player_mod.Player(home, clock=clock).gate()["why"]
    assert "isn't paired" in player_mod.Player(home_mod.Home(FakeVm(), clock=clock), clock=clock).gate()["why"]


# ── Connect: where each device is, hand over, remote ──────────────────────────────
def state(**kw):
    return {"track": "A/B/one.mp3", "title": "One", "artist": "A", "pos": 30, "playing": True,
            "queue": ["A/B/one.mp3", "A/B/two.mp3"], "idx": 0, **kw}


def test_another_device_sees_where_this_one_is_and_can_pick_up():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "phone1234", "iPhone", state())
    clock.now += 20
    [dev] = p.checkin(ADMIN, "tesla1234", "Tesla", None)["devices"]
    assert dev["name"] == "iPhone" and dev["live"] and dev["state"]["pos"] == 50.0      # 30 s in, 20 s ago
    assert dev["state"]["queue"] == ["A/B/one.mp3", "A/B/two.mp3"]
    assert p.checkin(FRIEND, "tesla1234", "Tesla", None)["devices"] == []                # nobody else's music
    clock.now += player_mod.LIVE_S
    [dev] = p.checkin(ADMIN, "tesla1234", "Tesla", None)["devices"]
    assert not dev["live"] and not dev["state"]["playing"]                               # still offered, as "left off"


def test_remote_commands_reach_only_a_live_device_once():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "phone1234", "iPhone", state())
    assert p.command(ADMIN, "phone1234", "pause") and p.command(ADMIN, "phone1234", "next")
    assert not p.command(ADMIN, "phone1234", "rm -rf")
    assert not p.command(FRIEND, "phone1234", "pause")
    assert p.checkin(ADMIN, "phone1234", "iPhone", None)["commands"] == ["pause", "next"]
    assert p.checkin(ADMIN, "phone1234", "iPhone", None)["commands"] == []
    clock.now += player_mod.LIVE_S + 1
    assert not p.command(ADMIN, "phone1234", "play")                                      # closed: nothing to queue for


def test_a_state_is_trimmed_to_sane_fields():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "phone1234", "x" * 500, {**state(), "queue": ["q"] * 5000 + [7, "z" * 999], "idx": 99999,
                                              "pos": "nope", "evil": "<script>"})
    [dev] = p.checkin(ADMIN, "other1234", "PC", None)["devices"]
    assert len(dev["name"]) == 40 and len(dev["state"]["queue"]) == player_mod.MAX_QUEUE
    assert dev["state"]["idx"] == 0 and "evil" not in dev["state"]


def test_repeat_shuffle_and_dj_are_kept_only_as_known_values():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "phone1234", "iPhone", state(repeat="one", shuffle=True, dj=True))
    [dev] = p.checkin(ADMIN, "other1234", "PC", None)["devices"]
    assert (dev["state"]["repeat"], dev["state"]["shuffle"], dev["state"]["dj"]) == ("one", True, True)
    p.checkin(ADMIN, "phone1234", "iPhone", state(repeat="<b>", shuffle="yes", dj=1))
    [dev] = p.checkin(ADMIN, "other1234", "PC", None)["devices"]
    assert (dev["state"]["repeat"], dev["state"]["shuffle"], dev["state"]["dj"]) == ("off", False, False)


def test_a_check_in_without_a_queue_keeps_the_one_held_and_moves_along_it():
    """A queue window is ~50 KB: a player sends it only when it changed, and then just says which place it is at."""
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "phone1234", "iPhone", state())
    body = state(idx=1, track="A/B/two.mp3", title="Two")
    del body["queue"]
    p.checkin(ADMIN, "phone1234", "iPhone", body)
    [dev] = p.checkin(ADMIN, "other1234", "PC", None)["devices"]
    assert dev["state"]["queue"] == ["A/B/one.mp3", "A/B/two.mp3"] and dev["state"]["idx"] == 1
    body["idx"] = 7                                                                      # beyond what is held: back to the start
    p.checkin(ADMIN, "phone1234", "iPhone", body)
    assert p.checkin(ADMIN, "other1234", "PC", None)["devices"][0]["state"]["idx"] == 0
    for bad in (True, "1", -1, 1.5, None):                                               # a bool is not an index
        p.checkin(ADMIN, "phone1234", "iPhone", {**body, "idx": bad})
        assert p.checkin(ADMIN, "other1234", "PC", None)["devices"][0]["state"]["idx"] == 0, bad


# ── the browser's autoplay rule, as the portal sees it ────────────────────────────────
def test_a_device_says_whether_its_browser_has_made_a_sound_since_it_loaded():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "phone1234", "iPhone", state())                                    # an older page: says nothing
    assert not p.needs_tap(ADMIN, "phone1234")
    p.checkin(ADMIN, "phone1234", "iPhone", state(armed=False, tap=True))                # reloaded, nobody has tapped yet
    assert p.needs_tap(ADMIN, "phone1234")
    p.checkin(ADMIN, "phone1234", "iPhone", state(armed=False))                          # loaded but not started
    assert p.needs_tap(ADMIN, "phone1234")
    p.checkin(ADMIN, "phone1234", "iPhone", state(tap=True))                             # says only that it is waiting on a tap
    assert p.needs_tap(ADMIN, "phone1234")
    p.checkin(ADMIN, "phone1234", "iPhone", state(armed=True, tap=True))                 # a play had worked, then the browser took the sound back
    assert p.needs_tap(ADMIN, "phone1234")
    p.checkin(ADMIN, "phone1234", "iPhone", state(armed=True, tap=False))                # a play() worked: nothing more is needed
    assert not p.needs_tap(ADMIN, "phone1234")
    p.checkin(ADMIN, "phone1234", "iPhone", state(armed="yes", tap="yes"))               # only real booleans count
    assert not p.needs_tap(ADMIN, "phone1234")
    [dev] = p.checkin(ADMIN, "other1234", "PC", None)["devices"]
    assert dev["state"]["armed"] is None and dev["state"]["tap"] is False
    assert not p.needs_tap(ADMIN, "nobody123") and not p.needs_tap(FRIEND, "phone1234")   # unknown device, or someone else's


# ── the routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    return app.test_client()


def test_music_needs_the_audio_grant(client):
    assert client.get("/music").status_code == 302                      # signed out: to sign-in
    _add_friend(client, "pal@example.com", ["valheim", "skippy", "video", "play", "experiments"])
    login(client, "pal@example.com")
    for r in (client.get("/music"), client.get("/music/gate"),
              client.post("/music/checkin", json={"device": "phone1234"}),
              client.post("/music/command", json={"device": "phone1234", "action": "pause"})):
        assert r.status_code == 403
    assert b'data-tel="tile:music"' not in client.get("/").data


def test_the_captain_gets_the_tile_the_player_and_a_pass(client):
    login(client, ADMIN)
    launcher = client.get("/apps").data.decode()
    assert 'data-tel="tile:music"' in launcher and f'aria-label="{NAME.replace(chr(39), "&#39;")}"' in launcher
    page = client.get("/music").data.decode()
    assert 'rel="manifest"' in page and "mediaSession" in (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    g = client.get("/music/gate").get_json()
    assert g["ok"] and g["url"] == GATE
    assert client.post("/music/checkin", json={"device": "bad id!"}).status_code == 400
    assert client.post("/music/checkin", json={"device": "phone1234", "name": "iPhone", "state": state()}).status_code == 200
    assert client.post("/music/command", json={"device": "phone1234", "action": "pause"}).get_json() == {"queued": True, "needs_tap": False}


def test_a_pass_is_never_cached(client):
    login(client, ADMIN)
    assert "no-store" in client.get("/music/gate").headers["Cache-Control"]


def test_the_library_offline_pill_follows_the_gate(client):
    login(client, ADMIN)
    tile = lambda: client.get("/apps").data.decode().split('data-tel="tile:music"')[0].rsplit('<div class="unit', 1)[1]   # noqa: E731 (the unit's opening tag)
    assert 'data-s="on"' in tile()
    home = client.application.extensions["home"]
    home.accept({"capabilities": {}})
    assert 'data-s="off"' in tile()                                     # the library is offline: a dark control with a diamond lamp


def test_a_remote_play_says_whether_the_other_browser_needs_a_tap(client):
    login(client, ADMIN)
    client.post("/music/checkin", json={"device": "phone1234", "name": "iPhone", "state": state(armed=False, tap=True)})
    assert client.post("/music/command", json={"device": "phone1234", "action": "play"}).get_json() == {"queued": True, "needs_tap": True}
    client.post("/music/checkin", json={"device": "tesla1234", "name": "Tesla", "state": state(armed=True, tap=False)})
    assert client.post("/music/command", json={"device": "tesla1234", "action": "play"}).get_json() == {"queued": True, "needs_tap": False}
    r = client.post("/music/command", json={"device": "closed1234", "action": "play"})        # never checked in
    assert r.status_code == 409 and r.get_json() == {"queued": False, "needs_tap": False}
    assert client.post("/music/command", json={"device": "phone1234", "action": "halt"}).status_code == 409


def test_the_manifest_names_the_app_and_is_public_like_static_files(client):
    m = client.get("/music/manifest.webmanifest")                      # no sign-in: a browser fetches it without the cookie
    assert m.status_code == 200 and m.mimetype == "application/manifest+json"
    j = m.get_json()
    assert j["name"] == NAME and j["short_name"] == "Bilby"
    assert j["start_url"] == "/music" and j["scope"] == "/music" and j["id"] == "/music"
    assert j["display"] == "standalone" and j["background_color"] == "#000000"
    assert all(client.get(i["src"]).status_code == 200 for i in j["icons"])         # every icon it names exists


def test_the_page_is_named_everywhere_it_shows_a_name_and_never_the_old_one(client):
    login(client, ADMIN)
    page = client.get("/music").data.decode()
    assert f"<title>{NAME} — Maker Portal</title>" in page
    assert 'name="apple-mobile-web-app-title" content="Bilby"' in page
    assert 'aria-label="Back"' in page and 'aria-label="Home"' in page                      # the bridge's strip: back, the glyph, home
    sources = [page, client.get("/").data.decode(), client.get("/music/manifest.webmanifest").data.decode(),
               *(p.read_text(encoding="utf-8") for p in (STATIC / "bilby-app.js", STATIC / "bilby-player.js", STATIC / "bilby-player.css"))]
    for text in sources:
        assert "Music Service" not in text and "Skippy Music" not in text
    assert f'aria-label="{NAME}"' in page and NAME in (STATIC / "bilby-app.js").read_text(encoding="utf-8")


def test_the_page_loads_its_scripts_and_styles_and_they_exist(client):
    login(client, ADMIN)
    page = client.get("/music").data.decode()
    for asset in ("bilby-player.js", "bilby-player.css", "bilby-app.js"):
        assert f"/static/{asset}" in page
        assert client.get(f"/static/{asset}").status_code == 200
    assert page.index("/static/bilby-player.js") < page.index("/static/bilby-app.js")     # the logic first: the app needs window.BilbyCore


def test_the_page_has_every_element_the_script_looks_up():
    """A renamed id would only show as a broken player in a browser: check the script's lookups against the template."""
    html = (PORTAL / "templates" / "music.html").read_text(encoding="utf-8")
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    have = set(re.findall(r'\bid="([\w-]+)"', html))
    looked_up = set(re.findall(r'\$\("#([\w-]+)"', js)) | set(re.findall(r'getElementById\("([\w-]+)"', js))
    assert looked_up and not looked_up - have - {"notice-progress"}, looked_up - have        # the progress bar is made by the script
    for slot in set(re.findall(r'data-slot="(\w+)"', js)) | set(re.findall(r'\[data-slot="(\w+)"', js)):
        assert f'data-slot="{slot}"' in html, slot
    for url in ("data-gate-url", "data-checkin-url", "data-command-url"):
        assert url in html


def test_the_page_explains_the_one_tap_the_browser_wants():
    html = (PORTAL / "templates" / "music.html").read_text(encoding="utf-8")
    assert 'id="tapbar"' in html and "Tap to start the sound" in html
    assert re.search(r'id="tapbar"[^>]*\bhidden\b', html), "the bar starts hidden: it shows only when a play() was refused"
    assert "one tap again" in html and "Browsers only let a page make sound after you tap it" in html
    assert 'aria-live="polite"' in html and 'role="status"' in html                      # now playing is announced


def test_every_control_in_the_page_is_a_real_button_with_a_type_and_the_keyboard_help_lists_the_shortcuts():
    html = (PORTAL / "templates" / "music.html").read_text(encoding="utf-8")
    for tag in re.findall(r"<button\b[^>]*>", html):
        assert 'type="' in tag, tag
    for key in ("Space", "Shift", "<kbd>M</kbd>", "<kbd>S</kbd>", "<kbd>R</kbd>", "<kbd>/</kbd>", "<kbd>?</kbd>"):
        assert key in html


def test_the_player_uses_the_portals_colours_and_keeps_every_focus_ring():
    css = (STATIC / "bilby-player.css").read_text(encoding="utf-8")
    base = (PORTAL / "templates" / "base.html").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", code), "a colour literal: use the portal's variables"
    defined = set(re.findall(r"(--[\w-]+)\s*:", base)) | set(re.findall(r"(--[\w-]+)\s*:", code))
    defined |= set(re.findall(r'setProperty\("(--[\w-]+)"', (STATIC / "bilby-app.js").read_text(encoding="utf-8")))   # set by the script on an element
    used = set(re.findall(r"var\((--[\w-]+)", code))
    assert used and not used - defined, used - defined
    shared = (STATIC / "bridge.css").read_text(encoding="utf-8")           # the list, the top row and the page bar live in the bridge's own sheet
    assert "prefers-reduced-motion" in shared and "prefers-reduced-motion" in (PORTAL / "templates" / "base.html").read_text(encoding="utf-8")
    assert len(re.findall(r"outline\s*:\s*none", code + shared)) <= 4   # the form fields (focus border), the search box (draws its own focus border), the list panel and the page bar (which draws its own)


# ── the music page is a page of the bridge ───────────────────────────────────────────────────────────────────────────
def test_the_music_page_is_on_the_bridge_with_its_own_glyph(client):
    login(client, ADMIN)
    page = client.get("/music").data.decode()
    assert '<body class="bridge"' in page and 'href="/static/bridge.css"' in page and "bridge.js" in page
    assert '<use href="#g-wave"/>' in page                                                 # the strip's mark: the same glyph as its control on the launcher
    assert '<h1 class="apptitle"' not in page and 'id="home-btn"' not in page                               # none of the old pages' chrome
    glyphs = set(re.findall(r'<symbol id="g-([\w-]+)"', (PORTAL / "templates" / "_glyphs.html").read_text(encoding="utf-8")))
    assert {"wave", "note", "disc", "tag", "queue", "search", "shuffle", "repeat", "play", "pause", "prev", "next", "vol", "mute",
            "moon", "devices", "headset", "up", "down", "trash", "letters", "sort", "shield"} <= glyphs


def test_every_glyph_the_player_draws_is_in_the_portals_glyph_set():
    """The script names its glyphs ("play", "queue"...); a name nobody drew would show as an empty key on a phone."""
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    html = (PORTAL / "templates" / "music.html").read_text(encoding="utf-8")
    drawn = set(re.findall(r'<symbol id="g-([\w-]+)"', (PORTAL / "templates" / "_glyphs.html").read_text(encoding="utf-8")))
    alias = dict(re.findall(r'(\w+): "(\w+)"', re.search(r"const GLYPH = \{(.*?)\}", js, re.S).group(1)))
    named = set(re.findall(r'\bico\("(\w+)"\)', js)) | set(re.findall(r'\bicon: "(\w+)"', js)) | set(re.findall(r'\bglyph: "(\w+)"', js))
    named |= set(re.findall(r'\b(?:title|head)\("(\w+)"', js))
    named |= set(re.findall(r'\["[\w-]+", "(\w+)", (?:LABELS\.\w+|"[^"]* [^"]*"), ', js))               # the deck's controls
    named |= set(re.findall(r'data-i="(\w+)"', js)) | set(re.findall(r'data-i="(\w+)"', html))
    named |= set(re.findall(r'<use href="#g-([\w-]+)"', html))
    assert len(named) > 25, named
    missing = {alias.get(n, n) for n in named} - drawn
    assert not missing, missing


def test_the_music_page_never_scrolls_and_draws_no_gradients():
    css = (STATIC / "bilby-player.css").read_text(encoding="utf-8") + (STATIC / "bridge.css").read_text(encoding="utf-8")
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"overflow(?:-y)?\s*:\s*(?:auto|scroll)", code)                   # no scrollbar anywhere on the page
    assert not re.search(r"(?:linear|radial|conic)-gradient", code)                       # black is see-through on the Viture glasses
    assert "scrollTop" not in js and "VList" not in js                                    # a list is cut into screens, never scrolled
    assert "Bridge.PList" in js and "class PList" in (STATIC / "bridge.js").read_text(encoding="utf-8")        # the list is the bridge's own


def test_the_music_page_has_a_control_for_each_way_in_and_the_words_are_only_names(client):
    login(client, ADMIN)
    page = client.get("/music").data.decode()
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    for need in ('id="deck"', 'id="listwrap"', 'id="scrub"', 'id="letters-key"', 'id="sort"', 'id="signal"', 'id="q-cnt"'):
        assert need in page, need
    for view in ("artists", "albums", "tracks", "genres", "added", "played", "dj", "shuffle-all"):
        assert f'["{view}"' in js, view                                                    # a control on Home for each
    assert 'class="nav' not in page and "<nav" not in page                                 # the old column of text links is gone
    keybar = re.search(r'<div class="keybar".*?</div>', page, re.S).group(0)
    assert "<span" in keybar and not re.search(r">\s*[A-Za-z]{3,}\s*<", re.sub(r"<kbd>.*?</kbd>", "", keybar))   # glyph keys: no words on them


def test_the_signal_lamp_follows_the_line_and_the_library():
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    css = (STATIC / "bridge.css").read_text(encoding="utf-8")                              # a lamp's own states are the bridge's
    body = re.search(r"function syncSignal\(\) \{(.*?)\n  \}", js, re.S).group(1)
    assert '"warn"' in body and '"off"' in body and '"on"' in body and 'dataset.signal === "lost"' in body
    assert js.count("syncSignal()") >= 5                                                  # drawn whenever the line or a notice changes
    assert '.lamp[data-s="warn"]' in css and '.lamp[data-s="off"]' in css                 # by shape as well as colour


def test_the_player_never_tells_the_portal_what_is_playing_except_through_connect():
    """Telemetry sees a tile tap and nothing else. What a device plays goes to the portal only in Connect check-ins (the Captain's own
    devices, in memory), and the listening history stays in this browser."""
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    assert "sendBeacon" not in js and "/telemetry" not in js and "data-tel" not in js
    posts = re.findall(r"(?:fetch|post)\((cfg\.\w+|url)", js)
    assert set(posts) <= {"cfg.checkin", "cfg.command", "cfg.gate", "url"}, posts


# ── the speakers on skippy-pc: two browser windows, each pinned to one audio output ─────────────────────────────────────

def test_a_speaker_window_is_listed_even_while_idle_and_an_ordinary_device_is_not():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "room-downstairs", "Downstairs (HDMI)", None, room="downstairs")
    p.checkin(ADMIN, "idle-laptop1", "Laptop", None)                    # nothing played yet: nothing to offer
    [dev] = p.checkin(ADMIN, "phone1234", "iPhone", None)["devices"]
    assert dev["id"] == "room-downstairs" and dev["room"] == "downstairs" and dev["live"] and not dev["state"]["track"]


def test_only_a_known_room_counts_as_one():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "room-garage1", "Garage", None, room="garage")
    assert p.checkin(ADMIN, "phone1234", "iPhone", None)["devices"] == []
    assert set(player_mod.ROOMS) == {"downstairs", "upstairs"}
    for r in player_mod.ROOMS.values():
        re.compile(r["match"])                                          # the browser compiles it as a RegExp too


def test_a_phone_can_ask_a_speaker_to_pick_up_its_music_once():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "room-upstairs", "Upstairs (Bluetooth)", None, room="upstairs")
    assert p.command(ADMIN, "room-upstairs", "take:phone1234")
    assert not p.command(ADMIN, "room-upstairs", "take:bad id!") and not p.command(ADMIN, "room-upstairs", "take:")
    assert not p.command(ADMIN, "room-upstairs", "take:phone1234; rm")
    assert p.checkin(ADMIN, "room-upstairs", "Upstairs (Bluetooth)", None, room="upstairs")["commands"] == ["take:phone1234"]
    assert p.checkin(ADMIN, "room-upstairs", "Upstairs (Bluetooth)", None, room="upstairs")["commands"] == []


def test_the_checkin_route_carries_the_room_and_the_page_hands_the_rooms_to_the_script(client):
    login(client, ADMIN)
    assert client.post("/music/checkin", json={"device": "room-downstairs", "name": "x", "room": "downstairs"}).status_code == 200
    reply = client.post("/music/checkin", json={"device": "phone1234", "name": "iPhone"}).get_json()
    assert [d["id"] for d in reply["devices"]] == ["room-downstairs"]
    assert client.post("/music/command", json={"device": "room-downstairs", "action": "take:phone1234"}).get_json()["queued"] is True
    page = client.get("/music").data.decode()
    m = re.search(r"data-rooms='([^']*)'", page)
    assert m and set(json.loads(m.group(1).replace("&#34;", '"'))) == {"downstairs", "upstairs"}


def test_the_script_pins_a_room_to_its_output_and_sends_music_there():
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    assert 'new URLSearchParams(location.search).get("room")' in js and '"room-" + room.key' in js
    assert "setSinkId" in js and "audiooutput" in js and "devicechange" in js
    assert '"take:" + srcId' in js and "/^take:/" in js
    assert re.search(r'"Both"', js) and "Play on" in js
