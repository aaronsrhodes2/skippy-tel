"""reference.py: before a Core run that draws its work, the best picture of what was asked is found, picked from a contact sheet
and described; the builder gets the sheet, and every fault review re-compares the work with the picture (work_check.py)."""
import io
import json
import time

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

import eyes  # noqa: E402
import reference  # noqa: E402
import work_check as wc  # noqa: E402
from test_work_check import _project  # noqa: E402

CORE = {"SKIPPY_CORE_EYES": "1", "ANTHROPIC_BASE_URL": "http://core", "ANTHROPIC_API_KEY": "k"}


def _png(colour, size=(400, 300)):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


def _web(results):
    """A fake web: SearXNG's JSON for any search, and each picture by its address (a missing one is a dead link)."""
    pictures = {("https:" + r["img_src"]) if r["img_src"].startswith("//") else r["img_src"]: r.pop("_png") for r in results if "_png" in r}

    def get(url):
        if "/search?" in url:
            return json.dumps({"results": results}).encode()
        if url not in pictures:
            raise OSError("404")
        return pictures[url]
    return get


def _core(answers):
    """A fake Core that answers each question in turn and records what it was sent: (number of pictures, text)."""
    asked = []

    def post(req):
        content = json.loads(req.data)["messages"][0]["content"]
        asked.append((sum(1 for b in content if b["type"] == "image"), next(b["text"] for b in content if b["type"] == "text")))
        return {"content": [{"type": "text", "text": answers[len(asked) - 1]}]}
    return post, asked


def test_it_finds_the_best_pictures_and_writes_what_they_all_share_and_keeps_it_for_the_run(tmp_path):
    root = _project(tmp_path)
    get = _web([{"img_src": "https://x/dead.jpg", "url": "https://x/1"},
                {"img_src": "https://x/red.png", "url": "https://x/red", "_png": _png("red")},
                {"img_src": "//x/blue.png", "thumbnail_src": "", "url": "https://x/blue", "_png": _png("blue")}])
    post, asked = _core(["a red bicycle\n", "2, 1", "1. Two wheels of equal size.\n2. The frame fills the picture."])
    ref = reference.prepare(str(root), "build me a red bicycle", "http://core", "k", post=post, get=get)
    assert ref["query"] == "a red bicycle" and ref["sources"] == ["https://x/blue", "https://x/red"]   # the dead link skipped, best first
    assert asked[0][0] == 0 and "build me a red bicycle" in asked[0][1]                     # the query: a question with no picture
    assert asked[1][0] == 1 and "2 numbered pictures" in asked[1][1] and "best 2" not in asked[1][1]   # one contact sheet, not a call per picture
    assert asked[2][0] == 1 and "ALL 2 examples share" in asked[2][1]      # the sheet: what they all have is the kind
    kept = Image.open(ref["image"])
    (r, g, b), (r2, g2, b2) = kept.getpixel((5, 5)), kept.getpixel((kept.width - 5, 5))
    assert b > 240 and r < 15 and r2 > 240 and b2 < 15                                     # side by side, best first (JPEG, near enough)
    assert reference.load(str(root), time.time() - 5)["sheet"].startswith("1. Two wheels")
    assert reference.load(str(root), time.time() + 3600) is None                            # an older run's reference never counts


def test_a_task_that_pictures_nothing_or_finds_nothing_has_no_reference(tmp_path):
    root = _project(tmp_path)
    post, _ = _core(["NONE"])
    assert reference.prepare(str(root), "rename the variables", "http://core", "k", post=post, get=_web([])) is None
    post, _ = _core(["a teapot"])
    assert reference.prepare(str(root), "draw a teapot", "http://core", "k", post=post, get=_web([])) is None


def test_it_is_on_only_for_a_core_run_in_a_project_that_draws_its_work(tmp_path):
    root = _project(tmp_path)
    assert reference.wanted(str(root), CORE)
    assert not reference.wanted(str(root), {})                                      # a Claude run looks for itself
    (root / wc.CONFIG).write_text(json.dumps({"checks": [{"files": "*.out", "check": "x"}]}))
    assert not reference.wanted(str(root), CORE)                                    # nothing is drawn
    (root / wc.CONFIG).write_text(json.dumps({"reference": False, "checks": [{"files": "*.out", "show": "x"}]}))
    assert not reference.wanted(str(root), CORE)                                    # switched off


