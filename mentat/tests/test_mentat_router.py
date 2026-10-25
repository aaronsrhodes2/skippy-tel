"""Mentat's trained-router stage in /ask (mentat/router_stage.py, hooked in mentat/app.py `_ask`).

The model itself is not called: `router_stage._run` is faked with the record the benchmark harness returns. Needs a sigma-ground checkout, like the other
integration tests:
    SIGMA_GROUND_DIR=/path/to/sigma-ground python -m pytest mentat/tests -q
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MENTAT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MENTAT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stage():
    return _load("router_stage")


def record(name="escape_velocity", args=None, value=11186.0, **extra):
    import json
    args = args or {"body": "Earth"}
    result = {"value": value, "units": "m/s", "source": "CODATA", "formula": "sqrt(2GM/r)"}
    return {"extracted_value": value, "router_plan": json.dumps({"tool": name, "tool_args": args}),
            "tool_calls": [{"name": name, "args": args, "result_text": json.dumps(result)}], **extra}


def run_answer(stage, monkeypatch, rec, model="mentat-router-v31-4b"):
    async def fake(*a, **k):
        if isinstance(rec, Exception):
            raise rec
        return rec
    monkeypatch.setattr(stage, "MODEL", model)
    monkeypatch.setattr(stage, "_run", fake)
    return stage.answer("escape velocity of Earth", server=None, tools={}, ollama_url="http://core:11434")


def test_off_unless_a_model_is_named(stage, monkeypatch):
    monkeypatch.setattr(stage, "MODEL", "")
    assert stage.enabled() is False
    assert stage.answer("q", server=None, tools={}, ollama_url="http://core:11434") is None


def test_a_tool_value_becomes_the_ask_result(stage, monkeypatch):
    out = run_answer(stage, monkeypatch, record())
    assert out["value"] == 11186.0 and out["units"] == "m/s" and out["error"] is None
    assert out["interpreter"] == "router mentat-router-v31-4b"
    assert out["interpreted_as"] == "escape_velocity(body='Earth')"
    assert out["inputs"]["route"] == "router" and out["inputs"]["chain"][0]["tool"] == "escape_velocity"


@pytest.mark.parametrize("extra", [{"router_decline": True}, {"router_clarify": "which body?"}, {"router_error": "bad json"}])
def test_declines_clarifications_and_errors_fall_through(stage, monkeypatch, extra):
    assert run_answer(stage, monkeypatch, record(**extra)) is None


def test_a_tool_error_or_no_value_falls_through(stage, monkeypatch):
    rec = record()
    rec["tool_calls"][0]["result_text"] = '{"error": "unknown body"}'
    assert run_answer(stage, monkeypatch, rec) is None
    assert run_answer(stage, monkeypatch, {**record(), "extracted_value": None}) is None


def test_a_router_that_is_down_or_slow_never_stops_ask(stage, monkeypatch):
    assert run_answer(stage, monkeypatch, ConnectionError("down")) is None
    assert run_answer(stage, monkeypatch, TimeoutError()) is None


def test_the_session_guard_blocks_a_call_before_it_runs(stage):
    import asyncio

    class Server:
        async def call_tool(self, *a):
            raise AssertionError("must not run")

    session = stage._Session(Server(), guard=lambda name, args: "looks like code")
    with pytest.raises(ValueError):
        asyncio.run(session.call_tool("escape_velocity", {"body": "__import__('os')"}))
