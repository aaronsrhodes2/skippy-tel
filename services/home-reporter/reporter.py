"""Home reporter: pushes skippy-pc's status and Bilby's music state to the
cloud portal every few seconds, and carries back the portal's control
requests (skip, pause…). Outbound HTTPS only, so nothing at home is exposed.

It authenticates with SKIPPY_HOME_TOKEN, a token made for this one job: the
Captain presses "Pair home" on the portal dashboard and puts the line it shows
into the vault .env. The portal keeps only its hash. Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PORTAL = os.environ.get("PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app").rstrip("/")
API = os.environ.get("SKIPPY_API", "http://flask-api:5000").rstrip("/")
MAC = os.environ.get("BILBY_MAC_URL", "").rstrip("/")         # Mac DJ assistant, if enabled
MENTAT = os.environ.get("MENTAT_URL", "").rstrip("/")         # Mentat + Qwen on skippy-pc
INTERVAL = float(os.environ.get("REPORT_INTERVAL", "15"))
FAST_INTERVAL = 1.5          # job polling while someone is chatting on the portal
IMPORTS_DIR = os.environ.get("IMPORTS_DIR", "/imports")      # game databases to carry to the VM
CHUNK = 8 * 1024 * 1024      # Cloud Run caps a request at 32 MB
ART_MAX = 400_000
MUSIC = os.environ.get("MUSIC_URL", "").rstrip("/")           # music-studio on skippy-pc (ACE-Step on the GPU)
OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
GPU = threading.Lock()       # one heavy GPU job at a time: a song and a chat model can't share the card
MUSIC_TIMEOUT = 600          # seconds a song may take
MUSIC_POLL = 2.0              # seconds between asking the studio how the song is coming
MUSIC_MODEL = os.environ.get("MUSIC_MODEL", "acestep-v15-turbo")   # 8-step turbo model
CLIP_MAX = 25_000_000        # the portal takes up to 30 MB; a request may be 32 MB
GATE = os.environ.get("MUSIC_GATE_URL", "").rstrip("/")        # music-gate: the Music app's library (inside)
GATE_PUBLIC = os.environ.get("MUSIC_GATE_PUBLIC", "").rstrip("/")   # ... and where Tailscale Funnel shows it (outside)
BRIDGE = os.environ.get("CLAUDE_BRIDGE_URL", "").rstrip("/")   # Claude Code's bridge on the host: the private Skippy
WORKSHOP_WAIT = 5            # seconds the bridge holds a request open waiting for news from the run
WORKSHOP_PUSH = 100          # events per push to the portal (a request may be 1 MB)
WORKSHOP_MAX_S = 3 * 3600    # a run this old is abandoned
TELEMETRY_PULL = 200         # portal events fetched per pull (site telemetry; the portal caps it at 500)
TELEMETRY_BATCH = 100_000    # bytes of events per POST to flask-api, which refuses a body over 128 KB
TELEMETRY_CYCLE_S = 20       # the most one collection may take: reports must stay on schedule
TELEMETRY_HUNT = 16          # POSTs one cycle may spend halving a batch flask-api answers 500 to, before it has stored anything
TELEMETRY_HUNT_MORE = 120    # ... and once it has (it is up and writing, so the fault is in particular events)
TELEMETRY_RECHECKS = 2       # times a lone event that got a 500 is sent again before it is called one flask-api will not store


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


def bilby_state() -> dict | None:
    """Bilby as SkippyTel sees it: Mac/Traktor decks, the Mac's pushed state,
    the local library on skippy-pc, or Drive (flask-api's /bilby/status picks),
    reshaped to the portal's deck view."""
    st = fetch(f"{API}/bilby/status")
    if not st:
        return None
    loaded = st.get("loaded") or {}
    now = st.get("now_playing")
    deck = st.get("playing_deck")
    if now and deck and "deck" not in now:
        now = {**now, "deck": deck}
    return {"source": st.get("source"), "online": st.get("source") not in (None, "none"),
            "message": st.get("message"), "now_playing": now,
            "deck_a": loaded.get("a"), "deck_b": loaded.get("b"),
            "playing_a": deck == "a", "playing_b": deck == "b"}


def core_health() -> dict | None:
    """flask-api's /health, trimmed to what the dashboard's Core widget shows
    (the model list already rides in `status`). None means the core didn't answer."""
    h = fetch(f"{API}/health")
    if not h:
        return None
    models = h.get("models") or {}
    ollama = models.get("ollama") or {}
    return {"status": h.get("status"), "spec_version": h.get("spec_version"),
            "bridge": h.get("bridge"), "hung_tiers": h.get("hung_tiers"), "tiers": models.get("tiers"),
            "ollama": {k: ollama.get(k) for k in ("available", "default", "default_present")}}


def gather() -> dict:
    status = fetch(f"{API}/status.json", timeout=10)
    services = fetch(f"{API}/services") or {}
    session = fetch(f"{API}/music/session") or {}
    bilby = bilby_state()
    # Setlist and next suggestion only exist on the Mac's DJ assistant.
    mac_up = bool(MAC) and bool(bilby) and bilby.get("source") == "traktor"
    setlist = (fetch(f"{MAC}/api/setlist") or {}).get("setlist") if mac_up else None
    up_next = fetch(f"{MAC}/bilby/next") if mac_up else None
    return {
        "status": status,
        "health": core_health(),
        "services": [{k: s.get(k) for k in ("id", "name", "available", "status_detail")}
                     for s in services.get("services", [])],
        "music_session": {k: session.get(k) for k in
                          ("mode", "status", "now_playing", "queue_remaining", "source")} if session else None,
        "bilby": bilby,
        "setlist": (setlist or [])[-20:],
        "up_next": (up_next or {}).get("track") if (up_next or {}).get("ok") else None,
        "mentat_home": bool(MENTAT) and _up(f"{MENTAT}/health"),
        "capabilities": capabilities(),
        # What people did on the portal lately (flask-api's site_events table); the Activity widget on /me shows it.
        "site_activity": fetch(f"{API}/telemetry/site_summary"),
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


def workshop_capability() -> dict:
    """The private Skippy is ready when the bridge is up and has its token; `model` is what it will use."""
    if not BRIDGE:
        return {"ready": False}
    workshop_info = (fetch(f"{BRIDGE}/health") or {}).get("workshop") or {}
    return {"ready": workshop_info.get("ready") is True, "model": str(workshop_info.get("model") or "")[:40]}


def capabilities() -> dict:
    """What this PC can do for the portal's chat right now. The portal offers only what's ready."""
    return {"make_music": {"ready": bool(MUSIC) and _up(f"{MUSIC}/health")}, "workshop": workshop_capability(),
            "music_gate": {"ready": bool(GATE and GATE_PUBLIC) and _up(f"{GATE}/health"), "url": GATE_PUBLIC}}


def free_gpu() -> None:
    """Ask Ollama to unload its models so a song gets the whole card (they reload on the next request)."""
    for model in (fetch(f"{OLLAMA}/api/ps") or {}).get("models") or []:
        fetch(f"{OLLAMA}/api/generate", "POST", {"model": model.get("name"), "keep_alive": 0}, timeout=30)


def _download(url: str, limit: int) -> tuple[bytes, str]:
    """GET a clip from the studio, refusing to read more than `limit` bytes."""
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read(limit + 1), r.headers.get_content_type()


def make_music(job: dict, token: str) -> dict:
    """ACE-Step's REST server (music-studio): release a task, poll it, fetch the mp3, and hand
    the audio to the portal, which plays it in the chat."""
    if not MUSIC:
        return {"error": "music isn't set up on skippy-pc"}
    try:
        seconds = int(job.get("duration_s") or 45)
    except (TypeError, ValueError):
        seconds = 45
    seconds = max(10, min(120, seconds))
    prompt = str(job.get("prompt") or "")[:500]
    ask = {"prompt": prompt, "lyrics": str(job.get("lyrics") or "").strip()[:3000] or "[Instrumental]",
           "audio_duration": seconds, "inference_steps": 8, "batch_size": 1, "audio_format": "mp3",
           "thinking": True, "use_random_seed": True, "model": MUSIC_MODEL}
    with GPU:
        free_gpu()
        started = fetch(f"{MUSIC}/release_task", "POST", ask, timeout=30)
        task = ((started or {}).get("data") or {}).get("task_id")
        if not task:
            return {"error": "the music studio didn't take the job" + ("" if started else " (no answer)")}
        clip, deadline = None, time.time() + MUSIC_TIMEOUT
        while time.time() < deadline:
            time.sleep(MUSIC_POLL)
            polled = fetch(f"{MUSIC}/query_result", "POST", {"task_id_list": [task]}, timeout=30)
            items = (polled or {}).get("data") or []
            item = items[0] if items and isinstance(items[0], dict) else None
            if not item or item.get("status") == 0:
                continue                                    # not there yet (or one missed poll)
            try:
                parts = json.loads(item.get("result") or "[]")
            except ValueError:
                parts = []
            first = parts[0] if parts and isinstance(parts[0], dict) else {}
            if item.get("status") == 1 and first.get("file"):
                clip = first
                break
            return {"error": str(first.get("error") or "the music studio couldn't make that song")[:200]}
        if clip is None:
            return {"error": f"the song took longer than {MUSIC_TIMEOUT // 60} minutes"}
        file = str(clip["file"])
        url = MUSIC + (file if file.startswith("/v1/audio") else "/v1/audio?path=" + urllib.parse.quote(file, safe=""))
        try:
            audio, ctype = _download(url, CLIP_MAX)
        except Exception as e:  # noqa: BLE001
            return {"error": f"couldn't fetch the song from the music studio: {e}"}
    if len(audio) > CLIP_MAX:
        return {"error": "the clip came out too big to send"}
    title = prompt[:60]
    up = urllib.request.Request(f"{PORTAL}/home/media/{job.get('id')}?title={urllib.parse.quote(title)}", data=audio,
                                method="PUT", headers={"Content-Type": ctype, "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(up, timeout=120) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        return {"error": f"couldn't hand the clip to the portal: {e}"}
    try:
        length = round(float((clip.get("metas") or {}).get("duration") or seconds), 1)
    except (TypeError, ValueError):
        length = float(seconds)
    return {"result": {"title": title, "seconds": length}}


def _bridge(method: str, path: str, body: dict | None, token: str, timeout: float = 40) -> tuple[int | None, dict]:
    """A call to the bridge with the home token. (None, {}) means it didn't answer at all."""
    req = urllib.request.Request(f"{BRIDGE}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json",
                                                         "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except Exception:  # noqa: BLE001 — down, refused, timed out
        return None, {}


def workshop(job: dict, token: str) -> dict:
    """The private Skippy: hand the Captain's message to Claude Code on the bridge, then relay the run's events
    to the portal until it ends. The bridge keeps every event, so a portal deploy or a reporter restart loses
    nothing: the portal's reply says how many events it holds and this sends what it's missing."""
    task, conv, message, who = job.get("id"), job.get("conv"), job.get("message"), job.get("who")

    def report(start: int, events: list, state: str, error: str | None = None) -> dict | None:
        req = urllib.request.Request(
            f"{PORTAL}/home/workshop/{task}", method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            data=json.dumps({"who": who, "conv": conv, "start": start, "events": events,
                             "state": state, "error": error}).encode())
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or b"{}")
        except Exception:  # noqa: BLE001 — the portal is down or redeploying; the bridge holds the events
            return None

    def fail(reason: str) -> dict:
        for _ in range(3):
            if report(0, [], "error", reason) is not None:
                break
            time.sleep(1)
        return {"error": reason}

    if not BRIDGE:
        return fail("The private Skippy isn't set up on skippy-pc (CLAUDE_BRIDGE_URL).")
    status, started = _bridge("POST", "/workshop", {"task": task, "conv": conv, "message": message}, token)
    if status != 200:
        return fail({409: "Skippy is still working on the last one.",
                     401: "The bridge doesn't accept this token: is the vault's SKIPPY_HOME_TOKEN the paired one?",
                     503: "The bridge has no SKIPPY_HOME_TOKEN configured.",
                     None: "The bridge on skippy-pc isn't running (make bridge)."}.get(status)
                    or started.get("message") or f"The bridge said HTTP {status}.")
    after, silent, stopped, deadline = 0, 0, False, time.time() + WORKSHOP_MAX_S
    while time.time() < deadline:
        status, run = _bridge("GET", f"/workshop/run/{task}?after={after}&wait={WORKSHOP_WAIT}", None, token,
                              timeout=WORKSHOP_WAIT + 15)
        if status == 404:
            return fail("The bridge lost this task (it was restarted).")
        if status != 200:
            silent += 1
            if silent >= 20:
                return fail("Lost contact with the bridge on skippy-pc.")
            time.sleep(2)
            continue
        silent = 0
        events, upto, done = run.get("events") or [], int(run.get("next") or 0), bool(run.get("done"))
        chunk = events[:WORKSHOP_PUSH]
        final = done and len(events) <= WORKSHOP_PUSH
        state = ("done" if run.get("ok") else "error") if final else "running"
        reply = report(after, chunk, state, (run.get("error") if state == "error" else None))
        if reply is None:
            time.sleep(2)
            continue
        if reply.get("stop") and not stopped:
            stopped = _bridge("POST", f"/workshop/cancel/{task}", {}, token)[0] == 200
        have, before = reply.get("have"), after
        after = min(have, after + len(chunk)) if isinstance(have, int) and have >= 0 else after + len(chunk)
        if final and after >= upto:
            return {"result": {"state": state}}
        if after == before and not chunk:
            time.sleep(0.5)                     # nothing moved: don't spin
    return fail("Gave up waiting for the run to end.")


def run_job(job: dict, token: str = "") -> dict:
    """A job from the portal. ask_mentat → Mentat + Qwen here at home; make_music → the music studio;
    workshop → the private Skippy (Claude Code on the bridge)."""
    if job.get("kind") == "workshop":
        return workshop(job, token)
    if job.get("kind") == "make_music":
        return make_music(job, token)
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
            outcome = run_job(job, self.token)
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


class Uploads:
    """The portal asks for a game's database (`imports_wanted`); if the export
    is here (IMPORTS_DIR/<name>.sql.gz), send it in chunks, one at a time."""

    def __init__(self, token: str):
        self.token, self.busy = token, set()

    def want(self, names: list) -> None:
        for name in names or []:
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]{1,32}", name):
                continue                      # a plain game id, never a path
            path = os.path.join(IMPORTS_DIR, f"{name}.sql.gz")
            if name not in self.busy and os.path.isfile(path):
                self.busy.add(name)
                threading.Thread(target=self._send, args=(name, path), daemon=True).start()

    def _put(self, name: str, offset: int, total: int, sha: str, chunk: bytes) -> dict:
        req = urllib.request.Request(
            f"{PORTAL}/home/imports/{name}?offset={offset}&total={total}&sha256={sha}", data=chunk,
            method="PUT", headers={"Content-Type": "application/octet-stream",
                                   "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
            if e.code == 409:                   # portal has a different amount: resume from there
                return body
            raise RuntimeError(body.get("error") or f"HTTP {e.code}") from e

    def _send(self, name: str, path: str) -> None:
        try:
            total = os.path.getsize(path)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(CHUNK), b""):
                    h.update(block)
            sha, offset = h.hexdigest(), 0
            print(f"home-reporter: uploading {name} ({total // 1_000_000} MB)", flush=True)
            with open(path, "rb") as f:
                while offset < total:
                    f.seek(offset)
                    reply = self._put(name, offset, total, sha, f.read(CHUNK))
                    offset = int(reply.get("have", 0))
                    if reply.get("complete"):
                        print(f"home-reporter: {name} uploaded; the VM loads it on its next start", flush=True)
                        return
        except Exception as e:  # noqa: BLE001 — the next report asks again
            print(f"home-reporter: {name} upload failed: {e}", flush=True)
        finally:
            self.busy.discard(name)


class SiteTelemetry:
    """Collects what signed-in people did on the portal (portal/telemetry.py) and files it in flask-api's
    SQLite (POST /telemetry/site_events, next to the phone UI's clicks).

    The portal holds the newest events in memory. Each pull says which portal process (`boot`) and the last
    `seq` already stored; the portal forgets everything up to it, so an event is only dropped after it has
    been filed. Progress only moves after flask-api has answered ok, so a flask-api that is down (or a
    reporter restart, which starts again from the portal's copy) loses nothing: flask-api ignores an event it
    already has (same boot and seq). Only a 400/413/422 from flask-api (this batch itself is refused) skips it;
    a 404 (an older flask-api that doesn't have the route yet) or any 5xx is retried, so a rollout in the wrong
    order loses nothing (the portal keeps up to 5000 events meanwhile).

    One exception to "a 5xx is retried for ever": flask-api answers 500 when a database write fails, and it once
    answered 500 to a batch holding one event it could never store (a lone-surrogate string), which the reporter
    re-sent every cycle for ever while nothing behind it was filed. So a 500 (and only a 500: 502/503/504, a 404 or
    no answer at all mean flask-api is away, not that an event is bad) makes `_hunt` halve the batch until the events
    flask-api refuses stand alone. A lone event is skipped only if flask-api has meanwhile STORED other events (it is
    up and writing) and the event still gets a 500 when sent again. If flask-api 500s everything (a broken database)
    nothing is ever skipped: the hunt is bounded, and the next cycle starts again from the same place."""

    def __init__(self, token: str):
        self.token, self.boot, self.after, self.warned = token, "", 0, False
        self.last_status = 0        # HTTP status of flask-api's last answer to a POST (0: none)
        self.poisoned = 0           # events skipped because flask-api will not store them

    def _pull(self) -> dict | None:
        url = f"{PORTAL}/home/telemetry?" + urllib.parse.urlencode(
            {"boot": self.boot, "after": self.after, "limit": TELEMETRY_PULL})
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            self._warn(f"portal said HTTP {e.code} to the telemetry pull" +
                       (" (SKIPPY_HOME_TOKEN doesn't match; pair again on the dashboard)" if e.code == 401 else ""))
            return None
        except Exception as e:  # noqa: BLE001 — portal down or redeploying: the next cycle asks again
            self._warn(f"can't pull telemetry from the portal: {e}")
            return None
        return body if isinstance(body, dict) and isinstance(body.get("events"), list) else None

    def _store(self, events: list) -> bool | None:
        """True: filed. None: flask-api will never take this batch (4xx). False: try again later (`last_status` says why)."""
        self.last_status = 0
        req = urllib.request.Request(f"{API}/telemetry/site_events", data=json.dumps({"events": events}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                self.last_status = r.status
                return bool(json.loads(r.read() or b"{}").get("ok"))
        except urllib.error.HTTPError as e:
            self.last_status = e.code
            if e.code in (400, 413, 422):                # this batch itself is the problem: retrying can't help
                self._warn(f"flask-api refused a telemetry batch (HTTP {e.code}); skipping it")
                return None
            self._warn(f"flask-api said HTTP {e.code} storing telemetry")     # 404 = an older flask-api without the route
            return False
        except Exception as e:  # noqa: BLE001
            self._warn(f"can't reach flask-api to store telemetry: {e}")
            return False

    def _file(self, batch: list) -> tuple[bool, int]:
        """File one batch. (True, n): every event in it is settled (n were stored) and `after` is past it. (False, n): stop
        here and start again from `after` next cycle (n events were stored before that; flask-api ignores their re-send)."""
        stored = self._store(batch)
        if stored is False and self.last_status == 500:
            return self._hunt(batch)
        if stored is False:
            return False, 0
        self.after = max(self.after, batch[-1]["seq"])
        return True, len(batch) if stored else 0             # None: refused for good, skipped

    def _hunt(self, batch: list) -> tuple[bool, int]:
        """flask-api answered 500 to `batch`. Halve it, in seq order, until the events it still refuses are alone; file
        the rest. Then settle each lone event: skipped for good only if flask-api stored something else meanwhile (so it
        is up and writing: the fault is in the event) and the event still gets a 500 when sent again; otherwise the
        cycle stops there and the same events are pulled and tried again. The hunt is bounded (TELEMETRY_HUNT POSTs
        while flask-api has stored nothing, TELEMETRY_HUNT_MORE after), so an outage costs a handful of POSTs a cycle."""
        outcomes: list = []                                   # (events, stored) in seq order; stored False = a lone event that got a 500
        state = {"posts": 0, "up": False}

        def walk(events: list) -> bool:                       # False: stop (out of budget, or flask-api went away)
            if len(events) == 1:
                outcomes.append((events, False))
                return True
            mid = len(events) // 2
            return part(events[:mid]) and part(events[mid:])

        def part(events: list) -> bool:
            if state["posts"] >= (TELEMETRY_HUNT_MORE if state["up"] else TELEMETRY_HUNT):
                return False
            state["posts"] += 1
            stored = self._store(events)
            if stored is not False:
                state["up"] = state["up"] or stored is True
                outcomes.append((events, stored))
                return True
            return self.last_status == 500 and walk(events)

        finished, filed = walk(batch), 0
        for events, stored in outcomes:
            if stored is False:
                verdict = self._recheck(events[0]) if state["up"] else "later"
                if verdict == "later":
                    return False, filed
                filed += verdict == "stored"
            else:
                filed += len(events) if stored else 0
            self.after = max(self.after, events[-1]["seq"])
        return finished, filed

    def _recheck(self, event: dict) -> str:
        """'stored' (it went in this time), 'refused' (flask-api now refuses it outright: settled), 'poison' (still a 500 alone,
        after TELEMETRY_RECHECKS more tries: skipped) or 'later' (flask-api went away: nothing is known)."""
        for _ in range(TELEMETRY_RECHECKS):
            stored = self._store([event])
            if stored is True:
                return "stored"
            if stored is None:
                return "refused"
            if self.last_status != 500:
                return "later"
        self.poisoned += 1
        label = lambda v: str(v)[:16].encode("ascii", "replace").decode()      # noqa: E731 — never print what may hold a lone surrogate
        print(f"home-reporter: skipping a telemetry event flask-api will not store (boot {label(event.get('boot'))} "
              f"seq {label(event.get('seq'))}, {label(event.get('kind'))}): it gets HTTP 500 alone while others are stored", flush=True)
        return "poison"

    def _warn(self, message: str) -> None:
        if not self.warned:                      # once per outage, like the portal reports
            print(f"home-reporter: {message}", flush=True)
            self.warned = True

    @staticmethod
    def chunks(events: list) -> list[list]:
        """Split into POSTs of at most TELEMETRY_BATCH bytes (an event is at most ~15 KB, so one always fits)."""
        out, current, size = [], [], 0
        for e in events:
            n = len(json.dumps(e))
            if current and size + n > TELEMETRY_BATCH:
                out.append(current)
                current, size = [], 0
            current.append(e)
            size += n
        return out + ([current] if current else [])

    def collect(self) -> int:
        """One cycle: pull, file, repeat while the portal has more. Returns how many events were filed."""
        filed, deadline = 0, time.time() + TELEMETRY_CYCLE_S
        while time.time() < deadline:
            pulled = self._pull()
            if pulled is None:
                break
            if pulled.get("boot") != self.boot:              # first pull, or the portal restarted: its counter began again
                self.boot, self.after = str(pulled.get("boot") or ""), 0
            events = [e for e in pulled["events"] if isinstance(e, dict) and isinstance(e.get("seq"), int)]
            for batch in self.chunks(events):
                settled, count = self._file(batch)
                filed += count
                if not settled:
                    return filed                             # nothing past this point moves: it is pulled again
            self.warned = False
            if not events or not pulled.get("more"):
                break
        return filed


def run_commands(commands: list) -> list[dict]:
    done = []
    for c in commands or []:
        fn = COMMANDS.get((c.get("target"), c.get("action")))
        done.append({"id": c.get("id"), "ok": fn is not None and fn() is not None})
    return done


def main() -> None:
    token = home_token()
    jobs = Jobs(token)
    uploads = Uploads(token)
    site = SiteTelemetry(token)
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
            uploads.want(reply.get("imports_wanted"))
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
        try:
            site.collect()                  # what people did on the portal, into flask-api's SQLite
        except Exception as e:  # noqa: BLE001 — never let this stop the reports
            print(f"home-reporter: telemetry collection failed: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
