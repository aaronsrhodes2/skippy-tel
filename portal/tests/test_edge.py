"""The direct front door, the portal's half (edge.py): https://home.makerportal.live straight to skippy-pc's gates, the Tailscale Funnel staying
as the fallback. The door is looked at from Cloud Run, never waited for, and offered to a browser only as the pass's extra field "direct".

What is checked here, with no network (the probe and the clock are injected; the one real HTTP probe talks to a server on this machine):
the host is a plain name under makerportal.live or .studio or the door is off for good; alive() answers at once and refreshes in the background
(stale-while-revalidate, one refresh at a time, a hung one given up on); every gate-pass JSON carries "direct" while the door is up and null
otherwise, built from EDGE_HOST alone; and the strict *.ts.net checks on "url" still refuse everything they refused before."""
import hashlib
import http.server
import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import audiobooks as books_mod  # noqa: E402
import config  # noqa: E402
import edge as edge_mod  # noqa: E402
import home as home_mod  # noqa: E402
import player as player_mod  # noqa: E402
import popup_in as popup_in_mod  # noqa: E402
import video as video_mod  # noqa: E402
import voice as voice_mod  # noqa: E402
from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402

import app as portal  # noqa: E402

HOST = "home.makerportal.live"
TOKEN = "the-home-token"
ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def until(cond, timeout=5.0):
    """Real time, for the threads: wait until `cond()` is true (a failure here is a test that would otherwise hang)."""
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "gave up waiting"
        time.sleep(0.002)
    return True


class Probe:
    """The probe an Edge is handed: answers from a script (the last answer repeats), counts its calls and how many ran at once, and call
    number n can be held up until holds[n] is set."""

    def __init__(self, *answers, delay=0.0):
        self.answers = list(answers) or [(True, "")]
        self.calls, self.urls, self.active, self.most = 0, [], 0, 0
        self.holds: dict[int, threading.Event] = {}
        self.delay = delay
        self._lock = threading.Lock()

    def __call__(self, url, timeout):
        with self._lock:
            n = self.calls
            self.calls += 1
            self.urls.append((url, timeout))
            self.active += 1
            self.most = max(self.most, self.active)
            answer, hold = self.answers[min(n, len(self.answers) - 1)], self.holds.get(n)
        try:
            if hold is not None and not hold.wait(10):
                raise AssertionError("the test never let the probe go")
            if self.delay:
                time.sleep(self.delay)
            if isinstance(answer, Exception):
                raise answer
            return answer
        finally:
            with self._lock:
                self.active -= 1


def the_door(*answers, clock=None, **kw):
    """An Edge for HOST on a scripted probe, already asked once (so it has an answer)."""
    edge = edge_mod.Edge(HOST, probe=Probe(*answers), clock=clock or Clock(), **kw)
    edge.refresh()
    return edge


# ── the host: a plain name under our own domains, or the door is off for good ──────────────────────────────
@pytest.mark.parametrize("given, kept", [
    ("home.makerportal.live", "home.makerportal.live"),
    ("HOME.MakerPortal.live", "home.makerportal.live"),                 # a name is a name in any case
    (" home.makerportal.live\n", "home.makerportal.live"),              # an environment variable's stray whitespace
    ("a.b.makerportal.studio", "a.b.makerportal.studio"),
    ("xn--80ak6aa92e.makerportal.live", "xn--80ak6aa92e.makerportal.live"),
    ("a" * 63 + ".makerportal.live", "a" * 63 + ".makerportal.live"),
])
def test_a_plain_name_under_our_own_domains_is_the_door(given, kept):
    assert edge_mod.clean_host(given) == kept and edge_mod.Edge(given).host == kept


BAD_HOSTS = ["", "   ", None, 7, ["home.makerportal.live"],
             "https://home.makerportal.live", "http://home.makerportal.live", "//home.makerportal.live",           # a scheme
             "home.makerportal.live:443", "home.makerportal.live:47443",                                           # a port
             "home.makerportal.live/", "home.makerportal.live/music", "home.makerportal.live?x=1", "home.makerportal.live#x",   # a path, a query
             "user@home.makerportal.live", "user:pw@home.makerportal.live", "evil.com@home.makerportal.live",    # user info
             "makerportal.live", ".makerportal.live", "www.makerportal.live.", "home.makerportal.live.",          # the portal itself, a dot too many
             "makerportal.studio", "evilmakerportal.live", "home.makerportal.live.evil.com", "home.makerportal.liv",
             "home.example.com", "localhost", "127.0.0.1", "[::1]", "home.makerportal.dev",                       # not ours
             "home..makerportal.live", "-home.makerportal.live", "home-.makerportal.live", "ho me.makerportal.live",
             "home_1.makerportal.live", "höme.makerportal.live", "home.makerportal.live\nevil.com", "home.makerportal.live\x00",
             "a" * 64 + ".makerportal.live", ("a" * 60 + ".") * 5 + "makerportal.live"]


