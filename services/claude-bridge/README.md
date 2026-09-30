# solid-gold bridge

Tiny HTTP service that runs **on the Windows host** (not in docker) and
shells out to Claude Code CLI. SkippyTel's `flask-api` container calls
it via `host.docker.internal:9337` so the SkippyTel "solid gold" and
"trust the awesomeness" tiers use Aaron's Claude Code subscription
(via `~/.claude/.credentials.json`) instead of the metered API key.

## Why not in docker

Claude Code is a Windows binary with OAuth credentials in `%USERPROFILE%\.claude\`.
A Linux container can't run it and shouldn't have those credentials.
Bridge stays on the host; container talks to it.

## Launch

Stdlib-only Python script, no pip install. Just:

```bash
make bridge          # from D:/Aaron/development/SkippyTel
```

Or directly:

```powershell
cd D:\Aaron\development\SkippyTel\services\claude-bridge
python bridge.py
```

Environment overrides (set in the same shell before launch):

| Var | Default | Purpose |
|---|---|---|
| `CLAUDE_BRIDGE_PORT` | `9337` | Port bound on 0.0.0.0 (covers loopback + host.docker.internal) |
| `CLAUDE_BRIDGE_CWD` | `D:\Aaron\development` | Working directory `claude -p` runs in |
| `CLAUDE_PATH` | `claude` | Full path to `claude.cmd`/`claude.exe` if not in PATH |
| `CLAUDE_AWESOMENESS_MODEL` | `claude-opus-4-7` | Model used by `trust the awesomeness` tier |

## Endpoints

### `GET /health`
```json
{"status": "ok", "service": "claude-bridge", "cwd": "...", "claude": "...", "awe_model": "..."}
```

### `POST /solid-gold`
```json
{
  "text":   "your prompt here",
  "mode":   "plan" | "bypass",     // plan = solid gold, bypass = trust the awesomeness
  "stream": false                  // true → NDJSON, false → single JSON {output, returncode}
}
```

Stream output is NDJSON, one line per chunk:
```
{"text": "first chunk of assistant prose"}
{"text": "second chunk"}
{"done": true, "returncode": 0}
```

### The private Skippy: `POST /workshop`, `GET /workshop/run/<task>`, `POST /workshop/cancel/<task>`

Claude Code with **full permissions** (`--dangerously-skip-permissions`) on the
SkippyTel repo, driven from the Captain's portal dashboard (`workshop_engine.py`).
These three need `Authorization: Bearer <SKIPPY_HOME_TOKEN>` (the token the home
reporter holds; read from the environment or the vault `G:/My Drive/vault/.env`).
With no token configured they answer 503 and stay shut. `/solid-gold` takes the
same token (see the note below).

- **Model:** `claude-opus-5-5`, a constant in `workshop_engine.py`. No environment
  variable and no request field changes it.
- **Where it works:** its own clone of the repo (`WORKSHOP_REPO`, default
  `D:\Aaron\development\SkippyTel-workshop`, cloned on first use), never the
  Captain's checkout. It deploys skippy-pc services from `WORKSHOP_PRIMARY`.
- **Instructions:** `workshop_prompt.md` (loaded from here, never from a request):
  the ask → change → test → push to main → deploy → verify loop, plus what it must ask first.
- **Sessions:** one Claude Code session per chat conversation, remembered in
  `.skippy-workshop-sessions.json`, so follow-ups have the context of the last.
- **Runs:** `POST /workshop {task, conv, message}` starts one (asking again with
  the same `task` re-attaches instead of re-running); `GET /workshop/run/<task>?after=N&wait=S`
  returns events from index N (`{t:"text"}` and `{t:"tool", name, detail}`, never
  tool output; commands are redacted for credentials). One task at a time (409 when busy).
- **Login:** if `claude` on this PC isn't signed in, the run fails with a plain
  message; run `claude` in a terminal and use `/login`.

Tests: `python -m pytest services/claude-bridge/tests` (a stand-in `claude`, no tokens).

## Security note

The bridge listens on `0.0.0.0` (so Docker can reach it) and runs Claude Code, so
**every** door except `/health` wants `Authorization: Bearer <SKIPPY_HOME_TOKEN>`,
`/solid-gold` included (it used to take no authentication: closed 2026-09-30,
`tests/test_solid_gold_auth.py`), and with no token configured they all stay shut (503).
flask-api's client (`app/clients/claude_bridge.py`) sends the token from its own
environment, which the vault `.env` fills with the same value. Anything else on the
network can knock on port 9337 and gets 401.

## Boot persistence (deferred)

Currently launched manually with `make bridge`. To survive a reboot
without hand-launching, register a Windows scheduled task running on
user logon. See `misc/FUTURE_PLANS.md`.
