"""Nagatha's Video Service (the browser video app at /video): the portal signs passes the home video-gate accepts (same home
token, its own label, so a music pass never opens it), only for a gate address on the tailnet's Funnel, and serves an admin-only
page whose script reads everything else from the gate itself.

The player runs in a browser: what can be checked without one is checked here (the routes, the tile, the page's markup against the
ids the script looks up, the stylesheet's use of the portal's tokens, the self-hosted hls.js); its pure logic runs under Node in
test_video_js.py. The gate's own code is run in a SEPARATE Python process: services/video-gate has a catalog.py too, and the
portal's own catalog.py is already imported here."""
import hashlib
import json
import re
import subprocess
import sys
import base64
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import player as player_mod  # noqa: E402
import video as video_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

TOKEN = "the-home-token"
GATE = "https://skippy-pc.tailab2cc7.ts.net:8443"
PORTAL = Path(__file__).resolve().parents[1]
STATIC = PORTAL / "static"
VIDEO_GATE = PORTAL.parent / "services" / "video-gate"
NAME = "Nagatha's Video Service"
HLS = STATIC / "vendor" / "hls-1.6.15.min.js"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def paired_home(clock, caps=None):
    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    home = home_mod.Home(vm, clock=clock)
    home.accept({"capabilities": caps if caps is not None else {"video_gate": {"ready": True, "url": GATE}}})
    return home


def gate_says(token: str, passes: list[str], now: float, label: str = "video") -> list:
    """Ask services/video-gate/gate.py itself (in its own process) whether it takes each pass: its scope ("rw"/"ro") or False.
    With label="music", the MUSIC gate (True/False)."""
    where = VIDEO_GATE if label == "video" else PORTAL.parent / "services" / "music-gate"
    code = ("import json, sys; sys.path.insert(0, '.'); import gate; a = json.loads(sys.argv[1]); "
            "print(json.dumps([gate.valid(gate.gate_key(a['token']), p, a['now']) or False for p in a['passes']]))")
    run = subprocess.run([sys.executable, "-c", code, json.dumps({"token": token, "passes": passes, "now": now})], cwd=str(where),
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout)


# ── the pass ──────────────────────────────────────────────────────────────────
def test_the_pass_opens_the_video_gate_and_only_the_video_gate():
    pytest.importorskip("requests")                                   # the gate's own imports
    clock = Clock()
    g = video_mod.Video(paired_home(clock), clock=clock).gate()
    assert g["ok"] and g["url"] == GATE
    assert video_mod.PASS_S <= g["exp"] - clock.now <= video_mod.PASS_S + video_mod.PASS_BUCKET_S
    later = g["exp"] + 1
    assert gate_says(TOKEN, [g["pass"]], clock.now) == ["rw"]                      # the gate's own check, its own code
    friend = video_mod.Video(paired_home(clock), clock=clock).gate(read_only=True)["pass"]
    assert gate_says(TOKEN, [friend], clock.now) == ["ro"]                         # a friend's pass: watch, never write
    assert gate_says("another-token", [g["pass"]], clock.now) == [False]
    assert gate_says(TOKEN, [g["pass"]], later) == [False]                         # and it runs out
    assert gate_says(TOKEN, [g["pass"]], clock.now, label="music") == [False]      # a video pass never opens the music gate
    music = player_mod.sign(player_mod.gate_key(hashlib.sha256(TOKEN.encode()).hexdigest()), g["exp"])
    assert gate_says(TOKEN, [music], clock.now) == [False]                         # ... nor a music pass the video gate


def test_the_signing_matches_the_gates_published_test_vector():
    """services/video-gate/README.md: token 'test-home-token', exp 1800003600 → this pass. The portal starts from the sha256."""
    key = video_mod.gate_key(hashlib.sha256(b"test-home-token").hexdigest())
    assert video_mod.sign(key, 1800003600) == "1800003600.d8383a1cefb72b136b01fb22ff9e87fb"


def test_a_pass_is_never_shorter_than_a_film_night_and_never_longer_than_the_gate_takes():
    clock = Clock()
    home = paired_home(clock)
    v = video_mod.Video(home, clock=clock)
    base = clock.now
    for step in range(0, 3 * video_mod.PASS_BUCKET_S, 997):
        clock.now = base + step
        home.accept({"capabilities": {"video_gate": {"ready": True, "url": GATE}}})
        g = v.gate()
        assert video_mod.PASS_S <= g["exp"] - clock.now <= 2 * 24 * 3600, step      # the gate's MAX_PASS_S is two days


