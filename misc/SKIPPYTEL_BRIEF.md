# SkippyTel — Handoff Brief

**Status:** v2 · April 25 2026
**v1 authored:** April 22 2026
**Source:** Copied from `zPhone/misc/SKIPPYTEL_BRIEF.md` per quarantine
doctrine — SkippyTel implements from this document, never imports from
the phone repo. Keep in sync manually when the zPhone copy updates.

> For the full document with companion context see the zPhone repo copy.
> This file is SkippyTel's working reference.

---

## 1. What SkippyTel IS (and is NOT)

**IS.**
- An upstream AI inference service (LLM, Whisper, face recognition)
- A **service control plane** — the single source of truth for what
  capabilities exist in the Skippy ecosystem, who has them, and how
  phones should surface them
- REST over Tailscale. Stateless request/response. SSE where the phone
  long-polls. Phone initiates; PC answers.

**IS NOT.**
- **Not a passthrough view.** Passthrough apps mount into the phone's
  1540×960 viewport via `PASSTHROUGH_PROTOCOL.md`. SkippyTel is the
  other direction.
- **Not a push channel.** Tailscale can partition. Design for
  phone-initiated only.
- **Not a data plane for external services.** When a service registers
  a `base_url`, phone clients hit that service **directly** over
  Tailscale. SkippyTel holds the manifest entry only. See §4.

---

## 2. Transport contract

| Property | Value |
|---|---|
| Binding | Tailscale mesh + loopback. **No public internet exposure.** |
| Auth | **Tailscale IS the auth.** No JWT, no mTLS in Phase 1. Registration endpoint adds `X-Skippy-Token` (see §5). |
| Cleartext | Allowed inside the mesh. |
| Hostname (phone → PC) | `http://skippy-pc:3003` via Tailscale MagicDNS. |
| Hostname (emulator → PC) | `http://10.0.2.2:3003` via AVD host loopback. |
| Phone HTTP client | OkHttp, 3s connect / 5s read timeout. Pings `/health` every 10s. |
| Spec version | `spec_version: "1"` in every response body. Integer increments on breaking changes. |

---

## 3. Core endpoints

| Endpoint | Contract | Budget |
|---|---|---|
| `GET /health` | 200 OK → phone ONLINE. Drives RightBar signal glyph (4/3/2/1/0 bars at <50ms/<150ms/<400ms/reachable/unreachable). | Sub-100ms |
| `POST /intent/unmatched` | `{text,source?,context?}` → `{reply,speak,tier}`. LLM escalation. SSE via `Accept:text/event-stream`. | < 2s |
| `POST /translate/audio` | Multipart audio → `{detected_lang,english_text,confidence}`. Whisper + Gemini. | < 3s |
| `POST /translate/text` | `{text,target_lang?}` → translation. | < 1s |
| `GET /starmap?lat&lng&t_iso` | Celestial objects above horizon. Wraps `physics-mcp`. | Cacheable |
| `POST /heybuddy/identify` | Face crop JPEG → `{match:{name,confidence}\|null}`. Face DB never leaves PC. | < 1s |
| `GET /battery` | `{level,source,plugged,ups_state}`. Polled every 60s. | Sub-second |
| `POST /image` | **Stub — 503 not_deployed.** Reserved for first-party SD service. | — |
| `GET /services` | Live service manifest. See §4. | Sub-50ms probes |
| `GET /services/<id>/icon` | 128×128 PNG. Generated once, cached in `state/icons/`. | — |
| `POST /services/register` | Dynamic service registration. See §5. Pending implementation. | — |
| `POST /services/heartbeat/<id>` | Keep-alive for registered services. See §5. | — |
| `DELETE /services/<id>` | Clean deregister. See §5. | — |

---

## 4. Service manifest protocol — `GET /services`

Polled every **5 minutes** by SkippyChat and SkippyDroid. The single
discovery endpoint for the entire Skippy ecosystem.

### ServiceManifest shape
```json
{
  "id":           "bilby",
  "name":         "Bilby",
  "tagline":      "Automated DJ",
  "available":    true,
  "status_detail":"Mac Bilby reachable",
  "base_url":     "http://skippy-mac:7334",
  "display": {
    "mode":          "overlay",
    "hud_zone":      "topcenter",
    "companion_app": null
  },
  "voice_triggers": ["what's playing", "bilby next"],
  "endpoints": { "status": "/bilby/status", "next": "/bilby/next" }
}
```

