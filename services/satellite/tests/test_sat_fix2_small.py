"""The second adversarial review of the house speed change (2026-11-05), the small fixes (fix2:small): what they pin, and what each failed on before.

P5  strip_stage_directions took a title between asterisks for a stage direction whenever its first word ended in -s, -ing or -ed ("Now playing *Dancing Queen* on the Enclave." was spoken and
    kept as "Now playing on the Enclave."; 17 of 43 well-known songs lost their words, "*Tomorrow's weather* will be fine." became "will be fine."), and "*Careless Whisper*" because its last
    word is a direction verb. A TITLE (two words or more that are not small joining words, all begun with a capital) is never a direction, nor is a span that starts with a possessive or a
    contraction; "*Rolls eyes*", "*adjusts imaginary dial*" and every direction of the lists are as they were.
P7  Room._cover_the_wait: a turn believed hot got ONE quip, at 4 s, and then silence; when the belief was stale (another client of Core's slot had evicted the cache) a 20 s turn heard
    nothing after that, where a cold one heard a quip at 1 s and another at 6 s. The 4 s floor stays; after the first quip the wait goes on as it always did.
J4  tests/test_sat_voicegate_timers.py failed when it was run alone ("No module named ready"): it passed in the whole suite only through the sys.modules of other tests.

(P6, the voice-gate cache, is pinned in services/voice-gate/tests/test_sat_fix2_gate.py: that service runs in its own pytest session.)

Self-contained (its own fakes), so a change to another test file's helpers cannot break it. The real-time tests run the clock at a tenth of its speed."""
import asyncio
import importlib.util
import random
import re
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import brain as brain_mod
import filler as filler_mod
import hub as hub_mod
from brain import Brain
from hub import Room
from timers import Timers

SATELLITE = Path(__file__).resolve().parents[1]
GATE_DIR = SATELLITE.parent / "voice-gate"


# ═══ P5: a title between asterisks is spoken whole ═══
strip = brain_mod.strip_stage_directions

# 43 well-known songs, as a model writes them (the first seven are the ones the review named)
TITLES = [
    "Dancing Queen", "Smells Like Teen Spirit", "Rolling in the Deep", "Chasing Cars", "Careless Whisper", "Jessie's Girl", "Tomorrow's Weather",
    "Billie Jean", "Bohemian Rhapsody", "Hotel California", "Stairway to Heaven", "Wonderwall", "Sweet Child o' Mine", "Hey Jude", "Let It Be",
    "Purple Rain", "Take On Me", "Livin' on a Prayer", "Every Breath You Take", "Sweet Dreams", "Blinding Lights", "Walking on Sunshine",
    "Dancing in the Dark", "Born in the U.S.A.", "Under Pressure", "Somebody to Love", "Another One Bites the Dust", "Killing in the Name",
    "Counting Stars", "Shape of You", "Uptown Funk", "Thinking Out Loud", "Singing in the Rain", "It's Raining Men", "Stayin' Alive",
    "Girls Just Want to Have Fun", "Thunderstruck", "Sweet Home Alabama", "Eye of the Tiger", "Waterloo", "Hotel Yorba", "Pumped Up Kicks", "Dreams",
]
assert len(TITLES) == 43
# Titles the lists of direction words used to take as well (a verb first or a noun last), and other phrases in the same case
LIST_WORDS = ["Beat It", "Smile Like You Mean It", "Cough Syrup", "Rock and Roll", "Drop the Beat", "Whisper to a Scream", "Laugh Now Cry Later", "Hum Along", "Rolling Stones Gather No Moss"]
PHRASES = ["Tomorrow's weather", "Today's forecast", "Wednesday's meeting", "Monday's alarm", "Living Room Lights", "Dining Room Lights", "Working Hours", "Opening Hours", "Closing Time",
           "Waiting Room", "Boarding Pass", "It's raining", "it's raining", "today's forecast", "tomorrow's weather"]


