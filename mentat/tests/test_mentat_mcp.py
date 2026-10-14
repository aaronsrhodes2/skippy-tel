"""Mentat's /mcp endpoint (mentat/app.py): the public tools as a remote MCP server.

Needs a sigma-ground checkout, like the Weapon vs. Armor integration tests:
    SIGMA_GROUND_DIR=/path/to/sigma-ground python -m pytest mentat/tests -q
"""
import asyncio
import importlib.util
import json
import threading
from pathlib import Path

import pytest

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}}


@pytest.fixture(scope="module")
def mentat():
    pytest.importorskip("mcp")
    pytest.importorskip("sigma_ground")
    path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("mentat_app_mcp", path)   # not `app`: the portal has one too
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(mentat):
    mentat._hits.clear()
    yield mentat.app.test_client()
    mentat._hits.clear()


def rpc(client, method, params=None, id_=7):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method, **({"params": params} if params is not None else {})}
    r = client.post("/mcp", json=msg)
    assert r.status_code == 200, r.data
    return r.get_json()


def call(client, name, arguments):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments})["result"]


def test_initialize_agrees_a_protocol_version_and_offers_tools_with_sigma_grounds_rules(client, mentat):
    got = client.post("/mcp", json=INIT).get_json()
    assert got["id"] == 1 and got["result"]["protocolVersion"] == "2025-06-18"
    assert got["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert got["result"]["serverInfo"]["name"] == "mentat"
    assert "provenance" in got["result"]["instructions"].lower()
    unknown = rpc(client, "initialize", {**INIT["params"], "protocolVersion": "1999-01-01"})
    assert unknown["result"]["protocolVersion"] == mentat.LATEST_PROTOCOL_VERSION


def test_notifications_get_202_and_no_body(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.data == b""


def test_tools_list_is_exactly_the_public_set_the_rest_api_serves(client):
    tools = rpc(client, "tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == [t["name"] for t in client.get("/tools").get_json()]
    assert "simulate" not in names and not any(n.startswith("playground_") for n in names)
    assert all(t["inputSchema"]["type"] == "object" for t in tools)


def test_a_call_returns_the_tools_own_result_as_json_text(client):
    res = call(client, "convert_units", {"value": 1, "from_units": "mile", "to_units": "km"})
    assert res["isError"] is False
    assert json.loads(res["content"][0]["text"])["value"] == pytest.approx(1.609344)


def test_hidden_and_unknown_tools_are_protocol_errors(client):
    for name in ("simulate", "playground_create", "no_such_tool"):
        assert rpc(client, "tools/call", {"name": name, "arguments": {}})["error"]["code"] == -32602
    assert rpc(client, "resources/list")["error"]["code"] == -32601
    assert rpc(client, "ping")["result"] == {}


def test_the_rest_apis_guards_hold_and_come_back_as_tool_errors(client, mentat, monkeypatch):
    code = call(client, "solve_equation", {"equation": "__import__('os').system('id')", "variable": "x"})
    assert code["isError"] and "looks like code" in code["content"][0]["text"]
    assert call(client, "convert_units", ["not", "an", "object"])["isError"]
    mentat._hits.clear()
    monkeypatch.setattr(mentat, "RATE_PER_MIN", 1)
    assert not call(client, "convert_units", {"value": 1, "from_units": "m", "to_units": "cm"})["isError"]
    slow = call(client, "convert_units", {"value": 1, "from_units": "m", "to_units": "cm"})
    assert slow["isError"] and "rate limit" in slow["content"][0]["text"]


def test_bad_posts_and_other_verbs_are_refused(client, mentat):
    assert client.post("/mcp", data="not json", content_type="application/json").status_code == 400
    assert client.post("/mcp", json=[INIT]).status_code == 400                       # no batches
    assert client.post("/mcp", json={"id": 1, "method": "ping"}).get_json()["error"]["code"] == -32600
    assert client.post("/mcp", data="x" * (mentat.MAX_MCP_BYTES + 1),
                       content_type="application/json").status_code == 413
    assert client.get("/mcp").status_code == 405 and client.delete("/mcp").status_code == 405


def test_the_official_mcp_client_connects_lists_and_calls(mentat):
    """The real SDK client, over real HTTP: what Claude's connector and Claude Code do."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from werkzeug.serving import make_server

    httpd = make_server("127.0.0.1", 0, mentat.app, threaded=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    async def session():
        async with streamable_http_client(f"http://127.0.0.1:{httpd.server_port}/mcp") as (read, write, _):
            async with ClientSession(read, write) as s:
                init = await s.initialize()
                tools = (await s.list_tools()).tools
                res = await s.call_tool("hawking_temperature", {"mass_kg": 1.989e30})
                return init, tools, res

    try:
        mentat._hits.clear()
        init, tools, res = asyncio.run(session())
    finally:
        httpd.shutdown()
    assert init.serverInfo.name == "mentat"
    assert len(tools) == len(mentat.TOOLS) > 100
    assert not res.isError
    assert json.loads(res.content[0].text)["value"] == pytest.approx(6.17e-8, rel=1e-2)


def test_version_reports_the_sigma_ground_commit_and_the_tool_count(client, mentat, monkeypatch):
    monkeypatch.setenv("SIGMA_GROUND_REF", "feedface0123")
    got = client.get("/version").get_json()
    assert got["sigma_ground_ref"] == "feedface0123"
    assert got["tools"] == len(mentat.TOOLS) and got["tools"] > 100
    monkeypatch.delenv("SIGMA_GROUND_REF")
    assert client.get("/version").get_json()["sigma_ground_ref"] == "dev"


def test_an_ode_with_primes_is_maths_but_a_quote_elsewhere_is_still_code(client, mentat):
    ok = client.post("/tools/solve_ode", json={"equation": "y'' + y"}).get_json()
    assert ok["ok"] is True and "C1" in ok["result"]["value"], ok
    # the same apostrophe in any other argument is still refused, and so is code hidden in the ODE
    assert client.post("/tools/simplify_expr", json={"expression": "x'"}).status_code == 400
    assert client.post("/tools/solve_ode", json={"equation": "y'' + __import__('os')"}).status_code == 400
    assert client.post("/tools/solve_ode", json={"equation": "y'' + open(y)"}).status_code == 400


def test_the_tool_contract_reaches_the_rest_and_mcp_callers(client, mentat):
    # a quantity string works, and the conversion is echoed
    ok = client.post("/tools/kinetic_energy", json={"mass_kg": "145 g", "velocity_m_s": "90 mph"}).get_json()
    assert ok["ok"] is True and ok["result"]["converted"]["mass_kg"] == "145 g = 0.145 kg"
    # a caller's mistake is a 400 with the contract's structured detail, as a pydantic failure was before
    bad = client.post("/tools/schwarzschild_radius", json={})
    assert bad.status_code == 400
    body = bad.get_json()
    assert body["ok"] is False and body["detail"]["code"] == "missing_argument" and body["detail"]["param"] == "mass_kg"
    wrong = client.post("/tools/schwarzschild_radius", json={"mass_kg": "54 km/h"})
    assert wrong.status_code == 400 and wrong.get_json()["detail"]["code"] == "wrong_dimension"
    # the tool's own refusal stays a 200 with value null, now with a code
    miss = client.post("/tools/density", json={"material": "unobtainium"}).get_json()
    assert miss["ok"] is True and miss["result"]["value"] is None and miss["result"]["error"]["code"] == "unknown_key"
    # over MCP the same failure is a tool error the model reads
    via_mcp = call(client, "schwarzschild_radius", {})
    assert via_mcp["isError"] is True and json.loads(via_mcp["content"][0]["text"])["code"] == "missing_argument"
