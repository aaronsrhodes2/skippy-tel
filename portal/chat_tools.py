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


def mentat_gap(out) -> dict | None:
    """None when Mentat modelled the question. When it answered but could not (sigma-ground's `simulate` returns
    value None with a note saying why: not a scenario it can model, or one it recognised but could not run), the gap:
    {"reason": its note, "route": how it was read, "tag": its provenance tag}. `out` is the tool's JSON text or the dict.
    An outage (Mentat or Qwen down) is not a gap: nothing was asked of the physics."""
    try:
        result = json.loads(out) if isinstance(out, str) else out
    except ValueError:
        return None
    if not isinstance(result, dict) or "value" not in result or result["value"] is not None:
        return None
    if mentat_suggestion(result):            # Mentat asked for an input (it suggests, never fills in): not a gap
        return None
    inputs = result.get("inputs") if isinstance(result.get("inputs"), dict) else {}
    return {"reason": str(result.get("notes") or "no reason given"), "route": str(inputs.get("route") or ""),
            "tag": str(result.get("provenance_tag") or "")}


def mentat_suggestion(out) -> dict | None:
    """Mentat's clarification when the question left an input out: {needs, suggested: {name: {value, units, why,
    label}}, sentence, follow_up: {question, given}}. Mentat never fills an input in by itself (the Captain, 2026-10-23):
    it suggests, and only a "yes" sends the follow-up, which computes. None for an answer, a gap or an error."""
    try:
        result = json.loads(out) if isinstance(out, str) else out
    except ValueError:
        return None
    clar = result.get("clarification") if isinstance(result, dict) else None
    if not isinstance(clar, dict) or not isinstance(clar.get("follow_up"), dict):
        return None
    return clar


