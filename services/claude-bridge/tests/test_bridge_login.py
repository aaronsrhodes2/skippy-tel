"""/health says whether the private Skippy can take a message, and why not. The Captain's chat used to learn that
Claude's sign-in had expired only from a failed run; the bridge now asks `claude auth status` in the background.

The probe must never make /health wait, and a hiccup (timeout, error, unreadable output) must never flip the chat to
"not ready": only a clear `loggedIn: false` does. The conftest stubs the real function for every test; the ones here
that need it put it back."""
import json
import sys
import threading
import time

import pytest

import bridge
import workshop_engine as engine

REAL_RUN_AUTH_STATUS = engine.run_auth_status         # imported before conftest's autouse stub replaces it


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def probe_with(answers, clock=None, **kwargs):
    """A LoginProbe whose `claude auth status` is replaced by `answers` (a list: one entry per run, the last repeating).
    Returns (probe, calls); an entry that is an Exception is raised, a callable is called."""
    calls, queue = [], list(answers)

    def runner(cmd, timeout):
        calls.append((cmd, timeout))
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item() if callable(item) else item
    return engine.LoginProbe(["claude"], runner=runner, clock=clock or Clock(), **kwargs), calls


def settle(probe):
    """Wait for the probe's background check to finish."""
    thread = probe._thread
    if thread is not None:
        thread.join(10)
        assert not thread.is_alive()


def answer(probe):
    """What status() says once the check it just started has finished."""
    probe.status()
    settle(probe)
    return probe.status()


IN, OUT = json.dumps({"loggedIn": True}), json.dumps({"loggedIn": False})


# ── reading the answer ──────────────────────────────────────────────────────
@pytest.mark.parametrize("text, expected", [
    (IN, "ok"),
    (OUT, "expired"),
    ('{"loggedIn": true, "authMethod": "claude.ai", "email": "captain@example.com"}\n', "ok"),
    ("A new version is available\n" + OUT, "expired"),                  # a banner before the JSON is tolerated
    ("", None), (None, None), ("not json", None), ("{}", None), ("[]", None), ("{broken", None),
    ('{"loggedIn": "yes"}', None), ('{"loggedIn": null}', None), ('{"loggedIn": 1}', None),
])
def test_only_a_boolean_loggedIn_is_an_answer(text, expected):
    assert engine.parse_login(text) == expected


# ── the probe itself ────────────────────────────────────────────────────────
def test_status_never_waits_for_the_check():
    release = threading.Event()
    probe, calls = probe_with([lambda: (release.wait(10), IN)[1]])
    started = time.time()
    assert probe.status() == "unknown"                       # at once, while the check is still running
    assert time.time() - started < 2
    assert probe.status() == "unknown"                       # still running: no second check is started
    release.set()
    settle(probe)
    assert probe.status() == "ok" and len(calls) == 1


def test_it_is_unknown_until_the_first_answer_then_ok_or_expired():
    probe, _ = probe_with([IN])
    assert probe.status() == "unknown"
    settle(probe)
    assert probe.status() == "ok"
    probe, _ = probe_with([OUT])
    assert probe.status() == "unknown"
    settle(probe)
    assert probe.status() == "expired"


def test_an_answer_is_cached_for_two_minutes_then_asked_again():
    clock = Clock()
    probe, calls = probe_with([IN, OUT], clock)
    assert answer(probe) == "ok" and len(calls) == 1
    for _ in range(5):
        clock.now += 20                                      # 100 s in
        assert probe.status() == "ok"
    settle(probe)
    assert len(calls) == 1                                   # trusted: nobody ran claude again
    clock.now += 21                                          # 121 s
    assert answer(probe) == "expired" and len(calls) == 2    # stale: it asked again, and the new answer is now the one held
    assert engine.LOGIN_TTL_S == 120 and engine.LOGIN_TIMEOUT_S == 15 and probe.ttl == 120 and probe.timeout == 15


@pytest.mark.parametrize("hiccup", [None, "", "this is not json", "{}", RuntimeError("boom"), OSError("no such file")])
def test_a_hiccup_never_changes_the_answer(hiccup):
    clock = Clock()
    probe, calls = probe_with([hiccup], clock)
    assert answer(probe) == "unknown"                        # never flips ready to false on a hiccup
    probe, calls = probe_with([IN, hiccup, OUT, hiccup], clock)
    assert answer(probe) == "ok"
    clock.now += 121
    assert answer(probe) == "ok"                             # a hiccup keeps what was known
    clock.now += 121
    assert answer(probe) == "expired"
    clock.now += 121
    assert answer(probe) == "expired"                        # and an expired login is not forgotten by one either
    assert len(calls) == 4


