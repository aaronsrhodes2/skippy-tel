"""What purple did versus what it said, over its last runs (the Captain, 2026-11-01): the replies whose words claim a push, a deploy or a
test run its calls don't show, edits with no test run, and outward calls (push, deploy, restart, kill) it never mentioned; and the typical
toolchain per kind of request, so a workflow that keeps failing shows up in bulk.

It reads the private Skippy's conversation indexes (services/claude-bridge/conversation_log.py: <date>_<conv>.turns.json, every turn's
events) and judges each reply with the same rules as the strip under it in the chat (services/claude-bridge/toolchain.py): a turn that
carries its `chain` event is read from it (times and outcomes included); an older one is rebuilt from its tool lines.

    python tools/purple_audit.py                 the last 50 replies
    python tools/purple_audit.py --runs 200 --json
    python tools/purple_audit.py --since 2026-11-01

tools/nightly.py adds ledger_line() to the day's ledger: "purple: 2 replies claimed a push they didn't make".
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "claude-bridge"))
import toolchain  # noqa: E402

CONVERSATIONS = Path(os.getenv("WORKSHOP_CONVERSATIONS", r"D:\Aaron\conversations\private-skippy"))

REQUEST_KINDS = [                                   # the first that matches the Captain's message
    ("ship", re.compile(r"\b(deploy|push|commit|ship|release|roll ?back|go live)\b")),
    ("fix", re.compile(r"\b(fix|bug|broken|broke|error|fail\w*|crash\w*|doesn't|does not|isn't|not working|wrong|stuck)\b")),
    ("build", re.compile(r"\b(add|build|make|create|implement|write|new|redesign|change|move|rename|refactor)\b")),
    ("house", re.compile(r"\b(lights?|car|tesla|thermostat|music|play|garage|door|pod|alarm|timer|weather)\b")),
    ("look", re.compile(r"\b(check|look|find|show|status|why|what|how|where|when|explain|is it|are)\b|\?\s*$")),
]
SHORT = {"read": "R", "edit": "E", "shell": "S", "git": "G", "web": "W", "deploy": "D", "house": "H", "other": "o"}
WORDS = {"push": "claimed a push they didn't make", "deploy": "claimed a deploy or live they didn't check", "test": "claimed tests they didn't run",
         "untested": "edited files with no test run", "unsaid-push": "pushed without saying so", "unsaid-deploy": "deployed without saying so",
         "unsaid-restart": "restarted something without saying so", "unsaid-kill": "killed a process without saying so",
         "unsaid-destroy": "ran a destructive command without saying so"}
MAX_SHAPE = 16                                       # groups a shape shows before "…+n"
LEDGER_RULES = ("push", "deploy", "test", "unsaid-push", "unsaid-deploy", "unsaid-restart", "unsaid-kill", "unsaid-destroy", "untested")


def request_kind(message: str) -> str:
    low = str(message or "").lower()
    for kind, pattern in REQUEST_KINDS:
        if pattern.search(low):
            return kind
    return "other"


def shape(calls: list[dict]) -> str:
    """A toolchain in a few letters, runs squeezed: R5 E2 S G (read five times, edit twice, a shell call, git)."""
    out: list[list] = []
    for c in calls:
        letter = SHORT.get(c.get("k"), "o")
        if out and out[-1][0] == letter:
            out[-1][1] += 1
        else:
            out.append([letter, 1])
    groups = [letter + (str(n) if n > 1 else "") for letter, n in out]
    return (" ".join(groups[:MAX_SHAPE]) + (f" …+{len(groups) - MAX_SHAPE}" if len(groups) > MAX_SHAPE else "")) or "-"


def replies(folder: Path = CONVERSATIONS, since: float = 0.0) -> list[dict]:
    """Every finished reply in the conversation indexes, oldest first: {conv, title, at, message, kind, calls, flags, state, timed}."""
    out = []
    for path in sorted(folder.glob("*.turns.json")) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for t in data.get("turns") or []:
            if not isinstance(t, dict) or t.get("state") == "running":
                continue
            at = t.get("at") if isinstance(t.get("at"), (int, float)) else 0.0
            if at < since:
                continue
            events = t.get("events") if isinstance(t.get("events"), list) else []
            chain = next((e for e in reversed(events) if isinstance(e, dict) and e.get("t") == "chain"), None)
            timed = chain is not None
            if chain is None:
                chain = toolchain.chain_event(events) or {"calls": [], "flags": []}
            out.append({"conv": data.get("conv"), "title": data.get("title"), "at": at, "message": str(t.get("message") or ""),
                        "kind": request_kind(t.get("message")), "calls": chain.get("calls") or [], "flags": chain.get("flags") or [],
                        "state": t.get("state"), "error": t.get("error"), "timed": timed})
    out.sort(key=lambda r: r["at"])
    return out


def audit(rows: list[dict]) -> dict:
    flags = collections.Counter(f["rule"] for r in rows for f in r["flags"])
    kinds: dict[str, dict] = {}
    for kind in sorted({r["kind"] for r in rows}):
        mine = [r for r in rows if r["kind"] == kind]
        counts = sorted(len(r["calls"]) for r in mine)
        shapes = collections.Counter(shape(r["calls"]) for r in mine)
        used = collections.Counter(c.get("n") for r in mine for c in r["calls"])
        kinds[kind] = {"replies": len(mine), "median_calls": counts[len(counts) // 2] if counts else 0,
                       "flagged": sum(1 for r in mine if r["flags"]), "failed": sum(1 for r in mine if r["state"] == "error"),
                       "common_shapes": shapes.most_common(3), "tools": used.most_common(6)}
    return {"replies": len(rows), "flagged": sum(1 for r in rows if r["flags"]), "by_rule": dict(flags), "kinds": kinds,
            "mismatches": [{"at": r["at"], "conv": r["conv"], "message": r["message"][:120], "flags": [f["text"] for f in r["flags"]],
                            "shape": shape(r["calls"])} for r in rows if r["flags"]]}


def ledger_line(rows: list[dict]) -> str:
    """One line for the night's ledger: the worst mismatches first, or that purple's words matched its calls."""
    by = collections.Counter(f["rule"] for r in rows for f in r["flags"])
    if not rows:
        return "purple: no replies"
    bits = [f"{by[rule]} repl{'ies' if by[rule] != 1 else 'y'} {WORDS[rule]}" for rule in LEDGER_RULES if by.get(rule)]
    if not bits:
        return f"purple: {len(rows)} replies, every claim backed by a call"
    return "purple: " + ", ".join(bits[:3]) + (f" (of {len(rows)})" if rows else "")


def day_rows(date: str, folder: Path = CONVERSATIONS) -> list[dict]:
    start = time.mktime(time.strptime(date, "%Y-%m-%d"))
    return [r for r in replies(folder, start) if r["at"] < start + 86400]


def report_text(result: dict) -> str:
    lines = [f"{result['replies']} replies, {result['flagged']} with a mismatch"]
    for rule, n in sorted(result["by_rule"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:4d}  {WORDS.get(rule, rule)}")
    lines += ["", "Typical toolchain by kind of request (R read, E edit, S shell, G git, W web, D deploy/live, H Mentat/house):"]
    for kind, k in result["kinds"].items():
        common = "; ".join(f"{s} ({n})" for s, n in k["common_shapes"])
        lines.append(f"  {kind:6s} {k['replies']:4d} replies, median {k['median_calls']} calls, {k['flagged']} flagged, {k['failed']} failed")
        lines.append(f"         {common}")
        lines.append("         " + ", ".join(f"{name} {n}" for name, n in k["tools"]))
    if result["mismatches"]:
        lines += ["", "Mismatches (newest last):"]
        for m in result["mismatches"]:
            lines.append(f"  {time.strftime('%m-%d %H:%M', time.localtime(m['at']))} {m['conv']}: {m['message']!r}")
            lines.append(f"      [{m['shape']}]")
            lines += [f"      \u26a0 {f}" for f in m["flags"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=50, help="the last N replies (default 50)")
    ap.add_argument("--since", default=None, help="only replies from this day on (YYYY-MM-DD)")
    ap.add_argument("--folder", default=str(CONVERSATIONS))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # the warning glyph, on a Windows console too
    since = time.mktime(time.strptime(args.since, "%Y-%m-%d")) if args.since else 0.0
    rows = replies(Path(args.folder), since)[-max(1, args.runs):]
    result = audit(rows)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(report_text(result))
        print("\n" + ledger_line(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
