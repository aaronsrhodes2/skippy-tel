"""The portal's client for Mentat (mentat/app.py): sigma-ground's physics tools
over HTTP, for the public Experiments playground. (Skippy's chat asks the
Mentat + Qwen copy on skippy-pc instead, through home.py.)

Mentat's tool list is fetched once and cached; everything degrades to "no
physics tools" if Mentat is unreachable, never to a broken page or chat.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

CACHE_SECONDS = 600
TIMEOUT = 25


class MentatError(Exception):
    """`status` is Mentat's HTTP status when it answered, None when it couldn't be reached."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def offline(self) -> bool:
        """Nothing was computed because Mentat is down, too old, or broke (not because the request was bad)."""
        return self.status is None or self.status == 404 or self.status >= 500


class Mentat:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._tools: tuple[float, list[dict]] | None = None
        self._wva: tuple[float, dict] | None = None

    def _request(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            try:
                body = json.loads(err.read() or b"{}")
            except ValueError:
                body = {}
            raise MentatError(body.get("error") or f"Mentat answered {err.code}", err.code) from err
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            raise MentatError(f"Mentat is unreachable ({err})") from err

    def tools(self) -> list[dict]:
        if self._tools and time.monotonic() - self._tools[0] < CACHE_SECONDS:
            return self._tools[1]
        tools = self._request("GET", "/tools")
        self._tools = (time.monotonic(), tools)
        return tools

    def call(self, name: str, args: dict) -> dict:
        body = self._request("POST", f"/tools/{name}", args or {})
        if not body.get("ok"):
            raise MentatError(body.get("error", "Mentat call failed"))
        return body["result"]

    # Weapon vs. Armor (mentat/wva.py): sigma-ground's own game solver, on Mentat.
    def wva_catalog(self) -> dict:
        if self._wva and time.monotonic() - self._wva[0] < CACHE_SECONDS:
            return self._wva[1]
        body = self._request("GET", "/wva/catalog")
        if not body.get("ok"):
            raise MentatError(body.get("error", "Mentat sent no Weapon vs. Armor catalog"), 502)
        self._wva = (time.monotonic(), body["result"])
        return body["result"]

    def wva_solve(self, fight: dict) -> dict:
        body = self._request("POST", "/wva/solve", fight)
        if not body.get("ok"):
            raise MentatError(body.get("error", "Mentat didn't run the fight"), 502)
        return body["result"]

    def search(self, query: str, limit: int = 12) -> list[dict]:
        words = [w for w in query.lower().split() if w]
        scored = []
        for t in self.tools():
            hay = f"{t['name']} {t['domain']} {t['description']}".lower()
            score = sum(hay.count(w) + (3 if w in t["name"] else 0) for w in words)
            if score:
                scored.append((score, t))
        scored.sort(key=lambda st: -st[0])
        return [{"name": t["name"], "domain": t["domain"], "description": t["description"][:300],
                 "input_schema": t["input_schema"]} for _, t in scored[:limit]]
