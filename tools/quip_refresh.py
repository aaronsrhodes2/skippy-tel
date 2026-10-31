"""Skippy's quips, kept fresh (the Captain, 2026-10-31: "a joke is funny three times at most"; run daily like sigma-ground's distiller).

The quips (services/satellite/filler.py) cover the wait while his answer is worked out. The hub's inventory (state/filler/plays.json) counts
every play. Once a day this:

  1. RETIRES every quip played MAX_PLAYS times: its entry moves from index.json to retired.json and its recording to state/filler/retired/
     (kept, never deleted: the words stay on record so a new one never repeats them);
  2. WRITES new ones with Skippy Core's chat model (local, free), kind by kind: one for each retired, plus enough to keep every kind at
     STOCK unplayed-or-fresh quips; never more than --max a day (default 40). A line is kept only if it is new (not any current or retired
     line, normalised), short, and gives no answer (a quip covers the wait; it must fit any question of its kind);
  3. VOICES them through the voice gate (the local voice: no cost) into state/filler/<kind>/q<hash>.mp3 and adds them to index.json.

The hub re-reads index.json by itself. Nothing is ever deleted.

    services/satellite/.venv/Scripts/python tools/quip_refresh.py            # the real thing (tools/quip_refresh.sh, the scheduled task)
    services/satellite/.venv/Scripts/python tools/quip_refresh.py --dry      # what it would retire and how many it would write; changes nothing
    services/satellite/.venv/Scripts/python tools/quip_refresh.py --max 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "satellite"))
import filler  # noqa: E402

BANK = ROOT / "state" / "filler"
STOCK = 12                      # per kind: quips with plays to spare (below MAX_PLAYS)
FACT_STOCK = 8                  # per subject: factoids with plays to spare
MAX_WORDS = 16
FACT_WORDS = 32

FACT_HINTS = {
    "space": "space, planets, stars, black holes, spaceflight", "physics": "physics: quantum, relativity, light, energy, gravity",
    "chemistry": "chemistry, the elements, materials", "music": "music, DJing, electronic music, famous albums and artists",
    "games": "video games: EverQuest, Garry's Mod, Star Control, Factorio, Satisfactory, Baldur's Gate 3, Minecraft",
    "tabletop": "tabletop roleplaying: Daggerheart, Dungeons & Dragons, dice, famous modules", "cars": "electric cars, Tesla, batteries, driving",
    "smarthome": "smart homes, lighting, home automation", "ai": "AI, computers, programming, robots",
    "books": "science fiction books, especially Craig Alanson's Expeditionary Force (Skippy, Joe Bishop)", "film": "films and TV",
    "food": "food, cooking, coffee, beer", "ar": "augmented reality, smart glasses, displays",
}

FACT_PROMPT = """You write short spoken factoids for Skippy the Magnificent, the sarcastic, vain ancient AI beer can from the Expeditionary Force books.
He says one while the Captain waits for an answer about: {hint}.

Rules for every factoid:
- One surprising, TRUE, well-established fact, {words} words or fewer, said in Skippy's voice (a short sarcastic lead-in is fine, like "Fun fact:").
- NO numbers, figures, measurements, percentages or dates of any kind, not even spelled out (a number must never come from a language model).
- Nothing you are not certain of; no rumours, no trivia that is commonly repeated but false.
- Not one of these, and not the same fact reworded:
{existing}

Write exactly {n} factoids as a JSON array of strings, and nothing else."""

NUMBER_WORDS = re.compile(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|hundred|thousand|"
                          r"million|billion|trillion|percent|half|dozen|first|second|third)\b", re.I)

KIND_HINTS = {
    "thinking": "any question at all, while he thinks",
    "performance": "a request for a poem, joke, song, story or other performance",
    "science": "a physics, maths or science question",
    "advice": "a request for his opinion or advice",
    "personal": "a question about Skippy himself",
    "compliment": "the Captain praising him",
    "insult": "the Captain insulting him",
    "media": "a request to play music",
    "home": "a household command (lights, plugs, the house)",
    "goodbye": "thanks or a goodbye (a sign-off; nothing follows it)",
    "long": "a second line when the answer is taking a long time",
}

PROMPT = """You write short spoken lines for Skippy the Magnificent, the sarcastic, vain, secretly loyal ancient AI beer can from the Expeditionary Force
books, who calls the Captain "monkey" now and then. These lines are pre-recorded and played to cover the wait for: {hint}.

