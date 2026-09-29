"""tts_settings.py — Persistent TTS voice settings store.

Skippy can adjust his own voice during conversation by emitting a
[TTS_ADJUST: {...}] annotation. Settings are stored in /app/state/
and survive container restarts. The LLM is the only one who touches
this in normal use; the Captain can override via:

    GET  /tts/settings         → see current values
    PATCH /tts/settings        → override any key directly
    POST /tts/settings/reset   → back to defaults

Keys map directly to ElevenLabs voice_settings:
    stability      float 0–1   (v3: 0=creative, 1=monotone)
    speed          float 0.7–1.2

Only `stability` is exposed in the v3 web UI. Other keys are kept
here for future flexibility but currently omitted from API calls
since v3 handles them internally.
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_PATH = Path("/app/state/tts_settings.json")

# Defaults match what the web UI uses (full Creative, neutral speed)
DEFAULTS: dict = {
    "stability": 0.05,
}

# Allowable range clamps — prevents the LLM from doing something silly
_CLAMPS: dict[str, tuple] = {
    "stability": (0.0, 1.0),
    "speed":     (0.7, 1.2),
}


def load() -> dict:
    """Return current settings (file overrides defaults; file may be partial)."""
    base = dict(DEFAULTS)
    try:
        if _PATH.exists():
            stored = json.loads(_PATH.read_text())
            base.update({k: v for k, v in stored.items() if k in _CLAMPS})
    except Exception as exc:
        log.warning("tts_settings: could not read %s: %s", _PATH, exc)
    return base


def update(partial: dict) -> dict:
    """
    Merge `partial` into current settings, clamp values, persist, return result.
    Unknown keys are silently ignored.
    """
    current = load()
    changed = []
    for key, val in partial.items():
        if key not in _CLAMPS:
            continue
        lo, hi = _CLAMPS[key]
        clamped = max(lo, min(hi, float(val)))
        current[key] = clamped
        changed.append(f"{key}={clamped:.3f}")
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(current, indent=2))
        if changed:
            log.info("tts_settings updated: %s", ", ".join(changed))
    except Exception as exc:
        log.warning("tts_settings: could not write %s: %s", _PATH, exc)
    return current


def reset() -> dict:
    """Wipe stored settings, restore defaults."""
    try:
        _PATH.unlink(missing_ok=True)
        log.info("tts_settings: reset to defaults")
    except Exception as exc:
        log.warning("tts_settings: reset failed: %s", exc)
    return dict(DEFAULTS)
