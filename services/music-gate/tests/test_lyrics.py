"""Lyric lines: the Mac's per-file and per-song stores against the library's real tracks, one test per matching rule (the keys
below are shaped like the real ones), the copy kept in /state, the refresh, and the route behind the pass."""
import gzip
import json
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import gate
import lyrics
from library import Library
from lyrics import LyricsStore, clean, match, song_key

NOW = 1_800_000_000
KEY = gate.gate_key("home-token")
Z = "Delerium feat. Phildel/Mythologie (2016)/Zero.m4a"


def track(path, title=None):
    """(path, artist, title) as the catalog has it: the artist is the folder, the title the tag or the file's name."""
    artist = path.split("/", 1)[0]
    return path, artist, title if title is not None else path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def nfd(text):
    return unicodedata.normalize("NFD", text)


# ── rule 1: the entry's path is a catalog path ──────────────────────────────────────────────────────────────────────

def test_exact_path_gets_its_line():
    by, stats = match({Z: "betrayal"}, {}, [track(Z), track("Other/Album/Song.mp3")])
    assert by == {Z: "betrayal"} and stats["exact"] == 1 and stats["unmatched"] == 0


def test_an_entry_whose_file_isnt_in_the_library_is_dropped():
    by, stats = match({"Gone/Album (1999)/Never Here.mp3": "x"}, {}, [track(Z)])
    assert by == {} and stats["unmatched"] == 1


# ── rule 2: Unicode form and case ───────────────────────────────────────────────────────────────────────────────────

def test_unicode_form_is_ignored_both_ways():
    path = "Abwärts/Amok Koma (1980)/04 - Abwarts-Shanghai Stinker.mp3"
    assert match({nfd(path): "a"}, {}, [track(path)])[0] == {path: "a"}          # the Mac's NFD key, the library's NFC name
    assert match({path: "b"}, {}, [track(nfd(path))])[0] == {nfd(path): "b"}     # ...and the other way (the catalog's spelling is returned)


def test_case_is_ignored_when_the_library_has_one_such_file():
    path = "Sex Pistols/God Save The Queen (1977)/God Save the Queen.mp3"
    by, stats = match({"Sex Pistols/God Save the Queen (1977)/God Save the Queen.mp3": "anthem"}, {}, [track(path)])
    assert by == {path: "anthem"} and stats["folded"] == 1


def test_case_is_not_enough_when_the_library_has_two_such_files():
    paths = ["Morrissey/Years (1999)/Cosmic Dancer.mp3", "morrissey/Years (1999)/Cosmic Dancer.mp3"]
    # two files differ only by case: which one the line belongs to can't be told
    assert match({"MORRISSEY/Years (1999)/Cosmic Dancer.mp3": "x"}, {}, [track(p) for p in paths])[0] == {}


def test_the_mac_duplicate_suffix_is_not_a_path_match_but_the_song_covers_it():
    # "Scars and Stripes_3.mp3" is the Mac's duplicate name; the library kept "Scars and Stripes.mp3": by path they differ...
    path = "Angelspit/Krankhaus (2006)/Scars and Stripes.mp3"
    assert match({"Angelspit/Krankhaus (2006)/Scars and Stripes_3.mp3": "scars"}, {}, [track(path)])[0] == {}
    # ...and the per-song store, which has no file names in it, finds it by artist and title
    assert match({}, {"angelspit\tscars and stripes": "scars"}, [track(path)])[0] == {path: "scars"}


def test_a_stricter_entry_keeps_the_file_over_a_looser_one():
    path = "Sex Pistols/God Save The Queen (1977)/God Save the Queen.mp3"
    by, stats = match({"Sex Pistols/God Save the Queen (1977)/God Save the Queen.mp3": "folded",
                       path: "exact"}, {}, [track(path)])
    assert by == {path: "exact"} and stats["exact"] == 1 and stats["folded"] == 0 and stats["superseded"] == 1
    # whichever comes first in the index
    assert match({path: "exact", "Sex Pistols/God Save the Queen (1977)/God Save the Queen.mp3": "folded"}, {},
                 [track(path)])[0] == {path: "exact"}


def test_of_two_entries_of_one_tier_the_first_in_the_index_wins():
    path = "Sex Pistols/God Save The Queen (1977)/God Save the Queen.mp3"
    assert match({"sex pistols/god save the queen (1977)/god save the queen.mp3": "first",
                  "SEX PISTOLS/GOD SAVE THE QUEEN (1977)/GOD SAVE THE QUEEN.MP3": "second"}, {}, [track(path)])[0] == {path: "first"}


