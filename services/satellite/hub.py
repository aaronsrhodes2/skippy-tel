"""The hub: Skippy in every room. skippy-pc does the hearing, thinking and talking; a room only needs a box with a microphone and a speaker.
Two kinds of box, both dialled by the hub (the same direction Home Assistant uses), both driving the same Room logic:

* **an ESPHome box** (`esphome://host`): the Waveshare ESP32-S3 86 boxes, flashed with rooms/skippy-room.yaml. The hub speaks ESPHome's own
  API to it (esphome_link.py, aioesphomeapi: what Home Assistant uses), so no Home Assistant is needed. It streams its microphone; the hub
  hears "Skippy"; Skippy's voice and Bilby's music go back as URLs it plays from the hub's media server (media.py).
* **a Wyoming satellite** (`host[:port]`, port 10700): wyoming-satellite on a Raspberry Pi, for example. It either heard "Skippy" itself
  (its own openWakeWord with our model; start_stage `asr`) or streams everything (start_stage `wake`) and the hub listens for the name.

    box  --(audio)-->  hub: the "Skippy" spotter (wakeword.py), Vad, the voice gate's Whisper, the quick brain, Skippy's local voice (Kokoro + F5-TTS)
         <--(what it heard, Skippy's voice, the music)--

Nothing is recorded or kept, as in ear.py. Timers are per room and are announced in the room that set them; each room has its own short
memory; the house (lights, plugs) is shared. A Room never knows which kind of box it is talking to: that is its Link (WyomingLink here,
EsphomeLink in esphome_link.py).

    python hub.py                    rooms from SATELLITE_ROOMS, e.g. "kitchen=esphome://skippy-kitchen.local,den=192.168.1.41"
    python hub.py --say kitchen "Dinner is ready, monkeys."      say something in one room, then quit
"""
from __future__ import annotations

import argparse
import re
import asyncio
import sys
import time
from array import array
from collections import deque
from pathlib import Path

from vad import Vad, rms
from voicegate import BudgetSpent, GateError, wav_bytes
from wake import is_goodbye, is_stop, wake_split

PORT = 10700                                                    # Wyoming's
ESPHOME_PORT = 6053                                             # ESPHome's native API
RATE, WIDTH, CHANNELS = 16000, 2, 1                             # what the hub hears in (boxes are asked for this; others are converted)
BLOCK = 800                                                     # 50 ms: what the Vad's numbers were tuned on (the same as ear.py)
BLOCK_MS = 50
CHUNK = 2048                                                    # samples per audio-chunk sent back over Wyoming
IDLE, LISTENING, FOLLOWUP, BUSY = "idle", "listening", "followup", "busy"
PRESENT_S = 20 * 60                                             # a voice heard in the room this recently: someone is around (for a conversation starter)
HUSH_S = 45                                                     # ... but not in the last this many seconds: never talk over people
AFTER_TURN_S = 180                                              # nor this soon after he last spoke to Skippy
STARTER_ANSWER_S = 180                                          # an offered starter still takes "Skippy, tell me more" this long


def parse_rooms(spec: str) -> list[tuple[str, str, str, int]]:
    """"kitchen=esphome://skippy-kitchen.local, den=192.168.1.41:10701" ->
    [("kitchen", "esphome", "skippy-kitchen.local", 6053), ("den", "wyoming", "192.168.1.41", 10701)]."""
    rooms = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, eq, addr = (s.strip() for s in part.partition("="))
        kind = "wyoming"
        if addr.startswith("esphome://"):
            kind, addr = "esphome", addr[len("esphome://"):]
        elif addr.startswith("wyoming://"):
            addr = addr[len("wyoming://"):]
        host, colon, port = addr.rpartition(":") if ":" in addr else (addr, "", "")
        if not eq or not name or not host or (colon and not port.isdigit()) or not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError(f"a room is name=host[:port] or name=esphome://host, not {part!r}")
        rooms.append((name, kind, host, int(port) if port else (ESPHOME_PORT if kind == "esphome" else PORT)))
    if len({r[0] for r in rooms}) != len(rooms):
        raise ValueError("two rooms with the same name")
    return rooms


def ev(kind: str, data: dict | None = None, payload: bytes | None = None):
    from wyoming.event import Event                             # noqa: PLC0415 — the protocol's own reference implementation
    return Event(type=kind, data=data or {}, payload=payload)


def pcm_floats(pcm: bytes) -> list[float]:
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    return [x / 32768 for x in a]


