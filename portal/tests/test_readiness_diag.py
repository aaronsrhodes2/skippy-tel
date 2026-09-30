"""infra/gcp-games/diag.sh's readiness block and how the portal reads what it writes.

Three things are pinned here: the patterns in diag.sh are the ones in portal/readiness.py (bash can't import Python, so
the copy is checked, not trusted); the block, extracted from diag.sh and run for real against the real log fixtures,
gives the same answer as readiness.state() at every marker of every real boot; and diagnostics.py reads the READY lines
back, never trusting a snapshot from before this boot.
"""
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import diagnostics
import readiness

ROOT = Path(__file__).resolve().parents[2]
DIAG = ROOT / "infra" / "gcp-games" / "diag.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "readiness"

OPEN, CLOSE = "# >>> readiness", "# <<< readiness"
CONTAINER_CALL = re.compile(r"^[ \t]*ready_container (\w+) '([^']*)' '([^']*)'[ \t]*$", re.M)
STARTUP_CALL = re.compile(r"scan_ready (\w+) '([^']*)' '([^']*)'")


def diag_text() -> str:
    return DIAG.read_text(encoding="utf-8")


def block() -> str:
    """The lines of diag.sh between its two readiness marker comments."""
    text = diag_text()
    assert text.count(OPEN) == 1 and text.count(CLOSE) == 1, "diag.sh needs exactly one delimited readiness block"
    return text[text.index(OPEN):text.index(CLOSE) + len(CLOSE)]


# ── the copy in diag.sh is the same as readiness.py ───────────────────────────

def test_diag_sh_carries_exactly_the_markers_of_readiness_py():
    text = block()
    in_diag = {game: (boot, ready) for game, boot, ready in CONTAINER_CALL.findall(text)}
    in_diag.update({game: (boot, ready) for game, boot, ready in STARTUP_CALL.findall(text)})
    assert set(in_diag) == set(readiness.MARKERS), "every game with markers is scanned in diag.sh, and vice versa"
    for game, (boot, ready) in in_diag.items():
        assert boot == readiness.MARKERS[game].boot_pattern, f"{game}: boot pattern differs between diag.sh and readiness.py"
        assert ready == readiness.MARKERS[game].ready_pattern, f"{game}: ready pattern differs between diag.sh and readiness.py"
    from_a_container = {g for g, _, _ in CONTAINER_CALL.findall(text)}
    assert from_a_container == {g for g, m in readiness.MARKERS.items() if m.log == "container"}
    assert {g for g, _, _ in STARTUP_CALL.findall(text)} == {g for g, m in readiness.MARKERS.items() if m.log == "startup"}


def test_the_block_never_puts_a_backslash_or_brace_into_a_pattern_argument():
    for _, boot, ready in [*CONTAINER_CALL.findall(block()), *STARTUP_CALL.findall(block())]:
        for pattern in (boot, ready):
            assert "\\" not in pattern and "{" not in pattern


def test_diag_sh_still_writes_its_existing_lines_and_the_new_section_sits_before_the_udp_listing():
    text = diag_text()
    for existing in ("JOIN_CODE dragonwilds=", "JOIN_CODE valheim=", "--- udp listeners (game ports)", "=== end ==="):
        assert existing in text
    assert text.index(OPEN) < text.index("--- udp listeners")           # inside the same snapshot, before its tail
    assert text.index(CLOSE) < text.index("=== end ===")
    assert 'echo "--- readiness"' in block()                            # its own section, so it isn't glued to a container's


# ── running the real block ────────────────────────────────────────────────────