@pytest.mark.parametrize("url", [
    "https://skippy-pc.tailab2cc7.ts.net:8443", "https://skippy-pc.tailab2cc7.ts.net:8443/", "https://skippy-pc.tailab2cc7.ts.net",
])
def test_a_funnel_address_is_signed_for(url):
    clock = Clock()
    g = video_mod.Video(paired_home(clock, {"video_gate": {"ready": True, "url": url}}), clock=clock).gate()
    assert g["ok"] and g["url"] == url.rstrip("/")


@pytest.mark.parametrize("caps", [
    {"video_gate": {"ready": False, "url": GATE}},                                        # the gate (or Jellyfin) isn't ready
    {"video_gate": {"ready": True, "url": "https://evil.example.com"}},                   # never off the tailnet
    {"video_gate": {"ready": True, "url": "http://skippy-pc.tailab2cc7.ts.net:8443"}},    # never plain http
    {"video_gate": {"ready": True, "url": "https://skippy-pc.tailab2cc7.ts.net:8080"}},   # a port Funnel doesn't use
    {"video_gate": {"ready": True, "url": "https://skippy-pc.tailab2cc7.ts.net.evil.example.com"}},
    {"video_gate": {"ready": True, "url": "https://user@skippy-pc.tailab2cc7.ts.net:8443"}},
    {"video_gate": {"ready": True, "url": "https://skippy-pc.tailab2cc7.ts.net:8443/x"}},
    {"video_gate": {"ready": True, "url": "https://skippy-pc.tailab2cc7.ts.net:8443\n"}},
    {"video_gate": {"ready": True, "url": 7}},
    {"music_gate": {"ready": True, "url": "https://skippy-pc.tailab2cc7.ts.net"}},       # the music gate's address is not the video gate
    {},
])
def test_no_pass_unless_the_video_gate_is_ready_on_the_tailnet(caps):
    clock = Clock()
    g = video_mod.Video(paired_home(clock, caps), clock=clock).gate()
    assert not g["ok"] and "isn't answering" in g["why"] and "pass" not in g


def test_a_local_gate_is_signed_for_only_when_a_dev_tool_hands_in_the_pattern():
    clock = Clock()
    caps = {"video_gate": {"ready": True, "url": "http://127.0.0.1:3006"}}
    assert not video_mod.Video(paired_home(clock, caps), clock=clock).gate()["ok"]
    dev = video_mod.Video(paired_home(clock, caps), clock=clock, gate_url_re=re.compile(r"http://127\.0\.0\.1:\d+"))
    assert dev.gate()["ok"] and dev.gate()["url"] == "http://127.0.0.1:3006"
    assert "gate_url_re" not in (PORTAL / "app.py").read_text(encoding="utf-8")       # create_app never loosens it


def test_no_pass_when_home_is_silent_or_unpaired():
    clock = Clock()
    home = paired_home(clock)
    clock.now += home_mod.STALE_AFTER + 1
    assert "isn't reporting" in video_mod.Video(home, clock=clock).gate()["why"]
    assert "isn't paired" in video_mod.Video(home_mod.Home(FakeVm(), clock=clock), clock=clock).gate()["why"]


def test_video_gate_is_a_capability_home_listens_for():
    assert "video_gate" in home_mod.KNOWN_CAPABILITIES


# ── the routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    return app.test_client()


def test_video_needs_the_video_grant(client):
    assert client.get("/video").status_code == 302                       # signed out: to sign-in
    assert client.get("/video/gate").status_code in (302, 401, 403)
    _add_friend(client, "pal@example.com", ["valheim", "skippy", "audio", "play", "experiments"])
    login(client, "pal@example.com")
    assert client.get("/video").status_code == 403
    r = client.get("/video/gate")
    assert r.status_code == 403 and b"pass" not in r.data
    assert b'data-tel="tile:video"' not in client.get("/").data


