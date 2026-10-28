"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { performance } = require("node:perf_hooks");
const Processor = require("../chrome-extension/text-processor.js");
const VoiceReadiness = require("../chrome-extension/voice-readiness.js");

const voices = [
  { id: "kurisu", name: "クリス" },
  { id: "yui", name: "結衣" },
  { id: "nare", name: "ナレーション" },
  { id: "runa", name: "ルナ" }
];
const base = Processor.mergedSettings({
  narrationEnabled: true,
  characterTagPriority: true,
  narratorVoice: "nare",
  dialogueVoice: "runa",
  defaultVoice: "yui",
  cachedVoices: voices,
  removeMarkdown: false,
  dialoguePairs: ["「」", "『』"]
});
const routingScenarios = [
  ["A", "【クリス】「今日はいい天気」そう彼女は言った。", base],
  ["B", "「おはよう」彼女は笑った。", base],
  ["C", "【クリス】「おはよう」クリスは笑った。【結衣】「やっはろー！」結衣も手を振った。", base],
  ["D", "朝になった。【クリス】「出発しましょう」二人は歩き出した。", base],
  ["E", "【クリス】「そうですね」「では行きましょう」彼女は立ち上がった。", base]
];

function actual(input, settings = base) {
  return Processor.processText(input, settings).map(({ text, voice, voiceSource }) => ({ text, voice, voiceSource }));
}

function checkCase(label, input, expected) {
  const result = actual(input);
  assert.deepEqual(result.map(({ text, voice }) => [text, voice]), expected, label);
  // The queue's locked semantic route must resolve to the same existing Voice
  // ID that the text processor selected, not the global/default Voice.
  for (const segment of Processor.processText(input, base)) {
    const resolved = VoiceReadiness.resolveJobVoice(
      { ...segment, voiceRoute: { ...segment.voiceRoute, locked: true } },
      base,
      voices,
      voices,
      base.defaultVoice
    );
    assert.equal(resolved.ok, true, `${label}: ${segment.text}`);
    assert.equal(resolved.voiceId, segment.voice, `${label}: ${segment.text}`);
  }
  console.log(`${label}: ${result.map(({ text, voice }) => `${JSON.stringify(text)} → ${voice}`).join(" | ")}`);
}

checkCase("A", "【クリス】「今日はいい天気」そう彼女は言った。", [
  ["今日はいい天気", "kurisu"], ["そう彼女は言った。", "nare"]
]);
checkCase("B", "「おはよう」彼女は笑った。", [
  ["おはよう", "runa"], ["彼女は笑った。", "nare"]
]);
checkCase("C", "【クリス】「おはよう」クリスは笑った。【結衣】「やっはろー！」結衣も手を振った。", [
  ["おはよう", "kurisu"], ["クリスは笑った。", "nare"],
  ["やっはろー！", "yui"], ["結衣も手を振った。", "nare"]
]);
checkCase("D", "朝になった。【クリス】「出発しましょう」二人は歩き出した。", [
  ["朝になった。", "nare"], ["出発しましょう", "kurisu"], ["二人は歩き出した。", "nare"]
]);
checkCase("E", "【クリス】「そうですね」「では行きましょう」彼女は立ち上がった。", [
  ["そうですね", "kurisu"], ["では行きましょう", "kurisu"], ["彼女は立ち上がった。", "nare"]
]);

const matrixInput = "【クリス】「会話」地の文";
const matrix = [
  { narrationEnabled: true, characterTagPriority: true, expected: [["会話", "kurisu"], ["地の文", "nare"]] },
  { narrationEnabled: true, characterTagPriority: false, expected: [["会話", "runa"], ["地の文", "nare"]] },
  { narrationEnabled: false, characterTagPriority: true, expected: [["「会話」地の文", "kurisu"]] },
  { narrationEnabled: false, characterTagPriority: false, expected: [["「会話」地の文", "yui"]] }
];
for (const combination of matrix) {
  const settings = Processor.mergedSettings({ ...base, ...combination });
  const result = actual(matrixInput, settings);
  assert.deepEqual(result.map(({ text, voice }) => [text, voice]), combination.expected);
}
assert.equal(new Set(matrix.map((item) => JSON.stringify(actual(matrixInput, Processor.mergedSettings({ ...base, ...item }))))).size, 4);
console.log("ON/OFF matrix: ON/ON=kurisu+nare; ON/OFF=runa+nare; OFF/ON=kurisu; OFF/OFF=yui");

