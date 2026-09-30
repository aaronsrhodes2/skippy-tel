"""Skippy Portal — sign in with Google; see and control the game servers,
their Discord channels, and the other services; chat with Skippy. Admins
also get /admin: server settings, restart/update, Discord setup.

Deployed by .github/workflows/deploy.yml on every push — see docs/cicd.md.
Local run:  FLASK_APP=app.py flask run   (needs every var in .env.example)
"""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import re
from functools import wraps

from flask import (Blueprint, Flask, abort, flash, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import access_requests as access_mod
import admin_panel
import auth
import autoconfig
import chat
import chat_tools
import config
import diagnostics
import discord_admin
import envfile
import experiments
import friends as friends_mod
import home as home_mod
import imports as imports_mod
import media as media_mod
import mentat_client
import ops
import spend
import ratelimit
import readiness
import tasks
import telemetry_web
import watch
import workshop as workshop_mod
from catalog import GAMES, PLAYER_IDLE_MAX, PLAYER_IDLE_MIN, PORTALS, SERVICES, missing_settings
from gcp import GamesVm


def create_app(settings: dict | None = None, vm=None, skippy=None, mentat=None, home=None,
               imports=None) -> Flask:
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
    app.extensions["access_requests"] = access_mod.Requests(vm)      # people asking to be let in (Admin → Friends)
    mentat = mentat or (mentat_client.Mentat(app.config["MENTAT_URL"]) if app.config.get("MENTAT_URL") else None)
    app.extensions["mentat"] = mentat
    home = home or home_mod.Home(vm)
    app.extensions["home"] = home
    # What one caller may spend: public Mentat calls per visitor, and chat for
    # everyone but the Captain (every message costs Anthropic money).
    limiter = ratelimit.Limiter()
    app.extensions["limiter"] = limiter
    # Human-scale: a person never hits these; a script hammering them does.
    MENTAT_PER_MIN = 10
    CHAT_PER_HOUR = 60
    imports = imports or imports_mod.Imports(vm)
    media = media_mod.Media()
    workshop = workshop_mod.Workshop(home)          # the Captain's private Skippy (admin only)
    app.extensions["workshop"] = workshop
    telemetry_web.init_app(app)                     # usage recording, /privacy, the tap beacon, /home/telemetry

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
                "idle_min": PLAYER_IDLE_MIN, "idle_max": PLAYER_IDLE_MAX, "switch_owner": ops.SWITCH_OWNER,
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
        if not user and auth.promote_visitor():   # approved since they asked: they're in, no second sign-in
            user = auth.current_user()
        if not user and auth.visitor():           # signed in with Google but not on the list yet: their request page
            return redirect(url_for("request_access"))
        if not (user and auth.is_allowed(user["email"])):
            return render_template("login.html")
        status, _ = vm_status()
        shown = my_games()
        # The Games tile names the game that's up (or set for the next wake-up), only from the games this
        # person may see: a hidden game's name never shows.
        set_games = [shown[g].name for g in (status.games if status else []) if g in shown]
        live_games = [(g, shown[g].name) for g in status.games if g in shown] \
            if status and status.status == "RUNNING" else []           # the badge links to each one's page
        asking = len(pending_requests()) if auth.is_admin(me()) else 0   # a badge on the Admin tile
        return render_template("home.html", status=status, games=shown, set_games=set_games, live_games=live_games,
                               asking=asking,
                               hidden_up=bool(status and status.status == "RUNNING"
                                              and any(g not in shown for g in status.games)),
                               show_experiments=auth.has_portal(me(), "experiments"),
                               show_bilby=auth.has_portal(me(), "bilby"), music=home.music(),
                               has_me="me" in app.blueprints)

    @app.get("/health")
    def health():
        return "ok"

    # Browsers and phones ask for these at the root, whatever the page's <link> says; public, like /static.
    @app.get("/favicon.ico")
    @app.get("/apple-touch-icon.png")
    def site_icons():
        name = "favicon.ico" if request.path.endswith(".ico") else "apple-touch-icon.png"
        return send_from_directory(app.static_folder, name, max_age=86400)

    # ── asking for access (the Discord pins say "sign in and tap Request access") ─────────────
    # One person, one address, and everyone together: a flood of throwaway accounts can't fill the queue.
    ACCESS_PER_HOUR, ACCESS_PER_IP_HOUR, ACCESS_ALL_PER_HOUR = 3, 6, 60

    def address_key() -> str:
        """The caller's address for rate limiting. An IPv6 address counts as its /64: one home network is handed
        billions of them. (The address is the last X-Forwarded-For hop: right on Cloud Run's own front end.)"""
        ip = ratelimit.client_ip(request)
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if parsed.version == 6 else ip

    def pending_requests() -> list[dict]:
        """People asking for access, newest first: the Friends section, the launcher badge and the dashboard line.
        A request that no longer needs an answer is left out (they became core, or a friend who now has every game
        they asked for), so an approval whose cleanup failed doesn't linger."""
        friends = app.extensions["friends"]
        rows = []
        for email, info in app.extensions["access_requests"].all().items():
            wanted = [g for g in info.get("games", []) if g in GAMES]
            entry = friends.get(email)
            held = friends_mod.portals_of(entry)
            if auth.is_core(email) or (entry is not None and wanted and set(wanted) <= set(held)):
                continue
            rows.append({"email": email, "name": info.get("name", ""), "note": info.get("note", ""), "games": wanted,
                         "names": [GAMES[g].name for g in wanted], "is_friend": entry is not None,
                         "held_names": [PORTALS[p] for p in held],
                         "tick": [p for p in PORTALS if p in set(wanted) | set(held)],   # Approve ADDS to what they have
                         "when": str(info.get("at", ""))[:16].replace("T", " ") + " UTC"})
        return sorted(rows, key=lambda r: r["when"], reverse=True)

    @app.route("/request-access", methods=["GET", "POST"])
    def request_access():
        """A signed-in person, or a verified Google account that isn't on the list, asks for a game. The email is
        the one Google verified, so nobody can ask on behalf of an address they don't own."""
        auth.promote_visitor()                    # approved since they asked: they're in, no second sign-in
        who = auth.current_user() or auth.visitor()
        game = request.values.get("game", "")
        game = game if game in GAMES else None
        game_name = GAMES[game].name if game else None
        access = app.extensions["access_requests"]
        if not who:                               # not signed in yet: send them through Google and back to the game
            if game:
                auth.remember_next(f"/games/{game}")
            return render_template("request_access.html", who=None, game=game, game_name=game_name)
        email = who["email"]
        have = auth.visible_games(email) if auth.is_allowed(email) else []
        askable = {g: GAMES[g] for g in GAMES if g not in have}
        waiting = access.get(email) or {}
        pending_ids = [g for g in waiting.get("games", []) if g in GAMES]
        shown = dict(who=who, game=game, game_name=game_name, askable=askable, have=[GAMES[g].name for g in have],
                     have_game=bool(game and game in have), pending=waiting or None, pending_ids=pending_ids,
                     pending_names=[GAMES[g].name for g in pending_ids])
        if request.method != "POST":              # GET (and HEAD, which must never save anything)
            return render_template("request_access.html", **shown)
        if not askable:                           # already has every game: nothing to queue
            return redirect(url_for("request_access", game=game))
        picked = request.form.getlist("games")[:20]
        wanted = [g for g in picked if g in askable]
        if not wanted:
            if any(g in have for g in picked):    # approved while the form was open: they're in, say so
                return redirect(url_for("request_access", game=game))
            flash("Tick at least one game to ask for.", "error")
            return render_template("request_access.html", **shown), 400
        # Cheapest to blame first: the caller's address and the whole queue, then the person. Each allowance is only
        # spent when the ones before it passed, so a crowded address can't burn a genuine person's own allowance.
        if (not limiter.allow("access-ip:" + address_key(), ACCESS_PER_IP_HOUR, 3600)
                or not limiter.allow("access:all", ACCESS_ALL_PER_HOUR, 3600)):
            flash("A lot of requests are coming in right now, and yours wasn't saved. Try again in a little while.", "error")
            return render_template("request_access.html", **shown), 429
        if not limiter.allow("access:" + email, ACCESS_PER_HOUR, 3600):
            flash(f"You've already sent a few requests this hour. Give it a while, or nudge {ops.SWITCH_OWNER} on Discord.", "error")
            return render_template("request_access.html", **shown), 429
        try:
            outcome = access.submit(email, who.get("name", ""), wanted, request.form.get("note", ""))
        except access_mod.Full:
            flash(f"The request list is full right now, and yours wasn't saved. Nudge {ops.SWITCH_OWNER} on Discord.", "error")
            return render_template("request_access.html", **shown), 503
        except access_mod.Unavailable:
            flash("Couldn't save that just now. Try again in a minute.", "error")
            return render_template("request_access.html", **shown), 503
        print(json.dumps({"event": "access_request", "email": email, "games": wanted, "outcome": outcome}), flush=True)
        flash(f"Request sent. {ops.SWITCH_OWNER} will see it on the dashboard.", "ok")
        return redirect(url_for("request_access", game=game))     # a reload shows the page again, it doesn't re-send

    def live_info(status) -> tuple[dict[str, str], dict[str, int], dict[str, bool]]:
        """Per-session invite codes (Dragonwilds makes a new one each boot), players online and which games' own logs
        say they're joinable (portal/readiness.py), read fresh from the VM on every page load while it runs."""
        if status and status.status == "RUNNING":
            return diagnostics.live_all(vm, status.started_at)
        return {}, {}, {}

    def starting_now(status, gid: str, ready: dict[str, bool]) -> bool:
        """Is `gid` up but not joinable yet? The game's own log first (`ready`: the READY lines), the boot timer only
        as the fallback (portal/readiness.py is_starting)."""
        on = bool(status and status.status == "RUNNING" and gid in status.games)
        return on and readiness.is_starting(gid, ready.get(gid), status.minutes_up(), GAMES[gid].ready_minutes)

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

    @dash.get("/games")
    @auth.login_required
    def index():
        """The Games app: server state, Stop, and a card per game. (Was /dashboard, "Servers" and the
        launcher's game tiles and "Server room" tile: one place now.)"""
        status, error = vm_status()
        # Cards only: the join details, passwords and Discord links live on each game's own page, so this page
        # no longer reads the VM's serial output for invite codes on every load.
        return render_template("games.html", status=status, error=error, games=my_games())

    @dash.get("/dashboard")
    def legacy_dashboard():
        return redirect(url_for("dashboard.index"), 301)      # old bookmarks and links

    @dash.post("/games/start")
    @auth.login_required
    def games_start():
        wanted = request.form.getlist("games")
        if any(not auth.can_see(me(), g) for g in wanted):
            flash("You don't have access to that game.", "error")
            return back("dashboard.index")
        try:
            ok, message = ops.start_games(vm, wanted, request.form.get("idle_minutes"), auth.is_admin(me()),
                                          who=me(), visible=auth.visible_games(me()))
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
        if gid not in GAMES:
            abort(404)
        if not auth.can_see(me(), gid):           # a real game they can't see yet: offer to ask for it
            return redirect(url_for("request_access", game=gid))
        status, error = vm_status()
        env = status.env if status else {}
        banner = env.get(GAMES[gid].banner_key, "")
        codes, players, ready = live_info(status)
        return render_template(
            "game.html", game=GAMES[gid], t=GAMES[gid].theme, status=status, error=error,
            links=discord_admin.links(env, gid),
            banner=banner if banner.startswith("https://") else "",
            bot_configured=bool(app.config["DISCORD_BOT_TOKEN"]),
            codes=codes, players=players, starting=starting_now(status, gid, ready),
        )

    @dash.get("/games/<gid>/live")
    @auth.login_required
    def game_live(gid):
        """What changes while a game's page stays open: is it up, still starting, and its invite code (a
        fresh one every boot). The page polls this every 20 s and reloads itself when the state flips."""
        if not auth.can_see(me(), gid):
            abort(404)
        status, _ = vm_status()
        on = bool(status and status.status == "RUNNING" and gid in status.games)
        codes, players, ready = live_info(status)
        payload = dict(on=on, starting=starting_now(status, gid, ready),
                       code=codes.get(gid) if on else None, players=players.get(gid) if on else None)
        if on:
            payload["ready"] = ready.get(gid)       # the game's own log: true, false, or null (no answer: the timer decided)
        resp = jsonify(payload)
        resp.headers["Cache-Control"] = "no-store"
        return resp

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
            friends=friend_rows(status), portals=PORTALS, access_requests=pending_requests(),
            core=sorted(app.config["ALLOWED_EMAILS"]),
            needs={g: m for g in GAMES if status and (m := missing_settings(g, status.env))},
        )

    def friend_rows(status) -> list[dict]:
        env = status.env if status else {}
        invites = {g: discord_admin.links(env, g)["invite"] or f"https://discord.gg/{app.config['DISCORD_INVITE']}"
                   for g in GAMES}
        sender = auth.current_user().get("name") or me()
        rows = []
        for email, info in sorted(app.extensions["friends"].all().items()):
            portals = friends_mod.portals_of(info)
            subject, body = friends_mod.invite_text(email, portals, sender, request.url_root, invites)
            rows.append({"email": email, **info, "portals": portals,
                         "mailto": friends_mod.invite_mailto(email, portals, sender, request.url_root, invites),
                         "invite_text": f"{subject}\n\n{body}"})
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
                if not problem and value and s.check:          # true/false, version and list settings say what's wrong
                    value, problem = s.check(value)
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

    @admin.post("/autoconfigure")
    @auth.admin_required
    def autoconfigure():
        """Fill in every game's settings that software can fill in (autoconfig.py). Values never appear in the
        message; a join password typed here replaces the existing ones, left blank it only fills blanks."""
        try:
            plan = autoconfig.plan(vm.status().env, GAMES, join_password=request.form.get("join_password") or None)
        except autoconfig.PlanError as exc:
            flash(str(exc), "error")
            return back("admin.index")
        if plan.set:
            vm.update_env(plan.set)
        flash(plan.summary() + (" Restart the server to apply them." if plan.set else ""), "ok")
        return back("admin.index")

    def settle_request(email: str, portals: list[str]) -> list[str]:
        """Take what was just granted off the person's waiting request (Approve, or a Save on their own card: the same
        rule). What they asked for that is covered comes off; the request goes when nothing is left; what wasn't granted
        stays waiting, so a request amended after the card was drawn is never silently dropped. Returns the games still
        being asked for. A failed cleanup never undoes the grant: pending_requests() hides a request whose person
        already holds everything they asked for."""
        try:
            return app.extensions["access_requests"].answer(email, portals)
        except access_mod.Unavailable:
            return []

    @admin.post("/friends")
    @auth.admin_required
    def friends_save():
        email = friends_mod.normalize(request.form.get("email", ""))
        from_request = request.form.get("from_request") == "1"          # Approve on a request card
        if not email:
            flash("That doesn't look like an email address.", "error")
        elif auth.is_core(email):
            flash(f"{email} is set in the deploy config and already sees everything.", "error")
            settle_request(email, list(PORTALS))                        # they hold everything: nothing left to answer
        else:
            portals = [p for p in request.form.getlist("portals") if p in PORTALS]
            if from_request and not portals:
                flash("Tick at least one thing to approve, or Dismiss the request.", "error")
                return redirect(url_for("admin.index") + "#requests")
            existing = app.extensions["friends"].get(email)
            if from_request and existing is not None:                   # Approve only ever ADDS: a page drawn before
                portals = [p for p in PORTALS if p in set(portals) | set(friends_mod.portals_of(existing))]   # a change can't undo it
            try:
                app.extensions["friends"].upsert(email, portals, added_by=me())
            except friends_mod.Unavailable:
                flash("Couldn't reach the VM to save that, so nothing was changed. Try again in a minute.", "error")
                return redirect(url_for("admin.index") + "#friends")
            left = settle_request(email, portals)
            flash(f"{'Added' if existing is None else 'Updated'} {email}"
                  + (f": {', '.join(PORTALS[p] for p in portals)}." if portals else " (nothing granted yet).")
                  + (f" Still asking for: {', '.join(GAMES[g].name for g in left if g in GAMES)}." if left else "")
                  + (" Use Invite to email them." if existing is None else ""), "ok")
        return redirect(url_for("admin.index") + "#friends")

    @admin.post("/requests/dismiss")
    @auth.admin_required
    def requests_dismiss():
        email = friends_mod.normalize(request.form.get("email", ""))
        try:
            if email and app.extensions["access_requests"].dismiss(email):
                flash(f"Dismissed {email}'s request. Nothing was granted.", "ok")
        except access_mod.Unavailable:
            flash("Couldn't reach the VM to dismiss that. Try again in a minute.", "error")
        return redirect(url_for("admin.index") + "#friends")

    @admin.post("/friends/remove")
    @auth.admin_required
    def friends_remove():
        email = friends_mod.normalize(request.form.get("email", ""))
        if email:
            try:
                app.extensions["friends"].remove(email)
                flash(f"Removed {email}. They lose access on their next page load.", "ok")
            except friends_mod.Unavailable:
                flash("Couldn't reach the VM to remove that, so nothing was changed. Try again in a minute.", "error")
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
        configured = (app.config.get("PORTAL_URL") or "").strip().rstrip("/")
        if configured and discord_admin.notify.portal_url({"PORTAL_URL": configured}) != configured:
            raise RuntimeError("PORTAL_URL is set but isn't a plain https:// address (no path, no spaces): "
                               "the pinned posts would ignore it. Fix it in the deploy settings.")
        updates = discord_admin.setup(token, app.config["DISCORD_INVITE"], list(GAMES), vm.status().env,
                                      portal_url=app.config.get("PORTAL_URL") or request.url_root)   # canonical address, else where we're served
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
    admin_panel.init_app(app, vm)          # the per-game Server admin card: admin-only refresh route + template global

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
        return jsonify({**home.accept(report), "imports_wanted": imports.wanted()})

    @app.put("/home/imports/<name>")
    def home_import_chunk(name):
        """skippy-pc uploading a game database for the VM (imports.py), in chunks."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        try:
            out = imports.receive(name, request.args.get("offset", type=int, default=-1),
                                  request.args.get("total", type=int, default=0),
                                  request.args.get("sha256", ""), request.get_data(), request.url_root)
        except imports_mod.ImportError_ as exc:
            return jsonify(error=str(exc), **exc.extra), exc.status
        return jsonify(out)

    @app.put("/home/media/<job_id>")
    def home_media(job_id):
        """skippy-pc handing over a finished job's audio (media.py): only for a job the portal is waiting on."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        owner = home.owner_of(job_id)
        if owner is None:
            return jsonify(error="no such job"), 404
        request.max_content_length = media_mod.MAX_ONE
        try:
            out = media.put(job_id, request.get_data(cache=False), (request.mimetype or "").lower(),
                            owner=owner, title=request.args.get("title", ""))
        except media_mod.MediaError as exc:
            return jsonify(error=str(exc)), exc.status
        return jsonify(out)

    @app.get("/games/<name>/import")
    def game_import_download(name):
        """The games VM fetching the dump, with the one-time token from its metadata."""
        if not imports.authorized(name, request.headers.get("Authorization")):
            abort(401)
        path = imports.path(name)
        if not path:
            abort(404)
        return send_file(path, mimetype="application/gzip")

    @app.post("/games/<name>/import/done")
    def game_import_done(name):
        if not imports.authorized(name, request.headers.get("Authorization")):
            abort(401)
        imports.done(name)
        return jsonify(ok=True)

    @app.get("/ops/home")
    def ops_home():
        """Read-only home diagnostics for the ops workflow (scripts/remote.py home):
        a Google-signed ID token for one of OPS_EMAILS (the deployer), nothing else."""
        if not auth.ops_caller(request.headers.get("Authorization"), app.config["OPS_EMAILS"]):
            abort(401)
        return jsonify(home.diagnostics())

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

    @app.post("/home/workshop/<task>")
    def home_workshop(task):
        """skippy-pc reporting how a private-Skippy run is going (workshop.py). The reply says how many
        events this portal holds (so a redeployed portal gets the earlier ones again) and whether to stop."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        if (request.content_length or 0) > home_mod.MAX_REPORT:
            abort(413)
        if not re.fullmatch(r"[a-f0-9]{8,32}", task):
            abort(404)
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(workshop.push(task, str(body.get("who") or ""), str(body.get("conv") or ""),
                                         body.get("start"), body.get("events"), str(body.get("state") or ""),
                                         body.get("error")))
        except ValueError:
            abort(400)

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
        kind = a.get("type") if a.get("type") in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
        return app.response_class(base64.b64decode(a["b64"]), mimetype=kind,       # never HTML
                                  headers={"Cache-Control": "private, max-age=3600",
                                           "X-Content-Type-Options": "nosniff"})

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
        """For the ops workflow (Google ID token for the deployer): runs what it queued."""
        if not auth.ops_caller(request.headers.get("Authorization"), app.config["OPS_EMAILS"]):
            abort(401)
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
        asking = len(pending_requests())
        if asking:
            warnings.append(f"{asking} {'person is' if asking == 1 else 'people are'} asking for access: Admin → Friends.")
        core = home.core()
        if home.paired() and home.age() is not None and not home.online():
            warnings.append(f"skippy-pc has been silent for {home.age() // 60} min (power, network or the reporter).")
        elif home.online():                     # a silent home's last report is stale; the line above says so
            warnings += [f"Core: {p}" for p in core["problems"]]
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
            home=home.status(), music=home.music(), core=core, services=SERVICES,
            workshop_ready=workshop.ready(), workshop_model=home.capability_note("workshop", "model"),
            activity=telemetry_web.activity(app, home),
        )

    # The private Skippy. Admin only (Google login + ADMIN_EMAILS), and every call must carry a header a
    # cross-site page can't add without a CORS preflight: this chat can deploy code.
    def workshop_call(view):
        @wraps(view)
        @auth.admin_required
        def wrapped(*args, **kwargs):
            if request.headers.get("X-Workshop") != "1":
                abort(400)
            resp = app.make_response(view(*args, **kwargs))
            resp.headers["Cache-Control"] = "no-store"
            return resp
        return wrapped

    @mine.post("/workshop/send")
    @workshop_call
    def workshop_send():
        body = request.get_json(silent=True) or {}
        message = str(body.get("message") or "")
        try:
            task = workshop.send(me(), str(body.get("conv") or ""), message)
        except workshop_mod.Busy as exc:
            return jsonify(error=str(exc)), 409
        except workshop_mod.NotReady as exc:
            return jsonify(error=str(exc)), 503
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        print(json.dumps({"event": "workshop", "user": me(), "task": task, "chars": len(message)}), flush=True)  # never the text
        return jsonify(task=task)

    @mine.get("/workshop/events")
    @workshop_call
    def workshop_events():
        return jsonify(workshop.read(me(), request.args.get("task", ""), request.args.get("after", type=int, default=0)))

    @mine.post("/workshop/stop")
    @workshop_call
    def workshop_stop():
        body = request.get_json(silent=True) or {}
        return jsonify(ok=workshop.stop(me(), str(body.get("task") or "")))

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
            experiments.forget(vm)
            flash(f"{'Hid' if slug in gone else 'Showing'} {slug} on the public Experiments page.", "ok")
        return redirect(url_for("me.index"))

    app.register_blueprint(mine)

    # ── experiments: public showcase (no sign-in) ──────────────────────────────
    lab = Blueprint("experiments", __name__, url_prefix="/experiments")

    @lab.get("")
    def index():
        return render_template("experiments.html", bench=experiments.benchmark(),
                               **experiments.inventory(experiments.visible(vm)))

    @lab.get("/double-slit")
    def double_slit():
        return render_template("demo_double_slit.html")

    @lab.get("/nbody")
    def nbody():
        return render_template("demo_nbody.html", scoreboard=experiments.NBODY_SCOREBOARD)

    # Weapon vs. Armor: the page draws; every number comes from sigma-ground's own game
    # solver on Mentat (mentat/wva.py), which validates and clamps what it's sent.
    WVA_MAX_BODY = 8_000

    def wva_failed(exc: mentat_client.MentatError):
        """Say plainly why there's no result; never make one up."""
        if exc.status == 429:
            return jsonify(error="Mentat is busy right now. Try again in a minute."), 429
        if not exc.offline:                                    # Mentat refused this fight: say why
            return jsonify(error=str(exc)), 400
        if exc.status is None:                                 # unreachable (the raw socket error stays in the logs)
            print(json.dumps({"event": "wva_offline", "error": str(exc)[:300]}), flush=True)
            why = "Mentat is offline right now."
        elif exc.status == 404:
            why = "Mentat is up, but this version of it doesn't have Weapon vs. Armor yet."
        else:                                                  # Mentat's own words: e.g. the game didn't load, or a timeout
            why = f"Mentat couldn't run it: {exc}".rstrip(".") + "."
        return jsonify(error=why, offline=True), 502

    @lab.get("/weapon-vs-armor")
    def weapon_vs_armor():
        return render_template("demo_weapon_vs_armor.html", available=mentat is not None,
                               source=f"{experiments.REPO}/tree/master/games/weapon_vs_armor")

    @lab.get("/weapon-vs-armor/api/catalog")
    def wva_catalog():
        if not mentat:
            return jsonify(error="Mentat isn't deployed yet, so there's nothing to fire.", offline=True), 503
        try:
            return jsonify(mentat.wva_catalog())
        except mentat_client.MentatError as exc:
            return wva_failed(exc)

    @lab.post("/weapon-vs-armor/api/solve")
    def wva_solve():
        if not mentat:
            return jsonify(error="Mentat isn't deployed yet, so there's nothing to fire.", offline=True), 503
        # One budget per visitor for all public Mentat calls: the playground's, and these.
        if not limiter.allow("mentat:" + ratelimit.client_ip(request), MENTAT_PER_MIN, 60):
            return jsonify(error="Slow down: that's a lot of physics for one minute. Try again shortly."), 429
        if (request.content_length or 0) > WVA_MAX_BODY:
            return jsonify(error="That fight is too large to send."), 413
        fight = request.get_json(silent=True)
        if not isinstance(fight, dict):
            return jsonify(error="Send the fight as a JSON object."), 400
        try:
            return jsonify(result=mentat.wva_solve(fight))
        except mentat_client.MentatError as exc:
            return wva_failed(exc)

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
        if not limiter.allow("mentat:" + ratelimit.client_ip(request), MENTAT_PER_MIN, 60):
            return jsonify(error="Slow down: that's a lot of physics for one minute. Try again shortly."), 429
        args = request.get_json(silent=True) or {}
        if not isinstance(args, dict):
            return jsonify(error="arguments must be a JSON object"), 400
        try:
            return jsonify(result=mentat.call(name, args))
        except mentat_client.MentatError as exc:
            return jsonify(error=str(exc)), 400

    @lab.get("/radiance/")
    def radiance_index():
        # Radiance's JS writes ?scene into the page as HTML on a failed load
        # (setErr → innerHTML), and it runs on the portal's own origin: a crafted
        # link would be script with the signed-in user's session. Only a plain
        # scene id (the same shape its viewer accepts) and probe=1 get through.
        args = request.args
        scene_ok = re.fullmatch(r"[a-z0-9_]{1,80}", args.get("scene", "")) is not None
        if any(k not in ("scene", "probe") for k in args) or ("scene" in args and not scene_ok) \
                or args.get("probe", "1") != "1":
            clean = {"scene": args["scene"]} if scene_ok else {}
            return redirect(url_for("experiments.radiance_index", **clean))
        page = experiments.radiance_dir() / "index.html"
        if not page.exists():
            return render_template("experiments.html", bench=experiments.benchmark(), missing="Radiance",
                                   **experiments.inventory(experiments.EXPERIMENTS)), 503
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
                                    home_mentat=home if home.mentat_ready() else None,
                                    home_music=home if home.can("make_music") else None)

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
        if not auth.is_admin(user["email"]):
            if not limiter.allow("chat:" + user["email"], CHAT_PER_HOUR, 3600):
                return jsonify(error=f"That's {CHAT_PER_HOUR} messages this hour; Skippy needs a breather. "
                                     "Try again in a bit."), 429
        home.touch_chat()
        force, rest = chat_tools.parse_verb(message)
        if force and not rest:
            rest = {"ask_mentat": "(the user chose /physics with no question: ask what to compute)",
                    "make_music": "(the user chose /music with no description: ask what kind of music to make)"
                    }.get(force, "Go ahead.")
        try:
            admin = auth.is_admin(user["email"])
            tools = tools_for(user)
            model = app.config["CHAT_MODEL"]
            page = str(body.get("page") or "")[:100] or None
            prompt = chat.system_prompt(user["name"], is_admin=admin, page=page,
                                        games=[GAMES[g].name for g in auth.visible_games(user["email"])])
            used: list[str] = []
            calls: list[tuple] = []       # (tool, arguments, result) for telemetry_web.record_chat: it keeps names and arguments, never results

            def run_tool(name, args):
                used.append(name)
                calls.append((name, args, ""))
                out = tools.run(name, args)
                calls[-1] = (name, args, out)
                return out

            schemas = tools.schemas()
            if force in ("ask_mentat", "make_music") and rest.startswith("(the user chose"):
                force = None                     # nothing to act on yet: let Skippy ask
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
        telemetry_web.record_chat(message, text, model, calls, forced=force)
        return jsonify(reply=text, tools=used, mentat_available=mentat_on, mentat_where=where,
                       media=tools.media)

    @talk.get("/media/<job_id>")
    @skippy_required
    def media_file(job_id):
        """A clip skippy-pc made for this chat (media.py): its requester or an admin, no one else."""
        item = media.get(job_id)
        if not item or not (auth.is_admin(me()) or item["owner"] == me()):
            abort(404)                           # not 403: don't confirm that a clip exists
        resp = send_file(io.BytesIO(item["data"]), mimetype=item["type"], conditional=True, max_age=0)
        resp.headers["Cache-Control"] = "private, no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

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
