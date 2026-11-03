"""The portal's nightly tidy, skippy-pc's half (portal/adaptive.py is the other): read the portal's telemetry (flask-api's SQLite,
filled by the home reporter), sum up the last WINDOW_DAYS per control, and hand it to the portal's /home/adaptive, which prunes,
reorders or suggests merges and says what it did. tools/nightly.py runs it and puts the changes in the overnight report.

    python tools/adaptive_ui.py            # summarise and send; prints what the portal changed
    python tools/adaptive_ui.py --show     # summarise only: what would be sent (nothing changes)
    python tools/adaptive_ui.py --approve key-attach key-tools    # also retire these now (the Captain said so in chat)
    python tools/adaptive_ui.py --graveyard [words]  # BEFORE adding a control: is one like it retired? (revive it instead)

What it sends is counts per control (taps, days on screen, days since the last tap), the days each surface was in use, and how
often one control's tap follows another's: no people, no pages beyond the surface they belong to.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("UI_TELEMETRY_DB", ROOT.parent / "music-collection" / "state" / "ui_telemetry.db"))
VAULT_ENV = Path(os.environ.get("SKIPPY_VAULT_ENV", r"G:\My Drive\vault\.env"))
PORTAL = os.environ.get("PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app").rstrip("/")
WINDOW_DAYS = 30
PAIR_S = 15                         # a tap this soon after another one counts as following it
# the pages each surface is on screen with: the chat's keys whenever the chat is open (/ is the chat for anyone with it), the launcher
# on /apps (and its apps in the chat's tray), the dashboard's panes on /me
SURFACE_PAGES = {"chat": ("/", "/chat"), "launcher": ("/apps", "/"), "me": ("/me",)}


def vault_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in VAULT_ENV.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip("\"'")
    except OSError:
        pass
    return ""


def day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def summarise(con: sqlite3.Connection, now: float | None = None, window: int = WINDOW_DAYS) -> dict:
    now = time.time() if now is None else now
    since = now - window * 86400
    first = con.execute("SELECT MIN(ts) FROM site_events").fetchone()[0]
    controls: dict[str, dict] = defaultdict(lambda: {"taps": 0, "seen_days": 0, "last_days": None})
    seen_days: dict[str, set] = defaultdict(set)
    rows = con.execute("SELECT ts, who, name FROM site_events WHERE kind = 'ui' AND ts >= ? ORDER BY who, ts", (since,)).fetchall()
    pairs: Counter = Counter()
    prev: tuple | None = None
    for ts, who, name in rows:
        if name.startswith("seen:"):
            seen_days[name[5:]].add(day(ts))
            continue
        c = controls[name]
        c["taps"] += 1
        ago = round((now - ts) / 86400, 1)
        c["last_days"] = ago if c["last_days"] is None else min(c["last_days"], ago)
        if prev and prev[1] == who and ts - prev[0] <= PAIR_S and prev[2] != name:
            pairs[(prev[2], name)] += 1
        prev = (ts, who, name)
    for name, days in seen_days.items():
        controls[name]["seen_days"] = len(days)
    surface_days = {}
    for surface, pages in SURFACE_PAGES.items():
        marks = ",".join("?" * len(pages))
        days = {day(r[0]) for r in con.execute(f"SELECT ts FROM site_events WHERE kind = 'page' AND ts >= ? AND name IN ({marks})",
                                                (since, *pages))}
        surface_days[surface] = len(days)
    return {"window_days": window, "span_days": round((now - first) / 86400, 1) if first else 0,
            "controls": dict(controls), "surface_days": surface_days,
            "pairs": [[a, b, n] for (a, b), n in pairs.most_common(40)]}


def send(stats: dict, approved: list[str]) -> dict:
    token = vault_value("SKIPPY_HOME_TOKEN")
    if not token:
        raise SystemExit("no SKIPPY_HOME_TOKEN in the vault")
    req = urllib.request.Request(f"{PORTAL}/home/adaptive", data=json.dumps({"stats": stats, "approved": approved}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def graveyard() -> dict:
    token = vault_value("SKIPPY_HOME_TOKEN")
    if not token:
        raise SystemExit("no SKIPPY_HOME_TOKEN in the vault")
    req = urllib.request.Request(f"{PORTAL}/home/graveyard", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def matches(g: dict, words_: list[str]) -> list[dict]:
    """The retired controls whose id, label or glyph has any of the words (all of them when no words)."""
    want = [w.lower() for w in words_]
    return [m for m in g.get("retired") or []
            if not want or any(w in f"{m['id']} {m['label']} {m['glyph']}".lower() for w in want)]


def words(changes: list[dict]) -> str:
    """One line for the overnight report ("" when nothing changed)."""
    said = {"retire": "retired", "order": "moved up", "merge": "could merge"}
    return "; ".join(f"{said.get(c['kind'], c['kind'])} {c['label']} ({c['why']})" for c in changes)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--show", action="store_true", help="summarise only, send nothing")
    ap.add_argument("--approve", nargs="*", default=[], help="module ids to retire now (key-attach, key-tools, ...)")
    ap.add_argument("--graveyard", nargs="*", metavar="WORD", help="list retired controls (matching any WORD): look before adding one")
    a = ap.parse_args(argv)
    if a.graveyard is not None:
        g = graveyard()
        found = matches(g, a.graveyard)
        for m in found:
            why = next((e["why"] for e in g.get("ledger") or [] if e.get("id") == m["id"] and e.get("kind") == "retire"), "retired by hand")
            print(f"{m['id']:<22} {m['label']}  ({why})")
        if not found:
            print("nothing like that in the Graveyard: adding it is fine" if a.graveyard else "the Graveyard is empty")
        else:
            print("revive one of these (Admin > Graveyard) instead of adding a new control, if it does the job")
        return 0
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    stats = summarise(con)
    if a.show:
        print(json.dumps(stats, indent=1))
        return 0
    out = send(stats, a.approve)
    changes = out.get("changes") or []
    print(words(changes) or "the portal's controls: nothing to change tonight")
    return 0


if __name__ == "__main__":
    sys.exit(main())
