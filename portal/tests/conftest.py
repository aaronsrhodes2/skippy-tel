import sys
from pathlib import Path

# Import portal modules the way the image does (flat, from /app).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
