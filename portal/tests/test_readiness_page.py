"""The game's own log decides "starting" vs "online" on the pages, the live endpoint, the chat tool and `ops status`;
the boot timer (Game.ready_minutes) only stands in when the snapshot has no READY line for the game."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from gcp import VmStatus  # noqa: E402

ADMIN = "aaron.s.rhodes@gmail.com"
FRIEND = "friend@example.com"
SETTINGS = {
    "SECRET_KEY": "test", "SESSION_COOKIE_SECURE": False, "SESSION_COOKIE_SAMESITE": "Lax",
    "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret",
    "ALLOWED_EMAILS": frozenset({ADMIN, FRIEND}), "ADMIN_EMAILS": frozenset({ADMIN}),
    "GCP_PROJECT": "p", "GCP_ZONE": "z", "GCP_INSTANCE": "i",
    "CHAT_MODEL": "claude-haiku-4-5", "CHAT_ENABLED": True,
    "DISCORD_INVITE": "gPTVS7Hmj", "DISCORD_BOT_TOKEN": "",
    "OPS_EMAILS": frozenset({"skippy-deployer@skippy-tel.iam.gserviceaccount.com"}),
}


class FakeVm:
    """The parts of gcp.GamesVm the pages use. The serial port 2 is the skippy-diag snapshot; reads are counted."""

    def __init__(self):
        self.state = VmStatus(status="TERMINATED", machine_type="e2-standard-4", external_ip="35.1.2.3",
                              games=["valheim"], idle_minutes="30",
                              env={"VALHEIM_PASSWORD": "hunter22", "DRAGONWILDS_OWNER_ID": "owner1",
                                   "DRAGONWILDS_ADMIN_PASSWORD": "adm1n", "DRAGONWILDS_WORLD_PASSWORD": "dragonpw"})
        self.serial = {1: "", 2: ""}
        self.serial_reads = []
        self.metadata = {}

    def status(self):
        return self.state

    def serial_output(self, port):
        self.serial_reads.append(port)
        return self.serial[port]

    def get_metadata(self, key):
        return self.metadata.get(key, "")

    def set_metadata(self, key, value):
        self.metadata[key] = value

    def update_env(self, updates):
        self.state.env.update(updates)


class FakeSkippy:
    def reply(self, system, messages, tools=None, run_tool=None, model=None, force_tool=None):
        self.tools, self.run_tool = tools, run_tool
        return "Obviously."


@pytest.fixture
def env():
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy)
    return app.test_client(), vm, skippy


def login(client, email):
    with client.session_transaction() as s:
        s["user"] = {"email": email, "name": "Captain", "picture": None}


def boot(vm, game="valheim", minutes_up=1):
    """The VM is running `game` and booted `minutes_up` minutes ago."""
    vm.state.status, vm.state.games = "RUNNING", [game]
    vm.state.started_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_up)


def snapshot(vm, *lines, taken=None):
    """What skippy-diag wrote to serial port 2. By default it is fresh: taken a minute after the boot."""
    taken = taken or (vm.state.started_at + timedelta(minutes=1) if vm.state.started_at else datetime.now(timezone.utc))
    vm.serial[2] = (f"=== skippy-diag {taken.isoformat(timespec='seconds')} ===\nNAMES\tSTATUS\tPORTS\n"
                    + "--- readiness\n" + "".join(f"{ln}\n" for ln in lines) + "=== end ===\n")


# ── /games/<id>/live ──────────────────────────────────────────────────────────

# (game, minutes since boot, READY lines in the snapshot, starting?, the READY value the endpoint reports)
LIVE_CASES = [
    # no answer from the log: the boot timer, exactly as before this feature
    ("valheim", 1, [], True, None), ("valheim", 12, [], False, None),
    # the log says joinable: not starting, even a minute in (Valheim's ready_minutes is 5)
    ("valheim", 1, ["READY valheim=1"], False, True),
    # the log says not yet: still starting, even 12 minutes in (a slow first boot; the timer would have said online)
    ("valheim", 12, ["READY valheim=0"], True, False),
    # ...but a marker that has said "not yet" for 45+ minutes has stopped matching: back to the timer
    ("valheim", 50, ["READY valheim=0"], False, False),
    # Dragonwilds' markers are UNVERIFIED: the log may only make it ready sooner, never later than the timer
    ("dragonwilds", 1, ["READY dragonwilds=1"], False, True), ("dragonwilds", 12, ["READY dragonwilds=0"], False, False),
    ("dragonwilds", 2, ["READY dragonwilds=0"], True, False),
    # a READY line for some other game says nothing about this one
    ("valheim", 1, ["READY factorio=1"], True, None), ("valheim", 12, ["READY factorio=0"], False, None),
]


@pytest.mark.parametrize("game,minutes,lines,starting,ready", LIVE_CASES)
def test_live_follows_the_games_own_log_and_falls_back_to_the_timer(env, game, minutes, lines, starting, ready):
    client, vm, _ = env
    login(client, ADMIN)
    boot(vm, game, minutes)
    snapshot(vm, *lines)
    got = client.get(f"/games/{game}/live").get_json()
    assert got["on"] is True and got["starting"] is starting and got["ready"] is ready, got


def test_live_ignores_a_snapshot_from_the_previous_boot(env):
    """The serial port can keep the last session's snapshot; its 'ready' must not pass for this boot's."""
    client, vm, _ = env
    login(client, ADMIN)
    boot(vm, "valheim", minutes_up=1)
    snapshot(vm, "READY valheim=1", taken=vm.state.started_at - timedelta(hours=3))
    got = client.get("/games/valheim/live").get_json()
    assert got["starting"] is True and got["ready"] is None                     # the timer, as if there were no READY line
    snapshot(vm, "READY valheim=1", taken=vm.state.started_at + timedelta(seconds=75))
    got = client.get("/games/valheim/live").get_json()
    assert got["starting"] is False and got["ready"] is True                    # this boot's own snapshot counts


def test_live_keeps_its_old_shape_when_the_game_is_not_up(env):
    client, vm, _ = env
    login(client, ADMIN)
    assert client.get("/games/valheim/live").get_json() == {"on": False, "starting": False, "code": None, "players": None}
    boot(vm, "factorio", 1)                                                       # another game is the one that's up
    snapshot(vm, "READY valheim=1", "READY factorio=0")
    assert client.get("/games/valheim/live").get_json() == {"on": False, "starting": False, "code": None, "players": None}


def test_live_never_breaks_when_the_serial_port_cannot_be_read(env):
    client, vm, _ = env
    login(client, ADMIN)
    boot(vm, "valheim", 1)

    def broken(port):
        raise RuntimeError("serial port unavailable")
    vm.serial_output = broken
    got = client.get("/games/valheim/live").get_json()
    assert got["on"] is True and got["starting"] is True and got["ready"] is None   # the timer, and the page still works


def test_live_and_the_page_agree_on_every_case(env):
    """The page's first paint and its 20-second poll come from the same decision: they can't disagree, or the page
    would reload in a loop."""
    client, vm, _ = env
    login(client, ADMIN)
    for game, minutes, lines, starting, _ in LIVE_CASES:
        boot(vm, game, minutes)
        snapshot(vm, *lines)
        page = client.get(f"/games/{game}").data.decode()
        assert f"starting: {'true' if starting else 'false'}}};" in page, (game, minutes, lines)


# ── the game's page ───────────────────────────────────────────────────────────

def _lamp(page):
    """The game page's lamp (templates/game.html): on = up, boot = coming up, idle = asleep, off = down or another game."""
    import re
    return re.search(r'class="gname" data-s="(\w+)"', page).group(1)


