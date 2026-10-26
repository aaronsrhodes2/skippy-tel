"""Shifting Prophesy (/shifting-prophesy): purple Skippy's shake-down cruise; the portal shows both builds and every turn.

The referee's runner on skippy-pc uploads each build and the turn record through the home-token route into the portal's own
bucket (prophesy/<folder>/...); the portal reads them at request time (prophesy.py). What can be checked here without a
bucket: who gets in, that only the home token can upload, that a build is framed under the app bar with its query string
intact and never edited, that each file goes out with the right type, that nothing outside a folder can be read or written,
that the board shows the record and checks its shape on the way in, and that a portal with no bucket says so."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import prophesy  # noqa: E402
from test_app import ADMIN, FRIEND, PAL, FakeSkippy, FakeVm, SETTINGS, _add_friend, _pair, login  # noqa: E402

import app as portal  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake"
GAME_HTML = b"<html><head></head><body><canvas id='c'></canvas><script type='module' src='src/main.js'></script></body></html>"


def _version(commit):
    return json.dumps({"build": "7-" + commit[:7], "commit": commit, "built_at": "2026-10-25T00:00:00Z", "branch": "main",
                       "secret": "/etc/passwd"}).encode()


def _index(turns):
    return json.dumps({"experiment": "shifting-prophesy", "turns": turns}).encode()


def _turn(n, captain=None, **over):
    tid = f"{n:03d}"
    arm = lambda name, rate: {"outcome": "success", "minutes": 12, "cost_usd": 0.42 if name == "haiku" else None, "peak_context": 88000,
                              "compactions": 0, "commits": 2, "deploy": "success", "live": True,
                              "grade": {"status": "fail", "checks": {"runs": 100, "finishable": rate}},
                              "shots": [f"{tid}/{name}/shots/Prophesy-start.png"]}
    t = {"id": tid, "n": n, "kind": "turn", "prompt": f"Prompt number {n}", "finished_at": "2026-10-25T01:00:00Z",
         "arms": {"haiku": arm("haiku", 98.4), "purple": arm("purple", 61)}, "captain": captain}
    t.update(over)
    return t


class FakeStore:
    """One folder of the bucket: a dict of path -> bytes."""

    def __init__(self, files):
        self.files = dict(files)
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)


class Bucket:
    """The portal's bucket as prophesy.py uses it (get, put, delete, list on prophesy/<folder>/<path>), over one FakeStore
    per folder, so each test can look at a folder's files directly."""

    def __init__(self, stores):
        self.stores = stores

    def _split(self, key):
        assert key.startswith(prophesy.PREFIX), key
        folder, _, path = key[len(prophesy.PREFIX):].partition("/")
        return folder, path

    def get(self, key):
        folder, path = self._split(key)
        return self.stores[folder].read(path) if folder in self.stores else None

    def put(self, key, data, content_type):
        folder, path = self._split(key)
        self.stores.setdefault(folder, FakeStore({})).files[path] = bytes(data)

    def delete(self, key):
        folder, path = self._split(key)
        self.stores[folder].files.pop(path, None)

    def list(self, prefix):
        folder = prefix[len(prophesy.PREFIX):].rstrip("/")
        return [f"{prophesy.PREFIX}{folder}/{p}" for p in self.stores.get(folder, FakeStore({})).files]


@pytest.fixture(autouse=True)
def _fresh_cache():
    prophesy.reset_cache()
    yield
    prophesy.reset_cache()


@pytest.fixture
def stores(monkeypatch):
    s = {
        "haiku": FakeStore({"index.html": GAME_HTML, "src/main.js": b"export {};\n", "version.json": _version("aaaa111bbbb222")}),
        "purple": FakeStore({"index.html": b"<html><body>purple</body></html>", "version.json": _version("cccc333dddd444")}),
        "turns": FakeStore({"index.json": _index([_turn(1, {"note": "close", "haiku": {"score": 7, "note": ""}, "purple": {"score": 5, "note": "no doors"}}),
                                                   _turn(2)]),
                            "001/haiku/shots/Prophesy-start.png": PNG, "001/haiku/reply.md": b"Built the generator.\n"}),
    }
    shim = Bucket(s)
    monkeypatch.setattr(prophesy, "bucket", lambda: shim)
    return s


