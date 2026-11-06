"""fix2:view: what the model is SHOWN, and what it is allowed to say (Problems 1, 4 and the memory half of 8 of the review of the fixes to the house speed change, 2026-11-05).

P1  The memory keeps the real tool exchanges and every call starts with EXAMPLE, and a call that offers NO tools was shown them anyway: Skippy Core answered with its own tool-call XML
    ("<tool_call><function=weather>...") instead of words, and that was spoken, summarised and kept. On the real Core, after four routed commands: 6 of 8 weather answers and 6 of 6 "play some jazz";
    with plain words 0 of 8. So a call with no tools is shown the conversation in PLAIN WORDS (plain_words, shaped in the ONE place Brain._create and by warm()'s second request), and no tool
    markup is ever spoken or kept whatever the model writes (Brain._spoken); when that leaves nothing, a routed tool's own short answer is the fallback.
P4  The window slid one exchange per turn once eight were held, so every model turn read the whole window again; it slides in blocks now (MEMORY_BLOCK).
P8  A conversation is forgotten after FORGET_AFTER_S of quiet; the history setter keeps whole user / assistant pairs only.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

import brain as brain_mod
import house as house_mod
import weather as weather_mod
from brain import Brain
from timers import Timers

NOW = "Thursday, November 05, 2026, 14:45"
XML = "<tool_call>\n<function=weather>\n<parameter=when>\ntoday\n</parameter>\n</function>\n</tool_call>"
XML_CUT = "<tool_call>\n<function=play_music>\n<parameter=query>\njazz\n</parameter>\n<parameter=shuffle>\n"       # (what the model ran out of room in)


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


class CoreLike:
    """Skippy Core as the review found it: shown tool_use / tool_result blocks in a call that offers NO tools, it answers in its own tool-call XML instead of words; otherwise it says `words`."""

    def __init__(self, words="Words, monkey."):
        self.words, self.calls = words, []
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": copy.deepcopy(kw["messages"])})
        blocks = any(isinstance(m["content"], list) for m in kw["messages"])
        return NS(content=[text(XML if blocks and not kw.get("tools") else self.words)], stop_reason=None)


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


PROPS = {"music": {"action": {"type": "string"}, "level": {"type": "integer"}}}


def fake_tools(*names, results=None):
    ran = []

    def run(name, args):
        ran.append((name, dict(args)))
        return (results or {}).get(name, f"({name} done)")
    return ([{"name": n, "description": n, "input_schema": {"type": "object", "properties": PROPS.get(n, {})}} for n in names], run), ran


def brain(client, hass=None, room="", extra=None, **kw):
    clock = [1_793_000_000.0]
    b = Brain(client, "m", Timers(clock=lambda: clock[0]), persona="PERSONA", clock=lambda: clock[0], now=lambda: NOW, hass=hass, extra=extra, **kw)
    if room:
        b.room = room
    b.sleep = lambda s: None
    return b, clock


@pytest.fixture
def no_routes(monkeypatch):
    for name in ("light_route", "timer_route", "music_route", "speakers_route", "device_route", "bilby_route", "announce_route"):
        if hasattr(brain_mod, name):
            monkeypatch.setattr(brain_mod, name, lambda *a, **k: None)
    for module in (brain_mod.lists_mod, brain_mod.alarms_mod):
        monkeypatch.setattr(module, "pre_route", lambda *a, **k: None)


def lived(b, clock, n=4):
    """A conversation that happened: n exchanges, each with a tool call and its answer (what routed commands leave in the memory). The quiet clock says it is going on."""
    for i in range(1, n + 1):
        b._ran = []
        b._record("set_timer", {"seconds": 60 * i, "label": f"t{i}"}, f"Timer set: t{i}, {i} minute{'s' if i != 1 else ''}.")
        b._remember(f"set a timer for {i} minutes", f"Timer set for {i} minutes.")
    b._ran = []
    b._last = clock[0] if clock is not None else b.clock()


def valid(messages):
    """What the API accepts: roles alternate from a user message; every tool_use is answered by a tool_result with the same ids in the very next message; no tool_result without its tool_use."""
    pending = None
    for i, m in enumerate(messages):
        assert m["role"] == ("user" if i % 2 == 0 else "assistant"), (i, m["role"])
        blocks = m["content"] if isinstance(m["content"], list) else []
        uses = [b["id"] for b in blocks if b.get("type") == "tool_use"]
        results = [b["tool_use_id"] for b in blocks if b.get("type") == "tool_result"]
        if m["role"] == "assistant":
            assert pending is None, "a tool_use was not answered"
            pending = set(uses) or None
        else:
            assert (set(results) == pending) if pending is not None else not results, (i, results, pending)
            pending = None
    assert pending is None
    return True


def stored(b):
    return b._window()


def is_plain(call, answer=None):
    """A call that offers no tools was shown words only: strings, no example, roles alternating from the user to the user, and (when `answer` is given) the newest message holds it."""
    msgs = call["messages"]
    assert not call.get("tools"), "this call offers tools"
    assert msgs and all(isinstance(m["content"], str) for m in msgs), [m["content"] for m in msgs if not isinstance(m["content"], str)]
    assert brain_mod.EXAMPLE[0] not in msgs and "kitchen fan" not in str(msgs) and "tool_use" not in str(msgs) and "tool_result" not in str(msgs)
    assert [m["role"] for m in msgs] == ["user", "assistant"] * (len(msgs) // 2) + ["user"], [m["role"] for m in msgs]
    if answer is not None:
        assert answer in msgs[-1]["content"], msgs[-1]["content"]
    return True


# ── plain_words: the one place the plain view is made ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix2_plain_words_drops_the_example_and_every_tool_block_keeps_the_words_and_leaves_the_callers_list_alone():
    use = {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "set_timer", "input": {"seconds": 60}}]}
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Timer set: tea, 1 minute."}]}
    sent = [*copy.deepcopy(list(brain_mod.EXAMPLE)), {"role": "user", "content": "set a timer"}, use, result, {"role": "assistant", "content": "Timer set."}, {"role": "user", "content": "[Right now: noon.]\n\nthanks"}]
    before = copy.deepcopy(sent)
    got = brain_mod.plain_words(sent)
    assert got == [{"role": "user", "content": "set a timer"}, {"role": "assistant", "content": "Timer set."}, {"role": "user", "content": "[Right now: noon.]\n\nthanks"}]
    assert sent == before and got is not sent                                                                 # (the caller goes on appending to its own list: a NEW list, nothing mutated)
    got[0]["content"] = "spoiled"
    assert sent == before


def test_fix2_plain_words_keeps_the_roles_alternating_whatever_it_drops():
    pair = [{"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "n", "input": {}}]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "x"}]}]
    u, a = (lambda t: {"role": "user", "content": t}), (lambda t: {"role": "assistant", "content": t})
    assert brain_mod.plain_words([a("stray"), u("one"), *pair, a("two"), u("three")]) == [u("one"), a("two"), u("three")]               # (a leading assistant message goes: the conversation starts with him)
    assert brain_mod.plain_words([u("one"), *pair, *pair, u("two")]) == [u("one\n\ntwo")]                                            # (two messages in a row from one role are joined)
    assert brain_mod.plain_words([u("one"), a("two"), u("three"), u("four")]) == [u("one"), a("two"), u("three\n\nfour")]
    assert brain_mod.plain_words([u("one"), a("two"), a("three")]) == [u("one"), a("two\n\nthree")]
    assert brain_mod.plain_words([]) == [] and brain_mod.plain_words(None) == [] and brain_mod.plain_words([a("only")]) == []


def test_fix2_plain_words_turns_text_blocks_into_a_string_and_skips_what_is_no_words():
    blocks = {"role": "assistant", "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": "there."}]}
    assert brain_mod.plain_words([{"role": "user", "content": "hi"}, blocks]) == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello there."}]
    odd = [{"role": "user", "content": "hi"}, "not a message", {"role": "system", "content": "x"}, {"role": "assistant", "content": None}, {"role": "assistant", "content": [{"type": "text", "text": "  "}]},
           {"role": "assistant", "content": 7}, {"role": "assistant", "content": "ok"}]
    assert brain_mod.plain_words(odd) == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]


def test_fix2_plain_words_on_two_thousand_random_conversations_always_alternates_never_mutates_and_is_a_fixed_point():
    import random
    rng = random.Random(20261105)
    texts = ["hi", "set a timer", "Done.", "Timer set: tea, 1 minute.", "", "  ", "ok then"]

    def message():
        role = rng.choice(["user", "assistant", "user", "assistant", "system", "tool"])
        kind = rng.choice(["str", "str", "str", "text", "use", "result", "mixed", "none", "int", "junk"])
        t = rng.choice(texts)
        content = {"str": t, "text": [{"type": "text", "text": t}], "use": [{"type": "tool_use", "id": "a", "name": "n", "input": {}}], "result": [{"type": "tool_result", "tool_use_id": "a", "content": t}],
                   "mixed": [{"type": "text", "text": t}, {"type": "tool_use", "id": "b", "name": "n", "input": {}}], "none": None, "int": 7, "junk": ["x", 3]}[kind]
        return rng.choice([{"role": role, "content": content}, {"role": role, "content": content}, "junk"]) if kind == "junk" else {"role": role, "content": content}

    for _ in range(2000):
        msgs = [message() for _ in range(rng.randint(0, 9))]
        if rng.random() < 0.4:
            msgs = copy.deepcopy(list(brain_mod.EXAMPLE)) + msgs
        before = copy.deepcopy(msgs)
        got = brain_mod.plain_words(msgs)
        assert msgs == before and got is not msgs
        assert all(set(m) == {"role", "content"} and isinstance(m["content"], str) for m in got)
        assert [m["role"] for m in got] == ["user", "assistant"] * (len(got) // 2) + ["user"] * (len(got) % 2)
        assert brain_mod.plain_words(got) == got
        said = " ".join(str(m.get("content")) for m in before if isinstance(m, dict))
        assert all(piece.strip() in said for m in got for piece in m["content"].split("\n\n"))                  # (nothing is made up: every piece of it was in what came in)


def test_fix2_plain_words_drops_a_message_that_has_a_tool_call_in_it_together_with_the_words_that_came_with_it():
    mixed = {"role": "assistant", "content": [{"type": "text", "text": "Let me check that."}, {"type": "tool_use", "id": "m1", "name": "weather", "input": {}}]}
    answered = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "m1", "content": "Sunny."}, {"type": "text", "text": "carry on"}]}
    only_use = {"role": "assistant", "content": [{"type": "tool_use", "id": "m2", "name": "weather", "input": {}}]}
    only_result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "m2", "content": "Sunny."}]}
    for pair in ([mixed, answered], [only_use, only_result], [mixed, only_result], [only_use, answered]):
        assert brain_mod.plain_words([{"role": "user", "content": "weather?"}, *pair, {"role": "assistant", "content": "Sunny."}]) == [{"role": "user", "content": "weather?"}, {"role": "assistant", "content": "Sunny."}]
    assert brain_mod.plain_words([{"role": "user", "content": "x"}, only_use, {"role": "user", "content": "y"}]) == [{"role": "user", "content": "x\n\ny"}]         # (a tool_use alone goes too)
    assert brain_mod.plain_words([{"role": "user", "content": "x"}, {"role": "assistant", "content": "z"}, only_result]) == [{"role": "user", "content": "x"}, {"role": "assistant", "content": "z"}]


def test_fix2_plain_words_drops_the_example_only_at_the_head_and_only_the_whole_example():
    example = copy.deepcopy(list(brain_mod.EXAMPLE))
    later = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi."}, *copy.deepcopy(example)]
    assert [m["content"] for m in brain_mod.plain_words(later)] == ["hello", "Hi.", "turn the kitchen fan on", "Done."]                      # (his words later in the talk are words)
    changed = copy.deepcopy(example)
    changed[0]["content"] = "turn the kitchen fan off"
    assert [m["content"] for m in brain_mod.plain_words(changed + [{"role": "user", "content": "x"}])] == ["turn the kitchen fan off", "Done.", "x"]


# ── P1: every call that offers no tools is shown words, in the one place ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix2_the_request_is_shaped_in_one_place_and_a_call_that_offers_tools_is_not_touched():
    b, clock = brain(Model(), hass=FakeHouse())
    lived(b, clock)
    msgs = b._prefix() + [{"role": "user", "content": "now"}]
    with_tools = dict(model="m", max_tokens=5, system="s", messages=msgs, tools=b.tools())
    assert b._shape(with_tools) is with_tools and with_tools["messages"] is msgs and msgs[:4] == list(brain_mod.EXAMPLE)        # (nothing changes on the tools path)
    for none in ({"model": "m", "messages": msgs}, {"model": "m", "messages": msgs, "tools": []}, {"model": "m", "messages": msgs, "tools": None}):
        shaped = b._shape(none)
        assert shaped is not none and is_plain({**shaped, "tools": None}) and shaped["messages"][-1] == {"role": "user", "content": "now"} and shaped["model"] == "m"
        assert none["messages"] is msgs and msgs[:4] == list(brain_mod.EXAMPLE) and any(isinstance(m["content"], list) for m in msgs)                # (the caller's list is as it was)
    assert [m["content"] for m in b._shape({"messages": msgs})["messages"]][:2] == ["set a timer for 1 minutes", "Timer set for 1 minutes."]


SHAPES = {
    "audiobook": dict(extra=("audiobook",), results={"audiobook": "(chapter 4 of 12) You are forty minutes in."}, heard="where am I in my book", answer="You are forty minutes in."),
    "play_music": dict(extra=("play_music",), results={"play_music": "Playing jazz on the Enclave."}, heard="play some jazz", answer="Playing jazz on the Enclave."),
    "set_alarm": dict(extra=("set_alarm",), results={"set_alarm": "Alarm set for 7:00 AM."}, heard="wake me at 7 am", answer="Alarm set for 7:00 AM."),
    "list": dict(extra=("list",), results={"list": "Added milk to the shopping list. It has 4 items."}, heard="add milk to the shopping list", answer="Added milk to the shopping list."),
    "weather": dict(extra=(), results={}, heard="what's the weather", answer="Today in Gold Canyon: sunny, high 78."),
}


class StubWeather:
    def tools(self):
        return [weather_mod.TOOL]

    def run(self, args):
        return "Today in Gold Canyon: sunny, high 78."


def shaped_brain(kind, client, hass=None):
    s = SHAPES[kind]
    extra, ran = fake_tools(*s["extra"], results=s["results"]) if s["extra"] else (None, [])
    kw = {"weather": StubWeather()} if kind == "weather" else {}
    b, clock = brain(client, hass=hass, extra=extra, **kw)
    if kind == "audiobook":
        b.pre_route = lambda heard: {"action": "where"}
    return b, clock, s


@pytest.mark.parametrize("kind", list(SHAPES))
@pytest.mark.parametrize("memory", ["fresh", "after four routed commands"])
def test_fix2_a_routed_answer_is_worded_from_plain_words_and_core_answers_in_words_not_in_tool_markup(kind, memory):
    core = CoreLike()
    b, clock, s = shaped_brain(kind, core, hass=FakeHouse())
    if memory != "fresh":
        lived(b, clock)
    assert b.reply(s["heard"]) == "Words, monkey." and b.timing["route"] == kind                              # the real Core said XML here, 6 of 8 and 6 of 6
    assert len(core.calls) == 1 and is_plain(core.calls[0], s["answer"])                                         # it still SEES the tool's answer: a plain string in the newest message
    assert "<" not in str(b._turns) and b.history[-1] == {"role": "assistant", "content": "Words, monkey."}
    if memory != "fresh":
        assert [m["content"] for m in core.calls[0]["messages"][:2]] == ["set a timer for 1 minutes", "Timer set for 1 minutes."]                   # what happened before is there, as words


def test_fix2_the_wording_of_a_mentat_asked_by_the_house_is_shown_plain_words_too():
    from test_sat_physics import asked_from_memory
    heard = "quick, how quickly must a rocket move to get free of Mars?"
    b, client, _ = asked_from_memory("[Mentat] The escape velocity of Mars is 5.03 km/s. Source: NASA Mars Fact Sheet.", [text("Mentat says 11186 meters per second, from sigma-ground.")])
    b.hass = FakeHouse()
    lived(b, None, 3)
    assert b.reply(heard) == "Mentat says 11186 meters per second, from sigma-ground."
    plain = {**client.calls[1], "messages": copy.deepcopy(client.calls[1]["messages"])}
    assert "tools" not in plain and is_plain(plain, "Mentat's answer: 11186 m/s.") and heard in plain["messages"][-1]["content"]
    assert plain["messages"][0]["content"] == "set a timer for 1 minutes"
    assert "tools" in client.calls[0]


def test_fix2_a_tool_call_written_as_text_is_worded_from_plain_words_after_a_refused_call_left_blocks_in_the_conversation():
    """A denial's second try: the model calls a tool the gate refuses (a tool_use and a NOT_DONE tool_result are now in the conversation), then writes a call as text; the wording call that follows has
    no tools and must be shown words only, still alternating, with the house's note about the call it ran as its newest message."""
    extra, ran = fake_tools("cancel_alarm", "set_alarm")
    model = Model([text("I cannot cancel alarms from here.")], [call("cancel_alarm", cid="refused")], [text("[list_timers]")], [text("Nothing is running, monkey.")])
    b, clock = brain(model, hass=FakeHouse(), extra=extra)
    lived(b, clock)
    assert b.reply("can you cancel my alarm") == "Nothing is running, monkey." and len(model.calls) == 4
    refused = model.calls[2]["messages"]
    assert any(isinstance(m["content"], list) for m in refused) and valid(refused)                              # the second try (tools offered) keeps the blocks as they are
    assert "tools" in model.calls[1] and "tools" in model.calls[2] and "tools" not in model.calls[3]
    assert is_plain(model.calls[3], "The house ran the tool call you wrote as text") and "No timers running." in model.calls[3]["messages"][-1]["content"]
    assert [m["content"] for m in model.calls[3]["messages"][:2]] == ["set a timer for 1 minutes", "Timer set for 1 minutes."]
    assert "Not done" not in str(model.calls[3]["messages"])                                                     # (the refused call's answer is a block: not words)


