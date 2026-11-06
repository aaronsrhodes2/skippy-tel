"""The house voice, snappy like an Echo: the hub's half (2026-11-05, the Captain: "streamline it so it becomes snappy like Amazon Echo").

Measured that day: Skippy Core's chat model (Qwen3.6-35B-A3B on llama.cpp, a hybrid model that reuses a cached prefix only from a checkpoint) re-reads the whole 5.6k-token
house prompt (4-6 s, 6-8 s with the tools) whenever the text before that checkpoint changed, and answers in 0.6 s when the prefix is warm; and every reply of Skippy's own
voice costs the voice studio 1.4-2 s unless that exact text was said before. The hub's half of the fix, with the tests that failed before each piece:

* H1 keep Core's prompt cache warm (SpeedKeeper): Brain.warm() in a thread at hub start (backoff 5 s doubling to 60, ten tries) and every 240 s a brain was not used; one warm
  at a time, never while a turn is being thought, never when Core is known to be away, never on the hot path, trouble logged once a minute. (It was also warmed when the NAME
  was heard, while the prompt was believed hot; the review of 2026-11-05 measured that warm at +0.06 s hot, with the clock now in the newest message, and 20 s -> 40 s cold, and
  it was taken out: test_sat_fix_hub.py and the name tests below pin that the name starts nothing.)
* H2 the fixed phrases the brain can say (acks, timer confirmations) are rendered ahead, one at a time, three seconds apart, only in a quiet house, into a cache that
  Room.say() reads first: the first time a template reply is said it makes no TTS call at all;
* H3 the turn's timing line shows the prompt's state, a clip rendered ahead, a template reply, and whatever warm/template/cache facts the brain exposes;
* H4 the end-of-speech wait (1100 ms) is NOT shortened: real recordings spliced with 400-700 ms pauses show that every shorter wait cuts some of them.

Self-contained (its own fakes), so a change to another test file's helpers cannot break it. The brain's side (Brain.warm, fixed_phrases) is another file's: it is a fake here."""
import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import hub as hub_mod
from hub import Room, TurnTiming
from timers import Timers
from voicegate import BudgetSpent, GateError

MAIN_THREAD = threading.get_ident()


# ── fakes ──
class Stop(BaseException):
    """Raised by the test clock when a never-ending loop has run as long as the test wants (not an Exception: nothing under test may swallow it)."""


class Runaway(BaseException):
    """Raised by the test clock when a loop under test goes on far past anything an honest run does (an exit that never comes): the run FAILS with this message, it does not hang."""


class Clock:
    """The keeper's clock and sleep, moved by the test: a sleep takes no real time, moves `t`, runs what the test scheduled for that moment, and stops the loop at `until`.
    A loop that has slept `max_sleeps` times and is still going is stopped with Runaway (the third review, 2026-11-05, K7 and K18: a start-up warm waiting on a turn that is over, or with no
    bound on its tries, slept for ever and the run never ended)."""

    MAX_SLEEPS = 10_000                  # the longest honest run in this file sleeps 174 times (measured)

    def __init__(self, t=1000.0, until=None, max_sleeps=MAX_SLEEPS):
        self.t, self.until, self.slept, self.due, self.max_sleeps = t, until, [], [], max_sleeps

    def __call__(self):
        return self.t

    def at(self, t, fn):
        self.due.append((t, fn))

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds
        if len(self.slept) > self.max_sleeps:
            raise Runaway(f"the loop under test slept {self.max_sleeps} times ({self.t - 1000:.0f} simulated seconds) and is not done: it waits for something that never happens")
        for item in [d for d in self.due if d[0] <= self.t]:
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
    """The voice gate: tts() counts what it is asked, says when (the keeper's clock), and can be made to fail, for one text or for all."""

    def __init__(self, clock=None, said=None):
        self.tts_calls, self.tts_at, self.fail, self.fail_all, self.clock = [], [], {}, None, clock
        self.stt_said = list(said or [])
        self.active = self.most_active = 0
        self.slow = 0.0

    def stt(self, wav):
        return self.stt_said.pop(0) if self.stt_said else ""

    def tts(self, text):
        self.active += 1
        self.most_active = max(self.most_active, self.active)
        try:
            self.tts_calls.append(text)
            self.tts_at.append(self.clock() if self.clock is not None else None)
            if self.slow:
                time.sleep(self.slow)
            err = self.fail.get(text, self.fail_all)
            if err is not None:
                raise err
            return b"mp3:" + text.encode()
        finally:
            self.active -= 1

    def summary(self, text):
        return "SUMMARY"


class Brain:
    """A brain as the hub sees it: reply(), warm() (seconds, None when Core did not answer, or an exception to raise)."""

    def __init__(self, results=None, hold=None, reply_hold=None, say="Done, monkey.", clock=None):
        self.results = list(results) if results else [0.6]
        self.hold, self.reply_hold, self.say, self.clock = hold, reply_hold, say, clock
        self.warm_calls, self.warm_at, self.warm_threads, self.heard = [], [], [], []

    def warm(self):
        self.warm_calls.append(time.monotonic())
        self.warm_at.append(self.clock.t if self.clock is not None else None)
        self.warm_threads.append(threading.get_ident())
        if self.hold is not None:
            assert self.hold.wait(10), "the test never let the warm finish"
        out = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(out, BaseException):
            raise out
        return out

    def reply(self, heard):
        self.heard.append(heard)
        if self.reply_hold is not None:
            assert self.reply_hold.wait(10), "the test never let the reply finish"
        if isinstance(self.say, BaseException):
            raise self.say
        return self.say


