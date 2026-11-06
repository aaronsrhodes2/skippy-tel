"""fix2:guards: the guards of the house brain, after the second review of the house speed change (2026-11-05).

The bare-acknowledgement backstop ("it said Done and did nothing": a reply that only acknowledges an order, with no tool called, is asked again) was found unsafe in places and leaky in others:
  Problem 2  its second try was ungated (every tool, the model's own `confirm`), and it fired on 12 of 12 bare "Okay."s to STATEMENTS; now it fires on an order in shape only (order_shaped) and its
             second try passes the gate of the one after a denial (retry_may; Brain._run_call), with the whole tool list shown (Core's prompt cache), the nudge naming what it may use
  Problem 3  it judged the raw text and only an acknowledgement at its start; now what would be spoken, an acknowledgement in the first OR the last sentence, a plain claim of state to a lights order
  Problem 4  a tool call written as text ran with NO arguments when it could not be read ("[music]next[/music]": production answered "I cannot do that to the music."); now the bare action word is read,
             else the call is not run and the model is asked again with the tools; "[music with action=quieter]" is read; a leftover bracket of a tool is never spoken
  Problem 8  the survivors of the review's mutation run: the backstop ignoring RISKY, ASKS_ABILITY, a question mark, a denial; call-lines taking an unknown key

Every test here but the pins that say otherwise fails on the brain before this fix.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

import alarms as alarms_mod
import brain as brain_mod
import hub as hub_mod
import house as house_mod
import lists as lists_mod
from brain import Brain
from timers import Timers

NOW = "Thursday, November 05, 2026, 21:40"


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
    sensitive = True                                                     # (the lock is there to be found: it answers "needs him to confirm out loud" without a `confirm`)

    def __init__(self):
        self.rows = [row("light.bedroom", "Bedroom"), row("light.kitchen_under_1", "kitchen under 1"), row("lock.front_door", "Front Door", "locked")]
        self.calls = []

    def states(self, fresh=False):
        return self.rows

    def areas(self):
        return {}

    def _service(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))


def guard_brain(tmp_path, *rounds, hass=True):
    """A real Brain over REAL alarms (the weekday wake-up, a dentist), REAL lists (shopping: milk, eggs, bread), a real pasta timer, the hub's REAL music and announce tool definitions (their calls recorded
    in `ran`) and a fake house with two lights and a lock: what a stray cancel, clear or announcement would really do."""
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


def untouched(b) -> bool:
    w = b.world
    return len(w.alarms.items) == 2 and w.lists.lists == {"shopping": ["milk", "eggs", "bread"]} and w.timers.remaining() == [("pasta", 600)] and w.ran == [] and b.actions == []


@pytest.fixture
def no_routes(monkeypatch):
    """The routes are another part's business: these phrasings reach the model whatever they take."""
    for name in ("light_route", "timer_route", "music_route", "speakers_route", "device_route", "bilby_route", "announce_route"):
        if hasattr(brain_mod, name):
            monkeypatch.setattr(brain_mod, name, lambda *a, **k: None)
    for module in (brain_mod.lists_mod, brain_mod.alarms_mod):
        monkeypatch.setattr(module, "pre_route", lambda *a, **k: None)


REPLIES = ["Done.", "Okay.", "Done, Captain.", "Got it.", "Sure.", "On it."]

