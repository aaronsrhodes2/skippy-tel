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

Snappy like an Echo (2026-11-05): one SpeedKeeper (below) keeps Skippy Core's prompt cache warm (Brain.warm: at the hub's start and after 240 s of nobody using a brain; not
when the name is heard: the review measured that warm as worth nothing hot and as costly cold) and renders the brain's fixed phrases ahead into a cache that Room.say reads first (no TTS call for a template reply); every turn's timing line
(TurnTiming) says where its time went. SATELLITE_SPEED=off in the vault switches both off.

    python hub.py                    rooms from SATELLITE_ROOMS, e.g. "kitchen=esphome://skippy-kitchen.local,den=192.168.1.41"
    python hub.py --say kitchen "Dinner is ready, monkeys."      say something in one room, then quit
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
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
HOT_FILLER_S = 4.0                                              # the filler quip waits at least this long while Skippy Core's prompt is believed hot (SpeedKeeper.core_state): the model then answers in 1.5-4.4 s (median 2.1 s), and a quip of about 2 s in front of that only delays the answer
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


async def render_clip(piece, said: str):
    """("mp3", bytes): `said` in Skippy's own voice, as one clip. `piece` makes one piece of it (an async callable: Room._tts_piece for a reply, which covers a busy studio
    with a quip, or a plain gate call for a render ahead, which never speaks). The gate speaks at most 500 characters at a time: a longer text is made in pieces, one after
    another (the voice studio speaks one line at a time and refuses a second, 2026-10-31), and joined into one clean stream. Raises what the pieces raise."""
    parts = split_for_voice(said)
    clips = [await piece(t) for t in parts]
    return ("mp3", await asyncio.to_thread(one_stream, b"".join(clips)))


