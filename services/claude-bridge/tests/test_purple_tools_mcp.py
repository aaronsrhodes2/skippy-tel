"""purple-tools (services/purple-tools: web search, Mentat, pop-ups, PDF, browser) is offered to a run as one more MCP server, beside the house's."""
import json
import sys
from pathlib import Path

import pytest

import sdk_engine as sdk
import workshop_engine as engine


@pytest.fixture
def server(tmp_path, monkeypatch):
    f = tmp_path / "purple-tools" / "server.py"
    f.parent.mkdir()
    f.write_text("# a server\n")
    monkeypatch.setattr(engine, "PURPLE_TOOLS_SERVER", f)
    return f


def test_the_config_names_the_server_and_the_python_that_runs_it(shop, server):
    path = shop.purple_mcp_config()
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    assert Path(path).name == "purple-tools-mcp.json"
    assert cfg == {"mcpServers": {"purple-tools": {"type": "stdio", "command": Path(sys.executable).as_posix(), "args": [server.as_posix()]}}}


def test_without_the_server_there_is_no_config_and_a_run_goes_without(shop, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PURPLE_TOOLS_SERVER", tmp_path / "nowhere" / "server.py")
    assert shop.purple_mcp_config() is None


@pytest.mark.parametrize("brain", ["claude", "core"])
def test_the_command_offers_purple_tools_after_the_houses_on_either_brain(shop, server, monkeypatch, brain):
    monkeypatch.setattr(shop, "house_mcp_config", lambda: "house.json")
    cmd = shop.command(None, brain=brain)
    pairs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--mcp-config"]
    assert pairs == ["house.json", shop.purple_mcp_config()]


def test_the_command_has_no_purple_pair_when_there_is_no_server(shop, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PURPLE_TOOLS_SERVER", tmp_path / "nowhere" / "server.py")
    monkeypatch.setattr(shop, "house_mcp_config", lambda: None)
    assert "--mcp-config" not in shop.command(None)


def test_the_sdk_engine_gets_one_merged_file_of_only_the_servers_switched_on(shop, server, monkeypatch, tmp_path):
    house = tmp_path / "house.json"
    house.write_text(json.dumps({"mcpServers": {"skippy-house": {"type": "stdio", "command": "py", "args": ["h.py"]}}}))
    monkeypatch.setattr(shop, "house_mcp_config", lambda: str(house))
    assert shop.core_mcp_config(house=False, purple=False) is None
    both = json.loads(Path(shop.core_mcp_config(house=True, purple=True)).read_text(encoding="utf-8"))
    assert set(both["mcpServers"]) == {"skippy-house", "purple-tools"}
    only = json.loads(Path(shop.core_mcp_config(house=False, purple=True)).read_text(encoding="utf-8"))
    assert set(only["mcpServers"]) == {"purple-tools"}


def test_purple_tools_are_a_switch_in_engines_json_and_off_by_default(tmp_path):
    assert sdk.engine_options(str(tmp_path))["purple_tools"] is False
    (tmp_path / "engines.json").write_text(json.dumps({"purple_tools": True}))
    assert sdk.engine_options(str(tmp_path))["purple_tools"] is True
    (tmp_path / "engines.json").write_text(json.dumps({"purple_tools": "yes"}))
    assert sdk.engine_options(str(tmp_path))["purple_tools"] is False
