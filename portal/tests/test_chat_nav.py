"""Words that move the stage (static/skippy-nav.js): the chat's navigation keys as things to say. Runs tests/js/nav under Node 18+; skipped without it."""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node 18+")
def test_navigation_words_run_under_node():
    r = subprocess.run(["node", "--test", *sorted(str(p) for p in (HERE / "js" / "nav").glob("*.test.js"))], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
