"""The Captain's cameras as house events (2026-11-01: a Reolink PoE doorbell and Reolink PoE cameras, all on the LAN, no vendor account; Ring
is off the list because its sign-in needs a text message that never reaches him at home).

Two local sources, no cloud anywhere:
  * Frigate on skippy-core (core/compose.cams.yml; FRIGATE_URL, the tailnet door :7355): what each camera saw. Its events (a person, a car)
    are read every POLL_S from /api/events; /api/stats says which cameras send frames; the snapshots come from it too.
  * the doorbell itself (REOLINK_DOORBELL=front_door=192.168.1.60, REOLINK_PASSWORD; REOLINK_USER, default admin): its button. Frigate
    doesn't know about a button, so the hub listens to the doorbell's own push channel (reolink_aio, the library Home Assistant's local
    Reolink integration uses) and turns each press into a "ding" on the Frigate camera of the same name.

  * each camera is one read-only entity, camera.<name>: state ringing / person / idle / unavailable, attributes last_person, last_car,
    last_ding (epoch seconds) and doorbell, so home_state can say "Front door is idle, last person 12 minutes ago, last doorbell press ...".
  * Watch (on the hub's loop): a press is said in every room; a person in one room (the routine room, whose pod also plays it upstairs);
    cars only with CAMS_ANNOUNCE_CARS=on. Each once per window per camera, and each also DMed with the camera's snapshot through the portal.
Only the hub (CAMS_OWNER=1, set by hub.py) listens and announces; everything else that builds the house (the house MCP) reads Frigate on demand.
Nothing here can change a camera.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
import urllib.request

from homekit import Loop
from house import House, HomeError

POLL_S = 2.0                            # Frigate's events: how often the hub asks
STATE_S = 10.0                          # a non-owner's states(): how stale they may be
PERSON_S = 60                           # a camera says "person" this long after one
RINGING_S = 60
ANNOUNCE = {"ding": 30, "person": 300, "car": 600}   # per camera: the same kind is said again only after this many seconds
RETRY_S = (5.0, 300.0)


def spoken_name(name: str) -> str:
    return str(name or "camera").replace("_", " ").strip()


def spoken(event: dict) -> str:
    where = spoken_name(event.get("camera", ""))
    if event["kind"] == "ding":
        return f"Someone's at the {where}."
    if event["kind"] == "car":
        return f"A car at the {where}."
    return f"Someone's outside, at the {where}."


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


def doorbells(spec: str) -> dict[str, str]:
    """REOLINK_DOORBELL "front_door=192.168.1.60[,side_door=...]" -> {camera: host}."""
    out = {}
    for part in str(spec or "").split(","):
        if "=" in part:
            cam, host = (x.strip() for x in part.split("=", 1))
            if cam and host:
                out[cam] = host
    return out


class Frigate:
    """Frigate's own API (no login: it is tailnet-only). `get(path) -> bytes`; tests pass a fake."""

    def __init__(self, url: str, get=None, timeout: float = 8.0):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.get = get or self._get

    def _get(self, path: str) -> bytes:
        with urllib.request.urlopen(f"{self.url}{path}", timeout=self.timeout) as r:   # noqa: S310 — the tailnet's Frigate
            return r.read()

    def stats(self) -> dict:
        return json.loads(self.get("/api/stats"))

    def events(self, after: float, limit: int = 50) -> list[dict]:
        q = urllib.parse.urlencode({"after": int(after), "limit": limit, "labels": "person,car", "include_thumbnails": 0})
        return json.loads(self.get(f"/api/events?{q}")) or []

    def snapshot(self, event_id: str | None = None, camera: str = "") -> bytes | None:
        try:
            if event_id:
                return self.get(f"/api/events/{urllib.parse.quote(event_id)}/snapshot.jpg?bbox=0")
            return self.get(f"/api/{urllib.parse.quote(camera)}/latest.jpg?height=720")
        except Exception:                                   # noqa: BLE001 — a DM without a picture is still a DM
            return None


