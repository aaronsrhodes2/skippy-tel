"""Matter, through skippy-core's controller (core/compose.matter.yml: python-matter-server, the one Home Assistant uses). Open, local,
no account: Skippy pairs a device with its setup code and controls it over the home network. The controller holds the fabric; this
module is the House backend that talks to its WebSocket API (MATTER_URL, e.g. ws://skippy-core:5580/ws, reachable on the tailnet only).

What a node becomes (by its Descriptor's device types, endpoint by endpoint):
  * light.<name>    an on/off, dimmable or colour light (OnOff, LevelControl: on, off, toggle, brightness)
  * switch.<name>   a plug-in unit, or anything else with an OnOff cluster
  * climate.<name>  a thermostat (Thermostat cluster: the temperature, the set point, the mode). Temperatures are in the unit the
                    thermostat itself shows (its UI configuration cluster): a Nest set to Fahrenheit is spoken to in Fahrenheit.
The names and rooms he gives things live in state/home/matter.json beside HomeKit's home.json (git-ignored). Pairing:
homectl.py matter-pair <code> "Name" --area "Room" (the 11-digit code or the QR code's MT:... text); the Nest is shared from the Google
Home app (Settings > Linked Matter apps and services > Link apps), which hands over a code for an already-connected device.
"""
from __future__ import annotations

import itertools
import json
import re
import time
from pathlib import Path

from homekit import Loop, slug
from house import House, HomeError

ON_OFF, LEVEL, THERMOSTAT, THERMO_UI, DESCRIPTOR, BASIC = 6, 8, 513, 516, 29, 40
LIGHT_TYPES = {0x0100, 0x0101, 0x010C, 0x010D}       # on/off, dimmable, colour temperature, extended colour light
PLUG_TYPES = {0x010A, 0x010B}                         # on/off and dimmable plug-in units
THERMOSTAT_TYPES = {0x0301}
SKIP_TYPES = {0x0016, 0x0011, 0x0103, 0x0104, 0x0105, 0x000E, 0x0013}   # root node, power source, switches that only send, bridge, ...
MODES = {0: "off", 1: "heat_cool", 3: "cool", 4: "heat", 5: "emergency_heat", 6: "precooling", 7: "fan_only", 8: "dry", 9: "sleep"}
ASK_SECONDS = 15.0
RETRY_SECONDS = 0.8                                   # the same second chance HomeKit gets (homekit.py): a dozing device answers the next time
PAIR_SECONDS = 300.0                                  # commissioning a new device over Bluetooth takes a minute or two


def _field(struct, number: int, name: str):
    """A struct field from the controller, which keys them by field id ("0") or by name, depending on the cluster."""
    if isinstance(struct, dict):
        return struct.get(str(number), struct.get(name))
    return None


def device_types(attrs: dict, ep: int) -> set[int]:
    out = set()
    for item in attrs.get(f"{ep}/{DESCRIPTOR}/0") or []:
        t = _field(item, 0, "deviceType")
        if isinstance(t, int):
            out.add(t)
    return out


def endpoints(attrs: dict) -> list[int]:
    return sorted({int(k.split("/", 1)[0]) for k in attrs if re.match(r"^\d+/", k)})


def to_display(centi_c, fahrenheit: bool):
    if not isinstance(centi_c, (int, float)):
        return None
    c = centi_c / 100
    return round(c * 9 / 5 + 32) if fahrenheit else round(c, 1)


def from_display(value: float, fahrenheit: bool) -> int:
    c = (value - 32) * 5 / 9 if fahrenheit else value
    if not 5 <= c <= 35:
        raise HomeError(f"{value} degrees is outside what a thermostat should be set to.")
    return int(round(c * 100))


