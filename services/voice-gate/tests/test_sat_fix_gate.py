"""voice-gate's cache, the fixes after the adversarial review of the speed change (2026-11-05).

P15: a cached line whose file would not go (on the Windows bind mount a file a reader has open cannot be unlinked) used to leave the order but stay on disk, so the
cache sat above its cap until the next restart. It is now unlinked at a later store. P13: the cache writes atomically, and the pieces of that are pinned here: a
reader never meets half a file, a store that dies midway leaves nothing a restart could take for a line, and two stores of the same words at once never write one
temp file. Everything runs on real files in a temporary folder; only the failures (an unlink that is refused, a write that stops halfway) are made up."""
import hashlib
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate  # noqa: E402


def settle(voice, seconds=10):
    assert voice.wait(seconds), "the background stores did not finish"


def fill(folder, n):
    """n cache files already on disk, ages apart: the first is the oldest."""
    names = [f"{i:032x}.mp3" for i in range(n)]
    for i, name in enumerate(names):
        p = folder / name
        p.write_bytes(b"x")
        os.utime(p, (1_000_000 + i * 10, 1_000_000 + i * 10))
    return names


def on_disk(folder):
    return sorted(p.name for p in folder.glob("*.mp3"))


def file_of(text):
    """The cache's own naming of a line (a restart finds the old files by it)."""
    return hashlib.sha256(text.encode()).hexdigest()[:32] + ".mp3"


def refuse_unlink(monkeypatch, victims, while_on):
    """os.unlink that fails the way Windows fails for a file a reader has open (a PermissionError) for the file names in `victims`, while while_on() says so.
    Returns the list of the refused attempts."""
    real, refused = os.unlink, []

    def unlink(path, *a, **k):
        name = os.path.basename(str(path))
        if name in victims and while_on():
            refused.append(name)
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real(path, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)
    return refused


# --- P15: a refused unlink is tried again ----------------------------------------------------------------------------------------------------------------

def test_fixgate_a_line_that_would_not_leave_the_cache_is_unlinked_at_the_next_store(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=5)
    folder = tmp_path / "tts-cache"
    names = fill(folder, 5)                                                    # a cache at its cap; names[0] is the oldest
    reader_has_it = {"open": True}
    refused = refuse_unlink(monkeypatch, {names[0]}, lambda: reader_has_it["open"])
    voice.remember("skippy|one", b"1")
    settle(voice)
    assert refused == [names[0]] and len(on_disk(folder)) == 6 and names[0] in on_disk(folder)      # the oldest would not go: one over the cap
    reader_has_it["open"] = False                                              # the reader let go
    voice.remember("skippy|two", b"2")
    settle(voice)
    left = on_disk(folder)
    assert len(left) == 5 and names[0] not in left and names[1] not in left     # healed with no restart: the stuck file and the next oldest are both gone
    assert voice.cached("skippy|one") == b"1" and voice.cached("skippy|two") == b"2"


