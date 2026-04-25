import httpx
from config import settings


class OllamaUnavailableError(Exception):
    pass


async def health_check() -> bool:
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=3.0) as c:
            r = await c.get("/api/tags")
            return r.status_code == 200
    except httpx.ConnectError:
        return False


async def chat(messages: list[dict], model: str | None = None) -> str:
    model = model or settings.ollama_model
    payload = {"model": model, "messages": messages, "stream": False}
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120.0) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"]
    except httpx.ConnectError as e:
        raise OllamaUnavailableError("Ollama unreachable") from e
    except httpx.HTTPStatusError as e:
        raise OllamaUnavailableError(f"Ollama error: {e.response.status_code}") from e


async def stream_chat(messages: list[dict], model: str | None = None):
    """Yields text chunks as an async generator."""
    model = model or settings.ollama_model
    payload = {"model": model, "messages": messages, "stream": True}
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120.0) as c:
            async with c.stream("POST", "/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    import json
                    data = json.loads(line)
                    if chunk := data.get("message", {}).get("content"):
                        yield chunk
                    if data.get("done"):
                        break
    except httpx.ConnectError as e:
        raise OllamaUnavailableError("Ollama unreachable") from e
