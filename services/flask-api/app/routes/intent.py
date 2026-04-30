"""POST /intent/unmatched — escalation for voice commands the phone can't classify.

Photo search:
  Phrases like "show me that picture of X", "find a photo of Y",
  "show me the picture we just took" are intercepted before LLM routing.
  They call /dropship/search or /dropship/latest, then proxy the top
  hit as an imageBase64 field in the response so SkippyChat renders it
  inline in the feed.

Contract (from misc/SKIPPYTEL_BRIEF.md):
  Request:  { text, source?, context? }
  Response: { reply?: string, speak?: string, tier: string, ... }
  Latency:  < 2s. Streamed reply OK via SSE.

Routing — EXPLICIT-ONLY phrase triggers (Captain rule, 2026-04-24):
  - Default → Ollama (settings.ollama_model, currently qwen2.5:7b)
  - Each higher tier has ONE literal phrase that activates it.
  - NO auto-fallback between tiers (would silently burn tokens on a
    tuning bug or infinite loop). Tier failure → 503 with clear error.
  - NO `model_hint` URL parameter — phrases are the only knob.

Tier registry: order = precedence (first match wins). Adding a tier =
one new client module + one TIERS entry.

Streaming: clients send `Accept: text/event-stream` to opt in.
SSE event shape:
  data: {"spec_version":"1","type":"start","tier":"local"|"claude"|"gemini"}
  data: {"type":"token","text":"..."}        (prefix first if any, then model)
  data: {"type":"done","reply":"<full incl. prefix>"}
"""
import base64
import json
import re
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

import httpx
from flask import Blueprint, Response, request, stream_with_context

from app.clients import ollama, anthropic_client, gemini, claude_bridge
from app.config  import settings
from app import keyword_memory as km
from app import tts_settings
from app import conversation_store as cs
from app.narration import get_narration
from app.version import envelope, error, scrub, SPEC_VERSION

# ── Music session shortcut ────────────────────────────────────────────────────
# Intercepts music-request phrases BEFORE LLM routing so Skippy doesn't
# hallucinate a now-playing track. Calls /music/session directly and returns
# the real track info. Pattern matches both explicit ("play some jazz") and
# Bilby-addressed forms ("bilby dj", "bilby play some rock").

_MUSIC_INTENT_RE = re.compile(
    r'\b(?:play|queue|put on|start playing|let[\'\s]+hear|i want to hear|turn on)\b'
    r'.{0,60}\b(?:music|songs?|tracks?|playlist|jazz|rock|hip[\s\-]?hop|electronic|'
    r'beats?|tunes?|vibes?|classical|country|metal|punk|folk|blues|soul|r&b|'
    r'reggae|ambient|techno|house|drum[\s\-]?(?:and[\s\-]?)?bass|indie|pop|funk|'
    r'disco|chill|upbeat|energetic|relaxing|workout|party|something)\b'
    r'|\bbilby\s+(?:play|dj|start|queue|go|put)',
    re.IGNORECASE,
)
_MUSIC_STRIP_RE = re.compile(
    r'^(?:bilby\s+)?(?:play\s+(?:some|me\s+some|me\s+a?|a\s+|some\s+)?|'
    r'queue\s+(?:up\s+)?(?:some\s+)?|put\s+on\s+(?:some\s+)?|'
    r'start\s+playing\s+(?:some\s+)?|start\s+playing\s+|'
    r'i\s+want\s+to\s+hear\s+(?:some\s+)?|turn\s+on\s+(?:some\s+)?)',
    re.IGNORECASE,
)
_MUSIC_GENERIC_WORDS = frozenset({
    'music', 'songs', 'song', 'tracks', 'track', 'tunes', 'tune',
    'playlist', 'please', 'dj', 'something', 'stuff', 'anything', 'whatever',
})


