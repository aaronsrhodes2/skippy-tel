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
            "n": 3, "d": 1, "g": "Rock", "s": 200, "art": False, "y": 1999 if "tagged" in path.name else 0}


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


# Every route behind the pass, new ones included: a missing, forged, expired or other-key pass is a 403, whatever follows it
# (a bad ?w= or a path that doesn't exist must not be answered differently, or a stranger could probe the library).
ROUTES = ["catalog.json", "lyrics.json", "a/Artist%20A/Album%201/tagged.m4a", "art/Artist%20A/Album%201/tagged.m4a",
          "art/Artist%20A/Album%201/tagged.m4a?w=96", "art/Artist%20A/Album%201/tagged.m4a?w=banana", "art/nothing.mp3?w=96",
          "art/..%2Fsecret.mp3?w=96"]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("bad", ["nope", f"{NOW + 3600}.{'0' * 32}", f"{NOW - 5}.{'a' * 32}", gate.sign(gate.gate_key("other"), NOW + 3600)])
def test_every_route_wants_a_valid_pass(client, route, bad):
    assert client.get(f"/t/{bad}/{route}").status_code == 403


def test_a_pass_is_never_logged(client, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    tok = gate.sign(KEY, NOW + 3600)
    for route in ROUTES:
        client.get(f"/t/{tok}/{route}")
    client.get("/t/nope/catalog.json")
    assert tok not in caplog.text and tok.split(".")[1] not in caplog.text


def test_the_player_can_read_the_etag_and_send_if_none_match(client):
    """The player is on another origin: without these headers it can't read the ETag to cache the catalog by it, and its
    If-None-Match would be refused at the preflight."""
    tok = gate.sign(KEY, NOW + 3600)
    r = client.get(f"/t/{tok}/catalog.json")
    assert "ETag" in r.headers["Access-Control-Expose-Headers"]
    pre = client.options(f"/t/{tok}/catalog.json", headers={"Origin": "https://portal.example", "Access-Control-Request-Method": "GET",
                                                           "Access-Control-Request-Headers": "if-none-match"})
    assert pre.status_code in (200, 204) and "If-None-Match" in pre.headers["Access-Control-Allow-Headers"]
    assert "GET" in pre.headers["Access-Control-Allow-Methods"] and pre.headers["Access-Control-Allow-Origin"] == "*"


def test_if_none_match_in_the_forms_a_proxy_may_send(client):
    tok = gate.sign(KEY, NOW + 3600)
    etag = client.get(f"/t/{tok}/catalog.json").headers["ETag"]
    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        assert client.get(f"/t/{tok}/catalog.json", headers={"If-None-Match": header}).status_code == 304, header
    assert client.get(f"/t/{tok}/catalog.json", headers={"If-None-Match": '"nope"'}).status_code == 200


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


@pytest.mark.parametrize("rel", ["../secret.mp3", "Artist%20A/../../secret.mp3", "Artist%20A/Album%201/lossless.flac",
                                 "Artist%20A/Album%201/old.wma", "nothing.mp3", "Artist%20A", "..%5Csecret.mp3",
                                 "%2e%2e/secret.mp3", "Artist%20A%2F..%2F..%2Fsecret.mp3", "%00.mp3"])
def test_only_library_audio_has_art_with_or_without_a_width(client, rel):
    tok = gate.sign(KEY, NOW + 3600)
    for query in ("", "?w=96", "?w=768"):
        assert client.get(f"/t/{tok}/art/{rel}{query}").status_code == 404, query


def test_a_symlink_out_of_the_library_is_not_followed(client, lib, tmp_path):
    link = lib.root / "Artist A" / "escape.mp3"
    try:
        link.symlink_to(tmp_path / "secret.mp3")
    except OSError:
        pytest.skip("this machine can't make symlinks")
    tok = gate.sign(KEY, NOW + 3600)
    assert client.get(f"/t/{tok}/a/Artist%20A/escape.mp3").status_code == 404
    assert client.get(f"/t/{tok}/art/Artist%20A/escape.mp3?w=96").status_code == 404


# ── the catalog's new fields ────────────────────────────────────────────────────────────────────────────────────────

def test_fields_grow_only_at_the_end(lib):
    data = json.loads(gzip.decompress(lib.catalog_gz))
    assert data["fields"][:9] == ["p", "t", "a", "al", "n", "d", "g", "s", "art"]      # what an older player reads
    assert data["fields"][9:] == ["y", "m"] and all(len(t) == len(data["fields"]) for t in data["tracks"])


def test_year_and_date_added(lib):
    tracks = {t["p"]: t for t in catalog(lib)}
    st = (lib.root / "Artist A" / "Album 1" / "tagged.m4a").stat()
    assert tracks["Artist A/Album 1/tagged.m4a"]["y"] == 1999                  # from its tag
    assert tracks["Artist A/Album 1/tagged.m4a"]["m"] == int(st.st_mtime)      # "date added" is the file's mtime, in seconds
    assert tracks["loose.mp3"]["y"] == 0                                       # no tag, no folder year: unknown is 0


def test_a_year_in_the_album_folder_fills_in_for_a_missing_tag(tmp_path):
    root = tmp_path / "music"
    (root / "Delerium" / "Mythologie (2016)").mkdir(parents=True)
    (root / "Delerium" / "Mythologie (2016)" / "Zero.mp3").write_bytes(b"x")
    (root / "Delerium" / "Singles").mkdir()
    (root / "Delerium" / "Singles" / "Silence.mp3").write_bytes(b"x")
    (root / "Delerium" / "Remixes (19)").mkdir()
    (root / "Delerium" / "Remixes (19)" / "Silence.mp3").write_bytes(b"x")
    library = Library(root, tmp_path / "state", reader=lambda p: {"t": p.stem, "y": 0, "s": 1})
    library.scan()
    years = {t["p"].split("/")[1]: t["y"] for t in catalog(library)}
    assert years == {"Mythologie (2016)": 2016, "Singles": 0, "Remixes (19)": 0}


def test_the_etag_is_the_same_after_a_restart_and_changes_with_the_catalog(lib, tmp_path):
    import hashlib
    first = lib.etag
    # a hash of the content, not Python's hash(): that one is salted per process, which would change it at every restart
    assert first == f'"3-{hashlib.sha1(lib.catalog_gz).hexdigest()[:12]}"'
    again = Library(lib.root, tmp_path / "state2", reader=fake_tags, clock=lambda: NOW)
    again.scan()
    assert again.etag == first                      # a player's cached catalog survives the gate restarting
    (lib.root / "Artist A" / "Album 1" / "new.mp3").write_bytes(b"new")
    again.scan()
    assert again.etag != first


def test_a_v1_cache_keeps_the_catalog_up_while_the_years_are_read_in(lib, tmp_path):
    """Deploying this version must not leave the player without a catalog for the minutes the re-read takes: the old cache
    still builds one at once (years from the folders), and then every file's tags are read again, once."""
    state = tmp_path / "old-state"
    state.mkdir()
    old_files = {rel: [size, mtime, ({k: v for k, v in tags.items() if k != "y"} if tags else None)]
                 for rel, (size, mtime, tags) in lib._cache.items()}
    (state / "tags.json").write_text(json.dumps({"v": 1, "files": old_files}), encoding="utf-8")
    reads = []
    upgraded = Library(lib.root, state, reader=lambda p: reads.append(p.name) or fake_tags(p))
    assert upgraded.count == 3 and upgraded.catalog_gz                      # playable before any scan
    upgraded.scan()
    assert sorted(reads) == ["01 - Artist A - Song One.mp3", "loose.mp3", "tagged.m4a"]   # the unreadable ones stay cached
    assert {t["p"]: t["y"] for t in catalog(upgraded)}["Artist A/Album 1/tagged.m4a"] == 1999
    reads.clear()
    upgraded.scan()
    assert reads == []                                                      # and only once


def test_a_changed_file_is_read_again(lib, tmp_path):
    reads = []
    again = Library(lib.root, tmp_path / "state", reader=lambda p: reads.append(p.name) or fake_tags(p))
    (lib.root / "loose.mp3").write_bytes(b"longer than before")
    again.scan()
    assert reads == ["loose.mp3"]


class _NoThread:
    """In place of threading.Thread: remembers what would have run in the background, runs nothing."""
    started = []

    def __init__(self, target=None, name="", daemon=False):
        self.target, self.name = target, name

    def start(self):
        _NoThread.started.append(self.name)


def test_from_env_wires_the_lyrics_to_the_library_and_starts_both_refreshers(tmp_path, monkeypatch):
    root = tmp_path / "music"
    (root / "Artist A" / "Album 1").mkdir(parents=True)
    monkeypatch.setenv("MUSIC_ROOT", str(root))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "a-made-up-token-for-this-test")
    monkeypatch.setenv("ART_CACHE_MB", "1")
    monkeypatch.delenv("LYRICS_URL", raising=False)
    monkeypatch.delenv("LYRICS_MATCH", raising=False)
    monkeypatch.setattr(gate.threading, "Thread", _NoThread)
    _NoThread.started = []
    app = gate.from_env()
    assert sorted(_NoThread.started) == ["library-scan", "lyrics-refresh"]
    store, library = app.config["LYRICS"], app.config["LIBRARY"]
    assert store.rebuild in library.on_build                          # a finished scan reaches the lyric store
    assert store.url == "http://flask-api:5000/music/lyric-summaries" and store.with_songs is True
    assert app.config["THUMBS"].max_bytes == 1024 * 1024 and app.config["THUMBS"].dir == tmp_path / "state" / "art-cache"


