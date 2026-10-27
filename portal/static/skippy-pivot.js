/* Does this message ask to change the subject? (docs/pivot.md)
 *
 *   SkippyPivot.detect(text) -> "pivot" | "additive" | null
 *
 * The Captain's rule (2026-10-27): "let's pivot", "let's change the subject", "on to a new feature" and the like ask for a pivot (the
 * chat then asks whether stopping now would lose work in progress). "By the way", "also", "I noticed" are ADDITIVE: they add to the
 * conversation and never trigger a pivot. Only the start of the message counts: a pivot phrase deep inside a long message is just words.
 * The chat is never switched by this alone: a "pivot" answer only opens the question; nothing is dumped or compressed until he agrees.
 * Pure, no DOM: tested by portal/tests/js/pivot/skippy-pivot.test.js.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyPivot = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const HEAD = 90;                                     // characters from the start that may carry the phrase

  // Opening words that ADD to the conversation. They win over a pivot phrase right behind them ("also, let's pivot" is still an addition
  // to what he is saying; a real pivot says so first).
  const ADDITIVE = /^\s*(?:oh[,!]?\s+(?:and|also|one more thing)|by the way|btw|also|and also|i noticed|i just noticed|i also noticed|one more thing|while we(?:'|’)re at it|while we are at it|side note|aside|p\.?s\.?|quick question|unrelated|on a related note|in addition|another thing|speaking of)\b/i;

  // Phrases that ask to leave the subject. "pivot" alone is not one (pivot table, pivot point): the phrase needs its verb or its target.
  const PIVOT = [
    /\b(?:let(?:'|’)?s|lets|let us|we should|we need to|time to|i want to|i(?:'|’)d like to|i would like to|ready to)\s+(?:pivot|switch gears|change (?:the )?subject|change (?:the )?topic|change direction|move on|move to something else|talk about something else)\b(?!\s+(?:table|chart|tables|charts|data|point|the table|the chart))/i,
    /\b(?:pivot(?:ing)?\s+to|switching gears|change of subject|change of topic|changing the subject|changing topics|new topic|different topic|new subject|different subject)\b/i,
    /\bon to (?:a |the |our )?(?:new|next|different|another)\b/i,
    /\b(?:moving|move|going|onward|onwards) on to (?:a |the |our )?(?:new|next|different|another|something)\b/i,
    /\bnext (?:up|feature|task|project|thing)\b/i,
    /\bnow for something (?:else|different)\b/i,
  ];

  function detect(text) {
    const t = String(text == null ? "" : text).replace(/^\s+/, "");
    if (!t) return null;
    if (ADDITIVE.test(t)) return "additive";
    const head = t.slice(0, HEAD);
    return PIVOT.some((re) => re.test(head)) ? "pivot" : null;
  }

  // What the chat knows about work in progress -> the question to ask before pivoting, or null when stopping loses nothing.
  // state: {running: bool, goal: "running"|"checking"|"parked"|"done"|..., uncommitted: n, queued: n, task: "short words"}
  function inTheMiddle(state) {
    const s = state || {};
    const bits = [];
    if (s.running) bits.push("a run is still going");
    if (s.goal === "running" || s.goal === "checking") bits.push("a goal is still being worked on");
    if (s.uncommitted > 0) bits.push(s.uncommitted + " change" + (s.uncommitted === 1 ? "" : "s") + " not committed yet");
    if (s.queued > 0) bits.push(s.queued + " message" + (s.queued === 1 ? "" : "s") + " waiting");
    if (!bits.length) return null;
    return "We look to be in the middle of something" + (s.task ? " (" + s.task + ")" : "") + ": " + bits.join(", ") + ". Pivot now, or finish first?";
  }

  return {detect, inTheMiddle, HEAD};
});
