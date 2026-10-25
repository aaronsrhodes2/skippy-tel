"""A voxel build (MineBench's voxel.exec JSON: {"tool": "voxel.exec", "input": {"code", "gridSize", "palette", "seed"}}) as one
self-contained HTML page: it runs the build's code with MineBench's rules in the browser (the same as runtime.mjs), keeps only the
blocks you can see (a neighbour on some side is empty), and draws them with three.js (orbit, zoom). render.py photographs it.
    python tools/voxel/viewer.py build.json out.html "Title" "subtitle"
"""
import json
import sys


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0d1117;--ink:#e6edf3;--dim:#8b949e}
:root[data-theme="light"]{--bg:#f6f8fa;--ink:#1f2328;--dim:#59636e}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#f6f8fa;--ink:#1f2328;--dim:#59636e}}
html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif;overflow:hidden}
#hud{position:fixed;left:16px;top:12px;right:16px;pointer-events:none}
#hud h1{font-size:18px;margin:0 0 2px}#hud p{margin:0;color:var(--dim)}
#stats{position:fixed;left:16px;bottom:12px;color:var(--dim);font-size:12px}
canvas{display:block}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body>
<div id="hud"><h1>__TITLE__</h1><p>__SUBTITLE__</p></div><div id="stats">building…</div>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
const INPUT = __INPUT__;
const NAMES = ["stone","cobblestone","oak_planks","bricks","stone_bricks","grass_block","dirt","sand","oak_log","oak_leaves","water","white_wool",
 "black_wool","red_wool","blue_wool","green_wool","yellow_wool","orange_wool","purple_wool","brown_wool","gray_wool","glass","glowstone","iron_block","gold_block"];
const COLORS = [0x7f7f7f,0x6b6b6b,0xa2834f,0x965a4a,0x7a7a7a,0x5d9b3a,0x866043,0xdbd3a0,0x6b5534,0x3f7a26,0x3f76e4,0xe9ecec,0x15151a,0xa12722,
 0x35399d,0x546d1b,0xf8c527,0xf07613,0x7a2aad,0x724728,0x3e4447,0xb8d8e8,0xf7d27a,0xd8d8d8,0xf6d03c];
const G = INPUT.gridSize || 256, t0 = performance.now();
let s = (INPUT.seed | 0) || 1;
const rng = () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return (s >>> 0) / 4294967296; };
const T = (v) => Math.trunc(v);
const world = new Map();
const put = (a, b, c, t) => { if (a < 0 || b < 0 || c < 0 || a >= G || b >= G || c >= G) return; const i = NAMES.indexOf(t); if (i >= 0) world.set((a * G + b) * G + c, i); };
const block = (a, b, c, t) => put(T(a), T(b), T(c), String(t));
const box = (a1, b1, c1, a2, b2, c2, t) => { [a1,b1,c1,a2,b2,c2] = [a1,b1,c1,a2,b2,c2].map(T);
  for (let a = Math.min(a1,a2); a <= Math.max(a1,a2); a++) for (let b = Math.min(b1,b2); b <= Math.max(b1,b2); b++)
    for (let c = Math.min(c1,c2); c <= Math.max(c1,c2); c++) put(a, b, c, String(t)); };
const line = (...g) => { let p, q, t; if (g.length === 3) { p = [g[0].x,g[0].y,g[0].z]; q = [g[1].x,g[1].y,g[1].z]; t = g[2]; } else { p = g.slice(0,3); q = g.slice(3,6); t = g[6]; }
  p = p.map(T); q = q.map(T); const n = Math.max(...p.map((v,k) => Math.abs(q[k]-v)), 1);
  for (let i = 0; i <= n; i++) put(...p.map((v,k) => Math.round(v + (q[k]-v)*i/n)), String(t)); };
new Function("block","box","line","rng","Math","GRID_SIZE","PALETTE", '"use strict";\n' + INPUT.code)(block, box, line, rng, Math, G, INPUT.palette || "simple");
const seen = [];
for (const [k, i] of world) { const c = k % G, b = Math.floor(k / G) % G, a = Math.floor(k / G / G);
  const open = [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]].some(([dx,dy,dz]) => { const x=a+dx,y=b+dy,z=c+dz;
    return x<0||y<0||z<0||x>=G||y>=G||z>=G||!world.has((x*G+y)*G+z); });
  if (open) seen.push(a, b, c, i); }
const scene = new THREE.Scene();
const renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);
const camera = new THREE.PerspectiveCamera(45, 1, 0.5, 5000);
const n = seen.length / 4, mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1), new THREE.MeshLambertMaterial(), n);
let lo = [G,G,G], hi = [0,0,0]; const m = new THREE.Matrix4(), col = new THREE.Color();
for (let j = 0; j < n; j++) { const a = seen[4*j], b = seen[4*j+1], c = seen[4*j+2];
  m.makeTranslation(a, b, c); mesh.setMatrixAt(j, m); mesh.setColorAt(j, col.setHex(COLORS[seen[4*j+3]]));
  lo = [Math.min(lo[0],a),Math.min(lo[1],b),Math.min(lo[2],c)]; hi = [Math.max(hi[0],a),Math.max(hi[1],b),Math.max(hi[2],c)]; }
