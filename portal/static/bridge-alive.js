/* The living background (Skippy's page): neon wireframe spirals that drift where nobody can predict, reach for each other
   or for the corners of the screen, hold on for a few seconds, and let go. It is there so the space around the chat feels
   inhabited: a mind at work, not a page waiting.

       Bridge.alive(canvas)   → { pulse(strength) }     (also published as window.skippyAlive)

   Each spiral wanders on its own sum of slow sines with its own random frequencies, and now and then picks entirely new ones, so
   its path never repeats. At random intervals it ATTACHES (a wire grows from its tip to another spiral's tip, or to a corner of
   the screen), the two are drawn together by a spring while light runs along the wire, and later it DETACHES: the wire snaps,
   sparks fly and both fly apart. pulse() makes everything brighter and faster for a moment (the page calls it as a question
   goes out and an answer comes back). The page is never covered: the interface sits on top, and this draws dim.
   Respects "reduce motion": it paints one still frame. It makes no network calls and uses no timers: every interval is counted
   inside the animation loop, which the browser pauses when the tab is hidden. */
(function () {
  var Bridge = window.Bridge = window.Bridge || {};
  var COLORS = ["#00ff88", "#00ffff", "#aa44ff", "#ff44aa", "#ffcc00"];
  var TAU = Math.PI * 2;
  function rnd(a, b) { return a + Math.random() * (b - a); }

  Bridge.alive = function (cv) {
    var ctx = cv.getContext("2d"), W = 0, H = 0, dpr = 1, t = 0, last = 0, energy = 0, still = false;
    var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    var spirals = [], links = [], sparks = [], corners = [];

    function size() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = cv.clientWidth; H = cv.clientHeight;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      corners = [{ x: 0, y: 0 }, { x: W, y: 0 }, { x: W, y: H }, { x: 0, y: H }];
    }

    function Spiral(i) {
      this.col = COLORS[i % COLORS.length];
      this.x = rnd(.15, .85) * W; this.y = rnd(.15, .85) * H; this.vx = rnd(-20, 20); this.vy = rnd(-20, 20);
      this.retune(); this.link = null; this.cool = rnd(2, 7);       // seconds before it may reach for something
    }
    Spiral.prototype.retune = function () {                        // new, unrelated wandering: the unpredictable part
      this.f = [rnd(.03, .13), rnd(.05, .17), rnd(.02, .09), rnd(.04, .15)];
      this.p = [rnd(0, TAU), rnd(0, TAU), rnd(0, TAU), rnd(0, TAU)];
      this.cx = rnd(.1, .9) * W; this.cy = rnd(.1, .9) * H;        // where it is drifting around
      this.R = rnd(.18, .42) * Math.min(W, H);                     // how large it is
      this.turns = rnd(2.4, 4.6); this.spin = rnd(-.7, .7); this.sway = rnd(.5, 1.6); this.squash = rnd(.65, 1);
      this.retuneAt = t + rnd(9, 26);
    };
    // the outer end of the spiral: what a wire attaches to
    Spiral.prototype.tip = function () { var p = this.point(1); return { x: p[0], y: p[1] }; };
    Spiral.prototype.point = function (u) {                        // u: 0 centre .. 1 outer end
      var th = u * this.turns * TAU, r = this.R * Math.pow(u, .82) * (1 + .1 * Math.sin(th * 3 + t * this.sway));
      var a = th + t * this.spin + Math.sin(t * .31 + this.p[3]) * .6;
      return [this.x + r * Math.cos(a), this.y + r * Math.sin(a) * this.squash];
    };
    Spiral.prototype.step = function (dt) {
      if (t > this.retuneAt) this.retune();
      var tx = this.cx + Math.sin(t * this.f[0] + this.p[0]) * W * .22 + Math.sin(t * this.f[1] * 2.3 + this.p[1]) * W * .08;
      var ty = this.cy + Math.sin(t * this.f[2] + this.p[2]) * H * .22 + Math.sin(t * this.f[3] * 2.1 + this.p[0]) * H * .08;
      var ax = (tx - this.x) * .5, ay = (ty - this.y) * .5;
      if (this.link) {                                             // held by a wire: drawn toward what it is attached to
        var o = this.link.other(this), k = .9, ox = o.x, oy = o.y;
        var dx = ox - this.x, dy = oy - this.y, d = Math.hypot(dx, dy) || 1, want = (this.R + (o.R || 0)) * .85;
        ax += dx / d * (d - want) * k; ay += dy / d * (d - want) * k;
      }
      this.vx = (this.vx + ax * dt) * Math.pow(.35, dt); this.vy = (this.vy + ay * dt) * Math.pow(.35, dt);
      this.x += this.vx * dt; this.y += this.vy * dt;
      this.cool -= dt;
    };
    Spiral.prototype.draw = function (a) {
      var N = 120, pts = [], i;
      for (i = 0; i <= N; i++) pts.push(this.point(i / N));
      ctx.strokeStyle = this.col; ctx.lineCap = "round";
      for (var pass = 0; pass < 2; pass++) {                       // a wide faint stroke under a thin bright one: neon
        ctx.lineWidth = pass ? 1.4 : 5;
        for (i = 0; i < N; i += 8) {
          ctx.globalAlpha = a * (pass ? .95 : .12) * (.25 + .75 * Math.sin(Math.PI * (.12 + .88 * i / N)));
          ctx.beginPath(); ctx.moveTo(pts[i][0], pts[i][1]);
          for (var j = i + 1; j <= Math.min(i + 8, N); j++) ctx.lineTo(pts[j][0], pts[j][1]);
          ctx.stroke();
        }
      }
      ctx.lineWidth = 1; ctx.globalAlpha = a * .16;                 // the wireframe: struts from one turn to the next
      var per = Math.round(N / this.turns);
      for (i = 0; i + per <= N; i += 6) { ctx.beginPath(); ctx.moveTo(pts[i][0], pts[i][1]); ctx.lineTo(pts[i + per][0], pts[i + per][1]); ctx.stroke(); }
      ctx.globalAlpha = a * .5; ctx.beginPath(); ctx.arc(pts[N][0], pts[N][1], 3, 0, TAU); ctx.stroke();   // the tip: where wires attach
    };

    // A wire between a spiral's tip and another spiral's tip, or a corner of the screen.
    function Link(a, b) { this.a = a; this.b = b; this.age = 0; this.hold = rnd(4, 9); this.state = "grow"; this.k = 0; }
    Link.prototype.other = function (s) { return this.a === s ? (this.b.tip ? this.b : { x: this.b.x, y: this.b.y, R: 0 }) : this.a; };
    Link.prototype.ends = function () {
      var p = this.a.tip(), q = this.b.tip ? this.b.tip() : this.b;
      return [p, q];
    };
    Link.prototype.step = function (dt) {
      this.age += dt;
      if (this.state === "grow") { this.k = Math.min(1, this.k + dt / 1.3); if (this.k >= 1) this.state = "hold"; }
      else if (this.state === "hold" && this.age > this.hold) { this.state = "snap"; this.k = 1; this.snap(); }
      else if (this.state === "snap") { this.k -= dt / .9; if (this.k <= 0) return false; }
      return true;
    };
    Link.prototype.snap = function () {                            // detach: sparks, and both ends fly apart
      var e = this.ends(), self = this;
      for (var i = 0; i < 14; i++) sparks.push({ x: e[0].x * .5 + e[1].x * .5, y: e[0].y * .5 + e[1].y * .5, vx: rnd(-90, 90), vy: rnd(-90, 90), life: rnd(.5, 1.2), col: self.a.col });
      [this.a, this.b].forEach(function (s) {
        if (!s.tip) return;
        var d = self.other(s), dx = s.x - d.x, dy = s.y - d.y, n = Math.hypot(dx, dy) || 1;
        s.vx += dx / n * rnd(60, 150); s.vy += dy / n * rnd(60, 150); s.link = null; s.cool = rnd(3, 9);
        if (Math.random() < .6) s.retune();
      });
    };
    Link.prototype.draw = function (a) {
      var e = this.ends(), p = e[0], q = e[1], k = this.state === "snap" ? this.k : this.k;
      var mx = (p.x + q.x) / 2 + Math.sin(t * 1.7 + this.age) * 14, my = (p.y + q.y) / 2 + Math.cos(t * 1.3 + this.age) * 14;
      var steps = 24, i, prev = null;
      ctx.lineWidth = 1.3;
      for (i = 0; i <= steps * k; i++) {                           // a quadratic wire, drawn only as far as it has grown (or not yet retracted)
        var u = i / steps, x = (1 - u) * (1 - u) * p.x + 2 * u * (1 - u) * mx + u * u * q.x, y = (1 - u) * (1 - u) * p.y + 2 * u * (1 - u) * my + u * u * q.y;
        if (prev) {
          ctx.strokeStyle = u < .5 ? this.a.col : (this.b.col || this.a.col); ctx.globalAlpha = a * (this.state === "snap" ? .5 * k : .8);   // each half its own colour
          if (this.state === "snap" && i % 3 === 0) { prev = [x, y]; continue; }                                                          // a snapped wire flickers
          ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(x, y); ctx.stroke();
        }
        prev = [x, y];
      }
      if (this.state === "hold" || this.state === "grow") {        // light runs along it
        for (var n = 0; n < 3; n++) {
          var v = ((t * .45 + n / 3) % 1) * k, px = (1 - v) * (1 - v) * p.x + 2 * v * (1 - v) * mx + v * v * q.x, py = (1 - v) * (1 - v) * p.y + 2 * v * (1 - v) * my + v * v * q.y;
          ctx.fillStyle = "#fff"; ctx.globalAlpha = a * .9; ctx.fillRect(px - 1.5, py - 1.5, 3, 3);
        }
      }
    };

    function maybeAttach() {
      var free = spirals.filter(function (s) { return !s.link && s.cool <= 0; });
      if (!free.length || Math.random() > .02) return;             // checked every frame: on average one attaches every ~second or so, never on a beat
      var a = free[Math.floor(Math.random() * free.length)], b;
      var others = spirals.filter(function (s) { return s !== a && !s.link; });
      if (others.length && Math.random() < .6) b = others[Math.floor(Math.random() * others.length)];
      else b = corners[Math.floor(Math.random() * 4)];             // or reach for the edge of the screen: the interface it lives in
      var l = new Link(a, b); a.link = l; if (b.tip) b.link = l; links.push(l);
    }

    function frame(ms) {
      var dt = Math.min(.05, (ms - last) / 1000 || .016); last = ms; t += dt * (1 + energy * .8);
      energy = Math.max(0, energy - dt * .55);
      ctx.clearRect(0, 0, W, H);
      var a = .5 + Math.min(.5, energy * .35) + .08 * Math.sin(t * .8);   // dim, breathing slowly, brighter while it "thinks"
      spirals.forEach(function (s) { s.step(dt); });
      maybeAttach();
      links = links.filter(function (l) { return l.step(dt); });
      spirals.forEach(function (s) { s.draw(a); });
      links.forEach(function (l) { l.draw(a); });
      sparks = sparks.filter(function (p) { p.life -= dt; return p.life > 0; });
      sparks.forEach(function (p) {
        p.x += p.vx * dt; p.y += p.vy * dt; p.vx *= .96; p.vy *= .96;
        ctx.globalAlpha = Math.min(1, p.life) * a; ctx.fillStyle = p.col; ctx.fillRect(p.x - 1, p.y - 1, 2, 2);
      });
      ctx.globalAlpha = 1;
      if (!still) requestAnimationFrame(frame);
    }

    size();
    for (var i = 0; i < 5; i++) spirals.push(new Spiral(i));
    window.addEventListener("resize", size);
    var api = { pulse: function (s) { energy = Math.min(3, energy + (s || 1)); } };
    window.skippyAlive = api;
    if (reduce) { still = true; t = 3; spirals.forEach(function (s) { s.step(.016); }); frame(16); }   // one still frame
    else requestAnimationFrame(frame);
    return api;
  };
})();