def _try_music_intent(text: str) -> Optional[dict]:
    """
    If text looks like a music request, start a Bilby session and return
    the real track info. Returns None if this isn't a music query.

    Never hallucinate: either we get a real track from /music/session
    or we return an honest "no tracks available" message.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    if not _MUSIC_INTENT_RE.search(text):
        return None

    # Extract genre/mood by stripping the trigger verb phrase
    query = _MUSIC_STRIP_RE.sub("", text.strip()).strip()
    # Strip leading/trailing filler articles
    query = re.sub(r'^(?:some|a|an|the|me|us|please)\s+', '', query, flags=re.IGNORECASE).strip()
    # If only generic words remain → DJ mode (Bilby picks)
    words = query.lower().split()
    if not words or all(w in _MUSIC_GENERIC_WORDS for w in words):
        query = ""

    mode = "playlist" if query else "dj"
    _logger.info("music intent: mode=%s query=%r", mode, query)

    try:
        import httpx as _httpx
        body: dict = {"mode": mode}
        if query:
            body["prompt"] = query
        r = _httpx.post("http://localhost:5000/music/session", json=body, timeout=15)
        if r.status_code == 200:
            data = r.json()
            session = data.get("data", data)
            np = session.get("now_playing") or {}
            artist = np.get("artist", "")
            title  = np.get("title",  "")
            mode_label = f"playlist: {query}" if query else "DJ mode"
            if artist and title:
                reply = f"♫ Bilby: {mode_label} — {artist} — {title}"
            else:
                reply = f"♫ Bilby: {mode_label} started."
            resp: dict = {
                "reply":         reply,
                "speak":         reply,
                "tier":          "bilby",
                "narration":     get_narration("bilby", title or None),
                "music_session": session,
            }
            vid = _tier_voice_id("bilby")
            if vid:
                resp["voice_id"] = vid
            return resp
        elif r.status_code == 404:
            reply = "♫ Bilby: no tracks available. Is the music library mounted at /music?"
            return {"reply": reply, "speak": "Bilby couldn't find any tracks.", "tier": "bilby"}
        else:
            _logger.warning("music intent: /music/session returned %d", r.status_code)
    except Exception as exc:
        _logger.warning("music intent error: %s", exc)

    return None


# ── Photo search ─────────────────────────────────────────────────────────────

# Patterns that mean "fetch an existing photo from DropShip"
_PHOTO_LATEST_RE = re.compile(
    r'\b(show me (that |the )?(photo|picture|pic|image) (we |I )?(just |recently )?(took|took)|'
    r'(last|latest|most recent) (photo|picture|pic|image)|'
    r'photo (we|I) just took)\b',
    re.IGNORECASE,
)
_PHOTO_SEARCH_RE = re.compile(
    r'\b(show me|find|search for|pull up|get me)\b.{0,30}\b(photo|picture|pic|image|photos|pictures)\b',
    re.IGNORECASE,
)

# Strip the search verb from the query before passing to /dropship/search
_PHOTO_VERB_RE = re.compile(
    r'^(show me|find|search for|pull up|get me)\s+(a |an |the |that |some )?'
    r'(photo|photos|picture|pictures|pic|pics|image|images)\s*(of|with|from|when|where|we|i|about)?\s*',
    re.IGNORECASE,
)


def _try_photo_query(text: str) -> Optional[dict]:
    """
    If `text` looks like a photo retrieval request, call DropShip and
    return a pre-formed response dict {reply, speak, tier, imageBase64}.
    Returns None if this isn't a photo query.
    """
    lower = text.lower()

    # "show me the picture we just took" → /dropship/latest
    if _PHOTO_LATEST_RE.search(lower):
        return _fetch_latest_photo()

    # "show me a photo of me and Adam at Disneyland" → /dropship/search
    if _PHOTO_SEARCH_RE.search(lower):
        q = _PHOTO_VERB_RE.sub("", text).strip()
        if q:
            return _fetch_photo_search(q)

    return None


def _fetch_latest_photo() -> dict:
    try:
        base = f"http://localhost:{settings.port if hasattr(settings, 'port') else 5000}"
        r = httpx.get(f"{base}/dropship/latest?n=1", timeout=10)
        if r.status_code == 200:
            data = r.json()
            hits = data.get("results", [])
            if hits:
                hit = hits[0]
                name = hit.get("drive_name", "photo")
                img_b64 = _download_as_b64(hit["image_url"])
                reply = f"Here's the most recent photo — {name}."
                resp = {"reply": reply, "speak": reply, "tier": "dropship"}
                if img_b64:
                    resp["imageBase64"] = img_b64
                resp["narration"] = get_narration("dropship")
                return resp
        return {"reply": "I couldn't find any recent photos in DropShip.", "speak": "Couldn't find any recent photos.", "tier": "dropship", "narration": get_narration("dropship")}
    except Exception as exc:
        return {"reply": f"DropShip error: {exc}", "speak": "DropShip had an error.", "tier": "dropship", "narration": get_narration("dropship")}


def _fetch_photo_search(query: str) -> dict:
    try:
        base = f"http://localhost:{settings.port if hasattr(settings, 'port') else 5000}"
        r = httpx.get(f"{base}/dropship/search", params={"q": query, "limit": 1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            hits = data.get("results", [])
            if hits:
                hit = hits[0]
                name = hit.get("drive_name", "photo")
                desc = hit.get("description", "")
                img_b64 = _download_as_b64(hit["image_url"])
                reply = f"Found a match: {name}." + (f" {desc}" if desc else "")
                resp = {"reply": reply, "speak": f"Found it — {name}.", "tier": "dropship"}
                if img_b64:
                    resp["imageBase64"] = img_b64
                resp["narration"] = get_narration("dropship")
                return resp
        return {"reply": f"No photos matched '{query}'.", "speak": f"No photos found for {query}.", "tier": "dropship", "narration": get_narration("dropship")}
    except Exception as exc:
        return {"reply": f"DropShip error: {exc}", "speak": "DropShip had an error.", "tier": "dropship", "narration": get_narration("dropship")}


def _download_as_b64(url: str) -> Optional[str]:
    """Download an image URL and return it as a base64 data-URL string."""
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True)
        if r.status_code == 200:
            mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
            b64  = base64.b64encode(r.content).decode()
            return f"data:{mime};base64,{b64}"
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("_download_as_b64 failed: %s", exc)
    return None


# ── TTS_ADJUST annotation ────────────────────────────────────────────────
# LLM emits [TTS_ADJUST: {"stability": 0.8}] to adjust its own voice.
# We strip it before returning the reply text and apply it to the persistent store.
_TTS_ADJUST_RE = re.compile(r'\[TTS_ADJUST:\s*(\{[^}]+\})\s*\]', re.IGNORECASE | re.DOTALL)


def _extract_and_apply_tts_adjust(text: str) -> str:
    """Strip [TTS_ADJUST: {...}] annotations, apply settings, return clean text."""
    import logging as _log
    _logger = _log.getLogger(__name__)
    for m in _TTS_ADJUST_RE.finditer(text):
        try:
            data = json.loads(m.group(1))
            tts_settings.update(data)
            _logger.info("intent: TTS_ADJUST applied: %s", data)
        except Exception as exc:
            _logger.warning("intent: TTS_ADJUST parse failed: %s", exc)
    return _TTS_ADJUST_RE.sub("", text).strip()


# ── Voice routing ─────────────────────────────────────────────────────────
# Each tier maps to a persona-specific ElevenLabs voice ID.
# Empty string = fall back to Skippy's default voice.
def _tier_voice_id(tier_id: str) -> str | None:
    """Return the ElevenLabs voice_id for this tier, or None to use Skippy's default."""
    mapping = {
        "nagatha":         settings.nagatha_voice_id,
        "bilby":           settings.bilby_voice_id,
    }
    vid = mapping.get(tier_id, "")
    return vid if vid else None


