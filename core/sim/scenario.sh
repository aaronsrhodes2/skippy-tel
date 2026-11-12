#!/usr/bin/env bash
# SkippyCore's simulator scenario: the pull deployer against a local "origin", stand-in models, no GPU.
#   1. deploy the baseline: the deployer deploys origin/main and the smoke test passes
#   2. push a deliberately broken commit: the deployer rolls back to the baseline and the stack is healthy again
#   3. push a fix, then set CORE_FULL=general with its files missing: the deploy is refused and the stack stays healthy
# Runs inside core/sim's container (core/sim/run.sh), or on any Linux host with Docker as root (CI runs it there with
# SIM_HOST=1). SRC = the repo (its working tree, uncommitted changes included), MODELS_ROOT = the stand-ins.
set -euo pipefail
SRC="${SRC:-/src}"
export MODELS_ROOT="${MODELS_ROOT:-/srv/models}"
WORK="${SIM_WORK:-/tmp/skippycore-sim}"
export CORE_STATE="${CORE_STATE:-$WORK/state}"
export CORE_ENV="$WORK/core.env"
REPO="$WORK/box"
fails=0

say() { printf '\n== %s\n' "$*"; }
status() { python3 -c "import json,sys; print(json.load(open('$CORE_STATE/deployer/status.json'))['$1'])"; }
expect() {   # expect <state>: the deployer's last status
  local got; got="$(status state)"
  if [[ "$got" == "$1" ]]; then echo "PASS: deployer says $got"; else
    echo "FAIL: deployer says $got, expected $1"; python3 -m json.tool "$CORE_STATE/deployer/status.json"; fails=$((fails + 1)); fi
}
deploy() { python3 "$REPO/core/deployer/deployer.py" run || true; }
smoke_ok() {
  if (cd "$REPO" && python3 core/smoke.py --wait 300); then echo "PASS: the running stack is healthy"; else
    echo "FAIL: the running stack is not healthy"; fails=$((fails + 1)); fi
}
dump() {
  say "compose logs (tail)"
  (cd "$REPO" && docker compose -p skippycore -f core/compose.core.yml logs --tail 80 --no-color) || true
  say "llama-swap's own log (upstream output included)"
  curl -fsS -m 10 http://127.0.0.1:8080/logs 2>&1 | tail -80 || true
  say "the chat slot's llama-server, started by hand for 20 s"
  local cmd
  cmd="$(python3 -c "import json; print(json.load(open('$CORE_STATE/llm/config.yaml'))['models']['chat']['cmd'].replace('\${PORT}', '9999'))")" || true
  echo "$cmd"
  (cd "$REPO" && docker compose -p skippycore -f core/compose.core.yml exec -T llm sh -c "timeout 20 $cmd" 2>&1 | tail -60) || true
}
push() {  # push <message>: commit the work tree's changes to origin/main
  git -C "$WORK/dev" add -A && git -C "$WORK/dev" commit -qm "$1" && git -C "$WORK/dev" push -q origin HEAD:main
}

say "setting up origin, the dev clone and the box's clone"
rm -rf "$WORK" && mkdir -p "$WORK/dev" "$CORE_STATE"
git config --global --add safe.directory '*'
git config --global user.email sim@skippycore && git config --global user.name sim
git init -q --bare -b main "$WORK/origin.git"
git -C "$SRC" ls-files -co --exclude-standard -z | (cd "$SRC" && tar --null -T - -cf -) | tar -xf - -C "$WORK/dev"
git -C "$WORK/dev" init -q -b main && git -C "$WORK/dev" remote add origin "$WORK/origin.git"
push "sim: baseline"
git clone -q "$WORK/origin.git" "$REPO"
install -m 0600 /dev/null "$WORK/vault.env"
cat > "$CORE_ENV" <<CONF
REPO_DIR=$REPO
CI_LOCAL=
CORE_GPU=0
CORE_FULL=
MODELS_ROOT=$MODELS_ROOT
CORE_STATE=$CORE_STATE
CORE_VAULT_ENV=$WORK/vault.env
MUSIC_ROOT=$WORK/music
SMOKE_WAIT=900
CONF
mkdir -p "$WORK/music"
python3 "$SRC/core/llm/models.py" check --root "$MODELS_ROOT" --full ""
say "devices llama.cpp can see (expect none here: a software Vulkan device would mean slow or crashing CPU runs)"
image="$(sed -n 's/^ *image: *\(ghcr.io\/mostlygeek\/llama-swap[^ ]*\).*/\1/p' "$SRC/core/compose.core.yml" | head -1)"
docker run --rm --entrypoint /app/llama-server "$image" --list-devices 2>&1 | tail -20 || true

say "1. deploy the baseline"
deploy; expect ok; [[ "$(status state)" == ok ]] || dump

say "2. a broken commit is rolled back"
sed -i 's#SHIM_NAMES=/config/shim.json#SHIM_NAMES=/config/nothing-here.json#' "$WORK/dev/core/compose.core.yml"
push "sim: break the shim's name table"
deploy; expect rolled-back
smoke_ok
deploy; expect known-bad

say "3. missing full-size files are refused, the stack stays up"
git -C "$WORK/dev" revert --no-edit HEAD >/dev/null
git -C "$WORK/dev" push -q origin HEAD:main
deploy; expect ok
sed -i 's/^CORE_FULL=.*/CORE_FULL=general/' "$CORE_ENV"
deploy; expect refused
smoke_ok

(( fails )) && dump
say "simulator: $([[ $fails == 0 ]] && echo 'ALL PASSED' || echo "$fails FAILED")"
(cd "$REPO" && docker compose -p skippycore -f core/compose.core.yml down --remove-orphans >/dev/null 2>&1) || true
exit $(( fails > 0 ))
