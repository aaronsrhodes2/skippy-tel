// Skippy's face in the chat corner (portal/static/skippy-face.js, a port of services/satellite/face_render.py): its tick, under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const SkippyFace = require("../../static/skippy-face.js");

function run(f, ms, t0 = 0) { let t = t0; for (; t < t0 + ms; t += 50) f.tick(t); return t; }

test("idle: the can sits upright, the mouth is a line, no tint, no thinking graphics", () => {
  const f = new SkippyFace();
  f.setState("idle");
  run(f, 3000);
  assert.equal(f.face_rgb, 0x6a28c9);
  assert.equal(f.rc[3], 0);
  assert.ok(f.mu * f.mg < 3, "mouth closed");
  assert.ok(f.eq.every(l => l.opa === 0));
});

test("speaking: the mouth opens and the can rocks", () => {
  const f = new SkippyFace();
  f.setState("speaking"); f.talking = true; f.talkT0 = 0;
  let wide = 0, lean = new Set();
  for (let t = 0; t < 4000; t += 50) { f.tick(t); wide = Math.max(wide, f.mu + f.ml); lean.add(f.lean_k); }
  assert.ok(wide > 10, "the mouth opens: " + wide);
  assert.ok(lean.size > 1, "the can leans");
});

test("thinking: amber, the can tints over four seconds, equations rise", () => {
  const f = new SkippyFace();
  f.setState("thinking");
  run(f, 5000);
  assert.equal(f.face_rgb, 0xf59e0b);
  assert.ok(f.rc[3] > 60 && f.rc[0] > 200, "tinted orange: " + f.rc);
  assert.ok(f.eq.some(l => l.opa > 0 && l.text), "an equation is showing");
});

test("listening: ears up and the microphone beside him", () => {
  const f = new SkippyFace();
  f.setSession(true); f.showMic = true; f.setState("listening");
  run(f, 2000);
  assert.equal(f.face_rgb, 0x22d3ee);
  assert.ok(f.ear > 0.9 && f.ear_ol.opa === 255 && f.mic_cap.opa === 255);
  f.showMic = false; f.setSession(false); f.setState("idle");
  run(f, 2000, 2000);
  assert.ok(f.ear < 0.06 && f.mic_cap.opa === 0);
});

test("the hat is drawn every frame and stays on his head", () => {
  const f = new SkippyFace();
  f.setState("speaking"); f.talking = true;
  for (let t = 0; t < 6000; t += 50) {
    f.tick(t);
    assert.ok(Math.abs(f.ha) <= 0.45 && Math.abs(f.hx) <= 40, "hat within its limits");
  }
  assert.ok(f.hat_top.pts.length === 41 && f.cloth.some(b => !b.hidden));
});
