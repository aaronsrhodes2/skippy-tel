"""Transcoded HLS: Jellyfin's playlists are PARSED and WRITTEN again out of validated pieces (never forwarded), so no key and no foreign
host can reach the browser; only files an earlier playlist of the session advertised can be fetched; at most N transcodes run;
and a transcode that nobody watches is stopped in Jellyfin."""
import re
import time

import pytest

import fake_jellyfin as fj
import hls
from hls import PlaylistError, Sessions, rewrite_master, rewrite_media

BRAVO, ALPHA = fj.fid(2), fj.fid(1)                 # Bravo: mkv with an ac3 and an aac track and two subtitles; runtime 6000 s
SID = "ab" * 16
SECRET = "S3CR3TKEYVALUE0123456789abcdef"

# What Jellyfin 10.11.8 really sent for a master playlist (measured), the API key in the image-stream line included.
REAL_MASTER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=256000,AVERAGE-BANDWIDTH=256000,VIDEO-RANGE=SDR,CODECS="avc1.64001F,mp4a.40.2",RESOLUTION=960x720,'
    "FRAME-RATE=23.976\n"
    "main.m3u8?MediaSourceId=abc&PlaySessionId=def&ApiKey=" + SECRET + "\n"
    '#EXT-X-IMAGE-STREAM-INF:BANDWIDTH=7696,RESOLUTION=320x240,CODECS="jpeg",URI="Trickplay/320/tiles.m3u8?ApiKey=' + SECRET + '"\n')

REAL_MEDIA = (
    "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:11\n#EXT-X-MEDIA-SEQUENCE:0\n"
    "#EXTINF:10.000000, nodesc\nhls1/main/0.ts?MediaSourceId=abc&PlaySessionId=def&ApiKey=" + SECRET +
    "&runtimeTicks=0&actualSegmentLengthTicks=100000000\n"
    "#EXTINF:9.5, nodesc\nhls1/main/1.ts?runtimeTicks=100000000&actualSegmentLengthTicks=95000000&PlaySessionId=def\n"
    "#EXT-X-ENDLIST\n")


def session(start=0.0, now=1000.0):
    return hls.Session(SID, BRAVO, "owner", {"PlaySessionId": SID}, start, now)


def entries(sess):
    return {k: (kind, extras) for k, (kind, extras) in sess.table.items()}


# ── rewriting: master ───────────────────────────────────────────────────────────────────────────────────────────────

def test_master_is_rewritten_from_validated_pieces():
    sess = session()
    out = rewrite_master(REAL_MASTER, sess, forbidden=(SECRET,))
    assert out == ("#EXTM3U\n"
                   "#EXT-X-STREAM-INF:BANDWIDTH=256000,AVERAGE-BANDWIDTH=256000,RESOLUTION=960x720,FRAME-RATE=23.976,VIDEO-RANGE=SDR,"
                   'CODECS="avc1.64001F,mp4a.40.2"\n'
                   f"main.m3u8?s={SID}\n")
    assert SECRET not in out and "ApiKey" not in out and "Trickplay" not in out and "http" not in out and "MediaSourceId" not in out
    assert entries(sess) == {"main.m3u8": ("playlist", {})}          # what the browser may ask for next, nothing else


def test_a_start_offset_becomes_ext_x_start():
    assert "#EXT-X-START:TIME-OFFSET=60.500,PRECISE=YES\n" in rewrite_master(REAL_MASTER, session(start=60.5))
    assert "EXT-X-START" not in rewrite_master(REAL_MASTER, session(start=0))


def test_a_variant_without_bandwidth_gets_a_default():
    out = rewrite_master("#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=640x480\nmain.m3u8?x=1\n", session())
    assert "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=640x480\n" in out


@pytest.mark.parametrize("text", [
    "",                                                                      # nothing
    "<html>not a playlist</html>",
    "#EXT-X-STREAM-INF:BANDWIDTH=1\nmain.m3u8\n",                            # no #EXTM3U first line
    "#EXTM3U\n",                                                             # no variant at all
    "#EXTM3U\nmain.m3u8\n",                                                  # a URI without its #EXT-X-STREAM-INF
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n",                              # a variant without its URI
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n#EXT-X-STREAM-INF:BANDWIDTH=2\nmain.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttp://evil.example/main.m3u8\n",   # a foreign host
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttps://evil.example/main.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n//evil.example/main.m3u8\n",      # a scheme-relative one
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n/Videos/x/main.m3u8\n",           # an absolute path on Jellyfin
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n../main.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhls1/../../main.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n%2e%2e/main.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmain.m3u8%0d%0aSet-Cookie:x\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nevil.php\n",                       # not a playlist shape
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmain.m3u8.ts\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\na/b/c/d/e/main.m3u8\n",              # deeper than any real one
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmain.m3u8?" + "&".join(f"k{i}=1" for i in range(100)) + "\n",   # a query bomb
])
def test_a_master_that_is_not_what_jellyfin_sends_is_refused_whole(text):
    with pytest.raises(PlaylistError):
        rewrite_master(text, session(), forbidden=(SECRET,))


