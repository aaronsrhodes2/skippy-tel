"""The Captain's private MCP servers, for the /me page: which ones he has configured for Claude (user scope) and whether each one works.

LIST reads `~/.claude.json` (the file `claude mcp add --scope user` writes) and returns only names and how each is reached: the transport,
the program's file name or the URL's host. Never arguments, environment variables, headers or paths: those hold keys.
TEST starts one named server exactly as configured, does the MCP handshake (initialize, then tools/list) and stops it. Only a name that is in
the file can be tested; nothing here runs anything the Captain did not already configure for himself.
Both are behind the bridge's home token (bridge.py `_workshop_ok`).
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

PROTOCOL = "2025-03-26"
TEST_SECONDS = 25.0
MAX_TOOLS = 80


def config_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG") or Path.home() / ".claude.json")


def _load() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return {str(k): v for k, v in servers.items() if isinstance(v, dict)} if isinstance(servers, dict) else {}


def _transport(cfg: dict) -> str:
    kind = str(cfg.get("type") or "").lower()
    if kind in ("http", "sse", "stdio"):
        return kind
    return "http" if cfg.get("url") else "stdio"


def listing() -> dict:
    """{servers: [{name, transport, via}]}: how each is reached, no secrets."""
    out = []
    for name, cfg in sorted(_load().items()):
        transport = _transport(cfg)
        if transport == "stdio":
            via = Path(str(cfg.get("command") or "")).name or "?"
        else:
            via = urlparse(str(cfg.get("url") or "")).hostname or "?"
        out.append({"name": name, "transport": transport, "via": via})
    return {"servers": out}


class _Stdio:
    """One MCP stdio server: newline-delimited JSON-RPC on its stdin/stdout."""

    def __init__(self, cfg: dict):
        command = str(cfg.get("command") or "")
        exe = shutil.which(command) or command
        env = {**os.environ, **{str(k): str(v) for k, v in (cfg.get("env") or {}).items()}}
        self.proc = subprocess.Popen([exe, *[str(a) for a in cfg.get("args") or []]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self) -> None:
        for raw in self.proc.stdout:
            self.lines.put(raw)
        self.lines.put(None)

    def send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def call(self, method: str, params: dict | None, deadline: float) -> dict:
        self.next_id += 1
        want = self.next_id
        self.send({"jsonrpc": "2.0", "id": want, "method": method, "params": params or {}})
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"{method}: no answer in time")
            try:
                raw = self.lines.get(timeout=left)
            except queue.Empty:
                raise TimeoutError(f"{method}: no answer in time") from None
            if raw is None:
                raise ConnectionError("the server exited")
            try:
                msg = json.loads(raw)
            except ValueError:
                continue                                    # a stray log line on stdout
            if isinstance(msg, dict) and msg.get("id") == want:
                if msg.get("error"):
                    raise RuntimeError(str((msg["error"] or {}).get("message") or "error")[:200])
                return msg.get("result") or {}

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


def _test_stdio(cfg: dict, deadline: float) -> dict:
    server = _Stdio(cfg)
    try:
        init = server.call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "skippy-me", "version": "1"}}, deadline)
        server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = server.call("tools/list", {}, deadline).get("tools") or []
        info = init.get("serverInfo") or {}
        return {"server": f"{info.get('name', '')} {info.get('version', '')}".strip(), "tools": [str(t.get("name")) for t in tools][:MAX_TOOLS]}
    finally:
        server.close()


def _post(url: str, body: dict, headers: dict, timeout: float):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
        sid = r.headers.get("Mcp-Session-Id")
    if text.lstrip().startswith("event:") or "\ndata:" in text or text.startswith("data:"):
        text = next((ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")), "{}")
    return json.loads(text or "{}"), sid


def _test_http(cfg: dict, deadline: float) -> dict:
    url = str(cfg.get("url") or "")
    headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}
    init, sid = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "skippy-me", "version": "1"}}}, headers, max(1.0, deadline - time.monotonic()))
    if sid:
        headers["Mcp-Session-Id"] = sid
    try:
        _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, 5)
    except (urllib.error.URLError, ValueError, OSError):
        pass                                                # a notification has no reply to wait for
    res, _ = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers, max(1.0, deadline - time.monotonic()))
    info = (init.get("result") or {}).get("serverInfo") or {}
    tools = (res.get("result") or {}).get("tools") or []
    return {"server": f"{info.get('name', '')} {info.get('version', '')}".strip(), "tools": [str(t.get("name")) for t in tools][:MAX_TOOLS]}


def test(name: str, seconds: float = TEST_SECONDS) -> tuple[int, dict]:
    """(HTTP status, body) for one server: {ok, ms, server, tools} or {ok: false, error}. Unknown names are refused, nothing is started."""
    cfg = _load().get(name)
    if cfg is None:
        return 404, {"ok": False, "error": "That isn't one of your configured MCP servers."}
    started = time.monotonic()
    try:
        deadline = started + seconds
        got = _test_http(cfg, deadline) if _transport(cfg) != "stdio" else _test_stdio(cfg, deadline)
    except (OSError, ValueError, TimeoutError, ConnectionError, RuntimeError, urllib.error.URLError) as exc:
        return 200, {"ok": False, "ms": int((time.monotonic() - started) * 1000), "error": (str(exc) or type(exc).__name__)[:200]}
    return 200, {"ok": True, "ms": int((time.monotonic() - started) * 1000), **got}
