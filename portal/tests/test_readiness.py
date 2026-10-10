"""portal/readiness.py: markers -> "is this game really joinable?", pinned to REAL game logs.

The fixtures under fixtures/readiness/ are trimmed captures of real containers (see the README there for how each was
made). Valheim and Factorio were run for this feature; their markers are VERIFIED. Satisfactory, Dragonwilds and
EverQuest could not be run here, so their tests below use synthetic or script-derived lines and say so: they pin the
code's behaviour, they do not prove the markers match the games (docs/games/README.md → Readiness says how to check).
"""
import re
from pathlib import Path

import pytest

import readiness
from catalog import GAMES
from readiness import READY, STARTING, UNKNOWN

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "readiness"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def upto(log: str, needle: str, nth: int = 1) -> str:
    """The log up to and including the nth line that contains `needle`: what diag.sh would see at that moment."""
    seen = 0
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if needle in line:
            seen += 1
            if seen == nth:
                return "\n".join(lines[:i + 1]) + "\n"
    raise AssertionError(f"{needle!r} isn't there {nth} time(s) in the fixture")


def count(log: str, needle: str) -> int:
    return sum(needle in line for line in log.splitlines())


# ── the marker table itself ───────────────────────────────────────────────────

def test_every_game_the_vm_can_run_has_markers():
    compose = (ROOT / "games" / "docker-compose.yml").read_text(encoding="utf-8")
    containers = set(re.findall(r"^\s+container_name:\s*(\S+)", compose, re.M))
    assert containers == {"valheim", "factorio", "satisfactory", "dragonwilds", "garrysmod"}
    assert set(readiness.games()) == containers | {"everquest"}            # EverQuest is its own stack (everquest.sh)
    assert set(readiness.games()) == set(GAMES)                            # ...and every game the portal lists


def test_patterns_use_only_the_dialect_grep_awk_and_python_read_alike():
    """No backslashes (awk -v eats them), no {n} (mawk), no groups or lookarounds; a paren is written [(]."""
    for game, m in readiness.MARKERS.items():
        assert m.boot and m.ready, game
        for pattern in (*m.boot, *m.ready):
            assert "\\" not in pattern and "{" not in pattern and "(?" not in pattern, (game, pattern)
            assert "(" not in pattern.replace("[(]", "") and ")" not in pattern.replace("[)]", ""), (game, pattern)
            assert not pattern.startswith("|") and not pattern.endswith("|") and "||" not in pattern, (game, pattern)
            re.compile(pattern)                                             # and Python accepts it
        assert m.boot_pattern == "|".join(m.boot) and m.ready_pattern == "|".join(m.ready)


def test_only_what_was_seen_in_a_real_log_is_marked_verified():
    assert readiness.verified("valheim") and readiness.verified("factorio")
    for guessed in ("satisfactory", "dragonwilds", "everquest", "no-such-game"):
        assert not readiness.verified(guessed), guessed
    for game, m in readiness.MARKERS.items():
        assert ("UNVERIFIED" in m.notes) == (not m.verified), game          # the notes say so in words, too


def test_no_marker_fires_on_an_entrypoint_trace_line():
    """The entrypoints' `set -x` trace goes to stderr; when a tool joins stderr AFTER stdout (ops_agent's `tail` does)
    such lines land out of order, so a marker on one could make a boot look like it happened after the ready line."""
    for name, game in (("factorio-vm-boot-ops-order.txt", "factorio"), ("valheim-restarted-container.txt", "valheim")):
        m = readiness.MARKERS[game]
        for line in fixture(name).splitlines():
            if line.startswith("+"):
                assert not re.search(m.boot_pattern, line) and not re.search(m.ready_pattern, line), line


# ── unknown ───────────────────────────────────────────────────────────────────

