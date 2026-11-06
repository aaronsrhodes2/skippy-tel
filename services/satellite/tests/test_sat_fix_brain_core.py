"""fix:brain-core: "it said Done and did nothing" (P1, P2 of the review of the house speed change, 2026-11-05), the warm-up race (P5, the brain's half) and the routed wording's annotation (P12a).

Measured on the real Core with 8 phrasings no route takes ("I want the living room lights off", "I need a pasta timer for ten minutes", "could you skip to the next track" ...): the model called
the right tool for 21 of 24 in a fresh conversation, but after four routed commands in its memory for 1 of 8, after one earlier turn for 0 of 8, after two chat turns for 1 of 8: a memory of
words teaches words. Every test here fails on the brain before the fix.

P2  the memory keeps the REAL exchange (the tool call and its answer), cut by whole exchanges, the plain words are a view of it; the note is never in it
P1  one constant example of a tool doing what he asked heads every call (and warm()'s, which builds the same prefix through the same helper); the prompt says a thing is done by calling its tool;
    a reply that only acknowledges an order, with no tool called, is asked again with the tools
P5  a reply that begins while the prompt is being warmed waits for the warm-up (bounded) and says so in its timing
P12 [SKIPPY_LEARN: ...] and [TTS_ADJUST: ...] are not read aloud after a routed command
"""
from __future__ import annotations

import copy
import threading
import time
from types import SimpleNamespace as NS

import pytest

import brain as brain_mod
import house as house_mod
from brain import Brain
from timers import Timers

NOW = "Thursday, November 05, 2026, 14:45"


def text(t):
    return NS(type="text", text=t)


def call(tool_name, /, cid=None, **args):
    return NS(type="tool_use", id=cid or "tu_" + tool_name, name=tool_name, input=args)


class Model:
    """A scripted model. Every call is kept, the messages as they were when it was made (the reply goes on to append to its own list)."""

    def __init__(self, *rounds):
        self.rounds, self.calls = list(rounds), []
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": copy.deepcopy(kw["messages"])})
        if not self.rounds:
            raise AssertionError("the model was asked more often than this test says")
        return NS(content=self.rounds.pop(0), stop_reason=None)


def row(eid, name, state="off", **attrs):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": name, **attrs}}


class FakeHouse(house_mod.House):
    def __init__(self):
        self.rows, self.calls = [row("light.bedroom", "Bedroom"), row("light.kitchen_under_1", "kitchen under 1"), row("switch.coffee_maker", "Coffee Maker")], []

    def states(self, fresh=False):
        return self.rows

    def areas(self):
        return {}

    def _service(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))


PROPS = {"music": {"action": {"type": "string"}, "level": {"type": "integer"}}}               # (the hub's music tool has these; the other fakes take none)


def fake_tools(*names, results=None):
    ran = []

    def run(name, args):
        ran.append((name, dict(args)))
        return (results or {}).get(name, f"({name} done)")
    return ([{"name": n, "description": n, "input_schema": {"type": "object", "properties": PROPS.get(n, {})}} for n in names], run), ran


def mk(*rounds, hass=None, room="", extra=None, **kw):
    clock = [1_793_000_000.0]
    model = Model(*rounds)
    b = Brain(model, "m", Timers(clock=lambda: clock[0]), persona="PERSONA", clock=lambda: clock[0], now=lambda: NOW, hass=hass, extra=extra, **kw)
    if room:
        b.room = room
    b.sleep = lambda s: None
    return b, model, clock


@pytest.fixture
def no_routes(monkeypatch):
    """The routes are another part's business: these phrasings reach the model whatever they take."""
    for name in ("light_route", "timer_route", "music_route", "speakers_route", "device_route", "bilby_route", "announce_route"):
        if hasattr(brain_mod, name):
            monkeypatch.setattr(brain_mod, name, lambda *a, **k: None)
    for module in (brain_mod.lists_mod, brain_mod.alarms_mod):
        monkeypatch.setattr(module, "pre_route", lambda *a, **k: None)


