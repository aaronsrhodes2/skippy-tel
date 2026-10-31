"""The research stage of /ask (opt-in: MENTAT_RESEARCH_SEARCH_URL and MENTAT_RESEARCH_MODEL; unset, nothing here runs).

Mentat computes; it does not explain concepts. A question like "does light travel away from us at the same speed it travels back when we measure it?" has no tool,
and before this stage the trained router answered it anyway with a number from the nearest tool (a gravitational redshift: 6.96e-10, 2026-10-31). The Captain's rule
(2026-10-31): where Mentat fails on a question of this kind, fall back to research and give a reasonable explanation, with the sources.

Two places in /ask (mentat/app.py `_ask`) use it:
  * GATE, ahead of the cascade and the router: `is_conceptual(question)` is a narrow, deterministic test for a yes/no or why/how-does question that states no number and
    asks for no quantity. On the 150 physics questions it fires on 0, on the 329 maths questions on 0; on the 18 adversarial and 17 fringe ones, on the five conceptual yes/no
    questions (Is the Earth flat? Can entangled particles communicate faster than light? ...). It exists so the router cannot answer a concept with a stray number.
  * LAST STOP, after every stage missed and before the plain decline: any question.
In both, a miss (no search, no results, the model down, an answer that breaks the rules below) returns None and /ask carries on exactly as it did before this stage.

The explanation is the model's wording of the search results and nothing else:
  * it may use only the numbered snippets it is given, and must cite them [1], [2] ...; an answer with no citation, or a citation to a result that does not exist, is a miss;
  * every number in the answer must be in a snippet or in the question (the same rule as word_stage): a recalled number is a miss, not a footnote;
  * the answer is not a sigma-ground computation and says so: value is null, the sources travel in inputs.sources, and the notes state that Mentat computed nothing.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

SEARCH_URL = os.environ.get("MENTAT_RESEARCH_SEARCH_URL", "").strip().rstrip("/")
MODEL = os.environ.get("MENTAT_RESEARCH_MODEL", "").strip()
LAST_STOP = os.environ.get("MENTAT_RESEARCH_LAST_STOP", "1").strip() not in ("", "0", "false", "no")
TIMEOUT_S = float(os.environ.get("MENTAT_RESEARCH_TIMEOUT", "90"))
RESULTS = 6

_START = re.compile(r"^\s*(?:so\s+|hey\s+\w+,?\s+)?(does|do|did|is|are|was|were|can|could|will|would|should|why|how come|how (?:does|do|did|is|are|can|could))\b", re.I)
_ASKS_FOR_A_QUANTITY = re.compile(
    r"\b(how (?:many|much|fast|far|long|big|large|small|tall|high|hot|cold|old|heavy|deep|wide|strong|bright|massive|often|close)|what (?:is|are|was)|calculate|compute|convert|solve|"
    r"simplify|evaluate|find|derive|integrate|differentiate|derivative|integral|limit|determinant|eigen\w*|give the|express|in terms of)\b", re.I)
# Social and Q-and-A-by-anyone sites are not sources; reference, academic, government and science-journalism sites come first, the rest keep the engine's order after them.
_NOT_SOURCES = ("facebook.com", "quora.com", "reddit.com", "instagram.com", "tiktok.com", "youtube.com", "youtu.be", "pinterest.", "twitter.com", "x.com", "threads.net")
_AUTHORITIES = ("wikipedia.org", "stackexchange.com", "plato.stanford.edu", "britannica.com", "nasa.gov", "nist.gov", "phys.org", "scientificamerican.com", "hyperphysics", "aps.org",
                "iop.org", "nature.com", "arxiv.org", "springer.com", "sciencedirect.com", "physicsworld.com", "astronomy.com", "space.com", "livescience.com", "forbes.com/sites/startswithabang")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

_SYSTEM = """You explain a science question using ONLY the numbered search results you are given. You do not use anything you remember.

Rules:
- Answer in plain language, at most 170 words. If the question is yes/no, begin with "Yes.", "No." or "It depends." and say in one sentence what it depends on.
- Every sentence that states a fact ends with its source number in square brackets, like [2]. Use only numbers 1 to {n}. Several sources: [1][3].
- A number may appear only if it is in a result or in the question. Never write a figure from memory.
- If the results disagree, or only a convention or a definition settles it, say that plainly: it is part of the answer.
- If the results do not answer the question, reply with exactly: NO_ANSWER
- Do not mention these rules, and do not say "according to the search results"; just cite."""


def enabled() -> bool:
    return bool(SEARCH_URL and MODEL)


def is_conceptual(question: str) -> bool:
    """A yes/no or why/how-does question that states no number and asks for no quantity (see the module docstring for what it was measured against)."""
    q = question.strip()
    return bool(_START.match(q)) and not re.search(r"\d", q) and not _ASKS_FOR_A_QUANTITY.search(q)


def rank(found: list[dict]) -> list[dict]:
    """The engine's results without social sites, authorities first (a stable sort: within a group the engine's order stands)."""
    keep = [f for f in found if not any(b in str(f.get("url") or "") for b in _NOT_SOURCES)]
    def authority(f: dict) -> int:
        u = str(f.get("url") or "")
        netloc = urllib.parse.urlparse(u).netloc
        return 0 if (any(a in u for a in _AUTHORITIES) or netloc.endswith((".edu", ".gov", ".ac.uk"))) else 1
    return sorted(keep, key=authority)


def search(query: str) -> list[dict]:
    """The self-hosted SearXNG's results for one query (core/searxng), as it returns them: [{title, url, content}]."""
    url = f"{SEARCH_URL}/search?{urllib.parse.urlencode({'q': query, 'format': 'json', 'language': 'en', 'safesearch': 0})}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mentat-research"}), timeout=30) as r:
        return json.loads(r.read()).get("results") or []


