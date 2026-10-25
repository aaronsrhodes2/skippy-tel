# Vision and AR pop-ups (`services/vision-popups`)

The Captain's vision and AR-development tools, as **pop-ups** (docs/popups.md): models that load only when asked, run as patient jobs
on skippy-pc's 16 GB card, and hand their result back to the chat. One container, one registry file (`services/vision-popups/popups.json`),
one entry per tool. Like `services/image-studio`, a model loads on the first request and unloads after `IDLE_UNLOAD_S` (300 s) idle,
or as soon as another tool needs the card: only one tool's models are on the card at a time.

| Pop-up (chat tool) | Endpoint | Model (pinned) | Gives back | Who |
|---|---|---|---|---|
| `remove_background` | `/popup/remove_background` | BiRefNet (MIT) | the subject on transparency, or on pure `#000000` | everyone |
| `upscale_image` | `/popup/upscale` | Real-ESRGAN x4plus (BSD-3) | 4x the pixels, up to 4096 px a side, alpha kept | everyone |
| `depth_map` | `/popup/depth` | Depth Anything V2 Large (CC-BY-NC-4.0) | depth 8-bit / 16-bit, a normal map | admin |
| `segment_image` | `/popup/segment` | SAM 2.1 Hiera-L (Apache-2.0) + Grounding DINO for words | cut-out, mask, or every region | admin |
| `detect_objects` | `/popup/detect` | Grounding DINO base (Apache-2.0) | labelled boxes, or JSON | admin |
| `read_document` | `/popup/ocr` | PaddleOCR-VL 1.6 (Apache-2.0) | the text; tables as HTML, formulas as LaTeX | everyone |
| `body_pose` | `/popup/pose` | RT-DETR (people) + ViTPose++ base (Apache-2.0) | skeleton picture, or 17 keypoints a person | admin |

Every tool takes the picture the person attached (the chat shrinks it to about 1 MB; the service takes up to 16 MB and 40 Mpx) and an
`output` argument naming which of its results comes back (`json` gives the text and the data instead). `POST /run/<tool>` returns
all of a tool's results at once, as JSON with base64 pictures: that's for the agent and for `bench.py`.

## AR: black is see-through

On the Viture XR glasses pure black (`#000000`) emits no light, so it is transparent (the colour doctrine in
`D:\Aaron\development\CLAUDE.md`). `remove_background` and `segment_image` therefore offer `on_black`: the subject composited on
exact `#000000`, which floats in the room with no box around it. Two catches, both handled:

- The subject's own blacks vanish too. `lift` (0-64) raises its darkest pixels to at least that value, so a black jacket stays visible
  as a very dark grey. 16-24 is usually enough.
- Soft edges (hair) fade to black rather than to the room. On the glasses that reads as a thin dark fringe; `birefnet_hr_matting` gives
  the softest matte, plain `birefnet` the cleanest hard edge.

`depth_map` gives relative depth (near is bright) for occlusion and parallax, and a normal map computed from it (two gradients: nearly
free) for relighting a flat picture. It is not metric: see MoGe-2 below for metres and the camera's field of view.

## Benchmarks

Measured 2026-10-24/25 on skippy-pc (RTX 4070 Ti SUPER, 16 GB) with `bench.py`, one tool per container. **Warm** is a run with the
model already on the card (the median case for a burst of requests); **cold** includes loading the weights from the volume and
CUDA's start-up, on a quiet Docker VM (on a VM that is swapping, cold loads took 5-10 minutes: see "Operating it"). **Peak** is
PyTorch's reserved memory; add ~0.4 GB of CUDA context. **The card's budget:** with Ollama unloaded (the reporter does that before any
pop-up with `vram_gb > 0`), Windows, the resident voices (tts-studio, 3 GB) and Whisper leave about **6 GB**. Everything below fits.

