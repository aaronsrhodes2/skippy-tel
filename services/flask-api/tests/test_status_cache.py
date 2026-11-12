"""/status.json is asked for by the home reporter every ~15 s and reads two SQLite files on the Windows bind mount. It is kept for ten
seconds and those reads run in a thread, so a slow disk can't freeze the gevent worker (app/offload.py)."""
import pytest
from flask import Flask

from app.routes import status


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(status, "_ping", lambda url, path="/health", timeout=1.5: (True, "HTTP 200"))
    monkeypatch.setattr(status, "_ollama_models", lambda: ["qwen3:8b"])
    monkeypatch.setattr(status, "_ups_status", lambda: None)
    monkeypatch.setattr(status, "_drive_ok", lambda: False)
    app = Flask(__name__)
    app.register_blueprint(status.bp)
    return app.test_client()


def test_the_snapshot_is_kept_ten_seconds(client, monkeypatch):
    built = []
    real = status.snapshot
    monkeypatch.setattr(status, "snapshot", lambda: built.append(1) or real())
    first = client.get("/status.json").get_json()
    assert client.get("/status.json").get_json() == first and client.get("/status.json").get_json() == first
    assert len(built) == 1                                                       # three requests, one build
    t = {"now": status.time.monotonic() + status._SNAPSHOT_TTL_S + 1}
    monkeypatch.setattr(status.time, "monotonic", lambda: t["now"])
    client.get("/status.json")
    assert len(built) == 2                                                       # ten seconds on, a new one


def test_the_sqlite_reads_run_off_the_event_loop(client, monkeypatch):
    ran = []
    real = status.off_loop
    monkeypatch.setattr(status, "off_loop", lambda fn, *a, **k: ran.append(fn.__name__) or real(fn, *a, **k))
    client.get("/status.json")
    assert sorted(ran) == ["_tags_stats", "_telem_stats"]
