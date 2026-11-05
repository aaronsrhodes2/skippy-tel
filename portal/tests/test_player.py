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
    assert '<body class="bridge"' in page and 'href="/static/bridge.css' in page and "bridge.js" in page
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
    code = re.sub(r"\.chat-bridge [^{}]*\{[^}]*\}", "", code)                              # (Skippy's chat, not on this page: its transcript scrolls)
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
    assert "sendBeacon" not in js and "/telemetry" not in js
    # its keys carry fixed telemetry ids (music:seek, music:remote-<glyph>: which control, never which song; 2026-11-02, every control
    # counted so the nightly tidy can retire the unused ones)
    tels = re.findall(r'(?:data-tel="|dataset\.tel = ")([^"]*)', js)
    assert tels and all(t.startswith("music:") and "${" not in t for t in tels), tels
    posts = re.findall(r"(?:fetch|post)\((cfg\.\w+|url)", js)
    # cfg.fav: the Captain's own ★ tap sends the one song he chose to his favorites list (never automatic; the key shows for him alone)
    assert set(posts) <= {"cfg.checkin", "cfg.command", "cfg.gate", "cfg.fav", "url"}, posts


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


# ── the room boxes: skippy-pc's hub checks each in as a speaker (services/satellite/music.py) ──────────────────────────────────────────

def test_a_room_box_is_listed_like_a_speaker_but_a_browser_cannot_claim_to_be_one():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "skippyroom-kitchen", "Kitchen", None, room="box-kitchen", box=True)
    p.checkin(ADMIN, "sneaky1234", "Laptop", None, room="box-den")               # a page saying "box-" is not a box
    [dev] = p.checkin(ADMIN, "phone1234", "iPhone", None)["devices"]
    assert dev["id"] == "skippyroom-kitchen" and dev["room"] == "box-kitchen" and dev["live"]
    p.checkin(ADMIN, "skippyroom-x", "X", None, room="box-not a room!", box=True)
    assert not any(d["id"] == "skippyroom-x" for d in p.checkin(ADMIN, "phone1234", "iPhone", None)["devices"])


