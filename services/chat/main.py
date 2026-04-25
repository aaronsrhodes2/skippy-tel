from fastapi import FastAPI, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import chat

app = FastAPI(title="skippy-chat")


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    tier: str | None = None
    system: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "skippy-chat"}


@app.post("/chat")
async def handle_chat(req: ChatRequest):
    reply = await chat.handle_chat(req.session_id, req.message, req.tier, req.system)
    return {"reply": reply}


@app.post("/chat/stream")
async def handle_chat_stream(req: ChatRequest):
    async def sse_generator():
        async for chunk in chat.stream_handle_chat(req.session_id, req.message, req.tier, req.system):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@app.post("/translate")
async def handle_translate(body: dict):
    text = body.get("text", "")
    target = body.get("target_language", "English")
    session_id = body.get("session_id", "translate")
    tier = body.get("tier")

    prompt = (
        f"Translate the following text to {target}. "
        f"Output only the translation, no explanations.\n\nText: {text}"
    )
    reply = await chat.handle_chat(session_id, prompt, tier)
    return {"translation": reply}
