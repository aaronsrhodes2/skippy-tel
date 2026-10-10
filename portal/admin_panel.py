"""The "Server admin" card on each game's page: installed version, image age, the mods list, the update policy in
effect and when it last updated. Admin only (ADMIN_EMAILS, the Captain): friends never get the card in their page
and every route here answers them 403 (signed-out: the sign-in redirect).

Read-only facts come from the VM's newest snapshot (serial port 2), the same read that gives join codes:
infra/gcp-games/game_facts.py prints them, diagnostics.game_facts() parses them. What to do about them stays where it
was: settings (catalog.UPDATE_SETTINGS, wired in games/docker-compose.yml) and the existing Restart & update. There is
no file upload and no installer here. Per game, what the image really supports is in docs/games/MODS_AND_UPDATES.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, abort, make_response, render_template, request

import auth
import diagnostics
from catalog import GAMES, UPDATE_DEFAULTS, Game

STALE_AFTER_S = 6 * 60          # the snapshot timer is 2 minutes: three misses and the numbers say so

# What each game's mod support is, for the card's mods section. `kind` picks the wording when the list is empty.
#   loader: the game has a working mod loader on this server (it can be on or off, or it lists what it loaded)
#   manual: mods can exist but nothing here installs or updates them
#   none:   nothing to list, and why
MOD_SUPPORT = {
    "valheim": {"kind": "loader", "builtin": {"BepInExPack_Valheim": "mod loader"}},
    "factorio": {"kind": "loader", "builtin": {"base": "built in", "elevated-rails": "built in", "quality": "built in",
                                              "space-age": "built in"}},
    "satisfactory": {"kind": "manual", "builtin": {}},
    "dragonwilds": {"kind": "none", "builtin": {},
                    "why": "The image we run documents no mod support, so there is nothing to install or list."},
    "garrysmod": {"kind": "manual", "builtin": {}},
    "everquest": {"kind": "none", "builtin": {},
                  "why": "The world lives in the server's database, not in mods; the client is patched by hand (eqhost.txt)."},
}


def _when(iso: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def ago(iso: str, now: datetime | None = None) -> str:
    """'12 days ago' for an ISO time, "" when it isn't one."""
    when = _when(iso or "")
    if not when:
        return ""
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - when).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min ago"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


def _on(env: dict, key: str) -> bool:
    """A true/false setting, blank meaning the default the compose file uses."""
    value = (env.get(key) or UPDATE_DEFAULTS[key]).strip().lower()
    return value == "true"


