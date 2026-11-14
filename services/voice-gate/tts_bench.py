#!/usr/bin/env python3
"""Benchmark voice-gate TTS rendering.

Hits the gate's /t/<pass>/tts endpoint with each voice and a set of test
lines, measuring wall-clock time, audio size, and cache behaviour.

Usage:
    python tts_bench.py                        # single pass, stock + skippy
    python tts_bench.py --whole                # same, but whole=True (one F5 run)
    python tts_bench.py --repeats 5            # warm-up + 5 timed runs
    python tts_bench.py --url http://x:10000   # non-default gate URL
    python tts_bench.py --token <pass>         # pre-generated pass token

To generate a pass for the live gate:
    python tts_bench.py --gen-token            # prints a token and exits
"""

from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_mod
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

# ── test corpus ──────────────────────────────────────────────────────────────

CORPUS = [
    "Hello.",
    "The quick brown fox jumps over the lazy dog.",
    "Skippy here. What do you need?",
    "A long paragraph with many sentences. It keeps going and going. "
    "We want to see how the renderer handles sustained text, not just "
    "a single short phrase. This should be enough to trigger multiple "
    "internal sentence boundaries and test the streaming path properly.",
    "1, 2, 3, 4, 5, 6, 7, 8, 9, 10.",
    "What? No. Really? Okay.",
]

# ── pass helpers ─────────────────────────────────────────────────────────────

def _gate_key(home_token: str) -> bytes:
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac_mod.new(hashed.encode(), b"voice-gate-v1", hashlib.sha256).digest()


def _sign(key: bytes, exp: int, scope: str = "listen") -> str:
    text = f"voice-tts-{scope}:{exp}" if scope in ("stock", "skippy") else f"voice:{exp}"
    return f"{exp}.{hmac_mod.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _url(base: str, pass_: str, voice: str, whole: bool) -> str:
    """Build the POST URL with voice/whole query params."""
    params = urllib.parse.urlencode({"voice": voice, "whole": "1"} if whole else {"voice": voice})
    return f"{base}/t/{pass_}/tts?{params}"


def _post(url: str, text: str) -> tuple[int, int, float, str]:
    """POST text to the gate, return (status, bytes, ms, cache_header)."""
    body = json.dumps({"text": text}).encode()
    t0 = time.perf_counter()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            elapsed = (time.perf_counter() - t0) * 1000
            cache = resp.headers.get("X-Voice-Cache", "?")
            return resp.status, len(data), elapsed, cache
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return exc.code, 0, elapsed, "?"


# ── bench ────────────────────────────────────────────────────────────────────

def bench(base: str, pass_: str, whole: bool, repeats: int) -> None:
    voices = ("stock", "skippy")
    header = f"{'voice':<10} {'len':>6}  {'ms':>8}  {'B/s':>10}  {'cache':>6}  {'status':>5}"

    for run in range(1, repeats + 1):
        label = f"\n--- run {run}/{repeats}  whole={whole}  gate={base} ---"
        print(label)
        print(header)
        print("-" * len(header))

        for voice in voices:
            url = _url(base, pass_, voice, whole)
            for line in CORPUS:
                status, size, ms, cache = _post(url, line)
                bps = int(size / (ms / 1000)) if ms > 0 else 0
                print(f"{voice:<10} {len(line):>6}  {ms:>8.1f}  {bps:>10,}  {cache:>6}  {status:>5}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark voice-gate TTS")
    p.add_argument("--url", default="http://localhost:10000", help="Gate base URL")
    p.add_argument("--pass", default=None, dest="pass_", help="Gate pass token")
    p.add_argument("--whole", action="store_true", help="Send whole=True (one F5 run)")
    p.add_argument("--repeats", type=int, default=1, help="Number of timed runs (1 = warm-up + 1)")
    p.add_argument("--token", default=None, help="Pre-generated pass token (same as --pass)")
    p.add_argument("--gen-token", action="store_true", help="Generate a pass token and exit")
    args = p.parse_args()

    token = args.pass_ or args.token or os.environ.get("VOICE_GATE_PASS", "")

    # Generate a token if asked
    if args.gen_token:
        home_token = os.environ.get("SKIPPY_HOME_TOKEN", "")
        if not home_token:
            print("SKIPPY_HOME_TOKEN not set — generating a local test token (won't work against a real gate)", file=sys.stderr)
            home_token = "test"
        key = _gate_key(home_token)
        t = _sign(key, int(time.time()) + 3600, "skippy")
        print(t)
        return

    if not token:
        print("Error: --token/--pass required, or set VOICE_GATE_PASS env var", file=sys.stderr)
        sys.exit(1)

    # Health check
    try:
        with urllib.request.urlopen(f"{args.url}/health", timeout=5) as resp:
            print(f"gate health: {resp.status} OK")
    except urllib.error.HTTPError as exc:
        print(f"gate health: {exc.code} (continuing anyway)", file=sys.stderr)
    except OSError as exc:
        print(f"gate unreachable: {exc}", file=sys.stderr)
        sys.exit(1)

    bench(args.url, token, args.whole, args.repeats)


if __name__ == "__main__":
    main()
