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
from app.version import envelope, error, scrub, SPEC_VERSION

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
                return resp
        return {"reply": "I couldn't find any recent photos in DropShip.", "speak": "Couldn't find any recent photos.", "tier": "dropship"}
    except Exception as exc:
        return {"reply": f"DropShip error: {exc}", "speak": "DropShip had an error.", "tier": "dropship"}


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
                return resp
        return {"reply": f"No photos matched '{query}'.", "speak": f"No photos found for {query}.", "tier": "dropship"}
    except Exception as exc:
        return {"reply": f"DropShip error: {exc}", "speak": "DropShip had an error.", "tier": "dropship"}


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
    # named after Captain's choice, see misc/FUTURE_PLANS.md). "ask gemini" stays
    # as a transition alias and may be removed later.
    Tier(
        id="nagatha",
        triggers=["ask nagatha", "ask gemini"],
        strip_re=re.compile(r"ask (?:nagatha|gemini)[,.:]?\s*", re.IGNORECASE),
        prefix="Here's Nagatha's response: ",
        chat=lambda text: gemini.chat(text),
        stream=lambda text: gemini.stream_chat(text),
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


def _resolve_tier(text: str) -> tuple[Optional[Tier], str]:
    lower = text.lower()
    for t in TIERS:
        if any(trig in lower for trig in t.triggers):
            return t, t.strip_re.sub("", text).strip()
    return None, text  # None ⇒ default Ollama


def _wants_sse() -> bool:
    return "text/event-stream" in request.headers.get("Accept", "")


@bp.post("/intent/unmatched")
def intent_unmatched():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return error("text is required", error_type="bad_request", status=400)

    # ── Photo retrieval shortcut ──────────────────────────────────────────
    # Check before LLM routing — no AI call needed for "show me that photo of X"
    photo_resp = _try_photo_query(text)
    if photo_resp is not None:
        return envelope(photo_resp)

    tier, clean = _resolve_tier(text)

    if _wants_sse():
        return Response(
            stream_with_context(_sse(clean, tier)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return _single_shot(clean, tier)


def _single_shot(text: str, tier: Optional[Tier]):
    try:
        if tier is None:
            reply = ollama.chat([{"role": "user", "content": text}])
            full = reply
            tier_id = "local"
        else:
            reply = tier.chat(text)
            full = (tier.prefix + reply) if tier.prefix else reply
            tier_id = tier.id

        # Strip [IMAGE: prompt] sentinel so it doesn't appear in the reply text.
        # Image generation is not yet deployed; sentinel is preserved in protocol
        # for when we stand up a first-party SD service.
        clean, _ = _extract_image_prompt(full)
        resp: dict = {"reply": clean, "speak": clean, "tier": tier_id}
        return envelope(resp)
    except ollama.OllamaUnavailable as e:
        return error(str(e), error_type="local_tier_unavailable", status=503)
    except Exception as e:
        return error(str(e), error_type="upstream_error", status=502)


def _sse(text: str, tier: Optional[Tier]) -> Iterator[str]:
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
        yield evt({"type": "done", "reply": "".join(full), "tier": tier_id})
    except ollama.OllamaUnavailable as e:
        yield evt({"type": "error", "error": "local_tier_unavailable",
                   "message": scrub(str(e))})
    except Exception as e:
        yield evt({"type": "error", "message": scrub(str(e))})
