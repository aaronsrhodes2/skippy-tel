"""The reference picture (the Captain, 2026-11-03: "we need a better picture inspection and description before starting, and our goal
re-check should re-compare the best source image it can find").

In the MineBench fault-loop series the coder and its reviewer both worked from three words, "a choo-choo train": the reviewer's "no
fault left" meant nothing was plainly broken, never that a stranger would know the thing at a glance, and it passed builds a blind
judge lost every time. So before a run that draws its work starts, the best picture of what was asked is found and looked at:

  1. Core's big model turns the task into a short image-search query for the one thing it asks to be made;
  2. SearXNG on skippy-core (tailnet, no key) finds pictures of it; the first few that open become one numbered contact sheet, and the
     big model picks the best reference (the thing itself, whole, clearly seen, the version most people would picture);
  3. it writes the reference sheet from that picture: what makes it recognisable, part by part, with proportions, silhouette and what
     dominates the whole.

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
                "drawn? Reply with only a short image-search query (2 to 6 words) for a clear picture of that thing, nothing else. If "
                "the task asks for nothing that can be pictured, reply NONE.")
PICK_PROMPT = ("These are {n} numbered pictures found by searching for \"{query}\". The task they are for:\n<task>\n{task}\n</task>\n"
               "Which ONE is the best reference for what the task asks to be made: the thing itself, whole and uncut, clearly seen, "
               "the everyday version most people would picture, from a view it is easily recognised from, with no text over it, no "
               "collage and nothing else competing with it? Reply with only its number.")
SHEET_PROMPT = ("This is the reference picture for this task:\n<task>\n{task}\n</task>\nWrite the reference sheet a builder will "
                "follow and a reviewer will check the work against: a numbered list of 6 to 12 things that make the thing it shows "
                "recognisable as \"{query}\", each one something you can see in this picture: its main parts and their shapes, where "
                "each sits relative to the others, rough proportions (sizes relative to each other, as fractions or ratios), its "
                "colours and the outline it makes, and last, that the thing itself must fill and dominate the work. Describe only the "
                "thing, the way any example of it would be recognised: leave out its surroundings and background, other objects "
                "beside it, and markings only this one example has (numbers, names, logos). No advice on how to build it, nothing "
                "about the photo itself.")


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


def search(query: str, base: str = SEARCH_URL, get=None) -> list[dict]:
    """SearXNG's image results for `query`: dicts with `src` (the picture), `thumb` and `page`."""
    url = base.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "categories": "images", "format": "json"})
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


def prepare(clone: str, task: str, url: str, key: str, post=None, get=None, base: str = SEARCH_URL) -> dict | None:
    """Find, pick and describe the reference for `task`; keep it for the work check and return it as
    {"image", "sheet", "query", "source", "made"}, or None when the task pictures nothing or nothing could be found."""
    query = eyes.ask(url, key, None, "", QUERY_PROMPT.format(task=task), max_tokens=40, post=post, model=eyes.REVIEW_MODEL,
                     system="You answer with only what is asked.").strip().strip('"').splitlines()[0].strip()
    if not query or query.upper().startswith("NONE"):
        return None
    found = search(query, base=base, get=get)
    fetch = get or _get

    def load(r):
        for src in filter(None, (r["thumb"], r["src"])):
            try:
                return r, _image(fetch(src))
            except Exception:                                    # noqa: BLE001 — a dead link or not a picture: the next one
                continue
        return None

    picked = []
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for got in pool.map(load, found[:CANDIDATES * 3]):
            if got and len(picked) < CANDIDATES:
                picked.append(got)
    if not picked:
        return None
    choice = 0
    if len(picked) > 1:
        said = eyes.ask(url, key, contact_sheet([im for _, im in picked]), "image/png",
                        PICK_PROMPT.format(n=len(picked), query=query, task=task), max_tokens=10, post=post, model=eyes.REVIEW_MODEL,
                        system="You answer with only what is asked.")
        n = re.search(r"\d+", said)
        choice = min(max(int(n.group()) - 1, 0), len(picked) - 1) if n else 0
    result, im = picked[choice]
    try:
        im = _image(fetch(result["src"]))                       # the full picture, when the search showed a thumbnail
    except Exception:                                            # noqa: BLE001 — the thumbnail will do
        pass
    im.thumbnail((KEEP_SIDE, KEEP_SIDE))
    folder = _clone_dir(clone)
    os.makedirs(folder, exist_ok=True)
    image = os.path.join(folder, "reference.jpg")
    im.save(image, "JPEG", quality=90)
    data, mime = eyes.picture(image)
    sheet = eyes.ask(url, key, data, mime, SHEET_PROMPT.format(task=task, query=query), max_tokens=1200, post=post,
                     model=eyes.REVIEW_MODEL, system="You describe pictures precisely.")
    ref = {"image": image, "sheet": sheet, "query": query, "source": result["page"] or result["src"], "made": time.time()}
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
    return (f"Before you start: the best picture of what was asked was found (a search for \"{ref['query']}\"; it is at "
            f"{ref['image']}, from {ref['source']}), and Skippy Core's big model wrote what makes it recognisable:\n\n{ref['sheet']}\n\n"
            "Make your work read as that, at a glance. Each drawing of your work will be compared with this picture, item by item.")


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
