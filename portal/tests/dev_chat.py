"""DEV ONLY: run Skippy's chat on this machine, in a real browser, against the REAL Skippy Core and the REAL home Mentat.

    ssh -N -L 18080:127.0.0.1:8080 aaron@skippy-core &            # skippy-core's llama-swap, through SSH
    python portal/tests/dev_chat.py                                 # portal on :8776
    SKIPPY_CORE_MODEL=coder python portal/tests/dev_chat.py         # use another llama-swap slot as Skippy Core

then open http://127.0.0.1:8776/dev-login (the Captain) or http://127.0.0.1:8776/dev-login?as=guest (a friend).

What is real and what stands in:
  * Skippy Core is skippy-core's llama-swap (the Anthropic SDK with base_url = the tunnel), the same code path the portal
    uses through the funnel. Claude and Gemini show in the Captain's picker but stay off here: no keys are read.
  * physics jobs are answered by the running skippytel-mentat container on skippy-pc (docker exec), so the ⚛ card shows a
    real Mentat answer. The home is a test Home that reports Mentat up; the games VM is the tests' FakeVm.
Not collected by pytest (no test_ prefix); nothing here ships.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]


def real_mentat(question: str) -> dict:
    code = ("import json,sys,urllib.request;r=urllib.request.Request('http://localhost:8080/ask',"
            "data=json.dumps({'question':sys.argv[1]}).encode(),headers={'content-type':'application/json'});"
            "print(urllib.request.urlopen(r,timeout=100).read().decode())")
    out = subprocess.run(["docker", "exec", "skippytel-mentat", "python", "-c", code, question],
                         capture_output=True, text=True, timeout=110)
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {"ok": False, "error": (out.stderr or out.stdout or "no answer")[:300]}


def make_portal():
    import app as portal
    from flask import redirect, request, session
    from test_app import ADMIN, FRIEND, FakeVm, SETTINGS, _pair, REPORT

    settings = dict(SETTINGS)
    settings["TELEMETRY_ENABLED"] = False
    app = portal.create_app(settings, vm=FakeVm())
    @app.get("/dev-login")
    def dev_login():
        guest = request.args.get("as") == "guest"
        session["user"] = {"email": FRIEND if guest else ADMIN, "name": "Pat" if guest else "Captain", "picture": None}
        return redirect("/chat")

    client = app.test_client()
    token = _pair(client)
    auth = {"Authorization": f"Bearer {token}"}

    def reporter():                       # a home that reports Mentat up and answers its jobs with the real Mentat
        while True:
            got = client.post("/home/report", json={**json.loads(json.dumps(REPORT)), "mentat_home": True}, headers=auth)
            jobs = ((got.get_json() or {}).get("jobs") or []) + client.get("/home/jobs", headers=auth).get_json()["jobs"]
            for job in jobs:                  # a job comes back in the report's reply, or from the fast poll
                if job.get("kind") == "ask_mentat":
                    client.post(f"/home/jobs/{job['id']}", json=real_mentat(job["question"]), headers=auth)
            time.sleep(0.5)

    threading.Thread(target=reporter, daemon=True).start()

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", default="http://127.0.0.1:18080")
    ap.add_argument("--port", type=int, default=8776)
    args = ap.parse_args()
    os.environ["SKIPPY_CORE_URL"] = args.core
    if not os.environ.get("SKIPPY_CORE_API_KEY"):             # llama-swap's key: from the vault into this process only, never printed
        vault = Path("G:/My Drive/vault/.env")
        for line in vault.read_text(encoding="utf-8").splitlines() if vault.is_file() else []:
            if line.startswith("SKIPPY_CORE_API_KEY="):
                os.environ["SKIPPY_CORE_API_KEY"] = line.split("=", 1)[1].strip().strip("\"'")
        os.environ.setdefault("SKIPPY_CORE_API_KEY", "dev")
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GEMINI_FREE_API_KEY"):
        os.environ.pop(k, None)
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", args.port, make_portal(), threaded=True)
    print(f"dev_chat: portal on :{args.port}, Skippy Core at {args.core} "
          f"(slot {os.environ.get('SKIPPY_CORE_MODEL') or 'chat'}); open http://127.0.0.1:{args.port}/dev-login", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