def test_the_captain_gets_the_tile_the_page_and_a_pass(client):
    login(client, ADMIN)
    launcher = client.get("/apps").data.decode()
    assert 'data-tel="tile:video"' in launcher and re.search(r'aria-label="Nagatha(&#39;|\')s Video Service"', launcher)
    assert client.get("/video").status_code == 200
    r = client.get("/video/gate")
    g = r.get_json()
    assert g["ok"] and g["url"] == GATE and re.fullmatch(r"[0-9]{10}\.[0-9a-f]{32}", g["pass"])
    assert "no-store" in r.headers["Cache-Control"]                      # a pass is a credential: never from a cache


def test_the_gate_route_says_why_in_plain_words(client):
    login(client, ADMIN)
    client.application.extensions["home"].accept({"capabilities": {}})
    g = client.get("/video/gate").get_json()
    assert g == {"ok": False, "why": "skippy-pc is up, but its video gate (or Jellyfin behind it) isn't answering."}


def test_the_library_offline_pill_follows_the_video_gate(client):
    login(client, ADMIN)
    tile = lambda: client.get("/apps").data.decode().split('data-tel="tile:video"')[0].rsplit('<div class="unit', 1)[1]   # noqa: E731
    assert 'data-s="on"' in tile()
    client.application.extensions["home"].accept({"capabilities": {"video_gate": {"ready": False, "url": GATE}}})
    assert 'data-s="off"' in tile()


def test_the_manifest_names_the_app_and_is_public_like_static_files(client):
    m = client.get("/video/manifest.webmanifest")
    assert m.status_code == 200 and m.mimetype == "application/manifest+json"
    j = m.get_json()
    assert j["name"] == NAME and j["short_name"] == "Nagatha"
    assert j["start_url"] == "/video" and j["scope"] == "/video" and j["id"] == "/video"
    assert j["display"] == "standalone" and j["background_color"] == "#000000"
    assert all(client.get(i["src"]).status_code == 200 for i in j["icons"])
    assert "pass" not in m.data.decode() and "ts.net" not in m.data.decode()    # public: it names the app and nothing else


def test_the_page_is_named_and_is_an_app_with_a_way_home(client):
    login(client, ADMIN)
    page = client.get("/video").data.decode()
    assert f"<title>{NAME} — Maker Portal</title>".replace("'", "&#39;") in page or f"<title>{NAME} — Maker Portal</title>" in page
    assert 'aria-label="Back"' in page and 'aria-label="Home"' in page and 'data-app="video"' in page      # the bridge's strip: back, the glyph, home
    assert 'rel="manifest"' in page and 'name="apple-mobile-web-app-title" content="Nagatha"' in page


def test_the_app_has_its_own_accent():
    base = (PORTAL / "templates" / "base.html").read_text(encoding="utf-8")
    m = re.search(r'\[data-app="video"\]\s*\{\s*--accent:\s*var\((--[\w-]+)\)', base)
    assert m and m.group(1) != "--pink"                                   # not Bilby's colour


def test_the_page_loads_its_scripts_and_styles_and_they_exist(client):
    login(client, ADMIN)
    page = client.get("/video").data.decode()
    for asset in ("nagatha-core.js", "nagatha-app.js", "nagatha.css", "bridge.js", "bridge-pure.js", "bridge.css"):
        assert f"/static/{asset}" in page and client.get(f"/static/{asset}").status_code == 200
    assert page.index("/static/nagatha-core.js") < page.index("/static/nagatha-app.js")
    hls_src = re.search(r'data-hls-src="([^"]+)"', page).group(1)
    assert hls_src == "/static/vendor/hls-1.6.15.min.js" and client.get(hls_src).status_code == 200
    assert "cdn" not in page.lower()                                      # self-hosted: nothing from a CDN at play time


def test_hls_js_is_the_pinned_release_byte_for_byte_with_its_licence():
    raw = HLS.read_bytes().replace(b"\r\n", b"\n")                        # (a Windows checkout may have turned LF into CRLF)
    head, body = raw.split(b"\n", 1)
    assert b"hls.js 1.6.15" in head and b"Apache-2.0" in head and b"https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.6.15/hls.min.js" in head
    sri = re.search(rb"sha512-([A-Za-z0-9+/=]+)", head).group(1).decode()
    assert base64.b64encode(hashlib.sha512(body).digest()).decode() == sri == \
        "CSVqc4a7tn+tizDNt+eDoVn2fXYAwMDpCLrwGlWrOktNfZQ9gp4dKKScElMeRlrIifhliXs0a06BLaUgmMlCUw=="
    lic = (STATIC / "vendor" / "hls.js.LICENSE.txt").read_text(encoding="utf-8")
    assert "Apache License, Version 2.0" in lic and "Dailymotion" in lic


