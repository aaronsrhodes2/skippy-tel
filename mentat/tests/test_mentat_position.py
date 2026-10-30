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