def _bash_can_run_the_block() -> bool:
    bash = shutil.which("bash")
    if not bash:
        return False
    try:
        out = subprocess.run([bash, "-c", "command -v awk >/dev/null && echo ok"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "ok"


needs_bash = pytest.mark.skipif(not _bash_can_run_the_block(), reason="needs bash and awk")


STAMPED = re.compile(r"^\d{4}-\d\d-\d\dT\S+ ", re.M)


def block_script() -> str:
    """diag.sh's readiness block behind stubs for `docker`, `curl` and `journalctl`, driven by FAKE_* variables: only
    FAKE_CONTAINER exists, in state FAKE_STATE, with FAKE_LOG as its `docker logs -t` (each line may start with Docker's
    time stamp; without -t the stub prints FAKE_LOG_PLAIN, the same log without the stamps, as Docker does); FAKE_GAMES
    is the metadata's game list and FAKE_JOURNAL the startup script's output."""
    return f"""
docker() {{
  case "$1" in
    inspect) [ "${{!#}}" = "$FAKE_CONTAINER" ] && echo "$FAKE_STATE" || return 1 ;;
    logs)    [ "${{!#}}" = "$FAKE_CONTAINER" ] && case " $* " in *" -t "*) cat "$FAKE_LOG" ;; *) cat "$FAKE_LOG_PLAIN" ;; esac ;;
  esac
}}
curl() {{ echo "$FAKE_GAMES"; }}
journalctl() {{ cat "$FAKE_JOURNAL"; }}
{block()}
"""


def write_fake_files(tmp_path, log: str, journal: str = "") -> dict:
    files = {}
    for name, content in (("log", log), ("log.plain", STAMPED.sub("", log)), ("journal", journal)):
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files[name] = path
    return files


def run_block_stdout(tmp_path, *, container="valheim", state="running", log="", games="", journal=""):
    """Run diag.sh's readiness block (block_script) with bash on this machine. Returns everything it printed."""
    files = write_fake_files(tmp_path, log, journal)
    env = {**os.environ, "FAKE_CONTAINER": container, "FAKE_STATE": state, "FAKE_LOG": files["log"].as_posix(),
           "FAKE_LOG_PLAIN": files["log.plain"].as_posix(), "FAKE_JOURNAL": files["journal"].as_posix(), "FAKE_GAMES": games}
    done = subprocess.run([shutil.which("bash"), "-c", block_script()], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == "--- readiness", done.stdout
    return done.stdout


def run_block(tmp_path, **kwargs):
    """`READY <game>=<0|1>` from the block, by game (a `since=` time stamp after the value is not part of the answer)."""
    return dict(re.findall(r"^READY (\w+)=([01])(?: since=\S+)?$", run_block_stdout(tmp_path, **kwargs), re.M))


def marker_prefixes(log: str, game: str):
    """The log cut at every marker line (each is a moment diag.sh could have run), plus the whole log."""
    m = readiness.MARKERS[game]
    lines = log.splitlines()
    cuts = [i + 1 for i, ln in enumerate(lines) if re.search(m.boot_pattern, ln) or re.search(m.ready_pattern, ln)]
    return [("\n".join(lines[:n]) + "\n", n) for n in cuts] + [(log, len(lines))]


REAL = [("valheim", "valheim-restarted-container.txt"), ("valheim", "valheim-crossplay-boot.txt"),
        ("factorio", "factorio-restarted-container.txt"), ("factorio", "factorio-vm-boot-ops-order.txt")]
AS_LINE = {readiness.READY: "1", readiness.STARTING: "0", readiness.UNKNOWN: None}


@needs_bash
@pytest.mark.parametrize("game,name", REAL)
def test_the_diag_block_agrees_with_readiness_py_at_every_marker_of_every_real_boot(tmp_path, game, name):
    """The point of the whole exercise: the bash/awk on the VM and the Python the tests pin say the same thing."""
    seen = set()
    for cut, n in marker_prefixes((FIXTURES / name).read_text(encoding="utf-8"), game):
        got = run_block(tmp_path, container=game, log=cut).get(game)
        assert got == AS_LINE[readiness.state(game, cut)], f"{name} cut at line {n}"
        seen.add(got)
    assert {"0", "1"} <= seen, "the real boot passes through both starting and ready"


@needs_bash
def test_a_container_that_is_not_running_is_not_joinable_whatever_its_log_says(tmp_path):
    ready_log = (FIXTURES / "valheim-crossplay-boot.txt").read_text(encoding="utf-8")       # ends with the shutdown, after ready
    assert readiness.state("valheim", ready_log) == readiness.READY
    assert run_block(tmp_path, log=ready_log, state="running") == {"valheim": "1"}
    for state in ("exited", "restarting", "created", "paused", "dead"):
        assert run_block(tmp_path, log=ready_log, state=state) == {"valheim": "0"}, state


@needs_bash
def test_no_line_when_there_is_nothing_to_say(tmp_path):
    assert run_block(tmp_path, container="nothing-of-ours", log="anything\n") == {}          # no such game container this boot
    assert run_block(tmp_path, log="") == {}                                                 # a running container, an empty log
    assert run_block(tmp_path, log="steamcmd: updating 42%\nsome other line\n") == {}        # nothing recognisable: the timer decides


@needs_bash
def test_every_game_container_is_scanned_by_its_own_markers(tmp_path):
    lines = "\n".join([
        "[2026.09.29-02.40.00:001][  0]LogInit: Engine Version: 5.4.0",                      # synthetic UE lines (UNVERIFIED games)
        '[2026.09.29-02.43.11:120][ 51]LogNetSessionSettings: Setting ["JoinCode"] written with key[xz] value[YTKM-EKL1]',
    ]) + "\n"
    assert run_block(tmp_path, container="dragonwilds", log=lines) == {"dragonwilds": "1"}
    assert run_block(tmp_path, container="dragonwilds", log=lines.splitlines()[0] + "\n") == {"dragonwilds": "0"}
    listening = "[..]LogNet: GameNetDriver IpNetDriver_2147482622 IpNetDriver listening on port 7777\n"
    assert run_block(tmp_path, container="satisfactory", log="LogInit: Engine Version: 5.3\n" + listening) == {"satisfactory": "1"}


@needs_bash
def test_everquest_is_read_from_the_startup_output_and_only_when_it_is_one_of_this_boots_games(tmp_path):
    pre = 'skippy-games google_metadata_script_runner[1318]: Metadata key("startup-script"), command("/bin/bash"): '
    booting = f"2026-09-30T06:44:46+00:00 {pre}=== skippy-games boot 2026-09-30T06:44:46+00:00 ===\n" \
              f"2026-09-30T06:44:50+00:00 {pre}everquest: first boot: installing (downloads the PEQ world; ~15-20 min)\n"
    online = booting + f"2026-09-30T06:59:01+00:00 {pre}everquest: online at 35.1.2.3:5999 (world 35.1.2.3:9000)\n"
    assert run_block(tmp_path, container="none", games="valheim everquest", journal=booting) == {"everquest": "0"}
    assert run_block(tmp_path, container="none", games="everquest", journal=online) == {"everquest": "1"}
    assert run_block(tmp_path, container="none", games="valheim factorio", journal=online) == {}     # not running EverQuest this boot
    assert run_block(tmp_path, container="none", games="", journal=online) == {}                     # metadata unreadable
    assert run_block(tmp_path, container="none", games="everquest", journal="") == {}                # journal unreadable: the timer decides


# ── the portal reads what the block writes ────────────────────────────────────

def snapshot(taken: str, *lines: str) -> str:
    return f"=== skippy-diag {taken} ===\nNAMES\tSTATUS\tPORTS\nvalheim\tUp 2 minutes\t\n--- readiness\n" + "".join(f"{ln}\n" for ln in lines) \
        + "--- udp listeners (game ports)\n(none)\n=== end ===\n"


def test_ready_lines_are_read_from_the_snapshot():
    snap = snapshot("2026-09-30T06:49:16+00:00", "READY valheim=1", "READY factorio=0")
    assert diagnostics.ready_states(snap) == {"valheim": True, "factorio": False}
    assert diagnostics.ready_states("") == {} and diagnostics.ready_states("=== skippy-diag t ===\nnothing\n") == {}
    assert diagnostics.ready_states(snapshot("2026-09-30T06:49:16+00:00", "READY valheim=2", "READY x=", "ready valheim=1")) == {}


def test_a_snapshot_from_before_this_boot_is_no_answer():
    """The serial port can keep the last session's snapshot, and a boot's first one lands a minute in: yesterday's
    'ready' must never read as ready now."""
    booted = datetime(2026, 9, 30, 6, 44, 47, tzinfo=timezone.utc)
    old = snapshot("2026-09-30T06:40:00+00:00", "READY valheim=1")
    fresh = snapshot("2026-09-30T06:46:03+00:00", "READY valheim=1")
    assert diagnostics.ready_states(old, booted) == {} and diagnostics.ready_states(fresh, booted) == {"valheim": True}
    assert diagnostics.ready_states(old) == {"valheim": True}                                  # boot time unknown: nothing to compare
    assert diagnostics.ready_states(old, booted.replace(minute=30)) == {"valheim": True}
    assert diagnostics.ready_states(fresh, booted + timedelta(minutes=10)) == {}
    assert diagnostics.ready_states("READY valheim=1\n", booted) == {}                         # can't prove it's fresh
    assert diagnostics.ready_states(snapshot("garbage", "READY valheim=1"), booted) == {}
    other_zone = snapshot("2026-09-29T23:46:03-07:00", "READY valheim=1")                       # same instant as `fresh`, other offset
    assert diagnostics.ready_states(other_zone, booted) == {"valheim": True}


def test_the_latest_snapshot_of_a_crlf_serial_port_wins():
    port2 = snapshot("2026-09-30T06:40:00+00:00", "READY valheim=1") + snapshot("2026-09-30T06:46:03+00:00", "READY valheim=0")
    port2 = port2.replace("\n", "\r\n")
    assert diagnostics.ready_states(diagnostics.latest_snapshot(port2)) == {"valheim": False}


def test_one_games_view_of_the_logs_carries_its_ready_line():
    snap = ("=== skippy-diag t ===\nNAMES\tSTATUS\tPORTS\nvalheim\tUp 2 minutes\t\nfactorio\tUp 2 minutes\t\n"
            "--- valheim: last 40 log lines\nnoise\n--- factorio: last 40 log lines\nmore noise\n"
            "--- readiness\nREADY valheim=1\nREADY factorio=0\n=== end ===\n")
    mine = diagnostics.for_game(snap, "valheim")
    assert "READY valheim=1" in mine and "READY factorio=0" not in mine and "more noise" not in mine


def test_live_all_reads_codes_players_and_readiness_from_one_snapshot_and_never_raises():
    snap = snapshot("2026-09-30T06:49:16+00:00", "JOIN_CODE valheim=106969", "PLAYERS valheim=2", "READY valheim=1")

    class Vm:
        def serial_output(self, port):
            assert port == 2
            return snap

    assert diagnostics.live_all(Vm()) == ({"valheim": "106969"}, {"valheim": 2}, {"valheim": True}, {})   # the 4th: when each starting game began starting
    assert diagnostics.live(Vm()) == ({"valheim": "106969"}, {"valheim": 2})
    assert diagnostics.current_ready(Vm()) == {"valheim": True}

    class Broken:
        def serial_output(self, port):
            raise RuntimeError("serial port unavailable")

    assert diagnostics.live_all(Broken()) == ({}, {}, {}, {}) and diagnostics.current_ready(Broken()) == {}
