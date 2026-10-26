#!/bin/bash
# Garry's Mod: put the Mentat stage addon (sigma-ground, folder stage/gmod/addon) at <data>/garrysmod/addons/mentat-stage, fresh from the
# house, before srcds starts. skippy-core (core/cd, publish.json) puts a tarball of that folder in the games bucket whenever it changes; no
# GitHub, no git on the VM (docs/no-github.md).
#
# Runs on the games VM, as root, from two places (shipped in instance metadata as `gmod-addon`, like valheim_seed.py):
#   * startup.sh, at every boot, with the game containers down;
#   * the ops agent's `restart-game garrysmod`, with the container stopped (ops_agent.py fetches this file from metadata first,
#     so a deploy reaches a VM that booted before it existed).
#
# It NEVER stops the server from starting: every failure is reported on one line and the script still exits 0. A fetch that fails
# (the bucket unreachable, no token, the version not published) leaves the previous copy in place, or no addon on a first boot.
#
# How: one authenticated GET of $BASE/<version>.tar.gz (the VM's own metadata token; the bucket is private) into a scratch folder on the data
# disk. The tarball holds addon/ (the files) and SOURCE ("<tree id> <commit>"). The tree id is compared with the one recorded in the installed
# copy (.source); when they match NOTHING is touched, so file times stay put and game_backup.py (which fingerprints size + mtime, and backs up
# garrysmod/addons) doesn't see a change, and an archive, at every boot. Otherwise the new copy is built beside the old one and swapped in
# with two renames. Links in the archive are dropped: this runs as root on content from outside the VM.
#
# Which version: the portal setting GARRYSMOD_ADDON_REF (read from the games .env), or GMOD_ADDON_REF in the environment.
#   * blank, or `master`: the tip. This is the development loop: push, restart the game, test. It also means whoever can push to
#     that repo can run Lua on this server and send Lua to every player who joins, so use it while the Captain is developing.
#   * a full 40-character commit id: exactly that commit's tarball (published for every version of the addon). A commit id cannot be moved,
#     so a compromised repo or account cannot change what runs here; only the Captain, behind the portal login, can change the pin.
# Other settings (all optional, for tests): GMOD_ADDON_BASE (a URL: https://storage.googleapis.com/... or file://...), GMOD_ADDON_TOKEN,
# GMOD_ADDON_NAME, GMOD_ADDON_OWNER, GAMES_ENV, DATA.
BASE=${GMOD_ADDON_BASE:-https://storage.googleapis.com/skippy-tel-game-assets/gmod-addon}
GAMES_ENV=${GAMES_ENV:-/srv/games/.env}
REF=${GMOD_ADDON_REF:-$(sed -n 's/^GARRYSMOD_ADDON_REF=//p' "$GAMES_ENV" 2>/dev/null | tail -n 1 | tr -d '\r"'"'"' ')}
REF=${REF:-master}
NAME=${GMOD_ADDON_NAME:-mentat-stage}
DATA=${DATA:-/srv/games/data}
OWNER=${GMOD_ADDON_OWNER:-1000:1000}        # the uid:gid srcds runs as in the image (PUID/PGID in games/docker-compose.yml)
GAMEDIR=$DATA/garrysmod
DEST=$GAMEDIR/addons/$NAME
export HOME=${HOME:-/root}

say() { echo "gmod addon: $*"; }

refresh() {
  mkdir -p "$GAMEDIR/addons" || { say "can't make $GAMEDIR/addons; nothing done"; return; }
  rm -rf "$GAMEDIR"/.addon-fetch.*                              # a crashed run's leftovers
  local have
  have=$(cut -d' ' -f2 "$DEST/.source" 2>/dev/null | cut -c1-7); have=${have:-none}

  if ! printf '%s' "$REF" | grep -Eq '^([0-9a-f]{40}|master)$'; then
    say "the version '$(printf '%s' "$REF" | cut -c1-40)' is not a commit id or master; keeping the addon as it is ($have)"
    return
  fi

  local work src out tree commit tok
  work=$(mktemp -d "$GAMEDIR/.addon-fetch.XXXXXX") || { say "can't make a scratch folder; keeping the addon as it is ($have)"; return; }
  tok=${GMOD_ADDON_TOKEN:-}
  case "$BASE" in
    file://*) ;;
    *) [ -n "$tok" ] || tok=$(curl -fsS -m 5 -H 'Metadata-Flavor: Google' \
         http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token 2>/dev/null \
         | sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p') ;;
  esac
  if ! out=$(curl -fsS -m 90 ${tok:+-H "Authorization: Bearer $tok"} -o "$work/addon.tar.gz" "$BASE/$REF.tar.gz" 2>&1); then
    say "couldn't fetch the addon ($REF): $(echo "$out" | tail -n 2 | tr '\n' ' ' | cut -c1-300); keeping the addon as it is ($have)"
    rm -rf "$work"; return
  fi
  src=$work/x
  mkdir "$src" && tar --force-local -xzf "$work/addon.tar.gz" -C "$src" --no-same-owner 2>/dev/null \
    || { say "the addon archive can't be unpacked; keeping the addon as it is ($have)"; rm -rf "$work"; return; }
  find "$src" -type l -delete 2>/dev/null
  if [ ! -d "$src/addon" ] || [ -z "$(find "$src/addon" -type f -name '*.lua' -print -quit)" ]; then
    say "the archive has no Lua in addon/; keeping the addon as it is ($have)"
    rm -rf "$work"; return
  fi
  read -r tree commit < "$src/SOURCE" 2>/dev/null \
    || { say "the archive has no SOURCE; keeping the addon as it is ($have)"; rm -rf "$work"; return; }
  if ! printf '%s %s' "$tree" "$commit" | grep -Eq '^[0-9a-f]{40} [0-9a-f]{40}$'; then
    say "the archive's SOURCE isn't a tree and a commit id; keeping the addon as it is ($have)"
    rm -rf "$work"; return
  fi

  if [ -f "$DEST/.source" ] && [ "$(cut -d' ' -f1 "$DEST/.source")" = "$tree" ]; then
    say "$NAME is already at ${commit:0:7}; nothing to do"
    rm -rf "$work"; return
  fi
  if ! { cp -a "$src/addon" "$work/new" && printf '%s %s\n' "$tree" "$commit" > "$work/new/.source"; }; then
    say "couldn't build the new copy; keeping the addon as it is ($have)"
    rm -rf "$work"; return
  fi
  chown -R "$OWNER" "$work/new" 2>/dev/null || true
  if [ -e "$DEST" ] && ! mv "$DEST" "$work/old"; then        # (a failed move must not let the next one land INSIDE the old folder)
    say "couldn't move the old copy aside; keeping the addon as it is ($have)"
    rm -rf "$work"; return
  fi
  if mv "$work/new" "$DEST"; then
    say "$NAME updated $have -> ${commit:0:7} ($(find "$DEST" -type f | wc -l) files)"
  else
    [ -e "$work/old" ] && mv "$work/old" "$DEST"
    say "couldn't put the new copy in place; keeping the addon as it was ($have)"
  fi
  rm -rf "$work"
}

# The admin-only gate (docs/games/proposals/gmod-admin-only.md): its own addon folder, written from THIS script (shipped in metadata,
# not fetched from the Mentat repo), so a push to that repo can't remove or change it. Only the admin SteamID64 below may join; the
# join password stays set in the container as a second lock. Written only when the content differs, so the file times stay put.
admin_gate() {
  local dir=$GAMEDIR/addons/skippy-admin-only/lua/autorun/server file tmp
  file=$dir/sv_admin_only.lua
  mkdir -p "$dir" || { say "can't make $dir; the admin gate is not installed"; return; }
  tmp=$(mktemp "$dir/.gate.XXXXXX") || { say "can't make a scratch file; the admin gate is not updated"; return; }
  cat > "$tmp" <<'LUA'
-- Admin-only join gate (Skippy). Server-side. Written by gmod_addon.sh; edit the script, not this file.
local ADMINS = {
    ["76561197989151002"] = true,   -- the Captain
}

-- Refuses every join whose SteamID64 is not on the list. Returning nothing lets the normal join go on.
-- If this file ever fails to load, the hook is not registered and the join password (set in the container) still applies.
hook.Add("CheckPassword", "SkippyAdminOnly", function(steamid64, ip, svpass, clpass, name)
    if ADMINS[steamid64] then return end
    return false, "This server is admin-only."
end)

-- Anyone already connected who is not on the list is removed (checked every 15 seconds).
timer.Create("SkippyAdminOnlyKick", 15, 0, function()
    for _, ply in ipairs(player.GetAll()) do
        if not ADMINS[ply:SteamID64()] then ply:Kick("This server is admin-only.") end
    end
end)
LUA
  if cmp -s "$tmp" "$file"; then rm -f "$tmp"; return; fi        # current: say nothing, touch nothing
  chmod 644 "$tmp"
  if mv "$tmp" "$file"; then
    chown -R "$OWNER" "$GAMEDIR/addons/skippy-admin-only" 2>/dev/null || true
    say "admin gate installed or updated (skippy-admin-only)"
  else
    rm -f "$tmp"; say "couldn't install the admin gate; the join password is the only lock"
  fi
}

admin_gate || true
refresh || true
exit 0
