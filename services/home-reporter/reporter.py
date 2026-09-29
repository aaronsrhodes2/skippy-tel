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


def capabilities() -> dict:
    """What this PC can do for the portal's chat right now. The portal offers only what's ready."""
    return {"make_music": {"ready": bool(MUSIC) and _up(f"{MUSIC}/health")}}


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


def run_job(job: dict, token: str = "") -> dict:
    """A job from the portal. ask_mentat → Mentat + Qwen here at home; make_music → the music studio."""
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
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