def test_the_hub_checks_a_box_in_with_the_home_token_and_gets_a_phones_take(client):
    body = {"device": "skippyroom-kitchen", "name": "Kitchen (Skippy)", "room": "box-kitchen", "state": {"track": "", "playing": False}}
    assert client.post("/home/music/checkin", json=body).status_code == 401
    assert client.post("/home/music/checkin", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    auth = {"Authorization": f"Bearer {TOKEN}"}
    for bad in ({**body, "device": "phone1234"}, {**body, "room": "box-den"}, {**body, "room": "downstairs"}, {**body, "device": "skippyroom-"}):
        assert client.post("/home/music/checkin", json=bad, headers=auth).status_code == 400
    assert client.post("/home/music/checkin", json=body, headers=auth).get_json()["commands"] == []
    login(client, ADMIN)                                                   # the Captain's phone sees it under "Play on" and sends the music there
    reply = client.post("/music/checkin", json={"device": "phone1234", "name": "iPhone"}).get_json()
    assert [(d["id"], d["room"]) for d in reply["devices"]] == [("skippyroom-kitchen", "box-kitchen")]
    assert client.post("/music/command", json={"device": "skippyroom-kitchen", "action": "take:phone1234"}).get_json()["queued"] is True
    reply = client.post("/home/music/checkin", json=body, headers=auth).get_json()
    assert reply["commands"] == ["take:phone1234"]


def test_with_more_than_two_speakers_the_play_on_menu_offers_all_of_them():
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    assert 'rooms.length === 2 ? "Both" : "All"' in js


def test_favorites_are_marked_from_a_pod_or_purple_and_from_the_player_and_played_back(client):
    """The Captain, 2026-10-31: "Skippy, add this song to my favorites", from a pod, purple Skippy or the player; the hub then plays them back."""
    auth = {"Authorization": f"Bearer {TOKEN}"}
    box = {"device": "skippyroom-office", "name": "Office (Skippy)", "room": "box-office",
           "state": {"track": "Moby/Play/03 Porcelain.mp3", "title": "Porcelain", "artist": "Moby", "playing": True}}
    assert client.post("/home/music/favorite", json={"device": "skippyroom-office"}).status_code == 401
    assert client.post("/home/music/favorite", json={}, headers=auth).status_code == 409          # nothing playing yet
    client.post("/home/music/checkin", json=box, headers=auth)
    got = client.post("/home/music/favorite", json={"device": "skippyroom-office", "source": "pod"}, headers=auth).get_json()
    assert got["favorite"]["title"] == "Porcelain" and got["favorite"]["source"] == "pod"
    assert client.get("/home/music/favorites", headers=auth).get_json()["favorites"][0]["track"] == "Moby/Play/03 Porcelain.mp3"
    login(client, ADMIN)
    assert client.get("/music/favorites").get_json()["favorites"][0]["title"] == "Porcelain"
    m3u = client.get("/music/favorites.m3u")
    assert m3u.status_code == 200 and b"Moby/Play/03 Porcelain.mp3" in m3u.data


def test_a_song_he_is_tired_of_leaves_the_pool_and_every_mix_until_he_favorites_it_again(client):
    """The Captain, 2026-11-01: one favorites pool, pruned while it plays ("Skippy, I'm tired of this song")."""
    auth = {"Authorization": f"Bearer {TOKEN}"}
    box = {"device": "skippyroom-office", "name": "Office (Skippy)", "room": "box-office",
           "state": {"track": "Moby/Play/03 Porcelain.mp3", "title": "Porcelain", "artist": "Moby", "playing": True}}
    assert client.post("/home/music/tired", json={}).status_code == 401
    assert client.post("/home/music/tired", json={}, headers=auth).status_code == 409             # nothing playing yet
    client.post("/home/music/checkin", json=box, headers=auth)
    client.post("/home/music/favorite", json={"device": "skippyroom-office"}, headers=auth)
    got = client.post("/home/music/tired", json={"device": "skippyroom-office"}, headers=auth).get_json()["tired"]
    assert got["title"] == "Porcelain" and got["was_favorite"] is True
    listed = client.get("/home/music/favorites", headers=auth).get_json()
    assert listed["favorites"] == [] and listed["tired"] == ["Moby/Play/03 Porcelain.mp3"]
    client.post("/home/music/favorite", json={"device": "skippyroom-office"}, headers=auth)        # a favorite again: off the tired list
    assert client.get("/home/music/favorites", headers=auth).get_json()["tired"] == []


def test_every_song_he_hears_goes_on_the_played_list_that_discovery_moves_past(client):
    """The Captain, 2026-11-01: discovery plays his unplayed songs. A pod, the stereo, his browser or the car past 30 s into a song marks it."""
    auth = {"Authorization": f"Bearer {TOKEN}"}
    box = {"device": "skippyroom-office", "name": "Office (Skippy)", "room": "box-office",
           "state": {"track": "Moby/Play/03 Porcelain.mp3", "title": "Porcelain", "artist": "Moby", "playing": True, "pos": 5}}
    client.post("/home/music/checkin", json=box, headers=auth)
    assert client.get("/home/music/favorites", headers=auth).get_json()["played"] == []           # 5 s in: not yet
    box["state"]["pos"] = 40
    client.post("/home/music/checkin", json=box, headers=auth)
    client.post("/home/music/checkin", json=box, headers=auth)                                     # once per song, not per check-in
    login(client, ADMIN)
    assert client.post("/music/played", json={"track": "Moby/Play/04 Southside.mp3"}).get_json() == {"ok": True}   # the car's page
    got = client.get("/music/favorites").get_json()
    assert sorted(got["played"]) == ["Moby/Play/03 Porcelain.mp3", "Moby/Play/04 Southside.mp3"] and got["tired"] == []


def test_the_favorite_key_is_the_captains_alone(client):
    """The ★ key in the player marks the song playing in that browser (portal/favorites.py); a friend with the audio grant never sees it."""
    login(client, ADMIN)
    page = client.get("/music").get_data(as_text=True)
    assert 'data-fav-url="/music/favorites"' in page and "Add this song to your favorites" in page
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    assert 'data-act="fav" data-fav-only hidden' in js and 'case "fav": favorite(); break;' in js and "b.hidden = !cfg.fav" in js
    got = client.post("/music/favorites", json={"track": "Moby/Play/03 Porcelain.mp3", "title": "Porcelain", "artist": "Moby"}).get_json()
    assert got["favorite"]["title"] == "Porcelain"


def test_the_stereos_spotify_names_and_mix_are_kept_and_the_mix_command_is_allowed():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    uri = "spotify:track:" + "a" * 22
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", state(queue=["A/B/one.mp3", uri], track=uri, idx=1,
              titles={uri: ["Around the World", "Daft Punk"], 7: ["x"], "bad": "nope"}, mix={"on": True, "xfade": "6"}), room="box-stereo", box=True)
    body = state(queue=None, track=uri, idx=1, mix={"on": False, "xfade": 99})
    del body["queue"]
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", body, room="box-stereo", box=True)       # no queue, no titles: the held ones stay
    [dev] = p.checkin(ADMIN, "other1234", "PC", None)["devices"]
    assert dev["state"]["titles"] == {uri: ["Around the World", "Daft Punk"]}
    assert dev["state"]["mix"] == {"on": False, "xfade": 30.0}
    assert p.command(ADMIN, "skippyroom-stereo", "mix:on") and p.command(ADMIN, "skippyroom-stereo", "mix:off")
    assert not p.command(ADMIN, "skippyroom-stereo", "mix:loud")
    p.checkin(ADMIN, "phone1234", "iPhone", state(mix="no"))
    assert next(d for d in p.checkin(ADMIN, "other1234", "PC", None)["devices"] if d["id"] == "phone1234")["state"]["mix"] is None


