"""Speed of the house voice, the brain's part (the Captain, 2026-11-05: "streamline it so it becomes snappy like Amazon Echo").

Core's chat model (llama.cpp, a hybrid model) can reuse a cached prompt only up to the first word that changed ahead of the last ~500 tokens. The system prompt used to carry the
clock (to the minute), the room, the house audio and the news in its middle, so every minute and every room cost a cold 4-6 s turn (measured: 0.6-1.0 s when the text is the same).
This file pins the new shape. Every test here fails on the commit before it.

B1  the system prompt is the same text all day in every room; what changes rides in a note at the START of his message, which the stored history never keeps
B2  Brain.warm() sends the very prefix a turn sends, one token long, and never disturbs a reply
B3  a spoken answer is short: the prompt says so, the token limits keep it so, a cut one ends on a whole sentence, a stage direction is never spoken
B4  a routed tool that simply worked is confirmed from a closed bank of phrases, with no second model call (the hub pre-renders the bank into the TTS cache)
B5  "turn on the bedroom lamp" and "set a timer for five minutes" are routed before the model (it had said "Done." and switched nothing)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain as brain_mod  # noqa: E402
import house as house_mod  # noqa: E402
import news as news_mod  # noqa: E402
from brain import Brain  # noqa: E402
from timers import Timers  # noqa: E402

NOW_A = "Thursday, November 05, 2026, 14:45"
NOW_B = "Friday, November 06, 2026, 03:07"


def text(t):
    return NS(type="text", text=t)


def tool(name, **args):
    return NS(type="tool_use", id="tu_" + name, name=name, input=args)


class Model:
    """A scripted model that remembers every call it was asked. Asked more than it was told to answer, it raises (a routed command must never reach it)."""

    def __init__(self, *rounds, stop_reason=None):
        self.rounds, self.calls, self.stop_reason = list(rounds), [], stop_reason
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if not self.rounds:
            raise AssertionError("the model was asked, and this test says it must not be")
        return NS(content=self.rounds.pop(0), stop_reason=self.stop_reason)


def fake_tools(*names, results=None):
    """(extra, ran): tools that change nothing, with the answers the test gives them."""
    ran = []

    def run(name, args):
        ran.append((name, dict(args)))
        return (results or {}).get(name, f"({name} done)")
    return ([{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in names], run), ran


def speed_brain(*rounds, now=NOW_A, room="", stop_reason=None, **kw):
    clock = [1_793_000_000.0]
    timers = Timers(clock=lambda: clock[0])
    model = Model(*rounds, stop_reason=stop_reason)
    b = Brain(model, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: now, **kw)
    if room:
        b.room = room
    b.sleep = lambda s: None
    return b, model, timers, clock


def row(eid, name, state="off", **attrs):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": name, **attrs}}


ROWS = [row("light.bedroom", "Bedroom"), row("light.kitchen_under_1", "kitchen under 1"), row("light.kitchen_under_2", "kitchen under 2"), row("light.kitchen_under_3", "kitchen under 3"),
        row("light.track_stove_light", "track stove light"), row("light.living_track_middle", "living track middle"), row("light.living_track_throne", "living track throne"),
        row("light.deck_sliding_door", "Deck sliding door"), row("light.security_light", "security Light"), row("switch.coffee_maker", "Coffee Maker"),
        row("switch.space_heater", "Space Heater"), row("fan.bedroom_fan", "Bedroom Fan"), row("lock.front_door", "Front Door", "locked"),
        row("cover.garage_door", "Garage Door", "closed", device_class="garage"), row("sensor.bedroom_temperature", "Bedroom Temperature", "70")]


class FakeHouse(house_mod.House):
    """A real House (its names, its groups, its guard rails) over rows in memory; every service call is recorded."""

    def __init__(self, rows=None, areas=None):
        self.rows, self._areas, self.calls = list(ROWS if rows is None else rows), dict(areas or {}), []

    def states(self, fresh=False):
        return self.rows

    def areas(self):
        return self._areas

    def _service(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))


def house_brain(*rounds, room="", **kw):
    h = FakeHouse(kw.pop("rows", None), kw.pop("areas", None))
    b, model, timers, clock = speed_brain(*rounds, hass=h, room=room, **kw)
    return b, model, h, timers


# ── B1: one system prompt for the whole day, in every room ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def news_with(clock, *titles):
    n = news_mod.News({}, clock=lambda: clock[0])
    n.last = {"subject": "politics", "when": "day", "at": clock[0], "items": [{"title": t, "content": f"About {t}.", "source": "example.com", "date": ""} for t in titles]}
    return n


def test_speed_brain_the_system_prompt_is_byte_identical_for_any_minute_any_room_and_whatever_the_house_is_doing():
    a, _, _, clock_a = speed_brain(now=NOW_A, room="living")
    b, _, _, clock_b = speed_brain(now=NOW_B, room="bedroom")
    a.audio = NS(status=lambda: "Bilby is playing Gasoline by MDFMK on the Enclave upstairs.", playing=lambda: True)
    b.audio = NS(status=lambda: "Bilby is not playing anything.", playing=lambda: False)
    a.news, b.news = news_with(clock_a, "Senate passes bill"), news_mod.News({})
    a.offered = {"note": "Source: the harbour story", "at": clock_a[0]}
    assert a.system() == b.system()
    system = a.system()
    for volatile in ("14:45", "03:07", "November 05", "November 06", "talking to you from", "Right now in the house", "Gasoline", "harbour", "Senate passes"):
        assert volatile not in system, volatile
    assert "## The house" in system and "upstairs: living room" in system and "Enclave speakers" in system            # the floors and rooms are the same all day: they stay
    ctx_a, ctx_b = a.context(), b.context()
    assert ctx_a.startswith("[Right now: ") and ctx_a.endswith("]") and ctx_b.startswith("[Right now: ") and ctx_b.endswith("]")
    for part in (NOW_A, "talking to you from the living room (upstairs)", "Bilby is playing Gasoline by MDFMK on the Enclave upstairs.", "Source: the harbour story", "Senate passes bill"):
        assert part in ctx_a, part
    assert NOW_B in ctx_b and "talking to you from the bedroom (upstairs)" in ctx_b and "Bilby is not playing anything." in ctx_b
    assert NOW_A not in ctx_b and "Gasoline" not in ctx_b and "Senate" not in ctx_b and "harbour" not in ctx_b


def test_speed_brain_an_old_news_note_or_a_stale_offer_is_not_in_the_context_and_no_audio_means_no_status_line():
    b, _, _, clock = speed_brain(room="living")
    b.news = news_with(clock, "Senate passes bill")
    b.offered = {"note": "Source: the harbour story", "at": clock[0]}
    clock[0] += news_mod.KEEP_S + 1
    ctx = b.context()
    assert "Senate" not in ctx and "harbour" not in ctx and "Right now in the house" not in ctx and NOW_A in ctx


def test_speed_brain_the_prompt_tells_the_model_what_the_note_is_and_that_the_clock_line_is_the_time():
    extra, _ = fake_tools("play_music", "music")
    system = Brain(None, "m", None, persona="P", extra=extra).system()
    assert "[Right now:" in system and "NOT something he said" in system and "clock" in system and "answer any question about the time or date from it" in system
    assert "Bilby: the house's audio" in system and "at the end of this prompt" not in system                              # the audio lines are no longer at the end of the prompt


def test_speed_brain_every_model_call_of_a_turn_starts_the_user_message_with_the_note_and_the_history_keeps_the_plain_words():
    b, model, *_ = speed_brain([tool("list_timers")], [text("Nothing running, monkey.")], [text("Fine.")], room="living")
    assert b.reply("any timers") == "Nothing running, monkey."
    first = model.calls[0]["messages"][0]["content"]
    assert first.startswith("[Right now: ") and first.endswith("]\n\nany timers") and NOW_A in first
    assert model.calls[1]["messages"][0]["content"] == first                               # the second round of the same turn shares the whole prefix
    assert b.history == [{"role": "user", "content": "any timers"}, {"role": "assistant", "content": "Nothing running, monkey."}]
    b.now = lambda: NOW_B
    b.reply("and now")
    second = model.calls[2]
    assert second["system"] == model.calls[0]["system"]                                    # a new minute: the same system text
    kept = second["messages"][:4]                                                          # (fix:brain-core P2: the memory keeps the exchange as it happened, the call and its answer; b.history above is the plain words)
    assert kept[0] == {"role": "user", "content": "any timers"} and kept[3] == {"role": "assistant", "content": "Nothing running, monkey."}
    assert kept[1]["content"][0]["type"] == "tool_use" and kept[1]["content"][0]["name"] == "list_timers" and kept[2]["content"][0]["type"] == "tool_result"
    assert second["messages"][-1]["content"].startswith("[Right now: ") and NOW_B in second["messages"][-1]["content"] and second["messages"][-1]["content"].endswith("\n\nand now")


def test_speed_brain_a_routed_wording_call_a_second_try_and_a_text_call_carry_the_same_note():
    extra, ran = fake_tools("music")
    b, model, *_ = speed_brain([text("Quiet.")], extra=extra, room="living")
    assert b.reply("pause the music") == "Quiet."                                         # routed: the tool ran first, the model only words it
    sent = model.calls[0]["messages"][-1]["content"]
    assert sent.startswith("[Right now: ") and NOW_A in sent and "pause the music\n\n[The music tool's answer for this request: (music done)]" in sent and "tools" not in model.calls[0]
    b, model, *_ = speed_brain([text("Alarm set for seven.")], [text("It is not, I made that up.")], room="living")
    b.reply("wake me at seven")                                                            # a claim with no tool: asked again
    assert model.calls[1]["messages"][0]["content"].startswith("[Right now: ") and model.calls[1]["messages"][0]["content"].endswith("\n\nwake me at seven")
    b, model, *_ = speed_brain([text('[set_timer seconds="90" label="tea"]')], [text("Tea in ninety seconds.")], room="living")
    b.reply("tea timer, ninety seconds")                                                   # a call written as text, run, then worded
    assert model.calls[1]["messages"][0]["content"].startswith("[Right now: ") and "tools" not in model.calls[1]


def test_speed_brain_the_wording_call_of_a_routed_tool_sees_the_house_as_the_tool_left_it():
    state = {"playing": False}
    extra, ran = fake_tools("music", results={"music": "Done: pause on the house radio. (and a note that keeps this from being a plain success)"})
    b, model, *_ = speed_brain([text("Quiet.")], extra=extra, room="living")
    b.audio = NS(status=lambda: "Bilby is playing." if state["playing"] else "Bilby is paused.", playing=lambda: state["playing"])
    real = b.run_extra
    b.run_extra = lambda name, args: (state.update(playing=False), real(name, args))[1]
    state["playing"] = True
    b.reply("pause the music")
    sent = model.calls[0]["messages"][-1]["content"]
    assert "Bilby is paused." in sent and "Bilby is playing." not in sent                  # (the status is read after the tool ran, as the system prompt was before)


def test_speed_brain_mentat_asked_by_the_house_carries_the_note_too():
    from test_sat_physics import asked_from_memory
    b, client, _ = asked_from_memory("[Mentat] The escape velocity of Mars is 5.03 km/s. Source: NASA Mars Fact Sheet.", [text("Mentat says 11186 meters per second, from sigma-ground.")])
    b.reply("quick, how quickly must a rocket move to get free of Mars?")
    last = client.calls[1]["messages"][-1]["content"]
    assert last.startswith("[Right now: ") and "Mentat's answer: 11186 m/s." in last
    assert [m["content"] for m in b.history[-2:]][0] == "quick, how quickly must a rocket move to get free of Mars?"


# ── B2: Brain.warm() ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_speed_brain_warm_sends_the_prefix_a_turn_sends_one_token_long_and_counts_for_nothing():
    b, model, timers, clock = speed_brain([text("Hi.")], [text("ok")], [text("ok")], room="living")
    b.reply("hello")
    before = (list(b.actions), dict(b.timing), list(b.history), b._last, b.retries, b.guard)
    took = b.warm()
    assert isinstance(took, float) and took >= 0.0 and len(model.calls) == 3                 # the reply's, and the two shapes a turn sends
    sent = model.calls[1]
    assert sent["max_tokens"] == 1 and sent["model"] == "m" and sent["system"] == b.system() and sent["tools"] == b.tools()
    assert sent["messages"][:2] == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi."}] and sent["messages"][-1]["role"] == "user"
    assert len(sent["messages"]) == 3 and "[Right now" not in str(sent["messages"][:2])
    plain = model.calls[2]                                                                       # a routed command's wording call has no tools: Core keeps a prompt per shape
    assert plain["max_tokens"] == 1 and plain["system"] == sent["system"] and plain["messages"] == sent["messages"] and "tools" not in plain
    assert (list(b.actions), dict(b.timing), list(b.history), b._last, b.retries, b.guard) == before            # nothing a reply counts, and the quiet clock is not reset


def test_speed_brain_warm_stops_after_the_first_shape_when_a_reply_began_and_a_failing_second_shape_costs_nothing():
    b, model, *_ = speed_brain([text("ok")], [text("ok")])
    real = model.create

    def create(**kw):
        if len(model.calls) == 0:
            b._busy = True                                                                       # a reply begins while the first shape is being warmed
        return real(**kw)
    model.create = create
    assert b.warm() is not None and len(model.calls) == 1 and "tools" in model.calls[0]
    b._busy = False
    c, cmodel, *_ = speed_brain([text("ok")])                                                    # the second shape fails (the script has no second answer): the first one worked
    assert c.warm() is not None and len(cmodel.calls) == 2 and "tools" not in cmodel.calls[1]


def test_speed_brain_warm_sends_no_history_once_the_conversation_is_forgotten():
    b, model, timers, clock = speed_brain([text("Hi.")], [text("ok")])
    b.reply("hello")
    clock[0] += brain_mod.FORGET_AFTER_S + 1
    assert b.warm() is not None
    assert len(model.calls[-1]["messages"]) == 1 and b.history                             # the next reply forgets it; the warm-up must not do it for the reply
    fresh, model2, *_ = speed_brain([text("ok")])
    assert fresh.warm() is not None and len(model2.calls[0]["messages"]) == 1 and model2.calls[0]["system"] == fresh.system()


def test_speed_brain_warm_never_raises_and_says_none_when_it_could_not():
    class Down:
        def create(self, **kw):
            raise ConnectionError("skippy-core is away")
    down = Down()
    down.messages = down
    b, *_ = speed_brain()
    b.client = down
    assert b.warm() is None
    b.client = Model([text("x")])
    b.system = lambda: 1 / 0
    assert b.warm() is None and b.client.calls == []
    assert Brain(None, "m", None, persona="P").warm() is None                              # no client at all


def test_speed_brain_warm_leaves_a_running_reply_alone_and_the_flag_is_always_let_go():
    seen = {}
    b, model, *_ = speed_brain([text("Hi.")])
    real = model.create

    def create(**kw):
        seen["busy"], seen["warm"], seen["calls"] = b._busy, b.warm(), len(model.calls)
        return real(**kw)
    model.create = create
    assert b._busy is False
    b.reply("hello")
    assert seen == {"busy": True, "warm": None, "calls": 0} and len(model.calls) == 1 and b._busy is False        # a reply was running: warm() asked nothing
    boom, bmodel, *_ = speed_brain()

    def bad(**kw):
        raise ValueError("400 bad request")
    bmodel.create = bad
    with pytest.raises(ValueError):
        boom.reply("hello")
    assert boom._busy is False


# ── B3: short spoken replies ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_speed_brain_the_prompt_asks_for_one_or_two_short_sentences_a_few_words_to_confirm_and_no_stage_directions():
    system = Brain(None, "m", None, persona="PERSONA").system()
    assert "one or two short sentences" in system and "a few words" in system and "stage directions" in system and "*sighs*" in system
    assert "speaking aloud" in system and "no markdown" in system and "replacing his Alexa" in system and "WHOLE thing" in system          # what it said before still stands
    assert system.startswith("PERSONA")


def test_speed_brain_a_wording_call_after_a_routed_tool_is_160_tokens_and_the_tool_loop_300():
    extra, ran = fake_tools("music")
    b, model, *_ = speed_brain([text("Quiet.")], extra=extra)
    b.reply("pause the music")
    assert model.calls[0]["max_tokens"] == brain_mod.WORDING_TOKENS == 160
    b, model, *_ = speed_brain([tool("list_timers")], [text("Nothing running.")])
    b.reply("any timers")
    assert [c["max_tokens"] for c in model.calls] == [brain_mod.SPEAK_TOKENS, brain_mod.SPEAK_TOKENS] and brain_mod.SPEAK_TOKENS == 300      # the round that calls the tool keeps room for the call
    extra, ran = fake_tools("audiobook")
    b, model, *_ = speed_brain([text("Paused.")], extra=extra)
    b.pre_route = lambda heard: {"action": "pause"}
    b.reply("stop")
    assert model.calls[0]["max_tokens"] == 200                                             # (as before)


def test_speed_brain_the_other_calls_of_a_turn_are_bounded_too():
    from test_sat_news import Get, results
    b, model, *_ = speed_brain([text("Alarm set for seven.")], [tool("set_timer", seconds=60)], [text("A minute.")])
    b.reply("wake me at seven")
    assert [c["max_tokens"] for c in model.calls] == [300, 300, 300]                       # first reply, the second try, its wording
    b, model, *_ = speed_brain([text('[set_timer seconds="90" label="tea"]')], [text("Tea in ninety seconds.")])
    b.reply("tea timer, ninety seconds")
    assert [c["max_tokens"] for c in model.calls] == [300, 160]                            # the wording of a call written as text is a wording call
    nb, nmodel, *_ = speed_brain([text("Here is politics.")], news=news_mod.News({}, get=Get(results("Senate passes bill", "Governor signs law"))))
    nb.reply("what's the news")
    nb.reply("Politics.")
    assert nmodel.calls[0]["max_tokens"] == 300                                            # the news is talked through: its wording call keeps more room


def test_speed_brain_a_reply_the_model_ran_out_of_room_on_ends_on_a_whole_sentence():
    cut = "It is nine. Try to keep up. And now the rest of a ramble that never ev"
    b, model, *_ = speed_brain([text(cut)], stop_reason="max_tokens")
    assert b.reply("tell me something") == "It is nine. Try to keep up."
    b, model, *_ = speed_brain([text(cut)], stop_reason="end_turn")
    assert b.reply("tell me something") == cut                                             # a reply that simply ended is whatever it said
    b, model, *_ = speed_brain([text("no full sentence at all, only a start")], stop_reason="max_tokens")
    assert b.reply("tell me something") == "no full sentence at all, only a start"        # nothing better to cut to
    b, model, *_ = speed_brain([text("Done! Really? Yes. And then the")], stop_reason="length")
    assert b.reply("tell me something") == "Done! Really? Yes."
    assert brain_mod.whole_sentences('He said "Fine." and then') == 'He said "Fine."'


def test_speed_brain_the_note_is_never_read_out_even_when_the_model_copies_it():
    b, model, *_ = speed_brain([text("[Right now: Thursday, November 05, 2026, 14:45. He is talking to you from the living room (upstairs).]\n\nIt is a quarter to three, monkey.")], room="living")
    assert b.reply("what time is it") == "It is a quarter to three, monkey." and "Right now" not in b.history[-1]["content"]


def test_speed_brain_a_stage_direction_is_never_spoken_nor_kept_in_the_memory():
    b, model, *_ = speed_brain([text("*sighs* It is nine, monkey. [laughs] Try to keep up.")], [text("2*3*4 is 24, and *this* is not a stage direction? Yes it is.")])
    assert b.reply("what time is it") == "It is nine, monkey. Try to keep up."
    assert b.history[-1]["content"] == "It is nine, monkey. Try to keep up."
    assert b.reply("what is 2*3*4") == "2*3*4 is 24, and this is not a stage direction? Yes it is."      # (a product is not an action; and "*this*" is a stressed word, spoken without its asterisks: fix:brain-routes P4, it used to be deleted)
    assert brain_mod.strip_stage_directions('[SKIPPY_LEARN: {"a": 1}] kept') == '[SKIPPY_LEARN: {"a": 1}] kept'
    assert brain_mod.strip_stage_directions("*adjusts imaginary dial* There.") == "There."


# ── B4: instant confirmations ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_speed_brain_a_timer_is_set_and_confirmed_without_the_model():
    b, model, timers, clock = speed_brain()
    said = b.reply("set a timer for five minutes")
    assert said == "Timer set for five minutes." and model.calls == [] and timers.remaining() == [("5 minutes", 300)]
    assert b.timing["route"] == "set_timer" and b.timing["model_s"] == [] and [a["tool"] for a in b.actions] == ["set_timer"] and b.guard == (None, None)
    assert b.history == [{"role": "user", "content": "set a timer for five minutes"}, {"role": "assistant", "content": "Timer set for five minutes."}]
    assert b.reply("Skippy, set a pasta timer for ninety seconds.") == "Timer set for one minute thirty seconds." and ("pasta", 90) in timers.remaining() and model.calls == []
    assert b.reply("set a timer for an hour and a half") == "Timer set for one hour thirty minutes." and ("90 minutes", 5400) in timers.remaining() and model.calls == []


def test_speed_brain_the_timing_says_whether_the_prompt_was_warm_and_whether_the_reply_was_a_template():
    """The hub's timing line shows whatever short facts the brain's split holds besides route, model_s, tools_s and total_s (hub.TurnTiming): the log then says, per turn, how much of
    Core's prompt came from its cache, and which replies were a phrase of the closed bank."""
    b, model, *_ = speed_brain([text("Hi.")], [text("Hello again.")])
    real = model.create
    model.create = lambda **kw: NS(content=real(**kw).content, stop_reason=None, usage=NS(input_tokens=137, cache_read_input_tokens=7998))
    b.reply("hello")
    assert b.timing["cache_read"] == 7998 and b.timing["uncached"] == 137 and "template" not in b.timing
    b.reply("and again")
    assert b.timing["cache_read"] == 7998 and set(b.timing) == {"route", "model_s", "tools_s", "total_s", "cache_read", "uncached"}
    assert b.reply("set a timer for five minutes") == "Timer set for five minutes." and b.timing["template"] is True and "cache_read" not in b.timing        # (a new reply starts clean)
    plain, pmodel, *_ = speed_brain([text("Hi.")])
    plain.reply("hello")                                                                       # a reply without usage in it: no facts, and no error
    assert set(plain.timing) == {"route", "model_s", "tools_s", "total_s"}


