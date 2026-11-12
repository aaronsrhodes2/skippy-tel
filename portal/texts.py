"""Texts: Google Voice's text messages, read from Gmail (the Captain, 2026-11-02: his phone goes Wi-Fi only, so texts and the codes in them
have to reach him through the portal, and the Tesla's own cellular).

Google Voice forwards every text to his Gmail (Voice > Settings > Messages > Forward messages to email) as a mail from an address at
txt.voice.google.com. This reads those mails with the read-only grant that "Connect Google for Skippy" already holds (google_link.py:
gmail.readonly). It sends nothing, keeps nothing (Gmail has its own copy; this only remembers what it parsed, in memory) and asks Gmail
again at most every CACHE_S seconds.

  GET /texts            (admin) the page: his texts, newest first, a verification code in each one a tap to copy
  GET /texts/state      (admin) the same as JSON (the page, /hud and /drive poll it)
  GET /home/texts/code  (home token) purple Skippy's `texts_latest_code`: the code that arrived in the last five minutes, if any

`state()` never raises: Google not connected, mail not allowed, or Gmail not answering is a plain {connected, why} for the page to show.
"""
from __future__ import annotations

import base64
import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

from flask import Blueprint, abort, jsonify, render_template, request

import auth

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
QUERY = "from:txt.voice.google.com newer_than:7d"
LIST_MAX = 20            # texts kept and shown
CACHE_S = 30             # Gmail is asked again no sooner than this
FRESH_S = 300            # a code is "new" for five minutes
BODY_MAX = 600
CODE_NEAR = 80           # a code within this many characters of a word like "code" or "verify"
SHORT_BODY = 80          # a body this short with one number in it is a code even without a word to say so
ID = re.compile(r"[0-9a-f]{8,32}")

# ── reading one mail ────────────────────────────────────────────────────────────────────────────────────────────
_SUBJECT = re.compile(r"(?i)^\s*(?:(?:re|fwd?):\s*)*(?:new\s+)?(?:text\s+|group\s+)?message\s+from\s+(.+?)\s*$")
_NUMBER = re.compile(r"\(?\+?\d[\d\s().\-]{2,}\d\)?")
_TAIL = re.compile(r"(?im)^[ \t]*(?:sent using (?:google voice|sms-to-email)|to respond to this text message|reply to this email|https?://voice\.google\.com)")


def _unwrap(number: str) -> str:
    """'(+1 555-123-4567)' is the subject's own brackets; '(555) 123-4567' is the number's: only the first are taken off."""
    n = number.strip()
    return n[1:-1].strip() if n.startswith("(") and n.endswith(")") and n.count("(") == 1 and n.count(")") == 1 else n


def sender_of(subject: str, from_header: str = "") -> dict:
    """{name, number, sender} from 'New text message from Alice Smith (+1 555-123-4567)', 'New text message from 22000' and the like;
    the mail's own From name is the fallback."""
    match = _SUBJECT.match(subject or "")
    who = match.group(1) if match else ""
    found = _NUMBER.search(who)
    number = _unwrap(found.group(0)) if found else ""
    name = re.sub(r"\s+", " ", _NUMBER.sub("", who)).strip(" ()-")
    if not name and not number:
        name = re.sub(r"\s*<[^>]*>", "", from_header or "").strip().strip('"')
        found = _NUMBER.search(name)
        if found:
            number, name = _unwrap(found.group(0)), re.sub(r"\s+", " ", _NUMBER.sub("", name)).strip(" ()-")
    return {"name": name, "number": number, "sender": name or number or "unknown"}


def cut_footer(text: str) -> str:
    """The text itself, without Voice's footer ('-- / Sent using SMS-to-email. Reply to this email ...')."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    tail = _TAIL.search(text)
    if not tail:
        return text.strip()
    return re.sub(r"(?:\n[ \t]*--[ \t]*)?\s*$", "", text[:tail.start()]).strip()


def _walk(part: dict):
    yield part
    for sub in part.get("parts") or []:
        if isinstance(sub, dict):
            yield from _walk(sub)


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _plain(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def body_of(payload: dict) -> str:
    """The message's text: its text/plain part, else its HTML part with the tags taken off."""
    parts = list(_walk(payload or {}))
    for want in ("text/plain", "text/html"):
        for part in parts:
            data = (part.get("body") or {}).get("data")
            if str(part.get("mimeType") or "").lower() == want and data:
                try:
                    text = _b64(data)
                except ValueError:
                    continue
                return _plain(text) if want == "text/html" else text
    return ""