def test_hostile_attributes_are_dropped_not_passed_on():
    text = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=<script>,CODECS="a;b<x>",AUDIO="aud",SUBTITLES="s",'
            'URI="http://evil.example/",FRAME-RATE=x,VIDEO-RANGE=EVIL,NAME="Authorization: Bearer x"\nmain.m3u8\n')
    out = rewrite_master(text, session())
    assert out.splitlines()[1] == "#EXT-X-STREAM-INF:BANDWIDTH=1000"
    assert "evil" not in out and "script" not in out and "Authorization" not in out


def test_an_api_key_inside_a_codecs_string_is_caught_by_the_last_line_of_defence():
    # CODECS is shape-checked (letters, digits, dots, commas), and a key is exactly that: only the known-secret check catches it
    text = f'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,CODECS="{SECRET}"\nmain.m3u8\n'
    assert SECRET in rewrite_master(text, session())                         # (without the list of secrets it would go out)
    with pytest.raises(PlaylistError):
        rewrite_master(text, session(), forbidden=(SECRET,))


def test_the_final_check_rejects_keys_urls_and_escapes():
    for line in ("x?api_key=1", "x?ApiKey=1", "x?api-key=1", "http://x", "https://x", "//x", "../x", "a\x00b", "a\rb", "Token=abc"):
        with pytest.raises(PlaylistError):
            hls._finish(["#EXTM3U", line], ())
    with pytest.raises(PlaylistError):
        hls._finish(["#EXTM3U", "harmless " + SECRET], (SECRET,))
    assert hls._finish(["#EXTM3U", "main.m3u8?s=" + SID], (SECRET, "")) == "#EXTM3U\nmain.m3u8?s=" + SID + "\n"


def test_a_byte_order_mark_and_odd_line_endings_are_fine():
    out = rewrite_master("﻿" + REAL_MASTER.replace("\n", "\r\n"), session())
    assert out.startswith("#EXTM3U\n") and "\r" not in out and f"main.m3u8?s={SID}" in out


# ── rewriting: media playlist ───────────────────────────────────────────────────────────────────────────────────────

def test_a_media_playlist_keeps_its_timing_and_loses_everything_else():
    sess = session()
    out = rewrite_media(REAL_MEDIA, sess, forbidden=(SECRET,))
    assert out == ("#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:11\n#EXT-X-MEDIA-SEQUENCE:0\n"
                   f"#EXTINF:10.000000,\nhls1/main/0.ts?s={SID}\n#EXTINF:9.5,\nhls1/main/1.ts?s={SID}\n#EXT-X-ENDLIST\n")
    assert SECRET not in out and "ApiKey" not in out and "runtimeTicks" not in out and "PlaySessionId" not in out
    # the two numbers Jellyfin needs to find a segment again stay on OUR side, per file
    assert entries(sess) == {"hls1/main/0.ts": ("segment", {"runtimeTicks": "0", "actualSegmentLengthTicks": "100000000"}),
                             "hls1/main/1.ts": ("segment", {"runtimeTicks": "100000000", "actualSegmentLengthTicks": "95000000"})}


