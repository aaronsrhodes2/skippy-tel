"""fix3:text: what the house SAYS that a model wrote, after the second review of the house speed change (2026-11-05, round 3).

core#1  a persona annotation ([SKIPPY_LEARN: {...}] / [TTS_ADJUST: {...}]) the token limit CUT was spoken: ANNOTATION needed the closing "}]", and the cut-back to a whole sentence landed inside its text
        ("... Adds items. Reads lists."), so the rest of it went to the speaker. Brain._spoken strips an annotation (whole, without JSON, or cut) from its bracket to the end BEFORE that cut-back.
core#3  Daily.word (the morning brief, homecoming, bedtime ...) cleaned only <think> and [IMAGE: ...]: it now runs the brain's cleaning (strip_model_markup), and WORDING carries the brain's sentence about the annotations.
core#6  a stray "</think>" (and the reasoning before it), or an unclosed "<think>", was spoken.
core#7  a call written as text: arguments the tool does not have are dropped ("[music mode=weird]" ran music with a `mode`); a bracket that starts with a tool's name but cannot be read ("[set_timer 90 tea]
        Ninety seconds.") left a claim with nothing behind it, so the model is asked again; the routed wording path cuts a call written as words for ANY tool, not two.
code#2  the empty-argument rule of the text-call rescue (E6 "stands alone or only looks", E8 the pile threshold, and the clauses of _needs_arguments / _bare_action no schema of the real tools tells apart).

The tests that say "(passes on the brain before this fix too)" pin what must NOT change.
"""
from __future__ import annotations

import asyncio
import copy
import random
import re
from types import SimpleNamespace as NS

import pytest

import alarms as alarms_mod
import brain as brain_mod
import car as car_mod
import daily as daily_mod
import house as house_mod
import hub as hub_mod
import lists as lists_mod
import news as news_mod
import physics as physics_mod
import weather as weather_mod
from brain import Brain
from timers import Timers

NOW = "Thursday, November 05, 2026, 21:40"


def text(t):
    return NS(type="text", text=t)


def call(tool_name, /, cid=None, **args):
    return NS(type="tool_use", id=cid or "tu_" + tool_name, name=tool_name, input=args)


class Model:
    """A scripted model. A round is a list of blocks, or (blocks, stop_reason). Every call is kept, the messages as they were when it was made."""

    def __init__(self, *rounds):
        self.rounds, self.calls = list(rounds), []
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": copy.deepcopy(kw["messages"])})
        if not self.rounds:
            raise AssertionError("the model was asked more often than this test says")
        blocks = self.rounds.pop(0)
        blocks, stop = blocks if isinstance(blocks, tuple) else (blocks, None)
        return NS(content=blocks, stop_reason=stop)


def row(eid, name, state="off", **attrs):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": name, **attrs}}


class FakeHouse(house_mod.House):
    sensitive = True

    def __init__(self):
        self.rows = [row("light.bedroom", "Bedroom"), row("light.kitchen_under_1", "kitchen under 1"), row("lock.front_door", "Front Door", "locked")]
        self.calls = []

    def states(self, fresh=False):
        return self.rows

    def areas(self):
        return {}

    def _service(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))


def make_brain(tmp_path, *rounds, hass=True):
    """A real Brain over REAL alarms (the weekday wake-up, a dentist), REAL lists, a real pasta timer, the hub's REAL music and announce tool definitions (required: ["action"] for the music tool, like production;
    their calls recorded in `ran`) and a fake house: what a stray call would really do."""
    clock = [1_793_000_000.0]
    tmp_path.mkdir(parents=True, exist_ok=True)
    al = alarms_mod.Alarms(tmp_path / "alarms.json", clock=lambda: clock[0])
    ls = lists_mod.Lists(tmp_path / "lists.json")
    ran = []
    fakes = list(hub_mod.MUSIC_TOOLS) + [hub_mod.ANNOUNCE_TOOL]
    pairs = [al.tools("kitchen"), ls.tools(), (fakes, lambda name, args: ran.append((name, dict(args))) or f"({name} done)")]
    tools = [t for ts, _ in pairs for t in ts]

    def run_extra(name, args):
        return next(fn for ts, fn in pairs if name in {t["name"] for t in ts})(name, args)
    timers = Timers(clock=lambda: clock[0])
    model = Model(*rounds)
    house = FakeHouse() if hass else None
    b = Brain(model, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: NOW, hass=house, extra=(tools, run_extra))
    b.sleep = lambda s: None
    al.add("alarm", "07:00", "wake-up", "weekdays")
    al.add("alarm", "09:30", "dentist", "once")
    ls.run("list", {"action": "add", "list": "shopping", "items": ["milk", "eggs", "bread"]})
    timers.add(600, "pasta")
    b.world = NS(alarms=al, lists=ls, timers=timers, ran=ran, house=house)
    return b, model


