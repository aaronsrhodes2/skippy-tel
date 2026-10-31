"""Maker Portal — sign in with Google; see and control the game servers,
their Discord channels, and the other services; chat with Skippy. Admins
also get /admin: server settings, restart/update, Discord setup.

Deployed by skippy-core (core/cd/cd.py, settings in core/cd/deploy.json) after every push its CI passes — see docs/no-github.md.
Local run:  FLASK_APP=app.py flask run   (needs every var in .env.example)
"""
from __future__ import annotations

import dataclasses
import io
import ipaddress
import json
import os
import secrets
import re
import threading
import time
from functools import wraps

from flask import (Blueprint, Flask, abort, flash, has_request_context, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import access_requests as access_mod
import admin_panel
import auth
import attachments as attachments_mod
import autoconfig
import brains
import buildinfo
import chat
import chat_tools
import starter as starter_mod
import doctor as doctor_mod
import config
import diagnostics
import discord_admin
import envfile
import experiments
import friends as friends_mod
import home as home_mod
import housechat as housechat_mod
import imports as imports_mod
import media as media_mod
import mentat_client
import modules as modules_mod
import ops
import play
import player as player_mod
import spend
import starcontrol
import doomloop
import prophesy
import ratelimit
import readiness
import deploys as deploys_mod
import recovery
import notify as notify_mod
import power as power_mod
import recovery_web
import status as status_mod
import status_web
import car_web
import ship as ship_mod
import tabletop as tabletop_mod
import chat_history
import patient as patient_mod
import popup_in as popup_in_mod
import tool_policy
import popups
import chat_names
import tasks
import telemetry_web
import video as video_mod
import voice as voice_mod
import watch
import workshop as workshop_mod
from catalog import GAMES, PLAYER_IDLE_MAX, PLAYER_IDLE_MIN, PORTALS, SERVICES, missing_settings
from gcp import GamesVm

CANONICAL_HOST = "makerportal.live"
OTHER_HOSTS = frozenset({"www.makerportal.live", "makerportal.studio", "www.makerportal.studio"})


def create_app(settings: dict | None = None, vm=None, skippy=None, mentat=None, home=None,
               imports=None, github=None, tabletop=None, history=None, pictures=None) -> Flask:
    app = Flask(__name__)
    app.config.update(settings if settings is not None else config.load())
    # The recovery buttons' GitHub token lives only inside the client object, never in app.config (templates see that).
    token = app.config.pop("GITHUB_ACTIONS_TOKEN", "") or ""
    # Who deploys the portal says where its deploys are read (docs/no-github.md): skippy-core's deploys (core/cd) set
    # DEPLOYS_FROM=house, and its reports (POST /home/deploys) answer the ship cards and Recovery; GitHub's deploy.yml didn't.
    house_deploys = deploys_mod.HouseDeploys()
    if github is None:
        github = house_deploys if os.environ.get("DEPLOYS_FROM") == "house" else recovery.GitHub(token)
    del token
    # Cloud Run terminates TLS; trust its X-Forwarded-Proto/Host so OAuth
    # redirect URLs come out as https://<portal-host>/...
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # The portal's own name is makerportal.live (tools/domains.py maps every host below to this service); the others send
    # the visitor there, path and query kept, so a Google sign-in only ever comes back to one address. The run.app
    # address is left alone: it's the fallback when DNS or a certificate misbehaves.
    @app.before_request
    def _canonical_host():
        host = (request.host or "").split(":")[0].lower()
        if host in OTHER_HOSTS:
            qs = request.query_string.decode("latin-1")
            return redirect(f"https://{CANONICAL_HOST}{request.path}" + (f"?{qs}" if qs else ""), 301)

    auth.init_app(app)
    app.register_blueprint(auth.bp)

    vm = vm or GamesVm(app.config["GCP_PROJECT"], app.config["GCP_ZONE"], app.config["GCP_INSTANCE"])
    bot_override = skippy        # tests hand in one fake chat for every brain; live, brains.chat_for builds one per message
    app.extensions["friends"] = friends_mod.Friends(vm)
    app.extensions["modules"] = modules_mod.Modules(vm)               # the launcher's controls, and the Admin Graveyard that retires them
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
    IMAGES_PER_DAY = 10          # pictures a guest may make (the Captain: no limit); the home GPU draws each one
    POPUPS_PER_DAY = 10          # pop-up jobs a guest may start, all pop-ups together (only those whose audience is everyone)
    imports = imports or imports_mod.Imports(vm)
    media = media_mod.Media()
    # the patient queue (patient.py): pictures and songs, made when their machine is quiet, kept in the bucket until collected; the
    # Captain's are kept like his chats, forever
    pics = pictures if pictures is not None else patient_mod.from_env(forever=lambda e: str(e).strip().lower() in app.config["ADMIN_EMAILS"])

    def box_for(kind: str, name: str = "") -> str:
        """Which machine makes it: quality pictures (make_image_hq) on skippy-core; quick pictures and songs on skippy-pc; a pop-up
        wherever its registry entry says (popups.py)."""
        if kind == "popup":
            return (popups.get(name) or {}).get("box") or "pc"
        return "core" if kind == "make_image_hq" else "pc"

    def gpu_for(box: str) -> dict | None:
        """The GPU's free memory and load where the portal can see it: skippy-pc's card, from the starter's nvidia-smi; skippy-core's,
        from its quality picture studio's /health, as the reporter passes it up with the make_image_hq capability."""
        if box == "core":
            caps = ((home.report or {}).get("capabilities") or {}) if home.online() else {}
            # the quality studio's figures first; else any pop-up on skippy-core whose /health gives them (it's one shared memory)
            for name in ("make_image_hq", *(n for n, e in popups.load().items() if e["box"] == "core")):
                info = caps.get(name)
                if not isinstance(info, dict) or info.get("box") != "core":
                    continue
                gpu = {"free_pct": _num(info, "gpu_free_pct"), "util_pct": _num(info, "gpu_util_pct"),
                       "free_gib": _num(info, "gpu_free_gib")}
                if any(v is not None for v in gpu.values()):
                    return gpu
            return None
        if box != "pc" or not starter.online():
            return None
        st = starter.status or {}
        if "gpu_free_pct" not in st:
            return None
        info = ((home.report or {}).get("capabilities") or {}).get("make_image")
        if isinstance(info, dict) and info.get("box", "pc") == "pc" and info.get("loaded") is True:
            # The studio still holds its model from the last picture: that memory is the picture's own, so counting it as "not free"
            # kept every picture after the first waiting until the studio unloaded (10 idle minutes), then loaded it again.
            return {"util_pct": st.get("gpu_util_pct")}
        return {"free_pct": st.get("gpu_free_pct"), "util_pct": st.get("gpu_util_pct")}

    def patient_hold(j, jobs) -> bool:
        """home.py asks before handing a job out: True keeps a patient job back until its machine is quiet and its GPU free, one at
        a time per machine, oldest first."""
        job = j["job"]
        if job.get("kind") not in patient_mod.KINDS:
            return False
        box = job.get("box") or box_for(job["kind"], job.get("name", ""))
        for other in jobs:                                    # one per machine at a time, and the oldest goes first
            o = other["job"]
            if (other is j or o.get("kind") not in patient_mod.KINDS or (o.get("box") or box_for(o["kind"], o.get("name", ""))) != box
                    or other["event"].is_set()):
                continue
            if other["sent"] > 0 or other["created"] < j["created"]:
                return True
        return not pics.ready(box, gpu_for(box))[0]
    home.hold = patient_hold
    requeue = {"at": 0.0}

    def requeue_patient() -> None:
        """After a restart the queue in memory is empty: put every job still owed (written down in the bucket) back in line, with
        its own id, so its card keeps working. At most once a minute (it lists the bucket)."""
        import time as _time
        if _time.time() - requeue["at"] < 60:
            return
        requeue["at"] = _time.time()
        try:
            for job_id, meta in pics.waiting().items():
                if not home.has(job_id) and isinstance(meta.get("job"), dict):
                    home.submit(meta["kind"], _owner="", _ttl=patient_mod.JOB_TTL, _id=job_id,
                                **{k: v for k, v in meta["job"].items() if k not in ("id", "kind")})
        except Exception as exc:  # noqa: BLE001 — no bucket: nothing to put back
            print(json.dumps({"event": "patient_requeue_failed", "why": str(exc)[:200]}), flush=True)
    workshop = workshop_mod.Workshop(home)          # the Captain's private Skippy (admin only)
    app.extensions["workshop"] = workshop
    player = player_mod.Player(home)                # Bilby's Audio Service: the pass and Connect (admin only)
    app.extensions["player"] = player               # (so tests/dev_music.py can point it at a gate on this machine)
    video = video_mod.Video(home)                   # Nagatha's Video Service: the pass for the video gate (admin only)
    voice = voice_mod.Voice(home)                   # Skippy's ears and mouth: the pass for the voice gate (every chat)
    big_files = popup_in_mod.PopupIn(home)          # big files for pop-ups: the pass for skippy-pc's popup-gate (docs/popups.md)
    app.extensions["popup_in"] = big_files
    app.extensions["voice"] = voice
    app.extensions["video"] = video                 # (so tests/dev_video.py can point it at the gate on this machine)
    telemetry_web.init_app(app)                     # usage recording, /privacy, the tap beacon, /home/telemetry

    def me() -> str:
        return auth.current_user()["email"]

    def brain_ready(b) -> bool:
        return bot_override is not None or b.ready()

    def mode_views(is_admin: bool) -> list[dict]:
        """The chat's mode key (brains.MODES): Public for everyone; Private and Auto for the Captain, each ready only when
        skippy-pc's agent is and offers that brain. A guest's list is just Public (the page shows no key for it)."""
        agent = {b["id"]: b["ready"] for b in workshop.brains()} if is_admin and workshop.ready() else {}
        return [{**m, "ready": brain_ready(brains.CORE) if m["agent"] is None else bool(agent.get(m["agent"]))}
                for m in brains.modes(is_admin)]

    def my_games() -> dict:
        """The games the signed-in person may see, in catalog order."""
        return {g: GAMES[g] for g in auth.visible_games(me())}

    @app.context_processor
    def globals_():
        user = auth.current_user()
        member = bool(user) and auth.is_allowed(user["email"])      # signed in AND still on the list: a removed friend sees the login page
        build = buildinfo.info() if member else None               # the footer's build stamp: signed-in pages only (templates/base.html)
        return {"user": user, "is_admin": bool(user) and auth.is_admin(user["email"]),
                "build": build, "build_label": buildinfo.label(build["built_at"]) if build else "",
                "build_tip": buildinfo.tooltip(build) if build else "",
                "can_skippy": member and auth.has_portal(user["email"], "skippy"),
                "chat_languages": chat.LANGUAGES, "idle_min": PLAYER_IDLE_MIN, "idle_max": PLAYER_IDLE_MAX, "switch_owner": ops.SWITCH_OWNER,
                "chat_enabled": bool(user) and brain_ready(brains.CORE),
                "discord_invite": f"https://discord.gg/{app.config['DISCORD_INVITE']}"}

    def vm_status():
        try:
            return vm.status(), None
        except Exception as exc:   # noqa: BLE001 — show it on the page, don't crash it
            return None, str(exc)

    def workshop_context() -> dict:
        """What the agent's chat (templates/_workshop.html) needs; only ever built for the Captain."""
        return dict(workshop_ready=workshop.ready(), workshop_model=home.capability_note("workshop", "model"),
                    workshop_reason=home.capability_reason("workshop"), workshop_projects=workshop.projects(),
                    workshop_brains=workshop.brains(),
                    workshop_notify_ready=notifier.configured)

    @app.get("/")
    def index():
        """Home: the app portal. Signed out, Google sign-in. Signed in, the launcher: a card per sub-portal the person may use, Skippy
        (the chat, /chat) among them. The Captain's agent lives in the Skippy app too (see chat.index)."""
        user = auth.current_user()
        if not user and auth.promote_visitor():   # approved since they asked: they're in, no second sign-in
            user = auth.current_user()
        if not user and auth.visitor():           # signed in with Google but not on the list yet: their request page
            return redirect(url_for("request_access"))
        if not (user and auth.is_allowed(user["email"])):
            return render_template("login.html")
        if auth.has_portal(user["email"], "skippy"):   # the chat is the star: everyone with the grant lands in it, the other
            return chat_page()                         # apps one tap away in its tray (the launcher itself stays at /apps)
        return launcher_page()

    @app.get("/manifest.webmanifest")
    def site_manifest():
        """Add to Home Screen opens the portal with no browser bars at all (display: fullscreen, falling back to standalone). Public, like
        /static: a browser fetches a manifest without the sign-in cookie, and it names the site and nothing else. The music and video
        apps keep their own manifests (their pages declare them first)."""
        return app.response_class(json.dumps({
            "id": "/", "name": "Skippy the Magnificent", "short_name": "Skippy", "description": "Skippy the Magnificent: chat, games, music and films.",
            "start_url": "/", "scope": "/", "display": "fullscreen", "display_override": ["fullscreen", "standalone"], "orientation": "any",
            "background_color": "#000000", "theme_color": "#000000",
            "icons": [{"src": url_for("static", filename="icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
                      {"src": url_for("static", filename="icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
                      {"src": url_for("static", filename="apple-touch-icon.png"), "sizes": "180x180", "type": "image/png"},
                      {"src": url_for("static", filename="favicon.svg"), "sizes": "any", "type": "image/svg+xml"}]}),
            mimetype="application/manifest+json")

    @app.get("/apps")
    @auth.login_required
    def apps():
        """The launcher: a card per app this person may use. Home (/) is the chat for everyone with the chat grant."""
        return launcher_page()

    @app.get("/apps/tiles")
    @auth.login_required
    def app_tiles():
        """The chat's app tray: the launcher's tiles as data, fetched the first time the tray opens (so the chat page doesn't
        wait on the games VM's status). The chat itself isn't in it: you're in it."""
        status, _ = vm_status()
        shown = my_games()
        live_games = [(g, shown[g].name) for g in status.games if g in shown]             if status and status.status == "RUNNING" else []
        asking = len(pending_requests()) if auth.is_admin(me()) else 0
        tiles = [t for t in launcher_tiles(status, shown, live_games, asking) if t["id"] != "skippy"]
        return jsonify(tiles=[{k: t[k] for k in ("id", "glyph", "label", "href", "state", "badge")} for t in tiles],
                       all=url_for("apps"))

    def launcher_page():
        status, _ = vm_status()
        shown = my_games()
        # The Games tile names the game that's up (or set for the next wake-up), only from the games this
        # person may see: a hidden game's name never shows.
        set_games = [shown[g].name for g in (status.games if status else []) if g in shown]
        live_games = [(g, shown[g].name) for g in status.games if g in shown] \
            if status and status.status == "RUNNING" else []           # the badge links to each one's page
        asking = len(pending_requests()) if auth.is_admin(me()) else 0   # a badge on the Admin tile
        return render_template("home.html", tiles=launcher_tiles(status, shown, live_games, asking))

    def launcher_tiles(status, shown, live_games, asking) -> list[dict]:
        """The launcher's controls, in module order: the ones this person is granted, minus what the Captain retired in the
        Graveyard. `state` is the lamp (on = ready/live, idle = asleep, off = down) and the only status the launcher shows."""
        retired = app.extensions["modules"].retired()
        up = bool(status and status.status == "RUNNING")
        who = me()
        wanted = {"skippy": auth.has_portal(who, "skippy"), "games": bool(shown), "play": auth.has_portal(who, "play"),
                  "music": auth.has_portal(who, "audio"), "video": auth.has_portal(who, "video"),
                  "experiments": auth.has_portal(who, "experiments"), "tabletop": auth.has_portal(who, "tabletop"),
                  "car": auth.is_admin(who),
                  "dashboard": auth.is_admin(who) and "me" in app.blueprints, "admin": auth.is_admin(who)}
        tiles = []
        for m in modules_mod.MODULES:
            if not wanted.get(m.id) or m.id in retired:
                continue
            tile = {"id": m.id, "glyph": m.glyph, "label": m.label, "href": url_for(m.endpoint),
                    "state": None, "badge": 0, "names": []}
            if m.id == "skippy":
                tile["state"] = "on"
            elif m.id == "games":      # asleep, up, or unreachable; the names of what is up are links to those games' pages
                tile["state"] = "off" if not status else "on" if up else "idle"
                tile["names"] = [(url_for("dashboard.game", gid=g), name) for g, name in live_games]
            elif m.id == "music":
                tile["state"] = "on" if home.can("music_gate") else "off"
            elif m.id == "video":
                tile["state"] = "on" if home.can("video_gate") else "off"
            elif m.id == "admin" and asking:
                tile["badge"], tile["href"] = asking, url_for("admin.index") + "#friends"
            tiles.append(tile)
        return tiles

    @app.get("/health")
    def health():
        return "ok"

    @app.get("/version")
    def version():
        """Which build is serving: tools/loop_timer.py polls this (signed out, once a second) to learn the moment a deploy became
        visible, so it is public, tiny, never cached (a cached answer would make the new version appear late) and holds no secret."""
        response = jsonify({**buildinfo.info(), "popups": len(popups.names())})     # how many pop-up tools it knows: the deploy's smoke test compares with the registry
        response.headers["Cache-Control"] = "no-store"
        return response

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

    def live_info(status) -> tuple[dict[str, str], dict[str, int], dict[str, bool], dict]:
        """Per-session invite codes (Dragonwilds makes a new one each boot), players online, which games' own logs
        say they're joinable (portal/readiness.py) and since when each starting game has been starting, read fresh
        from the VM on every page load while it runs."""
        if status and status.status == "RUNNING":
            return diagnostics.live_all(vm, status.started_at)
        return {}, {}, {}, {}

    def starting_now(status, gid: str, ready: dict[str, bool], since: dict) -> bool:
        """Is `gid` up but not joinable yet? The game's own log first (`ready`: the READY lines; `since`: when it began
        saying "starting"), the boot timer only as the fallback (portal/readiness.py is_starting)."""
        on = bool(status and status.status == "RUNNING" and gid in status.games)
        return on and readiness.is_starting(gid, ready.get(gid), status.minutes_up(), GAMES[gid].ready_minutes,
                                            readiness.minutes_since(since.get(gid)))

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
        theme = GAMES[gid].theme
        banner = env.get(GAMES[gid].banner_key, "")
        if not banner.startswith("https://"):                    # no admin-set banner: the art that ships with the portal
            banner = url_for("static", filename=theme.hero_image) if theme.hero_image else ""
        codes, players, ready, since = live_info(status)
        return render_template(
            "game.html", game=GAMES[gid], t=theme, status=status, error=error,
            links=discord_admin.links(env, gid),
            banner=banner, backdrop=url_for("static", filename=theme.bg_image) if theme.bg_image else "",
            bot_configured=bool(app.config["DISCORD_BOT_TOKEN"]),
            codes=codes, players=players, starting=starting_now(status, gid, ready, since),
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
        codes, players, ready, since = live_info(status)
        payload = dict(on=on, starting=starting_now(status, gid, ready, since),
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
            module_rows=[{"id": m.id, "glyph": m.glyph, "label": m.label, "retirable": m.retirable,
                          "retired": m.id in app.extensions["modules"].retired()} for m in modules_mod.MODULES],
        )

    @admin.post("/modules")
    @auth.admin_required
    def module_switch():
        """The Graveyard: retire a launcher module (its control leaves the launcher) or revive it. Nothing is deleted."""
        module_id, action = request.form.get("id", ""), request.form.get("action", "")
        if module_id not in modules_mod.BY_ID or action not in ("retire", "revive"):
            abort(400)
        store = app.extensions["modules"]
        try:
            (store.retire if action == "retire" else store.revive)(module_id)
        except KeyError:
            abort(400)                                    # a module that can't be retired (Admin)
        except modules_mod.Unavailable as exc:
            flash(str(exc), "error")
        return back("admin.index")

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
        """From a game's page the form names the game (`game`): the VM comes back with that game only. Before, the page
        restarted whatever list was saved, so RuneScape's button brought back every game that had run last time."""
        game = request.form.get("game")
        if game is not None and game not in GAMES:
            abort(400)
        ok, message = ops.restart_server(vm, [game] if game else None)
        flash(message, "ok" if ok else "error")
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
    starter = starter_mod.Starter()
    app.extensions["starter"] = starter
    # The doctor (doctor.py): keeps skippy-pc and Skippy Core at their baseline by itself, through the starter; DMs the Captain when it can't.
    doctor = doctor_mod.Doctor(home, starter, notify=lambda text: notifier.alert(text))
    app.extensions["doctor"] = doctor
    recovery_web.init_app(app, workshop, limiter, github, home=home, starter=starter, doctor=doctor)   # Recovery: one button (/admin/recovery/repair)
    status_web.init_app(app, status_mod.Status(), home, starter, doctor, workshop, limiter, vm_status)   # the Status map (docs/status-design.md)
    car_web.init_app(app, home)                         # the Captain's Tesla, read only (docs/tesla.md)
    ships = ship_mod.ShipWatch(github, app.extensions["recovery"]["runs"])   # the private Skippy's ship card
    app.extensions["ships"] = ships
    # Tell the Captain on Discord when a private-Skippy run ends (notify.py: a DM to him only, one per run, switchable).
    notifier = notify_mod.Notifier(app.config.get("DISCORD_BOT_TOKEN") or "", vm, app.config.get("PORTAL_URL") or "")
    power = power_mod.Power(notify=lambda text: notifier.alert(text))   # the house's UPSes: one DM per change (power.py)
    app.extensions["power"] = power
    app.extensions["notifier"] = notifier

    def run_ended(news: dict) -> None:
        project = workshop.project(news["project"]) or {"name": news["project"]}
        notifier.finished(news["owner"], news["task"], project["name"], news["request"], news["state"], news["error"],
                          news["last_text"], news["finished_age_s"], portal_url=request.host_url if has_request_context() else "")
    workshop.on_final = run_ended

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
        local = report.get("local") if isinstance(report.get("local"), dict) else {}
        reply = {**home.accept(report), "imports_wanted": imports.wanted()}
        doctor.observe_home(report.get("doctor"))                 # skippy-pc's tests (services/home-reporter/doctor_checks.py)
        requeue_patient()                                         # patient jobs owed from before a restart: back in line
        if brains.CORE.ready() and doctor.core_due():             # and Skippy Core's, from here, on a thread (doctor.core_test)
            def test_core():
                try:
                    doctor.observe_core(*doctor_mod.core_test(brains.CORE.base_url(), brains.CORE.key(), brains.model_of(brains.CORE)))
                finally:
                    doctor.core_done()
            threading.Thread(target=test_core, daemon=True).start()
        doctor.tick()
        sweep = app.extensions.get("chat_sweep")
        if sweep:                                                 # guests' expired chats: analysed, then deleted (chat_history.py)
            sweep[0]()
            insights = sweep[1](report.get("insights_ack"))
            if insights:
                reply["insights"] = insights
        alert = str(((local.get("commute") or {}).get("alert")) or "")[:300] if isinstance(local.get("commute"), dict) else ""
        try:                                                      # a slow drive or a crash on his road: one DM per episode (notify.py)
            notifier.traffic_alert(next(iter(sorted(app.config["ADMIN_EMAILS"])), ""), alert)
        except Exception:                                         # noqa: BLE001 — never let a notification touch the report
            pass
        return jsonify(reply)

    @app.post("/home/deploys")
    def home_deploys():
        """skippy-core's deploys (core/cd/cd.py): its recent runs in, the redeploy/rollback requests Recovery made out. Home token."""
        if request.content_length is not None and request.content_length > 100_000:
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        return jsonify(house_deploys.report(payload))

    @app.post("/home/starter")
    def home_starter():
        """The starter on skippy-pc (starter.py): outbound calls with the home token. Takes its status and results, hands back the start/stop requests."""
        if request.content_length is not None and request.content_length > 60_000:
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        commands = starter.poll(payload)
        doctor.tick()                                             # (a heal it decides now goes out with the next call)
        st = starter.status or {}
        if "ups_on_battery" in st:                                # skippy-pc's own UPS: one DM per change (power.py)
            power.observe("pc", st["ups_on_battery"], st.get("ups_charge"), st.get("ups_runtime_min"))
        return jsonify(commands=commands)

    @app.post("/home/ups")
    def home_ups():
        """skippy-core's UPS (NUT's event hook on the box, core/install/ups.sh): on battery, low, back on mains. Home token, like the
        reporter and the starter; skippy-core calls it directly because skippy-pc is already down by the time its battery runs low."""
        if (request.content_length or 0) > 4_000:
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        status = str(body.get("status") or "")[:40].split()      # NUT's ups.status: "OL", "OB", "OB LB", "OL CHRG" ...
        num = lambda k: body[k] if isinstance(body.get(k), int) and not isinstance(body.get(k), bool) and 0 <= body[k] <= 9999 else None  # noqa: E731
        runtime = num("runtime_s")
        said = power.observe("core", "OB" in status if ("OB" in status or "OL" in status) else None, num("charge"),
                             runtime // 60 if runtime is not None else None, low="LB" in status)
        return jsonify(ok=True, told=bool(said))

    @app.post("/home/music/checkin")
    def home_music_checkin():
        """skippy-pc's hub checking a room box in as one of the Captain's Bilby Connect devices (services/satellite/music.py): listed under
        "Play on" in every player, and handed the same play/pause/next/prev/take commands a speaker window gets."""
        if (request.content_length or 0) > 1_000_000:            # a whole queue (player.MAX_QUEUE paths) rides along when it changes
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        body = request.get_json(silent=True) or {}
        dev, room = str(body.get("device") or ""), str(body.get("room") or "")
        if not player_mod.BOX_DEVICE.fullmatch(dev) or room != "box-" + dev[len("skippyroom-"):]:
            abort(400)
        state = body.get("state")
        captain = next(iter(sorted(app.config["ADMIN_EMAILS"])), "")
        if not captain:
            abort(404)
        return jsonify(player.checkin(captain, dev, str(body.get("name") or ""), state if isinstance(state, dict) else None, room=room, box=True))

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
        request.max_content_length = media_mod.MAX_ONE
        ctype = (request.mimetype or "").lower()
        if ctype in patient_mod.TYPES:                      # a picture or a song: kept in the bucket, so it outlives the portal (patient.py)
            data = request.get_data(cache=False)
            try:
                kept = pics.keep(job_id, data, ctype, request.args.get("title", ""))
            except Exception as exc:  # noqa: BLE001 — no bucket: it can still be served from memory below
                kept = None
                print(json.dumps({"event": "patient_not_kept", "why": str(exc)[:200]}), flush=True)
            if kept and kept.get("keep"):                     # the Captain's: a Discord DM says it's ready
                what = ("result" if kept.get("kind") == "popup" else
                        "picture" if kept.get("type", "").startswith("image/") else "song")
                notifier.alert(f"Your {what} is ready in Skippy's chat: {kept.get('title') or 'untitled'}.")
            if owner is None:
                return (jsonify(ok=True, kept=True), 200) if kept else (jsonify(error="no such job"), 404)
            try:
                return jsonify(media.put(job_id, data, ctype, owner=owner, title=request.args.get("title", "")))
            except media_mod.MediaError as exc:
                return jsonify(error=str(exc)), exc.status
        if owner is None:
            return jsonify(error="no such job"), 404
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
        if (request.content_length or 0) > workshop_mod.MAX_PUSH:       # (a full-size picture rides along as base64)
            abort(413)
        if not re.fullmatch(r"[a-f0-9]{8,32}", task):
            abort(404)
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(workshop.push(task, str(body.get("who") or ""), str(body.get("conv") or ""),
                                         body.get("start"), body.get("events"), str(body.get("state") or ""),
                                         body.get("error"), str(body.get("project") or ""), body.get("finished_age_s")))
        except ValueError:
            abort(400)

    @app.get("/home/workshop")
    def home_workshop_list():
        """What this portal holds of the private Skippy's runs (workshop.py `listing`), for the home reporter's
        reconciler: it compares this with what the bridge holds and pushes the difference, so a portal that was just
        redeployed (empty) is refilled without anyone asking. Same home-token door as the push; never cached."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        resp = jsonify(workshop.listing())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/home/patient")
    def home_patient():
        """The picture and song queue as the portal sees it (patient.py): every job still owed, where it is (in line and why,
        handed out, or missing from this process's queue) and how old. For whoever holds the home token (skippy-pc, the
        sessions working on it): a job that sat for half an hour without a word is how the 2026-10-24 dog was found."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        now, out = time.time(), []
        live = home.patient_jobs()
        for job_id, meta in pics.waiting().items():
            j = live.get(job_id)
            box = meta.get("box") or "pc"
            if j is None:
                where = "not in the queue (put back on the next report)"
            elif j["sent"]:
                where = "handed out"
            else:
                ok, why = pics.ready(box, gpu_for(box))
                where = "next up" if ok else why
                if ok and patient_hold(j, live.values()):
                    where = "behind an older job on " + box
            out.append({"id": job_id, "kind": meta.get("kind"), "box": box, "age_s": int(now - (meta.get("at") or now)),
                        "where": where})
        resp = jsonify(jobs=sorted(out, key=lambda r: -r["age_s"]))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ── The old Bilby page (a mirror of the Mac's DJ decks) is gone: Bilby's Audio Service below is where the music is ──
    # Its address, and every address under it (state.json, art), still lead somewhere: old bookmarks, a home-screen
    # shortcut and the links in old Discord posts land on the player. (GET only: nothing posts there any more.)
    @app.get("/bilby", strict_slashes=False)
    @app.get("/bilby/<path:rest>")
    def legacy_bilby(rest=""):
        return redirect(url_for("music.player_page"), 301)

    # ── Bilby's Audio Service: the Captain's library on any browser (player.py; the Captain and friends granted "audio") ────
    listen = Blueprint("music", __name__, url_prefix="/music")

    def device_id() -> str:
        dev = str(request.values.get("device") or (request.get_json(silent=True) or {}).get("device") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", dev):
            abort(400)
        return dev

    @listen.get("")
    @auth.portal_required("audio")
    def player_page():
        return render_template("music.html", rooms=player_mod.ROOMS)

    @listen.get("/manifest.webmanifest")
    def player_manifest():
        """Add to Home Screen opens straight into the player, full screen, like an app. (Public, like /static: the browser
        fetches a manifest without the sign-in cookie.)"""
        icon = url_for("static", filename="apple-touch-icon.png")
        start = url_for("music.player_page")
        return app.response_class(json.dumps({
            "id": start, "name": player_mod.NAME, "short_name": player_mod.SHORT_NAME,
            "description": "The Captain's whole music collection, streamed from home to any browser.",
            "start_url": start, "scope": start, "display": "standalone", "orientation": "any", "categories": ["music"],
            "background_color": "#000000", "theme_color": "#000000",
            "icons": [{"src": icon, "sizes": "180x180", "type": "image/png"},
                      {"src": url_for("static", filename="favicon.svg"), "sizes": "any", "type": "image/svg+xml"}]}),
            mimetype="application/manifest+json")

    @listen.get("/gate")
    @auth.portal_required("audio")
    def player_gate():
        resp = jsonify(player.gate())
        resp.headers["Cache-Control"] = "no-store"          # a pass is a credential: never from a cache
        return resp

    @listen.post("/checkin")
    @auth.portal_required("audio")
    def player_checkin():
        body = request.get_json(silent=True) or {}
        state = body.get("state")
        return jsonify(player.checkin(me(), device_id(), str(body.get("name") or ""),
                                      state if isinstance(state, dict) else None, room=str(body.get("room") or "")))

    @listen.post("/command")
    @auth.portal_required("audio")
    def player_command():
        body = request.get_json(silent=True) or {}
        dev = device_id()
        ok = player.command(me(), dev, str(body.get("action") or ""))
        # needs_tap: the other player's browser will refuse sound until someone taps it (it says so in its check-in)
        return jsonify(queued=ok, needs_tap=ok and player.needs_tap(me(), dev)), (200 if ok else 409)

    app.register_blueprint(listen)

    # ── Nagatha's Video Service: the Captain's Jellyfin films on any browser (video.py; the Captain and friends granted "video") ────
    films = Blueprint("video", __name__, url_prefix="/video")

    @films.get("")
    @auth.portal_required("video")
    def video_page():
        return render_template("video.html")

    @films.get("/manifest.webmanifest")
    def video_manifest():
        """Add to Home Screen opens straight into the film library, full screen. (Public, like /static: the browser fetches a
        manifest without the sign-in cookie; it names the app and nothing else.)"""
        icon = url_for("static", filename="apple-touch-icon.png")
        start = url_for("video.video_page")
        return app.response_class(json.dumps({
            "id": start, "name": video_mod.NAME, "short_name": video_mod.SHORT_NAME,
            "description": "The Captain's film library, streamed from home to any browser.",
            "start_url": start, "scope": start, "display": "standalone", "orientation": "any", "categories": ["entertainment"],
            "background_color": "#000000", "theme_color": "#000000",
            "icons": [{"src": icon, "sizes": "180x180", "type": "image/png"},
                      {"src": url_for("static", filename="favicon.svg"), "sizes": "any", "type": "image/svg+xml"}]}),
            mimetype="application/manifest+json")

    @films.get("/gate")
    @auth.portal_required("video")
    def video_gate():
        resp = jsonify(video.gate(read_only=not auth.is_admin(me())))   # a friend's pass can't write the watch state
        resp.headers["Cache-Control"] = "no-store"          # a pass is a credential: never from a cache
        return resp

    app.register_blueprint(films)

    # ── The Tabletop: Heroes of Chance, the heroes and the cork board (tabletop.py; the Captain and friends granted "tabletop") ──
    # Every write needs the X-Tabletop header: a page on another site can make a player's browser POST a form, but it
    # can't add a header without a CORS preflight this portal never answers.
    table = tabletop or tabletop_mod.Tabletop(tabletop_mod.store_from_env())
    app.extensions["tabletop"] = table
    heroes = tabletop_mod.Heroes(table)                # the party's sheets, their folders, and the GM's Fear
    tt = Blueprint("tabletop", __name__, url_prefix="/tabletop")

    def tt_call(fn, *args):
        if not table.ready:
            return jsonify(error="The Tabletop has no storage yet (TABLETOP_BUCKET)."), 503
        if request.method == "POST" and request.headers.get("X-Tabletop") != "1":
            return jsonify(error="missing X-Tabletop"), 400
        try:
            return jsonify(fn(*args) or {"ok": True})
        except tabletop_mod.NotFound:
            return jsonify(error="That card or hero is gone."), 404
        except tabletop_mod.Refused as err:
            return jsonify(error=str(err)), 400
        except tabletop_mod.StorageMissing:
            table.reset(); heroes.reset()
            app.logger.error("tabletop: the storage bucket doesn't exist (infra/bootstrap makes it)")
            return jsonify(error="The Tabletop's storage bucket doesn't exist yet: the GM needs to run infra/bootstrap."), 503
        except Exception:   # noqa: BLE001 — the bucket is unreachable: say so, never a bare 500
            table.reset(); heroes.reset()               # the write didn't happen: don't keep a copy that says it did
            app.logger.exception("tabletop: storage call failed")
            return jsonify(error="The Tabletop can't reach its storage right now. Try again in a minute."), 503

    def tt_who():
        user = auth.current_user()
        return user["email"], (user.get("name") or "").split(" ")[0], auth.is_admin(user["email"])

    @tt.get("")
    @auth.portal_required("tabletop")
    def page():
        """Heroes of Chance: the heroes' cards along the top, and below them the cork board where everything else about the game is
        added and shown."""
        return render_template("tabletop.html", ready=table.ready, gm=auth.is_admin(me()), me_email=me(), title=tabletop_mod.CAMPAIGN)

    @tt.get("/board")
    @auth.portal_required("tabletop")
    def board():
        """The whole cork board; with ?since=<rev>, just {"rev": n} while nothing has changed (the page polls this)."""
        since = request.args.get("since", type=int)

        def answer():                                   # the Fear rides along on every poll: it's on screen everywhere
            fear_now = heroes.fear()
            if since is not None and table.rev() == since:
                return {"rev": since, "fear": fear_now}
            return {**table.view(gm=auth.is_admin(me())), "fear": fear_now}      # a player's copy has fogged and hidden cards taken out
        resp = tt_call(answer)
        if not isinstance(resp, tuple):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @tt.post("/board/text")
    @auth.portal_required("tabletop")
    def card_text():
        who, name, gm = tt_who()
        return tt_call(lambda: {"card": table.add_text(who, name, request.get_json(silent=True) or {}, gm)})

    @tt.post("/board/image")
    @auth.portal_required("tabletop")
    def card_image():
        who, name, gm = tt_who()
        upload = request.files.get("img")
        data = upload.read(tabletop_mod.MAX_IMAGE + 1) if upload else b""
        return tt_call(lambda: {"card": table.add_image(who, name, data, request.form.to_dict(), gm)})

    @tt.post("/board/c/<cid>/move")
    @auth.portal_required("tabletop")
    def card_move(cid):
        who, _, gm = tt_who()
        return tt_call(lambda: {"card": table.move(cid, who, gm, request.get_json(silent=True) or {})})

    @tt.post("/board/c/<cid>/edit")
    @auth.portal_required("tabletop")
    def card_edit(cid):
        who, _, gm = tt_who()
        return tt_call(lambda: {"card": table.edit(cid, who, gm, request.get_json(silent=True) or {})})

    @tt.post("/board/c/<cid>/vis")
    @auth.admin_required
    def card_vis(cid):
        """The GM shows a card, fogs it, or hides it. Only the GM sees fogged and hidden cards' content (tabletop.py)."""
        return tt_call(lambda: {"card": table.set_vis(cid, (request.get_json(silent=True) or {}).get("vis"))})

    @tt.post("/board/c/<cid>/delete")
    @auth.portal_required("tabletop")
    def card_delete(cid):
        who, _, gm = tt_who()
        return tt_call(table.remove, cid, who, gm)

    @tt.get("/board/img/<name>")
    @auth.portal_required("tabletop")
    def card_img(name):
        if not table.ready:
            abort(503)
        found = table.image(name, auth.is_admin(me()))
        if not found:
            abort(404)
        resp = app.response_class(found[0], mimetype=found[1])
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"   # a card's picture never changes under its name
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # ── Heroes: every field of the official sheet, editable by the hero's player and the GM; a folder each; the GM's Fear ──
    @tt.get("/fear")
    @auth.portal_required("tabletop")
    def fear():
        return tt_call(lambda: {"fear": heroes.fear(), "max": tabletop_mod.FEAR_MAX})

    @tt.post("/fear")
    @auth.admin_required
    def fear_set():
        body = request.get_json(silent=True) or {}
        return tt_call(lambda: {"fear": heroes.set_fear(value=body.get("value"), delta=body.get("delta"))})

    @tt.get("/heroes")
    @auth.portal_required("tabletop")
    def heroes_list():
        return tt_call(lambda: {"heroes": heroes.summary(gm=auth.is_admin(me())), "fear": heroes.fear()})

    @tt.post("/heroes")
    @auth.admin_required
    def hero_new():
        body = request.get_json(silent=True) or {}
        return tt_call(lambda: heroes.create(me(), body.get("name"), body.get("sheet"),
                                             body.get("player", ""), body.get("player_name", ""), body.get("gm_ref")))

    @tt.get("/h/<hid>")
    @auth.portal_required("tabletop")
    def hero(hid):
        """A hero's sheet and folder; with ?since=<rev>, just {"rev": n} while nothing has changed (the open sheet polls this)."""
        since = request.args.get("since", type=int)
        who, _, gm = tt_who()

        def answer():
            if since is not None and heroes.hero(hid)["rev"] == since:
                return {"rev": since}
            return {**heroes.view(hid, history=gm), "can_edit": heroes.can_edit(hid, who, gm), "gm": gm}
        resp = tt_call(answer)
        if not isinstance(resp, tuple):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @tt.post("/h/<hid>/set")
    @auth.portal_required("tabletop")
    def hero_set(hid):
        who, _, gm = tt_who()

        def change():
            if not heroes.can_edit(hid, who, gm):
                raise tabletop_mod.Refused("Only this hero's player (or the GM) can change the sheet.")
            return heroes.update(hid, who, (request.get_json(silent=True) or {}).get("ops"))
        return tt_call(change)

    @tt.post("/h/<hid>/player")
    @auth.admin_required
    def hero_player(hid):
        body = request.get_json(silent=True) or {}
        return tt_call(heroes.set_player, hid, body.get("player", ""), body.get("player_name", ""))

    @tt.post("/h/<hid>/gm")
    @auth.admin_required
    def hero_gm_ref(hid):
        """The GM's quick reference for this hero: storytelling details only the GM sees."""
        return tt_call(heroes.set_gm_ref, hid, (request.get_json(silent=True) or {}).get("gm_ref", []))

    @tt.post("/h/<hid>/delete")
    @auth.admin_required
    def hero_delete(hid):
        return tt_call(heroes.remove, hid)

    @tt.post("/h/<hid>/files")
    @auth.portal_required("tabletop")
    def hero_file_add(hid):
        who, _, gm = tt_who()
        upload = request.files.get("file")
        data = upload.read(tabletop_mod.MAX_FILE + 1) if upload else b""

        def add():
            if not heroes.can_edit(hid, who, gm):
                raise tabletop_mod.Refused("Only this hero's player (or the GM) can add to the folder.")
            return {"file": heroes.add_file(hid, who, data, request.form.get("name") or (upload.filename if upload else ""))}
        return tt_call(add)

    @tt.post("/h/<hid>/avatar")
    @auth.portal_required("tabletop")
    def hero_avatar(hid):
        """Which picture in the hero's folder is their face on the card (the hero's player and the GM choose)."""
        who, _, gm = tt_who()

        def choose():
            if not heroes.can_edit(hid, who, gm):
                raise tabletop_mod.Refused("Only this hero's player (or the GM) can choose the avatar.")
            body = request.get_json(silent=True) or {}
            return heroes.set_avatar(hid, body.get("file"), who, body.get("focus"))
        return tt_call(choose)

    @tt.post("/h/<hid>/files/<fid>/delete")
    @auth.portal_required("tabletop")
    def hero_file_delete(hid, fid):
        who, _, gm = tt_who()
        return tt_call(heroes.remove_file, hid, fid, who, gm)

    @tt.get("/h/<hid>/files/<fid>")
    @auth.portal_required("tabletop")
    def hero_file(hid, fid):
        if not table.ready:
            abort(503)
        try:
            found = heroes.file(hid, fid)
        except tabletop_mod.NotFound:
            found = None
        if not found:
            abort(404)
        data, ctype, meta = found
        resp = app.response_class(data, mimetype=ctype)
        safe = re.sub(r'[^A-Za-z0-9 ._()-]', "_", meta["name"])   # ASCII only: it goes in a header
        if not safe.lower().endswith("." + meta["ext"]):
            safe += "." + meta["ext"]
        resp.headers["Content-Disposition"] = f'{"attachment" if request.args.get("dl") else "inline"}; filename="{safe}"'
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "sandbox"          # a PDF opens on its own, never as part of the portal
        return resp

    @tt.get("/h/<hid>/print")
    @auth.portal_required("tabletop")
    def hero_print(hid):
        """The sheet on paper: every field in the official sheet's order and wording, for a player on pencil and paper."""
        if not table.ready:
            abort(503)
        try:
            h = heroes.view(hid)
        except tabletop_mod.NotFound:
            abort(404)
        return render_template("tabletop_print.html", h=h, s=h["sheet"], traits=tabletop_mod.TRAITS)

    app.register_blueprint(tt)

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
                # The newest COMPLETE snapshot of this boot: the one still being written has no readings or
                # pressure lines yet and would blank them (and raise "watchdog not reporting" warnings) for its seconds.
                snap = diagnostics.current_snapshot(vm.serial_output(2), status.started_at)
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
            buried=[m.id[3:] for m in modules_mod.MODULES if m.id.startswith("me-") and m.id in app.extensions["modules"].retired()],   # panes the Graveyard retired
            codes=diagnostics.join_codes(snap), players=diagnostics.players_online(snap), res=res,
            warnings=warnings,
            session_cost=spend.session_cost(status.machine_type, status.minutes_up()) if status else None,
            friends=friends_list, chatter=discord_chatter(env),
            discord_ready=bool(app.config["DISCORD_BOT_TOKEN"]),
            experiments=experiments.EXPERIMENTS, hidden=experiments.hidden(vm), mentat_tools=mentat_tools,
            home=home.status(), music=home.music(), core=core, services=SERVICES,
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
            if str(body.get("brain") or "core") == "core":
                pics.touch("core")                          # the agent thinking on Skippy Core: patient jobs there wait
            task, again = workshop.send_once(me(), str(body.get("conv") or ""), message, str(body.get("project") or ""), body.get("images"),
                                             brain=str(body.get("brain") or "core"), client_id=str(body.get("client_id") or ""))
        except workshop_mod.Busy as exc:
            return jsonify(error=str(exc)), 409
        except workshop_mod.NotReady as exc:
            return jsonify(error=str(exc)), 503
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if not again:
            print(json.dumps({"event": "workshop", "user": me(), "task": task, "chars": len(message), "images": len(body.get("images") or [])}), flush=True)  # never the text
        return jsonify(task=task, again=again)

    @mine.get("/workshop/events")
    @workshop_call
    def workshop_events():
        return jsonify(workshop.read(me(), request.args.get("task", ""), request.args.get("after", type=int, default=0)))

    @mine.post("/workshop/stop")
    @workshop_call
    def workshop_stop():
        body = request.get_json(silent=True) or {}
        return jsonify(ok=workshop.stop(me(), str(body.get("task") or "")))

    @mine.post("/workshop/resync")
    @workshop_call
    def workshop_resync():
        """The chat's "Check again" (also sent by itself when a run goes quiet): wake skippy-pc's reconciler."""
        body = request.get_json(silent=True) or {}
        return jsonify(workshop.resync(me(), str(body.get("task") or "")))

    @mine.post("/workshop/history")
    @workshop_call
    def workshop_history():
        """Ask skippy-pc for the Conversations list (no `conv`) or one conversation's turns (`conv`). Answers a job id at
        once; the page polls /workshop/history/result for the answer (skippy-pc picks jobs up within a few seconds)."""
        body = request.get_json(silent=True) or {}
        conv = body.get("conv")
        if not workshop.ready():
            return jsonify(error="skippy-pc's workshop isn't answering, so the conversations can't be fetched now."), 503
        try:
            job = workshop.ask_history(me(), None if conv is None else str(conv))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(job=job)

    @mine.get("/workshop/image")
    @auth.admin_required
    def workshop_image():
        """A screenshot the private Skippy attached (workshop.py image_event). An <img> can't send the X-Workshop header,
        so this GET is guarded by the admin login and the owner check only: it changes nothing and serves only bytes
        that were checked to BE a PNG or JPEG, as that type, with nosniff and a CSP that allows nothing."""
        found = workshop.image(me(), request.args.get("task", ""), request.args.get("n", type=int, default=-1))
        if found is None:
            abort(404)
        mime, data = found
        resp = app.response_class(data, mimetype=mime)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        ext = {"image/png": "png", "image/jpeg": "jpg", "model/gltf-binary": "glb", "audio/mpeg": "mp3", "audio/wav": "wav",
               "audio/ogg": "ogg", "audio/flac": "flac", "video/mp4": "mp4", "audio/midi": "mid", "application/zip": "zip"}.get(mime, "bin")
        how = "attachment" if mime in workshop_mod.SAVE_ONLY else "inline"     # MIDI and zips are saved; the rest shows in the chat
        resp.headers["Content-Disposition"] = f"{how}; filename=skippy-{request.args.get('n', type=int, default=0)}.{ext}"
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @mine.post("/workshop/notify")
    @workshop_call
    def workshop_notify():
        """The card's switch: a Discord DM when a run ends, on or off (default on; kept in VM metadata)."""
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("on"), bool):
            return jsonify(error="on must be true or false"), 400
        return jsonify(on=notifier.set_enabled(me(), body["on"]), configured=notifier.configured)

    @mine.get("/workshop/ship")
    @workshop_call
    def workshop_ship():
        """The ship card's poll: how the commit a run pushed is doing (ship.py). Only for a commit one of the asker's runs
        said it pushed: anything else is a 404, so this can't be used to ask GitHub about arbitrary commits."""
        found = workshop.ship(me(), request.args.get("sha", ""))
        if found is None:
            return jsonify(error="unknown commit"), 404
        return jsonify(ships.status(found, buildinfo.info()["commit"]))

    @mine.post("/workshop/folder")
    @workshop_call
    def workshop_new_folder():
        """New folder: skippy-pc makes D:/Aaron/development/<name>, its git repo and the private GitHub repo. A job id at
        once; the page polls /workshop/folder/result."""
        if not workshop.ready():
            return jsonify(error="skippy-pc's workshop isn't answering, so no folder can be made now."), 503
        try:
            return jsonify(job=workshop.new_folder(me(), str((request.get_json(silent=True) or {}).get("name") or "")))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @mine.get("/workshop/folder/result")
    @workshop_call
    def workshop_new_folder_result():
        return jsonify(workshop.new_folder_result(me(), request.args.get("job", "")))

    @mine.get("/workshop/history/result")
    @workshop_call
    def workshop_history_result():
        return jsonify(workshop.history_result(me(), request.args.get("job", "")))

    @mine.post("/mcp")
    @auth.admin_required
    def mcp_ask():
        """The Captain's private MCP servers: the list (no `name`) or a handshake with one (`name`), both read on skippy-pc, where his
        Claude config lives. Answers a job id at once; /me/mcp/result is polled. Only the Captain, and nothing but a name from his own list."""
        if not home.online():
            return jsonify(error="skippy-pc isn't reporting, so its MCP servers can't be read right now."), 503
        name = (request.get_json(silent=True) or {}).get("name")
        if name is None:
            return jsonify(job=home.start("mcp_list", me(), ttl=60))
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,60}", name):
            return jsonify(error="Bad server name."), 400
        return jsonify(job=home.start("mcp_test", me(), ttl=90, name=name))

    @mine.get("/mcp/result")
    @auth.admin_required
    def mcp_result():
        job = request.args.get("job", "")
        if not re.fullmatch(r"[a-f0-9]{8,32}", job) or home.owner_of(job) != me():
            return jsonify(state="unknown")
        return jsonify(home.peek(job))

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
        return redirect(url_for("experiments.index" if request.form.get("next") == "experiments" else "me.index"))

    app.register_blueprint(mine)

    # ── browser games: played in a tab, public like the experiments (play.py) ──
    @app.get("/play")
    def play_index():
        # Star Control Zero is a card that says "coming soon" until the image carries its web build.
        games = [dataclasses.replace(g, endpoint="starcontrol.page")
                 if g.slug == "star-control-zero" and starcontrol.bundled() else g for g in play.GAMES]
        # Doom Loop is a card that says "coming soon" until the dev channel holds a build.
        games = [dataclasses.replace(g, endpoint="doomloop.page")
                 if g.slug == "doom-loop" and doomloop.bundled() else g for g in games]
        return render_template("play.html", games=games)

    @app.get("/play/orion-trail")
    def orion_trail():
        """Orion Trail: one self-contained page (static/orion-trail/, vendored), framed under the app bar. Public, like
        the other Browser Games pages."""
        return render_template("orion_trail.html")

    # ── experiments: public showcase (no sign-in) ──────────────────────────────
    lab = Blueprint("experiments", __name__, url_prefix="/experiments")

    @lab.get("")
    def index():
        manage = dict(manage=experiments.EXPERIMENTS, hidden_slugs=experiments.hidden(vm)) if (auth.current_user() and auth.is_admin(auth.current_user()["email"])) else {}   # the Captain's show/hide, on the public page only for him
        return render_template("experiments.html", bench=experiments.benchmark(), **manage,
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
        return render_template("benchmark.html", bench=experiments.benchmark(), distill=experiments.distillation())

    @lab.get("/minebench")
    def minebench():
        return render_template("minebench.html", runs=experiments.MINEBENCH_RUNS)

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

    # ── Star Control Zero: the Captain's game in the browser (starcontrol.py; every signed-in person) ────
    scz = Blueprint("starcontrol", __name__, url_prefix="/starcontrol")

    @scz.get("/")
    @auth.login_required
    def page():
        """The game's own page with the portal's app bar on top. The trailing slash matters: the page asks
        for its files by relative path, so they resolve under /starcontrol/."""
        if request.args:                                   # nothing here takes a query string
            return redirect(url_for("starcontrol.page"))
        if not starcontrol.bundled():
            return render_template("starcontrol.html"), 503
        html = (starcontrol.game_dir() / "index.html").read_text(encoding="utf-8")
        # The tab icon is the portal's own (public) one: browsers may ask for an icon without the session
        # cookie, and the game's files are behind the sign-in.
        html = html.replace('href="favicon.png"', f'href="{url_for("static", filename="favicon-32.png")}"', 1)
        resp = app.response_class(html.replace("</body>", starcontrol.APP_BAR_SNIPPET + "</body>", 1))
        resp.headers["Cache-Control"] = "no-cache"         # a new release shows on the next visit
        return resp

    @scz.get("/version")
    def version():
        """Which build is bundled (public: a build id and sizes, nothing private). The deploy's smoke test
        reads it to prove the game shipped with the image."""
        info = starcontrol.version()
        resp = jsonify(bundled=starcontrol.bundled(), **{k: info[k] for k in
                       ("build", "commit", "built_at", "bundle_bytes", "bundle_parts", "music_tracks", "music_bytes")
                       if k in info})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @scz.get("/<path:path>")
    @auth.login_required
    def game_file(path):
        """The game's files: its page, the bundle parts and the music. send_from_directory refuses any path
        that leaves the folder and answers Range requests, which the music streams with. The bundle parts
        carry the build id in their query string, so they can be cached for a long time; the rest is short."""
        pinned = "web.tar.gz.part" in path and request.args.get("v") == starcontrol.version().get("build")
        return send_from_directory(starcontrol.game_dir(), path, mimetype=starcontrol.mimetype_for(path),
                                   max_age=31536000 if pinned else 3600)

    app.register_blueprint(scz)

    # ── Doom Loop: the Captain's work-in-progress game, a dev channel read from GCS (doomloop.py; every signed-in person) ──
    dl = Blueprint("doomloop", __name__, url_prefix="/doomloop")

    @dl.get("/")
    @auth.login_required
    def page():
        """The newest build's page with the portal's app bar and dev badge on top. The trailing slash matters: the
        page asks for its files by relative path, so they resolve under /doomloop/."""
        if request.args:                                   # nothing here takes a query string
            return redirect(url_for("doomloop.page"))
        info = doomloop.version()
        raw = doomloop.read("index.html") if info else None
        if raw is None:
            return render_template("doomloop.html"), 503
        page_html = raw.decode("utf-8", errors="replace")
        resp = app.response_class(page_html.replace("</body>", doomloop.app_bar(info) + "</body>", 1))
        resp.headers["Cache-Control"] = "no-cache"         # a new build shows on the next visit
        return resp

    @dl.get("/version")
    def version():
        """Which build the dev channel holds (public: a build id and a time, nothing private)."""
        info = doomloop.version()
        resp = jsonify(bundled=bool(info), **info)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @dl.get("/<path:path>")
    @auth.login_required
    def game_file(path):
        """The build's files, read from the bucket. A path that leaves the folder is a 404 before storage is asked.
        Every file is revalidated (an ETag), so a new build is picked up without clearing the browser cache."""
        if doomloop.clean_path(path) is None:
            abort(404)
        data = doomloop.read(path)
        if data is None:
            abort(404)
        resp = app.response_class(data, mimetype=doomloop.mimetype_for(path))
        resp.headers["Cache-Control"] = "no-cache"
        resp.add_etag()
        return resp.make_conditional(request)

    app.register_blueprint(dl)

    # ── Shifting Prophesy: two AI models build the same game, one prompt at a time (prophesy.py; every signed-in person) ──
    sp = Blueprint("prophesy", __name__, url_prefix="/shifting-prophesy")

    def arm_or_404(arm):
        if arm not in prophesy.ARMS:
            abort(404)
        return prophesy.ARMS[arm]

    def bucket_file(folder, path):
        """A file from one folder of the experiment's bucket, revalidated by ETag. A path that leaves the folder is a 404
        before storage is asked."""
        if prophesy.clean_path(path) is None:
            abort(404)
        data = prophesy.read(folder, path)
        if data is None:
            abort(404)
        resp = app.response_class(data, mimetype=prophesy.mimetype_for(path))
        resp.headers["Cache-Control"] = "no-cache"
        resp.add_etag()
        return resp.make_conditional(request)

    @sp.get("/")
    @auth.login_required
    def board():
        """Both arms' live builds, then one page per turn (turns() is newest first; the template lays them oldest to newest and
        opens on the newest), each with every attempt at it."""
        rows = prophesy.turns()
        versions = {a: prophesy.version(a) for a in prophesy.ARMS}
        return render_template("prophesy.html", arms=prophesy.ARMS, versions=versions, turns=rows, totals=prophesy.totals(rows),
                               short={a: prophesy.short_commit(v) for a, v in versions.items()},
                               check_order=prophesy.CHECKS, profile_order=prophesy.PROFILE, legend=prophesy.legend())

    @sp.get("/turns/<path:path>")
    @auth.login_required
    def record_file(path):
        """A file of the turn record: a screenshot, a reply, the action lines, a report."""
        return bucket_file(prophesy.RECORD, path)

    @sp.get("/<arm>/")
    @auth.login_required
    def game(arm):
        """An arm's newest build, framed under the portal's app bar; the query string (?seed=...) goes through to it."""
        info_arm = arm_or_404(arm)
        info = prophesy.version(arm)
        if not info:
            return render_template("prophesy_nobuild.html", arm=info_arm), 503
        query = request.query_string.decode("utf-8", errors="replace")
        resp = app.response_class(render_template("prophesy_game.html", arm=info_arm, commit=prophesy.short_commit(info),
                                                  src="index.html" + ("?" + query if query else "")))
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @sp.get("/<arm>/version")
    def arm_version(arm):
        """Which build an arm's folder holds (public: a build id and a time; the referee's runner reads it)."""
        arm_or_404(arm)
        info = prophesy.version(arm)
        resp = jsonify(bundled=bool(info), **info)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @sp.get("/<arm>/<path:path>")
    @auth.login_required
    def game_file(arm, path):
        """A file of an arm's build, read from the bucket."""
        arm_or_404(arm)
        return bucket_file(arm, path)

    app.register_blueprint(sp)

    @app.put("/home/prophesy/<folder>/<path:path>")
    def home_prophesy_put(folder, path):
        """skippy-pc (the cruise's referee) uploading one file of a build or of the turn record (prophesy.py). The code is
        local to skippy-pc (Platinum §23), so this, not a CI, is how a turn reaches the board."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        request.max_content_length = prophesy.MAX_FILE
        try:
            prophesy.put(folder, path, request.get_data(cache=False))
        except ValueError:
            abort(404)
        except prophesy.tabletop.StorageMissing as exc:
            return jsonify(error=str(exc)), 503
        return jsonify(ok=True)

    @app.post("/home/prophesy/<folder>/finish")
    def home_prophesy_finish(folder):
        """After a build's files are up: drop the previous build's leftovers ({"keep": [paths]})."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        keep = (request.get_json(silent=True) or {}).get("keep")
        if not isinstance(keep, list):
            abort(400)
        try:
            gone = prophesy.finish(folder, keep)
        except ValueError:
            abort(404)
        except prophesy.tabletop.StorageMissing as exc:
            return jsonify(error=str(exc)), 503
        return jsonify(ok=True, deleted=gone)

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

    class PatientJobs:
        """make_image's and make_music's view of the job queue (patient.py): the job waits until its machine is quiet, may take
        hours, and is written down in the bucket, which outlives the portal and the request that asked for it."""
        def __init__(self, email):
            self.email = email

        def start(self, kind, owner="", **payload):
            box = box_for(kind, payload.get("name", ""))
            job = home.start(kind, owner, ttl=patient_mod.JOB_TTL, box=box, **payload)
            try:
                pics.expect(job, self.email, kind, box, payload)
            except Exception as exc:  # noqa: BLE001 — no bucket: it still comes, it just won't outlive a restart
                print(json.dumps({"event": "patient_not_written_down", "why": str(exc)[:200]}), flush=True)
            return job

        def owner_of(self, job):
            return home.owner_of(job)

        def wait_result(self, job, wait):
            return home.wait_result(job, wait)

    def agent_picture(owner: str, picture_id: str, prompt: str, tier: str = "") -> None:
        """The private Skippy wrote `SKIPPY_PICTURE: ...` (or `SKIPPY_PICTURE_HQ: ...` for a quality one; services/claude-bridge): a
        patient picture job for the Captain under the id the bridge gave it, so his chat's card (and a reopened conversation) finds
        it. Once: a push that repeats it changes nothing."""
        kind = "make_image_hq" if tier == "hq" else "make_image"
        if not home.can(kind) or home.has(picture_id) or pics.state(picture_id, owner, True) != "unknown":
            return
        PatientJobs(owner).start(kind, owner, prompt=prompt, width=1024, height=1024, _id=picture_id)
        print(json.dumps({"event": "agent_picture_queued", "job": picture_id}), flush=True)
    workshop.on_picture = agent_picture

    def popup_payload(entry: dict, args: dict, given: dict | None) -> dict:
        """A pop-up job as the reporter reads it (services/home-reporter popup_job): its name, its arguments as one JSON string (the
        patient queue writes down plain values only, so a restarted portal can put it back in line), and the file it takes, if any."""
        summary = " ".join(str(v) for v in args.values() if isinstance(v, (str, int, float)) and not isinstance(v, bool))
        out = {"name": entry["name"], "args": json.dumps(args)[:20_000], "prompt": (entry["name"] + ": " + summary)[:300]}
        if given and given.get("ref"):                  # a big file kept at home: its id and whose it is (the reporter checks both)
            out.update(input_ref=given["ref"], input_owner=chat_history.owner_id(given["owner"]), input_type=given["type"],
                       input_name=str(given.get("name") or "file")[:80])
        elif given:
            out.update(input_b64=given["b64"], input_type=given["type"], input_name=str(given.get("name") or "file")[:80])
        return out

    def agent_popup(owner: str, job_id: str, name: str, args: dict, given: dict | None = None) -> None:
        """The private Skippy wrote `SKIPPY_POPUP: <name> <json args>` (services/claude-bridge): a patient pop-up job for the Captain under
        the bridge's id, like agent_picture. Only a pop-up that is registered and ready at home; once, however often it is pushed."""
        entry = popups.get(name)
        if not entry or not home.can(name) or home.has(job_id) or pics.state(job_id, owner, True) != "unknown":
            return
        if given and given.get("ref"):
            given = {**given, "owner": owner}             # the bridge kept it under the Captain's id (workshop_engine._popup_file)
        PatientJobs(owner).start("popup", owner, **popup_payload(entry, args, given), _id=job_id)
        print(json.dumps({"event": "agent_popup_queued", "job": job_id, "popup": name}), flush=True)
    workshop.on_popup = agent_popup

    def chat_mentat():
        """The chat's physics: skippy-core's Mentat, straight over its Funnel (MENTAT_URL), when it is up and thinking with Qwen."""
        m = app.extensions.get("mentat")
        return m if m is not None and m.ready() else None

    def tools_for(user, trace: str = "") -> chat_tools.ChatTools:
        """Green Skippy's tools: the guest chat, the same set for everyone, the Captain included (his call, 2026-10-30: the tool
        list is green's security layer; the house, the car, songs, the VM's restart and logs and Skippy Memory are purple's).
        Only the Captain's quotas differ: his pictures and pop-ups never run out."""
        email = user["email"]
        return chat_tools.ChatTools(vm, is_admin=False, visible=auth.visible_games(email),
                                    who=email, trace=trace,
                                    music=home.music if auth.has_portal(email, "audio") else None,
                                    home_mentat=chat_mentat(),
                                    home_house=home if home.can("house") else None,
                                    home_car=home if home.can("car") else None,
                                    home_music=PatientJobs(email) if home.can("make_music") else None,
                                    home_image=PatientJobs(email) if home.can("make_image") or home.can("make_image_hq") else None,
                                    image_tiers=tuple(k for k in ("make_image", "make_image_hq") if home.can(k)),
                                    popups=[e for n, e in popups.load().items() if e["chat"] and home.can(n)],
                                    home_popup=PatientJobs(email), popup_payload=popup_payload,
                                    popup_allow=(lambda: True) if auth.is_admin(email) else
                                                (lambda: limiter.allow("popup:" + email, POPUPS_PER_DAY, 86400)),
                                    image_allow=(lambda: True) if auth.is_admin(email) else
                                                (lambda: limiter.allow("image:" + email, IMAGES_PER_DAY, 86400)))

    app.extensions["chat_tools_for"] = tools_for      # for the tests: what a given person's tools are, without a model in the way

    @talk.get("/badges")
    @skippy_required
    def badges():
        """The chat's direct-verb badge panel for this person."""
        home.touch_chat()
        return jsonify(tools_for(auth.current_user()).badges())

    @talk.post("/popup-in")
    @skippy_required
    def popup_in_pass():
        """A pass to send ONE big file (a whole track) straight to skippy-pc's popup-gate for a pop-up (popup_in.py): only for someone
        a ready pop-up that takes files is offered to; guests' bytes are counted against the day."""
        email, captain = me(), auth.is_admin(me())
        takers = [e for n, e in popups.load().items() if e["media_in"] and e["chat"] and home.can(n) and tool_policy.popup_allowed(e, captain)]
        if not takers:
            out = {"ok": False, "why": "No tool that takes files is ready for you right now."}
        else:
            out = big_files.grant(email, captain, (request.get_json(silent=True) or {}).get("bytes"))
        resp = jsonify(out)
        resp.headers["Cache-Control"] = "no-store"         # a pass is a credential: never from a cache
        return resp

    @talk.get("/voice/gate")
    @skippy_required
    def voice_gate():
        """Where the voice service is and a pass for it, in the voice this person gets in this chat mode (voice_mod.voice_for: the
        server's call; ?mode= is only a hint). Guests: the stock voice made at home. The Captain: the local Skippy clone (Private),
        ElevenLabs (Auto), stock (Public). The cloned voices are personal use only, so nobody else is ever signed one."""
        pics.touch("pc")                                     # voice is skippy-pc's card at work: patient jobs wait (patient.py)
        resp = jsonify(voice.gate(voice=voice_mod.voice_for(auth.is_admin(me()), request.args.get("mode"))))
        resp.headers["Cache-Control"] = "no-store"          # a pass is a credential: never from a cache
        return resp

    def chat_page():
        captain = auth.is_admin(me())
        # Each person's chats are kept in their browser under their own key: a one-way id of the signed-in address (never the
        # address itself), so two people on one device (the Captain and a guest) never see each other's chats.
        owner = chat_history.owner_id(me())
        retired = app.extensions["modules"].retired()
        return render_template("front.html", captain=captain, chat_modes=mode_views(captain),
                               chat_buried=[k for k in modules_mod.CHAT_KEYS if "key-" + k in retired],   # keys the Graveyard retired
                               chat_owner=owner,
                               **(workshop_context() if captain else {}))

    @talk.get("")
    @skippy_required
    def index():
        """The Skippy app: the public brain (Haiku and Mentat: no tools that write or run anything) for everyone with the chat grant, and for
        the Captain alone also the agent (Claude Code on skippy-pc, templates/_workshop.html), whose endpoints each check for the Captain again."""
        return chat_page()

    def log_mentat_gap(user, question, out, how, save):
        """A physics question Mentat could not model: auto-file it. One structured line in Cloud Run's logs
        (`{"event": "mentat_gap", ...}`) and a `mentat_gap` event in the activity record that skippy-pc collects, so a
        later session can fix what sigma-ground is missing. The question and Mentat's reason are kept only for the Captain
        or someone who ticked "Allow us to save your questions"; for anyone else just that a gap happened and how."""
        gap = chat_tools.mentat_gap(out)
        if gap is None:
            return None
        keep = auth.is_admin(user["email"]) or save
        event = {"how": how, "route": gap["route"][:60], "tag": gap["tag"][:40]}
        if keep:
            event.update(question=question[:300], reason=gap["reason"][:400])
        print(json.dumps({"event": "mentat_gap", "user": user["email"], **event}), flush=True)
        telemetry_web.record_gap(event)
        return {"logged": True, "reason": gap["reason"][:400] if keep else None}

    def raw_mentat(user, message, question, save, follow=None, trace=""):
        """The chat's "Barney Style" switch, when it is OFF (the default): the question goes to Mentat as typed and Mentat's own answer comes back as it
        left it, with no Haiku on either side (it doesn't read the question first and doesn't put the answer in Skippy's
        voice). It is for seeing what Haiku adds or loses. Same Mentat, same rights (the person must be allowed the physics
        tool) and the same rate limit as a normal message; the answer is not added to the conversation history (the page
        keeps it out), so a later message to Haiku isn't coloured by it."""
        if not question or question.startswith("(the user chose"):
            return jsonify(error="Type the physics question to send to Mentat."), 400
        tools = tools_for(user, trace)
        args = follow if follow else {"question": question}       # a yes to Mentat's suggestion: its follow-up, as it made it
        try:
            out = tools.run("ask_mentat", args)
        except chat_tools.ToolError as exc:
            return jsonify(error=str(exc)), 503
        gap = log_mentat_gap(user, question, out, "straight to Mentat", save)
        if gap:                                             # Mentat has no physics for this: say so, and it's logged
            print(json.dumps({"event": "chat", "user": user["email"], "model": "none (raw Mentat)", "tools": ["ask_mentat"],
                              "mentat_available": True, "mentat_where": tools.mentat_where, "gap": True}), flush=True)
            return jsonify(reply="failed due to incompetence", tools=["ask_mentat"], mentat_available=True,
                           mentat_where=tools.mentat_where, media=[], raw=True, gap=gap)
        try:
            text = json.dumps(json.loads(out), indent=2, ensure_ascii=False)
        except ValueError:                                  # a very long answer is cut at 20 000 characters
            text = out
        print(json.dumps({"event": "chat", "user": user["email"], "model": "none (raw Mentat)",
                          "tools": ["ask_mentat"], "mentat_available": True, "mentat_where": tools.mentat_where}), flush=True)
        if save:
            telemetry_web.record_chat(message, text, "none (raw Mentat)", [("ask_mentat", args, out)],
                                      forced="ask_mentat")
        card = mentat_card(args["question"], out)
        return jsonify(reply=text, tools=["ask_mentat"], mentat_available=True, mentat_where=tools.mentat_where,
                       media=[], raw=True, mentat=[card] if card else [])

    def mentat_first(user, message, question, history, save, follow=None, trace="", page=None):
        """/mentat (or /physics) with a question: straight to Mentat, no model reading it first (Mentat has its own: Qwen and the
        router, on skippy-core), and THEN Skippy Core puts Mentat's answer Barney Style, with no tools. Whatever mode the chat is
        in (the agent's run queue never holds it up). Core down: Mentat's own answer, as the raw path gives it."""
        tools = tools_for(user, trace)
        args = follow if follow else {"question": question}
        try:
            out = tools.run("ask_mentat", args)
        except chat_tools.ToolError as exc:
            return jsonify(error=str(exc)), 503
        gap = log_mentat_gap(user, question, out, "/mentat, then Barney Style", save)
        card = mentat_card(args["question"], out)
        base = dict(tools=["ask_mentat"], mentat_available=True, mentat_where=tools.mentat_where, media=[],
                    mentat=[card] if card else [])
        if gap:
            return jsonify(reply="failed due to incompetence", raw=True, gap=gap, **base)
        brain = brains.BRAINS[brains.BARNEY]
        text, model = None, None
        if brain_ready(brain):
            prompt = (chat.system_prompt(user["name"], page=page,
                                         games=[GAMES[g].name for g in auth.visible_games(user["email"])])
                      + chat.language_instruction() + chat.barney_instruction())
            convo = chat.build_messages(history, question) + [
                {"role": "assistant", "content": out[:6000]}, {"role": "user", "content": chat.BARNEY_PHRASE}]
            model = brains.model_of(brain)
            try:
                text = chat.strip_sentinels((bot_override or brains.chat_for(brain)).reply(prompt, convo, tools=None, run_tool=None,
                                                                                             model=model))
            except chat.ChatError as exc:
                print(json.dumps({"event": "mentat_first_barney_failed", "error": str(exc)[:200]}), flush=True)
        print(json.dumps({"event": "chat", "user": user["email"], "model": model or "none (raw Mentat)", "tools": ["ask_mentat"],
                          "mentat_available": True, "mentat_where": tools.mentat_where, "mentat_first": True}), flush=True)
        if text is None:                                    # Skippy Core is down: Mentat's own words, not nothing
            try:
                text = json.dumps(json.loads(out), indent=2, ensure_ascii=False)
            except ValueError:
                text = out
            return jsonify(reply=text, raw=True, **base)
        if save:
            telemetry_web.record_chat(message, text, model, [("ask_mentat", args, out)], forced="ask_mentat")
        return jsonify(reply=text, brain=brain.id, barney=True, **base)

    def mentat_card(question: str, out: str) -> dict | None:
        """What the page's ⚛ card shows for one Mentat answer: the result, and the proof (steps with sources) as Mentat
        gave it. Mentat's own JSON, trimmed to the fields a reader needs; None when the tool answered with an error."""
        try:
            d = json.loads(out)
        except ValueError:
            return None
        if not isinstance(d, dict):
            return None
        steps = []
        for link in ((d.get("inputs") or {}).get("chain") or [])[:6] if isinstance(d.get("inputs"), dict) else []:
            for s in (link.get("steps") or [])[:12] + ([link["result"]] if isinstance(link.get("result"), dict) else []):
                if isinstance(s, dict):
                    steps.append({k: str(s.get(k) or "")[:160] for k in ("label", "value", "units", "formula", "source")})
        keep = ("value", "units", "display", "formula", "interpreted_as", "assumed", "notes", "source", "uncertainty",
                "provenance_tag", "interpreter", "error")
        card = {"question": question[:300], **{k: d.get(k) for k in keep}, "steps": steps[:30]}
        clar = chat_tools.mentat_suggestion(d)
        if clar:     # a suggestion, not an answer: the card shows what Mentat would assume, and the page keeps the follow-up
            card["suggestion"] = {"sentence": str(clar.get("sentence") or "")[:600],
                                  "suggested": clar.get("suggested") or {},
                                  "follow_up": chat_tools.clean_follow_up(clar.get("follow_up"))}
        return card

    def shape_files(files, text, brain, bot):
        """A message's files for Skippy Core: its chat slot is text-only, so its vision slot describes each picture first
        (guests can show it things too), and a PDF or text file goes in as its text."""
        def describe(f):
            try:
                return bot.reply("You describe pictures precisely.", [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": f.mime, "data": f.b64}},
                    {"type": "text", "text": attachments_mod.DESCRIBE_PROMPT}]}], model="vision")
            except chat.ChatError as exc:
                return f"(the vision model couldn't look at it: {exc})"
        return attachments_mod.text_for(files, text, describe)

    @talk.post("/send")
    @skippy_required
    def send():
        body = request.get_json(silent=True) or {}
        message = (body.get("message") or "").strip()
        if not message:
            return jsonify(error="Say something."), 400
        user = auth.current_user()
        admin = auth.is_admin(user["email"])
        if not admin:
            if not limiter.allow("chat:" + user["email"], CHAT_PER_HOUR, 3600):
                return jsonify(error=f"That's {CHAT_PER_HOUR} messages this hour; Skippy needs a breather. "
                                     "Try again in a bit."), 429
        home.touch_chat()
        pics.touch("core")                                    # Core answers and Mentat thinks, both on skippy-core: patient jobs there wait (patient.py)
        trace = "chat-" + secrets.token_hex(8)        # this turn's id, kept with Mentat's research record of it
        # The person agreed to Mentat's suggested inputs: the page sends back the follow-up Mentat made, and the portal
        # asks Mentat with exactly those values (no brain retyping numbers). Anything but a plain yes ignores it.
        follow = chat_tools.clean_follow_up(body.get("mentat_follow_up")) if chat.is_yes(message) else None
        force, rest = chat_tools.parse_verb(message)
        if force and not rest:
            rest = {"ask_mentat": "(the user chose /physics with no question: ask what to compute)",
                    "make_music": "(the user chose /music with no description: ask what kind of music to make)"
                    }.get(force, "Go ahead.")
        if body.get("raw") is True:                       # Mentat straight, no model either side (kept for anyone who asks for it)
            return raw_mentat(user, message, rest, body.get("save") is True, follow, trace)
        if force == "ask_mentat" and not rest.startswith("(the user chose") and not chat.wants_barney(rest):
            return mentat_first(user, message, rest, body.get("history"), body.get("save") is True, follow, trace,
                                str(body.get("page") or "")[:100] or None)
        # Which brain: the server decides. Guests always get Skippy Core; the Captain gets his pick. Barney Style (the
        # page's key, or the phrase) always runs on Skippy Core with no tools, whoever asks.
        barney = body.get("barney") is True or chat.wants_barney(rest)
        try:
            brain = brains.BRAINS[brains.BARNEY] if barney else brains.pick(body.get("mode") or body.get("brain"), admin)
        except PermissionError as exc:
            return jsonify(error=str(exc)), 403
        if not brain_ready(brain) and not barney:
            # Green's only brain is down (or not set up yet): Mentat straight, as before Skippy Core existed, never a paid
            # model in its place.
            return raw_mentat(user, message, rest, body.get("save") is True, follow, trace)
        if not brain_ready(brain):
            return jsonify(error=f"{brain.label} isn't available right now"
                                 + (": skippy-core may be offline." if brain.id == "core" else ".")), 503
        try:
            files = attachments_mod.clean(body.get("files"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        bot = bot_override or brains.chat_for(brain)
        try:
            tools = tools_for(user, trace)
            tools.files = files               # a pop-up that takes a picture or a sound takes it from this message (popups.py)
            model = brains.model_of(brain)
            page = str(body.get("page") or "")[:100] or None
            prompt = chat.system_prompt(user["name"], page=page,
                                        games=[GAMES[g].name for g in auth.visible_games(user["email"])])
            used: list[str] = []
            calls: list[tuple] = []       # (tool, arguments, result): kept only if this person allowed saving (below)
            cards: list[dict] = []        # every Mentat answer in this reply, for the page's ⚛ card

            def run_tool(name, args):
                used.append(name)
                calls.append((name, args, ""))
                out = tools.run(name, args)
                calls[-1] = (name, args, out)
                if name == "ask_mentat":                  # Mentat had no physics for it: file the bug
                    q = str(args.get("question") or "")
                    log_mentat_gap(user, q, out, f"through {brain.label}", body.get("save") is True)
                    card = mentat_card(q, out)
                    if card:
                        cards.append(card)
                return out

            schemas = tools.schemas()
            if force not in tools.available():
                force = None                     # a direct verb (/status, /image) forces only a tool this person has: the same for all
            if force in ("ask_mentat", "make_music") and rest.startswith("(the user chose"):
                force = None                     # nothing to act on yet: let Skippy ask
            prompt += chat.language_instruction()
            if barney:
                # Barney Style: Skippy Core explains the whole conversation (Mentat's answers included) plainly,
                # grounded in what Mentat found. No tools: nothing new is computed or looked up.
                prompt += chat.barney_instruction()
                schemas, force = None, None
                rest = rest if chat.wants_barney(rest) else chat.BARNEY_PHRASE
            convo = chat.build_messages(body.get("history"), rest)
            if files:                     # the message's files, shaped for this brain (attachments.py)
                convo[-1] = {"role": "user", "content": shape_files(files, rest, brain, bot)}
            if follow and schemas and any(t.get("name") == "ask_mentat" for t in schemas):
                # A yes to Mentat's suggestion: the portal asks Mentat with the agreed values itself, and the brain only
                # explains the answer it is handed (no tools), as when a brain ignores a forced tool below.
                try:
                    out = run_tool("ask_mentat", follow)
                except chat_tools.ToolError as exc:
                    out = json.dumps({"error": str(exc)})
                convo[-1] = {"role": "user", "content": (
                    f"{rest}\n\n[The user agreed to the inputs Mentat suggested, so Mentat was asked again with them "
                    f"({json.dumps(follow['given'])}) and answered (its own JSON, unedited): {out[:6000]}]\n"
                    "Answer from Mentat's result only, as the physics section of your instructions says.")}
                text = bot.reply(prompt, convo, tools=None, run_tool=None, model=model)
            else:
                text = bot.reply(prompt, convo, tools=schemas, run_tool=run_tool, model=model, force_tool=force)
            if (schemas and "ask_mentat" not in used and chat.claims_mentat(text)
                    and any(t.get("name") == "ask_mentat" for t in schemas)):
                # The brain SAID Mentat answered but never called it (a local model writing the tool call as text, then
                # inventing the result and its sources). That answer is thrown away: the turn runs again with Mentat
                # forced, so every number shown comes from Mentat itself.
                print(json.dumps({"event": "mentat_claim_without_call", "brain": brain.id, "model": model}), flush=True)
                text = bot.reply(prompt, convo, tools=schemas, run_tool=run_tool, model=model, force_tool="ask_mentat")
                if "ask_mentat" not in used:
                    # Not every brain honours a forced tool (llama-swap ignores tool_choice): then the portal asks Mentat
                    # itself, with the person's own words, and the brain only explains the answer it is handed (no tools).
                    try:
                        out = run_tool("ask_mentat", {"question": rest})
                    except chat_tools.ToolError as exc:
                        out = json.dumps({"error": str(exc)})
                    handed = convo[:-1] + [{"role": "user", "content": (
                        f"{rest}\n\n[Mentat was asked this question and answered (its own JSON, unedited): {out[:6000]}]\n"
                        "Answer from Mentat's result only, as the physics section of your instructions says.")}]
                    text = bot.reply(prompt, handed, tools=None, run_tool=None, model=model)
            if (schemas and "make_image" not in used and any(t.get("name") == "make_image" for t in schemas)
                    and (chat.image_tag(text) or chat.wants_picture(rest))):
                # A picture was asked for (or the brain wrote [IMAGE: ...] as text) and make_image was never called: Skippy
                # Core does that, and nothing came back (2026-10-23). The portal draws it itself, from the tag's
                # description or the person's own words, and the picture appears under the reply as usual.
                print(json.dumps({"event": "image_without_call", "brain": brain.id, "model": model}), flush=True)
                try:
                    run_tool("make_image", {"prompt": chat.image_tag(text) or rest})   # done or drawing: the card shows it
                except chat_tools.ToolError as exc:
                    text += f"\n\n(No picture: {exc})".replace(" Say so plainly.", "").replace(" Say so, and that it resets tomorrow.", " It resets tomorrow.")
        except chat.ChatError as exc:
            return jsonify(error=str(exc)), 502
        text = chat.strip_sentinels(text)
        mentat_on = tools.mentat_ok
        where = tools.mentat_where
        # One line per reply in Cloud Run's logs: who, which brain, which tools (never the text).
        print(json.dumps({"event": "chat", "user": user["email"], "brain": brain.id, "model": model,
                          "tools": used, "mentat_available": mentat_on, "mentat_where": where}), flush=True)
        if body.get("save") is True:             # only when they ticked "Allow us to save your questions"; never by default
            telemetry_web.record_chat(message, text, model, calls, forced=force)
        return jsonify(reply=text, tools=used, mentat_available=mentat_on, mentat_where=where,
                       media=tools.media, brain=brain.id, mentat=cards, barney=barney)

    # ── The quick spoken line: in Hands-off with the agent (Private, Auto), Skippy Core's resident chat model says one line at once ──
    # The agent's turn takes a minute (2026-10-29: 64 of the 68 s he waited for "respond to me with voice" were the agent's own tool calls;
    # the voice itself started in 0.94 s). So while it works, the chat model, always loaded, answers in a sentence or two: the answer
    # itself when the conversation already holds it or it's small talk, else what is about to happen. It never claims anything was done.
    QUICK_SYSTEM = ("You are Skippy, talking out loud with the Captain. Your full self (an agent with tools) is already working on his "
                    "message and will answer properly in a minute. Right now say ONE or TWO short spoken sentences, no lists, no markdown: "
                    "if it is small talk or the conversation below already holds the answer, answer it; otherwise say plainly what you are "
                    "about to check or do. Never say you did, found, changed or fixed anything: nothing has run yet. Never invent a number.")

    @talk.post("/quick")
    @auth.admin_required
    def quick_line():
        body = request.get_json(silent=True) or {}
        message = str(body.get("message") or "").strip()[:2000]
        if not message:
            return jsonify(error="Say something."), 400
        context = body.get("context") if isinstance(body.get("context"), list) else []
        convo = [{"role": "assistant" if t.get("role") == "assistant" else "user", "content": str(t.get("text") or "")[:1500]}
                 for t in context[-6:] if isinstance(t, dict) and str(t.get("text") or "").strip()]
        while convo and convo[0]["role"] != "user":         # a conversation starts with him
            convo.pop(0)
        convo.append({"role": "user", "content": message})
        merged = []                                          # two of his in a row (a heard line, a queued message) become one turn
        for t in convo:
            if merged and merged[-1]["role"] == t["role"]:
                merged[-1]["content"] += "\n" + t["content"]
            else:
                merged.append(dict(t))
        brain = brains.BRAINS[brains.BARNEY]                # Skippy Core's chat model, no tools
        if not brain_ready(brain):
            return jsonify(say=""), 200                      # no quick line: the agent's answer still comes
        try:
            text = chat.strip_sentinels((bot_override or brains.chat_for(brain)).reply(QUICK_SYSTEM, merged, tools=None, run_tool=None,
                                                                                         model=brains.model_of(brain)))
        except chat.ChatError as exc:
            print(json.dumps({"event": "quick_line_failed", "error": str(exc)[:200]}), flush=True)
            return jsonify(say=""), 200
        return jsonify(say=" ".join(str(text or "").split())[:400])

    # ── Skippy Memory: every saved AI conversation (Grok, Claude, ...), to continue here (admin only) ──
    @talk.post("/memory")
    @auth.admin_required
    def memory_ask():
        """Ask skippy-pc for the list (no `key`) or one conversation (`key`). Answers a job id at once; the chat polls
        /chat/memory/result (skippy-pc picks jobs up within a few seconds)."""
        if not home.online():
            return jsonify(error="skippy-pc isn't reporting, so Skippy Memory can't be read right now."), 503
        key = (request.get_json(silent=True) or {}).get("key")
        if key is None:
            return jsonify(job=home.start("memory_list", me(), ttl=120))
        if not isinstance(key, str) or not re.fullmatch(r"[a-z-]{2,20}:[A-Za-z0-9_.-]{1,80}", key):
            return jsonify(error="Bad conversation key."), 400
        return jsonify(job=home.start("memory_get", me(), ttl=120, key=key))

    @talk.get("/memory/result")
    @auth.admin_required
    def memory_result():
        job = request.args.get("job", "")
        if not re.fullmatch(r"[a-f0-9]{8,32}", job) or home.owner_of(job) != me():
            return jsonify(state="unknown")
        return jsonify(home.peek(job))

    # ── Your chats, on the portal for 30 days (chat_history.py): only ever the signed-in person's own file ──
    def ask_core(system: str, text: str) -> str:
        """One quick question to Skippy Core with no tools (a chat's name): raises when Core isn't there."""
        if not brain_ready(brains.CORE):
            raise RuntimeError("Skippy Core isn't configured")
        bot = bot_override or brains.chat_for(brains.CORE)
        return bot.reply(system, [{"role": "user", "content": text}], model=brains.model_of(brains.CORE))

    chats_kept = history if history is not None else chat_history.from_env()
    if chats_kept.namer is None:
        chats_kept.namer = lambda message: chat_names.name(message, ask_core)
    chats_kept.forever = lambda email: str(email).strip().lower() in app.config["ADMIN_EMAILS"]   # the Captain's: forever; a guest's: 30 days
    app.extensions["chat_history"] = chats_kept
    housechat_mod.register(app, auth, home, me, chats_kept.store)   # the permanent Home chat (the house conversation); not one of these chats, so nothing here can expire or rename it

    # Expiry (chat_history.sweep): about once an hour, nudged by the home reporter's check-ins, Skippy Core analyses up to ten
    # expired guest chats before they are deleted; each insight is kept in the bucket and handed home to skippy-pc in the
    # report's reply (state/insights there) until the reporter says it has written it down.
    sweep_state = {"last": 0.0, "running": False, "outbox": []}

    def record_insight(owner, chat, insight):
        item = chats_kept.record_insight(owner, chat, insight)
        print(json.dumps({"event": "chat_insight", **{k: item[k] for k in ("chat", "turns")},
                          **{k: len(item[k]) for k in ("right", "wrong", "bugs", "improvements")}}), flush=True)
        sweep_state["outbox"] = (sweep_state["outbox"] + [item])[-200:]

    def sweep_now():
        try:
            keep = {chat_history.owner_id(e) for e in app.config["ADMIN_EMAILS"]}
            chats_kept.sweep(chat_history.analyse_with(ask_core), record_insight, keep_owners=keep)
        except Exception as exc:  # noqa: BLE001 — Core away, the bucket missing: try again next hour
            print(json.dumps({"event": "chat_sweep_paused", "why": str(exc)[:200]}), flush=True)
        finally:
            sweep_state["running"] = False

    def maybe_sweep():
        if sweep_state["running"] or time.time() - sweep_state["last"] < 3600 or not brain_ready(brains.CORE):
            return
        sweep_state.update(last=time.time(), running=True)
        threading.Thread(target=sweep_now, daemon=True).start()

    def insights_for_home(acked) -> list:
        """What the reply carries home: insights the reporter hasn't confirmed yet (it sends their "at"+"chat" back)."""
        done = {str(a) for a in acked} if isinstance(acked, list) else set()
        sweep_state["outbox"] = [i for i in sweep_state["outbox"] if i["at"] + "|" + i["chat"] not in done]
        return sweep_state["outbox"][:20]
    app.extensions["chat_sweep"] = (maybe_sweep, insights_for_home, sweep_state)

    def history_call(fn):
        try:
            return jsonify(fn())
        except chat_history.Refused as exc:
            return jsonify(error=str(exc)), 400
        except tabletop_mod.StorageMissing:
            return jsonify(error="The portal can't keep chats right now; they stay in this browser."), 503

    @talk.get("/history")
    @skippy_required
    def history_list():
        resp = history_call(lambda: {"chats": chats_kept.chats(me()), "keep_days": chat_history.KEEP_DAYS})
        resp = resp if isinstance(resp, tuple) else (resp, 200)
        resp[0].headers["Cache-Control"] = "private, no-store"
        return resp

    @talk.put("/history/<chat_id>")
    @skippy_required
    def history_save(chat_id):
        body = request.get_json(silent=True) or {}
        return history_call(lambda: chats_kept.save(me(), chat_id, body.get("title"), body.get("history")))

    @talk.patch("/history/<chat_id>")
    @skippy_required
    def history_update(chat_id):
        body = request.get_json(silent=True) or {}
        return history_call(lambda: chats_kept.update(me(), chat_id, body.get("title"), body.get("star")))

    @talk.delete("/history/<chat_id>")
    @skippy_required
    def history_forget(chat_id):
        return history_call(lambda: chats_kept.forget(me(), chat_id) or {"ok": True})

    @talk.get("/media/<job_id>/state")
    @skippy_required
    def media_state(job_id):
        """A picture's state for the chat's "drawing" card: done, drawing, or unknown (gone, given up on, or not yours)."""
        item = media.get(job_id)
        extra = {}
        if item and (auth.is_admin(me()) or item["owner"] == me()):
            state = "done"
        else:
            state = pics.state(job_id, me(), auth.is_admin(me()))
            if state == "waiting":                            # in line, or being made: say which, and why it waits
                meta = pics.pending(job_id) or {}
                if meta.get("kind") == "make_image_hq":
                    extra["tier"] = "hq"                        # a quality picture: the card says it may take up to an hour
                if meta.get("kind") == "popup":                 # a pop-up: the card says roughly how long its kind takes
                    entry = popups.get(str((meta.get("job") or {}).get("name") or "")) or {}
                    extra.update(popup=entry.get("name") or "pop-up", est_s=entry.get("est_seconds"))
                seen = home.peek(job_id)
                if seen.get("handed_out"):
                    state = "working"
                elif seen["state"] == "error":
                    state, extra = "failed", {**extra, "why": seen.get("error")}
                else:
                    meta = pics.pending(job_id) or {}
                    box = meta.get("box") or "pc"
                    state = "queued"
                    extra = {**extra, "why": pics.ready(box, gpu_for(box))[1] or "next in line", "box": box}
        if state == "done":                                   # what kind of card it becomes (a pop-up's type is known only now)
            ctype = item["type"] if item and (auth.is_admin(me()) or item["owner"] == me()) else pics.type_of(job_id)
            extra["kind"] = popups.card_kind(ctype or "")
        resp = jsonify(state=state, **extra)
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @talk.get("/media/<job_id>")
    @skippy_required
    def media_file(job_id):
        """A clip skippy-pc made for this chat (media.py): its requester or an admin, no one else."""
        item = media.get(job_id)
        if not item or not (auth.is_admin(me()) or item["owner"] == me()):
            kept = pics.get(job_id, me(), auth.is_admin(me()))      # a picture kept in the bucket (pictures.py)
            if not kept:
                abort(404)                           # not 403: don't confirm that a clip exists
            item = {"data": kept[0], "type": kept[1]}
        resp = send_file(io.BytesIO(item["data"]), mimetype=item["type"], conditional=True, max_age=0)
        resp.headers["Cache-Control"] = "private, no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if popups.card_kind(item["type"]) == "file":              # a pop-up's MIDI or zip: saved, never opened in the page
            resp.headers["Content-Disposition"] = f'attachment; filename="{job_id}.{popups.OUTPUT_TYPES[item["type"]]}"'
        return resp

    app.register_blueprint(talk)
    return app


def _num(info: dict, key: str):
    """A number a capability reported (a GPU figure), else None: a bool is not a number here."""
    value = info.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _lazy_app():
    """gunicorn imports `app:app`; tests import create_app without env vars."""
    import os
    return create_app() if os.environ.get("FLASK_SECRET_KEY") else None


app = _lazy_app()

if __name__ == "__main__":
    import os
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
