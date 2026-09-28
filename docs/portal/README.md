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

## How it fits together

- **Auth:** Google sign-in; `ALLOWED_EMAILS` / `ADMIN_EMAILS` re-checked on
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

Direct Claude API (`claude-opus-5`, server-side refusal fallback on), with
Skippy's persona from `services/flask-api/app/skippy_persona.md`. History
lives in the browser tab and is sent with each message; the server keeps
nothing.

**Not yet connected to skippy-tel-core or your MCP tools.** Cloud Run has no
route into the home Tailscale mesh where skippy-tel-core runs. Next step
(tracked in the roadmap): give the portal a way in — Tailscale on the
portal side or a tunnel from the home side — then route chat through
skippy-tel-core's `/intent/unmatched` and expose its capabilities as tools.

## Known rough edges

- Choosing different games while the VM is **running** saves the choice;
  it applies at the next restart.
- Restart is synchronous (stop → start can take a few minutes); the page
  waits. Fine for one admin; a background job + progress view is the fix if
  it gets annoying.
