/* Skippy's ears and mouth for the portal's chats (/chat and the personal Skippy on /me).
 *
 *   const voice = SkippyVoice.attach({gate: "/chat/voice/gate", mic: <button>, speaker: <button>, onText(text), lang(): "en"});
 *   voice.say(replyText);          // reads it aloud if the speaker key is on
 *
 * Hearing: the mic key records with MediaRecorder (any current browser: Chrome, Firefox, Safari, on phones too) and sends the
 * recording to skippy-pc's Whisper through the voice gate; the words land in the message box via onText. Nothing is sent
 * anywhere else, and nothing is recorded until the key is tapped.
 * Speaking: the speaker key turns on reading replies aloud. The Captain's pass lets the gate speak in Skippy's voice; every
 * other pass (and any failure) falls back to the device's own built-in voice, so it works on every device either way.
 * Lamps: mic data-s = "on" recording, "boot" understanding, "warn" it didn't work; speaker data-s = "on" speaking.
 * The pure helpers (pickMime, speakable) are exported for tests/js/skippy-voice.test.js.
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyVoice = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";
  const MIMES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4", "audio/aac"];
  const MAX_SPEECH = 500, MAX_REC_MS = 60000, SPEAK_KEY = "skippy-voice-speak";
  // The silent clip that unlocks audio playback inside a tap (phones refuse sound that no tap started).
  const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";

  function pickMime(isSupported) {
    for (const m of MIMES) { try { if (isSupported(m)) return m; } catch (e) {} }
    return "";
  }

  /* What is worth saying aloud: no code chips, links or markdown marks, and at most MAX_SPEECH characters, cut at a sentence. */
  function speakable(text) {
    let t = String(text || "").replace(/`([^`\n]*)`/g, "$1").replace(/https?:\/\/\S+/g, "").replace(/[*_#>~]+/g, "").replace(/\s+/g, " ").trim();
    if (t.length <= MAX_SPEECH) return t;
    const cut = t.slice(0, MAX_SPEECH);
    let at = -1;
    for (const sep of [". ", "! ", "? "]) at = Math.max(at, cut.lastIndexOf(sep));
    return (at > MAX_SPEECH / 2 ? cut.slice(0, at + 1) : cut).trim();
  }

  function attach(opts) {
    const mic = opts.mic, speaker = opts.speaker, onText = opts.onText || function () {};
    const lang = opts.lang || function () { return ""; };
    const canRecord = !!(root.navigator && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && root.MediaRecorder);
    if (mic && !canRecord) mic.hidden = true;
    let pass = null, wanted = null, recorder = null, stream = null, stopTimer = null, playing = null;
    const audio = new Audio();

    function paint(el, s) { if (!el) return; if (s) el.dataset.s = s; else delete el.dataset.s; el.classList.toggle("lit", s === "on"); }
    function warn(el) { paint(el, "warn"); setTimeout(function () { if (el.dataset.s === "warn") paint(el, null); }, 4000); }

    async function gate() {
      if (pass && pass.exp * 1000 - Date.now() > 300000) return pass;
      const r = await fetch(opts.gate, { cache: "no-store", credentials: "same-origin" });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.why || "voice is out of reach");
      pass = d;
      return d;
    }
    const base = function (g) { return g.url.replace(/\/$/, "") + "/t/" + g.pass; };

    async function transcribe(blob, type) {
      let g = await gate();
      const q = lang() ? "?lang=" + encodeURIComponent(lang()) : "";
      let r = await fetch(base(g) + "/stt" + q, { method: "POST", headers: { "Content-Type": type }, body: blob });
      if (r.status === 403) { pass = null; g = await gate(); r = await fetch(base(g) + "/stt" + q, { method: "POST", headers: { "Content-Type": type }, body: blob }); }
      if (!r.ok) throw new Error("not understood");
      return (await r.json()).text || "";
    }

    function release() { if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; clearTimeout(stopTimer); }

    async function start() {
      stopSpeaking();
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mime = pickMime(function (m) { return MediaRecorder.isTypeSupported(m); });
        recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      } catch (e) { release(); recorder = null; warn(mic); return; }
      const chunks = [], rec = recorder;
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = async function () {
        release(); recorder = null;
        const type = (rec.mimeType || mime || "audio/webm").split(";")[0];
        if (!chunks.length) { paint(mic, null); return; }
        paint(mic, "boot");
        try {
          const text = (await transcribe(new Blob(chunks, { type: type }), type)).trim();
          paint(mic, null);
          if (text) onText(text); else warn(mic);
        } catch (e) { warn(mic); }
      };
      rec.start();
      paint(mic, "on");
      stopTimer = setTimeout(function () { if (recorder && recorder.state === "recording") recorder.stop(); }, MAX_REC_MS);
    }
    function stop() { if (recorder && recorder.state === "recording") recorder.stop(); }
    if (mic) mic.addEventListener("click", function () { if (mic.dataset.s === "boot") return; recorder ? stop() : start(); });

    // ── speaking ──
    function stopSpeaking() {
      try { audio.pause(); } catch (e) {}
      if (root.speechSynthesis) speechSynthesis.cancel();
      playing = null; paint(speaker, wanted ? "idle" : null);
    }
    function builtin(text) {
      if (!root.speechSynthesis) return;
      const u = new SpeechSynthesisUtterance(text);
      if (lang()) u.lang = lang();
      u.onend = u.onerror = function () { if (playing === u) { playing = null; paint(speaker, "idle"); } };
      playing = u; paint(speaker, "on"); speechSynthesis.speak(u);
    }
    async function say(text) {
      if (!wanted) return;
      const t = speakable(text);
      if (!t) return;
      stopSpeaking();
      let g = null;
      try { g = await gate(); } catch (e) { g = null; }
      if (g && g.speak) {
        const token = playing = {};
        paint(speaker, "on");
        try {
          const r = await fetch(base(g) + "/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) });
          if (!r.ok) throw new Error("no voice");
          const url = URL.createObjectURL(await r.blob());
          if (playing !== token) { URL.revokeObjectURL(url); return; }
          audio.src = url;
          audio.onended = audio.onerror = function () { URL.revokeObjectURL(url); if (playing === token) { playing = null; paint(speaker, "idle"); } };
          await audio.play();
          return;
        } catch (e) { if (playing !== token) return; playing = null; }
      }
      builtin(t);                                                 // everyone else, and when Skippy's own voice can't be reached
    }
    function setWanted(on) {
      wanted = on;
      if (speaker) { speaker.setAttribute("aria-pressed", on ? "true" : "false"); speaker.classList.toggle("lit", false); }
      if (!on) { stopSpeaking(); paint(speaker, null); } else paint(speaker, "idle");
      try { localStorage.setItem(SPEAK_KEY, on ? "yes" : "no"); } catch (e) {}
    }
    if (speaker) {
      let was = false; try { was = localStorage.getItem(SPEAK_KEY) === "yes"; } catch (e) {}
      setWanted(was);
      speaker.addEventListener("click", function () {
        if (!wanted) { try { audio.src = SILENCE; audio.play().catch(function () {}); } catch (e) {} }     // unlock sound inside this tap
        setWanted(!wanted);
      });
    }
    return { say: say, stop: stopSpeaking, enabled: function () { return !!wanted; } };
  }

  return { attach: attach, pickMime: pickMime, speakable: speakable, MAX_SPEECH: MAX_SPEECH };
});
