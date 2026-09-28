"""Portal settings from environment variables (see .env.example).

On Cloud Run the secrets come from Secret Manager via `--set-secrets` and
the rest from `--set-env-vars`, both in .github/workflows/deploy.yml.
Secrets have no defaults: the app refuses to start without them rather than
running unauthenticated.
"""
from __future__ import annotations

import os

UNSET = "unset"   # placeholder bootstrap.sh stores for optional secrets you skipped


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value == UNSET:
        raise RuntimeError(f"{name} is not set — see portal/.env.example")
    return value


def _optional(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return default if value in ("", UNSET) else value


def _emails(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def load() -> dict:
    allowed = _emails(_required("ALLOWED_EMAILS"))
    return {
        # Flask session signing — rotating it signs everyone out.
        "SECRET_KEY": _required("FLASK_SECRET_KEY"),
        "SESSION_COOKIE_SECURE": _optional("SESSION_COOKIE_SECURE", "true") == "true",
        "SESSION_COOKIE_SAMESITE": "Lax",   # also what stops cross-site form posts
        "SESSION_COOKIE_HTTPONLY": True,

        # "Sign in with Google" OAuth client (web application).
        "GOOGLE_CLIENT_ID": _required("GOOGLE_CLIENT_ID"),
        "GOOGLE_CLIENT_SECRET": _required("GOOGLE_CLIENT_SECRET"),

        # Who may sign in, and the subset who may change settings/restart.
        "ALLOWED_EMAILS": allowed,
        "ADMIN_EMAILS": _emails(_optional("ADMIN_EMAILS")) & allowed,

        # The games VM.
        "GCP_PROJECT": _required("GCP_PROJECT"),
        "GCP_ZONE": _optional("GCP_ZONE", "us-west1-b"),
        "GCP_INSTANCE": _optional("GCP_INSTANCE", "skippy-games"),

        # Skippy chat (ANTHROPIC_API_KEY is read by the SDK itself).
        "CHAT_MODEL": _optional("CHAT_MODEL", "claude-opus-5"),
        "CHAT_ENABLED": bool(_optional("ANTHROPIC_API_KEY")),

        # Discord: the invite link shown to players, and the bot token the
        # admin page uses to create channels (optional until you want that).
        "DISCORD_INVITE": _optional("DISCORD_INVITE", "gPTVS7Hmj"),
        "DISCORD_BOT_TOKEN": _optional("DISCORD_BOT_TOKEN"),
    }
