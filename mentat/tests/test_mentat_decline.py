"""The last stop of /ask (mentat/app.py `_decline_plainly`): a question no stage answered, and the keyword translator can only offer its generic
falling-object blurb for, is declined plainly instead of answered with an offer about falling objects.

Needs a sigma-ground checkout (SIGMA_GROUND_DIR), like the other app tests.
"""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def mentat():
    pytest.importorskip("mcp")
    pytest.importorskip("sigma_ground")
    path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("mentat_app_decline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generic():
    from sigma_ground.materia import translator as tr
    return tr._CLARIFY


def result_of(generic, **over):
    base = {"value": None, "clarification": None, "error": None, "notes": generic, "source": "sigma_ground.materia.translator",
            "inputs": {"route": "clarify"}}
    base.update(over)
    return base


def test_a_question_about_bananas_is_declined_not_offered_a_falling_object(mentat, generic):
    r = result_of(generic)
    mentat._decline_plainly("How many bananas tall is Mount Everest? Assume one banana is 18 centimeters long.", r)
    assert r["notes"].startswith("Mentat has no tool that answers this question")
    assert "falling object" not in r["notes"]
    assert r["value"] is None and r["source"] == "mentat" and r["inputs"]["route"] == "decline"


def test_a_question_that_mentions_falling_keeps_the_hint(mentat, generic):
    r = result_of(generic)
    mentat._decline_plainly("What happens when a tree falls in a forest?", r)
    assert r["notes"].startswith("Mentat has no tool") and "falling object" in r["notes"]


@pytest.mark.parametrize("over", [
    {"value": 3.7},                                           # an answer is never touched
    {"clarification": {"needs": ["material_key"]}},           # a specific clarification is kept
    {"error": {"code": "x"}},
    {"notes": "Which material do you mean?"},                 # not the generic blurb
])
def test_anything_but_the_generic_blurb_is_left_alone(mentat, generic, over):
    r = result_of(generic, **over)
    before = dict(r)
    mentat._decline_plainly("anything", r)
    assert r == before
