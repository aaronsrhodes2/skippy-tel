"""The devices decide (the Captain, 2026-11-05: "a single audio stream going out and the devices decide if they are outputting that stream or not"; "If no devices are
listening, the stream should pause"). Every speaker has a switch kept on disk; the places are shortcuts that set them; a box covers only for a stereo that is on and gone;
and with nobody listening the stream is held where the sound was lost and carries on when a speaker is back."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outputs as O  # noqa: E402
import radio  # noqa: E402
import station  # noqa: E402
from test_sat_radio import ENV, Clock, call, make_house, on_loop, stop_all  # noqa: E402

FLOORS = {"hdmi": "downstairs", "enclave": "upstairs", "pod:living": "upstairs"}


def outs(path=None, clock=None, seed=None):
    return O.Outputs(["hdmi", "enclave"], ["living"], FLOORS, path=path, clock=clock or Clock(), seed=seed, log=lambda *a: None)


# ── the switches ──
def test_every_stereo_is_on_and_a_box_is_silent_until_it_is_needed():
    o = outs()
    assert o.effective(lambda k: True) == {"hdmi", "enclave"} and o.mode_of("pod:living") == "auto" and o.ids() == ["hdmi", "enclave", "pod:living"]
    snap = {x["id"]: x for x in o.snapshot(lambda k: k == "hdmi", {"living"})}
    assert snap["hdmi"] == {"id": "hdmi", "name": "Sony", "on": True, "open": True, "floor": "downstairs"}
    assert snap["enclave"]["on"] is True and snap["enclave"]["open"] is False                  # on, but not there right now
    assert snap["pod:living"]["on"] is False and snap["pod:living"]["mode"] == "auto"


def test_the_switches_are_kept_on_disk_and_a_lost_or_torn_file_means_every_stereo_on(tmp_path):
    path = tmp_path / "outputs.json"
    o = outs(path)
    assert o.set("hdmi", False) and not o.set("hdmi", False)                                   # a real change is saved, the same again is not
    assert o.set("pod:living", True)
    again = outs(path)                                                                          # a restart of the radio or the hub
    assert again.loaded and again.on == {"hdmi": False, "enclave": True} and again.mode_of("pod:living") == "on"
    path.write_text('{"stereo": {"hdmi": fal', encoding="utf-8")                                # torn in the middle of a write
    torn = outs(path)
    assert not torn.loaded and torn.effective(lambda k: True) == {"hdmi", "enclave"}            # never silence
    path.write_text(json.dumps({"stereo": {"hdmi": "no", "enclave": False}, "pods": {"living": "maybe"}}), encoding="utf-8")
    odd = outs(path)
    assert odd.on == {"hdmi": True, "enclave": False} and odd.mode_of("pod:living") == "auto"   # what is not understood is ignored
    path.unlink()
    assert outs(path).effective(lambda k: True) == {"hdmi", "enclave"}


def test_the_first_start_keeps_the_stream_where_it_plays_now(tmp_path):
    o = outs(tmp_path / "outputs.json", seed=["enclave"])                                       # station.json said: upstairs only
    assert o.on == {"hdmi": False, "enclave": True} and (tmp_path / "outputs.json").exists()
    assert outs(tmp_path / "outputs.json", seed=["hdmi", "enclave"]).on == {"hdmi": False, "enclave": True}      # and from then on the file rules, not the seed


def test_a_place_is_only_a_shortcut_that_sets_the_switches():
    o = outs()
    o.place(["enclave"])                                                                        # "upstairs"
    assert o.on == {"hdmi": False, "enclave": True}
    o.place(["hdmi", "enclave"])                                                                # "everywhere"
    assert o.on == {"hdmi": True, "enclave": True}
    o.place(["pod:living"])                                                                     # a room with only its box
    assert o.on == {"hdmi": False, "enclave": False} and o.mode_of("pod:living") == "on"
    o.place(["hdmi"])
    assert o.mode_of("pod:living") == "auto"                                                    # a box not named is back to covering only


# ── a box covers only for a stereo that is on and gone ──
def test_a_box_covers_for_its_floors_stereo_only_after_twenty_seconds_and_goes_quiet_when_it_is_back():
    clock, up = Clock(), {"hdmi", "enclave"}
    o = outs(clock=clock)
    there = lambda k: k in up                                                                   # noqa: E731
    assert o.effective(there) == {"hdmi", "enclave"}
    up.discard("enclave")                                                                       # the Enclave drops (a Tesla charge command)
    assert o.effective(there) == {"hdmi", "enclave"}                                            # on, said so, just not there yet
    clock.t += 19.0
    assert "pod:living" not in o.effective(there)
    clock.t += 2.0
    assert o.effective(there) == {"hdmi", "enclave", "pod:living"}                              # its floor's stereo has been gone for 21 s: the living room's box covers
    assert o.listening(there, set()) == {"hdmi"} and o.listening(there, {"living"}) == {"hdmi", "pod:living"}
    up.add("enclave")
    assert "pod:living" not in o.effective(there) and o.down_since == {}


def test_off_means_off_a_box_never_covers_for_a_stereo_he_switched_off_or_when_it_is_off_itself():
    clock, up = Clock(), {"hdmi"}
    o = outs(clock=clock)
    there = lambda k: k in up                                                                   # noqa: E731
    o.set("enclave", False)
    clock.t += 600.0
    assert o.effective(there) == {"hdmi"}                                                       # upstairs is off by his choice, not gone: the box does not cover
    o.set("enclave", True)
    clock.t += 1.0
    o.effective(there)
    clock.t += 25.0
    assert "pod:living" in o.effective(there)
    o.set("pod:living", False)                                                                  # "turn the living room box off"
    assert "pod:living" not in o.effective(there) and o.mode_of("pod:living") == "off"
    o.set("pod:living", True)
    o.set("enclave", False)
    assert o.effective(there) == {"hdmi", "pod:living"}                                         # a box that is on plays, whatever the stereos do


# ── on the station: what plays moves at once, and never restarts ──
def test_flipping_a_switch_moves_what_plays_at_once_without_restarting_it():
    loop = on_loop()
    h, r = make_house()
    call(loop, h.box.play_queue(list(CAT_ITEMS)))
    play = r.plays[max(r.plays)]
    assert play.targets == {"hdmi"} and h.speakers == {"hdmi"}
    assert h.set_switch("enclave", True) and play.targets == {"hdmi", "enclave"} and len(r.plays) == 1 and r.plays[play.id] is play      # the same play, the same song
    assert h.set_switch("hdmi", False) and play.targets == {"enclave"} and h.speakers == {"enclave"}
    assert h.box.playing
    stop_all(loop, h)


def test_connect_flips_a_switch_and_the_state_carries_the_speakers():
    loop = on_loop()
    h, r = make_house(mono=Clock())
    call(loop, h.box.play_queue(list(CAT_ITEMS)))
    st = h.box.state()
    assert [o["id"] for o in st["outs"]] == ["hdmi", "enclave", "pod:living"] and st["held"] is None and st["kind"] == "song"
    call(loop, h.box.command("out:enclave:on", []))
    assert h.speakers == {"hdmi", "enclave"} and [o["on"] for o in h.box.state()["outs"]] == [True, True, False]
    call(loop, h.box.command("out:hdmi:off", []))
    assert h.speakers == {"enclave"}
    call(loop, h.box.command("out:nowhere:off", []))                                            # not a speaker it has: nothing happens
    call(loop, h.box.command("out:enclave:maybe", []))
    assert h.speakers == {"enclave"}
    assert h.switch_command("out:pod:living:on") and "pod:living" in h.speakers and "pods" in r.plays[max(r.plays)].targets
    stop_all(loop, h)


def test_the_speakers_tool_turns_one_speaker_on_or_off_in_his_words():
    loop = on_loop()
    h, r = make_house(mono=Clock())
    call(loop, h.box.play_queue(list(CAT_ITEMS)))
    run = lambda **a: call(loop, station.run(h, "speakers", {"what": "music", **a}, "living"))     # noqa: E731
    assert run(device="the Sony", on=True) == "Done: the Sony on. The music plays downstairs now."
    assert run(device="the Enclave", on=True).startswith("Done: the Enclave on. The music plays everywhere now.")
    assert run(device="sony", on=False) == "Done: the Sony off. The music plays upstairs now."
    assert h.speakers == {"enclave"} and h.outputs.mode_of("hdmi") == "off"
    assert run(device="the living room box", on=True).startswith("Done: the Living box on.") and "pod:living" in h.speakers
    assert run(device="the toaster", on=False).startswith("I don't know that speaker")
    assert run(device="enclave", on="false").startswith("Done: the Enclave off.")                  # a string from a model still means off
    assert run(place="downstairs").startswith("Done: the music plays downstairs") and h.speakers == {"hdmi"}      # a place is a shortcut for several switches
    stop_all(loop, h)


def test_a_play_that_names_no_place_leaves_the_switches_alone_and_one_that_names_a_place_sets_them():
    loop = on_loop()
    h, r = make_house()
    h.place(["enclave"])
    said = call(loop, station.run(h, "play_music", {"what": "Pink Floyd"}, "office", None))        # asked from a room whose own place is not upstairs
    assert said.startswith("Playing 2 tracks upstairs") and h.speakers == {"enclave"}
    said = call(loop, station.run(h, "play_music", {"what": "Pink Floyd", "speaker": "downstairs"}, "office", None))
    assert said.startswith("Playing 2 tracks downstairs") and h.speakers == {"hdmi"}
    stop_all(loop, h)


# ── nobody listening, no stream ──
CAT_ITEMS = ["Pink Floyd/Animals/01 Dogs.mp3", "Pink Floyd/Animals/02 Pigs.mp3", "Bauhaus/Flat Field/Dark Entries.mp3"]


class Setup:
    """A station with the clocks in the test's hands, and the stereos it can reach in a set."""

    def __init__(self, tmp_path=None, up=("hdmi", "enclave"), **kw):
        self.mono, self.clock, self.wall = Clock(), Clock(), Clock()
        self.h, self.r = make_house(self.clock, mono=self.mono, wall=self.wall, switches=(tmp_path / "outputs.json") if tmp_path else None, **kw)
        self.up = set(up)
        self.h._there = lambda k: k in self.up
        self.loop = on_loop()

    def run(self, coro):
        return call(self.loop, coro)

    def elapse(self, seconds: float, ticks: int = 1):
        for _ in range(ticks):
            self.mono.t += seconds / ticks
            self.clock.t += seconds / ticks
            self.wall.t += seconds / ticks
            self.run(self.h.listen_tick())