const customSettings = Processor.mergedSettings({
  ...base,
  dialoguePairs: ["「」", "『』", "【】", "()"]
});
assert.deepEqual(actual("【クリス】【今日は】地の文", customSettings).map(({ text, voice }) => [text, voice]), [
  ["今日は", "kurisu"], ["地の文", "nare"]
]);
assert.deepEqual(actual("【未登録の台詞】地の文", Processor.mergedSettings({ ...customSettings, unknownTagBehavior: "skip" })).map(({ text, voice }) => [text, voice]), [
  ["未登録の台詞", "runa"], ["地の文", "nare"]
]);
assert.deepEqual(actual("「一」『二』(three)", customSettings).map(({ text, voice }) => [text, voice]), [
  ["一", "runa"], ["二", "runa"], ["three", "runa"]
]);
const manualAliasSettings = Processor.mergedSettings({
  ...customSettings,
  characterMappings: [
    { enabled: true, tag: "クリス", voice: "kurisu", manual: false },
    { enabled: true, tag: "クリス手動", voice: "yui", manual: true }
  ]
});
assert.equal(Processor.characterMappingCandidates(voices, manualAliasSettings.characterMappings)
  .some((item) => item.manual && item.tag === "クリス手動" && item.voice === "yui"), true);
assert.deepEqual(actual("【クリス手動】「会話」地の文", manualAliasSettings).map(({ text, voice }) => [text, voice]), [
  ["会話", "yui"], ["地の文", "nare"]
]);

const unknownInput = "【未登録】「未知話者の会話」地の文";
assert.deepEqual(actual(unknownInput).map(({ text, voice }) => [text, voice]), [
  ["【未登録】", "nare"], ["未知話者の会話", "runa"], ["地の文", "nare"]
]);
const unmatched = "未対応の「括弧";
assert.equal(actual(unmatched).map(({ text }) => text).join(""), unmatched);
assert.equal(actual(unmatched).every(({ voice }) => voice === "nare"), true);

// The two editors save only their own changes relative to a shared snapshot.
// A sidebar toggle and detailed-page Voice/tag edits must survive reconciliation.
const snapshot = Processor.mergedSettings({ ...base, characterMappings: [{ enabled: true, tag: "クリス", voice: "kurisu" }] });
const optionsDraft = Processor.mergedSettings({
  ...snapshot,
  narratorVoice: "nare",
  dialogueVoice: "runa",
  characterMappings: [{ enabled: true, tag: "クリス", voice: "kurisu" }, { enabled: true, tag: "結衣", voice: "yui" }]
});
const sidebarPatch = { narrationEnabled: false, characterTagPriority: false, dialoguePairs: ["「」", "()"] };
const remote = Processor.mergeSettingsPatch(snapshot, sidebarPatch, snapshot);
const reconciled = Processor.reconcileSettingsEditor(remote, optionsDraft, snapshot).settings;
assert.equal(reconciled.narrationEnabled, false);
assert.equal(reconciled.characterTagPriority, false);
assert.deepEqual(reconciled.dialoguePairs, ["「」", "()"]);
assert.deepEqual(reconciled.characterMappings.map((item) => item.voice), ["kurisu", "yui"]);
const duplicateVoiceRows = Processor.mergeSettingsPatch(snapshot, {
  characterMappings: [
    { enabled: true, tag: "クリス", voice: "kurisu", manual: false },
    { enabled: false, tag: "結衣", voice: "yui", manual: false },
    { enabled: true, tag: "クリス手動", voice: "yui", manual: true }
  ]
}, snapshot);
assert.equal(duplicateVoiceRows.characterMappings.length, 3);

assert.equal(Processor.classifyApiError(new Error("backend unavailable"), 503).retryable, true);
assert.equal(Processor.classifyApiError(new Error("unknown voice"), 404).retryable, false);
console.log("Custom pairs, unknown tags, unmatched punctuation, editor merge, and retry classification: passed");

