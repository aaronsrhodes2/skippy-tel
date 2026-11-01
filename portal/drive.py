"""The Drive app (/drive, the Captain only): the controls he uses while driving, for the Tesla's browser. One bridge screen, big keys, glyphs.

    the book       resume his current audiobook at its last place (the same places the pods use: audiobook-gate's places.json, which merges
                   the hub's state/audiobooks.json with what browsers save into state/audiobooks-inbox/, read back by the hub's audiobooks.Store)
    two shuffles   Bilby Shuffle "discovery" (all of Bilby's songs, his favorites mixed in) and "library" (his favorites), from the music gate
                   and portal/favorites.py; the queue logic is static/drive-pure.js
    Skippy         a pod in the car: the trained "Skippy" wake word runs in the browser (static/skippy-wake.js, the model from voice-gate), the
                   turn goes to the house brain through the Home chat (portal/housechat.py: as if typed, answered silently at home) and the
                   answer is spoken here; the music is ducked while he listens and speaks (static/skippy-duck.js), never stopped

Everything plays in this one page, so the browser (or the car) never has two pages fighting over the sound. The page uses existing routes
only (/music/gate, /music/favorites, /audiobooks/gate, /chat/voice/gate, /home-chat/send, /home-chat/events); this module adds the page.
"""
from __future__ import annotations

import mimetypes

from flask import Blueprint, render_template

import auth

# onnxruntime-web's loader and engine (static/vendor/ort/) must be served as a module and as wasm, whatever the host's own table says
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")


def init_app(app) -> None:
    bp = Blueprint("drive", __name__)

    @bp.get("/drive")
    @auth.admin_required
    def page():
        return render_template("drive.html")

    app.register_blueprint(bp)