def looks_technical(text: str) -> bool:
    """Code, file paths, flags, hex, tracebacks: what nobody wants intoned aloud (the summary is for this, not for his personality)."""
    import re                                                   # noqa: PLC0415
    if "`" in text or "```" in text or "->" in text or "::" in text:
        return True
    if re.search(r"[\w-]+\.(?:py|js|ts|yaml|yml|json|sh|md|cpp|h|tf|html|css|log)", text) or re.search(r"0x[0-9a-fA-F]{3,}", text):
        return True
    if re.search(r"[\w.]+[\/][\w.]+[\/][\w.]+", text) or re.search(r"[A-Za-z_][\w.]*\([^)]{0,40}\)", text):
        return True
    return sum(text.count(c) for c in "{}[]<>=;_#|") > max(6, len(text) // 25)


def one_stream(mp3: bytes, run=None, decoder=None) -> bytes:
    """One clean MP3 stream for the pod from MP3 pieces joined end to end (the voice gate joins one per sentence, the hub joins the gate's; the pod's
    decoder stopped at the second piece's header: silent mid-answer, 2026-10-31). The audio router's single pass (speaker_out.prepare): decoded once,
    the PC outputs play that same sound, the pod's copy is encoded from it."""
    import speaker_out                                          # noqa: PLC0415
    return speaker_out.prepare(mp3, run=run, decoder=decoder)


def split_for_voice(text: str, limit: int = 480) -> list[str]:
    """Pieces of at most `limit` characters, cut at sentence ends where there are any, so a long reply can be spoken whole (mp3 pieces join into one clip)."""
    import re                                                   # noqa: PLC0415
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        while len(sentence) > limit:                            # a sentence longer than a piece: cut it at a space
            cut = sentence.rfind(" ", 0, limit) or limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(sentence[:cut])
            sentence = sentence[cut:].strip()
        if len(cur) + len(sentence) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur = (cur + " " + sentence).strip()
    if cur:
        out.append(cur)
    return out or [text[:limit]]


class Room:
    """One room: the audio its box streams in, what Skippy hears and decides, and what goes back through its `link`.

    The link calls begin(remote_wake) when the box starts a turn, feed_pcm() with its audio, stream_ended() when it stops, tap() when someone
    touched it. The room calls the link's woke / voice_started / voice_stopped / heard / released / show / speak / announce."""

    def __init__(self, name, link, gate, brain, timers, spotter=None, local_voice=None, log=print, floor: float = 0.012,
                 min_speech_ms: int = 600, listen_ms: int = 8000, followup_ms: int = 6000, summarize_over: int = 1500, filler=None, filler_after_s: float = 1.0, pc_outs=(), debug: bool = False, mic_gain: float = 1.0, hlog=None, workspace=None, run_action=None, homechat=None, starters=None):
        self.name, self.link, self.gate, self.brain, self.timers, self.spotter = name, link, gate, brain, timers, spotter
        self.local_voice, self.log = local_voice, log
        self.floor, self.min_speech_ms, self.listen_ms, self.followup_ms, self.summarize_over = floor, min_speech_ms, listen_ms, followup_ms, summarize_over
        self.mic_gain = mic_gain                                # SATELLITE_MIC_GAIN: digital gain on the box's audio
        self.debug = debug                                      # SATELLITE_DEBUG=1: the log shows what room talk was heard (private: off by default)
        self.pc_outs = list(pc_outs)                            # speaker_out.PcOut: his voice also out of skippy-pc's HDMI / Bluetooth stereo
        self.filler, self.filler_after_s = filler, filler_after_s       # pre-recorded quips (filler.py) to cover a slow answer
        self.hlog = hlog                                        # housemem.HouseLog: the house log (words after the name, tools, outcomes; never audio)
        self.workspace, self.run_action = workspace, run_action  # proposals.Workspace (what Skippy may ask, the automations a yes made) and house.run_tool bound to the house
        self.homechat = homechat                                # homechat.HomeChat: what is addressed to him and what he says, words only, for the portal's Home chat
        self.pending = None                                     # the proposal just put to him, waiting for yes / no / never
        self.starters = starters                                # starters.Starters (shared by every room): things Skippy brings up himself
        self.pending_starter = None                             # the starter just offered, waiting for his answer
        self._voice_at = self._turn_at = None                   # (monotonic) a voice last heard in the room; his last turn with Skippy
        self._starter_check = 0.0
        self.remote_wake = True                                 # until the box says it woke on its own
        self.music = None                                       # music.BoxMusic for a box that plays Bilby's library
        self.book = None                                        # audiobooks.BookPlayer: the Captain's audiobooks (pod and PC outputs); ducked while Skippy listens and talks
        self.last_media = "music"                               # "music" or "book": what "pause" and "resume" mean when both exist
        self.daily = None                                       # daily.Daily (shared): the morning, coming-home and bedtime routines
        self.stereo = None                                      # stereo.StereoMusic: Bilby's library on the house stereo (the routines' music); ducked like a book
        self.tasks: set = set()
        self.voice = asyncio.Lock()                             # one thing said at a time (a timer waits for an answer to finish)
        self.vad = Vad(floor_min=floor)
        self.pre: deque = deque(maxlen=24)                      # 1.2 s before the voice was noticed: "Hey Skippy" is quieter than what follows, so the bar is crossed late
        self.buf: list | None = None
        self._speech_t0 = self._shown_t = None                  # when the voice began; when he last perked up for something that may not be for him
        self._probed = False
        self.t, self._carry = 0, []                             # t: milliseconds of audio heard on this connection (the room's own clock)
        self.mode, self.deadline = IDLE, 0
        self.recorder = None                                    # wakerec.Recorder: `hub.py --record` sends the room's audio there instead of listening
        link.room = self

    def _fresh(self) -> None:
        """Ready for the next thing said: the room's learned quiet level stays, everything half-heard goes."""
        self.vad.restart()
        self.pre.clear()
        self.buf, self._carry = None, []
        if self.spotter is not None:
            self.spotter.reset()

    # ── what the link tells the room ──
    async def begin(self, remote_wake: bool) -> None:
        """The box started a turn: streaming everything for the hub to hear its name (remote_wake), or it already heard it (a command follows)."""
        self.remote_wake = remote_wake
        if not remote_wake and self.mode == BUSY:               # it heard its name in Skippy's own answer: send it back to sleep
            await self.link.released()
        elif not remote_wake:
            self._fresh()
            self.mode, self.deadline = LISTENING, self.t + self.listen_ms
            self.log(f"[{self.name}] listening (the box woke)")
        elif self.mode != BUSY:
            self._resume(IDLE if self.mode != FOLLOWUP else FOLLOWUP, max(0, self.deadline - self.t))

    def stream_ended(self) -> None:
        if self.buf and self.mode != BUSY:
            self._finish()                                      # it stopped streaming mid-sentence: use what we have

    def tap(self) -> None:
        """A touch on the box's screen: the same as saying "Skippy"."""
        if self.mode == IDLE and self.remote_wake:
            self._woke("tap")

    # ── the audio, 50 ms at a time ──
    def feed_pcm(self, pcm: bytes) -> None:
        self._last_audio = time.monotonic()
        floats = pcm_floats(pcm)
        if self.mic_gain != 1.0:                                # the pod's microphone is quiet for a normal voice across a room: lift it (clipped) before anything listens
            g = self.mic_gain
            floats = [max(-1.0, min(1.0, x * g)) for x in floats]
        self._carry.extend(floats)
        while len(self._carry) >= BLOCK:
            block, self._carry = self._carry[:BLOCK], self._carry[BLOCK:]
            self.t += BLOCK_MS
            self.feed(block)

    def feed(self, block) -> None:
        if self.debug:                                          # SATELLITE_DEBUG=1: a heartbeat of what the room's microphone and the room's state are doing
            self._dbg_n, self._dbg_peak = getattr(self, "_dbg_n", 0) + 1, max(getattr(self, "_dbg_peak", 0.0), rms(block))
            if self._dbg_n % 600 == 0:
                self.log(f"[{self.name}] (debug: 30 s of audio, loudest {self._dbg_peak:.3f}, mode {self.mode}, recorder {self.recorder is not None}, buf {self.buf is not None})")
                self._dbg_peak = 0.0
        if self.recorder is not None:
            self.recorder.feed(block)
            return
        if self.mode == BUSY:
            return                                              # thinking or talking: never answer our own voice
        if any(getattr(o, "talking_until", 0) > time.monotonic() for o in self.pc_outs):
            self.pre.clear()                                    # the PC outputs (the Sony) still say his last words, after the pod: deaf to them too
            return
        self.pre.append(block)
        if self.mode == IDLE and self.spotter is not None and self.remote_wake and self.spotter.feed(block):
            self._woke(getattr(self.spotter, "name", "skippy"))
        event = self.vad.push(rms(block), self.t)
        if self.buf is not None and event != "speech":
            self.buf.append(block)
        if event == "speech":
            self._voice_at = time.monotonic()
            self.buf = list(self.pre)
            self._speech_t0, self._probed = self.t, False
            if self.mode in (LISTENING, FOLLOWUP):
                self._spawn(self.link.voice_started())
        if self.mode == IDLE and self.spotter is None and self.buf is not None and self._speech_t0 is not None and hasattr(self.link, "perk"):     # (with a wake-word model, room talk never goes to Whisper)
            spoken = self.t - self._speech_t0
            if self._shown_t is None and spoken >= 500:       # a voice that lasts: he perks up at once, before it is known whether it was for him
                self._shown_t = self.t
                self._spawn(self.link.perk())
            if not self._probed and spoken >= 1000:           # and about a second in, Whisper is asked whether it began with his name (no waiting for the end of the sentence)
                self._probed = True
                self._spawn(self._probe(list(self.buf)))
        elif event == "end" and self.buf is not None:
            self._finish()
        self._tick()

    async def _probe(self, blocks) -> None:
        try:
            text = await asyncio.to_thread(self.gate.stt, wav_bytes([x for block in blocks for x in block]))
        except GateError:
            return
        woke, _ = wake_split(text)
        if woke and self.mode == IDLE and self.buf is not None:        # still speaking and still waiting: he is addressed
            self._woke("early")

    def _woke(self, by: str) -> None:
        self.log(f"[{self.name}] woke")
        for media in (self.book, self.stereo):
            if media is not None:
                media.duck(True, 25)                            # a book or the stereo playing on the PC outputs goes quiet while he talks to Skippy
        self._record("wake", by=by)
        self.mode, self.deadline = LISTENING, self.t + self.listen_ms
        if self.buf is None:
            self.buf = list(self.pre)[:-1]                      # "Skippy, lights off" in one breath: keep the whole sentence (this block is added below)
        self._spawn(self.link.woke(by))

    def _tick(self) -> None:
        if self._shown_t is not None and self.mode == IDLE and self.buf is None and self.t > self._shown_t + 2500:
            self._shown_t = None                                    # he perked up for nothing (a cough, the TV): back to his resting face
            self._spawn(self.link.idle_face())
        elif self._shown_t is not None and self.mode != IDLE:
            self._shown_t = None
        if self.mode in (LISTENING, FOLLOWUP) and self.t > self.deadline and self.buf is None:
            if self.pending_starter is not None:                # offered, and nobody answered
                self._starter_answer(self.pending_starter, "ignored", "")
                self.pending_starter = None
            self.mode = IDLE
            self._session(False)
            self._spawn(self.link.released())                   # nothing said: the box goes back to waiting for its name

    def _finish(self) -> None:
        utterance, self.buf = self.buf, None
        if len(utterance) * BLOCK_MS - self.vad.end_silence_ms < self.min_speech_ms:
            return                                              # a cough or a click is not worth sending
        if self.mode == IDLE and self.spotter is not None:
            return                                              # room talk, and the spotter did not hear its name: gone, never sent
        was = self.mode
        self.mode = BUSY
        self._spawn(self._utterance(was, [x for block in utterance for x in block]))

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ── what was said ──
    async def _utterance(self, was: str, samples) -> None:
        try:
            if was != IDLE:
                await self.link.voice_stopped()
            if self.debug:                                      # keep the last thing heard, to listen to what was cut off
                try:
                    (Path(__file__).resolve().parents[2] / "state" / "utterances").mkdir(exist_ok=True)
                    (Path(__file__).resolve().parents[2] / "state" / "utterances" / f"{int(time.time())}.wav").write_bytes(wav_bytes(samples))
                    self.log(f"[{self.name}] (debug: kept {len(samples) / 16000:.1f} s of audio)")
                except OSError:
                    pass
            try:
                text = await asyncio.to_thread(self.gate.stt, wav_bytes(samples))
            except GateError as e:
                self.log(f"[{self.name}] (could not hear: {e})")
                text = ""
            woke, rest = wake_split(text)
            if was == IDLE:                                     # no spotter: Whisper is the wake word
                if not woke:
                    self.log(f"[{self.name}] (heard something that was not for me" + (f": {text!r}" if self.debug else "") + ")")
                    self._resume(IDLE)
                    return
                self.log(f"[{self.name}] woke")
                self._record("wake", by="whisper")
                await self.link.woke("skippy")
                if not rest:
                    self._resume(LISTENING, self.listen_ms)
                    return
                command = rest
            else:
                command = rest if woke else text
            if not command:
                self._resume(was, self.listen_ms if was == LISTENING else self.followup_ms)
                return
            await self.handle(command)
        except Exception as e:                                  # noqa: BLE001 — one bad turn must not take the room down
            self.log(f"[{self.name}] (turn failed: {type(e).__name__}: {e})")
            self._resume(IDLE)
            await self.link.released()

    def _emit(self, kind: str, **fields) -> None:
        """A turn event for the portal's Home chat: only ever called for words addressed to him or said by him; a deque append, never waits."""
        if self.homechat is not None:
            self.homechat.emit(self, kind, **fields)

    def _record(self, kind: str, **fields) -> None:
        if self.hlog is not None:
            self.hlog.event(kind, room=self.name, **fields)

    async def _answer_proposal(self, command: str) -> bool:
        """He was just asked about a routine: yes / no / never is stored (and a yes becomes an automation); anything else is an ordinary command."""
        pending, self.pending = self.pending, None
        from proposals import parse_answer                      # noqa: PLC0415
        answer = parse_answer(command)
        if answer is None or self.workspace is None:
            return False
        auto = self.workspace.answer(pending["id"], answer, command)
        self._record("answer", proposal=pending["id"], answer=answer, words=command, automation=auto["id"] if auto else None)
        reply = {"yes": f"Done. Every {pending['weekday']} at {pending['time']}, and I will not even gloat.", "no": "Fine. I will keep my brilliance to myself for a month.",
                 "never": "Understood. I will never ask again."}[answer]
        await self.say(reply, follow_up=True)
        self._resume(FOLLOWUP if self.remote_wake and self.link.hub_follows_up else IDLE, self.followup_ms)
        return True

    async def _maybe_propose(self) -> None:
        """A conversation is open and he is awake: put one confident routine to him, once a day at most (proposals.next_to_ask decides)."""
        if self.workspace is None or self.pending is not None:
            return
        try:
            p = self.workspace.next_to_ask()
            if p is None:
                return
            self.workspace.mark_asked(p["id"])
        except Exception as e:                                  # noqa: BLE001 — a bad proposal file must never stop the house answering
            self.log(f"[{self.name}] (proposals: {type(e).__name__}: {e})")
            return
        self.pending = p
        self._record("proposal", proposal=p["id"], text=p["text"])
        self.log(f"[{self.name}] proposing: {p['id']}")
        await self.say(p["text"], follow_up=True)

    def _starter_answer(self, cand: dict, answer: str, words: str) -> None:
        try:
            self.starters.answer(cand["id"], answer, words)
        except Exception as e:                                  # noqa: BLE001 — a record that can't be written must never stop the house answering
            self.log(f"[{self.name}] (starters: {type(e).__name__}: {e})")
        self._record("starter_answer", starter=cand["id"], answer=answer, words=words or None)

    async def _answer_starter(self, command: str) -> tuple[bool, str]:
        """He was just offered a conversation starter: a no / not now / never (or a stop) ends it, and backs Skippy off; anything else takes it up
        (a bare yes asks for more on it). -> (handled, the command to go on with)."""
        cand, self.pending_starter = self.pending_starter, None
        from proposals import parse_answer                      # noqa: PLC0415
        answer = parse_answer(command)
        if answer is None and (is_stop(command) or is_goodbye(command)):
            answer = "no"
        if answer in ("no", "never"):
            self._starter_answer(cand, answer, command)
            topic = {"world": "news", "": "of that"}.get(cand.get("topic", ""), cand.get("topic", ""))
            reply = ("Fine. I will keep my brilliance to myself for the rest of the day." if answer == "no"
                     else f"Understood. No more {topic} from me for a month.")
            await self.say(reply)                               # (still busy: the room never hears its own voice)
            self._resume(IDLE)
            await self.link.released()
            return True, command
        self._starter_answer(cand, "yes", command)
        if answer == "yes" and len(command.split()) <= 4:
            command = f"{command}. Tell me more about that."
        return False, command

    async def maybe_start(self) -> None:
        """Bring something up unasked (starters.py): only when this room is idle and quiet, someone has been around lately, nothing plays, and the
        house-wide rules (hours, a few a day, backed off after a no) allow it. Checked once a minute from the tick."""
        if (self.pending_starter is not None and self.mode == IDLE
                and time.monotonic() - getattr(self, "_starter_at", 0.0) > STARTER_ANSWER_S):
            self._starter_answer(self.pending_starter, "ignored", "")   # the room went back to rest without an answer (2026-10-31: the box stopped
            self.pending_starter = None                                 # streaming after the announcement and the watchdog ended the follow-up)
        if self.starters is None or self.mode != IDLE or self.pending is not None or self.pending_starter is not None or self.recorder is not None:
            return
        if self.daily is not None and (self.daily.busy or self.daily.arrived_at is not None):
            return                                              # a routine is running (or waiting for him to come in): no starters on top
        now = time.monotonic()
        if now - self._starter_check < 60 or self.voice.locked() or self.buf is not None:
            return
        self._starter_check = now
        if getattr(self.music, "playing", False) or getattr(self.book, "playing", False):
            return                                              # never over music or a book
        if any(getattr(o, "talking_until", 0) > now for o in self.pc_outs):
            return
        heard, turn = self._voice_at, self._turn_at
        if heard is None and turn is None:                      # a restart forgot who was around: the house log remembers his last words to Skippy
            seen = await asyncio.to_thread(self.starters.last_seen)
            if seen is not None:
                turn = now - max(0.0, self.starters.clock() - seen)
        spoke = getattr(self, "_last_said", ("", None))[1]      # Skippy's own last words, a routine's or a timer's included (2026-10-31: a starter 13 s after the morning routine)
        around = (heard is not None and now - heard < PRESENT_S) or (turn is not None and now - turn < PRESENT_S)
        hushed = ((heard is None or now - heard > HUSH_S) and (turn is None or now - turn > AFTER_TURN_S)
                  and (spoke is None or now - spoke > AFTER_TURN_S))
        if not (around and hushed):
            return
        try:
            cand = await asyncio.to_thread(self.starters.next)
        except Exception as e:                                  # noqa: BLE001
            self.log(f"[{self.name}] (starters: {type(e).__name__}: {e})")
            return
        if cand is None or self.mode != IDLE or self.buf is not None:
            return
        self.mode = BUSY
        voice = await self._voice(cand["opener"])               # only in his own voice: with the gate down (2026-10-31) it went out unheard and spent the slot
        if voice is None or voice[0] != "mp3" or self.mode != BUSY:
            self.log(f"[{self.name}] (starter held back: Skippy's voice is not answering)")
            self._resume(IDLE)
            return
        self.starters.mark_offered(cand, self.name)
        from starters import note_of                            # noqa: PLC0415
        if hasattr(self.brain, "offer"):
            self.brain.offer(cand["opener"], note_of(cand))
        news = getattr(self.brain, "news", None)
        if news is not None and cand.get("sources"):
            news.adopt(cand.get("headline") or cand.get("topic", "the news"), cand["sources"])
        self.pending_starter, self._starter_at = cand, time.monotonic()
        self._record("starter", starter=cand["id"], topic=cand.get("topic"), text=cand["opener"])
        self.log(f"[{self.name}] starter ({cand.get('topic')}): {cand['opener'][:120]}")
        try:
            await self.say(cand["opener"], announce=True, voice=voice)
        finally:
            self._resume(FOLLOWUP, self.followup_ms)            # he can answer without the name

    async def typed(self, command: str, speak: bool = True) -> None:
        """Words the Captain typed in the portal's Home chat, treated exactly as if they had been spoken here (the brain answers; the answer is spoken
        on this room's outputs when `speak`). The room is busy meanwhile, so it never hears its own voice, then goes back to what it was doing."""
        was, self.mode = self.mode, BUSY
        try:
            await self.handle(command, typed=True, speak=speak)
        finally:
            self._resume(IDLE if was == BUSY else was, self.followup_ms if was == FOLLOWUP else 0)

    def is_echo(self, command: str) -> bool:
        """Skippy's own last words heard back (2026-10-31: the projector played "News about what? ..." 18 s late, after the room's deaf window,
        and the room took it as his answer): most of what was heard is in what he said in the last 90 s."""
        said, at = getattr(self, "_last_said", ("", 0.0))
        words = re.findall(r"[a-z']+", (command or "").lower())
        if len(words) < 4 or time.monotonic() - at > 90:
            return False
        mine = set(re.findall(r"[a-z']+", said.lower()))
        return sum(w in mine for w in words) >= 0.8 * len(words)

    async def handle(self, command: str, typed: bool = False, speak: bool = True) -> None:
        if not typed and self.is_echo(command):
            self.log(f"[{self.name}] (heard my own voice back: ignored)")
            self._resume(FOLLOWUP if self.remote_wake and self.link.hub_follows_up else IDLE, self.followup_ms)
            return
        if not typed:
            await self.link.heard(command)                      # also tells a self-waking satellite to stop streaming
            self._emit("heard", text=command)                   # addressed to him (his name, a tap, or an open conversation): the Home chat
            if self.pending is not None and await self._answer_proposal(command):
                return
            if self.pending_starter is not None:
                done, command = await self._answer_starter(command)
                if done:
                    return
        if self.daily is not None and await self.daily.claim(self, command):
            self._turn_at = time.monotonic()                    # a routine's words ("goodnight", "skip", "later"): the routine answers, not the brain
            if self.mode == BUSY and not typed:
                self._resume(IDLE)
            return
        self.pending = None
        self._turn_at = time.monotonic()
        if not typed and (is_goodbye(command) or is_stop(command)):
            self._record("command", words=command, tools=[], outcome="sign_off")
            self._resume(IDLE)
            await self._sign_off(command)
            await self.link.released()
            return
        self.log(f"[{self.name}] heard: {command}")
        started, outcome = time.time(), "ok"
        try:
            work = asyncio.ensure_future(asyncio.to_thread(self.brain.reply, command))
            await self._cover_the_wait(work, command)
            reply = await work
        except Exception as e:                                  # noqa: BLE001 — whatever the API did, he gets an answer
            self.log(f"[{self.name}] (brain failed: {type(e).__name__})")
            outcome = "brain_failed"
            reply = "My brain is not answering. Typical."
        self.log(f"[{self.name}] said: {reply[:120]}")
        calls = list(getattr(self.brain, "actions", None) or [])
        if self.hlog is not None:
            self._record("command", words=command, tools=[c["tool"] for c in calls], outcome=outcome, reply=reply, seconds=round(time.time() - started, 1))
            self.hlog.actions(self.name, calls)
        from housemem import ACTIONS                            # noqa: PLC0415
        tools = [ACTIONS[c["tool"]] + (": " + str(c.get("args", {}).get("name") or c.get("args", {}).get("what") or c.get("args", {}).get("label") or "")).rstrip(": ")
                 for c in calls if c.get("tool") in ACTIONS]
        if tools:
            self._emit("actions", tools=tools[:8])
        await self.say(reply, follow_up=True, silent=not speak, typed=typed)
        if typed:
            return
        if outcome == "ok":
            await self._maybe_propose()
        if self.remote_wake and self.link.hub_follows_up:
            self._resume(FOLLOWUP, self.followup_ms)            # like Alexa: a few seconds to answer back without the name
        else:
            self._resume(IDLE)

    async def _sign_off(self, command: str) -> None:
        """A short pre-recorded line when he is thanked or told goodbye (not for a plain stop or cancel)."""
        import re                                               # noqa: PLC0415
        if self.filler is None or not re.search(r"thank|bye|done|later", command, re.I):
            return
        quip = self.filler.pick("goodbye", command)
        if quip is not None:
            text, mp3 = quip
            self.log(f"[{self.name}] (sign-off: {text})")
            async with self.voice:
                await self.link.show(text)
                await self._out(text, ("mp3", mp3), announce=True)

    async def _cover_the_wait(self, work, command: str) -> None:
        """While the answer is worked out, say a pre-recorded quip as soon as it is clearly not going to be instant (never for a quick home command), and one more
        if it is still not ready after the first: Skippy answers at once, and the real answer follows."""
        if self.filler is None or self.filler_after_s <= 0:
            return
        kind = self.filler.kind_for(command)
        wait = self.filler_after_s * (2.2 if kind == "home" else 1.0)     # a quick household command is usually answered at once: give it longer
        done, _ = await asyncio.wait({work}, timeout=wait)
        import filler as filler_mod                             # noqa: PLC0415
        topic = filler_mod.topic_for(command)                   # the second line, when the wait drags: a factoid about what he asked, if there is one in stock
        second = f"fact:{topic}" if topic and getattr(self.filler, "has", lambda k: False)(f"fact:{topic}") else "long"
        for stage in (kind, second):
            if done:
                return
            quip = self.filler.pick(stage, command)
            if quip is not None:
                text, mp3 = quip
                self.log(f"[{self.name}] (filler: {text})")
                async with self.voice:
                    await self.link.show(text)
                    await self._out(text, ("mp3", mp3), announce=True)
            done, _ = await asyncio.wait({work}, timeout=5.0)

    def _resume(self, mode: str, window_ms: int = 0) -> None:
        self._fresh()
        self.mode, self.deadline = mode, self.t + window_ms
        self._session(mode == FOLLOWUP)

    def _session(self, on: bool) -> None:
        """A conversation is open (the name opened it; it closes after a long silence or a goodbye): the box says so on its screen."""
        show = getattr(self.link, "session", None)
        if show is not None:
            show(on)

    async def say(self, text: str, follow_up: bool = False, announce: bool = False, silent: bool = False, typed: bool = False, voice=None) -> None:
        """Skippy's voice in this room: the gate's local TTS (a summary when long), else the machine's own voice. Waits until it has played."""
        said = text
        if looks_technical(text) or len(text) > self.summarize_over:       # only technical or very long output is boiled down: his voice and jokes are spoken whole
            try:
                said = await asyncio.to_thread(self.gate.summary, text)
            except GateError:
                said = text[:self.summarize_over]
        from emotion import emotion_of                           # noqa: PLC0415 — rules over the words, no model call
        self._emit("announce" if announce else "said", text=said, mood=emotion_of(said), typed=True if typed else None, silent=True if silent else None)
        if silent:
            return                                              # "speak aloud" off: the answer only appears in the thread
        self._last_said = (said, time.monotonic())              # an echo of it is not the Captain talking (is_echo)
        async with self.voice:
            voice = voice if voice is not None and said == text else await self._voice(said)    # a clip made already (a starter) is used as it is
            await self.link.show(said)
            for media in (self.book, self.stereo):
                if media is not None:
                    media.duck(True, 90)                        # a book or the stereo (on the PC outputs) ducks under Skippy's voice, then comes back
            try:
                await self._out(said, voice, follow_up=follow_up, announce=announce)
            finally:
                for media in (self.book, self.stereo):
                    if media is not None:
                        media.duck(True, self.followup_ms / 1000 if follow_up else 2.5)

    async def _out(self, said: str, voice, follow_up: bool = False, announce: bool = False) -> None:
        """One clip of Skippy's voice in this room (the voice lock held). With a PC output open that is the room's one voice (the Captain,
        2026-10-31: two speakers never line up exactly enough for an ear), it plays there ALONE: the pod gets the words without the sound,
        draws the lips on the PC output's clock, and stays the mic. No PC output open, or the router dropped the clip: the pod says it."""
        sole = None
        if voice is not None and voice[0] == "mp3":
            sole = next((o for o in self.pc_outs if getattr(o, "sole", False) and o.available()), None)
        if sole is None:
            self._also(voice)
            if announce:
                await self.link.announce(said, voice)
            else:
                await self.link.speak(said, voice, follow_up)
            return
        mouth = getattr(self.link, "mouth", None)
        if mouth is not None:
            await mouth(voice, said, mirror_now=False)          # the shapes, from the very clip; lips() starts them (and the other pods) when it sounds
        playing = asyncio.ensure_future(sole.play(voice[1], on_sound=getattr(self.link, "lips", None)))
        if announce:
            await self.link.announce(said, None)                # the box's turn ends without its own sound
        else:
            await self.link.speak(said, None, follow_up)
        if not await playing:
            self.log(f"[{self.name}] (the {sole.kind} output dropped it: the pod says it)")
            await self.link.announce(said, voice)

    def _also(self, voice) -> None:
        """Start this clip on the PC's own outputs too (each waits its own delay so they line up with the box); never holds anything up."""
        if voice is not None and voice[0] == "mp3":
            for out in self.pc_outs:
                asyncio.ensure_future(out.play(voice[1]))

    async def _tts_piece(self, text: str, tries: int = 3, wait_s: float = 1.5) -> bytes:
        """One piece in Skippy's voice; a refusal while the studio is busy with someone else's line (a routine, a quip refresh) is tried again."""
        for attempt in range(tries):
            try:
                return await asyncio.to_thread(self.gate.tts, text)
            except BudgetSpent:
                raise
            except GateError as e:
                if attempt == tries - 1 or not re.search(r"\((?:429|503)\)", str(e)):
                    raise
                if not await self._quip_while_busy(text):       # the Captain, 2026-10-31: a refusal is a moment for a stocked factoid or quip
                    await asyncio.sleep(wait_s)
        raise GateError("text to speech refused")

    async def _quip_while_busy(self, text: str) -> bool:
        """A recorded line (no studio needed) while the studio is busy: a factoid on the subject if one is stocked, else a 'still working' quip.
        Once per reply. Called with the room's voice lock held (inside say), so it plays straight to the link. True when one played."""
        if self.filler is None or getattr(self, "_quipped_for", None) == text[:80]:
            return False
        self._quipped_for = text[:80]
        import filler as filler_mod                             # noqa: PLC0415
        topic = filler_mod.topic_for(text)
        kind = f"fact:{topic}" if topic and getattr(self.filler, "has", lambda k: False)(f"fact:{topic}") else "long"
        quip = self.filler.pick(kind, text)
        if quip is None:
            return False
        said, mp3 = quip
        self.log(f"[{self.name}] (voice busy, filler: {said})")
        await self.link.show(said)
        await self._out(said, ("mp3", mp3), announce=True)
        return True

    async def _voice(self, said: str):
        """("mp3", bytes) from Skippy's own voice, ("pcm", bytes, rate) from the machine's, or None when neither answers."""
        try:
            parts = split_for_voice(said)                       # the gate speaks at most 500 characters at a time: a longer reply is spoken in pieces, joined into one clip
            clips = [await self._tts_piece(t) for t in parts]  # one after another: the voice studio speaks one line at a time and refuses a second (2026-10-31)
            return ("mp3", await asyncio.to_thread(one_stream, b"".join(clips)))
        except BudgetSpent:
            self.log(f"[{self.name}] (Skippy's voice has used today's budget: using the machine's own voice)")
        except (GateError, RuntimeError, OSError) as e:
            self.log(f"[{self.name}] (voice unavailable: {e}: using the machine's own voice)")
        if self.local_voice is not None:
            try:
                pcm, rate = await asyncio.to_thread(self.local_voice, said)
                return ("pcm", pcm, rate)
            except (RuntimeError, OSError) as e:
                self.log(f"[{self.name}] (no voice at all: {e})")
        return None

    async def announce(self, text: str, typed: bool = False) -> None:
        """Say something in this room unprompted (a timer, a message from elsewhere, or words typed in the portal's Home chat)."""
        was, self.mode = self.mode, BUSY
        try:
            await self.say(text, announce=True, typed=typed)
        finally:
            self._resume(IDLE if was == BUSY else was, self.followup_ms if was == FOLLOWUP else 0)

    async def timers_due(self) -> None:
        # a watchdog: the room's clock runs on the audio it hears, so if the box's audio stops while he is waiting for a command, nothing would ever time out
        last = getattr(self, "_last_audio", None)
        if last is not None and self.mode in (LISTENING, FOLLOWUP) and time.monotonic() - last > 8:
            self.log(f"[{self.name}] (no audio from the box for 8 s while listening: back to rest)")
            self._resume(IDLE)
            self._shown_t = None
            await self.link.released()
        for timer in self.timers.due():
            self.log(f"[{self.name}] timer done: {timer.label}")
            await self.announce(f"Your {timer.label} timer is done.")
        if self.workspace is not None and self.run_action is not None:
            for auto in self.workspace.due():                   # automations only exist after his recorded yes; each fires once a day, quietly
                result = await asyncio.to_thread(self.run_action, auto["action"]["tool"], auto["action"]["args"])
                self.log(f"[{self.name}] automation {auto['id']}: {result}")
                self._record("automation", automation=auto["id"], tool=auto["action"]["tool"], target=auto["action"]["args"].get("name"),
                             act=auto["action"]["args"].get("action"), outcome=str(result)[:120])
        if self.daily is not None:
            try:
                await self.daily.tick(self)
            except Exception as e:                              # noqa: BLE001 — a routine's trouble never stops the room's clock
                self.log(f"[{self.name}] (routines: {type(e).__name__}: {e})")
        await self.maybe_start()


class WyomingLink:
    """A Room's way to a Wyoming satellite: events over one TCP connection (the `wyoming` package's reader and writer)."""

    hub_follows_up = True                                       # a streaming satellite keeps streaming, so the hub can keep listening

    def __init__(self, send, decode, sleep=asyncio.sleep, log=print):
        self.send, self.decode, self.sleep, self.log = send, decode, sleep, log
        self.room: Room | None = None
        self.converter = None

    async def on_event(self, event) -> None:
        kind, data, room = event.type, event.data or {}, self.room
        if kind == "run-pipeline":
            await room.begin(remote_wake=data.get("start_stage") == "wake")
        elif kind == "audio-start":
            self._converter(data)
        elif kind == "audio-chunk" and event.payload:
            if self.converter is None:
                self._converter(data)
            room.feed_pcm(self._convert(event))
        elif kind == "audio-stop" and not room.remote_wake:
            room.stream_ended()
        elif kind == "ping":
            await self.send(ev("pong", {"text": data.get("text")}))
        elif kind == "info":
            sat = data.get("satellite") or {}
            self.log(f"[{room.name}] connected: {sat.get('name') or 'a Wyoming satellite'}")

    def _converter(self, data: dict) -> None:
        rate, width, channels = int(data.get("rate") or RATE), int(data.get("width") or WIDTH), int(data.get("channels") or CHANNELS)
        if (rate, width, channels) == (RATE, WIDTH, CHANNELS):
            self.converter = False                              # already what we want
        else:
            from wyoming.audio import AudioChunkConverter      # noqa: PLC0415
            self.converter = AudioChunkConverter(RATE, WIDTH, CHANNELS)

    def _convert(self, event) -> bytes:
        if not self.converter:
            return event.payload
        from wyoming.audio import AudioChunk                    # noqa: PLC0415
        return self.converter.convert(AudioChunk.from_event(event)).audio

    # ── what the room tells the satellite ──
    async def woke(self, by: str) -> None:
        await self.send(ev("detection", {"name": by, "timestamp": self.room.t}))

    async def voice_started(self) -> None:
        await self.send(ev("voice-started", {"timestamp": self.room.t}))

    async def voice_stopped(self) -> None:
        await self.send(ev("voice-stopped", {"timestamp": self.room.t}))

    async def heard(self, text: str) -> None:
        await self.send(ev("transcript", {"text": text}))

    async def released(self) -> None:
        if not self.room.remote_wake:                           # an empty transcript sends a self-waking satellite back to its own wake word
            await self.send(ev("transcript", {"text": ""}))

    async def show(self, text: str) -> None:
        pass                                                    # a Wyoming satellite has no screen to write on

    async def speak(self, said: str, voice, follow_up: bool = False) -> None:
        await self.send(ev("synthesize", {"text": said}))
        if voice is None:
            return
        if voice[0] == "mp3":
            try:
                pcm, rate = await asyncio.to_thread(self.decode, voice[1])
            except (RuntimeError, OSError) as e:
                self.log(f"[{self.room.name}] (could not decode the voice: {e})")
                return
        else:
            _, pcm, rate = voice
        fmt = {"rate": rate, "width": 2, "channels": 1}
        await self.send(ev("audio-start", {**fmt, "timestamp": 0}))
        step = CHUNK * 2
        for i in range(0, len(pcm), step):
            await self.send(ev("audio-chunk", {**fmt, "timestamp": i * 1000 // (2 * rate)}, pcm[i:i + step]))
        await self.send(ev("audio-stop", {"timestamp": len(pcm) * 1000 // (2 * rate)}))
        await self.sleep(len(pcm) / (2 * rate) + 0.4)          # the satellite plays it now; its microphone hears it, so we wait it out

    announce = speak


class Hub:
    """Keeps a connection to every room's box, reconnecting when one drops (a box unplugged must not need the hub restarted)."""

    def __init__(self, rooms, make_room, log=print, connect=asyncio.open_connection, retry_s=(2, 5, 15, 30), decode=None,
                 sleep=asyncio.sleep, esphome=None):
        self.rooms_spec, self.make_room, self.log, self.connect, self.retry_s = rooms, make_room, log, connect, retry_s
        self.decode, self.sleep = decode, sleep
        self.esphome = esphome                                  # esphome_link.serve, bound to the media server (build_rooms)
        self.rooms: dict[str, Room] = {}

    async def run(self) -> None:
        await asyncio.gather(*(self.keep(*spec) for spec in self.rooms_spec))

    async def keep(self, name: str, kind: str, host: str, port: int, forever: bool = True) -> None:
        failures = 0
        while True:
            try:
                if kind == "esphome":
                    if self.esphome is None:
                        raise RuntimeError("ESPHome boxes need aioesphomeapi (requirements.txt)")
                    await self.esphome(self, name, host, port)
                else:
                    await self.serve(name, host, port)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:                              # noqa: BLE001 — one room's trouble never takes the other rooms down
                if failures == 0:
                    self.log(f"[{name}] not reachable at {host}:{port} ({type(e).__name__}: {e}); retrying")
                failures += 1
            finally:
                self.rooms.pop(name, None)
            if not forever:
                return
            await asyncio.sleep(self.retry_s[min(failures, len(self.retry_s) - 1)])

    async def serve(self, name: str, host: str, port: int) -> None:
        from wyoming.event import async_read_event, async_write_event  # noqa: PLC0415
        reader, writer = await self.connect(host, port)
        lock = asyncio.Lock()

        async def send(event) -> None:
            async with lock:
                await async_write_event(event, writer)

        link = WyomingLink(send, self.decode, sleep=self.sleep, log=self.log)
        room = self.rooms[name] = self.make_room(name, link)
        ticker = asyncio.ensure_future(self.tick(room))
        try:
            await send(ev("describe"))
            await send(ev("run-satellite"))
            while True:
                event = await async_read_event(reader)
                if event is None:
                    self.log(f"[{name}] disconnected")
                    return
                await link.on_event(event)
        finally:
            ticker.cancel()
            for task in list(room.tasks):
                task.cancel()
            writer.close()

    @staticmethod
    async def tick(room: Room) -> None:
        while True:
            await asyncio.sleep(0.5)
            await room.timers_due()

    async def say(self, name: str, text: str) -> None:
        room = self.rooms.get(name)
        if room is None:
            raise KeyError(f"no room called {name!r} is connected")
        await room.announce(text)


# ── the program ──
def build_rooms(env: dict):
    """A Room factory sharing the gate, the house and the spotter's model file; each room gets its own brain memory and timers, and a box
    that plays music gets the music tools."""
    import audio                                                # noqa: PLC0415
    import audiobooks                                           # noqa: PLC0415
    import book_voice                                           # noqa: PLC0415
    import ear                                                  # noqa: PLC0415
    from brain import Brain                                     # noqa: PLC0415
    from timers import Timers                                   # noqa: PLC0415
    from wakeword import Spotter, default_model                 # noqa: PLC0415
    from voicegate import Gate                                  # noqa: PLC0415
    token = env.get("SKIPPY_HOME_TOKEN", "")
    if not token:
        sys.exit("hub: SKIPPY_HOME_TOKEN must be in the vault .env (or the environment)")
    gate = Gate(env.get("SATELLITE_GATE", "http://127.0.0.1:3007"), token)
    house = ear.build_house(env)                                # the same house as the single-room listener: one set of lights
    client, brain_model = ear.brain_client(env, "hub")           # Skippy Core's chat model: local, never a paid one
    model_path = default_model(env)
    from filler import FillerBank                                # noqa: PLC0415
    import speaker_out                                           # noqa: PLC0415
    filler_bank = FillerBank.load(Path(__file__).resolve().parents[2] / "state" / "filler")      # recorded by tools/make_filler_bank.py; none = no quips
    threshold = float(env.get("SATELLITE_WAKE_THRESHOLD", "0.5"))
    if model_path.is_file():
        print(f"wake word: {model_path.name} (threshold {threshold})", flush=True)
    else:
        print(f"wake word: no model at {model_path}; using Whisper for the wake word (see wakeword/README.md)", flush=True)
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)   # noqa: E731
    import housemem                                              # noqa: PLC0415
    import house as house_mod                                    # noqa: PLC0415
    from proposals import Workspace                              # noqa: PLC0415
    hlog = housemem.HouseLog(housemem.home_dir(env))              # skippy-home/log: local, append-only, words and outcomes, never audio
    import news as news_mod                                      # noqa: PLC0415
    from starters import Starters                                # noqa: PLC0415
    starters = None if env.get("SATELLITE_STARTERS", "on").lower() in ("off", "0", "no") else \
        Starters(Path(__file__).resolve().parents[2] / "state" / "starters", log_root=hlog.root)   # filled daily by tools/starter_refresh.py
    workspace = Workspace(hlog.root)
    run_action = (lambda tool, args: house_mod.run_tool(house, tool, args)) if house is not None else None
    loop = asyncio.get_running_loop()                           # build_rooms runs on the hub's loop (_main)
    import daily as daily_mod                                    # noqa: PLC0415
    from brain import load_persona                               # noqa: PLC0415
    stereo_holder: dict = {}

    def stereo():
        """The house stereo (SKIPPY_STEREO_OUT, default the HDMI to the Sony), made once the first box with a media server is connected."""
        if "music" not in stereo_holder and stereo_holder.get("media") is not None:
            import router as router_mod                          # noqa: PLC0415
            from stereo import StereoMusic                       # noqa: PLC0415
            kinds = [k for k in env.get("SKIPPY_STEREO_OUT", "hdmi").split("+") if k]
            import spotify as spotify_mod                        # noqa: PLC0415 — his Spotify on the same stereo (services/spotify), unless SKIPPY_SPOTIFY=off
            spot = None if env.get("SKIPPY_SPOTIFY", "on").lower() in ("off", "0", "no") else spotify_mod.Client.from_env(env)
            music = stereo_holder["music"] = StereoMusic(router_mod.shared(env, log), kinds, stereo_holder["media"], log=log, spotify=spot, env=env)
            music.start()
            connect = getattr(make_room, "connect", None)        # the stereo is a Bilby Connect player too: "Play on", its state, "favorite this"
            if connect is not None:
                connect.add("stereo", env.get("SKIPPY_STEREO_NAME", "House stereo"), music.box)
        return stereo_holder.get("music")
    routines = None if env.get("SKIPPY_ROUTINES", "on").lower() in ("off", "0", "no") else         daily_mod.Daily(env, house=house, client=client, model=brain_model, filler=filler_bank, stereo=stereo, hlog=hlog, log=log, persona=load_persona())
    if routines is not None:
        import google_home                                       # noqa: PLC0415 — his calendar, mail and contacts, read only, once he has signed in
        routines.extras.update(google_home.extras(google_home.Google(env, log=log), routines.aword, log=log))
    from homechat import HomeChat                                # noqa: PLC0415
    import music as music_mod                                    # noqa: PLC0415
    homechat = HomeChat(env.get("PORTAL_URL") or music_mod.PORTAL, token, enabled=env.get("SATELLITE_HOME_CHAT", "on").lower() not in ("off", "0", "no"), log=log)

    def make_room(name, link):
        timers = Timers()
        spotter = Spotter.load(model_path, threshold=threshold) if model_path.is_file() else None
        holder: dict = {}
        plays_music = "player" in getattr(link, "keys", {})      # an ESPHome box with a media player (esphome_link.discover ran first)
        tools = music_tools(lambda: holder["room"], loop, favorites=music_mod.Favorites(env.get("PORTAL_URL") or music_mod.PORTAL, token),
                            stereo=stereo) if plays_music else None
        book = audiobooks.build_player(env, name, link, log)     # None when SATELLITE_AUDIOBOOKS=off
        if book is not None:
            tools = audiobooks.combine(tools, audiobooks.book_tools(lambda: holder["room"], loop))
        brain = Brain(client, brain_model, timers, hass=house, extra=tools, news=news_mod.News(env, log=log))
        if book is not None:
            brain.pre_route = lambda heard: book_voice.pre_route(heard, holder["room"])
        room = holder["room"] = Room(name, link, gate, brain, timers, spotter=spotter, local_voice=audio.local_voice_pcm16, log=log,
                                     floor=float(env.get("SATELLITE_FLOOR", "0.012")), filler=filler_bank, debug=env.get("SATELLITE_DEBUG") == "1", mic_gain=float(env.get("SATELLITE_MIC_GAIN", "1.0")), pc_outs=speaker_out.parse_outputs(env, name, log)[1],
                                     filler_after_s=float(env.get("SATELLITE_FILLER_S", "1.0")), hlog=hlog, workspace=workspace, run_action=run_action, homechat=homechat, starters=starters,
                                     followup_ms=int(float(env.get("SATELLITE_SESSION_S", "30")) * 1000))      # the name opens a conversation: it stays open this long without a word
        room.book = book
        if getattr(link, "media", None) is not None:
            stereo_holder.setdefault("media", link.media)
        room.daily = routines
        room.stereo = stereo() if routines is not None else None
        return room
    make_room.homechat = homechat                               # _main runs its sync loop (never under --record)
    return make_room


MUSIC_TOOLS = [
    {"name": "music", "description": "Control the music playing in THIS room (Bilby's Audio Service on this room's speaker), or on the house stereo when "
                                     "that is what plays (Spotify included).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["pause", "resume", "next", "previous", "louder", "quieter", "favorite"],
                      "description": "favorite: add the song playing now to his favorites (for DJing later): 'add this song to my favorites', 'favorite this'"}},
                      "required": ["action"]}},
    {"name": "play_music", "description": "Play music from the Captain's own library in THIS room: an artist, an album or a song, in his words; "
                                          "'favorites' for his favorites list; 'everything' for the whole library. shuffle=true for 'shuffle ...'. "
                                          "queue=true for 'after this', 'queue ...', 'add ... to the queue' (it plays after what plays now, Spotify included).",
     "input_schema": {"type": "object", "properties": {"what": {"type": "string", "description": "e.g. 'Pink Floyd', 'Comfortably Numb', 'favorites', 'everything'"},
                                                       "shuffle": {"type": "boolean"}, "queue": {"type": "boolean"},
                                                       "speaker": {"type": "string", "enum": ["here", "stereo"],
                                                                   "description": "stereo: on the house stereo (the Sony): 'on the stereo', 'downstairs', 'DJ ...'"}},
                      "required": ["what"]}},
    {"name": "dj_mode", "description": "Bilby's DJ mode for his library on the house stereo: 'DJ mode on' / 'DJ mode off'. On: each track mixes into the "
                                       "next on the beat (tempo matched, on a phrase) and a shuffle is ordered like a DJ set; off: one ends, the next starts.",
     "input_schema": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}},
    {"name": "spotify", "description": "The Captain's Spotify (Premium), played on the house stereo (the Sony), not this room's pod. Only when he says Spotify. "
                                       "play: an artist, a song, an album, one of his playlists ('my road trip playlist') or 'liked songs'; shuffle=true for "
                                       "'shuffle ...'; queue=true for 'after this' / 'queue ...'. add: add the song playing now to one of his Spotify playlists "
                                       "('add this song to my Spotify playlist Gym'). like: save the song playing now to his Spotify liked songs.",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["play", "add", "like"]},
                                                       "what": {"type": "string", "description": "play: e.g. 'Daft Punk', 'my workout playlist', 'liked songs'"},
                                                       "playlist": {"type": "string", "description": "add: the playlist's name, in his words"},
                                                       "shuffle": {"type": "boolean"}, "queue": {"type": "boolean"}},
                      "required": ["action"]}},
]