class PhraseBrain(Brain):
    def __init__(self, phrases, **kw):
        super().__init__(**kw)
        self.phrases = phrases

    def fixed_phrases(self):
        if isinstance(self.phrases, BaseException):
            raise self.phrases
        return list(self.phrases)


class FillerSpy:
    """A filler bank that remembers being asked for a quip."""

    def __init__(self):
        self.picked = []

    def has(self, kind):
        return False

    def pick(self, kind, text):
        self.picked.append((kind, text))
        return ("Hold that thought.", b"quip-mp3")


def new_keeper(gate=None, clock=None, until=None, **kw):
    clock = clock or Clock(until=until)
    log = []
    kw.setdefault("brain_module", NS())                        # the brain module's own fixed phrases are another file's: none unless a test gives some
    kw.setdefault("own_phrases", ())                           # ... and so are the hub's own (HUB_FIXED), unless a test asks for them
    keeper = hub_mod.SpeedKeeper(gate if gate is not None else Gate(clock), log=log.append, clock=clock, sleep=clock.sleep, **kw)
    return keeper, clock, log


def make_room(name="office", brain=None, gate=None, keeper=None, **kw):
    link, log = Link(), []
    room = Room(name, link, gate if gate is not None else Gate(), brain if brain is not None else Brain(), Timers(clock=lambda: 0), log=log.append, **kw)
    if keeper is not None:
        room.speed, room.clips = keeper, keeper.clips
    return room, link, log


async def until(condition, seconds=5.0):
    """Wait (really) for something a worker thread does."""
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "timed out waiting"
        await asyncio.sleep(0.005)


def hot(keeper, brain, ago=60.0):
    """As if a warm of this brain worked `ago` seconds ago: the prompt is believed hot, and no warm is due for it yet (the 240 s loop)."""
    keeper.hot_at[id(brain)] = keeper.touched[id(brain)] = keeper.clock() - ago


# ═══ H1: Core's prompt cache kept warm ═══
def test_speed_hub_h1a_the_start_up_warm_runs_in_a_thread_and_is_tried_again_with_a_doubling_backoff_until_it_works():
    async def go():
        keeper, clock, log = new_keeper()
        brain = Brain(results=[None, None, 0.6])                 # Core did not answer twice (it was starting), then did
        await keeper.startup(NS(rooms={"office": NS(brain=brain)}))
        return keeper, clock, brain
    keeper, clock, brain = asyncio.run(go())
    # (the review of 2026-11-05, P11: a try keeps the keeper's gates, Core's minute away after a failed warm included, so the waits that are not one-second looks at a gate are the backoff
    # itself, and a retry comes a minute after the failure at the soonest: test_sat_fix_hub.py pins that)
    assert len(brain.warm_calls) == 3 and [s for s in clock.slept if s != 1.0] == [5, 10] and clock.t - 1000 >= 120 and keeper.started
    assert brain.warm_threads and MAIN_THREAD not in brain.warm_threads            # brain.warm() blocks on the network: never on the loop


def test_speed_hub_h1a_stops_after_ten_tries_with_a_backoff_that_stops_doubling_at_a_minute():
    async def go():
        keeper, clock, log = new_keeper()
        brain = Brain(results=[None])                            # Core never answers
        await keeper.startup(NS(rooms={"office": NS(brain=brain)}))
        return keeper, clock, brain
    keeper, clock, brain = asyncio.run(go())
    assert len(brain.warm_calls) == 10 and [s for s in clock.slept if s != 1.0] == [5, 10, 20, 40, 60, 60, 60, 60, 60] and not keeper.started       # (one-second looks at the gates between)


def test_speed_hub_h1a_waits_for_a_box_to_connect_and_leaves_a_brain_that_cannot_warm_alone():
    async def go():
        keeper, clock, log = new_keeper()
        brain, hub = Brain(), NS(rooms={})
        clock.at(1003, lambda: hub.rooms.update(office=NS(brain=brain)))
        await keeper.startup(hub)
        none_keeper, none_clock, _ = new_keeper()
        plain = NS(rooms={"den": NS(brain=object())})            # an older brain: no warm()
        await none_keeper.startup(plain)
        return keeper, clock, brain, none_keeper, none_clock
    keeper, clock, brain, none_keeper, none_clock = asyncio.run(go())
    assert len(brain.warm_calls) == 1 and clock.t >= 1003 and keeper.started       # nothing is asked of a room that is not there yet
    assert none_clock.slept == [] and none_keeper.started                          # nothing to warm, nothing to wait for (the phrases need not wait either)


@pytest.mark.parametrize("state", ["hot", "cold"])
@pytest.mark.parametrize("how", ["name", "tap", "box woke"])
def test_speed_hub_h1b_the_name_a_tap_or_a_box_that_woke_by_itself_starts_no_warm_and_the_turn_goes_on(how, state):
    """(the review of 2026-11-05, P5) The wake used to warm the prompt in the background while he finished the sentence. Measured on Core: +0.06 s with the prompt hot (the clock rides in
    the newest message now), and a turn twice as slow (20 s -> 40 s) when the keeper's belief that it was hot was stale. Whatever the keeper believes, the wake starts nothing."""
    async def go():
        brain = Brain()
        keeper, clock, log = new_keeper()
        room, link, _ = make_room(brain=brain, keeper=keeper)
        if state == "hot":
            hot(keeper, brain, ago=100)                          # (long past anything a recent turn would postpone)
        began = time.monotonic()
        if how == "name":
            room._woke("skippy")
        elif how == "tap":
            room.tap()
        else:
            await room.begin(False)
        cued = time.monotonic() - began
        await asyncio.sleep(0.05)                                # (a warm would be running in a thread by now)
        during = (len(brain.warm_calls), bool(keeper.inflight), keeper.core_state(brain))
        await room.handle("what time is it", typed=True)
        return cued, during, link, brain
    cued, during, link, brain = asyncio.run(go())
    assert cued < 0.2 and during == (0, False, state)            # the wake returned at once and asked nothing of Core (the keeper's belief is what it was)
    assert link.spoken == ["Done, monkey."] and brain.heard == ["what time is it"] and brain.warm_calls == []


