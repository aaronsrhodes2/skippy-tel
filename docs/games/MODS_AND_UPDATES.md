# Mods and updates, per game

What each game server we run does about keeping its **server version** and its **mods** current, where the mods live on
the games VM's data disk, how to see what is installed, and what is not supported. This is what the admin-only
**Server admin** card on each game's page (`portal/admin_panel.py`) shows and controls, and why it says what it says.

**How much of this was seen, not read.** Valheim (`ghcr.io/lloesche/valheim-server:latest`) and Factorio
(`factoriotools/factorio:stable`) were started locally in Docker with the environment `games/docker-compose.yml` gives
them (2026-09-29/30), their scripts were read inside the images, and the real logs and folders are the test fixtures
(`portal/tests/fixtures/admin_panel/README.md`). Satisfactory, Dragonwilds and EverQuest are too heavy to run here:
what is said about them comes from their images' documentation, the compose file and the read-only `ops` output, and
every such claim is marked **UNVERIFIED**. Unmarked claims were observed.

| Game | Server version updates | Mod support on our server | Mods live in (`data/…` on the VM) | Installed version is read from |
|---|---|---|---|---|
| Valheim | Every start, and every 15 min while nobody is on (Steam). No switch, no pin. | BepInEx, opt-in (`VALHEIM_BEPINEX`). The pack updates itself; plugins never do. | `valheim/config/bepinex/plugins/` | container log `Valheim version:`; Steam manifest for the build |
| Factorio | Every boot: the image tag is pulled. Tag = version, so it can be pinned (`FACTORIO_VERSION`). | Native mods; the image can update them from mods.factorio.com (`FACTORIO_UPDATE_MODS`, needs your factorio.com username + token). | `factorio/mods/` | `factorio/factorio-current.log`, first line |
| Satisfactory | Every start unless `SKIPUPDATE=true` (`SATISFACTORY_SKIP_UPDATE`). UNVERIFIED | Possible by hand; the image installs and updates nothing. UNVERIFIED | `satisfactory/gamefiles/FactoryGame/Mods/` UNVERIFIED | Steam manifest (UNVERIFIED path) |
| RuneScape: Dragonwilds | Every start (`UPDATE_ON_START`, default true). UNVERIFIED | None documented. | n/a | Steam manifest (UNVERIFIED path) |
| Garry's Mod | Every boot: the image is pulled, and its start-up updates the server files from Steam. UNVERIFIED. No switch, no pin. | A Workshop collection (`GARRYSMOD_WORKSHOP_COLLECTION`), downloaded by the server at every start; addons by hand. UNVERIFIED | `garrysmod/addons` | Not read yet (the card shows the image's age) |
| EverQuest | Never at boot: AkkStack is pinned to one commit. | None: the world is a database. | n/a | `.git/HEAD` of the AkkStack checkout; the image tag |

## Valheim

Image `ghcr.io/lloesche/valheim-server:latest`, `SERVER_PUBLIC=false`, crossplay off by default (unchanged).

**Where things are** (compose mounts `data/valheim/config` at `/config` and `data/valheim/server` at `/opt/valheim`; all seen):

```
data/valheim/config/            worlds_local/  adminlist.txt  bannedlist.txt  permittedlist.txt  prefs  backups/
data/valheim/config/bepinex/    BepInEx.cfg  BepInEx.cfg.default  plugins/  patchers/        (only once BEPINEX=true has run)
data/valheim/server/dl/server/  the Steam download; steamapps/appmanifest_896660.acf is its manifest
data/valheim/server/server/     the installed vanilla server
data/valheim/server/bepinex/    the vanilla server merged with BepInEx; BepInEx/plugins/, BepInEx/LogOutput.log,
                                BepInEx/config -> /config/bepinex
data/valheim/server/dl/bepinex/ BepInEx.zip and the dates it was fetched
```

