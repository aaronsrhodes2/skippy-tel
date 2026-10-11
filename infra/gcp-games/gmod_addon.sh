#!/bin/bash
# Garry's Mod: put the Mentat stage addon (the public repo aaronsrhodes2/sigma-ground, folder stage/gmod/addon) at
# <data>/garrysmod/addons/mentat-stage, fresh from GitHub, before srcds starts.
#
# Runs on the games VM, as root, from two places (shipped in instance metadata as `gmod-addon`, like valheim_seed.py):
#   * startup.sh, at every boot, with the game containers down;
#   * the ops agent's `restart-game garrysmod`, with the container stopped (ops_agent.py fetches this file from metadata first,
#     so a deploy reaches a VM that booted before it existed).
#
# It NEVER stops the server from starting: every failure is reported on one line and the script still exits 0. A fetch that fails
# (GitHub down, no network, git missing and not installable) leaves the previous copy in place, or no addon on a first boot.
#
# How: a shallow, sparse, blob-less clone of $REPO at $REF (one commit, its trees, and only the files under $SUB: a few MB) into a
# scratch folder on the data disk. The addon's tree id is compared with the one recorded in the installed copy (.source); when they
# match NOTHING is touched, so file times stay put and game_backup.py (which fingerprints size + mtime, and backs up
# garrysmod/addons) doesn't see a change, and an archive, at every boot. Otherwise the new copy is built beside the old one and
# swapped in with two renames. The clone is thrown away: no .git is ever left in addons/.
#
# Which version: the portal setting GARRYSMOD_ADDON_REF (read from the games .env), or GMOD_ADDON_REF in the environment.
#   * blank: the tip of `master`. This is the development loop: push, restart the game, test. It also means whoever can push to
#     that repo can run Lua on this server and send Lua to every player who joins, so use it while the Captain is developing.
#   * a full 40-character commit id: exactly that commit, fetched by id. A branch can be moved and a commit id cannot, so a
#     compromised repo or account cannot change what runs here; only the Captain, behind the portal login, can change the pin.
#   * a branch or tag name: as the blank case, for that branch or tag.
# Other settings (all optional, for tests and for pointing at a fork): GMOD_ADDON_REPO, GMOD_ADDON_PATH, GMOD_ADDON_NAME,
# GMOD_ADDON_OWNER, GAMES_ENV, DATA.
REPO=${GMOD_ADDON_REPO:-https://github.com/aaronsrhodes2/sigma-ground.git}
GAMES_ENV=${GAMES_ENV:-/srv/games/.env}
REF=${GMOD_ADDON_REF:-$(sed -n 's/^GARRYSMOD_ADDON_REF=//p' "$GAMES_ENV" 2>/dev/null | tail -n 1 | tr -d '\r"'"'"' ')}
REF=${REF:-master}
SUB=${GMOD_ADDON_PATH:-stage/gmod/addon}
NAME=${GMOD_ADDON_NAME:-mentat-stage}
DATA=${DATA:-/srv/games/data}
OWNER=${GMOD_ADDON_OWNER:-1000:1000}        # the uid:gid srcds runs as in the image (PUID/PGID in games/docker-compose.yml)
GAMEDIR=$DATA/garrysmod
DEST=$GAMEDIR/addons/$NAME
export HOME=${HOME:-/root} GIT_TERMINAL_PROMPT=0 GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=30

say() { echo "gmod addon: $*"; }

refresh() {
  mkdir -p "$GAMEDIR/addons" || { say "can't make $GAMEDIR/addons; nothing done"; return; }
  rm -rf "$GAMEDIR"/.addon-fetch.*                              # a crashed run's leftovers
  local have
  have=$(cut -d' ' -f2 "$DEST/.source" 2>/dev/null | cut -c1-7); have=${have:-none}

  if ! printf '%s' "$REF" | grep -Eq '^([0-9a-f]{40}|[A-Za-z0-9][A-Za-z0-9._/-]{0,79})$'; then
    say "the version '$(printf '%s' "$REF" | cut -c1-40)' is not a commit id or a branch name; keeping the addon as it is ($have)"
    return
  fi

  if ! command -v git >/dev/null 2>&1; then
    say "git isn't installed on this VM; installing it"
    export DEBIAN_FRONTEND=noninteractive
    { timeout 240 apt-get install -y -qq --no-install-recommends git || { timeout 120 apt-get update -qq && timeout 240 apt-get install -y -qq --no-install-recommends git; }; } >/dev/null 2>&1 \
      || { say "couldn't install git; keeping the addon as it is ($have)"; return; }
  fi

  local work src out tree commit
  work=$(mktemp -d "$GAMEDIR/.addon-fetch.XXXXXX") || { say "can't make a scratch folder; keeping the addon as it is ($have)"; return; }
  src=$work/src
  # core.symlinks=false: this runs as root on content from the internet, so a symlink in the repo arrives as a plain file.
  if printf '%s' "$REF" | grep -Eq '^[0-9a-f]{40}$'; then
    # pinned: fetch exactly this commit, in the same shallow, sparse, blob-less shape as the branch case
    if ! out=$({ git init --quiet "$src" && git -C "$src" config core.symlinks false && git -C "$src" remote add origin "$REPO" \
                 && git -C "$src" sparse-checkout set "$SUB" \
                 && timeout 90 git -C "$src" fetch --quiet --depth 1 --filter=blob:none origin "$REF" \
                 && git -C "$src" checkout --quiet FETCH_HEAD; } 2>&1); then
      say "couldn't fetch $REPO at the pinned commit ${REF:0:7}: $(echo "$out" | tail -n 2 | tr '\n' ' ' | cut -c1-300); keeping the addon as it is ($have)"
      rm -rf "$work"; return
    fi
  elif ! out=$(timeout 90 git clone --quiet -c core.symlinks=false --depth 1 --branch "$REF" --filter=blob:none --sparse "$REPO" "$src" 2>&1 \
             && timeout 90 git -C "$src" sparse-checkout set "$SUB" 2>&1); then
    say "couldn't fetch $REPO ($REF): $(echo "$out" | tail -n 2 | tr '\n' ' ' | cut -c1-300); keeping the addon as it is ($have)"
    rm -rf "$work"; return
  fi
  if [ ! -d "$src/$SUB" ] || [ -z "$(find "$src/$SUB" -type f -name '*.lua' -print -quit)" ]; then
    say "$SUB isn't in $REPO at $REF, or has no Lua in it; keeping the addon as it is ($have)"
    rm -rf "$work"; return
  fi
  tree=$(git -C "$src" rev-parse "HEAD:$SUB") && commit=$(git -C "$src" rev-parse HEAD) \
    || { say "couldn't read the commit; keeping the addon as it is ($have)"; rm -rf "$work"; return; }

  if [ -f "$DEST/.source" ] && [ "$(cut -d' ' -f1 "$DEST/.source")" = "$tree" ]; then
    say "$NAME is already at ${commit:0:7}; nothing to do"
    rm -rf "$work"; return
  fi
  if ! { cp -a "$src/$SUB" "$work/new" && printf '%s %s\n' "$tree" "$commit" > "$work/new/.source"; }; then
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

refresh || true
exit 0
