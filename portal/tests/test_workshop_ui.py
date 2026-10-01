"""The private Skippy's card on /me must never dead-end.

The incident: the Captain's self-deploying demo worked once, then a redeploy mid-task left the card saying "went quiet"
with Send greyed out. The page only set a note and kept polling; Send was never released, and the only way out was New
chat. This is the Captain's only way in when he is away from the PC, so the card now (a) says what is going on and for
how long, (b) asks skippy-pc to check again by itself and on a Check again button, (c) brings Send back after 30 s of
quiet, (d) never gives up polling, only slows down.

Two layers. The first reads the rendered page (no JS runtime). The second runs the page's REAL inline script in node
against a fake browser (fake DOM, fake fetch, fake clock: seconds of virtual time cost milliseconds), the same approach as
test_fix_readiness_admin_card.py, and is skipped where node is missing (the CI runners have it). What it pins is what
the page ships.
"""
import json
import re
import shutil
import subprocess

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="needs node to run the page's inline script")
BUSY = "Skippy is still working on the last one."


@pytest.fixture
def env():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    app.extensions["home"].accept({"capabilities": {"workshop": {"ready": True, "model": "claude-opus-5-5"}}})
    return app.test_client(), app


@pytest.fixture
def page(env):
    client, _ = env
    login(client, ADMIN)
    return client.get("/me").data.decode()


def card(page: str) -> str:
    """The private Skippy's own markup: from its opening tag to the end of its script."""
    start = page.index('<div class="w me-chat" id="private-skippy"')
    return page[start:page.index("</script>", start)]


def script(page: str) -> str:
    found = [s for s in re.findall(r"<script>(.*?)</script>", page, re.S) if "ps-quiet" in s]
    assert len(found) == 1, f"expected one inline script for the card, found {len(found)}"
    return found[0]


# ── what the page ships (no JS runtime) ───────────────────────────────────────────────────────────────

def test_the_card_carries_the_resync_url_the_check_again_button_and_the_notes(page):
    assert 'RESYNC = "/me/workshop/resync"' in page                           # url_for('me.workshop_resync'), rendered
    assert re.search(r'<button type="button" id="ps-check">Check again</button>', page)
    js = script(page)
    for phrase in ("No word from skippy-pc for ", "It reconnects by itself; this can take a minute after a deploy.",
                   "hasn't picked this up for ", "Can't reach the portal (", "Your sign-in to the portal ended.",
                   "(left waiting)", "You can send a new message now; this one will be left waiting."):
        assert phrase in page, phrase
    assert "Reload page" in page and 'id="ps-reload"' in page                  # a way out when only a new sign-in helps
    assert "aria-live" in page and "role=\"status\"" in page


def test_the_card_never_gives_up_polling(page):
    js = script(page)
    assert "quiet > 90" not in page and "Lost the link" not in page            # the old give-up at 90 quiet polls
    assert not re.search(r"quiet\w*\s*>\s*\d+\s*\)\s*break", js)
    # backing off, not stopping: 1.2 s healthy, then 2 s / 5 s / 10 s by how long it has been quiet
    assert "POLL_MS = 1200" in js and "quietMs < 60000 ? 2000 : quietMs < 300000 ? 5000 : 10000" in js
    # Send comes back after 30 s of quiet; an automatic "check again" is at most once a minute
    assert "LEAVE_AFTER_MS = 30000" in js and "RESYNC_EVERY_MS = 60000" in js
    assert "TIMEOUT_MS" in js and "AbortController" in js                      # one hung request can't freeze the card


def test_every_element_the_script_looks_up_exists_in_the_markup(page):
    js, html = script(page), card(page)
    ids = set(re.findall(r'root\.querySelector\("#([\w-]+)"\)', js))
    assert {"ps-log", "ps-form", "ps-text", "ps-send", "ps-stop", "ps-new", "ps-quiet", "ps-quiet-msg", "ps-quiet-hint",
            "ps-check", "ps-reload"} <= ids
    for ident in ids:
        assert f'id="{ident}"' in html, ident
    assert "{{" not in html and "{%" not in html                                  # nothing left unrendered


def test_the_quiet_strip_sits_between_the_log_and_the_form_not_inside_the_live_log(page):
    html = card(page)
    log, quiet, form = html.index('id="ps-log"'), html.index('id="ps-quiet"'), html.index('id="ps-form"')
    assert log < quiet < form
    assert re.search(r'id="ps-quiet"[^>]*\bhidden\b', html)                       # hidden until the link goes quiet
    assert re.search(r'id="ps-stop" hidden', html) and re.search(r'id="ps-reload" hidden', html)
    assert html[log:quiet].count("</div>") == 1                                   # the log div closes before the strip opens


