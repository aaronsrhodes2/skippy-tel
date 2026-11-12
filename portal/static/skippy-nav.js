/* Words that move the stage: the chat's navigation keys (Apps, Your chats, New chat, and each app's tile) as things you can say or type
 * (the Captain, 2026-11-11: "why do we need UI controls? ... asking will be faster than navigating menus"). The telemetry said the most-tapped
 * controls are all navigation, and navigation is short to say, so it is spoken here instead of hunted for: instant, no model, no tokens.
 *
 *   SkippyNav.maybe(text)          could this be a navigation request? (a few words, the right shape): the page loads the tiles only then
 *   SkippyNav.match(text, tiles)   {kind: "new"} | {kind: "sheet", name: "apps" | "chats"} | {kind: "open", tile} | null
 *
 * Conservative on purpose: the whole message must be the request (six words at most), so "open the pod bay doors" or a real question never
 * gets taken; anything that does not match goes to Skippy as before. Pure functions, no DOM: tests/js/nav runs them under Node.
 */
(function (root) {
  "use strict";
  const MAX_WORDS = 6;
  const norm = (t) => String(t || "").toLowerCase().replace(/[’`]/g, "'").replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim()
    .replace(/^(hey |ok |okay )?(skippy )?(can you |could you |would you |will you )?(please )?/, "").replace(/ please$/, "").trim();
  const ALIASES = {
    games: ["game servers", "servers"], play: ["browser games"], music: ["bilby", "music", "bilby's audio service", "bilbys audio service", "audio service", "songs"],
    video: ["nagatha", "video", "movies", "films", "nagatha's video service"], books: ["audiobooks", "books", "audio books"], experiments: ["experiments", "demos"],
    tabletop: ["tabletop", "heroes of chance"], car: ["car", "tesla"], alarms: ["alarms", "alarm", "timers", "alarms and timers"], drive: ["drive", "driving"],
    hud: ["glasses", "hud"], texts: ["texts", "text messages"], dashboard: ["dashboard", "my dashboard"], admin: ["admin"],
  };
  const NEW = /^(start (a |an )?)?(new|fresh) (chat|conversation)$|^start over$|^clear (the |this )?chat$/;
  const CHATS = /^(show |open |list |go to )?(me )?(my |the |all )?(past |old |previous |earlier )?(chats|conversations|chat history|history)$/;
  const APPS = /^(show |open |list |go to )?(me )?(my |the |all )?(apps|app tray|launcher|tiles|all apps)$/;
  const OPEN = /^(open|go to|take me to|launch|show me|show|bring up|switch to) (the |my )?(.+?)( app| page| tile)?$/;
  const shaped = (t) => t !== "" && t.split(" ").length <= MAX_WORDS;
  function maybe(text) {
    const t = norm(text);
    return shaped(t) && (NEW.test(t) || CHATS.test(t) || APPS.test(t) || OPEN.test(t));
  }
  function match(text, tiles) {
    const t = norm(text);
    if (!shaped(t)) return null;
    if (NEW.test(t)) return {kind: "new"};
    if (CHATS.test(t)) return {kind: "sheet", name: "chats"};
    if (APPS.test(t)) return {kind: "sheet", name: "apps"};
    const m = OPEN.exec(t);
    if (!m) return null;
    const want = m[3].trim();
    for (const tile of tiles || []) {
      const names = [String(tile.label || "").toLowerCase().replace(/[’`]/g, "'"), String(tile.id || "").toLowerCase(), ...(ALIASES[tile.id] || [])];
      if (names.includes(want)) return {kind: "open", tile: tile};
    }
    return null;
  }
  const api = {norm: norm, maybe: maybe, match: match};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SkippyNav = api;
})(typeof window !== "undefined" ? window : globalThis);