# ── rule 3: the song ────────────────────────────────────────────────────────────────────────────────────────────────

SONGS = {"bauhaus\tthe spy in the cab": "A spy.", "the police\twalking on the moon": "A giant step.",
         "clan of xymox\t7th time": "Seventh.", "alien sex fiend\ti walk the line": "Walking.",
         "faderhead\tintro": "NOT A SONG", "faderhead\ttrack 13": "NOT A SONG", "abwärts\tärger": "Rage."}


@pytest.mark.parametrize("path,title,line", [
    # the same song on another album or a compilation, with a different file name
    ("Bauhaus/In the Flat Field (2009)/The Spy in the Cab.mp3", None, "A spy."),
    ("The Police/Mastermix Classic Cuts (2019)/Walking on the Moon.mp3", None, "A giant step."),
    # the title is the tag's, not the file's: a numbered or odd file name doesn't matter
    ("Clan of Xymox/Subsequent Pleasures (1994)/05_x.mp3", "7th Time", "Seventh."),
    # the Mac's base_title: a trailing "(...)" or "[...]" version note isn't part of the song
    ("Alien Sex Fiend/Drive My Rocket (1994)/I Walk the Line (Radio Edit).mp3", None, "Walking."),
    ("Alien Sex Fiend/Drive My Rocket (1994)/I Walk the Line [Remastered 2009].mp3", None, "Walking."),
    # case, spacing and Unicode form are ignored on both sides
    ("BAUHAUS/Album/The  Spy  in the Cab.mp3", None, "A spy."),
    (nfd("Abwärts/Amok Koma (1980)/Ärger.mp3"), None, "Rage."),
])
def test_a_song_is_the_same_wherever_it_lives(path, title, line):
    by, stats = match({}, SONGS, [track(path, title)])
    assert by == {path: line} and stats["song"] == 1


def test_a_song_never_crosses_artists():
    assert match({}, SONGS, [track("Bauhaus/Album/Walking on the Moon.mp3")])[0] == {}
    assert match({}, SONGS, [track("Somebody Else/Album/The Spy in the Cab.mp3")])[0] == {}


@pytest.mark.parametrize("title", ["Intro", "Track 13", "TRACK 13", "Instrumental", "Untitled", "A", "", "Interlude (Part 2)"])
def test_a_generic_title_is_not_a_song(title):
    songs = {"faderhead\t" + t: "NOT A SONG" for t in ("intro", "track 13", "instrumental", "untitled", "a", "", "interlude")}
    assert match({}, songs, [track("Faderhead/Asteria (2019)/x.mp3", title)])[0] == {}


def test_a_track_without_an_artist_is_not_guessed():
    assert song_key("", "The Spy in the Cab") is None and match({}, SONGS, [("loose.mp3", "", "The Spy in the Cab")])[0] == {}


def test_many_files_of_one_song_all_get_its_line():
    paths = ["Bauhaus/In the Flat Field (2009)/The Spy in the Cab.mp3", "Bauhaus/Singles (1982)/The Spy in the Cab_2.m4a",
             "Bauhaus/Live (1999)/The Spy in the Cab (live).mp3"]
    assert match({}, SONGS, [track(p, "The Spy in the Cab" + (" (live)" if "live" in p.lower() else "")) for p in paths])[0] \
        == {p: "A spy." for p in sorted(paths)}


def test_a_file_entry_wins_over_the_song_store():
    path = "Bauhaus/In the Flat Field (2009)/The Spy in the Cab.mp3"
    by, stats = match({path: "the file's own line"}, SONGS, [track(path)])
    assert by == {path: "the file's own line"} and stats["exact"] == 1 and stats["song"] == 0


def test_the_song_store_can_be_turned_off():
    path = "Bauhaus/In the Flat Field (2009)/The Spy in the Cab.mp3"
    assert match({}, SONGS, [track(path)], with_songs=False)[0] == {}
    assert match({path: "x"}, SONGS, [track(path)], with_songs=False)[0] == {path: "x"}


def test_the_title_is_cut_the_way_the_mac_cuts_it():
    # stage9_lyrics.base_title: a trailing (...) or [...] of up to 40 characters goes, everything from the first one that fits
    assert song_key("Band", "Song (A) (B)") == ("band", "song")
    assert song_key("Band", "Song [Remastered 2009]") == ("band", "song")
    assert song_key("Band", "(Don't Fear) The Reaper") == ("band", "(don't fear) the reaper")        # not trailing: part of the title
    long_note = "x" * 41
    assert song_key("Band", f"Song ({long_note})") == ("band", f"song ({long_note})")                # longer than the Mac cuts
    assert match({}, {"band\tsong": "kept"}, [track("Band/Album/x.mp3", "Song (A) (B)")])[0] == {"Band/Album/x.mp3": "kept"}


