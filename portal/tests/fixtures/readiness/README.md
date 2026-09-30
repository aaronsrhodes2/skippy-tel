# Real game logs for portal/readiness.py

Trimmed captures of real containers, made on 2026-09-30. They are what `docker logs <game> 2>&1` printed (which is what
`infra/gcp-games/diag.sh` scans), minus Docker's own timestamps. **Only whole lines were dropped, never edited**, and the
order is the original; the two exceptions are redactions: the developer's home IP and the VM's public IP became
`203.0.113.7` (a documentation address), and the lines carrying a generated Factorio RCON password were dropped. Dropped as noise: steamcmd download progress and file lists, Unity start-up and shader chatter,
world-generation "Failed to place all ..." lines, Factorio mod loading and checksums, entrypoint `set -x` traces (except
in the VM sample), and long PlayFab connection strings.

| File | What it is | How it was made |
|---|---|---|
| `valheim-restarted-container.txt` | One container, three boots: (1) first boot, server downloaded through steamcmd, **fresh world**; (2) `docker restart`, the world now saved; (3) the game process SIGKILLed and respawned by supervisord. Crossplay off. | `ghcr.io/lloesche/valheim-server:latest` as `lab-readiness-valheim` with the env of `games/docker-compose.yml` (`SERVER_NAME`, `WORLD_NAME`, `SERVER_PASS`, `SERVER_PUBLIC=false`, `CROSSPLAY=false`, `BACKUPS`...), no published ports; `docker restart`, then `pkill -9 -f valheim_server.x86_64` inside. |
| `valheim-crossplay-boot.txt` | One boot with `CROSSPLAY=true` up to `Session ... is active`, then a `docker stop`. | A new container on the same volumes, run for **about two minutes** (it registers the host's IP with PlayFab while it runs), no published ports, then removed. |
| `factorio-restarted-container.txt` | One container: (1) first boot with a **new save** (Factorio runs twice: `--create`, then `--start-server`), public listing on, so `Missing token`; then `"visibility": {"public": false}` was set in server-settings.json and (2) `docker restart` (its SIGTERM shutdown is in the log) gave (3) a boot with no `Missing token` and the same ready line; ended with a `docker stop`. | `factoriotools/factorio:stable` as `lab-readiness-factorio` with the env of `games/docker-compose.yml`, no published ports. |
| `factorio-vm-boot-ops-order.txt` | The real games VM, one boot of Factorio with its existing save, **in the ops agent's stream order**: `ops_agent.py`'s `tail` prints stdout, then stderr, so the entrypoint's `set -x` lines come *after* Factorio's own. | `gh workflow run ops.yml -f command=tail -f args="factorio 300"` (read-only). |

The lab containers and volumes were named `lab-readiness-*` and removed afterwards. Nothing here is a secret: the
lab passwords and the join code in the crossplay log belonged to throw-away servers that no longer exist.

## Re-capturing

Run the same image with the same env, `docker logs -t <container> > raw.log`, cut out the noise with `grep -v`, strip
the leading timestamps, and check the markers in `portal/readiness.py` still fire in the right places:
`python -m pytest portal/tests/test_readiness.py portal/tests/test_readiness_diag.py`. When a game update reworded a line,
the tests over these files are what fail first; replace the fixture with a new capture, don't loosen the pattern.
