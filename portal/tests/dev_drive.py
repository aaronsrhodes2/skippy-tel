"""DEV ONLY: run the Drive page (/drive) on this machine, in a real browser, against the REAL home services, without Google sign-in.

    python portal/tests/dev_drive.py            # portal on :8767; open http://127.0.0.1:8767/dev-login (signs in as the Captain, lands on /drive)

What is real and what stands in:
  * the portal is the real portal app, built the way the tests build it (FakeVm); its "home" is told the REAL gates' public addresses (the
    Funnel URLs the reporter advertises), and its stored hash is the REAL home token's, so the passes it signs open the real music, audiobook and
    voice gates exactly as the car's browser would. Places the book key saves go to the real hub inbox: this IS his book.
  * the Spotify rows: the portal's `spotify` jobs are run here against the real services/spotify on 127.0.0.1:43873 (the reporter's own
    spotify_job), instead of waiting for the home reporter, which reports to the cloud portal, not this one.
  * the house lamps: the capabilities are the reporter's real ones (read from its container), Spotify's lamp from the real service's /health.
    Skippy Core's lamp stays unknown (the cloud portal's doctor tests it) and "the pods" is lit only if this portal's Home chat sees a hub,
    which it never does (the hub syncs with the cloud portal): the Home chat round trip can't be tried here.
  * /dev/clip/<n>: a held-out recording of "Skippy" (state/wakeword/real/test/positive), for feeding the in-browser wake word without a mic.

Not collected by pytest (no test_ prefix); nothing here ships. It never prints the home token.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "services" / "home-reporter")]
VAULT = Path("G:/My Drive/vault/.env")
SPOTIFY = "http://127.0.0.1:43873"


def home_token() -> str:
    for line in VAULT.read_text(encoding="utf-8").splitlines():
        if line.startswith("SKIPPY_HOME_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("no SKIPPY_HOME_TOKEN in the vault")


def real_capabilities() -> dict:
    out = subprocess.run(["docker", "exec", "skippytel-home-reporter", "python", "-c",
                          "import json, reporter; print(json.dumps(reporter.capabilities()))"], capture_output=True, text=True, timeout=120)
    caps = json.loads(out.stdout.strip().splitlines()[-1])
    return {k: v for k, v in caps.items() if k in ("music_gate", "book_gate", "voice_gate", "house", "car")}


def make_portal():
    import app as portal
    import home as home_mod
    import reporter
    from flask import abort, redirect, send_file, session
    from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS

    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(home_token().encode()).hexdigest()}))
    home = home_mod.Home(vm)
    caps = real_capabilities()

    def report():
        while True:
            home.accept({"capabilities": caps})
            time.sleep(5)

    threading.Thread(target=report, daemon=True).start()
    time.sleep(0.2)

    jobs: dict = {}
    real_submit = home.submit

    def submit(kind, **payload):                       # Spotify's jobs run here, against the real service; anything else as before
        if kind != "spotify":
            return real_submit(kind, **payload)
        jid = f"dev-{len(jobs)}"
        jobs[jid] = reporter.spotify_job({"kind": kind, **payload}, SPOTIFY)
        return jid

    real_wait = home.wait
    home.submit = submit
    home.wait = lambda jid, timeout: jobs.pop(jid) if jid in jobs else real_wait(jid, timeout)
    settings = dict(SETTINGS)
    settings["TELEMETRY_ENABLED"] = False
    app = portal.create_app(settings, vm=vm, skippy=FakeSkippy(), home=home)

    def spotify_check():
        while True:
            try:
                ok = json.loads(urllib.request.urlopen(SPOTIFY + "/health", timeout=5).read()).get("ready") is True
            except Exception:                           # noqa: BLE001
                ok = False
            app.extensions["doctor"].checks["spotify"] = {"ok": ok, "why": "", "ms": 0, "since": time.time(), "at": time.time()}
            time.sleep(15)

    threading.Thread(target=spotify_check, daemon=True).start()

    @app.get("/dev-login")
    def dev_login():
        session["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
        return redirect("/drive")

    @app.get("/dev/clip/<int:n>")
    def dev_clip(n):
        clips = sorted((ROOT / "state" / "wakeword" / "real" / "test" / "positive").glob("*.wav"))
        if not 0 <= n < len(clips):
            abort(404)
        return send_file(clips[n], mimetype="audio/wav")

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8767)
    args = ap.parse_args()
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", args.port, make_portal(), threaded=True)
    print(f"Drive on http://127.0.0.1:{args.port}/dev-login (real gates, real Spotify)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
