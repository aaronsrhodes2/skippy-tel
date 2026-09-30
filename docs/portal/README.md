# Skippy Portal

One page, gated to you and whoever you allow: game servers with their
Discord channels, the other services, a text chat with Skippy, and an admin
page. Code in [`portal/`](../../portal); deployed on every push by
[CI/CD](../cicd.md) to Cloud Run as `skippy-portal`.

| Page | Who | What |
|---|---|---|
| `/dashboard` | allowlist | Server status, start (game picker + auto-sleep) / stop, per-game connect info, `#game` and 🔊 voice Discord links |
| `/chat` | allowlist | Text chat with Skippy (Claude, Skippy persona) |
| `/admin` | admins | Game settings (world names, passwords, Dragonwilds owner ID), **Restart & update**, **Set up / repair Discord channels** |

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
2. **They play:** Servers page → **Play RuneScape: Dragonwilds**. The VM
   wakes, the server updates itself and loads the saved world. The tile shows
   "Starting — booted N min ago" until it's typically joinable (~6 min), then
   the join steps and a **Show join password** reveal.
3. **It cleans up after itself:** after the auto-sleep window with nobody
   playing, worlds are saved and the VM powers off. The next friend to click
   Play wakes it again, with the same world.

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
- **Discord:** the admin button ([`portal/discord_admin.py`](../../portal/discord_admin.py))
  creates `#game` + 🔊 voice channels, a webhook and a pinned status post per
  game, and stores the IDs/webhooks in `games-env` — which is where the
  dashboard's links and the VM's status posts both come from.
- **Stops are graceful however they happen:** the VM's shutdown-script
  saves worlds and posts to Discord for the portal's Stop button, the idle
  watchdog, or a manual stop alike.

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
`portal/ops.py`), and tool output never includes settings values — join
passwords stay behind the dashboard's "Show join password". Skippy asks for
confirmation before stopping or restarting.

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

- **A portal deploy doesn't lose the conversation.** History and progress live on
  skippy-pc (the bridge keeps every event and one Claude session per conversation);
  the reporter tells the portal what it has, and a redeployed portal replies "I hold N
  events", so the reporter re-sends the rest. The page keeps its transcript in
  `localStorage` and re-attaches to a running task after a reload or a deploy.
- **What crosses the wire:** your messages, the agent's text, and one activity line per
  tool call (name and command or path, credentials redacted), never tool output. The
  portal logs who and how long, never the message.
- **Setup on skippy-pc:** run `make bridge` (it needs `SKIPPY_HOME_TOKEN`, read from the
  vault `.env`), make sure `claude` there is signed in (`claude`, then `/login`), and
  rebuild the reporter (`CLAUDE_BRIDGE_URL` is in `docker-compose.override.yml`). The card
  says plainly when the workshop isn't answering.
- **Limits:** one task at a time; **Stop** cancels it; a task the portal hears nothing
  about for 2 minutes shows "went quiet". The agent's rules are in
  `services/claude-bridge/workshop_prompt.md` (it must ask before touching workflows,
  auth, secrets or its own harness). Those rules are a prompt, not a sandbox: the
  real guards are the login, the token and the tests.

## Known rough edges

- Choosing different games while the VM is **running** saves the choice;
  it applies at the next restart.
- Restart is synchronous (stop → start can take a few minutes); the page
  waits. Fine for one admin; a background job + progress view is the fix if
  it gets annoying.