@pytest.mark.parametrize("fails", [False, True])
def test_speed_hub_h1_no_warm_runs_while_a_turn_is_being_thought_and_the_flag_clears_when_the_turn_fails_too(fails):
    async def go():
        release = threading.Event()
        keeper, clock, log = new_keeper(until=1100)
        brain = Brain(reply_hold=release, say=RuntimeError("Core went away") if fails else "Done, monkey.", clock=clock)
        room, link, _ = make_room(brain=brain, keeper=keeper)
        hub = NS(rooms={"office": room})
        hot(keeper, brain, ago=1000)                             # (warmed long ago: the 240 s loop finds it due at once, only the turn in progress is in the way)
        turn = asyncio.ensure_future(room.handle("what time is it", typed=True))
        await until(lambda: brain.heard)
        thinking = bool(keeper.thinking)
        try:
            await keeper.keep_warm(hub)                          # 100 s of the loop looking every 15 s, while the turn is thought
        except Stop:
            pass
        during = len(brain.warm_calls)
        quiet_during = keeper.quiet(hub)
        release.set()
        await turn
        return thinking, during, quiet_during, bool(keeper.thinking), keeper, clock, link
    thinking, during, quiet_during, after, keeper, clock, link = asyncio.run(go())
    assert thinking and during == 0 and not quiet_during and not after
    assert (clock.t < keeper.away_until) is fails                # a turn that failed is Core away; one that was answered clears it


def test_speed_hub_h1c_a_brain_not_used_for_240_s_is_warmed_and_a_turn_or_a_warm_postpones_it():
    """(the first look at a brain that was never warmed warms it: a room that has just connected is ready within 15 s)"""
    async def go():
        keeper, clock, log = new_keeper(until=1700)
        brain = Brain(clock=clock)
        clock.at(1400, lambda: keeper.turn_ended(brain))         # a turn ended at 1400
        try:
            await keeper.keep_warm(NS(rooms={"office": NS(brain=brain)}))
        except Stop:
            pass
        return brain
    brain = asyncio.run(go())
    assert [round(t) for t in brain.warm_at] == [1015, 1255, 1645]      # at the first look (never warmed), 240 s after that, then 240 s after the turn (a loop that looks every 15 s)


def test_speed_hub_h1c_the_loop_waits_out_a_turn_in_progress_and_leaves_a_warm_in_flight_alone():
    async def go():
        keeper, clock, log = new_keeper(until=1600)
        brain = Brain(clock=clock)
        clock.at(1250, lambda: keeper.turn_started(brain))
        clock.at(1262, lambda: keeper.turn_ended(brain))
        try:
            await keeper.keep_warm(NS(rooms={"office": NS(brain=brain)}))
        except Stop:
            pass
        return brain
    brain = asyncio.run(go())
    assert [round(t) for t in brain.warm_at] == [1015, 1510]     # not at 1255 (a turn was being thought), and 240 s after that turn ended


def test_speed_hub_h1_a_confirmation_the_brain_worded_itself_never_asked_the_model_so_it_proves_and_warms_nothing():
    async def go():
        keeper, clock, log = new_keeper()
        brain = Brain(say="Done.")
        room, link, _ = make_room(brain=brain, keeper=keeper)
        hot(keeper, brain, ago=100)
        before = (keeper.hot_at[id(brain)], keeper.touched[id(brain)])
        brain.timing = {"route": "music", "model_s": [], "tools_s": 0.1, "total_s": 0.1}          # a phrase of the brain's own bank: no model call
        await room.handle("pause", typed=True)
        template = (keeper.hot_at[id(brain)], keeper.touched[id(brain)]) == before
        brain.timing = {"route": None, "model_s": [0.9], "tools_s": 0.0, "total_s": 0.9}          # a reply the model made
        await room.handle("tell me a joke", typed=True)
        asked = keeper.hot_at[id(brain)] == keeper.touched[id(brain)] == clock.t
        brain.timing = None                                      # a brain that keeps no record is taken to have asked
        clock.t += 50
        await room.handle("and another", typed=True)
        return template, asked, keeper.hot_at[id(brain)] == clock.t
    assert asyncio.run(go()) == (True, True, True)


def test_speed_hub_h1_one_warm_at_a_time_two_prefills_of_the_same_prompt_only_slow_each_other():
    """(The warm of a room that wakes is gone, P5; the warm running while another starts is the idle loop and the start-up warm, which test_sat_fix_hub.py pins: P11.)"""
    async def loop():
        keeper, clock, log = new_keeper(until=1100)
        a, b = Brain(clock=clock), Brain(clock=clock)
        try:
            await keeper.keep_warm(NS(rooms={"kitchen": NS(brain=a), "den": NS(brain=b)}))   # two rooms that have just connected: neither was ever warmed
        except Stop:
            pass
        return [round(t) for t in a.warm_at], [round(t) for t in b.warm_at]
    assert asyncio.run(loop()) == ([1015], [1030])               # one per look, 15 s apart, never together


