"""The adversarial review of the house speed change (2026-11-05), the fixes to the brain's ROUTES (brain.py's timer_route, light_route, strip_stage_directions and the Brain methods
that serve them, timers.py, musicguard.py). Every test here fails on the code before the fix; the numbers are in the report.

P3   "set a timer for five minutes", then "cancel the five minute timer" cancelled nothing (the label was "5 minutes", the cancel looked for the words)
P4   strip_stage_directions deleted emphasised words ("Do *not* open the garage door." became "Do open the garage door.")
P7   "turn off every light" switched the room he is in only
P9   a curt question with a question mark acted; "the five minute timer" set a timer; a label with an article was lost without a word
P10  routers, fridges, pumps ... are never switched by a route; plug and fan groups are the model's; "turn off the sony" is still one speaker switch
P1   natural ways to say it (in here, lights out, back on, I would like ..., ", Skippy") reach the route: measured on a battery of about 200 phrasings, in five rooms, on two houses
P12b a light called "living track middle" is not music
P13  the behaviours the mutation run found unpinned: what _confirmation confirms, what light_route refuses, what _light_resolves drops
"""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain as brain_mod  # noqa: E402
import house as house_mod  # noqa: E402
import musicguard  # noqa: E402
from brain import Brain  # noqa: E402
from timers import Timers  # noqa: E402

NOW = "Thursday, November 05, 2026, 14:45"


def text(t):
    return NS(type="text", text=t)


def tool(_name, **args):
    return NS(type="tool_use", id="tu_" + _name, name=_name, input=args)


class Model:
    """A scripted model that remembers every call it was asked. Asked more than it was told to answer, it raises (a routed command must never reach it)."""

    def __init__(self, *rounds):
        self.rounds, self.calls = list(rounds), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if not self.rounds:
            raise AssertionError("the model was asked, and this test says it must not be")
        return NS(content=self.rounds.pop(0), stop_reason=None)


