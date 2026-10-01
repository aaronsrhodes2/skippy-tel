"""Subtitles: text tracks as WebVTT through Jellyfin's converter, cached briefly (a viewer flips between tracks), never for an index
the catalog doesn't list."""
import fake_jellyfin as fj
import gate

BRAVO, ALPHA = fj.fid(2), fj.fid(1)            # Bravo has an srt (index 3) and a picture subtitle (index 4); Alpha has none


def asked(stack):
    return stack.fake.calls(f"/Videos/{BRAVO}/{BRAVO}/Subtitles/")


def test_a_text_subtitle_is_webvtt_with_the_right_headers(stack):
    r = stack.get(f"/sub/{BRAVO}/3.vtt")
    assert r.status_code == 200 and r.content_type == "text/vtt; charset=utf-8" and r.data.startswith(b"WEBVTT")
    assert r.headers["Cache-Control"] == "private, max-age=3600" and r.headers["Access-Control-Allow-Origin"] == "*"
    assert asked(stack)[-1]["path"].endswith("/Subtitles/3/Stream.vtt") and asked(stack)[-1]["authed"]


def test_flipping_between_tracks_asks_jellyfin_once(stack):
    for _ in range(5):
        assert stack.get(f"/sub/{BRAVO}/3.vtt").status_code == 200
    assert len(asked(stack)) == 1


def test_the_cache_forgets_after_ten_minutes(stack):
    stack.get(f"/sub/{BRAVO}/3.vtt")
    stack.clock.advance(601)
    stack.get(f"/sub/{BRAVO}/3.vtt")
    assert len(asked(stack)) == 2


def test_a_picture_subtitle_or_a_missing_one_is_a_404_decided_from_the_catalog(stack):
    for name in ("4.vtt", "5.vtt", "0.vtt", "99.vtt"):                      # 4: PGS (can't become text), the rest: not subtitle streams
        r = stack.get(f"/sub/{BRAVO}/{name}")
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}
    assert stack.get(f"/sub/{ALPHA}/3.vtt").status_code == 404                # a film with no subtitles at all
    assert asked(stack) == []


def test_a_subtitle_that_is_not_webvtt_is_a_502_and_not_kept(stack):
    stack.fake.vtt = "<html>this is not a subtitle</html>"
    r = stack.get(f"/sub/{BRAVO}/3.vtt")
    assert r.status_code == 502 and r.get_json() == {"error": "Jellyfin sent something unusable"}
    stack.fake.vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nfixed\n"
    assert stack.get(f"/sub/{BRAVO}/3.vtt").data.endswith(b"fixed\n")


def test_a_byte_order_mark_before_webvtt_is_accepted(stack):
    stack.fake.vtt = "﻿WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n"
    assert stack.get(f"/sub/{BRAVO}/3.vtt").status_code == 200


def test_jellyfin_down_is_a_503_and_a_cached_subtitle_still_plays(fake, make_stack):
    s = make_stack(fake)
    s.get(f"/sub/{BRAVO}/3.vtt")
    fake.go_down()
    assert s.get(f"/sub/{BRAVO}/3.vtt").status_code == 200
    fake.item(2)["MediaStreams"].append(fj.sub_stream(7, "ass", "spa"))
    fake.come_back()
    assert s.library.refresh()
    fake.go_down()
    r = s.get(f"/sub/{BRAVO}/7.vtt")
    assert r.status_code == 503 and r.get_json() == {"error": "Jellyfin is not answering"}


def test_a_huge_subtitle_is_refused_not_held_in_memory(stack, monkeypatch):
    stack.fake.vtt = "WEBVTT\n\n" + "x" * (3 * 1024 * 1024)
    r = stack.get(f"/sub/{BRAVO}/3.vtt")
    assert r.status_code == 502


def test_the_subtitle_cache_is_bounded_in_bytes_and_time():
    t = [0.0]
    cache = gate.SubCache(lambda: t[0], ttl=100, max_bytes=1000)
    for n in range(10):
        cache.put(("a", n), b"x" * 300)
    assert sum(len(v[1]) for v in cache._d.values()) <= 1000 and cache.get(("a", 9)) and cache.get(("a", 0)) is None
    t[0] = 101
    assert cache.get(("a", 9)) is None                                       # expired
    cache.put(("big", 1), b"x" * 5000)                                       # bigger than the whole cache: not kept, nothing else lost for it
    assert cache.get(("big", 1)) is None
