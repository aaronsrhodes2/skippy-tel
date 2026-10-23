"""The Wyoming hub: Skippy in every room. skippy-pc does the hearing, thinking and talking; each room only needs a small box with a microphone and a
speaker running a Wyoming satellite (wyoming-satellite on a Raspberry Pi, or any device that speaks the protocol).

    room box  --(Wyoming over TCP, port 10700)-->  this hub  -->  the voice gate (Whisper; Skippy's ElevenLabs voice)  +  the quick brain
              <--  "detection", "transcript", Skippy's voice as PCM  --

The hub connects OUT to each satellite (the same direction Home Assistant uses), sends `run-satellite`, and from then on the satellite streams.
Two kinds of satellite, decided by the `run-pipeline` it sends:

* it heard "Skippy" itself (its own openWakeWord with our skippy model; start_stage `asr`): the audio that follows is the command;
* it streams everything (start_stage `wake`): the hub listens for "Skippy" with the same model (wakeword.py), or, with no model yet, the
  old way (each utterance to Whisper, kept only if it begins with "Skippy"). Only this kind can do Alexa's follow-up window.

Nothing is recorded or kept, as in ear.py. Timers are per room and are announced in the room that set them. Each room has its own short memory.

    python hub.py                    rooms from SATELLITE_ROOMS, e.g. "kitchen=192.168.1.40,bedroom=192.168.1.41:10700"
    python hub.py --say kitchen "Dinner is ready, monkeys."      say something in one room, then quit
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from array import array
from collections import deque

from vad import Vad, rms
from voicegate import BudgetSpent, GateError, wav_bytes
from wake import is_goodbye, is_stop, wake_split

PORT = 10700
RATE, WIDTH, CHANNELS = 16000, 2, 1                             # what the hub hears in (satellites are asked for this; others are converted)
BLOCK = 800                                                     # 50 ms: what the Vad's numbers were tuned on (the same as ear.py)
BLOCK_MS = 50
CHUNK = 2048                                                    # samples per audio-chunk sent back
IDLE, LISTENING, FOLLOWUP, BUSY = "idle", "listening", "followup", "busy"


def parse_rooms(spec: str) -> list[tuple[str, str, int]]:
    """"kitchen=192.168.1.40, bedroom=pi-bed.local:10701" -> [("kitchen", "192.168.1.40", 10700), ("bedroom", "pi-bed.local", 10701)]."""
    rooms = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, eq, addr = (s.strip() for s in part.partition("="))
        host, colon, port = addr.rpartition(":") if ":" in addr else (addr, "", "")
        if not eq or not name or not host or (colon and not port.isdigit()):
            raise ValueError(f"a room is name=host[:port], not {part!r}")
        rooms.append((name, host, int(port) if port else PORT))
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


class Room:
    """One satellite: the audio it streams in, what Skippy hears and decides, what goes back. `send` is an async callable taking an Event."""

    def __init__(self, name, send, gate, brain, timers, spotter=None, decode=None, local_voice=None, log=print, floor: float = 0.012,
                 min_speech_ms: int = 600, listen_ms: int = 8000, followup_ms: int = 6000, summarize_over: int = 280, sleep=asyncio.sleep):
        self.name, self.send, self.gate, self.brain, self.timers, self.spotter = name, send, gate, brain, timers, spotter
        self.decode, self.local_voice, self.log, self.sleep = decode, local_voice, log, sleep
        self.floor, self.min_speech_ms, self.listen_ms, self.followup_ms, self.summarize_over = floor, min_speech_ms, listen_ms, followup_ms, summarize_over
        self.remote_wake = True                                 # until the satellite says it woke on its own
        self.converter = None
        self.tasks: set = set()
        self.voice = asyncio.Lock()                             # one thing said at a time (a timer waits for an answer to finish)
        self.vad = Vad(floor_min=floor)
        self.pre: deque = deque(maxlen=8)                       # 400 ms before the voice was noticed, so the first syllable is not lost
        self.buf: list | None = None
        self.t, self._carry = 0, []                             # t: milliseconds of audio heard on this connection (the room's own clock)
        self.mode, self.deadline = IDLE, 0

    def _fresh(self) -> None:
        """Ready for the next thing said: the room's learned quiet level stays, everything half-heard goes."""
        self.vad.restart()
        self.pre.clear()
        self.buf, self._carry = None, []
        if self.spotter is not None:
            self.spotter.reset()

    # ── events from the satellite ──
    async def on_event(self, event) -> None:
        kind, data = event.type, event.data or {}
        if kind == "run-pipeline":
            self.remote_wake = data.get("start_stage") == "wake"
            if not self.remote_wake and self.mode == BUSY:      # it heard its name in Skippy's own answer: send it back to sleep
                await self.send(ev("transcript", {"text": ""}))
            elif not self.remote_wake:                          # it heard "Skippy" itself: what follows is the command
                self._fresh()
                self.mode, self.deadline = LISTENING, self.t + self.listen_ms
                self.log(f"[{self.name}] woke (on the satellite)")
        elif kind == "audio-start":
            self._converter(data)
        elif kind == "audio-chunk" and event.payload:
            if self.converter is None:
                self._converter(data)
            self.feed_pcm(self._convert(event))
        elif kind == "audio-stop" and not self.remote_wake and self.buf:
            self._finish()                                      # it stopped streaming mid-sentence: use what we have
        elif kind == "ping":
            await self.send(ev("pong", {"text": data.get("text")}))
        elif kind == "info":
            sat = data.get("satellite") or {}
            self.log(f"[{self.name}] connected: {sat.get('name') or 'a Wyoming satellite'}")

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
            self._woke()
        event = self.vad.push(rms(block), self.t)
        if self.buf is not None and event != "speech":
            self.buf.append(block)
        if event == "speech":
            self.buf = list(self.pre)
            if self.mode in (LISTENING, FOLLOWUP):
                self._spawn(self.send(ev("voice-started", {"timestamp": self.t})))
        elif event == "end" and self.buf is not None:
            self._finish()
        self._tick()

    def _woke(self) -> None:
        self.log(f"[{self.name}] woke")
        self.mode, self.deadline = LISTENING, self.t + self.listen_ms
        if self.buf is None:
            self.buf = list(self.pre)[:-1]                      # "Skippy, lights off" in one breath: keep the whole sentence (this block is added below)
        self._spawn(self.send(ev("detection", {"name": getattr(self.spotter, "name", "skippy"), "timestamp": self.t})))

    def _tick(self) -> None:
        if self.mode in (LISTENING, FOLLOWUP) and self.t > self.deadline and self.buf is None:
            self.mode = IDLE
            if not self.remote_wake:                            # an empty transcript sends the satellite back to its own wake word
                self._spawn(self.send(ev("transcript", {"text": ""})))

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
                await self.send(ev("voice-stopped", {"timestamp": self.t}))
            try:
                text = await asyncio.to_thread(self.gate.stt, wav_bytes(samples))
            except GateError as e:
                self.log(f"[{self.name}] (could not hear: {e})")
                text = ""
            woke, rest = wake_split(text)
            if was == IDLE:                                     # no spotter: Whisper is the wake word
                if not woke:
                    self._resume(IDLE)
                    return
                self.log(f"[{self.name}] woke")
                await self.send(ev("detection", {"name": "skippy", "timestamp": self.t}))
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

    async def handle(self, command: str) -> None:
        await self.send(ev("transcript", {"text": command}))   # also tells a self-waking satellite to stop streaming
        if is_goodbye(command) or is_stop(command):
            self._resume(IDLE)
            return
        self.log(f"[{self.name}] heard: {command}")
        try:
            reply = await asyncio.to_thread(self.brain.reply, command)
        except Exception as e:                                  # noqa: BLE001 — whatever the API did, he gets an answer
            self.log(f"[{self.name}] (brain failed: {type(e).__name__})")
            reply = "My brain is not answering. Typical."
        self.log(f"[{self.name}] said: {reply[:120]}")
        await self.say(reply)
        if self.remote_wake:
            self._resume(FOLLOWUP, self.followup_ms)            # like Alexa: a few seconds to answer back without the name
        else:
            self._resume(IDLE)

    def _resume(self, mode: str, window_ms: int = 0) -> None:
        self._fresh()
        self.mode, self.deadline = mode, self.t + window_ms

    async def say(self, text: str) -> None:
        """Skippy's voice in this room: the gate's ElevenLabs (a summary when long), else the machine's own voice. Waits until it has played."""
        said = text
        if len(text) > self.summarize_over:
            try:
                said = await asyncio.to_thread(self.gate.summary, text)
            except GateError:
                said = text[:self.summarize_over]
        async with self.voice:
            await self._speak(said)

    async def _speak(self, said: str) -> None:
        pcm = rate = None
        try:
            pcm, rate = await asyncio.to_thread(self.decode, await asyncio.to_thread(self.gate.tts, said))
        except BudgetSpent:
            self.log(f"[{self.name}] (Skippy's voice has used today's budget: using the machine's own voice)")
        except (GateError, RuntimeError, OSError) as e:
            self.log(f"[{self.name}] (voice unavailable: {e}: using the machine's own voice)")
        if pcm is None and self.local_voice is not None:
            try:
                pcm, rate = await asyncio.to_thread(self.local_voice, said)
            except (RuntimeError, OSError) as e:
                self.log(f"[{self.name}] (no voice at all: {e})")
        await self.send(ev("synthesize", {"text": said}))
        if not pcm:
            return
        fmt = {"rate": rate, "width": 2, "channels": 1}
        await self.send(ev("audio-start", {**fmt, "timestamp": 0}))
        step = CHUNK * 2
        for i in range(0, len(pcm), step):
            await self.send(ev("audio-chunk", {**fmt, "timestamp": i * 1000 // (2 * rate)}, pcm[i:i + step]))
        await self.send(ev("audio-stop", {"timestamp": len(pcm) * 1000 // (2 * rate)}))
        await self.sleep(len(pcm) / (2 * rate) + 0.4)          # the satellite plays it now; its microphone hears it, so we wait it out

    async def announce(self, text: str) -> None:
        """Say something in this room unprompted (a timer, a message from elsewhere)."""
        was, self.mode = self.mode, BUSY
        try:
            await self.say(text)
        finally:
            self._resume(IDLE if was == BUSY else was, self.followup_ms if was == FOLLOWUP else 0)

    async def timers_due(self) -> None:
        for timer in self.timers.due():
            self.log(f"[{self.name}] timer done: {timer.label}")
            await self.announce(f"Your {timer.label} timer is done.")


class Hub:
    """Keeps a connection to every room's satellite, reconnecting when one drops (a box unplugged must not need the hub restarted)."""

    def __init__(self, rooms, make_room, log=print, connect=asyncio.open_connection, retry_s=(2, 5, 15, 30)):
        self.rooms_spec, self.make_room, self.log, self.connect, self.retry_s = rooms, make_room, log, connect, retry_s
        self.rooms: dict[str, Room] = {}

    async def run(self) -> None:
        await asyncio.gather(*(self.keep(name, host, port) for name, host, port in self.rooms_spec))

    async def keep(self, name: str, host: str, port: int, forever: bool = True) -> None:
        failures = 0
        while True:
            try:
                await self.serve(name, host, port)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:                              # noqa: BLE001 — one room's trouble never takes the other rooms down
                if failures == 0:
                    self.log(f"[{name}] not reachable at {host}:{port} ({type(e).__name__}: {e}); retrying")
                failures += 1
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

        room = self.rooms[name] = self.make_room(name, send)
        ticker = asyncio.ensure_future(self._tick(room))
        try:
            await send(ev("describe"))
            await send(ev("run-satellite"))
            while True:
                event = await async_read_event(reader)
                if event is None:
                    self.log(f"[{name}] disconnected")
                    return
                await room.on_event(event)
        finally:
            ticker.cancel()
            for task in list(room.tasks):
                task.cancel()
            writer.close()

    @staticmethod
    async def _tick(room: Room) -> None:
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
    """A Room factory sharing the gate, the house and the spotter's model file; each room gets its own brain memory and timers."""
    import anthropic                                            # noqa: PLC0415
    import audio                                                # noqa: PLC0415
    import ear                                                  # noqa: PLC0415
    from brain import Brain                                     # noqa: PLC0415
    from timers import Timers                                   # noqa: PLC0415
    from wakeword import Spotter, default_model                 # noqa: PLC0415
    from voicegate import Gate                                  # noqa: PLC0415
    token, key = env.get("SKIPPY_HOME_TOKEN", ""), env.get("ANTHROPIC_API_KEY", "")
    if not token or not key:
        sys.exit("hub: SKIPPY_HOME_TOKEN and ANTHROPIC_API_KEY must be in the vault .env (or the environment)")
    gate = Gate(env.get("SATELLITE_GATE", "http://127.0.0.1:3007"), token)
    house = ear.build_house(env)                                # the same house as the single-room listener: one set of lights
    client = anthropic.Anthropic(api_key=key)
    model_path = default_model(env)
    threshold = float(env.get("SATELLITE_WAKE_THRESHOLD", "0.5"))
    if model_path.is_file():
        print(f"wake word: {model_path.name} (threshold {threshold})", flush=True)
    else:
        print(f"wake word: no model at {model_path}; using Whisper for the wake word (see wakeword/README.md)", flush=True)
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)   # noqa: E731

    def make_room(name, send):
        timers = Timers()
        spotter = Spotter.load(model_path, threshold=threshold) if model_path.is_file() else None
        brain = Brain(client, env.get("SATELLITE_MODEL", "claude-haiku-4-5"), timers, hass=house)
        return Room(name, send, gate, brain, timers, spotter=spotter, decode=audio.mp3_to_pcm16, local_voice=audio.local_voice_pcm16, log=log,
                    floor=float(env.get("SATELLITE_FLOOR", "0.012")))
    return make_room


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
        sys.exit('hub: no rooms: set SATELLITE_ROOMS, e.g. "kitchen=192.168.1.40" (see README.md)')
    hub = Hub(rooms, build_rooms(env), log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True))
    if args.say:
        return asyncio.run(_say_once(hub, rooms, *args.say))
    print(time.strftime("%H:%M:%S"), "hub: rooms " + ", ".join(f"{n} ({h}:{p})" for n, h, p in rooms), flush=True)
    asyncio.run(hub.run())
    return 0


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
