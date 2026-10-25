"""The pop-up tools: each takes the attached picture plus a few options and gives back pictures, text and data.

    remove_background  BiRefNet: the subject on transparency, and on pure black (see-through on the Viture glasses)
    upscale            Real-ESRGAN x4 (a transparent picture keeps its alpha)
    depth              Depth Anything V2: relative depth (8- and 16-bit) and a normal map from it
    segment            SAM 2.1: masks from points, a box, words (Grounding DINO finds the box) or everything
    detect             Grounding DINO: boxes for what the words name ("find the mugs")
    ocr                PaddleOCR-VL: documents, tables, formulas, charts, 100+ languages
    pose               ViTPose++ on RT-DETR's people: body keypoints (COCO-17), for rigging and hand-off to AR

Every runner returns {"text": one line for the chat, "outputs": [encoded pictures], "data": {...}}."""
from __future__ import annotations

import html
import re

import numpy as np
from PIL import Image

import imagekit as ik
import models as m
from imagekit import BadInput

TOOLS: dict[str, dict] = {}


def tool(name: str, default: str, choices: tuple, summary: str):
    def wrap(fn):
        TOOLS[name] = {"run": fn, "default": default, "models": choices, "summary": summary}
        return fn
    return wrap


def pick(name: str, params: dict) -> str:
    want = str(params.get("model") or TOOLS[name]["default"])
    if want not in TOOLS[name]["models"]:
        raise BadInput(f"{name} runs on {', '.join(TOOLS[name]['models'])}")
    return want


def _imagenet(img: Image.Image, size: tuple[int, int], dtype):
    import torch
    x = torch.from_numpy(np.asarray(img.resize(size, Image.BILINEAR), dtype=np.float32) / 255.0).permute(2, 0, 1)
    mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
    return ((x - mean) / std).unsqueeze(0).to("cuda", dtype=dtype)


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


# ── remove_background ─────────────────────────────────────────────────────────

def alpha_matte(rgb: Image.Image, which: str) -> Image.Image:
    import torch
    (net,) = m.use(which)
    side = m.MODELS[which].extra["size"]
    with torch.inference_mode():
        pred = net(_imagenet(rgb, (side, side), torch.float16))[-1].sigmoid()
    a = pred[0, 0].float().cpu().numpy()
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8), "L").resize(rgb.size, Image.BILINEAR)


@tool("remove_background", "birefnet", ("birefnet", "birefnet_matting"),
      "the subject cut out: on transparency and on pure black (see-through on the glasses)")
def remove_background(rgb, alpha, params):
    which = pick("remove_background", params)
    matte = alpha_matte(rgb, which)
    if alpha is not None:
        matte = Image.fromarray(np.minimum(np.asarray(matte), np.asarray(alpha)), "L")
    background = str(params.get("background") or "both")
    if background not in ("transparent", "black", "both"):
        raise BadInput("background is transparent, black or both")
    outputs = []
    if background in ("transparent", "both"):
        outputs.append(ik.encode(ik.cutout(rgb, matte), "cutout"))
    if background in ("black", "both"):
        outputs.append(ik.encode(ik.on_black(rgb, matte, params.get("lift") or 0), "on_black"))
    if params.get("mask"):
        outputs.append(ik.encode(matte, "mask"))
    a = np.asarray(matte)
    box = ik.mask_bbox(a > 127)
    share = float((a > 127).mean())
    return {"text": f"Cut out the subject ({share:.0%} of the picture)." if box else "I couldn't find a clear subject to cut out.",
            "outputs": outputs, "data": {"subject_box": box, "coverage": round(share, 4)}}


# ── upscale ───────────────────────────────────────────────────────────────────

MAX_UPSCALED_SIDE = 4096
TILE, PAD = 512, 16


