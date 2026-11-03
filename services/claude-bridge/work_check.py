"""The work check (the Captain, 2026-11-01: help the coder match Claude without touching its prompt or training it): a turn may not
end while the work it made fails the project's own checker, while it hasn't looked at what it made, or, where the project asks
for it, before it has put a real effort in. Facts, never advice on how to do the work.

A project opts in with `.skippy-check.json` at its root:

    {"checks": [{"files": "build.json",                       # a glob, relative to the root
                 "check": "node tools/check.mjs {file}",      # exit 0 = good; anything else is the agent's to fix, output included
                 "show":  "python tools/render.py {file} {png}"}],   # optional: draws the file as a picture
     "budget": 60000}                                         # optional: output tokens a run is expected to spend on such work

Only files matching a glob that this run changed (written after it started) count, so a run that never touched them is never
held up, and a project without the file has no checks at all. Each reason is given a limited number of times (LIMITS), so a run
can never be held forever. The CLI engine's Stop hook (finish_gate.py) and the SDK engine's StopGate both ask unfinished().

On a Skippy Core run (SKIPPY_CORE_EYES=1: the coder can't see) the look is the fix-the-worst-fault loop (the Captain, 2026-11-03):
the gate itself has Core's big model review each new picture against the task (eyes.worst_fault) and sends the run back with that
review and its ONE worst fault; the next version is reviewed again, first for that fault, until a review names none or the
"fault" limit is spent. (In the MineBench rematch the coder read honest reviews and shipped anyway.) A viewer that can't draw
the work is said once, so "I can't trigger the render" never ships unseen.
"""
from __future__ import annotations

import calendar
import glob
import hashlib
import json
import os
import subprocess
import tempfile
import time

CONFIG = ".skippy-check.json"
LOOK_DIR = os.path.join(tempfile.gettempdir(), "skippy-look")   # the pictures of the work: outside the repo (never committed; a run reads anywhere)
LIMITS = {"check": 4, "look": 3, "fault": 5, "draw": 1, "budget": 2}   # how many times each reason may send a run back
CHECK_TIMEOUT = 300
OUTPUT_CHARS = 1500


def _config(clone: str) -> dict:
    try:
        with open(os.path.join(clone, CONFIG), encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def changed(clone: str, pattern: str, started: float) -> list[str]:
    """The files matching `pattern` (relative to `clone`) written since `started`, as paths relative to the clone."""
    out = []
    for path in sorted(glob.glob(os.path.join(clone, pattern), recursive=True)):
        try:
            if os.path.isfile(path) and os.path.getmtime(path) >= started:
                out.append(os.path.relpath(path, clone).replace("\\", "/"))
        except OSError:
            continue
    return out


def _run(command: str, clone: str, **values) -> tuple[int, str]:
    for k, v in values.items():
        command = command.replace("{" + k + "}", f'"{v}"')
    try:
        p = subprocess.run(command, shell=True, cwd=clone, capture_output=True, text=True, timeout=CHECK_TIMEOUT,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 124, f"(it ran longer than {CHECK_TIMEOUT} s)"
    said = (p.stdout + p.stderr).strip()
    return p.returncode, said if len(said) <= OUTPUT_CHARS else "..." + said[-OUTPUT_CHARS:]


def output_tokens(transcript: str, started: float) -> int:
    """Output tokens the model spent in this run: its replies in the session transcript since `started` (one count per reply)."""
    total, seen = 0, set()
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                stamp = str(d.get("timestamp") or "")
                try:
                    when = calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))   # the transcript stamps UTC
                except ValueError:
                    when = started
                msg = d.get("message") or {}
                key = msg.get("id") or d.get("uuid")
                if when < started - 2 or key in seen:             # (stamps are read to the second)
                    continue
                seen.add(key)
                total += int((msg.get("usage") or {}).get("output_tokens") or 0)
    except OSError:
        return -1                                   # unreadable: no opinion
    return total


def _eyes(env) -> tuple[str, str]:
    """Core's address and key when this run's coder can't see for itself (eyes.py's switch), else ('', '')."""
    if env.get("SKIPPY_CORE_EYES") != "1":
        return "", ""
    return env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_API_KEY", "")


