"""Markdown in the private Skippy's replies: the renderer's own tests run under Node (tests/js/md), and the page loads it before the
chat script that calls it and renders every text part through it (never innerHTML)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]


def test_the_chat_renders_text_parts_through_the_markdown_module_and_loads_it_first():
    me = (PORTAL / "templates" / "me.html").read_text(encoding="utf-8")
    shop = (PORTAL / "templates" / "_workshop.html").read_text(encoding="utf-8")
    assert me.index("skippy-md.js") < me.index('include "_workshop.html"')
    assert "SkippyMd.render(p.text, document)" in shop
    js = (PORTAL / "static" / "skippy-md.js").read_text(encoding="utf-8")
    assert not re.search(r"\.innerHTML|insertAdjacentHTML|eval\(|document\.write", js)     # text only goes in with textContent
    assert not re.search(r"fetch\(|XMLHttpRequest|sendBeacon", js)               # no network calls


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
def test_the_markdown_modules_node_tests_pass():
    d = PORTAL / "tests" / "js" / "md"
    run = subprocess.run([_node(), "--test", *sorted(str(p) for p in d.glob("*.test.js"))], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300, cwd=str(d))
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1500:]