# ── the code in a text ──────────────────────────────────────────────────────────────────────────────────────────
_KEYWORD = re.compile(r"(?i)\b(?:code|verification|verify|otp|passcode|pin|password)\b|one[- ]time")
# what looks like a long number but is not a code: a phone number, a date, money, a percentage, a link (blanked out, same length)
_NOT_A_CODE = re.compile(r"""(?x)
    (?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}          # a phone number
  | \b\d{3}[\s.\-]\d{4}\b                                                        # a 7-digit local number
  | \b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b | \b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b       # a date
  | [$€£]\s?\d[\d,]*(?:\.\d+)?                                          # money
  | \d+(?:\.\d+)?%                                                               # a percentage
  | https?://\S+                                                                 # a link
""")
_RUN = re.compile(r"(?<!\d)(?<!\d[.,])\d{4,8}(?!\d)(?![.,]\d)")
_SPLIT = re.compile(r"(?<!\d)(\d{3})[ \-](\d{3})(?!\d)")                         # '123 456' / '123-456', taken as a code only beside a keyword
_YEAR = re.compile(r"(?:19|20)\d\d")
_UNIT = re.compile(r"(?i)\s?(?:am|pm|a\.m|p\.m|st|nd|rd|th|km|mi|kg|lbs?|hrs?|hours?|min|mins|minutes?|dollars?|usd)\b")


def code_of(body: str) -> str:
    """The verification code in a text, or ''. A 4-8 digit run within CODE_NEAR characters of a word like code / verification / verify /
    OTP / passcode / PIN wins (the nearest one; with such a word even 2024 is a code); without one, a lone run in a short body is
    taken, unless it is a year or has a unit after it. A phone number, a date, an amount or a link never is."""
    text = re.sub(r"\s+", " ", (body or "").replace(" ", " "))
    flat = _NOT_A_CODE.sub(lambda m: " " * len(m.group()), text)
    runs = [(m.start(), m.end(), m.group()) for m in _RUN.finditer(flat)]
    runs += [(m.start(), m.end(), m.group(1) + m.group(2)) for m in _SPLIT.finditer(flat)]
    words = [(m.start(), m.end()) for m in _KEYWORD.finditer(flat)]
    best = None
    for start, end, digits in runs:
        near = min((max(0, start - w_end, w_start - end) for w_start, w_end in words), default=None)
        if near is not None and near <= CODE_NEAR and (best is None or (near, start) < best[:2]):
            best = (near, start, digits)
    if best:
        return best[2]
    lone = [r for r in runs if len(r[2]) == r[1] - r[0]]                         # (a split '123 456' only counts beside a keyword)
    if len(flat.strip()) <= SHORT_BODY and len(lone) == 1 and not _YEAR.fullmatch(lone[0][2]) and not _UNIT.match(flat, lone[0][1]):
        return lone[0][2]
    return ""


# ── Gmail ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _get(url: str, token: str, params: dict | None = None, timeout: float = 15.0) -> tuple[int, dict]:
    import requests                                                   # noqa: PLC0415
    r = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


def _date_of(header: str) -> int:
    try:
        return int(parsedate_to_datetime(header).timestamp())
    except (TypeError, ValueError, IndexError):
        return 0