def test_the_last_speaker_switched_off_holds_the_stream_at_once_and_a_speaker_back_carries_it_on():
    s = Setup()
    s.h.place(["enclave"])
    s.run(s.h.box.play_queue(CAT_ITEMS))
    s.elapse(5.0)
    assert s.h.box.playing and s.h.held is None and s.h.listening() == {"enclave"}
    s.h.set_switch("enclave", False)                                                            # nobody is left to hear it
    s.elapse(1.0)
    assert not s.h.box.playing and s.h.held["kind"] == "nobody" and s.h.held_public()["why"] == "no speaker is on"
    assert not s.r.plays and s.h.box.queue == CAT_ITEMS                                         # paused, the queue and the place kept
    place = s.h.box.offset
    assert place == pytest.approx(6.0, abs=1.1)
    s.elapse(60.0, 6)                                                                           # minutes of nothing: still held, no ghost playing
    assert not s.h.box.playing and s.h.box.offset == place
    s.h.set_switch("hdmi", True)                                                                # he turns the Sony on
    s.elapse(1.0)
    assert s.h.box.playing and s.h.held is None and s.h.speakers == {"hdmi"}
    assert s.r.plays[max(s.r.plays)].source.proc.args[s.r.plays[max(s.r.plays)].source.proc.args.index("-i") + 1] == f"http://radio/{CAT_ITEMS[0]}?s={place:.1f}"      # the same song at the same second
    stop_all(s.loop, s.h)


