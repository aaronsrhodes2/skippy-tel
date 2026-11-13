"""Server actions and the rules around them, shared by the dashboard buttons
and Skippy's chat tools so both give the same answers.

Each action returns (ok, message): the message is written for the person who
asked, and the caller decides how to show it (a flash, a tool result).
"""
from __future__ import annotations

import watch
from catalog import GAMES, clamp_idle, missing_settings
from gcp import CapacityError


SWITCH_OWNER = "Aaron"       # who changes the server's game


def capacity_message(exc: CapacityError) -> str:
    """Google had no room for the VM in its data center (it happens after a stop, for minutes to hours)."""
    return (f"Google has no spare capacity for the server in {exc.zone} right now, on any of the machine sizes "
            f"the portal can use ({', '.join(exc.tried)}). Nothing is broken and nothing was lost; the games "
            "you picked are saved. This usually clears within a few minutes: press Play again shortly.")


def _names(ids, visible: list[str] | None) -> list[str]:
    return [GAMES[g].name for g in ids if g in GAMES and (visible is None or g in visible)]


def switch_message(current: list[str], wanted: list[str], visible: list[str] | None = None) -> str:
    """Why a friend can't start a different game, and who to ask. Names only games they can see."""
    now = " and ".join(_names(current, visible))
    want = " and ".join(_names(wanted, visible)) or "another game"
    lead = f"{now} is the game on the server right now." if now else "The server is set to a different game."
    return f"{lead} To play {want} instead, ask {SWITCH_OWNER} to switch the server."


def stay_on(vm) -> bool:
    """Is the VM in the stay-on state (metadata `stay-on`)? Unreadable means no."""
    try:
        return vm.get_metadata("stay-on") == "1"
    except Exception:   # noqa: BLE001: a start must not fail over a flag it couldn't read
        return False


def set_stay_on(vm, on: bool) -> tuple[bool, str]:
    """Switch stay-on on or off. Never stops, starts or restarts the VM: the watchdog picks it up at its next check."""
    vm.stay_on(on)
    if on:
        return True, ("Stay-on is ON: the server never goes to sleep and has no hour limit (idle-minutes 0, max-hours 0), "
                      "now and after every later start, until `stay-on off`. It bills for every hour it is on.")
    return True, "Stay-on is OFF: the server sleeps after 30 idle minutes and a session ends after 8 hours, as before."


def start_games(vm, games: list[str], idle_raw: str | None, is_admin: bool, who: str = "",
                visible: list[str] | None = None) -> tuple[bool, str]:
    """`visible` is the games this person may see (None: all); it keeps a hidden game's name out of messages."""
    selected = [g for g in games if g in GAMES]
    if not selected:
        return False, "Pick at least one game."
    status = vm.status()
    problems = [p + " An admin can set it on the Admin page."
                for g in selected for p in missing_settings(g, status.env)]
    if problems:
        return False, " ".join(problems)
    different = [g for g in selected if status.games and g not in status.games]
    if different and not is_admin:
        # One game at a time, and only the Captain changes it: a friend may only wake the game that's set.
        watch.record_blocked(vm, who, selected, list(status.games))
        return False, switch_message(list(status.games), different, visible)
    if status.status == "RUNNING":
        if set(selected) - set(status.games):
            watch.record_blocked(vm, who, selected, list(status.games))
        return False, ("The server is already running. Games can only be changed while it's asleep "
                       "(or by an admin restart).")
    # Stay-on (`ops stay-on`, gcp.GamesVm.stay_on) is the Captain's standing order: no start puts the idle timer back.
    idle = 0 if stay_on(vm) else clamp_idle(idle_raw, is_admin)
    try:
        vm.start(selected, idle_minutes=idle)
    except CapacityError as exc:
        return False, capacity_message(exc)
    ready = max(GAMES[g].ready_minutes for g in selected)
    return True, (f"Starting {', '.join(GAMES[g].name for g in selected)} — usually joinable in about "
                  f"{ready} minutes (longer right after a game update). "
                  + ("It stays on until someone stops it (auto-sleep is off)." if idle == 0 else
                     f"It sleeps after {idle} idle minutes; anyone on the portal can wake it again."))


def may_stop(vm, is_admin: bool, visible: list[str]) -> tuple[bool, str]:
    """Stopping kicks everyone off every game on the VM, so a friend may only
    do it when every running game is one of theirs."""
    if is_admin:
        return True, ""
    others = [g for g in vm.status().games if g not in visible]
    if others:
        return False, "Other games you don't have access to are running on this server; it can't be stopped from your account."
    return True, ""


def stop_server(vm) -> tuple[bool, str]:
    vm.stop()
    return True, "Server stopped. Worlds were saved first."


def restart_server(vm, games: list[str] | None = None) -> tuple[bool, str]:
    """Restart the VM. `games` (from a game's own page: just that game) replaces the saved list; None keeps it (the
    Admin page and the chat tool, which say which games that is)."""
    if games is not None:
        games = [g for g in games if g in GAMES]
        if not games:
            return False, "Pick a game."
    try:
        status = vm.restart(games) if games is not None else vm.restart()
    except CapacityError as exc:
        return False, capacity_message(exc)
    return True, (f"Restarted ({', '.join(status.games) or 'no games'}). Server images and game "
                  "updates are pulled on boot; allow 5–10 minutes.")
