"""Doom Loop dev channel: the Captain's work-in-progress game, playable in the browser at /doomloop.

The game lives in its own repo (github.com/aaronsrhodes2/doom-loop). On every push to main that repo's CI
builds the static site and uploads it to a folder of one GCS bucket (DOOMLOOP_BUCKET, folder DOOMLOOP_PREFIX).
The portal reads that folder at request time with its own service account, so a new build shows up without
redeploying the portal. Nothing here is baked into the image, nothing talks to an LLM, and saves stay in the
player's own browser.

Layout of the folder (what the game's `scripts/build-web` produces as dist/):

    index.html     the game's page; it must ask for its files by relative path (Vite `base: './'`)
    version.json   {"build": ..., "commit": ..., "built_at": ..., "branch": ...}; written last, so a build
                   that is still uploading is not shown
    assets/...     everything else

Until the bucket is set up, or before the first build lands, the page says there is no build yet.
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
from pathlib import PurePosixPath

log = logging.getLogger(__name__)

BUCKET_ENV = "DOOMLOOP_BUCKET"
PREFIX_ENV = "DOOMLOOP_PREFIX"
DEFAULT_PREFIX = "dev/"
REPO = "https://github.com/aaronsrhodes2/doom-loop"
VERSION_TTL = 10.0           # seconds: how long a build's version.json is trusted before the folder is asked again

# Sent explicitly: python:slim has no /etc/mime.types, and a wrong type on a script or a wasm module breaks the game.
MIMETYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".txt": "text/plain; charset=utf-8",
    ".wgsl": "text/plain; charset=utf-8",
}
DEFAULT_MIMETYPE = "application/octet-stream"

VERSION_FIELDS = ("build", "commit", "built_at", "branch")
_SAFE_ID = re.compile(r"^[0-9A-Za-z._-]{1,64}$")


class GcsStore:
    """Reads files from one folder of one bucket. The client is made on first use, so a portal without the
    google-cloud-storage library or without credentials (tests, local runs) still starts."""

    def __init__(self, bucket: str, prefix: str):
        self.bucket_name = bucket
        self.prefix = prefix if prefix.endswith("/") or not prefix else prefix + "/"
        self._bucket = None

    def read(self, path: str) -> bytes | None:
        """The file's bytes, or None when it isn't there (or the bucket can't be read: logged, not raised)."""
        try:
            if self._bucket is None:
                from google.cloud import storage          # imported here: only a deployed portal needs it
                self._bucket = storage.Client().bucket(self.bucket_name)
            blob = self._bucket.blob(self.prefix + path)
            if not blob.exists():
                return None
            return blob.download_as_bytes()
        except Exception as exc:                          # missing library, no credentials, no bucket yet, network
            log.warning("doomloop: could not read %s: %s", path, exc)
            return None


def get_store():
    """The dev channel's storage, or None when no bucket is configured."""
    bucket = os.environ.get(BUCKET_ENV, "").strip()
    if not bucket:
        return None
    return GcsStore(bucket, os.environ.get(PREFIX_ENV, DEFAULT_PREFIX))


_version_cache: tuple[float, dict] | None = None


def reset_cache() -> None:
    global _version_cache
    _version_cache = None


def clean_path(path: str) -> str | None:
    """A relative path that stays inside the folder, or None. Refuses empty, absolute, backslash, NUL,
    dot-segment and empty-segment paths before anything touches storage."""
    if not path or "\\" in path or "\x00" in path or path.startswith("/"):
        return None
    parts = path.split("/")            # not PurePosixPath: it drops "." and empty segments silently
    if any(p in ("", ".", "..") for p in parts):
        return None
    return path


def mimetype_for(path: str) -> str:
    return MIMETYPES.get(PurePosixPath(path).suffix.lower(), DEFAULT_MIMETYPE)


def read(path: str) -> bytes | None:
    safe = clean_path(path)
    store = get_store()
    if safe is None or store is None:
        return None
    return store.read(safe)


def version() -> dict:
    """The current build's version.json (only the public fields), or {} when there is no build. Cached briefly."""
    global _version_cache
    now = time.monotonic()
    if _version_cache and now - _version_cache[0] < VERSION_TTL:
        return _version_cache[1]
    info: dict = {}
    raw = read("version.json")
    if raw:
        try:
            import json
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("build"), str) and _SAFE_ID.match(data["build"]):
            info = {k: data[k] for k in VERSION_FIELDS if isinstance(data.get(k), str) and len(data[k]) <= 64}
    _version_cache = (now, info)
    return info


def bundled() -> bool:
    return bool(version())


def app_bar(info: dict) -> str:
    """The portal's app bar (‹ Browser Games, the title, a dev-build badge, the Home button), drawn above the game
    instead of over it. Black base, no gradients, like every Skippy surface. The badge shows which commit you are
    playing; its text is checked against a strict pattern and escaped, since it comes from a file."""
    commit = info.get("commit", "")
    commit = commit if _SAFE_ID.match(commit) else ""
    badge = html.escape(f"dev build {commit[:7]}" if commit else "dev build")
    return f"""
<style>
  #game, #gate {{ top: 44px !important; height: calc(100% - 44px) !important; }}
  #sp-bar {{ position: fixed; left: 0; right: 0; top: 0; height: 44px; z-index: 99; display: flex; align-items: center;
            gap: 12px; padding: 0 12px; box-sizing: border-box; background: #000; border-bottom: 1px solid #003322;
            font: 14px -apple-system, 'Segoe UI', Roboto, sans-serif; }}
  #sp-bar a {{ text-decoration: none; }}
  #sp-bar .back {{ color: #00ccff; padding: 6px 12px 6px 8px; border-radius: 999px; border: 1px solid #006644; }}
  #sp-bar h1 {{ margin: 0; font-size: 15px; font-weight: 600; color: #fff; flex: 1; white-space: nowrap; overflow: hidden; }}
  #sp-bar #sp-badge {{ color: #ffcc00; font-size: 12px; border: 1px solid #ffcc00; border-radius: 999px; padding: 2px 8px; }}
  #sp-bar #home-btn {{ width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center;
                      justify-content: center; font-size: 1.05rem; color: #00ccff; border: 1px solid #00ccff; }}
</style>
<header id="sp-bar" data-app="play">
  <a class="back" href="/play">&lsaquo; Browser Games</a>
  <h1 class="apptitle">Doom Loop</h1>
  <span id="sp-badge">{badge}</span>
  <a id="home-btn" href="/" aria-label="Home" title="Home">&#8962;</a>
</header>
"""
