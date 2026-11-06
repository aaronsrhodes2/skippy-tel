"""The house voice, snappy like an Echo: what the adversarial review of 2026-11-05 found in the hub's half, fixed (the hub, the voice gate client, the quips).

Every fix has a test that failed on the hub as it was when the review was written (the P numbers are the review's); the controls beside them (a cold prompt keeps its delays, a brain that
records nothing is judged as before, a start-up warm waits for a turn) passed then and pin what must not change:

* P5   the warm when the name is heard raced the turn when the keeper's belief that the prompt was hot was stale (a cold 10.5k-token prefill: the turn alone 20.7 s, with the warm
       beside it 40 s) and gained +0.06 s when it was hot (the real Brain, 15 turns each way): taken out. The start-up warm and the 240 s loop stay.
* P6   the filler quip is tuned for 15 s model turns: with Skippy Core's prompt hot the model answers in 1.5-4.4 s (median 2.1 s) and a quip at 1.0 s (2.2 s for a household
       command) sat in front of nearly every answer for about 2 s (the 207 recorded quips last 0.5-7.8 s, median 2.1 s). A hot prompt waits max(filler_after_s, 4 s) for its first
       quip; a cold or unknown one keeps its delays. (The second quip of a hot turn that drags on: it was dropped, then put back by the second review, a stale belief left a 20 s
       turn with one quip and silence.)
* P8   the log called a model-worded "Done, Captain." a template reply: the brain's own record of the reply (its "template" key, when it recorded a model_s list) decides,
       the hub's guess from the phrase bank is only for a brain that records nothing.
* P11  the start-up warm's retries ignored the 60 s "Core is away" pause and another room's warm in flight (a second prefill of the same prompt beside the first).
* P13  (the survivors of the mutation run in this region) a start-up warm waits while a turn is being thought.
* the hub's Gate.tts asks the voice gate for a whole reply ("whole": true), so the studio renders a short reply in one F5 run (0.35-1.1 s saved per 2-3 sentence reply).

Self-contained (its own fakes), so a change to another test file's helpers cannot break it. The brain's side (Brain.warm, Brain.timing) is another file's: it is a fake here."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import filler as filler_mod
import hub as hub_mod
import voicegate
from hub import Room
from timers import Timers

GATE_PY = Path(__file__).resolve().parents[2] / "voice-gate" / "gate.py"


# ── fakes ──
class Stop(BaseException):
    """Raised by the test clock when a never-ending loop has run as long as the test wants (not an Exception: nothing under test may swallow it)."""


class Clock:
    """The keeper's clock and sleep, moved by the test: a sleep takes no real time, moves `t`, runs what the test scheduled for that moment, and stops the loop at `until`."""

    def __init__(self, t=1000.0, until=None):
        self.t, self.until, self.slept, self.due = t, until, [], []

    def __call__(self):
        return self.t

    def at(self, t, fn):
        self.due.append((t, fn))

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds
        for item in sorted([d for d in self.due if d[0] <= self.t], key=lambda d: d[0]):
            self.due.remove(item)
            item[1]()
        if self.until is not None and self.t > self.until:
            raise Stop()
        await asyncio.sleep(0)


class Link:
    """What Room asks of a box, recorded."""
    hub_follows_up = True

    def __init__(self):
        self.room, self.events, self.spoken, self.voices, self.shown, self.announced = None, [], [], [], [], []

    async def woke(self, by): self.events.append(("woke", by))
    async def voice_started(self): self.events.append(("voice_started",))
    async def voice_stopped(self): self.events.append(("voice_stopped",))
    async def heard(self, text): self.events.append(("heard", text))
    async def released(self): self.events.append(("released",))
    async def show(self, text): self.shown.append(text)
    async def idle_face(self): pass
    def session(self, on): self.events.append(("session", on))

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(said)
        self.voices.append(voice)

    async def announce(self, said, voice):
        self.announced.append(said)


class Gate:
    """The voice gate: tts() counts what it is asked."""

    def __init__(self):
        self.tts_calls = []

    def stt(self, wav):
        return ""

    def tts(self, text):
        self.tts_calls.append(text)
        return b"mp3:" + text.encode()

    def summary(self, text):
        return "SUMMARY"


