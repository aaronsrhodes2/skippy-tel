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
                 min_speech_ms: int = 600, listen_ms: int = 8000, followup_ms: int = 6000, summarize_over: int = 1500, filler=None, filler_after_s: float = 1.0):
        self.name, self.link, self.gate, self.brain, self.timers, self.spotter = name, link, gate, brain, timers, spotter
        self.local_voice, self.log = local_voice, log
        self.floor, self.min_speech_ms, self.listen_ms, self.followup_ms, self.summarize_over = floor, min_speech_ms, listen_ms, followup_ms, summarize_over
        self.filler, self.filler_after_s = filler, filler_after_s       # pre-recorded quips (filler.py) to cover a slow answer
        self.remote_wake = True                                 # until the box says it woke on its own
        self.music = None                                       # music.BoxMusic for a box that plays Bilby's library
        self.tasks: set = set()
        self.voice = asyncio.Lock()                             # one thing said at a time (a timer waits for an answer to finish)
        self.vad = Vad(floor_min=floor)
        self.pre: deque = deque(maxlen=24)                      # 1.2 s before the voice was noticed: "Hey Skippy" is quieter than what follows, so the bar is crossed late
        self.buf: list | None = None
        self.t, self._carry = 0, []                             # t: milliseconds of audio heard on this connection (the room's own clock)
        self.mode, self.deadline = IDLE, 0
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
        self._carry.extend(pcm_floats(pcm))
        while len(self._carry) >= BLOCK:
            block, self._carry = self._carry[:BLOCK], self._carry[BLOCK:]
            self.t += BLOCK_MS
            self.feed(block)

    def feed(self, block) -> None:
        if self.mode == BUSY:
            return                                              # thinking or talking: never answer our own voice
        self.pre.append(block)
        if self.mode == IDLE and self.spotter is not None and self.remote_wake and self.spotter.feed(block):
            self._woke(getattr(self.spotter, "name", "skippy"))
        event = self.vad.push(rms(block), self.t)
        if event in ("speech", "end"):
            self.log(f"[{self.name}] (voice {event}: level {rms(block):.3f}, mode {self.mode})")
        if self.buf is not None and event != "speech":
            self.buf.append(block)
        if event == "speech":
            self.buf = list(self.pre)
            if self.mode in (LISTENING, FOLLOWUP):
                self._spawn(self.link.voice_started())
        elif event == "end" and self.buf is not None:
            self._finish()
        self._tick()

    def _woke(self, by: str) -> None:
        self.log(f"[{self.name}] woke")
        self.mode, self.deadline = LISTENING, self.t + self.listen_ms
        if self.buf is None:
            self.buf = list(self.pre)[:-1]                      # "Skippy, lights off" in one breath: keep the whole sentence (this block is added below)
        self._spawn(self.link.woke(by))

    def _tick(self) -> None:
        if self.mode in (LISTENING, FOLLOWUP) and self.t > self.deadline and self.buf is None:
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
            try:
                text = await asyncio.to_thread(self.gate.stt, wav_bytes(samples))
            except GateError as e:
                self.log(f"[{self.name}] (could not hear: {e})")
                text = ""
            woke, rest = wake_split(text)
            if was == IDLE:                                     # no spotter: Whisper is the wake word
                if not woke:
                    self.log(f"[{self.name}] (heard, not for me: {text!r})")
                    self._resume(IDLE)
                    return
                self.log(f"[{self.name}] woke")
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

    async def handle(self, command: str) -> None:
        await self.link.heard(command)                          # also tells a self-waking satellite to stop streaming
        if is_goodbye(command) or is_stop(command):
            self._resume(IDLE)
            await self._sign_off(command)
            await self.link.released()
            return
        self.log(f"[{self.name}] heard: {command}")
        try:
            work = asyncio.ensure_future(asyncio.to_thread(self.brain.reply, command))
            await self._cover_the_wait(work, command)
            reply = await work
        except Exception as e:                                  # noqa: BLE001 — whatever the API did, he gets an answer
            self.log(f"[{self.name}] (brain failed: {type(e).__name__})")
            reply = "My brain is not answering. Typical."
        self.log(f"[{self.name}] said: {reply[:120]}")
        await self.say(reply, follow_up=True)
        if self.remote_wake and self.link.hub_follows_up:
            self._resume(FOLLOWUP, self.followup_ms)            # like Alexa: a few seconds to answer back without the name
        else:
            self._resume(IDLE)

    async def _sign_off(self, command: str) -> None:
        """A short pre-recorded line when he is thanked or told goodbye (not for a plain stop or cancel)."""
        import re                                               # noqa: PLC0415
        if self.filler is None or not re.search(r"thank|bye|done|later", command, re.I):
            return
        quip = self.filler.pick("goodbye")
        if quip is not None:
            text, mp3 = quip
            self.log(f"[{self.name}] (sign-off: {text})")
            async with self.voice:
                await self.link.show(text)
                await self.link.announce(text, ("mp3", mp3))

    async def _cover_the_wait(self, work, command: str) -> None:
        """While the answer is worked out, say a pre-recorded quip as soon as it is clearly not going to be instant (never for a quick home command), and one more
        if it is still not ready after the first: Skippy answers at once, and the real answer follows."""
        if self.filler is None or self.filler_after_s <= 0:
            return
        kind = self.filler.kind_for(command)
        if kind is None:
            return
        done, _ = await asyncio.wait({work}, timeout=self.filler_after_s)
        for stage in (kind, "long"):
            if done:
                return
            quip = self.filler.pick(stage)
            if quip is not None:
                text, mp3 = quip
                self.log(f"[{self.name}] (filler: {text})")
                async with self.voice:
                    await self.link.show(text)
                    await self.link.announce(text, ("mp3", mp3))
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

    async def say(self, text: str, follow_up: bool = False, announce: bool = False) -> None:
        """Skippy's voice in this room: the gate's local TTS (a summary when long), else the machine's own voice. Waits until it has played."""
        said = text
        if looks_technical(text) or len(text) > self.summarize_over:       # only technical or very long output is boiled down: his voice and jokes are spoken whole
            try:
                said = await asyncio.to_thread(self.gate.summary, text)
            except GateError:
                said = text[:self.summarize_over]
        async with self.voice:
            voice = await self._voice(said)
            await self.link.show(said)
            if announce:
                await self.link.announce(said, voice)
            else:
                await self.link.speak(said, voice, follow_up)

    async def _voice(self, said: str):
        """("mp3", bytes) from Skippy's own voice, ("pcm", bytes, rate) from the machine's, or None when neither answers."""
        try:
            parts = split_for_voice(said)                       # the gate speaks at most 500 characters at a time: a longer reply is spoken in pieces, joined into one clip
            clips = await asyncio.gather(*[asyncio.to_thread(self.gate.tts, t) for t in parts])
            return ("mp3", b"".join(clips))
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

    async def announce(self, text: str) -> None:
        """Say something in this room unprompted (a timer, a message from elsewhere)."""
        was, self.mode = self.mode, BUSY
        try:
            await self.say(text, announce=True)
        finally:
            self._resume(IDLE if was == BUSY else was, self.followup_ms if was == FOLLOWUP else 0)

    async def timers_due(self) -> None:
        for timer in self.timers.due():
            self.log(f"[{self.name}] timer done: {timer.label}")
            await self.announce(f"Your {timer.label} timer is done.")


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
    filler_bank = FillerBank.load(Path(__file__).resolve().parents[2] / "state" / "filler")      # recorded by tools/make_filler_bank.py; none = no quips
    threshold = float(env.get("SATELLITE_WAKE_THRESHOLD", "0.5"))
    if model_path.is_file():
        print(f"wake word: {model_path.name} (threshold {threshold})", flush=True)
    else:
        print(f"wake word: no model at {model_path}; using Whisper for the wake word (see wakeword/README.md)", flush=True)
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)   # noqa: E731
    loop = asyncio.get_running_loop()                           # build_rooms runs on the hub's loop (_main)

    def make_room(name, link):
        timers = Timers()
        spotter = Spotter.load(model_path, threshold=threshold) if model_path.is_file() else None
        holder: dict = {}
        plays_music = "player" in getattr(link, "keys", {})      # an ESPHome box with a media player (esphome_link.discover ran first)
        tools = music_tools(lambda: holder["room"], loop) if plays_music else None
        brain = Brain(client, brain_model, timers, hass=house, extra=tools)
        room = holder["room"] = Room(name, link, gate, brain, timers, spotter=spotter, local_voice=audio.local_voice_pcm16, log=log,
                                     floor=float(env.get("SATELLITE_FLOOR", "0.012")), filler=filler_bank,
                                     filler_after_s=float(env.get("SATELLITE_FILLER_S", "1.0")),
                                     followup_ms=int(float(env.get("SATELLITE_SESSION_S", "180")) * 1000))      # the name opens a conversation: it stays open this long without a word
        return room
    return make_room