def test_speed_hub_h1_a_warm_that_raises_or_fails_never_reaches_a_turn_and_is_logged_once_a_minute_at_most():
    async def go():
        keeper, clock, log = new_keeper()
        brain = Brain(results=[RuntimeError("boom"), None, None, RuntimeError("boom")])
        out = []
        for gap in (0, 10, 10, 60):                              # four failures at 0 s, 10 s, 20 s, 80 s
            clock.t += gap
            out.append(await keeper.warm(brain, "test"))         # never raises
        return out, log
    out, log = asyncio.run(go())
    assert out == [None, None, None, None] and len(log) == 2     # one line for the first minute, one for the next


def test_speed_hub_h1_core_known_to_be_away_is_skipped_silently_for_a_minute_and_tried_again_after():
    async def go():
        keeper, clock, log = new_keeper(until=1100)
        down, up = Brain(results=[None]), Brain(clock=clock)     # Core answers one room's warm with nothing ... and the other room's prompt was never warmed (the loop finds it due)
        await keeper.warm(down, "start")                         # Core did not answer, at 1000: away until 1060
        logged, seen = len(log), []
        clock.at(1059, lambda: seen.append(len(log)))            # (what the loop had said by the time it tried again)
        try:
            await keeper.keep_warm(NS(rooms={"office": NS(brain=up)}))
        except Stop:
            pass
        return logged, seen, [round(t) for t in up.warm_at], keeper.core_state(up)
    assert asyncio.run(go()) == (1, [1], [1060], "hot")          # the looks at 1015, 1030 and 1045 warmed nothing and said nothing; the one at 1060 did


# ═══ H2: the fixed phrases rendered ahead ═══
def test_speed_hub_h2_a_phrase_rendered_ahead_is_said_with_no_tts_call_at_all():
    async def go():
        keeper, clock, log = new_keeper()
        gate = keeper.gate
        brain = PhraseBrain(["Done.", "Okay."])
        room, link, _ = make_room(brain=brain, gate=gate, keeper=keeper)
        hub = NS(rooms={"office": room})
        report = await keeper.prerender_pass(hub)
        rendered = list(gate.tts_calls)
        await room.say("Done.")                                  # the first time it is said
        await room.say("Something never rendered.")
        return report, rendered, gate.tts_calls, link
    report, rendered, calls, link = asyncio.run(go())
    assert report == (2, 0, False) and rendered == ["Done.", "Okay."]
    assert calls == ["Done.", "Okay.", "Something never rendered."]                     # say("Done.") added nothing; only the other text went to the studio
    assert link.voices[0] == ("mp3", b"mp3:Done.") and link.spoken == ["Done.", "Something never rendered."]


def test_speed_hub_h2_the_phrases_are_the_brains_acks_and_fixed_phrases_once_each_and_the_hubs_own():
    module = NS(ACKS=["Done.", "Okay."], fixed_phrases=lambda: ["Timer set for one minute.", "Done."])
    brain = PhraseBrain(["Got it.", "Okay.", "", None, 7])
    brain.ACKS = ["Sure."]
    keeper, _, _ = new_keeper(brain_module=module, own_phrases=hub_mod.HUB_FIXED)
    got = keeper.phrases(NS(rooms={"a": NS(brain=brain), "b": NS(brain=brain)}))          # two rooms, one brain
    assert got == ["Got it.", "Okay.", "Sure.", "Timer set for one minute.", "Done."] + list(hub_mod.HUB_FIXED)       # the brain's own, the module's, then the hub's; each once
    assert "My brain is not answering. Typical." in hub_mod.HUB_FIXED and all(isinstance(p, str) and p for p in got)
    broken = PhraseBrain(RuntimeError("no list"))                                          # a brain whose list fails costs the other sources nothing
    again = new_keeper(brain_module=module)[0].phrases(NS(rooms={"a": NS(brain=broken)}))
    assert again == ["Timer set for one minute.", "Done.", "Okay."]
    assert new_keeper(brain_module=NS(), own_phrases=hub_mod.HUB_FIXED)[0].phrases(NS(rooms={})) == list(hub_mod.HUB_FIXED)       # nothing known: only what the hub itself says
    assert new_keeper(brain_module=NS())[0].phrases(NS(rooms={})) == []


def test_speed_hub_h2_one_at_a_time_and_at_least_three_seconds_apart():
    async def go():
        clock = Clock()
        gate = Gate(clock)
        gate.slow = 0.02
        keeper, _, _ = new_keeper(gate, clock=clock)
        room, link, _ = make_room(brain=PhraseBrain(["A.", "B.", "C.", "D."]), gate=gate, keeper=keeper)
        await keeper.prerender_pass(NS(rooms={"office": room}))
        return gate
    gate = asyncio.run(go())
    ours = [(c, t) for c, t in zip(gate.tts_calls, gate.tts_at) if c in ("A.", "B.", "C.", "D.")]
    assert [c for c, _ in ours] == ["A.", "B.", "C.", "D."] and gate.most_active == 1
    assert all(b - a >= 3.0 for (_, a), (_, b) in zip(ours, ours[1:]))


