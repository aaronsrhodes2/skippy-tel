#!/usr/bin/env bash
# Keeps the solid-gold bridge (services/claude-bridge/bridge.py) running on skippy-pc, forever.
#
#   bash tools/bridge_supervisor.sh            run it (foreground). `make bridge-supervised` does the same.
#   bash tools/bridge_supervisor.sh --status   is the bridge answering? is a supervisor running? last log lines.
#   bash tools/install_bridge_startup.sh       (dry run) how to start this at every logon: docs/portal/remote-recovery.md
#
# WHY this exists: the bridge is the one link of the private Skippy's chain that nothing restarted. The portal is on
# Cloud Run (Google restarts it), the home reporter is a Docker container (`restart: unless-stopped`, plus its own
# watchdog), but the bridge was `make bridge` in a terminal: a crash, a closed window or a reboot ended it, and with it
# the Captain's hands on skippy-pc, until somebody sat at the PC. (Incident 2026-09-30: after the demo's chat froze, "is
# the bridge even up?" was a question only someone at the PC could answer.)
#
# What it does:
#   * runs `python bridge.py` (same environment defaults as `make bridge`: CLAUDE_BRIDGE_CWD, CLAUDE_PATH) and, whenever
#     it exits, starts it again: after 3 s, then 6, 12, 24, 48, 60, 60 ... A bridge that stayed up for 5 minutes counts as
#     healthy, so the next failure starts the ladder from 3 s again.
#   * never runs a second bridge: if one already answers /health on the bridge's port (a hand-started `make bridge`, or
#     an orphan of an earlier supervisor), it does NOT start another. It watches that one and starts its own only
#     after it has stopped answering. A second SUPERVISOR is refused through a pid file.
#   * restarts a WEDGED bridge: the process is alive but /health has not answered for 5 checks in a row (about 2.5
#     minutes). A bridge that cannot answer /health cannot relay a run either, so nothing is lost by it.
#   * everything it and the bridge print goes to state/bridge-supervisor.log (state/ is gitignored), copy-truncated at
#     2 MB into bridge-supervisor.log.1 so it can never fill the disk. No secret is printed: the bridge reads
#     SKIPPY_HOME_TOKEN itself and never logs it.
#
# Settings (environment; the defaults are the production ones). BRIDGE_SUP_* exist for the tests.
#   CLAUDE_BRIDGE_PORT        9337     the bridge's port (also what /health is asked on)
#   CLAUDE_BRIDGE_CWD         D:/Aaron/development   where `claude -p` runs (same default as `make bridge`)
#   CLAUDE_PATH               (unset)  the claude binary, if it is not on PATH
#   PYTHON                    python   (else python, py, python3: the first that exists)
#   BRIDGE_SUP_MIN_DELAY=3  BRIDGE_SUP_MAX_DELAY=60  BRIDGE_SUP_RESET_AFTER=300   the restart ladder, in seconds
#   BRIDGE_SUP_HEALTH_FAILS=5  BRIDGE_SUP_HEALTH_EVERY=30  BRIDGE_SUP_HEALTH_GRACE=30   wedge detection (FAILS=0: off)
#   BRIDGE_SUP_WATCH_POLL=10  BRIDGE_SUP_WATCH_FAILS=3   watching a bridge that someone else started
#   BRIDGE_SUP_LOG_MAX=2097152   BRIDGE_SUP_STATE_DIR=<repo>/state
#   BRIDGE_SUP_CMD            replaces `python bridge.py` (tests: a stub), split on spaces
#   BRIDGE_SUP_MAX_RUNS=0     stop after this many runs (tests; 0 = forever)

# Not `set -e`: a crashing child is the normal case here, and must never end the loop.

num() {                         # num <value> <default>: a non-negative integer, else the default
  case "$1" in ''|*[!0-9]*) echo "$2" ;; *) echo "$1" ;; esac
}

stamp() { date '+%Y-%m-%d %H:%M:%S'; }

