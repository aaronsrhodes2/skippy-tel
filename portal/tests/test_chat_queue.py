"""The chat queue, the same in both chats (the Captain, 2026-10-30: every QOL feature in both; only the tools differ): what is sent
while Skippy works waits in line under the box and goes as ONE message when he is done, repeats taken out (static/skippy-queue.js)."""
import shutil
import subprocess
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
TEMPLATES = PORTAL / "templates"


def _node():
    exe = shutil.which("node")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True)
    return exe if out.returncode == 0 and int(out.stdout.strip().lstrip("v").split(".")[0]) >= 18 else None


@pytest.mark.skipif(_node() is None, reason="Node 18+ isn't installed here")
def test_the_queue_helpers_pass_their_node_tests():
    d = PORTAL / "tests" / "js" / "queue"
    run = subprocess.run([_node(), "--test", *sorted(str(p) for p in d.glob("*.test.js"))], capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]


def test_both_chats_queue_through_the_shared_helper():
    front = (TEMPLATES / "front.html").read_text(encoding="utf-8")
    assert front.index("skippy-queue.js") < front.index('include "_chat.html"')     # loaded before either chat runs
    for name in ("_chat.html", "_workshop.html"):
        page = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "SkippyQueue.add(" in page and "SkippyQueue.gulp(" in page, name
        assert "Several things queued" not in page, name                            # one wording, in the helper
