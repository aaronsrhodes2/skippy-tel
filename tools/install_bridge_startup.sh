#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyBridge": it starts tools/bridge_supervisor.sh, which keeps the
# solid-gold bridge running, every time the Captain signs in to skippy-pc.
#
#   bash tools/install_bridge_startup.sh                  DRY RUN: shows the task and the exact schtasks command. Changes nothing.
#   bash tools/install_bridge_startup.sh --apply          registers it (run again to update it: the task is replaced)
#   bash tools/install_bridge_startup.sh --remove         DRY RUN of the removal
#   bash tools/install_bridge_startup.sh --remove --apply stops and deletes the task
#   bash tools/install_bridge_startup.sh --status         asks Windows about the task (read-only, runs at once)
#
# Why the task is defined by an XML file and not by `schtasks /Create /SC ONLOGON /TR ...`: that short form cannot say
# "restart if it fails", and it gives every task a 72-hour execution limit, so Windows would kill the supervisor after
# three days. The XML says: no time limit, restart on failure, don't stop on battery, one copy at a time, as the Captain
# (his `claude` login and ~/.claude live in his account; SYSTEM would have none), at logon.
#
# It can only start after somebody has signed in: the bridge needs the Captain's own session. After a power cut the PC
# sits at the sign-in screen until he signs in (or Windows is set to sign in by itself: docs/portal/remote-recovery.md).
# If Windows answers "Access is denied", run this again from a terminal opened as Administrator.
#
# Git Bash rewrites arguments that look like Unix paths ("/Create" becomes "C:/Program Files/Git/Create"): every
# schtasks call below sets MSYS_NO_PATHCONV=1, and the printed commands carry it, so pasting them works too.
#
# Settings (environment, for tests): SCHTASKS (default schtasks.exe), BASH_EXE, BRIDGE_TASK_USER (DOMAIN\name).

TASK=${STARTUP_TASK:-SkippyBridge}            # SkippyStarter when run through install_starter_startup.sh

# ── pure pieces (the tests source this file and call them) ───────────────────

xml_escape() {                  # & < > " ' for element text (sed: bash 5.2's "&" in a replacement means "the match")
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g' -e "s/'/\&apos;/g"
}

shquote() {                     # the word as a bash command line needs it: bare when plain, else 'single quoted'
  local q="'"
  case "$1" in
    ''|*[!A-Za-z0-9_./:=,@%+-]*) printf "'%s'" "${1//$q/$q\\$q$q}" ;;
    *) printf '%s' "$1" ;;
  esac
}

