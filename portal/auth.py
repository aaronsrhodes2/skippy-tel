"""Google sign-in + email allowlists — the whole auth model.

Google vouches for the email. Who may sign in:
  • ALLOWED_EMAILS (deploy config): core users, every game
  • friends an admin added on the Admin page (friends.py): only their games
ADMIN_EMAILS (a subset of the core) may change settings and manage friends.
All of it is re-checked on every request, so removing someone or taking a
game away takes effect on the next page load, not when the session expires.
"""
from __future__ import annotations

from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, current_app, redirect, render_template, session, url_for

bp = Blueprint("auth", __name__)
oauth = OAuth()


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


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("index"))
        if not is_allowed(user["email"]):
            session.clear()
            return redirect(url_for("auth.unauthorized"))
        return view(*args, **kwargs)
    return wrapped


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
    return oauth.google.authorize_redirect(url_for("auth.callback", _external=True))


@bp.route("/auth/callback")
def callback():
    token = oauth.google.authorize_access_token()
    info = token.get("userinfo") or oauth.google.userinfo()
    email = (info.get("email") or "").strip().lower()
    if not info.get("email_verified", True) or not is_allowed(email):
        return redirect(url_for("auth.unauthorized"))
    session["user"] = {"email": email, "name": info.get("name", email), "picture": info.get("picture")}
    return redirect(url_for("index"))


@bp.route("/unauthorized")
def unauthorized():
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