def test_fix2_the_review_s_own_examples_are_spoken_whole():
    assert strip("Now playing *Dancing Queen* on the Enclave.") == "Now playing Dancing Queen on the Enclave."
    assert strip("*Tomorrow's weather* will be fine.") == "Tomorrow's weather will be fine."


@pytest.mark.parametrize("star", ["*", "**"])
def test_fix2_43_well_known_songs_between_asterisks_lose_not_a_word(star):
    lost = [t for t in TITLES if strip(f"Now playing {star}{t}{star} on the Enclave.") != f"Now playing {t} on the Enclave."]
    assert lost == []                                                                       # (17 of the 43 on the code before)


@pytest.mark.parametrize("said, kept", [
    ("*Dancing Queen* is by ABBA.", "Dancing Queen is by ABBA."), ("It is *Dancing Queen*.", "It is Dancing Queen."), ("*Rolling in the Deep*", "Rolling in the Deep"),
    ("Try *Smells Like Teen Spirit*, *Chasing Cars* and *Careless Whisper*.", "Try Smells Like Teen Spirit, Chasing Cars and Careless Whisper."),
    ("*sighs* Fine, *Dancing Queen* it is.", "Fine, Dancing Queen it is."), ("Now playing *Dancing Queen* [laughs] by ABBA.", "Now playing Dancing Queen by ABBA."),
])
def test_fix2_a_title_wherever_it_stands_in_the_reply(said, kept):
    assert strip(said) == kept


@pytest.mark.parametrize("phrase", LIST_WORDS + PHRASES)
def test_fix2_a_title_with_a_direction_word_at_its_edge_and_a_possessive_are_spoken_too(phrase):
    assert strip(f"Now *{phrase}* on the Enclave.") == f"Now {phrase} on the Enclave."


# Directions as the model writes them: the house log of 2026-11-01..05 (sighs, sigh, waves goodbye, raises an eyebrow, adjusts imaginary dial), the persona's own examples (sigh, eye roll) and the usual
# ones; all lower case, as every one of the 84 in the log. (stretches, mumbles, grumbles, flickers, salutes and "does a little dance" are not here: one unknown verb or "does" were never taken.)
DIRECTIONS = [
    "sighs", "sigh", "waves goodbye", "raises an eyebrow", "adjusts imaginary dial", "eye roll", "rolls eyes", "clears throat", "dramatic pause", "pauses",
    "chuckles", "laughs", "grins", "smirks", "shrugs", "yawns", "hums", "taps the can", "leans in", "checks watch",
    "glances at the ceiling", "waves a hand", "scratches head", "tilts head", "crosses arms", "folds arms", "rubs temples", "snaps fingers", "clicks fingers",
    "drums fingers", "shakes head", "nods slowly", "whispers", "mutters under breath", "groans", "gasps", "snorts",
    "scoffs", "sniffs", "winks", "smiles", "sighs heavily", "sighing deeply", "rolling eyes", "clearing throat", "adjusting imaginary dial",
    "sighed", "laughed", "long pause", "pauses dramatically", "taps the dial", "spins around", "bows deeply", "beats chest",
]
SENTENCE_CASE = ["Sighs", "Sighs heavily", "Rolls eyes", "Adjusts imaginary dial", "Clears throat", "Dramatic pause", "Taps the can", "Leans in", "Waves goodbye", "Raises an eyebrow"]
BRACKETED = ["sighs", "laughs", "chuckles", "whispers", "clicks fingers", "taps the can", "pause", "cough", "gasp", "shrugs", "sighs deeply", "rolls eyes"]


@pytest.mark.parametrize("star", ["*", "**"])
def test_fix2_every_stage_direction_of_the_log_and_the_persona_is_still_deleted(star):
    kept = [d for d in DIRECTIONS if strip(f"It is nine. {star}{d}{star} Fine.") != "It is nine. Fine."]
    assert kept == []                                                                       # 54 of 54 (as before)