@pytest.fixture
def no_routes(monkeypatch):
    """The routes are another part's business: these phrasings reach the model whatever they take."""
    for name in ("light_route", "timer_route", "music_route", "speakers_route", "device_route", "bilby_route", "announce_route"):
        if hasattr(brain_mod, name):
            monkeypatch.setattr(brain_mod, name, lambda *a, **k: None)
    for module in (brain_mod.lists_mod, brain_mod.alarms_mod):
        monkeypatch.setattr(module, "pre_route", lambda *a, **k: None)


def spoken(t, stop=None):
    return Brain._spoken(NS(content=[text(t)], stop_reason=stop))


# ── core#1: a persona annotation is never spoken, wherever the token limit cuts it ───────────────────────────────────────────────────────────────────────────────────────────────────
ANNOTATIONS = {
    "learn with periods and commas": '[SKIPPY_LEARN: {"phrase": "shopping list", "intent": "list", "description": "Adds items. Reads lists. Captain uses this for groceries, chores, and more.", "note": "He says \'milk\' a lot, I do not mind."}]',
    "tts with periods": '[TTS_ADJUST: {"stability": 0.3, "note": "He likes it calm. Keep the voice low and slow. Never shout!"}]',
    "the persona's nickname example, on three lines": ('[SKIPPY_LEARN: {"phrase": "beercan", "intent": "nickname", "canonical": "skippy",\n                "description": "Captain\'s nickname for Skippy the Magnificent",\n'
                                                       '                "note": "I find this endearing and undignified in equal measure."}]'),
    "braces and brackets inside": '[SKIPPY_LEARN: {"phrase": "a", "aliases": ["b", "c"], "meta": {"k": "v. w", "n": 1}}]',
    "no JSON in it": "[SKIPPY_LEARN: phrase=tea. intent=timer, note=none]",
    "lower case": '[skippy_learn: {"phrase": "x", "note": "y. z"}]',
}


@pytest.mark.parametrize("stop", [None, "max_tokens"])
@pytest.mark.parametrize("kind", sorted(ANNOTATIONS))
@pytest.mark.parametrize("lead, said", [("Added. Four items now. ", "Added. Four items now."), ("Sure thing, Captain ", "Sure thing, Captain"), ("", "")])
def test_fix3_an_annotation_is_never_spoken_wherever_the_token_limit_cuts_it(kind, lead, said, stop):
    """Every prefix of a real annotation, after a reply that ends a sentence, one that does not, and none at all: nothing of it is spoken (at HEAD most prefixes with a period in the cut part were)."""
    annotation = ANNOTATIONS[kind]
    leaks = {i: spoken(lead + annotation[:i], stop) for i in range(len(annotation) + 1) if spoken(lead + annotation[:i], stop) != said}
    assert leaks == {}, (kind, list(leaks.items())[:3])


@pytest.mark.parametrize("tag", ["SKIPPY_LEARN", "TTS_ADJUST"])
def test_fix3_the_words_that_end_inside_the_name_of_a_tag_are_cut_too(tag):
    for i in range(1, len(tag) + 2):
        assert spoken("Fine, monkey [" + tag[:i - 1]) == "Fine, monkey", tag[:i - 1]
        assert spoken("Fine, monkey [" + tag[:i - 1], "max_tokens") == "Fine, monkey", tag[:i - 1]
    assert spoken("Fine [S") == "Fine" and spoken("Fine [sk") == "Fine" and spoken("Fine, ok [") == "Fine, ok"            # (a lone "[" at the end is no word either)
    assert spoken("Fine [Q") == "Fine [Q" and spoken("Fine [laughs") == "Fine [laughs"                                  # (the end of the words inside a bracket that is no tag of the persona's is left alone)


def test_fix3_the_review_s_cases_a_period_in_the_cut_part_and_a_reply_that_is_only_the_annotation():
    cut = 'Added. Four items now. [SKIPPY_LEARN: {"phrase": "shopping list", "intent": "list", "description": "Adds items. Reads lists. Captain uses this for gro'
    assert spoken(cut, "max_tokens") == "Added. Four items now."
    assert spoken('Jazz it is. [TTS_ADJUST: {"stability": 0.3, "note": "He likes it calm. Keep the voice low and slow. Never', "max_tokens") == "Jazz it is."
    assert spoken('[SKIPPY_LEARN: {"phrase": "play some jazz", "intent": "play_music_jazz", "note": "Simple genre request tha', "max_tokens") == ""


def test_fix3_a_whole_annotation_in_the_middle_or_before_the_words_is_cut_out_and_the_words_stay():
    assert spoken('Done. [TTS_ADJUST: {"stability": 0.3}] Enjoy.') == "Done. Enjoy."
    assert spoken('[SKIPPY_LEARN: {"phrase": "tea"}] Tea in ninety seconds.') == "Tea in ninety seconds."
    assert spoken("Done. [SKIPPY_LEARN: phrase=tea] Enjoy.") == "Done. Enjoy."                                    # (a bracket of the tag with no JSON in it)
    assert spoken('Done. [SKIPPY_LEARN: {"a": 1}] [TTS_ADJUST: {"stability": 0.5}]') == "Done."                  # (both of the persona's, one after the other)
    assert spoken('Noted. [SKIPPY_LEARN: {"phrase": "x. y"}] More words that ran out of room and', "max_tokens") == "Noted."   # (a whole one with a period in it, then a cut: the cut-back cannot land inside it)


