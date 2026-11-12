/* The direct front door, the page's half (the Caddy edge on skippy-pc is the other end; portal/*.py build the address from EDGE_HOST).

   A gate pass the portal hands a page ({ok, url, pass, exp, ...}) names the gate twice: `url`, the Tailscale Funnel address it always
   had, and `direct`, the same gate on skippy-pc's own front door, https://<EDGE_HOST>/<prefix> (music, video, books, voice, in).
   `direct` is absent when the portal has the door off or cannot reach it from outside. The Funnel is a relay, and a relay stalls on a
   poor link (the Captain, 2026-11-05: "Funnels and bridges seem like common failure points for us. Can we give direct HTTP to all of my
   devices?"), so a page uses the direct door whenever THIS device can reach it, and the Funnel is the way back. Both reach the same
   gate and take the same pass, so a page needs nothing but a different `url`.

   SkippyDirect.pick(g [, {fresh}])   a promise of g itself (never a rejection). g.funnel is the address g.url had. When g.direct is
                                      set and GET <g.direct>/health answers 2xx with JSON within 2.5 s, g.url becomes g.direct;
                                      otherwise g.url stays the Funnel. Every `${g.url}/t/${g.pass}/...` the page builds then goes
                                      the right way unchanged. The answer is remembered for this tab (sessionStorage, and this page's
                                      memory when there is no storage): 120 s when the door answered, 300 s when it did not, and
                                      callers that ask while a probe is in the air wait for that one probe. `fresh: true` asks again
                                      whatever is remembered: a retry after trouble (a forced renewal of the pass) means "look again".
                                      A pass that comes with NO door (the portal had it down), in a browser that has seen the door answer
                                      before, gets g.exp pulled in to 35 minutes from now, so the players' own "renew 30 minutes before the
                                      end" asks the portal again in about five and the page finds the door when it is back, instead of
                                      staying on the Funnel for 8-14 hours. (A browser that never saw the door is left alone.)
   SkippyDirect.demote(g [, used])    a call that went to the direct door got NO answer (the connection failed, not an HTTP error):
                                      put g on the Funnel, remember the door as down (five minutes), and say true when the same call
                                      is worth making once more on g.url. `used` is the address that call went to (default g.url), so
                                      two calls that failed together both learn that the Funnel is the way on.
   SkippyDirect.create(env)           a separate instance with a memory of its own. env = {fetch, storage, now, probeMs}: the page's
                                      parts, replaceable for tests/js/direct.test.js, which runs this file under Node.
   SkippyDirect.doorOf(direct) / prefixOf(direct)   the one form of a direct address ("" when it is not http(s)); its last path segment

   A page that may run without this file says `if (window.SkippyDirect) g = await SkippyDirect.pick(g);`. base.html loads it in the head
   of every page, ahead of the page's own scripts, so only a page that is not built on base.html would be without it. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.SkippyDirect = factory(root);
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  const PROBE_MS = 2500;       // a door that has not answered /health by now is not the door to use
  const OK_MS = 120000;        // an answer is believed for two minutes ...
  const BAD_MS = 300000;       // ... and a silence for five, so a door that is down costs one probe, not one per request
  const RENEW_S = 2100;        // a pass with no door offered is treated as ending in 35 minutes, which every player answers by asking for a new one in 5 (they renew 30 minutes before the end)
  const KEY = "skippy-direct:";
  const SEEN = "skippy-direct-seen";
  const DOOR = /^https?:\/\/[^\s/?#@]+(?:\/[^\s?#]*)?$/i;      // (no `user@` in front of the host: https://home.makerportal.live@elsewhere/ is elsewhere)

  /** The direct address in its one form (no trailing slash), or "" when it is not an http(s) address at all. */
  function doorOf(direct) {
    if (typeof direct !== "string") return "";
    const d = direct.trim().replace(/\/+$/, "");
    return DOOR.test(d) ? d : "";
  }
  /** The last path segment of a direct address: "music" for https://home.makerportal.live/music ("" when there is none). */
  function prefixOf(direct) {
    const d = doorOf(direct);
    return d ? (d.replace(/^https?:\/\/[^/]*/i, "").split("/").filter(Boolean).pop() || "") : "";
  }

  function create(env) {
    env = env || {};
    const verdicts = new Map();   // door -> {ok, at}: this page's own memory, so a browser with no storage still asks once, not on every call
    const flying = new Map();     // door -> the probe in the air: callers that arrive meanwhile wait for it

    const here = () => (typeof globalThis !== "undefined" ? globalThis : root) || {};
    const clock = () => (typeof env.now === "function" ? env.now() : Date.now());
    function storage() {          // reading the property can throw too (a sandboxed frame, blocked site data)
      try { return env.storage !== undefined ? env.storage : (here().sessionStorage || null); } catch (e) { return null; }
    }
    // A clock that went backwards (a car's can be minutes out) makes an answer too old to trust, never immortal.
    const alive = (v, t) => !!v && typeof v.ok === "boolean" && typeof v.at === "number" && t >= v.at && t - v.at < (v.ok ? OK_MS : BAD_MS);

    function recall(door, t) {
      const mine = verdicts.get(door);
      if (alive(mine, t)) return mine;
      try {
        const s = storage();
        const raw = s ? s.getItem(KEY + door) : null;
        const v = raw ? JSON.parse(raw) : null;
        if (alive(v, t)) { verdicts.set(door, { ok: v.ok, at: v.at }); return verdicts.get(door); }
      } catch (e) { /* no storage, or nothing readable in it: this door is not remembered */ }
      return null;
    }
    function remember(door, ok, t) {
      verdicts.set(door, { ok: !!ok, at: t });
      try { const s = storage(); if (s) s.setItem(KEY + door, JSON.stringify({ ok: !!ok, at: t })); } catch (e) { /* private window or full: this page's memory has it */ }
      if (ok) noteDoor();
    }
    // Has this browser ever seen the door answer? Only then is a pass without one worth asking for again soon (a site whose door is not set up must not renew its passes every five minutes for nothing).
    let seen = false;
    function longTerm() { try { return env.local !== undefined ? env.local : (here().localStorage || null); } catch (e) { return null; } }
    function sawDoor() {
      if (seen) return true;
      try { const l = longTerm(); if (l && l.getItem(SEEN) === "1") seen = true; } catch (e) { /* no storage: this page's memory only */ }
      return seen;
    }
    function noteDoor() { seen = true; try { const l = longTerm(); if (l) l.setItem(SEEN, "1"); } catch (e) { /* private window or full */ } }

    /** GET <door>/health: true only when it answers 2xx with a JSON object (what every gate's /health is) in time. Never rejects. */
    function probe(door) {
      return new Promise(function (resolve) {
        let over = false, timer = null, stop = null;
        const end = function (ok) {
          if (over) return;
          over = true; clearTimeout(timer); resolve(ok);
          if (!ok && stop) { try { stop.abort(); } catch (e) { /* already over */ } }      // a slow door stops costing a connection
        };
        try {
          const f = env.fetch || here().fetch;
          if (typeof f !== "function") { end(false); return; }
          timer = setTimeout(function () { end(false); }, env.probeMs || PROBE_MS);
          const init = { cache: "no-store", credentials: "omit" };
          if (typeof here().AbortController === "function") { stop = new (here().AbortController)(); init.signal = stop.signal; }
          Promise.resolve(f.call(here(), door + "/health", init))
            .then(function (r) {
              if (!r || !r.ok) return false;                                   // not a 2xx: the edge answered, but this gate is not behind it
              return r.json().then(function (j) { return !!j && typeof j === "object" && !Array.isArray(j); }, function () { return false; });   // an HTML page is not the gate
            })
            .then(end, function () { end(false); });
        } catch (e) { end(false); }
      });
    }

    function check(door) {
      let p = flying.get(door);
      if (!p) {
        p = probe(door).then(function (ok) {
          flying.delete(door);
          remember(door, ok, clock());
          return ok;
        });
        flying.set(door, p);
      }
      return p;
    }

    async function pick(g, opts) {
      try {
        if (!g || typeof g !== "object" || typeof g.url !== "string") return g;
        if (typeof g.funnel !== "string" || !g.funnel) g.funnel = g.url;      // (asked again: g.url may already be the direct door, g.funnel never is)
        const door = doorOf(g.direct);
        let ok = false;
        if (door && door !== g.funnel) {
          const known = opts && opts.fresh ? null : recall(door, clock());
          ok = known ? known.ok : await check(door);
        }
        g.url = ok ? door : g.funnel;
        // A pass that came with no door (the portal had it off, or down just then) must not keep this page on the Funnel for the pass's eight to fourteen hours: the players
        // ask for a new pass 30 minutes before the old one ends, so it is made to end in 35 minutes, and the page looks at the door again in about five.
        if (!door && typeof g.exp === "number" && isFinite(g.exp) && sawDoor()) { const cap = clock() / 1000 + RENEW_S; if (g.exp > cap) g.exp = cap; }
      } catch (e) { /* whatever went wrong, g goes on as it was */ }
      return g;
    }

    function demote(g, used) {
      try {
        if (!g || typeof g !== "object" || typeof g.funnel !== "string" || !g.funnel) return false;
        const door = doorOf(g.direct);
        if (!door || door === g.funnel) return false;
        const went = typeof used === "string" && used ? used.replace(/\/+$/, "") : g.url;
        if (went !== door) return false;                                       // that call was not on the direct door: the Funnel has nothing to add
        g.url = g.funnel;
        remember(door, false, clock());
        return true;
      } catch (e) { return false; }
    }

    return { pick: pick, demote: demote };
  }

  const shared = create({});
  return { pick: shared.pick, demote: shared.demote, create: create, doorOf: doorOf, prefixOf: prefixOf, PROBE_MS: PROBE_MS, OK_MS: OK_MS, BAD_MS: BAD_MS, RENEW_S: RENEW_S };
});
