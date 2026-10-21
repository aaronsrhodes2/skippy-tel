# SkippyTel — Claude Code Context

**First time here?** Read [PLATINUM_RULES.md](../PLATINUM_RULES.md) — this project inherits all universal rules there.
**Shell: bash, even on Windows** ([§20](../PLATINUM_RULES.md)). Every command in this repo's docs is bash; don't reach for PowerShell.

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
│   ├── bilby_proxy.py   # GET /bilby/karaoke — the lyric line as JSON (the shelved phone apps poll it)
│   ├── vault.py         # GET /vault/bootstrap, GET /apk/<app>
│   ├── status.py        # GET /status.json — what the home reporter sends the portal
│   ├── portal.py        # /portal/{hud-state,chat-state,hud-frame,history,...} — the phone/glasses state relay
│   ├── translate.py     # POST /translate/text, POST /translate/audio
│   ├── image.py         # POST /image — Stable Diffusion proxy
│   ├── battery.py       # POST /battery — phone battery reporting
│   ├── starmap.py       # GET /starmap — star map data
│   └── health.py        # GET /health
└── clients/
    ├── ollama.py         # Default tier — OLLAMA_MODEL (the vault sets qwen3:8b)
    ├── anthropic_client.py  # "put on your thinking cap" tier
    ├── gemini.py         # "ask nagatha" tier
    ├── bilby.py          # Mac Bilby daemon client
    └── whisper.py        # Audio transcription
