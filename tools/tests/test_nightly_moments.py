"""The nightly run's moments sweep (tools/nightly.py moments_sweep, portal/vision.py sweep): it asks the portal with the home token, says how many
old clips went, and never fails the ledger when the portal or its bucket is not there."""
import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adaptive_ui  # noqa: E402
import nightly  # noqa: E402


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_sweep_posts_to_the_portal_with_the_home_token_and_counts_the_clips(monkeypatch):
    sent = {}

    def urlopen(req, timeout=0):
        sent.update(url=req.full_url, method=req.get_method(), auth=req.get_header("Authorization"), timeout=timeout)
        return Reply(json.dumps({"clips_deleted": 3}).encode())
    monkeypatch.setattr(nightly.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(adaptive_ui, "vault_value", lambda name: "tok-123")
    lines = []
    got = nightly.moments_sweep(False, lines.append)
    assert got == {"ok": True, "clips_deleted": 3}
    assert sent["url"].endswith("/home/vision/sweep") and sent["method"] == "POST" and sent["auth"] == "Bearer tok-123"
    assert any("moments sweep" in line for line in lines)


def test_a_dry_run_asks_nothing_and_a_missing_token_or_a_portal_without_a_bucket_is_a_line_not_a_failure(monkeypatch):
    monkeypatch.setattr(nightly.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a dry run must not call the portal")))
    assert nightly.moments_sweep(True, print) == {"dry": True}
    monkeypatch.setattr(adaptive_ui, "vault_value", lambda name: "")
    assert nightly.moments_sweep(False, lambda m: None) == {"ok": False, "why": "no home token"}
    monkeypatch.setattr(adaptive_ui, "vault_value", lambda name: "tok")

    def no_bucket(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 503, "no storage", {}, None)
    monkeypatch.setattr(nightly.urllib.request, "urlopen", no_bucket)
    assert nightly.moments_sweep(False, lambda m: None) == {"ok": False, "why": "HTTPError"}


def test_the_overnight_line_mentions_cleared_clips_only_when_there_were_some():
    base = {"systems": {"live": {"ok": True}, "behind": False, "ci": {"state": "success"}}, "jobs": [], "upkeep": {"disks": {}, "hub": {}}}
    assert "clip" not in nightly.overnight_line({**base, "moments": {"ok": True, "clips_deleted": 0}})
    assert "1 old moment clip cleared" in nightly.overnight_line({**base, "moments": {"ok": True, "clips_deleted": 1}})
    assert "2 old moment clips cleared" in nightly.overnight_line({**base, "moments": {"ok": True, "clips_deleted": 2}})
