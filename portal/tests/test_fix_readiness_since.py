"""The 45-minute give-up is measured from when the GAME started saying "starting", not from the VM's boot.

readiness.py explains why a boot marker resets the state: `docker restart` (ops restart-game), a crash, and a game's own
updater or daily restart keep the same container. But the old limit (OVERDUE_MINUTES) was compared with the VM's uptime,
so any restart later than 45 minutes into a session had its "starting" answer thrown away and the page said online and
joinable while the server was down: the very case the reset was written for.

Now diag.sh reads the log with `docker logs -t` and writes `READY <game>=0 since=<time>`, the time of the boot line that
opened the current run of "starting"; the portal measures the limit from it, and falls back to the VM's uptime only when
a line has no time (a container that isn't running, an older line).
"""
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import diagnostics  # noqa: E402
import readiness  # noqa: E402
from catalog import GAMES  # noqa: E402
from test_fix_readiness_snapshot import Serial, snap  # noqa: E402
from test_readiness_diag import (FIXTURES, REAL, block, block_script, marker_prefixes, needs_bash,  # noqa: E402
                                 run_block_stdout, write_fake_files)
from test_readiness_page import ADMIN, SETTINGS, FakeSkippy, FakeVm, boot, joinable_now, login  # noqa: E402

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


def stamp(when: datetime, nanos: int = 123456789) -> str:
    """A time stamp as `docker logs -t` prints it."""
    return f"{when:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"


def stamped(log: str, first: datetime = datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)) -> str:
    """A fixture (captured without Docker's time stamps) with them put back: line i gets first + 2 s * i and its own nanoseconds."""
    return "".join(f"{stamp(first + timedelta(seconds=2 * i), (i * 7919) % 10 ** 9)} {line}\n" for i, line in enumerate(log.splitlines()))


def ready_lines(stdout: str) -> dict[str, tuple[str, str | None]]:
    """{game: (0|1, since or None)} from a block's output."""
    return {g: (v, s or None) for g, v, s in re.findall(r"^READY (\w+)=([01])(?: since=(\S+))?$", stdout, re.M)}


# ── the rule ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("game,ready,up,starting_min,expected", [
    # a restart hours into a session: the log says "starting" since a minute ago, however old the VM is
    ("valheim", False, 120, 1, True), ("factorio", False, 200, 2, True), ("valheim", False, 120, 44, True),
    # ...but not for ever: 45 minutes in "starting" and the marker has stopped matching (or the game is stuck)
    ("valheim", False, 120, 45, False), ("valheim", False, 120, 500, False),
    # a VM up 50 minutes whose game restarted 30 minutes ago is still starting (the old rule: uptime >= 45, so "online")
    ("valheim", False, 50, 30, True),
    # no time on the line (a container that isn't running, an older line): the VM's uptime, exactly as before
    ("valheim", False, 120, None, False), ("valheim", False, 44, None, True), ("valheim", False, None, None, True),
    # the game's own minutes count even when the VM's uptime isn't known
    ("valheim", False, None, 3, True), ("valheim", False, None, 60, False),
    # the log saying joinable, or nothing at all, is untouched by how long a run of "starting" was
    ("valheim", True, 120, 2, False), ("valheim", None, 120, 2, False), ("valheim", None, 3, 2, True),
    # an UNVERIFIED marker still may only say "ready sooner than the timer", never hold "starting" longer
    ("dragonwilds", False, 120, 2, False), ("satisfactory", False, 30, 1, False),
    ("dragonwilds", False, 3, 2, True), ("everquest", False, 1, None, True),
])
def test_the_give_up_limit_counts_the_time_the_game_has_said_starting(game, ready, up, starting_min, expected):
    assert readiness.is_starting(game, ready, up, GAMES[game].ready_minutes, starting_min) is expected


def test_minutes_since_counts_whole_minutes_and_never_goes_negative():
    now = datetime(2026, 9, 30, 8, 0, 0, tzinfo=UTC)
    assert readiness.minutes_since(None, now) is None
    assert readiness.minutes_since(now - timedelta(minutes=2, seconds=59), now) == 2
    assert readiness.minutes_since(now - timedelta(seconds=10), now) == 0
    assert readiness.minutes_since(now + timedelta(minutes=5), now) == 0            # a clock a little ahead of ours


