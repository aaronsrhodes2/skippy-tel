"""The nightly notes: opens Claude Code in the skippy-home workspace on Skippy Core's coder (the same route the workshop's Core brain uses: Claude Code with
its own CLAUDE_CONFIG_DIR pointed at skippy-core's llama-swap via SKIPPY_CORE_URL / SKIPPY_CORE_API_KEY from the vault) and asks for a short dated note in notes/.
It does not touch the bridge or its settings: it only reads the same two vault values. The agent may read the workspace and write notes/ (CLAUDE.md says the rest).
Exit 0 even when Core is down: the learner's numbers (routines.py) are the product, the note is a bonus.

    python home_agent.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from housemem import home_dir

VAULT_ENV = Path(os.environ.get("SKIPPY_VAULT_ENV", r"G:\My Drive\vault\.env"))
PROMPT = ("Read CLAUDE.md, then routines/SUMMARY.md and today's log (log/{date}.jsonl). Write notes/{date}.md: in under 200 words, what you noticed today, "
          "which routines look new or fading, anything odd, and what more data would settle. Do not edit anything else. Say 'not enough data yet' when that is true.")


def vault_values(names=("SKIPPY_CORE_URL", "SKIPPY_CORE_API_KEY")) -> dict:
    out = {}
    try:
        for line in VAULT_ENV.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in names:
                out[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return {**{n: os.environ[n] for n in names if os.environ.get(n)}, **out}


def command(date: str) -> list[str]:
    exe = shutil.which("claude") or "claude"
    return [exe, "-p", PROMPT.format(date=date), "--permission-mode", "acceptEdits", "--allowedTools", "Read", "Write", "Glob", "Grep", "--max-turns", "12"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root, date = home_dir(), time.strftime("%Y-%m-%d")
    cmd = command(date)
    vals = vault_values()
    if args.dry_run or not (vals.get("SKIPPY_CORE_URL") and vals.get("SKIPPY_CORE_API_KEY")):
        print(f"agent step ({'dry run' if args.dry_run else 'Skippy Core not configured: skipped'}): cd {root} && {' '.join(cmd[:3])} ...")
        return 0
    cfg = root / ".claude-core"                                  # its own config folder: no subscription login wins over the key
    cfg.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
    env.update(ANTHROPIC_BASE_URL=vals["SKIPPY_CORE_URL"].rstrip("/"), ANTHROPIC_API_KEY=vals["SKIPPY_CORE_API_KEY"], ANTHROPIC_AUTH_TOKEN=vals["SKIPPY_CORE_API_KEY"],
               CLAUDE_CONFIG_DIR=str(cfg), CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_TELEMETRY="1")
    try:
        done = subprocess.run(cmd, cwd=root, env=env, timeout=900, capture_output=True, text=True)
        print(f"agent step: exit {done.returncode}; {(done.stdout or '').strip()[:300]}")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"agent step: skipped ({type(e).__name__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
