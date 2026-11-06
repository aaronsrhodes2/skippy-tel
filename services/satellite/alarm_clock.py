"""The house's alarm clock (2026-11-01: the Captain replaced his Echoes, and wants "a solid timer and alarm tracker that I can rely on to wake
me up in the morning"). ONE clock for the whole house, ticking on its own: an alarm rings even while every pod is off the Wi-Fi.

- The alarms are one list for the house (state/alarms/house.json; the old per-room files are merged into it once), set by voice in any room
  (alarms.py's tools) or on the portal's Alarms page. Each rings in a place: where he said, else a morning alarm rings upstairs, where he
  sleeps (SATELLITE_WAKE_PLACE, default "upstairs"): the Enclave on skippy-core's aux, and the pods of the rooms up there once they exist.
- Ringing: a chime, then "Good morning, it's 7:00 ...", once a minute, a little louder each time, until he answers or GIVE_UP_S goes by.
  Everything that falls due together, or while one rings, rings as ONE (an alarm leads; a reminder's words and a second place follow it): a reminder
  never silences an alarm. A reminder that rides an alarm's ring is said in its first two rings (a ring of reminders alone goes on until each has been said twice),
  and a reminder that rode a ring which ended before a whole ring was said, or an alarm that fell due in the very tick the ring gave up, comes back a minute later (a
  reminder that LED the ring and was stopped is not set again). Cancelling a repeating alarm that leads on his phone hands the ring to the next of them. Unanswered for ALERT_AFTER_S, his phone hears it too (the portal's /home/alert: a Discord DM with the Alarms page's Stop and Snooze).
- He answers with any word to a pod (alarms.Alarms.answered: "stop", "snooze"), or the Stop / Snooze keys on his phone (the portal queues
  them; this clock takes them on its next sync). Whatever comes first wins everywhere: the ringing is this one object's.
- The portal hears the whole schedule every SYNC_S (every second while one rings), so when this hub is down at alarm time the portal
  alerts his phone itself (portal/alarms.py), and the evening before it checks that the hub is reporting.
- Timers: each room's own (timers.py, the brain sets them), ticked here, so one that runs out while its pod is away is still said upstairs. A timer's line is
  said after the ring, in the background: it never holds the clock, or the next ring, up.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import re
import time
import wave
from collections import deque
from pathlib import Path

import alarms as alarms_mod

SYNC_S = 15                         # the schedule to the portal, normally
SYNC_RINGING_S = 1.0                # while one rings: his phone's Stop lands within a second or two
RING_GAP_S = 60
ALERT_AFTER_S = 3 * 60              # unanswered this long: his phone too
ALERT_AGAIN_S = 10 * 60             # and once more, if he still hasn't answered
GIVE_UP_S = 30 * 60                 # then it stops, and his phone is told it was never answered
REMINDER_RINGS = 2
POD_JOINS_AT_RING = 3               # a box whose room has a stereo that is on rings by it alone until this ring (2 minutes unanswered), then joins
LISTEN_AFTER_S = 1.2                # a silent box opens its "stop" window this long after the stereo went quiet: the Enclave's latency and the room's echo
RING_WAIT_S = 45                    # the longest a silent box waits for the stereo's ring (a clip is seconds long) before it speaks after all
FLOOR_WAIT_S = 20                   # a ring waits this long for Skippy to finish what he is saying (a routine's line, an answer) before it speaks over it: an alarm must wake him
GAINS = (0.45, 0.6, 0.75, 0.9, 1.0)  # each ring a little louder (the first never startles him out of bed)
CHECK_AT = "21:30"                  # the evening check: an alarm tomorrow and the way it rings is broken -> his phone is told tonight
SETTLE_S = 120                      # the evening check waits this long after the hub starts: the router has not opened the Enclave yet (2026-11-05: a restart at 22:30 told him
                                    # the upstairs speaker "isn't connected" a second before the router found it)
WAKE_WINDOW = ("04:30", "11:00")    # an alarm in these hours is a wake-up: the morning briefing is planned around the first of them (wake_time, daily.Daily.morning_time)
PORTAL_PAGE = "https://makerportal.live/alarms"


def chime(gain: float = 1.0, repeats: int = 2, rate: int = 16000) -> bytes:
    """An alarm sound, not speech: three rising notes, `repeats` times, as a 16-bit mono WAV (the router's decoder reads WAV as well as MP3)."""
    notes, frames = (880.0, 1174.7, 1568.0), bytearray()
    for _ in range(repeats):
        for f in notes:
            n = int(rate * 0.22)
            for i in range(n):
                env = min(1.0, i / (rate * 0.01)) * math.exp(-3.2 * i / n)      # a soft attack, a bell's fall
                v = gain * 0.6 * env * (math.sin(2 * math.pi * f * i / rate) + 0.3 * math.sin(4 * math.pi * f * i / rate))
                frames += int(max(-1.0, min(1.0, v)) * 32000).to_bytes(2, "little", signed=True)
        frames += bytes(2 * int(rate * 0.25))
    return wav(bytes(frames), rate)


def wav(pcm16: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def pcm_of(wav_bytes: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


def key_of(a) -> str:
    """One going-off of one alarm: a repeating alarm has a new key each day (the portal's backup tells them apart by this)."""
    return f"{a.id}@{int(a.due)}"


class AlarmClock:
    def __init__(self, state: Path | str | None, env: dict | None = None, gate=None, local_voice=None, router=None, post=None,
                 clock=time.time, log=print, layout=None):
        self.env = env or {}
        self.state = Path(state) if state else None
        self.clock, self.log, self.gate, self.local_voice = clock, log, gate, local_voice
        self.router = router                            # router.Router (the house's PC outputs: the Enclave, the HDMI), or None
        self.post = post or self._post
        self.layout = layout
        self.wake_place = self.env.get("SATELLITE_WAKE_PLACE", "upstairs")
        # a hub room's old names (SATELLITE_ROOM_ALIASES=theater=living: the pod moved upstairs 2026-11-01): what was set under one still finds it
        self.aliases = {k.strip(): v.strip() for k, _, v in (p.partition("=") for p in self.env.get("SATELLITE_ROOM_ALIASES", "").split(",")) if v.strip()}
        self.alarms = alarms_mod.Alarms(self.state / "alarms" / "house.json" if self.state else None, clock=clock, log=log, wake_place=self.wake_place)
        self._merge_old()
        self.timers: dict = {}                          # room -> timers.Timers
        self.ringing: dict | None = None                # {"alarm", "key", "since", "rings", "next", "alerts", "also": [(alarm, key)], "heard": {key: rings}}: everything due
                                                        # together rings as one (_fire); "heard" counts the whole rings a reminder's words were said in (it is said twice)
        self.fired: deque = deque(maxlen=30)            # what went off, and how it ended, for the portal
        self.done: list = []                            # command ids the portal sent, carried out (sent back on the next sync)
        self.changed = asyncio.Event() if _loop_running() else None
        self._checked = ""                              # the date of the last evening check
        self._born = self.clock()                       # when this clock was made (the evening check settles for SETTLE_S first)
        self._ring_task: asyncio.Task | None = None     # the ring being rung now (an answer cancels it between its parts)
        self._snoozed: dict[int, float] = {}            # snoozed copies still waiting to ring: id -> due (is_ringing: the morning briefing keeps quiet for them)
        self._bg: set = set()                           # the timers' lines being said, or waiting for a room's voice (_say_due)
        self._lines = asyncio.Lock()                    # one timer's line at a time across the rooms (they were said one after the other while tick() waited on each; now they are started, not waited for)
        self.hub = None

    # ── the house's one list ──
    def _merge_old(self) -> None:
        """The per-room files (state/alarms/<room>.json, before 2026-11-01) become part of the house list once; each alarm keeps its room."""
        if self.state is None:
            return
        for old in sorted((self.state / "alarms").glob("*.json")):
            if old.name == "house.json":
                continue
            try:
                items = json.loads(old.read_text(encoding="utf-8"))
                for raw in items:
                    a = alarms_mod.Alarm(**raw)
                    a.id, a.room = max((x.id for x in self.alarms.items), default=0) + 1, a.room or old.stem
                    self.alarms.items.append(a)
                self.alarms._save()
                old.rename(old.with_suffix(".merged"))
                if items:
                    self.log(f"(alarms: {len(items)} from {old.name} are now the house's)")
            except (OSError, ValueError, TypeError) as e:
                self.log(f"(alarms: could not merge {old.name}: {e})")

    def timers_for(self, room: str):
        from timers import Timers                       # noqa: PLC0415
        if room not in self.timers:
            self.timers[room] = Timers(path=self.state / "timers" / f"{room}.json" if self.state else None, clock=self.clock)
        return self.timers[room]

    def tools(self, room: str):
        return self.alarms.tools(room=room)

    def poke(self) -> None:
        if self.changed is not None:
            self.changed.set()

    # ── where it rings ──
    def _layout(self):
        if self.layout is None:
            import house_layout                          # noqa: PLC0415
            self.layout = house_layout.default()
        return self.layout

    def place_of(self, a) -> tuple[list[str], list[str]]:
        """(router outputs, hub rooms with a pod) where alarm `a` rings. A place he named ("upstairs", "everywhere", "the bedroom") is looked
        up in house_layout.json; "" is the room that set it (its pod, and the announcement outputs, as a timer's words go)."""
        hub_rooms = sorted(getattr(self.hub, "rooms", {}) or {})
        if not a.where:
            import speaker_out                          # noqa: PLC0415
            return speaker_out.announce_kinds(self.env), [self.hub_room(a.room)] if a.room else hub_rooms[:1]
        try:
            lay = self._layout()
            kinds = lay.outputs(a.where)
            rooms = lay.split(a.where)[0] or []
        except Exception:                               # noqa: BLE001 — no layout file: the place words of speaker_out
            kinds, rooms = None, []
        if kinds is None:
            import speaker_out                          # noqa: PLC0415
            kinds = speaker_out.place_kinds(self.env, a.where) or []
        pods = [r for r in hub_rooms if self._pod_room(r) in rooms]
        if not kinds and not pods:                      # a place with nothing to ring in (no speaker, no pod yet): where it was set, and upstairs
            return sorted(set(self._place_kinds(self.wake_place))), [self.hub_room(a.room)] if a.room else hub_rooms[:1]
        return sorted(set(kinds)), pods

    def hub_room(self, name: str) -> str:
        """The hub room `name` is now (an old name through SATELLITE_ROOM_ALIASES), else `name` itself."""
        return self.aliases.get(name, name) if name not in (getattr(self.hub, "rooms", {}) or {}) else name

    def _pod_room(self, hub_room: str) -> str:
        try:
            return self._layout().room_of_pod(hub_room)
        except Exception:                               # noqa: BLE001
            return hub_room

    def _place_kinds(self, word: str) -> list[str]:
        try:
            return self._layout().outputs(word) or []
        except Exception:                               # noqa: BLE001
            return []

    # ── the clock ──
    async def run(self, hub) -> None:
        self.hub = hub
        if self.changed is None:
            self.changed = asyncio.Event()
        self.log(f"alarm clock: {len(self.alarms.items)} set; a morning alarm rings {self.wake_place}")
        asyncio.ensure_future(self._sync_loop())
        while True:
            try:
                await self.tick()
            except Exception as e:                      # noqa: BLE001 — the clock never stops for one bad tick
                self.log(f"(alarm clock: {type(e).__name__}: {e})")
            await asyncio.sleep(0.5)

    async def tick(self) -> None:
        now = self.clock()
        self._fire(now)
        r = self.ringing
        if r is not None and now >= r["next"]:
            a = r["alarm"]
            if (a.kind == "reminder" and self._all_said(r)) or now - r["since"] >= GIVE_UP_S:
                self._end("gave_up")
                if a.kind == "alarm":
                    await self.alert(f"⏰ Your {a.hhmm} alarm rang for {GIVE_UP_S // 60} minutes and nobody answered. I've stopped it.")
            else:
                r["rings"] += 1
                r["next"] = now + RING_GAP_S
                joined, lead_key = self._joined(r), r["key"]
                if await self._ring_once(a, r["rings"], [m for m, _ in joined]):
                    self._said(r, [(a, lead_key)] + joined)
                # (an answer that came in while it rang ended it between the parts: there is nothing left to tell his phone he hasn't answered)
                lead = r["alarm"]                       # (the alarm that led may have been cancelled on his phone while it rang: _cancel_lead)
                if self.ringing is r and lead.kind == "alarm" and ((r["alerts"] == 0 and now - r["since"] >= ALERT_AFTER_S)
                                                                   or (r["alerts"] == 1 and now - r["since"] >= ALERT_AGAIN_S)):
                    r["alerts"] += 1
                    await self.alert(f"⏰ Your {lead.hhmm} alarm is ringing" + (f" ({lead.label})" if lead.label else "") + f" and you haven't answered. "
                                     f"Stop or snooze it: {PORTAL_PAGE}")
        await self._timers_due()                        # after the ring, never before it, and never waited for: a timer's line is said in the background (_say_due)
        await self.evening_check()

    def _fire(self, now: float) -> None:
        """What has fallen due starts ringing. The house has ONE ringing, so everything due together, or falling due while something rings, rings as one: the
        first alarm leads (an alarm is never dropped for a reminder), the others join it in `also` and ring in the same ring, each in its own place, with
        their words after its own. (Until 2026-11-05 the LAST one due won and the rest ended as "replaced": a 'take the pills' reminder set after the wake
        alarm for the same minute silenced the alarm, and a reminder that fell due while the alarm rang ended it.)"""
        keys = {a.id: key_of(a) for a in self.alarms.items if a.due <= now}
        fired = self.alarms.due()
        self.alarms.ringing = None                      # the ringing is this clock's (alarms.Alarms' own is for a room without a clock)
        if not fired:
            return
        new = []
        for a in fired:
            key = keys.get(a.id, key_of(a))
            self.log(f"{a.kind} going off: {a.hhmm} {a.label} ({a.where or a.room or 'here'})")
            self.fired.append({"key": key, "id": a.id, "kind": a.kind, "label": a.label, "hhmm": a.hhmm, "at": round(now), "ended": None, "by": None})
            new.append((a, key))
        cur = self.ringing
        members = (([(cur["alarm"], cur["key"])] + list(cur.get("also") or [])) if cur is not None else []) + new
        lead = next((m for m in members if m[0].kind == "alarm"), members[0])       # the alarm already ringing stays the one that leads
        rest = [m for m in members if m is not lead]
        if cur is not None and lead[1] == cur["key"]:
            cur["also"], cur["next"] = rest, now        # the newcomer is heard at once, in a ring of the one that is going
        else:                                           # (a reminder that was leading and is now one of the others keeps the count of the rings it was said in)
            self.ringing = {"alarm": lead[0], "key": lead[1], "since": now, "rings": 0, "next": now, "alerts": 0, "also": rest,
                            "heard": dict(cur.get("heard") or {}) if cur is not None else {}}
        self.poke()

    @staticmethod
    def _joined(r: dict) -> list:
        """The others in the ring (_fire) that are in THIS ring, as (alarm, key): an alarm in every one, a reminder in its first REMINDER_RINGS (a reminder is said
        twice, whatever it rides on: its words, and its place, are not repeated for as long as the alarm goes unanswered)."""
        heard = r.get("heard") or {}
        return [m for m in r.get("also") or [] if m[0].kind != "reminder" or heard.get(m[1], 0) < REMINDER_RINGS]

    @staticmethod
    def _said(r: dict, members: list) -> None:
        """A whole ring was rung (not cut short by an answer): everything in it has been rung once more (a reminder is said twice; an alarm that rode a ring has been RUNG, which is what
        keeps it from being put back when the ring gives up, _end)."""
        heard = r.setdefault("heard", {})
        for m, k in members:
            heard[k] = heard.get(k, 0) + 1

    @staticmethod
    def _all_said(r: dict) -> bool:
        """A ring of reminders alone (an alarm in a ring rings until it is answered, or gives up) is over when each has been said REMINDER_RINGS times: one that
        joins a reminder's ring gets its two rings as well."""
        heard = r.get("heard") or {}
        return all(heard.get(k, 0) >= REMINDER_RINGS for k in [r["key"]] + [k for _, k in r.get("also") or []])

    def _end(self, how: str, by: str = "") -> None:
        r, self.ringing = self.ringing, None
        if r is None:
            return
        task = self._ring_task
        if task is not None and not task.done():
            task.cancel()                               # a ring is several parts one after the other: an answer stops the ones that have not begun
        heard, back = r.get("heard") or {}, set()
        for m, k in r.get("also") or []:                # the reminders that rode this ring and were never said: the answer, or the end of the ring, came before one
            if (m.kind == "reminder" or how == "gave_up") and not heard.get(k) and self._put_back(m):       # whole ring (an answer to the alarm is not an answer to them); an ALARM that fell due in the tick the ring gave up was never rung at all (2026-11-05, round 2)
                back.add(k)
        keys = {r["key"]} | {k for _, k in r.get("also") or []}
        for f in self.fired:
            if f["key"] in keys and f["ended"] is None:
                f["ended"], f["by"], f["after_s"] = how, by or None, round(self.clock() - (r["since"] if f["key"] == r["key"] else f["at"]))
                if f["key"] in back:
                    f["ended"], f["by"] = "snoozed", "not heard"
        self.poke()

    def _put_back(self, reminder) -> bool:
        """A reminder that was never said is not lost (nor, when its ring gave up, an alarm that fell due in that very tick and so was never rung): a one-off was deleted from
        the list as it fell due, so it is set again, as a one-shot about a minute on, to ring on its own (`Alarms.snooze`: the same words, the same place). True when it was set."""
        try:
            copy = self.alarms.snooze(reminder, 1)
        except (ValueError, TypeError, OverflowError) as e:
            self.log(f"(alarm clock: the reminder {reminder.label!r} was not heard and could not be set again: {e})")
            return False
        self.log(f"(alarm clock: the reminder {reminder.label!r} was not heard: again at {alarms_mod.say_when(copy.due, self.clock())})")
        self._remember_snooze(copy)
        return True

    def _cancel_lead(self, r: dict) -> None:
        """The alarm that leads the ring was cancelled on his phone (a repeating one: a one-off has left the list as it fell due). Only its own record ends: the
        others in the ring (_fire) were not cancelled, so the next of them leads and the ring goes on as it was, a minute apart and louder each time. Alone in the
        ring, its cancelling ends the ring."""
        others = list(r.get("also") or [])
        if not others:
            self._end("cancelled", "phone")
            return
        for f in self.fired:
            if f["key"] == r["key"] and f["ended"] is None:
                f["ended"], f["by"], f["after_s"] = "cancelled", "phone", round(self.clock() - r["since"])
        heard = r.get("heard") or {}
        lead = (next((m for m in others if m[0].kind == "alarm"), None)                                  # an alarm still leads
                or next((m for m in others if heard.get(m[1], 0) < REMINDER_RINGS), others[0]))          # else a reminder not yet said twice (one that was is not read out a third time as the lead)
        r["alarm"], r["key"], r["also"] = lead[0], lead[1], [m for m in others if m is not lead]
        self.poke()                                     # (a reminder that leads on is said by its own count, `heard`: twice)

    async def _timers_due(self) -> None:
        """The rooms' timers that have run out are said in their rooms, each line in the background (_say_due): the clock goes straight on."""
        started = False
        for room, timers in list(self.timers.items()):
            for t in timers.due():                      # (each timer is handed out once: its line is started once)
                try:
                    self.log(f"[{room}] timer done: {t.label}")
                    self._say_due(room, f"Your {alarms_mod.timer_name(t.label)} timer is done.")
                except Exception as e:                  # noqa: BLE001 — one timer's line that cannot be started costs the others, and the rings, nothing
                    self.log(f"(alarm clock: the line for the {t.label!r} timer could not be started: {type(e).__name__}: {e})")
                started = True
                self.poke()
        if started:
            await asyncio.sleep(0)                      # the lines get their first turn now (a room that is free says it at once), not when the clock next waits

    def _say_due(self, room: str, text: str) -> asyncio.Task:
        """A timer's words in its room, started and left to say themselves. Never waited for: tick() used to wait up to FLOOR_WAIT_S for the line, and the next ring
        then waited its own floor on top of that, so an alarm that fell due while the clock was parked on a timer's line started up to twice that late. The room's
        voice lock has no bound (a long reply, a routine's part: daily SAY_LIMIT_S, 240 s) and is FIFO: the line waits for whoever holds it, and a ring that comes
        after it queues behind it for the floor (FLOOR_WAIT_S at most, then rings anyway); one that is already ringing is done before the line is started, so the line
        is said right after it. `_bg` keeps the task until it is done, and an exception in the line is logged there (_said_due), never raised into the clock."""
        task = asyncio.ensure_future(self.say_in(room, text))
        self._bg.add(task)
        task.add_done_callback(self._said_due)
        return task

    def _said_due(self, task) -> None:
        self._bg.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.log(f"(alarm clock: a timer's words were not said: {type(task.exception()).__name__}: {task.exception()})")

    # ── ringing ──
    async def _ring_once(self, a, n: int, also=()) -> bool:
        """One ring as a task of its own, so that an answer (`_end`) can stop it between its parts: the box, which rings after the Enclave has finished, never
        rings an alarm he has already stopped. True when the whole ring was rung, False when an answer cut it short."""
        task = self._ring_task = asyncio.ensure_future(self.ring(a, n, also))
        try:
            await task
            return True
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if task.cancelled() and (me is None or me.cancelling() == 0):
                return False                            # stopped by an answer, not cancelled from outside
            raise
        finally:
            if self._ring_task is task:
                self._ring_task = None

    def _also_words(self, b) -> str:
        """What another item due in the same ring adds to the leading one's words: a reminder's text, an alarm's name (an unnamed alarm adds nothing)."""
        if b.kind == "reminder":
            return self.alarms.spoken(b)
        name = re.sub(r"\s*\balarm$", "", (b.label or "").strip(), flags=re.I)
        return f"Your {name} alarm." if name else ""

    async def ring(self, a, n: int, also=()) -> None:
        """One ring: the chime (an alarm only) and the words, on the place's outputs and pods, each ring louder than the last. `also`: the others due in this
        same ring (_fire): each rings in its own place too, and their words follow the leading one's."""
        gain = GAINS[min(n - 1, len(GAINS) - 1)]
        text = self.alarms.spoken(a) + ("" if a.kind == "reminder" else " Or tap Stop on your phone." if n == 1 else "")
        kinds, pods = self.place_of(a)
        kinds, pods = list(kinds), list(pods)
        for b in also:
            extra, (more_kinds, more_pods) = self._also_words(b), self.place_of(b)
            if extra and extra not in text:
                text += " " + extra
            kinds += [k for k in more_kinds if k not in kinds]
            pods += [p for p in more_pods if p not in pods]
        tone = chime(gain, repeats=2 if n < 3 else 3) if a.kind == "alarm" else chime(gain * 0.7, repeats=1)
        voice = await self._voice(text)
        # The box's own speaker is the last resort (the Captain, 2026-11-04: "we never want to output to the hub speakers unless that house level's
        # stereo is unavailable"): a box whose room's stereo is on stays silent (it still shows the words, and stays deaf until the stereo is quiet)
        # while the stereo rings for it, and speaks after all if that ring fails. The software cannot see the stereo's own switch or knob, so the
        # box joins the stereo when nobody has answered in two minutes (an alarm's third ring; a reminder rings twice, so its second).
        joins_at = POD_JOINS_AT_RING if a.kind == "alarm" else min(POD_JOINS_AT_RING, REMINDER_RINGS)
        quiet, kinds, with_stereo = [], list(kinds), set()
        for room in pods:
            r = (getattr(self.hub, "rooms", {}) or {}).get(self.hub_room(room))
            try:
                outs = r.stereo_kinds() if r is not None and hasattr(r, "stereo_kinds") else []
            except Exception as e:                      # noqa: BLE001 — a failed look at the stereo must never keep an alarm quiet: the box rings
                self.log(f"(alarm: could not look at {room}'s stereo: {type(e).__name__}: {e}: the box rings)")
                outs = []
            if outs:
                with_stereo.add(room)
                kinds += [k for k in outs if k not in kinds]    # the room's stereo rings every time it is on: the box joins it, it never replaces it
                if n < joins_at:
                    quiet.append(room)
        self.log(f"ring {n} ({gain:.2f}): outputs {'+'.join(kinds) or 'none'}, pods {', '.join(pods) or 'none'}"
                 f"{' (silent: ' + ', '.join(quiet) + ')' if quiet else ''}: {text}")
        async with self._floor():                       # one Skippy at a time: the ring waits for a routine's line to end, and nothing starts over the ring
            stereo = asyncio.ensure_future(self._play_outs(kinds, [tone, voice], gain)) if kinds else None
            jobs = [stereo] if stereo is not None else []
            # a box waits for the stereo's ring only when it has a stereo of its own to wait for (hub.Room.ring waits whenever it is handed one): the pod of a room with
            # no stereo on rings at once, as before, not after the Enclave upstairs has finished its chime and words
            jobs += [self._ring_pod(room, tone, voice, text, quiet=room in quiet, wait=stereo if room in with_stereo else None) for room in pods]
            await asyncio.gather(*jobs, return_exceptions=True)

    @contextlib.asynccontextmanager
    async def _floor(self, who: str = "the ring"):
        """The house has one voice (the Captain, 2026-11-05: "I don't generally want two skippys speaking at the same time"; at 07:10 the alarm's rings were
        spoken over the morning routine's lines, because only the box's part of a ring waited for a room's voice lock and the Enclave's did not). A ring
        takes every room's voice lock, so a line already being said ends first and nothing new is said over the ring; the rooms' own ring (hub.Room.ring)
        is then told it holds the floor. After FLOOR_WAIT_S the ring speaks anyway: a long line must never keep him asleep. (A timer's line for a pod that is
        away takes the floor the same way, `who`: it has no voice lock of its own to wait for.)"""
        held, deadline = [], time.monotonic() + FLOOR_WAIT_S
        try:
            for name, room in sorted((getattr(self.hub, "rooms", {}) or {}).items()):
                lock = getattr(room, "voice", None)
                if not isinstance(lock, asyncio.Lock):
                    continue
                try:
                    await asyncio.wait_for(lock.acquire(), max(0.01, deadline - time.monotonic()))
                    held.append(lock)
                except asyncio.TimeoutError:
                    self.log(f"(alarm: {name} was still talking after {FLOOR_WAIT_S} s: {who} speaks over it)")
            yield held
        finally:
            for lock in held:
                lock.release()

    async def _voice(self, text: str) -> bytes | None:
        """Skippy's voice (mp3), else the machine's (as WAV), else None: the chime still rings."""
        if self.gate is not None:
            try:
                return await asyncio.to_thread(self.gate.tts, text)
            except Exception as e:                      # noqa: BLE001
                self.log(f"(alarm: Skippy's voice unavailable: {e}: the machine's voice)")
        if self.local_voice is not None:
            try:
                pcm, rate = await asyncio.to_thread(self.local_voice, text)
                return wav(pcm, rate)
            except Exception as e:                      # noqa: BLE001
                self.log(f"(alarm: no voice at all: {e}: the chime only)")
        return None

    async def _play_outs(self, kinds: list[str], clips: list, gain: float) -> bool:
        """The clips on the router's outputs, one after the other. True when something sounded, False when nothing did (no router, nothing open,
        every clip dropped): a box that stayed silent for this ring speaks after all (hub.Room.ring)."""
        if self.router is None:
            return False
        import speaker_out                              # noqa: PLC0415
        out = speaker_out.RoutedOut(self.router, kinds, log=self.log, duck_others=False)
        if not any(self.router.available(k) for k in kinds):
            self.log(f"(alarm: {'+'.join(kinds)} not open: nobody hears it there)")
            return False
        sounded = False
        for clip in clips:
            if clip:
                sounded = bool(await out.play(clip, gain=gain if clip[:4] == b"RIFF" else min(1.0, gain + 0.2))) or sounded
        return sounded

    async def _ring_pod(self, room: str, tone: bytes, voice: bytes | None, text: str, quiet: bool = False, wait=None) -> None:
        r = (getattr(self.hub, "rooms", {}) or {}).get(self.hub_room(room))
        if r is None:
            return
        # a real room's voice lock is held by the ring (_floor), whether or not it was got in time; a room that has none (a test's stand-in) is rung as it always was
        held = {"floor": True} if isinstance(getattr(r, "voice", None), asyncio.Lock) else {}
        await r.ring(tone, voice, text, quiet=quiet, wait=wait, **held)

    async def say_in(self, room: str, text: str) -> None:
        """A timer's words in its room (its pod and the announcement outputs), or, its pod away, the announcement outputs alone, once the house's voice is free."""
        async with self._lines:                         # one timer's line at a time across the rooms: two rooms' timers that fall due together are said one after the other, not over each other
            r = (getattr(self.hub, "rooms", {}) or {}).get(self.hub_room(room))
            if r is not None:
                await r.announce(text)
                return
            import speaker_out                          # noqa: PLC0415
            kinds = speaker_out.announce_kinds(self.env)
            voice = await self._voice(text)
            if kinds and voice:
                async with self._floor("the timer's line"):         # (tick() no longer waits for this line, so it takes its turn on the house's voice as a ring does)
                    await self._play_outs(kinds, [voice], 1.0)

    # ── answering ──
    def answered(self, words: str, by: str = "") -> str | None:
        """Words said to any pod while one rings (alarms.Alarms.answered decides what they mean); the reply, or None (not an answer)."""
        r = self.ringing
        if r is None:
            return None
        self.alarms.ringing = {"alarm": r["alarm"], "left": 0, "next": 0}
        before = {x.id for x in self.alarms.items}
        reply = self.alarms.answered(words)
        if reply is None:
            return None
        snoozed = "Snoozed" in reply
        if snoozed:
            for x in self.alarms.items:                 # (alarms.Alarms.answered made the leading one's copy itself)
                if x.id not in before:
                    self._remember_snooze(x)
            self._snooze_rest(r["alarm"], [m for m, _ in r.get("also") or []])
        self._end("snoozed" if snoozed else "stopped", by=by)
        return reply

    def _remember_snooze(self, s) -> None:
        """A snoozed copy of an alarm, waiting: is_ringing() stays true until it has rung (the morning briefing does not start in the nine minutes he asked for)."""
        if s.kind == "alarm":
            self._snoozed[s.id] = s.due

    def _snooze_rest(self, lead, also, minutes: int = alarms_mod.SNOOZE_S // 60) -> None:
        """A ring with more than one alarm in it (_fire): a snooze brings every alarm back, each in its own place; the leading one's copy is made by the
        caller. A second alarm for the same place is the same wake-up (one copy); a reminder in the ring was said, and is not asked again."""
        places = {(lead.where, lead.room)} if lead.kind == "alarm" else set()
        for b in also:
            if b.kind == "alarm" and (b.where, b.room) not in places:
                places.add((b.where, b.room))
                self._remember_snooze(self.alarms.snooze(b, minutes))

    def stop(self, by: str = "phone") -> str:
        if self.ringing is None:
            return "Nothing is ringing."
        a = self.ringing["alarm"]
        self._end("stopped", by=by)
        return "Alarm off." if a.kind == "alarm" else "Okay."

    WOKE_SNOOZE_S = 30

    def woke(self, by: str = "") -> bool:
        """'Skippy' said while one rings: it stops at once (the Captain, 2026-11-01: "alarms stop as soon as the wake word is invoked"). A
        "snooze" in the next WOKE_SNOOZE_S still snoozes it (snooze_after_wake). True when something was ringing."""
        if self.ringing is None:
            return False
        self._woke_stop = (self.ringing["alarm"], self.clock(), [m for m, _ in self.ringing.get("also") or []])
        self._end("stopped", by=f"wake word ({by})" if by else "wake word")
        return True

    def snooze_after_wake(self, words: str, by: str = "") -> str | None:
        """The words after the wake word that stopped an alarm: "snooze" puts it off nine minutes; anything else is for the brain (None)."""
        a, at, also = getattr(self, "_woke_stop", (None, 0.0, ()))
        w = (words or "").lower()
        if a is None or self.clock() - at > self.WOKE_SNOOZE_S:
            return None
        said = re.findall(r"[a-z']+", w)
        if said and all(t in alarms_mod.DISMISS for t in said):        # "Skippy, stop": already stopped by the wake word
            self._woke_stop = (None, 0.0, ())
            return "Alarm off." if a.kind == "alarm" else "Okay."
        if not alarms_mod.SNOOZE_RE.search(w):
            return None
        self._woke_stop = (None, 0.0, ())
        s = self.alarms.snooze(a)
        self._remember_snooze(s)
        self._snooze_rest(a, also)
        return f"Fine. Snoozed until {alarms_mod.say_when(s.due, self.clock())}. Nine minutes, not a second more."

    def snooze(self, by: str = "phone", minutes: int = alarms_mod.SNOOZE_S // 60) -> str:
        if self.ringing is None:
            return "Nothing is ringing."
        a = self.ringing["alarm"]
        s = self.alarms.snooze(a, minutes)
        self._remember_snooze(s)
        self._snooze_rest(a, [m for m, _ in self.ringing.get("also") or []], minutes)
        self._end("snoozed", by=by)
        return f"Snoozed until {alarms_mod.say_when(s.due, self.clock())}."

    # ── the portal ──
    def view(self) -> dict:
        now = self.clock()
        items = []
        for a in sorted(self.alarms.items, key=lambda a: a.due):
            kinds, pods = self.place_of(a)
            items.append({"id": a.id, "kind": a.kind, "key": key_of(a), "due": round(a.due), "hhmm": a.hhmm, "days": a.days, "label": a.label,
                          "where": a.where or a.room or "", "when": alarms_mod.say_when(a.due, now),
                          "repeat": alarms_mod.say_days(a.days) if a.days else "", "outputs": kinds, "pods": pods})
        timers = [{"room": room, "label": t.label, "due": round(t.due), "seconds": t.seconds}
                  for room, ts in self.timers.items() for t in sorted(ts._timers, key=lambda t: t.due)]
        r = self.ringing
        ringing = None if r is None else {"key": r["key"], "id": r["alarm"].id, "kind": r["alarm"].kind, "label": r["alarm"].label,
                                          "hhmm": r["alarm"].hhmm, "since": round(r["since"]), "rings": r["rings"]}
        return {"at": round(now, 1), "alarms": items, "timers": timers, "ringing": ringing, "fired": list(self.fired),
                "health": self.health(), "rooms": sorted(getattr(self.hub, "rooms", {}) or {}), "done": list(self.done)}

    def health(self) -> dict:
        out = {}
        if self.router is not None:
            for k in self._place_kinds(self.wake_place) or ["enclave"]:
                out[k] = bool(self.router.available(k))
        return out

    def command(self, cmd: dict) -> str:
        """One thing the portal's Alarms page asked: stop, snooze, cancel (an alarm by id, or a timer), add an alarm or a timer."""
        op = str(cmd.get("op") or "")
        try:
            if op == "stop":
                return self.stop("phone")
            if op == "snooze":
                return self.snooze("phone", int(cmd.get("minutes") or alarms_mod.SNOOZE_S // 60))
            if op == "cancel":
                gone = int(cmd.get("alarm"))
                ok = self.alarms.cancel_id(gone)
                r = self.ringing
                if ok and r is not None:
                    if r["alarm"].id == gone:
                        self._cancel_lead(r)            # only its own record ends; the next of the ring leads on (alone, the ring ends)
                    else:                               # one of the others in the ring: the ring goes on without it
                        keys = {k for m, k in r.get("also") or [] if m.id == gone}
                        r["also"] = [m for m in r.get("also") or [] if m[0].id != gone]
                        for f in self.fired:
                            if f["key"] in keys and f["ended"] is None:
                                f["ended"], f["by"] = "cancelled", "phone"
                return "Cancelled." if ok else "That one is already gone."
            if op == "add":
                a = self.alarms.add(str(cmd.get("kind") or "alarm") if cmd.get("kind") in ("alarm", "reminder") else "alarm",
                                    str(cmd.get("time") or ""), str(cmd.get("label") or ""), str(cmd.get("repeat") or "once"),
                                    str(cmd.get("date") or ""), where=str(cmd["where"]) if cmd.get("where") else None)
                return f"Set for {alarms_mod.say_when(a.due, self.clock())}" + (f", {alarms_mod.say_days(a.days)}" if a.days else "") + "."
            if op == "timer":
                room = str(cmd.get("room") or "") or next(iter(sorted(getattr(self.hub, "rooms", {}) or {})), "") or "house"
                t = self.timers_for(room).add(int(cmd.get("seconds") or 0), str(cmd.get("label") or ""))
                return f"{t.label} timer started."
            if op == "cancel_timer":
                n = sum(ts.cancel(str(cmd.get("label") or "")) for room, ts in self.timers.items() if not cmd.get("room") or room == cmd.get("room"))
                return f"Cancelled {n}." if n else "No such timer."
        except (ValueError, TypeError) as e:
            return f"Not done: {e}"
        return "I don't know that one."

    def _post(self, path: str, body: dict) -> dict:
        import requests                                  # noqa: PLC0415
        import music                                     # noqa: PLC0415
        portal = (self.env.get("PORTAL_URL") or music.PORTAL).rstrip("/")
        r = requests.post(portal + path, json=body, headers={"Authorization": f"Bearer {self.env.get('SKIPPY_HOME_TOKEN', '')}"}, timeout=10)
        r.raise_for_status()
        return r.json() if r.content else {}

    async def sync(self) -> None:
        body = self.view()
        try:
            reply = await asyncio.to_thread(self.post, "/home/alarms", body)
        except Exception as e:                          # noqa: BLE001 — the portal away: the clock rings all the same
            if not getattr(self, "_sync_warned", False):
                self.log(f"(alarm clock: the portal didn't take the schedule: {type(e).__name__}: {e})")
                self._sync_warned = True
            return
        self._sync_warned = False
        self.done = [d for d in self.done if d["id"] not in set((reply or {}).get("ack") or [])]
        for cmd in (reply or {}).get("commands") or []:
            if isinstance(cmd, dict) and cmd.get("id") and cmd["id"] not in {d["id"] for d in self.done}:
                said = self.command(cmd)
                self.log(f"alarm clock: the portal asked {cmd.get('op')}: {said}")
                self.done.append({"id": cmd["id"], "said": said})
                self.poke()

    async def _sync_loop(self) -> None:
        while True:
            await self.sync()
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), SYNC_RINGING_S if self.ringing else SYNC_S)
            except asyncio.TimeoutError:
                pass

    async def alert(self, text: str) -> bool:
        """His phone: the portal's /home/alert (one Discord DM)."""
        self.log(f"alarm clock: alert: {text}")
        try:
            got = await asyncio.to_thread(self.post, "/home/alert", {"text": text})
            return bool((got or {}).get("sent"))
        except Exception as e:                          # noqa: BLE001
            self.log(f"(alarm clock: the alert didn't reach the portal: {type(e).__name__}: {e})")
            return False

    # ── the morning routine plans around the alarm (daily.Daily.morning_time) ──
    def wake_time(self, date: str) -> str | None:
        """The first wake-up alarm (an alarm, never a reminder, in WAKE_WINDOW: 04:30-11:00) that rings on `date` ("2026-11-05"), as "HH:MM"; None when none does.
        Read from the schedule, not from `due`: a repeating alarm's `due` is already next week's once it has rung, and the answer must not change then.
        The window is applied to each alarm before the first is taken: a one-off at 03:30 for an early drive, or a snooze made at 23:56 that is due at 00:05,
        must not hide the 07:00 alarm the morning is planned around."""
        try:
            weekday = alarms_mod.DAYS[time.strptime(date, "%Y-%m-%d").tm_wday]
        except ValueError:
            return None
        now = self.clock()
        times = []
        for a in self.alarms.items:
            if a.kind != "alarm":
                continue
            if a.days:
                if weekday not in a.days or (date == time.strftime("%Y-%m-%d", time.localtime(now)) and not self._rings_today(a, now)):
                    continue
            elif time.strftime("%Y-%m-%d", time.localtime(a.due)) != date:
                continue
            try:
                h, m = alarms_mod.parse_hhmm(a.hhmm)    # (a hand-edited house.json can hold "0700" or "7")
            except ValueError:
                continue
            hm = f"{h:02d}:{m:02d}"
            if WAKE_WINDOW[0] <= hm < WAKE_WINDOW[1]:
                times.append(hm)
        return min(times) if times else None

    def _rings_today(self, a, now: float) -> bool:
        """A repeating alarm on today's list rings today when its next going-off is still today, or when it already rang today (its `due` is tomorrow's, or next
        week's, once it has). One set up after its time today ('wake me at 5:30 every day' at 06:00) first rings tomorrow: it is not part of today."""
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if time.strftime("%Y-%m-%d", time.localtime(a.due)) == today:
            return True
        return any(f["id"] == a.id and f["hhmm"] == a.hhmm and f["kind"] == "alarm" and time.strftime("%Y-%m-%d", time.localtime(f["at"])) == today
                   for f in self.fired)

    def is_ringing(self) -> bool:
        """Something rings now, or a snoozed alarm is waiting to ring again. The morning briefing (daily.Daily.tick) never starts while this is true: it would
        talk over the ring, or start the moment he said 'snooze' with the Bedroom light coming up and the news, in the nine minutes he asked for."""
        if self.ringing is not None:
            return True
        live = {a.id: a.due for a in self.alarms.items}
        self._snoozed = {i: due for i, due in self._snoozed.items() if live.get(i) == due}   # one that has rung (a one-off is deleted as it fires) or was cancelled is no longer
        return bool(self._snoozed)                                                              # waiting; (an id that was given to a new alarm since has another due)

    # ── the evening before ──
    def next_wake(self, within_s: float = 20 * 3600):
        """The next alarm (not a reminder) in the coming `within_s`, or None."""
        now = self.clock()
        soon = [a for a in self.alarms.items if a.kind == "alarm" and now < a.due <= now + within_s]
        return min(soon, key=lambda a: a.due) if soon else None

    def problems(self, a) -> list[str]:
        kinds, pods = self.place_of(a)
        out = []
        if self.router is not None and kinds and not any(self.router.available(k) for k in kinds) and not pods:
            import speaker_out                          # noqa: PLC0415
            out.append(f"the {speaker_out.place_words(self.env, kinds) or '+'.join(kinds)} speaker isn't connected")
        return out

    def tomorrow_line(self) -> str:
        """For the bedtime routine: "Your alarm is set for 6:30." or "No alarm is set for tomorrow.", and anything that would keep it quiet."""
        a = self.next_wake()
        if a is None:
            return "No alarm is set for tomorrow."
        hm = f"{int(a.hhmm[:2]) % 12 or 12}:{a.hhmm[3:]}"
        line = f"Your alarm is set for {hm}" + (f", {a.label}" if a.label else "") + "."
        trouble = self.problems(a)
        if trouble:
            line += f" But {' and '.join(trouble)}, so I'll ring your phone as well."
        return line

    async def evening_check(self) -> None:
        """At CHECK_AT, once a day: an alarm in the next 12 hours and something that would keep it quiet -> his phone hears tonight."""
        now = self.clock()
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._checked == today or time.strftime("%H:%M", time.localtime(now)) < self.env.get("SATELLITE_ALARM_CHECK_AT", CHECK_AT):
            return
        if now - self._born < SETTLE_S:                 # too early to judge: the outputs are still being found (the day is not marked, so it is judged a moment later)
            return
        self._checked = today
        a = self.next_wake(12 * 3600)
        if a is None:
            return
        trouble = self.problems(a)
        if trouble:
            await self.alert(f"⏰ Tomorrow's {a.hhmm} alarm: {' and '.join(trouble)}. I'll still ring the pods and your phone; "
                             f"check the speaker before bed. {PORTAL_PAGE}")


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
