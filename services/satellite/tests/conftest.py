import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))        # the satellite's modules; run this folder in its own pytest session (other services have a gate.py too)