def test_the_page_has_every_element_the_script_looks_up():
    html = (PORTAL / "templates" / "video.html").read_text(encoding="utf-8")
    js = (STATIC / "nagatha-app.js").read_text(encoding="utf-8")
    have = set(re.findall(r'\bid="([\w-]+)"', html))
    looked_up = set(re.findall(r'\$\("#([\w-]+)"', js)) | set(re.findall(r'getElementById\("([\w-]+)"', js))
    assert looked_up and not looked_up - have, looked_up - have
    acts = set(re.findall(r'case "([\w-]+)"', js))
    for act in set(re.findall(r'data-act="([\w-]+)"', html)):
        assert act in acts, act
    for attr in ("data-gate-url", "data-hls-src"):
        assert attr in html


def test_every_control_is_a_real_button_with_a_name_and_the_help_lists_the_shortcuts():
    html = (PORTAL / "templates" / "video.html").read_text(encoding="utf-8")
    for tag in re.findall(r"<button\b[^>]*>", html):
        assert 'type="' in tag, tag
        assert 'aria-label="' in tag or "data-act=\"tap\"" in tag or 'type="submit"' in tag, tag
    for key in ("Space", "<kbd>F</kbd>", "<kbd>M</kbd>", "<kbd>C</kbd>", "<kbd>P</kbd>", "<kbd>Esc</kbd>", "<kbd>/</kbd>", "<kbd>?</kbd>"):
        assert key in html, key
    assert 'id="p-seek"' in html and 'aria-label="Position in the film"' in html
    assert re.search(r'id="tapbar"[^>]*\bhidden\b', html) and "tap the page" in html      # the autoplay rule, explained
    assert 'role="alert"' in html and 'aria-live="polite"' in html
    assert re.search(r"<video\b[^>]*\bplaysinline\b[^>]*\bcrossorigin=\"anonymous\"", html)   # iPhone inline; CORS for <track>s


def test_the_app_uses_the_portals_colours_and_keeps_focus_rings():
    css = (STATIC / "nagatha.css").read_text(encoding="utf-8")
    base = (PORTAL / "templates" / "base.html").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", code), "a colour literal: use the portal's variables"
    assert "gradient" not in code                                         # the colour doctrine: black base, no smears
    defined = set(re.findall(r"(--[\w-]+)\s*:", base)) | set(re.findall(r"(--[\w-]+)\s*:", code))
    defined |= set(re.findall(r'setProperty\("(--[\w-]+)"', (STATIC / "nagatha-app.js").read_text(encoding="utf-8")))
    used = set(re.findall(r"var\((--[\w-]+)", code))
    assert used and not used - defined, used - defined
    assert len(re.findall(r"outline\s*:\s*none", code)) <= 3              # the search box, the scroller, a card (each draws its own)
    assert "prefers-reduced-motion" in css


def test_the_app_tells_the_portal_nothing_about_what_is_watched():
    """The pass is the only thing the page asks the portal for; the catalog, the films and the progress go to skippy-pc's gate."""
    js = (STATIC / "nagatha-app.js").read_text(encoding="utf-8")
    assert "data-tel" not in js and "/telemetry" not in js
    assert set(re.findall(r"fetch\(([\w.]+)", js)) <= {"cfg.gate", "C.catalogUrl", "C.progressUrl", "C.directUrl"}
    assert "credentials: \"same-origin\"" in js and js.count("credentials:") == 1     # only the portal call carries the cookie


def test_a_friend_gets_a_read_only_pass_the_captain_a_read_write_one(client):
    import video as video_mod
    login(client, ADMIN)
    rw = client.get("/video/gate").get_json()["pass"]
    client.get("/logout")
    _add_friend(client, "pal@example.com", ["video"])
    login(client, "pal@example.com")
    ro = client.get("/video/gate").get_json()["pass"]
    assert ro != rw and ro.split(".")[0] == rw.split(".")[0]                       # same expiry, different scope
    key = video_mod.gate_key(client.application.extensions["home"]._stored_hash())
    exp = int(ro.split(".")[0])
    assert ro == video_mod.sign(key, exp, "ro") and rw == video_mod.sign(key, exp, "rw")