def test_fix2_a_direction_that_starts_a_sentence_with_a_capital_is_still_deleted_it_is_no_title():
    kept = [d for d in SENTENCE_CASE if strip(f"*{d}* Fine.") != "Fine."]
    assert kept == []                                                                       # ("Leans in", "Rolls eyes": the words after the first are small or lower case)
    assert strip("*Sighs.* Fine.") == "Fine." and strip("*Clears throat.* Ahem.") == "Ahem."


def test_fix2_a_bracketed_direction_is_deleted_as_it_was():
    assert [d for d in BRACKETED if strip(f"It is nine. [{d}] Fine.") != "It is nine. Fine."] == []


def test_fix2_a_possessive_or_a_contraction_is_no_action_in_lower_case_either():
    assert strip("The *today's forecast* is rain.") == "The today's forecast is rain."
    assert strip("Well, *it's raining* again.") == "Well, it's raining again."
    assert strip("*tomorrow's weather* is fine.") == "tomorrow's weather is fine."
    assert strip("*let's go* then.") == "let's go then."


def test_fix2_a_negation_stays_a_negation_whatever_it_is_wrapped_in():
    for said, kept in (("Do *not* open the garage door.", "Do not open the garage door."), ("*Never Stop Believing* is a song.", "Never Stop Believing is a song."),
                       ("It is *not Dancing Queen*.", "It is not Dancing Queen."), ("*No Woman No Cry* plays.", "No Woman No Cry plays."), ("Turn *off* the lights.", "Turn off the lights.")):
        assert strip(said) == kept


# the random property: whatever mix of plain words, emphasised words, titles, directions and products a reply is made of, what is spoken is exactly what is not a direction
TOK = re.compile(r"[a-z0-9']+")
PLAIN = "turn open close the garage door lights kitchen bedroom on you can please do it now Captain playing opening closing turned dimmed starts needs Thanks going used".split()
NEGATIONS = ["not", "never", "no", "off", "don't", "cannot", "nothing", "isn't"]
SINGLE = "kitchen bedroom garage door really very good all every this oh actually next see show".split()                  # emphasised words that are on no list
MULTI = ["very good", "really cool", "kitchen lights", "the garage", "every single", "actually magnificent", "right now", "my name", "that one", "bedroom light"]
FIRST = ["Dancing", "Rolling", "Chasing", "Counting", "Walking", "Smells", "Girls", "Thinking", "Careless", "Singing", "Killing", "Blinding", "Pumped", "Tomorrow's", "Jessie's", "Today's",
         "Closing", "Opening", "Waiting", "Burning", "Dreams", "Thanks", "Lights", "Doors", "Looks", "Wakes", "Sweet", "Hotel", "Purple"]
NEXT = ["Queen", "Cars", "Stars", "Whisper", "Lights", "Rain", "Sunshine", "Spirit", "Kicks", "Girl", "Weather", "Heart", "Room", "Time", "Smile", "Pause", "Beat", "Roll", "Loud", "Love", "Fire"]
JOINERS = ["in the", "of the", "on", "to", "and", "of", "at the", "for", "with"]
VERBS3 = ["adjusts", "rolls", "clears", "taps", "leans", "raises", "waves", "checks", "glances", "rubs", "scratches", "tilts", "crosses", "shakes", "folds", "snaps", "drums"]
OBJECTS = ["eyes", "throat", "the can", "in", "an eyebrow", "goodbye", "imaginary dial", "head", "arms", "fingers", "temples", "at the ceiling", "slowly"]


def random_title(rnd):
    kind = rnd.random()
    if kind < 0.4:
        return rnd.choice(TITLES)
    if kind < 0.5:
        return rnd.choice(PHRASES[:9] + LIST_WORDS)
    words = [rnd.choice(FIRST)]
    for _ in range(rnd.randint(1, 3)):
        if rnd.random() < 0.35:
            words.append(rnd.choice(JOINERS))
        words.append(rnd.choice(NEXT))
    return " ".join(words)


