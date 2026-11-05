// The direct front door's page half (static/direct.js, SkippyDirect.pick / demote) under Node: the door that answers, the one that is slow,
// down, wrong or not offered, what is remembered and for how long (a clock the test sets), what several callers at once cost, and a page
// with no storage. Every browser part (fetch, sessionStorage, the clock) is replaced by a fake.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const D = require("../../static/direct.js");

const FUNNEL = "https://skippy-pc.tail1234.ts.net:10000/music";
const DOOR = "https://home.makerportal.live/music";
const gate = extra => Object.assign({ ok: true, url: FUNNEL, pass: "1.a", exp: 2000000000, direct: DOOR }, extra);

/** A fetch that answers with `answer(url, init)` and writes every call down. */
function edge(answer) {
  const f = (url, init) => { f.calls.push({ url: String(url), init }); try { return Promise.resolve(answer(String(url), init)); } catch (e) { return Promise.reject(e); } };
  f.calls = [];
  return f;
}
const reply = body => ({ ok: true, status: 200, json: async () => body });
const healthy = () => edge(() => reply({ ok: true, ready: true }));
const memoryStorage = () => {
  const m = new Map();
  return { m, getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); } };
};
const clock = start => { const c = { t: start, now: () => c.t }; return c; };
const T0 = 1_800_000_000_000;

test("the contract's numbers: 2.5 s to answer, believed 120 s when it did, 300 s when it did not", () => {
  assert.equal(D.PROBE_MS, 2500);
  assert.equal(D.OK_MS, 120000);
  assert.equal(D.BAD_MS, 300000);
});

test("a door that answers: g.url becomes the direct address, g.funnel keeps the Funnel's, and it is g itself that comes back", async () => {
  const f = healthy(), door = D.create({ fetch: f, storage: null });
  const g = gate();
  const back = await door.pick(g);
  assert.equal(back, g);
  assert.equal(g.url, DOOR);
  assert.equal(g.funnel, FUNNEL);
  assert.equal(g.direct, DOOR);                                                                  // the portal's own fields are left as they were
  assert.deepEqual([g.ok, g.pass, g.exp], [true, "1.a", 2000000000]);
  assert.equal(`${g.url}/t/${g.pass}/catalog.json`, "https://home.makerportal.live/music/t/1.a/catalog.json");   // what a page builds from it
});

test("the probe is GET <direct>/health, never cached, with no credentials, and can be stopped", async () => {
  const f = healthy(), door = D.create({ fetch: f, storage: null });
  await door.pick(gate());
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, DOOR + "/health");
  assert.equal(f.calls[0].init.cache, "no-store");
  assert.equal(f.calls[0].init.credentials, "omit");
  assert.ok(f.calls[0].init.signal instanceof AbortSignal);
  assert.equal(f.calls[0].init.method, undefined);                                                // a GET: no method, no body, no headers to preflight
  assert.equal(f.calls[0].init.body, undefined);
  assert.equal(f.calls[0].init.headers, undefined);
});

test("a trailing slash on the direct address changes nothing", async () => {
  const f = healthy(), door = D.create({ fetch: f, storage: null });
  const g = gate({ direct: DOOR + "/" });
  await door.pick(g);
  assert.equal(f.calls[0].url, DOOR + "/health");
  assert.equal(g.url, DOOR);
});

test("a door that is up but not ready (the library is still being read) is still a door that answers", async () => {
  const door = D.create({ fetch: edge(() => reply({ ok: true, ready: false, detail: "the music library is still being read" })), storage: null });
  const g = gate();
  await door.pick(g);
  assert.equal(g.url, DOOR);
});

test("a door that is slow: the Funnel stays, and the probe is called off", async () => {
  let signal = null;
  const f = edge((url, init) => { signal = init.signal; return new Promise((resolve, reject) => init.signal.addEventListener("abort", () => reject(new Error("aborted")))); });
  const door = D.create({ fetch: f, storage: null, probeMs: 30 });
  const g = gate();
  const t0 = Date.now();
  await door.pick(g);
  assert.equal(g.url, FUNNEL);
  assert.equal(g.funnel, FUNNEL);
  assert.ok(Date.now() - t0 < 1500);
  assert.equal(signal.aborted, true);                                                              // a slow door stops costing a connection
});

test("a door whose headers come but whose body never does is slow too", async () => {
  const door = D.create({ fetch: edge(() => ({ ok: true, status: 200, json: () => new Promise(() => {}) })), storage: null, probeMs: 30 });
  const g = gate();
  await door.pick(g);
  assert.equal(g.url, FUNNEL);
});

