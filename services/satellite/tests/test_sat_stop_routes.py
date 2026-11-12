"""Stopping, turning down and turning off, read by brain.music_route before the model runs (the review of 2026-11-05 of 7509b5c "every way of saying stop pauses Bilby").

Four things it got wrong, each pinned here with the phrases from the review's battery:
* "turn that music down" (any audio word but "it / the music / that / the volume / the stereo") PAUSED Bilby: `down` was a way of turning off. It turns the volume down.
* "turn off everything" paused Bilby and left the lights and plugs on (the model was given no tools), and with the station playing "turn off the living room" / "turn it off downstairs"
  paused it too: the place was peeled off and the bare "turn off" that was left counted as a stop.
* "music off" started music: no stop pattern had no verb, and the play grammar searched the library for a song called "off".
* a transcript that repeated "all the" made the stop patterns take exponential time (a regex holds the GIL, so the hub's event loop stood still).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain import Brain, bilby_route, music_grammar, music_route, stop_route  # noqa: E402
from test_sat_brain import Client, text  # noqa: E402
from timers import Timers  # noqa: E402

PAUSE = {"action": "pause"}
QUIETER = {"action": "quieter"}

TURN_DOWN = ["turn that music down", "turn this music down", "turn my music down", "turn the sound down", "turn down the radio", "turn the speakers down", "turn the audio down",
             "turn the heavy metal down", "turn the noise down", "turn down the sound", "turn down this music", "Skippy, turn that music down please", "turn down the speakers"]
STILL_STOPS = ["shut down the music", "power down the stereo", "shut the music down", "power the stereo down", "turn off the music", "turn the speakers off", "switch off the radio",
               "stop everything", "pause everything", "stop all the music", "stop all of the music", "turn off all the music", "turn off all of the music", "shut all the music off"]
EVERYTHING = ["turn off everything", "turn everything off", "shut everything off", "turn off all of it", "switch everything off", "kill everything", "power off everything"]
PLACES = ["turn off the living room", "turn off the theater", "shut off the theater", "turn off the Sony", "turn off upstairs", "turn off the house", "turn it off downstairs",
          "turn off the downstairs", "shut it off upstairs", "switch off the whole house"]
MUSIC_OFF = ["music off", "Bilby, music off", "bilby music off", "the music off", "stereo off", "radio off", "speakers off", "sound off", "audio off", "no music", "no more music",
             "I'm done with the music", "i am done with the music", "enough of this music", "enough of the noise", "that's enough music", "Skippy, music off please"]


def test_turning_the_music_down_by_any_audio_word_is_quieter_never_a_pause():
    for heard in TURN_DOWN:
        for playing in (None, False, True):
            assert music_route(heard, playing) == QUIETER, (heard, playing, music_route(heard, playing))
            assert stop_route(heard, playing) is None, (heard, playing)                        # (and `down` is no longer a way of turning off)
    assert music_route("turn it up a bit", True) == {"action": "louder"} and music_route("turn the music down", True) == QUIETER      # the old forms are unchanged


def test_shutting_down_is_still_a_stop_and_so_is_everything_that_was_a_stop_before():
    for heard in STILL_STOPS:
        for playing in (None, False, True):
            assert music_route(heard, playing) == PAUSE, (heard, playing)


def test_turn_off_everything_and_turn_off_a_place_are_left_to_the_model_which_has_the_house():
    for heard in EVERYTHING + PLACES:
        for playing in (None, False, True):
            assert music_route(heard, playing) is None, (heard, playing, music_route(heard, playing))
    # a place beside an audio word is still a stop (Bilby is one stream: it all stops), and Bilby named makes a bare stop his
    for heard in ("stop the music downstairs", "turn the music off downstairs and upstairs", "turn off the music in the living room", "bilby, turn it off upstairs"):
        assert music_route(heard, None) == PAUSE, heard


def test_music_off_and_its_kin_stop_the_music_and_never_search_the_library_for_off():
    for heard in MUSIC_OFF:
        for playing in (None, False, True):
            assert music_route(heard, playing) == PAUSE, (heard, playing, music_route(heard, playing))
        assert bilby_route(heard) is None, (heard, bilby_route(heard))                      # (a brain with play_music and no music tool must not start a song called "off" either)
    for heard in ("DJ mode off", "dj off", "shuffle off", "Bilby, DJ mode off"):
        assert music_route(heard, True) is None, heard                                    # the modes' own switches (the dj_mode tool), not a stop
    for name in ("off the wall", "quiet riot", "silent running", "mute math", "enough is enough", "out of time"):
        assert music_route(f"play {name}", True) is None, name                            # a title that holds the word is still a title, for the model or the grammar
    assert music_grammar("bilby quiet riot") == {"what": "quiet riot"} and music_grammar("bilby silent running") == {"what": "silent running"}


def stop_brain(*rounds):
    ran = []
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("play_music", "music")]
    client = Client(*rounds)
    b = Brain(client, "m", Timers(clock=lambda: 1000.0), persona="PERSONA", clock=lambda: 1000.0, now=lambda: "Sunday 09:30")
    b.extra_tools, b.run_extra = tools, lambda name, args: ran.append((name, args)) or f"({name} done)"
    return b, client, ran


def test_the_brain_pauses_for_music_off_and_hands_turn_off_everything_to_the_model_with_its_tools():
    b, c, ran = stop_brain([text("Silence.")])
    assert b.reply("music off") == "Silence." and ran == [("music", PAUSE)]                    # it used to run play_music {"what": "off"}: the library started
    b, c, ran = stop_brain([text("Quieter, monkey.")])
    b.reply("turn that music down")
    assert ran == [("music", QUIETER)]
    b, c, ran = stop_brain([text("Lights are the model's business, monkey.")])
    b.audio = NS(playing=lambda: True, status=lambda: "")
    b.reply("turn off everything")
    assert ran == [] and "tools" in c.calls[0] and {t["name"] for t in c.calls[0]["tools"]} >= {"music", "play_music"}       # no pause, and the model has its tools
    b, c, ran = stop_brain([text("Upstairs is a place, not a switch.")])
    b.audio = NS(playing=lambda: True, status=lambda: "")
    b.reply("turn off upstairs")
    assert ran == [] and "tools" in c.calls[0]


def test_a_transcript_that_repeats_all_the_does_not_make_the_stop_patterns_take_exponential_time():
    """'stop ' + 'all the ' * 22 + 'xyz' took 1.5 s per call (n=24: 8 s, doubling with every repeat) and a regex does not let go of the GIL."""
    for heard in ("stop " + "all the " * 22 + "xyz", "turn off " + "all the " * 22 + "xyz", "turn " + "all the " * 22 + "xyz off", "no more " + "all the " * 22 + "xyz",
                  "stop " + "the the " * 30 + "xyz"):
        t0 = time.perf_counter()
        assert stop_route(heard, True) is None and music_route(heard, True) is None
        assert time.perf_counter() - t0 < 0.5, heard
    assert stop_route("stop all the music", True) == PAUSE and stop_route("stop all of the music", None) == PAUSE and stop_route("turn off all the all of the music", None) == PAUSE
