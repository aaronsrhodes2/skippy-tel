# Maker Portal

One page, gated to you and whoever you allow: game servers with their
Discord channels, the other services, a text chat with Skippy, and an admin
page. Code in [`portal/`](../../portal); deployed on every push by
[CI/CD](../cicd.md) to Cloud Run as `skippy-portal`.

| Page | Who | What |
|---|---|---|
| `/dashboard` | allowlist | Server status, start (game picker + auto-sleep) / stop, per-game connect info, `#game` and 🔊 voice Discord links |
| `/chat` | allowlist | Text chat with Skippy (Claude, Skippy persona) |
| `/admin` | admins | Game settings (world names, passwords, Dragonwilds owner ID), **Restart & update**, **Set up / repair Discord channels**, the **Recovery** cards (below) |
| `/starcontrol/` | everyone signed in | **Star Control Zero**: the Captain's fan game, played in the browser; a card in Browser Games (`/play`) (see below) |
| `/doomloop/` | everyone signed in | **Doom Loop**: the Captain's work-in-progress game, the newest build from its repo, with a dev-build badge; a card in Browser Games (`/play`) (see "Doom Loop dev channel" below) |
| `/music` | admins | **Bilby's Audio Service**: the Captain's music collection streamed from skippy-pc to any browser (see below). `/bilby` redirects here |

## Friends: play without the Captain online

The server lives in Google's cloud on its own persistent disk. Nothing about
it depends on your PC or on you being signed in.

1. **Grant access:** Admin → **Friends** → their email + the games they may
   see → **Add friend**, then **Invite ✉** (opens a ready-written email in your
   own mail app). Instant, no deploy. Any Google account works, Gmail or not.
   Friends see, start and chat about only their games, and may stop the server
   only when every running game is theirs. Stored in the VM's `portal-friends`
   metadata ([`portal/friends.py`](../../portal/friends.py)); `ALLOWED_EMAILS` in
   the deploy workflow stays for core users who always see everything.
