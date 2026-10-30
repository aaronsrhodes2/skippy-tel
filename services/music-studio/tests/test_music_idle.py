"""music_idle.py's one decision: end ACE-Step's server only when its models are loaded and no song is queued or running."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("music_idle", Path(__file__).resolve().parents[1] / "music_idle.py")
music_idle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(music_idle)

LOADED = {"models_initialized": True, "llm_initialized": True}
QUIET = {"jobs": {"queued": 0, "running": 0}, "queue_size": 0}


def test_loaded_and_quiet_is_idle():
    assert music_idle.idle(LOADED, QUIET) is True
    assert music_idle.idle({"models_initialized": False, "llm_initialized": True}, QUIET) is True


def test_a_bare_server_is_left_alone():
    assert music_idle.idle({"models_initialized": False, "llm_initialized": False}, QUIET) is False


def test_a_song_queued_or_running_is_never_cut_off():
    assert music_idle.idle(LOADED, {"jobs": {"queued": 0, "running": 1}, "queue_size": 0}) is False
    assert music_idle.idle(LOADED, {"jobs": {"queued": 1, "running": 0}, "queue_size": 0}) is False
    assert music_idle.idle(LOADED, {"jobs": {}, "queue_size": 2}) is False


def test_no_answer_is_not_idle():
    assert music_idle.idle(None, QUIET) is False and music_idle.idle(LOADED, None) is False
