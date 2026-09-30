"""Operate the games VM from anywhere Google credentials are available — in
practice from the `ops` GitHub Actions workflow, so Claude can diagnose and
act without anyone fetching logs or pressing buttons.

    python scripts/remote.py status
    python scripts/remote.py logs [game]
    python scripts/remote.py start <game> [<game> ...]
    python scripts/remote.py stop | restart
    python scripts/remote.py discord-setup     # runs in the portal (it holds the bot token)
    python scripts/remote.py tail <game> [lines]         # these four run on the VM
    python scripts/remote.py search <game> <regex>       # (ops_agent.py), VM must be on
    python scripts/remote.py stats
    python scripts/remote.py restart-game <game>
    python scripts/remote.py backups [game]              # per-game rollback points (game_backup.py TARGETS)
    python scripts/remote.py restore-game <game> <archive|latest>   # roll one game back (files set aside, not deleted)
    python scripts/remote.py configure [apply] [join-password]   # set every game up without typing anything:
                                                         # previews; `apply` writes (autoconfig.py)
    python scripts/remote.py home                        # skippy-pc as the portal sees it
                                                         # (needs OPS_ID_TOKEN: the workflow mints it)

Uses the portal's own modules (portal/gcp.py, ops.py, diagnostics.py), so it
follows exactly the rules the portal buttons do. Every secret value (world /
admin passwords, bot tokens, webhook URLs) is registered with GitHub's log
masker before anything is printed, and redacted again as belt and braces.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portal"))

import autoconfig  # noqa: E402
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
        "max_hours": s.max_hours,                    # the watchdog's session ceiling (0 = none)
        "minutes_up": s.minutes_up(),
        "invite_codes": codes,
        "missing_settings": {g: missing_settings(g, s.env) for g in GAMES if missing_settings(g, s.env)},
        # Setting values only where they aren't secret; secrets just say whether they're set.
        "settings": {k: ("(set)" if is_secret(k) and v else v) for k, v in sorted(s.env.items())},
    }


PORTAL_TASKS = ("discord-setup",)
PORTAL_URL = os.environ.get("PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app")


def portal_task(vm, name: str) -> int:
    """Queue a task in VM metadata, ask the portal to run the queue, report."""
    task_id = uuid.uuid4().hex[:12]
    queue = json.loads(vm.get_metadata("portal-tasks") or "[]")
    vm.set_metadata("portal-tasks", json.dumps(queue + [{"id": task_id, "task": name}]))
    req = urllib.request.Request(f"{PORTAL_URL}/tasks/run", data=b"", method="POST",
                                 headers={"Authorization": f"Bearer {os.environ.get('OPS_ID_TOKEN', '')}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        results = json.loads(resp.read())["results"]
    mine = [r for r in results if r["id"] == task_id]
    if not mine:   # another caller ran it; read the recorded result
        time.sleep(2)
        mine = [r for r in json.loads(vm.get_metadata("portal-task-results") or "[]") if r["id"] == task_id]
    if not mine:
        print(f"{name}: no result recorded (queued as {task_id})")
        return 1
    print(f"{name}: {'OK' if mine[0]['ok'] else 'FAILED'} — {mine[0]['message']}")
    return 0 if mine[0]["ok"] else 1


def configure(vm, args: list[str], env: dict[str, str]) -> int:
    """`configure` shows what would be set; `configure apply [join-password]` writes it. Only key names are
    ever printed, and every new secret is registered with GitHub's log masker before anything is written."""
    apply = bool(args) and args[0] == "apply"
    rest = args[1:] if apply else args
    join = rest[0] if rest else None
    if join:
        mask_secrets({"JOIN_PASSWORD": join})
    try:
        plan = autoconfig.plan(env, GAMES, join_password=join)
    except autoconfig.PlanError as exc:
        print(exc)
        return 1
    mask_secrets(plan.set)
    print(("APPLIED: " if apply else "PREVIEW (nothing written; `configure apply` writes it): ") + plan.summary())
    print(json.dumps({"set" if apply else "would_set": sorted(plan.set)}, indent=2))
    if apply and plan.set:
        vm.update_env(plan.set)
        print("Saved to the VM's settings. They apply on the next boot: restart the server (or start it) to use them.")
    return 0


AGENT_COMMANDS = ("tail", "search", "stats", "restart-game", "backups", "restore-game")


def agent(vm, cmd: str, args: list[str], env: dict[str, str]) -> int:
    """Send a request to the VM's ops agent and wait for its reply on serial 3."""
    if vm.status().status != "RUNNING":
        print("The VM is asleep; the ops agent only runs while it's on. Use `start` first.")
        return 1
    if cmd == "search" and len(args) > 2:
        args = [args[0], " ".join(args[1:])]      # a regex may contain spaces
    req_id = uuid.uuid4().hex[:12]
    vm.set_metadata("ops-request", json.dumps({"id": req_id, "cmd": cmd, "args": args}))
    start, end = f"=== ops-reply {req_id} ===", f"=== ops-end {req_id} ==="
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        time.sleep(3)
        out = vm.serial_output(3).replace("\r\n", "\n").replace("\r", "")
        if end in out:
            reply = scrub(out[out.index(start) + len(start):out.index(end)].strip("\n"), env)
            print(reply)
            return 1 if reply.startswith("error:") else 0       # the agent reports a failure as "error: ..."
    print("No reply from the VM's ops agent within 3 minutes. It's installed at boot, so a VM "
          "booted before it existed needs one restart.")
    return 1


PORTAL_URL = os.environ.get("PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app")


def home() -> int:
    """The portal's view of skippy-pc (reporter, Mentat + Qwen, Bilby, services)."""
    req = urllib.request.Request(f"{PORTAL_URL}/ops/home",
                                 headers={"Authorization": f"Bearer {os.environ['OPS_ID_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(json.dumps(json.loads(r.read()), indent=2))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "home":                   # doesn't touch the VM
        return home()
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
    elif cmd == "configure":
        return configure(vm, args, env)
    elif cmd == "start":
        ok, message = ops.start_games(vm, args, None, is_admin=True)
        print(message)
        return 0 if ok else 1
    elif cmd == "stop":
        print(ops.stop_server(vm)[1])
    elif cmd == "restart":
        print(ops.restart_server(vm)[1])
    elif cmd in PORTAL_TASKS:
        return portal_task(vm, cmd)
    elif cmd in AGENT_COMMANDS:
        return agent(vm, cmd, args, env)
    else:
        print(f"unknown command {cmd!r}\n{__doc__}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