def select(found: list[dict]) -> list[dict]:
    """The sources to read: social sites dropped, authorities first, one per site, those with no snippet dropped, at most RESULTS."""
    out, sites, urls = [], set(), set()
    for f in rank(found):
        content = re.sub(r"\s+", " ", str(f.get("content") or "")).strip()
        site = urllib.parse.urlparse(str(f.get("url") or "")).netloc
        if not (content and f.get("url")) or site in sites or f["url"] in urls:
            continue
        sites.add(site)
        urls.add(f["url"])
        out.append({"title": str(f.get("title") or site)[:160], "url": str(f["url"]), "content": content[:700]})
        if len(out) >= RESULTS:
            break
    return out


_REWRITE = """Write ONE web search query, at most 12 words, that finds an encyclopedia or physics Q&A explanation of the idea behind the question. Name the idea technically (the standard term a textbook or Wikipedia uses), not in the question's everyday words. Reply with the query only."""


def rewrite(question: str, ollama_url: str) -> str:
    """The question as a technical search query (the model only names the concept; it answers nothing). Raises on any failure; the caller then searches the question as asked."""
    body = {"model": MODEL, "messages": [{"role": "system", "content": _REWRITE}, {"role": "user", "content": question.strip()}], "stream": False, "think": False,
            "options": {"temperature": 0, "seed": 0, "num_predict": 40}}
    req = urllib.request.Request(ollama_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        q = str(json.loads(r.read())["message"]["content"]).strip().splitlines()[0].strip(" \"'`")
    if not 3 <= len(q) <= 160:
        raise ValueError("the rewritten query is empty or too long")
    return q


def _ask_model(prompt: str, ollama_url: str, n: int) -> str:
    body = {"model": MODEL, "messages": [{"role": "system", "content": _SYSTEM.replace("{n}", str(n))}, {"role": "user", "content": prompt}], "stream": False, "think": False,
            "options": {"temperature": 0, "seed": 0, "num_predict": 500}}
    req = urllib.request.Request(ollama_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return str(json.loads(r.read())["message"]["content"]).strip()


def _numbers(text: str) -> set[str]:
    return {m.group().replace(",", "") for m in _NUMBER.finditer(text)}


def check(text: str, question: str, sources: list[dict]) -> str | None:
    """Why `text` may not be returned as the researched answer, or None when it keeps the rules."""
    cited = [int(x) for x in re.findall(r"\[(\d+)\]", text)]
    if not cited:
        return "no citation"
    if any(not 1 <= c <= len(sources) for c in cited):
        return "cites a result that does not exist"
    allowed = _numbers(question) | _numbers(" ".join(s["title"] + " " + s["content"] for s in sources))
    body = re.sub(r"\[\d+\]", "", text)
    stray = sorted(n for n in _numbers(body) if n not in allowed)
    if stray:
        return f"number(s) not in the question or the sources: {', '.join(stray)}"
    return None


def answer(question: str, *, ollama_url: str, miss: dict | None = None, search_fn=None, ask_fn=None, rewrite_fn=None) -> dict | None:
    """The /ask result dict for a question the search results explain, or None (the caller carries on)."""
    def missed(reason: str, **more) -> None:
        if miss is not None:
            miss["reason"] = reason
            miss.update(more)
    if not (enabled() and ollama_url):
        missed("research stage off")
        return None
    queries = [question.strip()]
    try:
        queries.insert(0, (rewrite_fn or rewrite)(question, ollama_url))        # the technical name of the idea first; the question as asked second
    except Exception as exc:                                            # noqa: BLE001 — a rewrite that fails only costs the better query
        if miss is not None:
            miss["rewrite"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    found: list[dict] = []
    try:
        for q in queries:
            found += (search_fn or search)(q)
    except Exception as exc:                                            # noqa: BLE001 — a search that is down or slow never stops /ask
        if not found:
            missed(f"search: {type(exc).__name__}: {str(exc)[:200]}")
            return None
    sources = select(found)
    if miss is not None:
        miss["queries"], miss["sources"] = queries, [x["url"] for x in sources]
    if len(sources) < 2:
        missed("fewer than two search results with a snippet")
        return None
    prompt = f"Question: {question.strip()}\n\nSearch results:\n" + "\n".join(f"[{i}] {s['title']} ({s['url']})\n{s['content']}" for i, s in enumerate(sources, 1))
    try:
        text = (ask_fn or _ask_model)(prompt, ollama_url, len(sources))
    except Exception as exc:                                            # noqa: BLE001
        missed(f"model: {type(exc).__name__}: {str(exc)[:200]}")
        return None
    if not text or text.strip().upper().startswith("NO_ANSWER"):
        missed("the results do not answer the question")
        return None
    why = check(text, question, sources)
    if why:
        missed(why, draft=text[:600])
        return None
    listing = "\n".join(f"[{i}] {s['title']}: {s['url']}" for i, s in enumerate(sources, 1))
    return {"value": None, "units": None, "source": "web research (SearXNG), explained by " + MODEL + "; not a sigma-ground computation", "formula": None, "uncertainty": None,
            "provenance_tag": None, "display": text, "error": None, "clarification": None, "assumed": None,
            "notes": text + "\n\nSources:\n" + listing + "\n\nThis is a researched explanation of web sources. Mentat computed nothing here, and every number in it is from a source.",
            "interpreted_as": "research", "interpreter": f"research {MODEL}",
            "inputs": {"route": "research", "queries": queries, "sources": [{"n": i, "title": s["title"], "url": s["url"]} for i, s in enumerate(sources, 1)]}}