@pytest.mark.parametrize("busy", ["busy", "listening", "followup", "speaking", "thinking", "heard a voice", "hearing one now", "alarm ringing", "routine running"])
def test_speed_hub_h2_nothing_is_rendered_while_a_room_is_speaking_thinking_listening_or_has_just_heard_a_voice(busy):
    async def go():
        clock = Clock()
        gate = Gate(clock)
        keeper, _, _ = new_keeper(gate, clock=clock)
        room, link, _ = make_room(brain=PhraseBrain(["A."]), gate=gate, keeper=keeper)
        hub = NS(rooms={"office": room})
        undo = lambda: None                                      # noqa: E731
        if busy in ("busy", "listening", "followup"):
            room.mode = busy
            undo = lambda: setattr(room, "mode", "idle")         # noqa: E731
        elif busy == "speaking":
            await room.voice.acquire()
            undo = room.voice.release
        elif busy == "thinking":
            keeper.turn_started(room.brain)
            undo = lambda: keeper.turn_ended(room.brain)         # noqa: E731
        elif busy == "heard a voice":
            room._voice_at = time.monotonic()
            undo = lambda: setattr(room, "_voice_at", time.monotonic() - 100)   # noqa: E731
        elif busy == "hearing one now":
            room.buf = []
            undo = lambda: setattr(room, "buf", None)            # noqa: E731
        elif busy == "alarm ringing":
            room.clock = NS(ringing={"alarm": 1})
            undo = lambda: setattr(room, "clock", None)          # noqa: E731
        else:
            room.daily = NS(busy=True)
            undo = lambda: setattr(room, "daily", None)          # noqa: E731
        was_quiet = keeper.quiet(hub)
        clock.at(1020, undo)                                     # the house goes quiet at 1020
        await keeper.prerender_pass(hub)
        return was_quiet, keeper.quiet(hub), gate
    was_quiet, now_quiet, gate = asyncio.run(go())
    assert not was_quiet and now_quiet and gate.tts_calls == ["A."]
    assert gate.tts_at[0] >= 1020                                # not a moment before


def test_speed_hub_h2_it_waits_for_the_first_warm_or_a_minute_whichever_comes_first():
    async def go(started_at=None):
        clock = Clock(until=1300)
        gate = Gate(clock)
        keeper, _, _ = new_keeper(gate, clock=clock)
        room, link, _ = make_room(brain=PhraseBrain(["A."]), gate=gate, keeper=keeper)
        if started_at is not None:
            clock.at(started_at, lambda: setattr(keeper, "started", True))
        try:
            await keeper.prerender(NS(rooms={"office": room}))
        except Stop:
            pass
        return gate.tts_at[0]
    assert asyncio.run(go()) >= 1060                             # Core never warmed: after a minute anyway
    assert 1005 <= asyncio.run(go(started_at=1005)) < 1020       # the warm worked at 1005: the phrases follow at once


def test_speed_hub_h2_a_render_that_fails_is_skipped_three_in_a_row_end_the_pass_and_the_next_pass_renders_what_is_missing():
    async def go():
        clock = Clock()
        gate = Gate(clock)
        keeper, _, log = new_keeper(gate, clock=clock)
        room, link, _ = make_room(brain=PhraseBrain(["A.", "B.", "C.", "D.", "E."]), gate=gate, keeper=keeper)
        hub = NS(rooms={"office": room})
        gate.fail = {"B.": GateError("text to speech refused (503)")}
        first = await keeper.prerender_pass(hub)                 # one text fails: skipped, the others go on
        have = [p for p in "ABCDE" if f"{p}." in keeper.clips]
        calls = len(gate.tts_calls)
        gate.fail, gate.fail_all = {}, GateError("the voice gate is not answering")
        keeper.clips.clear()                                     # (a gate that was restarted, and a cache that was)
        down = await keeper.prerender_pass(hub)                  # the gate is down: three tries, then the pass is over
        down_calls = len(gate.tts_calls) - calls
        gate.fail_all = None
        up = await keeper.prerender_pass(hub)                    # the gate is back: everything that is missing
        before = len(gate.tts_calls)
        await room.say("B.")                                     # the one that failed the first time, said now: nothing left to ask the studio
        return first, have, down, down_calls, up, [p for p in "ABCDE" if f"{p}." in keeper.clips], len(gate.tts_calls) - before
    first, have, down, down_calls, up, have_now, asked = asyncio.run(go())
    assert first == (4, 1, False) and have == ["A", "C", "D", "E"]
    assert down == (0, 3, False) and down_calls == 3
    assert up == (5, 0, False) and have_now == list("ABCDE") and asked == 0


def test_speed_hub_h2_a_spent_voice_budget_stops_the_pass_and_the_machines_own_voice_is_never_kept():
    async def go():
        clock = Clock()
        gate = Gate(clock)
        gate.fail_all = BudgetSpent("today's voice budget is spent")
        keeper, _, log = new_keeper(gate, clock=clock)
        room, link, _ = make_room(brain=PhraseBrain(["A.", "B.", "C."]), gate=gate, keeper=keeper)
        room.local_voice = lambda text: (b"\x00\x00", 16000)     # the machine's own voice: what _voice falls back to
        report = await keeper.prerender_pass(NS(rooms={"office": room}))
        return report, gate.tts_calls, len(keeper.clips)
    report, calls, kept = asyncio.run(go())
    assert report == (0, 0, True) and calls == ["A."] and kept == 0           # one try; the rest wait for tomorrow; no pcm clip in the cache


