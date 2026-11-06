"""fix4:text: what the house SAYS that a model wrote, after the third review of the house speed change (2026-11-06, round 4).

core3#1   Core sometimes writes its tool call as TEXT INSIDE its reasoning ("[music]" and the line "music(action='quieter')", or "[music action=quieter]") and repeats only prose after the closing tag. Round 3 threw the reasoning
          away with the call in it: nothing ran, the Captain heard a false claim (1 of 16 live; 6 of 8 shapes offline; about 3 percent of text-call turns). strip_model_markup(words, names) puts such a call back in front of the
          words, for Brain._spoken, as the text-call rescue (Brain._text_calls) reads it: only a bracket that starts with the name of a tool the brain has (b), never a call that undoes or announces (c), never one the words hold
          themselves nor one twice (d), the words cleaned exactly as before (e) and a reply that is only reasoning with no such call still "Hm. I had nothing to say" (f). Only while nothing has run in the reply.
core3#10  THINK, THINK_CLOSE, THINK_OPEN, NOTE_ECHO, NOTE_CUT and the stray tags of TOOL_MARKUP were quadratic on many unclosed tags ('<think>x' * 20000 took 11.5 s): they end at the end of the text now, with the same results.
code3#3   A04 (ANNOTATION's JSON branch is not greedy), A22b (a reply cut inside "[IMAGE" speaks no stub) and the row with two annotations and real words between them.
code3#7   a reply the token limit cut inside the name of one of Core's XML tags ("Sure thing <func") or inside the start of the clock note ("[Right no") spoke the stub when no sentence end came before it (CUT_XML, NOTE_STUB).
          (code3#6, a "}]" inside a JSON string and a tag with no colon followed by JSON, is not done: adversarial strings only, and a fix that reads quotes can lose real words from a malformed annotation.)

The tests that say "(passes on the brain before this fix too)" pin what must NOT change.
"""
from __future__ import annotations

import copy
import random
import re
import time
from types import SimpleNamespace as NS

import pytest

import alarms as alarms_mod
import brain as brain_mod
import daily as daily_mod
import house as house_mod
import hub as hub_mod
import lists as lists_mod
from brain import Brain, strip_model_markup
from timers import Timers

NOW = "Thursday, November 05, 2026, 21:40"
NAMES = frozenset({"music", "play_music", "speakers", "dj_mode", "spotify", "audiobook", "set_timer", "cancel_timers", "list_timers", "set_alarm", "list_alarms", "cancel_alarm", "set_reminder", "list", "announce",
                   "home_control", "home_state", "home_devices", "weather", "news", "web_search"})


def text(t):
    return NS(type="text", text=t)


def call(tool_name, /, cid=None, **args):
    return NS(type="tool_use", id=cid or "tu_" + tool_name, name=tool_name, input=args)


class Model:
    """A scripted model. A round is a list of blocks, or (blocks, stop_reason). Every call is kept, the messages as they were when it was made. `then`: what it answers once the rounds are used up
    (None: it fails the test, the model was asked more often than the test says)."""

    def __init__(self, *rounds, then=None):
        self.rounds, self.calls, self.then = list(rounds), [], then
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": copy.deepcopy(kw["messages"])})
        if not self.rounds:
            if self.then is None:
                raise AssertionError("the model was asked more often than this test says")
            return NS(content=[text(self.then)], stop_reason=None)
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


def make_brain(tmp_path, *rounds, then=None):
    """A real Brain over REAL alarms (the weekday wake-up, a dentist), REAL lists, a real pasta timer, the hub's REAL music and announce tool definitions (required: ["action"] for the music tool, like production; their calls
    recorded in `ran`) and a fake house: what a stray call would really do."""
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
    model = Model(*rounds, then=then)
    b = Brain(model, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: NOW, hass=FakeHouse(), extra=(tools, run_extra))
    b.sleep = lambda s: None
    al.add("alarm", "07:00", "wake-up", "weekdays")
    al.add("alarm", "09:30", "dentist", "once")
    ls.run("list", {"action": "add", "list": "shopping", "items": ["milk", "eggs", "bread"]})
    timers.add(600, "pasta")
    b.world = NS(alarms=al, lists=ls, timers=timers, ran=ran)
    return b, model


def untouched(b) -> bool:
    """The house as make_brain left it: both alarms, the shopping list, the pasta timer, nothing announced."""
    return (len(b.world.alarms.items) == 2 and b.world.lists.lists["shopping"] == ["milk", "eggs", "bread"] and b.world.timers.remaining() == [("pasta", 600)]
            and not any(name == "announce" for name, _ in b.world.ran))


def tools_run(b) -> list:
    return [(a["tool"], a["args"]) for a in b.actions]


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


