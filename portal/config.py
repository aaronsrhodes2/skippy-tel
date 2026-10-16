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
        # Service accounts allowed to read /ops/home (scripts/remote.py home).
        "OPS_EMAILS": _emails(os.environ.get("OPS_EMAILS", "skippy-deployer@skippy-tel.iam.gserviceaccount.com")),

        # The games VM.
        "GCP_PROJECT": _required("GCP_PROJECT"),
        "GCP_ZONE": _optional("GCP_ZONE", "us-west1-b"),
        "GCP_INSTANCE": _optional("GCP_INSTANCE", "skippy-games"),

        # Skippy chat (ANTHROPIC_API_KEY is read by the SDK itself).
        "CHAT_MODEL": _optional("CHAT_MODEL", "claude-haiku-4-5"),
        "BARNEY_MODEL": _optional("BARNEY_MODEL", "claude-haiku-4-5"),   # Barney Style (the plain-words explainer): its own model setting
        "CHAT_ENABLED": bool(_optional("ANTHROPIC_API_KEY")),

        # Discord: the invite link shown to players, and the bot token the
        # admin page uses to create channels (optional until you want that).
        "DISCORD_INVITE": _optional("DISCORD_INVITE", "gPTVS7Hmj"),
        "PORTAL_URL": _optional("PORTAL_URL", ""),   # the address the Discord pins link to (a custom domain, once there is one); else where the portal is reached
        "MENTAT_URL": _optional("MENTAT_URL", ""),
        "DISCORD_BOT_TOKEN": _optional("DISCORD_BOT_TOKEN"),
        # The Admin page's deploy buttons (recovery.py): a fine-grained token, this repo only, Actions read/write. Optional:
        # without it the Deploy pipeline card says "not configured". create_app takes it OUT of app.config at once, so no
        # template (Flask hands them `config`) can ever print it.
        "GITHUB_ACTIONS_TOKEN": _optional("GITHUB_ACTIONS_TOKEN"),

        # Usage recording for signed-in people (telemetry.py; /privacy says what). On unless switched off.
        "TELEMETRY_ENABLED": _optional("TELEMETRY_ENABLED", "true").lower() not in ("false", "0", "no", "off"),
    }
