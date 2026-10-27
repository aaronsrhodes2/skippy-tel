"""The portal's client for Mentat (mentat/app.py): sigma-ground's physics tools
over HTTP. There is ONE Mentat, on skippy-core (Qwen + the router), and MENTAT_URL is its
Tailscale Funnel: the Experiments playground, Weapon vs. Armor and Skippy's chat
(`ask_mentat`) all come straight here, no hop through skippy-pc.

Mentat's tool list is fetched once and cached; everything degrades to "no
physics tools" if Mentat is unreachable, never to a broken page or chat.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

CACHE_SECONDS = 600
TIMEOUT = 100                # a tool call: over Mentat's own per-call limit (MENTAT_CALL_TIMEOUT 90 s, core/compose.core.yml)
# A question: the same limit plus Qwen reading it first (the router, the card busy with another chat).
ASK_TIMEOUT = 110
READY_SECONDS = 30           # how long a /health answer counts (the chat asks before every turn)


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
        self._ready: tuple[float, bool] | None = None

    def _request(self, method: str, path: str, payload=None, timeout: float = TIMEOUT, headers: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            try:
                body = json.loads(err.read() or b"{}")
            except ValueError:
                body = {}
            raise MentatError(body.get("error") or f"Mentat answered {err.code}", err.code) from err
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            raise MentatError(f"Mentat is unreachable ({err})") from err

    def ready(self) -> bool:
        """Mentat answers /health ready AND thinks with Qwen (mode "llm"): a keywords-only Mentat is not offered to the chat
        (the Captain wants a clear "offline" over a degraded answer). Cached READY_SECONDS, a short timeout: never slows a turn much."""
        if self._ready and time.monotonic() - self._ready[0] < READY_SECONDS:
            return self._ready[1]
        try:
            h = self._request("GET", "/health", timeout=5)
            ok = bool(h.get("ready")) and h.get("mode") == "llm"
        except MentatError:
            ok = False
        self._ready = (time.monotonic(), ok)
        return ok

    def ask_mentat(self, question: str, given: dict | None = None, trace: str = "", timeout: float = ASK_TIMEOUT) -> dict:
        """Skippy's chat: a question in words, Mentat's answer (its own JSON). `given`: values the person agreed to after Mentat
        suggested them; `trace`: this chat turn's id, kept in Mentat's research record. TimeoutError / RuntimeError when it can't."""
        body = {"question": question, **({"given": given} if given else {})}
        try:
            reply = self._request("POST", "/ask", body, timeout=timeout,
                                  headers={"X-Skippy-Trace": str(trace)[:200], "X-Skippy-Caller": "portal-chat"})
        except MentatError as exc:
            if exc.status is None and "timed out" in str(exc).lower():
                raise TimeoutError("Mentat didn't answer in time") from exc
            raise RuntimeError(str(exc)) from exc
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error") or "Mentat failed")
        return reply["result"]

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
