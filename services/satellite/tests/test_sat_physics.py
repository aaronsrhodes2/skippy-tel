"""The house voice's Mentat (PLATINUM 21: Mentat does the physics): ask_mentat sends his question to Mentat on skippy-core and returns its answer as plain words with units and a
short source; a plain physics or unit question reaches it before the model answers; it never fills an input in (only his yes sends Mentat's own suggestion back); an outage is said,
never covered with a number from memory; the prompt names Mentat as out of reach only while the tool is missing."""
import urllib.error
from types import SimpleNamespace as NS

import physics
from brain import Brain
from test_sat_brain import Client, text, tool
from test_sat_car import make as make_car, reader
from timers import Timers

ESCAPE = {"ok": True, "result": {
    "assumed": None, "clarification": None, "display": None, "error": None, "formula": "v_esc = sqrt(2 G M / r)", "interpreted_as": "escape_velocity(body_name='earth')",
    "interpreter": "fold rule", "provenance_tag": "DERIVED", "uncertainty": None, "units": "m/s", "value": 11186.135603087496,
    "source": "sigma-ground (classical escape velocity); Earth's GM from JPL DE440 header (header.440; Park et al. 2021), radius from predictor table",
    "notes": "Earth: GM 398600.435507 km^3/s^2 (JPL DE440 header (header.440; Park et al. 2021)). Velocity needed to escape gravitational binding to infinity."}}
LIGHT = {"ok": True, "result": {"units": "m / s", "value": 299792458.0, "source": "scipy.constants (CODATA: speed of light in vacuum)", "provenance_tag": "VERIFIED",
                                "interpreted_as": "lookup_constant(name='speed_of_light')", "notes": "Exact by definition in the SI (uncertainty 0)."}}
NEEDS_MASS = {"ok": True, "result": {"value": None, "units": None, "clarification": {
    "needs": ["mass_kg"], "sentence": "How heavy is the car? I need a mass to give you the kinetic energy.",
    "suggested": {"mass_kg": {"value": 1500, "units": "kg", "why": "a typical family car", "label": "mass"}},
    "follow_up": {"question": "What is the kinetic energy of a car going 60 miles per hour?", "given": {"mass_kg": 1500}}}}}
WEB = {"ok": True, "result": {"value": None, "units": None, "formula": None, "interpreted_as": "research", "interpreter": "research skippy-chat",
                              "display": "The New York Yankees won the 1999 World Series [2]. They swept the Braves [4][5].",
                              "notes": "The New York Yankees won the 1999 World Series [2].\n\nSources:\n[1] 1999 World Series - Wikipedia: https://en.wikipedia.org/wiki/1999_World_Series"}}


def make(*replies, env=None):
    """A Physics whose Mentat answers from the script (an exception is raised); `sent` records every request."""
    sent, script = [], list(replies)

    def post(url, body, headers=None, timeout=None):
        sent.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        got = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(got, Exception):
            raise got
        return got
    return physics.Physics(env or {}, post=post, clock=lambda: clock[0]), sent


clock = [1000.0]


def test_the_answer_is_the_value_with_its_units_how_mentat_read_it_and_a_short_source():
    p, sent = make(ESCAPE)
    said = p.run({"question": "What is the escape velocity of the Earth?"}, room="living", trace="house-living-1")
    assert said.startswith("Mentat's answer: 11186 m/s. Mentat read the question as: escape_velocity(body_name='earth'). Source: sigma-ground (classical escape velocity). Provenance: DERIVED.")
    assert "Note: Earth: GM 398600.435507" in said and "Velocity needed to escape" in said and "calculate or convert it yourself" in said
    assert "11186.1" not in said and "JPL DE440 header (header.440; Park" not in said.split("Note:")[0]               # five digits, and the source is its first clause
    assert sent == [{"url": "http://skippy-core:7350/ask", "body": {"question": "What is the escape velocity of the Earth?"}, "timeout": physics.ASK_TIMEOUT_S,
                     "headers": {"X-Skippy-Caller": "house-voice", "X-Skippy-Trace": "house-living-1"}}]