def test_it_keeps_the_phone_layout_and_the_existing_look(page):
    css = next(s for s in re.findall(r"<style>(.*?)</style>", page, re.S) if ".me-chat" in s)
    assert "@media (max-width: 900px)" in css and "flex-wrap: wrap" in css          # the buttons wrap instead of overflowing
    used = set(re.findall(r"var\((--[\w-]+)\)", css))
    defined = set(re.findall(r"(--[\w-]+)\s*:", page))                               # base.html's palette
    assert used and used <= defined, used - defined                                  # existing variables only
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", re.search(r"\.me-chat \.quiet.*?\n", css).group(0))   # the new strip uses none of its own


# ── the page's real script, in node ────────────────────────────────────────────────────────────────────

HARNESS = r"""
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const START = 1000000;
let now = START;
const timers = []; let timerId = 0;
const fakeSetTimeout = (fn, ms) => { const t = {id: ++timerId, at: now + (ms || 0), fn, every: 0}; timers.push(t); return t.id; };
const fakeClearTimeout = (id) => { const t = timers.find(x => x.id === id); if (t) t.dead = true; };
const fakeSetInterval = (fn, ms) => { const t = {id: ++timerId, at: now + ms, fn, every: ms}; timers.push(t); return t.id; };
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(r => setImmediate(r)); };
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    let next = null;
    for (const t of timers) if (!t.dead && t.at <= end && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) next = t;
    if (!next) break;
    now = Math.max(now, next.at);
    if (next.every) next.at += next.every; else next.dead = true;
    next.fn();
    await settle();
  }
  now = end;
  await settle();
}
class FakeDate extends Date { static now() { return now; } }

class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.own = ""; this.className = ""; this.hidden = false; this.disabled = false;
    this.value = ""; this.on = {}; this.cls = new Set(); this.scrollTop = 0; this.scrollHeight = 0; this.focused = 0;
    const self = this;
    this.classList = {toggle(c, on) { if (on) self.cls.add(c); else self.cls.delete(c); }, contains: (c) => self.cls.has(c)};
  }
  get textContent() { return this.children.length ? this.children.map(c => c.textContent).join(" | ") : this.own; }
  set textContent(t) { this.children = []; this.own = String(t); }
  appendChild(c) { this.children.push(c); return c; }
  replaceChildren(...cs) { this.children = cs; this.own = ""; }
  addEventListener(ev, fn) { (this.on[ev] = this.on[ev] || []).push(fn); }
  focus() { this.focused++; }
  fire(ev, extra) { return Promise.all((this.on[ev] || []).map(fn => fn(Object.assign({preventDefault() {}, shiftKey: false}, extra)))); }
  requestSubmit() { return this.fire("submit"); }
}
const els = {};
for (const id of ["ps-log", "ps-form", "ps-text", "ps-send", "ps-stop", "ps-new", "ps-quiet", "ps-quiet-msg", "ps-quiet-hint", "ps-check", "ps-reload"]) els[id] = new El("x");
for (const id of ["ps-stop", "ps-quiet", "ps-quiet-hint", "ps-reload"]) els[id].hidden = true;
const root = {querySelector(sel) { const e = els[sel.slice(1)]; if (!e) throw new Error("the script looked up " + sel + ", which the markup lacks"); return e; }};
const document = {getElementById: (id) => { if (id !== "private-skippy") throw new Error("unexpected id " + id); return root; },
                  createElement: (tag) => new El(tag)};
const storage = new Map(Object.entries(input.storage || {}));
const localStorage = {getItem: (k) => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => { storage.set(k, String(v)); },
                      removeItem: (k) => { storage.delete(k); }};
const crypto = {randomUUID: () => "11111111-2222-3333-4444-555555555555"};
let confirmAnswer = true, reloads = 0;
const confirm = () => confirmAnswer;
const location = {reload: () => { reloads++; }};
class FakeAbort { constructor() { this.signal = {aborted: false, onabort: null}; } abort() { this.signal.aborted = true; if (this.signal.onabort) this.signal.onabort(); } }

// The portal, as the page sees it: /me/workshop/{events,resync,send,stop}.
const tasks = JSON.parse(JSON.stringify(input.tasks || {}));
let net = "ok", resyncMode = "ok", sendNext = null, made = 0, latency = 0;
const calls = [];
const later = (v) => (latency ? new Promise(res => fakeSetTimeout(() => res(v), latency)) : Promise.resolve(v));   // a slow portal
const reply = (status, body, ctype, redirected) => ({
  ok: status >= 200 && status < 300, status, redirected: !!redirected,
  headers: {get: (n) => (n.toLowerCase() === "content-type" ? (ctype || "application/json") : null)}, json: async () => body});
function fakeFetch(url, opts) {
  opts = opts || {};
  const u = new URL(url, "http://portal.test");
  const route = u.pathname.replace("/me/workshop/", "");
  const rec = {t: now - START, route, method: opts.method || "GET"};
  calls.push(rec);
  if (route === "events") {
    const task = u.searchParams.get("task"), after = Number(u.searchParams.get("after"));
    rec.task = task; rec.after = after;
    if (net === "throw") return Promise.reject(new Error("network down"));
    if (net === "hang") return new Promise((_, reject) => { if (opts.signal) opts.signal.onabort = () => reject(new Error("aborted")); });
    if (net === "html") return Promise.resolve(reply(502, "<html>bad gateway</html>", "text/html"));
    if (net === "redirect") return Promise.resolve(reply(200, "<html>sign in</html>", "text/html", true));
    if (net === "forbidden") return Promise.resolve(reply(403, {error: "no"}));
    const t = tasks[task];                                          // the answer is decided now and arrives `latency` later
    if (!t) return later(reply(200, {state: "unknown", events: [], next: after, error: null}));
    if (t.raw !== undefined) return later(reply(200, t.raw));
    const evs = t.events || [];
    const body = {state: t.state, error: t.error || null, events: evs.slice(after), next: evs.length};
    if (t.quiet_s !== undefined && t.quiet_s !== null) body.quiet_s = t.quiet_s;
    return later(reply(200, body));
  }
  if (route === "resync") {
    rec.task = JSON.parse(opts.body).task;
    if (resyncMode === "throw") return Promise.reject(new Error("network down"));
    return Promise.resolve(reply(200, {state: "running", error: null}));
  }
  if (route === "send") {
    rec.message = JSON.parse(opts.body).message;
    if (sendNext) { const s = sendNext; sendNext = null; return Promise.resolve(reply(s.status, s.body)); }
    const id = "new" + (++made);
    tasks[id] = {state: "queued", events: [], error: null};
    return Promise.resolve(reply(200, {task: id}));
  }
  if (route === "stop") { rec.task = JSON.parse(opts.body).task; return Promise.resolve(reply(200, {ok: true})); }
  throw new Error("unexpected fetch " + url);
}

function snap() {
  const e = els;
  return {
    t: now - START, strip: !e["ps-quiet"].hidden, msg: e["ps-quiet-msg"].textContent, hint: !e["ps-quiet-hint"].hidden,
    reload: !e["ps-reload"].hidden, bad: e["ps-quiet"].cls.has("bad"), sendDisabled: e["ps-send"].disabled,
    stopShown: !e["ps-stop"].hidden, checkDisabled: e["ps-check"].disabled, checkText: e["ps-check"].textContent,
    log: e["ps-log"].children.map(b => ({cls: b.className + (b.cls.has("failed") ? " failed" : ""), text: b.textContent})),
    task: storage.has("ps-task") ? JSON.parse(storage.get("ps-task")) : null, focused: e["ps-text"].focused,
    resyncs: calls.filter(c => c.route === "resync").length, text: e["ps-text"].value,
  };
}

(async () => {
  new Function("document", "localStorage", "crypto", "fetch", "setTimeout", "clearTimeout", "setInterval", "confirm",
               "AbortController", "Date", "location", input.script)(
    document, localStorage, crypto, fakeFetch, fakeSetTimeout, fakeClearTimeout, fakeSetInterval, confirm, FakeAbort, FakeDate, location);
  await settle();
  const snaps = {};
  for (const s of input.steps) {
    if ("adv" in s) await advance(s.adv);
    else if ("tasks" in s) { for (const [id, patch] of Object.entries(s.tasks)) { if (patch === null) delete tasks[id]; else tasks[id] = Object.assign(tasks[id] || {events: []}, patch); } await settle(); }
    else if ("net" in s) net = s.net;
    else if ("latency" in s) latency = s.latency;
    else if ("breakDom" in s) El.prototype.replaceChildren = function () { throw new TypeError("this.replaceChildren is not a function"); };   // an old browser
    else if ("resyncMode" in s) resyncMode = s.resyncMode;
    else if ("sendNext" in s) sendNext = s.sendNext;
    else if ("confirm" in s) confirmAnswer = s.confirm;
    else if ("type" in s) els["ps-text"].value = s.type;
    else if ("submit" in s) { await els["ps-form"].requestSubmit(); await settle(); }          // Enter in the box: no button check
    else if ("click" in s) {                                                                    // a real click: not on a disabled or hidden control
      const el = els[s.click];
      if (!el.disabled && !el.hidden) { await (s.click === "ps-send" ? els["ps-form"].requestSubmit() : el.fire("click")); await settle(); }
    }
    else if ("snap" in s) snaps[s.snap] = snap();
    else throw new Error("unknown step " + JSON.stringify(s));
  }
  console.log(JSON.stringify({snaps, calls, reloads, tasks, final: snap()}));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def ev(n: int) -> dict:
    return {"t": "text", "text": f"step {n}"}


def drive(page: str, steps: list, tasks: dict | None = None, storage: dict | None = None, follow: str | None = None) -> dict:
    """Run the card's script against the fake browser. `follow` = a task the page was already following (a reload
    mid-task), with a transcript that has its (empty) assistant turn."""
    store = dict(storage or {})
    if follow:
        store.setdefault("ps-conv", "conv-12345678")
        store.setdefault("ps-log", json.dumps([{"role": "user", "text": "go"}, {"role": "assistant", "parts": []}]))
        store.setdefault("ps-task", json.dumps({"task": follow, "after": 0}))
    # bytes both ways, UTF-8 explicitly: text mode would decode node's output with the console's code page on Windows
    # (the card's "…" came back as mojibake there)
    done = subprocess.run([NODE, "-e", HARNESS], capture_output=True, timeout=120,
                          input=json.dumps({"script": script(page), "steps": steps, "tasks": tasks or {}, "storage": store}).encode())
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))


def polls(out: dict, task: str | None = None) -> list[int]:
    return [c["t"] for c in out["calls"] if c["route"] == "events" and (task is None or c.get("task") == task)]


def resyncs(out: dict) -> list[int]:
    return [c["t"] for c in out["calls"] if c["route"] == "resync"]


S = 1000  # ms


@needs_node
def test_a_stalled_run_shows_what_is_happening_checks_again_by_itself_and_brings_send_back(page):
    out = drive(page, [
        {"adv": 500}, {"snap": "healthy"},
        {"tasks": {"t1": {"state": "stalled", "quiet_s": 125}}},
        {"adv": 2000}, {"snap": "quiet"},
        {"adv": 27 * S}, {"snap": "before_30s"},
        {"adv": 3 * S}, {"snap": "after_30s"},
        {"adv": 30 * S}, {"snap": "t62"},
        {"adv": 55 * S}, {"snap": "t117"},
        {"adv": 10 * S}, {"snap": "t127"},
    ], tasks={"t1": {"state": "running", "events": [ev(1), ev(2)]}}, follow="t1")
    s = out["snaps"]
    assert not s["healthy"]["strip"] and s["healthy"]["sendDisabled"] and s["healthy"]["stopShown"]
    assert s["healthy"]["log"][-1]["text"] == "step 1 | step 2" and s["healthy"]["resyncs"] == 0
    # quiet at once, with the portal's own word on how long ("2 min" from its quiet_s of 125), Send still off
    q = s["quiet"]
    assert q["strip"] and q["msg"] == ("No word from skippy-pc for 2 min. It reconnects by itself; "
                                       "this can take a minute after a deploy.")
    assert q["sendDisabled"] and not q["hint"] and q["checkText"] == "Check again" and not q["checkDisabled"]
    assert q["stopShown"] and not q["reload"] and not q["bad"]
    # one automatic resync on entering the quiet state, then at most one a minute
    assert [s[k]["resyncs"] for k in ("quiet", "before_30s", "after_30s", "t62", "t117", "t127")] == [1, 1, 1, 2, 2, 3]
    gaps = [b - a for a, b in zip(resyncs(out), resyncs(out)[1:])]
    assert gaps and min(gaps) >= 60 * S and resyncs(out)[0] - 1200 < 2 * S
    # Send is held back for 30 s of quiet, then comes back with the hint; Stop and Check again stay
    assert s["before_30s"]["sendDisabled"] and not s["before_30s"]["hint"]
    a = s["after_30s"]
    assert not a["sendDisabled"] and a["hint"] and a["stopShown"] and a["checkText"] == "Check again"
    assert "a minute after a deploy" in a["msg"]


@needs_node
def test_a_task_skippy_pc_never_picked_up_says_so_and_is_just_as_quiet(page):
    out = drive(page, [{"adv": 3 * S}, {"snap": "early"}, {"adv": 30 * S}, {"snap": "later"}],
                tasks={"t1": {"state": "offline"}}, follow="t1")
    early, later = out["snaps"]["early"], out["snaps"]["later"]
    assert early["strip"] and early["msg"].startswith("skippy-pc hasn't picked this up for ")
    assert "Is the bridge running there?" in early["msg"] and "a minute after a deploy" in early["msg"]
    assert early["sendDisabled"] and not later["sendDisabled"] and later["hint"]       # same rules: Send back after 30 s
    assert resyncs(out)[0] < 2 * S


@needs_node
def test_it_polls_every_1_2_s_while_healthy_then_2_5_and_10_s_by_how_long_it_has_been_quiet_and_never_gives_up(page):
    out = drive(page, [
        {"adv": 6 * S},
        {"tasks": {"t1": {"state": "stalled"}}},
        {"adv": 20 * 60 * S}, {"snap": "after_20_min"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    times = polls(out)
    first_quiet = next(t for t in times if t > 6 * S)                       # the first poll that saw "stalled"
    healthy = [b - a for a, b in zip(times, times[1:]) if b <= first_quiet]
    assert set(healthy) == {1200}
    for a, b in zip(times, times[1:]):
        if a < first_quiet:
            continue
        quiet_ms = a - first_quiet
        assert b - a == (2000 if quiet_ms < 60 * S else 5000 if quiet_ms < 300 * S else 10000), (a, b)
    end = out["snaps"]["after_20_min"]
    assert times[-1] > end["t"] - 10 * S - 1                                    # still polling at minute twenty
    assert end["strip"] and re.search(r"for 19 min\.", end["msg"]) and not end["bad"]       # 19 min 58 s
    assert "Lost the link" not in json.dumps(end) and not end["reload"]
    assert not end["sendDisabled"] and end["checkText"] == "Check again"       # and Send is on offer the whole time


@needs_node
def test_check_again_polls_now_and_asks_for_a_resync_and_shows_it_is_working(page):
    out = drive(page, [
        {"tasks": {"t1": {"state": "stalled"}}},
        {"adv": 2500}, {"snap": "quiet"},
        {"click": "ps-check"}, {"snap": "clicked"},
        {"adv": 2500}, {"snap": "later"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    s = out["snaps"]
    # the click at 2500 ms polled at once (the next scheduled poll was 3200) and posted a resync at the same moment
    assert 2500 in polls(out) and 2500 in resyncs(out)
    assert s["clicked"]["checkText"] == "Check again" and not s["clicked"]["checkDisabled"]    # a quick answer: done already
    assert polls(out)[polls(out).index(2500) + 1] == 4500                       # then the normal cadence resumes from there
    assert resyncs(out).count(2500) == 1


@needs_node
def test_check_again_says_checking_while_the_answer_is_pending_and_a_hung_request_times_out(page):
    out = drive(page, [
        {"tasks": {"t1": {"state": "stalled"}}},
        {"adv": 2500}, {"net": "hang"}, {"adv": 1000},                          # the 3200 poll is out now, and never answered
        {"click": "ps-check"}, {"snap": "checking"},
        {"adv": 25 * S}, {"snap": "timed_out"},                                  # 20 s later the page gave up on that request
        {"net": "ok"}, {"adv": 20 * S}, {"snap": "after"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    s = out["snaps"]
    assert s["checking"]["checkText"] == "Checking…" and s["checking"]["checkDisabled"]
    assert s["checking"]["msg"].startswith("No word from skippy-pc")             # the last thing it knew
    assert s["timed_out"]["msg"].startswith("Can't reach the portal (")          # the hung request did not freeze the card
    assert s["after"]["checkText"] == "Check again" and not s["after"]["checkDisabled"]
    assert s["after"]["msg"].startswith("No word from skippy-pc")                # the portal answered again: still stalled


@needs_node
def test_when_the_link_heals_the_card_goes_back_to_normal_and_the_final_answer_ends_it(page):
    final = {"t": "text", "text": "The Music app is built and deployed."}
    out = drive(page, [
        {"tasks": {"t1": {"state": "stalled"}}}, {"adv": 40 * S}, {"snap": "quiet"},
        {"tasks": {"t1": {"state": "running", "events": [ev(1), ev(2)]}}}, {"adv": 3 * S}, {"snap": "healed"},
        {"tasks": {"t1": {"state": "done", "events": [ev(1), ev(2), final]}}}, {"adv": 3 * S}, {"snap": "done"},
        {"adv": 30 * S}, {"snap": "later"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    s = out["snaps"]
    assert s["quiet"]["strip"] and not s["quiet"]["sendDisabled"]
    assert not s["healed"]["strip"] and s["healed"]["sendDisabled"] and s["healed"]["stopShown"]     # busy again, nothing to check
    assert s["healed"]["log"][-1]["text"] == "step 1 | step 2" and s["healed"]["task"] == {"task": "t1", "after": 2}
    d = s["done"]
    assert not d["strip"] and not d["sendDisabled"] and not d["stopShown"] and d["task"] is None   # finished: Send is back, no task to resume
    assert d["log"][-1]["text"] == "step 1 | step 2 | The Music app is built and deployed." and d["focused"] >= 1
    assert s["later"]["t"] - d["t"] == 30 * S and polls(out)[-1] < d["t"]                  # and it stopped polling at the end


@needs_node
def test_a_failed_run_ends_with_its_error_in_red_and_a_stopped_one_without(page):
    for error, red in (("The bridge lost this task.", True), ("stopped", False)):
        out = drive(page, [{"adv": 3 * S}, {"tasks": {"t1": {"state": "error", "error": error, "events": [ev(1)]}}},
                           {"adv": 3 * S}, {"snap": "end"}],
                    tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
        end = out["snaps"]["end"]
        assert end["log"][-1]["text"] == f"step 1 | {error}" and end["log"][-1]["cls"].endswith("failed") is red
        assert not end["sendDisabled"] and end["task"] is None


@needs_node
def test_sending_while_quiet_leaves_the_old_task_and_the_server_decides(page):
    out = drive(page, [
        {"tasks": {"t1": {"state": "stalled"}}}, {"adv": 25 * S},
        {"type": "second"}, {"click": "ps-send"}, {"submit": True}, {"snap": "too_early"},   # 24 s quiet: Send is off, Enter does nothing
        {"adv": 8 * S}, {"snap": "ready"},
        {"click": "ps-send"}, {"adv": 3 * S}, {"snap": "left"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    s = out["snaps"]
    assert s["too_early"]["sendDisabled"] and s["too_early"]["text"] == "second"            # not sent, not lost
    assert s["too_early"]["resyncs"] == 1 and not [c for c in out["calls"] if c["route"] == "send" and c["t"] < 26 * S]
    assert not s["ready"]["sendDisabled"] and s["ready"]["hint"]
    left = s["left"]
    assert [b["text"] for b in left["log"]] == ["go", "step 1 | (left waiting)", "second", "…"]   # the old turn is marked; the new one waits
    assert left["task"] == {"task": "new1", "after": 0}                                  # the page follows the new task now
    assert left["sendDisabled"] and left["stopShown"] and not left["strip"]
    (sent,) = [c for c in out["calls"] if c["route"] == "send"]
    assert sent["message"] == "second"
    assert not [c for c in out["calls"] if c["route"] == "events" and c["task"] == "t1" and c["t"] > sent["t"]]   # t1 is forgotten
    assert [c for c in out["calls"] if c["route"] == "events" and c["task"] == "new1" and c["t"] >= sent["t"]]


@needs_node
def test_an_answer_that_arrives_after_the_page_left_that_task_is_dropped(page):
    # a slow portal: a poll of the old task is still out when Send is pressed, and it comes back with news
    out = drive(page, [
        {"latency": 5 * S}, {"tasks": {"t1": {"state": "stalled"}}},
        {"adv": 30 * S}, {"tasks": {"t1": {"events": [ev(1), ev(2), ev(3)]}}}, {"adv": 8 * S},
        {"snap": "ready"}, {"type": "second"}, {"click": "ps-send"}, {"adv": 15 * S}, {"snap": "after"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    s = out["snaps"]
    assert not s["ready"]["sendDisabled"] and s["ready"]["t"] == 38 * S
    (sent,) = [c for c in out["calls"] if c["route"] == "send"]
    assert [c for c in out["calls"] if c["route"] == "events" and c["task"] == "t1" and c["t"] < sent["t"]][-1]["t"] > sent["t"] - 5 * S   # one was out
    a = s["after"]
    assert a["task"] == {"task": "new1", "after": 0}                          # the old poll's answer did not put t1 back
    assert [b["text"] for b in a["log"]][:3] == ["go", "step 1 | (left waiting)", "second"]     # nor add its news to the old turn


@needs_node
def test_a_bug_inside_a_poll_is_a_quiet_link_not_a_dead_card(page):
    # whatever the page trips over while drawing (here: a browser without replaceChildren), the loop survives it:
    # the card says it can't get through, keeps polling, and Send comes back after 30 s
    out = drive(page, [{"adv": 2 * S}, {"breakDom": True}, {"adv": 40 * S}, {"snap": "s"}],
                tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    s = out["snaps"]["s"]
    assert s["strip"] and s["msg"].startswith("Can't reach the portal (") and not s["sendDisabled"]
    assert len([t for t in polls(out) if t > 10 * S]) >= 10


@needs_node
def test_events_that_are_not_events_are_ignored_and_an_old_transcript_without_parts_still_works(page):
    out = drive(page, [{"adv": 3 * S}, {"snap": "s"}],
                tasks={"t1": {"state": "running", "events": [None, 5, "x", ev(1), [], ev(2)]}}, follow="t1",
                storage={"ps-log": json.dumps([{"role": "user", "text": "go"}, {"role": "assistant"}])})
    s = out["snaps"]["s"]
    assert not s["strip"] and s["log"][-1]["text"] == "step 1 | step 2"


@needs_node
def test_a_busy_answer_to_that_send_is_shown_and_the_old_task_is_watched_again(page):
    out = drive(page, [
        {"tasks": {"t1": {"state": "stalled"}}}, {"adv": 33 * S},
        {"sendNext": {"status": 409, "body": {"error": BUSY}}},
        {"type": "second"}, {"click": "ps-send"}, {"adv": 2 * S}, {"snap": "after"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    a = out["snaps"]["after"]
    assert [b["text"] for b in a["log"]] == ["go", "step 1", "second", BUSY]               # no "(left waiting)": it was taken back
    assert a["log"][-1]["cls"].endswith("failed")
    assert a["task"] == {"task": "t1", "after": 1}
    send_at = next(c["t"] for c in out["calls"] if c["route"] == "send")
    assert [c for c in out["calls"] if c["route"] == "events" and c["task"] == "t1" and c["t"] > send_at]   # watching t1 again
    assert a["strip"] and a["sendDisabled"]                                                   # quiet again: a fresh 30 s before Send is offered


@needs_node
def test_a_busy_bridge_on_the_new_task_also_brings_the_old_one_back(page):
    # The portal had lost the old task, so its own busy check passed; the reporter then got the bridge's 409 and failed the NEW task.
    out = drive(page, [
        {"tasks": {"t1": {"state": "stalled"}}}, {"adv": 33 * S},
        {"type": "second"}, {"click": "ps-send"}, {"adv": 500},
        {"tasks": {"new1": {"state": "error", "error": BUSY}, "t1": {"state": "running", "events": [ev(1), ev(2)]}}},
        {"adv": 3 * S}, {"snap": "after"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    a = out["snaps"]["after"]
    assert [b["text"] for b in a["log"]] == ["go", "step 1 | step 2", "second", BUSY]
    assert a["task"] == {"task": "t1", "after": 2} and a["sendDisabled"] and a["stopShown"]


@needs_node
def test_a_portal_that_lost_the_task_is_quiet_not_dead_and_a_half_rebuilt_record_duplicates_nothing(page):
    out = drive(page, [
        {"adv": 500}, {"tasks": {"t1": None}}, {"adv": 3 * S}, {"snap": "unknown"},
        {"tasks": {"t1": {"state": "running", "events": [ev(1), ev(2)]}}}, {"adv": 5 * S}, {"snap": "partial"},
        {"tasks": {"t1": {"state": "running", "events": [ev(i) for i in range(1, 6)]}}}, {"adv": 5 * S}, {"snap": "full"},
        {"tasks": {"t1": {"state": "done", "events": [ev(i) for i in range(1, 6)]}}}, {"adv": 5 * S}, {"snap": "done"},
    ], tasks={"t1": {"state": "running", "events": [ev(1), ev(2), ev(3)]}}, follow="t1")
    s = out["snaps"]
    assert s["unknown"]["strip"] and s["unknown"]["msg"].startswith("No word from skippy-pc for ") and s["unknown"]["resyncs"] == 1
    assert s["partial"]["log"][-1]["text"] == "step 1 | step 2 | step 3" and not s["partial"]["strip"]   # nothing taken back
    assert s["partial"]["task"] == {"task": "t1", "after": 3}                                          # the position never moves back
    assert s["full"]["log"][-1]["text"] == "step 1 | step 2 | step 3 | step 4 | step 5"
    assert s["done"]["task"] is None and not s["done"]["sendDisabled"]


@needs_node
def test_a_dead_network_says_so_and_the_portal_answering_html_is_not_a_sign_in_problem(page):
    for mode in ("throw", "html", "hang"):
        out = drive(page, [{"adv": 2 * S}, {"net": mode}, {"adv": 60 * S}, {"snap": "q"}],
                    tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
        q = out["snaps"]["q"]
        assert q["strip"] and q["msg"].startswith("Can't reach the portal (") and "Trying again by itself." in q["msg"], (mode, q["msg"])
        assert not q["reload"] and not q["bad"]
        assert not q["sendDisabled"], mode                                       # Send is back after 30 s of this too
        assert q["log"][-1]["text"] == "step 1"                                  # nothing the page had was lost


@needs_node
def test_an_ended_sign_in_offers_a_reload_and_does_not_pretend_a_resend_would_work(page):
    for mode in ("redirect", "forbidden"):
        out = drive(page, [{"adv": 2 * S}, {"net": mode}, {"adv": 90 * S}, {"snap": "q"}, {"click": "ps-reload"}],
                    tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
        q = out["snaps"]["q"]
        assert q["strip"] and q["bad"] and q["reload"] and "Reload this page to sign in again" in q["msg"], mode
        assert q["sendDisabled"] and not q["hint"]                              # a new message could not be sent either
        assert resyncs(out) == [] and out["reloads"] == 1                       # no point asking skippy-pc; the button reloads


@needs_node
def test_a_nonsense_answer_never_strands_the_card(page):
    for raw in ({"state": "running", "events": "nope", "next": "x"}, None, [], {"state": 5}):
        out = drive(page, [{"adv": 10 * S}, {"snap": "s"}],
                    tasks={"t1": {"raw": raw}}, follow="t1")
        assert len(polls(out)) >= 4                                              # still polling, whatever it was told
        assert out["snaps"]["s"]["log"][-1]["text"] == "…", raw                  # and nothing odd was drawn


@needs_node
def test_a_healthy_long_run_never_shows_the_strip_and_polls_steadily(page):
    steps = [{"adv": 60 * S}]
    for n in range(2, 12):
        steps += [{"tasks": {"t1": {"events": [ev(i) for i in range(1, n + 1)]}}}, {"adv": 60 * S}]
    out = drive(page, steps + [{"snap": "end"}], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    end = out["snaps"]["end"]
    assert not end["strip"] and end["sendDisabled"] and end["stopShown"] and resyncs(out) == []
    assert end["log"][-1]["text"] == " | ".join(f"step {i}" for i in range(1, 12))
    assert set(b - a for a, b in zip(polls(out), polls(out)[1:])) == {1200}


@needs_node
def test_stop_stays_on_a_quiet_run_and_goes_once_pressed(page):
    out = drive(page, [
        {"tasks": {"t1": {"state": "stalled"}}}, {"adv": 3 * S}, {"snap": "quiet"},
        {"click": "ps-stop"}, {"adv": 5 * S}, {"snap": "stopped"},
    ], tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    s = out["snaps"]
    assert s["quiet"]["stopShown"] and not s["stopped"]["stopShown"]              # and the 1 s refresh does not bring it back
    assert [c for c in out["calls"] if c["route"] == "stop"][0]["task"] == "t1"
    assert s["stopped"]["log"][-1]["text"] == "step 1 | Stopping…"


@needs_node
def test_a_first_message_follows_to_the_end_and_a_failed_send_does_not_strand_the_card(page):
    final = {"t": "text", "text": "All done."}
    out = drive(page, [
        {"sendNext": {"status": 503, "body": {"error": "The bridge isn't running."}}},
        {"type": "first"}, {"click": "ps-send"}, {"snap": "refused"},
        {"type": "second"}, {"click": "ps-send"}, {"snap": "sent"},
        {"tasks": {"new1": {"state": "running", "events": [ev(1)]}}}, {"adv": 2 * S}, {"snap": "running"},
        {"tasks": {"new1": {"state": "done", "events": [ev(1), final]}}}, {"adv": 2 * S}, {"snap": "done"},
    ])
    s = out["snaps"]
    assert [b["text"] for b in s["refused"]["log"]] == ["first", "The bridge isn't running."]
    assert not s["refused"]["sendDisabled"] and not s["refused"]["stopShown"]        # a refusal is not a stuck card
    assert s["sent"]["sendDisabled"] and s["sent"]["task"] == {"task": "new1", "after": 0}
    assert s["running"]["log"][-1]["text"] == "step 1" and s["running"]["stopShown"]
    assert s["done"]["log"][-1]["text"] == "step 1 | All done." and not s["done"]["sendDisabled"]


@needs_node
def test_new_chat_still_asks_first_when_a_task_is_running_and_then_clears_everything(page):
    steps = [{"adv": 2 * S}, {"confirm": False}, {"click": "ps-new"}, {"snap": "kept"},
             {"confirm": True}, {"click": "ps-new"}, {"snap": "cleared"}]
    out = drive(page, steps, tasks={"t1": {"state": "running", "events": [ev(1)]}}, follow="t1")
    assert out["snaps"]["kept"]["log"][-1]["text"] == "step 1" and out["snaps"]["kept"]["sendDisabled"]
    c = out["snaps"]["cleared"]
    assert c["log"] == [] and c["task"] is None and not c["sendDisabled"] and not c["strip"]


@needs_node
def test_the_task_is_remembered_before_the_first_answer_so_a_reload_still_finds_it(page):
    # the send goes through, but every poll fails: a reload now must still find the task to resume
    out = drive(page, [{"net": "throw"}, {"type": "go"}, {"click": "ps-send"}, {"adv": 3 * S}, {"snap": "s"}])
    assert out["snaps"]["s"]["task"] == {"task": "new1", "after": 0}
