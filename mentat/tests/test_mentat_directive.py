"""Mentat's directive rule in /ask (mentat/directive_stage.py): a question that names a tool and its arguments runs that call; anything else falls through.

The tool itself is faked (a recorder). The 2026-11-03 miss that started this: an agent asked "Where is asteroid 433 Eros ... Use the body_position tool with
body_name='eros', when='2029-10-27T00:00:00', central_body='barycenter', budget_s=100." and the router answered with asteroid_analysis's surface gravity.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MENTAT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def stage():
    spec = importlib.util.spec_from_file_location("directive_stage", MENTAT / "directive_stage.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["directive_stage"] = module
    spec.loader.exec_module(module)
    return module


TOOLS = {
    "body_position": SimpleNamespace(inputSchema={"properties": {"body_name": {"type": "string"}, "when": {"type": "string"}, "central_body": {"type": "string"}, "budget_s": {"type": "number"}}}),
    "prediction_contest": SimpleNamespace(inputSchema={"properties": {"body_name": {"type": "string"}, "lead_years": {"type": "integer"}}}),
    "solvable_bodies": SimpleNamespace(inputSchema={"properties": {}}),
}
EROS = ("Where is asteroid 433 Eros on 2029-10-27T00:00:00 UTC? I need its position vector (X, Y, Z) in the ICRF/J2000 frame relative to the solar system barycenter. "
        "Use the body_position tool with body_name='eros', when='2029-10-27T00:00:00', central_body='barycenter', budget_s=100.")


def test_the_eros_directive_runs_body_position_as_written(stage):
    calls = []

    def call(name, args):
        calls.append((name, args))
        return {"value": {"body": "433 Eros"}, "units": "km", "source": "our own test-particle integration", "notes": "PREDICTION (our calculation)."}
    out = stage.answer(EROS, call=call, tools=TOOLS)
    assert calls == [("body_position", {"body_name": "eros", "when": "2029-10-27T00:00:00", "central_body": "barycenter", "budget_s": 100.0})]
    assert out["value"]["body"] == "433 Eros" and out["error"] is None and out["inputs"]["route"] == "directive"
    assert out["interpreted_as"].startswith("body_position(body_name='eros'")


@pytest.mark.parametrize("question, tool, args", [
    ("call prediction_contest(body_name='moon', lead_years=5)", "prediction_contest", {"body_name": "moon", "lead_years": 5}),
    ("Please run body_position with body_name=\"Apophis\" and when=2029-04-13", "body_position", {"body_name": "Apophis", "when": "2029-04-13"}),
])
def test_other_phrasings_of_a_directive(stage, question, tool, args):
    assert stage.read(question, TOOLS)[:2] == (tool, args)


@pytest.mark.parametrize("question, reason", [
    ("Where is Mars on 2029-10-27?", "no tool named"),                                             # an ordinary question: the position rule's
    ("Use the magic_tool with body_name='eros'", "no such tool"),                                 # a tool Mentat does not have
    ("Use body_position for Eros please", "no argument"),                                          # the tool named, nothing to call it with
    ("use solvable_bodies with budget_s=3", "no argument"),                                        # an argument that is not in that tool's signature
])
def test_everything_else_falls_through_with_a_reason(stage, question, reason):
    miss = {}
    assert stage.answer(question, call=lambda *a: pytest.fail("no tool call"), tools=TOOLS, miss=miss) is None
    assert reason in miss["directive"]


def test_the_tools_refusal_is_the_answer_and_a_broken_tool_falls_through(stage):
    out = stage.answer(EROS, call=lambda n, a: {"value": None, "notes": "'eros' is not supported yet"}, tools=TOOLS)
    assert out["value"] is None and "not supported" in out["error"]
    miss = {}

    def boom(name, args):
        raise RuntimeError("kernel missing")
    assert stage.answer(EROS, call=boom, tools=TOOLS, miss=miss) is None and "kernel missing" in miss["directive"]
