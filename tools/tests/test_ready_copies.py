"""Every service that serves the readiness contract keeps an identical copy of core/ready.py beside its code (services build as separate
images): this fails when one drifts, and when a service's image stops shipping its copy (PLATINUM section 24)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANON = ROOT / "core" / "ready.py"
# (where the copy sits, the Dockerfile that must ship it)
COPIES = (("mentat", "mentat/Dockerfile"), ("services/flask-api/app", "services/flask-api/Dockerfile"),
          ("services/music-gate", "services/music-gate/Dockerfile"), ("services/video-gate", "services/video-gate/Dockerfile"),
          ("services/voice-gate", "services/voice-gate/Dockerfile"), ("services/tts-studio", "services/tts-studio/Dockerfile"))


def _text(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def test_every_copy_of_the_readiness_helper_is_identical_to_the_canonical_one():
    want = _text(CANON)
    for where, _ in COPIES:
        assert _text(ROOT / where / "ready.py") == want, f"{where}/ready.py differs from core/ready.py: cp core/ready.py {where}/ready.py"


def test_each_image_ships_its_copy():
    for where, dockerfile in COPIES:
        text = (ROOT / dockerfile).read_text(encoding="utf-8")
        # flask-api copies the whole app folder; the others name the file
        assert "ready.py" in text or (where.endswith("/app") and "COPY app" in text.replace("./app", "app")), f"{dockerfile} doesn't ship ready.py"
