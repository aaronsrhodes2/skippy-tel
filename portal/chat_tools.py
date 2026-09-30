"""Tools Skippy can use in the portal chat: the same server controls as the
dashboard, with the same rights (ops.py holds the rules).

Everyone signed in: server_status, start_game, stop_server — as on the
dashboard. Admins also get restart_server and server_logs; for players it isn't offered at
all, so the model can't even try it.

Tool output never includes settings values (passwords live in games-env):
only status, games, timings and how to connect.

Physics: when Mentat + Qwen on skippy-pc is up (home.py; the reporter carries
the question) and tool_policy allows it (an "everyone" server), Skippy gets ONE
physics tool, ask_mentat, which hands the question in plain English to
Mentat's own interpreter. The model doesn't pick formulas or fill in Mentat's
~220 maths tools. When home or Qwen is down there is no physics tool: the
cloud copy (keywords only) serves the public playground, not the chat.

Music: people with the Bilby grant get now_playing (what Bilby reports).

Local capabilities: things skippy-pc's own hardware does, offered only while home
advertises them (home.can) and tool_policy allows. Today make_music (ACE-Step on the
Captain's GPU, admin only): it starts a job, waits a little, and if the song isn't ready
says so; music_result looks in later. Finished audio rides back through media.py and
shows in the chat as a player (self.media).

Direct verbs: a message starting with "/physics", "/status", … forces that
tool on the first step (see VERBS); the chat's badge panel inserts them.
"""
from __future__ import annotations

import copy
import html
import json
import re

import diagnostics
import ops
import readiness
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

_MAKE_MUSIC = {
    "name": "make_music",
    "description": "Compose a song or instrumental on the Captain's own GPU at home (ACE-Step): free, no "
                   "tokens, about a minute. Give a compact style description (genre, mood, instruments, "
                   "tempo) and, only if wanted, lyrics with [verse]/[chorus] markers; no lyrics = "
                   "instrumental. The finished clip shows as a player under your reply. If it isn't ready "
                   "yet the result says so: tell the user and use music_result later. Only when the user "
                   "actually asks for music to be made.",
    "input_schema": {"type": "object",
                     "properties": {"prompt": {"type": "string", "description": "Style: genre, mood, instruments, tempo."},
                                    "lyrics": {"type": "string", "description": "Optional lyrics; empty = instrumental."},
                                    "duration_s": {"type": "integer", "minimum": 10, "maximum": 120,
                                                   "description": "Seconds; default 45."}},
                     "required": ["prompt"], "additionalProperties": False},
}

_MUSIC_RESULT = {
    "name": "music_result",
    "description": "Check on a song started earlier with make_music that wasn't ready when it returned.",
    "input_schema": {"type": "object", "properties": {"job_id": {"type": "string"}},
                     "required": ["job_id"], "additionalProperties": False},
}

