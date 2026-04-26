"""
DropShip — Google Drive image service for the Skippy ecosystem.

DropShip is the single source of truth for all images:
  - SD-generated images from SkippyChat
  - Google Photos migration
  - Apple Photos migration (via Mac)
  - Photos from non-music Drive folders
  - Any photo taken on-device (uploaded here automatically)

All images live in the Drive `Images/` folder. The phone never holds
Drive credentials — SkippyTel proxies everything.

Routes:
  GET  /dropship/health              — connectivity + folder check
  GET  /dropship/list                — paginated image list
  GET  /dropship/image/<file_id>     — stream full-res image
  GET  /dropship/thumb/<file_id>     — redirect to Drive thumbnail URL
  POST /dropship/upload              — upload an image (from SkippyChat SD save)
  GET  /dropship/search?q=...        — semantic search via LLaVA tags (SQLite FTS)
  GET  /dropship/latest?n=1          — most recently uploaded image(s)
"""

import io
import logging
import os
import threading
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.config import settings
from app.version import envelope, error

log = logging.getLogger(__name__)
bp  = Blueprint("dropship", __name__)

# ── Drive client (lazy singleton, thread-safe) ────────────────────────────────

_drive_lock    = threading.Lock()
_drive_service = None   # googleapiclient.discovery.Resource
_drive_creds   = None   # google.oauth2.credentials.Credentials (for AuthorizedSession)

_STATE_DIR    = Path("/drive-state")          # bind-mounted from music-collection/state/
_CREDS_FILE   = _STATE_DIR / "drive_credentials.json"
_TOKEN_FILE   = _STATE_DIR / "drive_token.json"
_SCOPES       = ["https://www.googleapis.com/auth/drive"]

_PAGE_SIZE_DEFAULT = 50
_PAGE_SIZE_MAX     = 200


def _get_drive():
    """Return a cached Drive service, refreshing credentials if needed."""
    global _drive_service, _drive_creds
    with _drive_lock:
        if _drive_service is not None:
            return _drive_service
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            if not _TOKEN_FILE.exists():
                raise FileNotFoundError(f"Drive token not found at {_TOKEN_FILE}")

            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                _TOKEN_FILE.write_text(creds.to_json())
                log.info("Drive token refreshed")

            _drive_creds   = creds
            _drive_service = build("drive", "v3", credentials=creds)
            log.info("Drive service initialised")
        except Exception as exc:
            log.warning("Drive init failed: %s", exc)
            raise
    return _drive_service


def _images_folder_id() -> str | None:
    """Return the Drive Images folder ID from config, or None if not set."""
    fid = settings.google_drive_images_folder_id
    return fid if fid else None


# ── GET /dropship/health ──────────────────────────────────────────────────────

@bp.get("/dropship/health")
def dropship_health():
    fid = _images_folder_id()
    if not fid:
        return jsonify({"status": "degraded", "reason": "GOOGLE_DRIVE_IMAGES_FOLDER_ID not set"}), 200

    try:
        svc = _get_drive()
        meta = svc.files().get(fileId=fid, fields="id,name").execute()
        return jsonify({"status": "ok", "images_folder": meta["name"], "folder_id": fid})
    except Exception as exc:
        return jsonify({"status": "error", "reason": str(exc)}), 503


# ── GET /dropship/list ────────────────────────────────────────────────────────

@bp.get("/dropship/list")
def dropship_list():
    """
    Paginated image list from Drive Images/ folder.

    Query params:
      page_token — Drive nextPageToken for subsequent pages
      size        — results per page (default 50, max 200)
    """
    fid = _images_folder_id()
    if not fid:
        return error("GOOGLE_DRIVE_IMAGES_FOLDER_ID not configured", status=503)

    page_token = request.args.get("page_token") or None
    size = min(int(request.args.get("size", _PAGE_SIZE_DEFAULT)), _PAGE_SIZE_MAX)

    try:
        svc = _get_drive()
        resp = svc.files().list(
            q=(
                f"'{fid}' in parents "
                "and mimeType contains 'image/' "
                "and trashed = false"
            ),
            pageSize=size,
            pageToken=page_token,
            orderBy="createdTime desc",
            fields=(
                "nextPageToken, "
                "files(id, name, mimeType, size, createdTime, thumbnailLink, imageMediaMetadata)"
            ),
        ).execute()

        items = []
        for f in resp.get("files", []):
            meta = f.get("imageMediaMetadata", {})
            items.append({
                "id":         f["id"],
                "name":       f.get("name", ""),
                "mime":       f.get("mimeType", ""),
                "size":       f.get("size"),
                "date":       f.get("createdTime"),
                "thumb_url":  f.get("thumbnailLink"),
                "width":      meta.get("width"),
                "height":     meta.get("height"),
            })

        return jsonify({
            "images":     items,
            "next_page":  resp.get("nextPageToken"),
            "count":      len(items),
        })

    except Exception as exc:
        log.error("dropship list error: %s", exc)
        return error(str(exc), status=503)


