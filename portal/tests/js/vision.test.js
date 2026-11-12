// Skippy-vision's page half (static/vision.js) under Node: the spoken words, the host's stills, and the flash with its hold, its clip,
// its Forget and its failure, every browser part replaced by a fake.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../../static/vision.js");

const b64 = n => Buffer.from("x".repeat(n)).toString("base64");

test("the camera words: a snap is a flash, 'big time' or 'hold it' is a hold, a sentence that merely contains one is neither", () => {
  for (const t of ["snap", "Snap.", "snap it", "Flash!", "flash that", "SNAP THIS"]) assert.equal(V.flashWord(t), "flash", t);
  for (const t of ["snap it big time", "Snap it big time.", "hold it", "hold that!", "snap and hold", "flash and hold"]) assert.equal(V.flashWord(t), "hold", t);
  for (const t of ["", null, undefined, "snapshot", "take a snap of this", "what is a flash drive", "hold the door", "snap it big time please"]) assert.equal(V.flashWord(t), null, String(t));
});

test("the reading words: read it, what am I looking at, steady on and off", () => {
  for (const t of ["read that", "Read this.", "what does that say?", "translate it"]) assert.equal(V.readWord(t), "read", t);
  for (const t of ["what am I looking at", "What's that?", "what is this"]) assert.equal(V.readWord(t), "look", t);
  assert.equal(V.readWord("steady on"), "steady-on");
  assert.equal(V.readWord("Steady reading off."), "steady-off");
  for (const t of ["", "read me a book", "steady as she goes"]) assert.equal(V.readWord(t), null, t);
});

test("the glasses' stills: the three cameras the host sent, under the names the portal takes, and nothing for a missing or tiny one", () => {
  const got = V.glassesStills({ rgb: b64(150), left: b64(150), right: b64(150), batteryC: 31 });
  assert.deepEqual(got.map(g => g.name), ["glasses-rgb.jpg", "glasses-left.jpg", "glasses-right.jpg"]);
  assert.ok(got.every(g => g.data.startsWith("data:image/jpeg;base64,")));
  assert.deepEqual(V.glassesStills({ rgb: b64(150), left: "", right: "tiny" }).map(g => g.name), ["glasses-rgb.jpg"]);
  assert.deepEqual(V.glassesStills(null), []);
  assert.deepEqual(V.glassesStills(undefined), []);
});

// ── the flash, with a fake portal, fake host and no page to photograph ──
function world(extra = {}) {
  const calls = [], saved = [], said = [];
  const host = "host" in extra ? extra.host : {
    flashed: [],
    flash(hold) { this.flashed.push(hold); return JSON.stringify({ rgb: b64(200), left: b64(200), right: b64(200) }); },
    flashClip() { return Buffer.from("PK\x03\x04zipbytes").toString("base64"); },
  };
  const respond = extra.respond || (() => ({ ok: true, json: async () => ({ id: "20261102-210000", files: [] }) }));
  const cfg = {
    flash: "/vision/flash", momentAdd: "/vision/moment/_MID_/file", momentDelete: "/vision/moment/_MID_/delete", momentKeep: "/vision/moment/_MID_/keep",
    page: false, nav: {}, doc: {}, host,
    fetch: async (url, init) => { calls.push({ url, init }); return respond(url, init); },
    say: t => said.push(t), onSaved: (m, frame) => saved.push({ m, frame }), wait: async () => {},
  };
  return { calls, saved, said, host, vision: V.attach(Object.assign(cfg, extra.cfg || {})) };
}

test("a flash sends the glasses' three stills in one request, says nothing, and hands the page the moment and the glasses' frame", async () => {
  const w = world();
  const m = await w.vision.flash(false, "key");
  assert.equal(m.id, "20261102-210000");
  assert.equal(w.calls.length, 1);
  const body = JSON.parse(w.calls[0].init.body);
  assert.deepEqual(body.images.map(i => i.name), ["glasses-rgb.jpg", "glasses-left.jpg", "glasses-right.jpg"]);
  assert.equal(body.hold, false);
  assert.equal(body.by, "key");
  assert.deepEqual(w.host.flashed, [false]);
  assert.equal(w.saved.length, 1);
  assert.ok(w.saved[0].frame.startsWith("data:image/jpeg;base64,"));
  assert.deepEqual(w.said, []);
});

test("a hold asks the host for its clip a moment later and sends the zip to the moment it made", async () => {
  const w = world();
  await w.vision.flash(true, "word");
  await new Promise(ok => setTimeout(ok, 20));                       // (the clip follows the flash; its wait is a fake)
  assert.equal(w.calls.length, 2);
  assert.equal(w.calls[1].url, "/vision/moment/20261102-210000/file?name=glasses-rgb-7s.zip");
  assert.equal(w.calls[1].init.headers["Content-Type"], "application/zip");
  assert.equal(Buffer.from(await w.calls[1].init.body.arrayBuffer()).subarray(0, 4).toString("latin1"), "PK\x03\x04");
  assert.equal(JSON.parse(w.calls[0].init.body).hold, true);
});

test("a plain tap never asks for a clip", async () => {
  const w = world();
  await w.vision.flash(false, "key");
  await new Promise(ok => setTimeout(ok, 20));
  assert.equal(w.calls.length, 1);
});

