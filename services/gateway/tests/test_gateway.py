"""The gateway (gateway.py) over fake backend doors: a short listing, find_tools, call_tool routing, a dead door, collisions, and the key rule."""
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gateway as gw  # noqa: E402


class Fake:
    """A backend door in process: rpc(url, key, method, params, timeout) -> result. `down` makes it refuse."""

    def __init__(self, tools, down=False):
        self.tools, self.down, self.calls = tools, down, []

    def __call__(self, url, key, method, params=None, timeout=15.0):
        if self.down:
            raise ConnectionRefusedError("refused")
        if method == "tools/list":
            return {"tools": self.tools}
        self.calls.append((method, params, key))
        return {"content": [{"type": "text", "text": f"{url}:{params['name']}:{json.dumps(params['arguments'])}"}], "isError": False}


def tool(name, desc="", props=()):
    return {"name": name, "description": desc, "inputSchema": {"type": "object", "properties": {p: {} for p in props}}}


@pytest.fixture
def world():
    doors = {"http://purple": Fake([tool("purple_ask", "Ask purple Skippy to do a job", ["message"])]),
             "http://house": Fake([tool("home_control", "Turn a light on", ["name"]), tool("ask", "the house asks")]),
             "http://mentat": Fake([tool("ask", "Ask a physics question", ["q"])])}
    backends = {n: {"url": f"http://{n}", "key": "k-" + n} for n in ("purple", "house", "mentat")}
    g = gw.Gateway(backends, rpc_fn=lambda url, key, *a, **k: doors[url](url, key, *a, **k))
    return g, doors


def call(g, tool_name, **args):
    reply = g.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": args}})
    r = reply["result"]
    return r["content"][0]["text"], r["isError"]


def test_it_lists_three_short_tools_not_every_backend_tool(world):
    g, _ = world
    names = [t["name"] for t in g.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
    assert names == ["find_tools", "call_tool", "backends"]
    assert g.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_find_tools_filters_by_word_and_door(world):
    g, _ = world
    rows = json.loads(call(g, "find_tools", query="light")[0])
    assert [(r["backend"], r["name"]) for r in rows] == [("house", "home_control")] and rows[0]["arguments"] == ["name"]
    assert {r["backend"] for r in json.loads(call(g, "find_tools")[0])} == {"purple", "house", "mentat"}
    assert [r["backend"] for r in json.loads(call(g, "find_tools", backend="mentat")[0])] == ["mentat"]


def test_call_tool_routes_to_the_door_with_its_key(world):
    g, doors = world
    text, err = call(g, "call_tool", name="home_control", arguments={"name": "lamp"})
    assert not err and text == 'http://house:home_control:{"name": "lamp"}'
    assert doors["http://house"].calls[0][2] == "k-house"
    text, err = call(g, "home_control", name="lamp")                    # a real tool name works directly too
    assert not err and "home_control" in text


def test_a_shared_name_goes_to_the_first_door_and_backend_dot_name_reaches_another(world):
    g, _ = world
    assert call(g, "ask", q="x")[0].startswith("http://house:")         # house is listed before mentat
    assert call(g, "call_tool", name="mentat.ask", arguments={"q": "x"})[0].startswith("http://mentat:")


def test_a_dead_door_is_absent_not_an_error_for_the_rest(world):
    g, doors = world
    doors["http://purple"].down = True
    status = json.loads(call(g, "backends")[0])
    assert status["purple"]["up"] is False and status["house"]["up"] is True and status["house"]["tools"] == 2
    text, err = call(g, "home_control", name="lamp")
    assert not err
    text, err = call(g, "purple_ask", message="hi")
    assert err and "No door has a tool" in text


def test_a_door_that_dies_mid_call_says_so_and_is_looked_at_again(world):
    g, doors = world
    assert not call(g, "purple_ask", message="hi")[1]
    doors["http://purple"].down = True
    g.rpc = lambda url, key, method, *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("refused")) if method == "tools/call" else doors[url](url, key, method, *a)
    text, err = call(g, "purple_ask", message="hi")
    assert err and "not answering" in text


def test_unknown_names_and_methods(world):
    g, _ = world
    assert call(g, "call_tool")[1] is True
    assert call(g, "nope")[1] is True
    assert g.handle({"jsonrpc": "2.0", "id": 2, "method": "bogus"})["error"]["code"] == -32601


def test_full_listing_is_opt_in(world, monkeypatch):
    g, _ = world
    monkeypatch.setenv("GATEWAY_FULL", "1")
    assert {t["name"] for t in g.tool_list()} == {"purple_ask", "home_control", "ask"}


def test_backends_come_from_defaults_overrides_and_json():
    b = gw.load_backends({"PURPLE_MCP_KEY": "pk", "GATEWAY_HOUSE_URL": "http://h:1/mcp", "GATEWAY_BACKENDS": json.dumps({"factorio": {"url": "http://f/mcp", "key": "fk"}})})
    assert b["purple"]["key"] == "pk" and b["house"]["url"] == "http://h:1/mcp" and b["factorio"] == {"url": "http://f/mcp", "key": "fk"} and b["mentat"]["url"].endswith("7350/mcp")
    assert gw.load_backends({"GATEWAY_BACKENDS": "not json"})["purple"]["url"].endswith("47860/mcp")


def _post(url, body, key=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def test_the_http_door_refuses_off_loopback_without_a_key_and_checks_it(world):
    g, _ = world
    with pytest.raises(SystemExit):
        gw.make_http_server("0.0.0.0", 0, g)
    server = gw.make_http_server("127.0.0.1", 0, g, key="gate-key")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
    try:
        listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert _post(url, listing)[0] == 401 and _post(url, listing, "nope")[0] == 401
        status, reply = _post(url, listing, "gate-key")
        assert status == 200 and len(reply["result"]["tools"]) == 3
        status, reply = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "call_tool", "arguments": {"name": "home_control", "arguments": {"name": "a"}}}}, "gate-key")
        assert status == 200 and "home_control" in reply["result"]["content"][0]["text"]
        assert _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, "gate-key")[0] == 202
    finally:
        server.shutdown()
