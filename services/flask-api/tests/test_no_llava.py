"""LLaVA is gone from the stack (the Captain, 2026-10-13: it was for a one-time job), and DropShip is parked.

DropShip's Drive scanner used to call the llava vision model every ten minutes, which kept a 4.9 GB model
resident in Ollama. These checks keep that from coming back by accident: no code names the model or the
modules that called it, and the app does not start a photo scanner thread.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
SOURCES = sorted(APP.rglob("*.py"))


def test_no_module_names_the_llava_model_or_the_modules_that_called_it():
    pattern = re.compile(r"""["']llava|photo_tagger|photo_scan|describe_image|_tag_async""", re.I)
    hits = [f"{p.relative_to(APP)}:{n}: {line.strip()[:80]}"
            for p in SOURCES for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(line)]
    assert not hits, hits


def test_the_scanner_and_the_tagger_files_are_gone():
    assert not (APP / "background" / "photo_scan.py").exists()
    assert not (APP / "clients" / "photo_tagger.py").exists()


def test_the_app_starts_no_background_scanner():
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert "start_background_scanner" not in main
    assert "threading.Thread(target=_run_scanner" not in main


def test_the_tag_store_keeps_what_the_dormant_routes_read():
    """/dropship/list, /latest and /search stay (parked, not deleted): their store functions must remain."""
    from app import photo_tags
    for name in ("init_db", "mark_seen", "upsert_tag", "get_page", "get_latest", "search", "get_all_seen_ids"):
        assert callable(getattr(photo_tags, name)), name
    for gone in ("get_tagged_ids", "get_recently_attempted_ids", "record_attempt"):
        assert not hasattr(photo_tags, gone), gone