def test_fix2_a_second_try_without_tools_is_shown_words_and_the_callers_conversation_keeps_its_blocks():
    model = Model([text("I did not play anything.")])
    b, clock = brain(model, hass=FakeHouse())
    lived(b, clock)
    messages = b._prefix() + [{"role": "user", "content": b._user_turn("play some jazz")}, {"role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "list_timers", "input": {}}]},
                              {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "content": "No timers running."}]}]
    kept = copy.deepcopy(messages)
    assert b._second_try("Playing jazz.", messages, "[The house: nudge]", [], "fallback") == "I did not play anything."
    assert is_plain(model.calls[0], "[The house: nudge]") and "tools" not in model.calls[0]
    assert messages[:len(kept)] == kept and valid(messages[:len(kept)])                                         # (what the caller had is as it was; it only appended its own two)
    assert [m["role"] for m in model.calls[0]["messages"][-3:]] == ["user", "assistant", "user"] and model.calls[0]["messages"][-2]["content"] == "Playing jazz."


def test_fix2_warm_reads_into_the_cache_exactly_the_plain_prefix_the_next_call_without_tools_sends():
    extra, ran = fake_tools("audiobook", results={"audiobook": "(chapter 4 of 12) You are forty minutes in."})
    model = Model([text("ok")], [text("ok")], [text("Forty minutes in, monkey.")])
    b, clock = brain(model, hass=FakeHouse(), extra=extra)
    b.pre_route = lambda heard: {"action": "where"}
    lived(b, clock)
    assert b.warm() is not None
    assert b.reply("where am I in my book") == "Forty minutes in, monkey."
    with_tools, without_tools, wording = model.calls
    assert "tools" in with_tools and "tools" not in without_tools and "tools" not in wording
    assert with_tools["messages"][:4] == list(brain_mod.EXAMPLE) and any(isinstance(m["content"], list) for m in with_tools["messages"])      # the tools shape keeps the example and the blocks
    assert is_plain(without_tools) and without_tools["messages"][-1] == {"role": "user", "content": brain_mod.WARM_WORDS}
    assert without_tools["messages"][:-1] == wording["messages"][:-1] and without_tools["system"] == wording["system"]                    # warm() read exactly what the wording call sends before his message