def test_the_page_says_online_as_soon_as_the_log_does(env):
    client, vm, _ = env
    login(client, FRIEND)
    boot(vm, "valheim", minutes_up=1)                       # the timer alone would say "starting" until minute 5
    snapshot(vm, "READY valheim=1")
    page = client.get("/games/valheim").data.decode()
    assert _lamp(page) == "on" and "~5" not in page          # lit, and no "coming up" clock
    assert "35.1.2.3:2456" in page and "hunter22" in page   # and the way in is there


def test_the_page_keeps_saying_starting_while_the_log_does_however_late(env):
    client, vm, _ = env
    login(client, FRIEND)
    boot(vm, "valheim", minutes_up=12)                      # the timer alone would say online
    snapshot(vm, "READY valheim=0")
    page = client.get("/games/valheim").data.decode()
    assert _lamp(page) == "boot" and "<b>12</b> / ~5" in page      # coming up: 12 minutes in, joinable at about 5


def test_when_the_log_gives_no_answer_the_page_is_exactly_what_it_was(env):
    """No READY line (an older VM, or the first snapshot not there yet): the timer, and its hint, stay."""
    client, vm, _ = env
    login(client, FRIEND)
    boot(vm, "valheim", minutes_up=2)
    snapshot(vm)                                             # a snapshot without a readiness line
    page = client.get("/games/valheim").data.decode()
    assert _lamp(page) == "boot" and "<b>2</b> / ~5" in page
    vm.serial[2] = ""                                        # no snapshot at all yet
    assert "<b>2</b> / ~5" in client.get("/games/valheim").data.decode()
    boot(vm, "valheim", minutes_up=9)
    assert _lamp(client.get("/games/valheim").data.decode()) == "on"


