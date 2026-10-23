"""Skippy's chat has three MODES (the Captain's design, 2026-10-23). The page shows a mode key; this module says what each is.

  public   everyone            green   Skippy Core, the local model on skippy-core (llama-swap's chat slot behind its Tailscale
                                       Funnel and key), answered here by /chat/send with the portal's public tools (Mentat,
                                       game status, pictures). The only mode a guest has, and the only one /chat/send serves.
  private  the Captain only    purple  the AGENT on skippy-pc (Claude Code, templates/_workshop.html) thinking with Skippy
                                       Core's coder: every message, local models only, full access to D:/Aaron/development.
  auto     the Captain only    orange  the same agent thinking with Claude (Anthropic): external models where they help.

Private and Auto never touch /chat/send: they go through /me/workshop/* (admin-only, re-checked per call). Gemini is gone (no
free image tier, unreliable); so are the paid quick-chat brains (Auto is Claude through the agent). Barney Style is always
Skippy Core. The server decides, never the page: /chat/send answers Public and refuses anything else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Brain:
    id: str
    label: str
    model: str
    key_env: str
    base_env: str

    def key(self) -> str:
        v = os.environ.get(self.key_env, "").strip()
        return "" if v == "unset" else v

    def base_url(self) -> str:
        v = os.environ.get(self.base_env, "").strip().rstrip("/")
        return "" if v == "unset" else v

    def ready(self) -> bool:
        return bool(self.key() and self.base_url())


CORE = Brain("core", "Skippy Core", "chat", "SKIPPY_CORE_API_KEY", "SKIPPY_CORE_URL")
BRAINS: dict[str, Brain] = {CORE.id: CORE}       # the portal answers with Skippy Core only
DEFAULT = BARNEY = "core"

# The mode key, in order. `agent` is the bridge brain a Captain's mode drives (templates/_workshop.html); None = this chat.
MODES = (
    {"id": "public", "label": "Public", "glyph": "can", "theme": "green", "agent": None, "admin": False},
    {"id": "private", "label": "Private", "glyph": "core", "theme": "purple", "agent": "core", "admin": True},
    {"id": "auto", "label": "Auto", "glyph": "claude", "theme": "orange", "agent": "claude", "admin": True},
)


def modes(is_admin: bool) -> list[dict]:
    """The modes this person may pick, in key order. A guest has exactly one: Public."""
    return [dict(m) for m in MODES if is_admin or not m["admin"]]


def model_of(brain: Brain) -> str:
    """Skippy Core's llama-swap slot: "chat" unless SKIPPY_CORE_MODEL picks another (e.g. "coder")."""
    return os.environ.get("SKIPPY_CORE_MODEL", "").strip() or brain.model


def pick(requested, is_admin: bool) -> Brain:
    """The brain /chat/send answers with: Skippy Core (Public), whoever asks. Anything else that reaches here (an old page
    asking for "opus", Private or Auto, which belong to the agent) is refused."""
    if requested in (None, "", "core", "public"):
        return CORE
    raise PermissionError("This chat answers in Public mode with Skippy Core; Private and Auto run on the agent.")


def chat_for(brain: Brain):
    """The object with reply() for this brain: Skippy Core speaks Anthropic's Messages API (llama-swap), so it is the
    Anthropic SDK with another base_url and key."""
    import chat
    return chat.SkippyChat(brain.model, api_key=brain.key(), base_url=brain.base_url() or None, label=brain.label)
