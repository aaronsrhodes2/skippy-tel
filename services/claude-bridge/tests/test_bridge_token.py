"""R07: a Google Drive hiccup must not shut the bridge.

The bridge's bearer token lives in the vault .env on Google Drive (G:), and the bridge re-reads it every 30 s. It used to
overwrite its cache with whatever the read returned, and the read returns '' when Drive is restarting, paused, signed out
or not mounted yet. So a hiccup flipped /health's workshop.ready to false and every workshop route to 503 within 30 s;
a run in flight then lost its relay (and the reporter gave up on it after 20 refusals) while the run itself carried on.

Now the cache keeps the last NON-EMPTY token and only another non-empty read replaces it."""
import time

import pytest

import bridge
import workshop_engine as engine
from bridge_testkit import calls, finish, make_door, make_shop

REAL_HOME_TOKEN = engine.home_token                # bridge.workshop IS this module: keep the real reader before a test swaps it


class Vault:
    """Stands in for workshop.home_token(): returns what the test queued, one answer per read (the last one repeats)."""

    def __init__(self, *answers):
        self.answers, self.reads = list(answers), 0

    def __call__(self):
        self.reads += 1
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


@pytest.fixture
def vault(monkeypatch):
    def install(*answers, cache=(0.0, "")):
        v = Vault(*answers)
        monkeypatch.setattr(bridge.workshop, "home_token", v)
        monkeypatch.setattr(bridge, "_token_cache", cache)
        return v
    return install


def test_an_empty_read_never_replaces_a_token_that_is_held(vault):
    v = vault("good-token", "", "", "good-token", cache=(0.0, ""))
    assert bridge.current_token(now=1000.0) == "good-token"
    assert bridge.current_token(now=1031.0) == "good-token"            # Drive hiccup: the read came back empty ...
    assert bridge.current_token(now=1062.0) == "good-token"            # ... twice, and it is still the one we hold
    assert bridge.current_token(now=1093.0) == "good-token" and v.reads == 4


def test_a_new_non_empty_token_does_replace_it_so_repairing_home_needs_no_restart(vault):
    vault("old-token", "", "new-token", cache=(0.0, ""))
    assert bridge.current_token(now=1000.0) == "old-token"
    assert bridge.current_token(now=1031.0) == "old-token"             # an empty read in between changes nothing
    assert bridge.current_token(now=1062.0) == "new-token"             # the pairing changed: the new one is honoured at once
    assert bridge.current_token(now=1063.0) == "new-token"


def test_a_vault_file_that_loses_the_value_or_vanishes_never_wins(tmp_path, monkeypatch):
    """The real reader, through the real cache: a vault that was emptied, or stopped existing, reads as ''."""
    vault_file = tmp_path / ".env"
    vault_file.write_text("SKIPPY_HOME_TOKEN=from-the-vault\n", encoding="utf-8")
    monkeypatch.setattr(bridge.workshop, "home_token", lambda: REAL_HOME_TOKEN({}, (str(vault_file),)))
    monkeypatch.setattr(bridge, "_token_cache", (0.0, ""))
    assert bridge.current_token(now=1000.0) == "from-the-vault"
    vault_file.write_text("SKIPPY_HOME_TOKEN=\n", encoding="utf-8")     # Drive handed back a stub with the value gone
    assert bridge.current_token(now=1031.0) == "from-the-vault"
    vault_file.unlink()                                                # G: unmounted altogether
    assert bridge.current_token(now=1062.0) == "from-the-vault"


def test_the_token_is_still_reread_at_most_every_thirty_seconds(vault):
    v = vault("t", cache=(0.0, ""))
    for now in (1000.0, 1010.0, 1029.9, 1030.0):
        assert bridge.current_token(now=now) == "t"
    assert v.reads == 1                                                # inside 30 s of the last look: no read
    assert bridge.current_token(now=1030.1) == "t" and v.reads == 2    # 30 s on: one more
    assert bridge.TOKEN_REREAD_S == 30.0


def test_with_no_token_yet_it_looks_again_within_seconds_so_the_chat_wakes_soon_after_drive_mounts(vault):
    """At logon the bridge can start before Drive has mounted G:. Nothing is held then, so it must not wait 30 s each time."""
    v = vault("", "", "late-token", cache=(0.0, ""))
    assert bridge.current_token(now=1000.0) == ""                      # nothing yet: shut (503), as before
    assert bridge.current_token(now=1003.0) == "" and v.reads == 1     # not hammered on every request
    assert bridge.current_token(now=1005.1) == "" and v.reads == 2     # but retried after 5 s ...
    assert bridge.current_token(now=1010.3) == "late-token"            # ... and picked up the moment Drive is there
    assert bridge.TOKEN_RETRY_S == 5.0


def test_a_fresh_cache_is_trusted_as_it_always_was(vault):
    v = vault("never-read", cache=(time.time() + 3600, "cached"))
    assert bridge.current_token() == "cached" and v.reads == 0


# ── through the real HTTP door ──────────────────────────────────────────────
def test_a_drive_hiccup_does_not_shut_the_workshop_door_or_health(tmp_path, monkeypatch):
    shop = make_shop(tmp_path, monkeypatch)
    call, server = make_door(shop, monkeypatch)
    try:
        v = Vault("")                                                   # Drive is gone: every read is empty
        monkeypatch.setattr(bridge.workshop, "home_token", v)
        monkeypatch.setattr(bridge, "_token_cache", (0.0, "door-token"))     # stale, so the next request really reads
        body = {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi"}
        status, started = call("POST", "/workshop", body)
        assert status == 200 and started["ok"] is True                  # before the fix: 503 bridge_disabled
        finish(shop.get("aaaaaaaa"))
        assert v.reads == 1                                             # it did look, and ignored the empty answer
        assert call("GET", "/workshop/runs")[0] == 200
        assert call("GET", "/workshop/run/aaaaaaaa")[0] == 200
        health = call("GET", "/health", token=None)[1]["workshop"]
        assert health["ready"] is True and "reason" not in health
        assert call("POST", "/workshop", body, token="wrong")[0] == 401  # and a stranger still gets nothing
        assert len(calls(shop)) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_with_no_token_ever_the_door_is_still_shut(tmp_path, monkeypatch):
    shop = make_shop(tmp_path, monkeypatch)
    call, server = make_door(shop, monkeypatch)
    try:
        monkeypatch.setattr(bridge.workshop, "home_token", Vault(""))
        monkeypatch.setattr(bridge, "_token_cache", (0.0, ""))
        status, out = call("POST", "/workshop", {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi"})
        assert status == 503 and out["error"] == "bridge_disabled"
        health = call("GET", "/health", token=None)[1]["workshop"]
        assert health["ready"] is False and health["reason"] == "SKIPPY_HOME_TOKEN isn't configured on this PC"
    finally:
        server.shutdown()
        server.server_close()