@pytest.mark.parametrize("bad", BAD_HOSTS)
def test_anything_else_switches_the_door_off_for_good_and_nothing_is_ever_probed(bad):
    assert edge_mod.clean_host(bad) == ""
    probe = Probe((True, ""))
    edge = edge_mod.Edge(bad, probe=probe)
    assert edge.host == "" and edge.alive() is False and edge.refresh() is False and edge.wait()
    assert all(edge.direct(p) is None for p in edge_mod.PREFIXES) and probe.calls == 0
    st = edge.status()
    assert st["host"] == "" and st["alive"] is False and st["ever_alive"] is False and "EDGE_HOST" in st["error"]
    assert "evil" not in st["error"]                                       # (the junk it was given is never echoed)


def test_an_empty_setting_says_it_is_not_set_and_a_bad_one_says_what_is_wrong():
    assert edge_mod.Edge("").status()["error"] == "EDGE_HOST is not set"
    assert edge_mod.Edge(None).status()["error"] == "EDGE_HOST is not set"
    assert "plain name" in edge_mod.Edge("https://home.makerportal.live").status()["error"]


def test_a_setting_that_is_set_but_wrong_is_said_once_in_the_log_and_an_unset_or_good_one_is_quiet():
    lines = []
    edge_mod.Edge("", log=lines.append)
    edge_mod.Edge(None, log=lines.append)
    edge_mod.Edge(HOST, log=lines.append)
    assert lines == []                                                     # (nothing to say: off on purpose, or fine)
    edge_mod.Edge("home.makerportal.live:443", log=lines.append)
    assert [json.loads(line) for line in lines] == [{"event": "edge_host_ignored", "why": "EDGE_HOST is not a plain name under makerportal.live or makerportal.studio"}]
    assert "443" not in lines[0]                                           # (the junk it was given is not echoed)


# ── alive(): the last answer at once, a refresh in the background ──────────────────────────────────────
def test_before_the_first_probe_finishes_it_says_no_at_once_and_a_request_never_waits():
    clock, probe = Clock(), Probe((True, ""))
    probe.holds[0] = release = threading.Event()
    edge = edge_mod.Edge(HOST, probe=probe, clock=clock)
    began = time.monotonic()
    assert edge.alive() is False and edge.direct("music") is None and edge.alive() is False
    assert time.monotonic() - began < 1.0                                  # (the probe is held up as long as the test likes: nothing waited for it)
    until(lambda: probe.calls == 1)
    assert edge.status() == {"host": HOST, "alive": False, "checked_s_ago": None, "error": "not checked yet", "ever_alive": False}
    release.set()
    assert edge.wait()
    assert edge.alive() is True and edge.direct("music") == f"https://{HOST}/music"
    assert probe.calls == 1


def test_the_probe_asks_https_ping_with_a_two_second_deadline():
    probe = Probe((True, ""))
    edge = edge_mod.Edge(HOST, probe=probe)
    assert edge.refresh() is True
    assert probe.urls == [(f"https://{HOST}/ping", 2.0)]


def test_a_good_answer_holds_a_minute_and_a_bad_one_half_that():
    clock, probe = Clock(), Probe((True, ""), (False, "refused"), (True, ""))
    edge = edge_mod.Edge(HOST, probe=probe, clock=clock)
    edge.refresh()
    assert (edge_mod.TTL_OK_S, edge_mod.TTL_BAD_S) == (60, 30)
    clock.now += 59
    assert edge.alive() is True and edge.wait() and probe.calls == 1        # still fresh: nobody asks
    clock.now += 1
    assert edge.alive() is True                                            # a minute old: the old answer, at once...
    until(lambda: probe.calls == 2) and edge.wait()                        # ...and a refresh in the background, which finds it down
    assert edge.alive() is False and probe.calls == 2
    clock.now += 29
    assert edge.alive() is False and edge.wait() and probe.calls == 2
    clock.now += 1
    assert edge.alive() is False                                           # half a minute on: the dead door is asked again...
    until(lambda: probe.calls == 3) and edge.wait()
    assert edge.alive() is True                                            # ...and it is back


def test_the_ttls_are_the_callers_to_set():
    clock, probe = Clock(), Probe((True, ""))
    edge = edge_mod.Edge(HOST, probe=probe, clock=clock, ttl_ok=5, ttl_bad=1)
    edge.refresh()
    clock.now += 4
    edge.alive()
    edge.wait()
    assert probe.calls == 1
    clock.now += 1
    edge.alive()
    until(lambda: probe.calls == 2)
    edge.wait()


