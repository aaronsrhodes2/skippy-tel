#!/usr/bin/env python3
"""The Captain's car's charging, switched over Bluetooth: POST /charge, GET /health. skippy-core sits close enough to hear the car.

Tesla's own tesla-control (teslamotors/vehicle-command, built pinned into this image) signs each command with a key that lives on the box
(/etc/skippy-core/tesla-ble/private.pem, paired once by tapping the key card on the centre console) and sends it straight to the car over
Bluetooth: no Tesla server, no Fleet API fee. The key is enrolled as charging_manager, so the car takes nothing from it but charging.
Reading the car stays with TeslaMate (core/car); this only sends.

    POST /charge {"action": "start"|"stop"}            {"ok": true, "said": "charging started"}
    POST /charge {"action": "limit", "percent": 85}    {"ok": true, "said": "charge limit set to 85%"}
    GET  /health  the readiness contract (ready.py): ready when the radio, the key and the VIN are all there

One command at a time (the radio is one); a car that is asleep is woken once and asked again.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ready import Readiness

PORT = int(os.environ.get("PORT", "7352"))
HOST = os.environ.get("HOST", "127.0.0.1")                 # host networking (the radio): the tailnet reaches it only through tailscale serve
KEY = os.environ.get("TESLA_KEY_FILE", "/keys/private.pem")
VIN = os.environ.get("TESLA_VIN", "").strip()
HCI = Path(os.environ.get("HCI_PATH", "/sys/class/bluetooth/hci0"))
TOOL = os.environ.get("TESLA_CONTROL", "tesla-control")
TIMEOUT = 45

LOCK = threading.Lock()
READY = Readiness("car-ble")

# What the car says when it won't, in plain words (tesla-control prints the car's own reason).
REASONS = (("is_charging", "it was already charging"), ("not_charging", "it wasn't charging"),
           ("disconnected", "the car isn't plugged in"), ("complete", "the battery is already at its limit"),
           ("not been paired", "the key isn't paired with the car yet (tap the key card on the centre console)"),
           ("unauthorized", "the car refused the key for that"))
ASLEEP = ("asleep", "context deadline exceeded", "timeout", "not connected")


def command(action: str, percent=None) -> list[str]:
    if action == "start":
        return ["charging-start"]
    if action == "stop":
        return ["charging-stop"]
    if action == "limit":
        try:
            p = int(percent)
        except (TypeError, ValueError):
            raise ValueError("a limit needs a percent") from None
        if not 50 <= p <= 100:
            raise ValueError("the car takes a charge limit from 50 to 100%")
        return ["charging-set-limit", str(p)]
    raise ValueError("action is start, stop or limit")


def said(action: str, percent=None) -> str:
    return {"start": "charging started", "stop": "charging stopped"}.get(action) or f"charge limit set to {int(percent)}%"


def why(out: str) -> str:
    low = out.lower()
    for needle, words in REASONS:
        if needle in low:
            return words
    last = [line.strip() for line in out.strip().splitlines() if line.strip()]
    return (last[-1] if last else "no answer from tesla-control")[:200]


def run(args: list[str], runner=subprocess.run) -> tuple[bool, str]:
    try:
        got = runner([TOOL, "-ble", "-vin", VIN, "-key-file", KEY, *args], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "timeout: the car didn't answer over Bluetooth"
    out = (got.stdout or "") + (got.stderr or "")
    return got.returncode == 0 and "error" not in out.lower(), out


def charge(action: str, percent=None, runner=subprocess.run) -> dict:
    args = command(action, percent)
    with LOCK:
        token = READY.begin(action)
        try:
            ok, out = run(args, runner)
            if not ok and any(w in out.lower() for w in ASLEEP) and not any(n in out.lower() for n, _ in REASONS):
                run(["wake"], runner)                           # asleep: wake it, then ask once more
                ok, out = run(args, runner)
        finally:
            READY.end(token)
    if ok:
        READY.progress()
        return {"ok": True, "said": said(action, percent)}
    reason = why(out)
    if reason in ("it was already charging", "it wasn't charging", "the battery is already at its limit"):
        return {"ok": True, "said": f"nothing to do: {reason}"}
    return {"ok": False, "error": reason}


def health() -> dict:
    deps = {"radio": {"ready": HCI.exists(), "detail": "" if HCI.exists() else f"no Bluetooth radio at {HCI}"},
            "key": {"ready": os.access(KEY, os.R_OK), "detail": "" if os.access(KEY, os.R_OK) else f"no key at {KEY}"},
            "vin": {"ready": bool(VIN), "detail": "" if VIN else "TESLA_VIN is not set"}}
    bad = [d["detail"] for d in deps.values() if not d["ready"]]
    return READY.report(ready=not bad, detail="; ".join(bad), deps=deps)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.split("?", 1)[0] == "/health":
            got = health()
            self._send(200 if got["ready"] else 503, got)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/charge":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
            got = charge(str(body.get("action") or ""), body.get("percent"))
        except (ValueError, AttributeError) as e:
            return self._send(400, {"ok": False, "error": str(e)})
        self._send(200 if got["ok"] else 502, got)

    def log_message(self, *args):
        pass


def main() -> None:
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