test("a door that answers with an error is not the door to use", async () => {
  for (const status of [404, 500, 502, 503, 504, 403, 301]) {
    const door = D.create({ fetch: edge(() => ({ ok: false, status, json: async () => ({ error: "x" }) })), storage: null });
    const g = gate();
    await door.pick(g);
    assert.equal(g.url, FUNNEL, String(status));
  }
});

test("an answer that is not the gate's: an HTML page (a captive portal), JSON that is no object, nothing at all", async () => {
  const bad = [
    { ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token <"); } },     // 200 with a login page
    reply(null), reply(42), reply("ok"), reply([]), reply(true),
    { ok: true, status: 200 },                                                                           // no body reader at all
    null, undefined,
  ];
  for (const answer of bad) {
    const door = D.create({ fetch: edge(() => answer), storage: null });
    const g = gate();
    await door.pick(g);
    assert.equal(g.url, FUNNEL, JSON.stringify(answer));
  }
});

test("a door that cannot be reached at all: fetch rejects, or throws on the spot, or the page has no fetch: the Funnel, and never a rejection", async () => {
  const rejects = edge(() => { throw new TypeError("Failed to fetch"); });
  const throwsAtOnce = () => { throw new Error("blocked"); };
  for (const f of [rejects, throwsAtOnce, null]) {
    const door = D.create({ fetch: f, storage: null });
    const g = gate();
    await assert.doesNotReject(door.pick(g));
    assert.equal(g.url, FUNNEL);
    assert.equal(g.funnel, FUNNEL);
  }
});

test("no usable direct address: no probe at all, and g.url stays the Funnel", async () => {
  for (const direct of [undefined, null, "", "   ", 42, {}, [], "javascript:alert(1)", "ftp://home.makerportal.live/music", "//home.makerportal.live/music",
                        "home.makerportal.live/music", "https://", "https://home.makerportal.live/mu sic", "https://home.makerportal.live/music?x=1", FUNNEL]) {
    const f = healthy(), door = D.create({ fetch: f, storage: null });
    const g = gate({ direct });
    const back = await door.pick(g);
    assert.equal(back, g, String(direct));
    assert.equal(f.calls.length, 0, String(direct));
    assert.equal(g.url, FUNNEL, String(direct));
    assert.equal(g.funnel, FUNNEL, String(direct));
  }
  const g = { ok: true, url: FUNNEL, pass: "1.a", exp: 1 };                                          // the field is simply not there (door off)
  await D.create({ fetch: healthy(), storage: null }).pick(g);
  assert.deepEqual(g, { ok: true, url: FUNNEL, pass: "1.a", exp: 1, funnel: FUNNEL });
});

test("something that is not a gate response comes back as it is, and nothing throws", async () => {
  const f = healthy(), door = D.create({ fetch: f, storage: null });
  const frozen = Object.freeze(gate());
  for (const g of [null, undefined, "str", 42, true, {}, { url: 5, direct: DOOR }, { direct: DOOR }, [], frozen]) {
    const back = await door.pick(g);
    assert.equal(back, g);
  }
  assert.equal(frozen.url, FUNNEL);                                                                  // (a frozen object cannot be changed: it is left alone)
  assert.equal(f.calls.length, 0);
  await assert.doesNotReject(door.pick());
});

test("callers that arrive while a probe is in the air share it", async () => {
  let release;
  const f = edge(() => new Promise(resolve => { release = () => resolve(reply({ ok: true })); }));
  const door = D.create({ fetch: f, storage: null });
  const a = gate(), b = gate({ pass: "2.b" }), c = gate({ pass: "3.c" });
  const all = Promise.all([door.pick(a), door.pick(b), door.pick(c)]);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(f.calls.length, 1);                                                                   // one probe for the three
  release();
  await all;
  assert.deepEqual([a.url, b.url, c.url], [DOOR, DOOR, DOOR]);
  assert.deepEqual([a.funnel, b.funnel, c.funnel], [FUNNEL, FUNNEL, FUNNEL]);
  await door.pick(gate());
  assert.equal(f.calls.length, 1);                                                                   // and the answer is remembered
});

test("an answer is believed for 120 s when the door answered", async () => {
  const f = healthy(), c = clock(T0), door = D.create({ fetch: f, storage: null, now: c.now });
  await door.pick(gate());
  assert.equal(f.calls.length, 1);
  c.t = T0 + 119_999;
  const g = gate();
  await door.pick(g);
  assert.equal(f.calls.length, 1);                                                                   // still believed
  assert.equal(g.url, DOOR);
  c.t = T0 + 120_000;
  await door.pick(gate());
  assert.equal(f.calls.length, 2);                                                                   // asked again
});

test("a silence is believed for 300 s, so a door that is down costs one probe, not one per request", async () => {
  const f = edge(() => { throw new TypeError("Failed to fetch"); }), c = clock(T0), door = D.create({ fetch: f, storage: null, now: c.now });
  const first = gate();
  await door.pick(first);
  assert.equal(first.url, FUNNEL);
  for (let i = 0; i < 5; i++) await door.pick(gate());
  assert.equal(f.calls.length, 1);
  c.t = T0 + 299_999;
  await door.pick(gate());
  assert.equal(f.calls.length, 1);
  c.t = T0 + 300_000;
  await door.pick(gate());
  assert.equal(f.calls.length, 2);
});

test("the answer goes in sessionStorage under the direct address, and the next page load of the tab reads it", async () => {
  const store = memoryStorage(), c = clock(T0);
  const f1 = healthy();
  await D.create({ fetch: f1, storage: store, now: c.now }).pick(gate());
  assert.deepEqual(JSON.parse(store.getItem("skippy-direct:" + DOOR)), { ok: true, at: T0 });
  c.t = T0 + 60_000;
  const f2 = healthy(), g = gate();                                                                  // a reload: a new page, a new instance, the same tab's storage
  await D.create({ fetch: f2, storage: store, now: c.now }).pick(g);
  assert.equal(f2.calls.length, 0);
  assert.equal(g.url, DOOR);
  c.t = T0 + 120_000;
  const f3 = healthy();
  await D.create({ fetch: f3, storage: store, now: c.now }).pick(gate());
  assert.equal(f3.calls.length, 1);                                                                  // too old by then
  const down = memoryStorage();
  await D.create({ fetch: edge(() => reply("no")), storage: down, now: c.now }).pick(gate());
  assert.equal(JSON.parse(down.getItem("skippy-direct:" + DOOR)).ok, false);
  c.t += 299_000;
  const f4 = healthy(), g4 = gate();
  await D.create({ fetch: f4, storage: down, now: c.now }).pick(g4);
  assert.equal(f4.calls.length, 0);                                                                  // a door that was down stays down for the five minutes
  assert.equal(g4.url, FUNNEL);
});

test("a clock that went backwards, and a stored answer that makes no sense, never make an answer last for ever", async () => {
  const store = memoryStorage(), c = clock(T0);
  store.setItem("skippy-direct:" + DOOR, JSON.stringify({ ok: true, at: T0 + 10 * 60_000 }));       // stamped in the future
  const f = healthy();
  await D.create({ fetch: f, storage: store, now: c.now }).pick(gate());
  assert.equal(f.calls.length, 1);
  for (const junk of ["", "not json", "null", "42", "[]", "{}", JSON.stringify({ ok: "yes", at: T0 }), JSON.stringify({ ok: true, at: "now" }), JSON.stringify({ ok: true })]) {
    const s = memoryStorage();
    s.setItem("skippy-direct:" + DOOR, junk);
    const probe = healthy();
    const g = gate();
    await D.create({ fetch: probe, storage: s, now: c.now }).pick(g);
    assert.equal(probe.calls.length, 1, junk);
    assert.equal(g.url, DOOR, junk);
  }
});

test("a page with no usable storage works the same, and its own memory still keeps the answer", async () => {
  const throwing = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("QuotaExceededError"); } };
  const refused = env => Object.defineProperty(env, "storage", { get() { throw new Error("SecurityError: the page may not touch storage"); } });   // (reading the property throws)
  const envs = [() => ({ storage: throwing }), () => ({ storage: null }), () => refused({})];
  for (const make of envs) {
    const f = healthy();
    const door = D.create(Object.assign(make(), { fetch: f, now: () => T0 }));
    const a = gate(), b = gate();
    await assert.doesNotReject(door.pick(a));
    assert.equal(a.url, DOOR);
    await door.pick(b);
    assert.equal(b.url, DOOR);
    assert.equal(f.calls.length, 1);                                                                 // asked once, remembered in this page's memory
  }
});

