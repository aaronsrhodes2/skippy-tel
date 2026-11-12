"""The tools purple Skippy has beyond Claude Code's own (services/purple-tools/server.py lists them, in this order).

Each tool is {name, description, input_schema, run(args) -> text}. A tool returns plain words for the model to read, capped; it never raises
(`call` turns a failure into its own words and isError).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH_URL = os.environ.get("PURPLE_SEARCH_URL", "http://skippy-core:7360").rstrip("/")   # SearXNG on skippy-core (core/compose.core.yml), tailnet only
MAX_TEXT = 7000
CAR_FLEET_URL = os.environ.get("PURPLE_CAR_FLEET_URL", "http://skippy-core:7353").rstrip("/")   # the car as a guard (core/car-fleet), tailnet only
CAR_URL = os.environ.get("PURPLE_CAR_URL", "http://skippy-core:7351").rstrip("/")           # the car reader (core/car): TeslaMate's latest values


def allowed_roots(cwd: Path | None = None) -> list[Path]:
    """The folders a tool may read a file from: the run's own folder (the server's working directory) plus PURPLE_ALLOWED_ROOTS (paths separated
    by the OS path separator). An MCP tool sits outside the bridge's deny_read wall, so it keeps its own."""
    roots = [cwd or Path.cwd()] + [Path(x) for x in os.environ.get("PURPLE_ALLOWED_ROOTS", "").split(os.pathsep) if x.strip()]
    return [r.resolve() for r in roots]


def checked_path(raw, cwd: Path | None = None) -> Path:
    path = Path(str(raw or "")).expanduser()
    path = (path if path.is_absolute() else (cwd or Path.cwd()) / path).resolve()
    if not any(path == r or r in path.parents for r in allowed_roots(cwd)):
        raise ValueError(f"{path.name or path}: that file is outside this run's folder; copy it into the folder first")
    return path
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text, n: int) -> str:
    one = re.sub(r"\s+", " ", _CONTROL.sub("", str(text or ""))).strip()
    return one if len(one) <= n else one[: n - 1] + "…"


def http_get_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "purple-tools/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000))


# ── web_search ────────────────────────────────────────────────────────────────────────────────────────────────
def web_search(args: dict, get=http_get_json) -> str:
    query = _clean(args.get("query"), 300)
    if not query:
        raise ValueError("web_search needs a query")
    try:
        n = max(1, min(10, int(args.get("num_results") or 6)))
    except (TypeError, ValueError):
        n = 6
    params = {"q": query, "format": "json", "safesearch": "0"}
    if args.get("time_range") in ("day", "week", "month", "year"):
        params["time_range"] = args["time_range"]
    if args.get("category") in ("general", "news", "science", "it", "files", "images", "videos"):
        params["categories"] = args["category"]
    data = get(f"{SEARCH_URL}/search?{urllib.parse.urlencode(params)}")
    results = [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("url")][:n]
    lines = []
    for answer in (data.get("answers") or [])[:2]:
        lines.append("Direct answer: " + _clean(answer if isinstance(answer, str) else answer.get("answer"), 400))
    for i, r in enumerate(results, 1):
        eng = r.get("engines") or ([r["engine"]] if r.get("engine") else [])
        engines = ", ".join(str(e) for e in eng)
        lines.append(f"{i}. {_clean(r.get('title'), 160)}\n   {_clean(r.get('url'), 300)}\n   {_clean(r.get('content'), 280)}"
                     + (f"\n   [{engines}]" if engines else ""))
    if not results:
        down = [f"{e[0]} ({e[1]})" for e in (data.get("unresponsive_engines") or []) if isinstance(e, list) and len(e) >= 2][:6]
        lines.append(f"No results for {query!r}." + (f" Engines that did not answer: {', '.join(down)}." if down else " Try other words."))
    return "\n".join(lines)[:MAX_TEXT]


# ── Mentat: the physics (PLATINUM section 21: every physical number comes from Mentat, with its source) ──────────────────────────────────────────
MENTAT_URL = os.environ.get("PURPLE_MENTAT_URL", "http://skippy-core:7350").rstrip("/")   # Mentat on skippy-core, tailnet only


