"""The Captain's Google calendar, mail and contacts for the morning and bedtime routines (daily.py), READ ONLY, on skippy-pc.

He grants it once, with the Maker Portal's own Google sign-in (2026-11-01, the Captain: "the exact same sign in as makerportal.live's admin"):
Admin > Google > Connect Google for Skippy (portal/google_link.py). The portal keeps the refresh token in its private bucket; this machine
borrows an hour's access token at a time from GET /home/google/access with the home token (SKIPPY_HOME_TOKEN, as the home reporter). His
password never passes through here. The scopes are read-only: calendar.readonly, gmail.readonly, contacts.readonly, tasks.readonly. Mail is
read only to be summed up by Core (the local model); nothing is sent anywhere else.

The old local grant still works as a fallback: when GOOGLE_HOME_CLIENT (a "Desktop app" OAuth client JSON) and GOOGLE_HOME_TOKEN both exist
in the vault folder, they are used instead of the portal (`python google_home.py connect` makes that token).

    python google_home.py check       what it can see: calendars (and whether the work one is shared in), counts of today's events and mail
    python google_home.py connect     the local fallback's one-time sign-in (opens the browser); normally not needed

Settings (vault .env): PORTAL_URL (default the portal's run.app address), SKIPPY_HOME_TOKEN, GOOGLE_WORK_CALENDAR (his work address, to find
the work calendar), SKIPPY_TZ (his time zone, default America/Los_Angeles); the fallback's GOOGLE_HOME_CLIENT and GOOGLE_HOME_TOKEN.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("SKIPPY_VAULT_DIR", r"G:\My Drive\vault"))
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/contacts.readonly", "https://www.googleapis.com/auth/tasks.readonly"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL = "https://www.googleapis.com/calendar/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
PEOPLE = "https://people.googleapis.com/v1"
TASKS = "https://tasks.googleapis.com/tasks/v1"
PORTAL = "https://skippy-portal-1015050363686.us-west1.run.app"     # machines use the run.app address (never DNS or a certificate)
CONNECT_HINT = "press Connect Google for Skippy in Admin on the portal"

BILL_RE = re.compile(r"\b(?:bill|invoice|statement (?:is )?(?:ready|available)|payment (?:due|reminder|failed|declined)|amount due|due date|autopay|past due|"
                     r"your (?:receipt|payment)|renewal)\b", re.I)
PACKAGE_RE = re.compile(r"\b(?:out for delivery|arriving today|arrives today|delivered today|will be delivered today|delivery today|"
                        r"expected (?:delivery )?today|arriving (?:by|before) \d)", re.I)
SHIP_FROM = re.compile(r"amazon|ups|fedex|usps|dhl|ontrac|lasership|shipment|tracking|delivery", re.I)


class NotConnected(Exception):
    """He hasn't granted access yet (no token), or the grant was withdrawn."""


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:                      # noqa: S310 — the portal's own address
        return json.loads(r.read())


def _post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:                      # noqa: S310 — Google's own token endpoint
        return json.loads(r.read())


