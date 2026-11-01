"""Alarms and reminders at a time of day, the Echo's other everyday job besides timers ("wake me at 7", "every weekday at 6:30",
"remind me at 5 to call Mom", "remind me in 20 minutes to check the oven"). Kept on disk per room (state/alarms/<room>.json), so a hub
restart forgets nothing. An alarm rings in the room that set it until he answers (any word to that room stops it; "snooze" puts it off
nine minutes), at most RINGS times a minute apart; a reminder is said twice, a minute apart, unless he answers the first."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
REPEATS = {"once": [], "daily": DAYS, "weekdays": DAYS[:5], "weekends": DAYS[5:]}
RINGS = {"alarm": 5, "reminder": 2}
RING_GAP_S = 60
SNOOZE_S = 9 * 60
MAX = 40
WAKE_HOURS = (3, 12)              # an alarm for 03:00-11:59 is a wake-up: it rings where he sleeps unless he said where
DISMISS = set("ok okay alright all right stop it off alarm reminder turn i'm im i am up awake thanks thank you got enough shut quiet cancel dismiss "
              "yes yeah yep sure fine skippy the that please now already".split())    # a reply made only of these words just stops it

TOOLS = [
    {"name": "set_alarm", "description": "Set an alarm at a time of day ('wake me at 7', 'alarm for 6:30 every weekday'). It rings until he "
                                         "answers (stop or snooze, aloud or on his phone); a morning one rings upstairs where he sleeps. For a length of time ('in 20 minutes') use set_timer instead.",
     "input_schema": {"type": "object", "properties": {
         "time": {"type": "string", "description": "24-hour HH:MM, e.g. 07:00 or 18:30"},
         "date": {"type": "string", "description": "YYYY-MM-DD for a one-off on a given day; leave out for the next time that clock time comes round"},
         "repeat": {"type": "string", "description": "once (default), daily, weekdays, weekends, or days like 'mon,wed,fri'"},
         "label": {"type": "string", "description": "what it is for, if he said"},
         "where": {"type": "string", "description": "where it rings, if he said: here, upstairs, downstairs, everywhere, or a room ('in the bedroom'). "
                                                   "Leave out: a morning alarm rings upstairs where he sleeps, any other in this room"}}, "required": ["time"]}},
    {"name": "set_reminder", "description": "Remind him of something at a time ('remind me at 5 to call Mom', 'remind me in 20 minutes to check the oven', "
                                            "'every Monday at 8 remind me to take the bins out'). Said aloud in this room.",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "what to remind him of, in his words (e.g. 'call Mom')"},
         "time": {"type": "string", "description": "24-hour HH:MM"}, "date": {"type": "string", "description": "YYYY-MM-DD, if not the next time that clock time comes round"},
         "in_minutes": {"type": "integer", "description": "instead of time: this many minutes from now"},
         "repeat": {"type": "string", "description": "once (default), daily, weekdays, weekends, or days like 'mon'"}}, "required": ["text"]}},
    {"name": "list_alarms", "description": "The alarms and reminders that are set, and when each goes off.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "cancel_alarm", "description": "Cancel alarms or reminders: by what they are for or their time ('the 7 o'clock alarm', 'the Mom reminder'), "
                                            "or every one of a kind when 'what' is blank.",
     "input_schema": {"type": "object", "properties": {"what": {"type": "string"}, "kind": {"type": "string", "enum": ["alarm", "reminder", "any"]}}}},
]


@dataclass
class Alarm:
    id: int
    kind: str                     # "alarm" | "reminder"
    due: float                    # wall-clock seconds of the next time it goes off
    hhmm: str                     # "07:00": when a repeating one comes back
    days: list[str] = field(default_factory=list)   # [] = once
    label: str = ""               # an alarm's purpose, a reminder's words
    where: str = ""               # where it rings: a place in his words ("upstairs", "everywhere", "bedroom"); "" = the room that set it
    room: str = ""                # the hub room it was set in ("theater"; "" = the portal's Alarms page or an old file)


def parse_days(repeat: str) -> list[str]:
    r = (repeat or "once").strip().lower()
    if r in REPEATS:
        return list(REPEATS[r])
    days = [d for d in DAYS if re.search(rf"\b{d}", r)]
    if not days:
        raise ValueError(f"I don't know the repeat {repeat!r}: once, daily, weekdays, weekends or days like mon,wed")
    return days


def parse_hhmm(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2})(?::?(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*", (text or "").lower())
    if not m:
        raise ValueError(f"I can't read the time {text!r} (24-hour HH:MM)")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "")[:1]
    if ap == "p" and h < 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"{text!r} is not a time of day")
    return h, mi


def next_at(hhmm: str, days: list[str], after: float, date: str = "") -> float:
    """The first moment after `after` (local time) at `hhmm`, on `date` or one of `days` (none: any day)."""
    h, mi = parse_hhmm(hhmm)
    if date:
        t = time.mktime(time.strptime(f"{date} {h:02d}:{mi:02d}", "%Y-%m-%d %H:%M"))
        if t <= after:
            raise ValueError(f"{date} at {h:02d}:{mi:02d} has already gone by")
        return t
    lt = time.localtime(after)
    for add in range(0, 8):
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + add, h, mi, 0, 0, 0, -1))
        if t > after and (not days or DAYS[time.localtime(t).tm_wday] in days):
            return t
    raise ValueError("no such day")


def say_when(t: float, now: float) -> str:
    """'7 AM', 'tomorrow at 6:30 PM', 'Friday at 9 AM', 'November 14 at 8 AM'."""
    lt, ln = time.localtime(t), time.localtime(now)
    clock = time.strftime("%I:%M %p", lt).lstrip("0").replace(":00 ", " ")
    days = (time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1)) - time.mktime((ln.tm_year, ln.tm_mon, ln.tm_mday, 12, 0, 0, 0, 0, -1))) / 86400
    days = round(days)
    if days == 0:
        return clock
    if days == 1:
        return f"tomorrow at {clock}"
    if days < 7:
        return f"{time.strftime('%A', lt)} at {clock}"
    return f"{time.strftime('%B', lt)} {lt.tm_mday} at {clock}"


def say_days(days: list[str]) -> str:
    for name, ds in REPEATS.items():
        if ds and sorted(days) == sorted(ds):
            return {"daily": "every day", "weekdays": "every weekday", "weekends": "every weekend day"}[name]
    full = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
    return "every " + " and ".join(full[d] for d in days)


class Alarms:
    def __init__(self, path: Path | str | None = None, clock=time.time, log=print, wake_place: str = ""):
        self.path = Path(path) if path else None
        self.clock, self.log = clock, log
        self.wake_place = wake_place              # where a wake-up alarm rings when he doesn't say (alarm_clock.py: "upstairs"); "" = where it was set
        self.items: list[Alarm] = []
        self.ringing: dict | None = None          # {"alarm": Alarm, "left": n, "next": t}: going off now, until he answers
        self._load()

    # ── on disk ──
    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            self.items = [Alarm(**a) for a in json.loads(self.path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError) as e:
            self.log(f"(alarms: {self.path} unreadable, starting empty: {e})")
            return
        now = self.clock()
        for a in list(self.items):                # missed while the hub was down: a repeating one moves on; a one-off within the hour still goes off
            if a.due < now - 3600:
                if a.days:
                    a.due = next_at(a.hhmm, a.days, now)
                else:
                    self.items.remove(a)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(a) for a in self.items], indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            self.log(f"(alarms: could not save: {e})")

    # ── setting ──
    def add(self, kind: str, hhmm: str = "", label: str = "", repeat: str = "once", date: str = "", in_minutes: int | None = None,
            where: str | None = None, room: str = "") -> Alarm:
        if len(self.items) >= MAX:
            raise ValueError("that is a lot of alarms already")
        now = self.clock()
        if in_minutes is not None:
            if not 1 <= int(in_minutes) <= 7 * 24 * 60:
                raise ValueError("between a minute and a week from now")
            due = now + 60 * int(in_minutes)
            hhmm, days = time.strftime("%H:%M", time.localtime(due)), []
        else:
            if not hhmm:
                raise ValueError("say when: a time of day, or in how many minutes")
            h, mi = parse_hhmm(hhmm)
            hhmm, days = f"{h:02d}:{mi:02d}", parse_days(repeat)
            due = next_at(hhmm, days, now, "" if days else (date or ""))
        if where is None:                         # not said: a wake-up alarm rings where he sleeps (WAKE_PLACE), anything else where it was set
            where = self.wake_place if kind == "alarm" and WAKE_HOURS[0] <= int(hhmm[:2]) < WAKE_HOURS[1] else ""
        where = "" if str(where).strip().lower() in ("", "here", "this room") else str(where).strip().lower()[:40]
        a = Alarm(id=max((x.id for x in self.items), default=0) + 1, kind=kind, due=due, hhmm=hhmm, days=days, label=(label or "").strip()[:120],
                  where=where, room=room)
        self.items.append(a)
        self._save()
        return a

    def cancel(self, what: str = "", kind: str = "any") -> int:
        w = (what or "").lower().strip()
        hit = []
        for a in self.items:
            if kind in ("alarm", "reminder") and a.kind != kind:
                continue
            if w:
                words = f"{a.label} {a.hhmm} {say_when(a.due, self.clock())}".lower()
                try:
                    h, mi = parse_hhmm(re.sub(r"\b(o'?clock|alarm|reminder|the)\b", "", w))
                    if a.hhmm != f"{h:02d}:{mi:02d}":
                        continue
                except ValueError:
                    if not any(t in words for t in re.findall(r"[a-z0-9]+", w) if t not in ("the", "alarm", "reminder", "my", "for", "to")):
                        continue
            hit.append(a)
        self.items = [a for a in self.items if a not in hit]
        if self.ringing and self.ringing["alarm"] in hit:
            self.ringing = None
        self._save()
        return len(hit)

    def cancel_id(self, alarm_id: int) -> bool:
        before = len(self.items)
        self.items = [a for a in self.items if a.id != int(alarm_id)]
        self._save()
        return len(self.items) != before

    def snooze(self, a: Alarm, minutes: int = SNOOZE_S // 60) -> Alarm:
        """A one-off copy of `a`, `minutes` from now, ringing in the same place."""
        return self.add(a.kind, label=a.label, in_minutes=minutes, where=a.where, room=a.room)

    def listing(self) -> str:
        now = self.clock()
        if not self.items:
            return "No alarms or reminders are set."
        parts = []
        for a in sorted(self.items, key=lambda a: a.due):
            when = say_when(a.due, now) + (f", {say_days(a.days)}" if a.days else "") + (f", {a.where}" if a.where else "")
            parts.append(f"alarm {when}" + (f" ({a.label})" if a.label else "") if a.kind == "alarm" else f"reminder {when}: {a.label}")
        return "; ".join(parts) + "."

    # ── going off ──
    def due(self) -> list[Alarm]:
        """The ones whose time has come (a repeating one is moved to its next day, a one-off is dropped); the last of them starts ringing."""
        now = self.clock()
        fired = [a for a in self.items if a.due <= now]
        if not fired:
            return []
        for a in fired:
            if a.days:
                a.due = next_at(a.hhmm, a.days, now + 1)
            else:
                self.items.remove(a)
        self._save()
        last = fired[-1]
        self.ringing = {"alarm": last, "left": RINGS[last.kind] - 1, "next": now + RING_GAP_S}
        return fired

    def ring_again(self) -> Alarm | None:
        """Still unanswered and a minute gone: the alarm to say once more (None: nothing ringing, or not yet)."""
        r = self.ringing
        if r is None or self.clock() < r["next"]:
            return None
        if r["left"] <= 0:
            self.ringing = None
            return None
        r["left"] -= 1
        r["next"] = self.clock() + RING_GAP_S
        return r["alarm"]

    def answered(self, words: str) -> str | None:
        """He spoke while one rang: it stops. 'snooze' puts it off nine minutes. The reply to say, or None when it was only stopped quietly
        by a request for something else (the brain answers that)."""
        r, self.ringing = self.ringing, None
        if r is None:
            return None
        a = r["alarm"]
        w = (words or "").lower()
        if re.search(r"\bsnooze\b|five more minutes|ten more minutes|let me sleep", w):
            s = self.snooze(a)
            return f"Fine. Snoozed until {say_when(s.due, self.clock())}. Nine minutes, not a second more."
        said = re.findall(r"[a-z']+", w)
        if said and all(t in DISMISS for t in said):
            return "Alarm off." if a.kind == "alarm" else "Okay."
        return None

    def spoken(self, a: Alarm) -> str:
        if a.kind == "reminder":
            return f"Reminder: {a.label}."
        lt = time.localtime(self.clock())
        hello = "Good morning. " if WAKE_HOURS[0] <= lt.tm_hour < WAKE_HOURS[1] else ""
        clock = f"{lt.tm_hour % 12 or 12}:{lt.tm_min:02d}" + ("" if hello else (" PM" if lt.tm_hour >= 12 else " AM"))
        return f"{hello}It's {clock}. " + (f"Your {a.label} alarm. " if a.label else "This is your alarm. ") + "Say stop, or snooze."

    # ── the brain's tools ──
    def run(self, name: str, args: dict, room: str = "") -> str:
        args = args or {}
        try:
            if name == "set_alarm":
                a = self.add("alarm", str(args.get("time") or ""), str(args.get("label") or ""), str(args.get("repeat") or "once"), str(args.get("date") or ""),
                             where=str(args["where"]) if args.get("where") else None, room=room)
                return (f"Alarm set for {say_when(a.due, self.clock())}" + (f", {say_days(a.days)}" if a.days else "") + (f", ringing {a.where}" if a.where else "")
                        + (f" ({a.label})." if a.label else "."))
            if name == "set_reminder":
                text = str(args.get("text") or "").strip()
                if not text:
                    return "Not set: remind him of what?"
                mins = args.get("in_minutes")
                a = self.add("reminder", str(args.get("time") or ""), text, str(args.get("repeat") or "once"), str(args.get("date") or ""),
                             in_minutes=int(mins) if mins not in (None, "") else None, room=room)
                return f"Reminder set for {say_when(a.due, self.clock())}" + (f", {say_days(a.days)}" if a.days else "") + f": {a.label}."
            if name == "list_alarms":
                return self.listing()
            if name == "cancel_alarm":
                n = self.cancel(str(args.get("what") or ""), str(args.get("kind") or "any"))
                return f"Cancelled {n}." if n else "Nothing matched, so nothing was cancelled. " + self.listing()
        except (ValueError, TypeError, OverflowError) as e:
            return f"Not set: {e}"
        return "I have no such tool."

    def tools(self, room: str = ""):
        return TOOLS, (lambda name, args: self.run(name, args, room=room))
