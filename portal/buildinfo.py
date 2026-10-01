"""Which build is this portal? The answer behind GET /version and the small stamp in the footer.

The Captain times the self-deploy loop ("prompt sent" to "new deployment visible", tools/loop_timer.py) and needs a
way to SEE which build he is looking at. The image carries two environment variables baked in at build time (so a
revision that gets rolled back still reports its own build, not the one that replaced it): GIT_SHA (the full commit)
and BUILD_TIME (ISO 8601, UTC). Cloud Run itself sets K_REVISION. Outside the image none of them exist: "dev".

Pure on purpose (the environment and the clock are arguments), so it is tested without a server.

Nothing here can leak a secret, and nothing odd can reach a page: every value is checked against the shape it must
have and replaced by its "unknown" form otherwise, so a stray build argument (say GIT_SHA=unknown, or a typo'd
BUILD_TIME) shows as "dev" / "" instead of garbage in a footer or in the loop timer's comparison.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

_SHA = re.compile(r"[0-9a-f]{7,64}")                     # git's own abbreviations and full ids, lower case
_REVISION = re.compile(r"[a-z][a-z0-9-]{0,62}")          # Cloud Run revision names: skippy-portal-00042-abc


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# This process's start: gunicorn imports the app once (one worker), so import time IS the start of the process, and
# a new Cloud Run revision shows a new value here even when the commit is the same (a redeploy).
STARTED_AT = _iso_utc(datetime.now(timezone.utc))


def _built_at(raw: str) -> str:
    """BUILD_TIME as `2026-09-30T19:03:12Z`, or "" when it is missing or not a time zone-aware ISO 8601 stamp."""
    try:
        moment = datetime.fromisoformat(raw.strip())      # 3.11+ reads the trailing Z
    except ValueError:
        return ""
    if moment.tzinfo is None:                              # "19:03:12" with no zone: is that UTC or local? Don't guess.
        return ""
    return _iso_utc(moment)


def info(environ=None, started_at: str | None = None) -> dict:
    """The /version payload: `{commit, short, built_at, revision, started_at}` (the contract tools/loop_timer.py reads)."""
    env = os.environ if environ is None else environ
    sha = (env.get("GIT_SHA") or "").strip().lower()
    commit = sha if _SHA.fullmatch(sha) else "dev"
    revision = (env.get("K_REVISION") or "").strip()
    return {
        "commit": commit,
        "short": commit[:7] if commit != "dev" else "dev",
        "built_at": _built_at(env.get("BUILD_TIME") or ""),
        "revision": revision if _REVISION.fullmatch(revision) else "",
        "started_at": STARTED_AT if started_at is None else started_at,
    }


def label(built_at: str) -> str:
    """The build time as plain UTC text (`2026-09-30 19:03 UTC`): what the footer shows before (or without) the script
    that swaps it for the viewer's local time."""
    return f"{built_at[:10]} {built_at[11:16]} UTC" if built_at else ""


def tooltip(build: dict) -> str:
    """The footer stamp's hover text: the full commit and the Cloud Run revision (the short form is all that fits on a line)."""
    parts = []
    if build["commit"] != "dev":
        parts.append(f"commit {build['commit']}")
    if build["revision"]:
        parts.append(f"revision {build['revision']}")
    if build["built_at"]:
        parts.append(f"built {build['built_at']}")
    return " · ".join(parts) or "a local build (no GIT_SHA: not built by the deploy pipeline)"