log() {                         # to the log file, and to the terminal when someone is watching one (BRIDGE_SUP_ECHO=1/0 forces it)
  local line
  line="$(stamp) supervisor: $*"
  if [ "${BRIDGE_SUP_ECHO:-}" = 1 ] || { [ "${BRIDGE_SUP_ECHO:-}" != 0 ] && [ -t 1 ]; }; then
    echo "$line" 2>/dev/null || true
  fi
  printf '%s\n' "$line" >>"$LOG" 2>/dev/null || true
}

init_config() {
  ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
  STATE_DIR=${BRIDGE_SUP_STATE_DIR:-$ROOT/state}
  LOG=$STATE_DIR/bridge-supervisor.log
  PIDFILE=$STATE_DIR/bridge-supervisor.pid
  PORT=$(num "${CLAUDE_BRIDGE_PORT:-}" 9337)
  HEALTH_URL=${BRIDGE_SUP_HEALTH_URL:-http://127.0.0.1:$PORT/health}
  MIN_DELAY=$(num "${BRIDGE_SUP_MIN_DELAY:-}" 3)
  MAX_DELAY=$(num "${BRIDGE_SUP_MAX_DELAY:-}" 60)
  [ "$MIN_DELAY" -ge 1 ] || MIN_DELAY=1
  [ "$MAX_DELAY" -ge "$MIN_DELAY" ] || MAX_DELAY=$MIN_DELAY
  RESET_AFTER=$(num "${BRIDGE_SUP_RESET_AFTER:-}" 300)
  HEALTH_FAILS=$(num "${BRIDGE_SUP_HEALTH_FAILS:-}" 5)
  HEALTH_EVERY=$(num "${BRIDGE_SUP_HEALTH_EVERY:-}" 30)
  [ "$HEALTH_EVERY" -ge 1 ] || HEALTH_EVERY=1
  HEALTH_GRACE=$(num "${BRIDGE_SUP_HEALTH_GRACE:-}" 30)
  KILL_GRACE=$(num "${BRIDGE_SUP_KILL_GRACE:-}" 5)
  WATCH_POLL=$(num "${BRIDGE_SUP_WATCH_POLL:-}" 10)
  [ "$WATCH_POLL" -ge 1 ] || WATCH_POLL=1
  WATCH_FAILS=$(num "${BRIDGE_SUP_WATCH_FAILS:-}" 3)
  LOG_MAX=$(num "${BRIDGE_SUP_LOG_MAX:-}" 2097152)
  MAX_RUNS=$(num "${BRIDGE_SUP_MAX_RUNS:-}" 0)
  WORKDIR=$ROOT/services/claude-bridge
  CMD=()
  CHILD=""
  MON=""
  NAPPID=""
  STOP=""
}

# ── pure pieces (the tests drive these directly) ─────────────────────────────

# backoff_step <delay> <seconds the bridge stayed up>  ->  "<seconds to wait now> <delay for the next failure>"
backoff_step() {
  local delay=$1 up=$2 next
  if [ "$up" -ge "$RESET_AFTER" ]; then delay=$MIN_DELAY; fi   # it ran for a good while: this is a new incident
  next=$(( delay * 2 ))
  if [ "$next" -gt "$MAX_DELAY" ]; then next=$MAX_DELAY; fi
  echo "$delay $next"
}

now() { echo "$SECONDS"; }      # wall seconds since the shell started; a function so the tests can fake the clock

nap() {                         # sleep, but let a signal (stop) interrupt it at once
  sleep "$1" &
  NAPPID=$!
  wait "$NAPPID" 2>/dev/null
  NAPPID=""
  return 0
}

probe() {                       # does the bridge's /health answer (HTTP 200)?
  curl -fsS -m 3 -o /dev/null "$HEALTH_URL" >/dev/null 2>&1
}

pick_python() {
  local p
  if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return 0; fi
  for p in python py python3; do      # python3 last: on Windows it can be the Microsoft Store stub
    if command -v "$p" >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  return 1
}

build_cmd() {
  CMD=()
  if [ -n "${BRIDGE_SUP_CMD:-}" ]; then
    read -r -a CMD <<<"$BRIDGE_SUP_CMD"
    return 0
  fi
  local py
  py=$(pick_python) || return 1
  CMD=("$py" bridge.py)
}

rotate_log() {                  # copy-truncate: the child keeps its (append-mode) handle, so nothing is lost for long
  local size
  size=$(wc -c <"$LOG" 2>/dev/null) || return 0
  size=${size//[!0-9]/}
  [ "${size:-0}" -gt "$LOG_MAX" ] || return 0
  cp -f "$LOG" "$LOG.1" 2>/dev/null && : >"$LOG"
  return 0
}

# ── one supervisor at a time ─────────────────────────────────────────────────

supervisor_alive() {            # is the pid in the pid file a live supervisor (not a reused pid, not us)?
  local pid cmdline
  pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ "$pid" != "$$" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  if [ -r "/proc/$pid/cmdline" ]; then                      # after a reboot the pid may belong to something else
    cmdline=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null)
    case "$cmdline" in *bridge_supervisor*) ;; *) return 1 ;; esac
  fi
  return 0
}

