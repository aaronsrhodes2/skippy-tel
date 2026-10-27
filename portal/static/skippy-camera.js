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
 * MORE THAN ONE KEY IN THE VIEWFINDER (the Captain, 2026-10-27): besides the shutter and the flip there is
 *   - EVERY CAMERA: one tap takes a picture with each camera the device has, one after the other (front and back when both),
 *     each its own file; every camera is stopped as soon as its frame is taken;
 *   - SCREEN: a picture of what is on the screen now. Where the browser can capture the screen (getDisplayMedia: desktops) that
 *     is the real screen, one frame, the capture stopped at once; elsewhere (iPhone and iPad Safari cannot) it is a snapshot of
 *     this page, drawn by the self-hosted html2canvas (static/vendor/html2canvas.min.js) when the page loaded it.
 * Every picture goes to opts.onPhoto(dataUrl, {width, height, name, kind}); it returns false when the message has no room left,
 * which stops a run of pictures. They arrive in the chat as ordinary attachments, each with its remove key.
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
    function calm() { key.title = "Camera: photos, every camera, or the screen"; key.setAttribute("aria-label", key.title); lamp(null); }
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
                  button("flip", "Front or back camera", "cam-flip", () => { facing = facing === "user" ? "environment" : "user"; start(); }),
                  button("stack", "Take a picture with every camera", "cam-all", snapAll),
                  button("image", "Take a picture of the screen", "cam-screen", screen));
      view.append(video, note, keys); stage.appendChild(view);
      key.setAttribute("aria-pressed", "true");
      start();
    }

    // A canvas holding a finished frame -> the largest JPEG that fits the budget -> the chat. false = no room (stop taking more).
    function deliver(full, w, h, meta) {
      const got = fit(w, h, opts.budget(), (k, q) => {
        if (k === 1) return full.toDataURL("image/jpeg", q);
        const c = doc.createElement("canvas"); c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
        c.getContext("2d").drawImage(full, 0, 0, c.width, c.height);
        return c.toDataURL("image/jpeg", q);
      });
      if (!got) { say("No room for another picture in this message: send these first."); return false; }
      return opts.onPhoto(got.url, Object.assign({width: Math.round(w * got.scale), height: Math.round(h * got.scale)}, meta)) !== false;
    }
    function frameOf(v) {
      const w = v.videoWidth, h = v.videoHeight, full = doc.createElement("canvas"); full.width = w; full.height = h;
      full.getContext("2d").drawImage(v, 0, 0, w, h);
      return {full, w, h};
    }

    function snap() {
      if (!video || !stream || !(video.videoWidth > 0)) return;          // nothing to take yet: the viewfinder stays
      const f = frameOf(video), kind = facing === "user" ? "front" : "back";
      close();                                                            // the camera light goes off now, before the encoding
      deliver(f.full, f.w, f.h, {name: "camera-" + kind, kind: "camera"});
    }

    // One picture from one specific camera, then it is stopped. {err} when it gave no frame in time.
    async function takeFrom(constraints) {
      let s;
      try { s = await media.getUserMedia({audio: false, video: constraints}); } catch (e) { return {err: e}; }
      const v = doc.createElement("video"); v.setAttribute("playsinline", ""); v.playsInline = true; v.muted = true; v.srcObject = s;
      try {
        try { await v.play(); } catch (e) {}
        const t0 = Date.now();
        while (!(v.videoWidth > 0)) {
          if (Date.now() - t0 > WAIT_FRAME_MS) return {err: {name: "NotReadableError"}};
          await new Promise(r => setTimeout(r, 100));
        }
        await new Promise(r => setTimeout(r, 250));                      // a first frame is often dark: let the exposure settle
        const tr = s.getVideoTracks()[0], st = tr && tr.getSettings ? tr.getSettings() : {};
        return {frame: frameOf(v), facing: st.facingMode || ""};
      } finally { s.getTracks().forEach(t => { try { t.stop(); } catch (e) {} }); v.srcObject = null; }
    }
    function cameraName(label, facingMode, index) {
      const l = String(label || "").toLowerCase();
      if (facingMode === "user" || /front|user|face/.test(l)) return "front";
      if (facingMode === "environment" || /back|rear|environment/.test(l)) return "back";
      return "camera" + (index + 1);
    }

    // EVERY CAMERA: each video input the device has, one after the other.
    async function snapAll() {
      if (!media || !media.enumerateDevices) { snap(); return; }
      close();                                                            // the viewfinder's own stream first: some devices open one camera at a time
      let devices = [];
      try { devices = (await media.enumerateDevices()).filter(d => d.kind === "videoinput"); } catch (e) {}
      if (devices.length < 2) {                                           // one camera (or none listed): just that one
        const only = await takeFrom({facingMode: {ideal: "environment"}, width: {ideal: ASK}, height: {ideal: ASK}});
        if (only.err) { tell(why(only.err)); return; }
        deliver(only.frame.full, only.frame.w, only.frame.h, {name: "camera-" + cameraName("", only.facing, 0), kind: "camera"});
        return;
      }
      let taken = 0;
      for (let i = 0; i < devices.length; i++) {
        const got = await takeFrom({deviceId: {exact: devices[i].deviceId}, width: {ideal: ASK}, height: {ideal: ASK}});
        if (got.err) { if (!taken) { tell(why(got.err)); return; } continue; }
        const name = "camera-" + cameraName(devices[i].label, got.facing, i);
        if (!deliver(got.frame.full, got.frame.w, got.frame.h, {name, kind: "camera"})) break;
        taken++;
      }
    }

    // SCREEN: the real screen where the browser allows it, otherwise a snapshot of this page.
    async function screen() {
      close();
      if (media && media.getDisplayMedia) {
        let s = null;
        try { s = await media.getDisplayMedia({video: true, audio: false}); }
        catch (e) { if (e && e.name === "NotAllowedError") { say("Screen capture was cancelled."); return; } }
        if (s) {
          try {
            const v = doc.createElement("video"); v.muted = true; v.playsInline = true; v.srcObject = s;
            try { await v.play(); } catch (e) {}
            const t0 = Date.now();
            while (!(v.videoWidth > 0) && Date.now() - t0 < WAIT_FRAME_MS) await new Promise(r => setTimeout(r, 100));
            if (v.videoWidth > 0) { const f = frameOf(v); deliver(f.full, f.w, f.h, {name: "screen", kind: "screen"}); return; }
          } finally { s.getTracks().forEach(t => { try { t.stop(); } catch (e) {} }); }
        }
      }
      if (typeof root.html2canvas === "function") {                       // iPhone, iPad, Android: this page, as it looks now
        try {
          const canvas = await root.html2canvas(doc.body, {backgroundColor: null, logging: false, useCORS: true, scale: Math.min(2, root.devicePixelRatio || 1)});
          deliver(canvas, canvas.width, canvas.height, {name: "screen", kind: "screen"});
          return;
        } catch (e) { say("The page couldn't be captured: " + (e && e.message ? e.message : "unknown error")); return; }
      }
      say("This browser can't take a screenshot here.");
    }

    key.addEventListener("click", () => {
      if (view && stream && video.videoWidth > 0) return snap();
      if (view) close();                                                  // no picture (busy, refused): a tap tries again
      open();
    });
    doc.addEventListener("visibilitychange", () => { if (doc.hidden && view) close(); });     // never left running behind the page
    doc.addEventListener("keydown", (e) => { if (e.key === "Escape" && view) close(); });
    calm();
    return {open, close, snap, snapAll, screen, isOpen: () => !!view};
  }

  return {attach, fit, BUSY, ONCE};
});
