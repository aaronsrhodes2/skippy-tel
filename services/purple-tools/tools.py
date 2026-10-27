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