class TurnTiming:
    """Where the time of one spoken turn went, from the end of his voice to Skippy's voice being ready (the Captain, 2026-11-05: "streamline it so it becomes snappy
    like Amazon echo"; measured that day, heard -> said had a median of 8 s and a p90 of 24 s): wait (the silence the voice detector waited to be sure he had finished),
    hear (the voice gate's Whisper), think (the brain, with its own split when it keeps one: brain.timing = {"route", "model_s": [...], "tools_s", "total_s"}) and voice
    (the reply turned into audio). One log line per turn and a `timing` in the house log's command record. A typed turn has no wait and no hear. Nothing here raises: a
    number that cannot be read is left out.

    What the keeper (SpeedKeeper) and the brain add when they have something to say, and nothing when they do not (so a room without one logs what it always did):
    `core` (hub: Skippy Core's prompt cache as the thinking began, "hot" / "warming" / "cold"; shown as `prompt hot`), `cached` (hub: the voice was a clip rendered ahead, no
    TTS call; `voice 0.0s cached`), `template` (the reply was one of the brain's fixed phrases: the hub's guess from the text, or the brain's own word for it), and whatever else
    the brain's split holds besides route, model_s, tools_s and total_s (a prompt-cache hit, tokens read from the cache, an ack's name ...): short names with a yes/no, a
    number or a few characters, shown as they are (`warm hit`, `no template`, `cache_read 5125`) and kept flat in the numbers. The brain's side can add keys without the hub
    knowing them."""

    BRAIN_KEYS = ("route", "model_s", "tools_s", "total_s")     # what the brain's split has always held

    def __init__(self, heard: dict | None = None, sink=None):
        heard = heard if isinstance(heard, dict) else {}
        self.wait, self.hear = heard.get("wait"), heard.get("hear")
        self.think = self.voice = None
        self.brain = None
        self.core = None                                        # SpeedKeeper.core_state() as the thinking began; None in a room without a keeper
        self.cached = False                                     # the voice came from the clips rendered ahead
        self.template = False                                   # the reply is one of the brain's fixed phrases
        self.sink, self._done = sink, False                     # sink(self) is called once: when the voice is ready, or when the turn ended without one

    def thought(self, seconds: float, brain=None) -> None:
        self.think, self.brain = seconds, brain

    def voiced(self, seconds: float, cached: bool = False) -> None:
        self.voice, self.cached = seconds, bool(cached)
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

    def _extras(self) -> list[tuple[str, object]]:
        """What else the brain recorded about its reply, whatever it calls it: names of lower case letters, digits and underscores with a yes/no, a finite number or up to 24
        printable characters for a value (anything else is not for a log line), at most six."""
        b = self.brain if isinstance(self.brain, dict) else {}
        out = []
        for key, value in list(b.items()):                      # (a copy: the brain's thread may still be adding to its record)
            if key in self.BRAIN_KEYS or not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", key):
                continue
            if isinstance(value, (bool, int)) or (isinstance(value, float) and math.isfinite(value)) or (isinstance(value, str) and 0 < len(value) <= 24 and value.isprintable()):
                out.append((key, round(value, 1) if isinstance(value, float) else value))
        return out[:6]

    @staticmethod
    def _extra_text(key: str, value) -> str:
        if value is True:
            return key.replace("_", " ")                        # warm_hit: True -> "warm hit"
        if value is False:
            return "no " + key.replace("_", " ")                # template: False -> "no template"
        return f"{key} {value}"                                 # cache_read: 5125 -> "cache_read 5125"

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
            for key, value in self._extras():
                out.setdefault(key, value)
        except Exception:                                       # noqa: BLE001 — the brain's own numbers are a bonus
            pass
        if self.core:
            out["core"] = str(self.core)
        if self.cached:
            out["cached"] = True
        if self.template:
            out.setdefault("template", True)                    # (the brain's own word for it, when it gave one, stays)
        return out

    def _inner(self) -> list[str]:
        """The bracket after the thinking time: the brain's own split, what else it recorded, then the prompt cache's state and a template reply (the hub's own facts)."""
        inner, extras = [], []
        try:
            if self.brain:
                route, model, tools = self._split()
                inner.append(f"route {route if route else 'none'}")
                if model:
                    inner.append("model " + "+".join(f"{x:.1f}" for x in model) + "s")
                if tools is not None:
                    inner.append(f"tools {float(tools):.1f}s")
            extras = self._extras()
            inner += [self._extra_text(k, v) for k, v in extras]
        except Exception:                                       # noqa: BLE001
            pass
        if self.core:
            inner.append(f"prompt {self.core}")
        if self.template and not any(k == "template" for k, _ in extras):
            inner.append("template")
        return inner

    def line(self) -> str:
        """(timing: wait 0.7s, hear 0.9s, think 3.4s [route none; model 2.1+1.3s; tools 0.2s], voice 1.1s = 6.1s); with a keeper: (timing: ..., think 1.0s [route none;
        model 0.6s; tools 0.0s; prompt hot; template], voice 0.0s cached = 2.6s)"""
        bits = []
        for name, v in self._parts():
            text = f"{name} {v:.1f}s"
            if name == "think":
                inner = self._inner()
                if inner:
                    text += " [" + "; ".join(inner) + "]"
            elif name == "voice" and self.cached:
                text += " cached"
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
        self.pod_voice = True                                   # SATELLITE_POD_VOICE=1 gives the box's own speaker Skippy's voice back; otherwise it only rings alarms and timers
        self._alarm_sound = False                               # True while a timer or alarm announces itself: that one may sound from the box
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
        self.speed = None                                       # SpeedKeeper (shared): Skippy Core's prompt cache kept warm, the fixed phrases rendered ahead (attach_speed)
        self.clips = None                                       # ... and those clips (speed.clips): _voice reads them first, so a template reply makes no TTS call
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
        if self.mode in (LISTENING, FOLLOWUP) and self.buf is not None:      # his voice's loudness to the face: the pencil writes as fast as he talks
            show = getattr(self.link, "level", None)
            if show is not None:
                show(level)
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
        self._window_face(self.listen_ms)
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
        speed = self.speed
        if speed is not None:
            turn.core = speed.core_state(self.brain)            # Skippy Core's prompt cache as the thinking begins: the start-up warm, the keep-warm loop, the last turn
            speed.turn_started(self.brain)                      # no warm runs while this is being thought: it would only compete with the real call
        try:
            work = asyncio.ensure_future(asyncio.to_thread(self.brain.reply, command))
            await self._cover_the_wait(work, command)
            reply = await work
        except Exception as e:                                  # noqa: BLE001 — whatever the API did, he gets an answer
            self.log(f"[{self.name}] (brain failed: {type(e).__name__})")
            outcome = "brain_failed"
            reply = "My brain is not answering. Typical."
        finally:
            if speed is not None:
                speed.turn_ended(self.brain, ok=outcome == "ok", model=self._asked_model())     # (a failed turn: Core is taken for away for a minute, and nothing warms it meanwhile)
        record = getattr(self.brain, "timing", None) if outcome == "ok" else None             # (a failed reply has no split of its own: the last one's is not its)
        turn.thought(time.monotonic() - thought, record)
        turn.template = speed is not None and self._template_reply(reply, record)
        self.log(f"[{self.name}] said: {reply[:120]}")
        calls = list(getattr(self.brain, "actions", None) or [])
        seconds = round(time.time() - started, 1)
        guard = " ".join(x for x in (getattr(self.brain, "guard", None) or ()) if x) or None      # claim_reasked etc.: the count of claims needs no log archaeology

        def note(t: TurnTiming) -> None:
            """The command's record (with the timing) and the timing line: written when the reply's voice is ready, a moment after the reply, so the timing is whole."""
            try:
                if self.hlog is not None:
                    self._record("command", words=command, tools=[c["tool"] for c in calls], outcome=outcome, reply=reply, seconds=seconds, guard=guard, timing=t.numbers(),
                                 calls=[{"tool": c["tool"], "args": c.get("args") or {}} for c in calls[:4]])        # (the calls' arguments: aliases.py learns a phrase that worked from them)
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

    def _template_reply(self, reply: str, record) -> bool:
        """Is this reply a closed-bank confirmation (the timing line says `template`)? The brain knows: when it recorded its own split of the reply (a model_s list), its word is taken, `template` or not.
        A "Done, Captain." the MODEL worded is a model reply that happens to equal a phrase of the bank, and it is exactly the kind of reply the log is read for (the review of 2026-11-05: the false
        acknowledgements, hidden when the hub judged by the text). A brain that records nothing, or a turn that failed (`record` is None: the brain's record is the last reply's), is judged by the
        phrase bank, as before."""
        if isinstance(record, dict) and isinstance(record.get("model_s"), list):
            return bool(record.get("template"))
        return self.speed is not None and self.speed.is_fixed(reply)

    def _asked_model(self) -> bool:
        """Did the reply just made ask the model (the brain's own record of it)? A confirmation the brain worded from a phrase of its own never did, so it neither warmed nor
        proved anything about Skippy Core's cache. True when that cannot be told: a brain that keeps no such record is taken to have asked."""
        try:
            record = getattr(self.brain, "timing", None)
            return bool(record["model_s"]) if isinstance(record, dict) and "model_s" in record else True
        except Exception:                                       # noqa: BLE001
            return True

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

    def _prompt_is_hot(self) -> bool:
        """Does the keeper believe Skippy Core's prompt is hot for this room's brain? False when that cannot be told: no keeper, a warm running (the answer may wait for it), or a keeper that fails."""
        try:
            return self.speed is not None and self.speed.core_state(self.brain) == "hot"
        except Exception:                                       # noqa: BLE001 — a belief about Core's cache must never cost a quip or a turn
            return False

    async def _cover_the_wait(self, work, command: str) -> None:
        """While the answer is worked out, say a pre-recorded quip as soon as it is clearly not going to be instant (never for a quick home command), and one more
        if it is still not ready after the first: Skippy answers at once, and the real answer follows.

        What "clearly not going to be instant" is depends on Core's prompt (the review of 2026-11-05). On a cold prompt a model turn takes 15-20 s, and a quip at 1 s fills dead air. On a hot one it
        takes 1.5-4.4 s (median 2.1 s), and a quip (the 207 recorded ones last 0.5-7.8 s, median 2.1 s) in front of that holds the voice lock and puts the answer behind itself. So while the keeper
        believes the prompt hot, the FIRST quip waits max(filler_after_s, HOT_FILLER_S) (a household command still gets its longer factor); a cold or unknown prompt (no keeper, a warm running, a
        keeper that cannot say) keeps the delays it always had. After the first quip the wait goes on the same way whatever the belief: 5 s more, and the second quip if the answer is still not
        there. (A belief that the prompt is hot goes stale: another client of Core's chat slot evicts the cache after the keeper last saw it hot, and that turn takes 20 s. With the second quip
        dropped for a hot turn it got one quip at 4 s and then silence, where a cold one got a quip at 1 s and another at 6 s. A genuinely hot turn, 1.5-4.4 s, is over before any quip.)"""
        if self.filler is None or self.filler_after_s <= 0:
            return
        hot = self._prompt_is_hot()
        kind = self.filler.kind_for(command)
        after = max(self.filler_after_s, HOT_FILLER_S) if hot else self.filler_after_s
        wait = after * (2.2 if kind == "home" else 1.0)         # a quick household command is usually answered at once: give it longer
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
            done, _ = await asyncio.wait({work}, timeout=5.0)         # (a hot turn too: its second quip comes if the answer is still not there, see above)

    def _resume(self, mode: str, window_ms: int = 0) -> None:
        self._fresh()
        self.mode, self.deadline = mode, self.t + window_ms
        if mode in (LISTENING, FOLLOWUP):
            self._window_opened()
            self._window_face(window_ms)
        if mode == LISTENING:
            self.vad.arm(self._last_voice)                      # only the name was heard so far: the command still starts at the lower bar (not after a reply: its own echo)
        self._session(mode == FOLLOWUP)

    def _window_face(self, ms: int) -> None:
        """Tell the face how long this window is: its teal fades to purple across it (the Captain, 2026-11-11)."""
        show = getattr(self.link, "window", None)
        if show is not None and ms > 0:
            show(ms)

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
            ahead = (voice is None or said != text) and self.clips is not None and said in self.clips     # a fixed phrase, rendered ahead: no TTS call
            voice = voice if voice is not None and said == text else await self._voice(said)    # a clip made already (a starter) is used as it is
            if timing is not None:
                timing.voiced(time.monotonic() - made, cached=ahead)
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
        quiet_pod = voice is not None and not self.pod_voice and not self._alarm_sound     # the pod's own speaker is for alarms and timers only (the Captain, 2026-11-12)
        if sole is None:
            self._also(voice)
            if quiet_pod:
                voice = None                                    # the words and the lips, no sound from the box: the stereos carry the voice
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
            if quiet_pod:
                self.log(f"[{self.name}] (the {sole.kind} output dropped it; the pod's speaker is off for voice)")
            else:
                self.log(f"[{self.name}] (the {sole.kind} output dropped it: the pod says it)")
                await self.link.announce(said, voice)
        elif getattr(sole, "last_heard", True) is False and not quiet_pod:   # a probe of an output taken for off (sync.py), and still nobody heard it
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
        """("mp3", bytes) from Skippy's own voice, ("pcm", bytes, rate) from the machine's, or None when neither answers. A fixed phrase that was rendered ahead
        (SpeedKeeper, the clips) is served from memory: no call to the voice gate at all, and nothing to wait for."""
        ready = self.clips.get(said) if self.clips is not None else None
        if ready is not None:
            return ready
        try:
            return await render_clip(self._tts_piece, said)
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
                    if voice is not None and self.pod_voice:    # the box's own speaker is for the chime alone (the Captain, 2026-11-12): the words are on its screen and the stereos say them
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
            self._alarm_sound = True
            try:
                await self.announce(f"Your {timer_name(timer.label)} timer is done.")
            finally:
                self._alarm_sound = False
        if self.alarms is not None and self.clock is None:
            for alarm in self.alarms.due():
                self.log(f"[{self.name}] {alarm.kind} going off: {alarm.hhmm} {alarm.label}")
                self._record("alarm", what=alarm.kind, label=alarm.label or None)
                self._alarm_sound = True
                try:
                    await self.announce(self.alarms.spoken(alarm))
                finally:
                    self._alarm_sound = False
                self._listen_for_answer()
            again = self.alarms.ring_again()                    # nobody answered: once a minute, a few times
            if again is not None:
                self._alarm_sound = True
                try:
                    await self.announce(self.alarms.spoken(again))
                finally:
                    self._alarm_sound = False
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


