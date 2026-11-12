"""A voxel build as a picture: four views of it (front-left, front-right, back, top) in one PNG, for a chat to show
(the agent prints `SKIPPY_IMAGE: <png>`). It opens viewer.py's page in headless Chromium (Playwright's own; three.js comes from the
CDN, so it needs the internet) and photographs it.
    python tools/voxel/render.py build.json out.png ["Title"]   [--html out.html] [--glb out.glb: a 3D model for the chat]
        [--views "front left" --size 1600x1100 --fit: one big view, e.g. for a reviewer that must see the work, not a thumbnail of it]
Prints the block count and the image's path. MineBench's format: {"tool": "voxel.exec", "input": {"code", "gridSize", "palette",
"seed"}}; check a build's code first with `node tools/voxel/runtime.mjs build.json` (errors, block count, bounds).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from viewer import make_html  # noqa: E402

VIEWS = ((35, 25, "front left"), (-35, 25, "front right"), (180, 30, "back"), (0, 80, "top"))


FILL = 0.9                            # --fit: how much of a view the build should fill
MAX_GLB = 12 * 1024 * 1024            # what the agent may show in the chat (the bridge's MAX_SHOT_BYTES)


def render(build: dict, out: Path, title: str, html_out: Path | None = None, size=(900, 640), timeout_s: float = 120,
           glb_out: Path | None = None, zoom: float = 1.9, views: tuple[str, ...] = (), fit: bool = False) -> dict:
    """`views`: the VIEWS to draw, by name (all four unless given); one view is drawn alone, at `size`. `fit`: each view is drawn
    again with the camera moved in until the build fills about 90% of it (the default distance frames its bounding sphere,
    so a long, low build fills under half the frame)."""
    chosen = tuple(v for v in VIEWS if not views or v[2] in views) or VIEWS
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageDraw
    html = make_html(build, title, "")
    page_file = Path(html_out) if html_out else Path(tempfile.mkdtemp()) / "build.html"
    page_file.write_text(html, encoding="utf-8")
    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(page_file.resolve().as_uri())
        try:
            page.wait_for_function("window.__ready === true", timeout=timeout_s * 1000)
        except Exception:
            browser.close()
            raise RuntimeError("the build did not draw: " + ("; ".join(errors)[:300] or "timed out (no internet for three.js?)"))
        stats = page.evaluate("window.__stats")
        glb_note = None
        if glb_out:
            data = bytes(page.evaluate("window.__glb()"))
            if len(data) <= MAX_GLB:
                Path(glb_out).parent.mkdir(parents=True, exist_ok=True)
                Path(glb_out).write_bytes(data)
                glb_note = {"glb": str(glb_out), "glb_bytes": len(data), "quads": page.evaluate("window.__glbQuads")}
            else:
                glb_note = {"glb": None, "why": f"the model is {len(data) // 1048576} MB, over the chat's 12 MB: show the picture only"}
        page.add_style_tag(content="#hud,#stats{display:none}")
        for az, el, _ in chosen:
            page.evaluate(f"window.__view({az}, {el}, {zoom})")
            page.wait_for_timeout(150)
            shot = Image.open(io.BytesIO(page.screenshot(timeout=timeout_s * 1000)))   # 295K blocks took over 30 s a view
            covered = _covered(shot) if fit else 0
            if 0 < covered < 0.85:
                page.evaluate(f"window.__view({az}, {el}, {zoom * covered / FILL})")
                page.wait_for_timeout(150)
                shot = Image.open(io.BytesIO(page.screenshot(timeout=timeout_s * 1000)))
            shots.append(shot)
        browser.close()
    w, h = shots[0].size
    cols = 2 if len(shots) > 1 else 1
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, h * rows + 40), (13, 17, 23))
    for i, (img, (_, _, name)) in enumerate(zip(shots, chosen)):
        sheet.paste(img, ((i % cols) * w, 40 + (i // cols) * h))
        ImageDraw.Draw(sheet).text(((i % cols) * w + 10, 40 + (i // cols) * h + 8), name, fill=(139, 148, 158))
    ImageDraw.Draw(sheet).text((12, 12), f"{title}  ·  {stats['blocks']:,} blocks  ·  {stats['size'][0]} x {stats['size'][1]} x {stats['size'][2]}",
                               fill=(230, 237, 243))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)
    return {**stats, "image": str(out), "html": str(page_file) if html_out else None, **(glb_note or {})}


def _covered(shot) -> float:
    """How much of the view the build fills: the larger of its width and height share, measured off the centre (the camera
    looks at the build's middle, so moving it in grows the build about the centre)."""
    mask = shot.convert("L").point(lambda v: 255 if v > 12 else 0)
    box = mask.getbbox()
    if not box:
        return 0
    cx, cy = shot.width / 2, shot.height / 2
    return max(max(cx - box[0], box[2] - cx) / cx, max(cy - box[1], box[3] - cy) / cy)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build")
    ap.add_argument("out")
    ap.add_argument("title", nargs="?", default="voxel build")
    ap.add_argument("--html")
    ap.add_argument("--zoom", type=float, default=1.9, help="camera distance in build sizes (smaller is closer)")
    ap.add_argument("--views", help="the views to draw, comma separated (front left, front right, back, top); all four by default")
    ap.add_argument("--size", default="900x640", help="each view's size in pixels, WxH")
    ap.add_argument("--fit", action="store_true", help="move the camera in until the build fills each view")
    ap.add_argument("--glb", help="also write the build as a 3D model (binary glTF) for the chat's inline viewer")
    a = ap.parse_args(argv)
    got = render(json.loads(Path(a.build).read_text(encoding="utf-8")), Path(a.out), a.title, Path(a.html) if a.html else None,
                 glb_out=Path(a.glb) if a.glb else None, zoom=a.zoom, size=tuple(int(n) for n in a.size.lower().split("x")),
                 views=tuple(v.strip() for v in (a.views or "").split(",") if v.strip()), fit=a.fit)
    print(json.dumps(got))
    return 0


if __name__ == "__main__":
    sys.exit(main())
