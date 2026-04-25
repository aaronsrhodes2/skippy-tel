"""Sync insightface client. Proxies multipart image to the insightface container."""
import os

import httpx


INSIGHTFACE_BASE_URL = os.getenv("INSIGHTFACE_BASE_URL", "http://insightface:8000")


class InsightFaceUnavailable(Exception):
    pass


def identify(image_bytes: bytes, filename: str,
             content_type: str | None = None) -> dict:
    files = {"image": (filename or "face.jpg", image_bytes,
                       content_type or "image/jpeg")}
    try:
        # 30s ceiling — first request after container start pays model
        # load (~325MB download + GPU prep). Brief budget is <1s warm.
        r = httpx.post(
            f"{INSIGHTFACE_BASE_URL}/identify",
            files=files,
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError as e:
        raise InsightFaceUnavailable("insightface service unreachable") from e
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        raise InsightFaceUnavailable(
            f"insightface HTTP {e.response.status_code}: {body}"
        ) from e
