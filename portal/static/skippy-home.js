/* The permanent Home chat inside Skippy's chat (the Captain only): the house conversation, live, words only.
 *
 *   const home = SkippyHome.attach({view, composer, urls: {events, send, forward}, glyph(id), say(text)});
 *   home.open() / home.close() / home.isOpen() / home.send(text)
 *
 * The view holds .home-flow (the transcript), .home-state, .home-forward, .home-modes button[data-mode], .home-room, .home-speak.
 * Events come from /home-chat/events (polled while the view is open and the tab is visible): heard = said TO Skippy at a pod,
 * said = what he said back (with his mood letter), announce = his timers and announcements, actions = the tools he used, one folded
 * line, note = the house saying something about the thread itself (forwarding switched). Typed messages are marked "typed".
 * Everything is written with textContent: nothing from a room is ever treated as markup. */
(function () {
  "use strict";
  const MOODS = {n: "neutral", h: "happy", e: "excited", l: "laughing", s: "surprised", q: "curious", a: "annoyed", m: "smug", d: "sad",
                 w: "worried", b: "bored", r: "embarrassed", v: "loving", k: "sick"};

  function clock(sec) {
    const d = new Date(sec * 1000), now = new Date();
    const t = d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit"});
    return d.toDateString() === now.toDateString() ? t : d.toLocaleDateString(undefined, {month: "short", day: "numeric"}) + " " + t;
  }

  function turn(ev, glyph) {
    const div = document.createElement("div");
    if (ev.kind === "actions" || ev.kind === "note") {
      div.className = "home-line" + (ev.kind === "note" ? " note" : "");
      div.append(glyph(ev.kind === "note" ? "info" : "gear"));
      const span = document.createElement("span");
      span.textContent = (ev.kind === "actions" ? ev.room + " · " + ev.tools.join(" · ") : ev.text) + " · " + clock(ev.at);
      div.append(span);
      return div;
    }
    const heard = ev.kind === "heard";
    div.className = "turn home-turn " + (heard ? "user" : "assistant") + (ev.kind === "announce" ? " announce" : "") + (ev.typed ? " typed" : "") + (ev.silent ? " silent" : "");
    const body = document.createElement("div"); body.className = "tx";
    const who = document.createElement("div"); who.className = "who";
    const m = ev.mood && MOODS[ev.mood.toLowerCase()] ? " · " + (ev.mood === ev.mood.toUpperCase() ? "very " : "") + MOODS[ev.mood.toLowerCase()] : "";
    who.textContent = ev.room + " · " + clock(ev.at) + (ev.kind === "announce" ? " · announcement" : "") + (ev.typed ? " · typed" : "")
                    + (ev.silent ? " · not spoken" : "") + m;
    const text = document.createElement("div"); text.textContent = ev.text;
    body.append(who, text);
    div.append(glyph(heard ? (ev.typed ? "pen" : "ear") : "can"), body);
    return div;
  }

  function attach(opts) {
    const view = opts.view, flow = view.querySelector(".home-flow"), state = view.querySelector(".home-state");
    const fwd = view.querySelector(".home-forward"), room = view.querySelector(".home-room"), speak = view.querySelector(".home-speak");
    const modes = [...view.querySelectorAll(".home-modes button")];
    let open = false, seq = 0, timer = null, mode = "say", forward = true, loaded = false, busy = false;

    const atEnd = () => flow.scrollHeight - flow.scrollTop - flow.clientHeight < 60;
    function paintRooms(rooms) {
      const want = room.value, ids = ["*"].concat(rooms);
      if (ids.join() === [...room.options].map(o => o.value).join()) return;
      room.replaceChildren(...ids.map(r => { const o = document.createElement("option"); o.value = r; o.textContent = r === "*" ? "all rooms" : r; return o; }));
      room.value = ids.includes(want) ? want : (rooms[0] || "*");
    }
    function paintState(d) {
      forward = d.forward;
      fwd.setAttribute("aria-pressed", forward ? "true" : "false");
      fwd.title = forward ? "Forwarding is on: tap to switch it off" : "Forwarding is off: tap to switch it on";
      state.textContent = !forward ? "Forwarding is off: nothing said at the pods comes here." : d.hub ? "" : "The room hub isn't answering.";
      state.hidden = !state.textContent;
      paintRooms(d.rooms || []);
    }
    async function poll() {
      if (!open || document.hidden) return;
      try {
        const r = await fetch(opts.urls.events + "?after=" + seq, {cache: "no-store"});
        if (!r.ok) throw new Error(String(r.status));
        const d = await r.json(), stick = atEnd() || !loaded;
        if (!loaded) flow.replaceChildren();
        d.events.forEach(ev => { if (ev.seq > seq) { flow.append(turn(ev, opts.glyph)); seq = ev.seq; } });
        seq = Math.max(seq, d.seq || 0);
        if (!loaded && !d.events.length) { const e = document.createElement("div"); e.className = "home-line note"; e.textContent = "Nothing has been said to Skippy at a pod yet."; flow.append(e); }
        loaded = true;
        paintState(d);
        if (stick) flow.scrollTop = flow.scrollHeight;
      } catch (e) { state.textContent = "The portal isn't answering."; state.hidden = false; }
    }
    function paintMode() {
      modes.forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === mode ? "true" : "false"));
      if (open && opts.composer) opts.composer.placeholder = mode === "say" ? "Say to Skippy, as if spoken in the room" : "Make Skippy say";
    }
    modes.forEach(b => b.addEventListener("click", () => { mode = b.dataset.mode; paintMode(); }));
    speak.addEventListener("click", () => speak.setAttribute("aria-pressed", speak.getAttribute("aria-pressed") === "true" ? "false" : "true"));
    fwd.addEventListener("click", async () => {
      try {
        await fetch(opts.urls.forward, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({on: !forward})});
        poll();
      } catch (e) { opts.say("The portal isn't answering."); }
    });

    async function send(text) {
      text = String(text || "").trim();
      if (!text || busy) return false;
      busy = true;
      try {
        const r = await fetch(opts.urls.send, {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({mode: mode, text: text, room: room.value || "*", speak: speak.getAttribute("aria-pressed") === "true"})});
        if (!r.ok) { opts.say(((await r.json().catch(() => ({}))).error) || "That didn't go."); return false; }
        poll();
        return true;
      } catch (e) { opts.say("The portal isn't answering."); return false; } finally { busy = false; }
    }

    return {
      isOpen: () => open,
      send,
      open() {
        if (open) return;
        open = true; view.hidden = false; paintMode(); poll();
        timer = setInterval(poll, 1500);
      },
      close() {
        if (!open) return;
        open = false; view.hidden = true; clearInterval(timer);
        if (opts.composer) opts.composer.placeholder = opts.restore || "Message Skippy";
      },
    };
  }
  window.SkippyHome = {attach: attach, turn: turn};
})();
