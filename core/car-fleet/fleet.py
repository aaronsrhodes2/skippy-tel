#!/usr/bin/env python3
"""The Captain's car as a guard (2026-10-31, the Captain: "operate my car as an external security device"): Tesla's Fleet API from skippy-core.

The car streams its Sentry state to our own Fleet Telemetry server (the fleet-telemetry container at telemetry.makerportal.live:7443,
forwarded by the home router), which publishes it to MQTT; this listens. Sentry's states (Tesla's schema): off, idle, armed, AWARE (someone came close, the
cameras are recording), PANIC (a threat: touched, leaned on; the car's own alarm), quiet. On AWARE and PANIC it keeps an event, which
skippy-pc's reporter carries to the portal (a Discord DM to the Captain). On PANIC it also answers: flashes the lights and locks the
doors (the Captain: no honk). Commands are signed by Tesla's own tesla-http-proxy (built pinned into this image) with the app's key
(/etc/skippy-core/tesla-fleet/private.pem, whose public half makerportal.live serves); his tokens (tokens.json beside it) are kept
fresh here. Tesla lets no app read the cameras' pictures or set the cabin lights: the clips stay on the car's USB drive.

    GET  /health      the readiness contract (ready.py): ready when the proxy runs, his tokens refresh and MQTT is connected
    GET  /sentry      {"state", "since", "events": [{"id", "at", "state", "said"}], "react", "telemetry": {"heard_s", "online"}}
    POST /sentry      {"action": "arm"|"disarm"}               Sentry Mode on or off (wakes the car once if it's asleep)
    POST /command     {"action": "flash"|"lock"}               the two things the guard does, by hand (to test it)
    POST /charge      {"action": "start"|"stop"}               charging on or off (2026-11-01: replaced core/car-ble's Bluetooth, which
                      {"action": "limit", "percent": 85}        fought the Enclave speakers for skippy-core's radio); {"ok", "said"|"error"}
    POST /telemetry   {"action": "push"|"status"}              send the car its streaming config (signed), or read what it has
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ready import Readiness

PORT = int(os.environ.get("PORT", "7353"))
KEYS = Path(os.environ.get("FLEET_KEYS", "/keys"))                      # /etc/skippy-core/tesla-fleet: private.pem, tokens.json, telemetry/
TOKENS = KEYS / "tokens.json"
PRIVATE = KEYS / "private.pem"
TELEMETRY_CA = KEYS / "telemetry" / "ca.pem"
PUSHED = KEYS / "telemetry" / "pushed.json"
VIN = os.environ.get("TESLA_VIN", "").strip()
TELEMETRY_HOST = os.environ.get("TELEMETRY_HOST", "").strip()            # telemetry.makerportal.live (install/tesla.sh writes it)
TELEMETRY_PORT = int(os.environ.get("TELEMETRY_PORT", "7443"))     # the router forwards it straight through
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC_BASE = os.environ.get("TELEMETRY_TOPIC", "telemetry")
REACT = os.environ.get("CAR_SENTRY_REACT", "1") == "1"
AUTH = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
PROXY = os.environ.get("PROXY_URL", "https://127.0.0.1:4443")
PROXY_TOOL = os.environ.get("TESLA_HTTP_PROXY", "tesla-http-proxy")
COOLDOWN_S = 60                 # one answer (flash + lock) a minute, however often the alarm trips
EVENTS = 20
CONFIG_DAYS = 360               # how long the car keeps streaming before the config must be pushed again (Tesla caps it at a year)
REPUSH_DAYS = 300
FIELDS = {"SentryMode": {"interval_seconds": 1}}   # only Sentry: every signal streamed is billed, and this one changes rarely

READY = Readiness("car-fleet")
STATES = ("off", "idle", "armed", "aware", "panic", "quiet")
SAID = {"aware": "someone came close; the cameras are recording", "panic": "someone touched the car; its alarm went off"}


def sentry_state(payload) -> str:
    """Tesla's SentryMode value as one of STATES, or "". The MQTT value is JSON and its shape follows the car's software: a string
    ("SentryModeStateAware", sometimes "Aware") or an object holding one; anything else is unknown."""
    try:
        value = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    except ValueError:
        value = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    if isinstance(value, dict):
        value = next((v for v in value.values() if isinstance(v, str)), "")
    text = str(value or "").strip().strip('"').lower().replace("sentrymodestate", "")
    return text if text in STATES else ""


class Sentry:
    """The car's Sentry state and the last EVENTS times it noticed someone. `react` is called (on its own thread) on a new PANIC, at most
    once per COOLDOWN_S; what it says is kept with the event."""

    def __init__(self, react=None, clock=time.time, background: bool = True):
        self.react, self.clock, self.background = react, clock, background
        self.state, self.since = "", None
        self.events: deque = deque(maxlen=EVENTS)
        self.heard: float | None = None
        self.online: bool | None = None
        self._reacted = 0.0
        self._n = 0
        self._lock = threading.Lock()

    def observe(self, state: str, retained: bool = False) -> dict | None:
        """A SentryMode value from the car. A retained message (the broker's copy at a reconnect) only sets the state: it is old news."""
        now = self.clock()
        with self._lock:
            self.heard = now
            if not state or state == self.state:
                return None
            self.state, self.since = state, now
            if retained or state not in SAID:
                return None
            self._n += 1
            event = {"id": f"{int(now)}-{self._n}", "at": int(now), "state": state, "said": SAID[state]}
            self.events.append(event)
            answer = state == "panic" and self.react is not None and now - self._reacted >= COOLDOWN_S
            if answer:
                self._reacted = now
        if answer:
            if self.background:
                threading.Thread(target=self._answer, args=(event,), daemon=True).start()
            else:
                self._answer(event)
        return event

    def _answer(self, event: dict) -> None:
        try:
            did = self.react()
        except Exception as exc:  # noqa: BLE001 — the event stands even if the car didn't take the answer
            did = f"the answer failed: {exc}"
        with self._lock:
            event["said"] += f"; {did}"

    def connectivity(self, payload) -> None:
        try:
            status = str((json.loads(payload) or {}).get("Status") or "").upper()
        except (ValueError, AttributeError):
            return
        self.online = status == "CONNECTED" if status else self.online

    def view(self) -> dict:
        now = self.clock()
        with self._lock:
            return {"state": self.state or "unknown", "since": int(self.since) if self.since else None, "events": list(self.events),
                    "react": self.react is not None,
                    "telemetry": {"heard_s": int(now - self.heard) if self.heard else None, "online": self.online}}


# ── his tokens ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class Tokens:
    """tokens.json (tools/tesla_fleet.py wrote it at his sign-in): the access token, refreshed when under 10 minutes are left. Tesla returns
    a new refresh token each time, so the file is rewritten (atomically, 600) every refresh."""

    def __init__(self, path: Path = TOKENS, post=None, clock=time.time):
        self.path, self.clock = path, clock
        self.post = post or _post_form
        self._lock = threading.Lock()
        self.problem = ""

    def get(self) -> str:
        with self._lock:
            try:
                keep = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.problem = f"no tokens at {self.path}: sign in (python tools/tesla_fleet.py login-url, then exchange)"
                raise RuntimeError(self.problem) from None
            if int(keep.get("expires_at") or 0) - self.clock() > 600:
                self.problem = ""
                return keep["access_token"]
            code, got = self.post(AUTH, {"grant_type": "refresh_token", "client_id": keep.get("client_id", ""),
                                         "refresh_token": keep.get("refresh_token", "")})
            if code != 200 or not got.get("access_token"):
                self.problem = f"Tesla refused the token refresh (HTTP {code} {got.get('error', '')}): sign in again"
                raise RuntimeError(self.problem)
            keep.update({k: got[k] for k in ("access_token", "refresh_token", "id_token") if got.get(k)})
            keep["expires_at"] = int(self.clock()) + int(got.get("expires_in") or 0)
            tmp = self.path.with_suffix(".new")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(keep, f)
            os.replace(tmp, self.path)
            self.problem = ""
            return keep["access_token"]


def _post_form(url: str, form: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": e.reason}


# ── the Fleet API, through the signing proxy ───────────────────────────────────────────────────────────────────────────────────────────────────
_LOCAL = ssl.create_default_context()
_LOCAL.check_hostname = False
_LOCAL.verify_mode = ssl.CERT_NONE           # the proxy's own certificate, made at start, on 127.0.0.1 inside this container


class Fleet:
    def __init__(self, tokens: Tokens, vin: str = VIN, call=None, sleep=time.sleep):
        self.tokens, self.vin, self.sleep = tokens, vin, sleep
        self.call = call or self._call

    def _call(self, method: str, path: str, body: dict | None, token: str) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(PROXY + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60, context=_LOCAL) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"error": e.reason}

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        return self.call(method, path, body, self.tokens.get())

    def command(self, name: str, body: dict | None = None, wake: bool = False) -> str:
        """One signed command; "" when the car took it, else why not in plain words. A sleeping car (408) is woken once when `wake`."""
        if not self.vin:
            return "no VIN (sudo bash core/install/tesla.sh --apply)"
        path = f"/api/1/vehicles/{self.vin}/command/{name}"
        def asleep(code, got) -> bool:                # the Fleet API says 408; the signing proxy says 500 "vehicle is offline or asleep"
            return code == 408 or "offline or asleep" in str((got or {}).get("error") if isinstance(got, dict) else "")
        code, got = self.request("POST", path, body or {})
        if asleep(code, got) and wake:
            self.request("POST", f"/api/1/vehicles/{self.vin}/wake_up")
            for _ in range(10):
                self.sleep(3)
                code, got = self.request("POST", path, body or {})
                if not asleep(code, got):
                    break
        result = got.get("response") if isinstance(got, dict) else None
        if code == 200 and isinstance(result, dict) and result.get("result") is not False:
            return ""
        why = (result or {}).get("reason") if isinstance(result, dict) else None
        return str(why or (got or {}).get("error") or f"HTTP {code}")[:200]

    # What the car says when it won't, in plain words; the first three mean there was nothing to do.
    CHARGE_REASONS = (("is_charging", "it was already charging"), ("not_charging", "it wasn't charging"),
                      ("complete", "the battery is already at its limit"), ("disconnected", "the car isn't plugged in"),
                      ("already_set", "the limit was already set to that"))

    def charge(self, action: str, percent=None) -> dict:
        """Charging on or off, or the charge limit (wakes a sleeping car once). {"ok", "said"} or {"ok": False, "error"}."""
        if action == "limit":
            try:
                p = int(percent)
            except (TypeError, ValueError):
                raise ValueError("a limit needs a percent") from None
            if not 50 <= p <= 100:
                raise ValueError("the car takes a charge limit from 50 to 100%")
            name, body, said = "set_charge_limit", {"percent": p}, f"charge limit set to {p}%"
        elif action in ("start", "stop"):
            name, body, said = f"charge_{action}", None, f"charging {action}{'ed' if action == 'start' else 'ped'}"
        else:
            raise ValueError("action is start, stop or limit")
        why = self.command(name, body, wake=True)
        if not why:
            return {"ok": True, "said": said}
        reason = next((words for needle, words in self.CHARGE_REASONS if needle in why.lower()), why)
        if reason in ("it was already charging", "it wasn't charging", "the battery is already at its limit", "the limit was already set to that"):
            return {"ok": True, "said": f"nothing to do: {reason}"}
        return {"ok": False, "error": reason}

    def guard(self) -> str:
        """What the car does when Sentry's alarm goes off: flash the lights, lock the doors (no horn: the Captain)."""
        bits = []
        for name, done in (("flash_lights", "lights flashed"), ("door_lock", "doors locked")):
            why = self.command(name)
            bits.append(done if not why else f"{done.split()[0]} didn't: {why}")
        return ", ".join(bits)

    def telemetry_config(self, now: float | None = None) -> dict:
        ca = TELEMETRY_CA.read_text(encoding="ascii")
        return {"hostname": TELEMETRY_HOST, "port": TELEMETRY_PORT, "ca": ca, "fields": FIELDS, "alert_types": ["service"],
                "exp": int((time.time() if now is None else now) + CONFIG_DAYS * 86400), "prefer_typed": True}

    def push_telemetry(self) -> tuple[bool, str]:
        if not (self.vin and TELEMETRY_HOST):
            return False, "no VIN or telemetry host (sudo bash core/install/tesla.sh --apply)"
        code, got = self.request("POST", "/api/1/vehicles/fleet_telemetry_config", {"vins": [self.vin], "config": self.telemetry_config()})
        resp = (got or {}).get("response") or {}
        skipped = resp.get("skipped_vehicles") or {}
        if code != 200 or any(skipped.get(k) for k in ("missing_key", "unsupported_hardware", "unsupported_firmware", "max_configs")):
            return False, f"Tesla didn't take it: HTTP {code} {got.get('error') or skipped or ''}"[:300]
        try:
            PUSHED.write_text(json.dumps({"at": int(time.time())}), encoding="utf-8")
        except OSError:
            pass
        return True, f"sent to {resp.get('updated_vehicles', 0)} car(s); the car connects once it has synced"

    def telemetry_status(self) -> dict:
        code, got = self.request("GET", f"/api/1/vehicles/{self.vin}/fleet_telemetry_config")
        ecode, errs = self.request("GET", f"/api/1/vehicles/{self.vin}/fleet_telemetry_errors")
        cfg = (got or {}).get("response") or {}
        return {"code": code, "synced": cfg.get("synced"), "host": (cfg.get("config") or {}).get("hostname"),
                "fields": sorted((cfg.get("config") or {}).get("fields") or {}),
                "errors": ((errs or {}).get("response") or {}).get("fleet_telemetry_errors") if ecode == 200 else None}


TOKENS_ = Tokens()
FLEET = Fleet(TOKENS_)
SENTRY = Sentry(react=FLEET.guard if REACT else None)
STATE = {"mqtt": False, "proxy": None}


def proxy_up() -> bool:
    try:
        with urllib.request.urlopen(PROXY + "/health", timeout=3, context=_LOCAL) as r:
            return r.status == 200
    except (OSError, urllib.error.URLError):
        return False


def health() -> dict:
    deps = {"proxy": {"ready": proxy_up()}, "mqtt": {"ready": STATE["mqtt"]}}
    try:
        TOKENS_.get()
        deps["tokens"] = {"ready": True}
    except RuntimeError as exc:
        deps["tokens"] = {"ready": False, "detail": str(exc)}
    bad = [k for k, v in deps.items() if not v["ready"]]
    detail = "; ".join(deps[k].get("detail") or f"{k} not up" for k in bad)
    view = SENTRY.view()
    return READY.report(ready=not bad, detail=detail, deps=deps, sentry=view["state"], heard_s=view["telemetry"]["heard_s"])


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
        path = self.path.split("?", 1)[0]
        if path == "/health":
            got = health()
            self._send(200 if got["ready"] else 503, got)
        elif path == "/sentry":
            self._send(200, SENTRY.view())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10_000)) or b"{}")
        except ValueError:
            return self._send(400, {"ok": False, "error": "send JSON"})
        action = str((body or {}).get("action") or "")
        try:
            if path == "/sentry" and action in ("arm", "disarm"):
                why = FLEET.command("set_sentry_mode", {"on": action == "arm"}, wake=True)
                return self._send(200 if not why else 502, {"ok": not why, "said": f"Sentry Mode {'on' if action == 'arm' else 'off'}", "error": why})
            if path == "/command" and action in ("flash", "lock"):
                why = FLEET.command("flash_lights" if action == "flash" else "door_lock")
                return self._send(200 if not why else 502, {"ok": not why, "error": why})
            if path == "/charge":
                try:
                    got = FLEET.charge(action, (body or {}).get("percent"))
                except ValueError as exc:
                    return self._send(400, {"ok": False, "error": str(exc)})
                return self._send(200 if got["ok"] else 502, got)
            if path == "/telemetry" and action == "push":
                ok, said = FLEET.push_telemetry()
                return self._send(200 if ok else 502, {"ok": ok, "said": said})
            if path == "/telemetry" and action == "status":
                return self._send(200, {"ok": True, **FLEET.telemetry_status()})
        except RuntimeError as exc:                 # no tokens, a refused refresh
            return self._send(503, {"ok": False, "error": str(exc)})
        self._send(400, {"ok": False, "error": "POST /sentry arm|disarm, /command flash|lock, /charge start|stop|limit, /telemetry push|status"})

    def log_message(self, *args):   # quiet: the reporter asks every 15 s
        pass


