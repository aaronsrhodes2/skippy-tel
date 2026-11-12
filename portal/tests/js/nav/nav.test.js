// Words that move the stage (portal/static/skippy-nav.js). portal/tests/test_chat_nav.py runs this under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const N = require("../../../static/skippy-nav.js");

const tiles = [{id: "games", label: "Game servers", href: "/games"}, {id: "music", label: "Bilby's Audio Service", href: "/music"},
               {id: "video", label: "Nagatha's Video Service", href: "/video"}, {id: "drive", label: "Drive", href: "/drive"},
               {id: "alarms", label: "Alarms and timers", href: "/alarms"}, {id: "dashboard", label: "Dashboard", href: "/me"}];

test("the three chat keys can be said", () => {
  assert.deepEqual(N.match("new chat", tiles), {kind: "new"});
  assert.deepEqual(N.match("Hey Skippy, start a new conversation please", tiles), {kind: "new"});
  assert.deepEqual(N.match("start over", tiles), {kind: "new"});
  assert.deepEqual(N.match("show my chats", tiles), {kind: "sheet", name: "chats"});
  assert.deepEqual(N.match("chat history", tiles), {kind: "sheet", name: "chats"});
  assert.deepEqual(N.match("open the apps", tiles), {kind: "sheet", name: "apps"});
  assert.deepEqual(N.match("apps", tiles), {kind: "sheet", name: "apps"});
});

test("an app opens by its name, its id or what he calls it", () => {
  assert.equal(N.match("open Bilby", tiles).tile.href, "/music");
  assert.equal(N.match("go to the movies", tiles).tile.href, "/video");
  assert.equal(N.match("take me to nagatha", tiles).tile.href, "/video");
  assert.equal(N.match("launch drive", tiles).tile.href, "/drive");
  assert.equal(N.match("show me my dashboard", tiles).tile.href, "/me");
  assert.equal(N.match("open Bilby's Audio Service", tiles).tile.href, "/music");
  assert.equal(N.match("open timers", tiles).tile.href, "/alarms");
});

test("anything that is not exactly a navigation request goes to Skippy", () => {
  assert.equal(N.match("open the pod bay doors", tiles), null);                       // no such app
  assert.equal(N.match("open the music and play something calm", tiles), null);       // more than a navigation request (and too long)
  assert.equal(N.match("play music", tiles), null);                                   // a request for sound, not a place
  assert.equal(N.match("what is the new chat feature for?", tiles), null);
  assert.equal(N.match("", tiles), null);
  assert.equal(N.match("open bilby", []), null);                                      // tiles not loaded: nothing to open yet
  assert.ok(!N.maybe("what time is it"));
  assert.ok(N.maybe("open bilby") && N.maybe("new chat") && N.maybe("my chats"));      // worth loading the tiles for
});