test("each door has its own answer", async () => {
  const VIDEO_FUNNEL = "https://skippy-pc.tail1234.ts.net:8443/video", VIDEO = "https://home.makerportal.live/video";
  const f = edge(url => (url.startsWith(VIDEO) ? { ok: false, status: 502, json: async () => ({}) } : reply({ ok: true })));
  const door = D.create({ fetch: f, storage: memoryStorage(), now: () => T0 });
  const music = gate(), video = gate({ url: VIDEO_FUNNEL, direct: VIDEO });
  await door.pick(music);
  await door.pick(video);
  assert.equal(music.url, DOOR);
  assert.equal(video.url, VIDEO_FUNNEL);                                                             // the video gate is down behind a working edge
  assert.deepEqual(f.calls.map(c => c.url), [DOOR + "/health", VIDEO + "/health"]);
});

test("fresh: look again whatever is remembered (a retry after trouble)", async () => {
  let up = true;
  const f = edge(() => (up ? reply({ ok: true }) : { ok: false, status: 502, json: async () => ({}) }));
  const c = clock(T0), door = D.create({ fetch: f, storage: memoryStorage(), now: c.now });
  const g = gate();
  await door.pick(g);
  assert.equal(g.url, DOOR);
  up = false;
  await door.pick(g);
  assert.equal(g.url, DOOR);                                                                         // remembered: not asked
  assert.equal(f.calls.length, 1);
  await door.pick(g, { fresh: true });
  assert.equal(f.calls.length, 2);
  assert.equal(g.url, FUNNEL);                                                                       // the same object goes back to the Funnel
  assert.equal(g.funnel, FUNNEL);                                                                    // and the Funnel's address was never overwritten
  up = true;
  await door.pick(g);
  assert.equal(f.calls.length, 2);                                                                   // a silence is remembered too ...
  assert.equal(g.url, FUNNEL);
  await door.pick(g, { fresh: true });
  assert.equal(f.calls.length, 3);                                                                   // ... until someone says look again
  assert.equal(g.url, DOOR);
  await door.pick(g, { fresh: false });
  await door.pick(g, null);
  await door.pick(g, "yes");
  assert.equal(f.calls.length, 3);
});

