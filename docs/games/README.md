# Pop-up game servers

Valheim, Factorio, Satisfactory and **RuneScape: Dragonwilds** (ARK deferred:
too heavy), each with its own Discord text channel, voice channel and
pinned status post. Servers start when someone wants to play and put
themselves to sleep when nobody is, so you pay for play-hours, not months.

| Piece | Where |
|---|---|
| The servers (one Docker Compose profile per game) | [`games/docker-compose.yml`](../../games/docker-compose.yml) |
| GCP VM, data disk, daily snapshots, firewall, static IP | [`infra/gcp-games/`](../../infra/gcp-games) |
| Start / stop / status from the PC or Mac | [`scripts/games.sh`](../../scripts/games.sh) |
| Idle watchdog (auto-sleep) | [`infra/gcp-games/idle-watchdog.sh`](../../infra/gcp-games/idle-watchdog.sh) |
| Discord channels + pinned status posts | [`scripts/discord_setup.py`](../../scripts/discord_setup.py), [`infra/gcp-games/discord-notify.py`](../../infra/gcp-games/discord-notify.py) |

## Hosting options

Rough costs, **estimates from list prices; check the
[GCP calculator](https://cloud.google.com/products/calculator) before relying on them.**

| Option | Monthly cost (≈40 play-hours) | Pros | Cons |
|---|---|---|---|
| **GCP pop-up VM** *(recommended; this repo)* | ≈ $20–25: compute ~$3–7 (e2-standard-4 ≈ $0.13/hr, e2-highmem-4 ≈ $0.18/hr), disks ~$9 (30 GB boot + 60 GB data), static IP ~$7 while idle, snapshots + egress ~$2–4 | Covers all four games for one price. Home IP stays private. Doesn't compete with your gaming/streaming PC. Daily snapshots. | A few minutes to boot. The fixed ~$16–18/mo (disks + IP) is paid even in months you don't play. |
| Home PC (same compose file) | ≈ electricity | Free. Great for testing tonight. | Router port forwarding. Exposes your home IP. Competes with your games and Steam streaming for RAM/CPU. Docker Desktop needs a login. |
| Always-on rented box (e.g. Hetzner) | ≈ €30–60 flat for 16–32 GB (check current pricing) | Always up, no boot wait. | Paying 24/7 for occasional play. |
| Shockbyte (Jagex's Dragonwilds partner) | per game per month | Zero setup. | One game per rental; you don't own it. |
| Nitrado (today) | ≈ $15–30 **per game** | Zero setup. | What you're trying to leave. |

Break-even: once you'd otherwise rent **2+ games** on Nitrado, the pop-up VM
wins. It wins sooner if you drop the static IP (`games.sh` + the Discord
posts already show the current IP, but saved server entries in Valheim and
Satisfactory would need updating).

**Tip:** a new GCP billing account gets a free trial credit. And your Gmail
shows the "Default Gemini Project" billing account had payment warnings in
August; fix the payment method before deploying, or the VM can get suspended.

## Setup (once, ~30 min)

1. **GCP project + safety net**
   ```bash
   gcloud auth login && gcloud auth application-default login
   gcloud projects create skippy-games-<something-unique>
   gcloud config set project skippy-games-<...>
   gcloud billing projects link skippy-games-<...> --billing-account <ID>   # gcloud billing accounts list
   ```
   In the console, go to **Billing → Budgets & alerts** and create a budget of $30/mo with email alerts at 50/90/100%.

2. **Create the infrastructure** (VM is created *stopped*)
   ```bash
   cd infra/gcp-games
   cp terraform.tfvars.example terraform.tfvars   # set project
   tofu init && tofu apply                        # or terraform
   ```

3. **Secrets**
   ```bash
   cp games/.env.example games/.env    # fill in passwords, Dragonwilds OWNER_ID
   scripts/games.sh env                # uploads it to the VM (never to git or tofu state)
   ```
   Your Dragonwilds **Player ID** is at the bottom of the in-game Settings menu (copy button).
   It works from the Xbox version too.

4. **Discord** (optional, but it's how friends find out what's up)
   1. Go to <https://discord.com/developers/applications> → **New Application** "Skippy" → **Bot** → Reset Token → copy it.
   2. Invite it to your server (replace `APP_ID`; the permissions are Manage Channels, Manage Webhooks,
      Manage Messages (to pin), View Channels, Send Messages, Read History):
      `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot&permissions=536947728`
   3. Run the setup. It creates the **Game Servers** category, then `#valheim` + 🔊 Valheim,
      `#factorio` + 🔊 Factorio, and so on, each with a webhook and a pinned status post:
      ```bash
      DISCORD_BOT_TOKEN=... python scripts/discord_setup.py --invite gPTVS7Hmj
      scripts/games.sh env
      ```
      It's safe to re-run: it reuses what exists and re-pins. Only games you list with `--games` are touched.
      The bot token is only used here, on your PC. The VM only gets webhook URLs.
      You can remove the bot from the server afterwards if you like.

## Playing

```bash
scripts/games.sh up dragonwilds                 # just Dragonwilds
scripts/games.sh up valheim factorio            # two at once (VM sized automatically)
scripts/games.sh up satisfactory --idle 120     # longer grace before auto-sleep (0 = never)
scripts/games.sh status
scripts/games.sh logs dragonwilds
scripts/games.sh down                           # save + sleep now
```
Or use `make games-up GAMES="valheim dragonwilds"`, `make games-down`, `make games-status`.

What happens:
- The VM is resized to fit the games (8 → 64 GB) and boots. The requested servers start;
  others stay off.
- Each game's pinned Discord post flips to 🟢 with connection details. A "starting up" message
  goes to that game's channel.
- The watchdog checks every 5 minutes. After `--idle` minutes (default 30) with no player
  traffic, it saves the worlds, flips the posts to ⚫, posts "went to sleep", and powers off.
  A server nobody joins goes back to sleep 30 minutes after starting.
- The first start of each game downloads the server (5–10 min). Dragonwilds and the others
  update on every start, so the server version matches the game client.

### Per-game notes

| Game | Port(s) | First-run chores |
|---|---|---|
| Valheim | UDP 2456–2457 | Set `VALHEIM_PASSWORD` (≥5 chars). Import an old world by copying `.db`/`.fwl` into `data/valheim/config/worlds_local/`. |
| Factorio | UDP 34197 | Set `game_password` in `data/factorio/config/server-settings.json`, then restart. |
| Satisfactory | UDP+TCP 7777, TCP 8888 | The first player to connect claims the server and sets the admin + join passwords. Do this right away. |
| Dragonwilds | UDP **7778** (moved off 7777 so it can run beside Satisfactory) | `DRAGONWILDS_OWNER_ID` + `DRAGONWILDS_ADMIN_PASSWORD` required. Players join with the **invite code** shown on the portal's Dragonwilds page (new every boot), or search the server name in the Public tab (whole name, exact case: `Skippy` won't find `SkippyScape`) plus the world password; crossplay with Xbox/PS5/Switch 2. Up to 6 players. |

### Moving a Nitrado world over
Download the save from Nitrado's file browser, `scripts/games.sh up <game>` once so the folders
exist, then copy the save onto the VM
(`gcloud compute scp <file> skippy-games:/srv/games/data/<game>/... --zone us-west1-b`) and restart.

### Backups
The data disk is snapshotted daily at 3am Pacific and kept 7 days (`snapshot_retention_days`).
To restore: `scripts/games.sh down`, then in the console go to Disks → create a disk from the snapshot, and on the VM → Edit, detach `skippy-games-data` and attach the restored disk with device name `games-data`.

## Testing at home tonight (no GCP needed)
The same compose file runs on the PC under Docker Desktop:
```powershell
cd games; copy .env.example .env   # fill it in
docker compose --profile dragonwilds up -d
docker logs -f dragonwilds
```
To let people outside your network in, forward the game's UDP port on your router (Dragonwilds: 7778).