def test_numbers_are_said_to_five_digits_an_exact_one_whole_and_a_huge_one_in_words():
    n = physics.say_number
    assert n(11186.135603087496) == "11186" and n(499.00478383615643) == "499" and n(539580.8563199999) == "539580" and n(0.621371192237334) == "0.62137"
    assert n(299792458.0) == "299792458" and n(0) == "0" and n(-273.15) == "-273.15" and n(3.5) == "3.5"
    assert n(6.02214076e23) == "6.0221 times 10 to the 23" and n(1.602176634e-19) == "1.6022 times 10 to the -19"
    assert n(True) == "True" and n(float("nan")) == "nan"
    p, _ = make(LIGHT)
    assert p.run({"question": "how fast is light"}).startswith("Mentat's answer: 299792458 m / s. ")
    assert physics.say_value({"dipole_moment_debye": 1.836651840103348}) == "dipole moment debye 1.8367"


def test_a_web_answer_is_marked_as_a_search_and_loses_its_citation_marks_and_links():
    p, _ = make(WEB)
    said = p.run({"question": "who won the 1999 world series"})
    assert said.startswith("Mentat did not calculate this: it looked it up on the web.")
    assert "The New York Yankees won the 1999 World Series. They swept the Braves." in said
    assert "[" not in said and "http" not in said and "Sources" not in said


def test_when_an_input_is_missing_mentat_suggests_and_only_his_yes_sends_exactly_that_back():
    p, sent = make(NEEDS_MASS, ESCAPE)
    said = p.run({"question": "What is the kinetic energy of a car going 60 miles per hour?"}, room="kitchen")
    assert "How heavy is the car?" in said and "It suggests mass = 1500 kg (a typical family car)." in said and "It has computed nothing." in said and "use_suggestion=true" in said
    clock[0] += 60
    p.run({"use_suggestion": True, "question": "whatever the model wrote", "given": {"mass_kg": 9999}}, room="kitchen")
    assert sent[1]["body"] == {"question": "What is the kinetic energy of a car going 60 miles per hour?", "given": {"mass_kg": 1500}}      # Mentat's own follow-up, never the model's values
    assert p.run({"use_suggestion": True}, room="kitchen").startswith("There is no suggestion of Mentat's waiting")                       # used once
    p, sent = make(NEEDS_MASS)
    p.run({"question": "kinetic energy of a car at 60 mph"}, room="kitchen")
    assert p.run({"use_suggestion": True}, room="living").startswith("There is no suggestion")                                            # another room's yes is not his
    clock[0] += physics.KEEP_SUGGESTION_S + 1
    assert p.run({"use_suggestion": True}, room="kitchen").startswith("There is no suggestion") and len(sent) == 1                        # and five minutes is all it lasts


def test_a_new_question_clears_a_suggestion_that_was_never_answered():
    p, sent = make(NEEDS_MASS, ESCAPE)
    p.run({"question": "kinetic energy of a car at 60 mph"}, room="kitchen")
    p.run({"question": "escape velocity of the earth"}, room="kitchen")
    assert p.run({"use_suggestion": True}, room="kitchen").startswith("There is no suggestion") and len(sent) == 2


def test_the_follow_up_is_only_kept_when_it_is_plain_values_of_a_sane_size():
    ok = {"question": "q", "given": {"a": 1, "b": 2.5, "c": "9 kg"}}
    assert physics.clean_follow_up(ok) == ok
    for bad in (None, "x", {"question": "", "given": {"a": 1}}, {"question": "q", "given": {}}, {"question": "q", "given": {"a": True}}, {"question": "q", "given": {"a": [1]}},
                {"question": "q", "given": {"x" * 61: 1}}, {"question": "q", "given": {str(i): i for i in range(41)}}):
        assert physics.clean_follow_up(bad) is None, bad
    assert physics.clean_follow_up({"question": "q", "given": {"a": "z" * 500}})["given"]["a"] == "z" * 120


