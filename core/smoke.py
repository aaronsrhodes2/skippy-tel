#!/usr/bin/env python3
"""SkippyCore's smoke test: is the stack answering the way SkippyTel's callers need? CI, the simulator and the
deployer all run this one file. Standard library only (it runs on the bare host).

    python3 core/smoke.py [--wait 600] [--json]

Checks, in order (each prints ok/FAIL and its time; exit 1 if any fails):
  tags        the shim's /api/tags lists the default model (OLLAMA_MODEL, default qwen3:8b)
  chat        /api/chat, not streamed: a reply in message.content (what flask-api's clients/ollama.py reads)
  stream      /api/chat streamed: NDJSON lines, the last one done: true
  json        /api/generate with format "json": the response parses as JSON
  slots       llama-swap /v1/chat/completions on every slot in shim.json; the on-demand ones swap (the last one
              loaded is the only on-demand slot running afterwards)
  messages    Anthropic /v1/messages on the coder slot (the coding fallback: Claude Code with ANTHROPIC_BASE_URL)
  health      flask-api /health says models.ollama.default_present
  intent      flask-api /intent/unmatched gets Skippy's reply

URLs: SMOKE_SHIM (http://127.0.0.1:11434), SMOKE_LLM (http://127.0.0.1:8080), SMOKE_API (http://127.0.0.1:3003);
the slot list comes from ${CORE_STATE}/llm/shim.json (or SMOKE_NAMES).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SHIM = os.environ.get("SMOKE_SHIM", "http://127.0.0.1:11434").rstrip("/")
LLM = os.environ.get("SMOKE_LLM", "http://127.0.0.1:8080").rstrip("/")
API = os.environ.get("SMOKE_API", "http://127.0.0.1:3003").rstrip("/")
DEFAULT = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT", "300"))   # CPU stand-ins, cold loads: generous


class Fail(Exception):
    pass


def call(method: str, url: str, body: dict | None = None, *, timeout: float = TIMEOUT, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    key = os.environ.get("LLM_API_KEY")
    auth = {"Authorization": f"Bearer {key}"} if key and url.startswith(LLM) else {}
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **auth, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def call_json(method: str, url: str, body: dict | None = None, **kw) -> dict:
    status, text = call(method, url, body, **kw)
    if status >= 400:
        raise Fail(f"{method} {url}: HTTP {status}: {text[:300]}")
    try:
        return json.loads(text)
    except ValueError:
        raise Fail(f"{method} {url}: not JSON: {text[:300]}") from None


def slots() -> list[dict]:
    path = Path(os.environ.get("SMOKE_NAMES") or Path(os.environ.get("CORE_STATE", "/var/lib/skippy-core")) / "llm" / "shim.json")
    rows = json.loads(path.read_text(encoding="utf-8"))["models"]
    seen, out = set(), []
    for r in rows:
        if r["name"] == r["slot"] and r["slot"] not in seen:
            seen.add(r["slot"])
            out.append(r)
    return out


USER = [{"role": "user", "content": "Say hello to the Captain in five words or fewer."}]
CAP = {"num_predict": 256}      # generous for five words, but a rambling stand-in can't run its context out


def check_tags():
    names = [m["name"] for m in call_json("GET", f"{SHIM}/api/tags")["models"]]
    if DEFAULT not in names and f"{DEFAULT}:latest" not in names:
        raise Fail(f"{DEFAULT} not in /api/tags: {names}")
    return f"{len(names)} names"


def check_chat():
    body = call_json("POST", f"{SHIM}/api/chat", {"model": DEFAULT, "stream": False, "messages": USER, "options": CAP})
    text = (body.get("message") or {}).get("content", "")
    if not text.strip() or body.get("done") is not True:
        raise Fail(f"no reply: {json.dumps(body)[:300]}")
    return repr(text.strip()[:60])


def check_stream():
    status, text = call("POST", f"{SHIM}/api/chat", {"model": DEFAULT, "messages": USER, "options": CAP})
    if status != 200:
        raise Fail(f"HTTP {status}: {text[:300]}")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows or rows[-1].get("done") is not True or any(r.get("done") for r in rows[:-1]):
        raise Fail(f"bad NDJSON stream ({len(rows)} lines)")
    said = "".join((r.get("message") or {}).get("content", "") for r in rows)
    if not said.strip():
        raise Fail("stream carried no text")
    return f"{len(rows)} lines"


def check_json():
    body = call_json("POST", f"{SHIM}/api/generate", {
        "model": DEFAULT, "stream": False, "format": "json",
        "prompt": 'Reply with a JSON object with one key "ok" set to true.', "options": {"num_predict": 400}})
    try:
        json.loads(body.get("response", ""))
    except ValueError:
        raise Fail(f"not JSON: {body.get('response', '')[:200]!r}") from None
    return "parses"


def check_slots():
    rows = slots()
    for r in rows:
        body = call_json("POST", f"{LLM}/v1/chat/completions",
                         {"model": r["slot"], "messages": USER, "max_tokens": 16})
        if not body.get("choices"):
            raise Fail(f"{r['slot']}: no choices: {json.dumps(body)[:200]}")
    on_demand = [r["slot"] for r in rows if not r.get("resident")]
    if len(on_demand) >= 2:
        running = {m.get("model") for m in call_json("GET", f"{LLM}/running").get("running", [])
                   if m.get("state", "ready") == "ready"}
        loaded = [s for s in on_demand if s in running]
        if loaded != [on_demand[-1]]:
            raise Fail(f"on-demand slots should swap: running {sorted(running)}, expected only {on_demand[-1]} of {on_demand}")
    return ", ".join(r["slot"] for r in rows)


def check_messages():
    coder = next((r["slot"] for r in slots() if r["slot"] == "coder"), None)
    if not coder:
        return "no coder slot: skipped"
    body = call_json("POST", f"{LLM}/v1/messages", {"model": coder, "max_tokens": 16, "messages": USER},
                     headers={"anthropic-version": "2023-06-01"})
    if body.get("type") != "message" or not body.get("content"):
        raise Fail(f"not an Anthropic message: {json.dumps(body)[:300]}")
    return "ok"


def check_auth():
    """With LLM_API_KEY set, the llm port must refuse a request without it (the Funnel puts this port on the internet)."""
    if not os.environ.get("LLM_API_KEY"):
        return "no key: open port (tailnet-only boxes, the simulator)"
    status, _ = call("POST", f"{LLM}/v1/chat/completions", {"model": "chat", "messages": USER, "max_tokens": 1},
                     headers={"Authorization": "Bearer wrong"})
    if status != 401:
        raise Fail(f"a wrong key got HTTP {status}, expected 401")
    return "401 without the key"


def check_health():
    body = call_json("GET", f"{API}/health", timeout=30)
    ollama = (body.get("models") or {}).get("ollama") or {}
    if not ollama.get("default_present"):
        raise Fail(f"flask-api says the default model is missing: {ollama}")
    return f"default {ollama.get('default')}"


def check_intent():
    body = call_json("POST", f"{API}/intent/unmatched", {"text": "Hello Skippy, are you awake?"})
    if not (body.get("reply") or "").strip():
        raise Fail(f"no reply: {json.dumps(body)[:300]}")
    return repr(body["reply"].strip()[:60])


CHECKS = [("tags", check_tags), ("chat", check_chat), ("stream", check_stream), ("json", check_json),
          ("slots", check_slots), ("messages", check_messages), ("auth", check_auth), ("health", check_health), ("intent", check_intent)]


def wait_ready(seconds: float) -> None:
    """Until the shim and flask-api answer at all (containers just started; the chat model is loading)."""
    deadline = time.monotonic() + seconds
    last = ""
    while True:
        try:
            if call("GET", f"{SHIM}/api/tags", timeout=5)[0] == 200 and call("GET", f"{API}/health", timeout=10)[0] == 200:
                return
        except (OSError, urllib.error.URLError) as e:
            last = str(e)
        left = deadline - time.monotonic()
        if left <= 0:
            raise Fail(f"stack not answering after {seconds:.0f}s ({last})")
        time.sleep(min(3, left))


def run(only: list[str] | None = None, wait: float = 0) -> list[dict]:
    results = []
    if wait:
        t0 = time.monotonic()
        try:
            wait_ready(wait)
            results.append({"check": "ready", "ok": True, "detail": "", "s": round(time.monotonic() - t0, 1)})
        except Fail as e:
            return [{"check": "ready", "ok": False, "detail": str(e), "s": round(time.monotonic() - t0, 1)}]
    for name, fn in CHECKS:
        if only and name not in only:
            continue
        t0 = time.monotonic()
        try:
            detail, ok = fn(), True
        except (Fail, OSError, urllib.error.URLError, ValueError, KeyError) as e:
            detail, ok = f"{type(e).__name__}: {e}" if not isinstance(e, Fail) else str(e), False
        results.append({"check": name, "ok": ok, "detail": detail, "s": round(time.monotonic() - t0, 1)})
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for the stack to answer first")
    ap.add_argument("--only", default="", help="comma list of checks")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    results = run([s for s in a.only.split(",") if s] or None, a.wait)
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['check']:9} {r['s']:6.1f}s  {r['detail']}", flush=True)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
