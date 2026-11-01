"""The Drive app (/drive, the Captain only): the controls he uses while driving, for the Tesla's browser. One bridge screen, big keys, glyphs.

    the book       resume his current audiobook at its last place (the same places the pods use: audiobook-gate's places.json, which merges
                   the hub's state/audiobooks.json with what browsers save into state/audiobooks-inbox/, read back by the hub's audiobooks.Store)
    two shuffles   Bilby Shuffle "discovery" (all of Bilby's songs, his favorites mixed in) and "library" (his favorites), from the music gate
                   and portal/favorites.py; the queue logic is static/drive-pure.js
    Skippy         a pod in the car: the trained "Skippy" wake word runs in the browser (static/skippy-wake.js, the model from voice-gate), the
                   turn goes to the house brain through the Home chat (portal/housechat.py: as if typed, answered silently at home) and the
                   answer is spoken here; the music is ducked while he listens and speaks (static/skippy-duck.js), never stopped

Everything plays in this one page, so the browser (or the car) never has two pages fighting over the sound. The page uses existing routes
(/music/gate, /music/favorites, /audiobooks/gate, /chat/voice/gate, /home-chat/send, /home-chat/events) and this module's own:

    the Spotify rows   a browser can't play Spotify, so a Spotify row of a shuffle plays on the CAR's own Spotify (a Connect device of type
                       Automobile), through skippy-pc (the reporter's `spotify` job -> services/spotify): /drive/spotify/mix (his liked songs and
                       suggestions from his liked artists), /drive/spotify/play {uri}, /drive/spotify/pause, /drive/spotify/car (what it plays,
                       so the page knows when the song is over and plays the next row itself)
"""
from __future__ import annotations

import mimetypes
import re

from flask import Blueprint, jsonify, render_template, request

import auth

# onnxruntime-web's loader and engine (static/vendor/ort/) must be served as a module and as wasm, whatever the host's own table says
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")


URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}")
WAIT_S = {"mix": 75, "car": 15, "car_play": 25, "car_pause": 15}


def spotify(home, op: str, **payload) -> tuple[dict, int]:
    """One Spotify operation at home: (answer, HTTP status). Said in words when home is silent or the car's Spotify isn't there."""
    if not home.online():
        return {"ok": False, "why": "skippy-pc isn't reporting in"}, 503
    home.touch_chat()                                       # the reporter polls every 1.5 s while this is going on
    outcome = home.wait(home.submit("spotify", op=op, **payload), WAIT_S[op])
    if outcome is None:
        return {"ok": False, "why": "skippy-pc didn't answer in time"}, 504
    if outcome.get("error"):
        return {"ok": False, "why": str(outcome["error"])[:300]}, 502
    return {"ok": True, **(outcome.get("result") or {})}, 200


def init_app(app, home) -> None:
    bp = Blueprint("drive", __name__)

    def answer(op: str, **payload):
        body, code = spotify(home, op, **payload)
        resp = jsonify(body)
        resp.status_code = code
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/drive/spotify/mix")
    @auth.admin_required
    def spotify_mix():
        return answer("mix")

    @bp.get("/drive/spotify/car")
    @auth.admin_required
    def spotify_car():
        return answer("car")

    @bp.post("/drive/spotify/play")
    @auth.admin_required
    def spotify_play():
        uri = str((request.get_json(silent=True) or {}).get("uri") or "")
        if not URI.fullmatch(uri):
            return jsonify(ok=False, why="a spotify:track: URI"), 400
        pos = (request.get_json(silent=True) or {}).get("pos_ms")
        return answer("car_play", uri=uri, pos_ms=max(0, int(pos)) if isinstance(pos, (int, float)) and not isinstance(pos, bool) else 0)

    @bp.post("/drive/spotify/pause")
    @auth.admin_required
    def spotify_pause():
        return answer("car_pause")

    @bp.get("/drive")
    @auth.admin_required
    def page():
        return render_template("drive.html")

    app.register_blueprint(bp)
