"""The Ollama shim speaks Ollama to SkippyTel's callers and OpenAI to llama-swap: the shapes clients/ollama.py,
flask-api /health, Mentat, voice-gate and the reporter read, plus the rule that a resident slot is never unloaded."""
import json

import pytest

from conftest import load
from fake_llm import FakeLLM

shim = load("ollama-shim/shim.py", "core_ollama_shim")

NAMES = {"models": [
    {"name": "chat", "slot": "chat", "variant": "standin", "repo": "Qwen/Qwen3-0.6B-GGUF", "size": 10, "resident": True},
    {"name": "qwen3:8b", "slot": "chat", "variant": "standin", "repo": "Qwen/Qwen3-0.6B-GGUF", "size": 10, "resident": True},
    {"name": "coder", "slot": "coder", "variant": "full", "repo": "unsloth/X", "size": 20, "resident": False},
    {"name": "broken", "slot": "broken", "variant": "standin", "repo": "x/y", "size": 1, "resident": False},
]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    llm = FakeLLM()
    names = tmp_path / "shim.json"
    names.write_text(json.dumps(NAMES))
    monkeypatch.setenv("LLM_URL", llm.url)
    monkeypatch.setenv("SHIM_NAMES", str(names))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    yield shim.create_app().test_client(), llm, names
    llm.close()


def _ndjson(resp):
    return [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]


def test_root_version_and_tags(env):
    client, _, _ = env
    assert client.get("/").get_data(as_text=True) == "Ollama is running"
    assert client.get("/api/version").get_json()["version"]
    names = [m["name"] for m in client.get("/api/tags").get_json()["models"]]
    assert "qwen3:8b" in names and "chat:latest" in names, "flask-api /health looks for the default name or name:latest"


def test_tags_follow_a_rerendered_names_file(env):
    client, _, names = env
    import os
    names.write_text(json.dumps({"models": NAMES["models"][:1]}))
    os.utime(names, (1, 1))
    assert [m["name"] for m in client.get("/api/tags").get_json()["models"]] == ["chat:latest"]


def test_chat_not_streamed_is_what_clients_ollama_reads(env):
    client, llm, _ = env
    r = client.post("/api/chat", json={"model": "qwen3:8b", "stream": False,
                                       "messages": [{"role": "user", "content": "hi"}],
                                       "options": {"temperature": 0.2, "num_predict": 50}, "format": "json"})
    body = r.get_json()
    assert r.status_code == 200 and body["message"]["content"] == "Hello, Captain." and body["done"] is True
    assert body["message"]["thinking"] == "hmm" and body["eval_count"] == 3 and body["prompt_eval_count"] == 7
    sent = llm.calls[-1][1]
    assert sent["model"] == "qwen3:8b" and sent["temperature"] == 0.2 and sent["max_tokens"] == 50
    assert sent["response_format"] == {"type": "json_object"} and sent["stream"] is False


def test_chat_streams_ndjson_ending_in_done(env):
    client, _, _ = env
    rows = _ndjson(client.post("/api/chat", json={"model": "chat", "messages": [{"role": "user", "content": "hi"}]}))
    assert "".join(r["message"]["content"] for r in rows).strip() == "Hello, Captain."
    assert [r["done"] for r in rows][-1] is True and not any(r["done"] for r in rows[:-1])
    assert rows[-1]["eval_count"] == 3


def test_tool_calls_come_back_as_objects(env):
    client, llm, _ = env
    llm.tool_calls = [{"id": "c1", "type": "function", "function": {"name": "look", "arguments": '{"a": 1}'}}]
    tools = [{"type": "function", "function": {"name": "look", "parameters": {}}}]
    body = client.post("/api/chat", json={"model": "chat", "stream": False, "tools": tools,
                                          "messages": [{"role": "user", "content": "x"}]}).get_json()
    assert body["message"]["tool_calls"] == [{"function": {"name": "look", "arguments": {"a": 1}}}]
    assert llm.calls[-1][1]["tools"] == tools
    rows = _ndjson(client.post("/api/chat", json={"model": "chat", "tools": tools, "messages": [{"role": "user", "content": "x"}]}))
    assert rows[-1]["message"]["tool_calls"] == [{"function": {"name": "look", "arguments": {"a": 1}}}]


def test_history_with_tool_calls_goes_up_as_openai(env):
    client, llm, _ = env
    history = [{"role": "user", "content": "x"},
               {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "look", "arguments": {"a": 1}}}]},
               {"role": "tool", "content": "42"}]
    client.post("/api/chat", json={"model": "chat", "stream": False, "messages": history})
    sent = llm.calls[-1][1]["messages"]
    assert sent[1]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}' and sent[2] == {"role": "tool", "content": "42"}


def test_generate_with_system_and_schema(env):
    client, llm, _ = env
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    body = client.post("/api/generate", json={"model": "chat", "prompt": "summarise", "system": "be brief",
                                              "stream": False, "format": schema}).get_json()
    assert body["response"] == "Hello, Captain." and body["done"] is True
    sent = llm.calls[-1][1]
    assert sent["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "summarise"}]
    assert sent["response_format"]["json_schema"]["schema"] == schema
    rows = _ndjson(client.post("/api/generate", json={"model": "chat", "prompt": "x"}))
    assert "".join(r["response"] for r in rows).strip() == "Hello, Captain." and rows[-1]["done"] is True


def test_unknown_model_is_a_404_like_ollama(env):
    client, llm, _ = env
    r = client.post("/api/chat", json={"model": "llama3", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 404 and "not found" in r.get_json()["error"]
    assert client.post("/api/show", json={"model": "nope"}).status_code == 404
    assert llm.calls == []


def test_upstream_trouble_is_an_error_not_a_hang(env, monkeypatch):
    client, _, _ = env
    assert client.post("/api/chat", json={"model": "broken", "stream": False,
                                          "messages": [{"role": "user", "content": "x"}]}).status_code == 502
    monkeypatch.setenv("LLM_URL", "http://127.0.0.1:1")
    r = client.post("/api/chat", json={"model": "chat", "stream": False, "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 502 and "unreachable" in r.get_json()["error"]


def test_keep_alive_zero_unloads_on_demand_slots_never_the_resident_one(env):
    """The reporter frees the GPU before a song with exactly this call, for every model /api/ps lists."""
    client, llm, _ = env
    llm.running = [{"model": "chat", "state": "ready"}, {"model": "coder", "state": "ready"}]
    loaded = [m["name"] for m in client.get("/api/ps").get_json()["models"]]
    assert loaded == ["chat:latest", "coder:latest"]
    for name in loaded:
        r = client.post("/api/generate", json={"model": name, "keep_alive": 0})
        assert r.get_json()["done_reason"] == "unload"
    assert [c for c in llm.calls if c[0] == "unload"] == [("unload", "coder")]


def test_an_empty_request_loads(env):
    client, llm, _ = env
    assert client.post("/api/generate", json={"model": "coder"}).get_json()["done_reason"] == "load"
    assert llm.calls[-1] == ("load", "coder")


def test_v1_passes_through_with_anthropic_headers_and_the_key(env, monkeypatch):
    client, llm, _ = env
    monkeypatch.setenv("LLM_API_KEY", "sekrit")
    r = client.post("/v1/messages", json={"model": "coder", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]},
                    headers={"anthropic-version": "2023-06-01"})
    assert r.get_json()["content"][0]["text"] == "anthropic ok"
    kind, body, headers = llm.calls[-1]
    assert body["model"] == "coder" and headers.get("Anthropic-Version") == "2023-06-01"
    assert headers.get("Authorization") == "Bearer sekrit"
