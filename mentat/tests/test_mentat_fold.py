"""Mentat's fold rule in /ask (mentat/fold_stage.py): a named body and a folded quantity go to that tool with body_name, nothing else does.

The tools are faked (a recorder); the real body_name lookup is tested in sigma-ground (sigma_ground/mcp/test_body_name_folds.py). The miss that
started this: the trained router answered "escape velocity from Jupiter" as solar_system_body > escape_velocity, or with the mass from memory.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MENTAT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def stage():
    spec = importlib.util.spec_from_file_location("fold_stage", MENTAT / "fold_stage.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["fold_stage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("question, tool, args", [
    ("What's the escape velocity from Earth's surface?", "escape_velocity", {"body_name": "earth"}),
    ("What's the escape velocity from the surface of the Sun?", "escape_velocity", {"body_name": "sun"}),
    ("escape velocity of Earth", "escape_velocity", {"body_name": "earth"}),
    ("How fast must a rocket go to leave Mars for good?", "escape_velocity", {"body_name": "mars"}),
    ("What speed does it take to break free of Titan's gravity?", "escape_velocity", {"body_name": "titan"}),
    ("What is the escape velocity 400 km above Earth?", "escape_velocity", {"body_name": "earth", "altitude_km": 400.0}),
    ("Escape speed at an altitude of 1,000 km over the Moon?", "escape_velocity", {"body_name": "moon", "altitude_km": 1000.0}),
    ("If the Sun collapsed into a black hole, what would its event horizon radius be?", "schwarzschild_radius", {"body_name": "sun"}),
    ("Schwarzschild radius of Jupiter?", "schwarzschild_radius", {"body_name": "jupiter"}),
    ("How small would Earth have to be squeezed to become a black hole?", "schwarzschild_radius", {"body_name": "earth"}),
    ("Where would the photon sphere be if the Sun were a black hole?", "photon_sphere_radius", {"body_name": "sun"}),
    ("ISCO of a black hole with the mass of the Sun?", "isco_radius", {"body_name": "sun"}),
    ("If Mars became a black hole, what would its Hawking temperature be?", "hawking_temperature", {"body_name": "mars"}),
    ("How long would a black hole with the Moon's mass take to evaporate?", "hawking_evaporation_time", {"body_name": "moon"}),
    ("Gravitational time dilation at the surface of Jupiter?", "gravitational_time_dilation", {"body_name": "jupiter"}),
])
def test_folded_questions_are_read(stage, question, tool, args):
    got_tool, got_args, why = stage.read(question)
    assert (got_tool, got_args) == (tool, args), why


@pytest.mark.parametrize("question", [
    "What is the escape velocity of a planet with mass 6e24 kg and radius 6.4e6 m?",       # its own numbers: the router's
    "Escape speed for a body of 3 Earth masses and radius 9000 km?",
    "What is the Schwarzschild radius of a 10 solar mass black hole?",
    "What is the escape velocity from the solar system at Earth's orbit?",                  # an orbit, not the body
    "Is the escape velocity of Jupiter bigger than Saturn's?",                              # a comparison, two bodies
    "What is the escape velocity of a neutron star?",                                       # no catalogue body
    "What is the mass of Mars?",                                                            # no folded quantity
    "Where will Mars be on March 1, 2027?",
    "How fast does the ISS orbit Earth at 408 km?",
])
def test_everything_else_falls_through_with_a_reason(stage, question):
    miss = {}
    assert stage.answer(question, call=lambda *a: pytest.fail("no tool call"), miss=miss) is None
    assert miss["fold"]


def test_the_answer_is_the_tools_with_the_route_said(stage):
    calls = []

    def call(name, args):
        calls.append((name, args))
        return {"value": 59532.1, "units": "m/s", "source": "sigma-ground (classical escape velocity); Jupiter's GM from JUP365",
                "provenance_tag": "DERIVED", "formula": "v_esc = sqrt(2 G M / r)", "notes": "Jupiter: GM ..."}
    out = stage.answer("What's the escape velocity from Jupiter's surface?", call=call)
    assert calls == [("escape_velocity", {"body_name": "jupiter"})]
    assert out["value"] == 59532.1 and out["units"] == "m/s" and out["error"] is None and out["interpreter"] == "fold rule"
    assert out["inputs"]["route"] == "fold" and out["interpreted_as"] == "escape_velocity(body_name='jupiter')"
    assert "JUP365" in out["source"] and out["provenance_tag"] == "DERIVED"


def test_a_tool_that_gives_nothing_hands_the_question_on(stage):
    miss = {}
    assert stage.answer("Escape velocity of Phobos?", call=lambda n, a: {"value": None, "notes": "no radius"}, miss=miss) is None
    assert "no value" in miss["fold"]
