"""The audiobook player: the library, the remembered place, the clock, the outputs, the voice tool. Real ffmpeg/ffprobe make the synthetic books."""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audiobooks as A  # noqa: E402
import book_out  # noqa: E402
import media as M  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def tone(path: Path, seconds: float, title: str = "", artist: str = "", album: str = "", chapters=None) -> None:
    meta = [";FFMETADATA1"] + ([f"title={title}"] if title else []) + ([f"artist={artist}"] if artist else []) + ([f"album={album}"] if album else [])
    for start, end, name in chapters or []:
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}", f"END={int(end * 1000)}", f"title={name}"]
    meta_file = path.with_suffix(".txt")
    meta_file.write_text("\n".join(meta) + "\n", encoding="utf-8")
    codec = ["-c:a", "aac"] if path.suffix in (".m4b", ".m4a") else ["-c:a", "libmp3lame"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-i", str(meta_file), "-map_metadata", "1", *codec, str(path)],
                   check=True, capture_output=True)
    meta_file.unlink()


@pytest.fixture
def shelf(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    tone(root / "Anne McCaffrey - Dragonflight.m4b", 12, title="Dragonflight", artist="Anne McCaffrey",
         chapters=[(0, 4, "The Weyr"), (4, 8, "Ruth"), (8, 12, "Thread")])
    series = root / "Brandon Sanderson" / "Mistborn"
    series.mkdir(parents=True)
    for i, name in enumerate(["01 Prologue", "02 The Crew", "10 Last"], 1):
        tone(series / f"{name}.mp3", 3, album="Mistborn", artist="Brandon Sanderson")
    (root / "Locked.aaxc").write_bytes(b"not a book")
    return root


class FakePod:
    def __init__(self):
        self.urls, self.stops = [], 0

    async def play(self, url):
        self.urls.append(url)

    async def stop(self):
        self.stops += 1


class FakeMedia:
    def book_url(self, path, start, speed, towards):
        return f"http://hub/{Path(path).name}?s={start:.1f}&t={speed:.2f}"


class FakeOut:
    def __init__(self):
        self.calls, self.gain, self.stops = [], 1.0, 0

    def start(self, path, off, speed):
        self.calls.append((Path(path).name, round(off, 1), speed))
        return True

    def stop(self):
        self.stops += 1


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_player(shelf, tmp_path, clock=None, pod=True):
    clock = clock or Clock()
    lib = A.Library(shelf, tmp_path / "lib.json")
    lib.refresh(force=True)
    store = A.Store(tmp_path / "audiobooks.json")
    pod_, out = (FakePod() if pod else None), FakeOut()
    p = A.BookPlayer(lib, store, pod=pod_, media=FakeMedia() if pod else None, pc_outs=[out], clock=clock, wall=clock, log=lambda *_: None, name="office")
    return p, pod_, out, clock, store


@needs_ffmpeg
def test_the_library_finds_books_chapters_authors_and_skips_protected_files(shelf, tmp_path):
    lib = A.Library(shelf, tmp_path / "lib.json")
    assert lib.refresh(force=True) == 2 and lib.skipped_protected == 1
    dragon, mist = (lib.get("anne-mccaffrey-dragonflight-m4b"), lib.get("brandon-sanderson-mistborn"))
    assert dragon["title"] == "Dragonflight" and dragon["author"] == "Anne McCaffrey"
    assert [c["title"] for c in dragon["chapters"]] == ["The Weyr", "Ruth", "Thread"] and abs(dragon["duration"] - 12) < 0.5
    assert mist["title"] == "Mistborn" and mist["author"] == "Brandon Sanderson" and len(mist["files"]) == 3
    assert [c["title"] for c in mist["chapters"]] == ["Prologue", "The Crew", "Last"]            # natural order: 10 comes after 02
    again = A.Library(shelf, tmp_path / "lib.json")
    again.probe = lambda *_: pytest.fail("a cached book must not be probed again")
    assert again.refresh(force=True) == 2


@needs_ffmpeg
def test_loose_titles_find_the_book(shelf, tmp_path):
    lib = A.Library(shelf, tmp_path / "lib.json")
    lib.refresh(force=True)
    assert lib.find("the dragon one")[0]["title"] == "Dragonflight"
    assert lib.find("sanderson")[0]["title"] == "Mistborn"
    assert lib.find("dragonflite")[0]["title"] == "Dragonflight"                                  # a mis-heard word
    assert lib.find("cookery")[1] == []
    best, many = lib.find("brandon dragon")
    assert best is None and len(many) == 2                                                         # half of each: ask which


def test_the_store_survives_a_restart_and_a_torn_file(tmp_path):
    path = tmp_path / "audiobooks.json"
    s = A.Store(path)
    s.put("b1", pos=123.4, chapter=2, speed=1.25, last_played=5)
    s.save(force=True)
    assert not (tmp_path / "audiobooks.json.tmp").exists()
    s2 = A.Store(path)
    assert s2.current == "b1" and s2.progress("b1")["pos"] == 123.4 and s2.progress("nope")["pos"] == 0.0
    path.write_text("{ torn", encoding="utf-8")
    assert A.Store(path).current is None                                                           # a bad file is a fresh start, not a crash
    s.put("b1", pos=130)
    s.save(now=s._saved + 1)                                                                       # throttled
    assert A.Store(path).current is None
    s.save(now=s._saved + 6)
    assert A.Store(path).progress("b1")["pos"] == 130


@needs_ffmpeg
def test_play_pause_resume_chapters_and_skips(shelf, tmp_path):
    async def go():
        p, pod, out, clock, store = make_player(shelf, tmp_path)
        w = await p.play("anne-mccaffrey-dragonflight-m4b")
        assert w["chapter"] == 1 and p.playing and pod.urls[-1].endswith("?s=0.0&t=1.00") and out.calls[-1] == ("Anne McCaffrey - Dragonflight.m4b", 0.0, 1.0)
        clock.t += 5
        assert p.pos() == 5.0 and p.where()["chapter_title"] == "Ruth"
        await p.pause()
        assert not p.playing and pod.stops >= 1 and p.pos() == 5.0 and store.progress(p.book["id"])["pos"] == 5.0
        clock.t += 60
        await p.resume()                                                                           # a long pause picks up a few seconds back
        assert p.pos() == 2.0 and pod.urls[-1].endswith("?s=2.0&t=1.00")
        w = await p.next_chapter()
        assert w["chapter"] == 2 and p.pos() == 4.0
        clock.t += 7
        assert (await p.previous_chapter())["chapter"] == 2 and p.pos() == 4.0                     # well into a chapter: back to its start
        clock.t += 1
        assert (await p.previous_chapter())["chapter"] == 1 and p.pos() == 0.0                     # just after a start: the one before
        await p.seek(8.5)
        assert await p.next_chapter() is None                                                      # the last chapter
        assert (await p.skip(-30))["pos"] == 0.0
        assert (await p.skip(30))["pos"] == 11.0
    asyncio.run(go())


@needs_ffmpeg
def test_the_place_survives_a_hub_restart(shelf, tmp_path):
    async def go():
        p, pod, out, clock, store = make_player(shelf, tmp_path)
        await p.play("anne-mccaffrey-dragonflight-m4b")
        clock.t += 7
        await p.tick()                                                                             # the once-a-second tick keeps the place
        store.save(now=clock.t + 99)
        # the hub dies here: no pause, no goodbye
        p2, pod2, out2, clock2, store2 = make_player(shelf, tmp_path, clock=Clock())
        assert store2.current == "anne-mccaffrey-dragonflight-m4b"
        await p2.play(store2.current)
        assert p2.pos() == 7.0 and pod2.urls[-1].endswith("?s=7.0&t=1.00")
    asyncio.run(go())


@needs_ffmpeg
def test_a_folder_book_moves_file_to_file_and_finishes(shelf, tmp_path):
    async def go():
        p, pod, out, clock, store = make_player(shelf, tmp_path)
        await p.play("brandon-sanderson-mistborn")
        clock.t += 3.2
        await p.tick()
        assert out.calls[-1][0].startswith("02") and out.calls[-1][1] == 0.0 and p.where()["chapter"] == 2
        await p.seek(6.5)
        assert out.calls[-1][0].startswith("10") and abs(out.calls[-1][1] - 0.5) < 0.1
        clock.t += 3
        await p.tick()
        assert not p.playing and store.progress(p.book["id"])["finished"]
        await p.play("brandon-sanderson-mistborn")                                                 # a finished book starts over
        assert p.pos() == 0.0
    asyncio.run(go())


@needs_ffmpeg
def test_sleep_timer_speed_and_ducking(shelf, tmp_path):
    async def go():
        p, pod, out, clock, store = make_player(shelf, tmp_path)
        await p.play("anne-mccaffrey-dragonflight-m4b")
        p.set_sleep(minutes=0.05)                                                                  # 3 s
        clock.t += 2
        await p.tick()
        assert p.playing
        clock.t += 2
        await p.tick()
        assert not p.playing and p.sleep_at is None
        await p.seek(1)
        await p.resume()
        p.set_sleep(end_of_chapter=True)
        clock.t += 3.5
        await p.tick()
        assert not p.playing and abs(p.pos() - 4.5) < 1
        assert await p.set_speed(1.25) == 1.25
        await p.resume()
        clock.t += 4
        assert abs(p.pos() - (p.offset + 5)) < 0.2 and pod.urls[-1].endswith("t=1.25") and out.calls[-1][2] == 1.25      # speed moves the book 1.25 s per second
        p.duck(True, 10)
        assert out.gain == A.DUCK_GAIN
        clock.t += 11
        await p.tick()
        assert out.gain == 1.0
    asyncio.run(go())


@needs_ffmpeg
def test_the_voice_tool_end_to_end(shelf, tmp_path):
    async def go():
        p, pod, out, clock, store = make_player(shelf, tmp_path)
        say = lambda **a: A.run_tool(p, a)                                                         # noqa: E731
        assert "Dragonflight" in await say(action="list")
        r = await say(action="play", title="the dragon one")
        assert "Dragonflight" in r and "never read" in r and p.playing
        clock.t += 5
        assert "chapter 2" in (await say(action="where")).lower()
        assert "Paused" in await say(action="pause") and not p.playing
        assert "Ruth" in await say(action="resume")
        assert "Chapter 3" in await say(action="next_chapter")
        assert "last chapter" in await say(action="next_chapter")
        assert "back" in await say(action="back", seconds=30)
        assert "5 minutes" in await say(action="sleep", minutes=5) and p.sleep_at
        assert "off" in await say(action="sleep", minutes=0) and p.sleep_at is None
        assert "1.25" in await say(action="speed", speed=1.25)
        assert "Several could be it" in await say(action="play", title="brandon dragon")
        assert "Nothing" in await say(action="play", title="gardening")
        await p.pause()
        assert (await say(action="play")).count("Dragonflight") == 1                               # no title: the book he was on
    asyncio.run(go())


def test_an_empty_library_says_what_to_do(tmp_path):
    async def go():
        lib = A.Library(tmp_path / "nothing", None)
        p = A.BookPlayer(lib, A.Store(tmp_path / "s.json"), log=lambda *_: None)
        r = await A.run_tool(p, {"action": "play"})
        assert "no audiobooks" in r and "DRM-free" in r
    asyncio.run(go())


def test_book_tools_and_the_combined_set(tmp_path):
    async def go():
        loop = asyncio.get_running_loop()
        room = SimpleNamespace(book=None, last_media="music")
        tools, run = A.book_tools(lambda: room, loop)
        both = A.combine(([{"name": "music"}], lambda n, a: "music!"), (tools, run))
        assert {t["name"] for t in both[0]} == {"music", "audiobook"}
        assert both[1]("music", {}) == "music!"
        assert await asyncio.to_thread(both[1], "audiobook", {"action": "list"}) == "This room has no way to play books."
        assert both[1]("nope", {}) == "I have no such tool."
    asyncio.run(go())


def test_the_pod_url_and_the_ffmpeg_lines_carry_the_speed_and_the_place():
    srv = M.MediaServer(host="10.0.0.2", home_token="t")
    url = srv.book_url(r"D:\Aaron\audiobooks\x\Book One.m4b", 61.25, 1.5)
    assert url.startswith(f"http://10.0.0.2:10800/b/{srv.secret}/") and url.endswith(".flac?s=61.2&t=1.50") and "audiobooks" not in url      # no path leaks to the box
    assert list(srv.books.values()) == [r"D:\Aaron\audiobooks\x\Book One.m4b"]
    a = M.ffmpeg_args("in.m4b", 10, 1.25)
    assert a.index("-ss") < a.index("-i") < a.index("-af") and "atempo=1.25" in a and "-af" not in M.ffmpeg_args("in.m4b", 0)
    p = book_out.pcm_args("in.m4b", 10, 1.25)
    assert p.index("-i") < p.index("-af") and p[-1] == "pipe:1" and "-af" not in book_out.pcm_args("in.m4b", 0)


def test_the_pc_output_streams_chunks_with_the_ducking_gain():
    import threading
    import numpy as np

    class Proc:
        def __init__(self, data):
            self.stdout = SimpleNamespace(read=self._read)
            self.data = data
            self.killed = False

        def _read(self, n):
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

        def kill(self):
            self.killed = True

    class Stream:
        def __init__(self):
            self.writes, self.closed = [], threading.Event()

        def write(self, d):
            self.writes.append(d.copy())

        def close(self):
            self.closed.set()

    ones = np.ones((book_out.RATE // 5, 2), dtype="<f4").tobytes()                                 # 0.2 s
    proc, stream, spawned = Proc(ones), Stream(), []
    out = book_out.PcBookOut("hdmi", "Fake", popen=lambda args, **kw: spawned.append(args) or proc, open_stream=lambda dev: stream, find=lambda part: (3, "Fake HDMI"))
    out.gain = 0.5
    assert out.start("book.m4b", 12.0, 1.25)
    assert stream.closed.wait(3)
    out.stop()
    assert spawned and spawned[0][spawned[0].index("-ss") + 1] == "12.00" and len(stream.writes) == 2 and float(stream.writes[0][0][0]) == 0.5 and proc.killed
    gone = book_out.PcBookOut("bt", "Enclave", find=lambda part: None, log=lambda *_: None)
    assert gone.start("book.m4b", 0) is False                                                      # the Bluetooth stereo is off: skipped quietly


# ── the plain spoken commands ──
def routed(heard, playing=True, paused_book=False, sleep=False, lib=None, last="book"):
    import book_voice
    player = SimpleNamespace(playing=playing, book={"id": "b"} if (playing or paused_book) else None, speed=1.0, sleep_at=1 if sleep else None,
                             library=lib or SimpleNamespace(refresh=lambda: 0, find=lambda q: (None, [])))
    return book_voice.pre_route(heard, SimpleNamespace(book=player, last_media=last))


def test_plain_spoken_commands_reach_the_tool_without_the_model():
    assert routed("Skippy, stop") == {"action": "pause"} and routed("pause the book") == {"action": "pause"} and routed("be quiet") == {"action": "pause"}
    assert routed("skippy stop", playing=False, paused_book=True) is None                          # nothing playing: "stop" is not for the book
    assert routed("stop", last="music") == {"action": "pause"} and routed("stop", playing=False, last="music") is None
    assert routed("continue", playing=False, paused_book=True) == {"action": "resume"}
    assert routed("resume", playing=False, paused_book=True, last="music") is None                 # the music was last: "resume" is the music's
    assert routed("next chapter") == {"action": "next_chapter"} and routed("go to the previous chapter") == {"action": "previous_chapter"}
    assert routed("go back thirty seconds") == {"action": "back", "seconds": 30} and routed("back") == {"action": "back", "seconds": 30}
    assert routed("skip forward two minutes") is None or routed("skip forward two minutes")["seconds"] == 120
    assert routed("rewind 10 seconds") == {"action": "back", "seconds": 10}
    assert routed("stop in twenty minutes") == {"action": "sleep", "minutes": 20.0} and routed("pause in half an hour") == {"action": "sleep", "minutes": 30.0}
    assert routed("sleep timer 45 minutes") == {"action": "sleep", "minutes": 45.0} and routed("stop at the end of this chapter") == {"action": "sleep", "end_of_chapter": True}
    assert routed("cancel the sleep timer") == {"action": "sleep", "minutes": 0}
    assert routed("where was I") == {"action": "where"} and routed("what chapter is this") == {"action": "where"}
    assert routed("read faster") == {"action": "speed", "speed": 1.25} and routed("normal speed") == {"action": "speed", "speed": 1.0}
    assert routed("read my book", playing=False) == {"action": "play"} and routed("keep reading", playing=False) == {"action": "play"}
    assert routed("what time is it") is None and routed("turn off the lights") is None and routed("play some pink floyd", playing=False) is None


def test_reading_a_named_book_needs_it_to_be_in_the_library():
    lib = SimpleNamespace(refresh=lambda: 1, find=lambda q: ({"title": "Dragonflight"}, [{"title": "Dragonflight"}]) if "dragon" in q else (None, []))
    assert routed("read the dragon one", playing=False, lib=lib) == {"action": "play", "title": "the dragon one"}
    assert routed("play dragonflight", playing=False, lib=lib) is None                            # one word, no "read"/"book": might be a song
    assert routed("read the book about gardening", playing=False, lib=lib) is None


def test_the_brain_runs_the_tool_first_and_the_model_only_words_it():
    from brain import Brain
    from timers import Timers
    seen = {}

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                seen["tools"], seen["last"] = kw.get("tools"), kw["messages"][-1]["content"]
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Paused. Silence, at last.")])

    ran = []
    b = Brain(Client, "m", Timers(), persona="p", extra=([{"name": "audiobook"}], lambda n, a: ran.append((n, a)) or "(Done.) Paused it."))
    b.pre_route = lambda heard: {"action": "pause"} if "stop" in heard else None
    assert b.reply("stop") == "Paused. Silence, at last." and ran == [("audiobook", {"action": "pause"})]
    assert seen["tools"] is None and "Paused it." in seen["last"] and b.actions[-1]["tool"] == "audiobook"
