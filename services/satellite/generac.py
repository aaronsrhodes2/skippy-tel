"""The Captain's Generac standby generator (G0071780, 2019, Evolution 2.0 controller; 2026-11-01): read only, local, no Generac account.

Generac's own Wi-Fi module talks only to Generac's Mobile Link cloud, so the Captain chose the local route instead (2026-11-01, "no vendor walls"):
genmon (github.com/jgyates/genmon) on a Raspberry Pi wired to the Evolution controller's 8-pin aux port (RS-232 over Modbus; the Wi-Fi module
unplugged), powered from the generator's own 12 V, so it keeps answering through an outage. genmon's web server (genserv.py, port 8000 by default)
answers JSON on the LAN; this reads it at GENMON_URL (in the vault: "http://genmon:8000"). If he turns on genmon's own login, GENMON_USER and
GENMON_PASSWORD (the Pi's, not any vendor's) sign in to it.

  * the generator is one read-only entity, generator.<name>: state ready / running / exercising / manual / stopped / warning, attributes
    battery_voltage, run_hours, utility_voltage, outage, fault (genmon's alarm text), maintenance_alert, service_a / service_b / battery_check
    (genmon's "Service A Due" etc.), exercise_time, last_exercise, last_run (epoch seconds) and a few more of genmon's own fields.
  * Watch (below, on the hub's loop): running in auto that is not an exercise is an outage: every pod says it and the portal DMs him; the power
    coming back, a fault (an alarm) and service due are told the same way; a hand start is said in one room; an exercise is only remembered.
Nothing here can change the generator: genmon can start, stop and set the exercise, and none of those commands is ever sent. Reading only.
Only the hub polls (SKIPPY_GENERAC_OWNER=1, set by hub.py), so one process turns changes into events; it writes what it knows to
state/generac/gen.json, which the house MCP and anything else that builds the house read (owner=False).
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from house import House, HomeError

POLL_S = 15                             # genmon reads the controller every few seconds; an outage should be told within about a minute
RETRY_S = (15.0, 300.0)
READS = ("status_json", "maint_json", "outage_json")   # genserv.py's /cmd/<command>: read-only commands only
NAME = "Generator"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "generator"


def flatten(node, out: dict | None = None) -> dict:
    """genmon's JSON is nested lists of one-key dicts ({"Status": [{"Engine": [{"Battery Voltage": "13.40 V"}, ...]}, ...]}): one flat {key: value}
    of the leaves (a list of plain strings, like a log, stays a list)."""
    out = {} if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)) and not (isinstance(v, list) and all(isinstance(x, str) for x in v) and v):
                flatten(v, out)
            else:
                out[str(k)] = v
    elif isinstance(node, list):
        for x in node:
            flatten(x, out)
    return out


def _num(v) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(v if v is not None else ""))
    return float(m.group()) if m else None


def _log_time(entry: str) -> float | None:
    """A genmon log line starts "MM/DD/YY HH:MM:SS ..." (the controller's own clock, local time)."""
    m = re.match(r"\s*(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})", str(entry))
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%m/%d/%y %H:%M:%S").timestamp()
    except ValueError:
        return None


def _entries(v) -> list:
    """A genmon log: a list of lines, or (Last Log Entries) just the newest line."""
    return v if isinstance(v, list) else [v] if isinstance(v, str) and v.strip() else []


def _last(entries, needle: str) -> float | None:
    times = [_log_time(e) for e in _entries(entries) if needle in str(e).lower()]
    return max((t for t in times if t), default=None)


def parse(base: str, status: dict, maint: dict, outage: dict) -> dict:
    """One generator as Skippy keeps it, from genmon's getbase and its status / maintenance / outage JSON."""
    s, m, o = flatten(status), flatten(maint), flatten(outage)
    engine, switch, base = str(s.get("Engine State", "")).lower(), str(s.get("Switch State", "")).lower(), str(base or "").strip().upper()
    alarm = str(s.get("System In Alarm") or "").strip()
    fault = alarm if alarm and alarm.lower() not in ("none", "no", "false", "0") else None
    if "exercis" in engine:
        state = "exercising"
    elif "running" in engine or "cranking" in engine or base.startswith("RUNNING"):
        state = "running"
    elif "off" in switch or base == "OFF":
        state = "stopped"
    elif "manual" in switch or base == "MANUAL":
        state = "manual"
    elif fault or base == "ALARM":
        state = "warning"
    else:
        state = "ready"
    run_log = s.get("Run Log") or s.get("Start / Stop Log") or []
    return {"name": NAME, "state": state, "auto": "auto" in switch, "switch": s.get("Switch State"), "engine": s.get("Engine State"),
            "model": m.get("Model"), "controller": m.get("Controller Detected") or m.get("Controller"),
            "battery_voltage": _num(s.get("Battery Voltage")), "run_hours": _num(m.get("Total Run Hours")),
            "utility_voltage": _num(o.get("Utility Voltage", s.get("Utility Voltage"))),
            "outage": str(o.get("System In Outage", "")).lower() == "yes", "outage_status": o.get("Status"),
            "fault": fault or ("an alarm, per genmon" if base == "ALARM" else None),
            "maintenance_alert": base == "SERVICEDUE",
            "service_a": m.get("Service A Due"), "service_b": m.get("Service B Due"), "battery_check": m.get("Battery Check Due"),
            "exercise_time": m.get("Exercise Time"),
            "logged_exercise": _last(run_log, "exercise"), "logged_outage": _last(run_log, "utility loss"),
            "last_alarm": (_entries(s.get("Alarm Log")) or [None])[0]}


class Genmon:
    """genmon's web server. `fetch()` -> (getbase, status, maint, outage); HomeError when it doesn't answer."""

    def __init__(self, url: str, user: str = "", password: str = "", http=None):
        import requests                                 # noqa: PLC0415
        self.url, self.user, self.password = url.rstrip("/"), user, password
        self.http = http or requests.Session()
        self.signed_in = False

    def _sign_in(self) -> None:
        r = self.http.post(f"{self.url}/", data={"username": self.user, "password": self.password}, timeout=10, verify=False)
        if r.status_code >= 400:
            raise HomeError(f"genmon refused its login ({r.status_code}): check GENMON_USER / GENMON_PASSWORD")
        self.signed_in = True

    def cmd(self, command: str):
        if command not in READS + ("getbase",):           # never start / stop / setexercise: reading only
            raise HomeError(f"{command} is not a read")
        if self.user and not self.signed_in:
            self._sign_in()
        try:
            r = self.http.get(f"{self.url}/cmd/{command}", timeout=10, verify=False)
        except Exception as e:                           # noqa: BLE001
            raise HomeError(f"genmon at {self.url} is not answering ({type(e).__name__})") from None
        if r.status_code in (401, 403) or (self.user and "<form" in r.text[:2000]):
            self.signed_in = False
            raise HomeError("genmon wants its login again")
        if r.status_code != 200:
            raise HomeError(f"genmon answered {r.status_code}")
        if command == "getbase":
            return r.text.strip().strip('"')
        try:
            return r.json()
        except ValueError:
            raise HomeError(f"genmon's {command} was not JSON") from None

    def fetch(self) -> tuple:
        return (self.cmd("getbase"),) + tuple(self.cmd(c) for c in READS)


class Mirror:
    """state/generac/gen.json: what the hub knows, for the other processes. Written whole."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / "gen.json"

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, data: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.path)


class Generac(House):
    """The generator as a house backend. The owner polls genmon on its own thread (a slow Pi never holds the hub) and publishes gen.json;
    a mirror only reads gen.json."""

    def __init__(self, mirror: Mirror, sensitive: bool = False, *, api=None, log=print, clock=time.time, poll_s: float = POLL_S,
                 owner: bool = True, start: bool = True):
        self.mirror, self.sensitive, self.log, self.clock, self.poll_s, self.owner = mirror, sensitive, log, clock, poll_s, owner
        self.api = api
        self.gen: dict | None = None               # parse() + last_exercise / last_run / running_since
        self.events: list[dict] = []               # {"kind": "outage"|"restored"|"manual"|"fault"|"maintenance"|"exercise", ...}
        self.lock = threading.Lock()
        self.connected = False
        self.why = "genmon has not answered yet" if api is not None else "GENMON_URL is not set (genmon on the generator's Pi)"
        self.ready = threading.Event()
        self._closed = False
        if owner and start and api is not None:
            threading.Thread(target=self._keep, daemon=True, name="generac").start()
        else:
            self.ready.set()

    def _keep(self) -> None:
        delay = RETRY_S[0]
        while not self._closed:
            try:
                self.poll()
                delay = RETRY_S[0]
                time.sleep(self.poll_s)
                continue
            except Exception as e:                       # noqa: BLE001 — the Pi off, Wi-Fi gone: say why on the Status map, try later
                was = self.connected
                self.connected = False
                self.why = str(e)[:160] if isinstance(e, HomeError) else f"genmon: {type(e).__name__}: {str(e)[:120]}"
                if was:
                    self.log(f"generac: {self.why}")
            self.ready.set()
            self.publish()
            time.sleep(delay)
            delay = min(delay * 2, RETRY_S[1])

    def poll(self) -> None:
        """One read of genmon; a change becomes an event for the Watch."""
        now = self.clock()
        new = parse(*self.api.fetch())
        was_connected = self.connected
        with self.lock:
            old = self.gen
            for k in ("last_exercise", "last_run", "running_since"):
                new[k] = (old or {}).get(k)
            for k, logged in (("last_exercise", "logged_exercise"), ("last_run", "logged_outage")):   # the controller's own log, when newer
                if new.get(logged) and (new[k] is None or new[logged] > new[k]):
                    new[k] = new[logged]
            if old is not None:
                self._changes(old, new, now)
            elif new["state"] in ("running", "exercising"):
                new["running_since"] = now
            self.gen = new
            self.connected, self.why = True, ""
        self.ready.set()
        if not was_connected:
            self.log(f"generac: genmon answers ({new['state']}, battery {new['battery_voltage']} V)")
        self.publish()

    def _changes(self, old: dict, new: dict, now: float) -> None:
        before, after = old["state"], new["state"]
        if after != before:
            if after in ("running", "exercising"):
                new["running_since"] = now
            if after == "running":
                self.events.append({"kind": "outage" if new.get("outage") or new.get("auto") else "manual", "at": now})
            if before in ("running", "exercising") and after not in ("running", "exercising"):
                ran = now - (old.get("running_since") or now)
                new["running_since"] = None
                new["last_run" if before == "running" else "last_exercise"] = now
                self.events.append({"kind": "restored" if before == "running" else "exercise", "at": now, "ran_s": ran})
            elif after == "exercising":
                new["last_exercise"] = now
        if new["fault"] and new["fault"] != old.get("fault"):
            self.events.append({"kind": "fault", "at": now, "what": new["fault"]})
        if new["maintenance_alert"] and not old.get("maintenance_alert"):
            self.events.append({"kind": "maintenance", "at": now, "what": service_words(new)})

    def publish(self) -> None:
        try:
            with self.lock:
                data = {"at": self.clock(), "connected": self.connected, "why": self.why, "gen": self.gen}
            self.mirror.save(data)
        except Exception:                                # noqa: BLE001 — the mirror is a convenience
            pass

    def _read_mirror(self) -> None:
        got = self.mirror.load()
        if not isinstance(got, dict):
            self.connected, self.why = False, "the hub has not published the generator (is it running, with GENMON_URL set?)"
            return
        stale = self.clock() - float(got.get("at") or 0) > max(4 * self.poll_s, 2 * RETRY_S[1])
        self.connected = bool(got.get("connected")) and not stale
        self.why = "the hub's generator mirror is stale" if stale else str(got.get("why") or "")
        with self.lock:
            self.gen = got.get("gen") if isinstance(got.get("gen"), dict) else None

    def take_events(self) -> list[dict]:
        with self.lock:
            out, self.events = self.events, []
        return out

    def close(self) -> None:
        self._closed = True

    def current(self) -> dict | None:
        if not self.owner:
            self._read_mirror()
        elif not self.ready.is_set():
            self.ready.wait(5)
        with self.lock:
            return dict(self.gen) if self.gen else None

    def summary(self) -> str:
        """The Status map's line: "Generator ready, battery 13.4 V" (or why genmon isn't answering)."""
        g = self.current()
        if not self.connected or g is None:
            return self.why
        return (f"{g['name']} {g['state']}" + (f", battery {g['battery_voltage']:g} V" if g.get("battery_voltage") else "")
                + (f", {g['fault']}" if g.get("fault") else "") + (", service due" if g.get("maintenance_alert") else ""))

    def attention(self) -> str | None:
        """The morning's line when something needs him (an alarm, service due, the switch off or in manual, genmon silent), else None."""
        g = self.current()
        if not self.connected:
            return f"I can't see the generator right now: {self.why}." if g is not None else None
        if g is None:
            return None
        bits = []
        if g.get("fault"):
            bits.append(f"it reports an alarm, {g['fault']}")
        if g.get("maintenance_alert"):
            bits.append("service is due" + (f" ({service_words(g)})" if service_words(g) else ""))
        if g["state"] == "stopped":
            bits.append("its switch is off, so it won't start in an outage")
        elif g["state"] == "manual":
            bits.append("its switch is in manual")
        return ("The generator: " + ", and ".join(bits) + ".") if bits else None

    # ── the house ───────────────────────────────────────────────────────────────
    def states(self, fresh: bool = False) -> list[dict]:
        g = self.current()
        if g is None:
            raise HomeError(f"The generator is not connected: {self.why}")
        keys = ("battery_voltage", "run_hours", "utility_voltage", "outage", "fault", "maintenance_alert", "service_a", "service_b",
                "battery_check", "exercise_time", "last_exercise", "last_run", "running_since", "switch", "engine", "model", "last_alarm")
        attrs = {k: g.get(k) for k in keys}
        attrs["friendly_name"] = g["name"]
        return [{"entity_id": f"generator.{slug(g['name'])}", "state": g["state"] if self.connected else "unavailable", "attributes": attrs}]

    def _service(self, domain: str, service: str, data: dict) -> None:
        raise HomeError("The generator is read only.")


