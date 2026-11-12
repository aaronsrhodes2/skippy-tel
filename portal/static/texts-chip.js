/* The code chip (portal/texts.py), on /hud and /drive: when a text with a verification code arrives, a big tappable chip with the code appears
   beside the now-playing line for five minutes. A tap copies the code and puts the chip away. In hub mode (no screen to read) the code is
   said once: "Your code is 4 8 2 9 1 3". It starts itself on #drive[data-texts] and asks the portal (that attribute is the address) every
   minute while the page is visible. Texts arrive through Google Voice's forwarding to his Gmail; the portal parses them.

   SkippyTexts.codeSpoken("482913")             "4 8 2 9 1 3"
   SkippyTexts.plan(fresh, nowSec, memory)      {show, speak, key, code, left_s}   memory = {dismissed: Set, spoken: Set}
   SkippyTexts.attach(opts)                     the running chip; the page's parts (document, fetch, clipboard, voice, clock) are replaceable for tests */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.SkippyTexts = factory(root);
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  const FRESH_S = 300, EVERY_MS = 60000, COPIED_MS = 700;
  const clean = c => String(c || "").replace(/[^0-9A-Za-z]/g, "");
  const STYLE = "#code-chip{width:auto;height:44px;padding:0 16px;gap:10px;margin:0 6px;--accent:var(--green)}" +
                "#code-chip[hidden]{display:none}" +
                "#code-chip .digits{position:relative;z-index:1;font:700 26px var(--mono);letter-spacing:.12em;color:var(--green)}" +
                "#code-chip[data-s=on] .digits{color:#fff}" +
                "#code-chip .gl{position:relative;z-index:1;display:flex;color:var(--green)}";

  /** "482913" is read out one digit at a time. */
  function codeSpoken(code) { return clean(code).split("").join(" "); }

  /** What to do about the portal's `fresh` ({code, sender, at, age_s}): show the chip, say it, or nothing. The portal's own age is
   *  preferred to this device's clock (a car's clock can be minutes out). */
  function plan(fresh, nowSec, memory) {
    const none = { show: false, speak: false, key: "", code: "", left_s: 0 };
    const code = clean(fresh && fresh.code);
    if (!code) return none;
    const age = typeof fresh.age_s === "number" ? fresh.age_s : nowSec - Number(fresh.at || 0);
    if (!(age < FRESH_S)) return none;
    const key = (fresh.at || 0) + ":" + code, gone = memory.dismissed.has(key);
    return { show: !gone, speak: !gone && !memory.spoken.has(key), key: key, code: code, left_s: Math.max(1, Math.round(FRESH_S - Math.max(0, age))) };
  }

  function attach(o) {
    o = o || {};
    const doc = o.doc || root.document;
    const anchor = doc && doc.getElementById("now-t"), drive = doc && doc.getElementById("drive");
    const url = o.url || (drive && drive.dataset && drive.dataset.texts);
    if (!anchor || !url) return null;
    const getState = o.fetch || (u => root.fetch(u, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } }).then(r => r.ok ? r.json() : null));
    const clip = o.clipboard !== undefined ? o.clipboard : (root.navigator && root.navigator.clipboard);
    const speak = o.speak || (text => { const d = root.DriveApp; if (d && d.voice && typeof d.voice.say === "function") { d.voice.say(text); return true; } return false; });
    const inHub = o.hub || (() => !!(drive && drive.classList && drive.classList.contains("hub")));
    const nowSec = o.now || (() => Date.now() / 1000);
    const later = o.setTimeout || ((fn, ms) => root.setTimeout(fn, ms));
    const clear = o.clearTimeout || (id => root.clearTimeout(id));
    const every = o.setInterval || ((fn, ms) => root.setInterval(fn, ms));
    const memory = { dismissed: new Set(), spoken: new Set() };

    if (!doc.getElementById("code-chip-style") && doc.head) {
      const style = doc.createElement("style"); style.id = "code-chip-style"; style.textContent = STYLE; doc.head.appendChild(style);
    }
    const chip = doc.createElement("button"), mark = doc.createElement("span"), digits = doc.createElement("span");
    chip.type = "button"; chip.id = "code-chip"; chip.className = "key"; chip.hidden = true;
    chip.setAttribute("data-tel", "drive:code");
    chip.setAttribute("aria-label", "Copy the code from the text that just came in"); chip.title = "Copy the code from the text that just came in";
    mark.className = "gl"; mark.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-chats"/></svg>';
    digits.className = "digits";
    chip.append(mark, digits);
    anchor.insertAdjacentElement("afterend", chip);

    let current = null, timer = 0;
    function hide() { chip.hidden = true; clear(timer); current = null; }
    function show(p) {
      current = p; digits.textContent = p.code; chip.hidden = false;
      clear(timer); timer = later(hide, p.left_s * 1000);
    }

    async function check() {
      if (doc.hidden) return;
      let state = null;
      try { state = await getState(url); } catch (e) { return; }                // a dead zone: the chip stays as it is
      const p = plan(state && state.fresh, nowSec(), memory);
      if (!p.show) { if (current) hide(); return; }
      if (!current || current.key !== p.key) show(p);
      if (p.speak && inHub() && speak("Your code is " + codeSpoken(p.code))) memory.spoken.add(p.key);
    }

    async function copy(text) {
      try { if (clip && clip.writeText) { await clip.writeText(text); return true; } } catch (e) { /* the old way below */ }
      try {
        const box = doc.createElement("textarea"); box.value = text; box.setAttribute("readonly", "");
        box.style.cssText = "position:fixed;top:0;left:0;opacity:0"; doc.body.appendChild(box); box.select();
        const ok = doc.execCommand("copy"); doc.body.removeChild(box); return !!ok;
      } catch (e) { return false; }
    }
    chip.addEventListener("click", async () => {
      const p = current; if (!p) return;
      memory.dismissed.add(p.key);                                               // put away for good, copied or not
      const ok = await copy(p.code);
      chip.dataset.s = ok ? "on" : "warn"; digits.textContent = ok ? "Copied" : "Hold to copy";
      later(() => { delete chip.dataset.s; hide(); }, ok ? COPIED_MS : 1500);
    });

    const poll = every(check, EVERY_MS);
    doc.addEventListener("visibilitychange", check);
    check();
    return { check: check, chip: chip, memory: memory, hide: hide, poll: poll };
  }

  // the page: start by itself where the page asked for it (not under Node)
  if (!(typeof module === "object" && module.exports) && root && root.document) {
    const start = () => { if (root.document.querySelector("#drive[data-texts]")) attach({}); };
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", start); else start();
  }

  return { codeSpoken: codeSpoken, plan: plan, attach: attach, FRESH_S: FRESH_S, EVERY_MS: EVERY_MS };
});