def test_speed_brain_cancelling_timers_is_confirmed_without_the_model_and_nothing_to_cancel_goes_to_the_model():
    b, model, timers, clock = speed_brain([text("Nothing was running, monkey.")], [text("Which one, monkey?")])
    b.reply("set a pasta timer for ten minutes")
    b.reply("set a tea timer for three minutes")
    assert b.reply("cancel the pasta timer") == "Timer cancelled." and timers.remaining() == [("tea", 180)] and model.calls == []
    assert b.reply("cancel all timers") == "Timer cancelled." and timers.remaining() == [] and model.calls == []             # (one was left: one is cancelled)
    b.reply("set a timer for ten minutes")
    b.reply("set a timer for twenty minutes")
    assert b.reply("cancel all my timers") == "Timers cancelled." and timers.remaining() == [] and model.calls == []
    assert b.reply("cancel all timers") == "Nothing was running, monkey." and len(model.calls) == 1                 # "Cancelled 0 timers.": the model words that
    timers.add(600, "a")
    timers.add(600, "b")
    assert b.reply("cancel the timer") == "Which one, monkey?" and len(timers.remaining()) == 2                       # which one? two are running: the model asks


def test_speed_brain_a_light_is_switched_and_confirmed_without_the_model():
    b, model, h, timers = house_brain(room="bedroom")
    said = b.reply("turn on the bedroom lamp")
    assert said in brain_mod.ACKS and model.calls == [] and h.calls == [("light", "turn_on", {"entity_id": "light.bedroom"})]
    assert b.timing["route"] == "home_control" and b.timing["model_s"] == [] and b.actions[0]["tool"] == "home_control" and b.actions[0]["args"] == {"name": "bedroom lamp", "action": "on"}
    said = b.reply("turn the coffee maker off")
    assert said in brain_mod.ACKS and h.calls[-1] == ("switch", "turn_off", {"entity_id": "switch.coffee_maker"}) and model.calls == []
    assert b.history[-2:] == [{"role": "user", "content": "turn the coffee maker off"}, {"role": "assistant", "content": said}]


