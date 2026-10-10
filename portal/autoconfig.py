"""Set the game servers up without the Captain typing anything.

plan() looks at the VM's settings (games-env) and works out what to write so every game can
start: a join password wherever a game has one and it's blank, admin passwords, and the defaults
games/docker-compose.yml already uses, made explicit so the pages show them. It never overwrites
a value that's already set, with one exception: a join password the Captain names on purpose
(join_password=...) replaces the existing ones. What software can't invent (a Player ID from
inside the game) is returned in `needs_you`, never guessed.

The admin page's "Set up automatically" button and `scripts/remote.py configure` both call this,
so they can't disagree. Values are never put in messages or logs: only key names.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

import envfile
from catalog import VALHEIM_DEFAULTS, Game, missing_settings, valheim_password_problems

# What games/docker-compose.yml uses when a setting is blank. Writing them changes nothing about how a
# game runs; it makes the values visible on the pages and in status. Never applied over a set value.
DEFAULTS = {
    "VALHEIM_SERVER_NAME": "Skippy Valheim", "VALHEIM_PUBLIC": "false",
    **VALHEIM_DEFAULTS,       # world name, seed (= valheim_seed.py's), crossplay (= the compose default): tests keep them equal
    "FACTORIO_SAVE_NAME": "skippy",
    "SATISFACTORY_MAX_PLAYERS": "4",
    "DRAGONWILDS_SERVER_NAME": "Skippy", "DRAGONWILDS_WORLD_NAME": "Ashenfall",
    "GARRYSMOD_SERVER_NAME": "Skippy Garry's Mod", "GARRYSMOD_GAMEMODE": "sandbox", "GARRYSMOD_MAP": "gm_construct", "GARRYSMOD_MAX_PLAYERS": "16",
}
PLACEHOLDERS = {"", "change-me"}
MIN_JOIN = 5                                    # scripts/games.sh: "min 5 chars"

_WORDS = ("otter", "ember", "maple", "quartz", "harbor", "willow", "falcon", "cobalt", "meadow", "lantern",
          "juniper", "raven", "cedar", "comet", "thistle", "marble", "sparrow", "copper", "hazel", "delta")


class PlanError(ValueError):
    """The requested join password isn't usable; the message says why (never the value)."""


@dataclass
class Plan:
    set: dict[str, str] = field(default_factory=dict)         # what to write to games-env
    generated: list[str] = field(default_factory=list)        # secrets made up here
    chosen: list[str] = field(default_factory=list)           # join passwords set to the one the Captain named
    defaults: list[str] = field(default_factory=list)         # settings given their default
    needs_you: list[dict] = field(default_factory=list)       # {game, problem}: only a human can fix these

    def summary(self) -> str:
        """Plain English for a flash message or a log line: names of settings, never their values."""
        parts = []
        if self.chosen:
            parts.append("join password set for " + ", ".join(_pretty(k) for k in self.chosen))
        if self.generated:
            parts.append("made up " + ", ".join(_pretty(k) for k in self.generated))
        if self.defaults:
            parts.append(f"filled in {len(self.defaults)} default{'s' if len(self.defaults) != 1 else ''}")
        text = "Set up automatically: " + "; ".join(parts) + "." if parts else "Nothing to set up: every setting is already filled in."
        if self.needs_you:
            text += " Still needs you: " + " ".join(n["problem"] for n in self.needs_you)
        return text


def _pretty(key: str) -> str:
    return key.replace("_", " ").lower()


def _friendly_password() -> str:
    return secrets.choice(_WORDS) + str(secrets.randbelow(900) + 100)


def _join_problem(value: str, game: Game, env: dict[str, str]) -> str | None:
    problem = envfile.check(value)
    if problem:
        return f"it {problem}"
    if len(value) < MIN_JOIN:
        return f"it must be at least {MIN_JOIN} characters"
    name = env.get(f"{game.id.upper()}_SERVER_NAME") or DEFAULTS.get(f"{game.id.upper()}_SERVER_NAME", "")
    if name and (name.lower() in value.lower() or value.lower() in name.lower()):   # Valheim refuses it either way round
        return "it must not contain, or be part of, the server name"
    if game.id == "valheim" and valheim_password_problems(value, env):
        return "it must not appear inside the world's name or seed"
    return None


def plan(env: dict[str, str], games: dict[str, Game], join_password: str | None = None) -> Plan:
    out = Plan()
    merged = dict(env)

    def put(key: str, value: str, bucket: list[str]) -> None:
        out.set[key] = merged[key] = value
        bucket.append(key)

    if join_password is not None:
        join_password = join_password.strip()
        for game in games.values():
            if game.join_password_key:
                why = _join_problem(join_password, game, merged)
                if why:
                    raise PlanError(f"That join password won't work for {game.name}: {why}.")

    for game in games.values():
        for s in game.settings:
            current = merged.get(s.key, "")
            if s.key == game.join_password_key:
                if join_password is not None:
                    if current != join_password:
                        put(s.key, join_password, out.chosen)
                elif current in PLACEHOLDERS:
                    put(s.key, _friendly_password(), out.generated)
            elif s.secret and not s.external and current in PLACEHOLDERS:      # (a token someone else issued can't be made up)
                put(s.key, secrets.token_urlsafe(12), out.generated)
            elif s.key in DEFAULTS and current == "":
                put(s.key, DEFAULTS[s.key], out.defaults)
    for game in games.values():
        for problem in missing_settings(game.id, merged):
            out.needs_you.append({"game": game.id, "problem": problem})
    return out
