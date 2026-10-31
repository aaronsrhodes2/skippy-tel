"""Skippy's daily routines at home (the Captain, 2026-10-31): the MORNING briefing, COMING HOME from work, and BEDTIME.

    morning     at SKIPPY_MORNING_AT (07:10) every day, only if he is home (the car is parked at home, or the house heard him last night):
                lights up gently, then weather and what to wear, the news (world, new science, software / AR / AI: sourced only), today's
                calendar, the mail that matters, the car, a joke, a word of the day, a science fact, the overnight report, then Bilby's
                library shuffled on the stereo. "Skippy, good morning" runs it by hand.
    coming home the car arrives home after SKIPPY_HOMECOMING_AWAY_H (3) hours away: the kitchen lights (and the outdoor ones after dark) come on at
                once; when he is heard in the room (or says "Skippy, I'm home"): the latest news, Bilby's shuffle, and Skippy asks about his day.
    bedtime     "Skippy, goodnight": every light off, the car charging (if plugged in) and Sentry armed, his day and tomorrow, then an offer to talk.
                "Skippy, goodnight at 11" asks at 11 first instead.

While one runs, each part ends with what comes next ("Next: the news."); in the pause after it, without the name: "skip" drops that next part,
"later" / "snooze" puts the rest off ten minutes, "stop" ends it. Anything else he says is answered as usual, and the routine waits until he is done.

Everything spoken is either read from a source (the news search, Open-Meteo's forecast, TeslaMate, the house log) or Skippy's own joke; Core (the
local model) only words it. Figures are the sources' own (PLATINUM 21: Skippy computes none; the one unit conversion is Mentat's, below).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

KM_TO_MI = 0.621371192237334            # Mentat convert_units 1 km -> mile (pint UnitRegistry), 2026-10-31
CAR_URL = os.environ.get("SKIPPY_CAR_URL", "http://skippy-core:7351").rstrip("/")            # the car reader (core/car): TeslaMate's latest values
CAR_BLE_URL = os.environ.get("SKIPPY_CAR_BLE_URL", "http://skippy-core:7352").rstrip("/")    # charging over Bluetooth (core/car-ble)
CAR_FLEET_URL = os.environ.get("SKIPPY_CAR_FLEET_URL", "http://skippy-core:7353").rstrip("/")  # Sentry through the Fleet API (core/car-fleet)
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GAP_S = 3.0                              # the pause after each part: room for "skip", "later", "stop"
SNOOZE_S = 600
CORE_LIMIT_S = 75                        # Core gets this long to word one part
SAY_LIMIT_S = 240                        # one part said aloud never holds the routine longer than this
TALK_WAIT_S = 90                         # room talk (not to Skippy) holds the next part back at most this long
HOME_NEAR_KM = 0.3                       # the car this close to home is home
AWAY_KM = 1.0                            # ... and this far is away
PRESENCE_WAIT_S = 45 * 60                # after the car gets home, the spoken part waits this long for him to be heard in the room

SKIP_RE = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:skip(?: (?:it|that|this|ahead|that one))?|next|move on|pass)\W*$", re.I)
LATER_RE = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:later|snooze|not now|in a (?:bit|minute|few minutes)|give me (?:ten|10|a few) minutes)\W*$", re.I)
END_RE = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:stop|cancel|enough|that'?s (?:all|enough)|skip (?:all|everything|the rest)|we'?re done|i'?m done)\W*$", re.I)
GOODNIGHT_RE = re.compile(r"^\W*(?:good ?night|night night|nighty night|bed ?time|i'?m (?:going|off) to bed|going to bed|time for bed|"
                          r"(?:run|start) (?:the |my )?bed ?time(?: routine)?)(?:,? (?:skippy|monkey|buddy))?\W*$", re.I)
TIMED_RE = re.compile(r"^\W*(?:good ?night|bed ?time(?: routine)?)\s+(?:at|for)\s+(\d{1,2})(?:[: ](\d\d))?\s*(a\.?m\.?|p\.?m\.?)?\W*$", re.I)
MORNING_RE = re.compile(r"^\W*(?:good morning|(?:run|start|do) (?:the |my )?morning(?: routine| briefing)?|morning briefing)(?:,? (?:skippy|monkey))?\W*$", re.I)
HOME_RE = re.compile(r"^\W*(?:i'?m home|i am home|honey,? i'?m home|i'?m back(?: home)?|(?:run|start) (?:the )?(?:coming|getting) home(?: routine)?)\W*$", re.I)

# Open-Meteo's weather codes (WMO), in words
MORNING_NEWS = ("world:major world news|science:space discovery astronomers|science:physics breakthrough discovery|AI:AI breakthrough research|"
                "AR:AR smart glasses news|software:software release open source")    # the subjects tools/starter_refresh.py found worth searching
JUNK_RE = re.compile(r"insidermonkey|thestreet|fool\.com|zacks|seekingalpha|benzinga|marketbeat|investorplace|stocktitan", re.I)

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers", 82: "violent rain showers",
       85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}

WORDING = (" You are speaking aloud in his house, so: plain spoken sentences, no lists, no markdown, no emoji, no URLs. Say only what the material "
           "below says; never add a fact, a number or a detail of your own. Keep it short. Stay in character.")


def http_get_json(url: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:                 # noqa: S310 — our own services on the tailnet, and Open-Meteo
        return json.loads(r.read())


def http_post_json(url: str, body: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:                 # noqa: S310
        return json.loads(r.read())


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km, for "is the car at home" only (a threshold, never said aloud)."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def hm_of(text: str) -> tuple[int, int]:
    h, m = (text or "07:10").split(":")
    return int(h), int(m)


def clock_words(ts: float) -> str:
    t = time.localtime(ts)
    return time.strftime("%I:%M %p", t).lstrip("0").replace(":00 ", " ")


def plain_headlines(found: list, n: int = 5) -> str:
    """The headlines as they are, each with its source: what is said when Core can't word them."""
    picked = [it for _, it in found][:n]
    return "The headlines. " + " ".join(f"{it.get('source')}: {str(it.get('title')).rstrip('.')}." for it in picked)


