#!/usr/bin/env python3
"""The portal's door to skippy-core, checked the way the portal uses it: the Anthropic SDK with another base_url.

    python core/llm/door_check.py [--model chat] [--think] [--url URL]

SKIPPY_CORE_URL and SKIPPY_CORE_API_KEY come from the environment, else from the vault (VAULT_ENV, default
G:/My Drive/vault/.env on skippy-pc).

Qwen3 thinks before every answer unless told not to, and llama-server ignores Anthropic's `thinking: disabled`; what
works is llama.cpp's own field, `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` (5 s -> 0.3 s for a
short hello). That is the default here; --think checks the thinking mode instead (the reply then starts with a
thinking block, and max_tokens must leave room for it).

Four checks, each PASS/FAIL: no key is refused (401), a plain reply, a streamed reply (SSE text deltas), and a tool
round trip (the model asks for ask_mentat with a tool_use block, takes a tool_result and answers from it), streamed.
Needs `pip install anthropic` (skippy-pc has it). Never prints the key. Exit 1 if anything failed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import anthropic

TOOL = {
    "name": "ask_mentat",
    "description": "Ask Mentat, the physics engine, to compute a physical quantity. Use it for every physics number.",
    "input_schema": {"type": "object", "properties": {"question": {"type": "string", "description": "the question"}},
                     "required": ["question"]},
}
ASK = "What is the escape velocity of Earth? Use the ask_mentat tool."
MENTAT = '{"value": 11186, "unit": "m/s", "source": "mentat escape_velocity(M_earth, R_earth)"}'


def from_vault(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    try:
        lines = open(os.environ.get("VAULT_ENV", "G:/My Drive/vault/.env"), encoding="utf-8").read().splitlines()
    except OSError:
        return ""
    return next((ln.split("=", 1)[1].strip().strip('"') for ln in lines if ln.startswith(name + "=")), "")


def text_of(msg) -> str:
    return "".join(b.text for b in msg.content if b.type == "text")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="chat")
    ap.add_argument("--url", default=from_vault("SKIPPY_CORE_URL"))
    ap.add_argument("--think", action="store_true", help="leave Qwen3's thinking on")
    a = ap.parse_args(argv)
    key = from_vault("SKIPPY_CORE_API_KEY")
    if not a.url or not key:
        print("set SKIPPY_CORE_URL and SKIPPY_CORE_API_KEY", file=sys.stderr)
        return 2
    client = anthropic.Anthropic(base_url=a.url, api_key=key, timeout=600, max_retries=0)
    kw = {} if a.think else {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    room = 4096 if a.think else 400
    results = []

    def check(name, fn):
        t = time.monotonic()
        try:
            detail = fn()
            results.append(True)
            print(f"PASS  {name:10} {time.monotonic() - t:5.1f}s  {detail}")
        except Exception as e:          # noqa: BLE001: report every failure the same way
            results.append(False)
            print(f"FAIL  {name:10} {time.monotonic() - t:5.1f}s  {type(e).__name__}: {str(e)[:300]}")

    def no_key():
        try:
            anthropic.Anthropic(base_url=a.url, api_key="wrong", max_retries=0).messages.create(
                model=a.model, max_tokens=1, messages=[{"role": "user", "content": "hi"}])
        except anthropic.AuthenticationError:
            return "401"
        raise AssertionError("a wrong key was answered")

    def plain():
        m = client.messages.create(model=a.model, max_tokens=room, **kw,
                                   messages=[{"role": "user", "content": "Say hello to the Captain in five words."}])
        assert m.type == "message" and text_of(m).strip(), m
        return repr(text_of(m).strip()[:60])

    def stream():
        deltas = []
        with client.messages.stream(model=a.model, max_tokens=room, **kw,
                                    messages=[{"role": "user", "content": "Count from one to five in words."}]) as s:
            for ev in s:
                if ev.type == "content_block_delta" and ev.delta.type == "text_delta":
                    deltas.append(ev.delta.text)
            final = s.get_final_message()
        assert len(deltas) > 1, f"{len(deltas)} text deltas"
        return f"{len(deltas)} text deltas, stop_reason {final.stop_reason}"

    def tools():
        msgs = [{"role": "user", "content": ASK}]
        with client.messages.stream(model=a.model, max_tokens=room + 600, tools=[TOOL], messages=msgs, **kw) as s:
            first = s.get_final_message()
        uses = [b for b in first.content if b.type == "tool_use"]
        assert first.stop_reason == "tool_use" and uses and uses[0].name == "ask_mentat", first
        assert isinstance(uses[0].input, dict) and uses[0].input.get("question"), uses[0].input
        msgs += [{"role": "assistant", "content": first.content},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": uses[0].id, "content": MENTAT}]}]
        with client.messages.stream(model=a.model, max_tokens=room + 600, tools=[TOOL], messages=msgs, **kw) as s:
            second = s.get_final_message()
        said = text_of(second)
        assert second.stop_reason == "end_turn" and ("11186" in said.replace(",", "") or "11.2" in said or "11.19" in said), said
        return f"tool_use {uses[0].input!r} -> {said.strip()[:80]!r}"

    for name, fn in (("no key", no_key), ("plain", plain), ("stream", stream), ("tool use", tools)):
        check(name, fn)
    print("door: ALL PASSED" if all(results) else "door: FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
