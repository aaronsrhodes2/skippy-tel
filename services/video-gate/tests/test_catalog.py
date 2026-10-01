"""The catalog: its shape, the `direct` rule on real-looking MediaStreams, what happens when Jellyfin is down or sends nonsense."""
import calendar
import gzip
import json
import time

import pytest

import fake_jellyfin as fj
from catalog import Library, build_item, play_info
from helpers import by_title
from jellyfin import Client

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"        # ffprobe's name for the MP4 family: what Jellyfin reports for a .mp4/.mov/.m4v


def v(*args, **kw):
    return fj.video_stream(*args, **kw)


def a(*args, **kw):
    return fj.audio_stream(*args, **kw)


# (what it is, Jellyfin's Container, MediaStreams, direct?)   Shapes copied from the Captain's server (13 Ghosts, Tetsuo II...).
DIRECT_TABLE = [
    ("h264 High + aac LC in mp4", MP4, [v(), a()], True),
    ("h264 Main + aac 5.1", MP4, [v(profile="Main"), a(channels=6)], True),
    ("h264 + mp3 stereo", MP4, [v(), a(codec="mp3", profile="")], True),
    ("h264 and no audio at all", MP4, [v()], True),
    ("container given as plain mp4", "mp4", [v(), a()], True),
    ("container given as MOV", "MOV", [v(), a()], True),
    ("m4v", "m4v", [v(), a()], True),
    ("HE-AAC", MP4, [v(), a(profile="HE-AAC")], True),
    ("cover art (mjpeg) listed before the real video", MP4, [v("mjpeg", "Baseline", 8, "yuvj420p", 600, 0), v(index=1), a(index=2)], True),
    ("first audio is aac, a second one is ac3 (a browser plays the first)", MP4, [v(), a(), a(codec="ac3", index=2)], True),
    ("mkv", "mkv", [v(), a()], False),
    ("matroska,webm", "matroska,webm", [v(), a()], False),
    ("avi / mpeg4", "avi", [v("mpeg4", "Advanced Simple Profile", None, "yuv420p"), a(codec="mp3")], False),
    ("hevc", MP4, [v("hevc", "Main", 8, "yuv420p", 1080), a()], False),
    ("vp9 in mp4", MP4, [v("vp9"), a()], False),
    ("av1 in mp4", MP4, [v("av1"), a()], False),
    ("ac3 audio", MP4, [v(), a(codec="ac3", profile="")], False),
    ("dts audio", MP4, [v(), a(codec="dts", profile="DTS")], False),
    ("mp2 audio (22 of the Captain's mp4s)", MP4, [v(), a(codec="mp2", profile="")], False),
    ("first audio is ac3, a later one is aac", MP4, [v(), a(codec="ac3", profile=""), a(index=2)], False),
    ("flac audio", MP4, [v(), a(codec="flac", profile="")], False),
    ("aac Main profile (not LC)", MP4, [v(), a(profile="Main")], False),
    ("multichannel mp3", MP4, [v(), a(codec="mp3", channels=6, profile="")], False),
    ("10-bit h264 (BitDepth 10)", MP4, [v(depth=10, pix="yuv420p10le", profile="High 10"), a()], False),
    ("10-bit by pixel format only", MP4, [v(depth=None, pix="yuv420p10le"), a()], False),
    ("High 10 profile", MP4, [v(profile="High 10"), a()], False),
    ("4:2:2", MP4, [v(pix="yuv422p", profile="High 4:2:2"), a()], False),
    ("4:4:4", MP4, [v(pix="yuv444p", profile="High 4:4:4 Predictive"), a()], False),
    ("level 6.2", MP4, [v(level=62), a()], False),
    ("8k", MP4, [v(height=4320), a()], False),
    ("no height", MP4, [dict(v(), Height=None), a()], False),
    ("not probed yet (Jellyfin has no streams: 7 of the Captain's)", None, [], False),
    ("audio only", "mp3", [a(codec="mp3")], False),
    ("only cover art, no video", MP4, [v("mjpeg", "Baseline", 8, "yuvj420p", 600, 0), a(index=1)], False),
]


@pytest.mark.parametrize("what,container,streams,direct", DIRECT_TABLE, ids=[t[0] for t in DIRECT_TABLE])
def test_direct_rule(what, container, streams, direct):
    assert play_info({"Container": container, "MediaStreams": streams})["direct"] is direct


