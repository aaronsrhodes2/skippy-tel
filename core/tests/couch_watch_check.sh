#!/usr/bin/env bash
# The scenarios behind core/tests/test_couch_run.py: core/couch/run.sh's watch() against a fake Moonlight (a sleep) and a fake log, with a
# flatpak app id nothing runs under. Touches nothing of the kiosk.   usage: bash couch_watch_check.sh /path/to/core/couch/run.sh
set -u
RUN="${1:?path to run.sh}"
T="$(mktemp -d)"; LOG="$T/moonlight.log"
FLATPAK_ID=org.example.NotARealApp
WATCH_S=0.3
HANG_S=3
eval "$(sed -n '/^new_lines() {/p;/^watch() {/,/^}/p' "$RUN")"
logger() { :; }                                                    # watch() tells the journal; a test must not (a run on skippy-core itself would)
bad=0
ok() { if [[ "$2" == "$3" ]]; then echo "ok    $1"; else echo "FAIL  $1 (wanted $2, got $3)"; bad=1; fi; }
state() { if kill -0 "$1" 2>/dev/null; then echo alive; else echo dead; fi; }
flood() { local i; for ((i = 0; i < $1; i++)); do printf 'INFO: Video decode unit queue overflow\nINFO: IDR frame request sent\nINFO: Waiting for IDR frame\n' >>"$LOG"; done; }
start() { from=$(stat -c %s "$LOG"); sleep 60 & p=$!; watch "$p" "$from" & w=$!; }
finish() { kill "$p" "$w" 2>/dev/null; wait "$p" "$w" 2>/dev/null; }

echo "== A: a teardown that hangs"
echo "11:59:59 - SDL Info (0): Stopping audio stream..." >"$LOG"       # an OLD session's lines must not count
start; sleep 1.2
ok "A1 old 'Stopping' lines are ignored" alive "$(state $p)"
echo "12:00:01 - SDL Info (0): Stopping input stream..." >>"$LOG"
sleep 0.6; ok "A2 teardown just started: given its grace" alive "$(state $p)"
sleep 4; ok "A3 teardown stuck past HANG_S: ended" dead "$(state $p)"
grep -q "ending Moonlight, its teardown hung" "$LOG" && echo "ok    A4 said so in the log" || { echo "FAIL  A4 not in the log"; bad=1; }
finish

echo "== B: a flooded decoder queue"
: >"$LOG"; start; sleep 0.8
ok "B1 quiet log: alive" alive "$(state $p)"
flood 100; sleep 1.5
ok "B2 flood: ended" dead "$(state $p)"
grep -q "ending Moonlight, its decoder queue is flooded" "$LOG" && echo "ok    B3 said so in the log" || { echo "FAIL  B3 not in the log"; bad=1; }
finish

echo "== C: a few overflows are normal"
: >"$LOG"; start; flood 8; sleep 1.2
ok "C1 8 overflow lines: alive" alive "$(state $p)"
finish

echo "== D: a flood an OLD session left in the log does not end the new Moonlight"
: >"$LOG"; flood 300; start; sleep 1.2
ok "D1 old flood ignored: alive" alive "$(state $p)"
finish

echo "== E: a clean end is left alone"
: >"$LOG"; from=$(stat -c %s "$LOG"); sleep 1 & p=$!; watch "$p" "$from" & w=$!
printf '12:00:01 - SDL Info (0): Stopping input stream...\n12:00:01 - SDL Info (0): Cleaning up platform...\n' >>"$LOG"
wait "$p" "$w" 2>/dev/null
grep -q "ending Moonlight" "$LOG" && { echo "FAIL  E1 a clean end was 'ended'"; bad=1; } || echo "ok    E1 nothing killed, nothing logged"

rm -rf "$T"
(( bad )) && { echo "FAILED"; exit 1; }
echo "all passed"
