"""The house's alarm clock (2026-11-01: the Captain replaced his Echoes, and wants "a solid timer and alarm tracker that I can rely on to wake
me up in the morning"). ONE clock for the whole house, ticking on its own: an alarm rings even while every pod is off the Wi-Fi.

- The alarms are one list for the house (state/alarms/house.json; the old per-room files are merged into it once), set by voice in any room
  (alarms.py's tools) or on the portal's Alarms page. Each rings in a place: where he said, else a morning alarm rings upstairs, where he
  sleeps (SATELLITE_WAKE_PLACE, default "upstairs"): the Enclave on skippy-core's aux, and the pods of the rooms up there once they exist.
- Ringing: a chime, then "Good morning, it's 7:00 ...", once a minute, a little louder each time, until he answers or GIVE_UP_S goes by.
  Unanswered for ALERT_AFTER_S, his phone hears it too (the portal's /home/alert: a Discord DM with the Alarms page's Stop and Snooze).
- He answers with any word to a pod (alarms.Alarms.answered: "stop", "snooze"), or the Stop / Snooze keys on his phone (the portal queues
  them; this clock takes them on its next sync). Whatever comes first wins everywhere: the ringing is this one object's.
- The portal hears the whole schedule every SYNC_S (every second while one rings), so when this hub is down at alarm time the portal
  alerts his phone itself (portal/alarms.py), and the evening before it checks that the hub is reporting.
- Timers: each room's own (timers.py, the brain sets them), ticked here, so one that runs out while its pod is away is still said upstairs.
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
        self.ringing: dict | None = None                # {"alarm", "key", "since", "rings", "next", "alerts"}
        self.fired: deque = deque(maxlen=30)            # what went off, and how it ended, for the portal
        self.done: list = []                            # command ids the portal sent, carried out (sent back on the next sync)
        self.changed = asyncio.Event() if _loop_running() else None
        self._checked = ""                              # the date of the last evening check
        self._ring_task: asyncio.Task | None = None     # the ring being rung now (an answer cancels it between its parts)
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
        for room, timers in list(self.timers.items()):
            for t in timers.due():
                self.log(f"[{room}] timer done: {t.label}")
                await self.say_in(room, f"Your {alarms_mod.timer_name(t.label)} timer is done.")
                self.poke()
        keys = {a.id: key_of(a) for a in self.alarms.items if a.due <= now}
        fired = self.alarms.due()
        self.alarms.ringing = None                      # the ringing is this clock's (alarms.Alarms' own is for a room without a clock)
        for a in fired:
            self.log(f"{a.kind} going off: {a.hhmm} {a.label} ({a.where or a.room or 'here'})")
            self._end("replaced")
            self.ringing = {"alarm": a, "key": keys.get(a.id, key_of(a)), "since": now, "rings": 0, "next": now, "alerts": 0}
            self.fired.append({"key": self.ringing["key"], "id": a.id, "kind": a.kind, "label": a.label, "hhmm": a.hhmm, "at": round(now),
                               "ended": None, "by": None})
            self.poke()
        r = self.ringing
        if r is not None and now >= r["next"]:
            a = r["alarm"]
            limit = REMINDER_RINGS if a.kind == "reminder" else None
            if (limit is not None and r["rings"] >= limit) or now - r["since"] >= GIVE_UP_S:
                self._end("gave_up")
                if a.kind == "alarm":
                    await self.alert(f"⏰ Your {a.hhmm} alarm rang for {GIVE_UP_S // 60} minutes and nobody answered. I've stopped it.")
                return
            r["rings"] += 1
            r["next"] = now + RING_GAP_S
            await self._ring_once(a, r["rings"])
            if a.kind == "alarm" and ((r["alerts"] == 0 and now - r["since"] >= ALERT_AFTER_S) or (r["alerts"] == 1 and now - r["since"] >= ALERT_AGAIN_S)):
                r["alerts"] += 1
                await self.alert(f"⏰ Your {a.hhmm} alarm is ringing" + (f" ({a.label})" if a.label else "") + f" and you haven't answered. "
                                 f"Stop or snooze it: {PORTAL_PAGE}")
        await self.evening_check()

    def _end(self, how: str, by: str = "") -> None:
        r, self.ringing = self.ringing, None
        if r is None:
            return
        task = self._ring_task
        if task is not None and not task.done():
            task.cancel()                               # a ring is several parts one after the other: an answer stops the ones that have not begun
        for f in self.fired:
            if f["key"] == r["key"] and f["ended"] is None:
                f["ended"], f["by"], f["after_s"] = how, by or None, round(self.clock() - r["since"])
        self.poke()

    # ── ringing ──
    async def _ring_once(self, a, n: int) -> None:
        """One ring as a task of its own, so that an answer (`_end`) can stop it between its parts: the box, which rings after the Enclave has finished, never
        rings an alarm he has already stopped."""
        task = self._ring_task = asyncio.ensure_future(self.ring(a, n))
        try:
            await task
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if task.cancelled() and (me is None or me.cancelling() == 0):
                return                                  # stopped by an answer, not cancelled from outside
            raise
        finally:
            if self._ring_task is task:
                self._ring_task = None

    async def ring(self, a, n: int) -> None:
        """One ring: the chime (an alarm only) and the words, on the place's outputs and pods, each ring louder than the last."""
        gain = GAINS[min(n - 1, len(GAINS) - 1)]
        text = self.alarms.spoken(a) + ("" if a.kind == "reminder" else " Or tap Stop on your phone." if n == 1 else "")
        kinds, pods = self.place_of(a)
        tone = chime(gain, repeats=2 if n < 3 else 3) if a.kind == "alarm" else chime(gain * 0.7, repeats=1)
        voice = await self._voice(text)
        # The box's own speaker is the last resort (the Captain, 2026-11-04: "we never want to output to the hub speakers unless that house level's
        # stereo is unavailable"): a box whose room's stereo is on stays silent (it still shows the words, and stays deaf until the stereo is quiet)
        # while the stereo rings for it, and speaks after all if that ring fails. The software cannot see the stereo's own switch or knob, so the
        # box joins the stereo when nobody has answered in two minutes (an alarm's third ring; a reminder rings twice, so its second).
        joins_at = POD_JOINS_AT_RING if a.kind == "alarm" else min(POD_JOINS_AT_RING, REMINDER_RINGS)
        quiet, kinds = [], list(kinds)
        for room in pods:
            r = (getattr(self.hub, "rooms", {}) or {}).get(self.hub_room(room))
            try:
                outs = r.stereo_kinds() if r is not None and hasattr(r, "stereo_kinds") else []
            except Exception as e:                      # noqa: BLE001 — a failed look at the stereo must never keep an alarm quiet: the box rings
                self.log(f"(alarm: could not look at {room}'s stereo: {type(e).__name__}: {e}: the box rings)")
                outs = []
            if outs:
                kinds += [k for k in outs if k not in kinds]    # the room's stereo rings every time it is on: the box joins it, it never replaces it
                if n < joins_at:
                    quiet.append(room)
        self.log(f"ring {n} ({gain:.2f}): outputs {'+'.join(kinds) or 'none'}, pods {', '.join(pods) or 'none'}"
                 f"{' (silent: ' + ', '.join(quiet) + ')' if quiet else ''}: {text}")
        async with self._floor():                       # one Skippy at a time: the ring waits for a routine's line to end, and nothing starts over the ring
            stereo = asyncio.ensure_future(self._play_outs(kinds, [tone, voice], gain)) if kinds else None
            jobs = [stereo] if stereo is not None else []
            jobs += [self._ring_pod(room, tone, voice, text, quiet=room in quiet, wait=stereo) for room in pods]
            await asyncio.gather(*jobs, return_exceptions=True)

    @contextlib.asynccontextmanager
    async def _floor(self):
        """The house has one voice (the Captain, 2026-11-05: "I don't generally want two skippys speaking at the same time"; at 07:10 the alarm's rings were
        spoken over the morning routine's lines, because only the box's part of a ring waited for a room's voice lock and the Enclave's did not). A ring
        takes every room's voice lock, so a line already being said ends first and nothing new is said over the ring; the rooms' own ring (hub.Room.ring)
        is then told it holds the floor. After FLOOR_WAIT_S the ring speaks anyway: a long line must never keep him asleep."""
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
                    self.log(f"(alarm: {name} was still talking after {FLOOR_WAIT_S} s: the ring speaks over it)")
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
        """A timer's words in its room (its pod and the announcement outputs), or, its pod away, the announcement outputs alone."""
        r = (getattr(self.hub, "rooms", {}) or {}).get(self.hub_room(room))
        if r is not None:
            await r.announce(text)
            return
        import speaker_out                              # noqa: PLC0415
        kinds = speaker_out.announce_kinds(self.env)
        voice = await self._voice(text)
        if kinds and voice:
            await self._play_outs(kinds, [voice], 1.0)

    # ── answering ──
    def answered(self, words: str, by: str = "") -> str | None:
        """Words said to any pod while one rings (alarms.Alarms.answered decides what they mean); the reply, or None (not an answer)."""
        r = self.ringing
        if r is None:
            return None
        self.alarms.ringing = {"alarm": r["alarm"], "left": 0, "next": 0}
        reply = self.alarms.answered(words)
        if reply is None:
            return None
        self._end("snoozed" if "Snoozed" in reply else "stopped", by=by)
        return reply

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
        self._woke_stop = (self.ringing["alarm"], self.clock())
        self._end("stopped", by=f"wake word ({by})" if by else "wake word")
        return True

    def snooze_after_wake(self, words: str, by: str = "") -> str | None:
        """The words after the wake word that stopped an alarm: "snooze" puts it off nine minutes; anything else is for the brain (None)."""
        a, at = getattr(self, "_woke_stop", (None, 0.0))
        w = (words or "").lower()
        if a is None or self.clock() - at > self.WOKE_SNOOZE_S:
            return None
        said = re.findall(r"[a-z']+", w)
        if said and all(t in alarms_mod.DISMISS for t in said):        # "Skippy, stop": already stopped by the wake word
            self._woke_stop = (None, 0.0)
            return "Alarm off." if a.kind == "alarm" else "Okay."
        if not alarms_mod.SNOOZE_RE.search(w):
            return None
        self._woke_stop = (None, 0.0)
        s = self.alarms.snooze(a)
        return f"Fine. Snoozed until {alarms_mod.say_when(s.due, self.clock())}. Nine minutes, not a second more."

    def snooze(self, by: str = "phone", minutes: int = alarms_mod.SNOOZE_S // 60) -> str:
        if self.ringing is None:
            return "Nothing is ringing."
        a = self.ringing["alarm"]
        s = self.alarms.snooze(a, minutes)
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
                ok = self.alarms.cancel_id(int(cmd.get("alarm")))
                if ok and self.ringing and self.ringing["alarm"].id == int(cmd.get("alarm")):
                    self._end("cancelled", "phone")
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
        """The first alarm (never a reminder) that rings on `date` ("2026-11-05"), as "HH:MM"; None when none does. Read from the schedule, not from
        `due`: a repeating alarm's `due` is already next week's once it has rung, and the answer must not change then."""
        try:
            weekday = alarms_mod.DAYS[time.strptime(date, "%Y-%m-%d").tm_wday]
        except ValueError:
            return None
        times = []
        for a in self.alarms.items:
            if a.kind != "alarm":
                continue
            if a.days:
                if weekday not in a.days:
                    continue
            elif time.strftime("%Y-%m-%d", time.localtime(a.due)) != date:
                continue
            times.append(a.hhmm)
        return min(times) if times else None

    def is_ringing(self) -> bool:
        return self.ringing is not None

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
