"""Every inline script on the front page parses, for the Captain and for a guest (2026-10-30: a broken string in _chat.html stopped
the whole chat script, the apps tray with it, and every test still passed because none of them ran the page's script)."""
import re
import shutil
import subprocess

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", re.S)


def _node():
    exe = shutil.which("node")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True)
    return exe if out.returncode == 0 and int(out.stdout.strip().lstrip("v").split(".")[0]) >= 18 else None


@pytest.mark.skipif(_node() is None, reason="Node 18+ isn't installed here")
@pytest.mark.parametrize("who", [ADMIN, FRIEND])
def test_every_inline_script_on_the_front_page_parses(who, tmp_path):
    app = portal.create_app({**SETTINGS, "CHAT_ENABLED": True}, vm=FakeVm(), skippy=FakeSkippy())
    client = app.test_client()
    login(client, who)
    page = client.get("/").data.decode()
    checked = 0
    for i, (attrs, body) in enumerate(SCRIPT.findall(page)):
        if not body.strip() or "importmap" in attrs or "json" in attrs:
            continue
        f = tmp_path / f"s{i}.{'mjs' if 'module' in attrs else 'js'}"
        f.write_text(body, encoding="utf-8")
        run = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, f"inline script {i} on / for {who} doesn't parse:\n{run.stderr[:1500]}"
        checked += 1
    assert checked >= 2
