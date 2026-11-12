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
import contextlib
import json
import re
import asyncio
import sys
import time
from array import array
from collections import deque
from pathlib import Path

from audiobooks import BOOK_TOOLS
from vad import Vad, loudest, rms
from voicegate import BudgetSpent, GateError, wav_bytes
from wake import is_decline, is_goodbye, is_stop, wake_split

PORT = 10700                                                    # Wyoming's
ESPHOME_PORT = 6053                                             # ESPHome's native API
RATE, WIDTH, CHANNELS = 16000, 2, 1                             # what the hub hears in (boxes are asked for this; others are converted)
BLOCK = 800                                                     # 50 ms: what the Vad's numbers were tuned on (the same as ear.py)
BLOCK_MS = 50
MAX_UTTERANCE_BLOCKS = 1800                                     # 90 s: nothing he says to Skippy is longer, and a buffer that only grows is a leak
CHUNK = 2048                                                    # samples per audio-chunk sent back over Wyoming
IDLE, LISTENING, FOLLOWUP, BUSY = "idle", "listening", "followup", "busy"
REST_SILENCE_S = 20                                             # a box streams all the time it rests (the hub hears its name): this long with no audio, it is asked to start again
PRESENT_S = 20 * 60                                             # a voice heard in the room this recently: someone is around (for a conversation starter)
HUSH_S = 45                                                     # ... but not in the last this many seconds: never talk over people
AFTER_TURN_S = 180                                              # nor this soon after he last spoke to Skippy
STARTER_ANSWER_S = 180                                          # an offered starter still takes "Skippy, tell me more" this long
STOP_AUDIO_WAIT_S = 3.0                                         # a plain "stop" waits this long for the radio's pause and then signs off anyway (the radio's own call allows 40 s, and the room is deaf all that time)
# What Whisper writes for noise it cannot read (2026-11-04 08:12: "Thanks for watching!" was taken as a command): never a thing said to the house. A sentence that
# merely holds the words is another matter (the review of 2026-11-05): "Skippy, turn off the lights. Thanks for watching!" keeps its command, and a real sentence ("thank you
# for watching the kids", "please subscribe me to the weather alerts", "put the subtitles by default on the TV") reaches the house. The longer outros are noise too (round 2:
# "Thanks for watching, see you next time." and "Please subscribe to my channel and like the video." came through as commands, which the first filter had dropped): a sentence
# that STARTS with the phrase is noise unless the words right after it make it about something (what is watched, who is subscribed to what), and a stock goodbye in the
# sentence right behind a dropped outro is that video's own.
_FOR_WATCHING = r"(?:and )?(?:thanks?|thank you)(?: (?:so|very) much)? for watching(?:[ ,]+(?:everyone|everybody|guys|folks|friends|y'all|you all))?"
# what right after "for watching" makes it about something real ("... the kids", "... over the house", "... it for me"); "... my (new) video", "... this channel" do not
_WATCHED = (r"(?:(?:the|a|an|some|another|my|our|your|his|her|their|this|that|these|those)\s+(?!(?:\S+\s+){0,2}?(?:video|channel|episode|stream|vlog|tutorial|clip|content|lesson|lecture|presentation|podcast)s?\b)"
            r"|(?:it|him|them|me|us|over|out|after)\b)")
# ... and right after "please subscribe" ("... me to the alerts", "... to the weather alerts", "... my daughter to ..."); "... (to) my channel", "... and hit the bell" do not
_SUBSCRIBED = (r"(?:(?:me|us|him|her|them|it)\b|(?:the|my|our|your|his|her|their|this|that)\s+(?!(?:\S+\s+){0,2}?channel\b)"
               r"|to\s+(?!(?:(?:the|my|our|your|his|her|their|this)\s+)?(?:\S+\s+){0,2}?channel\b))")
_LEAD_IN = r"(?:(?:so|ok|okay|alright|all right|well|oh|hey|hi|hello|guys|everyone|everybody|folks|friends)\b\W*)*"        # "Hi everyone, thanks for watching!"
_SUBSCRIBE_ASK = r"please (?:(?:like|share|comment)\W+(?:and\W+)?)*subscribe"                                                   # "please subscribe", "please like, share and subscribe"
ARTIFACT = re.compile(rf"^\W*(?:{_LEAD_IN}(?:{_FOR_WATCHING}\b(?!\s+{_WATCHED})|{_SUBSCRIBE_ASK}\b(?!\s+{_SUBSCRIBED})).*|subtitles? by(?! default)\b.*|.*amara\.org.*)\W*$", re.I | re.S)
ARTIFACT_TAIL = re.compile(rf"[,;]?\s*\b{_FOR_WATCHING}\W*$", re.I)                  # ... or the end of a command that ran on into it: "turn off the lights, thank you for watching"
_CLOSING = (r"(?:(?:i|we)(?:\W?ll| will) )?(?:see|catch) (?:you|ya)\b|(?:talk|speak) to you\b|(?:good ?)?bye\b|take care\b|until next time\b"
            r"|have a (?:great|good|nice|wonderful|lovely) (?:day|one|week|weekend|night|time)\b|(?:don\W?t forget|remember) to\b|(?:please )?(?:(?:like|share|comment|and)\W+)*subscribe\b|hit the bell\b")
OUTRO_MORE = re.compile(rf"^\W*(?:and |so )?(?:{_CLOSING})", re.I)                  # "Thanks for watching. See you next time.": the second sentence, only ever behind a dropped outro


def without_artifacts(text: str) -> tuple[str, list[str]]:
    """(what is left of `text` when the sentences Whisper makes up for noise are taken out, the sentences taken out). Nothing left: the whole of it was noise."""
    kept, dropped = [], []
    behind = False                                              # the sentence before this one was an outro, or ended in one: a stock goodbye now is that video's
    for sentence in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        if not sentence:
            continue
        if ARTIFACT.match(sentence) or (behind and OUTRO_MORE.match(sentence)):
            dropped.append(sentence)
            behind = True
            continue
        trimmed = ARTIFACT_TAIL.sub("", sentence).strip()
        behind = bool(trimmed) and trimmed != sentence
        if behind:
            dropped.append(sentence[len(trimmed):].strip(" ,;"))
            sentence = trimmed
        kept.append(sentence)
    return " ".join(kept), dropped


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


