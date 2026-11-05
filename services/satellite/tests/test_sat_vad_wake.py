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


def run_on(vad, levels, t=0):
    """Like run(), but on a clock that carries on from `t`: returns (events, t)."""
    events = []
    for lv, ms in levels:
        for _ in range(0, ms, 50):
            t += 50
            e = vad.push(lv, t)
            if e:
                events.append((e, t))
    return events, t


def test_an_armed_vad_starts_on_a_soft_command_the_normal_bar_misses():
    """The Captain, 2026-11-05: "Skippy hears his wake word well, but the following commands I have to shout"."""
    assert run(Vad(floor_min=0.015), [(0.004, 800), (0.04, 1500), (0.004, 2000)]) == []        # the normal bar is 0.048: nothing began
    vad = Vad(floor_min=0.015)
    _, t = run_on(vad, [(0.004, 800)])                                                          # the room's quiet is learned
    vad.arm(0.04)                                                                               # and the name has just been said, about as softly
    ev, _ = run_on(vad, [(0.04, 1500), (0.004, 2000)], t)
    assert [e[0] for e in ev] == ["speech", "end"]


def test_arming_never_raises_the_bar_and_a_restart_disarms():
    vad = Vad(floor_min=0.015)
    run_on(vad, [(0.004, 800)])
    normal = vad.bar()
    vad.arm(0.5)                                                                                # a shouted name: a share of it is above the normal bar
    assert vad.start_bar() == normal
    vad.arm(0.0)
    assert vad.start_bar() < normal
    vad.restart()
    assert vad.start_bar() == normal


def test_a_dip_between_syllables_no_longer_throws_away_what_was_counted():
    vad = Vad(floor_min=0.015)
    _, t = run_on(vad, [(0.004, 800)])
    ev, _ = run_on(vad, [(0.1, 50), (0.004, 50)] * 12 + [(0.004, 2000)], t)                     # a voice that flickers across the bar
    assert [e[0] for e in ev] == ["speech", "end"]
    lone = Vad(floor_min=0.015)
    _, t = run_on(lone, [(0.004, 800)])
    ev, _ = run_on(lone, [(0.1, 50), (0.004, 400), (0.1, 50), (0.004, 400), (0.1, 50), (0.004, 2000)], t)
    assert ev == []                                                                             # three separate clicks are still no voice


def test_while_a_command_is_expected_a_soft_voice_cannot_raise_the_rooms_quiet_but_a_quieter_room_lowers_it():
    vad = Vad(floor_min=0.012)
    _, t = run_on(vad, [(0.03, 800)])                                                           # music in the room: its quiet is 0.03
    vad.arm(0.0)
    _, t = run_on(vad, [(0.04, 300)], t)                                                        # a soft voice under the armed bar
    assert vad.quiet <= 0.03 + 1e-9
    run_on(vad, [(0.012, 3000)], t)                                                             # the Enclave ducks under his name
    assert vad.quiet < 0.02


def test_arming_lapses_by_itself_so_a_louder_room_is_learned_again():
    """A detector left armed (a window that closed with no command) learnt the room's quiet downward only: a TV turned on later held 'speech' open for ever."""
    from vad import ARM_MS
    vad = Vad(floor_min=0.015)
    _, t = run_on(vad, [(0.004, 800)])
    vad.arm(0.0)
    assert vad.start_bar() < vad.bar()
    _, t = run_on(vad, [(0.004, ARM_MS + 500)], t)
    assert vad._armed is None and vad.start_bar() == vad.bar()
    ev, t = run_on(vad, [(0.1, 6000)], t)                                                         # the room gets louder and stays: speech begins ...
    assert [e[0] for e in ev] == ["speech"]
    ev, _ = run_on(vad, [(0.1, 2000), (0.004, 3000)], t)                                          # ... and ends when it stops
    assert [e[0] for e in ev] == ["end"]


def test_loudest_is_how_loud_a_voice_was_not_how_loud_a_click_was():
    from vad import loudest
    assert loudest([]) == 0.0 and abs(loudest([0.1, 0.3]) - 0.2) < 1e-9
    assert abs(loudest([0, 0, 0.9, 0, 0]) - 0.3) < 1e-9 and abs(loudest([0.1, 0.2, 0.3, 0.2, 0.1]) - 0.7 / 3) < 1e-9


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
