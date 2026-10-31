"""The doctor's brain (the Captain's recovery design, 2026-10-23): keep everything at its known good baseline by itself, and put it all
back with ONE operation when asked.

The portal is the brain because it is the one part that can't go down (Cloud Run, Google's side). It sees:
  * skippy-pc's tests, in every home report (services/home-reporter/doctor_checks.py): does each service do its job, in time?
  * the starter on the Windows host (starter.py, tools/skippy_starter.py): is Docker up, how many containers, is the bridge there?
  * Skippy Core, which it tests itself over the Funnel (a real five-token reply, every five minutes: core_test below)
  * skippy-pc's silence: no report for minutes while the starter still answers means the reporter itself is stuck

and it acts through the starter, which runs on Windows itself and so works when Docker is down:
  heal <name>   one thing back to good: a compose service recreated (stop, rm, up --build), the bridge restarted, Docker restarted
                clean, or Skippy Core repaired over SSH. Sent by itself when a test has failed for HEAL_AFTER_S in a row, at most
                MAX_TRIES times an hour per thing; after that it stops trying and DMs the Captain once.
  repair        the button: everything back to baseline at once (repair() below says what that is)

It never heals while the Captain has stopped the stack on purpose (the starter's "back on at" time is set), and the starter itself
refuses to heal a service that someone is building at that moment (a session's own rebuild). Code is never reverted here: undoing a
change is the private Skippy's job, from a chat that works.
"""
from __future__ import annotations

import threading
import time

HEAL_AFTER_S = 300          # failing this long, test after test, before anything is touched (a rebuild takes a minute or three)
MAX_TRIES = 3               # heals per thing per hour; then a DM, and it waits for the next hour
SILENT_HEAL_S = 240         # no home report this long while the starter answers: the reporter itself is stuck
CORE_EVERY_S = 300          # how often the portal tests Skippy Core
CORE_TIMEOUT_S = 120        # Core may be busy with a long reply: a test waits its turn this long
UNREACHABLE_DM_S = 600      # skippy-pc and its starter both silent this long: tell the Captain (nothing here can fix it)
# No "ollama": since 2026-10-25 it is skippy-core's shim, which skippy-core's own doctor and deployer keep up.
HEALABLE = ("flask-api", "whisper", "music-studio", "image-studio", "music-gate", "video-gate",
            "voice-gate", "tts-studio", "home-reporter", "bridge", "docker", "core", "openaudible")
LABELS = {"flask-api": "Skippy's API", "ollama": "Ollama (local models)", "whisper": "Whisper (hearing)", "music-studio": "Music studio",
          "image-studio": "Picture studio", "mentat-home": "Mentat", "music-gate": "Audio service", "video-gate": "Video service",
          "voice-gate": "Voice", "tts-studio": "Local voices", "home-reporter": "Home reporter", "bridge": "Private Skippy's bridge",
          "docker": "Docker", "openaudible": "OpenAudible (web server)", "core": "Skippy Core", "portal": "Portal", "starter": "Starter (skippy-pc's own)"}