def fake_tools(*names, results=None):
    ran = []

    def run(name, args):
        ran.append((name, dict(args)))
        return (results or {}).get(name, f"({name} done)")
    return ([{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in names], run), ran


def timer_brain(*rounds, **kw):
    clock = [1_793_000_000.0]
    timers = Timers(clock=lambda: clock[0])
    model = Model(*rounds)
    b = Brain(model, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: NOW, **kw)
    b.sleep = lambda s: None
    return b, model, timers, clock


def row(eid, name, state="off", **attrs):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": name, **attrs}}


NINE = [row("light.bedroom", "Bedroom"), row("light.kitchen_under_1", "kitchen under 1"), row("light.kitchen_under_2", "kitchen under 2"), row("light.kitchen_under_3", "kitchen under 3"),
        row("light.track_stove_light", "track stove light"), row("light.living_track_middle", "living track middle"), row("light.living_track_throne", "living track throne"),
        row("light.deck_sliding_door", "Deck sliding door"), row("light.security_light", "security Light")]
RICH = NINE + [row("switch.coffee_maker", "Coffee Maker"), row("switch.desk_lamp", "Desk Lamp"), row("switch.living_room_tv", "Living Room TV"), row("switch.router", "Router"),
               row("switch.garage_fridge", "Garage Fridge"), row("switch.sump_pump", "Sump Pump"), row("switch.washer", "Washer"), row("switch.space_heater", "Space Heater"),
               row("switch.kettle", "Kettle"), row("fan.bedroom_fan", "Bedroom Fan"), row("fan.office_fan", "Office Fan"), row("fan.living_room_fan", "Living Room Fan"),
               row("lock.front_door", "Front Door", "locked"), row("cover.garage_door", "Garage Door", "closed", device_class="garage"),
               row("sensor.bedroom_temperature", "Bedroom Temperature", "70")]
RICH_AREAS = {"switch.coffee_maker": "Kitchen", "switch.desk_lamp": "Office", "switch.router": "Office", "switch.garage_fridge": "Garage", "switch.sump_pump": "Garage",
              "switch.washer": "Garage", "switch.space_heater": "Office", "switch.kettle": "Kitchen"}
RISKY_IDS = {"switch.router", "switch.garage_fridge", "switch.sump_pump", "switch.space_heater", "switch.kettle", "lock.front_door", "cover.garage_door"}


class FakeHouse(house_mod.House):
    """A real House (its names, its groups, its guard rails) over rows in memory; every service call is recorded as (domain, service, entity)."""

    def __init__(self, rows=None, areas=None):
        self.rows, self._areas, self.calls = list(NINE if rows is None else rows), dict(areas or {}), []

    def states(self, fresh=False):
        return self.rows

    def areas(self):
        return self._areas

    def _service(self, domain, service, data):
        self.calls.append((domain, service, data["entity_id"]))


def house_brain(*rounds, rows=None, areas=None, room="", **kw):
    h = FakeHouse(rows, areas)
    b, model, timers, clock = timer_brain(*rounds, hass=h, **kw)
    b.room = room
    return b, model, h, timers


# ── P3: cancel a timer by what it is ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
CANCELS = [
    ("set a timer for five minutes", "cancel the five minute timer"), ("set a timer for five minutes", "cancel the 5 minute timer"),
    ("set a timer for five minutes", "cancel the five minutes timer"), ("set a timer for five minutes", "cancel the 5 minutes timer"),
    ("set a timer for five minutes", "Skippy, cancel the five minute timer please"), ("set a timer for 5 minutes", "cancel the five minute timer"),
    ("set a timer for ninety seconds", "cancel the ninety second timer"), ("set a timer for ninety seconds", "cancel the 90 second timer"),
    ("set a timer for 90 seconds", "cancel the ninety seconds timer"), ("set a timer for forty five seconds", "cancel the forty five second timer"),
    ("set a timer for one hour", "stop the one hour timer"), ("set a timer for an hour", "cancel the 1 hour timer"), ("set a timer for one hour", "cancel the hour timer"),
    ("set a timer for half an hour", "cancel the thirty minute timer"), ("set a timer for half an hour", "cancel the half hour timer"),
    ("set a timer for an hour and a half", "cancel the ninety minute timer"), ("set a timer for twenty five minutes", "delete the 25 minute timer"),
    ("set a timer for five minutes", "cancel the timer for five minutes"), ("set a timer for ten minutes", "cancel the timer for 10 minutes"),
    ("set a pasta timer for ten minutes", "cancel the pasta timer"), ("set a pasta timer for ten minutes", "stop the pasta timer"), ("set a tea timer for three minutes", "cancel my tea timer"),
    ("set a timer for ten minutes", "cancel the timer"), ("set a timer for ten minutes", "cancel all timers"), ("set a timer for ten minutes", "cancel my timers"),
]


@pytest.mark.parametrize("setting, cancel", CANCELS)
def test_fixbr_a_timer_is_cancelled_by_the_words_he_set_it_with_no_model_call(setting, cancel):
    b, model, timers, clock = timer_brain()
    assert b.reply(setting).startswith("Timer set for") and len(timers.remaining()) == 1
    assert b.reply(cancel) == "Timer cancelled." and timers.remaining() == [] and model.calls == [], cancel
    assert [a["tool"] for a in b.actions] == ["cancel_timers"] and b.timing["route"] == "cancel_timers" and b.timing["model_s"] == []


def test_fixbr_a_length_that_is_not_running_is_left_to_the_model_and_nothing_is_cancelled():
    b, model, timers, clock = timer_brain([text("No ten minute timer is running, monkey. There is a five minute one.")])
    b.reply("set a timer for five minutes")
    assert b.reply("cancel the ten minute timer") == "No ten minute timer is running, monkey. There is a five minute one."
    assert len(model.calls) == 1 and "Cancelled 0 timers." in str(model.calls[0]["messages"][-1]["content"]) and timers.remaining() == [("5 minutes", 300)]
    assert b.actions[0]["tool"] == "cancel_timers" and "template" not in b.timing          # (the model words the honest answer: a templated "Timer cancelled." would be a lie)


def test_fixbr_a_length_cancels_that_length_only_not_a_longer_one_whose_name_contains_its_digits():
    b, model, timers, clock = timer_brain()
    b.reply("set a timer for twenty five minutes")
    b.reply("set a timer for five minutes")
    b.reply("set a timer for fifteen minutes")
    assert b.reply("cancel the five minute timer") == "Timer cancelled." and sorted(timers.remaining()) == [("15 minutes", 900), ("25 minutes", 1500)]
    assert b.reply("cancel the fifteen minute timer") == "Timer cancelled." and timers.remaining() == [("25 minutes", 1500)]
    assert timers.cancel("5 minutes") == 0 and timers.cancel("minutes") == 1 and timers.remaining() == []         # (a word that is not a length still matches as it always did)


def test_fixbr_an_unnamed_timer_of_that_length_goes_before_a_named_one_of_the_same_length():
    b, model, timers, clock = timer_brain()
    b.reply("set a pasta timer for five minutes")
    b.reply("set a timer for five minutes")
    assert b.reply("cancel the five minute timer") == "Timer cancelled." and timers.remaining() == [("pasta", 300)]
    assert b.reply("cancel the five minute timer") == "Timer cancelled." and timers.remaining() == []               # nothing else of that length: the named one is the five minute timer left


def test_fixbr_timers_compare_number_words_digits_and_unit_plurals_for_any_call_of_the_tool():
    clock = [1000.0]
    t = Timers(clock=lambda: clock[0])
    for said in ("five minute", "five minutes", "5 minute", "5 minutes", "Five Minutes", "5 min", "a five minute", "five-minute"):
        t.add(300)
        assert t.cancel(said) == 1 and t.remaining() == [], said
    for said, seconds in (("ninety second", 90), ("90 seconds", 90), ("one hour", 3600), ("1 hour", 3600), ("an hour", 3600), ("a minute", 60), ("twenty five minutes", 1500), ("forty five seconds", 45)):
        t.add(seconds)
        assert t.cancel(said) == 1 and t.remaining() == [], said
    t.add(300, "pasta")
    t.add(300)
    assert t.cancel("PAS") == 1 and t.remaining() == [("5 minutes", 300)]                                           # the substring rule for a name is as it was
    assert t.cancel("second") == 0 and t.cancel("minute") == 1 and t.cancel() == 0                                  # ("second" alone is no length; "minute" still matches the word)
    b, model, timers, clock = timer_brain()
    timers.add(300)
    assert b.run_tool("cancel_timers", {"label": "five minute"}) == "Cancelled 1 timer." and timers.remaining() == []        # the model's own call


def test_fixbr_ordinals_and_bare_units_are_no_timer_names_and_the_stop_of_a_bare_timer_stays_the_models():
    r = brain_mod.timer_route
    for heard in ("cancel the second timer", "cancel the first timer", "cancel the last timer", "cancel the other timer", "cancel the next timer", "cancel the new timer", "cancel the third timer",
                  "stop the timer", "stop all timers", "stop the timers", "kill the timer", "cancel the five minute pasta timer", "cancel the timer for the", "stop it", "cancel the seconds timer"):
        assert r(heard, 3) is None, heard
    assert r("cancel the minute timer", 3) == ("cancel_timers", {"label": "1 minute"}) and r("cancel the hour timer", 3) == ("cancel_timers", {"label": "1 hour"})        # ("the hour timer": an hour)
    b, model, timers, clock = timer_brain([text("Which one, monkey?")])
    b.reply("set a timer for ninety seconds")
    b.reply("set a pasta timer for ten minutes")
    assert b.reply("cancel the second timer") == "Which one, monkey?" and len(timers.remaining()) == 2             # (it used to cancel the 90 SECONDS timer: "second" is in its name)


def test_fixbr_the_cancel_route_says_the_length_the_way_the_timer_is_named():
    r = brain_mod.timer_route
    assert r("cancel the 5 minute timer", 4) == ("cancel_timers", {"label": "5 minutes"})
    assert r("cancel the five minutes timer", 4) == ("cancel_timers", {"label": "5 minutes"})
    assert r("stop the one hour timer", 4) == ("cancel_timers", {"label": "1 hour"})
    assert r("cancel the ninety second timer", 4) == ("cancel_timers", {"label": "90 seconds"})
    assert r("cancel the half hour timer", 4) == ("cancel_timers", {"label": "30 minutes"})
    assert r("cancel the timer for five minutes", 4) == ("cancel_timers", {"label": "5 minutes"})
    assert r("cancel the timer for pasta", 4) == ("cancel_timers", {"label": "pasta"})
    assert r("kill the pasta timer", 4) == ("cancel_timers", {"label": "pasta"}) and r("cancel the pasta timer", 4) == ("cancel_timers", {"label": "pasta"})
    assert r("cancel all timers", 4) == ("cancel_timers", {"label": ""}) and r("cancel every timer", 4) == ("cancel_timers", {"label": ""})
    assert r("cancel the timer", 1) == ("cancel_timers", {"label": ""}) and r("cancel the timer", 2) is None


def test_fixbr_a_name_is_no_length_and_a_length_is_no_name():
    clock = [1000.0]
    t = Timers(clock=lambda: clock[0])
    t.add(120, "second break")                                                                                     # a NAME with a unit word in it
    t.add(90)
    assert t.cancel("break") == 1 and t.remaining() == [("90 seconds", 90)]
    assert t.cancel("second break") == 0 and t.cancel("90 seconds") == 1


# ── P4: the stage directions, and the words that only look like one ──────────────────────────────────────────────────────────────────────────────────────────────────────
EMPHASIS = [
    ("Do *not* open the garage door.", "Do not open the garage door."), ("It is *not* on.", "It is not on."), ("The lights are *off*, not on.", "The lights are off, not on."),
    ("Never *ever* do that.", "Never ever do that."), ("*Oh*, look", "Oh, look"), ("I turned off *all* the lights.", "I turned off all the lights."), ("That is *very good*.", "That is very good."),
    ("It is **not** on", "It is not on"), ("You *do not* want that.", "You do not want that."), ("It *is not* locked.", "It is not locked."), ("It is *never* on.", "It is never on."),
    ("*This* is not a direction.", "This is not a direction."), ("Is it *no* longer on?", "Is it no longer on?"), ("I said *kitchen*, not bedroom.", "I said kitchen, not bedroom."),
]


@pytest.mark.parametrize("said, kept", EMPHASIS)
def test_fixbr_an_emphasised_word_is_spoken_without_its_asterisks_never_deleted(said, kept):
    assert brain_mod.strip_stage_directions(said) == kept


DIRECTIONS = [
    ("*sighs* It is nine.", "It is nine."), ("It is nine. *sighs*", "It is nine."), ("Fine. *chuckles* Fine.", "Fine. Fine."), ("*adjusts imaginary dial* There.", "There."), ("*clears throat* Ahem.", "Ahem."),
    ("*sighing deeply* Fine.", "Fine."), ("*Sighs.* Fine.", "Fine."), ("*laughed* Fine.", "Fine."), ("*pauses* Fine.", "Fine."), ("*dramatic pause* Fine.", "Fine."), ("*rolls eyes* Fine.", "Fine."),
    ("[laughs] Fine.", "Fine."), ("[clicks fingers] Fine.", "Fine."), ("Fine. [sighs deeply]", "Fine."), ("[pause] Fine.", "Fine."), ("Fine, [shrugs] fine.", "Fine, fine."),
    ("*hums* ok *grins* ok *snickers* ok", "ok ok ok"), ("[cough] [gasp] Fine.", "Fine."), ("Well [taps the can] fine.", "Well fine."),
]


@pytest.mark.parametrize("said, spoken", DIRECTIONS)
def test_fixbr_a_stage_direction_is_deleted_from_what_is_spoken(said, spoken):
    assert brain_mod.strip_stage_directions(said) == spoken


UNTOUCHED = ["2*3*4", "what is 2*3*4", "a*b*c", "x*y*z is a product", "5 * 6 * 7", 'say [SKIPPY_LEARN: {"a": 1}] kept', "[Right now: Thursday]", "Item [1] is done", "[a b c d e f] six words",
             "[list action=\"read\"]", "He said * and * again", "", "plain words, nothing to do.", "A * B", "use the * key"]


@pytest.mark.parametrize("said", UNTOUCHED)
def test_fixbr_a_product_a_note_a_number_in_brackets_and_plain_words_are_left_alone(said):
    assert brain_mod.strip_stage_directions(said) == said


def test_fixbr_property_a_sentences_negations_always_survive_whatever_is_emphasised_or_acted():
    rnd = random.Random(20261105)
    negations = ["not", "never", "no", "off", "don't", "cannot", "nothing", "isn't"]
    words = "turn open close the garage door lights kitchen bedroom on you can please do it now Captain".split()
    directions = ["*sighs*", "[laughs]", "*adjusts imaginary dial*", "[clicks fingers]", "*clears throat*", "*dramatic pause*"]
    for _ in range(2000):
        parts, wanted = [], []
        for _ in range(rnd.randint(3, 9)):
            kind = rnd.random()
            if kind < 0.25:
                n = rnd.choice(negations)
                parts.append(rnd.choice([n, f"*{n}*", f"**{n}**", f"*{n} {rnd.choice(words)}*", f"*{rnd.choice(words)} {n}*"]))
                wanted.append(n)
            elif kind < 0.40:
                parts.append(rnd.choice(directions))
            elif kind < 0.55:
                w = rnd.choice(words)
                parts.append(f"*{w}*")
                wanted.append(w)
            else:
                parts.append(rnd.choice(words))
        said = " ".join(parts) + rnd.choice([".", "!", "?", ""])
        spoken = brain_mod.strip_stage_directions(said)
        left = re.findall(r"[a-z']+", spoken.lower())
        for w in wanted:
            for token in re.findall(r"[a-z']+", w.lower()):
                assert token in left, (said, spoken)
        assert "*" not in spoken and "[" not in spoken, (said, spoken)
        assert "  " not in spoken and not spoken.startswith(" ") and not spoken.endswith(" "), (said, spoken)
    for product in ("2*3*4", "12*7*8 is 672", "a*b*c", "3 * 4"):
        assert brain_mod.strip_stage_directions(product) == product


def test_fixbr_the_brain_speaks_and_remembers_an_emphasised_negation():
    b, model, timers, clock = timer_brain([text("Do *not* open the garage door, monkey. *sighs*")])
    assert b.reply("should I open the garage door") == "Do not open the garage door, monkey." and b.history[-1]["content"] == "Do not open the garage door, monkey."


# ── P9: the timer route's false positives ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fixbr_a_noun_phrase_about_a_timer_is_not_an_order_to_set_one():
    r = brain_mod.timer_route
    for heard in ("the five minute timer", "my five minute timer", "that ten minute timer", "this five minute timer", "our 20 minute timer", "the 5 minute timer please", "the pasta ten minute timer"):
        assert r(heard) is None, heard
    for heard, args in (("a five minute timer", {"seconds": 300}), ("another five minute timer", {"seconds": 300}), ("five minute timer", {"seconds": 300}),
                        ("a new ten minute pasta timer", {"seconds": 600, "label": "pasta"}), ("set the five minute timer", {"seconds": 300}), ("set the timer for fifteen minutes", {"seconds": 900})):
        assert r(heard) == ("set_timer", args), heard
    b, model, timers, clock = timer_brain([text("Which timer, monkey?")])
    assert b.reply("the five minute timer") == "Which timer, monkey?" and timers.remaining() == [] and len(model.calls) == 1               # (an answer to "which timer?" set a NEW one)


def test_fixbr_a_label_with_an_article_or_a_pronoun_is_kept_as_he_said_it_never_dropped_in_silence():
    r = brain_mod.timer_route
    for heard, seconds, label in (("set a timer for 5 minutes to check the oven", 300, "check the oven"), ("set a timer for five minutes to check on it", 300, "check on it"),
                                  ("set a timer for ten minutes for the water to boil", 600, "water to boil"), ("set a timer for ten minutes to take it out", 600, "take it out"),
                                  ("set a timer for ten minutes for the eggs", 600, "eggs"), ("set a timer for ten minutes to steep", 600, "steep"), ("set a timer for ten minutes for my tea", 600, "tea"),
                                  ("set a ten minute timer to flip the steak", 600, "flip the steak")):
        assert r(heard) == ("set_timer", {"seconds": seconds, "label": label}), heard
    for heard in ("set a pasta timer for ten minutes for the water", "set a timer for 20 minutes for my pizza in the oven"):
        assert r(heard) is None, heard                                                              # two names, or more than a label: the model hears all of it, nothing is lost without a word
    for heard, seconds in (("set a timer for five minutes for it", 300), ("set a timer for five minutes for him", 300), ("set a timer for five minutes to it", 300), ("set a timer for ten minutes for the", 600)):
        assert r(heard) == ("set_timer", {"seconds": seconds}), heard                              # (a phrase of only "it" or "the" has nothing to lose)
    assert r("set a second timer for five minutes") == ("set_timer", {"seconds": 300}) and r("set another timer for five minutes") == ("set_timer", {"seconds": 300})        # (nothing lost: "second" says only that it is another)
    b, model, timers, clock = timer_brain()
    assert b.reply("set a timer for 5 minutes to check the oven") == "Timer set for five minutes." and timers.remaining() == [("check the oven", 300)] and model.calls == []


def test_fixbr_a_second_timer_is_not_one_second():
    r = brain_mod.timer_route
    assert r("set a second timer for five minutes") == ("set_timer", {"seconds": 300}) and r("set a second timer for ten minutes for the pasta") == ("set_timer", {"seconds": 600, "label": "pasta"})
    assert r("set a timer for a second") == ("set_timer", {"seconds": 1}) and r("set a 1 second timer") == ("set_timer", {"seconds": 1}) and r("set a timer for 1 second") == ("set_timer", {"seconds": 1})
    assert brain_mod.parse_duration("a second timer") is None and brain_mod.parse_duration("a second") == 1 and brain_mod.parse_duration("a second timer for five minutes") == 300


def test_fixbr_decimals_in_a_length_survive_the_plain_reading():
    r = brain_mod.timer_route
    assert r("set a timer for 1.5 hours") == ("set_timer", {"seconds": 5400}) and r("Set a timer for 2.5 minutes, please.") == ("set_timer", {"seconds": 150})
    assert brain_mod._plain("Turn off the lights, Skippy.") == "turn off the lights" and brain_mod._plain("hey skippy, turn off the lights please thanks skippy") == "turn off the lights"


# ── the house the lights are in ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def nine():
    return Brain(None, "m", None, hass=FakeHouse(NINE))._house_devices()


@pytest.fixture(scope="module")
def rich():
    return Brain(None, "m", None, hass=FakeHouse(RICH, RICH_AREAS))._house_devices()


def switched(heard, rows=None, areas=None, room=""):
    """(the route, the entities the real House switched, the services) for `heard` said in `room`: the whole way, Brain._light_pre_route -> home_control -> House.control."""
    h = FakeHouse(rows, areas)
    b = Brain(None, "m", None, hass=h)
    b.room = room
    got = b._light_pre_route(heard)
    if got is not None:
        house_mod.run_tool(h, got[0], got[1])
    return got, {c[2] for c in h.calls}, {c[1] for c in h.calls}


ALL_LIGHTS = {r["entity_id"] for r in NINE}


# ── P7: every and each are all ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard", ["turn off every light", "turn off each light", "turn off every lamp", "turn off each lamp", "switch off every light", "every light off", "each light off",
                                    "turn every light off", "turn each light off", "turn off every one of the lights", "please turn off each of the lights"])