def _esrgan_tiled(net, rgb: Image.Image) -> Image.Image:
    import torch
    x = torch.from_numpy(np.asarray(rgb, dtype=np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
    _, _, h, w = x.shape
    out = torch.zeros((1, 3, h * 4, w * 4))
    with torch.inference_mode():
        for y0 in range(0, h, TILE):
            for x0 in range(0, w, TILE):
                ya, yb = max(0, y0 - PAD), min(h, y0 + TILE + PAD)
                xa, xb = max(0, x0 - PAD), min(w, x0 + TILE + PAD)
                tile = net(x[:, :, ya:yb, xa:xb].to("cuda", dtype=torch.float16)).float().cpu()
                ty, tx = (y0 - ya) * 4, (x0 - xa) * 4
                th, tw = (min(h, y0 + TILE) - y0) * 4, (min(w, x0 + TILE) - x0) * 4
                out[:, :, y0 * 4:y0 * 4 + th, x0 * 4:x0 * 4 + tw] = tile[:, :, ty:ty + th, tx:tx + tw]
    arr = (out[0].clamp(0, 1).permute(1, 2, 0).numpy() * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


@tool("upscale", "realesrgan_x4", ("realesrgan_x4",), "four times the pixels (Real-ESRGAN), up to 4096 px a side")
def upscale(rgb, alpha, params):
    (net,) = m.use("realesrgan_x4")
    target = min(4.0, MAX_UPSCALED_SIDE / max(rgb.size))
    if target <= 1.0:
        raise BadInput(f"the picture is already {max(rgb.size)} px; upscale stops at {MAX_UPSCALED_SIDE}")
    src = rgb
    if target < 4.0:   # shrink the input so x4 lands on the cap, rather than drawing pixels that get thrown away
        src = rgb.resize((max(1, round(rgb.width * target / 4)), max(1, round(rgb.height * target / 4))), Image.LANCZOS)
    big = _esrgan_tiled(net, src)
    size = (round(rgb.width * target), round(rgb.height * target))
    if big.size != size:
        big = big.resize(size, Image.LANCZOS)
    if alpha is not None:
        big = ik.cutout(big, alpha.resize(size, Image.BICUBIC))
    return {"text": f"Upscaled {rgb.width}x{rgb.height} to {size[0]}x{size[1]}.",
            "outputs": [ik.encode(big, "upscaled")], "data": {"size": list(size), "scale": round(target, 3)}}


# ── depth ─────────────────────────────────────────────────────────────────────

def disparity(rgb: Image.Image, which: str) -> np.ndarray:
    import torch
    (model, proc), = m.use(which)
    inputs = proc(images=rgb, return_tensors="pt").to("cuda")
    inputs["pixel_values"] = inputs["pixel_values"].to(torch.float16)
    with torch.inference_mode():
        pred = model(**inputs).predicted_depth
    pred = torch.nn.functional.interpolate(pred.unsqueeze(1).float(), size=(rgb.height, rgb.width), mode="bicubic", align_corners=False)
    return pred[0, 0].cpu().numpy()


@tool("depth", "depth_anything_v2_large", ("depth_anything_v2_large", "depth_anything_v2_small"),
      "relative depth (near = bright) and a normal map, for occlusion and parallax")
def depth(rgb, alpha, params):
    which = pick("depth", params)
    d = disparity(rgb, which)
    outputs = [ik.encode(ik.gray8(d), "depth"), ik.encode(ik.gray16(d), "depth16")]
    if params.get("normals", True):
        outputs.append(ik.encode(ik.normals_from_depth(d, float(params.get("strength") or 4.0)), "normals"))
    return {"text": "Depth map (near is bright; relative, not metres) and a normal map.", "outputs": outputs,
            "data": {"near_is": "bright", "metric": False, "size": [rgb.width, rgb.height]}}


# ── detect ────────────────────────────────────────────────────────────────────

def find(rgb: Image.Image, words: str, which: str, threshold: float = 0.4, alongside: tuple = ()) -> list[dict]:
    import torch
    (model, proc), = m.use(which, alongside=alongside)
    prompt = ik.detection_prompt(words)
    inputs = proc(images=rgb, text=prompt, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        out = model(**inputs)
    res = proc.post_process_grounded_object_detection(out, inputs["input_ids"], threshold=threshold, text_threshold=0.25,
                                                      target_sizes=[(rgb.height, rgb.width)])[0]
    labels = res.get("text_labels") or res.get("labels")
    found = [{"label": str(lab) or "?", "score": round(float(s), 3), "box": [round(float(v), 1) for v in b]}
             for s, lab, b in zip(res["scores"], labels, res["boxes"])]
    return sorted(found, key=lambda f: -f["score"])[:100]


@tool("detect", "grounding_dino_base", ("grounding_dino_base", "grounding_dino_tiny"),
      "boxes around whatever the words name (\"find the mugs\")")
def detect(rgb, alpha, params):
    which = pick("detect", params)
    found = find(rgb, params.get("text") or params.get("prompt") or "", which, float(params.get("threshold") or 0.4))
    counts: dict[str, int] = {}
    for f in found:
        counts[f["label"]] = counts.get(f["label"], 0) + 1
    said = ", ".join(f"{n} {lab}" for lab, n in counts.items()) or "nothing that matches"
    return {"text": f"Found {said}.", "outputs": [ik.encode(ik.draw_boxes(rgb, found), "boxes")], "data": {"found": found}}


# ── segment ───────────────────────────────────────────────────────────────────

def _points(params, w: int, h: int):
    pts = params.get("points") or ([params["point"]] if params.get("point") else [])
    out, labels = [], []
    for p in pts[:16]:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise BadInput("a point is [x, y] in pixels") from None
        if not (0 <= x < w and 0 <= y < h):
            raise BadInput("a point is outside the picture")
        out.append([x, y])
        labels.append(int(p[2]) if len(p) > 2 else 1)
    return out, labels


def _box(b, w: int, h: int):
    try:
        x0, y0, x1, y1 = (float(v) for v in b)
    except (TypeError, ValueError):
        raise BadInput("a box is [x0, y0, x1, y1] in pixels") from None
    x0, x1 = sorted((max(0.0, min(w, x0)), max(0.0, min(w, x1))))
    y0, y1 = sorted((max(0.0, min(h, y0)), max(0.0, min(h, y1))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise BadInput("the box is empty")
    return [x0, y0, x1, y1]


def sam_masks(rgb, which, points=None, labels=None, boxes=None, alongside: tuple = ()) -> tuple[list[np.ndarray], list[float]]:
    """One mask per box (or one for the points), the best of SAM's three guesses for each."""
    import torch
    (model, proc), = m.use(which, alongside=alongside)
    kw = {}
    if boxes:
        kw["input_boxes"] = [[list(b) for b in boxes]]
    if points:
        kw["input_points"] = [[[list(p) for p in points]]]
        kw["input_labels"] = [[list(labels)]]
    inputs = proc(images=rgb, return_tensors="pt", **kw).to("cuda")
    with torch.inference_mode():
        out = model(**inputs, multimask_output=True)
    masks = proc.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"])[0]   # objects x 3 x H x W
    scores = out.iou_scores[0].float().cpu()                                              # objects x 3
    best = scores.argmax(-1)
    return ([masks[i, int(best[i])].numpy().astype(bool) for i in range(masks.shape[0])],
            [float(scores[i, int(best[i])]) for i in range(masks.shape[0])])


def sam_everything(rgb, which, grid: int = 24, batch: int = 16) -> tuple[list[np.ndarray], list[float]]:
    """SAM's automatic masks, done by hand: a grid of single-point prompts over one image embedding, the confident ones kept,
    the overlapping ones merged away (imagekit.dedupe_masks)."""
    import torch
    (model, proc), = m.use(which)
    w, h = rgb.size
    inputs = proc(images=rgb, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        emb = model.get_image_embeddings(inputs["pixel_values"])
    xs = (np.arange(grid) + 0.5) / grid
    pts = [[float(x * w), float(y * h)] for y in xs for x in xs]
    masks, scores = [], []
    for i in range(0, len(pts), batch):
        chunk = pts[i:i + batch]
        p = proc(images=rgb, input_points=[[[q] for q in chunk]], input_labels=[[[1] for _ in chunk]], return_tensors="pt")
        with torch.inference_mode():
            out = model(input_points=p["input_points"].to("cuda"), input_labels=p["input_labels"].to("cuda"),
                        image_embeddings=emb, multimask_output=True)
        mk = proc.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"].cpu())[0]
        sc = out.iou_scores[0].float().cpu()
        best = sc.argmax(-1)
        for j in range(mk.shape[0]):
            s = float(sc[j, int(best[j])])
            if s >= 0.80:
                masks.append(mk[j, int(best[j])].numpy().astype(bool))
                scores.append(s)
    keep = ik.dedupe_masks(masks, scores, 0.7, min_area=int(w * h * 0.0015))[:96]
    return [masks[k] for k in keep], [scores[k] for k in keep]


def _union(masks: list[np.ndarray], size) -> Image.Image:
    u = np.zeros((size[1], size[0]), dtype=bool)
    for mk in masks:
        u |= mk
    return Image.fromarray(u.astype(np.uint8) * 255, "L")


@tool("segment", "sam2.1_large", ("sam2.1_large", "sam2.1_base_plus"),
      "masks from a point, a box, words or everything (SAM 2.1)")
def segment(rgb, alpha, params):
    which = pick("segment", params)
    w, h = rgb.size
    points, labels = _points(params, w, h)
    boxes = [_box(b, w, h) for b in (params.get("boxes") or ([params["box"]] if params.get("box") else []))[:16]]
    words = str(params.get("text") or "").strip()
    found = []
    if words and not boxes:
        detector = str(params.get("detector") or "grounding_dino_base")
        if detector not in TOOLS["detect"]["models"]:
            raise BadInput("the detector is grounding_dino_base or grounding_dino_tiny")
        m.use(detector, which)       # both on the card for the whole call: the words find the boxes, SAM traces them
        found = find(rgb, words, detector, float(params.get("threshold") or 0.4), alongside=(which,))[:16]
        if not found:
            return {"text": f"I couldn't find {words} in the picture.", "outputs": [], "data": {"masks": []}}
        boxes = [f["box"] for f in found]
    if not points and not boxes:
        masks, scores = sam_everything(rgb, which, _int(params.get("grid"), 24, 8, 48))
        data = [{"box": ik.mask_bbox(mk), "area": int(mk.sum()), "score": round(s, 3)} for mk, s in zip(masks, scores)]
        return {"text": f"Found {len(masks)} regions.", "outputs": [ik.encode(ik.overlay_masks(rgb, masks), "regions")],
                "data": {"masks": data}}
    masks, scores = [], []
    if boxes:
        masks, scores = sam_masks(rgb, which, boxes=boxes, alongside=(detector,) if found else ())
    else:
        masks, scores = sam_masks(rgb, which, points=points, labels=labels)
    union = _union(masks, rgb.size)
    outputs = [ik.encode(ik.cutout(rgb, union), "cutout"), ik.encode(ik.on_black(rgb, union, params.get("lift") or 0), "on_black"),
               ik.encode(union, "mask")]
    if len(masks) > 1:
        outputs.append(ik.encode(ik.overlay_masks(rgb, masks), "regions"))
    data = [{"box": ik.mask_bbox(mk), "area": int(mk.sum()), "score": round(s, 3), **({"label": found[i]["label"]} if found else {})}
            for i, (mk, s) in enumerate(zip(masks, scores))]
    return {"text": f"Cut out {len(masks)} {'region' if len(masks) == 1 else 'regions'}.", "outputs": outputs, "data": {"masks": data}}


# ── ocr ───────────────────────────────────────────────────────────────────────

OCR_TASKS = {"text": "OCR:", "table": "Table Recognition:", "formula": "Formula Recognition:", "chart": "Chart Recognition:"}
OCR_MAX_SIDE = 2048
OCR_MAX_TOKENS = 4096


def read_text(rgb: Image.Image, which: str, task: str) -> str:
    import torch
    (model, proc), = m.use(which)
    img = ik.fit_side(rgb, OCR_MAX_SIDE)
    prompt = OCR_TASKS[task] if which == "paddleocr_vl" else m.MODELS[which].extra.get("prompt", "OCR this image.")
    msgs = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": prompt}]}]
    inputs = proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=OCR_MAX_TOKENS, do_sample=False)
    text = proc.decode(out[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
    if which == "surya_ocr_2" and task == "text":   # Surya answers in HTML blocks: keep the words, a line per block
        text = re.sub(r"</(div|p|tr|li|h\d)>", "\n", text)
        text = re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()
    return text


def otsl_to_html(text: str) -> str:
    """PaddleOCR-VL writes tables in OTSL (<fcel> a cell, <ecel> an empty one, <lcel>/<ucel>/<xcel> merged into the cell left of /
    above it, <nl> a row's end); people want HTML. Merged cells become colspan/rowspan."""
    if "<fcel>" not in text and "<ecel>" not in text:
        return text
    rows = [re.findall(r"<(fcel|ecel|lcel|ucel|xcel)>([^<]*)", r) for r in text.split("<nl>") if r.strip()]
    grid = [[{"tag": t, "text": html.escape(v.strip()), "col": 1, "row": 1} for t, v in r] for r in rows]
    for y, r in enumerate(grid):
        for x, c in enumerate(r):
            if c["tag"] == "lcel":
                k = x - 1
                while k > 0 and r[k]["tag"] in ("lcel", "xcel"):
                    k -= 1
                if k >= 0:
                    r[k]["col"] += 1
            elif c["tag"] == "ucel":
                k = y - 1
                while k > 0 and x < len(grid[k]) and grid[k][x]["tag"] in ("ucel", "xcel"):
                    k -= 1
                if k >= 0 and x < len(grid[k]):
                    grid[k][x]["row"] += 1
    out = ["<table>"]
    for r in grid:
        cells = []
        for c in r:
            if c["tag"] in ("lcel", "ucel", "xcel"):
                continue
            span = (f' colspan="{c["col"]}"' if c["col"] > 1 else "") + (f' rowspan="{c["row"]}"' if c["row"] > 1 else "")
            cells.append(f"<td{span}>{c['text']}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "\n".join(out + ["</table>"])


@tool("ocr", "paddleocr_vl", ("paddleocr_vl", "surya_ocr_2"),
      "the words of a document, a table (as HTML), a formula (LaTeX) or a chart; 100+ languages")
def ocr(rgb, alpha, params):
    which = pick("ocr", params)
    task = str(params.get("task") or "text")
    if task not in OCR_TASKS:
        raise BadInput(f"ocr's task is {', '.join(OCR_TASKS)}")
    text = read_text(rgb, which, task)
    if task == "table":
        text = otsl_to_html(text)
    return {"text": text or "I couldn't read any text in it.", "outputs": [], "data": {"task": task, "chars": len(text)}}


# ── pose ──────────────────────────────────────────────────────────────────────

COCO_EDGES = [(15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12), (5, 6), (5, 7), (6, 8), (7, 9), (8, 10),
              (1, 2), (0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6)]
COCO_NAMES = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
              "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"]


@tool("pose", "vitpose_plus_base", ("vitpose_plus_base",), "body keypoints for each person (COCO-17), with a skeleton picture")
def pose(rgb, alpha, params):
    import torch
    from PIL import ImageDraw
    det, det_proc, vit, vit_proc = (x for pair in m.use("rtdetr_person", "vitpose_plus_base") for x in pair)
    inputs = det_proc(images=rgb, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        out = det(**inputs)
    res = det_proc.post_process_object_detection(out, target_sizes=[(rgb.height, rgb.width)], threshold=0.4)[0]
    people = [b.tolist() for b, lab in zip(res["boxes"], res["labels"]) if int(lab) == 0][:12]
    if not people:
        return {"text": "I couldn't see anyone in it.", "outputs": [], "data": {"people": []}}
    coco = [[x0, y0, x1 - x0, y1 - y0] for x0, y0, x1, y1 in people]
    p = vit_proc(rgb, boxes=[coco], return_tensors="pt").to("cuda")
    with torch.inference_mode():
        o = vit(**p, dataset_index=torch.zeros(len(coco), dtype=torch.long, device="cuda"))
    poses = vit_proc.post_process_pose_estimation(o, boxes=[coco])[0]
    shown = rgb.copy()
    draw = ImageDraw.Draw(shown)
    r = max(2, round(max(rgb.size) / 300))
    found = []
    for k, person in enumerate(poses):
        kp, sc = person["keypoints"].tolist(), person["scores"].tolist()
        colour = ik.PALETTE[k % len(ik.PALETTE)]
        for a, b in COCO_EDGES:
            if sc[a] > 0.3 and sc[b] > 0.3:
                draw.line([tuple(kp[a]), tuple(kp[b])], fill=colour, width=r)
        for (x, y), s in zip(kp, sc):
            if s > 0.3:
                draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)
        found.append({"box": [round(v, 1) for v in people[k]],
                      "keypoints": {n: [round(x, 1), round(y, 1), round(s, 3)] for n, (x, y), s in zip(COCO_NAMES, kp, sc)}})
    return {"text": f"Found {len(found)} {'person' if len(found) == 1 else 'people'}.",
            "outputs": [ik.encode(shown, "skeleton")], "data": {"people": found}}