def test_speed_brain_all_the_lights_and_a_rooms_lights_are_one_call_each():
    b, model, h, timers = house_brain(room="living")
    assert b.reply("turn off all the lights") in brain_mod.ACKS
    assert sorted(c[2]["entity_id"] for c in h.calls) == sorted(r["entity_id"] for r in ROWS if r["entity_id"].startswith("light.")) and {c[1] for c in h.calls} == {"turn_off"} and model.calls == []
    h.calls.clear()
    assert b.reply("turn on the kitchen lights") in brain_mod.ACKS
    assert sorted(c[2]["entity_id"] for c in h.calls) == ["light.kitchen_under_1", "light.kitchen_under_2", "light.kitchen_under_3", "light.track_stove_light"] and model.calls == []
    h.calls.clear()
    assert b.reply("turn on the lights") in brain_mod.ACKS                                  # he is in the living room: its lights
    assert sorted(c[2]["entity_id"] for c in h.calls) == ["light.living_track_middle", "light.living_track_throne"] and model.calls == []


def test_speed_brain_music_that_worked_is_confirmed_without_the_model_and_every_other_answer_still_goes_to_it():
    answers = {"music": "Done: pause on the house radio."}
    extra, ran = fake_tools("music", "play_music", results=answers)
    b, model, *_ = speed_brain(extra=extra)
    assert b.reply("pause the music") in brain_mod.TRANSPORT_ACKS["pause"] and model.calls == [] and ran == [("music", {"action": "pause"})]
    answers["music"] = "Done: resume on the house radio."
    assert b.reply("resume") in brain_mod.TRANSPORT_ACKS["resume"] and model.calls == []
    answers["music"] = "Done: next on the house radio."
    assert b.reply("next song") in brain_mod.TRANSPORT_ACKS["next"] and model.calls == []
    answers["music"] = "Done: previous on the house radio."
    assert b.reply("previous track") in brain_mod.TRANSPORT_ACKS["previous"] and model.calls == []
    answers["music"] = "Done: the house stereo is at 6 of 10."
    assert b.reply("louder") == "Volume six." and model.calls == []
    answers["music"] = "Done: the house stereo is at 3 of 10."
    assert b.reply("volume three") == "Volume three." and model.calls == []
    for other in ("Done: resume on the house radio. But no speaker is on, so it waits where it was.", "(music done)", "The house radio is not answering right now.",
                  "Not louder: the house stereo is already at full (10 of 10); past that is the speaker's own knob.", "Playing on the house radio: Gasoline by MDFMK."):
        answers["music"] = other
        b2, m2, *_ = speed_brain([text("Words from the model.")], extra=extra)
        assert b2.reply("resume") == "Words from the model." and len(m2.calls) == 1, other