def test_a_log_with_no_marker_is_unknown_not_starting():
    assert readiness.state("valheim", "") == UNKNOWN
    assert readiness.state("valheim", "\n\n") == UNKNOWN
    assert readiness.state("factorio", "   2.009 Info UDPSocket.cpp:38: Opening socket\nsome other line\n") == UNKNOWN
    assert readiness.state("no-such-game", fixture("valheim-crossplay-boot.txt")) == UNKNOWN
    assert readiness.state("valheim", fixture("factorio-vm-boot-ops-order.txt")) == UNKNOWN   # another game's log says nothing


def test_the_ready_marker_alone_is_ready_and_the_boot_marker_alone_is_starting():
    """A log that starts mid-way (the first lines rotated out) still answers."""
    log = fixture("valheim-restarted-container.txt")
    only_ready = "\n".join(line for line in log.splitlines() if "Opened Steam server" in line)
    only_boot = "\n".join(line for line in log.splitlines() if "supervisord started" in line)
    assert readiness.state("valheim", only_ready) == READY
    assert readiness.state("valheim", only_boot) == STARTING


def test_a_line_that_is_both_boot_and_ready_counts_as_ready():
    assert readiness.state("valheim", "supervisord started with pid 1 Opened Steam server\n") == READY


# ── Valheim (VERIFIED): the real boots ────────────────────────────────────────

VALHEIM = "valheim-restarted-container.txt"      # boot 1: server download + fresh world, `docker restart`, then a SIGKILL'd game
# The game process starting. (Not just "Running Valheim Server": the updater prints "Running Valheim Server updater ...".)
SERVER_START = "valheim-server INFO - Running Valheim Server"


def test_valheim_fresh_world_is_starting_until_the_world_is_loaded():
    log = fixture(VALHEIM)
    assert readiness.state("valheim", upto(log, "supervisord started with pid")) == STARTING
    assert readiness.state("valheim", upto(log, SERVER_START, 1)) == STARTING
    # Things that look like "up" but must NOT count, in the order the real boot printed them:
    assert readiness.state("valheim", upto(log, "Server is now listening on UDP query port 2457", 1)) == STARTING
    assert readiness.state("valheim", upto(log, "Game server connected", 1)) == STARTING     # 21 s before the world was done
    assert readiness.state("valheim", upto(log, "Registering lobby", 1)) == STARTING
    assert readiness.state("valheim", upto(log, "Opened Steam server", 1)) == READY


def test_valheim_game_server_connected_is_not_the_ready_marker():
    """The brief for this feature named it; the real logs say it fires before the world has loaded (boot 1) and only
    0.1 s after the socket opened on a saved world (boot 2). It's ahead of the true moment in one of the two."""
    log = fixture(VALHEIM)
    lines = log.splitlines()
    connected = [i for i, ln in enumerate(lines) if "Game server connected" in ln]
    opened = [i for i, ln in enumerate(lines) if "Opened Steam server" in ln]
    assert connected[0] < opened[0]                # fresh world: connected first, joinable ~21 s later
    assert connected[1] > opened[1]                # saved world: joinable first
    assert readiness.state("valheim", "\n".join(lines[:connected[0] + 1])) == STARTING


def test_valheim_a_restarted_container_forgets_the_old_runs_ready_line():
    """`docker restart` (ops restart-game) keeps the container, so its log still holds the old run's `Opened Steam
    server` above the new start-up. Without the boot reset the page would say "online" through the whole update."""
    log = fixture(VALHEIM)
    assert readiness.state("valheim", upto(log, "Opened Steam server", 1)) == READY
    assert readiness.state("valheim", upto(log, "received SIGTERM")) == READY                # old run still "joinable" for the second it dies
    assert readiness.state("valheim", upto(log, "supervisord started with pid", 2)) == STARTING
    assert readiness.state("valheim", upto(log, SERVER_START, 2)) == STARTING
    assert readiness.state("valheim", upto(log, "Server is now listening on UDP query port 2457", 2)) == STARTING
    assert readiness.state("valheim", upto(log, "Opened Steam server", 2)) == READY
    # a "did any ready line ever appear" reading has no reset, and gets exactly these moments wrong:
    assert "Opened Steam server" in upto(log, "supervisord started with pid", 2)


