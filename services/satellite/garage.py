"""The garage door, through a ratgdo32 on the opener's wall-button terminals (rooms/garage.yaml): ESPHome's native API through aioesphomeapi, the
way esphome_link.py reaches the room pods, so the hub is the ratgdo's Home Assistant. No MyQ, no cloud (Chamberlain shut MyQ to everyone else in 2023).

    GARAGE_ESPHOME=esphome://skippy-garage.local     (or its IP; :6053 is the default port) in the vault turns it on; unset, nothing runs
    GARAGE_ESPHOME_KEY=...                           the API encryption key flash_garage.sh built it with

What it becomes (a House backend, so voice and the skippy-house MCP see it like any other thing):
  * cover.garage_door          "Garage door": open, close, stop, set_position; its position and whether the safety beam is blocked
  * light.garage_light         "Garage light": the opener's own light
  * binary_sensor.garage_motion / binary_sensor.garage_obstruction   read only
  * lock.garage_remotes        "Garage remotes": the opener's remote lockout (locked: the car remotes and keypad do nothing)
The guard rails are house.py's: a garage cover and a lock are hidden unless HASS_SENSITIVE=1 and need a spoken yes (confirm), and text chats
drop confirm (house_mcp.py, the reporter). On top of that this backend never closes the door while the opener reports an obstruction.
One narrow exception: close_on_departure() (only ever closes; autoclose.py calls it when the car leaves home, GARAGE_AUTOCLOSE).
The ratgdo pushes every change, so states() answers from what it last said; a dropped connection is dialled again (2 s doubling to 60 s).
"""
from __future__ import annotations

import asyncio
import threading
from urllib.parse import urlparse

from homekit import Loop
from house import House, HomeError

PORT = 6053
CONNECT_WAIT_S = 6.0                                  # how long a first question waits for the first connection (house_mcp.py starts cold)
BACKOFF_S = (2.0, 60.0)
OFFLINE = "The garage door opener is not answering, so I did nothing."
OBSTRUCTED = "I won't close the garage door: its safety sensor says something is in the way. Clear it, then ask me again."
ROWS = {                                              # entity id: its spoken name
    "cover.garage_door": "Garage door",
    "light.garage_light": "Garage light",
    "binary_sensor.garage_motion": "Garage motion",
    "binary_sensor.garage_obstruction": "Garage obstruction",
    "lock.garage_remotes": "Garage remotes",
}


def parse(url: str) -> tuple[str, int]:
    """esphome://host[:port] (or a bare host) -> (host, port)."""
    url = str(url or "").strip()
    u = urlparse(url if "://" in url else f"esphome://{url}")
    if not u.hostname:
        raise ValueError(f"GARAGE_ESPHOME should look like esphome://<ip>, not {url!r}")
    return u.hostname, u.port or PORT


def entity_of(info) -> str | None:
    """Which of ROWS an ESPHome entity is (by its kind and its name), or None for ratgdo's diagnostics."""
    kind = type(info).__name__
    words = f"{getattr(info, 'object_id', '') or ''} {getattr(info, 'name', '') or ''}".lower()
    if kind == "CoverInfo":
        return "cover.garage_door"
    if kind == "LightInfo":
        return "light.garage_light"
    if kind == "BinarySensorInfo" and "obstruction" in words:
        return "binary_sensor.garage_obstruction"
    if kind == "BinarySensorInfo" and "motion" in words:
        return "binary_sensor.garage_motion"
    if kind == "LockInfo" and "remote" in words:
        return "lock.garage_remotes"
    return None


