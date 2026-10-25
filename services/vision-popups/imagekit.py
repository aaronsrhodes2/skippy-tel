"""Picture plumbing for vision-popups, with no GPU and no model: decode what the person attached, encode what goes back,
and the small pieces of geometry the tools share (a cut-out on pure black, normals from depth, mask de-duplication).
Pure numpy + Pillow, so the tests run anywhere."""
from __future__ import annotations

import base64
import binascii
import io

import numpy as np
from PIL import Image, ImageDraw, ImageOps

MAX_INPUT_BYTES = 16_000_000     # the chat sends ~1 MB (FILE_B64 1.1 MB of base64); the agent's screenshots up to 12 MB
MAX_INPUT_PIXELS = 40_000_000    # a decompression bomb stops here, not in the card
MAX_OUTPUT_BYTES = 24_000_000    # the reporter hands up to CLIP_MAX (25 MB) to the portal
Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS

# The passthrough palette (D:/Aaron/development/CLAUDE.md): black is see-through on the Viture glasses.
GREEN, AMBER, CYAN, VIOLET, RED, WHITE = (0, 255, 136), (255, 170, 0), (0, 204, 255), (129, 140, 248), (255, 68, 68), (255, 255, 255)
PALETTE = [GREEN, CYAN, AMBER, VIOLET, RED, WHITE]


class BadInput(ValueError):
    """The request itself is wrong (the person can fix it): a 400, never a retry."""


def decode(data) -> tuple[Image.Image, Image.Image | None]:
    """Attached picture (bytes or base64, with or without a data: prefix) -> (RGB picture, its alpha or None), upright."""
    if isinstance(data, str):
        data = data.split(",", 1)[1] if data.startswith("data:") else data
        if len(data) > MAX_INPUT_BYTES * 4 // 3 + 4:
            raise BadInput("the picture is too big")
        try:
            data = base64.b64decode(data, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise BadInput("the picture isn't valid base64") from exc
    if not data:
        raise BadInput("a picture is needed")
    if len(data) > MAX_INPUT_BYTES:
        raise BadInput("the picture is too big")
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Image.DecompressionBombError as exc:
        raise BadInput("the picture has too many pixels") from exc
    except Exception as exc:  # noqa: BLE001 — Pillow raises many kinds for junk
        raise BadInput("that isn't a picture this can read") from exc
    alpha = None
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        alpha = rgba.getchannel("A")
        if alpha.getextrema() == (255, 255):
            alpha = None
        img = rgba
    return img.convert("RGB"), alpha


def encode(img: Image.Image, name: str) -> dict:
    """A picture going back: PNG, unless that is over the hand-over limit (then lossless WebP, then a high JPEG/WebP)."""
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=img.width * img.height <= 1_000_000)   # optimize costs seconds on big pictures
    if out.tell() <= MAX_OUTPUT_BYTES:
        return {"name": name, "mime": "image/png", "bytes": out.getvalue()}
    for fmt, kw, mime in (("WEBP", {"lossless": True}, "image/webp"),
                          ("WEBP", {"quality": 92}, "image/webp") if img.mode in ("RGBA", "LA") else ("JPEG", {"quality": 92}, "image/jpeg")):
        out = io.BytesIO()
        img.save(out, format=fmt, **kw)
        if out.tell() <= MAX_OUTPUT_BYTES:
            return {"name": name, "mime": mime, "bytes": out.getvalue()}
    raise BadInput("the result is too big to hand back")


def as_json(output: dict) -> dict:
    return {"name": output["name"], "mime": output["mime"], "b64": base64.b64encode(output["bytes"]).decode()}


