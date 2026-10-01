"""Hostile input on every route: traversal, SSRF, header injection, query smuggling. The rule these tests pin: whatever the client
sends, the gate either answers from its own catalog or asks Jellyfin for exactly one of its templated URLs, and nothing a client
wrote ever ends up in an upstream URL or header."""
import json
import re

import pytest

import fake_jellyfin as fj

ALPHA, BRAVO = fj.fid(1), fj.fid(2)

BAD_IDS = [
    "../../../etc/passwd", "..%2f..%2fetc%2fpasswd", "%2e%2e", "%2e%2e%2f%2e%2e%2f", "..\\..\\x", "%5c..%5c",
    "http://evil.example/", "http:%2f%2fevil.example%2f", "https%3a%2f%2fevil.example", "//evil.example/x", "evil.example%2f",
    "0" * 31, "0" * 33, "g" * 32, "G" * 32, "A" * 32, "0" * 31 + "A", "-" * 32, " " * 32, "٠" * 32, "0" * 16 + "-" + "0" * 15,
    ALPHA + "%0d%0aSet-Cookie:x=1", ALPHA + "%0a", ALPHA + "%00", ALPHA + "%20", "%20" + ALPHA, ALPHA + "/", ALPHA + "%2f", ALPHA + ".json",
    ALPHA + "?x=1", ALPHA + "%3fx=1", ALPHA + "%23x", "null", "undefined", "%", "%zz", "{id}", "*", "'", '"', "<script>", "0x" + "0" * 30,
    "1e" + "0" * 30, "0" * 32 + "0" * 32,
]
ROUTES = [  # (a label, method, a path with {id}, a body for POSTs)
    ("direct", "GET", "/v/{id}/direct", None),
    ("hls master", "GET", "/v/{id}/hls/master.m3u8", None),
    ("hls variant", "GET", "/v/{id}/hls/main.m3u8?s=" + "ab" * 16, None),
    ("poster", "GET", "/img/{id}/poster", None),
    ("subtitle", "GET", "/sub/{id}/3.vtt", None),
]


def quiet(stack):
    """A mark in Jellyfin's request log: what the test must not add to."""
    return len(stack.fake.requests)


@pytest.mark.parametrize("ident", BAD_IDS, ids=[f"{n:02d}" for n in range(len(BAD_IDS))])
def test_a_bad_id_is_a_404_on_every_route_and_never_reaches_jellyfin(stack, ident):
    mark = quiet(stack)
    for label, method, path, _ in ROUTES:
        r = stack.web.open(f"{stack.base}{path.format(id=ident)}", method=method)
        assert r.status_code == 404, (label, ident, r.status_code)
        assert "Location" not in r.headers and "evil" not in r.get_data(as_text=True)
    for body in (json.dumps({"id": ident, "pos": 10, "state": "progress"}),):
        r = stack.web.post(f"{stack.base}/progress", data=body.encode())
        assert r.status_code in (404, 400), ident                  # (a quote or a backslash in the JSON text is the player's mistake: 400)
    assert quiet(stack) == mark


@pytest.mark.parametrize("kind", ["Primary", "POSTER", "poster/", "poster%2f..%2f..", "banner", "../poster", "poster%0d%0a", "", "primary",
                                  "Backdrop", "logo.png", "thumb%00", "%70oster%2f", "..", "poster;x"])
def test_a_bad_image_kind_is_a_404_and_asks_nothing(stack, kind):
    mark = quiet(stack)
    assert stack.web.get(f"{stack.base}/img/{ALPHA}/{kind}").status_code == 404
    assert quiet(stack) == mark


@pytest.mark.parametrize("name", ["3", "3.", ".vtt", "x.vtt", "-3.vtt", "+3.vtt", "3.VTT", "3.srt", "3.vtt/", "3.vtt%0a", "3.vtt%00", "3 .vtt", "٣.vtt",
                                  "03a.vtt", "1000.vtt", "99999999999999999999.vtt", "3.vtt.vtt", "..%2f3.vtt", "4.vtt", "0.vtt", "2.vtt"])
def test_only_a_real_text_subtitle_index_is_ever_asked_for(stack, name):
    mark = quiet(stack)
    r = stack.web.get(f"{stack.base}/sub/{BRAVO}/{name}")
    assert r.status_code == 404, name                              # (4 is a picture subtitle, 0 and 2 are not subtitles at all)
    assert quiet(stack) == mark


