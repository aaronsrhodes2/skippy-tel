"""read_pdf (services/purple-tools/tools.py): page ranges, the text layer, a scan pointed at the OCR pop-up, and plain refusals."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402

pypdf = pytest.importorskip("pypdf")


def make_pdf(path, pages):
    """A PDF with one page per string (empty string = a blank page with no text layer)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = PdfWriter()
    for text in pages:
        page = w.add_blank_page(width=300, height=300)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = w._add_object(stream)
    with open(path, "wb") as f:
        w.write(f)


def test_page_ranges():
    assert tools._page_range("", 5) == [0, 1, 2, 3, 4]
    assert tools._page_range("2,4-5,9", 6) == [1, 3, 4]
    assert tools._page_range("3", 10) == [2]
    for bad in ("x", "1-", "-3"):
        with pytest.raises(ValueError):
            tools._page_range(bad, 5)
    with pytest.raises(ValueError, match="outside"):
        tools._page_range("9", 5)


def test_text_comes_back_by_page_and_a_range_picks_pages(tmp_path):
    f = tmp_path / "doc.pdf"
    make_pdf(f, ["first page has plenty of words", "second page has other words", "third page closes the story"])
    out = tools.read_pdf({"path": str(f)}, cwd=tmp_path)
    assert "doc.pdf: 3 pages" in out and "--- page 2 ---" in out and "second page has other words" in out
    only = tools.read_pdf({"path": str(f), "pages": "3"}, cwd=tmp_path)
    assert "third page closes" in only and "first page" not in only and "(showing 1)" in only


def test_a_page_without_text_is_named_as_a_scan_and_never_invented(tmp_path):
    f = tmp_path / "scan.pdf"
    make_pdf(f, ["a page with real words in it", ""])
    out = tools.read_pdf({"path": str(f)}, cwd=tmp_path)
    assert "real words" in out and "no text layer on page(s) 2" in out and "read_document" in out


def test_missing_and_non_pdf_files_say_why(tmp_path):
    with pytest.raises(ValueError, match="no file at"):
        tools.read_pdf({"path": str(tmp_path / "nope.pdf")})
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    out, is_error = tools.call("read_pdf", {"path": str(junk)})
    assert is_error


def test_it_is_a_listed_tool():
    assert "read_pdf" in [t["name"] for t in tools.tool_list()]
