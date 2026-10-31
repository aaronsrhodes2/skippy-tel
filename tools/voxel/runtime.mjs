// MineBench's voxel.exec, run locally (the rules of github.com/Ammaar-Alam/minebench lib/ai/tools/voxelExec.ts): your JavaScript calls
// block(x,y,z,type), box(x1,y1,z1,x2,y2,z2,type) (filled, inclusive), line(x1,y1,z1,x2,y2,z2,type), rng() (xorshift32 from the seed),
// Math, GRID_SIZE, PALETTE. Arguments are truncated to integers.
//   node tools/voxel/runtime.mjs build.json             -> block count, bounds, out-of-grid and unknown-block counts, time
//   node tools/voxel/runtime.mjs build.json voxels.bin  -> also writes the expanded voxels (x,y,z as uint16, palette index as uint8)
//   add --forward=z (the direction of travel) to check the wheels against it: any round, thin disc, whatever it is made of
//   VOXEL_DEBUG=<material> prints every round shape of that material the wheel check looked at, and why it was kept or not
import { readFileSync, writeFileSync } from "node:fs";

const SIMPLE = ["stone", "cobblestone", "oak_planks", "bricks", "stone_bricks", "grass_block", "dirt", "sand", "oak_log", "oak_leaves",
  "water", "white_wool", "black_wool", "red_wool", "blue_wool", "green_wool", "yellow_wool", "orange_wool", "purple_wool", "brown_wool",
  "gray_wool", "glass", "glowstone", "iron_block", "gold_block"];

const args = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const flag = (name, dflt) => (process.argv.find((a) => a.startsWith(`--${name}=`)) || "").slice(name.length + 3) || dflt;
const call = JSON.parse(readFileSync(args[0], "utf8"));
const input = call.input || call;
const G = input.gridSize || 256;
const t0 = Date.now();
let x = (input.seed | 0) || 1;
const rng = () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return (x >>> 0) / 0x1_0000_0000; };
const T = (v) => { if (typeof v !== "number" || !Number.isFinite(v)) throw new Error("Non-finite numeric argument"); return Math.trunc(v); };
const voxels = new Map();            // last write wins, like a world
let placed = 0, outside = 0;
const unknown = new Map();
const put = (a, b, c, type) => {
  placed++;
  if (a < 0 || b < 0 || c < 0 || a >= G || b >= G || c >= G) { outside++; return; }
  const i = SIMPLE.indexOf(type);
  if (i < 0) { unknown.set(type, (unknown.get(type) || 0) + 1); return; }
  voxels.set((a * G + b) * G + c, i);
};
const block = (a, b, c, t) => put(T(a), T(b), T(c), String(t));
const box = (a1, b1, c1, a2, b2, c2, t) => {
  [a1, b1, c1, a2, b2, c2] = [a1, b1, c1, a2, b2, c2].map(T);
  for (let a = Math.min(a1, a2); a <= Math.max(a1, a2); a++)
    for (let b = Math.min(b1, b2); b <= Math.max(b1, b2); b++)
      for (let c = Math.min(c1, c2); c <= Math.max(c1, c2); c++) put(a, b, c, String(t));
};
const line = (...args) => {
  let p, q, t;
  if (args.length === 3) { p = [args[0].x, args[0].y, args[0].z]; q = [args[1].x, args[1].y, args[1].z]; t = args[2]; }
  else { p = args.slice(0, 3); q = args.slice(3, 6); t = args[6]; }
  p = p.map(T); q = q.map(T);
  const n = Math.max(...p.map((v, k) => Math.abs(q[k] - v)), 1);
  for (let s = 0; s <= n; s++) put(...p.map((v, k) => Math.round(v + (q[k] - v) * s / n)), String(t));
};
new Function("block", "box", "line", "rng", "Math", "GRID_SIZE", "PALETTE", `"use strict";\n${input.code}`)(
  block, box, line, rng, Math, G, input.palette || "simple");

