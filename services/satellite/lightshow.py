"""Light shows: loops that drive the colour lights by themselves (the house layer itself only switches and dims).

The storm (2026-11-06): every colour bulb drifts between deep blue and white on its OWN random course, so the room looks like a roiling stormy sky.
Each bulb picks its next look at random (how white, which blue, how bright, how long to take, how long to hold), never close to where another bulb is
heading, so no two ever match; the bulb fades there itself (Matter's transition time), a command every few seconds, not a stream. A look is "whiteness"
0 (deep storm blue) to 1 (cool white, 6500 K): the saturation falls as it rises, and the bulb is first set to a cool white so the white end is cold,
never warm. A bulb that is off is left alone, and the show ends by itself when every bulb is off ("turn off the lights" ends the storm); stopping it
puts each bulb back as it was.

    ./.venv/Scripts/python homectl.py storm start [light ...]    every colour light in the house, or the ones named; runs until stopped
    ./.venv/Scripts/python homectl.py storm stop                 ends it and puts each light back as it was
    ./.venv/Scripts/python homectl.py storm status
    ./.venv/Scripts/python homectl.py storm run [light ...]      the same in this terminal (Ctrl-C ends it)

`start` runs the show in a process of its own (`python lightshow.py run <entity_id> ...`) that keeps a heartbeat in state/home/storm.json; `stop` asks it
to finish by a flag file (state/home/storm.stop) so the lights are restored, and only ends it by its PID when it does not answer.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from house import HomeError

BLUE = (212.0, 250.0)                 # hue in degrees: azure to indigo (240 is pure blue); each look picks its own
COOL_WHITE_K = 6500
ZONES = ((0.00, 0.15, 28), (0.15, 0.45, 30), (0.45, 0.75, 22), (0.75, 1.00, 20))   # whiteness bands and how often each is picked: deep blue ... near white
APART = 0.22                          # a bulb's next whiteness differs from every other bulb's by at least this, when a pick can
TRIES = 8
SWELL_CHANCE = 0.18                   # now and then a quick surge instead of a slow roll
COMMAND_SECONDS = 4.0                 # a bulb that does not answer is skipped for a while, never waited on (the others would stall)
BACKOFF_SECONDS = 10.0
POLL_SECONDS = 5.0                    # how often the bulbs' on/off is looked at
SLICE = 0.4                           # the loop sleeps in slices this long, so a stop is prompt
BEAT_SECONDS = 4.0
BEAT_STALE = 40.0                     # a heartbeat older than this is a show that is not running (a hung controller call can take 15 s)
STOP_WAIT = 20.0
START_WAIT = 25.0


@dataclass(frozen=True)
class Look:
    white: float                      # 0 deep blue .. 1 white
    hue: float                        # degrees
    saturation: float                 # percent
    level: float                      # percent
    seconds: float                    # the fade to it
    hold: float                       # then stay a moment


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
    """The next look for one bulb, apart from where the other bulbs are heading."""
    best = _whiteness(rng, others)
    swell = rng.random() < SWELL_CHANCE
    return Look(white=best, hue=rng.uniform(*BLUE), saturation=100.0 * (1.0 - best),
                level=min(100.0, 45.0 + 35.0 * best + rng.uniform(0.0, 20.0)),     # deep blue is dimmer, the white end brighter
                seconds=rng.uniform(0.5, 1.2) if swell else rng.uniform(1.5, 6.5), hold=rng.uniform(0.0, 1.8))


class Storm:
    """`house` is a matter.Matter (or anything with its colour methods); `eids` are the lights, `rng`, `clock` and `sleep` are injectable for the tests."""

    def __init__(self, house, eids: list[str], rng: random.Random | None = None, clock=time.monotonic, sleep=time.sleep, log=lambda message: None):
        self.house, self.eids = house, list(eids)
        self.rng, self.clock, self.sleep, self.log = rng or random.Random(), clock, sleep, log
        self.white: dict[str, float] = {}                 # where each bulb is heading now
        self.commands = 0

    def _try(self, eid: str, what: str, call) -> bool:
        try:
            call()
            return True
        except HomeError as e:
            self.log(f"{eid}: {what} failed: {e}")
            return False

    def run(self, stop=lambda: False, tick=lambda: None) -> str:
        """Run until stop() says so or every light is off; the lights are put back as they were. Returns why it ended: "stopped" or "off"."""
        house, rng, clock = self.house, self.rng, self.clock
        was = house.color_snapshot(self.eids)
        reason = "stopped"
        try:
            for eid in self.eids:                                           # on, and a cool white first: the bulb keeps it, so the white end of every look is cold
                self._try(eid, "switching on", lambda eid=eid: house.switch(eid, True))
                self._try(eid, "setting white", lambda eid=eid: house.set_white(eid, COOL_WHITE_K, 1.0, timeout=COMMAND_SECONDS))
            self.sleep(1.2)
            start = clock()
            due = {eid: start + rng.uniform(0.0, 3.0) for eid in self.eids}      # staggered, so they never move together
            live = set(self.eids)
            quiet = 0
            last_poll = start
            while not stop():
                now = clock()
                tick()
                if now - last_poll >= POLL_SECONDS:
                    last_poll = now
                    try:
                        on = {s["entity_id"] for s in house.states(fresh=True) if s.get("state") == "on"}
                        live = on & set(self.eids)
                        quiet = 0 if live else quiet + 1
                    except HomeError as e:
                        self.log(f"looking at the lights failed: {e}")
                    if quiet >= 2:                                           # two looks in a row with nothing on: he turned them off
                        reason = "off"
                        break
                eid = min(due, key=due.get)
                if due[eid] > now:
                    self.sleep(min(SLICE, due[eid] - now))
                    continue
                if eid not in live:                                          # off (or out of reach): not driven, looked at again soon
                    due[eid] = now + 3.0
                    continue
                look = pick_look(rng, [w for e, w in self.white.items() if e != eid])
                started = clock()
                ok = (self._try(eid, "colour", lambda: house.set_color(eid, look.hue, look.saturation, look.seconds, timeout=COMMAND_SECONDS))
                      and self._try(eid, "level", lambda: house.set_level(eid, look.level, look.seconds, timeout=COMMAND_SECONDS)))
                if not ok:
                    due[eid] = clock() + BACKOFF_SECONDS
                    continue
                self.commands += 2
                self.white[eid] = look.white
                due[eid] = started + look.seconds + look.hold
        finally:
            power = reason != "off"                                          # stopped: back on or off as they were; he switched them off: they stay off
            for eid, before in was.items():
                self._try(eid, "putting it back", lambda eid=eid, before=before: house.color_restore(eid, before, 2.0, power=power, timeout=COMMAND_SECONDS))
        return reason


# ── the process around it: start, stop, status ──────────────────────────────────
def beat_path(folder: Path) -> Path:
    return Path(folder) / "storm.json"


def stop_path(folder: Path) -> Path:
    return Path(folder) / "storm.stop"


def log_path(folder: Path) -> Path:
    return Path(folder) / "storm.log"


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
        return "The storm is not running."
    ago = (now if now is not None else time.time()) - float(beat.get("started", 0))
    return f"The storm is running on {', '.join(beat.get('lights') or [])} ({int(ago // 60)} min so far)."


def start(env: dict, folder: Path, matter, names: list[str], out=print, spawn=subprocess.Popen, sleep=time.sleep, now=time.time) -> int:
    folder = Path(folder)
    if read_beat(folder) is not None:
        out(status(folder))
        return 0
    lights = resolve(matter, names)
    folder.mkdir(parents=True, exist_ok=True)
    stop_path(folder).unlink(missing_ok=True)
    flags = {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt" \
        else {"start_new_session": True}
    with open(log_path(folder), "w", encoding="utf-8") as log:
        proc = spawn([sys.executable, "-u", str(Path(__file__).resolve()), "run", *[x["entity_id"] for x in lights]],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(Path(__file__).resolve().parent), env=dict(os.environ), **flags)
    deadline = now() + START_WAIT
    while now() < deadline:
        if read_beat(folder) is not None:
            out(f"The storm is on: {', '.join(x['label'] for x in lights)}. `homectl.py storm stop` ends it and puts them back.")
            return 0
        if proc.poll() is not None:
            break
        sleep(0.5)
    tail = ""
    try:
        tail = log_path(folder).read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
    except (OSError, IndexError):
        pass
    out("The storm did not start." + (f" Its last words: {tail}" if tail else ""))
    return 1


def stop(folder: Path, out=print, sleep=time.sleep, now=time.time, kill=None) -> int:
    folder = Path(folder)
    beat = read_beat(folder)
    if beat is None:
        beat_path(folder).unlink(missing_ok=True)
        stop_path(folder).unlink(missing_ok=True)
        out("The storm is not running.")
        return 0
    stop_path(folder).write_text("stop", encoding="utf-8")
    deadline = now() + STOP_WAIT
    while now() < deadline:
        if not beat_path(folder).exists():
            out("The storm is over; the lights are back as they were.")
            return 0
        sleep(0.5)
    pid = int(beat.get("pid") or 0)                                          # it did not answer: end that one process by its PID (never by name)
    if pid:
        (kill or _kill)(pid)
    beat_path(folder).unlink(missing_ok=True)
    stop_path(folder).unlink(missing_ok=True)
    out("The storm did not answer, so it was ended; the lights stay as the storm left them (switch them off and on, or set a colour).")
    return 1


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
    else:
        import signal                                                        # noqa: PLC0415
        os.kill(pid, signal.SIGTERM)


def worker(matter, folder: Path, eids: list[str], out=print) -> int:
    """The show's own process body: runs until the stop flag appears or every light is off, keeps its heartbeat, and restores the lights."""
    folder = Path(folder)
    labels = {x["entity_id"]: x["label"] for x in matter.color_lights()}
    info = {"pid": os.getpid(), "started": time.time(), "lights": [labels.get(e, e) for e in eids]}
    last = {"at": 0.0}

    def tick() -> None:
        if time.monotonic() - last["at"] >= BEAT_SECONDS:
            last["at"] = time.monotonic()
            write_beat(folder, info)
    write_beat(folder, info)
    storm = Storm(matter, eids, log=lambda m: out(time.strftime("%H:%M:%S ") + m))
    try:
        reason = storm.run(stop=lambda: stop_path(folder).exists(), tick=tick)
        out(f"{time.strftime('%H:%M:%S')} storm ended ({reason}) after {storm.commands} commands")
    finally:
        beat_path(folder).unlink(missing_ok=True)
        stop_path(folder).unlink(missing_ok=True)
    return 0


