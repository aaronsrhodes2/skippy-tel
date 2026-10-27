"""Mentat's cascade stage in /ask (mentat/cascade_stage.py): the benchmark's deterministic cascade, opt-in by MENTAT_CASCADE.

The cascade itself is faked (`run_sigma_ground.cascade_answer` replaced with a record); the real one is tested in sigma-ground
(benchmark/test_cascade_answer.py). Needs a sigma-ground checkout, like the other integration tests:
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
    return _load("cascade_stage")


@pytest.fixture
def harness(monkeypatch):
    H = pytest.importorskip("sigma_ground.mcp.benchmark.run_sigma_ground")
    T = pytest.importorskip("sigma_ground.mcp.benchmark.tweaks")
    yield H, T
    T.reset()


def record(value="No. The Earth is an oblate spheroid.", units="", **extra):
    return {"answer_text": str(value), "extracted_value": value, "extracted_units": units, "tool_calls": [], "turns": 0, "elapsed_s": 0.01, **extra}


def test_off_unless_a_mode_is_named(stage, monkeypatch):
    monkeypatch.setattr(stage, "MODE", "")
    assert stage.enabled() is False
    assert stage.answer("Is the Earth flat?") is None
    monkeypatch.setattr(stage, "MODE", "sideways")
    assert stage.enabled() is False


def test_a_stage_answer_becomes_the_ask_result(stage, monkeypatch, harness):
    H, T = harness
    monkeypatch.setattr(stage, "MODE", "before")
    monkeypatch.setattr(H, "cascade_answer", lambda q, t0, router: record(refusal_hit="false_premise"))
    out = stage.answer("Is the Earth flat?")
    assert out["value"].startswith("No.") and out["interpreter"] == "cascade" and out["inputs"]["route"] == "cascade"
    assert out["inputs"]["stage"] == "refusal:false_premise" and out["source"].startswith("sigma-ground benchmark cascade")
    assert out["inputs"]["harness_record"]["extracted_value"] == out["value"]
    assert not T.on("cascade.semantic") and T.on("cascade.refusal")   # the configuration the stage ran in


def test_no_match_and_no_value_fall_through_with_a_reason(stage, monkeypatch, harness):
    H, T = harness
    monkeypatch.setattr(stage, "MODE", "after")
    miss = {}
    monkeypatch.setattr(H, "cascade_answer", lambda q, t0, router: None)
    assert stage.answer("zxq", miss=miss) is None and miss["cascade"] == "no stage matched"
    monkeypatch.setattr(H, "cascade_answer", lambda q, t0, router: record(value=None, thermo_hit="carnot"))
    assert stage.answer("q", miss=miss) is None and miss["cascade"] == "thermo:carnot matched without a value"


def test_a_broken_cascade_never_stops_ask(stage, monkeypatch, harness):
    H, T = harness
    monkeypatch.setattr(stage, "MODE", "before")

    def boom(q, t0, router):
        raise RuntimeError("classifier exploded")
    monkeypatch.setattr(H, "cascade_answer", boom)
    miss = {}
    assert stage.answer("q", miss=miss) is None and miss["cascade"].startswith("RuntimeError: classifier exploded")


def test_stages_named_in_mentat_cascade_off_are_switched_off(stage, monkeypatch, harness):
    H, T = harness
    monkeypatch.setattr(stage, "OFF", ("math", "nonsense"))
    stage.configure_harness()
    assert not T.on("cascade.math") and T.on("cascade.thermo") and T.on("cascade.refusal")
    monkeypatch.setattr(stage, "OFF", ())
    stage.configure_harness()
    assert T.on("cascade.math")
    assert set(stage.STAGES) == {n.removeprefix("cascade.") for n in T.REGISTRY if n.startswith("cascade.")}   # the names stay the harness's


def test_the_semantic_stage_is_opt_in(stage, monkeypatch, harness):
    H, T = harness
    monkeypatch.setattr(stage, "SEMANTIC", True)
    stage.configure_harness()
    assert T.on("cascade.semantic")
    monkeypatch.setattr(stage, "SEMANTIC", False)
    stage.configure_harness()
    assert not T.on("cascade.semantic")