class Doctor:
    def __init__(self, home, starter, notify=None, clock=time.time):
        self.home, self.starter, self.notify, self.clock = home, starter, notify or (lambda text: False), clock
        self._lock = threading.Lock()
        self.checks: dict[str, dict] = {}          # name -> {ok, why, ms, since, at}
        self.tries: dict[str, list[float]] = {}    # name -> heal times this hour
        self.told: dict[str, float] = {}           # name -> when the Captain was last DMed about it
        self.actions: list[dict] = []              # the doctor's own log, newest last: what it did and why
        self.core_at = 0.0
        self.core_running = False
        self.repairing: dict | None = None         # the button's run: {at, by, steps, done, ok}

    # ── what it sees ──
    def _set(self, name: str, ok: bool, why: str = "", ms: int | None = None) -> None:
        now = self.clock()
        old = self.checks.get(name)
        since = old["since"] if old and old["ok"] == ok else now
        self.checks[name] = {"ok": ok, "why": "" if ok else str(why)[:160], "ms": ms, "since": since, "at": now}

    def observe_home(self, tests) -> None:
        """A home report's tests (services/home-reporter/doctor_checks.py)."""
        if not isinstance(tests, dict):
            return
        with self._lock:
            for name, t in list(tests.items())[:40]:
                if isinstance(name, str) and name in HEALABLE and isinstance(t, dict):
                    self._set(name, t.get("ok") is True, str(t.get("why") or ""), t.get("ms") if isinstance(t.get("ms"), int) else None)

    def observe_core(self, ok: bool, why: str = "", ms: int | None = None) -> None:
        with self._lock:
            self._set("core", ok, why, ms)

    def core_due(self) -> bool:
        """Time to test Skippy Core again? (The caller runs core_test on a thread and reports with observe_core.)"""
        with self._lock:
            if self.core_running or self.clock() - self.core_at < CORE_EVERY_S:
                return False
            self.core_at, self.core_running = self.clock(), True
            return True

    def core_done(self) -> None:
        with self._lock:
            self.core_running = False

    # ── what it does by itself ──
    def _log(self, what: str, why: str, by: str = "doctor") -> None:
        self.actions = (self.actions + [{"at": self.clock(), "what": what, "why": why[:200], "by": by}])[-30:]

    def _maintenance(self) -> bool:
        """The Captain stopped the stack on purpose (a heavy task): nothing is healed until it is back."""
        return bool((self.starter.status or {}).get("resume_at")) if self.starter else False

    def _try(self, name: str, why: str) -> bool:
        """Heal `name` if it may be tried now (the hourly allowance); DM the Captain once when the allowance is used up."""
        now = self.clock()
        tries = [t for t in self.tries.get(name, []) if now - t < 3600]
        if len(tries) >= MAX_TRIES:
            if name not in self.told or now - self.told[name] > 3600:
                self.told[name] = now
                self.notify(f"Skippy's doctor: {LABELS.get(name, name)} is still failing after {MAX_TRIES} tries this hour "
                            f"({why}). It will try again next hour; Admin > Recovery has the details.")
            return False
        try:
            self.starter.request("heal", "doctor", arg=name)
        except ValueError:
            return False                                     # already waiting, or the queue is full: next look
        self.tries[name] = tries + [now]
        self._log(f"heal {name}", why)
        return True

    def tick(self) -> list[str]:
        """One look (called with every home report and every starter call): heal what has been failing long enough. Returns what it healed."""
        healed = []
        if self.starter is None or not self.starter.online() or self._maintenance() or self.repairing and not self.repairing.get("done"):
            self._unreachable()
            return healed
        now = self.clock()
        with self._lock:
            status = self.starter.status or {}
            if isinstance(status.get("openaudible"), bool):  # the starter probes it itself: it listens on the host's loopback only
                self._set("openaudible", status["openaudible"], "" if status["openaudible"] else
                          ("the app is running but its web server is off" if status.get("openaudible_app") else "the app is not running"))
            if status.get("docker") is False:                # Docker itself: the starter restarts it clean
                if self._try("docker", "Docker isn't answering"):
                    healed.append("docker")
                return healed
            age = self.home.age() if self.home else None
            if age is not None and age > SILENT_HEAL_S and status.get("docker"):
                if self._try("home-reporter", f"no report for {age // 60} min"):
                    healed.append("home-reporter")
            for name, c in self.checks.items():
                if c["ok"] or now - c["since"] < HEAL_AFTER_S:
                    continue
                if name == "bridge" and status.get("busy"):
                    continue                                 # mid-run: never pulled out from under a task
                if name == "openaudible" and status.get("openaudible_app"):
                    # running, web server off: a preference only the Captain sets, and the app may be converting his library.
                    # Never touched; he is told once in a while.
                    if now - self.told.get(name, -1e12) > 6 * 3600:
                        self.told[name] = now
                        self.notify("Skippy's doctor: OpenAudible is running but its web server is not answering. I left the app alone "
                                    "(it may be converting). Turn it on in OpenAudible > Preferences > Web Server.")
                    continue
                if self._try(name, c["why"] or "failing"):
                    healed.append(name)
        return healed

    def _unreachable(self) -> None:
        """skippy-pc and its starter both silent: nothing here can reach it (off, asleep, no internet). Tell the Captain once."""
        if self.starter is None or self.starter.online():
            return
        age = self.home.age() if self.home else None
        if age is not None and age < UNREACHABLE_DM_S:
            return
        now = self.clock()
        if "skippy-pc" not in self.told or now - self.told["skippy-pc"] > 6 * 3600:
            self.told["skippy-pc"] = now
            self.notify("Skippy's doctor: skippy-pc and its starter have both been silent for over 10 minutes. The PC is off, asleep, "
                        "at the sign-in screen, or without internet: nothing remote can fix that.")

    # ── the button ──
    def repair(self, by: str, failing_only: bool = False) -> dict:
        """Everything back to its known good baseline, as one operation (the starter does skippy-pc's part, and Skippy Core's over
        SSH): Docker answering (restarted clean if hung), main's latest pulled into skippy-pc's checkout when that is a fast-forward
        (never discarding anything), every container up from it (rebuilt where it changed), whatever is failing recreated, the bridge
        restarted when it isn't mid-run, Skippy Core repaired. The portal's own part (a redeploy when it is behind main or its last
        deploy failed, stuck agent records cleared) is done by the caller (recovery_web.py)."""
        failing = sorted(n for n, c in self.checks.items() if not c["ok"] and n not in ("docker", "core", "bridge", "openaudible"))
        cid = self.starter.request("repair", by, arg=",".join(failing))
        with self._lock:
            self.repairing = {"at": self.clock(), "by": by, "id": cid, "done": False, "ok": None, "summary": ""}
            self.tries.clear()                               # a fresh start: the hourly allowances begin again
            self._log("repair", "the Captain pressed Put everything back" if by != "doctor" else "", by)
        return self.repairing

    def repair_result(self) -> dict | None:
        r = self.repairing
        if r and not r["done"] and self.starter:
            got = self.starter.result(r["id"])
            if got is not None:
                r.update(done=True, ok=got["ok"], summary=got["summary"])
        return r

    # ── what the card shows ──
    def view(self) -> dict:
        now = self.clock()
        rows = []
        starter_ok = bool(self.starter and self.starter.online())
        rows.append({"name": "starter", "label": LABELS["starter"], "ok": starter_ok,
                     "why": "" if starter_ok else "not answering: the PC is off, asleep, at sign-in, or the starter isn't installed"})
        if self.starter and starter_ok:
            docker = (self.starter.status or {}).get("docker")
            rows.append({"name": "docker", "label": LABELS["docker"], "ok": bool(docker), "why": "" if docker else "not answering"})
        age = self.home.age() if self.home else None
        home_ok = age is not None and age < 60
        rows.append({"name": "home-reporter", "label": LABELS["home-reporter"], "ok": home_ok,
                     "why": "" if home_ok else ("never heard from" if age is None else f"silent for {age // 60} min")})
        with self._lock:
            for name, c in sorted(self.checks.items(), key=lambda kv: LABELS.get(kv[0], kv[0])):
                rows.append({"name": name, "label": LABELS.get(name, name), "ok": c["ok"], "why": c["why"],
                             "for_s": int(now - c["since"]), "ms": c["ms"], "stale": now - c["at"] > 180})
            actions = [{**a, "ago_s": int(now - a["at"])} for a in reversed(self.actions[-8:])]
        return {"rows": rows, "ok": all(r["ok"] for r in rows), "actions": actions, "maintenance": self._maintenance(),
                "repair": self.repair_result()}


