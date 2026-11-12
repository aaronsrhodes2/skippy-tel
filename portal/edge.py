"""The direct front door, the portal's half (the Captain, 2026-11-05: "Funnels and bridges seem like common failure points for us. Can we give
direct HTTP to all of my devices?").

Every device that is not on the tailnet (the Tesla on cellular, a friend's browser, his phone with Tailscale off) reaches the media gates on
skippy-pc through a Tailscale Funnel relay, and the relay stalls on a poor link. The direct door is a Caddy reverse proxy on skippy-pc that the
home router forwards to (external TCP 443 -> skippy-pc :47443 -> the edge container's :443), reached straight on home's public IP at
https://home.makerportal.live. The Funnel stays, as the automatic fallback. What the four pieces of that work agree on:

  * EDGE_HOST (a setting, from the environment; empty means the door is off everywhere) names the door. On it, with the prefix stripped before the
    gate sees the request: /music is music-gate, /video video-gate, /books audiobook-gate, /voice voice-gate, /in popup-gate (PREFIXES below);
    /ping answers 200 "ok" from Caddy itself; everything else is 404. The gates already send CORS "*" because the signed pass is the credential.
  * Every gate-pass JSON the portal hands to a browser gains one field, "direct": "https://<EDGE_HOST>/<prefix>", or null when the door is off or
    not reachable from outside right now. "url" stays the Funnel address exactly as before, behind the same strict *.ts.net checks.
  * static/direct.js (the page's half) uses "direct" only after GET <direct>/health answers, and falls back to the Funnel on any problem.

This file is what decides "right now". The portal runs on Cloud Run, outside the house, so its probe of https://<EDGE_HOST>/ping sees what a
friend's browser sees: the DNS name, the router's forward, the certificate and the proxy, all at once. A page that is told a dead address pays a
timeout before it falls back, so the portal says null when it knows the door is shut.

Three rules keep it from ever costing a request anything:
  * alive() NEVER waits for the network. It answers with the last probe at once; when that probe is older than its TTL it starts ONE background
    refresh (a daemon thread) and the answer is the new one next time (stale-while-revalidate). Before the first probe has finished it says False.
  * The address is built here from EDGE_HOST alone. A gate's report (skippy-pc's word) never supplies it, and a host that is not a plain DNS name
    under makerportal.live or makerportal.studio switches the door off for good: it would be the one place a credential could be handed to a
    stranger's server.
  * Only a 200 whose body is "ok" counts as a live door, over verified TLS, with no redirect followed: a parked page, a captive portal or an ISP's
    error page is not the door.

A door that was never up says nothing (it is optional); the doctor (doctor.py) tells the Captain once when one that WAS up has stayed down.
"""
from __future__ import annotations

import inspect
import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

# The edge's path layout (Caddy handle_path, the prefix stripped before the gate). A new gate behind the door is a new entry here and in the Caddyfile.
PREFIXES = ("music", "video", "books", "voice", "in")
DOMAINS = (".makerportal.live", ".makerportal.studio")      # the Captain's own: a door anywhere else is never signed for
PING = "/ping"
TIMEOUT_S = 2.0                # one probe; a door slower than this is no better than the Funnel
TTL_OK_S = 60                  # a live door is looked at again after this...
TTL_BAD_S = 30                 # ...a dead one sooner, so it comes back into use quickly
HUNG_S = 15                    # a refresh that has not answered by now is given up on (a stuck socket must not freeze the door for good)

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_NAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")


def clean_host(value) -> str:
    """The door's host name, lowercased, or "" when it is not a plain DNS name ending in .makerportal.live or .makerportal.studio: a scheme,
    a port, a path, user info, a query, an underscore, a space, a trailing dot, a name that is not ASCII, the bare domain itself (that is the
    portal) and every other domain are all refused. Only whitespace around the name is forgiven (an environment variable's stray newline)."""
    if not isinstance(value, str):
        return ""
    host = value.strip().lower()
    if not host or len(host) > 253 or not host.isascii() or not host.endswith(DOMAINS):
        return ""
    return host if _NAME.fullmatch(host) else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect from the door is an answer that is not "ok": it is never followed (to wherever it points)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# No environment proxy either: the probe is the browser's own straight road to the door.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _why(exc: BaseException) -> str:
    """One short plain phrase for a failed probe (the doctor and Admin show it)."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc          # a URLError wraps the real cause
    if isinstance(reason, str):
        return reason[:80]
    if isinstance(reason, TimeoutError):                  # (socket.timeout is TimeoutError)
        return "no answer in time"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "its certificate is not accepted"
    if isinstance(reason, ssl.SSLError):
        return "the secure connection failed"
    if isinstance(reason, socket.gaierror):
        return "the name does not resolve"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, ConnectionError):
        return "the connection dropped"
    if isinstance(reason, OSError):
        return ("network error: " + (reason.strerror or type(reason).__name__))[:80]
    return type(reason).__name__


def http_probe(url: str, timeout: float = TIMEOUT_S) -> tuple[bool, str]:
    """GET `url`: (True, "") only for a 200 whose body is "ok"; otherwise (False, why). Never raises."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "skippy-portal/edge-probe", "Accept": "text/plain", "Cache-Control": "no-cache"})
        with _OPENER.open(request, timeout=timeout) as reply:
            body = reply.read(16)                         # "ok" is two bytes; anything longer is not the door's answer
            if reply.status == 200 and body.strip() == b"ok":
                return True, ""
            return False, f"HTTP {reply.status}, and not the \"ok\" the door answers"
    except urllib.error.HTTPError as exc:
        exc.close()
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 — every way of not answering is a dead door, said in a few words
        return False, _why(exc)


