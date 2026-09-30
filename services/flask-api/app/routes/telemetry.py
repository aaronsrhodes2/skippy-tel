"""UI Telemetry — deprecation tracking for touch-input elements.

Every tappable control in every Skippy app fires a POST /telemetry/ui_event
when touched. This endpoint stores it in SQLite so we can audit which
elements are actually used vs. which are dead weight waiting to be removed.

Deprecation rule: if a UI element appears in the code but has zero clicks
in this log (or hasn't been clicked in > 30 days), it is a candidate for
removal. Run GET /telemetry/ui_summary to surface the audit list.

The same database also holds SITE telemetry: what signed-in people do on the cloud
portal (pages opened, actions, tile taps, chats, errors). The portal keeps its events in
memory; the home reporter pulls them and POSTs batches here (they are stored once each:
UNIQUE (boot, seq)). Nothing here reads an IP address or a user-agent: the portal never
sends them. What is recorded is the portal's own contract, portal/telemetry.py, and
the portal's public /privacy page is rendered from it. Events older than 180 days are pruned.

Routes:
  POST /telemetry/ui_event    — log one click  {app, element, ts?}
  GET  /telemetry/ui_summary  — aggregate counts, sorted by clicks ASC
  GET  /telemetry/ui_raw      — raw event log (last N rows, for debugging)
  POST /telemetry/site_events — the home reporter's batch of portal events  {events: [...]}
  GET  /telemetry/site_summary— what people opened and did lately, top pages/tiles, ?days=30&recent=15
  POST /telemetry/site_forget — delete everything recorded under one email  {user}
"""

