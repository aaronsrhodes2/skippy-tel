"""A purple clone's files are LF: CRLF files made every multi-line Edit fail and sent the model into a "stale Read" retry loop (2026-11-13)."""
import subprocess

import workshop_engine as engine


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "core.autocrlf", "true")
    (tmp_path / "a.js").write_bytes(b"one\ntwo\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "x")
    (tmp_path / "a.js").unlink()
    _git(tmp_path, "checkout-index", "-f", "-a")               # autocrlf=true: the working file is CRLF now
    return tmp_path


def _shop(tmp_path):
    return engine.Workshop(["claude"], repo=str(tmp_path), primary=str(tmp_path), sessions=str(tmp_path / "s.json"), state_dir=str(tmp_path / "state"))


def test_a_clean_crlf_clone_is_written_again_as_lf(tmp_path):
    r = _repo(tmp_path)
    assert b"\r\n" in (r / "a.js").read_bytes()
    assert _shop(tmp_path)._lf_checkout(str(r)) is True
    assert (r / "a.js").read_bytes() == b"one\ntwo\n"
    assert _git(r, "status", "--porcelain").stdout.strip() == ""      # git sees no change
    assert _shop(tmp_path)._lf_checkout(str(r)) is False              # nothing left to do


def test_a_dirty_clone_is_never_rewritten(tmp_path):
    r = _repo(tmp_path)
    (r / "a.js").write_bytes(b"one\r\nTWO edited\r\n")
    assert _shop(tmp_path)._lf_checkout(str(r)) is False
    assert (r / "a.js").read_bytes() == b"one\r\nTWO edited\r\n"       # uncommitted work untouched
