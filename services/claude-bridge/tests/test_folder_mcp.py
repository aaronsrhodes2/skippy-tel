"""A folder opened in place brings its own .mcp.json servers to purple's run (the Factorio mod's skippy-copilot), and nothing else does."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import workshop_engine as we  # noqa: E402


def shop(tmp_path):
    s = we.Workshop.__new__(we.Workshop)                 # only the methods under test: no repo, no projects file
    s.state_dir = str(tmp_path / "state")
    return s


def folder(tmp_path, servers, pid="f-ai-factorio"):
    root = tmp_path / "AI-factorio"
    root.mkdir()
    if servers is not None:
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return SimpleNamespace(id=pid, clone=str(root))


def test_a_folders_servers_start_in_the_folder_and_load_at_once(tmp_path):
    p = folder(tmp_path, {"skippy-copilot": {"command": "python", "args": ["-m", "copilot.mcp_server"]}})
    got = shop(tmp_path).folder_mcp_servers(p)
    assert got["skippy-copilot"]["args"] == ["-m", "copilot.mcp_server"]
    assert got["skippy-copilot"]["cwd"] == Path(p.clone).as_posix() and got["skippy-copilot"]["alwaysLoad"] is True


def test_its_own_cwd_and_http_servers_are_kept_as_written(tmp_path):
    p = folder(tmp_path, {"a": {"command": "x", "cwd": "C:/elsewhere"}, "b": {"type": "http", "url": "http://127.0.0.1:1/mcp"}})
    got = shop(tmp_path).folder_mcp_servers(p)
    assert got["a"]["cwd"] == "C:/elsewhere" and "cwd" not in got["b"]


def test_a_registered_project_or_a_folder_without_the_file_brings_nothing(tmp_path):
    s = shop(tmp_path)
    assert s.folder_mcp_servers(folder(tmp_path, {"x": {"command": "y"}}, pid="skippytel")) == {}
    assert s.folder_mcp_servers(SimpleNamespace(id="f-empty", clone=str(tmp_path / "nowhere"))) == {}
    assert s.folder_mcp_config(SimpleNamespace(id="f-empty", clone=str(tmp_path / "nowhere"))) is None


def test_the_config_file_and_the_core_config_carry_them(tmp_path):
    s = shop(tmp_path)
    p = folder(tmp_path, {"skippy-copilot": {"command": "python", "args": ["-m", "copilot.mcp_server"]}})
    path = s.folder_mcp_config(p)
    assert "skippy-copilot" in json.loads(Path(path).read_text(encoding="utf-8"))["mcpServers"]
    core = s.core_mcp_config(False, False, None, p)
    assert "skippy-copilot" in json.loads(Path(core).read_text(encoding="utf-8"))["mcpServers"]
