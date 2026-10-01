"""gunicorn's entry point: the gate wired from the environment (the vault .env), without starting its background threads or
touching any real Jellyfin."""
import logging
import re
import threading
import time
import types

import pytest

import fake_jellyfin as fj
import gate
from helpers import NOW


class NoThreads:
    """threading.Thread stand-in: records what would have been started instead of running a forever-loop."""
    started: list = []

    def __init__(self, target=None, daemon=None, name=None, **kw):
        self.target, self.name = target, name

    def start(self):
        NoThreads.started.append(self.name)


@pytest.fixture
def env(monkeypatch, tmp_path, fake):
    NoThreads.started = []
    # only gate.py's view of `threading` is swapped (patching the module itself would stop the fake Jellyfin's own request threads)
    stub = types.SimpleNamespace(**{k: getattr(threading, k) for k in dir(threading) if not k.startswith("__")})
    stub.Thread = NoThreads
    monkeypatch.setattr(gate, "threading", stub)
    for name in ("JELLYFIN_USER", "CATALOG_REFRESH_S", "HLS_MAX_TRANSCODES", "HLS_IDLE_S", "MAX_STREAMS", "IMG_CACHE_MB", "SKIPPY_HOME_TOKEN",
                 "JELLYFIN_API_KEY", "JELLYFIN_URL", "STATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "the-home-token")
    monkeypatch.setenv("JELLYFIN_API_KEY", fj.KEY)
    monkeypatch.setenv("JELLYFIN_URL", fake.url)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    return monkeypatch


def test_the_app_is_wired_from_the_environment_and_starts_its_two_threads(env, fake, tmp_path):
    app = gate.from_env()
    assert sorted(NoThreads.started) == ["catalog-refresh", "hls-reaper"]
    parts = app.extensions["video"]
    assert parts["library"].refresh()                                          # the real library code against the fake Jellyfin
    tok = gate.sign(gate.gate_key("the-home-token"), NOW + 60)
    web = app.test_client()
    pass_now = gate.sign(gate.gate_key("the-home-token"), int(time.time()) + 3600)
    assert web.get(f"/t/{pass_now}/catalog.json").status_code == 200
    assert web.get(f"/t/{gate.sign(gate.gate_key('another-token'), int(time.time()) + 3600)}/catalog.json").status_code == 403
    assert tok and web.get("/health").get_json()["items"] == 11


def test_the_poster_cache_lives_under_the_state_directory(env, fake, tmp_path):
    app = gate.from_env()
    app.extensions["video"]["library"].refresh()
    tok = gate.sign(gate.gate_key("the-home-token"), int(time.time()) + 3600)
    assert app.test_client().get(f"/t/{tok}/img/{fj.fid(1)}/poster?w=160").status_code == 200
    assert list((tmp_path / "state" / "img").rglob("*.jpg"))


def test_the_known_key_is_a_last_line_of_defence_in_the_playlists(env, fake):
    fake.playlist_hook = lambda text, which: text.replace('CODECS="avc1.64001F,mp4a.40.2"', f'CODECS="{fj.KEY}"') if which == "master" else text
    app = gate.from_env()
    app.extensions["video"]["library"].refresh()
    tok = gate.sign(gate.gate_key("the-home-token"), int(time.time()) + 3600)
    r = app.test_client().get(f"/t/{tok}/v/{fj.fid(2)}/hls/master.m3u8")
    assert r.status_code == 502 and fj.KEY not in r.get_data(as_text=True)


def test_the_defaults(env, fake):
    env.delenv("JELLYFIN_URL")
    app = gate.from_env()
    assert app.extensions["video"]["client"].base == "http://host.docker.internal:8096"
    assert app.extensions["video"]["sessions"].cap == 2 and app.extensions["video"]["sessions"].idle_s == 120.0


def test_the_settings_are_read_and_a_typo_falls_back_with_a_warning(env, caplog):
    env.setenv("HLS_MAX_TRANSCODES", "3")
    env.setenv("HLS_IDLE_S", "45")
    env.setenv("CATALOG_REFRESH_S", "600")
    parts = gate.from_env().extensions["video"]
    assert (parts["sessions"].cap, parts["sessions"].idle_s, parts["library"].refresh_s) == (3, 45.0, 600.0)
    caplog.set_level(logging.WARNING)
    for name, bad in (("HLS_MAX_TRANSCODES", "two"), ("HLS_IDLE_S", "5"), ("CATALOG_REFRESH_S", "-1"), ("MAX_STREAMS", "nan"), ("IMG_CACHE_MB", "inf")):
        env.setenv(name, bad)
    parts = gate.from_env().extensions["video"]                                # none of these may stop the app from starting
    assert (parts["sessions"].cap, parts["sessions"].idle_s, parts["library"].refresh_s) == (2, 120.0, 300.0)
    assert caplog.text.count("isn't a number") >= 5 and fj.KEY not in caplog.text


@pytest.mark.parametrize("raw,default,lo,want", [("", 5.0, 1.0, 5.0), ("7", 5.0, 1.0, 7.0), ("7.5", 5.0, 1.0, 7.5), (" 7 ", 5.0, 1.0, 7.0), ("abc", 5.0, 1.0, 5.0),
                                                 ("0", 5.0, 1.0, 5.0), ("-3", 5.0, 1.0, 5.0), ("nan", 5.0, 1.0, 5.0), ("inf", 5.0, 1.0, 5.0),
                                                 ("1e999", 5.0, 1.0, 5.0), ("29", 300.0, 30.0, 300.0), ("30", 300.0, 30.0, 30.0)])
def test_setting(monkeypatch, raw, default, lo, want):
    monkeypatch.setenv("SOME_SETTING", raw)
    assert gate._setting("SOME_SETTING", default, lo) == want


def test_no_home_token_starts_but_refuses_every_pass(env, caplog):
    env.delenv("SKIPPY_HOME_TOKEN")
    caplog.set_level(logging.WARNING)
    app = gate.from_env()
    assert "SKIPPY_HOME_TOKEN isn't set" in caplog.text
    app.extensions["video"]["library"].refresh()
    web = app.test_client()
    for key in (gate.gate_key(""), gate.gate_key("anything"), b""):
        assert web.get(f"/t/{gate.sign(key, int(time.time()) + 60)}/catalog.json").status_code == 403
    assert web.get("/health").status_code == 200


@pytest.mark.parametrize("name,value", [("JELLYFIN_API_KEY", ""), ("JELLYFIN_API_KEY", "short"), ("JELLYFIN_API_KEY", "has space inside 123456"),
                                        ("JELLYFIN_URL", "ftp://host"), ("JELLYFIN_URL", "http://user:pw@host:8096")])
def test_a_bad_jellyfin_setting_stops_the_start_and_the_message_has_no_secret(env, name, value):
    env.setenv(name, value)
    with pytest.raises(RuntimeError) as e:
        gate.from_env()
    text = str(e.value)
    assert text.startswith("video-gate cannot start:") and fj.KEY not in text and (value not in text or value in ("", "ftp://host"))
    assert "pw" not in text and e.value.__cause__ is None
    assert NoThreads.started == []                                             # and no background thread was left running


def test_a_crash_in_a_handler_is_logged_by_class_and_place_only(stack, caplog, monkeypatch):
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(stack.library, "snapshot", lambda: (_ for _ in ()).throw(KeyError(stack.tok + fj.KEY)))
    r = stack.get("/catalog.json")
    assert r.status_code == 500 and r.get_json() == {"error": "something went wrong"}
    assert re.search(r"KeyError in a handler \(\w+\.py:\d+\)", caplog.text)
    assert stack.tok not in caplog.text and fj.KEY not in caplog.text and fj.KEY not in r.get_data(as_text=True)
