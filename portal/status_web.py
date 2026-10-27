"""The Status page (`/status`, admin only): the whole house on one page, drawn as a graph (docs/status-design.md).

  GET  /status          the page: machines as nodes, the real connections as lines, a server opens into its containers, models and MCPs
  GET  /status/state    the same as JSON, refreshed every 10 s while the page is open; nothing is fetched while it is closed
  POST /status/heal     one container's heal (the doctor's own heal, through the starter on skippy-pc: same names, same rules)
  POST /status/repair   "Put everything back" (the doctor's one repair, as recovery_web's button, without the redirect)
  POST /home/core-report  skippy-core's reporter (core/reporter): the home token, like the home report and the starter
"""
from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request

import auth
import buildinfo
import recovery
import starter as starter_mod

REPAIR_LIMIT, REPAIR_WINDOW_S = 2, 300


def init_app(app, status, home, starter, doctor, workshop, limiter, vm_status) -> None:
    bp = Blueprint("status", __name__)
    app.extensions["status"] = status

    def roots() -> list[str]:
        return [r for r in (app.config.get("PORTAL_URL"), request.host_url.rstrip("/")) if r]

    def state() -> dict:
        vm, _ = vm_status()
        return status.tree(
            home_online=home.online(), home_age=home.age(), home_report=home.report, starter_view=starter.view(),
            portal=buildinfo.info(), games_vm={"status": vm.status} if vm else None, core_verdict=home.core())

    @bp.get("/status")
    @auth.admin_required
    def page():
        return render_template("status.html", heal_names=list(starter_mod.HEAL_NAMES))

    @bp.get("/status/state")
    @auth.admin_required
    def state_json():
        resp = jsonify(state())
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    def same_origin_or_abort() -> None:
        if not recovery.same_origin(request.headers.get("Origin"), request.headers.get("Referer"), roots()):
            abort(403)

    @bp.post("/status/heal")
    @auth.admin_required
    def heal():
        same_origin_or_abort()
        name = str((request.get_json(silent=True) or {}).get("name") or "")
        if not starter.online():
            return jsonify(ok=False, why="skippy-pc's starter isn't answering: the PC is off, asleep, at the sign-in screen, or the starter isn't installed"), 503
        try:
            starter.request("heal", auth.current_user()["email"], arg=name)
        except ValueError as exc:
            return jsonify(ok=False, why=str(exc)), 400
        return jsonify(ok=True, why=f"healing {name}: the starter carries it out within seconds")

    @bp.post("/status/repair")
    @auth.admin_required
    def repair():
        same_origin_or_abort()
        if not limiter.allow("status:repair", REPAIR_LIMIT, REPAIR_WINDOW_S):
            return jsonify(ok=False, why="Already putting everything back: the Problems list shows how it's going."), 429
        who = auth.current_user()["email"]
        said = []
        if workshop.clear_stuck():
            said.append("cleared stuck chat records")
        if workshop.paused:
            workshop.resume()
            said.append("the private Skippy is back on")
        if doctor and starter.online():
            doctor.repair(who)
            said.append("skippy-pc and skippy-core are being put back")
        else:
            said.append("skippy-pc's starter isn't answering, so skippy-pc and skippy-core can't be reached")
        return jsonify(ok=True, why="; ".join(said) + ".")

    @app.post("/home/core-report")
    def home_core_report():
        if (request.content_length or 0) > 200_000:
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        status.accept_core(body)
        return jsonify(ok=True)

    app.register_blueprint(bp)