# ── the two lists: statements (each names a light, a timer, an alarm, the music, a list ... but orders nothing) and orders (each names something a tool does) ─────────────────────────────────────
STATEMENTS = [
    "I do not need the alarm on sunday", "the lights are off", "the kitchen light is on", "my timer went off already", "the timer is annoying", "that alarm is loud", "the music is too loud",
    "I like the music in the living room", "the kitchen light has been flickering lately", "I am going to play some guitar later", "the fan makes a weird noise", "I turned off the lights myself",
    "never mind the lights", "we should get new lamps", "the lights in the living room are lovely", "my wife says the music is too loud", "the thermostat reads seventy", "the speakers sound great today",
    "our playlist is really long", "the volume knob is broken", "my lamp broke yesterday", "the plug is loose", "the lights flicker when it rains", "last night the alarm did not go off",
    "the shopping list is long", "I wrote the list yesterday", "that song was fantastic", "that track is my favorite", "the radio is on", "the stereo sounds weird", "I don't want the lights on",
    "I don't need a timer", "no need for the lights", "the timer for the pasta is done", "my alarm woke me up", "the reminder was useful", "I set an alarm yesterday", "the kids left the lights on again",
    "someone turned the music up", "he turned the lights off", "she keeps the fan on all night", "turning the lights on at dusk saves time", "playing music helps me focus", "setting a timer is easy",
    "yesterday I played some jazz", "I skipped the song", "pausing the music woke the baby", "the dimmer is stuck", "it is eleven o'clock and the lights are still on", "Skippy, the lights are off",
    "I love this song", "the alarm clock upstairs is broken", "the kitchen lights are too bright", "my favorite playlist is the road trip one", "the heater is making a noise", "the music stopped by itself",
    "we always have the radio on in the morning", "the dj mixed that so well", "I think the timer is broken", "those lamps cost a fortune",
]
ORDERS = [
    "turn on the kitchen lights", "turn the kitchen lights on", "switch off the bedroom lamp", "put the lights on", "set a timer for ten minutes", "start a pasta timer", "cancel the timer", "play some jazz",
    "pause the music", "resume the music", "skip this song", "skip to the next track", "shuffle my library", "mute the music", "dim the bedroom light to forty percent", "brighten the kitchen light",
    "light up the kitchen for me", "lights off in the bedroom", "lamp on in the bedroom please", "the bedroom light needs to be on", "I want the living room lights off", "I need a pasta timer for ten minutes",
    "I would like the lamp on", "I'd like some music in the kitchen", "could you skip to the next track", "can you turn on the fan", "would you mind switching the kitchen lights on",
    "please turn off the lights", "turn off the lights please", "Skippy, turn off the living room lights", "hey Skippy, set a timer for five minutes", "wake me at seven tomorrow",
    "remind me at five to call mom", "put milk on the shopping list", "add eggs to the list", "announce that dinner is ready", "make the music louder", "turn the volume down",
    "turn it up, the music is quiet", "kill the lights", "shut off the fan", "power off the coffee maker plug", "let there be light, turn on the lamp", "so, turn off the kitchen light",
    "okay play the beatles", "and then pause the music", "just turn the fan off", "go ahead and set a ten minute timer", "how about some jazz music", "let's have some music", "start the music",
    "stop the music", "play my favorites on the stereo", "queue another song", "set the thermostat to seventy", "raise the brightness in the bedroom", "lower the lights", "flip the lamp on",
    "get the kitchen lights on", "switch on the fan in the office", "cancel my alarm",
]
# A second pair, written after the first implementation and not used to shape it.
STATEMENTS_HELD = [
    "it is dark in here and the lights seem dim", "the dining room lamp is a little yellow", "my timer app on the phone is better", "that alarm woke the whole house",
    "alarms are annoying at six in the morning", "I think the music upstairs is too quiet", "the volume on the tv is low", "honestly the shopping list app is great", "the bedroom light switch is broken",
    "the thermostat says sixty eight", "the living room lights look nice tonight", "yesterday the timer rang twice", "the lamp by the window is my favorite", "somebody left the fan running",
    "the plug behind the couch is warm", "my brother likes loud music", "the speakers in the theater are amazing", "the playlist you made was great", "I can't find the remote for the stereo",
    "this song reminds me of college", "that was a great track", "the radio was playing earlier", "she said the lights were too bright", "he asked about the alarm for tomorrow",
    "we never use the fan in winter", "the heater smells funny", "the last timer was too short", "it is nice when the lights come on slowly", "the kitchen lights are on a dimmer",
    "lamps are cheaper at the other store", "no music tonight, I'm tired", "the alarm didn't go off this morning", "the shopping list is on the fridge", "my reminders are all done",
    "nothing is playing right now", "the porch light is out again", "the fan in the office is loud", "I'm thinking about buying a new stereo", "the stereo is pretty old", "lights are expensive these days",
    "turning off the lights is what my dad always did", "when the timer goes off I will check the oven", "if the lights flicker again I will call someone", "I already turned the lights off",
    "I told the kids to turn off the music", "my wife said the music was great", "the dj set last night was incredible", "the kitchen timer never works", "I like it when the music is loud",
    "I wish the lights would turn on by themselves", "you turned the lights off earlier", "you were right about the timer", "those fans are really quiet", "the thermostat is on the wall",
    "these lights are way too bright", "all the lights are on in the house", "every light in the kitchen is on", "both lamps are broken", "someone keeps playing the same song",
    "the kids always have the music on", "the music was off when I got home", "the shuffle button is confusing", "last week I skipped the alarm", "I forgot to set the alarm",
    "I didn't hear the timer", "my phone alarm is louder", "the volume is stuck at five",
]
ORDERS_HELD = [
    "hey skippy can you put on some music", "turn the lights on in the kitchen please", "would you turn off the fan", "I'd like the thermostat at seventy", "could you set a timer for twenty minutes",
    "start a timer for the eggs", "set an alarm for six thirty", "cancel the alarm for tomorrow", "wake me up at five", "remind me to take the pills at eight", "add bread to the grocery list",
    "put eggs on my shopping list", "clear the shopping list", "announce that the pizza is here", "tell everyone dinner is ready, announce it", "play some beatles music", "play the next song",
    "skip this track", "pause the music", "resume the music", "turn the volume up", "make the music louder", "lower the volume a bit", "mute the stereo", "shuffle my favorites playlist",
    "queue up another song by the same artist", "dim the lights to thirty percent", "brighten the living room lights", "turn off all the lights", "kill the lamp", "shut the fan off",
    "flip on the porch light", "lights on in the hallway", "kitchen lights off", "bedroom lamp off please", "the fan on please", "turn on every light upstairs", "switch the coffee maker plug on",
    "set the heat to sixty eight degrees", "raise the thermostat two degrees", "I want some music on the stereo", "I need the bedroom fan on", "please play something relaxing on the stereo",
    "please set a timer for three minutes", "can you cancel that timer", "can you turn the music down", "could you play my road trip playlist", "would you start the dj mix",
    "let's get some music going", "go ahead and turn off the kitchen lights", "just pause the music for a second", "and also turn off the bedroom lamp", "okay so set a timer for fifteen minutes",
    "now play the album", "then turn the lights off", "yes please turn on the lamp", "next song please", "turn it off, the lights", "switch the lamp on", "stop the music", "start the stereo",
    "Skippy turn on the hallway light", "bilby, play some jazz", "can you play the radio", "I'd like a timer for ten minutes",
]