def http_post_json(url: str, body: dict, timeout: float = 90.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "purple-tools/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(2_000_000))
    except urllib.error.HTTPError as e:                         # Mentat answers a refusal as JSON with a status: read it, it says why
        try:
            return json.loads(e.read(200_000))
        except (ValueError, OSError):
            raise e


def format_mentat(answer) -> str:
    """Mentat's {"ok", "result"} as words: the value WITH its unit and source (never a bare number), or what it needs, or why it refused."""
    if not isinstance(answer, dict):
        return "Mentat answered something unreadable."
    r = answer.get("result") if isinstance(answer.get("result"), dict) else answer
    err = r.get("error") if isinstance(r, dict) else None
    follow = (r.get("clarification") or {}) if isinstance(r.get("clarification"), dict) else {}
    lines = []
    if r.get("value") is not None:
        lines.append(f"Answer: {r['value']} {r.get('units') or ''}".rstrip())
        for label, key in (("Formula", "formula"), ("Source", "source"), ("Interpreted as", "interpreted_as"), ("Notes", "notes"), ("Uncertainty", "uncertainty")):
            if r.get(key):
                lines.append(f"{label}: {_clean(r[key], 400)}")
        if r.get("provenance_tag"):
            lines.append(f"Provenance: {r['provenance_tag']}")
    elif isinstance(err, dict) and err.get("code") == "needs_input" or follow:
        lines.append("Mentat needs more from the person before it can answer: " + _clean((err or {}).get("message") or r.get("notes"), 500))
        given = (follow.get("follow_up") or {}).get("given") if isinstance(follow.get("follow_up"), dict) else None
        if given:
            lines.append("It suggests these values: " + json.dumps(given) + ". Do NOT fill them in yourself: ask the person; only if they agree, call "
                         "ask_mentat again with given set to those values (their yes is the permission).")
    elif err:
        lines.append("Mentat could not answer: " + _clean(err.get("message") if isinstance(err, dict) else err, 500))
    elif answer.get("error"):
        lines.append("Mentat refused: " + _clean(answer.get("error"), 400))
    else:
        lines.append("Mentat gave no value. " + _clean(r.get("notes"), 300))
    return "\n".join(lines)[:MAX_TEXT]


def ask_mentat(args: dict, post=http_post_json) -> str:
    question = _clean(args.get("question"), 2000)
    if not question:
        raise ValueError("ask_mentat needs a question")
    body = {"question": question}
    if isinstance(args.get("given"), dict) and args["given"]:
        body["given"] = args["given"]
    return format_mentat(post(f"{MENTAT_URL}/ask", body))


def mentat_tool(args: dict, post=http_post_json) -> str:
    name = str(args.get("name") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,80}", name):
        raise ValueError("mentat_tool needs a tool name (see mentat_tools)")
    arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
    return format_mentat(post(f"{MENTAT_URL}/tools/{name}", arguments))


def mentat_tools(args: dict, get=http_get_json) -> str:
    listing = get(f"{MENTAT_URL}/tools")
    needle = _clean(args.get("search"), 80).lower()
    rows = [t for t in listing if isinstance(t, dict) and t.get("name") and (not needle or needle in (t["name"] + " " + str(t.get("description"))).lower()
                                                                              or needle == str(t.get("domain") or "").lower())]
    if not rows:
        return f"No Mentat tool matches {needle!r}." if needle else "Mentat lists no tools."
    out = [f"{len(rows)} of {len(listing)} Mentat tools" + (f" matching {needle!r}" if needle else "") + " (showing up to 40):"]
    for t in rows[:40]:
        props = ", ".join(((t.get("input_schema") or {}).get("properties") or {}))
        out.append(f"- {t['name']}({props}): {_clean(t.get('description'), 140)}")
    return "\n".join(out)[:MAX_TEXT]


# ── PDFs: the text layer first (free, instant), a picture of the page for the OCR pop-up when there is none ─────────────────────────────────────
MAX_PDF_BYTES = 60_000_000


def _page_range(spec, total: int) -> list[int]:
    """'1-5', '3', '2,4,7-9' (1-based) as 0-based page numbers inside the document; empty or missing = the first 20."""
    spec = str(spec or "").strip()
    if not spec:
        return list(range(min(total, 20)))
    out: list[int] = []
    for part in spec.split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", part)
        if not m:
            raise ValueError(f"pages must look like '1-5' or '2,4,7-9', not {spec!r}")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        out += [n - 1 for n in range(lo, min(hi, total) + 1) if n >= 1]
    if not out:
        raise ValueError(f"the document has {total} pages; {spec!r} is outside it")
    return sorted(set(out))[:60]


def _render_page(path: Path, number: int, folder: Path) -> str | None:
    """A PNG of one page for the OCR pop-up (read_document), if a renderer is installed; None when not."""
    try:
        import pypdfium2 as pdfium                                        # noqa: PLC0415 — optional
        image = pdfium.PdfDocument(str(path))[number].render(scale=1.6).to_pil()
    except Exception:  # noqa: BLE001
        return None
    folder.mkdir(exist_ok=True)
    target = folder / f"{path.stem[:40]}-page{number + 1}.png"
    image.convert("L").save(target, optimize=True)
    return str(target) if target.stat().st_size <= MAX_INPUT else None


def read_pdf(args: dict, cwd: Path | None = None) -> str:
    path = checked_path(args.get("path"), cwd)
    if not path.is_file():
        raise ValueError(f"there is no file at {path}")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError(f"{path.name} is over {MAX_PDF_BYTES // 1_000_000} MB")
    try:
        from pypdf import PdfReader                                       # noqa: PLC0415
    except ImportError as e:
        raise ValueError("pypdf is not installed for the tool server (pip install pypdf)") from e
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise ValueError(f"{path.name} is password protected") from None
    total = len(reader.pages)
    wanted = _page_range(args.get("pages"), total)
    out, blank = [f"{path.name}: {total} pages" + (f" (showing {len(wanted)})" if len(wanted) != total else "")], []
    used = len(out[0])
    for n in wanted:
        text = re.sub(r"[ \t]+\n", "\n", (reader.pages[n].extract_text() or "")).strip()
        if len(text) < 20:
            blank.append(n)
            continue
        chunk = f"\n--- page {n + 1} ---\n{text}"
        if used + len(chunk) > MAX_TEXT:
            out.append(f"\n[stopped at page {n + 1} to stay short: ask for the next pages with pages='{n + 1}-{n + 6}']")
            break
        out.append(chunk)
        used += len(chunk)
    if blank:
        shots = [(n, _render_page(path, n, (cwd or Path.cwd()) / ".purple-out")) for n in blank[:6]]
        made = [f"page {n + 1}: {s}" for n, s in shots if s]
        out.append(f"\n[no text layer on page(s) {', '.join(str(n + 1) for n in blank)} (a scan). "
                   + ("Pictures of them are saved; run read_document with input_path on each to read it: " + "; ".join(made) if made
                      else "No page renderer is installed here (pip install pypdfium2) to make pictures for read_document.") + "]")
    return "".join(out)[:MAX_TEXT + 1200]


# ── a headless browser: the page as a person sees it (scripts run), for sites WebFetch can't read and for looking at a page you built ──────────────
# Playwright's Chromium on this PC, one fresh private context per call (no cookies, no sign-ins, nothing kept): a call is a URL, optional steps, then
# the text, the links and, if asked, a picture. http(s) only; file:, chrome: and the like are refused.
BROWSE_STEPS = ("click", "fill", "press", "wait_ms", "wait_for", "scroll")


def _check_url(url: str) -> str:
    url = str(url or "").strip()
    if not re.fullmatch(r"https?://[^\s]+", url):
        raise ValueError("browse needs a full http:// or https:// address")
    return url


def browse(args: dict, cwd: Path | None = None, launcher=None) -> str:
    url = _check_url(args.get("url"))
    steps = args.get("steps") if isinstance(args.get("steps"), list) else []
    if len(steps) > 20:
        raise ValueError("at most 20 steps in one call")
    if launcher is None:
        try:
            from playwright.sync_api import sync_playwright               # noqa: PLC0415 — optional
        except ImportError as e:
            raise ValueError("Playwright is not installed for the tool server (pip install playwright; playwright install chromium)") from e
        launcher = sync_playwright
    width, height = (int(args.get("width") or 1280), int(args.get("height") or 800))
    notes: list[str] = []
    with launcher() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(viewport={"width": max(320, min(width, 2400)), "height": max(320, min(height, 3000))}).new_page()
            page.set_default_timeout(15000)
            page.goto(url, wait_until="load", timeout=30000)
            for i, step in enumerate(steps, 1):
                if not isinstance(step, dict) or len(step) < 1 or next(iter(step)) not in BROWSE_STEPS:
                    raise ValueError(f"step {i} must be one of {', '.join(BROWSE_STEPS)}, for example {{\"click\": \"text=Sign in\"}}")
                kind, value = next(iter(step.items()))
                if kind == "click":
                    page.click(str(value))
                elif kind == "fill":
                    page.fill(str(value), str(step.get("text", "")))
                elif kind == "press":
                    page.keyboard.press(str(value))
                elif kind == "wait_ms":
                    page.wait_for_timeout(max(0, min(int(value), 10000)))
                elif kind == "wait_for":
                    page.wait_for_selector(str(value))
                elif kind == "scroll":
                    page.mouse.wheel(0, int(value))
                notes.append(f"step {i} {kind}: ok")
            title = page.title()
            text = page.inner_text("body") if page.query_selector("body") else ""
            links = page.eval_on_selector_all("a[href]", "els => els.slice(0, 200).map(a => [a.innerText.trim().slice(0, 60), a.href])")
            shot = None
            if args.get("screenshot"):
                folder = (cwd or Path.cwd()) / ".purple-out"
                folder.mkdir(exist_ok=True)
                shot = folder / f"browse-{int(time.time())}.png"
                page.screenshot(path=str(shot), full_page=bool(args.get("full_page")))
            final = page.url
        finally:
            browser.close()
    text = re.sub(r"\n{3,}", "\n\n", _CONTROL.sub("", text)).strip()
    out = [f"{final}", f"title: {_clean(title, 200)}"] + notes
    if shot:
        out.append(f"screenshot saved {shot} (Read it to look at it)")
    out.append("--- text ---\n" + text[: MAX_TEXT - 1500])
    seen, rows = set(), []
    for label, href in links:
        if href in seen or not href.startswith(("http://", "https://")):
            continue
        seen.add(href)
        rows.append(f"- {label or '(no text)'}: {href}")
    if rows:
        out.append("--- links (first 25) ---\n" + "\n".join(rows[:25]))
    return "\n".join(out)[:MAX_TEXT + 1500]


# ── the Captain's car: charging on and off, through Tesla's Fleet API (core/car-fleet) ───────────────────────────────────────────────────────────────────
def car_charge(args: dict, post=http_post_json, get=http_get_json) -> str:
    action = str(args.get("action") or "").strip().lower()
    if action not in ("start", "stop", "limit", "status"):
        raise ValueError("car_charge's action is start, stop, limit or status")
    said = ""
    if action != "status":
        body = {"action": action} | ({"percent": args.get("percent")} if action == "limit" else {})
        got = post(f"{CAR_FLEET_URL}/charge", body, timeout=120.0)
        if not isinstance(got, dict) or not got.get("ok"):
            raise ValueError("The car didn't take it: " + str((got or {}).get("error") if isinstance(got, dict) else "no answer"))
        said = str(got.get("said") or "done") + ". "
    try:
        car = (get(f"{CAR_URL}/car", 10.0).get("cars") or [{}])[0]
    except Exception:  # noqa: BLE001 — the command went through; the reading is a bonus
        return said + "(TeslaMate's reading isn't available right now.)"
    bits = [f"battery {car.get('battery_level')}%", f"limit {car.get('charge_limit_soc')}%",
            "plugged in" if car.get("plugged_in") else "not plugged in", f"charging state {car.get('charging_state') or 'unknown'}"]
    if car.get("charger_power"):
        bits.append(f"{car['charger_power']} kW")
    lag = " (TeslaMate's reading can take a minute to catch up)" if action != "status" else ""
    return said + f"{car.get('name') or 'The car'}: " + ", ".join(bits) + f"{lag}."


def car_sentry(args: dict, post=http_post_json, get=http_get_json) -> str:
    """Sentry Mode on or off through the Fleet API (from anywhere), or what Sentry has seen. The alarm's answer (lights, lock) is automatic."""
    action = str(args.get("action") or "").strip().lower()
    if action not in ("arm", "disarm", "status"):
        raise ValueError("car_sentry's action is arm, disarm or status")
    said = ""
    if action != "status":
        got = post(f"{CAR_FLEET_URL}/sentry", {"action": action}, timeout=90.0)
        if not isinstance(got, dict) or not got.get("ok"):
            raise ValueError("The car didn't take it: " + str((got or {}).get("error") if isinstance(got, dict) else "no answer"))
        said = str(got.get("said") or "done") + ". "
    view = get(f"{CAR_FLEET_URL}/sentry", 10.0)
    events = [f"{time.strftime('%a %H:%M', time.localtime(e['at']))} {e.get('said')}" for e in (view.get("events") or [])[-5:] if e.get("at")]
    heard = (view.get("telemetry") or {}).get("heard_s")
    out = said + f"Sentry is {view.get('state') or 'unknown'}"
    out += f" (last word from the car {heard} s ago)" if heard is not None else " (the car hasn't streamed yet)"
    return out + (". Recent: " + "; ".join(events) if events else ". Nothing seen lately.")


# ── Bilby favorites: mark the song the Captain is listening to, for DJing later (portal/favorites.py) ──────────────────────────────────────────────
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
    folder = (cwd or Path.cwd()) / ".purple-out" / f"moment-{mid}"
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
    "browse": {
        "description": "Open a web page in a real headless browser on this PC (scripts run, so single-page apps and JavaScript-built pages work) and get its "
                       "title, visible text and links; optionally click, type or scroll first, and save a screenshot. A fresh private browser each call: "
                       "nothing is signed in and nothing is kept. Use WebFetch for plain pages; use this when WebFetch gets an empty shell, when a click "
                       "is needed, or to look at a page you built (use screenshot, then Read the picture).",
        "input_schema": {"type": "object", "properties": {
            "url": {"type": "string", "description": "the full http:// or https:// address"},
            "steps": {"type": "array", "description": "do these in order before reading: {\"click\": selector}, {\"fill\": selector, \"text\": \"...\"}, "
                                                      "{\"press\": \"Enter\"}, {\"wait_ms\": 1000}, {\"wait_for\": selector}, {\"scroll\": 600}; a selector is "
                                                      "CSS or Playwright's text=...",
                      "items": {"type": "object"}},
            "screenshot": {"type": "boolean", "description": "save a picture of the page in .purple-out/"},
            "full_page": {"type": "boolean", "description": "with screenshot: the whole page, not just the window"},
            "width": {"type": "integer", "description": "window width (default 1280; 390 is a phone)"},
            "height": {"type": "integer", "description": "window height (default 800)"}}, "required": ["url"]},
        "run": browse},
    "read_pdf": {
        "description": "Read a PDF from a path on this PC: the text of up to 20 pages at a time (give `pages`, like '5-9', for others), page by page. "
                       "Instant and free. Pages that are scans have no text; for those it saves a picture of the page in .purple-out/ and tells you to run "
                       "read_document (OCR) on it.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "the PDF's path"},
            "pages": {"type": "string", "description": "which pages, 1-based: '1-5', '3', '2,4,7-9' (default: the first 20)"}}, "required": ["path"]},
        "run": read_pdf},
    "web_search": {
        "description": "Search the web (a self-hosted metasearch engine at home: no account, no key). Returns the top results with title, URL and a "
                       "snippet. Use it for anything after your training, for documentation, error messages, versions and facts you are not sure of; "
                       "then WebFetch a result's URL to read the page.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "what to search for, in plain words"},
            "num_results": {"type": "integer", "description": "how many results, 1 to 10 (default 6)"},
            "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "only results from this long ago (optional)"},
            "category": {"type": "string", "enum": ["general", "news", "science", "it", "files", "images", "videos"], "description": "default general"}},
            "required": ["query"]},
        "run": web_search},
    "ask_mentat": {
        "description": "Ask Mentat a physics, chemistry or maths question in plain words. EVERY physical number you report must come from Mentat with its "
                       "source (the Captain's rule): never calculate or recall one yourself. Returns the value with its unit, formula and source. If it "
                       "says it needs more, ask the person: do not invent or fill in inputs, and pass `given` only with values the person agreed to.",
        "input_schema": {"type": "object", "properties": {
            "question": {"type": "string", "description": "the question, in plain words, with the numbers the person gave"},
            "given": {"type": "object", "description": "inputs the person confirmed (name to number or quantity text); only after they agreed"}},
            "required": ["question"]},
        "run": ask_mentat},
    "mentat_tool": {
        "description": "Call one named Mentat tool with exact arguments (find names with mentat_tools). Use it when you already know which tool and "
                       "its inputs; otherwise ask_mentat. The answer carries the value, unit and source.",
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "the tool's name, for example schwarzschild_radius"},
            "arguments": {"type": "object", "description": "the tool's arguments, as its schema names them"}}, "required": ["name"]},
        "run": mentat_tool},
    "mentat_tools": {
        "description": "List Mentat's tools (about 220) with their argument names, optionally only those matching a word or a domain (for example "
                       "'orbit', 'thermo', 'optics').",
        "input_schema": {"type": "object", "properties": {"search": {"type": "string", "description": "a word to match in the name, description or domain (optional)"}}},
        "run": mentat_tools},
    "bilby_favorite": {
        "description": "Bilby (the Captain's music): add the song he is listening to RIGHT NOW (on any of his Bilby players or room pods) to his DJ favorites "
                       "list, or list the favorites. Use when he says 'add this song to my favorites', 'favorite this', 'what's on my favorites'.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "list"]}}},
        "run": bilby_favorite},
    "spotify": {
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
    "car_charge": {
        "description": "The Captain's Tesla: start or stop charging, set the charge limit, or read the charge state. Commands go through Tesla's Fleet API "
                       "(from anywhere; wakes the car once if needed); the car must be plugged in to start. Use status to check before or after.",
        "input_schema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["start", "stop", "limit", "status"]},
            "percent": {"type": "integer", "description": "with limit: the new charge limit, 50 to 100"}}, "required": ["action"]},
        "run": car_charge},
    "car_sentry": {
        "description": "The Captain's Tesla as a guard: arm or disarm Sentry Mode (through Tesla's Fleet API, from anywhere; wakes the car once if needed), "
                       "or read what Sentry has seen (someone near, the alarm). When the alarm goes off the car flashes its lights and locks by itself "
                       "(never the horn) and the Captain gets a Discord DM.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["arm", "disarm", "status"]}}, "required": ["action"]},
        "run": car_sentry},
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


