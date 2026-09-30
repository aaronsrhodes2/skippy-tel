"""One crafted event must not make POST /telemetry/site_events answer 500 for the whole batch (flask-api's half).

The defect: a JSON string like "\\ud800" decodes to a lone surrogate. _site_row json.dumps it with ensure_ascii=False and the SQLite
bind raised UnicodeEncodeError; the blanket `except` turned that into HTTP 500 for the batch, and the reporter (which retries a 5xx)
re-sent it for ever, so no good event behind it was ever stored.

Now: a bad string is repaired (each lone surrogate becomes U+FFFD, the event is kept), an event that still cannot be bound is
refused on its own (counted in `rejected`), and only a failure of the database itself (locked, full, unreadable) is a 500.
"""
import json
import sqlite3
import time

import pytest
from flask import Flask

from app.routes import telemetry


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "ui_telemetry.db"
    monkeypatch.setattr(telemetry, "_DB_PATH", path)
    monkeypatch.setattr(telemetry, "_last_prune", 0.0)
    telemetry.init_db()
    app = Flask(__name__)
    app.register_blueprint(telemetry.bp)
    return app.test_client(), path


def ev(seq, kind="page", name="/games", user="pal@example.com", boot="ab12cd34", role="friend", sid="0a1b2c3d", **detail):
    return {"boot": boot, "seq": seq, "ts": time.time(), "user": user, "role": role, "sid": sid, "kind": kind, "name": name,
            "detail": detail}


def post(client, events):
    """What the reporter sends: json.dumps with ensure_ascii, so a lone surrogate travels as the six characters \\ud800."""
    return client.post("/telemetry/site_events", data=json.dumps({"events": events}), content_type="application/json")


def rows(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM site_events ORDER BY seq").fetchall()
    finally:
        con.close()


def every_text(path):
    return [str(v) for r in rows(path) for v in tuple(r)]


@pytest.mark.parametrize("poison", [
    {"detail": {"page": "/x\ud800y"}},                                   # the beacon's p
    {"detail": {"fields": ["\ud800", "ok"]}},                            # the key names of a JSON POST body
    {"detail": {"\udfff": 1}},                                           # a key
    {"detail": {"deep": [{"k": ["a", {"z": "\udc00\ud800 x"}]}]}},       # nested (low before high is not a pair: both are lone)
    {"name": "/p\ud800"}, {"user": "pal\ud800@example.com"}, {"sid": "ab\ud800"},
])
def test_a_lone_surrogate_anywhere_in_an_event_is_repaired_and_never_fails_the_batch(db, poison):
    client, path = db
    res = post(client, [ev(1), {**ev(2), **poison}, ev(3)])
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "stored": 3, "duplicates": 0, "rejected": 0}
    stored = rows(path)
    assert [r["seq"] for r in stored] == [1, 2, 3]
    for text in every_text(path):
        text.encode("utf-8")                                             # nothing unencodable is in the file
    assert "�" in "".join(every_text(path))


def test_a_batch_of_nothing_but_bad_events_is_still_a_200_and_a_retry_stores_nothing_twice(db):
    client, path = db
    batch = [{**ev(n), "detail": {"page": "/x\ud800"}} for n in (1, 2)]
    assert post(client, batch).status_code == 200 and post(client, batch).get_json()["duplicates"] == 2
    assert len(rows(path)) == 2


@pytest.mark.parametrize("bad", [{"seq": 2 ** 70}, {"seq": 2 ** 63}, {"ts": 10 ** 400}, {"seq": 5.5}])
def test_an_event_whose_numbers_sqlite_cannot_hold_is_refused_alone_not_a_500(db, bad):
    client, path = db
    res = post(client, [ev(1), {**ev(2), **bad}, ev(3)])
    assert res.status_code == 200 and res.get_json()["stored"] == 2 and res.get_json()["rejected"] == 1
    assert [r["seq"] for r in rows(path)] == [1, 3]


def test_a_row_the_database_refuses_on_its_own_does_not_take_the_rest_of_the_batch_with_it(db, monkeypatch):
    client, path = db
    real = telemetry._site_row

    def row(e, now):
        got = real(e, now)
        return (got[:9] + (object(),)) if got and e["seq"] == 2 else got            # an unbindable value in one row

    monkeypatch.setattr(telemetry, "_site_row", row)
    res = post(client, [ev(1), ev(2), ev(3)])
    assert res.status_code == 200 and res.get_json()["stored"] == 2 and res.get_json()["rejected"] == 1
    assert [r["seq"] for r in rows(path)] == [1, 3]


def test_an_event_that_blows_up_while_it_is_being_read_is_refused_alone(db, monkeypatch):
    client, path = db
    real = telemetry._site_row_unsafe

    def row(e, now):
        if e["seq"] == 2:
            raise RecursionError("a detail nested deeper than json.dumps can follow")
        return real(e, now)

    monkeypatch.setattr(telemetry, "_site_row_unsafe", row)
    res = post(client, [ev(1), ev(2), ev(3)])
    assert res.status_code == 200 and res.get_json()["stored"] == 2 and res.get_json()["rejected"] == 1
    assert [r["seq"] for r in rows(path)] == [1, 3]


def test_a_body_nested_past_what_json_can_parse_is_a_400_not_a_500(db):
    client, path = db
    # The parser's nesting limit differs by Python version and patch (CI's 3.12 follows 5000 levels, 3.13 on Windows doesn't),
    # so go far past any of them: the C scanner counts its own depth and raises RecursionError long before a million.
    depth = 1_000_000
    body = '{"events": [{"detail": %s}]}' % ("[" * depth + "]" * depth)
    res = client.post("/telemetry/site_events", data=body, content_type="application/json")
    assert res.status_code == 400 and rows(path) == []


def test_a_failure_of_the_database_itself_is_still_a_500_so_the_reporter_keeps_the_events(db, monkeypatch):
    client, path = db

    def locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(telemetry, "_conn", locked)
    assert post(client, [ev(1)]).status_code == 500


def test_a_batch_that_stored_a_partial_run_before_a_bad_row_reports_what_really_went_in(db, monkeypatch):
    client, path = db
    real = telemetry._site_row
    monkeypatch.setattr(telemetry, "_site_row",
                        lambda e, now: (lambda got: (got[:9] + (object(),)) if got and e["seq"] == 3 else got)(real(e, now)))
    res = post(client, [ev(n) for n in range(1, 6)])
    assert res.get_json() == {"ok": True, "stored": 4, "duplicates": 0, "rejected": 1}
    assert [r["seq"] for r in rows(path)] == [1, 2, 4, 5]