def test_speed_hub_h2_a_render_never_plays_a_quip_in_a_room_while_the_studio_is_busy():
    async def go():
        clock = Clock()
        gate = Gate(clock)
        gate.fail_all = GateError("the voice gate is busy (429)")
        keeper, _, _ = new_keeper(gate, clock=clock)
        spy = FillerSpy()
        room, link, _ = make_room(brain=PhraseBrain(["A.", "B."]), gate=gate, keeper=keeper, filler=spy)
        await keeper.prerender_pass(NS(rooms={"office": room}))
        quiet_run = (list(spy.picked), list(link.announced), list(link.shown))
        try:
            await room._tts_piece("Something said live.", tries=2, wait_s=0.0)      # the real reply path, by contrast, covers a busy studio with a quip
        except GateError:
            pass
        return quiet_run, list(spy.picked)
    quiet_run, live = asyncio.run(go())
    assert quiet_run == ([], [], [])
    assert live and live[0][1] == "Something said live."


def test_speed_hub_h2_the_rooms_share_the_clips_and_a_long_phrase_is_joined_like_any_reply(monkeypatch):
    joined = []
    monkeypatch.setattr(hub_mod, "one_stream", lambda mp3, **k: joined.append(mp3) or b"JOINED")
    long_phrase = " ".join(["This is one rather long fixed sentence of the brain's."] * 14)          # over 480 characters: pieces
    assert len(hub_mod.split_for_voice(long_phrase)) >= 2 and len(long_phrase) < 1500

    async def go():
        clock = Clock()
        gate = Gate(clock)
        keeper, _, _ = new_keeper(gate, clock=clock)
        kitchen, k_link, _ = make_room("kitchen", PhraseBrain([long_phrase]), gate=gate, keeper=keeper)
        den, d_link, _ = make_room("den", Brain(), gate=gate, keeper=keeper)
        await keeper.prerender_pass(NS(rooms={"kitchen": kitchen, "den": den}))
        made = len(gate.tts_calls)
        await den.say(long_phrase)                               # another room, the same cache
        return made, len(gate.tts_calls), d_link, len(joined)
    made, after, d_link, joins = asyncio.run(go())
    assert made >= 2 and after == made and joins == 1                            # the pieces were made one by one and joined once; saying it made no call and no join
    assert d_link.voices == [("mp3", b"JOINED")]


def test_speed_hub_h2_the_background_job_does_what_it_is_for_start_to_finish_and_survives_a_dead_gate():
    async def go():
        clock = Clock(until=1500)
        gate = Gate(clock)
        gate.fail_all = GateError("the voice gate is not answering")           # the gate is still starting when the hub is
        keeper, _, log = new_keeper(gate, clock=clock)
        room, link, _ = make_room(brain=PhraseBrain(["A.", "B."]), gate=gate, keeper=keeper)
        clock.at(1100, lambda: setattr(gate, "fail_all", None))                 # ... and comes up a little later
        try:
            await keeper.prerender(NS(rooms={"office": room}))
        except Stop:
            pass
        return [p for p in ("A.", "B.") if p in keeper.clips], gate.tts_calls
    have, calls = asyncio.run(go())
    assert have == ["A.", "B."] and calls == ["A.", "B.", "A.", "B."]           # the pass that failed was tried again after a while, and the gate was not hammered meanwhile


def test_speed_hub_the_clip_cache_keeps_a_bounded_number_and_forgets_the_oldest():
    cache = hub_mod.ClipCache(limit=3)
    for i in range(5):
        cache.put(f"phrase {i}", ("mp3", b"x"))
    assert len(cache) == 3 and "phrase 0" not in cache and "phrase 1" not in cache and cache.get("phrase 4") == ("mp3", b"x")
    cache.put("phrase 2", ("mp3", b"y"))                         # a text that is in already is replaced, not added
    assert len(cache) == 3 and cache.get("phrase 2") == ("mp3", b"y") and cache.get("nothing") is None
    assert cache.get("  phrase 2\n") == ("mp3", b"y") and "phrase 2 " in cache                  # matched as the voice gate matches: without the whitespace around it
    cache.clear()
    assert len(cache) == 0
    assert hub_mod.fixed_phrases_of(NS(fixed_phrases=lambda: [" Done. ", "Done.", "\n"], ACKS=("Okay.\n",))) == ["Done.", "Okay."]


def test_speed_hub_a_job_that_fails_is_logged_and_started_again_and_never_takes_the_others_down():
    async def go():
        keeper, clock, log = new_keeper()
        tries = []

        async def job(hub):
            tries.append(1)
            if len(tries) == 1:
                raise ValueError("a bug in a favour")

        await keeper._guarded(job, NS(rooms={}))
        return tries, clock.slept, log
    tries, slept, log = asyncio.run(go())
    assert tries == [1, 1] and slept == [60] and len(log) == 1 and "ValueError" in log[0]


def test_speed_hub_run_does_all_three_jobs_on_a_real_clock_and_ends_cleanly_when_cancelled(monkeypatch):
    for name, value in (("RENDER_AFTER_S", 0.2), ("RENDER_GAP_S", 0.05), ("WARM_CHECK_S", 0.05), ("WARM_IDLE_S", 0.3), ("WARM_START_BACKOFF_S", (0.05, 0.1)), ("WARM_AWAY_S", 0.2)):
        monkeypatch.setattr(hub_mod, name, value)                # (WARM_AWAY_S: a failed warm takes Core for away this long, and the start-up retry waits it out: P11)

    async def go():
        gate, log = Gate(), []
        brain = PhraseBrain(["Done.", "Okay."], results=[None, 0.6])        # Core is slow to wake: the first warm fails, the next works
        keeper = hub_mod.SpeedKeeper(gate, log=log.append, brain_module=NS(), own_phrases=())
        room, link, _ = make_room(brain=brain, gate=gate, keeper=keeper)
        job = asyncio.ensure_future(keeper.run(NS(rooms={"office": room})))
        await until(lambda: keeper.started and len(keeper.clips) == 2 and len(brain.warm_calls) >= 3, 8)        # start-up (twice), the phrases, and the idle loop
        job.cancel()
        try:
            await job
            cancelled = False
        except asyncio.CancelledError:
            cancelled = True
        return cancelled, gate.tts_calls, brain.warm_calls, log
    cancelled, tts, warms, log = asyncio.run(go())
    assert cancelled and sorted(tts) == ["Done.", "Okay."] and len(warms) >= 3
    assert not [x for x in log if "failed" in x and "warm" not in x]


