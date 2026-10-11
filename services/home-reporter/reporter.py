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
GATHER_BUDGET_S = float(os.environ.get("GATHER_BUDGET", "9"))   # the most one report waits for its probes (they run side by side)
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
GATE = os.environ.get("MUSIC_GATE_URL", "").rstrip("/")        # music-gate: Bilby's Audio Service's library (inside)
GATE_PUBLIC = os.environ.get("MUSIC_GATE_PUBLIC", "").rstrip("/")   # ... and where Tailscale Funnel shows it (outside)
VIDEO_GATE = os.environ.get("VIDEO_GATE_URL", "").rstrip("/")         # video-gate: Nagatha's Video Service's Jellyfin (inside)
VIDEO_GATE_PUBLIC = os.environ.get("VIDEO_GATE_PUBLIC", "").rstrip("/")   # ... and its Funnel address (outside, :8443)
VOICE_GATE = os.environ.get("VOICE_GATE_URL", "").rstrip("/")         # voice-gate: Skippy's ears (Whisper) and mouth (inside)
VOICE_GATE_PUBLIC = os.environ.get("VOICE_GATE_PUBLIC", "").rstrip("/")   # ... and its Funnel address (outside, :10000)
BRIDGE = os.environ.get("CLAUDE_BRIDGE_URL", "").rstrip("/")   # Claude Code's bridge on the host: the private Skippy
WORKSHOP_WAIT = 5            # seconds the bridge holds a request open waiting for news from the run
WORKSHOP_PUSH = 100          # events per push to the portal (a request may be 1 MB)
WORKSHOP_MAX_S = 3 * 3600    # a run this old is abandoned
WORKSHOP_RECENT_S = 3600     # a finished run older than this is no longer carried to the portal (the bridge forgets it too)
START_TRIES = 4              # times a run is started on a bridge that doesn't answer (it may be restarting) ...
START_RETRY_S = 3            # ... this many seconds apart
SYNC_INTERVAL = 10           # seconds between the reconciler's looks at the bridge and the portal
SYNC_FAST = 2                # ... while someone is watching the chat
CATCH_UP_STEPS = 60          # pushes one run may take in one reconciler pass (100 events each: more than the 5000 cap)
WATCHDOG_S = 900             # no successful report for this long: this process exits and Docker starts a fresh one
LOST_TASK = "skippy-pc lost this task (the bridge was restarted). Send your message again."
_TASK = re.compile(r"[a-f0-9]{8,32}")
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


class _Probes:
    """The report's probes, run side by side and waited for under ONE deadline.

    Why: the report is also the heartbeat. The portal calls skippy-pc silent after STALE_AFTER (60 s), drops every
    capability and refuses Send (the private Skippy's chat included). These probes used to run one after another
    (/status.json 10 s, five 4 s calls, three 3 s ones, a 4 s bridge look: about 45 s when flask-api or Ollama is slow, say
    while a song generates), and the POST, the telemetry pull and the sleep came after that, so a slow flask-api made the
    portal think the whole PC was gone. Now a probe that misses the budget is simply missing from this report (None, the way
    a service that is down already reads; nothing stale is ever shown as live) and the report goes out on time. A probe
    that raises degrades the same way instead of taking the process down: an odd reply from one source must not be able to
    stop the heartbeat. Its thread is a daemon with its own (short) timeouts, so a slow one finishes by itself."""

    def __init__(self, budget: float):
        self.deadline = time.monotonic() + budget
        self.lock = threading.Lock()
        self.out: dict = {}
        self.threads: dict = {}

    def start(self, name: str, fn) -> None:
        thread = threading.Thread(target=self._run, args=(name, fn), daemon=True)
        self.threads[name] = thread
        thread.start()

    def _run(self, name: str, fn) -> None:
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001 — see the class docstring
            if _probe_failures.get(name) != repr(e):
                _probe_failures[name] = repr(e)
                print(f"home-reporter: the {name} probe failed ({e!r}); reporting it as missing", flush=True)
            return
        _probe_failures.pop(name, None)
        with self.lock:
            self.out[name] = value

    def get(self, name: str, default=None):
        """One probe's answer, waiting for it (within the deadline); `default` when it was late or failed."""
        thread = self.threads.get(name)
        if thread is not None:
            thread.join(max(0.0, self.deadline - time.monotonic()))
        with self.lock:
            return self.out.get(name, default)

    def finish(self) -> dict:
        """Wait for the rest (until the deadline) and return what has answered by now."""
        for thread in list(self.threads.values()):
            thread.join(max(0.0, self.deadline - time.monotonic()))
        with self.lock:
            return dict(self.out)


