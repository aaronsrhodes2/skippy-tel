"""What skippy-pc reports about itself: service health, UPS, models, photo
tags, UI telemetry, and Bilby's music state. The home reporter
(services/home-reporter) POSTs it every ~15 s; the portal can't reach home,
so control requests ride back on the reply, and so do jobs (physics
questions for Mentat + Qwen at home): each report picks them up, and while
someone's chatting the reporter also polls /home/jobs every 1.5 s.

State lives in this process (the portal runs one instance), so a fresh
instance shows "waiting" for one report interval. Pairing: the dashboard
makes a token, shows it once, and keeps only its hash in VM metadata
`portal-home`.
"""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import secrets
import threading
import time

KEY = "portal-home"
STALE_AFTER = 60          # seconds without a report → "home is silent"
FAST_WINDOW = 300         # after chat activity, the reporter polls for jobs quickly this long
REDELIVER_AFTER = 20      # a job handed out but not answered goes out again after this
MAX_REPORT = 1_000_000    # bytes (album art included)
_ACTIONS = {("music", "skip"), ("music", "pause"), ("music", "resume"), ("music", "stop")}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _text(value, limit: int = 120) -> str:
    return str(value if value is not None else "")[:limit]


def core_check(report: dict | None) -> dict:
    """skippy-tel-core (the flask-api at home) as the Captain needs to see it:
    a verdict (`ok`, `degraded`, `down`, `unknown` before the first report), a
    plain-English list of what's wrong, the tiers, and the whole service
    manifest. Pure: it only reads what the home reporter sent, and trims it,
    so a bad report can't put anything odd on the dashboard."""
    if report is None or "health" not in report:      # no report yet, or a home reporter older than this widget
        return {"verdict": "unknown", "problems": [], "tiers": [], "services": [], "api_version": None}
    health = report.get("health")                     # present but None = the core itself didn't answer
    ollama = (health or {}).get("ollama") or {}
    tiers_in = (health or {}).get("tiers") or {}
    problems: list[str] = []
    verdict = "ok"

    if not health:
        problems.append("skippy-tel-core isn't answering /health.")
        verdict = "down"
    else:
        if health.get("status") != "ok":
            hung = ", ".join(map(str, health.get("hung_tiers") or []))
            problems.append(f"Core reports {_text(health.get('status'), 30)}" + (f" (hung: {_text(hung)})" if hung else "") + ".")
        if ollama.get("available") is False:
            problems.append("Ollama isn't reachable, so the default (local) tier is down.")
            verdict = "down"
        elif ollama.get("default_present") is False:            # `is False`: an older core doesn't say
            problems.append(f"Skippy's default model {_text(ollama.get('default'), 60)} isn't installed in Ollama, "
                            "so default-tier chats fail (HTTP 503).")
        for name, label in (("claude", "Claude"), ("nagatha", "Nagatha (Gemini)"), ("elevenlabs", "ElevenLabs")):
            if tiers_in.get(name) is False:
                problems.append(f"{label} has no API key on skippy-pc.")
    for c in (report.get("status") or {}).get("services") or []:
        if c.get("ok") is False:
            problems.append(f"{_text(c.get('name'), 40)} is down ({_text(c.get('detail'), 60)}).")
    if problems and verdict == "ok":
        verdict = "degraded"

    tiers = [{"name": "ollama", "ok": ollama.get("available") is True and ollama.get("default_present") is not False},
             {"name": "claude", "ok": tiers_in.get("claude") is True},
             {"name": "nagatha", "ok": tiers_in.get("nagatha") is True},
             {"name": "elevenlabs", "ok": tiers_in.get("elevenlabs") is True},
             {"name": "bridge", "ok": bool((health or {}).get("bridge"))}] if health else []
    services = [{"name": _text(s.get("name") or s.get("id"), 40), "available": bool(s.get("available")),
                 "detail": _text(s.get("status_detail"))} for s in report.get("services") or []]
    return {"verdict": verdict, "problems": problems, "tiers": tiers, "services": services,
            "api_version": (health or {}).get("spec_version")}


