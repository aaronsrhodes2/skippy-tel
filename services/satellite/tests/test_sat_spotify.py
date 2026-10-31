"""His Spotify on the house stereo (spotify.py, stereo.py, music.BoxMusic, hub's `spotify` tool): one queue of Bilby paths and Spotify URIs, a plain
cut where the kind changes, a track's end moving the queue on, plays from his phone picked up, and the voice tool's words."""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hub  # noqa: E402
import music  # noqa: E402
import spotify  # noqa: E402
import stereo  # noqa: E402

A = "spotify:track:" + "a" * 22
B = "spotify:track:" + "b" * 22
C = "spotify:track:" + "c" * 22


class FakePlay:
    def __init__(self, source, layer, targets):
        self.source, self.layer, self.targets, self.gain = source, layer, set(targets), 1.0
        self.finished = threading.Event()


class FakeRouter:
    sinks = {"hdmi": object()}

    def __init__(self):
        self.plays: list[FakePlay] = []
        self.stopped: list[FakePlay] = []

    def available(self, kind):
        return True

    def play(self, source, layer="voice", targets=None, gain=1.0):
        p = FakePlay(source, layer, targets or ())
        p.gain = gain
        self.plays.append(p)
        return p

    def stop(self, p):
        self.stopped.append(p)
        p.finished.set()

    def live(self):
        return [p for p in self.plays if p not in self.stopped]


class FakeProc:
    def __init__(self, args, **_kw):
        self.args = args
        self.stdout = self

    def read(self, _n):
        threading.Event().wait(0.05)
        return b""

    def kill(self):
        pass


class FakeClient:
    pcm = "tcp://127.0.0.1:43872"

    def __init__(self):
        self.calls: list[tuple] = []
        self.status: dict = {"logged_in": True}
        self.lists = {"gym": {"playlist": {"id": "p1", "name": "Gym"}, "tracks": [{"uri": C, "title": "Lift", "artist": "Iron"}]}}

    def play(self, uri, pos_ms=0, then=None):
        self.calls.append(("play", uri, pos_ms))
        self.then = list(then or [])

    def pause(self):
        self.calls.append(("pause",))

    def now(self):
        return dict(self.status)

    def tracks_for(self, what, n=30):
        return {"kind": "artist", "name": "Daft Punk", "tracks": [{"uri": A, "title": "One More Time", "artist": "Daft Punk"},
                                                                  {"uri": B, "title": "Aerodynamic", "artist": "Daft Punk"}]}

    def playlist(self, name):
        if name.lower() not in self.lists:
            raise spotify.SpotifyError(f"none of his playlists is called {name!r}")
        return self.lists[name.lower()]

    def liked(self, n=200):
        return [{"uri": B, "title": "Liked", "artist": "Someone"}]

    def search(self, q, kind="track", n=10):
        self.calls.append(("search", q))
        return [{"uri": C, "title": "Money", "artist": "Pink Floyd"}]

    def add_to_playlist(self, playlist, uris):
        self.calls.append(("add", playlist, tuple(uris)))
        return {"name": "Gym"}

    def save(self, uris):
        self.calls.append(("save", tuple(uris)))

    def road(self, uri):
        self.calls.append(("road", uri))


class FakeMedia:
    async def catalog(self):
        return {"Pink Floyd/Money.flac": ("Money", "Pink Floyd", "DSOTM")}

    def track_url(self, track, offset, towards):
        return f"http://hub/{track}"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(client=None):
    router, clock = FakeRouter(), Clock()
    s = stereo.StereoMusic(router, ["hdmi"], FakeMedia(), log=lambda *_: None, clock=clock, popen=FakeProc,
                           spotify=client if client is not None else FakeClient())
    return s, router, clock


def run(coro):
    return asyncio.run(coro)


def test_spotify_audio_is_resampled_from_the_container_to_the_routers_rate():
    args = spotify.pcm_args("tcp://127.0.0.1:43872")
    assert args[args.index("-i") + 1] == "tcp://127.0.0.1:43872"
    assert args[args.index("-ar") + 1] == "44100" and args[-3:] == ["-ar", "48000", "pipe:1"]
    assert "-af" not in args and "-filter:a" not in args                # nothing done to their sound but the rate


