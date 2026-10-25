// MineBench's voxel.exec, run locally (the rules of github.com/Ammaar-Alam/minebench lib/ai/tools/voxelExec.ts): your JavaScript calls
// block(x,y,z,type), box(x1,y1,z1,x2,y2,z2,type) (filled, inclusive), line(x1,y1,z1,x2,y2,z2,type), rng() (xorshift32 from the seed),
// Math, GRID_SIZE, PALETTE. Arguments are truncated to integers.
//   node tools/voxel/runtime.mjs build.json             -> block count, bounds, out-of-grid and unknown-block counts, time
//   node tools/voxel/runtime.mjs build.json voxels.bin  -> also writes the expanded voxels (x,y,z as uint16, palette index as uint8)
import { readFileSync, writeFileSync } from "node:fs";

const SIMPLE = ["stone", "cobblestone", "oak_planks", "bricks", "stone_bricks", "grass_block", "dirt", "sand", "oak_log", "oak_leaves",
  "water", "white_wool", "black_wool", "red_wool", "blue_wool", "green_wool", "yellow_wool", "orange_wool", "purple_wool", "brown_wool",
  "gray_wool", "glass", "glowstone", "iron_block", "gold_block"];

const call = JSON.parse(readFileSync(process.argv[2], "utf8"));
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
console.log(JSON.stringify({ blocks: voxels.size, placed_calls: placed, outside_grid: outside, unknown_types: Object.fromEntries(unknown),
  bounds: { min: lo, max: hi }, floating_parts: floating.length, floating_blocks: floating.reduce((n, f) => n + f.blocks, 0),
  biggest_floating: floating.slice(0, 8), ms: Date.now() - t0 }));
if (process.argv[3]) {
  const buf = Buffer.alloc(voxels.size * 7); let o = 0;
  for (const [k, i] of voxels) {
    const c = k % G, b = Math.floor(k / G) % G, a = Math.floor(k / G / G);
    buf.writeUInt16LE(a, o); buf.writeUInt16LE(b, o + 2); buf.writeUInt16LE(c, o + 4); buf.writeUInt8(i, o + 6); o += 7;
  }
  writeFileSync(process.argv[3], buf);
}