_probe_failures: dict = {}          # probe name -> the last failure printed, so a persistent one is said once, not every report


def _mac_setlist() -> list | None:
    return (fetch(f"{MAC}/api/setlist") or {}).get("setlist")


def gather() -> dict:
    """Everything one report carries. The shape is fixed (the portal's `core_check`, `capabilities` and the widgets read
    these keys); only how it is collected changed, see `_Probes`."""
    probes = _Probes(GATHER_BUDGET_S)
    probes.start("status", lambda: fetch(f"{API}/status.json", timeout=10))
    probes.start("health", core_health)
    probes.start("services", lambda: fetch(f"{API}/services"))
    probes.start("session", lambda: fetch(f"{API}/music/session"))
    probes.start("bilby", bilby_state)
    probes.start("mentat_home", lambda: bool(MENTAT) and _up(f"{MENTAT}/health"))
    # What people did on the portal lately (flask-api's site_events table); the Activity widget on /me shows it.
    probes.start("site_activity", lambda: fetch(f"{API}/telemetry/site_summary"))
    for name, probe in _capability_probes().items():
        probes.start(f"cap_{name}", probe)
    bilby = probes.get("bilby")
    # Setlist and next suggestion only exist on the Mac's DJ assistant (so they wait for the look at Bilby).
    mac_up = bool(MAC) and bool(bilby) and bilby.get("source") == "traktor"
    if mac_up:
        probes.start("setlist", _mac_setlist)
        probes.start("up_next", lambda: fetch(f"{MAC}/bilby/next"))
    got = probes.finish()
    services = got.get("services") if isinstance(got.get("services"), dict) else {}
    session = got.get("session") if isinstance(got.get("session"), dict) else {}
    setlist = got.get("setlist") if isinstance(got.get("setlist"), list) else None
    up_next = got.get("up_next") if isinstance(got.get("up_next"), dict) else {}
    return {
        "status": got.get("status"),
        "health": got.get("health"),
        "services": [{k: s.get(k) for k in ("id", "name", "available", "status_detail")}
                     for s in services.get("services", []) if isinstance(s, dict)],
        "music_session": {k: session.get(k) for k in
                          ("mode", "status", "now_playing", "queue_remaining", "source")} if session else None,
        "bilby": bilby,
        "setlist": (setlist or [])[-20:],
        "up_next": up_next.get("track") if up_next.get("ok") else None,
        "mentat_home": bool(got.get("mentat_home")),
        "capabilities": {name: got.get(f"cap_{name}") or _capability_down(name) for name in _capability_probes()},
        "site_activity": got.get("site_activity"),
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


_PROJECT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_GITHUB_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
MAX_PROJECTS = 40


def workshop_projects(raw) -> list[dict]:
    """The bridge's project list (projects.json, without paths), checked and cut to what the portal needs. Every report
    carries it, so it stays small: an id, a name, the GitHub repo and its branch."""
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not _PROJECT_ID.fullmatch(str(p.get("id") or "")):
            continue
        repo = str(p.get("repo") or "")
        out.append({"id": p["id"], "name": " ".join(str(p.get("name") or p["id"]).split())[:60],
                    "repo": repo if _GITHUB_REPO.fullmatch(repo) else "",
                    "branch": str(p.get("branch") or "main")[:100]})
    return out[:MAX_PROJECTS]


def workshop_capability() -> dict:
    """The private Skippy is ready when the bridge is up and has its token and Claude's login there isn't known to be
    expired (the bridge decides: `ready`); `model` is what it will use. When it is NOT ready, `reason` is the bridge's own
    plain-English why ("Claude's login on skippy-pc expired: run `claude auth login` there"), or ours when the bridge
    didn't answer at all, so the portal's card can say what to fix instead of only "isn't answering". `login` (ok /
    expired / unknown) rides along for the portal's diagnostics; both are left out when the bridge says nothing."""
    if not BRIDGE:
        return {"ready": False}
    health = fetch(f"{BRIDGE}/health")
    info = (health.get("workshop") if isinstance(health, dict) else None) or {}
    if not isinstance(info, dict):
        info = {}
    out = {"ready": info.get("ready") is True, "model": str(info.get("model") or "")[:40]}
    if info.get("login") in ("ok", "expired", "unknown"):
        out["login"] = info["login"]
    projects = workshop_projects(info.get("projects"))
    if projects:
        out["projects"] = projects                                    # the chat's project picker (ids and names only)
    reason = info.get("reason")
    if not out["ready"]:
        if isinstance(reason, str) and reason.strip():
            out["reason"] = " ".join(reason.split())[:200]            # one plain line: it is shown on the card
        elif health is None:
            out["reason"] = "the bridge on skippy-pc isn't answering (is it running? make bridge)"
    return out


def _capability_probes() -> dict:
    """One probe per capability, so `gather` can run them side by side (each waits at most ~4 s on its own service)."""
    return {"make_music": lambda: {"ready": bool(MUSIC) and _up(f"{MUSIC}/health")},
            "workshop": workshop_capability,
            "music_gate": lambda: {"ready": bool(GATE and GATE_PUBLIC) and _up(f"{GATE}/health"), "url": GATE_PUBLIC},
            "video_gate": video_gate_capability,
            "voice_gate": voice_gate_capability}


def video_gate_capability() -> dict:
    """The video gate's /health always answers while the container runs (it is the liveness probe too); it is only worth a
    tile when its own `ready` says a catalog exists AND Jellyfin answers, so that is what is read, not just a 200."""
    health = fetch(f"{VIDEO_GATE}/health") if VIDEO_GATE and VIDEO_GATE_PUBLIC else None
    return {"ready": isinstance(health, dict) and health.get("ready") is True, "url": VIDEO_GATE_PUBLIC}


def voice_gate_capability() -> dict:
    """Ready when Whisper answers behind the gate (the microphone); `speak` says whether Skippy's voice (flask-api's ElevenLabs) is up too."""
    health = fetch(f"{VOICE_GATE}/health") if VOICE_GATE and VOICE_GATE_PUBLIC else None
    return {"ready": isinstance(health, dict) and health.get("ready") is True, "url": VOICE_GATE_PUBLIC}


def _capability_down(name: str) -> dict:
    """What a capability reads as when its probe was late or failed: not ready (the safe answer), never the last known one."""
    if name == "music_gate":
        return {"ready": False, "url": GATE_PUBLIC}
    if name == "video_gate":
        return {"ready": False, "url": VIDEO_GATE_PUBLIC}
    if name == "voice_gate":
        return {"ready": False, "url": VOICE_GATE_PUBLIC}
    return {"ready": False}


def capabilities() -> dict:
    """What this PC can do for the portal's chat right now. The portal offers only what's ready."""
    return {name: probe() for name, probe in _capability_probes().items()}


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


def _now() -> float:
    """The clock the private Skippy's relay and reconciler read (one place, so the chaos suite can run minutes of outage in milliseconds)."""
    return time.time()


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class _Say:
    """Print a problem when it CHANGES, not every time it is seen again: a portal that is down for an hour is one line,
    not 360. `clear()` (on the next success) lets the same problem be said again if it comes back."""

    def __init__(self):
        self.last = None

    def __call__(self, message: str) -> None:
        if message != self.last:
            self.last = message
            print(f"home-reporter: {message}", flush=True)

    def clear(self) -> None:
        self.last = None


_push_said = _Say()


def push_workshop(token: str, task: str, who: str, conv: str, start: int, events: list, state: str,
                  error: str | None = None, project: str = "", finished_age_s: float | None = None) -> dict | None:
    """Tell the portal how a private-Skippy run is going: events[0] is index `start` of the run's own list. The reply is
    the portal's {"have": events it holds (send from there), "stop": the Captain pressed Stop}. None means the portal
    didn't take it (down, redeploying, refused): the bridge still holds everything, so the caller just tries again.
    `finished_age_s` (only for a run the bridge says is over, on every chunk) lets the portal tell a run that just ended
    from an old one it is being caught up on, so the Captain is notified once, not again after every deploy."""
    body = {"who": who, "conv": conv, "start": start, "events": events, "state": state, "error": error, "project": project}
    if finished_age_s is not None:
        body["finished_age_s"] = finished_age_s
    req = urllib.request.Request(
        f"{PORTAL}/home/workshop/{task}", method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        data=json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            reply = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        _push_said(f"the portal said HTTP {e.code} to a workshop push" +
                   (" (SKIPPY_HOME_TOKEN doesn't match; pair again on the dashboard)" if e.code == 401 else ""))
        return None
    except Exception as e:  # noqa: BLE001 — the portal is down or redeploying; the bridge holds the events
        _push_said(f"can't reach the portal to report the private Skippy: {e}")
        return None
    _push_said.clear()
    return reply if isinstance(reply, dict) else None


def portal_get(token: str, path: str, timeout: float = 15) -> tuple[dict | None, str]:
    """GET from the portal with the home token (the same Authorization header Jobs._post sends). (None, why) when it
    didn't answer with a JSON object."""
    req = urllib.request.Request(f"{PORTAL}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}" + (" (SKIPPY_HOME_TOKEN doesn't match)" if e.code == 401 else "")
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    return (body, "") if isinstance(body, dict) else (None, "an odd reply")


def portal_started(token: str, task: str) -> bool:
    """Has the portal already seen this task running (or ended)? Then the run exists on the bridge, and delivering the job
    again must only re-attach to it, never start it a second time (the bridge may have forgotten it; redoing the work
    and splicing a second run's events onto the first's would be worse than saying it was lost)."""
    listing, _ = portal_get(token, "/home/workshop")
    for rec in (listing or {}).get("tasks") or []:
        if isinstance(rec, dict) and rec.get("task") == task:
            return rec.get("state") in ("running", "stalled", "done", "error")
    return False


PUSH_BYTES = 700_000         # one push's events, as JSON: the portal refuses a request over 1 MB, and a screenshot is ~530 KB


def push_chunk(events: list) -> list:
    """The next events to push: at most WORKSHOP_PUSH of them and at most PUSH_BYTES of JSON, but always at least one
    (a single event is never bigger than the bridge lets it be: text 4 KB, a screenshot 400 KB before base64)."""
    out, size = [], 0
    for event in events[:WORKSHOP_PUSH]:
        n = len(json.dumps(event))
        if out and size + n > PUSH_BYTES:
            break
        out.append(event)
        size += n
    return out


class Relay:
    """Carries ONE run's events from the bridge to the portal, a round at a time (`step`). Two things drive it: the
    streaming thread (`workshop`, one long-poll round after another while a job is being worked) and the reconciler
    (`WorkshopSync`, which catches the portal up when nothing is streaming). Both may push the same run at the same
    moment: that is safe because the portal's reply to every push says how many events it holds (`have`), pushes are
    idempotent, and a finished run can't be reopened by a late "running" (portal/workshop.py `push`)."""

    def __init__(self, token: str, task: str, who: str, conv: str, after: int = 0, project: str = ""):
        self.token, self.task, self.who, self.conv, self.after = token, task, who, conv, after
        self.project = project                 # so a portal rebuilding the record after a deploy knows the run's project
        self.state: str | None = None          # "done"/"error" once the portal holds the end
        self.stopped = False

    def step(self, wait: float = 0) -> str:
        """One look at the bridge (holding up to `wait` s for news) and one push. Returns "final" (the portal now holds
        everything and the end), "more" (moved: go again at once), "idle" (nothing new; the push was a heartbeat),
        "lost" (the bridge doesn't know this task), "bridge" or "portal" (that side didn't answer)."""
        status, run = _bridge("GET", f"/workshop/run/{self.task}?after={self.after}&wait={wait}", None, self.token,
                              timeout=wait + 15)
        if status == 404:
            return "lost"
        if status != 200:
            return "bridge"
        events, upto, done = run.get("events") or [], _int(run.get("next")), bool(run.get("done"))
        chunk = push_chunk(events)
        final = done and len(chunk) == len(events)
        state = ("done" if run.get("ok") else "error") if final else "running"
        error = (run.get("error") or "The run ended without finishing.") if state == "error" else None
        age = run.get("finished_age_s") if done else None
        reply = push_workshop(self.token, self.task, self.who, self.conv, self.after, chunk, state, error, self.project,
                              age if isinstance(age, (int, float)) and not isinstance(age, bool) else None)
        if reply is None:
            return "portal"
        if reply.get("stop") and not self.stopped:
            self.stopped = _bridge("POST", f"/workshop/cancel/{self.task}", {}, self.token)[0] == 200
        have, before = reply.get("have"), self.after
        self.after = min(have, self.after + len(chunk)) if isinstance(have, int) and have >= 0 else self.after + len(chunk)
        if final and self.after >= upto:
            self.state = state
            return "final"
        return "more" if self.after != before or chunk else "idle"


def workshop(job: dict, token: str) -> dict:
    """The private Skippy: hand the Captain's message to Claude Code on the bridge, then relay the run's events
    to the portal until it ends. The bridge keeps every event, so a portal deploy or a reporter restart loses
    nothing: the portal's reply says how many events it holds and this sends what it's missing.

    This thread is only the fast path (one round per second or so while a run is going). It does not have to be the
    only way the portal hears: WorkshopSync (below) compares the bridge's runs with the portal's records every few
    seconds and repairs the difference, and adopts a run nothing is streaming by handing this function a job with
    `attach` set, which may only join a run the bridge already has, never start one."""
    task, conv, message = job.get("id"), job.get("conv"), job.get("message") or ""
    who = str(job.get("who") or "")
    project = str(job.get("project") or "")
    project = project if _PROJECT_ID.fullmatch(project) else ""     # "" = the bridge's default (SkippyTel)

    def fail(reason: str) -> dict:
        for _ in range(3):
            if push_workshop(token, task, who, conv, 0, [], "error", reason, project) is not None:
                break
            time.sleep(1)
        return {"error": reason}

    if not BRIDGE:
        return fail("The private Skippy isn't set up on skippy-pc (CLAUDE_BRIDGE_URL).")
    attach = bool(job.get("attach")) or portal_started(token, task)
    for attempt in range(START_TRIES):            # a bridge that is restarting answers in a few seconds
        status, started = _bridge("POST", "/workshop", {"task": task, "conv": conv, "message": message, "who": who,
                                                        "attach": attach, "project": project}, token)
        if status is not None or attempt == START_TRIES - 1:
            break
        time.sleep(START_RETRY_S)
    if status == 404 and attach:
        return fail(LOST_TASK)
    if status != 200:
        return fail({409: "Skippy is still working on the last one.",
                     401: "The bridge doesn't accept this token: is the vault's SKIPPY_HOME_TOKEN the paired one?",
                     503: "The bridge has no SKIPPY_HOME_TOKEN configured.",
                     None: "The bridge on skippy-pc isn't running (make bridge)."}.get(status)
                    or started.get("message") or f"The bridge said HTTP {status}.")
    relay, silent, deadline = Relay(token, task, who, conv, project=project), 0, _now() + WORKSHOP_MAX_S
    while _now() < deadline:
        outcome = relay.step(WORKSHOP_WAIT)
        if outcome == "final":
            return {"result": {"state": relay.state}}
        if outcome == "lost":
            return fail(LOST_TASK)
        if outcome == "bridge":
            silent += 1
            if silent >= 20:
                # Say nothing to the portal: the run may be going fine and only the link is down, and an error here would
                # stick. The reconciler re-adopts the run when the bridge answers, or ends it plainly if it came back empty.
                return {"error": "Lost contact with the bridge on skippy-pc."}
            time.sleep(2)
            continue
        silent = 0
        if outcome == "portal":
            time.sleep(2)
        elif outcome == "idle":
            time.sleep(0.5)                     # nothing moved: don't spin
    return fail("Gave up waiting for the run to end.")


_CONV = re.compile(r"[A-Za-z0-9-]{8,64}")


def workshop_conversations(job: dict, token: str) -> dict:
    """The private Skippy's Conversations list (job kind workshop_conversations) or one conversation's turns
    (workshop_conversation, with `conv`), read from the bridge and handed back as the job's result. A job and not a part
    of every report: the list is wanted only when the Captain opens it, and one conversation can be hundreds of KB."""
    if not BRIDGE:
        return {"error": "The private Skippy isn't set up on skippy-pc (CLAUDE_BRIDGE_URL)."}
    if job.get("kind") == "workshop_conversation":
        conv = str(job.get("conv") or "")
        if not _CONV.fullmatch(conv):
            return {"error": "bad conversation id"}
        status, body = _bridge("GET", f"/workshop/conversation/{conv}", None, token, timeout=20)
    else:
        status, body = _bridge("GET", "/workshop/conversations", None, token, timeout=20)
    if status == 404:
        return {"error": "That conversation isn't on skippy-pc."}
    if status != 200 or not isinstance(body, dict):
        return {"error": "The bridge on skippy-pc isn't answering." if status is None else f"The bridge said HTTP {status}."}
    return {"result": body}


_MEMORY_KEY = re.compile(r"[a-z-]{2,20}:[A-Za-z0-9_.-]{1,80}")


def memory_job(job: dict, token: str) -> dict:
    """Skippy Memory for the chat's "Continue with Skippy": the list of saved AI conversations (memory_list) or one
    conversation's text (memory_get, with `key`), read from the bridge, which keeps the archive (memory_service.py)."""
    if not BRIDGE:
        return {"error": "The bridge isn't set up on skippy-pc (CLAUDE_BRIDGE_URL)."}
    if job.get("kind") == "memory_get":
        key = str(job.get("key") or "")
        if not _MEMORY_KEY.fullmatch(key):
            return {"error": "bad conversation key"}
        status, body = _bridge("GET", "/memory/conversation?" + urllib.parse.urlencode({"key": key}), None, token, timeout=30)
    else:
        status, body = _bridge("GET", "/memory/list", None, token, timeout=30)
    if status == 200 and isinstance(body, dict):
        return {"result": body}
    if status is None:
        return {"error": "The bridge on skippy-pc isn't answering."}
    if status == 404:
        return {"error": "That conversation isn't in Skippy Memory."}
    return {"error": str((body or {}).get("error") or f"The bridge said HTTP {status}.")[:300]}


HOST_OPS = ("restart-service", "restart-bridge", "reset-workshop", "funnel")
_HOST_ARG = re.compile(r"[a-z0-9-]{0,40}")


def host_op(job: dict, token: str) -> dict:
    """An Admin button for skippy-pc itself (job kind host_op): restart a service or the bridge, reset a workshop clone,
    Funnel on/off. The bridge does it (it runs on Windows, with docker, git and tailscale; this container has none of
    them) and checks every name against its own allow-lists; this only refuses what is plainly malformed."""
    op, arg = str(job.get("op") or ""), str(job.get("arg") or "")
    if op not in HOST_OPS or not _HOST_ARG.fullmatch(arg):
        return {"error": "That isn't an operation skippy-pc does."}
    if not BRIDGE:
        return {"error": "The bridge isn't set up on skippy-pc (CLAUDE_BRIDGE_URL)."}
    status, body = _bridge("POST", f"/host/{op}", {"arg": arg}, token, timeout=150)
    if status is None:
        return {"error": "The bridge on skippy-pc isn't answering. Its supervisor restarts it within a minute if it "
                         "crashed; if this keeps happening, skippy-pc needs a look in person."}
    if status in (200, 409) and isinstance(body, dict) and body.get("summary"):
        return {"result": {"ok": bool(body.get("ok")) and status == 200, "summary": str(body["summary"])[:400],
                           "output": str(body.get("output") or "")[:1600]}}
    return {"error": f"The bridge said HTTP {status}."}


def run_job(job: dict, token: str = "") -> dict:
    """A job from the portal. ask_mentat → Mentat + Qwen here at home; make_music → the music studio;
    workshop → the private Skippy (Claude Code on the bridge); workshop_conversation(s) → its conversation history."""
    if job.get("kind") == "workshop":
        return workshop(job, token)
    if job.get("kind") in ("workshop_conversations", "workshop_conversation"):
        return workshop_conversations(job, token)
    if job.get("kind") == "make_music":
        return make_music(job, token)
    if job.get("kind") == "host_op":
        return host_op(job, token)
    if job.get("kind") in ("memory_list", "memory_get"):
        return memory_job(job, token)
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
        if "fast_for_s" in reply:         # a hand-made reply (the reconciler adopting a run) must not end the quick poll
            self.fast_until = _now() + float(reply.get("fast_for_s") or 0)
        for job in reply.get("jobs") or []:
            with self._lock:
                if job.get("id") in self._running:
                    continue
                self._running.add(job.get("id"))
            threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def is_running(self, job_id: str) -> bool:
        """A thread here is working this job (for the private Skippy: streaming the run's events to the portal)."""
        with self._lock:
            return job_id in self._running

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
            if _now() < self.fast_until:
                try:
                    self.accept(self._post("/home/jobs", None, method="GET"))
                except Exception:  # noqa: BLE001 — the next report carries jobs too
                    pass
                time.sleep(FAST_INTERVAL)
            else:
                time.sleep(1)


class WorkshopSync:
    """The reconciler: whatever the bridge holds of the private Skippy's runs ends up in the portal, whether or not
    anything is streaming it and whether or not the portal's job queue still offers the job.

    Why it exists: the only thing that pushed a run to the portal was the relay thread, and the only thing that started
    one was the portal's job queue. A self-deploy kills both at once: the agent pushes (every push redeploys the Cloud
    Run portal, wiping its task records AND its job queue) and then rebuilds this container (killing the thread). The
    run finished fine on the bridge and nothing was left that knew to tell the portal.

    Every SYNC_INTERVAL seconds (SYNC_FAST while someone is watching the chat) it compares the two sides and repairs the
    difference. Everything it does is idempotent (the portal's `have` handshake makes a repeated push a no-op), so racing
    the streaming relay is harmless, and it only ever looks at runs the bridge itself lists: it can't start one."""

    def __init__(self, token: str, jobs: Jobs):
        self.token, self.jobs, self._say = token, jobs, _Say()

    def interval(self) -> float:
        return SYNC_FAST if _now() < self.jobs.fast_until else SYNC_INTERVAL

    def run_forever(self) -> None:
        last = None
        while True:
            now = _now()
            if last is None or now - last >= self.interval():
                last = now
                try:
                    self.sync_once()
                except Exception as e:  # noqa: BLE001 — never let this loop die: it is what brings the chat back
                    self._say(f"workshop sync failed: {e!r}")
            time.sleep(0.5)

    def _catch_up(self, task: str, who: str, conv: str, have: int, project: str = "") -> str:
        """Push what the portal is missing (from its own `have`), then the end if the run is over. A run still going gets
        at least one push even when nothing is new: that heartbeat is what keeps the portal from calling it stalled."""
        relay, outcome = Relay(self.token, task, who, conv, after=have, project=project), "more"
        for _ in range(CATCH_UP_STEPS):
            outcome = relay.step(0)
            if outcome != "more":
                break
        return outcome

    def sync_once(self) -> dict:
        """One pass. Returns what it did: {"pushed": [tasks], "adopted": [tasks], "lost": [tasks]}. It does nothing at all
        (and invents no error) unless both the bridge and the portal answered."""
        out: dict = {"pushed": [], "adopted": [], "lost": []}
        # The portal first, then the bridge: every record the portal holds was created by a push, which the reporter only
        # makes after the bridge accepted the run, so a record in the first list is in the second unless the bridge
        # really lost it. The other order could "lose" a run that started in between.
        listing, why = portal_get(self.token, "/home/workshop")
        if listing is None or not isinstance(listing.get("tasks"), list):
            self._say(f"workshop sync: the portal didn't answer ({why or 'an odd reply'})")
            return out
        status, body = _bridge("GET", "/workshop/runs", None, self.token, timeout=15)
        runs = body.get("runs") if status == 200 else None
        if not isinstance(runs, list):
            self._say("workshop sync: the bridge didn't answer " + ("(it isn't running)" if status is None else f"(HTTP {status})"))
            return out
        known = {r["task"]: r for r in listing["tasks"] if isinstance(r, dict) and _TASK.fullmatch(str(r.get("task") or ""))}
        on_bridge = set()
        for run in runs:
            task = str(run.get("task") or "") if isinstance(run, dict) else ""
            if not _TASK.fullmatch(task):
                continue
            on_bridge.add(task)
            who, done = str(run.get("who") or ""), bool(run.get("done"))
            if not who:
                continue                                  # nobody could read it
            age = run.get("finished_age_s")
            if done and not (isinstance(age, (int, float)) and age < WORKSHOP_RECENT_S):
                continue                                  # long over: whoever wanted it has had an hour to get it
            rec = known.get(task)
            if done and rec and rec.get("final") and rec.get("state") == ("done" if run.get("ok") else "error") \
                    and _int(rec.get("have")) >= _int(run.get("events")) and not rec.get("want"):
                continue                                  # the portal holds all of it
            conv = str(run.get("conv") or "")
            project = str(run.get("project") or "")
            project = project if _PROJECT_ID.fullmatch(project) else ""
            outcome = self._catch_up(task, who, conv, _int(rec.get("have")) if rec else 0, project)
            if outcome in ("portal", "bridge"):
                # That side stopped answering mid-pass (a deploy, a restart). Stop here: one try, not one per run (each could
                # cost its full timeout), and leave every verdict, "lost" included, to a pass in which both sides answer.
                # (push_workshop has already said what the portal did; the bridge's silence is said here, once.)
                if outcome == "bridge":
                    self._say("workshop sync: the bridge stopped answering during a pass")
                return out
            if outcome == "lost":
                continue          # listed a moment ago and gone now: the next pass sees it missing and ends the portal's record
            out["pushed"].append(task)
            if not done and not self.jobs.is_running(task):
                # Nothing is streaming this run: hand it to the fast path (a round every second, not every 10).
                self.jobs.accept({"jobs": [{"id": task, "kind": "workshop", "conv": conv, "who": who,
                                            "message": "(re-attach)", "attach": True, "project": project}]})
                out["adopted"].append(task)
        for task, rec in known.items():
            # A run the portal thinks is going but the bridge no longer has (it restarted without its runs, or pruned
            # them): the chat must end, not wait. A queued task isn't one: the job queue still owns it.
            if rec.get("final") or task in on_bridge or rec.get("state") not in ("running", "stalled") or self.jobs.is_running(task):
                continue
            if push_workshop(self.token, task, str(rec.get("owner") or ""), "", _int(rec.get("have")), [], "error",
                             LOST_TASK) is None:
                return out                                # the portal went quiet: don't wait out the same timeout for every record
            out["lost"].append(task)
        self._say.clear()                                 # a whole pass went through: the next failure is news again
        return out


class Watchdog:
    """If the portal hasn't accepted one of this process's reports for WATCHDOG_S (a wedged connection pool, a hung thread,
    a network that never came back), exit with a non-zero code so Docker (`restart: unless-stopped`) starts a fresh
    process. Nothing is lost by it: the relay and the reconciler pick every run up again from the bridge."""

    def __init__(self, limit: float = WATCHDOG_S, clock=time.monotonic, exit_fn=os._exit):
        self.limit, self.clock, self.exit_fn = limit, clock, exit_fn
        self.started = self.last_ok = clock()

    def ok(self) -> None:
        """A report reached the portal."""
        self.last_ok = self.clock()

    def check(self) -> bool:
        now = self.clock()
        if now - self.started > self.limit and now - self.last_ok > self.limit:
            print(f"home-reporter: no successful report to the portal for {int(now - self.last_ok)} s; "
                  "exiting so Docker starts a fresh process", flush=True)
            self.exit_fn(3)
            return True
        return False

    def run_forever(self) -> None:
        while True:
            time.sleep(60)
            self.check()


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

    def run_forever(self) -> None:
        """Its own thread (main() starts it): a cycle may take up to TELEMETRY_CYCLE_S and must never delay a report."""
        while True:
            try:
                self.collect()                  # what people did on the portal, into flask-api's SQLite
            except Exception as e:  # noqa: BLE001 — never let this stop the collecting
                print(f"home-reporter: telemetry collection failed: {e}", flush=True)
            time.sleep(INTERVAL)

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
    watchdog = Watchdog()
    threading.Thread(target=jobs.poll_forever, daemon=True).start()
    threading.Thread(target=watchdog.run_forever, daemon=True).start()
    if BRIDGE:                                       # the private Skippy: catch the portal up on whatever the bridge holds
        threading.Thread(target=WorkshopSync(token, jobs).run_forever, daemon=True).start()
    # What people did on the portal goes into flask-api's SQLite on its own thread: a collection may take 20 s, and while it
    # ran in this loop the next report (the heartbeat the portal judges "silent" by) waited behind it.
    threading.Thread(target=site.run_forever, daemon=True).start()
    sent_art, results, warned = None, [], False
    while True:
        began = time.monotonic()
        try:
            report = gather()
        except Exception as e:  # noqa: BLE001 — gather guards each probe; this is for a bug in the assembling itself
            print(f"home-reporter: gathering the report failed: {e!r}; sending a bare heartbeat", flush=True)
            report = {}
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
            watchdog.ok()
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
        time.sleep(max(1.0, INTERVAL - (time.monotonic() - began)))       # a steady cadence: the work above counts against the wait


if __name__ == "__main__":
    main()