def test_a_stale_answer_is_returned_at_once_while_exactly_one_refresh_runs():
    clock, probe = Clock(), Probe((True, ""), (False, "connection refused"))
    edge = edge_mod.Edge(HOST, probe=probe, clock=clock)
    edge.refresh()
    probe.holds[1] = release = threading.Event()
    clock.now += edge_mod.TTL_OK_S
    assert edge.alive() is True
    until(lambda: probe.calls == 2)
    for _ in range(25):                                                    # the refresh is still out: asking again starts nothing, waits for nothing
        assert edge.alive() is True and edge.direct("video") == f"https://{HOST}/video"
    assert probe.calls == 2 and probe.most == 1
    release.set()
    assert edge.wait()
    assert edge.alive() is False and edge.direct("video") is None
    assert edge.status()["error"] == "connection refused"


def test_a_probe_that_blows_up_is_a_dead_door_not_a_failed_request():
    edge = edge_mod.Edge(HOST, probe=Probe(RuntimeError("boom")))
    assert edge.refresh() is False
    st = edge.status()
    assert st["alive"] is False and st["error"] == "RuntimeError" and st["ever_alive"] is False
    assert edge.alive() is False and edge.direct("music") is None


@pytest.mark.parametrize("answer, ok, why", [
    (True, True, ""), (False, False, "no answer"), ((True,), True, ""), ((False,), False, "no answer"), ((), False, "no answer"),
    ((False, "HTTP 502"), False, "HTTP 502"), ((True, "ignored"), True, ""), (None, False, "no answer"), ((False, "x" * 500), False, "x" * 160),
])
def test_a_probe_may_answer_with_a_bool_or_a_pair(answer, ok, why):
    edge = edge_mod.Edge(HOST, probe=lambda url, timeout: answer)
    assert edge.refresh() is ok
    assert edge.status()["alive"] is ok and edge.status()["error"] == why


def test_a_probe_gets_what_it_takes_the_url_and_deadline_or_just_the_url_or_nothing():
    seen, ping = [], f"https://{HOST}/ping"
    both = lambda url, timeout: seen.append(("both", url, timeout)) or (True, "")      # noqa: E731
    url_only = lambda url: seen.append(("url", url)) or (True, "")                       # noqa: E731
    neither = lambda: seen.append(("none",)) or (True, "")                               # noqa: E731
    rest = lambda *args: seen.append(("rest", args)) or (True, "")                       # noqa: E731
    for probe in (both, url_only, neither, rest):
        assert edge_mod.Edge(HOST, probe=probe).refresh() is True
    assert seen == [("both", ping, 2.0), ("url", ping), ("none",), ("rest", (ping, 2.0))]


def test_a_refresh_that_hangs_is_given_up_on_and_its_late_answer_is_dropped():
    clock, probe = Clock(), Probe((False, "late"), (True, ""))
    probe.holds[0] = late = threading.Event()
    edge = edge_mod.Edge(HOST, probe=probe, clock=clock, hung_s=15)
    assert edge.alive() is False
    until(lambda: probe.calls == 1)
    clock.now += 14
    edge.alive()
    assert probe.calls == 1                                                # still within its time: nothing else is started
    clock.now += 1
    edge.alive()                                                           # 15 s and no answer: this one is abandoned, a new refresh asks
    until(lambda: probe.calls == 2)
    assert edge.wait()
    assert edge.alive() is True
    late.set()
    for t in [t for t in threading.enumerate() if t.name == "edge-probe"]:
        t.join(5)
    assert edge.alive() is True and edge.status()["error"] == "" and edge.status()["ever_alive"] is True     # (the old word did not overwrite the new)


def test_a_clock_that_steps_back_does_not_freeze_the_answer():
    clock, probe = Clock(), Probe((True, ""), (True, ""))
    edge = edge_mod.Edge(HOST, probe=probe, clock=clock)
    edge.refresh()
    clock.now -= 3600
    edge.alive()
    until(lambda: probe.calls == 2)
    edge.wait()


def test_status_tells_the_doctor_what_it_needs_and_ever_alive_does_not_forget():
    clock, probe = Clock(), Probe((True, ""), (False, "the name does not resolve"))
    edge = edge_mod.Edge(HOST, probe=probe, clock=clock)
    edge.refresh()
    clock.now += 12
    st = edge.status()
    assert st == {"host": HOST, "alive": True, "checked_s_ago": 12.0, "error": "", "ever_alive": True}
    edge.refresh()
    st = edge.status()
    assert st == {"host": HOST, "alive": False, "checked_s_ago": 0.0, "error": "the name does not resolve", "ever_alive": True}
    assert set(st) == {"host", "alive", "checked_s_ago", "error", "ever_alive"}
    json.dumps(st)                                                         # it goes straight into a JSON page


