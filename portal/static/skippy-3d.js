/* Skippy the Magnificent, alive in the corner of the chat. A module: loaded with an import map for three.js (see templates/_skippy3d.html).
 *
 * The model (static/skippy/skippy.glb) is the licensed print kit, decimated: body, can, hat, head, hand_l, hand_r. Nothing here is baked
 * animation; it is all driven live, so he can react to what the chat is doing:
 *   idle      a slow float, a sway, the hat a beat behind the head, the pointing hand bobbing
 *   speaking  nods and chatters, hat bouncing (set by the "skippy-speaking" event, which skippy-voice.js fires while a reply plays)
 *   thinking  head tilted, still (the "skippy-thinking" event; chat forms fire it while waiting for an answer)
 * Anything may fire:  window.dispatchEvent(new CustomEvent("skippy-speaking", {detail: true}))
 * If WebGL or the model fails, or the person prefers reduced motion, the still picture stays (with a CSS bob unless reduced motion).
 * He can be hidden (the x) and brought back (his face); the choice is remembered in this browser only.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const root = document.getElementById("skippy3d");
const stage = root && root.querySelector(".s3-stage");
const KEY = "skippy-3d-hidden";
const MODEL = root && root.dataset.model;
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

function remembered() { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; } }
function remember(on) { try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* private window: fine */ } }

if (root) {
  const show = hidden => { root.classList.toggle("is-hidden", hidden); remember(hidden); };
  root.querySelector(".s3-x").addEventListener("click", () => show(true));
  root.querySelector(".s3-face").addEventListener("click", () => show(false));
  root.classList.toggle("is-hidden", remembered());
  if (!reduced) start().catch(e => { console.warn("skippy-3d: staying a picture:", e); root.dataset.err = String(e && e.message || e); root.classList.add("is-still"); });       // the picture stays if anything goes wrong
  else root.classList.add("is-reduced");
}

async function start() {
  const canvas = document.createElement("canvas");
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x303050, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 2.4); sun.position.set(-1.5, 2.5, 3); scene.add(sun);
  const rim = new THREE.DirectionalLight(0xa66cff, 1.4); rim.position.set(2, 1, -2); scene.add(rim);

  const gltf = await new GLTFLoader().loadAsync(MODEL);
  const skippy = new THREE.Group();
  skippy.add(gltf.scene);
  scene.add(skippy);

  const part = {};
  gltf.scene.traverse(o => { if (o.isMesh) part[o.name.replace(/\.\d+$/, "")] = o; });
  // Meshes sit in world coordinates (feet at the origin), so each moving part gets a pivot at the right joint.
  function pivot(mesh, where) {
    const box = new THREE.Box3().setFromObject(mesh), c = box.getCenter(new THREE.Vector3());
    const p = new THREE.Group();
    p.position.set(c.x, where === "base" ? box.min.y : c.y, c.z);
    mesh.parent.add(p); p.attach(mesh);
    return p;
  }
  const head = part.head && pivot(part.head, "base");
  const hat = part.hat && pivot(part.hat, "base");
  if (head && hat) head.attach(hat);                                      // the hat rides the head
  const pointer = part.hand_r && pivot(part.hand_r, "centre");
  const rest = part.hand_l && pivot(part.hand_l, "centre");

  const box = new THREE.Box3().setFromObject(gltf.scene), size = box.getSize(new THREE.Vector3()), mid = box.getCenter(new THREE.Vector3());
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 1000);
  function frame() {
    const w = stage.clientWidth || 160, h = stage.clientHeight || 200;
    renderer.setSize(w, h, false); camera.aspect = w / h;
    const fit = Math.max(size.y, size.x / camera.aspect) * 0.62 / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    camera.position.set(mid.x, mid.y, mid.z + fit); camera.lookAt(mid); camera.updateProjectionMatrix();
  }
  stage.append(canvas); frame();
  new ResizeObserver(frame).observe(stage);
  root.classList.add("is-3d");

  let speaking = false, thinking = false, level = 0, tilt = 0;
  addEventListener("skippy-speaking", e => { speaking = !!e.detail; });
  addEventListener("skippy-thinking", e => { thinking = !!e.detail; });
  let visible = true;
  new IntersectionObserver(es => { visible = es[0].isIntersecting; }).observe(root);
  const base = { head: head && head.rotation.clone(), hat: hat && hat.rotation.clone(), y: skippy.position.y };

  let last = performance.now();
  function tick(now) {
    requestAnimationFrame(tick);
    if (!visible || root.classList.contains("is-hidden") || document.hidden) { last = now; return; }
    const dt = Math.min(0.05, (now - last) / 1000), t = now / 1000; last = now;
    level += ((speaking ? 1 : 0) - level) * Math.min(1, dt * 8);                    // how talkative he is, eased
    tilt += ((thinking && !speaking ? 1 : 0) - tilt) * Math.min(1, dt * 4);
    const chatter = Math.sin(t * 13) * 0.5 + Math.sin(t * 7.3 + 1) * 0.5;           // not a metronome
    skippy.position.y = base.y + Math.sin(t * 1.6) * 0.9 + level * Math.abs(Math.sin(t * 6.5)) * 0.9;
    skippy.rotation.y = Math.sin(t * 0.55) * 0.22 + level * Math.sin(t * 2.1) * 0.12;
    if (head) {
      head.rotation.x = base.head.x + Math.sin(t * 1.1) * 0.04 + level * (0.10 + chatter * 0.10);
      head.rotation.z = base.head.z + Math.sin(t * 0.8) * 0.05 + tilt * 0.22;
      head.scale.y = 1 + level * 0.035 * (chatter * 0.5 + 0.5);
    }
    if (hat) {
      hat.rotation.x = base.hat.x + Math.sin(t * 1.1 - 0.5) * 0.05 + level * Math.sin(t * 9) * 0.06;
      hat.rotation.z = base.hat.z + Math.sin(t * 0.8 - 0.6) * 0.04 - tilt * 0.05;
    }
    if (pointer) pointer.rotation.z = Math.sin(t * 2.4) * 0.05 + level * Math.sin(t * 5.2) * 0.1;
    if (rest) rest.rotation.z = Math.sin(t * 1.3 + 2) * 0.04;
    renderer.render(scene, camera);
  }
  requestAnimationFrame(tick);
}