# ── the house stereo's speakers (services/satellite/radio.py): `kind`, `outs` and `held` ride along in its check-in, and the page flips a speaker with out:<id>:on|off ──

def stereo_state(**kw):
    """What the radio sends: an audiobook as the current item of the one stream, three speakers, nothing held."""
    return {"track": "", "title": "Dune", "artist": "Frank Herbert", "pos": 0, "playing": True, "kind": "book", "idx": 1,
            "queue": ["A/B/one.mp3", "book:dune-m4b"], "titles": {"book:dune-m4b": ["Dune", "Frank Herbert"]},
            "book": {"id": "dune-m4b", "title": "Dune", "pos": 100.0, "chapter": 2, "playing": True, "speed": 1.0},
            "outs": [{"id": "enclave", "name": "Enclave", "on": True, "open": True, "floor": "upstairs"},
                     {"id": "hdmi", "name": "Sony", "on": False, "open": True, "floor": "downstairs"},
                     {"id": "pod:living", "name": "Living room box", "on": True, "open": False, "floor": ""}],
            "held": None, **kw}


def stereo_seen_by_a_phone(p, **kw):
    """The hub checks the House stereo in with this state; what another player (the Captain's phone) is then shown for it."""
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", stereo_state(**kw), room="box-stereo", box=True)
    return next(d for d in p.checkin(ADMIN, "phone1234", "iPhone", None)["devices"] if d["id"] == "skippyroom-stereo")["state"]


def test_the_stereos_kind_speakers_and_hold_are_kept_typed_and_short():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    got = stereo_seen_by_a_phone(p, held={"why": "no speaker is on", "since": 1_800_000_000, "evil": "<script>"})
    assert got["kind"] == "book" and got["track"] == "" and got["playing"] is True
    assert got["outs"] == [{"id": "enclave", "name": "Enclave", "on": True, "open": True, "floor": "upstairs"},
                           {"id": "hdmi", "name": "Sony", "on": False, "open": True, "floor": "downstairs"},
                           {"id": "pod:living", "name": "Living room box", "on": True, "open": False, "floor": ""}]
    assert got["held"] == {"why": "no speaker is on", "since": 1_800_000_000.0}                  # (nothing but why and since)
    assert got["queue"] == ["A/B/one.mp3", "book:dune-m4b"] and got["titles"] == {"book:dune-m4b": ["Dune", "Frank Herbert"]}   # a book is an item of the queue
    assert got["book"] == {"id": "dune-m4b", "title": "Dune", "pos": 100.0, "chapter": 2, "playing": True, "speed": 1.0}
    assert [stereo_seen_by_a_phone(p, kind=k)["kind"] for k in ("song", "spotify", "book")] == ["song", "spotify", "book"]
    assert [stereo_seen_by_a_phone(p, kind=k)["kind"] for k in ("podcast", "", None, 7, True, ["book"], {"a": 1})] == [""] * 7      # a word it does not know is dropped


