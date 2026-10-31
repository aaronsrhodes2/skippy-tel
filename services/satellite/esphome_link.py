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
from pathlib import Path

PLAY_TIMEOUT_S = 90


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
    hub_follows_up = False                                          # the box does it (continue_conversation)

    def __init__(self, client, media, box_host: str, log=print, wait_cap: float = PLAY_TIMEOUT_S):
        from aioesphomeapi import VoiceAssistantEventType as E    # noqa: PLC0415
        self.E, self.client, self.media, self.box_host, self.log, self.wait_cap = E, client, media, box_host, log, wait_cap
        self.room = None
        self.music = None
        self.keys: dict[str, int] = {}                              # player, subtitle, tap, mic
        self.run_live, self.stage, self.vad_ended = False, "", False
        self._played = asyncio.Event()
        self._follow = False
        self._tasks: set = set()

    def discover(self, entities) -> None:
        for e in entities:
            kind, oid = type(e).__name__, str(getattr(e, "object_id", "") or "").lower()
            if kind == "MediaPlayerInfo":
                self.keys.setdefault("player", e.key)
            elif kind == "TextInfo" and "subtitle" in oid:
                self.keys["subtitle"] = e.key
            elif kind == "TextInfo" and "mouth" in oid:
                self.keys["mouth"] = e.key
            elif kind == "BinarySensorInfo" and oid.endswith("tap"):
                self.keys["tap"] = e.key

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
        await self.room.begin(remote_wake=remote)
        return 0                                                    # 0: the audio comes over the API connection itself

    async def handle_audio(self, data: bytes, data2: bytes | None = None) -> None:
        self.room.feed_pcm(data)

    async def handle_stop(self, abort: bool) -> None:
        self.room.stream_ended()
        if abort:
            self.run_live = False

    async def handle_announcement_finished(self, finished) -> None:
        self._played.set()

    def on_state(self, state) -> None:
        kind = type(state).__name__
        if kind == "BinarySensorState" and state.key == self.keys.get("tap") and state.state:
            self.room.tap()
        elif kind == "MediaPlayerEntityState" and state.key == self.keys.get("player") and self.music is not None:
            from aioesphomeapi import MediaPlayerState            # noqa: PLC0415
            if state.state == MediaPlayerState.IDLE:
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
        await self.voice_stopped()                                  # (heard in one breath with the name: the box must still stop listening)
        self._event(self.E.VOICE_ASSISTANT_STT_END, {"text": text})
        self._event(self.E.VOICE_ASSISTANT_INTENT_START)
        self.stage = "intent"

    async def released(self) -> None:
        """Nothing (more) to say: end the turn; the box starts listening for its name again by itself (start_continuous)."""
        if self.run_live and self.stage != "wake":
            # The box keeps the last turn's continue_conversation: say 0, or after a "goodbye" it would start its next turn without the name
            self._event(self.E.VOICE_ASSISTANT_INTENT_END, {"conversation_id": "", "continue_conversation": "0", "speech": ""})
            self._event(self.E.VOICE_ASSISTANT_RUN_END)
            self.run_live, self.stage = False, ""

    async def mouth(self, voice, said: str = "") -> None:
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
            self.client.text_command(self.keys["mouth"], mood + envelope(pcm, cap=249))
        except Exception as e:                                      # noqa: BLE001 — the mouth just babbles
            self.log(f"[{self.room.name}] (mouth envelope: {type(e).__name__})")
            return
        if RHUBARB.exists():
            task = asyncio.create_task(self._rhubarb(voice, mood))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

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
            self.client.text_command(self.keys["mouth"], mood + shapes(cues, cap=249))
        except Exception as e:                                      # noqa: BLE001 — the loudness digits stay
            self.log(f"[{self.room.name}] (rhubarb: {type(e).__name__})")

    async def show(self, text: str) -> None:
        if "subtitle" in self.keys:
            self.client.text_command(self.keys["subtitle"], plain(text)[:120])

    async def speak(self, said: str, voice, follow_up: bool = False) -> None:
        if not self.run_live:
            await self.announce(said, voice)
            return
        url = self.media.put_clip(voice, self.box_host) if voice else ""
        await self.mouth(voice, said)
        self._event(self.E.VOICE_ASSISTANT_INTENT_END, {"conversation_id": "", "continue_conversation": "1" if follow_up else "0", "speech": said})
        self._event(self.E.VOICE_ASSISTANT_TTS_START, {"text": plain(said)})
        self._played.clear()
        if url:
            self._event(self.E.VOICE_ASSISTANT_TTS_END, {"url": url})
        self._follow = follow_up and bool(url)
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

    # ── the box as Bilby's speaker (music.BoxMusic's player) ──
    async def play(self, url: str) -> None:
        if "player" in self.keys:
            self.client.media_player_command(self.keys["player"], media_url=url, announcement=False)

    async def stop(self) -> None:
        from aioesphomeapi import MediaPlayerCommand              # noqa: PLC0415
        if "player" in self.keys:
            self.client.media_player_command(self.keys["player"], command=MediaPlayerCommand.STOP)

    async def volume(self, step: float) -> None:
        from aioesphomeapi import MediaPlayerCommand              # noqa: PLC0415
        if "player" in self.keys:
            self.client.media_player_command(self.keys["player"], command=MediaPlayerCommand.VOLUME_UP if step > 0 else MediaPlayerCommand.VOLUME_DOWN)


async def serve(hub, name: str, host: str, port: int, *, media, noise_psk: str, connect=None, label: str = "", make_client=None) -> None:
    """One box, for as long as it stays connected (hub.keep dials it again when it drops)."""
    from music import BoxMusic                                     # noqa: PLC0415
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
        entities, _ = await client.list_entities_services()
        link = EsphomeLink(client, media, host, log=hub.log)
        link.discover(entities)
        room = hub.rooms[name] = hub.make_room(name, link)
        hub.log(f"[{name}] connected: {getattr(info, 'friendly_name', '') or getattr(info, 'name', '') or 'an ESPHome box'}")
        if "player" in link.keys:
            link.music = room.music = BoxMusic(link, media, towards=host, log=hub.log, name=name)
            if connect is not None:
                connect.add(name, label or f"{name.replace('-', ' ').title()} (Skippy)", room.music)
        client.subscribe_states(link.on_state)
        unsub = client.subscribe_voice_assistant(handle_start=link.handle_start, handle_stop=link.handle_stop, handle_audio=link.handle_audio,
                                                 handle_announcement_finished=link.handle_announcement_finished)
        ticker = asyncio.ensure_future(hub.tick(room))
        try:
            await gone.wait()
            hub.log(f"[{name}] disconnected")
        finally:
            ticker.cancel()
            unsub()
    finally:
        if connect is not None:
            connect.remove(name)
        if room is not None:
            for task in list(room.tasks):
                task.cancel()
        await client.disconnect()
