#!/usr/bin/env bash
# SkippyCore on its UPS (a CyberPower CP1500 AVR on USB, 0764:0501): Network UPS Tools shuts the box down cleanly and has the
# UPS cut, then restore, its outlets, so the box comes back by itself when the mains does (the Captain, 2026-10-24).
#   sudo bash ups.sh            # dry run: says what it would write, changes nothing
#   sudo bash ups.sh --apply    # write the NUT config (standalone), start the driver, server and monitor
#   sudo bash ups.sh --check    # read-only: the UPS answers, the monitor runs, the timer and delays are set. Exit 1 if not
#   ... | sudo bash ups.sh --token   # the home token on stdin (never an argument, never printed) -> /etc/skippy-core/ups.env
# What happens on a power cut:
#   * on battery: upssched starts a 3-minute timer (ONBATT_S); mains back before it ends cancels it;
#   * the timer ends, or the UPS says its battery is low (whichever comes first): upsmon shuts the box down (Docker stops its
#     containers like any clean poweroff) and leaves /etc/killpower;
#   * at the very end of the shutdown, NUT's own hook (/lib/systemd/system-shutdown/nutshutdown) tells the UPS to turn its outlets
#     off after OFFDELAY s and back on ONDELAY s after the mains returns. The box then boots on its own, which needs the BIOS
#     set to power on after AC loss (the Captain sets that once), and every service already restarts at boot.
#   * every event (on battery, low, back) is also posted to the portal's /home/ups (portal/power.py DMs the Captain); skippy-pc
#     has shut itself down by then, so skippy-core reports for itself. Back on mains for a minute: it wakes skippy-pc with a
#     Wake-on-LAN packet (skippy-pc shut down early, so if the mains returned before its own UPS ran dry it never lost power).
# The monitor's password is made once on the box (root-only, in /etc/nut/upsd.users and upsmon.conf) and never printed.
set -euo pipefail
NAME="cp1500"
ONBATT_S="${UPS_ONBATT_S:-180}"
OFFDELAY="${UPS_OFFDELAY:-60}"          # outlets off this long after the shutdown hook asks (the box is already down by then)
ONDELAY="${UPS_ONDELAY:-120}"           # back on this long after the mains returns (must be more than OFFDELAY for usbhid-ups)
MODE="${1:-dry}"
UPS_ENV=/etc/skippy-core/ups.env        # PORTAL_URL, SKIPPY_HOME_TOKEN (root and nut only), PC_MAC
PC_MAC="${PC_MAC:-F4:B5:20:54:3C:B9}"   # skippy-pc's wired adapter (Intel I219-V), to wake it

if [[ "$MODE" == "--token" ]]; then
  [[ $EUID -eq 0 ]] || { echo "run it with sudo"; exit 1; }
  IFS= read -r tok; [[ ${#tok} -ge 20 ]] || { echo "no token on stdin"; exit 1; }
  umask 077; mkdir -p /etc/skippy-core
  printf 'PORTAL_URL=%s\nSKIPPY_HOME_TOKEN=%s\nPC_MAC=%s\n' "${PORTAL_URL:-https://skippy-portal-1015050363686.us-west1.run.app}" "$tok" "$PC_MAC" > "$UPS_ENV"
  chown root:nut "$UPS_ENV" 2>/dev/null || true
  chmod 640 "$UPS_ENV"; echo "token written to $UPS_ENV (root and NUT's own user only)"; exit 0   # upsmon's hooks run as nut
fi

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "the UPS answers (upsc $NAME ups.status)"      "upsc $NAME@localhost ups.status"
  chk "nut-monitor is running"                        "systemctl is-active nut-monitor"
  chk "a ${ONBATT_S}s on-battery timer is set"        "grep -q 'START-TIMER onbatt $ONBATT_S' /etc/nut/upssched.conf"
  chk "the outlets come back ${ONDELAY}s after mains" "grep -q 'ondelay = $ONDELAY' /etc/nut/ups.conf"
  chk "events reach the portal ($UPS_ENV has the token)" "grep -q '^SKIPPY_HOME_TOKEN=.\{20,\}' $UPS_ENV"
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
NOTIFYCMD /etc/nut/notify-cmd
NOTIFYFLAG ONBATT SYSLOG+WALL+EXEC
NOTIFYFLAG ONLINE SYSLOG+WALL+EXEC
NOTIFYFLAG LOWBATT SYSLOG+WALL+EXEC
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
cat > /etc/nut/notify-cmd <<'EOF'
#!/bin/sh
# upsmon's one NOTIFYCMD: the timers (upssched), then the portal and the wake (skippy-ups-event, in the background so upsmon never waits).
/usr/sbin/upssched "$@"
/usr/local/sbin/skippy-ups-event "$NOTIFYTYPE" >/dev/null 2>&1 &
EOF
chmod 755 /etc/nut/notify-cmd
cat > /usr/local/sbin/skippy-ups-event <<'EOF'
#!/bin/sh
# One UPS event: tell the portal (POST /home/ups, the home token), and when the power is back for a minute, wake skippy-pc.
EVENT="$1"
[ -r /etc/skippy-core/ups.env ] && . /etc/skippy-core/ups.env
read_var() { upsc cp1500@localhost "$1" 2>/dev/null; }
report() {
  [ -n "$SKIPPY_HOME_TOKEN" ] || return 0
  st="$(read_var ups.status)"; ch="$(read_var battery.charge)"; rt="$(read_var battery.runtime)"
  curl -s -m 20 --retry 3 --retry-delay 5 -X POST "$PORTAL_URL/home/ups" -H "Authorization: Bearer $SKIPPY_HOME_TOKEN" \
       -H 'Content-Type: application/json' -d "{\"status\": \"$st\", \"charge\": ${ch:-0}, \"runtime_s\": ${rt:-0}, \"event\": \"$EVENT\"}" >/dev/null
}
wake_pc() {
  [ -n "$PC_MAC" ] || return 0
  python3 - "$PC_MAC" <<'PY'
import socket, sys
mac = bytes.fromhex(sys.argv[1].replace(":", "").replace("-", ""))
packet = bytes([0xFF] * 6) + mac * 16
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for _ in range(3):
    s.sendto(packet, ("255.255.255.255", 9))
PY
  logger -t ups "power back: woke skippy-pc ($PC_MAC)"
}
logger -t ups "event $EVENT"
report
if [ "$EVENT" = "ONLINE" ]; then
  sleep 60
  case "$(read_var ups.status)" in OL*) wake_pc; report ;; esac
fi
EOF
chmod 755 /usr/local/sbin/skippy-ups-event
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
