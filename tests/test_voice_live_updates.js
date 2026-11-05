"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function check(directory) {
  const folder = path.join(__dirname, "..", directory);
  const store = { defaultVoice: "first", dialogueVoice: "first", narratorVoice: "first",
    characterMappings: [{ tag: "Alice", voice: "first", enabled: true }] };
  let catalog = [{ id: "first", name: "First" }];
  const storageListeners = [];
  let onMessage;
  const event = { addListener() {} };
  const context = vm.createContext({
    console: { info() {}, warn() {}, error() {}, log() {} }, setTimeout, clearTimeout,
    AbortController, URL, Blob, performance,
    fetch: async (url) => ({ ok: true, json: async () => String(url).endsWith("/voices") ? { voices: structuredClone(catalog) } : { status: "ok" } }),
    browser: {
      storage: { onChanged: { addListener(fn) { storageListeners.push(fn); } }, local: {
        async get(defaults) {
          if (defaults === null) return structuredClone(store);
          return Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => [key, structuredClone(store[key] ?? fallback)]));
        },
        async set(updates) {
          const changes = {};
          for (const [key, value] of Object.entries(updates)) {
            if (JSON.stringify(store[key]) === JSON.stringify(value)) continue;
            changes[key] = { oldValue: store[key], newValue: structuredClone(value) };
            store[key] = structuredClone(value);
          }
          if (Object.keys(changes).length) for (const listener of storageListeners) listener(changes, "local");
        }
      } },
      runtime: { onMessage: { addListener(fn) { onMessage = fn; } },
        sendMessage: async () => ({}),
        sendNativeMessage: async (_, request) => {
          if (request.command === "voice_import") catalog.push({ id: request.id, name: request.name });
          if (request.command === "voice_update") {
            if (request.enabled === false) catalog = catalog.filter((voice) => voice.id !== request.id);
            else {
              const voice = catalog.find((item) => item.id === request.id);
              if (voice) voice.name = request.name || voice.name;
              else catalog.push({ id: request.id, name: request.name });
            }
          }
          if (request.command === "voice_delete") catalog = catalog.filter((voice) => voice.id !== request.id);
          return { ok: true, voices: structuredClone(catalog), apiReload: { status: "reloaded" } };
        }
      }, menus: { create() {}, onClicked: event }, commands: { onCommand: event },
      browserAction: { onClicked: event }, sidebarAction: { toggle: async () => {} }
    }
  });
  context.IrodoriInstallConfig = { nativeHost: "io.github.irodori.voice_reader.firefox" };
  context.IrodoriFirefoxNativeMessage = (...args) => context.browser.runtime.sendNativeMessage(...args);
  for (const file of ["text-processor.js", "voice-readiness.js", "api-state.js"]) {
    vm.runInContext(fs.readFileSync(path.join(folder, file), "utf8"), context);
  }
  // Only suppress automatic engine startup; test the real background message
  // handler, storage listener, readiness and queue code without a browser mock
  // accidentally starting a user's Native Host.
  const source = fs.readFileSync(path.join(folder, "background.js"), "utf8")
    .replace(/\nensureEngineReady\(\)\.catch\([\s\S]*$/, "");
  vm.runInContext(source + "\nglobalThis.test = { state, ensureJobVoiceReady, settings, invalidatePendingVoiceJobs };", context);
  const test = context.test;
  test.state.paused = true;
  const send = (message) => onMessage(message, {});
  await send({ type: "voices" });
  const playing = { status: "playing", voice: "first", blob: {} };
  test.state.current = playing;
  await send({ type: "native-control", command: "voice_import", id: "new", name: "New" });
  assert(test.state.voices.some((voice) => voice.id === "new"), "import invalidates the live background catalog");
  assert(store.cachedVoices.some((voice) => voice.id === "new"), "other open UI pages receive the new catalog");
  assert.equal(test.state.current, playing, "Voice changes must not stop playing audio");
  await context.browser.storage.local.set({ characterMappings: [{ tag: "Alice", voice: "new", enabled: true }] });
  const job = { engineEpoch: 0, voiceRevision: -1, tag: "Alice", voice: "first",
    voiceRoute: { voice: "first", source: "character_tag", tag: "Alice", locked: true } };
  await test.ensureJobVoiceReady(job);
  assert.equal(job.voice, "new", "the next TTS job uses the changed character Voice without restart");
  await send({ type: "native-control", command: "voice_update", id: "new", name: "Renamed" });
  assert.equal(store.voiceDisplayNames.new, "Renamed");
  const record = { source: "manual", conversationId: "test", messageId: "1", state: "generated" };
  const pending = { status: "generated", blob: {}, answerRecord: record, chunkIndex: 0 };
  test.state.queue.push(pending);
  await send({ type: "native-control", command: "voice_update", id: "new", enabled: false });
  assert(!test.state.voices.some((voice) => voice.id === "new"));
  assert.equal(pending.blob, null, "prefetched WAVs do not retain an edited/disabled Voice");
  assert.equal(pending.status, "queued");
  await send({ type: "native-control", command: "voice_update", id: "new", name: "Renamed", enabled: true });
  assert(test.state.voices.some((voice) => voice.id === "new"));
  await send({ type: "native-control", command: "voice_delete", id: "new" });
  assert(!test.state.voices.some((voice) => voice.id === "new"));
  await send({ type: "native-control", command: "voice_delete", id: "first" });
  const empty = await send({ type: "voices" });
  assert.equal(empty.voices.length, 0, "deleting the last Voice must not resurrect cached entries");
  assert.equal(store.cachedVoices.length, 0);
  assert.equal(test.state.current, playing);
  const saved = context.IrodoriTextProcessor.settingsPatchWithoutEngine({ defaultVoice: "new", cachedVoices: [{ id: "obsolete" }] });
  assert.equal(saved.cachedVoices, undefined, "an old editor cannot overwrite the live catalog");
  console.log(directory + ": Voice import/update/rename/disable/enable/delete, pending routing and current playback passed");
}

(async () => {
  await check("chrome-extension");
  if (process.argv.includes("--firefox")) await check("firefox-extension");
})().catch((error) => { console.error(error); process.exitCode = 1; });
