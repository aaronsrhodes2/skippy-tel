"""SkippyCore's Ollama shim: the Ollama API that SkippyTel's services already speak, in front of llama-swap.

flask-api, Mentat, the reporter, voice-gate and Bilby call `OLLAMA_URL` with Ollama's API. On SkippyCore the models
run in llama.cpp behind llama-swap (OpenAI-style API, one model per slot, swapped on demand), so this answers on
Ollama's port with Ollama's shapes and translates:

    GET  /, /api/version          "Ollama is running", a version
    GET  /api/tags                every name in shim.json (core/llm/models.py render): slots and their aliases
    POST /api/show                one name's details
    GET  /api/ps                  what llama-swap has loaded (GET /running), under every name of that slot
    POST /api/chat                -> /v1/chat/completions; stream = NDJSON lines ending in done: true
    POST /api/generate            -> /v1/chat/completions (system + prompt); no prompt = load, or keep_alive 0 = unload
    *    /v1/...                  passed straight through (OpenAI and Anthropic /v1/messages, streaming included)

Mapping: options.temperature/top_p/top_k/seed/stop -> the same; options.num_predict -> max_tokens; format "json" ->
response_format json_object, a schema -> json_schema; tools pass through, and tool calls come back with their
arguments as an object, as Ollama gives them. A name not in shim.json answers 404 like Ollama ("model ... not found").

keep_alive 0 never unloads a RESIDENT slot (the chat model is meant to stay loaded on the box; the reporter's
"free the GPU before a song" call is for skippy-pc's 4070, not the Halo). An on-demand slot is unloaded.

Settings: LLM_URL (llama-swap, default http://llm:8080), SHIM_NAMES (default /config/shim.json; re-read when it
changes, so a redeploy that renders new slots needs no restart), LLM_API_KEY (sent as a bearer token if set).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context

VERSION = "0.12.0-skippycore-shim"       # an Ollama-like version: some clients compare it
TIMEOUT = (5, 600)                        # connect, read: a big model's first load can take minutes
_names: dict = {"mtime": None, "path": None, "table": {}}


def llm_url() -> str:
    return os.environ.get("LLM_URL", "http://llm:8080").rstrip("/")


def _headers() -> dict:
    key = os.environ.get("LLM_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def names() -> dict:
    """name -> {slot, variant, repo, size}, from shim.json; re-read when the file changes."""
    path = Path(os.environ.get("SHIM_NAMES", "/config/shim.json"))
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _names["path"] != path or _names["mtime"] != mtime:
        data = json.loads(path.read_text(encoding="utf-8"))
        _names.update(path=path, mtime=mtime, table={m["name"]: m for m in data.get("models", [])})
    return _names["table"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _tag(name: str) -> str:
    """Ollama always shows a tag: "skippy-chat" lists as "skippy-chat:latest"."""
    return name if ":" in name else f"{name}:latest"


def _resolve(name: str | None):
    table = names()
    if not name:
        return None, None
    if name in table:
        return name, table[name]
    if name.endswith(":latest") and name[: -len(":latest")] in table:
        return name[: -len(":latest")], table[name[: -len(":latest")]]
    return None, None


def _not_found(name):
    return jsonify(error=f"model '{name}' not found"), 404


def _upstream_error(exc: Exception):
    return jsonify(error=f"llm unreachable: {type(exc).__name__}"), 502


def _details(m: dict) -> dict:
    return {"format": "gguf", "family": m["slot"], "parameter_size": "", "quantization_level": "",
            "skippycore": {"slot": m["slot"], "variant": m["variant"], "repo": m["repo"]}}


def _model_row(name: str, m: dict) -> dict:
    return {"name": _tag(name), "model": _tag(name), "modified_at": _now(), "size": m["size"],
            "digest": f"skippycore-{m['slot']}-{m['variant']}", "details": _details(m)}


# ── request mapping ──────────────────────────────────────────────────────────

OPTION_MAP = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "seed": "seed", "stop": "stop",
              "num_predict": "max_tokens", "repeat_penalty": "repeat_penalty", "min_p": "min_p"}


def _openai_body(body: dict, messages: list[dict], stream: bool) -> dict:
    out = {"model": body["model"], "messages": [_openai_message(x) for x in messages], "stream": stream}
    for k, v in (body.get("options") or {}).items():
        if k in OPTION_MAP and v is not None and not (k == "num_predict" and v < 0):
            out[OPTION_MAP[k]] = v
    fmt = body.get("format")
    if fmt == "json":
        out["response_format"] = {"type": "json_object"}
    elif isinstance(fmt, dict):
        out["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": fmt}}
    if body.get("tools"):
        out["tools"] = body["tools"]
    if stream:
        out["stream_options"] = {"include_usage": True}
    return out


def _openai_message(msg: dict) -> dict:
    out = {"role": msg.get("role", "user"), "content": msg.get("content") or ""}
    if msg.get("tool_calls"):           # Ollama gives arguments as an object; OpenAI wants a JSON string
        out["tool_calls"] = [{"id": c.get("id") or f"call_{i}", "type": "function",
                              "function": {"name": c["function"]["name"],
                                           "arguments": c["function"]["arguments"] if isinstance(c["function"].get("arguments"), str)
                                           else json.dumps(c["function"].get("arguments") or {})}}
                             for i, c in enumerate(msg["tool_calls"])]
    if msg.get("tool_call_id"):
        out["tool_call_id"] = msg["tool_call_id"]
    return out


def _ollama_tool_calls(calls) -> list[dict]:
    out = []
    for c in calls or []:
        fn = c.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {"_raw": args}
        out.append({"function": {"name": fn.get("name"), "arguments": args or {}}})
    return out


def _counts(usage: dict | None) -> dict:
    usage = usage or {}
    return {"prompt_eval_count": usage.get("prompt_tokens", 0), "eval_count": usage.get("completion_tokens", 0)}


# ── upstream calls ───────────────────────────────────────────────────────────

def _post(path: str, payload: dict, stream: bool = False):
    return requests.post(f"{llm_url()}{path}", json=payload, headers=_headers(), timeout=TIMEOUT, stream=stream)


def _sse(resp):
    """The data objects of an OpenAI-style SSE stream."""
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


def _complete(body: dict, messages: list[dict], name: str, kind: str):
    """kind: "chat" (message) or "generate" (response). One function for both endpoints, stream or not."""
    stream = body.get("stream", True)               # Ollama streams unless told not to
    started = time.monotonic()
    payload = _openai_body({**body, "model": name}, messages, stream)
    try:
        resp = _post("/v1/chat/completions", payload, stream=stream)
    except requests.RequestException as exc:
        return _upstream_error(exc)
    if resp.status_code >= 400:
        detail = resp.text[:500]
        resp.close()
        return jsonify(error=f"llm HTTP {resp.status_code}: {detail}"), 502 if resp.status_code >= 500 else resp.status_code
    model = body["model"]

    def shape(content: str, thinking: str = "", tool_calls=None, done=False, extra=None) -> dict:
        row = {"model": model, "created_at": _now()}
        if kind == "chat":
            message = {"role": "assistant", "content": content}
            if thinking:
                message["thinking"] = thinking
            if tool_calls:
                message["tool_calls"] = tool_calls
            row["message"] = message
        else:
            row["response"] = content
            if thinking:
                row["thinking"] = thinking
        row["done"] = done
        if done:
            row.update({"done_reason": "stop", "total_duration": int((time.monotonic() - started) * 1e9), **(extra or {})})
        return row

    if not stream:
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        row = shape(msg.get("content") or "", msg.get("reasoning_content") or "", _ollama_tool_calls(msg.get("tool_calls")),
                    done=True, extra=_counts(data.get("usage")))
        if choice.get("finish_reason") == "length":
            row["done_reason"] = "length"
        return jsonify(row)

    def gen():
        usage, reason, calls = None, "stop", {}
        try:
            for data in _sse(resp):
                usage = data.get("usage") or usage
                for choice in data.get("choices") or []:
                    delta = choice.get("delta") or {}
                    for c in delta.get("tool_calls") or []:     # tool calls arrive in pieces: gather, send at the end
                        slot = calls.setdefault(c.get("index", 0), {"function": {"name": "", "arguments": ""}})
                        fn = c.get("function") or {}
                        slot["function"]["name"] += fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
                    if choice.get("finish_reason") == "length":
                        reason = "length"
                    text, thinking = delta.get("content") or "", delta.get("reasoning_content") or ""
                    if text or thinking:
                        yield json.dumps(shape(text, thinking)) + "\n"
        except requests.RequestException as exc:
            yield json.dumps({"error": f"llm stream broke: {type(exc).__name__}"}) + "\n"
            return
        finally:
            resp.close()
        last = shape("", tool_calls=_ollama_tool_calls([calls[i] for i in sorted(calls)]), done=True, extra=_counts(usage))
        last["done_reason"] = reason
        yield json.dumps(last) + "\n"

    return Response(stream_with_context(gen()), mimetype="application/x-ndjson")


def _running() -> list[dict]:
    r = requests.get(f"{llm_url()}/running", headers=_headers(), timeout=5)
    r.raise_for_status()
    return r.json().get("running") or []


def _unload(slot: str) -> None:
    requests.post(f"{llm_url()}/api/models/unload/{slot}", headers=_headers(), timeout=30)


# ── the app ──────────────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def root():
        return Response("Ollama is running", mimetype="text/plain")

    @app.get("/api/version")
    def version():
        return jsonify(version=VERSION)

    @app.get("/api/tags")
    def tags():
        return jsonify(models=[_model_row(n, m) for n, m in names().items()])

    @app.post("/api/show")
    def show():
        body = request.get_json(silent=True) or {}
        asked = body.get("model") or body.get("name")
        name, m = _resolve(asked)
        if not m:
            return _not_found(asked)
        return jsonify(modelfile="", parameters="", template="", details=_details(m), model_info={},
                       capabilities=["completion", "tools"])

    @app.get("/api/ps")
    def ps():
        try:
            running = _running()
        except (requests.RequestException, ValueError) as exc:
            return _upstream_error(exc)
        loaded = {r.get("model") for r in running if r.get("state", "ready") in ("ready", "starting")}
        rows = [{**_model_row(n, m), "expires_at": "", "size_vram": m["size"]}
                for n, m in names().items() if m["slot"] in loaded and n == m["slot"]]
        return jsonify(models=rows)

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        name, m = _resolve(body.get("model"))
        if not m:
            return _not_found(body.get("model"))
        if not body.get("messages"):
            return _load_or_unload(body, m)
        return _complete(body, body["messages"], name, "chat")

    @app.post("/api/generate")
    def generate():
        body = request.get_json(silent=True) or {}
        name, m = _resolve(body.get("model"))
        if not m:
            return _not_found(body.get("model"))
        if not body.get("prompt"):
            return _load_or_unload(body, m)
        messages = ([{"role": "system", "content": body["system"]}] if body.get("system") else []) + \
                   [{"role": "user", "content": body["prompt"]}]
        return _complete(body, messages, name, "generate")

    def _load_or_unload(body: dict, m: dict):
        """Ollama's empty request: load the model, or with keep_alive 0 unload it (never a resident slot)."""
        unload = body.get("keep_alive") in (0, "0", "0s", "0m")
        reason = "load"
        if unload:
            reason = "unload"
            if not m.get("resident", m["slot"] == "chat"):
                try:
                    _unload(m["slot"])
                except requests.RequestException:
                    pass                        # on-demand slots drop after their idle ttl anyway
        else:
            try:
                requests.get(f"{llm_url()}/upstream/{m['slot']}/health", headers=_headers(), timeout=TIMEOUT)
            except requests.RequestException as exc:
                return _upstream_error(exc)
        return jsonify(model=body["model"], created_at=_now(), response="", done=True, done_reason=reason)

    @app.route("/v1/<path:rest>", methods=["GET", "POST"])
    def passthrough(rest):
        """OpenAI- and Anthropic-style calls go straight to llama-swap (which routes by the body's model)."""
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept", "anthropic-version",
                                                                         "anthropic-beta", "x-api-key")}
        hdrs.update(_headers())
        try:
            resp = requests.request(request.method, f"{llm_url()}/v1/{rest}", data=request.get_data(), headers=hdrs,
                                    params=request.args, timeout=TIMEOUT, stream=True)
        except requests.RequestException as exc:
            return _upstream_error(exc)

        def body():
            try:
                yield from resp.iter_content(chunk_size=None)
            finally:
                resp.close()
        return Response(stream_with_context(body()), status=resp.status_code,
                        content_type=resp.headers.get("content-type", "application/json"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "11434")), threaded=True)
