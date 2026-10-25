# 3D and video pop-ups on skippy-core

Three quality-tier pop-ups ([docs/popups.md](popups.md)) in one service on skippy-core, `core/popups-3d` (port 7348), registered
in `services/3d-video-popups/popups.json`. They load only while they work: every job is its own process, so the box's resident
language models (chat, coder and vision, ~75 GB) keep their memory between jobs.

| Pop-up | What it makes | Model | Seconds (measured) | Peak GiB | Licence |
|---|---|---|---|---|---|
| `image_to_3d` | a picture of one object -> a textured `.glb` | Hunyuan3D-2: shape 2.0-turbo + FlashVDM, Paint 2.0-turbo | ~60-140 s | 16.1 | Tencent Hunyuan 3D 2.0 Community |
| `text_to_3d` | a description -> a textured `.glb` | a picture from the core image-studio (Qwen-Image, 20 steps), then `image_to_3d` | see below | ~30 (the picture; the two never overlap) | Apache 2.0 + the above |
| `make_video` | a description (+ a first frame) -> 1-4 s of `.mp4`, no sound | Wan 2.2 TI2V-5B, fp16, through stable-diffusion.cpp's Vulkan build | ~19 min for 2 s, ~31 min for 3 s, ~45 min for 4 s | 20 | Apache 2.0 |

All three are `admin` (the Captain), `box: core`, `tier: quality`, so the portal's patient queue starts one only after skippy-core has
been quiet for 5 minutes with 35 GB free and the GPU under 20% busy, and only one job per machine at a time.

## Verdicts