test("a second press while the first is being taken is the same moment", async () => {
  let release, n = 0;
  const w = world({ respond: () => n++ ? { ok: true, json: async () => ({ id: "20261102-210002" }) }
                                       : new Promise(ok => { release = () => ok({ ok: true, json: async () => ({ id: "20261102-210001" }) }); }) });
  const first = w.vision.flash(false, "key");
  assert.equal(await w.vision.flash(false, "key"), null);
  await new Promise(ok => setTimeout(ok, 5));
  release();
  assert.equal((await first).id, "20261102-210001");
  assert.equal(w.calls.length, 1);
  assert.equal((await w.vision.flash(false, "key")).id, "20261102-210002");          // and the key is free again
});

test("no glasses, no cameras, no page: the flash still goes, with nothing but the request", async () => {
  const w = world({ host: null });
  const m = await w.vision.flash(false, "key");
  assert.ok(m);
  assert.deepEqual(JSON.parse(w.calls[0].init.body).images, []);
  assert.equal(w.saved[0].frame, null);
  assert.equal(w.vision.hosted(), false);
});

test("a portal that refuses, or cannot be reached, is 'Not saved.' and no moment", async () => {
  const refused = world({ respond: () => ({ ok: false, json: async () => ({}) }) });
  assert.equal(await refused.vision.flash(false, "key"), null);
  assert.deepEqual(refused.said, ["Not saved."]);
  assert.equal(refused.saved.length, 0);
  const down = world({ respond: () => { throw new Error("offline"); } });
  assert.equal(await down.vision.flash(false, "key"), null);
  assert.deepEqual(down.said, ["Not saved."]);
  assert.equal(await down.vision.flash(false, "key") === null, true);    // (and the busy flag was let go, so it tries again)
  assert.equal(down.said.length, 2);
});

test("Forget and Keep go to the moment's own addresses", async () => {
  const w = world();
  assert.equal(await w.vision.forget("20261102-210000"), true);
  assert.equal(w.calls[0].url, "/vision/moment/20261102-210000/delete");
  assert.equal(w.calls[0].init.method, "POST");
  assert.equal(await w.vision.keep("20261102-210000", true), true);
  assert.equal(w.calls[1].url, "/vision/moment/20261102-210000/keep");
  assert.deepEqual(JSON.parse(w.calls[1].init.body), { on: true });
  const bad = world({ respond: () => ({ ok: false }) });
  assert.equal(await bad.vision.forget("x"), false);
});

// ── what he sees: the freshest frame, the writing in it, and one sentence on it ──
test("the freshest frame is the glasses' own RGB frame; a stub of a frame, or no camera at all, is none", async () => {
  const w = world({ host: { frame: () => b64(300), flash() { return "{}"; } } });
  assert.ok((await w.vision.frame()).startsWith("data:image/jpeg;base64,"));
  assert.equal(await world({ host: { frame: () => "short" } }).vision.frame(), null);
  assert.equal(await world({ host: null }).vision.frame(), null);
});

test("readSigns and lookAt send the picture to the portal's reader and give back the writing and the sentence", async () => {
  const w = world({ cfg: { read: "/vision/read", look: "/vision/look" },
                    respond: url => ({ ok: true, json: async () => url.startsWith("/vision/read") ? { seen: [{ text: "Salida", lang: "es", en: "Exit" }] } : { said: "a door" } }) });
  const pic = "data:image/jpeg;base64,AAAA";
  assert.deepEqual(await w.vision.readSigns(pic, "20261102-210000"), [{ text: "Salida", lang: "es", en: "Exit" }]);
  assert.equal(w.calls[0].url, "/vision/read?moment=20261102-210000");
  assert.equal(JSON.parse(w.calls[0].init.body).image, pic);
  assert.equal(await w.vision.lookAt(pic), "a door");
  assert.equal(w.calls[1].url, "/vision/look");
});

test("a reader that cannot be asked, a picture that is missing and a page without the addresses are all null, and ask nothing needlessly", async () => {
  const refused = world({ cfg: { read: "/vision/read", look: "/vision/look" }, respond: () => ({ ok: false, json: async () => ({}) }) });
  assert.equal(await refused.vision.readSigns("data:image/jpeg;base64,AAAA"), null);
  assert.equal(await refused.vision.lookAt("data:image/jpeg;base64,AAAA"), null);
  const down = world({ cfg: { read: "/vision/read" }, respond: () => { throw new Error("offline"); } });
  assert.equal(await down.vision.readSigns("data:image/jpeg;base64,AAAA"), null);
  const bare = world();
  assert.equal(await bare.vision.readSigns("data:image/jpeg;base64,AAAA"), null);          // no address configured
  assert.equal(await bare.vision.lookAt(null), null);
  assert.equal(bare.calls.length, 0);
  const silent = world({ cfg: { look: "/vision/look" }, respond: () => ({ ok: true, json: async () => ({ said: "" }) }) });
  assert.equal(await silent.vision.lookAt("data:image/jpeg;base64,AAAA"), null);         // an empty sentence is no sentence
});

test("the recorder words: record a walk, stop recording, and nothing else", () => {
  for (const t of ["record a walk", "Record the walk.", "start recording", "Start recording a walk!"]) assert.equal(V.hostWord(t), "record-on", t);
  for (const t of ["stop recording", "Stop the recording.", "end the walk"]) assert.equal(V.hostWord(t), "record-off", t);
  for (const t of ["", null, "record", "recording", "record my screen", "stop", "go for a walk"]) assert.equal(V.hostWord(t), null, String(t));
});