class Brain:
    """A brain as the hub sees it: reply(), warm() (seconds, or None when Core did not answer). `timing` is whatever the test says the brain recorded (or nothing)."""

    def __init__(self, results=None, say="Done, monkey.", clock=None, timing="none", phrases=()):
        self.results = list(results) if results else [0.6]
        self.say, self.clock, self.phrases = say, clock, list(phrases)
        self.warm_at, self.warm_calls, self.heard = [], 0, []
        if timing != "none":
            self.timing = timing

    def warm(self):
        self.warm_calls += 1
        self.warm_at.append(self.clock.t if self.clock is not None else None)
        out = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(out, BaseException):
            raise out
        return out

    def reply(self, heard):
        self.heard.append(heard)
        if isinstance(self.say, BaseException):
            raise self.say
        return self.say

    def fixed_phrases(self):
        return list(self.phrases)


class Bank(filler_mod.FillerBank):
    """The real bank's idea of which kind of quip suits what was asked (kind_for), with nothing on disk; pick() remembers what it was asked for and always has a quip."""

    def __init__(self):
        super().__init__(Path(__file__).parent / "no-such-folder", {k: [{"file": "x.mp3", "text": k}] for k in ("thinking", "home", "long")})
        self.picked = []

    def pick(self, kind, command=""):
        self.picked.append(kind)
        return (f"quip {kind}", b"quip-mp3")


class Waits:
    """hub.py's `asyncio` with wait() scripted: it notes the timeout it was asked for and answers at once ("done" from the `done_at`-th wait on, never when None), so a delay of
    seconds is a number the test reads and not time it sits through. Everything else is asyncio's own."""

    def __init__(self, done_at=None):
        self.timeouts, self.done_at = [], done_at

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def wait(self, fs, timeout=None, **kw):
        self.timeouts.append(timeout)
        fs = set(fs)
        return (fs, set()) if self.done_at is not None and len(self.timeouts) >= self.done_at else (set(), fs)


def new_keeper(clock=None, **kw):
    clock = clock or Clock()
    log = []
    kw.setdefault("brain_module", NS())
    kw.setdefault("own_phrases", ())
    keeper = hub_mod.SpeedKeeper(Gate(), log=log.append, clock=clock, sleep=clock.sleep, **kw)
    return keeper, clock, log


def make_room(brain=None, keeper=None, **kw):
    link, log = Link(), []
    room = Room("office", link, Gate(), brain if brain is not None else Brain(), Timers(clock=lambda: 0), log=log.append, **kw)
    if keeper is not None:
        room.speed, room.clips = keeper, keeper.clips
    return room, link, log


def hot(keeper, brain, ago=60.0):
    """As if a warm of this brain worked `ago` seconds ago: the prompt is believed hot."""
    keeper.hot_at[id(brain)] = keeper.touched[id(brain)] = keeper.clock() - ago


# ═══ P5: the name warms nothing ═══
@pytest.mark.parametrize("state", ["hot", "cold"])
@pytest.mark.parametrize("how", ["name", "tap", "box woke"])
def test_fixhub_p5_the_wake_starts_no_warm_whatever_the_keeper_believes_about_the_prompt(how, state):
    """Measured on the real Core (2026-11-05): a warm at the name gained +0.06 s with the prompt hot (the real Brain: 15 turns each way, a median 2.23 s without it and 2.16 s with it;
    builder 1's probe of the old layout: 1.30 s against 1.10 s), and cost a turn 20 s -> 40 s when the prefix had been evicted since the keeper last saw it hot (the belief is 300 s;
    other clients of Core's chat slot evict it): llama.cpp does not share a prefill that is in flight. The start-up warm and the 240 s loop stay; the wake asks nothing of Core."""
    async def go():
        keeper, clock, log = new_keeper()
        brain = Brain()
        room, link, _ = make_room(brain=brain, keeper=keeper)
        if state == "hot":
            hot(keeper, brain, ago=100)                          # (long past anything a recent turn would postpone)
        if how == "name":
            room._woke("skippy")
        elif how == "tap":
            room.tap()
        else:
            await room.begin(False)
        await asyncio.sleep(0.05)                                # (a warm would be running in a thread by now)
        asked = (brain.warm_calls, bool(keeper.inflight), keeper.core_state(brain))
        await room.handle("what time is it", typed=True)         # and the sentence that follows is answered as ever
        return asked, link.spoken, [e for e in link.events if e[0] == "woke"]
    asked, spoken, woke = asyncio.run(go())
    assert asked == (0, False, state)
    assert spoken == ["Done, monkey."] and bool(woke) is (how != "box woke")          # (the wake itself still happens: the box lights up, the window opens)


