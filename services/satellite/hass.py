"""Home Assistant, the way home-AI projects usually reach it: its REST API with a long-lived access token.

    GET  /api/states                       every entity and its state
    POST /api/services/<domain>/<service>  do something ({"entity_id": ..., ...})
    POST /api/template                     render a Jinja template (used here only to learn which area each entity is in)

Home Assistant is the hub: it already speaks to the lights, plugs, thermostats, speakers and so on (Hue, Zigbee, Matter, Alexa devices...), so Skippy
never talks to a device, only to Home Assistant. What he may do here is deliberately narrow, the standard guard rails:
  * only these domains (lights, switches, fans, covers, climate, media players, scenes, scripts, vacuums, input booleans);
  * locks, alarm panels and garage doors / gates are never touched unless HASS_SENSITIVE=1, and then only with an explicit confirm;
  * a spoken name must match ONE entity: with several close matches nothing happens and the choices are given back so Skippy can ask which;
  * every call is a named service on a named entity: no raw service calls from the model, no templates from the model.
The token and URL come from the vault (HASS_URL, HASS_TOKEN); they are never printed or returned.
"""
from __future__ import annotations

import re
import time

import requests

ALLOWED = ("light", "switch", "fan", "cover", "climate", "media_player", "scene", "script", "vacuum", "humidifier", "input_boolean")
READ_ONLY = ("sensor", "binary_sensor", "weather", "person", "sun")                  # may be asked about, never changed
SENSITIVE_DOMAINS = ("lock", "alarm_control_panel")
SENSITIVE_COVERS = ("garage", "gate", "door")                                         # a cover's device_class
MAX_LIST = 40
MAX_CALL_SECONDS = 8


class HassError(Exception):
    """Home Assistant did not answer, or refused. The message is safe to say aloud."""