# ── the Games list ────────────────────────────────────────────────────────────

def test_the_games_list_still_never_reads_the_vm_and_hands_the_pill_to_the_live_poll(env):
    client, vm, _ = env
    login(client, FRIEND)
    boot(vm, "valheim", minutes_up=1)
    snapshot(vm, "READY valheim=1")
    page = client.get("/games").data.decode()
    assert vm.serial_reads == []                                             # the page render reads no serial output
    assert page.count("data-live=") == 1 and 'data-live="/games/valheim/live"' in page   # only the game that's up polls
    assert ".unit[data-live]" in page and "location.reload" not in page
    assert 'data-s="boot" data-tel="tile:game:valheim"' in page              # first paint: the timer's guess, until the poll corrects it


def test_the_games_list_first_paint_is_the_timer_and_a_sleeping_server_polls_nothing(env):
    client, vm, _ = env
    login(client, FRIEND)
    boot(vm, "valheim", minutes_up=12)
    assert 'data-s="on" data-tel="tile:game:valheim"' in client.get("/games").data.decode()
    vm.state.status = "TERMINATED"
    page = client.get("/games").data.decode()
    assert "data-live=" not in page and ".unit[data-live]" not in page       # nothing is up, so no poll and no script
    assert vm.serial_reads == []


# ── chat and ops ──────────────────────────────────────────────────────────────

def joinable_now(client, skippy, email=ADMIN):
    login(client, email)
    client.post("/chat/send", json={"message": "is it up?"})
    return json.loads(skippy.run_tool("server_status", {}))["games"]["valheim"]["joinable_now"]


def test_chat_says_joinable_from_the_log_and_falls_back_to_the_timer(env):
    client, vm, skippy = env
    boot(vm, "valheim", minutes_up=1)
    snapshot(vm, "READY valheim=1")
    assert joinable_now(client, skippy) is True                # log: ready, a minute in
    boot(vm, "valheim", minutes_up=12)
    snapshot(vm, "READY valheim=0")
    assert joinable_now(client, skippy) is False               # log: still starting, twelve minutes in
    snapshot(vm)
    assert joinable_now(client, skippy) is True                # no answer from the log: the timer says up
    boot(vm, "valheim", minutes_up=1)
    snapshot(vm)
    assert joinable_now(client, skippy) is False               # ...and here the timer says starting


def test_ops_status_reports_which_games_are_ready(env):
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    sys.path.insert(0, scripts)
    try:
        import remote
    finally:
        sys.path.remove(scripts)
    _, vm, _ = env
    assert remote.status(vm)["ready"] == {}                    # asleep: nothing is joinable
    boot(vm, "valheim", minutes_up=3)
    snapshot(vm, "READY valheim=1")
    assert remote.status(vm)["ready"] == {"valheim": True}
