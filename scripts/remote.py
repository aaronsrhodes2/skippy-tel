"""Operate the games VM from anywhere Google credentials are available — in
practice from the `ops` GitHub Actions workflow, so Claude can diagnose and
act without anyone fetching logs or pressing buttons.

    python scripts/remote.py status
    python scripts/remote.py logs [game]
    python scripts/remote.py start <game> [<game> ...]
    python scripts/remote.py stop | restart

Uses the portal's own modules (portal/gcp.py, ops.py, diagnostics.py), so it
follows exactly the rules the portal buttons do. Every secret value (world /
admin passwords, bot tokens, webhook URLs) is registered with GitHub's log
masker before anything is printed, and redacted again as belt and braces.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portal"))

import diagnostics  # noqa: E402
import ops  # noqa: E402
from catalog import GAMES, missing_settings  # noqa: E402
from gcp import GamesVm  # noqa: E402

SECRET_WORDS = ("PASSWORD", "TOKEN", "SECRET", "WEBHOOK", "KEY")


def is_secret(key: str) -> bool:
    return any(w in key for w in SECRET_WORDS)


def mask_secrets(env: dict[str, str]) -> None:
    """Tell GitHub Actions to hide these values anywhere in this job's log."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for key, value in env.items():
        if is_secret(key) and len(value) >= 3:
            print(f"::add-mask::{value}", flush=True)


def scrub(text: str, env: dict[str, str]) -> str:
    text = diagnostics.redact(text, env)
    for key, value in env.items():
        if is_secret(key) and len(value) >= 3:
            text = text.replace(value, "••••")
    return text


def status(vm) -> dict:
    s = vm.status()
    codes = diagnostics.current_join_codes(vm) if s.status == "RUNNING" else {}
    return {
        "status": s.status,
        "machine_type": s.machine_type,
        "external_ip": s.external_ip,
        "games": s.games,
        "idle_minutes": s.idle_minutes,
        "minutes_up": s.minutes_up(),
        "invite_codes": codes,
        "missing_settings": {g: missing_settings(g, s.env) for g in GAMES if missing_settings(g, s.env)},
        # Setting values only where they aren't secret; secrets just say whether they're set.
        "settings": {k: ("(set)" if is_secret(k) and v else v) for k, v in sorted(s.env.items())},
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    vm = GamesVm(os.environ.get("GCP_PROJECT", "skippy-tel"), os.environ.get("GCP_ZONE", "us-west1-b"),
                 os.environ.get("GCP_INSTANCE", "skippy-games"))
    env = vm.status().env
    mask_secrets(env)

    if cmd == "status":
        print(json.dumps(status(vm), indent=2))
    elif cmd == "logs":
        game = args[0] if args and args[0] in GAMES else None
        logs = diagnostics.read(vm, game)
        print("=== containers" + (f" ({game})" if game else "") + " ===")
        print(scrub(logs["containers"], env))
        print("\n=== last boot (startup script) ===")
        print(scrub(logs["startup"], env))
    elif cmd == "start":
        ok, message = ops.start_games(vm, args, None, is_admin=True)
        print(message)
        return 0 if ok else 1
    elif cmd == "stop":
        print(ops.stop_server(vm)[1])
    elif cmd == "restart":
        print(ops.restart_server(vm)[1])
    else:
        print(f"unknown command {cmd!r}\n{__doc__}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
