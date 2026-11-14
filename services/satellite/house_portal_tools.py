"""The house's portal-side tools, served by the house MCP (house_mcp.py, server `skippy-house`): the Captain's Bilby favorites, his Spotify account,
Skippy-vision's moments and his latest text-message code. They lived in purple-tools until 2026-11-13; the house MCP is now the one place anything
about the house is served, for purple, a hub, a game mod or any other client.

Each tool is {description, input_schema, run(args) -> text}; a tool raises ValueError with its own words, which `call` turns into an error result.
(`spotify_account` is the old purple-tools `spotify`: search, pause, playlists, add and like through the Spotify service; the house radio's `spotify`
plays on the speakers.)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_TEXT = 7000
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text, n: int) -> str:
    one = re.sub(r"\s+", " ", _CONTROL.sub("", str(text or ""))).strip()
    return one if len(one) <= n else one[: n - 1] + "\u2026"


PORTAL_URL = os.environ.get("PURPLE_PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app").rstrip("/")


def _home_token() -> str:
    """The home token (the reporter's, the hub's): from the environment, else the vault on G:."""
    tok = os.environ.get("SKIPPY_HOME_TOKEN", "")
    if tok:
        return tok
    try:
        for line in Path(os.environ.get("PURPLE_VAULT", "G:/My Drive/vault/.env")).read_text(encoding="utf-8").splitlines():
            if line.startswith("SKIPPY_HOME_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def bilby_favorite(args: dict, request=None) -> str:
    """Mark what he is listening to now (any of his Bilby players or pods), or list the favorites."""
    action = args.get("action") or "add"
    token = _home_token()
    if not token:
        raise ValueError("bilby_favorite: no home token on this PC")
    if request is None:
        def request(method, url, body=None):
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
    if action == "list":
        code, got = request("GET", f"{PORTAL_URL}/home/music/favorites")
        items = got.get("favorites") or []
        if not items:
            return "His favorites list is empty."
        return "\n".join(f"{i + 1}. {f.get('artist') or '?'} - {f.get('title') or f.get('track')}" for i, f in enumerate(items[:100]))[:MAX_TEXT]
    code, got = request("POST", f"{PORTAL_URL}/home/music/favorite", {"source": "purple"})
    if code == 409:
        return "Nothing is playing on any of his Bilby players, so there is nothing to add."
    if code != 200:
        raise ValueError(f"bilby_favorite: the portal answered {code}")
    f = got.get("favorite") or {}
    return f"Added to his favorites: {f.get('artist') or '?'} - {f.get('title') or f.get('track')}" + (" (already there; moved to the top)" if int(f.get("count") or 1) > 1 else "")


# ── Skippy-vision: one flash of what the house and the PC show (portal/vision.py; the Captain's key and words "snap" do the same) ──────────────────────
def _home_request(method: str, url: str, body=None, timeout: float = 30.0, raw: bool = False):
    """(status, json or bytes) from the portal with the home token."""
    token = _home_token()
    if not token:
        raise ValueError("no home token on this PC")
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(30_000_000)
            return r.status, data if raw else json.loads(data or b"{}")
    except urllib.error.HTTPError as e:
        data = e.read(1_000_000)
        return e.code, data if raw else (json.loads(data) if data[:1] in (b"{", b"[") else {})


def vision_flash(args: dict, request=_home_request) -> str:
    """Save a moment of what skippy-pc can see right now: the pods' screens and this PC's monitors."""
    code, got = request("POST", f"{PORTAL_URL}/home/vision/flash", {})
    if code == 503:
        raise ValueError("vision_flash: moments have no storage yet (the portal's bucket)")
    if code != 200 or not got.get("id"):
        raise ValueError(f"vision_flash: the portal answered {code}")
    waiting = "; the pods' and this PC's screens are arriving (about 30 s)" if got.get("pending") else "; skippy-pc isn't reporting, so nothing was fetched"
    return f"Saved moment {got['id']}{waiting}. Look at it with vision_moment (id {got['id']})."


def _moment_line(m: dict) -> str:
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(m.get("at") or 0)))
    seen = "; ".join(_clean(s.get("en") or s.get("text"), 80) for s in (m.get("seen") or [])[:2] if isinstance(s, dict))
    bits = [f"{len(m.get('files') or [])} files", f"by {m.get('by')}"]
    if m.get("hold"):
        bits.append("clip")
    if m.get("said"):
        bits.append(f"said: {_clean(m['said'], 120)}")
    if seen:
        bits.append(f"read: {seen}")
    if m.get("pending"):
        bits.append("still arriving")
    return f"{m.get('id')} · {when} · " + ", ".join(bits)


def vision_moment(args: dict, request=_home_request, cwd: Path | None = None) -> str:
    """The latest moments, or one moment's pictures saved into .purple-out/ to Read."""
    mid = str(args.get("id") or "").strip()
    if mid and not re.fullmatch(r"\d{8}-\d{6}(?:-\d{1,2})?", mid):
        raise ValueError("vision_moment: an id looks like 20261102-210000")
    code, got = request("GET", f"{PORTAL_URL}/home/vision/moments?limit={30 if mid else max(1, min(10, int(args.get('limit') or 5)))}")
    if code != 200:
        raise ValueError(f"vision_moment: the portal answered {code}")
    moments = got.get("moments") or []
    if not mid:
        return "\n".join(_moment_line(m) for m in moments)[:MAX_TEXT] or "No moments yet."
    moment = next((m for m in moments if m.get("id") == mid), None)
    if moment is None:
        raise ValueError(f"vision_moment: no moment {mid} among the latest ones")
    base = (cwd / ".purple-out") if cwd else Path(os.environ.get("HOUSE_MCP_OUT") or Path(__file__).resolve().parents[2] / "state" / "purple-out")   # the server is not in the run's folder: a fixed, readable one
    folder = base / f"moment-{mid}"
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in [x for x in moment.get("files") or [] if str(x.get("type") or "").startswith("image/")][:12]:
        name = str(f.get("name") or "")
        status, data = request("GET", f"{PORTAL_URL}/home/vision/moment/{mid}/{urllib.parse.quote(name)}", raw=True)
        if status == 200 and isinstance(data, (bytes, bytearray)) and data:
            (folder / name).write_bytes(bytes(data))
            saved.append(str(folder / name))
    clip = "; a 10-second clip is in the moment too (glasses-rgb-7s.zip, not fetched)" if any(x.get("name") == "glasses-rgb-7s.zip" for x in moment.get("files") or []) else ""
    return _moment_line(moment) + clip + ("\nSaved: " + ", ".join(saved) + "\nRead a picture to look at it." if saved else "\nNo pictures could be fetched.")