def test_one_queue_mixes_bilby_and_spotify_with_a_plain_cut_between_them():
    async def go():
        s, router, clock = make()
        client = s.player.spotify
        items = s.box.add_spotify([{"uri": A, "title": "One More Time", "artist": "Daft Punk"}])
        await s.box.play_queue(["Pink Floyd/Money.flac"] + items)
        bilby = router.plays[-1]
        assert bilby.source.proc.args[bilby.source.proc.args.index("-i") + 1] == "http://hub/Pink Floyd/Money.flac"
        await s.box.next()                                                 # Bilby -> Spotify
        assert bilby in router.stopped                                     # cut, not faded
        stream = router.live()[-1]
        assert stream.layer == "media" and "tcp://127.0.0.1:43872" in stream.source.proc.args
        assert client.calls[-1] == ("play", A, 0)
        assert s.box.title() == ("One More Time", "Daft Punk")
        await s.box.prev()                                                 # back to Bilby: the Spotify stream is cut and the speaker paused
        assert stream in router.stopped and ("pause",) in client.calls
        await s.stop()
    run(go())


def test_the_next_spotify_item_is_started_just_before_this_one_ends_on_the_same_stream():
    async def go():
        s, router, clock = make()
        client = s.player.spotify
        s.box.add_spotify([{"uri": A, "title": "a", "artist": "x"}, {"uri": B, "title": "b", "artist": "y"}])
        await s.box.play_queue([A, B, "Pink Floyd/Money.flac"])
        assert client.calls[-1] == ("play", A, 0) and client.then == []              # one track at a time (the desktop app takes no lists)
        stream = router.live()[-1]
        client.status = {"logged_in": True, "playing": True, "uri": A, "pos_ms": 1000, "ms": 200000}
        await s.player.spotify_tick(client.now())
        client.status.update(pos_ms=199000)                                         # a second left: B is started, no wait for the album to go on
        await s.player.spotify_tick(client.now())
        assert client.calls[-1] == ("play", B, 0) and s.box.idx == 1 and s.box.title() == ("b", "y") and router.live() == [stream]
        await s.stop()
    run(go())


def test_the_album_going_on_by_itself_ends_the_track():
    async def go():
        s, router, clock = make()
        client = s.player.spotify
        s.box.add_spotify([{"uri": A, "title": "a", "artist": "x"}])
        await s.box.play_queue([A])
        client.status = {"logged_in": True, "playing": True, "uri": A, "pos_ms": 1000, "ms": 200000}
        await s.player.spotify_tick(client.now())
        clock.t += 200
        client.status.update(uri=C, pos_ms=300)                                     # the album's next track: ours ended, the queue is done
        await s.player.spotify_tick(client.now())
        assert not s.box.playing and ("pause",) in client.calls
    run(go())


def test_the_end_of_a_spotify_run_crossfades_into_the_next_bilby_track():
    async def go():
        s, router, clock = make()
        client = s.player.spotify
        s.box.add_spotify([{"uri": A, "title": "a", "artist": "x"}])
        await s.box.play_queue([A, "Pink Floyd/Money.flac"])
        stream = router.live()[-1]
        client.status = {"logged_in": True, "playing": True, "uri": A, "pos_ms": 1000, "ms": 200000}
        await s.player.spotify_tick(client.now())
        client.status.update(pos_ms=195000)                                          # 5 s left, inside the 6 s crossfade
        await s.player.spotify_tick(client.now())
        bilby = s.player.play_obj
        assert s.box.idx == 1 and bilby is not None and bilby is not stream
        assert stream not in router.stopped and bilby.gain < 0.5                    # both play: Spotify fading out, Bilby fading in
        for _ in range(30):
            clock.t += 0.5
            await asyncio.sleep(0.06)
        assert stream in router.stopped and bilby.gain == 1.0 and ("pause",) in client.calls
        await s.stop()
    run(go())


def test_bilby_hands_over_to_spotify_with_a_fade_when_the_dj_chain_asks():
    async def go():
        s, router, clock = make()
        s.box.add_spotify([{"uri": A, "title": "a", "artist": "x"}])
        await s.box.play_queue(["Pink Floyd/Money.flac", A])
        bilby = router.plays[-1]
        await s.player.handoff_to_spotify(4.0)
        assert s.box.idx == 1 and s.player.spotify.calls[-1] == ("play", A, 0) and bilby not in router.stopped
        for _ in range(20):
            clock.t += 0.5
            await asyncio.sleep(0.06)
        assert bilby in router.stopped
        await s.stop()
    run(go())


