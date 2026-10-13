"""The recorder (telemetry.py) wired into the portal: the Flask hooks that watch signed-in people,
the routes (/privacy, the tap beacon, the home reporter's pull), the chat helper, and the dashboard's
Activity view. app.py makes one init_app() call; everything else lives here.

What a hook records and what it never does is the contract in telemetry.py (RECORDED / NOT_RECORDED),
which /privacy renders. The rules that keep it small:
  - only a signed-in person who is still on the list (a visitor, a signed-out browser, a removed friend: nothing);
  - no IP address and no user-agent are ever read, let alone stored;
  - pages = GET requests that returned HTML; JSON, images and audio are not pages, so polls never show up;
  - actions = every non-GET request, with field NAMES and only the SAFE_VALUES allow-list's values;
  - the polling and machine endpoints in SKIP_ENDPOINTS are never recorded at all;
  - a failure in the recorder is logged and swallowed: it must never break a page.
"""
from __future__ import annotations

import logging
import re
import secrets
import time

from flask import abort, current_app, g, jsonify, render_template, request, session

import auth
import catalog
import experiments
import play
import telemetry as tel_mod

log = logging.getLogger("telemetry")

# Endpoints that are never recorded: what a page polls while it is open, media, and the machine-to-machine ones.
SKIP_ENDPOINTS = frozenset({
    "static", "health", "site_icons",
    "dashboard.game_live", "dashboard.game_discord",               # a game page polls these
    "server_admin.fragment",                                        # ... and the admin card's 60 s refresh (HTML, so the mimetype rule wouldn't skip it)
    "chat.badges", "chat.media_file",                               # the chat fetches these itself
    "me.workshop_send", "me.workshop_events", "me.workshop_stop",   # the Captain's private Skippy: not recorded at all
    "me.workshop_resync", "me.workshop_history", "me.workshop_history_result",
    "me.workshop_ship", "me.workshop_notify", "me.workshop_image",
    "experiments.radiance_file", "radiance_probe",
    "starcontrol.game_file", "starcontrol.version",                 # the game's own files (its inner page is HTML)
    "home_report", "home_jobs", "home_job_done", "home_media", "home_import_chunk", "home_workshop",
    "home_telemetry", "game_import_download", "game_import_done", "ops_home", "tasks_run",
    "telemetry_tap",
})
SKIP_PREFIXES = ("/static/", "/home/", "/ops/", "/tasks/", "/telemetry/")
CHAT_ENDPOINTS = frozenset({"chat.send"})          # a good send is recorded as a chat event, not also as an action

_ID = r"[a-z0-9_-]{1,32}"
# Form fields whose VALUES are kept (ids, switches and small numbers: never text). A value that doesn't
# fit its pattern is not kept. Every other field is recorded by name only.
SAFE_VALUES = {
    "games": _ID,               # which games were ticked to start
    "game": _ID,                # which game a request-access form is about
    "portals": _ID,             # which apps a friend is granted (an admin's form)
    "slug": r"[a-z0-9_-]{1,60}",  # which experiment to show or hide
    "idle_minutes": r"\d{1,4}",  # the server's idle sleep
    "from_request": r"[01]",    # approving from a request card
}
_TAP = re.compile(r"[a-z0-9:_.\-]{1,60}")
LAUNCHER_TILES = ("skippy", "games", "play", "music", "video", "experiments", "dashboard", "admin")   # templates/home.html
ME_PANES = ("mcp", "home", "server", "core", "activity-pane", "watcher", "spend", "people", "links", "weather")   # templates/me.html: pane:<id>, for the Graveyard
TAPS_PER_MIN = 120          # one person's beacon budget
STUCK_AFTER_S = 900         # an event this old is still unacknowledged although home keeps pulling: the widget says so (normally < 1 min)


