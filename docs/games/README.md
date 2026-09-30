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
| Idle watchdog (auto-sleep + session limit) | [`infra/gcp-games/idle_watchdog.py`](../../infra/gcp-games/idle_watchdog.py), see [Auto-sleep](#auto-sleep-the-session-limit-and-how-to-see-why-a-vm-stayed-up) |
| Monthly budget alert | [`infra/gcp-games/budget.tf`](../../infra/gcp-games/budget.tf), see [docs/cicd.md](../cicd.md#cost-alarm-monthly-budget) |
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
   The budget alert ($30/mo, emails at 50/90/100%) is in Terraform (`infra/gcp-games/budget.tf`) and switches on when
   you give it the billing account id (`BILLING_ACCOUNT`; the deployer also needs `billing.budgets` permission on that
   account, e.g. `roles/billing.costsManager`). Steps: [docs/cicd.md](../cicd.md#cost-alarm-monthly-budget). Or make
   one by hand in the console under **Billing → Budgets & alerts**.

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
- The watchdog checks every 5 minutes. After `--idle` minutes (default 30) with no players,
  it saves the worlds, flips the posts to ⚫, posts "went to sleep", and powers off.
  A server nobody joins goes back to sleep 30 minutes after starting. Whatever it sees, a session
  also ends `max-hours` (default 8) after boot, with a Discord heads-up 15 minutes before.
- The first start of each game downloads the server (5–10 min). Dragonwilds and the others
  update on every start, so the server version matches the game client.

### Auto-sleep, the session limit, and how to see why a VM stayed up
[`idle_watchdog.py`](../../infra/gcp-games/idle_watchdog.py) runs on the VM every 5 minutes (a systemd timer;
the script is fetched from instance metadata `idle-watchdog` at every boot, so a change reaches a VM at its
next boot).

- **Nobody playing** for `idle-minutes` (default 30) in a row: save the worlds, post to Discord, power off.
  "Playing" is, in this order: a game's own log giving an exact count (Valheim: Steam `Got connection` /
  `Closing socket` pairs, or a crossplay server's session line; Factorio: `[JOIN]` / `[LEAVE]`); packets on
  two-way flows the kernel marks ASSURED (a player's stream: a flow must be answered *and* keep going for over
  2 seconds, so a scanner's one-shot probe never qualifies); and only when neither can be read, the raw packet
  count on the game ports at a high bar (3000 per 5 minutes). Scanner and server-browser noise is ignored.
  A log count only covers the game's latest start (a `docker restart`, a crash or Valheim's server being respawned
  cuts every player off without a "left" line, and the container's log keeps the old run).
  Evidence that can't be read never means "nobody": the idle clock holds, and the limit below ends the session.
  The "went to sleep" post is made once, by the watchdog, with the real reason; it leaves
  `/run/skippy-games/offline-posted` so the VM's shutdown script (which runs on that poweroff) does not post a second,
  bare "stopped". Every other way the VM stops has no such note and gets that one "stopped" post from the script.
  Each step of the shutdown (`docker compose stop`, EverQuest's stop, the Discord post, `poweroff`) logs its exit
  status and the tail of its output under `skippy-idle`.
- **Session limit**: metadata `max-hours` (Terraform variable `max_hours`, default **8**, 0 = none) stops the VM
  that many hours after boot whatever the traffic says, with a heads-up in the game's Discord channel 15
  minutes ahead. It is off while a session's idle-minutes is 0 (the admin's "never sleep").
  To change it: edit the variable's default (or `terraform.tfvars`) and push; nothing else writes it.
- **The portal's Watcher** (Dashboard → warnings) says so when the server has been on longer than the limit plus
  an hour, or longer than 2 hours with nobody playing *and* the watchdog's idle clock past its limit by a check (or
  its checks not running: the report's `last_check_s`). The 30 idle minutes after a long session are not a warning.
- **Why the first version never slept the VM** (found 2026-09-30; kept here so nobody re-learns it). It ran
  `iptables -I -t mangle PREROUTING ...`. iptables reads the word after `-I` as the *chain*, so `-t` was the
  chain and `mangle` a rule number: `Invalid rule number `mangle'`, exit status 2, and `set -e` ended the script
  before its first count: every 5 minutes, for the life of the VM, with nothing in the journal under its tag.
  Reproduced by running the original script unmodified on Debian 12 (iptables 1.8.9, nf_tables and legacy).
  Whether scanner traffic would also have exceeded its 500-packets threshold could not be tested (a home
  network lets nothing unsolicited in); the new logic doesn't depend on it.
- **Seeing what it sees**: the `ops` workflow's `logs` prints an "idle watchdog" block (refreshed every 2
  minutes, and live after a deploy: it runs the script straight from metadata, no reboot): the timer and
  service state, the journal under `skippy-idle` and the unit's own journal (where a crashing watchdog says
  why), `/run/skippy-games/{packets,idle}`, the iptables counters, and the report: raw and ASSURED packets since
  the previous snapshot, the kernel's UDP flows to the game ports by state (sustained / answered once /
  unanswered) with the number of distinct source addresses, the exact player counts (`PLAYERS game=N`), and one
  `WATCHDOG verdict=active|idle|unknown ...` line with the reason. Reading it adds two counting-only iptables
  rules (no target: they change no traffic), so counters run even on a VM that hasn't rebooted onto this version.
  `journalctl -t skippy-idle` on the VM has one line per check.

### Per-game notes

| Game | Port(s) | First-run chores |
|---|---|---|
| Valheim | UDP 2456–2457 | Set `VALHEIM_PASSWORD` (≥5 chars; it may not appear inside the world name or seed). The world grows from `VALHEIM_WORLD_SEED` (default `skippyhasyourmoney`; `keep` = leave it): at boot `valheim_seed.py` writes the world's metadata file, and a world with another seed is set aside in `data/valheim/config/replaced-worlds/`, never deleted. A world put back on purpose (`restore-game`, or copied in) is left alone until the seed setting changes again. **Crossplay** (`VALHEIM_CROSSPLAY`, default `false`): `true` gives a 6-digit **join code**, new every boot, for Steam, Xbox and Game Pass (Join Game → Add server → paste it), but the server then stops listening on 2456, so `ip:2456` no longer works, and its players never touch the ports the idle watchdog's packet counters watch (a crossplay server binds 2457 and one ephemeral port: checked). The watchdog reads such a server's own player count from its log instead, but only a server with 0 players has been seen, so leave it off until someone has played on it with the watchdog running. Import an old world by copying it into `data/valheim/config/worlds_local/`. |
| Factorio | UDP 34197 | Set `game_password` in `data/factorio/config/server-settings.json`, then restart. |
| Satisfactory | UDP+TCP 7777, TCP 8888 | The first player to connect claims the server and sets the admin + join passwords. Do this right away. |
| Dragonwilds | UDP **7778** (moved off 7777 so it can run beside Satisfactory) | `DRAGONWILDS_OWNER_ID` + `DRAGONWILDS_ADMIN_PASSWORD` required. Players join with the **invite code** shown on the portal's Dragonwilds page (new every boot), or search the server name in the Public tab (whole name, exact case: `Skippy` won't find `SkippyScape`) plus the world password; crossplay with Xbox/PS5/Switch 2. Up to 6 players. |

