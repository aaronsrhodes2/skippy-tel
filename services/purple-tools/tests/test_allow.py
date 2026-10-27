"""The tools read files only from the run's own folder (and PURPLE_ALLOWED_ROOTS)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402


def test_inside_the_folder_is_fine_outside_and_dotdot_are_refused(tmp_path, monkeypatch):
    run, other = tmp_path / "run", tmp_path / "other"
    run.mkdir()
    other.mkdir()
    (run / "a.pdf").write_bytes(b"x")
    (other / "secret.pdf").write_bytes(b"x")
    monkeypatch.delenv("PURPLE_ALLOWED_ROOTS", raising=False)
    assert tools.checked_path("a.pdf", run) == (run / "a.pdf").resolve()
    assert tools.checked_path(str(run / "a.pdf"), run) == (run / "a.pdf").resolve()
    for bad in (str(other / "secret.pdf"), "../other/secret.pdf"):
        with pytest.raises(ValueError, match="outside this run's folder"):
            tools.checked_path(bad, run)
    monkeypatch.setenv("PURPLE_ALLOWED_ROOTS", str(other))
    assert tools.checked_path(str(other / "secret.pdf"), run).name == "secret.pdf"


def test_read_pdf_and_popup_input_honour_it(tmp_path, monkeypatch):
    run, other = tmp_path / "run", tmp_path / "other"
    run.mkdir()
    other.mkdir()
    (other / "s.pdf").write_bytes(b"x")
    monkeypatch.delenv("PURPLE_ALLOWED_ROOTS", raising=False)
    with pytest.raises(ValueError, match="outside"):
        tools.read_pdf({"path": str(other / "s.pdf")}, cwd=run)
    with pytest.raises(ValueError, match="outside"):
        tools.run_popup({"name": "x"}, {"input_path": str(other / "s.pdf")}, post=lambda *a, **k: {}, cwd=run)
