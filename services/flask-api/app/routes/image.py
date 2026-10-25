"""POST /image — retired: pictures are NOT made here.

Pictures are made by the image studio as patient jobs (portal/patient.py): the portal chat's make_image tool, or the private
Skippy writing one line `SKIPPY_PICTURE: <description>` in its reply. This route only stays so the shelved phone apps get a
clean 503 saying where pictures come from (tests/test_no_interface.py keeps it). An agent asked for a picture must not try to
wire it up (2026-10-24: this stub talked Skippy Core's coder out of drawing a cat).
"""
from flask import Blueprint
from app.version import error

bp = Blueprint("image", __name__)


@bp.post("/image")
def generate_image():
    return error(
        "Pictures aren't made here: ask Skippy in the portal chat (the agent writes a SKIPPY_PICTURE: line).",
        error_type="not_here",
        status=503,
    )
