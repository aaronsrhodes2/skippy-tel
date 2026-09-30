#!/usr/bin/env python3
"""Idle watchdog for the pop-up game VM: powers the VM off when nobody is playing, and after a hard time limit.

Installed as /usr/local/bin/skippy-idle-watchdog by startup.sh (fetched from instance metadata `idle-watchdog` at
every boot) and run every 5 minutes by skippy-idle-watchdog.timer.

    skippy-idle-watchdog            one check: what the timer runs
    skippy-idle-watchdog arm        insert the passive iptables counters (idempotent) so counting starts now
    skippy-idle-watchdog report     the evidence, for skippy-diag (diag.sh); never sleeps the VM

What counts as a player (every number below was observed on real Linux, see portal/tests/test_idle_watchdog.py):

  1. A game's own log, where it gives an exact count: Valheim's crossplay session line, Valheim's Steam
     "Got connection SteamID" / "Closing socket" pairs, Factorio's [JOIN] / [LEAVE].
  2. Packets on ASSURED conntrack flows to the game ports. The kernel sets ASSURED only when a flow was answered
     AND kept going for over 2 seconds: a player's stream, never a scanner's one-shot probe. An iptables rule with
     `-m conntrack --ctstatus ASSURED` counts them over the whole interval (a snapshot of the flow table alone
     would miss a player who left a minute ago); the flow table (/proc/net/nf_conntrack) corroborates a trickle.
  3. Only when neither of those can be read: the raw packet count on the game ports, at a high threshold
     (scanners and server-browser queries are a trickle; a connected player is tens of packets a second).

Evidence that cannot be read is never taken to mean "nobody is here": the idle clock simply does not move. And
whatever the evidence says, the VM stops after `max-hours` (metadata; default 8; 0 = off), the backstop for every
way the evidence could be wrong.

Why the version before this one never slept the VM: it ran `iptables -I -t mangle PREROUTING ...`. iptables reads
the word after -I as the chain, so "-t" was the chain and "mangle" a rule number: "Invalid rule number `mangle'",
exit 2, and `set -e` ended the script before its first count, every 5 minutes, for the life of the VM (reproduced
on iptables 1.8.9, nf_tables and legacy, Debian 12).

Stdlib only: runs on a stock Debian 12 VM (Python 3.11).
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# ── constants ────────────────────────────────────────────────────────────────

INTERVAL_MIN = 5                           # the timer's period (startup.sh)
DEFAULT_IDLE_MIN = 30                      # metadata idle-minutes when it can't be read
DEFAULT_MAX_HOURS = 8.0                    # metadata max-hours when unset or unreadable
ANNOUNCE_AHEAD_S = 15 * 60                 # the Discord heads-up before the backstop

# Thresholds are packets per 5-minute interval, scaled to the interval actually measured.
ASSURED_STRONG = 100      # packets on ASSURED flows that settle it alone (0.33/s; a connected player sends tens/s)
ASSURED_WEAK = 10         # a trickle: counts only while an ASSURED flow is still in the flow table
FALLBACK_PACKETS = 3000   # raw packets, used only when nothing better can be read (10/s; the old 500 was 1.7/s)
# The valheim-server image's own idle check tolerates 30 datagrams in 3 s (10/s) before calling a server busy;
# FALLBACK_PACKETS is that same rate.

# Which port belongs to which game. The union is what the counters and the flow scan watch; every port here must
# be open in main.tf's firewall (a test checks).
GAME_PORTS: dict[str, list[tuple[int, int]]] = {
    "valheim": [(2456, 2457)],
    "factorio": [(34197, 34197)],
    "satisfactory": [(7777, 7777)],
    "dragonwilds": [(7778, 7778)],
    "everquest": [(9000, 9000), (5998, 5999), (7000, 7030)],
}
PROFILES = ("valheim", "factorio", "satisfactory", "dragonwilds")     # compose profiles (games/docker-compose.yml)

STATE = Path(os.environ.get("SKIPPY_STATE_DIR", "/run/skippy-games"))     # tmpfs: resets on every boot
ROOT = "/srv/games"
META = "http://metadata.google.internal/computeMetadata/v1/instance/attributes/"
DISCORD = "/usr/local/bin/skippy-discord"
EVERQUEST = "/usr/local/bin/skippy-everquest"
TAG = "skippy-idle"                        # journald tag: journalctl -t skippy-idle

RULE_RAW = "skippy-games"                  # counts every packet to the game ports
RULE_ASSURED = "skippy-games-assured"      # counts the packets of ASSURED (two-way, sustained) flows only

# Networks whose traffic is never a player on the Internet: containers, the host itself, the VPC.
_LOCAL = [ipaddress.ip_network(n) for n in
          ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
           "::1/128", "fc00::/7", "fe80::/10")]


def port_spec() -> str:
    """The iptables multiport list: '2456:2457,34197,...'."""
    ranges = sorted({r for group in GAME_PORTS.values() for r in group})
    return ",".join(f"{a}:{b}" if a != b else str(a) for a, b in ranges)


def game_of(port: int) -> str | None:
    for game, ranges in GAME_PORTS.items():
        if any(a <= port <= b for a, b in ranges):
            return game
    return None


# ── running programs ─────────────────────────────────────────────────────────

def run(argv: list[str], timeout: float = 60, merge: bool = False) -> tuple[int, str]:
    """(exit status, output). Never raises: a missing program is 127, a hang is 124. `merge` interleaves stderr
    with stdout in the order written (what `docker logs` needs to keep a log in order)."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge else subprocess.PIPE,
                           text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + ("" if merge else (p.stderr or ""))
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out after {timeout:g}s"
    except OSError as exc:
        return 126, f"{argv[0]}: {exc}"


