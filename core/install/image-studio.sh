#!/usr/bin/env bash
# Let skippy-pc's home reporter reach the picture studio (core/image-studio, port 7347) and nobody else.
#   sudo bash image-studio.sh            # dry run: says what it would do, changes nothing
#   sudo bash image-studio.sh --apply    # add the ufw rule: 7347/tcp in on tailscale0, from skippy-pc's tailnet address only
#   sudo bash image-studio.sh --check    # read-only: the rule is there and the studio answers /health. Exit 1 if not
# The studio uses host networking, so ufw's INPUT rules really guard it (a Docker-published port would bypass ufw).
# SSH (tailnet and LAN) is untouched.
set -euo pipefail
PORT="${STUDIO_PORT:-7347}"
FROM="${STUDIO_FROM:-100.122.71.14}"     # skippy-pc on the tailnet (tailscale status)
MODE="${1:-dry}"
RULE=(allow in on tailscale0 from "$FROM" to any port "$PORT" proto tcp comment "image-studio for skippy-pc's home reporter")

has_rule() { ufw status | grep -qE "^$PORT/tcp on tailscale0 +ALLOW +$FROM"; }
answers() { curl -fs -m 15 "http://127.0.0.1:$PORT/health" >/dev/null; }

if [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "ufw lets $FROM reach :$PORT on tailscale0"  has_rule
  chk "studio answers http://127.0.0.1:$PORT/health" answers
  exit $bad
fi

if has_rule; then echo "the rule is already there"; exit 0; fi
if [[ "$MODE" != "--apply" ]]; then echo "would run: ufw ${RULE[*]}"; exit 0; fi
ufw "${RULE[@]}"
ufw status | grep -E "^$PORT/tcp"
