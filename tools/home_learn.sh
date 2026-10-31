#!/usr/bin/env bash
# The nightly home-learning run (the SkippyHomeLearn scheduled task runs this): learn routines from skippy-home/log, write routines/ and any new proposals,
# let Skippy Core's coder add a dated note in notes/, and commit the day to skippy-home (local git, no remote). Log: state/home-learn.log.
#   bash tools/home_learn.sh --dry-run     show what the learner would find and write nothing
cd "$(dirname "$0")/.." || exit 1
py=services/satellite/.venv/Scripts/python
home="${SKIPPY_HOME_DIR:-/d/Aaron/development/skippy-home}"
if [ "$1" = "--dry-run" ]; then
  "$py" services/satellite/routines.py learn --dry-run --home "$home" && "$py" services/satellite/home_agent.py --dry-run
  exit $?
fi
mkdir -p state
{
  echo "== $(date '+%Y-%m-%d %H:%M:%S') home learn"
  "$py" services/satellite/routines.py learn --home "$home"
  "$py" services/satellite/home_agent.py
  cd "$home" && git add -A && git -c user.name="Skippy" -c user.email="skippy@skippy-pc.local" commit -qm "Nightly: house log, routines and proposals for $(date +%F)" && echo committed || echo "nothing new to commit"
} >> state/home-learn.log 2>&1
