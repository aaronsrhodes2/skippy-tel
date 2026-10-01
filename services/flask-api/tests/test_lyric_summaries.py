"""GET /music/lyric-summaries: Bilby's two lyrics stores (Mac paths, content flags, a model that sometimes drifts into
Chinese) turned into {library-relative path: one sentence} and {"artist<TAB>title": one sentence} for the music-gate. One test
per rule, with keys shaped like the real ones. Run on their own: `python -m pytest services/flask-api/tests -q` (the portal
has an `app.py` of its own)."""
import json
import os
import unicodedata

import pytest
from flask import Flask

from app.routes import lyric_summaries as ls

MAC = "/:Users/aaronrhodes/development/music organize/corrected_music/"


def entry(summary="The song explores betrayal.", **extra):
    return {"summary": summary, "theme": "", "flags": [], **extra}


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Scratch stores in place of /drive-state's, and a client for the route; write()/write_songs() change the files."""
    index, dedup = tmp_path / "lyrics_index.json", tmp_path / "lyrics_dedup.json"
    monkeypatch.setattr(ls, "_INDEX_PATH", index)
    monkeypatch.setattr(ls, "_DEDUP_PATH", dedup)
    monkeypatch.setattr(ls, "_files", ls._Store(lambda: ls._INDEX_PATH, ls.build))
    monkeypatch.setattr(ls, "_songs", ls._Store(lambda: ls._DEDUP_PATH, ls.build_songs))
    monkeypatch.setattr(ls, "_good", {"sig": None, "body": b"", "etag": ""})
    app = Flask(__name__)
    app.register_blueprint(ls.bp)

    def put(path, data, raw):
        path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
        st = path.stat()
        bump = getattr(put, "n", 0) + 1
        put.n = bump
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 1_000_000_000))   # a distinct mtime, even on a coarse clock

    class S:
        client = app.test_client()
        index_path, dedup_path = index, dedup

        def get(self, **kw):
            return self.client.get("/music/lyric-summaries", **kw)

        def write(self, data, raw=None):
            put(index, data, raw)

        def write_songs(self, data, raw=None):
            put(dedup, data, raw)
    return S()


# ── the key: Mac path → library-relative path ───────────────────────────────────────────────────────────────────────

def rel(key):
    return ls.classify(key)[0]


def test_the_mac_prefix_is_taken_off():
    assert rel(MAC + "Delerium feat. Phildel/Mythologie (2016)/Zero.m4a") == "Delerium feat. Phildel/Mythologie (2016)/Zero.m4a"


def test_the_prefix_without_the_odd_colon_too():
    # 20 of the real keys read /Users/aaronrhodes/... (no colon): the same library
    assert rel("/Users/aaronrhodes/development/music organize/corrected_music/Wolfsheim/Casting Shadows (2003)/Approaching Lightspeed.mp3") \
        == "Wolfsheim/Casting Shadows (2003)/Approaching Lightspeed.mp3"


def test_a_windows_style_key_and_stray_slashes_come_out_the_same():
    assert rel("C:\\music organize\\corrected_music\\Bauhaus\\Spirit (1989)\\Slice of Life.mp3") == "Bauhaus/Spirit (1989)/Slice of Life.mp3"
    assert rel(MAC + "Bauhaus//Spirit (1989)///Slice of Life.mp3") == "Bauhaus/Spirit (1989)/Slice of Life.mp3"


def test_unicode_is_composed():
    nfd = unicodedata.normalize("NFD", "Abwärts/Amok Koma (1980)/Ärger.mp3")
    assert nfd != "Abwärts/Amok Koma (1980)/Ärger.mp3"
    assert rel(MAC + nfd) == "Abwärts/Amok Koma (1980)/Ärger.mp3"


def test_everything_after_the_first_corrected_music_is_the_path():
    assert rel(MAC + "corrected_music/Odd/Album/Song.mp3") == "corrected_music/Odd/Album/Song.mp3"   # an artist named so, not a second root


@pytest.mark.parametrize("key,why", [
    ("/:Users/aaronrhodes/Music/Sina True/alternative/The Sisters Of Mercy/Rare Songs/More.mp3", "outside_library"),
    ("/:Library/Application Support/Native Instruments/Traktor Pro 4/Factory Sounds/Bumpin Flava - Bumpy.mp3", "outside_library"),
    (MAC + "Sisters/Rare Songs/More.flac", "not_playable"),
    (MAC + "Sisters/Rare Songs/More.ogg", "not_playable"),
    (MAC + "Sisters/Rare Songs/More", "not_playable"),
    (MAC + "../../etc/passwd.mp3", "bad_path"),
    (MAC + "Artist/./Song.mp3", "bad_path"),
    (MAC + "Artist/../../Song.mp3", "bad_path"),
    (MAC, "outside_library"),
    ("", "outside_library"),
    (None, "bad_path"),
    (7, "bad_path"),
    (MAC + "Artist/Song\x00.mp3", "bad_path"),
])
def test_what_the_player_can_never_use_is_left_out(key, why):
    assert ls.classify(key) == (None, why)


