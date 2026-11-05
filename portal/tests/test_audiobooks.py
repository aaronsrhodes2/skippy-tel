"""The Audiobooks app (/audiobooks): the Captain's alone, never a friend's grant. The portal signs passes only the home audiobook-gate accepts (its own
label, so a music, video or voice pass never opens it), and only for a gate address on the tailnet; "Play on" a room box goes through Bilby's Connect
as `book:` commands the hub's BookPlayer understands; no audio or file ever passes through the portal. What a browser does (the audio element, the
layout) is seen by hand with tests/dev_books.py against the real gate; the page's markup is checked here against the ids its script looks up."""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import audiobooks as books_mod  # noqa: E402
import catalog  # noqa: E402
import home as home_mod  # noqa: E402
import player as player_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

TOKEN = "the-home-token"
GATE = "https://skippy-pc.tailab2cc7.ts.net:10000/books"
PORTAL = Path(__file__).resolve().parents[1]
STATIC = PORTAL / "static"
ROOT = PORTAL.parent


def _gate_module():
    """services/audiobook-gate/gate.py, loaded by path: the other half of the handshake."""
    root = ROOT / "services" / "audiobook-gate"
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("audiobook_gate_gate", root / "gate.py")
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
    home.accept({"capabilities": caps if caps is not None else {"book_gate": {"ready": True, "url": GATE}}})
    return home


# ── the pass ──────────────────────────────────────────────────────────────────
def test_the_pass_opens_the_audiobook_gate_and_nothing_else():
    clock = Clock()
    home = paired_home(clock)
    g = books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock).gate()
    assert g["ok"] and g["url"] == GATE
    assert books_mod.PASS_S <= g["exp"] - clock.now <= books_mod.PASS_S + books_mod.PASS_BUCKET_S
    gate = _gate_module()
    assert gate.valid(gate.gate_key(TOKEN), g["pass"], clock.now)              # the same key on both sides
    assert not gate.valid(gate.gate_key("another-token"), g["pass"], clock.now)
    assert not gate.valid(gate.gate_key(TOKEN), g["pass"], g["exp"] + 1)       # and it runs out


def test_a_music_pass_never_opens_the_books_and_a_books_pass_never_opens_the_music():
    clock = Clock()
    home = paired_home(clock, {"book_gate": {"ready": True, "url": GATE}, "music_gate": {"ready": True, "url": "https://skippy-pc.tailab2cc7.ts.net"}})
    mine = books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock).gate()["pass"]
    music = player_mod.Player(home, clock=clock).gate()["pass"]
    gate = _gate_module()
    key = gate.gate_key(TOKEN)
    assert gate.valid(key, mine, clock.now) and not gate.valid(key, music, clock.now)
    music_gate = ROOT / "services" / "music-gate" / "gate.py"
    text = music_gate.read_text(encoding="utf-8")
    m = re.search(r'def sign\(key: bytes, exp: int\) -> str:\n    return f"\{exp\}\.\{hmac\.new\(key, f\'(\w+):', text)
    assert m and m.group(1) == "music"                                         # and the labels really differ in the signed message too


def test_every_pass_in_a_bucket_is_the_same_string_so_the_browser_can_cache_by_url():
    clock = Clock()
    home = paired_home(clock)
    b = books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock)
    start = clock.now - clock.now % books_mod.PASS_BUCKET_S
    clock.now = start
    first = b.gate()["pass"]
    clock.now = start + books_mod.PASS_BUCKET_S - 1
    home.accept({"capabilities": {"book_gate": {"ready": True, "url": GATE}}})
    assert b.gate()["pass"] == first
    clock.now = start + books_mod.PASS_BUCKET_S
    home.accept({"capabilities": {"book_gate": {"ready": True, "url": GATE}}})
    assert b.gate()["pass"] != first


@pytest.mark.parametrize("url", [
    "https://evil.example.com/books", "http://skippy-pc.tailab2cc7.ts.net:10000/books",
    "https://skippy-pc.tailab2cc7.ts.net.evil.example.com:10000/books", "https://skippy-pc.tailab2cc7.ts.net:10000",
    "https://skippy-pc.tailab2cc7.ts.net:10000/books/../x"])