class Garage(House):
    def __init__(self, url: str, noise_psk: str = "", sensitive: bool = False, *, make_client=None, log=print,
                 connect_wait: float = CONNECT_WAIT_S, backoff: tuple[float, float] = BACKOFF_S):
        self.host, self.port = parse(url)
        self.sensitive, self.log, self.connect_wait, self.backoff = sensitive, log, connect_wait, backoff
        if make_client is None:
            def make_client():
                from aioesphomeapi import APIClient                 # noqa: PLC0415
                return APIClient(self.host, self.port, None, noise_psk=noise_psk or None, client_info="skippy-hub-garage")
        self.make_client = make_client
        self.keys: dict[str, int] = {}                              # entity id -> the ratgdo's key for it
        self.values: dict[int, object] = {}                         # key -> its latest state, as the ratgdo pushed it
        self.client = None
        self.connected = False
        self.ready = threading.Event()                              # set at the first connection (and stays set)
        self.sessions = 0                                           # how many times it has connected (the tests watch reconnects)
        self._closed = False
        self._loop = Loop()
        self._task = asyncio.run_coroutine_threadsafe(self._keep(), self._loop.loop)

    # ── the connection ──────────────────────────────────────────────────────────
    async def _keep(self) -> None:
        delay = self.backoff[0]
        while not self._closed:
            was = self.sessions
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:                                  # noqa: BLE001 — unreachable, refused, a bad key: dial again later
                self.log(f"garage: cannot reach the ratgdo at {self.host}:{self.port} ({type(e).__name__}: {e})")
            self.connected, self.client = False, None
            if self._closed:
                break
            if self.sessions > was:
                delay = self.backoff[0]
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.backoff[1])

    async def _session(self) -> None:
        client = self.make_client()
        gone = asyncio.Event()

        async def on_stop(expected: bool) -> None:
            gone.set()

        await client.connect(on_stop=on_stop, login=True)
        try:
            entities, _services = await client.list_entities_services()
            keys = {}
            for e in entities:
                eid = entity_of(e)
                if eid and eid not in keys:
                    keys[eid] = e.key
            if "cover.garage_door" not in keys:
                raise HomeError(f"{self.host} has no garage door on it: is it flashed with rooms/garage.yaml?")
            self.keys = keys
            client.subscribe_states(self._on_state)
            self.client, self.connected = client, True
            self.sessions += 1
            self.ready.set()
            self.log(f"garage: connected to the ratgdo at {self.host} ({', '.join(sorted(keys))})")
            await gone.wait()
            self.log("garage: the ratgdo disconnected; dialling again")
        finally:
            self.connected, self.client = False, None
            try:
                await client.disconnect()
            except Exception:                                       # noqa: BLE001
                pass

    def _on_state(self, state) -> None:
        self.values[state.key] = state

    def close(self) -> None:
        self._closed = True
        self._task.cancel()

    # ── what the house sees ─────────────────────────────────────────────────────
    def _value(self, eid: str):
        key = self.keys.get(eid)
        return None if key is None else self.values.get(key)

    def _obstructed(self) -> bool:
        st = self._value("binary_sensor.garage_obstruction")
        return bool(getattr(st, "state", False))

    def _position(self) -> float | None:
        st = self._value("cover.garage_door")
        pos = getattr(st, "position", None)
        return float(pos) if isinstance(pos, (int, float)) else None

    def _door_state(self) -> str:
        st = self._value("cover.garage_door")
        if st is None:
            return "unknown"
        op = int(getattr(st, "current_operation", 0) or 0)
        if op == 1:
            return "opening"
        if op == 2:
            return "closing"
        pos = self._position()
        return "closed" if pos is not None and pos <= 0.01 else "open"

    def states(self, fresh: bool = False) -> list[dict]:
        if not self.ready.is_set():
            self.ready.wait(self.connect_wait)
        out = []
        for eid, key in (self.keys or {"cover.garage_door": None}).items():     # never reached yet: the door, unavailable (not "no such thing")
            attrs = {"friendly_name": ROWS[eid]}
            st = self.values.get(key)
            if not self.connected:
                state = "unavailable"
            elif eid == "cover.garage_door":
                state = self._door_state()
                pos = self._position()
                attrs.update({"device_class": "garage", "obstructed": self._obstructed()})
                if pos is not None:
                    attrs["current_position"] = int(round(pos * 100))
            elif eid == "lock.garage_remotes":
                state = {1: "locked", 2: "unlocked", 3: "jammed", 4: "locking", 5: "unlocking"}.get(int(getattr(st, "state", 0) or 0), "unknown")
            else:
                state = "unknown" if st is None else ("on" if getattr(st, "state", False) else "off")
            if eid == "binary_sensor.garage_motion":
                attrs["device_class"] = "motion"
            out.append({"entity_id": eid, "state": state, "attributes": attrs})
        return out

    def areas(self) -> dict[str, str]:
        return {eid: "garage" for eid in self.keys}

    def door(self) -> dict:
        """The door as autoclose.py reads it: {"state": open|closed|opening|closing|unavailable|unknown, "obstructed": bool}."""
        if not self.connected or "cover.garage_door" not in self.keys:
            return {"state": "unavailable", "obstructed": False}
        return {"state": self._door_state(), "obstructed": self._obstructed()}

    def close_on_departure(self) -> str:
        """THE ONE EXCEPTION to the spoken yes (the Captain, 2026-11-01: "when we leave in the car we should automatically close the garage
        door"). autoclose.py calls this when the car has driven away from home. It can only CLOSE: there is no argument, no action, no
        position, so nothing can use it to open the door. It refuses while the opener reports an obstruction, and it never retries
        (autoclose.py watches the result). Returns what it did: "closing", or "already closed"."""
        if self.door()["state"] == "closed":
            return "already closed"
        self._service("cover", "close_cover", {"entity_id": "cover.garage_door"})       # the obstruction refusal is in _service
        return "closing"

    def _service(self, domain: str, service: str, data: dict) -> None:
        eid = data["entity_id"]
        client = self.client
        if client is None or not self.connected:
            raise HomeError(OFFLINE)
        key = self.keys.get(eid)
        if key is None:
            raise HomeError("I do not know that one.")
        if domain == "cover":
            pos = self._position()
            if service == "close_cover" or (service == "set_cover_position" and pos is not None and data.get("position", 100) / 100 < pos):
                if self._obstructed():                              # house.control refuses first; this is the fresh, last word
                    raise HomeError(OBSTRUCTED)
            if service == "open_cover":
                call = lambda: client.cover_command(key, position=1.0)                          # noqa: E731
            elif service == "close_cover":
                call = lambda: client.cover_command(key, position=0.0)                          # noqa: E731
            elif service == "stop_cover":
                call = lambda: client.cover_command(key, stop=True)                             # noqa: E731
            elif service == "set_cover_position":
                p = max(0, min(100, int(data.get("position", 0)))) / 100
                call = lambda: client.cover_command(key, position=p)                            # noqa: E731
            else:
                raise HomeError(f"The garage door cannot {service.replace('_', ' ')}.")
        elif domain == "light":
            on = {"turn_on": True, "turn_off": False}.get(service)
            if service == "toggle":
                on = not getattr(self._value(eid), "state", False)
            if on is None:
                raise HomeError(f"The garage light cannot {service.replace('_', ' ')}.")
            call = lambda: client.light_command(key, state=on)                                  # noqa: E731
        elif domain == "lock":
            from aioesphomeapi import LockCommand                   # noqa: PLC0415
            cmd = {"lock": LockCommand.LOCK, "unlock": LockCommand.UNLOCK}.get(service)
            if cmd is None:
                raise HomeError(f"The garage remotes cannot {service}.")
            call = lambda: client.lock_command(key, cmd)                                        # noqa: E731
        else:
            raise HomeError("The garage has nothing like that.")

        async def send():
            call()
        self._loop.run(send())
        self.last = {"entity_id": eid, "service": service}
