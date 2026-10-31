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
        self.events, self.texts, self.media, self.announced = [], [], [], []
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


async def boot(spotter=True, connect=None):
    client, media, made, log = FakeClient(), FakeMedia(), {}, []

    def make_room(name, link):
        made["room"] = Room(name, link, made["gate"], made["brain"], made["timers"], spotter=made.get("spotter"), log=log.append)
        return made["room"]
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
    run(go())


# ── Bilby's music on the box ──
def test_take_plays_the_phones_queue_from_its_place_and_a_track_ending_moves_on():
    async def go():
        client, media, made, hub, task, log = await boot()
        box = made["room"].music
        await box.take({"queue": ["Pink Floyd/Animals/01 Dogs.mp3", "Pink Floyd/Animals/02 Pigs.mp3"], "idx": 0,
                        "track": "Pink Floyd/Animals/01 Dogs.mp3", "pos": 61.5})
        assert client.media[-1] == (1, {"media_url": "http://hub:10800/m/s/track.flac?p=Pink Floyd/Animals/01 Dogs.mp3&s=61.5", "announcement": False})
        assert client.texts[-1] == (2, "Dogs — Pink Floyd")
        client.on_states(MediaPlayerEntityState(key=1, state=MediaPlayerState.IDLE))      # within the settle time: our own track change
        await asyncio.sleep(0.02)
        assert box.idx == 0
        box._settle_until = 0
        client.on_states(MediaPlayerEntityState(key=1, state=MediaPlayerState.IDLE))      # the track ended
        await settle(lambda: box.idx == 1)
        assert "02 Pigs" in client.media[-1][1]["media_url"] and "&s=0.0" in client.media[-1][1]["media_url"]
        task.cancel()
    run(go())


def test_pause_stops_and_remembers_the_place_and_resume_starts_there():
    async def go():
        client, media, made, hub, task, log = await boot()
        box = made["room"].music
        t = [100.0]
        box.clock = lambda: t[0]
        await box.play_queue(["Queen/News/01 We Will Rock You.m4a"])
        t[0] += 42
        await box.command("pause", [])
        assert client.media[-1] == (1, {"command": MediaPlayerCommand.STOP}) and not box.playing and box.offset == 42.0
        await box.command("play", [])
        assert client.media[-1][1]["media_url"].endswith("&s=42.0") and box.playing
        st = box.state()
        assert st["title"] == "We Will Rock You" and st["artist"] == "Queen" and st["playing"] and st["queue"] == ["Queen/News/01 We Will Rock You.m4a"]
        assert "queue" not in box.state()                                             # only when it changed: the portal keeps the last one
        task.cancel()
    run(go())


def test_connect_checks_the_box_in_as_a_room_and_carries_out_a_take():
    async def go():
        posted = []
        phone = {"id": "phone1234", "state": {"queue": ["Queen/News/01 We Will Rock You.m4a"], "idx": 0, "track": "Queen/News/01 We Will Rock You.m4a", "pos": 3}}

        def post(url, body):
            posted.append((url, body))
            return {"commands": ["take:phone1234"] if len(posted) == 1 else [], "devices": [phone]}
        connect = music_mod.Connect("https://portal.example", "tok", post=post)
        client, media, made, hub, task, log = await boot(connect=connect)
        await connect.once()
        url, body = posted[0]
        assert url == "https://portal.example/home/music/checkin"
        assert body["device"] == "skippyroom-kitchen" and body["room"] == "box-kitchen" and body["name"] == "Kitchen" and body["state"]["queue"] == []
        assert "We Will Rock You" in client.media[-1][1]["media_url"]
        await connect.once()
        assert posted[1][1]["state"]["queue"] == ["Queen/News/01 We Will Rock You.m4a"] and posted[1][1]["state"]["playing"]
        task.cancel()
    run(go())


def test_a_portal_that_is_away_never_stops_the_music_and_gets_the_queue_again_when_back():
    async def go():
        calls = []

        def post(url, body):
            calls.append(body)
            if len(calls) == 1:
                raise ConnectionError("portal away")
            return {}
        connect = music_mod.Connect("https://p", "tok", post=post, log=lambda m: None)
        client, media, made, hub, task, log = await boot(connect=connect)
        await connect.once()
        await connect.once()
        assert "queue" in calls[0]["state"] and "queue" in calls[1]["state"]
        task.cancel()
    run(go())


def test_spoken_music_requests_find_his_tracks_and_the_tools_drive_this_rooms_box():
    cat = FakeMedia().cat
    assert music_mod.search(cat, "some Pink Floyd") == ["Pink Floyd/Animals/01 Dogs.mp3", "Pink Floyd/Animals/02 Pigs.mp3"]
    assert music_mod.search(cat, "pigs") == ["Pink Floyd/Animals/02 Pigs.mp3"]
    assert music_mod.search(cat, "news of the world") == ["Queen/News/01 We Will Rock You.m4a"]
    assert music_mod.search(cat, "the") == [] and music_mod.search(cat, "abba") == []

    async def go():
        client, media, made, hub, task, log = await boot()
        tools, run_tool = music_tools(lambda: made["room"], asyncio.get_running_loop())
        assert {t["name"] for t in tools} == {"music", "play_music"}
        said = await asyncio.to_thread(run_tool, "play_music", {"what": "Pink Floyd"})
        assert said == "Playing 2 tracks, starting with Dogs by Pink Floyd." and "Dogs" in client.media[-1][1]["media_url"]
        assert await asyncio.to_thread(run_tool, "music", {"action": "louder"}) == "Done: louder."
        assert client.media[-1] == (1, {"command": MediaPlayerCommand.VOLUME_UP})
        assert await asyncio.to_thread(run_tool, "play_music", {"what": "abba"}) == "Nothing in his library matches 'abba'."
        made["room"].music = None
        assert await asyncio.to_thread(run_tool, "music", {"action": "pause"}) == "This room has no speaker for music."
        task.cancel()
    run(go())


# ── the media server ──
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