def test_fix3_words_that_only_look_like_an_annotation_are_left_alone():
    for plain in ("[SKIPPY_LEARNING] is not a tag.", "The TTS_ADJUST knob is on the left.", "Item [1] is done.", "Use the [IMAGE] key.", "[sighs] left for strip_stage_directions", "It is 5 [or 6] o'clock.",
                  "SKIPPY_LEARN: is how it starts."):
        assert spoken(plain) == plain, plain


def test_fix3_the_note_that_starts_his_message_is_cut_when_the_reply_copies_it_and_runs_out_of_room():
    assert spoken("Fine. [Right now: Thursday, November 05, 2026, 21:40. He is talking to you from the liv", "max_tokens") == "Fine."
    assert spoken("Fine. [Right now: Thursday, November 05, 2026, 21:40.] It is nine.") == "Fine. It is nine."          # (the whole copy was cut already: passes on the brain before this fix too)


@pytest.mark.parametrize("routed", [True, False])
def test_fix3_a_cut_annotation_never_reaches_the_speaker_on_the_routed_wording_path_nor_the_plain_path(routed, tmp_path, no_routes, monkeypatch):
    lead = "Jazz it is." if routed else "Once upon a time, a can woke up."
    cut = lead + ' [SKIPPY_LEARN: {"phrase": "play some jazz", "intent": "play_music", "description": "Plays jazz. Reads the library. Captain uses this for the ev'
    if routed:
        monkeypatch.setattr(brain_mod, "bilby_route", lambda heard: {"genre": "jazz", "shuffle": True})
    b, model = make_brain(tmp_path, ([text(cut)], "max_tokens"))
    said = b.reply("play some jazz" if routed else "tell me a story")
    assert said == lead and "SKIPPY" not in str(b._turns) and len(model.calls) == 1
    assert ("tools" not in model.calls[0]) if routed else ("tools" in model.calls[0])                                  # (the routed wording call offers no tools; the plain path does)


def test_fix3_a_reply_that_is_only_a_cut_annotation_is_nothing_said_not_the_annotation(tmp_path, no_routes):
    b, model = make_brain(tmp_path, ([text('[SKIPPY_LEARN: {"phrase": "play some jazz", "intent": "x", "note": "Simple genre request tha')], "max_tokens"))
    assert b.reply("tell me a story") == "Hm. I had nothing to say, which is unlike me." and "SKIPPY" not in str(b._turns)


def test_fix3_the_second_try_after_a_claim_never_returns_an_annotation(tmp_path, no_routes):
    """The second try's words were returned as _spoken gave them and nothing took the persona's annotation out of them on that path (the claim path: every tool on offer, no gate): it was spoken."""
    b, model = make_brain(tmp_path, [text("Alarm set for seven.")], [call("set_alarm", time="07:00")],
                          [text('Alarm set for seven, Captain. [SKIPPY_LEARN: {"phrase": "wake me at seven", "intent": "set_alarm", "description": "Sets the wake-up. Reads it back."}]')])
    said = b.reply("wake me up at seven")
    assert said == "Alarm set for seven, Captain." and "SKIPPY" not in str(b._turns) and [a["tool"] for a in b.actions] == ["set_alarm"]


# ── core#6: Core's reasoning is never spoken ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, said", [
    ("<think>I should say hello.</think> Hello, Captain.", "Hello, Captain."),                            # a whole block
    ("<think>\nTwo lines.\nOf thought.\n</think>\n\nIt is nine.", "It is nine."),
    ("<THINK>x</THINK>It is nine.", "It is nine."),                                                          # (any case)
    ("It is <THINK>x</THINK> nine.", "It is nine."),                                                         # (a block in the middle, in capitals: the lone-close rule must not be the one that takes it)
    ("<think reason=1>x</think> It is nine.", "It is nine."),
    ("It is <think>x</think> nine <think>y</think>o'clock.", "It is nine o'clock."),                        # (two blocks, the words around them stay and do not run together)
    ("Thinking it over.</think> It is nine.", "It is nine."),                                                # a lone closing tag, and all before it
    ("[music]\nnext\n</think>\n\n[music]\nnext", "[music]\nnext"),                                            # (the real Core, 2 of 20 text-call replies)
    ("The reasoning. </think>", ""),
    ("</think>It is nine.", "It is nine."),
    ("It is nine. <think>and then I would say", "It is nine."),                                              # an unclosed opening tag, and all after it
    ("<think>I am still thinking about", ""),
    ("<think>a</think>It is nine. <think>b", "It is nine."),
    ("Reasoning </think> answer <think>more reasoning", "answer"),
    ("I think so. Let me think about it. The thinking man's answer.", "I think so. Let me think about it. The thinking man's answer."),      # (the word, not the tag: passes on the brain before this fix too)
    ("Use <thinking>this</thinking> tag.", "Use <thinking>this</thinking> tag."),                              # (not Core's tag)
])
def test_fix3_core_s_reasoning_is_never_in_the_words(raw, said):
    assert spoken(raw) == said and spoken(raw, "max_tokens") == said


