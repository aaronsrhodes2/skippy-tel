"""Round 3 of the 2026-11-05 review of the house hub: what the round-2 reviewer found wrong or left open in round 2's fixes.

* "Bilby, turn off DJ mode" / "tell Bilby to turn off shuffle" were sent, by the stop guards, to the play grammar, which searched the library for a song called "turn off dj", found
  nothing and shuffled the WHOLE library (a regression of round 1, which paused);
* a request-shaped question with no music in it ("can I get a timer for ten minutes?") was taken for a music ask, so a false "Music is now playing" was asked again instead of cut;
* "what can you actually do" no longer counted as an ability question;
* an alarm that fell due in the very tick a ring gave up was closed unrung and lost;
* timers of two rooms that fell due together were said over each other;
* a cancelled leading alarm handed the ring to a reminder that had been said twice, which was then read a third time;
* a morning briefing asked for by hand AFTER its slot did not mark the day, so the scheduled one said it all again.
"""
import asyncio
import time
from types import SimpleNamespace as NS

import pytest

import alarm_clock
import alarms as alarms_mod
import brain
import house_layout
import musicguard
from alarm_clock import AlarmClock
from brain import Brain
from timers import Timers


# ── the play grammar and the stop guards ────────────────────────────────────────────────────────────────────────────────

class _Client:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return NS(content=[NS(type="text", text="Done, monkey.")])


def _ran(heard, playing=True):
    clock = [1000.0]
    b = Brain(_Client(), "m", Timers(clock=lambda: clock[0]), persona="PERSONA", clock=lambda: clock[0], now=lambda: "Sunday 09:30")
    ran = []
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("play_music", "music", "speakers", "dj_mode")]
    b.extra_tools, b.run_extra = tools, lambda name, args: ran.append((name, dict(args))) or f"({name} done)"
    b._house_playing = lambda: playing
    b.reply(heard)
    return ran


OFF_FORMS = ["Bilby, turn off DJ mode", "bilby turn off dj mode", "Bilby, stop the dj", "tell bilby to stop the dj", "Bilby, dj off", "Bilby, DJ mode off", "Bilby, turn off shuffle",
             "Bilby, no more shuffle", "tell bilby to turn off shuffle", "Skippy, tell Bilby to switch off dj mode", "Bilby, kill the dj", "Bilby, shuffle off", "Bilby, enough shuffle"]


@pytest.mark.parametrize("playing", [True, False])
@pytest.mark.parametrize("heard", OFF_FORMS)
def test_r3_a_dj_or_shuffle_switch_said_to_bilby_is_the_models_never_a_search_that_shuffles_the_library(heard, playing):
    assert brain.music_grammar(heard) is None, heard
    ran = _ran(heard, playing)
    assert not any(name == "play_music" for name, _ in ran), (heard, ran)             # round 2: play_music {'what': 'turn off dj'} replaced the queue with the whole library
    assert ran == [], (heard, ran)                                                    # no pre-route claims it: the model has the dj_mode tool


@pytest.mark.parametrize("heard, key", [("Bilby, play goth upstairs", "genre"), ("Bilby, play heavy metal", "genre"), ("Bilby, play some Bauhaus", "what"), ("Bilby", "what"),
                                        ("Bilby, shuffle the library", "mode"), ("play the song Kids by MGMT", "song")])
def test_r3_the_guard_leaves_real_play_requests_alone(heard, key):
    got = brain.music_grammar(heard)
    assert got is not None and key in got, (heard, got)


def test_r3_a_plain_stop_to_bilby_is_still_a_stop_and_not_a_search():
    assert brain.music_grammar("Bilby, music off") is None                            # (round 1's pin)
    assert _ran("Bilby, stop", True) == [("music", {"action": "pause"})] or _ran("Bilby, stop", True) == []


# ── "what can you actually do" ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("heard", ["what can you actually do", "what can you really do", "what can you even do", "what else are you able to do", "what else are you good at",
                                   "what are you good for", "what could you possibly do", "what do you actually do", "what do you know how to control", "what can you do for me",
                                   "what are your abilities", "what else can you do"])
def test_r3_an_ability_question_with_an_adverb_is_still_an_ability_question(heard):
    assert brain.ASKS_ABILITY.search(heard), heard


@pytest.mark.parametrize("heard", ["what do you know about my car", "what do you see in the car", "what are you seeing outside", "what do you hear from the garage", "what do you think about it",
                                   "what is my car's charge level right now", "how is my car doing"])
def test_r3_a_question_about_the_house_or_the_car_is_not_an_ability_question(heard):
    assert not brain.ASKS_ABILITY.search(heard), heard


# ── the music guard: only a request FOR MUSIC is an ask ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("heard, said", [("can I get a timer for ten minutes?", "Timer set. Music is now playing."), ("what about the weather?", "Playing some jazz for you."),
                                         ("could I have a reminder at five?", "Playing a chime on the speakers."), ("how about seven o'clock?", "Alarm set for seven. I started some music for you."),
                                         ("can I get a glass of water", "Playing some jazz.")])