def test_the_start_offset_goes_into_the_media_playlist_too():
    out = rewrite_media(REAL_MEDIA, session(start=12.0))
    assert out.splitlines()[:4] == ["#EXTM3U", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-VERSION:3", "#EXT-X-START:TIME-OFFSET=12.000,PRECISE=YES"]
    no_version = rewrite_media("#EXTM3U\n#EXTINF:5,\nhls1/main/0.ts?runtimeTicks=0\n", session(start=3))
    assert no_version.splitlines()[1] == "#EXT-X-START:TIME-OFFSET=3.000,PRECISE=YES"


def test_fragmented_mp4_playlists_keep_their_init_segment():
    text = ('#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:10\n#EXT-X-MAP:URI="hls1/main/-1.mp4?PlaySessionId=x&ApiKey=' + SECRET + '"\n'
            "#EXTINF:10,\nhls1/main/0.mp4?runtimeTicks=0&actualSegmentLengthTicks=1\n#EXT-X-ENDLIST\n")
    sess = session()
    out = rewrite_media(text, sess, forbidden=(SECRET,))
    assert f'#EXT-X-MAP:URI="hls1/main/-1.mp4?s={SID}"' in out and SECRET not in out
    assert set(entries(sess)) == {"hls1/main/-1.mp4", "hls1/main/0.mp4"}


def test_byte_ranges_survive_only_when_they_are_numbers():
    ok = "#EXTM3U\n#EXTINF:5,\n#EXT-X-BYTERANGE:1000@2000\nhls1/main/0.ts?runtimeTicks=0\n"
    assert "#EXT-X-BYTERANGE:1000@2000\n" in rewrite_media(ok, session())
    bad = "#EXTM3U\n#EXTINF:5,\n#EXT-X-BYTERANGE:1000@evil\nhls1/main/0.ts?runtimeTicks=0\n"
    assert "BYTERANGE" not in rewrite_media(bad, session())


@pytest.mark.parametrize("text", [
    "#EXTM3U\n#EXT-X-TARGETDURATION:10\n",                                                     # no segments at all
    "#EXTM3U\nhls1/main/0.ts\n",                                                               # a segment without its #EXTINF
    "#EXTM3U\n#EXTINF:abc,\nhls1/main/0.ts\n",                                                 # a duration that isn't a number
    "#EXTM3U\n#EXTINF:5,\nhttp://evil.example/0.ts\n",
    "#EXTM3U\n#EXTINF:5,\n//evil.example/0.ts\n",
    "#EXTM3U\n#EXTINF:5,\n/etc/passwd\n",
    "#EXTM3U\n#EXTINF:5,\n../../0.ts\n",
    "#EXTM3U\n#EXTINF:5,\nhls1/main/0.ts%00.m3u8\n",
    "#EXTM3U\n#EXTINF:5,\nhls1/main/0.php\n",
    "#EXTM3U\n#EXTINF:5,\nhls1/main/0.m3u8\n",                                                 # a playlist where a segment should be
    "#EXTM3U\n#EXTINF:5,\nhls1/main/0.ts\n#EXTINF:5,\nhls1/main/x.ts\n",                       # a segment name that isn't a number
    '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="http://evil.example/key"\n#EXTINF:5,\nhls1/main/0.ts\n',     # encrypted: nobody could play it
    '#EXTM3U\n#EXT-X-MAP:URI="http://evil.example/init.mp4"\n#EXTINF:5,\nhls1/main/0.mp4\n',
    '#EXTM3U\n#EXT-X-MAP:URI="../init.mp4"\n#EXTINF:5,\nhls1/main/0.mp4\n',
])
def test_a_media_playlist_that_is_not_what_jellyfin_sends_is_refused_whole(text):
    sess = session()
    with pytest.raises(PlaylistError):
        rewrite_media(text, sess, forbidden=(SECRET,))
    assert sess.table == {}                                                                    # and nothing it mentioned became fetchable


def test_a_playlist_with_endless_segments_is_refused(monkeypatch):
    monkeypatch.setattr(hls, "MAX_SEGMENTS", 5)
    text = "#EXTM3U\n" + "".join(f"#EXTINF:1,\nhls1/main/{n}.ts?runtimeTicks={n}\n" for n in range(6))
    with pytest.raises(PlaylistError):
        rewrite_media(text, session())
    assert rewrite_media(text.rsplit("#EXTINF", 1)[0], session()).count("#EXTINF") == 5


def test_other_tags_and_comments_are_dropped():
    text = ("#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2023-01-01T00:00:00Z\n#EXT-X-SESSION-DATA:DATA-ID=\"x\",VALUE=\"http://evil.example\"\n"
            "# a comment with ApiKey=" + SECRET + "\n#EXT-X-DEFINE:NAME=\"x\"\n#EXTINF:5,\nhls1/main/0.ts?runtimeTicks=0\n")
    out = rewrite_media(text, session(), forbidden=(SECRET,))
    assert out == f"#EXTM3U\n#EXTINF:5,\nhls1/main/0.ts?s={SID}\n"


def test_an_extra_query_value_that_is_not_a_number_is_not_kept():
    sess = session()
    rewrite_media("#EXTM3U\n#EXTINF:5,\nhls1/main/0.ts?runtimeTicks=../../x&actualSegmentLengthTicks=5&other=1\n", sess)
    assert entries(sess) == {"hls1/main/0.ts": ("segment", {"actualSegmentLengthTicks": "5"})}


# ── request parameters ──────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [(None, 1080), ("720", 720), ("144", 144), ("2160", 2160), ("1080", 1080), ("143", None), ("2161", None),
                                      ("72", None), ("12345", None), ("", None), ("abc", None), ("720.5", None), ("-720", None), ("٧٢٠", None)])
