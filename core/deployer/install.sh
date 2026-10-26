#!/usr/bin/env bash
# Install SkippyCore's pull deployer on the box (Ubuntu 26.04, Docker and git already there). Idempotent.
#   sudo bash core/deployer/install.sh            # dry run: says what it would do
#   sudo bash core/deployer/install.sh --apply
# Then check /etc/skippy-core/core.env (CI_LOCAL points at core/ci's results: install core/ci too) and put the vault in
# this repo's contents and checks) and the vault in /etc/skippy-core/vault.env; both stay root-only (0600).
set -euo pipefail
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
REPO_URL="${REPO_URL:-/mnt/pc/trunk/git/skippy-tel.git}"      # the house's own git, through the data trunk (docs/data-trunk.md)
REPO_DIR="${REPO_DIR:-/opt/skippy-core/repo}"
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }

[[ -d "$REPO_DIR/.git" ]] || do_ git clone --quiet "$REPO_URL" "$REPO_DIR"
do_ install -d -m 0700 /etc/skippy-core
do_ install -d -m 0755 /var/lib/skippy-core /srv/models
if [[ ! -f /etc/skippy-core/core.env ]]; then
  if (( APPLY )); then
    umask 077
    cat > /etc/skippy-core/core.env <<CONF
# SkippyCore deployer settings (core/deployer/deployer.py). Onlining a big model = add its slot to CORE_FULL.
REPO_DIR=$REPO_DIR
CI_LOCAL=/var/lib/skippy-core/ci
CORE_FULL=
MODELS_ROOT=/srv/models
CORE_STATE=/var/lib/skippy-core
CORE_VAULT_ENV=/etc/skippy-core/vault.env
MUSIC_ROOT=/srv/music
CORE_GPU=1
CONF
    echo "+ wrote /etc/skippy-core/core.env (check CI_LOCAL)"
  else
    echo "would: write /etc/skippy-core/core.env (template)"
  fi
fi
[[ -f /etc/skippy-core/vault.env ]] || do_ install -m 0600 /dev/null /etc/skippy-core/vault.env
do_ install -m 0644 "$REPO_DIR/core/deployer/skippy-core-deployer.service" /etc/systemd/system/
do_ install -m 0644 "$REPO_DIR/core/deployer/skippy-core-deployer.timer" /etc/systemd/system/
# the doctor runs its own copy of the deployer, so it works whatever the checkout holds (a box set up by hand)
do_ install -d -m 0755 /opt/skippy-core/doctor
do_ install -m 0755 "$REPO_DIR/core/deployer/deployer.py" /opt/skippy-core/doctor/deployer.py
do_ install -m 0644 "$REPO_DIR/core/deployer/skippy-core-doctor.service" /etc/systemd/system/
do_ install -m 0644 "$REPO_DIR/core/deployer/skippy-core-doctor.timer" /etc/systemd/system/
do_ systemctl daemon-reload
do_ systemctl enable --now skippy-core-deployer.timer
do_ systemctl enable --now skippy-core-doctor.timer
(( APPLY )) || echo "dry run: nothing changed. Re-run with --apply."
