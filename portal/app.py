"""Skippy Portal — sign in with Google; see and control the game servers,
their Discord channels, and the other services; chat with Skippy. Admins
also get /admin: server settings, restart/update, Discord setup.

Deployed by .github/workflows/deploy.yml on every push — see docs/cicd.md.
Local run:  FLASK_APP=app.py flask run   (needs every var in .env.example)
"""
from __future__ import annotations

import base64
import json
from functools import wraps

from flask import (Blueprint, Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import chat
import chat_tools
import config
import diagnostics
import discord_admin
import envfile
import experiments
import friends as friends_mod
import home as home_mod
import mentat_client
import ops
import spend
import tasks
import watch
from catalog import GAMES, PLAYER_IDLE_MAX, PLAYER_IDLE_MIN, PORTALS, SERVICES
from gcp import GamesVm


def create_app(settings: dict | None = None, vm=None, skippy=None, mentat=None, home=None) -> Flask:
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
    mentat = mentat or (mentat_client.Mentat(app.config["MENTAT_URL"]) if app.config.get("MENTAT_URL") else None)
    app.extensions["mentat"] = mentat
    home = home or home_mod.Home(vm)
    app.extensions["home"] = home

    def me() -> str:
        return auth.current_user()["email"]

    def my_games() -> dict:
        """The games the signed-in person may see, in catalog order."""
        return {g: GAMES[g] for g in auth.visible_games(me())}

    @app.context_processor
    def globals_():
        user = auth.current_user()
        return {"user": user, "is_admin": bool(user) and auth.is_admin(user["email"]),
                "can_skippy": bool(user) and auth.is_allowed(user["email"]) and auth.has_portal(user["email"], "skippy"),
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
        """Front page: a card per sub-portal the viewer may use; sign-in otherwise."""
        user = auth.current_user()
        if not (user and auth.is_allowed(user["email"])):
            return render_template("login.html")
        status, _ = vm_status()
        return render_template("home.html", status=status, games=my_games(),
                               show_experiments=auth.has_portal(me(), "experiments"),
                               show_bilby=auth.has_portal(me(), "bilby"), music=home.music(),
                               has_me="me" in app.blueprints)

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
            ok, message = ops.start_games(vm, wanted, request.form.get("idle_minutes"), auth.is_admin(me()),
                                          who=me())
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
            friends=friend_rows(status), portals=PORTALS,
            core=sorted(app.config["ALLOWED_EMAILS"]),
        )

    def friend_rows(status) -> list[dict]:
        env = status.env if status else {}
        invites = {g: discord_admin.links(env, g)["invite"] or f"https://discord.gg/{app.config['DISCORD_INVITE']}"
                   for g in GAMES}
        sender = auth.current_user().get("name") or me()
        rows = []
        for email, info in sorted(app.extensions["friends"].all().items()):
            portals = friends_mod.portals_of(info)
            rows.append({"email": email, **info, "portals": portals,
                         "mailto": friends_mod.invite_mailto(email, portals, sender, request.url_root, invites)})
        return rows

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
            portals = [p for p in request.form.getlist("portals") if p in PORTALS]
            new = app.extensions["friends"].get(email) is None
            app.extensions["friends"].upsert(email, portals, added_by=me())
            flash(f"{'Added' if new else 'Updated'} {email}"
                  + (f": {', '.join(PORTALS[p] for p in portals)}." if portals else " (nothing granted yet).")
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

    def run_discord_setup() -> str:
        """Create/repair every game's channels, invites, webhooks and pinned
        posts. Raises with Discord's own explanation on failure."""
        token = app.config["DISCORD_BOT_TOKEN"]
        if not token:
            raise RuntimeError("No Discord bot token configured — see docs/cicd.md → Secrets.")
        updates = discord_admin.setup(token, app.config["DISCORD_INVITE"], list(GAMES), vm.status().env)
        vm.update_env(updates)
        return (f"Discord ready: {len(GAMES)} games' text + voice channels, invites, webhooks and pinned "
                "posts. The VM picks up the webhooks on its next start.")

    @admin.post("/discord")
    @auth.admin_required
    def discord_setup():
        try:
            flash(run_discord_setup(), "ok")
        except Exception as exc:   # noqa: BLE001
            flash(f"Discord setup failed: {exc}", "error")
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

    # ── home reporter (services/home-reporter) ─────────────────────────────────
    @app.post("/home/report")
    def home_report():
        """No login: the paired home reporter's bearer token is the key."""
        if (request.content_length or 0) > home_mod.MAX_REPORT:
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        report = request.get_json(silent=True)
        if not isinstance(report, dict):
            abort(400)
        return jsonify(home.accept(report))

    @app.get("/home/jobs")
    def home_jobs():
        """The reporter's quick poll while someone's chatting."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        return jsonify(home.jobs_reply())

    @app.post("/home/jobs/<job_id>")
    def home_job_done(job_id):
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        if (request.content_length or 0) > home_mod.MAX_REPORT:
            abort(413)
        return jsonify(ok=home.finish(job_id, request.get_json(silent=True) or {}))

    # ── Bilby: what's playing (grant "bilby"; controls are admin only) ──────────
    music = Blueprint("bilby", __name__, url_prefix="/bilby")

    def bilby_required(view):
        @wraps(view)
        @auth.login_required
        def wrapped(*args, **kwargs):
            if not auth.has_portal(me(), "bilby"):
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    @music.get("")
    @bilby_required
    def index():
        return render_template("bilby.html", music=home.music())

    @music.get("/state.json")
    @bilby_required
    def state():
        return jsonify(home.music())

    @music.get("/art")
    @bilby_required
    def art():
        a = home.current_art()
        if not a:
            abort(404)
        return app.response_class(base64.b64decode(a["b64"]), mimetype=a.get("type") or "image/jpeg",
                                  headers={"Cache-Control": "private, max-age=3600"})

    @music.post("/control")
    @auth.admin_required
    def control():
        ok = home.queue("music", request.form.get("action", ""))
        if request.accept_mimetypes.best == "application/json":
            return jsonify(queued=ok), (200 if ok else 400)
        flash("Sent to skippy-pc; it lands within ~15 seconds." if ok else "Unknown action.",
              "ok" if ok else "error")
        return redirect(url_for("bilby.index"))

    app.register_blueprint(music)

    # ── queued tasks from remote operators (tasks.py) ──────────────────────────
    @app.post("/tasks/run")
    def tasks_run():
        """No login: runs only what someone who controls the VM queued."""
        return jsonify(results=tasks.run_pending(vm, {"discord-setup": run_discord_setup}))

    # ── personal dashboard (admin only) ────────────────────────────────────────
    mine = Blueprint("me", __name__, url_prefix="/me")

    def discord_chatter(env: dict, limit: int = 10) -> list[dict]:
        token = app.config["DISCORD_BOT_TOKEN"]
        if not token:
            return []
        msgs = []
        for gid in GAMES:
            channel = env.get(f"DISCORD_CHANNEL_{gid.upper()}")
            if not channel:
                continue
            try:
                msgs += [{**m, "game": gid} for m in discord_admin.recent_messages(token, channel)[-limit:]]
            except Exception:   # noqa: BLE001 — one quiet channel shouldn't blank the widget
                continue
        return sorted(msgs, key=lambda m: m.get("at", ""))[-limit:][::-1]

    @mine.get("")
    @auth.admin_required
    def index():
        status, error = vm_status()
        env = status.env if status else {}
        running = bool(status and status.status == "RUNNING")
        snap = ""
        if running:
            try:
                snap = diagnostics.latest_snapshot(vm.serial_output(2))
            except Exception:   # noqa: BLE001
                snap = ""
        res = diagnostics.resources(snap) if snap else {}
        warnings = watch.warnings(vm, snap, res)
        if home.paired() and home.age() is not None and not home.online():
            warnings.append(f"skippy-pc has been silent for {home.age() // 60} min (power, network or the reporter).")
        try:
            mentat_tools = len(mentat.tools()) if mentat else None
        except mentat_client.MentatError:
            mentat_tools = None
        friends_list = [(email, [PORTALS[p] for p in friends_mod.portals_of(info)])
                        for email, info in sorted(app.extensions["friends"].all().items())]
        return render_template(
            "me.html", status=status, error=error,
            codes=diagnostics.join_codes(snap), players=diagnostics.players_online(snap), res=res,
            warnings=warnings,
            session_cost=spend.session_cost(status.machine_type, status.minutes_up()) if status else None,
            spend=spend.estimate(status.machine_type if status else "e2-standard-4"),
            friends=friends_list, chatter=discord_chatter(env),
            discord_ready=bool(app.config["DISCORD_BOT_TOKEN"]),
            experiments=experiments.EXPERIMENTS, hidden=experiments.hidden(vm), mentat_tools=mentat_tools,
            home=home.status(), music=home.music(),
        )

    @mine.post("/pair-home")
    @auth.admin_required
    def pair_home():
        """Make the home reporter's token. Shown once; only its hash is kept."""
        resp = app.make_response(render_template("pair_home.html", token=home.pair()))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @mine.post("/experiments")
    @auth.admin_required
    def toggle_experiment():
        slug = request.form.get("slug", "")
        if slug in {x.slug for x in experiments.EXPERIMENTS}:
            gone = set(experiments.hidden(vm)) ^ {slug}
            vm.set_metadata(experiments.HIDDEN_KEY, json.dumps(sorted(gone)))
            flash(f"{'Hid' if slug in gone else 'Showing'} {slug} on the public Experiments page.", "ok")
        return redirect(url_for("me.index"))

    app.register_blueprint(mine)

    # ── experiments: public showcase (no sign-in) ──────────────────────────────
    lab = Blueprint("experiments", __name__, url_prefix="/experiments")

    @lab.get("")
    def index():
        return render_template("experiments.html", experiments=experiments.visible(vm),
                               bench=experiments.benchmark())

    @lab.get("/mentat")
    def mentat_page():
        return render_template("mentat.html", available=mentat is not None)

    @lab.get("/benchmark")
    def benchmark():
        return render_template("benchmark.html", bench=experiments.benchmark())

    @lab.get("/mentat/api/tools")
    def mentat_tools():
        if not mentat:
            return jsonify(error="Mentat isn't deployed yet."), 503
        try:
            return jsonify(mentat.tools())
        except mentat_client.MentatError as exc:
            return jsonify(error=str(exc)), 502

    @lab.post("/mentat/api/call/<name>")
    def mentat_call(name):
        if not mentat:
            return jsonify(error="Mentat isn't deployed yet."), 503
        args = request.get_json(silent=True) or {}
        if not isinstance(args, dict):
            return jsonify(error="arguments must be a JSON object"), 400
        try:
            return jsonify(result=mentat.call(name, args))
        except mentat_client.MentatError as exc:
            return jsonify(error=str(exc)), 400

    @lab.get("/radiance/")
    def radiance_index():
        page = experiments.radiance_dir() / "index.html"
        if not page.exists():
            return render_template("experiments.html", experiments=experiments.EXPERIMENTS,
                                   bench=experiments.benchmark(), missing="Radiance"), 503
        html = page.read_text(encoding="utf-8")
        return html.replace("</body>", experiments.RADIANCE_SHOWCASE_SNIPPET + "</body>", 1)

    @lab.get("/radiance/<path:path>")
    def radiance_file(path):
        return send_from_directory(experiments.radiance_dir(), path, max_age=3600)

    app.register_blueprint(lab)

    @app.post("/probe")
    def radiance_probe():
        """Radiance's viewer reports its first frame to /probe (a dev check); accept and drop it."""
        return "", 204

    # ── chat ───────────────────────────────────────────────────────────────────
    talk = Blueprint("chat", __name__, url_prefix="/chat")

    def skippy_required(view):
        """Chat needs the Skippy grant (core users always have it)."""
        @wraps(view)
        @auth.login_required
        def wrapped(*args, **kwargs):
            if not auth.has_portal(me(), "skippy"):
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def tools_for(user) -> chat_tools.ChatTools:
        email = user["email"]
        return chat_tools.ChatTools(vm, is_admin=auth.is_admin(email), visible=auth.visible_games(email),
                                    who=email,
                                    music=home.music if auth.has_portal(email, "bilby") else None,
                                    home_mentat=home if home.mentat_ready() else None)

    @talk.get("/badges")
    @skippy_required
    def badges():
        """The chat's direct-verb badge panel for this person."""
        home.touch_chat()
        return jsonify(tools_for(auth.current_user()).badges())

    @talk.get("")
    @skippy_required
    def index():
        return render_template("chat.html")

    @talk.post("/send")
    @skippy_required
    def send():
        if not app.config["CHAT_ENABLED"]:
            return jsonify(error="Chat isn't configured (no Anthropic API key)."), 503
        body = request.get_json(silent=True) or {}
        message = (body.get("message") or "").strip()
        if not message:
            return jsonify(error="Say something."), 400
        user = auth.current_user()
        home.touch_chat()
        force, rest = chat_tools.parse_verb(message)
        if force and not rest:
            rest = {"ask_mentat": "(the user chose /physics with no question: ask what to compute)"
                    }.get(force, "Go ahead.")
        try:
            admin = auth.is_admin(user["email"])
            tools = tools_for(user)
            model, badge = chat.pick_model(message, app.config["CHAT_MODEL"], admin)
            page = str(body.get("page") or "")[:100] or None
            prompt = chat.system_prompt(user["name"], is_admin=admin, page=page,
                                        games=[GAMES[g].name for g in auth.visible_games(user["email"])])
            used: list[str] = []

            def run_tool(name, args):
                used.append(name)
                return tools.run(name, args)

            schemas = tools.schemas()
            if force == "ask_mentat" and rest.startswith("(the user chose"):
                force = None                     # nothing to ask yet: let Skippy ask for the question
            text = skippy.reply(prompt,
                                chat.build_messages(body.get("history"), rest),
                                tools=schemas, run_tool=run_tool, model=model, force_tool=force)
        except chat.ChatError as exc:
            return jsonify(error=str(exc)), 502
        mentat_on = tools.mentat_ok
        where = tools.mentat_where
        # One line per reply in Cloud Run's logs: who, which model, which tools (never the text).
        print(json.dumps({"event": "chat", "user": user["email"], "model": model,
                          "tools": used, "mentat_available": mentat_on, "mentat_where": where}), flush=True)
        return jsonify(reply=text, model=badge, tools=used, mentat_available=mentat_on, mentat_where=where)

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
