"""The news, for Skippy at home (the Captain, 2026-10-31: "news?" "about what?" "politics" -> a search -> a conversation about it).

Headlines come from the house's own search (SearXNG on skippy-core, the one purple Skippy and tools/quip_refresh.py use), news category, with a
time range so they are recent. The brain gets a `news` tool; a bare "what's the news?" is answered with "about what?" before any model runs, and a
short answer to that question goes straight to the tool, so the local model never has to remember the second step. What was found is kept for a
while so "tell me more about the second one" still has the headlines (the brain's memory keeps words, not tool results).

Nothing here makes up a fact: Skippy talks only from the snippets. A physical figure is never his to give (PLATINUM 21); a reported count (votes,
seats) is the news's own and is said as reported.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH_URL = os.environ.get("PURPLE_SEARCH_URL", "http://skippy-core:7360").rstrip("/")
KEEP_S = 15 * 60                                                # the last headlines stay in his head this long
MAX_ITEMS = 6

ASK_WHAT = "News about what? Politics, science, your games, something near home? Pick one, monkey."
BARE_RE = re.compile(r"^\W*(?:(?:what(?:'s| is| are)|give me|tell me|any|got any|read me|catch me up on|i want|let's hear|hit me with)\s+)?(?:the\s+|some\s+)?"
                     r"(?:latest\s+|today'?s\s+)?(?:news|headlines)(?:\s+(?:today|now|then|please|this morning|tonight))?\W*$", re.I)
ASKED_RE = re.compile(r"\bnews about what\b", re.I)
NOT_A_SUBJECT = re.compile(r"^\W*(?:no|nope|nah|never ?mind|forget it|cancel|stop|nothing|not now)\b", re.I)
MORE_RE = re.compile(r"\b(?:more|what about|how about|details?|deeper|dig|expand|what happened|go on|elaborate|explain)\b", re.I)
ORDINALS = {"first": 1, "one": 1, "second": 2, "two": 2, "third": 3, "three": 3, "fourth": 4, "four": 4, "fifth": 5, "five": 5, "sixth": 6, "six": 6}
ORDINAL_RE = re.compile(r"\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|last)\b(?:\s+(?:one|story|headline|thing|item))?|"
                        r"\b(?:number|story|headline|item)\s+(one|two|three|four|five|six|\d)\b")
STOP = {"tell", "more", "about", "that", "this", "with", "what", "happened", "details", "detail", "deeper", "story", "please", "skippy", "news", "headline",
        "which", "there", "they", "them", "their", "some", "really", "just", "know", "want", "explain", "elaborate", "expand", "first", "second", "third", "last"}
LOCAL_RE = re.compile(r"\b(?:near (?:me|here|home)|local(?:ly)?|around here|my (?:town|city|area|state)|in town)\b", re.I)

TOOL = {"name": "news", "description": "Recent news headlines from the web about a subject (politics, an election, science, a game, a place ...). "
                                       "Use it whenever he asks about the news or current events; talk only from what it returns.",
        "input_schema": {"type": "object", "properties": {
            "subject": {"type": "string", "description": "What the news is about, in his words, e.g. 'politics', 'the election results', 'space'."},
            "when": {"type": "string", "enum": ["day", "week", "month"], "description": "How recent: day (default), week or month."},
            "local": {"type": "boolean", "description": "True when he means news near his home."}},
            "required": ["subject"]}}

WEB_TOOL = {"name": "web_search", "description": "Search the web for anything that is not the news: facts, games, products, places, how-tos, "
                                               "recommendations ('Steam games like Zelda'). Talk only from what it returns.",
            "input_schema": {"type": "object", "properties": {
                "query": {"type": "string", "description": "What to search for, as you would type it into a search engine."}},
                "required": ["query"]}}

WEB_PROMPT = (" You can search the web with the web_search tool for anything else he asks that you don't know or that may have changed (games, "
              "products, places, facts, recommendations): call it in that same turn, never say you can't look things up, and answer only from "
              "what it returns, in a few spoken sentences.")

PROMPT = (" He can ask for the news: the news tool gives you today's headlines on any subject. Never say you will look something up or fetch "
          "the headlines: call the news tool in that same turn, then tell him what it found. Talk him through the two or three that matter most, like a "
          "conversation, in your own words and only from what the tool returned (who, what, where); never invent a detail, a result or a quote; say when "
          "something is still unsettled. Give the takeaway, not the data: a figure or two from a report is fine, never a string of numbers read "
          "out; then ask which one he wants more on. When he wants more on one story, call the news tool again with that story as the subject (a few words of its headline) and tell him what the new results add; never fill a gap with a guess ('likely', 'perhaps', "
          "'probably'): say what the reports don't say.")


def home_place(env: dict | None = None) -> str:
    """"City, ST" from HOME_ADDRESS in the vault ("12 Some St, Town, ST 85000"), for local news; "" when unknown. Never logged or said."""
    env = env if env is not None else os.environ
    addr = env.get("SKIPPY_NEWS_PLACE") or ""
    if addr:
        return addr
    parts = [p.strip() for p in (env.get("HOME_ADDRESS") or "").split(",") if p.strip()]
    if len(parts) < 2:
        return ""
    state = re.sub(r"\s*\d[\d-]*$", "", parts[-1]).strip()      # drop the ZIP
    if state.lower() in ("usa", "us", "united states") and len(parts) >= 3:
        parts, state = parts[:-1], re.sub(r"\s*\d[\d-]*$", "", parts[-2]).strip()
    city = parts[-2] if len(parts) >= 2 else ""
    return ", ".join(x for x in (city, state) if x and not re.search(r"\d", x))


def http_get_json(url: str, timeout: float = 20) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:     # noqa: S310 — our own SearXNG on the tailnet
        return json.loads(r.read())


def search(query: str, when: str = "day", get=http_get_json, category: str = "news") -> list[dict]:
    """[{title, content, url, source, date}] for `query`, newest category first; [] when nothing (or the search is down: the caller says so)."""
    params = {"q": query, "format": "json", "categories": category}
    if when is not None:                                        # None: any time (a plain web search)
        params["time_range"] = when if when in ("day", "week", "month", "year") else "day"
    data = get(f"{SEARCH_URL}/search?{urllib.parse.urlencode(params)}")
    out, seen = [], set()
    for r in data.get("results") or []:
        if not isinstance(r, dict) or not r.get("url") or not (r.get("title") or "").strip():
            continue
        key = re.sub(r"\W+", " ", r["title"].lower()).strip()[:80]
        if key in seen:
            continue
        seen.add(key)
        host = urllib.parse.urlparse(r["url"]).netloc.removeprefix("www.")
        out.append({"title": clean(r.get("title"), 200), "content": clean(r.get("content"), 400), "url": r["url"], "source": host,
                    "date": str(r.get("publishedDate") or "")[:10]})
    return out


def clean(text, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).replace("\ufffd", "'").strip()[:limit]


def listing(items: list[dict]) -> str:
    return "\n".join(f"{i}. {it['title']} ({it['source']}{', ' + it['date'] if it['date'] else ''}): {it['content']}" for i, it in enumerate(items, 1))


def is_bare_request(heard: str) -> bool:
    """"What's the news?" with no subject: Skippy asks "about what?" first."""
    return bool(BARE_RE.match(heard or ""))


class News:
    """The `news` tool for Brain(extra=...), and what it found last (for follow-ups)."""

    def __init__(self, env: dict | None = None, get=http_get_json, clock=time.time, log=None):
        self.place = home_place(env)
        self.get, self.clock, self.log = get, clock, log
        self.last: dict | None = None                           # the headlines he was given: {"subject", "when", "items", "at"}
        self.deeper: dict | None = None                         # more on one of them: {"story", "title", "items", "at"}

    def _search(self, query: str, when: str) -> tuple[str, list[dict]]:
        items = search(query, when, self.get)
        if len(items) < 3 and when == "day":                    # a quiet day for that subject: widen to the week (keeping the day's if the week has fewer)
            wider = search(query, "week", self.get)
            if len(wider) > len(items):
                when, items = "week", wider
        return when, items[:MAX_ITEMS]

    def run(self, args: dict) -> str:
        args = args or {}
        subject = clean(args.get("subject"), 160)
        if not subject:
            return "Ask him what the news should be about."
        story = args.get("story") if isinstance(args.get("story"), int) else None
        when = args.get("when") if args.get("when") in ("day", "week", "month") else ("week" if story else "day")
        local = bool(args.get("local")) or bool(LOCAL_RE.search(subject))
        query = LOCAL_RE.sub("", subject).strip() or "news"
        if local and self.place:
            query = f"{query} {self.place}"
        try:
            when, items = self._search(query, when)
        except Exception as e:                                  # noqa: BLE001 — said aloud, not raised
            if self.log:
                self.log(f"(news: search failed: {type(e).__name__})")
            return "The news search is not answering right now. Say so plainly."
        span = {"day": "the last day", "week": "the last week", "month": "the last month"}[when]
        if story:                                               # more on one story he was told about: the list he heard stays as it was
            if not items:
                return f"No more reports found on story {story} ({subject}) than the headline already said. Say so; do not guess."
            self.deeper = {"story": story, "title": subject, "items": items, "at": self.clock()}
            return f"More reports on story {story}, \"{subject}\", from {span} (tell him what they add; do not guess past them):\n{listing(items)}"
        if not items:
            return f"No recent news found about {subject}."
        self.last, self.deeper = {"subject": subject, "when": when, "items": items, "at": self.clock()}, None
        return f"Headlines about {subject} from {span}:\n{listing(items)}"

    def web(self, args: dict) -> str:
        """The `web_search` tool: the same SearXNG, its general results, any date."""
        query = clean((args or {}).get("query"), 200)
        if not query:
            return "Ask him what to search for."
        try:
            items = search(query, None, self.get, category="general")[:MAX_ITEMS]
        except Exception as e:                                  # noqa: BLE001 — said aloud, not raised
            if self.log:
                self.log(f"(web search failed: {type(e).__name__})")
            return "The web search is not answering right now. Say so plainly."
        return f"Web results for \"{query}\":\n{listing(items)}" if items else f"The web search found nothing for {query}. Say so; do not guess."

    def adopt(self, subject: str, items: list[dict]) -> None:
        """Headlines found elsewhere (a conversation starter's sources) become the ones he was given, so "tell me more" works on them."""
        self.last, self.deeper = {"subject": subject, "when": "day", "items": list(items)[:MAX_ITEMS], "at": self.clock()}, None

    def recent(self) -> str:
        """The headlines found in the last KEEP_S, as a note that rides in the brain's context() (the bracketed block at the start of the newest user message, never in the cached system prompt); "" when none."""
        if not self.last or self.clock() - self.last["at"] > KEEP_S:
            return ""
        note = f"\n\nThe news you looked up a little while ago, about {self.last['subject']} (talk only from this):\n{listing(self.last['items'])}"
        if self.deeper and self.clock() - self.deeper["at"] <= KEEP_S:
            note += f"\n\nMore reports you found on story {self.deeper['story']}, \"{self.deeper['title']}\":\n{listing(self.deeper['items'])}"
        return note

    # ── the brain's hooks ──
    def tools(self):
        return [TOOL, WEB_TOOL]

    def story_of(self, heard: str) -> int | None:
        """Which of the headlines he was just given he means ("the second one", "number three", "the last one", or words from its headline); None if none."""
        if not self.last or self.clock() - self.last["at"] > KEEP_S or not MORE_RE.search(heard or ""):
            return None
        items, h = self.last["items"], (heard or "").lower()
        m = ORDINAL_RE.search(h)
        if m:
            word = next(g for g in m.groups() if g)
            n = len(items) if word == "last" else ORDINALS.get(word) or (int(word) if word.isdigit() else 0)
            return n if 1 <= n <= len(items) else None
        said = {w for w in re.findall(r"[a-z']{4,}", h) if w not in STOP}
        best = max(range(len(items)), key=lambda i: len(said & set(re.findall(r"[a-z']{4,}", items[i]["title"].lower()))))
        hits = len(said & set(re.findall(r"[a-z']{4,}", items[best]["title"].lower())))
        return best + 1 if hits >= 1 and said else None

    def pre_route(self, heard: str, history: list[dict]) -> dict | None:
        """Tool args when the model need not decide: a short answer to "news about what?" (e.g. "politics"), or "more on the second one".
        None = the model decides. (The local model, left to it, told a follow-up from guesses instead of searching again, 2026-10-31.)"""
        n = self.story_of(heard)
        if n:
            words = self.last["items"][n - 1]["title"].split()
            return {"subject": " ".join(words[:14]), "story": n}
        last = next((m for m in reversed(history) if m.get("role") == "assistant"), None)
        if not last or not ASKED_RE.search(str(last.get("content") or "")):
            return None
        words = (heard or "").strip(" .!?")
        if not words or len(words.split()) > 8 or NOT_A_SUBJECT.match(words):
            return None
        words = re.sub(r"^\W*(?:(?:um|uh|hmm|well|oh|ok(?:ay)?)\W+)*(?:about\s+|on\s+|the\s+news\s+(?:about|on)\s+)?", "", words, flags=re.I).strip() or words
        return {"subject": words}


def load_env_place(path: Path | None = None) -> str:
    """HOME_ADDRESS's town for a tool run outside the hub (tools/starter_refresh.py), read from the vault."""
    try:
        text = (path or Path(r"G:\My Drive\vault\.env")).read_text(encoding="utf-8")
    except OSError:
        return ""
    env = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#"))
    return home_place({k.strip(): v.strip().strip("\"'") for k, v in env.items()})