def music_tools(room_of, loop, favorites=None, stereo=None):
    """(tools, run) for Brain(extra=...): the music in the room the brain belongs to, and the house stereo (`stereo()`: stereo.StereoMusic or None),
    where his Spotify plays. The brain runs in a worker thread; the music lives on the hub's event loop, so each call is handed over to it. A room
    without a speaker for music says so."""
    def run(name: str, args: dict) -> str:
        import music as music_mod                               # noqa: PLC0415
        if name == "dj_mode":                                   # house-wide, kept in state/dj.json (the vault's BILBY_DJ is the default)
            import dj                                           # noqa: PLC0415
            on = bool((args or {}).get("on"))
            dj.set_enabled(on)
            return ("Done: DJ mode is on. His tracks on the house stereo now mix into each other on the beat, from the next change of track."
                    if on else "Done: DJ mode is off. On the house stereo one track now ends and the next starts.")
        room = room_of()
        box = getattr(room, "music", None)
        if box is None:
            return "This room has no speaker for music."

        async def go() -> str:
            book = getattr(room, "book", None)
            house = stereo() if stereo is not None else None
            on_stereo = house is not None and house.playing and not box.playing   # the stereo is what plays: "next", "pause", "favorite this" mean it
            if name == "music":
                action = str((args or {}).get("action") or "")
                if book is not None and book.book is not None and (book.playing or (room.last_media == "book" and not box.playing and not on_stereo)):
                    if action == "pause":                       # "stop" / "pause" while a book plays means the book
                        await book.pause()
                        return "Done: paused the audiobook."
                    if action == "resume":
                        await book.resume()
                        return "Done: the audiobook is playing again."
                target = house.box if on_stereo or (action == "resume" and room.last_media == "stereo" and house is not None) else box
                step = {"pause": target.pause, "resume": target.resume, "next": target.next, "previous": target.prev}.get(action)
                if step is not None:
                    await step()
                    return f"Done: {action}" + (" on the house stereo." if target is not box else ".")
                if action == "favorite":                        # "Skippy, add this song to my favorites" (portal/favorites.py)
                    if favorites is None:
                        return "His favorites list is not set up here."
                    return await asyncio.to_thread(favorites.mark, room.name, music_mod.device_id("stereo") if on_stereo else "")
                if action in ("louder", "quieter"):              # the pods' volume is locked at 70% (the Captain, 2026-10-31: louder, they crackle)
                    return ("Not done: the pods' volume is locked at one level on purpose (louder, they crackle). "
                            "The house stereos have their own volume knobs; tell him to use those.")
                return "I cannot do that to the music."
            if name == "spotify":
                return await spotify_go(room, box, book, house, args or {})
            what = str((args or {}).get("what") or "")
            mixed = bool((args or {}).get("shuffle"))
            plain = " ".join(re.findall(r"[a-z]+", what.lower()))
            if re.fullmatch(r"(?:my |his )?(?:favou?rites?|favou?rite songs?|liked songs)", plain):
                found = await asyncio.to_thread(favorites.tracks) if favorites is not None else []
                if not found:
                    return "His favorites list is empty so far."
            elif re.fullmatch(r"(?:everything|all|all (?:of )?(?:my |his )?music|(?:my |his |the )?(?:whole |entire )?(?:library|collection))", plain):
                found, mixed = list(await box.media.catalog()), True
            else:
                found = music_mod.search(await box.media.catalog(), what)
            if not found:
                return f"Nothing in his library matches {what!r}."
            if mixed:
                import random                                   # noqa: PLC0415
                found = random.sample(found, len(found))[:music_mod.MAX_QUEUE]
            if (args or {}).get("queue") and (on_stereo or box.playing):
                target = house.box if on_stereo else box        # one queue: Bilby's tracks after the Spotify ones on the stereo, or the pod's own
                await target.append(found)
                return f"Queued {len(found)} track{'s' if len(found) != 1 else ''} after this" + (" on the house stereo." if on_stereo else ".")
            if (args or {}).get("speaker") == "stereo" and house is not None:
                if book is not None:
                    await book.pause()
                if box.playing:
                    await box.pause()                           # the pod and the stereo never play at once
                room.last_media = "stereo"
                n = await house.play_tracks(found, mixed)
                title, artist = house.box.title()
                import dj                                       # noqa: PLC0415
                return (f"Playing {n} track{'s' if n != 1 else ''} on the house stereo" + (" in DJ mode" if dj.enabled(house.env) else "")
                        + f", starting with {title}" + (f" by {artist}." if artist else "."))
            if book is not None:
                await book.pause()                              # music and a book never play at once
            if house is not None and house.playing:
                await house.stop()                              # nor the pod and the stereo
            room.last_media = "music"
            await box.play_queue(found)
            title, artist = box.title()
            return f"Playing {len(found)} track{'s' if len(found) != 1 else ''}, starting with {title}" + (f" by {artist}." if artist else ".")
        try:
            return asyncio.run_coroutine_threadsafe(go(), loop).result(40)
        except Exception as e:                                  # noqa: BLE001 — said aloud, not raised
            return f"The music did not answer ({type(e).__name__})."
    return MUSIC_TOOLS, run


