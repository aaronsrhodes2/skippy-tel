"""Model Watchdog — GC-pattern in-flight prompt tracker.

Each LLM dispatch calls record(tier, prompt, model) immediately before
sending the request. On successful response it calls clear(tier).

Any record that survives past HANG_THRESHOLD_SECS was never cleared —
the model hung, timed out, or crashed mid-response. The health endpoint
calls check_and_reroll() which:

  1. Detects hung records (records older than HANG_THRESHOLD_SECS).
  2. Logs prompt content/size/encoding to /drive-state/model_health.log.
  3. For Ollama: docker-restarts the container (with a 120s cooldown).
  4. Clears the stuck record so the next request gets a clean slate.

Saved prompts are the raw user text (UTF-8 "text" encoding) or a hex
preview if the content is non-UTF-8 ("binary" encoding). The saved prompt
enables writing a regression test for any prompt that kills a model.

Tier IDs match the naming convention in intent.py:
  "local"   → Ollama (qwen2.5:7b inside Docker)
  "nagatha" → Gemini
"""
import subprocess
import threading
import time
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

HANG_THRESHOLD_SECS  = 90    # record alive this long → assumed hung
RESTART_COOLDOWN_SECS = 120  # minimum gap between restarts of the same tier
LOG_PATH = Path("/drive-state/model_health.log")

# ── State ──────────────────────────────────────────────────────────────────────

_lock = threading.Lock()

# { tier: {"prompt": str, "model": str, "started": float} }
_in_flight: dict[str, dict] = {}

# { tier: float } — timestamp of last restart attempt
_last_restart: dict[str, float] = {}


# ── Public API ────────────────────────────────────────────────────────────────

def record(tier: str, prompt: str, model: str) -> None:
    """Allocate an in-flight record before dispatching to tier.

    Like Java's object allocation before GC tracking — the record exists
    until clear() frees it on successful response.  If it's still alive
    when health check runs, it's hung.
    """
    with _lock:
        _in_flight[tier] = {
            "prompt":  prompt,
            "model":   model,
            "started": time.time(),
        }


def clear(tier: str) -> None:
    """Free the in-flight record for tier after a successful response.

    Like a GC free() — called only when the model responded cleanly.
    NOT called on connection errors or timeouts; those keep the record
    alive so the health check can detect and reroll them.
    """
    with _lock:
        _in_flight.pop(tier, None)


def is_busy(tier: str) -> bool:
    """True if tier has any in-flight request right now (hung or still processing)."""
    with _lock:
        return tier in _in_flight


def get_hung(threshold_secs: float = HANG_THRESHOLD_SECS) -> dict[str, dict]:
    """Return tiers whose in-flight records have lived longer than threshold."""
    now = time.time()
    with _lock:
        return {
            tier: rec
            for tier, rec in _in_flight.items()
            if now - rec["started"] > threshold_secs
        }


def check_and_reroll() -> list[dict]:
    """Scan for hung tiers, restart where possible, return event list.

    Called from GET /health. Safe to call frequently — cooldown guard
    prevents rapid restart loops. Returns:
      [{"tier": "local", "model": "qwen2.5:7b",
        "duration_secs": 95.2, "restarted": True}, ...]
    """
    hung = get_hung()
    events = []
    for tier, rec in hung.items():
        duration = time.time() - rec["started"]
        restarted = _maybe_restart(tier, rec)
        events.append({
            "tier":          tier,
            "model":         rec["model"],
            "duration_secs": round(duration, 1),
            "restarted":     restarted,
        })
    return events


# ── Internal helpers ──────────────────────────────────────────────────────────

def _log_hang(tier: str, rec: dict) -> None:
    """Append a hang event to the persistent model health log.

    Captures: tier, model, duration, prompt byte-count, encoding (text/binary),
    and a 200-char preview of the prompt.  Newlines in the preview are escaped
    so each log entry fits on two lines and is grep-friendly.
    """
    duration = time.time() - rec["started"]
    raw_prompt = rec.get("prompt", "")

    try:
        prompt_bytes = raw_prompt.encode("utf-8")
        encoding = "text"
        preview = raw_prompt[:200].replace("\n", "\\n")
    except Exception:
        prompt_bytes = b""
        encoding = "binary"
        preview = repr(raw_prompt[:200])

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(
            f"[HANG] tier={tier!r} model={rec['model']!r} "
            f"duration={duration:.1f}s "
            f"prompt_bytes={len(prompt_bytes)} encoding={encoding}\n"
            f"  preview: {preview}\n"
        )


def _maybe_restart(tier: str, rec: dict) -> bool:
    """Restart the stuck tier if the cooldown allows.

    Returns True if a restart was attempted (or, for remote tiers, if the
    record was cleared).  Returns False if still in cooldown.
    """
    now = time.time()
    with _lock:
        last = _last_restart.get(tier, 0.0)
        if now - last < RESTART_COOLDOWN_SECS:
            return False
        _last_restart[tier] = now
        _in_flight.pop(tier, None)   # clear the hung record

    _log_hang(tier, rec)

    if tier == "local":
        # Ollama runs in Docker alongside this container.
        # `docker restart ollama` sends SIGTERM then SIGKILL, then starts fresh.
        try:
            subprocess.run(
                ["docker", "restart", "ollama"],
                timeout=30,
                capture_output=True,
                check=False,
            )
            _append_log(f"[RESTARTED] tier=local model={rec['model']!r}\n")
        except Exception as exc:
            _append_log(f"[RESTART-FAIL] tier=local error={exc!r}\n")
    else:
        # Remote tiers (Anthropic, Gemini) — can't restart; just clear + log.
        _append_log(
            f"[CLEARED] tier={tier!r} model={rec['model']!r} "
            f"— remote service, no restart\n"
        )

    return True


def _append_log(line: str) -> None:
    """Append a single line to the model health log (no HANG prefix)."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