**Server updates.** At every container start the image's updater runs `steamcmd +app_update 896660 validate` as an
anonymous login and logs either `Valheim Server was updated - restarting` or `Valheim Server is already the latest
version`. While the server runs, a cron (`UPDATE_CRON`, default `*/15 * * * *`) checks again, but only when nobody is
connected (`UPDATE_IF_IDLE=true`), and a new build restarts the server (the image also restarts it daily at 05:10
container time when idle, `RESTART_CRON`). There is no supported setting to skip this or to
hold an older build; `PUBLIC_TEST=true` switches to the public-test branch, which is not a pin. The compose file pulls
the image itself fresh on every boot as well (`startup.sh`: `compose pull`).

**Installed version.** Once per start the server prints `Valheim version: l-1.0.16 (network version 40)`; BepInEx's log
has `Valheim Version: l-1.0.16`. The Steam manifest (`appmanifest_896660.acf`) holds `buildid` (25527701) and
`LastUpdated` (epoch seconds: when Steam last changed the files, our "last updated"). `game_facts.py` prints both:
`l-1.0.16 (Steam build 25527701)`. The line is in the container's log, which is per boot (compose recreates the
container at every boot); a log that rolled over would lose it, and the card would then show the Steam build alone.

**Mods** are BepInEx plugins, only while `BEPINEX=true` (`VALHEIM_BEPINEX`, default false). With it on, every start:
the updater downloads *BepInExPack_Valheim* from Thunderstore (denikson's pack; 5.4.2351 today), merges it over the
server into `data/valheim/server/bepinex`, and copies whatever is in `data/valheim/config/bepinex/plugins/` (and
`patchers/`) into `BepInEx/plugins/`. Taking a file out of `config/bepinex/plugins/` removes it at the next start
(`Removing …/plugins/X.dll - no longer present in /config/bepinex`). The image refreshes **only the BepInEx pack**;
plugins are plain DLLs and nothing updates them: to update one, replace the file on the server disk and Restart &
update. The image also has a ValheimPlus mode (`VALHEIM_PLUS`); we don't wire it.

**The mods list** is BepInEx's own record of the start, `data/valheim/server/bepinex/BepInEx/LogOutput.log`, rewritten
on every start: `[Message:   BepInEx] User is running BepInExPack Valheim version 5.4.2351` and one
`[Info   :   BepInEx] Loading [Lab Plugin 1.2.3] (com.skippy.labplugin)` per plugin. That file outlives a run, so it is
only trusted while the image's own `Server config is …, mod: BepInEx` log line says BepInEx is what ran (with it off
the same log line says `mod: none`, and the old `LogOutput.log` stays on the disk: observed).

**Not supported / to know.** No version pin. Any BepInEx start logs `isModded: True`, even with no plugin. A plugin that
changes gameplay must also be on every player's PC, and console players can't install mods (the general rule; whether a
BepInEx server can be joined through crossplay was not tested: UNVERIFIED). **The image is a "Legacy container image
mirror"** (its own label): the same project now publishes `ghcr.io/community-valheim-tools/valheim-server` (registry
metadata read 2026-09-30: same commit `e36cbfb`, built the same day). Both are identical today; the legacy path may stop
getting builds, so moving to the new one is a decision for the Captain, not made here.

## Factorio

Image `factoriotools/factorio:${FACTORIO_VERSION:-stable}`, data in `data/factorio` (mounted at `/factorio`, all seen):

```
data/factorio/config/    server-settings.json (has empty "username" and "token" keys)  map-*.json  rconpw
data/factorio/mods/      mod-list.json, and <name>_<version>.zip per installed mod
data/factorio/saves/     scenarios/  script-output/  temp/  player-data.json
data/factorio/factorio-current.log   rewritten on every start; the last one is factorio-previous.log
```

