"""The nightly learner: reads skippy-home/log/*.jsonl and writes skippy-home/routines/<id>.json, one per weekday-aware pattern, each with its evidence.

Deterministic and conservative, so the numbers can be trusted whatever model writes the notes beside them:
  * a pattern needs MIN_DAYS different dates on the same weekday whose times fall inside one 2 x WINDOW_MIN span;
  * confidence = min(1, n/5) x (n / days that weekday was seen at all) x (1 - half the scatter): three identical Wednesdays out of three seen is 0.6,
    three out of five is 0.36, so a thin or patchy record never reads as sure;
  * kinds: wake (first wake of the day), light (a lamp or plug turned on/off by name), music (play_music by words), bedtime (last event of the day, 20:00 or later).
Nothing leaves this machine. `learn --dry-run` prints what it would write.

    python routines.py learn [--dry-run] [--home DIR] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

MIN_DAYS = 3
WINDOW_MIN = 20
PROPOSE_AT = 0.55
KINDS_AUTOMATABLE = {"light"}
BEDTIME_AFTER = 20 * 60


def hhmm(minutes: float) -> str:
    m = int(round(minutes)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def to_min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "x"


def load_events(root: Path) -> list[dict]:
    out = []
    for path in sorted((Path(root) / "log").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue                                        # a half-written line is skipped, never fatal
            if isinstance(rec, dict) and rec.get("date") and rec.get("time") and rec.get("weekday"):
                out.append(rec)
    return out


def samples(events: list[dict], today: str | None) -> tuple[dict, dict]:
    """({(kind, key, weekday): {date: minutes}}, {weekday: set of dates seen}). One sample per date: the first of the day, or the last for bedtime."""
    found: dict = defaultdict(dict)
    seen: dict = defaultdict(set)
    last_of_day: dict = {}
    for e in sorted(events, key=lambda r: r.get("ts", "")):
        d, wd, m = e["date"], e["weekday"], to_min(e["time"])
        seen[wd].add(d)
        last_of_day[(wd, d)] = m
        if e["kind"] == "wake":
            found[("wake", "", wd)].setdefault(d, m)
        elif e["kind"] == "action" and e.get("what") == "home" and e.get("act") in ("on", "off") and e.get("target"):
            found[("light", f"{e['target'].lower()}|{e['act']}", wd)].setdefault(d, m)
        elif e["kind"] == "action" and e.get("tool") == "play_music" and e.get("target"):
            found[("music", e["target"].lower(), wd)].setdefault(d, m)
    for (wd, d), m in last_of_day.items():
        if m >= BEDTIME_AFTER and d != today:
            found[("bedtime", "", wd)][d] = m
    return found, seen


def densest(points: dict) -> dict:
    """The most dates whose times sit inside one 2 x WINDOW_MIN span ({date: minutes})."""
    items = sorted(points.items(), key=lambda kv: kv[1])
    best: list = []
    for i in range(len(items)):
        group = [kv for kv in items[i:] if kv[1] - items[i][1] <= 2 * WINDOW_MIN]
        if len(group) > len(best):
            best = group
    return dict(best)


def learn(root: Path, today: str | None = None, now: float | None = None) -> list[dict]:
    found, seen = samples(load_events(root), today)
    routines = []
    for (kind, key, wd), points in sorted(found.items()):
        group = densest(points)
        n = len(group)
        if n < MIN_DAYS:
            continue
        times = sorted(group.values())
        spread = times[-1] - times[0]
        observed = len(seen[wd])
        conf = round(min(1, n / 5) * (n / max(n, observed)) * (1 - 0.5 * spread / (2 * WINDOW_MIN)), 2)
        median = statistics.median(times)
        dates = sorted(group)
        target, _, act = key.partition("|")
        routines.append({
            "id": f"{kind}-{slug(key) + '-' if key else ''}{wd.lower()}", "kind": kind, "key": key, "target": target or None, "act": act or None, "weekday": wd,
            "time": hhmm(round(median / 5) * 5), "median_time": hhmm(median), "spread_min": spread, "n": n, "observed_days": observed, "confidence": conf,
            "first_date": dates[0], "last_date": dates[-1], "dates": dates, "times": {d: hhmm(group[d]) for d in dates},
            "automatable": kind in KINDS_AUTOMATABLE, "updated": time.strftime("%Y-%m-%d", time.localtime(now or time.time()))})
    return routines


def describe(r: dict) -> str:
    what = {"wake": "first wake of the day", "bedtime": "last word of the evening", "music": f"music: {r['key']}",
            "light": f"{r['target']} turned {r['act']}"}[r["kind"]]
    return (f"{r['weekday']}s around {r['time']}: {what} - {r['n']} of {r['observed_days']} {r['weekday']}s seen, "
            f"{r['first_date']} to {r['last_date']}, scatter {r['spread_min']} min, confidence {r['confidence']}")


def write(root: Path, routines: list[dict]) -> list[str]:
    folder = Path(root) / "routines"
    folder.mkdir(parents=True, exist_ok=True)
    keep = {f"{r['id']}.json" for r in routines}
    for old in folder.glob("*.json"):
        if old.name not in keep:
            old.unlink()                                        # the learner owns this folder: a pattern that no longer holds is gone (git keeps it)
    for r in routines:
        (folder / f"{r['id']}.json").write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    lines = ["# Learned routines", "", f"{len(routines)} pattern(s). Evidence in each routine's JSON. Generated by routines.py learn; do not edit.", ""]
    lines += [f"- {describe(r)}" for r in sorted(routines, key=lambda r: -r["confidence"])]
    (folder / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sorted(keep)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("learn")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--home", default=None)
    p.add_argument("--today", default=None)
    args = ap.parse_args(argv)
    from housemem import home_dir                               # noqa: PLC0415
    root = Path(args.home) if args.home else home_dir()
    events = load_events(root)
    routines = learn(root, today=args.today or time.strftime("%Y-%m-%d"))
    print(f"{len(events)} event(s) in {root / 'log'}; {len(routines)} routine(s) hold (each seen on at least {MIN_DAYS} different days)")
    for r in sorted(routines, key=lambda r: -r["confidence"]):
        print("  " + describe(r))
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    write(root, routines)
    from proposals import Workspace                             # noqa: PLC0415
    made = Workspace(root).refresh(routines)
    print(f"wrote routines/ ; {len(made)} new proposal(s) waiting to be asked: {', '.join(made) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
