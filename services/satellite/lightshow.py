"""Light shows: routines that drive the colour lights by themselves (the house layer itself only switches and dims).

Two routines (2026-11-06), each bulb on its OWN random course, never close to where another bulb is heading, every change faded by the bulb itself (Matter's
transition time: a command now and then, not a stream):

  storm     deep blue to cool white, a roiling stormy sky. Each bulb picks its next look at random (how white, which blue, how bright, how long to take,
            how long to hold), fades there in a few seconds, now and then a quick surge. First a 6500 K white, so the white end is cold, never warm.
  fortress  deep purple and violet, so slow you would hardly notice it is changing: each bulb drifts a few degrees and a few points of brightness over
            4 to 11 minutes (the first fade into it, from another routine, takes 4 to 7 minutes). Never bright.

A show is a plan: one routine, or one for a time and then another (`--for 5h --then fortress`: the switch happens by itself, the bulbs fading from wherever
they are). It may wait for its time (`--at 18:30`). A bulb that is off is left alone, and the show ends by itself when every bulb is off ("turn off the
lights" ends it); stopping it puts each bulb back exactly as it was (colour, level, power). One show at a time: a new `show` while one is on takes over in place.

    ./.venv/Scripts/python homectl.py show storm --for 5h --then fortress --at 18:30    wait for 18:30, the storm for 5 hours, then the fortress for ever
    ./.venv/Scripts/python homectl.py show storm                                       the storm now, until stopped
    ./.venv/Scripts/python homectl.py show fortress                                    the fortress now (while a show is on: switch to it, in place)
    ./.venv/Scripts/python homectl.py show status
    ./.venv/Scripts/python homectl.py show stop                                        ends it and puts each light back as it was
      --lights "LIFX 1787" "LIFX E25D"   only these (default: every colour light in the house)      --foreground   run in this terminal (Ctrl-C ends it)

`show <routine>` runs the show in a process of its own (`python lightshow.py run --plan storm:18000,fortress ...`) that keeps a heartbeat in state/home/show.json;
`stop` asks it to finish by a flag file (state/home/show.stop) so the lights are restored, and only ends it by its PID when it does not answer. A scheduled show
lives in that process: if skippy-pc restarts before its time, start it again.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from house import HomeError

# ── the storm
BLUE = (212.0, 250.0)                 # hue in degrees: azure to indigo (240 is pure blue); each look picks its own
COOL_WHITE_K = 6500
ZONES = ((0.00, 0.15, 28), (0.15, 0.45, 30), (0.45, 0.75, 22), (0.75, 1.00, 20))   # whiteness bands and how often each is picked: deep blue ... near white
APART = 0.22                          # a bulb's next whiteness differs from every other bulb's by at least this, when a pick can
TRIES = 8
SWELL_CHANCE = 0.18                   # now and then a quick surge instead of a slow roll

# ── the fortress
VIOLET = (262.0, 292.0)               # hue in degrees: violet to deep purple (240 is blue, 300 is magenta: neither)
FORTRESS_SATURATION = (88.0, 100.0)
FORTRESS_LEVEL = (30.0, 55.0)         # deep: never bright
FORTRESS_DRIFT = (8.0, 6.0)           # the most one fade moves a bulb: hue degrees, brightness points
FORTRESS_FADE = (240.0, 660.0)        # seconds one fade takes (4 to 11 minutes)
FORTRESS_SETTLE = (240.0, 420.0)      # the first fade into it from another routine (4 to 7 minutes)
FORTRESS_HOLD = (0.0, 60.0)
FORTRESS_APART = 8.0                  # degrees between two bulbs' hues, when a pick can

# ── the engine
COMMAND_SECONDS = 4.0                 # a bulb that does not answer is skipped for a while, never waited on (the others would stall)
BACKOFF_SECONDS = 10.0
POLL_SECONDS = 5.0                    # how often the bulbs' on/off is looked at
SLICE = 0.4                           # the loop sleeps in slices this long, so a stop is prompt
START_PRIME_SECONDS = 1.0
SWITCH_PRIME_SECONDS = 6.0            # a routine that needs a setting first (the storm's white) does it gently when it takes over from another
BEAT_SECONDS = 4.0
BEAT_STALE = 40.0                     # a heartbeat older than this is a show that is not running (a hung controller call can take 15 s)
STOP_WAIT = 20.0
START_WAIT = 25.0
SWITCH_WAIT = 10.0


@dataclass(frozen=True)
class Look:
    key: float                        # where it is heading, for keeping the bulbs apart (the storm: whiteness 0..1; the fortress: hue)
    hue: float                        # degrees
    saturation: float                 # percent
    level: float                      # percent
    seconds: float                    # the fade to it
    hold: float                       # then stay a moment


# ── the storm's looks
def _free(others: list[float]) -> list[tuple[float, float]]:
    """The stretches of 0..1 that are at least APART from every other bulb's heading."""
    spans = [(0.0, 1.0)]
    for o in others:
        kept = []
        for lo, hi in spans:
            if o - APART > lo:
                kept.append((lo, min(hi, o - APART)))
            if o + APART < hi:
                kept.append((max(lo, o + APART), hi))
        spans = [(a, b) for a, b in kept if b > a]
    return spans


