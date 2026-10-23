"""Recovery is ONE operation (the Captain's design, 2026-10-23): "Put everything back". The doctor (doctor.py) keeps everything at its
known good baseline by itself; this button does all of it at once, for when something is wrong now. It does not revert code: undoing a
change is the private Skippy's job, from a chat that works.

The button (POST /admin/recovery/repair): admin only, from the portal's own page (Origin/Referer), at most twice in five minutes, logged,
and it acts at once (the Captain's rule: no confirm boxes). What it does:
  * the portal's own part, here: the private Skippy's stuck chat records cleared and a pause lifted; a redeploy of main (the deploy
    workflow, tests first) when the newest deploy failed, or main is ahead of what's live and nothing is deploying
  * skippy-pc and Skippy Core: doctor.repair(), carried out by the starter on the Windows host (tools/skippy_starter.py)

The card (templates/_recovery.html) is one checklist (every part, green or red, and why) with the doctor's last actions under it;
GET /admin/recovery/state is the same, as JSON, for the card to refresh itself while it is open.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, request, url_for

import auth
import buildinfo
import recovery

BACK = "#recovery"
REPAIR_LIMIT, REPAIR_WINDOW_S = 2, 300


def init_app(app, workshop, limiter, github: recovery.GitHub, clock=None, home=None, starter=None, doctor=None) -> None:
    log = recovery.Log()
    runs_cache = recovery.RunsCache(github)
    app.extensions["recovery"] = {"log": log, "github": github, "runs": runs_cache}
    now = clock or (lambda: datetime.now(timezone.utc))
    bp = Blueprint("recovery", __name__, url_prefix="/admin/recovery")

    def own_roots() -> list[str]:
        return [request.host_url, app.config.get("PORTAL_URL") or ""]

    def portal_row() -> dict:
        """The portal's own line in the checklist: what it serves, and whether main is ahead or the last deploy failed."""
        build = buildinfo.info()
        row = {"name": "portal", "label": "Portal", "ok": True, "why": "", "short": build["short"], "revision": build.get("revision")}
        if not github.configured:
            row["why"] = "the deploy pipeline isn't visible (no GITHUB_ACTIONS_TOKEN)"
            return row
        runs, error = runs_cache.get()
        if runs is None:
            row["why"] = error or "GitHub didn't answer"
            return row
        view = recovery.pipeline(runs, build["commit"], now())
        newest = runs[0] if runs else None
        if newest and newest["status"] == "completed" and newest["conclusion"] == "failure":
            row.update(ok=False, why=f"the newest deploy ({newest['short']}) failed")
        elif view.get("stuck"):
            row.update(ok=False, why=f"a deploy has been running for over 20 minutes ({view['stuck']['short']})")
        elif view.get("ahead"):
            busy = bool(newest and newest["status"] != "completed")
            row.update(ok=busy, why=f"main is {view['ahead']} push{'es' if view['ahead'] != 1 else ''} ahead" + (": deploying now" if busy else ""))
        return row

    def state() -> dict:
        view = doctor.view() if doctor else {"rows": [], "ok": True, "actions": [], "maintenance": False, "repair": None}
        rows = [portal_row()] + view["rows"]
        workshop_row = {"name": "workshop", "label": "Private Skippy", "ok": not workshop.paused and workshop.ready(),
                        "why": "paused" if workshop.paused else ("" if workshop.ready() else "not ready on skippy-pc")}
        rows.append(workshop_row)
        at = now()
        return {"rows": rows, "ok": all(r["ok"] for r in rows), "actions": view["actions"], "maintenance": view["maintenance"],
                "repair": view["repair"],
                "log": [{**e, "when": recovery.ago(datetime.fromtimestamp(e["at"], timezone.utc), at)} for e in log.recent()[:5]]}

    @bp.post("/repair")
    @auth.admin_required
    def repair():
        who = auth.current_user()["email"]
        if not recovery.same_origin(request.headers.get("Origin"), request.headers.get("Referer"), own_roots()):
            log.add("repair", who, "refused: the request didn't come from the portal's own page", False)
            abort(403)
        if not limiter.allow("recovery:repair", REPAIR_LIMIT, REPAIR_WINDOW_S):
            flash("Already putting everything back: the checklist shows how it's going.", "error")
            return redirect(url_for("admin.index") + BACK)
        said = []
        # the portal's own part
        cleared = workshop.clear_stuck()
        if cleared:
            said.append(f"cleared {len(cleared)} stuck chat record(s)")
        if workshop.paused:
            workshop.resume()
            said.append("the private Skippy is back on")
        row = portal_row()
        if github.configured and not row["ok"] and "deploying now" not in row["why"]:
            try:
                github.dispatch_deploy()
                runs_cache.forget()
                said.append("redeploying main (" + row["why"] + ")")
            except recovery.GitHubError as exc:
                said.append(f"couldn't redeploy: {exc}")
        # skippy-pc and Skippy Core
        if doctor and starter and starter.online():
            doctor.repair(who)
            said.append("skippy-pc and Skippy Core are being put back (the checklist follows it)")
        else:
            said.append("skippy-pc's starter isn't answering, so skippy-pc and Skippy Core can't be reached: the PC is off, asleep, at "
                        "the sign-in screen, or the starter isn't installed")
        text = "Putting everything back: " + "; ".join(said) + "."
        log.add("repair", who, text, True)
        flash(text, "ok")
        return redirect(url_for("admin.index") + BACK)

    @bp.get("/state")
    @auth.admin_required
    def state_json():
        resp = jsonify(state())
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    app.register_blueprint(bp)
    app.jinja_env.globals["recovery_state"] = state
