"""The Drive app (drive.py): the Captain's keys at the wheel. His alone, never a grant; one page that plays the book and the shuffles itself,
with Skippy's voice and the in-browser wake word (onnxruntime-web, served as a module and as wasm)."""
import pytest

pytest.importorskip("authlib")

from test_app import ADMIN, FRIEND, env, login  # noqa: E402,F401


def test_the_page_is_the_captains_alone(env):
    client, _, _ = env
    assert client.get("/drive").status_code == 302                 # signed out: to the sign-in
    login(client, FRIEND)
    assert client.get("/drive").status_code == 403
    assert b'href="/drive"' not in client.get("/apps").data
    login(client, ADMIN)
    r = client.get("/drive")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for k in ("k-book", "k-disc", "k-lib", "k-skippy", "k-play", "k-next"):         # the six keys, and nothing else to press
        assert f'id="{k}"' in html
    for s in ("skippy-duck.js", "skippy-voice.js", "skippy-wake.js", "drive-pure.js", "drive-app.js", "audiobooks-pure.js"):
        assert s in html
    assert b'href="/drive"' in client.get("/apps").data            # a tile on his launcher
    assert "/home-chat/send" in html and "/audiobooks/gate" in html and "/music/gate" in html and "/chat/voice/gate" in html


def test_the_wake_word_engine_is_served_as_a_module_and_as_wasm(env):
    c, _, _ = env
    assert c.get("/static/vendor/ort/ort-wasm-simd-threaded.mjs").mimetype == "text/javascript"
    assert c.get("/static/vendor/ort/ort-wasm-simd-threaded.wasm").mimetype == "application/wasm"


def test_drive_is_on_the_launcher_for_the_captain_only(env):
    import modules
    assert modules.BY_ID["drive"].endpoint == "drive.page" and not modules.BY_ID["drive"].pane


def test_spotify_rows_go_to_the_cars_spotify_through_home(env, monkeypatch):
    client, _, _ = env
    home = client.application.extensions["home"]
    login(client, FRIEND)
    assert client.post("/drive/spotify/play", json={"uri": "spotify:track:" + "1" * 22}).status_code == 403
    login(client, ADMIN)
    assert client.post("/drive/spotify/play", json={"uri": "spotify:album:" + "1" * 22}).status_code == 400
    sent = []
    monkeypatch.setattr(home, "online", lambda: True)
    monkeypatch.setattr(home, "submit", lambda kind, **p: sent.append((kind, p)) or "j1")
    monkeypatch.setattr(home, "wait", lambda job, timeout: {"result": {"ok": True, "device": "T3-P0"}})
    r = client.post("/drive/spotify/play", json={"uri": "spotify:track:" + "1" * 22, "pos_ms": 61000})
    assert r.status_code == 200 and r.get_json()["device"] == "T3-P0"
    assert sent[-1] == ("spotify", {"op": "car_play", "uri": "spotify:track:" + "1" * 22, "pos_ms": 61000})
    monkeypatch.setattr(home, "wait", lambda job, timeout: {"error": "the car's Spotify isn't among his devices (open Spotify in the car)"})
    r = client.get("/drive/spotify/car")
    assert r.status_code == 502 and "open Spotify in the car" in r.get_json()["why"]
    monkeypatch.setattr(home, "wait", lambda job, timeout: None)
    assert client.get("/drive/spotify/mix").status_code == 504