# ── snappy like an Echo (the Captain, 2026-11-05: "streamline it so it becomes snappy like Amazon Echo") ──
# Measured that day: Skippy Core's chat model (a hybrid, llama.cpp: it reuses a cached prefix only from a checkpoint a little before the end of the last prompt) re-reads the whole
# 5.6k-token house prompt, 4-6 s (6-8 s with the tools), whenever the text before that checkpoint changed, and answers in 0.6 s when the prefix is warm; and every spoken
# reply costs the voice studio 1.4-2 s unless that exact text was rendered before. The SpeedKeeper is the hub's half of the fix (the brain's half: what comes first in its
# prompt, and Brain.warm / fixed_phrases): it keeps the prompt warm and renders the brain's fixed phrases ahead of time. Every part is a favour to a turn that may never come:
# nothing here is awaited on a turn's way, nothing raises into one, and a house that is busy gets left alone.
WARM_START_TRIES = 10                                           # (a) the first warm, at hub start: this many tries, ...
WARM_START_BACKOFF_S = (5, 60)                                  # ... 5 s apart at first, doubling up to a minute
# (b), a warm when the name is heard, was built and then taken out again (review of 2026-11-05, P5): see the SpeedKeeper's docstring for what it was worth
WARM_IDLE_S = 240                                              # (c) a brain nobody used or warmed for this long is warmed (other clients of Core's chat slot evict the prefix)
WARM_CHECK_S = 15                                               # (c) how often that is looked at
WARM_AWAY_S = 60                                                # a warm that failed or a turn that failed: Core is taken for away this long, and nothing warms it meanwhile
WARM_HOT_S = 300                                                # a warm or a turn that succeeded this recently: the prompt is probably still hot (TurnTiming's `prompt hot`)
WARM_LOG_S = 60                                                 # the warm's and the renders' trouble is logged once a minute at most
RENDER_AFTER_S = 60                                             # (H2) the fixed phrases are rendered after the first warm worked, or this long after the start anyway
RENDER_GAP_S = 3.0                                              # ... one at a time, this long apart
RENDER_QUIET_S = 8                                              # ... and only when no room has heard a voice for this long
RENDER_AGAIN_S = 900                                            # ... a pass renders what is missing; the next is this much later (a gate that was down has come back)
RENDER_RETRY_S = 300                                            # ... or this much after a pass that had failures
RENDER_SPENT_S = 3600                                           # ... or this much after the voice's budget for the day ran out
RENDER_FAILS = 3                                                # this many failures in a row end a pass (a gate that is down is not asked over and over)
HUB_FIXED = ("My brain is not answering. Typical.",             # what the hub itself says without the brain (Room.handle when the model is away) ...
             "Okay, no problem, Captain.")                      # ... and when a no is answered and there is no recorded quip (Room._decline)


