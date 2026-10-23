"""The "Skippy" spotter (wakeword.py) with a stand-in model, and the single-room ear when it has one: its name wakes it, room talk never leaves."""
from ear import BLOCK_MS, Ear
from timers import Timers
from wakeword import FRAME, Spotter, default_model

from test_sat_ear import QUIET, VOICE, FakeBrain, FakeGate


class Model:
    """Scores each 80 ms frame from a script; records what it was given."""

    def __init__(self, scores):
        self.scores, self.frames, self.resets = list(scores), [], 0

    def predict(self, frame):
        self.frames.append(list(frame))
        return {"skippy": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


def test_frames_are_80_ms_of_int16_whatever_block_size_comes_in():
    m = Model([])
    s = Spotter(m)
    s.feed([0.5] * 1000)
    assert m.frames == []
    s.feed([0.5] * 2000)
    assert len(m.frames) == 2 and len(m.frames[0]) == FRAME and m.frames[0][0] == int(0.5 * 32767)


def test_a_word_needs_patience_frames_in_a_row_above_the_threshold():
    s = Spotter(Model([0.9, 0.1, 0.9, 0.9]), threshold=0.5, patience=2)
    assert [s.feed([0.0] * FRAME) for _ in range(4)] == [False, False, False, True]


def test_one_word_wakes_it_once_then_it_is_deaf_for_a_moment():
    m = Model([0.9] * 40)
    s = Spotter(m, patience=1, refractory_ms=1500)
    heard = [s.feed([0.0] * FRAME) for _ in range(40)]
    assert heard[0] is True and heard[1:19] == [False] * 18 and heard[19] is True    # 1.5 s is 18.75 frames
    assert heard.count(True) == 3 and m.resets == 3                    # each wake forgets the word


def test_the_model_lives_in_the_git_ignored_state_folder_unless_told_otherwise():
    assert default_model({}).as_posix().endswith("state/wakeword/skippy.onnx")
    assert str(default_model({"SATELLITE_WAKE_MODEL": "/x/y.onnx"})) == "/x/y.onnx"


def test_loading_a_model_that_is_not_there_says_where_to_get_one(tmp_path):
    try:
        Spotter.load(tmp_path / "skippy.onnx")
    except FileNotFoundError as e:
        assert "wakeword/README.md" in str(e)
    except ImportError:
        pass                                                         # this machine has no openwakeword: nothing to load with anyway
    else:
        raise AssertionError("loaded a model that does not exist")


# ── the single-room ear with a spotter ──
class Spot:
    name = "skippy"

    def __init__(self):
        self.armed, self.resets = False, 0

    def feed(self, block):
        hit, self.armed = self.armed, False
        return hit

    def reset(self):
        self.resets += 1


class Room:
    def __init__(self):
        self.gate, self.brain, self.spot, self.t = FakeGate(), FakeBrain(), Spot(), 0
        self.chimes, self.played = [], []
        self.ear = Ear(self.gate, self.brain, Timers(clock=lambda: self.t / 1000), self.played.append, self.chimes.append, lambda s: None,
                       log=lambda m: None, spotter=self.spot)
        self.go(QUIET, 600)

    def go(self, level, ms):
        for _ in range(0, ms, BLOCK_MS):
            self.t += BLOCK_MS
            self.ear.feed(self.t, [level] * 800)


def test_the_ear_with_a_spotter_never_sends_room_talk_to_whisper():
    r = Room()
    r.go(VOICE, 1500)
    r.go(QUIET, 1500)
    assert r.gate.stt_calls == 0 and r.chimes == []


def test_the_ear_with_a_spotter_hears_skippy_and_the_command_in_one_breath():
    r = Room()
    r.gate.said.append("Skippy, lights off.")
    r.go(VOICE, 400)
    r.spot.armed = True
    r.go(VOICE, 1100)
    r.go(QUIET, 1500)
    assert r.chimes[0] == "wake" and r.brain.heard == ["lights off."] and r.played
    assert r.spot.resets >= 1                                        # it forgot its own name in the answer


# ── wakectl.py: his own recordings, and the threshold they suggest ──
def test_a_clip_is_scored_by_its_highest_frame_and_the_threshold_chosen_hears_nine_in_ten_with_no_false_wake(tmp_path):
    import wakectl
    from voicegate import wav_bytes

    class Loud:                                                     # scores a frame by its loudness: "Skippy" clips are loud, the others quiet
        def predict(self, frame):
            return {"skippy": max(abs(x) for x in frame) / 32767}

        def reset(self):
            pass
    for kind, level, n in (("positive", 0.75, 10), ("negative", 0.35, 5)):
        (tmp_path / kind).mkdir()
        for i in range(n):
            (tmp_path / kind / f"{i}.wav").write_bytes(wav_bytes([level] * 8000))
    pos, neg = wakectl.score(Loud(), tmp_path)
    assert len(pos) == 10 and all(abs(s - 0.75) < 0.01 for s in pos) and all(abs(s - 0.35) < 0.01 for s in neg)
    lines = []
    assert wakectl.report(pos, neg, lines.append) == 0.7 and lines[-1] == "Use SATELLITE_WAKE_THRESHOLD=0.7 (in the vault .env)."
    assert wakectl.report([], neg, lines.append) is None