### `base_url` routing rule
- **Null** → all traffic (voice trigger response, data calls) goes
  through SkippyTel.
- **Non-null** → SkippyTel is control plane only. Phone clients route
  data calls directly to `base_url` over Tailscale. Emulator without
  Tailscale falls through to SkippyTel proxy path.

### `display.mode` values
| mode | behaviour |
|---|---|
| `overlay` | Render in SkippyDroid HUD zone named by `hud_zone` |
| `companion` | Launch Android app named by `companion_app`; fall back to `base_url` in browser |
| `inline` | Render result inline in SkippyChat feed |
| `background` | No user-visible surface. Hidden from widget and services panel. |

### Phone-side consumers
- **SkippyChat** — `KeywordScanner.dynamicTriggers`: `phrase → service_id`
  map, sorted longest-first. New services are voice-activatable within
  5 min, no rebuild.
- **SkippyDroid** — `ServiceRegistry.services: StateFlow<List<ServiceManifest>>`
  drives `ServicesPanelModule`.
- **ServiceGridWidget** — home screen widget; shows available non-background
  services as tappable icons. Fetches icons from `/services/<id>/icon`.

---

## 5. Dynamic service registration *(pending implementation)*

Allows external services (Mac Bilby, etc.) to self-register at startup.
Eliminates hardcoded entries in `services.py` for external services.

### Endpoints
```
POST   /services/register          — register or re-register
POST   /services/heartbeat/<id>    — keep-alive (every 60s)
DELETE /services/<id>              — clean shutdown
```
All require `X-Skippy-Token: <SKIPPY_REGISTRATION_TOKEN>` header.

### Registration body
```json
{
  "id":            "bilby",
  "name":          "Bilby",
  "tagline":       "...",
  "base_url":      "http://skippy-mac:7334",
  "display":       { "mode": "overlay", "hud_zone": "topcenter" },
  "voice_triggers": ["what's playing", "bilby next"],
  "endpoints":     { "status": "/bilby/status" },
  "ttl_seconds":   90
}
```

### Semantics
- Miss two heartbeats → `available: false` auto-set.
- Persisted to `state/registered_services.json`.
- Dynamic registration **overrides** static entry on `id` collision.
- `GET /services` merges both sources transparently.

### Mac Bilby startup sequence
```python
POST /services/register   { ...manifest... }
# loop every 60s:
POST /services/heartbeat/bilby
# on SIGTERM:
DELETE /services/bilby
```

---

## 6. Current manifest (April 25 2026)

| id | mode | base_url | available logic |
|---|---|---|---|
| `intent` | companion → `local.skippy.chat` | null | always |
| `bilby` | overlay → topcenter | `http://skippy-mac:7334` | TCP probe :7334 |
| `dropship` | companion → `local.skippy.gallery` | null | Drive folder ID set |
| `image` | inline | null | **always false (stub)** |
| `translate` | inline | null | always |
| `ups` | overlay → topend | null | TCP probe :9338 |
| `heybuddy` | inline | null | always |
| `jellyfin` | companion → `org.jellyfin.mobile` | `http://skippy-pc:8096` | TCP probe :8096 |
| `starmap` | overlay → viewport | null | **always false (stub)** |
| `vault` | background | null | always |

---

## 7. Doctrine

- **Palette discipline.** Structured responses use palette names only:
  `"cyan"`, `"amber"`, `"red"`, `"green"`, `"violet"`. Never hex.
- **No ellipsis, no scrollbars.** HUD text must wrap cleanly.
- **Stateless by default.** Phone may retry after partition.
- **Version field.** `spec_version: "1"` in every response.
- **Quarantine holds.** SkippyTel does not import from SkippyDroid,
  SkippyAR, SkippyGlassesMac, or any DJ project. Protocol details copied
  as comments into SkippyTel code directly.

---

## 8. Privacy posture

- **Face DB never leaves PC.**
- **Voice audio never leaves the mesh.**
- **Image generation** (when deployed) runs locally. No pixel reaches
  third-party APIs.
- **Claude/Gemini** receive text intent queries only — never voice audio,
  face crops, GPS, or image data. Ollama is always the default tier.

---

*End of brief. Questions → the Captain.*