def test_concurrent_status_calls_start_one_check():
    release = threading.Event()
    probe, calls = probe_with([lambda: (release.wait(10), IN)[1]])
    threads = [threading.Thread(target=probe.status) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    release.set()
    settle(probe)
    assert len(calls) == 1


# ── the real command, against a stand-in claude ─────────────────────────────
def fake_cmd(shop):
    return [sys.executable, str(shop.fake)]


def test_the_real_check_runs_auth_status_and_reads_the_json(shop, monkeypatch):
    for login, expected in (("in", "ok"), ("out", "expired"), ("banner", "ok")):
        monkeypatch.setenv("FAKE_LOGIN", login)
        text = REAL_RUN_AUTH_STATUS(fake_cmd(shop), 15)
        assert engine.parse_login(text) == expected, text    # exit status 1 (signed out) still carries the JSON
    monkeypatch.setenv("FAKE_LOGIN", "in")
    seen = json.loads(REAL_RUN_AUTH_STATUS(fake_cmd(shop), 15))
    assert seen["argv"] == ["auth", "status"]                # exactly this command, nothing else
    assert seen["leak"] is False                             # and without the door's key in its environment


@pytest.mark.parametrize("login", ["junk", "fail"])
def test_the_real_check_with_nothing_readable_is_a_hiccup(shop, monkeypatch, login):
    monkeypatch.setenv("FAKE_LOGIN", login)
    assert engine.parse_login(REAL_RUN_AUTH_STATUS(fake_cmd(shop), 15)) is None


def test_the_real_check_gives_up_on_a_hung_claude_and_kills_it(shop, tmp_path, monkeypatch):
    beat = tmp_path / "heartbeat"
    monkeypatch.setenv("FAKE_LOGIN", "hang")
    monkeypatch.setenv("FAKE_HEARTBEAT", str(beat))
    started = time.time()
    assert REAL_RUN_AUTH_STATUS(fake_cmd(shop), 3) is None
    assert time.time() - started < 20
    assert beat.exists()                                     # it did start, and was hung
    before = beat.stat().st_size
    time.sleep(0.8)
    assert beat.stat().st_size == before                     # and it is dead now, not left running in the background


def test_a_claude_that_cannot_start_is_a_hiccup_not_a_crash(tmp_path):
    assert REAL_RUN_AUTH_STATUS([str(tmp_path / "no-such-claude")], 5) is None


def test_the_probe_end_to_end_with_the_real_command(shop, monkeypatch):
    monkeypatch.setattr(engine, "run_auth_status", REAL_RUN_AUTH_STATUS)
    monkeypatch.setenv("FAKE_LOGIN", "out")
    probe = engine.LoginProbe(fake_cmd(shop))
    assert probe.status() == "unknown"
    settle(probe)
    assert probe.status() == "expired"


# ── /health ─────────────────────────────────────────────────────────────────
def expired_after_one_check(shop):
    shop.login_probe = engine.LoginProbe(["claude"], runner=lambda cmd, timeout: OUT)
    return shop.login_probe


def test_health_names_the_login_and_is_ready_while_it_is_unknown_or_ok(door, shop):
    shop.login_probe = engine.LoginProbe(["claude"], runner=lambda cmd, timeout: IN)
    status, health = door("GET", "/health", token=None)
    assert status == 200 and health["workshop"]["login"] == "unknown" and health["workshop"]["ready"] is True
    assert "reason" not in health["workshop"]
    settle(shop.login_probe)
    health = door("GET", "/health", token=None)[1]["workshop"]
    assert health["login"] == "ok" and health["ready"] is True and "reason" not in health
    assert health["model"] == "claude-sonnet-5-5" and health["busy"] is False


def test_an_expired_login_makes_the_workshop_not_ready_and_says_how_to_fix_it(door, shop):
    probe = expired_after_one_check(shop)
    assert door("GET", "/health", token=None)[1]["workshop"]["ready"] is True       # the first answer isn't in yet
    settle(probe)
    workshop = door("GET", "/health", token=None)[1]["workshop"]
    assert workshop["login"] == "expired" and workshop["ready"] is False
    assert workshop["reason"].startswith("Claude's login on skippy-pc expired") and "tools/claude_token.sh" in workshop["reason"]


def test_no_token_makes_it_not_ready_whatever_the_login_and_says_so(door, shop, monkeypatch):
    shop.login_probe = engine.LoginProbe(["claude"], runner=lambda cmd, timeout: IN)
    door("GET", "/health", token=None)
    settle(shop.login_probe)
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, ""))
    workshop = door("GET", "/health", token=None)[1]["workshop"]
    assert workshop["ready"] is False and workshop["login"] == "ok"
    assert workshop["reason"] == "SKIPPY_HOME_TOKEN isn't configured on this PC"
    probe = expired_after_one_check(shop)                                           # both wrong: the token is the first thing to fix
    door("GET", "/health", token=None)
    settle(probe)
    assert door("GET", "/health", token=None)[1]["workshop"]["reason"] == "SKIPPY_HOME_TOKEN isn't configured on this PC"


def test_health_answers_while_the_check_is_still_running_and_shows_nothing_it_printed(door, shop):
    release = threading.Event()
    shop.login_probe = engine.LoginProbe(["claude"], runner=lambda cmd, timeout: (
        release.wait(20), '{"loggedIn": true, "email": "captain@example.com", "orgName": "Skippy Labs"}')[1])
    try:
        started = time.time()
        status, health = door("GET", "/health", token=None)
        assert status == 200 and time.time() - started < 5                          # not held up by the check
        assert health["workshop"]["login"] == "unknown"
    finally:
        release.set()
    settle(shop.login_probe)
    text = json.dumps(door("GET", "/health", token=None)[1])
    assert '"login": "ok"' in text
    assert "captain@example.com" not in text and "Skippy Labs" not in text and "door-token" not in text


def test_the_health_check_does_not_start_a_check_on_every_call(door, shop):
    clock = Clock()
    probe, calls = probe_with([IN], clock)
    shop.login_probe = probe
    for _ in range(5):
        door("GET", "/health", token=None)
        settle(probe)
    assert len(calls) == 1