| Tool | Model | Input | Cold s | Warm s | Peak GB | Fits | Notes |
|---|---|---|---|---|---|---|---|
| remove_background | **BiRefNet** | 640x425 | 42 (3 s once cached) | 0.3-0.5 | 2.8 | yes | 2048x1360 photo: 1.3 s warm |
| remove_background | BiRefNet-matting | 640x425 | 12 | 0.5 | 3.1 | yes | softer edges (hair) |
| remove_background | BiRefNet_HR / HR-matting (2048 px) | 640x425 | 30-176 | 0.7 | **9.7** | **no** | dropped |
| upscale | **Real-ESRGAN x4plus** | 320x212 -> 1280x848 | 14 | 0.4 | 0.8 | yes | |
| upscale | Real-ESRGAN x4plus | 1024x680 -> 4096x2720 | 7 | 5.4 | 3.2 | yes | 512 px tiles |
| depth | **Depth Anything V2 Large** | any (runs at 518 px) | 48 | 0.4 | 0.9 | yes | + normals, ~free |
| depth | Depth Anything V2 Small | any | 1.5 | 0.3 | 0.2 | yes | Apache-2.0, coarser |
| segment | **SAM 2.1 Hiera-L**, a point | 640x425 | ~20 | 0.8 | 1.6 | yes | |
| segment | SAM 2.1 Hiera-L, everything (24x24 grid) | 352x230 | ~20 | 1.9 | 1.9 | yes | 43 regions |
| segment | SAM 2.1 Hiera-B+, everything | 352x230 | 6 | 1.8 | 1.5 | yes | 42 regions: no faster here |
| segment | SAM 2.1 L + Grounding DINO, words | 640x480 | 27 | 0.8 | 3.1 | yes | both models held for the call |
| detect | **Grounding DINO base** | 640x480 | 25 | 0.2 | 2.4 | yes | 2 remotes, 2 cats at 0.4 |
| detect | Grounding DINO tiny | 640x480 | 14 | 0.2 | 2.1 | yes | |
| ocr | **PaddleOCR-VL 1.6** | mixed-language page, 1400x530 | 84 | 40 | 1.9 | yes | **CER 3.0 %** |
| ocr | PaddleOCR-VL 1.6 | the same page as a phone photo | 90 | 47 | 1.9 | yes | **CER 3.2 %** |
| ocr | PaddleOCR-VL 1.6 | table (English + Chinese) | 24 | 5.4 | 1.9 | yes | **CER 0 %**, HTML |
| ocr | Surya OCR 2 | mixed page / photo / table | 48-237 | 33-35 / 19 | 1.5 | yes | CER 4.5 % / 6.4 % / 8.8 % |
| pose | **RT-DETR + ViTPose++ base** | 640x428, 3 people | 45 | 0.3 | 0.8 | yes | |

