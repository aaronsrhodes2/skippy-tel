import anthropic
from config import settings

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

# Per-session conversation history: session_id → list of messages
_histories: dict[str, list[dict]] = {}

DEFAULT_SYSTEM = (
    "You are Skippy, a helpful AI assistant running on a local gaming rig. "
    "Be concise and direct. You have access to local AI models and tools."
)


async def chat(session_id: str, message: str, system: str | None = None) -> str:
    history = _histories.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    response = await _client.messages.create(
        model=settings.anthropic_model,
        max_tokens=8192,
        system=system or DEFAULT_SYSTEM,
        messages=history,
    )
    reply = response.content[0].text
    history.append({"role": "assistant", "content": reply})
    return reply


async def stream_chat(session_id: str, message: str, system: str | None = None):
    """Yields text chunks as an async generator."""
    history = _histories.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    full_response = ""
    async with _client.messages.stream(
        model=settings.anthropic_model,
        max_tokens=8192,
        system=system or DEFAULT_SYSTEM,
        messages=history,
    ) as stream:
        async for text in stream.text_stream:
            full_response += text
            yield text

    history.append({"role": "assistant", "content": full_response})


def clear_history(session_id: str) -> None:
    _histories.pop(session_id, None)