def fit_side(img: Image.Image, longest: int) -> Image.Image:
    """Shrink (never grow) so the longest side is at most `longest`."""
    if max(img.size) <= longest:
        return img
    scale = longest / max(img.size)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def cutout(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    """The subject on transparency (RGBA)."""
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def on_black(rgb: Image.Image, alpha: Image.Image, lift: int = 0) -> Image.Image:
    """The subject on pure #000000, which the Viture glasses show as see-through. `lift` (0-64) raises the subject's
    darkest pixels so they don't vanish with the background: its own black would be invisible too."""
    px = np.asarray(rgb, dtype=np.float32)
    a = np.asarray(alpha, dtype=np.float32)[..., None] / 255.0
    lift = max(0, min(64, int(lift or 0)))
    if lift:
        px = lift + px * (255.0 - lift) / 255.0
    return Image.fromarray(np.clip(px * a + 0.5, 0, 255).astype(np.uint8), "RGB")


def gray16(values: np.ndarray) -> Image.Image:
    """A float map, normalised to 0..65535, as a 16-bit greyscale PNG (bright = high)."""
    v = values.astype(np.float64)
    lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
    v = (v - lo) / (hi - lo) if hi > lo else np.zeros_like(v)
    return Image.fromarray((v * 65535 + 0.5).astype(np.uint16))


def gray8(values: np.ndarray) -> Image.Image:
    v = values.astype(np.float64)
    lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
    v = (v - lo) / (hi - lo) if hi > lo else np.zeros_like(v)
    return Image.fromarray((v * 255 + 0.5).astype(np.uint8), "L")


def normals_from_depth(disparity: np.ndarray, strength: float = 4.0) -> Image.Image:
    """A tangent-space style normal map (RGB = XYZ * 0.5 + 0.5, Z towards the viewer) from relative depth.

    Depth Anything gives disparity (near = large); the surface is read as a height field of it, normalised so the result
    doesn't depend on the image's scale. Cheap (two gradients) and good enough for AR lighting and parallax, not metric."""
    d = disparity.astype(np.float64)
    span = float(d.max() - d.min()) or 1.0
    d = (d - d.min()) / span * max(d.shape) / 64.0 * strength
    dzdy, dzdx = np.gradient(d)
    n = np.stack([-dzdx, dzdy, np.ones_like(d)], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return Image.fromarray(((n * 0.5 + 0.5) * 255 + 0.5).astype(np.uint8), "RGB")


def mask_bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def dedupe_masks(masks: list[np.ndarray], scores: list[float], iou_limit: float = 0.7, min_area: int = 1) -> list[int]:
    """Which masks to keep, best first: drop the tiny ones and any that overlap a better one by more than `iou_limit`."""
    order = sorted(range(len(masks)), key=lambda i: -scores[i])
    kept: list[int] = []
    flat = [m.reshape(-1).astype(bool) for m in masks]
    areas = [int(f.sum()) for f in flat]
    for i in order:
        if areas[i] < min_area:
            continue
        if all((flat[i] & flat[j]).sum() / max(1, (flat[i] | flat[j]).sum()) <= iou_limit for j in kept):
            kept.append(i)
    return kept


def overlay_masks(rgb: Image.Image, masks: list[np.ndarray], opacity: float = 0.55) -> Image.Image:
    """Every mask painted over the photo in the palette's colours, each outlined."""
    base = np.asarray(rgb, dtype=np.float32).copy()
    for k, m in enumerate(masks):
        colour = np.array(PALETTE[k % len(PALETTE)], dtype=np.float32)
        sel = m.astype(bool)
        base[sel] = base[sel] * (1 - opacity) + colour * opacity
    out = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")
    return out


def draw_boxes(rgb: Image.Image, found: list[dict]) -> Image.Image:
    """Boxes and labels over the photo (detect's picture)."""
    out = rgb.copy()
    draw = ImageDraw.Draw(out)
    width = max(2, round(max(out.size) / 400))
    labels = sorted({f["label"] for f in found})
    for f in found:
        colour = PALETTE[labels.index(f["label"]) % len(PALETTE)]
        x0, y0, x1, y1 = f["box"]
        draw.rectangle([x0, y0, x1, y1], outline=colour, width=width)
        tag = f"{f['label']} {f['score']:.2f}"
        tw = draw.textlength(tag)
        draw.rectangle([x0, max(0, y0 - 14), x0 + tw + 6, max(14, y0)], fill=(0, 0, 0))
        draw.text((x0 + 3, max(0, y0 - 13)), tag, fill=colour)
    return out


def detection_prompt(text: str) -> str:
    """"find the mugs and the keyboard" -> "mugs. keyboard." (Grounding DINO wants lower-case phrases, each ending in a dot)."""
    t = str(text or "").lower().strip()
    for lead in ("find me ", "find the ", "find ", "detect the ", "detect ", "where are the ", "where is the ", "where's the "):
        if t.startswith(lead):
            t = t[len(lead):]
            break
    parts = []
    for chunk in t.replace(";", ",").replace(" and ", ",").replace(".", ",").split(","):
        chunk = chunk.strip()
        for art in ("the ", "a ", "an ", "all ", "any "):
            if chunk.startswith(art):
                chunk = chunk[len(art):]
        if chunk and chunk not in parts and chunk not in ("the", "a", "an", "all", "any", "me"):
            parts.append(chunk[:60])
    if not parts:
        raise BadInput("say what to look for")
    return " ".join(p + "." for p in parts[:12])
