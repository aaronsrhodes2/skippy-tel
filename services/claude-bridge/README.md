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

## Boot persistence (deferred)

Currently launched manually with `make bridge`. To survive a reboot
without hand-launching, register a Windows scheduled task running on
user logon. See `misc/FUTURE_PLANS.md`.