def command(action: str, names: list[str], env: dict, folder: Path, get_matter, out=print) -> int:
    """homectl.py's `storm start|stop|status|run`. `get_matter` builds the controller link only when it is needed."""
    try:
        if action == "status":
            out(status(folder))
            return 0
        if action == "stop":
            return stop(folder, out)
        if action == "start":
            return start(env, folder, get_matter(), names, out)
        if action == "run":
            matter = get_matter()
            lights = resolve(matter, names)
            out(f"The storm is on: {', '.join(x['label'] for x in lights)}. Ctrl-C ends it and puts them back.")
            try:
                Storm(matter, [x["entity_id"] for x in lights], log=out).run()
            except KeyboardInterrupt:
                pass
            return 0
    except HomeError as e:
        out(str(e))
        return 1
    out(f"I do not know how to {action} the storm.")
    return 1


def main(argv=None) -> int:
    """`python lightshow.py run <entity_id> ...`: what `homectl.py storm start` launches in a process of its own."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "run":
        print(__doc__)
        return 2
    from homectl import load_env, matter_path                               # noqa: PLC0415
    from matter import Matter, Names                                        # noqa: PLC0415
    env = load_env()
    if not env.get("MATTER_URL"):
        print("MATTER_URL isn't set.")
        return 1
    return worker(Matter(env["MATTER_URL"], Names(matter_path(env))), matter_path(env).parent, argv[1:], out=lambda m: print(m, flush=True))


if __name__ == "__main__":
    sys.exit(main())
