# SkippyTel — future plans

Items deliberately deferred. Not blocking anything; tracked here so they
don't get lost when current focus moves on.

---

## ~~Rename "Gemini" tier~~ — done 2026-04-24, persona is **Nagatha**

Tier id is `nagatha`; primary trigger `"ask nagatha"`; prefix
`"Here's Nagatha's response: "`. `"ask gemini"` retained as a
transition alias — remove from `TIERS[1].triggers` in
`services/flask-api/app/routes/intent.py` once you're confident no
muscle memory or doc still uses it. Underlying client is unchanged
(Google Gemini, `gemini-flash-latest`, key in `GEMINI_API_KEY`).

GCP project for the Nagatha key: `projects/344901679317`
(`skippytel-nagatha`). Stash for if we ever migrate to Vertex AI.

---

## Pre-warm models at container start

Cold start of `/intent/unmatched` (Ollama tier) is ~35s the first time
after `docker compose up`. `OLLAMA_KEEP_ALIVE=-1` keeps it warm
afterwards. To eliminate even the first cold-start: add a tiny init
script that fires a no-op inference at each model on container ready.
Same for Whisper (~10s).

---

## Migrate off Docker Desktop to WSL2 + systemd Docker

Docker Desktop is user-mode — needs a Windows login to start. A
"true headless" SkippyTel would survive a reboot without anyone
logging in. Path: WSL2 distro with systemd enabled, Docker installed
inside, Tailscale on the WSL distro side too. Bigger lift; defer
until reboots become a real operational pain.

---

## TLS termination for SkippyTel egress (transparent)

Currently cleartext over Tailscale (per brief — mesh is the boundary).
If we ever want to expose SkippyTel beyond the tailnet (or just want
HTTPS for principle), drop nginx/caddy in front with auto-cert. Has
to be transparent to the phone — no cert prompt, no UI. Caddy with
internal CA + Tailscale's cert provisioning would do it.

---

## Phone-side / SkippyDroid follow-ons (not SkippyTel work but tracked)

- Wire `CommandDispatcher.onUnmatched` → `POST /intent/unmatched`
  (currently logs-only per brief).
- Apply lockstep changes from `misc/SKIPPYDROID_PORT_CHANGE_NOTE.md`
  (port 3003, cleartext config).
- Battery panel aggregator polling each tailnet node's `/battery`.
- Native APIs not yet hooked: ContactsContract, CalendarContract,
  HealthConnect, MediaSession, BluetoothLE, NFC, Activity Recognition.
