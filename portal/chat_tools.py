"""Tools Skippy can use in the portal chat: the same server controls as the
dashboard, with the same rights (ops.py holds the rules).

Everyone signed in: server_status, start_game, stop_server — as on the
dashboard. Admins also get restart_server and server_logs; for players it isn't offered at
all, so the model can't even try it.

Tool output never includes settings values (passwords live in games-env):
only status, games, timings and how to connect.
"""
from __future__ import annotations

import html
import json
import re

import diagnostics
import ops
from catalog import GAMES

_TAGS = re.compile(r"<[^>]+>")

_SCHEMAS = {
    "server_status": {
        "name": "server_status",
        "description": "Current state of the game server VM: asleep/starting/online, which games it "
                       "runs, minutes since boot, when each game is usually joinable, how to connect, "
                       "and the idle auto-sleep setting. Use it whenever asked whether a game is up.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "start_game": {
        "name": "start_game",
        "description": "Wake the sleeping server with the chosen games. Refused if it's already running "
                       "or a game is missing required admin settings; the result says why.",
        "input_schema": {
            "type": "object",
            "properties": {"games": {"type": "array", "items": {"type": "string", "enum": list(GAMES)},
                                     "minItems": 1}},
            "required": ["games"],
            "additionalProperties": False,
        },
    },
    "stop_server": {
        "name": "stop_server",
        "description": "Stop the server for everyone (worlds are saved first). Only after the user "
                       "has explicitly confirmed in this conversation.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "server_logs": {
        "name": "server_logs",
        "description": "Admin diagnostics: the latest snapshot of the game containers (status, last "
                       "log lines, listening game ports, memory, disk) and the last boot's startup "
                       "script output. Use it to work out why a game isn't reachable. Passwords are masked.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "restart_server": {
        "name": "restart_server",
        "description": "Restart the server: applies saved settings and pulls game/image updates. "
                       "Disconnects everyone. Only after the user has explicitly confirmed.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

_EVERYONE = ("server_status", "start_game", "stop_server")
_ADMIN = _EVERYONE + ("restart_server", "server_logs")


class ToolError(Exception):
    """Reported back to the model as an is_error tool result."""


def _plain(markup: str) -> str:
    return html.unescape(_TAGS.sub("", markup))


class ChatTools:
    def __init__(self, vm, is_admin: bool):
        self.vm, self.is_admin = vm, is_admin
        self.names = _ADMIN if is_admin else _EVERYONE

    def schemas(self) -> list[dict]:
        return [_SCHEMAS[n] for n in self.names]

    def run(self, name: str, args: dict) -> str:
        if name not in self.names:
            raise ToolError(f"{name} isn't available to you.")
        return json.dumps(getattr(self, "_" + name)(args or {}))

    def _server_status(self, _args) -> dict:
        s = self.vm.status()
        running = s.status == "RUNNING"
        up = s.minutes_up()
        games = {}
        for g in s.games:
            if g not in GAMES:
                continue
            game = GAMES[g]
            info = {"name": game.name, "usually_joinable_after_boot_min": game.ready_minutes}
            if running:
                info["joinable_now"] = up is None or up >= game.ready_minutes
                info["how_to_connect"] = _plain(game.connect(s.external_ip or "?", s.env))
            games[g] = info
        return {
            "state": {"RUNNING": "online", "TERMINATED": "asleep"}.get(s.status, s.status.lower()),
            "games": games if running else {},
            "last_selected_games": [] if running else [GAMES[g].name for g in s.games if g in GAMES],
            "minutes_since_boot": up,
            "sleeps_after_idle_minutes": s.idle_minutes,
            "join_password": "shown to signed-in users on the portal dashboard (Show join password)",
        }

    def _start_game(self, args) -> dict:
        ok, message = ops.start_games(self.vm, list(args.get("games") or []), None, self.is_admin)
        if not ok:
            raise ToolError(message)
        return {"started": True, "message": message}

    def _stop_server(self, _args) -> dict:
        return {"stopped": True, "message": ops.stop_server(self.vm)[1]}

    def _server_logs(self, _args) -> dict:
        logs = diagnostics.read(self.vm)
        return {k: v[-6000:] for k, v in logs.items()}

    def _restart_server(self, _args) -> dict:
        return {"restarted": True, "message": ops.restart_server(self.vm)[1]}
