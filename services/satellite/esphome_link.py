"""A Room's way to an ESPHome box (rooms/skippy-room.yaml): ESPHome's native API through aioesphomeapi, the library Home Assistant uses, so the
hub is the box's Home Assistant. The events and their data are the ones Home Assistant's own esphome/assist_satellite.py sends.

    the box streams its microphone (use_wake_word: the hub hears "Skippy")   ->  handle_start / handle_audio / handle_stop
    the hub: RUN_START, WAKE_WORD_START ... WAKE_WORD_END, STT_START, STT_VAD_START/END, STT_END {text}, INTENT_START,
             INTENT_END {speech, continue_conversation}, TTS_START {text}, TTS_END {url}, RUN_END
    the box plays the TTS url on its media player (as an announcement, over the music) and says so (VoiceAssistantAnnounceFinished)

continue_conversation=1 makes the box start the next turn without the name, Alexa's follow-up window, done by the box itself. Unprompted speech
(a timer, hub.py --say) is an announcement; Bilby's tracks are the media player's media. The screen's subtitle is a `text` entity the hub
writes; a tap is a `binary_sensor` the hub hears as "Skippy".
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from pathlib import Path

import emotion

PLAY_TIMEOUT_S = 90
EMOTION_CACHE: dict[str, str] = {}          # a line's words -> its emotion codes (the fillers come round again: _model_emotion)
EMOTION_CACHE_MAX = 600


def _voice_seconds(voice) -> float:
    """Roughly how long an answer takes to say: the box tells us when it is done, but never wait past this (plus a margin)."""
    if voice is None:
        return 0.0
    if voice[0] == "mp3":
        return len(voice[1]) * 8 / 64000                           # mp3: assume 64 kbps (local voices are similar)
    return len(voice[1]) / (2 * voice[2])


def envelope(pcm: bytes, step_ms: int = 60, rate: int = 16000, cap: int = 250) -> str:
    """One digit per `step_ms` of 16 kHz mono s16le audio: its loudness, 0 (silent) to 9 (as loud as the clip gets)."""
    import array
    import math
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) // 2 * 2])
    n = rate * step_ms // 1000
    levels = [math.sqrt(sum(x * x for x in a[i:i + n]) / max(1, len(a[i:i + n]))) for i in range(0, len(a), n)][:cap]
    if not levels:
        return ""
    top = sorted(levels)[int(len(levels) * 0.95) - 1 if len(levels) > 20 else -1] or 1
    return "".join(str(min(9, int(9 * l / top + 0.35))) if l > 0.04 * top else "0" for l in levels)


RHUBARB = Path(os.environ.get("RHUBARB", "")) if os.environ.get("RHUBARB") else     Path(__file__).resolve().parents[2] / "state" / "rhubarb" / "Rhubarb-Lip-Sync-1.13.0-Windows" / "rhubarb.exe"   # Rhubarb Lip Sync (MIT): mouth shapes from audio


def plain(said: str) -> str:
    """What to show on the screen: the words without the voice's stage directions ([sighs])."""
    import re
    return re.sub(r"\s*\[[^\]]*\]\s*", " ", said or "").strip()


def mood_of(said: str) -> str:
    """The face's mood while he says this: n neutral, h happy, a sarcastic, s surprised, q curious, d sad."""
    t = (said or "").lower()
    if "[sigh" in t or "[groan" in t:
        return "a"
    t = plain(t)
    if any(w in t for w in ("sorry", "unfortunately", "can't", "cannot", "unable", "afraid")):
        return "d"
    if any(w in t for w in ("monkey", "obviously", "idiot", "genius", "magnificent", "of course")):
        return "a"
    if any(w in t for w in ("wow", "whoa", "no way", "incredible")):
        return "s"
    if t.rstrip().endswith("?"):
        return "q"
    if "!" in t or any(w in t for w in ("great", "done", "ready", "awesome", "glad")):
        return "h"
    return "n"


def shapes(cues, step_ms: int = 60, cap: int = 249) -> str:
    """Rhubarb's cues [(start seconds, shape letter)] as one letter per `step_ms`."""
    if not cues:
        return ""
    out, j = [], 0
    for i in range(cap):
        t = i * step_ms / 1000.0
        while j + 1 < len(cues) and cues[j + 1][0] <= t:
            j += 1
        out.append(cues[j][1])
        if t > cues[-1][0] + 0.3:
            break
    return "".join(out)