def test_the_result_is_sorted_so_its_etag_is_stable():
    paths = [f"A/B/{c}.mp3" for c in "zyx"]
    assert list(match({p: "x" for p in paths}, {}, [track(p) for p in paths])[0]) == sorted(paths)


# ── what flask-api's answer is allowed to be ────────────────────────────────────────────────────────────────────────

def test_clean_keeps_only_well_formed_lines():
    got = clean({"v": 1, "by": {"A/B/C.mp3": "  A   song\nabout   things ", "A/B/D.mp3": "", "A/B/E.mp3": 7, "": "x",
                                "A/B/F.mp3": "x" * 401, 5: "x"}, "songs": {"a\tb": " fine ", "c\td": None, "e": ""}})
    assert got == ({"A/B/C.mp3": "A song about things"}, {"a\tb": "fine"})
    assert clean({"v": 1, "by": {}}) == ({}, {})                      # an older flask-api: no songs
    for bad in (None, [], {}, {"v": 2, "by": {}}, {"v": 1}, {"v": 1, "by": []}):
        assert clean(bad) is None
    assert clean({"v": 1, "by": {}, "songs": "nonsense"}) == ({}, {})


# ── the store: fetch, keep, serve ───────────────────────────────────────────────────────────────────────────────────

TRACKS = (track(Z), track("Angelspit/Krankhaus (2006)/Scars and Stripes.mp3"), track("New Artist/New Album (2020)/Fresh.mp3"))
PATHS = tuple(t[0] for t in TRACKS)


def payload(by=None, songs=None):
    return json.dumps({"v": 1, "by": {Z: "betrayal"} if by is None else by,
                       "songs": {"angelspit\tscars and stripes": "scars"} if songs is None else songs}).encode()


class Fake:
    """A stand-in for lyrics._http_fetch that answers from a script and remembers what it was asked."""
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, url, etag):
        self.asked.append((url, etag))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def served(store):
    return json.loads(gzip.decompress(store.served[0]))


def test_nothing_fetched_serves_an_empty_set(tmp_path):
    store = LyricsStore(tmp_path, url="http://flask-api:5000/x", fetch=Fake(OSError("down")))
    assert served(store) == {"v": 1, "by": {}} and store.count == 0 and store.served[1]


def test_fetch_matches_serves_and_keeps_a_copy(tmp_path):
    fake = Fake((200, '"v1"', payload()))
    store = LyricsStore(tmp_path, url="http://flask-api:5000/x", fetch=fake, clock=lambda: NOW)
    store.rebuild(TRACKS)
    assert served(store)["by"] == {}                                    # catalog known, nothing fetched yet
    assert store.refresh() is True
    assert served(store) == {"v": 1, "by": {Z: "betrayal", PATHS[1]: "scars"}}      # one by path, one by song
    assert store.stats["exact"] == 1 and store.stats["song"] == 1
    saved = json.loads((tmp_path / "lyrics.json").read_text(encoding="utf-8"))
    assert saved["etag"] == '"v1"' and saved["fetched_at"] == NOW and saved["by"] == {Z: "betrayal"} and len(saved["songs"]) == 1


def test_a_restart_with_flask_api_down_still_serves_the_copy(tmp_path):
    first = LyricsStore(tmp_path, url="http://flask-api:5000/x", fetch=Fake((200, '"v1"', payload())))
    first.refresh()
    again = LyricsStore(tmp_path, url="http://flask-api:5000/x", fetch=Fake(ConnectionRefusedError("down")))
    again.rebuild(TRACKS)                                               # the library's catalog from ITS cache
    assert served(again)["by"] == {Z: "betrayal", PATHS[1]: "scars"}
    assert again.refresh() is False and served(again)["by"][Z] == "betrayal"


def test_if_none_match_goes_out_and_a_304_changes_nothing(tmp_path):
    fake = Fake((200, '"v1"', payload()), (304, '"v1"', b""))
    store = LyricsStore(tmp_path, url="http://flask-api:5000/x", fetch=fake)
    store.rebuild(TRACKS)
    store.refresh()
    before = store.served
    assert store.refresh() is True
    assert fake.asked == [("http://flask-api:5000/x", ""), ("http://flask-api:5000/x", '"v1"')] and store.served == before


