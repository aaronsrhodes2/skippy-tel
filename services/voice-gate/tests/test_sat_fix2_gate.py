"""voice-gate's cache, the second adversarial review of the speed change (2026-11-05), problem 7: a BURST of stores left files above the cap that nothing knew about any more.

The list of files to remove (`_doomed`) was cut back to the cap whatever it held, so the names of files nobody had tried to remove were dropped: one caller issuing 600 rapid stores
left 40 to 63 files over a cap of 40 (the review), and 60 bursts of 600 stores (one caller, or 16 threads) left 117 and 357 files over the cap on average, until the next restart read the
folder again. Now a name leaves the list only when its file is gone, its line is in the cache again (or on its way, and a store that fails puts it back), or it was tried DOOMED_TRIES
times and refused every time. A pass takes the whole list for itself, so the stores of a burst do not each go down it.

Everything runs on real files in a temporary folder; only the failures (an unlink that is refused, a replace that is refused) are made up. The sibling file test_sat_fix_gate.py pins the
retry of a refused unlink, the atomic write and the temp files, and stays as it is.

Nothing here may run for ever (the third review, 2026-11-05, code#3): this file is in CI's services job, which has no timeout. The two tests that loop DOOMED_TRIES times (one store
per try) took their loop count from the constant itself, so DOOMED_TRIES = 10**9 (a name never given up) did not fail them, it kept them storing for ever. They now check first that the
constant is a few (`tries()`), and the loop of the first one also has a deadline of its own (`LOOP_S`) that fails with a message instead of waiting."""
import hashlib
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate  # noqa: E402


def settle(voice, seconds=30):
    assert voice.wait(seconds), "the background stores did not finish"


MOST_TRIES = 10          # "a few" (gate.py: a folder nothing can be removed from costs each store a few unlinks, not a growing list)
LOOP_S = 60              # what a loop of stores below may take in all, on a slow disk: the real ones take a second or two


def tries():
    """gate.DOOMED_TRIES, after a check that it is a few. The tests below store once per try, so a constant of 10**9 (a file that would never be given up) must FAIL them here, at once and with
    this message, not keep them storing for ever."""
    assert 1 <= gate.DOOMED_TRIES <= MOST_TRIES, (f"gate.DOOMED_TRIES is {gate.DOOMED_TRIES}: a file that will not go must be given up after a few refusals (1 to {MOST_TRIES}), "
                                                  f"or a folder nothing can be removed from costs every store a longer list for ever")
    return gate.DOOMED_TRIES


def test_fix3gate_g2_the_tries_a_file_is_given_before_it_is_left_for_a_restart_are_a_few():
    assert 1 <= tries() <= MOST_TRIES


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
    return hashlib.sha256(text.encode()).hexdigest()[:32] + ".mp3"


def unlisted(voice, folder):
    """The files on disk that the cache knows nothing about: not a line of it, not on the list of files to remove."""
    with voice._lock:
        known = set(voice._order or ()) | set(voice._doomed)
    return [n for n in on_disk(folder) if n not in known]


# --- a folder far over its cap comes down to it at one store -------------------------------------------------------------------------------------------

