#!/usr/bin/env bash
# SkippyCore as a headless, remote-only server: it recovers by itself and always has a second way in.
# Run after firstboot.sh, once skippy-pc's key is in ~aaron/.ssh/authorized_keys (keys-only SSH needs it).
#   sudo bash harden.sh            # dry run: says what it would do, changes nothing
#   sudo bash harden.sh --apply
# Idempotent. Never touches disks, partitions or Tailscale's login. Takes effect fully after one reboot.
# Each change is its own drop-in file named skippy-core-*, so undoing one means deleting one file.
set -euo pipefail
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
LAN="${LAN:-192.168.0.0/24}"            # the second door: SSH from the home network
NIC="${NIC:-eno1}"
TZ_NAME="${TZ_NAME:-America/Los_Angeles}"
REBOOT_AT="${REBOOT_AT:-04:30}"         # when a security update that needs a reboot gets one
PURGE="open-vm-tools modemmanager multipath-tools open-iscsi ubuntu-fan cloud-init apport snapd"

if [[ "${1:-}" == "--check" ]]; then
  # Read-only: is every piece of the hardening live right now? Exit 1 if not (the health checks use this too).
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "boots to text (multi-user)"           '[[ $(systemctl get-default) == multi-user.target ]]'
  chk "hardware watchdog armed"              '[[ $(cat /sys/class/watchdog/watchdog0/state) == active ]]'
  chk "kernel panic reboots after 10 s"      '[[ $(sysctl -n kernel.panic) == 10 ]]'
  chk "fsck repairs by itself"               'grep -q fsck.repair=yes /proc/cmdline'
  chk "GPU can map ~124 GB (GTT)"            '(( $(cat /sys/class/drm/card*/device/mem_info_gtt_total | head -1) > 130000000000 ))'
  chk "Wake-on-LAN on $NIC"                  'ethtool "$NIC" | grep "^[[:space:]]*Wake-on: g"'
  chk "SSH keys only"                        'sshd -T | grep -x "passwordauthentication no"'
  chk "SSH for aaron only"                   'sshd -T | grep -x "allowusers aaron"'
  chk "firewall active"                      'ufw status | grep "^Status: active"'
  chk "services up (ssh tailscale docker)"   'systemctl is-active -q ssh tailscaled docker'
  chk "auto security updates + reboot"       'apt-config dump | grep "Automatic-Reboot \"true\""'
  chk "no failed units"                      '[[ $(systemctl --failed --no-legend | wc -l) == 0 ]]'
  exit $bad
fi

do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }
put() {  # put <path> <mode>: write stdin to path (only when the content differs)
  local path="$1" mode="$2" new; new="$(cat)"
  if [[ -f "$path" && "$(cat "$path")" == "$new" ]]; then echo "ok: $path"; return 1; fi
  if (( APPLY )); then printf '%s\n' "$new" | install -D -m "$mode" /dev/stdin "$path"; echo "+ wrote $path"
  else echo "would: write $path"; fi
}
step() { echo; echo "== $*"; }