# ── the quick pop-ups on this PC: picture and audio tools, one MCP tool each, straight from the registry ──────────────────────────────────────────
# The home reporter owns the GPU lock and the registry, and has a loopback door for exactly this (services/home-reporter/local_api.py). The slow
# ones (box core: 3D models, video) are not here: they wait for Core to be quiet through the portal's queue (a SKIPPY_POPUP: line).
POPUP_URL = os.environ.get("PURPLE_POPUP_URL", "http://127.0.0.1:47450").rstrip("/")
MAX_INPUT = 1_000_000          # docs/popups.md: about 1 MB of file
_popup_cache: dict = {"at": 0.0, "tools": {}}


def popup_tools(get=http_get_json, now=time.monotonic) -> dict[str, dict]:
    """The registry's quick tools as MCP tools, read from the reporter (cached a minute; an unreachable reporter means none, never an error)."""
    if now() - _popup_cache["at"] < 60 and _popup_cache["tools"]:
        return _popup_cache["tools"]
    try:
        listing = get(f"{POPUP_URL}/popups", 5.0)
    except Exception:  # noqa: BLE001 — the reporter is down: the other tools still work
        listing = []
    out = {}
    for e in listing if isinstance(listing, list) else []:
        if not isinstance(e, dict) or e.get("family") == "plumbing":      # the test pop-up (echo) is for checking the pipes, not a tool for the agent
            continue
        name = str(e.get("name") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,60}", name) or name in TOOLS:
            continue
        schema = json.loads(json.dumps(e.get("inputs") or {"type": "object", "properties": {}}))
        schema.setdefault("type", "object")
        props = schema.setdefault("properties", {})
        if e.get("media_in"):
            props["input_path"] = {"type": "string", "description": "the path of the file to use (" + ", ".join(e["media_in"]) + "; about 1 MB at most)"}
            if e.get("media_required"):
                schema["required"] = sorted(set(schema.get("required") or []) | {"input_path"})
        secs = e.get("est_seconds")
        out[name] = {"description": f"{_clean(e.get('description'), 600)} Runs on this PC's card" + (f", about {secs} s" if secs else "") + "; results come back "
                                    "as words, or as a file saved in .purple-out/ in your folder (Read a picture to see it).",
                     "input_schema": schema, "popup": e, "run": None}
    _popup_cache.update(at=now(), tools=out)
    return out


