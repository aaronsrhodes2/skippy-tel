"""Learned phrases (aliases.py): an order that missed and then worked runs its call at once, with guards; the miss ledger (misses.py) is what teaches it."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aliases  # noqa: E402
import brain  # noqa: E402
import misses  # noqa: E402
from test_sat_misses import act, cmd  # noqa: E402


def learned(cmds, acts=(), previous=None):
    return aliases.learn(cmds, list(acts), brain, misses, previous, "2026-11-11")["aliases"]


MISS = cmd("10:00:00", "turn off the upstairs lights", reply="No lights listed.", tools=["home_devices"], date="2026-11-01")
WORKED = {**cmd("09:00:00", "Turn off the upstairs lights.", reply="Done.", tools=["home_control"], date="2026-11-05"),
          "calls": [{"tool": "home_control", "args": {"name": "upstairs lights", "action": "off"}}]}


def test_a_phrase_that_missed_and_then_worked_becomes_an_alias_with_the_call_that_worked():
    (a,) = learned([MISS, WORKED])
    assert a["phrase"] == "turn off the upstairs lights" and a["tool"] == "home_control" and a["args"] == {"name": "upstairs lights", "action": "off"}
    assert a["learned"] == "2026-11-05" and not a["disabled"]


def test_nothing_is_learned_without_a_miss_first_or_for_anything_unsafe():
    assert learned([WORKED]) == []                                                                       # it never missed: aliases repair misses, they are not a cache
    lock = {**WORKED, "words": "lock the front door", "calls": [{"tool": "home_control", "args": {"name": "front door", "action": "lock"}}]}
    assert learned([cmd("10:00:00", "lock the front door", reply="I cannot.", date="2026-11-01"), lock]) == []
    heat = {**WORKED, "calls": [{"tool": "home_control", "args": {"name": "the thermostat", "action": "off"}}]}
    assert learned([MISS, heat]) == []                                                                   # a sensitive name, whatever the action
    two = {**WORKED, "calls": WORKED["calls"] * 2}
    assert learned([MISS, two]) == []                                                                    # one clean call only
    one_word = {**cmd("09:00:00", "lights", tools=["home_control"], date="2026-11-05"), "calls": WORKED["calls"]}
    assert learned([cmd("10:00:00", "lights", reply="Hm.", date="2026-11-01"), one_word]) == []          # at least two words
    assert learned([MISS, {k: v for k, v in WORKED.items() if k != "calls"}]) == []                      # old log lines carry no arguments: nothing to learn from


def test_a_phrase_that_is_later_repeated_or_corrected_is_switched_off_and_stays_off():
    again = [cmd("11:00:00", "turn off the upstairs lights", reply="Done.", tools=["home_control"], date="2026-11-06"),
             cmd("11:00:20", "turn off the upstairs lights", reply="Done.", tools=["home_control"], date="2026-11-06")]
    (a,) = learned([MISS, WORKED, *again])
    assert a["disabled"] and "rephrased" in a["disabled"]
    (still,) = learned([MISS, WORKED], previous={"aliases": [a]})
    assert still["disabled"]                                                                             # off is remembered: it does not come back by itself


def test_the_store_runs_an_alias_only_when_the_tool_is_here_and_the_call_is_still_safe(tmp_path):
    path = tmp_path / "aliases.json"
    good = {"phrase": "banana mode", "tool": "dj_mode", "args": {"mode": "on"}, "learned": "2026-11-05", "disabled": None}
    path.write_text(json.dumps({"aliases": [good, {**good, "phrase": "off one", "disabled": "rephrased on 2026-11-06"},
                                            {"phrase": "open sesame", "tool": "home_control", "args": {"name": "garage door", "action": "open"}, "disabled": None}]}), encoding="utf-8")
    store = aliases.AliasStore(path)
    assert store.route("Hey Skippy, banana mode, please.", {"dj_mode"}) == ("dj_mode", {"mode": "on"})
    assert store.route("banana mode", {"music"}) is None                                                 # the tool is not here
    assert store.route("off one", {"dj_mode"}) is None and store.route("open sesame", {"home_control"}) is None   # switched off / a hand-edited unsafe call
    assert store.route("banana", {"dj_mode"}) is None and aliases.AliasStore(path, enabled=False).route("banana mode", {"dj_mode"}) is None
    assert aliases.AliasStore(tmp_path / "none.json").route("banana mode", {"dj_mode"}) is None


def test_the_brain_runs_an_alias_before_the_model_and_the_model_only_words_it(tmp_path):
    from timers import Timers
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({"aliases": [{"phrase": "banana mode", "tool": "dj_mode", "args": {"mode": "on"}, "disabled": None}]}), encoding="utf-8")
    seen = {}

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                seen["tools"] = kw.get("tools")
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Banana mode. Obviously.")])

    ran = []
    b = brain.Brain(Client, "m", Timers(), persona="p", extra=([{"name": "dj_mode"}], lambda n, a: ran.append((n, a)) or "(Done.) DJ mode on."))
    b.aliases = aliases.AliasStore(path)
    assert b.reply("banana mode") == "Banana mode. Obviously." and ran == [("dj_mode", {"mode": "on"})] and seen["tools"] is None
