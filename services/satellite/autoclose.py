"""The garage door closes itself when the Captain drives away and leaves it open (2026-11-01: "I have left my garage door open a few times,
drove away and didn't notice ... when we leave in the car we should automatically close the garage door").

    the car (skippy-core's car reader, core/car: TeslaMate's latest values)  ->  driving, and more than GARAGE_AUTOCLOSE_NEAR_M from home
    (or out of the home geofence) for GARAGE_AUTOCLOSE_GRACE_S  ->  the door open?  ->  garage.Garage.close_on_departure()

The one exception to the spoken yes for the garage door, and a narrow one: this module only ever calls close_on_departure(), which can only
close. Arriving home opens nothing. The opener's own safety eyes still reverse the door; on top of that:
  * an obstruction (before or while closing), or the door not closed within GARAGE_AUTOCLOSE_WAIT_S: no retry, and it is an ALERT
    (the Captain: "it should alert me if it was unable to close the door"): a Discord DM at once, every time, naming the reason; Skippy says it
    in the house (the routine room's pod, and so the Enclave upstairs: SATELLITE_ANNOUNCE_OUT); one more DM after GARAGE_AUTOCLOSE_REPEAT_S if
    the door is still open;
  * closed: one DM ("Closed the garage door after you left") and a line in the next homecoming routine.
It fires once per departure: the car has to be seen at home again before the next one. A hub that starts while the car is away waits for
it to come home first (it never guesses that he just left).

Settings (the vault): GARAGE_AUTOCLOSE (on unless 0; needs GARAGE_ESPHOME), SKIPPY_HOME_LATLON (else where the car sleeps, daily.py),
GARAGE_HOME_GEOFENCE (TeslaMate's name for home, if he made one), GARAGE_AUTOCLOSE_NEAR_M / _GRACE_S / _WAIT_S / _REPEAT_S.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request

from house import HomeError

NEAR_M, GRACE_S, WAIT_S, REPEAT_S, POLL_S = 150.0, 20.0, 60.0, 600.0, 10.0
DRIVING_SHIFTS = ("D", "R")
CLOSED = "Closed the garage door after you left."
OBSTRUCTED = "Couldn't close the garage door after you left: its safety sensor says something is in the way. It's still open."
TIMED_OUT = "Couldn't close the garage door after you left: it still wasn't closed after {s} seconds. It may be open."
OFFLINE = "You drove off, but I couldn't check the garage door: the opener isn't answering. It may be open."
STILL_OPEN = "Reminder: the garage door is still open. I tried to close it after you left and couldn't."


def driving(car: dict | None) -> bool:
    if not car:
        return False
    return str(car.get("state") or "").lower() == "driving" or str(car.get("shift_state") or "").upper() in DRIVING_SHIFTS


class AutoClose:
    """tick() every POLL_S (run() does, on the hub's loop). garage: door() and close_on_departure() only. car() -> the car reader's car or
    None; home() -> (lat, lon) or None; alert(text): a Discord DM; announce(text): Skippy says it in the house."""

    def __init__(self, garage, car, home, alert, announce, *, near_m: float = NEAR_M, grace_s: float = GRACE_S, wait_s: float = WAIT_S,
                 repeat_s: float = REPEAT_S, home_geofence: str = "", clock=time.monotonic, wall=time.time, log=print):
        self.garage, self.car, self.home, self.alert, self.announce = garage, car, home, alert, announce
        self.near_m, self.grace_s, self.wait_s, self.repeat_s = near_m, grace_s, wait_s, repeat_s
        self.home_geofence, self.clock, self.wall, self.log = home_geofence.strip().lower(), clock, wall, log
        self.armed = False                 # the car has been seen at home since the last departure
        self.away_since: float | None = None
        self.phase = ""                    # "" | "closing" | "failed"
        self.started = 0.0
        self.repeat_at: float | None = None
        self.pending_news: str | None = None

    # ── where the car is ──
    def at_home(self, car: dict | None) -> bool | None:
        """True at home, False away, None when the car says nothing about where it is."""
        if not car:
            return None
        fence = str(car.get("geofence") or "").strip().lower()
        if self.home_geofence and fence == self.home_geofence:
            return True
        home = self.home()
        if home is not None and car.get("latitude") is not None and car.get("longitude") is not None:
            from daily import km_between                       # noqa: PLC0415 — a threshold, never said aloud
            return km_between(home, (float(car["latitude"]), float(car["longitude"]))) * 1000 <= self.near_m
        if self.home_geofence:
            return False if fence != self.home_geofence else True
        return None

    # ── the clock ──
    def tick(self) -> None:
        now = self.clock()
        try:
            car = self.car()
        except Exception:                                      # noqa: BLE001 — the car reader is down: nothing to decide this time
            car = None
        where = self.at_home(car)
        if where:
            if not self.armed:
                self.log("garage auto-close: the car is home; armed for the next departure")
            self.armed, self.away_since = True, None
            if self.phase == "failed":                         # he's home: he can see the door himself, no reminder
                self.phase, self.repeat_at = "", None
        if self.phase == "closing":
            self._watch(now)
        elif self.phase == "failed" and self.repeat_at is not None and now >= self.repeat_at:
            self.phase, self.repeat_at = "", None
            if self.garage.door()["state"] != "closed":
                self._dm(STILL_OPEN)
        if self.armed and where is False and driving(car):
            self.away_since = self.away_since if self.away_since is not None else now
            if now - self.away_since >= self.grace_s:
                self.armed, self.away_since = False, None
                self._departed(now)
        elif where is not True:
            self.away_since = None

    def _departed(self, now: float) -> None:
        door = self.garage.door()
        self.log(f"garage auto-close: the car has left; the door is {door['state']}")
        if door["state"] == "closed":
            return
        if door["state"] in ("unavailable", "unknown"):
            return self._fail(now, OFFLINE)
        if door["obstructed"]:
            return self._fail(now, OBSTRUCTED)
        try:
            did = self.garage.close_on_departure()
        except HomeError as e:
            return self._fail(now, OBSTRUCTED if self.garage.door()["obstructed"] else f"Couldn't close the garage door after you left: {e}")
        if did == "already closed":
            return
        self.phase, self.started = "closing", now

    def _watch(self, now: float) -> None:
        door = self.garage.door()
        if door["state"] == "closed":
            self.phase = ""
            self.pending_news = f"By the way, I closed the garage door after you left, at {time.strftime('%H:%M', time.localtime(self.wall()))}."
            self._dm(CLOSED)
        elif door["obstructed"]:
            self._fail(now, OBSTRUCTED)                        # the eyes reversed it: no second try
        elif now - self.started > self.wait_s:
            self._fail(now, TIMED_OUT.format(s=int(self.wait_s)))

    def _fail(self, now: float, text: str) -> None:
        self.phase, self.repeat_at = "failed", now + self.repeat_s
        self.pending_news = text
        self._dm(text)
        try:
            self.announce(text)
        except Exception as e:                                 # noqa: BLE001 — the DM already went
            self.log(f"garage auto-close: could not announce it ({type(e).__name__}: {e})")

    def _dm(self, text: str) -> None:
        self.log(f"garage auto-close: {text}")
        try:
            self.alert(text)
        except Exception as e:                                 # noqa: BLE001
            self.log(f"garage auto-close: the DM failed ({type(e).__name__}: {e})")

    def news(self) -> str | None:
        """For the homecoming routine: what happened to the door while he was out, once."""
        text, self.pending_news = self.pending_news, None
        return text

    async def run(self, hub, routine_room=lambda: "") -> None:
        """On the hub's loop: tick every POLL_S; announcements go to one room (the routine room, else the first connected one), whose pod
        also plays them upstairs (announce_outs)."""
        queue: list[str] = []
        self.announce = queue.append
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception as e:                             # noqa: BLE001 — never let the watcher die
                self.log(f"garage auto-close: {type(e).__name__}: {e}")
            while queue:
                text = queue.pop(0)
                rooms = getattr(hub, "rooms", {})
                room = rooms.get(routine_room()) or next(iter(rooms.values()), None)
                if room is None:
                    self.log("garage auto-close: no room connected to say it in")
                    continue
                try:
                    await room.announce(text)
                except Exception as e:                         # noqa: BLE001
                    self.log(f"garage auto-close: could not announce it ({type(e).__name__}: {e})")
            await asyncio.sleep(POLL_S)


# ── the real car, the real DM ──
def car_reader(url: str, timeout: float = 5.0):
    def car() -> dict | None:
        with urllib.request.urlopen(f"{url.rstrip('/')}/car", timeout=timeout) as r:    # noqa: S310 — the tailnet's car reader
            cars = (json.loads(r.read().decode("utf-8")) or {}).get("cars") or []
        return cars[0] if cars and isinstance(cars[0], dict) else None
    return car


def portal_alert(portal: str, token: str, log=print, post=None, tries: int = 3, wait_s: float = 5.0):
    """A DM to the Captain through the portal (POST /home/alert, the home token), on its own thread, tried a few times: an alert must not be lost."""
    def send(text: str) -> bool:
        body = json.dumps({"text": text}).encode()
        req = urllib.request.Request(f"{portal.rstrip('/')}/home/alert", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:     # noqa: S310
            return r.status == 200
    post = post or send

    def alert(text: str) -> None:
        def go():
            for i in range(tries):
                try:
                    if post(text):
                        return
                except Exception as e:                         # noqa: BLE001
                    log(f"garage auto-close: DM try {i + 1} failed ({type(e).__name__})")
                time.sleep(wait_s)
            log("garage auto-close: the DM never went through")
        threading.Thread(target=go, daemon=True, name="garage-alert").start()
    return alert


def garage_of(house):
    """The Garage backend inside the house (alone, or one part of house.Combined), or None."""
    from garage import Garage                                  # noqa: PLC0415
    if isinstance(house, Garage):
        return house
    return next((p for p in getattr(house, "parts", []) if isinstance(p, Garage)), None)


def build(env: dict, house, home, log=print) -> AutoClose | None:
    """The watcher, when there is a ratgdo and GARAGE_AUTOCLOSE isn't 0; else None."""
    g = garage_of(house)
    if g is None or str(env.get("GARAGE_AUTOCLOSE", "1")).lower() in ("0", "off", "no"):
        return None
    import music                                               # noqa: PLC0415
    from daily import CAR_URL                                  # noqa: PLC0415
    f = lambda k, d: float(env.get(k) or d)                   # noqa: E731
    return AutoClose(g, car_reader(env.get("SKIPPY_CAR_URL") or CAR_URL), home,
                     portal_alert(env.get("PORTAL_URL") or music.PORTAL, env.get("SKIPPY_HOME_TOKEN", ""), log=log), lambda text: None,
                     near_m=f("GARAGE_AUTOCLOSE_NEAR_M", NEAR_M), grace_s=f("GARAGE_AUTOCLOSE_GRACE_S", GRACE_S),
                     wait_s=f("GARAGE_AUTOCLOSE_WAIT_S", WAIT_S), repeat_s=f("GARAGE_AUTOCLOSE_REPEAT_S", REPEAT_S),
                     home_geofence=env.get("GARAGE_HOME_GEOFENCE", ""), log=log)
