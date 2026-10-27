"""PURPLE_TOOLS_ONLY narrows what the server offers and will run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402

SIX = "web_search,ask_mentat,mentat_tool,mentat_tools,read_pdf,browse"


def test_unset_offers_all_static_tools(monkeypatch):
    monkeypatch.delenv("PURPLE_TOOLS_ONLY", raising=False)
    monkeypatch.setattr(tools, "popup_tools", lambda *a, **k: {})
    assert set(tools.offered()) == set(tools.TOOLS)


def test_a_list_narrows_without_asking_the_reporter(monkeypatch):
    monkeypatch.setenv("PURPLE_TOOLS_ONLY", " " + SIX.replace(",", " , "))
    def boom(*a, **k):
        raise AssertionError("asked the reporter")
    monkeypatch.setattr(tools, "popup_tools", boom)
    assert [t["name"] for t in tools.tool_list()] == [n for n in tools.TOOLS if n in SIX.split(",")]
    assert len(tools.tool_list()) == 6


def test_a_hidden_tool_cannot_be_called_and_a_listed_popup_still_can(monkeypatch):
    monkeypatch.setenv("PURPLE_TOOLS_ONLY", "web_search")
    text, is_error = tools.call("ask_mentat", {"question": "x"})
    assert is_error and "no tool named" in text and "web_search" in text
    entry = {"name": "search_memory"}
    monkeypatch.setenv("PURPLE_TOOLS_ONLY", "search_memory")
    monkeypatch.setattr(tools, "popup_tools", lambda *a, **k: {"search_memory": {"description": "d", "input_schema": {}, "popup": entry, "run": None}})
    monkeypatch.setattr(tools, "run_popup", lambda e, a, **k: "ran " + e["name"])
    assert tools.call("search_memory", {}) == ("ran search_memory", False)


def test_a_popup_result_keeps_an_extension_people_can_open(tmp_path):
    import base64
    import tools
    for ctype, ext in (("audio/midi", ".mid"), ("model/gltf-binary", ".glb"), ("image/png", ".png")):
        got = tools.run_popup({"name": "x", "est_seconds": 1}, {}, cwd=tmp_path,
                              post=lambda url, body, timeout: {"type": ctype, "b64": base64.b64encode(b"data").decode()})
        assert f"{ext} (" in got and any(f.suffix == ext for f in (tmp_path / ".purple-out").iterdir())