def test_fix2gate_a_folder_far_over_its_cap_comes_down_to_it_at_one_store(tmp_path):
    """The first store reads the folder and everything past the cap leaves the cache at once (the cap was lowered, or the last run ended in a burst). All of those files are on the list
    to remove; the list used to be cut back to the cap (3) before one of them was tried, so 61 files stayed and nothing knew about them."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    folder = tmp_path / "tts-cache"
    names = fill(folder, 63)
    voice.remember("skippy|the one that reads the folder", b"new")
    settle(voice)
    left = on_disk(folder)
    assert len(left) == 3 and file_of("skippy|the one that reads the folder") in left      # the cap, and the newest of the lines
    assert left == sorted([names[-1], names[-2], file_of("skippy|the one that reads the folder")])
    assert not voice._doomed and not unlisted(voice, folder)


def test_fix2gate_nothing_is_dropped_before_it_was_tried_and_a_name_is_given_up_only_after_the_tries(tmp_path, monkeypatch, caplog):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    folder = tmp_path / "tts-cache"
    fill(folder, 63)
    refused = []
    real = os.unlink

    def unlink(path, *a, **k):
        if str(path).startswith(str(folder)):
            refused.append(os.path.basename(str(path)))
            raise PermissionError(13, "in use")
        return real(path, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)
    n_tries = tries()                                                                    # (a few: this loop stores once per try)
    began = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="voice-gate"):
        for n in range(n_tries + 2):
            assert time.monotonic() - began < LOOP_S, f"store {n}: the stores of this loop have taken {LOOP_S} s; a store that does not end, or a name that is never given up"
            refused.clear()
            voice.remember(f"skippy|store {n}", b"s")
            settle(voice, 15)
            if n < n_tries:
                # all 61 files over the cap (and one more each store) are tried at every one of the first DOOMED_TRIES stores: none of them was dropped untried
                assert len(refused) >= 61 and len(set(refused)) >= 61, (n, len(refused))
            else:
                assert len(refused) <= n_tries, (n, len(refused))                        # given up: they cost the stores nothing any more (the new evictions of the last stores only)
    assert len(voice._doomed) < n_tries
    assert sum("would not go" in r.getMessage() for r in caplog.records) >= 61          # and each give-up said so once


def test_fix2gate_a_file_that_goes_at_the_last_try_is_removed(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    folder = tmp_path / "tts-cache"
    names = fill(folder, 3)
    refusals = {"n": 0}
    real = os.unlink
    n_tries = tries()

    def unlink(path, *a, **k):
        if os.path.basename(str(path)) == names[0] and refusals["n"] < n_tries - 1:
            refusals["n"] += 1
            raise PermissionError(13, "in use")                                      # refused DOOMED_TRIES - 1 times ...
        return real(path, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)
    for n in range(n_tries):
        voice.remember(f"skippy|line {n}", b"l")
        settle(voice, 15)
    assert names[0] not in on_disk(folder) and refusals["n"] == n_tries - 1    # ... and then it went


# --- a store that fails leaves nothing unlisted --------------------------------------------------------------------------------------------------------

def test_fix2gate_a_line_stored_again_is_no_reason_to_forget_its_old_file_when_that_store_fails(tmp_path, monkeypatch):
    """The sequence of the burst, laid out by hand. Line a is stored again (take 2 is on its way), another store evicts the OLD a from the cache and its retry finds a pending, so a leaves
    the list of files to remove unremoved (take 2 will list it again). Take 2 then fails (Windows refuses a replace over a file another store is replacing). The old file of a was on disk,
    in no list: it stayed until a restart. Now the failed store puts a back on the list and its pass removes the file."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=2)
    folder = tmp_path / "tts-cache"
    for word in "ab":
        voice.remember(f"skippy|{word}", word.encode())
        settle(voice)
    a, b, c = file_of("skippy|a"), file_of("skippy|b"), file_of("skippy|c")
    at_replace, go = threading.Event(), threading.Event()
    real = os.replace

    def replace(src, dst, *args, **kw):
        if os.path.basename(str(dst)) == a:
            at_replace.set()                                                         # take 2 of a: on its way, held at the replace ...
            go.wait(10)
            raise PermissionError(5, "Access is denied")                             # ... and then refused
        return real(src, dst, *args, **kw)
    monkeypatch.setattr(os, "replace", replace)
    voice.remember("skippy|a", b"a take 2")
    assert at_replace.wait(10)
    voice.remember("skippy|c", b"c")                                                 # evicts the old a (the oldest); its retry meets a pending
    deadline = time.time() + 10
    while c not in on_disk(folder) and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)                                                                  # (c's pass has run: it found a pending and let it go)
    assert a in on_disk(folder)                                                      # the old file of a: still there, on no list
    go.set()
    settle(voice)
    monkeypatch.undo()
    assert on_disk(folder) == sorted([b, c]) and not unlisted(voice, folder)         # take 2 failed, the old file went with it
    assert voice.cached("skippy|a") is None                                          # (a miss: said again, it is rendered and stored again)


def writers_left(voice):
    with voice._lock:
        return len(voice._writers)


def until(condition, seconds=10):
    end = time.time() + seconds
    while not condition() and time.time() < end:
        time.sleep(0.01)
    return condition()