def valid(messages):
    """What the API accepts: roles alternate from a user message; every tool_use is answered by a tool_result with the same ids in the very next message; no tool_result without its tool_use;
    every tool_use id is new."""
    seen, pending = set(), None
    for i, m in enumerate(messages):
        assert m["role"] == ("user" if i % 2 == 0 else "assistant"), (i, m["role"])
        blocks = m["content"] if isinstance(m["content"], list) else []
        uses = [b["id"] for b in blocks if b.get("type") == "tool_use"]
        results = [b["tool_use_id"] for b in blocks if b.get("type") == "tool_result"]
        if m["role"] == "assistant":
            assert pending is None, "a tool_use was not answered"
            assert not set(uses) & seen, "a tool_use id twice"
            seen |= set(uses)
            pending = set(uses) or None
            assert not results
        else:
            assert (set(results) == pending) if pending is not None else not results, (i, results, pending)
            pending = None
    assert pending is None
    return True


def stored(b):
    """Everything the memory holds, as one list of messages."""
    return b._window()


# ── P2: the memory keeps what was done, not just what was said ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix_brain_core_a_routed_command_is_remembered_with_the_tool_call_that_ran_and_the_plain_words_stay_plain():
    b, model, _ = mk([NS(type="text", text="Hi.")])
    assert b.reply("set a timer for five minutes") == "Timer set for five minutes." and model.calls == []
    assert b.history == [{"role": "user", "content": "set a timer for five minutes"}, {"role": "assistant", "content": "Timer set for five minutes."}]       # what every reader of history sees
    kept = stored(b)
    assert [m["role"] for m in kept] == ["user", "assistant", "user", "assistant"] and valid(kept)
    use, result = kept[1]["content"][0], kept[2]["content"][0]
    assert use["type"] == "tool_use" and use["name"] == "set_timer" and use["input"]["seconds"] == 300 and result["type"] == "tool_result" and result["tool_use_id"] == use["id"]
    assert result["content"].startswith("Timer set") and kept[3] == {"role": "assistant", "content": "Timer set for five minutes."}
    b.reply("hello")
    sent = model.calls[0]["messages"]
    assert sent[:4] == kept and sent[4]["content"].endswith("\n\nhello") and valid(sent)                  # the next turn shows the model that the command was done by calling a tool


def test_fix_brain_core_a_model_turn_that_ran_tools_keeps_the_calls_it_used_and_not_the_first_try_that_was_taken_back():
    b, model, _ = mk([NS(type="text", text="Alarm set for seven.")], [call("set_timer", seconds=60, label="tea")], [NS(type="text", text="A minute, monkey.")], [NS(type="text", text="Fine.")])
    assert b.reply("wake me when the tea is done") == "A minute, monkey." and [a["tool"] for a in b.actions] == ["set_timer"]         # claimed, asked again, the tool ran
    kept = stored(b)
    assert [m["role"] for m in kept] == ["user", "assistant", "user", "assistant"] and valid(kept)
    assert kept[0]["content"] == "wake me when the tea is done" and kept[1]["content"][0]["name"] == "set_timer" and kept[1]["content"][0]["input"] == {"seconds": 60, "label": "tea"}
    assert kept[3]["content"] == "A minute, monkey."
    assert "NOTHING was done" not in str(kept) and "Alarm set for seven" not in str(kept)                  # the claim and the house's nudge are not memory
    b.reply("thanks")
    assert model.calls[3]["messages"][:4] == kept


def test_fix_brain_core_a_text_call_and_a_mentat_asked_by_the_house_are_remembered_as_the_calls_they_were(no_routes):
    b, model, _ = mk([NS(type="text", text='[set_timer seconds="90" label="tea"]')], [NS(type="text", text="Tea in ninety seconds.")])
    assert b.reply("tea timer, ninety seconds") == "Tea in ninety seconds."
    kept = stored(b)
    assert valid(kept) and kept[1]["content"][0]["name"] == "set_timer" and kept[1]["content"][0]["input"] == {"seconds": 90, "label": "tea"} and kept[3]["content"] == "Tea in ninety seconds."
    assert "[set_timer" not in str(kept)                                                                   # a call written as text is never the memory (it taught the model to write calls as text)
    from test_sat_physics import ESCAPE, asked_from_memory
    heard = "quick, how quickly must a rocket move to get free of Mars?"
    p, client, _ = asked_from_memory("[Mentat] The escape velocity of Mars is 5.03 km/s. Source: NASA Mars Fact Sheet.", [text("Mentat says 11186 meters per second, from sigma-ground.")])
    assert p.reply(heard) == "Mentat says 11186 meters per second, from sigma-ground."
    kept = stored(p)
    assert valid(kept) and kept[1]["content"][0]["name"] == "ask_mentat" and kept[1]["content"][0]["input"] == {"question": heard} and "11186" in kept[2]["content"][0]["content"]
    assert "5.03" not in str(kept) and ESCAPE                                                              # (the figure from memory was never the memory)