def _ask(probe, url: str):
    """Call a probe with what it takes: (url, timeout), (url) or nothing (a test hands in the smallest callable that does the job)."""
    try:
        params = list(inspect.signature(probe).parameters.values())
    except (TypeError, ValueError):                       # (a callable that will not say)
        return probe(url, TIMEOUT_S)
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return probe(url, TIMEOUT_S)
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return probe(*(url, TIMEOUT_S)[:len(positional)])


def _print(line: str) -> None:
    print(line, flush=True)


class Edge:
    """The direct door as the portal sees it. One per process (create_app builds it from EDGE_HOST and hands it to the gates and the doctor).

    probe(url, timeout) -> (ok, why) is injectable (a bare bool works too, and so does a probe that takes only the url, or nothing); so are the
    clock and the TTLs. All state sits behind one lock; the probe itself runs outside it, on a daemon thread, one at a time."""

    def __init__(self, host, probe=None, clock=time.time, ttl_ok=TTL_OK_S, ttl_bad=TTL_BAD_S, log=None, hung_s=HUNG_S):
        self.host = clean_host(host)
        configured = isinstance(host, str) and bool(host.strip())
        self.why_off = "" if self.host else ("EDGE_HOST is not a plain name under makerportal.live or makerportal.studio" if configured
                                             else "EDGE_HOST is not set")
        self.clock, self.ttl_ok, self.ttl_bad, self.hung_s = clock, ttl_ok, ttl_bad, hung_s
        self._probe = probe or http_probe
        self._log = log or _print
        if configured and not self.host:                       # a typo in the deploy settings must not leave the door off with no word anywhere
            self._say({"event": "edge_host_ignored", "why": self.why_off})
        self._lock = threading.Lock()
        self._ok = False                      # the last answer (False until the first probe finishes)
        self._when: float | None = None       # when that probe finished, by self.clock
        self._error = ""
        self._ever = False                    # has any probe, since this object was made, found the door up?
        self._gen = 0                         # which refresh is the current one: a late answer from one given up on is dropped
        self._began: float | None = None      # when the refresh in flight started (None: none is)

    # ── what a request asks ──
    def alive(self) -> bool:
        """Is the door up, as of the last probe? Answers at once, whatever the network is doing; a stale answer starts one refresh in the background."""
        if not self.host:
            return False
        now = self.clock()
        with self._lock:
            ok = self._ok
            gen = self._begin(now) if self._stale(now) and not self._running(now) else None
        if gen is not None:
            self._spawn(gen)
        return ok

    def direct(self, prefix: str) -> str | None:
        """The door's address for one gate ("https://<host>/music"), or None: the door is off, not up right now, or this is not one of PREFIXES."""
        if not self.host or prefix not in PREFIXES:
            return None
        return f"https://{self.host}/{prefix}" if self.alive() else None

    def status(self) -> dict:
        """{host, alive, checked_s_ago, error, ever_alive}: what the doctor and the Admin pages show. Reads only (no refresh is started)."""
        now = self.clock()
        with self._lock:
            error = self._error if self._when is not None else "not checked yet"
            return {"host": self.host, "alive": self._ok, "checked_s_ago": None if self._when is None else round(max(0.0, now - self._when), 1),
                    "error": error if self.host else self.why_off, "ever_alive": self._ever}

    # ── the probe ──
    def refresh(self) -> bool:
        """One probe now, on this thread (tests and dev tools; a request never calls it): store the answer and return it."""
        if not self.host:
            return False
        with self._lock:
            gen = self._begin(self.clock())
        return self._probe_and_store(gen)

    def wait(self, timeout: float = 5.0) -> bool:
        """Wait for the refresh in flight, if any (tests and dev tools). True when none is running afterwards."""
        end = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._began is None:
                    return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.002)

    # ── inside (the lock is held where it says) ──
    def _stale(self, now: float) -> bool:
        if self._when is None:
            return True
        age = now - self._when
        return age < 0 or age >= (self.ttl_ok if self._ok else self.ttl_bad)        # (a clock that stepped back is stale too)

    def _running(self, now: float) -> bool:
        return self._began is not None and 0 <= now - self._began < self.hung_s

    def _begin(self, now: float) -> int:
        self._gen += 1
        self._began = now
        return self._gen

    def _spawn(self, gen: int) -> None:
        worker = threading.Thread(target=self._probe_and_store, args=(gen,), name="edge-probe", daemon=True)
        try:
            worker.start()
        except RuntimeError:                 # no thread to be had: nothing is in flight, a later request tries again
            with self._lock:
                if self._gen == gen:
                    self._began = None

    def _probe_and_store(self, gen: int) -> bool:
        url = f"https://{self.host}{PING}"
        try:
            got = _ask(self._probe, url)
            ok, why = (bool(got[0]), str(got[1]) if len(got) > 1 else "") if isinstance(got, tuple) and got else (bool(got), "")
        except Exception as exc:  # noqa: BLE001 — a probe that blows up is a dead door, never a failed request
            ok, why = False, _why(exc)
        why = "" if ok else (why or "no answer")[:160]
        now = self.clock()
        with self._lock:
            if gen != self._gen:             # a newer refresh took over while this one hung: its word stands
                return ok
            changed = self._when is None or ok != self._ok
            self._ok, self._when, self._error = ok, now, why
            self._ever = self._ever or ok
            self._began = None
        if changed:                          # one line per change (and the first), never one per probe
            self._say({"event": "edge_door", "host": self.host, "alive": ok, "why": why})
        return ok

    def _say(self, line: dict) -> None:
        try:
            self._log(json.dumps(line))
        except Exception:  # noqa: BLE001 — a logger's trouble is not the door's
            pass
