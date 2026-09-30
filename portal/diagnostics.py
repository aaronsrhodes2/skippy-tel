"""Server logs for admins, read from the VM's serial ports (gcp.py
serial_output): the startup script's output on the console (port 1) and the
latest skippy-diag snapshot of the game containers (port 2, written every
2 minutes by infra/gcp-games/startup.sh). Secret setting values are masked
before anything is shown. Stdlib only.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from catalog import GAMES

MARK = "=== skippy-diag "
STARTUP_LINES = 80
_STARTUP = re.compile(r"startup-script|google_metadata_script_runner")


def latest_snapshot(port2: str) -> str:
    port2 = port2.replace("\r\n", "\n").replace("\r", "")   # serial ports end lines with CRLF
    start = port2.rfind(MARK)
    if start == -1:
        return ""
    end = port2.find("=== end ===", start)
    return port2[start:] if end == -1 else port2[start:end + len("=== end ===")]


_JOIN_LINE = re.compile(r"^JOIN_CODE (\w+)=(\S+)$", re.M)
_JOIN_LOG = re.compile(r'Setting \["JoinCode"\].*?value\[([^\]]+)\]')


def join_codes(snapshot: str) -> dict[str, str]:
    """Invite codes by game from a snapshot: skippy-diag's JOIN_CODE lines,
    else (older VMs) a JoinCode line still in Dragonwilds' log tail."""
    codes = dict(_JOIN_LINE.findall(snapshot))
    if "dragonwilds" not in codes:
        found = _JOIN_LOG.findall(snapshot)
        if found:
            codes["dragonwilds"] = found[-1]
    return codes


def current_join_codes(vm, booted_at: datetime | None = None) -> dict[str, str]:
    """Live invite codes (≤2 minutes old). Empty when the VM is off, the
    snapshot isn't there yet (or is from before this boot: see current_snapshot),
    or the serial port can't be read."""
    try:
        return join_codes(current_snapshot(vm.serial_output(2), booted_at))
    except Exception:   # noqa: BLE001 — an invite code is a nicety; never break the page
        return {}


def players_online(snapshot: str) -> dict[str, int]:
    """Players currently in each game, from PLAYERS lines skippy-diag writes
    (`PLAYERS dragonwilds=2`). Games whose log format we haven't learned yet
    simply aren't in the result, and the page shows "–"."""
    return {g: int(n) for g, n in re.findall(r"^PLAYERS (\w+)=(\d+)$", snapshot, re.M)}


# >>> readiness (portal/readiness.py explains the markers; infra/gcp-games/diag.sh writes the lines)
_READY_LINE = re.compile(r"^READY (\w+)=([01])(?: since=(\S*))?$", re.M)      # `since=` only on a 0, and only when its log had times
_SNAPSHOT_TIME = re.compile(r"^=== skippy-diag (\S+) ===", re.M)
_LOG_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?")


def snapshot_time(snapshot: str) -> datetime | None:
    """When the snapshot was taken, from its header (`=== skippy-diag 2026-09-30T06:49:16+00:00 ===`, `date -Is`)."""
    found = _SNAPSHOT_TIME.search(snapshot)
    if not found:
        return None
    try:
        taken = datetime.fromisoformat(found.group(1))
    except ValueError:
        return None
    return taken if taken.tzinfo else taken.replace(tzinfo=timezone.utc)


def from_this_boot(snapshot: str, booted_at: datetime | None) -> bool:
    """Was the snapshot taken since the VM's last start? Without `booted_at` there is nothing to compare, so yes; a header
    that can't be read can't be proven fresh (the serial port can keep the previous session's output across a restart)."""
    if booted_at is None:
        return True
    taken = snapshot_time(snapshot)
    return taken is not None and taken >= booted_at


def ready_states(snapshot: str, booted_at: datetime | None = None) -> dict[str, bool]:
    """Is each game joinable, from the `READY <game>=<0|1>` lines skippy-diag writes from the games' own logs.

    A game with no line isn't in the result: the caller falls back to the boot timer. Given `booted_at` (the VM's last
    start), a snapshot taken before it, or one whose header can't be read, describes the PREVIOUS session (the serial
    port can keep its output across a restart, and the first snapshot of a boot lands a minute in) and counts as
    no answer: "ready" from yesterday's boot must never show as ready now."""
    if not from_this_boot(snapshot, booted_at):
        return {}
    return {game: value == "1" for game, value, _ in _READY_LINE.findall(snapshot)}


def log_time(text: str) -> datetime | None:
    """A `docker logs -t` time (`2026-09-30T06:49:16.123456789Z`: nanoseconds, which datetime can't hold) as an aware
    datetime; None when it isn't one."""
    found = _LOG_TIME.fullmatch(text or "")
    if not found:
        return None
    whole, fraction, zone = found.groups()
    try:
        return datetime.fromisoformat(whole + (f".{fraction[:6].ljust(6, '0')}" if fraction else "")
                                      + ("+00:00" if zone in (None, "Z") else zone))
    except ValueError:
        return None