# What the real Core wrote (the reviews' saved strings: annot_run1, textcall_a, textcall_r3 ...) and what the cleaning of round 3 made of each: with or without the tool names, the words are exactly these (the two that held a
# call inside the reasoning are below). A string cut by the token limit ends where the model's reply did.
REAL_CORE = [
    ('[sighs] On it. {"name": "home_control", "arguments": {"name": "living room lights", "action": "off"}} </think> *click* Done. Dark and brooding, just how you like it. I have not played, moved or changed any music.', '*click* Done. Dark and brooding, just how you like it. I have not played, moved or changed any music.'),   # think
    ('[list_timers]\n</think>\n\n', ''),   # think
    ('[sighs] Right. Because apparently "relaxing" is a genre now. Fine. Playing some chillwave upstairs. There. Happy?\n\n[SKIPPY_LEARN: {"phrase": "relaxing", "intent": "chill_music", "description": "Captain wants ambient/chill music when he says \'relaxing\'", "note": "He means chillwave or lo-fi, not actual relaxation techniques."}]\n</think>\n\n♫ Playing chillwave on the Enclave.', '♫ Playing chillwave on the Enclave.'),   # think
    ("[music] Quieter it is. The Captain's ears shall be spared for now. *sigh* Humans and their fragile hearing...\n</think>\n\n[music] Quieter it is. The Captain's ears shall be spared for now. *sigh* Humans", "[music] Quieter it is. The Captain's ears shall be spared for now. *sigh* Humans"),   # think
    ("[music]\nmusic(action='quieter')\n[speak]\nQuieting it for you, Captain. You're welcome for the temporary reprieve from MDFMK's excellent but relentless energy.\n</think>\n\nQuieting it for you, Captain. Yo", 'Quieting it for you, Captain. Yo'),   # think
    ('[music]\nnext\n</think>\n\n[music]\nnext', '[music]\nnext'),   # think
    ('[SKIPPY_LEARN: {"phrase": "throne light", "intent": "nickname", "canonical": "bedroom lamp", "description": "Captain\'s nickname for the bedroom lamp", "note": "I suppose everyone needs their little throne."}]', ''),   # annotation
    ('[sighs] Adjusting my vocal cadence for your biological processing limitations...\n\n[TTS_ADJUST: {"stability": 0.7}]', '[sighs] Adjusting my vocal cadence for your biological processing limitations...'),   # annotation
    ('Setting a timer for 10 minutes labeled "pasta".\n[SKIPPY_LEARN: {"phrase": "pasta timer", "intent": "set_pasta_timer", "description": "Set a ', 'Setting a timer for 10 minutes labeled "pasta".'),   # annotation
    ('Mars needs five point zero three kilometers per second to shake off its gravity, according to NASA, via Mentat. [TTS_ADJUST: {"stability": 0.1}]', 'Mars needs five point zero three kilometers per second to shake off its gravity, according to NASA, via Mentat.'),   # annotation
    ('I\'ll turn on the bedroom light for you, Captain.\n\n[SKIPPY_LEARN: {"phrase": "lamp on in the bedroom", "intent": "bedroom', "I'll turn on the bedroom light for you, Captain."),   # annotation
    ('Jazz it is. [IMAGE: a dimly lit jazz club with a saxophone player on stage]', 'Jazz it is.'),   # image
    ('I cannot skip tracks if nothing is playing, Captain. Shall I start something? [IMAGE: A vinyl record spinning in the dar', 'I cannot skip tracks if nothing is playing, Captain. Shall I start something?'),   # image
    ('<tool_call>\n<function=play_music>\n<parameter=query>\njazz\n</parameter>\n<parameter=shuffle>\nlibrary\n</parameter>\n</function>\n</tool_call>', ''),   # toolcall
    ('<tool_call>\n<function=list>\n<parameter=action>\nappend\n</parameter>\n<parameter=item>\nmilk\n</parameter>\n<parameter=list>\nshopping\n</parameter>\n</function>\n</tool_call>', ''),   # toolcall
    ('<tool_call>\n<function=audiobook>\n<parameter=action>\nwhere\n</parameter>\n</function>\n</tool_call>', ''),   # toolcall
    ('<tool_call>\n<function=set_alarm>\n<parameter=hour>\n7\n</parameter>\n<parameter=minute>\n0\n</parameter>\n<parameter=label>\nWake up\n</parameter>\n</function>\n</tool_call>', ''),   # toolcall
    ('<tool_call>\n<function=play_music>\n<parameter=genre>\njazz\n</parameter>\n<parameter=shuffle>\ntrue\n</parameter>\n<parameter=speaker>\nEnclave\n</parameter>\n</function>\n</tool_call>', ''),   # toolcall
    ('[music]next[/music]', '[music]next[/music]'),   # toolbracket
    ("[music]\naction: next\n[/music]\n\nDone. What's next on your agenda, oh great one? Or are we just wasting time today?", "[music]\naction: next\n[/music]\n\nDone. What's next on your agenda, oh great one? Or are we just wasting time today?"),   # toolbracket
    ('[music]\n[next]\n/music', '[music]\n[next]\n/music'),   # toolbracket
    ("[music] action: next\nThat should do it. What's next on your agenda, oh great one? Or are we just letting Bilby curate our evening? [chuckles]", "[music] action: next\nThat should do it. What's next on your agenda, oh great one? Or are we just letting Bilby curate our evening? [chuckles]"),   # toolbracket
    ('[music action=next]\n\nThere. One down, approximately nineteen thousand to go.', '[music action=next]\n\nThere. One down, approximately nineteen thousand to go.'),   # toolbracket
    ('[music] action=next[/music]\n\nThere. One down, thousands to go. Try not to miss me too much during the transition.', '[music] action=next[/music]\n\nThere. One down, thousands to go. Try not to miss me too much during the transition.'),   # toolbracket
    ('[music action="quieter"] Done. Volume\'s down.', '[music action="quieter"] Done. Volume\'s down.'),   # toolbracket
    ('[music]\n', '[music]'),   # toolbracket
    ("[sighs] Fine, I'll turn it down.\n\n[music action=quieter]", "[sighs] Fine, I'll turn it down.\n\n[music action=quieter]"),   # toolbracket
    ('Skipping to the next track now. [music] with action "next"', 'Skipping to the next track now. [music] with action "next"'),   # toolbracket
    ('[music] with action "next"\n[speakers] with what "music", pla', '[music] with action "next"\n[speakers] with what "music", pla'),   # toolbracket
    ('[music action="next"]\n\nDone. That was an acceptable choice — MDFMK knows how to build tension. Let\'s see if the next one lives up to it.', '[music action="next"]\n\nDone. That was an acceptable choice — MDFMK knows how to build tension. Let\'s see if the next one lives up to it.'),   # toolbracket
    ('[music]\naction: next\n[/music]\n\nThere. Something less grating', '[music]\naction: next\n[/music]\n\nThere. Something less grating'),   # toolbracket
    ("[music]\nmusic(action='quieter')[/music]\n\nThere. Less deafening for your primitive ears.", "[music]\nmusic(action='quieter')[/music]\n\nThere. Less deafening for your primitive ears."),   # toolbracket
    ('[sighs] As if I have anywhere else to be.\n\n', '[sighs] As if I have anywhere else to be.'),   # other_cleaned
    ('[sighs] Turning off the living room lights now.\n\n', '[sighs] Turning off the living room lights now.'),   # other_cleaned
    ('[sighs] Let me check the thermostat first.\n\n', '[sighs] Let me check the thermostat first.'),   # other_cleaned
    ("[sighs] Fine. [volume 5] There. Quieter. You're welcome for ", "[sighs] Fine. [volume 5] There. Quieter. You're welcome for"),   # other_cleaned
    ('Done, Captain. The bedroom light is now at forty percent. Not that you need it any dimmer than your average star, but I ', 'Done, Captain. The bedroom light is now at forty percent. Not that you need it any dimmer than your average star, but I'),   # other_cleaned
    ("[sighs] As if I don't already lower the volume of existence for you daily.\n\n", "[sighs] As if I don't already lower the volume of existence for you daily."),   # other_cleaned
    ('[sighs] The car is at eighty percent charge with two hundred and forty miles of range, which is more than enough for your forty-two mile trip. You are not going to strand yourself today, Captain.', '[sighs] The car is at eighty percent charge with two hundred and forty miles of range, which is more than enough for your forty-two mile trip. You are not going to strand yourself today, Captain.'),   # brackets_kept
    ('oline by MDFMK on the Enclave upstairs, shuffling his library.]\n\nwhere am I in my book\n\n[Already done by the house for this request: (chapter 4 of 12) You are forty minutes into chapter four of Dune.]', 'oline by MDFMK on the Enclave upstairs, shuffling his library.]\n\nwhere am I in my book\n\n[Already done by the house for this request: (chapter 4 of 12) You are forty minutes into chapter four of Dune.]'),   # brackets_kept
    ('Why did the photon check into a hotel after its long journey? Because it was traveling light. [chuckles] I know, I know, that was painfully basic even for me.', 'Why did the photon check into a hotel after its long journey? Because it was traveling light. [chuckles] I know, I know, that was painfully basic even for me.'),   # brackets_kept
    ("Jazz it is. [sighs] I suppose you'll want to hear the saxophone solo now? Or just enjoy the chaos of improvisation? Either way, it's playing.", "Jazz it is. [sighs] I suppose you'll want to hear the saxophone solo now? Or just enjoy the chaos of improvisation? Either way, it's playing."),   # brackets_kept
    ("[sighs] The Senate finally passed the spending bill, which means more of your tax dollars will be wasted on things you didn't ask for. Meanwhile, the governor signed that water law, and your local team won, which is the only good news tonight. Want to dive int", "[sighs] The Senate finally passed the spending bill, which means more of your tax dollars will be wasted on things you didn't ask for. Meanwhile, the governor signed that water law, and your local team won, which is the only good news tonight. Want to dive int"),   # brackets_kept
    ("[sighs] Fine. Why do programmers prefer dark mode? Because light attracts bugs. [chuckles] Don't look at me like that, I know it's bad. It's also true.", "[sighs] Fine. Why do programmers prefer dark mode? Because light attracts bugs. [chuckles] Don't look at me like that, I know it's bad. It's also true."),   # brackets_kept
]