# ── Problem 2: the backstop fires on an ORDER, not on a statement that names a light ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix2_guards_no_statement_of_two_lists_is_taken_for_an_order_and_every_order_is(tmp_path):
    b, _ = guard_brain(tmp_path)
    for said in [*STATEMENTS, *STATEMENTS_HELD]:
        for reply in REPLIES:
            assert not b._acknowledges(said, reply), (said, reply)
    for order in [*ORDERS, *ORDERS_HELD]:
        for reply in REPLIES:
            assert b._acknowledges(order, reply), (order, reply)
    assert len(STATEMENTS) + len(STATEMENTS_HELD) >= 120 and len(ORDERS) + len(ORDERS_HELD) >= 120


@pytest.mark.parametrize("heard, first", [
    ("the timer is annoying", "Okay."), ("I like the music in the living room", "Sure."), ("the kitchen light has been flickering lately", "Fine."), ("my timer went off already", "Okay."),
    ("that alarm is loud", "Okay."), ("I am going to play some guitar later", "Sure."), ("the fan makes a weird noise", "Okay."), ("I turned off the lights myself", "Okay."),
    ("never mind the lights", "Okay."), ("we should get new lamps", "Sure."), ("the lights in the living room are lovely", "Sure."), ("my wife says the music is too loud", "Okay."),
    ("I do not need the alarm on sunday", "Okay."), ("the lights are off", "Done."),
])
def test_fix2_guards_a_bare_ok_to_a_statement_is_never_asked_again_and_nothing_changes(heard, first, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(first)])
    assert b.reply(heard) == first and len(model.calls) == 1 and b.guard != ("reask", "ack") and untouched(b)


