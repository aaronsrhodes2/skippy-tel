"""Text chat with Skippy, via the Claude API.

History lives in the browser (sent with each message) — Cloud Run keeps no
state between requests, and a signed cookie is too small for a
conversation. Only the signed-in, allowlisted user can post here, so the
history they send is trusted as their own.

Not yet connected to skippy-tel-core or its MCP servers: the portal runs on
Cloud Run, which has no route into the home Tailscale mesh where
skippy-tel-core lives. See docs/portal/README.md → Chat.
"""
from __future__ import annotations

from pathlib import Path

MAX_MESSAGES = 40          # history sent to the model: the last 20 exchanges
MAX_CHARS = 8000           # per message

_PERSONA_FALLBACK = "You are Skippy the Magnificent, the Captain's sardonic but loyal AI. Keep replies short."


def persona() -> str:
    """services/flask-api/app/skippy_persona.md — copied into the portal image."""
    for path in (Path(__file__).with_name("skippy_persona.md"),
                 Path(__file__).resolve().parents[1] / "services/flask-api/app/skippy_persona.md"):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return _PERSONA_FALLBACK


def system_prompt(user_name: str) -> str:
    return (
        f"{persona()}\n\n"
        "## Where you are\n"
        f"You're talking with {user_name} through the Skippy Portal's text chat. "
        "The portal controls the Captain's game servers (Valheim, Factorio, Satisfactory, "
        "RuneScape: Dragonwilds) but in this chat you have no tools: you can't start servers, "
        "read email, or reach skippy-tel-core yet. If asked to do something that needs a tool, "
        "say so plainly and point to the portal's buttons instead of pretending."
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

    def reply(self, system: str, messages: list[dict]) -> str:
        import anthropic
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=messages,
                # Server-side refusal fallback: if the model declines on policy
                # grounds, the API re-runs the turn on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise ChatError("Skippy is rate-limited right now. Try again in a minute.") from exc
        except anthropic.AuthenticationError as exc:
            raise ChatError("The portal's Anthropic API key is missing or invalid.") from exc
        except anthropic.APIStatusError as exc:
            raise ChatError(f"The Claude API returned an error ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise ChatError("Couldn't reach the Claude API.") from exc

        if response.stop_reason == "refusal":
            return "I can't help with that one."
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return text or "(no reply)"