**Server updates.** The tag is the version. `stable` moves when Factorio releases a stable build, and the boot's
`compose pull` picks it up (`docker manifest inspect` confirms exact tags such as `factoriotools/factorio:2.0.77`
exist), so today's behaviour is "the newest stable at every boot". `FACTORIO_VERSION=2.0.77` holds the server there.
The portal only checks the *shape* of the value (digits `x.y.z`, `stable` or `latest`), not that the tag exists. If
it doesn't, `startup.sh` starts the games one at a time after the joint `compose up` fails: the other games come up,
and the startup log (`ops logs`, Admin → Logs) says `STARTUP FAILED for: factorio` with compose's own error. It is
never swapped for `stable`: a pin is there to keep the save and the players' clients on one version.
The image carries the version in three places: `ENV VERSION`, the label `factorio.version`, and
`factorio --version` (`Version: 2.0.77 (build 84539, linux64, headless)`).

**Installed version.** The first line of `factorio-current.log`: `Factorio 2.0.77 (build 84539, linux64, headless,
space-age)`. The label is the fallback.

**Mods** are zips in `data/factorio/mods/` (or unpacked folders), switched on and off by `mod-list.json`. The list of
what is really loaded is the log: every loaded mod has a `Checksum of <name>: <n>` line; the version is on
`Loading mod <name> <version> (data.lua)`, which a mod with no `data.lua` never gets (observed with a control-script-only
mod), so its version is taken from its file name. `mod-list.json` alone is not the truth: it can list mods that are
not on disk (Factorio ignores them and rewrote the list without them at the next start), and it does not show a mod
that was just dropped in.

**Mod updates** (`UPDATE_MODS_ON_START=true`, our `FACTORIO_UPDATE_MODS`, default false). The image's `docker-update-mods.sh` /
`update-mods.sh` go through every **enabled** mod in `mod-list.json` except `base`, `elevated-rails`, `quality`,
`space-age`; ask `mods.factorio.com/api/mods/<name>/full`; take the newest release that fits the running game (observed:
`Skipping mod version 2.1.0 because of factorio version mismatch`, then `Downloading even-distribution_2.0.2.zip`);
download it as `?username=…&token=…` if that exact zip isn't already there; check its SHA1; delete older zips of the same
mod. A mod that is listed but not installed is therefore **downloaded**, which is the honest way to add one (no upload).
`UPDATE_IGNORE` (our `FACTORIO_UPDATE_IGNORE`) is a comma list of mods to leave alone. A mod that isn't on the portal
(`Custom mod not on https://mods.factorio.com, skipped.`) is left as it is. Credentials are read from env `USERNAME` and
`TOKEN` (our `FACTORIO_USERNAME`, `FACTORIO_TOKEN`; the token is the factorio.com service token, a secret), else Docker
secrets, else `server-settings.json`. **With none, observed:** `You need to provide your Factorio username…`, then
`Download failed: Code 403.` for each download, and the server starts normally: harmless, but nothing updates. The card
reports each start's outcome (`MODUPDATE`): checked / updated / already current / failed (with the HTTP code) / skipped.

**Watch out: Space Age is on.** This image version defaults `DLC_SPACE_AGE=true`, so every start enables `elevated-rails`,
`quality` and `space-age` in `mod-list.json` (observed: `Enable mod space-age for DLC`), and the server runs Space Age.
Today's live VM log (read-only `ops search`) has a `Refusing connection … ModsMismatch` from a client, then a join a minute
later. That is what a client whose mods differ from the server's gets; the cause of that one refusal is UNVERIFIED, but a
client without the DLC would be refused. We did not change it (see decisions).

## Satisfactory

Image `wolveix/satisfactory-server:latest`, data in `data/satisfactory` (mounted at `/config`). **UNVERIFIED throughout:**
too heavy to run here; from the image's README.

- `/config` holds `backups/`, `gamefiles/` (the 8 GB+ server install), `logs/`, `saved/` (saves, blueprints, config).
- Updates: at every start, unless `SKIPUPDATE=true` (default false; our `SATISFACTORY_SKIP_UPDATE`). `STEAMBETA=false`
  (compose) is the stable branch. Skipping means players on a newer client can't join.
- Installed version: Steam's `steamapps/appmanifest_1690800.acf` under `gamefiles` (path UNVERIFIED; the script tries
  several places and prints nothing if it finds none). The build id is not the version the game shows.
