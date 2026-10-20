#!/usr/bin/env bash
# SkippyCore's first boot on a fresh Ubuntu 26.04 server: remote access first, then the tools, then the repo.
#   sudo bash firstboot.sh            # dry run: says what it would do, changes nothing
#   sudo bash firstboot.sh --apply
# Order (core/install/README.md): SSH, Tailscale, Docker, clone, models check. Idempotent: a re-run skips what is done.
# It never touches a disk or partition, and never edits the bootloader (the kernel options are a separate, manual step).
set -euo pipefail
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
REPO_URL="${REPO_URL:-https://github.com/aaronsrhodes2/skippy-tel.git}"
REPO_DIR="${REPO_DIR:-/opt/skippy-core/repo}"
MODELS_ROOT="${MODELS_ROOT:-/srv/models}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="${BUNDLE:-$HERE/skippy-tel.bundle}"          # the USB stick carries one, for a box without GitHub yet
OWNER="${SUDO_USER:-}"

do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }
step() { echo; echo "== $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

if (( APPLY )) && [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi
. /etc/os-release 2>/dev/null || true
[[ "${ID:-}" == "ubuntu" ]] || echo "warning: this is ${PRETTY_NAME:-not Ubuntu}; the script is written for Ubuntu 26.04"

step "1/5 SSH"
do_ apt-get update
have sshd || do_ apt-get install -y openssh-server
do_ systemctl enable --now ssh

step "2/5 Tailscale"
if ! have tailscale; then
  # Tailscale's own installer: adds its signed apt repository for this Ubuntu release, then installs.
  if (( APPLY )); then echo "+ curl -fsSL https://tailscale.com/install.sh | sh"; curl -fsSL https://tailscale.com/install.sh | sh
  else echo "would: curl -fsSL https://tailscale.com/install.sh | sh"; fi
fi
do_ systemctl enable --now tailscaled
if (( APPLY )) && tailscale status >/dev/null 2>&1; then
  echo "tailscale is already signed in: $(tailscale ip -4 2>/dev/null | head -1)"
else
  echo "Tailscale prints a sign-in link next: open it on your phone and approve the box."
  do_ tailscale up
fi

step "3/5 Docker, git, python3"
do_ apt-get install -y docker.io docker-compose-v2 git python3 curl ca-certificates
do_ systemctl enable --now docker
[[ -n "$OWNER" && "$OWNER" != root ]] && do_ usermod -aG docker "$OWNER"

step "4/5 the repo -> $REPO_DIR"
if [[ -d "$REPO_DIR/.git" ]]; then
  echo "already cloned: $(git -C "$REPO_DIR" log --oneline -1 2>/dev/null || echo '?')"
else
  # The repo is private: a fresh box has no GitHub credentials, so the stick's bundle comes first. origin is then
  # pointed at GitHub; the deployer's token (core/README.md, deployer step) is what lets later fetches through.
  do_ install -d -m 0755 "$(dirname "$REPO_DIR")"
  if [[ -f "$BUNDLE" ]]; then
    do_ git clone --quiet -b main "$BUNDLE" "$REPO_DIR"
    do_ git -C "$REPO_DIR" remote set-url origin "$REPO_URL"
  else
    echo "no bundle at $BUNDLE: cloning from GitHub (private repo: needs credentials)"
    do_ git clone --quiet "$REPO_URL" "$REPO_DIR"
  fi
fi

step "5/5 models check (MODELS_ROOT=$MODELS_ROOT)"
do_ install -d -m 0755 "$MODELS_ROOT"
if (( APPLY )); then
  # Missing files are expected on day one (the models come from skippy-pc's D:\models later): report, don't fail.
  MODELS_ROOT="$MODELS_ROOT" python3 "$REPO_DIR/core/llm/models.py" check || echo "models check: not ready yet (expected before the copy)"
else
  echo "would: MODELS_ROOT=$MODELS_ROOT python3 $REPO_DIR/core/llm/models.py check"
fi

echo
if (( APPLY )); then
  echo "done. Next: core/README.md 'Day one on the box' from step 3 (kernel options), then the deployer."
  [[ -n "$OWNER" ]] && echo "log out and back in once so '$OWNER' can use docker without sudo."
else
  echo "dry run: nothing changed. Re-run with --apply."
fi
