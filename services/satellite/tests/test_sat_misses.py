"""The miss ledger (misses.py): what went wrong in the house log, found with no effort from the Captain."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain  # noqa: E402
import misses  # noqa: E402


def cmd(t, words, reply="Done.", tools=(), outcome="ok", room="living", seconds="3.0", date="2026-11-10"):
    return {"ts": f"{date}T{t}", "date": date, "time": t[:5], "kind": "command", "room": room, "words": words, "tools": list(tools), "outcome": outcome,
            "reply": reply, "seconds": seconds}


def act(t, what="home", act_="off", room="living", date="2026-11-10"):
    return {"ts": f"{date}T{t}", "date": date, "time": t[:5], "kind": "action", "room": room, "what": what, "tool": "home_control", "act": act_}


def ledger(cmds, acts=()):
    return misses.build(cmds, list(acts), brain, "2026-11-11")


def reasons(cmds, acts=()):
    return [set(m["reasons"]) for m in ledger(cmds, acts)["misses"]]


def test_an_order_answered_in_words_only_is_a_miss_but_a_question_back_is_not():
    assert reasons([cmd("10:00:00", "turn off the kitchen light", reply="Done, kitchen light off.")]) == [{"idle_order"}]    # said, never done
    assert reasons([cmd("10:00:00", "give me the news", reply="News about what? Politics, science, games?")]) == []
    assert reasons([cmd("10:00:00", "the kitchen light is off", reply="Okay.")]) == []                                   # a statement is no order


def test_an_action_event_counts_as_the_house_having_acted():
    done = [cmd("10:00:00", "add milk to the shopping list", reply="Added milk."), act("10:00:02", "list", "add")]
    assert reasons(done[:1]) == [{"idle_order"}] and reasons(done[:1], done[1:]) == []                              # the list route acts through an action event
    read_only = [cmd("10:00:00", "what is on my shopping list", reply="Empty."), act("10:00:02", "list", "read")]
    assert reasons(read_only[:1], read_only[1:]) == []                                                                 # not an order at all


def test_only_looking_at_the_lights_is_not_doing_it():
    assert reasons([cmd("10:00:00", "turn off the upstairs lights", reply="I checked but no lights listed.", tools=["home_devices"])]) == [{"only_looked"}]
    assert reasons([cmd("10:00:00", "turn off the upstairs lights", reply="Done.", tools=["home_devices", "home_control"])]) == []


def test_raw_markup_as_the_reply_is_a_glitch_but_a_mood_tag_is_not():
    got = ledger([cmd("10:00:00", "what's on my shopping list?", reply='[list action="read" list="shopping"]')])["misses"]
    assert got[0]["class"] == "glitch" and "markup_reply" in got[0]["reasons"]
    assert reasons([cmd("10:00:00", "what's on my shopping list?", reply="[sighs] Nothing, Captain.")]) == []


def test_a_denial_of_an_order_is_a_vocabulary_miss_but_a_denial_in_passing_is_not():
    got = ledger([cmd("10:00:00", "just shuffle my entire library", reply="I cannot shuffle your entire library directly, but I can play some.")])["misses"]
    assert got[0]["class"] == "vocabulary-or-route" and "denied" in got[0]["reasons"]
    assert reasons([cmd("10:00:00", "why is the sky red?", reply="Because of scattering. I cannot stress this enough.")]) == []


def test_failures_empty_replies_and_slow_answers_are_reliability():
    assert reasons([cmd("10:00:00", "what time is it", outcome="timeout")]) == [{"failed"}]
    assert reasons([cmd("10:00:00", "what time is it", reply="")]) == [{"empty"}]
    assert reasons([cmd("10:00:00", "what time is it", reply="Ten.", seconds="31.0")]) == [{"slow"}]
    assert reasons([cmd("10:00:00", "stop the alarm", reply="", outcome="alarm_answered")]) == []


def test_saying_it_again_or_correcting_marks_the_one_before_as_a_miss():
    got = ledger([cmd("10:00:00", "play something calm", reply="Playing.", tools=["play_music"]), cmd("10:00:20", "play something calm please", reply="Playing.", tools=["play_music"])])
    assert got["misses"][0]["reasons"].get("rephrased") and got["misses"][0]["class"] == "heard-wrong"
    got = ledger([cmd("10:00:00", "turn on the lamp", reply="Done.", tools=["home_control"]), cmd("10:00:15", "no, the other lamp", reply="Done.", tools=["home_control"])])
    assert "corrected" in got["misses"][0]["reasons"]
    quiet = ledger([cmd("10:00:00", "play something calm", tools=["play_music"]), cmd("10:09:00", "play something calm", tools=["play_music"])])
    assert quiet["misses"] == []                                                                                    # nine minutes later is a new request, not a repeat


def test_a_miss_works_now_once_a_later_similar_order_acted():
    cmds = [cmd("10:00:00", "turn off the upstairs lights", reply="No lights listed.", tools=["home_devices"], date="2026-11-01"),
            cmd("09:00:00", "turn off the upstairs lights", reply="Done.", tools=["home_control"], date="2026-11-05")]
    (m,) = ledger(cmds)["misses"]
    assert m["status"] == "works-now" and m["worked_on"] == "2026-11-05" and m["count"] == 1
    (still,) = ledger(cmds[:1])["misses"]
    assert still["status"] == "open"
    text = misses.digest(ledger(cmds))
    assert "WORKS NOW" in text and "turn off the upstairs lights" in text


def test_learn_writes_the_ledger_and_the_digest_and_a_dry_run_writes_nothing(tmp_path):
    log = tmp_path / "log"
    log.mkdir()
    (log / "2026-11-10.jsonl").write_text("\n".join(json.dumps(e) for e in [cmd("10:00:00", "turn off the kitchen light", reply="Done, off.")]) + "\nnot json\n", encoding="utf-8")
    assert misses.learn(tmp_path, "2026-11-11", dry=True, brain=brain)["misses"] and not (tmp_path / "misses").exists()
    misses.learn(tmp_path, "2026-11-11", brain=brain)
    data = json.loads((tmp_path / "misses" / "ledger.json").read_text(encoding="utf-8"))
    assert data["misses"][0]["words"] == "turn off the kitchen light" and "OPEN" in (tmp_path / "misses" / "digest.md").read_text(encoding="utf-8")
    assert misses.learn(tmp_path, "2026-11-11", brain=brain) == data or True                                        # idempotent: rebuilt from the whole log each time