def test_a_garbled_speaker_list_is_cleaned_not_trusted():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    garbled = [{"id": "enclave", "name": "E" * 100, "on": "yes", "open": 1, "floor": "attic"},         # not real booleans, not a floor, a name too long
               {"id": "enclave", "name": "again", "on": True},                                         # a repeat of an id
               {"id": "Bad Id"}, {"id": "a:b:c"}, {"id": "x" * 25}, {"id": ""}, {"id": 7}, {"id": "pod:"}, {"id": "../etc"}, {"id": "enclave\n"}, "enclave",
               {"id": "pod:living-room", "on": True, "open": True, "floor": "upstairs"}]                 # no name: the last part of its id
    assert len(garbled) == player_mod.MAX_OUTS
    later = [{"id": ":x"}, {"id": "pod:" + "y" * 25}, None, 5, ["enclave"], {"name": "no id"}, {"id": "hdmi", "name": "past the twelfth"}]      # (only the first twelve are looked at)
    assert stereo_seen_by_a_phone(p, outs=garbled + later)["outs"] == [
        {"id": "enclave", "name": "E" * 40, "on": False, "open": False, "floor": ""},
        {"id": "pod:living-room", "name": "living-room", "on": True, "open": True, "floor": "upstairs"}]
    many = [{"id": f"pod:r{i}", "name": f"R{i}", "on": True, "open": True} for i in range(30)]
    assert [o["id"] for o in stereo_seen_by_a_phone(p, outs=many)["outs"]] == [f"pod:r{i}" for i in range(player_mod.MAX_OUTS)] and player_mod.MAX_OUTS == 12
    for not_a_list in ("enclave", {"id": "enclave"}, 5, None, True):
        assert stereo_seen_by_a_phone(p, outs=not_a_list)["outs"] == []


def test_a_hold_is_a_short_sentence_and_a_time_and_nothing_else():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    for not_held in (None, False, True, "no speaker", 5, [], ["x"], 0):
        assert stereo_seen_by_a_phone(p, held=not_held)["held"] is None, not_held
    assert stereo_seen_by_a_phone(p, held={"why": "w" * 200, "since": 1_800_000_000.25})["held"] == {"why": "w" * 80, "since": 1_800_000_000.2}
    for since in ("soon", None, True, -5, float("nan"), float("inf"), 5e9, [1]):                     # not an epoch time: 0, and the hold itself still stands
        assert stereo_seen_by_a_phone(p, held={"why": "x", "since": since})["held"] == {"why": "x", "since": 0.0}, since
    assert stereo_seen_by_a_phone(p, held={"why": 7, "since": 5})["held"] == {"why": "", "since": 5.0}
    assert stereo_seen_by_a_phone(p, held={})["held"] == {"why": "", "since": 0.0}                   # a held stream with no details is still held


def test_a_hold_ends_with_the_check_in_that_no_longer_says_it():
    """The stream resumes by itself when a speaker comes back: the page must not keep showing a wait the radio has stopped reporting."""
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    assert stereo_seen_by_a_phone(p, held={"why": "no speaker is on", "since": 5})["held"]["why"] == "no speaker is on"
    assert stereo_seen_by_a_phone(p, held=None)["held"] is None
    held_again = stereo_state(held={"why": "no speaker is on", "since": 5})
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", held_again, room="box-stereo", box=True)
    del held_again["held"], held_again["outs"]                                                          # a lighter check-in, or an older radio: nothing is carried over
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", held_again, room="box-stereo", box=True)
    [dev] = [d for d in p.checkin(ADMIN, "phone1234", "iPhone", None)["devices"] if d["id"] == "skippyroom-stereo"]
    assert dev["state"]["held"] is None and dev["state"]["outs"] == []