def test_starting_since_is_when_the_current_run_of_starting_began():
    """The reference the awk in diag.sh is held to (and the rule in words): the time on the line that opened the run."""
    t = [stamp(datetime(2026, 9, 30, 6, 0, s, tzinfo=UTC), s) for s in range(0, 60, 10)]
    log = f"{t[0]} supervisord started with pid 26\n{t[1]} steamcmd: updating\n{t[2]} valheim-server INFO - Running Valheim Server\n"
    assert readiness.starting_since("valheim", log) == t[0]      # the second boot line is the same run: it doesn't restart the clock
    log += f"{t[3]} Opened Steam server\n"
    assert readiness.starting_since("valheim", log) is None      # joinable: nothing is starting
    log += f"{t[4]} valheim-server INFO - Running Valheim Server\n"
    assert readiness.starting_since("valheim", log) == t[4]      # a restart of the game inside the same container: a new run
    assert readiness.starting_since("valheim", "") is None and readiness.starting_since("valheim", "nothing here\n") is None
    assert readiness.starting_since("nope", log) is None
    assert readiness.starting_since("valheim", "supervisord started with pid 26\n") is None       # a log without time stamps
    assert readiness.starting_since("valheim", "=== supervisord started with pid 26\n") is None    # `===` is not a time
    both = f"{t[0]} supervisord started with pid 26\n{t[1]} Opened Steam server and supervisord started with pid 27\n"
    assert readiness.starting_since("valheim", both) is None     # a line that is both counts as ready, as in state()


def test_the_time_stamp_pattern_is_plain_text_that_awk_and_python_read_alike():
    assert "\\" not in readiness.STAMP and "{" not in readiness.STAMP
    for good in ("2026-09-30T06:49:16.123456789Z", "2026-09-30T06:49:16Z", "2026-09-30T06:49:16+00:00"):
        assert re.search(readiness.STAMP, good), good
    for bad in ("", "===", "skippy-games", "2026-09-30", "26-09-30T06:49:16"):
        assert not re.search(readiness.STAMP, bad), bad


# ── the block on the VM writes it ─────────────────────────────────────────────

def test_the_block_carries_the_stamp_pattern_of_readiness_py_and_reads_the_log_with_time_stamps():
    text = block()
    assert readiness.STAMP in text, "diag.sh's copy of the time stamp pattern differs from readiness.py's"
    calls = re.findall(r"docker logs[^|\n]*\|\s*scan_ready", text)
    assert calls, "the block reads a container's log through scan_ready"
    for call in calls:
        assert re.search(r"docker logs (-t|--timestamps) ", call), f"the container logs are read without -t: {call}"
    assert "since=" in text


@needs_bash
@pytest.mark.parametrize("game,name", REAL)
def test_the_block_says_when_the_run_of_starting_began_at_every_marker_of_every_real_boot(tmp_path, game, name):
    """Against the real logs, with Docker's time stamps put back: the bash/awk on the VM and readiness.py agree on the
    state AND on the time, at every moment diag.sh could have run."""
    seen = set()
    for cut, n in marker_prefixes(stamped((FIXTURES / name).read_text(encoding="utf-8")), game):
        got = ready_lines(run_block_stdout(tmp_path, container=game, log=cut)).get(game)
        state, since = readiness.state(game, cut), readiness.starting_since(game, cut)
        assert got == ({readiness.READY: ("1", None), readiness.STARTING: ("0", since), readiness.UNKNOWN: None}[state]), \
            f"{name} cut at line {n}: {got} vs {state}, {since}"
        seen.add(got)
    assert any(v == "0" and s for v, s in filter(None, seen)), "a real boot passes through 'starting' with a time on it"
    assert any(v == "1" and s is None for v, s in filter(None, seen))


@needs_bash
def test_a_restart_inside_the_same_container_moves_the_time_to_the_new_run(tmp_path):
    """valheim-restarted-container.txt is one container with three runs: a first boot, a `docker restart`, and the game
    process killed and respawned. Cut at every boot line that follows a ready line: the run of "starting" began there."""
    lines = stamped((FIXTURES / "valheim-restarted-container.txt").read_text(encoding="utf-8")).splitlines()
    boot_pattern = re.compile(readiness.MARKERS["valheim"].boot_pattern)
    restarts = [i for i, ln in enumerate(lines)
                if boot_pattern.search(ln) and readiness.state("valheim", "\n".join(lines[:i]) + "\n") == readiness.READY]
    assert len(restarts) == 2, "the docker restart and the respawn"
    for i in restarts:
        got = ready_lines(run_block_stdout(tmp_path, log="\n".join(lines[:i + 1]) + "\n"))["valheim"]
        assert got == ("0", lines[i].split()[0])                     # the time of the RESTART's boot line, not the first boot's


