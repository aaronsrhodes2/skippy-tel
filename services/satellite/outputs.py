"""The house stereo's speakers, each with its own switch (the Captain, 2026-11-05: "a single audio stream going out and the devices decide if they are
outputting that stream or not").

Bilby plays ONE stream. Every speaker it could come out of has a switch that is kept on disk (state/radio/outputs.json), so a restart of the radio or the
hub changes nothing, and every stereo is on until he turns it off:

  stereo (hdmi = the Sony, enclave, bt)   on | off
  pod    (the room boxes, "pod:<room>")   auto | on | off      auto: silent, except it covers for its floor's stereo that is on but not there

`effective()` is the set of speakers the stream is sent to; `listening()` the ones that really hear it right now (on AND there). The places ("upstairs",
"downstairs", "everywhere", a room) are only shortcuts that set the switches (`place`). Nothing here plays or stops anything: radio.py reads it every second,
retargets what plays, and holds the stream while nobody listens (HouseRadio.listen_tick).

A missing or unreadable file means every stereo on, never silence. A stereo he switched off is respected: a box never covers for it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATE = Path(__file__).resolve().parents[2] / "state" / "radio" / "outputs.json"
POD = "pod:"
FALLBACK_S = 20.0            # a stereo that is on but not there this long: its floor's box covers (the Enclave drops ~30 s on every Tesla charge command)
CONNECT_S = 10.0             # a box that was just asked to cover has this long to tune in before nobody counts as listening
NAMES = {"enclave": "Enclave", "hdmi": "Sony", "bt": "Bluetooth stereo"}
ORDER = ("hdmi", "enclave", "bt")


def pod_id(room: str) -> str:
    return POD + room


def is_pod(ident: str) -> bool:
    return ident.startswith(POD)


def name_of(ident: str) -> str:
    if is_pod(ident):
        return f"{ident[len(POD):].replace('-', ' ').replace('_', ' ').title()} box"
    return NAMES.get(ident, ident)


def layout_floors(env: dict | None = None) -> tuple[list[str], list[str], dict[str, str]]:
    """(the stereo kinds, the pod rooms, id -> floor) from the house's layout (house_layout.json): the Sony is downstairs, the Enclave upstairs, the living
    room's box upstairs. Without a layout: the router kinds of SKIPPY_PLACES and no pods."""
    import speaker_out                                               # noqa: PLC0415
    kinds: list[str] = []
    pods: list[str] = []
    floors: dict[str, str] = {}
    try:
        import house_layout                                          # noqa: PLC0415
        lay = house_layout.default()
        for room, data in lay.rooms.items():
            floor = lay.floor_of(room)
            for k in data.get("outputs") or []:
                if k not in kinds:
                    kinds.append(k)
                floors.setdefault(k, floor)
            for p in data.get("pods") or []:
                if p not in pods:
                    pods.append(p)
                floors.setdefault(pod_id(p), floor)
    except Exception:                                                # noqa: BLE001 — no layout file: SKIPPY_PLACES below
        pass
    for k in (k for ks in speaker_out.places(env or {}).values() for k in ks):
        if k not in kinds:
            kinds.append(k)
    for spec in (env or {}).get("SATELLITE_OUT", "").split(";"):     # a room whose voice has a pod ("living=pod+enclave") is a pod room too
        if "=" in spec and "pod" in speaker_out.split_kinds(spec.split("=", 1)[1]):
            room = spec.split("=", 1)[0].strip()
            if room and room not in pods:
                pods.append(room)
    kinds.sort(key=lambda k: ORDER.index(k) if k in ORDER else len(ORDER))
    return kinds, pods, floors


