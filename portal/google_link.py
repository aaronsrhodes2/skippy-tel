"""Connect Google for Skippy: the Captain's calendar, mail, contacts and tasks for the morning and bedtime routines, READ ONLY,
through the portal's own Google sign-in (the same web client and the same callback; no second "Desktop app" client).

  GET  /admin/google/connect      (admin) off to Google, asking the read-only scopes with offline access; back at auth.callback
  POST /admin/google/disconnect   (admin) revokes the grant at Google and forgets it
  GET  /home/google/access        (home token) a short-lived access token for skippy-pc (services/satellite/google_home.py)

The connect flow is told apart from a normal sign-in by its `state`: the connect route picks it and keeps it in the session, and
auth.callback hands the answer here only when Google sends that same state back. A normal sign-in never touches it. The grant is
taken only from an admin's verified address, and it never changes who is signed in.

The refresh token stays on the portal: one private file in the Tabletop's bucket (google-link/skippy.json, the same private bucket
as the chats), never in a page, a cookie or a log line. skippy-pc only ever gets access tokens (an hour each), refreshed here with the
portal's own client.
"""
from __future__ import annotations

import json
import secrets
import threading
import time

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, request, session, url_for

import auth

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/contacts.readonly", "https://www.googleapis.com/auth/tasks.readonly"]
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PATH = "google-link/skippy.json"
STATE_KEY = "google_link_state"
EARLY_S = 120                    # an access token is handed out only while it has at least this long left


def _post(url: str, data: dict) -> tuple[int, dict]:
    import requests                                                   # noqa: PLC0415
    r = requests.post(url, data=data, timeout=20)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


class Link:
    """The one grant: {email, refresh_token, scopes, granted} in the bucket, plus the current access token in memory."""

    def __init__(self, store, client, post=_post, clock=time.time):
        self.store, self.client, self.post, self.clock = store, client, post, clock    # client() -> (id, secret)
        self._lock = threading.Lock()
        self._access: tuple[str, float] | None = None

    def record(self) -> dict | None:
        raw = self.store.get(PATH)
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return None
        return data if isinstance(data, dict) and data.get("refresh_token") else None

    def state(self) -> dict:
        """What the Admin page shows: never the token."""
        rec = self.record()
        if not rec:
            return {"connected": False}
        granted = set(rec.get("scopes") or [])
        return {"connected": not rec.get("broken"), "email": rec.get("email", ""), "granted": rec.get("granted", 0),
                "missing": [s.rsplit("/", 1)[-1] for s in SCOPES if s not in granted], "broken": rec.get("broken", "")}

    def save(self, email: str, token: dict) -> list[str]:
        """Keep a fresh grant from the callback. Returns the read-only scopes Google did NOT grant (he can untick boxes)."""
        scopes = str(token.get("scope") or "").split()
        rec = {"email": email, "refresh_token": token["refresh_token"], "scopes": scopes, "granted": int(self.clock())}
        with self._lock:
            self.store.put(PATH, json.dumps(rec).encode(), "application/json")
            self._access = None
            if token.get("access_token"):
                self._access = (token["access_token"], self.clock() + float(token.get("expires_in") or 3600))
        return [s.rsplit("/", 1)[-1] for s in SCOPES if s not in scopes]

    def access(self) -> dict:
        """{"connected": True, "access_token", "expires_at", "email", "scopes"} or {"connected": False, "why"}."""
        rec = self.record()
        if not rec or rec.get("broken"):
            return {"connected": False, "why": (rec or {}).get("broken") or "not connected: press Connect Google for Skippy in Admin"}
        with self._lock:
            if not (self._access and self._access[1] > self.clock() + EARLY_S):
                cid, secret = self.client()
                code, body = self.post(TOKEN_URL, {"client_id": cid, "client_secret": secret, "refresh_token": rec["refresh_token"],
                                                   "grant_type": "refresh_token"})
                if code in (400, 401) and body.get("error") in ("invalid_grant", "unauthorized_client"):
                    rec["broken"] = "Google withdrew the grant: press Connect Google for Skippy in Admin again"
                    self.store.put(PATH, json.dumps(rec).encode(), "application/json")
                    self._access = None
                    return {"connected": False, "why": rec["broken"]}
                if code != 200 or not body.get("access_token"):
                    raise RuntimeError(f"Google's token endpoint answered {code} {body.get('error', '')}".strip())
                self._access = (body["access_token"], self.clock() + float(body.get("expires_in") or 3600))
            token, expires = self._access
        return {"connected": True, "access_token": token, "expires_at": int(expires), "expires_in": int(expires - self.clock()),
                "email": rec.get("email", ""), "scopes": rec.get("scopes") or []}

    def forget(self) -> bool:
        """Revoke at Google (best effort) and forget. True when Google confirmed the revoke."""
        rec = self.record()
        with self._lock:
            self.store.delete(PATH)
            self._access = None
        if not rec:
            return True
        try:
            code, _ = self.post(REVOKE_URL, {"token": rec["refresh_token"]})
        except Exception:                                             # noqa: BLE001 — forgetting it here is what matters
            return False
        return code in (200, 400)                                      # 400: Google had already dropped it