def test_no_pass_for_a_gate_off_the_tailnet(url):
    clock = Clock()
    home = paired_home(clock, {"book_gate": {"ready": True, "url": url}})
    g = books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock).gate()
    assert not g["ok"] and "pass" not in g and "isn't answering" in g["why"]


def test_no_pass_unless_the_gate_says_ready_or_home_is_there():
    clock = Clock()
    home = paired_home(clock, {"book_gate": {"ready": False, "url": GATE}})
    assert not books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock).gate()["ok"]
    home = paired_home(clock)
    clock.now += home_mod.STALE_AFTER + 1
    assert "isn't reporting" in books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock).gate()["why"]
    unpaired = home_mod.Home(FakeVm(), clock=clock)
    assert "isn't paired" in books_mod.Books(unpaired, player_mod.Player(unpaired, clock=clock), clock=clock).gate()["why"]


def test_a_local_gate_is_signed_for_only_when_a_dev_tool_hands_in_the_pattern():
    clock = Clock()
    caps = {"book_gate": {"ready": True, "url": "http://127.0.0.1:8766"}}
    home = paired_home(clock, caps)
    assert not books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock).gate()["ok"]
    dev = books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock, gate_url_re=re.compile(r"http://127\.0\.0\.1:\d+"))
    assert dev.gate()["ok"]
    assert "gate_url_re" not in (PORTAL / "app.py").read_text(encoding="utf-8")                 # create_app never sets it


# ── Play on: a room box, through Connect ─────────────────────────────────────────
def _office(player, clock=None, book=None, live=True):
    state = {"track": "", "queue": [], "armed": True}
    if book is not None:
        state["book"] = book
    return player.checkin(ADMIN, "skippyroom-office", "Office (Skippy)", state, room="box-office", box=True)


def test_a_boxs_book_state_is_kept_and_listed_as_a_room():
    clock = Clock()
    home = paired_home(clock)
    p = player_mod.Player(home, clock=clock)
    b = books_mod.Books(home, p, clock=clock)
    _office(p, book={"id": "dune-m4b", "title": "Dune", "pos": 100.0, "chapter": 2, "playing": True, "speed": 1.25, "junk": "x"})
    p.checkin(ADMIN, "phone1234", "Phone", {"track": "a/b.mp3", "queue": ["a/b.mp3"]})         # a browser player is not a room
    [room] = b.rooms(ADMIN)
    assert room["id"] == "skippyroom-office" and room["live"] and room["book"] == {"id": "dune-m4b", "title": "Dune", "pos": 100.0, "chapter": 2, "playing": True, "speed": 1.25}
    assert b.rooms(FRIEND) == []                                                                 # a room is the Captain's
    _office(p, book={"id": "../etc", "title": "x"})
    assert b.rooms(ADMIN)[0]["book"] is None                                                     # a bad id is dropped, not kept


@pytest.mark.parametrize("action", ["book:play:dune-m4b", "book:play:dune-m4b:3600.5", "book:pause", "book:resume", "book:skip:-30", "book:skip:30",
                                    "book:chapter:1", "book:chapter:-1", "book:speed:1.25", "book:speed:2"])
def test_book_commands_are_queued_for_a_room_box(action):
    clock = Clock()
    home = paired_home(clock)
    p = player_mod.Player(home, clock=clock)
    b = books_mod.Books(home, p, clock=clock)
    _office(p)
    assert b.command(ADMIN, "skippyroom-office", action)
    assert _office(p)["commands"] == [action]


@pytest.mark.parametrize("device, action", [
    ("skippyroom-office", "play"), ("skippyroom-office", "pause"), ("skippyroom-office", "take:abcdefgh1"),     # the music's own keys are not this route's
    ("skippyroom-office", "book:play:../../x"), ("skippyroom-office", "book:play:UPPER"), ("skippyroom-office", "book:skip:99999"),
    ("skippyroom-office", "book:rm -rf /"), ("skippyroom-office", "book:chapter:5"), ("skippyroom-office", "book:speed:12"),
    ("phone1234", "book:pause"), ("downstairs", "book:pause"), ("nope", "book:pause")])
def test_only_book_commands_and_only_for_a_room_box(device, action):
    clock = Clock()
    home = paired_home(clock)
    p = player_mod.Player(home, clock=clock)
    b = books_mod.Books(home, p, clock=clock)
    _office(p)
    p.checkin(ADMIN, "phone1234", "Phone", None)
    assert not b.command(ADMIN, device, action)
    assert _office(p)["commands"] == []