# ── the video page is a page of the bridge ───────────────────────────────────────────────────────────────────────────
def test_the_video_page_is_on_the_bridge_with_its_own_glyph(client):
    login(client, ADMIN)
    page = client.get("/video").data.decode()
    assert '<body class="bridge"' in page and 'href="/static/bridge.css"' in page
    assert page.index("/static/bridge-pure.js") < page.index("/static/bridge.js") < page.index("/static/nagatha-core.js")
    assert '<use href="#g-lens"/>' in page                                                  # the strip's mark: the same glyph as its control on the launcher
    assert '<h1 class="apptitle"' not in page and 'id="home-btn"' not in page               # none of the old pages' chrome
    for need in ('id="deck"', 'id="listwrap"', 'id="scrub"', 'id="letters-key"', 'id="sort"', 'id="signal"', 'id="p-signal"', 'id="toasts"'):
        assert need in page, need
    assert "<nav" not in page and "<select" not in page                                     # the old text links and the sort menu are gone


def test_every_glyph_the_video_page_draws_is_in_the_portals_glyph_set():
    html = (PORTAL / "templates" / "video.html").read_text(encoding="utf-8")
    js = (STATIC / "nagatha-app.js").read_text(encoding="utf-8")
    drawn = set(re.findall(r'<symbol id="g-([\w-]+)"', (PORTAL / "templates" / "_glyphs.html").read_text(encoding="utf-8")))
    alias = dict(re.findall(r'(\w+): "(\w+)"', re.search(r"const GLYPH = \{(.*?)\}", js, re.S).group(1)))
    named = set(re.findall(r'\bico\("(\w+)"\)', js)) | set(re.findall(r'\bkey\("(\w+)"', js)) | set(re.findall(r'\bglyph: "(\w+)"', js))
    named |= set(re.findall(r'\b(?:title|head)\("(\w+)"', js)) | set(re.findall(r'\bmsg\([^;]*?, "(\w+)"\)', js))
    named |= set(re.findall(r'\["\w+", "(\w+)", (?:LABELS\.\w+)', js)) | set(re.findall(r'actKey\([^;]*?, "(\w+)", (?:true|false)', js))
    named |= set(re.findall(r'data-i="(\w+)"', html)) | set(re.findall(r'<use href="#g-([\w-]+)"', html)) | set(re.findall(r'setIcon\(\w+, [^"]*"(\w+)"', js))
    named |= {"play", "pause", "vol", "mute", "fs", "fsx"}                                    # the player's own, switched by state
    assert len(named) > 18, named
    missing = {alias.get(n, n) for n in named} - drawn
    assert not missing, missing


def test_the_video_page_never_scrolls_and_draws_no_gradients():
    css = (STATIC / "nagatha.css").read_text(encoding="utf-8") + (STATIC / "bridge.css").read_text(encoding="utf-8")
    js = (STATIC / "nagatha-app.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"overflow(?:-y)?\s*:\s*(?:auto|scroll)", code)
    assert not re.search(r"(?:linear|radial|conic)-gradient", code)
    assert "scrollTop" not in js and "Bridge.PList" in js and "C.chunk" in js                # posters are cut into screens, never scrolled


def test_the_video_page_waits_out_a_dead_zone_instead_of_giving_up():
    """A phone in the mountains drops off the network: the film keeps its place and retries, quietly, until the line is back."""
    js = (STATIC / "nagatha-app.js").read_text(encoding="utf-8")
    assert "function recoverStart" in js and "function recoverTick" in js and "C.recoverDelay" in js and "C.stalled" in js
    for event in ('"online"', '"pageshow"', '"visibilitychange"'):
        assert f"addEventListener({event}" in js, event                                      # retried the instant the line may be back
    assert "recovering: true" in js and "if (!opts.recovering) report(" in js               # a retry is not a new viewing: nothing is reported while it waits
    assert js.count("recoverStart(") >= 6                                                    # every way the line can die ends in the wait, not in an error box
    css = (STATIC / "bridge.css").read_text(encoding="utf-8")
    assert '.lamp[data-s="warn"]' in css and '.lamp[data-s="off"]' in css                   # the lamp says so by shape as well as colour