### Mods and server versions
Each game's page has an admin-only **Server admin** card: the installed version, how old the image is, the mods the
server loaded, when it last updated, and the update policy the next start will follow, next to **Restart & update**.
Every boot already re-pulls the images and the servers update their game files (Valheim, Satisfactory and Dragonwilds
from Steam; Factorio by its image tag). Optional Admin settings add the rest: Factorio mod updates and pinning a version,
Valheim's BepInEx mods, skipping a Steam update. Nothing changes until one is turned on. What each image really
supports, where mods live on the data disk, and what is unsupported (there is no upload and no installer) is in
[MODS_AND_UPDATES.md](MODS_AND_UPDATES.md).

### Moving a Nitrado world over
Download the save from Nitrado's file browser, `scripts/games.sh up <game>` once so the folders
exist, then copy the save onto the VM
(`gcloud compute scp <file> skippy-games:/srv/games/data/<game>/... --zone us-west1-b`) and restart.

### How players join, per game
Every game's page on the portal shows the same block: what to type, where, and the join password, all click-to-copy, and it keeps itself current while open.

| Game | The way in | Invite code? |
|---|---|---|
| Valheim | Join Game → Add server → `ip:2456`, or with crossplay on the **join code** instead (the two exclude each other) | Only with crossplay on: new every boot |
| Dragonwilds | Worlds → **Invite Code** (the I key), or the Direct tab `ip:7778`, or search the server name | Yes: new every boot |
| Factorio | Multiplayer → Connect to address → `ip:34197` (one paste, port included) | No (Factorio has none; the address is stable) |
| Satisfactory | Main menu → **Server Manager** → Add Server: address `ip` in the first box, port `7777` in the second (two chips on the page, one click each; confirmed in the real dialog 2026-09-30). **Not** Multiplayer → Join Game (the IP-or-Session-ID screen, for player-hosted games): on a dedicated server it fails with "Encryption Token Missing" | No. Its Session ID is for player-hosted games, and the dedicated server's log has no join code (checked on the live server, 2026-09-30) |
| EverQuest | The `eqhost.txt` block on the page (copy it over the client's file), then `eqgame.exe patchme` | No |

The join password is shown in plain text on purpose (see `CLAUDE.md`, "Join passwords").

### Readiness: when is a game really joinable?
A game's page and card say **Starting** until the game's *own log* says players can join, not for a fixed number of minutes
(`ready_minutes` in `portal/catalog.py` is only the fallback). Every 2 minutes `infra/gcp-games/diag.sh` scans each game's
whole container log and writes `READY <game>=<0|1>` into the serial snapshot (its own `--- readiness` section; `ops
status` shows it as `ready`, `ops logs <game>` as the game's `READY` line). `portal/diagnostics.py` reads it and
`portal/readiness.py` decides. That file holds the markers, the evidence for each and the rules; the short version:

- Read the log top to bottom. A **boot** line means "starting up, forget everything above"; a **ready** line means
  "joinable now". The last one wins. A boot resets the state because `docker restart` (`ops restart-game`), a crash, and a
  game's own updater keep the same container, whose log still holds the old run's ready line.
- A container that isn't running is `0`. **No line** (older VM, first snapshot not there yet, a snapshot from before this
  boot, nothing recognisable in the log) means the page falls back to the boot timer and its "usually joinable about N min"
  hint, exactly as before. A game that has said "not yet" for 45 minutes is also handed back to the timer, so a marker a
  game update reworded can't keep a joinable game on "Starting".
- A marker that was never seen in a real log is **UNVERIFIED** and may only say "ready sooner than the timer", never
  "starting for longer".

| Game | Boot line | Ready line | Status |
|---|---|---|---|
| Valheim | `supervisord started with pid` (container), `valheim-server INFO - Running Valheim Server` (the game process) | `Opened Steam server`; with crossplay on `Session "..." with join code N and IP ... is active` | **Verified** on real boots (fixtures): fresh world, saved world, `docker restart`, crash, crossplay on. Not `Game server connected`: it fires 21 s *before* a fresh world has loaded. |
| Factorio | the `Factorio 2.0.x (build N, ...)` banner | `changing state from(CreatingGame) to(InGame)` | **Verified**, incl. the real VM's boot. A first boot with a new save runs Factorio twice (`--create`, then serve); each banner resets. `Missing token` is harmless: see below. |
| Satisfactory | `LogInit: Engine Version:` | `IpNetDriver listening on port 7777` | **UNVERIFIED**: Unreal Engine's usual lines, not seen in a Satisfactory log. |
| Dragonwilds | `LogInit: Engine Version:` | `Setting ["JoinCode"] written ... value[<code>]` | **UNVERIFIED**: the JoinCode line is real (the portal already reads the invite code from it); that it marks "joinable" and the start line are assumed. |
| EverQuest | `=== skippy-games boot` | `everquest: online at <ip>:5999` | **UNVERIFIED**: lines the scripts really print (`startup.sh`, `everquest.sh`), read from the startup script's output with `journalctl -u google-startup-scripts.service -b`. |

**Factorio's `Missing token`** (`Matching server connection failed: ... Missing token.`) is the server failing to publish
itself on factorio.com's public list: the image's `server-settings.json` has `"public": true` and no factorio.com
credentials. The Captain's servers are joined by address, so it doesn't matter: in every captured boot it comes *after* the
ready line, and with `"public": false` it disappears while the same ready line still appears. (Not tested: a real Factorio
client joining, there was none to hand.)

**Verifying an UNVERIFIED game** (needs one real session of it; do it the first time the game runs):
1. Start it and watch `gh workflow run ops.yml -f command=status` every minute or so: the `ready` field goes `false` → `true`
   for the game (no entry: the log had no recognisable line at all, which is itself a finding).
2. Read the whole boot: `ops tail <game> 2000` (or `search <game> <regex>` for one marker). Confirm the boot line opens the
   log and find the ready line.
3. Try to join with the real client the moment `ready` flips, and again a minute *before* it: the flip should sit
   between "can't join yet" and "can". Note the seconds either way.
4. If the marker is right: save the trimmed log as a fixture in `portal/tests/fixtures/readiness/`, pin it in
   `portal/tests/test_readiness.py` the way Valheim's are, and set `verified=True`. If not: fix the pattern in
   **both** `portal/readiness.py` and `infra/gcp-games/diag.sh` (`test_readiness_diag.py` fails until they match, and runs
   diag.sh's block against every fixture), then do 4 again.
   - EverQuest also needs: `ready` shows `false` from boot (the boot line, read from the journal) and `true` after
     `everquest: online at`. If it never shows up, the journal doesn't carry the startup output in that form.
   - Dragonwilds: also note whether the Direct-tab address (`ip:7778`) starts working before or after the JoinCode line.
     The invite code and the "online" badge appear together, and the badge must not flip before players can join by
     either route (the invite code or the direct address).

### Backups
**Per game (rollback of a bad day).** Each game's world files are flagged in `TARGETS` in
`infra/gcp-games/game_backup.py`. At every boot, with the containers down, `startup.sh` archives them
per game to `data/backups/<game>/<UTC time>-<fingerprint>.tar.gz` (unchanged files aren't archived twice;
the newest 10 are kept, 3 for EverQuest, 6 for Satisfactory). That archive is the state the session started
from. Through the `ops` workflow: `backups <game>` lists them, `restore-game <game> <archive|latest>` stops the
game, sets the current files aside in `backups/<game>/_replaced/` (never deleted), puts the archive back and
starts the game again. A new game or save file means a new `TARGETS` entry (the tests enforce it).

**Whole disk (disaster).** The data disk is snapshotted daily at 3am Pacific and kept 7 days (`snapshot_retention_days`).
To restore: `scripts/games.sh down`, then in the console go to Disks → create a disk from the snapshot, and on the VM → Edit, detach `skippy-games-data` and attach the restored disk with device name `games-data`.

## Testing at home tonight (no GCP needed)
The same compose file runs on the PC under Docker Desktop:
```powershell
cd games; copy .env.example .env   # fill it in
docker compose --profile dragonwilds up -d
docker logs -f dragonwilds
```
To let people outside your network in, forward the game's UDP port on your router (Dragonwilds: 7778).
