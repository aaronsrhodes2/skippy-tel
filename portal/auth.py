"""Google sign-in + a hardcoded email allowlist — the whole auth model.

No accounts, no passwords: Google vouches for the email, and
config.ALLOWED_EMAILS decides whether that email may in. Removing someone
is editing one environment variable and redeploying; no user database to
maintain.
"""
from __future__ import annotations

from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, redirect, session, url_for

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


def is_allowed(email: str) -> bool:
    return email.strip().lower() in current_app.config["ALLOWED_EMAILS"]


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("auth.login"))
        # Re-checked on every request, not just at login, so removing an
        # email from ALLOWED_EMAILS takes effect immediately — no need to
        # wait for an existing session to expire.
        if not is_allowed(user["email"]):
            session.clear()
            return redirect(url_for("auth.unauthorized"))
        return view(*args, **kwargs)
    return wrapped


@bp.route("/login")
def login():
    redirect_uri = url_for("auth.callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.route("/auth/callback")
def callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or oauth.google.userinfo()
    email = (userinfo.get("email") or "").strip().lower()
    if not userinfo.get("email_verified", True) or not is_allowed(email):
        return redirect(url_for("auth.unauthorized"))
    session["user"] = {
        "email": email,
        "name": userinfo.get("name", email),
        "picture": userinfo.get("picture"),
    }
    return redirect(url_for("dashboard.index"))


@bp.route("/unauthorized")
def unauthorized():
    from flask import render_template
    return render_template("unauthorized.html"), 403


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
