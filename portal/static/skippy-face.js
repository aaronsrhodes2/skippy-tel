/* Skippy the Magnificent — 2D canvas face.
 *
 * Draws a small animated face on a canvas element: a coloured ball (the
 * can), eyes, mouth, brows, and a pulsing ring light.  Driven by the same
 * state machine as the Python Face class in face_render.py (states, blink,
 * mouth, emotion targets) but rendered entirely in the browser — no backend
 * service, no stream.
 *
 * Mouth animation: when speaking, the mouth opens wider with the audio
 * level from the reply audio.  The caller passes the level (0..1) each
 * frame via face.mouthLevel = value.
 *
 * Usage:
 *   const face = new Face(canvasEl);
 *   face.set_state("thinking");
 *   // in your animation loop or requestAnimationFrame:
 *   face.draw(ctx);
 *   // while speaking:
 *   face.mouthLevel = audioLevel;  // 0..1 from an AnalyserNode
 */

export class Face {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.W = canvas.width || 160;
    this.H = canvas.height || 160;
    this._dirty = true;

    /* State machine (mirrors face_render.py Face) */
    this.state = "idle";
    this._phase = 0;

    /* Blink */
    this._blinkAt = Date.now() + 2000 + Math.random() * 3000;
    this._blinkOpen = 1;

    /* Easing targets — smooth transitions like the Python GLIDE */
    this._eo = 1;    /* eye openness  (1 = open, 0 = closed) */
    this._bh = 0;    /* brow height offset */
    this._ba = 0;    /* brow angle (negative = raised) */
    this._sm = 3;    /* smile (positive = up, negative = frown) */
    this._mx = 22;   /* mouth width */
    this._mu = 0.6;  /* mouth upper height */
    this._ml = 0.6;  /* mouth lower height */
    this._lx = 0;    /* gaze x */
    this._ly = 0;    /* gaze y */

    /* Ring breathing */
    this._breath = 0;
    this._nextBreath = Date.now() + 250;

    /* Idle glance wander */
    this._glanceAt = Date.now() + 1500 + Math.random() * 3500;
    this._gx = 0;
    this._gy = 0;

    /* Audio-driven mouth level (set by voice layer while speaking) */
    this.mouthLevel = 0;

    /* Reduced motion preference */
    this._reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* Colors per state (mirrors face_render.py table) */
    this.colors = {
      idle:       0x6a28c9,  /* purple */
      thinking:   0xf59e0b,  /* amber */
      speaking:   0xb05cff,  /* purple-pink */
      listening:  0x22d3ee,  /* cyan */
      recording:  0xff3b6b,  /* pink */
      music:      0x22c55e,  /* green */
      muted:      0xef4444,  /* red */
      offline:    0x475569,  /* grey */
      sick:       0xd6c92a,  /* yellow */
    };

