/* Skippy-vision, the page's half (portal/vision.py has the other): one flash of everything.

   SkippyVision.flashWord(text)      "snap" / "flash" -> "flash"; "snap it big time" / "hold it" -> "hold"; anything else -> null
   SkippyVision.readWord(text)       "read that" -> "read"; "what am I looking at" -> "look"; "steady on|off" -> "steady-on|steady-off"; else null
   SkippyVision.hostWord(text)       "record a walk" -> "record-on"; "stop recording" -> "record-off"; else null
   SkippyVision.attach(cfg)          the flash itself: {flash(hold, by), forget(id), keep(id, on), addPage(id, name)}, and what he sees: {frame(),
                                     readSigns(picture, moment) -> [{text, lang, en}] | null, lookAt(picture, moment) -> a sentence | null}; cfg = the
                                     page's data-* URLs (flash, read, look, momentAdd ...) and hooks
   SkippyVision.parse / names ...    small pure pieces (tests/js/vision.test.js runs them under Node)

   A flash gathers, at that instant and in parallel: a screenshot of this page, a still from every camera this device has, and (when
   OpenViture hosts the page on the glasses: window.OpenViture) the glasses' three cameras. They go to the portal in one request; the
   pods' and the PC's screens come from skippy-pc without us, and a hold's clip (7 s before, 3 s after) follows 3.2 s later from the host. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.SkippyVision = factory(root);
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  const HOLD_MS = 600;                 // a press this long is a hold
  const CLIP_AFTER_MS = 3200;          // the 3 s after, plus a little
  const PAGE_SCALE = 0.6, PAGE_QUALITY = 0.8, CAM_QUALITY = 0.85, CAM_WIDTH = 1280, CAM_WARM_MS = 400, CAM_TIMEOUT_MS = 4000;
  const MAX_CAMERAS = 3;
  const strip = t => String(t || "").trim().toLowerCase().replace(/[.!?,]+$/g, "").replace(/\s+/g, " ");

  function flashWord(text) {
    const t = strip(text);
    if (/^(snap it big time|snap big time|hold it|hold that|snap and hold|flash and hold)$/.test(t)) return "hold";
    if (/^(snap|flash)( it| that| this)?$/.test(t)) return "flash";
    return null;
  }

  function hostWord(text) {                                // about the glasses' recorder: "record a walk" / "stop recording"
    const t = strip(text);
    if (/^(record a walk|record the walk|start recording|start recording a walk)$/.test(t)) return "record-on";
    if (/^(stop recording|stop the recording|end the walk|stop recording the walk)$/.test(t)) return "record-off";
    return null;
  }

  function readWord(text) {
    const t = strip(text);
    if (/^(read (that|this|it)|what does (that|this|it) say|translate (that|this|it))$/.test(t)) return "read";
    if (/^(what am i looking at|what is that|what's that|what is this|what's this)$/.test(t)) return "look";
    if (/^steady (reading )?on$/.test(t)) return "steady-on";
    if (/^steady (reading )?off$/.test(t)) return "steady-off";
    return null;
  }

  const cameraName = n => "phone-cam-" + n + ".jpg";
  const bytesOf = b64 => { const bin = atob(b64); const out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; };
  const dataUrl = b64 => "data:image/jpeg;base64," + b64;

  /* What the host's flash() answered ({rgb, left, right, batteryC, thermal} as base64 JPEGs), as the portal's named stills. */
  function glassesStills(host) {
    const got = [];
    for (const [key, name] of [["rgb", "glasses-rgb.jpg"], ["left", "glasses-left.jpg"], ["right", "glasses-right.jpg"]])
      if (host && typeof host[key] === "string" && host[key].length > 100) got.push({ name: name, data: dataUrl(host[key]) });
    return got;
  }

  function parse(json) { try { return JSON.parse(json); } catch (e) { return null; } }

  // ── the browser's parts ──
  function blobToDataUrl(blob) {
    return new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => fail(r.error); r.readAsDataURL(blob); });
  }
  const canvasBlob = (canvas, q) => new Promise(ok => canvas.toBlob(ok, "image/jpeg", q));
  const within = (promise, ms) => Promise.race([promise, new Promise((_, no) => setTimeout(() => no(new Error("timed out")), ms))]);

  function loadScript(doc, src) {
    return new Promise((ok, fail) => {
      if (doc.querySelector('script[data-vision="' + src + '"]')) return ok();
      const s = doc.createElement("script"); s.src = src; s.dataset.vision = src; s.onload = () => ok(); s.onerror = () => fail(new Error("script"));
      doc.head.appendChild(s);
    });
  }

  async function pageShot(doc, cfg) {                        // a screenshot of this page, smaller than life: it is a record, not a poster
    try {
      if (!root.html2canvas) await loadScript(doc, cfg.html2canvas);
      const canvas = await within(root.html2canvas(doc.body, { scale: PAGE_SCALE, backgroundColor: "#000", logging: false, useCORS: true }), 6000);
      const blob = await canvasBlob(canvas, PAGE_QUALITY);
      return blob ? await blobToDataUrl(blob) : null;
    } catch (e) { return null; }
  }

  async function oneCamera(nav, doc, deviceId) {
    let stream = null;
    try {
      stream = await within(nav.mediaDevices.getUserMedia({ video: { deviceId: { exact: deviceId }, width: { ideal: CAM_WIDTH } }, audio: false }), CAM_TIMEOUT_MS);
      const video = doc.createElement("video"); video.muted = true; video.playsInline = true; video.srcObject = stream;
      await within(new Promise(ok => { video.onloadeddata = ok; video.play().catch(() => {}); }), CAM_TIMEOUT_MS);
      await new Promise(ok => setTimeout(ok, CAM_WARM_MS));                 // a camera that has only just opened is still choosing its exposure
      const canvas = doc.createElement("canvas"); canvas.width = video.videoWidth || CAM_WIDTH; canvas.height = video.videoHeight || 720;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await canvasBlob(canvas, CAM_QUALITY);
      return blob ? await blobToDataUrl(blob) : null;
    } catch (e) { return null; }
    finally { if (stream) stream.getTracks().forEach(t => t.stop()); }
  }

  async function cameraStills(nav, doc) {                    // one frame from each camera this device has, one after another (a phone opens one at a time)
    if (!nav || !nav.mediaDevices || !nav.mediaDevices.enumerateDevices) return [];
    let cams = [];
    try { cams = (await nav.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput").slice(0, MAX_CAMERAS); } catch (e) { return []; }
    const out = [];
    for (let i = 0; i < cams.length; i++) {
      const url = await oneCamera(nav, doc, cams[i].deviceId);
      if (url) out.push({ name: cameraName(i + 1), data: url });
    }
    return out;
  }

  /* The flash. cfg: {flash, momentAdd (with _MID_), momentDelete (with _MID_), momentKeep (with _MID_), html2canvas, doc, nav, host,
     fetch, say(text, moment), onSaved(moment, glassesFrameDataUrl|null), wait}. Every hook is the page's own. */
  function attach(cfg) {
    const doc = cfg.doc || root.document, nav = cfg.nav || root.navigator, host = cfg.host !== undefined ? cfg.host : root.OpenViture;
    const post = cfg.fetch || ((url, init) => root.fetch(url, Object.assign({ credentials: "same-origin" }, init)));
    const wait = cfg.wait || (ms => new Promise(ok => setTimeout(ok, ms)));
    let busy = false;
    const url = (tpl, id) => tpl.replace("_MID_", encodeURIComponent(id));

    async function flash(hold, by) {
      if (busy) return null;                                // a second tap while the first is being taken is the same moment
      busy = true;
      try {
        const hosted = host && typeof host.flash === "function" ? parse(host.flash(!!hold)) : null;      // at the instant, before anything slow
        const [shot, cams] = await Promise.all([cfg.page === false ? null : pageShot(doc, cfg), cameraStills(nav, doc)]);
        const images = [];
        if (shot) images.push({ name: cfg.pageName || "hud-page.jpg", data: shot });
        images.push(...cams, ...glassesStills(hosted));
        const r = await post(cfg.flash, { method: "POST", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify({ hold: !!hold, by: by || "key", images: images }) });
        const moment = r.ok ? await r.json() : null;
        if (!moment) { if (cfg.say) cfg.say("Not saved."); return null; }
        if (cfg.onSaved) cfg.onSaved(moment, hosted && hosted.rgb ? dataUrl(hosted.rgb) : (cams[0] ? cams[0].data : null));
        if (hold && host && typeof host.flashClip === "function") clip(moment.id, wait);
        return moment;
      } catch (e) {
        if (cfg.say) cfg.say("Not saved.");
        return null;
      } finally { busy = false; }
    }

    async function clip(id, waiter) {                        // the 7 s before and 3 s after, from the glasses: a moment later, as one zip
      await (waiter || wait)(CLIP_AFTER_MS);
      try {
        const b64 = host.flashClip();
        if (!b64 || b64 === "null") return false;
        const r = await post(url(cfg.momentAdd, id) + "?name=glasses-rgb-7s.zip", { method: "POST", headers: { "Content-Type": "application/zip" }, body: new Blob([bytesOf(b64)]) });
        return r.ok;
      } catch (e) { return false; }
    }

    const forget = id => post(url(cfg.momentDelete, id), { method: "POST" }).then(r => r.ok, () => false);
    const keep = (id, on) => post(url(cfg.momentKeep, id), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: !!on }) }).then(r => r.ok, () => false);

    async function frame() {                                 // the freshest picture of what he is looking at: the glasses' RGB camera, else the phone's first camera
      if (host && typeof host.frame === "function") {
        try { const b = host.frame(); if (typeof b === "string" && b.length > 100) return dataUrl(b); } catch (e) { /* fall back to the phone */ }
      }
      const cams = await cameraStills(nav, doc);
      return cams[0] ? cams[0].data : null;
    }

    async function askAbout(url, picture, moment) {         // one picture to the portal's reader (/vision/read, /vision/look); null when it can't be asked
      if (!url || !picture) return null;
      try {
        const r = await post(url + (moment ? "?moment=" + encodeURIComponent(moment) : ""),
                             { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ image: picture }) });
        return r.ok ? await r.json() : null;
      } catch (e) { return null; }
    }
    const readSigns = async (picture, moment) => { const d = await askAbout(cfg.read, picture, moment); return d && Array.isArray(d.seen) ? d.seen : null; };
    const lookAt = async (picture, moment) => { const d = await askAbout(cfg.look, picture, moment); return d && d.said ? String(d.said) : null; };

    async function addPage(id, name) {                       // this page's picture, added to a moment someone else made (the Tesla's Drive joining a flash)
      try {
        const shot = await pageShot(doc, cfg);
        if (!shot) return false;
        const r = await post(url(cfg.momentAdd, id) + "?name=" + encodeURIComponent(name || cfg.pageName || "hud-page.jpg"),
                             { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: new Blob([bytesOf(shot.split(",")[1])], { type: "image/jpeg" }) });
        return r.ok;
      } catch (e) { return false; }
    }
    return { flash: flash, forget: forget, keep: keep, clip: clip, addPage: addPage, frame: frame, readSigns: readSigns, lookAt: lookAt, hosted: () => !!(host && typeof host.flash === "function") };
  }

  return { flashWord, readWord, hostWord, attach, glassesStills, cameraName, parse, HOLD_MS, CLIP_AFTER_MS, MAX_CAMERAS };
});