def test_fixgate_a_line_said_again_while_it_waits_to_go_is_not_taken_away(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    folder = tmp_path / "tts-cache"
    for word in "abc":
        voice.remember(f"skippy|{word}", word.encode())
        settle(voice)
    reader_has_it = {"open": True}
    refuse_unlink(monkeypatch, {file_of("skippy|a")}, lambda: reader_has_it["open"])
    voice.remember("skippy|d", b"d")
    settle(voice)                                                              # a is the oldest: out of the cache, its file would not go
    assert file_of("skippy|a") in on_disk(folder)
    reader_has_it["open"] = False
    voice.remember("skippy|a", b"a again")                                     # the same words again: a is a line of the cache again, with new audio
    settle(voice)
    assert voice.cached("skippy|a") == b"a again"                              # (the retry must not unlink what was just stored)
    voice.remember("skippy|e", b"e")
    settle(voice)
    assert voice.cached("skippy|a") == b"a again" and file_of("skippy|a") in on_disk(folder)
    assert len(on_disk(folder)) == 3


def test_fixgate_a_line_whose_new_file_is_in_place_but_not_listed_yet_is_safe_from_another_stores_retry(tmp_path, monkeypatch):
    """The window between os.replace and the line's place in the order: a store of the same words has put its file in place but is not listed yet, and another store's
    retry runs right then. The line is pending, so the retry leaves the file alone."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    folder = tmp_path / "tts-cache"
    for word in "abc":
        voice.remember(f"skippy|{word}", word.encode())
        settle(voice)
    a, b = file_of("skippy|a"), file_of("skippy|b")
    reader_has_it = {"open": True}
    refuse_unlink(monkeypatch, {a}, lambda: reader_has_it["open"])
    voice.remember("skippy|d", b"d")
    settle(voice)                                                              # a is out of the cache, its old file would not go
    reader_has_it["open"] = False
    placed, go, b_gone = threading.Event(), threading.Event(), threading.Event()
    real_replace, real_unlink = os.replace, os.unlink

    def replace(src, dst, *args, **kw):
        real_replace(src, dst, *args, **kw)
        if os.path.basename(str(dst)) == a:
            placed.set()                                                       # a's new file is in place; the store has not listed it yet
            go.wait(10)

    def unlink(path, *args, **kw):
        real_unlink(path, *args, **kw)
        if os.path.basename(str(path)) == b:
            b_gone.set()                                                       # the other store has got past a (the oldest name on its list) and removed b
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "unlink", unlink)
    voice.remember("skippy|a", b"a again")
    assert placed.wait(10)
    voice.remember("skippy|e", b"e")                                           # another store, whose retry meets a on the list
    assert b_gone.wait(10)
    assert file_of("skippy|a") in on_disk(folder)                              # not taken away
    go.set()
    settle(voice)
    monkeypatch.undo()
    assert voice.cached("skippy|a") == b"a again" and voice.cached("skippy|e") == b"e"


def test_fixgate_a_stale_entry_for_a_line_that_is_in_the_cache_again_is_dropped_not_unlinked(tmp_path):
    """The other interleaving, set up by hand: a retry took its list before the line was stored again and meets it after (stored, listed, no longer pending)."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=5)
    folder = tmp_path / "tts-cache"
    voice.remember("skippy|live", b"live audio")
    settle(voice)
    voice._doomed[file_of("skippy|live")] = None                               # (the stale entry)
    voice._doomed[file_of("skippy|gone")] = None                               # (a real leftover that is not on disk any more)
    voice._unlink_doomed(folder)
    assert voice.cached("skippy|live") == b"live audio" and file_of("skippy|live") in on_disk(folder)
    assert not voice._doomed                                                   # both entries are settled: kept line dropped, missing file dropped


def test_fixgate_a_line_stored_while_a_retry_is_going_down_its_list_is_not_taken_away(tmp_path, monkeypatch):
    """The same race with the clock stopped: the retry's list is [x, b]; while it is unlinking x, b is said again and stored whole. Whether b may go is decided when the
    retry gets to b, not when the list was made."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=5)
    folder = tmp_path / "tts-cache"
    voice.remember("skippy|b", b"old b")
    settle(voice)
    (folder / "x.mp3").write_bytes(b"x")
    voice._order.pop(file_of("skippy|b"))                                      # (b left the cache and its file would not go; so did x)
    voice._doomed["x.mp3"] = None
    voice._doomed[file_of("skippy|b")] = None
    real, once = os.unlink, []

    def unlink(path, *args, **kw):
        if os.path.basename(str(path)) == "x.mp3" and not once:
            once.append(1)
            voice.remember("skippy|b", b"new b")                               # the retry is at x: b is said again and stored while it waits
            assert voice.wait(10)
        return real(path, *args, **kw)
    monkeypatch.setattr(os, "unlink", unlink)
    voice._unlink_doomed(folder)
    monkeypatch.undo()
    assert voice.cached("skippy|b") == b"new b" and file_of("skippy|b") in on_disk(folder)
    assert not voice._doomed and not (folder / "x.mp3").exists()


def test_fixgate_a_file_that_is_already_gone_is_not_retried(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    folder = tmp_path / "tts-cache"
    names = fill(folder, 3)
    voice.remember("skippy|first", b"1")                                       # (this store reads the folder: names[0] is the oldest)
    settle(voice)
    attempts, real = [], os.unlink

    def counting(path, *a, **k):
        attempts.append(os.path.basename(str(path)))
        return real(path, *a, **k)
    monkeypatch.setattr(os, "unlink", counting)
    (folder / names[1]).unlink()                                               # somebody else removed it: the eviction finds nothing to unlink
    attempts.clear()
    for n in range(4):
        voice.remember(f"skippy|more {n}", b"m")
        settle(voice)
    assert attempts.count(names[1]) == 1                                       # tried once, found gone, not tried again


def test_fixgate_a_file_that_never_goes_costs_the_stores_nothing_and_the_retries_stay_few(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    folder = tmp_path / "tts-cache"
    fill(folder, 3)
    per_store, now = [], []

    def refuse_all_cache_files(path, *a, **k):
        if str(path).startswith(str(folder)):
            now.append(1)
            raise PermissionError(13, "in use")
        return os.remove(path)
    monkeypatch.setattr(os, "unlink", refuse_all_cache_files)                  # a folder nothing can be removed from
    for n in range(12):
        now.clear()
        voice.remember(f"skippy|line {n}", f"audio {n}".encode())
        settle(voice)
        per_store.append(len(now))
        assert voice.cached(f"skippy|line {n}") == f"audio {n}".encode()       # every reply still works
    assert max(per_store) <= 3 + 1                                             # a list of failures cannot grow without end: at most the cap, plus the new eviction


# --- P13: the cache writes atomically ---------------------------------------------------------------------------------------------------------------------

def write_in_two_halves(tmp_path, after_first_half):
    """Path.write_bytes for files under tmp_path that writes half the bytes, calls after_first_half(), then writes the rest (a slow or dying disk)."""
    real = Path.write_bytes

    def write_bytes(self, data):
        if not str(self).startswith(str(tmp_path)):
            return real(self, data)
        with open(self, "wb") as f:
            f.write(data[:len(data) // 2])
            f.flush()
            after_first_half(self)
            f.write(data[len(data) // 2:])
        return len(data)
    return write_bytes


def test_fixgate_a_reader_never_meets_half_a_file_while_the_disk_is_slow(tmp_path, monkeypatch):
    writer = gate.Voice(str(tmp_path), time.time, cache_keep=10)
    reader = gate.Voice(str(tmp_path), time.time, cache_keep=10)               # another process on the same folder (a restart while the old one is still going)
    half, go = threading.Event(), threading.Event()

    def stall(_path):
        half.set()
        go.wait(10)
    monkeypatch.setattr(Path, "write_bytes", write_in_two_halves(tmp_path, stall))
    audio = b"A" * 1000
    writer.remember("skippy|Half.", audio)
    assert half.wait(10)                                                       # the writer is midway: 500 bytes are on disk somewhere
    seen = reader.cached("skippy|Half.")
    assert seen in (None, audio), f"a reader met {len(seen)} of {len(audio)} bytes"
    go.set()
    settle(writer)
    monkeypatch.undo()
    assert reader.cached("skippy|Half.") == audio                              # and when it is whole, it is a line


def test_fixgate_a_store_that_dies_midway_leaves_nothing_a_restart_could_take_for_a_line(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=10)
    folder = tmp_path / "tts-cache"

    def die(_path):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Path, "write_bytes", write_in_two_halves(tmp_path, die))
    voice.remember("skippy|Cut off.", b"B" * 1000)
    settle(voice)
    monkeypatch.undo()
    assert on_disk(folder) == [] and not list(folder.glob("*.tmp"))            # no half line, and the failed store cleaned up its own temp file
    restarted = gate.Voice(str(tmp_path), time.time, cache_keep=10)
    assert restarted.cached("skippy|Cut off.") is None
    restarted.remember("skippy|Cut off.", b"B" * 1000)                         # said again later: stored whole
    settle(restarted)
    assert restarted.cached("skippy|Cut off.") == b"B" * 1000


def test_fixgate_two_stores_of_the_same_words_at_once_each_write_their_own_temp_file(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=10)
    reader = gate.Voice(str(tmp_path), time.time, cache_keep=10)
    folder = tmp_path / "tts-cache"
    real, written = Path.write_bytes, []
    meet = threading.Barrier(2)

    def write_bytes(self, data):
        if str(self).startswith(str(tmp_path)):
            written.append(self.name)
            try:
                meet.wait(2)                                                   # both stores are inside their write at the same time
            except threading.BrokenBarrierError:
                pass
        return real(self, data)
    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    voice.remember("skippy|Twice.", b"first take of the line")
    voice.remember("skippy|Twice.", b"second take, a different one")
    settle(voice)
    monkeypatch.undo()
    assert len(written) == 2 and len(set(written)) == 2                        # two temp files: one store can never truncate the file the other is renaming
    assert reader.cached("skippy|Twice.") in (b"first take of the line", b"second take, a different one")
    assert on_disk(folder) == [file_of("skippy|Twice.")] and not list(folder.glob("*.tmp"))