@pytest.mark.parametrize("ext", ["mp3", "MP3", "m4a", "M4A", "aac"])
def test_the_three_playable_types_are_kept(ext):
    assert rel(MAC + f"A/B/C.{ext}") == f"A/B/C.{ext}"


# ── the summary ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_summary_is_trimmed_to_one_line():
    assert ls.clean_summary("  A  song\nabout\tthings.  ") == "A song about things."


@pytest.mark.parametrize("bad", [
    "", "   ", None, 5, ["a"], "x" * 401,
    "A man陷入绝望，质疑生命的意义，最终自杀。",                                   # the real kind of leak: Chinese mid-sentence
    "A song劝说某人不要离开，强调留下来的重要性。",
    "Explores the痛苦的翻译结果在这里并不重要",
    "A nostalgic lament for lost love and 逝去的时光。",
    "ひらがな", "한국어",
])
def test_a_garbled_or_empty_summary_is_left_out(bad):
    assert ls.clean_summary(bad) is None


def test_ordinary_punctuation_and_accents_are_fine():
    text = "A tender ode to Zürich: love, loss & the city's rain; ‘gone’ — “forever”… 100%?"
    assert ls.clean_summary(text) == text


# ── the per-file store ──────────────────────────────────────────────────────────────────────────────────────────────

def test_flags_and_theme_never_leave(served):
    served.write({MAC + "A/B/C.mp3": entry("About loss.", theme="grief", flags=["extreme_violence", "racism"])})
    r = served.get()
    assert r.status_code == 200 and r.mimetype == "application/json"
    body = r.get_json()
    assert body["by"] == {"A/B/C.mp3": "About loss."} and body["v"] == 1 and body["n"] == 1 and body["spec_version"] == "1"
    raw = r.get_data(as_text=True)
    assert "extreme_violence" not in raw and "racism" not in raw and "grief" not in raw and "flags" not in raw


def test_what_was_left_out_is_counted(served):
    served.write({
        MAC + "A/B/ok.mp3": entry(),
        MAC + "A/B/flac.flac": entry(),
        "/:Users/aaronrhodes/Music/Sina True/x/y.mp3": entry(),
        MAC + "A/B/garbled.mp3": entry("A man陷入绝望"),
        MAC + "A/B/empty.mp3": entry(""),
        MAC + "A/B/nosummary.mp3": {"flags": []},
        MAC + "A/B/notadict.mp3": "a string",
        MAC + "../x.mp3": entry(),
    })
    body = served.get().get_json()
    assert list(body["by"]) == ["A/B/ok.mp3"]
    assert body["dropped"] == {"outside_library": 1, "not_playable": 1, "bad_path": 1, "bad_summary": 4, "duplicate": 0}


def test_two_keys_that_straighten_to_one_path_keep_the_first(served):
    nfd = unicodedata.normalize("NFD", "Abwärts/A (1980)/Ärger.mp3")
    served.write({MAC + nfd: entry("first"), MAC + unicodedata.normalize("NFC", nfd): entry("second")})
    body = served.get().get_json()
    assert body["by"] == {"Abwärts/A (1980)/Ärger.mp3": "first"} and body["dropped"]["duplicate"] == 1


def test_the_order_of_the_index_is_kept(served):
    keys = [f"{c}/Album/Song.mp3" for c in "zyxwv"]
    served.write({MAC + k: entry(k) for k in keys})
    assert list(served.get().get_json()["by"]) == keys


def test_non_ascii_is_sent_as_it_is(served):
    served.write({MAC + "Röyksopp/Melody A.M. (2001)/Eple.mp3": entry("Bright — bittersweet “pop”.")})
    raw = served.get().get_data()
    assert "Röyksopp".encode("utf-8") in raw and b"\\u" not in raw


def test_every_key_is_a_clean_library_path(served):
    """Whatever the index holds, nothing leaves that could climb out of the library or isn't a playable file."""
    nasty = [MAC + k for k in ("../a.mp3", "a/../../b.mp3", "/abs.mp3", "a\\..\\b.mp3", "ok/Fine (2001)/Song.mp3", "a//b.m4a")]
    served.write({k: entry() for k in nasty})
    for path in served.get().get_json()["by"]:
        assert not path.startswith("/") and ".." not in path.split("/") and "" not in path.split("/") and "\\" not in path
        assert path.lower().endswith((".mp3", ".m4a", ".aac"))


# ── the per-song store ──────────────────────────────────────────────────────────────────────────────────────────────

