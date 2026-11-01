"""The Nest thermostat, through Google's Smart Device Management API (Device Access, 2026-11-01): his Nest Thermostat E has no Matter, so this
is the way in. It rides the portal's "Connect Google for Skippy" grant (portal/google_link.py): the hub borrows an hour's access token from the
portal with the home token (google_home.Google), and the Device Access project id comes with it (or NEST_PROJECT_ID in the vault).

What a thermostat becomes: climate.<name> ("Hallway thermostat": its own name in the Google Home app, else its room's), in the unit the
thermostat shows (Settings.temperatureScale: a Nest in Fahrenheit hears 70, not 21), exactly like Matter's thermostat, so Skippy's house tools
("what's the temperature", "set the thermostat to 70", "turn the heat off") need nothing new. Eco mode blocks a set point, so setting one
turns Eco off first. Google limits how often a thermostat may be asked, so the list is kept a minute; a "not connected" is kept five.

    python nest.py check               what it sees: each thermostat, the temperature, the mode and the set point
    python nest.py set <degrees>       set the (only) thermostat, then read it back
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from homekit import slug
from house import House, HomeError

SDM = "https://smartdevicemanagement.googleapis.com/v1"
T = "sdm.devices.traits."
C = "sdm.devices.commands."
CACHE_SECONDS = 60.0
NOT_CONNECTED_SECONDS = 300.0
MODES = {"HEAT": "heat", "COOL": "cool", "HEATCOOL": "heat_cool", "OFF": "off"}
CONNECT = "the Nest isn't connected to Skippy yet: press Connect Google for Skippy in Admin and tick the thermostat"


def to_display(celsius, fahrenheit: bool):
    if not isinstance(celsius, (int, float)):
        return None
    return round(celsius * 9 / 5 + 32) if fahrenheit else round(celsius, 1)


def from_display(value: float, fahrenheit: bool) -> float:
    return round((value - 32) * 5 / 9, 2) if fahrenheit else float(value)


def _call(method: str, url: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:                      # noqa: S310 — Google's own API
        raw = r.read()
    return json.loads(raw) if raw else {}


class Nest(House):
    def __init__(self, google, project: str = "", call=_call, clock=time.monotonic, sensitive: bool = False):
        self.google, self._project, self.call, self.clock, self.sensitive = google, project, call, clock, sensitive
        self._cache: tuple[float, list[dict]] | None = None
        self._index: dict[str, dict] = {}                   # entity_id -> {"name", "fahrenheit", "mode", "eco"}
        self._no: tuple[float, str] | None = None
        self.last: dict = {}

    # ── the account ─────────────────────────────────────────────────────────────
    def project(self) -> str:
        return self._project or getattr(self.google, "nest_project", "") or ""

    def _ask(self, method: str, path: str, body: dict | None = None) -> dict:
        if self._no and self.clock() < self._no[0]:
            raise HomeError(self._no[1])
        try:
            token = self.google.access_token()
            project = self.project()
            if not project:
                raise HomeError(CONNECT)
            return self.call(method, f"{SDM}/enterprises/{project}/{path}", token, body)
        except HomeError as e:
            self._no = (self.clock() + NOT_CONNECTED_SECONDS, str(e))
            raise
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                self._no = (self.clock() + NOT_CONNECTED_SECONDS, CONNECT)
                raise HomeError(CONNECT) from e
            if e.code == 429:
                raise HomeError("Google says the thermostat has been asked too often: try again in a minute.") from e
            if e.code == 400:
                raise HomeError("The thermostat refused that setting.") from e
            raise HomeError("Google's Nest service did not answer.") from e
        except Exception as e:                              # noqa: BLE001 — NotConnected from the portal, or the network
            if type(e).__name__ == "NotConnected":
                self._no = (self.clock() + NOT_CONNECTED_SECONDS, CONNECT)
                raise HomeError(CONNECT) from e
            raise HomeError("Google's Nest service did not answer.") from e

    # ── the house ───────────────────────────────────────────────────────────────
    def areas(self) -> dict[str, str]:
        return {eid: i["room"] for eid, i in self._index.items() if i.get("room")}

    def invalidate(self) -> None:
        self._cache = None

    def states(self, fresh: bool = False) -> list[dict]:
        if not fresh and self._cache and self.clock() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        try:
            devices = self._ask("GET", "devices").get("devices") or []
        except HomeError:
            if self._cache:
                return self._cache[1]
            raise
        out, index = [], {}
        for d in devices:
            if not str(d.get("type", "")).endswith(".THERMOSTAT"):
                continue
            tr = d.get("traits") or {}
            room = next((p.get("displayName") for p in d.get("parentRelations") or [] if p.get("displayName")), "")
            label = (tr.get(T + "Info") or {}).get("customName") or (f"{room} thermostat" if room else "thermostat")
            eid = f"climate.{slug(label)}"
            if eid in index:
                continue
            f = (tr.get(T + "Settings") or {}).get("temperatureScale") == "FAHRENHEIT"
            mode = (tr.get(T + "ThermostatMode") or {}).get("mode") or "OFF"
            eco = (tr.get(T + "ThermostatEco") or {}).get("mode") == "MANUAL_ECO"
            sp = tr.get(T + "ThermostatTemperatureSetpoint") or {}
            if eco:
                sp = tr.get(T + "ThermostatEco") or {}
            target = sp.get("coolCelsius") if mode == "COOL" else sp.get("heatCelsius")
            if mode == "HEATCOOL" and sp.get("heatCelsius") is not None and sp.get("coolCelsius") is not None:
                target = (sp["heatCelsius"] + sp["coolCelsius"]) / 2
            attrs = {"friendly_name": label, "current_temperature": to_display((tr.get(T + "Temperature") or {}).get("ambientTemperatureCelsius"), f),
                     "temperature": to_display(target, f), "unit_of_measurement": "°F" if f else "°C",
                     "hvac_action": ((tr.get(T + "ThermostatHvac") or {}).get("status") or "").lower(),
                     "humidity": (tr.get(T + "Humidity") or {}).get("ambientHumidityPercent")}
            if eco:
                attrs["preset_mode"] = "eco"
            out.append({"entity_id": eid, "state": MODES.get(mode, "unknown"), "attributes": attrs})
            index[eid] = {"name": d["name"].rsplit("/devices/", 1)[-1], "fahrenheit": f, "mode": mode, "eco": eco, "room": room,
                          "available": (tr.get(T + "ThermostatMode") or {}).get("availableModes") or []}
        self._index, self._cache = index, (self.clock(), out)
        return out

    def _command(self, device: str, command: str, params: dict) -> None:
        self._ask("POST", f"devices/{device}:executeCommand", {"command": C + command, "params": params})

    def _service(self, domain: str, service: str, data: dict) -> None:
        eid = data["entity_id"]
        if eid not in self._index:
            self.states(fresh=True)
        i = self._index.get(eid)
        if i is None:
            raise HomeError("I do not know that one any more.")
        started = self.clock()
        if service == "set_temperature":
            if i["mode"] == "OFF":
                raise HomeError("The thermostat is off. Ask him whether to turn the heat on first.")
            if i["eco"]:
                self._command(i["name"], "ThermostatEco.SetMode", {"mode": "OFF"})
            c = from_display(float(data["temperature"]), i["fahrenheit"])
            if not 9 <= c <= 32:                            # Nest's own range: 50-90 F
                raise HomeError(f"{data['temperature']} degrees is outside what the thermostat can be set to.")
            if i["mode"] == "HEATCOOL":                     # keep the band, centred on what he asked for
                self._command(i["name"], "ThermostatTemperatureSetpoint.SetRange", {"heatCelsius": c - 1.5, "coolCelsius": c + 1.5})
            elif i["mode"] == "COOL":
                self._command(i["name"], "ThermostatTemperatureSetpoint.SetCool", {"coolCelsius": c})
            else:
                self._command(i["name"], "ThermostatTemperatureSetpoint.SetHeat", {"heatCelsius": c})
        elif service == "turn_off":
            self._command(i["name"], "ThermostatMode.SetMode", {"mode": "OFF"})
        elif service in ("turn_on", "set_hvac_mode"):
            want = {"heat": "HEAT", "cool": "COOL", "heat_cool": "HEATCOOL", "off": "OFF"}.get(str(data.get("hvac_mode") or "heat"))
            if want not in i["available"] and i["available"]:
                raise HomeError(f"The thermostat can't do {str(data.get('hvac_mode') or 'heat').replace('_', ' and ')}.")
            self._command(i["name"], "ThermostatMode.SetMode", {"mode": want})
        else:
            raise HomeError("I cannot do that to the thermostat.")
        self.invalidate()
        self.last = {"entity_id": eid, "attempts": 1, "ms": round((self.clock() - started) * 1000)}

    # ── the bedtime routine ─────────────────────────────────────────────────────
    def bedtime(self, night: float | None = None) -> str | None:
        """One sentence for bedtime: the night set point when he chose one (SKIPPY_NIGHT_SETPOINT), else where it stands."""
        rows = self.states(fresh=True)
        if not rows:
            return None
        row = rows[0]
        a = row["attributes"]
        if night is not None and row["state"] in ("heat", "cool", "heat_cool") and a.get("temperature") != round(night):
            self._service("climate", "set_temperature", {"entity_id": row["entity_id"], "temperature": night})
            return f"I've set the thermostat to {round(night)} for the night; it's {a.get('current_temperature')} in here now."
        if row["state"] == "off":
            return f"The thermostat is off; it's {a.get('current_temperature')} degrees in here."
        return f"The thermostat is set to {a.get('temperature')}, and it's {a.get('current_temperature')} in here."


def main(argv=None) -> int:
    import argparse                                                     # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["check", "set"])
    ap.add_argument("degrees", nargs="?", type=float)
    args = ap.parse_args(argv)
    import google_home                                                  # noqa: PLC0415
    from ear import load_env                                            # noqa: PLC0415
    env = load_env()
    n = Nest(google_home.Google(env), env.get("NEST_PROJECT_ID", ""))
    try:
        rows = n.states(fresh=True)
        if args.cmd == "set":
            if args.degrees is None or len(rows) != 1:
                print("set needs the degrees, and exactly one thermostat")
                return 1
            n._service("climate", "set_temperature", {"entity_id": rows[0]["entity_id"], "temperature": args.degrees})
            rows = n.states(fresh=True)
    except HomeError as e:
        print(f"Nest: {e}")
        return 1
    for r in rows:
        a = r["attributes"]
        print(f"{a['friendly_name']} [{r['entity_id']}]: {r['state']}{' (eco)' if a.get('preset_mode') else ''}, "
              f"{a['current_temperature']}{a['unit_of_measurement']} now, set to {a['temperature']}, hvac {a['hvac_action'] or '-'}, "
              f"humidity {a.get('humidity')}%")
    if not rows:
        print("No thermostat in the Device Access project: tick it on Nest's page when connecting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