class Google:
    def __init__(self, env: dict | None = None, opener=None, post_form=_post_form, clock=time.time, log=print, get_json=_get_json):
        env = env if env is not None else os.environ
        self.client_path = Path(env.get("GOOGLE_HOME_CLIENT") or VAULT / "google-home-client.json")
        self.token_path = Path(env.get("GOOGLE_HOME_TOKEN") or VAULT / "google-home-token.json")
        self.work = (env.get("GOOGLE_WORK_CALENDAR") or "").strip().lower()
        self.portal = (env.get("PORTAL_URL") or PORTAL).rstrip("/")
        self.home_token = (env.get("SKIPPY_HOME_TOKEN") or "").strip()
        self.opener, self.post_form, self.clock, self.log, self.get_json = opener, post_form, clock, log, get_json
        self.post_json = None                                            # tests: (url, body) -> dict, like opener for GETs
        self.work_busy: bool | None = None                               # a free/busy-only work calendar: readable (None: not asked yet)
        self._access: tuple[str, float] | None = None
        self._portal_no: tuple[str, float] | None = None
        self.nest_project = (env.get("NEST_PROJECT_ID") or "").strip()                 # the portal's "not connected" (why, until when): asked again after a minute
        self._contacts: tuple[float, dict] | None = None

    # ── the grant ──
    def client(self) -> dict:
        data = json.loads(self.client_path.read_text(encoding="utf-8"))
        return data.get("installed") or data.get("web") or data

    @property
    def local(self) -> bool:
        """The fallback: a Desktop client and its token in the vault folder. Without both, the portal's grant is used."""
        return self.client_path.is_file() and self.token_path.is_file()

    @property
    def connected(self) -> bool:
        """The local grant, or the portal says it holds one. A portal that can't be reached counts as connected (unknown): the read
        that follows fails and is logged, rather than telling him to connect what is already connected."""
        if self.local:
            return True
        try:
            self.access_token()
        except NotConnected:
            return False
        except Exception:                                               # noqa: BLE001 — the portal or Google down: not his to fix
            return True
        return True

    def _from_portal(self) -> str:
        if self._portal_no and self._portal_no[1] > self.clock():
            raise NotConnected(self._portal_no[0])
        if not self.home_token:
            raise NotConnected("no SKIPPY_HOME_TOKEN to ask the portal with")
        try:
            got = self.get_json(f"{self.portal}/home/google/access", {"Authorization": f"Bearer {self.home_token}"})
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise NotConnected("the portal refused the home token") from e
            raise
        if not got.get("connected") or not got.get("access_token"):
            if got.get("error"):
                raise RuntimeError(f"the portal couldn't refresh the Google token: {got['error']}")
            why = str(got.get("why") or "not connected")
            self._portal_no = (why, self.clock() + 60)
            raise NotConnected(why)
        self._portal_no = None
        self.nest_project = str(got.get("nest_project") or "")             # the Nest's Device Access project (nest.py), set in Admin
        self._access = (got["access_token"], self.clock() + float(got.get("expires_in") or 600))
        return self._access[0]

    def connect(self, open_browser=webbrowser.open) -> None:
        """The one-time sign-in: a loopback page on this PC catches Google's answer (PKCE, no secret typed anywhere)."""
        c = self.client()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        got: dict = {}

        class Catch(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
                if q.get("state") == state:
                    got.update(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<p>Skippy has it. You can close this tab.</p>" if "code" in q else b"<p>No access was granted.</p>")

            def log_message(self, *a):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Catch)
        redirect = f"http://127.0.0.1:{server.server_port}"
        url = AUTH_URL + "?" + urllib.parse.urlencode({"client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code",
                                                       "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state,
                                                       "code_challenge": challenge, "code_challenge_method": "S256"})
        threading.Thread(target=server.handle_request, daemon=True).start()
        print("Opening Google's sign-in page. If it doesn't open, paste this into the browser:\n" + url, flush=True)
        open_browser(url)
        for _ in range(600):
            if got:
                break
            time.sleep(1)
        server.server_close()
        if "code" not in got:
            raise NotConnected(got.get("error") or "no answer from the sign-in page within ten minutes")
        tok = self.post_form(TOKEN_URL, {"code": got["code"], "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                                         "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
        if not tok.get("refresh_token"):
            raise NotConnected("Google gave no refresh token")
        self.token_path.write_text(json.dumps({"refresh_token": tok["refresh_token"], "scope": tok.get("scope", ""), "granted": int(self.clock())}),
                                   encoding="utf-8")

    def access_token(self) -> str:
        if self._access and self._access[1] > self.clock() + 60:
            return self._access[0]
        if not self.local:
            return self._from_portal()
        c, t = self.client(), json.loads(self.token_path.read_text(encoding="utf-8"))
        try:
            tok = self.post_form(TOKEN_URL, {"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                                             "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
        except urllib.error.HTTPError as e:
            if e.code in (400, 401):
                raise NotConnected("Google refused the token (access was withdrawn, or it expired)") from e
            raise
        self._access = (tok["access_token"], self.clock() + float(tok.get("expires_in", 3600)))
        return self._access[0]

    def get(self, url: str, params: dict | None = None) -> dict:
        full = url + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        if self.opener is not None:
            return self.opener(full)
        req = urllib.request.Request(full, headers={"Authorization": f"Bearer {self.access_token()}"})
        with urllib.request.urlopen(req, timeout=20) as r:                  # noqa: S310 — Google's APIs
            return json.loads(r.read())

    # ── calendar ──
    def calendars(self) -> list[dict]:
        return [c for c in self.get(f"{CAL}/users/me/calendarList").get("items") or [] if isinstance(c, dict)]

    def work_calendar(self, cals: list[dict] | None = None) -> dict | None:
        """The work calendar in his list, if it is shared in: GOOGLE_WORK_CALENDAR's address, else the one other person's calendar on a
        company domain (not gmail.com, not a group or holiday calendar)."""
        cals = cals if cals is not None else self.calendars()
        if self.work:
            # Not in his list: a work domain often shares only free/busy with an outside account (JFrog, 2026-11-01), so the times are
            # read through the freeBusy API (busy()), with no titles.
            return next((c for c in cals if str(c.get("id", "")).lower() == self.work),
                        {"id": self.work, "summary": "Work", "busy_only": True})
        others = [c for c in cals if not c.get("primary") and "@" in str(c.get("id")) and not re.search(
            r"gmail\.com$|group\.calendar\.google\.com$|group\.v\.calendar\.google\.com$|import\.calendar\.google\.com$", str(c.get("id")))]
        return others[0] if len(others) == 1 else None

    def events(self, day: date, days: int = 1) -> list[dict]:
        """Every event on the calendars he shows (selected), in order: {start, end, all_day, summary, location, calendar, work}."""
        start = datetime(day.year, day.month, day.day).astimezone()
        end = start + timedelta(days=days)
        cals = self.calendars()
        work = self.work_calendar(cals)
        out = []
        for c in cals:
            if not (c.get("selected") or c.get("primary")) or re.search(r"#holiday@|#contacts@", str(c.get("id"))):
                continue
            items = self.get(f"{CAL}/calendars/{urllib.parse.quote(c['id'], safe='')}/events",
                             {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime", "maxResults": 50})
            for e in items.get("items") or []:
                if e.get("status") == "cancelled" or any(a.get("self") and a.get("responseStatus") == "declined" for a in e.get("attendees") or []):
                    continue
                s, en = e.get("start") or {}, e.get("end") or {}
                out.append({"start": s.get("dateTime") or s.get("date"), "end": en.get("dateTime") or en.get("date"), "all_day": "date" in s,
                            "summary": e.get("summary") or "(busy)", "location": e.get("location") or "",
                            "calendar": c.get("summaryOverride") or c.get("summary") or c["id"], "work": work is not None and c["id"] == work.get("id")})
        if work and work.get("busy_only"):
            blocks = self.busy(work["id"], start, end)
            self.work_busy = blocks is not None
            out += [{"start": _local(b["start"]), "end": _local(b["end"]), "all_day": False, "summary": "busy (a work meeting; the title isn't shared)",
                     "location": "", "calendar": "Work", "work": True} for b in blocks or []]
        seen, kept = set(), []
        for e in sorted(out, key=lambda e: (not e["all_day"], _local(e["start"] or ""))):      # one order whatever zone Google wrote it in
            key = (e["summary"].strip().lower(), _local(e["start"] or ""))
            if key not in seen:                                         # the same event on two of his calendars is said once
                seen.add(key)
                kept.append(e)
        return kept

    def busy(self, cal: str, start: datetime, end: datetime) -> list[dict] | None:
        """A calendar's busy blocks ({start, end}) through the freeBusy API: all a free/busy-only share allows. None: not even that is shared."""
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": cal}]}
        if self.post_json is not None:
            r = self.post_json(f"{CAL}/freeBusy", body)
        else:
            req = urllib.request.Request(f"{CAL}/freeBusy", data=json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:          # noqa: S310 — Google's APIs
                r = json.loads(resp.read())
        c = (r.get("calendars") or {}).get(cal) or {}
        return None if c.get("errors") or not c else [b for b in c.get("busy") or [] if b.get("start") and b.get("end")]

    # ── contacts ──
    def contacts(self) -> dict:
        """{"emails": set of his contacts' addresses, "birthdays": [(name, month, day)]}, cached an hour."""
        if self._contacts and self.clock() - self._contacts[0] < 3600:
            return self._contacts[1]
        emails, birthdays, token = set(), [], None
        for _ in range(20):
            params = {"personFields": "names,emailAddresses,birthdays", "pageSize": 1000}
            if token:
                params["pageToken"] = token
            page = self.get(f"{PEOPLE}/people/me/connections", params)
            for p in page.get("connections") or []:
                name = ((p.get("names") or [{}])[0]).get("displayName") or ""
                emails |= {str(e.get("value", "")).lower() for e in p.get("emailAddresses") or [] if e.get("value")}
                for b in p.get("birthdays") or []:
                    d = b.get("date") or {}
                    if d.get("month") and d.get("day") and name:
                        birthdays.append((name, d["month"], d["day"]))
                        break
            token = page.get("nextPageToken")
            if not token:
                break
        self._contacts = (self.clock(), {"emails": emails, "birthdays": birthdays})
        return self._contacts[1]

    def birthdays(self, day: date) -> list[str]:
        return sorted({n for n, m, d in self.contacts()["birthdays"] if (m, d) == (day.month, day.day)})

    # ── mail ──
    def mail(self, query: str = "newer_than:1d in:inbox -category:promotions -category:social -category:forums", limit: int = 40) -> list[dict]:
        """The last day's inbox (no promotions or social): {from, address, subject, snippet, kind}, kind in bill / personal / package / other."""
        ids = [m["id"] for m in (self.get(f"{GMAIL}/messages", {"q": query, "maxResults": limit}).get("messages") or [])]
        known = self.contacts()["emails"]
        out = []
        for mid in ids:
            m = self.get(f"{GMAIL}/messages/{mid}", {"format": "metadata", "metadataHeaders": ["From", "Subject"]})
            h = {x["name"].lower(): x["value"] for x in (m.get("payload") or {}).get("headers") or []}
            sender = h.get("from", "")
            addr = (re.search(r"<([^>]+)>", sender) or re.search(r"(\S+@\S+)", sender) or [None, ""])[1].lower()
            name = re.sub(r"\s*<[^>]*>", "", sender).strip().strip('"') or addr
            subject, snippet = h.get("subject", ""), re.sub(r"\s+", " ", m.get("snippet") or "")[:300]
            text = f"{subject} {snippet}"
            kind = ("package" if PACKAGE_RE.search(text) and (SHIP_FROM.search(sender) or SHIP_FROM.search(text)) else
                    "bill" if BILL_RE.search(text) else "personal" if addr in known else "other")
            out.append({"from": name, "address": addr, "subject": subject, "snippet": snippet, "kind": kind})
        return out

    # ── tasks ──
    def todos(self, due_by: date | None = None) -> list[str]:
        out = []
        for tl in (self.get(f"{TASKS}/users/@me/lists").get("items") or [])[:10]:
            for t in self.get(f"{TASKS}/lists/{tl['id']}/tasks", {"showCompleted": "false", "maxResults": 100}).get("items") or []:
                due = (t.get("due") or "")[:10]
                if t.get("title") and (due_by is None or not due or due <= due_by.isoformat()):
                    out.append(t["title"] + (f" (due {due})" if due else ""))
        return out


def _local(iso: str) -> str:
    """A time in his own zone (an all-day date is left as it is), so times from different calendars sort together."""
    if "T" not in iso:
        return iso
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().isoformat()
    except ValueError:
        return iso


def _day_of(day, log=print) -> date | None:
    """A day given as a date, a datetime or "YYYY-MM-DD"; None when none was given, or it cannot be read (the caller then asks about tomorrow)."""
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    if day:
        try:
            return date.fromisoformat(str(day).strip()[:10])
        except ValueError:
            log(f"(google: {day!r} is not a day: asking about tomorrow)")
    return None


def clock_of(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return iso


def describe_events(events: list[dict]) -> str:
    lines = []
    for e in events:
        when = "all day" if e["all_day"] else f"{clock_of(e['start'])} to {clock_of(e['end'])}"
        lines.append(f"{when}: {e['summary']}" + (f" at {e['location']}" if e["location"] else "") + (" (work)" if e["work"] else f" ({e['calendar']})"))
    return "\n".join(lines)


def extras(g: Google, word, log=print) -> dict:
    """daily.Daily's extras from his Google account: each an async () -> words (or None when there is nothing to say; first_tomorrow() -> a timestamp, and takes
    the morning's date). `word(task, material, max_tokens)` is Daily.aword (Core)."""
    import asyncio                                                      # noqa: PLC0415

    async def run(fn, *a):
        try:
            return await asyncio.to_thread(fn, *a)
        except NotConnected as e:
            log(f"(google: not connected: {e})")
            return None
        except Exception as e:                                          # noqa: BLE001 — one service down never spoils the routine
            log(f"(google: {type(e).__name__}: {str(e)[:120]})")
            return None

    def today() -> date:
        return datetime.fromtimestamp(g.clock()).date()

    async def calendar():
        if not await asyncio.to_thread(lambda: g.connected):
            return f"I can't see your calendar or your mail yet: Google isn't connected. To fix it, {CONNECT_HINT}."
        events = await run(g.events, today())
        if events is None:
            return None
        cals = await run(g.calendars) or []
        work = g.work_calendar(cals)
        if not work or (work.get("busy_only") and g.work_busy is False):
            note = ("\n(His work calendar is NOT shared into this account, so work meetings are missing: say so in one "
                    "sentence, and that the steps to share it are in the Skippy notes.)")
        elif work.get("busy_only"):
            note = "\n(Work meetings show only as busy times: their titles aren't shared. Call each one 'a work meeting'.)"
        else:
            note = ""
        if not events:
            return await word("Tell him his calendar is clear today.", "No events today." + note, 120)
        return await word("Today's calendar: walk him through it in order, briefly; point out the first event and anything off-site.",
                          describe_events(events) + note, 350)

    async def mail():
        msgs = await run(g.mail)
        if msgs is None:
            return None
        keep = [m for m in msgs if m["kind"] in ("bill", "personal")]
        if not keep:
            return "Nothing in the mail that matters: no bills, and nobody you know wrote."
        material = "\n".join(f"[{m['kind']}] from {m['from']}: {m['subject']} -- {m['snippet']}" for m in keep[:15])
        return await word("The mail that matters since yesterday: the bills (who, and what they want) and personal mail from people he knows "
                          "(who, and what about). One sentence each at most; never read out an account number or a full amount of money "
                          "unless the snippet says it is due.", material, 400)

    async def packages():
        msgs = await run(g.mail, "newer_than:3d in:inbox", 60)
        if not msgs:
            return None
        pk = [m for m in msgs if m["kind"] == "package"]
        if not pk:
            return None
        return await word("Packages arriving today, from these shipping emails: what and from whom, briefly.",
                          "\n".join(f"{m['from']}: {m['subject']} -- {m['snippet']}" for m in pk[:8]), 200)

    async def birthdays():
        names = await run(g.birthdays, today())
        if not names:
            return None
        return f"Birthday{'s' if len(names) > 1 else ''} today: {', '.join(names)}. Call them, monkey."

    async def tomorrow():
        tm = today() + timedelta(days=1)
        events = await run(g.events, tm)
        todos = await run(g.todos, tm)
        parts = []
        if events:
            parts.append("Tomorrow's calendar:\n" + describe_events(events[:6]))
        if todos:
            parts.append("His to-dos due by tomorrow:\n" + "\n".join(todos[:10]))
        return "\n\n".join(parts) or None

    async def day():
        events = await run(g.events, today())
        return ("Today's calendar was:\n" + describe_events(events)) if events else None

    async def first_tomorrow(day=None):
        """The first timed event of the morning the bedtime leads to, as a timestamp (None: none). `day`: that morning's date (a date or "2026-11-05"), which
        daily.Daily.plan_tomorrow hands over so the calendar is asked about the same day it plans: after midnight that is today, not today + 1. Without a day,
        tomorrow by his clock, as before."""
        d = _day_of(day, log) or today() + timedelta(days=1)
        events = await run(g.events, d)
        timed = [e for e in events or [] if not e["all_day"]]
        return datetime.fromisoformat(timed[0]["start"].replace("Z", "+00:00")).timestamp() if timed else None

    async def events_today():
        """Today's events as they are (Daily works out the leave-by time and the day's miles from their locations)."""
        return await run(g.events, today()) if await asyncio.to_thread(lambda: g.connected) else None

    return {"calendar": calendar, "mail": mail, "packages": packages, "birthdays": birthdays, "tomorrow": tomorrow, "day": day,
            "first_tomorrow": first_tomorrow, "events_today": events_today}


def main(argv=None) -> int:
    import argparse                                                     # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["connect", "check"])
    args = ap.parse_args(argv)
    from ear import load_env                                            # noqa: PLC0415
    g = Google(load_env())
    if args.cmd == "connect":
        if not g.client_path.is_file():
            print(f"No OAuth client at {g.client_path}: create a 'Desktop app' OAuth client in Google Cloud and save its JSON there first.")
            return 1
        g.connect()
        print(f"Connected. Token kept at {g.token_path}.")
        return 0
    if not g.local:
        try:
            g.access_token()
        except NotConnected as e:
            print(f"Not connected ({e}). To fix it, {CONNECT_HINT}.")
            return 1
        print(f"Using the portal's grant ({g.portal}).")
    cals = g.calendars()
    print(f"{len(cals)} calendar(s) in his list:")
    for c in cals:
        print(f"  {'*' if c.get('primary') else ' '} {c.get('summaryOverride') or c.get('summary')}  [{c.get('accessRole')}]{'  selected' if c.get('selected') else ''}")
    w = g.work_calendar(cals)
    print("work calendar: " + (f"shared in ({w.get('summary')})" if w else "NOT found (set GOOGLE_WORK_CALENDAR to its address, or share it in)"))
    print(f"today: {len(g.events(date.today()))} event(s); mail last day: "
          + ", ".join(f"{k} {n}" for k, n in sorted(__import__('collections').Counter(m['kind'] for m in g.mail()).items())))
    print(f"contacts: {len(g.contacts()['emails'])} addresses, {len(g.contacts()['birthdays'])} birthdays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