@pytest.mark.parametrize("answer", [OSError("connection refused"), TimeoutError("slow"), (200, "", b"<html>nginx</html>"),
                                    (200, "", b'{"v": 2, "by": {}}'), (200, "", b'{"v": 1, "by": {}}'),
                                    (200, "", b'{"v": 1, "by": {}, "songs": {}}'),
                                    (503, "", b'{"error": "lyrics_unavailable"}')])
def test_a_bad_answer_keeps_the_lines_we_have(tmp_path, answer):
    store = LyricsStore(tmp_path, url="http://flask-api:5000/x", fetch=Fake((200, '"v1"', payload()), answer))
    store.rebuild(TRACKS)
    store.refresh()
    before = store.served
    assert store.refresh() is False
    assert store.served == before and store.count == 2


def test_an_older_flask_api_with_only_the_per_file_lines_still_works(tmp_path):
    store = LyricsStore(tmp_path, url="http://x/", fetch=Fake((200, '"v1"', json.dumps({"v": 1, "by": {Z: "betrayal"}}).encode())))
    store.rebuild(TRACKS)
    assert store.refresh() is True and served(store)["by"] == {Z: "betrayal"}


def test_a_corrupt_copy_is_ignored(tmp_path):
    (tmp_path / "lyrics.json").write_text("{not json", encoding="utf-8")
    assert LyricsStore(tmp_path, url="http://x/", fetch=Fake(OSError())).count == 0
    (tmp_path / "lyrics.json").write_text(json.dumps(["a", "list"]), encoding="utf-8")
    assert LyricsStore(tmp_path, url="http://x/", fetch=Fake(OSError())).count == 0


def test_a_new_catalog_places_lines_again(tmp_path):
    store = LyricsStore(tmp_path, url="http://x/", fetch=Fake((200, '"v1"', payload({Z: "betrayal", PATHS[2]: "fresh"}, {}))))
    store.rebuild(TRACKS[:2])
    store.refresh()
    assert PATHS[2] not in served(store)["by"]                          # not in the library yet
    store.rebuild(TRACKS)                                               # a scan found it
    assert served(store)["by"][PATHS[2]] == "fresh"


def test_the_etag_changes_only_when_the_lines_do(tmp_path):
    store = LyricsStore(tmp_path, url="http://x/", fetch=Fake((200, '"v1"', payload())))
    store.rebuild(TRACKS)
    store.refresh()
    first = store.served[1]
    store.rebuild(TRACKS)
    assert store.served[1] == first
    store.rebuild(TRACKS[:1])
    assert store.served[1] != first


def test_a_url_that_isnt_http_is_refused(tmp_path):
    store = LyricsStore(tmp_path, url="file:///etc/passwd")                 # the real fetch, which checks the scheme
    assert store.refresh() is False and store.count == 0


def test_no_url_means_no_fetching(tmp_path):
    store = LyricsStore(tmp_path, url="", fetch=Fake(AssertionError("must not be called")))
    assert store.refresh() is False


class _Stop(Exception):
    pass


def _run_loop(store, monkeypatch, every_s, rounds):
    """Run store.run_forever with sleep recorded instead of slept; stop after `rounds` sleeps."""
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == rounds:
            raise _Stop
    monkeypatch.setattr(lyrics.time, "sleep", sleep)
    with pytest.raises(_Stop):
        store.run_forever(every_s=every_s)
    return sleeps


def test_the_refresh_loop_backs_off_while_flask_api_is_down(tmp_path, monkeypatch):
    store = LyricsStore(tmp_path, url="http://x/", fetch=Fake(OSError("down")))
    # soon at first (flask-api may only be starting alongside), then every 10 minutes
    assert _run_loop(store, monkeypatch, 3 * 3600, 8) == [30, 60, 120, 240, 480, 600, 600, 600]


def test_the_refresh_loop_settles_to_the_long_interval_once_it_works(tmp_path, monkeypatch):
    store = LyricsStore(tmp_path, url="http://x/", fetch=Fake(OSError("down"), (200, '"v"', payload())))
    assert _run_loop(store, monkeypatch, 3 * 3600, 3) == [30, 3 * 3600, 3 * 3600]


