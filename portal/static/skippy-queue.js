/* The chat queue, shared by both chats (templates/_chat.html for green, templates/_workshop.html for the agent): messages sent while
 * Skippy is still working wait in line, and go together, as ONE message, when he is done.
 *
 *   SkippyQueue.add(queue, text)   push text unless it says what a waiting message already says; true if it went in
 *   SkippyQueue.gulp(items)        the one message the line becomes ("Several things queued ...", numbered), repeats taken out
 *   SkippyQueue.same(a, b)         two messages say the same thing: case, spacing and the punctuation at the ends don't count
 *
 * A repeat is a tap that looked unanswered or a voice line said twice; close but different words are kept (telling those apart
 * would take a model). Pure functions, no DOM: tests/js/queue runs them under Node.
 */
(function (root) {
  "use strict";
  const norm = (t) => String(t || "").toLowerCase().replace(/\s+/g, " ").trim().replace(/^[\s.,!?;:…]+|[\s.,!?;:…]+$/g, "");
  const same = (a, b) => norm(a) !== "" && norm(a) === norm(b);
  function add(queue, text) {
    if (!String(text || "").trim() || queue.some(q => same(q, text))) return false;
    queue.push(text);
    return true;
  }
  function gulp(items) {
    const kept = [];
    items.forEach(t => add(kept, t));
    return kept.length === 1 ? kept[0] : "Several things queued while you worked, in order:\n" + kept.map((t, i) => (i + 1) + ". " + t).join("\n");
  }
  const api = {norm: norm, same: same, add: add, gulp: gulp};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyQueue = api;
})(typeof window !== "undefined" ? window : this);
