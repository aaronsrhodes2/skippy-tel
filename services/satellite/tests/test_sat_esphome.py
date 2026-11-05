"""The ESPHome room boxes against a simulated box: a fake aioesphomeapi client (the real library's own entity and state classes and enums), the
hub's real Room and EsphomeLink, and the voice-pipeline events checked in the order Home Assistant sends them. Plus Bilby's music on a box and
its Connect check-ins, the music tools the brain gets, and the media server over real HTTP (with a pass the real music gate accepts)."""
import asyncio
import importlib.util
import struct
import sys
from pathlib import Path

import pytest

aioesphomeapi = pytest.importorskip("aioesphomeapi")
from aioesphomeapi import (BinarySensorInfo, BinarySensorState, MediaPlayerCommand, MediaPlayerEntityState, MediaPlayerInfo,  # noqa: E402
                           MediaPlayerState, TextInfo, VoiceAssistantCommandFlag)
from aioesphomeapi import VoiceAssistantEventType as E  # noqa: E402

import esphome_link  # noqa: E402
import music as music_mod  # noqa: E402
from hub import Hub, Room, music_tools  # noqa: E402
from timers import Timers  # noqa: E402

from test_sat_hub import FakeBrain, FakeGate, FakeSpotter  # noqa: E402

QUIET, VOICE = 0.004, 0.12
WAKE = int(VoiceAssistantCommandFlag.USE_WAKE_WORD)


def pcm(level, ms):
    return struct.pack("<h", int(level * 32767)) * (16 * ms)


class FakeClient:
    """The parts of aioesphomeapi.APIClient the hub uses, recording what it sends."""

    def __init__(self):
        self.events, self.texts, self.media, self.announced, self.switches = [], [], [], [], []
        self.va = {}
        self.on_states = None
        self.on_stop = None
        self.disconnected = False

    async def connect(self, on_stop=None, login=False):
        self.on_stop = on_stop

    async def device_info(self):
        class Info:
            friendly_name = "Skippy Kitchen"
        return Info()

    async def list_entities_services(self):
        return [MediaPlayerInfo(object_id="speaker", key=1, name="Speaker"), TextInfo(object_id="subtitle", key=2, name="Subtitle"),
                BinarySensorInfo(object_id="tap", key=3, name="Tap")], []

    def subscribe_states(self, cb):
        self.on_states = cb

    def subscribe_voice_assistant(self, **callbacks):
        self.va = callbacks
        return lambda: None

    def send_voice_assistant_event(self, kind, data):
        self.events.append((kind, dict(data or {})))
        if kind == E.VOICE_ASSISTANT_TTS_END:                      # the box plays the answer, then says it is done
            asyncio.get_running_loop().call_later(0.01, lambda: asyncio.ensure_future(self.va["handle_announcement_finished"](None)))

    def text_command(self, key, state):
        self.texts.append((key, state))

    def media_player_command(self, key, **kw):
        self.media.append((key, kw))

    def switch_command(self, key, state):
        self.switches.append((key, state))

    async def send_voice_assistant_announcement_await_response(self, media_id, timeout, text=""):
        self.announced.append((media_id, text))

    async def disconnect(self):
        self.disconnected = True

    def kinds(self):
        return [k for k, _ in self.events]


class FakeMedia:
    def __init__(self):
        self.clips = []
        self.cat = {"Pink Floyd/Animals/01 Dogs.mp3": ("Dogs", "Pink Floyd", "Animals"),
                    "Pink Floyd/Animals/02 Pigs.mp3": ("Pigs", "Pink Floyd", "Animals"),
                    "Queen/News/01 We Will Rock You.m4a": ("We Will Rock You", "Queen", "News of the World")}

    def put_clip(self, voice, towards=""):
        self.clips.append(voice)
        return f"http://hub:10800/v/s/{len(self.clips)}.mp3"

    def track_url(self, path, start_s=0.0, towards=""):
        return f"http://hub:10800/m/s/track.flac?p={path}&s={start_s:.1f}"

    async def catalog(self):
        return self.cat


