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

.env changes: restart only (no rebuild): `docker compose up -d flask-api`
Code changes: rebuild: `docker compose up --build -d flask-api`
```