async function verifyBackgroundTtsPayloads() {
  const backgroundPath = require.resolve("../chrome-extension/background.js");
  const source = fs.readFileSync(backgroundPath, "utf8");
  const listenerStart = source.indexOf("\nbrowserApi.runtime.onMessage.addListener(");
  assert.notEqual(listenerStart, -1, "background message-listener boundary");
  const captured = [];
  const timers = [];
  let schedulerCalls = 0;
  const visibleErrors = [];
  const fakeBrowser = {
    storage: {
      local: { get: async () => base },
      onChanged: { addListener() {} }
    },
    runtime: {}
  };
  const context = vm.createContext({
    browser: fakeBrowser,
    IrodoriTextProcessor: Processor,
    IrodoriVoiceReadiness: VoiceReadiness,
    URL,
    Blob,
    AbortController,
    performance,
    setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
    fetch: async (url, options) => {
      captured.push({ url, options, body: JSON.parse(options.body) });
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => null },
        blob: async () => new Blob([Buffer.alloc(64)])
      };
    },
    console: { info() {}, warn() {}, error() {}, log() {} }
  });
  vm.runInContext(`${source.slice(0, listenerStart)}
globalThis.__testHooks = {
  requestTts,
  generateJob,
  state,
  setRequestTts(value) { requestTts = value; },
  setEnsureJobVoiceReady(value) { ensureJobVoiceReady = value; },
  setPublishStatus(value) { publishStatus = value; },
  setDebugTransition(value) { debugTransition = value; },
  setCaptureTtsResultDetached(value) { captureTtsResultDetached = value; },
  setRunSchedulers(value) { runSchedulers = value; },
  setVisibleError(value) { visibleError = value; },
  setTimingLog(value) { timingLog = value; }
};`, context, { filename: backgroundPath });

  let index = 0;
  const scenarios = [...routingScenarios];
  for (const combination of matrix) {
    scenarios.push(["matrix", matrixInput, Processor.mergedSettings({ ...base, ...combination })]);
  }
  for (const [label, input, settings] of scenarios) {
    for (const segment of Processor.processText(input, settings)) {
      const resolved = VoiceReadiness.resolveJobVoice(
        { ...segment, voiceRoute: { ...segment.voiceRoute, locked: true } },
        settings,
        voices,
        voices,
        settings.defaultVoice
      );
      assert.equal(resolved.ok, true, `${label}: fresh Voice lookup for ${segment.text}`);
      const job = {
        ...segment,
        voice: resolved.voiceId,
        voiceSource: resolved.voiceSource,
        voiceRoute: { ...segment.voiceRoute, locked: true },
        messageId: `test-${label}-${index}`,
        chunkIndex: index,
        engineEpoch: 0,
        answerRecord: { originalText: input, chunkCount: 1, jobs: [] }
      };
      await context.__testHooks.requestTts(job);
      const sent = captured.at(-1);
      assert.equal(sent.url, "http://127.0.0.1:8765/tts");
      assert.equal(sent.body.text, segment.text, `${label}: API text`);
      assert.equal(sent.body.voice, segment.voice, `${label}: API Voice ID for ${segment.text}`);
      assert.equal(sent.body.speaker_tag_policy, "preserve");
      index += 1;
    }
  }
  const ids = [...new Set(captured.map((item) => item.body.voice))].sort();
  assert.deepEqual(ids, ["kurisu", "nare", "runa", "yui"]);
  console.log(`Background → mock /tts payloads: ${captured.length} requests; Voice IDs=${ids.join(", ")}`);

  const hooks = context.__testHooks;
  hooks.setEnsureJobVoiceReady(async () => {});
  hooks.setPublishStatus(async () => {});
  hooks.setDebugTransition(() => {});
  hooks.setCaptureTtsResultDetached(() => {});
  hooks.setTimingLog(() => {});
  hooks.setRunSchedulers(() => { schedulerCalls += 1; });
  hooks.setVisibleError(async (error) => { visibleErrors.push(error); });
  const retryRecord = { key: "retry", source: "manual", conversationId: "test", messageId: "retry", originalText: "retry", chunkCount: 1, jobs: [], state: "queued" };
  const retryJob = { answerRecord: retryRecord, messageId: "retry", chunkIndex: 0, engineEpoch: 0, status: "queued", ttsRetryAttempts: 0, text: "retry", voice: "kurisu", voiceSource: "character_tag" };
  retryRecord.jobs.push(retryJob);
  hooks.state.queue = [retryJob];
  let generationAttempts = 0;
  hooks.setRequestTts(async () => {
    generationAttempts += 1;
    if (generationAttempts < 3) {
      const error = new Error("backend busy");
      error.retryable = true;
      error.httpStatus = 503;
      throw error;
    }
    return new Blob([Buffer.alloc(64)]);
  });
  await hooks.generateJob(retryJob);
  assert.equal(retryJob.status, "queued");
  assert.equal(retryJob.ttsRetryAttempts, 1);
  assert.equal(timers.shift().delay, 1000);
  await hooks.generateJob(retryJob);
  assert.equal(retryJob.status, "queued");
  assert.equal(retryJob.ttsRetryAttempts, 2);
  assert.equal(timers.shift().delay, 2000);
  await hooks.generateJob(retryJob);
  assert.equal(retryJob.status, "generated");
  assert.equal(retryJob.ttsRetryAttempts, 0);
  assert.equal(generationAttempts, 3);

  const failedRecord = { key: "permanent", source: "manual", conversationId: "test", messageId: "permanent", originalText: "permanent", chunkCount: 1, jobs: [], state: "queued" };
  const failedJob = { answerRecord: failedRecord, messageId: "permanent", chunkIndex: 0, engineEpoch: 0, status: "queued", ttsRetryAttempts: 0, text: "permanent", voice: "kurisu", voiceSource: "character_tag" };
  failedRecord.jobs.push(failedJob);
  hooks.state.queue = [failedJob];
  hooks.setRequestTts(async () => {
    const error = new Error("invalid request");
    error.retryable = false;
    error.httpStatus = 404;
    throw error;
  });
  await hooks.generateJob(failedJob);
  assert.equal(failedJob.status, "failed");
  assert.equal(timers.length, 0);
  assert.equal(visibleErrors.length, 1);
  assert.match(visibleErrors[0], /区間1/);
  console.log(`Background TTS retry: 503 retries=2 (1000ms, 2000ms), then success; permanent 404 retries=0; scheduler finalizations=${schedulerCalls}`);
}

verifyBackgroundTtsPayloads().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
