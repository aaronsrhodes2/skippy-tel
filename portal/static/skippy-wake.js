/* The trained "Skippy" wake word in the browser: the same openWakeWord model the pods use (services/satellite/wakeword.py), run on this
 * device with onnxruntime-web, so nothing said in the car leaves it until his name is heard.
 *
 *   const ear = await SkippyWake.start({ ort, models: {mel, emb, word} (ArrayBuffers), threshold: 0.2, onWake() {...} });
 *   ear.pause(true)   // while Skippy speaks or a turn is being heard: his own voice saying his name must not wake him
 *   ear.stop()
 *
 * openWakeWord's pipeline, step for step (openwakeword/utils.py AudioFeatures): 16 kHz audio in 80 ms frames (1280 samples, int16 values as
 * floats) -> melspectrogram.onnx over the frame plus 480 samples before it -> 8 mel frames of 32 (x/10 + 2) -> every frame, embedding_model.onnx
 * over the last 76 mel frames -> one 96-wide embedding -> the word model over the last 16 embeddings -> a score 0..1. A word counts when
 * `patience` frames in a row reach the threshold; then it is deaf for `refractoryMs` (wakeword.py's Spotter, the same numbers).
 * The pure part (createPipeline with any `run` function, Resampler, Spotter) is exported for portal/tests/js/wake.test.js.
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyWake = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";
  const RATE = 16000, FRAME = 1280, CONTEXT = 480, MEL = 32, MEL_WIN = 76, EMB = 96, EMB_WIN = 16;

  /* Any input rate down to 16 kHz (linear interpolation is enough for a wake word; the browser's mic is usually 44.1 or 48 kHz). */
  function Resampler(from) {
    const step = from / RATE;
    let t = 0, prev = 0;                                           // t: where the next output sample falls, in input samples (-1 = the last one of the previous block)
    this.push = function (x) {                                     // Float32Array -1..1 -> array of int16-valued numbers at 16 kHz
      const out = [], at = function (k) { return k < 0 ? prev : x[k]; };
      while (t <= x.length - 1) {
        const i = Math.floor(t), f = t - i, v = f ? at(i) * (1 - f) + at(i + 1) * f : at(i);
        out.push(Math.max(-32768, Math.min(32767, Math.round(v * 32767))));
        t += step;
      }
      if (x.length) { t -= x.length; prev = x[x.length - 1]; }
      return out;
    };
  }

  /* The feature pipeline over three model runners: run(name, Float32Array data, dims) -> Promise<Float32Array>. */
  function createPipeline(run) {
    let raw = [], pending = [], mel = [], emb = [];
    for (let i = 0; i < MEL_WIN; i++) mel.push(new Float32Array(MEL).fill(1));      // openWakeWord starts its mel buffer as ones
    async function frame(samples) {                                // exactly FRAME samples -> a score, or null while warming up
      raw = raw.concat(samples);
      if (raw.length > FRAME + CONTEXT) raw = raw.slice(raw.length - FRAME - CONTEXT);
      const m = await run("mel", Float32Array.from(raw), [1, raw.length]);
      const frames = Math.floor(m.length / MEL);
      for (let i = 0; i < frames; i++) mel.push(Float32Array.from(m.subarray(i * MEL, (i + 1) * MEL), function (v) { return v / 10 + 2; }));
      if (mel.length > MEL_WIN + 24) mel = mel.slice(mel.length - MEL_WIN - 24);
      const win = new Float32Array(MEL_WIN * MEL);
      const start = mel.length - MEL_WIN;
      for (let i = 0; i < MEL_WIN; i++) win.set(mel[start + i], i * MEL);
      const e = await run("emb", win, [1, MEL_WIN, MEL, 1]);
      emb.push(Float32Array.from(e.subarray(0, EMB)));
      if (emb.length > EMB_WIN) emb = emb.slice(emb.length - EMB_WIN);
      if (emb.length < EMB_WIN) return null;
      const feat = new Float32Array(EMB_WIN * EMB);
      for (let i = 0; i < EMB_WIN; i++) feat.set(emb[i], i * EMB);
      const s = await run("word", feat, [1, EMB_WIN, EMB]);
      return s[0];
    }
    return {
      feed: async function (samples) {                             // int16-valued samples at 16 kHz, any count -> the scores of the frames completed
        for (let i = 0; i < samples.length; i++) pending.push(samples[i]);
        const scores = [];
        while (pending.length >= FRAME) {
          const f = pending.slice(0, FRAME); pending = pending.slice(FRAME);
          const s = await frame(f);
          if (s != null) scores.push(s);
        }
        return scores;
      },
      reset: function () { emb = []; pending = []; },
    };
  }

  /* wakeword.py's Spotter: `patience` frames in a row at the threshold, then deaf for refractoryMs. Times are in samples heard. */
  function Spotter(o) {
    o = o || {};
    const threshold = o.threshold == null ? 0.5 : o.threshold, patience = Math.max(1, o.patience || 2), refractory = Math.round((o.refractoryMs || 1500) * RATE / 1000);
    let run = 0, t = 0, deafUntil = 0;
    this.last = 0;
    this.score = function (s) {                                    // one frame's score -> true once when the word was just said
      t += FRAME; this.last = s;
      if (t < deafUntil) return false;
      run = s >= threshold ? run + 1 : 0;
      if (run < patience) return false;
      run = 0; deafUntil = t + refractory;
      return true;
    };
    this.reset = function () { run = 0; };
  }

  /* In a page: the microphone through Web Audio, the three models through onnxruntime-web. */
  async function start(o) {
    const ort = o.ort || root.ort;
    if (!ort) throw new Error("onnxruntime-web is not loaded");
    const opts = { executionProviders: ["wasm"], graphOptimizationLevel: "all" };
    const sess = {
      mel: await ort.InferenceSession.create(new Uint8Array(o.models.mel), opts),
      emb: await ort.InferenceSession.create(new Uint8Array(o.models.emb), opts),
      word: await ort.InferenceSession.create(new Uint8Array(o.models.word), opts),
    };
    async function run(name, data, dims) {
      const s = sess[name], feeds = {};
      feeds[s.inputNames[0]] = new ort.Tensor("float32", data, dims);
      const out = await s.run(feeds);
      return out[s.outputNames[0]].data;
    }
    const pipe = createPipeline(run), spot = new Spotter(o);
    const stream = o.stream || await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const Ctx = root.AudioContext || root.webkitAudioContext;
    const ctx = new Ctx();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);            // (an AudioWorklet would need its own file; 4096 samples is ~90 ms: plenty)
    const sink = ctx.createGain(); sink.gain.value = 0;            // the processor only runs when connected to the output: through silence
    const rs = new Resampler(ctx.sampleRate);
    let paused = false, busy = Promise.resolve(), stopped = false, backlog = 0;
    proc.onaudioprocess = function (ev) {
      if (paused || stopped) return;
      const x = rs.push(ev.inputBuffer.getChannelData(0));
      if (backlog > 6) return;                                     // a slow device drops audio rather than falling minutes behind
      backlog++;
      busy = busy.then(function () { return pipe.feed(x); }).then(function (scores) {
        backlog--;
        for (const s of scores) {
          if (o.onScore) o.onScore(s);
          if (spot.score(s) && !paused && !stopped) { pipe.reset(); spot.reset(); o.onWake && o.onWake(); }
        }
      }, function () { backlog--; });
    };
    src.connect(proc); proc.connect(sink); sink.connect(ctx.destination);
    if (ctx.state === "suspended") ctx.resume().catch(function () {});
    return {
      ctx: ctx,
      asleep: function () { return ctx.state === "suspended"; },
      resume: function () { if (ctx.state === "suspended") ctx.resume().catch(function () {}); },
      pause: function (on) { paused = !!on; if (on) { pipe.reset(); spot.reset(); } },
      last: function () { return spot.last; },
      stop: function () { stopped = true; try { proc.disconnect(); src.disconnect(); } catch (e) {} if (!o.stream) stream.getTracks().forEach(function (t) { t.stop(); }); ctx.close().catch(function () {}); },
    };
  }

  return { start: start, createPipeline: createPipeline, Resampler: Resampler, Spotter: Spotter, RATE: RATE, FRAME: FRAME };
});
