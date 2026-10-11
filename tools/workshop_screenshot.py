#!/usr/bin/env python3
"""Screenshot a portal page as the Captain would see it on his phone, from this checkout, with the test fakes.

The private Skippy runs this after it pushed a change to portal/templates or portal/static, then prints
`SKIPPY_IMAGE: <png>` so the bridge attaches the picture under its answer (he works by outcome: show him the result).

    python tools/workshop_screenshot.py front --out .skippy-shots/front.png [--width 390 --height 844]      (front = the Skippy chat page, /chat; home = the app portal, /)
    python tools/workshop_screenshot.py me --out .skippy-shots/me.png                                        (the dashboard)

What it does, and why it is safe:
  * the portal from THIS checkout, built the way the tests build it (portal/tests/test_app.py: create_app(SETTINGS,
    vm=FakeVm(), skippy=FakeSkippy())): no GCP, no Discord, no secrets, nothing real is called;
  * served on 127.0.0.1, on a free port, only while the shot is taken;
  * "signed in" as the scratch admin of the tests by a session cookie this process signs with a random key and adds to
    every request itself (a headless browser started from the command line can't be handed a cookie);
  * a headless Chrome or Edge ALREADY on this PC takes the shot (CHROME env, else the usual install paths, else PATH).
    Nothing is installed: with no browser it says so and exits 3.
Exit codes: 0 shot taken (the path is printed), 2 bad arguments, 3 no headless browser, 4 the shot failed.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 400_000                      # what the bridge will attach
BROWSERS = (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def find_browser(env=os.environ, candidates=BROWSERS, which=shutil.which) -> str | None:
    chosen = env.get("CHROME", "").strip()
    if chosen:
        return chosen if os.path.isfile(chosen) else None
    for path in candidates:
        if os.path.isfile(path):
            return path
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = which(name)
        if found:
            return found
    return None


def make_app():
    """The portal with the tests' fakes, its secret key replaced by a random one for this run, the scratch admin
    signed in on every request."""
    for p in (ROOT / "portal", ROOT / "portal" / "tests"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import app as portal                                  # noqa: E402 — the checkout's own portal
    from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402 — the fakes the tests use

    application = portal.create_app({**SETTINGS, "SECRET_KEY": secrets.token_hex(32)}, vm=FakeVm(), skippy=FakeSkippy())
    application.extensions["home"].accept({"capabilities": {"workshop": {"ready": True, "model": "claude-opus-5-5"}}})
    signer = application.session_interface.get_signing_serializer(application)
    cookie = f"{application.config.get('SESSION_COOKIE_NAME', 'session')}=" + \
        signer.dumps({"user": {"email": ADMIN, "name": "Captain", "picture": None}})
    inner = application.wsgi_app

    def signed_in(environ, start_response):
        environ["HTTP_COOKIE"] = cookie                   # this process's own server, on loopback, for one screenshot
        return inner(environ, start_response)
    application.wsgi_app = signed_in
    return application


def shoot(path: str, out: Path, width: int, height: int, browser, timeout: float = 90) -> int:
    """`browser` is the executable, or a command prefix list (the tests' stand-in)."""
    browser = list(browser) if isinstance(browser, (list, tuple)) else [browser]
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, make_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}{path}"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="skippy-shot-") as profile:
            done = subprocess.run([*browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                                   "--no-default-browser-check", f"--user-data-dir={profile}",
                                   f"--window-size={width},{height}", "--virtual-time-budget=4000",
                                   f"--screenshot={out}", url],
                                  capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"the headless browser didn't finish: {exc}", file=sys.stderr)
        return 4
    finally:
        server.shutdown()
    if done.returncode != 0 or not out.is_file() or out.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        print("the headless browser made no screenshot", file=sys.stderr)
        return 4
    size = out.stat().st_size
    if size > MAX_BYTES:
        print(f"{out} is {size} bytes, over the {MAX_BYTES} the chat takes: try a smaller --height", file=sys.stderr)
        return 4
    print(out.resolve())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", help="the portal page: front (the Skippy chat), me, apps, games ...")
    ap.add_argument("--out", required=True, help="where the PNG goes (e.g. .skippy-shots/me.png)")
    ap.add_argument("--width", type=int, default=390)
    ap.add_argument("--height", type=int, default=844)
    args = ap.parse_args(argv)
    # "me" works as well as "/me": Git Bash on Windows rewrites an argument starting with / into a C:/ path
    args.path = {"front": "/chat", "chat": "/chat", "home": "/"}.get(args.path) or (args.path if args.path.startswith("/") else "/" + args.path)
    if "//" in args.path or ":" in args.path or "\\" in args.path or not args.out.lower().endswith(".png") \
            or not (200 <= args.width <= 2000 and 200 <= args.height <= 4000):
        print("give a page path like /me, an --out ending in .png, and a sane size", file=sys.stderr)
        return 2
    browser = find_browser()
    if browser is None:
        print("No headless browser on this PC (Chrome or Edge, or CHROME=<path>). Nothing was installed.", file=sys.stderr)
        return 3
    return shoot(args.path, Path(args.out), args.width, args.height, browser)


if __name__ == "__main__":
    sys.exit(main())
