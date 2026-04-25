"""POST /image — proxy to the host-side Stable Diffusion service.

The flask-api container cannot reach localhost:5000 directly; the SD
server runs on the Docker host, reachable via host.docker.internal.

Request  (JSON): { "prompt": str, "negative_prompt"?: str,
                   "steps"?: int, "guidance_scale"?: float }
Response (JSON): { "image_b64": "<base64 PNG data URL>",
                   "image_id": "<gallery id>",
                   "prompt": "<echoed>", ... }

Errors return a JSON envelope with error / message fields.
"""
import httpx
from flask import Blueprint, request

from app.config   import settings
from app.version  import envelope, error

bp = Blueprint("image", __name__)

_SD_TIMEOUT = 120.0   # SD cold-boot on first request can be slow


@bp.post("/image")
def generate_image():
    body   = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return error("prompt is required", error_type="bad_request", status=400)

    payload = {"prompt": prompt}
    if body.get("negative_prompt"):
        payload["negative_prompt"] = body["negative_prompt"]
    if body.get("steps"):
        payload["steps"] = int(body["steps"])
    if body.get("guidance_scale"):
        payload["guidance_scale"] = float(body["guidance_scale"])

    try:
        r = httpx.post(
            f"{settings.sd_base_url}/generate",
            json=payload,
            timeout=_SD_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return envelope({
            "image_b64": data.get("image"),        # base64 PNG data URL from SD
            "image_id":  data.get("image_id", ""),
            "prompt":    prompt,
        })
    except httpx.ConnectError:
        return error("SD service unreachable", error_type="sd_unavailable", status=503)
    except httpx.HTTPStatusError as e:
        return error(f"SD error {e.response.status_code}",
                     error_type="sd_error", status=502)
    except Exception as e:
        return error(str(e), error_type="upstream_error", status=502)
