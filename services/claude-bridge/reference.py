"""The reference picture (the Captain, 2026-11-03: "we need a better picture inspection and description before starting, and our goal
re-check should re-compare the best source image it can find").

In the MineBench fault-loop series the coder and its reviewer both worked from three words, "a choo-choo train": the reviewer's "no
fault left" meant nothing was plainly broken, never that a stranger would know the thing at a glance, and it passed builds a blind
judge lost every time. So before a run that draws its work starts, the best picture of what was asked is found and looked at:

  1. Core's big model turns the task into a short image-search query for the one thing it asks to be made;
  2. SearXNG on skippy-core (tailnet, no key) finds pictures of it, Wikimedia Commons' photos of real things first; the first few that open become one numbered contact sheet, and the
     big model picks the best three (each the thing itself, whole, clearly seen; different examples of it);
  3. it writes the reference sheet from the three side by side: only what they ALL share (the Captain: "they all have wheels, so we
     need wheels"), part by part, with proportions, outline and what dominates the whole. One photo alone carried its own quirks into
     the sheet (a star emblem, the bridge it stood on) and the review then asked for a copy of that one engine.

Nothing in it is about any one subject: the picture carries that. The sheet reaches the builder before it starts (this file is a
UserPromptSubmit hook: its additionalContext), and the work check's fix-the-worst-fault loop sends the reference picture ahead of
each drawing of the work, so every review re-compares the work with it (work_check.py, eyes.worst_fault).

On only for a Skippy Core run (SKIPPY_CORE_EYES=1, the coder can't look for itself) in a project whose `.skippy-check.json` draws its
work (a `show`), unless it says `"reference": false`. Anything failing (no search, no picture, Core busy) says nothing: never a block.
The picture and sheet are kept with the work check's pictures, outside the repo (LOOK_DIR/<clone>/reference.*).
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eyes  # noqa: E402
import work_check  # noqa: E402

SEARCH_URL = os.environ.get("SKIPPY_SEARCH_URL") or os.environ.get("PURPLE_SEARCH_URL") or "http://skippy-core:7360"   # SearXNG
CANDIDATES = 9                       # pictures on the contact sheet
TILE = 360                           # each one's longest side on the sheet, pixels
KEEP_SIDE = 1600                     # the chosen picture is kept no bigger than this
FETCH_TIMEOUT = 15
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SkippyReference/1"

QUERY_PROMPT = ("Here is a task someone gave a builder:\n<task>\n{task}\n</task>\nWhat is the one thing it asks to be made, built or "
                "drawn? Reply with only a short image-search query (2 to 6 words) for photos of a real one: the thing's plain everyday "
                "name, not a nickname, a pet name or a style (a cartoon, a toy, a logo) unless the task itself asks for that style, and "
                "the word photo. Nothing else. If the task asks for nothing that can be pictured, reply NONE.")
PICK_PROMPT = ("These are {n} numbered pictures found by searching for \"{query}\". Which {k} are the best references for what "
               "\"{query}\" looks like: each a photo or faithful picture of a real one (never a cartoon, a character, a toy or a "
               "logo), the thing itself, whole and uncut, clearly seen, the everyday version most people would picture, from a view "
               "it is easily recognised from, with no text over it, no collage and nothing else competing with it; and different "
               "examples of it from one another? Reply with only their {k} numbers, best first.")
SHEET_PROMPT = ("These {k} pictures, side by side, are each an example of \"{query}\". Write a reference sheet that a builder will "
                "follow and a reviewer will check a made one against: a numbered list of 5 to 8 things that ALL {k} examples share, "
                "the things that make any \"{query}\" recognisable, the way a child's drawing of one is still recognised: its main "
                "parts and their shapes, where each sits relative to the others, rough proportions (sizes relative to each other, as "
                "fractions or ratios) and the outline it makes. Look at each picture for each item, and leave out anything only some "
                "of them have (a paint colour, an emblem, a number, a name, a small fitting): what they all have is the kind, the "
                "rest is one example. Describe the thing alone: nothing about what it stands on, what is around or behind it, or "
                "other objects beside it, and nothing about how big it is in the picture. Only the list: no advice, nothing about the "
                "photos, nothing after the last item.")
PHOTOS = "wikicommons.images"       # searched first: photos of real things (the web's own image search, first, gave the 35B "choo-choo"
                                     # clipart with faces, and "they all have a face" became a fault on every train)
REFERENCES = 3                       # examples the sheet is written from: what they all share is the kind, not one example



def _clone_dir(clone: str) -> str:
    return os.path.join(work_check.LOOK_DIR, hashlib.sha256(os.path.abspath(clone).encode()).hexdigest()[:12])


def wanted(clone: str, env=os.environ) -> bool:
    """A Core run in a project that draws its work, which hasn't switched the reference off."""
    if env.get("SKIPPY_CORE_EYES") != "1":
        return False
    cfg = work_check._config(clone)
    if cfg.get("reference") is False:
        return False
    return any(isinstance(c, dict) and c.get("show") for c in cfg.get("checks") or [])