import json
import logging
import math
import re
import sqlite3
import threading
import time
from contextlib import closing
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS site_events (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                boot   TEXT    NOT NULL,      -- the portal process that made the event ...
                seq    INTEGER NOT NULL,      -- ... and its counter: a redelivered event is ignored
                ts     REAL    NOT NULL,      -- unix timestamp (float seconds)
                who    TEXT    NOT NULL,      -- the signed-in person's email
                role   TEXT    NOT NULL,      -- admin | core | friend
                sid    TEXT    NOT NULL,      -- a random id for one sign-in
                kind   TEXT    NOT NULL,      -- page | action | ui | chat | error
                name   TEXT    NOT NULL,      -- the path, endpoint, tile id ...
                ok     INTEGER,               -- 1 / 0 for an action, else NULL
                detail TEXT    NOT NULL,      -- JSON, already redacted by the portal
                UNIQUE (boot, seq)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_site_ts ON site_events(ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_site_kind_name ON site_events(kind, name)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_site_who ON site_events(who)")
        con.commit()
        _prune_site(con, time.time())
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


# ══ Site telemetry: what signed-in people do on the cloud portal ═══════════════

SITE_KEEP_DAYS = 180
SITE_KINDS = ("page", "action", "ui", "chat", "error")
SITE_ROLES = ("admin", "core", "friend")
_MAX_BATCH    = 500          # events one POST may carry
_MAX_DETAIL   = 20_000       # characters of JSON per event
_PRUNE_EVERY  = 3600         # seconds between prunes on the write path
_last_prune   = 0.0


def _prune_site(con: sqlite3.Connection, now: float) -> int:
    """Delete site events older than SITE_KEEP_DAYS. The caller commits."""
    return con.execute("DELETE FROM site_events WHERE ts < ?", (now - SITE_KEEP_DAYS * 86400,)).rowcount


def _site_row(e, now: float):
    """One event from the reporter as a row, or None when it is malformed. The portal is the only producer and has
    already redacted it; this only refuses what could not be a real event."""
    if not isinstance(e, dict):
        return None
    boot, seq, ts, kind, name = e.get("boot"), e.get("seq"), e.get("ts"), e.get("kind"), e.get("name")
    if not (isinstance(boot, str) and re.fullmatch(r"[0-9a-f]{1,16}", boot)):
        return None
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return None
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return None
    if ts < now - SITE_KEEP_DAYS * 86400 or ts > now + 86400:
        return None
    if kind not in SITE_KINDS or not isinstance(name, str) or not name:
        return None
    who = str(e.get("user") or "").strip().lower()[:120]
    if not who:
        return None                              # the portal only records signed-in people
    role = e.get("role") if e.get("role") in SITE_ROLES else ""
    detail = e.get("detail") if isinstance(e.get("detail"), dict) else {}
    text = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    if len(text) > _MAX_DETAIL:
        text = json.dumps({"truncated": True})
    ok = detail.get("ok")
    return (boot, seq, float(ts), who, role, str(e.get("sid") or "")[:16], kind, name[:120],
            (1 if ok else 0) if isinstance(ok, bool) else None, text)


@bp.post("/telemetry/site_events")
def site_events():
    """The home reporter's batch: {"events": [{boot, seq, ts, user, role, sid, kind, name, detail}, ...]}.
    Idempotent: an event already stored (same boot and seq) is ignored, so a retry can't double count."""
    global _last_prune
    body = request.get_json(silent=True)
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return error("events must be a list", status=400)
    if len(events) > _MAX_BATCH:
        return error(f"at most {_MAX_BATCH} events per request", status=413)
    now = time.time()
    rows = [r for r in (_site_row(e, now) for e in events) if r is not None]
    try:
        with _lock, closing(_conn()) as con, con:
            before = con.total_changes
            con.executemany(
                "INSERT OR IGNORE INTO site_events (boot, seq, ts, who, role, sid, kind, name, ok, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            stored = con.total_changes - before
            if now - _last_prune > _PRUNE_EVERY:
                _prune_site(con, now)
                _last_prune = now
        return jsonify({"ok": True, "stored": stored, "duplicates": len(rows) - stored,
                        "rejected": len(events) - len(rows)})
    except Exception as exc:
        log.error("site telemetry write error: %s", exc)
        return error(str(exc), status=500)


def _recent_detail(kind: str, raw: str) -> dict:
    """Just enough of an event's detail for a 'lately' line: never the whole chat."""
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    out = {k: d[k] for k in ("status", "ok", "path") if k in d}
    if kind == "chat":
        out["message"] = str(d.get("message") or "")[:80]
        out["tools"] = [str(t.get("name"))[:30] for t in (d.get("tools") or [])[:6] if isinstance(t, dict)]
    return out


def site_summary(con: sqlite3.Connection, now: float, days: int = 30, recent: int = 15) -> dict:
    """What the portal's Activity widget shows. Counts over the last `days`; the tap table spans the whole
    retention (a tile nobody has tapped in six months is a removal candidate whatever this month says)."""
    since = now - days * 86400
    total, oldest, newest = con.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM site_events").fetchone()
    window, people = con.execute("SELECT COUNT(*), COUNT(DISTINCT who) FROM site_events WHERE ts >= ?", (since,)).fetchone()
    kinds = {r[0]: r[1] for r in con.execute(
        "SELECT kind, COUNT(*) FROM site_events WHERE ts >= ? GROUP BY kind", (since,))}
    top_pages = [{"name": r[0], "views": r[1], "people": r[2]} for r in con.execute(
        "SELECT name, COUNT(*), COUNT(DISTINCT who) FROM site_events WHERE kind = 'page' AND ts >= ? "
        "GROUP BY name ORDER BY COUNT(*) DESC, name LIMIT 10", (since,))]
    top_taps = [{"name": r[0], "taps": r[1]} for r in con.execute(
        "SELECT name, COUNT(*) FROM site_events WHERE kind = 'ui' AND ts >= ? "
        "GROUP BY name ORDER BY COUNT(*) DESC, name LIMIT 10", (since,))]
    taps = [{"name": r[0], "taps": r[1], "last_days": int((now - r[2]) / 86400)} for r in con.execute(
        "SELECT name, COUNT(*), MAX(ts) FROM site_events WHERE kind = 'ui' "
        "GROUP BY name ORDER BY COUNT(*), MAX(ts) LIMIT 300")]
    actions = [{"name": r[0], "count": r[1], "failed": r[2] or 0} for r in con.execute(
        "SELECT name, COUNT(*), SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) FROM site_events "
        "WHERE kind = 'action' AND ts >= ? GROUP BY name ORDER BY COUNT(*) DESC, name LIMIT 10", (since,))]
    errors = [{"name": r[0], "count": r[1]} for r in con.execute(
        "SELECT name, COUNT(*) FROM site_events WHERE kind = 'error' AND ts >= ? "
        "GROUP BY name ORDER BY COUNT(*) DESC, name LIMIT 5", (since,))]
    latest = [{"ts": r[0], "user": r[1], "role": r[2], "kind": r[3], "name": r[4], "detail": _recent_detail(r[3], r[5])}
              for r in con.execute(
                  "SELECT ts, who, role, kind, name, detail FROM site_events ORDER BY ts DESC, id DESC LIMIT ?", (recent,))]
    return {"generated_at": int(now), "days": days, "retention_days": SITE_KEEP_DAYS, "total_events": total,
            "window_events": window, "people": people, "oldest": oldest, "newest": newest, "kinds": kinds,
            "top_pages": top_pages, "top_taps": top_taps, "taps": taps, "actions": actions, "errors": errors,
            "recent": latest}


@bp.get("/telemetry/site_summary")
def site_summary_route():
    """Aggregates for the portal's dashboard (the home reporter carries this in its report as `site_activity`)."""
    days = min(max(request.args.get("days", type=int, default=30), 1), SITE_KEEP_DAYS)
    recent = min(max(request.args.get("recent", type=int, default=15), 0), 50)
    try:
        with closing(_conn()) as con:
            return jsonify(site_summary(con, time.time(), days, recent))
    except Exception as exc:
        log.error("site telemetry summary error: %s", exc)
        return error(str(exc), status=500)


@bp.post("/telemetry/site_forget")
def site_forget():
    """Delete everything recorded under one email address (a deletion request, see the portal's /privacy).
    Body: {"user": "pal@example.com"}."""
    body = request.get_json(silent=True)
    who = str(body.get("user") or "").strip().lower() if isinstance(body, dict) else ""
    if "@" not in who:
        return error("user (an email address) is required", status=400)
    try:
        with _lock, closing(_conn()) as con, con:
            deleted = con.execute("DELETE FROM site_events WHERE who = ?", (who,)).rowcount
        return jsonify({"ok": True, "deleted": deleted})
    except Exception as exc:
        log.error("site telemetry forget error: %s", exc)
        return error(str(exc), status=500)
