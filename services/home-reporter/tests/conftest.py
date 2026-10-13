import os
import sys
from pathlib import Path

os.environ.setdefault("LOCAL_INFO", "off")                      # weather and traffic never touch the network in tests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