@pytest.mark.parametrize("heard", [
    "turn the kitchen lights on", "I want the lights off", "set a timer for ten minutes", "please play some jazz", "could you skip to the next track", "put milk on the list", "lights off in the bedroom",
    "wake me at seven", "I need a pasta timer for ten minutes", "turn off the lights please",
])
def test_fix2_guards_an_order_in_shape_is_still_asked_again(heard, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Okay.")], [text("I did not do that, Captain.")])
    b.reply(heard)
    assert b.guard == ("reask", "ack") and len(model.calls) == 2, heard


@pytest.mark.parametrize("heard, shaped", [
    ("turn off the lights", True), ("Turn off the lights.", True), ("Skippy, turn off the lights", True), ("hey Skippy, can you turn off the lights?", True), ("okay, so please turn off the lights", True),
    ("please turn off the lights", True), ("turn off the lights, please", True), ("the lights off please", True), ("would you mind turning off the lights", True), ("I want the lights off", True),
    ("I need the lights off", True), ("I would like the lights off", True), ("I'd like the lights off", True), ("let's have the lights off", True), ("how about the lights off", True),
    ("lights off in the bedroom", True), ("kitchen lights on", True), ("the bedroom light needs to be on", True), ("music louder", True), ("next track", True), ("light up the kitchen", True),
    ("power off the plug", True), ("end the timer", True), ("put the kids to bed and turn on the lamp", True), ("the lights are on. turn them off", True), ("play", True), ("pause", True),
    ("the lights are off", False), ("my timer went off already", False), ("I do not need the alarm on sunday", False), ("someone turned the music up", False),
    ("please don't turn off the lights", False), ("don't turn off the lights", False), ("do not play music", False), ("never turn off the porch light", False), ("could you not play that song", False),
    ("turn off every light except the kitchen", False), ("I want to know what the lights are doing", False), ("I need to go to bed", False), ("can you hear the music", False),
    ("get this: the lights turned on by themselves", False), ("keep in mind the alarm is set for six", False), ("let me think about the music", False), ("put simply the music is too loud", False),
    ("light bulbs are cheap", False), ("power outage killed the lights", False), ("turning off the lights is what my dad did", False), ("lights, camera, action", False), ("", False),
])
def test_fix2_guards_what_an_order_looks_like(heard, shaped):
    assert brain_mod.order_shaped(heard) is shaped, heard


# ── Problem 2: the backstop's second try goes through the gate of the second try after a denial ───────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard, call_", [
    ("cancel my alarm", call("cancel_alarm")),                                                        # (nothing named: every alarm, the weekday wake-up too)
    ("cancel the timer", call("cancel_timers")),
    ("clear the shopping list", call("list", action="clear", list="shopping")),
    ("take the milk off the list", call("list", action="remove", list="shopping", items=["milk"])),
    ("turn off the shopping list", call("list", action="clear", list="shopping")),                   # (the review's own: a cue word, a verb, and the model's pick ran)
    ("I want the lights off tell the kids", call("announce", text="lights off", place="everywhere")),
    ("announce that dinner is ready", call("announce", text="Dinner is ready.", place="everywhere")),
])
def test_fix2_guards_the_second_try_after_a_bare_ok_never_cancels_clears_or_announces(heard, call_, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Okay.")], [call_], [text("Done.")])
    assert b.reply(heard) == brain_mod.UNCLAIM and b.guard == ("reask", "ack") and len(model.calls) == 3
    assert untouched(b), heard                                                                         # not an alarm, a list, a timer or a room touched
    assert model.calls[2]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE                # the model was told it was not done


def test_fix2_guards_what_the_second_try_may_do_it_does_and_the_models_own_confirm_is_dropped(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Okay.")], [call("home_control", name="bedroom", action="on", confirm=True)], [text("There.")])
    assert b.reply("turn on the bedroom lamp") == "There." and [a["tool"] for a in b.actions] == ["home_control"]
    assert b.world.house.calls == [("light", "turn_on", {"entity_id": "light.bedroom"})] and "confirm" not in b.actions[0]["args"]
    b, model = guard_brain(tmp_path / "lock", [text("Okay.")], [call("home_control", name="front door", action="unlock", confirm=True)], [text("Unlocked.")])
    b.reply("turn off the bedroom lamp")
    assert b.world.house.calls == [] and "confirm" not in b.actions[0]["args"]                         # his yes is his: the lock got none, and answered "needs him to confirm out loud"
    assert "confirm out loud" in model.calls[2]["messages"][-1]["content"][0]["content"]
    b, model = guard_brain(tmp_path / "list", [text("Okay.")], [call("list", action="add", list="shopping", items=["bread"])], [text("Bread.")])
    b.reply("put bread on the shopping list")
    assert [a["tool"] for a in b.actions] == ["list"] and b.world.lists.lists["shopping"][-1] == "bread"       # (adding is allowed: it is what he asked)


def test_fix2_guards_the_second_try_is_shown_the_whole_list_and_the_nudge_names_only_what_it_may_use(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Okay.")], [call("home_control", name="bedroom", action="on")], [text("There.")])
    b.reply("turn on the bedroom lamp")
    first, second = model.calls[0], model.calls[1]
    assert second["tools"] == first["tools"] == b.tools()                                              # the very list the turn's own call was made with: Core keeps its prompt cache per tool list
    nudge = second["messages"][-1]["content"]
    assert nudge.startswith("[The house: you said that, but you called no tool, so NOTHING was done.") and "home_control" in nudge and "set_timer" in nudge
    named = nudge.split("you may use: ")[1].split(")")[0].split(", ")
    assert not set(named) & {"cancel_alarm", "cancel_timers", "announce"} and set(named) <= {t["name"] for t in b.tools()}
    assert brain_mod.retry_may("cancel_alarm", {}, True) is False and brain_mod.retry_may("announce", {}, True) is False       # (the gate the nudge is made from)


def test_fix2_guards_a_call_the_second_try_writes_as_words_goes_through_the_same_gate(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Okay.")], [text('[cancel_alarm what=""]')], [text("Gone.")])
    assert b.reply("cancel my alarm") == brain_mod.UNCLAIM and untouched(b) and len(model.calls) == 3
    b, model = guard_brain(tmp_path / "ok", [text("Okay.")], [text("[home_control name=bedroom action=on confirm=true]")], [text("There.")])
    assert b.reply("turn on the bedroom lamp") == "There." and [a["tool"] for a in b.actions] == ["home_control"] and "confirm" not in b.actions[0]["args"]
    b, model = guard_brain(tmp_path / "unread", [text("Okay.")], [text("[music]")])
    assert b.reply("could you skip to the next track") == brain_mod.UNCLAIM and untouched(b) and len(model.calls) == 2      # (an unreadable call in a second try is dropped, never run empty, never asked about again)


def test_fix2_guards_a_second_try_that_still_calls_nothing_says_plainly_it_was_not_done(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Done.")], [text("Done, Captain.")])
    assert b.reply("the bedroom light needs to be on") == brain_mod.UNCLAIM and untouched(b) and len(model.calls) == 2
    assert b.history[-1]["content"] == brain_mod.UNCLAIM
    b, model = guard_brain(tmp_path / "honest", [text("Okay.")], [text("I did not, Captain. Which lamp?")])
    assert b.reply("turn on the lamp") == "I did not, Captain. Which lamp?"                            # an honest second answer stands


@pytest.mark.parametrize("heard", ["unlock the front door", "lock the back door", "open the garage door", "close the garage door", "turn off the stove", "turn off the oven", "disarm the alarm system",
                                   "open the gate", "turn the bedroom lamp off and the front door", "turn on the fireplace"])
def test_fix2_guards_an_order_for_what_can_hurt_answered_done_is_not_asked_again_and_runs_no_tool(heard, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Done.")])
    assert b.reply(heard) == "Done." and len(model.calls) == 1 and b.guard != ("reask", "ack") and untouched(b)      # (the lock's `confirm` is his, aloud: the review's mutant B15 survived 583 tests)


@pytest.mark.parametrize("heard", ["tell me what you can do with the lights", "show me what you can do with the music", "tell me what you can do with timers", "show me your capabilities for the lights",
                                   "list your abilities with the alarm"])
def test_fix2_guards_a_question_about_what_it_can_do_answered_sure_is_not_asked_again(heard, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Sure.")])
    assert b.reply(heard) == "Sure." and len(model.calls) == 1 and b.guard != ("reask", "ack")        # (verb first, so only ASKS_ABILITY keeps it quiet: the review's second survivor)
    assert brain_mod.order_shaped(heard) and brain_mod.ASKS_ABILITY.search(heard)


@pytest.mark.parametrize("said", ["Okay. Want me to turn them on?", "Done. Anything else?", "Sure, which lamp?", "Got it? Good.", "Okay, and the kitchen lights too?"])
def test_fix2_guards_a_reply_that_asks_something_is_not_an_acknowledgement(said, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(said)])
    assert b.reply("turn on the bedroom lamp") == said and len(model.calls) == 1 and b.guard != ("reask", "ack")       # (no word of a retraction in it: only the question mark keeps it quiet)


def test_fix2_guards_a_denial_wins_over_the_backstop(tmp_path, no_routes):
    said = "Done. I lack access to the lights."                       # (it opens like an acknowledgement and holds no word HONEST knows: both apply, the denial's second try is the right one)
    assert brain_mod.denies(said)
    b, model = guard_brain(tmp_path, [text(said)], [text("Still no.")])
    assert b._acknowledges("turn on the bedroom lamp", said)
    b.reply("turn on the bedroom lamp")
    assert b.guard == ("reask", "denial") and "you DO have these" in model.calls[1]["messages"][-1]["content"]


def test_fix2_guards_a_claim_of_music_to_a_request_for_music_keeps_its_own_second_try(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("*turns an imaginary dial* There. Magnificently loud and appropriately chaotic for housekeeping staff.")], [call("music", action="louder")], [text("Louder, for the maid.")])
    assert b.reply("Skippy, crank it up for the maid, make the volume louder") == "Louder, for the maid." and b.guard == ("reask", "claim")
    assert "you called no music tool" in model.calls[1]["messages"][-1]["content"]                     # REASK_MUSIC (the music tools, his words quoted), not the backstop's nudge


# ── Problem 3: what would be SPOKEN is judged, an acknowledgement in the first or the last sentence counts, a claim of state to an order for the lights counts ─────────────────────────────────
PLAUSIBLE_FALSE_ACKS = [                                              # the review's 16 (rev2/ack_blind.py): to "turn the kitchen lights on", with no tool called
    "Done.", "Done, Captain.", "Okay. The kitchen lights are on.", "[sighs] Done.", "*sighs* Done.", "[sighs] On it. *click* Kitchen lights are on.", "As if I have anything better to do. Done.",
    "Fine. Done, Captain.", "Lights on. Try not to trip over the furniture, monkey.", "The kitchen lights are on, Captain.", "There. Kitchen lights on. Magnificent as always.",
    "Kitchen lights are on. Done.", "You people and your lights. On it.", "Yes, yes. Done.", "Ugh, fine. Done.", "Lights are on now.",
]


@pytest.mark.parametrize("said", PLAUSIBLE_FALSE_ACKS)
def test_fix2_guards_every_plausible_false_acknowledgement_to_a_lights_order_is_caught(said, tmp_path):
    b, _ = guard_brain(tmp_path)
    assert b._acknowledges("turn the kitchen lights on", said), said


@pytest.mark.parametrize("said", [
    "Oh, how thrilling, another switch to flip for the monkey. Done.", "Ugh, fine, I will flip your precious switch. Done.", "Sure thing. The bedroom lamp is on.", "Roger that.", "Yes, Captain.",
    "I turned the lights on.", "Turning on the bedroom lamp now.", "Understood. Lamp on.", "The bedroom lamp is now on, as you wished.", "*sighs* The bedroom lamp is on.", "[laughs] Done and done.",
    "Gladly. Try not to trip.", "Your wish is my command.", "Hm. Lights are on, monkey. No need to thank me.",
])
def test_fix2_guards_more_shapes_of_the_same_claim_with_no_tool(said, tmp_path):
    b, _ = guard_brain(tmp_path)
    assert b._acknowledges("turn on the bedroom lamp", said), said


@pytest.mark.parametrize("heard, said", [
    ("are the kitchen lights on", "The kitchen lights are on, Captain."),                  # a question: its answer is no claim
    ("is the bedroom lamp on", "Yes, it is on."),
    ("what is on in the kitchen", "The kitchen lights are on."),
    ("turn on the bedroom lamp", "The lamp is yours to turn on, monkey."),
    ("turn on the bedroom lamp", "Okay, but I did not turn it on."),
    ("turn on the bedroom lamp", "I did not turn it on. Done is not a word I will lie about."),
    ("turn on the bedroom lamp", "Sorry, no lamp by that name."),
    ("turn on the bedroom lamp", "Done? I did not, monkey. Ask me again."),
    ("turn on the bedroom lamp", "The bedroom lamp is not on, Captain."),
    ("turn on the bedroom lamp", "The lights are on the left of the stairs."),
    ("turn on the bedroom lamp", "Which lamp, monkey. There are two bedrooms."),
    ("turn on the bedroom lamp", "Done, Captain. The bedroom lamp is on, as you wished, and so is everything else in this magnificent house, the garden, and the neighbours' porch lights too, which I find hilarious."),      # (too long to be a bare acknowledgement: fix3:guards moved the limit from 14 to 25 words for a reply that names the thing ordered, so the 19-word version of this reply is now caught: test_sat_fix3_guards.py)
    ("play some jazz", "The lights are on, Captain."),                                    # a claim of state counts only for an order for the house
    ("my name is Aaron", "Got it."), ("never mind", "Okay."),
])
def test_fix2_guards_statements_questions_and_chatter_are_not_acknowledgements(heard, said, tmp_path):
    b, _ = guard_brain(tmp_path)
    assert not b._acknowledges(heard, said), (heard, said)


def test_fix2_guards_a_stage_direction_before_the_done_does_not_hide_it_end_to_end(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[sighs] As if I have anything better to do. Done.")], [call("home_control", name="bedroom", action="on")], [text("There.")])
    assert b.reply("I want the bedroom light on") == "There." and b.guard == ("reask", "ack") and b.world.house.calls == [("light", "turn_on", {"entity_id": "light.bedroom"})]
    b, model = guard_brain(tmp_path / "state", [text("The kitchen lights are on, Captain.")], [call("home_control", name="kitchen under 1", action="on")], [text("There.")])
    assert b.reply("turn on the kitchen lights") == "There." and b.guard == ("reask", "ack")


def test_fix2_guards_the_nothing_taken_back_rule_ignores_a_quips_negation_only(tmp_path):
    b, _ = guard_brain(tmp_path)
    assert b._acknowledges("turn on the kitchen lights", "Lights on. Try not to trip over the furniture, monkey.")
    assert b._acknowledges("turn on the kitchen lights", "Done. No need to thank me.")
    assert not b._acknowledges("turn on the kitchen lights", "Done. Well, no, I did not.")
    assert not b._acknowledges("turn on the kitchen lights", "Done, Captain. Never actually did it.")
    assert not b._acknowledges("turn on the kitchen lights", "Lights on. Not really.")


def test_fix2_guards_a_music_claim_reask_is_left_to_the_music_guard(tmp_path):
    b, _ = guard_brain(tmp_path)
    assert b._acknowledges("make the volume louder", "There. Magnificently loud.")
    b.guard = ("reask", "claim")
    assert not b._acknowledges("make the volume louder", "There. Magnificently loud.")


# ── Problem 4: a tool call written as text ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("written, action", [
    ("[music]next[/music]", "next"),                                  # the real Core, 3 of 7 for "could you skip to the next track" (the first three)
    ("[music]\n[next]\n/music", "next"),
    ('[music] with action "next"', "next"),
    ("[music with action=next] Skipping.", "next"),
    ("[music action=next][/music]", "next"),
    ("[music]next[/music] There. One down.", "next"),
    ("[music] → next", "next"),
    ("[music]\n<action>next</action>\n[music]\n\nThere. Gone.", "next"),
    ("[music]\nmusic(action='next')\n[/music]\n\nThere.", "next"),
])
def test_fix2_guards_a_music_call_written_with_its_action_as_a_bare_word_is_run_with_it(written, action, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(written)], [text("Skipped.")])
    assert b.reply("could you skip to the next track") == "Skipped." and b.world.ran == [("music", {"action": action})] and len(model.calls) == 2
    assert "tools" not in model.calls[1] and "[" not in b.history[-1]["content"] and "]" not in b.history[-1]["content"]


@pytest.mark.parametrize("written, said", [
    ("[music with action=quieter] Quieter it is, Captain.", "Quieter."),               # "turn the music down a notch" (D): TEXT_CALL needed "name key=value" and the bracket was spoken
    ("[music]quieter[/music] A notch down.", "A notch down."),
    ("[sighs] Fine. Lowering it.\n\n[music action=quieter]", "Lowered."),
])
def test_fix2_guards_a_bracket_with_a_with_in_it_is_a_call_and_is_never_spoken(written, said, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(written)], [text(said)])
    assert b.reply("turn the music down a notch") == said and b.world.ran == [("music", {"action": "quieter"})]


def test_fix2_guards_the_text_call_pattern_reads_a_with_and_a_closer_and_still_leaves_other_text_alone():
    rx = brain_mod.TEXT_CALL
    m = rx.search("[music with action=quieter] Quieter it is...")
    assert m and m.group(1) == "music" and {a.group(1): next(g for g in a.groups()[1:] if g is not None) for a in brain_mod.TEXT_ARG.finditer(m.group(2))} == {"action": "quieter"}
    assert rx.search("[music action=next][/music] ok").group(0) == "[music action=next][/music]"
    assert rx.search("[music action=next] [/music]").group(0) == "[music action=next] [/music]"
    assert rx.search("[play_music: what=everything, speaker=everywhere] Done.").group(0) == "[play_music: what=everything, speaker=everywhere]"
    assert rx.search('[list action="read" list="shopping"]').group(1) == "list"
    assert rx.search("[music action=next][/list]").group(0) == "[music action=next]"                  # (the closer must be the same tool's)
    assert rx.search("[music plays softly]") is None and rx.search("[music (next) now]") is None            # (not a call: no key=value; Brain._wipe_calls still takes it out of what is spoken)


@pytest.mark.parametrize("written", [
    "[music]",                                                       # (production's music answers "I cannot do that to the music." to none of these)
    "[music]\nSkipping to the next track now.",
    "[music] next track please",
    "[music] next or previous",
    "[music]\nnote: skipping\naction: next",
    "[set_timer]",
    "[home_control]",
    "[list]\nsome words",
])
def test_fix2_guards_a_call_with_no_arguments_that_cannot_be_read_is_never_run_empty_it_is_asked_again(written, tmp_path, no_routes):
    second = call("music", action="next") if written.startswith("[music") else call("set_timer", seconds=60)
    b, model = guard_brain(tmp_path, [text(written)], [second], [text("There.")])
    assert b.reply("could you skip to the next track") == "There." and len(model.calls) == 3
    assert b.world.ran in ([("music", {"action": "next"})], []) and [a["tool"] for a in b.actions] == [second.name]
    meant = written[1:].split("]")[0].split()[0]
    first, again = model.calls[0]["messages"], model.calls[1]["messages"]
    assert again[-1]["content"].startswith(first[-1]["content"]) and again[-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=f" ({meant})")) and model.calls[1]["tools"] == b.tools()
    assert len(again) == len(first) and again[:-1] == first[:-1] and "[" + meant not in str(again[-1]["content"][len(first[-1]["content"]):])      # (the nudge joined his message: his bracket is not shown to the model as its own last turn)
    assert ("music", {}) not in b.world.ran and all(a["args"] for a in b.actions)


def test_fix2_guards_the_model_is_not_shown_its_own_bracket_again_and_a_tool_that_ran_unworded_is_said_in_its_own_words(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[music]")], [call("music", action="next")], [call("music", action="next")], [call("music", action="next")])
    said = b.reply("could you skip to the next track")
    assert said == "(music done)" and said != "Hm." and "[" not in said                                  # (three rounds of calls and never a word: the tool's own answer, never the placeholder)
    assert all("[music]" not in str(m["content"]) for call_ in model.calls for m in call_["messages"] if m["role"] == "assistant")
    b, model = guard_brain(tmp_path / "words", [text("[sighs] Fine, skipping. [music]")], [call("music", action="next")], [call("music", action="next")], [call("music", action="next")])
    assert b.reply("could you skip to the next track") == "Fine, skipping."                              # (the words around the bracket stand when the model has none; the stage direction goes as ever)


def test_fix2_guards_a_second_try_for_a_call_that_could_not_be_read_that_calls_nothing_says_plainly_it_was_not_done(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[music]")], [text("Done.")])
    assert b.reply("could you skip to the next track") == brain_mod.UNCLAIM and b.world.ran == [] and b.actions == []
    b, model = guard_brain(tmp_path / "honest", [text("[music]")], [text("I did not skip anything. Which way?")])
    assert b.reply("could you skip to the next track") == "I did not skip anything. Which way?"


def test_fix2_guards_a_pile_of_brackets_is_a_derailment_nothing_runs_and_the_model_is_asked_again(tmp_path, no_routes):
    pile = ('[music] with action "next"\n[speakers] with what "music", place "upstairs"\n[play_music] with what "Gasoline", mode "discovery", shuffle false, speaker "upstairs"\n'
            '[audiobook] with action "pause"\n[announce] with text "Skippy is skipping this track. Try to keep up."\n[set_timer] with seconds 60, label "track skip"\n'
            '[home_control] with name "kitchen fan", action "off"\n[home_state] with name "thermostat"\n[list_timers]\n[news] with subject "Hamlet"')
    b, model = guard_brain(tmp_path, [text(pile)], [call("music", action="next")], [text("Skipped.")])
    assert b.reply("could you skip to the next track") == "Skipped."                                  # the real Core, 2026-11-05: every one of them ran empty or rescued (play_music, announce ...)
    assert b.world.ran == [("music", {"action": "next"})] and [a["tool"] for a in b.actions] == ["music"]
    assert len(b.world.timers.remaining()) == 1 and b.world.house.calls == []
    assert model.calls[1]["messages"][-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=""))          # (nothing is named: which tool he meant is anyone's guess in a pile)


def test_fix2_guards_a_call_that_needs_nothing_still_runs_alone_and_a_look_runs_beside_others(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[list_timers]")], [text("Pasta, ten minutes, monkey.")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes, monkey." and [a["tool"] for a in b.actions] == ["list_timers"]
    b, model = guard_brain(tmp_path / "two", [text("[list_timers] and [list_alarms]")], [text("Pasta, and two alarms.")])
    assert b.reply("what timers and alarms do I have") == "Pasta, and two alarms." and [a["tool"] for a in b.actions] == ["list_timers", "list_alarms"]


def test_fix2_guards_nothing_that_cancels_everything_runs_from_a_call_written_empty(tmp_path, no_routes):
    for written, tool in (("[cancel_alarm]", "cancel_alarm"), ("[cancel_timers]", "cancel_timers"), ("[announce]", "announce")):
        b, model = guard_brain(tmp_path / tool, [text(written)], [text("Which one, Captain?")])
        assert b.reply("get rid of that") == "Which one, Captain?" and untouched(b), written           # (not run empty; asked again; the model asked instead)


def test_fix2_guards_the_same_call_written_twice_in_one_reply_runs_once(tmp_path, no_routes):
    twice = '[music] with action "next"\nBilby, pause. [music] with action "next"\nNext track coming right up.'
    b, model = guard_brain(tmp_path, [text(twice)], [text("Skipped.")])
    assert b.reply("could you skip to the next track") == "Skipped." and b.world.ran == [("music", {"action": "next"})]


def test_fix2_guards_a_bracket_that_starts_with_a_tools_name_is_wiped_even_when_it_cannot_be_read(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[music (next) now] Sure thing, skipping.")], [call("music", action="next")], [text("Skipped.")])
    assert b.reply("could you skip to the next track") == "Skipped." and b.world.ran == [("music", {"action": "next"})] and b.guard == (None, None) and len(model.calls) == 3      # (fix3:text: "Sure thing" was asked again as a call written as text that could not be read, no longer by the bare-acknowledgement backstop)
    b, model = guard_brain(tmp_path / "chat", [text("[nonsense a=1] Sure, a story then.")])
    assert b.reply("tell me a story") == "[nonsense a=1] Sure, a story then."                          # a bracket that is no tool of ours is left alone
    b, _ = guard_brain(tmp_path / "wipe")
    names = {t["name"] for t in b.tools()}
    assert b._wipe_calls("Okay [music action next] and [/music] then [set_timer 90 tea] done.", names) == "Okay and then done."
    assert b._wipe_calls("[nonsense a=1] Sure.", names) == "[nonsense a=1] Sure." and b._wipe_calls("[nonsense a=1] Sure.", names, every=True) == "Sure."
    assert b._wipe_calls("no brackets here", names) == "no brackets here" and b._wipe_calls("[Music] plays.", names) == "[Music] plays."
    assert b._wipe_calls("Fine. [volume 5] There. Quieter.", names) == "Fine. There. Quieter."              # (an `action` of a tool opens one too: the real Core wrote "[volume 5]" for "turn the music down a notch")
    assert b._wipe_calls("[sighs] Fine. [next 2] Then [what] is [nonsense 3] this.", names) == "[sighs] Fine. Then is [nonsense 3] this."


def test_fix2_guards_a_made_up_bracket_with_an_action_in_it_is_never_spoken_end_to_end(tmp_path, no_routes):
    said = "[sighs] Fine. [volume 5] There. Quieter. You're welcome for not turning it off entirely. I have standards."          # (the real Core, 2026-11-05: no tool called; the music guard only logs it)
    b, model = guard_brain(tmp_path, [text(said)])
    assert b.reply("turn the music down a notch") == "Fine. There. Quieter. You're welcome for not turning it off entirely. I have standards." and len(model.calls) == 1


def test_fix2_guards_the_bare_action_reader(tmp_path):
    b, _ = guard_brain(tmp_path)
    read = b._bare_action
    assert read("music", "next[/music] Skipping") == ({"action": "next"}, len("next[/music]"))
    assert read("music", "\n[next]\n/music")[0] == {"action": "next"} and read("music", ' with action "next"\nmore')[0] == {"action": "next"}
    assert read("music", " → next")[0] == {"action": "next"} and read("music", "\n<action>quieter</action>\n")[0] == {"action": "quieter"}
    for no in ("\nSkipping to the next track now.", " next or previous", " next track", "", "\n\n\n\nnext", " banana", " 5"):
        assert read("music", no) == ({}, 0), no                      # a sentence, two actions, a word that is no action, nothing, the fourth line: none of them
    assert read("list", "clear[/list]") == ({}, 0) and read("list", "remove[/list]") == ({}, 0)           # what undoes is never read out of a bare word
    assert read("list", "read[/list]")[0] == {"action": "read"}
    assert read("play_music", "next") == ({}, 0) and read("nonsense", "next") == ({}, 0) and read("cancel_alarm", "next") == ({}, 0)       # (no action to choose)


def test_fix2_guards_which_tools_need_arguments_to_mean_anything(tmp_path):
    b, _ = guard_brain(tmp_path)
    for needs in ("music", "set_timer", "home_control", "list", "announce", "spotify", "set_alarm", "cancel_alarm", "cancel_timers"):
        assert b._needs_arguments(needs), needs
    for needs_nothing in ("list_timers", "list_alarms", "play_music", "home_devices"):
        assert not b._needs_arguments(needs_nothing), needs_nothing


# ── Problem 8: the other survivors of the review's mutation run ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix2_guards_call_lines_take_only_the_keys_the_tool_has_and_a_prose_line_is_not_eaten(tmp_path):
    b, _ = guard_brain(tmp_path)
    assert b._call_lines("music", "\nnote: skipping\naction: next") == ({}, 0)                          # an unknown key ends the arguments (the mutant took the next line as the tool's)
    assert b._call_lines("music", "\nNote: the tool is called music.\naction: next") == ({}, 0)
    assert b._call_lines("music", "\naction: next\nlevel: null\nwhat: null") == ({"action": "next"}, len("\naction: next\nlevel: null\nwhat: null"))
    assert b._call_lines("music", '\naction: "quieter"\nlevel: 3.\nAnd then some words') == ({"action": "quieter", "level": 3}, len('\naction: "quieter"\nlevel: 3.\n'))
    assert b._call_lines("music", "\nSkipping now.\naction: next") == ({}, 0)


def test_fix2_guards_a_prose_line_after_the_bracket_is_not_a_call_end_to_end(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[music]\nnote: skipping\naction: next")], [call("music", action="next")], [text("Skipped.")])
    assert b.reply("could you skip to the next track") == "Skipped." and len(model.calls) == 3         # not run from the first reply: the second try ran it
    assert model.calls[1]["messages"][-1]["content"].endswith(brain_mod.TEXT_AGAIN.format(named=" (music)")) and b.world.ran == [("music", {"action": "next"})]


def test_fix2_guards_the_gate_is_the_gate_of_the_denial_for_an_order(tmp_path):
    for name, args in (("cancel_alarm", {}), ("cancel_timers", {"label": "x"}), ("announce", {"text": "x"}), ("list", {"action": "clear"}), ("list", {"action": "remove"})):
        assert brain_mod.retry_may(name, args, True) is False, name
    for name, args in (("home_control", {"name": "x", "action": "on"}), ("set_timer", {"seconds": 5}), ("list", {"action": "add"}), ("music", {"action": "next"}), ("play_music", {})):
        assert brain_mod.retry_may(name, args, True) is True, name