def test_speed_brain_a_failure_or_an_answer_that_says_more_than_done_still_goes_to_the_model():
    b, model, h, timers = house_brain([text("Unknown thing, monkey.")], room="bedroom")
    assert b.reply("turn on the flux capacitor") == "Unknown thing, monkey." and len(model.calls) == 1 and h.calls == []        # unknown: not routed at all
    # a group whose members did not all answer is more than "Done.": the model says which
    b, model, h, timers = house_brain([text("The kitchen under 2 did not answer, monkey.")], room="bedroom")
    real = h._service

    def flaky(domain, service, data):
        if data["entity_id"] == "light.kitchen_under_2":
            raise house_mod.HomeError("no answer")
        real(domain, service, data)
    h._service = flaky
    assert b.reply("turn off the kitchen lights") == "The kitchen under 2 did not answer, monkey." and len(model.calls) == 1
    assert "These did not answer: kitchen under 2." in model.calls[0]["messages"][-1]["content"]
    b, model, timers, clock = speed_brain([text("That is too long a timer.")])
    timers.add = lambda seconds, label="": (_ for _ in ()).throw(ValueError("a timer is between one second and a day"))
    assert b.reply("set a timer for five minutes") == "That is too long a timer." and "That did not work: a timer is between one second and a day" in model.calls[0]["messages"][-1]["content"]


