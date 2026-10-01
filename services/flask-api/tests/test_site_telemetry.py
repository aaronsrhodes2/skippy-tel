"""Site telemetry in flask-api: the portal's usage events, batched in by the home reporter, stored in the SAME SQLite file as
the phone UI's clicks (a new table, site_events), pruned after 180 days, summarised for the portal's dashboard, and deletable per
person. Run these on their own: `python -m pytest services/flask-api/tests -q` (the portal has an `app.py` of its own).

The events used here are made by the portal's real recorder (portal/telemetry.py, loaded by path: it is standard library only),
so the two services can't drift apart on what an event looks like."""
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest
from flask import Flask

from app.routes import telemetry

REPO = Path(__file__).resolve().parents[3]
DAY = 86400


def _portal_recorder():
    spec = importlib.util.spec_from_file_location("portal_telemetry_under_test", REPO / "portal" / "telemetry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A scratch SQLite file in place of /drive-state/ui_telemetry.db, and a client for the telemetry routes."""
    path = tmp_path / "ui_telemetry.db"
    monkeypatch.setattr(telemetry, "_DB_PATH", path)
    monkeypatch.setattr(telemetry, "_last_prune", 0.0)
    telemetry.init_db()
    app = Flask(__name__)
    app.register_blueprint(telemetry.bp)
    return app.test_client(), path


def ev(seq, kind="page", name="/games", user="pal@example.com", ts=None, boot="ab12cd34", role="friend", **detail):
    return {"boot": boot, "seq": seq, "ts": time.time() if ts is None else ts, "user": user, "role": role, "sid": "0a1b2c3d",
            "kind": kind, "name": name, "detail": detail}


def post(client, events):
    return client.post("/telemetry/site_events", json={"events": events})


def rows(path, sql="SELECT * FROM site_events ORDER BY id"):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── one store, one file ─────────────────────────────────────────────────────────

def test_site_events_share_the_phone_uis_database_and_the_old_routes_still_work(db):
    client, path = db
    tables = {r["name"] for r in rows(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"ui_events", "site_events"} <= tables
    assert client.post("/telemetry/ui_event", json={"app": "skippy_chat", "element": "send"}).get_json() == {"ok": True}
    assert post(client, [ev(1)]).get_json()["stored"] == 1
    summary = client.get("/telemetry/ui_summary").get_json()
    assert [(e["app"], e["element"], e["clicks"]) for e in summary["elements"]] == [("skippy_chat", "send", 1)]
    assert len(rows(path, "SELECT * FROM ui_events")) == 1 and len(rows(path)) == 1


def test_init_db_can_run_again_and_keeps_what_is_there(db):
    client, path = db
    post(client, [ev(1)])
    telemetry.init_db()
    telemetry.init_db()
    assert len(rows(path)) == 1


# ── storing a batch ────────────────────────────────────────────────────────────────

def test_a_batch_from_the_portals_real_recorder_is_stored_whole(db):
    client, path = db
    recorder = _portal_recorder().Telemetry()
    recorder.record("page", "/games", user="pal@example.com", role="friend", sid="0a1b2c3d", endpoint="dashboard.index", status=200, ms=12)
    recorder.record("action", "dashboard.games_start", user="pal@example.com", role="friend", sid="0a1b2c3d", ok=True,
                    status=302, fields=["games"], values={"games": ["valheim"]})
    recorder.record("ui", "tile:games", user="pal@example.com", role="friend", sid="0a1b2c3d", page="/")
    recorder.record("chat", "send", user="pal@example.com", role="friend", sid="0a1b2c3d", message="how do I join?",
                    reply="Tap Valheim.", model="claude-haiku-4-5", tools=[{"name": "server_status", "args": {}}], ms=900)
    recorder.record("error", "404", user="pal@example.com", role="friend", sid="0a1b2c3d", endpoint="", path="/nope", method="GET")
    batch = recorder.since("", 0)["events"]
    got = post(client, json.loads(json.dumps(batch))).get_json()          # what the reporter does: JSON over the wire
    assert got == {"ok": True, "stored": 5, "duplicates": 0, "rejected": 0}
    stored = rows(path)
    assert [r["kind"] for r in stored] == ["page", "action", "ui", "chat", "error"]
    assert [r["ok"] for r in stored] == [None, 1, None, None, None]
    assert json.loads(stored[3]["detail"])["message"] == "how do I join?" and stored[0]["boot"] == batch[0]["boot"]


def test_an_event_delivered_twice_is_stored_once(db):
    client, path = db
    batch = [ev(1), ev(2, kind="ui", name="tile:games")]
    assert post(client, batch).get_json()["stored"] == 2
    again = post(client, batch + [ev(3)]).get_json()
    assert again == {"ok": True, "stored": 1, "duplicates": 2, "rejected": 0}
    assert len(rows(path)) == 3
    assert post(client, [ev(1, boot="ffffffff")]).get_json()["stored"] == 1      # the same seq from another portal process is another event


@pytest.mark.parametrize("bad", [
    "not a dict", None, 5, {},
    ev(1, kind="mystery"), ev(1, name=""), ev(1, user=""), {**ev(1), "boot": "NOT-HEX"}, {**ev(1), "boot": ""},
    {**ev(1), "seq": -1}, {**ev(1), "seq": True}, {**ev(1), "seq": "3"}, {**ev(1), "ts": "yesterday"}, {**ev(1), "ts": float("nan")},
    {**ev(1), "ts": time.time() - 200 * DAY}, {**ev(1), "ts": time.time() + 3 * DAY}, {**ev(1), "name": 5}])
def test_a_malformed_event_is_refused_and_the_rest_of_the_batch_is_kept(db, bad):
    client, path = db
    body = json.dumps({"events": [ev(7), bad, ev(8)]}, allow_nan=True)
    got = client.post("/telemetry/site_events", data=body, content_type="application/json").get_json()
    assert got["stored"] == 2 and got["rejected"] == 1
    assert [r["seq"] for r in rows(path)] == [7, 8]


def test_a_bad_request_is_a_400_or_413_and_stores_nothing(db):
    client, path = db
    assert client.post("/telemetry/site_events", data="nope", content_type="application/json").status_code == 400
    assert client.post("/telemetry/site_events", json={"events": "nope"}).status_code == 400
    assert client.post("/telemetry/site_events", json=[ev(1)]).status_code == 400
    assert post(client, [ev(n) for n in range(501)]).status_code == 413
    assert rows(path) == []


def test_an_absurdly_large_detail_is_replaced_not_stored(db):
    client, path = db
    post(client, [ev(1, blob="x" * 30000)])
    assert json.loads(rows(path)[0]["detail"]) == {"truncated": True}


def test_who_is_kept_lowercase_and_unknown_roles_are_blanked(db):
    client, path = db
    post(client, [ev(1, user="Pal@Example.COM", role="wizard")])
    (row,) = rows(path)
    assert row["who"] == "pal@example.com" and row["role"] == ""


def test_the_events_carry_no_ip_or_user_agent_columns(db):
    _, path = db
    assert [r["name"] for r in rows(path, "PRAGMA table_info(site_events)")] == [
        "id", "boot", "seq", "ts", "who", "role", "sid", "kind", "name", "ok", "detail"]


# ── retention ────────────────────────────────────────────────────────────────────────

def _insert_old(path, seq, days):
    con = sqlite3.connect(str(path))
    con.execute("INSERT INTO site_events (boot, seq, ts, who, role, sid, kind, name, ok, detail) "
                "VALUES ('old00000', ?, ?, 'pal@example.com', 'friend', '', 'page', '/old', NULL, '{}')", (seq, time.time() - days * DAY))
    con.commit()
    con.close()


def test_events_older_than_180_days_are_pruned_when_a_batch_arrives_and_newer_ones_stay(db):
    client, path = db
    _insert_old(path, 1, 181)
    _insert_old(path, 2, 179)
    telemetry._last_prune = 0.0
    post(client, [ev(1)])
    assert sorted(r["seq"] for r in rows(path) if r["boot"] == "old00000") == [2]
    assert telemetry.SITE_KEEP_DAYS == 180


def test_pruning_is_at_most_hourly_on_the_write_path_and_always_at_start(db, monkeypatch):
    client, path = db
    post(client, [ev(1)])                                                  # this one prunes (the first in an hour)
    _insert_old(path, 9, 400)
    post(client, [ev(2)])                                                  # within the hour: it doesn't prune again
    assert any(r["boot"] == "old00000" for r in rows(path))
    telemetry.init_db()                                                    # a restart always prunes
    assert not any(r["boot"] == "old00000" for r in rows(path))


# ── the summary ────────────────────────────────────────────────────────────────────────

def _fill(client, now):
    events = [
        ev(1, name="/games", ts=now - 60, endpoint="dashboard.index", status=200),
        ev(2, name="/games", ts=now - 120, user="core@example.com", role="core"),
        ev(3, name="/games", ts=now - 130, user="core@example.com", role="core"),
        ev(4, name="/games/valheim", ts=now - 200),
        ev(5, name="/games/old", ts=now - 40 * DAY),                                          # outside a 30-day window
        ev(6, kind="ui", name="tile:games", ts=now - 300, page="/"),
        ev(7, kind="ui", name="tile:games", ts=now - 350, page="/"),
        ev(8, kind="ui", name="tile:skippy", ts=now - 100 * DAY, page="/"),                    # only in the whole-retention table
        ev(9, kind="action", name="dashboard.games_start", ts=now - 400, ok=True, status=302),
        ev(10, kind="action", name="dashboard.games_start", ts=now - 410, ok=False, status=403),
        ev(11, kind="error", name="404", ts=now - 500, path="/nope", endpoint="", method="GET"),
        ev(12, kind="chat", name="send", ts=now - 30, message="q" * 200, reply="a private answer", model="claude-haiku-4-5",
           tools=[{"name": "server_status", "args": {}}], ms=800),
    ]
    assert post(client, events).get_json()["stored"] == 12


def test_the_summary_counts_the_window_and_ranks_pages_and_taps(db):
    client, path = db
    now = time.time()
    _fill(client, now)
    got = client.get("/telemetry/site_summary?days=30").get_json()
    assert got["days"] == 30 and got["retention_days"] == 180 and got["total_events"] == 12 and got["window_events"] == 10
    assert got["people"] == 2 and got["kinds"] == {"page": 4, "ui": 2, "action": 2, "error": 1, "chat": 1}
    assert got["top_pages"] == [{"name": "/games", "views": 3, "people": 2}, {"name": "/games/valheim", "views": 1, "people": 1}]
    assert got["top_taps"] == [{"name": "tile:games", "taps": 2}]
    assert got["actions"] == [{"name": "dashboard.games_start", "count": 2, "failed": 1}]
    assert got["errors"] == [{"name": "404", "count": 1}]
    assert got["oldest"] < now - 99 * DAY < got["newest"]


def test_the_tap_table_covers_the_whole_retention_fewest_taps_first(db):
    client, path = db
    now = time.time()
    _fill(client, now)
    taps = client.get("/telemetry/site_summary?days=7").get_json()["taps"]
    assert taps == [{"name": "tile:skippy", "taps": 1, "last_days": 100}, {"name": "tile:games", "taps": 2, "last_days": 0}]


def test_the_latest_lines_are_newest_first_and_never_carry_a_whole_chat(db):
    client, path = db
    now = time.time()
    _fill(client, now)
    recent = client.get("/telemetry/site_summary?recent=3").get_json()["recent"]
    assert [r["name"] for r in recent] == ["send", "/games", "/games"] and [r["user"] for r in recent][1:] == [
        "pal@example.com", "core@example.com"]
    chat = recent[0]
    assert chat["kind"] == "chat" and chat["detail"] == {"message": "q" * 80, "tools": ["server_status"]}
    assert "private answer" not in json.dumps(recent) and len(recent) == 3
    assert client.get("/telemetry/site_summary?recent=0").get_json()["recent"] == []


def test_days_and_recent_are_clamped(db):
    client, _ = db
    assert client.get("/telemetry/site_summary?days=99999").get_json()["days"] == 180
    assert client.get("/telemetry/site_summary?days=-4").get_json()["days"] == 1
    assert client.get("/telemetry/site_summary?days=abc").get_json()["days"] == 30
    post(client, [ev(n, ts=time.time() - n) for n in range(1, 61)])
    assert len(client.get("/telemetry/site_summary?recent=500").get_json()["recent"]) == 50          # capped, not "everything"


def test_an_empty_database_summarises_to_zeros(db):
    client, _ = db
    got = client.get("/telemetry/site_summary").get_json()
    assert got["total_events"] == 0 and got["window_events"] == 0 and got["people"] == 0 and got["oldest"] is None
    assert got["top_pages"] == got["taps"] == got["recent"] == []


def test_the_summary_is_small_enough_to_ride_in_the_home_report(db):
    client, _ = db
    now = time.time()
    post(client, [ev(n, kind="ui", name=f"tile:tap-number-{n}", ts=now) for n in range(1, 401)])
    post(client, [ev(n, kind="ui", name=f"tile:tap-number-{n}", ts=now) for n in range(401, 451)])
    body = client.get("/telemetry/site_summary").get_data()
    assert len(json.loads(body)["taps"]) == 300 and len(body) < 100_000                              # the portal takes a 1 MB report


# ── deleting a person's events ─────────────────────────────────────────────────────────

def test_forgetting_a_person_deletes_only_their_events_whatever_the_case(db):
    client, path = db
    post(client, [ev(1, user="pal@example.com"), ev(2, user="pal@example.com", kind="chat", name="send", message="hi"),
                  ev(3, user="core@example.com")])
    client.post("/telemetry/ui_event", json={"app": "skippy_chat", "element": "send"})
    got = client.post("/telemetry/site_forget", json={"user": " Pal@Example.com "}).get_json()
    assert got == {"ok": True, "deleted": 2}
    assert [r["who"] for r in rows(path)] == ["core@example.com"] and len(rows(path, "SELECT * FROM ui_events")) == 1
    assert client.post("/telemetry/site_forget", json={"user": "pal@example.com"}).get_json()["deleted"] == 0


@pytest.mark.parametrize("body", [{}, {"user": ""}, {"user": "everyone"}, [], "x"])
def test_forgetting_needs_an_email_address_and_never_deletes_by_accident(db, body):
    client, path = db
    post(client, [ev(1)])
    assert client.post("/telemetry/site_forget", json=body).status_code == 400
    assert len(rows(path)) == 1
