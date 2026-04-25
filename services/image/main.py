from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sd_client

app = FastAPI(title="skippy-image")


class ImageRequest(BaseModel):
    prompt: str
    negative_prompt: str = "blurry, low quality, distorted"
    steps: int = 25
    guidance_scale: float = 7.5


@app.get("/health")
async def health():
    sd_up = await sd_client.health_check()
    return {"status": "ok", "service": "skippy-image", "sd_online": sd_up}


@app.post("/image")
async def image(req: ImageRequest):
    try:
        b64 = await sd_client.generate(req.prompt, req.negative_prompt, req.steps, req.guidance_scale)
        return {"image": b64, "mime_type": "image/png"}
    except sd_client.SDUnavailableError:
        raise HTTPException(status_code=503, detail="Stable Diffusion is offline. Start flask-sd on the host.")