# ═══ P6: the quip waits for a prompt that is hot ═══
COLD = "what is the capital of france"                           # a thinking quip, 1.0 s
FAST = "turn off the bedroom light"                              # a household command: 2.2 times the delay


async def cover(monkeypatch, command, state, done_at=None):
    """Room._cover_the_wait with the prompt `state`: -> (the timeouts it waited, the quips it picked, what the box announced)."""
    keeper, _, _ = new_keeper()
    brain = Brain()
    bank = Bank()
    room, link, _ = make_room(brain=brain, keeper=keeper if state != "no keeper" else None, filler=bank, filler_after_s=1.0)
    if state == "hot":
        hot(keeper, brain)
    elif state == "warming":
        keeper.inflight.add(id(brain))
    elif state == "broken keeper":
        room.speed = NS(core_state=lambda b: 1 / 0)
    waits = Waits(done_at)
    monkeypatch.setattr(hub_mod, "asyncio", waits)
    work = asyncio.ensure_future(asyncio.sleep(0))
    await room._cover_the_wait(work, command)
    work.cancel()
    return waits.timeouts, bank.picked, link.announced


@pytest.mark.parametrize("command,delay", [(COLD, 4.0), (FAST, 8.8)])
def test_fixhub_p6_a_hot_prompt_is_given_four_seconds_before_a_quip_and_the_household_factor_still_applies(monkeypatch, command, delay):
    timeouts, picked, announced = asyncio.run(cover(monkeypatch, command, "hot"))
    assert timeouts[0] == pytest.approx(delay)                   # (a hot model turn takes 1.5-4.4 s: it is over before a quip would be wanted)
    assert picked[:1] == [("home" if command == FAST else "thinking")] and announced[:1] == [("quip home" if command == FAST else "quip thinking")]     # (the FIRST quip: with the wait never over, the second follows, see below)


def test_fixhub_p6_a_hot_turn_that_drags_gets_the_second_quip_as_a_cold_one_does_only_the_first_waits_longer():
    """Changed by the second review (2026-11-05, fix2:small): this test said "a hot turn gets one quip at most", [4.0] and nothing after. The keeper's belief that the prompt is hot goes stale (another
    client of Core's chat slot evicts the cache after the keeper last saw it hot): that turn takes 20 s and heard one quip at 4 s, then silence, where a cold one heard a quip at 1 s and another at
    6 s. The floor of 4 s stays for the first quip; after it the wait goes on as ever. (tests/test_sat_fix2_small.py runs it in real time at a tenth of the speed.)"""
    async def go(state):
        with pytest.MonkeyPatch.context() as mp:
            return await cover(mp, COLD, state)
    hot_timeouts, hot_picked, _ = asyncio.run(go("hot"))
    cold_timeouts, cold_picked, _ = asyncio.run(go("cold"))
    assert hot_picked == ["thinking", "long"] and hot_timeouts == [4.0, 5.0, 5.0]        # the first quip at 4 s, the second 5 s after it
    assert cold_picked == ["thinking", "long"] and cold_timeouts == [1.0, 5.0, 5.0]      # a cold or unknown one: as it was


@pytest.mark.parametrize("state", ["cold", "warming", "no keeper", "broken keeper"])
@pytest.mark.parametrize("command,delay", [(COLD, 1.0), (FAST, 2.2)])
def test_fixhub_p6_a_cold_or_unknown_prompt_keeps_the_delays_it_had(monkeypatch, state, command, delay):
    timeouts, picked, _ = asyncio.run(cover(monkeypatch, command, state))
    assert timeouts[0] == pytest.approx(delay) and len(picked) == 2


def test_fixhub_p6_an_operator_who_set_a_longer_delay_keeps_it_when_hot(monkeypatch):
    async def go():
        keeper, _, _ = new_keeper()
        brain, bank = Brain(), Bank()
        room, link, _ = make_room(brain=brain, keeper=keeper, filler=bank, filler_after_s=6.0)       # (SATELLITE_FILLER_S=6)
        hot(keeper, brain)
        waits = Waits()
        monkeypatch.setattr(hub_mod, "asyncio", waits)
        work = asyncio.ensure_future(asyncio.sleep(0))
        await room._cover_the_wait(work, COLD)
        work.cancel()
        return waits.timeouts
    assert asyncio.run(go())[0] == 6.0                           # the floor never makes a wait shorter than it was