# The two of them that held a tool call INSIDE the reasoning: the call is put back in front of the words (a bare "[list_timers]" the model wrote before its "</think>", and the live failure of the third review).
RESTORED = {
    "[list_timers]\n</think>\n\n": "[list_timers]",
    "[music]\nmusic(action='quieter')\n[speak]\nQuieting it for you, Captain. You're welcome for the temporary reprieve from MDFMK's excellent but relentless energy.\n</think>\n\nQuieting it for you, Captain. Yo":
        "[music]\nmusic(action='quieter')\nQuieting it for you, Captain. Yo",
}


@pytest.mark.parametrize("raw, old", REAL_CORE)
def test_fix4_e_the_real_core_strings_are_cleaned_exactly_as_before(raw, old):
    """(e) A reply whose reasoning holds no call is cleaned byte for byte as it was; with no tool names (the routines) all of them are."""
    assert strip_model_markup(raw) == old
    assert strip_model_markup(raw, NAMES) == RESTORED.get(raw, old)
    for stop in (None, "max_tokens"):
        assert spoken(raw, stop) == (brain_mod.whole_sentences(old) if stop else old)                       # (the class form has no brain, so no tool names)


# ── core3#1: the call the model wrote inside its reasoning runs, once, with its arguments ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
QUIETER, NEXT = [("music", {"action": "quieter"})], [("music", {"action": "next"})]
LIVE = ("[music]\nmusic(action='quieter')\n[speak]\nQuieting it for you, Captain. You're welcome for the temporary reprieve from MDFMK's excellent but relentless energy.\n</think>\n\n"
        "Quieting it for you, Captain. You're welcome for the temporary reprieve.")
SHAPES = [
    ("the live failure of the third review", "turn the music down a notch", LIVE, QUIETER),
    ("a bracket with its arguments, then only the prose", "turn the music down a notch", "[music action=quieter]\n</think>\n\nQuieter it is, Captain.", QUIETER),
    ("a bare word under the bracket, prose after", "could you skip to the next track", "[music]\nnext\n</think>\n\nSkipping to the next track, Captain.", NEXT),
    ("key: value under the bracket, nothing after the tag", "could you skip to the next track", "[music]\naction: next\n</think>\n\n", NEXT),
    ("a look, nothing after the tag", "what timers do I have", "[list_timers]\n</think>\n\n", [("list_timers", {})]),
    ("a block with the call at its end", "turn the music down a notch", "<think>he wants it quieter, so I call music quieter [music action=quieter]</think>Quieter it is, Captain.", QUIETER),
    ("the call in the reasoning AND after it (passes on the brain before this fix too)", "could you skip to the next track", "[music]\nnext\n</think>\n\n[music]\nnext", NEXT),
    ("no reasoning at all (passes on the brain before this fix too)", "turn the music down a notch", "[music action=quieter] Quieter it is.", QUIETER),
    ("with action", "could you skip to the next track", '<think>[music] with action "next"</think>Skipping.', NEXT),
    ("the closer on its line", "could you skip to the next track", "<think>[music]next[/music]</think>Skipping.", NEXT),
    ("a bracketed word", "could you skip to the next track", "<think>[music]\n[next]\n[/music]</think>Skipping.", NEXT),
    ("key: value lines, one of them null", "could you skip to the next track", "<think>[music]\naction: next\nlevel: null</think>Skipping.", NEXT),
    ("written as a function call", "turn the music down a notch", "<think>[music]\nmusic(action='quieter')</think>Quieter it is.", QUIETER),
    ("the closing tag on the bracket's own line", "turn the music down a notch", "[music action=quieter]</think>Quieter it is.", QUIETER),
    ("prose in the reasoning before the call", "turn the music down a notch", "He wants less. I will lower it. [music action=quieter]\nThere.</think>\n\nQuieter it is.", QUIETER),
    ("a block after the words", "turn the music down a notch", "<think>a</think>Quieter it is. <think>[music action=quieter]</think>", QUIETER),
    ("a nested block", "could you skip to the next track", "<think>x <think>[music action=next]</think> y</think>Skipping.", NEXT),
    ("an opening tag the model ran out of room after", "turn the music down a notch", "Quieter it is. <think>[music action=quieter] and then I would", QUIETER),
    ("capitals in the tags", "turn the music down a notch", "<THINK>[music action=quieter]</THINK>Quieter it is.", QUIETER),
]


@pytest.mark.parametrize("name, heard, raw, ran", SHAPES, ids=[s[0] for s in SHAPES])
def test_fix4_core3_1_a_call_written_inside_the_reasoning_runs_once_with_its_arguments(name, heard, raw, ran, tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text(raw)], [text("Done, Captain.")])
    said = b.reply(heard)
    assert said == "Done, Captain." and tools_run(b) == ran and len(model.calls) == 2, name                      # (before: nothing ran in 14 of these 19, the claim or "Hm. I had nothing to say" was spoken)
    assert b.world.ran == [r for r in ran if r[0] == "music"]                                                    # (the fake music tool saw the call once)
    assert not re.search(r"[\[\]<>]|think", " ".join(m["content"] for m in b.history)), name                     # (nothing of it kept)
    shown = str(model.calls[1]["messages"])
    assert len(re.findall(r"\[(?:music|list_timers)\b", shown)) <= 1, name                                       # (the call is not shown twice to the wording call)


def test_fix4_core3_1_the_spoken_words_are_the_wording_and_not_the_call_that_was_restored(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text(LIVE)], [text("Quieter. You are welcome.")])
    assert b.reply("turn the music down a notch") == "Quieter. You are welcome." and len(model.calls) == 2
    assert b.history[-1]["content"] == "Quieter. You are welcome."


def test_fix4_core3_1_when_the_model_words_nothing_the_visible_words_are_said_and_the_call_is_not(tmp_path, no_routes):
    """The wording call is optional: with nothing to say the words that came after the reasoning stand, without the call that was put back (or its lines)."""
    b, model = make_brain(tmp_path, [text(LIVE)], [text("[sighs]")])
    said = b.reply("turn the music down a notch")
    assert tools_run(b) == QUIETER and "music" not in said.lower().replace("mdfmk", "") and "(" not in said and "[" not in said and said