def test_fixbr_every_and_each_mean_all_of_them_wherever_he_is(heard, nine):
    for here in ("living room", "bedroom", "kitchen", ""):
        assert brain_mod.light_route(heard, nine, here=here) == ("home_control", {"name": "all lights", "action": "off"}), (heard, here)
    for room in ("living", "bedroom", "office", ""):
        got, ids, services = switched(heard, room=room)
        assert ids == ALL_LIGHTS and services == {"turn_off"}, (heard, room)


def test_fixbr_every_light_in_a_named_place_is_that_places_lights_and_every_plug_or_fan_is_the_models(rich):
    r = brain_mod.light_route
    assert switched("turn off every light in the kitchen", room="living")[1] == {"light.kitchen_under_1", "light.kitchen_under_2", "light.kitchen_under_3", "light.track_stove_light"}
    assert switched("turn on each light upstairs", room="office")[1] >= {"light.bedroom", "light.living_track_middle"} and "light.deck_sliding_door" not in switched("turn on each light upstairs", room="office")[1]
    for heard in ("turn off every plug", "turn off each fan", "turn off every outlet", "turn on every fan", "turn off every switch"):
        assert r(heard, rich, here="living room") is None and r(heard, rich) is None, heard


# ── P9a: a curt question is a question ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard", ["bedroom light on?", "kitchen lights on?", "lights on?", "lights off?", "the bedroom lamp off?", "lamp on ?", "living room lights on?!", "kitchen lights off, please?",
                                    "bedroom lamp on??", "Bedroom light on?"])
