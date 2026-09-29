"""Tools Skippy can use in the portal chat: the same server controls as the
dashboard, with the same rights (ops.py holds the rules).

Everyone signed in: server_status, start_game, stop_server — as on the
dashboard. Admins also get restart_server and server_logs; for players it isn't offered at
all, so the model can't even try it.

Tool output never includes settings values (passwords live in games-env):
only status, games, timings and how to connect.

Physics: when Mentat is configured and tool_policy allows it for this
person (it's an "everyone" server), a curated set of Mentat's tools rides
along as `mentat_<name>`, plus `mentat_search` / `mentat_call` to reach the
rest of its ~220 tools on demand.
"""
from __future__ import annotations

import copy
import html
import json
import re

import diagnostics
import mentat_client
import ops
import tool_policy
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


_MENTAT_GENERIC = [
    {"name": "mentat_search",
     "description": "Find Mentat physics/maths tools (the Captain's own Wolfram rival, ~220 tools: "
                    "units, constants, symbolic maths, mechanics, relativity, cosmology, optics, "
                    "thermodynamics, circuits, chemistry, nuclear, materials, simulations) by keywords. "
                    "Returns names, descriptions and input schemas; then use mentat_call.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False}},
    {"name": "mentat_call",
     "description": "Run any Mentat tool by name with its arguments (see mentat_search for schemas). "
                    "Results carry a `source` and `formula`: cite them.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                      "required": ["name"], "additionalProperties": False}},
]


class ChatTools:
    def __init__(self, vm, is_admin: bool, visible: list[str] | None = None, mentat=None, who: str = ""):
        self.vm, self.is_admin, self.who = vm, is_admin, who
        self.visible = list(GAMES) if visible is None else list(visible)   # games this person may see
        self.names = _ADMIN if is_admin else _EVERYONE
        self.mentat = mentat if mentat and tool_policy.allowed("mentat", is_admin) else None
        self._mentat_schemas = self._load_mentat()

    def _load_mentat(self) -> list[dict]:
        """Mentat's favourites + search/call, or nothing if it's unreachable."""
        if not self.mentat:
            return []
        try:
            by_name = {t["name"]: t for t in self.mentat.tools()}
        except mentat_client.MentatError:
            return []
        favs = [{"name": f"mentat_{n}", "description": by_name[n]["description"][:1000],
                 "input_schema": by_name[n]["input_schema"]}
                for n in mentat_client.CHAT_FAVORITES if n in by_name]
        return favs + _MENTAT_GENERIC

    def schemas(self) -> list[dict]:
        out = []
        for name in self.names:
            tool = _SCHEMAS[name]
            if name == "start_game":
                if not self.visible:
                    continue                      # nothing this person may start
                tool = copy.deepcopy(tool)        # offer only the games they have access to
                tool["input_schema"]["properties"]["games"]["items"]["enum"] = self.visible
            out.append(tool)
        return out + self._mentat_schemas

    def run(self, name: str, args: dict) -> str:
        if name.startswith("mentat_") and self._mentat_schemas:
            return json.dumps(self._run_mentat(name, args or {}), default=str)[:20_000]
        if name not in self.names:
            raise ToolError(f"{name} isn't available to you.")
        return json.dumps(getattr(self, "_" + name)(args or {}))

    def _run_mentat(self, name: str, args: dict):
        try:
            if name == "mentat_search":
                return self.mentat.search(str(args.get("query", "")))
            if name == "mentat_call":
                return self.mentat.call(str(args.get("name", "")), args.get("arguments") or {})
            return self.mentat.call(name[len("mentat_"):], args)
        except mentat_client.MentatError as exc:
            raise ToolError(str(exc)) from exc

    def _server_status(self, _args) -> dict:
        s = self.vm.status()
        running = s.status == "RUNNING"
        up = s.minutes_up()
        games = {}
        codes = diagnostics.current_join_codes(self.vm) if running else {}
        for g in s.games:
            if g not in GAMES or g not in self.visible:
                continue
            game = GAMES[g]
            info = {"name": game.name, "usually_joinable_after_boot_min": game.ready_minutes}
            if running:
                info["joinable_now"] = up is None or up >= game.ready_minutes
                info["how_to_connect"] = _plain(game.connect(s.external_ip or "?", s.env))
                if g in codes:
                    info["invite_code"] = codes[g]   # Worlds → Invite Code; new each boot
            games[g] = info
        return {
            "state": {"RUNNING": "online", "TERMINATED": "asleep"}.get(s.status, s.status.lower()),
            "games": games if running else {},
            "last_selected_games": [] if running else [GAMES[g].name for g in s.games if g in self.visible],
            "other_games_running": running and any(g not in self.visible for g in s.games),
            "minutes_since_boot": up,
            "sleeps_after_idle_minutes": s.idle_minutes,
            "join_password": "shown to signed-in users on the portal dashboard (Show join password)",
        }

    def _start_game(self, args) -> dict:
        wanted = list(args.get("games") or [])
        if any(g not in self.visible for g in wanted):
            raise ToolError("You don't have access to that game.")
        ok, message = ops.start_games(self.vm, wanted, None, self.is_admin, who=self.who)
        if not ok:
            raise ToolError(message)
        return {"started": True, "message": message}

    def _stop_server(self, _args) -> dict:
        ok, message = ops.may_stop(self.vm, self.is_admin, self.visible)
        if not ok:
            raise ToolError(message)
        return {"stopped": True, "message": ops.stop_server(self.vm)[1]}

    def _server_logs(self, _args) -> dict:
        logs = diagnostics.read(self.vm)
        return {k: v[-6000:] for k, v in logs.items()}

    def _restart_server(self, _args) -> dict:
        return {"restarted": True, "message": ops.restart_server(self.vm)[1]}
