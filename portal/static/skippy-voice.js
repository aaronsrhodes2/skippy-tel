/* Skippy's ears and mouth for the portal's chats (/chat and the personal Skippy on /me).
 *
 *   const voice = SkippyVoice.attach({gate: "/chat/voice/gate", mic: <button>, speaker: <button>, box: <textarea>, status: <div>, onSend(), lang(): "en"});
 *   voice.say(replyText);          // reads it aloud if the speaker key is on
 *
 * A NO-TOUCH CONVERSATION: one tap starts it (and turns his voice on). You speak, a pause sends it, Skippy answers aloud, and when he has
 * finished the mic opens again by itself for your next turn, round after round. A tap while he talks is "my turn", a tap while it
 * listens or thinks ends the conversation, and so does saying "goodbye" (or going quiet for 30 s). The page calls voice.say(reply)
 * when a reply lands, and voice.resume() when none will come (an error).
 * STANDBY (the ear key): the microphone stays open and the page hears everything said near it, but only speech is sent to the home Whisper, and
 * it is thrown away unless it begins with "Skippy" ("Hey Skippy, ..." wakes it with the message in the same breath). Like any web page it listens
 * only while the page is open in the foreground (iOS and Android stop microphone capture in the background): a tablet on a stand with the screen on.
 * Hearing (hands-free): each turn is a session. A level bar shows that you are heard, your words appear in the
 * message box (opts.box) while you speak (the recording so far goes to skippy-pc's Whisper every couple of seconds through the voice
 * gate), a pause ends the utterance by itself, and then a 4 s countdown runs to send it (opts.onSend). Speaking again during the
 * count continues the message; saying "stop", tapping the key or the cross, or typing cancels. Nothing is recorded until the tap, and
 * the microphone closes when the session ends. Works with MediaRecorder + Web Audio (Chrome, Firefox, Safari, phones included).
 * Speaking: the speaker key turns on reading replies aloud. The Captain's pass lets the gate speak in Skippy's voice; every
 * other pass (and any failure) falls back to the device's own built-in voice, so it works on every device either way.
 * Lamps: mic data-s = "on" recording, "boot" understanding, "warn" it didn't work; speaker data-s = "on" speaking.
 * The pure helpers (pickMime, speakable, createVad, STOP_RE) are exported for tests/js/voice/skippy-voice.test.js.
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

  /* Hands-free: when has the speaker started, and when are they done? Feed it one loudness reading (0..1, the RMS of the microphone)
     with a clock in milliseconds, about every 60 ms; it answers "speech" once (they began), "end" once (a pause after speaking), or
     null. The bar for "speaking" follows the room: it is a multiple of the quiet level it has seen, never below a small floor. */
  function createVad(o) {
    o = o || {};
    const minSpeechMs = o.minSpeechMs || 160, endSilenceMs = o.endSilenceMs || 1400, floorMin = o.floorMin || 0.012, ratio = o.ratio || 3.2;
    const calMs = o.calMs || 400, quietCap = o.quietCap || 0.06;
    let quiet = floorMin, above = 0, speaking = false, quietSince = null, lastT = null, begun = false;
    let t0 = null, winSum = 0, winN = 0, winT = null, calib = Infinity;      // the first 400 ms learn the room's own noise before anything counts as speech
    return {
      get speaking() { return speaking; },
      threshold: function () { return Math.max(floorMin * 2.2, quiet * ratio); },
      push: function (level, t) {
        const dt = lastT == null ? 0 : t - lastT; lastT = t;
        if (t0 == null) { t0 = t; winT = t; }
        if (t - t0 < calMs) {                                                  // calibrating: the quietest ~100 ms window is the room
          winSum += level; winN++;
          if (t - winT >= 100) { calib = Math.min(calib, winSum / winN); winSum = 0; winN = 0; winT = t; }
          return null;
        }
        if (calib !== Infinity) { quiet = Math.max(floorMin, Math.min(quietCap, calib)); calib = Infinity; }
        const bar = Math.max(floorMin * 2.2, quiet * ratio);
        if (!speaking) {
          if (level > bar) { above += dt; if (above >= minSpeechMs) { speaking = true; begun = true; quietSince = null; return "speech"; } }
          else { above = 0; quiet = Math.max(floorMin, quiet * 0.95 + level * 0.05); }                  // learn how quiet this room is
          return null;
        }
        if (level > bar * 0.7) { quietSince = null; return null; }                                       // still talking (a little hysteresis)
        if (quietSince == null) quietSince = t;
        if (t - quietSince >= endSilenceMs) { speaking = false; return begun ? "end" : null; }
        return null;
      },
    };
  }

  /* Said alone during the send countdown, these mean "don't send it". */
  const STOP_RE = /^\s*(?:ok(?:ay)?[ ,]*)?(?:stop|cancel|wait|hold on|hold it|don'?t send(?: it)?|never ?mind|no|abort)\s*[.!?]*\s*$/i;
  const SEND_DELAY_MS = 4000;
  /* Standby: the page hears every utterance (the microphone stays open, but only SPEECH is ever sent to the home Whisper, and it is thrown away
     unless it begins with the wake word). "Hey Skippy, turn that down" wakes it and "turn that down" is the message; "Skippy." alone wakes it and
     the next thing said is the message. */
  const WAKE_RE = /\b(?:(?:hey|hi|ok|okay|yo)[ ,]+)?skipp(?:y|ie|ey)\b[ ,.!?:;-]*/i;
  function wakeSplit(text) {
    const t = String(text == null ? "" : text), m = WAKE_RE.exec(t.slice(0, 60));
    return m ? { woke: true, rest: t.slice(m.index + m[0].length).trim() } : { woke: false, rest: "" };
  }
  /* Said alone as a turn, these end the conversation (and send nothing). */
  const END_RE = /^\s*(?:ok(?:ay)?[ ,]*)?(?:good ?bye|bye(?: bye)?|that'?s all|that is all|stop listening|end (?:the )?conversation|we'?re done|i'?m done|talk to you later)\s*[.!?]*\s*$/i;

  /* What can be said aloud at all: no code blocks, code chips, links or markdown marks. */
  function plain(text) {
    return String(text || "").replace(/```[\s\S]*?```/g, " ").replace(/`([^`\n]*)`/g, "$1").replace(/https?:\/\/\S+/g, "")
      .replace(/[*_#>~]+/g, "").replace(/\s+/g, " ").trim();
  }
  /* A short reply is spoken as it is; a long one is spoken as a summary (the voice gate writes it), and if that cannot be had, as its
     first MAX_SPEECH characters, cut at a sentence. */
  function speakable(text) {
    const t = plain(text);
    if (t.length <= MAX_SPEECH) return t;
    const cut = t.slice(0, MAX_SPEECH);
    let at = -1;
    for (const sep of [". ", "! ", "? "]) at = Math.max(at, cut.lastIndexOf(sep));
    return (at > MAX_SPEECH / 2 ? cut.slice(0, at + 1) : cut).trim();
  }
  const SUMMARIZE_OVER = 280;

  function attach(opts) {
    const mic = opts.mic, ear = opts.ear, speaker = opts.speaker, box = opts.box, status = opts.status, onSend = opts.onSend;
    const onText = opts.onText || function () {};
    const lang = opts.lang || function () { return ""; };
    const canRecord = !!(root.navigator && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && root.MediaRecorder);
    if (mic && !canRecord) mic.hidden = true;
    if (ear && !canRecord) ear.hidden = true;
    let pass = null, wanted = null, session = null, playing = null, chat = false, awaiting = false, keepUntil = 0, standbyOn = false, shared = null;     // chat: a no-touch conversation is on; keepUntil: a parting message stays up till then
    const audio = new Audio();
    const speaking_ = function (on) { try { root.dispatchEvent(new CustomEvent("skippy-speaking", { detail: on })); } catch (e) { /* no DOM events: nothing animates */ } };      // the animated Skippy (static/skippy-3d.js) talks while this plays
    audio.addEventListener("playing", function () { if (audio.src !== SILENCE) speaking_(true); });
    ["ended", "pause", "error", "emptied"].forEach(function (ev) { audio.addEventListener(ev, function () { speaking_(false); }); });

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

    // A long reply is not read out word for word (it comes in parts, and symbols and paths are no way to talk): the gate writes what a
    // person would say about it.
    async function summarize(full) {
      let g = await gate();
      const send = function () { return fetch(base(g) + "/summary", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: full.slice(0, 8000) }) }); };
      let r = await send();
      if (r.status === 403) { pass = null; g = await gate(); r = await send(); }
      if (!r.ok) throw new Error("no summary");
      const t = word((await r.json()).text);
      if (!t) throw new Error("empty summary");
      return t;
    }

    // ── hearing: one tap starts a hands-free session ──
    // It shows that you are heard (a level bar, and your words as you speak), ends on a pause by itself, then counts down to send. Speaking
    // again during the count continues the message, saying "stop" (or a tap, or typing) cancels it.
    const word = function (t) { return String(t || "").replace(/\s+/g, " ").trim(); };
    function paintStatus(text, level, cancellable) {
      if (!status) return;
      status.hidden = text == null;
      if (text == null) { status.replaceChildren(); return; }
      if (!status._built) {
        const bar = document.createElement("span"), fill = document.createElement("i"), label = document.createElement("span"), x = document.createElement("button");
        bar.className = "lvbar"; bar.appendChild(fill); label.className = "vtxt"; x.type = "button"; x.className = "vx"; x.textContent = "\u00D7";
        x.title = "Cancel"; x.setAttribute("aria-label", "Cancel the voice message"); x.addEventListener("click", function () { cancel(); endChat(); });
        status.append(bar, label, x); status._built = { fill: fill, label: label, x: x };
      }
      const b = status._built;
      b.label.textContent = text; b.x.hidden = !cancellable;
      b.fill.style.width = Math.round(Math.min(1, Math.max(0, level || 0) * 5) * 100) + "%";         // x5: a normal voice is about 0.05 to 0.2 RMS
    }

    async function openMic() {
      if (shared && shared.alive) return shared;                  // still open from the last turn: no new permission prompt
      // Echo cancellation and noise suppression help when music or a film is playing nearby (headphones fix it completely).
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      const Ctx = root.AudioContext || root.webkitAudioContext;
      let ctx = null, an = null, buf = null, level = 0, timer = null;
      if (Ctx) {                                                  // the level meter: without Web Audio the session still works, just blind
        ctx = new Ctx(); const src = ctx.createMediaStreamSource(stream); an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
        buf = new Float32Array(an.fftSize);
        timer = setInterval(function () { an.getFloatTimeDomainData(buf); let sum = 0; for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]; level = Math.sqrt(sum / buf.length); }, 50);
      }
      const obj = { stream: stream, alive: true, level: function () { return level; }, blind: !an,
                    close: function () { obj.alive = false; if (shared === obj) shared = null; clearInterval(timer); stream.getTracks().forEach(function (t) { t.stop(); }); if (ctx && ctx.close) ctx.close().catch(function () {}); } };
      shared = obj;
      return obj;
    }
    function releaseMic() { if (shared) shared.close(); }

    // One utterance: resolves {spoke, text}. `idleMs`: give up (spoke:false) if nobody speaks within that long; `tick(msLeft)` counts that down.
    function segment(mic_, seg) {
      return new Promise(function (resolve) {
        const mime = pickMime(function (m) { return MediaRecorder.isTypeSupported(m); });
        const rec = new MediaRecorder(mic_.stream, mime ? { mimeType: mime } : undefined), chunks = [], vad = createVad(), t0 = Date.now();
        let spoke = false, ended = false, busy = false, lastInterim = "", timer = null, interimAt = 0, speechAt = 0;
        const type = (mime || "audio/webm").split(";")[0];
        const blob = function () { return new Blob(chunks, { type: type }); };
        rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        const finish = function (res) { if (ended) return; ended = true; clearInterval(timer); resolve(res); };
        rec.onstop = async function () {
          if (!spoke) return finish({ spoke: false, text: "" });
          if (seg.minSpeechMs && Date.now() - speechAt < seg.minSpeechMs) return finish({ spoke: false, text: "", short: true });      // a cough or a click: not worth sending
          try { finish({ spoke: true, text: word(await transcribe(blob(), type)) }); } catch (e) { finish({ spoke: true, text: lastInterim, failed: true }); }
        };
        seg.abort = function () { ended = true; clearInterval(timer); try { if (rec.state !== "inactive") { rec.onstop = null; rec.stop(); } } catch (e) {} resolve({ spoke: false, text: "", aborted: true }); };
        rec.start(250);
        timer = setInterval(async function () {
          if (ended) return;
          const now = Date.now(), lv = mic_.level(), ev = mic_.blind ? null : vad.push(lv, now);
          if (!spoke) {
            if (ev === "speech") { spoke = true; speechAt = now; interimAt = now - 1000; if (seg.onSpeech) seg.onSpeech(); }
            else if (seg.idleMs && now - t0 >= seg.idleMs) { try { rec.stop(); } catch (e) {} return; }
            else if (seg.tick && seg.idleMs) seg.tick(seg.idleMs - (now - t0), lv);
          }
          if (seg.level) seg.level(lv, spoke);
          if (spoke && ev === "end") { try { rec.stop(); } catch (e) {} return; }
          if (now - t0 >= MAX_REC_MS) { try { rec.stop(); } catch (e) {} return; }
          if (spoke && seg.interim && !busy && (mic_.blind || vad.speaking) && now - speechAt >= 1200 && now - interimAt > 2200 && chunks.length > 4) {      // the words appear while you are still speaking
            busy = true; interimAt = now;
            try { const t = word(await transcribe(blob(), type)); if (!ended && t) { lastInterim = t; if (seg.interim) seg.interim(t); } } catch (e) {}
            busy = false;
          }
        }, 60);
      });
    }

    async function handsFree(o) {
      stopSpeaking();
      let mic_;
      try { mic_ = await openMic(); } catch (e) { warn(mic); return; }
      const ctl = session = { mic: mic_, seg: null, cancelled: false };
      const draft = box ? word(box.value) : "";
      let message = (o && o.seed) ? o.seed : "";
      const show = function (t) { if (box) box.value = (draft ? draft + " " : "") + t; onText(t, false); };
      paint(mic, "on");
      try {
        let first = !message;
        if (message) { show(message); onText(message, true); }    // woken with the message in the same breath: straight to the countdown
        for (;;) {                                               // an utterance, then a count where speaking again continues it
          const seg = ctl.seg = {};
          seg.idleMs = first ? ((o && o.idleMs) || 10000) : SEND_DELAY_MS;
          seg.level = function (lv, speaking) { if (speaking) paintStatus("Hearing you", lv, true); else if (first) paintStatus("Listening", lv, true); };   // (the count paints itself, in tick)
          seg.tick = function (left, lv) {
            if (first) { paintStatus("Listening", lv, true); return; }
            paintStatus("Sending in " + Math.ceil(left / 1000) + ": tap, type or say stop to cancel", lv, true);
          };
          seg.onSpeech = function () { paint(mic, "on"); paintStatus("Hearing you", 0, true); };
          seg.interim = function (t) { show((message ? message + " " : "") + t); };
          const r = await segment(mic_, seg);
          if (ctl.cancelled || r.aborted) return;
          if (!r.spoke) {                                        // nobody spoke for the whole wait
            if (first) {                                         // nobody spoke at all
              if (chat) { endChat("I'm not hearing you: tap the mic to talk again"); return; }
              paintStatus("I didn't hear anything", 0, false); setTimeout(function () { if (!session) paintStatus(null); }, 2500); warn(mic); return;
            }
            break;                                               // the count ran out: send
          }
          paintStatus("Understanding\u2026", 0, true); paint(mic, "boot");
          if (chat && END_RE.test(r.text)) { endChat("Conversation ended"); return; }               // "goodbye": no touch needed to leave either
          if (!first && STOP_RE.test(r.text)) {
            if (chat) {                                          // in a conversation "stop" throws the message away and listens again: just say it again
              if (box) box.value = draft; onText("", false);
              paintStatus("Cancelled: go ahead", 0, false); awaiting = true; setTimeout(function () { awaiting = false; listenAgain(); }, 700); return;
            }
            show(message); paintStatus("Cancelled", 0, false); setTimeout(function () { if (!session) paintStatus(null); }, 1500); return;
          }
          if (r.text) { message = (message ? message + " " : "") + r.text; show(message); onText(message, true); }
          else if (first) { warn(mic); return; }
          first = false; paint(mic, "on");
        }
        if (!message) return;
        paintStatus("Sending\u2026", 0, chat);
        if (chat) awaiting = true;                               // the reply is coming: when it has been spoken, the mic opens again
        if (onSend) onSend(message);
        if (chat) { paint(mic, "boot"); paintStatus("Thinking\u2026", 0, true); }
      } finally {
        if (session === ctl) session = null;
        if (!chat && !standbyOn) mic_.close();                  // a conversation or standby keeps the one open microphone
        if (!chat) paint(mic, null);
        if (!chat) setTimeout(function () { if (!session && Date.now() >= keepUntil) paintStatus(null); }, 600);
      }
    }
    function cancel() {                                           // a tap, a keystroke, the cross: stop listening, keep whatever is in the box
      const c = session;
      if (c) { c.cancelled = true; session = null; if (c.seg && c.seg.abort) c.seg.abort(); if (!standbyOn) c.mic.close(); }
      paint(mic, null); paintStatus(null); chat = false; awaiting = false;
      if (standbyOn) setTimeout(standbyLoop, 400);                // typing or a tap cancels the message, not the standby
    }
    function endChat(why) {                                       // leave the conversation (the mic is closed already, or about to be)
      chat = false; awaiting = false; paint(mic, null);
      if (standbyOn) setTimeout(standbyLoop, 600); else releaseMic();       // back to standby, or the microphone is let go
      if (why) { keepUntil = Date.now() + 3500; paintStatus(why, 0, false); setTimeout(function () { if (!session && !chat) paintStatus(null); }, 3500); } else paintStatus(null);
      if (mic) mic.setAttribute("aria-pressed", "false");
    }
    function listenAgain() { if (chat && !session) handsFree({ idleMs: 30000 }); }
    function replied() {                                          // Skippy has answered and finished speaking: your turn, no touch needed
      if (chat && awaiting) { awaiting = false; setTimeout(listenAgain, 450); }      // (a short beat so his last word is not heard as yours)
    }
    async function standbyLoop() {                               // listen for "Skippy": every utterance is heard, none is kept unless it starts with the word
      if (!standbyOn || session || chat) return;
      let mic_;
      try { mic_ = await openMic(); } catch (e) { standbyOff('The microphone is blocked: allow it for this site, then tap the ear again'); return; }
      const ctl = session = { mic: mic_, seg: null, cancelled: false, standby: true };
      try {
        while (standbyOn && !ctl.cancelled) {
          const seg = ctl.seg = { idleMs: 0, minSpeechMs: 500 };
          seg.level = function (lv, speaking) { paintStatus(speaking ? "Hearing you" : 'Standby: say "Skippy"', lv, true); };
          const r = await segment(mic_, seg);
          if (ctl.cancelled || r.aborted || !standbyOn) return;
          if (!r.spoke || !r.text) continue;
          const w = wakeSplit(r.text);
          if (!w.woke) continue;                                  // not for him: gone
          session = null;
          chat = true; awaiting = false;
          paintStatus("Yes?", 0, true);
          handsFree({ idleMs: 10000, seed: w.rest });
          return;
        }
      } finally { if (session === ctl) session = null; }
    }
    function standbyOff(why) {
      standbyOn = false;
      if (ear) { ear.setAttribute("aria-pressed", "false"); paint(ear, null); }
      const c = session;
      if (c && c.standby) { c.cancelled = true; session = null; if (c.seg && c.seg.abort) c.seg.abort(); }
      if (!chat && !session) releaseMic();
      if (why) { keepUntil = Date.now() + 5000; paintStatus(why, 0, false); setTimeout(function () { if (!session && !chat && !standbyOn) paintStatus(null); }, 5000); }
      else if (!chat) paintStatus(null);
    }
    function standbyOnNow() {
      try { audio.src = SILENCE; audio.play().catch(function () {}); } catch (e) {}      // unlock sound inside this tap: replies are spoken later with no touch
      if (speaker && !wanted) setWanted(true);
      standbyOn = true;
      if (ear) { ear.setAttribute("aria-pressed", "true"); paint(ear, "on"); }
      standbyLoop();
    }
    if (ear) ear.addEventListener("click", function () { standbyOn ? standbyOff() : standbyOnNow(); });
    function startChat() {
      chat = true; awaiting = false;
      try { audio.src = SILENCE; audio.play().catch(function () {}); } catch (e) {}      // unlock sound inside this tap: later replies play with no touch
      if (speaker && !wanted) setWanted(true);                    // a conversation needs his voice
      if (mic) mic.setAttribute("aria-pressed", "true");
      handsFree({ idleMs: 10000 });
    }
    if (mic) mic.addEventListener("click", function () {
      if (mic.dataset.s === "boot" && !chat) return;
      if (session && session.standby) {                           // a tap during standby: "I'm talking now", no need to say the word
        const c = session; c.cancelled = true; session = null; if (c.seg && c.seg.abort) c.seg.abort();
        startChat(); return;
      }
      if (session) { cancel(); return; }                          // listening: a tap ends it all
      if (playing) { stopSpeaking(); if (chat) { awaiting = false; listenAgain(); } return; }     // he is talking: a tap is "my turn"
      if (chat) { endChat("Conversation ended"); return; }        // thinking: a tap ends the conversation
      startChat();
    });
    if (box) box.addEventListener("input", function () { if (session) cancel(); });          // typing means "I'll take it from here"

    // ── speaking ──
    function stopSpeaking() {
      try { audio.pause(); } catch (e) {}
      if (root.speechSynthesis) speechSynthesis.cancel();
      playing = null; paint(speaker, wanted ? "idle" : null);
    }
    function builtin(text, done) {
      if (!root.speechSynthesis) { if (done) done(); return; }
      const u = new SpeechSynthesisUtterance(text);
      if (lang()) u.lang = lang();
      u.onend = u.onerror = function () { if (playing === u) { playing = null; paint(speaker, "idle"); if (done) done(); } };
      playing = u; paint(speaker, "on"); speechSynthesis.speak(u);
    }
    async function say(text) {
      let finished = false;
      const done = function () { if (finished) return; finished = true; if (chat) paintStatus(null); replied(); };
      if (!wanted) { done(); return; }
      const full = plain(text);
      if (!full) { done(); return; }
      stopSpeaking();
      if (chat) paintStatus("Skippy is putting it into words\u2026", 0, true);
      let t = full.length <= SUMMARIZE_OVER ? full : "";
      if (!t) { try { t = await summarize(full); } catch (e) { t = speakable(text); } }       // no summary to be had: the opening, cut at a sentence
      if (!t) { done(); return; }
      if (chat) paintStatus("Skippy is speaking: tap to jump in", 0, true);
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
          audio.onended = audio.onerror = function () { URL.revokeObjectURL(url); if (playing === token) { playing = null; paint(speaker, "idle"); done(); } };
          await audio.play();
          return;
        } catch (e) { if (playing !== token) return; playing = null; }
      }
      builtin(t, done);                                           // everyone else, and when Skippy's own voice can't be reached
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
    return { say: say, stop: stopSpeaking, cancel: cancel, resume: replied, standby: function () { return standbyOn; }, chatting: function () { return chat; }, enabled: function () { return !!wanted; } };
  }

  return { attach: attach, pickMime: pickMime, speakable: speakable, plain: plain, createVad: createVad, wakeSplit: wakeSplit, WAKE_RE: WAKE_RE, STOP_RE: STOP_RE, END_RE: END_RE, SEND_DELAY_MS: SEND_DELAY_MS, MAX_SPEECH: MAX_SPEECH };
});