def test_fix2gate_an_older_take_of_the_same_words_finishing_first_does_not_take_the_newer_ones_mark_away(tmp_path, monkeypatch):
    """Two stores of the same words at once share one key in `_pending`. When the older one finishes while the newer one is still on its way (its file in place, not listed yet), the
    mark is still the newer one's: a pass that finds the line evicted meanwhile must let the file be (the newer store lists it in a moment). The mark used to be taken by whoever finished
    first, and the pass removed the file the newer store had just put in place: the line was listed with no file (a miss, and a render for nothing). In a burst of 600 stores of 90 lines
    that lost 5 to 25 lines of the 40 in the cache."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=1)
    folder = tmp_path / "tts-cache"
    w, x = file_of("skippy|w"), file_of("skippy|x")
    t1_at, t1_go, t2_at, t2_go = (threading.Event() for _ in range(4))
    real_write, real_known = Path.write_bytes, gate.Voice._known
    writes, knowns = [], []

    def write_bytes(self, data):
        if str(self).startswith(str(folder)):
            writes.append(1)
            if len(writes) == 1:                                                       # take 1's writer: held before it has put anything in place
                t1_at.set()
                t1_go.wait(10)
        return real_write(self, data)

    def known(self):
        knowns.append(1)
        if len(knowns) == 1:                                                           # take 2's writer (it is the first to get here): file in place, not listed yet
            t2_at.set()
            t2_go.wait(10)
        return real_known(self)
    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    monkeypatch.setattr(gate.Voice, "_known", known)
    voice.remember("skippy|w", b"take 1")
    assert t1_at.wait(10)
    voice.remember("skippy|w", b"take 2")                                              # the mark of w is take 2's from now on
    assert t2_at.wait(10)
    t1_go.set()                                                                        # take 1 goes on, replaces the file, lists w, and is done first
    assert until(lambda: writers_left(voice) == 1)
    voice.remember("skippy|x", b"x")                                                   # evicts w from the cache (cap 1); the pass finds w's file, with take 2 on its way to list it
    assert until(lambda: writers_left(voice) == 1 and x in on_disk(folder))
    time.sleep(0.2)
    t2_go.set()                                                                        # take 2 lists w (and x leaves)
    settle(voice)
    monkeypatch.undo()
    assert on_disk(folder) == [w] and list(voice._order) == [w] and not voice._pending and not voice._doomed
    assert voice.cached("skippy|w") in (b"take 1", b"take 2")                          # and the line is a hit, not a listed line with no file


# --- a burst -----------------------------------------------------------------------------------------------------------------------------------------------

def test_fix2gate_a_burst_of_stores_leaves_no_file_unlisted_and_the_folder_at_its_cap(tmp_path, monkeypatch):
    """The review's burst: stores as fast as they can be issued, from three callers, over a cap of 20. With the unlinks held until all 300 are issued, 280 files are on the list at
    once; none may be lost from it."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=20)
    folder = tmp_path / "tts-cache"
    release = threading.Event()
    real = os.unlink

    def unlink(path, *a, **k):
        if str(path).startswith(str(folder)):
            release.wait(20)
        return real(path, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)

    def caller(first):
        for i in range(first, 300, 3):
            voice.remember(f"skippy|burst line {i}", b"b")
    callers = [threading.Thread(target=caller, args=(k,)) for k in range(3)]
    for t in callers:
        t.start()
    for t in callers:
        t.join()
    deadline = time.time() + 20
    while len(on_disk(folder)) < 300 and time.time() < deadline:                       # (every file is written; none is removed: the unlinks are held)
        time.sleep(0.02)
    assert len(on_disk(folder)) == 300
    release.set()
    settle(voice, 60)
    monkeypatch.undo()
    assert len(on_disk(folder)) == 20 and not unlisted(voice, folder) and not voice._doomed


def test_fix2gate_bursts_of_stores_with_nothing_held_leave_no_file_unlisted(tmp_path, caplog):
    """The same burst on the real disk with nothing held back, several times, over one text each and over a few texts said again and again."""
    problems = []
    for run, pool in enumerate((None, None, 12, 12)):
        sub = tmp_path / f"run{run}"
        sub.mkdir()
        voice = gate.Voice(str(sub), time.time, cache_keep=20)
        texts = [f"skippy|line {i}" for i in range(pool or 300)]
        seq = [texts[i % len(texts)] for i in range(300)]
        with caplog.at_level(logging.WARNING, logger="voice-gate"):
            caplog.clear()

            def caller(k, seq=seq, voice=voice):
                for t in seq[k::3]:
                    voice.remember(t, b"a:" + t.encode())
            callers = [threading.Thread(target=caller, args=(k,)) for k in range(3)]
            for t in callers:
                t.start()
            for t in callers:
                t.join()
            settle(voice, 60)
            given_up = sum("would not go" in r.getMessage() for r in caplog.records)
        folder = sub / "tts-cache"
        stray = unlisted(voice, folder)
        if len(stray) > given_up:                                                      # (a file that was refused every time was said so: it is the only one that may stay)
            problems.append((run, pool, len(stray), given_up))
        assert not list(folder.glob("*.tmp"))
    assert not problems, problems


def test_fix2gate_a_burst_does_not_cost_every_store_the_whole_list(tmp_path, monkeypatch):
    """A pass takes the files on the list for itself: while one pass is at them, a store running beside it finds none to try as well. (Keeping the whole list for everybody, which is what
    the first form of this fix did, made every one of 2000 stores in a burst go down all of it: 76 s. Windows refuses a file another thread is deleting, too.) 300 stores with the unlinks
    held so that the list is long: each file is unlinked once."""
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=10)
    folder = tmp_path / "tts-cache"
    release = threading.Event()
    real, attempts = os.unlink, []

    def unlink(path, *a, **k):
        if str(path).startswith(str(folder)):
            release.wait(20)
            attempts.append(os.path.basename(str(path)))
        return real(path, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)
    for i in range(300):
        voice.remember(f"skippy|line {i}", b"b")
    deadline = time.time() + 20
    while len(on_disk(folder)) < 300 and time.time() < deadline:
        time.sleep(0.02)
    release.set()
    settle(voice, 60)
    monkeypatch.undo()
    assert len(on_disk(folder)) == 10
    assert len(attempts) <= 290 + 10, len(attempts)                                    # 290 files to remove, each tried about once (the old code: tens of thousands)