def random_reply(rnd):
    """-> (the reply as the model wrote it, the words that must be spoken, the products that must stand as they are)"""
    parts, kept, products = [], [], []
    for _ in range(rnd.randint(3, 10)):
        k, star = rnd.random(), rnd.choice(["*", "*", "**"])
        if k < 0.20:
            w = rnd.choice(PLAIN + NEGATIONS)
            parts.append(w)
            kept.append(w)
        elif k < 0.30:
            n = rnd.choice(NEGATIONS)
            span = rnd.choice([n, f"{n} {rnd.choice(PLAIN)}", f"{rnd.choice(PLAIN)} {n}", f"{rnd.choice(['is', 'do', 'does', 'was'])} {n}"])
            parts.append(f"{star}{span}{star}")
            kept.append(span)
        elif k < 0.40:
            w = rnd.choice(SINGLE)
            parts.append(f"{star}{w}{star}")
            kept.append(w)
        elif k < 0.45:
            w = rnd.choice(MULTI)
            parts.append(f"{star}{w}{star}")
            kept.append(w)
        elif k < 0.65:
            t = random_title(rnd)
            parts.append(f"{star}{t}{star}")
            kept.append(t)
        elif k < 0.80:
            d = rnd.choice(DIRECTIONS + [f"{rnd.choice(VERBS3)} {rnd.choice(OBJECTS)}"])
            parts.append(f"{star}{d}{star}")
        elif k < 0.85:
            parts.append(f"{star}{rnd.choice(SENTENCE_CASE)}{star}")
        elif k < 0.93:
            parts.append(f"[{rnd.choice(BRACKETED)}]")
        else:
            p = rnd.choice(["2*3*4", "12*7*8", "a*b*c", "x*y*z"])
            parts.append(p)
            kept.append(p)
            products.append(p)
        if rnd.random() < 0.2:
            parts[-1] += rnd.choice([",", ".", "!", "?"])
    return " ".join(parts) + rnd.choice([".", "!", "?", ""]), TOK.findall(" ".join(kept).lower()), products


def test_fix2_property_20000_replies_speak_exactly_what_is_no_direction():
    rnd = random.Random(20261105)
    wrong = []
    for _ in range(20000):
        said, expected, products = random_reply(rnd)
        spoken = strip(said)
        bad = []
        if TOK.findall(spoken.lower()) != expected:
            bad.append("words")                                                             # a word lost (a title, a negation, an emphasised word) or a direction left in
        if spoken.count("*") != sum(p.count("*") for p in products) or "[" in spoken or "]" in spoken:
            bad.append("marks left")
        if "  " in spoken or spoken.startswith(" ") or spoken.endswith(" "):
            bad.append("spaces")
        if any(p not in spoken for p in products):
            bad.append("product")
        if bad:
            wrong.append((said, spoken, bad))
    assert not wrong, (len(wrong), wrong[:3])                                               # (35,000 of 60,000 on the code before: every reply with a title in it)


def test_fix2_the_brain_speaks_and_remembers_a_title_whole():
    class Model:
        def __init__(self, *answers):
            self.answers, self.calls, self.messages = list(answers), [], self

        def create(self, **kw):
            self.calls.append(kw)
            return NS(content=[NS(type="text", text=self.answers.pop(0))], stop_reason=None)
    model = Model("The film is *Dancing with Wolves*. *sighs* Obviously, monkey.", "*Tomorrow's weather* will be fine. [laughs] Probably.")
    clock = [1_793_000_000.0]
    b = Brain(model, "m", Timers(clock=lambda: clock[0]), persona="PERSONA", clock=lambda: clock[0], now=lambda: "Thursday, November 05, 2026, 14:45")
    b.sleep = lambda s: None
    assert b.reply("which film has the kevin costner and the buffalo") == "The film is Dancing with Wolves. Obviously, monkey."
    assert b.history[-1]["content"] == "The film is Dancing with Wolves. Obviously, monkey."
    assert b.reply("and the weather") == "Tomorrow's weather will be fine. Probably."


# ═══ P7: a turn believed hot that runs long still gets its second quip ═══
COLD = "what is the capital of france"                          # a thinking quip, 1.0 s
FAST = "turn off the bedroom light"                              # a household command: 2.2 times the delay
SCALE = 0.1                                                      # one second of the review's clock is a tenth of a real second here


