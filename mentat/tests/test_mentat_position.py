"""Mentat's position rule in /ask (mentat/position_stage.py): "where is <body> at <time>" goes to body_position, nothing else does.

The tool itself is faked (a recorder); the real body_position is tested in sigma-ground (tests/test_where_when.py) and checked live by the
deploy. The 2026-10-29 miss that started this: "Where will Mars be on March 1, 2027, as seen from Earth?" reached Mars's fact sheet.
"""
import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

MENTAT = Path(__file__).resolve().parents[1]
NOW = datetime.datetime(2026, 10, 29, 18, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(scope="module")
def stage():
    spec = importlib.util.spec_from_file_location("position_stage", MENTAT / "position_stage.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["position_stage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("question, args", [
    ("Where will Mars be on March 1, 2027, as seen from Earth?", {"body_name": "mars", "when": "2027-03-01", "central_body": "earth"}),
    ("where is mars", {"body_name": "mars", "when": "now", "central_body": "earth"}),
    ("Where is Venus tonight?", {"body_name": "venus", "when": "now", "central_body": "earth"}),
    ("How far is Mars from the Sun on 2031-06-15?", {"body_name": "mars", "when": "2031-06-15", "central_body": "sun"}),
    ("What was the Moon's right ascension and declination on 20 July 1969?", {"body_name": "moon", "when": "1969-07-20", "central_body": "earth"}),
    ("Where was Jupiter on 7/4/1976?", {"body_name": "jupiter", "when": "1976-07-04", "central_body": "earth"}),
    ("Where will Saturn be in 3 years?", {"body_name": "saturn", "when": "2029-10-29T00:00:00Z", "central_body": "earth"}),
    ("Where is the Earth tomorrow?", {"body_name": "earth", "when": "2026-10-30", "central_body": "sun"}),
    ("Heliocentric position of Neptune at JD 2462000.5", {"body_name": "neptune", "when": "JD 2462000.5", "central_body": "sun"}),
    ("Where is the Sun relative to the barycentre right now?", {"body_name": "sun", "when": "now", "central_body": "ssb"}),
])
def test_position_questions_are_read(stage, question, args):
    got, why, _ = stage.read(question, NOW)
    if args["when"].endswith("Z"):            # "in 3 years" is a time of day too: compare the date
        assert got["when"][:10] == args["when"][:10] and {**got, "when": ""} == {**args, "when": ""}
    else:
        assert got == args, why


def test_a_year_alone_says_what_it_assumed(stage):
    got, _, assumed = stage.read("Where will Pluto be in 2040?", NOW)
    assert got["when"] == "2040-01-01" and "1 January" in assumed


@pytest.mark.parametrize("question", [
    "What is the mass of Mars?",
    "How far is Mars from the Sun?",                       # its orbit's size: the fact sheet's question, no time named
    "What is the escape velocity of Jupiter?",
    "How long does light take to reach Mars?",
    "Where was the Moon?",                                  # past tense, no date: never guessed
    "What is the distance between Mars and Jupiter on 2027-01-01?",   # two bodies: not this rule's
    "Where would a ball land if thrown at 20 m/s?",
])
def test_everything_else_falls_through_with_a_reason(stage, question):
    miss = {}
    assert stage.answer(question, call=lambda *a: pytest.fail("no tool call"), miss=miss, now=NOW) is None
    assert miss["position"]


def test_the_answer_is_the_tools_with_the_route_said(stage):
    calls = []

    def call(name, args):
        calls.append((name, args))
        return {"value": {"body": "Mars", "kind": "prediction"}, "units": "km", "source": "our own N-body integration", "provenance_tag": "DERIVED",
                "notes": "PREDICTION."}
    out = stage.answer("Where will Mars be on March 1, 2027?", call=call, now=NOW)
    assert calls == [("body_position", {"body_name": "mars", "when": "2027-03-01", "central_body": "earth"})]
    assert out["value"]["kind"] == "prediction" and out["error"] is None and out["interpreter"] == "position rule"
    assert out["inputs"]["route"] == "position" and out["interpreted_as"].startswith("body_position(")


def test_the_tools_refusal_is_the_answer(stage):
    out = stage.answer("Where will Mars be on 2300-01-01?", call=lambda n, a: {"value": None, "notes": "that date is outside it"}, now=NOW)
    assert out["value"] is None and "outside" in out["error"]


def test_a_broken_tool_falls_through(stage):
    miss = {}

    def boom(name, args):
        raise RuntimeError("kernel missing")
    assert stage.answer("where is mars", call=boom, miss=miss, now=NOW) is None and "kernel missing" in miss["position"]


# Small bodies (sigma-ground C30): the roster is sigma-ground's, faked here as the three names it would know; the 2026-11-03 miss that started this:
# "Where is asteroid 433 Eros on 2029-10-27T00:00:00 UTC? ... relative to the solar system barycenter" reached asteroid_analysis's surface gravity.
_ROSTER = {"433 eros": {"number": 433, "name": "Eros", "kind": "asteroid", "tier": "own_fit"}, "eros": {"number": 433, "name": "Eros", "kind": "asteroid", "tier": "own_fit"},
           "433": {"number": 433, "name": "Eros", "kind": "asteroid", "tier": "own_fit"}, "apophis": {"number": 99942, "name": "Apophis", "kind": "asteroid", "tier": "horizons"},
           "67p": {"number": None, "name": "67P/Churyumov-Gerasimenko", "kind": "comet", "tier": "catalogue"}}


@pytest.fixture
def roster(stage, monkeypatch):
    asked = []

    def fake(phrase):
        asked.append(phrase)
        return _ROSTER.get(phrase.lower())
    monkeypatch.setattr(stage, "small_body_lookup", fake)
    return asked


@pytest.mark.parametrize("question, args", [
    ("Where is asteroid 433 Eros on 2029-10-27T00:00:00 UTC? I need its position vector (X, Y, Z) in the ICRF/J2000 frame relative to the solar system barycenter.",
     {"body_name": "433 Eros", "when": "2029-10-27T00:00:00", "central_body": "ssb"}),
    ("where is eros right now", {"body_name": "433 Eros", "when": "now", "central_body": "earth"}),
    ("Where will Apophis be on April 13, 2029, as seen from Earth?", {"body_name": "99942 Apophis", "when": "2029-04-13", "central_body": "earth"}),
    ("Heliocentric position of comet 67P in 2028", {"body_name": "67P/Churyumov-Gerasimenko", "when": "2028-01-01", "central_body": "sun"}),
])
def test_small_bodies_the_roster_knows_are_read(stage, roster, question, args):
    got, why, _ = stage.read(question, NOW)
    assert got == args, why


def test_a_small_body_the_roster_does_not_know_falls_through(stage, roster):
    miss = {}
    assert stage.answer("Where is asteroid Bennu on 2029-10-27?", call=lambda *a: pytest.fail("no tool call"), miss=miss, now=NOW) is None
    assert "small body" in miss["position"] and "Bennu" in roster


@pytest.mark.parametrize("question, calculated", [
    ("Calculate where Mars was on 2015-06-15", True),                                   # the Captain's word: the chain with its work, not the kernel's value
    ("Compute the position of the Moon on 2020-01-01 from the Sun", True),
    ("Where is Mars on 2015-06-15? Show your work.", True),
    ("Where was Mars on 2015-06-15?", False),
])
def test_calculate_asks_for_the_chain_not_the_lookup(stage, question, calculated):
    got, why, _ = stage.read(question, NOW)
    assert got is not None, why
    assert got.get("calculate", False) is calculated
    assert got.get("budget_s") == (85 if calculated else None)                          # a calculation gets the ask route's whole budget; a lookup needs none


def test_without_sigma_ground_the_roster_is_empty_and_planets_still_work(stage, monkeypatch):
    monkeypatch.setattr(stage, "small_body_lookup", lambda phrase: None)
    assert stage.read("Where is Eros on 2029-10-27?", NOW)[0] is None
    assert stage.read("Where is Mars on 2029-10-27?", NOW)[0] == {"body_name": "mars", "when": "2029-10-27", "central_body": "earth"}
