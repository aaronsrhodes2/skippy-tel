"""The pivot phrase rule (static/skippy-pivot.js, docs/pivot.md) passes its node tests: "let's pivot" asks, "by the way" only adds."""
import shutil
import subprocess
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]


def _node():
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        return exe if int(out.lstrip("v").split(".")[0]) >= 18 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@pytest.mark.skipif(_node() is None, reason="Node 18+ isn't installed here")
def test_the_pivot_rule_passes_its_node_tests():
    d = PORTAL / "tests" / "js" / "pivot"
    run = subprocess.run([_node(), "--test", *sorted(str(p) for p in d.glob("*.test.js"))], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300, cwd=str(d))
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1500:]
