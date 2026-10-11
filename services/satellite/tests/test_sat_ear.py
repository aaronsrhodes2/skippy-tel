"""The whole listener against a simulated room: blocks of sound in, what Skippy hears, thinks and says out. No hardware, no network."""
from timers import Timers
from voicegate import BudgetSpent, GateError
from ear import BLOCK_MS, Ear

QUIET, VOICE = 0.004, 0.12


class FakeGate:
    def __init__(self):
        self.said, self.stt_calls, self.tts_calls, self.sum_calls = [], 0, [], []
        self.stt_error = self.tts_error = self.sum_error = None

    def stt(self, wav):
        self.stt_calls += 1
        if self.stt_error:
            raise self.stt_error
        return self.said.pop(0) if self.said else ""

    def tts(self, text):
        if self.tts_error:
            raise self.tts_error
        self.tts_calls.append(text)
        return b"mp3:" + text.encode()

    def summary(self, text):
        self.sum_calls.append(text)
        if self.sum_error:
            raise self.sum_error
        return "SUMMARY"


class FakeBrain:
    def __init__(self, reply="Hi."):
        self.heard, self.reply_text, self.fail = [], reply, False

    def reply(self, heard):
        self.heard.append(heard)
        if self.fail:
            raise RuntimeError("api down")
        return self.reply_text


class Room:
    """Feeds the ear 50 ms blocks: quiet or voice."""

    def __init__(self, **kw):
        self.gate, self.brain, self.timers = FakeGate(), FakeBrain(), Timers(clock=lambda: self.t / 1000)
        self.played, self.chimes, self.local, self.log, self.flushes, self.t = [], [], [], [], [0], 0
        self.ear = Ear(self.gate, self.brain, self.timers, self.played.append, self.chimes.append, self.local.append,
                       flush=lambda: self.flushes.__setitem__(0, self.flushes[0] + 1), log=self.log.append, **kw)
        self.go(QUIET, 600)                                          # the room is learned in the first 400 ms

    def go(self, level, ms):
        for _ in range(0, ms, BLOCK_MS):
            self.t += BLOCK_MS
            self.ear.feed(self.t, [level] * 800)

    def say(self, words, speaking_ms=1500):
        self.gate.said.append(words)
        self.go(VOICE, speaking_ms)
        self.go(QUIET, 1600)                                         # the pause that ends the utterance


def test_room_talk_is_heard_checked_for_the_word_and_thrown_away():
    r = Room()
    r.say("what are we having for dinner")
    r.say("the skipping rope is in the garage")
    assert r.gate.stt_calls == 2 and r.brain.heard == [] and r.played == [] and r.chimes == []
    assert r.log == []                                               # room talk is never logged


def test_hey_skippy_with_the_command_in_one_breath_is_answered_aloud():
    r = Room()
    r.say("Hey Skippy, what time is it")
    assert r.brain.heard == ["what time is it"] and r.chimes[0] == "wake" and r.gate.tts_calls == ["Hi."] and r.played == [b"mp3:Hi."]
    assert r.flushes[0] >= 1 and r.log == ["woke", "heard: what time is it", "said: Hi."]


def test_skippy_alone_wakes_it_and_the_next_utterance_is_the_command_no_word_needed():
    r = Room()
    r.say("Skippy.")
    assert r.brain.heard == [] and r.ear.mode == "listening" and r.chimes == ["wake"]
    r.say("set a timer for ten minutes")
    assert r.brain.heard == ["set a timer for ten minutes"] and r.ear.mode == "followup"


def test_after_an_answer_there_is_a_follow_up_window_without_the_word_then_it_goes_back_to_sleep():
    r = Room()
    r.say("Skippy, hello")
    r.say("and how are you")                                         # within the window: no word needed
    assert r.brain.heard == ["hello", "and how are you"]
    r.go(QUIET, 7000)                                                # the window closes
    assert r.ear.mode == "idle"
    r.say("tell me a secret")                                        # now it is room talk again
    assert r.brain.heard == ["hello", "and how are you"]


def test_a_listening_window_that_gets_nothing_closes_with_a_soft_tone():
    r = Room()
    r.say("Skippy.")
    r.go(QUIET, 9000)
    assert r.ear.mode == "idle" and r.chimes == ["wake", "done"]


def test_goodbye_and_stop_end_the_conversation_without_asking_the_brain():
    r = Room()
    r.say("Skippy, hello")
    r.say("goodbye")
    assert r.brain.heard == ["hello"] and r.ear.mode == "idle" and r.chimes[-1] == "done"
    r.say("Skippy, never mind")
    assert r.brain.heard == ["hello"]


def test_a_cough_or_a_click_is_never_sent_to_whisper():
    r = Room()
    r.gate.said.append("Skippy hello")
    r.go(VOICE, 250)
    r.go(QUIET, 1600)
    assert r.gate.stt_calls == 0 and r.brain.heard == []


def test_the_gate_being_down_or_the_brain_failing_never_stops_it_and_he_still_gets_an_answer():
    r = Room()
    r.gate.stt_error = GateError("down")
    r.say("Skippy hello")
    assert r.brain.heard == [] and any("could not hear" in line for line in r.log)
    r.gate.stt_error = None
    r.brain.fail = True
    r.say("Skippy, hello")
    assert r.gate.tts_calls == ["My brain is not answering. Typical."] and any("brain failed" in line for line in r.log)


def test_when_skippys_voice_is_out_of_budget_or_down_the_machines_own_voice_speaks():
    r = Room()
    r.gate.tts_error = BudgetSpent("spent")
    r.say("Skippy, hello")
    assert r.local == ["Hi."] and r.played == []
    r.gate.tts_error = GateError("down")
    r.say("and again")
    assert r.local == ["Hi.", "Hi."]


def test_a_long_reply_is_spoken_as_the_gates_summary_and_falls_back_to_its_opening():
    r = Room()
    r.brain.reply_text = "word " * 200
    r.say("Skippy, explain everything")
    assert r.gate.sum_calls and r.gate.tts_calls == ["SUMMARY"]
    r.gate.sum_error = GateError("no ollama")
    r.say("again please")
    assert r.gate.tts_calls[-1] == ("word " * 200)[:280]


def test_a_timer_that_comes_due_is_announced_by_itself():
    r = Room()
    r.timers.add(3, "pasta")
    r.go(QUIET, 4000)
    assert r.chimes[-1] == "timer" and r.gate.tts_calls == ["Your pasta timer is done."] and r.log[-1] == "timer done: pasta"