    /* Mouth shapes per state (w, upper, lower) */
    this.mouthShapes = {
      idle:      { w: 22, u: 0.6, l: 0.6 },
      thinking:  { w: 15, u: 10,  l: 10 },
      listening: { w: 24, u: 3,   l: 3 },
      recording: { w: 24, u: 8,   l: 8 },
      music:     { w: 24, u: 3,   l: 5 },
      muted:     { w: 10, u: 7,   l: 7 },
      offline:   { w: 10, u: 7,   l: 7 },
      sick:      { w: 20, u: 6,   l: 6 },
    };
  }

  /* ── public API ── */

  set_state(s) {
    if (s === this.state) return;
    this.state = s;
    this._dirty = true;
  }

  draw() {
    const ctx = this.ctx;
    const W = this.W, H = this.H;
    ctx.clearRect(0, 0, W, H);
    this._tick();
    this._drawRing(ctx, W, H);
    this._drawBall(ctx, W, H);
    this._drawEyes(ctx, W, H);
    this._drawBrows(ctx, W, H);
    this._drawMouth(ctx, W, H);
  }

  /* ── 50 ms tick (mirrors face_render.py tick) ── */

  _tick() {
    const now = Date.now();
    const s = this.state;
    const busy = this.colors[s] !== 0x6a28c9 && this.colors[s] !== 0x475569;

    /* Ring breathing */
    if (now >= this._nextBreath) {
      this._nextBreath = now + 250;
      this._breath = (this._breath + (busy ? 6 : 2)) % 360;
    }

    /* Blink */
    if (!this._reducedMotion) {
      if (now > this._blinkAt) {
        this._blinkAt = now + 2500 + Math.random() * 3500;
        this._blinkOpen = 0;
      }
      if (this._blinkOpen < 1 && now + 150 < this._blinkAt) {
        this._blinkOpen = Math.min(1, (now - (this._blinkAt - 150)) / 150);
      } else {
        this._blinkOpen = 1;
      }
    }

    /* Target values per state */
    let t_eo = 1, t_bh = 0, t_ba = 0, t_sm = 3, t_lx = 0, t_ly = 0;
    let mouth = this.mouthShapes[s] || this.mouthShapes.idle;

    if (s === "listening")  { t_eo = 1.25; t_bh = 10; t_sm = 4; }
    else if (s === "recording") { t_eo = 1.3; t_bh = 14; t_sm = 1; }
    else if (s === "thinking") {
      t_eo = 0.9; t_bh = 10; t_ba = 0.15;
      t_lx = -0.8 + 0.2 * Math.sin(this._phase * 0.7);
      t_ly = -0.8; t_sm = -2;
    }
    else if (s === "music") { t_eo = 0.55; t_bh = 4; t_sm = 9; }
    else if (s === "muted") { t_eo = 0.08; t_sm = 0; }
    else if (s === "offline") { t_eo = 0; t_sm = -3; t_bh = 0; }
    else if (s === "speaking") {
      t_sm = 4;
      /* Audio-driven mouth */
      const lv = this.mouthLevel;
      t_eo = 1.12; t_bh = 3 + 5 * (lv > 0.3 ? 1 : 0);
      mouth = { w: 10 + 20 * lv, u: 3 + 13 * lv, l: 3 + 17 * lv };
    }
    else if (s === "sick") { t_eo = 0.7; t_sm = 0; }
    else {
      /* idle / other — idle glance wander */
      if (now > this._glanceAt) {
        this._gx = (Math.random() * 2 - 1) * 0.6;
        this._gy = (Math.random() * 2 - 1) * 0.3;
        this._glanceAt = now + 1500 + Math.random() * 3500;
      }
      t_lx = this._gx; t_ly = this._gy;
      if (s === "idle") { t_eo = 1.12; t_bh = 4; t_sm = 4; }
    }

    /* Smooth interpolation (GLIDE factor) */
    const e = 0.35;
    this._eo += (t_eo * 0.8 - this._eo) * 0.3;
    this._bh += (t_bh - this._bh) * e;
    this._ba += (t_ba - this._ba) * e;
    this._sm += (t_sm - this._sm) * 0.3;
    this._lx += (t_lx - this._lx) * 0.25;
    this._ly += (t_ly - this._ly) * 0.25;

    /* Mouth easing */
    this._mx += (mouth.w - this._mx) * 0.55;
    this._mu += (mouth.u - this._mu) * 0.55;
    this._ml += (mouth.l - this._ml) * 0.55;

    /* Phase for animations */
    this._phase += busy ? 0.13 : 0.06;
  }

  /* ── drawing helpers ── */

  _drawRing(ctx, W, H) {
    const col = this.colors[this.state] || 0x6a28c9;
    const r = (col >> 16) & 255, g = (col >> 8) & 255, b = col & 255;
    const s = (Math.sin(this._breath * Math.PI / 180) + 1) / 2;
    const busy = this.colors[this.state] !== 0x6a28c9 && this.colors[this.state] !== 0x475569;
    const kk = busy ? (0.65 + 0.35 * s) : (0.45 + 0.35 * s);

    ctx.lineWidth = 3;
    ctx.lineCap = "round";
    /* Four nested frames of four bars each */
    for (let lv = 0; lv < 4; lv++) {
      const o = 10 * lv;
      const n = W - 20 * lv;
      const a = Math.round(255 * kk * (1 - lv * 0.15));
      ctx.strokeStyle = `rgba(${r},${g},${b},${(a / 255).toFixed(2)})`;
      /* top */
      this._rect(ctx, o, o, n - o, 3);
      /* bottom */
      this._rect(ctx, o, H - o - 3, n - o, 3);
      /* left */
      this._rect(ctx, o, o + 3, 3, H - 20 - 2 * o);
      /* right */
      this._rect(ctx, W - o - 3, o + 3, 3, H - 20 - 2 * o);
    }
  }

  _rect(ctx, x, y, w, h) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 2);
    ctx.fill();
  }

  _drawBall(ctx, W, H) {
    const cx = W / 2, cy = H / 2 + 5;
    const rx = 40, ry = 55;
    const col = this.colors[this.state] || 0x6a28c9;
    const r = (col >> 16) & 255, g = (col >> 8) & 255, b = col & 255;

    /* Subtle rock */
    const rockX = Math.sin(this._phase * 0.3) * 3;
    const rockY = Math.abs(Math.sin(this._phase * 0.21)) * 2;

    /* Shadow */
    ctx.save();
    ctx.shadowColor = `rgba(${r},${g},${b},0.3)`;
    ctx.shadowBlur = 12;
    ctx.shadowOffsetX = rockX;
    ctx.shadowOffsetY = rockY + 8;

    /* Ball */
    ctx.beginPath();
    ctx.ellipse(cx + rockX, cy + rockY, rx, ry, 0, 0, Math.PI * 2);
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fill();
    ctx.restore();
  }

  _drawEyes(ctx, W, H) {
    const cx = W / 2;
    const cy = H / 2 - 18;
    const eo = this._eo * this._blinkOpen;
    const eyeH = Math.max(2, 11 * eo);
    const eyeW = 12;
    const eyeY = cy - eyeH + 2;

    for (let side = -1; side <= 1; side += 2) {
      const ex = cx + side * 26;

      /* White */
      if (eyeH > 3) {
        ctx.beginPath();
        ctx.ellipse(ex, eyeY + eyeH - 2, eyeW - 2, eyeH - 2, 0, 0, Math.PI * 2);
        ctx.fillStyle = "#f7f7fa";
        ctx.fill();
      }

      /* Iris */
      if (eyeH > 6) {
        const irisR = Math.max(3, Math.round(8 * Math.min(1, eyeH / 14)));
        const ix = ex + this._lx * 6;
        const iy = eyeY + eyeH - 2 + this._ly * 4;
        ctx.beginPath();
        ctx.arc(ix, iy, irisR, 0, Math.PI * 2);
        ctx.fillStyle = "#2f7bd9";
        ctx.fill();
        ctx.strokeStyle = "#1d4fa0";
        ctx.lineWidth = 1;
        ctx.stroke();

        /* Pupil */
        ctx.beginPath();
        ctx.arc(ix, iy, Math.max(2, irisR - 3), 0, Math.PI * 2);
        ctx.fillStyle = "#0b0b0e";
        ctx.fill();

        /* Glint */
        ctx.beginPath();
        ctx.arc(ix + 1, iy - 2, 2, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
      }

      /* Closed eye (just a line) */
      if (eyeH <= 6) {
        ctx.beginPath();
        ctx.moveTo(ex - eyeW, eyeY + eyeH - 2);
        ctx.lineTo(ex + eyeW, eyeY + eyeH - 2);
        ctx.strokeStyle = "#1b1d22";
        ctx.lineWidth = 3;
        ctx.lineCap = "round";
        ctx.stroke();
      }
    }
  }

  _drawBrows(ctx, W, H) {
    const cx = W / 2;
    const cy = H / 2 - 18;
    const eo = this._eo;
    const browY = cy - (12 + 8 * eo * 0.45 + 4 + this._bh);

    for (let side = -1; side <= 1; side += 2) {
      const bx = cx + side * 26;
      const sl = this._ba * 18 * side;

      ctx.beginPath();
      ctx.moveTo(bx - 13, browY + sl * -1);
      ctx.quadraticCurveTo(bx - 4, browY + sl * 0.3 * -1 - 1.5, bx, browY + sl * 0.3 * -1 - 1.5);
      ctx.quadraticCurveTo(bx + 4, browY + sl * 0.3 - 1.5, bx + 13, browY + sl * -1);
      ctx.strokeStyle = "#1b1d22";
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      ctx.stroke();
    }
  }

  _drawMouth(ctx, W, H) {
    const cx = W / 2;
    const my = H / 2 + 26;
    const mw = this._mx;
    const mu = this._mu;
    const ml = this._ml;
    const sm = this._sm;

    /* Build the mouth outline (elliptical) */
    const pts = [];
    const steps = 24;
    for (let i = 0; i < steps; i++) {
      const u = -1 + i * 2 / (steps - 1);
      const edge = 1 - u * u;
      const yOff = -(mu + ml) * edge * 0.5 - sm * (1 - edge) * 0.25;
      const x = cx + mw * u;
      const y = my + yOff;
      pts.push({ x: Math.round(x), y: Math.round(y) });
    }

    if (mu + ml > 1.5) {
      /* Fill mouth interior */
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
      ctx.closePath();
      ctx.fillStyle = "#0b0b0e";
      ctx.fill();

      /* Teeth line when mouth is open wide */
      if (mu > 4 && this._mu + this._ml > 3) {
        const ty = my - mu * 0.35;
        ctx.beginPath();
        ctx.moveTo(cx - mw * 0.7, ty);
        ctx.lineTo(cx + mw * 0.7, ty);
        ctx.strokeStyle = "#eceef2";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    } else {
      /* Closed mouth — just the outline */
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
      ctx.strokeStyle = "#1b1d22";
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      ctx.stroke();
    }

    /* Chin dimple */
    const cw = 14 + mw * 0.18;
    const cyy = my + ml + 13;
    ctx.beginPath();
    for (let i = 0; i < 7; i++) {
      const t = -1 + i / 3;
      const px = cx + cw * t;
      const py = cyy + 5 * (1 - t * t);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.strokeStyle = "#1b1d22";
    ctx.lineWidth = 4;
    ctx.lineCap = "round";
    ctx.globalAlpha = 0.9;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}
