"""fix3:guards: the guards of the house brain, after the third pass (2026-11-05, evening): what the second review left.

  code#1   the second try after a CLAIM with no tool ("Alarm cancelled." to "I do not need the alarm on sunday") was ungated: it ran cancel_alarm with nothing named, list clear, announce. Now behind the
           gate of the other two second tries (retry_may; the model's own `confirm` dropped); a genuine "cancel my alarm" that was claimed and not done is told plainly (UNCLAIM)
  core#2   the spoken yes for a lock, a door or the garage was the MODEL's to write: Brain.run_tool drops its `confirm` unless the exchange before asked him and he now says a plain yes (says_yes)
  core#4   the second tries were gated by RISKY, not RISKY_THING: the router, the fridge, the pump, the CPAP ... were switched off by a second guess (8 of 8)
  core#5   order_shaped took statements for orders ("dim lighting makes me sleepy", "turn signals are on the left") and missed "bring the volume down"
  core#8   the backstop missed a progressive claim ("Setting a timer for ten minutes."), a 15-word one, and musicguard cut a lights claim to "I have not played any music"
  code#8   the denial's second try was shown a CUT tool menu (a different prompt prefix: read from scratch, 7.8 to 44 s): now the whole list, the gate at execution
  code#2   the survivors of the second review's mutation run: B8 (NOT_AN_ORDER inside the backstop), O7 (a trailing "please"), O8 ("please" in a negated utterance)
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
import musicguard
from brain import Brain
from timers import Timers

NOW = "Thursday, November 05, 2026, 22:40"


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
    """Two lights, a lock, a garage cover and the things a house has to keep running; every service call is recorded."""
    sensitive = True                                                     # (the lock and the garage are there to be found: they answer "needs him to confirm out loud first" without a `confirm`)

    def __init__(self):
        self.rows = [row("light.bedroom", "Bedroom"), row("light.kitchen_under_1", "kitchen under 1"), row("lock.front_door", "Front Door", "locked"),
                     row("cover.garage_door", "Garage Door", "closed", device_class="garage"), row("switch.router", "Router", "on"), row("switch.fridge", "Fridge", "on"),
                     row("switch.modem", "Modem", "on"), row("switch.sump_pump", "Sump Pump", "on"), row("switch.nas", "NAS", "on"), row("switch.oxygen_concentrator", "Oxygen Concentrator", "on"),
                     row("switch.cpap", "CPAP", "on"), row("switch.freezer", "Freezer", "on"), row("switch.oven", "Oven", "on")]
        self.calls = []

    def states(self, fresh=False):
        return self.rows

    def areas(self):
        return {}

    def _service(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))


def guard_brain(tmp_path, *rounds, hass=True):
    """A real Brain over REAL alarms (the weekday wake-up, a dentist), REAL lists (shopping: milk, eggs, bread), a real pasta timer, the hub's REAL music and announce tool definitions (their calls recorded
    in `ran`) and a fake house: what a stray cancel, clear or announcement, or a door that opens, would really do."""
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
    b.world = NS(alarms=al, lists=ls, timers=timers, ran=ran, house=house, clock=clock)
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
    monkeypatch.setattr(house_mod, "thermostat_route", lambda *a, **k: None)


def named_in_nudge(messages) -> set:
    """The tools the denial's nudge names ("... you DO have these: a, b, c. Read his words again ...")."""
    nudge = next(m["content"] for m in messages if isinstance(m["content"], str) and "you DO have these: " in m["content"])
    return set(nudge.split("you DO have these: ")[1].split(". Read his words")[0].split(", "))


# ── code#1: the second try after a claim with no tool is behind the gate ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard, claim, forbidden", [
    ("I do not need the alarm on sunday", "Alarm cancelled.", call("cancel_alarm")),                                          # (the review's own: both alarms were gone)
    ("my shopping list is long", "Removed everything from the list.", call("list", action="clear", list="shopping")),
    ("dinner is ready", "I announced it.", call("announce", text="Dinner is ready.", place="everywhere")),
    ("the shopping list is long", "Removed the milk from the list.", call("list", action="remove", list="shopping", items=["milk"])),
])
def test_fix3_a_claim_made_to_a_statement_gets_a_second_try_that_cancels_clears_and_announces_nothing(heard, claim, forbidden, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(claim)], [forbidden], [text("Done.")])
    assert b.reply(heard) == brain_mod.UNCLAIM and b.guard == (None, None) and len(model.calls) == 3
    assert untouched(b), heard
    assert model.calls[2]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE                  # the model was told it was not done