def _whiteness(rng: random.Random, others: list[float]) -> float:
    for _ in range(TRIES):                                              # the usual way: a band by its weight (mostly blue, now and then near white), then a place in it
        lo, hi, _weight = rng.choices(ZONES, weights=[z[2] for z in ZONES])[0]
        w = rng.uniform(lo, hi)
        if all(abs(w - o) >= APART for o in others):
            return w
    spans = _free(others)                                               # the others crowd it: straight from the room that is left
    if spans:
        pick = rng.uniform(0.0, sum(b - a for a, b in spans))
        for a, b in spans:
            if pick <= b - a:
                return a + pick
            pick -= b - a
        return spans[-1][1]
    return max((i / 20 for i in range(21)), key=lambda w: min(abs(w - o) for o in others))      # no room at all: as far from them as it can be


def pick_look(rng: random.Random, others: list[float]) -> Look:
    """The storm's next look for one bulb, apart from where the other bulbs are heading."""
    white = _whiteness(rng, others)
    swell = rng.random() < SWELL_CHANCE
    return Look(key=white, hue=rng.uniform(*BLUE), saturation=100.0 * (1.0 - white),
                level=min(100.0, 45.0 + 35.0 * white + rng.uniform(0.0, 20.0)),     # deep blue is dimmer, the white end brighter
                seconds=rng.uniform(0.5, 1.2) if swell else rng.uniform(1.5, 6.5), hold=rng.uniform(0.0, 1.8))


# ── the routines
class Routine:
    name = ""

    def prime(self, house, eid: str, seconds: float, timeout: float) -> None:
        """What a bulb is told before the first look."""

    def look(self, rng: random.Random, eid: str, others: list[float], first: bool) -> Look:
        raise NotImplementedError

    def landed(self, eid: str, look: Look) -> None:
        """A look was sent and the bulb took it."""


class StormRoutine(Routine):
    name = "storm"

    def prime(self, house, eid, seconds, timeout):
        house.set_white(eid, COOL_WHITE_K, seconds, timeout=timeout)    # the bulb keeps it, so the white end of every look is cold

    def look(self, rng, eid, others, first):
        return pick_look(rng, others)


class FortressRoutine(Routine):
    name = "fortress"

    def __init__(self):
        self.last: dict[str, tuple[float, float]] = {}                  # each bulb's hue and level as of its last look

    def look(self, rng, eid, others, first):
        prev = None if first else self.last.get(eid)
        best, far = (VIOLET[0], FORTRESS_LEVEL[0]), -1.0
        for _ in range(12):
            if prev is None:
                hue, level = rng.uniform(*VIOLET), rng.uniform(*FORTRESS_LEVEL)
            else:                                                       # a short step from where it is: nothing a person would catch moving
                hue = min(VIOLET[1], max(VIOLET[0], prev[0] + rng.uniform(-FORTRESS_DRIFT[0], FORTRESS_DRIFT[0])))
                level = min(FORTRESS_LEVEL[1], max(FORTRESS_LEVEL[0], prev[1] + rng.uniform(-FORTRESS_DRIFT[1], FORTRESS_DRIFT[1])))
            gap = min((abs(hue - o) for o in others), default=FORTRESS_APART)
            if gap >= FORTRESS_APART:
                best = (hue, level)
                break
            if gap > far:
                best, far = (hue, level), gap
        hue, level = best
        return Look(key=hue, hue=hue, saturation=rng.uniform(*FORTRESS_SATURATION), level=level,
                    seconds=rng.uniform(*(FORTRESS_SETTLE if prev is None else FORTRESS_FADE)), hold=rng.uniform(*FORTRESS_HOLD))

    def landed(self, eid, look):
        self.last[eid] = (look.hue, look.level)


