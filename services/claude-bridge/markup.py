"""The marks the Captain draws on a screenshot, read for Skippy Core's text-only coder (the Captain, 2026-10-31: "I often use screenshots with
visual markups to find and fix UI bugs, I need that capability").

A whole-picture description turns a red circle into "there is a red circle", and which button it was is lost. So, for every picture he
attaches to a Core run, before the run starts:

  1. FIND: Core's vision slot (Qwen3-VL) is asked where the hand-drawn marks are (circles, boxes, arrows, underlines, scribbles), as
     boxes in 0-1000 coordinates (its grounding); they come back in the picture's own pixels;
  2. LOOK CLOSER: each mark is cropped (a circle or box with a margin around it; an arrow around the place it points at) and that crop
     is described on its own: exactly which element the mark is on, with its text copied out;
  3. SAY: one line per mark, numbered, with its colour, shape, place and what is under it, ahead of the whole-picture description.

The coder then knows "mark 2, the red arrow at (1078,331), points at the 'Downloads' menu item" and can find that element in the code.
Marks the in-portal annotator drew (static/skippy-annotate.js) already come with a legend in the message; this reads them the same way.
Nothing here raises into a run: a picture whose marks can't be read just gets the plain description (and says so).
"""
from __future__ import annotations

import io
import json
import re

import eyes

MAX_MARKS = 8
FIND_PROMPT = ('Someone drew annotations BY HAND on top of this screenshot to point things out: circles, ovals, boxes, arrows, underlines, '
               'scribbles or highlights, in a pen colour. Find every one of them. Reply with JSON only, no words: '
               '[{"color": "red", "shape": "circle|box|arrow|underline|scribble|highlight", "bbox_2d": [x1, y1, x2, y2], "tip": [x, y] or null}] '
               'in coordinates from 0 to 1000 relative to the image width and height; "tip" is the point an arrow points at. '
               'Do not list the page\'s own design (its borders, buttons, icons or coloured text). [] if nobody drew anything.')
LOOK_PROMPT = ("This is a close-up of part of a screenshot, around a {color} {shape} that the Captain drew by hand to point something out. "
               "Say exactly which element(s) the mark is {where}: what kind of element (button, label, menu item, image, input, card ...), "
               "ALL its text copied exactly as printed, its colours, and anything that looks wrong about it (cut off, overlapping, misaligned, "
               "wrong colour, empty). Two or three sentences, no opinions about the mark itself.")
_JSON = re.compile(r"\[.*\]", re.S)


def _parse(text: str) -> list[dict]:
    m = _JSON.search(text or "")
    if not m:
        return []
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for g in got if isinstance(got, list) else []:
        box = g.get("bbox_2d") if isinstance(g, dict) else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            continue
        x1, y1, x2, y2 = (max(0.0, min(1000.0, float(v))) for v in box)
        if x2 - x1 < 2 or y2 - y1 < 2:
            if not (x2 - x1 >= 2 or y2 - y1 >= 2):            # a straight underline may be one pixel tall: keep it if it is long
                continue
        tip = g.get("tip")
        tip = [float(tip[0]), float(tip[1])] if isinstance(tip, list) and len(tip) == 2 and all(isinstance(v, (int, float)) for v in tip) else None
        out.append({"color": str(g.get("color") or "coloured")[:20], "shape": str(g.get("shape") or "mark")[:20],
                    "box": (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), "tip": tip})
    return out[:MAX_MARKS]


def _crop(img, mark: dict, w: int, h: int):
    """The part of the picture to look at for one mark, in pixels: (left, top, right, bottom)."""
    x1, y1, x2, y2 = (mark["box"][0] * w / 1000, mark["box"][1] * h / 1000, mark["box"][2] * w / 1000, mark["box"][3] * h / 1000)
    if mark["shape"] == "arrow" and mark["tip"]:
        tx, ty = mark["tip"][0] * w / 1000, mark["tip"][1] * h / 1000
        r = max(w, h) * 0.14
        return (max(0, tx - r), max(0, ty - r), min(w, tx + r), min(h, ty + r))
    mx, my = max(24, (x2 - x1) * 0.25), max(24, (y2 - y1) * 0.25)
    if mark["shape"] == "underline":
        my = max(my, h * 0.06)                               # an underline marks what is just above it
    return (max(0, x1 - mx), max(0, y1 - my), min(w, x2 + mx), min(h, y2 + my))


def read(path: str, url: str, key: str, post=None) -> str:
    """The marks on the picture at `path`, one numbered line each, or "" when there are none. Raises only if Core can't be asked at all."""
    from PIL import Image                                    # noqa: PLC0415 — only a Core run with a picture needs it
    data = open(path, "rb").read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    mime = eyes.IMAGE_TYPES.get("." + path.rsplit(".", 1)[-1].lower(), "image/jpeg")
    marks = _parse(eyes.ask(url, key, data, mime, FIND_PROMPT, max_tokens=700, post=post))
    lines = []
    for n, mark in enumerate(marks, 1):
        box = _crop(img, mark, w, h)
        buf = io.BytesIO()
        img.crop(tuple(int(v) for v in box)).save(buf, "JPEG", quality=90)
        where = "pointing at (the arrow's tip is near the middle)" if mark["shape"] == "arrow" else (
            "underlining" if mark["shape"] == "underline" else "drawn around or on")
        try:
            seen = eyes.ask(url, key, buf.getvalue(), "image/jpeg", LOOK_PROMPT.format(color=mark["color"], shape=mark["shape"], where=where),
                            max_tokens=400, post=post).strip()
        except Exception as exc:  # noqa: BLE001 — one unreadable close-up must not lose the others
            seen = f"(the close-up couldn't be read: {type(exc).__name__})"
        px = lambda x, y: f"({round(x * w / 1000)},{round(y * h / 1000)})"
        place = f"from {px(*mark['box'][:2])} to {px(*mark['box'][2:])}"
        if mark["shape"] == "arrow" and mark["tip"]:
            place += f", pointing at {px(*mark['tip'])}"
        lines.append(f"Mark {n}: {mark['color']} {mark['shape']} {place}. On it: {seen}")
    return "\n".join(lines)
