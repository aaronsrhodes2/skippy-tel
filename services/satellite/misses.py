"""The house's miss ledger: what went wrong, found in the house log with no effort from the Captain (2026-11-11: "it's not an ideal time to submit a bug").

Reads skippy-home/log/*.jsonl (housemem.py's one-line-per-event record) and writes skippy-home/misses/ledger.json and digest.md. Nothing is asked of him and nothing
in the house changes: the hub is never touched. Deterministic (the same log gives the same ledger), so every number can be trusted whatever model reads it.

Signals, per `command` event (what he said to a pod or the Home chat); `action` events (what the house really did) are what counts as "it acted":
  failed        its outcome is not ok (a crash, a timeout)                    -> reliability
  empty         no reply at all (an alarm answered or a goodbye is none)      -> reliability
  slow          it took SLOW_S seconds or more                                -> reliability
  markup_reply  the reply is raw markup ("[list action=...]", "[SKIPPY_LEARN: ...]") the house should have run, not said   -> glitch
  denied        an ORDER answered "I cannot" with nothing done                -> vocabulary-or-route: it never reached the tool
  idle_order    an ORDER answered with words only: no tool, no action, and no question back   -> vocabulary-or-route (the trust-killer: "Adding milk..." and nothing added)
  only_looked   an ORDER where only reading tools ran, nothing was done       -> vocabulary-or-route ("turn off the upstairs lights" -> home_devices -> "no lights listed")
  rephrased     his next words in that room, within REPHRASE_S, say nearly the same thing again   -> heard-wrong
  corrected     his next words, within CORRECT_S, open with a correction ("no", "not that", "I said ...")  -> heard-wrong
Misses are grouped by what he said (word overlap); a group is `works-now` once a later, similar request went through clean (an order that acted), and the digest
says so: "X missed 3 times; it works now".

    python misses.py learn [--dry-run] [--home DIR] [--today YYYY-MM-DD]
    python misses.py digest [--home DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

SLOW_S = 20.0
REPHRASE_S = 75
CORRECT_S = 60
ACTION_WINDOW_S = 120                                                    # an `action` event this soon after a command (same room) is what that command did
JACCARD = 0.6
REPHRASE_JACCARD = 0.5
FINE = {"ok", "alarm_answered", "sign_off", "answered"}                 # outcomes that are no failure
NO_REPLY_OK = {"alarm_answered", "sign_off"}
FILLER = {"skippy", "please", "hey", "ok", "okay", "the", "a", "an", "to", "me", "my", "can", "you", "could", "would", "i", "want", "some", "just", "now", "for", "it", "this", "that"}
CORRECTION = re.compile(r"^\W*(?:no\b|nope\b|not that\b|wrong\b|i said\b|that'?s not\b|that isn'?t\b|that was wrong\b)", re.I)
MARKUP = re.compile(r"^\s*\[(?:[A-Za-z_]+)[\s:=]")                       # "[list action=..." / "[SKIPPY_LEARN: ..." (a mood tag like "[sighs]" has its bracket right after the word)
CLASS = {"failed": "reliability", "empty": "reliability", "slow": "reliability", "markup_reply": "glitch", "denied": "vocabulary-or-route",
         "idle_order": "vocabulary-or-route", "only_looked": "vocabulary-or-route", "rephrased": "heard-wrong", "corrected": "heard-wrong"}
READ_TOOLS = {"list_timers", "list_alarms", "home_state", "home_devices", "car_status", "weather", "news", "web_search", "ask_mentat", "speakers_state", "list"}
READ_ACTS = {"read", "list", "what", "status"}


def home_dir(arg: str | None = None) -> Path:
    return Path(arg or os.environ.get("SKIPPY_HOME_DIR") or Path(__file__).resolve().parents[3] / "skippy-home")


def words_of(text: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in FILLER)


def similar(a: frozenset, b: frozenset, threshold: float = JACCARD) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= threshold


def epoch(e: dict) -> float:
    try:
        return datetime.fromisoformat(str(e.get("ts"))).timestamp()
    except ValueError:
        return 0.0


def load(home: Path) -> tuple[list[dict], list[dict]]:
    """(commands, actions) from every day's log."""
    cmds: list[dict] = []
    acts: list[dict] = []
    for f in sorted((home / "log").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            if e.get("kind") == "command" and e.get("words"):
                cmds.append(e)
            elif e.get("kind") == "action":
                acts.append(e)
    return cmds, acts


def tools_of(e: dict) -> list[str]:
    t = e.get("tools")
    if isinstance(t, str):                                               # the log kept a list as its text in some versions
        return re.findall(r"[a-z_]+", t)
    return [str(x) for x in t or []]


def acted(e: dict, actions: list[dict]) -> bool:
    """Did the house DO something for this command: a tool that is not a reading one, or an `action` event (same room, soon after) that is not a read."""
    if any(t not in READ_TOOLS for t in tools_of(e)):
        return True
    t0, room = epoch(e), e.get("room")
    return any(a.get("room") == room and -2 <= epoch(a) - t0 <= ACTION_WINDOW_S and str(a.get("act") or "") not in READ_ACTS for a in actions)


def signals(e: dict, nxt: dict | None, actions: list[dict], brain) -> list[str]:
    out: list[str] = []
    outcome = str(e.get("outcome") or "")
    reply = str(e.get("reply") or "")
    words = str(e.get("words") or "")
    if outcome not in FINE:
        out.append("failed")
    if not reply.strip() and outcome not in NO_REPLY_OK:
        out.append("empty")
    try:
        if float(e.get("seconds") or 0) >= SLOW_S:
            out.append("slow")
    except (TypeError, ValueError):
        pass
    if MARKUP.search(reply):
        out.append("markup_reply")
    elif outcome in FINE and outcome not in NO_REPLY_OK and brain.order_shaped(words) and not acted(e, actions):
        tools = tools_of(e)
        if brain.denies(reply):
            out.append("denied")
        elif tools:
            out.append("only_looked")
        elif "?" not in reply:                                           # a question back ("News about what?") is a fair answer to a thin order
            out.append("idle_order")
    if nxt is not None and nxt.get("room") == e.get("room"):
        gap = epoch(nxt) - epoch(e)
        said = str(nxt.get("words") or "")
        if 0 <= gap <= CORRECT_S and CORRECTION.search(said):
            out.append("corrected")
        elif 0 <= gap <= REPHRASE_S and (said.strip().lower() == words.strip().lower() or similar(words_of(words), words_of(said), REPHRASE_JACCARD)):
            out.append("rephrased")
    return out


def build(events: list[dict], actions: list[dict], brain, today: str | None = None) -> dict:
    """The whole ledger from the whole log (idempotent)."""
    events = sorted(events, key=epoch)
    groups: list[dict] = []
    clean_after: list[tuple[float, frozenset, str]] = []                     # (when, words, date) of orders that went through clean
    for i, e in enumerate(events):
        nxt = next((x for x in events[i + 1:i + 3] if x.get("room") == e.get("room")), None)
        sig = signals(e, nxt, actions, brain)
        w = words_of(str(e.get("words") or ""))
        if not sig:
            if brain.order_shaped(str(e.get("words") or "")) and acted(e, actions):
                clean_after.append((epoch(e), w, str(e.get("date"))))
            continue
        g = next((g for g in groups if similar(w, g["_w"])), None)
        if g is None:
            g = {"_w": set(w), "words": str(e.get("words")), "count": 0, "reasons": Counter(), "first": e.get("date"), "last": e.get("date"), "last_ts": 0.0,
                 "rooms": Counter(), "example_reply": "", "example_tools": []}
            groups.append(g)
        g["_w"] |= w
        g["count"] += 1
        g["reasons"].update(sig)
        g["last"], g["last_ts"] = e.get("date"), epoch(e)
        g["rooms"][str(e.get("room"))] += 1
        g["example_reply"] = str(e.get("reply") or "")[:160]
        g["example_tools"] = tools_of(e)
    out = []
    for g in groups:
        worked = next((d for t, cw, d in clean_after if t > g["last_ts"] and similar(cw, frozenset(g["_w"]))), None)
        reason = g["reasons"].most_common(1)[0][0]
        out.append({"words": g["words"], "count": g["count"], "class": CLASS[reason], "reasons": dict(g["reasons"]), "first": g["first"], "last": g["last"],
                    "rooms": dict(g["rooms"]), "example_reply": g["example_reply"], "example_tools": g["example_tools"],
                    "status": "works-now" if worked else "open", "worked_on": worked})
    out.sort(key=lambda m: (m["status"] != "open", -m["count"], m["last"] or ""))
    return {"built": today or time.strftime("%Y-%m-%d"), "commands": len(events), "misses": out}


def digest(ledger: dict, limit: int = 8) -> str:
    misses = ledger["misses"]
    opened = [m for m in misses if m["status"] == "open"]
    fixed = [m for m in misses if m["status"] == "works-now"]
    lines = [f"# House misses ({ledger['built']}): {len(opened)} open, {len(fixed)} work now, of {ledger['commands']} commands", ""]
    for m in opened[:limit]:
        why = ", ".join(f"{k} x{v}" for k, v in sorted(m["reasons"].items(), key=lambda kv: -kv[1]))
        lines.append(f"- OPEN [{m['class']}] \"{m['words']}\" x{m['count']} ({m['first']}..{m['last']}): {why}. He got: {m['example_reply'] or '(nothing)'}")
    for m in fixed[:limit]:
        lines.append(f"- WORKS NOW \"{m['words']}\": missed x{m['count']} (last {m['last']}), went through on {m['worked_on']}")
    return "\n".join(lines) + "\n"


def learn(home: Path, today: str | None = None, dry: bool = False, brain=None) -> dict:
    if brain is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import brain as brain                                            # noqa: PLC0415 — the same detectors the hub uses
    cmds, acts = load(home)
    ledger = build(cmds, acts, brain, today)
    if not dry:
        d = home / "misses"
        d.mkdir(parents=True, exist_ok=True)
        (d / "ledger.json").write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
        (d / "digest.md").write_text(digest(ledger), encoding="utf-8")
    return ledger


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["learn", "digest"])
    ap.add_argument("--home")
    ap.add_argument("--today")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ledger = learn(home_dir(a.home), a.today, dry=a.dry_run or a.cmd == "digest")
    sys.stdout.write(digest(ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
