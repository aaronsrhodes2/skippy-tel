"""core/quiet.py: builders keep off the box while the private Skippy is working, but never for more than four hours."""
from core_testlib import load

quiet = load("quiet.py", "core_quiet")


def test_not_busy_clears_the_stamp_and_never_defers(tmp_path):
    f = tmp_path / "busy-since"
    f.write_text("1")
    assert quiet.should_defer(f, now=100, is_busy=lambda: False) is False and not f.exists()


def test_busy_defers_then_gives_up_after_the_limit(tmp_path):
    f = tmp_path / "busy-since"
    assert quiet.should_defer(f, now=1000, is_busy=lambda: True) is True and f.exists()
    assert quiet.should_defer(f, now=1000 + quiet.MAX_DEFER_S - 1, is_busy=lambda: True) is True
    assert quiet.should_defer(f, now=1000 + quiet.MAX_DEFER_S + 1, is_busy=lambda: True) is False   # a stuck flag can't hold deploys forever


def test_an_unreachable_bridge_is_not_busy():
    assert quiet.busy("http://127.0.0.1:9/health", timeout=1) is False


def test_busy_reads_the_bridges_nested_workshop_flag(monkeypatch):
    import io
    import json
    for body, want in (({"workshop": {"busy": True}}, True), ({"workshop": {"busy": False}}, False), ({"busy": True}, True)):
        monkeypatch.setattr(quiet.urllib.request, "urlopen", lambda *a, _b=body, **k: io.BytesIO(json.dumps(_b).encode()))
        assert quiet.busy("http://x/health") is want
