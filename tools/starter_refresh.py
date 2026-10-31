"""Skippy's conversation starters, gathered daily (the Captain, 2026-10-31: "start conversations about breakthroughs in my interests and world news that
affects me"). The hub offers them (services/satellite/starters.py); this finds them. Run daily by the scheduled task "Skippy starters refresh".

For each of his interests (filler.TOPICS) and a few world subjects (elections, laws, his own town and state), it:

  1. SEARCHES the house's SearXNG (news, the last day; the week when the day is quiet);
  2. ASKS Skippy Core's chat model (local, free) to pick the one item that is a real new development (a result, not a plan, an opinion or an
     advert) or none, and to write the opener Skippy will say, from that item alone, with no digits or physical figures (PLATINUM 21);
  3. CHECKS it the way tools/quip_refresh.checked does: the opener's claim is searched again and kept only when a strict reading of the item and
     the new results calls it clearly supported.

Kept candidates go to state/starters/candidates.json (expired ones drop out); nothing else changes. Log: state/starter-refresh.log.

    services/satellite/.venv/Scripts/python tools/starter_refresh.py          # the real thing (tools/starter_refresh.sh, the scheduled task)
    services/satellite/.venv/Scripts/python tools/starter_refresh.py --dry    # what it would keep; writes nothing
    services/satellite/.venv/Scripts/python tools/starter_refresh.py --only space,world-election
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "satellite"))
sys.path.insert(0, str(ROOT / "tools"))
import filler  # noqa: E402
import news  # noqa: E402
import starters  # noqa: E402
from quip_refresh import FACT_HINTS, vault  # noqa: E402

FOLDER = ROOT / "state" / "starters"
OPENER_WORDS = 60                                               # the most kept; the model is asked for PROMPT_WORDS
PROMPT_WORDS = 40
KEEP = 12                                                       # candidates kept at most (the best first)
TTL = {"world": 36 * 3600, "science": 72 * 3600}

SCIENCE_QUERIES = {
    "space": "space discovery astronomers", "physics": "physics breakthrough discovery", "chemistry": "chemistry breakthrough new material",
    "games": "video game news EverQuest OR Factorio OR Satisfactory OR \"Star Control\" OR \"Baldur's Gate\"", "music": "electronic music news",
    "tabletop": "Daggerheart OR \"Dungeons & Dragons\" news", "cars": "Tesla news", "smarthome": "smart home Matter news", "ai": "AI breakthrough research",
    "books": "Craig Alanson Expeditionary Force", "film": "science fiction film series news", "food": "food science discovery", "ar": "AR smart glasses news",
}
WORLD_QUERIES = {"election": "election results", "vote": "{state} election", "law": "new law takes effect {state}", "town": "{city} news", "state": "{state} news", "world": "major world news"}

PICK_PROMPT = """You find conversation starters for Skippy the Magnificent, the sarcastic, vain, secretly loyal ancient AI beer can from the Expeditionary
Force books, who lives in the Captain's house and sometimes brings something up himself. The Captain cares about: {hint}.

Today's headlines from a web search:
{listing}

Pick the ONE item that is {criterion}. Not an opinion piece, an advert, a deal, a review, a rumour, a preview of something not yet done, a how-to,
or a list. If none qualifies, answer {{"pick": 0}}.

Otherwise answer {{"pick": <its number>, "importance": <1 to 5, how much he would care>, "opener": "<what Skippy says>"}}. The opener: {words}
words or fewer, in Skippy's voice, said out of the blue (start like "Hey, monkey, did you hear..." or "So, while you weren't paying attention..."),
telling what happened using ONLY what that item itself says, then a short question inviting him to talk about it. No digits, no measurements or
physical figures of any kind, no numbers or years spelled out, no web addresses. Answer with the JSON only."""

CRITERIA = {"science": "a genuinely new development he would find exciting: a discovery, a breakthrough, a first, a release that happened",
            "world": "an important change that actually affects a person living in {place}: an election result, a law passed or taking effect, a decision, "
                     "or a major event near him or in the world"}
WORLD_HINT = "the world's important news, and what happens near his home ({place})"

GROUND_PROMPT = """A statement, and what the news reports say. Do these reports CLEARLY and DIRECTLY say every fact in the statement (ignore its
joking tone and its closing question)? Answer with one word: "supported" only if they plainly do; "contradicted" if they say otherwise; "unclear"
if they don't settle every part.

Statement: {opener}

Reports:
{sources}"""

FIGURES = re.compile(r"\d|https?:|www\.|\b(?:percent|kilomet|miles?\b|meters?\b|metres?\b|degrees|light[- ]years?|kilograms?|tons?\b|pounds\b|feet\b|"
                     r"mph|km/h|watts?\b|volts?\b|hundreds?\b|thousands?\b|millions?\b|billions?\b|trillions?\b|twenty|thirty|forty|fifty|sixty|seventy|"
                     r"eighty|ninety)", re.I)


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        got = json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def acceptable(opener: str) -> bool:
    words = (opener or "").split()
    return bool(words) and len(words) <= OPENER_WORDS and not FIGURES.search(opener) and "*" not in opener and "#" not in opener


def supported(ask, opener: str, item: dict, search) -> bool:
    """quip_refresh.checked's rule for a starter: the item plus a fresh search for its headline, and a strict verdict."""
    try:
        more = search(item["title"], "week")
    except Exception:  # noqa: BLE001 — the item alone is still a source
        more = []
    reports = [item] + [r for r in more if starters.norm(r["title"]) != starters.norm(item["title"])][:4]
    verdict = ask(GROUND_PROMPT.format(opener=opener, sources="\n".join(f"- {r['title']} ({r['source']}): {r['content']}" for r in reports)))
    return bool(re.search(r"\bsupported\b", verdict or "", re.I)) and not re.search(r"\b(?:contradicted|unclear)\b", verdict or "", re.I)


