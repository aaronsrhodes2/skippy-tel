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
    for k in ("k-book", "k-disc", "k-lib", "k-skippy", "k-play", "k-house"):        # the six keys, and nothing else to press
        assert f'id="{k}"' in html
    assert 'id="k-next"' not in html and "skippy-face.js" in html and html.count("data-lamp=") == 8
    assert 'class="hit play"' in html and 'class="hit next"' in html                 # play/pause and next share the fifth key
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


class Home:
    def __init__(self, online=True, caps=()):
        self._online, self.caps = online, set(caps)

    def online(self):
        return self._online

    def can(self, name):
        return self._online and name in self.caps


def test_the_house_lamps_say_what_is_up_and_name_what_is_down():
    import drive
    got = drive.house_lamps(Home(caps={"house", "music_gate", "book_gate", "voice_gate"}), {"core": {"ok": True}, "spotify": {"ok": False}}, 4.0)
    assert {l["id"]: l["ok"] for l in got["lamps"]} == {"pc": True, "core": True, "hub": True, "house": True, "music": True, "books": True,
                                                           "voice": True, "spotify": False}
    assert got["verdict"] == "warn" and got["down"] == ["Spotify"]
    unknown = drive.house_lamps(Home(caps={"house"}), {}, None)                 # no doctor check yet: not known, not "down"
    assert {l["id"]: l["ok"] for l in unknown["lamps"]}["core"] is None and "the pods" in unknown["down"]
    silent = drive.house_lamps(Home(online=False), {"core": {"ok": True}, "spotify": {"ok": True}}, 2.0)
    assert silent["verdict"] == "down" and {l["id"]: l["ok"] for l in silent["lamps"]}["core"] is True
    assert "skippy-pc" in silent["down"] and "Spotify" in silent["down"]          # nothing at home can be seen past a silent skippy-pc


def test_the_house_route_is_the_captains(env):
    client, _, _ = env
    login(client, FRIEND)
    assert client.get("/drive/house").status_code == 403
    login(client, ADMIN)
    r = client.get("/drive/house")
    assert r.status_code == 200 and len(r.get_json()["lamps"]) == 8 and r.headers["Cache-Control"] == "no-store"


def test_every_element_and_setting_the_script_reads_is_on_the_page(env):
    """drive-app.js once wrote to an element the page no longer had: the throw lost the wake word every time it loaded."""
    import re
    from pathlib import Path
    client, _, _ = env
    login(client, ADMIN)
    html = client.get("/drive").get_data(as_text=True)
    js = (Path(__file__).resolve().parents[1] / "static" / "drive-app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'\$\("#([A-Za-z0-9-]+)', js))
    HUD_ONLY = {"k-hub", "said", "heard", "k-flash", "k-forget"}                         # the glasses' view only (/hud): the script checks they are there before using them
    assert ids and not [i for i in ids - HUD_ONLY if f'id="{i}"' not in html]
    hud = client.get("/hud").get_data(as_text=True)
    assert not [i for i in ids if f'id="{i}"' not in hud]
    keys = set(re.findall(r"cfg\.([A-Za-z]+)", js))
    attr = lambda k: "data-" + re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), k)   # noqa: E731
    assert keys and not [k for k in keys - {"wsSend", "wsEvents", "vision", "visionDelete", "visionKeep", "visionRead", "visionLook"} if attr(k) + "=" not in html]   # purple's doors and the camera key's: /hud only
    assert not [k for k in keys if attr(k) + "=" not in hud]


def test_bilbys_last_play_is_the_newest_connect_song_with_its_place_else_the_played_list():
    import drive
    now = 10_000.0
    listing = [{"name": "Office (Skippy)", "age": 300, "state": {"track": "A/One.mp3", "pos": 61.0, "title": "One", "artist": "A"}},
               {"name": "Phone", "age": 900, "state": {"track": "B/Two.mp3", "pos": 5.0}},
               {"name": "Book box", "age": 10, "state": {"track": "", "book": {"id": "aces-1"}}}]           # a book, not Bilby
    got = drive.bilby_last(listing, {"A/One.mp3": [3, now - 330]}, now)
    assert got == {"at": 9700, "track": "A/One.mp3", "pos": 61.0, "title": "One", "artist": "A", "where": "Office (Skippy)"}
    newer = drive.bilby_last(listing, {"C/Three.mp3": [1, now - 10]}, now)           # heard since, on a player Connect forgot (a restart)
    assert newer["track"] == "C/Three.mp3" and newer["pos"] == 0.0
    assert drive.bilby_last([], {}, now) is None


def test_the_last_route_is_the_captains_and_says_bilby(env):
    client, _, _ = env
    login(client, FRIEND)
    assert client.get("/drive/last").status_code == 403
    login(client, ADMIN)
    r = client.get("/drive/last")
    assert r.status_code == 200 and "bilby" in r.get_json() and r.headers["Cache-Control"] == "no-store"
