// The chat's camera key (portal/static/skippy-camera.js) under Node with a fake camera, a fake DOM and fake canvases whose JPEG size goes
// with the picture's area. No real camera: this proves the two taps, the back camera first, asking for full resolution, the camera
// stopped before the photo is encoded, downscaling only as far as the budget requires, the flip, and a busy iPad camera saying so.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Cam = require("../../../static/skippy-camera.js");

class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.on = {}; this.dataset = {}; this.hidden = false; this.parent = null; this.className = "";
    const set = new Set();
    this.classList = {add: c => set.add(c), remove: c => set.delete(c), contains: c => set.has(c),
      toggle: (c, on) => ((on === undefined ? !set.has(c) : on) ? set.add(c) : set.delete(c))};
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(k, f) { (this.on[k] = this.on[k] || []).push(f); }
  fire(k, e) { (this.on[k] || []).forEach(f => f(Object.assign({stopPropagation() {}}, e))); }
  click() { this.fire("click", {}); }
  append(...xs) { xs.forEach(x => this.appendChild(x)); }
  appendChild(x) { x.parent = this; this.children.push(x); return x; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; }
  querySelector(sel) {
    const want = sel.replace(/^\./, "");
    for (const c of this.children) {
      if (c.className && c.className.split(" ").includes(want)) return c;
      const d = c.querySelector(sel); if (d) return d;
    }
    return null;
  }
}
let encodes = [], log = [];
function makeDoc() {
  const doc = new El("document"); doc.hidden = false;
  doc.createElement = (tag) => {
    const e = new El(tag);
    if (tag === "canvas") {                              // a fake JPEG: its data URL length goes with the area and the quality
      e.getContext = () => ({drawImage: () => {}});
      e.toDataURL = (type, q) => { encodes.push({w: e.width, h: e.height, q}); log.push("encode"); return "data:image/jpeg;base64," + "A".repeat(Math.round(e.width * e.height * 0.5 * q)); };
    }
    if (tag === "video") {
      e.videoWidth = 0; e.videoHeight = 0;
      e.play = async () => { if (e.srcObject) { e.videoWidth = e.srcObject.frame[0]; e.videoHeight = e.srcObject.frame[1]; } };
    }
    return e;
  };
  doc.createElementNS = (ns, tag) => new El(tag);
  return doc;
}
function makeMedia({frame = [4032, 3024], fail = null} = {}) {
  const m = {asks: [], tracks: []};
  m.getUserMedia = async (c) => {
    m.asks.push(c);
    if (fail) { const e = new Error("held"); e.name = fail; throw e; }
    const track = new El("track"); track.stopped = false;
    track.stop = () => { track.stopped = true; log.push("stop"); };
    track.getCapabilities = () => ({width: {max: frame[0]}, height: {max: frame[1]}});
    track.applyConstraints = async (c2) => { m.applied = c2; };
    m.tracks.push(track);
    return {frame, getTracks: () => [track], getVideoTracks: () => [track]};
  };
  return m;
}
function setup(opts = {}) {
  encodes = []; log = [];
  const doc = makeDoc(), key = new El("button"), stage = new El("div"), said = [], photos = [], store = {};
  const lamp = new El("i"); lamp.className = "lamp"; key.appendChild(lamp);
  const media = makeMedia(opts);
  const cam = Cam.attach({key, stage, doc, media, say: t => said.push(t), budget: () => opts.budget || 1100000,
    onPhoto: (url, size) => photos.push({url, size}), store: {getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; }}});
  return {doc, key, stage, said, photos, media, cam};
}
const settle = () => new Promise(r => setTimeout(r, 20));
const view = (s) => s.stage.children.find(c => c.className === "camview");