ROUTINES = {"storm": StormRoutine, "fortress": FortressRoutine}


def _checked(plan) -> list[tuple[str, float | None]]:
    out = []
    for name, seconds in plan:
        if name not in ROUTINES:
            raise HomeError(f"I know these routines: {', '.join(ROUTINES)}. Not {name!r}.")
        out.append((name, float(seconds) if seconds else None))
    if not out:
        raise HomeError("A show needs at least one routine.")
    return out


class Show:
    """The engine. `house` is a matter.Matter (or anything with its colour methods); `eids` are the lights; `plan` is [(routine, seconds or None), ...];
    `rng`, `clock` (for the pacing), `wall` (for when a routine ends: the clock on the wall) and `sleep` are injectable for the tests."""

    def __init__(self, house, eids: list[str], plan, rng: random.Random | None = None, clock=time.monotonic, wall=time.time, sleep=time.sleep,
                 log=lambda message: None):
        self.house, self.eids = house, list(eids)
        self.rng, self.clock, self.wall, self.sleep, self.log = rng or random.Random(), clock, wall, sleep, log
        self.plan = _checked(plan)
        self.index = 0
        self.routine: Routine = ROUTINES[self.plan[0][0]]()
        self.phase_end: float | None = None
        self.heading: dict[str, float] = {}               # where each bulb is heading now
        self.fresh: set[str] = set()                      # bulbs that have not had a look from this routine yet
        self.commands = 0

    def replace_plan(self, plan) -> None:
        """A different plan, not started yet (the show is still waiting for its time)."""
        self.plan = _checked(plan)
        self.routine = ROUTINES[self.plan[0][0]]()

    def phase(self) -> dict:
        name = self.plan[self.index][0]
        return {"routine": name, "ends": self.phase_end, "then": self.plan[self.index + 1][0] if self.index + 1 < len(self.plan) else None}

    def _enter(self, index: int) -> None:
        name, seconds = self.plan[index]
        self.index, self.routine = index, ROUTINES[name]()
        self.phase_end = self.wall() + seconds if seconds else None
        self.heading, self.fresh = {}, set(self.eids)
        self.log(f"the {name} routine begins" + (f" ({seconds / 3600:.2g} h)" if seconds and seconds >= 3600 else f" ({seconds:.0f} s)" if seconds else ""))

    def _try(self, eid: str, what: str, call) -> bool:
        try:
            call()
            return True
        except HomeError as e:
            self.log(f"{eid}: {what} failed: {e}")
            return False

    def _prime(self, seconds: float) -> None:
        for eid in self.eids:
            self._try(eid, "priming", lambda eid=eid: self.routine.prime(self.house, eid, seconds, COMMAND_SECONDS))

    def run(self, stop=lambda: False, tick=lambda: None, poll_plan=lambda: None) -> str:
        """Run until stop() says so, every light is off or the plan has run out; the lights are put back as they were.
        Returns why it ended: "stopped", "off" or "done". `poll_plan()` returns a new plan to take over with, or None."""
        house, rng, clock = self.house, self.rng, self.clock
        was = house.color_snapshot(self.eids)
        reason = "stopped"
        try:
            for eid in self.eids:
                self._try(eid, "switching on", lambda eid=eid: house.switch(eid, True))
            self._enter(0)
            self._prime(START_PRIME_SECONDS)
            self.sleep(1.2)
            start = clock()
            due = {eid: start + rng.uniform(0.0, 3.0) for eid in self.eids}      # staggered, so they never move together
            live = set(self.eids)
            quiet = 0
            last_poll = start
            while not stop():
                now = clock()
                tick()
                new = poll_plan()
                switched = False
                if new:                                                          # a new show takes over in place: the bulbs fade on from where they are
                    self.plan = _checked(new)
                    self._enter(0)
                    switched = True
                elif self.phase_end is not None and self.wall() >= self.phase_end:
                    if self.index + 1 >= len(self.plan):
                        reason = "done"
                        break
                    self._enter(self.index + 1)
                    switched = True
                if switched:
                    self._prime(SWITCH_PRIME_SECONDS)
                    due = {eid: now + rng.uniform(0.0, 3.0) for eid in self.eids}
                if now - last_poll >= POLL_SECONDS:
                    last_poll = now
                    try:
                        on = {s["entity_id"] for s in house.states(fresh=True) if s.get("state") == "on"}
                        live = on & set(self.eids)
                        quiet = 0 if live else quiet + 1
                    except HomeError as e:
                        self.log(f"looking at the lights failed: {e}")
                    if quiet >= 2:                                               # two looks in a row with nothing on: he turned them off
                        reason = "off"
                        break
                eid = min(due, key=due.get)
                if due[eid] > now:
                    self.sleep(min(SLICE, due[eid] - now))
                    continue
                if eid not in live:                                              # off (or out of reach): not driven, looked at again soon
                    due[eid] = now + 3.0
                    continue
                look = self.routine.look(rng, eid, [h for e, h in self.heading.items() if e != eid], eid in self.fresh)
                started = clock()
                ok = (self._try(eid, "colour", lambda: house.set_color(eid, look.hue, look.saturation, look.seconds, timeout=COMMAND_SECONDS))
                      and self._try(eid, "level", lambda: house.set_level(eid, look.level, look.seconds, timeout=COMMAND_SECONDS)))
                if not ok:
                    due[eid] = clock() + BACKOFF_SECONDS
                    continue
                self.commands += 2
                self.fresh.discard(eid)
                self.heading[eid] = look.key
                self.routine.landed(eid, look)
                due[eid] = started + look.seconds + look.hold
        finally:
            power = reason != "off"                                          # stopped or done: back on or off as they were; he switched them off: they stay off
            for eid, before in was.items():
                self._try(eid, "putting it back", lambda eid=eid, before=before: house.color_restore(eid, before, 2.0, power=power, timeout=COMMAND_SECONDS))
        return reason


