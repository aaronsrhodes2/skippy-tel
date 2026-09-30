"""music-gate: nothing without a pass the portal signed, only MP3/AAC inside the library, seeking works, and the
catalog is built from tags with the folder names as the fallback."""
import gzip
import json

import pytest

import gate
from library import Library

NOW = 1_800_000_000
KEY = gate.gate_key("home-token")


def fake_tags(path):
    if path.name.startswith("bad"):
        return None          # unreadable, or Apple Lossless
    return {"t": "Tagged " + path.stem if "tagged" in path.name else "", "a": "Tag Artist", "al": "Tag Album",
            "n": 3, "d": 1, "g": "Rock", "s": 200, "art": False}


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "music"
    (root / "Artist A" / "Album 1").mkdir(parents=True)
    (root / "Artist A" / "Album 1" / "01 - Artist A - Song One.mp3").write_bytes(b"ID3" + bytes(range(256)) * 40)
    (root / "Artist A" / "Album 1" / "tagged.m4a").write_bytes(b"m4a")
    (root / "Artist A" / "Album 1" / "bad.m4a").write_bytes(b"alac")
    (root / "Artist A" / "Album 1" / "lossless.flac").write_bytes(b"flac")
    (root / "Artist A" / "Album 1" / "old.wma").write_bytes(b"wma")
    (root / "loose.mp3").write_bytes(b"x")
    (tmp_path / "secret.mp3").write_bytes(b"outside")
    library = Library(root, tmp_path / "state", reader=fake_tags, clock=lambda: NOW)
    library.scan()
    return library


@pytest.fixture
def client(lib):
    return gate.create_app(lib, KEY, clock=lambda: NOW).test_client()


def catalog(lib):
    data = json.loads(gzip.decompress(lib.catalog_gz))
    return [dict(zip(data["fields"], t)) for t in data["tracks"]]


def test_catalog_has_only_mp3_and_aac_with_folder_names_first(lib):
    tracks = {t["p"]: t for t in catalog(lib)}
    assert set(tracks) == {"Artist A/Album 1/01 - Artist A - Song One.mp3", "Artist A/Album 1/tagged.m4a", "loose.mp3"}
    one = tracks["Artist A/Album 1/01 - Artist A - Song One.mp3"]
    assert (one["t"], one["a"], one["al"], one["n"], one["g"], one["s"]) == ("Song One", "Artist A", "Album 1", 3, "Rock", 200)
    assert tracks["Artist A/Album 1/tagged.m4a"]["t"] == "Tagged tagged"
    assert (tracks["loose.mp3"]["a"], tracks["loose.mp3"]["al"]) == ("Tag Artist", "Tag Album")   # no folders: the tags


def test_tag_cache_reads_each_file_once(lib, tmp_path):
    reads = []
    again = Library(lib.root, tmp_path / "state", reader=lambda p: reads.append(p) or fake_tags(p))
    assert again.count == 3            # last run's catalog is there before any scan
    again.scan()
    assert reads == []                 # nothing changed: no file opened
    (lib.root / "Artist A" / "Album 1" / "new.mp3").write_bytes(b"new")
    again.scan()
    assert [p.name for p in reads] == ["new.mp3"] and again.count == 4


def test_pass_rules():
    good = gate.sign(KEY, NOW + 3600)
    assert gate.valid(KEY, good, NOW)
    assert not gate.valid(KEY, good, NOW + 3601)                                   # expired
    assert not gate.valid(gate.gate_key("other"), good, NOW)                       # someone else's key
    assert not gate.valid(KEY, gate.sign(KEY, NOW + gate.MAX_PASS_S + 60), NOW)    # too long-lived to be ours
    assert not gate.valid(KEY, good[:-1] + ("0" if good[-1] != "0" else "1"), NOW)
    assert not gate.valid(b"", good, NOW)                                          # no home token: nothing opens


def test_nothing_without_a_pass(client):
    for path in ("/t/nope/catalog.json", f"/t/{NOW + 60}.{'0' * 32}/a/loose.mp3", "/", "/music/loose.mp3"):
        assert client.get(path).status_code in (403, 404), path
    assert client.get("/health").get_json()["tracks"] == 3


def test_catalog_and_seeking(client):
    tok = gate.sign(KEY, NOW + 3600)
    r = client.get(f"/t/{tok}/catalog.json")
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    assert client.get(f"/t/{tok}/catalog.json", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304
    part = client.get(f"/t/{tok}/a/Artist%20A/Album%201/01%20-%20Artist%20A%20-%20Song%20One.mp3",
                      headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and len(part.data) == 10 and part.mimetype == "audio/mpeg"
    assert client.get(f"/t/{tok}/a/Artist%20A/Album%201/tagged.m4a").mimetype == "audio/mp4"


@pytest.mark.parametrize("rel", ["../secret.mp3", "Artist%20A/../../secret.mp3", "Artist%20A/Album%201/lossless.flac",
                                 "Artist%20A/Album%201/old.wma", "nothing.mp3", "Artist%20A", "..%5Csecret.mp3"])
def test_only_library_audio_is_served(client, rel):
    tok = gate.sign(KEY, NOW + 3600)
    assert client.get(f"/t/{tok}/a/{rel}").status_code == 404