# ── GET /dropship/image/<file_id> ─────────────────────────────────────────────

@bp.get("/dropship/image/<file_id>")
def dropship_image(file_id: str):
    """Stream the full-resolution image from Drive. Phone never holds credentials."""
    try:
        svc = _get_drive()
        meta = svc.files().get(fileId=file_id, fields="mimeType,name").execute()
        mime = meta.get("mimeType", "image/jpeg")

        from google.auth.transport.requests import AuthorizedSession
        session = AuthorizedSession(_drive_creds)
        resp = session.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
            stream=True,
        )
        resp.raise_for_status()

        def _generate():
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk

        return Response(
            stream_with_context(_generate()),
            mimetype=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    except Exception as exc:
        log.error("dropship image error %s: %s", file_id, exc)
        return error(str(exc), status=503)


# ── GET /dropship/thumb/<file_id> ─────────────────────────────────────────────

@bp.get("/dropship/thumb/<file_id>")
def dropship_thumb(file_id: str):
    """
    Redirect to Drive's built-in thumbnail URL (no bandwidth through SkippyTel).
    Drive generates thumbs automatically; the URL is valid for ~1 hour.
    """
    try:
        svc = _get_drive()
        meta = svc.files().get(fileId=file_id, fields="thumbnailLink").execute()
        thumb = meta.get("thumbnailLink")
        if not thumb:
            return error("No thumbnail available", status=404)
        # Bump thumbnail size from Drive's default (220px) to 512px
        thumb = thumb.replace("=s220", "=s512")
        from flask import redirect
        return redirect(thumb)
    except Exception as exc:
        return error(str(exc), status=503)


# ── POST /dropship/upload ─────────────────────────────────────────────────────

@bp.post("/dropship/upload")
def dropship_upload():
    """
    Upload an image to the Drive Images/ folder.
    Called by SkippyChat after saving an SD-generated image.

    Request: multipart/form-data  OR  JSON {"image_b64": "<data URL>", "name": "..."}
    Returns: {"id": "<drive_file_id>", "name": "..."}
    """
    fid = _images_folder_id()
    if not fid:
        return error("GOOGLE_DRIVE_IMAGES_FOLDER_ID not configured", status=503)

    try:
        svc = _get_drive()

        # Accept both multipart (file field) and JSON (base64 data URL)
        if request.content_type and "multipart" in request.content_type:
            f = request.files.get("image")
            if not f:
                return error("No image field in multipart body", status=400)
            img_bytes = f.read()
            mime      = f.content_type or "image/jpeg"
            name      = f.filename or f"skippy_{_ts()}.jpg"
        else:
            body = request.get_json(silent=True) or {}
            b64  = body.get("image_b64", "")
            if not b64:
                return error("image_b64 required", status=400)
            import base64
            if "," in b64:
                header, data = b64.split(",", 1)
                mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
            else:
                data, mime = b64, "image/png"
            img_bytes = base64.b64decode(data)
            name = body.get("name") or f"skippy_{_ts()}.png"

        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(img_bytes), mimetype=mime, resumable=False)
        meta  = {"name": name, "parents": [fid]}
        result = svc.files().create(body=meta, media_body=media, fields="id,name").execute()

        file_id   = result["id"]
        file_name = result["name"]
        log.info("DropShip upload: %s (%d bytes)", file_name, len(img_bytes))

        # Kick off async LLaVA tagging so the image is searchable soon
        _tag_async(file_id, file_name, img_bytes, mime)

        return jsonify({"id": file_id, "name": file_name})

    except Exception as exc:
        log.error("dropship upload error: %s", exc)
        return error(str(exc), status=503)