def test_the_hook_says_nothing_when_it_fails_and_never_blocks(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    for k, v in CORE.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "prompt": "draw a teapot"})))
    monkeypatch.setattr(reference, "prepare", lambda *a, **k: (_ for _ in ()).throw(OSError("no search")))
    assert reference.main() == 0 and capsys.readouterr().out == ""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "prompt": "draw a teapot"})))
    monkeypatch.setattr(reference, "prepare", lambda *a, **k: {"image": "r.jpg", "sheet": "1. A spout.", "query": "teapot", "source": "s"})
    out = json.loads(capsys.readouterr().out or "{}") if reference.main() == 0 else {}
    told = out["hookSpecificOutput"]
    assert told["hookEventName"] == "UserPromptSubmit" and "1. A spout." in told["additionalContext"]


def test_every_fault_review_sends_the_reference_first_and_goes_through_its_sheet(tmp_path):
    root = _project(tmp_path)
    started = time.time() - 1
    folder = tmp_path / "ref"
    folder.mkdir()
    image = folder / "reference.jpg"
    image.write_bytes(_png("green"))
    monkeypatch_dir = reference._clone_dir(str(root))
    import os
    os.makedirs(monkeypatch_dir, exist_ok=True)
    with open(os.path.join(monkeypatch_dir, "reference.json"), "w") as f:
        json.dump({"image": str(image), "sheet": "1. A spout on one side.", "query": "teapot", "source": "s", "made": time.time()}, f)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "draw a teapot"}}) + "\n")
    post, asked = _core(["1. Spout: missing.\nWORST FAULT: there is no spout", "1. Spout: present.\nWORST FAULT: none"])
    (root / "a.out").write_text("good v1")
    first = wc.unfinished(str(root), started, {}, str(transcript), env=CORE, post=post)
    assert asked[0][0] == 2 and "The FIRST is the reference: examples" in asked[0][1] and "1. A spout on one side." in asked[0][1]
    assert "the reference picture (" in first and "The worst fault: there is no spout" in first


def test_the_task_is_never_a_compaction_summary(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(d) for d in [
        {"type": "user", "message": {"content": "draw a teapot"}},
        {"type": "user", "isCompactSummary": True, "message": {"content": "This session is being continued from a previous conversation"}}]))
    assert eyes.task_of(str(t)) == "draw a teapot"


def test_a_check_can_give_the_reviewer_its_own_bigger_drawing(tmp_path):
    root = _project(tmp_path)
    cfg = json.loads((root / wc.CONFIG).read_text())
    cfg["checks"][0]["review"] = cfg["checks"][0]["show"].replace("show.py", "big.py")
    (root / wc.CONFIG).write_text(json.dumps(cfg))
    (root / "big.py").write_text("import sys\nopen(sys.argv[2], 'wb').write(b'BIG')\n")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "draw a teapot"}}) + "\n")
    post, asked = _core(["A teapot with no spout.\nWORST FAULT: there is no spout"])
    (root / "a.out").write_text("good v1")
    said = wc.unfinished(str(root), time.time() - 1, {}, str(transcript), env=CORE, post=post)
    assert "-review.png, no need to open it" in said and asked                # the reviewer saw the big drawing, not the thumbnail


def test_real_photos_come_first_and_the_web_only_fills_in(tmp_path):
    root = _project(tmp_path)
    pictures = {f"https://c/{i}.png": _png(c) for i, c in enumerate(("red", "green", "blue"))}
    pictures["https://web/cartoon.png"] = _png("yellow")
    searched = []

    def get(url):
        if "/search?" in url:
            searched.append(url)
            if "engines=wikicommons.images" in url:
                assert "categories" not in url and "photo" not in url.split("q=")[1].split("&")[0]
                return json.dumps({"results": [{"img_src": u, "url": u} for u in pictures if "/c/" in u]}).encode()
            return json.dumps({"results": [{"img_src": "https://web/cartoon.png", "url": "w"}]}).encode()
        return pictures[url]
    post, asked = _core(["teapot photo", "1 2 3", "1. A spout."])
    ref = reference.prepare(str(root), "draw a teapot", "http://core", "k", post=post, get=get)
    assert len(searched) == 1 and ref["sources"] == ["https://c/0.png", "https://c/1.png", "https://c/2.png"]   # three real ones: no web