class Step:
    """One part of a routine: a `title` (said as "Next: <title>"), and `make` (async, -> words to say, or None to drop the part) and/or `act`
    (async, does something in the house; -> words to say after, or None). `clip` is a pre-recorded (text, mp3) said instead of the words."""

    def __init__(self, title: str, make=None, act=None, last: bool = False):
        self.title, self.make, self.act, self.last = title, make, act, last
        self.text: str | None = None
        self.clip: tuple[str, bytes] | None = None
        self.ready: asyncio.Future | None = None


class Run:
    def __init__(self, name: str, steps: list[Step], room):
        self.name, self.steps, self.room = name, steps, room
        self.skip_next = self.cancelled = False
        self.snooze_until = 0.0
        self.task: asyncio.Task | None = None
        self.step_end = 0.0


class Daily:
    """The house's routines, shared by every room; they speak in SKIPPY_ROUTINE_ROOM (the first room by default). The hub calls claim() with
    every command (before the brain) and tick() from each room's ticker."""

    def __init__(self, env: dict, house=None, client=None, model: str = "", news=None, filler=None, stereo=None, hlog=None,
                 state_dir: Path | str | None = None, get=http_get_json, post=http_post_json, clock=time.time, mono=time.monotonic,
                 sleep=asyncio.sleep, log=print, persona: str = "", extras=None):
        self.env, self.house, self.client, self.model, self.news, self.filler, self.hlog = env, house, client, model, news, filler, hlog
        self.stereo = stereo                                      # () -> StereoMusic or None: Bilby's library on the house stereo (router.py)
        self.get, self.post, self.clock, self.mono, self.sleep, self.log, self.persona = get, post, clock, mono, sleep, log, persona
        self.extras = extras or {}                                # more morning / bedtime parts from elsewhere: {"calendar": async () -> str|None, ...}
        self.dir = Path(state_dir) if state_dir else Path(__file__).resolve().parents[2] / "state" / "daily"
        self.room_name = (env.get("SKIPPY_ROUTINE_ROOM") or "").strip()
        self.morning_at = env.get("SKIPPY_MORNING_AT", "07:10")
        self.away_h = float(env.get("SKIPPY_HOMECOMING_AWAY_H", "3"))
        self.gap_s = GAP_S
        self.run: Run | None = None
        self.asking: dict | None = None                           # a timed bedtime, asked and waiting for his yes / no: {"room", "at"}
        self.arrived_at: float | None = None                      # the car got home: the spoken part waits for him to be heard
        self._car_check = 0.0
        self.state = self._load()

    # ── what is kept between days (state/daily/state.json) ──
    def _load(self) -> dict:
        try:
            return json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "state.json.tmp"
            tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
            tmp.replace(self.dir / "state.json")
        except OSError as e:
            self.log(f"(daily: could not save its state: {e})")

    def _record(self, kind: str, **fields) -> None:
        if self.hlog is not None:
            self.hlog.event(kind, room=self.room_name or None, **fields)

    @property
    def busy(self) -> bool:
        return self.run is not None and self.run.task is not None and not self.run.task.done()

    def home(self) -> tuple[float, float] | None:
        """Home's position: SKIPPY_HOME_LATLON, else where the car spends the night (learned in state.json)."""
        spec = (self.env.get("SKIPPY_HOME_LATLON") or "").strip()
        if spec:
            try:
                lat, lon = (float(x) for x in spec.split(","))
                return lat, lon
            except ValueError:
                pass
        h = self.state.get("home")
        return (h["lat"], h["lon"]) if isinstance(h, dict) and "lat" in h else None

    # ── what he says ──
    async def claim(self, room, command: str) -> bool:
        """True when the words were for a routine (and were dealt with); False: the brain answers as usual."""
        c = (command or "").strip()
        if self.asking is not None and self.asking["room"] is room:
            from proposals import parse_answer                     # noqa: PLC0415
            answer, self.asking = parse_answer(c), None
            if answer == "yes":
                self.start("bedtime", room)
                return True
            if answer in ("no", "never"):
                await self._reply(room, "Fine. Stay up. I will be here, judging quietly.")
                return True
        if self.busy and self.run.room is room:
            if END_RE.match(c):
                self.run.cancelled = True
                self.run.task.cancel()
                self._record("routine", routine=self.run.name, outcome="stopped")
                await self._reply(room, "Fine. Routine over.", release=True)
                return True
            if LATER_RE.match(c):
                self.run.snooze_until = self.mono() + SNOOZE_S
                self._record("routine", routine=self.run.name, outcome="snoozed")
                await self._reply(room, "Ten minutes. Then I continue, whether you like it or not.", release=True)
                return True
            if SKIP_RE.match(c):
                self.run.skip_next = True
                await self._reply(room, "Skipping it.")
                return True
        m = TIMED_RE.match(c)
        if m:
            at = self._when(int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower())
            self.state["bedtime_ask"] = at
            self._save()
            await self._reply(room, f"Right. At {clock_words(at)} I will ask if you want the bedtime routine.")
            return True
        if GOODNIGHT_RE.match(c):
            self.start("bedtime", room)
            return True
        if MORNING_RE.match(c):
            self.start("morning", room)
            return True
        if HOME_RE.match(c):
            self.arrived_at = None
            self.start("homecoming", room, lights=True)
            return True
        return False

    def _when(self, h: int, m: int, ampm: str) -> float:
        """The next time it is h:m (an hour with no am/pm under 12 is taken as the evening: it's a bedtime)."""
        if ampm.startswith("p") and h < 12:
            h += 12
        elif ampm.startswith("a") and h == 12:
            h = 0
        elif not ampm and h < 12:
            h += 12
        now = self.clock()
        t = time.localtime(now)
        at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h % 24, m, 0, 0, 0, -1))
        return at + 86400 if at <= now else at

    async def _reply(self, room, text: str, release: bool = False) -> None:
        await room.say(text)
        from hub import FOLLOWUP, IDLE                              # noqa: PLC0415
        if release:
            room._resume(IDLE)
            await room.link.released()
        else:
            room._resume(FOLLOWUP if room.remote_wake and room.link.hub_follows_up else IDLE, room.followup_ms)

    # ── the clock ──
    async def tick(self, room) -> None:
        """Called every half second from each room; acts only for the routine room."""
        if self.room_name and room.name != self.room_name:
            return
        self.room_name = self.room_name or room.name
        if self.run is not None and self.run.room is not room and self.run.room.name == room.name:
            self.run.room = room                                      # the box reconnected: the routine carries on in its new Room
        if self.asking is not None and self.asking["room"] is not room and self.asking["room"].name == room.name:
            self.asking["room"] = room
        req = self.dir / "request.json"
        if req.exists() and not self.busy and room.mode != "busy":   # `python daily.py run morning` (from this PC, or a session testing it)
            try:
                name = json.loads(req.read_text(encoding="utf-8")).get("routine")
            except (OSError, ValueError, AttributeError):
                name = None
            req.unlink(missing_ok=True)
            if name in ("morning", "bedtime", "homecoming"):
                self.start(name, room)
                return
        now = self.clock()
        lt = time.localtime(now)
        today = time.strftime("%Y-%m-%d", lt)
        at = self.state.get("bedtime_ask")
        if at and now >= at and not self.busy and room.mode != "busy":
            self.state.pop("bedtime_ask", None)
            self._save()
            if now - at < 1800:
                self.asking = {"room": room, "at": now}
                await self._reply(room, f"It's {clock_words(now)}. Bedtime routine?")
            return
        if self.mono() - self._car_check >= 60:
            self._car_check = self.mono()
            await self._watch_car(room, lt)
        if self.arrived_at is not None and not self.busy:
            heard = getattr(room, "_voice_at", None)
            if heard is not None and heard > self.arrived_at:
                self.arrived_at = None
                self.start("homecoming", room, lights=False)
            elif self.mono() - self.arrived_at > PRESENCE_WAIT_S:
                self.arrived_at = None
                self.log(f"[{room.name}] (coming home: nobody heard in the room; the talking part is skipped)")
        when = self.morning_time(today)
        if when and (lt.tm_hour, lt.tm_min) >= hm_of(when) and lt.tm_hour < 11 and self.state.get("last_morning") != today and not self.busy:
            self.state["last_morning"] = today
            self._save()
            if await asyncio.to_thread(self.is_home):
                self.start("morning", room)
            else:
                self.log(f"[{room.name}] (morning routine: he isn't home; skipped)")
                self._record("routine", routine="morning", outcome="away")

    def morning_time(self, date: str) -> str:
        over = self.state.get("morning_override") or {}
        return over.get(date) or self.morning_at

    def car(self) -> dict | None:
        try:
            cars = self.get(f"{CAR_URL}/car", 10).get("cars") or []
        except Exception:                                         # noqa: BLE001 — the reader is down: no car today
            return None
        return cars[0] if cars and isinstance(cars[0], dict) else None

    def car_home(self, car: dict | None) -> bool | None:
        home = self.home()
        if not car or home is None or car.get("latitude") is None:
            return None
        return km_between(home, (car["latitude"], car["longitude"])) <= HOME_NEAR_KM

    def is_home(self) -> bool:
        """At home for the morning: the car is parked at home, or the house heard him since yesterday evening."""
        if self.car_home(self.car()):
            return True
        if self.hlog is None:
            return self.car() is None                             # nothing to go on: assume home rather than miss the morning
        since = self.clock() - 14 * 3600
        for path in sorted((Path(self.hlog.root) / "log").glob("*.jsonl"))[-2:]:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    e = json.loads(line)
                    ts = time.mktime(time.strptime(e["ts"], "%Y-%m-%dT%H:%M:%S"))
                except (ValueError, KeyError, TypeError):
                    continue
                if ts >= since and e.get("kind") in ("wake", "command"):
                    return True
        return False

    async def _watch_car(self, room, lt) -> None:
        """Once a minute: learn where home is (where the car sleeps), and notice it getting home after a long day out."""
        car = await asyncio.to_thread(self.car)
        if not car or car.get("latitude") is None:
            return
        pos = (car["latitude"], car["longitude"])
        if 2 <= lt.tm_hour < 5 and (car.get("shift_state") in (None, "P")):
            self._learn_home(pos)
        home = self.home()
        if home is None:
            return
        d = km_between(home, pos)
        now = self.clock()
        if d >= AWAY_KM:
            self.state.setdefault("away_since", now)
            self.state.pop("home_since", None)
            self._save()
        elif d <= HOME_NEAR_KM and "away_since" in self.state:
            away = now - self.state.pop("away_since")
            self.state["home_since"] = now
            self._save()
            if away >= self.away_h * 3600 and 11 <= lt.tm_hour < 23 and not self.busy:
                self.log(f"[{room.name}] the car is home after {away / 3600:.1f} h away: coming-home routine")
                self._record("routine", routine="homecoming", outcome="arrived")
                await self.homecoming_lights()
                self.arrived_at = self.mono()

    def _learn_home(self, pos: tuple[float, float]) -> None:
        nights = self.state.setdefault("nights", {})
        key = f"{pos[0]:.3f},{pos[1]:.3f}"
        today = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        if today in (nights.get(key) or []):
            return
        nights.setdefault(key, []).append(today)
        nights[key] = nights[key][-30:]
        best = max(nights, key=lambda k: len(nights[k]))
        lat, lon = (float(x) for x in best.split(","))
        if len(nights[best]) >= 2 or "home" not in self.state:
            self.state["home"] = {"lat": lat, "lon": lon, "how": f"the car parked there overnight on {len(nights[best])} night(s)"}
        self._save()

    # ── running a routine ──
    def start(self, name: str, room, **opts) -> None:
        if self.busy:
            self.run.cancelled = True
            self.run.task.cancel()
        self.news = getattr(room.brain, "news", None) or self.news
        steps = {"morning": self.morning_steps, "bedtime": self.bedtime_steps, "homecoming": self.homecoming_steps}[name](room, **opts)
        self.run = Run(name, steps, room)
        self.run.task = asyncio.ensure_future(self._go(self.run))
        self._record("routine", routine=name, outcome="started")
        self.log(f"[{room.name}] routine: {name}")

    async def _go(self, run: Run) -> None:
        from hub import BUSY, FOLLOWUP, IDLE                        # noqa: PLC0415
        try:
            for s in run.steps:                                       # the words are gathered in parallel while the first parts are said
                if s.make is not None:
                    s.ready = asyncio.ensure_future(self._make(s))
            spoken = 0
            for i, s in enumerate(run.steps):
                await self._wait_turn(run, first=spoken == 0)
                room = run.room                                       # (the box may have reconnected: tick() hands the run the new Room)
                if run.skip_next and spoken:
                    run.skip_next = False
                    self._record("routine", routine=run.name, outcome="skipped", step=s.title)
                    continue
                words = None
                if s.act is not None:
                    try:
                        words = await s.act()
                    except Exception as e:                            # noqa: BLE001 — one part failing never ends the routine
                        self.log(f"[{room.name}] (routine {run.name}: {s.title} failed: {type(e).__name__}: {e})")
                if s.ready is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(s.ready), 120)
                    except (asyncio.TimeoutError, Exception):         # noqa: BLE001
                        pass
                    words = " ".join(x for x in (words, s.text) if x) or None
                if not words and s.clip is None:
                    continue
                nxt = next((n for n in run.steps[i + 1:] if (n.ready is None or not n.ready.done() or n.text or n.clip) and n.title), None)
                tail = f" Next: {nxt.title}." if nxt is not None and not s.last else ""
                room.mode = BUSY
                self.log(f"[{room.name}] routine {run.name}: {s.title or 'part'}: {((s.clip[0] + ' ') if s.clip else '') + (words or '')}"[:300])
                try:
                    if s.clip is not None:
                        await asyncio.wait_for(self._clip(room, s.clip, (words or "") + tail), SAY_LIMIT_S)
                    else:
                        await asyncio.wait_for(room.say(words + tail, announce=True), SAY_LIMIT_S)
                except asyncio.TimeoutError:                          # a box that went away mid-sentence: on with the routine
                    self.log(f"[{room.name}] (routine {run.name}: {s.title}: the box never finished saying it)")
                spoken += 1
                room._resume(FOLLOWUP, room.followup_ms if s.last else int(self.gap_s * 1000) + 6000)
                run.step_end = self.mono()
            self._record("routine", routine=run.name, outcome="done")
        except asyncio.CancelledError:
            pass
        finally:
            for s in run.steps:
                if s.ready is not None and not s.ready.done():
                    s.ready.cancel()
            if run.room.mode == BUSY:
                run.room._resume(IDLE)

    async def _make(self, s: Step) -> None:
        try:
            got = await s.make()
        except Exception as e:                                        # noqa: BLE001
            self.log(f"(routine: {s.title}: {type(e).__name__}: {e})")
            return
        if isinstance(got, tuple):
            s.clip = got
        else:
            s.text = got

    async def _wait_turn(self, run: Run, first: bool) -> None:
        """Before the next part: the pause after the last one, nobody talking (to Skippy or in the room), and no snooze."""
        from hub import BUSY                                        # noqa: PLC0415
        started = self.mono()
        resumed = False
        while True:
            room = run.room
            if run.snooze_until > self.mono():
                resumed = True
                await self.sleep(1.0)
                started = self.mono()
                continue
            if resumed:
                resumed = False
                await room.say("Right. Where was I.", announce=True)
            quiet_since = max(run.step_end, getattr(room, "_turn_at", None) or 0.0, getattr(room, "_voice_at", None) or 0.0)
            talking = room.mode == BUSY or room.buf is not None or room.voice.locked()
            if first and not talking:
                return
            if not talking and (self.mono() - quiet_since >= self.gap_s or (self.mono() - started > TALK_WAIT_S and (room._turn_at or 0) < started)):
                return
            await self.sleep(0.25)

    async def _clip(self, room, clip: tuple[str, bytes], said: str) -> None:
        """A pre-recorded line (a fact from the quip bank), said in his own recorded voice, then the rest in words."""
        text, mp3 = clip
        async with room.voice:
            await room.link.show(text)
            room._also(("mp3", mp3))
            await room.link.announce(text, ("mp3", mp3))
        rest = said.strip()
        if rest:
            await room.say(rest, announce=True)

    # ── Core words things ──
    def word(self, task: str, material: str, max_tokens: int = 400, persona: bool = True) -> str | None:
        if self.client is None:
            return None
        system = self.persona + WORDING if persona else "Answer exactly as asked, nothing else."
        resp = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                           messages=[{"role": "user", "content": f"{task}\n\nMaterial:\n{material}"}])
        text = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        text = re.sub(r"<think>.*?</think>|\[IMAGE:[^\]]*\]", "", text, flags=re.S | re.I).strip()   # no picture tags in a spoken line
        return text or None

    async def aword(self, task: str, material: str, max_tokens: int = 400, persona: bool = True, fallback: str | None = None) -> str | None:
        """Core's words, or `fallback` (the plain sourced material) when Core is down or slow: a routine never goes silent for want of a model."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(self.word, task, material, max_tokens, persona), CORE_LIMIT_S) or fallback
        except Exception as e:                                        # noqa: BLE001
            self.log(f"(routine: Core didn't answer ({type(e).__name__}): plain words instead)")
            return fallback

    # ── the house ──
    def _entities(self, want) -> list[dict]:
        if self.house is None:
            return []
        out = []
        for s in self.house.states(fresh=True):
            name = str((s.get("attributes") or {}).get("friendly_name") or s.get("entity_id"))
            if want(s, name):
                out.append(s)
        return out

    def _lights_named(self, spec: str) -> list[dict]:
        names = [n.strip().lower() for n in (spec or "").split(",") if n.strip()]
        return self._entities(lambda s, name: name.lower() in names and s["entity_id"].split(".")[0] in ("light", "switch"))

    def _set(self, s: dict, on: bool, brightness: int | None = None) -> bool:
        domain = s["entity_id"].split(".")[0]
        data = {"entity_id": s["entity_id"]}
        if on and brightness is not None and domain == "light":
            data["brightness_pct"] = brightness
        try:
            self.house._service(domain, "turn_on" if on else "turn_off", data)
            return True
        except Exception as e:                                        # noqa: BLE001 — one light that doesn't answer never stops the rest
            self.log(f"(routine: {s['entity_id']}: {type(e).__name__}: {e})")
            return False

    async def lights_gently(self) -> str | None:
        lights = self._lights_named(self.env.get("SKIPPY_MORNING_LIGHTS", "Bedroom"))
        lights = [s for s in lights if s.get("state") != "on"]
        if not lights:
            return None
        dims = [s for s in lights if s["entity_id"].startswith("light.")]
        for pct in (5, 15, 30, 50) if dims else ():                   # about a minute from a glow to daylight
            for s in dims:
                await asyncio.to_thread(self._set, s, True, pct)
            await self.sleep(15)
        for s in lights:
            if s not in dims:                                         # a switch (the Bedroom's) has no dimmer: it just comes on, last
                await asyncio.to_thread(self._set, s, True)
        self.house.invalidate()
        return None

    async def homecoming_lights(self) -> str | None:
        spec = self.env.get("SKIPPY_HOMECOMING_LIGHTS", "kitchen under 1,kitchen under 2,kitchen under 3,track stove light")
        outdoor = self.env.get("SKIPPY_OUTDOOR_LIGHTS", "security Light,Deck sliding door")
        names = spec + ("," + outdoor if await asyncio.to_thread(self.dark) else "")
        done = 0
        for s in self._lights_named(names):
            if s.get("state") != "on" and await asyncio.to_thread(self._set, s, True):
                done += 1
        if self.house is not None:
            self.house.invalidate()
        self.log(f"(coming home: {done} light(s) on)")
        return None

    async def all_lights_off(self) -> str:
        def is_light(s, name):
            d = s["entity_id"].split(".")[0]
            return d == "light" or (d == "switch" and "light" in name.lower())
        on = [s for s in self._entities(is_light) if s.get("state") == "on"]
        off = sum(1 for ok in [await asyncio.to_thread(self._set, s, False) for s in on] if ok)
        if self.house is not None:
            self.house.invalidate()
        if not on:
            return "The lights were already off."
        return f"Lights off, all {off}." if off == len(on) else f"I turned off {off} of {len(on)} lights; the rest ignored me."

    def place(self) -> str:
        import news as news_mod                                       # noqa: PLC0415
        return news_mod.home_place(self.env)

    # ── the weather ──
    def forecast(self) -> dict | None:
        home = self.home()
        if home is None:
            return None
        q = {"latitude": f"{home[0]:.3f}", "longitude": f"{home[1]:.3f}", "timezone": "auto", "forecast_days": 2,
             "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
             "current": "temperature_2m,apparent_temperature,weather_code",
             "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,wind_speed_10m_max,sunrise,sunset,uv_index_max"}
        return self.get(f"{WEATHER_URL}?{urllib.parse.urlencode(q)}", 15)

    def dark(self) -> bool:
        try:
            d = (self.forecast() or {}).get("daily") or {}
            sunrise, sunset = d["sunrise"][0], d["sunset"][0]
        except (Exception,):                                          # noqa: BLE001 — no forecast: go by the clock
            h = time.localtime(self.clock()).tm_hour
            return h >= 18 or h < 7
        now = time.strftime("%Y-%m-%dT%H:%M", time.localtime(self.clock() + 1800))   # half an hour before sunset counts as dark
        return now >= sunset or time.strftime("%Y-%m-%dT%H:%M", time.localtime(self.clock())) < sunrise

    async def weather(self) -> str | None:
        f = await asyncio.to_thread(self.forecast)
        if not f:
            return None
        c, d = f.get("current") or {}, f.get("daily") or {}
        try:
            material = (f"Now: {c['temperature_2m']} F (feels like {c['apparent_temperature']} F), {WMO.get(c['weather_code'], 'unknown sky')}. "
                        f"Today: high {d['temperature_2m_max'][0]} F, low {d['temperature_2m_min'][0]} F, {WMO.get(d['weather_code'][0], 'mixed')}, "
                        f"chance of rain {d['precipitation_probability_max'][0]} percent, wind up to {d['wind_speed_10m_max'][0]} mph, "
                        f"UV index up to {d['uv_index_max'][0]}. Source: Open-Meteo's forecast for home.")
        except (KeyError, IndexError, TypeError):
            return None
        return await self.aword("Give him today's weather in two sentences, then one sentence on what to wear (a jacket, layers, an umbrella, "
                                "sunglasses) that follows from it. Use the temperatures exactly as given (46 F is cold, not freezing).", material, 220,
                                fallback=f"Today: a high of {d['temperature_2m_max'][0]} and a low of {d['temperature_2m_min'][0]} degrees, "
                                         f"{WMO.get(d['weather_code'][0], 'mixed')}, with a {d['precipitation_probability_max'][0]} percent chance of rain.")

    # ── the news ──
    async def headlines(self, subjects: list[tuple[str, str]], per: int = 3) -> list[tuple[str, dict]]:
        import news as news_mod                                       # noqa: PLC0415
        out, seen = [], set()
        for label, query in subjects:
            items = []
            for when in ("day", "week"):                              # a quiet day for that subject: the week's (as news.News does)
                try:
                    items = [it for it in await asyncio.to_thread(news_mod.search, query, when, self.get)
                             if not JUNK_RE.search(str(it.get("source") or "")) and it.get("url") not in seen]
                except Exception:                                     # noqa: BLE001
                    items = []
                if len(items) >= 2:
                    break
            seen |= {it.get("url") for it in items[:per]}
            out += [(label, it) for it in items[:per]]
        return out

    async def morning_news(self) -> str | None:
        spec = self.env.get("SKIPPY_MORNING_NEWS", MORNING_NEWS)
        subjects = [tuple(p.split(":", 1)) for p in spec.split("|") if ":" in p]
        found = await self.headlines(subjects)
        if not found:
            return "The news search isn't answering this morning, so no headlines. Enjoy the ignorance."
        if self.news is not None:
            self.news.adopt("this morning's news", [it for _, it in found])
        material = "\n".join(f"[{label}] {it.get('title')} ({it.get('source')}): {str(it.get('content') or '')[:300]}" for label, it in found)
        return await self.aword("This morning's news. Pick the two most important world stories, the newest scientific discovery, and the two most "
                                "interesting software, AR or AI stories. One or two sentences each, and name the source of each (\"Reuters says\"). "
                                "Skip anything that is only opinion, an ad or a stock tip; a kind with nothing worthwhile is left out without a word about it.", material, 700,
                                fallback=plain_headlines(found))

    async def latest_news(self) -> str | None:
        found = await self.headlines([("world", "major world news"), ("nation", "breaking news today")], per=4)
        if not found:
            return None
        if self.news is not None:
            self.news.adopt("today's news", [it for _, it in found])
        material = "\n".join(f"{it.get('title')} ({it.get('source')}): {str(it.get('content') or '')[:300]}" for _, it in found)
        return await self.aword("He just got home: welcome him home in a few words, then the three most important stories of the day (world events, politics, science; "
                                "never sports, celebrities or shopping), a sentence each, naming each source.", material, 500,
                                fallback="Welcome home. " + plain_headlines(found, 3))

    # ── fun ──
    def _said(self, kind: str) -> list[str]:
        return list((self.state.get("said") or {}).get(kind) or [])

    def _remember(self, kind: str, what: str) -> None:
        said = self.state.setdefault("said", {})
        said[kind] = (said.get(kind) or [])[-59:] + [what]
        self._save()

    async def joke(self) -> str | None:
        before = self._said("jokes")[-20:]
        text = await self.aword("Tell him one short, original, clean joke about science, engineering, space or computers. Just the joke and its punchline.",
                                "Jokes you already told (don't repeat or rework them):\n" + ("\n".join(before) or "(none yet)"), 160)
        if text:
            self._remember("jokes", text[:200])
            return "A joke, since you clearly need one. " + text
        return None

    async def word_of_day(self) -> str | None:
        """A word Core picks, its meaning and an example read from the web (SearXNG), never Core's own say-so."""
        import news as news_mod                                       # noqa: PLC0415
        used = self._said("words")
        raw = await self.aword("Name one uncommon but useful English word a curious engineer would enjoy learning. Answer with the word only.",
                               "Words already used (pick another): " + (", ".join(used) or "(none)"), 20, persona=False)
        bare = re.sub(r"\[[^\]]*\]|\*[^*]*\*", " ", raw or "")              # the persona's voice tags ([sighs], *monkeys*) are not the word
        w = next(iter(re.findall(r"[A-Za-z][A-Za-z-]{3,}", bare)), "").lower()
        if not w or w in used:
            return None
        try:
            hits = await asyncio.to_thread(news_mod.search, f"{w} definition meaning", "year", self.get, "general")
        except Exception:                                             # noqa: BLE001
            hits = []
        hits = [h for h in hits if w in (str(h.get("title")) + str(h.get("content"))).lower()][:4]
        if not hits:
            return None
        self._remember("words", w)
        material = "\n".join(f"{h.get('title')} ({h.get('source')}): {str(h.get('content') or '')[:300]}" for h in hits)
        return await self.aword(f"The word of the day is \"{w}\". Say the word, spell it once, give its meaning as these dictionary entries give it, and "
                                "one example sentence of your own using it correctly.", material, 220)

    async def science_fact(self):
        """A fact from the quip bank (each one checked against web sources when it was made: tools/quip_refresh.py), the least-played first."""
        if self.filler is None:
            return None
        kinds = [k for k in ("fact:space", "fact:physics", "fact:chemistry") if self.filler.has(k)]
        random.shuffle(kinds)
        for k in kinds:
            got = await asyncio.to_thread(self.filler.pick, k, "morning science fact")
            if got:
                return got
        return None

    # ── the car ──
    async def car_report(self, bedtime: bool = False) -> str | None:
        car = await asyncio.to_thread(self.car)
        if not car:
            return None
        rng = car.get("est_battery_range_km") or car.get("rated_battery_range_km")
        miles = f"{round(rng * KM_TO_MI)} miles" if rng else "an unknown range"
        plug = "plugged in" if car.get("plugged_in") else "not plugged in"
        charging = car.get("charging_state") or "unknown"
        need = (self.extras.get("miles_today") and await self.extras["miles_today"]()) or None
        material = (f"Battery {car.get('battery_level')} percent, range {miles}, {plug}, charging state {charging}, charge limit "
                    f"{car.get('charge_limit_soc')} percent." + (f" Today's calendar needs about {need} miles of driving." if need else ""))
        return await self.aword("One or two sentences on the car." + (" Warn him plainly if today's driving needs more range than it has." if need else ""),
                                material, 150, fallback="The car: " + material)

    async def car_bedtime(self) -> str | None:
        """Charging on if it's plugged in and not full or charging; Sentry armed (the guard: lights and locks, never the horn)."""
        car = await asyncio.to_thread(self.car)
        said = []
        if car and car.get("plugged_in") and car.get("charging_state") not in ("Charging", "Complete") \
                and (car.get("battery_level") or 0) < (car.get("charge_limit_soc") or 80):
            try:
                got = await asyncio.to_thread(self.post, f"{CAR_BLE_URL}/charge", {"action": "start"}, 120)
                said.append("The car is charging." if got.get("ok") else f"The car refused to charge: {got.get('error') or 'no reason'}.")
            except Exception as e:                                    # noqa: BLE001
                said.append(f"I couldn't reach the car to start charging ({type(e).__name__}).")
        elif car and car.get("plugged_in"):
            said.append(f"The car is plugged in and {('charging' if car.get('charging_state') == 'Charging' else 'already charged to its limit')}, "
                        f"at {car.get('battery_level')} percent.")
        elif car:
            said.append(f"The car isn't plugged in, at {car.get('battery_level')} percent. Plug it in, monkey.")
        if self.env.get("SKIPPY_BEDTIME_SENTRY", "arm") == "arm":
            try:
                got = await asyncio.to_thread(self.post, f"{CAR_FLEET_URL}/sentry", {"action": "arm"}, 90)
                said.append("Sentry is armed." if got.get("ok") else f"Sentry wouldn't arm: {got.get('error') or 'no reason'}.")
            except Exception:                                         # noqa: BLE001 — the guard (core/car-fleet) isn't running yet
                self.log("(bedtime: Sentry: the car's guard service isn't answering)")
        return " ".join(said) or None

    # ── his day ──
    def today_log(self) -> list[dict]:
        if self.hlog is None:
            return []
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        path = Path(self.hlog.root) / "log" / f"{day}.jsonl"
        out = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            pass
        return out

    async def day_summary(self) -> str | None:
        events = [e for e in self.today_log() if e.get("kind") in ("command", "action", "routine", "automation")]
        lines = [f"{e.get('time')} {e.get('kind')}: {e.get('words') or e.get('tool') or e.get('routine') or ''} {e.get('outcome') or ''}".strip() for e in events][-60:]
        extra = []
        for key in ("day", "tomorrow"):
            fn = self.extras.get(key)
            if fn is not None:
                got = await fn()
                if got:
                    extra.append(got)
        if not lines and not extra:
            return None
        material = "What the house heard and did today:\n" + ("\n".join(lines) or "(nothing)") + ("\n\n" + "\n\n".join(extra) if extra else "")
        return await self.aword("Sum up his day in two or three sentences from what the house saw, then tomorrow: his to-dos and his first event, "
                                "if the material has them.", material, 300)

    async def overnight(self) -> str | None:
        fn = self.extras.get("overnight")
        return await fn() if fn is not None else None

    # ── the routines ──
    def morning_steps(self, room, **_) -> list[Step]:
        t = time.localtime(self.clock())
        hello = f"Good morning, Captain. It's {clock_words(self.clock())} on {time.strftime('%A', t)}."

        async def greet():
            return hello

        steps = [Step("", act=self.lights_gently), Step("the weather", make=self._join(greet, self.weather)),
                 Step("the news", make=self.morning_news)]
        for key, title in (("calendar", "your calendar"), ("mail", "your mail"), ("birthdays", "birthdays"), ("packages", "packages")):
            fn = self.extras.get(key)
            if fn is not None:
                steps.append(Step(title, make=fn))
        steps += [Step("the car", make=self.car_report), Step("a joke", make=self.joke), Step("the word of the day", make=self.word_of_day),
                  Step("a science fact", make=self.science_fact), Step("the overnight report", make=self.overnight),
                  Step("music", act=self._music_on("Bilby's library, shuffled. Have a good day, monkey."), last=True)]
        return steps

    def homecoming_steps(self, room, lights: bool = True, **_) -> list[Step]:
        steps = [Step("", act=self.homecoming_lights)] if lights else []
        steps += [Step("the news", make=self.latest_news), Step("music", act=self._music_on(None)),
                  Step("", make=lambda: self.ask_about_day(room), last=True)]
        return steps

    def bedtime_steps(self, room, **_) -> list[Step]:
        async def lights_and_car():
            parts = [await self.all_lights_off(), await self.car_bedtime()]
            return " ".join(p for p in parts if p)
        stereo = self.stereo() if self.stereo is not None else None

        async def quiet():
            if stereo is not None and stereo.playing:
                await stereo.stop()
            return None
        return [Step("", act=quiet), Step("your day", act=lights_and_car), Step("your day and tomorrow", make=self.day_summary),
                Step("", act=self.plan_tomorrow), Step("", make=self.anything_else, last=True)]

    async def plan_tomorrow(self) -> str | None:
        """An early first event tomorrow moves tomorrow's morning earlier (SKIPPY_MORNING_LEAD_MIN before it, never before 04:30)."""
        fn = self.extras.get("first_tomorrow")
        ts = await fn() if fn is not None else None
        tomorrow = time.strftime("%Y-%m-%d", time.localtime(self.clock() + 86400))
        over = self.state.setdefault("morning_override", {})
        for d in [d for d in over if d < time.strftime("%Y-%m-%d", time.localtime(self.clock()))]:
            over.pop(d)
        if not ts:
            return None
        want = time.localtime(ts - 60 * float(self.env.get("SKIPPY_MORNING_LEAD_MIN", "90")))
        hm = (want.tm_hour, want.tm_min - want.tm_min % 5)
        if time.strftime("%Y-%m-%d", want) != tomorrow or hm >= hm_of(self.morning_at) or hm < (4, 30):
            over.pop(tomorrow, None)
            self._save()
            return None
        over[tomorrow] = f"{hm[0]:02d}:{hm[1]:02d}"
        self._save()
        return f"Your first thing tomorrow is early, so the morning briefing comes at {clock_words(time.mktime(want))}."

    async def anything_else(self) -> str:
        return "Anything on your mind before you sleep? I'm listening. If not, goodnight, Captain."

    async def ask_about_day(self, room) -> str:
        text = await self.aword("He just got home from work. Ask him about his day, warmly, in your own way, one or two sentences. Or, if the material "
                                "has a good subject, strike up a conversation about it instead.",
                                "(nothing else)", 120) or "So. How was your day, monkey?"
        if hasattr(room.brain, "offer"):
            room.brain.offer(text, "He just got home from work; you asked about his day.")
        return text

    def _join(self, *fns):
        async def go():
            parts = [await f() for f in fns]
            return " ".join(p for p in parts if p) or None
        return go

    def _music_on(self, said: str | None):
        async def go():
            stereo = self.stereo() if self.stereo is not None else None
            if stereo is None:
                return None
            n = await stereo.shuffle()
            return said if n else None
        return go


def main(argv=None) -> int:
    """python daily.py run morning|bedtime|homecoming : the running hub starts it in the routine room within a second."""
    import argparse                                                   # noqa: PLC0415
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("routine", choices=["morning", "bedtime", "homecoming"])
    args = ap.parse_args(argv)
    folder = Path(__file__).resolve().parents[2] / "state" / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "request.json").write_text(json.dumps({"routine": args.routine, "at": time.time()}), encoding="utf-8")
    print(f"asked the hub to run the {args.routine} routine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
