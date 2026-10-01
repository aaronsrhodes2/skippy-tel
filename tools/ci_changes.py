#!/usr/bin/env python3
"""deploy.yml's `changes` job: does Mentat need testing and deploying on this run?

Prints `mentat=true|false` and `reason=...` (GITHUB_OUTPUT format) on stdout. It only ever answers "false" when it has PROVED
that nothing Mentat is built from differs between the last successful deploy and this commit; every doubt answers "true":
a manual run, no earlier successful deploy, a base commit that cannot be fetched, a git failure, anything unexpected.

Why the base is the last SUCCESSFUL deploy and not the push's `before`: deploy.yml's concurrency group keeps one pending run
and cancels older pending ones, and a red run deploys nothing. Diffing against `before` would lose a Mentat change in either
case (push A touches mentat/ and is cancelled or fails; push B touches only the portal and would skip Mentat for good).
A tree diff against the last green deploy cannot lose it: Mentat was last deployed at, or after, that commit.

What Mentat is built from (mentat/Dockerfile, built from the repo root with .dockerignore): mentat/**, .dockerignore, and the
workflow itself, which holds the deploy flags and SIGMA_GROUND_REF (the pinned sigma-ground commit).
"""
from __future__ import annotations

import argparse
import subprocess
import sys

MENTAT_INPUTS = ("mentat/", ".github/workflows/", ".dockerignore")


def touches_mentat(changed: list[str]) -> str | None:
    """The first changed path Mentat is built from, or None."""
    for path in changed:
        if path.startswith(MENTAT_INPUTS):
            return path
    return None


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=120)


def decide(event: str, base: str, head: str = "HEAD", git_run=git) -> tuple[bool, str]:
    """(run_mentat, reason). `git_run` is the seam the tests replace."""
    if event != "push":
        return True, f"event is {event or 'unknown'}, not a push: run everything"
    if not base or set(base) == {"0"}:
        return True, "no earlier successful deploy to compare with: run everything"
    try:
        fetched = git_run("fetch", "--no-tags", "--depth=1", "origin", base)
        if fetched.returncode != 0:
            return True, f"could not fetch {base[:7]} ({fetched.stderr.strip()[:120]}): run everything"
        diff = git_run("diff", "--name-only", "--no-renames", base, head)
        if diff.returncode != 0:
            return True, f"git diff failed ({diff.stderr.strip()[:120]}): run everything"
    except Exception as exc:                    # timeout, git missing: any doubt means "run"
        return True, f"could not compare ({type(exc).__name__}): run everything"
    changed = [line for line in diff.stdout.splitlines() if line.strip()]
    if not changed:
        return False, f"no file differs from the last successful deploy ({base[:7]})"
    hit = touches_mentat(changed)
    if hit:
        return True, f"{hit} changed since the last successful deploy ({base[:7]})"
    return False, f"{len(changed)} file(s) changed since {base[:7]}, none that Mentat is built from"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event", default="")
    ap.add_argument("--base", default="")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args(argv)
    try:
        run, reason = decide(args.event, args.base, args.head)
    except Exception as exc:                    # never fail the job: a failed `changes` would silently skip Mentat
        run, reason = True, f"unexpected {type(exc).__name__}: run everything"
    print(f"mentat={'true' if run else 'false'}")
    reason = " ".join(reason.split())            # one line: a newline inside a GITHUB_OUTPUT value breaks the file's format
    print(f"reason={reason}")
    print(f"changes: mentat={run} ({reason})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
