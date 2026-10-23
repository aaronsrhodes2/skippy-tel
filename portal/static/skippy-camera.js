/* The camera key of Skippy's chat (the Captain only: templates/_chat.html renders the key for him alone).
 *
 *   const cam = SkippyCamera.attach({key: <button>, stage: <div>, budget: () => maxDataUrlChars, onPhoto(dataUrl, {width, height}), say(text)});
 *
 * Why: from the iPad he photographs another screen (a UI bug, another machine) and sends it, "literal screenshots".
 * TWO TAPS: the first opens a live viewfinder over the chat (the back camera; a key flips to the front one), the second (on the key,
 * the shutter or the picture itself) takes the frame. The camera is stopped AT ONCE, before anything else, so its light goes off;
 * nothing is recorded or kept running, and closing the viewfinder, leaving the page or Esc stops it too.
 * Readable text: the frame is taken at the camera's full resolution (the stream asks for the most it has) and is made smaller only
 * as far as the size limit requires (opts.budget(): the characters the JPEG data URL may have, the same limit pasted pictures have).
 * The photo then goes the way a pasted picture goes (opts.onPhoto). The front camera's preview is mirrored like a mirror; the photo is not.
 * On iPad the camera belongs to one app at a time: if Discord or FaceTime holds it, the key says so (and the viewfinder notes it once).
 * The pure helper fit() is exported for portal/tests/js/camera/skippy-camera.test.js.
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyCamera = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";
  const NOTED = "skippy-camera-noted";
  const ASK = 4096;                          // the stream asks for this much; the browser gives the most the camera has
  const MIN_EDGE = 480;                      // never smaller than this: past it a photo of a screen says nothing
  const QUALITY = [0.92, 0.85];              // full size at these first; only then smaller
  const WAIT_FRAME_MS = 5000;                // a camera that sends no picture this long is held by another app (iPad)
  const BUSY = "The camera is busy: another app (Discord, FaceTime) has it. On iPad only one app can use it at a time; close it there and tap again.";
  const ONCE = "On iPad the camera works for one app at a time: if Discord or FaceTime has it, close it there first.";

  // The largest JPEG of the frame that fits the budget: full size if it fits, else as little smaller as it takes.
  // encode(scale, quality) -> data URL. Returns {url, scale} or null when even MIN_EDGE doesn't fit.
  function fit(width, height, budget, encode) {
    for (const q of QUALITY) {
      const url = encode(1, q);
      if (url.length <= budget) return {url, scale: 1};
    }
    const q = QUALITY[QUALITY.length - 1], floor = Math.min(1, MIN_EDGE / Math.max(width, height));
    let k = 1, url = encode(1, q);
    for (let i = 0; i < 12 && k > floor; i++) {
      k = Math.max(floor, k * Math.min(0.92, Math.sqrt(budget / url.length) * 0.97));   // JPEG size goes about with the area
      url = encode(k, q);
      if (url.length <= budget) return {url, scale: k};
    }
    return null;
  }

  function why(err) {
    const n = (err && err.name) || "";
    if (n === "NotAllowedError" || n === "SecurityError") return "The camera isn't allowed for this site: in Safari, aA > Website Settings > Camera > Allow.";
    if (n === "NotFoundError" || n === "OverconstrainedError") return "This device has no camera the page can use.";
    if (n === "NotReadableError" || n === "AbortError" || n === "TrackStartError") return BUSY;
    return "The camera didn't start" + (err && err.message ? ": " + err.message : ".");
  }

  function attach(opts) {
    const doc = opts.doc || root.document, media = opts.media || (root.navigator && root.navigator.mediaDevices);
    const key = opts.key, stage = opts.stage, say = opts.say || function () {};
    const store = opts.store !== undefined ? opts.store : (function () { try { return root.localStorage; } catch (e) { return null; } })();
    let view = null, video = null, stream = null, facing = "environment", opening = 0, note = null;

    function lamp(s) { const l = key.querySelector(".lamp"); if (l) { if (s) l.dataset.s = s; else delete l.dataset.s; } }
    function tell(text) {                     // on the key (its title) and in the chat's status line; in the viewfinder too
      key.title = text; key.setAttribute("aria-label", text); lamp("warn"); say(text);
      if (note) { note.textContent = text; note.hidden = false; }
    }
    function calm() { key.title = "Camera: photograph a screen"; key.setAttribute("aria-label", key.title); lamp(null); }
    function stopStream() {
      if (stream) stream.getTracks().forEach(t => { try { t.stop(); } catch (e) {} });
      stream = null; if (video) video.srcObject = null;
    }
    function close() {
      opening++; stopStream();
      if (view) view.remove();
      view = video = note = null; key.classList.remove("lit"); key.setAttribute("aria-pressed", "false");
    }
    function button(glyph, label, cls, onClick) {
      const b = doc.createElement("button"); b.type = "button"; b.className = "key " + cls; b.title = label; b.setAttribute("aria-label", label);
      const s = doc.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("class", "g"); s.setAttribute("aria-hidden", "true");
      const u = doc.createElementNS("http://www.w3.org/2000/svg", "use"); u.setAttribute("href", "#g-" + glyph); s.appendChild(u); b.appendChild(s);
      b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); }); return b;
    }

    async function start() {
      const mine = ++opening;
      stopStream();
      video.classList.toggle("mirror", facing === "user");
      let s;
      try {
        s = await media.getUserMedia({audio: false, video: {facingMode: {ideal: facing}, width: {ideal: ASK}, height: {ideal: ASK}}});
      } catch (e) { if (mine === opening) tell(why(e)); return; }
      if (mine !== opening) { s.getTracks().forEach(t => t.stop()); return; }     // closed (or flipped) while it was starting
      stream = s;
      const track = s.getVideoTracks()[0];
      try {                                    // the most the camera has (Safari gives a modest size unless asked)
        const caps = track.getCapabilities ? track.getCapabilities() : {};
        if (caps.width && caps.width.max && caps.height && caps.height.max) await track.applyConstraints({width: {ideal: caps.width.max}, height: {ideal: caps.height.max}});
      } catch (e) {}
      track.addEventListener("ended", () => { if (stream === s) tell(BUSY); });   // taken by another app while open
      video.srcObject = s;
      try { await video.play(); } catch (e) {}
      const t0 = Date.now();
      while (mine === opening && !(video.videoWidth > 0)) {
        if (Date.now() - t0 > WAIT_FRAME_MS) { tell(BUSY); return; }
        await new Promise(r => setTimeout(r, 100));
      }
      if (mine === opening) { calm(); key.classList.add("lit"); }
    }

    function open() {
      if (!media || !media.getUserMedia) { tell("This browser can't use the camera here."); return; }
      view = doc.createElement("div"); view.className = "camview";
      video = doc.createElement("video"); video.setAttribute("playsinline", ""); video.setAttribute("muted", ""); video.setAttribute("autoplay", "");
      video.playsInline = true; video.muted = true; video.autoplay = true;
      video.addEventListener("click", snap);
      note = doc.createElement("p"); note.className = "cam-note"; note.hidden = true;
      let noted = false;
      try { noted = !!(store && store.getItem(NOTED)); } catch (e) {}
      if (!noted) { note.textContent = ONCE; note.hidden = false; try { if (store) store.setItem(NOTED, "1"); } catch (e) {} }
      const keys = doc.createElement("div"); keys.className = "cam-keys";
      keys.append(button("x", "Close the camera", "cam-close", close),
                  button("camera", "Take the photo", "cam-snap", snap),
                  button("flip", "Front or back camera", "cam-flip", () => { facing = facing === "user" ? "environment" : "user"; start(); }));
      view.append(video, note, keys); stage.appendChild(view);
      key.setAttribute("aria-pressed", "true");
      start();
    }

    function snap() {
      if (!video || !stream || !(video.videoWidth > 0)) return;          // nothing to take yet: the viewfinder stays
      const w = video.videoWidth, h = video.videoHeight;
      const full = doc.createElement("canvas"); full.width = w; full.height = h;
      full.getContext("2d").drawImage(video, 0, 0, w, h);
      close();                                                            // the camera light goes off now, before the encoding
      const got = fit(w, h, opts.budget(), (k, q) => {
        if (k === 1) return full.toDataURL("image/jpeg", q);
        const c = doc.createElement("canvas"); c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
        c.getContext("2d").drawImage(full, 0, 0, c.width, c.height);
        return c.toDataURL("image/jpeg", q);
      });
      if (!got) { say("No room for another picture in this message: send these first."); return; }
      opts.onPhoto(got.url, {width: Math.round(w * got.scale), height: Math.round(h * got.scale)});
    }

    key.addEventListener("click", () => {
      if (view && stream && video.videoWidth > 0) return snap();
      if (view) close();                                                  // no picture (busy, refused): a tap tries again
      open();
    });
    doc.addEventListener("visibilitychange", () => { if (doc.hidden && view) close(); });     // never left running behind the page
    doc.addEventListener("keydown", (e) => { if (e.key === "Escape" && view) close(); });
    calm();
    return {open, close, snap, isOpen: () => !!view};
  }

  return {attach, fit, BUSY, ONCE};
});
