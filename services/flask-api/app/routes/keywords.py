"""
SkippyTel — Keyword Memory API

Persistent keyword/intent store shared across all AI tiers and the Captain's phone.
All personalities get all keywords. The keyword set IS the Skippy brain — it survives
model swaps, app reinstalls, and the Captain getting a new phone.

Endpoints:

  GET /keywords
      Full keyword list (hardcoded + learned), sorted hardcoded-first.
      Query params:
        ?source=hardcoded|learned   — filter by source
        ?intent=<intent_id>         — filter by intent

  GET /keywords/sync
      Phone-ready sync payload.
      Returns:
        triggers       — learned keywords only, phrase→intent (load into dynamicTriggers)
        biasing_phrases — ALL phrases (hardcoded+learned), for BIASING_STRINGS + Whisper

  GET /keywords/whisper-primer
      Plain text, comma-separated list of ALL phrases.
      Passed directly to faster-whisper's initial_prompt parameter.

  POST /keywords/learn
      Body: { "phrase": str, "intent": str, "description": str, "note"?: str }
      Add a keyword manually. Returns 200 if updated, 201 if inserted.

  DELETE /keywords/<phrase>
      Forget a learned keyword. Refuses to delete hardcoded keywords.
"""

from flask import Blueprint, jsonify, request

from app import keyword_memory as km

bp = Blueprint("keywords", __name__)


@bp.get("/keywords")
def get_keywords():
    """All keywords. Optional ?source= and ?intent= filters."""
    source_filter = request.args.get("source")
    intent_filter = request.args.get("intent")

    keywords = km.get_all()

    if source_filter:
        keywords = [k for k in keywords if k["source"] == source_filter]
    if intent_filter:
        keywords = [k for k in keywords if k["intent"] == intent_filter]

    return jsonify({
        "spec_version": "1",
        "total":    len(keywords),
        "keywords": keywords,
    })


@bp.get("/keywords/sync")
def keywords_sync():
    """
    Phone-ready sync payload.

    triggers       — learned keywords only, phrase → intent.
                     Load these into KeywordScanner.dynamicTriggers alongside
                     service-level voice triggers.

    biasing_phrases — ALL keywords (hardcoded + learned), as a flat list of
                      phrases. Feed to Android BIASING_STRINGS extra and to
                      the Whisper initial_prompt.

    learned_count  — number of learned (non-hardcoded) keywords.
    """
    all_kw   = km.get_all()
    learned  = [k for k in all_kw if k["source"] != "hardcoded"]

    triggers = {k["phrase"]: k["intent"] for k in learned}
    biasing  = [k["phrase"] for k in all_kw]

    return jsonify({
        "spec_version":   "1",
        "triggers":       triggers,
        "biasing_phrases": biasing,
        "learned_count":  len(learned),
    })


@bp.get("/keywords/whisper-primer")
def keywords_whisper_primer():
    """
    Plain-text comma-separated list of all keyword phrases.
    Passed directly to faster-whisper's initial_prompt parameter.
    Biases the Whisper decoder toward the Captain's known vocabulary.
    """
    all_kw  = km.get_all()
    phrases = [k["phrase"] for k in all_kw]
    primer  = ", ".join(phrases)
    return primer, 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.post("/keywords/learn")
def learn_keyword():
    """
    Add or update a learned keyword.

    Body (JSON):
      phrase      str  required — the voice trigger phrase
      intent      str  required — what it maps to (e.g. "ups_status")
      description str  required — human-readable one-liner
      note        str  optional — Skippy-voice personality note

    Returns 201 on insert, 200 on update.
    """
    body = request.get_json(silent=True) or {}
    phrase      = str(body.get("phrase", "")).strip().lower()
    intent      = str(body.get("intent", "")).strip()
    description = str(body.get("description", "")).strip()
    note        = body.get("note") or body.get("personality_note")

    if not phrase or not intent or not description:
        return jsonify({"error": "phrase, intent, and description are required"}), 400

    inserted = km.learn(phrase, intent, description, personality_note=note, source="learned")
    status   = 201 if inserted else 200
    return jsonify({
        "phrase":    phrase,
        "intent":    intent,
        "inserted":  inserted,
    }), status


@bp.get("/keywords/aliases")
def get_aliases():
    """
    All nickname/alias entries, ordered by popularity (most-used first).
    Optional ?canonical=<name> to filter by what they point to.
    """
    canonical_filter = request.args.get("canonical")
    aliases = km.get_aliases(canonical=canonical_filter)
    return jsonify({
        "spec_version": "1",
        "total":   len(aliases),
        "aliases": aliases,
    })


@bp.post("/keywords/alias")
def learn_alias():
    """
    Manually record a nickname or alias.

    Body: { "alias": str, "canonical": str, "description": str, "note"?: str }
    Returns 201 on insert, 200 if already exists (uses incremented).
    """
    body = request.get_json(silent=True) or {}
    alias_phrase = str(body.get("alias", "")).strip().lower()
    canonical    = str(body.get("canonical", "")).strip().lower()
    description  = str(body.get("description", "")).strip()
    note         = body.get("note") or body.get("personality_note")

    if not alias_phrase or not canonical:
        return jsonify({"error": "alias and canonical are required"}), 400

    inserted = km.alias(alias_phrase, canonical, description=description, personality_note=note)
    return jsonify({
        "alias":     alias_phrase,
        "canonical": canonical,
        "inserted":  inserted,
    }), 201 if inserted else 200


@bp.delete("/keywords/<path:phrase>")
def forget_keyword(phrase: str):
    """
    Forget a learned keyword. Will not delete hardcoded phone commands.
    phrase is URL-encoded in the path, e.g. DELETE /keywords/code%20red
    """
    phrase = phrase.lower().strip()
    ok = km.forget(phrase)
    if ok:
        return jsonify({"forgotten": phrase}), 200
    # Either not found or is a hardcoded keyword
    all_phrases = {k["phrase"] for k in km.get_all()}
    if phrase in all_phrases:
        return jsonify({
            "error":   "cannot delete hardcoded keyword",
            "phrase":  phrase,
            "hint":    "Hardcoded keywords are baked into the phone app. Only learned keywords can be forgotten.",
        }), 403
    return jsonify({"error": "keyword not found", "phrase": phrase}), 404