def test_fix_brain_core_the_window_is_whole_exchanges_and_stays_bounded(no_routes):
    extra, ran = fake_tools("music", results={"music": "Done: next on the house radio."})
    rounds = []
    for i in range(12):
        rounds += [[call("list_timers", cid=f"c{i}")], [NS(type="text", text=f"none {i}")]]
    b, model, _ = mk(*rounds, extra=extra)
    for i in range(12):
        b.reply(f"any timers {i}")
    kept = stored(b)
    assert len(b._turns) == brain_mod.MEMORY_TURNS == 8 and len(kept) == 4 * 8 and valid(kept)           # never a tool_result without its tool_use, never a cut mid-exchange
    assert kept[0] == {"role": "user", "content": "any timers 4"} and kept[-1] == {"role": "assistant", "content": "none 11"}
    assert len(b.history) == 2 * 8 and b.history[0]["content"] == "any timers 4"                           # the plain view is the same exchanges
    assert len({m["content"][0]["id"] for m in kept if m["role"] == "assistant" and isinstance(m["content"], list)}) == 8      # every call has an id of its own, turn after turn


def test_fix_brain_core_what_a_tool_answered_is_cut_in_the_memory_but_the_model_read_all_of_it_in_its_own_turn(no_routes):
    extra, ran = fake_tools("music", results={"music": "x" * 5000})
    b, model, _ = mk([call("music", action="what")], [NS(type="text", text="A long answer.")], extra=extra)
    b.reply("what is playing")
    sent = model.calls[1]["messages"][-1]["content"][0]["content"]
    assert sent == "x" * 5000                                                                              # the round that follows the call sees the whole answer
    answer = stored(b)[2]["content"][0]["content"]
    assert len(answer) <= brain_mod.TRACE_CHARS + 4 and answer.startswith("x" * 100) and answer.endswith("...")


def test_fix_brain_core_one_exchange_keeps_its_last_few_calls_and_a_long_argument_is_cut(no_routes):
    many = [call("list_timers", cid=f"p{i}") for i in range(6)] + [call("music", cid="m", action="what", note="n" * 900)]
    extra, ran = fake_tools("music")
    b, model, _ = mk(many, [NS(type="text", text="Done with all of them.")], extra=extra)
    b.reply("any timers and what is playing")
    kept = stored(b)
    uses = [m["content"][0] for m in kept if m["role"] == "assistant" and isinstance(m["content"], list)]
    assert len(uses) == brain_mod.TRACE_CALLS == 4 and valid(kept) and uses[-1]["name"] == "music" and len(uses[-1]["input"]["note"]) == brain_mod.TRACE_CHARS


def test_fix_brain_core_the_context_note_is_never_in_the_memory_and_the_plain_view_is_what_readers_get():
    b, model, _ = mk([call("list_timers")], [NS(type="text", text="None, monkey.")], [NS(type="text", text="Hi.")], room="living")
    b.reply("any timers")
    b.reply("hello")
    assert "[Right now" not in str(stored(b)) and "[Right now" in model.calls[0]["messages"][0]["content"]
    assert b.history[-1] == {"role": "assistant", "content": "Hi."} and all(isinstance(m["content"], str) for m in b.history)       # musicguard's last answer, the news route's "about what?"


def test_fix_brain_core_history_can_still_be_set_in_plain_words_and_odd_lists_do_not_break_it():
    b, model, clock = mk([NS(type="text", text="Hi.")])
    b.history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}, {"role": "user", "content": "lone"}]
    assert [m["content"] for m in b.history] == ["a", "b", "c", "d"] and valid(stored(b))
    b._last = clock[0]                                                                                     # (a conversation in progress: a fresh Brain's quiet clock would forget it)
    b.reply("hello")
    assert [m["content"] for m in model.calls[0]["messages"][:4]] == ["a", "b", "c", "d"]
    b.history = []
    assert b.history == [] and stored(b) == []
    b.history = None
    assert b.history == []