MUSIC_WAIT_S = 100     # how long make_music holds the chat before saying "still working"

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
    ("music", "make_music", "Make music", "Music"),
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
    def __init__(self, vm, is_admin: bool, visible: list[str] | None = None, who: str = "",
                 music=None, home_mentat=None, home_music=None):
        self.vm, self.is_admin, self.who = vm, is_admin, who
        self.visible = list(GAMES) if visible is None else list(visible)   # games this person may see
        self.names = _ADMIN if is_admin else _EVERYONE
        self.music = music                    # callable → home.music(), only with the Bilby grant
        # Physics = Mentat + Qwen on skippy-pc (via the home reporter), or nothing.
        # No fallback to the cloud copy: without Qwen it answers from keywords
        # alone, and the Captain wants a clear "offline" over a degraded answer.
        self.home_mentat = home_mentat if tool_policy.allowed("mentat", is_admin) else None
        self.mentat_where: str | None = None  # "home" once a physics answer came back
        self.mentat_ok = bool(self.home_mentat)
        # Local capabilities (home.py advertises them; tool_policy decides who may use them).
        self.home_music = home_music if tool_policy.allowed("music_gen", is_admin) else None
        self.media: list[dict] = []           # audio made this turn: the chat page plays it

    def available(self) -> list[str]:
        names = [n for n in self.names if n != "start_game" or self.visible]
        if self.mentat_ok:
            names.append("ask_mentat")
        if self.music:
            names.append("now_playing")
        if self.home_music:
            names += ["make_music", "music_result"]
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
        if self.home_music:
            out += [_MAKE_MUSIC, _MUSIC_RESULT]
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
            result = self.home_mentat.ask_mentat(question)
        except (TimeoutError, RuntimeError) as exc:
            raise ToolError(f"Mentat is offline: {exc}. Say so; don't answer it yourself.") from exc
        self.mentat_where = "home"
        return result

    def _now_playing(self, _args) -> dict:
        return self.music()

    def _make_music(self, args) -> dict:
        prompt = str(args.get("prompt") or "").strip()[:500]
        if not prompt:
            raise ToolError("make_music needs a style description (genre, mood, instruments).")
        try:
            seconds = int(args.get("duration_s") or 45)
        except (TypeError, ValueError):
            seconds = 45
        job_id = self.home_music.start("make_music", owner=self.who, prompt=prompt,
                                       lyrics=str(args.get("lyrics") or "").strip()[:3000],
                                       duration_s=max(10, min(120, seconds)))
        return self._music_status(job_id, MUSIC_WAIT_S)

    def _music_result(self, args) -> dict:
        job_id = str(args.get("job_id") or "")
        owner = self.home_music.owner_of(job_id)
        if owner is not None and owner != self.who and not self.is_admin:
            raise ToolError("That isn't your music job.")
        return self._music_status(job_id, 5)

    def _music_status(self, job_id: str, wait: float) -> dict:
        state = self.home_music.wait_result(job_id, wait)
        if state["state"] == "done":
            result = state["result"] if isinstance(state.get("result"), dict) else {}
            if not any(m["job_id"] == job_id for m in self.media):
                self.media.append({"kind": "audio", "job_id": job_id, "url": f"/chat/media/{job_id}",
                                   "title": str(result.get("title") or "your track")[:80],
                                   "seconds": result.get("seconds")})
            return {"status": "done", "seconds": result.get("seconds"),
                    "note": "The player appears under your reply. Don't paste a link."}
        if state["state"] == "running":
            return {"status": "running", "job_id": job_id, "waited_s": state.get("age_s"),
                    "note": "Still generating on the Captain's GPU. Say so, and offer to check again with music_result."}
        if state["state"] == "error":
            raise ToolError(f"Music failed: {state['error']}. Say so plainly.")
        raise ToolError("That music job is gone (it expired, or the portal restarted). Offer to make it again.")

    def _server_status(self, _args) -> dict:
        s = self.vm.status()
        running = s.status == "RUNNING"
        up = s.minutes_up()
        games = {}
        codes, _players, ready, since = diagnostics.live_all(self.vm, s.started_at) if running else ({}, {}, {}, {})
        for g in s.games:
            if g not in GAMES or g not in self.visible:
                continue
            game = GAMES[g]
            info = {"name": game.name, "usually_joinable_after_boot_min": game.ready_minutes}
            if running:
                # The game's own log says when it's joinable; the boot timer only when the log gives no answer.
                info["joinable_now"] = not readiness.is_starting(g, ready.get(g), up, game.ready_minutes,
                                                                 readiness.minutes_since(since.get(g)))
                info["how_to_connect"] = _plain(game.connect(s.external_ip or "?", s.env))
                if g in codes:
                    info["invite_code"] = codes[g]   # what the game asks for is on its page (Game.invite_hint); new each boot
            games[g] = info
        return {
            "state": {"RUNNING": "online", "TERMINATED": "asleep"}.get(s.status, s.status.lower()),
            "games": games if running else {},
            "last_selected_games": [] if running else [GAMES[g].name for g in s.games if g in self.visible],
            "other_games_running": running and any(g not in self.visible for g in s.games),
            "minutes_since_boot": up,
            "sleeps_after_idle_minutes": s.idle_minutes,
            "join_password": "shown to signed-in users on the game's page, on the Password line beside how to join",
        }

    def _start_game(self, args) -> dict:
        wanted = list(args.get("games") or [])
        if any(g not in self.visible for g in wanted):
            raise ToolError("You don't have access to that game.")
        ok, message = ops.start_games(self.vm, wanted, None, self.is_admin, who=self.who, visible=self.visible)
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