def _client():
    return portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy()).test_client()


@pytest.fixture
def client(stores):
    return _client()


# ── who gets in ────────────────────────────────────────────────────────────────

def test_signed_out_gets_sign_in_not_the_games_or_the_record(client):
    for path in ("/shifting-prophesy/", "/shifting-prophesy/haiku/", "/shifting-prophesy/haiku/index.html",
                 "/shifting-prophesy/purple/version.json", "/shifting-prophesy/turns/001/haiku/shots/Prophesy-start.png"):
        r = client.get(path)
        assert r.status_code == 302, path
        assert GAME_HTML not in r.data and PNG not in r.data


def test_every_signed_in_person_can_watch_including_a_friend_with_one_game(client):
    for who in (ADMIN, FRIEND):
        login(client, who)
        assert client.get("/shifting-prophesy/").status_code == 200
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert client.get("/shifting-prophesy/haiku/").status_code == 200
    assert client.get("/shifting-prophesy/turns/001/haiku/shots/Prophesy-start.png").data == PNG


def test_each_builds_version_is_public_and_shows_only_the_public_fields(client):
    body = client.get("/shifting-prophesy/haiku/version").get_json()
    assert body["bundled"] is True and body["commit"] == "aaaa111bbbb222" and "secret" not in body
    assert client.get("/shifting-prophesy/purple/version").get_json()["commit"] == "cccc333dddd444"
    assert client.get("/shifting-prophesy/haiku/version").headers["Cache-Control"] == "no-store"


def test_only_the_two_arms_exist(client):
    login(client, FRIEND)
    for path in ("/shifting-prophesy/opus/", "/shifting-prophesy/opus/index.html", "/shifting-prophesy/opus/version", "/shifting-prophesy/turns/"):
        assert client.get(path).status_code == 404, path


# ── the builds ─────────────────────────────────────────────────────────────────

def test_a_build_is_framed_under_the_app_bar_with_its_query_string_and_never_edited(client):
    login(client, FRIEND)
    page = client.get("/shifting-prophesy/haiku/?seed=Prophesy&screen=7,7").data.decode()
    assert 'id="game" src="index.html?seed=Prophesy&amp;screen=7,7"' in page
    assert "<b>Haiku 5.5</b>" in page and 'id="sp-badge">aaaa111</span>' in page
    assert 'href="/shifting-prophesy/"' in page and 'data-app="play"' in page
    assert client.get("/shifting-prophesy/haiku/index.html").data == GAME_HTML          # the build's own page, byte for byte


def test_a_query_string_cannot_break_out_of_the_frame(client):
    login(client, FRIEND)
    page = client.get('/shifting-prophesy/haiku/?seed="><script>alert(1)</script>').data.decode()
    assert "<script>alert(1)" not in page


def test_the_badge_text_comes_from_a_file_so_it_is_checked(stores, client):
    stores["haiku"].files["version.json"] = json.dumps({"build": "1-x", "commit": "<script>alert(1)</script>"}).encode()
    login(client, FRIEND)
    page = client.get("/shifting-prophesy/haiku/").data.decode()
    assert "<script>alert(1)" not in page and 'id="sp-badge">build</span>' in page


def test_each_file_goes_out_with_the_type_the_browser_needs_and_is_revalidated(client):
    login(client, FRIEND)
    js = client.get("/shifting-prophesy/haiku/src/main.js")
    assert js.mimetype == "text/javascript" and js.headers["Cache-Control"] == "no-cache"
    again = client.get("/shifting-prophesy/haiku/src/main.js", headers={"If-None-Match": js.headers["ETag"]})
    assert again.status_code == 304
    assert client.get("/shifting-prophesy/turns/001/haiku/shots/Prophesy-start.png").mimetype == "image/png"
    reply = client.get("/shifting-prophesy/turns/001/haiku/reply.md")
    assert reply.mimetype == "text/plain" and reply.data == b"Built the generator.\n"


@pytest.mark.parametrize("path", ["haiku/../purple/index.html", "haiku/%2e%2e/turns/index.json", "turns/..%2fhaiku/index.html",
                                  "haiku/src//main.js", "haiku/src\\main.js"])
