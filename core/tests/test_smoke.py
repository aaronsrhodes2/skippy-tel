"""The smoke test itself: it passes against a stack that answers right, and names the check that breaks."""
import json
import threading

import pytest
from flask import Flask, jsonify
from werkzeug.serving import make_server

from conftest import load
from fake_llm import FakeLLM

shim_mod = load("ollama-shim/shim.py", "core_ollama_shim")
smoke = load("smoke.py", "core_smoke")

NAMES = {"models": [
    {"name": "chat", "slot": "chat", "variant": "standin", "repo": "a/b", "size": 1, "resident": True},
    {"name": "qwen3:8b", "slot": "chat", "variant": "standin", "repo": "a/b", "size": 1, "resident": True},
    {"name": "general", "slot": "general", "variant": "standin", "repo": "a/c", "size": 1, "resident": False},
    {"name": "coder", "slot": "coder", "variant": "standin", "repo": "a/d", "size": 1, "resident": False},
]}


def _serve(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def stack(tmp_path, monkeypatch):
    llm = FakeLLM()
    llm.reply = '{"ok": true}'
    llm.running = [{"model": "chat", "state": "ready"}, {"model": "coder", "state": "ready"}]
    names = tmp_path / "shim.json"
    names.write_text(json.dumps(NAMES))
    monkeypatch.setenv("LLM_URL", llm.url)
    monkeypatch.setenv("SHIM_NAMES", str(names))
    shim_server, shim_url = _serve(shim_mod.create_app())
    api_state = {"present": True}
    api = Flask("fake_api")

    @api.get("/health")
    def health():
        return jsonify(status="ok", models={"ollama": {"default": "qwen3:8b", "default_present": api_state["present"]}})

    @api.post("/intent/unmatched")
    def intent():
        return jsonify(reply="I am Skippy the Magnificent.")

    api_server, api_url = _serve(api)
    monkeypatch.setattr(smoke, "SHIM", shim_url)
    monkeypatch.setattr(smoke, "LLM", llm.url)
    monkeypatch.setattr(smoke, "API", api_url)
    monkeypatch.setenv("SMOKE_NAMES", str(names))
    yield llm, api_state
    for s in (shim_server, api_server):
        s.shutdown()
    llm.close()


def test_a_healthy_stack_passes(stack, capsys):
    assert smoke.main(["--wait", "5"]) == 0
    out = capsys.readouterr().out
    for name, _ in smoke.CHECKS:
        assert f"ok   {name}" in out


def test_each_failure_is_named(stack):
    llm, api_state = stack
    llm.running.append({"model": "general", "state": "ready"})        # the swap didn't happen
    api_state["present"] = False
    llm.reply = "not json at all"
    failed = {r["check"] for r in smoke.run() if not r["ok"]}
    assert failed == {"json", "slots", "health"}


def test_a_dead_stack_fails_the_wait(monkeypatch):
    monkeypatch.setattr(smoke, "SHIM", "http://127.0.0.1:1")
    results = smoke.run(wait=0.1)
    assert results[0]["check"] == "ready" and not results[0]["ok"] and len(results) == 1
