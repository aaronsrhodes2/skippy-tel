import httpx
from config import settings


class SDUnavailableError(Exception):
    pass


async def health_check() -> bool:
    try:
        async with httpx.AsyncClient(base_url=settings.sd_base_url, timeout=3.0) as c:
            r = await c.get("/health")
            return r.status_code == 200
    except httpx.ConnectError:
        return False


async def generate(
    prompt: str,
    negative_prompt: str = "blurry, low quality, distorted",
    steps: int = 25,
    guidance_scale: float = 7.5,
) -> str:
    """Returns base64-encoded PNG string."""
    try:
        async with httpx.AsyncClient(base_url=settings.sd_base_url, timeout=120.0) as c:
            r = await c.post("/generate", json={
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "steps": steps,
                "guidance_scale": guidance_scale,
            })
            r.raise_for_status()
            data = r.json()
            # flask-sd returns {"image": "data:image/png;base64,..."} or {"image": "<base64>"}
            img = data.get("image", "")
            if img.startswith("data:image"):
                img = img.split(",", 1)[1]
            return img
    except httpx.ConnectError as e:
        raise SDUnavailableError("Stable Diffusion offline") from e