def test_height(raw, want):
    assert hls.parse_height(raw) == want


@pytest.mark.parametrize("raw,runtime,want", [(None, 100, 0.0), ("", 100, 0.0), ("0", 100, 0.0), ("60", 6000, 60.0), ("60.5", 6000, 60.5),
                                              ("6000", 6000, 6000.0), ("6001", 6000, None), ("-1", 6000, None), ("1e3", 6000, None),
                                              ("1.2345", 6000, None), ("abc", 6000, None), ("5", 0, None), ("0", 0, 0.0), ("12345678", 99999999, None)])
def test_start(raw, runtime, want):
    assert hls.parse_start(raw, runtime) == want


def test_session_ids_are_32_lowercase_hex():
    assert hls.valid_sid(SID) and not hls.valid_sid(SID.upper()) and not hls.valid_sid(SID[:-1]) and not hls.valid_sid(None)
    assert not hls.valid_sid(SID + "\n") and not hls.valid_sid("../" + SID[3:]) and not hls.valid_sid("")


# ── the sessions ────────────────────────────────────────────────────────────────────────────────────────────────────

class Rec:
    def __init__(self):
        self.stopped, self.t = [], 1000.0

    def stop(self, sid):
        self.stopped.append(sid)

    def clock(self):
        return self.t


def sessions(rec, **kw):
    return Sessions(rec.stop, clock=rec.clock, cap=kw.pop("cap", 2), idle_s=kw.pop("idle_s", 100.0), **kw)


def test_one_transcode_per_pass_and_a_new_film_stops_the_old_one():
    rec = Rec()
    s = sessions(rec)
    first = s.create("pass-a", BRAVO, None, 720, 0)
    second = s.create("pass-a", ALPHA, 2, 480, 30.0)
    assert rec.stopped == [first.sid] and s.active_count() == 1
    assert second.params["AudioStreamIndex"] == "2" and second.params["MaxHeight"] == "480" and second.start == 30.0
    assert s.touch(first.sid, BRAVO) is None and s.touch(second.sid, ALPHA) is second


def test_the_cap_turns_the_next_viewer_away_before_touching_anyone():
    rec = Rec()
    s = sessions(rec, cap=2)
    a, b = s.create("a", BRAVO, None, 720, 0), s.create("b", BRAVO, None, 720, 0)
    with pytest.raises(hls.Busy):
        s.create("c", BRAVO, None, 720, 0)
    with pytest.raises(hls.Busy):
        s.create("d", ALPHA, None, 720, 0)
    assert rec.stopped == [] and s.touch(a.sid, BRAVO) is a and s.touch(b.sid, BRAVO) is b       # nobody lost their transcode to a refusal
    again = s.create("a", ALPHA, None, 720, 0)                                                   # a viewer replacing their own is not "another"
    assert rec.stopped == [a.sid] and again.sid != a.sid


def test_idle_sessions_stop_counting_and_come_back_to_life_when_there_is_room():
    rec = Rec()
    s = sessions(rec, cap=1, idle_s=100)
    a = s.create("a", BRAVO, None, 720, 0)
    rec.t += 101                                                                                 # a stopped watching
    b = s.create("b", BRAVO, None, 720, 0)                                                       # their slot is free for b
    assert s.active_count() == 1
    with pytest.raises(hls.Busy):
        s.touch(a.sid, BRAVO)                                                                    # a presses play again but b has the slot
    rec.t += 101
    assert s.touch(a.sid, BRAVO) is a                                                            # b is gone too: a is back
    assert s.touch(b.sid, ALPHA) is None                                                         # (a session is for one film)