def unfinished(clone: str, started: float, memo: dict, transcript: str = "", env=os.environ, post=None) -> str:
    """Why this run's work isn't finished, as facts ('' = it is, or the project asks nothing). `memo` keeps the counts and what
    has been shown between calls (the caller stores it)."""
    cfg = _config(clone)
    checks = [c for c in cfg.get("checks") or [] if isinstance(c, dict) and c.get("files")]
    if not checks:
        return ""
    sent = memo.setdefault("sent", {})
    shown = memo.setdefault("shown", {})
    faults = memo.setdefault("faults", {})            # file -> the worst fault it was last sent back to fix
    url, key = _eyes(env)
    task = ""
    if url and key and transcript:
        import eyes
        task = eyes.task_of(transcript)
    touched = False

    def send(kind: str, reason: str) -> str:
        if sent.get(kind, 0) >= LIMITS[kind]:
            return ""
        sent[kind] = sent.get(kind, 0) + 1
        return reason

    for c in checks:
        for rel in changed(clone, c["files"], started):
            touched = True
            if c.get("check"):
                code, said = _run(c["check"], clone, file=rel)
                if code:
                    why = send("check", f"{rel} fails the project's own check (`{c['check'].replace('{file}', rel)}`, exit {code}):\n{said}")
                    if why:
                        return why
                    continue
            if c.get("show"):
                try:
                    digest = hashlib.sha256(open(os.path.join(clone, rel), "rb").read()).hexdigest()
                except OSError:
                    continue
                if shown.get(rel) == digest:
                    if faults.get(rel):
                        why = send("fault", f"{rel} hasn't changed since its worst fault was named: {faults[rel]}\n"
                                            "Fix that, or say plainly why it can't be fixed.")
                        if why:
                            return why
                    continue
                png = os.path.join(LOOK_DIR, hashlib.sha256(os.path.abspath(clone).encode()).hexdigest()[:12], rel.replace("/", "_") + f"-{digest[:8]}.png")
                os.makedirs(os.path.dirname(png), exist_ok=True)
                code, said = _run(c["show"], clone, file=rel, png=png)
                if code or not os.path.isfile(png):
                    why = send("draw", f"The project's viewer couldn't draw {rel} (`{c['show']}`, exit {code}):\n{said}\n"
                                       "Nobody has seen this version. Make it draw, then look at it.")
                    if why:
                        return why
                    continue
                if task and sent.get("fault", 0) >= LIMITS["fault"]:
                    shown[rel] = digest
                    continue                        # its sends are spent: a review now would change nothing
                if task:
                    import eyes
                    try:
                        seen, worst = eyes.worst_fault(url, key, png, task, faults.get(rel, ""), post=post, drawn=rel)
                    except Exception:                                # noqa: BLE001 — Core couldn't review: the plain look below
                        seen = worst = None
                    if seen is not None:
                        shown[rel] = digest
                        if not worst:
                            faults.pop(rel, None)
                            continue                # no major fault left in this version
                        faults[rel] = worst
                        why = send("fault", f"Skippy Core's big model looked at {rel} as it is now ({png}, no need to open it) "
                                            f"against your task. Its review:\n\n{seen}\n\nThe worst fault: {worst}\n"
                                            "Fix that one first, then stop again: the new version is drawn and reviewed the same "
                                            "way until no major fault is left.")
                        if why:
                            return why
                        continue
                why = send("look", f"Here is {rel} as it is now, drawn by the project's viewer: {png}. You haven't looked at this "
                                   "version. Open it, compare it with what was asked, and fix what doesn't match.")
                if why:
                    shown[rel] = digest
                    return why
    budget = int(cfg.get("budget") or 0)
    if touched and budget and transcript:
        spent = output_tokens(transcript, started)
        if 0 <= spent < budget:
            return send("budget", f"This kind of work has a budget of about {budget:,} output tokens and this run has spent "
                                  f"{spent:,}. There is room left to make it better.")
    return ""