def ready_since(snapshot: str, booted_at: datetime | None = None) -> dict[str, datetime]:
    """When each game that is starting began saying so: the `since=` on its `READY <game>=0` line (readiness.py says what
    that time is). The portal counts its 45-minute give-up from it. A game with no such time isn't in the result (the VM's
    uptime stands in, as before). Same freshness rule as ready_states."""
    if not from_this_boot(snapshot, booted_at):
        return {}
    found: dict[str, datetime] = {}
    for game, value, since in _READY_LINE.findall(snapshot):
        when = log_time(since) if value == "0" else None
        if when:
            found[game] = when
        else:
            found.pop(game, None)         # the last line for a game is the answer, like ready_states
    return found
# <<< readiness


def resources(snapshot: str) -> dict:
    """Memory and world-disk use (%) from a snapshot's `free -m` / `df -h` lines."""
    out = {}
    mem = re.search(r"^Mem:\s+(\d+)\s+(\d+)", snapshot, re.M)
    if mem and int(mem.group(1)):
        out["memory_pct"] = round(100 * int(mem.group(2)) / int(mem.group(1)))
        out["memory_total_gb"] = round(int(mem.group(1)) / 1024, 1)
    disk = re.search(r"^\S+\s+\S+\s+\S+\s+\S+\s+(\d+)%\s+/srv/games/data", snapshot, re.M)
    if disk:
        out["disk_pct"] = int(disk.group(1))
    return out


def live(vm) -> tuple[dict[str, str], dict[str, int]]:
    """(invite codes, players online) from one fresh snapshot read."""
    codes, players, *_ = live_all(vm)
    return codes, players


def live_all(vm, booted_at: datetime | None = None) -> tuple[dict[str, str], dict[str, int], dict[str, bool],
                                                            dict[str, datetime]]:
    """(invite codes, players online, joinable per game, when each starting game began starting) from one fresh
    snapshot read: the newest complete snapshot of this boot (`current_snapshot`), so codes and players are as fresh as
    READY. `booted_at`: see ready_states. The last one is ready_since: readiness.is_starting counts its limit from it."""
    try:
        snap = current_snapshot(vm.serial_output(2), booted_at)
    except Exception:   # noqa: BLE001 — niceties; never break the page
        return {}, {}, {}, {}
    return join_codes(snap), players_online(snap), ready_states(snap, booted_at), ready_since(snap, booted_at)


def current_ready(vm, booted_at: datetime | None = None) -> dict[str, bool]:
    """Which games are joinable right now (≤2 minutes old). Empty when the VM is off, the snapshot isn't there yet or is
    from before this boot, or the serial port can't be read."""
    return live_all(vm, booted_at)[2]


# ── server admin facts: versions, image age, mods (admin panel only) ─────────────────────────────────────────
# infra/gcp-games/game_facts.py prints these into the snapshot under a `--- game facts` header, one line per fact
# per running game (never settings or secrets). portal/admin_panel.py shows them to admins; nothing here is for players.

_FACT_LINE = re.compile(r"^(VERSION|IMAGE|UPDATED|STARTED|MODS|MODUPDATE) ([a-z0-9_-]{1,32})=(.*)$", re.M)
_ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_HEADER_TIME = re.compile(r"^=== skippy-diag (\S+) ===", re.M)


def snapshot_stamp(snapshot: str) -> str:
    """When the snapshot was taken, as the raw `date -Is` text from its header, or "" (snapshot_time() above gives a
    datetime for the readiness check; this one is only shown to the admin as text)."""
    m = _HEADER_TIME.search(snapshot)
    return m.group(1) if m else ""


def parse_mods(value: str) -> list[tuple[str, str]]:
    """`name@version;name@version` -> [(name, version)]. The version is what follows the last '@'."""
    mods = []
    for item in value.split(";"):
        item = item.strip()
        if not item:
            continue
        name, at, version = item.rpartition("@")
        mods.append((name.strip(), version.strip() or "?") if at and name.strip() else (item, "?"))
    return mods


def game_facts(snapshot: str) -> dict[str, dict]:
    """Per game: version, image (tag) + image_built, updated, started, mods ([] = none loaded; missing key = this
    game has no mod list to read), modupdate ({count: n}). The last line of each kind wins."""
    facts: dict[str, dict] = {}
    for kind, game, value in _FACT_LINE.findall(snapshot):
        f = facts.setdefault(game, {})
        value = value.strip()
        if kind == "VERSION":
            f["version"] = value[:160]
        elif kind == "IMAGE":
            ref, _, built = value.rpartition(" ")
            f["image"], f["image_built"] = (ref, built) if ref and _ISO_Z.match(built) else (value, "")
        elif kind == "UPDATED":
            f["updated"] = value if _ISO_Z.match(value) else ""
        elif kind == "STARTED":
            f["started"] = value if _ISO_Z.match(value) else ""
        elif kind == "MODS":
            f["mods"] = parse_mods(value)
        elif kind == "MODUPDATE":
            pairs = (p.partition(":") for p in value.split(","))
            f["modupdate"] = {k: (int(v) if v.isdigit() else v) for k, _, v in pairs if k and v}
    return facts