def test_speed_brain_the_confirmations_rotate_in_order_and_come_from_a_closed_bank_the_hub_can_render_ahead():
    b, model, h, timers = house_brain(room="living")
    said = [b.reply(w) for w in ("turn on the coffee maker", "turn off the coffee maker") * 5]
    assert said == [brain_mod.ACKS[i % len(brain_mod.ACKS)] for i in range(10)] and len(set(brain_mod.ACKS)) == len(brain_mod.ACKS) >= 4
    assert all(len(a) <= 20 for a in brain_mod.ACKS)
    bank = brain_mod.fixed_phrases()
    assert len(bank) == len(set(bank)) and set(brain_mod.ACKS) <= set(bank)
    assert {"Timer set for five minutes.", "Timer set for ten minutes.", "Timer set for one minute thirty seconds.", "Timer set for one hour.", "Timers cancelled.", "Timer cancelled.",
            "Volume six.", "Volume ten."} <= set(bank)
    for phrases in brain_mod.TRANSPORT_ACKS.values():
        assert set(phrases) <= set(bank)
    b, model, timers, clock = speed_brain()
    for w in ("set a timer for 5 minutes", "set a timer for 45 minutes", "set a timer for 20 seconds", "set a timer for 2 hours", "cancel all timers"):
        assert b.reply(w) in bank, w
    assert all(isinstance(p, str) and p.strip() and "{" not in p for p in bank)
    assert b.fixed_phrases() == bank and b.ACKS == brain_mod.ACKS and brain_mod.fixed_phrases(timers=False) == bank[:26] and len(bank) == 97         # (asked of the module or of a brain)
    assert set(brain_mod.fixed_phrases(timers=False)) == set(brain_mod.ACKS) | {p for ps in brain_mod.TRANSPORT_ACKS.values() for p in ps} | set(brain_mod.CANCEL_ACKS) | {f"Volume {brain_mod.number_words(n)}." for n in range(1, 11)}


def test_speed_brain_a_reply_from_a_template_never_claims_more_than_the_tool_said():
    for phrase in brain_mod.ACKS:                                                          # it says "done", not what was done
        assert not set(re.findall(r"[a-z']+", phrase.lower())) & {"bedroom", "light", "lights", "lamp", "on", "off", "kitchen", "music", "timer", "playing", "started"}, phrase
    assert brain_mod.TRANSPORT_ACKS["pause"][0].lower().startswith("paus") and brain_mod.TRANSPORT_ACKS["resume"][0].lower().startswith("resum")
    assert all(set(p.lower().replace(".", "").split()) & {"paused", "pause", "okay"} for p in brain_mod.TRANSPORT_ACKS["pause"])


def test_speed_brain_the_guards_still_run_on_what_the_model_says():
    b, model, *_ = speed_brain([text("Alarm set for 14:05.")], [text("It is not, I made that up.")])
    assert b.reply("wake me at seven") == "It is not, I made that up." and "NOTHING was done" in str(model.calls[1]["messages"])         # CLAIMED
    extra, ran = fake_tools("play_music", "music", "speakers")
    b, model, *_ = speed_brain([text("Consider it done. Playing your heavy metal library right now.")], [text("I did it, honest.")], extra=extra)
    assert b.reply("Skippy, put something loud on, get some heavy metal going for me") == brain_mod.UNCLAIM_MUSIC and ran == []                    # musicguard