**OCR verdict: PaddleOCR-VL.** The samples (`bench.py` inputs, made with Windows' own fonts) mix English, Spanish, German, French,
Russian, Greek, Chinese, Japanese and Korean in one page, the same page photographed (perspective, blur, noise, uneven light) and a
bilingual table. PaddleOCR-VL read every script; its only slip was one Greek line partly read as look-alike Cyrillic letters. It
returns tables in OTSL, which the service turns into an HTML table. Surya 2 made more mistakes and is slower here: its Qwen3.5-style
decoder falls back to slow reference kernels without `flash-linear-attention`, and its licence is the more restrictive. Both are
slow-ish on a full page (~40 s: token-by-token generation), which is fine for a patient job; quick reads stay with skippy-core's
Qwen3-VL. The Captain's own mixed-language documents weren't available to this session: drop a few into the bench folder with a
`.txt` of the right answer and re-run `bench.py /bench ocr` to check on them.

**Background removal verdict: BiRefNet (general).** Clean on the test skier, poles and skis included, and on a motorbike
show photo. The matting variant is offered for hair and fur.

## Licences (personal use)

Everything here is fine for the Captain's personal use. Two need care if anything is ever shared or sold:

- **Depth Anything V2 Large: CC-BY-NC-4.0** (non-commercial). The Small model is Apache-2.0 and is offered as `model:
  depth_anything_v2_small` if that ever matters.
- **Surya OCR 2: modified OpenRAIL-M** (free for personal use and small organisations; its code is GPL-3.0). It is in the container
  only as the OCR comparison; PaddleOCR-VL (Apache-2.0) is the default.
- **RMBG-2.0** (Bria, the brief's other background remover) is **not included**: it is gated on Hugging Face (accepting Bria's terms
  needs the Captain's own click while signed in) and is non-commercial (CC-BY-NC-4.0, a commercial licence from Bria). It is BiRefNet's
  architecture retrained on Bria's licensed data; BiRefNet (MIT) is what it would be compared against. To try it: accept at
  https://huggingface.co/briaai/RMBG-2.0, `python tools/models_archive.py hf briaai/RMBG-2.0` with the vault's `HF_TOKEN`, add a
  `register(Model("rmbg2", "briaai__RMBG-2.0", ...))` line with the BiRefNet loader.

All other models: MIT, BSD-3-Clause or Apache-2.0. Every weight folder carries its `SOURCE.json` (revision + licence) in the archive.

## What else helps AR (researched 2026-10-24, verdict per model)

Licences checked on each model card (Hugging Face API) on 2026-10-24. "Fits" means beside the resident voices and Whisper with
Ollama unloaded: about 6 GB of the card.

| Model | What it gives AR | Licence | Verdict |
|---|---|---|---|
| **ViTPose++ base + RT-DETR** | 17 body keypoints a person | Apache-2.0 | **Included** (`body_pose`): native in transformers, small, no new runtime. |
| **RTMW / DWPose via rtmlib** | 133 whole-body keypoints: body, **hands** (21 a hand), face | Apache-2.0 | **Next, if hands matter.** ONNX models of ~100-300 MB; needs onnxruntime-gpu in the container (a second runtime), so left out of this first cut. |
| **MediaPipe Hands / Pose** | hands and body, real time | Apache-2.0 | **Not a pop-up.** It runs on a phone's CPU at 30 fps; its place is in the browser on the live camera feed (MediaPipe Tasks for Web), not a patient job. |
| Sapiens pose | dense 308-point pose | CC-BY-NC-4.0 | No: heavy (0.3-2 B), non-commercial, nothing ViTPose/RTMW miss for this use. |
| **MoGe-2 (ViT-L, normal variant)** | from one photo: **metric** depth, a point map, normals and the camera's **field of view** | MIT | **Recommended next.** The one thing Depth Anything V2 can't give: scale in metres and the intrinsics, which is what placing a virtual object at the right size needs. 1.3 GB of weights; needs the `moge` package (pinned commit). |
| **Depth Anything 3 (Large 1.1)** | depth and **camera poses** from one or several photos | Apache-2.0 (Small/Base/Large-1.1; Giant is CC-BY-NC) | **Recommended for multi-photo capture**: a few photos of a room give consistent depth and where each was taken from. Supersedes VGGT in its own paper. Needs the `depth-anything-3` package. |
| VGGT-1B | multi-view poses, depth, points | CC-BY-NC-4.0 (commercial copy gated) | No: 10 GB of fp32 weights, superseded by DA3. |
| MapAnything | metric multi-view reconstruction | Apache-2.0 (`map-anything-apache`) | Later, on skippy-core: 4.9 GB of weights, too big beside the voices. |
| Gaussian-splat capture (gsplat / nerfstudio) | a full 3D scene from many photos | Apache-2.0 | Not a pop-up on this card: minutes of optimisation per scene. A skippy-core job if the Captain wants room captures. |
| Apple SHARP | a Gaussian splat from **one** photo in under a second | Apple ML Research licence (research only) | No: the weights' licence is research-only; revisit if Apple relicenses. |
| **Normals from depth** | a normal map for relighting | (ours) | **Included** in `depth_map` (two gradients, free). |
| YOSO-normal / StableNormal | sharp learned normals | Apache-2.0 (YOSO) | Optional later: 2.6 GB, diffusion-based; MoGe-2's normals cover most of it. |
| Marigold IID / normals | albedo, shading, roughness, metallic: relight a photo convincingly | OpenRAIL++ | Later, on skippy-core: Stable Diffusion 2 based (~5 GB peak), too big here. |
| **SAM 3** | segment by words ("all the mugs") in one model | Meta SAM licence, **gated** | Worth it once the Captain accepts the gate: would replace the Grounding DINO + SAM 2.1 pair in `segment_image`. |
| RMBG-2.0 | background removal | CC-BY-NC-4.0, **gated** | Not included (see Licences); BiRefNet (MIT) is the same architecture. |

## Operating it

- **Start / rebuild:** `docker compose -f services/vision-popups/compose.yml build && docker compose -f services/vision-popups/compose.yml up -d`
  (its own Compose project, `skippy-popups-vision`, on the home stack's `skippytel_default` network; the reporter reaches it as
  `http://skippy-popup-vision:8004`, overridable with `VISION_POPUPS_URL`).
- **Weights:** `python tools/models_archive.py hf <repo>` puts a pinned snapshot in `D:\models\huggingface`; then
  `bash services/vision-popups/seed_weights.sh` streams them into the `vision-popups-weights` volume on C: (Docker reads D: at ~8 MB/s
  and starves under load; the pipe doesn't). A folder never seeded is copied by the service itself on first use, slowly.
- **Benchmark:** `docker run --rm --gpus all -v vision-popups-weights:/weights -v <inputs>:/bench skippy-popups-vision-vision-popups python /popups/bench.py`
  (the inputs: `person.png`, `room.png`, ... see `CASES` in `bench.py`; `ocr_*.png` with a `.txt` truth are scored by CER).
- **Safety:** before loading, the service checks the card's free memory against the model's measured peak plus 600 MB and answers
  503 `busy` instead of overcommitting: on 2026-10-23 an overcommitted card spilled into the Docker VM's RAM and froze it. The
  container has a 3 GB RAM cap (weights are loaded straight to the card).
- **Tests:** `python -m pytest services/vision-popups/tests` (no GPU: the image plumbing, the HTTP contract, the registry).