class TurnTiming:
    """Where the time of one spoken turn went, from the end of his voice to Skippy's voice being ready (the Captain, 2026-11-05: "streamline it so it becomes snappy
    like Amazon echo"; measured that day, heard -> said had a median of 8 s and a p90 of 24 s): wait (the silence the voice detector waited to be sure he had finished),
    hear (the voice gate's Whisper), think (the brain, with its own split when it keeps one: brain.timing = {"route", "model_s": [...], "tools_s", "total_s"}) and voice
    (the reply turned into audio). One log line per turn and a `timing` in the house log's command record. A typed turn has no wait and no hear. Nothing here raises: a
    number that cannot be read is left out."""

    def __init__(self, heard: dict | None = None, sink=None):
        heard = heard if isinstance(heard, dict) else {}
        self.wait, self.hear = heard.get("wait"), heard.get("hear")
        self.think = self.voice = None
        self.brain = None
        self.sink, self._done = sink, False                     # sink(self) is called once: when the voice is ready, or when the turn ended without one

    def thought(self, seconds: float, brain=None) -> None:
        self.think, self.brain = seconds, brain

    def voiced(self, seconds: float) -> None:
        self.voice = seconds
        self.finish()

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        if self.sink is not None:
            self.sink(self)

    def _parts(self) -> list[tuple[str, float]]:
        return [(name, float(v)) for name, v in (("wait", self.wait), ("hear", self.hear), ("think", self.think), ("voice", self.voice)) if isinstance(v, (int, float))]

    def _split(self) -> tuple:
        """(route, [seconds of each model call], seconds of tools) from the brain's own record of the reply, whatever of it there is."""
        b = self.brain if isinstance(self.brain, dict) else {}
        route = b.get("route")
        model = [float(x) for x in (b.get("model_s") or []) if isinstance(x, (int, float))]
        tools = b.get("tools_s") if isinstance(b.get("tools_s"), (int, float)) else None
        return route, model, tools

    def numbers(self) -> dict:
        """Seconds to 0.1, for the house log's command record."""
        out = {name: round(v, 1) for name, v in self._parts()}
        out["total"] = round(sum(v for _, v in self._parts()), 1)
        try:
            route, model, tools = self._split()
            if self.brain:
                out["route"] = str(route) if route else "none"
            if model:
                out["model"] = [round(x, 1) for x in model]
            if tools is not None:
                out["tools"] = round(float(tools), 1)
        except Exception:                                       # noqa: BLE001 — the brain's own numbers are a bonus
            pass
        return out

    def line(self) -> str:
        """(timing: wait 0.7s, hear 0.9s, think 3.4s [route none; model 2.1+1.3s; tools 0.2s], voice 1.1s = 6.1s)"""
        bits = []
        for name, v in self._parts():
            text = f"{name} {v:.1f}s"
            if name == "think" and self.brain:
                try:
                    route, model, tools = self._split()
                    inner = [f"route {route if route else 'none'}"]
                    if model:
                        inner.append("model " + "+".join(f"{x:.1f}" for x in model) + "s")
                    if tools is not None:
                        inner.append(f"tools {float(tools):.1f}s")
                    text += " [" + "; ".join(inner) + "]"
                except Exception:                               # noqa: BLE001
                    pass
            bits.append(text)
        return f"(timing: {', '.join(bits)} = {sum(v for _, v in self._parts()):.1f}s)"


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
        self.music = None                                       # radio_client.PodTuner: this pod as a remote and a speaker of the house radio
        self.daily = None                                      # daily.Daily (shared): the morning, coming-home and bedtime routines
        self.stereo = None                                      # radio_client.RoomRadio: the house radio (songs, Spotify, his audiobooks: one stream) as this room sees it; Skippy's voice ducks it
        self.alarms = None                                      # alarms.Alarms: this room's alarms and reminders (on disk); one rings here until he answers
        self.clock = None                                       # alarm_clock.AlarmClock (the house's, shared): rings the alarms and timers instead of this room
        self.inbox = None                                       # a Path: words handed to the running hub from this PC (`hub.py --type ROOM TEXT`), taken as typed
        self.tasks: set = set()
        self.voice = asyncio.Lock()                             # one thing said at a time (a timer waits for an answer to finish)
        self.vad = Vad(floor_min=floor)
        self.pre: deque = deque(maxlen=24)                      # 1.2 s before the voice was noticed: "Hey Skippy" is quieter than what follows, so the bar is crossed late
        self._levels: deque = deque(maxlen=200)                 # the loudness of each block heard (10 s), beside `pre`: how loud he is, and what the voice detector saw
        self._last_voice = 0.0                                  # how loud he was the last time (the name, or what he said after it): where the bar for his next command starts
        self._window_t0 = 0                                     # when the listening window opened (the room's clock)
        self.buf: list | None = None
        self._speech_t0 = self._shown_t = None                  # when the voice began; when he last perked up for something that may not be for him
        self._probed = False
        self._heard = None                                      # {"wait": s, "hear": s}: what the utterance now being answered cost before the brain (handle() takes it for the timing line)
        self.t, self._carry = 0, []                             # t: milliseconds of audio heard on this connection (the room's own clock)
        self.mode, self.deadline = IDLE, 0
        self.recorder = None                                    # wakerec.Recorder: `hub.py --record` sends the room's audio there instead of listening
        self._last_audio = time.monotonic()                     # (monotonic) the box's last audio; a box that never sends any is noticed too
        link.room = self

    def link_mic_off(self) -> bool:
        """The box's microphone switch is off (its key, or Home Assistant): it is meant to send nothing."""
        mic_off = getattr(self.link, "mic_off", None)
        return bool(mic_off()) if mic_off is not None else False

    def _fresh(self) -> None:
        """Ready for the next thing said: the room's learned quiet level stays, everything half-heard goes."""
        self.vad.restart()
        self.pre.clear()
        self._levels.clear()
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
            self._window_t0 = self.t
            self.vad.arm(self._last_voice)                      # the box heard the name itself: his first words start at the lower bar
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
        if getattr(self, "_kicked", 0):
            self.log(f"[{self.name}] (the box's audio is back, {self._last_audio - self._kicked_at:.1f} s after asking)")
            self._kicked = 0
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
            self._levels.clear()
            return
        level = rms(block)
        self.pre.append(block)
        self._levels.append(level)
        if self.mode == IDLE and self.spotter is not None and self.remote_wake and self.spotter.feed(block):
            self._woke(getattr(self.spotter, "name", "skippy"))
        event = self.vad.push(level, self.t)
        if self.buf is not None and event != "speech":
            self.buf.append(block)
            if len(self.buf) > MAX_UTTERANCE_BLOCKS:            # a detector held open by a steady noise would grow this for ever (25 KB a block): cut it, whatever the mode
                self.log(f"[{self.name}] (mic: {MAX_UTTERANCE_BLOCKS * BLOCK_MS // 1000} s without a pause: cut there)")
                self._finish()
                self.vad.restart()
                event = None
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
            self._finish(getattr(self.vad, "waited_ms", None))
        self._tick()

    async def _probe(self, blocks) -> None:
        try:
            text = await asyncio.to_thread(self.gate.stt, wav_bytes([x for block in blocks for x in block]))
        except GateError:
            return
        woke, _ = wake_split(text)
        if woke and self.mode == IDLE and self.buf is not None:        # still speaking and still waiting: he is addressed
            self._woke("early")

    def _alarm_hush(self, by: str) -> None:
        """'Skippy' while an alarm rings: it stops at once (the Captain, 2026-11-01); a "snooze" said next still snoozes it."""
        if self.clock is not None and self.clock.woke(by=self.name):
            self.log(f"[{self.name}] alarm stopped by the wake word ({by})")

    def _woke(self, by: str) -> None:
        self.log(f"[{self.name}] woke")
        self._alarm_hush(by)
        if self.stereo is not None:
            self.stereo.duck(True, 25)                          # the house radio (a song, Spotify or a book) goes quiet, on every speaker, while he talks to Skippy
        self._record("wake", by=by)
        self.mode, self.deadline = LISTENING, self.t + self.listen_ms
        self._window_opened()
        if self.buf is None:
            self.buf = list(self.pre)[:-1]                      # "Skippy, lights off" in one breath: keep the whole sentence (this block is added below)
        # How loud he is from where he stands is what the name just was (its loudest 150 ms): the first words of the command need only a share of that to
        # begin, not the room's whole bar (the Captain, 2026-11-05: "the following commands I have to shout to get it to record my voice")
        voice = loudest(list(self._levels)[-self.pre.maxlen:])
        if voice > 0:
            self._last_voice = voice
        self.vad.arm(self._last_voice)
        self._spawn(self.link.woke(by))

    def _tick(self) -> None:
        if self._shown_t is not None and self.mode == IDLE and self.buf is None and self.t > self._shown_t + 2500:
            self._shown_t = None                                    # he perked up for nothing (a cough, the TV): back to his resting face
            self._spawn(self.link.idle_face())
        elif self._shown_t is not None and self.mode != IDLE:
            self._shown_t = None
        if self.mode in (LISTENING, FOLLOWUP) and self.t > self.deadline and (self.buf is None or not self.vad.speaking):
            # (a buffer seeded when the name was heard and a voice detector that never heard him begin used to hold the window open for ever: the room
            # stayed "listening" until something loud enough came, which is how a soft command became a shouted one)
            if self.mode == LISTENING:                          # (a conversation that ends is no news; a name with nothing after it is what to tune from)
                self.log(f"[{self.name}] (mic: nothing said; loudest {self._after_window():.3f}, the bar was {self.vad.start_bar():.3f}, the room {self.vad.quiet:.3f})")
            self.buf = None
            self._window_over()

    def _window_over(self) -> None:
        """A listening window ends with nothing said: the same way back to rest as every other (the detector is disarmed, the half-heard goes) and the box goes back
        to waiting for its name."""
        if self.pending_starter is not None:                    # offered, and nobody answered
            self._starter_answer(self.pending_starter, "ignored", "")
            self.pending_starter = None
        self._resume(IDLE)
        self._spawn(self.link.released())

    def _after_window(self) -> float:
        """The loudest 150 ms since the listening window opened (the name is not in it)."""
        n = max(0, int((self.t - self._window_t0) // BLOCK_MS))
        return loudest(list(self._levels)[-n:]) if n else 0.0

    def _finish(self, waited_ms: float | None = None) -> None:
        """`waited_ms`: the silence the voice detector waited before it found the end (the first part of the turn's timing line)."""
        utterance, self.buf = self.buf, None
        if len(utterance) * BLOCK_MS - self.vad.end_silence_ms < self.min_speech_ms:
            return                                              # a cough or a click is not worth sending
        if self.mode == IDLE and self.spotter is not None:
            return                                              # room talk, and the spotter did not hear its name: gone, never sent
        heard = loudest(list(self._levels)[-len(utterance):])
        if heard > 0:
            self._last_voice = heard                            # the next command starts from how loud he was this time
        self.log(f"[{self.name}] (mic: a voice at {heard:.3f}, the bar was {getattr(self.vad, 'speech_bar', 0.0) or self.vad.start_bar():.3f}, the room {self.vad.quiet:.3f})")     # (the bar it cleared to begin: the arming may be over by now)
        was = self.mode
        self.mode = BUSY
        self._spawn(self._utterance(was, [x for block in utterance for x in block], None if waited_ms is None else waited_ms / 1000))

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ── what was said ──
    async def _utterance(self, was: str, samples, waited_s: float | None = None) -> None:
        try:
            # No voice_stopped() here: it stops the box's microphone until an answer comes. "Skippy" and a pause is an utterance of just the name
            # (or Whisper refuses it): the room listens on for the command, and a box told to stop sent nothing more (2026-11-01: 8 s of silence,
            # then the watchdog). heard() stops it once there is something to answer.
            if self.debug:                                      # keep the last thing heard, to listen to what was cut off
                try:
                    (Path(__file__).resolve().parents[2] / "state" / "utterances").mkdir(exist_ok=True)
                    (Path(__file__).resolve().parents[2] / "state" / "utterances" / f"{int(time.time())}.wav").write_bytes(wav_bytes(samples))
                    self.log(f"[{self.name}] (debug: kept {len(samples) / 16000:.1f} s of audio)")
                except OSError:
                    pass
            heard_at = time.monotonic()
            try:
                text = await asyncio.to_thread(self.gate.stt, wav_bytes(samples))
            except GateError as e:
                self.log(f"[{self.name}] (could not hear: {e})")
                text = ""
            hear_s = time.monotonic() - heard_at
            woke, rest = wake_split(text)
            if was == IDLE:                                     # no spotter: Whisper is the wake word
                if not woke:
                    self.log(f"[{self.name}] (heard something that was not for me" + (f": {text!r}" if self.debug else "") + ")")
                    self._resume(IDLE)
                    return
                self.log(f"[{self.name}] woke")
                self._alarm_hush("whisper")
                self._record("wake", by="whisper")
                await self.link.woke("skippy")
                command = rest
            else:
                command = rest if woke else text
            if command:                                         # what Whisper writes for noise is not a word said to the house: the rest of the command is
                command, dropped = without_artifacts(command)
                if dropped:
                    self.log(f"[{self.name}] (" + ("took out what Whisper makes of noise" if command else "not a command, only what Whisper makes of noise") + f": {dropped!r})")
            if not command:
                await self._nothing_followed(was, woke)
                return
            self._heard = {"wait": waited_s, "hear": hear_s}    # what this utterance cost before the brain: the turn's timing line (handle() takes it)
            await self.handle(command)
        except Exception as e:                                  # noqa: BLE001 — one bad turn must not take the room down
            self.log(f"[{self.name}] (turn failed: {type(e).__name__}: {e})")
            self._resume(IDLE)
            await self.link.released()

    async def _nothing_followed(self, was: str, woke: bool) -> None:
        """An utterance with no command in it. The name alone ("Skippy." and a pause), or the name and only what Whisper makes of noise: the command is still to come,
        so the window opens in full again. Noise with no name in it (a cough, the TV, a fan: at the lowered bar after the name they begin "speech" now and then) must not
        do that. It used to open another full window each time, and a room that flickers kept it open for ever, a Whisper call and a "voice" for every flicker, with
        whatever the TV said taken for his command (the review of 2026-11-05). Noise leaves the window as it was: it ends at its own deadline."""
        if woke:                                                # (with no wake-word model it was Whisper that heard the name: was == IDLE, and a window opens as for any name)
            self._resume(LISTENING if was == IDLE else was, self.followup_ms if was == FOLLOWUP else self.listen_ms)
            return
        left = self.deadline - self.t
        if left > 0:
            self._resume(was, left)
            return
        self.log(f"[{self.name}] (mic: only noise since the name: back to rest)")
        self._window_over()

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
        if answer == "no":
            await self._decline(command)
            return True
        reply = {"yes": f"Done. Every {pending['weekday']} at {pending['time']}, and I will not even gloat.", "never": "Understood. I will never ask again."}[answer]
        await self.say(reply, follow_up=True)
        self._resume(FOLLOWUP if self.remote_wake and self.link.hub_follows_up else IDLE, self.followup_ms)
        return True

    def asked_just_now(self) -> bool:
        """Skippy's last words ended with a question (a starter, an offer, "want me to ...?"), said in the last minute and a half."""
        said, at = getattr(self, "_last_said", ("", None))
        if at is None or time.monotonic() - at > 90:
            return False
        return any("?" in s for s in re.split(r"(?<=[.!?])\s+", (said or "").strip())[-2:])

    async def _decline(self, command: str) -> None:
        """He said no to what Skippy asked (the Captain, 2026-10-31: "short and to the point, 'Okay, no problem Captain' ... without a whole
        turn"): one pre-recorded line, no brain, and the conversation ends as after a sleep word."""
        quip = self.filler.pick("decline", command) if self.filler is not None and self.filler.has("decline") else None
        text = quip[0] if quip else "Okay, no problem, Captain."
        self.log(f"[{self.name}] (declined: {text})")
        if quip is not None:
            async with self.voice:
                await self.link.show(text)
                await self._out(text, ("mp3", quip[1]), announce=True)
            self._last_said = (text, time.monotonic())
        else:
            await self.say(text)
        self._resume(IDLE)
        await self.link.released()

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
        if answer == "no" or (answer is None and is_decline(command)):
            self._starter_answer(cand, "no", command)
            await self._decline(command)
            return True, command
        if answer == "never":
            self._starter_answer(cand, answer, command)
            topic = {"world": "news", "": "of that"}.get(cand.get("topic", ""), cand.get("topic", ""))
            await self.say(f"Understood. No more {topic} from me for a month.")   # (still busy: the room never hears its own voice)
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
        if getattr(self.music, "playing", False):
            return                                              # never over music or a book (the house radio plays both)
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

    def _house_audio_playing(self) -> bool:
        """Anything the house plays right now (a song, Spotify, a book: one stream), as the brain reads it for a bare "stop" (houseaudio.Audio)."""
        audio = getattr(self.brain, "audio", None)
        try:
            return bool(audio.playing()) if audio is not None else False
        except Exception:                                       # noqa: BLE001
            return False

    def _plain_audio_stop(self, command: str) -> bool:
        """He said a plain "stop" / "enough" while the house audio plays: the words brain.stop_route reads as a stop. A routine that is running (daily.END_RE) and the sign-off
        (wake.is_stop) take those words before the brain ever hears them, and the audio played on (the review of 2026-11-05); it is asked here, before either of them acts."""
        if not self._house_audio_playing():
            return False
        try:
            from brain import stop_route                        # noqa: PLC0415
            return stop_route(command, True) is not None
        except Exception:                                       # noqa: BLE001
            return False

    async def _also_stop_audio(self, command: str) -> list[str]:
        """The word that ended a routine or signed off was a stop of the house audio too (Bilby is one stream, and "stop" means all of it): pause it through the music tool,
        like the brain would have, so the stop is counted (a routine that has not started its music yet leaves it off) and in the house log. -> the tools that ran."""
        run = getattr(self.brain, "run_tool", None)
        if run is None or not self._house_audio_playing():      # (the routine's own "Music off." may have got there first)
            return []
        try:
            said = await asyncio.wait_for(asyncio.to_thread(run, "music", {"action": "pause"}), STOP_AUDIO_WAIT_S)
        except asyncio.TimeoutError:                            # a radio that took the poll but not the call: the room stays BUSY (deaf) only this long, not for the call's 40 s; its pause may still land
            self.log(f"[{self.name}] (could not stop the house audio: the radio did not answer within {STOP_AUDIO_WAIT_S:g} s)")
            return []
        except Exception as e:                                  # noqa: BLE001 — the word still did what it did
            self.log(f"[{self.name}] (could not stop the house audio: {type(e).__name__}: {e})")
            return []
        self.log(f"[{self.name}] stopped the house audio too: {str(said)[:80]}")
        return ["music"]

    async def handle(self, command: str, typed: bool = False, speak: bool = True) -> None:
        heard, self._heard = getattr(self, "_heard", None), None
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
        audio_stop = not typed and self._plain_audio_stop(command)
        ringing = self.clock is not None and self.clock.ringing is not None
        snoozed = None if ringing or self.clock is None else self.clock.snooze_after_wake(command, by=self.name)   # the wake word stopped it; "snooze" said next
        if snoozed is not None or ringing or (self.clock is None and self.alarms is not None and self.alarms.ringing is not None):
            answer = snoozed or (self.clock.answered(command, by=self.name) if ringing else self.alarms.answered(command))   # any word to any pod: "stop" / "snooze" here, anything else to the brain
            if answer is not None:
                self.log(f"[{self.name}] alarm answered: {command} -> {answer}")
                self._record("command", words=command, tools=["alarm"], outcome="alarm_answered")
                self._turn_at = time.monotonic()
                await self.say(answer, typed=typed)
                if not typed:
                    self._resume(IDLE)
                    await self.link.released()
                return
        if self.daily is not None and await self.daily.claim(self, command):
            if audio_stop and await self._also_stop_audio(command):        # "stop" that ended a routine left the music it started playing: one stop is everything
                self._record("command", words=command, tools=["music"], outcome="routine_stop")
            self._turn_at = time.monotonic()                    # a routine's words ("goodnight", "skip", "later"): the routine answers, not the brain
            if self.mode == BUSY and not typed:
                self._resume(IDLE)
            return
        self.pending = None
        self._turn_at = time.monotonic()
        if not typed and self.asked_just_now() and is_decline(command):
            self.log(f"[{self.name}] heard: {command}")
            self._record("command", words=command, tools=[], outcome="declined")
            await self._decline(command)
            return
        if not typed and (is_goodbye(command) or is_stop(command)):
            stopped = await self._also_stop_audio(command) if audio_stop else []       # a plain "stop" while the house plays is the house's stop too, not only the end of the talk
            self._record("command", words=command, tools=stopped, outcome="sign_off")
            self._resume(IDLE)
            await self._sign_off(command)
            await self.link.released()
            return
        self.log(f"[{self.name}] heard: {command}")
        started, outcome = time.time(), "ok"
        turn, thought = TurnTiming(None if typed else heard), time.monotonic()
        try:
            work = asyncio.ensure_future(asyncio.to_thread(self.brain.reply, command))
            await self._cover_the_wait(work, command)
            reply = await work
        except Exception as e:                                  # noqa: BLE001 — whatever the API did, he gets an answer
            self.log(f"[{self.name}] (brain failed: {type(e).__name__})")
            outcome = "brain_failed"
            reply = "My brain is not answering. Typical."
        turn.thought(time.monotonic() - thought, getattr(self.brain, "timing", None) if outcome == "ok" else None)     # (a failed reply has no split of its own: the last one's is not its)
        self.log(f"[{self.name}] said: {reply[:120]}")
        calls = list(getattr(self.brain, "actions", None) or [])
        seconds = round(time.time() - started, 1)
        guard = " ".join(x for x in (getattr(self.brain, "guard", None) or ()) if x) or None      # claim_reasked etc.: the count of claims needs no log archaeology

        def note(t: TurnTiming) -> None:
            """The command's record (with the timing) and the timing line: written when the reply's voice is ready, a moment after the reply, so the timing is whole."""
            try:
                if self.hlog is not None:
                    self._record("command", words=command, tools=[c["tool"] for c in calls], outcome=outcome, reply=reply, seconds=seconds, guard=guard, timing=t.numbers())
                    self.hlog.actions(self.name, calls)
                self.log(f"[{self.name}] {t.line()}")
            except Exception as e:                              # noqa: BLE001 — a log must never cost him his answer
                self.log(f"[{self.name}] (could not log the turn: {type(e).__name__}: {e})")
        turn.sink = note
        from housemem import DISPLAY, action_line               # noqa: PLC0415
        tools = [action_line(c) for c in calls if c.get("tool") in DISPLAY]
        if tools:
            self._emit("actions", tools=tools[:8])
        try:
            await self.say(reply, follow_up=True, silent=not speak, typed=typed, timing=turn)
        finally:
            turn.finish()                                       # (a silent typed turn or a failed one never reaches the voice: the record is still written)
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
        if mode in (LISTENING, FOLLOWUP):
            self._window_opened()
        if mode == LISTENING:
            self.vad.arm(self._last_voice)                      # only the name was heard so far: the command still starts at the lower bar (not after a reply: its own echo)
        self._session(mode == FOLLOWUP)

    def _window_opened(self) -> None:
        """A listening window opens: the watchdog's 8 s count from now, not from the box's last audio (the pod saying something stops its
        microphone; it starts again a moment after, by which time the old count said 'no audio' and ended every routine's follow-up)."""
        self._last_audio = max(self._last_audio or 0.0, time.monotonic())
        self._window_t0 = self.t

    async def _restart_box(self, why: str) -> None:
        """The box sends no audio when it should: ask it to start its stream again (a box that can; a Wyoming satellite reconnects by itself)."""
        restart = getattr(self.link, "restart_stream", None)
        if restart is None:
            return
        tries = getattr(self, "_kicked", 0) + 1
        self._kicked_at = time.monotonic()
        try:
            how = await restart(hard=tries >= 2)                # the first time its turn is ended; after that its microphone switch goes off and on
        except Exception as e:                                  # noqa: BLE001 — the next check tries again
            self.log(f"[{self.name}] (restarting the box's microphone failed: {type(e).__name__}: {e})")
            return
        if how:
            self._kicked = tries
            self.log(f"[{self.name}] (no audio from the box {why}: {how})")

    def _session(self, on: bool) -> None:
        """A conversation is open (the name opened it; it closes after a long silence or a goodbye): the box says so on its screen."""
        show = getattr(self.link, "session", None)
        if show is not None:
            show(on)

    async def say(self, text: str, follow_up: bool = False, announce: bool = False, silent: bool = False, typed: bool = False, voice=None, timing: TurnTiming | None = None) -> None:
        """Skippy's voice in this room: the gate's local TTS (a summary when long), else the machine's own voice. Waits until it has played.
        `timing`: the turn being answered; it is told how long the reply took to become audio (the last part of its timing line)."""
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
            made = time.monotonic()
            voice = voice if voice is not None and said == text else await self._voice(said)    # a clip made already (a starter) is used as it is
            if timing is not None:
                timing.voiced(time.monotonic() - made)
            await self.link.show(said)
            if self.stereo is not None:
                self.stereo.duck(True, 90)                      # the house radio ducks under Skippy's voice, then comes back
            try:
                await self._out(said, voice, follow_up=follow_up, announce=announce)
            finally:
                if self.stereo is not None:
                    self.stereo.duck(True, self.followup_ms / 1000 if follow_up else 2.5)

    async def _out(self, said: str, voice, follow_up: bool = False, announce: bool = False) -> None:
        """One clip of Skippy's voice in this room (the voice lock held). With a PC output open that is the room's one voice (the Captain,
        2026-10-31: two speakers never line up exactly enough for an ear), it plays there ALONE: the pod gets the words without the sound,
        draws the lips on the PC output's clock, and stays the mic. No PC output open, or the router dropped the clip: the pod says it."""
        sole = None
        if announce and voice is not None and voice[0] == "mp3":
            for out in getattr(self, "announce_outs", ()):      # a room with no pod and no mic (upstairs): no lips, no "was it heard?", never waited for
                asyncio.ensure_future(out.play(voice[1]))
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
        elif getattr(sole, "last_heard", True) is False:         # a probe of an output taken for off (sync.py), and still nobody heard it
            self.log(f"[{self.name}] (the {sole.kind} output is still off: the pod says it)")
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

    def stereo_kinds(self) -> list[str]:
        """The PC outputs that are this room's one voice and can sound right now ([] when only the box can speak): open and not taken for off, the
        same test as _out's but a look that spends nothing (RoutedOut.playable). The box's own speaker is the last resort for everything (the Captain,
        2026-11-04: "we never want to output to the hub speakers unless that house level's stereo is unavailable")."""
        # only the outputs the vault gives this room: "talk to me downstairs" rewrites the RoutedOut's kinds until the next restart (music_tools), and
        # another floor's stereo is no stand-in for this box
        configured = None
        env = getattr(self, "env", None)
        if env:
            import speaker_out                                  # noqa: PLC0415
            configured = set(speaker_out.room_kinds(env, self.name)) - {"pod"}
        for out in self.pc_outs:
            if getattr(out, "sole", False):
                kinds = out.playable() if hasattr(out, "playable") else (sorted(out.kinds) if out.available() else [])
                kinds = [k for k in kinds if configured is None or k in configured]
                if kinds:
                    return kinds
        return []

    def _stereo_is_on(self) -> bool:
        """This room has a stereo that can sound now (stereo_kinds). True when that cannot be told: waiting for a ring that is not this room's is only slow, speaking over
        one that is would be two Skippys at once."""
        try:
            return bool(self.stereo_kinds())
        except Exception:                                       # noqa: BLE001
            return True

    async def ring(self, tone: bytes, voice, text: str, quiet: bool = False, wait=None, floor: bool = False) -> None:
        """One ring of the house's alarm clock on this pod (alarm_clock.py): the chime, then the words; then a plain "stop" needs no name.
        `wait` is the house's stereo ringing (a task answering True when something sounded); `quiet`: the stereo rings for this box, which stays
        silent. The box shows the words and stays busy, deaf, until the stereo has finished and its echo has died away (its microphone would hear "Say
        stop" and the alarm would stop itself), then opens the "stop" window as after any ring. A box that is not quiet but has a stereo ringing (the
        alarm's third ring on) rings AFTER it, never with it: the same words from two speakers a moment apart are two Skippys at once (the Captain,
        2026-11-05). That is a box whose OWN room has a stereo on: a box in a room with none is not joining anything (the stereo's ring is somewhere else in the
        house, and it is the only speaker he hears), so it rings at once. If the stereo's ring failed or never finished, the box rings at once.
        `floor`: the alarm clock already holds this room's voice lock."""
        import alarm_clock                                      # noqa: PLC0415
        was, self.mode = self.mode, BUSY
        try:
            async with (contextlib.nullcontext() if floor else self.voice):
                await self.link.show(text)
                if wait is not None and (quiet or self._stereo_is_on()):
                    try:
                        sounded = await asyncio.wait_for(asyncio.shield(wait), alarm_clock.RING_WAIT_S) is not False     # False: nothing sounded on the stereo
                    except Exception as e:                      # noqa: BLE001 — it failed, or took too long: the box is the only speaker left
                        if quiet:
                            self.log(f"[{self.name}] (the stereo's ring failed: {type(e).__name__}: the box rings)")
                        sounded = False
                    if sounded:
                        await asyncio.sleep(alarm_clock.LISTEN_AFTER_S)     # the Enclave's latency and the room's echo have to die away, whoever speaks next
                    else:
                        quiet = False
                if not quiet:
                    await self.link.announce("", ("pcm", *alarm_clock.pcm_of(tone)))
                    if voice is not None:
                        clip = ("mp3", voice) if voice[:4] != b"RIFF" else ("pcm", *alarm_clock.pcm_of(voice))
                        await self.link.announce(text, clip)
        finally:
            self._resume(IDLE if was == BUSY else was, self.followup_ms if was == FOLLOWUP else 0)
        self._listen_for_answer()

    def _listen_for_answer(self) -> None:
        """After an alarm or a reminder: a plain "stop" or "snooze" needs no name, as after a routine's question (daily.py)."""
        if self.mode == IDLE and self.remote_wake and self.link.hub_follows_up:
            self._resume(FOLLOWUP, self.followup_ms)

    async def timers_due(self) -> None:
        # a watchdog: the room's clock runs on the audio it hears, so if the box's audio stops while he is waiting for a command, nothing would ever time out
        last = getattr(self, "_last_audio", None)
        if last is not None and self.mode in (LISTENING, FOLLOWUP) and time.monotonic() - last > 8:
            self.log(f"[{self.name}] (no audio from the box for 8 s while listening: back to rest)")
            self._resume(IDLE)
            self._shown_t = None
            await self.link.released()
            await self._restart_box("while listening")
        elif (last is not None and self.mode == IDLE and self.recorder is None and not self.voice.locked() and not self.link_mic_off()
              and time.monotonic() - last > REST_SILENCE_S
              and time.monotonic() - getattr(self, "_kicked_at", 0.0) > (REST_SILENCE_S if getattr(self, "_kicked", 0) < 3 else 300)):
            await self._restart_box(f"for {time.monotonic() - last:.0f} s while resting")       # deaf at rest: his name would never be heard
        for timer in (self.timers.due() if self.clock is None else ()):     # with the house's clock, it rings them (alarm_clock.py)
            self.log(f"[{self.name}] timer done: {timer.label}")
            from alarms import timer_name                       # noqa: PLC0415
            await self.announce(f"Your {timer_name(timer.label)} timer is done.")
        if self.alarms is not None and self.clock is None:
            for alarm in self.alarms.due():
                self.log(f"[{self.name}] {alarm.kind} going off: {alarm.hhmm} {alarm.label}")
                self._record("alarm", what=alarm.kind, label=alarm.label or None)
                await self.announce(self.alarms.spoken(alarm))
                self._listen_for_answer()
            again = self.alarms.ring_again()                    # nobody answered: once a minute, a few times
            if again is not None:
                await self.announce(self.alarms.spoken(again))
                self._listen_for_answer()
        if self.workspace is not None and self.run_action is not None:
            for auto in self.workspace.due():                   # automations only exist after his recorded yes; each fires once a day, quietly
                result = await asyncio.to_thread(self.run_action, auto["action"]["tool"], auto["action"]["args"])
                self.log(f"[{self.name}] automation {auto['id']}: {result}")
                self._record("automation", automation=auto["id"], tool=auto["action"]["tool"], target=auto["action"]["args"].get("name"),
                             act=auto["action"]["args"].get("action"), outcome=str(result)[:120])
        if self.inbox is not None and self.inbox.is_file() and self.mode == IDLE and not self.voice.locked():
            try:
                text = str(json.loads(self.inbox.read_text(encoding="utf-8")).get("text") or "").strip()
            except (OSError, ValueError, AttributeError):
                text = ""
            self.inbox.unlink(missing_ok=True)
            if text:
                self.log(f"[{self.name}] typed on this PC: {text}")
                await self.typed(text)
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
        await self.voice_stopped()                              # only now: "Skippy" and a pause must not stop it before the command (Room._utterance)
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
    import weather as weather_mod                                # noqa: PLC0415
    house_weather = weather_mod.Weather(env, log=log)            # one house, one weather (its stations and cache are shared by every room)
    import car as car_mod                                        # noqa: PLC0415 — his Tesla, read only (the car reader on skippy-core): the portal chat's car_status, by voice
    import physics as physics_mod                                # noqa: PLC0415 — Mentat, the only source of a physical number (PLATINUM 21): the portal chat's ask_mentat, by voice
    house_car = car_mod.Car(env, log=log)                        # one car and one Mentat for every room (a suggestion of Mentat's waits for his yes in the room that heard it)
    house_physics = physics_mod.Physics(env, log=log)
    from starters import Starters                                # noqa: PLC0415
    starters = None if env.get("SATELLITE_STARTERS", "on").lower() in ("off", "0", "no") else \
        Starters(Path(__file__).resolve().parents[2] / "state" / "starters", log_root=hlog.root)   # filled daily by tools/starter_refresh.py
    workspace = Workspace(hlog.root)
    run_action = (lambda tool, args: house_mod.run_tool(house, tool, args)) if house is not None else None
    loop = asyncio.get_running_loop()                           # build_rooms runs on the hub's loop (_main)
    import daily as daily_mod                                    # noqa: PLC0415
    from brain import load_persona                               # noqa: PLC0415
    import radio_client                                          # noqa: PLC0415
    import houseaudio                                            # noqa: PLC0415 — what Bilby plays now: the chat's prompt and a bare "stop"
    radio = radio_client.RadioClient.from_env(env, log=log)       # the house radio (radio.py, its own process): the hub is only its remote
    book_view = radio_client.BookView(radio)                      # the radio's book as the plain spoken commands read it (book_voice.pre_route)

    def stereo():
        """The house radio: Bilby's one station (radio.py). Its client answers even while the radio restarts (then it says so)."""
        return radio
    routines = None if env.get("SKIPPY_ROUTINES", "on").lower() in ("off", "0", "no") else         daily_mod.Daily(env, house=house, client=client, model=brain_model, filler=filler_bank, stereo=stereo, hlog=hlog, log=log, persona=load_persona())
    if routines is not None:
        import google_home                                       # noqa: PLC0415 — his calendar, mail and contacts, read only, once he has signed in
        routines.extras.update(google_home.extras(google_home.Google(env, log=log), routines.aword, log=log))
    import autoclose                                             # noqa: PLC0415 — the garage door closes itself when the car drives away
    garage_watch = autoclose.build(env, house, routines.home if routines is not None else lambda: None, log=log)
    if garage_watch is not None and routines is not None:
        routines.extras["garage_news"] = garage_watch.news        # the homecoming routine says what happened to the door
    import cams as cams_mod                                      # noqa: PLC0415 — the doorbell and cameras: a press, a person said aloud, DMed
    cams_watch = cams_mod.build(env, house, log=log)
    import generac                                               # noqa: PLC0415 — the standby generator: an outage, the power back, a fault said aloud, DMed
    generac_watch = generac.build(env, house, log=log)
    if generac_watch is not None and routines is not None:
        async def generator_morning():
            return await asyncio.to_thread(generac_watch.gen.attention)
        routines.extras["generator"] = generator_morning          # the morning says it only when the generator needs him (daily.py)
    from homechat import HomeChat                                # noqa: PLC0415
    import music as music_mod                                    # noqa: PLC0415
    homechat = HomeChat(env.get("PORTAL_URL") or music_mod.PORTAL, token, enabled=env.get("SATELLITE_HOME_CHAT", "on").lower() not in ("off", "0", "no"), log=log)
    import lists as lists_mod                                    # noqa: PLC0415
    state = Path(__file__).resolve().parents[2] / "state"
    house_lists = lists_mod.Lists(state / "lists.json", log=log)  # one set of lists for the whole house
    import alarm_clock as alarm_clock_mod                        # noqa: PLC0415 — the house's one alarm clock: rings even with every pod away
    import router as router_mod                                  # noqa: PLC0415
    house_clock = alarm_clock_mod.AlarmClock(state, env, gate=gate, local_voice=audio.local_voice_pcm16, log=log,
                                             router=router_mod.shared(env, log) if env.get("SATELLITE_ROUTER", "1") != "0" else None)
    if env.get("SATELLITE_ROUTER", "1") != "0":
        router_mod.shared(env, log).voice_hook = radio.duck_voice   # every clip of Skippy's voice (a reply, an alarm, a timer, an announcement) ducks the whole stream, on every device
    if routines is not None:
        async def alarm_tomorrow():
            return house_clock.tomorrow_line()
        routines.extras["alarm_tomorrow"] = alarm_tomorrow        # bedtime: "Your alarm is set for 6:30." (daily.py)
        routines.extras["wake_alarm"] = house_clock.wake_time     # the morning briefing comes before the day's first alarm, never over it (daily.morning_time)
        routines.extras["alarm_ringing"] = house_clock.is_ringing  # and waits while one rings
        nest = next((p for p in getattr(house, "parts", [house]) if type(p).__name__ == "Nest"), None)
        if nest is not None:
            async def thermostat_night():
                night = env.get("SKIPPY_NIGHT_SETPOINT", "").strip()
                try:
                    return await asyncio.to_thread(nest.bedtime, float(night) if night else None)
                except Exception as e:                           # noqa: BLE001 — not connected or Google down: bedtime goes on without it
                    log(f"(nest: {str(e)[:120]})")
                    return None
            routines.extras["thermostat_night"] = thermostat_night   # bedtime: the thermostat's line, and the night set point if he chose one
    connected: dict = {}                                         # room name -> Room, for announcements to the other rooms

    def make_room(name, link):
        timers = house_clock.timers_for(name)
        alarms = house_clock.alarms
        spotter = Spotter.load(model_path, threshold=threshold) if model_path.is_file() else None
        holder: dict = {}
        tools = music_tools(lambda: holder["room"], loop, stereo=stereo)   # every room is a remote of the house radio: music, Spotify and his audiobooks are ONE stream
        tools = audiobooks.combine(tools, house_clock.tools(name), house_lists.tools(), announce_tools(lambda: holder["room"], connected, env, loop))
        brain = Brain(client, brain_model, timers, hass=house, extra=tools, news=news_mod.News(env, log=log), weather=house_weather, car=house_car, physics=house_physics)
        brain.room = name                                        # the primer says which room of house_layout.json he is talking from
        brain.audio = houseaudio.Audio(radio, routines, hlog)    # the prompt says what Bilby plays now (a book too) and what happened lately; a bare "stop" waits for it
        brain.pre_route = lambda heard: book_voice.pre_route(heard, book_view)   # "next chapter", "back thirty seconds", "read my book": the radio's book, before the model
        room = holder["room"] = Room(name, link, gate, brain, timers, spotter=spotter, local_voice=audio.local_voice_pcm16, log=log,
                                     floor=float(env.get("SATELLITE_FLOOR", "0.012")), filler=filler_bank, debug=env.get("SATELLITE_DEBUG") == "1", mic_gain=float(env.get("SATELLITE_MIC_GAIN", "1.0")), pc_outs=speaker_out.parse_outputs(env, name, log)[1],
                                     filler_after_s=float(env.get("SATELLITE_FILLER_S", "1.0")), hlog=hlog, workspace=workspace, run_action=run_action, homechat=homechat, starters=starters,
                                     followup_ms=int(float(env.get("SATELLITE_SESSION_S", "30")) * 1000))      # the name opens a conversation: it stays open this long without a word
        room.alarms = alarms
        room.clock = house_clock
        room.inbox = state / "hub-inbox" / f"{name}.json"
        connected[name] = room
        room.announce_outs = speaker_out.announce_outputs(env, name, log)   # the routines, timers and messages also upstairs (SATELLITE_ANNOUNCE_OUT)
        room.env = env
        room.daily = routines
        room.stereo = radio.for_room(radio_client.room_kinds(env, name), name)   # Skippy speaking here ducks the whole station (all of it, on every device)
        return room
    make_room.radio = radio                                     # esphome_link.serve: each pod is a remote and a speaker of it (PodTuner)
    make_room.homechat = homechat                               # _main runs its sync loop (never under --record)
    make_room.alarm_clock = house_clock                         # _main runs it
    make_room.garage_watch = garage_watch                       # _main runs it (autoclose.py)
    make_room.cams_watch = cams_watch                           # _main runs it (cams.py)
    make_room.generac_watch = generac_watch                     # _main runs it (generac.py)
    make_room.routine_room = lambda: getattr(routines, "room_name", "") or ""
    make_room.house = house                                     # the heartbeat says which of its backends answer
    make_room.state = state
    return make_room


HEARTBEAT_S = 60                                                # state/hub-status.json, read by the starter for the Status map


def backend_health(house) -> dict:
    """{backend: {"ok", "things", "why"}} for each part of the house (HomeKit, Matter, the garage, Leviton, the Nest, ...): each asked
    through its own cache, so this costs no more than Skippy asking about the house."""
    out = {}
    for part in getattr(house, "parts", [house] if house is not None else []):
        name = type(part).__name__.lower()
        try:
            out[name] = {"ok": True, "things": len(part.states())}
            if callable(getattr(part, "summary", None)):        # a backend with one thing worth a line (the generator: "Generator ready, battery 13.4 V")
                out[name]["detail"] = str(part.summary())[:160]
        except Exception as e:                                  # noqa: BLE001 — HomeError (said aloud) or anything else: the reason is the point
            out[name] = {"ok": False, "why": str(e)[:160]}
    return out


async def heartbeat(hub, path: Path, house=None, every: float = HEARTBEAT_S, clock=time.time, sleep=asyncio.sleep) -> None:
    """Every minute, what the hub knows for the Status map: each room box connected or not, each house backend answering or not. Written
    whole (a temp file renamed), so the starter never reads half of it."""
    while True:
        try:
            backends = await asyncio.to_thread(backend_health, house) if house is not None else {}
            data = {"at": int(clock()), "rooms": {spec[0]: spec[0] in hub.rooms for spec in hub.rooms_spec}, "house": backends}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except Exception:                                       # noqa: BLE001 — the heartbeat never takes the hub down
            pass
        await sleep(every)


MUSIC_TOOLS = [
    {"name": "music", "description": "Control the music: the house radio, Bilby's ONE station that every speaker in the house plays (Spotify "
                                     "included).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["pause", "resume", "next", "previous", "louder", "quieter", "volume", "what", "favorite", "tired"],
                      "description": "favorite: add the song playing now to his favorites pool (from his library or Spotify): 'add this song to my favorites', 'favorite this'. "
                                     "tired: take the song playing now out of his favorites and every mix and skip it: 'I'm tired of this song', "
                                     "'drop this one', 'remove this from my favorites'. "
                                     "what: what is playing ('what song is this', 'who sings this'). louder / quieter / volume (with level): the house stereo's "
                                     "volume ('turn it down', 'volume 5')"},
                                                       "level": {"type": "integer", "description": "volume: 1 (quietest) to 10 (full)"}},
                      "required": ["action"]}},
    {"name": "play_music", "description": "Play music from the Captain's own library (Bilby) on the house radio, here or on the speakers named: an artist, an album "
                                          "or a song, in his words; 'favorites' for his favorites list; 'everything' for the whole library. shuffle=true for "
                                          "'shuffle ...'. queue=true for 'after this', 'queue ...', 'add ... to the queue' (it plays after what plays now, Spotify "
                                          "included). His grammar: mode (discovery / favorites) + filters (genre, playlist, artist, album, song, new) + speaker; "
                                          "where it plays goes in speaker ONLY, never in what, genre or artist ('heavy metal upstairs on the Enclave' is genre=heavy metal, "
                                          "speaker=upstairs); never ask him to confirm, just play it.",
     "input_schema": {"type": "object", "properties": {"what": {"type": "string", "description": "e.g. 'Pink Floyd', 'Comfortably Numb', 'favorites', 'everything'"},
                                                       "mode": {"type": "string", "enum": ["discovery", "favorites"],
                                                                "description": "discovery: all his songs, his favorites about one in four, and Spotify suggestions; "
                                                                               "favorites ('favorite music', 'my library'): his favorites and Spotify liked songs"},
                                                       "genre": {"type": "string", "description": "e.g. 'goth', 'darkwave', 'industrial'"},
                                                       "playlist": {"type": "string", "description": "one of his playlists (Traktor's DJ sets or Spotify's), by name"},
                                                       "artist": {"type": "string"}, "album": {"type": "string"}, "song": {"type": "string"},
                                                       "new": {"type": "boolean", "description": "'new songs': the ones added to Bilby most recently"},
                                                       "shuffle": {"type": "boolean"}, "queue": {"type": "boolean"},
                                                       "speaker": {"type": "string", "enum": ["here", "stereo", "everywhere", "downstairs", "upstairs"],
                                                                   "description": "ONLY when he named a place; leave it out otherwise (the speakers stay as they are: every speaker that is on plays the one stream). "
                                                                                  "here: this room's speakers; stereo: the speakers it plays on now ('on the stereo', 'DJ ...'); "
                                                                                  "everywhere: downstairs AND upstairs ('everywhere', 'the whole house', 'both'); "
                                                                                  "downstairs: the theater's Sony ('downstairs', 'theater', 'Sony'); "
                                                                                  "upstairs: the Enclave ('upstairs', 'living room', 'Enclave')"}},
                      "required": []}},
    {"name": "speakers", "description": "Which speakers play Bilby's ONE stream (songs, Spotify, his audiobooks), on command. Each speaker has a switch that stays as he set it: "
                                        "device + on turns ONE speaker on or off ('turn the Sony off', 'play on the Sony too', 'switch the Enclave on'); "
                                        "place sets them as a group ('move the music upstairs' = the Enclave on, the Sony off; 'music everywhere' = all on). "
                                        "what=music for the stream, voice for Skippy's own replies ('talk to me upstairs', 'your voice back downstairs'; place only). "
                                        "everywhere: downstairs and upstairs; downstairs: the theater's Sony; upstairs: the Enclave (living room). "
                                        "With every speaker off the stream waits; it carries on when one is turned on.",
     "input_schema": {"type": "object", "properties": {"what": {"type": "string", "enum": ["music", "voice"]},
                                                       "place": {"type": "string", "enum": ["everywhere", "downstairs", "upstairs"]},
                                                       "device": {"type": "string", "description": "one speaker, in his words: 'the Sony', 'the Enclave', 'the living room box' (what=music)"},
                                                       "on": {"type": "boolean", "description": "with device: true turns it on, false turns it off"}},
                      "required": ["what"]}},
    {"name": "dj_mode", "description": "Bilby's DJ mode for his library on the house stereo: 'DJ mode on' / 'DJ mode off'. On: each track mixes into the "
                                       "next on the beat (tempo matched, on a phrase) and a shuffle is ordered like a DJ set; off: one ends, the next starts.",
     "input_schema": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}},
    {"name": "spotify", "description": "The Captain's Spotify (Premium), played on the house radio (its speakers where it plays now). Only when he says Spotify. "
                                       "play: an artist, a song, an album, one of his playlists ('my road trip playlist') or 'liked songs'; shuffle=true for "
                                       "'shuffle ...'; queue=true for 'after this' / 'queue ...'. add: add the song playing now to one of his Spotify playlists "
                                       "('add this song to my Spotify playlist Gym'). like: save the song playing now to his Spotify liked songs.",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["play", "add", "like"]},
                                                       "what": {"type": "string", "description": "play: e.g. 'Daft Punk', 'my workout playlist', 'liked songs'"},
                                                       "playlist": {"type": "string", "description": "add: the playlist's name, in his words"},
                                                       "shuffle": {"type": "boolean"}, "queue": {"type": "boolean"}},
                      "required": ["action"]}},
    *BOOK_TOOLS,                                                # his audiobooks: an item of the same queue, so "stop" and "pause" are the music tool's
]