def test_the_reaper_stops_what_nobody_watches_once_and_forgets_the_long_dead():
    rec = Rec()
    s = sessions(rec, idle_s=100, ttl_s=1000)
    a, b = s.create("a", BRAVO, None, 720, 0), s.create("b", BRAVO, None, 720, 0)
    rec.t += 50
    s.reap()
    assert rec.stopped == []                                                                     # still within the idle time
    s.touch(b.sid, BRAVO)
    rec.t += 60
    s.reap()
    assert rec.stopped == [a.sid]                                                                # a: 110 s idle; b was seen 60 s ago
    s.reap()
    assert rec.stopped == [a.sid]                                                                # not stopped twice
    assert s.touch(a.sid, BRAVO) is a                                                            # paused, not gone: playing again wakes it
    rec.t += 2000
    s.reap()
    assert s.touch(b.sid, BRAVO) is None and s.touch(a.sid, BRAVO) is None                       # long dead: forgotten
    assert rec.stopped.count(b.sid) == 1


def test_stop_for_and_discard():
    rec = Rec()
    s = sessions(rec)
    a, b = s.create("a", BRAVO, None, 720, 0), s.create("b", ALPHA, None, 720, 0)
    assert s.stop_for("a", ALPHA) == 0 and s.stop_for("b", BRAVO) == 0 and rec.stopped == []     # the wrong pass or film: nothing
    assert s.stop_for("a", BRAVO) == 1 and rec.stopped == [a.sid]
    s.discard(b)
    assert rec.stopped == [a.sid, b.sid] and s.touch(b.sid, ALPHA) is None


def test_a_stopper_that_fails_never_breaks_the_caller():
    s = Sessions(lambda sid: (_ for _ in ()).throw(OSError("jellyfin is gone")), cap=2)
    a = s.create("a", BRAVO, None, 720, 0)
    s.create("a", ALPHA, None, 720, 0)                                                           # stopping the old one raised: swallowed
    assert s.stop_for("a", ALPHA) == 1
    s.discard(a)


def test_the_table_of_sessions_is_bounded():
    rec = Rec()
    s = Sessions(rec.stop, clock=rec.clock, cap=1000, idle_s=100, max_sessions=5)
    made = []
    for n in range(8):
        rec.t += 1
        made.append(s.create(f"p{n}", BRAVO, None, 720, 0))
    assert [m.sid for m in made[:3]] == rec.stopped                                              # the three oldest were stopped and dropped
    assert all(s.touch(m.sid, BRAVO) is None for m in made[:3]) and all(s.touch(m.sid, BRAVO) for m in made[3:])


def test_the_parameters_of_a_transcode_are_fixed_apart_from_the_validated_bits():
    p = hls.transcode_params(BRAVO, SID, 2, 720)
    assert p == {"MediaSourceId": BRAVO, "DeviceId": "skippy-video-gate", "PlaySessionId": SID, "VideoCodec": "h264", "AudioCodec": "aac",
                 "TranscodingMaxAudioChannels": "2", "MaxAudioChannels": "2", "SegmentContainer": "ts", "MinSegments": "1",
                 "BreakOnNonKeyFrames": "true", "MaxHeight": "720", "AudioStreamIndex": "2"}
    assert "AudioStreamIndex" not in hls.transcode_params(BRAVO, SID, None, 1080)
    assert not {k for k in p if k.lower() in ("apikey", "api_key", "token", "starttimeticks", "static")}


# ── the routes ──────────────────────────────────────────────────────────────────────────────────────────────────────

def master(stack, item=BRAVO, query="", tok=None):
    return stack.web.get(f"/t/{tok or stack.tok}/v/{item}/hls/master.m3u8{query}")


def sid_of(text):
    return re.search(r"\?s=([0-9a-f]{32})", text).group(1)


def seg_lines(text):
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def hls_calls(stack):
    return [r for r in stack.fake.calls("/Videos/") if r["method"] != "DELETE"]