class Link:
    """What Room asks of a box, recorded; `announce` (a quip) with the real seconds since the turn began."""
    hub_follows_up = True

    def __init__(self):
        self.t0, self.quips, self.announced, self.spoken = time.monotonic(), [], [], []

    async def woke(self, by): pass
    async def voice_started(self): pass
    async def voice_stopped(self): pass
    async def heard(self, text): pass
    async def released(self): pass
    async def show(self, text): pass
    async def idle_face(self): pass
    def session(self, on): pass

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(said)

    async def announce(self, said, voice):
        self.announced.append(said)
        self.quips.append(time.monotonic() - self.t0)


class Gate:
    def tts(self, text):
        return b"mp3:" + text.encode()

    def stt(self, wav):
        return ""

    def summary(self, text):
        return "SUMMARY"


class SlowBrain:
    """A brain whose answer takes `seconds` of real time (in the thread the hub gives it)."""

    def __init__(self, seconds):
        self.seconds = seconds

    def reply(self, heard):
        time.sleep(self.seconds)
        return "Done, monkey."


class Bank(filler_mod.FillerBank):
    """The real bank's idea of which kind of quip suits what was asked (kind_for), with nothing on disk; pick() remembers what it was asked for and always has a quip."""

    def __init__(self):
        super().__init__(Path(__file__).parent / "no-such-folder", {k: [{"file": "x.mp3", "text": k}] for k in ("thinking", "home", "long")})
        self.picked = []

    def pick(self, kind, command=""):
        self.picked.append(kind)
        return (f"quip {kind}", b"quip-mp3")


