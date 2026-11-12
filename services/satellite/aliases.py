"""Learned phrases: the house repairs its own vocabulary misses (the Captain, 2026-11-11: "house chat should be able to fix its own bugs").

The miss ledger (misses.py) finds an order the house failed at ("turn off the upstairs lights" -> only looked, did nothing). When the SAME words later went through clean
(a tool that acts ran, and nothing about it was corrected or repeated), that exact phrase becomes an alias: next time the hub runs that tool call at once, before the model
(the same place as the hand-written routes in brain.py: "the tool runs first, the model only words the answer"). Data, not code: skippy-home/aliases.json, rebuilt nightly.

Guards, because a wrong alias is worse than a miss:
  * exact phrase only (the words, lower case, without "skippy" / "please"), at least two words; nothing fuzzy ever runs a tool
  * only a phrase that MISSED before and then worked: aliases repair known misses, they are not a general cache
  * only safe tools: lights and switches (on / off / toggle / brightness, never a lock, door, garage, alarm or anything with a heat), the music tools, speakers
  * the call is the one that worked (its arguments are what the log recorded); an alias whose phrase is later repeated, corrected or failed is switched off and stays off
  * SATELLITE_ALIASES=off ignores the file; deleting an entry (or the file) undoes it

    python aliases.py learn [--dry-run] [--home DIR]     (misses.py learn calls it; run by hand to look)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

SAFE_ACTIONS = {"home_control": {"on", "off", "toggle", "set_brightness"}, "music": {"pause", "resume", "next", "previous", "louder", "quieter", "volume"},
                "play_music": None, "speakers": None, "dj_mode": None}               # None: any arguments
SENSITIVE = re.compile(r"\b(lock|unlock|garage|door|alarm|security|arm|disarm|generator|furnace|oven|stove|heater|thermostat)\b", re.I)
FRONT = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:skippy\s+|bilby\s+)?(?:(?:can|could|would|will) you\s+)?(?:please\s+)?")
MIN_WORDS = 2
MAX_ALIASES = 200


def norm_phrase(text: str) -> str:
    t = " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))
    t = FRONT.sub("", t).strip()
    return re.sub(r"\s+please$", "", t).strip()


def safe_call(call: dict) -> dict | None:
    """The one call an alias may run (scalar arguments, a safe tool and action), or None."""
    tool = str(call.get("tool") or "")
    if tool not in SAFE_ACTIONS:
        return None
    args = {k: v for k, v in (call.get("args") or {}).items() if k != "confirm" and isinstance(v, (str, int, float, bool))}
    allowed = SAFE_ACTIONS[tool]
    if allowed is not None and str(args.get("action") or "") not in allowed:
        return None
    if tool == "home_control" and SENSITIVE.search(str(args.get("name") or "")):
        return None
    return {"tool": tool, "args": args}


def learn(cmds: list[dict], acts: list[dict], brain, misses, previous: dict | None = None, today: str | None = None) -> dict:
    """The alias table from the whole log (idempotent). `previous`: the table as it was, so a switched-off alias stays off."""
    events = sorted(cmds, key=misses.epoch)
    off = {a["phrase"]: a for a in (previous or {}).get("aliases", []) if a.get("disabled")}
    missed_at: dict[str, float] = {}
    table: dict[str, dict] = {}
    for i, e in enumerate(events):
        nxt = next((x for x in events[i + 1:i + 3] if x.get("room") == e.get("room")), None)
        sig = misses.signals(e, nxt, acts, brain)
        phrase = norm_phrase(str(e.get("words") or ""))
        if len(phrase.split()) < MIN_WORDS:
            continue
        a = table.get(phrase)
        if sig:
            missed_at[phrase] = misses.epoch(e)
            if a and misses.epoch(e) > a["_since"] and not a.get("disabled"):         # it ran (or would have) and was repeated, corrected or failed: off, for good
                a["disabled"] = f"{sorted(sig)[0]} on {e.get('date')}"
            continue
        calls = [c for c in e.get("calls") or [] if isinstance(c, dict)]
        if a is not None or phrase not in missed_at or len(calls) != 1 or not misses.acted(e, acts):
            continue
        call = safe_call(calls[0])
        if call is None or not brain.order_shaped(str(e.get("words") or "")):
            continue
        table[phrase] = {"phrase": phrase, **call, "learned": str(e.get("date")), "missed_before": True, "_since": misses.epoch(e), "disabled": None}
    for phrase, was in off.items():                                                   # what was switched off stays off, even if the phrase works again
        if phrase in table:
            table[phrase]["disabled"] = was["disabled"]
    out = sorted(table.values(), key=lambda a: a["phrase"])[:MAX_ALIASES]
    for a in out:
        a.pop("_since", None)
    return {"built": today or time.strftime("%Y-%m-%d"), "aliases": out}


class AliasStore:
    """What the hub reads: aliases.json, reloaded when the file changes. `route(heard, available)` -> (tool, args) or None."""

    def __init__(self, path: Path, enabled: bool = True):
        self.path, self.enabled = Path(path), enabled
        self._stamp = None
        self._table: dict[str, dict] = {}

    def _load(self) -> None:
        try:
            stamp = self.path.stat().st_mtime
        except OSError:
            self._stamp, self._table = None, {}
            return
        if stamp == self._stamp:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._table = {a["phrase"]: a for a in data.get("aliases", []) if isinstance(a, dict) and a.get("phrase") and not a.get("disabled")}
        except (OSError, ValueError, KeyError, AttributeError):
            self._table = {}
        self._stamp = stamp

    def route(self, heard: str, available) -> tuple[str, dict] | None:
        if not self.enabled:
            return None
        self._load()
        a = self._table.get(norm_phrase(heard))
        if a is None or a.get("tool") not in available:
            return None
        call = safe_call(a)                                                          # re-checked on every use: a hand-edited file cannot widen what an alias may do
        return (call["tool"], dict(call["args"])) if call else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["learn"])
    ap.add_argument("--home")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import brain                                                                     # noqa: PLC0415
    import misses                                                                    # noqa: PLC0415
    home = misses.home_dir(a.home)
    path = home / "aliases.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    cmds, acts = misses.load(home)
    table = learn(cmds, acts, brain, misses, prev)
    if not a.dry_run:
        path.write_text(json.dumps(table, indent=1, ensure_ascii=False), encoding="utf-8")
    for al in table["aliases"]:
        print(("OFF " if al["disabled"] else "ON  ") + f"\"{al['phrase']}\" -> {al['tool']} {al['args']} (learned {al['learned']}{', ' + al['disabled'] if al['disabled'] else ''})")
    print(f"{len(table['aliases'])} aliases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