async def spotify_go(room, box, book, house, args: dict) -> str:
    """The `spotify` tool: his Spotify on the house stereo (spotify.py, stereo.py)."""
    import spotify as spotify_mod                               # noqa: PLC0415
    client = getattr(getattr(house, "player", None), "spotify", None)
    if house is None or client is None:
        return "Spotify is not set up on the house stereo here."
    action = str(args.get("action") or "play")
    try:
        if action == "play":
            what = str(args.get("what") or "").strip()
            if not what:
                return "Say what to play on Spotify."
            tracks, how = await asyncio.to_thread(spotify_mod.queue_for, client, what)
            if not tracks:
                return f"Spotify found nothing for {what!r}."
            if args.get("shuffle"):
                import random                                   # noqa: PLC0415
                tracks = random.sample(tracks, len(tracks))
            if args.get("queue") and (house.playing or box.playing):
                if house.playing:
                    n = await house.play_spotify(tracks, queue=True)
                    return f"Queued {n} Spotify track{'s' if n != 1 else ''} from {how} after this on the house stereo."
            if book is not None:
                await book.pause()
            if box.playing:
                await box.pause()                               # one thing at a time: the pod's own music stops for the stereo
            room.last_media = "stereo"
            n = await house.play_spotify(tracks)
            title, artist = house.box.title()
            return f"Playing {n} Spotify track{'s' if n != 1 else ''} from {how} on the house stereo, starting with {title}" + (f" by {artist}." if artist else ".")
        uri, label = await asyncio.to_thread(_spotify_now, client, house, box)
        if not uri:
            return label
        if action == "add":
            playlist = str(args.get("playlist") or "").strip()
            if not playlist:
                return "Which of his Spotify playlists?"
            got = await asyncio.to_thread(client.add_to_playlist, playlist, [uri])
            return f"Added {label} to his Spotify playlist {got.get('name') or playlist}."
        if action == "like":
            await asyncio.to_thread(client.save, [uri])
            return f"Saved {label} to his Spotify liked songs."
        return "I cannot do that with Spotify."
    except spotify_mod.SpotifyError as e:
        return f"Spotify said no: {e}"