def test_a_command_for_a_box_that_stopped_checking_in_is_refused():
    clock = Clock()
    home = paired_home(clock)
    p = player_mod.Player(home, clock=clock)
    b = books_mod.Books(home, p, clock=clock)
    _office(p)
    clock.now += player_mod.LIVE_S + 1
    assert not b.command(ADMIN, "skippyroom-office", "book:pause")


def test_the_hubs_commands_and_the_portals_are_the_same_grammar():
    """services/satellite/audiobooks.py COMMAND and player.BOOK_CMD are written twice (separate images): the same strings pass both."""
    spec = importlib.util.spec_from_file_location("sat_audiobooks", ROOT / "services" / "satellite" / "audiobooks.py")
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    cases = ["book:play:dune-m4b", "book:play:dune-m4b:3600.5", "book:pause", "book:resume", "book:skip:-30", "book:chapter:-1", "book:speed:1.25",
             "book:play:", "book:play:UP", "book:skip:99999", "book:chapter:2", "book:speed:12", "book:", "play", "book:pause now"]
    for c in cases:
        assert bool(hub.COMMAND.fullmatch(c)) == bool(player_mod.BOOK_CMD.fullmatch(c)), c


# ── the routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    return app.test_client()


ROUTES = (("get", "/audiobooks", None), ("get", "/audiobooks/gate", None), ("get", "/audiobooks/rooms", None),
          ("post", "/audiobooks/command", {"device": "skippyroom-office", "action": "book:pause"}))


def _call(client, method, url, body):
    return getattr(client, method)(url, **({"json": body} if body is not None else {}))


def test_signed_out_goes_to_sign_in_and_a_friend_is_refused_whatever_they_are_granted(client):
    for method, url, body in ROUTES:
        assert _call(client, method, url, body).status_code == 302
    everything = ["valheim", "skippy", "audio", "video", "play", "experiments", "tabletop"]
    _add_friend(client, "pal@example.com", everything)
    login(client, "pal@example.com")
    for method, url, body in ROUTES:
        assert _call(client, method, url, body).status_code == 403, url
    assert b'data-tel="tile:books"' not in client.get("/apps").data and b"/audiobooks" not in client.get("/apps").data


def test_it_is_not_a_grant_anyone_can_be_given():
    assert not any("book" in k.lower() for k in catalog.PORTALS)
    assert not any("book" in m.lower() for m in (PORTAL / "friends.py").read_text(encoding="utf-8").split("\n") if "PORTALS" in m)


def test_the_captain_gets_the_tile_the_page_a_pass_and_the_rooms(client):
    login(client, ADMIN)
    assert 'data-tel="tile:books"' in client.get("/apps").data.decode()
    page = client.get("/audiobooks")
    assert page.status_code == 200 and b"Audiobooks" in page.data
    g = client.get("/audiobooks/gate")
    assert g.get_json()["ok"] and g.get_json()["url"] == GATE and g.headers["Cache-Control"] == "no-store"     # a pass is never cached
    assert client.get("/audiobooks/rooms").get_json() == {"rooms": []}
    app = client.application
    app.extensions["player"].checkin(ADMIN, "skippyroom-office", "Office (Skippy)", {"track": "", "queue": []}, room="box-office", box=True)
    assert [r["name"] for r in client.get("/audiobooks/rooms").get_json()["rooms"]] == ["Office (Skippy)"]
    ok = client.post("/audiobooks/command", json={"device": "skippyroom-office", "action": "book:play:dune-m4b:12.5"})
    assert ok.status_code == 200 and ok.get_json() == {"queued": True}
    assert client.post("/audiobooks/command", json={"device": "skippyroom-office", "action": "pause"}).status_code == 409
    assert client.post("/audiobooks/command", json={}).status_code == 409


def test_the_launcher_lamp_follows_the_gate(client):
    login(client, ADMIN)
    tiles = {t["id"]: t for t in client.get("/apps/tiles").get_json()["tiles"]}
    assert tiles["books"]["state"] == "on" and tiles["books"]["glyph"] == "book" and tiles["books"]["href"] == "/audiobooks"