Rules for every line:
- {words} words or fewer, one or two short sentences, spoken English (no emoji, no markdown, no lists).
- It must fit ANY such request: never answer, never guess the answer, never mention a specific topic, number, song or device.
- Funny, in character, varied. Do not start most lines the same way. At most one line may begin with the tag [sighs].
- Use "monkey" in at most one line in three; vary how he addresses the Captain (or don't address him at all).
- Not one of these existing lines, and not a near-copy of one:
{existing}

Here are a few of his lines for style:
{examples}

Write exactly {n} new lines as a JSON array of strings, and nothing else."""


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def kinds_of(index: dict) -> list[str]:
    """Every kind the bank keeps: the quip kinds in the index, and a factoid kind per subject (filler.TOPICS)."""
    return list(dict.fromkeys(list(index) + [f"fact:{t}" for t in filler.TOPICS]))


def worn(plays: dict, file: str) -> bool:
    return int((plays.get(file) or {}).get("n", 0)) >= filler.MAX_PLAYS


def plan(index: dict, plays: dict, budget: int) -> tuple[dict, dict]:
    """({kind: [retired entries]}, {kind: how many new to write}); the new ones fill the kinds with the least stock first, within `budget`."""
    retire = {k: [e for e in v if worn(plays, e["file"])] for k, v in index.items()}
    short = {k: max(0, (FACT_STOCK if k.startswith("fact:") else STOCK) - sum(1 for e in index.get(k, []) if not worn(plays, e["file"])))
             for k in kinds_of(index)}
    want: dict = {}
    while budget > 0 and any(short.values()):
        k = max(short, key=lambda x: short[x])
        want[k] = want.get(k, 0) + 1
        short[k] -= 1
        budget -= 1
    return retire, want


def parse_lines(text: str) -> list[str]:
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return []
    return [str(x).strip() for x in got if isinstance(x, str) and str(x).strip()]


def acceptable(line: str, seen: set, fact: bool = False) -> bool:
    n = norm(line)
    if not n or n in seen or len(n.split()) > (FACT_WORDS if fact else MAX_WORDS) or re.search(r"\d|https?:|\*|#", line):
        return False
    return not (fact and NUMBER_WORDS.search(line))             # PLATINUM 21: no figure in a factoid, not even spelled out


GROUND_PROMPT = """A claim, and what a web search found about it. Do these sources CLEARLY and DIRECTLY state the claim's fact (ignore its joking tone)?
Answer with one word: "supported" only if they plainly do; "contradicted" if they say otherwise; "unclear" if they don't settle it.

Claim: {fact}

Sources:
{sources}"""


def web_search(query: str, get=None) -> list[str]:
    """Snippets for `query` from the house's own search (SearXNG on skippy-core, the same one purple Skippy uses)."""
    import os
    import urllib.parse
    import urllib.request
    base = os.environ.get("PURPLE_SEARCH_URL", "http://skippy-core:7360").rstrip("/")
    url = f"{base}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
    data = get(url) if get else json.loads(urllib.request.urlopen(url, timeout=20).read())
    return [f"{r.get('title', '')}: {r.get('content', '')}"[:400] for r in (data.get("results") or [])[:6] if isinstance(r, dict)]


def checked(ask, facts: list[str], search=web_search) -> list[str]:
    """The factoids the sources support (2026-10-31: a model checking itself passed a false one: "Daggerheart uses dice with symbols instead of
    numbers"). Each claim is searched on the web and kept only when the model, shown the snippets, calls it clearly supported; a claim with no
    search results, or an unclear one, is dropped."""
    kept = []
    for fact in facts:
        claim = re.sub(r"^(?:[^:]{0,60}:\s*)?", "", fact.split("Fun fact:")[-1]).strip() or fact
        try:
            sources = search(claim[:200])
        except Exception:  # noqa: BLE001 — no search, no proof: dropped
            sources = []
        if not sources:
            continue
        verdict = ask(GROUND_PROMPT.format(fact=claim, sources="\n".join(f"- {s}" for s in sources)))
        if re.search(r"\bsupported\b", verdict or "", re.I) and not re.search(r"\b(?:contradicted|unclear)\b", verdict or "", re.I):
            kept.append(fact)
    return kept


def write_lines(ask, kind: str, n: int, existing: list[str], examples: list[str], search=web_search) -> list[str]:
    """Up to n new lines of this kind from the model (`ask(prompt) -> text`), checked; two tries at most. A factoid (kind fact:<topic>) is
    also checked for truth by a second, strict pass."""
    fact = kind.startswith("fact:")
    seen = {norm(t) for t in existing}
    out: list[str] = []
    for _ in range(2):
        need = n - len(out)
        if need <= 0:
            break
        listing = "\n".join(f"  - {t}" for t in existing[-80:]) or "  (none yet)"
        if fact:
            prompt = FACT_PROMPT.format(hint=FACT_HINTS.get(kind[5:], kind[5:]), words=FACT_WORDS, n=need + 3, existing=listing)
        else:
            prompt = PROMPT.format(hint=KIND_HINTS.get(kind, "any request"), words=MAX_WORDS, n=need + 2, existing=listing,
                                   examples="\n".join(f"  - {t}" for t in examples[:6]))
        fresh = [line for line in parse_lines(ask(prompt)) if acceptable(line, seen, fact)]
        if fact:
            fresh = checked(ask, fresh, search)
        for line in fresh:
            if len(out) < n and norm(line) not in seen:
                out.append(line)
                seen.add(norm(line))
    return out


def file_for(kind: str, text: str) -> str:
    return f"{kind.replace(':', '-')}/q{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}.mp3"


def refresh(bank: Path, ask, tts, budget: int = 40, dry: bool = False, log=print, search=web_search) -> dict:
    index = load(bank / "index.json", {})
    plays = load(bank / "plays.json", {})
    retired = load(bank / "retired.json", {})
    retire, want = plan(index, plays, budget)
    report = {"retired": sum(len(v) for v in retire.values()), "planned": dict(want), "written": {}}
    log(f"retire {report['retired']}; write {sum(want.values())}: {want}")
    if dry:
        return report
    for kind, gone in retire.items():
        for e in gone:
            src, dst = bank / e["file"], bank / "retired" / e["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.move(str(src), str(dst))
            retired.setdefault(kind, []).append({**e, "plays": plays.get(e["file"], {}), "retired_at": time.strftime("%Y-%m-%d")})
        index[kind] = [e for e in index[kind] if e not in gone]
    for kind, n in want.items():
        existing = [e["text"] for e in index.get(kind, [])] + [e["text"] for e in retired.get(kind, [])]
        lines = write_lines(ask, kind, n, existing, [e["text"] for e in index.get(kind, [])] or existing, search)
        for text in lines:
            rel = file_for(kind, text)
            try:
                mp3 = tts(text)
            except Exception as exc:  # noqa: BLE001 — one line the voice won't say is skipped, the rest go on
                log(f"  {kind}: couldn't voice {text!r}: {type(exc).__name__}")
                continue
            (bank / rel).parent.mkdir(parents=True, exist_ok=True)
            (bank / rel).write_bytes(mp3)
            index.setdefault(kind, []).append({"file": rel, "text": text, "added": time.strftime("%Y-%m-%d")})
            report["written"][kind] = report["written"].get(kind, 0) + 1
            log(f"  {kind}: {text}")
    write_json(bank / "retired.json", retired)
    write_json(bank / "index.json", index)                    # last: the hub re-reads it when it changes
    log(f"done: retired {report['retired']}, wrote {sum(report['written'].values())}")
    return report


def vault() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--max", type=int, default=40)
    a = ap.parse_args()
    env = vault()
    import ear                                                  # noqa: PLC0415
    from voicegate import Gate                                  # noqa: PLC0415
    client, model = ear.brain_client(env, "quips")
    gate = Gate(env.get("SATELLITE_GATE", "http://127.0.0.1:3007"), env["SKIPPY_HOME_TOKEN"])

    def ask(prompt: str) -> str:
        r = client.messages.create(model=model, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
        return " ".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "quip refresh", flush=True)
    refresh(BANK, ask, gate.tts, budget=a.max, dry=a.dry, log=lambda s: print(s, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