def test_fix2_a_denials_filtered_menu_keeps_the_history_as_it_is_even_when_the_menu_lacks_a_tool_the_history_used(no_routes):
    """The tools path: the second try after a denial is SHOWN the whole tool list (fix3:guards: Core caches per tool list, a cut menu was read from scratch), the nudge names only what a question may use (a question may
    only look, so home_control is not named) and the history still holds home_control calls, which the whole list has."""
    model = Model([text("I cannot see the lights from here.")], [call("home_state", name="bedroom")], [text("It is off, monkey.")])
    b, clock = brain(model, hass=FakeHouse())
    lived(b, clock, 2)
    assert b.reply("is the bedroom light on") == "It is off, monkey." and len(model.calls) == 3 and [a["tool"] for a in b.actions] == ["home_state"]
    second = model.calls[1]
    names = {t["name"] for t in second["tools"]}
    named = second["messages"][-1]["content"].split("you DO have these: ")[1].split(". Read his words")[0].split(", ")
    assert "home_control" not in named and "home_state" in named and second["tools"] == model.calls[0]["tools"] and "home_control" in names
    assert second["messages"][:4] == list(brain_mod.EXAMPLE) and "home_control" in str(second["messages"][:4]) and valid(second["messages"][:-3])
    assert any(m["role"] == "assistant" and isinstance(m["content"], list) and m["content"][0]["name"] == "set_timer" for m in second["messages"])