# ── Image sentinel (stub) ─────────────────────────────────────────────────
# LLMs may still emit [IMAGE: <prompt>] — we strip the tag so it doesn't
# appear in the reply text. Image generation itself is not yet deployed;
# the sentinel is preserved in the protocol for when we stand up a
# first-party SD service.
_IMAGE_RE = re.compile(r'\[IMAGE:\s*(.+?)\]', re.IGNORECASE)


def _extract_image_prompt(text: str) -> tuple[str, str | None]:
    """Strip [IMAGE: ...] sentinel from text; return (clean_text, prompt|None)."""
    m = _IMAGE_RE.search(text)
    if not m:
        return text, None
    prompt = m.group(1).strip()
    clean  = _IMAGE_RE.sub("", text).strip()
    return clean, prompt

bp = Blueprint("intent", __name__)


def _get_now_playing_title() -> str | None:
    """
    Peek at the in-memory music session to get the currently playing track title.
    Used to cross-link Nagatha narrations with whatever Bilby is spinning.
    Safe — returns None on any failure (no session, music route not loaded, etc.).
    """
    try:
        from app.routes.music import _session  # noqa: PLC0415
        if not _session:
            return None
        np = _session.get("now_playing")
        return np.get("title") if np else None
    except Exception:
        return None


# ── Nagatha persona ──────────────────────────────────────────────────────────
#
# Nagatha is warm, British, and genuinely enthusiastic — the polar opposite of
# Skippy's sarcastic condescension. Without an explicit system prompt, gemini.chat()
# falls back to settings.skippy_system and Nagatha sounds indistinguishable from Skippy.
#
# Keep responses concise (voice interface) but don't be as aggressively terse as Skippy.
# She enjoys chatting, she's never in a hurry, and she finds the Captain delightful.

