"""Is a game server really joinable? Answered from the game's own log, not from a timer.

catalog.Game.ready_minutes is a guess ("about 5 minutes after boot"). A first boot that downloads the server, or a big
world, blows through it; a quick boot is over long before it. The truth is in the game's log, so infra/gcp-games/diag.sh
scans each game's container log with the markers below and writes `READY <game>=<0|1>` into the serial snapshot;
diagnostics.ready_states() reads those lines and `is_starting()` decides what the page says. This module is the single
source of truth for the markers and for the rules of how a log becomes a state; diag.sh carries a copy of the same
patterns (bash can't import Python), and portal/tests/test_readiness_diag.py fails when the two drift apart, and runs
diag.sh's own block against the real log fixtures under portal/tests/fixtures/readiness/.

How a log becomes a state (state() below, and the awk in diag.sh, do exactly this):

  Read the log top to bottom. A line matching a `boot` marker means "the game is (re)starting: everything before this
  is history", so the state becomes STARTING. A line matching a `ready` marker means "players can join now", so the
  state becomes READY. The state after the last line is the answer. A log with neither kind of line is UNKNOWN (the
  portal then falls back to the boot timer). If a line matches both, ready wins.

  Why "boot" resets: on the VM every boot removes the containers and creates new ones (startup.sh: `compose down`,
  then `up`), so the log restarts by itself. But `docker restart` (ops `restart-game`), a crash under
  `restart: unless-stopped`, and a game's own updater keep the SAME container, and its log then holds the old run's
  READY line above the new run's start-up. Without the reset the page would say "online" through the whole update.
  (Real example: portal/tests/fixtures/readiness/valheim-restarted-container.txt.)

  A container that isn't running is not joinable whatever its log says; diag.sh writes `READY <game>=0` for it.

  When did it start saying "starting"? diag.sh reads the log with `docker logs -t` (every line starts with Docker's own
  time stamp) and, for a game that is STARTING, adds the time of the boot line that OPENED the current run of starting:
  `READY valheim=0 since=2026-09-30T06:49:16.123456789Z`. (A second boot line inside the same run, like Valheim's
  supervisord and then its game process, doesn't move it: it is one run.) starting_since() below is the reference for
  it. The portal measures OVERDUE_MINUTES from that time, NOT from the VM's boot, so a restart two hours into a session
  is "starting" for its own first 45 minutes just like a boot is. A line with no time (a container that isn't running,
  a log without stamps such as EverQuest's journal) falls back to the VM's uptime, the old rule.

Patterns are POSIX extended regular expressions in the dialect that grep -E, awk (mawk on the VM) and Python's `re`
all read the same way: plain text, `[0-9]`, `.`, `*`, `+`, `|`, and bracket expressions. NO backslashes (awk and `-v`
would eat them: write `[(]` for a literal parenthesis), NO `{n}` (mawk), no groups, no lookarounds.

Every game says whether its markers were `verified`: observed in a real log (the fixtures) with the "starting" lines
that must NOT count seen alongside. The others are UNVERIFIED best knowledge; see docs/games/README.md → Readiness for
how to verify each. The page treats an unverified marker as a hint that can only say "ready sooner than the timer",
never "later" (see is_starting()). Stdlib only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

READY, STARTING, UNKNOWN = "ready", "starting", "unknown"

# If the log has said "starting" this long (since the game's own latest start, see `since=` above), the marker has
# stopped matching what the game prints (an update reworded a line) or the game is stuck. Either way the page stops
# insisting and goes back to the timer, so a stale marker can never leave a joinable game marked "starting" for good.
OVERDUE_MINUTES = 45

# What a `docker logs -t` line starts with: the time stamp of the log line. An ERE with no backslashes, like the
# markers; diag.sh carries a copy (test_readiness_diag.py / test_fix_readiness_since.py keep them the same).
STAMP = "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]"


@dataclass(frozen=True)
class Markers:
    boot: tuple[str, ...]        # the game (re)starts: everything above is history
    ready: tuple[str, ...]       # players can join from here on
    verified: bool               # observed in a real log (fixtures) — False means UNVERIFIED best knowledge
    log: str = "container"       # where diag.sh reads it: 'container' = `docker logs <game>`, 'startup' = the VM's startup-script output
    notes: str = ""              # what was seen, and what must not count (for people; the tests pin it)

    @property
    def boot_pattern(self) -> str:
        return "|".join(self.boot)

    @property
    def ready_pattern(self) -> str:
        return "|".join(self.ready)


MARKERS: dict[str, Markers] = {
    # ── Valheim (ghcr.io/lloesche/valheim-server) ─ VERIFIED: three real boots (fixtures) ────────────────────────────
    # A boot's log, in order (crossplay off; first boot = server download + a fresh world; seconds into the boot):
    #    0  `supervisord started with pid 26`                     <- boot marker (the container's first supervisor line)
    #  ~53  `valheim-server INFO - Running Valheim Server`        <- boot marker (each time the game process starts: also
    #                                                                after the daily 05:10 restart, an update or a crash;
    #                                                                19 s into a boot with the server already on disk)
    #  ~63  `Server is now listening on UDP query port 2457`      <- NOT ready: the image's own probe; the world is still
    #                                                                generating (21 s of "Failed to place all ..." follow)
    #  ~67  `Game server connected`                               <- NOT ready: Steam's login callback. 21 s BEFORE the
    #                                                                world was done on a fresh world, 0.1 s AFTER on a
    #                                                                saved one. (The brief for this feature named it the
    #                                                                marker; the real logs say it can fire too early.)
    #  ~88  `Registering lobby` / `Opened Steam server`           <- READY (crossplay off): ZNet opens its socket only
    #                                                                once the world is loaded, on fresh and saved worlds.
    # Crossplay on (VALHEIM_CROSSPLAY=true) never prints `Opened Steam server`; it says, in order:
    #    `Opened PlayFab server`, `Game server connected` (both too early),
    #    `New session server "Skippy Valheim" that has join code , now 0 player(s)`   <- no digits yet: NOT ready
    #    `Session "Skippy Valheim" registered with join code 551603`                  <- the code exists, but the game
    #    `Retry join-code check 99`, `... 98`                                            is still checking it for ~2 s
    #    `Session "Skippy Valheim" with join code 551603 and IP x.x.x.x:2456 is active with 0 player(s)`   <- READY
    # So the two ready patterns don't need to know which mode the server is in: only one of them ever appears in a boot.
    # Shutdown (`docker stop`/`restart`): `WARN received SIGTERM ...`, `Shutting down Valheim server with PID`, the save,
    # then (a restart) the next boot's first line about a second later: too short a window to need a "down" marker.
    "valheim": Markers(
        boot=("supervisord started with pid", "valheim-server INFO - Running Valheim Server"),
        ready=("Opened Steam server", "join code [0-9]+ and IP .* is active"),
        verified=True,
        notes="crossplay off: `Opened Steam server`; crossplay on: the `... is active` join-code line. "
              "`Game server connected` fires before the world is loaded and does not count.",
    ),

    # ── Factorio (factoriotools/factorio:stable) ─ VERIFIED: three real boots (fixtures), one of them the real VM ────
    # A first boot with a new save runs Factorio TWICE in the one container: `--create` (banner, `Factorio initialised`,
    # `Goodbye`), then `--start-server` (banner again). Both print the banner, so each restarts the state; only the
    # second reaches the ready line:
    #    `Factorio 2.0.77 (build 84539, linux64, headless, space-age)`          <- boot marker (stdout)
    #    `Factorio initialised`                                                 <- NOT ready: the map isn't loaded yet
    #    `Hosting game at IP ADDR:({0.0.0.0:34197})`                            <- NOT ready: socket open, still creating
    #    `updateTick(0) changing state from(CreatingGame) to(InGame)`           <- READY: hosting the loaded map
    #    `... changing state from(InGame) to(DisconnectingScheduled)` etc.      <- shutdown (SIGTERM), ends in `Goodbye`
    # The ready pattern names `CreatingGame` on purpose: it is the transition of a start-up, so a later InGame (say after
    # a save; not observed here) can't be mistaken for one.
    #
    # `Error ... Matching server connection failed: Error when creating server game: Missing token.` is NOT a problem
    # for the Captain's servers, which are joined by IP address (no listing) — it does not mean "not joinable":
    #   - it is the public-listing registration. The image's server-settings.json has `"visibility": {"public": true}`
    #     with empty `username` / `token`, so the game can't publish itself on factorio.com's matching server.
    #   - it comes AFTER the ready line in every boot (1.627 s vs 1.500 s in the fixtures; 2.463 s vs 2.276 s on the VM).
    #   - experiment: with `public: false` the message is gone and the very same
    #     `changing state from(CreatingGame) to(InGame)` line still appears (fixtures/factorio-restarted-container.txt).
    #   NOT tested: a real Factorio client joining (none here). Players joining by IP is how the portal tells them to.
    # So it is not a marker at all (neither boot, ready nor failure). If the Captain ever lists a server publicly,
    # `Missing token` becomes a real "not listed" problem, still not a joinability one.
    "factorio": Markers(
        boot=("Factorio [0-9.]+ [(]build [0-9]+",),
        ready=("changing state from[(]CreatingGame[)] to[(]InGame[)]",),
        verified=True,
        notes="the first `CreatingGame -> InGame`; `Missing token` (public listing) is harmless for joining by IP.",
    ),

    # ── Satisfactory (wolveix/satisfactory-server) ─ UNVERIFIED ─────────────────────────────────────────────────────
    # Too heavy to run for this (8-12 GB of RAM, multi-GB download). Both lines are Unreal Engine's own and appear in
    # every UE dedicated server's log as far as we know: `LogInit: Engine Version: 5.x...` opens each run, and
    # `LogNet: GameNetDriver IpNetDriver_... IpNetDriver listening on port 7777` is printed when the net driver starts
    # listening. NOT confirmed against a Satisfactory log; the game may also want its HTTPS API up (TCP 7777) before
    # Server Manager can add it. To verify: docs/games/README.md → Readiness.
    "satisfactory": Markers(
        boot=("LogInit: Engine Version:",),
        ready=("IpNetDriver listening on port 7777",),
        verified=False,
        notes="UNVERIFIED: Unreal Engine's engine-version line and the net driver's `listening on port 7777`.",
    ),

    # ── RuneScape: Dragonwilds (indifferentbroccoli/runescape-dragonwilds-server-docker) ─ UNVERIFIED ──────────────
    # Also Unreal Engine, so the same start line is assumed. The ready line is the one thing seen in a real VM log
    # (portal/diagnostics.py already reads the invite code from it, tests/test_logic.py has it):
    #    `LogNetSessionSettings: Setting ["JoinCode"] written with key[xz] value[YTKM-EKL1]`
    # i.e. the session exists and its invite code is out. The LINE is real; that it comes at the moment players can join
    # (and not earlier or later than the game's listen socket) is not confirmed. Written without brackets: `.` stands
    # for the `[` and `[^]]` for "a first character of the code" so an empty `value[]` doesn't count.
    "dragonwilds": Markers(
        boot=("LogInit: Engine Version:",),
        ready=("JoinCode.*written with key.*value.[^]]",),
        verified=False,
        notes="UNVERIFIED: the real `Setting [\"JoinCode\"] written` line = the invite code exists; the start line is assumed.",
    ),

    # ── EverQuest (AkkStack, infra/gcp-games/everquest.sh) ─ UNVERIFIED ────────────────────────────────────────────
    # Not a container we can scan: the stack has no single game process whose stdout says "up". The script itself
    # reports its progress into the VM's startup-script output (`log "everquest: ..."`), which diag.sh reads with
    # `journalctl -b`. Ready is its last line, `everquest: online at <ip>:5999 (world <ip>:9000)`, printed right after
    # `spire:launcher start` returns. The boot marker is the startup script's first line of every boot
    # (`=== skippy-games boot <date> ===`, startup.sh), so a boot that is still installing or importing reads "starting"
    # instead of "unknown". `journalctl -b` is per boot, so the reset comes for free.
    # UNVERIFIED: (1) the launcher returning doesn't prove the login server and zones already accept connections;
    # (2) that the startup script's output is in the journal in this form (its serial-console copy is:
    # `... google_metadata_script_runner[..]: Metadata key("startup-script"), command("/bin/bash"): started: factorio`).
    "everquest": Markers(
        boot=("=== skippy-games boot",),
        ready=("everquest: online at",),
        verified=False,
        log="startup",
        notes="UNVERIFIED: everquest.sh's own `everquest: online at ...` line, from the startup script's output.",
    ),
}


def games() -> tuple[str, ...]:
    return tuple(MARKERS)


def verified(game: str) -> bool:
    m = MARKERS.get(game)
    return bool(m and m.verified)


def state(game: str, log: str) -> str:
    """READY / STARTING / UNKNOWN for `game` from its log text (see the module docstring for the rules)."""
    m = MARKERS.get(game)
    if not m or not log:
        return UNKNOWN
    boot, ready = re.compile(m.boot_pattern), re.compile(m.ready_pattern)
    result = UNKNOWN
    for line in log.splitlines():
        if boot.search(line):
            result = STARTING
        if ready.search(line):       # after the boot test on purpose: a line that is both counts as ready
            result = READY
    return result


def starting_since(game: str, log: str) -> str | None:
    """When the current run of STARTING began: the time stamp (the first word) of the boot line that opened it, for a
    `docker logs -t` log. None when the state isn't STARTING or that word isn't a time. The reference for the awk in
    diag.sh (which prints `since=<this>`); a boot line inside a run that is already STARTING doesn't move it."""
    m = MARKERS.get(game)
    if not m or not log:
        return None
    boot, ready, stamp = re.compile(m.boot_pattern), re.compile(m.ready_pattern), re.compile(STAMP)
    result, since = UNKNOWN, ""
    for line in log.splitlines():
        if boot.search(line):
            if result != STARTING:
                words = line.split()
                since = words[0] if words else ""
            result = STARTING
        if ready.search(line):
            result = READY
    return since if result == STARTING and stamp.search(since) else None