def test_fixhub_p6_through_a_real_turn_the_prompts_state_picks_the_delay(monkeypatch):
    async def go(state):
        keeper, _, _ = new_keeper()
        brain = Brain()
        room, link, _ = make_room(brain=brain, keeper=keeper, filler=Bank(), filler_after_s=1.0)
        if state == "hot":
            hot(keeper, brain)
        waits = Waits(done_at=1)                                  # the answer is there at the first look: no quip, just the number asked for
        monkeypatch.setattr(hub_mod, "asyncio", waits)
        await room.handle("what time is it", typed=True)
        return waits.timeouts[0], link.announced, link.spoken
    cold = asyncio.run(go("cold"))
    hot_ = asyncio.run(go("hot"))
    assert cold[0] == pytest.approx(2.2) and hot_[0] == pytest.approx(8.8)
    assert cold[1] == hot_[1] == [] and cold[2] == hot_[2] == ["Done, monkey."]


# ═══ P8: the log tells what the brain says it did ═══
def logged(tmp_path, brain, command="turn it up", rendered=("Done, Captain.",), hub_phrases=()):
    """Run one typed turn in a room with a keeper whose clips already hold `rendered` (as the pass after the first warm leaves them): -> (the timing line, the command's timing record)."""
    import housemem
    tmp_path = Path(tmp_path) / f"house-log-{len(list(Path(tmp_path).iterdir()))}"          # (a house log of its own for every turn this test runs)
    tmp_path.mkdir()

    async def go():
        keeper, clock, _ = new_keeper(own_phrases=hub_phrases)
        link, log = Link(), []
        room = Room("office", link, Gate(), brain, Timers(clock=lambda: 0), log=log.append, hlog=housemem.HouseLog(tmp_path))
        room.speed, room.clips = keeper, keeper.clips
        hub = NS(rooms={"office": room})
        keeper.phrases(hub)
        for text in rendered:
            keeper.clips.put(text, ("mp3", b"clip"))
        await room.handle(command, typed=True)
        return log
    log = asyncio.run(go())
    path = next((tmp_path / "log").glob("*.jsonl"))
    record = next(r for r in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()) if r["kind"] == "command")
    return next(x for x in log if "(timing:" in x), record["timing"]


def test_fixhub_p8_a_done_the_model_worded_is_not_logged_as_a_template_reply(tmp_path):
    """The case the Captain wants to see in the log: the model said "Done, Captain." (its record: one model call, no template), which is also a phrase of the closed bank."""
    brain = Brain(say="Done, Captain.", timing={"route": None, "model_s": [1.7], "tools_s": 0.0, "total_s": 1.7}, phrases=["Done, Captain."])
    line, numbers = logged(tmp_path, brain)
    assert "template" not in line and "template" not in numbers, line
    assert numbers["model"] == [1.7] and numbers["route"] == "none"                       # what it did is there: a model call, no route
    assert numbers.get("cached") is True and "voice 0.0s cached" in line                  # (the clip was in memory: that much is true, and it is not called a template)


def test_fixhub_p8_a_template_the_brain_says_it_made_is_logged_as_one(tmp_path):
    brain = Brain(say="Done, Captain.", timing={"route": "home_control", "model_s": [], "tools_s": 0.1, "total_s": 0.1, "template": True}, phrases=["Done, Captain."])
    line, numbers = logged(tmp_path, brain)
    assert "template" in line and numbers["template"] is True and "model" not in numbers


def test_fixhub_p8_a_brain_that_records_nothing_is_judged_by_the_phrase_bank_as_before(tmp_path):
    brain = Brain(say="Done, Captain.", phrases=["Done, Captain."])                      # no `timing` at all
    line, numbers = logged(tmp_path, brain)
    assert "template" in line and numbers["template"] is True
    other = Brain(say="Something the bank does not have.", phrases=["Done, Captain."])
    line, numbers = logged(tmp_path, other)
    assert "template" not in line and "template" not in numbers


def test_fixhub_p8_a_failed_turn_is_judged_by_the_phrase_bank_not_by_the_last_replys_record(tmp_path):
    """The hub's own "My brain is not answering. Typical." is one of its fixed phrases; the brain's record of the reply BEFORE (one model call, no template) is not this turn's."""
    brain = Brain(say=RuntimeError("Core went away"), timing={"route": None, "model_s": [1.7], "tools_s": 0.0, "total_s": 1.7})
    line, numbers = logged(tmp_path, brain, rendered=(), hub_phrases=hub_mod.HUB_FIXED)
    assert "template" in line and numbers["template"] is True and "model" not in numbers


