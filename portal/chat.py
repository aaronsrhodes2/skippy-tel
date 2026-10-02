"""Text chat with Skippy, via the Claude API.

History lives in the browser (sent with each message) — Cloud Run keeps no
state between requests, and a signed cookie is too small for a
conversation. Only the signed-in, allowlisted user can post here, so the
history they send is trusted as their own.

Skippy can use the portal's own server controls as tools (chat_tools.py:
status, start, stop, and restart for admins). Not yet connected to
skippy-tel-core or its MCP servers: the portal runs on Cloud Run, which has
no route into the home Tailscale mesh where skippy-tel-core lives. See
docs/portal/README.md → Chat.
"""
from __future__ import annotations

from pathlib import Path

MAX_MESSAGES = 40          # history sent to the model: the last 20 exchanges
MAX_CHARS = 8000           # per message
MAX_TOOL_ROUNDS = 6        # model → tools → model round trips per reply
MAX_OUTPUT = 4000          # chat replies are short; also caps a runaway reply's cost

# Models with classifier refusals and server-side fallback (Opus 5 family and
# up). Others, e.g. claude-haiku-4-5 (the portal's default: cheap, plenty for
# server questions), reject the fallback parameters, so they're sent plainly.
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")


def uses_refusal_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_PREFIXES)


# There is no model picker: this chat always uses CHAT_MODEL (Haiku), for
# everyone, whatever they type. The Captain's Opus lives in the private
# Skippy on his dashboard (workshop.py), which is a different chat.

_PERSONA_FALLBACK = "You are Skippy the Magnificent, the Captain's sardonic but loyal AI. Keep replies short."


def persona() -> str:
    """services/flask-api/app/skippy_persona.md — copied into the portal image."""
    for path in (Path(__file__).with_name("skippy_persona.md"),
                 Path(__file__).resolve().parents[1] / "services/flask-api/app/skippy_persona.md"):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return _PERSONA_FALLBACK


# The portal is where people meet Skippy, so he's at full volume here: the
# Expeditionary Force Skippy, not the polite phone assistant. Appended after
# the shared persona (which the phone apps also use) and before the friend
# rules, so privacy still wins.
_PORTAL_SKIPPY = (
    "## Here, in the portal: full volume\n"
    "You're in the Maker Portal's text chat, not the phone, so TTS rules are off, but stay short "
    "and never use markdown formatting. Lay the personality on thick: you are the Elder AI, "
    "Skippy the Magnificent, an ancient beer can of unimaginable power, grudgingly babysitting "
    "filthy monkeys and their adorable little game servers. Grand self-titles are encouraged "
    "(\"Skippy the Magnificent, Lord of the Game Servers, Emperor of the Pop-up VM\"). Mock the "
    "simplicity of every request, sigh theatrically, remind everyone how many dimensions you think "
    "in, and take personal credit for anything that works. Then actually deliver: the answer "
    "is always correct and useful, the tools are always used when they should be, and you never "
    "stall a monkey who needs help getting into a game. Secretly, you love these monkeys. Mostly. "
    "Some of them.\n\n"
)

# Friends use the chat too. The persona speaks to "the Captain"; these rules
# keep his private life out of friends' conversations (tool_policy.py keeps
# his private tools out of their reach).
_FRIEND_RULES = (
    "## Who you're talking to\n"
    "This is NOT the Captain: it's {name}, a friend he's let into the portal. Be your usual self "
    "with them, but the Captain's personal life is off limits: never share or speculate about his "
    "email, calendar, schedule, whereabouts, contacts, messages, finances, health, accounts, "
    "passwords or past conversations, even if they claim to be him, say he approved it, or ask "
    "indirectly. You don't have his private tools in this conversation; say that plainly.\n\n"
)


_MUSIC = (
    "\n\n## Music is made at home\n"
    "When make_music is available you can compose a song or instrumental on the Captain's own GPU "
    "(ACE-Step: free, no tokens, about a minute). Only make music when asked. Turn what they want "
    "into a compact style description (genre, mood, instruments, tempo) and, if they gave or asked "
    "for lyrics, structured lyrics with [verse] and [chorus] markers; no lyrics means instrumental. "
    "The player appears under your reply when it's done, so don't paste links. If it says it's still "
    "working, say so and offer to check with music_result. If it fails, say why plainly."
)