def test_fix_brain_core_a_conversation_is_forgotten_after_ten_quiet_minutes_and_an_offer_joins_it_as_an_exchange():
    b, model, clock = mk([NS(type="text", text="Hi.")], [NS(type="text", text="Hello again.")])
    b.reply("set a timer for five minutes")
    b.offer("The harbour is on fire, monkey.", "Source: the harbour story")
    assert [len(t) for t in b._turns] == [4, 2] and b.history[-2]["content"].startswith("(Nobody spoke") and b.history[-1]["content"] == "The harbour is on fire, monkey."
    b.reply("tell me more")
    assert [m["content"] for m in model.calls[0]["messages"][:1]] == ["set a timer for five minutes"] and valid(model.calls[0]["messages"])
    clock[0] += brain_mod.FORGET_AFTER_S + 1
    b.reply("hello")
    assert len(model.calls[1]["messages"]) == 1 and len(b._turns) == 1                                    # the trace goes with the conversation


# ── P1: the model is shown how a command is done, and a bare acknowledgement is not accepted ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("flow", ["tool loop", "claim asked again", "denial asked again", "text call worded", "routed wording"])
def test_fix_brain_core_every_model_call_of_a_turn_starts_with_the_one_example_of_a_tool_doing_what_he_asked(flow, no_routes):
    extra, ran = fake_tools("audiobook")
    kw, heard = {}, "any timers"
    if flow == "tool loop":
        rounds = [[call("list_timers")], [text("None, monkey.")]]
    elif flow == "claim asked again":
        rounds, heard = [[text("Alarm set for seven.")], [call("set_timer", seconds=60)], [text("A minute.")]], "wake me at seven"
    elif flow == "denial asked again":
        rounds, heard = [[text("I cannot see the timers from here.")], [call("list_timers")], [text("None.")]], "any timers"
    elif flow == "text call worded":
        rounds, heard = [[text('[set_timer seconds="90"]')], [text("Ninety seconds.")]], "ninety seconds on the clock"
    else:
        rounds, heard, kw = [[text("Paused.")]], "stop", {"extra": extra}
    b, model, _ = mk(*rounds, hass=FakeHouse(), **kw)
    if flow == "routed wording":
        b.pre_route = lambda heard: {"action": "pause"}
    b.reply(heard)
    assert len(model.calls) == len(rounds)
    for c in model.calls:
        if c.get("tools"):
            assert c["messages"][:4] == list(brain_mod.EXAMPLE) and valid(c["messages"]), flow
        else:                                                                                              # (fix2:view: a call with no tools to call is shown plain words, never the example or a tool block: Core answered those in its own tool-call XML)
            assert brain_mod.EXAMPLE[0] not in c["messages"] and all(isinstance(m["content"], str) for m in c["messages"]), flow
    example = brain_mod.EXAMPLE
    assert example[0]["content"] == "turn the kitchen fan on" and example[1]["content"][0]["name"] == "home_control" and example[1]["content"][0]["input"] == {"name": "kitchen fan", "action": "on"}
    assert example[2]["content"][0]["content"].startswith("Done: ") and example[3] == {"role": "assistant", "content": "Done."}
    assert example[1]["content"][0]["id"] == example[2]["content"][0]["tool_use_id"]


def test_fix_brain_core_without_the_house_tool_there_is_no_example_and_the_memory_starts_the_messages():
    b, model, _ = mk([text("Hi.")], [text("Hello.")])
    b.reply("hello")
    assert [m["role"] for m in model.calls[0]["messages"]] == ["user"] and model.calls[0]["messages"][0]["content"].endswith("\n\nhello")
    b.reply("again")
    assert [m["content"] for m in model.calls[1]["messages"][:2]] == ["hello", "Hi."]


def test_fix_brain_core_the_example_is_a_copy_nobody_can_spoil_and_never_the_memory():
    b, model, _ = mk([call("list_timers")], [text("None.")], hass=FakeHouse())
    first = b._example()
    first[0]["content"] = "spoiled"
    first[1]["content"][0]["input"]["name"] = "spoiled"
    assert brain_mod.EXAMPLE[0]["content"] == "turn the kitchen fan on" and b._example()[1]["content"][0]["input"]["name"] == "kitchen fan"
    b.reply("any timers")
    assert "kitchen fan" not in str(stored(b)) and len(stored(b)) == 4