def test_spotify_moved_to_his_phone_is_let_go_not_pulled_back():
    async def go():
        s, router, clock = make()
        client = s.player.spotify
        await s.play_spotify([{"uri": A, "title": "a", "artist": "x"}, {"uri": B, "title": "b", "artist": "y"}])
        client.status = {"logged_in": True, "playing": True, "uri": A, "pos_ms": 1000, "ms": 200000}
        await s.player.spotify_tick(client.now())
        await s.player.spotify_tick({"logged_in": True, "playing": False, "stopped": True, "uri": "", "elsewhere": "Aaron's iPhone"})
        assert not s.box.playing and router.live() == [] and [c for c in client.calls if c[0] == "play"] == [("play", A, 0)]
    run(go())


def test_a_track_paused_at_its_end_counts_as_ended_but_a_mid_track_pause_does_not():
    async def go():
        s, router, clock = make()
        client = s.player.spotify
        await s.play_spotify([{"uri": A, "title": "a", "artist": "x"}, {"uri": B, "title": "b", "artist": "y"}])
        client.status = {"logged_in": True, "playing": True, "uri": A, "origin": "go-librespot", "pos_ms": 1000, "ms": 200000}
        await s.player.spotify_tick(client.now())
        clock.t += 60
        client.status = {"logged_in": True, "playing": False, "paused": True, "uri": A, "origin": "go-librespot", "pos_ms": 60000, "ms": 200000}
        await s.player.spotify_tick(client.now())
        assert s.box.idx == 0 and not s.box.playing                        # paused (from his phone): not the end
        client.status.update(playing=True, paused=False)                 # resumed from his phone
        await s.player.spotify_tick(client.now())
        assert s.box.playing
        client.status.update(playing=False, paused=True, pos_ms=199500)  # and stopped at its very end
        await s.player.spotify_tick(client.now())
        assert s.box.idx == 1
    run(go())


def test_a_track_that_never_starts_is_skipped():
    async def go():
        s, router, clock = make()
        await s.play_spotify([{"uri": A, "title": "a", "artist": "x"}, {"uri": B, "title": "b", "artist": "y"}])
        clock.t += 25
        await s.player.spotify_tick({"logged_in": True, "playing": False, "stopped": True, "uri": ""})
        assert s.box.idx == 1
    run(go())


def test_a_play_from_his_phone_on_the_skippy_speaker_reaches_the_stereo_and_stops_bilby():
    async def go():
        s, router, clock = make()
        await s.box.play_queue(["Pink Floyd/Money.flac"])
        bilby = router.plays[-1]
        st = {"logged_in": True, "playing": True, "uri": A, "title": "Song", "artist": "Band", "origin": "android", "pos_ms": 5000, "ms": 9000}
        await s.player.spotify_tick(st)
        assert bilby in router.stopped and len(router.live()) == 1
        assert s.box.state()["track"] == A and s.box.state()["title"] == "Song" and s.box.playing
        st.update(uri=B, title="Next")                                    # his phone's playlist goes on: followed, not fought
        await s.player.spotify_tick(st)
        assert s.box.track() == B and ("play", B, 0) not in s.player.spotify.calls
    run(go())


def test_nothing_playing_lets_the_router_go_quiet():
    async def go():
        s, router, clock = make()
        st = {"logged_in": True, "playing": True, "uri": A, "title": "Song", "artist": "Band", "origin": "android"}
        await s.player.spotify_tick(st)
        await s.stop()
        await s.player.spotify_tick({"logged_in": True, "playing": False, "paused": True, "uri": A, "origin": "android"})
        assert router.live() == []
    run(go())


def test_a_pods_own_speaker_skips_spotify_items():
    class Pod:
        def __init__(self):
            self.urls = []

        async def play(self, url):
            self.urls.append(url)

        async def stop(self):
            pass

    async def go():
        pod = Pod()
        box = music.BoxMusic(pod, FakeMedia(), log=lambda *_: None)
        await box.play_queue([A, "Pink Floyd/Money.flac"])
        assert pod.urls == ["http://hub/Pink Floyd/Money.flac"] and box.idx == 1
        await box.play_queue([A])
        assert not box.playing
    run(go())