class Home:
    def __init__(self, vm, clock=time.time):
        self.vm, self.clock = vm, clock
        self._lock = threading.Lock()
        self.report: dict | None = None
        self.received_at: float | None = None
        self.art: dict | None = None
        self._commands: list[dict] = []
        self._ids = itertools.count(1)
        self._hash_cache: tuple[float, str] | None = None
        self._jobs: dict[str, dict] = {}
        self.fast_until = 0.0

    # ── pairing ──────────────────────────────────────────────────────────
    def pair(self) -> str:
        token = secrets.token_urlsafe(32)
        self.vm.set_metadata(KEY, json.dumps({"token_sha256": _hash(token), "paired_at": int(self.clock())}))
        self._hash_cache = None
        return token

    def _stored_hash(self) -> str:
        now = self.clock()
        if self._hash_cache and now - self._hash_cache[0] < 60:
            return self._hash_cache[1]
        try:
            h = json.loads(self.vm.get_metadata(KEY) or "{}").get("token_sha256", "")
        except Exception:  # noqa: BLE001 — unreadable means unpaired
            h = ""
        self._hash_cache = (now, h)
        return h

    def paired(self) -> bool:
        return bool(self._stored_hash())

    def authorized(self, header: str | None) -> bool:
        stored = self._stored_hash()
        if not stored or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(_hash(header[7:].strip()), stored)

    # ── reports ──────────────────────────────────────────────────────────
    def accept(self, report: dict) -> dict:
        art = report.pop("art", None)
        report.pop("results", None)
        with self._lock:
            self.report, self.received_at = report, self.clock()
            if art and art.get("b64"):
                self.art = art
            commands, self._commands = self._commands, []
            key = self.art_key()
            need_art = bool(key) and (not self.art or self.art.get("key") != key)
        return {"commands": commands, "need_art": need_art, **self.jobs_reply()}

    # ── jobs: work for skippy-pc that someone is waiting on ──────────────
    def touch_chat(self) -> None:
        """Someone's chatting: have the reporter poll for jobs quickly for a while."""
        self.fast_until = self.clock() + FAST_WINDOW

    def jobs_reply(self) -> dict:
        now = self.clock()
        with self._lock:
            self._jobs = {i: j for i, j in self._jobs.items() if now - j["created"] < 600}
            out = []
            for j in self._jobs.values():
                if not j["event"].is_set() and now - j["sent"] >= REDELIVER_AFTER:
                    j["sent"] = now
                    out.append(j["job"])
        return {"jobs": out, "fast_for_s": max(0, int(self.fast_until - now))}

    def submit(self, kind: str, **payload) -> str:
        job_id = secrets.token_hex(8)
        with self._lock:
            self._jobs[job_id] = {"job": {"id": job_id, "kind": kind, **payload}, "event": threading.Event(),
                                  "outcome": None, "created": self.clock(), "sent": 0.0}
        return job_id

    def finish(self, job_id: str, outcome: dict) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j or j["event"].is_set():
                return False
            j["outcome"] = outcome if isinstance(outcome, dict) else {"error": "bad outcome"}
            j["event"].set()
        return True

    def wait(self, job_id: str, timeout: float) -> dict | None:
        j = self._jobs.get(job_id)
        if not j or not j["event"].wait(timeout):
            return None
        with self._lock:
            self._jobs.pop(job_id, None)
        return j["outcome"]

    def mentat_ready(self) -> bool:
        """Mentat + Qwen on skippy-pc is up and reporting."""
        return self.online() and bool((self.report or {}).get("mentat_home"))

    def ask_mentat(self, question: str, timeout: float = 75) -> dict:
        self.touch_chat()
        outcome = self.wait(self.submit("ask_mentat", question=question), timeout)
        if outcome is None:
            raise TimeoutError("Mentat at home didn't answer in time")
        if "result" not in outcome:
            raise RuntimeError(outcome.get("error") or "Mentat at home failed")
        return outcome["result"]

    def age(self) -> int | None:
        return None if self.received_at is None else int(self.clock() - self.received_at)

    def online(self) -> bool:
        age = self.age()
        return age is not None and age < STALE_AFTER

    def art_key(self) -> str | None:
        return (((self.report or {}).get("bilby") or {}).get("now_playing") or {}).get("art_url")

    def current_art(self) -> dict | None:
        art, key = self.art, self.art_key()
        return art if art and key and art.get("key") == key else None

    def queue(self, target: str, action: str) -> bool:
        if (target, action) not in _ACTIONS:
            return False
        with self._lock:
            self._commands = [c for c in self._commands if c["target"] != target][-10:]
            self._commands.append({"id": next(self._ids), "target": target, "action": action})
        return True

    # ── views ────────────────────────────────────────────────────────────
    def music(self) -> dict:
        """What Bilby knows is playing: the DJ decks on the Mac (Traktor) and
        the phone's music session. Safe for anyone with the Bilby grant."""
        r = self.report or {}
        mac = r.get("bilby") or None
        now = (mac or {}).get("now_playing")
        return {
            "online": self.online(),
            "age": self.age(),
            "traktor": {
                "online": bool(mac) and bool((mac or {}).get("online")),
                "source": (mac or {}).get("source"),
                "message": (mac or {}).get("message"),
                "now_playing": {k: v for k, v in now.items() if k != "art_url"} if now else None,
                "deck_a": (mac or {}).get("deck_a"), "deck_b": (mac or {}).get("deck_b"),
                "playing_a": bool((mac or {}).get("playing_a")),
                "playing_b": bool((mac or {}).get("playing_b")),
            },
            "has_art": self.current_art() is not None,
            "up_next": r.get("up_next"),
            "setlist": r.get("setlist") or [],
            "session": r.get("music_session"),
        }

    def diagnostics(self) -> dict:
        """For the ops workflow: is skippy-pc reporting, and is each piece up?"""
        r = self.report or {}
        st, bilby = r.get("status") or {}, r.get("bilby") or {}
        with self._lock:
            pending = sum(1 for j in self._jobs.values() if not j["event"].is_set())
        core = self.core()
        return {"paired": self.paired(), "online": self.online(), "last_report_s": self.age(),
                "mentat_home": bool(r.get("mentat_home")),
                "core": {"verdict": core["verdict"], "problems": core["problems"]},
                "services": {x.get("name"): x.get("ok") for x in st.get("services") or []},
                "manifest": {x.get("id"): x.get("available") for x in r.get("services") or []},
                "ollama_models": st.get("ollama_models"), "ups": st.get("ups"),
                "bilby": {"source": bilby.get("source"), "online": bilby.get("online"),
                          "message": bilby.get("message"), "playing": bool(bilby.get("now_playing"))},
                "music_session": (r.get("music_session") or {}).get("status"),
                "jobs_pending": pending, "fast_for_s": max(0, int(self.fast_until - self.clock()))}

    def core(self) -> dict:
        """skippy-tel-core's health for the dashboard. Admin only."""
        return core_check(self.report)

    def status(self) -> dict:
        """The old skippy-pc /status page's content. Admin only."""
        r = self.report or {}
        return {"online": self.online(), "age": self.age(), "paired": self.paired(),
                "status": r.get("status") or {}, "services": r.get("services") or []}
