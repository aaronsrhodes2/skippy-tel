"""The fourth round of the house speed change, the small fixes (2026-11-06; fix4:small): the follow-ups of the third review that were nobody's larger piece.

core3#8  "set the temperature to 45" and "set the temperature to seventy two" were answered with the FORECAST (weather.pre_route matches the word "temperature"), "make the thermostat 70"
         with a READ of the thermostat (ASK_THERMO matches "thermostat"): the house was not set. house.thermostat_route now understands a plain setting said about the temperature, the house or "it"
         ("set the house temperature to 68", "make it 70 degrees in here", "make the thermostat 70"), the degrees may be words ("seventy two"), a water heater or a space heater is no thermostat, and
         weather.pre_route leaves a COMMAND or a plain STATEMENT about a temperature ("set the oven temperature to 350", "the water temperature is 60") to the model. A weather question stays the weather's.
core3#9  the persona file (shared with flask-api) teaches the model to end a reply with [SKIPPY_LEARN: {...}] and [TTS_ADJUST: {...}], which only flask-api and the portal chat consume: in the house
         6 of 18 provoked replies were nothing else and the Captain heard "Hm. I had nothing to say, which is unlike me." brain.house_persona now leaves out every "## " section that names one.

Every test here that can fail on the code before the fix does; the numbers are in the report. The table below is routed through the REAL Brain (every pre-route in its real order: the car, Mentat,
the thermostat, the lights, the timers, the weather, the lists ...) with stubs for what the routes would call, so a neighbour that steals or loses a phrase shows."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain as brain_mod  # noqa: E402
import house as house_mod  # noqa: E402
import weather as weather_mod  # noqa: E402
from timers import Timers  # noqa: E402


# ── the whole route chain, with stubs ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class NoRoute(BaseException):
    """Raised by the model stub when a reply reaches the model BEFORE any route ran: the phrase has no route and the model would decide. (Not an Exception: nothing in the brain may swallow it.)"""


class Model:
    def __init__(self, seen):
        self.messages, self.seen = self, seen

    def create(self, **kw):
        if not self.seen:
            raise NoRoute()
        return NS(content=[NS(type="text", text="Fine.")], stop_reason="end_turn")


class House(house_mod.House):
    def states(self, fresh=False):
        return [{"entity_id": "climate.thermostat", "state": "heat", "attributes": {"friendly_name": "Thermostat", "current_temperature": 70, "temperature": 68}},
                {"entity_id": "light.bedroom", "state": "off", "attributes": {"friendly_name": "Bedroom"}}]

    def areas(self):
        return {}

    def _service(self, domain, service, data):
        pass


class Stub:
    def tools(self):
        return []

    def run(self, args):
        return "ok"


def route_of(heard):
    """(tool, args) of the route the brain takes for `heard` before the model, or None when the model would decide."""
    seen = []
    clock = [1_793_000_000.0]
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("list", "set_alarm", "announce", "speakers", "music", "play_music")]
    b = brain_mod.Brain(Model(seen), "m", Timers(clock=lambda: clock[0]), persona="P", clock=lambda: clock[0], now=lambda: "Friday, November 06, 2026, 09:00", hass=House(),
                        weather=Stub(), car=Stub(), physics=Stub(), extra=(tools, lambda n, a: "ok"))
    b.sleep = lambda s: None

    def run_tool(tool, args):
        seen.append((tool, dict(args)))
        return "ok"
    b.run_tool = run_tool
    try:
        b.reply(heard)
    except NoRoute:
        return None
    return seen[0] if seen else None


def SET(n):
    return "home_control", {"name": "thermostat", "action": "set_temperature", "value": n}


OFF = "home_control", {"name": "thermostat", "action": "off"}
HEAT = "home_control", {"name": "thermostat", "action": "heat"}
READ = "home_state", {"name": "thermostat"}


def WX(when="today", coast=False):
    return "weather", ({"when": when, "coast": True} if coast else {"when": when})


# ── the table: 40 thermostat commands and questions, 40 weather questions and unrelated phrases ────────────────────────────────────────────────────────────────────────────────────────────
THERMOSTAT = [
    # the settings that worked before
    ("set the thermostat to 70", SET(70)), ("Skippy, set the thermostat to 72 degrees", SET(72)), ("turn the heat up to 68 please", SET(68)), ("put the thermostat at 68", SET(68)),
    ("raise the thermostat to 75", SET(75)), ("set the AC to 72", SET(72)),
    # the degrees in words
    ("set the thermostat to seventy two", SET(72)), ("turn the heat up to sixty eight", SET(68)),
    # "make the thermostat 70": a read of it, before
    ("make the thermostat 70", SET(70)), ("make the thermostat 68 degrees", SET(68)), ("set the thermostat 70", SET(70)),
    # the TEMPERATURE: the forecast, before
    ("set the temperature to 72", SET(72)), ("set the temperature to 45", SET(45)), ("set the temperature to seventy two", SET(72)), ("set the temperature to seventy-two", SET(72)),
    ("change the temperature to 68", SET(68)), ("put the temperature at 70", SET(70)), ("make the temperature 70", SET(70)), ("can you set the temperature to 70", SET(70)),
    ("bring the temperature down to 68", SET(68)), ("turn the temperature down to 65", SET(65)), ("keep the temperature at 70", SET(70)),
    # the house, "it", in here
    ("set the house temperature to 68", SET(68)), ("set the house to 70 degrees", SET(70)), ("set the temperature in here to 70", SET(70)), ("make it 70 degrees in here", SET(70)),
    ("make it 68 in here", SET(68)), ("it's too cold, make it 72 in here", SET(72)),
    # off and on
    ("turn the heat off", OFF), ("turn off the heater", OFF), ("turn off the AC", OFF), ("turn on the furnace", HEAT),
    # the questions about the house
    ("what is the thermostat set to", READ), ("how warm is it in here", READ), ("how cold is it inside", READ), ("what is the temperature inside", READ), ("what's the temp at home", READ),
    ("is the heat on", READ), ("what's the house temperature", READ), ("what is the indoor temperature", READ),
]
OTHER = [
    # weather questions: the weather tool, never the thermostat
    ("what's the temperature outside", WX()), ("what's the temperature tomorrow", WX("tomorrow")), ("how cold is it", WX()), ("how hot is it", WX()),
    ("will the temperature drop tonight", WX("tonight")), ("what's the weather like", WX()), ("do I need an umbrella tomorrow", WX("tomorrow")), ("is it going to rain this weekend", WX("week")),
    ("what's the forecast for tomorrow", WX("tomorrow")), ("how's the surf", WX(coast=True)), ("what is the temperature going to be this week", WX("week")), ("what's the temperature", WX()),
    ("how warm is it outside", WX()), ("what is the temperature outside right now", WX()), ("will the temperature go above 90 tomorrow", WX("tomorrow")),
    ("will it make the temperature drop to 40 tonight", WX("tonight")), ("what will the temperature be at 6 tonight", WX("tonight")), ("what's the high temperature tomorrow", WX("tomorrow")),
    ("the temperature is 40 outside, do I need a jacket", WX()), ("what is the water temperature at the beach", WX(coast=True)),
    # not the thermostat and not the weather: the model's (None), or another route's own
    ("set the oven temperature to 350", None), ("preheat the oven to 400", None), ("the water temperature is 60", None), ("set the pool temperature to 82", None),
    ("set the water heater to 60", None), ("turn off the water heater", None), ("set the temperature to 350", None), ("should I set the temperature to 68 or 70", None),
    ("set the temperature to 68 at 10 pm", None), ("who set the temperature to 80", None), ("turn the temperature up 10 degrees", None), ("set the temperature to 72 and turn off the lights", None),
    ("make it 70 degrees", None), ("make it 50 in here", None), ("keep the temperature at 70 while I'm away", None),
    ("set a timer for 45 minutes", ("set_timer", {"seconds": 2700})), ("set the volume to 50", ("music", {"action": "volume", "level": 50})),
    ("what is the temperature of the sun", ("ask_mentat", {"question": "what is the temperature of the sun"})), ("how warm is it in the car", ("car_status", {"about": "climate"})),
    ("put the roast in at 350", None),
]
TABLE = THERMOSTAT + OTHER


def test_fix4_the_table_has_80_phrases_40_and_40_and_no_phrase_twice():
    assert len(THERMOSTAT) == 40 and len(OTHER) == 40 and len({h for h, _ in TABLE}) == 80


@pytest.mark.parametrize("heard, want", TABLE, ids=[h for h, _ in TABLE])
def test_fix4_core3_8_the_phrase_takes_the_route_it_should(heard, want):
    assert route_of(heard) == want


def test_fix4_core3_8_the_table_counts_80_of_80_route_right():
    wrong = [(h, route_of(h), want) for h, want in TABLE if route_of(h) != want]
    assert not wrong, f"{len(TABLE) - len(wrong)} of {len(TABLE)} route right; wrong: {wrong}"


# ── the routes by themselves: every weather question stays the weather's ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
WEATHER_QUESTIONS = [
    "what's the weather like", "what's the weather going to be tomorrow", "is it going to rain", "do I need an umbrella", "do I need a jacket tonight", "what should I wear today",
    "how's the surf today", "when is low tide tomorrow", "what's the forecast for the weekend", "how cold is it", "how hot is it", "how warm is it outside", "how warm is it out there today",
    "what's the temperature", "what's the temperature outside", "what is the temperature outside right now", "what's the temperature tomorrow", "what will the temperature be tonight",
    "what is the temperature going to be this week", "will the temperature drop tonight", "will the temperature go above 90 tomorrow", "is the temperature going to reach 100 today",
    "will it make the temperature drop to 40 tonight", "what's the high temperature tomorrow", "what's the low temperature tonight", "what temperature will it be at 5 pm tomorrow",
    "what will the temperature be at 6 tonight", "how's the temperature at the beach", "the temperature is 40 outside, do I need a jacket", "temperature is 30 degrees, should I wear a coat",
    "will the cold front change the temperature this week", "is the temperature changing tomorrow", "what's the temperature going to be like on the weekend", "tell me the temperature outside",
    "keep me posted on the temperature tomorrow", "will the clouds keep the temperature up tonight", "is the temperature going to drop to freezing tonight",
    "will the front bring the temperature down tonight", "will it turn cold, what temperature", "is the sun going to raise the temperature this afternoon", "how much will the temperature lower tonight",
]


@pytest.mark.parametrize("question", WEATHER_QUESTIONS)
def test_fix4_core3_8_a_weather_question_is_never_stolen_by_the_thermostat_and_never_refused_by_the_weather(question):
    assert house_mod.thermostat_route(question) is None, question
    assert weather_mod.pre_route(question) is not None, question


# What stays with the model: a setting that is not a plain one (the oven, a change, a choice, a condition, a time, a second command), for both routes.
THE_MODELS = [
    "set the oven temperature to 350", "set the pool temperature to 82", "set the water heater temperature to 120", "set the fridge temperature to 38", "set the nursery temperature to 72",
    "set the bedroom temperature to 70", "set the temperature in the bedroom to 70", "set the temperature of the pool to 82", "set the temperature for the oven to 350",
    "set the temperature to 100", "set the temperature to 350", "set the temperature to 5", "set the temperature to 3 hundred",
    "turn the temperature up", "turn the temperature down", "turn the temperature up 10 degrees", "make the temperature 10 degrees warmer", "make the house 10 degrees warmer",
    "make it 10 degrees warmer in here", "raise the temperature a few degrees",
    "set the temperature to 68 or 70", "set the temperature to sixty eight or seventy", "should I set the temperature to 68 or 70", "which is better, set the temperature to 68 or 70",
    "who set the temperature to 80", "why did you set the temperature to 80", "if it gets cold set the temperature to 70", "when I get home set the temperature to 70",
    "set the temperature at 10 pm to 68", "set the temperature to 68 at 10 pm", "set the temperature to 68 at 10:30", "set the temperature to 70 at six o'clock",
    "set the temperature to 72 and turn off the lights", "set the temperature to 72 and play some jazz", "set the temperature to 72, then play some jazz", "make it 70 in here and turn on the fan",
    "keep the temperature at 70 while I'm away", "make it 70 degrees", "set it to 70", "make it 50 in here", "make it 30 in here", "make it 90 degrees in here", "set the volume to 50 in here",
    "the water temperature is 60", "the temperature is 72 in here", "the temperature says 60 on my phone", "the temperature was 85 yesterday", "set a reminder to check the temperature at 5 pm",
    "set the water heater to 60", "set the space heater to 70", "turn off the water heater", "turn on the water heater", "turn off the space heater",
]


@pytest.mark.parametrize("heard", THE_MODELS)
def test_fix4_core3_8_what_is_not_a_plain_setting_is_the_models_neither_the_thermostat_nor_the_forecast_answers_it(heard):
    assert house_mod.thermostat_route(heard) is None, heard
    assert weather_mod.pre_route(heard) is None, heard


# ── the degrees ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
ONES = "one two three four five six seven eight nine".split()
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def number_words(n, joiner=" "):
    tens, ones = divmod(n, 10)
    word = [w for w, v in TENS.items() if v == tens * 10][0]
    return word + (joiner + ONES[ones - 1] if ones else "")


@pytest.mark.parametrize("n", range(20, 100))
def test_fix4_core3_8_every_two_digit_setting_is_understood_in_digits_and_in_words(n):
    for said in (str(n), number_words(n), number_words(n, "-")):
        for sentence in (f"set the thermostat to {said}", f"set the temperature to {said}", f"set the house temperature to {said} degrees", f"make the thermostat {said}", f"turn the heat up to {said}",
                         f"set the heat to {said}", f"change the temperature to {said} please"):
            assert house_mod.thermostat_route(sentence) == SET(n), sentence


@pytest.mark.parametrize("n", range(10, 100))
def test_fix4_core3_8_it_is_a_room_temperature_only_between_55_and_85_never_a_volume(n):
    got = house_mod.thermostat_route(f"make it {n} in here")
    assert got == (SET(n) if 55 <= n <= 85 else None), n


@pytest.mark.parametrize("sentence", ["set the temperature to 100", "set the thermostat to 350", "set the heat to 1000", "set the temperature to 7", "set the temperature to 007", "set the AC to 3 hundred",
                                      "make the thermostat 100", "set the temperature to hundred"])
def test_fix4_core3_8_three_digits_one_digit_and_no_digits_are_no_setting(sentence):
    got = house_mod.thermostat_route(sentence)
    assert got is None or got[1].get("action") != "set_temperature", sentence


@pytest.mark.parametrize("sentence", ["set the temperature to seventy two or seventy five", "set the temperature to seventy-two or 75", "set the temperature to sixty eight or seventy",
                                      "set the temperature to 68 or 70", "set the house to seventy two or seventy five", "make the thermostat seventy two or seventy five"])
def test_fix4_core3_8_a_choice_is_not_a_setting_and_the_ones_do_not_back_off_to_the_tens(sentence):
    """The words are possessive: "seventy two" is 72 or it is nothing, never 70 with "two" left over for the guard to miss."""
    assert house_mod.thermostat_route(sentence) in (None, READ), sentence


def test_fix4_core3_8_degrees_reads_what_degrees_matched():
    assert [house_mod.degrees(x) for x in ("72", "seventy two", "Seventy-Two", "SEVENTY", "twenty", "ninety nine", "03")] == [72, 72, 72, 70, 20, 99, 3]


def test_fix4_core3_8_a_water_heater_or_a_space_heater_is_not_the_thermostat_a_heater_is():
    for no in ("set the water heater to 60", "set the water heater to sixty", "set the space heater to 70", "set the Space Heater to 70", "turn off the water heater", "turn off the space heater"):
        assert house_mod.thermostat_route(no) is None, no
    for yes in ("set the heater to 70", "set the heating to 68", "set the heat to 66", "set the furnace to 68", "turn the heater off"):
        assert house_mod.thermostat_route(yes) is not None, yes


def test_fix4_core3_8_the_old_settings_are_exactly_as_they_were_for_every_phrase_the_old_pattern_took():
    """SET_THERMO is unchanged but for the words: every phrase the committed pattern took still gives the same value."""
    for sentence, n in (("set the thermostat to 70", 70), ("turn the heat up to 68 please", 68), ("the thermostat at 72", 72), ("put the AC at 74", 74), ("change the air conditioning to 73", 73),
                        ("turn the heater to 71", 71), ("bump the heat to 70", 70), ("I want the thermostat at 70", 70)):
        assert house_mod.thermostat_route(sentence) == SET(n), sentence


def test_fix4_core3_8_the_questions_about_the_house_are_reads_and_a_setting_beats_a_question_word():
    for ask in ("what's the house temperature", "what is the indoor temperature", "tell me the house temperature", "check the indoor temperature", "what's the temperature indoors",
                "how warm is it in here", "what is the temperature inside"):
        assert house_mod.thermostat_route(ask) == READ, ask
    assert house_mod.thermostat_route("set the house temperature to 68") == SET(68)         # ("house temperature" is also a question's words: the setting is looked at first)
    for out in ("what's the temperature outside", "what's the outside temperature", "how cold is it", "how hot is it out", "what's the temperature tomorrow"):
        assert house_mod.thermostat_route(out) is None, out


def test_fix4_core3_8_no_long_or_odd_utterance_makes_the_routes_slow():
    """The routes run on every turn, before the model: the new patterns (lazy gaps, optional words) must not backtrack badly on a long run of the same words (measured: 22 ms at worst for 1.4 kB)."""
    import time
    texts = ["set the temperature to " * 60, "set the thermostat " * 60, "make it " * 120, "set, the, temperature, " * 60, "what " * 150 + "house temperature", "turn the " * 100 + "temperature",
             "set the temperature to 68 or 70 " * 40, "will the temperature " * 80 + "drop tonight", "set a reminder " * 60 + "temperature at 5", "set the temperature to " + "seventy " * 120]
    t0 = time.perf_counter()
    for text in texts:
        house_mod.thermostat_route(text)
        weather_mod.pre_route(text)
    assert time.perf_counter() - t0 < 2.0


def test_fix4_core3_8_the_weather_regex_without_temperature_is_derived_from_the_weather_regex():
    """OTHER_WEATHER is WEATHER_RE minus the bare word, built from it, so a word added to one is in the other."""
    assert "temperature" not in weather_mod.OTHER_WEATHER.pattern and weather_mod.OTHER_WEATHER.pattern != weather_mod.WEATHER_RE.pattern
    for word in ("weather", "forecast", "rain", "umbrella", "jacket", "coat", "fog", "sunny", "storm", "snow", "how cold is it", "tides", "surf", "beach", "sunscreen", "what should i wear"):
        assert weather_mod.OTHER_WEATHER.search(word) and weather_mod.WEATHER_RE.search(word), word
    assert weather_mod.WEATHER_RE.search("temperature") and not weather_mod.OTHER_WEATHER.search("the temperature")


def test_fix4_core3_8_a_statement_with_another_weather_word_is_still_a_weather_question():
    assert weather_mod.pre_route("the temperature is 40 outside, do I need a jacket") == {"when": "today"}
    assert weather_mod.pre_route("the water temperature is 60") is None and weather_mod.pre_route("the temperature reads 72") is None
    assert weather_mod.pre_route("the temperature is 40 and it is going to rain tomorrow, what should I wear") == {"when": "tomorrow"}


# ── core3#9: the persona the house voice is taught from ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
RAW = brain_mod.PERSONA_FILE.read_text(encoding="utf-8")
CUT = ("Keyword Memory", "TTS Voice Adjustment")                                    # the sections of the file that teach the two annotations, by heading (not by line)
ALLOWED_TAGS = {"IMAGE"}                                                           # bracketed tags the house persona may still teach: [IMAGE: ...] is stripped by strip_model_markup, nothing keeps it
TAG = re.compile(r"\[([A-Z][A-Z0-9_]{2,}):")


def sections(text):
    """(title or None for the lead, whole text of the part) for each "## " section of a persona text, in order: joining the parts gives the text back."""
    parts = re.split(r"(?m)^(?=## )", text)
    return [((re.match(r"## (.*)", p).group(1).strip() if p.startswith("## ") else None), p) for p in parts]


def old_house_persona(text):
    """What house_persona did before this round: it dropped only the examples whose answer says "I cannot ..." (the committed behaviour, kept)."""
    return brain_mod._EXAMPLE.sub(lambda m: "" if brain_mod._SAYS_NO.search(m.group(0)) else m.group(0), text)


def test_fix4_core3_9_the_real_persona_file_has_the_two_annotation_sections_and_the_house_persona_has_neither_tag_left():
    assert "SKIPPY_LEARN" in RAW and "TTS_ADJUST" in RAW and [t for t, _ in sections(RAW) if t in CUT] == list(CUT)       # (the premise: the file teaches both)
    house = brain_mod.load_persona()
    assert "SKIPPY_LEARN" not in house and "TTS_ADJUST" not in house and "{KEYWORD_MEMORY}" not in house
    assert not re.search(r"skippy_learn|tts_adjust", house, re.I) and house == brain_mod.house_persona(RAW)


def test_fix4_core3_9_everything_outside_the_two_cut_sections_is_byte_for_byte_what_it_was():
    kept = "".join(p for t, p in sections(RAW) if t not in CUT)
    assert brain_mod.house_persona(RAW) == old_house_persona(kept)
    assert brain_mod.load_persona() == old_house_persona(kept)
    cut = [t for t, p in sections(RAW) if t is not None and t not in [u for u, _ in sections(brain_mod.house_persona(RAW))]]
    assert cut == list(CUT), f"the house persona lost {cut}, not exactly {list(CUT)}: the cut is over- or under-reaching"


def test_fix4_core3_9_the_nickname_example_that_sits_at_the_end_of_the_voice_section_goes_with_it():
    assert "beercan" in RAW and "beercan" not in brain_mod.house_persona(RAW)             # (the example is in SKIPPY_LEARN, under "TTS Voice Adjustment", after a rule: no heading of its own)
    assert "Example Responses" in brain_mod.house_persona(RAW) and "Character Doctrine" in brain_mod.house_persona(RAW)


def test_fix4_core3_9_a_persona_without_those_sections_is_unchanged_but_for_the_examples_it_always_dropped():
    text = ("# Skippy\nintro line\n\n## Core\nbe magnificent.\n\n### Sub\nmore\n\n## Rules\nbe brief.\n\n## Example Responses\n\nQ: \"What time is it?\"\nA: Late.\n\n"
            "Q: \"Can you set a timer?\"\nA: I cannot set timers from here.\n\nQ: \"Who are you?\"\nA: Skippy.\n")
    assert brain_mod.house_persona(text) == old_house_persona(text) and "I cannot" not in brain_mod.house_persona(text) and "Late." in brain_mod.house_persona(text)
    assert brain_mod.house_persona("no headings at all, and no tags") == "no headings at all, and no tags"
    assert brain_mod.house_persona("") == ""
    assert brain_mod.house_persona(brain_mod.house_persona(RAW)) == brain_mod.house_persona(RAW)           # (a fixed point)


def test_fix4_core3_9_a_section_that_names_either_tag_is_cut_whatever_it_is_called_and_the_lead_is_kept():
    text = "# Skippy\n\n## Core\nbe magnificent.\n\n## Anything At All\nEnd with [skippy_learn: {}] always.\n\n## Voice\nuse `[TTS_ADJUST: {}]` rarely\n\n### Deeper\nstill that section\n\n## Last\nthe end\n"
    assert brain_mod.house_persona(text) == "# Skippy\n\n## Core\nbe magnificent.\n\n## Last\nthe end\n"
    assert brain_mod.house_persona("## Only\nSKIPPY_LEARN everywhere") == ""
    lead = "# Skippy\nEnd with [SKIPPY_LEARN: {}].\n\n## Core\nx\n"
    assert brain_mod.house_persona(lead) == lead                   # (a tag in the lead, before any heading, is no section: the title is not thrown away; the guard below fails loudly on it instead)


def test_fix4_core3_9_the_house_persona_teaches_no_annotation_a_future_edit_of_the_file_adds_that_the_cut_misses():
    """FAILS LOUDLY when skippy_persona.md gains a new bracketed annotation ("[SKIPPY_NOTE: {...}]") that house_persona does not cut: the house has no consumer for it, strips it, and the Captain
    hears "Hm. I had nothing to say" when it is the whole reply (core3#9). Cut its section in brain.house_persona (name its tag in _ANNOTATED), or allow it in ALLOWED_TAGS on purpose."""
    left = set(TAG.findall(brain_mod.load_persona()))
    assert left <= ALLOWED_TAGS, (f"the persona now teaches the house voice {sorted(left - ALLOWED_TAGS)}: nothing in the house reads it. Add it to brain._ANNOTATED so its section is cut for the house voice "
                                  f"(flask-api keeps the file), or allow it in ALLOWED_TAGS here on purpose.")


def test_fix4_core3_9_the_guard_itself_sees_a_new_annotation_paragraph_the_cut_would_miss():
    future = RAW + "\n## Remember Things\nEnd a reply with [SKIPPY_NOTE: {\"note\": \"...\"}] to keep a note.\n"
    assert set(TAG.findall(brain_mod.house_persona(future))) - ALLOWED_TAGS == {"SKIPPY_NOTE"}                       # a tag the cut does not know: the guard above would fail
    assert set(TAG.findall(brain_mod.house_persona(RAW + "\n## More Memory\nuse [SKIPPY_LEARN: {}] again\n"))) <= ALLOWED_TAGS         # a new section naming a known tag is cut
    assert set(TAG.findall(RAW)) == {"SKIPPY_LEARN", "TTS_ADJUST", "IMAGE"}                                           # (what the whole file teaches today)
    assert set(TAG.findall(brain_mod.house_persona("# Skippy\nEnd with [SKIPPY_LEARN: {}].\n\n## Core\nx\n"))) == {"SKIPPY_LEARN"}      # (a tag in the lead is not cut, so the guard above sees it)


def test_fix4_core3_9_the_system_prompt_of_a_brain_has_none_of_it_and_is_the_same_text_every_time():
    b = brain_mod.Brain(Model([]), "m", Timers(), now=lambda: "x")
    system = b.system()
    assert "Keyword Memory" not in system and "{KEYWORD_MEMORY}" not in system and "TTS Voice Adjustment" not in system and "beercan" not in system
    assert system == b.system() and "Image Generation" in system and "Example Responses" in system
