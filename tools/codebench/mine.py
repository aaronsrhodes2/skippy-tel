"""CodeBench's tasks, mined from the Captain's own repos (SWE-bench's recipe, the Captain 2026-11-10: "Yes, real tasks", and "I want
purple skippy to be a lean, mean, full-stack game development ai assistant": after 16 MineBench rounds showed purple's limit is
building in 3D, not coding, its yardstick is the work it is for: game code, physics, the game servers, the portal's front end).

A task is a commit that changed code (Python or JavaScript) AND its tests (pytest `test_*.py`, or `*.test.js` under node:test),
where the commit's tests fail on its parent and pass on the commit. The player gets the parent's tree with no history and the
commit's tests already in it, failing, with the commit's message as the request (tools/codebench/run.py), and must make them pass:
  * fail_to_pass: tests that failed (or didn't exist) on the parent and pass on the commit: the change asked for;
  * pass_to_pass: the commit's other tests in those files, passing on both: what must not break.

    python tools/codebench/mine.py --repo ../sigma-ground [--scan 400] [--want 12] [--only REGEX] [--out tools/codebench/tasks.json]

`--only` keeps commits whose code change touches a path matching REGEX (SkippyTel's game parts, say). Proving a candidate runs its
tests twice in a scratch worktree of that repo (outside it, under CODEBENCH_DIR/mine/<repo>), so it takes minutes; tasks already in
the output file are kept and not proved again.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_FILE = re.compile(r"((^|/)tests?/(.+/)?test_[^/]+\.py|(^|/)tests?/(.+/)?[^/]+\.test\.m?js)$")
CODE_FILE = re.compile(r"\.(py|m?js)$")
WORK = Path(os.environ.get("CODEBENCH_DIR") or "D:/Aaron/development/skippy-codebench")
CODE_LINES = (5, 200)                  # the code change's size (added + deleted lines outside the tests): no one-liners, no rewrites
MAX_FILES = 8
TEST_TIMEOUT = 300


def git(*args, cwd, check=True) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:300]}")
    return p.stdout


def candidates(repo: Path, scan: int, only: str = "") -> list[dict]:
    """Commits whose diff fits a task: some tests and 5-200 lines of other code, at most MAX_FILES files."""
    keep = re.compile(only) if only else None
    out = []
    for sha in git("log", "--no-merges", "--format=%H", f"-n{scan}", "HEAD", cwd=repo).split():
        tests, code, lines, files = [], [], 0, 0
        for row in git("show", "--numstat", "--format=", sha, cwd=repo).splitlines():
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            files += 1
            added, deleted, path = parts
            if TEST_FILE.search(path):
                tests.append(path)
            elif CODE_FILE.search(path):
                code.append(path)
                lines += int(added) + int(deleted) if added.isdigit() and deleted.isdigit() else 999
        if keep and not any(keep.search(p) for p in code):
            continue
        if tests and code and files <= MAX_FILES and CODE_LINES[0] <= lines <= CODE_LINES[1]:
            out.append({"sha": sha, "tests": tests, "code": code, "code_lines": lines})
    return out


def outcomes(cwd: Path, tests: list[str]) -> dict[str, str]:
    """Each test's outcome (passed / failed / skipped), running each file alone: pytest for Python, node --test for JavaScript.
    A file that can't be collected or times out contributes nothing (its tests count as missing)."""
    got = {}
    for t in tests:
        if not (cwd / t).is_file():
            continue
        xml = Path(tempfile.mkdtemp()) / "junit.xml"
        if t.endswith(".py"):
            cmd = [sys.executable, "-m", "pytest", t, "-q", "-p", "no:cacheprovider", "-o", "addopts=", f"--junitxml={xml}"]
        else:
            cmd = ["node", "--test", "--test-reporter=junit", f"--test-reporter-destination={xml}", t]
        try:
            subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            continue
        if not xml.is_file() or not xml.stat().st_size:
            continue
        try:
            root = ET.parse(xml).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            name = f"{t}::{case.get('name')}"
            if case.find("failure") is not None or case.find("error") is not None:
                got[name] = "failed"
            elif case.find("skipped") is not None:
                got[name] = "skipped"
            else:
                got[name] = "passed"
    return got


def scratch(repo: Path) -> Path:
    w = WORK / "mine" / repo.name
    if not (w / ".git").exists():
        w.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "--detach", str(w), "HEAD", cwd=repo)
    return w


def prove(repo: Path, c: dict) -> dict | None:
    """The task, when the commit's tests fail on its parent and pass on it; else None."""
    w = scratch(repo)
    parent = git("rev-parse", f"{c['sha']}^", cwd=repo).strip()
    git("checkout", "-f", "--detach", parent, cwd=w)
    git("clean", "-fdq", cwd=w)
    for t in c["tests"]:                                         # the commit's tests on the parent's code
        if subprocess.run(["git", "cat-file", "-e", f"{c['sha']}:{t}"], cwd=w, capture_output=True).returncode == 0:
            git("checkout", c["sha"], "--", t, cwd=w)
    before = outcomes(w, c["tests"])
    git("checkout", "-f", "--detach", c["sha"], cwd=w)
    git("clean", "-fdq", cwd=w)
    after = outcomes(w, c["tests"])
    f2p = sorted(n for n, o in after.items() if o == "passed" and before.get(n) != "passed")
    p2p = sorted(n for n, o in after.items() if o == "passed" and before.get(n) == "passed")
    if not f2p or any(o == "failed" for o in after.values()):
        return None
    msg = git("show", "-s", "--format=%B", c["sha"], cwd=repo).strip()
    msg = "\n".join(line for line in msg.splitlines() if not line.startswith("Co-Authored-By:")).strip()
    return {"id": f"{repo.name}-{c['sha'][:8]}", "repo": repo.name, "sha": c["sha"], "parent": parent, "request": msg,
            "tests": c["tests"], "code": c["code"], "code_lines": c["code_lines"], "fail_to_pass": f2p, "pass_to_pass": p2p}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(HERE.parents[1]), help="the repo to mine (this one by default)")
    ap.add_argument("--scan", type=int, default=400, help="how many recent commits to look through")
    ap.add_argument("--want", type=int, default=12, help="stop when this repo has this many proved tasks")
    ap.add_argument("--only", default="", help="keep only commits whose code change touches a path matching this regex")
    ap.add_argument("--out", default=str(HERE / "tasks.json"))
    a = ap.parse_args(argv)
    repo, out = Path(a.repo).resolve(), Path(a.out)
    tasks = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
    known = {t["sha"] for t in tasks}
    mine = sum(1 for t in tasks if t["repo"] == repo.name)
    for c in candidates(repo, a.scan, a.only):
        if mine >= a.want:
            break
        if c["sha"] in known:
            continue
        try:
            task = prove(repo, c)
        except Exception as e:                                   # noqa: BLE001 — a commit that won't check out or run: not a task
            print(json.dumps({"sha": c["sha"][:10], "skipped": str(e)[:120]}), flush=True)
            continue
        print(json.dumps({"sha": c["sha"][:10], "task": bool(task), "f2p": len(task["fail_to_pass"]) if task else 0}), flush=True)
        if task:
            tasks.append(task)
            mine += 1
            out.write_text(json.dumps(tasks, indent=1), encoding="utf-8")
    print(json.dumps({"repo": repo.name, "tasks_from_it": mine, "tasks": len(tasks), "out": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
