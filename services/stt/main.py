from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form
from typing import Optional
import whisper_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    await whisper_client.init()
    yield


app = FastAPI(title="skippy-stt", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "skippy-stt"}


@app.post("/stt")
async def stt(
    audio: UploadFile = File(...),
    language: Optional[str] = Form(None),
):
    audio_bytes = await audio.read()
    transcript = await whisper_client.transcribe(audio_bytes, language)
    return {"transcript": transcript}
