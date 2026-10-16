#!/usr/bin/env python3
"""Voice capture: render a planned set of Skippy lines with ElevenLabs once, as teacher data for an own local voice.

    python tools/voice_capture.py                                  # DRY RUN: the plan, the characters, the credits. Spends nothing.
    python tools/voice_capture.py --go --max-credits 3500 --tier 1 # spends, up to that many credits, tier 1 only
    python tools/voice_capture.py --go --max-credits 2000 --tier 2 # later: the next tier (finished lines are skipped)

Credits cannot be re-spent, so this is built to be safe to run twice:
  - the default is a dry run; spending needs `--go` AND an explicit `--max-credits` (there is no default ceiling);
  - lines run in priority order (tier 1, then 2, then 3), and the run stops before the ceiling would be passed;
  - a line whose audio already exists is never rendered again;
  - a refused key or an empty balance (HTTP 401/402/403) stops the run at once and keeps everything made so far;
  - the key is read from the environment or the vault `.env` and is never printed.

It sends what production sends (text with the persona's emotion tags, `model_id`, `voice_settings: {stability}`), so the teacher IS the live
Skippy. Emotion tags only work on the v3 model; match `--model` to ELEVENLABS_MODEL in the vault `.env` (a warning says when they will not work).

Output (default state/voice-capture/skippy, gitignored):
    audio/<id>.mp3     one file per line
    manifest.jsonl     id, text_with_tags, plain_text, audio, chars, tier, group, split (train/holdout), model, voice_id, stability, ...
    failed.jsonl       lines ElevenLabs rejected (not fatal)

Personal use only: the cloned voice is built from an audiobook narrator's voice. The audio and any model trained on it stay on the Captain's machines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LINES = Path(__file__).with_name("voice_capture_lines.jsonl")
DEFAULT_OUT = ROOT / "state" / "voice-capture" / "skippy"
VAULT_ENV = "G:/My Drive/vault/.env"
API = "https://api.elevenlabs.io/v1"
TAGS = ("sighs", "chuckles", "laughs", "sarcastic", "whispers", "excited")      # skippy_persona.md "Emotion tags"
TAG_RE = re.compile(r"\[([a-z ]+)\]")
MAX_CHARS = 500                    # voice-gate refuses more
BYTES_PER_SECOND = 16000           # mp3_44100_128
CHARS_PER_SECOND = 15              # speaking rate, for the dry-run estimate


class Fatal(Exception):
    """The run cannot go on (key refused, balance gone): stop and keep what exists."""


class Skip(Exception):
    """ElevenLabs rejected this one line: log it and carry on."""


def load_lines(path) -> list[dict]:
    rows = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    return sorted(rows, key=lambda r: r["tier"])           # stable: file order within a tier


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", text)).strip()


def split_for(line_id: str) -> str:
    """Roughly a tenth of the lines are held out for the blind test; the choice is a hash of the id, so it never changes."""
    return "holdout" if int(hashlib.sha1(line_id.encode()).hexdigest()[:8], 16) % 10 == 0 else "train"


def cost(text: str, per_char: float) -> int:
    return math.ceil(len(text) * per_char)


def plan(rows, done_ids, max_tier, max_credits, per_char) -> dict:
    todo, credits = [], 0
    for r in sorted(rows, key=lambda r: r["tier"]):          # stable: file order within a tier
        if r["tier"] > max_tier or r["id"] in done_ids:
            continue
        c = cost(r["text"], per_char)
        if credits + c > max_credits:
            break                                           # priority order: never skip ahead to cheaper, lower-tier lines
        todo.append(r)
        credits += c
    return {"todo": todo, "credits": credits, "chars": sum(len(r["text"]) for r in todo)}


def read_env_file(path) -> dict:
    out = {}
    try:
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def render(api_key, voice_id, model, stability, text, output_format, timeout=120) -> bytes:
    body = json.dumps({"text": text, "model_id": model, "voice_settings": {"stability": stability}}).encode()
    req = urllib.request.Request(
        f"{API}/text-to-speech/{voice_id}?output_format={output_format}", data=body, method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 402, 403):
                raise Fatal(f"ElevenLabs refused: out of credits or key not allowed (HTTP {e.code})") from None
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise Skip(f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise Skip(f"network: {type(e).__name__}") from None
    raise Skip("retries exhausted")


def done_ids(out: Path) -> set[str]:
    audio = out / "audio"
    return {p.stem for p in audio.glob("*.mp3") if p.stat().st_size > 0} if audio.is_dir() else set()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lines", default=str(DEFAULT_LINES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--env-file", default=VAULT_ENV if Path(VAULT_ENV).exists() else "")
    ap.add_argument("--voice-id", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--stability", type=float, default=0.05, help="what production sends (tts_settings.DEFAULTS)")
    ap.add_argument("--output-format", default="mp3_44100_128")
    ap.add_argument("--tier", type=int, default=1, choices=(1, 2, 3), help="include lines up to this tier")
    ap.add_argument("--credits-per-char", type=float, default=1.0, help="1.0 is the cautious guess; some models bill half")
    ap.add_argument("--max-credits", type=int, default=0, help="REQUIRED with --go: the most this run may spend")
    ap.add_argument("--pause", type=float, default=0.4, help="seconds between calls")
    ap.add_argument("--go", action="store_true", help="actually render (spends credits)")
    a = ap.parse_args(argv)
    if a.go and a.max_credits <= 0:
        ap.error("--go needs an explicit --max-credits (credits cannot be re-spent)")

    env = {**read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith("ELEVENLABS_")}} if a.env_file else dict(os.environ)
    key = env.get("ELEVENLABS_API_KEY", "")
    voice = a.voice_id or env.get("ELEVENLABS_VOICE_ID", "")
    model = a.model or env.get("ELEVENLABS_MODEL", "") or "eleven_v3"
    out = Path(a.out)
    rows = load_lines(a.lines)
    ceiling = a.max_credits if a.go else 10**9
    p = plan(rows, done_ids(out), a.tier, ceiling, a.credits_per_char)

    print(f"{'' if a.go else 'DRY RUN. '}voice={voice or '(missing)'} model={model} stability={a.stability} tier<={a.tier}")
    if any(TAG_RE.search(r["text"]) for r in p["todo"]) and "v3" not in model:
        print(f"WARNING: model {model} is not v3, so the emotion tags in these lines will not be performed (they may be read aloud).")
    print(f"{len(p['todo'])} lines to render, {p['chars']} characters, about {p['credits']} credits at {a.credits_per_char}/char, "
          f"roughly {p['chars'] / CHARS_PER_SECOND / 60:.1f} minutes of speech")
    by_tier = {}
    for r in p["todo"]:
        by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + cost(r["text"], a.credits_per_char)
    print("by tier (credits):", ", ".join(f"tier {t}: {c}" for t, c in sorted(by_tier.items())) or "nothing left to do")
    if not a.go:
        print("Nothing was sent. Add --go --max-credits N to spend.")
        return 0
    if not key or not voice:
        print("Missing ELEVENLABS_API_KEY or voice id (set them, or pass --env-file / --voice-id).")
        return 2

    (out / "audio").mkdir(parents=True, exist_ok=True)
    spent = 0
    for n, r in enumerate(p["todo"], 1):
        try:
            audio = render(key, voice, model, a.stability, r["text"], a.output_format)
        except Fatal as e:
            print(f"STOPPED after {n - 1} lines, about {spent} credits: {e}")
            return 1
        except Skip as e:
            with (out / "failed.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"id": r["id"], "why": str(e)}) + "\n")
            print(f"[{n}/{len(p['todo'])}] {r['id']} skipped: {e}")
            continue
        target = out / "audio" / f"{r['id']}.mp3"
        tmp = target.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(target)
        spent += cost(r["text"], a.credits_per_char)
        row = {"id": r["id"], "text_with_tags": r["text"], "plain_text": strip_tags(r["text"]), "audio": f"audio/{r['id']}.mp3",
               "chars": len(r["text"]), "tier": r["tier"], "group": r["group"], "split": split_for(r["id"]), "model": model,
               "voice_id": voice, "stability": a.stability, "approx_seconds": round(len(audio) / BYTES_PER_SECOND, 1),
               "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with (out / "manifest.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{n}/{len(p['todo'])}] {r['id']} ok ({len(r['text'])} chars, running total about {spent} credits)")
        time.sleep(a.pause)
    print(f"Done: about {spent} credits spent. Output in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