def test_valheim_the_game_process_restarting_inside_a_running_container_resets_too():
    """A crash (or the daily 05:10 restart, or an update) restarts only the game; supervisord keeps the container."""
    log = fixture(VALHEIM)
    assert readiness.state("valheim", upto(log, "Killed")) == READY                      # the game was killed while joinable
    assert readiness.state("valheim", upto(log, SERVER_START, 3)) == STARTING
    assert readiness.state("valheim", log) == READY                                        # and it came back
    assert count(log, "supervisord started with pid") == 2                                 # (the container itself started twice)


def test_valheim_crossplay_is_ready_when_the_join_code_is_confirmed_active():
    log = fixture("valheim-crossplay-boot.txt")
    assert "Opened Steam server" not in log                                                # crossplay never prints it
    assert readiness.state("valheim", upto(log, SERVER_START)) == STARTING
    assert readiness.state("valheim", upto(log, "Opened PlayFab server")) == STARTING
    assert readiness.state("valheim", upto(log, "Game server connected")) == STARTING      # too early here as well
    assert readiness.state("valheim", upto(log, "that has join code , now 0 player(s)")) == STARTING   # no digits yet
    assert readiness.state("valheim", upto(log, "registered with join code")) == STARTING  # the code exists, still being checked
    assert readiness.state("valheim", upto(log, "is active with 0 player(s)")) == READY
    assert readiness.state("valheim", log) == READY                                        # the SIGTERM lines after it don't change that


def test_valheim_one_pattern_pair_serves_both_modes():
    """The markers don't need to be told whether crossplay is on: each mode prints only its own ready line."""
    fresh, crossplay = fixture(VALHEIM), fixture("valheim-crossplay-boot.txt")
    m = readiness.MARKERS["valheim"]
    assert re.search(m.ready_pattern, "Opened Steam server") and not re.search(m.ready_pattern, "Opened PlayFab server")
    assert not any(re.search(r"join code [0-9]+ and IP .* is active", ln) for ln in fresh.splitlines())
    assert not any("Opened Steam server" in ln for ln in crossplay.splitlines())


# ── Factorio (VERIFIED): the real boots, and what "Missing token" means ───────

FACTORIO = "factorio-restarted-container.txt"    # boot 1: new save (create + serve), `docker restart` with public listing off


def test_factorio_first_boot_with_a_new_save_runs_the_game_twice_and_is_ready_after_the_second():
    log = fixture(FACTORIO)
    assert count(log, "Factorio 2.0.77 (build") == 3                                       # create, serve, then the restart's serve
    assert readiness.state("factorio", upto(log, "Factorio 2.0.77 (build", 1)) == STARTING  # the `--create` run
    assert readiness.state("factorio", upto(log, "Factorio initialised", 1)) == STARTING
    assert readiness.state("factorio", upto(log, "Goodbye", 1)) == STARTING                # the create run ended; nothing is serving
    assert readiness.state("factorio", upto(log, "Factorio 2.0.77 (build", 2)) == STARTING
    assert readiness.state("factorio", upto(log, "Factorio initialised", 2)) == STARTING   # map not loaded yet
    assert readiness.state("factorio", upto(log, "Hosting game at", 1)) == STARTING        # socket open, game still being created
    assert readiness.state("factorio", upto(log, "changing state from(CreatingGame) to(InGame)", 1)) == READY