```

**Skippy core has no HTML interface** (the Captain's decision: the browser is the universal
client and its UI is the cloud portal, `portal/`). flask-api serves JSON, SSE, images, audio and APK
downloads only: the former `/status`, `/portal`, `/phone`, `/bilby/bigcard` and `/bilby/ui*` pages
are gone, and `tests/test_no_interface.py` fails if a page comes back or a data endpoint the home
reporter or the shelved phone apps use goes missing. A new human-facing page is a portal feature.

## Tier trigger phrases (in intent.py)

- Default → Ollama (OLLAMA_MODEL: qwen3:8b in the vault; config.py's fallback is still qwen2.5:7b)
- "put on your thinking cap" → Anthropic Claude
- "ask nagatha" / "ask gemini" → Gemini (Nagatha persona)

("trust the awesomeness" and "solid gold", the two Claude Code tiers, were removed 2026-09-30 at the Captain's request:
the private Skippy on the portal's `/me` has full access, so those verbs and flask-api's claude-bridge client are gone.)

## Adding a new route

1. Create `routes/my_route.py` with a Flask Blueprint `bp`
2. Add `from app.routes.my_route import bp as my_bp` in `main.py`
3. Add `app.register_blueprint(my_bp)` in `create_app()`
4. Rebuild: `docker compose up --build -d flask-api`

## Environment variables (.env)

Key vars:
- OLLAMA_MODEL — qwen3:8b (set in the vault; the code's fallback is qwen2.5:7b)
- ANTHROPIC_API_KEY — for Claude tier
- GEMINI_API_KEY — for Nagatha tier
- SD_BASE_URL — Stable Diffusion (default: http://host.docker.internal:5000)
- BILBY_MAC_URL — Mac Bilby daemon (default: http://skippy-mac:7334)
- BILBY_DRIVE_FOLDER_ID — Drive folder for streaming (pending Drive setup)

Secrets come straight from the vault: compose's `env_file` is
`G:/My Drive/vault/.env`.
.env changes: restart only (no rebuild): `docker compose up -d flask-api`
Code changes: rebuild: `docker compose up --build -d flask-api`

## Cloud: portal + games VM (GCP project skippy-tel)

The Captain doesn't run commands or fetch logs. Claude does, through GitHub
Actions (the sandbox can't reach GCP or run.app directly):

- **Deploy:** push to `main` (the only branch Google trusts; see "Branches and deploys") → `deploy.yml`
  (tests → Terraform `infra/gcp-games` → portal to Cloud Run). Redeploy without
  a code change: `workflow_dispatch` on `deploy.yml` (e.g. after a secret changes).
  **Time it / check before pushing:** `python tools/loop_timer.py --watch --since now` (before the push; `make loop-watch`) prints
  "prompt sent -> new version visible" stage by stage from GitHub's stamps and the portal's public `/version`;
  `python tools/affected_tests.py` runs only the tests the change can break (the full CI commands when it can't tell). docs/cicd.md "Timing the loop".
- **Operate/diagnose the games VM:** `workflow_dispatch` on `ops.yml` with
  `command` = `status` | `logs` | `start` | `stop` | `restart` and `args`
  (a game id for logs; game ids for start). Read the job log. Secrets are
  masked. `status` shows state, games, invite codes, missing settings.
  Don't stop/restart while people may be playing unless asked.
  On the VM itself (ops agent, infra/gcp-games/ops_agent.py; VM must be on):
  `tail <game> [lines]`, `search <game> <regex>` (whole container log),
  `stats` (CPU/mem/disk/per-container), `restart-game <game>` (one container,
  no VM reboot; for `garrysmod` it is stop, refresh the Mentat stage addon from GitHub, start: `gmod_addon.sh`). Requests go via metadata `ops-request`, replies on serial 3.
  The agent and the snapshot script (diag.sh) reload from metadata, so
  changing them needs only a deploy.
  `command` = `discord-setup` creates/repairs every game's Discord channels:
  it queues the job in VM metadata and the portal (which holds the bot token)
  runs it via POST /tasks/run (portal/tasks.py). New portal-side jobs go there.
- Portal: **https://makerportal.live** (the Captain's domain; www. and makerportal.studio redirect to it) —
  code in `portal/`, docs in `docs/portal/README.md` and `docs/cicd.md`. DNS is Cloud DNS in this project,
  hooked up by `tools/domains.py` (the `domains` workflow; idempotent, says what it waits for); the registrar
  (Squarespace) only holds the nameservers. Machines (home reporter, ops, loop timer, deploy smoke test) keep
  the fallback https://skippy-portal-1015050363686.us-west1.run.app, which never depends on DNS or a certificate.
- Portal design (the Captain's rule): **no top tabs; all cards, like a phone with
  apps.** Home (`/`) is a launcher, one tile per app; every other page is an app. A new
  feature is a module in `portal/modules.py` (it gets its launcher control), never a nav link.
  **The bridge** (2026-10-10, the one interface the whole portal is moving to; `docs/portal/README.md`
  → The bridge): a page opts in with `{% block ui %}bridge{% endblock %}` and `{% block glyph %}`;
  it is exactly one phone screen, **never scrolls and shows no scrollbar** (long content is paged or
  swiped, not scrolled), says things with **glyphs** from `templates/_glyphs.html` (no emoji, no
  words where a symbol will do; words only for names people look up: a server, a code, a password),
  and looks like a starship's controls (Maxolhx-inspired: cut corners, claws and fangs, lamps that
  breathe, a light running along a live control). On the bridge: launcher, Browser Games, the Graveyard, Skippy chat (paged conversation, glyph keys,
  sheets for language/tools/past chats/saving, and a living neon-spiral background:
  `static/bridge-alive.js`), Game Servers (a lit control per game, and each game's page as panes you flip
  through), Bilby's Audio Service (Home is a deck of glyph controls with counts; every list is shown a screen at a time with
  `PList`: swipe, PageUp/PageDown or drag the bar under it, a letter key for the alphabet; the player is a strip and a row of keys).
  Nagatha's Video Service (a deck of controls, posters a screen at a time, the film over everything, and it waits out a dead zone instead of failing).
  Admin (a deck of controls, each opening a pane of pages you flip; the explanatory sentences stay there, it is the one place that explains settings) and its server logs.
  The Dashboard (`/me`: a deck with the private Skippy first, which lays itself out as a paged chat, then a pane per widget).
  Experiments (the public inventory: tiles shown a screen at a time, a kind key, an about panel; the demos themselves are standalone pages).
  The experiment demos (the N-body shootout as a flip-book of plates from a real run of sigma-ground's shootout: `scripts/nbody_run.py`, `scripts/nbody_flipbook_build.py`; the double-slit, the Mentat playground, the benchmark).
  **Not yet** (old scrolling look, to migrate in batches): request access, privacy (Radiance and Weapon vs. Armor are separate apps).
- Copyable things (the Captain's rule: **one click, everywhere**): anything a person is expected to
  copy (an invite code, `ip:port`, a password, a command, a block to paste) is marked
  `<code class="code">` / `<pre class="copy">` (or `data-copy="text"` on a button), and
  `portal/static/copy.js` (loaded by base.html) gives it a Copy button and click-to-copy, also when it
  is added later by script. One box, one chip: an address and port that go in the SAME box are
  ONE chip (`ip:port`), never two copy operations; a dialog with separate Address and Port boxes
  (Satisfactory's Server Manager) gets one chip per box, side by side. Check the real dialog, not
  the docs. Skippy's chat renders `backticks` the same way.
- Secrets live in the Captain's Drive vault `.env`; he loads them into Secret
  Manager with `bash infra/bootstrap/secrets.sh --from-env ~/.env` in Cloud Shell.

## Home → portal bridge (home reporter)

The cloud portal can't reach skippy-pc, so skippy-pc reports out:
`services/home-reporter/reporter.py` (in `docker-compose.override.yml`, which
Compose merges automatically: a plain `docker compose up -d --build`) POSTs every
~15 s to `https://…run.app/home/report` with `Authorization: Bearer
$SKIPPY_HOME_TOKEN`. It sends flask-api's `/status.json` (the old `/status`
page as data; the page itself is gone), `/services` availability, `/music/session`, and Mac Bilby's
`/bilby/status`, setlist and next suggestion (+ album art when the track
changes). The reply carries queued controls (`music` skip/pause/resume/stop)
and jobs; while someone chats, the reporter also polls `GET /home/jobs` every
1.5 s and answers with `POST /home/jobs/<id>`.

- Core health: the report also carries flask-api's `/health` (`health`). The
  portal's `home.core_check()` turns it into a verdict (`ok`/`degraded`/`down`)
  plus plain-English problems: the **Core · skippy-tel** widget on `/me`, the
  Watcher lines, and `core` in `ops home`. `/health`'s `models.ollama` carries
  `default` and `default_present`, because an up Ollama missing the default
  model still says `status: ok` while every default chat 503s.

- Physics in Skippy's chat = `ask_mentat` → a job → `mentat-home` (same
  override file: mentat/Dockerfile with `MENTAT_USE_LLM=1`, Qwen via
  `ollama:11434`). No Qwen → Mentat answers 503 and Skippy says it's offline;
  there is deliberately NO fallback to the cloud copy (keywords only), which
  serves the public Experiments playground.

- Mentat for Claude (Desktop, claude.ai, phone, Claude Code): the **cloud** copy's `POST /mcp`
  (`mentat/app.py`: MCP Streamable HTTP, stateless, JSON replies; the same public tools and guards
  as `/tools/<name>`, no Qwen, so no GPU), added once at claude.ai → Customize → Connectors →
  custom connector `mentat` = `https://skippy-mentat-1015050363686.us-west1.run.app/mcp`.
  Not a `docker exec` into `skippytel-mentat`: cloud sessions can't reach skippy-pc, and each
  exec was its own server process. Smoke-tested by `deploy.yml`.