def test_fixbr_a_curt_question_with_a_question_mark_is_left_to_the_model(heard, nine):
    assert brain_mod.light_route(heard, nine, here="living room") is None, heard
    assert switched(heard, room="living")[0] is None


def test_fixbr_a_command_said_with_a_rising_voice_is_still_a_command(nine):
    r = brain_mod.light_route
    assert r("turn on the bedroom lamp?", nine) == ("home_control", {"name": "bedroom lamp", "action": "on"})
    assert r("can you turn off the kitchen lights?", nine) == ("home_control", {"name": "kitchen lights", "action": "off"})
    assert r("could you please turn the lights off?", nine, here="bedroom") == ("home_control", {"name": "bedroom lights", "action": "off"})
    assert r("bedroom light on", nine) == ("home_control", {"name": "bedroom light", "action": "on"}) and r("kitchen lights on.", nine) == ("home_control", {"name": "kitchen lights", "action": "on"})


# ── P10: what can hurt, and what is a speaker ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
INFRASTRUCTURE = ["router", "modem", "wifi", "wi-fi", "server", "nas", "ups", "fridge", "freezer", "refrigerator", "sump", "pump", "camera", "medical", "oxygen"]


@pytest.mark.parametrize("word", INFRASTRUCTURE)
def test_fixbr_a_router_a_fridge_a_pump_and_the_like_are_never_switched_by_a_route(word, nine):
    assert brain_mod.RISKY_THING.search(f"turn off the {word}"), word
    devices = nine + [{"id": "switch.thing", "name": f"Basement {word.title()}", "room": "", "kind": "switch"}]
    r = brain_mod.light_route
    assert r(f"turn off the {word}", devices) is None and r(f"turn off the basement {word}", devices) is None
    assert r("turn off the basement", devices) is None                                              # (the name is unsafe even when his words leave the unsafe word out)
    assert r("turn off the bedroom lamp", devices) == ("home_control", {"name": "bedroom lamp", "action": "off"})