# ── his texts: the verification code that just arrived (Google Voice's forwarding to his Gmail, read by portal/texts.py) ─────────────────────────────
def texts_latest_code(args: dict, request=_home_request) -> str:
    """The code from a text that came in during the last five minutes, with who sent it; or why there is none."""
    status, got = request("GET", f"{PORTAL_URL}/home/texts/code")
    if status != 200 or not isinstance(got, dict):
        raise ValueError(f"texts_latest_code: the portal answered {status}")
    if got.get("code"):
        ago = max(0, int(got.get("age_s") or 0))
        when = "just now" if ago < 45 else f"{max(1, round(ago / 60))} min ago"
        return f"The code is {got['code']} (from {got.get('sender') or 'an unknown sender'}, {when}). Digit by digit: {' '.join(str(got['code']))}."
    if not got.get("connected"):
        return f"His texts can't be read yet: {_clean(got.get('why') or 'Google is not connected', 200)}"
    return "No recent code: nothing with a verification code has come in his texts in the last five minutes." + (f" ({_clean(got['why'], 120)})" if got.get("why") else "")


# ── the Captain's Spotify: skippy-pc's Spotify speaker and his sign-in, in the spotify container (services/spotify) ───────────────────────────────────
SPOTIFY_URL = os.environ.get("PURPLE_SPOTIFY_URL", "http://127.0.0.1:43873").rstrip("/")
SPOTIFY_URI = re.compile(r"spotify:(?:track|album|playlist|artist):[A-Za-z0-9]{22}")


def _spotify_request(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    req = urllib.request.Request(SPOTIFY_URL + path, data=json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None),
                                 method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read(2_000_000) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(200_000) or b"{}")
        except ValueError:
            return e.code, {}