# ── iptables ─────────────────────────────────────────────────────────────────

def rule_spec(comment: str) -> list[str]:
    """One counting rule, everything after the -C/-I option. No target: it only counts. Only packets addressed to
    this host (before Docker's DNAT sends them on to a container), so a container's own outbound traffic to some
    remote port that happens to be on the list is not counted."""
    spec = ["PREROUTING", "-p", "udp", "-m", "addrtype", "--dst-type", "LOCAL",
            "-m", "multiport", "--dports", port_spec()]
    if comment == RULE_ASSURED:
        spec += ["-m", "conntrack", "--ctstatus", "ASSURED"]
    return spec + ["-m", "comment", "--comment", comment]


def iptables_argv(action: str, comment: str) -> list[str]:
    """`iptables -w 5 -t mangle -C|-I PREROUTING ...`. `-t mangle` must come BEFORE the action: iptables takes the
    word after -C/-I as the chain, so `iptables -I -t mangle PREROUTING` is "Invalid rule number `mangle'" (exit 2)."""
    return ["iptables", "-w", "5", "-t", "mangle", action, *rule_spec(comment)]


def ensure_rules(runner=run) -> tuple[list[str], list[str]]:
    """Add whichever counting rule is missing. Returns (rules just added, problems). Counters of a rule that was
    just added have watched nothing yet."""
    added, problems = [], []
    for comment in (RULE_RAW, RULE_ASSURED):
        if runner(iptables_argv("-C", comment))[0] == 0:
            continue
        rc, out = runner(iptables_argv("-I", comment))
        if rc == 0:
            added.append(comment)
        else:
            problems.append(f"iptables could not add the {comment} counter (exit {rc}): {out.strip()[:160]}")
    return added, problems


_COUNTER = re.compile(r"^\s*(\d+)\s+\d+\s.*/\*\s*(skippy-games(?:-assured)?)\s*\*/", re.M)


def parse_counters(text: str) -> dict[str, int]:
    """Packet counts by rule comment from `iptables -t mangle -L PREROUTING -v -x -n`. A missing rule is a missing
    key. (A duplicated rule sees the same packets twice over; the largest count is kept.)"""
    out: dict[str, int] = {}
    for pkts, comment in _COUNTER.findall(text):
        out[comment] = max(out.get(comment, 0), int(pkts))
    return out


def read_counters(runner=run) -> dict[str, int] | None:
    rc, out = runner(["iptables", "-w", "5", "-t", "mangle", "-L", "PREROUTING", "-v", "-x", "-n"])
    return parse_counters(out) if rc == 0 else None


# ── the kernel's flow table ──────────────────────────────────────────────────

@dataclass
class Flows:
    """UDP flows coming in to the game ports, from /proc/net/nf_conntrack."""
    total: int = 0
    assured: int = 0          # answered and sustained: a player's stream
    replied: int = 0          # answered, but not (yet) sustained: a probe that got an answer
    unreplied: int = 0        # never answered: a scanner's probe
    sources: int = 0          # distinct source addresses over all of them
    assured_sources: int = 0
    by_game: dict[str, dict[str, int]] = field(default_factory=dict)