class Outputs:
    def __init__(self, stereos, pods=(), floors: dict | None = None, path: Path | None = STATE, clock=time.monotonic, log=print, seed=None):
        self.stereos: list[str] = sorted(dict.fromkeys(stereos), key=lambda k: ORDER.index(k) if k in ORDER else len(ORDER))
        self.floors: dict[str, str] = dict(floors or {})
        self.path = Path(path) if path is not None else None
        self.clock, self.log = clock, log
        self.on: dict[str, bool] = {k: True for k in self.stereos}
        self.pods: dict[str, str] = {p: "auto" for p in pods}
        self.down_since: dict[str, float] = {}                      # stereo kind -> when it was first seen on but not there
        self.joined_at: dict[str, float] = {}                       # pod id -> when it entered the effective set
        self.loaded = self._load()
        if not self.loaded and seed is not None:                    # the first start after the switches arrived: keep the stream where it plays now
            self.seed(seed)

    # ── what it knows ──
    def ids(self) -> list[str]:
        return list(self.stereos) + [pod_id(p) for p in self.pods]

    def _know(self, ident: str) -> None:
        if is_pod(ident):
            self.pods.setdefault(ident[len(POD):], "auto")
        elif ident not in self.on:
            self.stereos.append(ident)
            self.on[ident] = True

    def mode_of(self, ident: str) -> str:
        return self.pods.get(ident[len(POD):], "auto") if is_pod(ident) else ("on" if self.on.get(ident, True) else "off")

    # ── what is changed ──
    def set(self, ident: str, on: bool) -> bool:
        """One speaker's switch (the portal's toggle, "turn the Sony off"). A box set on plays; set off it stays silent even to cover. True when it changed."""
        self._know(ident)
        before = self.mode_of(ident)
        if is_pod(ident):
            self.pods[ident[len(POD):]] = "on" if on else "off"
        else:
            self.on[ident] = bool(on)
        changed = before != self.mode_of(ident)
        if changed:
            self.save()
        return changed

    def place(self, kinds) -> bool:
        """A place in his words ("upstairs", "everywhere", a room) as switches: exactly these speakers on, the other stereos off, the other boxes back to auto."""
        kinds = list(dict.fromkeys(kinds))
        for k in kinds:
            self._know(k)
        before = (dict(self.on), dict(self.pods))
        for k in self.stereos:
            self.on[k] = k in kinds
        for p in self.pods:
            self.pods[p] = "on" if pod_id(p) in kinds else "auto"
        changed = before != (self.on, self.pods)
        if changed:
            self.save()
        return changed

    def seed(self, speakers) -> None:
        """Start from where the stream plays now (the radio's saved speakers) instead of everything on."""
        speakers = set(speakers or [])
        if not speakers:
            return
        for k in self.stereos:
            self.on[k] = k in speakers
        for p in self.pods:
            self.pods[p] = "on" if pod_id(p) in speakers else "auto"
        self.save()

    # ── what comes out of it ──
    def effective(self, available, now: float | None = None) -> set[str]:
        """The speakers the stream is sent to: every stereo that is on (there or not: an Enclave that drops comes back to the same stream), every box that is on,
        and a box on auto whose floor's stereos are all on but not there for FALLBACK_S. `available(kind)`: the stereo is really there."""
        now = self.clock() if now is None else now
        for k in self.stereos:                                      # when each stereo that is on went missing
            if self.on.get(k) and not available(k):
                self.down_since.setdefault(k, now)
            else:
                self.down_since.pop(k, None)
        out = {k for k in self.stereos if self.on.get(k)}
        for room, mode in self.pods.items():
            ident = pod_id(room)
            if mode == "on" or (mode == "auto" and self._covering(ident, now)):
                out.add(ident)
        for ident in list(self.joined_at):                          # a box that left the set forgets when it joined
            if ident not in out:
                del self.joined_at[ident]
        for ident in out:
            if is_pod(ident):
                self.joined_at.setdefault(ident, now)
        return out

    def _covering(self, ident: str, now: float) -> bool:
        floor = self.floors.get(ident, "")
        mates = [k for k in self.stereos if floor and self.floors.get(k) == floor and self.on.get(k)]
        return bool(mates) and all(k in self.down_since and now - self.down_since[k] >= FALLBACK_S for k in mates)

    def listening(self, available, pod_rooms, now: float | None = None) -> set[str]:
        """The speakers that hear the stream right now: a stereo that is on and there, a box in the effective set that holds the live stream open.
        `pod_rooms`: the rooms of the boxes tuned in ("" for one that did not say which)."""
        eff = self.effective(available, now)
        heard = {k for k in eff if not is_pod(k) and available(k)}
        for k in eff:
            if is_pod(k) and (k[len(POD):] in pod_rooms or "" in pod_rooms):
                heard.add(k)
        return heard

    def pending(self, hold_s: float, now: float | None = None) -> bool:
        """Nobody hears it yet, but someone may in a moment: a stereo that is on and has been missing for less than `hold_s` (it may come back, or a box may cover
        for it), or a box just asked to cover that has not tuned in yet."""
        now = self.clock() if now is None else now
        if any(now - t < hold_s for t in self.down_since.values()):
            return True
        return any(now - t < CONNECT_S for ident, t in self.joined_at.items() if is_pod(ident))

    def snapshot(self, available, pod_rooms, now: float | None = None) -> list[dict]:
        """For /state and Connect: [{id, name, on, open, floor}]. `on` is the stereo's switch, and for a box whether it plays now (it covers, or it is on)."""
        eff = self.effective(available, now)
        heard = self.listening(available, pod_rooms, now)
        out = []
        for ident in self.ids():
            pod = is_pod(ident)
            out.append({"id": ident, "name": name_of(ident), "on": (ident in eff) if pod else bool(self.on.get(ident)),
                        "open": (ident in heard) if pod else bool(available(ident)), "floor": self.floors.get(ident, ""),
                        **({"mode": self.pods[ident[len(POD):]]} if pod else {})})
        return out

    # ── on disk ──
    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "stereo": self.on, "pods": self.pods}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            self.log(f"(radio: could not save the speakers' switches: {e})")

    def _load(self) -> bool:
        """True when a good file was read. A torn or alien file is ignored: every stereo on, never silence."""
        if self.path is None:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError:
            return False
        except ValueError:
            self.log("(radio: the speakers' switches file is unreadable: every stereo is on)")
            return False
        if not isinstance(data, dict):
            return False
        for k, v in (data.get("stereo") or {}).items():
            if isinstance(k, str) and isinstance(v, bool):
                if k not in self.on:
                    self.stereos.append(k)
                self.on[k] = v
        for room, mode in (data.get("pods") or {}).items():
            if isinstance(room, str) and mode in ("auto", "on", "off"):
                self.pods[room] = mode
        return True