class ClipCache:
    """Skippy's voice for text that was rendered ahead: text -> ("mp3", bytes), one cache for every room. Only whole clips of the gate's own voice go in (never the machine's
    own), and only the SpeedKeeper puts them there, so a reply that is not a fixed phrase is never kept. The text is matched as the voice gate matches it: without the
    whitespace around it."""

    def __init__(self, limit: int = 256):
        self._clips: dict[str, tuple] = {}
        self.limit = limit

    @staticmethod
    def _key(text):
        return text.strip() if isinstance(text, str) else text

    def get(self, text: str):
        return self._clips.get(self._key(text))

    def put(self, text: str, clip: tuple) -> None:
        key = self._key(text)
        if key not in self._clips and len(self._clips) >= self.limit:
            self._clips.pop(next(iter(self._clips)))            # the oldest
        self._clips[key] = clip

    def clear(self) -> None:
        self._clips.clear()

    def __contains__(self, text) -> bool:
        return self._key(text) in self._clips

    def __len__(self) -> int:
        return len(self._clips)


def fixed_phrases_of(brain=None, module=None) -> list[str]:
    """Every fixed phrase the brain can say, each once: what a brain lists itself (fixed_phrases() and ACKS), then what the brain's module lists (the same two, where the phrases
    do not depend on a brain). Whatever is not a non-empty string is ignored, and a list that fails costs the others nothing."""
    out: list[str] = []
    for owner in (brain, module):
        if owner is None:
            continue
        lists = []
        listing = getattr(owner, "fixed_phrases", None)
        if callable(listing):
            try:
                lists.append(listing())
            except Exception:                                   # noqa: BLE001 — a favour: the other lists still count
                pass
        lists.append(getattr(owner, "ACKS", None))
        for items in lists:
            try:
                phrases = list(items or ())
            except TypeError:
                continue
            for p in phrases:
                if isinstance(p, str) and p.strip() and p.strip() not in out:
                    out.append(p.strip())
    return out


