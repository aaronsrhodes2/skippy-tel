"""mentat/word_stage.py: the last stop before a plain decline. The model is stubbed (`_ask_model`), the arithmetic is sigma-ground's evaluate_quantity.

Needs a sigma-ground checkout (SIGMA_GROUND_DIR), like the other app tests. The model's own accuracy is measured separately (mentat/eval/run_word_problems.py).
"""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def ws():
    pytest.importorskip("sigma_ground")
    path = Path(__file__).resolve().parents[1] / "word_stage.py"
    spec = importlib.util.spec_from_file_location("word_stage_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MODEL = "stub-model"
    return module


@pytest.fixture
def call():
    from sigma_ground.mcp.tools.units import evaluate_quantity

    def run(name, args):
        assert name == "evaluate_quantity"
        return evaluate_quantity(**args).to_dict()
    return run


def plan(expression, to_units=None, cannot=None, gives=True):
    return {"asks_for": "x", "question_gives_every_fact": gives, "expression": expression, "to_units": to_units, "cannot": cannot}


def scripted(ws, monkeypatch, *plans):
    """The model answers with each plan in turn; the messages it was sent are kept."""
    sent = []
    queue = list(plans)

    def fake(messages, ollama_url):
        sent.append([dict(m) for m in messages])
        return queue.pop(0)
    monkeypatch.setattr(ws, "_ask_model", fake)
    return sent


Q = "Two trains, 100 km apart, approach each other. One moves at 60 km/h, the other at 40 km/h. How long until they meet?"


def test_a_word_problem_is_computed_by_the_tool_from_the_questions_numbers(ws, call, monkeypatch):
    scripted(ws, monkeypatch, plan("100 km / (60 km/h + 40 km/h)", "hour"))
    out = ws.answer(Q, call=call, ollama_url="http://x")
    assert out["value"] == pytest.approx(1.0) and out["units"] == "hour"
    assert out["provenance_tag"] == "DERIVED" and out["source"] == "pint quantity arithmetic"
    assert out["inputs"]["route"] == "word_problem" and out["inputs"]["expression"] == "100 km / (60 km/h + 40 km/h)"
    assert "model only read" in out["notes"] and out["interpreter"] == "word problem stub-model"


@pytest.mark.parametrize("question, expression, bad", [
    ("How many seconds are in 3 weeks?", "3 * 7 * 24 * 60 * 60", [7, 24, 60, 60]),                    # conversion factors from memory
    ("How tall in bananas is a 2 m tall giraffe?", "2 m / 18 cm", [18]),                              # a fact the question never gave
    ("A rope is 3 m long.", "3 m * 365", [365]),
])
def test_a_number_the_question_does_not_state_is_unlicensed(ws, question, expression, bad):
    assert ws._unlicensed(expression, question) == bad


@pytest.mark.parametrize("question, expression", [
    ("The Moon is 384,000 km away.", "384000 km"),                                                    # a thousands separator
    ("The universe is 13.787 billion years old.", "13.787e9 year"),                                   # a scale word
    ("The universe is 13.787 billion years old.", "13.787 * 10**9 year"),
    ("A jacket costs 80 dollars with 15 percent off.", "80 * (1 - 15/100)"),                          # a percentage's 100
    ("A recipe uses twelve eggs for a dozen muffins.", "12 / 12"),                                    # number words
    ("A 3 m giraffe shrunk by a factor of one million.", "3 m / 1e6"),                                # a scale and its reciprocal
    ("A square has a side of 5 m.", "(5 m)**2"),                                                      # structural 2
])
def test_what_the_question_licenses_is_not_flagged(ws, question, expression):
    assert ws._unlicensed(expression, question) == []


def test_an_unlicensed_number_is_sent_back_and_the_corrected_expression_is_used(ws, call, monkeypatch):
    sent = scripted(ws, monkeypatch, plan("3 * 7 * 24 * 3600", "s"), plan("3 week", "s"))
    out = ws.answer("How many seconds are in 3 weeks?", call=call, ollama_url="http://x")
    assert out["value"] == 1814400 and out["inputs"]["attempts"] == 2
    assert "not stated in the question" in sent[1][-1]["content"]


def test_a_number_that_stays_unlicensed_is_a_miss_not_an_answer(ws, call, monkeypatch):
    scripted(ws, monkeypatch, *[plan("3 * 7 * 24 * 3600", "s")] * ws.ATTEMPTS)
    miss = {}
    assert ws.answer("How many seconds are in 3 weeks?", call=call, ollama_url="http://x", miss=miss) is None
    assert "not in the question" in miss["reason"]


def test_a_fact_the_question_lacks_is_a_decline_even_if_an_expression_was_written(ws, call, monkeypatch):
    scripted(ws, monkeypatch, plan("3 / day * 1 year", "dimensionless", cannot="calories per apple not given", gives=False))
    miss = {}
    assert ws.answer("If I eat 3 apples a day, how many calories do I eat in a year?", call=call, ollama_url="http://x", miss=miss) is None
    assert "does not give" in miss["reason"]


def test_a_cannot_is_a_decline(ws, call, monkeypatch):
    scripted(ws, monkeypatch, plan(None, None, cannot="the height of Everest is not given", gives=False))
    miss = {}
    assert ws.answer("How many bananas tall is Everest if a banana is 18 cm?", call=call, ollama_url="http://x", miss=miss) is None
    assert miss["reason"].startswith("cannot:")


def test_a_refusal_over_a_unit_conversion_gets_one_more_try(ws, call, monkeypatch):
    sent = scripted(ws, monkeypatch, plan(None, None, cannot="the conversion from weeks to seconds is not given", gives=False), plan("3 week", "s"))
    out = ws.answer("How many seconds are in 3 weeks?", call=call, ollama_url="http://x")
    assert out["value"] == 1814400
    assert "convert themselves" in sent[1][-1]["content"]


def test_a_tool_error_is_sent_back_and_a_wrong_dimension_never_becomes_an_answer(ws, call, monkeypatch):
    sent = scripted(ws, monkeypatch, plan("400 m / 12 m/second", "second"), plan("400 m / (12 m/second)", "second"))
    out = ws.answer("At 12 m/s, how many seconds to run 400 m?", call=call, ollama_url="http://x")
    assert out["value"] == pytest.approx(33.333, rel=1e-3) and out["units"] == "second"
    assert "parentheses" in sent[1][-1]["content"]


def test_a_negative_size_is_sent_back(ws, call, monkeypatch):
    sent = scripted(ws, monkeypatch, plan("6 km / (12 km/hour) - 6 km / (0.3 km/hour)", "hour"), plan("6 km / (0.3 km/hour) - 6 km / (12 km/hour)", "hour"))
    out = ws.answer("A tortoise walks 0.3 km/h and a hare 12 km/h over 6 km. By how many hours does the hare finish first?", call=call, ollama_url="http://x")
    assert out["value"] == pytest.approx(19.5) and "negative" in sent[1][-1]["content"]


def test_code_in_an_expression_is_refused_by_the_guard_and_nothing_runs(ws, call, monkeypatch):
    scripted(ws, monkeypatch, plan("__import__('os').system('x')", "dimensionless"))
    miss = {}
    assert ws.answer("What is 2 plus 2?", call=call, ollama_url="http://x", guard=lambda n, a: "That input looks like code, not maths.", miss=miss) is None
    assert "code" in miss["reason"]


def test_a_question_with_no_numbers_never_reaches_the_model(ws, call, monkeypatch):
    def boom(messages, ollama_url):
        raise AssertionError("the model must not be asked")
    monkeypatch.setattr(ws, "_ask_model", boom)
    miss = {}
    assert ws.answer("How long does light take to reach Mars?", call=call, ollama_url="http://x", miss=miss) is None
    assert "no numbers" in miss["reason"]


def test_a_model_that_is_down_is_a_miss_not_a_crash(ws, call, monkeypatch):
    def down(messages, ollama_url):
        raise OSError("connection refused")
    monkeypatch.setattr(ws, "_ask_model", down)
    miss = {}
    assert ws.answer(Q, call=call, ollama_url="http://x", miss=miss) is None
    assert "OSError" in miss["reason"]


def test_the_stage_is_off_without_a_model(ws, call):
    ws_model = ws.MODEL
    try:
        ws.MODEL = ""
        assert not ws.enabled() and ws.answer(Q, call=call, ollama_url="http://x") is None
    finally:
        ws.MODEL = ws_model