def test_fixbr_the_things_that_hurt_stay_the_models_in_a_rich_house(rich):
    for heard in ("turn off the router", "turn off the fridge", "turn off the sump pump", "turn off the garage fridge", "turn on the space heater", "turn on the kettle", "turn off the freezer",
                  "turn off the modem", "turn off the camera", "turn on the oven", "unlock the front door", "open the garage door"):
        for room in ("living", "office", ""):
            got, ids, _ = switched(heard, RICH, RICH_AREAS, room)
            assert got is None and not ids, (heard, room)
    assert switched("turn off the washer", RICH, RICH_AREAS)[1] == {"switch.washer"}                      # (an ordinary plug still is one)


def test_fixbr_plug_and_fan_groups_are_the_models_and_only_a_group_of_lights_is_a_route(rich):
    r = brain_mod.light_route
    for heard in ("turn off all the plugs", "turn on all the fans", "turn off the fans", "turn off all the outlets", "turn off the plugs in the office", "turn on all the switches", "turn off all the plugs upstairs"):
        for here in ("", "living room", "office", "bedroom"):
            assert r(heard, rich, here=here) is None, (heard, here)
    assert r("turn off all the lights", rich) == ("home_control", {"name": "all lights", "action": "off"})
    assert r("turn off the kitchen lights", rich) == ("home_control", {"name": "kitchen lights", "action": "off"})
    assert r("turn off the fan", rich, here="office") == ("home_control", {"name": "Office Fan", "action": "off"})           # one fan in the room is that fan, not a group
    assert r("fan on", rich, here="bedroom") == ("home_control", {"name": "Bedroom Fan", "action": "on"}) and r("turn off the fan", rich, here="theater") is None
    assert switched("turn off all the plugs", RICH, RICH_AREAS, "living")[0] is None


def test_fixbr_a_plug_called_sony_leaves_the_sony_to_the_speakers_while_the_house_plays():
    rows = NINE + [row("switch.sony", "Sony")]
    extra, ran = fake_tools("speakers", "music", "play_music")
    b, model, h, timers = house_brain([text("The Sony is off, monkey.")], rows=rows, extra=extra, room="living")
    b.audio = NS(playing=lambda: True, status=lambda: "Bilby is playing Gasoline on the Enclave.")
    assert b.reply("turn off the sony") == "The Sony is off, monkey."
    assert ran == [("speakers", {"what": "music", "device": "the sony", "on": False})] and h.calls == [] and b.timing["route"] == "speakers"
    b, model, h, timers = house_brain(rows=rows, extra=extra, room="living")                                  # nothing plays: the Sony is a TV, and its plug is the plug
    b.audio = NS(playing=lambda: False, status=lambda: "")
    assert b.reply("turn off the sony") in brain_mod.ACKS and h.calls == [("switch", "turn_off", "switch.sony")] and len(ran) == 1
    b, model, h, timers = house_brain(rows=rows, room="living")                                                # no speakers tool in this house at all: the plug
    assert b.reply("turn off the sony") in brain_mod.ACKS and h.calls == [("switch", "turn_off", "switch.sony")]
    b, model, h, timers = house_brain([text("The Enclave is off, monkey.")], rows=rows, extra=fake_tools("speakers", "music")[0], room="living")
    b.audio = NS(playing=lambda: True, status=lambda: "")
    assert b.reply("turn off the sony speakers") == "The Enclave is off, monkey." and h.calls == [] and b.timing["route"] == "speakers"          # (sound named: a speaker even when nothing plays)


# ── P1: how he really says it ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
NATURAL = [
    # (heard, the room he is in, the name sent to the house, the direction)
    ("turn off the lights in here", "living room", "living room lights", "off"), ("turn on the lights in this room", "bedroom", "bedroom lights", "on"),
    ("turn the lights off in here", "living room", "living room lights", "off"), ("turn off the light in here", "bedroom", "bedroom light", "off"),
    ("switch off the lights in this room please", "living room", "living room lights", "off"), ("lights out", "living room", "living room lights", "off"),
    ("lights out", "bedroom", "bedroom lights", "off"), ("Skippy, lights out.", "bedroom", "bedroom lights", "off"), ("turn the lights back on", "bedroom", "bedroom lights", "on"),
    ("turn the lights back off", "living room", "living room lights", "off"), ("turn back on the lights", "living room", "living room lights", "on"), ("I would like the lights off", "living room", "living room lights", "off"),
    ("I'd like the lights on", "bedroom", "bedroom lights", "on"), ("I want the lights on", "bedroom", "bedroom lights", "on"), ("I need the lights on", "living room", "living room lights", "on"),
    ("I want the living room lights off", "bedroom", "living room lights", "off"), ("I want to turn off the lights", "bedroom", "bedroom lights", "off"), ("I'd like you to turn the lights on", "bedroom", "bedroom lights", "on"),
    ("turn off the lights, Skippy", "living room", "living room lights", "off"), ("turn off the lights Skippy", "living room", "living room lights", "off"), ("lights off, Skippy", "bedroom", "bedroom lights", "off"),
    ("please turn off the lights", "bedroom", "bedroom lights", "off"), ("turn off the lights please", "bedroom", "bedroom lights", "off"), ("turn off the lights, thanks", "bedroom", "bedroom lights", "off"),
    ("turn the lights off, thank you", "bedroom", "bedroom lights", "off"), ("please turn off the lights, thanks", "bedroom", "bedroom lights", "off"), ("thanks Skippy, turn off the lights", "bedroom", "bedroom lights", "off"),
    ("would you mind turning off the lights", "bedroom", "bedroom lights", "off"), ("would you mind switching the kitchen lights on", "bedroom", "kitchen lights", "on"),
    ("do you mind turning the lights off", "bedroom", "bedroom lights", "off"), ("the bedroom light needs to be on", "living room", "bedroom light", "on"), ("the lights need to be off", "bedroom", "bedroom lights", "off"),
    ("make sure the lights are off", "bedroom", "bedroom lights", "off"), ("make sure the kitchen lights are on", "bedroom", "kitchen lights", "on"), ("lamp on in the bedroom please", "living room", "bedroom lamp", "on"),
    ("lights on in the kitchen", "bedroom", "kitchen lights", "on"), ("lights off in the living room", "bedroom", "living room lights", "off"), ("light up the kitchen for me", "bedroom", "kitchen lights", "on"),
    ("flip the lights on", "bedroom", "bedroom lights", "on"), ("let's turn on the lights", "bedroom", "bedroom lights", "on"), ("kitchen lights on please", "bedroom", "kitchen lights", "on"),
    ("turn off the lights right here", "living room", "living room lights", "off"), ("turn the lights on in the room", "bedroom", "bedroom lights", "on"), ("get the lights on", "bedroom", "bedroom lights", "on"),
    ("get the kitchen lights off", "bedroom", "kitchen lights", "off"),
]


