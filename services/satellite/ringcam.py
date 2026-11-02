"""The Captain's Ring cameras (a battery Video Doorbell and a Stick Up / Spotlight cam, 2026-11-01): their presses and motion as house events.

Ring has no local API, so this is Home Assistant's own route: the `ring_doorbell` library on Ring's cloud, with a refresh token he makes by
signing in HIMSELF once (`python ringcam.py signin`: his email, password and 2FA code are typed into that terminal, used once and never
stored; only Ring's refresh token is kept). Ring rotates the token on every refresh, so the live one lives in state/ring/token.json
(RING_REFRESH_TOKEN in the vault seeds it when that file is missing). The push channel (Firebase, the same one Ring's phone app uses) keeps
its own credentials in state/ring/fcm.json.

  * each camera is one read-only entity, camera.<name> ("Front Door"): state idle / motion / ringing / unavailable, attributes battery,
    last_motion, last_ding (epoch seconds), so home_state can say "Front Door is idle, battery 82 percent, last motion 12 minutes ago."
  * a press or a motion is pushed into `events` (Watch, below, drains it on the hub's loop): the pods say it, the portal DMs him a snapshot.
  * live video: not here. The cameras run on batteries and every live view drains them; the snapshot is the cheap picture.
Nothing here can change a camera: no siren, no light, no motion switch. Reading only.
Only the hub signs in (SKIPPY_RING_OWNER=1, set by hub.py): two processes refreshing one Ring token knock each other out. The hub writes what
it knows to state/ring/cams.json; the house MCP and anything else that builds the house read that mirror (owner=False).
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from homekit import Loop
from house import House, HomeError

USER_AGENT = "skippy-hub"
REFRESH_S = 600                         # battery, health and the last few events from history: every 10 minutes
MOTION_S = 60                           # a camera says "motion" for this long after one
RINGING_S = 60
RETRY_S = (30.0, 900.0)                 # Ring's cloud unreachable or the token refused: try again, backing off
ANNOUNCE_MOTION_S = 600                 # per camera: at most one motion announcement (and DM) per 10 minutes
ANNOUNCE_DING_S = 30                    # a press repeated within 30 s is the same visitor


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "camera"


def _epoch(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e11 else 1)
    if isinstance(v, datetime):
        return v.timestamp()
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


class TokenFile:
    """state/ring/token.json: Ring's OAuth token (a secret: never printed). Written whole, so a crash never leaves half a token."""

    def __init__(self, folder: Path, seed: str = ""):
        self.folder, self.seed = Path(folder), seed
        self.path, self.fcm_path, self.cams_path = self.folder / "token.json", self.folder / "fcm.json", self.folder / "cams.json"

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"refresh_token": self.seed} if self.seed else None

    def save(self, token: dict) -> None:
        self._write(self.path, token)

    def load_fcm(self) -> dict | None:
        try:
            return json.loads(self.fcm_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_fcm(self, creds: dict) -> None:
        self._write(self.fcm_path, creds)

    def save_cams(self, data: dict) -> None:
        self._write(self.cams_path, data)

    def load_cams(self) -> dict | None:
        try:
            return json.loads(self.cams_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, path: Path, data: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)


class Ring(House):
    """The cameras as a house backend. `connect` makes the logged-in ring_doorbell.Ring (tests pass a fake); its own loop on a thread,
    like the garage's ratgdo, so a slow cloud never holds the hub."""

    def __init__(self, tokens: TokenFile, sensitive: bool = False, *, connect=None, listen=None, log=print, clock=time.time,
                 refresh_s: float = REFRESH_S, owner: bool = True, start: bool = True):
        self.tokens, self.sensitive, self.log, self.clock, self.refresh_s = tokens, sensitive, log, clock, refresh_s
        self.connect = connect or self._connect
        self.listen = listen or self._listen
        self.cams: dict[int, dict] = {}            # Ring's device id -> {"name", "kind", "battery", "last_motion", "last_ding", "online"}
        self._devs: dict[int, object] = {}       # Ring's device id -> its ring_doorbell object (for snapshots)
        self.events: list[dict] = []               # {"kind": "ding"|"motion", "id", "name", "at"}: Watch drains it
        self.lock = threading.Lock()
        self.connected = False
        self.why = "not signed in yet: run `python ringcam.py signin` once"
        self.ready = threading.Event()
        self.ring = None
        self._closed = False
        self.owner = owner                         # False: a mirror of the hub's cams.json, never signed in
        self._loop = Loop() if owner and start else None
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._keep(), self._loop.loop)
        else:
            self.ready.set()

    # ── Ring's cloud ────────────────────────────────────────────────────────────
    async def _connect(self):
        from ring_doorbell import Auth, Ring as RingApi    # noqa: PLC0415
        token = self.tokens.load()
        if not token:
            raise HomeError(self.why)
        auth = Auth(USER_AGENT, token, self.tokens.save)
        ring = RingApi(auth)
        await ring.async_update_data()
        return ring

    async def _listen(self, ring, on_event):
        from ring_doorbell.listen import RingEventListener  # noqa: PLC0415
        listener = RingEventListener(ring, self.tokens.load_fcm(), self.tokens.save_fcm)
        listener.add_notification_callback(on_event)
        if not await listener.start():
            raise HomeError("Ring's push channel would not start")
        return listener

    async def _keep(self) -> None:
        delay = RETRY_S[0]
        while not self._closed:
            listener = None
            try:
                self.ring = await self.connect()
                await self.refresh()
                listener = await self.listen(self.ring, self.on_event)
                self.connected, self.why = True, ""
                self.ready.set()
                self.log(f"ring: connected ({', '.join(c['name'] for c in self.cams.values()) or 'no cameras'})")
                delay = RETRY_S[0]
                while not self._closed:
                    await asyncio.sleep(self.refresh_s)
                    await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as e:                       # noqa: BLE001 — refused token, cloud down: say why on the Status map, try later
                self.connected = False
                self.why = str(e)[:160] if isinstance(e, HomeError) else f"Ring's cloud: {type(e).__name__}: {str(e)[:120]}"
                self.ready.set()
                self.log(f"ring: {self.why}")
            finally:
                if listener is not None:
                    try:
                        await listener.stop()
                    except Exception:                    # noqa: BLE001
                        pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_S[1])

    async def refresh(self) -> None:
        """Battery and online state for every camera, and the latest motion / press from Ring's history (so a hub restart still knows them)."""
        ring = self.ring
        await ring.async_update_devices()
        for dev in ring.video_devices():
            try:
                await dev.async_update_health_data()
            except Exception:                            # noqa: BLE001 — a sleeping battery camera: keep the last health
                pass
            last = {"motion": None, "ding": None}
            try:
                for h in await dev.async_history(limit=10) or []:
                    kind, at = h.get("kind"), _epoch(h.get("created_at"))
                    if kind in last and at and (last[kind] is None or at > last[kind]):
                        last[kind] = at
            except Exception:                            # noqa: BLE001
                pass
            with self.lock:
                cam = self.cams.setdefault(dev.id, {"last_motion": None, "last_ding": None})
                cam.update({"name": dev.name, "kind": dev.kind, "battery": dev.battery_life,
                            "online": getattr(dev, "connection_status", None) != "offline"})
                for kind, at in last.items():
                    key = f"last_{kind}"
                    if at and (cam[key] is None or at > cam[key]):
                        cam[key] = at
                self._devs[dev.id] = dev
        self.publish()

    def on_event(self, ev) -> None:
        """Ring's push (on the listener's thread or loop): a press or a motion, once (an update to the same event is not news)."""
        kind = getattr(ev, "kind", "")
        if kind not in ("ding", "motion") or getattr(ev, "is_update", False):
            return
        now = self.clock()
        with self.lock:
            cam = self.cams.setdefault(ev.doorbot_id, {"name": ev.device_name, "kind": ev.device_kind, "battery": None, "online": True,
                                                       "last_motion": None, "last_ding": None})
            cam[f"last_{kind}"] = now
            self.events.append({"kind": kind, "id": ev.doorbot_id, "name": cam.get("name") or ev.device_name, "at": now})
        self.publish()

    def publish(self) -> None:
        """The mirror for the other processes (state/ring/cams.json): never a token, only what home_state says."""
        try:
            with self.lock:
                data = {"at": self.clock(), "connected": self.connected, "why": self.why, "cams": {str(k): v for k, v in self.cams.items()}}
            self.tokens.save_cams(data)
        except Exception:                                # noqa: BLE001 — the mirror is a convenience
            pass

    def _read_mirror(self) -> None:
        got = self.tokens.load_cams()
        if not isinstance(got, dict):
            self.connected, self.why = False, "the hub has not published the Ring cameras (is it running and signed in?)"
            return
        stale = self.clock() - float(got.get("at") or 0) > 3 * self.refresh_s
        self.connected = bool(got.get("connected")) and not stale
        self.why = "the hub's Ring mirror is stale" if stale else str(got.get("why") or "")
        with self.lock:
            self.cams = {int(k): v for k, v in (got.get("cams") or {}).items() if str(k).lstrip("-").isdigit() and isinstance(v, dict)}

    def take_events(self) -> list[dict]:
        with self.lock:
            out, self.events = self.events, []
        return out

    def snapshot(self, cam_id: int) -> bytes | None:
        """A fresh still from that camera (JPEG), or None: a battery camera that has gone back to sleep may not answer."""
        dev = self._devs.get(cam_id)
        if dev is None or self._loop is None:
            return None
        try:
            return self._loop.run(dev.async_get_snapshot(retries=6, delay=2), timeout=30)
        except HomeError:
            return None

    def close(self) -> None:
        self._closed = True

    # ── the house ───────────────────────────────────────────────────────────────
    def states(self, fresh: bool = False) -> list[dict]:
        if not self.owner:
            self._read_mirror()
        elif not self.ready.is_set():
            self.ready.wait(5)
        if not self.connected and not self.cams:
            raise HomeError(f"The Ring cameras are not connected: {self.why}")
        now = self.clock()
        out = []
        with self.lock:
            for cam in self.cams.values():
                if not self.connected or not cam.get("online", True):
                    state = "unavailable"
                elif cam.get("last_ding") and now - cam["last_ding"] < RINGING_S:
                    state = "ringing"
                elif cam.get("last_motion") and now - cam["last_motion"] < MOTION_S:
                    state = "motion"
                else:
                    state = "idle"
                out.append({"entity_id": f"camera.{slug(cam.get('name', ''))}", "state": state,
                            "attributes": {"friendly_name": cam.get("name") or "Ring camera", "battery": cam.get("battery"),
                                           "last_motion": cam.get("last_motion"), "last_ding": cam.get("last_ding"),
                                           "doorbell": "doorbell" in str(cam.get("kind", "")) or "doorbot" in str(cam.get("kind", "")) or cam.get("last_ding") is not None}})
        return out

    def _service(self, domain: str, service: str, data: dict) -> None:
        raise HomeError("The Ring cameras are read only.")


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