def _norm(text: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", str(text).lower()).split() if w not in ("the", "a", "my", "in", "light", "lights", "lamp")] or \
           re.sub(r"[^a-z0-9 ]", " ", str(text).lower()).split()


class Hass:
    def __init__(self, base: str, token: str, get=requests.get, post=requests.post, clock=time.monotonic, sensitive: bool = False):
        self.base = base.rstrip("/")
        self._get, self._post, self.clock, self.sensitive = get, post, clock, sensitive
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._areas: dict[str, str] | None = None
        self._cache: tuple[float, list[dict]] | None = None

    # ── the wire ────────────────────────────────────────────────────────────────
    def _call(self, fn, path: str, **kw):
        try:
            r = fn(self.base + path, headers=self._headers, timeout=MAX_CALL_SECONDS, **kw)
        except requests.RequestException:
            raise HassError("I cannot reach Home Assistant right now.") from None
        if r.status_code == 401:
            raise HassError("Home Assistant does not accept my access token.")
        if r.status_code == 404:
            raise HassError("Home Assistant does not know that.")
        if r.status_code >= 400:
            raise HassError(f"Home Assistant said no ({r.status_code}).")
        return r

    def ping(self) -> dict:
        """{ok, version, location, entities} for the dashboard; never raises."""
        try:
            cfg = self._call(self._get, "/api/config").json()
            n = len(self.states(fresh=True))
            return {"ok": True, "version": str(cfg.get("version") or ""), "location": str(cfg.get("location_name") or ""), "entities": n}
        except (HassError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def states(self, fresh: bool = False) -> list[dict]:
        if not fresh and self._cache and self.clock() - self._cache[0] < 5:
            return self._cache[1]
        data = self._call(self._get, "/api/states").json()
        if not isinstance(data, list):
            raise HassError("Home Assistant gave me something I do not understand.")
        self._cache = (self.clock(), data)
        return data

    def areas(self) -> dict[str, str]:
        """entity_id -> area name, best effort (the template API); empty when it is not available."""
        if self._areas is None:
            tpl = ("{% set ns = namespace(r={}) %}{% for s in states %}{% set a = area_name(s.entity_id) %}"
                   "{% if a %}{% set ns.r = dict(ns.r, **{s.entity_id: a}) %}{% endif %}{% endfor %}{{ ns.r | tojson }}")
            try:
                got = self._call(self._post, "/api/template", json={"template": tpl}).text
                import json
                self._areas = {str(k): str(v) for k, v in json.loads(got).items()}
            except (HassError, ValueError, AttributeError):
                self._areas = {}
        return self._areas

    # ── what he may touch ───────────────────────────────────────────────────────
    def _domain(self, s: dict) -> str:
        return str(s.get("entity_id", "")).split(".", 1)[0]

    def _is_sensitive(self, s: dict) -> bool:
        d = self._domain(s)
        if d in SENSITIVE_DOMAINS:
            return True
        return d == "cover" and str((s.get("attributes") or {}).get("device_class") or "") in SENSITIVE_COVERS

    def _visible(self, s: dict) -> bool:
        d = self._domain(s)
        if d in READ_ONLY:
            return True
        if d in SENSITIVE_DOMAINS:
            return self.sensitive
        if d in ALLOWED:
            return not self._is_sensitive(s) or self.sensitive
        return False

    def _label(self, s: dict) -> str:
        return str((s.get("attributes") or {}).get("friendly_name") or s.get("entity_id"))

    def find(self, query: str, domains: tuple | None = None) -> tuple[dict | None, list[dict]]:
        """(the one entity, []) when the words name exactly one; (None, [close matches]) when several fit; (None, []) when none does."""
        query = str(query or "").strip()
        states = [s for s in self.states() if self._visible(s)]
        if domains:
            states = [s for s in states if self._domain(s) in domains]
        for s in states:
            if s["entity_id"] == query.lower():
                return s, []
        want = set(_norm(query))
        if not want:
            return None, []
        areas = self.areas()
        scored = []
        for s in states:
            have = set(_norm(self._label(s))) | set(_norm(areas.get(s["entity_id"], "")))
            name = set(_norm(self._label(s)))
            hit = want & (name | set(_norm(areas.get(s["entity_id"], ""))))
            if not hit or not want <= have:                              # every word he said must be in the name or the area
                continue
            scored.append((len(want & name) * 2 + len(hit), -len(name), s))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        if not scored:
            return None, []
        exact = [t for t in scored if set(_norm(self._label(t[2]))) == want]
        if len(exact) == 1:
            return exact[0][2], []
        top = [t for t in scored if t[0] == scored[0][0] and t[1] == scored[0][1]]
        if len(top) == 1 and len(scored) == 1:
            return scored[0][2], []
        if len(top) == 1 and scored[0][0] > scored[1][0]:
            return scored[0][2], []
        return None, [t[2] for t in scored[:6]]

    # ── the tools ───────────────────────────────────────────────────────────────
    def devices(self, area: str = "", domain: str = "") -> str:
        areas = self.areas()
        out = []
        for s in self.states():
            if self._domain(s) not in ALLOWED or not self._visible(s):
                continue
            if domain and self._domain(s) != domain:
                continue
            if area and area.lower() not in areas.get(s["entity_id"], "").lower():
                continue
            out.append(f"{self._label(s)} ({s.get('state')})")
        if not out:
            return "Nothing matches in the house."
        extra = f"; and {len(out) - MAX_LIST} more" if len(out) > MAX_LIST else ""
        return "; ".join(out[:MAX_LIST]) + extra

    def state_of(self, query: str) -> str:
        s, close = self.find(query)
        if s is None:
            return self._ambiguous(query, close)
        a = s.get("attributes") or {}
        unit = a.get("unit_of_measurement") or ""
        extra = ""
        if self._domain(s) == "climate":
            extra = f", currently {a.get('current_temperature')} degrees, set to {a.get('temperature')}"
        if self._domain(s) == "light" and a.get("brightness") is not None:
            extra = f", at {round(int(a['brightness']) / 255 * 100)} percent"
        return f"{self._label(s)} is {s.get('state')}{(' ' + unit) if unit else ''}{extra}."

    def _ambiguous(self, query: str, close: list[dict]) -> str:
        if not close:
            return f"I cannot find anything in the house called {query}."
        return "That could be " + ", or ".join(self._label(c) for c in close) + ". Ask him which one; do not guess."

    def control(self, target: str, action: str, value=None, confirm: bool = False) -> str:
        action = str(action or "").lower().strip().replace(" ", "_")
        s, close = self.find(target)
        if s is None:
            return self._ambiguous(target, close)
        domain, eid, label = self._domain(s), s["entity_id"], self._label(s)
        if domain in READ_ONLY:
            return f"{label} can only be read, not changed."
        if self._is_sensitive(s) and not confirm:
            return f"{label} needs him to confirm out loud first. Ask him, and only call again with confirm set once he has said yes."
        service, data = self._plan(domain, action, value, s)
        if service is None:
            return f"I cannot {action.replace('_', ' ')} {label}."
        self._call(self._post, f"/api/services/{domain}/{service}", json={"entity_id": eid, **data})
        self._cache = None
        return f"Done: {label}, {action.replace('_', ' ')}{'' if value is None else ' ' + str(value)}."

    def _plan(self, domain: str, action: str, value, s: dict):
        number = None
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None, {}
        switchable = ("light", "switch", "fan", "input_boolean", "vacuum", "humidifier", "media_player", "climate")
        if action in ("on", "turn_on"):
            return ("turn_on" if domain in switchable + ("scene", "script") else None), {}
        if action in ("off", "turn_off"):
            return ("turn_off" if domain in switchable else None), {}
        if action == "toggle":
            return ("toggle" if domain in ("light", "switch", "fan", "input_boolean") else None), {}
        if action in ("activate", "run") and domain in ("scene", "script"):
            return "turn_on", {}
        if action == "set_brightness" and domain == "light" and number is not None:
            return "turn_on", {"brightness_pct": max(1, min(100, int(number)))}
        if action == "set_temperature" and domain == "climate" and number is not None:
            return "set_temperature", {"temperature": max(5, min(35, number))}
        if action == "set_volume" and domain == "media_player" and number is not None:
            return "volume_set", {"volume_level": max(0, min(100, number)) / 100}
        if domain == "cover" and action in ("open", "close", "stop"):
            return {"open": "open_cover", "close": "close_cover", "stop": "stop_cover"}[action], {}
        if domain == "media_player" and action in ("play", "pause", "stop", "next", "previous"):
            return {"play": "media_play", "pause": "media_pause", "stop": "media_stop", "next": "media_next_track", "previous": "media_previous_track"}[action], {}
        if domain == "vacuum" and action in ("start", "stop", "return_to_base"):
            return action, {}
        if domain == "lock" and self.sensitive:
            return {"lock": "lock", "unlock": "unlock"}.get(action), {}
        return None, {}


TOOLS = [
    {"name": "home_devices", "description": "List what is in the house that he can control, with each one's state. Optionally for one area (room) or one kind (light, switch, fan, cover, climate, media_player).",
     "input_schema": {"type": "object", "properties": {"area": {"type": "string"}, "domain": {"type": "string"}}}},
    {"name": "home_state", "description": "Ask the state of one thing in the house: a light, a temperature sensor, the thermostat, a door sensor. Use his own words for its name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "home_control", "description": "Do something to one thing in the house. Actions: on, off, toggle, set_brightness (value 1-100), set_temperature (value in degrees), set_volume (value 0-100), "
                                             "open, close, stop (covers), play, pause, next, previous (media players), activate (scenes, scripts). If several things fit the name, nothing happens and you must ask which. "
                                             "Locks and alarms need confirm=true, and only after he has said yes aloud.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "His words for it, e.g. 'living room lamp'."},
                                                       "action": {"type": "string"}, "value": {"type": "number"}, "confirm": {"type": "boolean"}},
                      "required": ["name", "action"]}},
]


def run_tool(hass: Hass, name: str, args: dict) -> str:
    """Never raises: whatever goes wrong comes back as a sentence Skippy can say."""
    args = args if isinstance(args, dict) else {}
    try:
        if name == "home_devices":
            return hass.devices(str(args.get("area") or ""), str(args.get("domain") or ""))
        if name == "home_state":
            return hass.state_of(str(args.get("name") or ""))
        if name == "home_control":
            return hass.control(str(args.get("name") or ""), str(args.get("action") or ""), args.get("value"), args.get("confirm") is True)
    except HassError as e:
        return str(e)
    return "I have no such tool."