def test_a_speaker_that_drops_gets_thirty_seconds_and_then_the_stream_is_held_where_the_sound_was_lost():
    s = Setup()
    s.h.place(["enclave"])
    s.run(s.h.box.play_queue(CAT_ITEMS))
    s.elapse(10.0)
    s.up.discard("enclave")                                                                     # the Enclave drops out (a Tesla charge command)
    lost = s.h.box.pos()
    s.elapse(29.0, 29)
    assert s.h.box.playing and s.h.held is None                                                 # it may be back in a moment: nothing yet
    s.elapse(3.0, 3)
    assert not s.h.box.playing and s.h.held["kind"] == "nobody"
    assert s.h.box.offset == pytest.approx(lost, abs=1.1)                                       # put back to where it stopped reaching anyone, not 30 s on
    s.up.add("enclave")                                                                         # and it is back
    s.elapse(2.0, 2)
    assert s.h.box.playing and s.h.held is None and s.h.box.pos() == pytest.approx(lost + 2.0, abs=2.1)
    stop_all(s.loop, s.h)


def test_a_short_drop_is_bridged_without_a_pause():
    s = Setup()
    s.h.place(["enclave"])
    s.run(s.h.box.play_queue(CAT_ITEMS))
    s.elapse(3.0)
    s.up.discard("enclave")
    s.elapse(25.0, 25)
    s.up.add("enclave")
    s.elapse(5.0, 5)
    assert s.h.box.playing and s.h.held is None and s.h.box.idx == 0                            # nothing paused, nothing replayed
    stop_all(s.loop, s.h)