# ── the tool markup is never spoken, never kept ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def spoken(t, stop=None):
    return Brain._spoken(NS(content=[text(t)], stop_reason=stop))


@pytest.mark.parametrize("raw, said", [
    (XML, ""),
    (XML_CUT, ""),                                                                                           # (ran out of room inside the call)
    ("<TOOL_CALL>\n<FUNCTION=weather>\n</FUNCTION>\n</TOOL_CALL>", ""),                                      # (any case)
    ("<tool_call><function=weather></function></tool_call>", ""),                                            # (on one line)
    ("Let me check. " + XML + " Sunny.", "Let me check. Sunny."),                                            # (the words around it stay)
    ("Sunny today. " + XML_CUT, "Sunny today."),
    ("<function=weather><parameter=when>today</parameter></function> It is sunny.", "It is sunny."),         # (a lone function)
    ("It is <parameter=when>today</parameter> sunny.", "It is sunny."),                                      # (a lone parameter)
    ("It is sunny.</tool_call>", "It is sunny."),                                                            # (a stray closing tag)
    ("<tool_call>{\"name\": \"weather\", \"arguments\": {}}</tool_call>", ""),                               # (the JSON form)
    ("Play it. <tool_call>\nfirst</tool_call> then <tool_call>\nsecond</tool_call> done.", "Play it. then done."),
    ('Sure. <tool_call>{"name": "weather", "arguments": {"when": "tod', "Sure."),                              # (cut inside the call: what is left of it is not words, whatever it looks like)
    ("Sure. <function=weather> when today", "Sure."),
    ("Sure. <parameter=when> today and more", "Sure."),
    ("<function=weather>Gold Canyon</function> It is sunny.", "It is sunny."),                               # (what is inside a function is its arguments, not words)
    ("It is <parameter=when>today</parameter> sunny.<parameter=x>", "It is sunny."),
    ("Jazz it is. Grab a drink, Captain. [IMAGE: cool jazz album cover with a saxophone in a smoky room]", "Jazz it is. Grab a drink, Captain."),      # (the persona's picture sentinel, shared with the portal chat: 3 of 117 real replies)
    ("Jazz it is. [image: cool jazz album", "Jazz it is."),
    ("Fine. [IMAGE: x] Next.", "Fine. Next."),
])
def test_fix2_tool_call_markup_is_never_in_the_words(raw, said):
    assert spoken(raw) == said


