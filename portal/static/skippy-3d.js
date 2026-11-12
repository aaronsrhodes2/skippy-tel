/* Skippy the Magnificent in the corner of the chat: the pods' own animated face (static/skippy-face.js, a port of the hub's
 * face_render.py), drawn on a canvas in this browser. No model, no stream, nothing from home.
 *
 * What he shows comes from the page:
 *   skippy-speaking  {detail: true|false}  a reply is being spoken (skippy-voice.js, from its speaker lamp)
 *   skippy-thinking  {detail: true|false}  an answer is on its way (the chat, from its Stop key)
 *   skippy-listening {detail: true|false}  Hands-off is listening (skippy-voice.js, from its mic lamp)
 * Anything may fire them:  window.dispatchEvent(new CustomEvent("skippy-thinking", {detail: true}))
 * The still picture stays until the can's five images have loaded, and for good if they fail or motion is reduced. He can be hidden
 * (the x) and brought back (his face); the choice is remembered in this browser only. Nothing runs while he is hidden or off screen.
 */
(function () {
  "use strict";
  const root = document.getElementById("skippy3d");
  if (!root || !window.SkippyFace) return;
  const KEY = "skippy-3d-hidden";
  const canvas = root.querySelector(".s3-canvas");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function remembered() { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; } }
  function remember(on) { try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* private window: fine */ } }
  const show = hidden => { root.classList.toggle("is-hidden", hidden); remember(hidden); };
  root.querySelector(".s3-x").addEventListener("click", () => show(true));
  root.querySelector(".s3-face").addEventListener("click", () => show(false));
  root.classList.toggle("is-hidden", remembered());
  if (reduced || !canvas || !canvas.getContext) { root.classList.add("is-still"); return; }

  const face = new window.SkippyFace();
  const flags = { speaking: false, thinking: false, listening: false };
  function apply() {                                    // speaking wins, then thinking, then listening, else idle
    const was = face.talking;
    face.talking = flags.speaking;
    if (face.talking && !was) face.talkT0 = performance.now();
    face.showMic = flags.listening;
    face.setSession(flags.listening);
    face.setState(flags.speaking ? "speaking" : flags.thinking ? "thinking" : flags.listening ? "listening" : "idle");
  }
  for (const name of ["speaking", "thinking", "listening"]) addEventListener("skippy-" + name, e => { flags[name] = !!e.detail; apply(); });
  apply();

  const base = root.dataset.frames;
  Promise.all([0, 1, 2, 3, 4].map(i => new Promise((ok, fail) => {
    const im = new Image();
    im.onload = () => ok(im); im.onerror = fail;
    im.src = base + "0" + i + ".png";
  }))).then(balls => {
    face.balls = balls;
    const ctx = canvas.getContext("2d");
    let visible = true;
    new IntersectionObserver(es => { visible = es[0].isIntersecting; }).observe(root);
    let started = false;
    setInterval(() => {                                 // the pod's own 50 ms tick, and a frame after each
      if (!visible || document.hidden || root.classList.contains("is-hidden")) return;
      const now = performance.now();
      face.tick(now);
      face.render(ctx);
      if (!started) { started = true; root.classList.add("is-live"); }
    }, 50);
  }, e => { console.warn("skippy-3d: staying a picture:", e); root.classList.add("is-still"); });
}());