2. **They play:** Games app (a grid of cards like the launcher; the game that's up glows) →
   tap the game's card → **Play RuneScape: Dragonwilds** on its page. The VM
   wakes, the server updates itself and loads the saved world. The tile shows
   "Starting — booted N min ago" until it's typically joinable (~6 min), then
   the join steps (an invite code and the address to copy, where to type them) and the join
   password, shown in plain text on purpose (see `CLAUDE.md`, "Join passwords").
3. **It cleans up after itself:** after the auto-sleep window with nobody
   playing, worlds are saved and the VM powers off. The next friend to click
   Play wakes it again, with the same world.

**Asking for access.** Each game's pinned Discord post has a **Maker Portal** field
linking to that game's page (`/games/<id>`; the link's host is `PORTAL_URL` in the VM's
settings, which the portal's Discord setup fills in from the address it is served on, so a
custom domain later needs only a re-run of setup). Someone without access signs in with
Google (a link to a game page survives the sign-in) and lands on **Request access**: pick a
game, add a short note, send. The request is stored with the email **Google verified**
(nobody can ask for an address they don't own; typed emails are ignored) in VM metadata
`portal-requests` (`portal/access_requests.py`; Terraform ignores it). Until approved they are
a "visitor" (`session["visitor"]`): they can reach the request page and nothing else. It shows
up in **Admin → Friends** above your friends, with **Approve** (the ordinary add-friend; it
**adds to** what an existing friend already has, and needs something ticked) and **Dismiss**,
plus a badge on the Admin tile and a line on the dashboard. Once approved, the person opens the
Discord link again and is in: no second sign-in or second request. Approving part of a
request leaves the rest waiting (and says so); Approve only ever *adds* to a friend's access.
Limits: one request per email (asking again adds games), 3 per hour per person, 6 per address
(an IPv6 address counts as its /64), 60 overall; at most 50 waiting, and a full list turns
*new* people away rather than evicting anyone already waiting; requests expire after 60 days.
Name and note are one short plain line (control, invisible and blank-looking characters and
"zalgo" combining marks removed). The friends list and the queue are read fresh and **refuse to
write if that read failed**, so a VM API hiccup can't wipe them; failed writes are reported, never
a raw error. One caveat: the lock is per process, so two portal revisions overlapping during a
rollout could lose a simultaneous request (the portal normally runs one instance).
**A custom domain later:** set `PORTAL_URL=https://your.domain` in the portal's deploy settings and
re-run Discord setup; the pins (and the VM, via `PORTAL_URL` in its settings) then link there. The
per-address limit reads the last `X-Forwarded-For` hop, which is right behind Cloud Run's own front end.

Guardrails for non-admins: auto-sleep can be 15–120 minutes, never "off";
**one game at a time, and only the Captain changes it**: a friend can only
wake the game the server is currently set to, and asking for another one
(in the page or in Skippy's chat) says to ask Aaron to switch it (`ops.start_games`;
the attempt shows in the dashboard's Watcher). The launcher's Games tile
names the game that's up, or the one set for the next wake-up; settings,
Restart, Discord setup and starting several games at once are admin-only.

**Set a world password.** Dragonwilds lists the world publicly, so without a
password anyone who searches the name can join. With one set, the portal
becomes how the password reaches your friends: signed-in players see it on
the tile, and the Discord post points to the portal instead of printing it.

**How fast is the popup?** Roughly: VM boot ~1 min, server update check +
world load ~3–5 min. Right after a Dragonwilds patch, the first start also
downloads the update (a few extra minutes). The Discord channel gets a
"starting up" ping, so people can wait there.

## How it fits together

- **Auth:** Google sign-in; `ALLOWED_EMAILS` / `ADMIN_EMAILS` + the Friends list re-checked on
  every request ([`portal/auth.py`](../../portal/auth.py)).
- **Games:** the portal runs as `skippy-agent` (roles/compute.admin) and
  drives the games VM through the Compute Engine API
  ([`portal/gcp.py`](../../portal/gcp.py)). Server **settings** live in the
  VM's `games-env` metadata; the VM writes them to `/srv/games/.env` on each
  boot. So: edit settings → Restart → applied. No `.env` files, no `nano`.
- **Restart = update:** every boot re-pulls the server images, and
  Dragonwilds/Valheim update their game files on start.
- **Server admin card (admin only):** on each game's page, in the Admin block: installed version, image age, mods, when it
  last updated, the update policy in effect. The facts come from the VM's snapshot (`diag.sh` runs
  [`game_facts.py`](../../infra/gcp-games/game_facts.py) every 2 minutes; [`portal/diagnostics.py`](../../portal/diagnostics.py)
  parses it; [`portal/admin_panel.py`](../../portal/admin_panel.py) builds the card and its one refresh route,
  `GET /admin/games/<game>/panel`, admin-only: a friend gets 403). Controls are ordinary Admin settings. Per-game details:
  [docs/games/MODS_AND_UPDATES.md](../games/MODS_AND_UPDATES.md).
- **Recovery cards (admin only, on `/admin`):** **Live version** (the serving commit and revision from `/version`, and how
  many pushes `main` is ahead of it), **Deploy pipeline** (the newest deploy runs; Roll back, Undo a rollback, Re-run the
  failed jobs of the newest deploy, Cancel a deploy stuck for over 20 minutes) and **Private Skippy** (cancel the current run,
  clear a stuck record, pause). Code: [`portal/recovery.py`](../../portal/recovery.py) (the rules and the GitHub client) and
  [`portal/recovery_web.py`](../../portal/recovery_web.py) (`POST /admin/recovery/<action>`). Every button is an admin-only
  POST from the portal's own page (Origin, else Referer), confirmed (the page asks, then sends `confirm=yes`; without it the
  server asks on a page of its own), rate limited per action and logged as one stdout line
  `{"event": "recovery", "action", "by", "result", "ok"}`; the last 20 show on the card. None of it is a chat tool. The deploy
  buttons use `GITHUB_ACTIONS_TOKEN` (fine-grained, this repo, Actions read/write only; deploy.yml copies the repo secret
  `PORTAL_ACTIONS_TOKEN` into Secret Manager), which never enters `app.config`; the client can call five endpoints (list deploy
  runs, one run's jobs, dispatch `rollback.yml`, re-run failed jobs, cancel a run), checked inside the one method that sends,
  never follows a redirect, and turns errors into plain sentences. Without the token the pipeline card says "Not configured".
  The pause lives in portal memory (a deploy clears it). What to press when: [remote-recovery.md](remote-recovery.md).
- **Discord:** the admin button ([`portal/discord_admin.py`](../../portal/discord_admin.py))
  creates `#game` + 🔊 voice channels, a webhook and a pinned status post per
  game, and stores the IDs/webhooks in `games-env` — which is where the
  dashboard's links and the VM's status posts both come from.
- **Stops are graceful however they happen:** the VM's shutdown-script
  saves worlds and posts to Discord for the portal's Stop button, the idle
  watchdog, or a manual stop alike (the watchdog posts its own "went to sleep", with the
  reason, and leaves a note so the script doesn't post a second one).

## Chat

Direct Claude API (`claude-haiku-4-5`: cheap and quick for server questions; set by `CHAT_MODEL` in the deploy workflow — Opus 5-family models also get the server-side refusal fallback), with
Skippy's persona from `services/flask-api/app/skippy_persona.md`. Everyone,
the Captain included, gets that default model whatever they type: there is no
model picker and no phrase that upgrades it (the Captain's Opus is meant for
his private dashboard chat, not this one). History
lives in the browser tab and is sent with each message; the server keeps
nothing.

**Server tools** (`portal/chat_tools.py`): Skippy can check the server's
status, start games and stop the server; admins also get restart. Same
rights and guardrails as the dashboard buttons (both go through
`portal/ops.py`), and tool output never includes settings values: Skippy has no
passwords to give, and sends people to the game's page, where the join password is
on the Password line beside how to join. Skippy asks for
confirmation before stopping or restarting.

**Barney Style (Haiku on or off).** A checkbox under the chat box, OFF by default and remembered in the browser. Off, the page
sends the question straight to Mentat as typed and shows Mentat's own answer unedited; on, Haiku explains it simply as before.
Comparing the two shows what Haiku adds or loses. No model is called when it is off; same Mentat, same physics permission and
rate limit as a normal message (the page sends `raw: true` on `POST /chat/send`; `raw_mentat` in `portal/app.py`). Switching it
on fills the box with "Give it to me Barney Style"; sending that makes Haiku read the whole chat (the straight-to-Mentat answers are
in the history, marked as Mentat's own) and explain it in plain words using only what Mentat found, with no tools. Because
everything goes to Mentat while it is off, server questions ("is Valheim up?") need Barney Style on.

**Mentat gaps are auto-filed.** When Mentat answers with `value: null` (sigma-ground can't model the question, or recognised it
and couldn't run it), the chat replies "failed due to incompetence" (straight to Mentat) or has Haiku say it, and the portal logs
a bug: one line `{"event": "mentat_gap", ...}` in Cloud Run's logs and a `mentat_gap` event in the activity record skippy-pc
collects (`log_mentat_gap` in `portal/app.py`, `mentat_gap` in `portal/chat_tools.py`). The question and Mentat's reason are kept
only for the Captain or someone who ticked "Allow us to save your questions"; for anyone else it records that a gap happened and
how it was read. To find them: search the logs for `mentat_gap`, then fix what sigma-ground is missing. An outage (Mentat or Qwen
down) is not a gap. Tests: `portal/tests/test_chat_raw.py`.

**Everywhere, for everyone:** the chat is docked bottom-right on every page
(same conversation as the Chat page, carried across pages in the tab) and
knows which page you're on. Friends get it too, with two privacy walls:
the system prompt tells Skippy a friend is not the Captain and his personal
life is off limits (`chat._FRIEND_RULES`), and **`portal/tool_policy.py`**
decides which skippy-tel-core MCP servers each audience may use: `physics`
for everyone; mail, calendar, Drive, memory, contacts, SMS, location … for
admins only; anything unlisted is admin-only by default.

**Not yet connected to skippy-tel-core or your MCP tools.** Cloud Run has no
route into the home Tailscale mesh where skippy-tel-core runs. Next step
(tracked in the roadmap): give the portal a way in — Tailscale on the
portal side or a tunnel from the home side — then route chat through
skippy-tel-core's `/intent/unmatched` and expose its capabilities as tools.

## Private Skippy (the left side of `/me`)

The Captain's own chat: Claude Code (Opus, hard-coded in the bridge, all the time)
running with full permissions on skippy-pc. Ask for a change and it edits its own
clone of the repo, runs the tests, pushes to `main`, watches the deploy and health
checks it. Only an admin can reach it (Google login plus `ADMIN_EMAILS`; every call
also needs an `X-Workshop` header, so another site can't drive it). It is not linked
to skippy-tel-core's services (Mentat, Bilby, mail…): that is deliberate, for now.

```
browser ── /me/workshop/send ──> portal (portal/workshop.py) ── job "workshop" ──> home reporter
                                                                                      │ POST /workshop
browser <── poll /me/workshop/events ── portal <── POST /home/workshop/<task> ── reporter ──> bridge
                                                                                      │ (services/claude-bridge/workshop_engine.py)
                                                                                    claude -p (Opus) in its own clone
```

- **Every project, not just SkippyTel.** `services/claude-bridge/projects.json` (checked by `projects.py` at bridge start)
  lists the Captain's repos: SkippyTel plus every repo directly under `D:\Aaron\development` whose origin is
  `github.com/aaronsrhodes2/...` (zPhone left out: shelved; quarksum and toh-silly left out: older checkouts of sigma-ground
  and remnant-silly). Each has its own working clone `D:\Aaron\development\<name>-workshop`, made by `git clone` on first
  use and never the Captain's checkout (the registry refuses a clone that is, or is inside, any checkout), and the
  checkout itself is only passed with `--add-dir`. The card's picker shows the open conversation's project (fixed once it has
  a message); **New chat** asks which project the next one is for. The prompt (`workshop_prompt.md`) keeps SkippyTel's
  deploy rules for SkippyTel only; for any other project the rule is that repo's own CLAUDE.md, else run its tests, push to
  its branch (solo repos, Platinum 17) and say plainly what was not deployed. Still one run at a time across all projects.
  Sessions are kept per project (SkippyTel keeps the bare conversation id, so older chats carry on).
- **Conversations.** The **Conversations** button lists the newest 50 (title = the first message, project, how many
  messages, when, "working now"); tap one to read it and carry on in it (a turn still running is followed live). The bridge
  keeps a turns index beside each conversation's Markdown in `D:\Aaron\conversations\private-skippy`
  (`<date>_<conv>.turns.json`: his messages, Skippy's text and tool lines, how each ended) and serves it on `GET
  /workshop/conversations` and `GET /workshop/conversation/<conv>` (home token); the portal asks for them as home jobs
  (`workshop_conversations`, `workshop_conversation`) and shows only the asker's own.
- **Proof it's live (the ship card).** When a run ends, the bridge compares its clone's HEAD with where it started and
  whether `origin/<branch>` now contains it; if the run pushed, its last event is `{"t":"ship", project, repo, branch,
  sha, subject, workflow}` (strictly checked by the portal's `ship_event`). The answer shows a card that polls
  `GET /me/workshop/ship?sha=` every 10 s until final (`portal/ship.py`; only for a commit one of your own runs pushed):
  for SkippyTel, that commit's deploy run (one new read-only call on the recovery client's allow-list: deploy runs by
  `head_sha`) as tests → build → deploy, green only when `/version`'s commit is that sha (or a later deploy that contains it
  is live), red with the run's link and **Roll back** (Admin page) when it failed. Other projects: the commit link and
  "pushed (no deploy pipeline here)", or a link to their deploy workflow (the portal's token reads skippy-tel only).
- **Tell me when it's done (Discord).** When a run the portal saw going ends (done, failed, stopped, or done on a question
  for you), `portal/notify.py` sends the Captain one Discord DM through the portal's bot: project, the first words of the
  request, the outcome, and the link to `/me` (at most 200 characters; never code, the agent's text or the error; pings
  nobody). Only the Captain: `DISCORD_CAPTAIN_ID` in the VM settings, else the owner of `DISCORD_GUILD_ID`'s server. Once
  per run: only when a record this process held as queued/running becomes final, never for a run that ended over 10 min
  ago (the reporter sends `finished_age_s` with a finished run, and a record a fresh portal rebuilds from an already
  finished run never notifies). The card's **Discord me when done** switch (default on) is kept in VM metadata
  `portal-workshop-notify`. Discord trouble is swallowed and logged once per kind.
- **Show me the result (screenshots).** After a pushed change to `portal/templates` or `portal/static`, the prompt has
  the agent run `python tools/workshop_screenshot.py me --out .skippy-shots/me.png` (the checkout's portal with
  `portal/tests`' fakes, a scratch admin session signed with a random key, served on 127.0.0.1 only, shot at phone size by
  a headless Chrome or Edge already on the PC; it installs nothing and exits 3 without one) and print
  `SKIPPY_IMAGE: .skippy-shots/me.png` on its own line (`.skippy-shots/` is git-ignored). The bridge strips that line and,
  as the run ends, attaches the file as `{"t":"image", mime, name, b64}` only if it is inside the project's clone, at most
  400 KB and really a PNG or JPEG by its first bytes (3 per run). The reporter keeps each push under 700 KB of JSON. The
  portal checks it again (`image_event`), keeps it in memory for its owner (3 per task, 40 in all), puts
  `{"t":"image","n"}` in the chat and serves the bytes from `GET /me/workshop/image?task=&n=` as that type with
  `nosniff` and a CSP that allows nothing; the card shows it under the answer. A portal deploy forgets them (the card says
  so), and a reopened conversation shows a line where one was.
- **A portal deploy doesn't lose the conversation.** History and progress live on
  skippy-pc (the bridge keeps every event and one Claude session per conversation);
  the reporter tells the portal what it has, and a redeployed portal replies "I hold N
  events", so the reporter re-sends the rest. The page keeps its transcript in
  `localStorage` and re-attaches to a running task after a reload or a deploy.
- **How it recovers (the reconciler).** The run itself lives on the bridge; the portal's copy is in memory and goes with
  every deploy, and a deploy also wipes the portal's job queue. So the reporter doesn't wait to be asked: every 10 s (2 s
  while the chat is open) `WorkshopSync` in `services/home-reporter/reporter.py` reads the bridge's runs (`GET
  /workshop/runs`) and the portal's records (`GET /home/workshop`) and repairs any difference: a run the portal lacks or
  holds only part of is pushed (from the portal's own `have`), a run still going gets a push every pass even with nothing new
  (the heartbeat that keeps it from showing "stalled"), a run nothing is streaming is handed to the quick relay (`attach`,
  which can only join a run the bridge already has, never start one), and a task the portal thinks is going but the bridge no
  longer has ends with "skippy-pc lost this task ... Send your message again" instead of hanging. It only acts when both
  sides answered, never invents an error, and is idempotent: the push says where its events start, the portal answers with how
  many it holds, and a finished task can't be reopened by a late "running", so racing the streaming relay costs nothing.
  Result: after any order of portal deploy, reporter rebuild and bridge restart, the chat catches up within about 30 s of all
  three being up, whether or not the queue still had the job. A job the queue offers again only ever re-attaches if the portal
  already saw the task running (so a bridge that forgot it can't redo the work), and the reporter exits by itself (Docker
  restarts it) after 15 minutes without one successful report to the portal. If the chat still shows it went quiet, **Check
  again** asks the reconciler to look now; Send comes back after 30 s of quiet; `GET /home/workshop` is the portal's side of it
  (home token) and `ops home` shows what the portal sees.
- **What crosses the wire:** your messages, the agent's text, and one activity line per
  tool call (name and command or path, credentials redacted), never tool output. The
  portal logs who and how long, never the message.
- **Setup on skippy-pc:** run `make bridge` (it needs `SKIPPY_HOME_TOKEN`, read from the
  vault `.env`), make sure `claude` there is signed in (`claude`, then `/login`), and
  rebuild the reporter (`CLAUDE_BRIDGE_URL` is in `docker-compose.override.yml`). The card
  says plainly when the workshop isn't answering, and why when the bridge knows: the reporter passes the bridge's
  `workshop.login` (`ok` / `expired` / `unknown`) and, when it is not ready, its `reason` ("SKIPPY_HOME_TOKEN isn't
  configured on this PC", "Claude's login on skippy-pc expired: run `claude auth login` there", or, when the bridge doesn't
  answer at all, that) in the report's `capabilities.workshop`; the card and Send's refusal show it.
- **The report is the heartbeat, and a slow service can't delay it.** The portal calls skippy-pc silent after 60 s and then
  offers nothing, the chat included. The reporter used to ask its services one after another (about 45 s worst case when
  flask-api or Ollama is slow) and then pull telemetry before the next report, so a slow flask-api could make a healthy PC
  look gone. Now `gather()` asks them side by side under one 9 s budget (`GATHER_BUDGET`): a probe that is late or fails is
  missing from that report (`None`, `ready: false`, never a stale answer), the telemetry pull has its own thread, and the
  loop sleeps the interval minus the work done, so reports keep coming about every 15 s. The report's shape is unchanged.
- **Limits:** one task at a time; **Stop** cancels it. A task the portal hears nothing about for 2 minutes is never given
  up on: the card says how long it has been quiet and that skippy-pc reconnects by itself (a long think or build with no
  new event is not quiet: a run still going is pushed every pass), asks the reconciler to look (and on **Check again**),
  gives Send back after 30 s (sending then leaves the old task behind; if the bridge is still busy it answers "still
  working on the last one") and keeps polling, slower, for as long as the page is open. The agent's rules are in
  `services/claude-bridge/workshop_prompt.md` (it must ask before touching workflows,
  auth, secrets or its own harness). Those rules are a prompt, not a sandbox: the
  real guards are the login, the token and the tests.

## Bilby's Audio Service (the Captain's music, `/music`)

The Captain's whole music collection (21k MP3/AAC files on skippy-pc) in any browser: iPhone, Mac, PC, the Tesla. Admin only, a tile
on the launcher (it says "library offline" when skippy-pc's music gate isn't answering). The browser is the universal client (the
Android apps are shelved), so this is the one player. It replaces the old `/bilby` page (a mirror of the Mac's DJ decks): `/bilby` and
everything under it now answers a 301 to `/music`, so old links keep working. The `bilby` portal grant still exists and now only
gates the chat's `now_playing` tool; `/music` itself is the Captain's alone.

```
browser ── /music (page, /music/gate pass, /music/checkin + /music/command: Connect) ──> portal  (portal/player.py)
   │
   └── https://skippy-pc.<tailnet>.ts.net/t/<pass>/{catalog.json,lyrics.json,a/<path>,art/<path>?w=} ──> music-gate on skippy-pc
```

The music never passes through the portal: the portal signs a pass (the key comes from the home token's sha256, the same one the gate
derives; no new secret), the browser carries it in the URL path to the gate, which Tailscale Funnel puts on the internet. A pass is
good for 8 to 14 hours and is the same string for everyone asking inside one six-hour window, so the browser's own cache (covers for a
week, songs for a day) still works across page loads. The gate's address is only ever signed for when it is on the tailnet
(`*.ts.net`); `Player(gate_url_re=...)` exists for the dev script alone.

**Files:** `portal/templates/music.html` (the shell), `portal/static/bilby-player.js` (the logic that needs no browser: the catalog,
search, the queue, Bilby DJ's pick, the autoplay state, the virtual list's arithmetic; run under Node by the tests),
`bilby-app.js` (the browser side: audio, views, Connect, media keys), `bilby-player.css` (portal colour variables only),
`portal/player.py` (the pass and Connect). **Bilby DJ** is on by default: when the queue runs out it picks the next track by the
real Bilby's rule (70 % the same artist, 30 % anything; never the current track, nothing from the last 50), 30 s before the end, so
it is queued and preloaded in time. **The lyric line** under the title is the one sentence about what the song is about
(`lyrics.json`, from the Mac's lyric stores through flask-api and the gate); it hides when a track has none.

**Why it sometimes asks for a tap.** A browser only lets a page make sound after a tap on that page, and that starts over at every
reload (Safari and iOS are the strictest: the permission belongs to the audio element a tap started, so there the player re-sources
ONE element). So the player never calls `play()` without a tap. When a saved queue comes back after a reload, or another device asks
it to play, or the browser refuses, it shows a **Tap to start the sound** bar with a one-line explanation and carries on exactly where
it was. After one tap everything works without more: the next song, previous, skip, and a remote's commands. Each device says in its
Connect check-in whether it is `armed` (a `play()` has worked since the page loaded) or waiting for a tap, so another device's remote
says "needs a tap first" instead of failing silently. Expect one tap after every deploy or reload; it is not an error.

**Connect.** Every open player checks in every few seconds (what it plays, where, a window of its queue). Another device lists them
under the devices button (the speaker icon, "Other devices"): **Play here** picks up the queue and place (and pauses the other), or use it as a remote (Pause, Play,
Next). In memory on the portal (one instance); a redeploy forgets it and the players check in again. A device's listening history
never goes to the portal: it stays in that browser, and telemetry only ever sees the tile tap.

**Looking at it without skippy-pc:** `python portal/tests/dev_music.py` (needs ffmpeg and Pillow) runs the real portal app beside the
REAL music-gate (`services/music-gate`'s own Flask app, over a generated tagged library in the temp folder; only the source of the
lyric lines is a few lines of stand-in), then open `http://127.0.0.1:8765/dev-login`. `--fake 21000` adds 21,000 stub tracks to see
the virtual lists at full size; `/dev/gate-down` and `/dev/home-off` show the "isn't reachable" states. Tests: `portal/tests/test_player.py`
(routes, passes, Connect, the page's markup against the script's lookups, the colour rule), `test_player_js.py` (runs
`portal/tests/js/*.test.js` under Node 18+ and feeds a catalog built by the real gate to the player's parser; both are skipped, not
failed, when Node or mutagen is missing). What only a browser can show (the audio element, drag and drop, the lock screen, the
layouts) was checked by hand, not by a test.

## Nagatha's Video Service (the Captain's films, `/video`)

The Captain's Jellyfin library on skippy-pc (285 films, 11 collections) in any browser: a phone, a laptop, a TV browser. Admin only,
a tile on the launcher next to Bilby's (it says "library offline" when the home reporter's `video_gate` capability isn't ready). The
sibling of Bilby's Audio Service and built the same way; the name is branding only (no Gemini).

```
browser ── /video (page) + /video/gate (a pass) ──> portal  (portal/video.py)
   │
   └── https://skippy-pc.<tailnet>.ts.net:8443/t/<pass>/{catalog.json, img/<id>/<kind>?w=, v/<id>/direct, v/<id>/hls/..., sub/<id>/<i>.vtt, progress}
                                                                       ──> video-gate on skippy-pc (services/video-gate) ──> Jellyfin
```

The film never passes through the portal. The portal signs a pass from the home token's sha256 with the video gate's own label
(`video-gate-v1`, signed text `video:<exp>`), so a music pass never opens the video gate and a video pass never opens the music gate.
The pass lasts 8 to 14 hours on a six-hour grid (the poster URLs stay the same across page loads, so the browser cache works) and is
only signed for an `https://*.ts.net` address with no port or Funnel's `:8443` (`Video(gate_url_re=...)` is for the dev script alone).
The reporter calls the gate ready only when its `/health` says `ready` (a catalog exists AND Jellyfin answers), not just a 200.

**How a film plays.** Direct play (the original MP4, no work for skippy-pc) when the gate's catalog says `direct` and the first audio
track is wanted; otherwise the gate's transcoded HLS: hls.js (self-hosted: `portal/static/vendor/hls-1.6.15.min.js`, byte for byte
the cdnjs release, its SRI and the Apache-2.0 licence beside it; loaded only when needed) or Safari's/iOS's own HLS. Choosing another
audio language switches a direct film to HLS at the same moment (a browser can't switch a plain MP4's audio). If a browser refuses a
"direct" file, the player falls back to HLS by itself; a 403 (a pass that ran out) fetches a new pass and carries on from the same
second; a third simultaneous transcode (the gate's cap is 2) says so in a sentence. Subtitles are the gate's WebVTT on `<track>`
elements (hls.js's own caption handling is off: left on, it empties every text track when it loads a manifest).

**Resume and Continue Watching.** The player reports `start`, then `progress` every 10 s while playing and at once on a pause or seek,
and `stop` on close or at the end (a `sendBeacon` when the tab closes); the gate writes it to Jellyfin, so Jellyfin's own apps agree.
The browser also keeps its own last position (newest 200 films), used when it is newer than the catalog it loaded. Reloading on a film
(`#watch/<id>` in the address) brings it back at that second; where the browser wants a tap first it shows **Tap to continue at …**,
the same autoplay rule as the music player.

**Files:** `portal/templates/video.html` (the shell), `portal/static/nagatha-core.js` (logic that needs no browser: the catalog,
search, shelves, resume, which way a film plays, progress timing, keys; run under Node by the tests), `nagatha-app.js` (the wall,
the details sheet, the player: custom accessible controls, full screen, picture in picture, Media Session, keyboard: Space/K, arrows,
F, M, C, P, 0, Esc, `/`, `?`), `nagatha.css` (portal colour variables only; the accent is amber), `portal/video.py` (the pass).

**Looking at it on skippy-pc:** `python portal/tests/dev_video.py` runs the real portal app against the REAL running video gate
(`http://127.0.0.1:3006`) with a scratch Captain sign-in: open `http://127.0.0.1:8775/dev-login`. It reads `SKIPPY_HOME_TOKEN` into
memory only (the gate takes only passes from that token) and never prints it. Progress it sends is real Jellyfin watch state.
Tests: `portal/tests/test_video.py` (routes admin-only, the pass checked by the gate's OWN code in a separate process, a music pass
refused, the URL rule, the tile and pill, the manifest, the page's markup against the script's lookups, the colour rule, hls.js's
bytes), `test_video_js.py` (runs `portal/tests/js/video/*.test.js` under Node 18+ and feeds a catalog the real gate builds from its
fake Jellyfin to the player's parser; skipped, not failed, without Node).

## Star Control Zero (the Captain's game, `/starcontrol`)

The Captain's Star Control fan-game prequel ([star-control-precursors](https://github.com/aaronsrhodes2/star-control-precursors)),
played in the browser. Everyone who can sign in can play it: core users and every friend, whatever games they were
granted (it is not a game server, so there is nothing to grant, wake or pay for). Its card in Browser Games (`/play`) links to it once the game is in the image, and says "coming soon" until then.

**How it gets here.** The game is Python (pygame); its repo's `tools/build_web.py` compiles it to WebAssembly with pygbag and
writes a folder of plain files, published as a GitHub Release (`scz-web.tar.gz`). `portal/Dockerfile` runs
`portal/starcontrol_fetch.py`, which downloads the release pinned in `deploy.yml` (`STARCONTROL_REF`, with its SHA-256 in
`STARCONTROL_SHA256`), refuses it if the hash differs, and unpacks it to `starcontrol_static/` in the image
(`STARCONTROL_DIR` overrides the folder). That step sits before the portal's own files in the Dockerfile, so the
half-gigabyte layer is rebuilt only when the pin changes. An empty `STARCONTROL_REF` builds a portal without the game:
no tile, and `/starcontrol/` says it isn't bundled.

| File | What |
|---|---|
| `index.html` | the door: on a computer it loads the game at once; on a phone or tablet it waits for a controller (or a real key press), so a device that can't play never downloads it |
| `game.html` | the game's own page (pygbag's, patched to fetch the bundle in parts) |
| `web.tar.gz.partNN` | the game: code, art and sound effects, about 75 MB, split into parts of 24 MB because Cloud Run caps one response at 32 MiB |
| `assets/music/<track>/<stem>.mp3` | the music, about 460 MB, streamed a track at a time while you play; never downloaded up front |
| `version.json` | the build id, the game commit, sizes |

**Routes** (`starcontrol` blueprint in `app.py`, helpers in `portal/starcontrol.py`):
`GET /starcontrol/` (signed in) is `index.html` with the portal's app bar injected above the game (‹ Home, the title, the round
Home button; the page is not a template, like Radiance); `GET /starcontrol/<path>` (signed in) is any file in the folder, with
its type sent explicitly and Range answered (the music needs it); the bundle parts are cached for a year when their `?v=` is
this build's id, everything else for an hour. `GET /starcontrol/version` is public (a build id and sizes): the deploy's smoke
test reads it to prove the game shipped. The WebAssembly runtime itself (Python, pygame) comes from pygbag's CDN,
`pygame-web.github.io`, pinned to one version by the build.

**Playing.** Keyboard: WASD or the arrows, Space / Enter to select, Esc to go back. Controller: any pad the browser reports
with the standard layout. No touch controls. Saves live in that browser's own storage (`localStorage`, keys `scz-save/...`):
they survive a reload and a new release, but they do not follow the player to another device.

**Shipping a new build of the game.** In the game repo: `python tools/build_web.py --tarball`, then
`gh release create <tag> build/scz-web.tar.gz`, then put the tag and `sha256sum build/scz-web.tar.gz` in `deploy.yml` and push.

Tests: `portal/tests/test_starcontrol.py` (who gets in, the page and its app bar, each file's type and caching, Range, no
reading outside the folder, the public version probe, the tile, a build without the game, the fetch script's checksum and
archive checks, the Dockerfile and `deploy.yml` pins). The game itself is tested in its own repo; its scripted walks also run
inside the browser build (`?test=<walk>` on the game's page).

## The bridge (the one interface the portal is moving to)

A page built on the bridge is **one phone screen**: the page is exactly the viewport (`100dvh`, `overflow: hidden`, no scroll, no
scrollbar, no pull-to-refresh), a grid shares the height among its controls, and anything longer than a screen is paged or swiped, not
scrolled. It looks like the manual controls of a Maxolhx starship (the cat-eyed, sabre-toothed patron race of the Expeditionary Force
books): cut corners, three claw slashes and two fangs on every control, a lamp that breathes when the thing behind it is ready, a light
that runs along the edge of a live control, a plate that floods with colour while a finger is on it. Black base, crisp edges, no gradients
(black is see-through on the Viture glasses). Code: `portal/static/bridge.css` and `bridge.js`, the glyphs in `portal/templates/_glyphs.html`,
the shell in `portal/templates/base.html`.

**Language-agnostic.** Say it with a glyph. Words appear only where they are a name or syntax people look up (a game server's name, a join
code, a password, a commit id). The words for a screen reader are on each control (`aria-label`, `title`), never on the page. Add a glyph to
`_glyphs.html`; never an emoji, never an image. A lamp says its state by shape as well as colour: lit circle = ready or live, ring = asleep,
diamond = down, triangle = needs attention. Skippy chat is the one place with a **language choice** (13 languages, each in its own script;
`chat.LANGUAGES`): the choice rides each message and the system prompt tells Skippy to answer in it while leaving names, codes, passwords,
commands and numbers untouched.

**Building a bridge page.** Extend `base.html`, add `{% block ui %}bridge{% endblock %}` and `{% block glyph %}NAME{% endblock %}` (the glyph
in the strip along the top; `{% block hud_back %}` if "back" isn't Home). For a grid of controls use `.deck` with
`style="--rows: N; --rows-wide: N; --rows-side: N"` (rows per layout) and a `.unit` per control (see `home.html`, `play.html`); content that is
more than a screen goes in pages the person flips, never in a scroller (a drag is blocked everywhere except `textarea`, `input`, `select` and
anything marked `data-pan`). Tests: `portal/tests/test_bridge.py`.

**Modules and the Graveyard.** Every launcher control is a module (`portal/modules.py`: id, glyph, page, a name for a screen reader). Who may use
a module is still the grants (`auth.py`); the Captain decides whether it is on the launcher at all. **Admin → Graveyard** (the first thing on the
page) is a key per module: lit = on the launcher, dark = retired; press to retire or revive. Retiring hides the control for everyone; the page
still answers at its own address and nothing is deleted. Admin itself can't be retired. The list is stored in the games VM's metadata
(`portal-modules`, ignored by Terraform), read fail-open (an unreadable list retires nothing) and written fail-closed (a read that fails or a list
that can't be read is never overwritten). Retirement is manual for now; it does not yet use the activity record to suggest what is unused.

**Pilot.** On the bridge: the launcher, Browser Games (`/play`), the Graveyard (inside Admin, which is otherwise still the old page) and the
language choice in chat. Still the old look, to move in batches: chat's own screen, admin, game servers, music, video, experiments and the demos,
the dashboard, request access, privacy.

## Telemetry: what the portal records, and where it goes

The Captain records how signed-in people use the portal (what gets opened, tapped and asked, what breaks), so he can
see what to keep and what to remove, and it says so: the public **`/privacy`** page, linked from every page's footer,
the sign-in page and the Skippy chat. That page is **rendered from `RECORDED`, `NOT_RECORDED` and `ALSO` in
`portal/telemetry.py`**, the same lists the recorder is written against, so it can't promise what the code doesn't do
(a test swaps the lists and checks the page follows). Change what is recorded → change those lists in the same commit.

```
browser ─tap─> POST /telemetry/tap ─┐                              (signed-in people only; taps on [data-tel])
portal hooks (telemetry_web.py) ────┴─> Telemetry (in memory, ≤5000) <─ GET /home/telemetry ── home reporter (each ~15 s)
                                                                                   │ POST /telemetry/site_events (≤100 KB batches)
                                                       flask-api: site_events table in ui_telemetry.db (skippy-pc)
                                                                                   │ GET /telemetry/site_summary → report `site_activity`
                                   /me → "Activity" widget (admin only) <──────────┘
```

- **Recorded**, for a signed-in person still on the list, each event tagged with their email, role and a random id
  for that sign-in: `page` (a GET that returned HTML: its path, never the query string), `action` (every non-GET: the
  endpoint, whether it worked, the field NAMES, and values only for the `SAFE_VALUES` allow-list in
  `telemetry_web.py`: game ids, idle minutes and the like, each checked against a pattern), `ui` (a tap on anything
  marked `data-tel`: the launcher's tiles, the Games cards, the experiments), `chat` (the message and reply, the model,
  the tools used with their arguments, redacted), `error`.
- **Never**: IP addresses or browser details (nothing reads them), signed-out people and visitors, a tool's result, setting
  values, passwords. Polls, media and the machine endpoints (`SKIP_ENDPOINTS`) and the private Skippy (`/me/workshop/*`)
  aren't recorded at all. A test pins every skipped name to a real endpoint, so a renamed route can't start recording, and
  a second test scans every template for what its scripts request by themselves (`url_for` inside a `<script>`, a `data-*`
  URL a script reads, literal paths) and fails unless each such GET endpoint is in `SKIP_ENDPOINTS` or doesn't answer a
  page: a new poller that returns an HTML fragment (the Server admin card's 60 s refresh was the first) must be added there.
- **Secrets in text** are scrubbed by `telemetry.redact()` (API keys, bearer tokens, JWTs, `password: …`, `password is …`,
  `VALHEIM_PASSWORD=…`, long token-looking strings), anything under a key that sounds secret is dropped, and a code a tool
  returned that Skippy then repeats is removed from the reply. Best effort: the chat says "don't type secrets".
  The scrubbing must stay cheap, because the portal is one process and a regex holds it for everyone: text is cut to its
  cap (+256 characters, so a secret at the cut is still seen) BEFORE it is scanned, `redact()` never scans more than
  `MAX_SCAN`, and no pattern may backtrack (no nested quantifiers: `test_fix_telemetry_redos.py` runs every compiled
  pattern in the module against adversarial input at the real sizes, in a child process with a hard time limit).
  A lone surrogate (`"\ud800"`: valid JSON, unencodable) is replaced by U+FFFD wherever it appears in what is recorded.
- **No ack, no loss.** The portal is stateless, so it keeps the newest 5000 events in memory (`dropped` counts overflow).
  Each pull says which portal process (`boot`) and the last `seq` it has stored; the portal forgets only up to that, and
  the reporter moves on only after flask-api answered ok. flask-api stores each `(boot, seq)` once, so a retry or a
  restarted reporter can't double count. A redeploy of the portal loses what was still waiting (seconds' worth).
  flask-api being away (no answer, 404, 502/503/504) or failing every write (500 to everything) only delays: nothing is
  skipped. A 500 makes the reporter halve the batch (a few POSTs, bounded) to find an event flask-api will never store;
  it skips such an event only after flask-api has stored other events meanwhile and the event still gets a 500 alone, and
  says so in its log. flask-api itself repairs a lone surrogate and refuses a row it can't bind without failing the batch.
  If the portal's oldest waiting event is over 15 minutes old while home keeps pulling, the widget says **stuck**, not ok.
- **Kept 180 days** (`KEEP_DAYS`; flask-api prunes at start and hourly), in the same SQLite file as the phone UI's
  clicks (`ui_events`), in the `site_events` table. Read it with
  `curl -s localhost:3003/telemetry/site_summary?days=30` (the same summary the widget shows) or SQLite directly.
- **Deleting a person's events** (the page tells people to ask): on skippy-pc,
  `curl -s -X POST localhost:3003/telemetry/site_forget -H 'content-type: application/json' -d '{"user":"pal@example.com"}'`
  answers how many rows it deleted. Events still waiting in the portal's memory arrive after that (they're seconds old):
  run it again a minute later, or remove the person from Admin → Friends first, since only people on the list are recorded.
- **Switch**: `TELEMETRY_ENABLED=false` (a deploy env var; default on) records nothing, hides the beacon and the notices,
  and `/privacy` says it is off.
- **Adding a tile**: put `data-tel="tile:<name>"` on it (an app tile) or `demo:<slug>`; a test checks every id the launcher,
  Games and Experiments templates use is one the Activity widget knows (`known_taps()`), which is how the widget lists
  tiles nobody has tapped.
- **Order of a rollout**: skippy-pc needs flask-api (the new routes and table) and home-reporter (the collector) rebuilt
  (the safe stop / rm / up sequence for `flask-api` and `home-reporter`); the portal deploys from `main` as usual. Until the
  reporter is running the new code the widget says "never collected", and the portal's buffer fills to 5000 and then
  drops the oldest. A flask-api without the routes only makes the reporter retry (it never loses a pulled event).

## Known rough edges

- Choosing different games while the VM is **running** saves the choice;
  it applies at the next restart.
- Restart is synchronous (stop → start can take a few minutes); the page
  waits. Fine for one admin; a background job + progress view is the fix if
  it gets annoying.

## Doom Loop dev channel (`/doomloop`)

The Captain's work-in-progress game (github.com/aaronsrhodes2/doom-loop), playable in the browser at its newest build, so
any push can be played on its intended target. Unlike Star Control Zero it is **not baked into the portal image**:
nothing about a new build redeploys the portal.

**How a build gets here:** doom-loop's CI (`.github/workflows/dev-deploy.yml` in that repo) runs the game's tests, builds
the static site (`dist/`: `index.html`, `assets/`, and `version.json` written last) and uploads it to the folder
`dev/` of the bucket `skippy-tel-doomloop-dev`. It authenticates as `doomloop-publisher` through Workload Identity
Federation (no key file): a second provider (`doomloop`) in the same pool trusts only doom-loop's `main`, and
`doomloop-publisher` can only write objects in that one bucket. It is **not** `skippy-deployer`, whose `run.admin` /
`compute.admin` roles another repo's CI must never hold. All of it is in `infra/bootstrap/` (applied by hand in Cloud
Shell, like the rest of bootstrap: see [docs/cicd.md](../cicd.md)).

**Routes** (`doomloop` blueprint in `app.py`, helpers in `portal/doomloop.py`): `GET /doomloop/` (signed in) is the build's
`index.html` with the portal's app bar and a "dev build <commit>" badge injected above the game; `GET /doomloop/<path>`
(signed in) is any file of the build, read from the bucket with the portal's own service account (`skippy-agent` has
`objectViewer` on the bucket), sent with the right content type and revalidated by ETag; a path that leaves the folder is a
404 before storage is asked. `GET /doomloop/version` is public (build id, commit, time, branch): the deploy's smoke test
reads it and accepts "no build yet". The build id is cached for 10 seconds, so a new build shows within seconds of its
upload. With no bucket configured (tests, local runs) or before the first build, the page says there is no build yet and the
card in `/play` says "coming soon".

**The game's side of the contract:** the site must ask for its files by relative path (Vite `base: './'`) so they resolve
under `/doomloop/`; no single file may exceed Cloud Run's 32 MiB response cap; saves stay in the player's browser.

**Setup once** (after the code is deployed): in Cloud Shell, `git pull` and apply `infra/bootstrap` (it creates the
bucket, the publisher and the second provider), then give doom-loop two repository variables (Settings → Secrets and
variables → Actions → Variables): `WIF_PROVIDER` = the `doomloop_workload_identity_provider` output and
`WIF_SERVICE_ACCOUNT` = the `doomloop_publisher_service_account` output. Neither is a secret.

Tests: `portal/tests/test_doomloop.py` (who gets in, the page and its badge, file types and revalidation, no path escape,
new builds without a redeploy, damaged or unsafe `version.json`, no-bucket behavior, the `/play` card).