def test_status_never_starts_a_probe():
    probe = Probe((True, ""))
    edge = edge_mod.Edge(HOST, probe=probe, clock=Clock())
    for _ in range(5):
        edge.status()
    assert edge.wait() and probe.calls == 0


def test_the_log_says_when_the_door_changes_and_only_then():
    lines = []
    edge = edge_mod.Edge(HOST, probe=Probe((True, ""), (True, ""), (False, "refused"), (False, "refused"), (True, "")), log=lines.append)
    for _ in range(5):
        edge.refresh()
    got = [json.loads(line) for line in lines]
    assert [(g["event"], g["alive"], g["why"]) for g in got] == [("edge_door", True, ""), ("edge_door", False, "refused"), ("edge_door", True, "")]
    assert all(g["host"] == HOST for g in got)


def test_a_logger_that_fails_costs_nothing():
    def broken(line):
        raise OSError("no stdout")
    edge = edge_mod.Edge(HOST, probe=Probe((True, "")), log=broken)
    assert edge.refresh() is True and edge.alive() is True


# ── which gates have an address ───────────────────────────────────────────────────────────────────────
def test_the_five_gates_have_an_address_and_nothing_else_does():
    edge = the_door((True, ""))
    assert edge_mod.PREFIXES == ("music", "video", "books", "voice", "in")
    assert {p: edge.direct(p) for p in edge_mod.PREFIXES} == {p: f"https://{HOST}/{p}" for p in ("music", "video", "books", "voice", "in")}
    for other in ("", "ping", "health", "admin", "Music", "music/", "/music", "music/../x", "music?x", "m", "inn", None, 5, ["music"]):
        assert edge.direct(other) is None, other


def test_a_prefix_that_is_not_a_gate_does_not_even_start_a_probe():
    probe = Probe((True, ""))
    edge = edge_mod.Edge(HOST, probe=probe)
    assert edge.direct("admin") is None and edge.wait() and probe.calls == 0


def test_many_threads_at_once_never_run_two_probes_at_once_and_nothing_fails():
    probe = Probe((True, ""), delay=0.001)
    edge = edge_mod.Edge(HOST, probe=probe, ttl_ok=0.003, ttl_bad=0.003)
    errors = []

    def hammer():
        try:
            for i in range(200):
                edge.alive()
                edge.direct(edge_mod.PREFIXES[i % 5])
                edge.status()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=hammer) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and not any(t.is_alive() for t in threads)
    assert edge.wait() and probe.most == 1 and probe.calls >= 2
    assert edge.status()["ever_alive"] is True


# ── the real probe, against a server on this machine ───────────────────────────────────────────────────
class _Door(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).hits.append(self.path)
        code, headers, body = {
            "/ping": (200, {}, b"ok"), "/newline": (200, {}, b"ok\n"), "/html": (200, {}, b"<html>ok</html>"), "/empty": (200, {}, b""),
            "/long": (200, {}, b"ok" + b"x" * 500), "/missing": (404, {}, b"not here"), "/bad": (502, {}, b"bad gateway"),
            "/moved": (302, {"Location": "/ping"}, b""), "/away": (301, {"Location": "http://127.0.0.1:1/ping"}, b""),
            "/slow": (200, {}, b"ok"),
        }.get(self.path, (404, {}, b""))
        if self.path == "/slow":
            time.sleep(1.5)
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:                                                    # (a probe that gave up before the answer came)
            pass


@pytest.fixture
def local_door():
    _Door.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Door)
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("path, ok, why", [
    ("/ping", True, ""), ("/newline", True, ""),                            # 200 and "ok": the door
    ("/html", False, "HTTP 200"), ("/empty", False, "HTTP 200"), ("/long", False, "HTTP 200"),         # a 200 that is not "ok" is a parked page, not the door
    ("/missing", False, "HTTP 404"), ("/bad", False, "HTTP 502"),
    ("/moved", False, "HTTP 302"), ("/away", False, "HTTP 301"),           # a redirect is an answer that is not "ok"
])
def test_only_a_200_that_says_ok_is_a_live_door(local_door, path, ok, why):
    got_ok, got_why = edge_mod.http_probe(local_door + path, 2.0)
    assert got_ok is ok and got_why.startswith(why)


def test_a_redirect_is_never_followed(local_door):
    edge_mod.http_probe(local_door + "/moved", 2.0)
    assert _Door.hits == ["/moved"]                                         # (it did not go on to /ping, let alone to another host)


