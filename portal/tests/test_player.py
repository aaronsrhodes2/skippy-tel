"""The Music app: the portal signs passes the home music-gate accepts (same key from the home token, no new secret),
only for a gate address on the tailnet, and keeps each device's place so another can pick up or act as its remote.
Admin only."""
import hashlib
import importlib.util
import json
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


def test_the_pass_opens_the_home_gate_and_nothing_else():
    clock = Clock()
    g = player_mod.Player(paired_home(clock), clock=clock).gate()
    assert g["ok"] and g["url"] == GATE and g["exp"] == clock.now + player_mod.PASS_S
    gate = _gate_module()
    assert gate.valid(gate.gate_key(TOKEN), g["pass"], clock.now)              # the same key on both sides
    assert not gate.valid(gate.gate_key("another-token"), g["pass"], clock.now)
    assert not gate.valid(gate.gate_key(TOKEN), g["pass"], g["exp"] + 1)       # and it runs out


@pytest.mark.parametrize("caps, why", [
    ({"music_gate": {"ready": False, "url": GATE}}, "isn't answering"),
    ({"music_gate": {"ready": True, "url": "https://evil.example.com"}}, "isn't answering"),   # never sent off the tailnet
    ({"music_gate": {"ready": True, "url": "http://skippy-pc.tailab2cc7.ts.net"}}, "isn't answering"),
    ({}, "isn't answering"),
])
def test_no_pass_unless_the_gate_is_up_on_the_tailnet(caps, why):
    clock = Clock()
    g = player_mod.Player(paired_home(clock, caps), clock=clock).gate()
    assert not g["ok"] and why in g["why"] and "pass" not in g


def test_no_pass_when_home_is_silent_or_unpaired():
    clock = Clock()
    home = paired_home(clock)
    clock.now += home_mod.STALE_AFTER + 1
    assert "isn't reporting" in player_mod.Player(home, clock=clock).gate()["why"]
    assert "isn't paired" in player_mod.Player(home_mod.Home(FakeVm(), clock=clock), clock=clock).gate()["why"]


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


# ── the routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    return app.test_client()


def test_music_is_the_captains_alone(client):
    assert client.get("/music").status_code == 302                      # signed out: to sign-in
    _add_friend(client, FRIEND, ["games", "skippy", "bilby", "experiments"])
    login(client, FRIEND)
    for r in (client.get("/music"), client.get("/music/gate"),
              client.post("/music/checkin", json={"device": "phone1234"}),
              client.post("/music/command", json={"device": "phone1234", "action": "pause"})):
        assert r.status_code == 403
    assert b'data-tel="tile:music"' not in client.get("/").data


def test_the_captain_gets_the_tile_the_player_and_a_pass(client):
    login(client, ADMIN)
    assert b'data-tel="tile:music"' in client.get("/").data
    page = client.get("/music").data.decode()
    assert 'rel="manifest"' in page and "mediaSession" in page
    g = client.get("/music/gate").get_json()
    assert g["ok"] and g["url"] == GATE
    assert client.post("/music/checkin", json={"device": "bad id!"}).status_code == 400
    assert client.post("/music/checkin", json={"device": "phone1234", "name": "iPhone", "state": state()}).status_code == 200
    assert client.post("/music/command", json={"device": "phone1234", "action": "pause"}).get_json() == {"queued": True}
    m = client.get("/music/manifest.webmanifest")
    assert m.mimetype == "application/manifest+json" and m.get_json()["start_url"] == "/music"