to_win() {                      # a Windows path (C:\...) when cygpath exists, else the path unchanged
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

to_mixed() {                    # C:/... : a Windows path with forward slashes, which bash takes as it is
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

find_bash_exe() {               # the Windows bash that runs the supervisor: the Git for Windows launcher, which sets up PATH
  if [ -n "${BASH_EXE:-}" ]; then printf '%s' "$BASH_EXE"; return 0; fi
  if command -v cygpath >/dev/null 2>&1; then
    local root_w wrapper
    root_w=$(cygpath -w /)                  # the install folder, with a trailing backslash
    wrapper="${root_w%\\}\\bin\\bash.exe"    # <install>\bin\bash.exe: the launcher (usr\bin\bash.exe alone lacks the PATH setup)
    if [ -f "$(cygpath -u "$wrapper")" ]; then printf '%s' "$wrapper"; return 0; fi
    cygpath -w "$(command -v bash)"
    return 0
  fi
  command -v bash
}

pick_python() {                 # python, py, python3 (python3 last: on Windows it can be the Microsoft Store stub)
  local p
  if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return 0; fi
  for p in python py python3; do
    if command -v "$p" >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  return 1
}

write_utf16() {                 # stdin -> <file> as UTF-16 with a BOM, which is what the XML's own header promises schtasks
  local py
  py=$(pick_python) || { echo "no python on PATH (tried python, py, python3)" >&2; return 1; }
  "$py" -c 'import sys; open(sys.argv[1], "w", encoding="utf-16").write(sys.stdin.read())' "$(to_win "$1")"
}

# The trigger: at logon (the default), or every day at STARTUP_AT (HH:MM, local time) when set: the midnight model download.
trigger_xml() {
  local user_xml=$1
  if [ -n "${STARTUP_AT:-}" ]; then
    printf '%s\n' "    <CalendarTrigger>" \
      "      <StartBoundary>2026-01-01T${STARTUP_AT}:00</StartBoundary>" \
      "      <Enabled>true</Enabled>" \
      "      <ScheduleByDay>" \
      "        <DaysInterval>1</DaysInterval>" \
      "      </ScheduleByDay>" \
      "    </CalendarTrigger>"
  else
    printf '%s\n' "    <LogonTrigger>" \
      "      <Enabled>true</Enabled>" \
      "      $user_xml" \
      "      <Delay>PT30S</Delay>" \
      "    </LogonTrigger>"
  fi
}

restart_xml() {                 # restart on failure (supervisors), unless STARTUP_RESTART=0 (a nightly job just tries again tomorrow)
  if [ "${STARTUP_RESTART:-1}" = 0 ]; then return 0; fi
  printf '%s\n' "    <RestartOnFailure>" \
    "      <Interval>PT1M</Interval>" \
    "      <Count>999</Count>" \
    "    </RestartOnFailure>"
}

# task_xml <bash exe> <supervisor script, forward slashes> <working directory> <user or empty>
task_xml() {
  local bash_exe=$1 script=$2 workdir=$3 user=$4 user_xml=""
  local desc="${STARTUP_DESC:-Keeps the solid-gold bridge (the hands of the private Skippy on skippy-pc) running: starts tools/bridge_supervisor.sh at logon. Log: state/bridge-supervisor.log. Made by tools/install_bridge_startup.sh.}"
  if [ -n "$user" ]; then user_xml="<UserId>$(xml_escape "$user")</UserId>"; fi
  cat <<EOF
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$(xml_escape "$desc")</Description>
    <URI>\\$TASK</URI>
  </RegistrationInfo>
  <Triggers>
$(trigger_xml "$user_xml")
  </Triggers>
  <Principals>
    <Principal id="Author">
      $user_xml
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>$(if [ "${STARTUP_WAKE:-0}" = 1 ]; then echo true; else echo false; fi)</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
$(restart_xml)
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$(xml_escape "$bash_exe")</Command>
      <Arguments>-l "$(xml_escape "$script")"</Arguments>
      <WorkingDirectory>$(xml_escape "$workdir")</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
EOF
}

# ── the script ───────────────────────────────────────────────────────────────

show() {                        # print a command the way it can be pasted into Git Bash
  local out="MSYS_NO_PATHCONV=1" a
  for a in "$@"; do out="$out $(shquote "$a")"; done
  printf '  $ %s\n' "$out"
}

main() {
  set -u
  local apply=0 action=install arg
  for arg in "$@"; do
    case "$arg" in
      --apply) apply=1 ;;
      --remove) action=remove ;;
      --status) action=status ;;
      -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; return 0 ;;
      *) echo "unknown option: $arg (try --help)" >&2; return 2 ;;
    esac
  done
  local schtasks=${SCHTASKS:-schtasks.exe}
  local root xml_file
  root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
  xml_file="$root/state/$TASK.task.xml"

  if [ "$action" = status ]; then
    show "$schtasks" /Query /TN "$TASK" /V /FO LIST
    MSYS_NO_PATHCONV=1 "$schtasks" /Query /TN "$TASK" /V /FO LIST
    return $?
  fi

  if [ "$action" = remove ]; then
    echo "Remove the scheduled task $TASK (stops it first; the bridge that is running now is not touched)."
    echo
    show "$schtasks" /End /TN "$TASK"
    show "$schtasks" /Delete /TN "$TASK" /F
    if [ "$apply" = 0 ]; then
      echo
      echo "DRY RUN: nothing was changed. Add --apply to do it."
      return 0
    fi
    MSYS_NO_PATHCONV=1 "$schtasks" /End /TN "$TASK" || true          # not running is fine
    MSYS_NO_PATHCONV=1 "$schtasks" /Delete /TN "$TASK" /F
    return $?
  fi

  local bash_exe script workdir user="${BRIDGE_TASK_USER:-}"
  bash_exe=$(find_bash_exe)
  script="$(to_mixed "$root")/${STARTUP_SCRIPT:-tools/bridge_supervisor.sh}"
  workdir=$(to_win "$root")
  if [ -z "$user" ] && [ -n "${USERNAME:-}" ]; then user="${USERDOMAIN:+$USERDOMAIN\\}$USERNAME"; fi

  if [ -n "${STARTUP_AT:-}" ]; then
    echo "Scheduled task $TASK: every day at $STARTUP_AT, as ${user:-the user who runs this}, run"
  else
    echo "Scheduled task $TASK: at logon of ${user:-the user who runs this}, run"
  fi
  echo "  $bash_exe -l \"$script\""
  local note="${STARTUP_NOTE:-the supervisor restarts the bridge itself, and logs to $workdir\\state\\bridge-supervisor.log}"
  if [ "${STARTUP_RESTART:-1}" = 0 ]; then
    echo "(no time limit; one copy at a time; not elevated; $note)"
  else
    echo "(no time limit; restarts on failure every minute; one copy at a time; not elevated; $note)"
  fi
  echo
  echo "The task definition:"
  task_xml "$bash_exe" "$script" "$workdir" "$user" | sed 's/^/    /'
  echo
  echo "Commands:"
  echo "  (write the task definition above, as UTF-16, to $(to_win "$xml_file"))"
  show "$schtasks" /Create /TN "$TASK" /XML "$(to_win "$xml_file")" /F
  if [ "$apply" = 0 ]; then
    echo
    echo "DRY RUN: nothing was changed. Add --apply to register it."
    echo "To start it without signing out: MSYS_NO_PATHCONV=1 schtasks.exe /Run /TN $TASK   (or just run: bash tools/bridge_supervisor.sh)"
    return 0
  fi

  mkdir -p "$root/state"
  task_xml "$bash_exe" "$script" "$workdir" "$user" | write_utf16 "$xml_file" || return 1
  if ! MSYS_NO_PATHCONV=1 "$schtasks" /Create /TN "$TASK" /XML "$(to_win "$xml_file")" /F; then
    echo "Windows refused. If it said \"Access is denied\", run this again from a terminal opened as Administrator." >&2
    return 1
  fi
  echo
  MSYS_NO_PATHCONV=1 "$schtasks" /Query /TN "$TASK" /FO LIST || true
  echo
  echo "Registered. It starts ${STARTUP_AT:+at $STARTUP_AT}${STARTUP_AT:-at your next sign-in}; to start it now: MSYS_NO_PATHCONV=1 schtasks.exe /Run /TN $TASK"
}

# Run only when executed; sourcing (the tests) just defines the functions.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