def test_an_older_radio_that_sends_none_of_it_reads_as_no_speakers_and_not_held():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", state(), room="box-stereo", box=True)
    [dev] = p.checkin(ADMIN, "phone1234", "iPhone", None)["devices"]
    got = dev["state"]
    assert (got["kind"], got["outs"], got["held"]) == ("", [], None)
    assert got["track"] == "A/B/one.mp3" and got["playing"] is True and got["queue"] == ["A/B/one.mp3", "A/B/two.mp3"] and got["idx"] == 0      # everything else as it was
    assert set(got) == {"book", "titles", "mix", "track", "title", "artist", "pos", "playing", "queue", "idx", "dj", "shuffle", "repeat", "armed", "tap",
                        "kind", "outs", "held"}                                                       # the whitelist, whole: a new field is a decision
    assert set(player_mod._state({})) == set(got)
    assert (player_mod._state({})["kind"], player_mod._state({})["outs"], player_mod._state({})["held"]) == ("", [], None)


@pytest.mark.parametrize("action", ["out:enclave:on", "out:enclave:off", "out:hdmi:on", "out:pod:living:off", "out:pod:living-room:on", "out:pod:on", "out:a_b-c9:off",
                                    "out:" + "a" * 24 + ":" + "b" * 24 + ":off"])
def test_a_well_formed_speaker_switch_is_queued_for_the_stereo(action):
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", stereo_state(), room="box-stereo", box=True)
    assert p.command(ADMIN, "skippyroom-stereo", action)
    assert p.checkin(ADMIN, "skippyroom-stereo", "House stereo", None, room="box-stereo", box=True)["commands"] == [action]       # handed over once
    assert p.checkin(ADMIN, "skippyroom-stereo", "House stereo", None, room="box-stereo", box=True)["commands"] == []


@pytest.mark.parametrize("action", ["out:enclave", "out:enclave:", "out:enclave:toggle", "out:enclave:ON", "out::on", "out:ENCLAVE:on", "out:pod:a:b:on", "out:x y:on",
                                    "out:" + "a" * 25 + ":on", "out:pod:" + "b" * 25 + ":on", "out:enclave:on ", "out:enclave:on\n", "out:../etc:on", "OUT:enclave:on",
                                    "out:pod:living:off;rm", "out:pod:living:off:on:on", "out:pod/living:on", "out:é:on", "out:enclave:on:off:on", "out", "out:"])
def test_only_a_well_formed_speaker_switch_is_queued(action):
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", stereo_state(), room="box-stereo", box=True)
    assert not p.command(ADMIN, "skippyroom-stereo", action)
    assert p.checkin(ADMIN, "skippyroom-stereo", "House stereo", None, room="box-stereo", box=True)["commands"] == []


def test_the_speaker_switch_joins_the_old_commands_without_changing_them():
    clock = Clock()
    p = player_mod.Player(paired_home(clock), clock=clock)
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", stereo_state(), room="box-stereo", box=True)
    for action in ("pause", "mix:on", "take:phone1234", "book:pause", "out:hdmi:on", "next"):
        assert p.command(ADMIN, "skippyroom-stereo", action), action
    assert p.checkin(ADMIN, "skippyroom-stereo", "House stereo", None, room="box-stereo", box=True)["commands"] == ["mix:on", "take:phone1234", "book:pause", "out:hdmi:on", "next"]   # (the last five are kept: as ever)
    for old_bad in ("halt", "mix:loud", "take:", "book:play:../x", "play now"):
        assert not p.command(ADMIN, "skippyroom-stereo", old_bad), old_bad
    assert not p.command(FRIEND, "skippyroom-stereo", "out:hdmi:on")                              # someone else's stereo is not theirs to switch
    clock.now += player_mod.LIVE_S + 1
    assert not p.command(ADMIN, "skippyroom-stereo", "out:hdmi:on")                               # and a stereo that stopped checking in cannot be told anything