# Types Windows' mimetypes doesn't know (a MIDI came back as .bin), so a result keeps an extension a tool or a person can open.
_EXT = {"audio/midi": ".mid", "audio/x-midi": ".mid", "model/gltf-binary": ".glb", "audio/flac": ".flac", "audio/ogg": ".ogg",
        "image/webp": ".webp", "application/zip": ".zip"}


def run_popup(entry: dict, args: dict, post=http_post_json, cwd: Path | None = None) -> str:
    args = dict(args)
    path = args.pop("input_path", None)
    body: dict = {"args": args}
    if path:
        f = checked_path(path, cwd)
        if not f.is_file():
            raise ValueError(f"there is no file at {path}")
        raw = f.read_bytes()
        if len(raw) > MAX_INPUT:
            raise ValueError(f"{f.name} is {len(raw) // 1000} KB; a pop-up takes about {MAX_INPUT // 1000} KB at most (shrink it first)")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        body["input"] = {"type": ctype, "name": f.name, "b64": base64.b64encode(raw).decode("ascii")}
    got = post(f"{POPUP_URL}/popup/{entry['name']}", body, timeout=max(60.0, min(3600.0, float(entry.get("est_seconds") or 60) * 4)))
    if not isinstance(got, dict) or got.get("error") or not got.get("b64"):
        raise ValueError(str((got or {}).get("error") if isinstance(got, dict) else "no answer") or f"{entry['name']} failed")
    data, ctype = base64.b64decode(got["b64"]), str(got.get("type") or "")
    if ctype in ("application/json", "text/plain") or ctype.startswith("text/"):
        return data.decode("utf-8", "replace")[:MAX_TEXT]
    ext = _EXT.get(ctype) or mimetypes.guess_extension(ctype) or ".bin"
    folder = (cwd or Path.cwd()) / ".purple-out"
    folder.mkdir(exist_ok=True)
    target = folder / f"{entry['name']}-{int(time.time())}{ext}"
    target.write_bytes(data)
    return f"{entry['name']} saved {target} ({len(data)} bytes, {ctype}). Read it to look at it."