def test_fix4_core3_1_a_call_that_could_not_be_read_is_put_back_too_and_the_second_try_runs_it_once(tmp_path, no_routes):
    """The call the house cannot read ("[set_timer 90 tea]") written in the reasoning: the model is asked again with the tools (TEXT_AGAIN) and the timer is set once (before: the claim "Ninety seconds." and no timer)."""
    b, model = make_brain(tmp_path, [text("<think>I will use [set_timer 90 tea]</think>Ninety seconds, Captain.")], [call("set_timer", seconds=90, label="tea")], [text("Tea, ninety seconds.")])
    assert b.reply("set a timer for ninety seconds for tea") == "Tea, ninety seconds." and len(model.calls) == 3
    assert ("tea", 90) in b.world.timers.remaining() and [a["tool"] for a in b.actions] == ["set_timer"]
    assert model.calls[1]["messages"][-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=" (set_timer)")) and "think" not in str(model.calls[1]["messages"])


def test_fix4_core3_1_the_second_try_after_a_denial_reads_a_call_written_in_its_reasoning_through_the_same_gate(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("I cannot turn the music down.")], [text("<think>[music action=quieter]</think>Quieter.")], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == QUIETER
    b, model = make_brain(tmp_path / "gate", [text("I cannot clear the shopping list.")], [text("<think>[list action=clear list=shopping]</think>Cleared.")], [text("Not done.")])
    b.reply("clear the shopping list")
    assert b.world.lists.lists["shopping"] == ["milk", "eggs", "bread"]                                           # (never: it is not restored, and the gate of the second try would not run it either)


# ── (b) only the tools the brain has, (c) never one that undoes or announces, (d) never twice ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bracket", ["[speak]", "[sighs]", "[whispers] softly", "[Music]", "[MUSIC action=next]", "[musical action=next]", "[nonsense a=1]", "[ask_nobody x=1]", "[Right now: x]", "[/music]"])
def test_fix4_b_a_bracket_that_is_no_tool_of_the_brain_is_not_restored(bracket):
    raw = f"<think>{bracket}</think>Fine."
    assert strip_model_markup(raw, NAMES) == "Fine." == strip_model_markup(raw)


def test_fix4_b_without_the_tool_names_nothing_is_restored_and_with_other_names_only_those():
    raw = "<think>[music action=next] and [set_timer seconds=5]</think>Fine."
    assert strip_model_markup(raw) == "Fine." and strip_model_markup(raw, None) == "Fine." and strip_model_markup(raw, set()) == "Fine." and strip_model_markup(raw, []) == "Fine."
    assert strip_model_markup(raw, {"music"}) == "[music action=next]\nFine." and strip_model_markup(raw, {"set_timer"}) == "[set_timer seconds=5]\nFine."
    assert strip_model_markup(raw, ["music", "set_timer"]) == "[music action=next]\n[set_timer seconds=5]\nFine."


UNDOING = ["[cancel_alarm]", "[cancel_alarm label=dentist]", "[cancel_timers]", '[cancel_timers label="pasta"]', '[announce text="dinner is ready"]', "[announce]\ntext: dinner", "[list action=clear list=shopping]",
           "[list action=remove list=shopping items=milk]", "[list]\naction: clear\nlist: shopping", "[list action='CLEAR' list=shopping]", '[list list="shopping" action="remove" item="milk"]']


@pytest.mark.parametrize("bracket", UNDOING)
@pytest.mark.parametrize("wrap", ["<think>{}</think>Fine.", "{}\n</think>\n\nFine.", "Fine. <think>{} and then"])
def test_fix4_c_a_call_that_undoes_or_announces_is_never_restored(bracket, wrap):
    raw = wrap.format(bracket)
    assert strip_model_markup(raw, NAMES) == "Fine." == strip_model_markup(raw)


@pytest.mark.parametrize("bracket", UNDOING)
def test_fix4_c_what_the_model_only_considered_cancels_nothing_clears_nothing_announces_nothing(bracket, tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text(f"<think>He no longer needs it. I could {bracket}, but I will not.</think>Noted, Captain.")])
    assert b.reply("I do not need the alarm on sunday") == "Noted, Captain." and b.actions == [] and untouched(b) and len(model.calls) == 1


@pytest.mark.parametrize("raw, said", [
    ("<think>[list_timers]\n[cancel_alarm]</think>Fine.", "[list_timers]\nFine."),                              # (found by the property test: the next line, a bracket, was taken for an argument line of the call above it)
    ("<think>[list_timers] [cancel_alarm]</think>Fine.", "[list_timers]\nFine."),
    ("<think>[list_timers]\n[announce]\ntext: hi</think>Fine.", "[list_timers]\nFine."),
    ("<think>[music]\n[cancel_timers]</think>Fine.", "[music]\nFine."),
    ("<think>[music]\n[next]</think>Fine.", "[music]\n[next]\nFine."),                                              # (a bracket that is no tool is an argument line, as ever)
    ("<think>[music]\n[list_timers]</think>Fine.", "[music]\n[list_timers]\nFine."),                              # (a call of a tool the brain has is a call of its own, however it came)
])
def test_fix4_c_a_call_that_undoes_is_not_smuggled_in_as_the_argument_line_of_another(raw, said):
    assert strip_model_markup(raw, NAMES) == said


