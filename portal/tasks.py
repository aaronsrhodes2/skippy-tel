"""Portal-side jobs that remote operators (the `ops` workflow → Claude) can ask
for, when a job needs something only the portal holds, like the Discord bot
token.

A request is written into the games VM's instance metadata (`portal-tasks`),
which only identities that control the VM can do. POST /tasks/run then makes
the portal run exactly what's queued and record the outcome in
`portal-task-results`. The endpoint itself needs no login: it can only run
what the VM's owners queued, and every task is safe to repeat.

    portal-tasks          [{"id": "…", "task": "discord-setup"}]
    portal-task-results   [{"id": "…", "task": "…", "ok": true, "message": "…", "at": "…"}]  (last 20)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

QUEUE, RESULTS, KEEP = "portal-tasks", "portal-task-results", 20


def _load(vm, key) -> list:
    try:
        data = json.loads(vm.get_metadata(key) or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def run_pending(vm, handlers: dict[str, Callable[[], str]]) -> list[dict]:
    """Run every queued task once, record the results, empty the queue."""
    queued = _load(vm, QUEUE)
    if not queued:
        return []
    vm.set_metadata(QUEUE, "[]")      # claim them first so a second caller doesn't repeat them
    done = []
    for item in queued:
        name = str(item.get("task", ""))
        handler = handlers.get(name)
        try:
            ok, message = (True, handler()) if handler else (False, f"unknown task {name!r}")
        except Exception as exc:   # noqa: BLE001 — report it, keep going
            ok, message = False, str(exc)
        done.append({"id": str(item.get("id", "")), "task": name, "ok": ok, "message": message,
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    vm.set_metadata(RESULTS, json.dumps((_load(vm, RESULTS) + done)[-KEEP:]))
    return done
