"""Tell the Captain when the private Skippy is done: one Discord direct message per finished run.

He works from his phone and doesn't sit watching the chat, so when a run ends (done, failed or stopped), or ends on a
question for him, the portal's Discord bot (DISCORD_BOT_TOKEN, the one that already runs the game channels) DMs him one
short line: the project, the first words of his request, the outcome, and the link to /me.

The rules, each one a test:
  * Only ever the Captain: DISCORD_CAPTAIN_ID from the VM settings (games-env) when set, else the owner of the Discord
    server (GET /guilds/{DISCORD_GUILD_ID}: owner_id). Nobody else, whatever a run says.
  * Never code, never secrets, never more than MAX_CHARS: his own words only, cut to a few, with backticks and @ removed,
    and Discord told to ping nobody (allowed_mentions). The agent's text is never quoted.
  * A switch on the chat card turns it off (default on). Kept in VM metadata `portal-workshop-notify`, so a deploy
    doesn't turn it back on.
  * At most one DM per run: workshop.py calls `finished` only when a record it already held as running becomes final in
    this process, and never for a run that finished over DEDUP_S ago (a reporter catching a fresh portal up on an old run);
    a set of tasks already told guards the rest.
  * Failures are swallowed and logged once per kind (a Discord outage must never touch the chat), and sending happens on a
    thread of its own so the reporter's push is answered at once.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
KEY = "portal-workshop-notify"
MAX_CHARS = 200
DEDUP_S = 600                    # a run that finished longer ago than this never DMs (see the module docstring)
OWNER_CACHE_S = 3600
SETTINGS_CACHE_S = 60
_SNOWFLAKE = re.compile(r"[0-9]{15,21}")


def _http(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:              # noqa: S310 — discord.com only
            return resp.status, resp.read(100_000)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def first_words(text: str, limit: int = 60) -> str:
    """His request as a few plain words: one line, no backticks or @, cut at a word."""
    flat = " ".join(str(text or "").replace("`", "").replace("@", "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0] or flat[:limit]
    return cut + "…"


def outcome_of(state: str, error: str | None, last_text: str) -> str:
    if state == "done":
        return "has a question for you" if last_text.rstrip().endswith("?") else "done"
    if (error or "").strip().lower() in ("stopped", "cancelled from the admin page", "cleared from the admin page"):
        return "stopped"
    return "failed"


def message(project_name: str, request: str, outcome: str, portal_url: str) -> str:
    words = first_words(request) or "your request"
    link = (portal_url.rstrip("/") + "/me") if portal_url else "/me"
    head = f"Skippy · {project_name}: “{words}” {outcome}. "
    room = MAX_CHARS - len(link)
    if len(head) > room:                         # the link always fits; the words give way
        head = head[:max(0, room - 2)].rstrip() + "… "
    return (head + link)[:MAX_CHARS]


class Notifier:
    def __init__(self, token: str, vm, portal_url: str = "", transport=None, clock=time.time, background: bool = True,
                 log=None):
        self.__token = (token or "").strip()
        self.vm, self.portal_url, self.clock, self.background = vm, (portal_url or "").strip(), clock, background
        self.transport = transport or _http
        self.log = log or (lambda line: print(line, flush=True))
        self._told: set[str] = set()
        self._logged: set[str] = set()
        self._owner: tuple[float, str] | None = None
        self._settings: tuple[float, dict] | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:                   # never the token
        return f"Notifier(configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self.__token)

    # ── the switch (per admin; default on) ──────────────────────────────────────
    def _off(self) -> set[str]:
        now = self.clock()
        if self._settings and now - self._settings[0] < SETTINGS_CACHE_S:
            return set(self._settings[1].get("off") or [])
        try:
            data = json.loads(self.vm.get_metadata(KEY) or "{}")
        except Exception:  # noqa: BLE001 — unreadable: the default (on)
            data = {}
        data = data if isinstance(data, dict) else {}
        self._settings = (now, data)
        return {e for e in data.get("off") or [] if isinstance(e, str)}

    def enabled(self, owner: str) -> bool:
        return owner not in self._off()

    def set_enabled(self, owner: str, on: bool) -> bool:
        off = self._off()
        off.discard(owner) if on else off.add(owner)
        self.vm.set_metadata(KEY, json.dumps({"off": sorted(off)}))
        self._settings = (self.clock(), {"off": sorted(off)})
        return on

    # ── who ────────────────────────────────────────────────────────────────────
    def _env(self) -> dict:
        try:
            return dict(self.vm.status().env or {})
        except Exception:  # noqa: BLE001
            return {}

    def captain_id(self) -> str | None:
        """DISCORD_CAPTAIN_ID if it is set (and looks like a Discord id), else the Discord server's owner."""
        env = self._env()
        chosen = str(env.get("DISCORD_CAPTAIN_ID") or "").strip()
        if chosen:
            return chosen if _SNOWFLAKE.fullmatch(chosen) else None
        now = self.clock()
        if self._owner and now - self._owner[0] < OWNER_CACHE_S:
            return self._owner[1]
        guild = str(env.get("DISCORD_GUILD_ID") or "").strip()
        if not _SNOWFLAKE.fullmatch(guild):
            return None
        status, raw = self._call("GET", f"/guilds/{guild}")
        try:
            owner = json.loads(raw).get("owner_id") if status == 200 else None
        except ValueError:
            owner = None
        if not isinstance(owner, str) or not _SNOWFLAKE.fullmatch(owner):
            return None
        self._owner = (now, owner)
        return owner

    # ── sending ────────────────────────────────────────────────────────────────
    def _call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
        headers = {"Authorization": f"Bot {self.__token}", "Content-Type": "application/json",
                   "User-Agent": "skippy-portal (https://github.com/aaronsrhodes2/skippy-tel, 1.0)"}
        return self.transport(method, API + path, headers, json.dumps(payload).encode() if payload is not None else None, 15)

    def _warn(self, kind: str, detail: str) -> None:
        if kind not in self._logged:              # once per kind: an outage is one line, not one per run
            self._logged.add(kind)
            self.log(json.dumps({"event": "workshop_notify", "problem": kind, "detail": detail[:200]}))

    def _send(self, text: str) -> bool:
        try:
            user = self.captain_id()
            if user is None:
                self._warn("no_captain", "set DISCORD_CAPTAIN_ID or DISCORD_GUILD_ID in the VM settings")
                return False
            status, raw = self._call("POST", "/users/@me/channels", {"recipient_id": user})
            channel = json.loads(raw).get("id") if status == 200 else None
            if not isinstance(channel, str) or not _SNOWFLAKE.fullmatch(channel):
                self._warn(f"dm_channel_{status}", "Discord wouldn't open a DM with the Captain")
                return False
            status, _ = self._call("POST", f"/channels/{channel}/messages",
                                   {"content": text, "allowed_mentions": {"parse": []}})
            if status not in (200, 201):
                self._warn(f"send_{status}", "Discord refused the DM")
                return False
            return True
        except Exception as exc:  # noqa: BLE001 — never let a notification touch the chat
            self._warn(f"error_{type(exc).__name__}", str(exc))
            return False

    def finished(self, owner: str, task: str, project_name: str, request: str, state: str, error: str | None,
                 last_text: str, finished_age_s: float | None, portal_url: str = "") -> bool:
        """A run the portal held as running has just ended. Returns whether a DM was started. `portal_url` is where the
        portal is reached (PORTAL_URL when configured wins)."""
        if not self.configured or not self.enabled(owner):
            return False
        if isinstance(finished_age_s, (int, float)) and finished_age_s > DEDUP_S:
            return False
        with self._lock:
            if task in self._told:
                return False
            self._told.add(task)
            if len(self._told) > 1000:
                self._told = set(list(self._told)[-500:])
        text = message(project_name, request, outcome_of(state, error, last_text), self.portal_url or portal_url)
        if self.background:
            threading.Thread(target=self._send, args=(text,), daemon=True).start()
            return True
        return self._send(text)
