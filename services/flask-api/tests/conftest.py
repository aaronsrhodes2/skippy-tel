import sys
from pathlib import Path

# Import the flask-api the way its image does (`app` package, from /app).
# Run these on their own: `python -m pytest services/flask-api/tests -q`.
# The portal has an `app.py` of its own, so one pytest session can't hold both.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_kept_answers():
    """/status.json and the telemetry summary are kept for seconds or a minute in production (app/offload.py says why); a test must
    never be handed what another test left behind."""
    from app.routes import status, telemetry
    status._SNAPSHOT.update(at=None, payload=None)
    telemetry._SUMMARY_CACHE.clear()
    yield