class SpeedKeeper:
    """The hub's half of "snappy like an Echo", one for the whole hub (build_rooms makes it, `_main` runs it, every room is attached to it: attach_speed).

    H1, Skippy Core's prompt cache kept warm (Brain.warm(): one token asked of the real house prompt, run in a thread): (a) once at the hub's start, tried again with a backoff
    (5 s doubling to a minute, ten tries) until it works; (c) every 15 s a brain that nobody has used or warmed for 240 s. A warm never starts while a turn is being thought (Room.handle
    brackets it with turn_started / turn_ended), while another warm of any room runs, or when Core is known to be away (a warm that failed, or a turn that did: for a minute), it
    is never on a turn's way (the loops are their own tasks, nothing waits for them), and what goes wrong in one is logged once a minute at most.

    There is no (b), a warm when the name is heard (built first, taken out again after the review of 2026-11-05, P5). Measured on Core: with the prompt hot the warm gains almost
    nothing now that the clock and the room ride in the newest message instead of the end of the system prompt (the real Brain, 15 turns each way: a median turn of 2.23 s without
    the warm and 2.16 s with it, 181 fresh tokens against 55; builder 1's probe of the old layout, 3 runs: a median 1.30 s against 1.10 s), and with the prompt cold, or evicted since
    the keeper last saw it hot (a 300 s belief; other clients of Core's chat slot evict the prefix), it makes the turn twice as slow: a cold 10.5k-token prefix took a turn alone
    20.7 s and 21.0 s, and with a warm started 3 s before it 40.0 s and 37.7 s, because llama.cpp does not share a prefill that is in flight. The start-up warm and the 240 s loop
    keep the prefix hot; what a turn re-reads (the note and his words) is a few hundred tokens.

    H2, the brain's fixed phrases (its acks and the like) rendered ahead into `clips`, which Room._voice reads first: the first time a template reply is said it is served from
    memory, with no call to the voice gate at all. One phrase at a time, RENDER_GAP_S apart, only when no room is speaking, thinking, listening or has just heard a voice; a
    render that fails is skipped (never retried in the moment, never covered with a quip, never the machine's own voice), RENDER_FAILS in a row end the pass, a spent
    voice budget ends it for the day.

    `clock` and `sleep` are for the tests. The brain's side is looked up with getattr (warm, fixed_phrases, ACKS), so a brain without them is simply left alone."""

    def __init__(self, gate, log=print, clock=time.monotonic, sleep=asyncio.sleep, clips: ClipCache | None = None, brain_module=None, own_phrases=HUB_FIXED):
        self.gate, self.log, self.clock, self.sleep = gate, log, clock, sleep
        self.clips = clips if clips is not None else ClipCache()
        self.module, self.own = brain_module, tuple(own_phrases)
        self.started = False                                    # (a) done: the first warm worked, or there is nothing to warm
        self.touched: dict[int, float] = {}                     # id(brain) -> when a warm of it began or a turn of it ended
        self.hot_at: dict[int, float] = {}                      # id(brain) -> when a warm or a turn of it last worked
        self.thinking: dict[int, int] = {}                      # id(brain) -> the turns being thought now
        self.inflight: set[int] = set()                         # id(brain) -> a warm is running
        self.away_until = 0.0                                   # Core is taken for away until then
        self._complained, self._announced = -math.inf, False
        self._fixed: set[str] = set()                           # every fixed phrase seen (is_fixed): the template replies

    # ── what the rooms tell it ──
    def core_state(self, brain) -> str:
        """Skippy Core's prompt cache for this brain, as far as the hub can tell: "warming" (a warm is running), "hot" (a warm or a turn worked in the last WARM_HOT_S) or "cold"."""
        key = id(brain)
        if key in self.inflight:
            return "warming"
        at = self.hot_at.get(key)
        return "hot" if at is not None and self.clock() - at < WARM_HOT_S else "cold"

    def turn_started(self, brain) -> None:
        key = id(brain)
        self.thinking[key] = self.thinking.get(key, 0) + 1

    def turn_ended(self, brain, ok: bool = True, model: bool = True) -> None:
        """A turn of this brain is over (Room.handle, in a finally). If it asked the model it touched the prompt, so no warm is due for a while, and it proved Core is there; one
        that never did (a confirmation from the brain's own phrases) changes nothing about either. If it failed, Core is taken for away for a minute."""
        key, now = id(brain), self.clock()
        left = self.thinking.get(key, 0) - 1
        if left > 0:
            self.thinking[key] = left
        else:
            self.thinking.pop(key, None)
        if not ok:
            self.touched[key] = now
            self.hot_at.pop(key, None)
            self.away_until = now + WARM_AWAY_S
        elif model:
            self.touched[key] = self.hot_at[key] = now
            self.away_until = 0.0

    def is_fixed(self, text) -> bool:
        """Is this one of the brain's fixed phrases (a template reply)? Known once the phrases have been looked up (startup, every render pass)."""
        return isinstance(text, str) and (text.strip() in self._fixed or text in self.clips)

    # ── H1: the warm ──
    def brains(self, hub) -> list:
        """The distinct brains of the rooms that are connected, in the order of the rooms."""
        seen, out = set(), []
        for room in list(getattr(hub, "rooms", {}).values()):
            brain = getattr(room, "brain", None)
            if brain is not None and id(brain) not in seen:
                seen.add(id(brain))
                out.append(brain)
        return out

    def _may_warm(self, brain, gap: float) -> bool:
        """A warm of this brain would be welcome now: it can warm, no warm is running (one at a time: two prefills of the same prompt side by side only slow each other),
        no turn is being thought anywhere, Core is not known to be away, and the prompt was not touched (warmed, or used by a turn) in the last `gap` seconds."""
        if not callable(getattr(brain, "warm", None)) or self.inflight or self.thinking:
            return False
        now = self.clock()
        return now >= self.away_until and now - self.touched.get(id(brain), -math.inf) >= gap

    def _claim(self, brain) -> bool:
        key = id(brain)
        if key in self.inflight:
            return False
        self.inflight.add(key)
        self.touched[key] = self.clock()
        return True

    def _complain(self, text: str) -> None:
        """One line a minute at most: a warm that fails every 15 s while Core is down must not fill the log."""
        now = self.clock()
        if now - self._complained < WARM_LOG_S:
            return
        self._complained = now
        try:
            self.log(f"(speed: {text})")
        except Exception:                                       # noqa: BLE001
            pass

    async def warm(self, brain, why: str = "") -> float | None:
        """Warm Skippy Core's prompt cache for this brain: brain.warm() in a thread. -> the seconds it took, or None (Core did not answer, the brain cannot warm, a warm of it
        is running already). Never raises."""
        key = id(brain)
        warm = getattr(brain, "warm", None)
        if not callable(warm) or not self._claim(brain):
            return None
        error, took = None, None
        try:
            took = await asyncio.to_thread(warm)
        except Exception as e:                                  # noqa: BLE001 — Brain.warm never raises; another brain that does is logged, not raised
            error = e
        finally:
            self.inflight.discard(key)
        now = self.clock()
        if took is True or (isinstance(took, (int, float)) and not isinstance(took, bool) and math.isfinite(took) and took >= 0):
            self.hot_at[key], self.away_until, self.started = now, 0.0, True
            if not self._announced:
                self._announced = True
                self.log(f"(speed: Skippy Core's prompt is warm: {0.0 if took is True else float(took):.1f} s)")
            return 0.0 if took is True else float(took)
        self.hot_at.pop(key, None)
        self.away_until = now + WARM_AWAY_S
        self._complain(f"warming Skippy Core's prompt ({why or 'a room'}) failed: {type(error).__name__}: {error}" if error is not None
                       else f"Skippy Core did not answer the warm ({why or 'a room'}); not asking again for {WARM_AWAY_S} s")
        return None

    async def startup(self, hub) -> None:
        """(a) The first warm, as soon as a box has connected (the first room's brain: the prompts of the rooms differ only in where he is). Tried again with a backoff of 5 s
        doubling to 60 until it works, ten tries at most, and a brain that cannot warm is nothing to wait for. Every try keeps the gates every other warm keeps (_may_warm; the review of
        2026-11-05): no turn being thought, no warm of ANY room running (two prefills of the same prompt side by side only slow each other), and Core not taken for away. Waiting for a gate
        spends no try; a failed warm takes Core for away for WARM_AWAY_S, so a retry comes a minute after the failure at the soonest (the backoff is the least time between tries)."""
        tries, delay = 0, WARM_START_BACKOFF_S[0]
        while tries < WARM_START_TRIES and not self.started:
            brains = self.brains(hub)
            if not brains:
                await self.sleep(1.0)                           # no box has connected yet
                continue
            brain = brains[0]
            self.phrases(hub)                                   # (the template replies are known from here on)
            if not callable(getattr(brain, "warm", None)):
                self.started = True
                return
            if not self._may_warm(brain, 0):                    # a turn is being thought, a warm is running (any room's), or Core is known to be away
                await self.sleep(1.0)
                continue
            if await self.warm(brain, "start") is not None:
                return
            tries += 1
            if tries < WARM_START_TRIES:
                await self.sleep(delay)
                delay = min(WARM_START_BACKOFF_S[1], delay * 2)
        if not self.started:
            self.log(f"(speed: Skippy Core did not answer {WARM_START_TRIES} warms at the start; the {WARM_IDLE_S} s loop keeps trying)")

    async def keep_warm(self, hub) -> None:
        """(c) Every WARM_CHECK_S: one brain that nobody has used or warmed for WARM_IDLE_S (one never warmed at all, like a room that has just connected, counts) is
        warmed. One at a time, never mid-turn."""
        while True:
            await self.sleep(WARM_CHECK_S)
            due = next((b for b in self.brains(hub) if self._may_warm(b, WARM_IDLE_S)), None)
            if due is not None:
                await self.warm(due, "idle")
            if len(self.touched) > 64:
                self._prune()

    def _prune(self) -> None:
        """Brains of rooms that reconnected are gone: forget what was kept about the ones not touched for an hour."""
        old = self.clock() - 3600
        for kept in (self.touched, self.hot_at):
            for key in [k for k, t in kept.items() if t < old and k not in self.inflight and k not in self.thinking]:
                del kept[key]

    # ── H2: the phrases rendered ahead ──
    def _brain_module(self):
        if self.module is not None:
            return self.module
        try:
            import brain as brain_mod                           # noqa: PLC0415
            return brain_mod
        except Exception:                                       # noqa: BLE001
            return None

    def phrases(self, hub) -> list[str]:
        """Every fixed phrase to have rendered: what each connected brain lists, what the brain's module lists, then what the hub itself says; each once."""
        out: list[str] = []
        lists = [fixed_phrases_of(b) for b in self.brains(hub)] + [fixed_phrases_of(None, self._brain_module()), list(self.own)]
        for phrases in lists:
            for p in phrases:
                if isinstance(p, str) and p.strip() and p.strip() not in out:
                    out.append(p.strip())
        self._fixed.update(out)
        return out

    def quiet(self, hub) -> bool:
        """The house is quiet enough to spend the voice studio on a render: no turn being thought, and no room busy, listening, speaking, hearing a voice now or a moment ago,
        ringing an alarm, running a routine, recording, or still waiting out its own voice on a PC output."""
        if self.thinking:
            return False
        now = time.monotonic()                                  # (what the rooms stamp their own times with)
        for room in list(getattr(hub, "rooms", {}).values()):
            if getattr(room, "mode", IDLE) != IDLE or getattr(room, "buf", None) is not None or getattr(room, "recorder", None) is not None:
                return False
            lock = getattr(room, "voice", None)
            if lock is not None and lock.locked():
                return False
            heard = getattr(room, "_voice_at", None)
            if heard is not None and now - heard < RENDER_QUIET_S:
                return False
            if getattr(getattr(room, "clock", None), "ringing", None) is not None or getattr(getattr(room, "daily", None), "busy", False):
                return False
            if any(getattr(o, "talking_until", 0) > now for o in getattr(room, "pc_outs", ())):
                return False
        return True

    async def render(self, text: str) -> bool:
        """Render one phrase into the clips: the pieces and the joining of a reply (render_clip), but a plain gate call for each: no retry in the moment, no quip in a room (a
        studio busy with someone's line makes this render wait for the next pass), no machine voice. True when it is in the clips; raises BudgetSpent."""
        async def piece(t: str) -> bytes:
            return await asyncio.to_thread(self.gate.tts, t)
        try:
            clip = await render_clip(piece, text)
        except BudgetSpent:
            raise
        except Exception as e:                                  # noqa: BLE001 — a render is a favour: this one is skipped
            self._complain(f"could not render {text[:40]!r} ahead: {type(e).__name__}: {e}")
            return False
        self.clips.put(text, clip)
        return True

    async def prerender_pass(self, hub) -> tuple[int, int, bool]:
        """One pass over the phrases that are not rendered yet: (rendered, failed, the voice's budget is spent)."""
        rendered = failed = streak = 0
        last = -math.inf
        for text in self.phrases(hub):
            if text in self.clips:
                continue
            while not (self.clock() - last >= RENDER_GAP_S and self.quiet(hub)):
                await self.sleep(1.0)                           # one at a time, three seconds apart, in a quiet house
            try:
                done = await self.render(text)
            except BudgetSpent:
                self._complain("Skippy's voice has used today's budget: the fixed phrases wait for tomorrow")
                return rendered, failed, True
            last = self.clock()
            if done:
                rendered, streak = rendered + 1, 0
            else:
                failed, streak = failed + 1, streak + 1
                if streak >= RENDER_FAILS:
                    break                                       # the gate is down: not asked again and again
        return rendered, failed, False

    async def prerender(self, hub) -> None:
        """(H2) After the first warm worked (or a minute after the start, whichever is first), and then every RENDER_AGAIN_S: render what is missing."""
        began = self.clock()
        while not self.started and self.clock() - began < RENDER_AFTER_S:
            await self.sleep(1.0)
        while True:
            wait = RENDER_AGAIN_S
            try:
                rendered, failed, spent = await self.prerender_pass(hub)
                wait = RENDER_SPENT_S if spent else RENDER_RETRY_S if failed else RENDER_AGAIN_S
                if not self.brains(hub):
                    wait = min(wait, 30)                        # no box has connected yet: its brain's phrases are still to come
                if rendered:
                    self.log(f"(speed: {rendered} fixed phrase{'s' if rendered != 1 else ''} rendered ahead{f', {failed} could not be' if failed else ''}: {len(self.clips)} in all)")
            except Exception as e:                              # noqa: BLE001 — a favour: the next pass tries again
                self._complain(f"the render pass failed: {type(e).__name__}: {e}")
            await self.sleep(wait)

    # ── all of it ──
    async def _guarded(self, job, hub) -> None:
        while True:
            try:
                await job(hub)
                return
            except Exception as e:                              # noqa: BLE001 — a bug in a favour must not take the hub's other jobs down with it
                self._complain(f"{job.__name__} stopped: {type(e).__name__}: {e}")
            await self.sleep(60)

    async def run(self, hub) -> None:
        """The hub's three background jobs, until cancelled. Never raises."""
        await asyncio.gather(self._guarded(self.startup, hub), self._guarded(self.keep_warm, hub), self._guarded(self.prerender, hub))