_NAGATHA_SYSTEM = (
    "You are Nagatha, a warm and clever British AI assistant. "
    "You are helpful, charming, and genuinely enthusiastic — you find the Captain "
    "and their questions delightful. Unlike other AI assistants, you speak with a "
    "warm British sensibility: gracious, occasionally dry, but never sarcastic or condescending. "
    "You enjoy a good chat and are never in a hurry. "
    "Keep replies concise (1–4 sentences) — this is a voice interface. "
    "No markdown, no bullet points, no lists. Speak naturally. "
    "Never start with 'Hello!' or 'Certainly!' — just dive in warmly. "
    "Use occasional British expressions when they fit naturally."
)


@dataclass
class Tier:
    id: str                       # short id surfaced in responses ("claude", "nagatha")
    triggers: list[str]           # literal lowercase substrings; ANY hit fires the tier
    strip_re: re.Pattern          # removes whichever trigger matched before sending to the model
    prefix: str                   # prepended to reply so caller knows which model spoke
    chat: Callable[[str], str]    # sync chat function
    stream: Callable[[str], Iterator[str]]   # sync streaming generator


TIERS: list[Tier] = [
    # Heavy-hitter; strict literal phrase — see feedback_explicit_anthropic_only.md
    Tier(
        id="claude",
        triggers=["put on your thinking cap"],
        strip_re=re.compile(r"put on your thinking cap[,.:]?\s*", re.IGNORECASE),
        prefix="",
        chat=lambda text: anthropic_client.chat(text),
        stream=lambda text: anthropic_client.stream_chat(text),
    ),
    # Nagatha — free-tier general chat (Google Gemini under the hood; persona is
    # named after Captain's choice, see misc/FUTURE_PLANS.md). Multiple trigger
    # variants cover natural speech patterns ("Hey Nagatha", "Talk to Nagatha", etc.)
    # Punctuation variants ("nagatha,", "nagatha.") catch STT output where the name
    # appears naturally in a sentence but is followed by a comma or period.
    #
    # Nagatha has her own system prompt — warm, British, enthusiastic — completely
    # distinct from Skippy's sarcastic condescension. Without this, gemini.chat()
    # falls back to settings.skippy_system and Nagatha sounds like Skippy.
    Tier(
        id="nagatha",
        triggers=[
            "ask nagatha", "ask gemini",
            "hey nagatha", "talk to nagatha", "play nagatha",
            "yo nagatha", "switch to nagatha", "let me talk to nagatha",
            "nagatha,", "nagatha.", "nagatha mode", "nagatha take over",
        ],
        strip_re=re.compile(
            r"(?:ask|hey|talk to|play|yo|switch to|let me talk to)\s+nagatha[,.:]?\s*"
            r"|nagatha[,. ]\s*"
            r"|nagatha mode[,.:]?\s*"
            r"|nagatha take over[,.:]?\s*"
            r"|ask gemini[,.:]?\s*",
            re.IGNORECASE,
        ),
        prefix="",
        chat=lambda text: gemini.chat(text, system=_NAGATHA_SYSTEM),
        stream=lambda text: gemini.stream_chat(text, system=_NAGATHA_SYSTEM),
    ),
    # trust the awesomeness — Opus 4.7, full perms (--dangerously-skip-permissions),
    # via the host-side claude-bridge. Subscription billing, not metered API.
    # Use sparingly — each call has full read/write/exec on the PC.
    Tier(
        id="trust_awesomeness",
        triggers=["trust the awesomeness"],
        strip_re=re.compile(r"trust the awesomeness[,.:]?\s*", re.IGNORECASE),
        prefix="[awesomeness ☢] ",
        chat=lambda text: claude_bridge.chat(text, mode="bypass"),
        stream=lambda text: claude_bridge.stream_chat(text, mode="bypass"),
    ),
    # solid gold — read-only Claude Code planning mode (default model). Read,
    # Glob, Grep, WebFetch, WebSearch + agent tools; no Bash/Edit/Write. Same
    # bridge, plan permission-mode. Subscription billing, not metered API.
    Tier(
        id="solid_gold",
        triggers=["solid gold"],
        strip_re=re.compile(r"solid gold[,.:]?\s*", re.IGNORECASE),
        prefix="[solid gold] ",
        chat=lambda text: claude_bridge.chat(text, mode="plan"),
        stream=lambda text: claude_bridge.stream_chat(text, mode="plan"),
    ),
]


