"""DEV ONLY: run the Audiobooks app on this machine, in a real browser, without Tailscale or Google sign-in.

    python portal/tests/dev_books.py                       # the REAL library in D:\\Audiobooks (read-only), portal on :8765, the real gate on :8766
    python portal/tests/dev_books.py --lib some\\folder     # a library laid out the same way: books/, art/, books.json

then open http://127.0.0.1:8765/dev-login (it signs the browser in as the Captain and lands on /audiobooks).

What is real and what stands in:
  * the portal is the real portal app, built the way the tests build it (FakeVm, a home that "reports" a book gate); the one dev-only change is
    Books.gate_url_re, so the gate may be http://127.0.0.1 instead of a *.ts.net address. Production never sets it.
  * the gate IS services/audiobook-gate (its own Flask app over its own Library and Places). The places it saves go to a temp folder, never to the
    real state/ (pass --real-state to write the hub's real inbox instead, to see the voice player pick a place up).
  * "Office (Skippy)" is a stand-in for the hub's room box: it checks in through the real Connect route and acts on the `book:` commands it is
    sent with a tiny clock, so Play on can be tried without a pod. (The real thing is tested against the hub in services/satellite/tests.)

Not collected by pytest (no test_ prefix); nothing here ships.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "services" / "audiobook-gate")]

TOKEN = "dev-token-not-a-secret"


class Server:
    def __init__(self, app, port):
        self.app, self.port = app, port

    def start(self):
        from werkzeug.serving import make_server
        self.srv = make_server("127.0.0.1", self.port, self.app, threaded=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def make_gate(lib: Path, state: Path, inbox: Path):
    import gate
    library = gate.Library(lib)
    library.refresh(force=True)
    places = gate.Places(state / "audiobooks.json", inbox)
    return gate.create_app(library, places, gate.gate_key(TOKEN)), library


def stand_in_box(app, library, name="Office", room="office"):
    """A room box that plays books on a clock, checked in through the real Connect route and moved by the `book:` commands it is handed."""
    player = app.extensions["player"]
    captain = next(iter(sorted(app.config["ADMIN_EMAILS"])))
    box = {"id": None, "pos": 0.0, "playing": False, "speed": 1.0, "at": time.time()}

    def pos():
        return box["pos"] + ((time.time() - box["at"]) * box["speed"] if box["playing"] else 0)

    def act(action):
        m = re.fullmatch(r"book:(play):([a-z0-9-]+)(?::([\d.]+))?|book:(pause|resume)|book:skip:(-?\d+)|book:speed:([\d.]+)|book:chapter:(-?1)", action)
        if not m:
            return
        now = pos()
        if m[1]:
            box.update(id=m[2], pos=float(m[3]) if m[3] else now, playing=True)
        elif m[4]:
            box.update(pos=now, playing=m[4] == "resume")
        elif m[5]:
            box.update(pos=max(0.0, now + int(m[5])))
        elif m[6]:
            box.update(pos=now, speed=float(m[6]))
        elif m[7]:
            box.update(pos=now)
        box["at"] = time.time()

    def loop():
        while True:
            book = library.books.get(box["id"]) if box["id"] else None
            state = {"queue": [], "armed": True}
            if book:
                state["book"] = {"id": box["id"], "title": book["t"], "pos": round(pos(), 1), "chapter": 1, "playing": box["playing"], "speed": box["speed"]}
            reply = player.checkin(captain, f"skippyroom-{room}", f"{name} (Skippy)", state, room=f"box-{room}", box=True)
            for action in reply["commands"]:
                print(room, "box got", action, flush=True)
                act(action)
            time.sleep(2)

    threading.Thread(target=loop, daemon=True).start()


def make_portal(gate_port: int):
    import app as portal
    import home as home_mod
    from flask import redirect, session
    from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS

    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    home = home_mod.Home(vm)
    caps = {"book_gate": {"ready": True, "url": f"http://127.0.0.1:{gate_port}"}}

    def report():
        while True:
            home.accept({"capabilities": caps})
            time.sleep(5)

    threading.Thread(target=report, daemon=True).start()
    time.sleep(0.2)
    settings = dict(SETTINGS)
    settings["TELEMETRY_ENABLED"] = False
    app = portal.create_app(settings, vm=vm, skippy=FakeSkippy(), home=home)
    app.extensions["books"].gate_url_re = re.compile(r"http://127\.0\.0\.1:\d+")

    @app.get("/dev-login")
    def dev_login():
        session["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
        return redirect("/audiobooks")

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lib", default=r"D:\Audiobooks")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--gate-port", type=int, default=8766)
    ap.add_argument("--real-state", action="store_true", help="save places into the hub's real state/audiobooks-inbox")
    args = ap.parse_args()
    state = Path(tempfile.gettempdir()) / "books-dev-state"
    state.mkdir(parents=True, exist_ok=True)
    inbox = ROOT / "state" / "audiobooks-inbox" if args.real_state else state / "inbox"
    gate_app, library = make_gate(Path(args.lib), state, inbox)
    Server(gate_app, args.gate_port).start()
    portal_app = make_portal(args.gate_port)
    stand_in_box(portal_app, library)
    stand_in_box(portal_app, library, "Kitchen", "kitchen")
    print(f"dev_books: the real audiobook-gate on :{args.gate_port} ({library.count} books), the portal on :{args.port}; open http://127.0.0.1:{args.port}/dev-login")
    Server(portal_app, args.port).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
