"""GET /status — human-readable HTML dashboard for SkippyTel.

Open in any browser: http://localhost:3003/status
                or:  http://skippy-pc:3003/status  (Tailscale)

Auto-refreshes every 30 seconds. Pure HTML — no JavaScript, no CDN.
Shows: service health, Ollama models, photo-tag stats, UI telemetry,
Drive connectivity, UPS status.
"""
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from flask import Blueprint, Response

bp = Blueprint("status", __name__)

_OLLAMA_URL     = "http://ollama:11434"
_WHISPER_URL    = "http://skippytel-whisper:8000"
_INSIGHTFACE_URL= "http://skippytel-insightface:8000"
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


# ── HTML helpers ──────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #000; color: #ccc; font-family: 'Courier New', monospace;
       font-size: 13px; padding: 24px; }
h1   { color: #ff44aa; letter-spacing: 4px; font-size: 14px;
       text-transform: uppercase; margin-bottom: 4px; }
.ts  { color: #444; font-size: 11px; margin-bottom: 24px; }
h2   { color: #ff44aa; font-size: 11px; letter-spacing: 3px;
       text-transform: uppercase; margin: 24px 0 10px; border-bottom: 1px solid #1a1a1a;
       padding-bottom: 4px; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 5px 10px 5px 0; text-align: left; vertical-align: top; }
th { color: #555; font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }
tr:hover td { background: #0a0a0a; }
.ok   { color: #00ff88; }
.warn { color: #ffaa00; }
.err  { color: #ff4444; }
.dim  { color: #444; }
.zero { color: #ff4444; font-weight: bold; }
"""


def _dot(ok: bool) -> str:
    cls = "ok" if ok else "err"
    sym = "●" if ok else "○"
    return f'<span class="{cls}">{sym}</span>'


def _row(*cells) -> str:
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return f"<tr>{tds}</tr>"


# ── Route ─────────────────────────────────────────────────────────────────────

@bp.get("/status")
def status_page():
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── probe all services ────────────────────────────────────────────────
    flask_ok  = True   # we're answering this request
    ollama_ok, ollama_msg = _ping(_OLLAMA_URL, "/api/tags")
    models    = _ollama_models()
    whisper_ok, whisper_msg   = _ping(_WHISPER_URL)
    iface_ok,   iface_msg     = _ping(_INSIGHTFACE_URL)
    ups       = _ups_status()
    tags      = _tags_stats()
    telem     = _telem_stats()
    drive_ok  = _drive_ok()

    # ── build HTML ────────────────────────────────────────────────────────
    rows_services = "".join([
        _row(_dot(True),  "flask-api",    "3003", '<span class="ok">running</span>'),
        _row(_dot(ollama_ok), "ollama",   "11434", ollama_msg),
        _row(_dot(whisper_ok), "whisper", "8000",  whisper_msg),
        _row(_dot(iface_ok),  "insightface", "8000", iface_msg),
        _row(_dot(drive_ok), "google-drive", "—",
             '<span class="ok">connected</span>' if drive_ok
             else '<span class="err">not connected</span>'),
    ])

    ups_row = ""
    if ups:
        ac_cls  = "ok" if ups["on_ac"] else "warn"
        ac_text = "on AC" if ups["on_ac"] else "ON BATTERY"
        chg     = ups["charge"] or 0
        chg_cls = "ok" if chg > 50 else ("warn" if chg > 20 else "err")
        ups_row = f"""
        <h2>⚡ UPS</h2>
        <table><tr>
          <th>Power</th><th>Charge</th><th>Runtime</th><th>Alert</th>
        </tr>{_row(
            f'<span class="{ac_cls}">{ac_text}</span>',
            f'<span class="{chg_cls}">{chg}%</span>',
            f'{ups.get("runtime","?")} min',
            ups.get("alert") or '<span class="dim">none</span>',
        )}</table>"""

    model_list = ", ".join(models) if models else '<span class="dim">none loaded</span>'

    tagged_pct = int(100 * tags["tagged"] / tags["total"]) if tags["total"] else 0
    tags_cls   = "ok" if tagged_pct >= 80 else ("warn" if tagged_pct >= 30 else "err")
    tags_section = f"""
    <h2>📷 Photo Tags</h2>
    <table><tr><th>DB</th><th>Total</th><th>Tagged</th><th>Coverage</th></tr>
    {_row(
        f'<span class="{"ok" if tags["db"]=="ok" else "err"}">{tags["db"]}</span>',
        tags["total"],
        tags["tagged"],
        f'<span class="{tags_cls}">{tagged_pct}%</span>',
    )}</table>"""

    if telem:
        telem_header = _row(
            "<th>App</th><th>Element</th><th>Clicks</th><th>Last used</th>"
        )
        telem_rows = "".join(
            _row(
                f'<span class="dim">{t["app"]}</span>',
                t["element"],
                f'<span class="{"zero" if t["clicks"]==0 else "ok"}">{t["clicks"]}</span>',
                f'{t["days_ago"]}d ago' if t["days_ago"] > 0 else "today",
            )
            for t in telem
        )
        telem_section = f"""
        <h2>👆 UI Telemetry  <span class="dim" style="font-size:10px">
          (sorted: lowest clicks first = removal candidates)</span></h2>
        <table>{telem_header}{telem_rows}</table>"""
    else:
        telem_section = """
        <h2>👆 UI Telemetry</h2>
        <p class="dim">No interactions recorded yet.</p>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SkippyTel Status</title>
  <style>{_CSS}</style>
</head>
<body>
  <h1>SkippyTel</h1>
  <p class="ts">Last updated: {now_str} &nbsp;·&nbsp; auto-refreshes every 30s</p>

  <h2>🛰 Services</h2>
  <table>
    <tr><th></th><th>Service</th><th>Port</th><th>Status</th></tr>
    {rows_services}
  </table>

  <h2>🤖 Ollama Models</h2>
  <p>{model_list}</p>

  {ups_row}
  {tags_section}
  {telem_section}

  <h2>🔗 Quick Links</h2>
  <table>
    <tr><td><a href="/health" style="color:#00ff88">/health</a></td>
        <td class="dim">JSON health check</td></tr>
    <tr><td><a href="/services" style="color:#00ff88">/services</a></td>
        <td class="dim">Service manifest</td></tr>
    <tr><td><a href="/telemetry/ui_summary" style="color:#00ff88">/telemetry/ui_summary</a></td>
        <td class="dim">UI click audit (JSON)</td></tr>
    <tr><td><a href="/dropship/health" style="color:#00ff88">/dropship/health</a></td>
        <td class="dim">Drive connectivity</td></tr>
    <tr><td><a href="/dropship/latest" style="color:#00ff88">/dropship/latest</a></td>
        <td class="dim">Most recent photo</td></tr>
    <tr><td><a href="/music/session" style="color:#00ff88">/music/session</a></td>
        <td class="dim">Current music session</td></tr>
  </table>
</body>
</html>"""

    return Response(html, mimetype="text/html")