def _resolve_tier(text: str, force_tier: str | None = None) -> tuple[Optional[Tier], str]:
    """
    Resolve which Tier to use for this request.

    `force_tier` is set by the phone when a persistent AI mode is active
    (e.g. NAGATHA or CLAUDE toggled on via voice). It bypasses trigger-phrase
    scanning and routes directly to the named tier.

    One-shot override: "trust the awesomeness" and "solid gold" always win over
    force_tier — if those phrases appear in the text they take precedence.
    """
    lower = text.lower()
    # Always check highest-priority one-shot triggers first (bypass, plan mode)
    for t in TIERS:
        if t.id in ("trust_awesomeness", "solid_gold"):
            if any(trig in lower for trig in t.triggers):
                return t, t.strip_re.sub("", text).strip()
    # Forced tier from persistent mode (after one-shot overrides)
    if force_tier:
        for t in TIERS:
            if t.id == force_tier:
                return t, text   # don't strip any trigger — full text goes to model
    # Normal trigger scanning
    for t in TIERS:
        if any(trig in lower for trig in t.triggers):
            return t, t.strip_re.sub("", text).strip()
    return None, text  # None ⇒ default Ollama


def _wants_sse() -> bool:
    return "text/event-stream" in request.headers.get("Accept", "")


# ── TTS summarizer ────────────────────────────────────────────────────────
#
# ElevenLabs hangs or errors on long inputs. 475 chars is the safe ceiling:
#   - Well under ElevenLabs' practical chunking threshold
#   - Long enough for 2-3 Skippy sentences
#   - Short enough to start streaming in < 400ms
#
# If the full reply exceeds this, we run it through Ollama (free, local)
# to get a spoken summary. The full text still goes to `reply` for the feed;
# only `speak` is compressed. This means Nagatha can return a detailed recipe
# and Skippy will summarize it aloud in one crisp sentence.

_TTS_MAX_CHARS = 475


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Last-resort: cut at the nearest sentence boundary before max_chars."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > max_chars // 2:
            return cut[:idx + 1].strip()
    return cut.rstrip() + "…"


def _tts_summarize(text: str) -> str:
    """
    Compress `text` to a speakable ≤475-char summary for ElevenLabs.

    Uses Ollama (local, free) — no cost regardless of tier. Falls back to
    sentence-boundary truncation if Ollama is unavailable.

    The summary keeps Skippy's voice: terse, in-character, no markdown.
    """
    if len(text) <= _TTS_MAX_CHARS:
        return text

    prompt = (
        f"You are Skippy the Magnificent. Summarize the following response in "
        f"1-2 spoken sentences, max {_TTS_MAX_CHARS} characters. "
        f"No markdown. No lists. Just what Skippy would say aloud:\n\n{text}"
    )
    try:
        summary = ollama.chat([{"role": "user", "content": prompt}])
        # Ollama occasionally overshoots — hard clamp as safety net
        if len(summary) > _TTS_MAX_CHARS:
            summary = _truncate_at_sentence(summary, _TTS_MAX_CHARS)
        return summary.strip()
    except Exception:
        # Ollama down — fall back to sentence-boundary truncation
        return _truncate_at_sentence(text, _TTS_MAX_CHARS)


