"""GET /status.json — SkippyTel's status as data.

Skippy core has no HTML interface any more (the Captain's decision): the old
human-readable GET /status page is gone, and the cloud portal shows this data
instead. The home reporter on skippy-pc POSTs this JSON to the portal every
~15 s (its "Core" widget, the Watcher lines, `ops home`).

Shows: service health, Ollama models, photo-tag stats, UI telemetry, Drive
connectivity, UPS status.
"""
import sqlite3
import time
from pathlib import Path

import httpx
from flask import Blueprint, jsonify

bp = Blueprint("status", __name__)

_OLLAMA_URL     = "http://ollama:11434"
_WHISPER_URL    = "http://skippytel-whisper:8000"
_UPS_URL        = "http://host.docker.internal:9338"
_TAGS_DB        = Path("/drive-state/photo_tags.db")
_TELEM_DB       = Path("/drive-state/ui_telemetry.db")


# ── Probes ────────────────────────────────────────────────────────────────────

def _ping(url: str, path: str = "/health", timeout: float = 1.5) -> tuple[bool, str]:
    try:
        r = httpx.get(f"{url}{path}", timeout=timeout)
        return r.status_code < 500, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:60]


def _ollama_models() -> list[str]:
    try:
        r = httpx.get(f"{_OLLAMA_URL}/api/tags", timeout=2.0)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def _tags_stats() -> dict:
    if not _TAGS_DB.exists():
        return {"total": 0, "tagged": 0, "db": "missing"}
    try:
        con = sqlite3.connect(str(_TAGS_DB))
        total  = con.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0]
        tagged = con.execute(
            "SELECT COUNT(*) FROM photo_tags WHERE description != '' AND tagged_at > 0"
        ).fetchone()[0]
        con.close()
        return {"total": total, "tagged": tagged, "db": "ok"}
    except Exception as e:
        return {"total": 0, "tagged": 0, "db": str(e)[:60]}


def _telem_stats() -> list[dict]:
    if not _TELEM_DB.exists():
        return []
    try:
        con = sqlite3.connect(str(_TELEM_DB))
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT app, element, COUNT(*) as clicks, MAX(ts) as last_ts
            FROM ui_events
            GROUP BY app, element
            ORDER BY clicks ASC
        """).fetchall()
        con.close()
        now = time.time()
        return [
            {
                "app": r["app"], "element": r["element"],
                "clicks": r["clicks"],
                "days_ago": int((now - r["last_ts"]) / 86400),
            }
            for r in rows
        ]
    except Exception:
        return []


def _ups_status() -> dict | None:
    try:
        r = httpx.get(f"{_UPS_URL}/status", timeout=1.0)
        d = r.json()
        return {
            "on_ac":   d.get("on_ac"),
            "charge":  d.get("charge"),
            "runtime": d.get("runtime"),
            "alert":   d.get("alert"),
        }
    except Exception:
        return None


def _drive_ok() -> bool:
    try:
        from app.routes.dropship import _get_drive, _images_folder_id
        fid = _images_folder_id()
        if not fid:
            return False
        svc = _get_drive()
        svc.files().get(fileId=fid, fields="id").execute()
        return True
    except Exception:
        return False


# ── Route ─────────────────────────────────────────────────────────────────────

def snapshot() -> dict:
    """Everything the status dashboard shows, as data (the home reporter sends
    this to the cloud portal)."""
    ollama_ok, ollama_msg = _ping(_OLLAMA_URL, "/api/tags")
    whisper_ok, whisper_msg = _ping(_WHISPER_URL)
    drive_ok = _drive_ok()
    return {
        "services": [
            {"name": "flask-api", "port": "3003", "ok": True, "detail": "running"},
            {"name": "ollama", "port": "11434", "ok": ollama_ok, "detail": ollama_msg},
            {"name": "whisper", "port": "8000", "ok": whisper_ok, "detail": whisper_msg},
            {"name": "google-drive", "port": "", "ok": drive_ok,
             "detail": "connected" if drive_ok else "not connected"},
        ],
        "ollama_models": _ollama_models(),
        "ups": _ups_status(),
        "photo_tags": _tags_stats(),
        "telemetry": _telem_stats(),
    }


@bp.get("/status.json")
def status_json():
    return jsonify(snapshot())