def test_factorio_missing_token_is_harmless_for_joining_by_ip():
    """`Matching server connection failed: ... Missing token.` is the public-listing registration failing (the image's
    server-settings.json has visibility.public=true and no factorio.com credentials). Evidence in the fixtures:
    it comes AFTER the ready line, and once public listing is off the message is gone while the same ready line still
    appears. So it is neither a marker nor a reason to hold the state back."""
    log = fixture(FACTORIO)
    lines = log.splitlines()
    token = [i for i, ln in enumerate(lines) if "Missing token" in ln]
    ready = [i for i, ln in enumerate(lines) if "changing state from(CreatingGame) to(InGame)" in ln]
    boots = [i for i, ln in enumerate(lines) if "Factorio 2.0.77 (build" in ln]
    assert len(token) == 1 and len(ready) == 2
    assert ready[0] < token[0] < boots[2]                                                   # first boot: ready, THEN the token error
    assert not any("Missing token" in ln for ln in lines[boots[2]:])                        # the public:false boot has none...
    assert readiness.state("factorio", upto(log, "Missing token")) == READY                 # ...and the error doesn't undo ready
    assert readiness.state("factorio", log) == READY                                        # ...which the token-free boot reaches too
    vm = fixture("factorio-vm-boot-ops-order.txt")                                          # the real VM boot has it as well
    assert "Missing token" in vm and readiness.state("factorio", vm) == READY


def test_factorio_a_restarted_container_resets_at_its_new_banner():
    log = fixture(FACTORIO)
    assert readiness.state("factorio", upto(log, "Received SIGTERM", 1)) == READY           # the old run, on its way down
    assert readiness.state("factorio", upto(log, "Factorio 2.0.77 (build", 3)) == STARTING  # the restart has begun: old ready forgotten
    assert readiness.state("factorio", upto(log, "Hosting game at", 2)) == STARTING
    assert readiness.state("factorio", upto(log, "changing state from(CreatingGame) to(InGame)", 2)) == READY


def test_factorio_real_vm_boot_in_the_ops_agents_stream_order():
    """ops_agent.py's `tail` prints stdout THEN stderr, so the entrypoint's trace comes after Factorio's own lines.
    The markers are all on stdout, so the order in which the two streams are joined can't change the answer."""
    vm = fixture("factorio-vm-boot-ops-order.txt")
    lines = vm.splitlines()
    assert lines.index(next(ln for ln in lines if ln.startswith("+++ readlink"))) > \
        lines.index(next(ln for ln in lines if "changing state from(CreatingGame) to(InGame)" in ln))
    assert readiness.state("factorio", vm) == READY
    assert readiness.state("factorio", upto(vm, "Hosting game at")) == STARTING


# ── UNVERIFIED games: behaviour of the code on synthetic / script-derived lines ─

def test_dragonwilds_join_code_line_means_ready_and_an_empty_code_does_not():
    """The JoinCode line is the one real Dragonwilds line we have (tests/test_logic.py reads the invite code from it).
    That it marks 'joinable' is UNVERIFIED; the engine-version boot line is assumed (Unreal Engine's own)."""
    real = '[2026.09.29-02.43.11:120][ 51]LogNetSessionSettings: Setting ["JoinCode"] written with key[xz] value[YTKM-EKL1]\n'
    blank = '[2026.09.29-02.43.11:120][ 51]LogNetSessionSettings: Setting ["JoinCode"] written with key[xz] value[]\n'
    boot = "[2026.09.29-02.40.00:001][  0]LogInit: Engine Version: 5.4.0-0+++UE5+Release-5.4\n"       # synthetic
    assert readiness.state("dragonwilds", real) == READY
    assert readiness.state("dragonwilds", blank) == UNKNOWN
    assert readiness.state("dragonwilds", boot + real) == READY
    assert readiness.state("dragonwilds", boot + real + boot) == STARTING                                # a restart forgets the old code
    assert readiness.state("dragonwilds", boot + real + boot + blank) == STARTING


def test_satisfactory_net_driver_line_means_ready_synthetic():
    boot = "[2026.09.29-19.38.30:001][  0]LogInit: Engine Version: 5.3.2-CSS+++FactoryGame+rel\n"       # synthetic
    listening = "[2026.09.29-19.38.33:193][  0]LogNet: GameNetDriver IpNetDriver_2147482622 IpNetDriver listening on port 7777\n"
    other_port = listening.replace("7777", "15000")
    assert readiness.state("satisfactory", boot) == STARTING
    assert readiness.state("satisfactory", boot + other_port) == STARTING
    assert readiness.state("satisfactory", boot + listening) == READY
    assert readiness.state("satisfactory", boot + listening + boot) == STARTING


