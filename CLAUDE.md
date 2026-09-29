# SkippyTel — Claude Code Context

PC-side AI inference server for the Skippy AR ecosystem.
Flask app in Docker, port 3003 externally.

## After any code change

```
docker compose up --build -d flask-api
curl -s http://localhost:3003/health
```

That's the full deploy cycle. ~15 seconds. No manual restart needed.

## Project structure

```
services/flask-api/app/
├── main.py              # Blueprint registration — add new routes here
├── config.py            # All settings (loaded from .env)
├── version.py           # envelope() / error() response helpers
├── skippy_persona.md    # Skippy's character prompt (loaded into all LLM tiers)
├── routes/
│   ├── intent.py        # POST /intent/unmatched — main LLM routing
│   ├── bilby.py         # GET /bilby/status, POST /bilby/next
│   ├── vault.py         # GET /vault/bootstrap, GET /phone, GET /apk/<app>
│   ├── translate.py     # POST /translate/text, POST /translate/audio
│   ├── image.py         # POST /image — Stable Diffusion proxy
│   ├── heybuddy.py      # POST /heybuddy/identify — face recognition
│   ├── battery.py       # POST /battery — phone battery reporting
│   ├── starmap.py       # GET /starmap — star map data
│   └── health.py        # GET /health
└── clients/
    ├── ollama.py         # Default tier — qwen2.5:7b
    ├── anthropic_client.py  # "put on your thinking cap" tier
    ├── gemini.py         # "ask nagatha" tier
    ├── claude_bridge.py  # "trust the awesomeness" + "solid gold" tiers
    ├── bilby.py          # Mac Bilby daemon client
    └── whisper.py        # Audio transcription

## Tier trigger phrases (in intent.py)

- Default → Ollama (qwen2.5:7b)
- "put on your thinking cap" → Anthropic Claude
- "ask nagatha" / "ask gemini" → Gemini (Nagatha persona)
- "trust the awesomeness" → Claude Code, full permissions
- "solid gold" → Claude Code, read-only plan mode

## Adding a new route

1. Create `routes/my_route.py` with a Flask Blueprint `bp`
2. Add `from app.routes.my_route import bp as my_bp` in `main.py`
3. Add `app.register_blueprint(my_bp)` in `create_app()`
4. Rebuild: `docker compose up --build -d flask-api`

## Environment variables (.env)

Key vars:
- OLLAMA_MODEL — default qwen2.5:7b
- ANTHROPIC_API_KEY — for Claude tier
- GEMINI_API_KEY — for Nagatha tier
- SD_BASE_URL — Stable Diffusion (default: http://host.docker.internal:5000)
- BILBY_MAC_URL — Mac Bilby daemon (default: http://skippy-mac:7334)
- BILBY_DRIVE_FOLDER_ID — Drive folder for streaming (pending Drive setup)

.env changes: restart only (no rebuild): `docker compose up -d flask-api`
Code changes: rebuild: `docker compose up --build -d flask-api`
```

## Cloud: portal + games VM (GCP project skippy-tel)

The Captain doesn't run commands or fetch logs. Claude does, through GitHub
Actions (the sandbox can't reach GCP or run.app directly):

- **Deploy:** push to `claude/great-edison-aevrtu` (or `main`) → `deploy.yml`
  (tests → Terraform `infra/gcp-games` → portal to Cloud Run). Redeploy without
  a code change: `workflow_dispatch` on `deploy.yml` (e.g. after a secret changes).
- **Operate/diagnose the games VM:** `workflow_dispatch` on `ops.yml` with
  `command` = `status` | `logs` | `start` | `stop` | `restart` and `args`
  (a game id for logs; game ids for start). Read the job log. Secrets are
  masked. `status` shows state, games, invite codes, missing settings.
  Don't stop/restart while people may be playing unless asked.
  On the VM itself (ops agent, infra/gcp-games/ops_agent.py; VM must be on):
  `tail <game> [lines]`, `search <game> <regex>` (whole container log),
  `stats` (CPU/mem/disk/per-container), `restart-game <game>` (one container,
  no VM reboot). Requests go via metadata `ops-request`, replies on serial 3.
  The agent and the snapshot script (diag.sh) reload from metadata, so
  changing them needs only a deploy.
  `command` = `discord-setup` creates/repairs every game's Discord channels:
  it queues the job in VM metadata and the portal (which holds the bot token)
  runs it via POST /tasks/run (portal/tasks.py). New portal-side jobs go there.
- Portal: https://skippy-portal-1015050363686.us-west1.run.app — code in
  `portal/`, docs in `docs/portal/README.md` and `docs/cicd.md`.
- Secrets live in the Captain's Drive vault `.env`; he loads them into Secret
  Manager with `bash infra/bootstrap/secrets.sh --from-env ~/.env` in Cloud Shell.
