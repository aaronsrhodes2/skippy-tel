"""All portal settings, from environment variables. See .env.example.

No secrets have defaults — the app refuses to start without them, rather
than silently running wide open (no allowlist) or unauthenticated.
"""
from __future__ import annotations

import os


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set — see portal/.env.example")
    return value


def _split_emails(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


class Config:
    # Flask session signing — rotate this to instantly log everyone out.
    SECRET_KEY = _required("FLASK_SECRET_KEY")

    # "Sign in with Google" OAuth client (Google Cloud Console → APIs &
    # Services → Credentials → OAuth client ID → Web application →
    # redirect URI https://<portal-host>/auth/callback). Separate from any
    # gcloud/Terraform credentials — this one only ever sees an email.
    GOOGLE_CLIENT_ID = _required("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = _required("GOOGLE_CLIENT_SECRET")

    # Who's allowed in. Comma-separated emails, lowercase-insensitive.
    ALLOWED_EMAILS = _split_emails(_required("ALLOWED_EMAILS"))

    # The games VM this portal controls.
    GCP_PROJECT = _required("GCP_PROJECT")
    GCP_ZONE = os.environ.get("GCP_ZONE", "us-west1-b")
    GCP_INSTANCE = os.environ.get("GCP_INSTANCE", "skippy-games")

    # AI assistant tile — v1 deep-links to Claude rather than hosting a
    # custom chat. Point it at a specific Project once one exists (see
    # docs/memory/README.md's "Skippy" Project instructions).
    AI_ASSISTANT_URL = os.environ.get("AI_ASSISTANT_URL", "https://claude.ai/new")
