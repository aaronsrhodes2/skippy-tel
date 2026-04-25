"""POST /image — Stable Diffusion image generation.

NOT YET DEPLOYED. The endpoint exists so clients get a clean 503 with a
useful message rather than a connection error.

When we're ready to stand this up properly it will be a first-party
service inside the SkippyTel Docker stack — not a dependency on any
external project.
"""
from flask import Blueprint
from app.version import error

bp = Blueprint("image", __name__)


@bp.post("/image")
def generate_image():
    return error(
        "Image generation not yet deployed — will be a first-party service in a future sprint.",
        error_type="not_deployed",
        status=503,
    )
