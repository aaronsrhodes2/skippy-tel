"""HomeKit, natively: Skippy pairs with the lights and switches itself over the local network, with the open-source `aiohomekit` library (the one Home
Assistant uses). No cloud, no Alexa, no hub, no Home Assistant.

A HomeKit accessory that shows "unpaired" on the network (the `sf` flag; yours do: the Leviton Decora Smart dimmers and switches) can be paired once with
the 8-digit setup code printed on it. After that Skippy reads and sets its characteristics directly: On, Brightness. Pairing keys and the names he gives each
thing live in `state/home/home.json` (git-ignored; the keys are secrets and are never printed or returned).

This module is the HomeKit backend of `house.House`: it makes Home Assistant-style entity dicts out of the accessories (a lightbulb is `light.<name>`, a
switch or outlet `switch.<name>`), so the guard rails and the spoken-name matching are the same as for any other backend.
aiohomekit is async; a worker thread runs its event loop so the rest of Skippy (sync) can ask.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path

from house import House, HomeError

LIGHTBULB, SWITCH, OUTLET = 0x43, 0x49, 0x47               # HomeKit service types (the first 8 hex digits of the UUID)
ON, BRIGHTNESS = 0x25, 0x08                                 # characteristic types
ASK_SECONDS = 12.0
RETRY_SECONDS = 0.8                                         # a Wi-Fi plug that dozed off usually answers the second time (the "tell Alexa twice" problem)
WARM_SECONDS = 30                                           # keep-warm: a cheap read of every switch this often, so its Wi-Fi never dozes off


def _short(uuid) -> int:
    try:
        return int(str(uuid).split("-", 1)[0], 16)
    except ValueError:
        return -1


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "thing"


class Loop:
    """An asyncio loop on its own thread; `run(coro)` waits for the answer."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="homekit-loop").start()

    def run(self, coro, timeout: float = ASK_SECONDS):
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)
        except TimeoutError:
            raise HomeError("The house took too long to answer.") from None
        except HomeError:
            raise
        except Exception as e:                              # noqa: BLE001 — a device that is off, unreachable, or refuses
            raise HomeError(f"I could not reach that ({type(e).__name__}).") from None