test("one tap opens the back camera asking for full resolution; a second takes the photo and stops the camera first", async () => {
  const s = setup({frame: [1280, 960]});
  s.key.click(); await settle();
  assert.ok(view(s), "the viewfinder is open");
  assert.equal(s.key.getAttribute("aria-pressed"), "true");
  assert.equal(s.media.asks[0].video.facingMode.ideal, "environment");
  assert.ok(s.media.asks[0].video.width.ideal >= 4000 && s.media.asks[0].audio === false);
  assert.deepEqual(s.media.applied, {width: {ideal: 1280}, height: {ideal: 960}});   // then the most the camera has
  s.key.click();
  assert.equal(s.photos.length, 1);
  assert.equal(view(s), undefined, "the viewfinder is gone");
  assert.ok(s.media.tracks[0].stopped, "the camera light is off");
  assert.ok(log.indexOf("stop") < log.indexOf("encode"), "stopped before the JPEG is made");
  assert.deepEqual(s.photos[0].size, {width: 1280, height: 960});                    // it fits: full size, not shrunk
  assert.equal(encodes[0].q, 0.92);
});

test("a big frame is made smaller only as far as the budget requires", async () => {
  const s = setup({frame: [4032, 3024], budget: 1100000});
  s.key.click(); await settle(); s.key.click();
  const [{url, size}] = s.photos;
  assert.ok(url.length <= 1100000);
  const largest = 4032 * Math.sqrt(1100000 / (4032 * 3024 * 0.5 * 0.85));             // the widest the fake JPEG can be and fit
  assert.ok(size.width <= largest && size.width > largest * 0.9, "shrunk, but not more than needed: " + size.width + " of " + Math.round(largest));
});

test("with no room left in the message it says so instead of sending", async () => {
  const s = setup({frame: [4032, 3024], budget: 1000});
  s.key.click(); await settle(); s.key.click();
  assert.equal(s.photos.length, 0);
  assert.match(s.said.join(" "), /No room/);
});

test("the flip key goes to the front camera, mirrored on screen only, and stops the back one", async () => {
  const s = setup({frame: [1280, 720]});
  s.key.click(); await settle();
  view(s).querySelector(".cam-flip").click(); await settle();
  assert.equal(s.media.asks[1].video.facingMode.ideal, "user");
  assert.ok(s.media.tracks[0].stopped);
  assert.ok(view(s).children[0].classList.contains("mirror"));
  view(s).querySelector(".cam-snap").click();
  assert.equal(s.photos.length, 1);
  assert.ok(s.media.tracks[1].stopped);
});

test("closing, leaving the page or Esc stops the camera; nothing is taken", async () => {
  for (const how of ["x", "hidden", "esc"]) {
    const s = setup();
    s.key.click(); await settle();
    if (how === "x") view(s).querySelector(".cam-close").click();
    if (how === "hidden") { s.doc.hidden = true; s.doc.fire("visibilitychange", {}); }
    if (how === "esc") s.doc.fire("keydown", {key: "Escape"});
    assert.equal(view(s), undefined, how);
    assert.ok(s.media.tracks[0].stopped, how);
    assert.equal(s.photos.length, 0, how);
  }
});

test("a camera another app holds (iPad: Discord) makes the key say so, and a tap tries again", async () => {
  const s = setup({fail: "NotReadableError"});
  s.key.click(); await settle();
  assert.equal(s.key.title, Cam.BUSY);
  assert.equal(s.key.querySelector(".lamp").dataset.s, "warn");
  assert.match(s.said.join(" "), /Discord/);
  s.key.click(); await settle();
  assert.equal(s.media.asks.length, 2, "tapped again: asked again");
});

test("a refused camera names the Safari setting", async () => {
  const s = setup({fail: "NotAllowedError"});
  s.key.click(); await settle();
  assert.match(s.key.title, /Website Settings > Camera > Allow/);
});

test("the one-app-at-a-time note shows on the first viewfinder only", async () => {
  const s = setup();
  s.key.click(); await settle();
  let note = view(s).children.find(c => c.className === "cam-note");
  assert.equal(note.hidden, false);
  assert.equal(note.textContent, Cam.ONCE);
  view(s).querySelector(".cam-close").click();
  s.key.click(); await settle();
  note = view(s).children.find(c => c.className === "cam-note");
  assert.equal(note.hidden, true);
});

test("fit keeps full size when it fits and never goes below a readable floor", () => {
  const enc = (w, h) => (k, q) => "x".repeat(Math.round(w * k * h * k * q * 0.5));
  assert.equal(Cam.fit(1000, 800, 1e6, enc(1000, 800)).scale, 1);
  assert.equal(Cam.fit(4000, 3000, 10, enc(4000, 3000)), null);
});