class Cams(House):
    def __init__(self, frigate: Frigate, bells: dict[str, str] | None = None, *, user: str = "admin", password: str = "",
                 owner: bool = False, sensitive: bool = False, log=print, clock=time.time, start: bool = True, make_host=None):
        self.frigate, self.bells, self.user, self.password = frigate, dict(bells or {}), user, password
        self.owner, self.sensitive, self.log, self.clock = owner, sensitive, log, clock
        self.make_host = make_host or self._make_host
        self.cams: dict[str, dict] = {}                    # camera -> {"online", "last_person", "last_car", "last_ding"}
        self.events: list[dict] = []                       # {"kind", "camera", "id", "at"}: Watch drains it
        self.seen: set[str] = set()                        # Frigate event ids already turned into an event
        self.lock = threading.Lock()
        self.connected, self.why, self.fetched = False, "Frigate hasn't answered yet", 0.0
        self.since = clock() - 3600                        # at start: the last hour's events fill in "last person", never announced
        self._first = True
        self._loop = None
        if owner and start:
            threading.Thread(target=self._poll_forever, daemon=True, name="cams-frigate").start()
            if self.bells:
                self._loop = Loop()
                for cam, host in self.bells.items():
                    asyncio.run_coroutine_threadsafe(self._bell(cam, host), self._loop.loop)

    # ── Frigate ─────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Which cameras send frames, and what Frigate saw since the last look. On the owner, new events become announcements."""
        try:
            stats = self.frigate.stats()
            got = self.frigate.events(self.since)
        except Exception as e:                             # noqa: BLE001 — skippy-core away, Frigate restarting: said on the Status map
            self.connected, self.why = False, f"Frigate on skippy-core isn't answering ({type(e).__name__})"
            return
        now = self.clock()
        with self.lock:
            for name, c in (stats.get("cameras") or {}).items():
                cam = self.cams.setdefault(name, {"last_person": None, "last_car": None, "last_ding": None})
                cam["online"] = bool((c or {}).get("camera_fps"))
                cam["doorbell"] = name in self.bells
            for ev in sorted(got, key=lambda e: e.get("start_time") or 0):
                eid, label, name, at = str(ev.get("id") or ""), ev.get("label"), ev.get("camera"), float(ev.get("start_time") or 0)
                if not eid or label not in ("person", "car") or not name or eid in self.seen:
                    continue
                self.seen.add(eid)
                cam = self.cams.setdefault(name, {"online": True, "last_person": None, "last_car": None, "last_ding": None,
                                                  "doorbell": name in self.bells})
                key = f"last_{label}"
                cam[key] = max(cam.get(key) or 0, at)
                self.since = max(self.since, at)
                if self.owner and not self._first:
                    self.events.append({"kind": label, "camera": name, "id": eid, "at": at})
            if len(self.seen) > 5000:                      # the ids only need to outlive the `after` window
                self.seen = set(list(self.seen)[-1000:])
        self._first = False
        self.connected, self.why, self.fetched = True, "", now

    def _poll_forever(self) -> None:
        while True:
            self.refresh()
            time.sleep(POLL_S)

    # ── the doorbell's button ──────────────────────────────────────────────────
    def _make_host(self, host: str):
        from reolink_aio.api import Host                   # noqa: PLC0415
        return Host(host, self.user, self.password)

    async def _bell(self, cam: str, host: str) -> None:
        """The doorbell's push channel (Baichuan, on the LAN): each press (visitor False -> True) is a ding. Dials again if it drops."""
        delay = RETRY_S[0]
        while True:
            api = None
            try:
                api = self.make_host(host)
                await api.get_host_data()
                was = {"visitor": False}

                def pushed():
                    now_visitor = bool(api.visitor_detected(0))
                    if now_visitor and not was["visitor"]:
                        self.ding(cam)
                    was["visitor"] = now_visitor
                api.baichuan.register_callback(f"skippy-{cam}", pushed, None, 0)
                await api.baichuan.subscribe_events()
                self.log(f"cams: listening to the {spoken_name(cam)} doorbell at {host}")
                delay = RETRY_S[0]
                while True:                                # the library keeps the channel alive; this only checks on it
                    await asyncio.sleep(60)
                    await api.baichuan.check_subscribe_events()
            except asyncio.CancelledError:
                raise
            except Exception as e:                         # noqa: BLE001 — unplugged, wrong password: try again, slower each time
                self.log(f"cams: the {spoken_name(cam)} doorbell at {host}: {type(e).__name__}: {str(e)[:120]}")
            finally:
                if api is not None:
                    try:
                        await api.baichuan.unsubscribe_events()
                        await api.logout()
                    except Exception:                      # noqa: BLE001
                        pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_S[1])

    def ding(self, cam: str) -> None:
        now = self.clock()
        with self.lock:
            c = self.cams.setdefault(cam, {"online": True, "last_person": None, "last_car": None, "doorbell": True})
            c["last_ding"] = now
            self.events.append({"kind": "ding", "camera": cam, "id": None, "at": now})

    def take_events(self) -> list[dict]:
        with self.lock:
            out, self.events = self.events, []
        return out

    # ── the house ───────────────────────────────────────────────────────────────
    def states(self, fresh: bool = False) -> list[dict]:
        if not self.owner and (fresh or self.clock() - self.fetched > STATE_S):
            self.refresh()
        if not self.connected and not self.cams:
            raise HomeError(f"The cameras aren't answering: {self.why}")
        now = self.clock()
        out = []
        with self.lock:
            for name, cam in self.cams.items():
                if not self.connected or not cam.get("online", True):
                    state = "unavailable"
                elif cam.get("last_ding") and now - cam["last_ding"] < RINGING_S:
                    state = "ringing"
                elif cam.get("last_person") and now - cam["last_person"] < PERSON_S:
                    state = "person"
                else:
                    state = "idle"
                out.append({"entity_id": f"camera.{name}", "state": state,
                            "attributes": {"friendly_name": spoken_name(name).capitalize(), "last_person": cam.get("last_person"),
                                           "last_car": cam.get("last_car"), "last_ding": cam.get("last_ding"),
                                           "doorbell": bool(cam.get("doorbell") or name in self.bells)}})
        return out

    def _service(self, domain: str, service: str, data: dict) -> None:
        raise HomeError("The cameras are read only.")


