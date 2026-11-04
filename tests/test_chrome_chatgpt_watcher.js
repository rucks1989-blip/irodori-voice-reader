"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Regression cases from Chrome's live ChatGPT DOM/route trace (2026-11-04).
// This checks watcher state transitions, not synthesis or physical playback.
async function fixture(path = "/", history = false, site = "chatgpt") {
  let now = 0, next = 0, mutation;
  const timers = new Map(), handlers = new Map(), enqueued = [], events = [];
  const users = [], answers = [], location = { pathname: path };
  const node = (id, text, order) => ({ id, text, order, isConnected: true,
    contains: () => false, compareDocumentPosition: other => order < other.order ? 4 : 2 });
  if (history) { users.push(node("history-user", "old", 1)); answers.push(node("history-answer", "old answer", 2)); }
  const adapter = {
    name: site, enabledKey: site + "Enabled", userMessages: () => users,
    candidates: () => answers, messageId: n => n.id, userText: n => n.text,
    text: n => n.text, streaming: n => Boolean(n.streaming),
    conversationId: () => location.pathname.split(site === "gemini" ? "/app/" : "/c/")[1] || ""
  };
  const win = {
    location, setTimeout: (fn, ms) => { timers.set(++next, { at: now + ms, fn }); return next; },
    clearTimeout: id => timers.delete(id), setInterval: () => 0, clearInterval() {},
    addEventListener() {}, removeEventListener() {},
    MutationObserver: class { constructor(fn) { mutation = fn; } observe() {} disconnect() {} }
  };
  const editor = { value: "", matches: selector => selector.includes(site === "gemini" ? "rich-textarea" : '[data-composer-markdown]'),
    closest(selector) { return this.matches(selector) ? this : null; } };
  const doc = { documentElement: {}, querySelectorAll: () => [editor],
    addEventListener: (type, fn) => handlers.set(type, fn), removeEventListener() {} };
  const context = vm.createContext({ Date: class extends Date { static now() { return now; } } });
  vm.runInContext(fs.readFileSync(require.resolve("../chrome-extension/chatgpt-watcher.js"), "utf8"), context);
  const control = context.IvrChatgptWatcher.start({ doc, win,
    api: { storage: { local: { get: async defaults => ({ ...defaults, [site + "Enabled"]: true, timingDebug: true }), set: async () => {} },
      onChanged: { addListener() {}, removeListener() {} } } },
    getAdapter: () => adapter, hash: async text => `hash:${text}`, toast() {},
    request: async (type, payload) => {
      if (type === "enqueue") { enqueued.push(payload); return { queued: true }; }
      events.push(payload.event);
    }, baselineQuietMs: 20, scanMs: 1, settleMs: 10 });
  const microtasks = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  async function advance(ms) {
    await microtasks();
    const end = now + ms;
    while (true) {
      const entry = [...timers].filter(([, t]) => t.at <= end).sort((a,b) => a[1].at - b[1].at)[0];
      if (!entry) break;
      const [id, task] = entry; now = task.at; timers.delete(id); await task.fn(); await microtasks();
    }
    now = end; await microtasks();
  }
  await advance(30);
  return { users, answers, enqueued, events, node, control, advance,
    async scan() { mutation(); await advance(2); },
    async route(path) { location.pathname = path; mutation(); await advance(2); },
    async send(text) { editor.value = text; handlers.get("keydown")({ isTrusted: true, key: "Enter", target: editor }); await advance(2); }
  };
}

module.exports = { fixture };
if (require.main === module) (async () => {
  const f = await fixture();
  await f.send("new question");
  f.users.push(f.node("u1", "new question", 1));
  const answer = f.node("a1", "unfinished", 2); answer.streaming = true; f.answers.push(answer);
  await f.scan();
  await f.route("/c/local-chatgpt%3Atemporary-uuid");
  await f.route("/c/server-conversation-id");
  await f.advance(100);
  assert.equal(f.enqueued.length, 0, "never enqueue during generation");
  answer.text = "音声再生の確認です。"; answer.streaming = false;
  await f.scan(); await f.advance(20);
  assert.equal(f.enqueued.length, 1, "the provisional-to-server transition must retain the send");
  assert.equal(f.enqueued[0].text, answer.text);
  assert.equal(f.enqueued[0].answer.conversationId, "server-conversation-id");
  await f.scan(); await f.advance(100);
  assert.equal(f.enqueued.length, 1, "exactly once after completion");
  assert.equal(f.events.filter(e => e.event === "chatgpt_route_transition_preserved").length, 2);
  f.control.stop();

  const h = await fixture("/c/history", true);
  await h.scan(); await h.advance(100);
  assert.equal(h.enqueued.length, 0, "reload must not read history");
  await h.send("expected");
  h.users.push(h.node("foreign", "different question", 3));
  h.answers.push(h.node("foreign-answer", "unrelated answer", 4));
  await h.scan(); await h.advance(30);
  assert.equal(h.enqueued.length, 0, "do not match an unrelated user turn");
  h.control.stop();

  const n = await fixture("/c/first");
  await n.send("pending question");
  n.users.push(n.node("u", "pending question", 1)); await n.scan();
  n.users.push(n.node("later-u", "later question", 3)); n.answers.push(n.node("later-a", "later answer", 4));
  await n.scan(); await n.advance(30);
  assert.equal(n.enqueued.length, 0, "a later question cannot donate its answer");
  await n.route("/c/other-conversation");
  await n.advance(30);
  assert.equal(n.enqueued.length, 0, "real conversation navigation cancels the pending send");
  n.control.stop();
  console.log("Chrome ChatGPT watcher: composer, two-stage route, streaming, exactly-once, history and unrelated-turn regressions passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
