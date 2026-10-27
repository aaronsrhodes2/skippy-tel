# What purple Skippy can reach: the model and pop-up inventory (2026-10-27)

**The target (the Captain, 2026-10-27): as close to Claude Haiku as we can get, including its tools.** This is the list of everything purple has
to call on, what is running right now, what is built but not merged, and, tool by tool, where purple already matches Claude Code with Haiku and
where it does not. The pop-up table below is generated from the registries (`python tools/popup_inventory.py`, checked by a test), so it cannot drift.

Purple Skippy is Claude Code on skippy-pc thinking with Skippy Core's coder. It calls a pop-up by writing a `SKIPPY_POPUP:` line (or the portal chat
calls it as a tool); the portal holds the job in the patient queue until the machine is quiet, then skippy-pc's reporter runs it. A pop-up loads its
model when asked and gives the memory back, so on the Status page it is **blue (on call)** until it has answered a question.

## 1. Always warm on skippy-core (llama-swap, `core/models.json`)

| Slot | What it is | Used for |
|---|---|---|
| `coder` | the coding model, resident | purple's thinking (the Workshop's Core brain) |
| `chat` | Qwen3.6-35B-A3B, resident | Public chat, summaries, Mentat's phrasings, Barney Style, chat names |
| `router` | Mentat's trained router (Qwen3-4B + LoRA, v3.1), resident | turns a physics or maths question into a Mentat tool plan |
| `vision` | Qwen3-VL, resident | describes pictures for the text-only coder (`eyes.py`), every word copied out |
| `general` | the big general tier, on demand; swaps with the coder | long answers (not used by purple by default) |

## 2. Registered pop-up tools (generated)

