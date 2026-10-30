"""Run ACE-Step's server and end it once a song is done, so nothing stays warm (the Captain, 2026-10-29: music takes time, quality
over speed). ACE-Step loads its models on the first song (ACESTEP_NO_INIT) and then keeps them parked in RAM forever; it has no unload
of its own. This watches /health (models loaded?) and /v1/stats (a job queued or running?) and, MUSIC_IDLE_EXIT_S after the last job
finished (time for the reporter to fetch the mp3), stops the server and exits. Docker's restart policy brings back a bare server.

    music_idle.py <the server's command...>
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

API = f"http://127.0.0.1:{os.environ.get('ACESTEP_API_PORT', '8001')}"
GRACE_S = int(os.environ.get("MUSIC_IDLE_EXIT_S", "120"))
EVERY_S = 10


def data(path: str):
    try:
        with urllib.request.urlopen(API + path, timeout=5) as r:
            return json.load(r).get("data") or {}
    except Exception:  # noqa: BLE001 — starting up, or busy: not a reason to stop it
        return None


def idle(health, stats) -> bool:
    """Loaded models, and no job queued or running."""
    if not health or stats is None:
        return False
    loaded = health.get("models_initialized") or health.get("llm_initialized")
    jobs = stats.get("jobs") or {}
    busy = jobs.get("queued") or jobs.get("running") or stats.get("queue_size")
    return bool(loaded) and not busy


def main() -> int:
    child = subprocess.Popen(sys.argv[1:])
    signal.signal(signal.SIGTERM, lambda *_: child.terminate())            # docker stop reaches the server
    quiet_since = None
    while child.poll() is None:
        time.sleep(EVERY_S)
        if not idle(data("/health"), data("/v1/stats")):
            quiet_since = None
            continue
        quiet_since = quiet_since or time.time()
        if time.time() - quiet_since >= GRACE_S:
            print("[music-studio] song done: ending so the models leave the card and the RAM", flush=True)
            child.terminate()
            try:
                child.wait(60)
            except subprocess.TimeoutExpired:
                child.kill()
            return 0
    return child.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