def test_the_hub_reports_the_stereos_speakers_and_the_page_flips_one(client):
    auth = {"Authorization": f"Bearer {TOKEN}"}
    body = {"device": "skippyroom-stereo", "name": "House stereo", "room": "box-stereo",
            "state": stereo_state(held={"why": "no speaker is on", "since": 1_800_000_000, "evil": "<script>"})}
    assert client.post("/home/music/checkin", json=body, headers=auth).get_json()["commands"] == []
    login(client, ADMIN)
    reply = client.post("/music/checkin", json={"device": "phone1234", "name": "iPhone"}).get_json()
    [stereo] = [d for d in reply["devices"] if d["id"] == "skippyroom-stereo"]
    assert stereo["room"] == "box-stereo" and stereo["state"]["kind"] == "book"
    assert [(o["id"], o["on"], o["open"]) for o in stereo["state"]["outs"]] == [("enclave", True, True), ("hdmi", False, True), ("pod:living", True, False)]
    assert stereo["state"]["held"] == {"why": "no speaker is on", "since": 1_800_000_000.0}
    ok = client.post("/music/command", json={"device": "skippyroom-stereo", "action": "out:pod:living:off"})
    assert ok.status_code == 200 and ok.get_json() == {"queued": True, "needs_tap": False}
    for bad in ("out:pod:living:maybe", "out:BAD:on", "out:", "out:enclave:on;rm"):
        assert client.post("/music/command", json={"device": "skippyroom-stereo", "action": bad}).status_code == 409, bad
    assert client.post("/home/music/checkin", json=body, headers=auth).get_json()["commands"] == ["out:pod:living:off"]      # the radio gets exactly that one
    assert client.post("/home/music/checkin", json=body, headers=auth).get_json()["commands"] == []


def test_a_book_the_stereo_plays_is_not_bilbys_last_song_and_nothing_to_favorite_and_still_a_room_for_the_books_app():
    """The new fields must not turn an audiobook into a song: drive.bilby_last and favorites.now_playing read `track`, which is empty for a book."""
    import audiobooks as books_mod
    import drive
    import favorites as favorites_mod
    clock = Clock()
    home = paired_home(clock)
    p = player_mod.Player(home, clock=clock)
    p.checkin(ADMIN, "skippyroom-stereo", "House stereo", stereo_state(), room="box-stereo", box=True)
    listing = p.listing(ADMIN)
    assert listing[0]["state"]["track"] == "" and listing[0]["state"]["kind"] == "book"
    assert drive.bilby_last(listing, {}, clock.now) is None                                          # a book, not Bilby (test_drive says the same of a pod's)
    assert favorites_mod.now_playing(listing) is None
    p.checkin(ADMIN, "skippyroom-office", "Office (Skippy)", {"track": "M/P/one.mp3", "title": "One", "artist": "M", "pos": 5, "playing": True,
                                                                "queue": ["M/P/one.mp3"], "kind": "song", "outs": [], "held": None}, room="box-office", box=True)
    last = drive.bilby_last(p.listing(ADMIN), {}, clock.now)
    assert last["track"] == "M/P/one.mp3" and last["where"] == "Office (Skippy)"                       # the song, not the book that is newer or older
    rooms = books_mod.Books(home, p, clock=clock).rooms(ADMIN)
    stereo = next(r for r in rooms if r["id"] == "skippyroom-stereo")
    assert set(stereo) == {"id", "name", "live", "age", "book"}                                       # the speakers and the hold are not the Books app's business
    assert stereo["book"] == {"id": "dune-m4b", "title": "Dune", "pos": 100.0, "chapter": 2, "playing": True, "speed": 1.0}
    assert not books_mod.Books(home, p, clock=clock).command(ADMIN, "skippyroom-stereo", "out:hdmi:on")      # that route passes only `book:` commands


