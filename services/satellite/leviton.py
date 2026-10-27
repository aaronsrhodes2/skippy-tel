"""Leviton, through the My Leviton cloud: every Decora Smart Wi-Fi switch on his account, with nothing paired, unscrewed or scanned.
The same account Google Home and Alexa use (the `decora_wifi` library, the one Home Assistant's Leviton integration uses); LEVITON_EMAIL and
LEVITON_PASSWORD live in the vault, never printed. Commands go through Leviton's servers (the internet, about a second), so a switch paired
locally too (HomeKit, Matter) should win: build_house puts this backend last, and Combined keeps the first backend's name.

What a switch becomes: light.<name> when it dims (canSetLevel: the D2MSD, D26HD), switch.<name> when it doesn't (the D215S). A switch the
cloud says is not connected is left out, as Matter leaves out an unavailable node: the others still answer. The names are his own, from the
app; rooms come from the words in them ("kitchen under 1").
"""
from __future__ import annotations

import time

from homekit import slug
from house import House, HomeError

CACHE_SECONDS = 4.0
RETRY_SECONDS = 0.8                                   # the same second chance HomeKit and Matter give


def _session():
    from decora_wifi import DecoraWiFiSession        # noqa: PLC0415 — only the hub needs it
    return DecoraWiFiSession()


def _switches(session) -> list:
    """Every IoT switch on the account, through every residence it may see."""
    from decora_wifi.models.residence import Residence                       # noqa: PLC0415
    from decora_wifi.models.residential_account import ResidentialAccount    # noqa: PLC0415
    out = []
    for perm in session.user.get_residential_permissions():
        if perm.residentialAccountId is not None:
            for residence in ResidentialAccount(session, perm.residentialAccountId).get_residences():
                out += residence.get_iot_switches()
        elif perm.residenceId is not None:
            out += Residence(session, perm.residenceId).get_iot_switches()
    return out


def _data(sw) -> dict:
    return getattr(sw, "data", None) or getattr(sw, "_data", None) or {}


class Leviton(House):
    def __init__(self, email: str, password: str, session=_session, switches=_switches, clock=time.monotonic, sleep=time.sleep,
                 sensitive: bool = False):
        self._email, self._password = email, password
        self._new_session, self._list = session, switches
        self.clock, self._sleep, self.sensitive = clock, sleep, sensitive
        self._s = None
        self._cache: tuple[float, list[dict]] | None = None
        self._index: dict[str, object] = {}              # entity_id -> the library's switch object
        self.last: dict = {}                              # the latest control: {"entity_id", "attempts", "ms"} (homectl.py prove)

    # ── the account ─────────────────────────────────────────────────────────────
    def _login(self):
        s = self._new_session()
        try:
            ok = s.login(self._email, self._password)
        except Exception as e:                            # noqa: BLE001 — the network, or Leviton's servers
            raise HomeError("Leviton's servers did not answer.") from e
        if not ok:
            raise HomeError("Leviton refused the login in the vault.")
        self._s = s
        return s

    def _fetch(self) -> list:
        """The switches, logging in again once when the session has expired."""
        for attempt in (1, 2):
            s = self._s or self._login()
            try:
                return self._list(s)
            except HomeError:
                raise
            except Exception as e:                        # noqa: BLE001 — an expired session looks like any other failure
                self._s = None
                if attempt == 2:
                    raise HomeError("Leviton's servers did not answer.") from e
        return []

    # ── the House ───────────────────────────────────────────────────────────────
    def invalidate(self) -> None:
        self._cache = None

    def states(self, fresh: bool = False) -> list[dict]:
        if not fresh and self._cache and self.clock() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        try:
            switches = self._fetch()
        except HomeError:
            if self._cache:
                return self._cache[1]
            raise
        out, index = [], {}
        for sw in switches:
            d = _data(sw)
            if not d.get("name") or d.get("connected") is False:
                continue
            kind = "light" if d.get("canSetLevel") else "switch"
            eid = f"{kind}.{slug(d['name'])}"
            if eid in index:
                continue                                  # two switches with one name: the first keeps it
            row = {"entity_id": eid, "state": "on" if str(d.get("power")).upper() == "ON" else "off",
                   "attributes": {"friendly_name": d["name"], "model": d.get("model")}}
            if kind == "light" and isinstance(d.get("brightness"), (int, float)):
                row["attributes"]["brightness"] = round(int(d["brightness"]) * 255 / 100)
            out.append(row)
            index[eid] = sw
        self._index, self._cache = index, (self.clock(), out)
        return out

    def _service(self, domain: str, service: str, data: dict) -> None:
        eid = data["entity_id"]
        if eid not in self._index:
            self.states(fresh=True)
        sw = self._index.get(eid)
        if sw is None:
            raise HomeError("I do not know that one any more.")
        if service == "toggle":
            want = {"power": "OFF" if self._read(sw).get("power") == "ON" else "ON"}
        elif service == "turn_off":
            want = {"power": "OFF"}
        elif service == "turn_on" and data.get("brightness_pct") is not None:
            want = {"power": "ON", "brightness": max(1, min(100, int(data["brightness_pct"])))}
        elif service == "turn_on":
            want = {"power": "ON"}
        else:
            raise HomeError("I cannot do that to it.")
        started = self.clock()
        for attempt in (1, 2):                            # said, then read back from Leviton; a second try if it didn't change
            try:
                sw.update_attributes(want)
                got = self._read(sw)
                if all(str(got.get(k)).upper() == str(v).upper() for k, v in want.items()):
                    self.last = {"entity_id": eid, "attempts": attempt, "ms": round((self.clock() - started) * 1000)}
                    return
            except HomeError:
                raise
            except Exception as e:                        # noqa: BLE001 — an expired session: each switch carries it, so fetch them anew
                if attempt == 2:
                    raise HomeError("Leviton's servers did not answer.") from e
                self._s = None
                try:
                    self.states(fresh=True)
                except HomeError:
                    pass
                sw = self._index.get(eid, sw)
            self._sleep(RETRY_SECONDS)
        raise HomeError("I told it twice and it did not change. It may be off the Wi-Fi.")

    def _read(self, sw) -> dict:
        sw.refresh()
        return _data(sw)