def test_the_whole_chain_master_variant_segments(stack):
    r = master(stack, query="?audio=2&h=720&start=60")
    assert r.status_code == 200 and r.mimetype == "application/vnd.apple.mpegurl" and r.headers["Cache-Control"] == "no-store"
    text = r.get_data(as_text=True)
    sid = sid_of(text)
    assert "#EXT-X-START:TIME-OFFSET=60.000,PRECISE=YES" in text and fj.KEY not in text and "ApiKey" not in text
    variant = seg_lines(text)[0]
    assert variant == f"main.m3u8?s={sid}"                                                       # relative: resolves under the same /hls/ prefix
    r = stack.get(f"/v/{BRAVO}/hls/{variant}")
    assert r.status_code == 200 and r.mimetype == "application/vnd.apple.mpegurl"
    media = r.get_data(as_text=True)
    segs = seg_lines(media)
    assert segs == [f"hls1/main/{n}.ts?s={sid}" for n in range(5)] and fj.KEY not in media and "runtimeTicks" not in media
    r = stack.get(f"/v/{BRAVO}/hls/{segs[2]}")
    assert r.status_code == 200 and r.mimetype == "video/mp2t" and r.data == b"Gseg2"
    assert r.headers["Cache-Control"] == "private, max-age=3600" and "Content-Disposition" not in r.headers
    # what the gate asked Jellyfin: its own parameters and the two numbers it kept for that file, the key only in the header
    asked = hls_calls(stack)
    assert [c["path"] for c in asked] == [f"/Videos/{BRAVO}/master.m3u8", f"/Videos/{BRAVO}/main.m3u8", f"/Videos/{BRAVO}/hls1/main/2.ts"]
    assert asked[0]["q"] == {**hls.transcode_params(BRAVO, sid, 2, 720)}
    assert asked[2]["q"]["runtimeTicks"] == "200000000" and asked[2]["q"]["actualSegmentLengthTicks"] == "100000000"
    assert asked[2]["q"]["PlaySessionId"] == sid and "StartTimeTicks" not in asked[2]["q"]       # a start offset breaks Jellyfin's segments: not sent
    assert all(c["authed"] and fj.KEY not in str(c["query"]) and "api_key" not in str(c["query"]).lower() for c in asked)


def test_fragmented_mp4_works_through_the_gate(fake, make_stack):
    fake.fmp4 = True
    s = make_stack(fake)
    sid = sid_of(master(s).get_data(as_text=True))
    media = s.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}").get_data(as_text=True)
    assert f'#EXT-X-MAP:URI="hls1/main/-1.mp4?s={sid}"' in media and "styp" not in media
    assert s.get(f"/v/{BRAVO}/hls/hls1/main/-1.mp4?s={sid}").data.startswith(b"\x00\x00\x00\x18styp")
    r = s.get(f"/v/{BRAVO}/hls/hls1/main/3.mp4?s={sid}")
    assert r.status_code == 200 and r.mimetype == "video/mp4"


def test_the_clients_own_parameters_never_reach_jellyfin(stack):
    r = master(stack, query="?api_key=evil&VideoCodec=copy&MaxHeight=99999&StartTimeTicks=5&DeviceId=evil&PlaySessionId=" + "c" * 32 +
                            "&MediaSourceId=" + ALPHA + "&AudioCodec=ac3&Static=true")
    assert r.status_code == 200
    q = hls_calls(stack)[0]["q"]
    assert q["VideoCodec"] == "h264" and q["MaxHeight"] == "1080" and q["DeviceId"] == "skippy-video-gate" and q["MediaSourceId"] == BRAVO
    assert q["PlaySessionId"] == sid_of(r.get_data(as_text=True)) and q["AudioCodec"] == "aac"
    assert not {"api_key", "StartTimeTicks", "Static"} & set(q) and "evil" not in str(hls_calls(stack)[0])


def test_client_headers_are_not_forwarded_to_the_transcoder(stack):
    evil = {"Cookie": "s=evil", "Authorization": "Bearer evil", "X-Emby-Token": "evil", "Range": "bytes=0-5", "Referer": "https://evil.example/",
            "X-Forwarded-For": "6.6.6.6"}
    sid = sid_of(master(stack).get_data(as_text=True))
    stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}", headers=evil)
    stack.get(f"/v/{BRAVO}/hls/hls1/main/0.ts?s={sid}", headers=evil)
    for c in hls_calls(stack):
        assert "evil" not in str(c["headers"]) and c["range"] is None and c["authed"]


@pytest.mark.parametrize("query", ["?audio=abc", "?audio=9", "?audio=0", "?audio=-1", "?audio=", "?audio=1.5", "?audio=1000", "?audio=1%0a",
                                   "?h=abc", "?h=", "?h=143", "?h=2161", "?h=720p", "?h=1e3",
                                   "?start=abc", "?start=-5", "?start=6001", "?start=1e3", "?start=1.2345", "?start=x"])
def test_bad_parameters_are_a_400_and_cost_nothing(stack, query):
    before = stack.sessions.active_count()
    r = master(stack, query=query)
    assert r.status_code == 400 and r.get_json() == {"error": "bad request"}
    assert hls_calls(stack) == [] and stack.sessions.active_count() == before