# ── the real HTTP fetch, against a real server ──────────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    body = payload()
    etag = '"served-1"'
    seen = []

    def do_GET(self):  # noqa: N802
        _Handler.seen.append(self.headers.get("If-None-Match"))
        if self.path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * (lyrics.MAX_BODY + 10))
        elif self.headers.get("If-None-Match") == self.etag:
            self.send_response(304)
            self.send_header("ETag", self.etag)
            self.end_headers()
        elif self.path == "/gone":
            self.send_response(503)
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("ETag", self.etag)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    _Handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_the_real_fetch_handles_200_304_and_errors(tmp_path, server):
    store = LyricsStore(tmp_path, url=server + "/lyric-summaries")
    store.rebuild(TRACKS)
    assert store.refresh() is True and store.count == 2 and store.source_etag == '"served-1"'
    assert store.refresh() is True and _Handler.seen == [None, '"served-1"']             # the second was a 304
    assert LyricsStore(tmp_path / "b", url=server + "/gone").refresh() is False         # a 503
    assert LyricsStore(tmp_path / "c", url=server + "/big").refresh() is False          # more than any real answer
    assert LyricsStore(tmp_path / "d", url="http://127.0.0.1:9/never").refresh() is False   # nothing listening


# ── the route ───────────────────────────────────────────────────────────────────────────────────────────────────────

def fake_tags(path):
    return {"t": path.stem, "a": "x", "al": "y", "n": 1, "d": 1, "g": "", "s": 100, "art": False, "y": 0}


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "music"
    for rel in PATHS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")
    lib = Library(root, tmp_path / "state", reader=fake_tags, clock=lambda: NOW)
    lib.scan()
    return lib


def test_the_library_hands_over_path_artist_and_title(library):
    assert set(library.identities) == {(Z, "Delerium feat. Phildel", "Zero"),
                                       (PATHS[1], "Angelspit", "Scars and Stripes"), (PATHS[2], "New Artist", "Fresh")}


def test_route_serves_gzipped_lines_with_an_etag(library, tmp_path):
    store = LyricsStore(tmp_path / "ls", url="http://x/", fetch=Fake((200, '"v1"', payload())))
    library.on_build.append(store.rebuild)
    store.rebuild(library.identities)
    store.refresh()
    client = gate.create_app(library, KEY, clock=lambda: NOW, lyrics=store).test_client()
    tok = gate.sign(KEY, NOW + 3600)
    r = client.get(f"/t/{tok}/lyrics.json")
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip" and r.mimetype == "application/json"
    assert r.headers["Cache-Control"] == "private, no-cache" and r.headers["Access-Control-Allow-Origin"] == "*"
    assert json.loads(gzip.decompress(r.data)) == {"v": 1, "by": {Z: "betrayal", PATHS[1]: "scars"}}
    again = client.get(f"/t/{tok}/lyrics.json", headers={"If-None-Match": r.headers["ETag"]})
    assert again.status_code == 304 and again.headers["ETag"] == r.headers["ETag"] and not again.data
    assert client.get("/health").get_json()["lyrics"] == 2
    # the key is the catalog's own path: every line belongs to a track the player has
    catalog_paths = {t[0] for t in json.loads(gzip.decompress(library.catalog_gz))["tracks"]}
    assert set(json.loads(gzip.decompress(r.data))["by"]) <= catalog_paths


def test_route_without_a_store_or_with_flask_api_down_is_an_empty_set_not_an_error(library, tmp_path):
    tok = gate.sign(KEY, NOW + 3600)
    for kwargs in ({}, {"lyrics": LyricsStore(tmp_path / "ls", url="http://x/", fetch=Fake(OSError("down")))}):
        client = gate.create_app(library, KEY, clock=lambda: NOW, **kwargs).test_client()
        r = client.get(f"/t/{tok}/lyrics.json")
        assert r.status_code == 200 and json.loads(gzip.decompress(r.data)) == {"v": 1, "by": {}}
        assert client.get(f"/t/{tok}/catalog.json").status_code == 200                  # playback never depends on it


def test_a_scan_that_adds_a_track_gives_it_its_line(library, tmp_path):
    store = LyricsStore(tmp_path / "ls", url="http://x/", fetch=Fake((200, '"v1"', payload({"Brand New/Album (2026)/Song.mp3": "new"}, {}))))
    library.on_build.append(store.rebuild)
    store.rebuild(library.identities)
    store.refresh()
    assert store.count == 0
    (library.root / "Brand New" / "Album (2026)").mkdir(parents=True)
    (library.root / "Brand New" / "Album (2026)" / "Song.mp3").write_bytes(b"x")
    library.scan()
    assert served(store)["by"] == {"Brand New/Album (2026)/Song.mp3": "new"}


def test_a_broken_listener_cannot_cost_the_catalog(library):
    def boom(identities):
        raise RuntimeError("nope")
    library.on_build.append(boom)
    library.scan()
    assert library.count == 3 and library.catalog_gz