def spotify(args: dict, request=None) -> str:
    """Search his Spotify, play on the house stereo (the "Skippy" speaker; the hub routes it to the Sony), his playlists, add to one, like."""
    request = request or _spotify_request
    action = str(args.get("action") or "").strip().lower()
    q = str(args.get("query") or "").strip()

    def ok(method, path, body=None):
        code, got = request(method, path, body)
        if code != 200:
            raise ValueError("spotify: " + str(got.get("error") or got.get("detail") or f"the Spotify service answered {code}"))
        return got

    def line(t: dict) -> str:
        return f"{t.get('artist') or t.get('by') or '?'} - {t.get('title') or t.get('name') or '?'}  {t.get('uri', '')}"

    if action == "search":
        kind = str(args.get("type") or "track")
        got = ok("GET", "/search?" + urllib.parse.urlencode({"q": q, "type": kind, "n": 10}))
        return "\n".join(line(t) for t in got.get("items") or []) or f"Spotify found nothing for {q!r}."
    if action == "play":
        uri = q if SPOTIFY_URI.fullmatch(q) else ""
        if not uri:
            tracks = ok("GET", "/tracks?" + urllib.parse.urlencode({"what": q, "n": 10})).get("tracks") or []
            if not tracks:
                return f"Spotify found nothing for {q!r}."
            uri = tracks[0]["uri"]
        ok("POST", "/play", {"uri": uri})
        return f"Playing {uri} on the house stereo (the Skippy speaker)."
    if action in ("pause", "resume"):
        ok("POST", "/" + action)
        return f"Done: {action}."
    if action == "now":
        st = ok("GET", "/now")
        if not st.get("logged_in"):
            return "The Spotify speaker is not logged in yet (services/spotify/signin.py)."
        return (("Playing" if st.get("playing") else "Paused" if st.get("paused") else "Stopped") +
                (f": {st.get('artist')} - {st.get('title')} {st.get('uri')}" if st.get("uri") else ""))
    if action == "playlists":
        return "\n".join(f"{p['name']} ({p.get('count', 0)} tracks{', his' if p.get('mine') else ''}) {p.get('uri', '')}"
                         for p in ok("GET", "/playlists").get("playlists") or []) or "He has no playlists."
    if action == "playlist":
        got = ok("GET", "/playlist?" + urllib.parse.urlencode({"name": q}))
        return f"{got['playlist'].get('name')}:\n" + "\n".join(line(t) for t in (got.get("tracks") or [])[:100])
    if action in ("add", "like"):
        uri = str(args.get("uri") or "").strip()
        if not SPOTIFY_URI.fullmatch(uri):
            st = ok("GET", "/now")
            uri = st.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            raise ValueError("spotify: give a spotify:track: uri (search first), or play something")
        if action == "like":
            ok("POST", "/save", {"uris": [uri]})
            return f"Saved {uri} to his liked songs."
        got = ok("POST", "/playlist/add", {"playlist": str(args.get("playlist") or q), "uris": [uri]})
        return f"Added {uri} to his playlist {(got.get('playlist') or {}).get('name')}."
    raise ValueError("spotify's action is search, play, pause, resume, now, playlists, playlist, add or like")


TOOLS: dict[str, dict] = {
    "bilby_favorite": {
        "description": "Bilby (the Captain's music): add the song he is listening to RIGHT NOW (on any of his Bilby players or room pods) to his DJ favorites "
                       "list, or list the favorites. Use when he says 'add this song to my favorites', 'favorite this', 'what's on my favorites'.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "list"]}}},
        "run": bilby_favorite},
    "spotify_account": {
        "description": "The Captain's Spotify (Premium). search (query, type track|artist|album|playlist), play (a spotify: URI, or words: plays the best "
                       "match on the house stereo, the 'Skippy' speaker), pause, resume, now (what plays), playlists (his), playlist (query = its name: its "
                       "tracks), add (uri, or the song playing now, to playlist), like (save uri, or the song playing now, to his liked songs). For his own "
                       "music library use Bilby (bilby_favorite and the portal), not this.",
        "input_schema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["search", "play", "pause", "resume", "now", "playlists", "playlist", "add", "like"]},
            "query": {"type": "string", "description": "search words, a spotify: URI to play, or a playlist's name"},
            "type": {"type": "string", "enum": ["track", "artist", "album", "playlist"]},
            "uri": {"type": "string", "description": "add/like: a spotify:track: URI (default: the song playing now)"},
            "playlist": {"type": "string", "description": "add: the playlist's name"}}, "required": ["action"]},
        "run": spotify},
    "vision_flash": {
        "description": "Skippy-vision: save a MOMENT of what the house and this PC show right now, the screens of the room pods and of this PC's monitors "
                       "(the Captain's glasses and phone join only when he presses the camera key himself). They arrive in about 30 seconds; look at them "
                       "with vision_moment. Use it to see what is on his screens, or to keep proof of how something looked.",
        "input_schema": {"type": "object", "properties": {}},
        "run": vision_flash},
    "vision_moment": {
        "description": "Skippy-vision: list the latest moments (what was saved, when, how, what was said or read in them), or give an `id` to save that "
                       "moment's pictures into .purple-out/ where you can Read them (the glasses' three cameras, the phone's, the pods' and the PC's screens).",
        "input_schema": {"type": "object", "properties": {
            "id": {"type": "string", "description": "a moment's id, like 20261102-210000 (omit to list the latest)"},
            "limit": {"type": "integer", "description": "when listing: how many, 1 to 10 (default 5)"}}},
        "run": vision_moment},
    "texts_latest_code": {
        "description": "His text messages (Google Voice, read through his Gmail): the verification code from a text that arrived in the last five minutes, and who "
                       "sent it. Use it when he says 'what's my code', 'read me the code' or is signing in somewhere and waiting for one. Says 'no recent code' "
                       "when there is none, and why when his texts cannot be read.",
        "input_schema": {"type": "object", "properties": {}},
        "run": texts_latest_code},
}


def tool_list() -> list[dict]:
    return [{"name": n, "description": t["description"], "inputSchema": t["input_schema"]} for n, t in TOOLS.items()]


def call(name: str, args: dict) -> tuple[str, bool]:
    """(text, isError): a failure is words, never a raise."""
    try:
        return str(TOOLS[name]["run"](args or {})), False
    except Exception as e:                                                  # noqa: BLE001 — every failure is said in words
        return str(e) or type(e).__name__, True
