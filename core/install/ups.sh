#!/usr/bin/env bash
# SkippyCore on its UPS (a CyberPower CP1500 AVR on USB, 0764:0501): Network UPS Tools shuts the box down cleanly and has the
# UPS cut, then restore, its outlets, so the box comes back by itself when the mains does (the Captain, 2026-10-24).
#   sudo bash ups.sh            # dry run: says what it would write, changes nothing
#   sudo bash ups.sh --apply    # write the NUT config (standalone), start the driver, server and monitor
#   sudo bash ups.sh --check    # read-only: the UPS answers, the monitor runs, the timer and delays are set. Exit 1 if not
# What happens on a power cut:
#   * on battery: upssched starts a 3-minute timer (ONBATT_S); mains back before it ends cancels it;
#   * the timer ends, or the UPS says its battery is low (whichever comes first): upsmon shuts the box down (Docker stops its
#     containers like any clean poweroff) and leaves /etc/killpower;
#   * at the very end of the shutdown, NUT's own hook (/lib/systemd/system-shutdown/nutshutdown) tells the UPS to turn its outlets
#     off after OFFDELAY s and back on ONDELAY s after the mains returns. The box then boots on its own, which needs the BIOS
#     set to power on after AC loss (the Captain sets that once), and every service already restarts at boot.
# The monitor's password is made once on the box (root-only, in /etc/nut/upsd.users and upsmon.conf) and never printed.
set -euo pipefail
NAME="cp1500"
ONBATT_S="${UPS_ONBATT_S:-180}"
OFFDELAY="${UPS_OFFDELAY:-60}"          # outlets off this long after the shutdown hook asks (the box is already down by then)
ONDELAY="${UPS_ONDELAY:-120}"           # back on this long after the mains returns (must be more than OFFDELAY for usbhid-ups)
MODE="${1:-dry}"

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "the UPS answers (upsc $NAME ups.status)"      "upsc $NAME@localhost ups.status"
  chk "nut-monitor is running"                        "systemctl is-active nut-monitor"
  chk "a ${ONBATT_S}s on-battery timer is set"        "grep -q 'START-TIMER onbatt $ONBATT_S' /etc/nut/upssched.conf"
  chk "the outlets come back ${ONDELAY}s after mains" "grep -q 'ondelay = $ONDELAY' /etc/nut/ups.conf"
  upsc "$NAME@localhost" 2>/dev/null | grep -E '^(ups.status|battery.charge|battery.runtime|ups.load|ups.delay.(shutdown|start)):' || true
  exit $bad
fi

[[ $EUID -eq 0 ]] || { echo "run it with sudo"; exit 1; }
if [[ "$MODE" != "--apply" ]]; then
  echo "dry run: would write /etc/nut/{nut.conf,ups.conf,upsd.conf,upsd.users,upsmon.conf,upssched.conf,upssched-cmd}"
  echo "  UPS '$NAME' via usbhid-ups (0764:0501), shut down after ${ONBATT_S}s on battery or at low battery,"
  echo "  outlets off ${OFFDELAY}s after the shutdown, back on ${ONDELAY}s after the mains returns. Run with --apply."
  exit 0
fi

command -v upsmon >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q nut
pw_file=/etc/nut/.upsmon-password
[[ -s "$pw_file" ]] || { umask 077; head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > "$pw_file"; }
PW="$(cat "$pw_file")"

cat > /etc/nut/nut.conf <<EOF
MODE=standalone
EOF
cat > /etc/nut/ups.conf <<EOF
maxretry = 3
[$NAME]
  driver = usbhid-ups
  port = auto
  vendorid = 0764
  productid = 0501
  desc = "CyberPower CP1500 AVR: skippy-core"
  offdelay = $OFFDELAY
  ondelay = $ONDELAY
EOF
cat > /etc/nut/upsd.conf <<EOF
LISTEN 127.0.0.1 3493
EOF
cat > /etc/nut/upsd.users <<EOF
[upsmon]
  password = $PW
  actions = SET
  upsmon primary
EOF
cat > /etc/nut/upsmon.conf <<EOF
MONITOR $NAME@localhost 1 upsmon $PW primary
MINSUPPLIES 1
SHUTDOWNCMD "/sbin/shutdown -h +0 'UPS: on battery too long or battery low'"
POWERDOWNFLAG /etc/killpower
NOTIFYCMD /usr/sbin/upssched
NOTIFYFLAG ONBATT SYSLOG+WALL+EXEC
NOTIFYFLAG ONLINE SYSLOG+WALL+EXEC
NOTIFYFLAG LOWBATT SYSLOG+WALL
NOTIFYFLAG FSD SYSLOG+WALL
NOTIFYFLAG COMMBAD SYSLOG
NOTIFYFLAG COMMOK SYSLOG
FINALDELAY 5
EOF
cat > /etc/nut/upssched.conf <<EOF
CMDSCRIPT /etc/nut/upssched-cmd
PIPEFN /run/nut/upssched.pipe
LOCKFN /run/nut/upssched.lock
AT ONBATT * START-TIMER onbatt $ONBATT_S
AT ONLINE * CANCEL-TIMER onbatt
EOF
cat > /etc/nut/upssched-cmd <<'EOF'
#!/bin/sh
# Called by upssched when a timer ends: on battery for the whole wait, so shut down now (upsmon sets the killpower flag).
case "$1" in
  onbatt) logger -t ups "on battery past the timer: shutting down"; /sbin/upsmon -c fsd ;;
  *) logger -t ups "upssched: unknown event $1" ;;
esac
EOF
chmod 755 /etc/nut/upssched-cmd
chown root:nut /etc/nut/*.conf /etc/nut/upsd.users
chmod 640 /etc/nut/*.conf /etc/nut/upsd.users

systemctl enable --now nut-driver-enumerator.service nut-server.service nut-monitor.service >/dev/null 2>&1 || true
systemctl restart nut-driver-enumerator.service 2>/dev/null || true
systemctl restart "nut-driver@$NAME.service" 2>/dev/null || upsdrvsvcctl start "$NAME"
systemctl restart nut-server.service nut-monitor.service
sleep 3
upsrw -s ups.delay.shutdown="$OFFDELAY" -u upsmon -p "$PW" "$NAME@localhost" >/dev/null 2>&1 || true
upsrw -s ups.delay.start="$ONDELAY" -u upsmon -p "$PW" "$NAME@localhost" >/dev/null 2>&1 || true
echo "applied: $NAME on usbhid-ups, ${ONBATT_S}s on-battery timer, outlets off ${OFFDELAY}s / on ${ONDELAY}s"
exec bash "$0" --check