def test_a_box_that_covers_for_a_dropped_stereo_means_the_stream_is_not_held(monkeypatch):
    s = Setup()
    tap = radio.Tap(lambda *a: None, start=False)
    monkeypatch.setitem(radio.TAPS, "tap://pods", tap)
    s.h.place(["enclave"])
    s.run(s.h.box.play_queue(CAT_ITEMS))
    s.up.discard("enclave")
    s.elapse(21.0, 21)
    assert "pod:living" in s.h.speakers                                                         # the living room's box was asked to cover
    tap.listen(label="living")                                                                  # and it tuned in
    s.elapse(60.0, 60)
    assert s.h.box.playing and s.h.held is None and s.h.listening() == {"pod:living"}
    stop_all(s.loop, s.h)


def test_a_held_stream_does_not_carry_on_after_ten_minutes_or_after_a_command():
    s = Setup()
    s.h.place(["enclave"])
    s.run(s.h.box.play_queue(CAT_ITEMS))
    s.h.set_switch("enclave", False)
    s.elapse(1.0)
    assert s.h.held
    s.elapse(660.0, 11)                                                                         # last night's music does not start under tomorrow's TV
    assert s.h.held is None
    s.h.set_switch("hdmi", True)
    s.elapse(2.0, 2)
    assert not s.h.box.playing
    s.run(s.h.box.resume())                                                                     # (his own "resume")
    s.h.set_switch("hdmi", False)
    s.elapse(1.0)
    assert s.h.held and not s.h.box.playing
    s.run(s.h.box.pause())                                                                      # he says stop while it waits: that cancels the wait
    s.h.set_switch("enclave", True)
    s.elapse(2.0, 2)
    assert not s.h.box.playing and s.h.held is None
    stop_all(s.loop, s.h)


def test_playing_with_every_speaker_off_is_held_at_once_and_says_so():
    s = Setup()
    s.h.place([])
    said = s.run(station.run(s.h, "play_music", {"what": "Pink Floyd"}, "living", None))
    assert "But no speaker is on, so it waits" in said and "(the Sony and the Enclave switched off)" in said
    s.elapse(1.0)
    assert not s.h.box.playing and s.h.held["kind"] == "nobody" and s.h.box.queue                # queued, held, nothing playing to nobody
    s.run(station.run(s.h, "speakers", {"what": "music", "device": "the Sony", "on": True}, "living"))
    s.elapse(1.0)
    assert s.h.box.playing and s.h.held is None
    stop_all(s.loop, s.h)