@pytest.mark.parametrize("plain", ["It is 5 < 7 and 9 > 3, monkey.", "Use the <b>bold</b> one.", "Call the function weather. The tool called weather said sunny.", "Tomorrow: sunny.\nTonight: clear.",
                                   "I wrote a tool_call once.", "The [IMAGE] key is not a prompt.", "[sighs] left for strip_stage_directions"])      # (fix3:text: the persona's annotations are _spoken's to cut now, test_sat_fix3_text.py)
def test_fix2_words_that_are_not_markup_are_left_exactly_as_they_were(plain):
    assert spoken(plain) == plain


def test_fix2_markup_is_cut_before_a_reply_that_ran_out_of_room_is_cut_back_to_its_last_sentence():
    assert spoken("It is sunny. Tomorrow too. " + XML_CUT, stop="max_tokens") == "It is sunny. Tomorrow too."
    assert spoken("It is sunny, and then " + XML_CUT, stop="max_tokens") == "It is sunny, and then"          # (no whole sentence in it: unchanged but for the markup)
    assert spoken("<tool_call>x. y.</tool_call> Tomorrow is sunny and", stop="max_tokens") == "Tomorrow is sunny and"      # (a full stop INSIDE the markup is no sentence end of the words: the markup goes first)


@pytest.mark.parametrize("kind", ["audiobook", "play_music", "set_alarm", "list", "weather"])
def test_fix2_a_routed_wording_that_is_only_markup_is_never_spoken_and_the_fallback_speaks(kind):
    model = Model([text(XML)])
    b, clock, s = shaped_brain(kind, model)
    said = b.reply(s["heard"])
    expected = {"audiobook": "You are forty minutes in.", "play_music": "Playing jazz on the Enclave.", "set_alarm": "Alarm set for 7:00 AM.", "list": "Added milk to the shopping list. It has 4 items.",
                "weather": brain_mod.UNWORDED["weather"]}[kind]
    assert said == expected and "<" not in said and b.history[-1]["content"] == expected and "<" not in str(b._turns)


def test_fix2_a_short_plain_tool_answer_is_the_fallback_and_material_keeps_its_plain_line():
    own = brain_mod.own_answer
    assert own("Timer set: pasta, 10 minutes.") == "Timer set: pasta, 10 minutes." and own("  Done: pause on the house radio.  ") == "Done: pause on the house radio."
    assert own("x" * brain_mod.OWN_ANSWER_CHARS) == "x" * brain_mod.OWN_ANSWER_CHARS and own("x" * (brain_mod.OWN_ANSWER_CHARS + 1)) == ""
    assert own("line one\nline two") == "" and own("") == "" and own(None) == "" and own("see [this] and {that} <here>") == "" and own("a [b]") == ""
    assert own("Playing <jazz>.") == "" and own("Playing {jazz}.") == "" and own("Playing [jazz].") == "" and own("Playing (jazz).") == "Playing (jazz)."
    assert set(brain_mod.UNWORDED) == {"weather", "news", "car_status", "ask_mentat"}
    assert all("wandered off" in line for line in brain_mod.UNWORDED.values())