def spoken(event: dict) -> str:
    name = str(event.get("name") or "the door").strip()
    where = name if name.lower().startswith("the ") else f"the {name}"
    if event["kind"] == "ding":
        return f"Someone's at {where}."
    return f"Motion at {where}."


class Watch:
    """On the hub's loop: every second, take the cameras' new events; a press is said in every connected room, a motion in one (the
    routine room, whose pod also plays it upstairs), each at most once per its window per camera; every one said is also DMed with a snapshot."""

    def __init__(self, ring: Ring, alert, *, motion: bool = True, log=print, clock=time.time,
                 motion_s: float = ANNOUNCE_MOTION_S, ding_s: float = ANNOUNCE_DING_S):
        self.ring, self.alert, self.motion, self.log, self.clock = ring, alert, motion, log, clock
        self.windows = {"motion": motion_s, "ding": ding_s}
        self.last: dict[tuple, float] = {}

    def news(self) -> list[dict]:
        """The events worth telling, after the per-camera windows."""
        out = []
        for ev in self.ring.take_events():
            if ev["kind"] == "motion" and not self.motion:
                continue
            key = (ev["kind"], ev["id"])
            if self.clock() - self.last.get(key, -1e12) < self.windows[ev["kind"]]:
                continue
            self.last[key] = self.clock()
            out.append(ev)
        return out

    def tell_phone(self, ev: dict) -> None:
        """The Discord DM, with a snapshot when the camera gives one: on its own thread (a snapshot can take 15 s)."""
        def go():
            pic = None
            try:
                pic = self.ring.snapshot(ev["id"])
            except Exception as e:                      # noqa: BLE001
                self.log(f"ring: snapshot failed ({type(e).__name__})")
            self.alert(spoken(ev), pic)
        threading.Thread(target=go, daemon=True, name="ring-dm").start()

    async def run(self, hub, routine_room=lambda: "", every: float = 1.0) -> None:
        while True:
            try:
                for ev in self.news():
                    text = spoken(ev)
                    self.log(f"ring: {text}")
                    self.tell_phone(ev)
                    rooms = dict(getattr(hub, "rooms", {}))
                    if ev["kind"] == "ding":
                        targets = list(rooms.values())
                    else:
                        one = rooms.get(routine_room()) or next(iter(rooms.values()), None)
                        targets = [one] if one is not None else []
                    for room in targets:
                        try:
                            await room.announce(text)
                        except Exception as e:          # noqa: BLE001
                            self.log(f"ring: could not announce it ({type(e).__name__}: {e})")
            except Exception as e:                      # noqa: BLE001 — never let the watcher die
                self.log(f"ring watch: {type(e).__name__}: {e}")
            await asyncio.sleep(every)


