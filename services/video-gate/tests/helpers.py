"""Constants and small helpers shared by the tests (importable by name, unlike conftest)."""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gate  # noqa: E402

NOW = 1_800_000_000
HOME_TOKEN = "test-home-token"
KEY = gate.gate_key(HOME_TOKEN)


def by_title(stack):
    """title → catalog item, from what the player would download."""
    data = json.loads(gzip.decompress(stack.get("/catalog.json").data))
    return {i["title"]: i for i in data["items"]}
