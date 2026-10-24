"""Skippy's quick brain for the house: the persona, spoken style, a short memory, and timers as its only tool."""
from types import SimpleNamespace as NS

from brain import FORGET_AFTER_S, TOOLS, Brain, say_duration
from timers import Timers


def text(t):
    return NS(type="text", text=t)


def tool(name, **args):
    return NS(type="tool_use", id="tu_" + name, name=name, input=args)


class Client:
    """Answers from a script: each call returns the next list of blocks."""

    def __init__(self, *rounds):
        self.rounds, self.calls = list(rounds), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return NS(content=self.rounds.pop(0))


def make(*rounds, now=None):
    clock = [1000.0]
    timers = Timers(clock=lambda: clock[0])
    client = Client(*rounds)
    return Brain(client, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: "Sunday 09:30"), client, timers, clock


def test_the_system_prompt_is_the_persona_plus_how_to_speak_aloud_and_the_time():
    b, c, *_ = make([text("Done.")])
    b.reply("hello")
    system = c.calls[0]["system"]
    assert system.startswith("PERSONA") and "speaking aloud" in system and "no markdown" in system and "Sunday 09:30" in system
    assert "replacing his Alexa" in system and c.calls[0]["model"] == "m" and c.calls[0]["max_tokens"] <= 400


def test_a_timer_request_runs_the_tool_and_speaks_the_result():
    b, c, timers, _ = make([tool("set_timer", seconds=600, label="pasta")], [text("Ten minutes, on the clock.")])
    assert b.reply("set a pasta timer for ten minutes") == "Ten minutes, on the clock."
    assert timers.remaining() == [("pasta", 600)]
    second = c.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and "Timer set: pasta, 10 minutes" in second[-1]["content"][0]["content"]
    assert second[-2]["role"] == "assistant"


def test_tools_cancel_and_list_and_bad_input_never_crash():
    b, _, timers, _ = make()
    timers.add(90, "tea")
    assert "tea: 1 minute 30 seconds left" in b.run_tool("list_timers", {})
    assert b.run_tool("cancel_timers", {"label": "tea"}) == "Cancelled 1 timer."
    assert b.run_tool("list_timers", {}) == "No timers running."
    assert b.run_tool("set_timer", {"seconds": "soon"}).startswith("That did not work")
    assert b.run_tool("set_timer", {"seconds": 0}).startswith("That did not work")
    assert b.run_tool("open_the_pod_bay_doors", {}) == "I have no such tool." and b.run_tool("set_timer", None).startswith("That did not work")


def test_it_remembers_the_last_few_exchanges_and_forgets_after_ten_quiet_minutes():
    b, c, _, clock = make([text("One.")], [text("Two.")], [text("Three.")])
    b.reply("first")
    b.reply("second")
    assert [m["content"] for m in c.calls[1]["messages"]] == ["first", "One.", "second"]       # the tool traces are not kept, only the words
    clock[0] += FORGET_AFTER_S + 1
    b.reply("third")
    assert [m["content"] for m in c.calls[2]["messages"]] == ["third"]


def test_it_never_loops_forever_on_tools_and_never_returns_nothing():
    b, c, *_ = make(*[[tool("list_timers")] for _ in range(4)])
    assert "nothing to say" in b.reply("loop")                                                    # four rounds at most
    assert len(c.calls) == 4
    b2, *_ = make([])
    assert "nothing to say" in b2.reply("hmm")


def test_the_only_tools_are_timers_and_say_duration_reads_naturally():
    assert {t["name"] for t in TOOLS} == {"set_timer", "cancel_timers", "list_timers"}            # no shell, no files, no deploys
    assert say_duration(1) == "1 second" and say_duration(61) == "1 minute 1 second" and say_duration(3600) == "1 hour" and say_duration(5400) == "1 hour 30 minutes"


def test_with_home_assistant_the_house_tools_join_the_timers_and_the_prompt_stops_saying_the_house_is_unconnected():
    from test_sat_hass import make as make_ha
    ha, ha_fake = make_ha()
    clock = [1000.0]
    timers = Timers(clock=lambda: clock[0])
    client = Client([NS(type="tool_use", id="tu_h", name="home_control", input={"name": "kitchen light", "action": "on"})], [text("Kitchen light is on.")])
    b = Brain(client, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: "Sunday 09:30", hass=ha)
    assert b.reply("kitchen light on") == "Kitchen light is on."
    assert ha_fake.calls == [("light/turn_on", {"entity_id": "light.kitchen"})]
    assert {t["name"] for t in client.calls[0]["tools"]} == {"set_timer", "cancel_timers", "list_timers", "home_devices", "home_state", "home_control"}
    system = client.calls[0]["system"]
    assert "the thermostat (in the unit it shows)" in system and "not connected yet" not in system
    plain, c2, *_ = make([text("ok")])
    plain.reply("hi")
    assert {t["name"] for t in c2.calls[0]["tools"]} == {"set_timer", "cancel_timers", "list_timers"} and "not connected yet" in c2.calls[0]["system"]
