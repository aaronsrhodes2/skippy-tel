# SkippyTel — Claude Code Context

**First time here?** Read [PLATINUM_RULES.md](../PLATINUM_RULES.md) — this project inherits all universal rules there.

PC-side AI inference server for the Skippy AR ecosystem.
Flask app in Docker, port 3003 externally.

## After any code change

See [PLATINUM_RULES §2 (Docker)](../PLATINUM_RULES.md#2--docker-universal-rule) and [§7 (Code vs Config)](../PLATINUM_RULES.md#7--code-vs-config).

**Code changes (rebuild):**
```
docker compose stop flask-api && docker compose rm -f flask-api && docker compose up --build -d flask-api
curl -s http://localhost:3003/health
```

**Config changes (restart only):**
```
docker compose up -d flask-api
```

Full rebuild cycle: ~15 seconds. No manual restart needed.

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

Secrets come straight from the vault: compose's `env_file` is
`G:/My Drive/vault/.env`.
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

## Home → portal bridge (home reporter)

The cloud portal can't reach skippy-pc, so skippy-pc reports out:
`services/home-reporter/reporter.py` (in `docker-compose.override.yml`, which
Compose merges automatically: a plain `docker compose up -d --build`) POSTs every
~15 s to `https://…run.app/home/report` with `Authorization: Bearer
$SKIPPY_HOME_TOKEN`. It sends flask-api's `/status.json` (the old `/status`
page as data), `/services` availability, `/music/session`, and Mac Bilby's
`/bilby/status`, setlist and next suggestion (+ album art when the track
changes). The reply carries queued controls (`music` skip/pause/resume/stop)
and jobs; while someone chats, the reporter also polls `GET /home/jobs` every
1.5 s and answers with `POST /home/jobs/<id>`.

- Physics in Skippy's chat = `ask_mentat` → a job → `mentat-home` (same
  override file: mentat/Dockerfile with `MENTAT_USE_LLM=1`, Qwen via
  `ollama:11434`). No Qwen → Mentat answers 503 and Skippy says it's offline;
  there is deliberately NO fallback to the cloud copy (keywords only), which
  serves the public Experiments playground.

- Token: dashboard → "Pair home" shows it once; the portal keeps only its
  sha256 in VM metadata `portal-home`. Put the line in the vault .env and
  SkippyTel's .env.
- Portal side: `portal/home.py` (in-memory; the portal runs max 1 instance),
  `/bilby` sub-portal (grant "bilby"; controls admin-only), Home widget on `/me`.

## EverQuest (EQEmu) on the games VM

Not a profile in games/docker-compose.yml: `infra/gcp-games/everquest.sh`
(metadata `everquest-script`) runs AkkStack (pinned commit) under
`data/everquest/akk-stack` on the world disk. First start: AkkStack's own
installer (~15-20 min). Ports: world 9000, login 5998-5999, zones 7000-7030/udp,
chat 7779 (moved off 7778 for Dragonwilds). Only game ports are published.

The Captain's home world (AkkStack in WSL on skippy-pc, own login server,
account `gonif`, Golden Era rules in the DB) is carried over once:
skippy-pc exports `SkippyTel/state/imports/everquest.sql.gz` → the home
reporter uploads it in chunks → portal (`portal/imports.py`) holds it and puts
a one-time token in VM metadata (`eq-import-token`/`eq-import-url`) → the
next EverQuest start loads it, runs `world database:updates`, and reports
done. State: metadata `portal-imports`.

## Branches and deploys

`main` is the truth: every deploy and ops run comes from it, and Google's
Workload Identity only trusts GitHub tokens whose ref is `refs/heads/main`
(infra/bootstrap/main.tf). Work happens on a branch, goes through a pull
request (test.yml runs the tests), and deploys when it merges. skippy-pc and
Cloud Shell track `main`.