class EsphomeLink:
    hub_follows_up = True                                           # the hub keeps the conversation open (the box streams again after each answer), see Room.followup_ms

    def __init__(self, client, media, box_host: str, log=print, wait_cap: float = PLAY_TIMEOUT_S):
        from aioesphomeapi import VoiceAssistantEventType as E    # noqa: PLC0415
        self.E, self.client, self.media, self.box_host, self.log, self.wait_cap = E, client, media, box_host, log, wait_cap
        self.room = None
        self.music = None                                           # radio_client.PodTuner: the pod's speaker tuned to the house radio
        self.player_playing = None                                  # the pod's media player says it plays (None: not reported yet)
        self.keys: dict[str, int] = {}                              # player, subtitle, tap, mic
        self.run_live, self.stage, self.vad_ended = False, "", False
        self._played = asyncio.Event()
        self._follow = False
        self._tasks: set = set()
        self.hub = None                                             # set by serve(): the other rooms' pods, to mirror his speech onto
        self.services: list = []                                    # the box's API actions (the `face` one drives the mirror)
        self.asked = ""
        self.streamer = None                                        # the face skippy-pc draws for this box (face_stream.FaceStreamer), once it is connected
        self._box_face = ("idle", False, False, False)              # the box's own last `Face` report: state, talking, mic off, speaker off
        self._lips_until = 0.0                                      # (monotonic) the hub drives the mouth until then: his voice is on the PC output (lips)
        self._mirror_next = None                                    # (mouth, emote) for the other pods, started by lips()
        self._mic_switch_off = False                                # the box's Microphone switch, as it last reported it
        self._ended_at = 0.0                                        # (monotonic) when released() last ended a turn on the box

    def discover(self, entities) -> None:
        for e in entities:
            kind, oid = type(e).__name__, str(getattr(e, "object_id", "") or "").lower()
            if kind == "MediaPlayerInfo":
                self.keys.setdefault("player", e.key)
            elif kind == "TextInfo" and "subtitle" in oid:
                self.keys["subtitle"] = e.key
            elif kind == "TextInfo" and "session" in oid:
                self.keys["session"] = e.key
            elif kind == "TextInfo" and "emote" in oid:
                self.keys["emote"] = e.key
            elif kind == "TextInfo" and "mouth" in oid:
                self.keys["mouth"] = e.key
            elif kind == "BinarySensorInfo" and oid.endswith("tap"):
                self.keys["tap"] = e.key
            elif kind == "BinarySensorInfo" and "stream_live" in oid:
                self.keys["stream_live"] = e.key
            elif kind == "TextSensorInfo" and oid == "face":
                self.keys["face"] = e.key
            elif kind == "SensorInfo" and oid == "stream_loss":
                self.keys["stream_loss"] = e.key
            elif kind == "SwitchInfo" and oid == "microphone":
                self.keys["mic"] = e.key

    def set_text(self, name: str, text: str) -> None:
        """Write one of the box's text entities (mouth, emote, session) and tell the face drawn here the same thing."""
        if name in self.keys:
            self.client.text_command(self.keys[name], text)
        f = self.streamer.face if self.streamer is not None else None
        if f is not None:
            if name == "mouth":
                f.mouth = text
            elif name == "emote":
                f.emote = text
            elif name == "session":
                f.set_session(text == "1")

    def music_heard(self, heard: bool) -> None:
        """The room has music on and nothing else: he dances to it now and then, in rest mode (the streamed face only)."""
        if self.streamer is not None:
            self.streamer.set_music(heard)

    def start_stream(self, enabled: bool = True) -> None:
        """Draw the face on skippy-pc and stream it to this box, if its firmware has the streamed face (a `Face` text sensor)."""
        if not enabled or "face" not in self.keys or self.streamer is not None:
            return
        from face_stream import FaceStreamer, RemoteFaceStreamer  # noqa: PLC0415 — OpenCV only loads when a box wants it
        try:
            ip = getattr(self.client, "connected_address", None) or socket.gethostbyname(self.box_host)
        except OSError as e:
            self.log(f"[{self.room.name if self.room else self.box_host}] (face stream: cannot resolve {self.box_host}: {e})")
            return
        label = self.room.name if self.room else self.box_host
        if os.environ.get("SATELLITE_FACE_PROC", "1") != "0":     # drawn in a child process of its own: the hub's work cannot make its frames late
            self.streamer = RemoteFaceStreamer(ip, log=self.log, name=label)
        else:
            self.streamer = FaceStreamer(ip, log=self.log, name=label)
        self.streamer.start()

    def stop_stream(self) -> None:
        if self.streamer is not None:
            self.streamer.stop()
            self.streamer = None

    def _event(self, kind, data: dict | None = None) -> None:
        self.client.send_voice_assistant_event(kind, {k: str(v) for k, v in (data or {}).items()} or None)

    # ── the box, through aioesphomeapi ──
    async def handle_start(self, conversation_id: str, flags: int, audio_settings, wake_word_phrase) -> int:
        from aioesphomeapi import VoiceAssistantCommandFlag       # noqa: PLC0415
        remote = bool(flags & VoiceAssistantCommandFlag.USE_WAKE_WORD)
        if not remote and self._follow:                             # the follow-up turn the box starts after an answer: let the answer finish
            for _ in range(60):
                if self.room.mode != "busy":
                    break
                await asyncio.sleep(0.05)
        self._follow = False
        self.run_live, self.stage, self.vad_ended = True, "wake" if remote else "stt", False
        self._event(self.E.VOICE_ASSISTANT_RUN_START)
        self._event(self.E.VOICE_ASSISTANT_WAKE_WORD_START if remote else self.E.VOICE_ASSISTANT_STT_START)
        try:
            await self.room.begin(remote_wake=remote)
        except Exception as e:                                      # noqa: BLE001 — no answer to its start request leaves the box waiting forever, deaf
            self.log(f"[{self.room.name}] (starting a turn: {type(e).__name__}: {e})")
        return 0                                                    # 0: the audio comes over the API connection itself

    async def handle_audio(self, data: bytes, data2: bytes | None = None) -> None:
        self.room.feed_pcm(data)

    async def handle_stop(self, abort: bool) -> None:
        self.room.stream_ended()
        if abort:
            self.run_live = False

    async def handle_announcement_finished(self, finished) -> None:
        self._played.set()

    def mic_off(self) -> bool:
        """The box's microphone is switched off (its key or Home Assistant): no audio is expected, and the hub never switches it back on."""
        return self._mic_switch_off or self._box_face[2]

    async def restart_stream(self, hard: bool = False) -> str:
        """The box should be streaming and is not (Room's watchdogs). Soft: end whatever turn it is stuck in (RUN_END takes it from waiting for
        an answer, or for the hub's reply to its start, back to idle, where it starts streaming again by itself). Hard: its Microphone switch off
        and on, which also brings back a box that stopped trying. What was done, or "" when nothing needed doing."""
        if hard and "mic" in self.keys and not self.mic_off():
            self.client.switch_command(self.keys["mic"], False)
            await asyncio.sleep(1.5)                                # (switched on before it has stopped, the box ignores the start)
            self.client.switch_command(self.keys["mic"], True)
            self.run_live, self.stage = False, ""
            return "switched its microphone off and on"
        if time.monotonic() - self._ended_at < 2:                   # released() just ended its turn: it is starting again already
            return ""
        self._event(self.E.VOICE_ASSISTANT_RUN_END)
        self.run_live, self.stage = False, ""
        return "ended its turn so it starts listening again"

    def on_state(self, state) -> None:
        kind = type(state).__name__
        if kind == "SwitchState" and state.key == self.keys.get("mic"):
            self._mic_switch_off = not state.state
        elif kind == "BinarySensorState" and state.key == self.keys.get("tap") and state.state:
            self.room.tap()
        elif kind == "BinarySensorState" and state.key == self.keys.get("stream_live") and self.streamer is not None:
            self.streamer.set_live(bool(state.state) and not getattr(state, "missing_state", False))
        elif kind == "SensorState" and state.key == self.keys.get("stream_loss") and self.streamer is not None:
            if not getattr(state, "missing_state", False):
                self.streamer.note_loss(int(state.state))
        elif kind == "TextSensorState" and state.key == self.keys.get("face") and self.streamer is not None:
            f = (str(state.state).split(",") + ["0", "0", "0"])[:4]          # "<state>,<talking>,<mic off>,<speaker off>" (older firmware sends two fields)
            self._box_face = (f[0], f[1] == "1", f[2] == "1", f[3] == "1")
            if time.monotonic() < self._lips_until:                 # his voice is on the stereo, not this box: the box's "not talking" is not the truth
                self.streamer.set_box_state("speaking", True, self._box_face[2], self._box_face[3])
            else:
                self.streamer.set_box_state(*self._box_face)
        elif kind == "MediaPlayerEntityState" and state.key == self.keys.get("player"):
            from aioesphomeapi import MediaPlayerState            # noqa: PLC0415
            self.player_playing = state.state == MediaPlayerState.PLAYING   # a hub that just came back: is the pod still playing the radio?
            if state.state == MediaPlayerState.IDLE and self.music is not None:
                asyncio.ensure_future(self.music.on_idle())

    # ── what the room tells the box ──
    async def woke(self, by: str) -> None:
        if self.run_live and self.stage == "wake":
            self._event(self.E.VOICE_ASSISTANT_WAKE_WORD_END, {"wake_word_output": by})
            self._event(self.E.VOICE_ASSISTANT_STT_START)
            self.stage = "stt"

    async def voice_started(self) -> None:
        self._event(self.E.VOICE_ASSISTANT_STT_VAD_START)

    async def voice_stopped(self) -> None:
        if not self.vad_ended:                                      # the box stops its microphone here and waits for the answer
            self._event(self.E.VOICE_ASSISTANT_STT_VAD_END)
            self.vad_ended = True

    async def heard(self, text: str) -> None:
        self.asked = text
        if self.streamer is not None:
            self.streamer.face.add_convo(0, text)
        if "emote" in self.keys:                                    # how he looks while he thinks about what he was just told
            self.set_text("emote", emotion.reaction(text) + "|")
        await self.voice_stopped()                                  # (heard in one breath with the name: the box must still stop listening)
        self._event(self.E.VOICE_ASSISTANT_STT_END, {"text": text})
        self._event(self.E.VOICE_ASSISTANT_INTENT_START)
        self.stage = "intent"

    async def perk(self) -> None:
        """His ears and cyan glow, as a face only: it must NOT send the box voice-assistant events (a wake event for room talk leaves its voice assistant waiting
        for a command that never comes, deaf and stuck 'listening')."""
        face = next((x for x in self.services if x.name == "face"), None)
        if face is not None:
            try:
                await self.client.execute_service(face, {"state": "listening"})
            except Exception as e:                                  # noqa: BLE001
                self.log(f"[{self.room.name}] (face: {type(e).__name__})")

    async def idle_face(self) -> None:
        """Put his resting face back (a goodbye or a stop ends the conversation without any answer, so no event ever tells the box it is over)."""
        face = next((x for x in self.services if x.name == "face"), None)
        if face is not None:
            try:
                await self.client.execute_service(face, {"state": "idle"})
            except Exception as e:                                  # noqa: BLE001
                self.log(f"[{self.room.name}] (face: {type(e).__name__})")

    async def released(self) -> None:
        """Nothing (more) to say: end the turn; the box starts listening for its name again by itself (start_continuous)."""
        await self.idle_face()
        if self.run_live and self.stage != "wake":
            # The box keeps the last turn's continue_conversation: say 0, or after a "goodbye" it would start its next turn without the name
            self._event(self.E.VOICE_ASSISTANT_INTENT_END, {"conversation_id": "", "continue_conversation": "0", "speech": ""})
            self._event(self.E.VOICE_ASSISTANT_RUN_END)
            self.run_live, self.stage = False, ""
            self._ended_at = time.monotonic()

    def others(self):
        """The other pods' links (every connected room but this one)."""
        rooms = getattr(self.hub, "rooms", {}) if self.hub is not None else {}
        return [r.link for r in rooms.values() if r.link is not self and hasattr(r.link, "mirror")]

    async def mirror(self, mouth_text: str, emote_text: str, seconds: float) -> None:
        """Another pod is speaking: this one animates the same speech, silently and at the same moment (his mouth, mood and rocking mirror in every room)."""
        face = next((x for x in self.services if x.name == "face"), None)
        if face is None or "mouth" not in self.keys:
            return
        try:
            self.set_text("mouth", mouth_text)
            if "emote" in self.keys and emote_text:
                self.set_text("emote", emote_text)
            await self.client.execute_service(face, {"state": "speaking"})
            await asyncio.sleep(min(seconds + 0.4, 30))
            await self.client.execute_service(face, {"state": "idle"})
        except Exception as e:                                      # noqa: BLE001 — a pod that is busy or gone simply misses it
            self.log(f"[{self.room.name}] (mirror: {type(e).__name__})")

    def session(self, on: bool) -> None:
        if "session" in self.keys:
            self.set_text("session", "1" if on else "0")

    async def mouth(self, voice, said: str = "", mirror_now: bool = True) -> None:
        """What the box's drawn face needs to talk: a mood letter, then one character per 60 ms for the mouth. Sent at once as loudness digits
        (ffmpeg, ~50 ms), then replaced by Rhubarb's mouth shapes when they are ready (a second or two later): the box reads them by elapsed time,
        so nothing waits and the speech starts at once. Best effort: without any of it the mouth just babbles."""
        if "mouth" not in self.keys or not voice:
            return
        mood = mood_of(said)
        try:
            proc = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1",
                                                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            pcm, _ = await asyncio.wait_for(proc.communicate(voice[1]), 10)
            text = mood + envelope(pcm, cap=249)
            self.set_text("mouth", text)
            etext = emotion.reaction(self.asked) + "|" + emotion.track(plain(said), self.asked, len(pcm) / 32000.0)
            if "emote" in self.keys:                                # what he feels, sentence by sentence, over the length of his voice
                self.set_text("emote", etext)
            if mirror_now:
                for other in self.others():                         # every other pod does the same, silently, at the same moment
                    self._spawn_mirror(other.mirror(text, etext, len(pcm) / 32000.0))
            else:
                self._mirror_next = (text, etext)                   # lips() starts them when the PC output sounds
            if "emote" in self.keys:                                # and a small model reads the sarcasm a second later (replaces the word rules; costs no waiting)
                self._spawn_mirror(self._model_emotion(said, len(pcm) / 32000.0))
        except Exception as e:                                      # noqa: BLE001 — the mouth just babbles
            self.log(f"[{self.room.name}] (mouth envelope: {type(e).__name__})")
            return
        if RHUBARB.exists():
            task = asyncio.create_task(self._rhubarb(voice, mood))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def lips(self, at: float, seconds: float) -> None:
        """Skippy's voice sounds from the room's PC output, not this box (the Captain, 2026-10-31: one source): the drawn face talks from `at`
        (time.monotonic, when the router says it sounds there) for `seconds`, on that clock, not the box's playback. mouth() set the shapes."""
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        self._lips_until = max(self._lips_until, at + seconds)
        loop.call_later(max(0.0, at - now), self._lips, True, seconds)
        loop.call_later(max(0.0, at + seconds - now), self._lips, False, 0.0)

    def _lips(self, on: bool, seconds: float) -> None:
        if not on and time.monotonic() < self._lips_until - 0.02:  # a later clip is still talking: keep the mouth going
            return
        if self.streamer is not None:                               # the face skippy-pc draws and streams to the box
            if on:
                self.streamer.set_box_state("speaking", True, self._box_face[2], self._box_face[3])
            else:
                self.streamer.set_box_state(*self._box_face)
        face = next((x for x in getattr(self, "services", []) if x.name == "face"), None)
        if face is not None:                                        # and the box's own drawing (when the stream is down): its mouth runs from now
            self._spawn_mirror(self._face_action(face, "speaking" if on else "idle"))
        if on and self._mirror_next is not None:                    # the other pods, silently, at the moment it sounds
            text, etext = self._mirror_next
            self._mirror_next = None
            for other in self.others():
                self._spawn_mirror(other.mirror(text, etext, seconds))

    async def _face_action(self, face, state: str) -> None:
        try:
            await self.client.execute_service(face, {"state": state})
        except Exception as e:                                      # noqa: BLE001 — the mouth just doesn't move this time
            self.log(f"[{self.room.name}] (face: {type(e).__name__})")

    def _spawn_mirror(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _model_emotion(self, said: str, seconds: float) -> None:
        brain = getattr(self.room, "brain", None)
        sentences = emotion.split(plain(said))
        if brain is None or not hasattr(brain, "client") or not sentences or len(sentences) > 14:
            return
        # A filler line is said while the real answer is being written on the same model, so its codes came back after the 8 s and were
        # thrown away (12 "emotion model: TimeoutError" on 2026-11-01, every one after a filler). The codes of a line are kept: fillers come
        # round again, and a late answer still lands for next time.
        key = plain(said)
        codes = EMOTION_CACHE.get(key)
        try:
            if codes is None:
                def ask():
                    resp = brain.client.messages.create(model=brain.model, max_tokens=40, messages=[{"role": "user", "content": emotion.codes_prompt(sentences, self.asked)}])
                    got = emotion.parse_codes("".join(getattr(b, "text", "") for b in resp.content), len(sentences))
                    if got is not None:
                        if len(EMOTION_CACHE) >= EMOTION_CACHE_MAX:
                            EMOTION_CACHE.pop(next(iter(EMOTION_CACHE)))
                        EMOTION_CACHE[key] = got
                    return got
                job = asyncio.ensure_future(asyncio.to_thread(ask))
                try:
                    codes = await asyncio.wait_for(asyncio.shield(job), 8)
                except asyncio.TimeoutError:
                    return                                          # the word rules stay this time; the answer is kept when it comes
            if codes is None:
                return
            etext = emotion.reaction(self.asked) + "|" + emotion.track_from_codes(plain(said), codes, seconds)
            self.set_text("emote", etext)
            for other in self.others():
                if "emote" in other.keys:
                    other.set_text("emote", etext)
        except Exception as e:                                      # noqa: BLE001 — the word rules stay
            self.log(f"[{self.room.name}] (emotion model: {type(e).__name__})")

    async def _rhubarb(self, voice, mood: str) -> None:
        import tempfile                                             # noqa: PLC0415
        try:
            with tempfile.TemporaryDirectory() as d:
                wav, tsv = f"{d}/c.wav", f"{d}/c.tsv"
                ff = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-nostdin", "-y", "-i", "pipe:0", "-ac", "1", "-ar", "16000", wav,
                                                          stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(ff.communicate(voice[1]), 10)
                rb = await asyncio.create_subprocess_exec(str(RHUBARB), "-r", "phonetic", "-f", "tsv", "-o", tsv, wav,
                                                          stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(rb.wait(), 20)
                with open(tsv, encoding="utf-8") as f:
                    cues = [(float(a), b) for a, b in (ln.split("	") for ln in f.read().splitlines() if "	" in ln)]
            self.set_text("mouth", mood + shapes(cues, cap=249))
            for other in self.others():                             # the better mouth shapes, to the pods that are mirroring
                if "mouth" in other.keys:
                    other.set_text("mouth", mood + shapes(cues, cap=249))
        except Exception as e:                                      # noqa: BLE001 — the loudness digits stay
            self.log(f"[{self.room.name}] (rhubarb: {type(e).__name__})")

    async def show(self, text: str) -> None:
        if "subtitle" in self.keys:
            self.client.text_command(self.keys["subtitle"], plain(text)[:120])
            if self.streamer is not None:                           # the box adds the same line to its conversation text
                self.streamer.face.add_convo(1, plain(text)[:120])

    async def speak(self, said: str, voice, follow_up: bool = False) -> None:
        # Not inside the Captain's own turn: none is open, or the open one is the turn the box started BY ITSELF after an announcement (a quip
        # covering the wait), still only listening for its name. An answer sent as that turn's reply is dropped by the box: silent pod, the
        # PC outputs alone speak it (2026-10-31). Announce it, like the quip.
        if not self.run_live or self.stage == "wake":
            await self.announce(said, voice)
            return
        url = self.media.put_clip(voice, self.box_host) if voice else ""
        await self.mouth(voice, said)
        self._event(self.E.VOICE_ASSISTANT_INTENT_END, {"conversation_id": "", "continue_conversation": "0", "speech": said})
        self._event(self.E.VOICE_ASSISTANT_TTS_START, {"text": plain(said)})
        self._played.clear()
        if url:
            self._event(self.E.VOICE_ASSISTANT_TTS_END, {"url": url})
        self._follow = False
        self._event(self.E.VOICE_ASSISTANT_RUN_END)
        self.run_live, self.stage = False, ""
        if url:
            await self._wait_played(voice)

    async def _wait_played(self, voice) -> None:
        try:
            await asyncio.wait_for(self._played.wait(), min(self.wait_cap, _voice_seconds(voice) + 3))
        except asyncio.TimeoutError:
            pass

    async def announce(self, said: str, voice) -> None:
        if voice is None:
            return
        url = self.media.put_clip(voice, self.box_host)
        await self.mouth(voice, said)
        self.run_live, self.stage = False, ""                       # the box stops listening to announce, then starts a new turn by itself
        try:
            await self.client.send_voice_assistant_announcement_await_response(media_id=url, timeout=min(self.wait_cap, _voice_seconds(voice) + 10),
                                                                               text=said)
        except Exception as e:                                      # noqa: BLE001 — it timed out or the box was busy: it was said or it was not
            self.log(f"[{self.room.name}] (announcement: {type(e).__name__})")

    # ── the box as one of the house radio's speakers (radio_client.PodTuner hands it the live stream) ──
    async def play(self, url: str) -> None:
        if "player" in self.keys:
            self.client.media_player_command(self.keys["player"], media_url=url, announcement=False)

    async def stop(self) -> None:
        from aioesphomeapi import MediaPlayerCommand              # noqa: PLC0415
        if "player" in self.keys:
            self.client.media_player_command(self.keys["player"], command=MediaPlayerCommand.STOP)

    async def volume(self, step: float) -> None:
        """A pod's volume is locked (the Captain, 2026-10-31: full volume crackles; the house stereos have the controls): nothing to change."""
        return None


async def serve(hub, name: str, host: str, port: int, *, media, noise_psk: str, connect=None, label: str = "", make_client=None) -> None:
    """One box, for as long as it stays connected (hub.keep dials it again when it drops)."""
    if make_client is None:
        from aioesphomeapi import APIClient                        # noqa: PLC0415
        make_client = lambda: APIClient(host, port, None, noise_psk=noise_psk or None, client_info="skippy-hub")   # noqa: E731
    client = make_client()
    gone = asyncio.Event()

    async def on_stop(expected: bool) -> None:
        gone.set()

    await client.connect(on_stop=on_stop, login=True)
    room = None
    try:
        info = await client.device_info()
        entities, services = await client.list_entities_services()
        link = EsphomeLink(client, media, host, log=hub.log)
        link.discover(entities)
        link.session(False)                                     # a restarted hub knows of no conversation: the box must not keep its ears out for one that was open
        link.hub, link.services = hub, services
        room = hub.rooms[name] = hub.make_room(name, link)
        hub.log(f"[{name}] connected: {getattr(info, 'friendly_name', '') or getattr(info, 'name', '') or 'an ESPHome box'}")
        radio = getattr(hub.make_room, "radio", None)
        tuner = None
        if "player" in link.keys and radio is not None:            # the pod is a remote and a speaker of the house radio, never its player
            from radio_client import PodTuner                      # noqa: PLC0415
            link.music = room.music = tuner = PodTuner(link, radio, name, towards=host, log=hub.log)
            if connect is not None:
                connect.add(name, label or f"{name.replace('-', ' ').title()} (Skippy)", tuner, book=getattr(room, "book", None))
        client.subscribe_states(link.on_state)
        link.start_stream(os.environ.get("SATELLITE_FACE_STREAM", "1") != "0")
        unsub = client.subscribe_voice_assistant(handle_start=link.handle_start, handle_stop=link.handle_stop, handle_audio=link.handle_audio,
                                                 handle_announcement_finished=link.handle_announcement_finished)
        ticker = asyncio.ensure_future(hub.tick(room))
        tuning = asyncio.ensure_future(tuner.run()) if tuner is not None else None
        try:
            await gone.wait()
            hub.log(f"[{name}] disconnected")
        finally:
            ticker.cancel()
            if tuning is not None:
                tuning.cancel()                                     # never a stop: what the pod plays comes from the radio, not from us
            unsub()
            link.stop_stream()
    finally:
        if connect is not None:
            connect.remove(name)
        if room is not None:
            for task in list(room.tasks):
                task.cancel()
        await client.disconnect()
