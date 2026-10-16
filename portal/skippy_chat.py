"""Skippy Chat: the Captain's development chat, slice 1 (the plan: /mnt/project-files/skippy-chat/plan.md).

Who may use it is decided here and nowhere else:
  • ONE identity: the Captain's verified Google email, pinned in this file. Not ADMIN_EMAILS (a list that can grow), not a
    friend's grant, not a share link. Nothing in deploy config can widen it, and an unset or unknown identity is refused.
  • The model is chosen on the server from MODELS below, never trusted from the page. A key that is not listed is refused.

Only what runs today is marked `runs`. Opus and SkippyCore are listed so the picker can say why they are not available
yet; choosing one is refused, never silently answered by a different model.
"""
from __future__ import annotations

CAPTAIN_EMAIL = "aaron.s.rhodes@gmail.com"

# key -> what the picker shows. `runs` is True only where a real backend answers the message today:
#   sonnet: the claude bridge on skippy-pc (workshop_engine.MODEL is claude-sonnet-5-5, fixed there on purpose).
MODELS: dict[str, dict] = {
    "sonnet": {"label": "Sonnet", "runs": True},
    "opus": {"label": "Opus", "runs": False, "reason": "Opus isn't wired to the bridge yet."},
    "skippycore": {"label": "SkippyCore", "runs": False,
                   "reason": "SkippyCore's local model box isn't built yet."},
}
DEFAULT_MODEL = "sonnet"


def is_captain(email, verified: bool = True) -> bool:
    """True only for the Captain's address, Google-verified. Anything else, including None or a non-string, is False."""
    if not verified or not isinstance(email, str):
        return False
    return email.strip().lower() == CAPTAIN_EMAIL


def options() -> list[dict]:
    """The picker: one row per model, in MODELS order, with whether it can answer now."""
    return [{"key": key, "label": spec["label"], "runs": spec["runs"], "reason": spec.get("reason", ""),
             "default": key == DEFAULT_MODEL} for key, spec in MODELS.items()]


def choose(key) -> tuple[str | None, str]:
    """(model key, '') when the server can answer with it, else (None, why). An unknown key is refused like an unavailable one."""
    if not isinstance(key, str) or key not in MODELS:
        return None, "Unknown model."
    spec = MODELS[key]
    if not spec["runs"]:
        return None, spec.get("reason", "That model isn't available.")
    return key, ""
