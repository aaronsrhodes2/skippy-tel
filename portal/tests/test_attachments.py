"""Files with a chat message (attachments.py): what is accepted, what is refused, and how each brain is handed them."""
import base64
import io
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import attachments as att  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

import app as portal  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def url(mime, data):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def tiny_pdf(text="Hello from a PDF"):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pictures_text_and_pdfs_are_accepted_and_typed_by_their_bytes():
    files = att.clean([{"name": "a.jpg", "data": url("image/jpeg", JPEG)},
                       {"name": "notes.md", "data": url("text/markdown", "# hi\nthere".encode())},
                       {"name": "game.py", "data": url("application/octet-stream", b"print(1)")},
                       {"name": "doc.pdf", "data": url("application/pdf", tiny_pdf())}])
    assert [f.kind for f in files] == ["image", "text", "text", "pdf"]
    assert files[1].text() == "# hi\nthere"


@pytest.mark.parametrize("item, says", [
    ({"name": "fake.png", "data": url("image/png", JPEG)}, "isn't the picture"),          # a jpeg pretending to be a png
    ({"name": "x.pdf", "data": url("application/pdf", b"not a pdf")}, "isn't a PDF"),
    ({"name": "x.bin", "data": url("application/octet-stream", b"\x00\x01")}, "pictures, PDFs and text"),
    ({"name": "x.txt", "data": url("text/plain", b"\xff\xfe\x00bad")}, "isn't plain text"),
    ({"name": "x.txt", "data": "data:text/plain;base64,@@@"}, "can read"),
    ({"name": "x.txt"}, "whole"),
])
def test_anything_else_is_refused_with_a_reason(item, says):
    with pytest.raises(ValueError, match=says):
        att.clean([item])


def test_count_and_size_limits():
    one = {"name": "a.png", "data": url("image/png", PNG)}
    with pytest.raises(ValueError, match="At most 4"):
        att.clean([one] * 5)
    big = {"name": "big.txt", "data": url("text/plain", b"a" * 1_100_000)}
    with pytest.raises(ValueError, match="too big"):
        att.clean([big])


def test_each_brain_gets_them_in_its_own_shape():
    pic, doc = att.clean([{"name": "a.png", "data": url("image/png", PNG)},
                          {"name": "n.txt", "data": url("text/plain", b"the notes")}])
    blocks = att.anthropic_blocks([pic, doc], "look")
    assert blocks[0]["type"] == "image" and "the notes" in blocks[1]["text"] and blocks[-1] == {"type": "text", "text": "look"}
    parts = att.openai_parts([pic, doc], "look")
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,") and parts[-1]["text"] == "look"
    text = att.text_for([pic, doc], "look", describe=lambda f: "a tiny black square")
    assert "a tiny black square" in text and "the notes" in text and text.endswith("look")


def test_a_guest_can_show_skippy_core_a_picture_through_the_vision_slot():
    skippy = FakeSkippy()
    calls = []

    def reply(system, messages, tools=None, run_tool=None, model=None, force_tool=None):
        calls.append((model, messages[-1]["content"]))
        return "a red brick wall" if model == "vision" else "Nice bricks."
    skippy.reply = reply
    client = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy).test_client()
    login(client, FRIEND)
    r = client.post("/chat/send", json={"message": "what is this?", "files": [{"name": "a.png", "data": url("image/png", PNG)}]})
    assert r.status_code == 200 and r.get_json()["reply"] == "Nice bricks."
    (vmodel, vcontent), (cmodel, ccontent) = calls
    assert vmodel == "vision" and vcontent[0]["type"] == "image"                 # the vision slot looked at the picture
    assert cmodel == "chat" and isinstance(ccontent, str) and "a red brick wall" in ccontent and ccontent.endswith("what is this?")


def test_skippy_core_gets_the_files_as_blocks_and_a_bad_file_is_a_400():
    skippy = FakeSkippy()
    client = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy).test_client()
    login(client, ADMIN)
    r = client.post("/chat/send", json={"message": "read this",
                                        "files": [{"name": "doc.pdf", "data": url("application/pdf", tiny_pdf())}]})
    assert r.status_code == 200
    content = skippy.seen[1][-1]["content"]
    assert isinstance(content, str) and content.rstrip().endswith("read this")         # Skippy Core reads the PDF as text
    r = client.post("/chat/send", json={"message": "x", "files": [{"name": "x.exe", "data": url("application/x-msdownload", b"MZ")}]})
    assert r.status_code == 400 and "pictures, PDFs and text" in r.get_json()["error"]
    assert json.dumps(r.get_json()).count("MZ") == 0
