"""A misbehaving Jellyfin: redirects, a connection that dies mid-film, a body that is too big. The gate never follows, never repeats
what it can't verify, and never lets a half-sent film pass for a whole one."""
import http.client
import socket
import threading
import time

import pytest

import fake_jellyfin as fj
from jellyfin import Client, JellyfinError

ALPHA = fj.fid(1)


def test_a_redirect_from_jellyfin_is_never_followed(fake, make_stack):
    evil = fj.FakeJellyfin()                                               # somewhere Jellyfin tries to send the gate (with its key, if it followed)
    evil.url = evil.start()
    try:
        fake.stream_redirect = evil.url + "/steal"
        s = make_stack(fake)
        r = s.get(f"/v/{ALPHA}/direct")
        assert r.status_code == 502 and r.get_json() == {"error": "Jellyfin sent something unusable"} and "Location" not in r.headers
        time.sleep(0.1)
        assert evil.requests == []                                         # not one request, so the key never travelled
    finally:
        evil.stop()


def test_the_client_has_no_proxy_cookies_or_redirects_from_the_environment(fake, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    c = Client(fake.url, fj.KEY)
    assert c.ping() is True and c.users()                                  # reached Jellyfin directly: a proxy on port 9 would have refused
    assert c._http.trust_env is False


def test_the_clients_repr_never_shows_the_key(fake):
    c = Client(fake.url, fj.KEY)
    assert fj.KEY not in repr(c) and fj.KEY not in str(c) and fj.KEY not in str(vars(c).get("base"))


@pytest.mark.parametrize("url", ["", "ftp://x", "http://", "http://u:p@host:1/", "http://host:1/?k=v", "javascript:alert(1)", "//host"])
def test_a_jellyfin_address_must_be_a_plain_http_one(url):
    with pytest.raises(ValueError):
        Client(url, fj.KEY)


@pytest.mark.parametrize("key", ["", "short", "has space in it 12345678", 'quote"inside12345678', "new\nline12345678", "x" * 300, "ключключключ"])
def test_a_missing_or_odd_key_is_refused_before_it_can_end_up_in_a_header(key):
    with pytest.raises(ValueError) as e:
        Client("http://127.0.0.1:1", key)
    assert key not in str(e.value) or key == ""


def test_the_upstream_methods_refuse_what_they_were_not_built_for(fake):
    c = Client(fake.url, fj.KEY)
    for bad in ("../x", "x" * 32, "A" * 32, ALPHA + "0", "", None, 5):
        with pytest.raises(ValueError):
            c.stream(bad)
        with pytest.raises(ValueError):
            c.image(bad, "Primary", 160)
        with pytest.raises(ValueError):
            c.subtitle(bad, 1)
    with pytest.raises(ValueError):
        c.image(ALPHA, "Banner", 160)
    with pytest.raises(ValueError):
        c.image(ALPHA, "Primary", 10 ** 6)
    with pytest.raises(ValueError):
        c.subtitle(ALPHA, -1)
    with pytest.raises(ValueError):
        c.subtitle(ALPHA, True)
    for name in ("../x.ts", "x.php", "http://evil/x.ts", "a/b/c/d/e/x.ts", "master.m3u8/../x"):
        with pytest.raises(ValueError):
            c.hls_text(ALPHA, name, {})
    for params in ({"api_key": "x y"}, {"a b": "1"}, {"k": "v&x=1"}, {"k": "../.."}, {"k": "a" * 100}):
        with pytest.raises(ValueError):
            c.hls_text(ALPHA, "master.m3u8", params)
    with pytest.raises(ValueError):
        c.stop_encoding("not-a-session")
    with pytest.raises(ValueError):
        c.set_user_data(fj.USER_ID, ALPHA, {"Played": "yes"})
    with pytest.raises(ValueError):
        c.set_user_data(fj.USER_ID, ALPHA, {"PlaybackPositionTicks": -1})
    with pytest.raises(ValueError):
        c.set_user_data(fj.USER_ID, ALPHA, {"LastPlayedDate": "yesterday"})
    assert fake.requests == []                                              # all of that was refused before anything went out


def test_only_the_four_known_fields_can_be_written_to_jellyfin(fake):
    c = Client(fake.url, fj.KEY)
    c.set_user_data(fj.USER_ID, ALPHA, {"Played": True, "IsFavorite": True, "Rating": 10, "Key": "x", "PlayCount": 2})
    assert fake.userdata[-1]["body"] == {"Played": True, "PlayCount": 2}


def test_an_oversize_playlist_is_refused_not_buffered(fake, make_stack):
    s = make_stack(fake)
    fake.playlist_hook = lambda text, which: text + "#" + "x" * (9 * 1024 * 1024) if which == "master" else text
    r = s.get(f"/v/{fj.fid(2)}/hls/master.m3u8")
    assert r.status_code == 502 and s.sessions.active_count() == 0


def test_an_image_that_claims_to_be_html_is_refused(fake, make_stack, monkeypatch):
    s = make_stack(fake)
    monkeypatch.setattr(fake, "image", lambda send, item_id, kind, q: send(200, b"<html>", "text/html"))
    r = s.get(f"/img/{ALPHA}/poster")
    assert r.status_code == 502 and not list(s.images.root.rglob("*.*"))


# ── a connection that dies in the middle of a film ──────────────────────────────────────────────────────────────────

def get_raw(port, path, timeout=3.0):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def test_a_film_cut_short_without_a_length_is_an_error_not_a_complete_file(live):
    """Jellyfin streams with chunked encoding and dies after 200 KB. The gate must not finish the response normally (that would put
    the terminating chunk on the wire and a player would think it had the whole file)."""
    fake = live.fake
    fake.stream_chunked, fake.stream_truncate = True, 200 * 1024
    with pytest.raises((http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionError, socket.timeout)):
        get_raw(live.port, f"{live.base}/v/{ALPHA}/direct")
    for _ in range(60):                                                    # and the slot and the upstream connection are given back
        if live.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"}).status_code == 206:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the slot was never given back")