def test_fix_brain_core_warm_sends_exactly_the_prefix_the_next_reply_sends_example_and_memory_with_its_tool_calls():
    b, model, _ = mk([call("list_timers")], [text("None, monkey.")], [text("ok")], [text("ok")], [text("Hi.")], hass=FakeHouse())
    b.reply("any timers")
    assert b.warm() is not None
    b.reply("and now")
    warm_with, warm_without, reply = model.calls[2], model.calls[3], model.calls[4]
    assert warm_with["messages"][:-1] == reply["messages"][:-1] and len(reply["messages"]) == 4 + 4 + 1             # the example, the exchange (user, call, answer, said), the new message
    assert warm_with["messages"][-1] == {"role": "user", "content": brain_mod.WARM_WORDS} and warm_without["messages"] == brain_mod.plain_words(warm_with["messages"])       # (the second request, with no tools, is the plain words: fix2:view)
    assert warm_with["system"] == reply["system"] and warm_with["tools"] == reply["tools"] and "tools" not in warm_without and valid(warm_with["messages"])


def test_fix_brain_core_warm_after_a_forgotten_conversation_sends_the_example_alone():
    b, model, clock = mk([text("Hi.")], [text("ok")], [text("ok")], hass=FakeHouse())
    b.reply("hello")
    clock[0] += brain_mod.FORGET_AFTER_S + 1
    assert b.warm() is not None
    assert model.calls[1]["messages"][:-1] == list(brain_mod.EXAMPLE) and len(b._turns) == 1                 # (the reply forgets it, not the warm-up)


def test_fix_brain_core_the_prompt_says_a_thing_is_done_by_its_tool_and_no_longer_hands_out_a_done_to_copy():
    system = Brain(None, "m", None, persona="PERSONA").system()
    assert "ONLY through your tools" in system and "never say you did something" in system and "you called no tool" in system and "call it first" in system
    assert "Done, Captain" not in system and "a few words" in system and "one or two short sentences" in system


ORDERS = [("I want the living room lights off", "Done."), ("would you mind switching the kitchen lights on", "Done, Captain. The kitchen lights are on."),
          ("the bedroom light needs to be on", "Already done, Captain."), ("lamp on in the bedroom please", "Okay."), ("dim the bedroom light to forty percent", "Got it."),
          ("light up the kitchen for me", "On it."), ("turn the coffee maker on", "No trouble at all."), ("lights off in the bedroom", "Off it goes."), ("brighten the kitchen light", "Certainly.")]


@pytest.mark.parametrize("heard, said", ORDERS)
def test_fix_brain_core_an_order_answered_with_a_bare_done_and_no_tool_is_asked_again_with_the_tools(heard, said, no_routes):
    house = FakeHouse()
    b, model, _ = mk([text(said)], [call("home_control", name="bedroom", action="on")], [text("There.")], hass=house, room="living")
    assert b.reply(heard) == "There." and len(model.calls) == 3 and b.guard == ("reask", "ack") and [a["tool"] for a in b.actions] == ["home_control"]
    assert house.calls == [("light", "turn_on", {"entity_id": "light.bedroom"})]
    nudge = model.calls[1]["messages"][-1]["content"]
    assert nudge.startswith(brain_mod.ASK_AGAIN_ONLY.split("{names}")[0]) and "NOTHING was done" in nudge and model.calls[1]["messages"][-2] == {"role": "assistant", "content": said}
    assert "announce" not in nudge and "cancel_timers" not in nudge                                         # (fix2:guards: the second try is behind the gate of the one after a denial, and the nudge names only what it may use)
    assert {t["name"] for t in model.calls[1]["tools"]} >= {"home_control"}


@pytest.mark.parametrize("heard, said, names, second", [
    ("I need a pasta timer for ten minutes", "Done.", (), call("set_timer", seconds=600)),
    ("could you skip to the next track", "Okay.", ("music", "play_music"), call("music", action="next")),
    ("wake me at seven tomorrow", "Got it.", ("set_alarm",), call("set_alarm", time="07:00")),
    ("put milk on the shopping list", "On it.", ("list",), call("list", action="add", items="milk")),
])        # (fix2:guards: an announcement is no longer one of them: the second try after a bare "Okay." never announces, see test_sat_fix2_brain_guards.py)
def test_fix_brain_core_the_backstop_knows_an_order_for_each_kind_of_tool_this_voice_has(heard, said, names, second, no_routes):
    extra, ran = fake_tools(*names) if names else (None, [])
    b, model, _ = mk([text(said)], [second], [text("There.")], extra=extra)
    assert b.reply(heard) == "There." and len(model.calls) == 3 and b.guard == ("reask", "ack") and len(b.actions) == 1


