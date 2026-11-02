"""The Captain's Generac standby generator (G0071780, 2019, Evolution controller, Generac's Mobile Link Wi-Fi module; 2026-11-01): read only.

Generac has no local API on the Wi-Fi module, so this is the route Home Assistant's community Generac integration takes (bentekkie/ha-generac,
after digitaldan's openHAB binding): Mobile Link's own web API at app.mobilelinkgen.com, behind Generac's Azure B2C sign-in. He signs in HIMSELF
once (`python generac.py signin`: his email and password typed into that terminal, used once and never stored); only the session cookies the
sign-in leaves are kept, in state/generac/session.json (GENERAC_SESSION_COOKIE in the vault, a Cookie header, seeds it when that file is
missing). Every poll writes back the cookies Mobile Link refreshes, so the session keeps itself alive; when Mobile Link finally refuses it,
the Status map and one DM say "sign in again".

  * the generator is one read-only entity, generator.<name>: state ready / running / exercising / warning / stopped / communication issue /
    offline, attributes battery_voltage, run_hours, protection_hours, last_seen, warning, maintenance_alert, fault (the E-code label),
    status_text, last_exercise, last_run (epoch seconds) and Mobile Link's other named properties as it sends them.
  * Watch (below, on the hub's loop): running that is not an exercise is an outage: the pods say it and the portal DMs him; the power coming
    back, a warning, a fault and a new maintenance alert are told the same way; an exercise is only remembered (home_state says the last one).
Nothing here can change the generator: no start, no stop, no exercise schedule. Reading only.
Only the hub signs in (SKIPPY_GENERAC_OWNER=1, set by hub.py), so one process refreshes the cookies; it writes what it knows to
state/generac/gen.json, which the house MCP and anything else that builds the house read (owner=False).
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from house import House, HomeError

API = "https://app.mobilelinkgen.com/api"
LOGIN = "https://generacconnectivity.b2clogin.com/generacconnectivity.onmicrosoft.com/B2C_1A_MobileLink_SignIn"
POLICY = "B2C_1A_SignUpOrSigninOnline"
POLL_S = 60                             # Mobile Link's own page refreshes about this often; HA polls every 30 s
RETRY_S = (30.0, 900.0)
STALE_S = 30 * 60                       # Mobile Link has not heard from the generator for this long: "offline" (its Wi-Fi or the cloud)
STATUS = {1: "ready", 2: "running", 3: "exercising", 4: "warning", 5: "stopped", 6: "communication issue"}   # apparatusStatus (ha-generac)
PROP_BATTERY, PROP_RUN_HOURS, PROP_PROTECTION = 69, 70, 31                      # ApparatusDetail.properties[].type
UA = "Mozilla/5.0 (skippy-hub; read only)"


class SignInNeeded(HomeError):
    """Mobile Link refused the session: he has to run `python generac.py signin` again."""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "generator"


def _epoch(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(apparatus: dict, detail: dict, now: float) -> dict:
    """One generator as Skippy keeps it, from Mobile Link's /v2/Apparatus/list entry and its /v1/Apparatus/details."""
    props = [p for p in (detail.get("properties") or []) if isinstance(p, dict)]
    by_type = {p.get("type"): p.get("value") for p in props}
    named = {}
    for p in props + [p for p in (detail.get("productInfo") or []) if isinstance(p, dict)]:
        if p.get("name") and p.get("type") not in (PROP_BATTERY, PROP_RUN_HOURS, PROP_PROTECTION) and isinstance(p.get("value"), (str, int, float)):
            named[str(p["name"])[:60]] = p["value"]
    seen = _epoch(detail.get("lastSeen"))
    connected = bool(detail.get("isConnected", apparatus.get("isConnected")))
    code = detail.get("apparatusStatus", apparatus.get("apparatusStatus"))
    state = STATUS.get(code, "unknown") if isinstance(code, int) else "unknown"
    if not connected or (seen is not None and now - seen > STALE_S):
        state = "offline"
    return {"name": detail.get("name") or apparatus.get("name") or "Generator", "state": state,
            "serial": detail.get("serialNumber") or apparatus.get("serialNumber"), "model": apparatus.get("modelNumber"),
            "battery_voltage": _num(by_type.get(PROP_BATTERY)), "run_hours": _num(by_type.get(PROP_RUN_HOURS)),
            "protection_hours": _num(by_type.get(PROP_PROTECTION)), "last_seen": seen,
            "warning": bool(detail.get("showWarning", apparatus.get("showWarning"))),
            "maintenance_alert": bool(detail.get("hasMaintenanceAlert")),
            "fault": (detail.get("eCodeLabel") or "").strip() or None,
            "status_label": detail.get("statusLabel"), "status_text": detail.get("statusText"),
            "properties": named}