- **`image_to_3d`: ship it.** The penguin test model came out clean on every side, the sign's lettering intact, in about a minute
  (138 s through the home reporter's own code, with the coder model generating alongside). Good for AR props, Garry's Mod props and
  minis. Works best on one whole object on a plain background: the background remover kept a few clutter blocks that touched the
  object. 40,000 faces, ~3 MB.
- **`text_to_3d`: ship it as a chain**, not a model of its own: the quality picture studio draws the object on a plain white
  background in a product-shot style, then `image_to_3d` makes it. Costs nothing extra on disk.
- **`make_video`: ship it, slow.** The test clip (a fox trotting through snow) has real motion and a coherent scene. It runs about
  10 minutes per second of clip at 1280x704, so it is capped at 4 s. Wan's own Chinese negative prompt is used: in English a
  "subtitles" smudge appeared at the bottom of the frame.
- **Gaussian splats from photos: a recommendation, not built.** See the end.

## How it runs on the box (gfx1151, ROCm and Vulkan)

- **Hunyuan3D-2 runs on ROCm PyTorch**: AMD's TheRock wheels built for gfx1151 (`torch 2.10.0+rocm7.13.0a20260513` from
  `https://rocm.nightlies.amd.com/v2/gfx1151/`), ROCm entirely inside the wheels, nothing on the host but the in-kernel amdgpu
  driver. 33 TFLOPS bf16 on a plain matmul. Its CUDA rasterizer (`custom_rasterizer`) builds for gfx1151 unchanged: torch's hipify
  turns the CUDA source into HIP, using `rocm[devel]` (~15 GB, the Dockerfile's build stage only). The image is 11 GB.
- **Wan 2.2 runs on stable-diffusion.cpp's Vulkan build**, the same pinned release as the picture studio (`master-948-228c707`).
- Three switches made the difference between unusable and good:

| Switch | Before | After |
|---|---|---|
| `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` (flash attention on gfx1151) | shape diffusion 15 s a step | 0.4 s a step |
| Hunyuan's FlashVDM decoder (`enable_flashvdm`) | volume decode 2766 chunks, 19 min | 64 chunks, under 1 s |
| **MIOpen off** (`torch.backends.cudnn.enabled = False`: PyTorch's own GEMM convolutions) | Paint not done after 20 min, **and 8 amdgpu ring resets in llama-server** | Paint in 28 s, no resets |

- **The MIOpen finding matters for anything ROCm on this box.** With MIOpen's convolutions, single GPU kernels ran so long that the
  language models' Vulkan work on the same iGPU timed out: the kernel logged `ring comp_1.x timeout ... Process llama-server` and
  reset the queue, eight times in 18 minutes, which fails chat replies. With MIOpen off, a probe asking the chat slot for a few
  words every 20 s got every answer in ~0.2-0.9 s through image_to_3d and through Wan.
- **Wan's VAE decode stays on the GPU with sd.cpp's default 256 px tiles, one frame at a time.** 512 px tiles with two frames
  decoded at once (`--vae-tile-size 512x512 --temporal-tiling ... temporal_decode_frames=2`) ended in `ErrorDeviceLost` and a full
  GPU reset ("GPU reset(2) succeeded"): llama-server and the picture studio were restarted and one chat request failed. On the CPU
  (`--backend vae=cpu`, as the picture studio does) it is safe but took 1496 s for 25 frames, against ~10 s a frame on the GPU.
- `torch.cuda.mem_get_info()` says ~127 GB free on this APU whatever is loaded; the service reads GTT from the amdgpu sysfs files
  instead (`gpu_free_gib`, `gpu_free_pct`, `gpu_util_pct` in `/health`, like the picture studio).
- transformers 5.x renamed DINOv2's attention weights, and Hunyuan3D's image encoder no longer loads: pinned to 4.57.1.
- Paint's loader reads `unet/diffusion_pytorch_model.bin` only (7.3 GB), not the safetensors copy beside it.

## Measurements (skippy-core, 2026-10-24)

Peak GiB is GTT (the iGPU's share of the 128 GB) above where it was when the job started.

**image_to_3d** (Hunyuan3D-2, the demo penguin, 40,000 faces)

| Run | Background off | Shape | Paint | Total | Peak GiB |
|---|---|---|---|---|---|
| 2mini-turbo shape only, no FlashVDM, no AOTriton | | 1363 s (75 s diffusion, 19 min decode) | | | 18.8 |
| 2.0-turbo + FlashVDM + AOTriton, MIOpen ON | | | >20 min, 8 llama-server ring resets | | |
| 2.0-turbo + FlashVDM + AOTriton, MIOpen off (cut-out picture) | 0 s | 34 s | 28 s | ~65 s | 16.1 |
| the same through `reporter.popup_job`, cluttered background, coder busy | 9.6 s | 44 s | 50 s | 138 s | 16.1 |

**make_video** (Wan 2.2 TI2V-5B fp16 + umt5-xxl Q8_0, 1280x704, 24 fps, 20 steps, euler, flow shift 5, cfg 5)

| Clip | Sampling | VAE decode | Total | Peak GiB |
|---|---|---|---|---|
| 1 s (25 frames) | 317 s | 1496 s on the CPU | 1819 s | 15 |
| 2 s (49 frames) | ~680 s | 469 s on the GPU (256 px tiles) | 1154 s | 20 |
| 3 s (73 frames) | 1146 s | GPU reset with 512 px tiles + temporal tiling | | |

So 3 s is ~31 min and 4 s ~45 min with the GPU decode; 5 s would pass an hour.

**text_to_3d**: TEXT_TO_3D_RESULT

## Licences (personal use)

- **Hunyuan3D-2** (weights and code): Tencent Hunyuan 3D 2.0 Community Licence. Free to use, including commercially under 1M monthly
  users; **not licensed in the EU, the UK or South Korea**. Fine for the Captain's own props, AR and minis in the US.
- **Wan 2.2 TI2V-5B**, umt5-xxl, Qwen-Image: Apache 2.0.
- **The background remover** is rembg's default, BRIA RMBG (`bria-rmbg.onnx`): CC BY-NC 4.0, non-commercial. Fine for personal use;
  anything sold would need another remover.
- stable-diffusion.cpp: MIT. Brush (below): Apache 2.0. COLMAP: BSD.

## What was considered and not built

- **TRELLIS / TRELLIS.2 (Microsoft, MIT):** CUDA-only extensions throughout (spconv, kaolin, nvdiffrast, flash-attn; TRELLIS.2's
  o-voxel, cumesh and flex_gemm). No ROCm path today. Hunyuan3D-2 already gives clean textured models here.
- **Hunyuan3D-2.1** (PBR materials, 3.3B shape model): the natural upgrade. Not tried; its Paint 2.1 needs the same rasterizer plus
  more. Worth a try once 2.0 has been used for a while.
- **LTX-2.3** (22B, with audio; stable-diffusion.cpp runs it): a possible video upgrade. Not benchmarked: it is larger than Wan 5B
  and this box spends ~10 min per second of Wan already.
- **Gaussian splats or photogrammetry from several photos (AR scene capture): recommended, not built.** Brush (wgpu, so Vulkan,
  Apache 2.0) has a headless Linux build that trains splats from a COLMAP folder, and COLMAP 3.12 is in Ubuntu's archive (CPU,
  for the camera poses). Why not now: the result is a `.ply` splat, which the chat can neither keep nor show (the pop-up output
  types are pictures, audio, `.glb`, `.mp4`, JSON and text), and the input is many photos while a pop-up takes one attached file.
  It needs two plumbing changes first: several photos (or a zip, or a short video) in, and a splat viewer in the chat's card. Then
  it fits the same container: COLMAP for poses, Brush for ~5-15 minutes of training, a `.ply` (or `.spz`) out.

## Operating it

- **On the box:** `CORE_POPUPS3D=1` in `/etc/skippy-core/core.env` makes the deployer add `core/popups-3d/compose.yml` to the stack
  (the simulator and CI never build the 11 GB image). Weights in `/srv/models/huggingface/`: `tencent__Hunyuan3D-2` (paint turbo,
  delight, 2.0-turbo shape and VAE), `tencent__Hunyuan3D-2mini`, `Comfy-Org__Wan_2.2_ComfyUI_Repackaged` (TI2V-5B fp16, its VAE),
  `city96__umt5-xxl-encoder-gguf` (Q8_0), and `/srv/models/u2net/models/bria-rmbg.onnx`.
- **Health:** `curl -s http://skippy-core:7348/health` from skippy-pc: `ready`, `busy`, each pop-up's `ready` and `peak_gib`, the
  GPU figures. `ready` is image_to_3d and make_video having their models; text_to_3d without the picture studio answers in words.
- **Logs:** `docker logs skippycore-popups-3d-1` (one line per job: seconds per stage, faces, peak).
- **Firewall:** none needed, as for the picture studio: ufw keeps the LAN out, Tailscale lets the tailnet in, `ALLOW_FROM` answers
  only skippy-pc (100.122.71.14) and the box itself.
- **Tests:** `core/tests/test_popups_3d.py` (the HTTP contract with fakes), `core/tests/test_deployer.py` (the compose switch),
  `portal/tests/test_popups.py` (the registry entries).