def test_songs_come_with_the_macs_keys_and_only_the_summary(served):
    served.write_songs({"priest\tcall my name": entry("A dark, intimate connection.", flags=["racism"], theme="x"),
                        "bel canto\ta shoulder to the wheel": entry("Rebuilding oneself.")})
    body = served.get().get_json()
    assert body["songs"] == {"priest\tcall my name": "A dark, intimate connection.",
                             "bel canto\ta shoulder to the wheel": "Rebuilding oneself."}
    assert body["n_songs"] == 2 and body["by"] == {}                     # no per-file store: its part is empty, not an error
    raw = served.get().get_data(as_text=True)
    assert "racism" not in raw and "flags" not in raw


def test_bad_song_keys_and_summaries_are_left_out_and_counted(served):
    served.write_songs({"no tab here": entry(), "\tno artist": entry(), "no title\t": entry(), "  \t  ": entry(),
                        "a\tgarbled": entry("A song劝说某人"), "a\tempty": entry(""), "a\tfine": entry("Fine."),
                        "b\tnot a dict": "x"})
    body = served.get().get_json()
    assert body["songs"] == {"a\tfine": "Fine."}
    assert body["songs_dropped"] == {"bad_key": 4, "bad_summary": 3, "duplicate": 0}


def test_song_keys_are_composed_unicode(served):
    nfd = unicodedata.normalize("NFD", "abwärts\tärger")
    served.write_songs({nfd: entry("About rage.")})
    assert list(served.get().get_json()["songs"]) == [unicodedata.normalize("NFC", nfd)]


def test_either_store_alone_is_enough(served):
    served.write({MAC + "A/B/C.mp3": entry()})
    assert served.get().get_json()["songs"] == {}
    served.dedup_path.write_text(json.dumps({"x\ty": entry("Y.")}), encoding="utf-8")
    assert served.get().get_json()["songs"] == {"x\ty": "Y."}            # picked up with no restart


# ── conditional requests, and stores that aren't there or are half-synced ───────────────────────────────────────────

def test_etag_and_304(served):
    served.write({MAC + "A/B/C.mp3": entry()})
    first = served.get()
    assert first.status_code == 200 and first.headers["ETag"] and first.headers["Cache-Control"] == "no-cache"
    again = served.get(headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304 and not again.data
    assert served.get(headers={"If-None-Match": f'W/{first.headers["ETag"]}, "x"'}).status_code == 304
    served.write({MAC + "A/B/C.mp3": entry("changed")})                       # the Mac wrote a new index
    assert served.get(headers={"If-None-Match": first.headers["ETag"]}).status_code == 200
    again = served.get()
    served.write_songs({"a\tb": entry()})                                     # ...or a new song store
    assert served.get(headers={"If-None-Match": again.headers["ETag"]}).status_code == 200


def test_a_store_is_read_once_until_it_changes(served, monkeypatch):
    served.write({MAC + "A/B/C.mp3": entry()})
    served.write_songs({"a\tb": entry()})
    served.get()
    monkeypatch.setattr(ls, "build", lambda index: pytest.fail("parsed the index again"))
    monkeypatch.setattr(ls, "build_songs", lambda index: pytest.fail("parsed the songs again"))
    assert served.get().status_code == 200


def test_no_stores_is_a_503_with_the_usual_envelope(served):
    r = served.get()
    assert r.status_code == 503 and r.get_json()["error"] == "lyrics_unavailable" and r.get_json()["spec_version"] == "1"


@pytest.mark.parametrize("raw", ["{not json", "", "[1, 2]", '"text"', "null"])
def test_damaged_stores_are_a_503_not_a_500(served, raw):
    served.write(None, raw=raw)
    served.write_songs(None, raw=raw)
    assert served.get().status_code == 503


def test_one_damaged_store_leaves_the_other_serving(served):
    served.write({MAC + "A/B/C.mp3": entry("kept")})
    served.write_songs(None, raw="{half")
    r = served.get()
    assert r.status_code == 200 and r.get_json()["by"] == {"A/B/C.mp3": "kept"} and r.get_json()["songs"] == {}


def test_a_good_copy_survives_a_store_being_rewritten_or_gone(served):
    served.write({MAC + "A/B/C.mp3": entry("kept")})
    served.write_songs({"a\tb": entry("kept too")})
    assert served.get().get_json()["by"] == {"A/B/C.mp3": "kept"}
    served.write(None, raw="{half a file, mid-sync")                          # Syncthing is rewriting it
    served.write_songs(None, raw="")
    body = served.get().get_json()
    assert body["by"] == {"A/B/C.mp3": "kept"} and body["songs"] == {"a\tb": "kept too"}
    served.index_path.unlink()
    served.dedup_path.unlink()
    assert served.get().get_json()["by"] == {"A/B/C.mp3": "kept"}