let lo = [G, G, G], hi = [-1, -1, -1];
for (const k of voxels.keys()) {
  const c = k % G, b = Math.floor(k / G) % G, a = Math.floor(k / G / G);
  [a, b, c].forEach((v, j) => { lo[j] = Math.min(lo[j], v); hi[j] = Math.max(hi[j], v); });
}
// Floating parts: pieces of the build (blocks joined face to face) that touch neither the ground (the build's lowest layer) nor anything
// resting on it. A vision model describing a picture misses these (MineBench v2's passenger cars floated beside the plate and it called
// the scene "recognisable"); this counts them. Smoke and clouds may float on purpose: the report says where each piece is and what it's made of.
const floating = [];
{
  const seen = new Set(), ground = lo[1];
  const dirs = [G * G, -G * G, G, -G, 1, -1];
  for (const start of voxels.keys()) {
    if (seen.has(start)) continue;
    const stack = [start], part = []; seen.add(start);
    let grounded = false;
    while (stack.length) {
      const k = stack.pop(); part.push(k);
      if (Math.floor(k / G) % G === ground) grounded = true;
      for (const d of dirs) {
        const n = k + d;
        if (voxels.has(n) && !seen.has(n) && Math.abs((n % G) - (k % G)) <= 1) { seen.add(n); stack.push(n); }
      }
    }
    if (!grounded) {
      const pl = [G, G, G], ph = [-1, -1, -1], kinds = new Map();
      for (const k of part) {
        const c = k % G, b = Math.floor(k / G) % G, a = Math.floor(k / G / G);
        [a, b, c].forEach((v, j) => { pl[j] = Math.min(pl[j], v); ph[j] = Math.max(ph[j], v); });
        const t = SIMPLE[voxels.get(k)]; kinds.set(t, (kinds.get(t) || 0) + 1);
      }
      floating.push({ blocks: part.length, min: pl, max: ph, made_of: [...kinds].sort((x, y) => y[1] - x[1]).slice(0, 3).map(([t]) => t) });
    }
  }
  floating.sort((x, y) => y.blocks - x.blocks);
}
// Wheels, found by their shape whatever they are made of (v8's were iron, and a check that looked only for black wool saw none). In
// every slice of the grid, each material's cells are shaved twice (a 3x3 erosion), so axles, bars and frames joined to a wheel fall
// away; what is left round, and not a full square, is a disc. A disc counts as a wheel when it is thin for its size and is not one
// layer of a bigger round solid (a boiler, a dome, a tree's crown, a puff of smoke continue past it in the same material).
// A wheel rolls ALONG the direction of travel, so its face points ACROSS the track. A face pointing along --forward is turned
// ("along"); a face pointing up is a wheel lying on the ground ("flat"). --forward=x|z, else the longer horizontal side (inferred).
const fwdFlag = flag("forward", "");
const forward = fwdFlag || (hi[0] - lo[0] >= hi[2] - lo[2] ? "x" : "z");
const AXES = ["x", "y", "z"];
const wheels = { forward, forward_source: fwdFlag ? "flag" : "inferred", discs: 0, upright: 0, along: 0, flat: 0, turned: 0,
  upright_at: [], turned_at: [] };
{
  const cells = [];
  for (const [k, m] of voxels) cells.push([Math.floor(k / G / G), Math.floor(k / G) % G, k % G, m]);
  const erode = (S) => {
    const E = new Set();
    for (const p of S) {
      const u = Math.floor(p / G), v = p % G;
      let ok = u > 0 && v > 0 && u < G - 1 && v < G - 1;
      for (let du = -1; ok && du <= 1; du++) for (let dv = -1; ok && dv <= 1; dv++) ok = S.has((u + du) * G + v + dv);
      if (ok) E.add(p);
    }
    return E;
  };
  for (let ax = 0; ax < 3; ax++) {
    const [ua, va] = [0, 1, 2].filter((j) => j !== ax);
    const slices = new Map();                       // slice * 32 + material -> Set(u * G + v)
    for (const c of cells) {
      const key = c[ax] * 32 + c[3];
      let set = slices.get(key);
      if (!set) slices.set(key, (set = new Set()));
      set.add(c[ua] * G + c[va]);
    }
    const found = new Map();                         // same key -> [{u, v, d}]
    for (const [key, S] of slices) {
      if (S.size < 20) continue;
      // each piece of this material in this slice (8-connected, so a rim drawn as a ring of diagonal steps stays one piece), at
      // most 80 across: no wheel is bigger, and it keeps the cost down
      const pieceSeen = new Set();
      for (const p0 of S) {
        if (pieceSeen.has(p0)) continue;
        const piece = [p0]; pieceSeen.add(p0);
        let u0 = G, u1 = -1, v0 = G, v1 = -1;
        for (let i = 0; i < piece.length; i++) {
          const p = piece[i], u = Math.floor(p / G), v = p % G;
          u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v);
          for (let du = -1; du <= 1; du++) for (let dv = -1; dv <= 1; dv++) {
            const uu = u + du, vv = v + dv, q = uu * G + vv;
            if (uu >= 0 && vv >= 0 && uu < G && vv < G && S.has(q) && !pieceSeen.has(q)) { pieceSeen.add(q); piece.push(q); }
          }
        }
        if (u1 - u0 + 1 < 7 || v1 - v0 + 1 < 7) continue;
        // fill what the piece encloses (a spoked wheel is a ring: filled, it is a disc), then shave twice. A piece over 80 across
        // (a wheel joined to the rails or the frame) is shaved without filling, which keeps the cost down
        let filled;
        if (u1 - u0 + 1 > 80 || v1 - v0 + 1 > 80) filled = new Set(piece);
        else {
        const W = v1 - v0 + 3, H = u1 - u0 + 3, grid = new Uint8Array(W * H);     // 1 piece, 2 outside
        for (const p of piece) grid[(Math.floor(p / G) - u0 + 1) * W + (p % G) - v0 + 1] = 1;
        const out = [0]; grid[0] = 2;
        for (let i = 0; i < out.length; i++) {
          const c = out[i], r = Math.floor(c / W), col = c % W;
          for (const [rr, cc] of [[r + 1, col], [r - 1, col], [r, col + 1], [r, col - 1]])
            if (rr >= 0 && cc >= 0 && rr < H && cc < W && !grid[rr * W + cc]) { grid[rr * W + cc] = 2; out.push(rr * W + cc); }
        }
        filled = new Set();
        for (let r = 1; r < H - 1; r++) for (let c = 1; c < W - 1; c++) if (grid[r * W + c] !== 2) filled.add((r - 1 + u0) * G + c - 1 + v0);
        }
        const E = erode(erode(filled)), seen = new Set();
        for (const e0 of E) {
          if (seen.has(e0)) continue;
          const stack = [e0]; seen.add(e0);
          let n = 0, a0 = G, a1 = -1, b0 = G, b1 = -1;
          while (stack.length) {
            const p = stack.pop(); n++;
            const u = Math.floor(p / G), v = p % G;
            a0 = Math.min(a0, u); a1 = Math.max(a1, u); b0 = Math.min(b0, v); b1 = Math.max(b1, v);
            for (const q of [p + G, p - G, p + 1, p - 1]) if (E.has(q) && !seen.has(q) && Math.abs((q % G) - v) <= 1) { seen.add(q); stack.push(q); }
          }
          const pu = a1 - a0 + 1, pv = b1 - b0 + 1, fill = n / (pu * pv);
          if (process.env.VOXEL_DEBUG === SIMPLE[key % 32]) console.error(JSON.stringify({ ax: AXES[ax], s: (key - key % 32) / 32, pu, pv, n, fill: +fill.toFixed(2), at: [a0, b0] }));
          if (Math.min(pu, pv) < 3 || Math.max(pu, pv) > 1.75 * Math.min(pu, pv) || fill < 0.5 || fill > 0.92) continue;
          if (!found.has(key)) found.set(key, []);
          found.get(key).push({ u: Math.round((a0 + a1) / 2), v: Math.round((b0 + b1) / 2), d: Math.max(pu, pv) + 4 });
        }
      }
    }
    // join the same disc over neighbouring slices: its thickness
    const used = new Set();
    const keys = [...found.keys()].sort((a, b) => a - b);
    for (const key of keys) {
      const m = key % 32, s0 = (key - m) / 32;
      for (const disc of found.get(key)) {
        if (used.has(disc)) continue;
        used.add(disc);
        let s = s0 + 1;
        for (;;) {
          const next = (found.get(s * 32 + m) || []).find((o) => !used.has(o) && Math.abs(o.u - disc.u) <= 1 && Math.abs(o.v - disc.v) <= 1 && Math.abs(o.d - disc.d) <= 2);
          if (!next) break;
          used.add(next); s++;
        }
        const thick = s - s0, centre = disc.u * G + disc.v;
        const dbg = (why) => process.env.VOXEL_DEBUG && console.error(JSON.stringify({ ax: AXES[ax], s0, m: SIMPLE[m], ...disc, thick, why }));
        if (thick > 0.4 * disc.d) { dbg("thick"); continue; }                // a cylinder or a drum, not a wheel
        if ((slices.get((s0 - 1) * 32 + m) || new Set()).has(centre) || (slices.get(s * 32 + m) || new Set()).has(centre)) { dbg("solid"); continue; }  // one layer of a solid
        wheels.discs++;
        const at = [0, 0, 0]; at[ax] = s0; at[ua] = disc.u; at[va] = disc.v;
        const why = AXES[ax] === forward ? "along" : AXES[ax] === "y" ? "flat" : "";
        if (!why) { wheels.upright++; wheels.upright_at.push({ at, face: AXES[ax], across: disc.d, thick, made_of: SIMPLE[m] }); continue; }
        wheels.turned++; wheels[why]++;
        wheels.turned_at.push({ at, face: AXES[ax], why, across: disc.d, thick, made_of: SIMPLE[m] });
      }
    }
  }
}
if (!wheels.discs) wheels.note = "no wheel-shaped discs found (round, 7 or more across): on a vehicle that means the wheels are missing or not round";
wheels.turned_at.sort((a, b) => b.across - a.across);
wheels.upright_at.sort((a, b) => b.across - a.across);
wheels.upright_at = wheels.upright_at.slice(0, 8);
wheels.turned_at = wheels.turned_at.slice(0, 12);
console.log(JSON.stringify({ blocks: voxels.size, wheels, placed_calls: placed, outside_grid: outside, unknown_types: Object.fromEntries(unknown),
  bounds: { min: lo, max: hi }, floating_parts: floating.length, floating_blocks: floating.reduce((n, f) => n + f.blocks, 0),
  biggest_floating: floating.slice(0, 8), ms: Date.now() - t0 }));
if (args[1]) {
  const buf = Buffer.alloc(voxels.size * 7); let o = 0;
  for (const [k, i] of voxels) {
    const c = k % G, b = Math.floor(k / G) % G, a = Math.floor(k / G / G);
    buf.writeUInt16LE(a, o); buf.writeUInt16LE(b, o + 2); buf.writeUInt16LE(c, o + 4); buf.writeUInt8(i, o + 6); o += 7;
  }
  writeFileSync(args[1], buf);
}