def test_a_query_string_never_gets_past_the_gate(stack):
    """Every route, with a fistful of parameters Jellyfin understands and would love to be handed: none are forwarded."""
    nasty = "api_key=evil&ApiKey=evil&static=false&Static=true&mediaSourceId=" + ALPHA + "&StartTimeTicks=5&Container=x&deviceId=evil&userId=" + fj.OTHER_ID
    sid = None
    for path in (f"/v/{ALPHA}/direct?{nasty}", f"/img/{ALPHA}/poster?w=160&{nasty}", f"/v/{BRAVO}/hls/master.m3u8?{nasty}",
                 f"/sub/{BRAVO}/3.vtt?{nasty}", f"/catalog.json?{nasty}"):
        r = stack.get(path)
        assert r.status_code in (200, 206, 304), path
        if "master.m3u8" in path:
            sid = r.get_data(as_text=True).split("?s=")[1].split()[0]
    stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}&{nasty}")
    stack.get(f"/v/{BRAVO}/hls/hls1/main/0.ts?s={sid}&{nasty}")
    asked = [r for r in stack.fake.requests if r["path"] not in ("/Users", "/Items")]
    assert len(asked) >= 6
    for r in asked:
        assert "evil" not in str(r["query"]) and "evil" not in str(r["headers"]) and fj.OTHER_ID not in str(r["query"]), r["path"]
        assert not {"api_key", "ApiKey", "Static", "StartTimeTicks", "Container", "userId"} & set(r["q"]), r["path"]
    assert [r["q"] for r in asked if r["path"].endswith("/stream")] == [{"static": "true", "mediaSourceId": ALPHA}]


def test_every_upstream_call_is_one_of_the_templates(stack):
    """Drive every route with a good id, then check each path Jellyfin saw against the short list the gate is allowed to ask for."""
    sid = master_sid(stack)
    stack.get(f"/v/{BRAVO}/hls/main.m3u8?s={sid}")
    stack.get(f"/v/{BRAVO}/hls/hls1/main/0.ts?s={sid}")
    stack.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"})
    stack.get(f"/img/{ALPHA}/poster")
    stack.get(f"/sub/{BRAVO}/3.vtt")
    stack.web.post(f"{stack.base}/progress", data=json.dumps({"id": BRAVO, "pos": 100, "state": "stop"}).encode())
    stack.get("/catalog.json")
    templates = [r"/Users", r"/Items", rf"/Items/{ALPHA}/Images/Primary", rf"/Videos/({ALPHA}|{BRAVO})/stream",
                 rf"/Videos/{BRAVO}/(master|main)\.m3u8", rf"/Videos/{BRAVO}/hls1/main/[0-9]+\.ts", r"/Videos/ActiveEncodings",
                 rf"/Videos/{BRAVO}/{BRAVO}/Subtitles/3/Stream\.vtt", rf"/UserItems/{BRAVO}/UserData", r"/System/Info/Public"]
    for r in stack.fake.requests:
        assert any(re.fullmatch(t, r["path"]) for t in templates), r["path"]
        assert r["method"] in ("GET", "HEAD", "POST", "DELETE") and not r.get("unexpected")
    methods = {(r["method"], r["path"].split("/")[1]) for r in stack.fake.requests}
    assert ("POST", "UserItems") in methods and ("DELETE", "Videos") in methods


def master_sid(stack):
    r = stack.get(f"/v/{BRAVO}/hls/master.m3u8")
    assert r.status_code == 200
    return r.get_data(as_text=True).split("?s=")[1].split()[0]


def test_headers_a_client_sends_do_not_change_what_the_gate_answers_or_asks(stack):
    evil = {"Host": "evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-For": "6.6.6.6", "X-Forwarded-Proto": "http",
            "Origin": "https://evil.example", "Referer": "https://evil.example/", "Cookie": "a=b", "Authorization": "Bearer evil",
            "X-Emby-Token": "evil", "X-MediaBrowser-Token": "evil", "Accept-Encoding": "gzip, br", "Transfer-Encoding": "chunked", "X-HTTP-Method-Override": "DELETE",
            "Destination": "http://evil.example/", "If-Range": "x", "Range": "bytes=0-9"}
    r = stack.get(f"/v/{ALPHA}/direct", headers=evil)
    assert r.status_code == 206 and "evil" not in str(r.headers)
    up = stack.fake.calls(f"/Videos/{ALPHA}/stream")[-1]
    assert "evil" not in str(up["headers"]) and up["headers"].get("accept-encoding") == "identity" and up["range"] == "bytes=0-9"
    assert up["headers"].get("host", "").startswith("127.0.0.1")                                  # Jellyfin is asked at its own address, whatever Host said


def test_responses_never_carry_anything_from_jellyfins_headers(fake, make_stack):
    fake.stream_extra_headers = {"Set-Cookie": "s=evil; Path=/", "Location": "http://evil.example/", "Content-Disposition": "attachment; filename=evil.exe",
                                 "X-Evil": "1", "Access-Control-Allow-Origin": "http://evil.example", "Content-Security-Policy": "default-src evil",
                                 "Last-Modified": "<script>alert(1)</script>", "ETag": '"evil"', "Server": "evil"}
    s = make_stack(fake)
    r = s.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    for name in ("Set-Cookie", "Location", "Content-Disposition", "X-Evil", "Content-Security-Policy", "ETag", "Server"):
        assert name not in r.headers, name
    assert r.headers["Access-Control-Allow-Origin"] == "*" and "evil" not in str(r.headers)
    assert r.headers["Content-Type"] == "video/mp4"


def test_a_malformed_range_or_date_from_jellyfin_is_not_repeated(fake, make_stack):
    fake.stream_extra_headers = {"Last-Modified": "not a date at all, but long enough <b>", "Content-Range": "bytes evil/x"}
    s = make_stack(fake)
    r = s.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and "Last-Modified" not in r.headers and "evil" not in str(r.headers)
    assert "Content-Range" not in r.headers
