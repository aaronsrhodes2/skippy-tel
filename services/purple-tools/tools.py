"""The tools purple Skippy has beyond Claude Code's own (services/purple-tools/server.py lists them, in this order).

Each tool is {name, description, input_schema, run(args) -> text}. A tool returns plain words for the model to read, capped; it never raises
(`call` turns a failure into its own words and isError).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

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


TOOLS: dict[str, dict] = {
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


def tool_list() -> list[dict]:
    return [{"name": n, "description": t["description"], "inputSchema": t["input_schema"]} for n, t in TOOLS.items()]


def call(name: str, args: dict) -> tuple[str, bool]:
    """(text, is_error). Never raises."""
    tool = TOOLS.get(name)
    if tool is None:
        return f"There is no tool named {name!r}. Tools: {', '.join(TOOLS)}.", True
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