def test_an_audio_track_must_be_one_the_film_has(stack):
    assert master(stack, query="?audio=1").status_code == 200 and master(stack, query="?audio=2").status_code == 200
    assert master(stack, query="?audio=3").status_code == 400                                    # 3 is a subtitle's index, not an audio track's
    assert hls_calls(stack)[0]["q"]["AudioStreamIndex"] == "1"


def test_unknown_films_and_series_are_404(stack):
    for ident in (fj.fid(99), fj.fid(10), fj.fid(20), "0" * 32):
        assert master(stack, item=ident).status_code == 404
    assert hls_calls(stack) == []


@pytest.mark.parametrize("rest", ["evil.php", "master.m3u8.ts", "../direct", "hls1/../../main.m3u8", "a/b/c/d/e/0.ts", "hls1/main/x.ts", "hls1/main/0.m3u8x",
                                  "hls1/main/0.ts/", "%2e%2e/main.m3u8", "hls1%2fmain%2f0.ts", "main.m3u8%00", "http://evil.example/0.ts", "/etc/passwd",
                                  "hls1/main/0.ts%0d%0aX:1", "hls1/main/9999999999.ts", "main"])
def test_only_the_file_shapes_hls_uses_can_be_asked_for(stack, rest):
    sid = sid_of(master(stack).get_data(as_text=True))
    before = len(hls_calls(stack))
    r = stack.get(f"/v/{BRAVO}/hls/{rest}?s={sid}")
    assert r.status_code == 404, rest
    assert len(hls_calls(stack)) == before                                                       # decided here, Jellyfin never asked


def test_a_file_the_playlist_never_mentioned_is_not_fetchable(stack):
    sid = sid_of(master(stack).get_data(as_text=True))
    stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}")                                               # five segments, 0..4
    before = len(hls_calls(stack))
    for rest in ("hls1/main/5.ts", "hls1/main/-1.mp4", "hls1/main/0.mp4", "other.m3u8", "hls1/sub/0.ts"):
        assert stack.get(f"/v/{BRAVO}/hls/{rest}?s={sid}").status_code == 404, rest
    assert len(hls_calls(stack)) == before
    assert stack.get(f"/v/{BRAVO}/hls/hls1/main/4.ts?s={sid}").status_code == 200                # the last advertised one


def test_the_session_id_is_required_genuine_and_for_this_film(stack):
    sid = sid_of(master(stack).get_data(as_text=True))
    stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}")
    before = len(hls_calls(stack))
    for q in ("", "?s=", f"?s={'0' * 32}", f"?s={sid.upper()}", f"?s={sid[:-1]}", f"?s={sid}0", f"?s={sid}%0a", "?s=../../x"):
        assert stack.get(f"/v/{BRAVO}/hls/hls1/main/0.ts{q}").status_code == 404, q
    assert stack.get(f"/v/{ALPHA}/hls/hls1/main/0.ts?s={sid}").status_code == 404               # the same sid on another film
    assert stack.get(f"/v/{BRAVO}/hls/main.m3u8").status_code == 404
    assert len(hls_calls(stack)) == before


def test_a_session_works_under_a_fresh_pass_too(stack):
    sid = sid_of(master(stack).get_data(as_text=True))
    other = stack.pass_for(5)                                       # the portal hands out a fresh pass mid-film: same session
    assert stack.web.get(f"/t/{other}/v/{BRAVO}/hls/main.m3u8?s={sid}").status_code == 200


def test_the_transcode_cap_returns_429_and_leaves_the_others_alone(fake, make_stack):
    s = make_stack(fake, cap=2)
    a, b = s.pass_for(1), s.pass_for(2)
    ra, rb = master(s, tok=a), master(s, tok=b)
    assert ra.status_code == 200 and rb.status_code == 200
    r = master(s, tok=s.pass_for(3))
    assert r.status_code == 429 and r.headers["Retry-After"] == "10" and r.get_json() == {"error": "too many requests", "retry": True}
    assert s.stopped == []                                                                       # nobody lost their film to the refusal
    assert len(fake.calls(f"/Videos/{BRAVO}/master.m3u8")) == 2                                   # and Jellyfin was not asked for a third
    s.clock.advance(121)                                                                         # the others stopped watching
    assert master(s, tok=s.pass_for(3)).status_code == 200


