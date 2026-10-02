"""Bilby's Audio Service (the browser player at /music): the portal signs passes the home music-gate accepts (same key from the
home token, no new secret), only for a gate address on the tailnet, and keeps each device's place so another can pick up or act as
its remote, and knows when a device's browser still needs a tap before it will make sound. Admin only.

The player itself (static/bilby-app.js) runs in a browser: what can be checked without one is checked here (the routes, the page's
markup against the ids the script looks up, the stylesheet's use of the portal's tokens); its pure logic is run under Node by
test_player_js.py when Node is installed."""
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
    launcher = client.get("/").data.decode()
    assert 'data-tel="tile:music"' in launcher and f"<h3>{NAME}</h3>" in launcher
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
    tile = lambda: client.get("/").data.decode().split('data-tel="tile:music"')[1].split("</a>")[0]   # noqa: E731 (the video tile has its own pill)
    assert "library offline" not in tile()
    home = client.application.extensions["home"]
    home.accept({"capabilities": {}})
    assert "library offline" in tile()


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
    assert re.search(r'<h1 class="apptitle">Bilby(&#39;|&apos;|\')s Audio Service</h1>', page)
    assert 'name="apple-mobile-web-app-title" content="Bilby"' in page and "&lsaquo; Home" in page
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
    assert "prefers-reduced-motion" in css
    assert len(re.findall(r"outline\s*:\s*none", code)) <= 1        # only the search box (which draws its own focus border)


def test_the_player_never_tells_the_portal_what_is_playing_except_through_connect():
    """Telemetry sees a tile tap and nothing else. What a device plays goes to the portal only in Connect check-ins (the Captain's own
    devices, in memory), and the listening history stays in this browser."""
    js = (STATIC / "bilby-app.js").read_text(encoding="utf-8")
    assert "sendBeacon" not in js and "/telemetry" not in js and "data-tel" not in js
    posts = re.findall(r"(?:fetch|post)\((cfg\.\w+|url)", js)
    assert set(posts) <= {"cfg.checkin", "cfg.command", "cfg.gate", "url"}, posts
