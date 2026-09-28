"""Server actions and the rules around them, shared by the dashboard buttons
and Skippy's chat tools so both give the same answers.

Each action returns (ok, message): the message is written for the person who
asked, and the caller decides how to show it (a flash, a tool result).
"""
from __future__ import annotations

from catalog import GAMES, clamp_idle, missing_settings


def start_games(vm, games: list[str], idle_raw: str | None, is_admin: bool) -> tuple[bool, str]:
    selected = [g for g in games if g in GAMES]
    if not selected:
        return False, "Pick at least one game."
    status = vm.status()
    problems = [p + " An admin can set it on the Admin page."
                for g in selected for p in missing_settings(g, status.env)]
    if problems:
        return False, " ".join(problems)
    if status.status == "RUNNING":
        return False, ("The server is already running. Games can only be changed while it's asleep "
                       "(or by an admin restart).")
    idle = clamp_idle(idle_raw, is_admin)
    vm.start(selected, idle_minutes=idle)
    ready = max(GAMES[g].ready_minutes for g in selected)
    return True, (f"Starting {', '.join(GAMES[g].name for g in selected)} — usually joinable in about "
                  f"{ready} minutes (longer right after a game update). It sleeps after {idle} "
                  "idle minutes; anyone on the portal can wake it again.")


def stop_server(vm) -> tuple[bool, str]:
    vm.stop()
    return True, "Server stopped. Worlds were saved first."


def restart_server(vm) -> tuple[bool, str]:
    status = vm.restart()
    return True, (f"Restarted ({', '.join(status.games) or 'no games'}). Server images and game "
                  "updates are pulled on boot; allow 5–10 minutes.")
