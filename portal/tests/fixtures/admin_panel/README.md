# Real samples for the Server admin card (portal/tests/test_admin_panel.py)

Everything here was captured on 2026-09-29/30 from the real images the games VM runs, started locally in Docker as
`lab-admin-*` containers with the environment games/docker-compose.yml gives them (no published ports). Nothing was
invented, but the mods used are do-nothing ones **written for the observation** (a Valheim plugin, not kept, and the two
Factorio zips below), because the point was to see how each server lists mods and no real mod was downloaded from a
third party for a test:

| File | What it is |
|---|---|
| `valheim/docker.vanilla-first-start.txt` | `docker logs` of `ghcr.io/lloesche/valheim-server:latest` (image built 2026-09-19): first start, vanilla. Trimmed (marked `[... omitted ...]`), lines untouched. |
| `valheim/docker.bepinex-restart.txt` | The same image restarted with `BEPINEX=true` and one plugin installed: the `Loading [Lab Plugin 1.2.3]` line, two boots in one log, and the byte-order mark BepInEx writes at the start of its header. |
| `valheim/LogOutput.with-plugin.txt`, `LogOutput.no-plugins.txt` | BepInEx's own `LogOutput.log` from `data/valheim/server/bepinex/BepInEx/` (rewritten on every start), with and without a plugin. The plugin (`Lab Plugin 1.2.3`) is a do-nothing DLL compiled for this from a 10-line C# file against the image's own `BepInEx.dll`. |
| `valheim/appmanifest_896660.acf` | Steam's manifest for the Valheim dedicated server, from `dl/server/steamapps/`. |
| `factorio/factorio-current.txt` (a `.log` here would be git-ignored) | The first 60 lines of `/factorio/factorio-current.log` from `factoriotools/factorio:stable` (2.0.77), with the two lab mods loaded. Cut before the lines that hold the PC's public IP. |
| `factorio/mods/` | The mods folder at that moment: `mod-list.json` as Factorio rewrote it, and two do-nothing lab mods, one with a `data.lua` (`skippy-lab-mod_0.1.3.zip`) and one control-script-only (`skippy-lab-control_1.0.0.zip`), which the log names but never gives a version. |
| `factorio/docker.update-mods-four-boots.txt` | The image's mod updater (`UPDATE_MODS_ON_START=true`) with no username or token, across four starts of one container: it downloads nothing and gets `403`. |
| `docker.inspect-container.*.txt`, `docker.inspect-image.*.txt` | The exact `docker inspect -f` / `docker image inspect -f` outputs game_facts.py asks for (`everquest` is the Captain's home AkkStack container, `eqemulator/eqemu-server:v16`). |
| `facts.lab-live-bepinex.txt`, `facts.lab-live-vanilla.txt` | The output of `infra/gcp-games/game_facts.py` itself, run for real against the live lab containers (Valheim with BepInEx on; then recreated vanilla, with BepInEx's old log still on disk). |

Not captured, because those servers are too heavy to run here: Satisfactory, Dragonwilds, and the EverQuest server itself.
The tests that touch them say UNVERIFIED and use text from the images' documentation, not a run.
