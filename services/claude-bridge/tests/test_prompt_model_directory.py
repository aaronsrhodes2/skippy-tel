"""The private Skippy's prompt sends it to core/models.json before it looks for a model, and tells it the chat speaks its
replies itself (it once said its chat was text-only and went hunting for a TTS engine that was already running)."""
from pathlib import Path

PROMPT = (Path(__file__).resolve().parents[1] / "workshop_prompt.md").read_text(encoding="utf-8")


def test_the_prompt_points_at_the_model_directory():
    assert "core\models.json" in PROMPT and "`home`" in PROMPT and "`slots`" in PROMPT


def test_the_prompt_says_the_chat_speaks_and_no_tts_tool_is_needed():
    assert "speaker key" in PROMPT and "voice-gate" in PROMPT and "never render speech" in PROMPT