@pytest.mark.parametrize("value,songs", [("path", False), ("PATH", False), ("song", True), ("nonsense", True), ("", True)])
def test_lyrics_match_setting(tmp_path, monkeypatch, value, songs):
    monkeypatch.setenv("MUSIC_ROOT", str(tmp_path))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "a-made-up-token-for-this-test")
    monkeypatch.setenv("LYRICS_MATCH", value)
    monkeypatch.setattr(gate.threading, "Thread", _NoThread)
    assert gate.from_env().config["LYRICS"].with_songs is songs


@pytest.mark.parametrize("url,threads", [("", ["library-scan"]), ("http://flask-api:5000/music/lyric-summaries", ["library-scan", "lyrics-refresh"])])
def test_no_lyrics_url_means_no_lyrics_thread(tmp_path, monkeypatch, url, threads):
    monkeypatch.setenv("MUSIC_ROOT", str(tmp_path))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "a-made-up-token-for-this-test")
    monkeypatch.setenv("LYRICS_URL", url)
    monkeypatch.setattr(gate.threading, "Thread", _NoThread)
    _NoThread.started = []
    gate.from_env()
    assert sorted(_NoThread.started) == threads


@pytest.mark.parametrize("raw,year", [("2016", 2016), ("2016-03-04", 2016), ("2016-03-04T08:00:00Z", 2016), ("20160304", 2016),
                                      ("03/04/2016", 2016), (" 1999 ", 1999), ("", 0), ("n/a", 0), ("0000", 0), ("0", 0),
                                      ("12", 0), ("9999", 0), ("1999 (remaster)", 1999), (None, 0), (["2001-05"], 2001)])