# ── words and times
def parse_duration(text: str) -> float:
    """Seconds from "5h", "90m", "5h30m", "1.5h", "300s"."""
    s = re.sub(r"\s+", "", str(text).lower())
    s = re.sub(r"hours?|hrs?", "h", s)
    s = re.sub(r"minutes?|mins?", "m", s)
    s = re.sub(r"seconds?|secs?", "s", s)
    if not re.fullmatch(r"(?:\d+(?:\.\d+)?[hms])+", s):
        raise HomeError(f"I cannot read {text!r} as a length of time: say 5h, 90m, 5h30m or 300s.")
    seconds = sum(float(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)([hms])", s))
    if seconds <= 0:
        raise HomeError("A routine has to run for more than no time.")
    return seconds


def parse_at(text: str, now: datetime | None = None) -> float:
    """The epoch time of "18:30", "6:30pm", "tomorrow 18:30" or "2026-11-07 18:30". A bare time is the next one."""
    now = now or datetime.now()
    s = " ".join(str(text).lower().split())
    day = None
    m = re.fullmatch(r"(today|tomorrow|\d{4}-\d{2}-\d{2})\s+(.+)", s)
    if m:
        day, s = m.groups()
    t = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not t:
        raise HomeError(f"I cannot read {text!r} as a time: say 18:30, 6:30pm, tomorrow 18:30 or 2026-11-07 18:30.")
    hour, minute, ampm = int(t.group(1)), int(t.group(2) or 0), t.group(3)
    if ampm:
        if not 1 <= hour <= 12:
            raise HomeError(f"{text!r} is not a time of day.")
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        raise HomeError(f"{text!r} is not a time of day.")
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    try:
        if day is None:
            when = base if base > now else base + timedelta(days=1)
        elif day == "today":
            when = base
        elif day == "tomorrow":
            when = base + timedelta(days=1)
        else:
            y, mo, d = (int(x) for x in day.split("-"))
            when = base.replace(year=y, month=mo, day=d)
    except ValueError:
        raise HomeError(f"{text!r} is not a date I can use.") from None
    if when <= now:
        raise HomeError("That time has already passed.")
    return when.timestamp()


def make_plan(routine: str, duration: str = "", then: str = "") -> list[tuple[str, float | None]]:
    if routine not in ROUTINES:
        raise HomeError(f"I know these routines: {', '.join(ROUTINES)}.")
    if then and not duration:
        raise HomeError("--then needs --for: how long the first routine runs before the next one takes over.")
    plan = [(routine, parse_duration(duration) if duration else None)]
    if then:
        plan.append((then, None))
    return _checked(plan)


def plan_arg(plan) -> str:
    return ",".join(f"{name}:{'' if not seconds else repr(float(seconds))}" for name, seconds in plan)


def parse_plan_arg(text: str) -> list[tuple[str, float | None]]:
    out = []
    for part in text.split(","):
        name, _, seconds = part.partition(":")
        out.append((name, float(seconds) if seconds else None))
    return _checked(out)


def span(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    h, m = divmod(seconds // 60, 60)
    return f"{h} h" + (f" {m} min" if m else "")


def plan_words(plan) -> str:
    return ", then ".join(f"{name}" + (f" for {span(seconds)}" if seconds else "") for name, seconds in plan)


def when_words(epoch: float, now: datetime | None = None) -> str:
    now = now or datetime.now()
    at = datetime.fromtimestamp(epoch)
    day = "today" if at.date() == now.date() else "tomorrow" if at.date() == (now + timedelta(days=1)).date() else at.strftime("%Y-%m-%d")
    return f"{at.strftime('%H:%M')} {day}"


# ── the process around it: start, switch, stop, status
def beat_path(folder: Path) -> Path:
    return Path(folder) / "show.json"


def stop_path(folder: Path) -> Path:
    return Path(folder) / "show.stop"


def next_path(folder: Path) -> Path:
    return Path(folder) / "show.next"


def log_path(folder: Path) -> Path:
    return Path(folder) / "show.log"


def read_beat(folder: Path, now: float | None = None) -> dict | None:
    """The running show's heartbeat, or None when there is none or it has gone stale (a crashed show is not running)."""
    try:
        data = json.loads(beat_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or (now if now is not None else time.time()) - float(data.get("beat", 0)) > BEAT_STALE:
        return None
    return data


def write_beat(folder: Path, data: dict) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = beat_path(folder).with_suffix(".tmp")
    tmp.write_text(json.dumps({**data, "beat": time.time()}), encoding="utf-8")
    tmp.replace(beat_path(folder))


def resolve(matter, names: list[str]) -> list[dict]:
    """The lights to run on: every colour light in the house, or the ones named (each must be a colour light, and name just one)."""
    lights = matter.color_lights()
    if not names:
        if not lights:
            raise HomeError("There is no colour light in the house to run it on.")
        return lights
    chosen = []
    for name in names:
        one, close = matter.find(name, domains=("light",))
        if one is None:
            raise HomeError(matter._ambiguous(name, close))
        found = next((x for x in lights if x["entity_id"] == one["entity_id"]), None)
        if found is None:
            raise HomeError(f"{matter._label(one)} cannot change colour.")
        if found not in chosen:
            chosen.append(found)
    return chosen


def status(folder: Path, now: float | None = None) -> str:
    beat = read_beat(folder, now)
    if beat is None:
        return "No show is on."
    now = now if now is not None else time.time()
    lights = ", ".join(beat.get("lights") or [])
    if beat.get("waiting_until"):
        return f"A show is waiting to begin at {when_words(beat['waiting_until'])}: {plan_words(beat.get('plan') or [])}, on {lights}."
    phase = beat.get("phase") or {}
    left = ""
    if phase.get("ends"):
        left = f", {span(phase['ends'] - now)} left" + (f", then the {phase['then']}" if phase.get("then") else "")
    return f"The {phase.get('routine') or 'show'} routine is running on {lights}{left}."


def start(env: dict, folder: Path, matter, plan, names: list[str], at: float | None = None, out=print, spawn=subprocess.Popen,
          sleep=time.sleep, now=time.time) -> int:
    folder = Path(folder)
    plan = _checked(plan)
    if read_beat(folder) is not None:                                       # one show at a time: this one takes over in place
        if at is not None:
            out("A show is already on: `homectl.py show stop` first, then give it its time.")
            return 1
        next_path(folder).write_text(json.dumps({"plan": [[n, s] for n, s in plan]}), encoding="utf-8")
        deadline = now() + SWITCH_WAIT
        while now() < deadline:
            if not next_path(folder).exists():
                out(f"Switched: {plan_words(plan)}. The lights fade on from where they are.")
                return 0
            sleep(0.4)
        next_path(folder).unlink(missing_ok=True)
        out("The show did not answer. `homectl.py show stop` ends it.")
        return 1
    lights = resolve(matter, names)
    folder.mkdir(parents=True, exist_ok=True)
    stop_path(folder).unlink(missing_ok=True)
    next_path(folder).unlink(missing_ok=True)
    flags = {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt" \
        else {"start_new_session": True}
    argv = [sys.executable, "-u", str(Path(__file__).resolve()), "run", "--plan", plan_arg(plan)]
    if at is not None:
        argv += ["--at", repr(float(at))]
    with open(log_path(folder), "w", encoding="utf-8") as log:
        proc = spawn(argv + [x["entity_id"] for x in lights], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                     cwd=str(Path(__file__).resolve().parent), env=dict(os.environ), **flags)
    deadline = now() + START_WAIT
    while now() < deadline:
        if read_beat(folder) is not None:
            labels = ", ".join(x["label"] for x in lights)
            if at is not None:
                out(f"Scheduled: {plan_words(plan)}, on {labels}, from {when_words(at)}. (It waits in a process on this PC: "
                    f"if skippy-pc restarts before then, give the command again.) `homectl.py show stop` cancels it.")
            else:
                out(f"The {plan[0][0]} routine is on: {labels} ({plan_words(plan)}). `homectl.py show stop` ends it and puts them back.")
            return 0
        if proc.poll() is not None:
            break
        sleep(0.5)
    tail = ""
    try:
        tail = log_path(folder).read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
    except (OSError, IndexError):
        pass
    out("The show did not start." + (f" Its last words: {tail}" if tail else ""))
    return 1


def stop(folder: Path, out=print, sleep=time.sleep, now=time.time, kill=None) -> int:
    folder = Path(folder)
    beat = read_beat(folder)
    if beat is None:
        for p in (beat_path(folder), stop_path(folder), next_path(folder)):
            p.unlink(missing_ok=True)
        out("No show is on.")
        return 0
    stop_path(folder).write_text("stop", encoding="utf-8")
    deadline = now() + STOP_WAIT
    while now() < deadline:
        if not beat_path(folder).exists():
            out("The show is over; the lights are back as they were.")
            return 0
        sleep(0.5)
    pid = int(beat.get("pid") or 0)                                          # it did not answer: end that one process by its PID (never by name)
    if pid:
        (kill or _kill)(pid)
    for p in (beat_path(folder), stop_path(folder), next_path(folder)):
        p.unlink(missing_ok=True)
    out("The show did not answer, so it was ended; the lights stay as it left them (switch them off and on, or set a colour).")
    return 1


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
    else:
        import signal                                                        # noqa: PLC0415
        os.kill(pid, signal.SIGTERM)


def _take_next(folder: Path):
    """A plan another `show` command left for the running show, or None."""
    path = next_path(folder)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path.unlink(missing_ok=True)
    return data.get("plan") if isinstance(data, dict) else None


def worker(matter, folder: Path, eids: list[str], plan, start_at: float | None = None, out=print, wall=time.time, sleep=time.sleep,
           clock=time.monotonic) -> int:
    """The show's own process body: waits for its time, then runs until the stop flag appears, every light is off or the plan runs out; keeps its heartbeat,
    takes over to a new plan when `show` leaves one, and restores the lights."""
    folder = Path(folder)
    plan = _checked(plan)
    labels = {x["entity_id"]: x["label"] for x in matter.color_lights()}
    info = {"pid": os.getpid(), "started": wall(), "lights": [labels.get(e, e) for e in eids], "plan": [[n, s] for n, s in plan],
            "waiting_until": start_at, "phase": None}
    last = {"at": 0.0}

    def beat() -> None:
        if clock() - last["at"] >= BEAT_SECONDS:
            last["at"] = clock()
            write_beat(folder, info)

    def said(message: str) -> None:
        out(time.strftime("%H:%M:%S ") + message)
    show = Show(matter, eids, plan, clock=clock, wall=wall, sleep=sleep, log=said)
    write_beat(folder, info)
    try:
        while start_at is not None and wall() < start_at:                    # waiting for its time: the lights are not touched
            if stop_path(folder).exists():
                said("cancelled before it began")
                return 0
            new = _take_next(folder)
            if new:
                show.replace_plan(new)
                info["plan"] = [[n, s] for n, s in show.plan]
            beat()
            sleep(1.0)
        info["waiting_until"] = None

        def tick() -> None:
            info["phase"] = show.phase()
            beat()

        def poll_plan():
            new = _take_next(folder)
            if not new:
                return None
            try:
                info["plan"] = [[n, s] for n, s in _checked(new)]
            except HomeError as e:                                           # a plan it cannot read never stops a show that is running
                said(f"ignored a plan it could not read: {e}")
                return None
            return new
        reason = show.run(stop=lambda: stop_path(folder).exists(), tick=tick, poll_plan=poll_plan)
        said(f"show ended ({reason}) after {show.commands} commands")
    finally:
        for p in (beat_path(folder), stop_path(folder), next_path(folder)):
            p.unlink(missing_ok=True)
    return 0


def command(what: str, env: dict, folder: Path, get_matter, out=print, duration: str = "", then: str = "", at: str = "",
            names=(), foreground: bool = False) -> int:
    """homectl.py's `show storm|fortress|stop|status`. `get_matter` builds the controller link only when it is needed."""
    try:
        if what == "status":
            out(status(folder))
            return 0
        if what == "stop":
            return stop(folder, out)
        plan = make_plan(what, duration, then)
        at_epoch = parse_at(at) if at else None
        matter = get_matter()
        if foreground:
            lights = resolve(matter, list(names))
            out(f"The {what} routine: {', '.join(x['label'] for x in lights)} ({plan_words(plan)}). Ctrl-C ends it and puts them back.")
            try:
                worker(matter, folder, [x["entity_id"] for x in lights], plan, at_epoch, out)
            except KeyboardInterrupt:
                pass
            return 0
        return start(env, folder, matter, plan, list(names), at_epoch, out)
    except HomeError as e:
        out(str(e))
        return 1


def main(argv=None) -> int:
    """`python lightshow.py run --plan storm:18000,fortress [--at EPOCH] <entity_id> ...`: what `homectl.py show` launches in a process of its own."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "run":
        print(__doc__)
        return 2
    ap = argparse.ArgumentParser(prog="lightshow.py run")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--at", type=float, default=None)
    ap.add_argument("eids", nargs="+")
    args = ap.parse_args(argv[1:])
    from homectl import load_env, matter_path                               # noqa: PLC0415
    from matter import Matter, Names                                        # noqa: PLC0415
    env = load_env()
    if not env.get("MATTER_URL"):
        print("MATTER_URL isn't set.")
        return 1
    return worker(Matter(env["MATTER_URL"], Names(matter_path(env))), matter_path(env).parent, args.eids, parse_plan_arg(args.plan), args.at,
                  out=lambda m: print(m, flush=True))


if __name__ == "__main__":
    sys.exit(main())
