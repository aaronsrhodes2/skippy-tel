import re
from ollama_client import OllamaUnavailableError

THINKING_CAP_PHRASE = "put on your thinking cap"

# Local per-session history for Ollama (separate from Anthropic's history)
_local_histories: dict[str, list[dict]] = {}


def resolve_tier(message: str, tier: str | None = None, local_available: bool = True) -> str:
    """Returns 'claude' or 'local'."""
    if tier == "claude":
        return "claude"
    if not local_available:
        return "claude"
    if THINKING_CAP_PHRASE in message.lower():
        return "claude"
    return "local"


def strip_thinking_cap(message: str) -> str:
    return re.sub(
        r"put on your thinking cap[,.]?\s*", "", message, flags=re.IGNORECASE
    ).strip()


async def handle_chat(
    session_id: str,
    message: str,
    tier: str | None = None,
    system: str | None = None,
) -> str:
    import ollama_client
    import anthropic_client

    t = resolve_tier(message, tier)
    clean = strip_thinking_cap(message)

    if t == "local":
        history = _local_histories.setdefault(session_id, [])
        history.append({"role": "user", "content": clean})
        try:
            reply = await ollama_client.chat(history)
            history.append({"role": "assistant", "content": reply})
            return reply
        except OllamaUnavailableError:
            pass  # fall through to Claude

    clean_for_claude = strip_thinking_cap(message)
    return await anthropic_client.chat(session_id, clean_for_claude, system)


async def stream_handle_chat(
    session_id: str,
    message: str,
    tier: str | None = None,
    system: str | None = None,
):
    """Async generator yielding text chunks."""
    import ollama_client
    import anthropic_client

    t = resolve_tier(message, tier)
    clean = strip_thinking_cap(message)

    if t == "local":
        history = _local_histories.setdefault(session_id, [])
        history.append({"role": "user", "content": clean})
        full = ""
        try:
            async for chunk in ollama_client.stream_chat(history):
                full += chunk
                yield chunk
            history.append({"role": "assistant", "content": full})
            return
        except OllamaUnavailableError:
            pass

    async for chunk in anthropic_client.stream_chat(session_id, strip_thinking_cap(message), system):
        yield chunk
