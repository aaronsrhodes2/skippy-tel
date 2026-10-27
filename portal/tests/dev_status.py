"""Look at the Status page in a real browser, with made-up machines: `python portal/tests/dev_status.py`, then open http://127.0.0.1:8765/dev-login.

A real portal app on fake services (the tests' own), signed in as the Captain by /dev-login, with the three machines reporting: skippy-pc
(containers, one unhealthy), skippy-core (models, containers, one restarting), and a few log lines. Nothing here touches the real house.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402

import app as portal  # noqa: E402


def main(port: int = 8765) -> None:
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    home, starter, status, logs = app.extensions["home"], app.extensions["starter"], app.extensions["status"], app.extensions["logs"]
    home.accept({"capabilities": {"workshop": {"ready": True}, "make_image": {"ready": True, "loaded": True}, "make_image_hq": {"ready": True, "loaded": False, "box": "core"},
                                 "image_to_3d": {"ready": True, "loaded": False}, "music_gate": {"ready": True}, "house": {"ready": False}},
                 "services": [{"name": "Skippy AI", "available": True}, {"name": "UPS Monitor", "available": False, "planned": True}],
                 "doctor": {"voice-gate": {"ok": True, "reachable": True, "state": "ready"}, "music-gate": {"ok": True, "reachable": True, "state": "busy", "jobs": 2},
                            "video-gate": {"ok": False, "reachable": True, "state": "hung", "jobs": 1, "stuck_s": 300, "why": "hung"},
                            "mentat-home": {"ok": False, "reachable": True, "state": "not-ready", "why": "waiting on llm (skippy-core doesn't offer the chat model)"},
                            "whisper": {"ok": True, "reachable": True}, "flask-api": {"ok": False, "reachable": False, "why": "no answer (URLError)"}},
                 "status": {"services": []}, "health": {"status": "ok", "models": {"ollama": {"available": True, "default_present": True}}}})
    starter.poll({"status": {"docker": True, "busy": True, "containers": [
        {"name": "skippytel-flask-api", "state": "running", "status": "Up 3 hours (healthy)", "image": "skippytel-flask-api"},
        {"name": "skippytel-voice-gate", "state": "running", "status": "Up 3 hours (unhealthy)", "image": "voice-gate"},
        {"name": "skippytel-music-gate", "state": "running", "status": "Up 2 days", "image": "music-gate"}]}})
    status.accept_core({"containers": [{"name": "skippycore-llm-1", "state": "running", "health": "healthy", "status": "Up 20 hours", "cpu": 12.5, "mem_mb": 1536},
                                       {"name": "skippycore-mentat-1", "state": "restarting", "restarts": 7}],
                        "models": [{"name": "chat", "state": "ready"}, {"name": "coder", "state": "ready"}, {"name": "router", "state": "ready"}, {"name": "vision", "state": "starting"}],
                        "deployer": {"state": "deferred", "commit": "d18a24e", "detail": "holding"}, "doctor": {"ok": True}, "vitals": {"mem_pct": 41, "disk_pct": 20}, "busy": True})
    logs.add("skippy-pc", [{"source": "docker/skippytel-voice-gate", "level": "error", "kind": "crash", "msg": "whisper did not answer in 30s"},
                           {"source": "docker/skippytel-flask-api", "level": "info", "msg": "GET /health 200"}])
    logs.add("skippy-core", [{"source": "docker/skippycore-mentat-1", "level": "error", "kind": "crash", "msg": "ModuleNotFoundError: No module named 'sigma_ground'"},
                             {"source": "journal/skippy-core-deployer", "level": "warn", "msg": "deferred: the private Skippy is working"}])

    @app.get("/dev-login")
    def dev_login():
        from flask import redirect, session
        session["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
        return redirect("/status")
    print(f"Status page: http://127.0.0.1:{port}/dev-login   (stop with Ctrl-C)", flush=True)
    app.run(host="127.0.0.1", port=port)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