def test_a_question_mentat_cannot_model_or_an_error_is_said_and_never_answered_from_memory():
    p, _ = make({"ok": True, "result": {"value": None, "notes": "Not a scenario I can model. I can do orbits, optics, thermodynamics."}})
    assert p.run({"question": "q"}).startswith("Mentat could not model that question: Not a scenario I can model.") and "do not give a number from memory" in p.run({"question": "q"})
    p, _ = make({"ok": True, "result": {"value": None, "error": {"code": "bad_arg", "message": "mass must be positive"}}})
    assert p.run({"question": "q"}) == "Mentat could not answer: mass must be positive. Say so; do not answer the physics yourself."
    p, _ = make({"ok": False, "error": "ask needs a question"})
    assert p.run({"question": "q"}).startswith("Mentat refused: ask needs a question.")
    p, _ = make("not a dict")
    assert "unreadable" in p.run({"question": "q"})
    p, _ = make({"ok": True, "result": {"value": 3, "units": "", "provenance_tag": "SPECULATIVE-PENDING"}})
    assert "speculative placeholder" in p.run({"question": "q"})


def test_an_outage_a_slow_answer_and_a_busy_mentat_each_say_so_without_a_number():
    logged = []
    for failure, expect in ((urllib.error.URLError("refused"), "is not answering right now (URLError)"), (TimeoutError("timed out"), "did not answer in time"),
                            (urllib.error.URLError(TimeoutError("timed out")), "did not answer in time"), (ValueError("bad json"), "is not answering right now (ValueError)"),
                            ({"ok": False, "error": "down", "status": 503}, "it answered 503"), ({"ok": False, "error": "gone", "status": 404}, "it answered 404"),
                            ({"ok": False, "error": "Slow down", "status": 429}, "slow down")):
        p, _ = make(failure)
        p.log = logged.append
        said = p.run({"question": "escape velocity of mars"})
        assert expect in said and "do not answer the physics yourself" in said and "Mentat's answer" not in said, (failure, said)
    assert logged and "(mentat:" in logged[0]
    assert make(ESCAPE)[0].run({}) == "ask_mentat needs his question, in his words." and make(ESCAPE)[0].run(None).startswith("ask_mentat needs")


def test_the_address_is_the_tailnet_one_unless_the_environment_says_otherwise():
    assert make(ESCAPE)[0].url == "http://skippy-core:7350"
    assert make(ESCAPE, env={"MENTAT_URL": "https://mentat.example:8443/"})[0].url == "https://mentat.example:8443"
    assert make(ESCAPE, env={"MENTAT_URL": "https://a", "SKIPPY_MENTAT_URL": "http://b:1"})[0].url == "http://b:1"


def test_text_is_cleaned_to_plain_words():
    assert physics.clean("A **bold** claim [3] at https://x.example/y and `more`.\nSources:\n[1] z") == "A bold claim at and more ."
    assert physics.clean("escape_velocity(body_name='earth')") == "escape_velocity(body_name='earth')"          # a name keeps its underscores
    assert physics.clean("One. Two is longer. Three is the longest of all of them here.", 30) == "One. Two is longer."
    assert physics.clean("word " * 100, 20) == "word word word word"
    assert physics.source_words("scipy.constants (CODATA: speed of light in vacuum)") == "scipy.constants (CODATA: speed of light in vacuum)"
    assert physics.source_words("sigma-ground (classical escape velocity); Earth's GM from JPL") == "sigma-ground (classical escape velocity)"