def test_everquest_markers_are_lines_the_scripts_really_print():
    """EverQuest's markers come from the startup script's own output, so they can be pinned to the scripts' text."""
    eq = (ROOT / "infra" / "gcp-games" / "everquest.sh").read_text(encoding="utf-8")
    startup = (ROOT / "infra" / "gcp-games" / "startup.sh").read_text(encoding="utf-8")
    assert 'log() { echo "everquest: $*"; }' in eq and 'log "online at ' in eq            # -> `everquest: online at <ip>:5999 ...`
    assert 'echo "=== skippy-games boot ' in startup
    m = readiness.MARKERS["everquest"]
    assert m.log == "startup" and re.search(m.ready_pattern, "everquest: online at 1.2.3.4:5999 (world 1.2.3.4:9000)")
    assert re.search(m.boot_pattern, "=== skippy-games boot 2026-09-30T06:44:46+00:00 ===")


def test_everquest_startup_output_in_the_real_console_line_format():
    """Line format copied from a real serial-port capture of this VM (ops workflow, `logs`); the everquest lines use
    the same wrapper, their text comes from everquest.sh. UNVERIFIED: that the journal has them the same way."""
    pre = 'google_metadata_script_runner[1318]: Metadata key("startup-script"), command("/bin/bash"): '
    boot = f'2026-09-30T06:44:46.602275+00:00 skippy-games {pre}=== skippy-games boot 2026-09-30T06:44:46+00:00 ===\n'
    install = f"2026-09-30T06:44:50.100000+00:00 skippy-games {pre}everquest: first boot: installing (downloads the PEQ world, quests, maps; ~15-20 min)\n"
    online = f"2026-09-30T06:59:01.000000+00:00 skippy-games {pre}everquest: online at 35.1.2.3:5999 (world 35.1.2.3:9000)\n"
    started = f"2026-09-30T06:59:02.000000+00:00 skippy-games {pre}started: everquest\n"
    assert readiness.state("everquest", "") == UNKNOWN
    assert readiness.state("everquest", boot) == STARTING              # the boot is in progress, the game isn't up yet
    assert readiness.state("everquest", boot + install) == STARTING    # still installing, however long it takes
    assert readiness.state("everquest", boot + install + online + started) == READY
    assert readiness.state("everquest", boot + install + online + boot) == STARTING     # a new boot's log, if a journal ever spans two


# ── how the page decides: the log first, the timer as the fallback ────────────

@pytest.mark.parametrize("game,ready,up,expected", [
    # no READY line (older VM, no snapshot yet, stale one, unreadable log): exactly the old timer
    ("valheim", None, 1, True), ("valheim", None, 5, False), ("valheim", None, 40, False), ("valheim", None, None, False),
    # the log says joinable: not starting, however young the boot (early ready beats the timer)
    ("valheim", True, 0, False), ("valheim", True, 1, False), ("everquest", True, 2, False),
    # the log says not yet, and the marker is verified: starting, however old the boot (a slow first boot)
    ("valheim", False, 12, True), ("valheim", False, 44, True), ("factorio", False, 30, True), ("valheim", False, None, True),
    # ...unless it has said so for so long the marker must have stopped matching: back to the timer
    ("valheim", False, readiness.OVERDUE_MINUTES, False), ("valheim", False, 500, False),
    # an UNVERIFIED marker may only say "ready sooner", never "starting for longer"
    ("satisfactory", False, 12, False), ("dragonwilds", False, 30, False), ("everquest", False, 20, False),
    ("satisfactory", False, 3, True), ("dragonwilds", False, 1, True),
])
def test_is_starting_reads_the_log_first_and_the_timer_only_as_the_fallback(game, ready, up, expected):
    ready_minutes = GAMES[game].ready_minutes
    assert readiness.is_starting(game, ready, up, ready_minutes) is expected
