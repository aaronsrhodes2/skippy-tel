#!/usr/bin/env bash
# Matter on SkippyCore: what the box needs around core/compose.matter.yml (the controller itself is a container the deployer runs).
#   sudo bash core/install/matter.sh            # dry run: says what it would do, changes nothing
#   sudo bash core/install/matter.sh --apply
#   sudo bash core/install/matter.sh --check    # read-only: is all of it live? exit 1 if not
# Idempotent. Each ufw rule carries a "matter:" comment, so `ufw status numbered` shows exactly what this added.
#
#   * Bluetooth (bluez): a brand-new Matter device is set up over Bluetooth, which is how it gets the Wi-Fi details. A device
#     already on the network (the Nest, shared from the Google Home app) needs none of it.
#   * ufw: harden.sh denies everything inbound. Matter needs the home LAN's mDNS and its device traffic (IPv4 and IPv6 link-local
#     / ULA), and skippy-pc's hub needs the controller's API, port 5580, which is opened on the TAILNET ONLY: the API has no
#     password of its own, so it is never on the LAN, and never on the Funnel.
#   * /etc/skippy-core/core.env: CORE_MATTER=1 (the deployer adds compose.matter.yml) and MATTER_ARGS (the LAN interface and the
#     Bluetooth adapter), then a repair so it starts now rather than at the next commit.
set -euo pipefail
MODE="${1:-}"
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1
LAN="${LAN:-192.168.0.0/24}"
NIC="${NIC:-eno1}"
CORE_ENV="${CORE_ENV:-/etc/skippy-core/core.env}"
REPO="${REPO:-/opt/skippy-core/repo}"
ARGS="--primary-interface $NIC --bluetooth-adapter 0"

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "IPv6 on (Matter cannot start without it)" '[[ -e /proc/net/if_inet6 && $(sysctl -n net.ipv6.conf.all.disable_ipv6) == 0 ]]'
  chk "bluetooth running"                    'systemctl is-active -q bluetooth'
  chk "ufw lets mDNS in from the LAN"        'ufw status | grep -q "matter: mDNS"'
  chk "ufw lets Matter devices reach it"     'ufw status | grep -q "matter: devices (LAN)"'
  chk "API open on the tailnet only"         'ufw status | grep "5580" | grep -q tailscale0 && ! ufw status | grep "5580" | grep -vq tailscale0'
  chk "CORE_MATTER=1 in $CORE_ENV"           'grep -qx "CORE_MATTER=1" "$CORE_ENV"'
  chk "controller answers on :5580"          'curl -fsS --max-time 5 http://127.0.0.1:5580/info | grep -q fabric_id'
  exit $bad
fi

do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }
rule() {  # rule <comment> <ufw args...>: add it unless a rule with this comment exists
  local comment="$1"; shift
  if ufw status 2>/dev/null | grep -qF "$comment"; then echo "ok: ufw $comment"; else do_ ufw "$@" comment "$comment"; fi
}
setting() {  # setting KEY VALUE: in core.env, replaced if present
  if grep -q "^$1=" "$CORE_ENV" 2>/dev/null; then
    if grep -qx "$1=$2" "$CORE_ENV"; then echo "ok: $1"; else do_ sed -i "s|^$1=.*|$1=$2|" "$CORE_ENV"; fi
  else
    if (( APPLY )); then echo "+ $1=$2 >> $CORE_ENV"; printf '%s=%s\n' "$1" "$2" >> "$CORE_ENV"; else echo "would: add $1=$2 to $CORE_ENV"; fi
  fi
}

if (( APPLY )) && [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi
# Matter's stack opens IPv6 sockets first thing: without IPv6 the controller dies at start ("Address family not supported").
if [[ ! -e /proc/net/if_inet6 || "$(sysctl -n net.ipv6.conf.all.disable_ipv6 2>/dev/null)" != 0 ]]; then
  echo "refusing: IPv6 is off on this box, and Matter cannot run without it (turn it back on, then run this again)" >&2; exit 1
fi

echo "== 1/3 Bluetooth (setting up brand-new devices)"
if dpkg-query -W -f='${Status}' bluez 2>/dev/null | grep -q "ok installed"; then echo "ok: bluez"
else DEBIAN_FRONTEND=noninteractive do_ apt-get install -y -q bluez; fi
systemctl is-active -q bluetooth 2>/dev/null && echo "ok: bluetooth running" || do_ systemctl enable --now bluetooth

echo "== 2/3 firewall: the LAN's Matter traffic in, the API on the tailnet only"
rule "matter: mDNS (LAN)"          allow proto udp from "$LAN" to any port 5353
rule "matter: mDNS (IPv6 link)"    allow proto udp from fe80::/10 to any port 5353
rule "matter: devices (LAN)"       allow proto udp from "$LAN" to any
rule "matter: devices (IPv6 link)" allow proto udp from fe80::/10 to any
rule "matter: devices (IPv6 ULA)"  allow proto udp from fd00::/8 to any
rule "matter: API for the hub (tailnet only)" allow in on tailscale0 proto tcp to any port 5580

echo "== 3/3 the deployer runs the controller"
setting CORE_MATTER 1
setting MATTER_ARGS "\"$ARGS\""
if (( APPLY )); then
  do_ python3 "$REPO/core/deployer/deployer.py" repair
  echo "then: sudo bash $REPO/core/install/matter.sh --check"
else
  echo "would: python3 $REPO/core/deployer/deployer.py repair   (starts it now)"
fi
