// The code chip on /hud and /drive (static/texts-chip.js) under Node: how a code is read out, what the portal's `fresh` means for the page,
// and the chip itself on a fake page: it appears beside the now-playing line, is said once in hub mode, copies on a tap and stays put away.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const X = require("../../static/texts-chip.js");

const memory = () => ({ dismissed: new Set(), spoken: new Set() });
const NOW = 1_800_000_000;

test("a code is read out one character at a time", () => {
  assert.equal(X.codeSpoken("482913"), "4 8 2 9 1 3");
  assert.equal(X.codeSpoken("G-4829"), "G 4 8 2 9");
  assert.equal(X.codeSpoken(" 12 34 "), "1 2 3 4");
  assert.equal(X.codeSpoken(""), "");
  assert.equal(X.codeSpoken(null), "");
});

test("plan: a code under five minutes old is shown and said once; the portal's own age is preferred to this clock", () => {
  const m = memory();
  const p = X.plan({ code: "482913", at: NOW - 120, age_s: 120 }, NOW, m);
  assert.deepEqual(p, { show: true, speak: true, key: `${NOW - 120}:482913`, code: "482913", left_s: 180 });
  assert.equal(X.plan({ code: "482913", at: NOW - 9000, age_s: 120 }, NOW, m).show, true);          // a clock hours out does not matter
  assert.equal(X.plan({ code: "482913", at: NOW - 120 }, NOW, m).left_s, 180);                       // without age_s: this clock
  assert.equal(X.plan({ code: "482913", at: NOW - 100, age_s: 299 }, NOW, m).left_s, 1);
});

test("plan: old, empty or missing is nothing", () => {
  const m = memory();
  const none = { show: false, speak: false, key: "", code: "", left_s: 0 };
  assert.deepEqual(X.plan({ code: "482913", at: NOW - 400, age_s: 300 }, NOW, m), none);
  assert.deepEqual(X.plan({ code: "482913", at: NOW - 400 }, NOW, m), none);
  assert.deepEqual(X.plan({ code: "", at: NOW }, NOW, m), none);
  assert.deepEqual(X.plan(null, NOW, m), none);
  assert.deepEqual(X.plan(undefined, NOW, m), none);
});

test("plan: a code that was put away stays away, and one already said is not said again", () => {
  const m = memory();
  const fresh = { code: "482913", at: NOW - 10, age_s: 10 };
  const key = X.plan(fresh, NOW, m).key;
  m.spoken.add(key);
  assert.deepEqual([X.plan(fresh, NOW, m).show, X.plan(fresh, NOW, m).speak], [true, false]);
  m.dismissed.add(key);
  assert.deepEqual([X.plan(fresh, NOW, m).show, X.plan(fresh, NOW, m).speak], [false, false]);
  assert.equal(X.plan({ code: "112233", at: NOW - 5, age_s: 5 }, NOW, m).show, true);               // the next one is a new code
});

// ── the chip on a fake page ──
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.style = {}; this.handlers = {}; this.hidden = false; this.textContent = ""; this.innerHTML = ""; this.className = ""; this.id = ""; this.title = ""; this.after = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  append(...kids) { this.children.push(...kids); }
  appendChild(k) { this.children.push(k); return k; }
  removeChild(k) { this.children = this.children.filter(c => c !== k); }
  insertAdjacentElement(where, el) { this.after = el; return el; }
  select() {}
  async click() { for (const fn of this.handlers.click || []) await fn({}); }
}

function page({ hub = false, state, clipboard, voice = true } = {}) {
  const els = { "now-t": new El("span"), drive: new El("section") };
  els.drive.dataset.texts = "/texts/state";
  els.drive.classList = { contains: c => c === "hub" && hub };
  const doc = { hidden: false, head: new El("head"), body: new El("body"), handlers: {}, commands: [],
    getElementById: id => els[id] || null, createElement: tag => new El(tag),
    addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); },
    execCommand(c) { this.commands.push(c); return doc.copyWorks !== false; } };
  const w = { state, voice, asked: [], said: [], timers: [], intervals: [], clock: NOW };
  const opts = {
    doc, url: "/texts/state", now: () => w.clock,
    fetch: async url => { w.asked.push(url); if (w.state instanceof Error) throw w.state; return w.state; },
    speak: text => { if (!w.voice) return false; w.said.push(text); return true; },
    hub: () => hub,
    clipboard: clipboard === undefined ? { copied: [], writeText(t) { this.copied.push(t); return Promise.resolve(); } } : clipboard,
    setTimeout: (fn, ms) => { const t = { fn, ms, off: false }; w.timers.push(t); return t; },
    clearTimeout: t => { if (t) t.off = true; },
    setInterval: (fn, ms) => { const t = { fn, ms }; w.intervals.push(t); return t; },
  };
  return { els, doc, w, opts };
}
const flush = () => new Promise(ok => setImmediate(ok));
const fresh = (code, age = 120, at = NOW - age) => ({ fresh: { code, sender: "22000", at, age_s: age } });