def test_queue_for_finds_liked_songs_his_playlists_and_artists():
    c = FakeClient()
    assert spotify.queue_for(c, "my liked songs")[1] == "his liked songs"
    tracks, how = spotify.queue_for(c, "my gym playlist")
    assert tracks[0]["uri"] == C and how == "his playlist Gym"
    tracks, how = spotify.queue_for(c, "Daft Punk on Spotify")
    assert how == "Daft Punk" and len(tracks) == 2


class Room:
    def __init__(self, box):
        self.music, self.book, self.name, self.last_media = box, None, "office", "music"


def tool(s, box):
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    room = Room(box)
    _, runner = hub.music_tools(lambda: room, loop, favorites=None, stereo=lambda: s)
    return runner, room


def test_the_voice_tool_plays_an_artist_on_the_stereo_and_adds_the_song_to_a_playlist():
    s, router, clock = make()

    class Pod:
        async def play(self, url):
            pass

        async def stop(self):
            pass
    pod = music.BoxMusic(Pod(), FakeMedia(), log=lambda *_: None)
    runner, room = tool(s, pod)
    said = runner("spotify", {"action": "play", "what": "Daft Punk"})
    assert said.startswith("Playing 2 Spotify tracks from Daft Punk on the house stereo, starting with One More Time")
    assert s.box.queue == [A, B] and room.last_media == "stereo"
    said = runner("spotify", {"action": "add", "playlist": "gym"})
    assert said == "Added One More Time by Daft Punk to his Spotify playlist Gym." and ("add", "gym", (A,)) in s.player.spotify.calls
    said = runner("spotify", {"action": "like"})
    assert ("save", (A,)) in s.player.spotify.calls
    said = runner("music", {"action": "next"})                            # the stereo is what plays: "next" means it
    assert said == "Done: next on the house stereo." and s.box.idx == 1
    said = runner("play_music", {"what": "Pink Floyd", "queue": True})    # one queue: Bilby's after Spotify's
    assert s.box.queue == [A, B, "Pink Floyd/Money.flac"] and "after this on the house stereo" in said


def test_adding_a_bilby_song_to_a_spotify_playlist_uses_spotifys_copy():
    s, router, clock = make()

    class Pod:
        async def play(self, url):
            pass

        async def stop(self):
            pass
    pod = music.BoxMusic(Pod(), FakeMedia(), log=lambda *_: None)
    runner, room = tool(s, pod)
    asyncio.run(pod.play_queue(["Pink Floyd/Money.flac"]))
    said = runner("spotify", {"action": "add", "playlist": "gym"})
    assert said == "Added Money by Pink Floyd (Spotify's copy) to his Spotify playlist Gym."
    assert ("search", "track:Money artist:Pink Floyd") in s.player.spotify.calls


def test_favorite_this_names_the_stereo_when_that_is_what_plays():
    s, router, clock = make()
    sent = []

    class Favs:
        def mark(self, room, device=""):
            sent.append((room, device))
            return "ok"

    class Pod:
        async def play(self, url):
            pass

        async def stop(self):
            pass
    pod = music.BoxMusic(Pod(), FakeMedia(), log=lambda *_: None)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    room = Room(pod)
    _, runner = hub.music_tools(lambda: room, loop, favorites=Favs(), stereo=lambda: s)
    runner("spotify", {"action": "play", "what": "Daft Punk"})
    runner("music", {"action": "favorite"})
    assert sent[-1] == ("office", "skippyroom-stereo")


def test_every_spotify_track_that_starts_goes_into_his_road_playlist_once():
    async def go():
        s, router, clock = make()
        client = s.player.spotify
        await s.play_spotify([{"uri": A, "title": "a", "artist": "x"}])
        client.status = {"logged_in": True, "playing": True, "uri": A, "origin": "go-librespot", "pos_ms": 1000, "ms": 200000}
        await s.player.spotify_tick(client.now())
        await s.player.spotify_tick(client.now())
        await asyncio.sleep(0.1)
        assert client.calls.count(("road", A)) == 1
        await s.stop()
        await s.player.spotify_tick({"logged_in": True, "playing": True, "uri": B, "title": "b", "artist": "y", "origin": "android"})
        await asyncio.sleep(0.1)
        assert ("road", B) in client.calls                                # one his phone played on the Skippy speaker too
        await s.stop()
    run(go())