def test_play_info_fields():
    p = play_info({"Container": MP4, "MediaStreams": [v(height=720), a(lang="eng", channels=6), a(codec="ac3", index=2, lang="fra"),
                                                       fj.sub_stream(3), fj.sub_stream(4, "pgssub", "deu", text=False, external=True)]})
    assert (p["container"], p["vcodec"], p["acodec"], p["height"]) == ("mp4", "h264", "aac", 720)
    assert p["audio"] == [{"i": 1, "lang": "eng", "codec": "aac", "ch": 6}, {"i": 2, "lang": "fra", "codec": "ac3", "ch": 2}]
    assert p["subs"] == [{"i": 3, "lang": "eng", "codec": "subrip", "external": False, "vtt": True},
                         {"i": 4, "lang": "deu", "codec": "pgssub", "external": True, "vtt": False}]


def test_catalog_shape(stack):
    r = stack.get("/catalog.json")
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip" and r.mimetype == "application/json"
    assert r.headers["Cache-Control"] == "private, no-cache"
    data = json.loads(gzip.decompress(r.data))
    assert set(data) == {"v", "items", "collections"} and data["v"] == 1
    items = {i["title"]: i for i in data["items"]}
    assert len(items) == 11
    alpha = items["Alpha"]
    assert alpha == {
        "id": fj.fid(1), "type": "movie", "title": "Alpha", "sort": "alpha", "year": 1994, "runtime": 5400, "overview": "A film.",
        "genres": ["Horror", "Thriller"], "rating": 7.5, "official": "PG-13", "added": calendar.timegm((2023, 9, 11, 9, 29, 4)),
        "collectionIds": [fj.fid(20)], "hasPoster": True, "hasBackdrop": True, "resume": 1200, "played": False,
        "play": {"container": "mp4", "vcodec": "h264", "acodec": "aac", "height": 480, "direct": True,
                 "audio": [{"i": 1, "lang": "eng", "codec": "aac", "ch": 2}], "subs": []}}
    bravo = items["Bravo"]["play"]
    assert (bravo["direct"], bravo["container"], [s["i"] for s in bravo["subs"]], [x["i"] for x in bravo["audio"]]) == (False, "mkv", [3, 4], [1, 2])
    assert [s["vtt"] for s in bravo["subs"]] == [True, False]            # the PGS (picture) subtitle can't become WebVTT
    assert items["Hotel"]["played"] is True and items["Hotel"]["play"]["direct"] is True and items["Hotel"]["play"]["audio"] == []
    assert items["Golf"]["hasPoster"] is False and items["Golf"]["hasBackdrop"] is False
    assert items["Foxtrot"]["play"]["direct"] is False and items["Foxtrot"]["runtime"] == 0     # not probed: still listed
    assert items["Juliet Show"]["type"] == "series" and items["Juliet Show"]["play"] is None   # a shelf, not a file
    ep = items["Juliet S01E02"]
    assert (ep["type"], ep["seriesId"], ep["season"], ep["ep"], ep["runtime"]) == ("episode", fj.fid(10), 1, 2, 2700)
    assert [i["sort"] for i in data["items"]] == sorted(i["sort"] for i in data["items"])        # stable order for the player
    colls = {c["title"]: c for c in data["collections"]}
    assert colls["The Alpha Collection"]["itemIds"] == [fj.fid(1), fj.fid(2)]    # by year; the id Jellyfin lists that isn't ours is dropped
    assert colls["The Alpha Collection"]["id"] == fj.fid(20) and colls["The Alpha Collection"]["hasPoster"] is True
    assert colls["Empty Box"]["itemIds"] == [] and colls["Empty Box"]["hasPoster"] is False
    assert items["Bravo"]["collectionIds"] == [fj.fid(20)] and items["Charlie"]["collectionIds"] == []


def test_catalog_has_no_paths_or_secrets(stack):
    text = gzip.decompress(stack.get("/catalog.json").data).decode()
    for needle in (fj.KEY, "D:\\", "Path", "ServerId", fj.SERVER_ID, "api_key", "ApiKey", "127.0.0.1", "Token"):
        assert needle not in text, needle