def test_fix_brain_core_a_second_try_that_still_calls_nothing_says_plainly_it_was_not_done(no_routes):
    b, model, _ = mk([text("Done.")], [text("Done, Captain.")], hass=FakeHouse())
    assert b.reply("the bedroom light needs to be on") == brain_mod.UNCLAIM and b.actions == [] and len(model.calls) == 2
    assert b.history[-1]["content"] == brain_mod.UNCLAIM


def test_fix_brain_core_a_second_try_that_repeats_the_claim_with_a_quip_is_not_taken_for_an_honest_one_but_a_real_retraction_stands(no_routes):
    claim = "Timer set for ten minutes, Captain."
    b, model, _ = mk([text(claim)], [text(claim + " Don't burn the kitchen down.")])                      # "n't" in a quip used to make the repeated claim "honest"
    assert b.reply("I need a pasta timer for ten minutes") == brain_mod.UNCLAIM and len(model.calls) == 2 and b.actions == []
    for retraction in ("I did not set it, monkey. Say it again.", "That was a mistake, I set nothing.", "I couldn't set it from here.", "I can't set timers.", "Which timer did you mean?"):
        b, model, _ = mk([text(claim)], [text(retraction)])
        assert b.reply("I need a pasta timer for ten minutes") == retraction, retraction


def test_fix_brain_core_the_backstop_judges_before_musicguards_tiers_which_file_lights_under_music(no_routes):
    house = FakeHouse()
    said = "Done, Captain. Turning off the living room lights for you."
    b, model, _ = mk([text(said)], [call("home_control", name="bedroom", action="off")], [text("Off.")], hass=house)
    assert b.reply("turn off the living room lights") == "Off." and [a["tool"] for a in b.actions] == ["home_control"] and len(model.calls) == 3
    assert "I have not played" not in b.history[-1]["content"]


@pytest.mark.parametrize("heard, said", [
    ("is the bedroom light on", "Done."),                                    # a question about the house (NOT_AN_ORDER) is not an order
    ("never mind", "Okay."),                                                 # no thing a tool does
    ("my name is Aaron", "Got it."),
    ("open the garage door", "Done."),                                       # what can hurt: the second try would be offered the lock's `confirm`
    ("what can you do with the lights", "Okay."),
    ("turn on the bedroom lamp", "Done? I did not, monkey. Ask me again."),    # a reply that asks, takes it back or is long is not an acknowledgement
    ("turn on the bedroom lamp", "Okay, but I did not turn it on."),
    ("turn on the bedroom lamp", "Done, Captain. The bedroom lamp is on, as you wished, and so is everything else in this magnificent house."),
    ("turn on the bedroom lamp", "The lamp is yours to turn on, monkey."),     # (not an acknowledgement at all)
])
def test_fix_brain_core_only_an_acknowledgement_to_an_order_is_asked_again(heard, said, no_routes):
    b, model, _ = mk([text(said)], hass=FakeHouse())
    assert b.reply(heard) == said and len(model.calls) == 1 and b.guard[1] != "ack"


def test_fix_brain_core_nothing_is_asked_again_when_a_tool_ran_or_no_tool_here_does_what_he_said(no_routes):
    b, model, _ = mk([call("home_control", name="bedroom", action="on")], [text("Done.")], hass=FakeHouse())
    assert b.reply("turn on the bedroom lamp") == "Done." and len(model.calls) == 2 and b.guard != ("reask", "ack")        # a tool ran: the words after it are true
    b, model, _ = mk([text("Okay.")])
    assert b.reply("play some jazz") == "Okay." and len(model.calls) == 1                                  # no music tool on this brain: nothing a second try could call
    b, model, _ = mk([text("Okay.")])
    assert b.reply("turn on the bedroom lamp") == "Okay." and len(model.calls) == 1                         # no house on this brain: it said so, or should have
    extra, ran = fake_tools("music", "play_music")
    b, model, _ = mk([text("Okay.")], [call("play_music", what="jazz")], [text("Playing jazz.")], extra=extra)
    assert b.reply("play some jazz") == "Playing jazz." and len(model.calls) == 3 and ran == [("play_music", {"what": "jazz"})]