def test_nothing_outside_a_folder_can_be_read(client, stores, path):
    login(client, FRIEND)
    for s in stores.values():
        s.reads.clear()
    assert client.get("/shifting-prophesy/" + path).status_code in (301, 308, 404), path
    for s in stores.values():
        assert not [p for p in s.reads if ".." in p or p.startswith("/") or "//" in p or "\\" in p]


def test_an_arm_with_no_build_says_so(stores, client):
    stores["purple"].files.clear()
    login(client, FRIEND)
    r = client.get("/shifting-prophesy/purple/")
    assert r.status_code == 503 and b"no Purple Skippy build" in r.data
    assert client.get("/shifting-prophesy/purple/version").get_json() == {"bundled": False}


# ── the board ──────────────────────────────────────────────────────────────────

def test_the_board_is_one_page_per_turn_oldest_to_newest_with_both_arms_and_the_scores(client):
    login(client, FRIEND)
    page = client.get("/shifting-prophesy/").data.decode()
    assert page.index("Turn 1") < page.index("Turn 2")                                     # pages are added at the end
    assert page.count('class="sppage"') == 3                                               # the overview, and a page per turn
    assert 'id="sp-turn-1"' in page and 'id="sp-turn-2"' in page and "pager.go(" in page and "pager.refresh()" in page and "hashchange" in page
    assert r'.replace(/["\\]/g, "")' in page                                              # the link's label is cleaned with a valid pattern
    assert "Prompt number 1" in page and "no doors" in page and "7/10" in page and "5/10" in page
    assert "$0.42" in page and "free" in page                                             # Haiku is billed, purple is not
    assert 'href="/shifting-prophesy/haiku/"' in page and 'href="/shifting-prophesy/purple/"' in page
    assert 'src="/shifting-prophesy/turns/001/haiku/shots/Prophesy-start.png"' in page
    assert "7</span> points over 1 scored turn, 1 won" in page                           # Haiku's running total


def _attempt(label, **over):
    n = label.split(":")
    folder = over.pop("folder", "T08/purple" if label.startswith("purple:1:8") else f"001/{n[0]}")
    a = {"label": label, "model": n[0], "kind": "trial" if folder.startswith("T") else "turn", "folder": folder, "note": "", "outcome": "success",
         "minutes": 74, "checks": {"runs": 100, "schema": 6.7, "finishable": 0}, "profile": {"calls": 349, "reads": 103}, "shots": [],
         "reply": f"{folder}/reply.md", "actions": f"{folder}/actions.txt", "report": f"{folder.split('/')[0]}/report.md",
         "has": {"reply": True, "actions": False}}
    a.update(over)
    return a


def test_a_turns_page_has_every_attempt_as_a_section_that_opens_by_itself(stores, client):
    ref = _attempt("haiku:1:1", benchmark=True, profile={"calls": 62, "reads": 10}, checks={k: 100 for k in prophesy.CHECKS})
    trial = _attempt("purple:1:8", note="SDK engine; no time limit", segments=3, compactions=1, summary_chars=[11015, 158],
                     peak_context=101688, harness={"engine": "sdk", "sdk": "0.2.174", "core_model": "Qwen3.6-35B-A3B-Q8_0.gguf", "core_context": 131072,
                                                   "sampling": {"temperature": 0.6, "top_k": 20}},
                     parity={"ok": False, "floor": False, "short": ["referee checks", "rooms per dungeon"],
                             "size": [{"measure": "rooms per dungeon", "bench_min": 9, "got": 5, "ok": False}]},
                     shots=["T08/purple/shots/Prophesy-start.png"])
    stores["turns"].files["index.json"] = _index([_turn(1, attempts=[ref, trial, _attempt("purple:1:2")])])
    login(client, FRIEND)
    page = client.get("/shifting-prophesy/").data.decode()
    assert page.count('<details class="attempt" name="sp-t001"') == 3                      # one group: one open at a time
    assert page.index("haiku:1:1") < page.index("purple:1:8") and page.index("purple:1:8") < page.index("purple:1:2")   # as the referee ordered them
    assert '<span class="tag">reference</span>' in page and "parity short" in page
    assert "SDK engine; no time limit" in page and "Agent SDK 0.2.174" in page and "Qwen3.6-35B-A3B-Q8_0.gguf" in page
    assert "temperature 0.6, top_k 20" in page and "3 segments" in page and "summaries of 11,015 / 158 characters" in page
    assert "Short on: referee checks, rooms per dungeon." in page and "<td>rooms per dungeon</td>" in page
    assert 'src="/shifting-prophesy/turns/T08/purple/shots/Prophesy-start.png"' in page
    assert 'href="/shifting-prophesy/turns/T08/purple/reply.md"' in page and "T08/purple/actions.txt" not in page    # no tool-call file was uploaded
    assert "<td>reads</td><td class=\"n\">103</td><td class=\"n\">10</td>" in page                   # beside the reference's own numbers


