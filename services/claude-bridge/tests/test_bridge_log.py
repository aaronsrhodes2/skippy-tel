"""The bridge's access log, under a supervisor that appends it to one file for months. The home reporter long-polls a
run every few seconds and probes /health constantly, so successful polls were nearly every line and buried the ones
that matter. Those (2xx GET /health, /workshop/run/*, /workshop/runs) are skipped; everything else is still logged."""
import pytest

import bridge
from bridge_testkit import finish

BODY = {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi"}


def lines(capsys):
    return [line for line in capsys.readouterr().err.splitlines() if line.startswith("[bridge]")]


def test_successful_polls_are_not_logged(door, shop, capsys):
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    capsys.readouterr()
    assert door("GET", "/health", token=None)[0] == 200
    assert door("GET", "/workshop/run/aaaaaaaa?after=0&wait=1")[0] == 200
    assert door("GET", "/workshop/run/aaaaaaaa?after=4")[0] == 200
    assert door("GET", "/workshop/runs")[0] == 200
    assert lines(capsys) == []


def test_everything_that_is_not_a_routine_success_is_still_logged(door, shop, capsys):
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    capsys.readouterr()
    assert door("GET", "/workshop/run/zzzzzzzz")[0] == 404                      # an unknown task
    assert door("GET", "/workshop/runs", token=None)[0] == 401                  # a refusal
    assert door("GET", "/workshop/run/aaaaaaaa", token="wrong")[0] == 401
    assert door("GET", "/nope")[0] == 404
    assert door("POST", "/workshop", {"task": "bad", "conv": "x", "message": "hi"})[0] == 400
    assert door("POST", "/workshop", {**BODY, "attach": True, "task": "bbbbbbbb"})[0] == 404
    assert door("POST", "/workshop", BODY)[0] == 200                            # an ordinary 200, but a POST
    assert door("POST", "/workshop/cancel/aaaaaaaa", {})[0] == 200
    got = lines(capsys)
    assert len(got) == 8, got
    for fragment, line in zip(('"GET /workshop/run/zzzzzzzz HTTP/1.1" 404', '"GET /workshop/runs HTTP/1.1" 401',
                               '"GET /workshop/run/aaaaaaaa HTTP/1.1" 401', '"GET /nope HTTP/1.1" 404',
                               '"POST /workshop HTTP/1.1" 400', '"POST /workshop HTTP/1.1" 404',
                               '"POST /workshop HTTP/1.1" 200', '"POST /workshop/cancel/aaaaaaaa HTTP/1.1" 200'), got):
        assert line.startswith("[bridge] 127.0.0.1 ") and fragment in line, (fragment, line)


def handler():
    h = object.__new__(bridge.Handler)
    h.client_address = ("127.0.0.1", 5555)
    return h


@pytest.mark.parametrize("request_line, status", [
    ("GET /health HTTP/1.1", "200"),
    ("GET /health HTTP/1.1", "204"),
    ("GET /health?probe=1 HTTP/1.1", "200"),
    ("GET /workshop/runs HTTP/1.1", "200"),
    ("GET /workshop/run/aaaaaaaa HTTP/1.1", "200"),
    ("GET /workshop/run/aaaaaaaa?after=3&wait=25 HTTP/1.1", "200"),
    ("GET /workshop/run/aaaaaaaa HTTP/1.1", "299"),
])
def test_a_2xx_get_of_a_polled_path_is_skipped(capsys, request_line, status):
    handler().log_message('"%s" %s %s', request_line, status, "-")
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("request_line, status", [
    ("GET /health HTTP/1.1", "500"),
    ("GET /health HTTP/1.1", "503"),
    ("GET /health HTTP/1.1", "404"),
    ("GET /health HTTP/1.1", "301"),
    ("GET /workshop/runs HTTP/1.1", "401"),
    ("GET /workshop/runs HTTP/1.1", "503"),
    ("GET /workshop/run/aaaaaaaa?after=3 HTTP/1.1", "404"),
    ("GET /workshop/run/aaaaaaaa HTTP/1.1", "400"),
    ("POST /workshop HTTP/1.1", "200"),
    ("POST /health HTTP/1.1", "200"),
    ("POST /workshop/cancel/aaaaaaaa HTTP/1.1", "200"),
    ("POST /solid-gold HTTP/1.1", "200"),
    ("GET /workshop/runsX HTTP/1.1", "200"),
    ("GET /workshop/run HTTP/1.1", "200"),
    ("GET /healthz HTTP/1.1", "200"),
    ("GET / HTTP/1.1", "200"),
    ("garbage", "200"),
    ("GET /health HTTP/1.1", "abc"),
])
def test_anything_else_is_logged_in_the_same_format_as_before(capsys, request_line, status):
    handler().log_message('"%s" %s %s', request_line, status, "-")
    assert capsys.readouterr().err == f'[bridge] 127.0.0.1 "{request_line}" {status} -\n'


def test_the_stdlibs_other_log_lines_are_never_skipped(capsys):
    handler().log_message("code %d, message %s", 400, "Bad request syntax ('GET /health')")
    handler().log_message("Request timed out: %r", TimeoutError("timed out"))
    err = capsys.readouterr().err
    assert "code 400, message Bad request syntax" in err and "Request timed out" in err