# ── B5: the routes ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def devices():
    return Brain(None, "m", None, hass=FakeHouse())._house_devices()


def test_speed_brain_the_house_devices_are_the_lights_plugs_and_fans_he_may_switch_with_their_rooms(devices):
    by_id = {d["id"]: d for d in devices}
    assert by_id["light.bedroom"] == {"id": "light.bedroom", "name": "Bedroom", "room": "bedroom", "kind": "light"}
    assert by_id["fan.bedroom_fan"]["kind"] == "fan" and by_id["fan.bedroom_fan"]["room"] == "bedroom" and by_id["switch.coffee_maker"] == {"id": "switch.coffee_maker", "name": "Coffee Maker", "room": "", "kind": "switch"}
    assert by_id["light.kitchen_under_1"]["room"] == "kitchen" and by_id["light.living_track_middle"]["room"] == "living room"
    assert not {"lock.front_door", "cover.garage_door", "sensor.bedroom_temperature"} & set(by_id)                       # locks, doors and sensors are not his to switch here
    assert Brain(None, "m", None)._house_devices() == [] and Brain(None, "m", None, hass=object())._house_devices() == []       # no house, or one that cannot say: nothing, never an error


LIGHT_YES = [
    ("turn on the bedroom lamp", ("bedroom lamp", "on")), ("Skippy, turn off the bedroom lamp.", ("bedroom lamp", "off")), ("turn the bedroom lamp off", ("bedroom lamp", "off")),
    ("switch the bedroom lamp on", ("bedroom lamp", "on")), ("switch off the bedroom light", ("bedroom light", "off")), ("bedroom lamp on", ("bedroom lamp", "on")),
    ("bedroom lights off", ("bedroom lights", "off")), ("put on the bedroom lamp", ("bedroom lamp", "on")), ("put the bedroom lamp off", ("bedroom lamp", "off")),
    ("turn out the bedroom light", ("bedroom light", "off")), ("please turn on the bedroom lamp", ("bedroom lamp", "on")), ("can you turn on the bedroom lamp", ("bedroom lamp", "on")),
    ("could you please turn off the bedroom lamp", ("bedroom lamp", "off")), ("turn on the bedroom lamp please", ("bedroom lamp", "on")), ("Turn ON the Bedroom Lamp!", ("bedroom lamp", "on")),
    ("power off the bedroom lamp", ("bedroom lamp", "off")), ("shut off the bedroom light", ("bedroom light", "off")), ("kill the bedroom lights", ("bedroom lights", "off")),
    ("turn on the lamp in the bedroom", ("bedroom lamp", "on")), ("turn off all the lights", ("all lights", "off")), ("turn on all lights", ("all lights", "on")),
    ("all the lights off", ("all lights", "off")), ("turn all of the lights on", ("all lights", "on")), ("turn off every light in the house", ("the house light", "off")),
    ("turn off the lights everywhere", ("everywhere lights", "off")), ("turn off the kitchen lights", ("kitchen lights", "off")), ("turn on the upstairs lights", ("upstairs lights", "on")),
    ("turn off the lights upstairs", ("upstairs lights", "off")), ("turn on all the lights in the kitchen", ("kitchen lights", "on")),
    ("turn off the coffee maker", ("Coffee Maker", "off")), ("turn on the bedroom fan", ("Bedroom Fan", "on")), ("turn on the living track middle", ("living track middle", "on")),
    ("turn on kitchen under one", ("kitchen under 1", "on")), ("kitchen under 2 off", ("kitchen under 2", "off")), ("turn on the bedroom", ("Bedroom", "on")),
    ("turn the coffee maker on", ("Coffee Maker", "on")), ("switch on the coffee maker", ("Coffee Maker", "on")), ("turn the lights off in the bedroom", ("bedroom lights", "off")),
    ("turn the lamp on in the bedroom", ("bedroom lamp", "on")), ("turn the lights off everywhere", ("everywhere lights", "off")), ("turn the lights on upstairs", ("upstairs lights", "on")),
    ("Skippy, switch the kitchen lights off please", ("kitchen lights", "off")),
]
LIGHT_NO = ["turn on the music", "turn off the stereo", "turn the volume up", "what lights are on", "is the bedroom lamp on", "are the lights on", "turn it off", "turn them on",
            "turn on the thing", "turn on the lights and play jazz", "turn off the lights in five minutes", "turn off the lights when I leave", "turn on the bedroom lamp at seven",
            "turn up the lights", "dim the bedroom lamp", "turn the bedroom lamp to fifty percent", "turn on the space heater", "turn on the oven", "turn off the garage light",
            "turn on the security light", "turn on the deck sliding door light", "turn the heat off", "turn off the dryer", "switch on the iron", "turn on the fireplace",
            "unlock the front door", "turn on the flux capacitor", "turn on the guest room lamp", "turn on the lamp", "turn on the lights", "turn off the kitchen", "turn on the track stove light",
            "turn on the radio", "turn off the thermostat", "I want the lamp on", "tell me a joke", "", "set a timer for five minutes", "turn on that light", "turn off the bedroom lamp and the fan",
            "turn off the lights downstairs",               # (this house lists no light downstairs: nothing to switch, so the model says so)
            "turn the lamp on in five minutes", "turn the lights off at seven", "turn the bedroom lamp on and off", "what's going on", "go on", "carry on", "keep it on", "is it on", "who is on"]


@pytest.mark.parametrize("heard, want", LIGHT_YES)
def test_speed_brain_light_route_understands_how_he_says_it(heard, want, devices):
    assert brain_mod.light_route(heard, devices) == ("home_control", {"name": want[0], "action": want[1]}), heard


@pytest.mark.parametrize("heard", LIGHT_NO)
def test_speed_brain_light_route_leaves_everything_else_to_the_model(heard, devices):
    assert brain_mod.light_route(heard, devices) is None, heard


