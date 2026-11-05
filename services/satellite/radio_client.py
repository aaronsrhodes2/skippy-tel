"""The hub's remote for the house radio (radio.py): the hubs change the station or add to it, they never play it.

* RadioClient: the station's state (a poller thread keeps a copy, so `playing` never waits on the network in the hub's loop), its commands
  (the music tools, the routines' shuffle and stop), and ducking a room's speakers while Skippy speaks there.
* RoomRadio: one room's view of it (room.stereo): duck() lowers that room's speakers only.
* PodTuner: a pod's own speaker as one of the station's speakers. When the station plays on "pod:<room>" the pod's media player is handed the
  radio's live stream (it comes from the radio, not the hub, so a hub restart never touches it); a hub that comes back finds the pod already
  playing and leaves it alone. To Bilby Connect the pod is a remote: "Play on" it puts the station in that room.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request

URL = "http://127.0.0.1:47850"
POLL_S = 1.0
DOWN = "The house radio is not answering right now."


class RadioClient:
    def __init__(self, url: str = URL, log=print, poll_s: float = POLL_S, request=None, start: bool = True):
        self.url, self.log, self.poll_s = url.rstrip("/"), log, poll_s
        self.request = request or self._request
        self.last: dict = {}
        self.ok = False
        self._warned = False
        self.pauses = 0                                                   # how many stops came from him through the music tool: a routine that began before one leaves its music off (daily._music_on)
        self.box = _RemoteBox(self)
        if start:
            threading.Thread(target=self._poll, daemon=True, name="radio-poll").start()

    @classmethod
    def from_env(cls, env: dict, log=print) -> "RadioClient":
        return cls(env.get("RADIO_URL") or f"http://127.0.0.1:{env.get('RADIO_PORT', '47850')}", log=log)

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:       # noqa: S310 — 127.0.0.1
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return json.loads(e.read() or b"{}")

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict | None:
        try:
            got = self.request(method, path, body, timeout)
        except Exception as e:                                            # noqa: BLE001 — the radio is restarting or down
            if not self._warned:
                self.log(f"(radio: not answering: {type(e).__name__}: {e})")
                self._warned = True
            self.ok = False
            return None
        self._warned, self.ok = False, True
        if isinstance(got, dict) and isinstance(got.get("state"), dict):
            self.last = got["state"]
        return got

    def _poll(self) -> None:
        while True:
            got = self.call("GET", "/state", timeout=2.0)
            if got is not None:
                self.last = got
            time.sleep(self.poll_s)

    # ── what the hub reads ──
    def state(self) -> dict:
        return dict(self.last)

    @property
    def playing(self) -> bool:
        return bool(self.ok and self.last.get("playing"))

    # ── what the hub asks ──
    def tool(self, name: str, args: dict, room: str = "") -> str:
        """One music tool, run by the radio (station.run). Blocking: the brain calls it from its worker thread."""
        if name == "music" and str((args or {}).get("action") or "") in ("pause", "stop"):
            self.pauses += 1                                              # his stop, counted even when the radio is away or already paused: it is what he asked for
        got = self.call("POST", "/tool", {"name": name, "args": args or {}, "room": room}, timeout=40.0)
        return (got or {}).get("said") or DOWN

    async def control(self, action: str) -> bool:
        got = await asyncio.to_thread(self.call, "POST", "/control", {"action": action})
        return bool(got and got.get("ok"))

    async def stop(self) -> None:
        await self.control("pause")

    async def shuffle(self, limit: int = 500) -> int:
        got = await asyncio.to_thread(self.call, "POST", "/shuffle", {}, 40.0)
        return int((got or {}).get("queued") or 0)

    async def take(self, state: dict, room: str) -> None:
        await asyncio.to_thread(self.call, "POST", "/take", {"state": state, "room": room})

    def duck(self, kinds, on: bool, hold_s: float = 30.0) -> None:
        """Never waits: a thread posts it (Skippy's voice must not wait on the radio)."""
        body = {"kinds": sorted(kinds), "on": bool(on), "hold_s": float(hold_s)}
        threading.Thread(target=self.call, args=("POST", "/duck", body, 2.0), daemon=True, name="radio-duck").start()

    def for_room(self, kinds) -> "RoomRadio":
        return RoomRadio(self, kinds)


class LocalRadio:
    """A station in this very process (station.run on `loop`): what the tests drive, with the same calls as RadioClient."""

    def __init__(self, house, loop, favorites=None):
        self.house, self.loop, self.favorites = house, loop, favorites
        self.pauses = 0

    @property
    def playing(self) -> bool:
        return bool(self.house.playing)

    def tool(self, name: str, args: dict, room: str = "") -> str:
        import station                                                    # noqa: PLC0415
        if name == "music" and str((args or {}).get("action") or "") in ("pause", "stop"):
            self.pauses += 1
        return asyncio.run_coroutine_threadsafe(station.run(self.house, name, args, room, self.favorites), self.loop).result(40)


class _RemoteBox:
    """The few BoxMusic calls the routines make on the house stereo (daily.py): next, resume, and whether a queue is there to resume."""

    def __init__(self, radio: RadioClient):
        self.radio = radio

    @property
    def queue(self) -> list:
        return [None] * int(self.radio.last.get("queue_len") or 0)

    async def next(self) -> None:
        await self.radio.control("next")

    async def resume(self) -> None:
        await self.radio.control("resume")

    async def pause(self) -> None:
        await self.radio.control("pause")


class RoomRadio:
    """room.stereo: the station as one room sees it. duck(True, s) lowers this room's speakers for `s` seconds (hub.Room's hooks)."""

    def __init__(self, radio: RadioClient, kinds):
        self.radio, self.kinds = radio, sorted(set(kinds))

    @property
    def playing(self) -> bool:
        return self.radio.playing

    def duck(self, on: bool, hold_s: float = 30.0) -> None:
        if self.radio.playing:                                            # nothing plays: no call at all
            self.radio.duck(self.kinds, on, hold_s)


def room_kinds(env: dict, room: str) -> list[str]:
    """The speakers Skippy uses in a room, as the radio names them: its router outputs, and its own pod as "pod:<room>"."""
    import speaker_out                                                    # noqa: PLC0415
    return [f"pod:{room}" if k == "pod" else k for k in speaker_out.room_kinds(env, room)]


class PodTuner:
    """A pod's speaker tuned to the station, and the pod as a Bilby Connect remote (Connect calls state() and command())."""

    def __init__(self, link, radio: RadioClient, room: str, towards: str = "", log=print, every: float = 2.0, clock=time.monotonic):
        self.link, self.radio, self.room, self.towards, self.log, self.every, self.clock = link, radio, room, towards, log, every, clock
        self.on: bool | None = None                                       # None: not known yet (a hub that just came back)
        self.started_at = clock()
        self._queue_sent = False

    @property
    def kind(self) -> str:
        return f"pod:{self.room}"

    @property
    def playing(self) -> bool:
        """The station plays (anywhere): the hub's starters never talk over music (hub.Room)."""
        return self.radio.playing

    def wanted(self) -> bool:
        st = self.radio.state()
        return bool(self.radio.ok and st.get("playing") and self.kind in (st.get("speakers") or []))

    def live_url(self) -> str:
        from media import local_address                                   # noqa: PLC0415
        live = self.radio.state().get("live") or {}
        if not live.get("path"):
            return ""
        return f"http://{local_address(self.towards or '8.8.8.8')}:{live.get('port')}{live['path']}"

    async def tick(self) -> None:
        want = self.wanted()
        if self.on is None:                                               # just connected: is the pod already playing the station?
            playing = getattr(self.link, "player_playing", None)
            if playing is None and self.clock() - self.started_at < 5.0:
                return                                                    # its first state report is still on the way
            self.on = bool(playing) and want
            if self.on:
                self.log(f"[{self.room}] the pod is still playing the house radio: left alone")
        if want and not self.on:
            url = self.live_url()
            if url:
                await self.link.play(url)
                self.on = True
                self.log(f"[{self.room}] the pod tuned in to the house radio")
        elif not want and self.on:
            await self.link.stop()
            self.on = False
            self.log(f"[{self.room}] the pod tuned out of the house radio")

    async def on_idle(self) -> None:
        """The pod's player stopped (the stream dropped: the radio restarted): tuned in again at the next tick while it is wanted."""
        if self.on:
            self.on = False

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:                                        # noqa: BLE001 — keep tuning
                self.log(f"[{self.room}] (radio tuner: {type(e).__name__}: {e})")
            await asyncio.sleep(self.every)

    # ── Bilby Connect: the pod as a remote ──
    def state(self) -> dict:
        st = self.radio.state()
        here = self.kind in (st.get("speakers") or []) and bool(st.get("playing"))
        out = {"track": st.get("track", "") if here else "", "title": st.get("title", "") if here else "", "artist": st.get("artist", "") if here else "",
               "pos": st.get("pos", 0.0) if here else 0.0, "playing": here, "idx": 0, "armed": True, "tap": False}
        if not self._queue_sent:
            out["queue"], self._queue_sent = [], True
        return out

    async def command(self, action: str, devices: list[dict]) -> None:
        if action.startswith("take:"):                                    # "Play on" this pod: the station, on this room's speakers
            src = next((d for d in devices if d.get("id") == action[5:]), None)
            if src and src.get("state"):
                await self.radio.take(src["state"], self.room)
            return
        await self.radio.control({"play": "resume", "prev": "previous"}.get(action, action))
