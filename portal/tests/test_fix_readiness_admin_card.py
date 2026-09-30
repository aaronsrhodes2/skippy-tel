"""The Server admin card's 60-second poll must never swap the card for something that isn't the card.

The route sits behind the portal's sign-in, and a signed-out request is answered with a redirect to the landing page.
`fetch` follows redirects, so the poll got a 200 `text/html`: the whole Sign-in page, which the old script wrote into the
card. These tests run the REAL inline script of the rendered game page in node (a few lines of fake `document`/`fetch`
around it; no jsdom, no browser), so what is pinned is what the page ships, and drive the real route with the test client.

This file also carries the harness that runs a page's inline script, which test_fix_readiness_snapshot.py reuses for
game.html's own poll.
"""
import json
import re
import shutil
import subprocess

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from test_readiness_page import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, boot, login, snapshot  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="needs node to run the page's inline script")

# A fake browser around one inline script: `document`, `fetch`, `setInterval` and `location`, nothing else. The script's
# interval callback is captured, then called once per step with that step's scripted answer to its fetch.
HARNESS = r"""
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const els = {};
for (const [id, init] of Object.entries(input.elements)) els[id] = init === null ? null : {...init};
let reloads = 0, tick = null, step = null;
const calls = [];
const document = {hidden: false, getElementById: (id) => (id in els ? els[id] : null), querySelector: () => null};
const location = {reload: () => { reloads++; }};
const setInterval = (fn) => { tick = fn; return 1; };
const fetch = async (url, opts) => {
  calls.push({url, headers: (opts && opts.headers) || {}});
  if (step.throws) throw new Error("network");
  const h = {};
  for (const [k, v] of Object.entries(step.headers || {})) h[k.toLowerCase()] = v;
  return {ok: step.status >= 200 && step.status < 300, status: step.status, redirected: !!step.redirected,
          type: step.type || "basic", headers: {get: (n) => (n.toLowerCase() in h ? h[n.toLowerCase()] : null)},
          text: async () => (step.body === undefined ? "" : step.body),
          json: async () => JSON.parse(step.body === undefined ? "null" : step.body)};
};
(async () => {
  new Function("document", "fetch", "setInterval", "location", input.script)(document, fetch, setInterval, location);
  const out = [];
  for (const s of input.steps) {
    step = s;
    await tick();
    out.push({reloads, elements: JSON.parse(JSON.stringify(els))});
  }
  console.log(JSON.stringify({calls, out}));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def inline_script(page: str, containing: str) -> str:
    """The one inline <script> of a rendered page whose text contains `containing`."""
    found = [s for s in re.findall(r"<script>(.*?)</script>", page, re.S) if containing in s]
    assert len(found) == 1, f"expected one inline script with {containing!r}, found {len(found)}"
    return found[0]


def run_script(script: str, steps: list[dict], elements: dict) -> dict:
    """Run `script` in node against fake elements, once per step. Returns {calls: [fetches], out: [state after each step]}."""
    done = subprocess.run([NODE, "-e", HARNESS], input=json.dumps({"script": script, "steps": steps, "elements": elements}),
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def env():
    vm = FakeVm()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    return app.test_client(), vm, app


PANEL = "/admin/games/valheim/panel"
FRAGMENT = {"X-Skippy-Fragment": "server-admin", "Content-Type": "text/html; charset=utf-8"}
SIGN_IN_PAGE = ("<!doctype html><html><head><title>Sign in - Skippy Portal</title><style>body{}</style></head>"
                "<body><nav>Skippy</nav><a class=\"btn\" href=\"/login\">Sign in with Google</a></body></html>")


def admin_page(env) -> str:
    client, vm, _ = env
    boot(vm, "valheim", minutes_up=10)
    snapshot(vm, "READY valheim=1")
    login(client, ADMIN)
    page = client.get("/games/valheim").data.decode()
    assert 'id="server-admin-body"' in page
    return page


# ── the script the page ships ─────────────────────────────────────────────────

@needs_node
def test_the_card_keeps_its_last_good_body_and_says_so_when_the_session_is_gone(env):
    page = admin_page(env)
    script = inline_script(page, "server-admin-body")
    steps = [
        {"status": 200, "headers": FRAGMENT, "body": "<p>GOOD 1</p>"},                                     # 0  a normal refresh
        {"status": 500, "headers": {}, "body": "boom"},                                                    # 1  the portal hiccups
        {"throws": True},                                                                                  # 2  a network blip
        {"status": 200, "redirected": True, "headers": {"Content-Type": "text/html"}, "body": SIGN_IN_PAGE},  # 3  session gone, redirect followed
        {"status": 401, "headers": {}, "body": "Signed out"},                                              # 4  session gone, plain 401
        {"status": 403, "headers": {}, "body": "Forbidden"},                                               # 5  no longer an admin
        {"status": 200, "headers": {"Content-Type": "text/html"}, "body": SIGN_IN_PAGE},                   # 6  a page that isn't the fragment
        {"status": 200, "type": "opaqueredirect", "headers": {}, "body": ""},                              # 7  redirect: "manual"
        {"status": 200, "headers": FRAGMENT, "body": "<p>GOOD 2</p>"},                                     # 8  signed back in elsewhere
    ]
    ran = run_script(script, steps, {"server-admin-body": {"innerHTML": "<p>GOOD 0</p>"},
                                     "server-admin-note": {"hidden": True}})
    body = [s["elements"]["server-admin-body"]["innerHTML"] for s in ran["out"]]
    note_hidden = [s["elements"]["server-admin-note"]["hidden"] for s in ran["out"]]
    assert body == ["<p>GOOD 1</p>", "<p>GOOD 1</p>", "<p>GOOD 1</p>", "<p>GOOD 1</p>", "<p>GOOD 1</p>", "<p>GOOD 1</p>",
                    "<p>GOOD 1</p>", "<p>GOOD 1</p>", "<p>GOOD 2</p>"], "only the real fragment may replace the card"
    # a hiccup (500, network) is not "signed out": no note. Everything that says the session is gone shows it, until a good answer.
    assert note_hidden == [True, True, True, False, False, False, False, False, True]
    assert all(s["reloads"] == 0 for s in ran["out"]), "the card never reloads the page"


@needs_node
def test_the_poll_says_it_is_a_poll_so_the_server_can_answer_401_and_targets_the_fragment_route(env):
    page = admin_page(env)
    ran = run_script(inline_script(page, "server-admin-body"), [{"status": 200, "headers": FRAGMENT, "body": "x"}],
                     {"server-admin-body": {"innerHTML": ""}, "server-admin-note": {"hidden": True}})
    (call,) = ran["calls"]
    assert call["url"] == PANEL
    assert call["headers"].get("X-Requested-With") == "fetch"


def test_the_page_has_the_note_hidden_outside_the_part_the_poll_replaces(env):
    page = admin_page(env)
    assert re.search(r'id="server-admin-note"[^>]*\bhidden\b', page), "the note starts hidden"
    # it sits above the body, not inside it: the poll rewrites everything inside the body
    assert page.index('id="server-admin-note"') < page.index('id="server-admin-body"')
    assert "Reload" in page[page.index('id="server-admin-note"'):][:400]


# ── the route the poll calls ──────────────────────────────────────────────────

def test_the_fragment_carries_a_marker_only_the_fragment_has(env):
    client, vm, _ = env
    boot(vm, "valheim", minutes_up=10)
    snapshot(vm, "READY valheim=1")
    login(client, ADMIN)
    resp = client.get(PANEL, headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200 and resp.headers["X-Skippy-Fragment"] == "server-admin"
    assert client.get("/games/valheim").headers.get("X-Skippy-Fragment") is None      # the full page is not the fragment
    assert client.get("/me").headers.get("X-Skippy-Fragment") is None


def test_a_signed_out_poll_gets_a_401_not_the_sign_in_page(env):
    client, vm, _ = env
    boot(vm, "valheim", minutes_up=10)
    poll = {"X-Requested-With": "fetch"}
    resp = client.get(PANEL, headers=poll)                                    # never signed in (or the session is gone)
    assert resp.status_code == 401 and "Sign in" not in resp.data.decode() and "X-Skippy-Fragment" not in resp.headers
    assert resp.headers["Cache-Control"] == "no-store"
    with client.session_transaction() as s:                                   # signed in with Google, but not on the list
        s["visitor"] = {"email": "stranger@example.com", "name": "S"}
    assert client.get(PANEL, headers=poll).status_code == 401
    login(client, "removed@example.com")                                      # was on the list, is not any more
    assert client.get(PANEL, headers=poll).status_code == 401


def test_the_navigations_and_the_friends_answers_do_not_change(env):
    client, vm, _ = env
    boot(vm, "valheim", minutes_up=10)
    resp = client.get(PANEL)                                                  # a plain visit while signed out: the sign-in redirect
    assert resp.status_code == 302 and "Server admin" not in resp.data.decode()
    login(client, FRIEND)
    assert client.get(PANEL, headers={"X-Requested-With": "fetch"}).status_code == 403   # signed in but not the Captain
    assert client.get(PANEL).status_code == 403
    login(client, ADMIN)
    assert client.get("/admin/games/nope/panel", headers={"X-Requested-With": "fetch"}).status_code == 404