class Session:
    """Mobile Link's cookies (secrets: never printed) in state/generac/session.json, and the generator mirror in gen.json. Written whole."""

    def __init__(self, folder: Path, seed: str = ""):
        self.folder, self.seed = Path(folder), seed.strip()
        self.path, self.mirror_path = self.folder / "session.json", self.folder / "gen.json"

    def load(self) -> dict | None:
        try:
            got = json.loads(self.path.read_text(encoding="utf-8"))
            return got if isinstance(got, dict) and got.get("cookies") else None
        except (OSError, ValueError):
            pass
        if self.seed:                                   # "name=value; name2=value2", copied once into the vault
            return {"cookies": [{"name": k.strip(), "value": v.strip(), "domain": "app.mobilelinkgen.com"}
                                for k, v in (c.split("=", 1) for c in self.seed.split(";") if "=" in c)]}
        return None

    def save(self, data: dict) -> None:
        self._write(self.path, data)

    def load_mirror(self) -> dict | None:
        try:
            return json.loads(self.mirror_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_mirror(self, data: dict) -> None:
        self._write(self.mirror_path, data)

    def _write(self, path: Path, data: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)


class MobileLink:
    """Mobile Link's web API over a requests.Session holding his cookies. `fetch()` -> [(apparatus, detail)]; SignInNeeded when refused."""

    def __init__(self, session: Session, http=None):
        import requests                                 # noqa: PLC0415
        self.store = session
        self.http = http or requests.Session()
        self.http.headers["User-Agent"] = UA
        got = session.load() or {}
        for c in got.get("cookies") or []:
            self.http.cookies.set(c["name"], c["value"], domain=c.get("domain") or "app.mobilelinkgen.com", path=c.get("path") or "/")
        self.csrf = got.get("csrf", "")
        self._last = self._jar()

    def _jar(self) -> list[dict]:
        return sorted(({"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.http.cookies),
                      key=lambda c: (c["domain"], c["name"]))

    def keep(self) -> None:
        """Mobile Link slides its cookies forward: write them back whenever they changed."""
        jar = self._jar()
        if jar != self._last:
            self.store.save({"cookies": jar, "csrf": self.csrf, "at": time.time()})
            self._last = jar

    def get(self, path: str):
        r = self.http.get(API + path, headers={"X-Csrf-Token": self.csrf} if self.csrf else {}, timeout=30, allow_redirects=False)
        if r.status_code == 204:
            return None
        if r.status_code in (301, 302, 401, 403) or "b2clogin" in r.headers.get("Location", ""):
            raise SignInNeeded("Mobile Link wants him to sign in again: run `python generac.py signin`")
        if r.status_code != 200:
            raise HomeError(f"Mobile Link answered {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise SignInNeeded("Mobile Link sent its sign-in page: run `python generac.py signin`") from None

    def fetch(self) -> list[tuple[dict, dict]]:
        out = []
        for a in self.get("/v2/Apparatus/list") or []:
            if not isinstance(a, dict) or a.get("type") != 0:          # 0: a generator (1 is a propane tank monitor)
                continue
            detail = self.get(f"/v1/Apparatus/details/{a.get('apparatusId')}") or {}
            out.append((a, detail if isinstance(detail, dict) else {}))
        self.keep()
        return out

    # ── the one-time sign-in (Generac's B2C flow, as ha-generac does it; nothing typed is kept) ──
    def sign_in(self, email: str, password: str) -> None:
        page = self.http.get(f"{API}/Auth/SignIn", params={"email": email}, timeout=30).text
        if not self._submit(page):
            settings = next((json.loads(line[len("var SETTINGS = "):-1]) for line in page.splitlines()
                             if line.startswith("var SETTINGS = ") and line.endswith(";")), None)
            if not settings or not settings.get("csrf") or not settings.get("transId"):
                raise HomeError("Generac's sign-in page has changed (no csrf / transId): the integration needs updating")
            self.csrf, tx = settings["csrf"], "StateProperties=" + settings["transId"]
            r = self.http.post(f"{LOGIN}/SelfAsserted", headers={"X-Csrf-Token": self.csrf}, params={"tx": tx, "p": POLICY},
                               data={"request_type": "RESPONSE", "signInName": email, "password": password}, timeout=30)
            if r.status_code != 200 or str((r.json() if r.text.strip().startswith("{") else {}).get("status")) != "200":
                raise HomeError("Generac refused that email and password")
            r = self.http.get(f"{LOGIN}/api/CombinedSigninAndSignup/confirmed", params={"csrf_token": self.csrf, "tx": tx, "p": POLICY}, timeout=30)
            if r.status_code != 200 or not self._submit(r.text):
                raise HomeError("Generac's sign-in did not hand back to Mobile Link (a verification step this code does not know?)")
        self._last = None
        self.keep()

    def _submit(self, page: str) -> bool:
        form = re.search(r'<form[^>]*action="([^"]+)"', page)
        fields = dict(re.findall(r'<input[^>]*name="(state|code)"[^>]*value="([^"]*)"', page))
        if not form or "state" not in fields or "code" not in fields:
            return False
        r = self.http.post(html.unescape(form.group(1)), data={k: html.unescape(v) for k, v in fields.items()}, timeout=30)
        if r.status_code != 200:
            raise HomeError(f"Mobile Link's sign-in hand-off answered {r.status_code}")
        return True


class Generac(House):
    """The generator as a house backend. The owner polls Mobile Link on its own thread (a slow cloud never holds the hub) and publishes
    gen.json; a mirror only reads gen.json."""

    def __init__(self, session: Session, sensitive: bool = False, *, api=None, log=print, clock=time.time, poll_s: float = POLL_S,
                 owner: bool = True, start: bool = True):
        self.session, self.sensitive, self.log, self.clock, self.poll_s, self.owner = session, sensitive, log, clock, poll_s, owner
        self.api = api
        self.gens: dict[str, dict] = {}            # Mobile Link's apparatus id -> parse() + last_exercise / last_run / running_since
        self.events: list[dict] = []               # {"kind": "outage"|"restored"|"warning"|"fault"|"maintenance"|"exercise"|"signin", ...}
        self.lock = threading.Lock()
        self.connected = False
        self.why = "not signed in yet: run `python generac.py signin` once"
        self.ready = threading.Event()
        self._closed = False
        self._signin_told = False
        if owner and start:
            threading.Thread(target=self._keep, daemon=True, name="generac").start()
        else:
            self.ready.set()

    def _keep(self) -> None:
        delay = RETRY_S[0]
        while not self._closed:
            try:
                if self.api is None:
                    if self.session.load() is None:
                        raise SignInNeeded(self.why)
                    self.api = MobileLink(self.session)
                self.poll()
                delay = RETRY_S[0]
                time.sleep(self.poll_s)
                continue
            except SignInNeeded as e:
                self.connected, self.why, self.api = False, str(e)[:160], None
                if not self._signin_told and self.session.load() is not None:      # he had signed in once and Mobile Link let go: tell him once
                    self._signin_told = True
                    with self.lock:
                        self.events.append({"kind": "signin", "at": self.clock()})
            except Exception as e:                       # noqa: BLE001 — cloud down, Wi-Fi gone: say why on the Status map, try later
                self.connected = False
                self.why = str(e)[:160] if isinstance(e, HomeError) else f"Mobile Link: {type(e).__name__}: {str(e)[:120]}"
            self.ready.set()
            self.log(f"generac: {self.why}")
            self.publish()
            time.sleep(delay)
            delay = min(delay * 2, RETRY_S[1])

    def poll(self) -> None:
        """One read of every generator on his account; a change of state becomes an event for the Watch."""
        now = self.clock()
        got = self.api.fetch()
        was_connected = self.connected
        with self.lock:
            for a, d in got:
                key = str(a.get("apparatusId"))
                new = parse(a, d, now)
                old = self.gens.get(key)
                for k in ("last_exercise", "last_run", "running_since"):
                    new[k] = (old or {}).get(k)
                if old is not None:
                    self._changes(old, new, now)
                elif new["state"] in ("running", "exercising"):
                    new["running_since"] = now
                self.gens[key] = new
            self.connected, self.why, self._signin_told = True, "", False
        self.ready.set()
        if not was_connected:
            self.log("generac: connected (" + (", ".join(g["name"] + " " + g["state"] for g in self.gens.values()) or "no generator on the account") + ")")
        self.publish()

    def _changes(self, old: dict, new: dict, now: float) -> None:
        name, before, after = new["name"], old["state"], new["state"]
        if after != before:
            if after in ("running", "exercising"):
                new["running_since"] = now
            if after == "running":
                self.events.append({"kind": "outage", "name": name, "at": now})
            if before in ("running", "exercising") and after not in ("running", "exercising"):
                ran = now - (old.get("running_since") or now)
                new["running_since"] = None
                new["last_run" if before == "running" else "last_exercise"] = now
                self.events.append({"kind": "restored" if before == "running" else "exercise", "name": name, "at": now, "ran_s": ran})
            elif after == "exercising":
                new["last_exercise"] = now
        if new["fault"] and new["fault"] != old.get("fault"):
            self.events.append({"kind": "fault", "name": name, "at": now, "what": new["fault"]})
        elif (after == "warning" and before != "warning") or (new["warning"] and not old.get("warning")):
            self.events.append({"kind": "warning", "name": name, "at": now, "what": new.get("status_text") or new.get("status_label") or ""})
        if new["maintenance_alert"] and not old.get("maintenance_alert"):
            self.events.append({"kind": "maintenance", "name": name, "at": now, "what": new.get("status_text") or ""})

    def publish(self) -> None:
        """The mirror for the other processes (gen.json): never a cookie, only what home_state says."""
        try:
            with self.lock:
                data = {"at": self.clock(), "connected": self.connected, "why": self.why, "gens": dict(self.gens)}
            self.session.save_mirror(data)
        except Exception:                                # noqa: BLE001
            pass

    def _read_mirror(self) -> None:
        got = self.session.load_mirror()
        if not isinstance(got, dict):
            self.connected, self.why = False, "the hub has not published the generator (is it running and signed in?)"
            return
        stale = self.clock() - float(got.get("at") or 0) > max(3 * self.poll_s, 2 * RETRY_S[1])
        self.connected = bool(got.get("connected")) and not stale
        self.why = "the hub's generator mirror is stale" if stale else str(got.get("why") or "")
        with self.lock:
            self.gens = {str(k): v for k, v in (got.get("gens") or {}).items() if isinstance(v, dict)}

    def take_events(self) -> list[dict]:
        with self.lock:
            out, self.events = self.events, []
        return out

    def close(self) -> None:
        self._closed = True

    def generators(self) -> list[dict]:
        if not self.owner:
            self._read_mirror()
        elif not self.ready.is_set():
            self.ready.wait(5)
        with self.lock:
            return [dict(g) for g in self.gens.values()]

    def summary(self) -> str:
        """The Status map's line: "Generator ready, battery 13.4 V" (or why it isn't answering)."""
        gens = self.generators()
        if not self.connected and not gens:
            return self.why
        return "; ".join(f"{g['name']} {g['state'] if self.connected else 'unknown'}" + (f", battery {g['battery_voltage']:g} V" if g.get("battery_voltage") else "")
                         + (f", {g['fault']}" if g.get("fault") else "") + (", maintenance due" if g.get("maintenance_alert") else "") for g in gens)

    def attention(self) -> str | None:
        """The morning's line when something needs him (a fault, a warning, maintenance due, offline, signed out), else None."""
        gens = self.generators()
        if not self.connected:
            if "sign in" in self.why:
                return "The generator's Mobile Link sign-in has run out, so I can't watch it. Sign in again when you have a minute."
            return f"I can't see the generator right now: {self.why}." if gens else None
        out = []
        for g in gens:
            bits = []
            if g.get("fault"):
                bits.append(f"it reports a fault, {g['fault']}")
            elif g.get("warning") or g["state"] == "warning":
                bits.append("it shows a warning" + (f": {g['status_text']}" if g.get("status_text") else ""))
            if g.get("maintenance_alert"):
                bits.append("maintenance is due")
            if g["state"] in ("offline", "communication issue", "stopped"):
                bits.append({"offline": "Mobile Link hasn't heard from it lately", "communication issue": "it has a communication issue",
                             "stopped": "it is switched off, so it won't start in an outage"}[g["state"]])
            if bits:
                out.append(f"The {g['name'].lower() if g['name'].lower() == 'generator' else g['name']}: " + ", and ".join(bits) + ".")
        return " ".join(out) or None

    # ── the house ───────────────────────────────────────────────────────────────
    def states(self, fresh: bool = False) -> list[dict]:
        gens = self.generators()
        if not self.connected and not gens:
            raise HomeError(f"The generator is not connected: {self.why}")
        out = []
        for g in gens:
            attrs = {k: g.get(k) for k in ("battery_voltage", "run_hours", "protection_hours", "last_seen", "warning", "maintenance_alert",
                                           "fault", "status_text", "last_exercise", "last_run", "running_since", "model", "serial")}
            attrs.update({"friendly_name": g["name"] if "generator" in g["name"].lower() else f"{g['name']} generator",
                          "properties": g.get("properties") or {}})
            out.append({"entity_id": f"generator.{slug(g['name'])}", "state": g["state"] if self.connected else "unavailable", "attributes": attrs})
        return out

    def _service(self, domain: str, service: str, data: dict) -> None:
        raise HomeError("The generator is read only.")


def generator_words(a: dict, now: float | None = None) -> str:
    """", battery 13.4 volts, last exercised 3 days ago, 412 run hours, maintenance due" from a generator's attributes."""
    from ringcam import ago                             # noqa: PLC0415
    now = time.time() if now is None else now
    out = ""
    if a.get("running_since"):
        out += f", running for {ago(now - a['running_since']).replace(' ago', '').replace('just now', 'under a minute')}"
    if a.get("fault"):
        out += f", fault {a['fault']}"
    elif a.get("warning") and a.get("status_text"):
        out += f", warning: {a['status_text']}"
    if isinstance(a.get("battery_voltage"), (int, float)):
        out += f", battery {a['battery_voltage']:g} volts"
    if a.get("last_exercise"):
        out += f", last exercised {ago(now - a['last_exercise'])}"
    if a.get("last_run"):
        out += f", last ran for an outage {ago(now - a['last_run'])}"
    if isinstance(a.get("run_hours"), (int, float)):
        out += f", {a['run_hours']:g} run hours"
    if a.get("maintenance_alert"):
        out += ", maintenance due"
    elif a.get("status_text") and not a.get("fault") and not a.get("warning"):
        out += f" ({a['status_text']})"
    return out


# ── the hub's side: announce and DM ─────────────────────────────────────────────
def spoken(ev: dict) -> str:
    name = str(ev.get("name") or "generator")
    the = "The generator" if name.lower() == "generator" else f"The {name}"
    kind = ev["kind"]
    if kind == "outage":
        return f"{the} just started. The power must be out."
    if kind == "restored":
        from ringcam import ago                         # noqa: PLC0415
        took = ago(ev.get("ran_s") or 0).replace(" ago", "").replace("just now", "about a minute")
        return f"{the} has stopped after {took}. The power's back."
    if kind == "fault":
        return f"{the} reports a fault: {ev.get('what')}."
    if kind == "warning":
        return f"{the} shows a warning{': ' + ev['what'] if ev.get('what') else ''}."
    if kind == "maintenance":
        return f"{the} says maintenance is due{': ' + ev['what'] if ev.get('what') else ''}."
    if kind == "signin":
        return "The generator's Mobile Link sign-in has run out. Run python generac.py signin on skippy-pc so I can watch it again."
    return f"{the} finished its exercise."


class Watch:
    """On the hub's loop: the generator's news. An outage and the power coming back are said in every room; a fault or a warning in the
    routine room; maintenance and a lost sign-in only DMed; an exercise only logged. Everything said is also DMed."""

    ALOUD = {"outage": "all", "restored": "all", "fault": "one", "warning": "one"}
    DM = ("outage", "restored", "fault", "warning", "maintenance", "signin")

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


def build(env: dict, house, log=print) -> Watch | None:
    """The hub's watcher, when the house has the generator and this process is its owner."""
    gen = generac_of(house)
    if gen is None or not gen.owner:
        return None
    import music as music_mod                           # noqa: PLC0415
    import ringcam                                      # noqa: PLC0415 — the same DM through the portal's /home/alert
    alert = ringcam.portal_alert(env.get("PORTAL_URL") or music_mod.PORTAL, env.get("SKIPPY_HOME_TOKEN", ""), log=log) \
        if env.get("SKIPPY_HOME_TOKEN") else (lambda text, image=None: None)
    return Watch(gen, alert, log=log)


def state_folder() -> Path:
    return Path(__file__).resolve().parents[2] / "state" / "generac"


def _status(folder: Path) -> int:
    """`python generac.py status`: one read straight from Mobile Link with the kept session (no hub needed), what home_state would say."""
    api = MobileLink(Session(folder))
    now = time.time()
    found = api.fetch()
    if not found:
        print("Signed in, but Mobile Link lists no generator on this account.")
    for a, d in found:
        g = parse(a, d, now)
        print(f"{g['name']}: {g['state']}{generator_words(g, now)}")
        print(f"  model {g['model']}, serial {g['serial']}, label {g['status_label']!r}, last seen {d.get('lastSeen')}")
        print(f"  other properties: {json.dumps(g['properties'])[:600]}")
    return 0


if __name__ == "__main__":
    import getpass
    import sys
    if sys.argv[1:2] == ["signin"]:
        store = Session(state_folder())
        email = input("Mobile Link (Generac) email: ").strip()
        password = getpass.getpass("Mobile Link password (not shown, not kept): ")
        api = MobileLink(store)
        try:
            api.sign_in(email, password)
            gens = api.fetch()
        except HomeError as e:
            print(f"Not signed in: {e}")
            sys.exit(1)
        print(f"Signed in. Generators: {', '.join(str(a.get('name')) for a, _ in gens) or 'none on this account yet'}. "
              f"Session kept in {store.path} (the hub picks it up within a minute of a restart).")
        sys.exit(0)
    if sys.argv[1:2] == ["status"]:
        sys.exit(_status(state_folder()))
    print("usage: python generac.py signin | status")
    sys.exit(2)