RESIDENT = ("chat", "coder", "vision")      # Skippy Core's always-loaded models (core/models.json "resident")


def core_loaded(base_url: str, key: str, get=None) -> bool:
    """Is llama-swap up with every resident model loaded and ready? Its /running answers in milliseconds without touching the GPU, so a
    box that is merely slow (a video or 3D pop-up rendering on the same GPU) says yes here while its replies crawl."""
    import json
    import urllib.request

    def _get(url, headers, timeout):
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return json.loads(r.read(200_000) or b"{}")
    try:
        got = (get or _get)(base_url.rstrip("/") + "/running", {"x-api-key": key, "Authorization": "Bearer " + key}, 15)
    except Exception:  # noqa: BLE001 — no answer at all: it really is down
        return False
    ready = {m.get("model") for m in (got.get("running") or []) if isinstance(m, dict) and m.get("state") == "ready"}
    return all(name in ready for name in RESIDENT)


def core_test(base_url: str, key: str, model: str = "chat", post=None, get=None) -> tuple[bool, str, int]:
    """Skippy Core writes a few tokens, within CORE_TIMEOUT_S: (ok, why, ms). Straight to its Messages API (llama-swap behind the
    Funnel), capped at 8 tokens, so a model that loads, thinks or loops forever is a failed test, not a hung portal."""
    import json
    import urllib.request
    if not (base_url and key):
        return False, "Skippy Core isn't configured", 0
    t0 = time.monotonic()

    def _post(url, body, headers, timeout):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read(100_000) or b"{}")
    try:
        status, got = (post or _post)(base_url.rstrip("/") + "/v1/messages",
                                      {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "Say OK. /no_think"}]},
                                      {"x-api-key": key, "Authorization": "Bearer " + key, "anthropic-version": "2023-06-01",
                                       "Content-Type": "application/json"}, CORE_TIMEOUT_S)
        ms = int((time.monotonic() - t0) * 1000)
        if status != 200:
            return False, f"HTTP {status}", ms
        wrote = any(isinstance(b, dict) and (b.get("text") or b.get("thinking")) for b in got.get("content") or [])
        return wrote, "" if wrote else "it answered with nothing", ms
    except Exception as exc:  # noqa: BLE001 — every way of not answering is a failed test...
        why = "no answer in time" if "timed out" in str(exc).lower() else f"no answer ({type(exc).__name__})"
        ms = int((time.monotonic() - t0) * 1000)
        # ...unless it is only slow: every model loaded and ready means a long job is sharing the GPU (a video pop-up), and healing it
        # would reload every model while the job carries on (2026-10-24: twice). Busy is healthy; the next test looks again.
        if core_loaded(base_url, key, get):
            return True, "busy: slow to reply while the GPU works, every model loaded", ms
        return False, why, ms