def service_words(a: dict) -> str:
    return ", ".join(f"{label} {a[k]}" for k, label in (("service_a", "service A in"), ("service_b", "service B in"),
                                                       ("battery_check", "battery check in")) if a.get(k))


def generator_words(a: dict, now: float | None = None) -> str:
    """", battery 13.4 volts, last exercised 3 days ago, 412 run hours, service due" from a generator's attributes."""
    now = time.time() if now is None else now
    out = ""
    if a.get("running_since"):
        out += f", running for {ago(now - a['running_since']).replace(' ago', '').replace('just now', 'under a minute')}"
    if a.get("outage"):
        out += ", the utility power is out"
    if a.get("fault"):
        out += f", alarm: {a['fault']}"
    if isinstance(a.get("battery_voltage"), (int, float)):
        out += f", battery {a['battery_voltage']:g} volts"
    if a.get("last_exercise"):
        out += f", last exercised {ago(now - a['last_exercise'])}"
    if a.get("exercise_time"):
        out += f", exercises {a['exercise_time']}"
    if a.get("last_run"):
        out += f", last ran for an outage {ago(now - a['last_run'])}"
    if isinstance(a.get("run_hours"), (int, float)):
        out += f", {a['run_hours']:g} run hours"
    if a.get("maintenance_alert"):
        out += ", service due" + (f" ({service_words(a)})" if service_words(a) else "")
    return out