def test_a_hold_is_not_his_stop_it_survives_a_radio_restart_and_the_kill_switch_turns_it_off(tmp_path):
    s = Setup(tmp_path)
    s.h.place(["enclave"])
    s.run(s.h.box.play_queue(CAT_ITEMS))
    s.elapse(4.0)
    s.h.set_switch("enclave", False)
    s.elapse(1.0)
    rad = radio.Radio(ENV, s.h, state=tmp_path, log=lambda *a: None)
    rad.save()
    saved = json.loads((tmp_path / "station.json").read_text())
    assert saved["held"]["kind"] == "nobody" and not saved["playing"]
    stop_all(s.loop, s.h)
    s2 = Setup(tmp_path)                                                                        # a restarted radio: the switches come from the file
    assert s2.run(s2.h.restore(rad.load(), log=lambda *a: None)) is False
    assert s2.h.held["kind"] == "nobody" and not s2.h.box.playing and s2.h.speakers == set()
    s2.h.set_switch("hdmi", True)
    s2.elapse(2.0, 2)
    assert s2.h.box.playing and s2.h.held is None                                               # it carries on after the restart
    stop_all(s2.loop, s2.h)
    off = Setup(hold_s=0)                                                                       # RADIO_HOLD_S=0
    off.h.place([])
    off.run(off.h.box.play_queue(CAT_ITEMS))
    off.elapse(120.0, 10)
    assert off.h.box.playing and off.h.held is None and off.h.listening_note() == ""
    stop_all(off.loop, off.h)


def test_a_book_held_for_lack_of_a_speaker_keeps_its_place(tmp_path):
    from test_sat_book_item import DRAGON, make_station          # noqa: PLC0415
    import audiobooks as A                                       # noqa: PLC0415
    mono = Clock()
    h, r, eng, wall = make_station(tmp_path, mono=mono)
    loop = on_loop()
    h.place(["hdmi"])
    h._there = lambda k: k == "hdmi"
    call(loop, h.box.play_queue([A.item(DRAGON)]))
    wall.t += 5.0
    mono.t += 5.0
    h.set_switch("hdmi", False)
    call(loop, h.listen_tick())
    assert not h.box.playing and not eng.playing and h.held["kind"] == "nobody"
    assert eng.store.progress(DRAGON)["pos"] == pytest.approx(5.0, abs=0.2)                      # the book's place, in the store
    h.set_switch("hdmi", True)
    call(loop, h.listen_tick())
    assert h.box.playing and eng.playing
    stop_all(loop, h)


def test_the_chat_says_each_speakers_state_truthfully_playing_only_while_it_plays():
    import houseaudio                                                                           # noqa: PLC0415
    outs = [{"id": "hdmi", "name": "Sony", "on": False, "open": True, "floor": "downstairs"}, {"id": "enclave", "name": "Enclave", "on": True, "open": True, "floor": "upstairs"},
            {"id": "pod:living", "name": "Living box", "on": False, "open": False, "floor": "upstairs", "mode": "auto"}]
    paused = houseaudio.station_line(dict(playing=False, title="T", artist="A", open=["enclave"], level=0.5, queue_len=3, outputs=outs))
    assert "Speakers: the Sony downstairs is switched off, the Enclave upstairs is on and the box in the living is silent" in paused and "playing it" not in paused
    playing = houseaudio.station_line(dict(playing=True, title="T", artist="A", open=["enclave"], level=0.5, queue_len=3, outputs=outs))
    assert "the Enclave upstairs is playing it" in playing
    gone = houseaudio.station_line(dict(playing=True, title="T", artist="A", open=[], level=0.5, queue_len=3, outputs=[dict(outs[0], on=True, open=False)] + outs[1:]))
    assert "the Sony downstairs is on but not connected right now" in gone
    held = houseaudio.station_line(dict(playing=False, title="T", artist="A", open=[], level=0.5, queue_len=3, held={"kind": "nobody", "why": "no speaker is on", "since": 1.0},
                                        outputs=[dict(o, on=False) if o["id"] != "pod:living" else o for o in outs]))
    assert "Bilby is holding T by A: no speaker is on, so there is nobody to play it to. It carries on by itself when a speaker is turned on" in held
    car = houseaudio.station_line(dict(playing=False, kind="book", title="Aces", book={"title": "Aces", "author": "A. Writer", "chapter": 3, "chapters": 9}, held={"kind": "car", "why": "x", "since": 1.0}))
    assert "he is listening to it in the car" in car and "'Play my book' carries on from the car's place" in car