def test_year_parsing(raw, year):
    from library import _year
    assert _year(raw) == year


def test_read_tags_takes_the_year_from_id3v24_v23_and_mp4(tmp_path, monkeypatch):
    from helpers import make_mp3
    from library import read_tags
    assert read_tags(make_mp3(tmp_path / "a.mp3", TIT2="A", TDRC="2016-03-04"))["y"] == 2016      # ID3v2.4 recording time
    assert read_tags(make_mp3(tmp_path / "b.mp3", v2_version=3, TIT2="B", TYER="1999"))["y"] == 1999  # v2.3's year frame
    assert read_tags(make_mp3(tmp_path / "c.mp3", TIT2="C"))["y"] == 0

    class Mp4:
        info = type("Info", (), {"codec": "mp4a.40.2", "length": 215.4})()
        tags = {"\xa9nam": ["Zero"], "\xa9ART": ["Delerium"], "\xa9alb": ["Mythologie"], "\xa9day": ["2016-03-04T08:00:00Z"],
                "trkn": [(3, 12)], "disk": [(1, 1)], "\xa9gen": ["Electronic"]}
    import mutagen
    monkeypatch.setattr(mutagen, "File", lambda path: Mp4())
    t = read_tags(tmp_path / "zero.m4a")
    assert (t["y"], t["n"], t["s"], t["t"]) == (2016, 3, 215, "Zero")