test("asked twice, g.funnel is still the Funnel's address and g.url follows the door", async () => {
  const f = healthy(), door = D.create({ fetch: f, storage: null, now: () => T0 });
  const g = gate();
  await door.pick(g);
  await door.pick(g);
  assert.equal(g.url, DOOR);
  assert.equal(g.funnel, FUNNEL);
  g.direct = null;                                                                                    // the portal turned the door off
  await door.pick(g);
  assert.equal(g.url, FUNNEL);
});

test("a pass that comes with no door, in a browser that has seen the door answer, is made to end in 35 minutes so the players ask again in about five; never longer than it was", async () => {
  const c = clock(T0), local = memoryStorage();
  const door = D.create({ fetch: healthy(), storage: null, local, now: c.now });
  const never = gate({ direct: null });
  await door.pick(never);
  assert.equal(never.exp, 2000000000);                                                               // this browser has never seen the door: left alone
  await door.pick(gate());                                                                           // the door answers once ...
  assert.equal(local.m.get("skippy-direct-seen"), "1");
  const cut = gate({ direct: null });
  await door.pick(cut);                                                                              // ... and a later pass comes without it
  assert.equal(cut.url, FUNNEL);
  assert.equal(cut.exp, T0 / 1000 + D.RENEW_S);
  assert.equal(D.RENEW_S, 2100);                                                                     // 30 minutes before the end is "renew": 35 - 30 = asked again in 5
  const soon = gate({ direct: null, exp: T0 / 1000 + 100 });
  await door.pick(soon);
  assert.equal(soon.exp, T0 / 1000 + 100);                                                           // a pass that ends sooner is never lengthened
  const offered = gate({ direct: DOOR, exp: 2000000000 });
  c.t = T0 + 130_000;                                                                                // (the remembered answer has gone stale)
  await D.create({ fetch: edge(() => { throw new Error("down"); }), storage: null, local, now: c.now }).pick(offered);
  assert.equal(offered.url, FUNNEL);
  assert.equal(offered.exp, 2000000000);                                                             // a door that was offered and did not answer from here: the pass is left as it is
  const next = D.create({ fetch: healthy(), storage: null, local, now: c.now });                     // a new page load: the browser still remembers having seen the door
  const again = gate({ direct: null });
  await next.pick(again);
  assert.equal(again.exp, (T0 + 130_000) / 1000 + D.RENEW_S);
  const odd = [gate({ direct: null, exp: "soon" }), gate({ direct: null, exp: NaN }), gate({ direct: null, exp: undefined })];
  for (const g of odd) await assert.doesNotReject(() => next.pick(g));                              // no usable expiry: nothing is invented
  assert.equal(odd[0].exp, "soon");
});