class FakeRadio:
    """radio_client.RadioClient as the pod sees it: the station's state, and what it was asked."""

    def __init__(self, playing=False, speakers=()):
        self.ok, self.calls = True, []
        self.last = {"playing": playing, "speakers": list(speakers), "track": "Pink Floyd/Animals/01 Dogs.mp3", "title": "Dogs",
                     "artist": "Pink Floyd", "pos": 12.0, "live": {"port": 47851, "path": "/live/abc.flac"}}

    def state(self):
        return dict(self.last)

    @property
    def playing(self):
        return bool(self.last["playing"])

    def tool(self, name, args, room=""):
        self.calls.append(("tool", name, args, room))
        return f"radio: {name}"

    async def control(self, action):
        self.calls.append(("control", action))
        return True

    async def take(self, state, room):
        self.calls.append(("take", state, room))


async def boot(spotter=True, connect=None, radio=None):
    client, media, made, log = FakeClient(), FakeMedia(), {}, []

    def make_room(name, link):
        made["room"] = Room(name, link, made["gate"], made["brain"], made["timers"], spotter=made.get("spotter"), log=log.append)
        return made["room"]
    make_room.radio = radio if radio is not None else FakeRadio()
    made["radio"] = make_room.radio
    made.update(gate=FakeGate(), brain=FakeBrain(), timers=Timers(clock=lambda: made.get("now", 0)), spotter=FakeSpotter() if spotter else None)
    hub = Hub([], make_room, log=log.append)
    task = asyncio.ensure_future(esphome_link.serve(hub, "kitchen", "10.0.0.5", 6053, media=media, noise_psk="k", connect=connect,
                                                    label="Kitchen", make_client=lambda: client))
    for _ in range(100):
        if client.va:
            break
        await asyncio.sleep(0.01)
    return client, media, made, hub, task, log


async def stream(client, level, ms):
    data = pcm(level, ms)
    for i in range(0, len(data), 1024):
        await client.va["handle_audio"](data[i:i + 1024])
    await asyncio.sleep(0.02)