# ═══ P11 and P13: the start-up warm keeps the keeper's gates ═══
HUB = lambda *brains: NS(rooms={f"room{i}": NS(brain=b) for i, b in enumerate(brains)})       # noqa: E731


def test_fixhub_p11_a_start_up_warm_waits_for_another_rooms_warm_that_is_running():
    async def go():
        keeper, clock, _ = new_keeper()
        a, b = Brain(clock=clock), Brain(clock=clock)
        keeper.inflight.add(id(b))                                # the den's warm (the idle loop's) is in flight ...
        clock.at(1004, lambda: keeper.inflight.discard(id(b)))   # ... and over at 1004
        await keeper.startup(HUB(a, b))                           # (the kitchen's brain is the first)
        return a.warm_at
    warm_at = asyncio.run(go())
    assert warm_at and warm_at[0] >= 1004                         # two prefills of the same prompt side by side only slow each other


def test_fixhub_p11_a_start_up_warm_honours_the_minute_core_is_taken_for_away():
    async def go():
        keeper, clock, _ = new_keeper()
        a = Brain(clock=clock)
        keeper.turn_started(a)
        keeper.turn_ended(a, ok=False)                            # a turn failed at 1000: Core is away until 1060
        await keeper.startup(HUB(a))
        return a.warm_at, keeper.started
    warm_at, started = asyncio.run(go())
    assert warm_at[0] >= 1060 and started


def test_fixhub_p11_a_retry_after_a_failed_start_up_warm_comes_after_the_minute_core_is_away_and_the_tries_are_not_spent_waiting():
    async def go():
        keeper, clock, _ = new_keeper()
        a = Brain(results=[None, None, 0.6], clock=clock)         # Core did not answer twice, then did
        await keeper.startup(HUB(a))
        return a.warm_at, keeper.started, clock.slept
    warm_at, started, slept = asyncio.run(go())
    assert started and len(warm_at) == 3
    assert warm_at[1] - warm_at[0] >= 60 and warm_at[2] - warm_at[1] >= 60          # (a failed warm marks Core away for WARM_AWAY_S)
    assert [s for s in slept if s != 1.0] == [5, 10]              # the backoff is the same (5 s, then 10); only the waiting for the pause is in one-second looks


def test_fixhub_p11_no_warm_is_claimed_during_a_turn_beside_another_warm_or_while_core_is_away_in_60_runs_of_a_flapping_core():
    """The review's stress, as a pin: Core answering and not answering, turns starting and failing, a second room joining. The two jobs that warm (the start-up warm and the 240 s loop,
    run side by side as the hub runs them) never claim a warm that a gate forbids. The review counted 35 to 42 such claims per 60 seeds, all on the start-up path."""
    import random

    async def one(seed):
        rnd = random.Random(seed)
        clock = Clock(until=1000 + 1500)
        keeper, _, _ = new_keeper(clock)
        brains = [Brain(results=[None if rnd.random() < 0.5 else 0.6 for _ in range(300)], clock=clock) for _ in range(2)]
        hub = NS(rooms={"kitchen": NS(brain=brains[0])})
        clock.at(1000 + rnd.uniform(0, 120), lambda: hub.rooms.update(den=NS(brain=brains[1])))        # a second room connects
        for _ in range(rnd.randint(2, 8)):                                                          # turns, some of which fail
            begin, brain = 1000 + rnd.uniform(0, 1400), rnd.choice(brains)
            clock.at(begin, lambda b=brain: keeper.turn_started(b))
            clock.at(begin + rnd.uniform(1, 40), lambda b=brain, ok=rnd.random() < 0.6: keeper.turn_ended(b, ok=ok))
        claims, wrong = [], []
        real_claim = keeper._claim

        def claim(brain):
            if keeper.thinking or keeper.inflight or clock.t < keeper.away_until:
                wrong.append((seed, clock.t, bool(keeper.thinking), sorted(keeper.inflight) != [], clock.t < keeper.away_until))
            claims.append(clock.t)
            return real_claim(brain)
        keeper._claim = claim

        async def guarded(job):
            try:
                await job
            except Stop:
                pass
        await asyncio.gather(guarded(keeper.startup(hub)), guarded(keeper.keep_warm(hub)))
        return claims, wrong

    total, wrong = 0, []
    for seed in range(60):
        claims, bad = asyncio.run(one(seed))
        total += len(claims)
        wrong += bad
    assert total > 300, total                                     # (the runs do warm; this is not an empty pass)
    assert wrong == [], wrong[:5]