def test_fix3_a_stray_closing_tag_is_not_spoken_end_to_end(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("Let me see. Nine, I think.</think> It is nine o'clock, monkey.")])
    assert b.reply("what time is it") == "It is nine o'clock, monkey." and "think" not in str(b._turns)
    b, model = make_brain(tmp_path / "only", [text("<think>I should say something clever.</think>")])
    assert b.reply("what time is it") == "Hm. I had nothing to say, which is unlike me." and "think" not in str(b._turns)


def test_fix3_a_text_call_with_a_stray_closing_tag_still_runs_once(tmp_path, no_routes):
    """The rescue read both brackets and ran the one call once before too; now the reasoning is gone before it looks, so neither the call's wording nor the memory is shown it."""
    b, model = make_brain(tmp_path, [text("[music]\nnext\n</think>\n\n[music]\nnext")], [text("Skipped.")])
    assert b.reply("could you skip to the next track") == "Skipped." and b.world.ran == [("music", {"action": "next"})] and "think" not in str(model.calls[1]["messages"])


def test_fix3_the_markup_cleaning_has_no_exceptions_and_leaves_no_annotation_or_tag_on_random_text():
    pieces = ["Done. ", "It is nine", " and ", ". ", "? ", "!", "[", "]", "{", "}", "[SKIPPY_LEARN: ", "[TTS_ADJUST: ", '{"phrase": "a. b", "n": 1}', "}]", "<think>", "</think>", "<tool_call>", "</tool_call>",
              "<function=weather>", "</function>", "<parameter=when>", "[IMAGE: a sunny sky", "[Right now: x", "\n", " ", "monkey", "[sighs]", "*sighs*", "[SKIPPY_", "LEARN", "[T"]
    rng = random.Random(20261105)
    bad = []
    for _ in range(4000):
        raw = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 14)))
        for stop in (None, "max_tokens"):
            out = spoken(raw, stop)
            if re.search(r"\[(?:SKIPPY_LEARN|TTS_ADJUST)\b", out, re.I) or re.search(r"</?think|<tool_call|<function=|<parameter=|\[IMAGE:|\[Right now:", out, re.I):
                bad.append((raw, stop, out))
    assert bad == [], bad[:3]


# ── core#3: the routines (Daily.word) say what a reply does ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class Core:
    """Core, faked: answers one text, and keeps what it was asked."""

    def __init__(self, said):
        self.said, self.asked = said, []
        self.messages = self

    def create(self, **kw):
        self.asked.append(kw)
        return NS(content=[NS(type="text", text=self.said)], stop_reason="end_turn")


def routine_word(tmp_path, said, **kw):
    core = Core(said)
    d = daily_mod.Daily({}, client=core, model="m", persona="PERSONA", state_dir=tmp_path, log=lambda m: None)
    return d.word("Give him today's weather.", "Material: sunny, 66.", **kw), core


@pytest.mark.parametrize("raw, said", [
    ('Good morning, Captain. Sunny and 66. [SKIPPY_LEARN: {"phrase": "good morning", "intent": "morning", "note": "he likes it"}]', "Good morning, Captain. Sunny and 66."),
    ('Sunny and 66. [TTS_ADJUST: {"stability": 0.3}]', "Sunny and 66."),
    ('Sunny and 66. [SKIPPY_LEARN: {"phrase": "weather", "intent": "weather", "description": "Reads it. Says it. Captain uses this for the mor', "Sunny and 66."),       # (cut by the token limit, a period inside)
    ("<tool_call><function=weather><parameter=when>today</parameter></function></tool_call> Sunny and 66.", "Sunny and 66."),
    ("Sunny and 66. <tool_call><function=weather>", "Sunny and 66."),
    ("Reasoning about the weather.</think> Sunny and 66.", "Sunny and 66."),
    ("<think>hm</think>Sunny and 66.", "Sunny and 66."),                                                    # (what it did before: passes on the brain before this fix too)
    ("Sunny and 66. [IMAGE: a sunny sky over the desert]", "Sunny and 66."),                               # (ditto)
    ("Sunny and 66. [IMAGE: a sunny sky. Over the des", "Sunny and 66."),                                               # (cut inside the sentinel: it used to need the closing bracket)
    ("Sunny, with a high of 66 and a low of 48. Bring a layer.", "Sunny, with a high of 66 and a low of 48. Bring a layer."),         # (nothing to clean: exactly as it was)
])
def test_fix3_a_routine_says_no_annotation_no_tool_markup_no_reasoning(tmp_path, raw, said):
    got, _ = routine_word(tmp_path, raw)
    assert got == said


def test_fix3_a_routine_that_was_only_leftovers_has_no_words_and_the_plain_material_is_said_instead(tmp_path):
    got, _ = routine_word(tmp_path, '[SKIPPY_LEARN: {"phrase": "x", "note": "y. z')
    assert got is None
    d = daily_mod.Daily({}, client=Core("<think>hm</think>"), model="m", persona="PERSONA", state_dir=tmp_path, log=lambda m: None)
    assert asyncio.run(d.aword("Give him today's weather.", "Material: sunny, 66.", fallback="Sunny and 66, plain.")) == "Sunny and 66, plain."          # (aword's fallback is the plain sourced material)