async def settle(cond, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


def run(coro):
    asyncio.run(coro)


def test_the_box_is_found_its_parts_named_and_it_becomes_a_room_with_music():
    async def go():
        client, media, made, hub, task, log = await boot()
        room = made["room"]
        assert room.link.keys == {"player": 1, "subtitle": 2, "tap": 3} and room.music is not None
        assert any("Skippy Kitchen" in line for line in log)
        task.cancel()
    run(go())


def test_skippy_and_a_command_in_one_breath_runs_the_pipeline_as_home_assistant_would():
    async def go():
        client, media, made, hub, task, log = await boot()
        assert await client.va["handle_start"]("", WAKE, None, None) == 0             # 0: the audio comes over the API connection
        await stream(client, QUIET, 600)
        made["gate"].said.append("Skippy, turn the lights off.")
        await stream(client, VOICE, 400)
        made["spotter"].arm()
        await stream(client, VOICE, 1100)
        await stream(client, QUIET, 1500)
        await settle(lambda: E.VOICE_ASSISTANT_RUN_END in client.kinds())
        assert client.kinds() == [E.VOICE_ASSISTANT_RUN_START, E.VOICE_ASSISTANT_WAKE_WORD_START, E.VOICE_ASSISTANT_WAKE_WORD_END,
                                  E.VOICE_ASSISTANT_STT_START, E.VOICE_ASSISTANT_STT_VAD_END, E.VOICE_ASSISTANT_STT_END,
                                  E.VOICE_ASSISTANT_INTENT_START, E.VOICE_ASSISTANT_INTENT_END, E.VOICE_ASSISTANT_TTS_START,
                                  E.VOICE_ASSISTANT_TTS_END, E.VOICE_ASSISTANT_RUN_END]
        data = dict(client.events)
        assert data[E.VOICE_ASSISTANT_STT_END] == {"text": "turn the lights off."}
        assert data[E.VOICE_ASSISTANT_INTENT_END]["continue_conversation"] == "0" and data[E.VOICE_ASSISTANT_INTENT_END]["speech"] == "Done, monkey."
        assert data[E.VOICE_ASSISTANT_TTS_END] == {"url": "http://hub:10800/v/s/1.mp3"} and media.clips == [("mp3", b"mp3")]
        assert client.texts == [(2, "Done, monkey.")]                                 # the box's subtitle
        assert made["brain"].heard == ["turn the lights off."]
        await settle(lambda: made["room"].mode == "followup")    # the name opened a conversation: it stays open (Room.followup_ms) instead of going back to sleep after one answer
        task.cancel()
    run(go())


def test_the_box_starts_the_follow_up_turn_and_he_answers_without_the_name():
    async def go():
        client, media, made, hub, task, log = await boot()
        await client.va["handle_start"]("", 0, None, None)                            # continue_conversation: no wake word this time
        assert client.kinds()[-2:] == [E.VOICE_ASSISTANT_RUN_START, E.VOICE_ASSISTANT_STT_START] and made["room"].mode == "listening"
        await stream(client, QUIET, 300)
        made["gate"].said.append("Make it ten minutes.")
        await stream(client, VOICE, 1500)
        await stream(client, QUIET, 1500)
        await settle(lambda: made["brain"].heard == ["Make it ten minutes."])
        assert E.VOICE_ASSISTANT_STT_VAD_START in client.kinds()
        task.cancel()
    run(go())


def test_a_follow_up_with_nothing_said_ends_the_turn_so_the_box_listens_for_its_name_again():
    async def go():
        client, media, made, hub, task, log = await boot()
        await client.va["handle_start"]("", 0, None, None)
        await stream(client, QUIET, 9000)
        await settle(lambda: client.kinds()[-1] == E.VOICE_ASSISTANT_RUN_END)
        assert made["gate"].stt_calls == 0
        assert client.events[-2] == (E.VOICE_ASSISTANT_INTENT_END, {"conversation_id": "", "continue_conversation": "0", "speech": ""})
        task.cancel()
    run(go())


def test_goodbye_ends_the_turn_and_tells_the_box_not_to_carry_on_without_its_name():
    async def go():
        client, media, made, hub, task, log = await boot()
        await client.va["handle_start"]("", 0, None, None)
        await stream(client, QUIET, 300)
        made["gate"].said.append("Goodbye.")
        await stream(client, VOICE, 1200)
        await stream(client, QUIET, 1500)
        await settle(lambda: client.kinds()[-1] == E.VOICE_ASSISTANT_RUN_END)
        assert made["brain"].heard == [] and dict(client.events)[E.VOICE_ASSISTANT_INTENT_END]["continue_conversation"] == "0"
        task.cancel()
    run(go())


def test_room_talk_never_leaves_the_hub_and_a_tap_is_the_same_as_its_name():
    async def go():
        client, media, made, hub, task, log = await boot()
        await client.va["handle_start"]("", WAKE, None, None)
        await stream(client, QUIET, 600)
        await stream(client, VOICE, 1500)
        await stream(client, QUIET, 1500)
        assert made["gate"].stt_calls == 0 and E.VOICE_ASSISTANT_WAKE_WORD_END not in client.kinds()
        client.on_states(BinarySensorState(key=3, state=True))
        await settle(lambda: E.VOICE_ASSISTANT_WAKE_WORD_END in client.kinds())
        assert made["room"].mode == "listening"
        task.cancel()
    run(go())


def test_an_answer_after_a_quip_is_announced_not_sent_into_the_turn_the_box_opened_by_itself():
    """2026-10-31: a quip (an announcement) covers the wait; the box then opens a new turn on its own, listening for its name. The answer sent as
    that turn's reply was dropped by the box (a silent pod, the PC outputs alone spoke it). It must be announced, like the quip."""
    async def go():
        client, media, made, hub, task, log = await boot()
        link = made["room"].link
        link.run_live, link.stage = True, "wake"                   # the turn the box started by itself after the quip
        await link.speak("It is 13:16.", ("mp3", b"x"))
        assert client.announced and client.announced[-1][1] == "It is 13:16."
        assert E.VOICE_ASSISTANT_TTS_END not in client.kinds()
        task.cancel()
    run(go())


def test_the_name_and_a_pause_keeps_the_box_streaming_for_the_command():
    """2026-11-01: "Skippy" ... (a breath) ... "what time is it?". The name alone was a finished utterance; the hub told the box the voice had
    ended (STT_VAD_END: the box stops its microphone and waits for an answer), Whisper heard only the name, the room listened on, and the box
    sent nothing more: 8 s of silence, then the watchdog. The box is told only once there is something to answer."""
    async def go():
        client, media, made, hub, task, log = await boot()
        await client.va["handle_start"]("", WAKE, None, None)
        await stream(client, QUIET, 600)
        made["gate"].said.append("Skippy.")
        made["spotter"].arm()
        await stream(client, VOICE, 700)
        await stream(client, QUIET, 1500)
        await settle(lambda: made["gate"].stt_calls == 1 and made["room"].mode == "listening")
        assert E.VOICE_ASSISTANT_STT_VAD_END not in client.kinds() and E.VOICE_ASSISTANT_RUN_END not in client.kinds()
        made["gate"].said.append("What time is it?")
        await stream(client, VOICE, 1200)
        await stream(client, QUIET, 1500)
        await settle(lambda: made["brain"].heard == ["What time is it?"])
        assert client.kinds().count(E.VOICE_ASSISTANT_STT_VAD_END) == 1
        task.cancel()
    run(go())


def test_a_listening_window_counts_the_boxs_silence_from_when_it_opens():
    """The pod saying something stops its microphone for as long as it talks; the follow-up window after it must not end at once for the
    silence while it was talking (every part of the morning routine ended its follow-up that way)."""
    async def go():
        client, media, made, hub, task, log = await boot()
        room = made["room"]
        room._last_audio -= 30                                    # it talked for 30 s
        room._resume("followup", room.followup_ms)
        await room.timers_due()
        assert room.mode == "followup" and not any("no audio" in line for line in log)
        room._last_audio -= 9
        await room.timers_due()
        assert room.mode == "idle" and any("no audio from the box for 8 s" in line for line in log)
        assert client.kinds()[-1] == E.VOICE_ASSISTANT_RUN_END    # and the box is told to start its stream again
        task.cancel()
    run(go())


def test_a_box_gone_quiet_at_rest_is_restarted_then_switched_off_and_on():
    async def go():
        client, media, made, hub, task, log = await boot()
        room, link = made["room"], made["room"].link
        link.keys["mic"] = 9
        room._last_audio -= 25
        await room.timers_due()
        assert client.kinds()[-1] == E.VOICE_ASSISTANT_RUN_END and client.switches == []
        await room.timers_due()                                   # asked a moment ago: not again yet
        assert client.kinds().count(E.VOICE_ASSISTANT_RUN_END) == 1
        room._kicked_at -= 25
        await room.timers_due()
        assert client.switches == [(9, False), (9, True)]
        await client.va["handle_audio"](pcm(QUIET, 50))
        assert any("audio is back" in line for line in log) and room._kicked == 0
        task.cancel()
    run(go())


def test_a_box_with_its_microphone_switched_off_is_left_alone():
    async def go():
        client, media, made, hub, task, log = await boot()
        room, link = made["room"], made["room"].link
        from aioesphomeapi import SwitchState
        link.keys["mic"] = 9
        link.on_state(SwitchState(key=9, state=False))
        room._last_audio -= 60
        await room.timers_due()
        assert E.VOICE_ASSISTANT_RUN_END not in client.kinds() and client.switches == []
        task.cancel()
    run(go())


def test_a_timer_is_an_announcement_over_the_music():
    async def go():
        client, media, made, hub, task, log = await boot()
        made["timers"].add(60, "pasta")
        made["now"] = 61
        await made["room"].timers_due()
        assert client.announced == [("http://hub:10800/v/s/1.mp3", "Your pasta timer is done.")]
        task.cancel()
    run(go())


def test_a_dropped_box_ends_the_serve_so_the_hub_dials_it_again():
    async def go():
        connect = music_mod.Connect("https://portal", "tok", post=lambda url, body: {})
        client, media, made, hub, task, log = await boot(connect=connect)
        assert "kitchen" in connect.boxes
        await client.on_stop(False)
        await asyncio.wait_for(task, 2)
        assert client.disconnected and "kitchen" not in connect.boxes and "kitchen" in hub.rooms   # (hub.keep removes it from hub.rooms)
        assert not any("command" in m[1] for m in client.media)                                  # and the pod was never told to stop
    run(go())


# ── the pod as a speaker and a remote of the house radio (radio.py) ──
def test_the_pod_tunes_in_when_the_station_plays_on_it_and_out_when_it_moves_away():
    async def go():
        radio = FakeRadio(playing=True, speakers=["pod:kitchen"])
        client, media, made, hub, task, log = await boot(radio=radio)
        tuner = made["room"].music
        tuner.on = False                                                     # (its first look done)
        await tuner.tick()
        url = client.media[-1][1]["media_url"]
        assert url.endswith(":47851/live/abc.flac?room=kitchen") and client.media[-1][1]["announcement"] is False      # the room says which box listens
        await tuner.tick()
        assert len(client.media) == 1                                        # tuned in once, not again every tick
        radio.last["speakers"] = ["enclave"]                                 # "move the music upstairs"
        await tuner.tick()
        assert client.media[-1] == (1, {"command": MediaPlayerCommand.STOP}) and not tuner.on
        task.cancel()
    run(go())


def test_a_hub_that_comes_back_leaves_a_pod_that_is_still_playing_the_station_alone():
    """The Captain's rule: a hub restart never stops or skips the music. The pod's stream comes from the radio, so a new hub finds it playing."""
    async def go():
        radio = FakeRadio(playing=True, speakers=["pod:kitchen"])
        client, media, made, hub, task, log = await boot(radio=radio)
        client.on_states(MediaPlayerEntityState(key=1, state=MediaPlayerState.PLAYING))
        tuner = made["room"].music
        await tuner.tick()
        assert tuner.on is True and client.media == [] and any("left alone" in line for line in log)
        client.on_states(MediaPlayerEntityState(key=1, state=MediaPlayerState.IDLE))   # the radio itself restarted: the stream dropped
        await asyncio.sleep(0.02)
        await tuner.tick()
        assert client.media[-1][1]["media_url"].endswith("/live/abc.flac?room=kitchen")         # tuned in again
        task.cancel()
    run(go())


def test_connect_checks_the_pod_in_as_a_remote_and_a_take_puts_the_station_in_its_room():
    async def go():
        posted = []
        phone = {"id": "phone1234", "state": {"queue": ["Queen/News/01 We Will Rock You.m4a"], "idx": 0, "track": "Queen/News/01 We Will Rock You.m4a", "pos": 3}}

        def post(url, body):
            posted.append((url, body))
            return {"commands": ["take:phone1234", "next"] if len(posted) == 1 else [], "devices": [phone]}
        connect = music_mod.Connect("https://portal.example", "tok", post=post)
        client, media, made, hub, task, log = await boot(connect=connect)
        await connect.once()
        url, body = posted[0]
        assert url == "https://portal.example/home/music/checkin"
        assert body["device"] == "skippyroom-kitchen" and body["room"] == "box-kitchen" and body["name"] == "Kitchen" and body["state"]["queue"] == []
        assert made["radio"].calls == [("take", phone["state"], "kitchen"), ("control", "next")]
        assert client.media == []                                            # the pod never plays a track itself
        task.cancel()
    run(go())


def test_the_music_tools_are_the_radios_with_the_asking_room():
    cat = FakeMedia().cat
    assert music_mod.search(cat, "some Pink Floyd") == ["Pink Floyd/Animals/01 Dogs.mp3", "Pink Floyd/Animals/02 Pigs.mp3"]
    assert music_mod.search(cat, "pigs") == ["Pink Floyd/Animals/02 Pigs.mp3"]
    assert music_mod.search(cat, "news of the world") == ["Queen/News/01 We Will Rock You.m4a"]
    assert music_mod.search(cat, "the") == [] and music_mod.search(cat, "abba") == []

    async def go():
        client, media, made, hub, task, log = await boot()
        tools, run_tool = music_tools(lambda: made["room"], asyncio.get_running_loop(), stereo=lambda: made["radio"])
        assert {t["name"] for t in tools} == {"music", "play_music", "spotify", "dj_mode", "speakers", "audiobook"}      # his books are the radio's too
        assert await asyncio.to_thread(run_tool, "play_music", {"what": "Pink Floyd"}) == "radio: play_music"
        assert await asyncio.to_thread(run_tool, "music", {"action": "favorite"}) == "radio: music"
        assert made["radio"].calls == [("tool", "play_music", {"what": "Pink Floyd"}, "kitchen"), ("tool", "music", {"action": "favorite"}, "kitchen")]
        assert client.media == []
        task.cancel()
    run(go())


def _gate():
    root = Path(__file__).resolve().parents[2] / "music-gate"
    pytest.importorskip("flask")
    sys.path.insert(0, str(root))
    try:
        spec = importlib.util.spec_from_file_location("music_gate_gate_for_sat", root / "gate.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(root))


def test_the_hubs_music_pass_is_one_the_real_music_gate_accepts():
    import time

    from media import music_key, music_pass
    gate = _gate()
    now = time.time()
    token = "the-home-token"
    assert gate.valid(gate.gate_key(token), music_pass(music_key(token), now), now)
    assert not gate.valid(gate.gate_key("another-token"), music_pass(music_key(token), now), now)


def test_the_media_server_serves_clips_and_transcoded_tracks_only_with_its_secret(unused_port=None):
    aiohttp = pytest.importorskip("aiohttp")
    import socket

    from media import MediaServer, ffmpeg_args

    class Proc:
        def __init__(self, args):
            self.args, self.returncode = args, None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b"fLaC" + b"\0" * 100)
            self.stdout.feed_eof()

        def kill(self):
            self.returncode = -9

        async def wait(self):
            self.returncode = self.returncode or 0

    spawned = []

    async def spawn(*args, **kw):
        spawned.append(args)
        return Proc(args)

    async def go():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        m = MediaServer(port=port, host="127.0.0.1", gate="http://127.0.0.1:3005", home_token="tok", spawn=spawn)
        await m.start()
        try:
            url = m.put_clip(("mp3", b"ID3voice"))
            wav = m.put_clip(("pcm", b"\0\0" * 160, 16000))
            async with aiohttp.ClientSession() as http:
                async with http.get(url) as r:
                    assert r.status == 200 and await r.read() == b"ID3voice" and r.headers["Content-Type"] == "audio/mpeg"
                async with http.get(wav) as r:
                    assert r.status == 200 and (await r.read())[:4] == b"RIFF"
                async with http.get(url.replace(m.secret, "guess")) as r:
                    assert r.status == 404
                track = m.track_url("Queen/News/01 We Will Rock You.m4a", 12)
                async with http.get(track) as r:
                    assert r.status == 200 and (await r.read())[:4] == b"fLaC"
                async with http.get(m.track_url("../../etc/passwd")) as r:
                    assert r.status == 404
        finally:
            await m.stop()
        args = spawned[0]
        assert args[:6] == ("ffmpeg", "-v", "error", "-nostdin", "-ss", "12.0")
        assert "http://127.0.0.1:3005/t/" in args[7] and args[7].endswith("/a/Queen/News/01%20We%20Will%20Rock%20You.m4a")
        assert list(args[-10:]) == ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", "-f", "flac", "pipe:1"]
        assert "-ss" not in ffmpeg_args("x", 0)
    asyncio.run(go())
