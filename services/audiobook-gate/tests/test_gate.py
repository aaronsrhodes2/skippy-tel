import gzip
import json
import time
from pathlib import Path

import pytest

import gate

TOKEN = "t" * 40
KEY = gate.gate_key(TOKEN)
NOW = 1_800_000_000


def _pass(exp=NOW + 3600):
    return gate.sign(KEY, exp)


def _make(tmp_path: Path):
    root = tmp_path / "ab"
    (root / "books").mkdir(parents=True)
    (root / "art").mkdir()
    (root / "books" / "Dune - Part One.m4b").write_bytes(bytes(range(256)) * 40)
    (root / "books" / "No Meta.m4b").write_bytes(b"x" * 100)
    (root / "books" / "cover.pdf").write_bytes(b"%PDF")
    from PIL import Image
    Image.new("RGB", (500, 500), (200, 30, 30)).save(root / "art" / "Dune - Part One.jpg")
    (root / "books.json").write_text(json.dumps([{
        "filename": "Dune - Part One", "title": "Dune", "author": "Frank Herbert", "narrated_by": "Scott Brick", "seconds": "3600",
        "summary": "<p>Spice &amp; sand.</p><p>Worms.</p>", "genre": "Sci-Fi:Classics", "release_date": "2007-01-02", "finished": "true",
        "chapters": [{"title": "One", "start_offset_ms": 0}, {"title": "Two", "start_offset_ms": 1800000}]}]), encoding="utf-8")
    return root


@pytest.fixture()
def env(tmp_path):
    root = _make(tmp_path)
    lib = gate.Library(root, clock=lambda: NOW)
    lib.refresh(force=True)
    hub = tmp_path / "audiobooks.json"
    places = gate.Places(hub, tmp_path / "inbox", clock=lambda: NOW)
    app = gate.create_app(lib, places, KEY, clock=lambda: NOW)
    return app.test_client(), lib, places, hub


def test_pass_labels_are_separate():
    assert gate.valid(KEY, _pass(), NOW)
    music = __import__("hmac").new(__import__("hashlib").sha256(TOKEN.encode()).hexdigest().encode(), b"music-gate-v1", "sha256").digest()
    assert not gate.valid(KEY, gate.sign(music, NOW + 3600), NOW)           # a music pass never opens the books
    assert not gate.valid(KEY, _pass(NOW - 1), NOW)                        # expired
    assert not gate.valid(KEY, _pass(NOW + gate.MAX_PASS_S + 5), NOW)       # claims too long
    assert not gate.valid(b"", _pass(), NOW)


def test_id_matches_the_hub():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ab", Path(__file__).resolve().parents[2] / "satellite" / "audiobooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("Dune - Part One.m4b", "A Deadly Education - A Novel (The Scholomance, Book 1).m4b", "No Meta.m4b"):
        assert gate.slug(name) == mod.slug(name)


def test_health_needs_no_pass_and_says_ready(env):
    c, *_ = env
    r = c.get("/health")
    assert r.status_code == 200 and r.get_json()["ready"] is True and r.get_json()["books"] == 2


def test_everything_else_needs_a_pass(env):
    c, *_ = env
    for path in ("/t/bad/catalog.json", "/t/bad/places.json", "/t/bad/a/x", "/t/bad/art/x"):
        assert c.get(path).status_code == 403
    assert c.put("/t/bad/place/x", json={"pos": 1}).status_code == 403
    assert c.get(f"/t/{_pass()}/nope").status_code == 404


