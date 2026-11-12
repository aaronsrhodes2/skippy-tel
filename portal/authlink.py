"""The glasses' sign-in link (the Captain, 2026-11-02).

OpenViture's WebView (a browser with no cookies) hosts /hud on the glasses and has to be signed in as him. On /hud in his phone's browser, "Sign the
glasses in" mints a one-time link; opening it in OpenViture (its HudLinkActivity takes the share or the tap) signs that browser in.

  POST /auth/link/new   the Captain, from a page of ours (the X-Link header): mints  <nonce>.<mac>  and returns {url, expires_in}
  GET  /auth/link?t=    public: from an OpenViture browser only, verifies the mac, spends the nonce, signs in as whoever minted it, redirects to /hud

A link is a full session as its minter, so it is as small as it can be: minted only by an admin's tap, good for TTL_S or one use (whichever comes
first), bound to the portal's secret key by an HMAC (a guessed or edited token never reaches the store), rate limited per address, kept in the bucket
(a redeploy between the mint and the use does not lose it, and a second instance cannot spend it twice; without a bucket, in memory), refused when the
bucket cannot confirm it was spent, and announced to the Captain on Discord every time one is used. A browser that is not OpenViture is turned away
WITHOUT spending it, so a chat app that fetches a pasted link to draw a preview cannot burn it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from flask import Blueprint, Response, abort, jsonify, redirect, request, session, url_for

import auth
import ratelimit

TTL_S = 300
PREFIX = "auth-links/"
NONCE = re.compile(r"[A-Za-z0-9_-]{32}")           # 24 random bytes, base64url, no padding
MAC = re.compile(r"[A-Za-z0-9_-]{22}")             # the first 16 bytes of an HMAC-SHA256, base64url
BROWSER = "OpenViture/"                            # what the host appends to its WebView's user agent (HudHostActivity)
TRIES = (20, 600)                                  # redeem attempts per address per window (count, seconds)
CLEAN_AT_MOST = 50                                 # stored links looked at (and the dead ones deleted) on each mint


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Links:
    def __init__(self, secret: bytes, store=None, clock=time.time, log=lambda line: None):
        self.secret, self.store, self.clock, self.log = secret, store, clock, log
        self._mem: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _mac(self, nonce: str) -> str:
        return _b64(hmac.new(self.secret, b"auth-link|" + nonce.encode(), hashlib.sha256).digest()[:16])

    def _clean(self) -> None:
        """Dead links out of memory and, a few at a time, out of the bucket (a link never used would otherwise stay there for ever)."""
        now = self.clock()
        self._mem = {n: r for n, r in self._mem.items() if float(r.get("exp") or 0) > now}
        if self.store is None:
            return
        try:
            for path in list(self.store.list(PREFIX))[:CLEAN_AT_MOST]:
                try:
                    rec = json.loads(self.store.get(path) or b"{}")
                except ValueError:
                    rec = {}
                if float(rec.get("exp") or 0) <= now:
                    self.store.delete(path)
        except Exception as exc:  # noqa: BLE001 — housekeeping never stops a mint
            self.log(json.dumps({"event": "auth_link_clean", "problem": type(exc).__name__}))

    def mint(self, user: dict) -> str:
        """A new link for this signed-in person: `nonce.mac`. With a bucket the bucket holds the truth (RuntimeError when it can't be written)."""
        nonce = _b64(secrets.token_bytes(24))
        rec = {"email": user["email"], "name": user.get("name") or user["email"], "picture": user.get("picture"), "exp": self.clock() + TTL_S}
        with self._lock:
            self._clean()
            if self.store is None:
                self._mem[nonce] = rec
            else:
                try:
                    self.store.put(f"{PREFIX}{nonce}.json", json.dumps(rec).encode(), "application/json")
                except Exception as exc:  # noqa: BLE001
                    self.log(json.dumps({"event": "auth_link_save", "problem": type(exc).__name__}))
                    raise RuntimeError("the link could not be kept") from exc
        return f"{nonce}.{self._mac(nonce)}"

    def redeem(self, token: str) -> dict | None:
        """Who the link signs in, if it is genuine, young and unspent; None otherwise. A genuine link is spent by this call, whatever it answers.
        With a bucket, only what the bucket confirms counts (another instance may have spent it) and a bucket that cannot confirm refuses."""
        nonce, _, mac = str(token or "").partition(".")
        if not NONCE.fullmatch(nonce) or not MAC.fullmatch(mac) or not hmac.compare_digest(mac, self._mac(nonce)):
            return None
        with self._lock:
            if self.store is None:
                rec = self._mem.pop(nonce, None)
            else:
                path = f"{PREFIX}{nonce}.json"
                try:
                    raw = self.store.get(path)
                    rec = json.loads(raw) if raw else None
                    if rec is not None:
                        self.store.delete(path)
                except Exception as exc:  # noqa: BLE001 — cannot confirm it is spent: refuse rather than risk a second use
                    self.log(json.dumps({"event": "auth_link_spend", "problem": type(exc).__name__}))
                    return None
        if not isinstance(rec, dict) or self.clock() > float(rec.get("exp") or 0):
            return None
        return rec


def init_app(app, notifier, store, limiter=None, clock=time.time) -> Links:
    secret = str(app.secret_key or "").encode()
    links = Links(secret, store, clock, log=lambda line: print(line, flush=True))
    limiter = limiter or ratelimit.Limiter()
    app.extensions["auth_links"] = links
    bp = Blueprint("authlink", __name__)

    @bp.post("/auth/link/new")
    @auth.admin_required
    def new():
        """Mint a link for the signed-in Captain. Only a page of ours sends X-Link (a form on another site cannot), and the cookie is SameSite=Lax."""
        if request.headers.get("X-Link") != "1":
            abort(400)
        if not secret:
            return jsonify(error="The portal has no secret key to sign a link with."), 503
        try:
            token = links.mint(auth.current_user())
        except RuntimeError:
            return jsonify(error="The link couldn't be kept (the bucket), so none was made."), 503
        base = (app.config.get("PORTAL_URL") or request.host_url).rstrip("/")
        return jsonify(url=f"{base}{url_for('authlink.use')}?t={token}", expires_in=TTL_S)

    @bp.get("/auth/link")
    def use():
        if not limiter.allow("authlink:" + ratelimit.client_ip(request), *TRIES):
            abort(429)
        if BROWSER not in (request.headers.get("User-Agent") or ""):
            return Response("Open this link in OpenViture: it signs the glasses in, and it only works there.", 403, mimetype="text/plain")
        rec = links.redeem(request.args.get("t", ""))
        if rec is None or not secret:
            return Response("This link has expired or was already used. Make a new one on /hud.", 403, mimetype="text/plain")
        if not auth.is_admin(rec["email"]):                      # whatever it was when minted, it must still be now
            return Response("This account can't sign the glasses in any more.", 403, mimetype="text/plain")
        session.clear()
        session["user"] = {"email": rec["email"], "name": rec["name"], "picture": rec.get("picture")}
        print(json.dumps({"event": "auth_link_used"}), flush=True)
        notifier.alert("The glasses signed in with a link you made.")
        return redirect("/hud")

    app.register_blueprint(bp)
    return links
