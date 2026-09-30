import sys
from pathlib import Path

# Import the flask-api the way its image does (`app` package, from /app).
# Run these on their own: `python -m pytest services/flask-api/tests -q`.
# The portal has an `app.py` of its own, so one pytest session can't hold both.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