<!-- popups:begin (tools/popup_inventory.py) -->
| Tool | What it does | Runs on | Takes | Gives | Typical run | Memory | Who |
|---|---|---|---|---|---|---|---|
| `echo` | The pop-up plumbing's test service: returns its arguments (and the size of any attached file) as JSON, or the attached file itself with mirror. | pc (quick) | audio, image | *, json | 1 s |  | admin |
| `image_to_3d` | Turns ONE attached picture of a single object into a textured 3D model (.glb) for AR, Garry's Mod props or tabletop miniatures. | core (quality) | image | gltf-binary | 3 min | 18 GB | admin |
| `text_to_3d` | Makes a textured 3D model (.glb) of one object from a description, for AR, Garry's Mod props or tabletop miniatures: it draws a picture of the object… | core (quality) | words | gltf-binary | 15 min | 30 GB | admin |
| `make_video` | Makes a short video clip (1 to 4 seconds, mp4, no sound) from a description; an attached picture becomes its first frame and gets animated. | core (quality) | image | mp4 | 32 min | 22 GB | admin |
| `sfx_quick` | Makes one short sound effect or drum hit for the Captain's electronic music in seconds (Stable Audio Open Small): kicks, snares, hats, risers, impact… | pc (quick) | words | wav | 30 s | 3 GB | admin |
| `sfx_loop` | Makes a tempo- and key-aware loop or longer one-shot for the Captain's tracks (Stable Audio Open 1.0): drum loops, hat loops, basslines, chord stabs,… | pc (quality) | words | wav | 100 s | 5 GB | admin |
| `melody_variations` | Takes a clip the Captain attached (a hummed or played melody, a riff, a loop) and re-imagines it in the style he describes, keeping its melody and ha… | pc (quality) | audio | wav | 75 s | 6 GB | admin |
| `stems` | Splits an attached track into drums, bass, vocals and other (Demucs v4) for remixing, sampling or study: a zip of four 320 kbps mp3s, or one stem (or… | pc (quality) | audio | mpeg, zip | 60 s | 3 GB | admin |
| `audio_to_midi` | Turns an attached clip (a melody, bassline, chords, a sung or played idea, or a stem from the stems tool) into a MIDI file the Captain drags into his… | pc (quick) | audio | midi | 25 s |  | admin |
| `search_memory` | Searches the Captain's own past by meaning, not keywords: his conversations with the private Skippy, the Skippy Memory archive of his chats with ever… | pc (quick) | words | json, plain | 20 s |  | admin |
| `diarize` | Turns an attached recording with several people talking (a meeting, a game session, a call) into a transcript labelled by speaker: who spoke when, th… | pc (quick) | audio | json, plain | 60 s |  | admin |
| `remove_background` | Cut the subject out of an attached picture (BiRefNet): a transparent PNG, or the subject on pure black, which the Captain's Viture XR glasses show as… | pc (quick) | image | json, png, webp | 30 s | 3.4 GB | everyone |
| `upscale_image` | Make an attached picture four times bigger and sharper (Real-ESRGAN x4), up to 4096 pixels on the long side; a transparent picture keeps its transpar… | pc (quick) | image | jpeg, json, png, webp | 30 s | 3.5 GB | everyone |
| `depth_map` | A depth map of an attached picture (Depth Anything V2: relative, near is bright) or a normal map made from it, for AR occlusion, parallax and relight… | pc (quick) | image | json, png | 20 s | 1.3 GB | admin |
| `segment_image` | Segment an attached picture with SAM 2.1: cut out the thing named in words, at a point or in a box, or (with nothing given) outline every region. | pc (quick) | image | json, png | 30 s | 3.6 GB | admin |
| `detect_objects` | Find whatever the words name in an attached picture and box it (Grounding DINO, open vocabulary): "find the mugs". | pc (quick) | image | json, png | 20 s | 2.7 GB | admin |
| `read_document` | Read a document, receipt, screenshot, table, formula or chart in an attached picture (PaddleOCR-VL, 100+ languages, mixed scripts): exact text, table… | pc (quick) | image | json, plain | 60 s | 2.4 GB | everyone |
| `body_pose` | Body pose of each person in an attached picture (RT-DETR finds people, ViTPose++ places 17 keypoints): a skeleton picture or the keypoints as JSON, f… | pc (quick) | image | json, png | 20 s | 1 GB | admin |
<!-- popups:end -->

All thirteen answered `ready` when this was written (2026-10-27, 20:50): the vision and 3D/video ones are **on call** (not loaded), search memory is
resident. They are listed in the portal's registry only since the portal image carries every family's registry file again (ff14d77: the build had
silently kept only the `echo` test; `/version` now says how many it knows and the deploy's smoke test compares it with the registry).

## 3. Built-in local capabilities (not in the registry)

| Capability | What | Where | Notes |
|---|---|---|---|
| `make_image` | quick pictures | skippy-pc (image-studio, FLUX.2 klein 4B) | about 3 s warm; ~7.5 GB of the card while loaded |
| `make_image_hq` | quality pictures | skippy-core (Qwen-Image-2512 through stable-diffusion.cpp, Vulkan) | on call; in main |
| `make_music` | songs | skippy-pc (ACE-Step 1.5) | one GPU job at a time, patient queue |
| voices | Skippy's own voice (F5-TTS clone) and a stock one (Kokoro) | skippy-pc `tts-studio`, through `voice-gate` | ElevenLabs is the fallback only |
| speech to text | Whisper | skippy-pc | through `voice-gate` |
| house | lights and switches (HomeKit, Matter, Leviton cloud) | the `skippy-house` MCP server | every run gets it |
| physics | Mentat: about 150 sigma-ground tools and the trained router | skippy-core `mentat` | the one place a physical number may come from (PLATINUM section 21) |

## 4. Built, not merged (branches)

| Work | Branch | What it adds |
|---|---|---|
| audio pop-ups | `claude/audio-popups-786718` | sound effects, loops, stems, audio-to-MIDI (3 commits ahead of main) |
| voice bake-off | `claude/hopeful-elgamal-0df0b1` | the clone and settings sweeps behind the live voice |

## 5. Purple against Claude Code with Haiku, tool by tool

Purple runs the same Claude Code program, so its built-in tools are the same ones; the gaps are the tools Claude Code gets from Anthropic's servers
and the senses the coder model does not have. `services/claude-bridge/sdk_engine.py` `CODING_TOOLS`: Bash, Read, Edit, Write, Grep, Glob, TodoWrite,
Task, WebFetch.

| Claude Code with Haiku | Purple today | Gap and plan |
|---|---|---|
| Bash, Read, Write, Edit, Glob, Grep, TodoWrite | **same program, same tools** | none (the lane guard limits where it may write) |
| Task (sub-agents) | same, but every sub-agent shares the one coder slot | works; one at a time (single FIFO queue, by design) |
| Skills, plugins, MCP servers | the Captain's coding skills and plugins, the house MCP | add Mentat and the pop-up tools as MCP tools so they are real tools, not a text line (design below) |
| Memory | one memory for the Captain and every brain | none |
| Seeing a picture (Read on an image) | `eyes.py` has the vision slot describe it, every word copied out | partial: a description, not pixels. The pop-ups (OCR, detection, depth, segmentation) go beyond Haiku for perception |
| Reading a PDF | no native PDF | **gap:** `pdftotext` first, then `read_document` (OCR) on pages with no text layer; wrap as one tool |
| **WebSearch** | **not available:** it is a server-side Anthropic tool | **gap, the biggest:** a local search (a self-hosted metasearch engine on skippy-core) behind a tool of the same name |
| WebFetch | in `CODING_TOOLS`; fetches and summarises | verify it reaches the local model (it expects a small summariser model name): add an alias in llama-swap if not |
| Browser and computer use | none for purple | **gap:** a headless browser (Playwright) as an MCP server on skippy-core; no real desktop |
| NotebookEdit | not enabled | low value; enable when a notebook is ever needed |
| Long context | the coder's window is 128K, about 30K of it Claude Code's own prompt | compressed headers (dev and STM), pivots with a knowledge dump (docs/pivot.md) |
| Plain reliability and judgement | a smaller model | referee loops and the orange review (docs/goal-mode.md) |
| Making pictures, 3D models, video, music, voice | **better than Haiku**: it has all of them, free | none |

## 6. Order of work to close the gaps

1. **WebSearch, local.** Nothing else in the list blocks research and docs lookups as hard.
2. **Pop-ups and Mentat as real MCP tools** (so purple sees names, arguments and results, not a convention in text).
3. **PDF reading** as one tool.
4. **Verify WebFetch** against the local model.
5. **A headless browser.**
6. Merge the audio and quick-image branches when their sessions finish.

Each pop-up and tool joins by one registry entry plus its container (docs/popups.md); the readiness contract (PLATINUM section 24) says whether it can
take work, and the Status page shows it green, blue (on call), yellow, red or grey.
