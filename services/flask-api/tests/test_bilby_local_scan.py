"""The music library scan (app/clients/bilby_local.py) must never freeze the worker.

2026-11-02: flask-api went "unhealthy" for hours and the doctor DMed the Captain "still failing after 3 tries". Cause: at import each
gunicorn worker (gevent: a "thread" is a coroutine in the one event loop) walked the 21k-track library over the Windows bind mount with
a stat() per file. That blocks, so the worker could not answer /health for minutes, gunicorn killed it at 120 s, the new worker scanned
again, for ever. Now the walk uses os.scandir (the type of each entry comes with the directory listing) and runs in a real thread
(app/offload.py), one scan at a time. Run on their own: `python -m pytest services/flask-api/tests -q`."""
import os
import threading
import time

import pytest

from app.clients import bilby_local as bl


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A small library in the shape the real one has: Artist/Album/track, loose tracks at both upper levels, and things that aren't audio."""
    def touch(rel):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    for rel in ("Ada/First (2001)/01 - Ada - Opening.mp3", "Ada/First (2001)/cover.jpg", "Ada/First (2001)/notes.txt", "Ada/loose track.flac",
                "Bob/Second (1999)/Closing.m4a", "Bob/Second (1999)/Extra Bit.opus", "top level.mp3", "readme.md",
                "Cy/Third/Disc 2/too deep.mp3"):                                  # four levels down: never scanned, as before
        touch(rel)
    monkeypatch.setattr(bl, "_MUSIC_ROOT", tmp_path)
    monkeypatch.setattr(bl, "_INDEX_FILE", tmp_path.parent / (tmp_path.name + "-index.json"))    # (beside the library, not in it)
    return tmp_path


def test_the_scan_finds_the_tracks_the_old_walk_found_and_nothing_else(library):
    got = {t["file_id"]: t for t in bl._scan_library()}
    assert sorted(got) == ["Ada/First (2001)/01 - Ada - Opening.mp3", "Ada/loose track.flac", "Bob/Second (1999)/Closing.m4a",
                           "Bob/Second (1999)/Extra Bit.opus", "top level.mp3"]
    opening = got["Ada/First (2001)/01 - Ada - Opening.mp3"]
    assert (opening["title"], opening["artist"], opening["album"], opening["name"]) == ("Opening", "Ada", "First (2001)", "01 - Ada - Opening.mp3")
    assert got["Ada/loose track.flac"]["album"] == "" and got["Ada/loose track.flac"]["artist"] == "Ada"
    assert got["top level.mp3"]["artist"] == "Unknown" and got["Bob/Second (1999)/Closing.m4a"]["artist"] == "Bob"


def test_a_missing_or_unreadable_library_is_an_empty_scan_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "_MUSIC_ROOT", tmp_path / "not mounted")
    assert bl._scan_library() == []


def test_the_walk_costs_a_listing_per_folder_not_a_stat_per_file(library, monkeypatch):
    for n in range(60):                                                          # sixty more tracks in one album
        (library / "Bob" / "Second (1999)" / f"track {n}.mp3").write_bytes(b"x")
    stats = []
    real = os.stat
    monkeypatch.setattr(os, "stat", lambda *a, **k: stats.append(a[0] if a else None) or real(*a, **k))
    assert len(bl._scan_library()) == 65
    assert len(stats) <= 3, stats                                                # (the old walk: one per entry, 3 round trips each on the mount)


def test_one_scan_however_many_callers_and_in_a_thread_of_its_own(library, monkeypatch):
    monkeypatch.setattr(bl, "_loaded", False)
    monkeypatch.setattr(bl, "_queue", [])
    monkeypatch.setattr(bl, "_now", None)
    scans, scan_thread = [], []
    real = bl._scan_library

    def slow_scan():
        scans.append(1)
        scan_thread.append(threading.get_ident())
        time.sleep(0.3)
        return real()
    monkeypatch.setattr(bl, "_scan_library", slow_scan)
    callers = [threading.Thread(target=bl._ensure_loaded) for _ in range(4)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(20)
    assert len(scans) == 1                                                        # the others waited for it instead of walking the library again
    assert bl.ready() and bl.queue_size() == 5 and bl.now_playing()["file_id"]    # and all of them saw the answer
    bl._ensure_loaded()
    assert len(scans) == 1                                                        # loaded: no more scans


def test_the_event_loop_serves_while_the_library_is_being_scanned(library, monkeypatch):
    """The point of it all: while one request waits for the scan, another is answered. (Needs gevent, as the image has.)"""
    gevent = pytest.importorskip("gevent")
    monkeypatch.setattr(bl, "_loaded", False)
    monkeypatch.setattr(bl, "_queue", [])
    monkeypatch.setattr(bl, "_now", None)
    monkeypatch.setattr(bl, "_scan_library", lambda: (time.sleep(0.4), [{"file_id": "a/b/c.mp3", "name": "c.mp3", "title": "c", "artist": "a", "album": "b"}])[1])
    answered = []

    def another_request():                                                         # /health, say
        for _ in range(5):
            answered.append(1)
            gevent.sleep(0.01)
    other = gevent.spawn(another_request)
    bl._ensure_loaded()
    assert len(answered) == 5 and bl.ready()
    other.join()


def test_a_restart_reads_the_saved_index_instead_of_walking_the_library_again(library, monkeypatch):
    monkeypatch.setattr(bl, "_loaded", False)
    monkeypatch.setattr(bl, "_queue", [])
    monkeypatch.setattr(bl, "_now", None)
    walks = []
    real = bl._scan_library
    monkeypatch.setattr(bl, "_scan_library", lambda: walks.append(1) or real())
    bl._ensure_loaded()
    assert walks == [1] and bl.queue_size() == 5 and bl._INDEX_FILE.is_file()           # the first start walks, and saves what it found
    monkeypatch.setattr(bl, "_loaded", False)
    monkeypatch.setattr(bl, "_queue", [])                                               # a restart: nothing in memory
    bl._ensure_loaded()
    assert walks == [1] and bl.queue_size() == 5                                         # read back, not walked
    bl.invalidate()
    assert not bl._INDEX_FILE.exists()
    bl._ensure_loaded()
    assert walks == [1, 1]                                                               # asked to rescan: a real walk


def test_an_old_or_foreign_or_broken_index_is_ignored(library, monkeypatch):
    tracks = [{"file_id": "a/b/c.mp3", "name": "c.mp3", "title": "c", "artist": "a", "album": "b"}]
    bl._write_index(tracks)
    assert bl._read_index() == tracks
    old = time.time() - bl._INDEX_MAX_AGE_S - 60
    os.utime(bl._INDEX_FILE, (old, old))
    assert bl._read_index() is None                                                      # older than twelve hours
    bl._write_index(tracks)
    monkeypatch.setattr(bl, "_MUSIC_ROOT", library / "elsewhere")
    assert bl._read_index() is None                                                      # for a different library
    monkeypatch.setattr(bl, "_MUSIC_ROOT", library)
    bl._INDEX_FILE.write_text("{not json", encoding="utf-8")
    assert bl._read_index() is None                                                      # broken: walked instead
    bl._INDEX_FILE.write_text('{"root": "%s", "tracks": []}' % str(library).replace("\\", "\\\\"), encoding="utf-8")
    assert bl._read_index() is None                                                      # an empty library is not worth trusting
