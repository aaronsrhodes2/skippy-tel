#!/usr/bin/env python3
"""
skippy_test.py — End-to-end functional test harness for the Skippy ecosystem.

Tests fall into two categories:
  A) SkippyTel API tests   — exercised via HTTP; automated pass/fail
  B) Phone-side tests      — require physical phone; instructions printed for manual run

Usage:
  python tools/skippy_test.py              # run all tests
  python tools/skippy_test.py --tier       # tier-routing tests only
  python tools/skippy_test.py --health     # health check only
  python tools/skippy_test.py --manual     # show manual phone test instructions

Colours are ANSI — works in PowerShell 7+, Windows Terminal, and standard terminals.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import http.client
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
BASE = "http://localhost:3003"
HOST = "localhost"
PORT = 3003
TIMEOUT = 15   # seconds per request

# ── Persistent HTTP connection (avoids Windows WinError 10048 port exhaustion) ─
_conn: Optional[http.client.HTTPConnection] = None

def _get_conn() -> http.client.HTTPConnection:
    global _conn
    if _conn is None:
        _conn = http.client.HTTPConnection(HOST, PORT, timeout=TIMEOUT)
    return _conn

def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    conn = _get_conn()
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))
    for attempt in range(2):
        try:
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            raw = r.read()
            return json.loads(raw.decode())
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
            # Server closed keep-alive connection — reconnect once
            global _conn
            _conn = http.client.HTTPConnection(HOST, PORT, timeout=TIMEOUT)
            conn = _conn
    raise RuntimeError(f"Request {method} {path} failed after retry")

# ── ANSI colours ──────────────────────────────────────────────────────────────
G  = "\033[92m"   # bright green
R  = "\033[91m"   # bright red
Y  = "\033[93m"   # amber/yellow
C  = "\033[96m"   # cyan
V  = "\033[95m"   # violet
DG = "\033[32m"   # dim green
W  = "\033[97m"   # white
RS = "\033[0m"    # reset

def ok(msg):   print(f"  {G}✓{RS} {msg}")
def fail(msg): print(f"  {R}✗{RS} {msg}")
def info(msg): print(f"  {C}·{RS} {msg}")
def warn(msg): print(f"  {Y}!{RS} {msg}")
def head(msg): print(f"\n{W}{msg}{RS}")
def rule():    print(f"{DG}{'─'*70}{RS}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def get(path) -> dict:
    return _request("GET", path)


def post(path, body: dict) -> dict:
    return _request("POST", path, body)


def send(text: str, source: str = "test") -> dict:
    """POST to /intent/unmatched — the main AI pipeline."""
    return post("/intent/unmatched", {"text": text, "source": source})


# ── Test result tracking ──────────────────────────────────────────────────────
passed = 0
failed = 0

def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        ok(f"{label}{(' — ' + detail) if detail else ''}")
    else:
        failed += 1
        fail(f"{label}{(' — ' + detail) if detail else ''}")

def check_exc(label: str, exc: Exception):
    """Register a test failure caused by an unexpected exception."""
    global failed
    failed += 1
    fail(f"{label} — exception: {exc}")

def check_tier(label: str, response: dict, expected_tier: str):
    tier = response.get("tier", "")
    check(label, tier == expected_tier,
          f"got tier={tier!r}, expected {expected_tier!r}")

def check_reply(label: str, response: dict, min_len: int = 5):
    reply = response.get("reply", "")
    check(label, len(reply) >= min_len,
          f"reply: {reply[:80]!r}" if reply else "empty reply")


# ═══════════════════════════════════════════════════════════════════════════════
# A) HEALTH TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_health():
    head("A1 — SkippyTel Health")
    rule()
    try:
        h = get("/health")
        check("Status is ok",          h.get("status") == "ok")
        check("spec_version present",  "spec_version" in h)

        models = h.get("models", {})
        ollama = models.get("ollama", {})
        check("Ollama is available",   ollama.get("available") is True,
              f"models: {ollama.get('models','(none)')}")

        tiers = models.get("tiers", {})
        check("ElevenLabs tier key live",   tiers.get("elevenlabs") is True)
        check("Nagatha tier key live",      tiers.get("nagatha")    is True)

        info(f"Raw health: {json.dumps(h, indent=None)}")

    except Exception as e:
        check_exc("Health endpoint", e)

    # Compare with what the phone thinks (from portal chat-state)
    head("A2 — Portal chat-state (phone's view of SkippyTel)")
    rule()
    try:
        cs = get("/portal/chat-state")
        candy = cs.get("candy", {})
        tl = candy.get("tl", {})
        tr = candy.get("tr", {})
        check("Phone TL candy not red",
              tl.get("color") != "red",
              f"TL color={tl.get('color')}, row1={tl.get('row1')}")
        check("Phone TR signal not empty",
              tr.get("row1", "░░░░") != "░░░░",
              f"TR row1={tr.get('row1')} color={tr.get('color')}")
        info(f"Phone sees candy: TL={tl.get('color')} TR={tr.get('row1')}/{tr.get('color')}")
    except Exception as e:
        warn(f"Portal chat-state not available (phone not pushing yet?): {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# B) TIER ROUTING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_tier_routing():
    head("B1 — Default Ollama tier (no special keyword)")
    rule()
    try:
        r = send("What color is the sky?")
        check_tier("Default prompt → ollama tier", r, "local")
        check_reply("Got a real reply", r)
        info(f"Reply: {r.get('reply','')[:100]}")
    except Exception as e:
        check_exc("Default Ollama request", e)

    head("B2 — Nagatha tier triggers (Gemini)")
    rule()
    nagatha_phrases = [
        "hey nagatha what do you think?",
        "ask nagatha about the weather",
        "talk to nagatha please",
        "nagatha, how are you?",
    ]
    for phrase in nagatha_phrases:
        try:
            r = send(phrase)
            check_tier(f"Nagatha trigger: {phrase!r}", r, "nagatha")
            info(f"  Reply: {r.get('reply','')[:80]}")
        except Exception as e:
            check_exc(f"Nagatha trigger: {phrase!r}", e)

    head("B4 — Trust the awesomeness (bridge bypass)")
    rule()
    try:
        r = send("Trust the awesomeness. Say hello.")
        tier = r.get("tier", "")
        if tier == "trust_awesomeness":
            ok(f"Bridge tier hit: trust_awesomeness")
            check_reply("Got a reply", r)
        else:
            warn(f"Bridge not running or different tier={tier!r}. "
                 f"Start bridge.py before demo.")
        info(f"Reply: {r.get('reply','')[:100]}")
    except Exception as e:
        check_exc("Trust the awesomeness request", e)

    head("B5 — Solid gold (CI/CD plan mode bridge)")
    rule()
    try:
        r = send("Solid gold, write a test.")
        tier = r.get("tier", "")
        if tier == "solid_gold":
            ok(f"Solid gold tier hit")
        else:
            warn(f"Solid gold bridge not running, tier={tier!r}")
        info(f"Reply: {r.get('reply','')[:100]}")
    except Exception as e:
        check_exc("Solid gold request", e)


# ═══════════════════════════════════════════════════════════════════════════════
# C) SPANISH TRANSLATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_translation():
    head("C1 — Spanish → English translation")
    rule()
    cases = [
        # Use non-ASCII so the fast-path ASCII shortcut doesn't override detection
        ("Hola, ¿cómo estás?",  "es", "hello"),
        ("Buenos días capián",        "es", "morning"),
        ("This is already English",             "en", "english"),
    ]
    for text, expected_lang, keyword_hint in cases:
        try:
            r = post("/translate/text", {"text": text})
            detected = r.get("detected_lang", "?")
            english  = r.get("english_text", "")
            check(f"Lang detected for {text!r}",
                  detected == expected_lang,
                  f"detected={detected}, english={english!r}")
            info(f"  -> {english!r}")
        except Exception as e:
            check_exc(f"Translation for {text!r}", e)


# ═══════════════════════════════════════════════════════════════════════════════
# D) BILBY / MUSIC TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_bilby():
    head("D1 — Bilby status")
    rule()
    try:
        r = get("/bilby/status")
        source = r.get("source", "none")
        if source != "none":
            ok(f"Bilby source active: {source}")
            np = r.get("now_playing") or {}
            if np:
                info(f"Now playing: {np.get('title','?')} - {np.get('artist','?')}")
            else:
                info("Bilby reachable but nothing playing")
        else:
            warn("Bilby source=none (Mac DJ daemon not running?)")
        check("Bilby response has source field", "source" in r)
    except Exception as e:
        warn(f"Bilby status timed out (Mac daemon probably not running): {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# E) PHONE-STATE RELAY TEST (skippy core serves data only: the old /portal page is gone)
# ═══════════════════════════════════════════════════════════════════════════════

def test_portal():
    head("E1 — HUD / chat state relay (what the phone pushes)")
    rule()
    try:
        hud = get("/portal/hud-state")
        check("HUD state has its corners",   isinstance(hud.get("corners"), dict)
              and {"tl", "tr", "bl", "br"} <= set(hud["corners"]))
        chat = get("/portal/chat-state")
        check("Chat state has candy",        isinstance(chat.get("candy"), dict))
    except Exception as e:
        check_exc("State relay check", e)

    head("E2 — Chat history accessible")
    rule()
    try:
        msgs = get("/portal/history?limit=5")
        check("History returns a list", isinstance(msgs, list))
        info(f"{len(msgs)} messages in history")
        if msgs:
            m = msgs[-1]
            info(f"Last: [{m.get('role')}] {m.get('text','')[:60]}")
    except Exception as e:
        check_exc("Chat history", e)


# ═══════════════════════════════════════════════════════════════════════════════
# F) MANUAL / PHONE-SIDE TEST INSTRUCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def print_manual_tests():
    head("F — Phone-side manual tests (voice commands via ADB or spoken)")
    rule()
    manual_tests = [
        ("F01", "Flashlight on",
         "Say: 'flashlight on' or 'torch on'",
         "Expected: torch LED fires, Skippy says 'Lights on'"),
        ("F02", "Flashlight off",
         "Say: 'flashlight off' or 'torch off'",
         "Expected: torch LED off, Skippy confirms"),
        ("F03", "Brightness up",
         "Say: 'brighter' or 'brightness up'",
         "Expected: screen brightness increases ~20%"),
        ("F04", "Brightness down",
         "Say: 'dimmer' or 'brightness down'",
         "Expected: screen brightness decreases ~20%"),
        ("F05", "Volume up",
         "Say: 'louder' or 'volume up'",
         "Expected: system volume increases 2 steps"),
        ("F06", "Volume down",
         "Say: 'softer' or 'volume down'",
         "Expected: system volume decreases"),
        ("F07", "Mute / skippy sleep",
         "Say: 'skippy sleep' or 'mute all'",
         "Expected: STT goes hotword-only (mic muted)"),
        ("F08", "Unmute / skippy wake",
         "Say: 'skippy wake' or 'hey skippy'",
         "Expected: full STT resumes"),
        ("F09", "Cancel draft",
         "Speak any phrase, then say: 'cancel that'",
         "Expected: draft input cleared"),
        ("F10", "Send now",
         "Speak a phrase, then say: 'send it'",
         "Expected: sent immediately, no 8s wait"),
        ("F11", "Planning mode enter",
         "Say: 'let's make a plan'",
         "Expected: PLANNING mode indicator, no auto-send"),
        ("F12", "Planning mode exit",
         "While in planning mode say: 'done planning'",
         "Expected: returns to VIBE mode"),
        ("F13", "Auto-send timer",
         "Speak any phrase, then stay silent for 8 seconds",
         "Expected: prompt sends automatically"),
        ("F14", "Shades on",
         "Say: 'put on the shades'",
         "Expected: screen goes black (SHADES mode)"),
        ("F15", "Shades off (eyes open)",
         "In SHADES mode, say: 'eyes open'",
         "Expected: returns to normal chat view"),
        ("F16", "Spanish mode",
         "Say: 'español' or 'speak spanish'",
         "Expected: STT biased to Spanish, translations appear in glasses"),
        ("F17", "Back to English",
         "In Spanish mode say: 'back to english'",
         "Expected: returns to English STT"),
        ("F18", "Predator vision (glasses)",
         "With glasses connected, say: 'predator vision'",
         "Expected: 7 color tint modes cycle on glasses at 2.5s each"),
        ("F19", "Predator cancel",
         "During predator cycle say: 'predator vision' again",
         "Expected: cycle stops, returns to normal"),
        ("F20", "Skip track (Bilby)",
         "Say: 'next track' or 'skip song'",
         "Expected: Bilby advances to next track"),
        ("F21", "Now playing (Bilby)",
         "Say: 'what's playing' or 'who sings this'",
         "Expected: track title/artist read aloud"),
        ("F22", "CI/CD dot demo",
         "Say: 'blue dot demo'",
         "Expected: Skippy announces CI/CD, Claude bridge runs, dot appears (~2 min)"),
        ("F23", "Dot color change",
         "After dot appears, say: 'demo dot orange'",
         "Expected: new dot color in ~2 min"),
        ("F24", "Dot rollback",
         "Say: 'remove the dot'",
         "Expected: DemoDotOverlay removed, app re-deployed"),
    ]

    for tid, name, action, expected in manual_tests:
        print(f"\n  {C}{tid}{RS} {W}{name}{RS}")
        print(f"       Action:   {action}")
        print(f"       Expected: {Y}{expected}{RS}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global passed, failed

    parser = argparse.ArgumentParser(description="Skippy ecosystem test harness")
    parser.add_argument("--health",  action="store_true", help="Health tests only")
    parser.add_argument("--tier",    action="store_true", help="Tier routing tests only")
    parser.add_argument("--portal",  action="store_true", help="Phone-state relay tests only")
    parser.add_argument("--manual",  action="store_true", help="Show manual phone tests")
    parser.add_argument("--all",     action="store_true", help="Run all automated + show manual")
    args = parser.parse_args()

    run_all = not any([args.health, args.tier, args.portal, args.manual, args.all])

    print(f"\n{W}=== SKIPPY ECOSYSTEM TEST HARNESS  .  {BASE} ==={RS}")

    t0 = time.time()

    if run_all or args.all or args.health:
        test_health()

    if run_all or args.all or args.tier:
        test_tier_routing()
        test_translation()
        test_bilby()

    if run_all or args.all or args.portal:
        test_portal()

    if args.manual or args.all:
        print_manual_tests()

    elapsed = time.time() - t0
    rule()
    total = passed + failed
    color = G if failed == 0 else R
    print(f"\n{color}{'PASS' if failed == 0 else 'FAIL'}{RS}  "
          f"{G}{passed}/{total} passed{RS}  "
          f"{'  ' + R + str(failed) + ' failed' + RS if failed else ''}  "
          f"  {DG}{elapsed:.1f}s{RS}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
