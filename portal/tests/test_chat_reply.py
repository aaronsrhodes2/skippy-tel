"""Reply to a part, in both chats (like Claude Desktop): select words in any message, tap the Reply key by them, and they go over the
box as a removable quote and at the start of the message as a markdown blockquote, so every brain knows which part is meant with no
server change (static/skippy-reply.js)."""
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
def test_the_reply_helpers_pass_their_node_tests():
    d = PORTAL / "tests" / "js" / "reply"
    run = subprocess.run([_node(), "--test", *sorted(str(p) for p in d.glob("*.test.js"))], capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]


def test_the_helper_is_loaded_before_the_chat_and_its_glyph_exists():
    front = (TEMPLATES / "front.html").read_text(encoding="utf-8")
    assert front.index("skippy-reply.js") < front.index('include "_chat.html"')
    assert 'id="g-reply"' in (TEMPLATES / "_glyphs.html").read_text(encoding="utf-8")


def test_the_composer_keeps_quotes_and_sends_them_as_blockquotes():
    chat = (TEMPLATES / "_chat.html").read_text(encoding="utf-8")
    assert '<div class="quotes" hidden>' in chat                                    # over the box, each with its own remove key
    assert 'SkippyReply.attach({scope: root.querySelector(".stage"), bodies: ".turn .tx, .me-chat .turn"' in chat   # both transcripts
    assert "SkippyReply.compose(pending_quotes.splice(0), text)" in chat             # in the message text: no server change
    assert "msg.required = !pending_quotes.length" in chat                          # a quote alone may go
    assert "SkippyCompose.quote" in chat


def test_both_transcripts_show_the_quotes_in_your_bubble():
    for name in ("_chat.html", "_workshop.html"):
        page = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "SkippyReply.split(" in page and "SkippyReply.quoteNode(" in page, name


def test_the_key_works_on_a_phone():
    js = (PORTAL / "static" / "skippy-reply.js").read_text(encoding="utf-8")
    assert '"selectionchange"' in js                                                # long-press selections fire this on iOS and Android
    assert 'addEventListener("pointerdown"' in js and "preventDefault" in js        # acts before the tap clears the selection
    assert "innerHTML" not in js                                                    # quotes are text, never markup