def test_an_attempt_is_checked_on_the_way_in(stores, client):
    good = _attempt("purple:1:8", note="<script>alert(1)</script>", shots=["T08/purple/shots/a.png", "../../etc/passwd", "001/haiku/shots/b.png",
                                                                          "T08/purple/shots/c.js"])
    bad = [_attempt("purple:1:9", folder="../x/purple"), _attempt("nobody:1:1"), _attempt("purple:1:2", kind="smoke"), {"label": "purple:1:3"}, "junk",
           _attempt("purple:one:two")]
    stores["turns"].files["index.json"] = _index([_turn(1, attempts=[good, *bad])])
    login(client, FRIEND)
    page = client.get("/shifting-prophesy/").data.decode()
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page
    assert "etc/passwd" not in page and "001/haiku/shots/b.png" not in page and "c.js" not in page and "T08/purple/shots/a.png" in page
    assert page.count('<details class="attempt"') == 1                                      # only the good one
    assert [a["label"] for a in prophesy.turns()[0]["attempts"]] == ["purple:1:8"]


def test_a_row_from_before_attempts_is_one_attempt_per_arm(stores, client):
    login(client, FRIEND)
    page = client.get("/shifting-prophesy/").data.decode()
    row = [r for r in prophesy.turns() if r["id"] == "002"][0]
    assert [a["label"] for a in row["attempts"]] == ["purple:2:1", "haiku:2:1"]
    assert 'data-label="haiku:2:1"' in page and 'data-label="purple:2:1"' in page


def test_the_references_numbers_come_from_the_benchmark_attempt(stores):
    ref = _attempt("haiku:1:1", benchmark=True, profile={"calls": 62})
    other = _attempt("haiku:1:2", profile={"calls": 99})
    stores["turns"].files["index.json"] = _index([_turn(1, attempts=[other, ref])])
    assert prophesy.turns()[0]["reference"] == {"calls": 62}


def test_the_record_is_checked_on_the_way_in(stores, client):
    bad = _turn(3, prompt="<script>alert(1)</script>")
    bad["arms"]["haiku"]["shots"] = ["../../etc/passwd", "003/purple/shots/x.png", "003/haiku/shots/ok.png", "003/haiku/shots/a.js"]
    stores["turns"].files["index.json"] = _index([bad, {"id": "../x", "kind": "turn"}, {"id": "004", "kind": "smoke"}, "junk"])
    login(client, FRIEND)
    page = client.get("/shifting-prophesy/").data.decode()
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page                     # the prompt is escaped
    assert "etc/passwd" not in page and "003/purple/shots/x.png" not in page and "a.js" not in page
    assert "003/haiku/shots/ok.png" in page
    rows = prophesy.turns()
    assert [r["id"] for r in rows] == ["003"]                                              # bad ids and smoke tests are dropped


def test_a_damaged_index_means_no_turns_not_an_error(stores, client):
    stores["turns"].files["index.json"] = b"not json {"
    login(client, FRIEND)
    r = client.get("/shifting-prophesy/")
    assert r.status_code == 200 and b"No turns yet" in r.data


def test_a_portal_with_no_bucket_shows_an_empty_board(monkeypatch):
    monkeypatch.delenv(prophesy.tabletop.BUCKET_ENV, raising=False)
    c = _client()
    login(c, FRIEND)
    page = c.get("/shifting-prophesy/").data.decode()
    assert "No turns yet" in page and page.count("No build yet.") == 2
    assert c.get("/shifting-prophesy/haiku/").status_code == 503