def test_a_slow_door_is_a_dead_one_and_nothing_hangs(local_door):
    began = time.monotonic()
    assert edge_mod.http_probe(local_door + "/slow", 0.3) == (False, "no answer in time")
    assert time.monotonic() - began < 1.4


def test_a_closed_port_is_connection_refused():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ok, why = edge_mod.http_probe(f"http://127.0.0.1:{port}/ping", 0.5)
    assert ok is False and why


def test_the_default_probe_is_the_real_one_and_it_never_raises():
    assert edge_mod.Edge(HOST)._probe is edge_mod.http_probe
    for junk in ("not a url", "", "https://", None, 7):                     # (no network: none of these gets as far as a socket)
        assert edge_mod.http_probe(junk, 0.5)[0] is False, junk


@pytest.mark.parametrize("exc, said", [
    (TimeoutError("timed out"), "no answer in time"),
    (socket.gaierror(11001, "getaddrinfo failed"), "the name does not resolve"),
    (ConnectionRefusedError(), "connection refused"),
    (ConnectionResetError(), "the connection dropped"),
    (OSError(113, "No route to host"), "network error: No route to host"),
    (RuntimeError(), "RuntimeError"),
])
def test_a_failure_is_said_in_a_few_plain_words(exc, said):
    import urllib.error
    assert edge_mod._why(exc) == said
    assert edge_mod._why(urllib.error.URLError(exc)) == said                # (urllib wraps the real cause)


def test_a_certificate_the_browser_would_refuse_is_said_so():
    import ssl
    import urllib.error
    assert edge_mod._why(urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))) == "its certificate is not accepted"
    assert edge_mod._why(urllib.error.URLError(ssl.SSLError(1, "handshake failure"))) == "the secure connection failed"


# ── every gate's pass carries "direct" ─────────────────────────────────────────────────────────────────
FUNNEL = {                                     # what skippy-pc reports for each gate: the Tailscale Funnel addresses, as before
    "music_gate": "https://skippy-pc.tailab2cc7.ts.net",
    "video_gate": "https://skippy-pc.tailab2cc7.ts.net:8443",
    "book_gate": "https://skippy-pc.tailab2cc7.ts.net:10000/books",
    "voice_gate": "https://skippy-pc.tailab2cc7.ts.net:10000",
    "popup_gate": "https://skippy-pc.tailab2cc7.ts.net:10000/in",
}
GATES = {                                      # name -> (capability, edge prefix, how the pass is asked for)
    "music": ("music_gate", "music", lambda h, c, e: player_mod.Player(h, clock=c, edge=e).gate()),
    "video": ("video_gate", "video", lambda h, c, e: video_mod.Video(h, clock=c, edge=e).gate()),
    "video-friend": ("video_gate", "video", lambda h, c, e: video_mod.Video(h, clock=c, edge=e).gate(read_only=True)),
    "books": ("book_gate", "books", lambda h, c, e: books_mod.Books(h, player_mod.Player(h, clock=c), clock=c, edge=e).gate()),
    "voice-listen": ("voice_gate", "voice", lambda h, c, e: voice_mod.Voice(h, clock=c, edge=e).gate()),
    "voice-speak": ("voice_gate", "voice", lambda h, c, e: voice_mod.Voice(h, clock=c, edge=e).gate(speak=True)),
    "voice-skippy": ("voice_gate", "voice", lambda h, c, e: voice_mod.Voice(h, clock=c, edge=e).gate(voice="skippy")),
    "popup-in": ("popup_gate", "in", lambda h, c, e: popup_in_mod.PopupIn(h, clock=c, edge=e).grant(ADMIN, True, 1000)),
}


def paired_home(clock, caps=None):
    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    home = home_mod.Home(vm, clock=clock)
    home.accept({"capabilities": caps if caps is not None else {n: {"ready": True, "url": u} for n, u in FUNNEL.items()}})
    return home


@pytest.mark.parametrize("name", GATES)
def test_each_pass_names_the_direct_door_while_it_is_up_and_keeps_the_funnel_address(name):
    cap, prefix, make = GATES[name]
    clock = Clock()
    home, door = paired_home(clock), the_door((True, ""), clock=clock)
    got = make(home, clock, door)
    assert got["ok"] is True and got["direct"] == f"https://{HOST}/{prefix}" and isinstance(got["direct"], str)
    assert got["url"] == FUNNEL[cap]                                        # "url" stays the Funnel address, exactly as before
    assert isinstance(got["pass"], str) and got["pass"]
    plain = make(home, clock, None)                                         # and nothing else about the pass changed with the door
    assert set(got) == set(plain) and plain["direct"] is None
    if name != "popup-in":                                                  # (a pop-up's pass has a fresh file id every time)
        assert {k: v for k, v in got.items() if k != "direct"} == {k: v for k, v in plain.items() if k != "direct"}