# ═══ H3: the timing line ═══
def test_speed_hub_h3_without_the_new_pieces_the_timing_line_and_numbers_are_what_they_were():
    t = TurnTiming({"wait": 0.7, "hear": 0.9})
    t.thought(3.4, {"route": None, "model_s": [2.1, 1.3], "tools_s": 0.2, "total_s": 3.4})
    t.voiced(1.1)
    assert t.line() == "(timing: wait 0.7s, hear 0.9s, think 3.4s [route none; model 2.1+1.3s; tools 0.2s], voice 1.1s = 6.1s)"
    assert t.numbers() == {"wait": 0.7, "hear": 0.9, "think": 3.4, "voice": 1.1, "total": 6.1, "route": "none", "model": [2.1, 1.3], "tools": 0.2}


def test_speed_hub_h3_the_line_shows_the_prompts_state_a_clip_rendered_ahead_and_a_template_reply():
    t = TurnTiming({"wait": 0.7, "hear": 0.9})
    t.core, t.template = "hot", True
    t.thought(1.0, {"route": None, "model_s": [0.6], "tools_s": 0.0, "total_s": 1.0})
    t.voiced(0.0, cached=True)
    assert t.line() == "(timing: wait 0.7s, hear 0.9s, think 1.0s [route none; model 0.6s; tools 0.0s; prompt hot; template], voice 0.0s cached = 2.6s)"
    assert t.numbers() == {"wait": 0.7, "hear": 0.9, "think": 1.0, "voice": 0.0, "total": 2.6, "route": "none", "model": [0.6], "tools": 0.0, "core": "hot", "cached": True, "template": True}
    cold = TurnTiming(None)                                      # a typed turn whose brain keeps no split: the prompt's state still shows
    cold.core = "cold"
    cold.thought(6.2)
    cold.voiced(1.5)
    assert cold.line() == "(timing: think 6.2s [prompt cold], voice 1.5s = 7.7s)" and cold.numbers()["core"] == "cold" and "cached" not in cold.numbers()


def test_speed_hub_h3_what_the_brain_exposes_is_shown_whatever_it_is_called():
    t = TurnTiming({"wait": 1.1, "hear": 0.4})
    t.template = True                                            # the hub's guess: the reply is a fixed phrase
    t.thought(0.8, {"route": "music", "model_s": [0.6], "tools_s": 0.1, "total_s": 0.8, "warm_hit": True, "template": False, "cache_read": 5125,
                    "ack": "Done.", "junk": object(), "n": float("nan")})
    t.voiced(0.2)
    line, numbers = t.line(), t.numbers()
    assert "warm hit" in line and "no template" in line and "cache_read 5125" in line and "ack Done." in line
    assert "junk" not in line and "nan" not in line and line.count("template") == 1       # the brain's own word beats the hub's guess
    assert numbers["warm_hit"] is True and numbers["template"] is False and numbers["cache_read"] == 5125 and numbers["ack"] == "Done." and "junk" not in numbers


def test_speed_hub_h3_a_turn_in_a_room_with_a_keeper_logs_the_prompt_the_template_and_the_clip_rendered_ahead(tmp_path):
    import housemem

    async def go():
        clock = Clock()
        gate = Gate(clock, said=["turn on the lamp"])
        keeper, _, _ = new_keeper(gate, clock=clock)
        brain = PhraseBrain(["Done."], say="Done.")
        link, log = Link(), []
        room = Room("office", link, gate, brain, Timers(clock=lambda: 0), log=log.append, hlog=housemem.HouseLog(tmp_path))
        room.speed, room.clips = keeper, keeper.clips
        await keeper.prerender_pass(NS(rooms={"office": room}))
        hot(keeper, brain)                                       # (the prompt was warmed a minute ago)
        await room._utterance("listening", [0.0] * 800, 1.1)
        return log, gate
    log, gate = asyncio.run(go())
    line = next(x for x in log if "(timing:" in x)
    assert "prompt hot" in line and "template" in line and "voice 0.0s cached" in line, line
    path = next((tmp_path / "log").glob("*.jsonl"))
    record = next(r for r in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()) if r["kind"] == "command")
    assert record["timing"]["core"] == "hot" and record["timing"]["template"] is True and record["timing"]["cached"] is True
    assert gate.tts_calls == ["Done."]                           # the studio was asked once, ahead of time; saying it asked nothing


def test_speed_hub_h3_a_room_without_a_keeper_adds_nothing_to_the_record(tmp_path):
    import housemem

    async def go():
        link, log = Link(), []
        room = Room("office", link, Gate(said=["hello there"]), Brain(), Timers(clock=lambda: 0), log=log.append, hlog=housemem.HouseLog(tmp_path))
        await room._utterance("listening", [0.0] * 800, 1.1)
        return log
    log = asyncio.run(go())
    path = next((tmp_path / "log").glob("*.jsonl"))
    record = next(r for r in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()) if r["kind"] == "command")
    assert not {"core", "cached", "template"} & set(record["timing"]) and "prompt" not in next(x for x in log if "(timing:" in x)