claim_pidfile() {               # 0 = ours; 1 = another supervisor is running
  if supervisor_alive; then return 1; fi
  rm -f "$PIDFILE" 2>/dev/null
  # noclobber makes the create atomic: of two supervisors starting in the same instant only one wins.
  if ( set -o noclobber; echo "$$" >"$PIDFILE" ) 2>/dev/null; then return 0; fi
  if supervisor_alive; then return 1; fi
  echo "$$" >"$PIDFILE" 2>/dev/null || true                  # unwritable state dir: run anyway, just without the lock
  return 0
}

release_pidfile() {
  [ "$(cat "$PIDFILE" 2>/dev/null)" = "$$" ] && rm -f "$PIDFILE" 2>/dev/null
  return 0
}

# ── running the bridge ───────────────────────────────────────────────────────

monitor() {                     # monitor <bridge pid>: rotates the log, and restarts a bridge that is alive but mute
  local pid=$1 fails=0
  sleep "$HEALTH_GRACE"         # it needs a moment to bind its port
  while kill -0 "$pid" 2>/dev/null; do
    rotate_log
    if [ "$HEALTH_FAILS" -gt 0 ]; then
      if probe; then
        fails=0
      else
        fails=$(( fails + 1 ))
        log "the bridge (pid $pid) is running but /health did not answer ($fails of $HEALTH_FAILS)"
        if [ "$fails" -ge "$HEALTH_FAILS" ]; then
          log "the bridge looks wedged: stopping it so it restarts"
          kill "$pid" 2>/dev/null
          sleep "$KILL_GRACE"
          kill -9 "$pid" 2>/dev/null
          return 0
        fi
      fi
    fi
    sleep "$HEALTH_EVERY"
  done
}

stop_monitor() {
  if [ -n "$MON" ]; then kill "$MON" 2>/dev/null; wait "$MON" 2>/dev/null; MON=""; fi
  return 0
}

run_child() {                   # runs the bridge once, to its end; returns its exit code
  if ! build_cmd; then
    log "no python on PATH (tried python, py, python3): set PYTHON, or install it. Trying again after the wait."
    return 127
  fi
  export CLAUDE_BRIDGE_CWD=${CLAUDE_BRIDGE_CWD:-D:/Aaron/development}   # `make bridge`'s default
  export PYTHONUNBUFFERED=${PYTHONUNBUFFERED:-1}                         # log lines appear when they happen
  log "starting the bridge: ${CMD[*]}"
  # Neither the bridge nor the monitor keeps our stdout or stdin: an orphaned `sleep` of the monitor must not hold a
  # terminal (or a test's pipe) open after we are gone.
  ( cd "$WORKDIR" 2>/dev/null; exec "${CMD[@]}" ) </dev/null >>"$LOG" 2>&1 &
  CHILD=$!
  ( monitor "$CHILD" ) </dev/null >>"$LOG" 2>&1 &
  MON=$!
  local rc
  wait "$CHILD"
  rc=$?
  stop_monitor
  CHILD=""
  return "$rc"
}

