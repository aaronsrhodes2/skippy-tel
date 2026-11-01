/* Skippy the Magnificent, the 2D animated face in the corner of the chat.
 *
 * The face is drawn in the browser by skippy-face.js (canvas renderer).
 * This controller drives it via custom events from the voice layer and
 * taps the reply audio element for mouth sync.
 *
 * Events listened for:
 *   skippy-speaking — detail: true when speaking starts, false when it ends
 *   skippy-thinking — detail: true when thinking, false when done
 *
 * Hide/show is remembered in localStorage.
 */

const root = document.getElementById("skippy3d");
if (!root) { throw new Error("skippy-3d: #skippy3d not found"); }

const KEY = "skippy-3d-hidden";
const hideBtn = root.querySelector(".s3-x");
const showBtn = root.querySelector(".s3-face");

function remembered() {
  try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; }
}
function remember(on) {
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* private window */ }
}

/* Hide / show */
const hide = hidden => {
  root.classList.toggle("is-hidden", hidden);
  remember(hidden);
};
hideBtn.addEventListener("click", () => hide(true));
showBtn.addEventListener("click", () => hide(false));
root.classList.toggle("is-hidden", remembered());

/* Drive face state from custom events */
const face = window._skippyFace;
if (!face) { throw new Error("skippy-3d: _skippyFace not initialised (skippy-face.js missing)"); }

addEventListener("skippy-speaking", e => {
  if (e.detail) {
    face.set_state("speaking");
    /* Tap the reply audio element for mouth-level sync */
    const audio = document.querySelector("audio");
    if (audio && !face._analyser) {
      try {
        const ac = new (window.AudioContext || window.webkitAudioContext)();
        const src = ac.createMediaElementSource(audio);
        const an = ac.createAnalyser();
        an.fftSize = 256;
        src.connect(an);
        an.connect(ac.destination);
        face._analyser = an;
        face._ac = ac;
        face._buf = new Float32Array(an.frequencyBinCount);
      } catch (_) { /* CORS or audio element missing — mouth stays closed */ }
    }
  } else {
    face.set_state("idle");
    face.mouthLevel = 0;
    if (face._analyser) {
      face._analyser.disconnect && face._analyser.disconnect();
      face._analyser = null;
    }
  }
});

addEventListener("skippy-thinking", e => {
  face.set_state(e.detail ? "thinking" : "idle");
});

/* Audio level → mouth animation (called from the render loop in the template) */
setInterval(() => {
  if (face._analyser && face._buf) {
    face._analyser.getFloatTimeDomainData(face._buf);
    let sum = 0;
    for (let i = 0; i < face._buf.length; i++) sum += face._buf[i] * face._buf[i];
    face.mouthLevel = Math.sqrt(sum / face._buf.length) * 4; // scale up
    if (face.mouthLevel > 1) face.mouthLevel = 1;
  }
}, 50);
