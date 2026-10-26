"""Star Control Zero: the Captain's fan-game prequel, playable in the browser at /starcontrol.

The game lives in its own repo (github.com/aaronsrhodes2/star-control-precursors). Its web build
(Python compiled to WebAssembly by pygbag; `tools/build_web.py` there) is published as a GitHub
Release, and the portal image carries one copy of it, fetched by portal/Dockerfile at the release
pinned in core/cd/deploy.json (STARCONTROL_REF) into STARCONTROL_DIR:

    index.html            the door: loads the game on a computer; on a phone or tablet it waits
                          for a controller (the game has no touch controls)
    game.html             the game's own page
    web.tar.gz.partNN     the game bundle, in parts under Cloud Run's 32 MiB response cap
    assets/music/<track>/ one MP3 per stem, streamed while you play
    version.json          which build this is

Nothing here talks to the game servers or to an LLM: the portal only hands out the files, to
signed-in people. Saves stay in the player's own browser.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("STARCONTROL_DIR", Path(__file__).with_name("starcontrol_static")))
REPO = "https://github.com/aaronsrhodes2/star-control-precursors"

# Sent explicitly: python:slim has no /etc/mime.types, and a guessed type for "web.tar.gz.part00"
# would be wrong anyway (the browser must get the bytes as they are, not gunzipped).
MIMETYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".png": "image/png",
}
DEFAULT_MIMETYPE = "application/octet-stream"


def game_dir() -> Path:
    return ROOT


def bundled() -> bool:
    return (ROOT / "index.html").is_file() and (ROOT / "game.html").is_file()


def version() -> dict:
    """The build's own version.json, or {} when the game isn't bundled (or the file is damaged)."""
    try:
        data = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def mimetype_for(path: str) -> str:
    return MIMETYPES.get(Path(path).suffix.lower(), DEFAULT_MIMETYPE)


# Injected into the game's index.html when served here: the portal's app bar (‹ Home, the title, the
# round Home button), drawn above the game instead of over it so it never hides the game's own
# corner read-outs. Black base and no gradients, like every Skippy surface.
APP_BAR_SNIPPET = """
<style>
  #game, #gate { top: 44px !important; height: calc(100% - 44px) !important; }
  #sp-bar { position: fixed; left: 0; right: 0; top: 0; height: 44px; z-index: 99; display: flex; align-items: center;
            gap: 12px; padding: 0 12px; box-sizing: border-box; background: #000; border-bottom: 1px solid #003322;
            font: 14px -apple-system, 'Segoe UI', Roboto, sans-serif; }
  #sp-bar a { text-decoration: none; }
  #sp-bar .back { color: #00ccff; padding: 6px 12px 6px 8px; border-radius: 999px; border: 1px solid #006644; }
  #sp-bar h1 { margin: 0; font-size: 15px; font-weight: 600; color: #fff; flex: 1; white-space: nowrap;
               overflow: hidden; text-overflow: ellipsis; }
  #sp-bar #home-btn { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center;
                      justify-content: center; font-size: 1.05rem; color: #00ccff; border: 1px solid #00ccff; }
</style>
<header id="sp-bar" data-app="play">
  <a class="back" href="/play">&lsaquo; Browser Games</a>
  <h1 class="apptitle">Star Control Zero</h1>
  <a id="home-btn" href="/" aria-label="Home" title="Home">&#8962;</a>
</header>
"""