def policy(game_id: str, env: dict, image: str = "") -> list[dict]:
    """The update policy in effect, in words: [{what, state, text}]. `state` is auto | on | off | pinned | manual | none
    and only colours a pill. Computed from the saved settings, so it says what the NEXT start will do."""
    rows: list[dict] = []

    def row(what: str, state: str, text: str) -> None:
        rows.append({"what": what, "state": state, "text": text})

    if game_id == "valheim":
        row("Server files", "auto", "Updated from Steam at every start. While it runs, Steam is checked every 15 minutes and a "
            "newer build restarts the server, but only when nobody is on. This image has no switch to turn that off or to hold a version.")
        row("Image", "auto", f"{image or 'The server image'} is pulled fresh at every boot.")
        if _on(env, "VALHEIM_BEPINEX"):
            row("Mods (BepInEx)", "on", "BepInEx is installed and refreshed from Thunderstore at every start. Plugins in "
                "data/valheim/config/bepinex/plugins are never updated for you: replace them on the server disk, then Restart & update.")
        else:
            row("Mods (BepInEx)", "off", "Off: a vanilla server. Turn on Mod support (BepInEx) in the settings below to use mods.")
    elif game_id == "factorio":
        tag = (env.get("FACTORIO_VERSION") or UPDATE_DEFAULTS["FACTORIO_VERSION"]).strip().lower()
        if tag in ("stable", "latest"):
            row("Server version", "auto", f"factoriotools/factorio:{tag} is pulled at every boot, so the server follows Factorio's "
                f"{'stable' if tag == 'stable' else 'experimental'} release.")
        else:
            row("Server version", "pinned", f"Held at Factorio {tag}: it does not update until the Server version setting changes.")
        if _on(env, "FACTORIO_UPDATE_MODS"):
            text = ("At every start each enabled mod in mod-list.json is updated from mods.factorio.com "
                    "(a listed mod that isn't installed yet is downloaded).")
            if not (env.get("FACTORIO_USERNAME") and env.get("FACTORIO_TOKEN")):
                text += " The Factorio username and token aren't both saved, so every download will fail with 403."
            if env.get("FACTORIO_UPDATE_IGNORE"):
                text += f" Left alone: {env['FACTORIO_UPDATE_IGNORE']}."
            row("Mod updates", "on", text)
        else:
            row("Mod updates", "off", "Off: installed mods stay as they are. Turn on Update mods on start in the settings below "
                "(it needs a factorio.com username and token).")
        row("Adding mods", "manual", "The portal uploads no files. List a mod as enabled in data/factorio/mods/mod-list.json (or put "
            "its .zip in data/factorio/mods) on the server disk; with mod updates on, the next start downloads listed mods.")
    elif game_id == "satisfactory":
        if _on(env, "SATISFACTORY_SKIP_UPDATE"):
            row("Server files", "pinned", "The update is skipped: the installed build is kept, and players on a newer game version can't join.")
        else:
            row("Server files", "auto", "Updated from Steam at every start (the image's default).")
        row("Mods", "manual", "This image runs the stock server and doesn't install or update mods. Mods placed on the server "
            "disk by hand are listed above when the server reports them.")
    elif game_id == "dragonwilds":
        if _on(env, "DRAGONWILDS_UPDATE_ON_START"):
            row("Server files", "auto", "Downloaded and validated from Steam at every start, so the server matches the game client.")
        else:
            row("Server files", "pinned", "Not updated on start: the server can fall behind the game client and refuse players.")
        row("Mods", "none", MOD_SUPPORT["dragonwilds"]["why"])
    elif game_id == "garrysmod":
        row("Server version", "auto", f"{image or 'ceifa/garrysmod:latest'} is pulled at every boot, so the server follows the image's rebuilds "
            "(the image's own start-up also updates the server files from Steam). There is no switch to hold a version.")
        if (env.get("GARRYSMOD_WORKSHOP_COLLECTION") or "").strip():
            row("Mods", "auto", f"Workshop collection {env['GARRYSMOD_WORKSHOP_COLLECTION'].strip()} is downloaded by the server at every start; "
                "change it in this game's settings. Players download the same addons when they join.")
        else:
            row("Mods", "manual", "No Workshop collection is set (Settings → Workshop collection). Addons can also be put in "
                "data/garrysmod/addons on the server disk by hand.")
    elif game_id == "everquest":
        row("Server version", "pinned", "AkkStack is pinned to one commit in infra/gcp-games/everquest.sh (AKK_REF). It changes "
            "only when that line changes and the portal is deployed; nothing updates at boot.")
        row("Mods", "none", MOD_SUPPORT["everquest"]["why"])
    return rows


def mod_update_text(summary: dict) -> str:
    """One sentence for Factorio's MODUPDATE line: what the last start's mod update did."""
    if not summary:
        return ""
    bits = [f"checked {summary.get('checked', 0)}", f"updated {summary.get('downloaded', 0)}",
            f"already current {summary.get('current', 0)}"]
    if summary.get("failed"):
        bits.append(f"{summary['failed']} failed" + (f" (HTTP {summary['code']})" if summary.get("code") else ""))
    if summary.get("skipped"):
        bits.append(f"{summary['skipped']} skipped")
    text = "Last start: " + ", ".join(bits) + "."
    if summary.get("nocreds"):
        text += " No Factorio username/token was given, so downloads can't succeed."
    return text


