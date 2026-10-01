"""The recovery buttons wired into the portal (recovery.py holds the rules): the admin-only POST routes under
/admin/recovery, and the `recovery_cards()` template global the Admin page renders its three cards from.

app.py makes one init_app() call. Nothing here is reachable by a friend, from a chat tool, or without a confirm.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import auth
import buildinfo
import recovery

BACK = "#recovery"


def init_app(app, workshop, limiter, github: recovery.GitHub, clock=None) -> None:
    log = recovery.Log()
    runs_cache = recovery.RunsCache(github)
    app.extensions["recovery"] = {"log": log, "github": github, "runs": runs_cache}
    now = clock or (lambda: datetime.now(timezone.utc))
    bp = Blueprint("recovery", __name__, url_prefix="/admin/recovery")

    def own_roots() -> list[str]:
        return [request.host_url, app.config.get("PORTAL_URL") or ""]

    def cards() -> dict:
        """Everything the three cards show. Only ever called from admin.html, which only an admin can render."""
        build, at = buildinfo.info(), now()
        out = {"build": build, "configured": github.configured, "error": None, "view": None,
               "workshop": workshop.current(), "paused": workshop.paused, "ready": workshop.ready(),
               "log": [{**e, "when": recovery.ago(datetime.fromtimestamp(e["at"], timezone.utc), at)} for e in log.recent()],
               "actions": recovery.ACTIONS, "repo": recovery.REPO, "fetched": recovery.RUNS_FETCHED}
        if github.configured:
            runs, error = runs_cache.get()
            out["error"] = error
            if runs is not None:
                out["view"] = recovery.pipeline(runs, build["commit"], at)
        return out

    def done(action: str, result: str, ok: bool = True):
        log.add(action, auth.current_user()["email"], result, ok)
        flash(result, "ok" if ok else "error")
        return redirect(url_for("admin.index") + BACK)

    @bp.post("/<action>")
    @auth.admin_required
    def act(action: str):
        spec = recovery.ACTIONS.get(action)
        if spec is None:
            abort(404)
        who = auth.current_user()["email"]
        if not recovery.same_origin(request.headers.get("Origin"), request.headers.get("Referer"), own_roots()):
            log.add(action, who, "refused: the request didn't come from the portal's own page", False)
            abort(403)
        if request.form.get("confirm") != "yes":
            # No confirm (no script on the page, or a hand-made POST): ask on a page of our own instead of acting.
            return render_template("recovery_confirm.html", action=action, spec=spec,
                                   fields={k: v for k, v in request.form.items() if k in ("revision",)})
        if spec.github and not github.configured:
            return done(action, "The deploy buttons aren't configured: the portal has no GITHUB_ACTIONS_TOKEN.", False)
        if action == "rollback" and recovery.valid_revision(request.form.get("revision")) is None:
            # Checked before the rate limit, so a typo doesn't cost the one rollback allowed per two minutes.
            return done(action, "That isn't a revision name: leave it blank, or type latest or a name like "
                                "skippy-portal-00042-abc. Nothing was started.", False)
        if not limiter.allow(f"recovery:{action}", spec.limit, spec.window_s):
            minutes = max(1, spec.window_s // 60)
            return done(action, f"{spec.label}: already done just now. Wait {minutes} min and look at the card first.", False)
        try:
            result = run(action, who)
        except recovery.GitHubError as exc:
            return done(action, f"{spec.label} failed: {exc}", False)
        runs_cache.forget()                       # the card's next look asks GitHub again
        return done(action, result)

    def newest_run() -> dict | None:
        runs = github.deploy_runs()
        return runs[0] if runs else None

    def run(action: str, who: str) -> str:
        """The action itself. The target (which run, which input) is decided HERE, from GitHub's current answer, never
        taken from the page: a page drawn ten minutes ago must not cancel or re-run a run that has since changed."""
        if action == "rollback":
            revision = recovery.valid_revision(request.form.get("revision")) or ""     # checked in act(); the client checks again
            github.dispatch_rollback(revision)
            target = f"to {revision}" if revision else "one revision back"
            return (f"Rollback started ({target}). The portal restarts in about a minute; reload then. "
                    "The next push to main deploys normally and undoes it.")
        if action == "undo-rollback":
            github.dispatch_rollback("latest")
            return "Undo started: traffic goes back to the newest revision in about a minute."
        if action == "rerun-failed":
            newest = newest_run()
            if not newest or newest["status"] != "completed" or newest["conclusion"] != "failure":
                raise recovery.GitHubError("The newest deploy didn't fail, so there's nothing to re-run "
                                           "(re-running an older one could put old code live).")
            github.rerun_failed(newest["id"])
            return f"Re-running the failed jobs of deploy {newest['short']}. Watch the card."
        if action == "cancel-stuck":
            newest = newest_run()
            stuck = recovery.pipeline([newest] if newest else [], "", now())["stuck"]
            if not stuck:
                raise recovery.GitHubError("The newest deploy isn't stuck (running for over 20 minutes), so nothing was cancelled.")
            github.cancel(stuck["id"])
            # No rollback dispatch on top: since the A2 fix, a cancelled deploy's own last step moves traffic back to the
            # revision it replaced. rollback.yml left blank would step back one revision MORE (or, when the deploy never
            # reached Cloud Run, roll back a good revision). The Roll back button stays for when it is still wrong.
            return (f"Cancelled deploy {stuck['short']}. If it had already switched traffic, its own rollback step puts the "
                    "previous revision back within a few minutes. Still wrong after that? Use Roll back.")
        if action == "workshop-cancel":
            ids = workshop.cancel_current()
            return f"Cancelled {len(ids)} run(s); skippy-pc is told to stop." if ids else "Nothing was running."
        if action == "workshop-clear":
            ids = workshop.clear_stuck()
            return f"Cleared {len(ids)} chat record(s)." if ids else "No stuck record to clear."
        if action == "workshop-pause":
            ids = workshop.pause(who)
            return "Private Skippy paused" + (f"; cancelled {len(ids)} run(s)." if ids else ".")
        if action == "workshop-resume":
            return "Private Skippy is back on." if workshop.resume() else "It wasn't paused."
        abort(404)                                # an ACTIONS entry with no code: never silently "succeed"

    app.register_blueprint(bp)
    app.jinja_env.globals["recovery_cards"] = cards