def test_fix2_a_routed_timer_or_light_or_song_that_could_not_be_worded_says_what_the_tool_said_not_ask_me_again():
    timers_down = Model([text(XML)])
    b, clock = brain(timers_down)
    b.timers.add = lambda seconds, label="": (_ for _ in ()).throw(ValueError("a timer is between one second and a day"))
    said = b.reply("set a timer for five minutes")
    assert said == "That did not work: a timer is between one second and a day" and "Ask me again" not in said and "<" not in str(b._turns)
    extra, ran = fake_tools("music", results={"music": "Done: pause on the house radio. But no speaker is on."})
    b, clock = brain(Model([text(XML_CUT)]), extra=extra)
    assert b.reply("pause the music") == "Done: pause on the house radio. But no speaker is on." and ran == [("music", {"action": "pause"})]
    h = FakeHouse()
    h.control = lambda *a, **k: "Done: kitchen under 1, on. These did not answer: kitchen under 2."
    b, clock = brain(Model([text("")]), hass=h)
    assert b.reply("turn on the kitchen lights") == "Done: kitchen under 1, on. These did not answer: kitchen under 2."


def test_fix2_an_answer_that_is_long_or_many_lines_or_has_brackets_in_it_is_not_read_out_as_the_fallback():
    for result in ("x" * 400, "line one\nline two", "Added [milk] to the list."):
        extra, ran = fake_tools("list", results={"list": result})
        b, clock = brain(Model([text(XML)]), extra=extra)
        assert b.reply("add milk to the shopping list") == brain_mod.TRIED, result


def test_fix2_the_fallbacks_that_name_the_material_are_unchanged(monkeypatch):
    from test_sat_news import Get, results
    news = brain_mod.news_mod.News({}, get=Get(results("Senate passes bill", "Governor signs law")))
    b, clock = brain(Model([text(XML)]), news=news)
    b.history = [{"role": "user", "content": "what's the news"}, {"role": "assistant", "content": brain_mod.news_mod.ASK_WHAT}]
    b._last = clock[0]
    assert b.reply("Politics.") == brain_mod.UNWORDED["news"]


def test_fix2_an_ask_mentat_wording_that_is_only_markup_says_the_plain_apology_and_the_figure_is_not_kept():
    from test_sat_physics import asked_from_memory
    b, client, _ = asked_from_memory("About 5.03 km/s, Captain.", [text(XML)])
    said = b.reply("quick, how quickly must a rocket move to get free of Mars?")
    assert said.startswith("Mentat answered, and then my brain wandered off") and "<" not in said and "<" not in str(b._turns) and "5.03" not in said


def test_fix2_a_text_call_wording_that_is_only_markup_falls_back_to_the_tools_answer(no_routes):
    extra, ran = fake_tools("music", results={"music": "Done: next on the house radio."})
    model = Model([text("[music action=next]")], [text(XML)])
    b, clock = brain(model, extra=extra)
    said = b.reply("could you hop to the next track")
    assert said == "Done: next on the house radio." and "<" not in said and ran == [("music", {"action": "next"})]


def test_fix2_a_reply_that_is_only_markup_says_so_in_words_and_keeps_none_of_it(no_routes):
    model = Model([text(XML)])
    b, clock = brain(model, hass=FakeHouse())
    said = b.reply("tell me a joke")
    assert said == "Hm. I had nothing to say, which is unlike me." and "<" not in str(b._turns) and b.actions == []                                 # (and no tool was run for markup: nothing RUNS it)


def test_fix2_markup_in_a_second_tries_words_is_not_taken_for_an_honest_answer(no_routes):
    model = Model([text("Alarm set for seven.")], [text(XML)])
    b, clock = brain(model, hass=FakeHouse())
    said = b.reply("wake me at seven")
    assert said == brain_mod.UNCLAIM and "<" not in said and b.actions == []


# ── P4: the window slides in blocks ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix2_the_window_slides_by_a_block_of_exchanges_not_by_one():
    assert brain_mod.MEMORY_TURNS == 8 and brain_mod.MEMORY_BLOCK == 4
    b, clock = brain(Model())
    held, oldest = [], []
    for i in range(1, 15):
        assert b.reply(f"set a timer for {i} minutes") == f"Timer set for {brain_mod.number_words(i)} minute{'s' if i != 1 else ''}."
        held.append(len(b._turns))
        oldest.append(b._turns[0][0]["content"])
        assert valid(stored(b)) and len(b._turns) <= brain_mod.MEMORY_TURNS and len(b.history) == 2 * len(b._turns)
    assert held == [1, 2, 3, 4, 5, 6, 7, 8, 5, 6, 7, 8, 5, 6]                                               # the 9th drops the oldest four (5 stay), and again at the 13th
    assert oldest[8] == "set a timer for 5 minutes" and oldest[12] == "set a timer for 9 minutes" and oldest[7] == "set a timer for 1 minutes"
    assert b._turns[-1][0]["content"] == "set a timer for 14 minutes"                                         # (the newest is always kept)


def test_fix2_between_slides_each_model_turn_sends_the_previous_ones_prefix_unchanged_so_core_reads_it_once_per_block():
    """The cache sees the start of the conversation change only when the window slides: ten model turns after eight routed exchanges, 3 slides' worth of change in nine steps, not nine."""
    model = Model(*[[text(f"Answer {i}.")] for i in range(10)])
    b, clock = brain(model)
    for i in range(1, 9):
        b.reply(f"set a timer for {i} minutes")
    for i in range(10):
        b.reply(f"name a thing number {i}.")
    grows = []
    for k in range(9):
        prefix = model.calls[k]["messages"][:-1]                                                              # (everything before his newest message, which carries the note)
        grows.append(model.calls[k + 1]["messages"][:len(prefix)] == prefix)
    assert grows == [False, True, True, True, False, True, True, True, False]
    assert all(valid(c["messages"][:-1]) for c in model.calls)


