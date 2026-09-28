"""Skippy Portal — one page: sign in with Google, see and control the game
servers, jump to the other services and to Skippy in Claude.

Run locally:   FLASK_APP=app.py flask run   (needs every var in .env.example)
Deploy:        see docs/portal/README.md
"""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for

import auth
from catalog import GAMES, SERVICES
from config import Config
from gcp import GamesVm


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    auth.init_app(app)
    app.register_blueprint(auth.bp)

    vm = GamesVm(app.config["GCP_PROJECT"], app.config["GCP_ZONE"], app.config["GCP_INSTANCE"])

    @app.get("/")
    def index():
        user = auth.current_user()
        if user and auth.is_allowed(user["email"]):
            return redirect(url_for("dashboard.index"))
        return render_template("login.html")

    from flask import Blueprint
    dash = Blueprint("dashboard", __name__)

    @dash.get("/dashboard")
    @auth.login_required
    def dashboard_index():
        try:
            status = vm.status()
            error = None
        except Exception as exc:                            # noqa: BLE001 — surface it, don't crash the page
            status, error = None, str(exc)
        return render_template(
            "dashboard.html",
            user=auth.current_user(),
            status=status,
            error=error,
            games=GAMES,
            services=SERVICES,
            ai_url=app.config["AI_ASSISTANT_URL"],
        )

    @dash.post("/games/start")
    @auth.login_required
    def games_start():
        selected = [g for g in request.form.getlist("games") if g in GAMES]
        idle = request.form.get("idle_minutes", "30")
        if selected:
            vm.start(selected, idle_minutes=int(idle) if idle.isdigit() else 30)
        return redirect(url_for("dashboard.index"))

    @dash.post("/games/stop")
    @auth.login_required
    def games_stop():
        vm.stop()
        return redirect(url_for("dashboard.index"))

    app.register_blueprint(dash)
    return app


app = create_app()

if __name__ == "__main__":
    # Cloud Run sets PORT; default to 8080 for local runs.
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
