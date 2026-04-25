"""Spec version + response envelope helpers.

Mirror of the version-field doctrine from the SkippyDroid passthrough
protocol (PASSTHROUGH_PROTOCOL.md §15) — every JSON response from
SkippyTel carries `spec_version: "1"`. Phone may refuse mismatches.
"""
import re

from flask import jsonify

SPEC_VERSION = "1"

# Strip URLs and `key=`/`api_key=` query params from any string that
# might end up in a response envelope. Upstream HTTP clients (httpx,
# anthropic SDK, etc.) often include the full request URL in their
# exception messages — and that URL can carry the API key as a query
# parameter (Google's Gemini REST API does this). Scrub before sending.
_URL_RE = re.compile(r"https?://\S+")
_KEY_RE = re.compile(r"(?i)(?:api[_-]?key|key)\s*[=:]\s*\S+")


def scrub(s: str) -> str:
    if not s:
        return s
    s = _URL_RE.sub("<url-redacted>", s)
    s = _KEY_RE.sub("<key-redacted>", s)
    return s


def envelope(payload: dict, status: int = 200):
    body = {"spec_version": SPEC_VERSION, **payload}
    return jsonify(body), status


def error(message: str, error_type: str = "error", status: int = 400):
    return envelope(
        {"error": error_type, "message": scrub(message)},
        status=status,
    )
