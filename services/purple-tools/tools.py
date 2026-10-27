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
    path = Path(str(args.get("path") or "")).expanduser()
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


TOOLS: dict[str, dict] = {
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
        if not isinstance(e, dict):
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


def run_popup(entry: dict, args: dict, post=http_post_json, cwd: Path | None = None) -> str:
    args = dict(args)
    path = args.pop("input_path", None)
    body: dict = {"args": args}
    if path:
        f = Path(str(path)).expanduser()
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
    ext = mimetypes.guess_extension(ctype) or ".bin"
    folder = (cwd or Path.cwd()) / ".purple-out"
    folder.mkdir(exist_ok=True)
    target = folder / f"{entry['name']}-{int(time.time())}{ext}"
    target.write_bytes(data)
    return f"{entry['name']} saved {target} ({len(data)} bytes, {ctype}). Read it to look at it."


def _popup_runner(entry: dict):
    return lambda args: run_popup(entry, args)


def tool_list() -> list[dict]:
    return [{"name": n, "description": t["description"], "inputSchema": t["input_schema"]} for n, t in {**TOOLS, **popup_tools()}.items()]


def call(name: str, args: dict) -> tuple[str, bool]:
    """(text, is_error). Never raises."""
    tool = TOOLS.get(name)
    if tool is None and name in popup_tools():
        tool = {"run": _popup_runner(popup_tools()[name]["popup"])}
    if tool is None:
        return f"There is no tool named {name!r}. Tools: {', '.join({**TOOLS, **popup_tools()})}.", True
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