def _popup_runner(entry: dict):
    return lambda args: run_popup(entry, args)


def offered() -> dict[str, dict]:
    """Every tool this server offers, narrowed by PURPLE_TOOLS_ONLY (comma-separated names; unset or empty = all). A smaller set costs the run less of
    its working room: each tool's schema is paid for in every request."""
    only = {n.strip() for n in os.environ.get("PURPLE_TOOLS_ONLY", "").split(",") if n.strip()}
    if only and only <= set(TOOLS):                       # no pop-up asked for: don't even ask the reporter what it has
        return {n: t for n, t in TOOLS.items() if n in only}
    allt = {**TOOLS, **popup_tools()}
    return {n: t for n, t in allt.items() if n in only} if only else allt


def tool_list() -> list[dict]:
    return [{"name": n, "description": t["description"], "inputSchema": t["input_schema"]} for n, t in offered().items()]


def call(name: str, args: dict) -> tuple[str, bool]:
    """(text, is_error). Never raises."""
    have = offered()
    tool = have.get(name)
    if tool is not None and tool.get("run") is None:
        tool = {"run": _popup_runner(tool["popup"])}
    if tool is None:
        return f"There is no tool named {name!r}. Tools: {', '.join(have)}.", True
    try:
        return tool["run"](args), False
    except ValueError as e:
        return str(e), True
    except urllib.error.HTTPError as e:
        return f"{name} failed: the service answered HTTP {e.code}.", True
    except (OSError, urllib.error.URLError) as e:
        return f"{name} failed: it could not reach its service at home ({type(e).__name__}). Is skippy-core on?", True
    except Exception as e:  # noqa: BLE001 — a tool must never take the server down
        return f"{name} failed ({type(e).__name__}: {str(e)[:160]}).", True
