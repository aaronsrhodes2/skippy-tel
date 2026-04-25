"""POST /heybuddy/identify — Hey Buddy face recognition.

Brief originally called this `/yeybuddy/identify`; renamed to
`/heybuddy/` per the Captain (it's the "hey buddy" you say when
you've forgotten someone's name). SkippyDroid side must rename
in lockstep — see misc/SKIPPYDROID_PORT_CHANGE_NOTE.md.

Contract:
  Request:  multipart image (face crop JPEG)
  Response: { match: {name, confidence} | null, ... }
  Latency:  < 1s warm.
  Privacy:  face DB never leaves the PC. SQLite at /data/faces.db
            inside the insightface container. Photos discarded.
"""
from flask import Blueprint, request

from app.clients import insightface as insightface_client
from app.version import envelope, error

bp = Blueprint("heybuddy", __name__)


@bp.post("/heybuddy/identify")
def heybuddy_identify():
    if "image" not in request.files:
        return error(
            "image file required (multipart field name 'image')",
            error_type="bad_request",
            status=400,
        )

    image = request.files["image"]
    try:
        result = insightface_client.identify(
            image.read(),
            filename=image.filename or "face.jpg",
            content_type=image.mimetype,
        )
        return envelope(result)
    except insightface_client.InsightFaceUnavailable as e:
        return error(str(e), error_type="upstream_unavailable", status=503)
    except Exception as e:
        return error(str(e), error_type="upstream_error", status=502)
