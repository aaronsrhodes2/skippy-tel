"""The pass: nothing works without one the portal signed, a MUSIC pass is not one, and a refused request never reaches Jellyfin."""
import hashlib
import hmac
import re

import pytest

import fake_jellyfin as fj
import gate
from helpers import KEY, NOW

HOME_TOKEN = "test-home-token"


def music_sign(home_token: str, exp: int) -> str:
    """The MUSIC gate's pass for the same home token (services/music-gate/gate.py: its own label and signed text)."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    key = hmac.new(hashed.encode(), b"music-gate-v1", hashlib.sha256).digest()
    return f"{exp}.{hmac.new(key, f'music:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def test_pass_rules():
    good = gate.sign(KEY, NOW + 3600)
    assert gate.valid(KEY, good, NOW)
    assert not gate.valid(KEY, good, NOW + 3601)                                   # expired
    assert not gate.valid(gate.gate_key("other"), good, NOW)                       # someone else's key
    assert not gate.valid(KEY, gate.sign(KEY, NOW + gate.MAX_PASS_S + 60), NOW)    # too long-lived to be ours
    assert gate.valid(KEY, gate.sign(KEY, NOW + gate.MAX_PASS_S), NOW)             # exactly two days is the limit
    assert not gate.valid(KEY, good[:-1] + ("0" if good[-1] != "0" else "1"), NOW)  # one character off
    assert not gate.valid(b"", good, NOW)                                          # no home token: nothing opens
    for junk in ("", "nope", "1.2", good.upper(), good + "0", " " + good, good + "\n", good.replace(".", ":")):
        assert not gate.valid(KEY, junk, NOW), junk


def test_a_music_pass_is_not_a_video_pass():
    exp = NOW + 3600
    music = music_sign(HOME_TOKEN, exp)
    video = gate.sign(KEY, exp)
    assert music != video                                         # same home token, same expiry: still different passes
    assert not gate.valid(KEY, music, NOW)
    # and the other way round: the video pass fails the music gate's check (its algorithm, written out here)
    assert music_sign(HOME_TOKEN, exp) != video.replace("video", "music")
    assert gate.gate_key(HOME_TOKEN) != hmac.new(hashlib.sha256(HOME_TOKEN.encode()).hexdigest().encode(), b"music-gate-v1",
                                                 hashlib.sha256).digest()


def test_unicode_digits_in_a_pass_are_refused_not_a_500(stack):
    # int() accepts other scripts' digits and hmac.compare_digest() raises on non-ASCII text: both must be turned away first
    assert not gate.valid(KEY, "٣" * 10 + "." + "a" * 32, NOW)
    for tok in ("٣" * 10 + "." + "a" * 32, "%00", "%2e%2e", "x" * 5000):
        assert stack.web.get(f"/t/{tok}/catalog.json").status_code == 403


def sample(rule, token):
    """A URL for a rule, with plausible values for its converters."""
    values = {"token": token, "id": fj.fid(1), "kind": "poster", "name": "2.vtt", "rest": "master.m3u8"}
    return re.sub(r"<(?:\w+:)?(\w+)>", lambda m: values[m.group(1)], rule.rule)


def passes(stack):
    exp = NOW + 3600
    return {
        "none": "nope",
        "expired": gate.sign(KEY, NOW - 1),
        "too long": gate.sign(KEY, NOW + gate.MAX_PASS_S + 100),
        "another gate key": gate.sign(gate.gate_key("other"), exp),
        "a music pass": music_sign(HOME_TOKEN, exp),
        "tampered": gate.sign(KEY, exp)[:-1] + "0" if not gate.sign(KEY, exp).endswith("0") else gate.sign(KEY, exp)[:-1] + "1",
    }


def test_every_route_refuses_every_bad_pass_and_never_touches_jellyfin(stack):
    rules = [r for r in stack.app.url_map.iter_rules() if r.rule != "/health"]
    assert sorted(r.rule for r in rules) == sorted([                  # a new route has to be added here, and so gets this test
        "/t/<token>/catalog.json", "/t/<token>/img/<id>/<kind>", "/t/<token>/v/<id>/direct", "/t/<token>/v/<id>/hls/<path:rest>",
        "/t/<token>/sub/<id>/<name>", "/t/<token>/progress"])
    before = len(stack.fake.requests)
    body = b'{"id": "%s", "pos": 10, "state": "progress"}' % fj.fid(1).encode()
    for rule in rules:
        for what, token in passes(stack).items():
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                r = stack.web.open(sample(rule, token), method=method, data=body if method == "POST" else None)
                assert r.status_code == 403, (rule.rule, method, what)
                assert r.get_json() == {"error": "forbidden"}
                assert r.headers["Access-Control-Allow-Origin"] == "*"   # so the player can read why
    assert len(stack.fake.requests) == before                        # not one upstream call for a refused request


def test_the_valid_pass_opens_every_route_family(stack):
    """The control for the test above: with a good pass the same URLs are NOT 403 (so that test isn't passing by accident)."""
    for rule in [r for r in stack.app.url_map.iter_rules() if r.rule != "/health"]:
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            body = b'{"id": "%s", "pos": 10, "state": "start"}' % fj.fid(1).encode()
            r = stack.web.open(sample(rule, stack.tok), method=method, data=body if method == "POST" else None)
            assert r.status_code != 403, (rule.rule, method)


@pytest.mark.parametrize("path", ["/", "/t", "/t/", "/music/x", "/static/x", "//health", "/health/x", "/admin", "/t/" + "a" * 40])
def test_nothing_else_exists(stack, path):
    r = stack.web.get(path)
    assert r.status_code in (403, 404)
    assert "Location" not in r.headers                                # never a redirect


def test_unknown_path_under_a_bad_pass_is_403_and_under_a_good_pass_404(stack):
    assert stack.web.get("/t/nope/anything/at/all").status_code == 403
    assert stack.get("/anything/at/all").status_code == 404


def test_health_needs_no_pass_and_says_what_it_knows(stack):
    r = stack.web.get("/health")
    assert r.status_code == 200 and r.get_json() == {"ok": True, "ready": True, "jellyfin": True, "items": 11}


def test_cors_preflight_needs_no_pass_and_asks_nothing_of_jellyfin(stack):
    before = len(stack.fake.requests)
    r = stack.web.open(f"/t/{stack.tok}/progress", method="OPTIONS")
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    assert "POST" in r.headers["Access-Control-Allow-Methods"] and "Range" in r.headers["Access-Control-Allow-Headers"]
    assert stack.web.open("/t/whatever/catalog.json", method="OPTIONS").status_code == 204
    assert len(stack.fake.requests) == before


def test_security_headers_everywhere(stack):
    for r in (stack.get("/catalog.json"), stack.web.get("/t/nope/catalog.json"), stack.web.get("/health"),
              stack.get(f"/v/{fj.fid(2)}/direct")):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        assert "Set-Cookie" not in r.headers
    assert "Content-Range" in stack.get("/catalog.json").headers.get("Access-Control-Expose-Headers", "")


def test_a_pass_never_appears_in_a_log(stack, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    stack.get("/catalog.json")
    stack.get(f"/v/{fj.fid(1)}/direct", headers={"Range": "bytes=0-9"})
    stack.get(f"/v/{fj.fid(99)}/direct")
    stack.web.get("/t/nope/catalog.json")
    assert stack.tok not in caplog.text and fj.KEY not in caplog.text