class Names:
    """state/home/matter.json: {"nodes": {node_id: {"name", "area"}}}. Only names and rooms: the controller holds the keys."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"nodes": {}}
        return data if isinstance(data.get("nodes"), dict) else {"nodes": {}}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class Matter(House):
    def __init__(self, url: str, names: Names, connect=None, loop: Loop | None = None, clock=time.monotonic, sensitive: bool = False):
        self.url, self.names, self.clock, self.sensitive = url, names, clock, sensitive
        self._connect = connect or self._ws_connect
        self._loop = loop or Loop()
        self._ws = None
        self._session = None
        self._ids = itertools.count(1)
        self._cache: tuple[float, list[dict]] | None = None
        self._index: dict[str, dict] = {}                 # entity_id -> {"node", "ep", "kind", "fahrenheit", "mode"}
        self.last: dict = {}                              # the latest control: {"entity_id", "attempts", "ms"} (homectl.py prove)

    # ── the controller's WebSocket ──────────────────────────────────────────────
    async def _ws_connect(self):
        import aiohttp                                    # noqa: PLC0415 — the hub's own dependency (media.py uses it too)
        self._session = self._session or aiohttp.ClientSession()
        ws = await self._session.ws_connect(self.url, heartbeat=30)
        await ws.receive_json(timeout=ASK_SECONDS)        # the controller says who it is first (server_info)
        return ws

    async def _call(self, command: str, timeout: float = ASK_SECONDS, **args):
        if self._ws is None or getattr(self._ws, "closed", False):
            self._ws = await self._connect()
        mid = str(next(self._ids))
        await self._ws.send_json({"message_id": mid, "command": command, "args": args})
        deadline = self.clock() + timeout
        while True:
            left = deadline - self.clock()
            if left <= 0:
                raise HomeError("The house's Matter controller took too long to answer.")
            msg = await self._ws.receive_json(timeout=left)
            if not isinstance(msg, dict) or msg.get("message_id") != mid:
                continue                                  # an event, or the answer to something else
            if "error_code" in msg:
                raise HomeError(f"Matter said no: {msg.get('details') or msg.get('error_code')}.")
            return msg.get("result")

    def ask(self, command: str, timeout: float = ASK_SECONDS, **args):
        return self._loop.run(self._call(command, timeout=timeout, **args), timeout=timeout + 5)

    # ── pairing and names (homectl.py) ──────────────────────────────────────────
    def info(self) -> dict:
        """The controller's own summary: its fabric, whether Wi-Fi details and Bluetooth are ready for a brand-new device."""
        return self.ask("server_info") or {}

    def set_wifi(self, ssid: str, password: str) -> None:
        """What a brand-new device is told during Bluetooth setup. The controller keeps it in memory only: set before each pairing."""
        self.ask("set_wifi_credentials", ssid=ssid, credentials=password)

    def pair(self, code: str, name: str, area: str = "", network_only: bool = False) -> str:
        code = (code or "").strip().replace("-", "").replace(" ", "")
        if not (re.fullmatch(r"\d{11}|\d{21}", code) or code.startswith("MT:")):
            raise HomeError("A Matter code is the 11 digits under the QR code, or the QR code's own text (it starts with MT:).")
        node = self.ask("commission_with_code", timeout=PAIR_SECONDS, code=code, network_only=network_only) or {}
        node_id = node.get("node_id")
        if node_id is None:
            raise HomeError("The device was not added.")
        data = self.names.load()
        data["nodes"][str(node_id)] = {"name": name, "area": area}
        self.names.save(data)
        self.invalidate()
        return f"Paired {name}."

    def label(self, node_id: int, name: str = "", area: str = "") -> None:
        data = self.names.load()
        entry = data["nodes"].setdefault(str(node_id), {"name": "", "area": ""})
        if name:
            entry["name"] = name
        if area:
            entry["area"] = area
        self.names.save(data)
        self.invalidate()

    def forget(self, node_id: int) -> None:
        self.ask("remove_node", node_id=int(node_id))
        data = self.names.load()
        data["nodes"].pop(str(node_id), None)
        self.names.save(data)
        self.invalidate()

    # ── the House ───────────────────────────────────────────────────────────────
    def invalidate(self) -> None:
        self._cache = None

    def areas(self) -> dict[str, str]:
        self.states()
        names = self.names.load()["nodes"]
        return {eid: names.get(str(i["node"]), {}).get("area", "") for eid, i in self._index.items()
                if names.get(str(i["node"]), {}).get("area")}

    def states(self, fresh: bool = False) -> list[dict]:
        if not fresh and self._cache and self.clock() - self._cache[0] < 4:
            return self._cache[1]
        try:
            nodes = self.ask("get_nodes") or []
        except HomeError:
            if self._cache:
                return self._cache[1]
            raise
        names = self.names.load()["nodes"]
        out, index = [], {}
        for node in nodes:
            if not node.get("available", True):
                continue                                  # off the network: the others still answer
            for row in self._entities(node, names.get(str(node.get("node_id")), {})):
                out.append({k: row[k] for k in ("entity_id", "state", "attributes")})
                index[row["entity_id"]] = {k: row[k] for k in ("node", "ep", "kind", "fahrenheit", "mode")}
        self._index, self._cache = index, (self.clock(), out)
        return out

    def _entities(self, node: dict, named: dict) -> list[dict]:
        attrs = node.get("attributes") or {}
        node_id = node.get("node_id")
        base = named.get("name") or attrs.get(f"0/{BASIC}/5") or attrs.get(f"0/{BASIC}/3") or f"Matter device {node_id}"
        found = []
        for ep in endpoints(attrs):
            types = device_types(attrs, ep)
            if ep == 0 or (types and types <= SKIP_TYPES):
                continue
            if types & THERMOSTAT_TYPES or f"{ep}/{THERMOSTAT}/0" in attrs:
                found.append((ep, "climate"))
            elif f"{ep}/{ON_OFF}/0" in attrs:
                found.append((ep, "light" if types & LIGHT_TYPES else "switch"))
        rows = []
        for ep, kind in found:
            name = base if len(found) == 1 else f"{base} {ep}"
            row = {"node": node_id, "ep": ep, "kind": kind, "fahrenheit": False, "mode": None,
                   "entity_id": f"{kind}.{slug(name)}", "attributes": {"friendly_name": name}}
            if kind == "climate":
                f = attrs.get(f"{ep}/{THERMO_UI}/0") == 1
                mode = attrs.get(f"{ep}/{THERMOSTAT}/28")
                set_attr = 17 if mode == 3 else 18        # cooling set point when cooling, heating otherwise
                row.update(fahrenheit=f, mode=mode, state=MODES.get(mode, "unknown"))
                row["attributes"].update(current_temperature=to_display(attrs.get(f"{ep}/{THERMOSTAT}/0"), f),
                                         temperature=to_display(attrs.get(f"{ep}/{THERMOSTAT}/{set_attr}"), f),
                                         unit_of_measurement="°F" if f else "°C")
            else:
                row["state"] = "on" if attrs.get(f"{ep}/{ON_OFF}/0") else "off"
                level = attrs.get(f"{ep}/{LEVEL}/0")
                if kind == "light" and isinstance(level, int):
                    row["attributes"]["brightness"] = round(level * 255 / 254)
            rows.append(row)
        return rows

    def _service(self, domain: str, service: str, data: dict) -> None:
        eid = data["entity_id"]
        if eid not in self._index:
            self.states(fresh=True)
        if eid not in self._index:
            raise HomeError("I do not know that one any more.")
        i = self._index[eid]
        node, ep = i["node"], i["ep"]
        if i["kind"] == "climate":
            self._climate(node, ep, i, service, data)
            return
        want = None
        if service == "toggle":
            want = not self._read_on(node, ep)
        elif service == "turn_off":
            want = False
        elif service == "turn_on" and data.get("brightness_pct") is not None:
            level = max(1, min(254, round(int(data["brightness_pct"]) * 254 / 100)))
            self.ask("device_command", node_id=node, endpoint_id=ep, cluster_id=LEVEL, command_name="MoveToLevelWithOnOff",
                     payload={"level": level, "transitionTime": 0, "optionsMask": 0, "optionsOverride": 0})
            return
        elif service == "turn_on":
            want = True
        else:
            raise HomeError("I cannot do that to it.")
        started = self.clock()
        for attempt in (1, 2):                            # said, then read back from the device; a second try if it didn't change
            try:
                self.ask("device_command", node_id=node, endpoint_id=ep, cluster_id=ON_OFF, command_name="On" if want else "Off", payload={})
                if self._read_on(node, ep) == want:
                    self.last = {"entity_id": eid, "attempts": attempt, "ms": round((self.clock() - started) * 1000)}
                    return
            except HomeError:
                if attempt == 2:
                    raise
            time.sleep(RETRY_SECONDS)
        raise HomeError("I told it twice and it did not change. It may be unplugged or off the Wi-Fi.")

    def _read_on(self, node: int, ep: int) -> bool:
        got = self.ask("read_attribute", node_id=node, attribute_path=f"{ep}/{ON_OFF}/0") or {}
        return bool(got.get(f"{ep}/{ON_OFF}/0")) if isinstance(got, dict) else bool(got)

    def _climate(self, node: int, ep: int, i: dict, service: str, data: dict) -> None:
        if service == "set_temperature":
            value = from_display(float(data["temperature"]), i["fahrenheit"])
            if i["mode"] == 1:                            # heat and cool: keep the band, centred on what he asked for
                self.ask("write_attribute", node_id=node, attribute_path=f"{ep}/{THERMOSTAT}/18", value=value - 100)
                self.ask("write_attribute", node_id=node, attribute_path=f"{ep}/{THERMOSTAT}/17", value=value + 100)
            else:
                self.ask("write_attribute", node_id=node, attribute_path=f"{ep}/{THERMOSTAT}/{17 if i['mode'] == 3 else 18}", value=value)
        elif service == "turn_off":
            self.ask("write_attribute", node_id=node, attribute_path=f"{ep}/{THERMOSTAT}/28", value=0)
        elif service == "turn_on":
            self.ask("write_attribute", node_id=node, attribute_path=f"{ep}/{THERMOSTAT}/28", value=4 if i["mode"] in (None, 0) else i["mode"])
        else:
            raise HomeError("I cannot do that to the thermostat.")