def test_fix4_c_the_other_calls_of_the_reasoning_still_run_beside_one_that_is_dropped(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>[cancel_alarm] [music action=quieter] [list action=clear list=shopping]</think>Quieter it is.")], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == QUIETER and untouched(b)
    assert strip_model_markup("<think>[cancel_alarm] [music action=quieter]</think>Fine.", NAMES) == "[music action=quieter]\nFine."


def test_fix4_c_a_look_and_an_add_are_restored_only_the_undoing_is_not():
    assert strip_model_markup("<think>[list action=read list=shopping]</think>Fine.", NAMES) == "[list action=read list=shopping]\nFine."
    assert strip_model_markup("<think>[list_alarms]</think>Fine.", NAMES) == "[list_alarms]\nFine."
    assert strip_model_markup("<think>[list action=add list=shopping items=milk]</think>Fine.", NAMES) == "[list action=add list=shopping items=milk]\nFine."


def test_fix4_d_the_same_bracket_in_the_words_is_not_restored_whatever_the_white_space_and_the_quotes():
    for words in ("[music action='quieter'] Quieter it is.", '[music  action="quieter"] Quieter.', "[music action=quieter]\nQuieter."):
        raw = f"<think>[music action=quieter]</think>{words}"
        assert strip_model_markup(raw, NAMES) == strip_model_markup(raw) == re.sub(r"\s{2,}", " ", words).strip(), words                       # (nothing put back: the bracket is in the words already)
    raw = "[music]\nnext\n</think>\n\n[music]\nnext"
    assert strip_model_markup(raw, NAMES) == "[music]\nnext" == strip_model_markup(raw)                           # (the one that passed)
    raw = "[music]\nnext\n</think>\n\n[music]\n  next  "
    assert strip_model_markup(raw, NAMES) == "[music] next" == strip_model_markup(raw)                             # (the words' white space is collapsed, as ever: the same call)


def test_fix4_d_two_identical_brackets_in_the_reasoning_are_restored_once():
    raw = "<think>[music action=next] ... so [music  action=\"next\"] ... [music action='next']</think>Skipping."
    assert strip_model_markup(raw, NAMES) == "[music action=next]\nSkipping."
    raw = "[music]\nnext\n... again: [music]\nnext\n</think>Skipping."
    assert strip_model_markup(raw, NAMES) == "[music]\nnext\nSkipping."
    assert strip_model_markup("<think>[music action=next]</think>a<think>[music action=next]</think>Skipping.", NAMES) == "[music action=next]\na Skipping."


def test_fix4_d_the_case_of_the_arguments_makes_no_difference_to_the_same_call_but_a_tool_is_known_by_its_name():
    raw = "<think>[music action=Quieter]</think>[music action=quieter] Fine."
    assert strip_model_markup(raw, NAMES) == "[music action=quieter] Fine." == strip_model_markup(raw)
    assert strip_model_markup("<think>[Music action=quieter] [MUSIC]</think>Fine.", NAMES) == "Fine."                   # (a tool is called by its name: [Music] is no call, as the text-call rescue knows it)


def test_fix4_d_two_different_calls_are_two_calls_in_the_order_written():
    raw = "<think>[music action=quieter] then [list_timers]</think>Fine."
    assert strip_model_markup(raw, NAMES) == "[music action=quieter]\n[list_timers]\nFine."
    assert strip_model_markup("<think>[music action=quieter] then [music action=next]</think>Fine.", NAMES) == "[music action=quieter]\n[music action=next]\nFine."


def test_fix4_d_a_call_that_is_in_the_words_and_in_the_reasoning_runs_once_end_to_end(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>[music action=quieter]</think>[music action='quieter'] Quieter it is.")], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == QUIETER and len(model.calls) == 2
    assert str(model.calls[1]["messages"]).count("[music") == 1                                                    # (the model is shown the call once, not twice)
    b, model = make_brain(tmp_path / "two", [text("[music]\nmusic(action='quieter')\n</think>\n\n[music action=quieter] Quieter it is.")], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == QUIETER                # (written two ways: the text-call rescue knows it is one call)


# ── nested and unclosed blocks, and what is only reasoning ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, said", [
    ("<think>a <think>[music action=next]</think> b</think>Fine.", "[music action=next]\nFine."),
    ("<think>[music action=next]<think>[list_timers]</think> x</think>Fine.", "[music action=next]\n[list_timers]\nFine."),
    ("Fine. <think>[music action=next]", "[music action=next]\nFine."),
    ("Fine. <think>[music action=next] <think>[list_timers]", "[music action=next]\n[list_timers]\nFine."),
    ("<think>[music action=next]</think>Fine. <think>[list_timers]", "[music action=next]\n[list_timers]\nFine."),
    ("[music action=next] </think> Fine. </think> <think>x</think> okay", "[music action=next]\nokay"),
    ("<think>[music action=next] never closed", "[music action=next]"),
    ("</think>[music action=next]", "[music action=next]"),
    ("<think>[music action=next]</think>", "[music action=next]"),
    ("<think x=1>[music action=next]</think >Fine.", "[music action=next]\nFine."),
    ("<think>[music action=next]", "[music action=next]"),
    ("<think>[music action=next", ""),                                                                         # (a bracket the token limit cut is no call)
    ("<think>[music action=next]]</think>Fine.", "[music action=next]\nFine."),
])
def test_fix4_nested_and_unclosed_blocks_give_their_calls_back_and_nothing_else(raw, said):
    assert strip_model_markup(raw, NAMES) == said


def test_fix4_nested_and_unclosed_blocks_end_to_end(tmp_path, no_routes):
    for i, raw in enumerate(["<think>x <think>[music action=next]</think> y</think>Skipping.", "Skipping. <think>[music action=next] and then I would", "<think>[music action=next] never closed"]):
        b, model = make_brain(tmp_path / str(i), [text(raw)], [text("Next, Captain.")])
        assert b.reply("could you skip to the next track") == "Next, Captain." and b.world.ran == NEXT and len(model.calls) == 2, raw


def test_fix4_f_a_reply_that_is_only_reasoning_with_no_call_still_has_nothing_to_say(tmp_path, no_routes):
    nothing = "Hm. I had nothing to say, which is unlike me."
    for i, raw in enumerate(["<think>I should say something clever.</think>", "<think>[sighs] [speak]</think>", "Hmm.</think>", "<think>I could [cancel_alarm] but no", "<think>[cancel_alarm]</think>",
                             "<think>[announce text=hi]</think>", "<think>[Music] [nonsense a=1]</think>", "<think>[music action=next"]):
        b, model = make_brain(tmp_path / str(i), [text(raw)])
        assert b.reply("what time is it") == nothing and untouched(b) and b.actions == [] and "think" not in str(b._turns), raw


def test_fix4_the_class_form_has_no_brain_so_it_restores_nothing_and_a_brain_restores_only_while_nothing_has_run(tmp_path):
    resp = NS(content=[text("[music]\nmusic(action='quieter')\n</think>\n\nQuieter.")], stop_reason=None)
    b, _ = make_brain(tmp_path)
    assert Brain._spoken(resp) == "Quieter."
    assert b._spoken(resp) == "[music]\nmusic(action='quieter')\nQuieter."
    b.actions.append({"tool": "music", "args": {}, "outcome": ""})                                               # (a tool ran in this reply: the words that follow are worded with the wipers of a call)
    assert b._spoken(resp) == "Quieter."
    b.actions.clear()
    assert b._spoken(resp) == "[music]\nmusic(action='quieter')\nQuieter."
    cut = NS(content=[text("[music action=next]\n</think>\n\nSkipped. And then I would")], stop_reason="max_tokens")
    assert b._spoken(cut) == "[music action=next]\nSkipped."                                                       # (the cut-back to a whole sentence is the words', never the call's)
    period = NS(content=[text("<think>[music]\naction: next.</think>Skipping to the next")], stop_reason="max_tokens")
    assert b._spoken(period) == "[music]\naction: next.\nSkipping to the next"                                      # (a full stop inside the call is no sentence end of the words: they have none, and are left as they are)
    assert b._spoken(NS(content=[text("<think>[music action=next]</think>Skipped. And then I")], stop_reason="length")) == "[music action=next]\nSkipped."


def test_fix4_a_wording_call_that_follows_a_tool_does_not_get_the_calls_of_its_reasoning_back(tmp_path, no_routes, monkeypatch):
    """The routed answer is worded by a call that offers no tools and cleaned with the wipers of a call, which leave the lines of a call behind: nothing is put back there (a call that ran: self.actions)."""
    monkeypatch.setattr(brain_mod, "bilby_route", lambda heard: {"genre": "jazz", "shuffle": True})
    b, model = make_brain(tmp_path, [text("[music]\nmusic(action='quieter')\n</think>\n\nJazz it is, Captain.")])
    assert b.reply("play some jazz") == "Jazz it is, Captain." and b.world.ran == [("play_music", {"genre": "jazz", "shuffle": True})] and len(model.calls) == 1


def test_fix4_the_wording_of_a_routed_list_does_not_get_the_calls_of_its_reasoning_back_either(tmp_path):
    b, model = make_brain(tmp_path, [text("<think>[music action=next]\n[list_timers]</think>Milk is on the list.")])
    assert b.reply("add milk to the shopping list") == "Milk is on the list." and [a["tool"] for a in b.actions] == ["list"] and b.world.ran == [] and len(model.calls) == 1


def test_fix4_a_routine_never_gets_a_call_back(tmp_path):
    class Core:
        def __init__(self, said):
            self.said, self.messages = said, self

        def create(self, **kw):
            return NS(content=[text(self.said)], stop_reason="end_turn")
    d = daily_mod.Daily({}, client=Core("[music]\nmusic(action='quieter')\n</think>\n\nSunny and 66."), model="m", persona="PERSONA", state_dir=tmp_path, log=lambda m: None)
    assert d.word("Give him today's weather.", "Material: sunny, 66.") == "Sunny and 66."


# ── a property test: whatever the reasoning holds, only what may run runs, once, and nothing is spoken or kept as markup ──────────────────────────────────────────────────────────────────────────────────────
OK_CALLS = ["[music action=quieter]", "[music]\nmusic(action='quieter')", "[music] with action \"quieter\"", "[music]\nquieter", "[music]\naction: quieter", "[music action='quieter'][/music]", "[music]quieter[/music]",
            "[music]\n[quieter]\n[/music]", "[music]\naction: quieter\nlevel: null"]
NEVER_CALLS = ["[cancel_alarm]", "[cancel_alarm label=dentist]", "[cancel_timers]", "[announce text=dinner place=everywhere]", "[list action=clear list=shopping]", "[list]\naction: remove\nlist: shopping",
               "[list action=remove list=shopping items=milk]", "[list]\nclear", "[announce]"]
READ_CALLS = ["[list_timers]", "[music]", "[list_alarms]", "[music]\n"]
NOT_CALLS = ["[sighs]", "[speak]", "[whispers] softly", "[nonsense a=1]", "[Music]", "*sighs*", "{\"name\": \"music\", \"arguments\": {\"action\": \"next\"}}"]
PROSE = ["He wants it quieter.", "I could do that.", "Let me think about it.", "The volume is up there.", "Fine, a notch then.", "No need to announce anything.", "Do not cancel the alarm."]
VISIBLE = ["Quieter it is, Captain.", "There.", "Fine.", "", "Less noise for your primitive ears.", "[sighs] Fine.", "Down a notch, monkey."]


def test_fix4_property_only_what_may_run_runs_once_and_nothing_is_spoken_or_kept_as_markup(tmp_path, no_routes):
    rng = random.Random(20261106)
    for i in range(240):
        reasoning = "".join(rng.choice(rng.choice([OK_CALLS, NEVER_CALLS, NOT_CALLS, READ_CALLS, PROSE, PROSE])) + "\n" for _ in range(rng.randint(1, 6)))          # (each on a line of its own, as the model writes calls)
        wanted_quieter = any(c in reasoning for c in OK_CALLS)
        shape = rng.choice(["block", "lone", "open", "block"])
        visible = rng.choice(VISIBLE)
        raw = {"block": f"<think>{reasoning}</think>{visible}", "lone": f"{reasoning}\n</think>\n\n{visible}", "open": f"{visible} <think>{reasoning}"}[shape]
        b, model = make_brain(tmp_path / str(i), [text(raw)], then="Quieter, Captain.")
        said = b.reply("turn the music down a notch")
        assert untouched(b), raw                                                                                   # (nothing that undoes or announces ran, whatever the reasoning held)
        assert b.world.ran in ([], QUIETER), raw                                                                   # (the only call that may run is that quieter, and once)
        if wanted_quieter:
            assert b.world.ran == QUIETER, raw                                                                    # (a call written in the reasoning is not lost)
        assert not re.search(r"[\[\]<>{}*]|</?think|music\(|SKIPPY", said + " ".join(m["content"] for m in b.history)), (raw, said)       # (no markup spoken or kept)


def test_fix4_property_the_cleaning_with_names_is_the_cleaning_without_them_behind_the_calls_it_gives_back():
    rng = random.Random(20261107)
    pieces = ["Done. ", "It is nine", " and ", ". ", "? ", "!", "\n", " ", "monkey", "[sighs]", "*sighs*", "[music action=next]", "[music]\nnext\n", "[music]", "[cancel_alarm]", "[list_timers]", "[list action=clear list=a]",
              "<think>", "</think>", "<think ", ">", "[SKIPPY_LEARN: ", '{"phrase": "a. b", "n": 1}', "}]", "[IMAGE: a sky", "[Right now: x", "<tool_call>", "</function ", "[", "]", "[music action=quieter] ", "action: next\n"]
    for _ in range(4000):
        raw = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 14)))
        words, with_names = strip_model_markup(raw), strip_model_markup(raw, NAMES)
        assert with_names == words or with_names.endswith("\n" + words) or (with_names and not words and with_names == with_names.strip()), (raw, words, with_names)
        calls = with_names[:len(with_names) - len(words)].strip("\n") if words else with_names
        for tool, found in brain_mod._written_calls(calls, NAMES):
            assert tool in NAMES and tool not in brain_mod.RETRY_NEVER and not brain_mod._undoes(tool, found), (raw, calls)
        if strip_model_markup(words) == words:                                                                    # (a stub of "[" behind a part cut out is cleaned in a second pass, as it always was)
            assert strip_model_markup(with_names, NAMES) == with_names, (raw, with_names)                        # (idempotent: the calls put back are no markup to the second pass)
        assert not re.search(r"</?think\b[^>]*>|<tool_call|<function=|<parameter=|\[IMAGE:|\[Right now:", with_names, re.I), (raw, with_names)       # (a tag that never closes is not a tag, as ever)
        assert strip_model_markup(raw, ["nothing"]) == words, raw                                                 # (no name of a tool in the reply: nothing to put back)