scene.add(mesh);
scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.1)); const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(1, 2, 1.4); scene.add(sun);
const mid = new THREE.Vector3((lo[0]+hi[0])/2, (lo[1]+hi[1])/2, (lo[2]+hi[2])/2), size = Math.max(hi[0]-lo[0], hi[1]-lo[1], hi[2]-lo[2], 8);
camera.position.set(mid.x + size*1.1, mid.y + size*0.7, mid.z + size*1.1);
const controls = new OrbitControls(camera, renderer.domElement); controls.target.copy(mid); controls.enableDamping = true;
const fit = () => { renderer.setSize(innerWidth, innerHeight); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); };
addEventListener("resize", fit); fit();
document.getElementById("stats").textContent = `${world.size.toLocaleString()} blocks (${n.toLocaleString()} visible) · ${hi[0]-lo[0]+1}×${hi[1]-lo[1]+1}×${hi[2]-lo[2]+1} · built in ${Math.round(performance.now()-t0)} ms · drag to orbit, scroll to zoom`;
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
// render.py: aim the camera (azimuth and elevation in degrees) and say when the build is drawn
window.__view = (az, el) => { const r = size * 1.9, a = az * Math.PI / 180, e = el * Math.PI / 180;
  camera.position.set(mid.x + r * Math.cos(e) * Math.sin(a), mid.y + r * Math.sin(e), mid.z + r * Math.cos(e) * Math.cos(a));
  controls.update(); renderer.render(scene, camera); };
window.__stats = { blocks: world.size, visible: n, size: [hi[0]-lo[0]+1, hi[1]-lo[1]+1, hi[2]-lo[2]+1] };
// render.py --glb: the build as a 3D model for the chat's inline viewer. Greedy meshing (one quad per same-coloured rectangle of
// visible faces, not one per block face) keeps a big build a few MB; GLTFExporter writes the binary glTF.
window.__glb = async () => {
  const { GLTFExporter } = await import("three/addons/exporters/GLTFExporter.js");
  const vol = new Uint8Array(G * G * G);                         // 0 = empty, else palette index + 1
  for (const [k, i] of world) vol[k] = i + 1;
  const at = (x, y, z) => (x < 0 || y < 0 || z < 0 || x >= G || y >= G || z >= G) ? 0 : vol[(x * G + y) * G + z];
  const pos = [], colr = [], idx = [];
  const tint = new THREE.Color();
  for (let d = 0; d < 3; d++) {                                  // the axis the faces look along
    const u = (d + 1) % 3, v = (d + 2) % 3, q = [0, 0, 0]; q[d] = 1;
    const mask = new Int16Array(G * G);
    for (let s = -1; s < G; s++) {                               // the plane between slice s and s+1
      for (let j = 0; j < G; j++) for (let i = 0; i < G; i++) {
        const p = [0, 0, 0]; p[d] = s; p[u] = i; p[v] = j;
        const a = at(p[0], p[1], p[2]), b = at(p[0] + q[0], p[1] + q[1], p[2] + q[2]);
        mask[j * G + i] = (a && !b) ? a : (!a && b) ? -b : 0;    // + faces the +axis side, - the -axis side
      }
      for (let j = 0; j < G; j++) for (let i = 0; i < G; ) {
        const m = mask[j * G + i];
        if (!m) { i++; continue; }
        let w = 1; while (i + w < G && mask[j * G + i + w] === m) w++;
        let h = 1;
        grow: for (; j + h < G; h++) for (let k = 0; k < w; k++) if (mask[(j + h) * G + i + k] !== m) break grow;
        for (let jj = 0; jj < h; jj++) for (let k = 0; k < w; k++) mask[(j + jj) * G + i + k] = 0;
        const o = [0, 0, 0]; o[d] = s + 1; o[u] = i; o[v] = j;
        const du = [0, 0, 0]; du[u] = w; const dv = [0, 0, 0]; dv[v] = h;
        const corners = [o, [o[0]+du[0], o[1]+du[1], o[2]+du[2]], [o[0]+du[0]+dv[0], o[1]+du[1]+dv[1], o[2]+du[2]+dv[2]], [o[0]+dv[0], o[1]+dv[1], o[2]+dv[2]]];
        const base = pos.length / 3;
        tint.setHex(COLORS[Math.abs(m) - 1]);
        for (const c of corners) { pos.push(c[0] - 0.5, c[1] - 0.5, c[2] - 0.5); colr.push(tint.r, tint.g, tint.b); }
        if (m > 0) idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
        else idx.push(base, base + 2, base + 1, base, base + 3, base + 2);
        i += w;
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colr, 3));
  geo.setIndex(pos.length / 3 > 65535 ? new THREE.Uint32BufferAttribute(idx, 1) : new THREE.Uint16BufferAttribute(idx, 1));
  geo.computeVertexNormals();
  const model = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9, metalness: 0 }));
  model.position.set(-mid.x, -lo[1], -mid.z);                    // centred on the ground, for the viewer's own camera
  const holder = new THREE.Scene(); holder.add(model);
  const glb = await new GLTFExporter().parseAsync(holder, { binary: true });
  window.__glbQuads = idx.length / 6;
  return Array.from(new Uint8Array(glb));
};
window.__ready = true;
</script></body></html>"""

def make_html(build: dict, title: str, subtitle: str = "") -> str:
    inp = build.get("input", build)
    esc = lambda t: t.replace("&", "&amp;").replace("<", "&lt;")   # noqa: E731
    return (PAGE.replace("__INPUT__", json.dumps(inp).replace("</", "<\\/"))
            .replace("__TITLE__", esc(title)).replace("__SUBTITLE__", esc(subtitle)))


if __name__ == "__main__":
    html = make_html(json.load(open(sys.argv[1], encoding="utf-8")), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(html)
    print("wrote", sys.argv[2], len(html), "bytes")
