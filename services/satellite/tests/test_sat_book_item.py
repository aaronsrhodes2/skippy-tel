"""An audiobook is an item of the house radio's queue (the Captain, 2026-11-05: "Bilby plays all the audio, even Audible books"). The book engine (audiobooks.py: the place,
the chapters, the speed, the sleep timer) lives in the radio and plays through the station's router, so pause, resume, next, volume, what's playing and Skippy's ducking are the
station's; a hub holds no player any more, so a hub restart cannot stop a book, and the radio's own restart resumes it at the place the store kept."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audiobooks as A  # noqa: E402
import dj  # noqa: E402
import music  # noqa: E402
import radio  # noqa: E402
import station  # noqa: E402
from test_sat_radio import CAT, ENV, Clock, call, make_house, on_loop, stop_all  # noqa: E402

BOOKS = {"Dragonflight.m4b": {"duration": 12.0, "tags": {"title": "Dragonflight", "artist": "Anne McCaffrey"},
                              "chapters": [{"title": "The Weyr", "start": 0, "end": 4}, {"title": "Ruth", "start": 4, "end": 8}, {"title": "Thread", "start": 8, "end": 12}]},
         "Aces.m4b": {"duration": 20.0, "tags": {"title": "Aces", "artist": "A. Writer"}, "chapters": []}}
DRAGON = "dragonflight-m4b"


def probe(path):
    return BOOKS.get(Path(path).name, {})


def make_station(tmp_path, switches=None, mono=None):
    """The radio's station with its book engine, built the way radio.build does it: on the station's output, a fake clock for the engine (and the radio's wall)."""
    wall = Clock()
    h, r = make_house(switches=switches, mono=mono, wall=wall)
    root = tmp_path / "books"
    root.mkdir(exist_ok=True)
    for name in BOOKS:
        (root / name).write_bytes(b"x")
    lib = A.Library(root, tmp_path / "lib.json", probe=probe, log=lambda *a: None)
    lib.refresh(force=True)
    store = A.Store(tmp_path / "audiobooks.json", wall=wall, mono=wall, inbox=tmp_path / "inbox")
    h.attach_books(lambda out, on_end, on_halt: A.BookPlayer(lib, store, pc_outs=[out], clock=wall, wall=wall, log=lambda *a: None, name="stereo", on_end=on_end, on_halt=on_halt))
    return h, r, h.player.books, wall


def play_args(r):
    return r.plays[max(r.plays)].source.proc.args


def test_a_book_is_an_item_of_the_queue_and_plays_through_the_stations_router(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    eng.store.put(DRAGON, pos=5.0, chapter=1, speed=1.25, last_played=1, finished=False)       # his place, kept in the store
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    assert h.box.playing and eng.playing and h.box.kind() == "book"
    play = r.plays[max(r.plays)]
    args = play_args(r)
    assert args[args.index("-i") + 1].endswith("Dragonflight.m4b") and args[args.index("-ss") + 1] == "5.00" and "atempo=1.25" in " ".join(args)
    assert play.layer == "media" and play.targets == {"hdmi"}                                     # the station's layer and the station's speakers
    st = h.box.state()
    assert st["track"] == "" and st["kind"] == "book" and st["title"] == "Dragonflight" and st["artist"] == "Anne McCaffrey"
    assert st["queue"] == [A.item(DRAGON)] and st["titles"] == {A.item(DRAGON): ["Dragonflight", "Anne McCaffrey"]}      # a book is never a `track`: the car would play it as a song
    wall.t += 4.0
    assert h.box.pos() == pytest.approx(10.0, abs=0.2)                                            # 5 s + 4 s at 1.25 times: the book's clock, in book seconds
    stop_all(loop, h)


def test_pause_is_the_stations_pause_and_resume_carries_on_at_the_books_place(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    wall.t += 8.0
    call(loop, h.box.pause())
    assert not h.box.playing and not eng.playing and not r.plays                                   # one stop: the book's play is gone
    assert eng.store.progress(DRAGON)["pos"] == 8.0 and h.box.offset == 8.0                        # and its place is kept, per book, in the store
    wall.t += 40.0
    call(loop, h.box.resume())
    assert h.box.playing and eng.playing and play_args(r)[play_args(r).index("-ss") + 1] == "5.00"    # a long pause picks up a few seconds earlier
    stop_all(loop, h)


def test_next_and_previous_are_chapters_and_the_last_chapter_goes_on_to_the_next_item(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON), "Pink Floyd/Animals/01 Dogs.mp3"]))
    call(loop, h.box.next())
    assert h.box.idx == 0 and eng.chapter_index() == 1 and play_args(r)[play_args(r).index("-ss") + 1] == "4.00"
    wall.t += 7.0
    call(loop, h.box.prev())                                                                       # well into the chapter: back to its start
    assert eng.chapter_index() == 1 and play_args(r)[play_args(r).index("-ss") + 1] == "4.00"
    call(loop, h.box.prev())                                                                       # at its start: the chapter before
    assert eng.chapter_index() == 0
    call(loop, h.box.next())
    call(loop, h.box.next())
    assert eng.chapter_index() == 2
    call(loop, h.box.next())                                                                       # the last chapter: the queue goes on
    assert h.box.idx == 1 and h.box.kind() == "song" and not eng.playing
    stop_all(loop, h)