@pytest.mark.parametrize("name", GATES)
def test_each_pass_says_null_when_the_door_is_down_off_or_not_yet_known(name):
    cap, prefix, make = GATES[name]
    clock = Clock()
    home = paired_home(clock)
    down = the_door((False, "connection refused"), clock=clock)
    assert make(home, clock, down)["direct"] is None and "direct" in make(home, clock, down)         # the key is there, and null
    assert make(home, clock, None)["direct"] is None                                                 # no door configured
    assert make(home, clock, edge_mod.Edge("", probe=Probe((True, ""))))["direct"] is None          # EDGE_HOST empty
    assert make(home, clock, edge_mod.Edge("https://home.makerportal.live", probe=Probe((True, ""))))["direct"] is None   # a bad one
    unknown = edge_mod.Edge(HOST, probe=Probe((True, "")), clock=clock)
    unknown._probe.holds[0] = release = threading.Event()
    assert make(home, clock, unknown)["direct"] is None                                              # not probed yet: the pass does not wait
    release.set()
    assert unknown.wait()
    assert make(home, clock, unknown)["direct"] == f"https://{HOST}/{prefix}"                       # the next one has it


BAD_GATE_URLS = {                              # everything the strict *.ts.net checks refused before, and the door's own address too
    "music_gate": ["https://evil.example.com", "http://skippy-pc.tailab2cc7.ts.net", "https://skippy-pc.tailab2cc7.ts.net.evil.example.com",
                   f"https://{HOST}", f"https://{HOST}/music", "http://127.0.0.1:8766", "", 7, None],
    "video_gate": ["https://evil.example.com", "http://skippy-pc.tailab2cc7.ts.net:8443", "https://skippy-pc.tailab2cc7.ts.net:8080",
                   "https://skippy-pc.tailab2cc7.ts.net.evil.example.com", "https://user@skippy-pc.tailab2cc7.ts.net:8443",
                   "https://skippy-pc.tailab2cc7.ts.net:8443/x", "https://skippy-pc.tailab2cc7.ts.net:8443\n", f"https://{HOST}", f"https://{HOST}/video", 7],
    "book_gate": ["https://evil.example.com/books", "http://skippy-pc.tailab2cc7.ts.net:10000/books", "https://skippy-pc.tailab2cc7.ts.net:10000",
                  "https://skippy-pc.tailab2cc7.ts.net:10000/other", f"https://{HOST}/books", f"https://{HOST}", 7],
    "voice_gate": ["https://evil.example.com:10000", "http://skippy-pc.tailab2cc7.ts.net:10000", "https://skippy-pc.tailab2cc7.ts.net:8443",
                   "https://skippy-pc.tailab2cc7.ts.net.evil.example.com:10000", f"https://{HOST}", f"https://{HOST}/voice", 7],
    "popup_gate": ["https://evil.example.com:10000/in", "http://skippy-pc.tailab2cc7.ts.net:10000/in", "https://skippy-pc.tailab2cc7.ts.net:10000",
                   "https://skippy-pc.tailab2cc7.ts.net:10000/in/", f"https://{HOST}/in", f"https://{HOST}", 7],
}


@pytest.mark.parametrize("name", GATES)
def test_the_strict_gate_address_checks_still_refuse_what_they_refused_before_door_or_no_door(name):
    cap, prefix, make = GATES[name]
    clock = Clock()
    door = the_door((True, ""), clock=clock)
    for bad in BAD_GATE_URLS[cap]:
        home = paired_home(clock, {**{n: {"ready": True, "url": u} for n, u in FUNNEL.items()}, cap: {"ready": True, "url": bad}})
        for edge in (door, None):
            got = make(home, clock, edge)
            assert got["ok"] is False and "isn't answering" in got["why"], (bad, got)
            assert "pass" not in got and "direct" not in got and "url" not in got, (bad, got)
    ready_off = paired_home(clock, {**{n: {"ready": True, "url": u} for n, u in FUNNEL.items()}, cap: {"ready": False, "url": FUNNEL[cap]}})
    assert make(ready_off, clock, door)["ok"] is False                      # a gate that is not ready gets no pass, door or no door


@pytest.mark.parametrize("name", GATES)
def test_the_direct_address_comes_from_edge_host_alone_never_from_a_report(name):
    cap, prefix, make = GATES[name]
    clock = Clock()
    sly = {n: {"ready": True, "url": u, "direct": "https://evil.example.com/x", "edge": "https://evil.example.com", "host": "evil.example.com",
               "direct_url": "https://evil.example.com", "prefix": "../evil"} for n, u in FUNNEL.items()}
    home = paired_home(clock, sly)
    assert make(home, clock, None)["direct"] is None
    got = make(home, clock, the_door((True, ""), clock=clock))
    assert got["direct"] == f"https://{HOST}/{prefix}" and "evil" not in json.dumps(got)