def test_the_portal_never_serves_a_book_or_a_cover():
    """No route here streams audio or art: the gate does, straight to the browser."""
    text = (PORTAL / "audiobooks.py").read_text(encoding="utf-8")
    assert "send_file" not in text and "send_from_directory" not in text
    app_text = (PORTAL / "app.py").read_text(encoding="utf-8")
    section = app_text[app_text.index('shelf = Blueprint("audiobooks"'):app_text.index("app.register_blueprint(shelf)")]
    assert "send_file" not in section and "send_from_directory" not in section and "open(" not in section


# ── the page ────────────────────────────────────────────────────────────────────
def test_the_pages_markup_has_every_id_its_script_looks_up_and_is_a_bridge_page(client):
    login(client, ADMIN)
    html = client.get("/audiobooks").data.decode()
    ids = set(re.findall(r'\bid="([\w-]+)"', html))
    script = (STATIC / "audiobooks-app.js").read_text(encoding="utf-8")
    wanted = set(re.findall(r'\$\("#([\w-]+)"', script)) | set(re.findall(r'\$\$\("#([\w-]+)', script))
    made_by_the_script = {"m-grid"}                                                                       # the Play on menu's grid, built when it opens
    assert wanted - ids - made_by_the_script == set(), f"the script looks up ids the page does not have: {sorted(wanted - ids)}"
    assert 'class="bridge' in html and 'data-app="books"' in html                                         # one phone screen, the bridge's shell
    assert 'data-gate-url="/audiobooks/gate"' in html and 'data-command-url="/audiobooks/command"' in html and 'data-rooms-url="/audiobooks/rooms"' in html
    for used in set(re.findall(r'#g-([\w-]+)', html)) | set(re.findall(r'data-i="([\w-]+)"', html)) | set(re.findall(r'icon\("([\w-]+)"\)', script)):
        assert f'id="g-{used}"' in client.get("/apps").data.decode(), f"glyph {used} is not in the glyph set"


def test_no_emoji_and_the_colours_come_from_the_portals_tokens():
    css = (STATIC / "audiobooks.css").read_text(encoding="utf-8")
    js = (STATIC / "audiobooks-app.js").read_text(encoding="utf-8")
    html = (PORTAL / "templates" / "audiobooks.html").read_text(encoding="utf-8")
    assert not re.findall("[\U0001F300-\U0001FAFF☀-➿]", css + js + html)
    css = re.sub(r"/[*].*?[*]/", "", css, flags=re.S)                                                   # (comments may say the words)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css) and "gradient" not in css                      # tokens only, no gradients on the glasses


def test_the_scripts_gate_calls_are_the_gates_routes():
    """Every path the page fetches from the gate exists in the gate."""
    script = (STATIC / "audiobooks-app.js").read_text(encoding="utf-8")
    gate_text = (ROOT / "services" / "audiobook-gate" / "gate.py").read_text(encoding="utf-8")
    for path in set(re.findall(r"gu\(`?\"?([a-z]+\.?[a-z]*)[/`\"?]", script)):
        assert f"/{path}" in gate_text, path
    assert "keepalive: true" in script and 'method: "PUT"' in script


# ── the direct door (edge.py; test_edge.py has the rest): the pass also says where to reach the gate without the Funnel ──
class _Door:
    """Stands in for edge.Edge: gate() only asks direct(prefix)."""

    def __init__(self, up=True):
        self.up = up

    def direct(self, prefix):
        return f"https://home.makerportal.live/{prefix}" if self.up else None


def test_the_pass_names_the_direct_door_while_it_is_up_and_the_funnel_address_is_untouched():
    clock = Clock()
    home = paired_home(clock)
    mine = lambda edge=None: books_mod.Books(home, player_mod.Player(home, clock=clock), clock=clock, edge=edge).gate()   # noqa: E731
    assert mine()["direct"] is None                                                                     # no door configured: the field is there, null
    up = mine(_Door(True))
    assert up["direct"] == "https://home.makerportal.live/books" and up["url"] == GATE                   # "url" is still the Funnel's
    assert mine(_Door(False))["direct"] is None
    bad = paired_home(clock, {"book_gate": {"ready": True, "url": "https://home.makerportal.live/books"}})   # the door's own address is no gate address
    refused = books_mod.Books(bad, player_mod.Player(bad, clock=clock), clock=clock, edge=_Door(True)).gate()
    assert not refused["ok"] and "direct" not in refused and "pass" not in refused