def search(query: str, base: str = SEARCH_URL, get=None, engines: str = "") -> list[dict]:
    """SearXNG's image results for `query` (only those `engines`, when named): dicts with `src` (the picture), `thumb` and `page`."""
    which = {"engines": engines} if engines else {"categories": "images"}     # a category would add its engines to the named ones
    url = base.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, **which, "format": "json"})
    raw = get(url) if get else _get(url, limit=8 * 1024 * 1024)
    out = []
    for r in (json.loads(raw.decode("utf-8", "replace")) or {}).get("results") or []:
        src = r.get("img_src") or ""
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http"):
            thumb = r.get("thumbnail_src") or ""
            out.append({"src": src, "thumb": ("https:" + thumb) if thumb.startswith("//") else thumb, "page": r.get("url") or ""})
    return out


def _get(url: str, limit: int = eyes.MAX_BYTES) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "image/*,application/json;q=0.9,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("too big")
    return data


def _image(data: bytes):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    if min(im.size) < 120:
        raise ValueError("too small to be a reference")
    return im.convert("RGB")


def contact_sheet(images: list) -> bytes:
    """The pictures side by side, three to a row, each numbered from 1 in a corner box. A PNG."""
    from PIL import Image, ImageDraw
    cols = 3
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * TILE, rows * TILE), "white")
    draw = ImageDraw.Draw(sheet)
    for i, im in enumerate(images):
        im = im.copy()
        im.thumbnail((TILE - 8, TILE - 8))
        x, y = (i % cols) * TILE, (i // cols) * TILE
        sheet.paste(im, (x + (TILE - im.width) // 2, y + (TILE - im.height) // 2))
        draw.rectangle((x + 2, y + 2, x + 44, y + 40), fill="black")
        draw.text((x + 12, y + 8), str(i + 1), fill="yellow", font_size=28)
    out = io.BytesIO()
    sheet.save(out, "PNG")
    return out.getvalue()


def side_by_side(images: list):
    """The chosen examples in one row, each the same height, as one picture (one image for every review, not three)."""
    from PIL import Image
    h = min(KEEP_SIDE // max(len(images), 1) * 3 // 4, max(im.height for im in images))
    rows = [im.resize((max(1, round(im.width * h / im.height)), h), Image.LANCZOS) for im in images]
    gap = 12
    out = Image.new("RGB", (sum(r.width for r in rows) + gap * (len(rows) - 1), h), (40, 40, 40))
    x = 0
    for r in rows:
        out.paste(r, (x, 0))
        x += r.width + gap
    out.thumbnail((KEEP_SIDE * 2, KEEP_SIDE))
    return out


def prepare(clone: str, task: str, url: str, key: str, post=None, get=None, base: str = SEARCH_URL) -> dict | None:
    """Find, pick and describe the reference for `task`; keep it for the work check and return it as
    {"image", "sheet", "query", "source", "made"}, or None when the task pictures nothing or nothing could be found."""
    query = eyes.ask(url, key, None, "", QUERY_PROMPT.format(task=task), max_tokens=40, post=post, model=eyes.REVIEW_MODEL,
                     system="You answer with only what is asked.").strip().strip('"').splitlines()[0].strip()
    if not query or query.upper().startswith("NONE"):
        return None
    fetch = get or _get

    def load(r):
        for src in filter(None, (r["thumb"], r["src"])):
            try:
                return r, _image(fetch(src))
            except Exception:                                    # noqa: BLE001 — a dead link or not a picture: the next one
                continue
        return None

    picked = []
    plain = re.sub(r"\s+photos?$", "", query, flags=re.I) or query
    for found in (lambda: search(plain, base=base, get=get, engines=PHOTOS), lambda: search(query, base=base, get=get)):
        try:
            found = [r for r in found() if r["src"] not in {p["src"] for p, _ in picked}]
        except Exception:                                        # noqa: BLE001 — that search failed: the next one
            continue
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            for got in pool.map(load, found[:CANDIDATES * 3]):
                if got and len(picked) < CANDIDATES:
                    picked.append(got)
        if len(picked) >= REFERENCES:
            break                                               # enough real photos: the web's cartoons and clipart never get in
    if not picked:
        return None
    order = [0]
    if len(picked) > 1:
        k = min(REFERENCES, len(picked))
        said = eyes.ask(url, key, contact_sheet([im for _, im in picked]), "image/png",
                        PICK_PROMPT.format(n=len(picked), k=k, query=query), max_tokens=20, post=post,
                        model=eyes.REVIEW_MODEL, system="You answer with only what is asked.")
        order = []
        for n in re.findall(r"\d+", said):
            n = int(n) - 1
            if 0 <= n < len(picked) and n not in order:
                order.append(n)
        order = order[:k] + [n for n in range(len(picked)) if n not in order][:k - len(order[:k])]   # fewer named: the next by search rank
    chosen = []
    for n in order:
        result, im = picked[n]
        try:
            im = _image(fetch(result["src"]))                   # the full picture, when the search showed a thumbnail
        except Exception:                                        # noqa: BLE001 — the thumbnail will do
            pass
        chosen.append((result, im))
    folder = _clone_dir(clone)
    os.makedirs(folder, exist_ok=True)
    image = os.path.join(folder, "reference.jpg")
    side_by_side([im for _, im in chosen]).save(image, "JPEG", quality=90)
    data, mime = eyes.picture(image)
    sheet = eyes.ask(url, key, data, mime, SHEET_PROMPT.format(query=query, k=len(chosen)), max_tokens=1200, post=post,
                     model=eyes.REVIEW_MODEL, system="You describe pictures precisely.")
    sources = [r["page"] or r["src"] for r, _ in chosen]
    ref = {"image": image, "sheet": sheet, "query": query, "source": sources[0], "sources": sources, "made": time.time()}
    with open(os.path.join(folder, "reference.json"), "w", encoding="utf-8") as f:
        json.dump(ref, f)
    return ref


def load(clone: str, started: float) -> dict | None:
    """This run's reference (made since `started`), or None."""
    try:
        with open(os.path.join(_clone_dir(clone), "reference.json"), encoding="utf-8") as f:
            ref = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(ref, dict) or float(ref.get("made") or 0) < started - 60 or not os.path.isfile(str(ref.get("image") or "")):
        return None
    return ref


def told(ref: dict) -> str:
    """What the builder is told before it starts."""
    return (f"Before you start: the best pictures of what was asked were found (a search for \"{ref['query']}\"; side by side at "
            f"{ref['image']}, from {', '.join(ref.get('sources') or [ref['source']])}), and Skippy Core's big model wrote what they all "
            f"share, what makes it recognisable:\n\n{ref['sheet']}\n\n"
            "Make your work read as that, at a glance. Each drawing of your work will be compared with these pictures, item by item.")


def main() -> int:
    """UserPromptSubmit: find the reference for the prompt and hand its sheet to the run. Never blocks."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        clone = str(event.get("cwd") or os.getcwd())
        task = str(event.get("prompt") or "").strip()[:eyes.TASK_CHARS]
        if not task or not wanted(clone):
            return 0
        url, key = work_check._eyes(os.environ)
        ref = prepare(clone, task, url, key)
    except Exception as e:                                       # noqa: BLE001 — no reference this time: the run goes on without one
        print(f"reference: none ({e})", file=sys.stderr)
        return 0
    if ref:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": told(ref)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
