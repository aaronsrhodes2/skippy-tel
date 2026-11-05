"""houseaudio.py: the lines the house chat reads about what plays and who started it (the review of 2026-11-05: status lines that stated things that were not so).

* a radio that went away after it was up: its stale last state said "Bilby is playing ..." while Audio.playing() was False;
* `open: []` (a stream that no speaker is hearing) fell back to the speakers that were CHOSEN, and the line said "playing ... on the Enclave ... Switched off: the Enclave";
* "started by the morning routine at 7:10" outlived a start that failed, a queue he replaced from his phone, and the day;
* the bedtime routine was said to start the library (it has no music step);
* the house log's last 40 raw records (wake and action events included) were cut BEFORE the commands and routines were picked out, so a routine's "started the music" vanished after
  about eight later commands.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import houseaudio  # noqa: E402
import housemem  # noqa: E402


@pytest.fixture(autouse=True)
def no_note_left_over():
    """houseaudio keeps one note of who started the music, for the whole process: these tests set it, so none may leave it for another test (or find one from a routine test)."""
    houseaudio._STARTED.clear()
    yield
    houseaudio._STARTED.clear()


class Radio:
    """A RadioClient: when the radio stops answering `ok` goes False and `last` stays the last state it sent (state() hands it out), as the real client does."""

    def __init__(self, ok=True, **state):
        self.ok, self.last = ok, state

    @property
    def playing(self):
        return bool(self.ok and self.last.get("playing"))

    def state(self):
        return dict(self.last)


STATE = dict(playing=True, title="Gasoline", artist="MDFMK", open=["enclave"], speakers=["enclave"], closed=[], level=0.7, queue_len=700)


def test_a_radio_that_stopped_answering_is_said_not_to_answer_and_never_to_play():
    houseaudio._STARTED.clear()
    dead = houseaudio.Audio(Radio(ok=False, **STATE))
    status = dead.status()
    assert dead.playing() is False and "not answering" in status and "Gasoline" not in status and "is playing" not in status
    assert "Bilby is playing Gasoline by MDFMK on the Enclave upstairs." in houseaudio.Audio(Radio(ok=True, **STATE)).status()
    assert "Bilby is playing Gasoline" in houseaudio.Audio(NS(state=lambda: dict(STATE), playing=True)).status()            # (a radio with no `ok`, as in the older tests, is taken as up)


def test_a_stream_that_no_speaker_hears_is_said_so_not_papered_over_with_the_speakers_that_were_chosen():
    gone = dict(playing=True, title="T", artist="A", open=[], speakers=["enclave"], closed=["enclave"], level=0.4, queue_len=5)
    line = houseaudio.station_line(gone)
    assert "on the Enclave upstairs" not in line and "no speaker" in line and "Switched off right now: the Enclave upstairs." in line and line.startswith("Bilby is playing T by A")
    older = houseaudio.station_line(dict(playing=True, title="T", artist="A", speakers=["enclave"], queue_len=5))            # a radio that sends no `open` at all
    assert "on the Enclave upstairs" in older
    assert "on the Enclave upstairs and the Sony downstairs" in houseaudio.station_line(dict(playing=True, title="T", open=["enclave", "hdmi"], queue_len=5))


def test_the_started_by_note_is_not_made_by_a_failed_start_and_is_dropped_when_the_queue_is_replaced():
    houseaudio._STARTED.clear()
    houseaudio.note_start("the morning routine", ok=False)                                         # the start failed (the radio was away): nothing to remember
    assert houseaudio._STARTED == {}
    houseaudio.note_start("the morning routine")
    houseaudio.note_start("his own request", ok=False)                                             # ... and a failed start does not wipe the note of the one before it
    assert houseaudio._STARTED["by"] == "the morning routine"
    radio = Radio(**STATE)
    a = houseaudio.Audio(radio)
    assert "This music was started by the morning routine at" in a.status() and "started by the morning routine" in a.status()      # the same queue: still true
    radio.last = {**STATE, "queue_len": 30}                                                       # he put another queue on from his phone (nothing notes that)
    assert "started by" not in a.status() and houseaudio._STARTED == {}
    houseaudio.note_start("his own request")                                                       # a new start is a new note, with a fresh look at the queue
    assert "started by his own request" in a.status()
    houseaudio._STARTED.clear()


def test_a_start_that_started_nothing_is_told_by_its_answer():
    for said in ("The house radio is not answering right now.", "The house radio could not do that (ValueError).", "Nothing in his library matches 'zzz'.",
                 "Nothing in his library to play right now (the music gate is not answering).", "  The house radio is not answering right now."):
        assert not houseaudio.start_ok(said), said
    for said in ("Playing 3 tracks of heavy metal.", "Nothing polka in his library, so here is the closest. Playing 12 tracks.", "Done: the music plays upstairs.", "", None):
        assert houseaudio.start_ok(said), said


def test_the_music_tools_the_log_is_read_for_are_the_music_guards():
    import musicguard
    assert houseaudio.MUSIC_TOOLS == musicguard.MUSIC_TOOLS


def test_a_routine_with_no_music_step_is_not_said_to_start_the_library():
    morning = NS(busy=True, run=NS(name="morning", steps=[NS(title="the news"), NS(title="music")]))
    bedtime = NS(busy=True, run=NS(name="bedtime", steps=[NS(title="the weather"), NS(title="")]))
    assert "its music step starts the library" in houseaudio.Audio(Radio(playing=False), morning).status()
    said = houseaudio.Audio(Radio(playing=False), bedtime).status()
    assert "bedtime routine is running" in said and "music step" not in said and "library" not in said
    legacy = NS(busy=True, run=NS(name="homecoming"))                                             # no steps to read: the two routines that have a music step, by name
    assert "its music step starts the library" in houseaudio.Audio(Radio(playing=False), legacy).status()
    assert "music step" not in houseaudio.Audio(Radio(playing=False), NS(busy=True, run=NS(name="bedtime"))).status()


def test_the_routines_music_line_is_not_lost_behind_forty_later_log_lines(tmp_path):
    now = [time.mktime((2026, 11, 4, 19, 0, 0, 0, 0, -1))]
    log = housemem.HouseLog(tmp_path, clock=lambda: now[0])
    log.event("routine", routine="homecoming", outcome="started the music")
    for _ in range(30):                                                                           # a busy half hour: a wake and an action for every command
        now[0] += 40
        log.event("wake", room="living")
        log.event("action", room="living", what="light", tool="home_control")
    log.event("command", words="what time is it", tools=[])
    houseaudio._STARTED.clear()
    status = houseaudio.Audio(Radio(**STATE), hlog=log).status()
    assert "19:00 the homecoming routine: started the music" in status and 'he said "what time is it"' in status


def test_music_lately_reads_the_house_log_for_a_music_tool_or_a_routines_music(tmp_path):
    log = housemem.HouseLog(tmp_path / "a")
    a = houseaudio.Audio(None, hlog=log)
    assert a.music_lately() is False
    log.event("command", words="what time is it", tools=[])
    log.event("routine", routine="morning", outcome="done")
    assert a.music_lately() is False
    log.event("command", words="stop the music", tools=["music"])
    assert a.music_lately() is True
    routine = housemem.HouseLog(tmp_path / "b")
    routine.event("routine", routine="homecoming", outcome="started the music")
    assert houseaudio.Audio(None, hlog=routine).music_lately() is True
    routine = housemem.HouseLog(tmp_path / "c")
    routine.event("routine", routine="homecoming", outcome="left the music off: he said stop")
    assert houseaudio.Audio(None, hlog=routine).music_lately() is True                           # a stop that kept the music off is a music event too
    assert houseaudio.Audio(None, hlog=NS(recent=lambda **kw: 1 / 0)).music_lately() is False and houseaudio.Audio(None).music_lately() is False
