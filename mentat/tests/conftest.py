import os
import sys
from pathlib import Path

# Import mentat's modules the way the image does (flat, from /app).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# The integration tests need a sigma-ground checkout (the image's /opt/sigma-ground):
# point SIGMA_GROUND_DIR at one, or have it on PYTHONPATH. Without it they skip.
if os.environ.get("SIGMA_GROUND_DIR"):
    sys.path.insert(0, os.environ["SIGMA_GROUND_DIR"])