def test_plain_physics_and_unit_questions_are_routed_to_mentat():
    r = physics.pre_route
    assert r("What is the escape velocity of Mars?") == {"question": "What is the escape velocity of Mars?"}
    assert r("Skippy, what's the speed of light") == {"question": "what's the speed of light"}
    for asked in ("what is the molar mass of caffeine", "what's the boiling point of nitrogen", "how long does light take to get from the sun to the earth",
                  "how far is the moon", "how hot is the sun", "what is the mass of jupiter", "what's the gravity on mars", "what is the kinetic energy of a 2 kilogram ball at 3 meters per second",
                  "how many ounces are in a cup", "how many minutes in a day", "convert 5 miles to kilometers", "what is 20 pounds in kilograms", "how much is a gallon in liters",
                  "how many seconds are in a year", "what's the half-life of carbon 14", "what is the schwarzschild radius of the sun"):
        assert r(asked) == {"question": asked}, asked


def test_science_phrasings_the_first_battery_missed_are_routed_too():
    # 2026-11-04, the real Core: these were answered from memory (a 70 kg person "weighs 114 pounds on the Moon"; 130 megajoules; 7.9 km/s)
    for asked in ("What would a seventy kilogram person weigh on the Moon?", "How fast do I have to go to orbit the Earth?", "how high is low earth orbit",
                  "How much energy is in a gallon of gasoline?", "what is the energy in a kilogram of uranium", "how much would I weigh on Mars"):
        assert physics.pre_route(asked) == {"question": asked}, asked
    for other in ("how much energy does the house use", "what's the energy rating of the fridge", "how much do I weigh", "how long will the dryer run"):
        assert physics.pre_route(other) is None, other


def test_a_figure_with_no_mentat_call_is_told_from_a_figure_that_is_chatter():
    for said in ("The escape velocity of Mars is 5.03 km/s.", "[Mentat] The escape velocity of Mars is 5.03 km/s. Source: NASA Mars Fact Sheet.", "It's roughly 130 megajoules, per Mentat.",
                 "You need about 17,500 miles per hour to orbit.", "Seven point nine kilometers per second? Try 7.9 kilometers per second.", "Earth's mass is 5.97 times 10 to the 24 kilograms.",
                 "Water boils at 373 kelvin.", "Mentat says it is 8 fluid ounces.",
                 # the way the voice says them (the real Core, 2026-11-04: it spells numbers out for the speaker)
                 "Escape velocity from the Martian surface is about five point zero kilometers per second. That is roughly eleven thousand two hundred miles per hour.",
                 "It takes four hundred ninety-nine seconds? No: it is three hundred thousand kilometers per second.", "About one hundred thirty megajoules.",
                 "Seven point nine kilometers per second, Captain.", "One mole of anything has six point zero two times ten to the twenty-three atoms, that is a lot of electron volts."):
        assert physics.unbacked(said), said
    for said in ("A 60 watt bulb is fine.", "It is 7 PM, monkey.", "Timer set for 10 minutes.", "Fifty-seven percent, about 205 miles of range.", "The Eiffel Tower is 330 meters tall.",
                 "Mentat is the physics engine I ask, when I am allowed to.", "Paris. Try to keep up.", "Rayleigh scattering: blue light scatters more.",
                 "Fifty-seven percent, about two hundred and five miles of range.", "Ten minutes on the clock, monkey.", "It is seventy-two degrees in here.",
                 "Two hundred twenty volts, but who is counting.", "Twenty five days until Christmas."):
        assert not physics.unbacked(said), said


def asked_from_memory(first, *later):
    """A brain whose physics question was not routed (the words match no route) and whose model answers `first` from memory with no tool; `later` are the rounds after that."""
    p, sent = make(ESCAPE)
    client = Client([text(first)], *later)
    b = Brain(client, "m", Timers(), persona="P", physics=p)
    b.sleep = lambda s: None
    return b, client, sent