def run_proxy() -> None:
    """Tesla's signing proxy on 127.0.0.1:4443 with a certificate made here (only this process talks to it); restarted if it ends."""
    tls = Path("/run/proxy")
    tls.mkdir(exist_ok=True)
    if not (tls / "tls.crt").exists():
        subprocess.run(["openssl", "req", "-x509", "-nodes", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:secp384r1", "-days", "3650",
                        "-subj", "/CN=localhost", "-keyout", str(tls / "tls.key"), "-out", str(tls / "tls.crt")],
                       check=True, capture_output=True)
    while True:
        subprocess.run([PROXY_TOOL, "-tls-key", str(tls / "tls.key"), "-cert", str(tls / "tls.crt"), "-key-file", str(PRIVATE),
                        "-host", "127.0.0.1", "-port", "4443"])
        time.sleep(5)


def listen() -> None:
    import paho.mqtt.client as mqtt   # here, so the tests (and CI) don't need paho

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="skippy-car-fleet")

    def on_connect(c, userdata, flags, reason, props=None):
        STATE["mqtt"] = not reason.is_failure
        if STATE["mqtt"]:
            c.subscribe([(f"{TOPIC_BASE}/+/v/SentryMode", 1), (f"{TOPIC_BASE}/+/connectivity", 1)])

    def on_disconnect(c, userdata, flags, reason, props=None):
        STATE["mqtt"] = False

    def on_message(c, userdata, msg):
        if msg.topic.endswith("/connectivity"):
            SENTRY.connectivity(msg.payload)
        else:
            SENTRY.observe(sentry_state(msg.payload), retained=bool(msg.retain))
        READY.progress()

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.reconnect_delay_set(1, 30)
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            client.loop_forever(retry_first_connection=True)
        except OSError:
            STATE["mqtt"] = False
            time.sleep(5)


def keep_config() -> None:
    """Pushes the streaming config again before it runs out (CONFIG_DAYS), once it has been pushed by hand the first time."""
    while True:
        time.sleep(86400)
        try:
            at = json.loads(PUSHED.read_text(encoding="utf-8")).get("at") or 0
            if time.time() - at > REPUSH_DAYS * 86400:
                print(json.dumps({"event": "telemetry_repush", "result": FLEET.push_telemetry()}), flush=True)
        except (OSError, ValueError, RuntimeError):
            pass


def main() -> None:
    for target in (run_proxy, listen, keep_config):
        threading.Thread(target=target, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