def test_catalog(env):
    c, lib, *_ = env
    r = c.get(f"/t/{_pass()}/catalog.json")
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
    books = {b["id"]: b for b in json.loads(gzip.decompress(r.data))["books"]}
    dune = books["dune-part-one-m4b"]
    assert (dune["t"], dune["a"], dune["n"], dune["g"], dune["y"], dune["oa"], dune["c"]) == ("Dune", "Frank Herbert", "Scott Brick", "Sci-Fi", "2007", True, True)
    assert dune["s"] == "Spice & sand.\nWorms." and dune["ch"] == [["One", 0.0], ["Two", 1800.0]] and dune["d"] == 3600.0
    assert books["no-meta-m4b"]["t"] == "No Meta" and books["no-meta-m4b"]["c"] is False      # a file OpenAudible never described still plays
    assert "_file" not in dune and "pdf" not in json.dumps(books)                              # no paths leave, no non-audio files
    assert c.get(f"/t/{_pass()}/catalog.json", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304


def test_audio_with_range(env):
    c, *_ = env
    full = c.get(f"/t/{_pass()}/a/dune-part-one-m4b")
    assert full.status_code == 200 and full.headers["Content-Type"].startswith("audio/mp4") and len(full.data) == 10240
    part = c.get(f"/t/{_pass()}/a/dune-part-one-m4b", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.data == bytes(range(10, 20))
    assert part.headers["Content-Range"] == "bytes 10-19/10240"


def test_cover_sizes(env):
    c, *_ = env
    small = c.get(f"/t/{_pass()}/art/dune-part-one-m4b?w=100")
    assert small.status_code == 200 and small.mimetype == "image/jpeg" and len(small.data) < 5000
    full = c.get(f"/t/{_pass()}/art/dune-part-one-m4b?w=900")
    assert full.status_code == 200 and len(full.data) > len(small.data)
    assert c.get(f"/t/{_pass()}/art/dune-part-one-m4b?w=abc").status_code == 400
    assert c.get(f"/t/{_pass()}/art/no-meta-m4b").status_code == 404


def test_place_roundtrip_and_hub_merge(env):
    c, lib, places, hub = env
    assert c.get(f"/t/{_pass()}/places.json").get_json() == {"current": None, "books": {}}
    r = c.put(f"/t/{_pass()}/place/dune-part-one-m4b", json={"pos": 1234.56, "chapter": 0, "speed": 1.25, "finished": False})
    assert r.status_code == 200
    got = c.get(f"/t/{_pass()}/places.json").get_json()
    assert got["current"] == "dune-part-one-m4b"
    assert got["books"]["dune-part-one-m4b"] == {"pos": 1234.6, "chapter": 0, "speed": 1.25, "last_played": NOW, "finished": False}
    # the hub has a newer place for the same book: it wins, and its "current" too
    hub.write_text(json.dumps({"current": "dune-part-one-m4b", "books": {"dune-part-one-m4b": {"pos": 99.0, "chapter": 0, "speed": 1.0, "last_played": NOW + 50, "finished": False}}}))
    assert c.get(f"/t/{_pass()}/places.json").get_json()["books"]["dune-part-one-m4b"]["pos"] == 99.0


def test_place_validation(env):
    c, *_ = env
    url = f"/t/{_pass()}/place/dune-part-one-m4b"
    p = c.put(url, json={"pos": 999999, "chapter": -3, "speed": 9, "finished": "yes"}).get_json()
    assert p["pos"] == 3600.0 and p["chapter"] == 0 and p["speed"] == 3.0 and p["finished"] is False
    assert c.put(url, data="[1]", content_type="application/json").status_code == 400
    assert c.put(f"/t/{_pass()}/place/not-a-book", json={"pos": 1}).status_code == 404
    assert c.put(url, data=b"{" + b" " * 5000 + b"}", content_type="application/json").status_code == 413


def test_funnel_mount_prefix_is_stripped(env):
    c, *_ = env
    assert c.get("/books/health").status_code == 200
    assert c.get(f"/books/t/{_pass()}/catalog.json").status_code == 200


def test_cors_preflight(env):
    c, *_ = env
    r = c.open(f"/t/{_pass()}/place/x", method="OPTIONS")
    assert r.status_code in (200, 204) and "PUT" in r.headers["Access-Control-Allow-Methods"] and r.headers["Access-Control-Allow-Origin"] == "*"
