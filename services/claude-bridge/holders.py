"""Who a risky command would hit (the Captain, 2026-10-31): when the lane guard refuses a kill by name or a reboot (lane_guard.RISKY),
its reason says, at that moment, which processes match and whose they are, which purple runs are going (how long they have run, their
limit, what they are doing now), and how to override: ask the Captain, and on his yes run the same command with `SKIPPY_FORCE=1` in front.

Plain data in, plain text out; the process list and the runs are read here but can be handed in (tests). Nothing here kills anything.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RUNS_DIR = os.environ.get("SKIPPY_RUNS_DIR") or "D:/Aaron/development/.skippy-workshop-runs"
RUN_LIMIT_MIN = 120                       # workshop_engine.MAX_RUN_S, in minutes (not imported: the guard stays light)
MAX_LINES = 10
FORCE = re.compile(r"^\s*SKIPPY_FORCE=1\s")

# What a process is, by its command line: the things a stray kill has hit or would hurt
KNOWN = [
    (re.compile(r"hub\.py", re.I), "the home hub (every room's voice and timers; needed all the time, a kill drops a conversation in progress)"),
    (re.compile(r"\b(?:front|bridge)\.py", re.I), "purple's bridge (every purple run goes through it; needed all the time)"),
    (re.compile(r"skippy_starter\.py", re.I), "the starter (the doctor's hands on this PC; needed all the time)"),
    (re.compile(r"face_stream\.py", re.I), "a pod's face stream (needed while the pod shows Skippy)"),
    (re.compile(r"purple-tools[\\/]server\.py", re.I), "purple's tools server (needed by every purple run)"),
    (re.compile(r"_mcp\.py|[\\/]mcp[\\/]|-mcp\.exe", re.I), "an MCP server of a Claude app or chat (needed while that chat is open)"),
    (re.compile(r"[\\/]dev_\w+\.py", re.I), "a dev harness (needed while someone is testing against it)"),
    (re.compile(r"(?:^|[\\/\"])claude(?:\.exe)?\"?(?:\s|$)", re.I), "a Claude Code session (a purple run, or one of the Captain's Code-tab chats)"),
    (re.compile(r"pytest|vitest|node .*test", re.I), "a test run (needed until it finishes, usually minutes)"),
]


def _target(cmd: str) -> tuple[str, str]:
    """('image', name) | ('title', pattern) | ('cmdline', pattern) | ('name', pattern) for a kill-by-name command, else ('', '')."""
    for rx, kind in ((r"/IM\s+\"?([^\"\s]+)", "image"), (r"WINDOWTITLE\s+(?:eq\s+)?([^\"]+)", "title"),
                     (r"-Name\s+\"?([^\"\s;|&]+)", "image"), (r"\bpkill\s+(?:-\w+\s+)*-f\s+\"?([^\"\s;|&]+)", "cmdline"),
                     (r"\b(?:pkill|killall)\s+(?:-\w+\s+)*\"?([^\"\s;|&]+)", "name")):
        m = re.search(rx, cmd, re.I)
        if m:
            return kind, m.group(1).strip().strip("*'").lower()
    return "", ""


def processes() -> list[dict]:
    """This machine's processes: [{pid, name, cmd}], [] when they can't be listed in time."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"],
                                 capture_output=True, text=True, timeout=6, stdin=subprocess.DEVNULL).stdout
            rows = json.loads(out or "[]")
            return [{"pid": r.get("ProcessId"), "name": str(r.get("Name") or ""), "cmd": str(r.get("CommandLine") or "")} for r in rows]
        out = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True, text=True, timeout=6).stdout
        rows = [line.split(None, 2) for line in out.splitlines() if line.strip()]
        return [{"pid": int(r[0]), "name": r[1], "cmd": r[2] if len(r) > 2 else r[1]} for r in rows if r[0].isdigit()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def active_runs(runs_dir: str = RUNS_DIR, now: float | None = None) -> list[str]:
    """One line per purple run still going: where, how long of its limit, what it is doing now."""
    now = time.time() if now is None else now
    lines = []
    for f in sorted(Path(runs_dir).glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "events" not in d or d.get("done"):
            continue
        mins = max(0, int((now - float(d.get("created") or now)) / 60))
        last = next((e for e in reversed(d.get("events") or []) if e.get("t") in ("tool", "text")), {})
        doing = f"{last.get('name')} {last.get('detail') or ''}" if last.get("t") == "tool" else (last.get("text") or "starting")
        lines.append(f"purple run {d.get('task')} ({d.get('project')}, conversation {str(d.get('conv') or '')[:8]}): running {mins} min "
                     f"of its {RUN_LIMIT_MIN} min limit; now: {' '.join(str(doing).split())[:110]}")
    return lines


def describe(proc: dict) -> str:
    """What the process is: a KNOWN label, else its command line, shortened."""
    what = next((label for rx, label in KNOWN if rx.search(proc["cmd"] or proc["name"])), "")
    if what:
        return what
    script = re.sub(r'^"?[^"]*?(?:python|pythonw|node)(?:\.exe)?"?\s+(?:-u\s+)?', "", proc["cmd"] or "")   # the script, not the interpreter
    return " ".join(script.split())[:90] or proc["name"] or "no command line"


def grouped(hit: list[dict]) -> list[str]:
    """One line per kind of process: the known ones first (the hub, the bridge), the fewest first among each."""
    kinds: dict[str, list] = {}
    for p in hit:
        kinds.setdefault(describe(p), []).append(p["pid"])
    labels = {label for _, label in KNOWN}
    order = sorted(kinds.items(), key=lambda kv: (kv[0] not in labels, len(kv[1])))
    lines = [f"{len(pids)} x {what} (PID {', '.join(map(str, pids[:4]))}{', ...' if len(pids) > 4 else ''})" for what, pids in order]
    return lines[:MAX_LINES] + ([f"... and {len(lines) - MAX_LINES} more kinds"] if len(lines) > MAX_LINES else [])


def report(cmd: str, kind: str, procs: list[dict] | None = None, runs: list[str] | None = None) -> str:
    """The facts for a refused risky command, and the way to force it."""
    runs = active_runs() if runs is None else runs
    parts = []
    if kind == "kill":
        how, pattern = _target(cmd)
        if how == "title":
            parts.append(f"Window titles can't be read from here, so which process owns \"{pattern}\" is unknown.")
        elif pattern:
            procs = processes() if procs is None else procs
            hit = [p for p in procs if (pattern in p["cmd"].lower() if how == "cmdline" else
                                        p["name"].lower() == pattern if how == "image" else pattern in p["name"].lower())]
            parts.append(f"It would stop {len(hit)} process(es): " + "; ".join(grouped(hit)) + "." if hit else "Nothing matches it right now.")
    else:
        parts.append("Everything on that machine stops with it: the hub, the bridge, the services, and these runs.")
    parts.append(("Going now: " + "; ".join(runs) + ".") if runs else "No purple run is going.")
    parts.append("Stop only your own job, by its PID. If this really must happen, ask the Captain (SKIPPY_QUESTION, options "
                 "\"Wait\" and \"Force it\") with these facts; only on his yes run the same command with `SKIPPY_FORCE=1 ` in front.")
    return " ".join(parts)