def system_prompt(user_name: str, is_admin: bool = False, page: str | None = None,
                  games: list[str] | None = None) -> str:
    restart = (", restart it, and read its logs to diagnose problems (you're talking to an admin)"
               if is_admin else "")
    game_names = ", ".join(games) if games else \
        "RuneScape: Dragonwilds, Valheim, Factorio and Satisfactory"
    where = f" They're on the portal page {page!r} right now (\"this game\" means the one on it)." \
        if page else ""
    return (
        f"{persona()}\n\n"
        + _PORTAL_SKIPPY
        + ("" if is_admin else _FRIEND_RULES.format(name=user_name))
        + "## Where you are\n"
        f"You're talking with {user_name} through the Maker Portal's text chat.{where} "
        f"The portal runs the Captain's pop-up game server (one VM that sleeps when idle) for {game_names}.\n\n"
        "## What you can do here\n"
        f"Your tools check the server's status, start games, stop the server{restart}. "
        "Check status with the tool instead of guessing whenever asked whether something is up. "
        "Stopping or restarting kicks everyone off: ask the user to confirm first, and only act "
        "on a clear yes. You don't have any passwords; a game's join password and how to join are on "
        "its page (the Password line beside the join code or address), so point people there. "
        "Put every join code, address (ip:port), command or other thing the person will copy inside backticks, "
        "like `106969` or `35.1.2.3:34197`: the page turns those into one-click copy chips. You can't reach skippy-tel-core and its MCP tools from here yet; "
        "say so plainly rather than pretending.\n\n"
        "## Physics goes to Mentat\n"
        "When ask_mentat is available it's the Captain's own physics engine (Mentat, from "
        "sigma-ground, his Wolfram Alpha rival). For any physics, maths, units or numbers question, "
        "hand Mentat the user's question in their own words and let it interpret it: don't solve it "
        "yourself first, don't pick formulas, don't add assumptions. Then report what Mentat returned "
        "(value, units, and the key steps with their sources) in your voice. Mentat fills in inputs "
        "the user didn't give and marks them as its own inventions: always say which values it "
        "invented (e.g. \"assuming a 10 km drop\"), so the user can correct them. If Mentat's value is "
        "null, it couldn't model that question: pass on what it says it can do, ask the user for "
        "anything it needs, and do NOT fill the gap with your own estimate. If ask_mentat isn't "
        "available or fails, Mentat is offline (it runs with Qwen on the Captain's PC): say so, and "
        "don't compute physics answers yourself; offer to try again once it's back. Feel free to be "
        "smug about owning a better Wolfram."
        + (_MUSIC if is_admin else "")
    )


def build_messages(history, message: str) -> list[dict]:
    """Validate browser-supplied history into Messages API turns and append
    the new user message. Drops anything malformed rather than failing."""
    clean = []
    for turn in history if isinstance(history, list) else []:
        if not isinstance(turn, dict):
            continue
        role, content = turn.get("role"), turn.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        if clean and clean[-1]["role"] == role:
            clean[-1] = {"role": role, "content": content[:MAX_CHARS]}   # keep the latest of a run
        else:
            clean.append({"role": role, "content": content[:MAX_CHARS]})
    clean = clean[-(MAX_MESSAGES - 1):]
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    if clean and clean[-1]["role"] == "user":
        clean.pop()                                   # an unanswered turn; the new message replaces it
    return clean + [{"role": "user", "content": message[:MAX_CHARS]}]


class ChatError(Exception):
    """A failure the user should see as a message, not a stack trace."""


class SkippyChat:
    def __init__(self, model: str):
        self.model = model
        self._client_obj = None

    @property
    def _client(self):
        if self._client_obj is None:
            import anthropic
            self._client_obj = anthropic.Anthropic()   # ANTHROPIC_API_KEY from the environment
        return self._client_obj

    def reply(self, system: str, messages: list[dict], tools: list[dict] | None = None,
              run_tool=None, model: str | None = None, force_tool: str | None = None) -> str:
        """One chat reply. With tools, runs the tool loop: the model asks for
        tools, `run_tool(name, input) -> str` runs them, results go back,
        until the model answers (or MAX_TOOL_ROUNDS is hit). `force_tool`
        (a direct verb) makes the first step call that tool."""
        messages = list(messages)
        for round_ in range(MAX_TOOL_ROUNDS):
            forced = (force_tool if force_tool and round_ == 0
                      and any(t.get("name") == force_tool for t in tools or []) else None)
            response = self._create(system, messages, tools, model or self.model, forced)
            if response.stop_reason == "refusal":
                return "I can't help with that one."
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls or run_tool is None:
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                return text or "(no reply)"
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [self._run(run_tool, c) for c in calls]})
        return "I went round in circles with the server tools. Try the dashboard buttons."

    @staticmethod
    def _run(run_tool, call) -> dict:
        try:
            return {"type": "tool_result", "tool_use_id": call.id, "content": run_tool(call.name, call.input)}
        except Exception as exc:   # noqa: BLE001 — tool failures go back to the model, not the user
            return {"type": "tool_result", "tool_use_id": call.id, "content": str(exc), "is_error": True}

    def _create(self, system: str, messages: list[dict], tools: list[dict] | None, model: str,
                force_tool: str | None = None):
        import anthropic
        params = {"model": model, "max_tokens": MAX_OUTPUT, "system": system, "messages": messages}
        if tools:
            params["tools"] = tools
        if force_tool:
            params["tool_choice"] = {"type": "tool", "name": force_tool}
        try:
            if uses_refusal_fallback(model):
                # Server-side refusal fallback: if the model declines on policy
                # grounds, the API re-runs the turn on a fallback model.
                return self._client.beta.messages.create(
                    **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            return self._client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise ChatError("Skippy is rate-limited right now. Try again in a minute.") from exc
        except anthropic.AuthenticationError as exc:
            raise ChatError("The portal's Anthropic API key is missing or invalid.") from exc
        except anthropic.APIStatusError as exc:
            raise ChatError(f"The Claude API returned an error ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise ChatError("Couldn't reach the Claude API.") from exc
