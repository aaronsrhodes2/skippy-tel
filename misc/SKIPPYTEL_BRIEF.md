# SkippyTel Brief — what the phone (SkippyDroid) expects from this server

> Source: Skippy AR System architecture plan, Session 8 — April 22 2026.
> Authored in the Skippy/SkippyDroid project; copied here as SkippyTel's
> starting context per the cross-project quarantine doctrine. SkippyTel
> implements from this document. No code crosses the boundary.

---

## What SkippyTel IS (and is NOT)

**IS:** An upstream AI inference service. Stateless HTTP(S) over Tailscale.
Phone calls PC, PC answers, phone renders. Services wrapped in Docker
Compose on the RTX 4070 Ti Super. Already partially real (Flask API on
`:5001`, Ollama on `:11434`, Physics MCP scaffolded in
`services/physics-mcp/`).

**IS NOT:**
- **Not a passthrough view.** Passthrough apps mount *into* the phone's
  1540×960 viewport via `PASSTHROUGH_PROTOCOL.md` (scene-tree + MJPEG
  on phone port `:47823`). SkippyTel is the other direction — phone →
  PC. Different concern, different port family, different role.
- **Not a push channel.** Phone hotspots drop. Tailscale mesh can
  partition. Everything is phone-initiated request/response (or SSE
  where the phone is the long-poller, not PC-initiated).
- **Not a frame transport.** Real-time AR frames are phone-local
  (loopback MJPEG between in-process producers and the passthrough host).
  SkippyTel never sees pose or frames.

## Transport contract

| Property | Value |
|---|---|
| Binding | Tailscale mesh + loopback. **No public internet exposure.** |
| Auth | **Tailscale IS the auth.** No JWT, no mTLS, no shared secret in Phase 1. |
| Cleartext | Allowed inside the mesh. Phase 2 revisits if the service leaves Tailscale. |
| Hostname the phone uses | `http://skippy-pc:3003` — literal string in `SkippyDroid/.../MainActivity.kt` (`pcUrl`). **Note:** brief was authored against `:5001`; both SkippyTel and SkippyDroid (Z-Phone) egress are now paired on `:3003` (different machines, never collide). SkippyDroid side must be updated in lockstep — see `misc/SKIPPYDROID_PORT_CHANGE_NOTE.md`. |
| Client | `TransportLayer.kt` — OkHttp, 3s connect / 5s read timeout, pings `/health` every 10s. |
| Degradation | `pcState = UNKNOWN / ONLINE / OFFLINE`. Modules with `requiresNetwork` skip render when OFFLINE. |
| Version field | Every response includes `spec_version: "1"`. Integer increments on breaking changes. Phone may refuse mismatches. |

## Endpoints the phone already expects (or plans to hit)

| Endpoint | Used by | Contract | Latency budget |
|---|---|---|---|
| `GET /health` | `TransportLayer.kt:91` every 10s | 200 OK on any body → ONLINE. Drives RightBar SignalStack (4/3/2/1/0 bars from `< 50ms / < 150ms / < 400ms / reachable / unreachable`). | **Sub-100ms warm** — critical for signal glyph to stay at 4 bars. |
| `POST /intent/unmatched` | `CommandDispatcher.onUnmatched` | `{ text, source, context }` → `{ reply?: string, speak?: string, actions?: [] }`. Phone escalates voice commands it can't classify locally. | **< 2s** for conversational feel. Streamed reply OK via SSE. |
| `POST /translate/audio` | Future Translation module | Multipart audio blob + optional hint language → `{ detected_lang, english_text, confidence }`. Uses `faster-whisper` medium. | **< 3s** end-to-end. |
| `GET /starmap?lat&lng&t_iso` | Future Star Map Phase 2 | List of `{ name, type, az_deg, alt_deg, mag }` for stars/planets above horizon. Wraps `services/physics-mcp/`. | **Cacheable** — phone may cache 60s. |
| `POST /heybuddy/identify` | Future Friend Recognizer | Face crop JPEG → `{ match: {name, confidence} \| null }`. InsightFace buffalo_l + cosine match against local SQLite. **Renamed from `/yeybuddy/`** per the Captain — see `misc/SKIPPYDROID_PORT_CHANGE_NOTE.md`. | **< 1s** per face. |
| `GET /battery` | Future battery-panel aggregator | `{ level: 0..100, source: "ac"/"battery"/"ups", plugged: bool }`. PC's own state. | Sub-second, polled every 60s. |
| `POST /tts/speak` *(optional)* | Future — if phone delegates TTS | `{ text, voice?, priority? }` → audio stream or reference URL. Current plan: phone does on-device TTS; this is fallback for PC-grade voices. | < 1s to first audio byte. |

`/starmap` should be a thin adapter over the existing `/physics`
blueprint scaffold — no duplicated ephemeris math.

## Doctrine inherited from SkippyDroid

- **Palette discipline.** Colored status uses palette enum names
  (`"cyan"`, `"amber"`, `"red"`, `"green"`, `"violet"`) — never hex.
- **No ellipsis, no scrollbars.** Any text destined for a HUD slot
  must wrap cleanly; tight replies beat prose paragraphs.
- **Quarantine holds.** SkippyTel does not import from SkippyDroid,
  SkippyAR, SkippyGlassesMac, or SkippyStarMap. Protocol details
  copied as comments into SkippyTel's own code.
- **Version field.** Mirror `PASSTHROUGH_PROTOCOL.md §15`: responses
  include `spec_version: "1"`.

## Privacy posture

- **Face DB never leaves PC.** SQLite lives on the 4070 Ti box.
- **Voice audio never leaves the mesh.** faster-whisper runs locally.
- **Claude API routing** is the open question (see below). Currently
  `docker-compose.yml` passes `ANTHROPIC_API_KEY` into the Flask
  service, implying PC proxies Claude. PC must be online to Anthropic
  for that — design Ollama fallback for when it isn't.

## Existing infra to build on (not replace)

| What | Where | Role |
|---|---|---|
| Flask router | `services/flask-api/app/main.py` | Top-level dispatcher; add blueprints. |
| `/health` | `services/flask-api/app/routes/health.py` | Already exists. Phone depends on it. |
| `/physics` blueprint | `services/flask-api/app/routes/physics.py` | Bolt `/starmap` on top. |
| Physics MCP | `services/physics-mcp/physics_mcp/` | Celestial math. |
| Ollama | docker-compose `ollama` on `:11434` | Default local LLM. `OLLAMA_BASE_URL` already wired. |
| Docker Compose | `docker-compose.yml` at repo root | New services as compose services, not sibling stacks. |

## Open questions for SkippyTel to resolve

1. **MCP vs REST naming.** Captain calls it an "MCP server." If literal Model Context Protocol → phone needs MCP client, not OkHttp. **Phone currently assumes REST.** Decide and record.
2. **Streaming for `/intent/unmatched`.** SSE for partial-token render? Or wait for full?
3. **Claude vs Ollama routing.** Phone (`model_hint`)? Router (per-intent)? Always Ollama-first, Claude-fallback?
4. **Face DB enrollment UX.** Manual photo import. CLI on PC? Web UI from Flask? Not in SkippyDroid.
5. **Audio codec for `/translate/audio`.** S23 records WAV/AAC; Whisper-friendly. Who transcodes.
