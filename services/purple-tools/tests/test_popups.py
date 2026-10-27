"""The quick pop-ups as MCP tools (tools.popup_tools / run_popup): one tool per registry entry, the file read from a path, JSON answers inline,
pictures saved where the agent can Read them, and an unreachable reporter costing nothing."""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402

ENTRY = {"name": "remove_background", "description": "Cut the subject out of an attached picture. More words.", "inputs": {"type": "object", "properties": {"output": {"type": "string"}}},
         "media_in": ["image/png", "image/jpeg"], "media_required": True, "outputs": ["image/png"], "est_seconds": 30, "box": "pc"}
SEARCH = {"name": "search_memory", "description": "Search the Captain's past.", "inputs": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
          "outputs": ["application/json"], "est_seconds": 20, "box": "pc"}


@pytest.fixture(autouse=True)
def fresh():
    tools._popup_cache.update(at=0.0, tools={})
    yield
    tools._popup_cache.update(at=0.0, tools={})


def test_each_registered_quick_tool_becomes_a_tool_with_its_schema_and_a_file_argument():
    got = tools.popup_tools(get=lambda url, timeout=0: [ENTRY, SEARCH, {"name": "web_search"}, {"name": "../bad"}, "junk", {"name": "echo", "family": "plumbing"}])
    assert sorted(got) == ["remove_background", "search_memory"]                 # a name already taken, an unsafe name and junk are dropped
    rb = got["remove_background"]["input_schema"]
    assert rb["properties"]["input_path"]["type"] == "string" and rb["required"] == ["input_path"] and "output" in rb["properties"]
    assert "input_path" not in got["search_memory"]["input_schema"]["properties"] and got["search_memory"]["input_schema"]["required"] == ["query"]
    assert "about 30 s" in got["remove_background"]["description"] and ".purple-out" in got["remove_background"]["description"]


def test_an_unreachable_reporter_means_no_pop_up_tools_and_the_rest_still_listed():
    def down(url, timeout=0):
        raise OSError("refused")
    assert tools.popup_tools(get=down) == {}
    names = [t["name"] for t in tools.tool_list()]
    assert "web_search" in names and "ask_mentat" in names


def test_a_picture_goes_in_by_path_and_the_result_is_saved_where_it_can_be_read(tmp_path):
    src = tmp_path / "photo.png"
    src.write_bytes(b"png-in")
    seen = {}

    def post(url, body, timeout=0):
        seen.update(url=url, body=body, timeout=timeout)
        return {"type": "image/png", "b64": base64.b64encode(b"png-out").decode(), "bytes": 7}
    text = tools.run_popup(ENTRY, {"input_path": str(src), "output": "png"}, post=post, cwd=tmp_path)
    assert seen["url"].endswith("/popup/remove_background") and seen["body"]["args"] == {"output": "png"} and seen["timeout"] == 120.0
    assert seen["body"]["input"]["name"] == "photo.png" and seen["body"]["input"]["type"] == "image/png" and base64.b64decode(seen["body"]["input"]["b64"]) == b"png-in"
    saved = next((tmp_path / ".purple-out").iterdir())
    assert saved.read_bytes() == b"png-out" and saved.name.startswith("remove_background-") and str(saved) in text and "Read it" in text


def test_json_comes_back_as_words_and_the_limits_and_failures_say_why(tmp_path):
    ok = lambda url, body, timeout=0: {"type": "application/json", "b64": base64.b64encode(b'{"hits": [1, 2]}').decode()}      # noqa: E731
    assert tools.run_popup(SEARCH, {"query": "x"}, post=ok, cwd=tmp_path) == '{"hits": [1, 2]}'
    assert not (tmp_path / ".purple-out").exists()                                                                           # nothing saved for words
    with pytest.raises(ValueError, match="no file at"):
        tools.run_popup(ENTRY, {"input_path": str(tmp_path / "nope.png")}, post=ok, cwd=tmp_path)
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * (tools.MAX_INPUT + 1))
    with pytest.raises(ValueError, match="shrink it first"):
        tools.run_popup(ENTRY, {"input_path": str(big)}, post=ok, cwd=tmp_path)
    with pytest.raises(ValueError, match="the model isn't loaded"):
        tools.run_popup(SEARCH, {"query": "x"}, post=lambda url, body, timeout=0: {"error": "the model isn't loaded"}, cwd=tmp_path)


def test_the_server_lists_and_calls_a_pop_up_like_any_tool(tmp_path, monkeypatch):
    tools.popup_tools(get=lambda url, timeout=0: [SEARCH])
    assert "search_memory" in [t["name"] for t in tools.tool_list()]
    monkeypatch.setattr(tools, "run_popup", lambda entry, args, **k: f"asked {entry['name']} {args}")
    assert tools.call("search_memory", {"query": "q"}) == ("asked search_memory {'query': 'q'}", False)
    assert tools.call("ghost", {})[1] is True