def _tag_async(file_id: str, name: str, image_bytes: bytes, mime: str):
    """Fire-and-forget: tag a newly-uploaded image in a background thread."""
    def _work():
        try:
            from app.clients.photo_tagger import describe_image
            from app import photo_tags
            photo_tags.mark_seen(file_id, name)
            desc = describe_image(image_bytes, mime) or ""
            photo_tags.upsert_tag(file_id, name, desc)
            if desc:
                log.info("DropShip auto-tag '%s' → %s…", name, desc[:60])
        except Exception as exc:
            log.warning("DropShip auto-tag failed for %s: %s", file_id, exc)

    threading.Thread(target=_work, name=f"tag-{file_id[:8]}", daemon=True).start()


# ── GET /dropship/search ──────────────────────────────────────────────────────

@bp.get("/dropship/search")
def dropship_search():
    """
    Search tagged photos by description.
    Query params:
      q      — search text (required)
      limit  — max results (default 5)
    Returns list of {file_id, drive_name, description, thumb_url, image_url}
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return error("q is required", status=400)
    limit = min(int(request.args.get("limit", 5)), 20)

    try:
        from app import photo_tags
        hits = photo_tags.search(q, limit=limit)
        base = request.host_url.rstrip("/")
        results = [
            {
                "file_id":     h["file_id"],
                "drive_name":  h["drive_name"],
                "description": h["description"],
                "image_url":   f"{base}/dropship/image/{h['file_id']}",
                "thumb_url":   f"{base}/dropship/thumb/{h['file_id']}",
                "date":        h.get("drive_created", ""),
            }
            for h in hits
        ]
        return jsonify({"results": results, "count": len(results), "query": q})
    except Exception as exc:
        log.error("dropship search error: %s", exc)
        return error(str(exc), status=503)


# ── GET /dropship/latest ──────────────────────────────────────────────────────

@bp.get("/dropship/latest")
def dropship_latest():
    """
    Return the most recently added image(s).
    Query params:
      n  — number of results (default 1, max 10)
    Used for "show me that picture we just took".
    """
    n = min(int(request.args.get("n", 1)), 10)
    try:
        from app import photo_tags
        hits = photo_tags.get_latest(limit=n)
        if not hits:
            # Fall back to Drive API if SQLite is empty
            return _latest_from_drive(n)
        base = request.host_url.rstrip("/")
        results = [
            {
                "file_id":    h["file_id"],
                "drive_name": h["drive_name"],
                "description": h.get("description", ""),
                "image_url":  f"{base}/dropship/image/{h['file_id']}",
                "thumb_url":  f"{base}/dropship/thumb/{h['file_id']}",
                "date":       h.get("drive_created", ""),
            }
            for h in hits
        ]
        return jsonify({"results": results, "count": len(results)})
    except Exception as exc:
        log.error("dropship latest error: %s", exc)
        return error(str(exc), status=503)


def _latest_from_drive(n: int):
    """Fallback when SQLite has no records yet — hit Drive directly."""
    fid = _images_folder_id()
    if not fid:
        return error("GOOGLE_DRIVE_IMAGES_FOLDER_ID not configured", status=503)
    try:
        svc = _get_drive()
        resp = svc.files().list(
            q=f"'{fid}' in parents and mimeType contains 'image/' and trashed = false",
            pageSize=n,
            orderBy="createdTime desc",
            fields="files(id, name, createdTime)",
        ).execute()
        base = request.host_url.rstrip("/")
        results = [
            {
                "file_id":    f["id"],
                "drive_name": f.get("name", ""),
                "description": "",
                "image_url":  f"{base}/dropship/image/{f['id']}",
                "thumb_url":  f"{base}/dropship/thumb/{f['id']}",
                "date":       f.get("createdTime", ""),
            }
            for f in resp.get("files", [])
        ]
        return jsonify({"results": results, "count": len(results)})
    except Exception as exc:
        return error(str(exc), status=503)


def _ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
