"""Home Assistant, the way home-AI projects usually reach it: its REST API with a long-lived access token.

    GET  /api/states                       every entity and its state
    POST /api/services/<domain>/<service>  do something ({"entity_id": ..., ...})
    POST /api/template                     render a Jinja template (used here only to learn which area each entity is in)

Used only when the Captain runs Home Assistant (HASS_URL, HASS_TOKEN in the vault); the guard rails are house.py's, the same for every backend.
"""
from __future__ import annotations

import time

import requests

from house import ALLOWED, MAX_LIST, READ_ONLY, TOOLS, HassError, House, HomeError, run_tool  # noqa: F401  (re-exported: the first backend's names)

MAX_CALL_SECONDS = 8


class Hass(House):
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

    def _service(self, domain: str, service: str, data: dict) -> None:
        self._call(self._post, f"/api/services/{domain}/{service}", json=data)

    def invalidate(self) -> None:
        self._cache = None