def _on(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def known_taps() -> list[str]:
    """Every tile a person can tap, by its data-tel id: the launcher's, a card per game, a browser game, an experiment.
    The Activity widget lists the ones with the fewest taps (removal candidates); a tile nobody has tapped has none."""
    return ([f"tile:{t}" for t in LAUNCHER_TILES] + [f"tile:game:{g}" for g in catalog.GAMES]
            + [f"pane:{p}" for p in ME_PANES] + [f"play:{x.slug}" for x in play.GAMES] + [f"demo:{x.slug}" for x in experiments.EXPERIMENTS])


def who():
    """(email, role, sid) of a signed-in person who is still on the list; None for anyone else. `sid` is a random
    id for this sign-in's session cookie (made on first use), never derived from anything about the browser."""
    user = session.get("user")
    email = str(user.get("email") or "").strip().lower() if isinstance(user, dict) else ""
    if not email or not auth.is_allowed(email):
        return None
    role = "admin" if auth.is_admin(email) else ("core" if auth.is_core(email) else "friend")
    sid = session.get("tsid")
    if not sid:
        sid = session["tsid"] = secrets.token_hex(4)
    return email, role, sid


def _skipped() -> bool:
    return (request.endpoint or "") in SKIP_ENDPOINTS or request.path.startswith(SKIP_PREFIXES)


def _fields() -> tuple[list[str], dict]:
    """The NAMES of what was submitted, and the values of the few SAFE_VALUES fields (only those that fit)."""
    if request.form:
        names = list(dict.fromkeys(request.form.keys()))
        values = {}
        for name, pattern in SAFE_VALUES.items():
            fit = [v for v in request.form.getlist(name) if re.fullmatch(pattern, v)]
            if fit:
                values[name] = fit[:20]
        return [n[:40] for n in names[:30]], values
    if request.is_json and (request.content_length or 0) <= 65536:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            return [str(k)[:40] for k in list(body)[:30]], {}
    return [], {}


def _observe(tel, response) -> None:
    if not tel.enabled or request.method in ("OPTIONS", "HEAD") or _skipped():
        return
    person = who()
    if not person:
        return
    status, endpoint, path = response.status_code, request.endpoint or "", request.path
    ms = int((time.perf_counter() - g.get("tel_t0", time.perf_counter())) * 1000)
    if request.method == "GET":
        if status >= 500 or (status >= 400 and response.mimetype == "text/html"):
            tel.record("error", str(status), *person, endpoint=endpoint, path=path, method="GET")
        elif status < 300 and response.mimetype == "text/html":
            tel.record("page", path, *person, endpoint=endpoint, status=status, ms=ms)
        return                                         # a redirect, JSON or a file isn't a page
    if endpoint in CHAT_ENDPOINTS and status < 400:
        return                                         # never what was said to Skippy: the Captain's rule (2026-09-30)
    fields, values = _fields()
    tel.record("action", endpoint or path, *person, path=path, method=request.method, status=status,
               ok=status < 400, fields=fields, values=values, ms=ms)
    if status >= 500:
        tel.record("error", str(status), *person, endpoint=endpoint, path=path, method=request.method)


def record_chat(message: str, reply: str, model: str, calls, forced: str | None = None):
    """One event for a message to Skippy and its reply, ONLY for someone who ticked "Allow us to save your questions" on
    the chat (the caller checks; the Captain's rule, 2026-09-30: nothing said in the chat is kept otherwise). `calls` is
    [(tool, arguments, result)]: the tool's name and arguments are kept, its result never is, and any code or password
    in a result that Skippy then repeated is scrubbed from the reply."""
    tel = current_app.extensions["telemetry"]
    try:
        person = who() if tel.enabled else None
        if not person:
            return None
        hidden = tel_mod.secret_values(result for _, _, result in calls)
        ms = int((time.perf_counter() - g.get("tel_t0", time.perf_counter())) * 1000)
        return tel.record("chat", "send", *person, message=message,
                          reply=tel_mod.scrub_values(reply, hidden), model=model,
                          tools=[{"name": name, "args": args} for name, args, _ in calls], ms=ms, forced=forced,
                          consented=True)
    except Exception:   # noqa: BLE001 — recording must never break the chat
        log.warning("telemetry: chat not recorded", exc_info=True)
        return None


def record_gap(detail: dict):
    """A physics question Mentat couldn't model (an auto-filed bug for the Captain to fix in sigma-ground). Always
    recorded: `detail` carries what the caller decided is fit to keep (the question only for the Captain or someone who
    ticked "Allow us to save your questions")."""
    tel = current_app.extensions["telemetry"]
    try:
        person = who() if tel.enabled else None
        if not person:
            return None
        return tel.record("mentat_gap", "gap", *person, **detail)
    except Exception:   # noqa: BLE001 — recording must never break the chat
        log.warning("telemetry: gap not recorded", exc_info=True)
        return None


def init_app(app):
    """Wire the recorder into `app`: the hooks, /privacy, the tap beacon and the home reporter's pull."""
    tel = tel_mod.Telemetry(enabled=_on(app.config.get("TELEMETRY_ENABLED", True)))
    app.extensions["telemetry"] = tel

    @app.context_processor
    def _flag():
        return {"telemetry_on": tel.enabled}

    @app.before_request
    def _start():
        g.tel_t0 = time.perf_counter()

    @app.after_request
    def _watch(response):
        try:
            _observe(tel, response)
        except Exception:   # noqa: BLE001 — never let recording break a page
            log.warning("telemetry: not recorded", exc_info=True)
        return response

    @app.get("/privacy")
    def privacy():
        """Public, and rendered from the recorder's own lists so it can't say something the code doesn't do."""
        return render_template("privacy.html", recorded=tel_mod.RECORDED, not_recorded=tel_mod.NOT_RECORDED,
                               also=tel_mod.ALSO, keep_days=tel_mod.KEEP_DAYS, enabled=tel.enabled)

    @app.post("/telemetry/tap")
    def telemetry_tap():
        """The page's beacon: a tap on something marked data-tel. Signed-in people only."""
        person = who()
        if not person:
            return "", 401
        if not tel.enabled:
            return "", 204
        if not app.extensions["limiter"].allow("tap:" + person[0], TAPS_PER_MIN, 60):
            return "", 429
        request.max_content_length = 2048                    # also bounds a body sent without a Content-Length: a 413, never a big read
        body = request.get_json(silent=True, force=True)     # sendBeacon posts a Blob: don't insist on the content type
        tap = str(body.get("e") or "") if isinstance(body, dict) else ""
        if not _TAP.fullmatch(tap):
            return "", 400
        page = str(body.get("p") or "")
        tel.record("ui", tap, *person, page=page if page.startswith("/") else "")
        return "", 204

    @app.get("/home/telemetry")
    def home_telemetry():
        """The home reporter collecting events (same bearer token as /home/jobs). `after` is the last seq it has
        stored for `boot`; it also acknowledges everything up to it, which the portal then forgets."""
        if not app.extensions["home"].authorized(request.headers.get("Authorization")):
            abort(401)
        boot = request.args.get("boot", "")
        boot = boot if re.fullmatch(r"[0-9a-f]{1,16}", boot) else ""
        after = max(0, request.args.get("after", type=int, default=0))
        limit = request.args.get("limit", type=int, default=200)
        resp = jsonify(tel.since(boot, after, limit))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    return tel


# ── the dashboard's Activity widget ──────────────────────────────────────────

def _ago(seconds) -> str:
    seconds = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size} {unit} ago"
    return "just now"


