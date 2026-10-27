"""purple Skippy's tools (services/purple-tools): the MCP server speaks the protocol, the search tool turns SearXNG's answer into words a model can
use and never raises, whatever the service does."""
import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402
import tools  # noqa: E402

SEARX = {"results": [
    {"title": "Qwen3 release notes", "url": "https://example.org/qwen3", "content": "Qwen3 adds   thinking\nmodes and more.", "engines": ["duckduckgo", "brave"]},
    {"title": "llama.cpp", "url": "https://github.com/ggml-org/llama.cpp", "content": "LLM inference in C/C++", "engine": "github"},
    {"title": "no url", "content": "dropped"}], "answers": ["42"], "unresponsive_engines": [["google", "CAPTCHA"]]}


def test_a_search_becomes_numbered_results_with_urls_snippets_and_engines():
    seen = {}

    def get(url):
        seen["url"] = url
        return SEARX
    text = tools.web_search({"query": "qwen3 thinking", "num_results": 5, "time_range": "week", "category": "it"}, get=get)
    assert "q=qwen3+thinking" in seen["url"] and "format=json" in seen["url"] and "time_range=week" in seen["url"] and "categories=it" in seen["url"]
    assert text.startswith("Direct answer: 42")
    assert "1. Qwen3 release notes\n   https://example.org/qwen3\n   Qwen3 adds thinking modes and more.\n   [duckduckgo, brave]" in text      # whitespace tidied
    assert "2. llama.cpp" in text and "[github]" in text and "no url" not in text                                                    # a result without a URL is dropped


def test_no_results_says_which_engines_did_not_answer_and_the_limits_hold():
    text = tools.web_search({"query": "zzz"}, get=lambda url: {"results": [], "unresponsive_engines": [["google", "CAPTCHA"], ["bing", "timeout"]]})
    assert "No results for 'zzz'" in text and "google (CAPTCHA)" in text and "bing (timeout)" in text
    long = {"results": [{"title": "t" * 500, "url": "https://x/" + "u" * 800, "content": "c" * 900} for _ in range(10)]}
    out = tools.web_search({"query": "q", "num_results": 99}, get=lambda url: long)
    assert len(out) <= tools.MAX_TEXT and out.count("\n1. ") + out.count("1. ") >= 1
    seen = {}
    tools.web_search({"query": "x" * 1000, "num_results": "junk", "time_range": "century", "category": "evil"}, get=lambda u: seen.setdefault("u", u) and {"results": []})
    assert "century" not in seen["u"] and "evil" not in seen["u"] and len(seen["u"]) < 600                                          # only known values, bounded query


def test_a_failing_service_is_the_tools_own_words_never_an_exception():
    def down(url):
        raise urllib.error.URLError("refused")

    def http500(url):
        raise urllib.error.HTTPError(url, 500, "boom", {}, None)
    tools.TOOLS["web_search"]["run"], real = (lambda a: tools.web_search(a, get=down)), tools.TOOLS["web_search"]["run"]
    try:
        text, is_error = tools.call("web_search", {"query": "x"})
        assert is_error and "could not reach its service at home" in text and "skippy-core" in text
        tools.TOOLS["web_search"]["run"] = lambda a: tools.web_search(a, get=http500)
        assert tools.call("web_search", {"query": "x"}) == ("web_search failed: the service answered HTTP 500.", True)
        assert tools.call("web_search", {}) == ("web_search needs a query", True)
        assert tools.call("nope", {})[1] is True and "web_search" in tools.call("nope", {})[0]
    finally:
        tools.TOOLS["web_search"]["run"] = real


def test_the_server_speaks_mcp_over_stdio_and_survives_junk():
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}), "not json", json.dumps([1, 2]),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "web_search", "arguments": {}}}),
             json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}), json.dumps({"jsonrpc": "2.0", "id": 5, "method": "bogus"})]
    out = io.StringIO()
    server.main(io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(l) for l in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]                                         # the notification and the junk got no reply
    assert replies[0]["result"]["serverInfo"]["name"] == "purple-tools" and replies[0]["result"]["protocolVersion"] == server.PROTOCOL
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert "web_search" in names and all("inputSchema" in t for t in replies[1]["result"]["tools"])
    assert replies[2]["result"]["isError"] is True and "needs a query" in replies[2]["result"]["content"][0]["text"]
    assert replies[4]["error"]["code"] == -32601


def test_the_config_the_bridge_writes_names_this_file():
    cfg = server.mcp_config("python.exe")["mcpServers"]["purple-tools"]
    assert cfg["type"] == "stdio" and cfg["command"] == "python.exe" and cfg["args"][0].endswith("server.py")
