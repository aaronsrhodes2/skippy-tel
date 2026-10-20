"""Artifacts in Skippy's chat (static/skippy-artifacts.js): the Node tests, and the promises that keep an artifact from reaching
the portal: an HTML page runs sandboxed WITHOUT allow-same-origin, an SVG is only ever an <img>, text goes in with textContent."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "skippy-artifacts.js").read_text(encoding="utf-8")


def _node():
    exe = shutil.which("node")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    return exe if int(re.findall(r"\d+", out)[0]) >= 18 else None


@pytest.mark.skipif(_node() is None, reason="Node 18+ isn't installed here")
def test_the_node_tests_pass():
    d = Path(__file__).resolve().parent / "js" / "artifacts"
    run = subprocess.run([_node(), "--test", *sorted(str(p) for p in d.glob("*.test.js"))], capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr


def test_an_html_artifact_is_sandboxed_away_from_the_portal():
    sandbox = re.findall(r'setAttribute\("sandbox", "([^"]*)"\)', JS)
    assert sandbox == ["allow-scripts allow-pointer-lock"]                       # scripts run; same-origin never granted
    assert "srcdoc" in JS and ".innerHTML" not in JS


def test_an_svg_is_only_an_image_and_media_come_only_from_portal_urls():
    assert 'data:image/svg+xml;charset=utf-8," + encodeURIComponent(spec.content)' in JS
    assert "fetch(" not in JS and "XMLHttpRequest" not in JS


def test_the_chat_loads_it_and_the_3d_viewer_is_self_hosted():
    front = (Path(__file__).resolve().parents[1] / "templates" / "front.html").read_text(encoding="utf-8")
    chat = (Path(__file__).resolve().parents[1] / "templates" / "_chat.html").read_text(encoding="utf-8")
    assert "skippy-artifacts.js" in front and "{block: artifact}" in chat and "SkippyArtifacts.fromMedia" in chat
    assert (STATIC / "vendor" / "model-viewer-4.0.0.min.js").is_file() and (STATIC / "vendor" / "model-viewer.LICENSE.txt").is_file()
    assert "vendor/model-viewer-4.0.0.min.js" in chat
