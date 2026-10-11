"""Silence detection and the wake word: the same behaviour as the browser's (portal/static/skippy-voice.js), against simulated microphones."""
from vad import Vad, rms
from wake import is_goodbye, is_stop, wake_split


def run(vad, levels):
    events, t = [], 0
    for lv, ms in levels:
        for _ in range(0, ms, 50):
            t += 50
            e = vad.push(lv, t)
            if e:
                events.append((e, t))
    return events


def test_rms_is_the_loudness_of_a_block():
    assert rms([]) == 0.0 and abs(rms([0.5, -0.5, 0.5, -0.5]) - 0.5) < 1e-9 and rms([0.0] * 100) == 0.0


def test_speech_begins_once_and_ends_only_after_a_pause():
    ev = run(Vad(), [(0.004, 1000), (0.12, 1200), (0.004, 1000), (0.004, 1000)])
    assert [e[0] for e in ev] == ["speech", "end"]
    assert 1000 <= ev[0][1] <= 1400
    assert 900 <= ev[1][1] - 2200 <= 1400                              # the end comes about 1.1 s into the pause


def test_a_short_pause_inside_a_sentence_does_not_end_it():
    ev = run(Vad(), [(0.004, 800), (0.12, 1000), (0.004, 600), (0.12, 1000), (0.004, 1800)])
    assert [e[0] for e in ev] == ["speech", "end"]


def test_a_quiet_room_is_silence_a_steady_noisy_room_is_not_speech_and_a_click_is_not_speech():
    assert run(Vad(), [(0.004, 3000)]) == []
    noisy = run(Vad(), [(0.05, 2500), (0.35, 800), (0.05, 2000)])        # a fan, then a voice well above it
    assert [e[0] for e in noisy] == ["speech", "end"] and noisy[0][1] >= 2500
    assert run(Vad(), [(0.004, 600), (0.2, 50), (0.004, 3000)]) == []


def test_wake_word_cases():
    cases = [("Hey Skippy, turn that down", "turn that down"), ("Skippy.", ""), ("OK Skippy", ""), ("um skippy what time is it", "what time is it"),
             ("Hi Skippie! play some music", "play some music"), ("skippy,   lights off", "lights off")]
    for said, rest in cases:
        assert wake_split(said) == (True, rest), said
    for said in ("the skipping rope", "what are we having for dinner", "skip this one", "", None, "x " * 40 + "skippy"):
        assert wake_split(said)[0] is False, said


def test_stop_and_goodbye_only_when_said_alone():
    for yes in ("stop", "Cancel.", "never mind", "that's all", "thanks", "okay stop"):
        assert is_stop(yes), yes
    for no in ("stop the music and play jazz", "cancel my timer", "go on"):
        assert not is_stop(no), no
    for yes in ("goodbye", "Bye bye.", "stop listening", "talk to you later"):
        assert is_goodbye(yes), yes
    assert not is_goodbye("goodbye cruel world, play a song")