ANNOUNCE_TOOL ={"name": "announce", "description": "Say something out loud elsewhere in the house, in your voice, like an intercom: 'announce dinner "
                                                  "is ready', 'tell upstairs I'm leaving in five minutes', 'call everyone down for the movie'. "
                                                  "text: the words to say there, in plain spoken words (his message, not a reply to him).",
                 "input_schema": {"type": "object", "properties": {
                     "text": {"type": "string"},
                     "place": {"type": "string", "description": "everywhere (default), upstairs, downstairs, or a room in his words"}},
                     "required": ["text"]}}


def announce_tools(room_of, connected: dict, env: dict, loop):
    """(tools, run) for the `announce` tool: the words in Skippy's voice on the speakers of the place named (the router's outputs there:
    the Enclave upstairs, the Sony downstairs) and on the other rooms' pods there; never back in the room that asked, which hears the reply."""
    def run(name: str, args: dict) -> str:
        import house_layout                                     # noqa: PLC0415
        import speaker_out as speaker_mod                       # noqa: PLC0415
        text = str((args or {}).get("text") or "").strip()
        place = str((args or {}).get("place") or "everywhere").strip() or "everywhere"
        if not text:
            return "Not announced: say what to announce."
        here = room_of()
        lay = house_layout.default()
        rooms, _ = lay.split(place)
        kinds = speaker_mod.place_kinds(env, place)
        if rooms is None and kinds is None:
            return f"I don't know the place {place!r}: everywhere, upstairs, downstairs, or a room."
        mine = set(speaker_mod.room_kinds(env, here.name))
        kinds = [k for k in (kinds or []) if k not in mine and k != "pod"]
        pods = [r for n, r in connected.items() if r is not here and (rooms is None or lay.room_of_pod(n) in rooms)]
        if not kinds and not pods:
            return f"Not announced: there is no speaker {place} but this one."

        async def go() -> str:
            import router as router_mod                         # noqa: PLC0415
            voice = await here._voice(text)
            if voice is None:
                return "Not announced: I have no voice right now."
            heard = []
            # A box whose room's stereo is on stays silent and the stereo says it for it: the box's own speaker is the last resort (the Captain, 2026-11-04).
            stereo, loud, standing_in = list(kinds), [], []
            for r in pods:
                try:
                    outs = list(r.stereo_kinds()) if hasattr(r, "stereo_kinds") else []     # even a stereo the asking room shares: it just hears it too
                except Exception:                               # noqa: BLE001 — a failed look at the stereo must never keep the box from speaking
                    outs = []
                if outs and voice[0] == "mp3":
                    stereo += [k for k in outs if k not in stereo]
                    standing_in.append(r)
                else:
                    loud.append(r)
            if stereo and voice[0] == "mp3":
                out = speaker_mod.RoutedOut(router_mod.shared(env), stereo, log=here.log, duck_others=False)
                if any(out.router.available(k) for k in stereo):
                    async def by_stereo(out=out, boxes=tuple(standing_in)) -> None:
                        if not await out.play(voice[1]):        # the router dropped it (never played late) or it would not decode: the boxes it stood in for say it
                            for box in boxes:
                                await box.link.announce(text, voice)
                    asyncio.ensure_future(by_stereo())
                    heard.append(lay.place_words(stereo) or place)
                else:                                           # the stereo went off since the look: those boxes speak after all
                    loud += standing_in
            for r in loud:
                asyncio.ensure_future(r.link.announce(text, voice))
                heard.append(r.name)
            here.log(f"[{here.name}] announced {place}: {text}")
            return (f"Announced {' and '.join(heard)}: {text}" if heard
                    else f"Not announced: the speakers {place} are off right now.")
        try:
            return asyncio.run_coroutine_threadsafe(go(), loop).result(60)
        except Exception as e:                                  # noqa: BLE001 — said aloud, not raised
            return f"The announcement did not go out ({type(e).__name__})."
    return [ANNOUNCE_TOOL], run


