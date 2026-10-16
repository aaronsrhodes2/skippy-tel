# Garry's Mod: admin-only server, with NPC bots for the Captain (APPLIED ON BRANCH, NOT LIVE)

Status: APPLIED ON THE BRANCH, NOT LIVE. The gate is written by `infra/gcp-games/gmod_addon.sh` (function `admin_gate`) and reaches the VM on the next boot or `restart-game garrysmod` once the change is on `main` and deployed. Nothing on the running server has changed yet.
Steam ID in use: 76561197989151002 (the Captain, sole admin).
Scope: the `garrysmod` container on the games VM (`games/docker-compose.yml`, service `garrysmod`, image `ceifa/garrysmod:latest`).

## What the server does today

- Join control is the join password only: `GARRYSMOD_PASSWORD` becomes `+sv_password` in `ARGS` (games/docker-compose.yml line 155). Anyone with the password can join.
- Lua runs from `data/garrysmod/addons`. The Captain's `mentat-stage` addon is fetched from GitHub `master` at every boot (`infra/gcp-games/gmod_addon.sh`). Anyone who can push to that repo can run Lua on the server.
- Nothing restricts who may join by Steam account.

## Why a password is not enough

`sv_password` cannot tell one person from another. An admin-only server needs a check on the SteamID64 of each connecting player. That check is server-side Lua, not a launch argument.

## The change, in three parts

### 1. Admin gate (a Lua addon, own folder)

Put the gate in its own addon folder, `data/garrysmod/addons/skippy-admin-only/`, not in the `mentat-stage` addon. Reason: `mentat-stage` is refreshed from GitHub at every boot and the gate must not be replaceable by a push to that repo.

The gate as written is in `infra/gcp-games/gmod_addon.sh` (function `admin_gate`, heredoc). It is written only when its content differs, so file times stay put.

Failure note: `CheckPassword` is the hook that decides joins. If the gate's Lua ever errors, the hook is not registered and joins fall back to the join password, which is therefore kept set (Part 3). The gate is not fail-closed on its own.

### 2. NPC bots (the Captain's own, not player slots)

- NPCs spawned from the Captain's admin session (`npc_*` entities via the sandbox spawn menu, or a small Lua tool that the Captain runs) do not take a player slot and do not join. Nothing is needed to allow them; the admin gate does not block them.
- "Bot characters" that are real players (the `bot` command, `ai_disabled` off) DO take a slot. Keep bots out of the design unless the Captain wants them. If he does, cap them with `GARRYSMOD_MAX_PLAYERS` and allow them through the gate by their bot SteamID64 (bots have none; this needs a separate check by name, which is weaker).
- LLM-connected NPCs (the experiments): a Lua `http.Fetch`/`http.Post` call from the server to an LLM endpoint. **The API key must not be in the addon or in the repo.** Put the key in a small proxy on the games VM (or on skippy-pc behind the bridge) and let the NPC call the proxy with no key in Lua. The addon then holds only the proxy URL.

### 3. Second lock and recovery

- Keep `GARRYSMOD_PASSWORD` set. If the gate fails, the password still stops strangers. This is the one lock that survives a broken addon.
- The portal's join password is shown in plain text on purpose (CLAUDE.md, "Join passwords"). Leave it as it is.
- rcon is not set in `ARGS` today. Recovery uses `ops restart-game garrysmod` (stops and restarts that container only) and the container's console. Adding `+rcon_password` would be a second change; not part of this proposal.

## Lockout risks

1. **Wrong SteamID64.** If `REPLACE_WITH_CAPTAIN_STEAMID64` is wrong, the Captain is refused too. Confirm it on the server first: the server log shows the SteamID64 of each join attempt (`ops search garrysmod "CheckPassword|SteamID"`), or use a public SteamID64 lookup on his profile. Enable only after one test join from the account that will be used.
2. **Alt accounts or Steam Family Sharing.** A borrowed or family-shared login has its own SteamID64. Only the account he plays on counts.
3. **Lua error.** A broken gate removes the check and joins fall back to the join password only. It cannot lock everyone out by itself. Check the log after each restart.
4. **Mentat addon push.** A push to `sigma-ground` `master` cannot remove the gate (separate folder). It can still run Lua on the server, which the gate does not stop. Pin `GARRYSMOD_ADDON_REF` if that matters.
5. **Existing connections.** The gate applies at join. A 15-second timer removes anyone already connected who is not on the list.

## Verify (before the change goes live)

1. Syntax: extract the gate from `gmod_addon.sh` and run a Lua 5.1 syntax check (`luac -p`). Expected: no output. (Not done here: no `luac` on this box.)
2. Logic: call the `CheckPassword` function with the Captain's SteamID64 (expect no return) and with a made-up SteamID64 (expect `false, "This server is admin-only."`).
3. Live, after enable: (a) the Captain joins from his account; (b) a second Steam account (a friend's, with permission) is refused with the message; (c) `ops tail garrysmod` shows the refusal.
4. Confirm the password line is still in the ARGS of the running container (`docker inspect garrysmod`).

## Rollback

1. Remove the gate folder: `rm -r data/garrysmod/addons/skippy-admin-only` on the VM (or the ops path for the data disk).
2. Restart the container through the ops workflow: `restart-game garrysmod`.
3. Confirm joins work with the password only (the old state). The `mentat-stage` addon is untouched by rollback.

## Not done by this proposal

- No change to `games/docker-compose.yml`, `infra/`, or the portal settings.
- No Steam account IDs written into the repo beyond the placeholder.
- No LLM key anywhere; the proxy is a separate step.
