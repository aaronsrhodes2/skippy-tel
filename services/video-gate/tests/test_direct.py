"""Direct play: the original file, Range-correct, streamed (never held in memory), and closed the moment the client leaves."""
import http.client
import time

import pytest

import fake_jellyfin as fj
from fake_jellyfin import body_bytes

ALPHA, BRAVO = fj.fid(1), fj.fid(2)


def hang_up(conn, response=None):
    """Really close the connection (http.client keeps the socket open while a response object still holds it)."""
    import socket
    sock = conn.sock
    if response is not None:
        response.close()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except (OSError, AttributeError):
        pass
    conn.close()


def stream_calls(stack, item=ALPHA):
    return stack.fake.calls(f"/Videos/{item}/stream")


def test_whole_file_and_headers(stack):
    r = stack.get(f"/v/{ALPHA}/direct")
    assert r.status_code == 200 and r.mimetype == "video/mp4"
    assert r.headers["Accept-Ranges"] == "bytes" and r.headers["Content-Length"] == "1000000"
    assert r.data == body_bytes(0, 1_000_000)
    assert "Content-Range" not in r.headers
    assert r.headers["Access-Control-Allow-Origin"] == "*" and "Content-Range" in r.headers["Access-Control-Expose-Headers"]
    assert r.headers["Last-Modified"] == "Mon, 11 Sep 2023 09:43:37 GMT"
    assert "Content-Disposition" not in r.headers and "Set-Cookie" not in r.headers


@pytest.mark.parametrize("rng,start,end", [
    ("bytes=10-19", 10, 19),                 # start
    ("bytes=0-0", 0, 0),
    ("bytes=500000-500999", 500000, 500999),  # middle
    ("bytes=999990-", 999990, 999999),        # open-ended
    ("bytes=-500", 999500, 999999),           # suffix
    ("bytes=999000-5000000", 999000, 999999),  # end beyond the file: clipped by the server
])
def test_range_answers_are_206_with_the_right_bytes(stack, rng, start, end):
    r = stack.get(f"/v/{ALPHA}/direct", headers={"Range": rng})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes {start}-{end}/1000000" and r.headers["Content-Length"] == str(end - start + 1)
    assert r.data == body_bytes(start, end - start + 1)
    assert stream_calls(stack)[-1]["range"] == rng                      # forwarded as asked, nothing added


def test_beyond_the_end_is_416(stack):
    r = stack.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=2000000-"})
    assert r.status_code == 416 and r.headers["Content-Range"] == "bytes */1000000" and r.data == b""


@pytest.mark.parametrize("rng", ["bytes=abc", "bytes=0-1,5-9", "items=0-5", "bytes=-", "bytes=", "bytes=١-٥",
                                 "bytes=" + "9" * 40 + "-", "BYTES=0-5", " bytes=0-5", "bytes=0-5 "])
def test_odd_range_headers_are_not_forwarded(stack, rng):
    r = stack.get(f"/v/{ALPHA}/direct", headers={"Range": rng})
    assert r.status_code == 200 and len(r.data) == 1_000_000
    assert stream_calls(stack)[-1]["range"] is None


def test_the_upstream_request_is_ours_not_the_clients(stack):
    r = stack.get(f"/v/{ALPHA}/direct", headers={
        "Range": "bytes=0-9", "Cookie": "session=evil", "Authorization": "Bearer evil", "X-Emby-Token": "evil", "X-Forwarded-For": "6.6.6.6",
        "Referer": "https://evil.example/", "If-Range": "x", "If-None-Match": '"x"', "Origin": "https://evil.example", "X-Custom": "1"})
    assert r.status_code == 206
    up = stream_calls(stack)[-1]
    assert up["q"] == {"static": "true", "mediaSourceId": ALPHA}        # exactly this query, nothing the client added
    assert up["authed"] and "evil" not in str(up["headers"])             # our Authorization, none of theirs
    for h in ("cookie", "x-emby-token", "x-forwarded-for", "referer", "if-range", "if-none-match", "origin", "x-custom"):
        assert h not in up["headers"], h
    assert fj.KEY not in str(up["query"])


def test_a_file_browsers_cannot_play_is_a_409_with_a_hint(stack):
    r = stack.get(f"/v/{BRAVO}/direct")
    assert r.status_code == 409 and r.get_json() == {"error": "conflict", "use": "hls"}
    r = stack.get(f"/v/{fj.fid(6)}/direct")                              # not probed yet: not known to be playable as is
    assert r.status_code == 409 and r.get_json()["use"] == "hls"
    assert stack.fake.calls("/Videos/") == []                            # decided from the catalog: Jellyfin wasn't asked