def test_catalog_etag_and_304(stack):
    r = stack.get("/catalog.json")
    etag = r.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')
    again = stack.get("/catalog.json", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.data == b"" and again.headers["ETag"] == etag
    assert stack.get("/catalog.json", headers={"If-None-Match": 'W/' + etag}).status_code == 304
    assert stack.get("/catalog.json", headers={"If-None-Match": '"other"'}).status_code == 200
    assert stack.get("/catalog.json").headers["ETag"] == etag               # unchanged data, unchanged tag


def test_catalog_is_served_from_memory(stack):
    n = len(stack.fake.requests)
    for _ in range(5):
        assert stack.get("/catalog.json").status_code == 200
    assert len(stack.fake.requests) == n                                     # (a refresh is poked in the background at most once a minute)


def test_catalog_text_is_cleaned_and_bounded(make_stack):
    fake = fj.FakeJellyfin(items=[
        fj.movie(1, "Long\x00 Title\x07", overview="x" * 1000 + "\x01"),
        fj.movie(2, "", overview=None, genres=("A" * 100, "B")),
        {**fj.movie(3, "Dashed"), "Id": "00000000-0000-0000-0000-000000000003"},
        {**fj.movie(4, "BadId"), "Id": "../etc/passwd"},
        {**fj.movie(5, "Weird Type"), "Type": "Trailer"},
        fj.movie(6, "Ratings", rating="not a number", official="X" * 50, created="yesterday"),
    ], boxes=[], children={})
    fake.url = fake.start()
    try:
        items = by_title(make_stack(fake))
    finally:
        fake.stop()
    assert set(items) == {"Long Title", "Untitled", "Dashed", "Ratings"}      # no bad ids, no foreign types
    assert len(items["Long Title"]["overview"]) == 400 and items["Long Title"]["overview"].endswith("\u2026")
    assert items["Untitled"]["overview"] == "" and items["Untitled"]["genres"][0].endswith("\u2026") and len(items["Untitled"]["genres"][0]) == 40
    assert items["Dashed"]["id"] == fj.fid(3)                                  # dashes normalised: still 32 hex
    assert items["Ratings"]["rating"] is None and len(items["Ratings"]["official"]) == 12 and items["Ratings"]["added"] == 0


def test_user_resolution(fake, make_stack):
    assert make_stack(fake).library.user_id == fj.USER_ID                      # the first administrator, not 'viewer'
    assert make_stack(fake, user="viewer").library.user_id == fj.OTHER_ID      # by name, any case
    assert make_stack(fake, user=fj.USER_ID).library.user_id == fj.USER_ID     # by id
    s = make_stack(fake, user="nobody", refresh=False)
    assert s.library.refresh() is False and s.library.error == "bad"          # a user that doesn't exist is an error, not a guess
    assert all(r["q"].get("userId") in (None, fj.USER_ID, fj.OTHER_ID) for r in fake.calls("/Items"))


def test_refresh_asks_only_the_expected_endpoints_with_the_key_in_a_header(stack):
    assert stack.fake.requests, "nothing was requested?"
    for r in stack.fake.requests:
        assert r["authed"], r["path"]                                          # the key travels in Authorization...
        assert fj.KEY not in json.dumps(r["query"]) and "api_key" not in json.dumps(r["query"]).lower()    # ...never in a URL
        assert not r.get("unexpected"), r["path"]
    assert {r["path"] for r in stack.fake.requests} == {"/Users", "/Items"}
    assert all(r["headers"]["accept-encoding"] == "identity" for r in stack.fake.requests)


def test_refresh_pages_through_a_big_library(make_stack):
    fake = fj.FakeJellyfin(items=[fj.movie(n, f"Film {n:04d}") for n in range(1, 1203)], boxes=[], children={})
    fake.url = fake.start()
    try:
        s = make_stack(fake)
        pages = [r for r in fake.calls("/Items") if "BoxSet" not in r["q"].get("IncludeItemTypes", "")]
    finally:
        fake.stop()
    assert s.library.count == 1202 and [p["q"]["StartIndex"] for p in pages] == ["0", "500", "1000"]


def test_jellyfin_down_serves_the_last_good_catalog(fake, make_stack, clock):
    s = make_stack(fake)
    before = s.get("/catalog.json")
    assert s.web.get("/health").get_json()["ready"] is True
    fake.go_down()                                                             # Jellyfin goes away
    assert s.library.refresh() is False and s.library.error == "down"
    after = s.get("/catalog.json")
    assert after.status_code == 200 and after.data == before.data              # the player is never left without a catalog
    clock.advance(10)                                                          # (/health caches Jellyfin's state for a few seconds)
    health = s.web.get("/health")
    assert health.status_code == 200                                           # alive...
    assert health.get_json() == {"ok": True, "ready": False, "jellyfin": False, "items": 11}     # ...but not ready
    # and what needs Jellyfin says so plainly, never a 500 and never any upstream text
    r = s.get(f"/img/{fj.fid(1)}/poster")
    assert r.status_code == 503 and r.get_json() == {"error": "Jellyfin is not answering"}
    r = s.get(f"/v/{fj.fid(1)}/direct")
    assert r.status_code == 503 and r.get_json() == {"error": "Jellyfin is not answering"}


def test_health_asks_jellyfin_at_most_every_five_seconds_however_often_it_is_hit(stack, clock):
    """/health needs no pass, so anyone can hammer it: it must not turn into a stream of requests to Jellyfin."""
    for _ in range(30):
        assert stack.web.get("/health").status_code == 200
    assert len(stack.fake.calls("/System/Info/Public")) == 1
    clock.advance(4.9)
    stack.web.get("/health")
    assert len(stack.fake.calls("/System/Info/Public")) == 1
    clock.advance(0.2)
    stack.web.get("/health")
    assert len(stack.fake.calls("/System/Info/Public")) == 2
    assert not stack.fake.calls("/System/Info/Public")[0]["authed"]                  # the probe carries no credentials


def test_jellyfin_comes_back(fake, make_stack, clock):
    s = make_stack(fake)
    fake.go_down()
    assert not s.library.refresh()
    clock.advance(10)
    assert s.web.get("/health").get_json()["ready"] is False
    fake.come_back()
    assert s.library.refresh() and s.library.error == ""                       # the pooled connections died with it: new ones are made
    clock.advance(10)
    assert s.web.get("/health").get_json()["ready"] is True


def test_never_built_catalog_is_a_503_not_a_500(fake, make_stack):
    s = make_stack(fake, refresh=False)
    s.library.tried_at = s.clock()                 # (a download within a minute of an attempt doesn't poke a new one)
    r = s.get("/catalog.json")
    assert r.status_code == 503 and r.get_json()["error"] == "Jellyfin is not answering" and r.get_json()["retry"] is True
    assert s.web.get("/health").get_json() == {"ok": True, "ready": False, "jellyfin": True, "items": 0}


@pytest.mark.parametrize("garbage", [b"not json", b'{"Items": "nope"}', b'["a"]', b'{"Items": [1, "x", null, {"Id": 5}]}', b""])
def test_garbage_from_jellyfin_keeps_the_last_good_catalog(fake, make_stack, garbage, monkeypatch):
    s = make_stack(fake)
    good = s.get("/catalog.json").data
    monkeypatch.setattr(Client, "_json", lambda self, path, params=None: json.loads(garbage) if garbage else (_ for _ in ()).throw(ValueError()))
    assert s.library.refresh() in (True, False)
    if garbage == b'{"Items": [1, "x", null, {"Id": 5}]}':
        assert s.library.count == 0                                           # an empty-but-valid answer replaces; junk rows are skipped
    else:
        assert s.get("/catalog.json").data == good


def test_http_errors_from_jellyfin_map_to_kinds(fake, make_stack):
    s = make_stack(fake)
    fake.items = []
    saved = s.client.__class__._fail
    for status, kind in ((400, "missing"), (404, "missing"), (401, "refused"), (403, "refused"), (500, "down"), (503, "down"), (418, "bad")):
        class R:
            status_code = status
            def close(self): pass
        assert saved(R()).kind == kind


def test_jellyfin_timeouts_are_short_and_reported_as_down(fake, make_stack):
    s = make_stack(fake, client_opts={"read_timeout": 0.3})
    fake.delay["/Items"] = 1.5
    t = time.time()
    assert s.library.refresh() is False and s.library.error == "down"
    assert time.time() - t < 1.2                                               # gave up at the timeout, didn't wait for Jellyfin


def test_apply_progress_changes_the_next_download(stack):
    first = stack.get("/catalog.json")
    stack.library.apply_progress(fj.fid(1), 777, None)
    second = stack.get("/catalog.json")
    assert second.headers["ETag"] != first.headers["ETag"]
    item = json.loads(gzip.decompress(second.data))["items"][0]
    assert item["title"] == "Alpha" and item["resume"] == 777 and item["played"] is False
    stack.library.apply_progress(fj.fid(1), 0, True)
    assert by_title(stack)["Alpha"]["played"] is True
    stack.library.apply_progress("f" * 32, 5, True)                            # an id we don't have: ignored, not a crash


def test_poke_refreshes_in_the_background_at_most_once_a_minute(fake, make_stack, clock, monkeypatch):
    s = make_stack(fake)
    calls = []
    monkeypatch.setattr(s.library, "refresh", lambda: calls.append(1))
    def settle():
        time.sleep(0.4)
    s.library.tried_at = clock()
    for _ in range(3):
        s.get("/catalog.json")
    settle()
    assert calls == []                                                          # asked within a minute of the last refresh: nothing
    clock.advance(61)
    for _ in range(3):
        s.get("/catalog.json")
    settle()
    assert len(calls) == 1                                                      # one refresh for three downloads
    assert build_item({"Id": fj.fid(1), "Type": "Movie"}) is not None


def test_library_class_is_independent_of_flask(fake):
    lib = Library(Client(fake.url, fj.KEY))
    assert lib.ready is False and lib.count == 0 and lib.snapshot() == (b"", "")
