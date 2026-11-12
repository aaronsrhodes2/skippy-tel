"""What does the Captain ask for, how often, and how long is it to say? The measurement behind a chat-first UI (docs/chat-first-ui.md, 2026-11-11):
a control earns its place only when the request it stands for is frequent AND long to say.

    python tools/intent_report.py [--days 30] [--top 12]

Reads (all local, nothing is sent anywhere):
  * taps:   the portal's telemetry (music-collection/state/ui_telemetry.db, site_events kind 'ui'), the Captain's (role admin), without the `seen:` beacons
  * voice:  the house log (skippy-home/log/*.jsonl, kind 'command'): the words he said to a pod and the tools they ran
  * agent:  the private Skippy's conversations (D:/Aaron/conversations/private-skippy/*.jsonl): each message he typed or said to it
A request is normalised (lower case, no fillers, no 'skippy') and grouped with the ones whose word sets overlap by at least 0.6 (Jaccard), greedily:
a measuring stick, not the final grouping (that is skippy-core's model, nightly). Prints, per source: how many requests, how many distinct kinds, how much of
the total the biggest groups cover, and the groups worth a key (3+ times on 2+ days, the typical phrasing 4+ words).
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT.parent / "music-collection" / "state" / "ui_telemetry.db"
HOME_LOG = ROOT.parent / "skippy-home" / "log"
AGENT = Path(r"D:\Aaron\conversations\private-skippy")
FILLER = {"skippy", "please", "hey", "ok", "okay", "the", "a", "an", "to", "me", "my", "can", "you", "could", "would", "i", "want", "some", "just", "now", "for", "and", "it", "this", "that", "on", "in", "of"}
JACCARD = 0.6


def norm(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if w not in FILLER]


def cluster(items: list[dict]) -> list[dict]:
    """items: {text, day, hour}. Greedy grouping by word-set overlap."""
    groups: list[dict] = []
    for it in items:
        ws = set(norm(it["text"]))
        if not ws:
            continue
        best = None
        for g in groups:
            inter = len(ws & g["words"])
            if inter / len(ws | g["words"]) >= JACCARD:
                best = g
                break
        if best is None:
            best = {"words": set(ws), "n": 0, "days": set(), "hours": Counter(), "lens": [], "examples": Counter()}
            groups.append(best)
        best["n"] += 1
        best["days"].add(it["day"])
        best["hours"][it["hour"]] += 1
        best["lens"].append(len(it["text"].split()))
        best["examples"][it["text"].strip().lower()[:90]] += 1
    return sorted(groups, key=lambda g: -g["n"])


def show(title: str, items: list[dict], top: int) -> None:
    total = len(items)
    print(f"\n== {title}: {total} requests")
    if not total:
        return
    groups = cluster(items)
    covered = lambda k: sum(g["n"] for g in groups[:k]) / total * 100
    print(f"   {len(groups)} distinct kinds; the biggest 5 cover {covered(5):.0f}% of them, the biggest 10 cover {covered(10):.0f}%")
    keys = [g for g in groups if g["n"] >= 3 and len(g["days"]) >= 2 and sum(g["lens"]) / len(g["lens"]) >= 4]
    print(f"   worth a key (3+ times on 2+ days, typically 4+ words): {len(keys)}")
    for g in groups[:top]:
        eg = g["examples"].most_common(1)[0][0]
        hrs = ", ".join(f"{h:02d}h" for h, _ in g["hours"].most_common(2))
        flag = "KEY" if g in keys else "   "
        print(f"   {flag} {g['n']:4d}x on {len(g['days']):2d} days, ~{sum(g['lens']) / len(g['lens']):4.1f} words, mostly {hrs}:  {eg}")


def load_voice(cutoff: float) -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(HOME_LOG / "*.jsonl"))):
        for line in open(f, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "command" and e.get("words") and e["ts"] >= time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(cutoff)):
                out.append({"text": e["words"], "day": e["date"], "hour": int(e["time"][:2])})
    return out


def load_agent(cutoff: float) -> list[dict]:
    out, seen = [], set()
    for f in sorted(glob.glob(str(AGENT / "*.jsonl"))):
        for line in open(f, encoding="utf-8", errors="replace"):
            if '"type":"user"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = (e.get("message") or {}).get("content")
            if isinstance(msg, list):                                  # blocks: his words are the text ones (a tool_result block is the agent's own plumbing)
                parts = [b.get("text", "") for b in msg if isinstance(b, dict) and b.get("type") == "text"]
                msg = " ".join(parts) if parts and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in msg) else None
            pid = e.get("promptId") or e.get("uuid")
            if not isinstance(msg, str) or not pid or pid in seen or msg.startswith(("<", "[Request", "Caveat")):
                continue
            seen.add(pid)
            ts = e.get("timestamp", "")
            if ts[:10] and time.mktime(time.strptime(ts[:10], "%Y-%m-%d")) >= cutoff:
                out.append({"text": msg[:300], "day": ts[:10], "hour": int(ts[11:13] or 0)})
    return out


def taps(cutoff: float, top: int) -> None:
    db = sqlite3.connect(DB)
    rows = db.execute("select name, count(*), count(distinct date(ts,'unixepoch')) from site_events where kind='ui' and role='admin' and ts>=? "
                      "and name not like 'seen:%' group by name order by 2 desc", (cutoff,)).fetchall()
    seen = {r[0][5:] for r in db.execute("select distinct name from site_events where kind='ui' and name like 'seen:%' and ts>=?", (cutoff,))}
    tapped = {r[0] for r in rows}
    never = sorted(seen - tapped)
    total = sum(r[1] for r in rows)
    print(f"\n== taps (the Captain, controls only): {total} taps on {len(rows)} different controls; {len(never)} controls were on screen and never tapped")
    run = 0
    for name, n, days in rows[:top]:
        run += n
        print(f"   {n:5d}x on {days:2d} days  ({run / total * 100:3.0f}% cumulative)  {name}")
    k80 = next((i + 1 for i in range(len(rows)) if sum(r[1] for r in rows[:i + 1]) >= total * 0.8), len(rows))
    print(f"   80% of all taps are on {k80} controls; the other {len(rows) - k80} share the last 20%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    cutoff = time.time() - a.days * 86400
    print(f"last {a.days} days")
    taps(cutoff, a.top)
    show("voice to the pods", load_voice(cutoff), a.top)
    show("typed or said to the private Skippy", load_agent(cutoff), a.top)


if __name__ == "__main__":
    main()
