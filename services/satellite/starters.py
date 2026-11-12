"""Conversation starters: Skippy brings something up himself (the Captain, 2026-10-31: make him feel alive).

Once a day tools/starter_refresh.py gathers candidates (a new breakthrough in one of his interests, filler.TOPICS; a world change that touches him:
an election, a law, something near home), each one sourced from the house's own search, recent, and kept only when a strict second reading of its
sources supports every word of the opener (the way tools/quip_refresh.checked does). They wait in state/starters/candidates.json.

The hub (hub.Room) offers one only when the room is idle and someone is around (a voice heard in the room lately, but not right now: never
over a conversation), nothing plays (no music, no book), in his waking hours (the house log's own record of when he talks to Skippy, once there is
enough of it), at most MAX_PER_DAY a day and GAP_S apart, house-wide. A "no" or "not now" ends them for the day; "never" mutes that subject for a
month; two offers nobody answers end them for the day too (probably nobody there). Every offer and answer is kept in state/starters/offers.json.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

MAX_PER_DAY = 3
GAP_S = 2.5 * 3600
MUTE_S = 30 * 86400
HOURS = (9, 21)                                                 # never before 9 or from 9 pm on, whatever the log says
LEARN_DAYS = 21                                                 # how far back the log says when he is usually around
MIN_LOG_DAYS = 5                                                # fewer days than this: not enough to say, the HOURS window alone decides
IGNORED_LIMIT = 2


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def load(path: Path, default):
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
        return got if isinstance(got, type(default)) else default
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def usual_hours(log_root: Path | None, today: str | None = None, clock=time.time) -> set[int] | None:
    """The hours of the day he talks to Skippy (wakes and commands in the house log over the last LEARN_DAYS), each widened by an hour on both
    sides; None when the log has fewer than MIN_LOG_DAYS days (not enough to say)."""
    if log_root is None:
        return None
    files = sorted((Path(log_root) / "log").glob("*.jsonl"))
    cutoff = time.strftime("%Y-%m-%d", time.localtime(clock() - LEARN_DAYS * 86400))
    days, hours = set(), set()
    for f in files:
        if f.stem < cutoff:
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") in ("wake", "command") and e.get("time"):
                    days.add(e.get("date"))
                    h = int(e["time"][:2])
                    hours.update({(h - 1) % 24, h, (h + 1) % 24})
        except OSError:
            continue
    return hours if len(days) >= MIN_LOG_DAYS else None


class Starters:
    """The candidates and the record of offers, shared by every room (one lock: two rooms never offer at once)."""

    def __init__(self, folder: Path | str, log_root: Path | None = None, clock=time.time):
        self.folder, self.log_root, self.clock = Path(folder), log_root, clock
        self.lock = threading.Lock()
        self._hours_at, self._hours = 0.0, None

    @property
    def candidates_path(self) -> Path:
        return self.folder / "candidates.json"

    @property
    def offers_path(self) -> Path:
        return self.folder / "offers.json"

    def state(self) -> dict:
        s = load(self.offers_path, {})
        s.setdefault("offers", [])
        s.setdefault("muted", {})
        s.setdefault("quiet_until", 0)
        return s

    def candidates(self) -> list[dict]:
        return load(self.candidates_path, {"items": []}).get("items") or []

    def hours(self) -> set[int] | None:
        if self.clock() - self._hours_at > 3600:                 # the log changes slowly: once an hour is plenty
            self._hours_at, self._hours = self.clock(), usual_hours(self.log_root, clock=self.clock)
        return self._hours

    def last_seen(self) -> float | None:
        """When he last spoke to Skippy anywhere (a wake or a command in today's house log), as epoch seconds: the hub's own memory of it is lost
        on every restart, the log is not."""
        if self.log_root is None:
            return None
        path = Path(self.log_root) / "log" / f"{time.strftime('%Y-%m-%d', time.localtime(self.clock()))}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") in ("wake", "command") and e.get("ts"):
                try:
                    return time.mktime(time.strptime(e["ts"], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    return None
        return None

    def why_not(self) -> str | None:
        """Why no starter may be offered now (house-wide), or None when one may."""
        now = self.clock()
        lt = time.localtime(now)
        if not HOURS[0] <= lt.tm_hour < HOURS[1]:
            return "hours"
        usual = self.hours()
        if usual is not None and lt.tm_hour not in usual:
            return "not his usual hour"
        s = self.state()
        if now < s["quiet_until"]:
            return "backed off"
        today = time.strftime("%Y-%m-%d", lt)
        todays = [o for o in s["offers"] if o.get("date") == today]
        if len(todays) >= MAX_PER_DAY:
            return "enough today"
        if s["offers"] and now - s["offers"][-1].get("at", 0) < GAP_S:
            return "too soon"
        return None

    def next(self) -> dict | None:
        """The candidate to offer now, or None: unexpired, never offered, its subject not muted; world news before a breakthrough when it is fresh,
        then the most important, then the newest."""
        if self.why_not() is not None:
            return None
        now, s = self.clock(), self.state()
        said = {o.get("id") for o in s["offers"]} | {norm(o.get("headline", "")) for o in s["offers"]}
        live = [c for c in self.candidates() if c.get("expires", 0) > now and c.get("id") not in said and norm(c.get("headline", "")) not in said
                and s["muted"].get(c.get("topic", ""), 0) < now and c.get("opener")]
        if not live:
            return None
        return max(live, key=lambda c: (int(c.get("importance", 0)), c.get("kind") == "world", c.get("found", 0)))

    def mark_offered(self, cand: dict, room: str) -> None:
        with self.lock:
            s = self.state()
            s["offers"] = (s["offers"] + [{"id": cand["id"], "headline": cand.get("headline", ""), "topic": cand.get("topic", ""), "room": room,
                                          "at": self.clock(), "date": time.strftime("%Y-%m-%d", time.localtime(self.clock())), "answer": None}])[-400:]
            write_json(self.offers_path, s)

    def answer(self, cid: str, answer: str, words: str = "") -> None:
        """yes (he took it up) / no (not now: done for the day) / never (that subject muted for a month) / ignored (nobody answered)."""
        with self.lock:
            s = self.state()
            mine = next((o for o in reversed(s["offers"]) if o.get("id") == cid), None)
            if mine is None:
                return
            mine["answer"], mine["words"] = answer, (words or "")[:200]
            now = self.clock()
            if answer in ("no", "never"):
                s["quiet_until"] = end_of_day(now)
            if answer == "never" and mine.get("topic"):
                s["muted"][mine["topic"]] = now + MUTE_S
            if answer == "ignored":
                today = time.strftime("%Y-%m-%d", time.localtime(now))
                if sum(1 for o in s["offers"] if o.get("date") == today and o.get("answer") == "ignored") >= IGNORED_LIMIT:
                    s["quiet_until"] = end_of_day(now)
            write_json(self.offers_path, s)


def end_of_day(now: float) -> float:
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 59, 59, 0, 0, -1))


def note_of(cand: dict) -> str:
    """The sources of a starter, as the brain's note for the follow-up questions."""
    return "\n".join(f"- {s.get('title', '')} ({s.get('source', '')}): {s.get('content', '')}" for s in cand.get("sources") or [])