def test_a_book_that_ends_hands_the_stream_to_the_next_item_and_a_last_item_pauses(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON), "Pink Floyd/Animals/01 Dogs.mp3"]))
    wall.t += 100.0
    call(loop, eng.tick())                                                                         # the clock passed its end
    assert eng.store.progress(DRAGON)["finished"] and h.box.idx == 1 and h.box.playing and h.box.kind() == "song"
    call(loop, h.box.pause())
    call(loop, h.box.play_queue([A.item("aces-m4b")]))
    wall.t += 100.0
    call(loop, eng.tick())
    assert not h.box.playing and h.box.offset == 0.0                                               # nothing after it: the station rests
    stop_all(loop, h)


def test_the_sleep_timer_stops_the_book_and_the_station_knows(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    eng.set_sleep(1)
    wall.t += 61.0
    call(loop, eng.tick())
    assert not eng.playing and not h.box.playing and h.box.offset == eng.offset                    # the engine stopped itself and told the queue
    stop_all(loop, h)


def test_music_started_over_a_book_pauses_it_with_its_place_and_read_my_book_carries_on(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    wall.t += 6.0
    assert call(loop, station.run(h, "play_music", {"what": "Pink Floyd"}, "living", None)).startswith("Playing 2 tracks")
    assert h.box.kind() == "song" and not eng.playing and eng.store.progress(DRAGON)["pos"] == 6.0      # replaced, not lost
    said = call(loop, station.run(h, "audiobook", {"action": "play"}, "living"))
    assert "Playing Dragonflight by Anne McCaffrey, chapter 2 of 3" in said and h.box.kind() == "book" and eng.playing
    assert play_args(r)[play_args(r).index("-ss") + 1] == "6.00"
    stop_all(loop, h)


def test_the_audiobook_tool_is_the_stations_one_stop_and_start(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    assert "Aces" in call(loop, station.run(h, "audiobook", {"action": "list"}, "living"))
    said = call(loop, station.run(h, "audiobook", {"action": "play", "title": "dragon"}, "living"))
    assert "Playing Dragonflight" in said and h.box.playing and h.box.queue == [A.item(DRAGON)]
    assert station.now_playing(h) == "Playing on the house radio: the audiobook Dragonflight by Anne McCaffrey, chapter 1 of 3."
    wall.t += 2.0
    assert call(loop, station.run(h, "music", {"action": "pause"}, "living", None)) == "Done: pause on the house radio."      # the music tool's pause stops the book
    assert not eng.playing and not h.box.playing
    said = call(loop, station.run(h, "audiobook", {"action": "resume"}, "living"))
    assert "Continuing Dragonflight" in said and eng.playing
    wall.t += 3.0
    assert "chapter 2 of 3, Ruth, 1 second in" in call(loop, station.run(h, "audiobook", {"action": "where"}, "living"))
    call(loop, station.run(h, "play_music", {"what": "Pink Floyd"}, "living", None))
    assert call(loop, station.run(h, "audiobook", {"action": "pause"}, "living")) == "Done: paused the house radio."        # music plays: the book's pause is the station's
    assert not h.box.playing
    stop_all(loop, h)


def test_the_radio_restarting_mid_book_resumes_it_at_the_store_place_and_a_hub_restart_changes_nothing(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    wall.t += 7.0
    call(loop, eng.tick())                                                                         # the engine saves its place every few seconds
    rad = radio.Radio(ENV, h, state=tmp_path, log=lambda *a: None)
    rad.save()
    saved = json.loads((tmp_path / "station.json").read_text())
    assert saved["queue"] == [A.item(DRAGON)] and saved["playing"]
    play_before = r.plays[max(r.plays)]
    # the hub is only a remote: dropping every object of it (a hub restart) leaves the book playing
    remote = radio_client_for(rad)
    del remote
    assert play_before.id in r.plays and h.box.playing and eng.playing
    stop_all(loop, h)
    loop2 = on_loop()
    h2, r2, eng2, wall2 = make_station(tmp_path)                                                    # a new process: nothing in memory but the files
    assert call(loop2, h2.restore(rad.load(), log=lambda *a: None))
    assert h2.box.playing and eng2.playing and play_args(r2)[play_args(r2).index("-ss") + 1] == "7.00"
    stop_all(loop2, h2)


def radio_client_for(rad):
    import radio_client                                                                              # noqa: PLC0415
    return radio_client.RadioClient("http://127.0.0.1:9", log=lambda *a: None, start=False)


def test_a_paused_book_comes_back_paused_as_the_loaded_book(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    wall.t += 5.0
    call(loop, h.box.pause())
    rad = radio.Radio(ENV, h, state=tmp_path, log=lambda *a: None)
    rad.save()
    h2, r2, eng2, _ = make_station(tmp_path)
    assert call(loop, h2.restore(rad.load(), log=lambda *a: None)) is False
    assert not h2.box.playing and h2.box.kind() == "book" and eng2.book["id"] == DRAGON and h2.box.title() == ("Dragonflight", "Anne McCaffrey") and not r2.plays
    loop.call_soon_threadsafe(loop.stop)


def test_a_missing_book_is_skipped_for_the_next_item_never_silence(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item("no-such-book"), "Pink Floyd/Animals/01 Dogs.mp3"]))
    for _ in range(50):
        if h.box.idx == 1:
            break
        call(loop, asyncio.sleep(0.02))
    assert h.box.idx == 1 and h.box.kind() == "song" and h.box.playing
    stop_all(loop, h)


def test_the_dj_chain_ends_where_a_book_begins():
    assert dj._is_book("book:dragonflight-m4b") and not dj._is_book("spotify:track:" + "a" * 22) and not dj._is_book("Pink Floyd/Animals/01 Dogs.mp3")
    h, r = make_house()
    p = dj.DjPlayer(r, ["hdmi"], env={}, popen=None, mixer=SimpleNamespace())

    class Engine:
        playing, book = False, None
        library = SimpleNamespace(refresh=lambda: 0)

        async def play(self, bid):
            self.played = bid
    p.books, p.chain, g = Engine(), object(), p.gen
    asyncio.run(p.play_book("x"))
    assert p.chain is None and p.gen == g + 1 and p.books.played == "x"                            # the feeder of the chain stops; the book starts


# ── Bilby Connect: the portal's Audiobooks app plays on the station ──
def test_connect_plays_a_book_on_the_station_and_checks_in_a_book_that_is_never_a_track(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    posted = []
    replies = [{"commands": [f"book:play:{DRAGON}"]}, {"commands": ["book:pause"]}, {}]

    def post(url, body):
        posted.append(body)
        return replies.pop(0) if replies else {}
    connect = music.Connect("http://portal", "tok", post=post, log=lambda *a: None)
    connect.add("stereo", "House stereo", h.box, book=eng)
    call(loop, connect.once())
    assert h.box.playing and h.box.kind() == "book" and eng.playing                               # the command went through the queue, not to a second player
    call(loop, connect.once())
    state = posted[-1]["state"]
    assert state["track"] == "" and state["kind"] == "book" and state["playing"] and state["book"]["id"] == DRAGON and state["book"]["playing"]
    assert state["outs"] and {"id", "name", "on", "open", "floor"} <= set(state["outs"][0]) and state["held"] is None
    call(loop, connect.once())                                                                     # "book:pause": the station's pause
    assert not h.box.playing and not eng.playing
    stop_all(loop, h)


# ── the car ──
def car_place(tmp_path, bid, pos, seen, playing=True):
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "x.json").write_text(json.dumps({"id": bid, "pos": pos, "chapter": 2, "speed": 1.0, "last_played": seen, "finished": False, "src": "car", "playing": playing}))


def test_a_book_the_car_is_playing_is_let_go_without_writing_the_houses_place_over_the_cars(tmp_path):
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path)
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    wall.t += 2.0
    call(loop, h.listen_tick())
    assert h.box.playing and h.held is None                                                       # nobody else has it
    car_place(tmp_path, DRAGON, 9.0, int(wall.t) + 1)
    wall.t += 1.0
    call(loop, h.listen_tick())
    assert not h.box.playing and not eng.playing and not r.plays and h.held["kind"] == "car"
    assert eng.store.progress(DRAGON)["pos"] == 9.0 and h.box.offset == 9.0                       # the car's place stays the book's place
    assert "car" in h.held_public()["why"]
    call(loop, h.listen_tick())
    assert not h.box.playing                                                                       # and it stays let go: only "play my book" takes it back
    car_place(tmp_path, DRAGON, 10.0, int(wall.t) + 2, playing=False)
    wall.t += 30.0
    call(loop, station.run(h, "audiobook", {"action": "play"}, "living"))
    assert eng.playing and play_args(r)[play_args(r).index("-ss") + 1] == "10.00"                 # at home it carries on from the car's place
    stop_all(loop, h)


# ── the hub's side: a pod forwards the portal's book command, and the plain commands ask the radio ──
def test_a_pods_book_command_and_the_plain_commands_go_through_the_radios_api(tmp_path):
    import radio_client                                                                              # noqa: PLC0415
    import book_voice                                                                                # noqa: PLC0415
    from aiohttp import web                                                                          # noqa: PLC0415
    loop = on_loop()
    h, r, eng, wall = make_station(tmp_path, switches=tmp_path / "outputs.json", mono=Clock())
    h.place(["hdmi"])
    rad = radio.Radio(ENV, h, log=lambda *a: None, state=tmp_path)

    async def serve():
        runner = web.AppRunner(rad.app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, site._server.sockets[0].getsockname()[1]
    runner, port = call(loop, serve())
    client = radio_client.RadioClient(f"http://127.0.0.1:{port}", log=lambda *a: None, start=False)
    view = radio_client.BookView(client)
    client.last = client.call("GET", "/state")
    assert not view.loaded and book_voice.pre_route("read my book", view) == {"action": "play"}      # nothing loaded yet: "read my book" is the tool's, before the model
    best, many = view.find("the dragon one")
    assert best["id"] == DRAGON and [m["id"] for m in many] == [DRAGON]
    assert book_voice.pre_route("read the dragon one", view) == {"action": "play", "title": "the dragon one"}
    tuner = radio_client.PodTuner(None, client, "living")
    assert asyncio.run(tuner.book_command(f"book:play:{DRAGON}"))                                    # the Audiobooks page's "Play on" the living room's box
    assert h.box.playing and h.box.kind() == "book" and eng.playing and h.speakers == {"enclave"}   # that room's speakers (the living room is the Enclave) play the one stream
    client.last = client.call("GET", "/state")
    assert view.loaded and view.current and view.playing and not view.sleeping and view.speed == 1.0
    assert book_voice.pre_route("stop", view) == {"action": "pause"} and book_voice.pre_route("next chapter", view) == {"action": "next_chapter"}
    assert not asyncio.run(tuner.book_command("book:play:no-such-book"))                             # a book he does not have: refused, nothing changes
    assert client.state()["track"] == "" and client.state()["kind"] == "book" and client.state()["book"]["id"] == DRAGON
    assert tuner.state()["track"] == "" and tuner.state()["title"] == "Dragonflight" if "pod:living" in client.state()["speakers"] else tuner.state()["playing"] is False
    call(loop, h.box.pause())
    call(loop, runner.cleanup())
    loop.call_soon_threadsafe(loop.stop)