def _txt(value, limit: int = 80) -> str:
    return "" if value is None else str(value)[:limit]


def _num(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def describe(kind: str, name: str, detail: dict) -> str:
    """One plain line for an event, from what flask-api's summary (or the portal's own buffer) carries."""
    if kind == "page":
        return name
    if kind == "ui":
        return f"tapped {name}"
    if kind == "action":
        return f"{name} " + ("ok" if detail.get("ok") else f"failed ({_txt(detail.get('status'), 6)})")
    if kind == "chat":
        tools = ", ".join(_txt(t.get("name") if isinstance(t, dict) else t, 20) for t in (detail.get("tools") or [])[:4])
        return "asked Skippy: " + _txt(detail.get("message"), 80) + (f" [{tools}]" if tools else "")
    if kind == "mentat_gap":
        return "Mentat gap: " + (_txt(detail.get("question"), 80) or _txt(detail.get("route"), 40) or "a question")
    if kind == "error":
        return f"error {name} on {_txt(detail.get('path'))}"
    return name


def activity_view(tel, summary, report_age=None, now=None) -> dict:
    """What the Activity widget shows, from the recorder's own health and flask-api's summary (which the home
    reporter carries). The summary is data from outside this process, so every value is coerced and trimmed."""
    health = tel.status()
    now = now if now is not None else tel.clock()
    last = health["last_collected_s"]
    stuck = tel.oldest_waiting_s(now)
    if not health["enabled"]:
        state = "off"
    elif last is None:
        state = "never"           # nobody has pulled yet: home isn't running the collecting reporter
    elif last >= 180:
        state = "stale"
    elif stuck is not None and stuck > STUCK_AFTER_S:
        state = "stuck"           # home pulls, but what it pulled is never acknowledged: it isn't being filed (flask-api down?)
    else:
        state = "ok"
    view = {"enabled": health["enabled"], "state": state, "waiting": health["waiting"], "dropped": health["dropped"],
            "last_collected_s": last, "stuck_s": stuck, "have": False, "days": 0, "window_events": 0, "people": 0, "oldest_days": None,
            "retention_days": tel_mod.KEEP_DAYS, "top_pages": [], "top_taps": [], "least_used": [], "actions": [],
            "errors": [], "recent": [], "report_age": report_age}
    if isinstance(summary, dict):
        view.update(have=True, days=_num(summary.get("days")), window_events=_num(summary.get("window_events")),
                    people=_num(summary.get("people")), retention_days=_num(summary.get("retention_days"), tel_mod.KEEP_DAYS))
        oldest = summary.get("oldest")
        view["oldest_days"] = max(0, int((now - oldest) // 86400)) if isinstance(oldest, (int, float)) else None
        rows = lambda key: [r for r in (summary.get(key) or [])[:60] if isinstance(r, dict)]   # noqa: E731
        view["top_pages"] = [{"name": _txt(r.get("name")), "views": _num(r.get("views")), "people": _num(r.get("people"))}
                             for r in rows("top_pages")[:8]]
        view["top_taps"] = [{"name": _txt(r.get("name")), "taps": _num(r.get("taps"))} for r in rows("top_taps")[:8]]
        view["actions"] = [{"name": _txt(r.get("name")), "count": _num(r.get("count")), "failed": _num(r.get("failed"))}
                           for r in rows("actions")[:8]]
        view["errors"] = [{"name": _txt(r.get("name")), "count": _num(r.get("count"))} for r in rows("errors")[:5]]
        seen = {_txt(r.get("name"), 60): r for r in rows("taps")}      # every tap in the retention window, by tile
        least = []
        for name in known_taps():
            row = seen.get(name)
            least.append({"name": name, "taps": _num(row.get("taps")) if row else 0,
                          "last_days": _num(row.get("last_days")) if row else None})
        # fewest taps first; among ties, the one untapped longest (never = longest)
        least.sort(key=lambda r: (r["taps"], -(r["last_days"] if r["last_days"] is not None else 10 ** 6), r["name"]))
        view["least_used"] = least[:8]
        for r in rows("recent")[:10]:
            detail = r.get("detail") if isinstance(r.get("detail"), dict) else {}
            ts = r.get("ts")
            view["recent"].append({"ago": _ago(now - ts) if isinstance(ts, (int, float)) else "",
                                   "user": _txt(r.get("user"), 60), "kind": _txt(r.get("kind"), 10),
                                   "text": describe(_txt(r.get("kind"), 10), _txt(r.get("name")), detail)})
    else:                          # home hasn't sent a summary: what the portal still holds is all there is
        for e in tel.recent(10):
            view["recent"].append({"ago": _ago(now - e["ts"]), "user": _txt(e["user"], 60), "kind": e["kind"],
                                   "text": describe(e["kind"], e["name"], e["detail"])})
    return view


def activity(app, home) -> dict:
    """The widget's data for /me: app.extensions["telemetry"] plus what home last reported."""
    return activity_view(app.extensions["telemetry"], home.site_activity(), report_age=home.age())