def portal_alert(portal: str, token: str, log=print, post=None, tries: int = 3, wait_s: float = 5.0):
    """alert(text, jpeg|None): a DM to the Captain through the portal's POST /home/alert (home token), the snapshot as base64. Tried a few
    times; called on the Watch's own thread."""
    import base64                                       # noqa: PLC0415
    import urllib.request                               # noqa: PLC0415

    def send(text: str, image: bytes | None) -> bool:
        payload = {"text": text, **({"image": base64.b64encode(image).decode("ascii")} if image else {})}
        req = urllib.request.Request(f"{portal.rstrip('/')}/home/alert", data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status == 200
    post = post or send

    def alert(text: str, image: bytes | None = None) -> None:
        for i in range(tries):
            try:
                if post(text, image):
                    return
            except Exception as e:                      # noqa: BLE001
                log(f"ring: DM try {i + 1} failed ({type(e).__name__})")
            time.sleep(wait_s)
        log("ring: the DM never went through")
    return alert


def ring_of(house) -> Ring | None:
    if isinstance(house, Ring):
        return house
    return next((p for p in getattr(house, "parts", []) if isinstance(p, Ring)), None)


def build(env: dict, house, log=print) -> Watch | None:
    """The hub's watcher, when the house has the cameras (homectl.build_house: RING=1 or a token in state/ring)."""
    ring = ring_of(house)
    if ring is None or not ring.owner:                    # a mirror (not the signed-in owner) has no events to tell
        return None
    import music as music_mod                           # noqa: PLC0415
    alert = portal_alert(env.get("PORTAL_URL") or music_mod.PORTAL, env.get("SKIPPY_HOME_TOKEN", ""), log=log) \
        if env.get("SKIPPY_HOME_TOKEN") else (lambda text, image=None: None)
    return Watch(ring, alert, motion=env.get("RING_ANNOUNCE_MOTION", "on").lower() not in ("off", "0", "no"), log=log)


def state_folder() -> Path:
    return Path(__file__).resolve().parents[2] / "state" / "ring"


# ── the one-time sign-in (he types; nothing he types is kept) ───────────────────
async def _signin(tokens: TokenFile) -> int:
    import getpass                                      # noqa: PLC0415
    from ring_doorbell import Auth, Requires2FAError, Ring as RingApi   # noqa: PLC0415
    auth = Auth(USER_AGENT, None, tokens.save)
    user = input("Ring email: ").strip()
    password = getpass.getpass("Ring password (not shown, not kept): ")
    try:
        await auth.async_fetch_token(user, password)
    except Requires2FAError:
        await auth.async_fetch_token(user, password, input("The 2FA code Ring just sent you: ").strip())
    ring = RingApi(auth)
    await ring.async_update_data()
    names = [d.name for d in ring.video_devices()]
    await auth.async_close()
    print(f"Signed in. Cameras: {', '.join(names) or 'none found'}. Token kept in {tokens.path} (restart the hub to use it).")
    return 0


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["signin"]:
        sys.exit(asyncio.run(_signin(TokenFile(state_folder()))))
    print("usage: python ringcam.py signin")
    sys.exit(2)