class Scaled:
    """hub.py's `asyncio` with wait() run at SCALE of the timeout it is given: the real clock, small. Everything else is asyncio's own."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def wait(self, fs, timeout=None, **kw):
        return await asyncio.wait(fs, timeout=None if timeout is None else timeout * SCALE, **kw)


def turn(monkeypatch, hot, model_s, command=COLD, filler_after_s=1.0):
    """A real Room.handle with a stub brain whose answer takes model_s (of the review's seconds) and the keeper's belief `hot`: -> (the real seconds of each quip, the quips picked, the real seconds in all)."""
    async def go():
        link, bank = Link(), Bank()
        room = Room("office", link, Gate(), SlowBrain(model_s * SCALE), Timers(clock=lambda: 0), log=[].append, filler=bank, filler_after_s=filler_after_s)
        monkeypatch.setattr(Room, "_prompt_is_hot", lambda self: hot)
        monkeypatch.setattr(hub_mod, "asyncio", Scaled())
        link.t0 = time.monotonic()
        await room.handle(command, typed=True)
        assert link.spoken == ["Done, monkey."]
        return list(link.quips), list(bank.picked), time.monotonic() - link.t0
    return asyncio.run(go())


def test_fix2_a_hot_turn_that_is_over_well_before_the_floor_never_reaches_a_quip(monkeypatch):
    quips, picked, took = turn(monkeypatch, hot=True, model_s=1.5)                         # (a hot model turn takes 1.5-4.4 s)
    assert quips == [] and picked == []


def test_fix2_a_hot_turn_a_little_past_the_floor_gets_its_one_quip_at_four_and_no_second(monkeypatch):
    quips, picked, took = turn(monkeypatch, hot=True, model_s=6.5)                         # (answered 2.5 s after the first quip began: long before a second is due)
    assert picked == ["thinking"] and len(quips) == 1
    assert quips[0] >= 4 * SCALE * 0.75 and took >= 6.5 * SCALE * 0.9                       # the 4 s floor is kept: not the 1 s of a cold prompt


def test_fix2_a_hot_turn_that_runs_on_gets_the_second_quip_like_a_cold_one(monkeypatch):
    """The review: a believed-hot 20 s turn heard one quip at 4 s and then nothing; a cold one, quips at 1 s and 6 s."""
    quips, picked, took = turn(monkeypatch, hot=True, model_s=12)
    assert picked == ["thinking", "long"] and len(quips) == 2                              # (one quip, "thinking", before)
    assert quips[0] >= 4 * SCALE * 0.75                                                      # still not before the floor
    assert 5 * SCALE * 0.8 <= quips[1] - quips[0] <= 5 * SCALE * 3                         # and the second 5 s after the first, as on a cold prompt


def test_fix2_a_cold_turn_is_as_it_was_a_quip_at_one_second_and_another_five_later(monkeypatch):
    quips, picked, took = turn(monkeypatch, hot=False, model_s=12)
    assert picked == ["thinking", "long"] and len(quips) == 2
    assert quips[0] < 4 * SCALE * 0.75                                                       # at about 1 s, long before a hot turn's
    assert 5 * SCALE * 0.8 <= quips[1] - quips[0] <= 5 * SCALE * 3


def test_fix2_a_household_command_believed_hot_keeps_its_longer_wait_and_gets_the_second_quip(monkeypatch):
    quips, picked, took = turn(monkeypatch, hot=True, model_s=17, command=FAST)
    assert picked == ["home", "long"] and len(quips) == 2                                   # 8.8 s (4 x 2.2) for the first, 5 s after it the second
    assert quips[0] >= 8.8 * SCALE * 0.75


def test_fix2_the_scripted_waits_a_hot_turn_that_drags_waits_four_then_five_then_five(monkeypatch):
    """The same schedule without the clock: asyncio.wait is scripted and says 'not done' at every look, so the timeouts are the numbers."""
    class Waits:
        def __init__(self):
            self.timeouts = []

        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def wait(self, fs, timeout=None, **kw):
            self.timeouts.append(timeout)
            return set(), set(fs)

    async def go(hot):
        bank, link = Bank(), Link()
        room = Room("office", link, Gate(), SlowBrain(0), Timers(clock=lambda: 0), log=[].append, filler=bank, filler_after_s=1.0)
        monkeypatch.setattr(Room, "_prompt_is_hot", lambda self: hot)
        waits = Waits()
        monkeypatch.setattr(hub_mod, "asyncio", waits)
        work = asyncio.ensure_future(asyncio.sleep(0))
        await room._cover_the_wait(work, COLD)
        work.cancel()
        return waits.timeouts, bank.picked
    assert asyncio.run(go(True)) == ([4.0, 5.0, 5.0], ["thinking", "long"])
    assert asyncio.run(go(False)) == ([1.0, 5.0, 5.0], ["thinking", "long"])


# ═══ J4: the voice-gate timers test passes alone ═══
def timers_test_module():
    path = Path(__file__).with_name("test_sat_voicegate_timers.py")
    spec = importlib.util.spec_from_file_location("sat_voicegate_timers_for_fix2", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fix2_the_voice_gate_test_loads_the_gate_with_its_own_ready_whatever_other_tests_left_behind(monkeypatch):
    mod = timers_test_module()
    path_before = list(sys.path)
    other = types.ModuleType("ready")                           # another gate's `ready`, as an earlier test can leave it in sys.modules
    monkeypatch.setitem(sys.modules, "ready", other)
    gate = mod.real_gate()
    assert gate.ready_mod is not other and Path(gate.ready_mod.__file__).parent == GATE_DIR
    assert sys.modules["ready"] is other and sys.path == path_before                           # the other one is put back, and the path is as it was
    monkeypatch.delitem(sys.modules, "ready")
    mod.real_gate()                                             # and when nothing was left behind (the test run alone)
    assert "ready" not in sys.modules and sys.path == path_before


def test_fix2_the_voice_gate_test_file_passes_when_it_is_run_alone():
    path = Path(__file__).with_name("test_sat_voicegate_timers.py")
    done = subprocess.run([sys.executable, "-m", "pytest", str(path), "-q", "-p", "no:cacheprovider"], cwd=str(SATELLITE), capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-2000:]            # (2 of its 5 failed alone before: "No module named ready")