def test_starting_another_film_with_the_same_pass_stops_the_first_transcode(stack):
    first = sid_of(master(stack).get_data(as_text=True))
    stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={first}")
    assert first in stack.fake.encodings                                                         # asking for the media playlist started ffmpeg
    second = sid_of(master(stack, item=ALPHA).get_data(as_text=True))
    assert stack.stopped == [first] and (("skippy-video-gate", first)) in stack.fake.deleted    # Jellyfin was told, with our device id
    assert first not in stack.fake.encodings and first != second
    assert stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={first}").status_code == 404                 # and the old session is gone


def test_a_progress_stop_ends_the_transcode(stack):
    sid = sid_of(master(stack).get_data(as_text=True))
    body = b'{"id": "%s", "pos": 100, "state": "stop"}' % BRAVO.encode()
    assert stack.web.post(f"{stack.base}/progress", data=body).status_code == 200
    assert stack.stopped == [sid] and ("skippy-video-gate", sid) in stack.fake.deleted
    assert stack.web.post(f"{stack.base}/progress", data=body).status_code == 200                # a second stop has nothing left to stop
    assert stack.stopped == [sid]


def test_nobody_watching_means_the_transcode_is_stopped(stack):
    sid = sid_of(master(stack).get_data(as_text=True))
    stack.clock.advance(60)
    stack.sessions.reap()
    assert stack.stopped == []
    stack.clock.advance(61)
    stack.sessions.reap()
    assert stack.stopped == [sid]
    assert stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}").status_code == 200                    # paused for a while, then play: Jellyfin starts again


def test_when_jellyfin_fails_the_session_is_not_kept(fake, make_stack):
    s = make_stack(fake)
    fake.go_down()
    r = master(s)
    assert r.status_code == 503 and r.get_json() == {"error": "Jellyfin is not answering"}
    assert s.sessions.active_count() == 0                                                        # no slot held by a transcode that never was
    fake.come_back()
    assert master(s).status_code == 200


@pytest.mark.parametrize("which,hook", [
    ("master", lambda text, w: "<html>login</html>"),
    ("master", lambda text, w: text.replace("main.m3u8", "http://evil.example/main.m3u8")),
    ("master", lambda text, w: text.replace('CODECS="avc1.64001F,mp4a.40.2"', f'CODECS="{fj.KEY}"')),            # the key in a shape-valid place
    ("main", lambda text, w: text.replace("hls1/main/1.ts", "//evil.example/1.ts")),
    ("main", lambda text, w: text.replace("hls1/main/", "../../")),
    ("main", lambda text, w: "#EXTM3U\n"),
])
def test_a_hostile_playlist_from_jellyfin_is_a_502_and_nothing_of_it_is_passed_on(fake, make_stack, which, hook):
    s = make_stack(fake)
    fake.playlist_hook = lambda text, w: hook(text, w) if w == which else text
    r = master(s)
    if which == "main":
        assert r.status_code == 200
        r = s.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid_of(r.get_data(as_text=True))}")
    assert r.status_code == 502 and r.get_json() == {"error": "Jellyfin sent something unusable"}
    assert "evil" not in r.get_data(as_text=True) and fj.KEY not in r.get_data(as_text=True)


def test_a_segment_jellyfin_cannot_find_is_a_404_not_a_stack_trace(stack):
    sid = sid_of(master(stack).get_data(as_text=True))
    stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}")
    stack.fake.items[:] = [i for i in stack.fake.items if i["Id"] != BRAVO]                      # the film vanished from Jellyfin meanwhile
    r = stack.get(f"/v/{BRAVO}/hls/hls1/main/0.ts?s={sid}")
    assert r.status_code == 404 and r.get_json() == {"error": "not found"}


def test_the_slots_are_shared_with_direct_play_and_returned(fake, make_stack):
    s = make_stack(fake, max_streams=1)
    sid = sid_of(master(s).get_data(as_text=True))
    s.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}")
    first = s.web.get(f"{s.base}/v/{BRAVO}/hls/hls1/main/0.ts?s={sid}", buffered=False)         # open and unread: holds the one slot
    busy = s.get(f"/v/{BRAVO}/hls/hls1/main/1.ts?s={sid}")
    assert busy.status_code == 429 and busy.headers["Retry-After"] == "2"
    assert s.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}").status_code == 200                         # playlists don't need a slot
    first.close()
    for _ in range(40):
        if s.get(f"/v/{BRAVO}/hls/hls1/main/1.ts?s={sid}").status_code == 200:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the slot was never given back")
