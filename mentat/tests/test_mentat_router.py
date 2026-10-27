"""Mentat's trained-router stage in /ask (mentat/router_stage.py, hooked in mentat/app.py `_ask`).

The model itself is never called. The first tests fake `router_stage._run` with the record the benchmark harness returns; the `TestRealHarness` tests
run the harness itself (sigma-ground's `_route_once`) with the model faked at the HTTP level and a fake tool server, because the 2026-10-25 .. 27
regression (every maths answer dropped: the harness was not configured as the router was measured) lived between those two and no faked-record test
could see it. Needs a sigma-ground checkout, like the other integration tests:
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


def run_answer(stage, monkeypatch, rec, model="mentat-router-v31-4b", miss=None):
    async def fake(*a, **k):
        if isinstance(rec, Exception):
            raise rec
        return rec
    monkeypatch.setattr(stage, "MODEL", model)
    monkeypatch.setattr(stage, "_run", fake)
    return stage.answer("escape velocity of Earth", server=None, tools={}, ollama_url="http://core:11434", miss=miss)


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


@pytest.mark.parametrize("rec, starts", [
    ({"router_decline": "not physics"}, "decline: not physics"),
    ({"router_clarify": "which body?"}, "clarify: which body?"),
    ({"router_error": "differentiate_expr returned no value"}, "error: differentiate_expr returned no value"),
    (ConnectionError("down"), "ConnectionError: down"),
    ({**record(), "extracted_value": None}, "no value"),
])
def test_a_miss_says_why(stage, monkeypatch, rec, starts):
    miss = {}
    assert run_answer(stage, monkeypatch, record(**rec) if isinstance(rec, dict) and "tool_calls" not in rec else rec, miss=miss) is None
    assert miss["reason"].startswith(starts), miss
    if isinstance(rec, dict):
        assert miss["plan"] == record()["router_plan"]


def test_router_off_says_so(stage, monkeypatch):
    miss = {}
    monkeypatch.setattr(stage, "MODEL", "")
    assert stage.answer("q", server=None, tools={}, ollama_url="http://core:11434", miss=miss) is None
    assert miss == {"reason": "router off"}


class TestRealHarness:
    """sigma-ground's harness as the stage runs it: the model is an HTTP fake that returns one plan, the tools a fake server."""

    PLAN = '{"tool": "differentiate_expr", "tool_args": {"expression": "x**2*sin(x)", "variable": "x"}}'

    @pytest.fixture(autouse=True)
    def _needs_sigma_ground(self):
        pytest.importorskip("sigma_ground.mcp.benchmark.run_sigma_ground")

    def _tools(self):
        from types import SimpleNamespace
        return {"differentiate_expr": SimpleNamespace(name="differentiate_expr",
                                                      inputSchema={"properties": {"expression": {}, "variable": {}, "order": {}}})}

    def _answer(self, stage, monkeypatch, result: dict, miss=None):
        import json
        import httpx
        from types import SimpleNamespace

        def model(request):
            assert request.url.path == "/api/chat" and json.loads(request.content)["model"] == "mentat-router-v31-4b"
            return httpx.Response(200, json={"message": {"content": self.PLAN}})

        class Server:
            calls = []

            async def call_tool(self, name, args):
                self.calls.append((name, args))
                return [SimpleNamespace(text=json.dumps(result))], None

        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(model), **kw))
        monkeypatch.setattr(stage, "MODEL", "mentat-router-v31-4b")
        server = Server()
        out = stage.answer("differentiate x^2 sin x", server=server, tools=self._tools(), ollama_url="http://fake:11434", miss=miss)
        return out, server

    def test_a_symbolic_result_is_an_answer(self, stage, monkeypatch):
        # The regression: a derivative is a string, and the harness drops a string unless structured_results is on.
        out, server = self._answer(stage, monkeypatch, {"value": "x**2*cos(x) + 2*x*sin(x)", "units": "", "source": "sympy.diff",
                                                         "formula": "d/dx [x**2*sin(x)]", "error": None})
        assert server.calls == [("differentiate_expr", {"expression": "x**2*sin(x)", "variable": "x"})]
        assert out is not None and out["value"] == "x**2*cos(x) + 2*x*sin(x)" and out["source"] == "sympy.diff"
        assert out["interpreted_as"] == "differentiate_expr(expression='x**2*sin(x)', variable='x')"
        assert out["inputs"]["route"] == "router" and out["inputs"]["plan"] == self.PLAN

    def test_a_rejected_call_is_named_by_its_cause(self, stage, monkeypatch):
        miss = {}
        out, _ = self._answer(stage, monkeypatch, {"value": None, "units": "", "error": {"code": "missing_argument", "param": "expression",
                                                                                       "message": "expression is required"}}, miss=miss)
        assert out is None
        assert miss["reason"].startswith("error:") and "differentiate_expr" in miss["reason"] and miss["plan"] == self.PLAN

    def test_the_harness_runs_in_the_measured_configuration(self, stage, monkeypatch):
        from sigma_ground.mcp.benchmark import tweaks
        tweaks.configure(None)                                     # the registry defaults: what the stage ran in before 2026-10-27
        assert not tweaks.on("structured_results")
        self._answer(stage, monkeypatch, {"value": 1.0, "units": "", "error": None})
        assert stage.PROFILE is None and stage.TWEAKS_ON == ("structured_results",)
        assert tweaks.on("structured_results")
        # not strict_params: the v3.1 router writes pre-rename names the server accepts as aliases, and invented extras the tool ignores
        # (measured live with strict_params on: -2 physics answers, +0)
        assert not tweaks.on("strict_params")

    def test_a_pre_rename_parameter_name_is_not_refused(self, stage, monkeypatch):
        import json
        import httpx
        from types import SimpleNamespace
        plan = '{"tool": "joules_to_eV", "tool_args": {"energy_joules": 1.0}}'
        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"message": {"content": plan}})), **kw))
        monkeypatch.setattr(stage, "MODEL", "mentat-router-v31-4b")

        class Server:
            async def call_tool(self, name, args):
                return [SimpleNamespace(text=json.dumps({"value": 6.242e18, "units": "eV", "error": None, "inputs": args}))], None

        tools = {"joules_to_eV": SimpleNamespace(name="joules_to_eV", inputSchema={"properties": {"energy_j": {}}})}   # renamed since training
        miss = {}
        out = stage.answer("1 J in eV", server=Server(), tools=tools, ollama_url="http://fake:11434", miss=miss)
        assert out is not None and out["value"] == 6.242e18, miss