test("the chip is made beside the now-playing line, hidden, counted, with one style and a one-minute poll", async () => {
  const p = page({ state: { fresh: null } });
  const chip = X.attach(p.opts).chip;
  await flush();
  assert.equal(p.els["now-t"].after, chip);
  assert.equal(chip.hidden, true);
  assert.equal(chip.id, "code-chip");
  assert.equal(chip.attrs["data-tel"], "drive:code");
  assert.match(chip.attrs["aria-label"], /Copy the code/);
  assert.equal(p.doc.head.children.length, 1);
  assert.equal(p.w.intervals.length, 1);
  assert.equal(p.w.intervals[0].ms, 60000);
  assert.deepEqual(p.w.asked, ["/texts/state"]);                      // and it looked at once
});

test("nothing is made on a page that did not ask for it", () => {
  const p = page({ state: null });
  delete p.opts.url; delete p.els.drive.dataset.texts;
  assert.equal(X.attach(p.opts), null);
  const q = page({ state: null }); delete q.els["now-t"];
  assert.equal(X.attach(q.opts), null);
});

test("a new code shows its digits for what is left of the five minutes, and goes when they are up", async () => {
  const p = page({ state: fresh("482913", 120) });
  const ctl = X.attach(p.opts);
  await flush();
  assert.equal(ctl.chip.hidden, false);
  assert.equal(ctl.chip.children[1].textContent, "482913");
  const gone = p.w.timers.filter(t => !t.off).pop();
  assert.equal(gone.ms, 180000);
  gone.fn();
  assert.equal(ctl.chip.hidden, true);
});

test("when the portal says there is no fresh code any more, the chip goes", async () => {
  const p = page({ state: fresh("482913", 120) });
  const ctl = X.attach(p.opts);
  await flush();
  assert.equal(ctl.chip.hidden, false);
  p.w.state = { fresh: null };
  await ctl.check();
  assert.equal(ctl.chip.hidden, true);
});

test("a dead zone leaves the chip as it was, and a hidden page does not ask", async () => {
  const p = page({ state: fresh("482913", 120) });
  const ctl = X.attach(p.opts);
  await flush();
  p.w.state = new Error("offline");
  await ctl.check();
  assert.equal(ctl.chip.hidden, false);
  const asked = p.w.asked.length;
  p.doc.hidden = true;
  await ctl.check();
  assert.equal(p.w.asked.length, asked);
});

test("in hub mode the code is said once, digit by digit; a second look at it is silent; a new code is said", async () => {
  const p = page({ hub: true, state: fresh("482913", 20) });
  const ctl = X.attach(p.opts);
  await flush();
  assert.deepEqual(p.w.said, ["Your code is 4 8 2 9 1 3"]);
  await ctl.check();
  assert.deepEqual(p.w.said, ["Your code is 4 8 2 9 1 3"]);
  p.w.state = fresh("771100", 5);
  await ctl.check();
  assert.deepEqual(p.w.said.slice(1), ["Your code is 7 7 1 1 0 0"]);
  assert.equal(ctl.chip.children[1].textContent, "771100");
});

test("off hub mode the chip shows and nothing is said; if his voice is not ready yet, it is tried again at the next look", async () => {
  const quiet = page({ hub: false, state: fresh("482913") });
  X.attach(quiet.opts);
  await flush();
  assert.deepEqual(quiet.w.said, []);
  const mute = page({ hub: true, state: fresh("482913"), voice: false });
  const ctl = X.attach(mute.opts);
  await flush();
  assert.equal(mute.w.said.length, 0);
  assert.equal(ctl.memory.spoken.size, 0);                            // (not marked as said)
  mute.w.voice = true;                                                // his voice is ready now
  await ctl.check();
  assert.deepEqual(mute.w.said, ["Your code is 4 8 2 9 1 3"]);
});

test("a tap copies the code, says so, puts the chip away and keeps it away", async () => {
  const p = page({ state: fresh("482913") });
  const ctl = X.attach(p.opts);
  await flush();
  await ctl.chip.click();
  assert.deepEqual(p.opts.clipboard.copied, ["482913"]);
  assert.equal(ctl.chip.dataset.s, "on");
  assert.equal(ctl.chip.children[1].textContent, "Copied");
  const away = p.w.timers.filter(t => !t.off).pop();
  assert.equal(away.ms, 700);
  away.fn();
  assert.equal(ctl.chip.hidden, true);
  assert.equal(ctl.chip.dataset.s, undefined);
  await ctl.check();                                                   // the same code is still "fresh" at the portal
  assert.equal(ctl.chip.hidden, true);
  p.w.state = fresh("999000", 3);
  await ctl.check();
  assert.equal(ctl.chip.hidden, false);
});

test("with no clipboard API the old way copies; with neither the chip says to hold the code", async () => {
  const old = page({ state: fresh("482913"), clipboard: null });
  const a = X.attach(old.opts);
  await flush();
  await a.chip.click();
  assert.deepEqual(old.doc.commands, ["copy"]);
  assert.equal(a.chip.dataset.s, "on");
  const none = page({ state: fresh("482913"), clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
  none.doc.copyWorks = false;
  const b = X.attach(none.opts);
  await flush();
  await b.chip.click();
  assert.equal(b.chip.dataset.s, "warn");
  assert.equal(b.chip.children[1].textContent, "Hold to copy");
  assert.equal(none.w.timers.filter(t => !t.off).pop().ms, 1500);
});