def where_said(house) -> str:
    import station                                              # noqa: PLC0415
    return station.where_said(house)


def not_there(house) -> str:
    import station                                              # noqa: PLC0415
    return station.not_there(house)


PLAYS = {"play_music", "spotify", "audiobook"}                  # the tools that start the station


def music_tools(room_of, loop, favorites=None, stereo=None):
    """(tools, run) for Brain(extra=...): the hub is the house radio's REMOTE (radio.py, the Captain 2026-11-01: "The hubs can change it, or
    add to it, but they are not the players"). Every music tool, the audiobook's too (a book is an item of the same one stream: "pause" is the
    music tool's whatever plays), goes to the radio as it is (`stereo()`: radio_client.RadioClient), with the asking room. The hub keeps only
    where Skippy's VOICE comes from. `favorites` is unused: the radio marks and drops songs itself."""
    def run(name: str, args: dict) -> str:
        args = dict(args or {})
        room = room_of()
        radio = stereo() if stereo is not None else None
        if name == "speakers" and args.get("what") == "voice":
            import speaker_out as speaker_mod                   # noqa: PLC0415
            env = getattr(room, "env", None) or {}
            kinds = speaker_mod.place_kinds(env, str(args.get("place") or ""))
            if not kinds:
                return "I don't know that place: everywhere, downstairs or upstairs."
            outs = [o for o in getattr(room, "pc_outs", []) if hasattr(o, "router")]
            if not outs:
                return "This room's voice only comes from its pod."
            outs[0].kinds = set(kinds)
            outs[0].kind = "+".join(kinds)
            open_ = {k for k in kinds if outs[0].router.available(k)}
            if not open_:
                return f"{speaker_mod.place_words(env, kinds).capitalize()} isn't on: my voice stays on the pod until it is."
            return f"Done: my voice now comes from {speaker_mod.place_words(env, open_)}."

        if radio is None:
            return "There is no house radio here."
        if not hasattr(radio, "tool"):                          # a station in this process (the tests): run it here, the way radio.py would
            import radio_client                                 # noqa: PLC0415
            radio = radio_client.LocalRadio(radio, loop, favorites)
        action = str(args.get("action") or "play")
        starts = (name in PLAYS and not args.get("queue") and action == "play") or (name in ("music", "audiobook") and action == "resume")
        said = radio.tool(name, args, getattr(room, "name", ""))
        if starts:
            import houseaudio                                   # noqa: PLC0415
            houseaudio.note_start("his own request" if name in PLAYS else "his resume", ok=houseaudio.start_ok(said))   # the chat can say why the music is on: for a start that started something (the radio away, or "Nothing in his library matches ...", left the note of the one before)
        return said
    return MUSIC_TOOLS, run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--say", nargs=2, metavar=("ROOM", "TEXT"), help="say this in one room, then quit")
    ap.add_argument("--type", nargs=2, metavar=("ROOM", "TEXT"), help="hand words to the RUNNING hub, as if typed in the Home chat (the brain answers aloud there)")
    ap.add_argument("--record", nargs="+", metavar=("KIND", "COUNT"), help="record wake-word clips through the pod's microphone: positive|negative [COUNT [ROOM]] (stop the running hub first; wakerec.py)")
    args = ap.parse_args(argv)
    if args.type:
        box = Path(__file__).resolve().parents[2] / "state" / "hub-inbox" / f"{args.type[0]}.json"
        box.parent.mkdir(parents=True, exist_ok=True)
        box.write_text(json.dumps({"text": args.type[1], "at": time.time()}), encoding="utf-8")
        print(f"handed to the hub: {box}")
        return 0
    for stream in (sys.stdout, sys.stderr):                     # a title with a Unicode hyphen must not kill a callback on Windows' cp1252 log
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from ear import load_env                                    # noqa: PLC0415
    env = load_env()
    env["CAMS_OWNER"] = "1"                                     # the hub is the one that listens to the cameras and says what they see (cams.py)
    env["SKIPPY_GENERAC_OWNER"] = "1"                           # ... and the one polling genmon for the generator (generac.py)
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
    hub.make_room.connect = connect                             # the pods check in as Connect remotes of the house radio (esphome_link.serve)
    try:
        if args.say:
            return await _say_once(hub, rooms, *args.say)
        if args.record:
            return await _record(hub, rooms, env, *args.record)
        log("hub: rooms " + ", ".join(f"{n} ({k} {h}:{p})" for n, k, h, p in rooms))
        homechat = getattr(hub.make_room, "homechat", None)
        watch = getattr(hub.make_room, "garage_watch", None)
        clock = getattr(hub.make_room, "alarm_clock", None)
        cams_watch = getattr(hub.make_room, "cams_watch", None)
        generac_watch = getattr(hub.make_room, "generac_watch", None)
        beat = heartbeat(hub, Path(hub.make_room.state) / "hub-status.json", getattr(hub.make_room, "house", None))             if getattr(hub.make_room, "state", None) else None
        await asyncio.gather(hub.run(), *([beat] if beat is not None else []), *([connect.run()] if connect else []), *([homechat.run(hub)] if homechat is not None else []),
                             *([watch.run(hub, hub.make_room.routine_room)] if watch is not None else []),
                             *([clock.run(hub)] if clock is not None else []),
                             *([cams_watch.run(hub, hub.make_room.routine_room)] if cams_watch is not None else []),
                             *([generac_watch.run(hub, hub.make_room.routine_room)] if generac_watch is not None else []))
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
