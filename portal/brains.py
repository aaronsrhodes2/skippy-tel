"""Which model answers in Skippy's chat, and who may pick it (the Captain's rule: guests get Skippy Core, only he gets paid models).

  core          Skippy Core: the local model on skippy-core (llama-swap's chat slot) behind its Tailscale Funnel and API key.
                Everyone's brain, and the one that does Barney Style. llama-swap speaks Anthropic's /v1/messages, tool calls
                included, so it is the Anthropic SDK with another base_url (chat.SkippyChat), not a second code path.
  haiku/sonnet/opus   Claude, on the portal's ANTHROPIC_API_KEY. The Captain only.
  gemini-free / gemini   Gemini on GEMINI_FREE_API_KEY / GEMINI_API_KEY, through Google's OpenAI-compatible endpoint. The Captain only.

A brain is offered only when its keys are set. The server decides, never the page: a guest's request for any other brain is
refused, and Barney Style always runs on Skippy Core. Keys are read from the environment here and never go into app.config
(templates see app.config).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import requests

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


@dataclass(frozen=True)
class Brain:
    id: str
    label: str
    glyph: str            # the picker shows a glyph per brain (templates/_glyphs.html)
    api: str              # "anthropic" (Claude, Skippy Core) or "openai" (Gemini)
    model: str
    key_env: str
    base_env: str = ""    # env var holding the base URL ("" = the API's own)
    base: str = ""        # a fixed base URL
    paid: bool = True
    admin_only: bool = True

    def key(self) -> str:
        v = os.environ.get(self.key_env, "").strip()
        return "" if v == "unset" else v

    def base_url(self) -> str:
        if self.base_env:
            v = os.environ.get(self.base_env, "").strip().rstrip("/")
            return "" if v == "unset" else v
        return self.base

    def ready(self) -> bool:
        return bool(self.key()) and (not self.base_env or bool(self.base_url()))

    def view(self) -> dict:
        return {"id": self.id, "label": self.label, "glyph": self.glyph, "paid": self.paid, "ready": self.ready()}


def _gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest"


BRAINS: dict[str, Brain] = {b.id: b for b in (
    Brain("core", "Skippy Core", "core", "anthropic", "chat", "SKIPPY_CORE_API_KEY", base_env="SKIPPY_CORE_URL",
          paid=False, admin_only=False),
    Brain("haiku", "Claude Haiku", "claude", "anthropic", "claude-haiku-5-5", "ANTHROPIC_API_KEY"),
    Brain("sonnet", "Claude Sonnet", "claude", "anthropic", "claude-sonnet-5-5", "ANTHROPIC_API_KEY"),
    Brain("opus", "Claude Opus", "claude", "anthropic", "claude-opus-5-5", "ANTHROPIC_API_KEY"),
    Brain("gemini-free", "Gemini (free)", "gemini", "openai", "", "GEMINI_FREE_API_KEY", base=GEMINI_BASE, paid=False),
    Brain("gemini", "Gemini", "gemini", "openai", "", "GEMINI_API_KEY", base=GEMINI_BASE),
)}
DEFAULT = "core"
BARNEY = "core"           # Barney Style is always Skippy Core (the Captain's rule), for him as well


def model_of(brain: Brain) -> str:
    """The model id sent. Skippy Core's is a llama-swap slot name: "chat" unless SKIPPY_CORE_MODEL picks another slot
    (e.g. "coder" while the full chat model isn't on the box yet)."""
    if brain.id == "core":
        return os.environ.get("SKIPPY_CORE_MODEL", "").strip() or brain.model
    return brain.model or _gemini_model()


def choices(is_admin: bool) -> list[Brain]:
    """The brains this person may pick, in picker order. A guest has exactly one: Skippy Core."""
    return [b for b in BRAINS.values() if is_admin or not b.admin_only]


def pick(requested, is_admin: bool) -> Brain:
    """The brain a message runs on. A guest always gets Skippy Core whatever the page sends; the Captain gets what he
    picked, or Skippy Core when he picked nothing. An unknown id, or a guest asking for another brain, is refused."""
    if not is_admin:
        if requested not in (None, "", DEFAULT):
            raise PermissionError("Guests talk to Skippy Core.")
        return BRAINS[DEFAULT]
    if requested in (None, ""):
        return BRAINS[DEFAULT]
    if requested not in BRAINS:
        raise PermissionError(f"There's no brain called {str(requested)[:40]!r}.")
    return BRAINS[requested]


class OpenAIChat:
    """The same reply() as chat.SkippyChat, over an OpenAI-style /chat/completions (Gemini's compatible endpoint). Tools
    arrive as Anthropic schemas and are translated; tool results go back as role "tool" messages."""

    MAX_TOOL_ROUNDS = 6
    TIMEOUT_S = 120

    def __init__(self, brain: Brain, session=None):
        self.brain = brain
        self.http = session or requests

    def reply(self, system, messages, tools=None, run_tool=None, model=None, force_tool=None) -> str:
        from chat import ChatError, MAX_OUTPUT
        convo = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])}
            for m in messages]
        fns = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t.get("input_schema") or {"type": "object"}}}
               for t in tools or []]
        for round_ in range(self.MAX_TOOL_ROUNDS):
            body = {"model": model or model_of(self.brain), "messages": convo, "max_tokens": MAX_OUTPUT}
            if fns:
                body["tools"] = fns
                if force_tool and round_ == 0 and any(f["function"]["name"] == force_tool for f in fns):
                    body["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
            try:
                r = self.http.post(self.brain.base_url() + "/chat/completions", json=body, timeout=self.TIMEOUT_S,
                                   headers={"Authorization": f"Bearer {self.brain.key()}"})
            except requests.RequestException as exc:
                raise ChatError(f"Couldn't reach {self.brain.label}.") from exc
            if r.status_code == 429:
                raise ChatError(f"{self.brain.label} is rate-limited right now. Try again in a minute.")
            if r.status_code in (401, 403):
                raise ChatError(f"The portal's key for {self.brain.label} is missing or invalid.")
            if r.status_code >= 400:
                raise ChatError(f"{self.brain.label} returned an error ({r.status_code}).")
            msg = ((r.json().get("choices") or [{}])[0]).get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls or run_tool is None:
                return (msg.get("content") or "").strip() or "(no reply)"
            convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    out = run_tool(fn.get("name", ""), args if isinstance(args, dict) else {})
                except Exception as exc:  # noqa: BLE001 — tool failures go back to the model, not the user
                    out = f"error: {exc}"
                convo.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": out})
        return "I went round in circles with the tools. Try again, or ask more simply."


def chat_for(brain: Brain):
    """The object with reply() for this brain. Built per message: cheap, and a key change needs no restart."""
    if brain.api == "openai":
        return OpenAIChat(brain)
    import chat
    return chat.SkippyChat(brain.model, api_key=brain.key(), base_url=brain.base_url() or None, label=brain.label)
