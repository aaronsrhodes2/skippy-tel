"""Home reporter: pushes skippy-pc's status and Bilby's music state to the
cloud portal every few seconds, and carries back the portal's control
requests (skip, pause…). Outbound HTTPS only, so nothing at home is exposed.

It authenticates with SKIPPY_HOME_TOKEN, a token made for this one job: the
Captain presses "Pair home" on the portal dashboard and puts the line it shows
into the vault .env. The portal keeps only its hash. Standard library only.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

PORTAL = os.environ.get("PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app").rstrip("/")
API = os.environ.get("SKIPPY_API", "http://flask-api:5000").rstrip("/")
MAC = os.environ.get("BILBY_MAC_URL", "http://skippy-mac:7334").rstrip("/")
MENTAT = os.environ.get("MENTAT_URL", "").rstrip("/")         # Mentat + Qwen on skippy-pc
INTERVAL = float(os.environ.get("REPORT_INTERVAL", "15"))
FAST_INTERVAL = 1.5          # job polling while someone is chatting on the portal
ART_MAX = 400_000


def home_token() -> str:
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        sys.exit("home-reporter: SKIPPY_HOME_TOKEN isn't set. Press 'Pair home' on the portal "
                 "dashboard and add the line it shows to the vault .env.")
    return token


def fetch(url: str, method: str = "GET", data: dict | None = None, timeout: float = 4.0,
          raw: bool = False):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read(ART_MAX + 1)
            if raw:
                return payload if len(payload) <= ART_MAX else None, r.headers.get("Content-Type", "")
            return json.loads(payload or b"null")
    except Exception:  # noqa: BLE001 — anything down just reports as missing
        return (None, "") if raw else None


def gather() -> dict:
    status = fetch(f"{API}/status.json", timeout=10)
    services = fetch(f"{API}/services") or {}
    session = fetch(f"{API}/music/session") or {}
    mac = fetch(f"{MAC}/bilby/status")
    setlist = (fetch(f"{MAC}/api/setlist") or {}).get("setlist") if mac else None
    up_next = fetch(f"{MAC}/bilby/next") if mac else None
    return {
        "status": status,
        "services": [{k: s.get(k) for k in ("id", "name", "available", "status_detail")}
                     for s in services.get("services", [])],
        "music_session": {k: session.get(k) for k in
                          ("mode", "status", "now_playing", "queue_remaining", "source")} if session else None,
        "bilby": mac,
        "setlist": (setlist or [])[-20:],
        "up_next": (up_next or {}).get("track") if (up_next or {}).get("ok") else None,
        "mentat_home": bool(MENTAT) and _up(f"{MENTAT}/health"),
    }


def _up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def art_for(report: dict) -> dict | None:
    url = ((report.get("bilby") or {}).get("now_playing") or {}).get("art_url")
    if not url:
        return None
    data, ctype = fetch(url, raw=True)
    if not data:
        return None
    return {"key": url, "type": ctype or "image/jpeg", "b64": base64.b64encode(data).decode()}


COMMANDS = {
    ("music", "skip"): lambda: fetch(f"{API}/music/session/skip", "POST", {}),
    ("music", "pause"): lambda: fetch(f"{API}/music/session/control", "POST", {"action": "pause"}),
    ("music", "resume"): lambda: fetch(f"{API}/music/session/control", "POST", {"action": "resume"}),
    ("music", "stop"): lambda: fetch(f"{API}/music/session/control", "POST", {"action": "stop"}),
}


def run_job(job: dict) -> dict:
    """A job from the portal. ask_mentat → Mentat + Qwen here at home."""
    if job.get("kind") == "ask_mentat" and MENTAT:
        reply = fetch(f"{MENTAT}/ask", "POST", {"question": job.get("question", "")}, timeout=100)
        if reply and reply.get("ok"):
            return {"result": reply["result"]}
        return {"error": (reply or {}).get("error") or "Mentat at home didn't answer"}
    return {"error": f"unknown job {job.get('kind')!r}"}


class Jobs:
    """Polls the portal for jobs: every report carries some, and while someone
    is chatting (the portal says `fast_until`) a light poll runs every 1.5 s so
    a physics question isn't stuck waiting for the next report."""

    def __init__(self, token: str):
        self.token, self.fast_until, self._running = token, 0.0, set()
        self._lock = threading.Lock()

    def _post(self, path: str, data: dict | None, method: str = "POST"):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(f"{PORTAL}{path}", data=body, method=method, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")

    def accept(self, reply: dict) -> None:
        self.fast_until = time.time() + float(reply.get("fast_for_s") or 0)
        for job in reply.get("jobs") or []:
            with self._lock:
                if job.get("id") in self._running:
                    continue
                self._running.add(job.get("id"))
            threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def _run(self, job: dict) -> None:
        try:
            outcome = run_job(job)
            self._post(f"/home/jobs/{job['id']}", outcome)
        except Exception as e:  # noqa: BLE001 — the portal times out and falls back
            print(f"home-reporter: job {job.get('id')} failed: {e}", flush=True)
        finally:
            with self._lock:
                self._running.discard(job.get("id"))

    def poll_forever(self) -> None:
        while True:
            if time.time() < self.fast_until:
                try:
                    self.accept(self._post("/home/jobs", None, method="GET"))
                except Exception:  # noqa: BLE001 — the next report carries jobs too
                    pass
                time.sleep(FAST_INTERVAL)
            else:
                time.sleep(1)


def run_commands(commands: list) -> list[dict]:
    done = []
    for c in commands or []:
        fn = COMMANDS.get((c.get("target"), c.get("action")))
        done.append({"id": c.get("id"), "ok": fn is not None and fn() is not None})
    return done


def main() -> None:
    token = home_token()
    jobs = Jobs(token)
    threading.Thread(target=jobs.poll_forever, daemon=True).start()
    sent_art, results, warned = None, [], False
    while True:
        report = gather()
        report["results"], results = results, []
        art_key = ((report.get("bilby") or {}).get("now_playing") or {}).get("art_url")
        if art_key and art_key != sent_art:
            report["art"] = art_for(report)
        body = json.dumps(report).encode()
        req = urllib.request.Request(f"{PORTAL}/home/report", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                reply = json.loads(r.read() or b"{}")
            if report.get("art"):
                sent_art = art_key
            if reply.get("need_art"):
                sent_art = None
            results = run_commands(reply.get("commands"))
            jobs.accept(reply)
            warned = False
        except urllib.error.HTTPError as e:
            if not warned:
                hint = (" (SKIPPY_HOME_TOKEN doesn't match; pair again on the dashboard)"
                        if e.code == 401 else "")
                print(f"home-reporter: portal said HTTP {e.code}{hint}", flush=True)
                warned = True
        except Exception as e:  # noqa: BLE001
            if not warned:
                print(f"home-reporter: can't reach the portal: {e}", flush=True)
                warned = True
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