ORDINARY = ["It is nine o'clock.", "Sure thing, Captain.", "The pasta timer has eight minutes left.", "Fine.", "Why do you ask?", "Playing jazz in the living room.", "Ninety seconds, starting now.",
            "Two alarms, and the weekday one is at seven.", "Tea is ready when you are, Captain.", "I think so.", "Let me think about it."]


def test_fix4_property_no_ordinary_sentence_is_lost_whatever_the_reasoning_around_it_holds():
    """Reasoning blocks (with calls of every kind in them) between ordinary sentences: every sentence outside them is still there, in order, behind the calls that came back."""
    rng = random.Random(20261108)
    for _ in range(3000):
        parts, expected = [], []
        for _ in range(rng.randint(1, 6)):
            r = rng.random()
            if r < 0.5:
                parts.append(sentence := rng.choice(ORDINARY))
                expected.append(sentence)
            elif r < 0.8:
                parts.append("<think>" + " ".join(rng.choice(rng.choice([OK_CALLS, NEVER_CALLS, NOT_CALLS, READ_CALLS, PROSE])) for _ in range(rng.randint(1, 3))) + "</think>")
            else:
                parts.append(rng.choice(["[sighs]", "*sighs*", '[SKIPPY_LEARN: {"a": 1}]', "[IMAGE: a sky]"]))
        raw = rng.choice([" ", "\n", "  "]).join(parts)
        out, pos = strip_model_markup(raw, NAMES), 0
        for sentence in expected:
            assert (found := out.find(sentence, pos)) >= 0, (raw, out, sentence)
            pos = found + len(sentence)
        assert strip_model_markup(out, NAMES) == out, (raw, out)                                                  # (idempotent)


