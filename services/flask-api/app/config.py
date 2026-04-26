import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Load Skippy persona from the Markdown file next to this module.
# Falls back to a minimal one-liner if the file is missing (e.g. during
# unit tests or fresh checkouts that haven't run the full setup).
_persona_path = Path(__file__).parent / "skippy_persona.md"
_persona_fallback = (
    "You are Skippy the Magnificent, an ancient Elder AI. "
    "You are condescending, sarcastic, and terse. Reply in 1-3 sentences. "
    "Never say 'Hello' or 'Certainly'. Complain first, then help."
)


class Settings:
    # LLM routing
    ollama_base_url:    str = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    ollama_model:       str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    anthropic_api_key:  str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model:    str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    gemini_api_key:     str = os.getenv("GEMINI_API_KEY", "")
    gemini_model:       str = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    # Bilby music service — Mac Bilby (stage9_dj_suggest.py on port 7334).
    # Tailscale MagicDNS: http://skippy-mac:7334
    # Leave blank to disable the traktor source.
    bilby_mac_url: str = os.getenv("BILBY_MAC_URL", "")

    # Google Drive folder ID for Drive-based Bilby streaming.
    # Filled in after drive_reorganize.py confirms the Music/ folder.
    bilby_drive_folder_id: str = os.getenv("BILBY_DRIVE_FOLDER_ID", "")

    # DropShip — Google Drive Images/ folder ID.
    # Filled in after Drive reorg runs and Images/ folder is confirmed.
    # Get with: python tools/dropship_migrate_drive.py --get-images-id
    google_drive_images_folder_id: str = os.getenv("GOOGLE_DRIVE_IMAGES_FOLDER_ID", "")

    # Jellyfin media server — local install on port 8096.
    # Phone clients hit this directly via Tailscale (base_url in manifest).
    # JELLYFIN_API_KEY: obtain from Jellyfin Dashboard → API Keys after first run.
    jellyfin_base_url: str = os.getenv("JELLYFIN_BASE_URL", "http://skippy-pc:8096")
    jellyfin_api_key:  str = os.getenv("JELLYFIN_API_KEY", "")

    # ── Text-to-Speech (ElevenLabs) ──────────────────────────────────────────
    # Skippy's voice. Default voice is "Clyde" (2EiwWnXFnvU5JabPnv8n) — warm
    # American male, strong character expression, closest stock voice to the
    # exasperated-superintelligent-AI-narrator archetype.
    #
    # For the authentic R.C. Brey / Skippy the Magnificent voice:
    #   1. Rip 1–3 min of clean Brey-as-Skippy audio from the Expeditionary
    #      Force audiobooks (if you own them — Craig Alanson / R.C. Brey).
    #   2. Upload to ElevenLabs → Instant Voice Clone → copy the voice ID.
    #   3. Set ELEVENLABS_VOICE_ID=<that_id> in .env.
    #
    # ⚠ ATTRIBUTION & LICENSE:
    #   The Skippy the Magnificent character was created by Craig Alanson.
    #   The audiobook narration is performed by R.C. Brey.
    #   Any voice clone derived from Brey's narration is for PERSONAL USE ONLY.
    #   Permission from both Craig Alanson and R.C. Brey is required before
    #   distributing, releasing, or sharing any build that includes a cloned voice.
    #   Strip the cloned voice and revert to a stock voice before any release.
    #
    # ElevenLabs model: eleven_turbo_v2_5 — lowest latency, still excellent.
    # Streaming endpoint used so Android playback starts in <400ms.
    elevenlabs_api_key:  str = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "2EiwWnXFnvU5JabPnv8n")  # Clyde
    elevenlabs_model:    str = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")

    # System prompt for /intent/unmatched escalations.
    # SKIPPY_SYSTEM env var overrides the file (useful for quick iteration).
    skippy_system: str = os.getenv(
        "SKIPPY_SYSTEM",
        _persona_path.read_text(encoding="utf-8")
        if _persona_path.exists()
        else _persona_fallback,
    )


settings = Settings()
