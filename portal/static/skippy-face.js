/* Skippy's face, the pods' own, drawn in the browser (the Captain, 2026-10-31: "replace the chat's avatar with our hub avatar").
 *
 * A line-for-line port of services/satellite/face_render.py (itself a port of the pod's 50 ms face tick in rooms/skippy-room.yaml):
 * the can (static/skippy/face/0N.png, the five lean images, tinted), the hat with its gravity physics, eyes, brows, the mouth and its
 * pen strokes, the emotion effects, the thinking graphics and the ears. Nothing about how he looks is decided here: change the look in
 * the yaml / face_render.py and keep this in step. Left out on purpose, being the pod's screen and not his face: the nimbus frame, the
 * scrolling conversation, the dance, and the pod's key states (earplugs). The floating microphone shows only while the chat listens.
 *
 *   const f = new SkippyFace(); f.setState("speaking"); f.talking = true; f.talkT0 = now;
 *   each 50 ms: f.tick(now); f.render(ctx)        // ctx: a 480 x 480 canvas, transparent
 *
 * tick() needs no browser (tests run it under Node); render() needs a 2D context and the five can images (f.balls).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SkippyFace = factory();
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const W = 480, H = 480, GLIDE = 0.4;
  const ANGS = [-0.1396, -0.0698, 0.0, 0.0698, 0.1396];
  const EQS = ["E = mc^2", "a^2 + b^2 = c^2", "F = ma", "PV = nRT", "e^(i*pi) = -1", "dx/dt = v", "sin(x) / x", "x = (-b +- sqrt(D)) / 2a",
    "S >= 0", "1 + 1 = 2?", "div B = 0", "f(x) = x^2"];
  const STATE_RGB = { listening: 0x22d3ee, recording: 0xff3b6b, thinking: 0xf59e0b, speaking: 0xb05cff, music: 0x22c55e, muted: 0xef4444,
    offline: 0x475569, sick: 0xd6c92a };
  const MS = { X: [22, 0.6, 0.6, 0], A: [22, 0.3, 0.3, 0], B: [24, 3, 3, 1], C: [24, 8, 8, 1], D: [26, 13, 17, 1],
    E: [15, 10, 10, 0], F: [10, 7, 7, 0], G: [24, 3, 5, 1], H: [20, 6, 6, 0] };
  const EMO = { h: [0.78, 7, 0, 0, 0, 11, 0, 0, 0], e: [1.35, 16, 0, 0, 0, 12, 0, 0, 0], l: [0.28, 10, 0, 0, 0, 14, 0, 0, 0],
    s: [1.45, 18, 0, 0, 0, 0, 0, 0, 0], q: [1.05, 4, -0.08, 12, 0, 2, 0, 0.7, -0.3], a: [0.78, 0, 0.45, 0, 0, -3, 0, 0, 0],
    m: [0.62, 5, 0.1, 0, 12, 8, 5, 0.6, 0], d: [0.85, 3, -0.4, 0, 0, -7, 0, 0, 0.7], w: [1.1, 10, -0.35, 0, 0, -4, 0, 0, 0],
    b: [0.45, 0, 0, 0, 0, -1, 0, 0.8, 0.2], r: [0.9, 6, -0.2, 0, 0, 3, 0, -0.7, 0.5], v: [1.2, 8, 0, 0, 0, 11, 0, 0, 0],
    k: [0.7, 2, -0.3, 0, 0, -5, 0, 0, 0.5] };
  const WOB = { e: 1.0, l: 1.0, a: 0.9, s: 0.8, w: 0.7, h: 0.6, r: 0.6, v: 0.6, q: 0.5, m: 0.5, d: 0.25, b: 0.2 };

  const u32 = () => Math.floor(Math.random() * 4294967296);
  const trunc = Math.trunc;
  const roundf = v => Math.floor(Math.abs(v) + 0.5) * (v >= 0 ? 1 : -1);
  const clampi = v => { const i = trunc(v); return i < 0 ? 0 : (i > 479 ? 479 : i); };
  const isUpper = c => c >= "A" && c <= "Z";
  const hex = h => "#" + (h >>> 0).toString(16).padStart(6, "0");

  function Line(width, color, opa, rounded) { return { pts: [], width: width, color: color, opa: opa === undefined ? 255 : opa, rounded: !!rounded }; }
  function put(ln, pts) { ln.pts = pts.map(p => [trunc(p[0]), trunc(p[1])]); }
  function Box(w, h, color, o) {
    o = o || {};
    return { x: 0, y: 0, w: w, h: h, color: color, opa: o.opa === undefined ? 255 : o.opa, radius: o.radius || 0, border: o.border || 0,
      border_color: o.border_color || 0, hidden: !!o.hidden };
  }
  function Label(color) { return { text: "", color: color, opa: 0, x: 0, y: 0 }; }

  function SkippyFace() {
    // inputs (what the box has)
    this.state_now = ""; this.talking = false; this.talkT0 = 0; this.mouth = ""; this.emote = ""; this.in_session = false;
    this.showMic = false;                                 // the chat is listening (the pod shows it whenever its keys are on)
    this.face_rgb = 0x6a28c9;
    // tick statics
    this.ph = 0; this.eo = 1; this.bh = 0; this.ba = 0; this.bl = 0; this.br = 0; this.lx = 0; this.ly = 0; this.sm = 0;
    this.mw = 22; this.mu = 0.5; this.ml = 0.5; this.teethv = 0;
    this.blink_at = 0; this.glance_at = 0; this.gx = 0; this.gy = 0;
    this.as_ = 0; this.fb_ = 0; this.fs_ = 0; this.fv_ = 0; this.fh_ = 0;
    this.tr = 0; this.tg = 0; this.tb = 0; this.ta = 0; this.sent_sig = -1; this.rc = [0, 0, 0, 0];
    this.think_t0 = 0; this.pst = "";
    this.rk = 0; this.ramp = 3; this.bx_ = 0; this.by_ = 0; this.prev_bx = 0;
    this.next_beat = 0; this.next_idle = 0; this.wobs = 0.4; this.lean_k = 2; this.mg = 2.1;
    this.ha = 0; this.hv = 0; this.hx = 0; this.hxv = 0; this.hy = 0; this.hyv = 0; this.hc = 0; this.pv = 0; this.nudge_at = 0;
    this.ear = 0; this.sx = 0; this.sy = 0;
    this.sl = []; for (let i = 0; i < 6; i++) this.sl.push({ on: false, t0: 0, x: 0, y: 0 });
    this.prev_state_think = ""; this.next_spawn = 0; this.bulb_t0 = 0; this.bulb_was = false;
    // the widgets, in the yaml's z-order
    this.ball_pos = [90, 80];
    this.balls = null;                                    // [5 images], set by the page
    this._ballCache = new Map();
    this.ear_fl = Line(8, 0x7d8492, 0, true); this.ear_fr = Line(8, 0x7d8492, 0, true);
    this.ear_ol = Line(3, 0xe6e9ef, 0, true); this.ear_or = Line(3, 0xe6e9ef, 0, true);
    this.mic_cap = Line(26, 0xe6e9ef, 0, true); this.mic_arc = Line(4, 0xe6e9ef, 0, true); this.mic_stem = Line(4, 0xe6e9ef, 0, true);
    this.hat_cast = Line(20, 0x000000, trunc(255 * 0.55), true);
    this.cloth = []; for (let i = 0; i < 40; i++) this.cloth.push(Box(13, 2, 0x1a1f3a, { hidden: true }));
    this.hat_crease_l = Line(12, 0x0a0d20, trunc(255 * 0.7), true); this.hat_crease_r = Line(12, 0x0a0d20, trunc(255 * 0.7), true);
    this.hat_top = Line(3, 0x2e3560, 255, true); this.hat_bot = Line(4, 0x3d4478, 255, true);
    this.rose = [[52, 0xf2f2f2], [42, 0xcc2b3a], [30, 0xf2f2f2], [18, 0xcc2b3a]].map(([s, c]) => Box(s, s, c, { radius: Math.floor(s / 2) }));
    this.lace_edge = Line(19, 0x7a5a14, 255, true); this.lace = Line(13, 0xe3b53c, 255, true);
    this.rose_btn = Box(16, 16, 0xe3b53c, { radius: 8, border: 2, border_color: 0x7a5a14 });
    this.white = [Box(20, 20, 0xf7f7fa, { radius: 100, hidden: true }), Box(20, 20, 0xf7f7fa, { radius: 100, hidden: true })];
    this.blush = [Box(50, 24, 0xff6b81, { opa: 0, radius: 100 }), Box(50, 24, 0xff6b81, { opa: 0, radius: 100 })];
    this.mfill = Line(7, 0x0b0b0e, 255);
    this.eye_l = Line(5, 0x1b1d22, 255); this.eye_r = Line(5, 0x1b1d22, 255);
    this.brow_l = Line(5, 0x1b1d22, 255); this.brow_r = Line(5, 0x1b1d22, 255);
    this.mouth_ln = Line(5, 0x1b1d22, 255);
    this.teeth = Line(3, 0xeceef2, 0);
    this.chin = Line(4, 0x1b1d22, 0, true);
    this.smile = [Line(4, 0x1b1d22, 0, true), Line(4, 0x1b1d22, 0, true)];
    this.frown = [Line(4, 0x1b1d22, 0, true), Line(4, 0x1b1d22, 0, true)];
    this.furrow = [Line(4, 0x1b1d22, 0, true), Line(4, 0x1b1d22, 0, true)];
    this.iris = [0, 1].map(() => Box(20, 20, 0x2f7bd9, { radius: 100, border: 2, border_color: 0x1d4fa0, hidden: true }));
    this.sweat = Box(14, 20, 0x7dd3fc, { opa: 0, radius: 100 });
    this.vein = Line(5, 0xe11d48, 0, true); this.heart = Line(4, 0xf472b6, 0, true);
    this.pup = [Box(14, 14, 0x0b0b0e, { radius: 7 }), Box(14, 14, 0x0b0b0e, { radius: 7 })];
    this.glint = [Box(5, 5, 0xffffff, { radius: 2 }), Box(5, 5, 0xffffff, { radius: 2 })];
    this.eq = [0, 1, 2, 3].map(() => Label(0xf59e0b));
    this.gr = [Line(3, 0x22d3ee, 0, true), Line(3, 0xf59e0b, 0, true)];
    this.bulb_fill = Line(5, 0xfacc15, 0, true); this.bulb_out = Line(3, 0xfff7b0, 0, true);
    this.rays = [0, 1, 2, 3, 4].map(() => Line(4, 0xfde047, 0, true));
  }

  SkippyFace.prototype.setState = function (state) {
    let c = this.in_session ? 0x22d3ee : 0x6a28c9;
    if (state in STATE_RGB) c = STATE_RGB[state];
    this.face_rgb = c; this.state_now = state;
  };
  SkippyFace.prototype.setSession = function (on) {
    this.in_session = !!on;
    if (this.state_now === "idle" || this.state_now === "") this.setState("idle");
  };

  // ── the 50 ms face tick (face_render.Face.tick) ──
  SkippyFace.prototype.tick = function (now) {
    const st = this.state_now;
    const dead = st === "offline";
    const talk = this.talking;
    let mood = "n", vis = "X";
    const env = this.mouth;
    if (talk) {
      const i = Math.floor((now - this.talkT0) / 60) + 1;
      if (env && env[0] >= "a" && env[0] <= "z") mood = env[0];
      vis = i < env.length ? env[i] : "BCDBCEA"[Math.floor(now / 90) % 7];
      if (vis >= "0" && vis <= "9") vis = vis === "0" ? "X" : (vis < "3" ? "B" : (vis < "6" ? "C" : "D"));
    }
    const [tw, tu, tl, tt] = MS[vis] || [22, 0.6, 0.6, 0];
    let t_eo = 1, t_bh = 0, t_ba = 0, t_bl = 0, t_br = 0, t_lx = 0, t_ly = 0, t_sm = 3;
    if (st === "listening") { t_eo = 1.25; t_bh = 10; t_sm = 4; }
    else if (st === "recording") { t_eo = 1.3; t_bh = 14; t_sm = 1; }
    else if (st === "thinking") { t_eo = 0.9; t_bl = 10; t_ba = 0.15; t_lx = -0.8 + 0.2 * Math.sin(this.ph * 0.7); t_ly = -0.8; t_sm = -2; }
    else if (st === "music") { t_eo = 0.55; t_bh = 4; t_sm = 9; }
    else if (st === "muted") { t_eo = 0.08; t_sm = 0; }
    else if (st === "offline") { t_eo = 0; t_sm = -3; t_bh = 0; }
    else if (st === "speaking" || talk) {
      t_sm = 4;
      t_bh = 3 + 5 * ("DC".includes(vis) ? 1 : 0);
      if (mood === "h") { t_eo = 0.75; t_sm = 11; t_bh = 7; }
      else if (mood === "a") { t_eo = 0.8; t_ba = 0.4; t_sm = 3; t_br = -4; }
      else if (mood === "s") { t_eo = 1.4; t_bh = 14; }
      else if (mood === "q") { t_bl = 11; t_ba = -0.1; t_sm = 2; t_ly = -0.3; }
      else if (mood === "d") { t_sm = -6; t_ba = -0.35; t_eo = 0.85; t_bh = 2; }
    } else {
      if (!dead && now > this.glance_at) {
        this.gx = ((u32() % 100) / 50 - 1) * 0.6;
        this.gy = ((u32() % 100) / 50 - 1) * 0.3;
        this.glance_at = now + 1500 + u32() % 3500;
      }
      t_lx = this.gx; t_ly = this.gy;
      if (this.in_session) { t_eo = 1.12; t_bh = 4; t_sm = 4; }
    }
    // emotion
    let emo = "n", ef = 0, estrong = false;
    const em = this.emote;
    const react = em.length >= 2 && em[1] === "|" ? em[0] : "n";
    const etl = em.length >= 2 && em[1] === "|" ? em.length - 2 : 0;
    if (talk && etl > 0) {
      let i = Math.floor((now - this.talkT0) / 100);
      if (i >= etl) i = etl - 1;
      const c = em[2 + i];
      estrong = isUpper(c);
      emo = estrong ? c.toLowerCase() : c;
      ef = estrong ? 1.0 : 0.7;
    } else if (talk && mood !== "n") { emo = mood; ef = 0.8; }
    else if (react !== "n" && (st === "thinking" || st === "listening" || (st === "idle" && this.in_session))) { emo = react; ef = 0.55; }
    if (st === "sick") { emo = "k"; ef = 1.0; }
    let t_as = 0, g_b = 0, g_s = 0, g_v = 0, g_h = 0;
    if (emo !== "n") {
      if (EMO[emo]) {
        const [eo2, bh2, ba2, bl2, br2, sm2, as2, lx2, ly2] = EMO[emo];
        t_eo += (eo2 - t_eo) * ef; t_bh += (bh2 - t_bh) * ef; t_ba += (ba2 - t_ba) * ef; t_bl += (bl2 - t_bl) * ef; t_br += (br2 - t_br) * ef;
        t_sm += (sm2 - t_sm) * ef; t_as = as2 * ef; t_lx += (lx2 - t_lx) * ef; t_ly += (ly2 - t_ly) * ef;
      }
      if (emo === "r") g_b = ef;
      if (emo === "v") { g_b = 0.6 * ef; g_h = ef; }
      if (emo === "w" || emo === "k") g_s = ef;
      if (emo === "a") g_v = estrong ? 1.0 : 0.45 * ef;
    }
    this.fb_ += (g_b - this.fb_) * 0.25; this.fs_ += (g_s - this.fs_) * 0.25; this.fv_ += (g_v - this.fv_) * 0.3;
    this.fh_ += (g_h - this.fh_) * 0.25; this.as_ += (t_as - this.as_) * 0.3;
    // the can's tint
    if (st === "thinking" && this.pst !== "thinking") this.think_t0 = now;
    this.pst = st;
    let R = 0, G = 0, B = 0, A = 0;
    if (st === "offline") { R = 47; G = 107; B = 255; A = 120; }
    else if (st === "sick" || emo === "k") { R = 222; G = 215; B = 40; A = 105; }
    else if (st === "thinking") { const k = Math.min((now - this.think_t0) / 4000, 1); R = 255; G = 138; B = 31; A = 30 + 90 * k; }
    else if (emo === "a") { R = 255; G = 42; B = 42; A = estrong ? 130 : 80; }
    if (A > 0) { this.tr += (R - this.tr) * 0.3; this.tg += (G - this.tg) * 0.3; this.tb += (B - this.tb) * 0.3; }
    this.ta += (A - this.ta) * 0.15;
    const qa = trunc(this.ta / 12) * 12;
    const sig = qa + (trunc(this.tr) >> 5) * 1000 + (trunc(this.tg) >> 5) * 10000 + (trunc(this.tb) >> 5) * 100000;
    if (sig !== this.sent_sig) { this.sent_sig = sig; this.rc = [trunc(this.tr), trunc(this.tg), trunc(this.tb), qa]; }
    // blink and ease
    if (now > this.blink_at) { this.blink_at = now + 2500 + u32() % 3500; this.bl = 0; }
    const blinking = (!dead && now + 150 > this.blink_at && now < this.blink_at) ? 0.1 : 1.0;
    const e = 0.35;
    this.eo += (t_eo * 0.8 - this.eo) * 0.3; this.bh += (t_bh - this.bh) * e; this.ba += (t_ba - this.ba) * e;
    this.bl += (t_bl - this.bl) * e; this.br += (t_br - this.br) * e;
    this.lx += (t_lx - this.lx) * 0.25; this.ly += (t_ly - this.ly) * 0.25; this.sm += (t_sm - this.sm) * 0.3;
    this.mw += (tw - this.mw) * 0.55; this.mu += (tu - this.mu) * 0.55; this.ml += (tl - this.ml) * 0.55; this.teethv += (tt - this.teethv) * 0.5;
    this.ph += talk ? 0.13 : 0.06;
    // the can's rocking
    let beat = false, wob = 0.4;
    if (talk) {
      let c0 = "n";
      if (em.length >= 3 && em[1] === "|") {
        const tl0 = em.length - 2;
        let i0 = Math.floor((now - this.talkT0) / 100);
        if (i0 >= tl0) i0 = tl0 - 1;
        c0 = em[2 + i0];
      }
      const st0 = isUpper(c0);
      const lc = st0 ? c0.toLowerCase() : c0;
      wob = (lc in WOB ? WOB[lc] : 0.35) * (st0 ? 1.0 : 0.7);
    }
    this.wobs += (wob - this.wobs) * 0.2;
    this.ramp += ((dead ? 0 : (talk ? 4 + 19 * this.wobs : 3)) - this.ramp) * 0.15;
    let ox, oy;
    if (talk) {
      if (now > this.next_beat) {
        this.next_beat = now + trunc(270 - 120 * this.wobs) + u32() % 70;
        this.rk += 1.7 + (u32() % 100) / 60;
        this.bx_ = Math.sin(this.rk) * this.ramp;
        this.by_ = -Math.abs(Math.sin(this.rk * 0.7)) * this.ramp * 0.55;
        beat = true;
      }
      ox = this.bx_; oy = this.by_;
    } else {
      if (dead) { this.bx_ = 0; this.by_ = 0; } else this.rk += 0.05;
      if (!dead && now > this.next_idle) {
        this.next_idle = now + 450;
        this.bx_ = roundf(Math.sin(this.rk * 9) * this.ramp);
        this.by_ = roundf(-Math.abs(Math.sin(this.rk * 9)) * this.ramp * 0.35);
      }
      ox = this.bx_; oy = this.by_;
    }
    const rkv = ox - this.prev_bx;
    this.prev_bx = ox;
    this.sx += (ox - this.sx) * GLIDE; this.sy += (oy - this.sy) * GLIDE;
    ox = this.sx; oy = this.sy;
    const k = 2 + trunc(roundf(ox / Math.max(this.ramp, 1.0) * 2.0));
    this.lean_k = Math.min(4, Math.max(0, k));
    const theta = ANGS[this.lean_k], lt = Math.tan(theta);
    const cx = 240 + ox, cy = 245 + oy;
    this.ball_pos = [90 + trunc(roundf(ox)), 80 + trunc(roundf(oy))];
    const pivotY = cy + 140;
    // eyes
    const eo = this.eo;
    const eye = (ex, ey) => {
      const w = 25, h = Math.max(22 * eo * blinking, 2);
      const pts = [];
      for (let i = 0; i < 18; i++) { const a = i * 6.2832 / 17; pts.push([clampi(ex + w * Math.cos(a)), clampi(ey + h * Math.sin(a))]); }
      return pts;
    };
    const ey = cy - 36;
    const cxe = cx + (pivotY - ey) * lt;
    const exl = cxe - 52, exr = cxe + 52;
    put(this.eye_l, eye(exl, ey)); put(this.eye_r, eye(exr, ey));
    const opn = eo * blinking > 0.3;
    const ew = 25, eh = 22 * eo * blinking;
    for (let s2 = 0; s2 < 2; s2++) {
      const wh = this.white[s2], ir = this.iris[s2], exx = s2 ? exr : exl;
      if (eh > 6) {
        wh.hidden = false; wh.w = trunc(2 * ew - 4); wh.h = trunc(2 * eh - 4); wh.x = trunc(exx - ew + 2); wh.y = trunc(ey - eh + 2);
        const d = trunc(32 * Math.min(1, eh / (22 * 0.9)));
        ir.hidden = false; ir.w = ir.h = d; ir.x = trunc(exx + this.lx * 8 - d / 2); ir.y = trunc(ey + this.ly * 6 - d / 2);
      } else { wh.hidden = ir.hidden = true; }
    }
    for (let s2 = 0; s2 < 2; s2++) {
      const pu = this.pup[s2], gl = this.glint[s2], exx = s2 ? exr : exl;
      if (opn) {
        pu.hidden = gl.hidden = false;
        pu.x = trunc(exx + this.lx * 8 - 7); pu.y = trunc(ey + this.ly * 6 - 7);
        gl.x = trunc(exx + this.lx * 8 - 1); gl.y = trunc(ey + this.ly * 8 - 7);
      } else { pu.hidden = gl.hidden = true; }
    }
    // brows
    const brow = (bx, side, extra) => {
      const y0 = cy - (36 + 36 * eo * 0.45 + 14 + this.bh + extra);
      const sl = this.ba * 18 * side;
      return [[clampi(bx - 26), clampi(y0 + (side < 0 ? sl : -sl) * -1)],
        [clampi(bx - 9), clampi(y0 + (side < 0 ? -sl : sl) * 0.3 - 3)],
        [clampi(bx + 9), clampi(y0 + (side < 0 ? sl : -sl) * 0.3 - 3)],
        [clampi(bx + 26), clampi(y0 + (side < 0 ? sl : -sl) * -1 * -1)]];
    };
    put(this.brow_l, brow(exl, -1, this.bl)); put(this.brow_r, brow(exr, 1, this.br));
    // mouth
    this.mg += ((talk ? 3.1 : 2.1) - this.mg) * 0.3;
    const my = cy + 52;
    const MW = this.mw * this.mg, MU = this.mu * this.mg, ML = this.ml * this.mg;
    const cxm = cx + (pivotY - my) * lt;
    const sm = this.sm;
    const mo = [];
    for (let i = 0; i < 12; i++) { const u = -1 + i * 2 / 11, edge = 1 - u * u; mo.push([clampi(cxm + MW * u), clampi(my - MU * edge - sm * (1 - edge) * 0.5 - this.as_ * u)]); }
    for (let i = 0; i < 12; i++) { const u = 1 - i * 2 / 11, edge = 1 - u * u; mo.push([clampi(cxm + MW * u), clampi(my + ML * edge - sm * (1 - edge) * 0.5 - this.as_ * u)]); }
    mo.push(mo[0]);
    put(this.mouth_ln, mo);
    const mf = [];
    if (MU + ML > 3) {
      const top = my - MU, bot = my + ML;
      let ltr = true, yy = top + 1.5;
      while (yy < bot - 1 && mf.length < 62) {
        let v = (yy - my) < 0 ? (my - yy) / MU : (yy - my) / ML;
        v = Math.min(v, 1.0);
        const half = MW * Math.sqrt(Math.max(1 - v * v, 0)) * 0.97;
        mf.push([clampi(cxm + (ltr ? -half : half)), clampi(yy)]);
        mf.push([clampi(cxm + (ltr ? half : -half)), clampi(yy)]);
        ltr = !ltr; yy += 5.5;
      }
    }
    if (mf.length >= 2) { put(this.mfill, mf); this.mfill.opa = 255; } else this.mfill.opa = 0;
    const ty = clampi(my - MU * 0.35);
    put(this.teeth, [[clampi(cxm - MW * 0.7), ty], [clampi(cxm + MW * 0.7), ty]]);
    this.teeth.opa = (this.teethv > 0.5 && this.mu + this.ml > 4) ? 150 : 0;
    // extra pen strokes
    const sc = sm > 3 ? Math.min((sm - 3) / 8, 1) : 0;
    const fc2 = sm < -2 ? Math.min((-sm - 2) / 6, 1) : 0;
    const cw = 14 + MW * 0.18, cyy = my + ML + 13;
    const chin = [];
    for (let i = 0; i < 7; i++) { const t = -1 + i / 3; chin.push([clampi(cxm + cw * t), clampi(cyy + 5 * (1 - t * t))]); }
    put(this.chin, chin);
    this.chin.opa = st === "muted" ? 0 : 230;
    for (let sd = 0; sd < 2; sd++) {
      const side = sd ? 1 : -1;
      const kx = cxm + side * MW, ky = my - sm * 0.5;
      if (sc > 0.05) {
        const half = (MU + ML) * 0.5 + 10 + 12 * sc, mid = my + (ML - MU) * 0.5 - sm * 0.2;
        const pts = [];
        for (let i = 0; i < 7; i++) { const t = -1 + i / 3; pts.push([clampi(kx + side * (7 + 11 * (1 - t * t) * (0.5 + 0.5 * sc))), clampi(mid + t * half)]); }
        put(this.smile[sd], pts); this.smile[sd].opa = trunc(255 * sc);
      } else this.smile[sd].opa = 0;
      if (fc2 > 0.05) {
        const pts = [];
        for (let i = 0; i < 7; i++) { const t = i / 6; pts.push([clampi(kx + side * (6 + 9 * Math.sin(3.14159 * t * 0.8))), clampi(ky + 6 + 40 * fc2 * t)]); }
        put(this.frown[sd], pts); this.frown[sd].opa = trunc(255 * fc2);
      } else this.frown[sd].opa = 0;
    }
    const fu_a = this.ba > 0.12 ? Math.min(this.ba / 0.4, 1) : 0;
    for (let sd = 0; sd < 2; sd++) {
      if (fu_a > 0.05) {
        const fx = cxe + (sd ? 8 : -8), fy = cy - (36 + 36 * eo * 0.45 + 14 + this.bh) - 4;
        put(this.furrow[sd], [[clampi(fx), clampi(fy)], [clampi(fx + (sd ? 2 : -2)), clampi(fy + 24 * fu_a)]]);
        this.furrow[sd].opa = trunc(255 * fu_a);
      } else this.furrow[sd].opa = 0;
    }
    this._hat(now, talk, beat, rkv, theta, lt, cx, cy, pivotY, dead);
    this._effects(now, cx, cy);
    this._thinking(now, st);
    this._ears(st, talk, cx, cy, pivotY, lt);
  };

  SkippyFace.prototype._hat = function (now, talk, beat, rkv, theta, lt, cx, cy, pivotY, dead) {
    this.hc += 1;
    if (dead || !(this.hc % 4 === 0 || beat)) return;
    const ax = rkv - this.pv;
    this.pv = rkv;
    for (let sub = 0; sub < 4; sub++) {
      let tq = 0.032 * Math.sin(this.ha + theta) - (sub === 0 ? ax : 0) * 0.0035 * Math.cos(this.ha);
      if (beat && sub === 0) tq += (u32() % 200 - 100) / 100 * 0.012 * (0.4 + 1.2 * this.wobs);
      this.hv += tq - this.hv * 0.02;
      this.ha += this.hv;
      if (!talk && now > this.nudge_at) { this.nudge_at = now + 5000 + u32() % 6000; this.hv += this.ha > 0 ? -0.1 : 0.1; }
      if (this.ha > 0.45) { this.ha = 0.45; if (this.hv > 0.04) this.hyv += 3; this.hv = -this.hv * 0.3; }
      if (this.ha < -0.45) { this.ha = -0.45; if (this.hv < -0.04) this.hyv += 3; this.hv = -this.hv * 0.3; }
      this.hxv += (this.ha * 60 - this.hx) * 0.08 - this.hxv * 0.2 - (sub === 0 ? ax : 0) * 0.35;
      this.hx += this.hxv;
      this.hyv += (-this.hy) * 0.14 - this.hyv * 0.22;
      this.hy += this.hyv;
      if (this.hx > 40) { this.hx = 40; this.hxv = -this.hxv * 0.5; }
      if (this.hx < -40) { this.hx = -40; this.hxv = -this.hxv * 0.5; }
      if (this.hy > 6) this.hy = 6;
      if (this.hy < -10) this.hy = -10;
    }
    const hw = 285;
    const py = cy - 62 + this.hy, px = cx + (pivotY - (cy - 62)) * lt + this.hx;
    const ca = Math.cos(this.ha + theta), sa = Math.sin(this.ha + theta);
    const rot = (lx, ly) => [trunc(px + lx * ca - ly * sa), trunc(py + lx * sa + ly * ca)];
    const profile = u => {
      const au = Math.abs(u), tip = 44 * u * u;
      return [tip - 152 * Math.pow(1 - Math.pow(au, 2.3), 1.25) - 42 * Math.exp(-(u * u) / 0.11), tip - 24 * (1 - u * u)];
    };
    const ht = [], hb = [];
    for (let i = 0; i < 41; i++) { const u = -1 + i * 2 / 40, [top, bot] = profile(u); ht.push(rot(u * hw, top)); hb.push(rot(u * hw, bot)); }
    put(this.hat_cast, hb.map(p => [p[0], p[1] + 13]));
    const cr = [];
    for (let sd = 0; sd < 2; sd++) {
      const uu = sd ? 0.30 : -0.30, [tp, bt2] = profile(uu), line = [];
      for (let i = 0; i < 10; i++) line.push(rot(uu * hw * (1 + 0.10 * (i / 9)), bt2 + (tp - bt2) * (0.92 * (i / 9))));
      cr.push(line);
    }
    const yat = (p, x) => {
      for (let i = 0; i < p.length - 1; i++) {
        const x0 = p[i][0], x1 = p[i + 1][0];
        if ((x0 <= x && x < x1) || (x1 <= x && x < x0)) return p[i][1] + (x - x0) / (x1 - x0) * (p[i + 1][1] - p[i][1]);
      }
      return NaN;
    };
    for (let c = 0; c < 40; c++) {
      const xc = c * 12 + 6;
      let yt = yat(ht, xc), yb = yat(hb, xc);
      const b = this.cloth[c];
      if (isNaN(yt) || isNaN(yb)) { b.hidden = true; continue; }
      if (yt > yb) { const t = yt; yt = yb; yb = t; }
      b.hidden = false; b.x = c * 12; b.y = trunc(yt) - 1; b.w = 13; b.h = trunc(yb - yt) + 2;
    }
    put(this.hat_crease_l, cr[0]); put(this.hat_crease_r, cr[1]);
    put(this.hat_top, ht); put(this.hat_bot, hb);
    const c0 = rot(hw * 0.34, -52);
    [52, 42, 30, 18].forEach((rs, i) => { this.rose[i].x = c0[0] - Math.floor(rs / 2); this.rose[i].y = c0[1] - Math.floor(rs / 2); });
    const p0 = rot(hw * 0.34 - 30, -52 + 36), p1 = rot(hw * 0.34 + 36, -52 - 44);
    put(this.lace_edge, [p0, p1]); put(this.lace, [p0, p1]);
    this.rose_btn.x = p0[0] - 8; this.rose_btn.y = p0[1] - 8;
  };

  SkippyFace.prototype._effects = function (now, cx, cy) {
    for (let sd = 0; sd < 2; sd++) { const b = this.blush[sd]; b.x = trunc(cx + (sd ? 62 : -112)); b.y = trunc(cy + 6); b.opa = trunc(170 * this.fb_); }
    this.sweat.x = trunc(cx + 96); this.sweat.y = trunc(cy - 64 + Math.floor(now / 120) % 8); this.sweat.opa = trunc(235 * this.fs_);
    const vs = 1 + 0.25 * Math.sin(now / 90), vx = cx - 104, vy = cy - 92;
    put(this.vein, [[clampi(vx), clampi(vy)], [clampi(vx + 18 * vs), clampi(vy + 18 * vs)], [clampi(vx + 9 * vs), clampi(vy + 9 * vs)],
      [clampi(vx + 18 * vs), clampi(vy)], [clampi(vx), clampi(vy + 18 * vs)]]);
    this.vein.opa = trunc(255 * this.fv_);
    if (this.fh_ > 0.05) {
      const hr = (now % 1800) / 1800, hx0 = cx + 100 + 8 * Math.sin(hr * 9), hy0 = cy - 70 - 90 * hr, pts = [];
      for (let i = 0; i < 26; i++) {
        const t = i * 6.2832 / 25;
        pts.push([trunc(hx0 + 0.9 * 16 * Math.pow(Math.sin(t), 3)),
          trunc(hy0 - 0.9 * (13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)))]);
      }
      put(this.heart, pts); this.heart.opa = trunc(255 * this.fh_ * (1 - hr * 0.8));
    } else this.heart.opa = 0;
  };

  SkippyFace.prototype._thinking = function (now, st) {
    const thinking = st === "thinking";
    if (this.prev_state_think === "thinking" && !thinking) this.bulb_t0 = now;
    this.prev_state_think = st;
    if (thinking && now > this.next_spawn) {
      for (let k = 0; k < 6; k++) {
        const s = this.sl[k];
        if (!s.on) {
          s.on = true; s.t0 = now;
          if (k < 4) {
            const t = EQS[u32() % 12];
            this.eq[k].text = t;
            const w = Math.min(t.length * 11, 440);
            s.x = 10 + u32() % (460 - w); s.y = 404 + u32() % 36;
          } else { s.x = k === 4 ? 14 : 402; s.y = 90 + u32() % 190; }
          this.next_spawn = now + 300 + u32() % 300;
          break;
        }
      }
    }
    for (let k = 0; k < 6; k++) {
      const s = this.sl[k];
      if (!s.on) continue;
      const age = now - s.t0, life = 1700;
      if (age > life) { s.on = false; if (k < 4) this.eq[k].opa = 0; else this.gr[k - 4].opa = 0; continue; }
      const a = age < 250 ? age / 250 : (age > life - 800 ? (life - age) / 800 : 1);
      const rise = trunc(age * 0.012);
      if (k < 4) {
        this.eq[k].x = s.x; this.eq[k].y = s.y + (s.y < 200 ? -rise : rise); this.eq[k].opa = trunc(255 * a);
      } else {
        const g = k - 4, typ = Math.floor(s.t0 / 7) % 4, w = 64, h = 44;
        const draw = Math.min(age / 900, 1), ox = s.x, oy = s.y - rise;
        const pts = [[0, 0], [0, h], [w, h], [0, h]];
        const m = trunc(22 * draw);
        for (let i = 0; i < m && pts.length < 28; i++) {
          const u = i / 21;
          const v = typ === 0 ? 0.5 + 0.4 * Math.sin(u * 9) : typ === 1 ? 1 - 4 * (u - 0.5) ** 2 : typ === 2 ? u * u
            : (i % 6 < 3 ? 0.3 + 0.1 * (i % 6) : 0.9 - 0.15 * (i % 6));
          pts.push([trunc(u * w), trunc(h - v * (h - 4))]);
        }
        put(this.gr[g], pts.map(([x, y]) => [clampi(ox + x), clampi(oy + y)]));
        this.gr[g].opa = trunc(230 * a);
      }
    }
    const bage = now - this.bulb_t0;
    if (this.bulb_t0 !== 0 && bage < 2000) {
      const k = bage < 280 ? (bage / 280) * 1.25 : (bage < 400 ? 1.25 - (bage - 280) / 120 * 0.25 : 1);
      const fade = bage > 1500 ? (2000 - bage) / 500 : 1;
      const bx = 372, by = 96, r = 22 * k;
      const bo = [];
      for (let i = 0; i < 19; i++) bo.push([clampi(bx + r * Math.cos(i * 6.2832 / 18)), clampi(by + r * Math.sin(i * 6.2832 / 18))]);
      bo.push([clampi(bx - r * 0.38), clampi(by + r * 1.4)], [clampi(bx + r * 0.38), clampi(by + r * 1.4)], [clampi(bx + r * 0.38), clampi(by + r * 0.85)]);
      const bf = [];
      let ltr = true, yy = by - r + 3;
      while (yy < by + r - 2 && bf.length < 22) {
        const half = Math.sqrt(Math.max(r * r - (yy - by) ** 2, 0)) * 0.9;
        bf.push([clampi(bx + (ltr ? -half : half)), clampi(yy)]);
        bf.push([clampi(bx + (ltr ? half : -half)), clampi(yy)]);
        ltr = !ltr; yy += 5;
      }
      put(this.bulb_out, bo); put(this.bulb_fill, bf);
      this.bulb_out.opa = trunc(255 * fade); this.bulb_fill.opa = trunc(220 * fade);
      [-2.9, -2.3, -1.5708, -0.85, -0.25].forEach((ang, i) => {
        const c2 = Math.cos(ang), s3 = Math.sin(ang);
        put(this.rays[i], [[clampi(bx + c2 * (r + 8)), clampi(by + s3 * (r + 8))], [clampi(bx + c2 * (r + 8 + 14 * k)), clampi(by + s3 * (r + 8 + 14 * k))]]);
        this.rays[i].opa = trunc(255 * fade * (bage > 150 ? 1 : 0));
      });
      this.bulb_was = true;
    } else if (this.bulb_was) {
      this.bulb_was = false;
      this.bulb_out.opa = this.bulb_fill.opa = 0;
      this.rays.forEach(ry => { ry.opa = 0; });
    }
  };

  SkippyFace.prototype._ears = function (st, talk, cx, cy, pivotY, lt) {
    const hearing = st === "listening" || st === "recording" || (st === "idle" && this.in_session && !talk) || (st === "" && this.in_session);
    this.ear += ((hearing ? 1 : 0) - this.ear) * 0.3;
    const fo = [this.ear_fl, this.ear_fr, this.ear_ol, this.ear_or];
    this._mic(st);
    if (this.ear < 0.06) { fo.forEach(ln => { ln.opa = 0; }); return; }
    for (let sd = 0; sd < 2; sd++) {
      const s2 = sd ? 1 : -1;
      const a = (sd ? 0.95 : 0.45) + 0.12 * Math.sin(this.ph * 2.3 + sd * 2) + 0.2 * (st === "recording" ? 1 : 0);
      const rot = s2 * a, by = cy - 4, bx = cx + (pivotY - by) * lt + s2 * 104;
      const ew = 21 * this.ear, eh = 44 * this.ear;
      const ux = Math.sin(rot), uy = -Math.cos(rot), rx = Math.cos(rot), ry = Math.sin(rot);
      const c0x = bx + ux * eh * 0.7, c0y = by + uy * eh * 0.7;
      const outl = [];
      for (let i = 0; i < 26; i++) {
        const t = i * 6.2832 / 25, lx = ew * Math.cos(t), lu = eh * Math.sin(t);
        outl.push([trunc(c0x + rx * lx + ux * lu), trunc(c0y + ry * lx + uy * lu)]);
      }
      const fill = [];
      let lr = true, u = -(eh - 8);
      while (u <= eh - 8 && fill.length < 24) {
        const half = Math.max((ew - 7) * Math.sqrt(Math.max(1 - (u / (eh - 5)) ** 2, 0)), 1);
        const l0 = lr ? -half : half, l1 = lr ? half : -half;
        lr = !lr;
        fill.push([trunc(c0x + rx * l0 + ux * u), trunc(c0y + ry * l0 + uy * u)]);
        fill.push([trunc(c0x + rx * l1 + ux * u), trunc(c0y + ry * l1 + uy * u)]);
        u += 7;
      }
      put(fo[sd], fill); put(fo[2 + sd], outl);
      fo[sd].opa = fo[2 + sd].opa = 255;
    }
  };

  SkippyFace.prototype._mic = function (st) {
    const parts = [this.mic_cap, this.mic_arc, this.mic_stem];
    if (!this.showMic || st === "offline") { parts.forEach(ln => { ln.opa = 0; }); return; }
    const my0 = 340 + 7 * Math.sin(this.ph * 1.3), mx0 = 425;
    put(this.mic_cap, [[mx0, my0 - 16], [mx0, my0 + 6]]);
    const arc = [];
    for (let i = 0; i < 10; i++) arc.push([mx0 + 20 * Math.cos(Math.PI * i / 9 + Math.PI), my0 + 4 - 20 * Math.sin(Math.PI * i / 9 + Math.PI)]);
    put(this.mic_arc, arc);
    put(this.mic_stem, [[mx0, my0 + 24], [mx0, my0 + 40]]);
    parts.forEach(ln => { ln.opa = 255; });
  };

  // ── drawing, in the yaml's order (face_render.Face.render), onto a transparent 480 x 480 canvas ──
  function line(ctx, ln) {
    if (ln.opa <= 0 || ln.pts.length < 2) return;
    ctx.globalAlpha = ln.opa / 255;
    ctx.strokeStyle = hex(ln.color); ctx.lineWidth = ln.width; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(ln.pts[0][0], ln.pts[0][1]);
    for (let i = 1; i < ln.pts.length; i++) ctx.lineTo(ln.pts[i][0], ln.pts[i][1]);
    ctx.stroke();
  }
  function box(ctx, b) {
    if (b.hidden || b.opa <= 0 || b.w <= 0 || b.h <= 0) return;
    ctx.globalAlpha = b.opa / 255;
    if (b.radius >= 100 || (b.radius > 0 && b.radius * 2 >= Math.min(b.w, b.h))) {
      const cx = b.x + Math.floor(b.w / 2), cy = b.y + Math.floor(b.h / 2), rx = Math.max(1, Math.floor(b.w / 2)), ry = Math.max(1, Math.floor(b.h / 2));
      ctx.fillStyle = hex(b.border ? b.border_color : b.color);
      ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.fill();
      if (b.border) {
        ctx.fillStyle = hex(b.color);
        ctx.beginPath(); ctx.ellipse(cx, cy, Math.max(1, rx - b.border), Math.max(1, ry - b.border), 0, 0, Math.PI * 2); ctx.fill();
      }
    } else { ctx.fillStyle = hex(b.color); ctx.fillRect(b.x, b.y, b.w, b.h); }
  }

  SkippyFace.prototype._ball = function () {
    const [r, g, b, qa] = this.rc, key = this.lean_k + ":" + r + ":" + g + ":" + b + ":" + qa;
    let img = this._ballCache.get(key);
    if (!img) {
      if (this._ballCache.size > 40) this._ballCache.clear();
      const src = this.balls[this.lean_k];
      img = document.createElement("canvas");
      img.width = src.naturalWidth || src.width; img.height = src.naturalHeight || src.height;
      const c = img.getContext("2d");
      c.drawImage(src, 0, 0);
      if (qa > 0) {                                       // the tint, mixed into the can's own pixels only (its alpha kept)
        c.globalCompositeOperation = "source-atop";
        c.fillStyle = "rgba(" + r + "," + g + "," + b + "," + (qa / 255) + ")";
        c.fillRect(0, 0, img.width, img.height);
      }
      this._ballCache.set(key, img);
    }
    return img;
  };

  SkippyFace.prototype.render = function (ctx) {
    ctx.save();
    ctx.clearRect(0, 0, W, H);
    for (const ln of [this.ear_fl, this.ear_fr, this.ear_ol, this.ear_or, this.mic_cap, this.mic_arc, this.mic_stem]) line(ctx, ln);
    if (this.balls) { ctx.globalAlpha = 1; ctx.drawImage(this._ball(), this.ball_pos[0], this.ball_pos[1]); }
    line(ctx, this.hat_cast);
    for (const b of this.cloth) box(ctx, b);
    for (const ln of [this.hat_crease_l, this.hat_crease_r, this.hat_top, this.hat_bot]) line(ctx, ln);
    for (const b of this.rose) box(ctx, b);
    line(ctx, this.lace_edge); line(ctx, this.lace); box(ctx, this.rose_btn);
    for (const b of this.white) box(ctx, b);
    for (const b of this.blush) box(ctx, b);
    for (const ln of [this.mfill, this.eye_l, this.eye_r, this.brow_l, this.brow_r, this.mouth_ln, this.teeth, this.chin, ...this.smile, ...this.frown, ...this.furrow]) line(ctx, ln);
    for (const b of this.iris) box(ctx, b);
    box(ctx, this.sweat); line(ctx, this.vein); line(ctx, this.heart);
    for (const b of this.pup) box(ctx, b);
    for (const b of this.glint) box(ctx, b);
    ctx.font = "20px 'Segoe UI', Arial, sans-serif"; ctx.textBaseline = "top";
    for (const lb of this.eq) {
      if (lb.opa > 0 && lb.text) { ctx.globalAlpha = lb.opa / 255; ctx.fillStyle = hex(lb.color); ctx.fillText(lb.text, lb.x, lb.y); }
    }
    for (const ln of [...this.gr, this.bulb_fill, this.bulb_out, ...this.rays]) line(ctx, ln);
    ctx.restore();
  };

  SkippyFace.W = W; SkippyFace.H = H;
  return SkippyFace;
}));