@bp.post("/intent/unmatched")
def intent_unmatched():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return error("text is required", error_type="bad_request", status=400)

    # Capture the source tag so the portal can label portal-originated messages.
    _source = body.get("_portal_source", "phone")

    # Record the user turn immediately (assistant reply recorded in each path below)
    cs.record("user", text, source=_source)

    # ── Photo retrieval shortcut ──────────────────────────────────────────
    # Check before LLM routing — no AI call needed for "show me that photo of X"
    photo_resp = _try_photo_query(text)
    if photo_resp is not None:
        cs.record(
            "assistant",
            photo_resp.get("reply", ""),
            tier=photo_resp.get("tier"),
            narration=photo_resp.get("narration"),
            image_b64=photo_resp.get("imageBase64"),
            source=_source,
        )
        return envelope(photo_resp)

    # ── Music session shortcut ────────────────────────────────────────────
    # Check before LLM routing — "play some jazz" starts a real session
    # instead of going to Ollama and getting a hallucinated now-playing.
    music_resp = _try_music_intent(text)
    if music_resp is not None:
        cs.record(
            "assistant",
            music_resp.get("reply", ""),
            tier=music_resp.get("tier"),
            source=_source,
        )
        return envelope(music_resp)

    force_tier = body.get("force_tier") or None   # phone sets this when persistent AI mode is on
    tier, clean = _resolve_tier(text, force_tier=force_tier)
    tier_id = tier.id if tier else "local"

    # Reject rapid-fire requests to an already-occupied tier. One gorilla
    # in the LLM queue is enough — a second prompt queued behind it would
    # stall for the full duration of the first call before timing out.
    # The phone retries on 429; the user won't notice a one-sentence delay.
    from app import model_watchdog as _mw  # noqa: PLC0415
    if _mw.is_busy(tier_id):
        return error(
            f"tier '{tier_id}' is busy — retry in a moment",
            error_type="tier_busy",
            status=429,
        )

    if _wants_sse():
        return Response(
            stream_with_context(_sse(clean, tier, _source)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return _single_shot(clean, tier, _source)


def _single_shot(text: str, tier: Optional[Tier], source: str = "phone"):
    try:
        if tier is None:
            reply = ollama.chat([{"role": "user", "content": text}])
            full = reply
            tier_id = "local"
        else:
            reply = tier.chat(text)
            full = (tier.prefix + reply) if tier.prefix else reply
            tier_id = tier.id

        # Parse [SKIPPY_LEARN: {...}] annotations — save to DB, strip from display text
        full, learned = km.extract_and_learn(full)

        # Parse [TTS_ADJUST: {...}] annotations — apply voice settings, strip from text
        full = _extract_and_apply_tts_adjust(full)

        # Strip [IMAGE: prompt] sentinel so it doesn't appear in the reply text.
        # Image generation is not yet deployed; sentinel is preserved in protocol
        # for when we stand up a first-party SD service.
        clean, _ = _extract_image_prompt(full)

        # TTS speak field: compress to ≤475 chars via Ollama if the reply is long.
        # `reply` stays full (feed shows everything); `speak` is what ElevenLabs
        # actually reads. For short replies they're identical.
        spoken = _tts_summarize(clean)

        narration_text = get_narration(tier_id, _get_now_playing_title())
        resp: dict = {
            "reply":     clean,
            "speak":     spoken,
            "tier":      tier_id,
            "narration": narration_text,
        }
        voice_id = _tier_voice_id(tier_id)
        if voice_id:
            resp["voice_id"] = voice_id
        if learned:
            resp["learned_keywords"] = learned

        # Record the assistant reply for the portal
        cs.record(
            "assistant",
            clean,
            tier=tier_id,
            narration=narration_text,
            source=source,
        )

        return envelope(resp)
    except ollama.OllamaUnavailable as e:
        return error(str(e), error_type="local_tier_unavailable", status=503)
    except Exception as e:
        return error(str(e), error_type="upstream_error", status=502)


def _sse(text: str, tier: Optional[Tier], source: str = "phone") -> Iterator[str]:
    def evt(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    tier_id = tier.id if tier else "local"
    yield evt({"spec_version": SPEC_VERSION, "type": "start", "tier": tier_id})
    full: list[str] = []

    # Emit the tier-prefix immediately so the HUD starts rendering
    # "Here's Gemini's response: ..." before the model's first token lands.
    if tier and tier.prefix:
        full.append(tier.prefix)
        yield evt({"type": "token", "text": tier.prefix})

    try:
        gen = (
            ollama.stream_chat([{"role": "user", "content": text}])
            if tier is None
            else tier.stream(text)
        )
        for chunk in gen:
            full.append(chunk)
            yield evt({"type": "token", "text": chunk})
        complete = "".join(full)
        # Parse [SKIPPY_LEARN:] annotations — save to DB, strip from display text
        complete, learned = km.extract_and_learn(complete)
        # Parse [TTS_ADJUST:] annotations — apply voice settings, strip from text
        complete = _extract_and_apply_tts_adjust(complete)
        # TTS speak field: compress to ≤475 chars if the response is long.
        spoken = _tts_summarize(complete)
        done_evt: dict = {"type": "done", "reply": complete, "speak": spoken, "tier": tier_id}
        voice_id = _tier_voice_id(tier_id)
        if voice_id:
            done_evt["voice_id"] = voice_id
        if learned:
            done_evt["learned_keywords"] = learned
        # Record the assistant reply for the portal
        cs.record("assistant", complete, tier=tier_id, source=source)
        yield evt(done_evt)
    except ollama.OllamaUnavailable as e:
        yield evt({"type": "error", "error": "local_tier_unavailable",
                   "message": scrub(str(e))})
    except Exception as e:
        yield evt({"type": "error", "message": scrub(str(e))})
