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

    # Stable Diffusion image service (host-side, reached via host.docker.internal)
    sd_base_url: str = os.getenv("SD_BASE_URL", "http://host.docker.internal:5000")

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

    # System prompt for /intent/unmatched escalations.
    # SKIPPY_SYSTEM env var overrides the file (useful for quick iteration).
    skippy_system: str = os.getenv(
        "SKIPPY_SYSTEM",
        _persona_path.read_text(encoding="utf-8")
        if _persona_path.exists()
        else _persona_fallback,
    )


settings = Settings()