@pytest.mark.parametrize("written, args", [
    ('[music]\naction: "next"', {"action": "next"}),                                  # the real Core, for "could you skip to the next track" (3 of 96): the call, then its arguments on the lines after it
    ("[music]\naction: next\nwhat: null\nlevel: null\nplaylist: null", {"action": "next"}),
    ("[music] action: next\nWhat is this? You want me to skip a track that is not playing?", {"action": "next"}),
    ("[music]\naction = volume\nlevel: 5.", {"action": "volume", "level": 5}),
    ("[music action=next]", {"action": "next"}),                                      # (the form it always read)
])
def test_fix_brain_core_a_call_written_as_text_with_its_arguments_on_the_next_lines_is_run_with_them(written, args, no_routes):
    extra, ran = fake_tools("music", results={"music": "Done: next on the house radio."})
    b, model, _ = mk([text(written)], [text("Skipped.")], extra=extra)
    assert b.reply("could you skip to the next track") == "Skipped." and ran == [("music", args)] and len(model.calls) == 2
    assert "tools" not in model.calls[1] and "Done: next on the house radio." in model.calls[1]["messages"][-1]["content"]


def test_fix_brain_core_the_lines_after_a_text_call_are_not_arguments_unless_they_are_the_tools_and_do_not_become_words():
    extra, ran = fake_tools("music")
    b, model, _ = mk([text("[music]\nNote: the tool is called music.\naction: next")], [text("")], extra=extra)
    b.reply("could you skip to the next track")
    assert ran == [("music", {})]                                                                          # a sentence right after the bracket ends the arguments: as before, no arguments
    b, model, _ = mk([text('[music]\naction: next\nIt is done.')], [NS(type="text", text="")], extra=extra)
    assert b.reply("could you skip to the next track") == "It is done." and ran[-1] == ("music", {"action": "next"})        # (the model's wording was empty: what is left of its text is said, the arguments are not)
    b, model, _ = mk([text('[set_timer]\nseconds: 90\nlabel: tea\nconfirm: true')], [text("Ninety seconds.")])
    b.reply("tea timer, ninety seconds")
    assert [(a["tool"], a["args"]) for a in b.actions] == [("set_timer", {"seconds": 90, "label": "tea"})]            # ("confirm" is not a timer's own argument)


# ── P5: a reply waits for the warm-up that is reading its prompt ───────────────────────────────────────────────────────────────────────────────────────────────────────────
class Hold:
    """A model whose one-token requests (a warm-up's) wait until the test lets them go; a reply's calls are answered at once. Every call is stamped."""

    def __init__(self):
        self.messages = self
        self.release, self.warm_started = threading.Event(), threading.Event()
        self.calls, self.lock = [], threading.Lock()

    def create(self, **kw):
        kind = "warm" if kw.get("max_tokens") == 1 else "reply"
        mine = [kind, time.monotonic(), None]
        with self.lock:
            self.calls.append(mine)
        if kind == "warm":
            self.warm_started.set()
            self.release.wait(10)
        mine[2] = time.monotonic()
        return NS(content=[text("Hi.")], stop_reason=None)


def begin_reply_during_a_warm_up(model, brain):
    warmer = threading.Thread(target=brain.warm)
    warmer.start()
    assert model.warm_started.wait(5)
    out = {}
    replier = threading.Thread(target=lambda: out.update(said=brain.reply("hello")))
    replier.start()
    deadline = time.monotonic() + 5
    while not brain._busy and time.monotonic() < deadline:
        time.sleep(0.005)
    time.sleep(0.1)
    return warmer, replier, out


def test_fix_brain_core_a_reply_that_begins_while_the_prompt_is_being_warmed_waits_for_it_instead_of_racing_it(capsys):
    model = Hold()
    b = Brain(model, "m", Timers(), persona="PERSONA")
    warmer, replier, out = begin_reply_during_a_warm_up(model, b)
    assert [c[0] for c in model.calls] == ["warm"] and "said" not in out                                   # the reply has asked the model nothing: it waits
    model.release.set()
    replier.join(5)
    warmer.join(5)
    assert out["said"] == "Hi." and [c[0] for c in model.calls] == ["warm", "reply"]                       # (the second shape is skipped: a reply is waiting for the model)
    assert model.calls[0][2] <= model.calls[1][1]                                                          # the warm-up's request ended before the reply's began
    assert b.timing["warm_wait_s"] >= 0.1 and b.timing["total_s"] >= b.timing["warm_wait_s"]
    assert "waited" in capsys.readouterr().out and b._warming is False and b._busy is False and b._warm_done.is_set()