def clean_follow_up(follow) -> dict | None:
    """The follow-up the page sends back with a "yes": {question, given} as Mentat made it, size-capped, plain values."""
    if not isinstance(follow, dict):
        return None
    question = str(follow.get("question") or "").strip()[:2000]
    given = follow.get("given")
    if not question or not isinstance(given, dict) or not given or len(given) > 40:
        return None
    clean = {}
    for k, v in given.items():
        if not isinstance(k, str) or len(k) > 60 or isinstance(v, bool) or not isinstance(v, (int, float, str)):
            return None
        clean[k] = v if not isinstance(v, str) else v[:120]
    return {"question": question, "given": clean}


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
                   "steps and their sources); or, when the question leaves out an input, a clarification "
                   "(value null) that SUGGESTS a value for each one and computes nothing; or, when it "
                   "can't model the question, a note saying what it CAN do (value null). Mentat never "
                   "fills an input in by itself. Only after the user agrees to a suggestion, call this "
                   "again with the clarification's follow_up: its question, and its given values exactly.",
    "input_schema": {"type": "object",
                     "properties": {"question": {"type": "string",
                                                 "description": "The user's question, verbatim."},
                                    "given": {"type": "object",
                                              "description": "Only after the user said yes to Mentat's "
                                                             "suggestion: the clarification's follow_up.given, "
                                                             "copied exactly. Never invent values here.",
                                              "additionalProperties": {"type": ["number", "string"]}}},
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

_MAKE_IMAGE = {
    "name": "make_image",
    "description": "Make a picture: free, no tokens. Give a vivid, specific description (subject, setting, style, lighting, "
                   "composition). The picture shows as an image card under your reply; don't paste links or write [IMAGE: ...]. "
                   "If it isn't ready yet the result says so: tell the user and use image_result later. Only when the user "
                   "asks for a picture.",
    "input_schema": {"type": "object",
                     "properties": {"prompt": {"type": "string", "description": "What to draw, in detail."},
                                    "orientation": {"type": "string", "enum": ["square", "landscape", "portrait"]}},
                     "required": ["prompt"], "additionalProperties": False},
}

_IMAGE_RESULT = {
    "name": "image_result",
    "description": "Check on a picture started earlier with make_image that wasn't ready when it returned.",
    "input_schema": {"type": "object", "properties": {"job_id": {"type": "string"}},
                     "required": ["job_id"], "additionalProperties": False},
}

IMAGE_WAIT_S = 25      # how long make_image holds the chat; a picture that takes longer (skippy-core draws for quality, not
                       # speed) comes as a "drawing" card that fills in by itself when it's done (portal/pictures.py)
_SIZES = {"square": (1024, 1024), "landscape": (1216, 832), "portrait": (832, 1216)}

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
    ("image", "make_image", "Make a picture", "Pictures"),
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
                 music=None, home_mentat=None, home_music=None, home_image=None, image_allow=None, trace: str = ""):
        self.vm, self.is_admin, self.who = vm, is_admin, who
        self.trace = trace                    # this chat turn's id: Mentat keeps it with the research record
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
        self.home_image = home_image if tool_policy.allowed("image_gen", is_admin) else None
        # image_allow() spends one of a guest's pictures for the day (the Captain's never runs out); None = no limit.
        self.image_allow = image_allow
        self.image_ok = bool(self.home_image)
        self.media: list[dict] = []           # audio made this turn: the chat page plays it

    def available(self) -> list[str]:
        names = [n for n in self.names if n != "start_game" or self.visible]
        if self.mentat_ok:
            names.append("ask_mentat")
        if self.music:
            names.append("now_playing")
        if self.home_music:
            names += ["make_music", "music_result"]
        if self.image_ok:
            names += ["make_image", "image_result"]
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
        if self.image_ok:
            out += [_MAKE_IMAGE, _IMAGE_RESULT]
        return out

    def run(self, name: str, args: dict) -> str:
        if name not in self.available():
            raise ToolError(f"{name} isn't available to you.")
        return json.dumps(getattr(self, "_" + name)(args or {}), default=str)[:20_000]

    def _ask_mentat(self, args) -> dict:
        question = str(args.get("question") or "").strip()
        if not question:
            raise ToolError("ask_mentat needs the question.")
        given = args.get("given") if isinstance(args.get("given"), dict) else None
        if given:
            follow = clean_follow_up({"question": question, "given": given})
            if follow is None:
                raise ToolError("given must be the clarification's follow_up.given, copied exactly.")
            given = follow["given"]
        try:
            result = self.home_mentat.ask_mentat(question, given=given, trace=self.trace)
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

    def _make_image(self, args) -> dict:
        prompt = str(args.get("prompt") or "").strip()[:900]
        if not prompt:
            raise ToolError("make_image needs a description of the picture.")
        width, height = _SIZES.get(str(args.get("orientation") or "square"), _SIZES["square"])
        if self.image_allow and not self.image_allow():
            raise ToolError("That's today's pictures used up (10 a day). Say so, and that it resets tomorrow.")
        job_id = self.home_image.start("make_image", owner=self.who, prompt=prompt, width=width, height=height)
        return self._image_status(job_id, IMAGE_WAIT_S)

    def _image_result(self, args) -> dict:
        job_id = str(args.get("job_id") or "")
        if not self.home_image:
            raise ToolError("There's no picture still being drawn at home.")
        owner = self.home_image.owner_of(job_id)
        if owner is not None and owner != self.who and not self.is_admin:
            raise ToolError("That isn't your picture.")
        return self._image_status(job_id, 5)

    def _image_status(self, job_id: str, wait: float) -> dict:
        state = self.home_image.wait_result(job_id, wait)
        if state["state"] == "done":
            result = state["result"] if isinstance(state.get("result"), dict) else {}
            if not any(m["job_id"] == job_id for m in self.media):
                self.media.append({"kind": "image", "job_id": job_id, "url": f"/chat/media/{job_id}",
                                   "title": str(result.get("title") or "your picture")[:80]})
            return {"status": "done",
                    "note": "The picture appears under your reply. Don't paste a link or write [IMAGE: ...]."}
        if state["state"] == "running":
            if not any(m["job_id"] == job_id for m in self.media):     # the page shows a "drawing" card that fills in by itself
                self.media.append({"kind": "image", "job_id": job_id, "url": f"/chat/media/{job_id}", "pending": True,
                                   "title": "your picture"})
            return {"status": "drawing", "job_id": job_id,
                    "note": "Skippy's studio is still drawing it (it takes its time, for quality). Say so in a few words: the "
                            "picture will appear under your reply by itself when it's done. Don't promise a time."}
        if state["state"] == "error":
            raise ToolError(f"The picture failed: {state['error']}. Say so plainly.")
        raise ToolError("That picture job is gone (it expired, or the portal restarted). Offer to make it again.")

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