@pytest.mark.parametrize("heard, here, name, action", NATURAL)
def test_fixbr_the_light_route_understands_how_he_really_says_it(heard, here, name, action, nine):
    assert brain_mod.light_route(heard, nine, here=here) == ("home_control", {"name": name, "action": action}), heard


def test_fixbr_light_up_is_a_places_lights_and_nothing_else(rich):
    r = brain_mod.light_route
    assert r("light up the kitchen", rich) == ("home_control", {"name": "kitchen lights", "action": "on"}) and r("light up the living room please", rich) == ("home_control", {"name": "living room lights", "action": "on"})
    for heard in ("light up the tv", "light up the coffee maker", "light up the room", "light up the grill", "light up the fireplace", "light up the bedroom lamp", "light up"):
        assert r(heard, rich, here="bedroom") is None, heard


def test_fixbr_in_here_without_a_known_room_is_the_models_and_a_named_room_beats_in_here(nine):
    r = brain_mod.light_route
    assert r("turn off the lights in here", nine) is None and r("lights out", nine) is None and r("lights out", nine, here="office") is None
    assert r("turn off the kitchen lights in here", nine, here="bedroom") == ("home_control", {"name": "kitchen lights", "action": "off"})


# the battery: about two hundred ways to say it, and ways not to, run through the real House (the whole way: route, tool, services) in five rooms
H = ("here",)
KITCHEN = {"light.kitchen_under_1", "light.kitchen_under_2", "light.kitchen_under_3", "light.track_stove_light"}
LIVING = {"light.living_track_middle", "light.living_track_throne"}
UPSTAIRS = KITCHEN | LIVING | {"light.bedroom"}
ROOM_LIGHTS = {"living": LIVING, "bedroom": {"light.bedroom"}, "office": set(), "theater": set(), "": set()}
HERE_FAN = {"living": {"fan.living_room_fan"}, "bedroom": {"fan.bedroom_fan"}, "office": {"fan.office_fan"}, "theater": set(), "": set()}
YES = [
    ("turn on the lights", "on", H), ("turn off the lights", "off", H), ("turn the lights on", "on", H), ("turn the lights off", "off", H), ("lights on", "on", H), ("lights off", "off", H),
    ("lights out", "off", H), ("turn off the lights in here", "off", H), ("turn on the lights in this room", "on", H), ("turn the lights off in here", "off", H), ("turn off the light in here", "off", H),
    ("switch off the lights in this room please", "off", H), ("turn the lights back on", "on", H), ("turn the lights back off", "off", H), ("I would like the lights off", "off", H),
    ("I want the lights on", "on", H), ("turn off the lights, Skippy", "off", H), ("Skippy, lights off", "off", H), ("please turn off the lights", "off", H), ("turn off the lights please", "off", H),
    ("turn off the lights, thanks", "off", H), ("turn the lights off, thank you", "off", H), ("can you turn off the lights", "off", H), ("could you please turn the lights off", "off", H),
    ("would you mind turning off the lights", "off", H), ("I'd like the lights on", "on", H), ("I need the lights on", "on", H), ("kill the lights", "off", H), ("cut the lights", "off", H),
    ("switch the lights off", "off", H), ("switch off the light", "off", H), ("put the lights on", "on", H), ("the lights need to be off", "off", H), ("turn off the lamp", "off", H),
    ("lamp off", "off", H), ("turn on the light", "on", H), ("here, lights on", "on", H), ("Turn off the lights.", "off", H), ("turn on the lights now", "on", H), ("hey skippy turn the lights off", "off", H),
    ("turn on the kitchen lights", "on", KITCHEN), ("turn off the kitchen lights", "off", KITCHEN), ("kitchen lights on", "on", KITCHEN), ("kitchen lights off please", "off", KITCHEN),
    ("turn the kitchen lights off", "off", KITCHEN), ("turn on the lights in the kitchen", "on", KITCHEN), ("lights on in the kitchen", "on", KITCHEN), ("lights off in the living room", "off", LIVING),
    ("turn on the living room lights", "on", LIVING), ("living room lights off", "off", LIVING), ("turn off the bedroom light", "off", {"light.bedroom"}), ("turn off the bedroom lamp", "off", {"light.bedroom"}),
    ("bedroom light on", "on", {"light.bedroom"}), ("switch the bedroom lamp on", "on", {"light.bedroom"}), ("lamp on in the bedroom please", "on", {"light.bedroom"}),
    ("would you mind switching the kitchen lights on", "on", KITCHEN), ("I want the living room lights off", "off", LIVING), ("the bedroom light needs to be on", "on", {"light.bedroom"}),
    ("turn on the light in the bedroom", "on", {"light.bedroom"}), ("turn the lights on upstairs", "on", UPSTAIRS), ("upstairs lights off", "off", UPSTAIRS), ("upstairs lights on", "on", UPSTAIRS),
    ("light up the kitchen for me", "on", KITCHEN), ("turn on the living track middle", "on", {"light.living_track_middle"}), ("turn off kitchen under one", "off", {"light.kitchen_under_1"}),
    ("turn on the deck light", "on", {"light.deck_sliding_door"}), ("turn the deck light off", "off", {"light.deck_sliding_door"}), ("turn on the bedroom", "on", {"light.bedroom"}),
    ("turn off all the lights", "off", ALL_LIGHTS), ("turn on all the lights", "on", ALL_LIGHTS), ("turn off every light", "off", ALL_LIGHTS), ("turn off each light", "off", ALL_LIGHTS),
    ("turn off every lamp", "off", ALL_LIGHTS), ("all lights off", "off", ALL_LIGHTS), ("turn off the lights everywhere", "off", ALL_LIGHTS), ("turn off all the lights in the house", "off", ALL_LIGHTS),
    ("turn off every light in the house", "off", ALL_LIGHTS), ("turn off the lights in the whole house", "off", ALL_LIGHTS),
]
YES_RICH = [
    ("turn on the coffee maker", "on", {"switch.coffee_maker"}), ("turn off the coffee maker", "off", {"switch.coffee_maker"}), ("turn the coffee maker on please", "on", {"switch.coffee_maker"}),
    ("turn on the bedroom fan", "on", {"fan.bedroom_fan"}), ("turn off the office fan", "off", {"fan.office_fan"}), ("turn on the desk lamp", "on", {"switch.desk_lamp"}),
    ("turn off the desk lamp", "off", {"switch.desk_lamp"}), ("turn on the living room tv", "on", {"switch.living_room_tv"}), ("switch off the washer", "off", {"switch.washer"}),
    ("turn off the fan", "off", ("here_fan",)), ("fan on", "on", ("here_fan",)), ("turn off the tv", "off", {"switch.living_room_tv"}), ("turn the tv off", "off", {"switch.living_room_tv"}),
]
MODEL_RICH = ["turn off the fans", "turn off all the plugs", "turn off every plug", "turn on all the fans", "turn off the plug in the office", "turn everything off", "turn off everything"]
NOT_COMMANDS = [
    "is the bedroom light on", "is the bedroom light on?", "are the lights on", "are the lights on?", "what lights are on", "which lights are on?", "bedroom light on?", "kitchen lights on?",
    "did I leave the lights on", "are the kitchen lights off", "is the fan on", "why are the lights on", "can you tell me if the lights are on", "do I have the lights on", "is anything on",
    "lights on?", "are the lights off in the kitchen?", "the lights are on", "were the lights off", "how many lights are on",
    "turn on the music", "turn off the music", "turn on shuffle", "turn off shuffle", "turn on dj mode", "turn off the radio", "turn on the stereo", "turn the music off", "turn off the sound", "turn it down",
    "turn on the heater", "turn on the kettle", "turn off the router", "turn off the fridge", "turn off the sump pump", "turn off the freezer", "turn off the modem", "unlock the front door",
    "open the garage door", "turn off the oven", "turn on the stove", "turn on the space heater", "turn off the security light", "turn off the camera", "turn off the wifi", "turn off the server",
    "turn off the ups", "turn off the refrigerator", "turn off the water pump", "turn off the oxygen", "turn off the medical device",
    "leave the light on", "keep the lights on", "don't turn off the lights", "never turn the lights off", "I left the lights on", "what's going on", "go on", "carry on", "tell me a joke",
    "turn it off", "turn them on", "turn on the thing", "turn that off", "turn it on", "I don't want the lights on", "I want to know if the lights are on", "I wonder if the lights are off",
    "dim the bedroom light", "turn the lights up", "turn the lights down", "brighten the lights", "set the lights to fifty percent", "dim the lights to forty percent", "turn up the lights",
    "turn off the lights in five minutes", "turn off the lights when I leave", "turn on the lights at seven", "turn off the lights after the movie", "turn the lights on tomorrow morning",
    "turn on the lights and play jazz", "turn off the lights and the fan", "turn off the bedroom lamp and the fan", "turn on the guest room lamp", "turn on the porch light",
    "set a timer for five minutes", "turn off the alarm", "stop the music", "play some jazz", "what time is it", "good night", "I'm going to bed", "it is dark in here", "make it darker",
    "turn off the lights except the bedroom", "turn off every light but the kitchen", "the lights are out", "I'm out", "lights, camera, action", "the lights went out",
]
ROOMS = ["living", "bedroom", "office", "theater", ""]


