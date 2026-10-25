import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                 # corpus, index, app, models
sys.path.insert(0, str(HERE.parent / "diarize"))     # diarize
sys.path.insert(0, str(HERE.parents[2]))             # the repo root: the memory package (the archive's parser)