MUSIC_TOOLS = [
    {"name": "music", "description": "Control the music playing in THIS room (Bilby's Audio Service on this room's speaker).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["pause", "resume", "next", "previous", "louder", "quieter"]}},
                      "required": ["action"]}},
    {"name": "play_music", "description": "Play music from the Captain's own library in THIS room: an artist, an album or a song, in his words.",
     "input_schema": {"type": "object", "properties": {"what": {"type": "string", "description": "e.g. 'Pink Floyd', 'Dark Side of the Moon', 'Comfortably Numb'"}},
                      "required": ["what"]}},
]


def music_tools(room_of, loop):
    """(tools, run) for Brain(extra=...): the music in the room the brain belongs to. The brain runs in a worker thread; the music lives on
    the hub's event loop, so each call is handed over to it. A room without a speaker for music says so."""
    def run(name: str, args: dict) -> str:
        import music as music_mod                               # noqa: PLC0415
        room = room_of()
        box = getattr(room, "music", None)
        if box is None:
            return "This room has no speaker for music."

        async def go() -> str:
            if name == "music":
                action = str((args or {}).get("action") or "")
                step = {"pause": box.pause, "resume": box.resume, "next": box.next, "previous": box.prev}.get(action)
                if step is not None:
                    await step()
                    return f"Done: {action}."
                if action in ("louder", "quieter"):
                    await box.volume(1 if action == "louder" else -1)
                    return f"Done: {action}."
                return "I cannot do that to the music."
            what = str((args or {}).get("what") or "")
            found = music_mod.search(await box.media.catalog(), what)
            if not found:
                return f"Nothing in his library matches {what!r}."
            await box.play_queue(found)
            title, artist = box.title()
            return f"Playing {len(found)} track{'s' if len(found) != 1 else ''}, starting with {title}" + (f" by {artist}." if artist else ".")
        try:
            return asyncio.run_coroutine_threadsafe(go(), loop).result(30)
        except Exception as e:                                  # noqa: BLE001 — said aloud, not raised
            return f"The music did not answer ({type(e).__name__})."
    return MUSIC_TOOLS, run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--say", nargs=2, metavar=("ROOM", "TEXT"), help="say this in one room, then quit")
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
        await media.start()
        connect = music.Connect(env.get("PORTAL_URL", music.PORTAL), env.get("SKIPPY_HOME_TOKEN", ""), log=log)
        labels = dict(part.split("=", 1) for part in env.get("SATELLITE_ROOM_NAMES", "").split(",") if "=" in part)
        esphome = lambda hub, name, host, port: esphome_link.serve(hub, name, host, port, media=media, connect=connect,   # noqa: E731
                                                                   noise_psk=env.get("SATELLITE_ESPHOME_KEY", ""), label=labels.get(name, ""))
    hub = Hub(rooms, build_rooms(env), log=log, decode=audio.mp3_to_pcm16, esphome=esphome)
    try:
        if args.say:
            return await _say_once(hub, rooms, *args.say)
        log("hub: rooms " + ", ".join(f"{n} ({k} {h}:{p})" for n, k, h, p in rooms))
        await asyncio.gather(hub.run(), *([connect.run()] if connect else []))
        return 0
    finally:
        if media is not None:
            await media.stop()


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