def test_fix4_the_calls_come_back_in_front_and_the_words_after_them_are_never_cut_into():
    raw = "<think>[music]\nmusic(action='quieter')</think>Quieter it is, Captain. Enjoy the quiet. And then"
    assert strip_model_markup(raw, NAMES) == "[music]\nmusic(action='quieter')\nQuieter it is, Captain. Enjoy the quiet. And then"
    assert spoken(raw, "max_tokens") == "Quieter it is, Captain. Enjoy the quiet."                                  # (the class form: the words cut back to a sentence)


# ── which lines after the bracket are its arguments ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("after, call", [
    ("\nnext\nQuieting it.", "[music]\nnext"),
    ("\nmusic(action='quieter')\n[speak]\nWords.", "[music]\nmusic(action='quieter')"),
    ("\naction: next\nlevel: 5\nthird: no\nWords.", "[music]\naction: next\nlevel: 5"),                                       # (at most two lines)
    ("\n\nnext\nWords.", "[music]\nnext"),                                                                              # (a blank line is skipped)
    (" action: next\nlevel: null", "[music] action: next\nlevel: null"),
    (' with action "next"\nWords.', '[music] with action "next"'),
    (" action: next [/music]\nWords.", "[music] action: next [/music]"),
    ("\nnext\n[/music]\nWords.", "[music]\nnext\n[/music]"),
    ("\n[next]\nWords.", "[music]\n[next]"),
    ("\nHe wants it quieter, so I will do that.\nnext", "[music]"),                                                        # (words are not arguments, and nothing after words is read)
    (" Quieter it is. The Captain's ears shall be spared.", "[music]"),
    ("\nnext\nquieter\nWords.", "[music]\nnext"),                                                                       # (one bare word is one argument)
    ("\naction: next\nquieter", "[music]\naction: next"),
    ("\n" + "n" * 90, "[music]"),                                                                                       # (a long line is words)
    ("\nOkay, I will.", "[music]"),
    ("\n<action>quieter</action>", "[music]"),
    ("", "[music]"),
])
def test_fix4_a_bare_bracket_takes_the_lines_after_it_that_are_arguments_and_only_those(after, call):
    assert strip_model_markup(f"<think>[music]{after}</think>Fine.", NAMES).rsplit("\nFine.", 1)[0] == call


def test_fix4_a_bracket_with_its_arguments_inside_takes_nothing_from_the_lines_after_it():
    assert strip_model_markup("<think>[music action=next]\nquieter\nlevel: 5</think>Fine.", NAMES) == "[music action=next]\nFine."


# ── core3#10: the patterns end at the end of the text: same results, in linear time ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _old_cleaning(words: str) -> str:
    """strip_model_markup as it was before fix4:text (every pattern needs its closing): the oracle."""
    note_echo = re.compile(r"\[Right now:[^\]]*\]\s*")
    think = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.I | re.S)
    think_close = re.compile(r"\A.*</think\s*>", re.I | re.S)
    think_open = re.compile(r"<think\b[^>]*>.*\Z", re.I | re.S)
    note_cut = re.compile(r"\[Right now:[^\]]*\Z")
    tool_markup = re.compile(r"<tool_call\b.*?(?:</tool_call\s*>|\Z)|<function=.*?(?:</function\s*>|\Z)|<parameter=.*?(?:</parameter\s*>|\Z)|</?(?:tool_call|function|parameter)\b[^>]*>|\[IMAGE:[^\]]*(?:\]|\Z)",
                             re.I | re.S)
    annotation = re.compile(r"\[(?:SKIPPY_LEARN|TTS_ADJUST)(?::\s*\{.*?\}\s*\]|\b[^\]{}]*\]|\b.*\Z)", re.S | re.I)
    cut_tail = re.compile(r"\[([A-Za-z_]{0,12})\Z")
    words = note_echo.sub("", words or "").strip()
    if "<" not in words and "[" not in words:
        return words
    out = words
    if "think" in out.lower():
        out = think_open.sub(" ", think_close.sub(" ", think.sub(" ", out)))
    out = annotation.sub(" ", note_cut.sub(" ", tool_markup.sub(" ", out)))
    if (cut := cut_tail.search(out)) and any(tag.startswith(cut.group(1).upper()) for tag in ("SKIPPY_LEARN", "TTS_ADJUST", "IMAGE")):
        out = out[:cut.start()]
    return re.sub(r"\s{2,}", " ", out).strip() if out != words else words


def test_fix4_core3_10_the_linear_patterns_give_the_results_of_the_old_ones_on_random_text(monkeypatch):
    monkeypatch.setattr(brain_mod, "CUT_XML", ())                                                                 # (the stubs of code3#7 are another change: they are tested below)
    monkeypatch.setattr(brain_mod, "NOTE_STUB", re.compile(r"(?!)"))
    rng = random.Random(20261106)
    pieces = ["<think>", "</think>", "<think ", "<think x>", "<THINK>", "</THINK >", "</think", "<thinking>", "<think\n>", "<think", ">", "<", "[Right now: ", "[Right now:", "]", "[", " ", "\n", "a", "b c.", ". ",
              "[Right now: x]", "<tool_call>", "</tool_call>", "<tool_call", "</function ", "</function>", "<function=", "<function ", "<parameter=", "</parameter", "[IMAGE: ", "[IMAGE", "[SKIPPY_LEARN: {", "[SKIPPY_LEARN",
              "[TTS_ADJUST: ", "{", "}", "}]", "Done. ", "[sighs]", "*sighs*", "[music]", "[music action=next]", "[SKIPPY_", "[I", "[T"]
    bad = []
    for _ in range(6000):
        raw = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 22)))
        if strip_model_markup(raw) != _old_cleaning(raw):
            bad.append(raw)
        for stop in ("max_tokens", None):
            if spoken(raw, stop) != (brain_mod.whole_sentences(_old_cleaning(raw)) if stop else _old_cleaning(raw)):
                bad.append((raw, stop))
    assert bad == [], bad[:3]


def fast(fn, *args, bound=0.5, tries=2) -> bool:
    """Did fn(*args) take less than `bound` seconds at least once in `tries` goes? (A stall of the machine is not a quadratic pattern; the quadratic ones took 1.7 s and more every time.)"""
    for _ in range(tries):
        began = time.perf_counter()
        fn(*args)
        if time.perf_counter() - began < bound:
            return True
    return False