test("demote: a call to the direct door got no answer: g goes on through the Funnel, the door is remembered as down, and the call is worth making again", async () => {
  const f = healthy(), c = clock(T0), door = D.create({ fetch: f, storage: memoryStorage(), now: c.now });
  const g = gate();
  await door.pick(g);
  assert.equal(g.url, DOOR);
  assert.equal(door.demote(g, DOOR), true);
  assert.equal(g.url, FUNNEL);
  assert.equal(g.funnel, FUNNEL);
  const next = gate();                                                                                // another pass, a moment later: no probe, the Funnel
  await door.pick(next);
  assert.equal(f.calls.length, 1);
  assert.equal(next.url, FUNNEL);
  c.t = T0 + 300_000;
  await door.pick(gate());
  assert.equal(f.calls.length, 2);                                                                   // five minutes on, the door is looked at again
});

test("demote: two calls that failed together both learn that the Funnel is the way on; a call that was on the Funnel learns nothing", async () => {
  const door = D.create({ fetch: healthy(), storage: null, now: () => T0 });
  const g = gate();
  await door.pick(g);
  assert.equal(door.demote(g, DOOR), true);                                                          // the first to fail
  assert.equal(door.demote(g, DOOR), true);                                                          // the second, which also went to the direct door
  assert.equal(door.demote(g, DOOR + "/"), true);
  assert.equal(door.demote(g, FUNNEL), false);                                                       // one that went to the Funnel failed for another reason
  assert.equal(door.demote(g), false);                                                               // (no `used`: g is on the Funnel now)
  await door.pick(g, { fresh: true });
  assert.equal(g.url, DOOR);
  assert.equal(door.demote(g), true);                                                                // (no `used`: g is on the direct door)
});

test("demote does nothing for a gate with no direct door, and never throws", () => {
  const door = D.create({ fetch: healthy(), storage: null });
  const plain = { ok: true, url: FUNNEL, pass: "1.a", funnel: FUNNEL };
  assert.equal(door.demote(plain, FUNNEL), false);
  assert.equal(plain.url, FUNNEL);
  for (const g of [null, undefined, "x", 3, {}, { url: DOOR, direct: DOOR }, Object.freeze({ url: DOOR, funnel: FUNNEL, direct: DOOR })])
    assert.doesNotThrow(() => door.demote(g, DOOR));
});

test("the shared instance (window.SkippyDirect.pick) uses the page's own fetch and sessionStorage", async () => {
  const keep = { fetch: globalThis.fetch, storage: Object.getOwnPropertyDescriptor(globalThis, "sessionStorage") };
  const store = memoryStorage(), f = healthy();
  const shared = "https://shared-instance.test/music";
  globalThis.fetch = f;
  Object.defineProperty(globalThis, "sessionStorage", { value: store, configurable: true, writable: true });
  try {
    const g = gate({ direct: shared });
    assert.equal(await D.pick(g), g);
    assert.equal(g.url, shared);
    assert.equal(f.calls[0].url, shared + "/health");
    assert.equal(JSON.parse(store.getItem("skippy-direct:" + shared)).ok, true);
    const h = gate({ direct: shared });
    await D.pick(h);
    assert.equal(f.calls.length, 1);
    assert.equal(h.url, shared);
    assert.equal(D.demote(h, shared), true);
    assert.equal(h.url, FUNNEL);
  } finally {
    globalThis.fetch = keep.fetch;
    if (keep.storage) Object.defineProperty(globalThis, "sessionStorage", keep.storage); else delete globalThis.sessionStorage;
  }
});

test("the address helpers: one form for a direct address, and its prefix is the last path segment", () => {
  assert.equal(D.doorOf("https://home.makerportal.live/music"), "https://home.makerportal.live/music");
  assert.equal(D.doorOf("  https://home.makerportal.live/music//  "), "https://home.makerportal.live/music");
  assert.equal(D.doorOf("http://localhost:8080/in"), "http://localhost:8080/in");
  for (const bad of [null, undefined, 3, "", "music", "/music", "javascript:alert(1)", "https://x y/z",
    "https://home.makerportal.live@evil.example/music", "https://user:pw@home.makerportal.live/music"]) assert.equal(D.doorOf(bad), "", String(bad));      // (user info is never a door)
  assert.deepEqual(["music", "video", "books", "voice", "in"].map(p => D.prefixOf("https://home.makerportal.live/" + p)), ["music", "video", "books", "voice", "in"]);
  assert.equal(D.prefixOf("https://home.makerportal.live/music/"), "music");
  assert.equal(D.prefixOf("https://home.makerportal.live"), "");
  assert.equal(D.prefixOf(null), "");
});
