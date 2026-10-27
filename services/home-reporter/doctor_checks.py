"""The doctor's tests (the Captain's recovery design, 2026-10-23): is each service on skippy-pc actually DOING its job, not just running?

The home reporter runs these with every report (side by side, under the report's deadline) and sends the answers to the portal,
which is the doctor's brain (portal/doctor.py): it decides what is broken, and asks the starter on the Windows host to heal it.
Each test answers in a few seconds or it fails, so a service that is up but hung (a model that froze, a loop that never returns)
counts as down, the same as one that crashed.

A test is a GET with a deadline and a check of what came back (a status, a `ready` flag). The ones that would load a model onto the
GPU on every report (an Ollama generation) are not here: deep() runs those, on demand (a repair's last step), so the doctor never
fills the card that today's crashes came from.

  name          the compose service the portal may heal (or "bridge": the private Skippy's bridge on the host)
  url           where to ask (from the reporter's own settings; a service with no URL here is not tested)
  ok(body)      does the answer mean it works?
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

TIMEOUT_S = 5.0
HUNG_S = 120          # a job running with no progress this long: hung (PLATINUM section 24)


def _get(url: str, timeout: float = TIMEOUT_S, data: dict | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET",
                                 headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(200_000)
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else None)
    except urllib.error.HTTPError as e:               # a 503 that says WHY (the readiness contract) is an answer, not "no answer"
        raw = e.read(200_000) if hasattr(e, "read") else b""
        try:
            return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else None)
        except ValueError:
            return e.code, None


def contract(body) -> dict | None:
    """The readiness contract (core/ready.py), if this answer is one: {state, jobs, stuck_s, detail}. state is ready, busy, hung or not-ready.
    A bare "ok" or a 200 is NOT this: the portal shows such a service as answering but unverified (PLATINUM section 24)."""
    if not (isinstance(body, dict) and "ready" in body and "accepting" in body and isinstance(body.get("jobs"), dict)):
        return None
    jobs, stuck = int(body["jobs"].get("running") or 0), int(body.get("stuck_s") or 0)
    if body["ready"] is not True:
        state = "not-ready"
    elif jobs and stuck >= HUNG_S:
        state = "hung"
    else:
        state = "busy" if jobs else "ready"
    return {"state": state, "jobs": jobs, "stuck_s": stuck, "detail": str(body.get("detail") or "")[:160]}


def ready_verdict(body, legacy=None):
    """The doctor's verdict for a service that serves the contract: true only when it says it is ready and is not hung; else why, in its own words."""
    c = contract(body)
    if c is None:
        return legacy(body) if legacy else True
    if c["state"] == "not-ready":
        return c["detail"] or "it says it isn't ready"
    if c["state"] == "hung":
        return f"hung: {c['jobs']} job{'s' if c['jobs'] != 1 else ''} running, no progress for {c['stuck_s']} s"
    return True


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).rstrip("/")


def checks() -> dict:
    """{name: (url, ok(body) -> bool or a reason string)} for every service this reporter knows how to reach."""
    out = {
        "flask-api": (_env("SKIPPY_API", "http://flask-api:5000") + "/health",
                      lambda b: isinstance(b, dict) and b.get("status") == "ok" or "its /health isn't ok"),
        "ollama": (_env("OLLAMA_URL", "http://ollama:11434") + "/api/tags",
                   lambda b: isinstance(b, dict) and isinstance(b.get("models"), list) or "it didn't list its models"),
        "whisper": (_env("WHISPER_URL", "http://whisper:8000") + "/health", lambda b: True),
    }
    optional = {
        "music-studio": ("MUSIC_URL", "/health", lambda b: True),
        "image-studio": ("IMAGE_URL", "/health", lambda b: isinstance(b, dict) and b.get("ready") is True or "it isn't ready"),
        "mentat-home": ("MENTAT_URL", "/health", lambda b: ready_verdict(b)),
        "music-gate": ("MUSIC_GATE_URL", "/health", lambda b: ready_verdict(b)),
        "video-gate": ("VIDEO_GATE_URL", "/health", lambda b: ready_verdict(b)),
        "voice-gate": ("VOICE_GATE_URL", "/health", lambda b: ready_verdict(b, lambda b: isinstance(b, dict) and b.get("ready") is True or "Whisper isn't answering behind it")),
        "bridge": ("CLAUDE_BRIDGE_URL", "/health",
                   lambda b: isinstance(b, dict) and b.get("status") == "ok" or "its /health isn't ok"),
    }
    for name, (env, path, ok) in optional.items():
        base = _env(env)
        if base:
            out[name] = (base + path, ok)
    return out


def run_one(name: str, url: str, ok, get=_get, clock=time.monotonic) -> dict:
    """{"ok", "ms", "why"} for one test. Never raises: a timeout, a refusal or a wrong answer is a failed test with its reason."""
    t0 = clock()
    c = None
    try:
        status, body = get(url)
        c = contract(body)
        verdict = ok(body) if status == 200 else ((c or {}).get("detail") or f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001 — every way of not answering is a failed test
        verdict = "no answer in time" if "timed out" in str(exc).lower() else f"no answer ({type(exc).__name__})"
    ms = int((clock() - t0) * 1000)
    out = {"ok": verdict is True, "ms": ms, "why": "" if verdict is True else str(verdict)[:120]}
    if c:                                               # what the service said about itself: ready, busy (jobs running) or hung
        out.update(state=c["state"], jobs=c["jobs"], stuck_s=c["stuck_s"])
    return out


def deep(get=_get) -> dict:
    """The tests that do real work on the GPU, for a repair's last step only: Ollama writes a few words (then unloads the model)."""
    out = {}
    base = _env("OLLAMA_URL", "http://ollama:11434")
    model = _env("OLLAMA_MODEL", "qwen3:8b")
    out["ollama"] = run_one("ollama", base + "/api/generate", lambda b: isinstance(b, dict) and bool(b.get("response")) or "it wrote nothing",
                            get=lambda url: get(url, timeout=120, data={"model": model, "prompt": "Say OK.", "stream": False,
                                                                        "keep_alive": 0, "options": {"num_predict": 5}}))
    return out