- Mods: the README says they "work" but are "rough around the edges", follow the Ficsit docs, and the container has no
  file-transfer server of its own. The stock server has no mod list; by the Ficsit docs a mod is a folder under
  `FactoryGame/Mods/` with a `<name>.uplugin` JSON file (`FriendlyName`, `VersionName`), which is what the card would
  list. Nothing installs or updates them, and a game update can break them.

## RuneScape: Dragonwilds

Image `indifferentbroccoli/runescape-dragonwilds-server-docker:latest` (Steam app 4019830), data in `data/dragonwilds`
(mounted at `/home/steam/server-files`: "server installation files, saves, and configuration"). **UNVERIFIED throughout.**

- Updates: `UPDATE_ON_START` (default true; our `DRAGONWILDS_UPDATE_ON_START`) downloads and validates the server files at
  every start. The server must match the game client, so leave it on.
- Version: the README says nothing about how it appears in logs. `game_facts.py` looks for `appmanifest_*.acf` under
  `data/dragonwilds` and its `steamapps`/`server-files/steamapps`, and shows `Steam build N` if it finds one.
- Mods: none documented, no pinning. The card says so plainly.

## Garry's Mod

Image `ceifa/garrysmod:latest` (Valve's dedicated server, Steam app 4020), `PUID`/`PGID` 1000, only `garrysmod/data` and
`garrysmod/addons` are kept on the data disk (`data/garrysmod/...`): the server itself lives in the image. **UNVERIFIED
throughout**: read from the image's README, not run here.

- Updates: the image is pulled at every boot; the image's own start-up is said to update the server files from Steam.
  There is no switch to hold a version.
- Mods: `GARRYSMOD_WORKSHOP_COLLECTION` is turned into `+host_workshop_collection <id>` on srcds's command line
  (`games/docker-compose.yml` builds the image's `ARGS` from the settings that are set). The server downloads the whole
  collection at every start and the players download the same addons when they join. A collection id, never a single addon's.
  Addons can also be dropped into `data/garrysmod/addons` by hand.
- The Captain's own mod (`mentat-stage`, Lua only, ~0.5 MB) is not in a collection: `gmod_addon.sh` fetches it from the public
  `aaronsrhodes2/sigma-ground` repo (`master`, folder `stage/gmod/addon`) at every boot and at every `ops restart-game garrysmod`.
  It records the folder's git tree id in `addons/mentat-stage/.source` and touches nothing when that id is unchanged, because
  `game_backup.py` fingerprints sizes and mtimes of `garrysmod/addons` and would otherwise archive the addon at every boot.
  The image has no git (Debian trixie-slim: wget, tar, unzip, gosu), so the clone is made on the VM, never in the container.
  **Which version:** the setting `GARRYSMOD_ADDON_REF` ("Mod version" in the admin panel). Blank follows `master` (the development
  loop: push, `ops restart-game garrysmod`, test), which means whoever can push to that repo can run Lua on this server and send Lua
  to every player who joins. A full 40-character commit id pins exactly that version (fetched by id; a branch can be moved, a commit
  cannot), so a compromised GitHub account changes nothing here. Pin one before the server is shown to strangers; the pin lives in
  the portal, behind the Captain's login, not in either repo.
- The join password is `+sv_password` on the same command line, so it is limited to letters, digits and `_ . -`.
- Any other srcds argument: `GARRYSMOD_EXTRA_ARGS` (appended to `ARGS` last). Any other variable of the image (`PRODUCTION`, `AUTOUPDATE`,
  `GSLT`, `AUTHKEY`, `PORT`): one more line under `garrysmod.environment` in `games/docker-compose.yml`.
- Not done: a Steam game server login token (GSLT, `GSLT` in the image) for being listed on Steam's master list. Players
  join by address, so the server runs anonymously.

## EverQuest

The Captain's Golden Era EQEmu, via AkkStack, its own compose project (`infra/gcp-games/everquest.sh`), everything under
`data/everquest`. The AkkStack commit is pinned by `AKK_REF` in that script; nothing updates at boot, and the world is the
database (`data/everquest/akk-stack/data/mariadb`). Home stack (observed): the server container is
`akk-stack-eqemu-server-1` on `eqemulator/eqemu-server:v16` (built 2025-02-27). On the VM (UNVERIFIED): the installed version
is the commit in `data/everquest/akk-stack/.git/HEAD`. To update: change `AKK_REF` and deploy; the start then runs
`world database:updates`. Mods: none; the client is patched by hand (`eqhost.txt`).

## The card and how the facts get there

`infra/gcp-games/game_facts.py`, shipped in instance metadata as `game-facts`, is run by `diag.sh` every 2 minutes under a
`--- game facts` header in the serial-port snapshot the portal already reads for join codes. Per running game, only these
lines (never settings, environment or secrets; `docker inspect` is only asked for fixed fields):

```
VERSION valheim=l-1.0.16 (Steam build 25527701)
IMAGE valheim=ghcr.io/lloesche/valheim-server:latest 2026-09-19T19:48:18Z     image tag, and when that image was built
UPDATED valheim=2026-09-30T06:48:47Z                                          Steam games: server files last changed
STARTED valheim=2026-09-30T07:01:53Z
MODS valheim=BepInExPack_Valheim@5.4.2351;Lab Plugin@1.2.3                     empty = none loaded; no line = no list to read
MODUPDATE factorio=checked:2,downloaded:0,current:0,failed:1,skipped:1,code:403
```

`portal/diagnostics.py` (`game_facts()`) parses them; `portal/admin_panel.py` builds the card. The card is inside the game
page's admin-only block and refreshes every minute from `GET /admin/games/<game>/panel` (admin only: a friend gets 403, a
signed-out visitor the sign-in redirect). While the server sleeps there are no facts, so the card shows only the policy
the next start will follow. "Image built" is how old the image is, not when it was pulled: Docker's own "last tagged"
time moves on a pull that changed nothing, so it isn't used.

## Settings added (Admin only; blank means the default, which is what the servers did before)

| Setting | Default | Effect | Needs |
|---|---|---|---|
| `VALHEIM_BEPINEX` | false | Install BepInEx and load `config/bepinex/plugins` at every start | nothing |
| `FACTORIO_VERSION` | stable | `stable`, `latest` or an exact version such as `2.0.77` (the image tag) | nothing |
| `FACTORIO_UPDATE_MODS` | false | Update enabled mods (and download listed ones) at every start | `FACTORIO_USERNAME` + `FACTORIO_TOKEN` |
| `FACTORIO_USERNAME` | blank | factorio.com username, for mod downloads | |
| `FACTORIO_TOKEN` | blank | factorio.com token: a secret, never shown again, never generated by "Set up automatically" | |
| `FACTORIO_UPDATE_IGNORE` | blank | Mods to leave alone, comma separated | |
| `SATISFACTORY_SKIP_UPDATE` | false | true keeps the installed build (UNVERIFIED) | |
| `DRAGONWILDS_UPDATE_ON_START` | true | false stops updating at start (UNVERIFIED) | |

`portal/tests/test_admin_panel.py` keeps each default equal to the `${KEY:-default}` in `games/docker-compose.yml`.

## Updating a mod by hand, per game (no upload, no installer)

Files go onto the server disk the way world files always have (`gcloud compute scp` to `/srv/games/data/<game>/…`, see
[README.md](README.md)), then **Restart & update** on the game's page.

- Valheim: replace the DLL in `valheim/config/bepinex/plugins/`. Remove a plugin by deleting it there.
- Factorio: with `FACTORIO_UPDATE_MODS` on and credentials saved, Restart & update updates every enabled mod; otherwise
  replace the zip in `factorio/mods/`, and delete the old version's zip.
- Satisfactory: mods go under `satisfactory/gamefiles/FactoryGame/Mods/` per the Ficsit docs (UNVERIFIED).
- Dragonwilds, EverQuest: nothing to update.