def test_a_physics_figure_from_memory_makes_the_house_ask_mentat_and_core_only_words_the_answer():
    heard = "quick, how quickly must a rocket move to get free of Mars?"
    assert physics.pre_route(heard) is None                                    # not routed: the model answered it, from memory, and called no tool
    b, c, sent = asked_from_memory("[Mentat] The escape velocity of Mars is 5.03 km/s. Source: NASA Mars Fact Sheet.", [text("Mentat says 11186 meters per second, from sigma-ground.")])
    assert b.reply(heard) == "Mentat says 11186 meters per second, from sigma-ground."
    assert [a["tool"] for a in b.actions] == ["ask_mentat"] and b.guard == ("force", "physics")
    assert sent[0]["body"] == {"question": heard}                              # his words as they were said, not the model's version of them
    assert "tools" not in c.calls[1] and "Mentat's answer: 11186 m/s." in c.calls[1]["messages"][-1]["content"] and heard in c.calls[1]["messages"][-1]["content"]
    assert [m["content"] for m in b.history[-2:]] == [heard, "Mentat says 11186 meters per second, from sigma-ground."]       # the memory keeps what was said aloud


def test_in_spoken_numbers_too_and_a_dead_mentat_is_said_instead_of_the_figure_from_memory():
    heard = "so how fast is the thing a rocket needs to leave Mars, roughly?"
    b, c, sent = asked_from_memory("Escape velocity from Mars is about five point zero kilometers per second, roughly eleven thousand miles per hour.",
                                   [text("Mentat is not answering, so I will not guess.")])
    b.physics.post = lambda url, body, headers=None, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("refused"))
    assert b.reply(heard) == "Mentat is not answering, so I will not guess." and "is not answering right now (URLError)" in c.calls[1]["messages"][-1]["content"]
    b, c, sent = asked_from_memory("About 5.03 km/s, Captain.")                  # and the words never came: the plain apology, not the figure
    assert b.reply("quick, how quickly must a rocket move to get free of Mars?").startswith("Mentat answered, and then my brain wandered off") and "5.03" not in b.history[-1]["content"]


def test_a_tool_call_written_as_words_where_no_tool_is_on_offer_is_never_spoken():
    # 2026-11-04, the real Core: handed a Mentat answer that missed, the routed wording call (no tools) wrote "[ask_mentat: ...]" as its whole reply
    p, sent = make(ESCAPE)
    client = Client([text("[ask_mentat: What is the escape velocity of Mars?]")])
    b = Brain(client, "m", Timers(), persona="P", physics=p)
    assert b.reply("What is the escape velocity of Mars?") == "Mentat answered, and then my brain wandered off. Ask me again."
    b = Brain(Client([text("Mentat missed. [ask_mentat question=\"x\"] Ask me in other words.")]), "m", Timers(), persona="P", physics=p)
    assert b.reply("What is the escape velocity of Mars?") == "Mentat missed.  Ask me in other words." and "[" not in b.history[-1]["content"]
    c, _ = make_car(reader())
    b = Brain(Client([text("[car_status about=charge]")]), "m", Timers(), persona="P", car=c)
    assert b.reply("what is my car's charge level") == "I looked at the car, and then my brain wandered off. Ask me again."


def test_a_figure_in_a_poem_a_joke_or_a_statement_is_left_alone():
    for heard, said in (("write me a limerick about the sun", "A star ten thousand light years away, shone bright on a cold winter day."),
                        ("tell me a joke about space", "Why did the astronaut break up? He needed 3 light years of space."),
                        ("the answer is five", "Five. Four kelvin, even. Fine.")):
        b, c, sent = asked_from_memory(said)
        assert b.reply(heard) == said and len(c.calls) == 1 and not sent and b.guard == (None, None), heard


def test_a_figure_after_any_other_tool_is_left_alone():
    p, _ = make(ESCAPE)
    client = Client([tool("list_timers")], [text("No timers, and the light travels at 299792458 m/s anyway.")])
    b = Brain(client, "m", Timers(), persona="P", physics=p)
    assert b.reply("any timers, and how fast is the thing that is not routed") == "No timers, and the light travels at 299792458 m/s anyway." and len(client.calls) == 2