def test_speed_brain_light_route_prefers_the_room_he_is_in_and_a_bare_kind_means_that_rooms_own():
    r = brain_mod.light_route
    d = [{"id": "light.a", "name": "Reading Lamp", "room": "bedroom", "kind": "light"}, {"id": "light.b", "name": "Reading Light", "room": "living room", "kind": "light"},
         {"id": "light.c", "name": "Sofa Lamp", "room": "living room", "kind": "light"}]
    assert r("turn on the reading", d) is None                                                # two readings: without a room he is in, the model asks
    assert r("turn on the reading", d, here="bedroom") == ("home_control", {"name": "Reading Lamp", "action": "on"})
    assert r("turn on the reading", d, here="living room") == ("home_control", {"name": "Reading Light", "action": "on"})
    assert r("turn on the reading", d, here="kitchen") is None                                # neither is in his room: no guess
    assert r("turn on the reading lamp", d) == ("home_control", {"name": "Reading Lamp", "action": "on"})                    # its own name: no room needed
    assert r("turn off the lights", d, here="living room") == ("home_control", {"name": "living room lights", "action": "off"})
    assert r("turn off the lamp", d, here="bedroom") == ("home_control", {"name": "bedroom lamp", "action": "off"})
    assert r("turn off the lights", d, here="office") is None and r("turn off the lights", d) is None
    twin = [{"id": "light.x", "name": "Desk Lamp", "room": "Office", "kind": "light"}, {"id": "light.y", "name": "Desk Lamp", "room": "Study", "kind": "light"}]
    assert r("turn on the desk lamp", twin) is None                                          # the same name twice: the house itself would not know which
    assert r("turn on the desk lamp", twin, here="office") == ("home_control", {"name": "Desk Lamp Office", "action": "on"})        # ... unless he is in one of the rooms: the name and the room
    assert r("turn on the desk lamp", twin, here="kitchen") is None


def test_speed_brain_light_route_never_switches_what_can_hurt_even_when_the_house_names_it(devices):
    r = brain_mod.light_route
    risky = devices + [{"id": "switch.kettle", "name": "Kettle", "room": "kitchen", "kind": "switch"}, {"id": "switch.dryer", "name": "Dryer plug", "room": "", "kind": "switch"},
                       {"id": "fan.heater", "name": "Heater Fan", "room": "", "kind": "fan"}, {"id": "switch.grill", "name": "Patio grill", "room": "", "kind": "switch"}]
    for heard in ("turn on the kettle", "turn on the dryer plug", "turn on the heater fan", "turn on the patio grill", "turn on all the plugs", "turn off the fireplace", "turn on the iron",
                  "switch on the space heater", "turn off the burners", "turn on the oven light", "turn on the garage door light", "turn off the gate light", "turn on the alarm system"):
        assert r(heard, risky) is None, heard
    assert r("turn on the coffee maker", risky) == ("home_control", {"name": "Coffee Maker", "action": "on"})
    assert r("turn on all the lights", risky) == ("home_control", {"name": "all lights", "action": "on"})                     # lights are lights, whatever they are called


def test_speed_brain_light_route_reads_the_devices_lazily_and_never_raises():
    calls = []

    def lazy():
        calls.append(1)
        return [{"id": "light.bedroom", "name": "Bedroom", "room": "bedroom", "kind": "light"}]
    for idle in ("what time is it", "what's going on", "go on", "carry on", "keep it on", "is it on", "who is on", "i left it on", "turn them off", "turn it on", "is the bedroom lamp on",
                 "don't turn the lights off", "leave the lights on", "keep the lights on", "never turn the lamp off"):
        assert brain_mod.light_route(idle, lazy) is None and calls == [], idle                      # not a switching sentence, or one about no thing: the house is not even asked
    assert brain_mod.light_route("turn on the bedroom lamp", lazy) == ("home_control", {"name": "bedroom lamp", "action": "on"}) and calls == [1]

    def broken():
        raise RuntimeError("the house is away")
    assert brain_mod.light_route("turn on the bedroom lamp", broken) is None and brain_mod.light_route("turn on the bedroom lamp", []) is None
    assert brain_mod.light_route(None, []) is None


def test_speed_brain_the_light_route_runs_through_the_real_house_and_leaves_what_the_house_cannot_settle_to_the_model():
    b, model, h, timers = house_brain([text("Which one, monkey?")], room="bedroom")
    assert b.reply("turn on the bedroom lamp") in brain_mod.ACKS and h.calls == [("light", "turn_on", {"entity_id": "light.bedroom"})] and model.calls == []
    # two things with the same name: the house would answer "That could be ...", and the route does not even try
    twins = [row("light.desk_a", "Desk Lamp"), row("light.desk_b", "Desk Lamp")]
    b, model, h, timers = house_brain([text("Which one, monkey?")], rows=twins, areas={"light.desk_a": "Study", "light.desk_b": "Office"})
    assert b.reply("turn on the desk lamp") == "Which one, monkey?" and h.calls == [] and len(model.calls) == 1 and "tools" in model.calls[0]
    # ... but in a room that one of them is in, the name and the room settle it, and the house agrees
    b, model, h, timers = house_brain(rows=twins, areas={"light.desk_a": "Study", "light.desk_b": "Office"}, room="office")
    assert b.reply("turn on the desk lamp") in brain_mod.ACKS and h.calls == [("light", "turn_on", {"entity_id": "light.desk_b"})] and model.calls == []

    class Stub:                                                                             # a house that cannot say what it has (a stub): the model decides, as before
        def devices(self, *a): return "Bedroom lamp: off."
        def control(self, *a): return "Done."
    own_call = NS(type="tool_use", id="tu_home_control", name="home_control", input={"name": "bedroom lamp", "action": "on"})      # (`tool()` takes `name` for itself)
    b, model, timers, clock = speed_brain([own_call], [text("Done.")], hass=Stub())      # (fix:brain-core P1: the model calls the tool itself; a bare "Done." with no call is asked again)
    assert b.reply("turn on the bedroom lamp") == "Done." and len(model.calls) == 2 and "tools" in model.calls[0] and [a["tool"] for a in b.actions] == ["home_control"]
    h2 = FakeHouse()                                                                        # a sensitive house: a lock or the garage door is not in the route's list, whatever the flag says
    h2.sensitive = True
    b, model, timers, clock = speed_brain([text("That needs your yes aloud, monkey.")], hass=h2)
    assert b.reply("turn on the front door") == "That needs your yes aloud, monkey." and h2.calls == []


