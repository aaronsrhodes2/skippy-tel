"""Background photo scanner — tags all images in Drive Images/ folder.

Runs as a daemon thread (started once at app startup). Every
SCAN_INTERVAL_S seconds it:

  1. Lists all images in the configured Drive Images/ folder
  2. Compares against already-tagged file IDs in SQLite
  3. For each untagged image: downloads it, calls LLaVA, stores description
  4. Sleeps until the next cycle

Rate limiting:
  - TAG_DELAY_S pause between each LLaVA call (avoid hammering Ollama)
  - If llava isn't installed, all describe_image() calls return None
    and the scanner quietly marks files as seen-but-untagged

This runs completely independently of HTTP requests so slow Ollama
inference never blocks the Flask worker threads.
"""
import io
import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SCAN_INTERVAL_S = 10 * 60   # full Drive scan every 10 minutes
TAG_DELAY_S     = 2.0        # pause between LLaVA calls
_MAX_TAG_PER_SCAN = 20       # don't burn through all files in one scan cycle


def _run_scanner():
    """Main loop — runs forever in a daemon thread."""
    from app.routes.dropship import _get_drive, _images_folder_id
    from app.clients.photo_tagger import describe_image
    from app import photo_tags

    log.info("photo_scan: background scanner started")
    # Give Flask a moment to finish starting up
    time.sleep(15)

    while True:
        try:
            _scan_cycle(_get_drive, _images_folder_id, describe_image, photo_tags)
        except Exception as exc:
            log.error("photo_scan: cycle error: %s", exc)
        time.sleep(SCAN_INTERVAL_S)


def _scan_cycle(get_drive_fn, folder_id_fn, describe_fn, tags_mod):
    """One full scan: list Drive → tag new files."""
    fid = folder_id_fn()
    if not fid:
        log.debug("photo_scan: no folder ID configured, skipping")
        return

    try:
        svc = get_drive_fn()
    except Exception as exc:
        log.warning("photo_scan: Drive not available: %s", exc)
        return

    tagged_ids = tags_mod.get_tagged_ids()
    tagged_this_cycle = 0
    page_token = None

    while True:
        resp = svc.files().list(
            q=(
                f"'{fid}' in parents "
                "and mimeType contains 'image/' "
                "and trashed = false"
            ),
            pageSize=100,
            pageToken=page_token,
            orderBy="createdTime desc",
            fields="nextPageToken, files(id, name, mimeType, createdTime)",
        ).execute()

        for f in resp.get("files", []):
            file_id = f["id"]
            name    = f.get("name", "")
            created = f.get("createdTime", "")
            mime    = f.get("mimeType", "image/jpeg")

            if file_id in tagged_ids:
                continue   # already done

            if tagged_this_cycle >= _MAX_TAG_PER_SCAN:
                log.info("photo_scan: hit per-cycle limit (%d), resuming next cycle", _MAX_TAG_PER_SCAN)
                return

            # Mark as seen immediately so a crash doesn't leave it in limbo
            tags_mod.mark_seen(file_id, name, created)

            # Download + tag
            try:
                from googleapiclient.http import MediaIoBaseDownload
                buf = io.BytesIO()
                dl_req = svc.files().get_media(fileId=file_id)
                dl = MediaIoBaseDownload(buf, dl_req, chunksize=512 * 1024)
                done = False
                while not done:
                    _, done = dl.next_chunk()
                buf.seek(0)
                image_bytes = buf.read()
            except Exception as exc:
                log.warning("photo_scan: download %s failed: %s", file_id, exc)
                tagged_this_cycle += 1
                time.sleep(TAG_DELAY_S)
                continue

            description = describe_fn(image_bytes, mime) or ""
            if description:
                tags_mod.upsert_tag(file_id, name, description, drive_created=created)
                log.info("photo_scan: tagged '%s' → %s…", name, description[:60])
            else:
                # llava not available or returned nothing — leave tagged_at=0 so
                # we retry this file on the next scan cycle
                log.debug("photo_scan: no description for '%s' (llava unavailable?), will retry", name)

            tagged_this_cycle += 1
            time.sleep(TAG_DELAY_S)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if tagged_this_cycle:
        log.info("photo_scan: cycle complete — tagged %d new file(s)", tagged_this_cycle)
    else:
        log.debug("photo_scan: cycle complete — nothing new to tag")


def start_background_scanner():
    """Start the scanner daemon thread. Call once from create_app()."""
    t = threading.Thread(target=_run_scanner, name="photo-scanner", daemon=True)
    t.start()
    log.info("photo_scan: daemon thread launched")
