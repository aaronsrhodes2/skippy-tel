# SkippyTel

PC-side AI inference for the Skippy AR system. The Android phone
([SkippyDroid](../Skippy)) calls these endpoints over Tailscale for
inference that can't run on-device: LLM chat, Whisper transcription,
face recognition, precise celestial ephemeris.

> **Quarantine:** SkippyTel does not import from SkippyDroid / SkippyAR /
> SkippyGlassesMac / SkippyStarMap. The single shared artifact is
> [`misc/SKIPPYTEL_BRIEF.md`](misc/SKIPPYTEL_BRIEF.md). Both sides
> implement from the brief; no code crosses the boundary.

## Quick start

```bash
cp .env.example .env       # add ANTHROPIC_API_KEY
make run                   # docker compose up -d --build
make pull-model            # one-time: pull qwen2.5:7b into ollama
make health                # GET /health
make intent                # POST /intent/unmatched (single-shot)
make intent-stream         # POST /intent/unmatched (SSE)
```

## Endpoints (v1)

| Endpoint | Status | Notes |
|---|---|---|
| `GET /health` | live | Sub-100ms. Phone polls every 10s. |
| `POST /intent/unmatched` | live | Ollama default → Anthropic on "put on your thinking cap" / `model_hint=claude` / Ollama unreachable. SSE if `Accept: text/event-stream`. |
| `GET /battery` | live | PC's own AC/battery state (psutil). |
| `GET /starmap?lat=&lng=&t_iso=` | **501 stub** | Wired when `services/physics-mcp/` exists. |
| `POST /translate/audio` | not yet | Whisper. Phase 2. |
| `POST /heybuddy/identify` | live | InsightFace buffalo_l + SQLite. Brief had `/yeybuddy/`; renamed per Captain. |
| `POST /tts/speak` | not yet | Optional. Phase 2. |

## Transport contract

- Bind to **Tailscale + loopback only.** No public internet exposure.
- **Tailscale IS the auth** in Phase 1 — no JWT, no mTLS, no shared secret.
- External port `3003`. Phone hits `http://skippy-pc:3003`.
- Every JSON response carries `spec_version: "1"`.

Doctrine (palette names not hex, no ellipsis, no scrollbars, version
field) lives in [`misc/SKIPPYTEL_BRIEF.md`](misc/SKIPPYTEL_BRIEF.md).

## Layout

```
services/
  flask-api/          ← Flask router. Blueprints: health, intent, battery, starmap.
  physics-mcp/        ← placeholder; future home of celestial / general physics service.
  chat/  image/  stt/  tts/   ← legacy from earlier scaffold; not wired in compose.
                                Available for porting when /translate/audio etc. land.
docker-compose.yml    ← flask-api + ollama (GPU)
misc/
  SKIPPYTEL_BRIEF.md  ← the contract. Authored in SkippyDroid, copied here.
Makefile              ← run / stop / logs / health / intent
```

## Phone-side coordination

The phone has `pcUrl = "http://skippy-pc:3003"` hardcoded in
`SkippyDroid/.../MainActivity.kt`. **Any port or hostname change here
must be applied there in the same commit.** See
[`misc/SKIPPYDROID_PORT_CHANGE_NOTE.md`](misc/SKIPPYDROID_PORT_CHANGE_NOTE.md).