def test_r3_a_false_music_claim_to_a_request_that_is_not_about_music_is_cut_not_asked_again(heard, said):
    assert musicguard.classify(heard, "", said, []) == ("retract", "claim"), (heard, said)


@pytest.mark.parametrize("heard", ["how about some jazz upstairs?", "can I get some metal on the Enclave?", "what about some jazz?", "do you mind pausing it?", "could I hear some jazz",
                                   "would you mind turning the music down?", "can you pause it"])
def test_r3_a_request_for_music_worded_as_a_question_is_still_asked_again_when_nothing_ran(heard):
    action, why = musicguard.classify(heard, "", "Jazz is now playing upstairs.", [])
    assert (action, why) == ("reask", "claim"), (heard, action, why)


# ── the alarm clock ──────────────────────────────────────────────────────────────────────────────────────────────────────

LAYOUT = house_layout.Layout({"floors": {"downstairs": {"rooms": ["theater"]}, "upstairs": {"rooms": ["living room", "bedroom"]}},
                              "rooms": {"theater": {"pods": ["theater"], "outputs": ["hdmi"]}, "living room": {"outputs": ["enclave"]}, "bedroom": {"pods": ["bedroom"]}}})


class _Router:
    def available(self, k):
        return True


def _at(h, mi, s=0):
    return time.mktime((2026, 11, 2, h, mi, s, 0, 0, -1))


@pytest.fixture
def quick_floor(monkeypatch):
    monkeypatch.setattr(alarm_clock, "FLOOR_WAIT_S", 0.3)


def _clock(tmp, now):
    said, posted = [], []

    def post(path, body):
        posted.append((path, body))
        return {"sent": True} if path == "/home/alert" else {}
    c = AlarmClock(tmp, {}, clock=lambda: now[0], log=lambda m: None, router=_Router(), post=post, layout=LAYOUT)

    async def play(kinds, clips, gain):
        return True
    c._play_outs = play

    async def voice(text):
        said.append(text)
        return b"mp3"
    c._voice = voice
    return c, said, posted


def _inject(c, now, kind, dt, label="", repeat=False, where="upstairs"):
    nid = max([x.id for x in c.alarms.items] + [0]) + 1
    due = now[0] + dt
    a = alarms_mod.Alarm(id=nid, kind=kind, due=due, hhmm=time.strftime("%H:%M", time.localtime(due)), days=list(alarms_mod.DAYS) if repeat else [], label=label, where=where, room="")
    c.alarms.items.append(a)
    return a


def test_r3_an_alarm_that_falls_due_in_the_tick_a_ring_gives_up_is_set_again_and_rings_a_minute_later(tmp_path, quick_floor):
    """Alarm 'gym' rings unanswered for 30 minutes; the loop stalls for a minute (a blocked disk, a clock step) and 'dentist' falls due at 06:29:40, during the stall: the next
    tick merges it into the ring and the ring gives up in the same tick. Round 2 closed it 'gave_up' unrung: a lost alarm."""
    now = [_at(6, 0)]

    async def go():
        c, said, _ = _clock(str(tmp_path), now)
        _inject(c, now, "alarm", 10, "gym")
        _inject(c, now, "alarm", 29 * 60 + 40, "dentist", where="everywhere")
        now[0] += 10
        await c.tick()
        for _ in range(28):
            now[0] += 60
            await c.tick()                                                         # 06:01:10 ... 06:29:10
        now[0] = _at(6, 30, 12)                                                    # the stall: no tick between 06:29:10 and 06:30:12
        await c.tick()
        assert sum("dentist" in s for s in said) == 0                              # not rung in the tick the ring gave up ...
        assert any(f["label"] == "gym" and f["ended"] == "gave_up" for f in c.fired)
        assert [(a.kind, a.label) for a in c.alarms.items] == [("alarm", "dentist")]       # ... but it is not lost: it is set again (a minute on)
        assert c.is_ringing()                                                      # (the morning briefing keeps quiet for it)
        now[0] += 61
        await c.tick()
        return c, said
    c, said = asyncio.run(go())
    assert sum("dentist" in s for s in said) == 1                                  # it rang, once
    assert c.ringing is not None and c.ringing["alarm"].label == "dentist"
    assert any(f["label"] == "dentist" for f in c.fired)


def test_r3_an_alarm_that_rode_a_ring_that_was_rung_is_not_set_again_when_the_ring_gives_up_later(tmp_path, quick_floor):
    now = [_at(6, 0)]

    async def go():
        c, said, _ = _clock(str(tmp_path), now)
        _inject(c, now, "alarm", 10, "gym")
        _inject(c, now, "alarm", 40, "dentist", where="everywhere")
        now[0] += 10
        await c.tick()
        now[0] += 31
        await c.tick()                                                             # 'dentist' joins the ring and is heard in it at once
        assert sum("dentist" in s for s in said) >= 1
        for _ in range(35):
            now[0] += 60
            await c.tick()                                                         # the ring gives up at 30 minutes
        return c, said
    c, said = asyncio.run(go())
    assert c.ringing is None
    assert not [a for a in c.alarms.items if a.label == "dentist"], "an alarm that was rung must not come back after the ring gives up"