def test_the_speaker_toggles_are_hexagons_with_a_telemetry_name_the_nightly_tidy_never_retires():
    import adaptive
    core = (STATIC / "bilby-player.js").read_text(encoding="utf-8")
    app = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    shared = (STATIC / "bridge.css").read_text(encoding="utf-8")
    tel = re.search(r'TOGGLE_TEL = "([^"]+)"', core).group(1)
    assert tel.startswith("music:") and not adaptive.retirable_control(tel)                            # a floor: without these keys a held stream cannot be brought back
    row = re.sub(r"//[^\n]*", "", re.search(r"makers\.souts = \{.*?\n  \};", app, re.S).group(0))      # (the code, not its comments)
    assert 'el("button", "key")' in row and 'setAttribute("aria-pressed"' in row and "b.dataset.tel = t.tel" in row and "dataset.out = t.id" in row
    assert "lamp" not in row and 'b.dataset.s = "boot"' in row                                       # no lamp in a toggle: a speaker that waits breathes, as bridge.css gives a toggle
    assert '.key[aria-pressed][data-s="boot"] .g' in shared and ".key[aria-pressed] .lamp" in shared and "clip-path: polygon(25% 0, 75% 0, 100% 50%" in shared
    assert 'case "souts":' in app and "toggleOut(row.dev, b.dataset.out)" in app and "C.outCommand(id, !o.on)" in app


def test_every_glyph_the_stereo_pane_draws_is_in_the_glyph_set():
    drawn = set(re.findall(r'<symbol id="g-([\w-]+)"', (PORTAL / "templates" / "_glyphs.html").read_text(encoding="utf-8")))
    core = (STATIC / "bilby-player.js").read_text(encoding="utf-8")
    app = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    speakers = set(re.findall(r'[?:] "(\w+)"', re.search(r"const outGlyph = .*?;\n", core).group(0)))     # a speaker, a screen, a pod
    assert speakers == {"vol", "pip", "can"}
    pane = speakers | {"mute", "book", "note", "arcs", "mix", "play", "pause", "prev", "next"}            # the held line, a book's glyph, the rows and keys
    assert 'ico("mute")' in app and 'ico("book")' in app
    assert pane <= drawn, pane - drawn


def test_the_speaker_toggles_are_lined_up_by_the_widths_the_stylesheet_draws():
    """A list row of the bridge has a fixed height and hides what does not fit, so the script cuts the toggles into lines by the widths it believes
    (BilbyCore SO_W, GAP_W): they must be the stylesheet's."""
    css = (STATIC / "bilby-player.css").read_text(encoding="utf-8")
    core = (STATIC / "bilby-player.js").read_text(encoding="utf-8")
    so_w, gap_w = (int(n) for n in re.search(r"const SO_W = (\d+), GAP_W = (\d+);", core).groups())
    width = int(re.search(r"\.souts \.so \{[^}]*?\bwidth: (\d+)px", css).group(1))
    between = int(re.search(r"\.vrow\.souts \{[^}]*?\bgap: 0 (\d+)px", css).group(1))
    assert so_w == width + between, (so_w, width, between)                                           # a toggle and the gap to the next
    side = re.search(r"\.souts \.gap \{[^}]*?margin: \d+px (\d+)px \d+px; border-left: (\d+)px", css)
    assert gap_w == 2 * int(side.group(1)) + int(side.group(2)) + 2 * between, (gap_w, side.groups(), between)    # a floor's line, its margins and the gaps either side
    app = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    assert 'rows.push({ type: "souts", h: 76, dev: st, line })' in app and "C.outsLines(C.outToggles(outs)" in app          # one row of the list per line


def test_the_toggle_commands_the_page_builds_are_the_ones_the_portal_accepts():
    """bilby-player.js (OUT_ID, outCommand) and player.py (OUT_ID, OUT) are written twice: the same ids pass both."""
    core = (STATIC / "bilby-player.js").read_text(encoding="utf-8")
    js_id = re.search(r"const OUT_ID = /\^(.*?)\$/;", core).group(1)
    assert js_id == player_mod.OUT_ID.pattern, (js_id, player_mod.OUT_ID.pattern)
    assert re.search(r"out:\$\{id\}:\$\{on \? \"on\" : \"off\"\}", core)