# ── the hub's side: announce and DM ─────────────────────────────────────────────
def ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} minutes ago"
    if s < 2 * 86400:
        h = round(s / 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    return f"{round(s / 86400)} days ago"


def portal_alert(portal: str, token: str, log=print, post=None, tries: int = 3, wait_s: float = 5.0):
    """alert(text, image=None): a DM to the Captain through the portal's POST /home/alert (home token), tried a few times; called on its own thread."""
    import urllib.request                               # noqa: PLC0415

    def send(text: str) -> bool:
        req = urllib.request.Request(f"{portal.rstrip('/')}/home/alert", data=json.dumps({"text": text}).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status == 200
    post = post or send

    def alert(text: str, image=None) -> None:
        for i in range(tries):
            try:
                if post(text):
                    return
            except Exception as e:                      # noqa: BLE001
                log(f"generac: DM try {i + 1} failed ({type(e).__name__})")
            time.sleep(wait_s)
        log("generac: the DM never went through")
    return alert


def spoken(ev: dict) -> str:
    kind = ev["kind"]
    if kind == "outage":
        return "The generator just started. The power must be out."
    if kind == "manual":
        return "The generator was started by hand."
    if kind == "restored":
        took = ago(ev.get("ran_s") or 0).replace(" ago", "").replace("just now", "about a minute")
        return f"The generator has stopped after {took}. The power's back."
    if kind == "fault":
        return f"The generator reports an alarm: {ev.get('what')}."
    if kind == "maintenance":
        return "The generator says service is due" + (f": {ev['what']}." if ev.get("what") else ".")
    return "The generator finished its exercise."


class Watch:
    """On the hub's loop: the generator's news. An outage and the power coming back are said in every room; an alarm or a hand start in the
    routine room; service due only DMed; an exercise only logged. Everything said is also DMed."""

    ALOUD = {"outage": "all", "restored": "all", "fault": "one", "manual": "one"}
    DM = ("outage", "restored", "fault", "maintenance", "manual")

    def __init__(self, gen: Generac, alert, *, log=print):
        self.gen, self.alert, self.log = gen, alert, log

    async def run(self, hub, routine_room=lambda: "", every: float = 2.0) -> None:
        while True:
            try:
                for ev in self.gen.take_events():
                    text = spoken(ev)
                    self.log(f"generac: {text}")
                    if ev["kind"] in self.DM:
                        threading.Thread(target=self.alert, args=(text, None), daemon=True, name="generac-dm").start()
                    how = self.ALOUD.get(ev["kind"])
                    rooms = dict(getattr(hub, "rooms", {}))
                    if how == "all":
                        targets = list(rooms.values())
                    elif how == "one":
                        one = rooms.get(routine_room()) or next(iter(rooms.values()), None)
                        targets = [one] if one is not None else []
                    else:
                        targets = []
                    for room in targets:
                        try:
                            await room.announce(text)
                        except Exception as e:          # noqa: BLE001
                            self.log(f"generac: could not announce it ({type(e).__name__}: {e})")
            except Exception as e:                      # noqa: BLE001 — never let the watcher die
                self.log(f"generac watch: {type(e).__name__}: {e}")
            await asyncio.sleep(every)


def generac_of(house) -> Generac | None:
    if isinstance(house, Generac):
        return house
    return next((p for p in getattr(house, "parts", []) if isinstance(p, Generac)), None)


def from_env(env: dict, folder: Path, sensitive: bool = False) -> Generac:
    owner = env.get("SKIPPY_GENERAC_OWNER") == "1" and bool(env.get("GENMON_URL"))
    api = Genmon(env["GENMON_URL"], env.get("GENMON_USER", ""), env.get("GENMON_PASSWORD", "")) if owner else None
    return Generac(Mirror(folder), sensitive=sensitive, api=api, owner=owner)


def build(env: dict, house, log=print) -> Watch | None:
    """The hub's watcher, when the house has the generator and this process polls genmon."""
    gen = generac_of(house)
    if gen is None or not gen.owner:
        return None
    import music as music_mod                           # noqa: PLC0415
    alert = portal_alert(env.get("PORTAL_URL") or music_mod.PORTAL, env.get("SKIPPY_HOME_TOKEN", ""), log=log) \
        if env.get("SKIPPY_HOME_TOKEN") else (lambda text, image=None: None)
    return Watch(gen, alert, log=log)


if __name__ == "__main__":
    import sys
    from ear import load_env                            # noqa: PLC0415
    env = load_env()
    if sys.argv[1:2] != ["status"] or not env.get("GENMON_URL"):
        print("usage: python generac.py status   (needs GENMON_URL in the vault: genmon on the generator's Pi, e.g. http://genmon:8000)")
        sys.exit(2)
    got = Genmon(env["GENMON_URL"], env.get("GENMON_USER", ""), env.get("GENMON_PASSWORD", "")).fetch()
    g = parse(*got)
    print(f"{g['name']}: {g['state']}{generator_words(g)}")
    print(f"  genmon base {got[0]!r}, switch {g['switch']!r}, engine {g['engine']!r}, model {g['model']!r}, last alarm {g['last_alarm']!r}")
    sys.exit(0)