def build(game: Game, status, facts: dict[str, dict], taken: str = "", now: datetime | None = None) -> dict:
    """Everything the card shows, for one game. `facts` is diagnostics.game_facts() of the newest snapshot."""
    now = now or datetime.now(timezone.utc)
    env = status.env if status else {}
    running = bool(status and status.status == "RUNNING")
    on = running and game.id in status.games
    f = facts.get(game.id, {}) if on else {}
    support = MOD_SUPPORT.get(game.id, {"kind": "none", "builtin": {}})
    mods = None
    if "mods" in f:
        mods = sorted(({"name": n, "version": v, "role": support["builtin"].get(n, "installed")} for n, v in f["mods"]),
                      key=lambda m: (m["role"] != "installed", m["name"].lower()))      # what was installed first
    taken_at = _when(taken)
    if not on:
        mods_note = "The server is asleep or another game is up: the list appears while this game runs."
    elif mods is None and support["kind"] == "none":
        mods_note = support["why"]
    elif mods is None:
        mods_note = "Waiting for the server's first snapshot (about a minute after it boots)."
    elif not [m for m in mods if m["role"] == "installed"]:
        mods_note = "No mods are loaded." + (" Mod support is off (setting: Mod support (BepInEx))."
                                             if game.id == "valheim" and not _on(env, "VALHEIM_BEPINEX") else "")
    else:
        mods_note = ""
    return {
        "game": game, "on": on, "running": running, "have_facts": bool(f),
        "version": f.get("version", ""),
        "image": f.get("image", ""), "image_built": (f.get("image_built") or "")[:10], "image_age": ago(f.get("image_built", ""), now),
        "updated": (f.get("updated") or "")[:10], "updated_ago": ago(f.get("updated", ""), now),
        "started_ago": ago(f.get("started", ""), now),
        "as_of": ago(taken, now), "stale": bool(taken_at and (now - taken_at).total_seconds() > STALE_AFTER_S),
        "mods": mods, "mods_note": mods_note,
        "mod_update": mod_update_text(f.get("modupdate") or {}),
        "policy": policy(game.id, env, f.get("image", "")),
    }


def init_app(app, vm) -> None:
    """Registers the admin-only refresh route and the `server_admin_panel(game, status)` template global that
    game.html calls, inside its admin-only block, so the facts are only ever read for an admin."""
    bp = Blueprint("server_admin", __name__, url_prefix="/admin/games")

    def panel_for(game: Game, status) -> dict:
        facts, taken = diagnostics.current_facts(vm) if status and status.status == "RUNNING" else ({}, "")
        return build(game, status, facts, taken)

    def poll_gets_401(view):
        """The page's own poll (it sends `X-Requested-With: fetch`) is never redirected to sign in: `fetch` would follow
        the redirect and hand the page the whole Sign-in document as if it were the card. It gets a plain 401 instead,
        so the card can keep its last body and say why. A visit in the browser is redirected as before."""
        @wraps(view)
        def wrapped(*args, **kwargs):
            resp = make_response(view(*args, **kwargs))
            if request.headers.get("X-Requested-With") == "fetch" and resp.status_code in (301, 302, 303, 307, 308):
                resp = make_response("Signed out", 401)
                resp.headers["Cache-Control"] = "no-store"
            return resp
        return wrapped

    @bp.get("/<gid>/panel")
    @poll_gets_401
    @auth.admin_required
    def fragment(gid):
        """The card's body as an HTML fragment, for the page's poll (the same body the page renders)."""
        game = GAMES.get(gid)
        if game is None:
            abort(404)
        try:
            status = vm.status()
        except Exception:   # noqa: BLE001 — the VM API: an empty card is better than a 500
            status = None
        resp = make_response(render_template("_server_admin_body.html", panel=panel_for(game, status), game=game))
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Skippy-Fragment"] = "server-admin"       # what the poll checks: only this may replace the card
        return resp

    app.register_blueprint(bp)
    app.jinja_env.globals["server_admin_panel"] = panel_for