def test_fixhub_p13_a_start_up_warm_waits_while_a_turn_is_being_thought():
    async def go():
        keeper, clock, _ = new_keeper()
        a = Brain(clock=clock)
        keeper.turn_started(a)                                    # a turn is being thought when the hub has just started
        clock.at(1007, lambda: keeper.turn_ended(a))
        await keeper.startup(HUB(a))
        return a.warm_at
    warm_at = asyncio.run(go())
    assert warm_at and warm_at[0] >= 1007                         # it would compete with the real call


# ═══ the hub asks the voice gate for a whole reply ═══
class Resp:
    def __init__(self, status=200, content=b"", js=None):
        self.status_code, self.content, self._js = status, content, js

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


def gate_with(resp):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return resp
    return voicegate.Gate("http://127.0.0.1:3007/", "tok", post=post, clock=lambda: 1_800_000_000), calls


def test_fixhub_whole_the_hubs_gate_asks_for_a_whole_reply_and_still_caps_the_text():
    g, calls = gate_with(Resp(200, b"ID3audio"))
    assert g.tts("Done, Captain.") == b"ID3audio"
    assert calls[0][1]["json"] == {"text": "Done, Captain.", "whole": True}
    g.tts("hi " * 400)
    assert len(calls[1][1]["json"]["text"]) == 500 and calls[1][1]["json"]["whole"] is True        # capped like the gate does, still whole
    assert calls[0][0].endswith("/tts") and calls[0][1]["timeout"] == 60
    g.tts("Hmm. Give me a moment, monkey.", whole=False)                                         # a caller can still ask for the sentence-by-sentence take (the quip tools: the
    assert calls[2][1]["json"] == {"text": "Hmm. Give me a moment, monkey."}                      # recordings the Captain has heard were made that way)


def test_fixhub_whole_only_the_speech_asks_for_it():
    g, calls = gate_with(Resp(200, b"x", {"text": "Short version."}))
    g.summary("a long text")
    assert calls[0][1]["json"] == {"text": "a long text"}
    g.stt(b"RIFFxxxx")
    assert "json" not in calls[1][1] and calls[1][1]["data"] == b"RIFFxxxx"


def real_gate():
    """services/voice-gate/gate.py as a module. It imports its sibling ready.py, so its folder is on the path while it loads (and another gate's `ready`, left in sys.modules by an
    earlier test, is not the one it gets): this works alone and in the whole suite, in any order."""
    sys.path.insert(0, str(GATE_PY.parent))
    other = sys.modules.pop("ready", None)
    try:
        spec = importlib.util.spec_from_file_location("voice_gate_real_fix_hub", GATE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(GATE_PY.parent))
        sys.modules.pop("ready", None)
        if other is not None:
            sys.modules["ready"] = other


class Streamed:
    """What requests gives back for the studio's streamed /speak."""
    status_code = 200

    def __init__(self, parts):
        self.parts = parts

    def iter_content(self, chunk_size=None):
        yield from self.parts

    def close(self):
        pass


def test_fixhub_whole_the_request_reaches_the_studio_through_the_real_voice_gate_and_the_cache_is_the_same_either_way():
    real = real_gate()
    now = 1_800_000_000
    seen = []

    def studio(url, **kw):
        seen.append((url, kw["json"]))
        return Streamed((b"mp3-one", b"mp3-two"))

    app = real.create_app(real.gate_key("tok"), "http://whisper", "http://studio", clock=lambda: now, post=studio, get=lambda *a, **k: None)
    client = app.test_client()

    def post(url, json=None, data=None, headers=None, timeout=None):
        r = client.post(url.split("127.0.0.1:3007", 1)[1], json=json, data=data, headers=headers)
        body = r.get_data()
        r.close()
        return NS(status_code=r.status_code, content=body, json=r.get_json)

    g = voicegate.Gate("http://127.0.0.1:3007", "tok", post=post, clock=lambda: now)
    assert g.tts("I am Skippy. Hello there.") == b"mp3-onemp3-two"
    assert seen == [("http://studio/speak", {"text": "I am Skippy. Hello there.", "voice": "skippy", "whole": True})]      # the studio is asked for ONE run
    assert g.tts("I am Skippy. Hello there.") == b"mp3-onemp3-two" and len(seen) == 1             # the same words are the same cache entry: said again, no render
