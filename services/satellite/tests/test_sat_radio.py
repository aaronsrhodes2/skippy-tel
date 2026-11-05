"""Bilby's house radio (radio.py, station.py, radio_client.py): one station on skippy-pc, the hubs only its remotes. A hub restart never touches
the music, the radio's own restart resumes where it was, a place switches which speakers play the one station, every grammar command goes
through the radio's API, Spotify falls back to Bilby at once, and Skippy speaking in a room ducks only that room's speakers."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outputs  # noqa: E402
import radio  # noqa: E402
import radio_client  # noqa: E402
import router as rt  # noqa: E402
import station  # noqa: E402
from test_sat_router import World  # noqa: E402
from test_sat_spotify import FakeClient, FakeProc  # noqa: E402

CAT = {"Pink Floyd/Animals/01 Dogs.mp3": ("Dogs", "Pink Floyd", "Animals", "Rock", 1),
       "Pink Floyd/Animals/02 Pigs.mp3": ("Pigs", "Pink Floyd", "Animals", "Rock", 2),
       "Bauhaus/Flat Field/Dark Entries.mp3": ("Dark Entries", "Bauhaus", "In the Flat Field", "Goth", 3)}
ENV = {"SATELLITE_OUT": "living=pod+enclave", "SKIPPY_STEREO_OUT": "hdmi", "SKIPPY_SPOTIFY": "off"}


class Media:
    async def catalog(self):
        return CAT

    def track_url(self, path, start=0.0, towards=""):
        return f"http://radio/{path}?s={start:.1f}"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def world_router():
    w = World(present=("HDMI", "tcp://core:4954", "tap://pods"))
    r = rt.Router([("enclave", "tcp://core:4954", 0.0), ("hdmi", "HDMI", 0.0), ("pods", "tap://pods", 0.0)], find=w.find,
                  stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None, autostart=False)
    r.mix_once()
    return r, w


def make_house(clock=None, spotify=None, switches=None, mono=None, wall=None, **kw):
    """`switches`: where the speakers' switches are kept (a restart of the radio finds them there); `mono`: the clock of the hold and of a box covering;
    `wall`: the radio's wall clock (when it was held, how fresh the car's place is)."""
    r, w = world_router()
    sw = outputs.Outputs(["hdmi", "enclave"], ["living"], {"hdmi": "downstairs", "enclave": "upstairs", "pod:living": "upstairs"}, path=switches,
                         clock=mono or time.monotonic, seed=["hdmi"]) if (switches or mono) else None
    h = radio.HouseRadio(r, ["hdmi"], Media(), outputs=sw, mono=mono or time.monotonic, wall=wall or time.time, log=lambda *a: None, popen=FakeProc, clock=clock or Clock(),
                         spotify=spotify, **kw)
    return h, r


def on_loop():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def call(loop, coro, wait=10):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(wait)


def stop_all(loop, h):
    call(loop, h.box.pause())                                       # ends its router play (a waiting thread would hold the run open)
    loop.call_soon_threadsafe(loop.stop)


# ── the radio's own restart resumes where it was ──
def test_the_radio_restarting_resumes_the_same_track_at_the_same_place_on_the_same_speakers(tmp_path):
    loop, clock = on_loop(), Clock()
    h, r = make_house(clock, switches=tmp_path / "outputs.json")
    call(loop, h.box.play_queue(list(CAT), 1))
    h.place(["hdmi", "enclave"])                                    # "now upstairs too": the switches are kept on disk
    h.set_level(0.6)
    clock.t += 73.0
    rad = radio.Radio(ENV, h, state=tmp_path, log=lambda *a: None)
    rad.save()
    saved = json.loads((tmp_path / "station.json").read_text())
    assert saved["idx"] == 1 and saved["pos"] == 73.0 and saved["playing"] and saved["speakers"] == ["enclave", "hdmi"]
    stop_all(loop, h)

    loop2 = on_loop()
    h2, r2 = make_house(switches=tmp_path / "outputs.json")         # a new process: nothing in memory but the files
    assert call(loop2, h2.restore(rad.load(), log=lambda *a: None))
    assert h2.box.idx == 1 and h2.box.playing and h2.speakers == {"enclave", "hdmi"} and h2.level == 0.6
    args = r2.plays[max(r2.plays)].source.proc.args
    assert args[args.index("-i") + 1] == "http://radio/Pink Floyd/Animals/02 Pigs.mp3?s=73.0"           # the same track, 73 s in
    assert r2.plays[max(r2.plays)].targets == {"enclave", "hdmi"}
    stop_all(loop2, h2)


def test_a_radio_that_was_paused_comes_back_paused_with_its_queue(tmp_path):
    loop = on_loop()
    h, _ = make_house()
    call(loop, h.box.play_queue(list(CAT)))
    call(loop, h.box.pause())
    rad = radio.Radio(ENV, h, state=tmp_path, log=lambda *a: None)
    rad.save()
    h2, r2 = make_house()
    assert call(loop, h2.restore(rad.load(), log=lambda *a: None)) is False
    assert not h2.box.playing and h2.box.queue == list(CAT) and not r2.plays
    loop.call_soon_threadsafe(loop.stop)


# ── one station, the speakers switched by place ──
def test_a_place_switches_which_speakers_play_the_one_station():
    loop = on_loop()
    h, r = make_house()
    call(loop, h.box.play_queue(list(CAT)))
    play = r.plays[max(r.plays)]
    assert call(loop, station.run(h, "speakers", {"what": "music", "place": "everywhere"})).startswith("Done: the music plays everywhere")
    assert h.speakers == {"enclave", "hdmi"} and play.targets == {"enclave", "hdmi"}       # the same play: the same song, never a second one
    call(loop, station.run(h, "speakers", {"what": "music", "place": "upstairs"}))
    assert play.targets == {"enclave"} and len(r.plays) == 1
    call(loop, station.run(h, "speakers", {"what": "music", "place": "downstairs"}))
    assert play.targets == {"hdmi"}
    assert station.here_kinds(ENV, "living") == ["enclave"]                               # "here" in the living room: its Enclave
    assert station.here_kinds(ENV, "bedroom") == ["pod:bedroom"]                          # a room with only a pod: the pod's own speaker
    h.move(["pod:bedroom", "hdmi"])
    assert play.targets == {"pods", "hdmi"} and h.speakers == {"pod:bedroom", "hdmi"}
    open_, closed = h.where()
    assert open_ == {"pod:bedroom", "hdmi"} and not closed
    stop_all(loop, h)


def test_the_pods_tap_hands_the_stations_mix_to_every_pod_tuned_in():
    got = []
    tap = radio.Tap(lambda out, frames, t, s: out.__setitem__(slice(None), 0.25), start=False)
    a, b = tap.listen(), tap.listen()
    tap.once()
    got = [a.get_nowait(), b.get_nowait()]
    assert all(float(g.max()) == 0.25 and g.shape == (rt.BLOCK, 2) for g in got)
    tap.leave(a)
    tap.once()
    assert a.empty() and not b.empty()
    assert radio.live_secret("tok") == radio.live_secret("tok") != radio.live_secret("other")   # the same after a restart, no one guesses it


# ── every grammar command through the radio's API ──
def test_the_grammar_goes_through_the_radios_api_and_the_hub_only_asks(tmp_path):
    import brain
    import hub
    from aiohttp import web

    loop = on_loop()
    h, r = make_house()
    rad = radio.Radio(ENV, h, log=lambda *a: None, state=tmp_path)

    async def serve():
        runner = web.AppRunner(rad.app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, site._server.sockets[0].getsockname()[1]
    runner, port = call(loop, serve())
    client = radio_client.RadioClient(f"http://127.0.0.1:{port}", log=lambda *a: None, start=False)

    class Room:
        name = "living"
    hub_loop = on_loop()
    _, run = hub.music_tools(lambda: Room, hub_loop, stereo=lambda: client)
    said = run("play_music", brain.music_grammar("hey skippy, play goth songs downstairs on discovery mode"))
    assert said.startswith("Playing 1 track of goth on discovery downstairs"), said
    assert h.box.queue == ["Bauhaus/Flat Field/Dark Entries.mp3"] and h.speakers == {"hdmi"}
    assert run("play_music", {"what": "Pink Floyd", "queue": True}).startswith("Queued 2 tracks after this")
    assert len(h.box.queue) == 3
    assert run("music", {"action": "next"}) == "Done: next on the house radio." and h.box.idx == 1
    assert run("play_music", {"what": "Pink Floyd"}).startswith("Playing 2 tracks downstairs")     # no place said: the speakers stay as they are, asked from anywhere
    assert h.speakers == {"hdmi"}
    assert run("play_music", {"what": "Pink Floyd", "speaker": "here"}).startswith("Playing 2 tracks upstairs")     # "here", said, from the living room
    assert h.speakers == {"enclave"}
    assert client.call("GET", "/state")["title"] == "Dogs"
    call(loop, h.box.pause())
    call(loop, runner.cleanup())
    for lp in (loop, hub_loop):
        lp.call_soon_threadsafe(lp.stop)


def test_a_hub_with_no_radio_answering_says_so_and_never_raises():
    client = radio_client.RadioClient("http://127.0.0.1:9", log=lambda *a: None, start=False)
    assert client.tool("play_music", {"what": "Pink Floyd"}, "living") == radio_client.DOWN
    assert not client.playing and client.state() == {}


# ── a hub restart never touches the music ──
def test_a_hub_restart_never_stops_or_skips_the_station():
    """The hub's objects are the remote only: dropping them all (a hub restart) leaves the radio's play, its queue and its place as they were."""
    loop = on_loop()
    h, r = make_house()
    call(loop, h.box.play_queue(list(CAT)))
    play, idx = r.plays[max(r.plays)], h.box.idx

    class Link:                                                     # the pod: it remembers every command the hub sends
        sent: list = []
        player_playing = True

        async def play(self, url):
            self.sent.append(("play", url))

        async def stop(self):
            self.sent.append(("stop",))
    remote = radio_client.RadioClient("http://127.0.0.1:9", log=lambda *a: None, start=False)
    remote.ok, remote.last = True, {"playing": True, "speakers": ["pod:living"], "live": {"port": 47851, "path": "/live/x.flac"}}
    tuner = radio_client.PodTuner(Link(), remote, "living")

    async def hub_life():
        task = asyncio.ensure_future(tuner.run())
        await asyncio.sleep(0.05)
        task.cancel()                                               # the hub goes away (the serve's finally)
    call(loop, hub_life())
    del remote, tuner                                               # nothing of the hub is left
    assert Link.sent == []                                          # no stop, no replay: the pod went on with the radio's stream
    assert play.id in r.plays and not play.finished.is_set() and h.box.idx == idx and h.box.playing
    stop_all(loop, h)


# ── Spotify: local first, an instant fallback ──
def test_a_spotify_row_that_is_refused_moves_on_to_bilby_at_once():
    loop = on_loop()

    class Refusing(FakeClient):
        def play(self, uri, pos_ms=0, then=None):
            raise RuntimeError("429")
    h, r = make_house(spotify=Refusing())
    uri = "spotify:track:" + "z" * 22
    items = h.box.add_spotify([{"uri": uri, "title": "Not Mine", "artist": "Nobody"}])
    call(loop, h.box.play_queue(items + ["Pink Floyd/Animals/01 Dogs.mp3"]))
    for _ in range(50):
        if h.box.idx == 1:
            break
        call(loop, asyncio.sleep(0.02))
    assert h.box.idx == 1 and h.box.playing and "01 Dogs" in h.box.track()
    stop_all(loop, h)


def test_local_first_a_spotify_song_he_owns_plays_from_bilbys_file():
    loop = on_loop()
    h, r = make_house(spotify=FakeClient())
    call(loop, h.box.play_queue([]))                                # the titles loaded
    items = h.box.add_spotify([{"uri": "spotify:track:" + "d" * 22, "title": "Dark Entries", "artist": "Bauhaus"}])
    assert h.box.local(items) == ["Bauhaus/Flat Field/Dark Entries.mp3"]
    loop.call_soon_threadsafe(loop.stop)


# ── ducking: the whole stream, on every device (the Captain, 2026-11-05: Skippy's voice ducks it) ──
def test_skippy_speaking_anywhere_ducks_the_whole_stream_on_every_speaker_and_it_comes_back():
    loop = on_loop()
    h, r = make_house()
    r.play(rt.ClipSource(np.full((rt.RATE * 30, 2), 0.8, dtype="float32")), layer="media", targets={"hdmi", "enclave"})

    async def go():
        h.duck_stream("room:living", True, hold_s=0.3)
        for _ in range(12):
            r.mix_once()
        assert r._kind_gain["enclave"] == 0.25 and r._kind_gain["hdmi"] == 0.25 and r._kind_gain["pods"] == 0.25      # the Sony and the boxes too, whatever the room
        await asyncio.sleep(0.45)                                   # the hold ran out: back up
        for _ in range(12):
            r.mix_once()
        assert r._kind_gain["enclave"] == 1.0 and r._kind_gain["hdmi"] == 1.0
    call(loop, go())
    loop.call_soon_threadsafe(loop.stop)
    assert radio_client.room_kinds(ENV, "living") == ["pod:living", "enclave"]


def test_two_voices_at_once_are_holders_and_the_stream_stays_down_until_the_last_one_is_done():
    loop = on_loop()
    h, r = make_house()
    r.play(rt.ClipSource(np.full((rt.RATE * 30, 2), 0.8, dtype="float32")), layer="media", targets={"hdmi", "enclave"})

    async def go():
        h.duck_stream("room:living", True, hold_s=5.0)
        h.duck_stream("room:office", True, hold_s=0.2)              # a short one in another room ...
        await asyncio.sleep(0.3)                                    # ... runs out while the living room still talks
        for _ in range(12):
            r.mix_once()
        assert r._kind_gain["enclave"] == 0.25 and r._kind_gain["hdmi"] == 0.25      # it must not bring the stream back
        h.duck_stream("room:living", False)                         # the last one is done
        for _ in range(12):
            r.mix_once()
        assert r._kind_gain["enclave"] == 1.0 and r._kind_gain["hdmi"] == 1.0 and not h._duck_holders
        h.duck_stream("router", True, hold_s=0.2)                   # a hub that dies mid-sentence cannot leave it ducked: the hold expires by itself
        await asyncio.sleep(0.3)
        for _ in range(12):
            r.mix_once()
        assert r._kind_gain["enclave"] == 1.0
    call(loop, go())
    loop.call_soon_threadsafe(loop.stop)


def test_an_old_hub_naming_a_rooms_kinds_still_ducks_and_the_radio_endpoint_takes_a_holder():
    loop = on_loop()
    h, r = make_house()
    r.play(rt.ClipSource(np.full((rt.RATE * 30, 2), 0.8, dtype="float32")), layer="media", targets={"hdmi", "enclave"})

    async def go():
        h.duck_kinds(["pod:living", "enclave"], True, hold_s=0.2)    # a hub that has not been restarted yet
        for _ in range(12):
            r.mix_once()
        assert r._kind_gain["hdmi"] == 0.25
        await asyncio.sleep(0.3)
    call(loop, go())
    loop.call_soon_threadsafe(loop.stop)


def test_a_rooms_duck_is_posted_only_while_the_station_plays_and_names_the_room_as_the_holder():
    sent = []
    c = radio_client.RadioClient("http://x", log=lambda *a: None, start=False, request=lambda m, p, b=None, t=0: sent.append((p, b)) or {})
    room = c.for_room(["pod:living", "enclave"], "living")
    room.duck(True, 25)
    assert sent == []                                               # nothing plays: no call at all
    c.ok, c.last = True, {"playing": True}
    room.duck(True, 25)
    for _ in range(100):
        if sent:
            break
        threading.Event().wait(0.01)
    assert sent == [("/duck", {"holder": "room:living", "kinds": ["enclave", "pod:living"], "on": True, "hold_s": 25.0})]