@needs_bash
def test_no_time_where_there_is_none_to_give(tmp_path):
    fixture = (FIXTURES / "valheim-crossplay-boot.txt").read_text(encoding="utf-8")
    starting, _ = marker_prefixes(fixture, "valheim")[0]                                         # up to the first boot line
    assert readiness.state("valheim", starting) == readiness.STARTING
    assert ready_lines(run_block_stdout(tmp_path, log=starting)) == {"valheim": ("0", None)}      # a log without time stamps
    ready_log = stamped(fixture)
    for state in ("exited", "restarting", "created", "paused", "dead"):                          # a container that isn't running
        assert ready_lines(run_block_stdout(tmp_path, log=ready_log, state=state)) == {"valheim": ("0", None)}, state
    booting = 'skippy-games google_metadata_script_runner[1318]: Metadata key("startup-script"): === skippy-games boot 2026-09-30T06:44:46+00:00 ===\n'
    assert ready_lines(run_block_stdout(tmp_path, container="none", games="everquest", journal=booting)) == {"everquest": ("0", None)}
    # ...and the plain ones a `journalctl -o cat` prints ("=== skippy-games boot ..."): its first word is not a time
    assert ready_lines(run_block_stdout(tmp_path, container="none", games="everquest",
                                        journal="=== skippy-games boot 2026-09-30T06:44:46+00:00 ===\n")) == {"everquest": ("0", None)}


def _docker_has(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


MAWK_IMAGE = "factoriotools/factorio:stable"     # Debian with mawk as awk: what the games VM's diag.sh really runs under
needs_mawk = pytest.mark.skipif(not _docker_has(MAWK_IMAGE), reason=f"needs Docker with {MAWK_IMAGE} already pulled (mawk)")


@needs_mawk
@pytest.mark.parametrize("game,name", REAL)
def test_the_block_under_mawk_says_the_same(tmp_path, game, name):
    """The VM's awk is mawk, this machine's may be gawk: run the block itself inside a mawk image on the real logs."""
    log = stamped((FIXTURES / name).read_text(encoding="utf-8"))
    cuts = marker_prefixes(log, game)
    picks = [cuts[0], cuts[len(cuts) // 2], cuts[-1]]
    container = f"rv-fix-mawk-{os.getpid()}"
    try:
        for cut, n in picks:
            write_fake_files(tmp_path, cut)
            done = subprocess.run(
                ["docker", "run", "--rm", "-i", "--name", container, "-v", f"{tmp_path}:/t:ro",
                 "-e", f"FAKE_CONTAINER={game}", "-e", "FAKE_STATE=running", "-e", "FAKE_LOG=/t/log",
                 "-e", "FAKE_LOG_PLAIN=/t/log.plain", "-e", "FAKE_JOURNAL=/t/journal", "-e", "FAKE_GAMES=",
                 "--entrypoint", "bash", MAWK_IMAGE, "-c", block_script()],
                capture_output=True, text=True, timeout=120)
            assert done.returncode == 0, done.stderr
            state, since = readiness.state(game, cut), readiness.starting_since(game, cut)
            want = {readiness.READY: ("1", None), readiness.STARTING: ("0", since), readiness.UNKNOWN: None}[state]
            assert ready_lines(done.stdout).get(game) == want, f"{name} cut at line {n}"
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=60)


# ── the portal reads it ───────────────────────────────────────────────────────

def test_ready_since_reads_the_time_and_only_for_a_game_that_is_starting():
    booted = datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)
    snapshot = snap(booted + timedelta(minutes=90), "READY valheim=0 since=2026-09-30T07:29:12.123456789Z",
                    "READY factorio=1", "READY dragonwilds=0", "READY satisfactory=0 since=2026-09-30T07:00:00+00:00")
    assert diagnostics.ready_since(snapshot, booted) == {"valheim": datetime(2026, 9, 30, 7, 29, 12, 123456, tzinfo=UTC),
                                                         "satisfactory": datetime(2026, 9, 30, 7, 0, 0, tzinfo=UTC)}
    assert diagnostics.ready_states(snapshot, booted) == {"valheim": False, "factorio": True, "dragonwilds": False, "satisfactory": False}
    for junk in ("since=", "since=garbage", "since=2026-13-45T99:99:99Z", "since=2026-09-30T07:29:12.Z", "since=07:29"):
        junk_snapshot = snap(booted + timedelta(minutes=90), f"READY valheim=0 {junk}")
        assert diagnostics.ready_since(junk_snapshot, booted) == {}, junk                     # no time: the uptime rule, as before
        assert diagnostics.ready_states(junk_snapshot, booted) == {"valheim": False}, junk    # ...but the answer itself stands
    assert diagnostics.ready_since("READY valheim=0 since=2026-09-30T07:29:12Z\n", booted) == {}   # no header: can't prove it's fresh
    old = snap(booted - timedelta(hours=1), "READY valheim=0 since=2026-09-30T04:29:12Z")            # the previous session's
    assert diagnostics.ready_since(old, booted) == {} and diagnostics.ready_states(old, booted) == {}
    assert set(diagnostics.ready_since(old)) == {"valheim"}                                          # boot time unknown: nothing to compare


def test_a_line_with_or_without_a_time_is_the_same_answer_for_ready_states_and_the_game_view():
    a = snap(datetime(2026, 9, 30, 6, 1, tzinfo=UTC), "READY valheim=0", "READY factorio=0 since=2026-09-30T06:00:30.5Z")
    assert diagnostics.ready_states(a) == {"valheim": False, "factorio": False}
    assert "READY factorio=0 since=2026-09-30T06:00:30.5Z" in diagnostics.for_game(a, "factorio")
    assert "READY valheim=0" not in diagnostics.for_game(a, "factorio")


def test_live_all_hands_over_when_each_starting_game_began_starting():
    booted = datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)
    port2 = snap(booted + timedelta(minutes=90), "PLAYERS valheim=0", "READY valheim=0 since=2026-09-30T07:29:12Z")
    codes, players, ready, since = diagnostics.live_all(Serial(port2), booted)
    assert (codes, players, ready) == ({}, {"valheim": 0}, {"valheim": False})
    assert since == {"valheim": datetime(2026, 9, 30, 7, 29, 12, tzinfo=UTC)}

    class Broken:
        def serial_output(self, port):
            raise RuntimeError("serial port unavailable")

    assert diagnostics.live_all(Broken(), booted) == ({}, {}, {}, {})