def camera_words(a: dict, now: float | None = None) -> str:
    """", last person 12 minutes ago, last car 2 hours ago, last doorbell press just now" from a camera's attributes (house.state_of)."""
    now = time.time() if now is None else now
    out = ""
    if isinstance(a.get("battery"), (int, float)):
        out += f", battery {int(a['battery'])} percent"
    for key, words in (("last_person", "last person"), ("last_car", "last car")):
        if a.get(key):
            out += f", {words} {ago(now - a[key])}"
    if a.get("last_ding"):
        out += f", last doorbell press {ago(now - a['last_ding'])}"
    elif a.get("doorbell"):
        out += ", no doorbell press lately"
    return out


# ── the hub's side: say it, DM it ───────────────────────────────────────────────
class Watch:
    """On the hub's loop: the cameras' new events, after the per-camera windows; a press in every connected room, the rest in one room;
    each also DMed with a snapshot (on its own thread: Frigate's snapshot is quick, but never on the loop)."""

    def __init__(self, cams: Cams, alert, *, cars: bool = False, log=print, clock=time.time, windows: dict | None = None):
        self.cams, self.alert, self.cars, self.log, self.clock = cams, alert, cars, log, clock
        self.windows = dict(windows or ANNOUNCE)
        self.last: dict[tuple, float] = {}

    def news(self) -> list[dict]:
        out = []
        for ev in self.cams.take_events():
            if ev["kind"] == "car" and not self.cars:
                continue
            key = (ev["kind"], ev["camera"])
            if self.clock() - self.last.get(key, -1e12) < self.windows[ev["kind"]]:
                continue
            self.last[key] = self.clock()
            out.append(ev)
        return out

    def tell_phone(self, ev: dict) -> None:
        def go():
            pic = self.cams.frigate.snapshot(ev.get("id"), ev["camera"])
            self.alert(spoken(ev), pic)
        threading.Thread(target=go, daemon=True, name="cams-dm").start()

    async def run(self, hub, routine_room=lambda: "", every: float = 0.5) -> None:
        while True:
            try:
                for ev in self.news():
                    text = spoken(ev)
                    self.log(f"cams: {text}")
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
                        except Exception as e:             # noqa: BLE001
                            self.log(f"cams: could not announce it ({type(e).__name__}: {e})")
            except Exception as e:                         # noqa: BLE001 — never let the watcher die
                self.log(f"cams watch: {type(e).__name__}: {e}")
            await asyncio.sleep(every)


def portal_alert(portal: str, token: str, log=print, post=None, tries: int = 3, wait_s: float = 5.0):
    """alert(text, jpeg|None): a DM to the Captain through the portal's POST /home/alert (home token), the snapshot as base64. Tried a few
    times; called on the Watch's own thread."""
    import base64                                          # noqa: PLC0415

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
            except Exception as e:                         # noqa: BLE001
                log(f"cams: DM try {i + 1} failed ({type(e).__name__})")
            time.sleep(wait_s)
        log("cams: the DM never went through")
    return alert


def cams_of(house) -> Cams | None:
    if isinstance(house, Cams):
        return house
    return next((p for p in getattr(house, "parts", []) if isinstance(p, Cams)), None)


def from_env(env: dict, sensitive: bool = False) -> Cams | None:
    """homectl.build_house's part: when FRIGATE_URL is set (the tailnet door :7355 on skippy-core)."""
    if not env.get("FRIGATE_URL") or env.get("SKIPPY_CAMS", "1") == "0":
        return None
    return Cams(Frigate(env["FRIGATE_URL"]), doorbells(env.get("REOLINK_DOORBELL", "")), user=env.get("REOLINK_USER") or "admin",
                password=env.get("REOLINK_PASSWORD") or env.get("FRIGATE_REOLINK_PASSWORD", ""), owner=env.get("CAMS_OWNER") == "1",
                sensitive=sensitive)


def build(env: dict, house, log=print) -> Watch | None:
    """The hub's watcher, when the house has the cameras and this is the hub."""
    cams = cams_of(house)
    if cams is None or not cams.owner:
        return None
    cams.log = log
    import music as music_mod                              # noqa: PLC0415
    alert = portal_alert(env.get("PORTAL_URL") or music_mod.PORTAL, env.get("SKIPPY_HOME_TOKEN", ""), log=log) \
        if env.get("SKIPPY_HOME_TOKEN") else (lambda text, image=None: None)
    return Watch(cams, alert, cars=env.get("CAMS_ANNOUNCE_CARS", "off").lower() in ("on", "1", "yes"), log=log)