def _spotify_now(client, house, box) -> tuple[str, str]:
    """(the Spotify URI of the song playing now, how to name it), or ("", why not). A Bilby song is looked up on Spotify by its title and artist."""
    import spotify as spotify_mod                               # noqa: PLC0415
    for player in (house.box, box):
        if player is not None and player.playing and player.track():
            title, artist = player.title()
            if spotify_mod.is_uri(player.track()):
                return player.track(), f"{title}" + (f" by {artist}" if artist else "")
            hits = client.search(f"track:{title} artist:{artist}" if artist else title, "track", 3)
            if not hits:
                return "", f"Spotify has no {title}" + (f" by {artist}." if artist else ".")
            h = hits[0]
            return h["uri"], f"{h.get('title')} by {h.get('artist')} (Spotify's copy)"
    return "", "Nothing is playing, so there is no song to add."




def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--say", nargs=2, metavar=("ROOM", "TEXT"), help="say this in one room, then quit")
    ap.add_argument("--record", nargs="+", metavar=("KIND", "COUNT"), help="record wake-word clips through the pod's microphone: positive|negative [COUNT [ROOM]] (stop the running hub first; wakerec.py)")
    args = ap.parse_args(argv)
    from ear import load_env                                    # noqa: PLC0415
    env = load_env()
    try:
        rooms = parse_rooms(env.get("SATELLITE_ROOMS", ""))
    except ValueError as e:
        sys.exit(f"hub: SATELLITE_ROOMS: {e}")
    if not rooms:
        sys.exit('hub: no rooms: set SATELLITE_ROOMS, e.g. "kitchen=esphome://skippy-kitchen.local" (see README.md)')
    return asyncio.run(_main(env, rooms, args))


