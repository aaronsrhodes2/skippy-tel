import importlib.util
import os
import sys
from pathlib import Path

# mentat/wva.py and research.py, importable as `wva` and `research` (as in the image, where it sits next to app.py). Loaded by
# path, not by putting mentat/ on sys.path: this suite runs in one pytest process with the
# portal's, and mentat/app.py would shadow the portal's `app` module.
MENTAT = Path(__file__).resolve().parents[1]
for _name in ("ready", "wva", "research", "router_stage", "cascade_stage"):        # research.py: the research log app.py imports
    if _name not in sys.modules:
        _spec = importlib.util.spec_from_file_location(_name, MENTAT / f"{_name}.py")
        sys.modules[_name] = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(sys.modules[_name])

# The integration tests need a sigma-ground checkout (the image's /opt/sigma-ground):
# point SIGMA_GROUND_DIR at one, or have it importable. Without it they skip.
if os.environ.get("SIGMA_GROUND_DIR"):
    sys.path.insert(0, os.environ["SIGMA_GROUND_DIR"])
