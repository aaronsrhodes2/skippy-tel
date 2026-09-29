"""Tools Skippy can use in the portal chat: the same server controls as the
dashboard, with the same rights (ops.py holds the rules).

Everyone signed in: server_status, start_game, stop_server — as on the
dashboard. Admins also get restart_server and server_logs; for players it isn't offered at
all, so the model can't even try it.

Tool output never includes settings values (passwords live in games-env):
only status, games, timings and how to connect.

Physics: when Mentat is reachable and tool_policy allows it (it's an
"everyone" server), Skippy gets ONE physics tool, ask_mentat, which hands the
question in plain English to Mentat's own interpreter. The model doesn't pick
formulas or fill in Mentat's ~220 maths tools; Mentat does that itself.

Music: people with the Bilby grant get now_playing (what Bilby reports).

Direct verbs: a message starting with "/physics", "/status", … forces that
tool on the first step (see VERBS); the chat's badge panel inserts them.
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


_ASK_MENTAT = {
    "name": "ask_mentat",
    "description": "The Captain's physics engine (Mentat, from sigma-ground). Pass the user's physics "
                   "or maths question in their own words, including every number and unit they gave. "
                   "Don't solve it, pick formulas, convert units or add assumptions: Mentat interprets "
                   "the question itself. It returns a worked, self-checked answer (value, units, the "
                   "steps and their sources) or, when it can't model the question, a note saying what "
                   "it CAN do (value null).",
    "input_schema": {"type": "object",
                     "properties": {"question": {"type": "string",
                                                 "description": "The user's question, verbatim."}},
                     "required": ["question"], "additionalProperties": False},
}

_NOW_PLAYING = {
    "name": "now_playing",
    "description": "What's playing right now according to Bilby: the Traktor decks on the Captain's Mac "
                   "(artist, title, BPM, key, genre, elapsed), Bilby's next suggestion, tonight's "
                   "setlist, and the phone's music session. Says when skippy-pc isn't reporting.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

# Direct verbs: "/verb …" at the start of a message forces that tool first.
# (verb, tool, badge label, category)
VERBS = (
    ("status", "server_status", "Server status", "Game server"),
    ("start", "start_game", "Start a game", "Game server"),
    ("stop", "stop_server", "Stop the server", "Game server"),
    ("restart", "restart_server", "Restart", "Game server"),
    ("logs", "server_logs", "Logs", "Game server"),
    ("physics", "ask_mentat", "Physics (Mentat)", "Physics"),
    ("playing", "now_playing", "Now playing", "Music"),
)
_VERB_RE = re.compile(r"^/([a-z]+)\b\s*(.*)$", re.S | re.I)


_ALIASES = {"mentat": "physics"}


def parse_verb(message: str) -> tuple[str | None, str]:
    """("/physics how fast…") → ("ask_mentat", "how fast…"). Unknown verbs pass through."""
    m = _VERB_RE.match(message.strip())
    if m:
        word = _ALIASES.get(m.group(1).lower(), m.group(1).lower())
        for verb, tool, _label, _cat in VERBS:
            if verb == word:
                return tool, m.group(2).strip()
    return None, message


class ChatTools:
    def __init__(self, vm, is_admin: bool, visible: list[str] | None = None, mentat=None, who: str = "",
                 music=None):
        self.vm, self.is_admin, self.who = vm, is_admin, who
        self.visible = list(GAMES) if visible is None else list(visible)   # games this person may see
        self.names = _ADMIN if is_admin else _EVERYONE
        self.mentat = mentat if mentat and tool_policy.allowed("mentat", is_admin) else None
        self.music = music                    # callable → home.music(), only with the Bilby grant
        self.mentat_ok = self._mentat_reachable()

    def _mentat_reachable(self) -> bool:
        if not self.mentat:
            return False
        try:
            self.mentat.tools()                   # cached; a cheap "is it up?"
            return True
        except mentat_client.MentatError:
            return False

    def available(self) -> list[str]:
        names = [n for n in self.names if n != "start_game" or self.visible]
        if self.mentat_ok:
            names.append("ask_mentat")
        if self.music:
            names.append("now_playing")
        return names

    def badges(self) -> dict[str, list[dict]]:
        """The chat's badge panel: {category: [{verb, label}]} for this person's tools."""
        have, out = set(self.available()), {}
        for verb, tool, label, cat in VERBS:
            if tool in have:
                out.setdefault(cat, []).append({"verb": verb, "label": label})
        return out

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
        if self.mentat_ok:
            out.append(_ASK_MENTAT)
        if self.music:
            out.append(_NOW_PLAYING)
        return out

    def run(self, name: str, args: dict) -> str:
        if name not in self.available():
            raise ToolError(f"{name} isn't available to you.")
        return json.dumps(getattr(self, "_" + name)(args or {}), default=str)[:20_000]

    def _ask_mentat(self, args) -> dict:
        question = str(args.get("question") or "").strip()
        if not question:
            raise ToolError("ask_mentat needs the question.")
        try:
            return self.mentat.ask(question)
        except mentat_client.MentatError as exc:
            raise ToolError(str(exc)) from exc

    def _now_playing(self, _args) -> dict:
        return self.music()

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
