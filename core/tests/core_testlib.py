"""core/ modules are loaded by path under unique names: the main CI session also holds portal/, tools/ and mentat/,
whose modules (app, models, ...) must not shadow these."""
import importlib.util
import os
import sys
from pathlib import Path

# The quiet gate (core/quiet.py) asks the real bridge whether the private Skippy is working. Tests must never depend on that.
os.environ["QUIET_BRIDGE_URL"] = "http://127.0.0.1:9/health"

CORE = Path(__file__).resolve().parents[1]


def load(rel: str, name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CORE / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