def minutes_since(when: datetime | None, now: datetime | None = None) -> int | None:
    """Whole minutes from `when` to now (never negative); None when `when` isn't known."""
    if when is None:
        return None
    return max(0, int(((now or datetime.now(timezone.utc)) - when).total_seconds() // 60))


def is_starting(game: str, ready: bool | None, minutes_up: int | None, ready_minutes: int,
                starting_minutes: int | None = None) -> bool:
    """The page's "still starting?" for a game that is on: the log first, the boot timer only as the fallback.

    `ready` is the snapshot's READY value for the game: True (joinable), False (starting) or None (no READY line: an
    older VM, no snapshot yet, a stale one from before this boot, a log we can't read, or nothing recognisable in it).
    None is exactly the old behaviour, so the "usually joinable after N min" hint is never lost.

    `starting_minutes` is how long the game's log has said "starting" (the `since=` on its READY line), and it is what
    OVERDUE_MINUTES is measured against: a game restarted at hour two is starting for its own first 45 minutes. Without
    it (a line with no time) the VM's uptime stands in, which is right for a boot and the best there is otherwise.
    """
    timer = minutes_up is not None and minutes_up < ready_minutes
    if ready is None:
        return timer
    if ready:
        return False
    if not verified(game):                 # a guessed marker may say "ready sooner", never "starting for longer"
        return timer
    held = starting_minutes if starting_minutes is not None else minutes_up
    if held is not None and held >= OVERDUE_MINUTES:
        return timer                       # the marker has stopped matching, or the game is stuck: don't insist
    return True
