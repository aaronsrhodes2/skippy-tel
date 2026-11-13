"""loop_breaker.py: the same failure again and again earns the next step up the ladder; a success resets it."""
import json
import subprocess
import sys
from pathlib import Path

import loop_breaker as lb

BAD = "Traceback (most recent call last):\n  ValueError: nope"
CMD = {"command": "python build.py --target x"}


def test_the_ladder_climbs_at_3_5_7_and_10_failures():
    br = lb.LoopBreaker()
    said = {}
    for n in range(1, 12):
        msg = br.observe("Bash", CMD, BAD)
        if msg:
            said[n] = msg
    assert sorted(said) == [3, 5, 7, 10]
    assert "hypothesis" in said[3] and "web_search" in said[5] and "approach" in said[7] and "Split the goal" in said[10]


def test_a_success_resets_and_other_calls_count_apart():
    br = lb.LoopBreaker()
    for _ in range(2):
        br.observe("Bash", CMD, BAD)
    assert br.observe("Bash", CMD, "ok built 3 files") == ""
    assert br.observe("Bash", CMD, BAD) == ""                      # back to failure number 1
    other = {"command": "git push"}
    assert [br.observe("Bash", other, "fatal: error: failed to push") for _ in range(2)] == ["", ""]
    assert br.counts[lb.signature("Bash", CMD)] == 1


def test_a_passing_test_run_is_not_a_failure():
    assert lb.failed("Bash", "===== 12 passed, 0 failed in 3s =====") is False
    assert lb.failed("Bash", "===== 1 failed, 11 passed in 3s =====") is True
    assert lb.failed("Read", "Traceback in the file I am reading") is False         # only shell calls are read for failure words


def test_the_hook_keeps_its_counts_between_runs_of_itself(tmp_path, monkeypatch):
    ev = json.dumps({"session_id": "s-test-123456", "tool_name": "Bash", "tool_input": CMD, "tool_response": {"stdout": "", "stderr": BAD}})
    outs = [subprocess.run([sys.executable, str(Path(lb.__file__))], input=ev, capture_output=True, text=True).stdout for _ in range(3)]
    assert outs[0] == "" and outs[1] == "" and "failed 3 times" in json.loads(outs[2])["hookSpecificOutput"]["additionalContext"]
    Path(lb._state_path("s-test-123456")).unlink(missing_ok=True)