def test_a_chunked_film_that_ends_properly_is_passed_on_whole(live):
    live.fake.stream_chunked = True
    live.fake.video_size = 300_000
    status, body = get_raw(live.port, f"{live.base}/v/{ALPHA}/direct")
    assert status == 200 and body == fj.body_bytes(0, 300_000)


def test_a_film_cut_short_with_a_length_never_completes_either(live):
    live.fake.stream_truncate = 100 * 1024
    with pytest.raises((http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionError, socket.timeout)):
        get_raw(live.port, f"{live.base}/v/{ALPHA}/direct", timeout=2.0)


def test_many_dropped_films_do_not_leak_slots(fake, make_stack):
    s = make_stack(fake, max_streams=2)
    for _ in range(6):
        first = s.web.get(f"{s.base}/v/{ALPHA}/direct", buffered=False)
        first.close()
    deadline = time.time() + 3
    while time.time() < deadline:
        if s.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"}).status_code == 206:
            return
        time.sleep(0.05)
    pytest.fail("slots leaked")


def test_a_hung_jellyfin_costs_a_timeout_not_a_thread_forever(fake, make_stack):
    s = make_stack(fake, client_opts={"read_timeout": 0.4})
    fake.delay[f"/Videos/{ALPHA}/stream"] = 1.5
    t = time.time()
    r = s.get(f"/v/{ALPHA}/direct")
    assert r.status_code == 503 and time.time() - t < 1.3
    assert s.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=0-9"}).status_code in (206, 503)


def test_the_upstream_connection_closes_when_the_gate_is_done(live):
    """After a ranged read the pooled connection is reusable and nothing is left half-read (a keep-alive connection with an unread
    body would poison the next request)."""
    for _ in range(8):
        status, body = get_raw(live.port, f"{live.base}/v/{ALPHA}/direct")
        assert status == 200 and len(body) == 1_000_000
        r = live.get(f"/v/{ALPHA}/direct", headers={"Range": "bytes=10-19"})
        assert r.status_code == 206 and r.data == fj.body_bytes(10, 10)
    assert threading.active_count() < 60


def test_jellyfin_error_kinds_never_carry_upstream_text(fake):
    c = Client(fake.url, fj.KEY)
    err = None
    try:
        c.subtitle(fj.fid(1), 5)                                           # Jellyfin answers 500 "Error processing request."
    except JellyfinError as e:
        err = e
    assert err is not None and err.kind == "down" and "Error processing" not in str(err) and err.__cause__ is None
