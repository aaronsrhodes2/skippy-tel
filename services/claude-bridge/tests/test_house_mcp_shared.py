"""A run uses the shared house MCP (one house, over HTTP) while it answers, and its own stdio copy otherwise."""
import json
from pathlib import Path

import workshop_engine as engine


class Up:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_live_shared_house_is_named_by_url_and_keeps_the_server_name(shop, monkeypatch):
    monkeypatch.setattr(engine, "house_http_url", lambda probe=None: "http://127.0.0.1:47861/mcp")
    cfg = json.loads(Path(shop.house_mcp_config()).read_text(encoding="utf-8"))
    assert cfg == {"mcpServers": {"skippy-house": {"type": "http", "url": "http://127.0.0.1:47861/mcp", "alwaysLoad": True}}}   # same name: mcp__skippy-house__* unchanged


def test_a_dead_shared_house_falls_back_to_the_stdio_copy(shop, monkeypatch):
    monkeypatch.setattr(engine, "house_http_url", lambda probe=None: None)
    path = shop.house_mcp_config()
    if path:                                                      # only where the satellite's venv exists
        assert json.loads(Path(path).read_text(encoding="utf-8"))["mcpServers"]["skippy-house"]["type"] == "stdio"


def test_the_probe_means_health_answering(monkeypatch):
    monkeypatch.setenv("HOUSE_MCP_URL", "http://127.0.0.1:1/mcp")
    seen = []

    def probe(url, timeout):
        seen.append(url)
        return Up()
    assert engine.house_http_url(probe) == "http://127.0.0.1:1/mcp" and seen == ["http://127.0.0.1:1/health"]
    assert engine.house_http_url(lambda url, timeout: (_ for _ in ()).throw(OSError("down"))) is None