def test_fix2_an_offer_joins_the_memory_in_the_same_place_and_slides_the_window_like_any_exchange():
    b, clock = brain(Model())
    for i in range(1, 9):
        b.reply(f"set a timer for {i} minutes")
    assert len(b._turns) == 8
    b.offer("The harbour is on fire, monkey.", "Source: the harbour story")
    assert len(b._turns) == 5 and b._turns[-1][0]["content"].startswith("(Nobody spoke") and b._turns[-1][-1]["content"] == "The harbour is on fire, monkey."
    assert b._turns[0][0]["content"] == "set a timer for 5 minutes" and valid(stored(b))
    for i in range(3):
        b.offer(f"Story {i}.", "note")
    assert len(b._turns) == 8
    b.offer("Story 3.", "note")
    assert len(b._turns) == 5 and b.history[-1]["content"] == "Story 3."


def test_fix2_the_block_never_empties_the_memory_whatever_the_constants_are(monkeypatch):
    monkeypatch.setattr(brain_mod, "MEMORY_TURNS", 2)
    monkeypatch.setattr(brain_mod, "MEMORY_BLOCK", 4)
    b, clock = brain(Model())
    for i in range(1, 6):
        b.reply(f"set a timer for {i} minutes")
        assert 1 <= len(b._turns) <= 2 and b._turns[-1][0]["content"] == f"set a timer for {i} minutes"
    monkeypatch.setattr(brain_mod, "MEMORY_TURNS", 3)
    monkeypatch.setattr(brain_mod, "MEMORY_BLOCK", 2)
    b, clock = brain(Model())
    held = []
    for i in range(1, 8):
        b.reply(f"set a timer for {i} minutes")
        held.append(len(b._turns))
    assert held == [1, 2, 3, 2, 3, 2, 3]


def test_fix2_warm_reads_the_window_the_next_reply_sends_also_right_after_a_slide():
    model = Model(*([[text("Hi.")]] * 4))
    b, clock = brain(model, hass=FakeHouse())
    for i in range(1, 10):
        b.reply(f"set a timer for {i} minutes")
    assert len(b._turns) == 5
    assert b.warm() is not None
    b.reply("and now")
    warm_with, warm_without, reply = model.calls
    assert warm_with["messages"][:-1] == reply["messages"][:-1] and len(reply["messages"]) == 4 + 4 * 5 + 1
    assert is_plain(warm_without) and [m["content"] for m in warm_without["messages"][:2]] == ["set a timer for 5 minutes", "Timer set for five minutes."]
    assert not any("set a timer for 1 minutes" == m["content"] for m in reply["messages"] if isinstance(m["content"], str))


def test_fix2_the_model_is_never_shown_more_than_memory_turns_exchanges_whatever_the_memory_holds():
    b, clock = brain(Model())
    b._turns = [[{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}] for i in range(11)]
    assert len(b._window()) == 2 * brain_mod.MEMORY_TURNS and b._window()[0]["content"] == "q3" and b._window()[-1]["content"] == "a10"


def test_fix2_the_plain_history_is_two_messages_per_exchange_of_the_window_and_is_a_copy():
    b, clock = brain(Model(), hass=FakeHouse())
    lived(b, clock, 3)
    assert b.history == [m for i in (1, 2, 3) for m in ({"role": "user", "content": f"set a timer for {i} minutes"}, {"role": "assistant", "content": f"Timer set for {i} minutes."})]
    b.history[0]["content"] = "spoiled"
    del b.history[:2]
    assert b._turns[0][0]["content"] == "set a timer for 1 minutes" and len(b.history) == 6
    assert all(isinstance(m["content"], str) for m in b.history) and len(b._window()) == 3 * 4                                    # (the model's view of the same exchanges has the blocks)


# ── P8: forgetting, and the history setter ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix2_a_conversation_is_forgotten_after_forget_after_s_of_quiet_and_not_a_moment_before():
    model = Model([text("Hi.")], [text("Hello.")], [text("Again.")])
    b, clock = brain(model, hass=FakeHouse())
    b.reply("hello")
    clock[0] += brain_mod.FORGET_AFTER_S                                                                      # exactly that long: still one conversation
    b.reply("and again")
    assert model.calls[1]["messages"][4:6] == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi."}] and len(b._turns) == 2
    clock[0] += brain_mod.FORGET_AFTER_S + 0.5                                                                # longer: a new one
    b.reply("anyone there")
    sent = model.calls[2]["messages"]
    assert sent[:4] == list(brain_mod.EXAMPLE) and len(sent) == 5 and "hello" not in str(sent) and "and again" not in str(sent) and "Hi." not in str(sent)       # the example alone, then his words
    assert len(b._turns) == 1 and b.history == [{"role": "user", "content": "anyone there"}, {"role": "assistant", "content": "Again."}]


def test_fix2_warm_forgets_exactly_as_a_reply_does_not_a_moment_early_and_not_a_moment_late():
    model = Model(*([[text("ok")]] * 4))
    b, clock = brain(model, hass=FakeHouse())
    lived(b, clock, 2)
    clock[0] += brain_mod.FORGET_AFTER_S                                                                      # exactly that long: the window is still what the next reply will send
    assert b.warm() is not None
    assert len(model.calls[0]["messages"]) == 4 + 2 * 4 + 1 and model.calls[0]["messages"][4]["content"] == "set a timer for 1 minutes"
    clock[0] += 0.5
    assert b.warm() is not None
    assert model.calls[2]["messages"][:-1] == list(brain_mod.EXAMPLE) and len(b._turns) == 2                  # (the reply forgets it, not the warm-up)