def gather(ask, subjects: dict, place: dict, search=None, seen: set | None = None, clock=time.time, log=print) -> list[dict]:
    """[candidate] from each subject {key: (kind, topic, query)}; one at most per subject, each checked."""
    search = search or (lambda q, when: news.search(q, when))
    seen = set(seen or ())
    out = []
    for key, (kind, topic, query) in subjects.items():
        try:
            items = search(query, "day")
            if len(items) < 3:
                items = max(items, search(query, "week"), key=len)
        except Exception as e:  # noqa: BLE001 — one subject's search failing never stops the others
            log(f"  {key}: search failed ({type(e).__name__})")
            continue
        items = [it for it in items if starters.norm(it["title"]) not in seen][:news.MAX_ITEMS]
        if not items:
            log(f"  {key}: nothing new")
            continue
        hint = WORLD_HINT.format(place=place["place"] or "the United States") if kind == "world" else FACT_HINTS.get(topic, topic)
        got = parse_json(ask(PICK_PROMPT.format(hint=hint, listing=news.listing(items), words=PROMPT_WORDS,
                                                criterion=CRITERIA[kind].format(place=place["place"] or "the United States"))))
        try:
            pick = int(got.get("pick") or 0)
        except (TypeError, ValueError):
            pick = 0
        opener = str(got.get("opener") or "").strip()
        if not 1 <= pick <= len(items):
            log(f"  {key}: none worth it")
            continue
        item = items[pick - 1]
        if not acceptable(opener):
            log(f"  {key}: opener refused (figures or length): {opener!r}")
            continue
        if not supported(ask, opener, item, search):
            log(f"  {key}: not supported by the reports, dropped: {opener!r}")
            continue
        now = clock()
        try:
            importance = max(1, min(5, int(got.get("importance") or 2)))
        except (TypeError, ValueError):
            importance = 2
        cand = {"id": hashlib.sha1(item["title"].encode("utf-8")).hexdigest()[:12], "kind": kind, "topic": topic, "subject": key, "opener": opener,
                "headline": item["title"], "url": item["url"], "importance": importance, "found": now, "expires": now + TTL[kind],
                "found_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "sources": items}
        seen.add(starters.norm(item["title"]))
        out.append(cand)
        log(f"  {key}: kept ({importance}) {opener}")
    return out


def subjects_for(place: dict, only: list[str] | None = None) -> dict:
    subs = {t: ("science", t, q) for t, q in SCIENCE_QUERIES.items() if t in filler.TOPICS}
    for k, q in WORLD_QUERIES.items():
        if ("{city}" in q and not place["city"]) or ("{state}" in q and not place["state"]):
            continue
        subs[f"world-{k}"] = ("world", "world", q.format(**place))
    return {k: v for k, v in subs.items() if not only or k in only}


def place_of(env: dict) -> dict:
    p = news.home_place(env)
    city, _, state = p.partition(",")
    return {"place": p, "city": city.strip(), "state": state.strip()}


def refresh(folder: Path, ask, env: dict, search=None, dry: bool = False, only=None, clock=time.time, log=print) -> list[dict]:
    store = starters.Starters(folder, clock=clock)
    now = clock()
    old = [c for c in store.candidates() if c.get("expires", 0) > now]
    offered = store.state()["offers"]
    seen = {starters.norm(c.get("headline", "")) for c in old} | {starters.norm(o.get("headline", "")) for o in offered}
    place = place_of(env)
    fresh = gather(ask, subjects_for(place, only), place, search=search, seen=seen, clock=clock, log=log)
    done = {o.get("id") for o in offered}
    keep = sorted([c for c in old + fresh if c["id"] not in done], key=lambda c: (-int(c.get("importance", 0)), -c.get("found", 0)))[:KEEP]
    log(f"done: {len(fresh)} new, {len(keep)} waiting")
    if not dry:
        starters.write_json(store.candidates_path, {"updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "items": keep})
    return fresh


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated subjects (space, ai, world-election ...)")
    a = ap.parse_args()
    env = vault()
    import ear                                                  # noqa: PLC0415
    client, model = ear.brain_client(env, "starters")

    def ask(prompt: str) -> str:
        r = client.messages.create(model=model, max_tokens=800, messages=[{"role": "user", "content": prompt}])
        return " ".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "starter refresh", flush=True)
    refresh(FOLDER, ask, env, dry=a.dry, only=[s.strip() for s in a.only.split(",") if s.strip()] or None, log=lambda s: print(s, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