def is_link_answer() -> bool:
    """Is this callback the answer to Connect Google for Skippy (the state its connect route chose), not a sign-in?"""
    want = session.get(STATE_KEY)
    return bool(want) and secrets.compare_digest(str(want), str(request.args.get("state") or ""))


def finish():
    """auth.callback, for the connect flow: keep the grant if it came from an admin's verified address. Never changes the sign-in."""
    from authlib.integrations.base_client import OAuthError           # noqa: PLC0415
    session.pop(STATE_KEY, None)
    back = redirect(url_for("admin.index") + "#google")
    user = auth.current_user()
    if not user or not auth.is_admin(user["email"]):                 # signed out (or demoted) on the way: nothing is kept
        abort(403)
    try:
        if request.args.get("error"):
            raise OAuthError(error=request.args["error"])
        token = auth.oauth.google.authorize_access_token()
        info = token.get("userinfo") or {}
    except OAuthError:
        flash("Google wasn't connected: it was cancelled or timed out. Press Connect again.", "error")
        return back
    email = str(info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified", False) or not auth.is_admin(email):
        flash("Only an admin's own Google account can be connected for Skippy. Nothing was kept.", "error")
        return back
    if not token.get("refresh_token"):
        flash("Google gave no lasting access (no refresh token). Press Connect again.", "error")
        return back
    missing = current_app.extensions["google_link"].save(email, token)
    print(json.dumps({"event": "google_link", "connected": True, "missing": missing}), flush=True)   # never the token
    flash(f"Google connected for Skippy ({email})." + (f" Not granted: {', '.join(missing)}." if missing else ""),
          "error" if missing else "ok")
    return back


def init_app(app, home, store) -> Link:
    link = Link(store, lambda: (app.config["GOOGLE_CLIENT_ID"], app.config["GOOGLE_CLIENT_SECRET"]))
    app.extensions["google_link"] = link
    bp = Blueprint("google_link", __name__)

    @bp.get("/admin/google/connect")
    @auth.admin_required
    def connect():
        state = secrets.token_urlsafe(24)
        session[STATE_KEY] = state
        return auth.oauth.google.authorize_redirect(
            url_for("auth.callback", _external=True), state=state, scope=" ".join(["openid", "email", *SCOPES]),
            access_type="offline", prompt="consent", include_granted_scopes="true", login_hint=auth.current_user()["email"])

    @bp.post("/admin/google/disconnect")
    @auth.admin_required
    def disconnect():
        revoked = link.forget()
        print(json.dumps({"event": "google_link", "connected": False, "revoked": revoked}), flush=True)
        flash("Google disconnected: Skippy can't read your calendar or mail any more." if revoked else
              "Forgotten here; Google didn't confirm the revoke (myaccount.google.com/permissions shows it).", "ok")
        return redirect(url_for("admin.index") + "#google")

    @bp.get("/home/google/access")
    def home_access():
        """skippy-pc's morning routine: a short-lived access token. The home token is the key; no cookie opens it."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        try:
            reply = link.access()
        except Exception as exc:                                      # noqa: BLE001 — Google down: say so, don't crash
            return jsonify(connected=True, error=str(exc)[:200]), 502
        resp = jsonify(reply)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app.register_blueprint(bp)
    return link