def test_other_questions_are_left_to_the_model_with_the_tool_and_the_rule():
    r = physics.pre_route
    for other in ("what time is it", "how many days until Christmas", "how many people are in the room", "how long does it take to cook rice", "set a timer for five minutes",
                  "what's the weather", "play the song escape velocity", "tell me a joke about physics", "news about the speed of light", "what is the capital of France",
                  "turn off the light", "how long does the light stay on", "how many cups of flour for a cake", "remind me to buy milk in two hours", "who won the world series"):
        assert r(other) is None, other


def test_a_physics_question_runs_mentat_before_the_model_answers_and_the_model_only_words_it():
    p, sent = make(ESCAPE)
    seen = []

    class Core:
        class messages:
            @staticmethod
            def create(**kw):
                seen.append(kw)
                return NS(content=[text("Mentat says about eleven thousand one hundred eighty-six meters per second, from sigma-ground. Don't jump.")])
    b = Brain(Core, "m", Timers(), persona="P", physics=p)
    b.room = "living"
    assert b.reply("What is the escape velocity of the Earth?").startswith("Mentat says about eleven thousand")
    assert [a["tool"] for a in b.actions] == ["ask_mentat"] and b.actions[0]["args"] == {"question": "What is the escape velocity of the Earth?"}
    assert "Mentat's answer: 11186 m/s." in seen[0]["messages"][-1]["content"] and "tools" not in seen[0]
    assert sent[0]["headers"]["X-Skippy-Trace"].startswith("house-living-")
    system = seen[0]["system"]
    assert "ask_mentat" in system and "you never are" in system and "physics calculations (Mentat)" not in system.split("Out of your reach from here:")[1].split("The ")[0]


def test_the_model_can_call_mentat_itself_and_a_yes_to_a_suggestion_goes_through_the_tool():
    p, sent = make(NEEDS_MASS, ESCAPE)
    client = Client([tool("ask_mentat", question="What is the kinetic energy of a car going 60 miles per hour?")], [text("Mentat needs a mass; it suggests 1500 kilograms. Use that?")],
                    [tool("ask_mentat", question="yes", use_suggestion=True)], [text("About five hundred forty thousand joules, per Mentat.")])
    b = Brain(client, "m", Timers(), persona="P", physics=p)
    b.room = "kitchen"
    assert b.reply("how much energy does a car at sixty miles an hour have, as a feeling").startswith("Mentat needs a mass")          # not a routed phrase: the model chose the tool
    assert b.reply("yes") == "About five hundred forty thousand joules, per Mentat."
    assert sent[1]["body"]["given"] == {"mass_kg": 1500} and "ask_mentat" in {t["name"] for t in client.calls[0]["tools"]}


def test_the_prompt_names_only_what_is_still_out_of_reach_and_the_tool_list_is_the_real_one():
    c, _ = make_car(reader())
    p, _ = make(ESCAPE)
    both = Brain(Client([text("ok")]), "m", Timers(), persona="P", car=c, physics=p)
    system = both.system()
    assert "Out of your reach from here: his files and code. The private Skippy on makerportal.live writes code." in system
    assert "car_status" in system.split("ONLY through these tools:")[1].split(".")[0] and "ask_mentat" in system.split("ONLY through these tools:")[1].split(".")[0]
    none = Brain(Client([text("ok")]), "m", Timers(), persona="P").system()
    assert "Out of your reach from here: his car, physics calculations (Mentat), his files and code. The Skippy chat on makerportal.live has the car and Mentat, and the private" in none
    only_mentat = Brain(Client([text("ok")]), "m", Timers(), persona="P", physics=p).system()
    assert "Out of your reach from here: his car, his files and code. The Skippy chat on makerportal.live has the car, and the private Skippy there writes code." in only_mentat