def battery(rows, areas, cases):
    """-> {outcome: [(phrase, room)]}: ok / missed / wrong (the wrong devices or direction) / risky / model (left to the model, as it should be) for the positives."""
    out = {k: [] for k in ("ok", "missed", "wrong", "risky", "model")}
    for phrase, action, spec in cases:
        for room in ROOMS:
            if spec == "model":
                want = None
            elif isinstance(spec, tuple):
                want = {"here": ROOM_LIGHTS, "here_fan": HERE_FAN}[spec[0]].get(room) or None
            else:
                want = set(spec)
            got, ids, services = switched(phrase, rows, areas, room)
            if got is None:
                out["model" if want is None else "missed"].append((phrase, room))
            elif ids & RISKY_IDS:
                out["risky"].append((phrase, room))
            elif want is not None and ids == want and services == {"turn_on" if action == "on" else "turn_off"}:
                out["ok"].append((phrase, room))
            else:
                out["wrong"].append((phrase, room, sorted(ids)))
    return out


def test_fixbr_the_battery_routes_the_unambiguous_ways_to_say_it_and_never_the_wrong_thing():
    assert len(YES) + len(YES_RICH) >= 70 and len(NOT_COMMANDS) >= 100
    for label, rows, areas, cases in (("the real nine lights", NINE, None, YES), ("a rich house", RICH, RICH_AREAS, YES + YES_RICH + [(p, "off", "model") for p in MODEL_RICH])):
        done = battery(rows, areas, cases)
        sure = len(done["ok"]) + len(done["missed"])
        print(f"[fix:brain-routes] {label}: routed {len(done['ok'])} of {sure} unambiguous = {100 * len(done['ok']) / sure:.1f} %, wrong {len(done['wrong'])}, risky {len(done['risky'])}")
        assert not done["wrong"] and not done["risky"], (label, done["wrong"][:5], done["risky"][:5])
        assert len(done["ok"]) >= 0.85 * sure, (label, len(done["ok"]), sure, sorted({p for p, _ in done["missed"]}))
        acted = [(p, room) for p in NOT_COMMANDS for room in ROOMS if switched(p, rows, areas, room)[0] is not None]
        assert not acted, (label, acted[:5])


# ── P12b: a light called "living track middle" is not music ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fixbr_a_reply_naming_a_light_with_a_music_word_in_its_name_is_not_a_music_claim_after_home_control():
    said = "The living room is now darkened, Captain. I turned off the living track middle and the living track throne."
    assert musicguard.classify("I want the living room lights off", "", said, ["home_control"]) == (None, None)
    assert musicguard.classify("darken the living room", "", "I turned off the track stove light.", ["home_control"]) == (None, None)
    assert musicguard.classify("darken the living room", "", "I switched off the music room lamp and the stereo light.", ["home_control"]) == (None, None)
    assert musicguard.classify("darken the living room", "", said, []) == ("retract", "claim")                  # (no tool ran: the same words are what they were)


def test_fixbr_a_true_light_reply_with_a_false_music_claim_in_it_is_still_caught():
    lie = "I turned off the living track middle and I started some jazz upstairs."
    assert musicguard.classify("turn off the living room lights", "", lie, ["home_control"]) == ("retract", "claim")
    assert musicguard.classify("turn off the lights and play some jazz upstairs", "", lie, ["home_control"]) == ("reask", "claim")
    assert musicguard.classify("turn off the lights", "", "Lights off. I paused the music and lowered the volume.", ["home_control"]) == ("retract", "claim")
    assert musicguard.classify("turn off the lights", "", "I turned off the living track middle. Playing jazz on the Enclave now.", ["home_control"]) == ("retract", "claim")
    for lie in ("Playing jazz upstairs and lights off now.", "I put the lights out and moved the music upstairs, lamps off too.", "Lights off, and I started the music in the living room lamp style.",
                "Lights off. Playing jazz upstairs, living room lamp on.", "I turned the living track middle off and moved the music upstairs.", "Lights off, music started upstairs.",
                "I turned off the stereo light and paused the music.", "Lights off everywhere and the music started upstairs."):
        assert musicguard.classify("turn off the lights", "", lie, ["home_control"]) == ("retract", "claim"), lie                  # (a place or a music word beside a light word is one name only when they are next to each other)