def test_fix3_wording_carries_the_brain_s_sentence_about_the_annotations_and_nothing_else_of_it_changed(tmp_path):
    sentence = "write [SKIPPY_LEARN] or [TTS_ADJUST] here (nothing strips them: they would be read aloud)"
    assert sentence in brain_mod.SPOKEN and sentence in daily_mod.WORDING                                       # (the same sentence)
    before = (" You are speaking aloud in his house, so: plain spoken sentences, no lists, no markdown, no emoji, no URLs. Say only what the material "
              "below says; never add a fact, a number or a detail of your own. Keep it short. Stay in character.")
    assert daily_mod.WORDING == before + " Never write [SKIPPY_LEARN] or [TTS_ADJUST] here (nothing strips them: they would be read aloud)."
    got, core = routine_word(tmp_path, "Sunny and 66.")
    assert got == "Sunny and 66." and core.asked[0]["system"] == "PERSONA" + daily_mod.WORDING and core.asked[0]["max_tokens"] == 400
    got, core = routine_word(tmp_path, "Serendipity", persona=False)
    assert core.asked[0]["system"] == "Answer exactly as asked, nothing else." and got == "Serendipity"           # (the pick of a word: no persona, no wording)


def test_fix3_a_routine_without_a_model_says_nothing_as_before(tmp_path):
    d = daily_mod.Daily({}, client=None, model="m", persona="PERSONA", state_dir=tmp_path, log=lambda m: None)
    assert d.word("x", "y") is None


# ── core#7: a call written as text ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix3_a_call_written_with_a_key_the_tool_does_not_have_is_not_run_as_written(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[music mode=weird] Down a notch.")], [call("music", action="quieter")], [text("Quieter.")])
    assert b.reply("turn the music down a notch") == "Quieter." and b.world.ran == [("music", {"action": "quieter"})] and len(model.calls) == 3         # (before: ran music {"mode": "weird"})
    assert model.calls[1]["messages"][-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=" (music)"))