def is_local(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return any(ip in net for net in _LOCAL if net.version == ip.version)


def parse_flow(line: str) -> tuple[str, int, str] | None:
    """(source address, destination port, state) of a UDP line of /proc/net/nf_conntrack, from the ORIGINAL
    direction (the first tuple: who started it, and where to). State: assured | replied | unreplied.

        ipv4 2 udp 17 118 src=A dst=B sport=X dport=2456 [UNREPLIED] src=B dst=A sport=2456 dport=X [ASSURED] ...
    """
    parts = line.split()
    if len(parts) < 6 or parts[2] != "udp":
        return None
    src, dport, seen_src, flags = None, None, 0, set()
    for tok in parts[5:]:
        if tok.startswith("["):
            flags.add(tok.strip("[]"))
            continue
        key, _, value = tok.partition("=")
        if key == "src":
            seen_src += 1
        if seen_src == 1:                       # the original direction only
            if key == "src":
                src = value
            elif key == "dport" and value.isdigit():
                dport = int(value)
    if src is None or dport is None:
        return None
    state = "assured" if "ASSURED" in flags else "unreplied" if "UNREPLIED" in flags else "replied"
    return src, dport, state


def parse_conntrack(text: str, include_local: bool = False) -> Flows:
    """Summarise the flows to the game ports. Flows started from inside (a container talking out, the host) are
    left out unless include_local: their destination port only looks like ours by coincidence."""
    flows, seen, seen_assured = Flows(), set(), set()
    for line in text.splitlines():
        flow = parse_flow(line)
        if flow is None:
            continue
        src, dport, state = flow
        game = game_of(dport)
        if game is None or (not include_local and is_local(src)):
            continue
        flows.total += 1
        setattr(flows, state, getattr(flows, state) + 1)
        seen.add(src)
        if state == "assured":
            seen_assured.add(src)
        per = flows.by_game.setdefault(game, {"assured": 0, "replied": 0, "unreplied": 0})
        per[state] += 1
    flows.sources, flows.assured_sources = len(seen), len(seen_assured)
    return flows


def read_flows(path: str = "/proc/net/nf_conntrack") -> Flows | None:
    try:
        return parse_conntrack(Path(path).read_text())
    except OSError:
        return None


# ── what the games say about their players ──────────────────────────────────

# Valheim 1.0.16, assembly_valheim.dll: 'Session "{0}" with join code {1} and IP {3} is active with {2} player(s)'
# and '{0} server "{1}" that has join code {2}, now {3} player(s)'. Both were seen in a real crossplay server's log.
_CROSSPLAY = re.compile(r'Session "[^"]*" with join code \d+ and IP \S+ is active with (\d+) player\(s\)'
                        r'|server "[^"]*" that has join code \d*, now (\d+) player\(s\)')
_STEAM_IN = re.compile(r"Got connection SteamID (\d+)")
_STEAM_OUT = re.compile(r"Closing socket (\d+)")
_JOIN = re.compile(r"\[JOIN\]\s+(.+?)\s+joined the game")
_LEAVE = re.compile(r"\[LEAVE\]\s+(.+?)\s+left the game")


def valheim_players(log: str) -> tuple[int | None, bool]:
    """(players, crossplay). A crossplay server (its log mentions a join code; a normal one never does) says how many
    players it has on its session lines, and only there: crossplay players never touch port 2456. The last such line
    is the current count. Steam connections are paired as well (`Got connection SteamID N` opens one, `Closing
    socket N` closes it) and the larger number wins. None = a crossplay server that has not said yet."""
    connected: set[str] = set()
    said: int | None = None
    for line in log.splitlines():
        if (m := _STEAM_IN.search(line)):
            connected.add(m.group(1))
        elif (m := _STEAM_OUT.search(line)):
            connected.discard(m.group(1))
        elif (m := _CROSSPLAY.search(line)):
            said = int(m.group(1) or m.group(2))
    crossplay = "join code" in log
    if not crossplay:
        return len(connected), False
    if said is None and not connected:
        return None, True
    return max(said or 0, len(connected)), True


def factorio_players(log: str) -> int:
    """Names between `[JOIN] name joined the game` and `[LEAVE] name left the game`."""
    online: set[str] = set()
    for line in log.splitlines():
        if (m := _JOIN.search(line)):
            online.add(m.group(1))
        elif (m := _LEAVE.search(line)):
            online.discard(m.group(1))
    return len(online)


def read_game_logs(runner=run) -> tuple[dict[str, int], list[str]]:
    """(exact player counts by game, running games whose players the packet counters cannot see and that have not
    said how many). Only games with a running container and a log that carries a count are asked."""
    players: dict[str, int] = {}
    blind: list[str] = []
    rc, out = runner(["docker", "ps", "--format", "{{.Names}}"], timeout=20)
    running = out.split() if rc == 0 else []
    if "valheim" in running:
        rc, log = runner(["docker", "logs", "valheim"], timeout=60, merge=True)
        if rc == 0:
            count, crossplay = valheim_players(log)
            if count is not None:
                players["valheim"] = count
            elif crossplay:
                blind.append("valheim")
    if "factorio" in running:
        rc, log = runner(["docker", "logs", "factorio"], timeout=60, merge=True)
        if rc == 0:
            players["factorio"] = factorio_players(log)
    return players, blind


# ── decision logic (pure) ────────────────────────────────────────────────────

@dataclass
class Sample:
    """What one check observed. None = could not be read."""
    uptime_s: float
    window_s: float                            # seconds the deltas cover
    raw_delta: int | None = None               # all packets to the game ports in the window
    assured_delta: int | None = None           # packets on ASSURED flows in the window
    flows: Flows | None = None
    players: dict[str, int] = field(default_factory=dict)
    blind: list[str] = field(default_factory=list)


@dataclass
class Assessment:
    active: bool | None                        # True: players; False: nobody; None: cannot tell
    reason: str


def _scaled(per_interval: int, window_s: float) -> int:
    """A per-5-minutes threshold for a window of another length (at least one packet)."""
    return max(1, math.ceil(per_interval * max(window_s, 1.0) / (INTERVAL_MIN * 60)))


def assess(s: Sample) -> Assessment:
    """Is anyone playing? Any positive evidence wins; evidence that could not be read never reads as 'nobody'."""
    online = {g: n for g, n in s.players.items() if n > 0}
    if online:
        return Assessment(True, "players in the game logs: " + ", ".join(f"{g}={n}" for g, n in sorted(online.items())))
    strong, weak = _scaled(ASSURED_STRONG, s.window_s), _scaled(ASSURED_WEAK, s.window_s)
    counters, table, blunt = s.assured_delta is not None, s.flows is not None, s.raw_delta is not None
    if counters:
        if s.assured_delta >= strong:
            return Assessment(True, f"{s.assured_delta} packets on sustained two-way flows")
        if s.assured_delta >= weak and table and s.flows.assured > 0:
            return Assessment(True, f"{s.assured_delta} packets on {s.flows.assured} sustained flow(s) still open")
    elif table:                                # no counter to ask: the flow table has to do
        if s.flows.assured > 0:
            return Assessment(True, f"{s.flows.assured} sustained two-way flow(s) in the flow table")
    elif blunt:                                # neither: the packet count, at a high bar
        if s.raw_delta >= _scaled(FALLBACK_PACKETS, s.window_s):
            return Assessment(True, f"{s.raw_delta} packets on the game ports (the only evidence readable)")
    if s.blind:
        return Assessment(None, f"cannot see {', '.join(s.blind)} players: crossplay traffic bypasses the game ports "
                                "and the game has not logged a player count")
    if not (counters or table or blunt):
        return Assessment(None, "no evidence could be read (counters, flow table and game logs all failed)")
    noise = f"; {max(s.raw_delta - (s.assured_delta or 0), 0)} packets of probe noise ignored" if blunt else ""
    return Assessment(False, "no player evidence" + noise)


@dataclass
class Config:
    idle_limit_min: int = DEFAULT_IDLE_MIN     # 0: never sleep (and no backstop)
    max_hours: float = DEFAULT_MAX_HOURS       # 0: no backstop


@dataclass
class State:
    idle_min: int = 0
    last_tick: float | None = None             # epoch seconds of the previous check
    announced: bool = False                    # the backstop's heads-up went out
    raw_total: int | None = None
    assured_total: int | None = None
    verdict: str = ""
    reason: str = ""
    unknown_min: int = 0                       # minutes in a row the watchdog could not tell


@dataclass
class Decision:
    action: str                                # none | announce | sleep
    why: str                                   # idle | backstop | ""
    idle_min: int
    reason: str
    assessment: Assessment


def step(state: State, s: Sample, cfg: Config, now: float) -> tuple[State, Decision]:
    """One tick of the state machine: the new state, and what to do."""
    new = State(**vars(state))
    a = assess(s)
    elapsed_min = max(1, round(min(max(s.window_s, 0.0), 2 * INTERVAL_MIN * 60) / 60))
    if a.active is True:
        new.idle_min, new.unknown_min = 0, 0
    elif a.active is False:
        new.idle_min, new.unknown_min = state.idle_min + elapsed_min, 0
    else:                                      # cannot tell: the idle clock holds
        new.unknown_min = state.unknown_min + elapsed_min
    new.last_tick = now
    new.verdict = {True: "active", False: "idle", None: "unknown"}[a.active]
    new.reason = a.reason

    if cfg.idle_limit_min > 0 and cfg.max_hours > 0 and s.uptime_s >= cfg.max_hours * 3600:
        return new, Decision("sleep", "backstop", new.idle_min, f"{cfg.max_hours:g} h limit reached", a)
    if cfg.idle_limit_min > 0 and new.idle_min >= cfg.idle_limit_min:
        return new, Decision("sleep", "idle", new.idle_min, f"nobody played for {new.idle_min} min", a)
    if (cfg.idle_limit_min > 0 and cfg.max_hours > 0 and not state.announced
            and s.uptime_s >= cfg.max_hours * 3600 - ANNOUNCE_AHEAD_S):
        new.announced = True
        return new, Decision("announce", "backstop", new.idle_min, "the session limit is 15 minutes away", a)
    return new, Decision("none", "", new.idle_min, a.reason, a)


# ── settings, state, side effects ────────────────────────────────────────────

def metadata(key: str, default: str = "") -> str:
    req = urllib.request.Request(META + key, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read().decode().strip()
    except Exception:  # noqa: BLE001 — an unreachable metadata server must not stop the check
        return default


def parse_idle(raw: str) -> int:
    """idle-minutes: 0 = never sleep; blank or junk = the default."""
    raw = raw.strip()
    return int(raw) if raw.isdigit() else DEFAULT_IDLE_MIN


def parse_max_hours(raw: str) -> float:
    """max-hours: 0 = no backstop; blank, junk or negative = the default."""
    try:
        value = float(raw.strip())
    except ValueError:
        return DEFAULT_MAX_HOURS
    return value if value >= 0 and math.isfinite(value) else DEFAULT_MAX_HOURS


def load_state(path: Path | None = None) -> State:
    path = path or STATE / "watchdog.json"
    try:
        data = json.loads(path.read_text())
        return State(**{k: v for k, v in data.items() if k in State.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return State()


def save_state(state: State, path: Path | None = None) -> None:
    path = path or STATE / "watchdog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(vars(state)))
    tmp.replace(path)
    # The two files the old script kept, which diag.sh and people still read.
    (path.parent / "idle").write_text(f"{state.idle_min}\n")
    if state.raw_total is not None:
        (path.parent / "packets").write_text(f"{state.raw_total}\n")


def uptime_s() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def log(message: str, runner=run) -> None:
    """To journald under the tag `skippy-idle` (journalctl -t skippy-idle), and to stdout (the unit's own journal)."""
    print(message, flush=True)
    runner(["logger", "-t", TAG, "--", message], timeout=10)


def delta(now: int | None, before: int | None) -> int | None:
    """Packets since the last reading. A counter that went backwards was reset (the rule was re-added): count from 0."""
    if now is None or before is None:
        return None
    return now - before if now >= before else now


def gather(state: State, now: float, runner=run, flows_path: str = "/proc/net/nf_conntrack"):
    """Read every kind of evidence, each failing on its own. Returns (sample, counter totals, problems)."""
    added, problems = ensure_rules(runner)
    counters = read_counters(runner)
    raw = counters.get(RULE_RAW) if counters else None
    assured = counters.get(RULE_ASSURED) if counters else None
    if counters is None:
        problems.append("the iptables counters could not be read")
    up = uptime_s()
    window = max(now - (state.last_tick if state.last_tick else now - up), 1.0)
    raw_d, assured_d = delta(raw, state.raw_total), delta(assured, state.assured_total)
    if state.raw_total is None:                # first reading: the counters began at 0 when the rule went in
        raw_d = raw
    if state.assured_total is None:
        assured_d = assured
    if RULE_RAW in added:                      # a rule added this very moment watched nothing: no reading
        raw_d = None
    if RULE_ASSURED in added:
        assured_d = None
    players, blind = read_game_logs(runner)
    sample = Sample(uptime_s=up, window_s=window, raw_delta=raw_d, assured_delta=assured_d,
                    flows=read_flows(flows_path), players=players, blind=blind)
    return sample, {"raw_total": raw, "assured_total": assured}, problems


def describe(s: Sample, a: Assessment, idle_min: int, cfg: Config) -> str:
    f = s.flows
    flows = ("flows unreadable" if f is None else
             f"flows {f.total} (sustained {f.assured}, answered {f.replied}, unanswered {f.unreplied}) "
             f"from {f.sources} address(es)")
    pkts = ("packets unreadable" if s.raw_delta is None else
            f"packets +{s.raw_delta} (+{'?' if s.assured_delta is None else s.assured_delta} on sustained flows)")
    players = ("players " + ", ".join(f"{g}={n}" for g, n in sorted(s.players.items()))
               if s.players else "no game log count")
    limit = f"{cfg.max_hours:g} h" if cfg.max_hours else "no limit"
    verdict = "ACTIVE" if a.active else "idle" if a.active is False else "UNKNOWN"
    return (f"idle {idle_min}/{cfg.idle_limit_min} min | {pkts} | {flows} | {players} | {verdict}: {a.reason} | "
            f"up {s.uptime_s / 3600:.1f} h of {limit}")


def stop_everything(reason: str, runner=run) -> None:
    """Save the worlds (graceful stop), say so on Discord, power off. Each step may fail without stopping the rest."""
    profiles = [arg for p in PROFILES for arg in ("--profile", p)]
    runner(["docker", "compose", "--project-directory", ROOT, *profiles, "stop", "--timeout", "120"], timeout=400)
    runner([EVERQUEST, "stop"], timeout=300)
    runner([DISCORD, "offline", reason], timeout=60)
    runner(["systemctl", "poweroff"], timeout=60)


def announce_backstop(cfg: Config, runner=run) -> None:
    span = f"{cfg.max_hours:g} hour" + ("" if cfg.max_hours == 1 else "s")
    runner([DISCORD, "notice",
            f"⏰ This server powers off in 15 minutes: a session is limited to {span}. "
            "Save up and log out; it can be started again afterwards."], timeout=60)


def tick(runner=run, dry_run: bool = False, now: float | None = None) -> int:
    cfg = Config(parse_idle(metadata("idle-minutes", str(DEFAULT_IDLE_MIN))), parse_max_hours(metadata("max-hours", "")))
    if cfg.idle_limit_min == 0:
        log("idle-minutes is 0: this session never sleeps and has no time limit", runner)
        return 0
    try:
        return _check(cfg, runner, dry_run, now)
    except Exception as exc:  # noqa: BLE001 — a bug in the evidence code must not switch the backstop off with it
        traceback.print_exc()
        log(f"ERROR the check crashed ({exc!r}): only the {cfg.max_hours:g} h session limit is in force", runner)
        if cfg.max_hours > 0 and uptime_s() >= cfg.max_hours * 3600:
            log(f"backstop: {cfg.max_hours:g} h limit reached: saving worlds and powering off", runner)
            if not dry_run:
                stop_everything(f"{cfg.max_hours:g} h limit reached", runner)
        return 0


def _check(cfg: Config, runner, dry_run: bool, now: float | None) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    state = load_state()
    sample, totals, problems = gather(state, now, runner)
    new, decision = step(state, sample, cfg, now)
    new.raw_total = totals["raw_total"] if totals["raw_total"] is not None else state.raw_total
    new.assured_total = totals["assured_total"] if totals["assured_total"] is not None else state.assured_total
    save_state(new)
    for problem in problems:
        log(f"WARNING {problem}", runner)
    log(describe(sample, decision.assessment, new.idle_min, cfg), runner)
    if decision.assessment.active is None:
        log(f"WARNING cannot tell whether anyone is playing ({new.unknown_min} min so far); only the "
            f"{cfg.max_hours:g} h limit will end this session", runner)
    if decision.action == "announce":
        log("announcing the session limit on Discord", runner)
        if not dry_run:
            announce_backstop(cfg, runner)
    elif decision.action == "sleep":
        log(f"{decision.why}: {decision.reason}: saving worlds and powering off", runner)
        if not dry_run:
            stop_everything(decision.reason, runner)
    return 0


# ── report (for skippy-diag) ─────────────────────────────────────────────────

def report(runner=run, out=print, flows_path: str = "/proc/net/nf_conntrack", now: float | None = None) -> int:
    """What the watchdog sees right now, as lines for the serial console. The two counting-only iptables rules are
    added if missing (so counting runs even before the VM reboots onto this version); packet deltas are since the
    previous report."""
    now = time.time() if now is None else now
    added, problems = ensure_rules(runner)
    counters = read_counters(runner) or {}
    flows = read_flows(flows_path)
    players, blind = read_game_logs(runner)
    prev_path = STATE / "report.json"
    try:
        prev = json.loads(prev_path.read_text())
    except (OSError, ValueError):
        prev = {}
    window = now - prev["at"] if isinstance(prev.get("at"), (int, float)) and now > prev["at"] else None
    raw, assured = counters.get(RULE_RAW), counters.get(RULE_ASSURED)
    raw_d = delta(raw, prev.get("raw")) if window and RULE_RAW not in added else None
    assured_d = delta(assured, prev.get("assured")) if window and RULE_ASSURED not in added else None
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        prev_path.write_text(json.dumps({"at": now, "raw": raw, "assured": assured}))
    except OSError:
        pass
    tick_state = load_state()
    cfg = Config(parse_idle(metadata("idle-minutes", str(DEFAULT_IDLE_MIN))), parse_max_hours(metadata("max-hours", "")))
    up = uptime_s()

    backstop = "" if cfg.max_hours and cfg.idle_limit_min else " (no backstop)"
    out(f"uptime {up / 3600:.2f} h; idle-minutes={cfg.idle_limit_min}; max-hours={cfg.max_hours:g}{backstop}")
    if tick_state.last_tick:
        out(f"last check {int(now - tick_state.last_tick)}s ago: {tick_state.verdict}, idle clock {tick_state.idle_min} min"
            f"{', cannot tell for ' + str(tick_state.unknown_min) + ' min' if tick_state.unknown_min else ''}"
            f" ({tick_state.reason})")
    else:
        out("no check has run since boot (the timer's first is 5 min after boot)")
    for problem in problems:
        out(f"PROBLEM {problem}")
    out("counters: raw=" + ("NO RULE" if raw is None else str(raw)) + " assured=" + ("NO RULE" if assured is None else str(assured))
        + (f" | last {window:.0f}s: raw +{raw_d}, assured +{assured_d}" if raw_d is not None else
           " | packet deltas start with the next report"))
    if flows is None:
        out("conntrack: /proc/net/nf_conntrack unreadable")
    else:
        out(f"conntrack, UDP to the game ports from outside: {flows.total} flows | sustained {flows.assured} "
            f"({flows.assured_sources} addresses) | answered once {flows.replied} | unanswered {flows.unreplied} | "
            f"{flows.sources} distinct source addresses")
        for game, per in sorted(flows.by_game.items()):
            out(f"  {game}: sustained {per['assured']}, answered once {per['replied']}, unanswered {per['unreplied']}")
    for game, n in sorted(players.items()):
        out(f"PLAYERS {game}={n}")
    if blind:
        out(f"blind: {', '.join(blind)} (crossplay: only the game's own log can say)")
    sample = Sample(uptime_s=up, window_s=window or INTERVAL_MIN * 60, raw_delta=raw_d, assured_delta=assured_d,
                    flows=flows, players=players, blind=blind)
    a = assess(sample)
    verdict = {True: "active", False: "idle", None: "unknown"}[a.active]
    out(f'WATCHDOG verdict={verdict} idle_min={tick_state.idle_min} limit={cfg.idle_limit_min} '
        f'up_min={int(up // 60)} max_hours={cfg.max_hours:g} unknown_min={tick_state.unknown_min} reason="{a.reason}"')
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "tick"
    if cmd == "tick":
        return tick(dry_run=os.environ.get("SKIPPY_WATCHDOG_DRY_RUN") == "1")
    if cmd == "arm":
        _, problems = ensure_rules()
        for problem in problems:
            print(f"PROBLEM {problem}", file=sys.stderr)
        return 1 if problems else 0
    if cmd == "report":
        return report()
    print(f"usage: {argv[0]} [tick|arm|report]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
