"""Watchdog fix: a player count read from a game's log must not outlive the process that wrote it.

`docker logs` survives `docker restart` (ops `restart-game`), a crash under `restart: unless-stopped` and supervisord
respawning the Valheim server inside the same container, and a killed process writes no "left" line. The watchdog used
to replay the WHOLE log, so a player who was connected when the game died stayed "online" forever, `assess()` ranked
that above every other kind of evidence, and the VM only slept at max-hours (about 8 h).

The tests use the REAL captures under fixtures/readiness (one container, several boots, one of them a SIGKILL that
supervisord respawned) and fixtures/watchdog (the real Valheim Steam connection lines), with the one line real logs
gave no chance to capture, a player who never left, spliced in where the process died.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

import readiness

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "gcp-games"
READY_FIX = Path(__file__).parent / "fixtures" / "readiness"
WATCH_FIX = Path(__file__).parent / "fixtures" / "watchdog"

spec = importlib.util.spec_from_file_location("idle_watchdog_fix_logs", INFRA / "idle_watchdog.py")
iw = importlib.util.module_from_spec(spec)
sys.modules["idle_watchdog_fix_logs"] = iw          # dataclasses need the module registered
spec.loader.exec_module(iw)

VALHEIM_BOOT = re.compile(readiness.MARKERS["valheim"].boot_pattern)
FACTORIO_BOOT = re.compile(readiness.MARKERS["factorio"].boot_pattern)
REAL_STEAM_IN = next(ln for ln in (WATCH_FIX / "valheim_steam_session_real.txt").read_text(encoding="utf-8").splitlines(keepends=True)
                     if "Got connection SteamID" in ln)
JOIN = "2026-09-30 06:52:10 [JOIN] Ann joined the game\n"
LEAVE = "2026-09-30 06:59:30 [LEAVE] Ann left the game\n"


def real(name: str) -> list[str]:
    return (READY_FIX / name).read_text(encoding="utf-8").splitlines(keepends=True)


def boots(lines: list[str], pattern) -> list[int]:
    return [i for i, ln in enumerate(lines) if pattern.search(ln)]


def spliced(lines: list[str], at: int, extra: str) -> str:
    return "".join(lines[:at] + [extra] + lines[at:])


# ── the patterns are the ones readiness.py and diag.sh use ───────────────────

def test_the_boot_patterns_are_the_ones_readiness_and_diag_sh_use():
    # The watchdog is one file fetched from metadata, so it can't import portal/readiness.py: the copy is pinned here.
    assert iw.BOOT["valheim"].pattern == readiness.MARKERS["valheim"].boot_pattern
    assert iw.BOOT["factorio"].pattern == readiness.MARKERS["factorio"].boot_pattern


@pytest.mark.parametrize("game,name", [("valheim", "valheim-restarted-container.txt"),
                                       ("valheim", "valheim-crossplay-boot.txt"),
                                       ("factorio", "factorio-restarted-container.txt"),
                                       ("factorio", "factorio-vm-boot-ops-order.txt")])
def test_the_real_boots_are_found_where_readiness_finds_them(game, name):
    lines = real(name)
    ours = boots(lines, iw.BOOT[game])
    assert ours == boots(lines, re.compile(readiness.MARKERS[game].boot_pattern))
    assert ours, f"no boot marker in the real {name}"


# ── Valheim ──────────────────────────────────────────────────────────────────

def test_a_valheim_player_cut_off_by_a_restart_or_a_kill_is_not_still_online():
    lines = real("valheim-restarted-container.txt")
    marks = boots(lines, VALHEIM_BOOT)
    assert len(marks) >= 4, "the real capture has the first boot, a docker restart and a SIGKILL respawn"
    for later in marks[1:]:                          # connected, then the game process (or the container) restarted
        log = spliced(lines, later, REAL_STEAM_IN)
        assert iw.valheim_players(log) == (0, False), f"stale after the boot marker on line {later + 1}"


def test_the_kill_in_the_real_log_ends_the_session_of_a_player_who_was_connected_then():
    lines = real("valheim-restarted-container.txt")
    killed = next(i for i, ln in enumerate(lines) if " Killed " in ln)            # supervisord's line for the SIGKILL
    assert iw.valheim_players(spliced(lines, killed, REAL_STEAM_IN)) == (0, False)


def test_a_valheim_player_connected_after_the_latest_start_still_counts():
    lines = real("valheim-restarted-container.txt")
    assert iw.valheim_players("".join(lines) + REAL_STEAM_IN) == (1, False)
    two = REAL_STEAM_IN.replace("76561198000000001", "76561198000000002")
    assert iw.valheim_players("".join(lines) + REAL_STEAM_IN + two) == (2, False)
    left = REAL_STEAM_IN.replace("Got connection SteamID", "Closing socket")
    assert iw.valheim_players("".join(lines) + REAL_STEAM_IN + left) == (0, False)


def test_a_valheim_player_who_reconnected_after_the_restart_counts_once():
    lines = real("valheim-restarted-container.txt")
    before = spliced(lines, boots(lines, VALHEIM_BOOT)[-1], REAL_STEAM_IN)
    assert iw.valheim_players(before + REAL_STEAM_IN) == (1, False)


def test_a_crossplay_count_from_before_a_restart_is_forgotten_and_the_silence_is_blind_not_nobody():
    crossplay = real("valheim-crossplay-boot.txt")
    said = next(i for i, ln in enumerate(crossplay) if "is active with 0 player(s)" in ln)
    new_session = next(i for i, ln in enumerate(crossplay) if "New session server" in ln)
    busy = crossplay[said].replace("active with 0 player(s)", "active with 3 player(s)")
    first_boot = "".join(crossplay[:said]) + busy
    assert iw.valheim_players(first_boot) == (3, True)
    # The container restarts and the game has not said anything about its session yet: blind, and never "3".
    assert iw.valheim_players(first_boot + "".join(crossplay[:new_session])) == (None, True)
    # ... then its own first session line (`now 0 player(s)`) and its own "is active" line, both about THIS boot.
    assert iw.valheim_players(first_boot + "".join(crossplay[:said])) == (0, True)
    assert iw.valheim_players(first_boot + "".join(crossplay[:said + 1])) == (0, True)


# ── Factorio ─────────────────────────────────────────────────────────────────

def test_a_factorio_player_who_was_in_when_the_server_restarted_is_not_still_online():
    lines = real("factorio-restarted-container.txt")
    marks = boots(lines, FACTORIO_BOOT)
    assert len(marks) >= 3
    for later in marks[1:]:
        assert iw.factorio_players(spliced(lines, later, JOIN)) == 0, f"stale after the banner on line {later + 1}"


def test_a_factorio_player_who_joined_after_the_latest_start_still_counts():
    lines = real("factorio-restarted-container.txt")
    whole = "".join(lines)
    assert iw.factorio_players(whole + JOIN) == 1
    assert iw.factorio_players(whole + JOIN + LEAVE) == 0
    stale = spliced(lines, boots(lines, FACTORIO_BOOT)[-1], JOIN)                   # Ann died with the old process
    assert iw.factorio_players(stale + JOIN.replace("Ann", "Bob")) == 1              # Bob is the only one here now


def test_the_real_first_boot_of_a_new_save_starts_factorio_twice_and_that_is_fine():
    lines = real("factorio-restarted-container.txt")
    assert len(boots(lines, FACTORIO_BOOT)) >= 3                                     # --create, --start-server, restart
    assert iw.factorio_players("".join(lines)) == 0


# ── whole checks, against a fake VM ──────────────────────────────────────────

def counters_listing():
    rows = ["Chain PREROUTING (policy ACCEPT 0 packets, 0 bytes)",
            "    pkts      bytes target     prot opt in     out     source               destination"]
    for comment in (iw.RULE_ASSURED, iw.RULE_RAW):
        rows.append(f"{0:>8} {0:>8}            17   --  *      *       0.0.0.0/0            0.0.0.0/0"
                    f"            multiport dports {iw.port_spec()} /* {comment} */")
    return "\n".join(rows) + "\n"


class QuietVm:
    """Rules in place, no packets at all, one running game whose log is whatever the test says."""

    def __init__(self, game, log, log_rc=0, iptables_rc=0):
        self.game, self.log, self.log_rc, self.iptables_rc = game, log, log_rc, iptables_rc
        self.calls, self.poweroff = [], False

    def __call__(self, argv, timeout=60, merge=False):
        self.calls.append(list(argv))
        if argv[0] == "iptables":
            if self.iptables_rc:
                return self.iptables_rc, "iptables: Permission denied (you must be root)"
            return (0, counters_listing()) if "-L" in argv else (0, "")
        if argv[:2] == ["docker", "ps"]:
            return 0, self.game + "\n"
        if argv[:2] == ["docker", "logs"]:
            return (self.log_rc, self.log) if self.log_rc == 0 else (self.log_rc, "Error: No such container")
        if argv == ["systemctl", "poweroff"]:
            self.poweroff = True
        return 0, ""


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, "STATE", tmp_path / "state")
    monkeypatch.setattr(iw, "metadata", lambda key, default="": {"idle-minutes": "30", "max-hours": "8"}.get(key, default))
    monkeypatch.setattr(iw, "read_flows", lambda path="/proc/net/nf_conntrack": iw.Flows())
    t = {"up": 0.0, "now": 9_000_000.0}
    monkeypatch.setattr(iw, "uptime_s", lambda: t["up"])

    def tick(vm, minutes=5):
        t["up"] += minutes * 60
        t["now"] += minutes * 60
        return iw.tick(vm, now=t["now"])
    return tick


def test_a_factorio_server_restarted_under_a_player_who_never_came_back_sleeps_at_the_idle_limit(clock):
    lines = real("factorio-restarted-container.txt")
    vm = QuietVm("factorio", spliced(lines, boots(lines, FACTORIO_BOOT)[-1], JOIN))
    for _ in range(5):
        clock(vm)
        assert not vm.poweroff
    clock(vm)
    assert vm.poweroff, "the stale [JOIN] kept the VM awake"


def test_a_valheim_server_killed_under_a_player_who_never_came_back_sleeps_at_the_idle_limit(clock):
    lines = real("valheim-restarted-container.txt")
    killed = next(i for i, ln in enumerate(lines) if " Killed " in ln)
    vm = QuietVm("valheim", spliced(lines, killed, REAL_STEAM_IN))
    for _ in range(6):
        clock(vm)
    assert vm.poweroff


def test_a_player_connected_since_the_latest_start_keeps_the_vm_awake_for_hours(clock):
    lines = real("factorio-restarted-container.txt")
    vm = QuietVm("factorio", "".join(lines) + JOIN)                                 # no packets: the log alone says so
    for _ in range(60):
        clock(vm)
    assert not vm.poweroff
    assert iw.load_state().idle_min == 0


def test_a_valheim_player_connected_since_the_latest_start_keeps_the_vm_awake(clock):
    vm = QuietVm("valheim", "".join(real("valheim-restarted-container.txt")) + REAL_STEAM_IN)
    for _ in range(24):
        clock(vm)
    assert not vm.poweroff


def test_a_log_that_cannot_be_read_never_reads_as_nobody(clock, monkeypatch):
    # docker logs fails AND the packet counters fail AND there is no flow table: nothing at all can be read.
    vm = QuietVm("factorio", "", log_rc=1, iptables_rc=3)
    monkeypatch.setattr(iw, "read_flows", lambda path="/proc/net/nf_conntrack": None)
    for _ in range(40):
        clock(vm)
    assert not vm.poweroff
    assert iw.load_state().idle_min == 0 and iw.load_state().unknown_min >= 200