def test_fix_brain_core_the_wait_is_bounded_and_a_warm_up_that_never_ends_does_not_hold_the_reply(monkeypatch, capsys):
    monkeypatch.setattr(brain_mod, "WARM_WAIT_S", 0.2)
    model = Hold()
    b = Brain(model, "m", Timers(), persona="PERSONA")
    warmer, replier, out = begin_reply_during_a_warm_up(model, b)
    replier.join(5)
    assert out["said"] == "Hi." and 0.15 <= b.timing["warm_wait_s"] < 3 and "gave up" in capsys.readouterr().out       # it went on after WARM_WAIT_S, the warm-up still running
    model.release.set()
    warmer.join(5)
    assert b._warm_done.is_set() and b._warming is False


def test_fix_brain_core_a_reply_with_no_warm_up_in_flight_waits_for_nothing_and_says_nothing(capsys):
    b, model, _ = mk([text("Hi.")])
    assert b.reply("hello") == "Hi." and "warm_wait_s" not in b.timing and "waited" not in capsys.readouterr().out
    assert b.warm() is None                                                                                 # (the model has no further answer: a warm-up fails quietly)
    assert b._warm_done.is_set() and b._warming is False


def test_fix_brain_core_the_gate_lets_exactly_one_of_a_warm_up_and_a_reply_go_first():
    """The two flags are settled under one lock: a warm-up that begins while a reply is running starts nothing, and the reply that begins while one is running waits for it."""
    model = Hold()
    b = Brain(model, "m", Timers(), persona="PERSONA")
    b._busy = True
    assert b.warm() is None and model.calls == [] and b._warm_done.is_set()
    b._busy = False
    model.release.set()
    assert b.warm() is not None and b._warm_done.is_set()


# ── P12(a): the persona's annotations are not read aloud after a routed command ──────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("annotation", ['[SKIPPY_LEARN: {"phrase": "cancel the five minute timer", "intent": "cancel_timers"}]', '[TTS_ADJUST: {"speed": 1.1}]'])
def test_fix_brain_core_a_routed_wording_never_speaks_the_personas_annotations(annotation):
    extra, ran = fake_tools("audiobook")
    b, model, _ = mk([text(f"Paused, monkey. {annotation}")], extra=extra)
    b.pre_route = lambda heard: {"action": "pause"}
    assert b.reply("stop") == "Paused, monkey." and "SKIPPY_LEARN" not in str(b.history) and "TTS_ADJUST" not in str(stored(b))


# ── the pieces ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix_brain_core_a_call_the_memory_cannot_keep_costs_the_reply_nothing():
    b, model, _ = mk([text("Hi.")])
    b._record("music", {"fine": 1, "bad": object()}, "answer")
    assert b._ran == []
    b._record("music", {"what": "w" * 1000}, "answer")
    assert len(b._ran[0]["input"]["what"]) == brain_mod.TRACE_CHARS and b._ran[0]["result"] == "answer" and b._ran[0]["id"].startswith("toolu_house_")
    ids = [b._ran[0]["id"]]
    b._record("music", {}, "again")
    assert b._ran[1]["id"] not in ids


def test_fix_brain_core_the_news_route_and_the_music_guard_read_the_plain_last_answer_after_a_turn_with_tools(no_routes):
    from test_sat_news import Get, results
    b, model, _ = mk([text("Here is politics."), ], news=news_mod_with(Get(results("Senate passes bill", "Governor signs law"))))
    assert b.reply("what's the news") == brain_mod.news_mod.ASK_WHAT and b.history[-1]["content"] == brain_mod.news_mod.ASK_WHAT
    b.reply("Politics.")                                                                                    # news.pre_route read the plain "about what?" in history, and ran the news tool
    assert [a["tool"] for a in b.actions] == ["news"] and b.history[-2]["content"] == "Politics."
    kept = stored(b)
    assert valid(kept) and kept[-3]["content"][0]["name"] == "news" and isinstance(b.history[-1]["content"], str)


def news_mod_with(get):
    return brain_mod.news_mod.News({}, get=get)
