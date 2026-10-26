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
import re
import secrets
import threading
import time

import popups

KEY = "portal-home"
STALE_AFTER = 60          # seconds without a report → "home is silent"
FAST_WINDOW = 300         # after chat activity, the reporter polls for jobs quickly this long
REDELIVER_AFTER = 20      # a job handed out but not answered goes out again after this
MAX_REPORT = 1_000_000    # bytes (album art included)
JOB_TTL = 600             # seconds a job waits for an answer before it's dropped (a job may ask for longer)
KNOWN_CAPABILITIES = ("make_music", "make_image", "make_image_hq", "workshop", "music_gate", "video_gate", "voice_gate", "house")   # what skippy-pc may advertise (and the pop-ups in popups.py); anything else it sends is ignored
_ACTIONS = {("music", "skip"), ("music", "pause"), ("music", "resume"), ("music", "stop")}
PROJECT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")                    # the private Skippy's project ids (projects.json)
GITHUB_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")


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
                 "planned": bool(s.get("planned")), "detail": _text(s.get("status_detail"))} for s in report.get("services") or []]
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
        self.hold = None                 # hold(job, all_jobs) -> True keeps a job back (the patient queue, app.py)

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
            self._jobs = {i: j for i, j in self._jobs.items() if now - j["created"] < j["ttl"]}
            out = []
            for j in sorted(self._jobs.values(), key=lambda x: x["created"]):
                if not j["event"].is_set() and now - j["sent"] >= REDELIVER_AFTER:
                    if self.hold is not None and self.hold(j, self._jobs.values()):
                        continue                           # a patient job: its machine isn't quiet yet (portal/patient.py)
                    j["sent"] = now
                    out.append(j["job"])
        return {"jobs": out, "fast_for_s": max(0, int(self.fast_until - now))}

    def submit(self, kind: str, _owner: str = "", _ttl: int = JOB_TTL, _id: str | None = None, **payload) -> str:
        job_id = _id or secrets.token_hex(8)                  # (_id: a patient job put back in line after a restart keeps its id)
        with self._lock:
            self._jobs[job_id] = {"job": {"id": job_id, "kind": kind, **payload}, "event": threading.Event(),
                                  "outcome": None, "created": self.clock(), "sent": 0.0, "owner": _owner,
                                  "ttl": _ttl}
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

    # ── long jobs (music): start now, look in later ──────────────────────
    def capabilities(self) -> dict[str, bool]:
        """What skippy-pc says it can do right now, from the last report. Only known names count,
        and only while home is reporting: a silent home offers nothing."""
        caps = (self.report or {}).get("capabilities")
        if not self.online() or not isinstance(caps, dict):
            return {}
        return {name: isinstance(caps.get(name), dict) and caps[name].get("ready") is True
                for name in KNOWN_CAPABILITIES + popups.names()}           # + every registered pop-up (popups.py)

    def can(self, name: str) -> bool:
        return bool(self.capabilities().get(name))

    def capability_note(self, name: str, key: str) -> str | None:
        """A short string skippy-pc attached to a ready capability (e.g. the workshop's model), else None."""
        info = ((self.report or {}).get("capabilities") or {}).get(name)
        value = info.get(key) if isinstance(info, dict) and self.can(name) else None
        return value[:40] if isinstance(value, str) else None

    def workshop_projects(self) -> list[dict]:
        """The private Skippy's projects as skippy-pc listed them (services/claude-bridge/projects.json, without paths):
        [{id, name, repo, branch}], checked again here because it comes from whatever holds the home token. Empty while
        home is silent or before a reporter that sends them."""
        info = ((self.report or {}).get("capabilities") or {}).get("workshop")
        raw = info.get("projects") if isinstance(info, dict) and self.online() else None
        out, seen = [], set()
        for p in raw if isinstance(raw, list) else []:
            pid = p.get("id") if isinstance(p, dict) else None
            if not isinstance(pid, str) or not PROJECT_ID.fullmatch(pid) or pid in seen:
                continue
            seen.add(pid)
            repo = p.get("repo") if isinstance(p.get("repo"), str) and GITHUB_REPO.fullmatch(p["repo"]) else ""
            branch = p.get("branch") if isinstance(p.get("branch"), str) and BRANCH.fullmatch(p["branch"]) else "main"
            out.append({"id": pid, "name": _text(" ".join(str(p.get("name") or pid).split()), 60) or pid,
                        "repo": repo, "branch": branch})
        return out[:40]

    def workshop_brains(self) -> list[dict]:
        """The brains skippy-pc's Workshop can think with: [{id, label, ready}], "claude" and "core" only (checked again here).
        Empty while home is silent or before a reporter that sends them."""
        info = ((self.report or {}).get("capabilities") or {}).get("workshop")
        raw = info.get("brains") if isinstance(info, dict) and self.online() else None
        return [{"id": b["id"], "label": _text(" ".join(str(b.get("label") or b["id"]).split()), 40) or b["id"],
                 "ready": b.get("ready") is True}
                for b in (raw if isinstance(raw, list) else []) if isinstance(b, dict) and b.get("id") in ("claude", "core")][:2]

    def capability_reason(self, name: str) -> str | None:
        """Why skippy-pc says a capability is NOT ready, in its own plain words (the bridge's "Claude's login on skippy-pc
        expired: run `claude auth login` there"), else None: nothing said, it IS ready, or home is silent (then the reason is
        the silence, and the report that would carry it is not coming). One short line, since it is shown on a card and
        comes from whatever holds the home token: the template escapes it."""
        info = ((self.report or {}).get("capabilities") or {}).get(name)
        if not self.online() or not isinstance(info, dict) or info.get("ready") is True:
            return None
        reason = info.get("reason")
        reason = " ".join(reason.split())[:200] if isinstance(reason, str) else ""
        return reason or None

    def start(self, kind: str, owner: str = "", ttl: int = JOB_TTL, **payload) -> str:
        """Queue a job for skippy-pc and return its id; `peek`/`wait_result` read it without consuming it."""
        self.touch_chat()
        return self.submit(kind, _owner=owner, _ttl=ttl, **payload)

    def patient_jobs(self) -> dict:
        """(a copy) The queued jobs the patient queue holds back or hands out: id -> {"job", "sent", "created", ...}."""
        with self._lock:
            return {i: dict(j) for i, j in self._jobs.items() if j["job"].get("kind") in ("make_image", "make_image_hq", "make_music", "popup")
                    and not j["event"].is_set()}

    def has(self, job_id: str) -> bool:
        return job_id in self._jobs

    def peek(self, job_id: str) -> dict:
        j = self._jobs.get(job_id)
        if not j:
            return {"state": "unknown"}
        if not j["event"].is_set():
            return {"state": "running", "age_s": int(self.clock() - j["created"]), "handed_out": j["sent"] > 0}
        out = j["outcome"] or {}
        if "result" in out:
            return {"state": "done", "result": out["result"]}
        return {"state": "error", "error": str(out.get("error") or "the job failed")[:300]}

    def wait_result(self, job_id: str, timeout: float) -> dict:
        j = self._jobs.get(job_id)
        if j:
            j["event"].wait(timeout)
        return self.peek(job_id)

    def owner_of(self, job_id: str) -> str | None:
        j = self._jobs.get(job_id)
        return j["owner"] if j else None

    HOUSE_WAIT_S = 30           # a Leviton through its cloud answers in about a second; the reporter picks up within 1.5 s while he chats

    def house_tool(self, tool: str, args: dict, timeout: float = HOUSE_WAIT_S) -> str:
        """One of the house's tools, run on skippy-pc (reporter.house_job): the sentence it answered."""
        self.touch_chat()
        outcome = self.wait(self.submit("house", tool=tool, args=args), timeout)
        if outcome is None:
            raise TimeoutError("skippy-pc didn't answer in time")
        if outcome.get("error"):
            raise RuntimeError(outcome["error"])
        return str((outcome.get("result") or {}).get("text") or "")

    def mentat_ready(self) -> bool:
        """Mentat + Qwen on skippy-pc is up and reporting."""
        return self.online() and bool((self.report or {}).get("mentat_home"))

    # The wait nests OUTSIDE the work: Mentat's own call limit on skippy-pc (MENTAT_CALL_TIMEOUT 90 s, docker-compose.override.yml)
    # plus the reporter picking the job up (1.5 s while someone chats, 15 s otherwise) and a margin. At 75 s a slow answer (Qwen
    # reloading after a picture freed the card) came back after the chat had already said Mentat was offline.
    MENTAT_WAIT_S = 110

    def ask_mentat(self, question: str, timeout: float = MENTAT_WAIT_S, given: dict | None = None, trace: str = "") -> dict:
        """`given`: values the person agreed to after Mentat suggested them. `trace`: this chat turn's id, kept in
        Mentat's research record."""
        self.touch_chat()
        extra = {"given": given} if given else {}
        outcome = self.wait(self.submit("ask_mentat", question=question, trace=trace, **extra), timeout)
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
                "capabilities": self.capabilities(),
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

    def site_activity(self) -> dict | None:
        """flask-api's summary of what people do on the portal (the reporter carries it as `site_activity`;
        telemetry_web.activity_view trims and shows it). Admin only. None until home has sent one."""
        value = (self.report or {}).get("site_activity")
        return value if isinstance(value, dict) else None

    def status(self) -> dict:
        """The old skippy-pc /status page's content. Admin only."""
        r = self.report or {}
        return {"online": self.online(), "age": self.age(), "paired": self.paired(),
                "status": r.get("status") or {}, "services": r.get("services") or [],
                "local": r.get("local") or {}}