def test_a_guests_pass_for_a_pop_ups_big_file_names_the_door_but_still_spends_the_days_bytes():
    clock = Clock()
    gate = popup_in_mod.PopupIn(paired_home(clock), clock=clock, edge=the_door((True, ""), clock=clock))
    mb = 50 * 1024 * 1024
    for n in range(4):                                                      # 200 MB is the guest's day
        got = gate.grant("friend@example.com", False, mb)
        assert got["ok"] is True and got["direct"] == f"https://{HOST}/in", n
    assert gate.left_today("friend@example.com", False) == 0
    refused = gate.grant("friend@example.com", False, mb)
    assert refused["ok"] is False and "direct" not in refused and "pass" not in refused


# ── create_app builds it from the setting and hands it to everything ─────────────────────────────────────
def test_create_app_builds_one_door_from_edge_host_and_gives_it_to_every_gate_and_the_doctor(monkeypatch):
    asked = []
    monkeypatch.setattr(edge_mod, "http_probe", lambda url, timeout: asked.append(url) or (True, ""))
    app = portal.create_app(dict(SETTINGS, EDGE_HOST=HOST), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    door = app.extensions["edge"]
    assert isinstance(door, edge_mod.Edge) and door.host == HOST
    assert [app.extensions[k].edge is door for k in ("player", "books", "video", "voice", "popup_in")] == [True] * 5
    assert app.extensions["doctor"].edge is door
    assert asked == [] and door.wait()                                      # nothing is probed until something asks (and then in the background)


@pytest.mark.parametrize("setting", [None, "", "https://home.makerportal.live", "home.example.com"])
def test_without_a_good_edge_host_every_pass_says_direct_null_and_nothing_is_ever_probed(monkeypatch, setting):
    def never(url, timeout):
        raise AssertionError("the door is off: nothing may be probed")
    monkeypatch.setattr(edge_mod, "http_probe", never)
    settings = dict(SETTINGS) if setting is None else dict(SETTINGS, EDGE_HOST=setting)
    app = portal.create_app(settings, vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    assert app.extensions["edge"].host == ""
    c = app.test_client()
    login(c, ADMIN)
    for url in ("/music/gate", "/video/gate", "/audiobooks/gate", "/chat/voice/gate"):
        got = c.get(url).get_json()
        assert got["ok"] is True and "direct" in got and got["direct"] is None, url
    assert app.extensions["edge"].wait()


def test_the_gate_routes_hand_the_browser_the_direct_address_once_the_door_has_been_seen_up(monkeypatch):
    monkeypatch.setattr(edge_mod, "http_probe", lambda url, timeout: (True, ""))
    app = portal.create_app(dict(SETTINGS, EDGE_HOST=HOST), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    door = app.extensions["edge"]
    c = app.test_client()
    login(c, ADMIN)
    assert c.get("/music/gate").get_json()["direct"] is None                # the very first look: not probed yet, and the request did not wait
    assert door.wait() and door.status()["ever_alive"] is True
    for url, prefix, cap in (("/music/gate", "music", "music_gate"), ("/video/gate", "video", "video_gate"),
                             ("/audiobooks/gate", "books", "book_gate"), ("/chat/voice/gate", "voice", "voice_gate")):
        resp = c.get(url)
        got = resp.get_json()
        assert got["ok"] is True and got["direct"] == f"https://{HOST}/{prefix}" and got["url"] == FUNNEL[cap], url
        assert "no-store" in resp.headers["Cache-Control"]                  # still a credential: never from a cache


def test_the_door_going_down_takes_the_address_back_on_the_next_pass(monkeypatch):
    answers = [(True, "")]
    monkeypatch.setattr(edge_mod, "http_probe", lambda url, timeout: answers[-1])
    clock = Clock()                                                         # (the door's own: home has its own, and goes quiet after a minute)
    app = portal.create_app(dict(SETTINGS, EDGE_HOST=HOST), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    door = app.extensions["edge"]
    door.clock = clock
    door.refresh()
    c = app.test_client()
    login(c, ADMIN)
    assert c.get("/video/gate").get_json()["direct"] == f"https://{HOST}/video"
    answers.append((False, "the name does not resolve"))
    clock.now += edge_mod.TTL_OK_S
    assert c.get("/video/gate").get_json()["direct"] == f"https://{HOST}/video"       # the old answer, at once (the page checks /health itself)
    assert door.wait()
    assert c.get("/video/gate").get_json()["direct"] is None                           # the refresh found it down


def test_the_starters_calls_make_the_doctor_look_at_the_door_and_a_row_appears_once_it_has_been_up(monkeypatch):
    """create_app -> the doctor -> the Edge, through the real route the starter on skippy-pc calls every ~10 s."""
    probe = Probe((True, ""))
    probe.holds[0] = release = threading.Event()
    monkeypatch.setattr(edge_mod, "http_probe", probe)
    app = portal.create_app(dict(SETTINGS, EDGE_HOST=HOST), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    door, doctor = app.extensions["edge"], app.extensions["doctor"]
    c = app.test_client()
    call = lambda: c.post("/home/starter", headers={"Authorization": f"Bearer {TOKEN}"}, json={"status": {"docker": True, "bridge": True}})   # noqa: E731
    assert call().status_code == 200                                        # the first look starts the probe in the background (held up here)...
    until(lambda: probe.calls == 1)
    assert not [r for r in doctor.view()["rows"] if r["name"] == "direct-door"]       # ...and says nothing until the door has been seen up
    release.set()
    assert door.wait() and door.status()["ever_alive"] is True
    assert call().status_code == 200
    row = next(r for r in doctor.view()["rows"] if r["name"] == "direct-door")
    assert row["ok"] is True and row["label"] == "Direct door"


def test_a_friend_with_the_video_grant_is_offered_the_door_too(monkeypatch):
    from test_app import _add_friend
    monkeypatch.setattr(edge_mod, "http_probe", lambda url, timeout: (True, ""))
    app = portal.create_app(dict(SETTINGS, EDGE_HOST=HOST), vm=FakeVm(), skippy=FakeSkippy(), home=paired_home(Clock()))
    c = app.test_client()
    _add_friend(c, "pal@example.com", ["video"])
    login(c, "pal@example.com")
    c.get("/video/gate")
    assert app.extensions["edge"].wait()
    got = c.get("/video/gate").get_json()
    assert got["ok"] is True and got["direct"] == f"https://{HOST}/video"
    assert c.get("/music/gate").status_code == 403                          # the door changed nobody's grants


# ── the setting ─────────────────────────────────────────────────────────────────────────────────
def test_edge_host_is_a_setting_that_defaults_to_off(monkeypatch):
    for key, value in {"ALLOWED_EMAILS": ADMIN, "FLASK_SECRET_KEY": "k", "GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s", "GCP_PROJECT": "p"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("EDGE_HOST", raising=False)
    assert config.load()["EDGE_HOST"] == ""
    monkeypatch.setenv("EDGE_HOST", " home.makerportal.live ")
    assert config.load()["EDGE_HOST"] == HOST
    monkeypatch.setenv("EDGE_HOST", "unset")                                # the placeholder the bootstrap stores for a skipped optional
    assert config.load()["EDGE_HOST"] == ""


def test_the_deployers_edge_host_is_one_the_portal_accepts():
    deploy = json.loads((ROOT / "core" / "cd" / "deploy.json").read_text(encoding="utf-8"))
    given = deploy["portal"]["env"].get("EDGE_HOST")
    if given is not None:                                                   # (the door is switched on by this line; a typo would silently leave it off)
        assert edge_mod.clean_host(given) == given and given != ""


def test_the_prefixes_are_the_ones_the_edges_caddyfile_routes_to_the_gates():
    """The path layout is written twice, here and in edge/Caddyfile (handle_path strips the prefix, then the gate sees /t/<pass>/...): a drift
    on either side is a pass that sends a browser to a 404, and the page falls back to the Funnel with nobody the wiser. Read only."""
    caddy = ROOT / "edge" / "Caddyfile"
    if not caddy.is_file():
        pytest.skip("the edge's Caddyfile is not in this checkout")
    text = caddy.read_text(encoding="utf-8")
    routes = {}
    for chunk in re.split(r"(?m)^(?=\s*handle(?:_path)?\s)", text):
        named = re.match(r"\s*handle_path\s+/(\w+)/\*", chunk)
        if named:
            upstream = re.search(r"reverse_proxy\s+([\w-]+):8080", chunk)
            routes[named.group(1)] = upstream.group(1) if upstream else None
    assert routes == {"music": "music-gate", "video": "video-gate", "books": "audiobook-gate", "voice": "voice-gate", "in": "popup-gate"}
    assert sorted(routes) == sorted(edge_mod.PREFIXES)
    assert re.search(r'handle\s+/ping\s*\{[^}]*respond\s+"ok"\s+200', text)          # the probe's "ok" is what Caddy says for /ping