# ═══ the test clock itself ═══
def test_speed_hub_a_clock_that_is_asked_to_sleep_without_end_ends_the_test():
    """The bound itself (the fourth review, 2026-11-06; K7 and K18 of the third review hung this file): a loop that never ends fails with Runaway after max_sleeps sleeps, quickly, whatever it
    waits for; and the default bound is far above the longest honest run of this file (174 sleeps), so no honest test meets it."""
    async def forever():
        clock = Clock(max_sleeps=200)
        while True:
            await clock.sleep(1.0)
    t0 = time.monotonic()
    with pytest.raises(Runaway, match="200 times"):
        asyncio.run(forever())
    assert time.monotonic() - t0 < 5 and Clock.MAX_SLEEPS >= 1000


# ═══ the wiring ═══
def test_speed_hub_the_keeper_is_on_unless_the_vault_says_off_and_a_room_gets_the_shared_clips():
    gate = Gate()
    assert hub_mod.speed_from_env({}, gate, log=print) is not None
    for off in ("off", "0", "no", "OFF"):
        assert hub_mod.speed_from_env({"SATELLITE_SPEED": off}, gate, log=print) is None, off
    keeper = hub_mod.speed_from_env({}, gate, log=print)
    a, _, _ = make_room("a")
    b, _, _ = make_room("b")
    hub_mod.attach_speed(a, keeper)
    hub_mod.attach_speed(b, keeper)
    assert a.speed is keeper and a.clips is keeper.clips is b.clips
    c, _, _ = make_room("c")
    hub_mod.attach_speed(c, None)                                # switched off: the room is as it was
    assert c.speed is None and c.clips is None


# ═══ H4: the end-of-speech wait stays (real recordings, spliced with pauses) ═══
REAL = Path(__file__).resolve().parents[3] / "state" / "wakeword" / "real"


@pytest.mark.skipif(not REAL.is_dir() or len(list(REAL.glob("*/*.wav"))) < 20, reason="the Captain's recordings (state/wakeword/real, git-ignored) are on skippy-pc only")
def test_speed_hub_h4_the_1100_ms_wait_never_cuts_a_400_to_700_ms_pause_in_his_own_voice_and_a_shorter_one_does():
    """The Captain's own recordings through the pod's microphone (his name, said every way he does), spliced: the first clip, a pause of the room's own quiet, the second clip, then
    three quiet seconds, run through the real Vad as Room.feed does (50 ms blocks, the vault's mic gain and floor), once as it is just after the name (armed: the lowered bar)
    and once not. Measured 2026-11-05 over 74 spliced pairs (pauses of 200 to 1300 ms): 1100 ms never cut a pause under 800 ms; 1000 ms cut one of 700 (armed); 900 ms cut 600 and 700
    (armed); 800 ms cut 650 and 700 (7 of 74 pairs, not armed) and 500 and 600 (armed). So shortening the wait, for short commands or at all, cuts sentences that continue after a
    normal pause, and the 1100 ms stays. This test is the evidence: it needs his recordings (state/wakeword/real, git-ignored), so it runs on skippy-pc only."""
    import glob
    import struct
    import wave

    from vad import Vad, loudest, rms
    gain, floor, block = 5.0, 0.015, 800                         # the vault's mic gain and floor, 50 ms blocks, as Room.feed sees them

    def levels_of(path):
        w = wave.open(str(path), "rb")
        data = w.readframes(w.getnframes())
        s = struct.unpack("<%dh" % (len(data) // 2), data[:len(data) // 2 * 2])
        s = [max(-1.0, min(1.0, x / 32768 * gain)) for x in s]
        return [rms(s[i:i + block]) for i in range(0, len(s) - block + 1, block)]

    def split(levels):
        quiet = sorted(levels)[len(levels) // 5]
        loud = [i for i, x in enumerate(levels) if x > max(quiet * 2.5, floor * 0.8)]
        return (levels[:loud[0]] or levels[:2], levels[loud[0]:loud[-1] + 1]) if loud else (levels, [])

    paths = sorted(glob.glob(str(REAL / "*" / "*.wav")) + glob.glob(str(REAL / "test" / "*" / "*.wav")))
    clips = [c for c in (split(levels_of(p)) for p in paths) if c[1]]
    assert len(clips) >= 20

    def cut(a, b, pause_ms, wait_ms, armed):
        """Did the Vad say "end" before the second clip began (the sentence cut in its pause)?"""
        noise = a[0]
        seq = (noise * 40)[:24] + a[1] + (noise * 40)[:pause_ms // 50] + b[1] + (noise * 80)[:60]
        second = (24 + len(a[1]) + pause_ms // 50) * 50
        v = Vad(floor_min=floor, end_silence_ms=wait_ms)
        for i, level in enumerate(seq):
            if armed and i == 10:
                v.arm(loudest(a[1]))                             # the name has just been said: the bar for his first words is the lowered one
            if v.push(level, (i + 1) * 50) == "end" and (i + 1) * 50 <= second + 50:
                return True
        return False

    pairs = list(zip(clips, clips[1:]))
    for armed in (False, True):
        for pause in (400, 500, 600, 700):
            assert not [1 for a, b in pairs if cut(a, b, pause, 1100, armed)], f"1100 ms cut a {pause} ms pause (armed={armed})"
    shorter = sum(cut(a, b, pause, 800, armed) for a, b in pairs for pause in (500, 600, 700) for armed in (False, True))
    assert shorter > 0, "an 800 ms wait no longer cuts any of his pauses: shortening it can be looked at again (H4)"