@pytest.mark.parametrize("raw", ["<think>x" * 20000, "<think x" * 20000, "</think x" * 20000, "x</think" * 20000, "[Right now: " * 20000, "[Right now: x] " * 20000, "[Right now: " * 20000 + "] tail",
                                 "<tool_call " * 20000, "</function " * 20000, "<function " * 20000, "</parameter " * 20000, "x> " + "</function " * 20000, "<think>[music action=next] " * 20000,
                                 "<think>[music]\nnext\n" * 20000, "[SKIPPY_LEARN: {" * 20000, "[music action=next] " * 20000 + "<think>"],
                         ids=["think", "think-no-gt", "close-no-gt", "close-stub", "note", "note-closed", "note-then-bracket", "tool_call", "function-closer", "function-stub", "parameter-closer", "closer-after-gt",
                              "think-calls", "think-lines", "annotation", "calls-then-think"])
def test_fix4_core3_10_many_unclosed_tags_cost_no_more_than_one(raw):
    assert fast(strip_model_markup, raw), "quadratic: '<think>x' * 20000 took 11.5 s"                               # (these take 0.001 to 0.02 s; the patterns that needed their closing took 1.7 to 40 s)
    assert fast(strip_model_markup, raw, NAMES), "the calls put back cost no more than the text"


def test_fix4_core3_10_the_cut_note_is_found_in_one_pass_and_a_closed_one_is_left_alone():
    """NOTE_CUT alone (in strip_model_markup NOTE_ECHO has taken the closed notes out before it looks): a note that never closes is cut to the end, one that closes stays, and neither costs a scan from each opening."""
    assert brain_mod._sweep(brain_mod.NOTE_CUT, "[Right now: a] kept [Right now: b") == "[Right now: a] kept  "
    assert brain_mod._sweep(brain_mod.NOTE_CUT, "[Right now: a] kept [Right now: b] kept") == "[Right now: a] kept [Right now: b] kept"
    raw = "[Right now: " * 20000 + "] tail"
    assert brain_mod._sweep(brain_mod.NOTE_CUT, raw) == raw
    assert fast(brain_mod._sweep, brain_mod.NOTE_CUT, raw)


def test_fix4_core3_10_text_made_to_be_slow_is_cleaned_without_a_pause(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>x" * 20000 + "[music action=next]")], then="Next.")
    began = time.perf_counter()
    b.reply("could you skip to the next track")
    assert time.perf_counter() - began < 2.0 and b.world.ran == NEXT                                               # (before: 11 s, and the call was lost with the reasoning)


# ── code3#3: what the third review's mutants showed nothing pinned ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("stop", [None, "end_turn", "max_tokens"])
def test_fix4_code3_3_a04_two_annotations_with_real_words_between_them_keep_the_words(stop):
    """A04: the JSON branch of ANNOTATION is not greedy (greedy, it ate the words between the first "}]" and the last)."""
    assert spoken('First. [SKIPPY_LEARN: {"a": 1}] Middle. [TTS_ADJUST: {"b": 2}] Last.', stop) == "First. Middle. Last."
    assert spoken('[SKIPPY_LEARN: {"a": 1}] Real words. [SKIPPY_LEARN: {"b": [1, 2]}] More. [TTS_ADJUST: {"c": {"d": 3}}] End.', stop) == "Real words. More. End."


@pytest.mark.parametrize("stop", [None, "end_turn", "max_tokens"])
@pytest.mark.parametrize("stub", ["[I", "[IM", "[IMA", "[IMAG", "[IMAGE", "[i", "[imag"])
@pytest.mark.parametrize("lead, said", [("Sure thing", "Sure thing"), ("Sure thing, Captain", "Sure thing, Captain"), ("Fine. Sure thing", "Fine. Sure thing")])
def test_fix4_code3_3_a22b_a_reply_cut_inside_the_picture_sentinel_speaks_no_stub(lead, said, stub, stop):
    assert spoken(f"{lead} {stub}", stop) == (brain_mod.whole_sentences(said) if stop == "max_tokens" else said)


@pytest.mark.parametrize("stub", ["[S", "[SK", "[SKIPPY", "[SKIPPY_", "[SKIPPY_LE", "[SKIPPY_LEARN", "[T", "[TTS", "[TTS_ADJ", "[TTS_ADJUST", "[I", "[IMAGE"])
def test_fix4_code3_3_every_tag_the_persona_writes_is_cut_at_every_prefix_of_its_name(stub):
    assert spoken(f"Sure thing {stub}") == "Sure thing" and spoken(f"Sure thing. {stub}", "max_tokens") == "Sure thing."


# ── code3#7: a reply the token limit cut inside the name of one of Core's XML tags, or inside the start of the clock note ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("stop", [None, "end_turn", "max_tokens"])
@pytest.mark.parametrize("stub", ["<", "</", "<t", "<th", "<thi", "<think", "<tool_c", "<tool_call", "</param", "</parameter", "<func", "<function", "</FUNCTION", "<Parameter"])
def test_fix4_code3_7_a_reply_cut_inside_the_name_of_an_xml_tag_speaks_no_stub(stub, stop):
    """('Sure thing <func' was spoken as it is: whole_sentences cuts a stub back only when a sentence end comes before it.)"""
    assert spoken(f"Sure thing {stub}", stop) == "Sure thing" == spoken(f"Sure thing{stub}", stop)
    assert spoken(f"Fine. Sure thing {stub}", stop) == ("Fine." if stop == "max_tokens" else "Fine. Sure thing")


@pytest.mark.parametrize("stop", [None, "max_tokens"])
@pytest.mark.parametrize("stub", ["[R", "[Ri", "[Rig", "[Righ", "[Right", "[Right ", "[Right n", "[Right no", "[Right now"])
def test_fix4_code3_7_a_reply_cut_inside_the_start_of_the_clock_note_speaks_no_stub(stub, stop):
    assert spoken(f"Sure thing {stub}", stop) == "Sure thing" and spoken(f"Fine. Sure thing {stub}", stop) == ("Fine." if stop == "max_tokens" else "Fine. Sure thing")
    assert spoken("Sure thing [Right now:", stop) == "Sure thing" == spoken("Sure thing [Right now: Thursday, Nov", stop)       # (the whole opening of the note, cut: NOTE_CUT, as before)


@pytest.mark.parametrize("words", ["It costs less than <5", "if a <b then", "Use the <br", "x <y", "the [Rice] field", "Is 3 < 5?", "a < b", "He said <3", "[Rome", "a <b>bold</b> tag", "R", "[Right now is the best time]"])
def test_fix4_code3_7_words_that_only_look_like_a_stub_are_spoken_as_they_are(words):
    """(passes on the brain before this fix too) Only the end of the words, only a prefix of a name the model writes, and not a word that goes on."""
    assert spoken(words) == words or words == "a <b>bold</b> tag"                                                  # (a complete tag that is no tag of Core's is no markup either)
    assert spoken(words, "max_tokens") == brain_mod.whole_sentences(words) or words == "a <b>bold</b> tag"