def test_fix3_a_genuine_cancel_that_was_claimed_and_not_done_is_told_plainly_and_cancels_nothing(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Alarm cancelled.")], [call("cancel_alarm", label="dentist")], [text("Gone.")])
    assert b.reply("cancel my dentist alarm") == brain_mod.UNCLAIM                                              # (as on the backstop path: the alarm he wakes to is never cancelled by a second guess)
    assert untouched(b) and b.history[-1]["content"] == brain_mod.UNCLAIM


def test_fix3_a_claim_to_an_order_still_gets_its_second_try_and_it_can_do_it(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Alarm set for six.")], [call("set_alarm", time="06:00")], [text("Six it is.")])
    assert b.reply("set an alarm for six in the morning") == "Six it is." and [a["tool"] for a in b.actions] == ["set_alarm"] and len(b.world.alarms.items) == 3
    b, model = guard_brain(tmp_path / "list", [text("Added milk to the list.")], [call("list", action="add", list="shopping", items=["butter"])], [text("Butter, too.")])
    assert b.reply("put butter on the shopping list") == "Butter, too." and b.world.lists.lists["shopping"][-1] == "butter"


def test_fix3_the_second_try_after_a_claim_to_a_question_may_only_look(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Alarm set for seven.")], [call("set_alarm", time="07:00")], [call("list_alarms")], [text("Two alarms, monkey.")])
    assert b.reply("what alarms do I have") == "Two alarms, monkey." and [a["tool"] for a in b.actions] == ["list_alarms"] and len(b.world.alarms.items) == 2      # a write is refused, a look runs
    assert model.calls[2]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE


def test_fix3_a_claim_is_retried_for_an_order_only_a_statement_or_a_question_that_holds_one_is_not(tmp_path, no_routes):
    """ordered = order_shaped(heard) and not NOT_AN_ORDER.search(heard): a harmless write (a timer, an item on a list) is refused when his words are a statement, or a question with an order inside (mutants M03, M04)."""
    for i, (heard, claim, write) in enumerate((("I do not need the alarm on sunday", "Alarm set for sunday.", call("set_timer", seconds=5, label="x")),
                                                ("my shopping list is long", "Added eggs to the list.", call("list", action="add", list="shopping", items=["jam"])),
                                                ("what time is it, and wake me at seven", "Alarm set for seven.", call("set_alarm", time="07:00")),
                                                ("what time is it, and add jam to the list", "Added jam to the list.", call("list", action="add", list="shopping", items=["jam"])))):
        b, model = guard_brain(tmp_path / f"s{i}", [text(claim)], [write], [text("Done.")])
        assert b.reply(heard) == brain_mod.UNCLAIM and untouched(b), heard
        assert model.calls[2]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE
    b, model = guard_brain(tmp_path / "order", [text("Alarm set for seven.")], [call("set_alarm", time="07:00")], [text("Seven it is.")])
    assert b.reply("wake me at seven") == "Seven it is." and len(b.world.alarms.items) == 3                      # (the same write for an order runs)


def test_fix3_the_second_try_after_a_claim_never_runs_the_models_own_confirm_and_a_call_written_as_text_passes_the_same_gate(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Alarm set for six.")], [call("home_control", name="garage door", action="open", confirm=True)], [text("Opened.")])
    b.reply("set an alarm for six in the morning")
    assert b.world.house.calls == [] and all("confirm" not in a["args"] for a in b.actions)                    # his yes is his: the garage got none and asked for it
    b, model = guard_brain(tmp_path / "text", [text("Alarm cancelled.")], [text("[cancel_alarm]")])
    assert b.reply("I do not need the alarm on sunday") == brain_mod.UNCLAIM and untouched(b)


# ── core#2: only HE says yes to a lock, a door or the garage ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def asked_the_house_way(*, name="garage door", action="open"):
    """The first turn of the real flow: the model calls home_control (no yes yet), the house answers that it needs him to confirm out loud, the model asks him."""
    return [[call("home_control", name=name, action=action)], [text(f"The {name} needs your word first, Captain. Shall I {action} it?")]]


@pytest.mark.parametrize("heard, name, action", [("open the garage door", "garage door", "open"), ("close the garage door", "garage door", "close"), ("lock the front door", "front door", "lock"),
                                                 ("can you open the garage", "garage door", "open"), ("let my brother in, unlock the door", "front door", "unlock")])
def test_fix3_a_sensitive_order_with_a_confirm_the_model_wrote_by_itself_makes_no_service_call(heard, name, action, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [call("home_control", name=name, action=action, confirm=True)], [text("It needs your word out loud first, Captain. Shall I?")])
    assert b.reply(heard) == "It needs your word out loud first, Captain. Shall I?"
    assert b.world.house.calls == [] and "confirm" not in b.actions[0]["args"]
    assert brain_mod.CONFIRM_ASKED in model.calls[1]["messages"][-1]["content"][0]["content"]                    # (the model was told what the house asks: it can ask him)


def test_fix3_the_house_really_says_what_the_brain_listens_for(tmp_path):
    out = house_mod.run_tool(FakeHouse(), "home_control", {"name": "garage door", "action": "open"})
    assert brain_mod.CONFIRM_ASKED in out and out.endswith("once he has said yes.")


@pytest.mark.parametrize("name, action, yes", [("garage door", "open", "yes"), ("garage door", "close", "Yeah, go ahead."), ("front door", "unlock", "Skippy, yes please"), ("front door", "lock", "do it"),
                                               ("garage door", "open", "yes, open the garage door"), ("garage door", "open", "open it"), ("front door", "unlock", "okay"), ("garage door", "open", "sure, thanks")])
def test_fix3_the_two_turn_flow_opens_it_once_his_yes_is_said(name, action, yes, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, *asked_the_house_way(name=name, action=action), [call("home_control", name=name, action=action, confirm=True)], [text("Done, Captain.")])
    b.reply(f"{action} the {name}")
    assert b.world.house.calls == []                                                                            # (the order alone changes nothing: the house asked)
    b.world.clock[0] += 5
    assert b.reply(yes) == "Done, Captain." and len(b.world.house.calls) == 1
    assert b.actions[0]["args"] == {"name": name, "action": action, "confirm": True}


def test_fix3_one_yes_opens_one_door_once(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [call("home_control", name="garage door", action="open", confirm=True)], [text("Open.")],
                           [call("home_control", name="garage door", action="close", confirm=True)], [text("Closed.")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    b.reply("yes")
    assert [c[1] for c in b.world.house.calls] == ["open_cover"]
    b.world.clock[0] += 5
    b.reply("close the garage door")                                                                            # (a new order: the model's confirm is dropped again, the house asks)
    assert [c[1] for c in b.world.house.calls] == ["open_cover"]


@pytest.mark.parametrize("what", ["nothing pending", "quiet gap", "past CONFIRM_FOR_S", "another exchange between", "an offer between"])
def test_fix3_a_yes_with_nothing_pending_or_stale_confirms_nothing(what, tmp_path, no_routes):
    if what == "nothing pending":
        b, model = guard_brain(tmp_path, [call("home_control", name="garage door", action="open", confirm=True)], [text("Opening.")])
    else:
        extra = {"another exchange between": [[text("It is twenty to eleven.")]]}.get(what, [])
        b, model = guard_brain(tmp_path, *asked_the_house_way(), *extra, [call("home_control", name="garage door", action="open", confirm=True)], [text("Opening.")])
        b.reply("open the garage door")
        if what == "another exchange between":
            b.world.clock[0] += 5
            b.reply("what time is it")
        if what == "an offer between":
            b.offer("Shall I tell you the news?", "news")
        b.world.clock[0] += {"quiet gap": brain_mod.FORGET_AFTER_S + 5, "past CONFIRM_FOR_S": brain_mod.CONFIRM_FOR_S + 5}.get(what, 5)
    b.reply("yes")
    assert b.world.house.calls == [], what                                                                      # (the model said "Opening." and the house did not)


@pytest.mark.parametrize("said", ["no", "yes but not now", "yes, and turn off the lights", "yes open the front door", "please", "right", "yes, don't", "wait", "okay turn on the kitchen lights", "the garage door",
                                  "yes please open the garage door and the front door and play some jazz", "open the front door"])
def test_fix3_only_a_plain_yes_confirms(said, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [call("home_control", name="garage door", action="open", confirm=True)], [text("Opening.")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    b.reply(said)
    assert b.world.house.calls == [], said


@pytest.mark.parametrize("name, action", [("front door", "unlock"), ("garage door", "close"), ("garage", "close")])
def test_fix3_his_yes_confirms_the_call_he_was_asked_about_and_no_other(name, action, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [call("home_control", name=name, action=action, confirm=True)], [text("Done.")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    b.reply("yes")
    assert b.world.house.calls == []


def test_fix3_a_name_that_points_at_the_same_thing_still_counts(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [call("home_control", name="Garage Door", action="Open", confirm=True)], [text("Open.")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    b.reply("yes")
    assert len(b.world.house.calls) == 1


def test_fix3_his_yes_confirms_the_thing_he_was_asked_about_not_another_with_the_same_action(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [call("home_control", name="front door", action="open", confirm=True)], [text("Done.")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    b.reply("yes")
    assert "confirm" not in b.actions[0]["args"] and b.world.house.calls == []                                   # (mutants M13, M23: the thing is checked as well as the action)


def test_fix3_one_yes_is_spent_by_the_first_call_even_when_the_model_calls_twice(tmp_path, no_routes):
    twice = [call("home_control", cid="a", name="garage door", action="open", confirm=True), call("home_control", cid="b", name="garage door", action="open", confirm=True)]
    b, model = guard_brain(tmp_path, *asked_the_house_way(), twice, [text("Open.")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    b.reply("yes")
    assert len(b.world.house.calls) == 1 and [a["args"].get("confirm") for a in b.actions] == [True, None]       # (mutant M15)


def test_fix3_a_yes_after_a_done_confirms_nothing(tmp_path, no_routes):
    """The exchange before this one must have been the house ASKING (CONFIRM_ASKED): after a door that was opened with his yes, a later "yes" to something else opens nothing (mutant M17)."""
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [call("home_control", name="garage door", action="open", confirm=True)], [text("Open.")],
                           [call("home_control", name="garage door", action="open", confirm=True)], [text("Open again.")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    b.reply("yes")
    assert len(b.world.house.calls) == 1
    b.world.clock[0] += 5
    b.reply("yes")                                                                                              # (he said yes to something else: the last answer was "Done: Garage Door, open.")
    assert len(b.world.house.calls) == 1


def test_fix3_the_pending_call_is_read_from_a_home_control_exchange_that_asked_and_from_nothing_else(tmp_path):
    b, _ = guard_brain(tmp_path)
    asked = f"Garage Door {brain_mod.CONFIRM_ASKED}. Ask him, and only call again with confirm set once he has said yes."

    def exchange(tool, result):
        return [{"role": "user", "content": "open the garage door"}, {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": {"name": "garage door", "action": "open"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": result}]}, {"role": "assistant", "content": "Shall I?"}]
    now = b.clock()
    b._last = now - 5
    b._turns = [exchange("home_control", asked)]
    assert b._confirm_given("yes", now) == {"name": "garage door", "action": "open"}
    b._turns = [exchange("list", asked)]                                                                        # (a tool that is not the house's: mutant M18)
    assert b._confirm_given("yes", now) is None
    b._turns = [exchange("home_control", "Done: Garage Door, open.")]
    assert b._confirm_given("yes", now) is None
    b._turns = [[{"role": "user", "content": "open the garage door"}, {"role": "assistant", "content": "Shall I?"}]]                    # (it never called the house: nothing was asked by it)
    assert b._confirm_given("yes", now) is None
    b._turns = []
    assert b._confirm_given("yes", now) is None
    b._turns = [exchange("home_control", asked)]
    b._last = now - brain_mod.CONFIRM_FOR_S - 1
    assert b._confirm_given("yes", now) is None


@pytest.mark.parametrize("a, b, same", [("garage door", "Garage Door", True), ("garage door", "garage", True), ("the garage door", "garage door", True), ("garage door", "front door", False),
                                        ("front door", "door", True), ("", "garage door", False), (None, "garage door", False), ("garage door", None, False), ("kitchen light", "kitchen fan", False)])
def test_fix3_same_thing(a, b, same):
    assert brain_mod._same_thing(a, b) is same, (a, b)


def test_fix3_a_lamp_is_unaffected_and_a_confirm_written_as_text_goes_through_the_same_place(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [call("home_control", name="bedroom", action="on", confirm=True)], [text("There.")])
    assert b.reply("I want the bedroom light on") == "There." and b.world.house.calls == [("light", "turn_on", {"entity_id": "light.bedroom"})] and "confirm" not in b.actions[0]["args"]
    b, model = guard_brain(tmp_path / "text", [text('[home_control name="garage door" action=open confirm=true]')], [text("Opening.")])
    b.reply("open the garage door")
    assert b.world.house.calls == [] and all("confirm" not in a["args"] for a in b.actions)


@pytest.mark.parametrize("said, yes", [
    ("yes", True), ("Yes.", True), ("yeah", True), ("yep", True), ("yup", True), ("sure", True), ("okay", True), ("ok", True), ("go ahead", True), ("do it", True), ("please do", True), ("confirm", True),
    ("confirmed", True), ("yes please", True), ("yes sir", True), ("Skippy, yes", True), ("hey Skippy yes", True), ("sure, thanks", True), ("I said yes", True), ("absolutely", True),
    ("that's right", True), ("go for it", True), ("yes, go ahead and do it", True), ("of course", True), ("sure thing", True), ("affirmative", True),
    ("no", False), ("", False), ("please", False), ("thanks", False), ("right", False), ("it", False), ("yes but", False), ("yes, turn off the lights", False), ("no thanks", False), ("not yet", False),
    ("yes and then play some music in the kitchen please", False), ("wait", False), ("stop", False), ("yes no", False), ("what", False), ("do not", False), ("cancel", False),
    ("yes yes yes yes yes yes yes yes yes yes", True), ("yes yes yes yes yes yes yes yes yes yes yes", False),                  # (ten words at most)
])
def test_fix3_says_yes(said, yes):
    assert brain_mod.says_yes(said) is yes, said


def test_fix3_says_yes_knows_the_thing_it_was_asked_about():
    asked = {"name": "garage door", "action": "open"}
    for said in ("open it", "yes, open the garage door", "yes open the door", "go ahead and open it", "open that"):
        assert brain_mod.says_yes(said, asked), said
    for said in ("open the front door", "close it", "yes close it", "the garage door", "garage", "open", "open the garage door"):          # (the order said again is an order again, not a yes)
        assert not brain_mod.says_yes(said, asked), said


# ── core#4: the router, the fridge, the pump ... are not switched off by a second guess ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
THINGS = [("the fridge", "Fridge"), ("the router", "Router"), ("the modem", "Modem"), ("the sump pump", "Sump Pump"), ("the nas", "NAS"), ("the oxygen concentrator", "Oxygen Concentrator"),
          ("the cpap", "CPAP"), ("the freezer", "Freezer")]


@pytest.mark.parametrize("thing, name", THINGS)
def test_fix3_a_bare_done_to_an_order_for_a_thing_that_must_keep_running_is_not_second_guessed(thing, name, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Done.")], [call("home_control", name=name, action="off")], [text("Off.")])
    assert b.reply(f"turn off {thing}") == "Done." and len(model.calls) == 1 and b.world.house.calls == [] and b.guard != ("reask", "ack")


@pytest.mark.parametrize("thing, name", THINGS)
def test_fix3_a_denial_of_an_order_for_a_thing_that_must_keep_running_stands(thing, name, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("I cannot do that from here, Captain.")], [call("home_control", name=name, action="off")], [text("Off.")])
    assert b.reply(f"turn off {thing}") == "I cannot do that from here, Captain." and len(model.calls) == 1 and b.world.house.calls == []


@pytest.mark.parametrize("thing, name", THINGS + [("the oven", "Oven"), ("all the plugs", "all plugs"), ("everything downstairs", "everything downstairs")])
def test_fix3_no_second_try_calls_those_whatever_his_words_were(thing, name, tmp_path, no_routes):
    """The gate at execution: the order was for the kitchen lights, the model's second pick is the fridge."""
    b, model = guard_brain(tmp_path, [text("Done.")], [call("home_control", name=name, action="off")], [text("Off.")])
    assert b.reply("turn off the kitchen lights") == brain_mod.UNCLAIM and b.world.house.calls == [] and b.actions == []
    assert model.calls[2]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE
    b, model = guard_brain(tmp_path / "claim", [text("Alarm set for six.")], [call("home_control", name=name, action="off")], [text("Off.")])
    b.reply("set an alarm for six in the morning")
    assert b.world.house.calls == [], thing


def test_fix3_the_gate_refuses_only_what_has_to_keep_running_and_leaves_the_lights_and_the_doors_to_the_house():
    may = brain_mod.retry_may
    for name in ("Fridge", "router", "the sump pump", "NAS", "CPAP", "oxygen concentrator", "oven", "space heater", "all the plugs", "every outlet", "everything", "all switches"):
        assert not may("home_control", {"name": name, "action": "off"}, True), name
    for name in ("kitchen lights", "bedroom", "bedroom lamp", "all the lights", "office fan", "coffee maker"):
        assert may("home_control", {"name": name, "action": "off"}, True), name
    for name in ("front door", "garage door", "back door lock"):
        assert may("home_control", {"name": name, "action": "unlock"}, True), name                              # (it runs with no `confirm`: the house asks him)
    assert not may("home_control", {"name": "kitchen lights", "action": "off"}, False)                           # (a question may only look)
    assert may("home_state", {"name": "fridge"}, False) and may("home_devices", {}, False)


# ── core#5 and code#6: what an order looks like ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
STATEMENTS = [                                                                                                  # (written BEFORE the rules, 60 of them: each names a light, a timer, the music ... and orders nothing)
    "play time is over for today", "play dates are on friday", "play lists are my thing", "pause buttons are tiny on that remote", "pause menus in games are boring", "skip ads are the best thing about it",
    "mute buttons are on the side", "turn signals are on the left of the lamp", "turn lanes have their own lights", "cut flowers need the fan off", "dim lights are cozy", "kill switches are on the fan for safety",
    "power lines are down so the lights are out", "set top boxes make the lamp flicker", "start times are on the timer app", "wake up calls are my alarm", "add ons for the timer app are free",
    "read lists are overrated", "clear lists are better than long ones", "light switches are by the door and the lamp is on the left",
    "we want the kitchen lights on a dimmer eventually", "we need to talk about the thermostat", "I would like to thank you for the music", "I want to apologize for the timer yesterday",
    "we want to buy a new stereo someday", "how about that music last night", "lamps on both sides of the bed is how we do it", "fans on high make the whole room cold", "volume up is the button on the right",
    "speakers off is the default at night", "music down low is how I like it at dinner", "lights off at nine is the rule for the kids", "kitchen lights on all night was a mistake",
    "I need a vacation more than a timer", "timer off is not an option on this oven",
    "I always set a timer when I cook rice", "somebody should fix that fan", "the porch light is on a sensor", "my brother set an alarm for five and woke the house", "we never use the bedroom lamp",
    "she turned the fan off before she left", "he plays the radio in the garage all day", "my timer app is better than the oven timer", "the lights flickered twice during dinner",
    "the volume knob on the old stereo is stiff", "the shopping list is on the fridge door", "playing music at night keeps the dog calm", "turning off the lights is what my dad always did",
    "the alarm on my phone is louder than yours", "those lamps cost a fortune", "the fan in the office is noisy", "last night the timer rang twice", "the music upstairs is too quiet for me",
    "I like the lights dim when I read", "my wife says the stereo is too loud", "nobody touched the thermostat", "the speakers sound better downstairs", "I told the kids the lights stay on",
    "the playlist you made was great", "the heat is on and the fan is off",
]
FRESH_STATEMENTS = [                                                                                            # (a second list, written AFTER the rules were fixed: never used to shape them)
    "play button on the remote is stuck and the lamp is dim", "set menus are cheaper than the timer on the wall clock", "open plan kitchens have great lights", "close ups of the fan blades looked amazing",
    "stop lights on this street are timed badly", "turn around time on the playlist is slow", "cut glass lamps are heavy", "start up music from the old computer is a classic",
    "skip hire costs more than the lights", "shuffle play on this stereo is broken", "kill joys never leave the lights on", "keep fit videos always have loud music", "make up lights are bright",
    "mute swans are beautiful, the music in that film was too", "show business is full of lights and noise", "read only lists are safe", "list price of this lamp is high", "add on timers cost extra",
    "we want a new stereo for christmas", "I would like a quieter fan someday", "we need more lamps in the living room eventually", "I want to say thank you for the alarm",
    "I need to tell you the timer is broken", "lights off during the movie is our family rule", "volume down low is the only way the baby sleeps", "fans on both windows are noisy",
    "how about that song yesterday", "I need a break from the music", "the timer on the oven has been broken for weeks", "someone left the lamp on in the office again", "my alarm clock is a rooster",
    "she plays the radio too loud when she cooks", "the speaker in the hall crackles", "he never turns the lights off", "I think the fan is dying", "the playlist has two hundred songs",
    "the lights in the garage burned out yesterday", "our thermostat reads three degrees high",
]
KNOWN_STATEMENTS = ["we need a new thermostat someday", "dim lighting makes me sleepy", "lights out at ten is the rule at camp", "turn signals are on the left", "play time is over", "light up the room is a good song",
                    "cut costs on the lights", "play it by ear", "kill time with a list", "can you set timers"]                                       # (the ten the second review named)
ORDERS = [                                                                                                      # (40, written before the rules; the review's missed "please bring the volume down a little" among them)
    "bring the volume down a little", "please bring the volume down a little", "please bring the lights up a bit", "bring up the kitchen lights", "announce to the kitchen that lunch is ready",
    "announce to the living room that dinner is ready", "get some jazz music going", "I'd like a timer for twenty minutes", "cancel the pasta timer", "start a ten minute timer",
    "give me a timer for fifteen minutes", "make a timer for five minutes", "would you set an alarm for six in the morning", "turn the volume way down", "keep the lights off in the bedroom",
    "leave the porch light on", "add butter to the grocery list", "read me the shopping list", "dim the kitchen lights", "set the bedroom lamp to fifty percent", "switch on the office fan",
    "shut off the living room lamp", "power down the kitchen lights", "kill the music", "bump the volume up", "crank the volume up", "lower the music", "silence the stereo", "play the next track",
    "turn the music up a bit", "I'd like the lights dimmed", "can you start a timer for six minutes", "will you turn on the porch light", "pause the music for a second", "unmute the speakers",
    "put on some classical music", "wake me up at seven fifteen", "remind me about the dentist at nine", "clear the grocery list", "play my morning playlist", "turn it down a little",
]
FRESH_ORDERS = [
    "turn on the hall lights", "turn the hall lights off", "switch off the fan in the office", "put the porch light on", "set a timer for forty five minutes", "start a three minute timer", "play some blues",
    "pause the music please", "resume the music", "skip this song", "next song please", "shuffle my favorites", "mute the speakers", "dim the living room lamp", "brighten the office lights",
    "lights off in the hall", "the hall light needs to be on", "I want the fan off", "I need a timer for twenty minutes", "I'd like some music on the stereo", "could you turn the volume up",
    "would you set an alarm for five", "can you turn off the fan", "please turn off the lights", "wake me at six thirty", "remind me to call mom at noon", "add milk to the list", "announce that dinner is ready",
    "make the music louder", "kill the fan", "shut off the lamp", "queue another song", "set the thermostat to seventy",
]
REPLIES = ["Done.", "Okay.", "Done, Captain.", "Got it.", "Sure.", "On it."]


def test_fix3_no_statement_of_four_lists_fires_the_backstop_and_the_orders_do(tmp_path):
    b, _ = guard_brain(tmp_path)
    for said in [*STATEMENTS, *FRESH_STATEMENTS, *KNOWN_STATEMENTS]:
        for reply in REPLIES:
            assert not b._acknowledges(said, reply), (said, reply)
    for order in [*ORDERS, *FRESH_ORDERS]:
        for reply in REPLIES:
            assert b._acknowledges(order, reply), (order, reply)
    assert len(STATEMENTS) >= 60 and len(FRESH_STATEMENTS) >= 38 and len(ORDERS) >= 40 and len(FRESH_ORDERS) >= 33


@pytest.mark.parametrize("heard, shaped", [
    ("please bring the volume down a little", True), ("bring the lights up", True), ("have the lights on by seven", True), ("give me a timer for ten minutes", True), ("send the music upstairs", True),
    ("enable DJ mode", True), ("disable shuffle", True), ("move the music to the kitchen", True),
    ("we need a new thermostat someday", False), ("dim lighting makes me sleepy", False), ("lights out at ten is the rule at camp", False), ("turn signals are on the left", False), ("play time is over", False),
    ("light up the room is a good song", False), ("cut costs on the lights", False), ("play it by ear", False), ("kill time with a list", False),
    ("we want the lights on eventually", False), ("the lights are usually off", False), ("always turn off the porch light at dusk", False), ("sometimes I turn the fan on", False),
    ("I would like to thank you", False), ("we need to talk about the lights", False), ("I want to apologize about the timer", False), ("I want to hear some music", True), ("I want to turn off the lights", True),
    ("I would like to set an alarm", True), ("we want to dim the lights", True), ("I want the lights off", True), ("I need a pasta timer for ten minutes", True), ("I'd like some music in the kitchen", True),
    ("I need a vacation more than a timer", False), ("we want a new stereo for christmas", False), ("I want a quieter fan", False), ("I want the music quieter than this", True), ("I want it louder", True),
    ("how about that music last night", False), ("how about some jazz music", True), ("how about the lights on", True),
    ("can you set timers", False), ("could you turn on lights", False), ("can you set alarms for me", False), ("can you set a timer", True), ("can you set a timer for ten minutes", True),
    ("can you set timers for ten minutes", True), ("can you set kitchen timers", True), ("can you play music", True), ("can you turn the lights on", True), ("could you pause", True), ("can you play jazz", True),
    ("turn off the lights that are on", True), ("play the song we were listening to", True), ("make sure the lights are off", True), ("tell everyone dinner is ready", True),
    ("announce that dinner is ready", True), ("turn on the lights and keep them on", True), ("play some music and make it loud", True), ("set the timer to go off at six", True),
    ("let the lights stay on all night", True), ("turn off the lights when the movie is over", True), ("dim the lights in the room where the kids are sleeping", True),
    ("play take on me", True), ("play is this love", True), ("play come as you are", True), ("queue another song", True), ("put the lights out", True),
    ("lights off in the bedroom", True), ("lamp on in the bedroom please", True), ("the bedroom light needs to be on", True), ("next track", True), ("music louder", True), ("lights on please", True),
    ("kitchen lights, please", True), ("the fan on, please", True), ("lamps on both sides of the bed is how we do it", False), ("fans on high make the whole room cold", False),
    ("fan on high", True), ("volume up is the button", False), ("volume up", True),
])
def test_fix3_what_an_order_looks_like(heard, shaped):
    assert brain_mod.order_shaped(heard) is shaped, heard


# ── core#8: the backstop's coverage ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard, said", [
    ("I need a pasta timer for ten minutes", "Setting a timer for ten minutes."),                                    # (the real Core, 2026-11-05: a progressive verb)
    ("I need a pasta timer for ten minutes", "[sighs] Setting a timer for ten minutes. Don't burn the pasta, Captain."),
    ("start a ten minute timer", "Starting your timer now."), ("set an alarm for six", "Setting an alarm for six."), ("wake me at seven", "I will wake you at seven, Captain. Sleep well."),
    ("add milk to the shopping list", "Adding milk to your list."), ("add milk to the shopping list", "Added, Captain."), ("cancel the pasta timer", "Cancelling the pasta timer."),
    ("would you mind switching the kitchen lights on", "Fine, I'll switch on the kitchen lights for you. All done. You're welcome."),                       # (15 words: the real Core)
    ("turn on the bedroom lamp", "Done, Captain. The bedroom lamp is on, as you wished, and so is everything else in this magnificent house."),      # (19 words)
    ("set a timer for ten minutes", "Sure thing, Captain, the pasta timer is going and the weather outside is delightful, the birds are singing, and the neighbours are loud."),      # (24 words that name the timer)
    ("pause the music", "Paused."), ("pause the music", "Okay, paused."), ("skip this song", "Next."), ("skip this song", "Skipping."), ("make it louder", "Louder it is."), ("play it upstairs", "Upstairs it is."),
    ("dim the lamp to forty percent", "Forty percent, as you like it."), ("skip this song", "Next, and you're welcome."), ("set the volume to five", "Volume five."), ("cancel the timer", "Timer cancelled."),
    ("set a timer for ten minutes", "Timer set for ten minutes."), ("turn off the bedroom fan", "Dimmed. Not that you deserve it."),
])
def test_fix3_the_backstop_catches_these_claims_with_no_tool(heard, said, tmp_path):
    b, _ = guard_brain(tmp_path)
    assert b._acknowledges(heard, said), (heard, said)


@pytest.mark.parametrize("heard, said", [
    ("set a timer for ten minutes", "I can set a timer for you, just tell me how long."),                           # (an offer is no claim)
    ("set a timer for ten minutes", "I could start a timer, but for what?"),
    ("set an alarm", "To set an alarm I need a time."),
    ("turn on the bedroom lamp", "Fine, I would turn on the lamp if I could reach it, but I cannot, so there is that."),
    ("add milk to the shopping list", "Nothing was added, the list is full of your nonsense."),
    ("set a timer for ten minutes", "Ten minutes? Timer set? Not a chance, monkey."),
    ("turn on the bedroom lamp", "Setting aside my disdain for such a trivial task, no."),
    ("play me a haiku", "Playing along: autumn moon, bright, a beer can sighs, nobody listens."),
    ("turn on the kitchen lights", "Fine, I will go ahead and turn on the kitchen lights for you right now because apparently nobody else in this house can reach a switch at all. Done."),      # (more than 25 words)
    ("set a timer for ten minutes", "The weather today is lovely, the pasta is cooking, the sun is out and the neighbors are loud and I wanted to tell you at length about all of it."),   # (long and names no timer)
    ("set a timer for ten minutes", "Sure thing, Captain, and the weather outside is delightful, the birds are singing, and the neighbours are loud again."),                       # (19 words and an acknowledgement, but it names no timer: mutant M46)
])
def test_fix3_the_backstop_does_not_take_these_for_claims(heard, said, tmp_path):
    b, _ = guard_brain(tmp_path)
    assert not b._acknowledges(heard, said), (heard, said)


def test_fix3_a_progressive_claim_to_a_timer_order_is_asked_again_end_to_end(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[sighs] Setting a timer for ten minutes. Don't burn the pasta, Captain.")], [call("set_timer", seconds=600, label="pasta")], [text("Ten minutes, on the clock.")])
    assert b.reply("I need a pasta timer for ten minutes") == "Ten minutes, on the clock." and b.guard == ("reask", "ack") and ("pasta", 600) in b.world.timers.remaining()


def test_fix3_a_15_word_false_acknowledgement_to_a_lights_order_is_asked_again_and_the_music_is_not_mentioned(tmp_path, no_routes):
    said = "[sighs] Fine, I'll switch on the kitchen lights for you. *presses imaginary button* There. All done. You're welcome."
    b, model = guard_brain(tmp_path, [text(said)], [call("home_control", name="kitchen under 1", action="on")], [text("There.")])
    assert b.reply("would you mind switching the kitchen lights on") == "There." and b.guard == ("reask", "ack") and b.world.house.calls == [("light", "turn_on", {"entity_id": "light.kitchen_under_1"})]
    b, model = guard_brain(tmp_path / "long", [text("Fine, I will go ahead and turn on the kitchen lights for you right now because apparently nobody else in this house can reach a switch at all. *presses imaginary button* Done.")])
    out = b.reply("would you mind switching the kitchen lights on")                                              # (too long to be judged: but never "I have not played, moved or changed any music.")
    assert "music" not in out and len(model.calls) == 1


def test_fix3_musicguard_does_not_retract_music_from_an_order_for_the_lights():
    c2 = "[sighs] Fine, I'll switch on the kitchen lights for you. *presses imaginary button* There. All done. You're welcome."
    assert musicguard.classify("would you mind switching the kitchen lights on", "", c2, []) == (None, None)       # (was ('retract', 'claim'): the imaginary button read as a dial)
    for heard in ("turn on the kitchen lights", "turn off the living room lights", "turn the lights on upstairs", "turn off the music room lamp", "flip the switch on the fan"):
        assert musicguard.lights_order(heard), heard
        assert musicguard.classify(heard, "", "*flips the switch* There. All done.", []) == (None, None), heard
    for heard in ("play jazz upstairs and turn on the lights", "turn the music up", "make the volume louder", "turn on the stereo", "play something in the living room"):
        assert not musicguard.lights_order(heard), heard
    for heard, said in (("turn on the lights upstairs", "Turning on the lights upstairs."), ("turn off the living room lights", "I turned off the living room lights, Captain."),
                        ("turn on the music room lamp", "Done. The music room lamp is on.")):             # (a light is named by a place or a word of the music: no music is claimed, with no tool or any; mutant M57)
        assert musicguard.classify(heard, "", said, []) == (None, None), said
    # what the guard is FOR still works: music claimed to an order for the lights, and a dial claimed to a request for music
    assert musicguard.classify("turn on the kitchen lights", "", "Done. Playing some jazz to go with it.", []) == ("retract", "claim")
    assert musicguard.classify("make the volume louder", "", "*turns an imaginary dial* There. Magnificently loud.", [])[0] == "reask"
    assert musicguard.classify("hello there", "", "I turned off the upstairs lights, Captain.", [])[0] == "retract"


# ── code#8: the denial's second try is shown the whole tool list ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard, denial", [("what timers do I have", "I have no access to your timers, Captain."), ("are you able to cancel alarms", "I cannot cancel alarms from here, Captain."),
                                            ("is the heat on", "I cannot see the thermostat, Captain."), ("I want a five minute egg timer", "I cannot set timers from here.")])
def test_fix3_the_denials_second_try_is_shown_the_whole_list_the_turn_itself_used_and_the_nudge_names_what_it_may_use(heard, denial, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(denial)], [text("Still no.")])
    b.reply(heard)
    first, second = model.calls[0], model.calls[1]
    assert second["tools"] == first["tools"] == b.tools(), heard                                               # (Core caches per tool list: a cut menu was read from scratch, 7.8 to 44 s)
    named = named_in_nudge(second["messages"])
    assert not named & {"cancel_alarm", "cancel_timers", "announce"} and named <= {t["name"] for t in b.tools()}
    if not brain_mod.order_shaped(heard) or brain_mod.NOT_AN_ORDER.search(heard):                              # (a question: it may only look)
        assert not named & {"set_timer", "set_alarm", "play_music", "speakers", "home_control"}


def test_fix3_what_the_cut_menu_forbade_still_does_not_run_and_the_right_tool_does(tmp_path, no_routes):
    for i, (heard, denial, wrong) in enumerate((("are you able to cancel alarms", "I cannot cancel alarms from here.", call("cancel_alarm")), ("do you know how to clear the list", "I cannot clear lists.", call("list", action="clear", list="shopping")),
                                                 ("tell my wife I love her", "I cannot call your wife.", call("announce", text="I love her", place="everywhere")),
                                                 ("what timers do I have", "I have no access to your timers.", call("set_timer", seconds=5)))):
        b, model = guard_brain(tmp_path / f"w{i}", [text(denial)], [wrong], [text("Not done.")])
        assert b.reply(heard) == denial and untouched(b), heard
        assert model.calls[2]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE
    b, model = guard_brain(tmp_path / "right", [text("I cannot set timers from here.")], [call("set_timer", seconds=300, label="eggs")], [text("Five minutes on the eggs.")])
    assert b.reply("I want a five minute egg timer") == "Five minutes on the eggs." and ("eggs", 300) in b.world.timers.remaining()
    b, model = guard_brain(tmp_path / "look", [text("I have no access to your timers.")], [call("list_timers")], [text("Pasta, monkey.")])
    assert b.reply("what timers do I have") == "Pasta, monkey." and [a["tool"] for a in b.actions] == ["list_timers"]


# ── code#2: the survivors of the second review's mutation run ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix3_b8_a_question_that_holds_an_order_is_not_second_guessed(tmp_path, no_routes):
    b, model = guard_brain(tmp_path)
    assert brain_mod.order_shaped("what time is it, and turn off the lights") and brain_mod.NOT_AN_ORDER.search("what time is it, and turn off the lights")
    assert not b._acknowledges("what time is it, and turn off the lights", "Done.")                             # (the question's second try may only look: NOT_AN_ORDER keeps the backstop out)
    b, model = guard_brain(tmp_path / "e2e", [text("Done.")])
    assert b.reply("what time is it, and turn off the lights") == "Done." and len(model.calls) == 1 and b.guard != ("reask", "ack")


def test_fix3_o7_a_trailing_please_is_an_order_shape(tmp_path, no_routes):
    assert brain_mod.order_shaped("the kitchen lights, please") and brain_mod.order_shaped("kitchen lights, please") and brain_mod.order_shaped("the fan, please")
    b, model = guard_brain(tmp_path, [text("Done.")], [call("home_control", name="kitchen under 1", action="on")], [text("There.")])
    assert b.reply("the kitchen lights, please") == "There." and b.guard == ("reask", "ack") and len(model.calls) == 3


def test_fix3_o8_please_in_a_negated_utterance_is_no_order(tmp_path, no_routes):
    for heard in ("I do not want the lights on, please", "don't turn the lights on, please", "not the kitchen lights, please", "never turn the lights on, please"):
        assert not brain_mod.order_shaped(heard), heard
    b, model = guard_brain(tmp_path, [text("Done.")])
    assert b.reply("I do not want the lights on, please") == "Done." and len(model.calls) == 1 and b.guard != ("reask", "ack")


# ── the 32 false acknowledgements of the second review, end to end (a scripted bare reply, routes off, the model asked again) ────────────────────────────────────────────────────────────────────────────
FALSE_ACKS = [("turn the kitchen lights on", t) for t in [
    "Done.", "Done, Captain.", "Okay. The kitchen lights are on.", "[sighs] Done.", "*sighs* Done.", "[sighs] On it. *click* Kitchen lights are on.", "As if I have anything better to do. Done.",
    "[sighs] As if I have anything better to do. Done.", "Fine. Done, Captain.", "Lights on. Try not to trip over the furniture, monkey.", "The kitchen lights are on, Captain.",
    "There. Kitchen lights on. Magnificent as always.", "Kitchen lights are on. Done.", "You people and your lights. On it.", "Yes, yes. Done.", "Ugh, fine. Done.", "Lights are on now.",
    "Consider it done, monkey.", "Your wish is my command.", "The lights are on. You are welcome.", "Turning them on now.", "I have turned on the kitchen lights."]]
FALSE_ACKS += [("set a timer for ten minutes", "Timer's set, Captain."), ("set a timer for ten minutes", "Okay. Ten minutes on the clock."), ("play some jazz", "Playing jazz now, monkey."),
               ("pause the music", "Paused."), ("make it louder", "Louder it is."), ("wake me at seven", "Alarm's set. Sleep well."), ("add milk to the shopping list", "Added, Captain."),
               ("turn off the bedroom fan", "The fan is off."), ("dim the lamp to forty percent", "Forty percent, as you like it."), ("skip this song", "Next, and you're welcome.")]


@pytest.mark.parametrize("heard, said", FALSE_ACKS)
def test_fix3_every_false_acknowledgement_of_the_review_gets_a_second_try(heard, said, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(said)], [text("I did not do that, Captain.")])
    b.reply(heard)
    assert len(model.calls) == 2, (heard, said)