def speed_from_env(env: dict, gate, log=print):
    """The hub's SpeedKeeper, or None when the vault says SATELLITE_SPEED=off (the way back to a hub that warms nothing and renders nothing ahead)."""
    if str(env.get("SATELLITE_SPEED", "on")).strip().lower() in ("off", "0", "no", "false"):
        return None
    return SpeedKeeper(gate, log=log)


def attach_speed(room, speed) -> None:
    """A room gets the hub's keeper and the clips it renders into (or, with no keeper, neither: the room is as it was)."""
    room.speed = speed
    room.clips = speed.clips if speed is not None else None


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
    speed = speed_from_env(env, gate, log=log)                   # Skippy Core's prompt kept warm and the brain's fixed phrases rendered ahead, for every room (SATELLITE_SPEED=off: neither)
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
        from aliases import AliasStore                          # noqa: PLC0415 — phrases that missed and then worked run their call at once (aliases.py)
        brain.aliases = AliasStore(housemem.home_dir(env) / "aliases.json", enabled=env.get("SATELLITE_ALIASES", "on").lower() not in ("off", "0", "no"))
        brain.pre_route = lambda heard: book_voice.pre_route(heard, book_view)   # "next chapter", "back thirty seconds", "read my book": the radio's book, before the model
        room = holder["room"] = Room(name, link, gate, brain, timers, spotter=spotter, local_voice=audio.local_voice_pcm16, log=log,
                                     floor=float(env.get("SATELLITE_FLOOR", "0.012")), filler=filler_bank, debug=env.get("SATELLITE_DEBUG") == "1", mic_gain=float(env.get("SATELLITE_MIC_GAIN", "1.0")), pc_outs=speaker_out.parse_outputs(env, name, log)[1],
                                     filler_after_s=float(env.get("SATELLITE_FILLER_S", "1.0")), hlog=hlog, workspace=workspace, run_action=run_action, homechat=homechat, starters=starters,
                                     followup_ms=int(float(env.get("SATELLITE_SESSION_S", "30")) * 1000))      # the name opens a conversation: it stays open this long without a word
        room.alarms = alarms
        room.pod_voice = env.get("SATELLITE_POD_VOICE", "0").lower() in ("1", "on", "yes")
        room.clock = house_clock
        room.inbox = state / "hub-inbox" / f"{name}.json"
        connected[name] = room
        room.announce_outs = speaker_out.announce_outputs(env, name, log)   # the routines, timers and messages also upstairs (SATELLITE_ANNOUNCE_OUT)
        room.env = env
        room.daily = routines
        room.stereo = radio.for_room(radio_client.room_kinds(env, name), name)   # Skippy speaking here ducks the whole station (all of it, on every device)
        attach_speed(room, speed)                                # the keeper knows this room's turns (its prompt's state, the quip's delay); a fixed phrase is served from the clips rendered ahead
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
    make_room.speed = speed                                     # _main runs it (SpeedKeeper.run); None when SATELLITE_SPEED is off
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
                                                       "mood": {"type": "string", "description": "descriptive words, all of which must fit (the word cloud in Traktor's collection): a feeling (relaxing, sad, dark, happy, energetic, angry, romantic, party), a use (driving, workout, late-night, background, dancing), a theme (death, fear, faith, lonely, night, nature), a sound (instrumental, slow, fast, minor, non-english), an era or year (80s, 1994), a genre family (industrial, goth) e.g. 'dark industrial', 'calm instrumental'"},
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
                                await box.link.announce(text, voice if getattr(box, "pod_voice", True) else None)
                    asyncio.ensure_future(by_stereo())
                    heard.append(lay.place_words(stereo) or place)
                else:                                           # the stereo went off since the look: those boxes speak after all
                    loud += standing_in
            for r in loud:
                asyncio.ensure_future(r.link.announce(text, voice if getattr(r, "pod_voice", True) else None))
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
        speed = getattr(hub.make_room, "speed", None)
        beat = heartbeat(hub, Path(hub.make_room.state) / "hub-status.json", getattr(hub.make_room, "house", None))             if getattr(hub.make_room, "state", None) else None
        await asyncio.gather(hub.run(), *([beat] if beat is not None else []), *([connect.run()] if connect else []), *([homechat.run(hub)] if homechat is not None else []),
                             *([watch.run(hub, hub.make_room.routine_room)] if watch is not None else []),
                             *([clock.run(hub)] if clock is not None else []),
                             *([speed.run(hub)] if speed is not None else []),
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
