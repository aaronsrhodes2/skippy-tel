"""browse (services/purple-tools/tools.py): the address is checked, steps run in order, a fake browser stands in for Chromium, and one real page
is read through the real Playwright when it is installed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402


class FakePage:
    def __init__(self):
        self.log, self.url = [], "https://example.test/final"

    def set_default_timeout(self, ms): pass
    def goto(self, url, **k): self.log.append(("goto", url))
    def click(self, sel): self.log.append(("click", sel))
    def fill(self, sel, text): self.log.append(("fill", sel, text))
    def title(self): return "A  title"
    def query_selector(self, sel): return object()
    def inner_text(self, sel): return "Hello\n\n\n\nworld"
    def eval_on_selector_all(self, sel, js): return [["Home", "https://example.test/"], ["Home again", "https://example.test/"], ["Mail", "mailto:x@y"], ["", "https://example.test/b"]]
    def screenshot(self, path, full_page=False): Path(path).write_bytes(b"png")


class FakeBrowser:
    def __init__(self, page): self.page, self.closed = page, False
    def new_context(self, **k): return type("C", (), {"new_page": lambda s: self.page})()
    def close(self): self.closed = True


def fake_launcher(page):
    browser = FakeBrowser(page)

    class PW:
        chromium = type("Ch", (), {"launch": lambda s, headless=True: browser})()

    class Ctx:
        def __enter__(self): return PW()
        def __exit__(self, *a): return False
    return (lambda: Ctx()), browser


def test_only_web_addresses_are_opened():
    for bad in ("file:///etc/passwd", "chrome://settings", "javascript:alert(1)", "", "example.com", "http://a b"):
        with pytest.raises(ValueError):
            tools.browse({"url": bad})


def test_steps_run_in_order_and_the_page_comes_back_as_text_and_unique_web_links(tmp_path):
    page = FakePage()
    launcher, browser = fake_launcher(page)
    out = tools.browse({"url": "https://example.test/", "steps": [{"click": "text=Go"}, {"fill": "#q", "text": "hi"}], "screenshot": True}, cwd=tmp_path, launcher=launcher)
    assert page.log == [("goto", "https://example.test/"), ("click", "text=Go"), ("fill", "#q", "hi")] and browser.closed
    assert out.startswith("https://example.test/final") and "title: A title" in out and "step 2 fill: ok" in out
    assert "Hello\n\nworld" in out and out.count("https://example.test/: Home") == 1 and "mailto" not in out and "(no text): https://example.test/b" in out
    assert len(list((tmp_path / ".purple-out").iterdir())) == 1 and "screenshot saved" in out


def test_bad_steps_say_what_is_allowed_and_the_browser_is_still_closed(tmp_path):
    launcher, browser = fake_launcher(FakePage())
    with pytest.raises(ValueError, match="step 1 must be one of"):
        tools.browse({"url": "https://example.test/", "steps": [{"eval": "x"}]}, cwd=tmp_path, launcher=launcher)
    assert browser.closed
    with pytest.raises(ValueError, match="at most 20"):
        tools.browse({"url": "https://example.test/", "steps": [{"wait_ms": 1}] * 21}, launcher=launcher)


def test_a_real_page_through_real_chromium(tmp_path):
    pytest.importorskip("playwright")
    html = tmp_path / "p.html"
    html.write_text("<title>Real</title><body><h1 id=h>Hi</h1><button onclick=\"document.getElementById('h').textContent='Clicked'\">Go</button></body>")
    import http.server
    import threading
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **k)       # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        try:
            out = tools.browse({"url": f"http://127.0.0.1:{server.server_port}/p.html", "steps": [{"click": "text=Go"}], "screenshot": True}, cwd=tmp_path)
        except Exception as e:  # noqa: BLE001
            if "Executable doesn't exist" in str(e):
                pytest.skip("Chromium is not installed")
            raise
        assert "title: Real" in out and "Clicked" in out and (tmp_path / ".purple-out").exists()
    finally:
        server.shutdown()


def test_it_is_a_listed_tool():
    assert "browse" in [t["name"] for t in tools.tool_list()]
