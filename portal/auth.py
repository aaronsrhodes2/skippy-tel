"""Google sign-in + email allowlists — the whole auth model.

Google vouches for the email. Who may sign in:
  • ALLOWED_EMAILS (deploy config): core users, every game
  • friends an admin added on the Admin page (friends.py): only their games
ADMIN_EMAILS (a subset of the core) may change settings and manage friends.
A Google-verified email that is on neither list is a "visitor" (session["visitor"], never
session["user"]): it can reach /request-access and nothing else (access_requests.py). A
session holds one identity, and a visitor who is later approved is promoted on their next visit.
All of it is re-checked on every request, so removing someone or taking a
game away takes effect on the next page load, not when the session expires.
"""
from __future__ import annotations

import re
from functools import wraps

from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client import OAuthError
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)
oauth = OAuth()

# The only place a sign-in may send someone back to: a game's page (the Discord pins link there).
# Anything else is ignored, so `next` can never be an open redirect.
_NEXT = re.compile(r"/games/([a-z0-9_-]{1,32})")


def init_app(app):
    oauth.init_app(app)
    oauth.register(
        name="google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def current_user() -> dict | None:
    return session.get("user")


def _friends():
    return current_app.extensions["friends"]


def is_core(email: str) -> bool:
    return email.strip().lower() in current_app.config["ALLOWED_EMAILS"]


def is_allowed(email: str) -> bool:
    return is_core(email) or _friends().get(email) is not None


def is_admin(email: str) -> bool:
    return email.strip().lower() in current_app.config["ADMIN_EMAILS"]


def visible_portals(email: str) -> list[str]:
    """Sub-portals (games, skippy, experiments) this person may use, in order."""
    from catalog import PORTALS
    from friends import portals_of
    if is_core(email):
        return list(PORTALS)
    return portals_of(_friends().get(email))


def has_portal(email: str, portal: str) -> bool:
    return portal in visible_portals(email)


def visible_games(email: str) -> list[str]:
    """Game ids this person may see and play, in catalog order."""
    from catalog import GAMES
    return [p for p in visible_portals(email) if p in GAMES]


def can_see(email: str, game: str) -> bool:
    return game in visible_games(email)


def visitor() -> dict | None:
    """A signed-in Google account (email verified) that isn't on the list: it may ask for access, nothing else."""
    return session.get("visitor")


def remember_next(path: str) -> None:
    """Remember a game page to come back to after signing in (anything else is ignored)."""
    from catalog import GAMES
    found = _NEXT.fullmatch(path or "")
    if found and found.group(1) in GAMES:
        session["next"] = path


def wanted_game() -> str | None:
    """The game page a person was trying to open when they were sent to sign in."""
    found = _NEXT.fullmatch(session.get("next") or "")
    return found.group(1) if found else None


def _become_visitor(user: dict) -> None:
    """A signed-in person who lost access (or never had it) keeps their verified identity, without the access."""
    keep = session.get("next")
    session.clear()
    session["visitor"] = {"email": user["email"], "name": user.get("name") or ""}
    if keep:
        session["next"] = keep


def promote_visitor() -> bool:
    """A visitor whose email has since been added (the Captain approved the request) becomes a signed-in user without
    signing in again: Google verified that email in this very session, and access is still checked as always."""
    seen = visitor()
    if not seen or not is_allowed(seen["email"]):
        return False
    session.pop("visitor", None)
    session["user"] = {"email": seen["email"], "name": seen.get("name") or seen["email"], "picture": None}
    return True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user and promote_visitor():        # approved since they asked: they're in, no second sign-in
            user = current_user()
        if not user:
            remember_next(request.path)           # a Discord link to a game page survives the trip through Google
            if visitor():                         # already signed in, just not on the list: their request page, not sign-in
                return redirect(url_for("auth.unauthorized"))
            return redirect(url_for("index"))
        if not is_allowed(user["email"]):
            _become_visitor(user)
            return redirect(url_for("auth.unauthorized"))
        return view(*args, **kwargs)
    return wrapped


def portal_required(portal: str):
    """For an app a friend may be granted (catalog.PORTALS: "audio", "video", ...): signed in, on the list, and holding
    that grant (the Captain and admins hold them all). Anyone else gets 403."""
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not has_portal(current_user()["email"], portal):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin(current_user()["email"]):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@bp.route("/login")
def login():
    # ?switch=1 makes Google ask which account, for someone who signed in with the wrong one
    extra = {"prompt": "select_account"} if request.args.get("switch") else {}
    return oauth.google.authorize_redirect(url_for("auth.callback", _external=True), **extra)


@bp.route("/auth/callback")
def callback():
    try:
        if request.args.get("error"):                                     # they cancelled on Google's screen
            raise OAuthError(error=request.args["error"])
        token = oauth.google.authorize_access_token()
        info = token.get("userinfo") or oauth.google.userinfo()
    except OAuthError:                                                    # cancelled, expired, or opened in another browser
        flash("Sign-in was cancelled or timed out. Try again.", "error")
        return redirect(url_for("index"))
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified", False):                # unverified = no entry, not even a request
        return redirect(url_for("auth.unauthorized"))
    if not is_allowed(email):
        # Verified, so they may ask to be let in. One identity per session: a friend signing in with a different,
        # unlisted account must not stay signed in as the first one, or the request would be filed under the wrong email.
        _become_visitor({"email": email, "name": info.get("name", "")})
        return redirect(url_for("auth.unauthorized"))
    session.pop("visitor", None)
    session["user"] = {"email": email, "name": info.get("name", email), "picture": info.get("picture")}
    game = wanted_game()                                                      # read before the pop below
    target = session.pop("next", None)
    if game and not can_see(email, game):
        return redirect(url_for("request_access", game=game))             # came for a game they can't see: offer to ask
    return redirect(target if game else url_for("index"))


@bp.route("/unauthorized")
def unauthorized():
    if promote_visitor():                                  # approved since they asked: no need to sign in again
        game = wanted_game()
        session.pop("next", None)
        return redirect(url_for("dashboard.game", gid=game) if game and can_see(current_user()["email"], game)
                        else url_for("index"))
    if current_user() and is_allowed(current_user()["email"]):    # they're in: nothing to be unauthorized about
        return redirect(url_for("index"))
    if visitor():
        return redirect(url_for("request_access", game=wanted_game()))
    return render_template("unauthorized.html"), 403


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


OPS_AUDIENCE = "skippy-portal-ops"


def ops_caller(header: str | None, allowed: frozenset[str]) -> str | None:
    """The service account behind a Google-signed ID token (audience
    OPS_AUDIENCE), if it's one of `allowed`; else None."""
    if not header or not header.startswith("Bearer ") or not allowed:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token
    try:
        claims = id_token.verify_oauth2_token(header[7:].strip(), Request(), audience=OPS_AUDIENCE)
    except Exception:  # noqa: BLE001 — bad, expired or foreign token
        return None
    email = claims.get("email", "")
    return email if claims.get("email_verified") and email in allowed else None