class Store:
    """state/home/home.json: {"devices": {id: {"name", "area", "pairing": {...}}}}. The pairing keys are secrets."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"devices": {}}
        return data if isinstance(data.get("devices"), dict) else {"devices": {}}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class HomeKit(House):
    def __init__(self, store: Store, controller_factory=None, loop: Loop | None = None, clock=time.monotonic, sensitive: bool = False):
        self.store, self.clock, self.sensitive = store, clock, sensitive
        self._loop = loop or Loop()
        self._factory = controller_factory or self._real_controller
        self._controller = None
        self._pairings: dict[str, object] = {}
        self._cache: tuple[float, list[dict]] | None = None
        self._index: dict[str, tuple[str, int, dict]] = {}        # entity_id -> (device id, aid, {"on": iid, "brightness": iid})
        self.last: dict = {}                                      # the latest control: {"entity_id", "attempts", "ms"} (homectl.py prove reads it)
        self._warm = None

    # ── the controller ──────────────────────────────────────────────────────────
    @staticmethod
    async def _real_controller():
        from aiohomekit import Controller                        # noqa: PLC0415
        from aiohomekit.zeroconf import ZeroconfServiceListener   # noqa: PLC0415
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf  # noqa: PLC0415
        azc = AsyncZeroconf()
        browser = AsyncServiceBrowser(azc.zeroconf, ["_hap._tcp.local.", "_hap._udp.local."], listener=ZeroconfServiceListener())   # aiohomekit looks for it
        controller = Controller(async_zeroconf_instance=azc)
        await controller.async_start()
        controller._skippy_browser = browser                      # keep it alive
        await asyncio.sleep(4)                                    # the browser needs a few seconds to hear the accessories announce themselves
        return controller

    async def _controller_ready(self):
        if self._controller is None:
            self._controller = await self._factory()
        return self._controller

    async def _pairing(self, device_id: str):
        if device_id not in self._pairings:
            data = self.store.load()["devices"].get(device_id) or {}
            if not data.get("pairing"):
                raise HomeError("That device is not paired.")
            controller = await self._controller_ready()
            self._pairings[device_id] = controller.load_pairing(device_id, data["pairing"])
        return self._pairings[device_id]

    # ── pairing (the command line, homectl.py) ───────────────────────────────────
    def discover(self, seconds: float = 8.0) -> list[dict]:
        """Every HomeKit accessory on the network: {id, name, host, paired}."""
        async def go():
            controller = await self._controller_ready()
            out = []
            async for d in controller.async_discover(seconds):
                desc = d.description
                out.append({"id": desc.id, "name": desc.name, "host": str(getattr(desc, "address", "") or ""), "paired": not bool(int(desc.status_flags or 0) & 1)})
            return out
        return self._loop.run(go(), timeout=seconds + 10)

    def pair(self, accessory_id: str, code: str, name: str, area: str = "") -> str:
        if not re.fullmatch(r"\d{3}-?\d{2}-?\d{3}", code or ""):
            raise HomeError("A setup code is eight digits, like 123-45-678.")
        code = f"{code[:3]}-{code[-5:-3]}-{code[-3:]}" if "-" not in code else code

        async def go():
            controller = await self._controller_ready()
            async for d in controller.async_discover(8):
                if d.description.id.lower() == accessory_id.lower():
                    finish = await d.async_start_pairing(accessory_id)
                    pairing = await finish(code)
                    return dict(pairing.pairing_data)
            raise HomeError("I cannot see that device on the network.")
        data = self._loop.run(go(), timeout=40)
        store = self.store.load()
        store["devices"][accessory_id] = {"name": name, "area": area, "pairing": data}
        self.store.save(store)
        self.invalidate()
        return f"Paired {name}."

    def unpair(self, accessory_id: str) -> None:
        store = self.store.load()
        store["devices"].pop(accessory_id, None)
        self.store.save(store)
        self._pairings.pop(accessory_id, None)
        self.invalidate()

    def label(self, accessory_id: str, name: str = "", area: str = "") -> None:
        store = self.store.load()
        if accessory_id not in store["devices"]:
            raise HomeError("That device is not paired.")
        if name:
            store["devices"][accessory_id]["name"] = name
        if area:
            store["devices"][accessory_id]["area"] = area
        self.store.save(store)
        self.invalidate()

    # ── the House ───────────────────────────────────────────────────────────────
    def invalidate(self) -> None:
        self._cache = None

    def areas(self) -> dict[str, str]:
        self.states()
        return {eid: self.store.load()["devices"][dev].get("area", "") for eid, (dev, _, _) in self._index.items()
                if dev in self.store.load()["devices"] and self.store.load()["devices"][dev].get("area")}

    def states(self, fresh: bool = False) -> list[dict]:
        if not fresh and self._cache and self.clock() - self._cache[0] < 4:
            return self._cache[1]
        devices = self.store.load()["devices"]
        out, index = [], {}
        for device_id, info in devices.items():
            try:
                rows = self._loop.run(self._read(device_id, info), timeout=ASK_SECONDS)
            except HomeError:
                continue                                         # one unreachable switch must not hide the others
            for eid, state, attrs, aid, iids in rows:
                out.append({"entity_id": eid, "state": state, "attributes": attrs})
                index[eid] = (device_id, aid, iids)
        self._index, self._cache = index, (self.clock(), out)
        return out

    async def _read(self, device_id: str, info: dict):
        pairing = await self._pairing(device_id)
        await pairing.async_populate_accessories_state(force_update=False)
        rows = []
        for acc in pairing.accessories:
            for svc in acc.services:
                kind = _short(svc.type)
                if kind not in (LIGHTBULB, SWITCH, OUTLET):
                    continue
                chars = {_short(c.type): c for c in svc.characteristics}
                if ON not in chars:
                    continue
                wanted = [(acc.aid, chars[ON].iid)] + ([(acc.aid, chars[BRIGHTNESS].iid)] if BRIGHTNESS in chars else [])
                got = await pairing.get_characteristics(wanted)
                is_on = bool((got.get(wanted[0]) or {}).get("value"))
                name = info.get("name") or acc.name or device_id
                attrs = {"friendly_name": name}
                if BRIGHTNESS in chars:
                    pct = (got.get(wanted[1]) or {}).get("value")
                    if pct is not None:
                        attrs["brightness"] = round(int(pct) * 255 / 100)
                domain = "light" if kind == LIGHTBULB else "switch"
                rows.append((f"{domain}.{slug(name)}", "on" if is_on else "off", attrs, acc.aid,
                             {"on": chars[ON].iid, "brightness": chars[BRIGHTNESS].iid if BRIGHTNESS in chars else None}))
        return rows

    def _service(self, domain: str, service: str, data: dict) -> None:
        eid = data["entity_id"]
        if eid not in self._index:
            self.states(fresh=True)
        if eid not in self._index:
            raise HomeError("I do not know that one any more.")
        device_id, aid, iids = self._index[eid]
        writes = []
        if service == "toggle":
            now = next((s for s in self._cache[1] if s["entity_id"] == eid), None) if self._cache else None
            service = "turn_off" if now and now["state"] == "on" else "turn_on"
        if service == "turn_off":
            writes.append((aid, iids["on"], False))
        elif service == "turn_on":
            writes.append((aid, iids["on"], True))
            if data.get("brightness_pct") is not None and iids.get("brightness"):
                writes.append((aid, iids["brightness"], int(data["brightness_pct"])))
        else:
            raise HomeError("I cannot do that to it.")

        want_on = writes[0][2]
        started = self.clock()

        async def go():
            """Write, then read it back: a switch that did not change is asked again on a fresh connection, once, so he never has to repeat himself."""
            for attempt in (1, 2):
                try:
                    pairing = await self._pairing(device_id)
                    await pairing.put_characteristics(writes)
                    got = await pairing.get_characteristics([(aid, iids["on"])])
                    if bool((got.get((aid, iids["on"])) or {}).get("value")) == want_on:
                        return attempt
                except Exception:                               # noqa: BLE001 — asleep, reconnecting, refused: the second try decides
                    if attempt == 2:
                        raise
                await self._forget(device_id)
                await asyncio.sleep(RETRY_SECONDS)
            raise HomeError("I told it twice and it did not change. It may be unplugged or off the Wi-Fi.")
        attempts = self._loop.run(go())
        self.last = {"entity_id": eid, "attempts": attempts, "ms": round((self.clock() - started) * 1000)}

    async def _forget(self, device_id: str) -> None:
        pairing = self._pairings.pop(device_id, None)
        close = getattr(pairing, "close", None)
        if close:
            try:
                await close()
            except Exception:                                   # noqa: BLE001 — it was already broken; that is why we are here
                pass

    # ── keep-warm ───────────────────────────────────────────────────────────────
    def keep_warm(self, every: float = WARM_SECONDS) -> None:
        """Read every paired switch's On every `every` seconds, in the background. A Wi-Fi plug that hears from us often keeps its radio awake and
        its connection open, so the first command after a quiet hour lands at once. Started by ear.py and hub.py (HOMEKIT_KEEP_WARM=0 turns it off)."""
        if self._warm is None and every > 0:
            self._warm = asyncio.run_coroutine_threadsafe(self._warm_loop(every), self._loop.loop)

    async def _warm_loop(self, every: float) -> None:
        while True:
            await asyncio.sleep(every)
            for device_id in list(self.store.load()["devices"]):
                try:
                    pairing = await self._pairing(device_id)
                    wanted = [(dev_aid, iids["on"]) for dev, dev_aid, iids in self._index.values() if dev == device_id]
                    if wanted:
                        await asyncio.wait_for(pairing.get_characteristics(wanted), ASK_SECONDS)
                except Exception:                               # noqa: BLE001 — asleep or away: the next command retries anyway
                    await self._forget(device_id)
