"""Skippy Portal — sign in with Google; see and control the game servers,
their Discord channels, and the other services; chat with Skippy. Admins
also get /admin: server settings, restart/update, Discord setup.

Deployed by .github/workflows/deploy.yml on every push — see docs/cicd.md.
Local run:  FLASK_APP=app.py flask run   (needs every var in .env.example)
"""
from __future__ import annotations

from flask import Blueprint, Flask, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import chat
import config
import discord_admin
from catalog import GAMES, PLAYER_IDLE_MAX, PLAYER_IDLE_MIN, SERVICES, clamp_idle, missing_settings
from gcp import GamesVm


def create_app(settings: dict | None = None, vm=None, skippy=None) -> Flask:
    app = Flask(__name__)
    app.config.update(settings if settings is not None else config.load())
    # Cloud Run terminates TLS; trust its X-Forwarded-Proto/Host so OAuth
    # redirect URLs come out as https://<portal-host>/...
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    auth.init_app(app)
    app.register_blueprint(auth.bp)

    vm = vm or GamesVm(app.config["GCP_PROJECT"], app.config["GCP_ZONE"], app.config["GCP_INSTANCE"])
    skippy = skippy or chat.SkippyChat(app.config["CHAT_MODEL"])

    @app.context_processor
    def globals_():
        user = auth.current_user()
        return {"user": user, "is_admin": bool(user) and auth.is_admin(user["email"]),
                "idle_min": PLAYER_IDLE_MIN, "idle_max": PLAYER_IDLE_MAX,
                "chat_enabled": app.config["CHAT_ENABLED"],
                "discord_invite": f"https://discord.gg/{app.config['DISCORD_INVITE']}"}

    def vm_status():
        try:
            return vm.status(), None
        except Exception as exc:   # noqa: BLE001 — show it on the page, don't crash it
            return None, str(exc)

    @app.get("/")
    def index():
        user = auth.current_user()
        if user and auth.is_allowed(user["email"]):
            return redirect(url_for("dashboard.index"))
        return render_template("login.html")

    @app.get("/health")
    def health():
        return "ok"

    # ── dashboard: everyone on the allowlist ───────────────────────────────────
    dash = Blueprint("dashboard", __name__)

    @dash.get("/dashboard")
    @auth.login_required
    def index():
        status, error = vm_status()
        return render_template(
            "dashboard.html", status=status, error=error, games=GAMES, services=SERVICES,
            links={g: discord_admin.links(status.env if status else {}, g) for g in GAMES},
        )

    @dash.post("/games/start")
    @auth.login_required
    def games_start():
        selected = [g for g in request.form.getlist("games") if g in GAMES]
        status, error = vm_status()
        if error:
            flash(f"Couldn't reach the VM: {error}", "error")
            return redirect(url_for("dashboard.index"))
        problems = [p for g in selected for p in missing_settings(g, status.env)]
        if not selected:
            flash("Pick at least one game.", "error")
        elif problems:
            for p in problems:
                flash(p + " An admin can set it on the Admin page.", "error")
        else:
            admin = auth.is_admin(auth.current_user()["email"])
            idle = clamp_idle(request.form.get("idle_minutes"), admin)
            if status.status == "RUNNING":
                flash("The server is already running. Games can only be changed while it's asleep "
                      "(or by an admin restart).", "error")
            else:
                vm.start(selected, idle_minutes=idle)
                ready = max(GAMES[g].ready_minutes for g in selected)
                flash(f"Starting {', '.join(GAMES[g].name for g in selected)} — usually joinable in about "
                      f"{ready} minutes (longer right after a game update). It sleeps after {idle} "
                      "idle minutes; anyone on the portal can wake it again.", "ok")
        return redirect(url_for("dashboard.index"))

    @dash.post("/games/stop")
    @auth.login_required
    def games_stop():
        vm.stop()
        flash("Server stopped. Worlds were saved first.", "ok")
        return redirect(url_for("dashboard.index"))

    app.register_blueprint(dash)

    # ── admin: settings, restart/update, Discord ───────────────────────────────
    admin = Blueprint("admin", __name__, url_prefix="/admin")

    @admin.get("")
    @auth.admin_required
    def index():
        status, error = vm_status()
        return render_template(
            "admin.html", status=status, error=error, games=GAMES,
            links={g: discord_admin.links(status.env if status else {}, g) for g in GAMES},
            bot_configured=bool(app.config["DISCORD_BOT_TOKEN"]),
        )

    @admin.post("/settings")
    @auth.admin_required
    def settings():
        updates = {}
        for game in GAMES.values():
            for s in game.settings:
                value = request.form.get(s.key)
                if value is None or (s.secret and value == ""):
                    continue          # secret left blank = keep the current value
                updates[s.key] = value.strip()
        if updates:
            vm.update_env(updates)
            flash("Settings saved. Restart the server to apply them.", "ok")
        return redirect(url_for("admin.index"))

    @admin.post("/restart")
    @auth.admin_required
    def restart():
        status = vm.restart()
        flash(f"Restarted ({', '.join(status.games) or 'no games'}). Server images and game "
              "updates are pulled on boot; allow 5–10 minutes.", "ok")
        return redirect(url_for("admin.index"))

    @admin.post("/discord")
    @auth.admin_required
    def discord_setup():
        token = app.config["DISCORD_BOT_TOKEN"]
        if not token:
            flash("No Discord bot token configured — see docs/cicd.md → Secrets.", "error")
            return redirect(url_for("admin.index"))
        status = vm.status()
        try:
            updates = discord_admin.setup(token, app.config["DISCORD_INVITE"], list(GAMES), status.env)
        except Exception as exc:   # noqa: BLE001
            flash(f"Discord setup failed: {exc}", "error")
            return redirect(url_for("admin.index"))
        vm.update_env(updates)
        flash("Discord channels, webhooks and pinned posts are ready. "
              "The VM picks up the webhooks on its next start.", "ok")
        return redirect(url_for("admin.index"))

    app.register_blueprint(admin)

    # ── chat ───────────────────────────────────────────────────────────────────
    talk = Blueprint("chat", __name__, url_prefix="/chat")

    @talk.get("")
    @auth.login_required
    def index():
        return render_template("chat.html")

    @talk.post("/send")
    @auth.login_required
    def send():
        if not app.config["CHAT_ENABLED"]:
            return jsonify(error="Chat isn't configured (no Anthropic API key)."), 503
        body = request.get_json(silent=True) or {}
        message = (body.get("message") or "").strip()
        if not message:
            return jsonify(error="Say something."), 400
        user = auth.current_user()
        try:
            text = skippy.reply(chat.system_prompt(user["name"]),
                                chat.build_messages(body.get("history"), message))
        except chat.ChatError as exc:
            return jsonify(error=str(exc)), 502
        return jsonify(reply=text)

    app.register_blueprint(talk)
    return app


def _lazy_app():
    """gunicorn imports `app:app`; tests import create_app without env vars."""
    import os
    return create_app() if os.environ.get("FLASK_SECRET_KEY") else None


app = _lazy_app()

if __name__ == "__main__":
    import os
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