async def _main(env: dict, rooms, args) -> int:
    import audio                                                # noqa: PLC0415
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)   # noqa: E731
    esphome = media = connect = None
    if any(kind == "esphome" for _, kind, _, _ in rooms):
        import esphome_link                                     # noqa: PLC0415
        import music                                            # noqa: PLC0415
        from media import MediaServer                           # noqa: PLC0415
        media = MediaServer(port=int(env.get("SATELLITE_MEDIA_PORT", "10800")), host=env.get("SATELLITE_MEDIA_HOST", ""),
                            gate=env.get("SATELLITE_MUSIC_GATE", "http://127.0.0.1:3005"), home_token=env.get("SKIPPY_HOME_TOKEN", ""))
        media.log = log
        await media.start()
        connect = music.Connect(env.get("PORTAL_URL", music.PORTAL), env.get("SKIPPY_HOME_TOKEN", ""), log=log)
        labels = dict(part.split("=", 1) for part in env.get("SATELLITE_ROOM_NAMES", "").split(",") if "=" in part)
        esphome = lambda hub, name, host, port: esphome_link.serve(hub, name, host, port, media=media, connect=connect,   # noqa: E731
                                                                   noise_psk=env.get("SATELLITE_ESPHOME_KEY", ""), label=labels.get(name, ""))
    hub = Hub(rooms, build_rooms(env), log=log, decode=audio.mp3_to_pcm16, esphome=esphome)
    hub.make_room.connect = connect                             # the house stereo checks in as a Connect player too (build_rooms' stereo())
    try:
        if args.say:
            return await _say_once(hub, rooms, *args.say)
        if args.record:
            return await _record(hub, rooms, env, *args.record)
        log("hub: rooms " + ", ".join(f"{n} ({k} {h}:{p})" for n, k, h, p in rooms))
        homechat = getattr(hub.make_room, "homechat", None)
        await asyncio.gather(hub.run(), *([connect.run()] if connect else []), *([homechat.run(hub)] if homechat is not None else []))
        return 0
    finally:
        if media is not None:
            await media.stop()