DURATIONS = [("five minutes", 300), ("5 minutes", 300), ("a minute", 60), ("an hour", 3600), ("half an hour", 1800), ("half a minute", 30), ("an hour and a half", 5400), ("a minute and a half", 90),
             ("one and a half hours", 5400), ("two and a half minutes", 150), ("90 seconds", 90), ("ninety seconds", 90), ("twenty five minutes", 1500), ("twenty-five minutes", 1500),
             ("1 hour 30 minutes", 5400), ("one hour and fifteen minutes", 4500), ("2 minutes 30 seconds", 150), ("a quarter of an hour", 900), ("a couple of minutes", 120),
             ("one hundred twenty seconds", 120), ("a hundred seconds", 100), ("45 secs", 45), ("10 min", 600), ("3 hrs", 10800), ("1.5 hours", 5400), ("eighteen minutes", 1080), ("an hour and 10 minutes", 4200)]


@pytest.mark.parametrize("said, seconds", DURATIONS)
def test_speed_brain_a_length_of_time_is_read_the_way_he_says_it(said, seconds):
    assert brain_mod.parse_duration(said) == seconds
    assert brain_mod.parse_duration(f"set a timer for {said} please") == seconds


def test_speed_brain_a_length_of_time_that_is_not_one_is_none():
    for said in ("a while", "five", "soon", "tomorrow", "zero minutes", "the minute hand", "a bit", "", "ten past five", "a few minutes"):
        assert brain_mod.parse_duration(said) is None, said


TIMER_YES = [
    ("set a timer for five minutes", {"seconds": 300}), ("Skippy, set a timer for 5 minutes.", {"seconds": 300}), ("set a timer for ten minutes", {"seconds": 600}),
    ("set a five minute timer", {"seconds": 300}), ("set a 5 minute timer", {"seconds": 300}), ("set a 10-minute timer", {"seconds": 600}), ("set a pasta timer for ten minutes", {"seconds": 600, "label": "pasta"}),
    ("set a ten minute pasta timer", {"seconds": 600, "label": "pasta"}), ("start a timer for 90 seconds", {"seconds": 90}), ("set a timer for an hour and a half", {"seconds": 5400}),
    ("set a timer for half an hour", {"seconds": 1800}), ("set a timer for 1 hour and 15 minutes", {"seconds": 4500}), ("timer for twenty five minutes", {"seconds": 1500}),
    ("ten minute timer", {"seconds": 600}), ("a 20 minute timer", {"seconds": 1200}), ("can you set a timer for ten minutes", {"seconds": 600}), ("please set a timer for 3 minutes", {"seconds": 180}),
    ("set a timer for ten minutes for the eggs", {"seconds": 600, "label": "eggs"}), ("set a timer for twenty seconds", {"seconds": 20}), ("make me a timer for 5 minutes", {"seconds": 300}),
    ("set the timer for fifteen minutes", {"seconds": 900}), ("set a timer for 5 minutes now", {"seconds": 300}), ("start a 3 minute egg timer", {"seconds": 180, "label": "egg"}),
]
TIMER_NO = ["I want a five minute timer", "set a timer", "set a timer for a while", "set a timer for 2 days", "set a timer for 0 minutes", "how long is left on the timer", "what timers do I have",
            "remind me in ten minutes", "set an alarm for seven", "set a timer for five minutes and play jazz", "is there a timer running", "can I get a timer for ten minutes", "tea timer, ninety seconds",
            "a tea timer", "wake me in five minutes", "do you know how to set a timer for ten minutes", "set a timer for 25 hours", "the timer for five minutes is done",
            "what is a five minute timer", "play a five minute song"]


@pytest.mark.parametrize("heard, args", TIMER_YES)
def test_speed_brain_timer_route_understands_how_he_says_it(heard, args):
    assert brain_mod.timer_route(heard) == ("set_timer", args), heard


@pytest.mark.parametrize("heard", TIMER_NO)
def test_speed_brain_timer_route_leaves_everything_else_to_the_model(heard):
    assert brain_mod.timer_route(heard) is None, heard


def test_speed_brain_cancelling_is_routed_only_when_it_is_clear_which_timers():
    r = brain_mod.timer_route
    for heard in ("cancel all timers", "cancel all my timers", "cancel the timers", "cancel my timers", "clear all timers", "delete all the timers", "Skippy, cancel all timers please"):
        assert r(heard, 5) == ("cancel_timers", {"label": ""}), heard
    assert r("cancel the pasta timer", 5) == ("cancel_timers", {"label": "pasta"}) and r("cancel my tea timer", 2) == ("cancel_timers", {"label": "tea"})
    assert r("cancel the timer", 1) == ("cancel_timers", {"label": ""}) and r("cancel the timer", 0) == ("cancel_timers", {"label": ""}) and r("cancel the timer") == ("cancel_timers", {"label": ""})
    assert r("cancel the timer", 2) is None and r("cancel my timer", lambda: 3) is None and r("cancel my timer", lambda: 1) == ("cancel_timers", {"label": ""})
    for heard in ("do you know how to cancel all my timers", "cancel the alarm", "cancel all alarms", "stop the timer", "cancel", "how do I cancel a timer", "I want to cancel the timer"):
        assert r(heard, 1) is None, heard


def test_speed_brain_number_words_and_durations_are_said_the_way_a_person_says_them():
    n, d = brain_mod.number_words, brain_mod.say_duration_words
    assert [n(i) for i in (0, 1, 5, 11, 20, 21, 45, 59, 99)] == ["zero", "one", "five", "eleven", "twenty", "twenty one", "forty five", "fifty nine", "ninety nine"]
    assert d(1) == "one second" and d(30) == "thirty seconds" and d(60) == "one minute" and d(90) == "one minute thirty seconds" and d(300) == "five minutes"
    assert d(3600) == "one hour" and d(5400) == "one hour thirty minutes" and d(7200) == "two hours" and d(86400) == "twenty four hours"
    assert brain_mod.say_duration(300) == "5 minutes"                                          # (the written form the tools return is as it was)


def test_speed_brain_routed_commands_cost_no_model_call_and_everything_else_costs_what_it_did():
    extra, ran = fake_tools("music", "play_music", results={"music": "Done: pause on the house radio."})
    cases = (("set a timer for five minutes", 0), ("turn on the bedroom lamp", 0), ("cancel all timers", 0), ("pause the music", 0), ("tell me a joke", 1), ("what is the capital of France", 1))
    for heard, calls in cases:
        b, model, h, timers = house_brain([text("Paris, obviously.")], room="bedroom", extra=extra)
        timers.add(60, "tea")
        b.reply(heard)
        assert len(model.calls) == calls, heard
        if calls:
            assert "tools" in model.calls[0] and model.calls[0]["messages"][-1]["content"].endswith(heard)