def test_fixbr_a_true_reply_that_names_the_lights_by_place_is_no_music_claim_after_home_control():
    """The Captain's own words are "upstairs lights": 'I turned off the upstairs lights' was cut to 'I have not played, moved or changed any music.' (the word upstairs is a speaker's place to the guard)."""
    for heard, said in (("turn off the upstairs lights", "I turned off the upstairs lights, Captain."), ("turn off the lights everywhere", "I switched off the lights everywhere."),
                        ("turn on the living room lamp", "I turned on the living room lamp."), ("turn on the lights downstairs", "I turned the downstairs lights on."),
                        ("turn off the theater lights", "I turned off the theater lights."), ("turn off the track stove light", "I turned off the track stove light."),
                        ("lights off in the living room", "I turned off the lights in the living room."), ("turn off the lights upstairs", "I switched the lights upstairs off.")):
        assert musicguard.classify(heard, "", said, ["home_control"]) == (None, None), said
        assert musicguard.classify(heard, "", said, []) != (None, None), said              # (with no tool at all the same words still are what they were)


def test_fixbr_the_model_that_switched_a_light_called_track_is_not_told_it_played_music():
    b, model, h, timers = house_brain([tool("home_control", name="living room lights", action="off")], [text("The living room is dark, Captain. I turned off the living track middle and the living track throne.")], room="living")
    said = b.reply("darken the living room please")
    assert said == "The living room is dark, Captain. I turned off the living track middle and the living track throne." and b.guard == (None, None)
    assert {c[2] for c in h.calls} == LIVING


# ── P13: the behaviours the mutation run found unpinned ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fixbr_a_routed_light_is_confirmed_only_by_an_exact_done_and_every_other_answer_reaches_the_model():
    b, model, h, timers = house_brain()
    args = {"name": "bedroom lamp", "action": "on"}
    for good in ("Done: Bedroom, on.", "Done: kitchen lights, off: kitchen under 1, kitchen under 2.", "Done: all lights, on: Bedroom, kitchen under 1."):
        assert b._confirmation("home_control", args, good) in brain_mod.ACKS, good
    for bad in ("That could be Desk Lamp, or Desk Light. Ask him which one; do not guess.", "I cannot on Bedroom.", "I cannot find anything in the house called lamp.",
                "Nothing I can switch is in the garage. Do not claim you did anything.", "Done: kitchen lights, on: kitchen under 1. These did not answer: kitchen under 2.",
                "These did not answer: kitchen under 2.", "done: Bedroom, on.", "Done", "", "Bedroom needs him to confirm out loud first. Ask him.", "The house did not answer."):
        assert b._confirmation("home_control", args, bad) is None, bad
    for action in ("toggle", "set_brightness", "open", "", None):
        assert b._confirmation("home_control", {"name": "bedroom lamp", "action": action}, "Done: Bedroom, on.") is None, action


def test_fixbr_a_house_that_answers_a_routed_command_with_a_failure_gets_the_model_to_word_it():
    for failure in ("That could be Bedroom, or Bedroom Fan. Ask him which one; do not guess.", "I cannot on Bedroom.", "The house did not answer."):
        b, model, h, timers = house_brain([text("That did not work, monkey.")], room="bedroom")
        h.control = lambda *a, **k: failure
        assert b.reply("turn on the bedroom lamp") == "That did not work, monkey." and len(model.calls) == 1 and failure in str(model.calls[0]["messages"][-1]["content"]), failure
        assert b.timing["route"] == "home_control" and "template" not in b.timing and b.actions[0]["outcome"] == failure[:120]


def test_fixbr_a_sentence_about_music_a_timer_an_alarm_or_dimming_is_never_a_light_even_when_a_lamp_is_named_so():
    r = brain_mod.light_route
    names = {"music": "Music Room Lamp", "audio": "Audio Lamp", "sound": "Sound Light", "stereo": "Stereo Light", "speaker": "Speaker Lamp", "radio": "Radio Lamp", "spotify": "Spotify Lamp",
             "bilby": "Bilby Lamp", "dj": "DJ Lamp", "playlist": "Playlist Lamp", "audiobook": "Audiobook Lamp", "book": "Book Lamp", "podcast": "Podcast Lamp", "volume": "Volume Lamp",
             "thermostat": "Thermostat Light", "heat": "Heat Lamp", "ac": "AC Lamp", "air conditioning": "Air Conditioning Lamp", "timer": "Timer Lamp", "alarm": "Alarm Light",
             "reminder": "Reminder Lamp", "dim": "Dim Lamp", "dimmer": "Dimmer Lamp", "brighter": "Brighter Lamp", "brightness": "Brightness Lamp", "percent": "Percent Lamp"}
    for word, name in names.items():
        devices = [{"id": "light.x", "name": name, "room": "", "kind": "light"}, {"id": "light.bedroom", "name": "Bedroom", "room": "bedroom", "kind": "light"}]
        assert r(f"turn off the {word}", devices) is None and r(f"turn on the {word}", devices) is None, word                  # the word alone would name that lamp
        assert r(f"turn off the {name.lower()}", devices) is None, name                                                     # ... and so would its whole name
    plain = [{"id": "light.x", "name": "Reading Lamp", "room": "", "kind": "light"}]
    assert r("turn off the reading lamp", plain) == ("home_control", {"name": "Reading Lamp", "action": "off"})            # (the same sentence without the word)


def test_fixbr_a_route_the_house_would_call_ambiguous_is_dropped_and_the_model_decides():
    twins = [row("light.desk_a", "Desk Lamp"), row("light.desk_b", "Desk Light")]          # the route reads two different names; the house reads both as "desk" and cannot tell which
    b, model, h, timers = house_brain([text("Which desk, monkey?")], rows=twins, room="living")
    assert b._light_resolves("Desk Lamp") is False and b._light_resolves("bedroom lamp") is False and Brain(None, "m", None)._light_resolves("bedroom lamp") is False
    assert b.reply("turn on the desk lamp") == "Which desk, monkey?" and h.calls == [] and b.actions == [] and b.timing["route"] is None and len(model.calls) == 1
    ok, model, h2, timers = house_brain(room="bedroom")
    assert ok._light_resolves("bedroom lamp") is True and ok._light_resolves("kitchen lights") is True and ok._light_resolves("all lights") is True and ok._light_resolves("flux capacitor") is False
    assert ok._light_resolves("Front Door") is False and ok._light_resolves("") is False