def test_totals_count_only_fully_scored_turns_and_ties_win_nothing():
    rows = [{"captain": {"haiku": {"score": 6}, "purple": {"score": 6}}}, {"captain": {"haiku": {"score": 8}, "purple": {"score": 3}}},
            {"captain": {"haiku": {"score": 9}}}, {"captain": None}]
    t = prophesy.totals(rows)
    assert t["haiku"] == {"points": 14.0, "scored": 2, "wins": 1}
    assert t["purple"] == {"points": 9.0, "scored": 2, "wins": 0}


def test_the_browser_games_card_links_to_the_board(client):
    login(client, FRIEND)
    assert 'href="/shifting-prophesy/"' in client.get("/play").data.decode()


# ── the runner's uploads (skippy-pc, the home token) ───────────────────────────

def _token(client):
    tok = _pair(client)
    client.get("/auth/logout")
    return {"Authorization": f"Bearer {tok}"}


def test_only_the_home_token_can_upload(client, stores):
    for headers in ({}, {"Authorization": "Bearer nope"}):
        assert client.put("/home/prophesy/haiku/index.html", data=b"x", headers=headers).status_code == 401
        assert client.post("/home/prophesy/haiku/finish", json={"keep": []}, headers=headers).status_code == 401
    login(client, ADMIN)                                                           # not even the Captain's cookie
    assert client.put("/home/prophesy/haiku/index.html", data=b"x").status_code == 401
    assert stores["haiku"].files["index.html"] == GAME_HTML


def test_a_new_build_replaces_the_old_one_and_shows_without_a_redeploy(client, stores):
    auth = _token(client)
    assert client.put("/home/prophesy/haiku/index.html", data=b"<html>new</html>", headers=auth).status_code == 200
    assert client.put("/home/prophesy/haiku/version.json", data=_version("eeee555ffff666"), headers=auth).status_code == 200
    r = client.post("/home/prophesy/haiku/finish", json={"keep": ["index.html", "version.json"]}, headers=auth)
    assert r.get_json() == {"ok": True, "deleted": 1}                               # the old build's src/main.js
    assert set(stores["haiku"].files) == {"index.html", "version.json"}
    assert client.get("/shifting-prophesy/haiku/version").get_json()["commit"] == "eeee555ffff666"


def test_the_record_grows_and_a_new_turn_shows_on_the_board(client, stores):
    auth = _token(client)
    assert client.put("/home/prophesy/turns/003/purple/reply.md", data=b"Fixed it.\n", headers=auth).status_code == 200
    assert client.put("/home/prophesy/turns/index.json", data=_index([_turn(3)]), headers=auth).status_code == 200
    assert client.post("/home/prophesy/turns/finish", json={"keep": []}, headers=auth).status_code == 404   # the record is never pruned
    assert "001/haiku/reply.md" in stores["turns"].files
    login(client, FRIEND)
    assert "Turn 3" in client.get("/shifting-prophesy/").data.decode()


@pytest.mark.parametrize("url", ["/home/prophesy/opus/index.html", "/home/prophesy/haiku/a//b.js", "/home/prophesy/haiku/a\\b.js",
                                 "/home/prophesy/haiku/%2e%2e/purple/index.html", "/home/prophesy/haiku/./x.js"])
def test_uploads_stay_inside_the_experiments_folders(client, stores, url):
    auth = _token(client)
    assert client.put(url, data=b"x", headers=auth).status_code in (308, 404)
    assert not [f for s in stores.values() for f in s.files if ".." in f or f.startswith("/") or "//" in f or "\\" in f]


def test_an_upload_too_big_is_refused(client, stores, monkeypatch):
    auth = _token(client)
    monkeypatch.setattr(prophesy, "MAX_FILE", 10)
    assert client.put("/home/prophesy/haiku/big.bin", data=b"x" * 11, headers=auth).status_code == 413
    assert "big.bin" not in stores["haiku"].files


def test_no_bucket_means_503_not_a_lost_upload(client, monkeypatch):
    auth = _token(client)
    monkeypatch.setattr(prophesy, "bucket", lambda: None)
    r = client.put("/home/prophesy/haiku/index.html", data=b"x", headers=auth)
    assert r.status_code == 503 and "TABLETOP_BUCKET" in r.get_json()["error"]
