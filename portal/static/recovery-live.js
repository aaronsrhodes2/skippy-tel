/* The Start-or-stop card on the Admin page's Recovery pane: while the card is on screen, its steps (Docker, the containers, the bridge,
   the model connections, the local services) are refreshed from /admin/recovery/status.json, so a start shows its progress without a reload.
   Nothing else on the page is polled. recovery_web.py answers the JSON; templates/_recovery.html draws the card. */
(function () {
  const card = document.getElementById("starter");   // its data-poll-url is the JSON to poll
  if (!card || !window.fetch) return;
  const DOT = {ok: "green", warn: "amber", wait: "amber", bad: "red"};
  function paint(j) {
    (j.steps || []).forEach(function (s) {
      const li = card.querySelector('[data-step="' + s.id + '"]');
      if (!li) return;
      li.dataset.state = s.state;
      li.querySelector(".dot").className = "dot " + (DOT[s.state] || "amber");
      li.querySelector("[data-detail]").textContent = s.detail;
    });
    const w = document.getElementById("working");
    if (w) w.textContent = j.working ? "Working: " + j.working : (j.waiting && j.waiting.length ? "Waiting for the starter to pick up: " + j.waiting.join(", ") : "");
  }
  function tick() {
    if (document.hidden || card.offsetParent === null) return;   // the pane is closed or the tab is away
    fetch(card.dataset.pollUrl, {credentials: "same-origin", cache: "no-store"})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j) paint(j); })
      .catch(function () {});
  }
  setInterval(tick, 3000);
})();