# Never a second copy. Returns when nothing answers on the bridge's port any more (or when asked to stop).
hold_while_bridge_answers() {
  probe || return 0
  log "a bridge already answers on $HEALTH_URL: not starting a second copy. Watching it; I start one if it stops."
  local fails=0
  while [ -z "$STOP" ]; do
    nap "$WATCH_POLL"
    if probe; then
      fails=0
    else
      fails=$(( fails + 1 ))
      if [ "$fails" -ge "$WATCH_FAILS" ]; then
        log "that bridge stopped answering: taking over"
        return 0
      fi
    fi
  done
}

supervise_loop() {
  local delay=$MIN_DELAY runs=0 started up rc step wait_s
  while [ -z "$STOP" ]; do
    hold_while_bridge_answers
    [ -z "$STOP" ] || break
    runs=$(( runs + 1 ))
    started=$(now)
    run_child
    rc=$?
    [ -z "$STOP" ] || break
    up=$(( $(now) - started ))
    step=$(backoff_step "$delay" "$up")
    wait_s=${step% *}
    delay=${step#* }
    log "the bridge exited (code $rc) after ${up}s; starting it again in ${wait_s}s"
    if [ "$MAX_RUNS" -gt 0 ] && [ "$runs" -ge "$MAX_RUNS" ]; then
      log "stopping after $runs runs (BRIDGE_SUP_MAX_RUNS)"
      return 0
    fi
    nap "$wait_s"
  done
  return 0
}

on_signal() {
  STOP=1
  log "told to stop: stopping the bridge too"
  if [ -n "$CHILD" ]; then kill "$CHILD" 2>/dev/null; fi
  if [ -n "$NAPPID" ]; then kill "$NAPPID" 2>/dev/null; fi
  stop_monitor
}

status_cmd() {                  # exit 0 when the bridge answers, 1 when it does not
  local body up=1
  if body=$(curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null); then
    echo "bridge:     answering on port $PORT: $body"
    up=0
  else
    echo "bridge:     NOT answering on $HEALTH_URL"
  fi
  if supervisor_alive; then
    echo "supervisor: running (pid $(cat "$PIDFILE"))"
  else
    echo "supervisor: not running (start it: bash tools/bridge_supervisor.sh)"
  fi
  if [ -f "$LOG" ]; then
    echo "--- last lines of $LOG"
    tail -n 12 "$LOG"
  else
    echo "(no log yet: $LOG)"
  fi
  return "$up"
}

main() {
  set -u
  init_config
  case "${1:-}" in
    --status) status_cmd; exit $? ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    '') ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  trap '' PIPE                                              # a closed terminal must not kill us when we print to it
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  if ! : >>"$LOG" 2>/dev/null; then LOG=/dev/null; fi       # an unwritable state dir must not stop the bridge starting
  if [ -d /mingw64/bin ]; then PATH="$PATH:/mingw64/bin"; fi   # a scheduled task's shell can lack the bundled curl
  if ! claim_pidfile; then
    echo "another supervisor is already running (pid $(cat "$PIDFILE" 2>/dev/null)): nothing to do."
    exit 0
  fi
  trap on_signal TERM INT HUP
  trap release_pidfile EXIT
  if ! command -v curl >/dev/null 2>&1; then
    log "curl is missing: the wedge check and the second-copy check are off"
    probe() { return 1; }
  fi
  log "supervisor started (pid $$); bridge port $PORT; log $LOG"
  supervise_loop
  log "supervisor finished"
}

# Run only when executed; `source tools/bridge_supervisor.sh` (the tests) just defines the functions.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