def test_fix2_every_reply_resets_the_quiet_clock_so_a_talk_with_pauses_is_one_conversation():
    model = Model(*([[text("Yes.")]] * 4))
    b, clock = brain(model)
    for i in range(4):
        b.reply(f"question {i}")
        clock[0] += brain_mod.FORGET_AFTER_S - 100                                                            # each pause is shorter than ten minutes, together they are much longer
    assert len(b._turns) == 4 and [m["content"] for m in model.calls[3]["messages"][:6:2]] == ["question 0", "question 1", "question 2"]


def test_fix2_what_the_news_route_and_the_music_guard_read_is_nothing_once_the_talk_is_forgotten():
    from test_sat_news import Get, results
    news = brain_mod.news_mod.News({}, get=Get(results("Senate passes bill", "Governor signs law")))
    model = Model([text("Here is politics."), ])
    b, clock = brain(model, news=news)
    assert b.reply("what's the news") == brain_mod.news_mod.ASK_WHAT                                          # about what?
    clock[0] += brain_mod.FORGET_AFTER_S + 1
    assert b.reply("Politics.") == "Here is politics." and b.actions == [] and b.timing["route"] is None           # the question was forgotten: "Politics." is no answer to it
    assert [m["content"] for m in b.history] == ["Politics.", "Here is politics."]


def test_fix2_an_offer_after_a_long_quiet_starts_a_new_conversation_and_one_within_it_joins_the_old():
    b, clock = brain(Model(), hass=FakeHouse())
    lived(b, clock, 2)
    clock[0] += brain_mod.FORGET_AFTER_S - 1
    b.offer("Story one.", "note")
    assert len(b._turns) == 3 and b.offered["at"] == clock[0]
    clock[0] += brain_mod.FORGET_AFTER_S                                                                      # exactly the limit after the offer: not yet forgotten
    b.offer("Story two.", "note")
    assert len(b._turns) == 4
    clock[0] += brain_mod.FORGET_AFTER_S + 1
    b.offer("Story three.", "note")
    assert len(b._turns) == 1 and b.history[-1]["content"] == "Story three."


def pairs(*words):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": w} for i, w in enumerate(words)]


def test_fix2_the_history_setter_keeps_whole_user_and_assistant_pairs_and_drops_an_unpaired_tail():
    b, clock = brain(Model())
    b.history = pairs("a", "b", "c")
    assert [m["content"] for m in b.history] == ["a", "b"] and len(b._turns) == 1 and all(len(t) == 2 for t in b._turns)
    b.history = pairs("a", "b", "c", "d", "e")
    assert [m["content"] for m in b.history] == ["a", "b", "c", "d"] and len(b._turns) == 2
    b.history = pairs("only")
    assert b.history == [] and b._turns == []
    for empty in ([], None, (), iter(())):
        b.history = pairs("a", "b")
        b.history = empty
        assert b.history == [] and b._turns == []
    b.history = iter(pairs("a", "b", "c", "d"))                                                                # (any iterable of messages)
    assert [m["content"] for m in b.history] == ["a", "b", "c", "d"]
    b.history = tuple(pairs("x", "y"))
    assert [m["content"] for m in b.history] == ["x", "y"]


def test_fix2_the_history_setter_keeps_the_last_memory_turns_pairs_and_every_pair_is_a_user_then_an_assistant():
    b, clock = brain(Model())
    b.history = pairs(*[f"{'q' if i % 2 == 0 else 'a'}{i // 2}" for i in range(2 * 11)])
    assert len(b._turns) == brain_mod.MEMORY_TURNS == 8 and b._turns[0][0]["content"] == "q3" and b._turns[-1][1]["content"] == "a10"
    assert all(t[0]["role"] == "user" and t[1]["role"] == "assistant" for t in b._turns) and valid(stored(b))


def test_fix2_the_history_setter_does_not_misalign_on_odd_lists_and_drops_what_is_no_words():
    b, clock = brain(Model())
    stray = [{"role": "assistant", "content": "stray"}, *pairs("a", "b", "c", "d")]                            # a list that starts with the wrong role
    b.history = stray
    assert [m["content"] for m in b.history] == ["a", "b", "c", "d"] and valid(stored(b))
    b.history = pairs("a", "b") + [{"role": "user", "content": "c2"}] + pairs("c", "d")[:0] + [{"role": "user", "content": "c3"}, {"role": "assistant", "content": "d"}]
    assert [m["content"] for m in b.history] == ["a", "b", "c2\n\nc3", "d"]                                    # (two of his in a row are one message: pairs stay whole)
    with_blocks = [*pairs("a")[:1], {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "n", "input": {}}]},
                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ans"}]}, {"role": "assistant", "content": "b"}]
    b.history = with_blocks
    assert [m["content"] for m in b.history] == ["a", "b"] and valid(stored(b)) and "tool" not in str(b._turns)      # (the setter takes words: no half exchange with a call and no answer)
    b.history = ["not a message", None, 7, {"role": "user"}, {"no": "role"}]
    assert b.history == []


def test_fix2_a_history_that_was_set_is_shown_to_the_model_as_the_pairs_it_kept():
    model = Model([text("Fine.")])
    b, clock = brain(model)
    b.history = pairs("a", "b", "c", "d", "lone")
    b._last = clock[0]
    b.reply("hello")
    assert [m["content"] for m in model.calls[0]["messages"][:4]] == ["a", "b", "c", "d"] and model.calls[0]["messages"][4]["content"].endswith("\n\nhello") and len(model.calls[0]["messages"]) == 5
