"""UI Telemetry — deprecation tracking for touch-input elements.

Every tappable control in every Skippy app fires a POST /telemetry/ui_event
when touched. This endpoint stores it in SQLite so we can audit which
elements are actually used vs. which are dead weight waiting to be removed.

Deprecation rule: if a UI element appears in the code but has zero clicks
in this log (or hasn't been clicked in > 30 days), it is a candidate for
removal. Run GET /telemetry/ui_summary to surface the audit list.

Routes:
  POST /telemetry/ui_event    — log one click  {app, element, ts?}
  GET  /telemetry/ui_summary  — aggregate counts, sorted by clicks ASC
  GET  /telemetry/ui_raw      — raw event log (last N rows, for debugging)
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from app.version import envelope, error

log = logging.getLogger(__name__)
bp  = Blueprint("telemetry", __name__)

_DB_PATH = Path("/drive-state/ui_telemetry.db")
_lock    = threading.Lock()


# ── DB init ───────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
    with _lock, _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS ui_events (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                app      TEXT    NOT NULL,      -- 'skippy_music', 'skippy_chat', …
                element  TEXT    NOT NULL,      -- 'music_skip', 'play_pause', …
                ts       REAL    NOT NULL        -- unix timestamp (float seconds)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_app_element ON ui_events(app, element)")
        con.commit()
    log.info("ui_telemetry DB ready at %s", _DB_PATH)


# ── POST /telemetry/ui_event ──────────────────────────────────────────────────

@bp.post("/telemetry/ui_event")
def ui_event():
    """
    Log a single UI interaction.
    Body: { "app": "skippy_music", "element": "music_skip", "ts": 1234567890.0 }
    ts is optional; defaults to server time.
    """
    body    = request.get_json(silent=True) or {}
    app_id  = (body.get("app") or "").strip()
    element = (body.get("element") or "").strip()

    if not app_id or not element:
        return error("app and element are required", status=400)

    ts = float(body.get("ts") or time.time())

    try:
        with _lock, _conn() as con:
            con.execute(
                "INSERT INTO ui_events (app, element, ts) VALUES (?, ?, ?)",
                (app_id, element, ts),
            )
            con.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("telemetry write error: %s", exc)
        return error(str(exc), status=500)


# ── GET /telemetry/ui_summary ─────────────────────────────────────────────────

@bp.get("/telemetry/ui_summary")
def ui_summary():
    """
    Aggregate click counts per app+element.

    Sorted by clicks ASC (zero/low-use elements first) so the audit list
    surfaces candidates for removal immediately.

    Returns:
      {
        "elements": [
          {
            "app": "skippy_music",
            "element": "music_skip",
            "clicks": 42,
            "first_seen": "2026-04-25",
            "last_seen":  "2026-04-25",
            "days_since_last": 0
          },
          ...
        ],
        "total_events": 123,
        "generated_at": 1234567890
      }
    """
    app_filter = request.args.get("app")   # optional: filter by app

    try:
        now = time.time()
        with _conn() as con:
            where = "WHERE app = ?" if app_filter else ""
            params = [app_filter] if app_filter else []

            rows = con.execute(f"""
                SELECT
                    app,
                    element,
                    COUNT(*)               AS clicks,
                    MIN(ts)                AS first_ts,
                    MAX(ts)                AS last_ts
                FROM ui_events
                {where}
                GROUP BY app, element
                ORDER BY clicks ASC, last_ts ASC
            """, params).fetchall()

            total = con.execute(
                f"SELECT COUNT(*) FROM ui_events {where}", params
            ).fetchone()[0]

        from datetime import datetime, timezone

        def fmt(ts):
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

        elements = [
            {
                "app":              r["app"],
                "element":          r["element"],
                "clicks":           r["clicks"],
                "first_seen":       fmt(r["first_ts"]),
                "last_seen":        fmt(r["last_ts"]),
                "days_since_last":  int((now - r["last_ts"]) / 86400),
            }
            for r in rows
        ]

        return jsonify({
            "elements":     elements,
            "total_events": total,
            "generated_at": int(now),
        })
    except Exception as exc:
        log.error("telemetry summary error: %s", exc)
        return error(str(exc), status=500)


# ── GET /telemetry/ui_raw ─────────────────────────────────────────────────────

@bp.get("/telemetry/ui_raw")
def ui_raw():
    """Most recent N events. n=100 default, max 1000. For debugging."""
    n = min(int(request.args.get("n", 100)), 1000)
    app_filter = request.args.get("app")

    try:
        with _conn() as con:
            where  = "WHERE app = ?" if app_filter else ""
            params = ([app_filter] if app_filter else []) + [n]
            rows = con.execute(
                f"SELECT app, element, ts FROM ui_events {where} ORDER BY ts DESC LIMIT ?",
                params,
            ).fetchall()

        from datetime import datetime, timezone
        events = [
            {
                "app":     r["app"],
                "element": r["element"],
                "ts":      r["ts"],
                "time":    datetime.fromtimestamp(r["ts"], tz=timezone.utc).isoformat(),
            }
            for r in rows
        ]
        return jsonify({"events": events, "count": len(events)})
    except Exception as exc:
        return error(str(exc), status=500)