@pytest.mark.parametrize("ident", [fj.fid(99), fj.fid(10), fj.fid(20), "0" * 32])      # unknown, a series, a collection, nothing
def test_unknown_or_unplayable_ids_are_404_without_asking_jellyfin(stack, ident):
    r = stack.get(f"/v/{ident}/direct")
    assert r.status_code == 404 and r.get_json() == {"error": "not found"}
    assert stack.fake.calls("/Videos/") == []


def test_head_asks_jellyfin_for_headers_only(stack):
    r = stack.web.head(f"{stack.base}/v/{ALPHA}/direct")
    assert r.status_code == 200 and r.headers["Content-Length"] == "1000000" and r.data == b""
    assert stream_calls(stack)[-1]["method"] == "HEAD"


def test_jellyfin_errors_become_plain_answers(stack):
    stack.fake.items.append(fj.movie(50, "Ghost"))                       # in Jellyfin's list, then gone: make the catalog know it...
    assert stack.library.refresh()
    stack.fake.items.pop()                                               # ...and Jellyfin no longer has it (it answers 400)
    r = stack.get(f"/v/{fj.fid(50)}/direct")
    assert r.status_code == 404 and r.get_json() == {"error": "not found"} and "Error processing" not in r.get_data(as_text=True)


def test_a_gzipped_upstream_answer_is_refused_not_passed_on_as_raw_bytes(stack):
    stack.fake.stream_extra_headers = {"Content-Encoding": "gzip"}
    r = stack.get(f"/v/{ALPHA}/direct")
    assert r.status_code == 502 and r.get_json() == {"error": "Jellyfin sent something unusable"}


def test_every_stream_slot_is_returned(make_stack, fake):
    s = make_stack(fake, max_streams=1)
    first = s.web.get(f"{s.base}/v/{ALPHA}/direct", buffered=False)       # open, nobody reading it: holds the only slot
    assert first.status_code == 200
    busy = s.get(f"/v/{ALPHA}/direct")
    assert busy.status_code == 429 and busy.get_json()["error"] == "too many requests"
    first.close()                                                         # the client left before the first byte
    for _ in range(30):                                                   # (the upstream close is immediate; allow for the thread)
        if s.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"}).status_code == 206:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the slot was never given back")
    for _ in range(5):                                                    # errors give the slot back too
        s.get(f"/v/{fj.fid(99)}/direct")
        assert s.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"}).status_code == 206


def test_it_streams_it_does_not_buffer_and_closes_upstream_when_the_client_goes(live):
    fake = live.fake
    fake.video_size = 300 * 1024 * 1024                                   # 300 MB "film"
    c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=10)
    c.request("GET", f"{live.base}/v/{ALPHA}/direct")
    r = c.getresponse()
    assert r.status == 200 and int(r.getheader("Content-Length")) == 300 * 1024 * 1024
    got = r.read(256 * 1024)                                              # read a little...
    assert got == body_bytes(0, 256 * 1024)
    hang_up(c, r)                                                         # ...and hang up
    key = f"stream:{ALPHA}"
    for _ in range(100):
        if fake.aborted.get(key):
            break
        time.sleep(0.05)
    assert fake.aborted.get(key), "Jellyfin was never told the client had gone (the upstream connection stayed open)"
    assert fake.sent[key] < 40 * 1024 * 1024, "the gate read far ahead of the client: it is buffering"
    # and the thread/slot is free again
    assert live.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"}).status_code == 206


def test_client_leaving_before_any_byte_also_closes_upstream(live):
    fake = live.fake
    fake.video_size = 300 * 1024 * 1024
    c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=10)
    c.request("GET", f"{live.base}/v/{ALPHA}/direct")
    hang_up(c)
    key = f"stream:{ALPHA}"
    for _ in range(100):
        if fake.aborted.get(key):
            break
        time.sleep(0.05)
    assert fake.aborted.get(key)
    assert fake.sent[key] < 40 * 1024 * 1024


def test_seeking_over_a_real_connection(live):
    c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=10)
    c.request("GET", f"{live.base}/v/{ALPHA}/direct", headers={"Range": "bytes=123456-123555"})
    r = c.getresponse()
    assert r.status == 206 and r.getheader("Content-Range") == "bytes 123456-123555/1000000"
    assert r.read() == body_bytes(123456, 100)
    c.close()