if (( APPLY )) && [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi
if (( APPLY )) && [[ ! -s /home/aaron/.ssh/authorized_keys ]]; then
  echo "refusing: /home/aaron/.ssh/authorized_keys is empty, and keys-only SSH would lock everyone out" >&2; exit 1
fi

step "1/7 boot to text, local time"
[[ "$(systemctl get-default)" == multi-user.target ]] && echo "ok: multi-user.target" || do_ systemctl set-default multi-user.target
[[ "$(timedatectl show -p Timezone --value 2>/dev/null)" == "$TZ_NAME" ]] && echo "ok: $TZ_NAME" || do_ timedatectl set-timezone "$TZ_NAME"

step "2/7 recover by itself: watchdog, panic reboot, no boot-menu wait, fsck repairs"
put /etc/systemd/system/skippy-core-watchdog.service 0644 <<'UNIT' && do_ systemctl daemon-reload && do_ systemctl enable skippy-core-watchdog.service || true
[Unit]
Description=SkippyCore: load the AMD chip watchdog (Ubuntu deny-lists its autoload; systemd then feeds it)
DefaultDependencies=no
After=systemd-modules-load.service
Before=sysinit.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/modprobe sp5100_tco
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
UNIT
put /etc/systemd/system.conf.d/10-skippy-core-watchdog.conf 0644 <<'CONF' && do_ systemctl daemon-reexec || true
[Manager]
# SkippyCore: reboot if the kernel or systemd stops for 60 s; force a reboot that hangs for 5 min
RuntimeWatchdogSec=60s
RebootWatchdogSec=5min
CONF
put /etc/sysctl.d/90-skippy-core.conf 0644 <<'CONF' && do_ sysctl -q -p /etc/sysctl.d/90-skippy-core.conf || true
# SkippyCore: a panicked kernel reboots itself after 10 s
kernel.panic = 10
CONF
put /etc/default/grub.d/91-skippy-core-headless.cfg 0644 <<'CONF' && do_ update-grub || true
# SkippyCore headless: no menu wait after a failed boot; fsck fixes errors by itself
GRUB_RECORDFAIL_TIMEOUT=5
GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT fsck.repair=yes"
CONF

step "3/7 Wake-on-LAN on $NIC (netplan; applied at the next boot, no network restart over SSH)"
NP=/etc/netplan/00-installer-config.yaml
if grep -q 'wakeonlan: true' "$NP" 2>/dev/null; then echo "ok: $NP"
else do_ sed -i "s/^      set-name: $NIC\$/      set-name: $NIC\n      wakeonlan: true/" "$NP"; do_ netplan generate; fi
do_ ethtool -s "$NIC" wol g

step "4/7 SSH: keys only, aaron only"
put /etc/ssh/sshd_config.d/00-skippy-keys-only.conf 0644 <<'CONF' && RELOAD=1 || true
# SkippyCore: keys only (first match wins, so this file sorts first)
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
CONF
put /etc/ssh/sshd_config.d/01-skippy-hardening.conf 0644 <<'CONF' && RELOAD=1 || true
# SkippyCore hardening (sorts before 50-cloud-init.conf; first match wins)
AllowUsers aaron
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
PermitEmptyPasswords no
ClientAliveInterval 60
ClientAliveCountMax 3
CONF
if [[ "${RELOAD:-0}" == 1 ]]; then do_ sshd -t; do_ systemctl reload ssh; fi
do_ tailscale set --auto-update

step "5/7 firewall: deny in, except SSH (tailnet + $LAN) and Tailscale"
if ufw status 2>/dev/null | grep "^Status: active" >/dev/null && ufw status | grep "ssh from the home LAN" >/dev/null; then
  echo "ok: ufw active with the SkippyCore rules"
else
  # dead-man switch: if this cuts off SSH, the firewall turns itself off again in 5 minutes
  do_ systemd-run --quiet --unit=skippy-ufw-deadman --on-active=5min /usr/sbin/ufw disable
  do_ ufw default deny incoming
  do_ ufw default allow outgoing
  do_ ufw allow in on tailscale0 to any port 22 proto tcp comment "ssh over the tailnet"
  do_ ufw allow from "$LAN" to any port 22 proto tcp comment "ssh from the home LAN (second door)"
  do_ ufw allow 41641/udp comment "tailscale direct connections"
  do_ ufw --force enable
  echo "NOW: from skippy-pc, check BOTH doors (ssh aaron@skippy-core, ssh aaron@<LAN ip>), then:"
  echo "     sudo systemctl stop skippy-ufw-deadman.timer   (else the firewall switches off in 5 minutes)"
fi

step "6/7 remove what a headless AI box doesn't use; add disk health and sensors"
installed=$( (dpkg-query -W -f='${binary:Package} ${Status}\n' $PURGE 2>/dev/null || true) | awk '/ installed$/{print $1}' | tr '\n' ' ')
if [[ -n "${installed// }" ]]; then
  # Purging these also removes the ubuntu-server metapackages: keep what they pulled in, so autoremove can't take it.
  keep=$(apt-cache depends --installed ubuntu-server ubuntu-server-minimal 2>/dev/null \
         | awk '/(Depends|Recommends): [a-z]/{print $2}' | grep -vxE "$(echo $PURGE | tr ' ' '|')|ubuntu-server.*" | sort -u)
  do_ apt-mark manual $keep openssh-server tailscale docker.io docker-compose-v2 git python3 curl gdisk
  do_ snap remove --purge hwctl
  DEBIAN_FRONTEND=noninteractive do_ apt-get purge -y -q $installed
  do_ rm -rf /etc/cloud /var/lib/cloud /snap /var/snap /var/lib/snapd
  DEBIAN_FRONTEND=noninteractive do_ apt-get autoremove --purge -y -q
else echo "ok: nothing to purge"; fi
DEBIAN_FRONTEND=noninteractive do_ apt-get install -y -q smartmontools lm-sensors
do_ systemctl enable --now smartmontools

step "7/7 security updates by themselves; a reboot they need at $REBOOT_AT"
put /etc/apt/apt.conf.d/52skippy-core-unattended 0644 <<CONF || true
// SkippyCore: security updates install by themselves; a reboot they need happens at $REBOOT_AT ($TZ_NAME)
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "$REBOOT_AT";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-New-Unused-Dependencies "true";
CONF

echo
(( APPLY )) && echo "done. Reboot once, then: sudo bash harden.sh --check" || echo "dry run: nothing changed. Re-run with --apply."
