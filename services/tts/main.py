from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
import kokoro_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    await kokoro_client.init()
    yield


app = FastAPI(title="skippy-tts", lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "skippy-tts"}


@app.post("/tts")
async def tts(req: TTSRequest):
    wav = await kokoro_client.synthesize(req.text, req.voice)
    return Response(content=wav, media_type="audio/wav")
