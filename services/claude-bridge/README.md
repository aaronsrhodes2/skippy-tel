# Claude bridge

Tiny HTTP service that runs **on the Windows host** (not in docker) and
shells out to the Claude Code CLI: the engine of the private Skippy on the portal's `/me`.
The home reporter reaches it via `host.docker.internal:9337`, and it runs on Aaron's
Claude Code subscription (via `~/.claude/.credentials.json`), not the metered API key.
(The old `POST /solid-gold` door, behind the "trust the awesomeness" and "solid gold" voice
tiers, was removed on 2026-09-30 at the Captain's request: the private Skippy has full access.)

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

## Endpoints

### `GET /health`
```json
{"status": "ok", "service": "claude-bridge", "cwd": "...", "claude": "...",
 "workshop": {"ready": true, "model": "claude-opus-5-5", "busy": false, "login": "ok"}}
```
`workshop.login` is `"ok"`, `"expired"` or `"unknown"`: the bridge asks `claude auth status`
in a background thread (15 s timeout, answer kept 120 s), so `/health` never waits on it.
`unknown` (nothing answered yet, or a timeout / error / unreadable output) does not count
against the chat; only a clear "not logged in" does. `ready` is false when the token isn't
configured or the login is `expired`, and then `workshop.reason` says why in plain words
(for example "Claude's login on skippy-pc expired: run `claude auth login` there").

The token is re-read from the environment or the vault `.env` (on Google Drive) every 30 s, and
**the bridge keeps the last non-empty one**: a read that comes back empty (Drive restarting,
paused, signed out, not mounted yet) never replaces it, so a Drive hiccup cannot flip
`workshop.ready` to false or turn every route into a 503 under a run in flight. Only another
non-empty value replaces it (re-pairing home still needs no restart). With none held at all
(the bridge started before Drive mounted) it looks again every 5 s.

### The private Skippy: `POST /workshop`, `GET /workshop/run/<task>`, `GET /workshop/runs`, `POST /workshop/cancel/<task>`

Claude Code with **full permissions** (`--dangerously-skip-permissions`) on the
SkippyTel repo, driven from the Captain's portal dashboard (`workshop_engine.py`).
These four need `Authorization: Bearer <SKIPPY_HOME_TOKEN>` (the token the home
reporter holds; read from the environment or the vault `G:/My Drive/vault/.env`).
With no token configured they answer 503 and stay shut.

- **Model:** `claude-opus-5-5`, a constant in `workshop_engine.py`. No environment
  variable and no request field changes it.
- **Where it works:** its own clone of the repo (`WORKSHOP_REPO`, default
  `D:\Aaron\development\SkippyTel-workshop`, cloned on first use), never the
  Captain's checkout. It deploys skippy-pc services from `WORKSHOP_PRIMARY`.
- **Instructions:** `workshop_prompt.md` (loaded from here, never from a request):
  the ask → change → test → push to main → deploy → verify loop, plus what it must ask first.
  **It reaches Claude as a file, never as an argument.** On Windows `claude` is npm's `claude.cmd`,
  and `cmd.exe` cuts a command line at the first newline: when the multi-line prompt was one
  `--append-system-prompt` argument only its first line arrived and `--resume` and `--add-dir`
  were dropped with it (so the guardrails were never in force and follow-ups never resumed). Now,
  on every task, the prompt is rendered (`{repo}`/`{primary}` filled in) into
  `.skippy-workshop-prompt.txt` next to the sessions file and passed as
  `--append-system-prompt-file`; the message goes by stdin. **No argument may contain a newline:**
  the engine refuses to launch one. If the file can't be written the run fails with a plain
  message instead of starting without its rules. The launch also passes
  `--system-prompt-snapshot off`: by default Claude Code records the prompt on a conversation's
  first request and replays that record on every resume, so a chat begun under the old
  one-line prompt (or before an edit to `workshop_prompt.md`) would keep the stale rules.
  Needs a `claude` that has both flags (verified on 2.1.285).
- **Sessions:** one Claude Code session per chat conversation, remembered in
  `.skippy-workshop-sessions.json`, so follow-ups have the context of the last.
- **Runs:** `POST /workshop {task, conv, message, who?, attach?}` starts one (asking again
  with the same `task` re-attaches instead of re-running); `GET /workshop/run/<task>?after=N&wait=S`
  returns events from index N (`{t:"text"}` and `{t:"tool", name, detail}`, never
  tool output; commands are redacted for credentials). One task at a time (409 when busy).
  `who` is the owner's email (at most 200 characters, else 400): the bridge only keeps it, so
  the reporter can tell a portal that forgot a run (a deploy wipes it) whose chat it belongs to.
  With `attach: true` an unknown task answers `404 {"error": "unknown_task"}` and **nothing
  starts**, so a relay that is only re-attaching can never begin a Claude run; the message may
  be empty then.
- **Listing:** `GET /workshop/runs` returns `{"runs": [{task, conv, who, events, done, ok,
  error, age_s, finished_age_s}]}`, newest first, at most 50: counts and state only, never the
  message or any event's content. The reporter compares it with the portal's records every few
  seconds and repairs any difference.
- **Surviving a restart:** every run is also saved to `.skippy-workshop-runs/<task>.json`
  next to the sessions file (written when it starts, at most every 5 s while it runs, always
  when it ends; atomic; the message text is not saved). When the bridge starts it takes back
  the runs younger than an hour. A run that was still going is closed as failed with "The
  bridge restarted while this was running." so the chat shows an ending instead of waiting.
  A disk that fails to write is logged once and never touches the run. Pruning an old run
  deletes its file too.
- **Log:** one stderr line per request, except successful (2xx) `GET /health`,
  `GET /workshop/run/*` and `GET /workshop/runs`, which the reporter polls every few
  seconds and which would otherwise fill the log under a supervisor. POSTs and every
  error (4xx/5xx) are always logged.
- **A run has a clock (the watchdog):** `MAX_RUN_S` (default 2 hours of wall clock) and `IDLE_S`
  (default 30 minutes without a single line of output; a long test run is silent for minutes, not
  half an hour). Either one kills the whole process tree (`taskkill /T` on Windows, the process
  group elsewhere) and finishes the run failed with a plain message ("Claude Code went quiet for
  30 minutes (a command or a sign-in prompt may be stuck), so I stopped it. Send the message again
  to retry."). A run that had to be killed is not resumed. Before this, one hung run (a credential
  prompt with nobody at the PC, a stuck command) kept the bridge Busy, every later message got
  HTTP 409, and nothing short of restarting the bridge ended it. The limits are constants in
  `workshop_engine.py`; Stop also works when a stray grandchild still holds the output pipe.
- **Nothing waits for a person:** `claude` and the engine's own `git` calls run with
  `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never` (and git with no stdin), so a missing
  credential fails in about a second with a readable error instead of opening a Git Credential
  Manager window nobody will click. Neither gets `SKIPPY_HOME_TOKEN` (this door's key).
- **Login:** if `claude` on this PC isn't signed in, the run fails with a plain
  message; run `claude` in a terminal and use `/login`.
- **A session that can't be found:** claude 2.1.285 reports it as `{"is_error": true, "errors":
  ["No conversation found ..."]}` with no `result`; the engine reads both shapes and starts a fresh
  session (the old, never-delivered `--resume` meant this path used to be unreachable).

Tests: `python -m pytest services/claude-bridge/tests` (a stand-in `claude`, no tokens). Two
Windows-only tests (skipped elsewhere, so CI never sees them) run a whole run through a `.cmd` shim
and the real `claude.cmd --version` through the engine's own launch path with a multi-line prompt
file; they are the ones that would have caught the newline truncation.

## Security note

The bridge listens on `0.0.0.0` (so Docker can reach it) and runs Claude Code, so
**every** door except `/health` wants `Authorization: Bearer <SKIPPY_HOME_TOKEN>`
(`tests/test_door_auth.py`), and with no token configured they all stay shut (503).
The token check comes before any input is looked at, so a stranger learns nothing about the rules.
The home reporter sends the token from its own environment, which the vault `.env` fills
with the same value. Anything else on the network can knock on port 9337 and gets 401.

## Boot persistence (deferred)

Currently launched manually with `make bridge`. To survive a reboot
without hand-launching, register a Windows scheduled task running on
user logon. See `misc/FUTURE_PLANS.md`.
