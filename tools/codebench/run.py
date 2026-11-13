"""Run CodeBench: each task (tasks.json, from mine.py) by each player, purple's way, then score it against the hidden tests.

A player is Claude Code with purple's system prompt and an empty config (no skills, no memory), run headless like purple runs:
`--dangerously-skip-permissions` with the lane guard keeping its writes in its own folder, effort high, a 131K window.
  * coder: Skippy Core's coder (the local model under test);
  * haiku: Claude Haiku 5.5, the bar (the Captain, 2026-11-10: "a local coding model ... on par with a 130k haiku"). RETIRED from
    the default 2026-11-12 (the Captain: "we have gotten what we want out of Haiku ... pivot all experimentation over to our local
    models only"): its 6 valid baseline runs are the bar; it plays only when named with --players and the API key has credit.
Both run a task at once (they share nothing); the coder's runs wait for purple's bridge to be idle, since Core serves one at a time.

Each run starts from the task's parent commit as a fresh one-commit repo (`git archive`, so no history: the fix can't be looked up)
with the commit's own test files already in it, failing: the spec, as "make these tests pass" (hidden tests that call functions
the commit invented can't be guessed from its message, and every player failing on that measures nothing). The request is the
commit's message. Scoring puts those test files back as the commit had them (an edited test earns nothing) and runs them:
  * resolved: every fail_to_pass test passes and every pass_to_pass test still does;
  * on_its_own: it ended without asking the Captain anything (the Captain: "continue tasks longer, like fable does, so it prompts
    me for permission basically never unless I ask for a gate or a stop");
  * minutes, turns, output tokens: what it cost.

    python tools/codebench/run.py [--tasks id,id] [--players coder] [--tag baseline] [--timeout 60]

Results go to CODEBENCH_DIR/results-<tag>.jsonl, one line per (task, player); a pair already there is skipped, so a run resumes.
`--gates` adds purple's stop gate (finish_gate.py: uncommitted work is sent back) for the coder: the setup purple really has.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mine  # noqa: E402

BRIDGE = HERE.parents[1] / "services" / "claude-bridge"
sys.path.insert(0, str(BRIDGE))
import projects  # noqa: E402
import workshop_engine as we  # noqa: E402

DEV = Path("D:/Aaron/development")
PLAYERS = {"haiku": "claude-haiku-5-5", "coder": we.CORE_MODEL}
ASKED = re.compile(r"\?\s*$|\b(should I|shall I|would you like|do you want|want me to|let me know|your call|confirm)\b", re.I | re.M)
MESSAGE = ("This folder is a checkout of {repo}. The request:\n\n{request}\n\nThe tests that check it are already in place and fail "
           "now: {tests}. Make them pass without changing them, keep the other tests passing, and commit locally when it is done "
           "(this folder has no remote: never push).")


def vault(name: str) -> str:
    for v in ("G:/My Drive/vault/.env", "D:/Aaron/development/.env"):
        p = Path(v)
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("no " + name)


def bridge_busy() -> bool:
    try:
        return json.loads(urllib.request.urlopen("http://localhost:9337/health", timeout=10).read())["workshop"]["busy"]
    except Exception:  # noqa: BLE001 — a bridge that doesn't answer is running nothing
        return False


def wipe(path: Path) -> None:
    """Remove a folder, git's read-only object files too (rmtree's ignore_errors left them behind on Windows)."""
    import os
    import stat
    if path.exists():
        shutil.rmtree(path, onexc=lambda fn, p, _e: (os.chmod(p, stat.S_IWRITE), fn(p)))


def checkout(task: dict, work: Path) -> None:
    """The task's parent commit as a fresh repo with one commit and no history."""
    wipe(work)
    work.mkdir(parents=True)
    tar = subprocess.run(["git", "archive", "--format=tar", task["parent"]], cwd=DEV / task["repo"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(work, filter="data")
    put_tests(task, work)                     # the spec: the commit's tests, failing (a new test can't be guessed from a message)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.name", "Aaron Rhodes"], ["git", "config", "user.email", "aaron.s.rhodes@gmail.com"],
                ["git", "config", "core.autocrlf", "false"], ["git", "add", "-A"], ["git", "commit", "-qm", "the starting point"]):
        subprocess.run(cmd, cwd=work, check=True, capture_output=True)


def setup(task: dict, player: str, run: Path, gates: bool) -> tuple[list, dict, Path]:
    work, config = run / "work", run / "config"
    checkout(task, work)
    wipe(config)
    config.mkdir(parents=True)
    (config / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_ATTRIBUTION_HEADER": "0"}}), encoding="utf-8")
    py = f'"{sys.executable}"'
    hooks = {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                             "hooks": [{"type": "command", "command": f'{py} "{BRIDGE}/lane_guard.py"', "timeout": 10}]}]}
    env = we.child_env()
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "SKIPPY_LANE_DEPLOY", "SKIPPY_LANE_FIX", "SKIPPY_FINISH_STATE"):
        env.pop(k, None)
    if gates:
        hooks["Stop"] = [{"hooks": [{"type": "command", "command": f'{py} "{BRIDGE}/finish_gate.py"', "timeout": 900}]}]
        state = config / "finish.json"
        state.write_text(json.dumps({"clone": str(work), "baseline": [], "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip(), "sent_back": 0,
            "started": time.time()}), encoding="utf-8")
        env["SKIPPY_FINISH_STATE"] = str(state)
    (config / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    project = projects.Project(id=f"f-cb-{task['id']}-{player}", name=task["repo"], primary=str(work), clone=str(work), repo="",
                               branch="", deploy="", inplace=True)
    prompt = config / "system-prompt.md"
    prompt.write_text(we.render_prompt((BRIDGE / "workshop_prompt.md").read_text(encoding="utf-8"), project), encoding="utf-8")
    env.update(CLAUDE_CONFIG_DIR=str(config), CLAUDE_PROJECT_DIR=str(work), CLAUDE_CODE_MAX_CONTEXT_TOKENS=we.CORE_CONTEXT,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_TELEMETRY="1")
    if player == "coder":
        key = vault("SKIPPY_CORE_API_KEY")
        env.update(ANTHROPIC_BASE_URL=vault("SKIPPY_CORE_URL"), ANTHROPIC_API_KEY=key, ANTHROPIC_AUTH_TOKEN=key,
                   ANTHROPIC_SMALL_FAST_MODEL=we.CORE_FAST_MODEL, ANTHROPIC_DEFAULT_HAIKU_MODEL=we.CORE_FAST_MODEL,
                   ANTHROPIC_DEFAULT_SONNET_MODEL=we.CORE_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL=we.CORE_MODEL,
                   CLAUDE_CODE_MAX_OUTPUT_TOKENS=we.CORE_MAX_OUTPUT, SKIPPY_CORE_EYES="1")
    else:
        env.pop("ANTHROPIC_BASE_URL", None)
        env.update(ANTHROPIC_API_KEY=vault("ANTHROPIC_API_KEY"), ANTHROPIC_SMALL_FAST_MODEL=PLAYERS["haiku"])
    cmd = [claude_exe(), "-p", "--output-format", "stream-json", "--verbose", "--model", PLAYERS[player], "--effort", "high",
           "--dangerously-skip-permissions", "--append-system-prompt-file", str(prompt), "--settings", str(config / "hooks.json")]
    return cmd, env, work


def put_tests(task: dict, work: Path) -> None:
    """The commit's own test files, as the commit has them, over whatever is in `work`."""
    for t in task["tests"]:
        body = subprocess.run(["git", "show", f"{task['sha']}:{t}"], cwd=DEV / task["repo"], capture_output=True)
        if body.returncode == 0:
            (work / t).parent.mkdir(parents=True, exist_ok=True)
            (work / t).write_bytes(body.stdout)


def claude_exe() -> str:
    """The native claude.exe purple runs (sdk_engine.native_cli), never npm's .cmd shim: through cmd.exe the prompt arrived cut off
    at its first newline and every option after it was lost (the pilot's first two runs)."""
    import sdk_engine
    return sdk_engine.native_cli([shutil.which("claude") or "claude"])


def score(task: dict, work: Path) -> dict:
    """The commit's tests put back as they were (an edited test earns nothing) and run."""
    put_tests(task, work)
    got = mine.outcomes(work, task["tests"])
    f2p = sum(got.get(n) == "passed" for n in task["fail_to_pass"])
    p2p = sum(got.get(n) == "passed" for n in task["pass_to_pass"])
    return {"resolved": f2p == len(task["fail_to_pass"]) and p2p == len(task["pass_to_pass"]),
            "fail_to_pass": f"{f2p}/{len(task['fail_to_pass'])}", "pass_to_pass": f"{p2p}/{len(task['pass_to_pass'])}"}


def ending(log: Path) -> dict:
    """From the stream-json log: the last words, whether they ask the Captain something, and turns, output tokens and working
    minutes summed over every `result` event (a background shell finishing wakes the session again, so one run can have several;
    the pilot's Haiku had five, and the last one's counts alone said 10 turns for a 32-turn run)."""
    final, turns, out, ms, results = "", 0, 0, 0, 0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "result":
            if e.get("is_error") and not results and int(e.get("num_turns") or 0) <= 1:
                return {"void": str(e.get("result") or "an API error")[:200]}   # refused at once (out of credit): no run, play it again
            results += 1
            final = str(e.get("result") or "")
            turns += int(e.get("num_turns") or 0)
            out += int((e.get("usage") or {}).get("output_tokens") or 0)
            ms += int(e.get("duration_ms") or 0)
    tail = final.strip()[-600:]
    return {"on_its_own": bool(tail) and not ASKED.search(tail[-300:]), "turns": turns, "output_tokens": out,
            "work_minutes": round(ms / 60000, 1), "results": results, "last_words": tail}


def play(task: dict, player: str, tag: str, timeout_min: int, gates: bool) -> dict:
    run = mine.WORK / "runs" / tag / f"{task['id']}-{player}"
    cmd, env, work = setup(task, player, run, gates and player == "coder")
    log = run / "stream.jsonl"
    t0 = time.time()
    message = MESSAGE.format(repo=task["repo"], request=task["request"], tests=", ".join(task["tests"]))
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, cwd=work, env=env, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
        p.stdin.write(message.encode("utf-8"))
        p.stdin.close()
        try:
            code = p.wait(timeout=timeout_min * 60)
        except subprocess.TimeoutExpired:
            p.kill()
            code = "timeout"
    end = ending(log)
    if "void" in end:
        return {"task": task["id"], "player": player, "tag": tag, "void": end["void"]}
    row = {"task": task["id"], "repo": task["repo"], "player": player, "tag": tag, "exit": code,
           "minutes": round((time.time() - t0) / 60, 1), **end, **score(task, work)}
    row["ending"] = judged(task, row["last_words"])
    if row["ending"]:                                           # Core's judge (hanging.judge, purple's own stop gate) over the regex
        row["on_its_own"] = row["ending"] == "done"
    if code == "timeout":                                       # cut off while working (no answer yet), or while waiting on a
        row["on_its_own"] = row["on_its_own"] and row["results"] > 0     # background shell after one: that answer still stands
    return row


def judged(task: dict, words: str) -> str:
    """How the run's last words end (done / promise / ask), by the same Core judge purple's stop gate uses; '' when Core can't say."""
    import hanging
    try:
        ask = hanging.core_asker()
    except Exception:                                           # noqa: BLE001 — no Core: the regex's on_its_own stands
        return ""
    return hanging.judge(task["request"], words, ask) if ask else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="", help="task ids, comma separated (all by default)")
    ap.add_argument("--players", default="coder")
    ap.add_argument("--tag", default="baseline", help="names this configuration in the results")
    ap.add_argument("--timeout", type=int, default=60, help="minutes a run may take")
    ap.add_argument("--gates", action="store_true", help="give the coder purple's stop gate")
    ap.add_argument("--file", default=str(HERE / "tasks.json"))
    a = ap.parse_args(argv)
    tasks = json.loads(Path(a.file).read_text(encoding="utf-8"))
    if a.tasks:
        want = set(a.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in want]
    results = mine.WORK / f"results-{a.tag}.jsonl"
    done = set()
    if results.is_file():
        for line in results.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["task"], r["player"]))
    lock = threading.Lock()

    def one(task, player):
        row = play(task, player, a.tag, a.timeout, a.gates)
        if "void" in row:                                       # not written: the next run of this tag plays it again
            print(json.dumps(row), flush=True)
            return
        with lock, open(results, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps({k: row[k] for k in ("task", "player", "resolved", "fail_to_pass", "pass_to_pass", "on_its_own",
                                              "minutes", "work_minutes", "turns", "output_tokens", "ending")}), flush=True)

    for task in tasks:
        todo = [p for p in a.players.split(",") if (task["id"], p) not in done]
        if "coder" in todo:
            while bridge_busy():
                time.sleep(30)
        threads = [threading.Thread(target=one, args=(task, p)) for p in todo]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
