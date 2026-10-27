# Pop-ups: on-demand models that join Skippy with one registry entry

**The contract (for the family sessions).** A pop-up is one entry in your family's `services/<family>-popups/popups.json`
(`{"popups": [ ... ]}`, same shape as `services/popups/registry.json`) plus your own container, defined in
`services/<family>-popups/compose.yml` as its own Compose project on the external network `skippytel_default` (copy
`services/popups/compose.yml`; give it a `name:` and a `container_name`). You never edit a shared file. Your service answers
`GET /health` with the readiness contract (PLATINUM section 24: a 200 is not "fine"): `ready`, `accepting`, `jobs` (running, queued, oldest_s), `stuck_s`,
`detail` in plain words when not ready (`core/ready.py` is the helper; keep an identical copy beside your service and add it to tools/tests/test_ready_copies.py);
add `loaded`, `gpu_free_pct`, `gpu_util_pct`, `gpu_free_gib` if you can, as the picture studio does, and `POST <endpoint>` with the JSON body `{"args": {...the tool's arguments...}, "input": {"type", "name", "b64"}}` (`input`
only when the person attached a file or the agent named one, at most ~1 MB of file), replying with the result's raw bytes under its
own `Content-Type` (one of your entry's `outputs`, at most 25 MB), or a non-2xx with `{"error": "plain words"}`. That is all: the home
reporter reads the registry from its mount (no rebuild, re-read when a file changes), advertises your pop-up while `/health` is ready,
runs each job (on skippy-pc, `vram_gb > 0` takes the GPU lock and unloads Ollama first) and hands the result to the portal; the portal
makes it a patient job on your `box` (5 quiet minutes, one job per machine, kept in the bucket), a chat tool named `name` for your
`audience` (its `description` and `inputs` are what the brains read; guests get 10 pop-up jobs a day), a card that shows your output
type (a picture, a player, a video, a 3D viewer for `model/gltf-binary`, a JSON/text block, a download for `audio/midi` and `application/zip`), and a line the private Skippy can write:
`SKIPPY_POPUP: <name> {json args}` (`"_file": "<path>"` hands it a picture or sound from its clone).

## An entry

```json
{
  "name": "remove_bg",            "family": "vision",           "box": "pc",          "tier": "quick",
  "url_env": "REMOVE_BG_URL",     "url": "http://skippy-popup-remove-bg:47420",     "endpoint": "/run",
  "inputs": {"type": "object", "properties": {"feather": {"type": "integer", "description": "Edge softness, 0-10."}},
             "additionalProperties": false},
  "media_in": ["image/*"],        "media_required": true,
  "outputs": ["image/png"],       "est_seconds": 20,            "vram_gb": 3,         "audience": "everyone",
  "description": "Cuts the subject out of a picture the user attached and returns it on a transparent background."
}
```

| field | meaning |
|---|---|
| `name` | the tool's name: `[a-z][a-z0-9_]{2,39}`, not one the portal already uses (popups.RESERVED) |
| `family` | your family (audio, vision, 3d-video, search-speech) |
| `box` | `pc` (skippy-pc, 16 GB card) or `core` (skippy-core, shared 128 GB) |
| `tier` | `quick` or `quality` |
| `url_env` / `url` | the reporter uses `$url_env` if it is set, else `url` (your container's name on `skippytel_default`; for `core`, `http://skippy-core:<port>`) |
| `endpoint` | the path the job is POSTed to |
| `inputs` | a JSON schema (type object) for the tool's arguments; don't name a property `attachment` |
| `media_in` | optional: types of attached file it takes (`image/*`, `audio/*`, or exact); the tool then gets an `attachment` number |
| `media_required` | optional: refuse without a file |
| `outputs` | what it may answer with: `image/png`/`jpeg`/`webp`, `audio/*` (wav, flac, mpeg, ogg), `model/gltf-binary`, `video/mp4`, `application/json`, `text/plain`, and as a download card `audio/midi`, `application/zip` |
| `est_seconds` | a typical run once started (the card says "about N min"; the reporter waits 4x, at most 2 h) |
| `vram_gb` / `mem_gb` | memory it needs; on `pc`, more than 0 means it takes the GPU lock |
| `audience` | `admin` (the Captain only) or `everyone` |
| `description` | what the brains read to decide when to call it (10-1000 characters) |
| `chat` | optional, default true: false keeps it out of the chat's tools (the agent can still use it) |

## Check before you hand over

```bash
python -m pytest portal/tests/test_popups.py -q          # every entry in the repo must pass popups.check
docker compose -f services/<family>-popups/compose.yml build && docker compose -f services/<family>-popups/compose.yml up -d
```

The test service `echo` (`services/popups/echo`) runs the whole path with no model: `portal/tests/test_popups.py` drives it from an agent
line through the real reporter code to the chat's card.

Code: `portal/popups.py` (registry), `portal/chat_tools.py` (tools), `portal/app.py` (`box_for`, `gpu_for`, `agent_popup`),
`services/home-reporter/reporter.py` (`popups`, `popup_job`), `services/claude-bridge/workshop_engine.py` (`split_popups`).
