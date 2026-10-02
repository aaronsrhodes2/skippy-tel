"""DEV ONLY: run Nagatha's Video Service on this machine, in a real browser, against the REAL video gate on skippy-pc.

    python portal/tests/dev_video.py                       # portal on :8775, the gate at http://127.0.0.1:3006 (docker compose's video-gate)
    python portal/tests/dev_video.py --gate http://127.0.0.1:3006 --port 8775

then open http://127.0.0.1:8775/dev-login (it signs the browser in as the Captain and lands on /video).

What is real and what stands in:
  * the gate is the running services/video-gate container (Jellyfin behind it): real catalog, posters, films, subtitles, HLS
    and progress. Progress the player sends IS written to Jellyfin's watch state, like it would be from the portal.
  * the portal is the real portal app, built the way the tests build it (FakeVm, a home that "reports" a ready video gate).
    The one dev-only change is Video.gate_url_re, so the gate may be http://127.0.0.1 instead of a *.ts.net address. Production
    never sets it: a gate off the tailnet is never signed for.
  * the passes are real: the gate only takes passes made from the home token it was started with, so this reads
    SKIPPY_HOME_TOKEN (from the environment, else the vault .env at G:/My Drive/vault/.env) into this process's memory, keeps
    only its sha256 (exactly what the portal keeps), and never prints or writes it.

Dev-only switches (real routes, not part of the app):
    /dev/home-off    the home reports no video gate (the portal answers "its video gate isn't answering"; the tile says offline)
    /dev/home-on     and back

Not collected by pytest (no test_ prefix); nothing here ships.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
VAULT = Path("G:/My Drive/vault/.env")


def token_sha256() -> str:
    """The home token's sha256, from the environment or the vault; the token itself goes out of scope right here."""
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token and VAULT.is_file():
        for line in VAULT.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?SKIPPY_HOME_TOKEN\s*=\s*(.*)$", line)
            if m:
                token = m.group(1).strip().strip('"').strip("'")
    if not token:
        sys.exit("dev_video: SKIPPY_HOME_TOKEN isn't in the environment or the vault; the gate would refuse every pass")
    return hashlib.sha256(token.encode()).hexdigest()


def make_portal(gate_url: str):
    import app as portal
    import home as home_mod
    from flask import redirect, session
    from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS

    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": token_sha256()}))
    home = home_mod.Home(vm)
    caps = {"video_gate": {"ready": True, "url": gate_url}, "music_gate": {"ready": False, "url": ""}}
    on = {"value": True}

    def report():
        while True:
            home.accept({"capabilities": caps if on["value"] else {}})
            time.sleep(5)

    threading.Thread(target=report, daemon=True).start()
    time.sleep(0.2)
    settings = dict(SETTINGS)
    settings["TELEMETRY_ENABLED"] = False
    app = portal.create_app(settings, vm=vm, skippy=FakeSkippy(), home=home)
    app.extensions["video"].gate_url_re = re.compile(r"http://127\.0\.0\.1:\d+")

    @app.get("/dev-login")
    def dev_login():
        session["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
        return redirect("/video")

    @app.get("/dev/home-off")
    def home_off():
        on["value"] = False
        home.accept({"capabilities": {}})
        return "home reports no video gate"

    @app.get("/dev/home-on")
    def home_on():
        on["value"] = True
        home.accept({"capabilities": caps})
        return "home reports the video gate"

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gate", default="http://127.0.0.1:3006")
    ap.add_argument("--port", type=int, default=8775)
    args = ap.parse_args()
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", args.port, make_portal(args.gate.rstrip("/")), threaded=True)
    print(f"dev_video: the portal on :{args.port} (gate {args.gate}); open http://127.0.0.1:{args.port}/dev-login", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
