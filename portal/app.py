"""Skippy Portal — sign in with Google; see and control the game servers,
their Discord channels, and the other services; chat with Skippy. Admins
also get /admin: server settings, restart/update, Discord setup.

Deployed by .github/workflows/deploy.yml on every push — see docs/cicd.md.
Local run:  FLASK_APP=app.py flask run   (needs every var in .env.example)
"""
from __future__ import annotations

from flask import Blueprint, Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import chat
import chat_tools
import config
import diagnostics
import discord_admin
import friends as friends_mod
import envfile
import ops
from catalog import GAMES, PLAYER_IDLE_MAX, PLAYER_IDLE_MIN, SERVICES
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
    app.extensions["friends"] = friends_mod.Friends(vm)

    def me() -> str:
        return auth.current_user()["email"]

    def my_games() -> dict:
        """The games the signed-in person may see, in catalog order."""
        return {g: GAMES[g] for g in auth.visible_games(me())}

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

    def live_info(status) -> tuple[dict[str, str], dict[str, int]]:
        """Per-session invite codes (Dragonwilds makes a new one each boot) and
        players online, read fresh from the VM on every page load while it runs."""
        return diagnostics.live(vm) if status and status.status == "RUNNING" else ({}, {})

    def back(default_endpoint: str):
        """Return to the game page a form came from (hidden `next` field), or
        to `default_endpoint`. Only /games/<known id> is accepted, so `next`
        can't bounce anyone off-site."""
        target = request.form.get("next", "")
        if target.startswith("/games/") and target[len("/games/"):] in GAMES:
            return redirect(target)
        return redirect(url_for(default_endpoint))

    # ── dashboard: everyone on the allowlist ───────────────────────────────────
    dash = Blueprint("dashboard", __name__)

    @dash.get("/dashboard")
    @auth.login_required
    def index():
        status, error = vm_status()
        return render_template(
            "dashboard.html", status=status, error=error, games=my_games(), services=SERVICES,
            links={g: discord_admin.links(status.env if status else {}, g) for g in my_games()},
            codes=live_info(status)[0],
        )

    @dash.post("/games/start")
    @auth.login_required
    def games_start():
        wanted = request.form.getlist("games")
        if any(not auth.can_see(me(), g) for g in wanted):
            flash("You don't have access to that game.", "error")
            return back("dashboard.index")
        try:
            ok, message = ops.start_games(vm, wanted, request.form.get("idle_minutes"), auth.is_admin(me()))
        except Exception as exc:   # noqa: BLE001 — the VM API; show it, don't 500
            ok, message = False, f"Couldn't reach the VM: {exc}"
        flash(message, "ok" if ok else "error")
        return back("dashboard.index")

    @dash.post("/games/stop")
    @auth.login_required
    def games_stop():
        ok, message = ops.may_stop(vm, auth.is_admin(me()), auth.visible_games(me()))
        if ok:
            ok, message = ops.stop_server(vm)
        flash(message, "ok" if ok else "error")
        return back("dashboard.index")

    @dash.get("/games/<gid>")
    @auth.login_required
    def game(gid):
        if not auth.can_see(me(), gid):
            abort(404)
        status, error = vm_status()
        env = status.env if status else {}
        banner = env.get(GAMES[gid].banner_key, "")
        return render_template(
            "game.html", game=GAMES[gid], t=GAMES[gid].theme, status=status, error=error,
            links=discord_admin.links(env, gid),
            banner=banner if banner.startswith("https://") else "",
            bot_configured=bool(app.config["DISCORD_BOT_TOKEN"]),
            **dict(zip(("codes", "players"), live_info(status))),
        )

    @dash.get("/games/<gid>/discord")
    @auth.login_required
    def game_discord(gid):
        """Read-only copy of the game's Discord text channel, for its page."""
        if not auth.can_see(me(), gid):
            abort(404)
        token = app.config["DISCORD_BOT_TOKEN"]
        status, error = vm_status()
        channel = (status.env if status else {}).get(f"DISCORD_CHANNEL_{gid.upper()}")
        if not token or not channel:
            return jsonify(error="This game's Discord channel isn't set up yet."), 404
        try:
            return jsonify(messages=discord_admin.recent_messages(token, channel))
        except Exception as exc:   # noqa: BLE001 — Discord down / bot lacks access: say so
            code = getattr(exc, "code", None)
            why = ("the bot can't read that channel (check its permissions)" if code in (401, 403)
                   else "Discord didn't answer")
            return jsonify(error=f"Couldn't load the channel: {why}."), 502

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
            friends=friend_rows(status),
            core=sorted(app.config["ALLOWED_EMAILS"]),
        )

    def friend_rows(status) -> list[dict]:
        env = status.env if status else {}
        invites = {g: discord_admin.links(env, g)["invite"] or f"https://discord.gg/{app.config['DISCORD_INVITE']}"
                   for g in GAMES}
        sender = auth.current_user().get("name") or me()
        return [{"email": email, **info,
                 "mailto": friends_mod.invite_mailto(email, info.get("games", []), sender,
                                                     request.url_root, invites)}
                for email, info in sorted(app.extensions["friends"].all().items())]

    @admin.post("/settings")
    @auth.admin_required
    def settings():
        updates, bad = {}, []
        for game in GAMES.values():
            for s in game.settings:
                value = request.form.get(s.key)
                if value is None or (s.secret and value == ""):
                    continue          # secret left blank = keep the current value
                value = value.strip()
                problem = envfile.check(value)
                if not problem and s.key == game.banner_key and value and not value.startswith("https://"):
                    problem = "must start with https://"
                if problem:
                    bad.append(f"{game.name} {s.label.lower()} {problem}.")
                    continue
                updates[s.key] = value
        for message in bad:
            flash(message + " Nothing was saved.", "error")
        if bad:
            return back("admin.index")
        if updates:
            vm.update_env(updates)
            flash("Settings saved. Restart the server to apply them.", "ok")
        return back("admin.index")

    @admin.post("/friends")
    @auth.admin_required
    def friends_save():
        email = friends_mod.normalize(request.form.get("email", ""))
        if not email:
            flash("That doesn't look like an email address.", "error")
        elif auth.is_core(email):
            flash(f"{email} is set in the deploy config and already sees everything.", "error")
        else:
            games = [g for g in request.form.getlist("games") if g in GAMES]
            new = app.extensions["friends"].get(email) is None
            app.extensions["friends"].upsert(email, games, added_by=me())
            flash(f"{'Added' if new else 'Updated'} {email}"
                  + (f": {', '.join(GAMES[g].name for g in games)}." if games else " (no games yet).")
                  + (" Use Invite to email them." if new else ""), "ok")
        return redirect(url_for("admin.index") + "#friends")

    @admin.post("/friends/remove")
    @auth.admin_required
    def friends_remove():
        email = friends_mod.normalize(request.form.get("email", ""))
        if email:
            app.extensions["friends"].remove(email)
            flash(f"Removed {email}. They lose access on their next page load.", "ok")
        return redirect(url_for("admin.index") + "#friends")

    @admin.post("/restart")
    @auth.admin_required
    def restart():
        flash(ops.restart_server(vm)[1], "ok")
        return back("admin.index")

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
        return back("admin.index")

    @admin.get("/logs")
    @auth.admin_required
    def logs():
        gid = request.args.get("game")
        game = GAMES.get(gid) if gid else None
        try:
            logs, error = diagnostics.read(vm, game.id if game else None), None
        except Exception as exc:   # noqa: BLE001
            logs, error = None, str(exc)
        return render_template("logs.html", logs=logs, error=error, game=game)

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
            admin = auth.is_admin(user["email"])
            tools = chat_tools.ChatTools(vm, is_admin=admin, visible=auth.visible_games(user["email"]))
            text = skippy.reply(chat.system_prompt(user["name"], is_admin=admin),
                                chat.build_messages(body.get("history"), message),
                                tools=tools.schemas(), run_tool=tools.run)
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