class _Pod:
    def __init__(self, name, log):
        self.name, self.log = name, log
        self.voice = asyncio.Lock()

    async def announce(self, text):
        async with self.voice:
            self.log.append(("start", self.name, time.monotonic()))
            await asyncio.sleep(0.2)
            self.log.append(("end", self.name, time.monotonic()))

    async def ring(self, *a, **k):
        pass


def test_r3_timers_of_two_rooms_that_fall_due_together_are_said_one_after_the_other(tmp_path, monkeypatch):
    monkeypatch.setattr(alarm_clock, "FLOOR_WAIT_S", 1.0)

    async def go():
        now = [1_793_000_000.0]
        log = []
        c = AlarmClock(str(tmp_path), {}, clock=lambda: now[0], log=lambda m: None, router=None, post=lambda p, b: {})
        c.hub = NS(rooms={"kitchen": _Pod("kitchen", log), "office": _Pod("office", log)})
        c.timers_for("kitchen").add(10, "pasta")
        c.timers_for("office").add(10, "tea")
        now[0] += 11
        began = time.monotonic()
        await c.tick()
        ticked = time.monotonic() - began
        for _ in range(40):
            pending = [t for t in c._bg if not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
        return log, ticked
    log, ticked = asyncio.run(go())
    assert ticked < 0.15, "tick() waited for a timer's line (it must start it and go on)"
    assert sorted(n for k, n, _ in log if k == "start") == ["kitchen", "office"]
    open_, overlap = 0, False
    for k, n, t in sorted(log, key=lambda e: e[2]):
        open_ += 1 if k == "start" else -1
        overlap = overlap or open_ > 1
    assert not overlap, "two Skippys speaking at once: the second room's timer line began before the first one ended"


def test_r3_cancelling_the_leading_alarm_does_not_hand_the_ring_to_a_reminder_that_was_said_twice(tmp_path, quick_floor):
    """A repeating alarm leads with a reminder that has been said twice; the alarm is cancelled on his phone and a second reminder joined late: the second leads on, the first
    is not read out a third time."""
    now = [_at(6, 0)]

    async def go():
        c, said, _ = _clock(str(tmp_path), now)
        a = _inject(c, now, "alarm", 10, "gym", repeat=True)
        _inject(c, now, "reminder", 10, "take the pills")
        now[0] += 10
        await c.tick()
        now[0] += 60
        await c.tick()
        now[0] += 30
        _inject(c, now, "reminder", 1, "call mom")
        now[0] += 1
        await c.tick()
        assert c.ringing["alarm"].label == "gym"
        c.command({"op": "cancel", "alarm": a.id})
        assert c.ringing is not None and c.ringing["alarm"].label == "call mom", "a reminder already said twice must not lead on"
        for _ in range(6):
            now[0] += 60
            await c.tick()
        return said
    said = asyncio.run(go())
    assert sum("take the pills" in s for s in said) == 2
    assert sum("call mom" in s for s in said) >= 1


# ── a morning briefing asked for by hand ──────────────────────────────────────────────────────────────────────────────────

def _asked_by_hand(tmp_path, hm):
    from test_sat_daily import HOME, car_at, finish, make, stamp
    now = [stamp("2026-11-05", hm)]
    d, room, link, *_ = make(tmp_path, now, car=car_at(HOME))
    (tmp_path / "daily").mkdir(parents=True, exist_ok=True)
    (tmp_path / "daily" / "request.json").write_text('{"routine": "morning"}', encoding="utf-8")

    async def go():
        await d.tick(room)
        first = d.busy
        await finish(d, 15)
        marked = d.state.get("last_morning")
        now[0] += 30
        await d.tick(room)
        second = d.busy
        await finish(d, 15)
        return first, marked, second
    first, marked, second = asyncio.run(go())
    return first, marked, second, [s for s in link.spoken if s.startswith("Good morning, Captain.")]


@pytest.fixture(autouse=False)
def _plain_voice(monkeypatch):
    import hub
    monkeypatch.setattr(hub, "one_stream", lambda mp3, **k: mp3)


def test_r3_a_briefing_asked_for_by_hand_after_its_slot_marks_the_day_so_the_scheduled_one_does_not_say_it_twice(tmp_path, _plain_voice):
    first, marked, second, goods = _asked_by_hand(tmp_path, "08:30")
    assert first and marked == "2026-11-05" and not second
    assert len(goods) == 1, goods


def test_r3_a_test_run_before_the_slot_leaves_the_day_alone_so_the_scheduled_briefing_still_comes(tmp_path, _plain_voice):
    first, marked, second, goods = _asked_by_hand(tmp_path, "03:00")
    assert first and marked is None, (first, marked)
    assert len(goods) == 1, goods


def test_r3_after_eleven_nothing_is_marked_and_nothing_repeats(tmp_path, _plain_voice):
    first, marked, second, goods = _asked_by_hand(tmp_path, "11:30")
    assert first and not second
    assert len(goods) == 1, goods