class Texts:
    """`link` is google_link.Link (or a function returning it: the portal builds this before the link exists); `http(url, token, params)`
    answers (status, json) and is replaced in tests."""

    def __init__(self, link, http=_get, clock=time.time):
        self.link, self.http, self.clock = link, http, clock
        self._lock = threading.Lock()
        self._cache: tuple[float, dict] | None = None
        self._parsed: dict[str, dict] = {}                            # a mail never changes: parsed once, forgotten when it leaves the 7 days
        self._last: list[dict] = []                                   # the last good list, shown again if Gmail stumbles

    # ── asking Google ──
    def _token(self) -> tuple[str, str]:
        link = self.link() if callable(self.link) else self.link
        if link is None:
            return "", "Google isn't set up on this portal"
        try:
            got = link.access()
        except Exception:                                             # noqa: BLE001 — never to the page
            return "", "Google didn't answer"
        if not got.get("connected"):
            return "", str(got.get("why") or "Google isn't connected: press Connect Google for Skippy in Admin")
        scopes = [str(s) for s in got.get("scopes") or []]
        if not any(s.endswith("gmail.readonly") or s.rstrip("/").endswith("mail.google.com") for s in scopes):
            return "", "Google wasn't allowed to read mail: press Connect Google for Skippy in Admin and leave the Gmail box ticked"
        return str(got["access_token"]), ""

    def _message(self, token: str, mid: str) -> dict | None:
        try:
            status, got = self.http(f"{GMAIL}/messages/{mid}", token, {"format": "full"})
        except Exception:                                             # noqa: BLE001 — one mail failing leaves the others
            return None
        if status != 200 or not isinstance(got, dict):
            return None
        payload = got.get("payload") or {}
        head = {str(h.get("name") or "").lower(): str(h.get("value") or "") for h in payload.get("headers") or [] if isinstance(h, dict)}
        who = sender_of(head.get("subject", ""), head.get("from", ""))
        body = cut_footer(body_of(payload))
        try:
            at = int(got.get("internalDate") or 0) // 1000
        except (TypeError, ValueError):
            at = 0
        return {"id": mid, "from": who["sender"], "number": who["number"], "at": at or _date_of(head.get("date", "")),
                "body": re.sub(r"\s+", " ", body).strip()[:BODY_MAX], "code": code_of(body)}

    def _read(self) -> dict:
        """One look at Gmail. Never raises."""
        now = self.clock()
        token, why = self._token()
        if not token:
            return {"connected": False, "why": why, "texts": [], "at": now}
        try:
            status, listing = self.http(f"{GMAIL}/messages", token, {"q": QUERY, "maxResults": LIST_MAX})
            if status in (401, 403):
                return {"connected": False, "why": ("Gmail refused the mail: press Connect Google for Skippy in Admin again and leave the Gmail box ticked"
                                                   if status == 403 else "Google refused the token: it will be renewed, try again in a minute"),
                        "texts": [], "at": now}
            if status != 200:
                raise RuntimeError(f"Gmail answered {status}")
            ids = [m["id"] for m in listing.get("messages") or [] if isinstance(m, dict) and ID.fullmatch(str(m.get("id") or ""))]
            new = [i for i in ids if i not in self._parsed]
            if len(new) > 2:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    got = list(pool.map(lambda i: self._message(token, i), new))
            else:
                got = [self._message(token, i) for i in new]
            for mid, text in zip(new, got):
                if text is not None:
                    self._parsed[mid] = text
            self._parsed = {i: self._parsed[i] for i in ids if i in self._parsed}
            self._last = sorted(self._parsed.values(), key=lambda t: t["at"], reverse=True)[:LIST_MAX]
            return {"connected": True, "why": "", "texts": self._last, "at": now}
        except Exception:                                             # noqa: BLE001 — Gmail stumbled: the last good list, and a word
            return {"connected": True, "why": "Gmail didn't answer just now", "texts": self._last, "at": now}

    # ── what the page, /hud, /drive and purple see ──
    @staticmethod
    def _fresh(texts: list[dict], now: float) -> dict | None:
        for t in texts:                                               # newest first
            if t["code"] and 0 <= now - t["at"] < FRESH_S:
                return {"code": t["code"], "sender": t["from"], "at": t["at"], "age_s": int(now - t["at"])}     # (age_s: the page need not trust its own clock)
        return None

    def state(self) -> dict:
        """{connected, why, texts: [{id, from, number, at, body, code}], fresh: {code, sender, at, age_s} | None, at}. Cached CACHE_S seconds;
        `fresh` is worked out at the moment of asking, so a code stops being new on time."""
        now = self.clock()
        with self._lock:
            if self._cache is None or now - self._cache[0] >= CACHE_S:
                self._cache = (now, self._read())
            seen = self._cache[1]
        texts = [dict(t) for t in seen["texts"]]
        return {"connected": seen["connected"], "why": seen["why"], "texts": texts, "fresh": self._fresh(texts, now), "at": int(seen["at"])}

    def latest_code(self) -> dict:
        """For purple: the code that came in the last five minutes ({code, sender, at, age_s}), or {code: None}."""
        got = self.state()
        base = {"connected": got["connected"], "why": got["why"]}
        fresh = got["fresh"]
        if not fresh:
            return {**base, "code": None}
        return {**base, **fresh}


def init_app(app, home, link) -> Texts:
    texts = Texts(link)
    app.extensions["texts"] = texts
    bp = Blueprint("texts", __name__)

    @bp.get("/texts")
    @auth.admin_required
    def page():
        return render_template("texts.html")

    @bp.get("/texts/state")
    @auth.admin_required
    def state():
        resp = jsonify(texts.state())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/home/texts/code")
    def home_code():
        """purple Skippy's door: the home token is the key; no cookie of any user opens it."""
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        resp = jsonify(texts.latest_code())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app.register_blueprint(bp)
    return texts