async def _record(hub: Hub, rooms, env: dict, kind: str, count: str = "30", name: str = "") -> int:
    """hub.py --record: connect to one room as usual, then take its audio for wakerec.Recorder (the pod's own microphone chain, which is what the wake word hears)."""
    from wakerec import Recorder                                # noqa: PLC0415
    spec = next((r for r in rooms if not name or r[0] == name), None)
    if kind not in ("positive", "negative") or spec is None:
        print(f"hub: --record positive|negative [COUNT [ROOM]]; room {name or '(first)'!r} {'unknown' if spec is None else 'ok'}")
        return 1
    serving = asyncio.ensure_future(hub.keep(*spec, forever=False))
    for _ in range(100):
        if spec[0] in hub.rooms:
            break
        await asyncio.sleep(0.1)
    room = hub.rooms.get(spec[0])
    if room is None:
        print(f"hub: could not reach {spec[0]}")
        serving.cancel()
        return 1
    await asyncio.sleep(2)                                      # the box starts its first listening turn
    root = Path(__file__).resolve().parents[2] / "state" / "wakeword" / "real"
    rec = room.recorder = Recorder(kind, int(count), root, lambda text: room.say(text, announce=True), log=room.log, floor=float(env.get("SATELLITE_FLOOR", "0.012")))
    done = await rec.run()
    room.recorder = None
    serving.cancel()
    return 0 if done else 1


async def _say_once(hub: Hub, rooms, name: str, text: str) -> int:
    spec = next((r for r in rooms if r[0] == name), None)
    if spec is None:
        print(f"hub: no room called {name!r}")
        return 1
    serving = asyncio.ensure_future(hub.keep(*spec, forever=False))
    for _ in range(50):
        if name in hub.rooms:
            break
        await asyncio.sleep(0.1)
    if name not in hub.rooms:
        print(f"hub: could not reach {name}")
        serving.cancel()
        return 1
    await hub.say(name, text)
    serving.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