def latest_complete_snapshot(port2: str) -> str:
    """The newest snapshot that reached its `=== end ===`. The one still being written (the script takes a few seconds)
    has no facts yet, so reading it would blank the admin card for those seconds; falls back to it when it's all there is."""
    port2 = port2.replace("\r\n", "\n").replace("\r", "")
    starts = [m.start() for m in re.finditer(re.escape(MARK), port2)]
    for i in range(len(starts) - 1, -1, -1):
        end = port2.find("=== end ===", starts[i])
        if end != -1 and (i + 1 == len(starts) or end < starts[i + 1]):
            return port2[starts[i]:end + len("=== end ===")]
    return latest_snapshot(port2)


def current_snapshot(port2: str, booted_at: datetime | None = None) -> str:
    """The snapshot the live readings come from (invite codes, players, READY lines, the /me dashboard): the newest
    COMPLETE one of this boot. The one still being written has its header and no readings yet (diag.sh streams them as it
    goes), so reading it would blank them for those seconds and flap the page's state; it is used only when no complete
    snapshot of this boot exists. With `booted_at`, a snapshot from before it is the previous session's and counts for
    nothing, whatever it carries: "" when that is all there is."""
    complete = latest_complete_snapshot(port2)
    if from_this_boot(complete, booted_at):
        return complete
    writing = latest_snapshot(port2)
    return writing if from_this_boot(writing, booted_at) else ""


def current_facts(vm) -> tuple[dict[str, dict], str]:
    """(facts by game, when the snapshot was taken) from the newest complete snapshot: ≤2 minutes old while the server
    runs. Empty when the VM is off, the snapshot isn't there yet, or the serial port can't be read."""
    try:
        snapshot = latest_complete_snapshot(vm.serial_output(2))
    except Exception:   # noqa: BLE001 — a nicety on an admin card; never break the page
        return {}, ""
    return game_facts(snapshot), snapshot_stamp(snapshot)


def for_game(snapshot: str, container: str) -> str:
    """Just one game's part of a snapshot: its `docker ps` row and its log
    section (container names match game ids — games/docker-compose.yml)."""
    if not snapshot:
        return ""
    lines = snapshot.splitlines()
    out = [ln for ln in lines[1:3] if ln.startswith("NAMES")]            # the ps header
    out += [ln for ln in lines if ln.split()[:1] == [container]]         # its ps row
    out += [ln for ln in lines if ln.startswith(f"READY {container}=")]  # and whether its own log says it's joinable
    marker = f"--- {container}: "
    if marker in snapshot:
        section = snapshot.split(marker, 1)[1]
        out.append(marker + section.split("\n--- ", 1)[0].rstrip())
    else:
        out.append(f"(no {container} container in the latest snapshot: not started this boot)")
    return "\n".join(out)


def startup_tail(port1: str, lines: int = STARTUP_LINES) -> str:
    """Startup-script lines from the console, last boot's worth."""
    port1 = port1.replace("\r\n", "\n").replace("\r", "")
    picked = [ln for ln in port1.splitlines() if _STARTUP.search(ln)]
    boots = [i for i, ln in enumerate(picked) if "=== skippy-games boot" in ln]
    if boots:
        picked = picked[boots[-1]:]
    return "\n".join(picked[-lines:])


# Secrets a game container makes up for itself and prints in its own log, so no setting holds them to mask by
# value. Factorio's image generates the RCON password and shows it on the server's command line
# (`--rcon-password <value>`), which `ops tail factorio` would otherwise print into a public-ish CI log.
_SELF_MADE_SECRET = re.compile(r"(rcon[-_ ]?password\W{1,3})(\S+)", re.IGNORECASE)


def redact(text: str, env: dict[str, str]) -> str:
    """Mask every secret setting's value (passwords) wherever it appears, and any RCON password a log prints."""
    for game in GAMES.values():
        for s in game.settings:
            value = env.get(s.key, "")
            if s.secret and len(value) >= 3:
                text = text.replace(value, "••••")
    return _SELF_MADE_SECRET.sub(lambda m: m.group(1) + "••••", text)


def read(vm, game: str | None = None) -> dict[str, str]:
    """Both logs, masked; with `game`, only that game's container. Port 2 is
    empty until the first snapshot lands (about a minute after boot) and on
    VMs booted before this existed."""
    env = vm.status().env
    try:
        snapshot = latest_snapshot(vm.serial_output(2))
    except Exception:   # noqa: BLE001 — port 2 not written yet reads as an error
        snapshot = ""
    if game:
        snapshot = for_game(snapshot, game)
    return {
        "containers": redact(snapshot, env) or
        "No container snapshot yet: it appears about a minute after the server boots "
        "(servers started before this feature need one Restart & update).",
        "startup": redact(startup_tail(vm.serial_output(1)), env) or "(no startup output on the console)",
    }