# ── the pages ─────────────────────────────────────────────────────────────────

@pytest.fixture
def env():
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy)
    return app.test_client(), vm, skippy


def restart_snapshot(vm, game, since_minutes_ago):
    """A fresh snapshot of this boot saying `game` is starting, since `since_minutes_ago` minutes ago (None: no time)."""
    now = datetime.now(UTC)
    tail = "" if since_minutes_ago is None else f" since={stamp(now - timedelta(minutes=since_minutes_ago, seconds=20))}"
    vm.serial[2] = snap(now - timedelta(seconds=45), f"READY {game}=0{tail}")


# (game, VM uptime, minutes the game has said "starting" (None: no time on the line), still starting?)
RESTARTS = [
    ("valheim", 120, 2, True),         # the finding: restart-game / the daily restart two hours into a session
    ("factorio", 90, 3, True),
    ("valheim", 50, 30, True),         # a VM up 50 min, its game restarted 30 min ago
    ("valheim", 120, 46, False),       # 46 minutes of "starting": the marker has stopped matching; the timer (online) decides
    ("valheim", 120, None, False),     # a line with no time: the VM's uptime, as before
    ("valheim", 12, None, True),       # ...and a slow first boot, as before
    ("valheim", 3, 2, True),           # the ordinary boot: nothing changes
    ("dragonwilds", 120, 2, False),    # UNVERIFIED markers can't hold "starting" past the timer
    ("dragonwilds", 3, 2, True),
]


@pytest.mark.parametrize("game,up,since_min,starting", RESTARTS)
def test_a_restart_late_in_a_session_says_starting_on_the_page_and_the_poll(env, game, up, since_min, starting):
    client, vm, _ = env
    login(client, ADMIN)
    boot(vm, game, up)
    restart_snapshot(vm, game, since_min)
    got = client.get(f"/games/{game}/live").get_json()
    assert got["on"] is True and got["starting"] is starting and got["ready"] is False, got
    page = client.get(f"/games/{game}").data.decode()
    assert f"starting: {'true' if starting else 'false'}}};" in page, "the first paint and the poll must agree or the page reloads in a loop"


def test_chat_and_ops_status_know_the_game_is_restarting_too(env):
    client, vm, skippy = env
    boot(vm, "valheim", 120)
    restart_snapshot(vm, "valheim", 2)
    assert joinable_now(client, skippy) is False                    # it was "True" (online) while the server was down
    restart_snapshot(vm, "valheim", 46)
    assert joinable_now(client, skippy) is True                     # stuck for 46 minutes: back to the timer
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        import remote
    finally:
        sys.path.remove(scripts)
    restart_snapshot(vm, "valheim", 2)
    assert remote.status(vm)["ready"] == {"valheim": False}


def test_the_docs_say_how_the_limit_is_measured():
    doc = (ROOT / "docs" / "games" / "README.md").read_text(encoding="utf-8")
    readiness_section = doc[doc.index("### Readiness"):doc.index("### Backups")]
    assert "since=" in readiness_section and "45 minutes" in readiness_section
    assert "docker logs -t" in readiness_section