def test_fix3_only_the_keys_the_tool_has_are_run(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[music action=next mode=weird] Skipping.")], [text("Skipped.")])
    assert b.reply("could you skip to the next track") == "Skipped." and b.world.ran == [("music", {"action": "next"})]                             # (before: {"action": "next", "mode": "weird"})
    b, model = make_brain(tmp_path / "two", [text("[play_music: what=everything, speaker=everywhere, flavor=spicy] Done.")], [text("Playing.")])
    assert b.reply("play everything everywhere") == "Playing." and b.world.ran == [("play_music", {"what": "everything", "speaker": "everywhere"})]
    b, model = make_brain(tmp_path / "three", [text('[list action="read" list="shopping" colour="red"]')], [text("Milk, eggs and bread.")])
    assert b.reply("what is on my shopping list") == "Milk, eggs and bread." and [(a["tool"], a["args"]) for a in b.actions] == [("list", {"action": "read", "list": "shopping"})]


def test_fix3_a_look_with_a_foreign_key_still_runs_alone_with_no_arguments(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[list_timers foo=1]")], [text("Pasta, ten minutes.")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes." and [(a["tool"], a["args"]) for a in b.actions] == [("list_timers", {})]


def test_fix3_a_tool_s_arguments_are_the_properties_of_its_schema(tmp_path):
    b, _ = make_brain(tmp_path)
    assert set(b._tool_props("music")) == {"action", "level"} and set(b._tool_props("set_timer")) == {"seconds", "label"} and set(b._tool_props("play_music")) >= {"what", "genre", "speaker"}
    assert b._tool_props("nonsense") == {} and b._tool_props("list_timers") == {}


def test_fix3_a_bracket_with_positional_arguments_is_a_call_that_was_meant_the_model_is_asked_again(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[set_timer 90 tea] Ninety seconds.")], [call("set_timer", seconds=90, label="tea")], [text("Tea, ninety seconds.")])
    assert b.reply("a ninety second tea timer") == "Tea, ninety seconds." and len(model.calls) == 3                                           # (before: one model call, "Ninety seconds." and no timer)
    assert ("tea", 90) in b.world.timers.remaining() and [a["tool"] for a in b.actions] == ["set_timer"]
    again = model.calls[1]["messages"]
    assert again[-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=" (set_timer)")) and model.calls[1]["tools"] == b.tools() and "[set_timer" not in str(again)


def test_fix3_a_claim_that_came_with_an_unreadable_bracket_and_no_tool_is_not_spoken(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[set_timer 90 tea] Ninety seconds.")], [text("Done.")])
    said = b.reply("a ninety second tea timer")
    assert said == brain_mod.UNCLAIM and b.actions == [] and b.world.timers.remaining() == [("pasta", 600)]                                   # (before: "Ninety seconds.", a timer that does not exist)
    b, model = make_brain(tmp_path / "honest", [text("[set_timer 90 tea] Ninety seconds.")], [text("I did not set it. For how long, exactly?")])
    assert b.reply("a ninety second tea timer") == "I did not set it. For how long, exactly?"


def test_fix3_two_unreadable_tool_brackets_are_a_pile_and_the_nudge_names_no_tool(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[set_timer 90 tea] [play_music jazz] Ninety seconds, and jazz.")], [text("Which one first, Captain?")])
    assert b.reply("tea and jazz") == "Which one first, Captain?" and b.actions == [] and b.world.ran == []
    assert model.calls[1]["messages"][-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=""))                                          # (one tool is named, two are anyone's guess)


def test_fix3_a_reply_that_is_only_an_unreadable_call_is_asked_again_not_nothing_to_say(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[play_music: genre=jazz, speaker=living room]")], [call("play_music", genre="jazz", speaker="upstairs")], [text("Jazz upstairs.")])
    assert b.reply("play some jazz in the living room") == "Jazz upstairs." and b.world.ran == [("play_music", {"genre": "jazz", "speaker": "upstairs"})]       # (before: "Hm. I had nothing to say")


def test_fix3_a_bracket_that_is_no_tool_of_ours_is_left_alone_and_a_bare_play_music_still_plays(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[nonsense a=1] Sure, a story then.")])
    assert b.reply("tell me a story") == "[nonsense a=1] Sure, a story then." and len(model.calls) == 1                                       # (passes on the brain before this fix too)
    b, model = make_brain(tmp_path / "play", [text("[play_music] Playing.")], [text("Playing.")])
    assert b.reply("play something") == "Playing." and b.world.ran == [("play_music", {})] and len(model.calls) == 2                            # (ditto: a call with no arguments, on purpose)


def test_fix3_a_stage_direction_that_is_an_action_word_is_wiped_not_a_call_that_was_meant(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[pause] Fine. It is twenty to ten.")])
    assert b.reply("what time is it") == "Fine. It is twenty to ten." and len(model.calls) == 1 and b.actions == []                            # (no second model call for it)
    b, model = make_brain(tmp_path / "digit", [text("Fine. [next 2] It is twenty to ten.")])                                                  # (an action word with a digit is no stage direction: the wipe takes it, strip_stage_directions would not)
    assert b.reply("what time is it") == "Fine. It is twenty to ten." and len(model.calls) == 1 and b.actions == []
    assert b._tool_brackets("[set_timer 90 tea] and [pause] and [/music] and [nonsense a=1] and [music (next) now] and [Music]", {t["name"] for t in b.tools()}) == ["set_timer", "music"]


def test_fix3_inside_a_second_try_the_unreadable_bracket_is_wiped_and_the_model_is_not_asked_a_third_time(tmp_path, no_routes):
    """(passes on the brain before this fix too) The denial's retry is optional and the room is deaf while it waits: a call it writes as words that cannot be read is dropped, the first reply stands."""
    b, model = make_brain(tmp_path, [text("I cannot set timers from here.")], [text("[set_timer 90 tea] Ninety seconds.")])
    assert b.reply("I need a ninety second tea timer") == "I cannot set timers from here." and len(model.calls) == 2 and b.actions == []


@pytest.mark.parametrize("name", ["music", "set_timer", "list", "home_control", "audiobook", "announce", "news", "weather", "set_alarm", "cancel_alarm", "speakers", "dj_mode", "spotify", "web_search",
                                  "ask_mentat", "car_status", "play_music", "cancel_timers", "list_timers", "home_state", "home_devices", "set_reminder", "list_alarms"])
def test_fix3_the_routed_wording_path_cuts_a_call_written_for_any_tool(name, tmp_path, no_routes, monkeypatch):
    monkeypatch.setattr(brain_mod, "bilby_route", lambda heard: {"genre": "jazz", "shuffle": True})
    b, model = make_brain(tmp_path, [text(f"[{name} x=1, y=two words] Jazz it is, Captain. [{name}]")])
    b.news, b.weather, b.car, b.physics = news_mod.News({}), weather_mod.Weather({}), car_mod.Car({}), physics_mod.Physics({})           # (the tools that only a configured brain has)
    assert name in {t["name"] for t in b.tools()}
    assert b.reply("play some jazz").strip() == "Jazz it is, Captain." and "[" not in b.history[-1]["content"], name


def test_fix3_phantom_still_knows_mentat_and_the_car_in_a_brain_that_has_neither(tmp_path):
    """(the two tools PHANTOM knew before) A brain with no car and no Mentat still never says a call written for them."""
    b, _ = make_brain(tmp_path)
    assert "ask_mentat" not in {t["name"] for t in b.tools()} and "car_status" not in {t["name"] for t in b.tools()}
    assert b._phantom("[ask_mentat: What is the half-life of carbon-14?]") == "" and b._phantom("[car_status about=charge] Charged.") == " Charged."


def test_fix3_phantom_knows_every_tool_the_brain_has_and_nothing_else(tmp_path):
    b, _ = make_brain(tmp_path)
    b.news, b.weather, b.car, b.physics = news_mod.News({}), weather_mod.Weather({}), car_mod.Car({}), physics_mod.Physics({})
    names = {t["name"] for t in b.tools()}
    assert {"music", "play_music", "set_timer", "list", "announce", "news", "weather", "car_status", "ask_mentat", "web_search", "home_control", "audiobook"} <= names
    for name in names:
        assert b._phantom(f"Mentat missed. [{name} question=\"x\"] Ask me in other words.") == "Mentat missed.  Ask me in other words.", name       # (the two spaces: the old test of test_sat_physics pins them)
        assert b._phantom(f"[{name.upper()}: a b c]") == "", name
    for plain in ("[nonsense a=1] hello", "[sighs] hello", "[Right now: Thursday] hello", "a [music" , "[musical a=1] hello", "[list_of_things] hello"):
        assert b._phantom(plain) == plain, plain
    assert brain_mod.phantom_pattern(()).search("[anything]") is None and brain_mod.PHANTOM.search("[ask_mentat: x]") and not brain_mod.PHANTOM.search("[music x=1]")


def test_fix3_mentat_s_wording_cuts_any_call_written_as_words_too(tmp_path):
    b, model = make_brain(tmp_path, [text("[set_timer 90 tea] About five kilometers per second. [music action=next]")])
    b.run_tool = lambda name, args: "5.03 km/s"                                                                  # (no real Mentat)
    assert b._ask_mentat_now("how fast is escape velocity at Mars") == "About five kilometers per second."


# ── code#2: the empty-argument rule of the text-call rescue (E6, E8, and the clauses no real schema tells apart) ─────────────────────────────────────────────────────────────────────────
def test_fix3_e6_a_call_with_no_arguments_does_not_run_beside_other_brackets_unless_it_only_looks(tmp_path, no_routes):
    """E6: without the "stands alone or only looks" clause "[play_music] [list_timers]" ran play_music with NO arguments (the music starts) beside the look."""
    b, model = make_brain(tmp_path, [text("[play_music] [list_timers]")], [text("Pasta, ten minutes.")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes." and [a["tool"] for a in b.actions] == ["list_timers"] and b.world.ran == [] and len(model.calls) == 2
    b, model = make_brain(tmp_path / "swapped", [text("[list_timers] [play_music]")], [text("Pasta, ten minutes.")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes." and [a["tool"] for a in b.actions] == ["list_timers"] and b.world.ran == []
    b, model = make_brain(tmp_path / "alone", [text("[play_music]")], [text("Playing.")])
    assert b.reply("play something") == "Playing." and b.world.ran == [("play_music", {})]                                                      # (alone, it runs with none: on purpose)


def test_fix3_e8_two_brackets_that_cannot_be_read_are_a_pile_and_nothing_runs_beside_them(tmp_path, no_routes):
    """E8: the pile is TWO lost tools (len(lost) >= 2): with a threshold of three the readable call beside two unreadable ones ran from the first reply."""
    b, model = make_brain(tmp_path, [text("[play_music] [announce] [music action=next]")], [text("Which one, Captain?")])
    assert b.reply("do the thing") == "Which one, Captain?" and b.world.ran == [] and b.actions == [] and untouched_lists(b)
    assert model.calls[1]["messages"][-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=""))                                             # (nothing is named: which tool he meant is anyone's guess in a pile)
    b, model = make_brain(tmp_path / "look", [text("[play_music] [announce] [list_timers]")], [text("Which one, Captain?")])
    assert b.reply("do the thing") == "Which one, Captain?" and b.actions == [] and len(model.calls) == 2                                        # (even a look beside two lost tools waits)


def test_fix3_e8_one_bracket_that_cannot_be_read_beside_a_readable_one_runs_the_readable_one(tmp_path, no_routes):
    """The other side of the threshold: ONE lost tool is no pile, the call that has its argument runs, the lost one does not."""
    b, model = make_brain(tmp_path, [text("[announce] [music action=next]")], [text("Skipped.")])
    assert b.reply("skip to the next track") == "Skipped." and b.world.ran == [("music", {"action": "next"})] and len(model.calls) == 2


def untouched_lists(b):
    return b.world.lists.lists == {"shopping": ["milk", "eggs", "bread"]} and len(b.world.alarms.items) == 2


def _custom(tmp_path, tools):
    model = Model()
    b = Brain(model, "m", Timers(), persona="PERSONA", extra=(tools, lambda name, args: "(done)"))
    return b


def _tool(name, required=None, action_enum=None, props=None):
    properties = dict(props or {})
    if action_enum:
        properties["action"] = {"type": "string", "enum": list(action_enum)}
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return {"name": name, "description": name, "input_schema": schema}


def test_fix3_each_clause_of_needs_arguments_decides_alone(tmp_path):
    """The real tools have a `required` AND an `action` enum together, so no test with them tells the two clauses (nor RETRY_NEVER) apart: tools that have only one of them do."""
    b = _custom(tmp_path, [_tool("needs_required", required=["a"], props={"a": {"type": "string"}}), _tool("needs_enum", action_enum=["go", "stop"]), _tool("announce", props={"text": {"type": "string"}}),
                           _tool("cancel_alarm", props={"what": {"type": "string"}}), _tool("needs_nothing", props={"a": {"type": "string"}})])
    for name in ("needs_required", "needs_enum", "announce", "cancel_alarm", "cancel_timers"):             # (cancel_timers is the brain's own, with no required argument: it is RETRY_NEVER too)
        assert b._needs_arguments(name), name
    assert not b._needs_arguments("needs_nothing") and not b._needs_arguments("list_timers") and not b._needs_arguments("not_a_tool")


def test_fix3_a_bare_word_is_never_read_as_the_action_of_a_tool_that_does_too_much_with_one(tmp_path):
    """E12: announce and cancel_alarm are never run from a bare word, even if their schema had an action to choose."""
    b = _custom(tmp_path, [_tool("announce", action_enum=["mute", "unmute"], props={"text": {"type": "string"}}), _tool("cancel_alarm", action_enum=["all", "one"]), _tool("mood", action_enum=["calm", "wild"])])
    assert b._bare_action("announce", "mute[/announce]") == ({}, 0) and b._bare_action("cancel_alarm", "all[/cancel_alarm]") == ({}, 0)
    assert b._bare_action("mood", "calm[/mood] There.") == ({"action": "calm"}, len("calm[/mood]"))


def test_fix3_the_fake_music_tool_of_the_other_files_has_the_real_one_s_schema_here(tmp_path):
    """The music tool this file runs against is the hub's own (required: ["action"], an `action` enum): the review's E4 and E12 survived only on schemas that lack one of them."""
    music = next(t for t in hub_mod.MUSIC_TOOLS if t["name"] == "music")
    assert music["input_schema"]["required"] == ["action"] and "next" in music["input_schema"]["properties"]["action"]["enum"]
    b, _ = make_brain(tmp_path)
    assert b._needs_arguments("music") and b._needs_arguments("set_timer") and b._needs_arguments("cancel_alarm") and b._needs_arguments("announce")


# ── whatever the model writes, the reply is words: a random-text property test of the whole chain (reply -> _spoken -> text calls -> the guards) ────────────────────────────────────────────────
def test_fix3_whatever_the_model_writes_no_call_runs_with_an_argument_the_tool_lacks_or_with_none_it_needs_and_nothing_markup_is_said(tmp_path, no_routes):
    pieces = ["Done. ", "It is nine. ", "Sure, Captain. ", "Skipping. ", "[music] ", "[music action=next] ", "[music mode=weird] ", "[music]next[/music] ", "[music] with action \"quieter\" ", "[set_timer 90 tea] ", "[set_timer seconds=90] ",
              "[play_music] ", "[play_music: genre=jazz, speaker=living room] ", "[list_timers] ", "[list_timers foo=1] ", "[announce] ", "[announce text=hi] ", "[cancel_alarm] ", "[cancel_timers] ", "[home_control name=bedroom] ",
              "[nonsense a=1] ", "[pause] ", "[volume 5] ", "[sighs] ", "*sighs* ", "[/music] ", "<think>hm</think> ", "</think> ", "<think>unclosed ", "<tool_call><function=weather></function></tool_call> ",
              "[SKIPPY_LEARN: {\"a\": \"b. c\"}] ", "[SKIPPY_LEARN: {\"a\": \"b. c", "[TTS_ADJUST: {\"stability\": 0.3}]", "[IMAGE: a sky", "action: next\n", "next\n", "\n"]
    rng = random.Random(1105)
    bad = []
    for n in range(260):
        first = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 6)))
        stop = rng.choice([None, "max_tokens"])
        again = rng.choice([[text("There.")], [text("Which one, Captain?")], [text("I did not do it. Ask me again.")], [call("music", action="next"), ], [call("set_timer", seconds=60, label="x")]])
        rounds = [([text(first)], stop), *[again, [text("Done, then.")], [text("Done, then.")], [text("Done, then.")], [text("Done, then.")]]]
        b, model = make_brain(tmp_path / f"n{n}", *rounds)
        try:
            said = b.reply(rng.choice(["could you skip to the next track", "what timers do I have", "tell me a story", "turn the music down a notch"]))
        except Exception as e:                                           # noqa: BLE001
            bad.append((first, stop, "raised", repr(e)))
            continue
        names = {t["name"] for t in b.tools()}
        if not isinstance(said, str) or not said.strip():
            bad.append((first, stop, "empty", said))
        if re.search(r"\[(?:SKIPPY_LEARN|TTS_ADJUST|IMAGE)|</?think|<tool_call|<function=|<parameter=", said, re.I) or re.search(r"\[(?:" + "|".join(sorted(names)) + r")\b", said):
            bad.append((first, stop, "markup said", said))
        for a in b.actions:                                               # (the scripted second rounds call music next and set_timer 60 with their own keys: a call read from the text must hold the tool's keys only too)
            if not set(a["args"]) <= set(b._tool_props(a["tool"])):
                bad.append((first, stop, "foreign key", a))
            if b._needs_arguments(a["tool"]) and not a["args"]:
                bad.append((first, stop, "ran empty", a))
    assert bad == [], bad[:3]