- Token: dashboard → "Pair home" shows it once; the portal keeps only its
  sha256 in VM metadata `portal-home`. Put the line in the vault .env and
  SkippyTel's .env.
- Portal side: `portal/home.py` (in-memory; the portal runs max 1 instance), Home widget on `/me`.
  The old `/bilby` page (a mirror of the Mac's decks, with phone-session controls) is gone: `/bilby` and everything
  under it answer a 301 to `/music`. The old "bilby" grant reads as "audio" (friends.portals_of). Grants are one per friend-grantable tile (`catalog.PORTALS`: skippy, each game, audio, video, play, experiments); Dashboard and Admin are never grantable. What the
  reporter still sends for it (the Mac's decks and setlist, the album art, the queued `music` commands) is read by
  `/me`'s widget and the chat tool only.

## Bilby's Audio Service (the browser music player, `/music`)

The Captain's collection in any browser (the Captain, and friends granted "audio" in Admin > Friends), a tile on the launcher: `portal/templates/music.html` +
`portal/static/bilby-{player,app}.js` + `bilby-player.css` (portal colour variables only) + `portal/player.py` (the pass and
Connect). The audio goes browser → Tailscale Funnel → `services/music-gate` with a pass the portal signs; it never passes
through the portal. A browser plays sound only after a tap on that page load, so after any reload or deploy the player shows
a "Tap to start the sound" bar once (it publishes `armed` in its Connect check-in so another device's remote can say so).
Run it beside the REAL gate on this machine with `python portal/tests/dev_music.py`. Full description, the autoplay
rule and the tests: `docs/portal/README.md` → Bilby's Audio Service.

## Nagatha's Video Service (the browser video app, `/video`)

The Captain's Jellyfin films in any browser (the Captain, and friends granted "video"; a friend's viewing never moves his Continue Watching), a tile beside Bilby's: `portal/templates/video.html` +
`portal/static/nagatha-{core,app}.js` + `nagatha.css` (portal colour variables only, amber accent) + `portal/video.py` (the pass, label
`video-gate-v1`: a music pass never opens it). The film goes browser → Tailscale Funnel `:8443` → `services/video-gate` → Jellyfin; never
through the portal. The reporter's `video_gate` capability (`VIDEO_GATE_URL`/`VIDEO_GATE_PUBLIC` in `docker-compose.override.yml`) is ready
only when the gate's `/health` says `ready`. Direct play when the catalog says `direct`, else HLS (hls.js self-hosted in
`portal/static/vendor/`, Safari's own on Apple). Run it against the REAL gate on this machine with `python portal/tests/dev_video.py`.
Full description and tests: `docs/portal/README.md` → Nagatha's Video Service.

## The Tabletop (Heroes of Chance, `/tabletop`)

The Captain's Daggerheart campaign (the lore is in `../heroes-of-chance`), as one page: a card per hero along the top, the Fear in the
top bar, and the one cork board below, where everything else about the game is added and shown. No campaign deck, no board list.
- **Heroes:** every field of the official sheet (`tabletop.SPEC`; a field not in SPEC can't be stored), a project, a folder, an
  avatar (cropped by a focus point and zoom), a print view; a GM-only quick reference per hero; the GM's Fear, kept between sessions.
- **The cork board:** every card is shown, fogged (a fog patch, nothing of what it is) or hidden (GM-only), per the GM; redacted
  server-side in `Tabletop.view()` / `image()`, never just hidden on screen.
- **Storage:** the private bucket `skippy-tel-tabletop`, made by `infra/bootstrap`, so a new bucket needs a bootstrap re-run in
  Cloud Shell. The portal reaches it through its runtime identity.
- **Code:** `portal/tabletop.py`, `templates/tabletop*.html`, `static/tabletop{,-heroes}.{js,css}`.
- **Full description:** `docs/portal/README.md` → The Tabletop.

## Home is the app portal (`/`); Skippy the Magnificent is the chat app (`/chat`), in two modes

`/` is Home: signed out, the Google sign-in; signed in with the chat grant, **Skippy's chat itself** (the star, 2026-10-20); without it, the
launcher. The launcher stays at `/apps`; inside the chat the other apps are a tray (one grid key on a phone, a slim row under the box on a wide
screen; `/apps/tiles` feeds it) and prior chats are a sheet (kept in that browser's localStorage only, never on the portal). No side bars anywhere. **Which model answers is `portal/brains.py`'s call, on the server, never the page's**
(2026-10-19): **guests** get Skippy Core only (the local model on skippy-core, reached through its Tailscale Funnel + `SKIPPY_CORE_API_KEY`; the same
Anthropic SDK, another base_url) with the portal's public tools (Mentat, game status); if Core is down they get Mentat straight, never a paid model.
**The Captain** picks in the brain sheet: Skippy Core (default), Claude Haiku/Sonnet/Opus, Gemini free/paid. Every brain calls Mentat for physics; the
reply carries the ⚛ card (Mentat's result and proof) with **Barney Style?**, which always runs on Skippy Core with no tools. A reply that claims Mentat
without calling it is thrown away and Mentat is forced (or asked by the portal itself). Dev: `portal/tests/dev_chat.py` (real Core over SSH, real Mentat).
**One chat** (2026-10-21): the Captain's AGENT (`templates/_workshop.html`, Claude Code on skippy-pc) lives INSIDE it as two more brains in
his brain sheet, "Agent · Claude" and "Agent · Skippy Core coder": picking one shows the agent's transcript in the chat's stage with a folder
chip, and the chat's composer, New chat, Stop and chats sheet drive it through `window.SkippyAgent` (the agent's engine is unchanged). No top
switch. Purple while an agent brain is picked (`html.agent`), green otherwise. Chats are kept per person (a one-way id of the address keys
the browser's storage). A Core-brain agent run gets the Captain's skills and plugins (never his login) and the llama.cpp prompt-cache fix. `/me` is the Captain's private MCP access (list + test his servers, through the bridge) and Home integration only: the other panes are Graveyard entries (`modules.py`, ids `me-<pane>`): the Captain retires one in the Admin Graveyard when its telemetry (`pane:<id>` taps, the Home pane's "Least-used UI") says nobody opens it, and revives it there. The Experiments show/hide moved to the Experiments page's About sheet. Showing the agent only to him is a convenience, not the security: every `/me/workshop/*`
call checks for the Captain again and needs the `X-Workshop` header, and
**`portal/tests/test_route_access.py` classifies every route (public, machine, beacon, member, grant, admin) and fails on any new unclassified one
or any under-guarded one**, probing nobody, a signed-in stranger and a chat-only friend. A new route must be added to its REGISTRY.
The animated 3D Skippy (`templates/_skippy3d.html`, `static/skippy-3d.js`, model in `static/skippy/`) lives in the corner of `/chat`.

**The starter** (`tools/skippy_starter.py`, `portal/starter.py`): skippy-pc's always-on service manager on the Windows host. The portal's Admin > Recovery card asks it (outbound-polling, home token, three operations only: start, stop with a back-on time, restart) to bring the whole stack up or down, even when Docker and the bridge are down. Details: docs/portal/remote-recovery.md.

**Full screen on phones and tablets:** `/manifest.webmanifest` (public) asks for `display: fullscreen` (falling back to standalone), so Add to Home Screen opens the
portal with no browser bars; the music and video apps keep their own manifests (their pages override `{% block pwa %}`). The top strip also has a
full-screen key (`#fs-key`, shown only where the browser has the Fullscreen API: Android Chrome, iPad; on iPhone it shows how to Add to Home Screen).
It hides itself when already opened from the home screen. Tests: `portal/tests/test_fullscreen.py`.

## The private Skippy's chat (on the front page, the Captain only): a transcript, not pages

Unlike the phone-sized bridge pages, this chat is a plain **scrolling transcript** (a long agent run must not be cut into columns; the exemption
was Skippy's own proposal, agreed 2026-10-11). A run of tool calls folds into one line ("14 steps: Read, Bash") that opens to each command. A
browser that has never opened the chat opens the **latest conversation** from skippy-pc instead of starting a blank one (New chat is the
explicit way to start one). **Pictures** (UI-bug screenshots): paste, drop or pick up to 4; the page shrinks each to a JPEG, the portal checks
them (`workshop.clean_images`), the job carries them to the bridge, which saves them under `.skippy-workshop-attachments/<task>/` beside the
sessions file (swept after 14 days) and names the paths in the message so Claude Code opens them with Read. Tests:
`portal/tests/test_workshop_attachments.py`, `services/claude-bridge/tests/test_bridge_attachments.py`, the UI harness in `test_workshop_*ui.py`.
The bridge only picks this up on restart (refused while a run is going).

## Skippy's voice (mic, voice-to-text, text-to-voice in both chats, on the front page)

A mic key and a speaker key on each chat, on any device with a current browser: `portal/static/skippy-voice.js` (MediaRecorder, no browser
vendor speech cloud) + `portal/voice.py` (the pass, label `voice-gate-v1`; scopes `listen` for everyone who may chat, `speak` for admins only,
because the cloned Skippy voice is personal-use) + `/chat/voice/gate` (the pass route). The recording goes browser → Tailscale Funnel `:10000` →
`services/voice-gate` → Whisper at home (flask-api's ElevenLabs for Skippy's voice; its key never leaves home); never through the portal.
Non-admins (and any failure) hear replies in their device's own built-in voice. The reporter's `voice_gate` capability (`VOICE_GATE_URL`/
`VOICE_GATE_PUBLIC`) is ready when Whisper answers behind the gate. Start it: `docker compose up --build -d voice-gate`, then
`tailscale funnel --https=10000 --bg 3007` (the bridge's `funnel on` now does all three doors). Tests: `portal/tests/test_voice.py`,
`services/voice-gate/tests` (run in their own pytest session).

## Star Control Zero (the Captain's game in the browser, `/starcontrol`)

The fan game from `github.com/aaronsrhodes2/star-control-precursors`, compiled to WebAssembly (pygbag) and played in
the browser by every signed-in person (core and friends alike; a card in the Browser Games app, `/play`; it says "coming soon" until the
image carries the game). The portal only hands out files: `portal/starcontrol.py` + the `starcontrol` blueprint in `app.py`
(`/starcontrol/` the page with the app bar injected, `/starcontrol/<file>` the bundle parts and the music,
`/starcontrol/version` public, read by the deploy's smoke test). The files are NOT in this repo: `portal/Dockerfile`
runs `portal/starcontrol_fetch.py`, which downloads the game repo's GitHub Release pinned in `deploy.yml`
(`STARCONTROL_REF` + `STARCONTROL_SHA256`), checks the hash and unpacks it into the image. **To ship a new build of
the game:** in the game repo run `python tools/build_web.py --tarball`, publish `build/scz-web.tar.gz` as a release,
then change both pins here and push. Keyboard or controller only; a phone or tablet waits for a controller before it
downloads anything. Saves stay in the player's browser. Full description: `docs/portal/README.md` → Star Control Zero.

## Private Skippy (the Workshop: left side of `/me`)

The Captain's own chat is Claude Code (Sonnet 5.5 at medium effort, hard-coded in the bridge, all the time; it was Opus until 2026-10-10) on skippy-pc, admin
only, focused on the ask → change → test → push → deploy → verify loop, not linked to
skippy-tel-core's services. Portal: `portal/workshop.py` + `/me/workshop/*` (job kind
`workshop`, capability `workshop`); reporter: `reporter.workshop()`; engine:
`services/claude-bridge/workshop_engine.py` + `workshop_prompt.md` (one Claude session per conversation).
Every project in `services/claude-bridge/projects.json` (SkippyTel and the Captain's other repos), each in its
own clone `D:\Aaron\development\<name>-workshop`, never his checkout; **or any folder directly under
`D:\Aaron\development` opened IN PLACE** (ids `f-<slug>`, "· folder" in the picker, the Captain's choice 2026-10-20: the
agent works right in it, git is the undo, its prompt opens with "never discard his uncommitted work"); **a brain per
conversation**: Claude (Sonnet) or Skippy Core's coder (Claude Code with its own `CLAUDE_CONFIG_DIR` pointed at skippy-core's
llama-swap via SKIPPY_CORE_URL/KEY; offered only when the box answers); one run at a time across all of them; a
Conversations list to reopen and continue any conversation; a ship card per push (`portal/ship.py`: the deploy run
for that commit, green only when `/version` serves it); a Discord DM to the Captain when a run ends (`portal/notify.py`,
once per run, switch on the card; `DISCORD_CAPTAIN_ID` in the VM settings, else the server owner); a screenshot of a
changed portal page under the answer (`tools/workshop_screenshot.py` + `SKIPPY_IMAGE:` line). Survives a portal redeploy
mid-task. Rules for anything like it: [PLATINUM §19](../PLATINUM_RULES.md). Full
description and setup: `docs/portal/README.md` → Private Skippy. Needs `make bridge` on
the host and `claude` signed in (`/login`) there. When it or the portal stops answering from
away: [docs/portal/remote-recovery.md](docs/portal/remote-recovery.md) (the phone-first runbook,
the `rollback` workflow, `make bridge-supervised` and `tools/install_bridge_startup.sh`).

## Local capabilities (skippy-pc's hardware, for the portal chat)

The portal chat can use skippy-pc's own hardware, free of tokens, through the same job
queue as `ask_mentat`. The reporter advertises what's ready (`capabilities` in each
report: a service is ready when its `/health` answers); the portal (`home.capabilities()`)
offers only those tools, and `tool_policy.py` decides who may use them (unlisted =
admin-only). Long jobs: `home.start()` then `peek()`/`wait_result()`; audio comes back
through `PUT /home/media/<job_id>` (`portal/media.py`: in memory, 30 min, audio types
only, 30 MB) and plays from `GET /chat/media/<job_id>` (its requester or an admin).

- **`make_music`** (admin only): `services/music-studio` is ACE-Step 1.5's own REST
  server: a pinned commit, weights mounted read-only from the models archive
  (`D:\models\huggingface\ACE-Step__Ace-Step1.5`), offline, no published port. The
  reporter unloads Ollama's models first (`OLLAMA_KEEP_ALIVE=-1` would otherwise keep
  them in VRAM), then release_task, poll, download, upload; one GPU job at a time. The
  Captain's rule of thumb (why it's local): no tokens, and nothing to wait on or pay for.
- **Adding one**: a job kind in `reporter.run_job`, a probe in `reporter.capabilities()`,
  the name in `home.KNOWN_CAPABILITIES`, a tool in `chat_tools.py`, an audience in
  `tool_policy.py`, and tests on both sides.
- Models live in `D:\models` (`tools/models_archive.py`: verify with `python
  tools/models_archive.py verify`). Ollama models are pulled into the Docker Ollama
  (`skippytel_ollama-data`), a different store from the host Ollama's.

## Join passwords: shown in plain text, on purpose (not a finding)

The Captain doesn't care who logs into his game servers. What a stranger can do to him in a
game is small and is undone from backups (next section). So a game's **join password** appears
in plain text, on the game page, the dashboard and in the Discord copy, and the join flow
carries **no extra friction**: no reveal click, no sign-in beyond the portal's own, no
"copy it from the admin page".
Don't report that as a security issue in a review or audit, and don't "fix" it.
Still guarded: admin passwords (`*_ADMIN_PASSWORD`), API keys and tokens, the Google
sign-in, the Admin page (secret settings stay masked), the Captain's private Skippy.
The value itself never goes in the repo or in chat (it lives in the vault `.env`).

## Game backups: flagged files, easy rollback of a bad day

Each game's world lives in a few specific files; those are the **backup targets**, flagged in
one place: `TARGETS` in `infra/gcp-games/game_backup.py`. At every boot, with the game
containers down, `startup.sh` archives them per game to `data/backups/<game>/<UTC>-<fp>.tar.gz`
(skipped when nothing changed since the last archive; the newest ones are kept). That archive
is the state the session *started* from, so it's the rollback point for a bad day. The daily
disk snapshot (3 am, 7 days) stays as the disaster copy, since the archives sit on the same disk.
Roll back through the `ops` workflow: `backups <game>` lists, `restore-game <game> <archive>`
puts one back (the current files are set aside, never deleted). Adding a game or a new save
file means adding it to `TARGETS`; the tests fail if a compose game has none.

## EverQuest (EQEmu) on the games VM

Not a profile in games/docker-compose.yml: `infra/gcp-games/everquest.sh`
(metadata `everquest-script`) runs AkkStack (pinned commit) under
`data/everquest/akk-stack` on the world disk. First start: AkkStack's own
installer (~15-20 min). Ports: world 9000, login 5998-5999, zones 7000-7030/udp,
chat 7779 (moved off 7778 for Dragonwilds). Only game ports are published.

**The login server is switched on by config, not by the database.** Only the DB comes from home; the launcher
starts the login server only when `web-admin.launcher.runLoginserver` is true and the world registers with it at
`127.0.0.1:5998`, so `everquest.sh` sets both every start (a fresh VM had world + zones but nothing on 5998/5999:
every login said "a connection to your server could not be reached"). It says "online at" only when UDP 5999 is bound
inside the container. `ops restart-game everquest` re-runs the newest script from metadata without a reboot. RoF2's
`eqhost.txt` is `[LoginServer]` / `Host=ip:5999`, not Titanium's `[Registration Servers]` braces.

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
(infra/bootstrap/main.tf). This is a solo repo (no contributors but the
Captain and Claude), so per [PLATINUM_RULES §17](../PLATINUM_RULES.md):
**no pull requests**. Commit and push straight to `main` (test.yml and
deploy.yml run on the push; deploy.yml re-runs the tests before it deploys).
skippy-pc and Cloud Shell track `main`.
