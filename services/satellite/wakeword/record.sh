#!/usr/bin/env bash
# Record the Captain through a pod's own microphone chain: stops the hub (by PID, it holds the pod), records, starts the hub again.
#   bash services/satellite/wakeword/record.sh positive 50 [room]
#   bash services/satellite/wakeword/record.sh negative 30 [room]
set -uo pipefail
SAT="$(cd "$(dirname "$0")/.." && pwd)"
kind="${1:?positive|negative}"; count="${2:-30}"; room="${3:-}"
pids=$(powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'satellite.hub\.py' -and \$_.CommandLine -notmatch '--record' } | ForEach-Object { \$_.ProcessId }" | tr -d '\r')
for p in $pids; do powershell.exe -NoProfile -Command "Stop-Process -Id $p -Force" ; done
sleep 3
cd "$SAT" && ./.venv/Scripts/python -u hub.py --record "$kind" "$count" $room
status=$?
schtasks.exe //Run //TN SkippyHub
exit $status
