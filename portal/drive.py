"""The Drive app (/drive, the Captain only): the controls he uses while driving, for the Tesla's browser. One bridge screen, big keys, glyphs.

    the book       resume his current audiobook at its last place (the same places the pods use: audiobook-gate's places.json, which merges
                   the hub's state/audiobooks.json with what browsers save into state/audiobooks-inbox/, read back by the hub's audiobooks.Store)
    two shuffles   Bilby Shuffle "discovery" (his unplayed songs and Spotify's new ones in his genres, never his favorites) and "library" (his one favorites pool), from the music gate
                   and portal/favorites.py; the queue logic is static/drive-pure.js
    the house      the last key: a lamp per thing at home he cares about (house_lamps: skippy-pc, Skippy Core, the pods, lights and plugs,
                   Bilby, audiobooks, the voice, Spotify); a tap names what is down
    Skippy         a pod in the car, his key showing the pods' own face (static/skippy-face.js): the trained "Skippy" wake word runs in the browser (static/skippy-wake.js, the model from voice-gate), the
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
# Spotify rows in the car's shuffle are OFF (the Captain, 2026-11-05: "I think the Spotify and local library mixing must be causing issues ... If we need to keep them
# separated, that's okay"). A Spotify row hands the song to the car's own Spotify app, which switches the Tesla's sound from the browser (Bilby) to the app and back,
# through the portal and skippy-pc (up to 25 s a hand-off, a poll every 4 s while it plays), on the one Spotify session the house radio shares; the radio log shows it
# ("Spotify moved to Tesla Media Player" a minute or so after the stereo handed over to Spotify, both mornings). The car plays Bilby's library alone, one stream through the
# music gate; Spotify plays where he asks for it. True brings the rows back (the routes below are all still here).
SPOTIFY_IN_CAR = False
# The house lamps (the Captain, 2026-11-01: "a house status light indicator, showing what in the house is up and reachable that we care about"):
# id, glyph, name. Names are words only because a lamp that is out has to say which thing it is.
LAMPS = (("pc", "server", "skippy-pc"), ("core", "core", "Skippy Core"), ("hub", "can", "the pods"), ("house", "home", "lights and plugs"),
         ("music", "wave", "Bilby"), ("books", "book", "audiobooks"), ("voice", "mic", "Skippy's voice"), ("spotify", "disc", "Spotify"))


def house_lamps(home, checks: dict, hub_seen_s: float | None) -> dict:
    """What in the house is up and reachable, one lamp each: True up, False down, None not known yet. Everything at home is down when skippy-pc
    is silent (nothing can be seen past it). checks: the doctor's {name: {ok, ...}}; hub_seen_s: seconds since the hub's last Home chat sync."""
    online = bool(home.online())
    doc = lambda name: (checks.get(name) or {}).get("ok") if name in checks else None      # noqa: E731
    state = {"pc": online, "core": doc("core"), "hub": hub_seen_s is not None and hub_seen_s < 30,
             "house": home.can("house"), "music": home.can("music_gate"), "books": home.can("book_gate"), "voice": home.can("voice_gate"),
             "spotify": doc("spotify")}
    if not online:
        state = {k: (False if k != "core" else state["core"]) for k in state}          # Skippy Core is tested from the portal itself
    lamps = [{"id": i, "glyph": g, "name": n, "ok": state[i]} for i, g, n in LAMPS]
    down = [l["name"] for l in lamps if l["ok"] is False]
    return {"lamps": lamps, "down": down, "verdict": "down" if not online else "warn" if down else "ok"}


def bilby_last(listing: list[dict], played: dict, now: float) -> dict | None:
    """When Bilby last played for him, and what: the newest of his Bilby Connect players' latest songs (any browser, pod or the stereo; with
    where it was in the song) and the newest song on the played list (a song heard past 30 s anywhere; no position). None: no record at all.
    -> {at (epoch s), track, pos, title, artist, where}. The Drive page compares `at` with the current book's last_played."""
    best = None
    for d in listing or []:
        st = d.get("state") or {}
        track = st.get("track")
        if not isinstance(track, str) or not track:
            continue
        at = now - float(d.get("age") or 0)
        if best is None or at > best["at"]:
            best = {"at": round(at), "track": track, "pos": float(st.get("pos") or 0), "title": st.get("title") or "", "artist": st.get("artist") or "",
                    "where": d.get("name") or ""}
    for track, v in (played or {}).items():
        try:
            at = float(v[1])
        except (IndexError, TypeError, ValueError):
            continue
        if best is None or at > best["at"] + 60:           # a song past its 30 s mark is also on the played list: Connect's entry (with its place) wins
            best = {"at": round(at), "track": track, "pos": 0.0, "title": "", "artist": "", "where": ""}
    return best


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

    @bp.get("/drive/last")
    @auth.admin_required
    def last():
        player, favs = app.extensions.get("player"), app.extensions.get("favorites")
        captain = auth.current_user()["email"]
        try:
            played = favs.played() if favs is not None else {}
        except Exception:                                   # noqa: BLE001 — a bucket hiccup: Connect alone still says something
            played = {}
        import time
        resp = jsonify(bilby=bilby_last(player.listing(captain) if player is not None else [], played, time.time()))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/drive/house")
    @auth.admin_required
    def house():
        doctor, chat = app.extensions.get("doctor"), app.extensions.get("housechat")
        seen = getattr(chat, "seen", 0) if chat is not None else 0
        resp = jsonify(house_lamps(home, dict(getattr(doctor, "checks", {}) or {}), (chat.clock() - seen) if seen else None))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/drive")
    @auth.admin_required
    def page():
        return render_template("drive.html", lamps=LAMPS, spotify_in_car=SPOTIFY_IN_CAR)

    app.register_blueprint(bp)
