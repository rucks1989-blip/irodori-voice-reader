"use strict";

const browserApi = typeof browser !== "undefined" ? browser : chrome;
const Processor = globalThis.IrodoriTextProcessor;
const VoiceReadiness = globalThis.IrodoriVoiceReadiness
  || (typeof require === "function" ? require("./voice-readiness.js") : null);
const ApiState = globalThis.IrodoriApiState;
const state = {
  queue: [],
  current: null,
  running: false,
  generationWorkers: 0,
  generationControllers: new Set(),
  playbackRunning: false,
  answerRecords: new Map(),
  answerOrder: [],
  answerSequence: 0,
  generatedKeys: new Set(),
  playStartedKeys: new Set(),
  playedKeys: new Set(),
  inFlight: new Map(),
  paused: false,
  audio: null,
  objectUrl: null,
  audioId: 0,
  audioTraceSequence: 0,
  lastAudioEndedAt: null,
  lastAudioEndedAnswerKey: null,
  playbackController: null,
  playbackLoopRunning: false,
  abortController: null,
  cancelPlayback: null,
  playSession: null,
  lastError: "",
  timingDebug: false,
  timingSessionId: "",
  timingLogFileName: "",
  timingLogPath: "",
  timingLogQueue: Promise.resolve(),
  timingLogWarned: false,
  apiMode: "managed",
  customApiUrl: "",
  engine: "standard",
  requestedEngine: null,
  engineSwitchSequence: 0,
  engineEpoch: 0,
  engineTransition: null,
  engineTransitionState: "idle",
  engineTransitionError: "",
  engineStarting: false,
  startupEpoch: 0,
  engineStatusRevision: 0,
  engineReady: false,
  voices: [],
  selectedVoice: "",
  voiceReady: false,
  voiceReadyPromise: null,
  voiceReadinessKey: "",
  voiceState: { status: "unknown", source: "none", error: "" },
  voiceRefreshSequence: 0,
  voiceRevision: 0,
  lastTimingSummary: null
};
const MAX_GENERATION_WORKERS = 2;
let initializationPromise = null;
let engineReadyPromise = null;
const NATIVE_HOST = globalThis.IrodoriInstallConfig.nativeHost;
const API_START_TIMEOUT_MS = 420000;
const API_START_POLL_INTERVAL_MS = 2000;
const SETTINGS_BACKUP_KEY = "irodoriSettingsBackup";
const SETTINGS_RECOVERY_KEY = "irodoriSettingsRecovery";
const VOICE_REFRESH_ATTEMPTS = 3;
const VOICE_REFRESH_DELAY_MS = 80;
const VOICE_RETRY_BASE_DELAY_MS = 250;
const MAX_TTS_RETRIES = 2;
const TTS_RETRY_BASE_DELAY_MS = 1000;
let settingsWriteQueue = Promise.resolve();
let apiStartupMonitor = null;

function settingsShapeIsValid(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (value.characterMappings !== undefined && !Array.isArray(value.characterMappings)) return false;
  if (value.dialoguePairs !== undefined && !Array.isArray(value.dialoguePairs)) return false;
  if (value.presets !== undefined && (!value.presets || typeof value.presets !== "object" || Array.isArray(value.presets))) return false;
  return true;
}

function persistSettingsPatch(patch, baseSnapshot = {}) {
  const cleanPatch = Processor.settingsPatchWithoutEngine(patch || {});
  const operation = settingsWriteQueue.then(async () => {
    const stored = await browserApi.storage.local.get(null);
    const current = Processor.mergedSettings(stored);
    if (!Object.keys(cleanPatch).length) return current;
    const candidate = Processor.mergedSettings(Processor.mergeSettingsPatch(current, cleanPatch, baseSnapshot));
    if (!settingsShapeIsValid(candidate)) throw new Error("設定データの形式が正しくありません。保存しませんでした。");
    const updates = Object.fromEntries(Object.keys(cleanPatch).map((key) => [key, candidate[key]]));
    await browserApi.storage.local.set({
      [SETTINGS_BACKUP_KEY]: { savedAt: new Date().toISOString(), settings: Processor.settingsExport(current) },
      ...updates,
      schemaVersion: Processor.DEFAULT_SETTINGS.schemaVersion,
      settingsVersion: Processor.DEFAULT_SETTINGS.settingsVersion
    });
    return Processor.mergedSettings(await browserApi.storage.local.get(null));
  });
  settingsWriteQueue = operation.catch(() => {});
  return operation;
}

async function settings() {
  await globalThis.IrodoriInstallReady;
  const config = Processor.mergedSettings(await browserApi.storage.local.get({ ...Processor.DEFAULT_SETTINGS, cachedVoices: [], voiceDisplayNames: {} }));
  state.timingDebug = Boolean(config.timingDebug);
  return config;
}

function timingNow() {
  return typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now();
}

function timingSessionId() {
  if (state.timingSessionId) return state.timingSessionId;
  const random = globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
    ? globalThis.crypto.randomUUID().replace(/-/g, "")
    : Math.random().toString(36).slice(2, 14);
  state.timingSessionId = `${new Date().toISOString().slice(0, 19).replace(/[^0-9]/g, "")}-${random}`;
  return state.timingSessionId;
}

function appendTimingRecord(record) {
  if (!state.timingDebug || !nativeMessagingAvailable() || !record || typeof record !== "object") return;
  state.timingLogQueue = state.timingLogQueue.then(async () => {
    if (!state.timingDebug) return;
    try {
      const result = await nativeControl("timing_log_append", {
        session_id: timingSessionId(),
        event: record
      });
      state.timingLogFileName = result.fileName || state.timingLogFileName;
      state.timingLogPath = result.path || state.timingLogPath;
      if (typeof browserApi.runtime.sendMessage === "function") {
        browserApi.runtime.sendMessage({
          type: "timing-log-updated",
          fileName: state.timingLogFileName,
          path: state.timingLogPath
        }).catch(() => {});
      }
    } catch (error) {
      if (!state.timingLogWarned) {
        state.timingLogWarned = true;
        console.warn("[Irodori] 計測ログ保存に失敗しました。読み上げは継続します。", error.message);
      }
    }
  }).catch((error) => {
    if (!state.timingLogWarned) {
      state.timingLogWarned = true;
      console.warn("[Irodori] 計測ログキューに失敗しました。読み上げは継続します。", error.message);
    }
  });
}

function timingLog(event, fields = {}) {
  if (!state.timingDebug) return;
  const record = {
    event,
    timestamp: new Date().toISOString(),
    perf_ms: Number(timingNow().toFixed(3)),
    ...fields
  };
  console.info("[Irodori][timing]", record);
  appendTimingRecord(record);
}

function voiceRoutingLog(stage, fields = {}) {
  // Timing logs must remain metadata-only.  Keep routing identifiers and
  // selected Voice, but never persist the user's response text.
  const safeFields = { ...fields };
  delete safeFields.raw_text;
  delete safeFields.text;
  timingLog("voice_route", { stage, ...safeFields });
}

if (browserApi.storage && browserApi.storage.onChanged && typeof browserApi.storage.onChanged.addListener === "function") {
  browserApi.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes && changes.timingDebug) {
      state.timingDebug = Boolean(changes.timingDebug.newValue);
    }
    if (area === "local" && changes) {
      const routingKeys = ["defaultVoice", "narratorVoice", "dialogueVoice", "characterMappings", "voiceDisplayNames", "cachedVoices", "characterTagPriority"];
      if (routingKeys.some((key) => changes[key] && JSON.stringify(changes[key].oldValue) !== JSON.stringify(changes[key].newValue))) {
        invalidatePendingVoiceJobs();
        if (changes.defaultVoice) invalidateVoiceReadiness("default-voice-changed");
        runSchedulers();
      }
    }
  });
}

function endpointForConfig(config) {
  return Processor.apiUrlForSettings(config || {});
}

function healthFailure(engine, endpoint, error) {
  const message = error?.message || String(error || "health確認に失敗しました");
  const result = ApiState.normalize(engine, { available: false, status: 0, error: message, endpoint });
  return {
    ...result,
    errorCode: /failed to fetch|networkerror|接続/i.test(message) ? "network_error" : "health_error",
    errorStage: "health",
    fetchUrl: `${apiBase(endpoint)}/health`
  };
}

async function engineHealthStatuses(config = null) {
  const current = config || await settings();
  if (current.apiMode !== "custom" && nativeMessagingAvailable()) {
    try {
      const nativeStatus = await nativeControl("status", { engine: current.engine });
      if (nativeStatus && nativeStatus.engines) {
        return Object.fromEntries(Object.entries(nativeStatus.engines).map(([engine, value]) => {
          const normalized = ApiState.normalize(engine, engine === current.engine && nativeStatus.startupError
            ? { ...value, error: nativeStatus.startupError } : value);
          normalized.startupActive = engine === current.engine && (nativeStatus.engineTransition || ["launching", "running"].includes(nativeStatus.startupState));
          return [engine, normalized];
        }));
      }
    } catch (error) {
      console.info("[Irodori] shared-engine-status-fallback", { reason: error?.message || String(error) });
    }
  }
  return Object.fromEntries(await Promise.all(Object.entries(Processor.ENGINE_ENDPOINTS).map(async ([engine, endpoint]) => {
    try {
      const healthUrl = `${apiBase(endpoint)}/health?engine=${encodeURIComponent(engine)}`;
      const response = await fetch(healthUrl, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      const failure = response.ok ? {} : {
        error: body.error || body.message || `HTTP ${response.status}`,
        errorCode: "health_error",
        errorStage: "health",
        fetchUrl: healthUrl
      };
      return [engine, ApiState.normalize(engine, { available: response.ok, status: response.status, health: body, endpoint, ...failure })];
    } catch (error) {
      return [engine, healthFailure(engine, endpoint, error)];
    }
  })));
}

function engineTransitionError() {
  const error = new Error("生成エンジンを切り替えています。接続済みになるまで読み上げを開始できません。");
  error.name = "AbortError";
  error.engineTransition = true;
  return error;
}

function nativeMessagingAvailable() {
  return typeof browserApi.runtime.sendNativeMessage === "function";
}

async function fetchVoices(config) {
  const endpoint = endpointForConfig(config);
  const response = await fetch(`${apiBase(endpoint)}/voices`, { cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `voice一覧取得エラー: ${response.status}`);
  return Array.isArray(body.voices) ? body.voices : [];
}

function invalidateVoiceReadiness(reason = "unspecified") {
  state.voiceRefreshSequence += 1;
  state.voiceReady = false;
  state.voiceReadyPromise = null;
  state.voiceReadinessKey = "";
  state.voiceState = { status: "pending", source: "none", reason, error: "" };
  console.info("[Irodori] voice-readiness-invalidated", { reason, sequence: state.voiceRefreshSequence });
}

function invalidatePendingVoiceJobs() {
  state.voiceRevision += 1;
  // Playing audio is deliberately outside this queue. Never stop it or clear
  // answer identities; only future synthesis/playback needs the new routing.
  for (const job of state.queue) {
    if (job.status !== "generated") continue;
    job.blob = null;
    job.status = "queued";
    job.nextAttemptAt = 0;
    job.answerRecord.state = "queued";
    state.generatedKeys.delete(`${job.answerRecord.source}|${job.answerRecord.conversationId}|${job.answerRecord.messageId}|${job.chunkIndex}`);
  }
}

function voiceReadinessKeyFor(config) {
  return [
    state.engineEpoch,
    config && config.apiMode,
    endpointForConfig(config),
    String(config && config.defaultVoice || "")
  ].join("|");
}

async function ensureVoiceReady(config = null, options = {}) {
  const current = config || await settings();
  const key = voiceReadinessKeyFor(current);
  if (state.engineTransition && !options.allowTransition) {
    throw VoiceReadiness.transientError("生成エンジン切替中のためVoiceを確認できません。", {
      stage: "engine-transition"
    });
  }
  if (!options.force && state.voiceReady && state.voiceReadinessKey === key) {
    return {
      voices: state.voices,
      freshVoices: state.voiceState.fresh ? state.voices : [],
      selectedVoice: state.selectedVoice,
      source: state.voiceState.source,
      stale: Boolean(state.voiceState.stale)
    };
  }
  if (state.voiceReadyPromise && !options.force) return state.voiceReadyPromise;
  const sequence = ++state.voiceRefreshSequence;
  const engineEpoch = state.engineEpoch;
  console.info("[Irodori] voice-readiness-refresh-start", { sequence, engine_epoch: engineEpoch, reason: options.reason || "ensure", endpoint: endpointForConfig(current) });
  const promise = (async () => {
    state.voiceReady = false;
    state.voiceState = { status: "refreshing", source: "none", reason: options.reason || "ensure", error: "" };
    const cached = state.voices.length ? state.voices : (Array.isArray(current.cachedVoices) ? current.cachedVoices : []);
    let refreshed;
    try {
      refreshed = await VoiceReadiness.refresh(
        () => fetchVoices(current),
        cached,
        {
          attempts: VOICE_REFRESH_ATTEMPTS,
          delayMs: VOICE_REFRESH_DELAY_MS,
          readyCheck: async () => {
            const response = await fetch(`${apiBase(endpointForConfig(current))}/health`, { cache: "no-store" });
            return response.ok;
          }
        }
      );
    } catch (error) {
      if (sequence !== state.voiceRefreshSequence || state.engineEpoch !== engineEpoch) {
        console.info("[Irodori] voice-readiness-refresh-stale", { sequence, current_sequence: state.voiceRefreshSequence, engine_epoch: engineEpoch, current_engine_epoch: state.engineEpoch });
        return { cancelled: true, voices: state.voices, freshVoices: [], source: "stale" };
      }
      state.voiceState = { status: error.voiceResolutionPermanent ? "empty" : "error", source: "none", reason: options.reason || "ensure", error: error.message };
      state.voiceReady = false;
      if (error.voiceResolutionPermanent) {
        state.voices = [];
        state.selectedVoice = "";
        await browserApi.storage.local.set({ cachedVoices: [] });
      }
      throw error;
    }
    if (sequence !== state.voiceRefreshSequence || state.engineEpoch !== engineEpoch) {
      console.info("[Irodori] voice-readiness-refresh-stale", { sequence, current_sequence: state.voiceRefreshSequence, engine_epoch: engineEpoch, current_engine_epoch: state.engineEpoch });
      return { cancelled: true, voices: state.voices, freshVoices: [], source: "stale" };
    }
    const latestConfig = await settings();
    if (!options.allowTransition && voiceReadinessKeyFor(latestConfig) !== key) {
      console.info("[Irodori] voice-readiness-settings-stale", { sequence, endpoint: endpointForConfig(current), latest_endpoint: endpointForConfig(latestConfig) });
      return { cancelled: true, voices: state.voices, freshVoices: [], source: "stale" };
    }
    const voices = VoiceReadiness.normalizeVoices(refreshed.voices);
    const freshVoices = refreshed.fresh ? voices : [];
    const selected = VoiceReadiness.resolveVoice({
      requestedVoice: current.defaultVoice,
      currentVoice: state.selectedVoice || current.defaultVoice,
      defaultVoice: current.defaultVoice,
      voices: freshVoices,
      cachedVoices: voices
    });
    if (!selected.ok) {
      const error = refreshed.transient
        ? VoiceReadiness.transientError("Voice一覧が一時的に利用できません。queueを保持して再確認します。", { stage: "voice-resolution" })
        : VoiceReadiness.unavailableError("利用可能なVoiceが0件のため読み上げを開始できません。", { stage: "voice-resolution" });
      state.voiceState = { status: error.voiceResolutionPermanent ? "empty" : "error", source: refreshed.source, error: error.message };
      state.voiceReady = false;
      throw error;
    }
    state.voices = voices;
    state.selectedVoice = selected.voiceId;
    // A cached list keeps the current selection visible, but it is not a
    // readiness condition: the API's current /voices response is still
    // required before a TTS request may start.  Keeping voiceReady=false
    // makes the next queued retry perform a real refresh instead of reusing
    // the same stale cache forever.
    state.voiceReady = Boolean(refreshed.fresh);
    state.voiceReadinessKey = refreshed.fresh ? key : "";
    state.voiceState = {
      status: refreshed.fresh ? "ready" : "stale",
      source: refreshed.source,
      fresh: Boolean(refreshed.fresh),
      stale: Boolean(refreshed.stale),
      selectedVoice: selected.voiceId,
      selectedRole: selected.role,
      attempts: refreshed.attempts,
      error: refreshed.error ? String(refreshed.error.message || refreshed.error) : ""
    };
    const updates = { cachedVoices: voices };
    await browserApi.storage.local.set(updates).catch(() => {});
    return { voices, freshVoices, selectedVoice: selected.voiceId, source: refreshed.source, stale: Boolean(refreshed.stale) };
  })();
  state.voiceReadyPromise = promise;
  promise.then(
    () => { if (state.voiceReadyPromise === promise) state.voiceReadyPromise = null; },
    () => { if (state.voiceReadyPromise === promise) state.voiceReadyPromise = null; }
  );
  return promise;
}

async function ensureJobVoiceReady(job) {
  if (!VoiceReadiness) throw new Error("Voice解決モジュールを読み込めません。");
  if (job.engineEpoch !== state.engineEpoch || state.engineTransition || state.engineStarting) {
    throw VoiceReadiness.transientError("生成エンジンまたはVoice一覧の切替中です。", { stage: "engine-transition" });
  }
  let config = await settings();
  const ready = await ensureVoiceReady(config);
  if (ready.cancelled) throw VoiceReadiness.transientError("Voice一覧の更新がengine切替で無効になりました。", { stage: "stale-voice-refresh" });
  if (!ready.freshVoices.length) throw VoiceReadiness.transientError("最新のVoice一覧を取得するまで読み上げを待機します。", { stage: "voice-list-refresh" });
  config = await settings();
  if (job.voiceRevision !== state.voiceRevision) {
    const previous = job.voiceRoute || {};
    const route = VoiceReadiness.routeForSegment({ ...job, voice: "", voiceRoute: null,
      dialogue: previous.dialogue, narration: previous.narration }, config);
    job.voice = route.voice;
    job.requestedVoice = route.voice;
    job.voiceRoute = route;
    job.voiceRevision = state.voiceRevision;
  }
  const resolution = VoiceReadiness.resolveJobVoice(
    job,
    config,
    ready.freshVoices,
    ready.voices,
    state.selectedVoice || config.defaultVoice
  );
  if (!resolution.ok) {
    throw VoiceReadiness.unavailableError("指定Voice・現在Voice・デフォルトVoiceを解決できません。", {
      stage: "job-voice-resolution",
      requestedVoice: resolution.requestedVoice
    });
  }
  job.requestedVoice = resolution.requestedVoice;
  job.voice = resolution.voiceId;
  job.voiceSource = resolution.voiceSource || job.voiceSource || "default";
  job.voiceRoute = {
    ...(resolution.route || job.voiceRoute || {}),
    voice: resolution.voiceId,
    source: job.voiceSource,
    tag: resolution.tag || "",
    mappedVoice: resolution.mappedVoice || "",
    fallbackVoice: resolution.fallbackVoice || (job.voiceRoute && job.voiceRoute.fallbackVoice) || "",
    dialogue: Boolean(resolution.route && resolution.route.dialogue),
    narration: Boolean(resolution.route && resolution.route.narration),
    locked: true
  };
  job.voiceResolution = {
    source: resolution.source,
    voiceSource: job.voiceSource,
    role: resolution.role,
    stale: Boolean(resolution.stale),
    tag: resolution.tag || ""
  };
  voiceRoutingLog("resolved", {
    message_id: job.messageId,
    raw_text: job.answerRecord && job.answerRecord.originalText,
    text: job.text,
    character_tag: job.voiceRoute.tag,
    mapped_voice: job.voiceRoute.mappedVoice,
    tag_resolution: job.voiceRoute.tagResolution || "",
    dialogue: Boolean(job.voiceRoute.dialogue),
    narration: Boolean(job.voiceRoute.narration),
    resolved_voice: job.voice,
    voice_source: job.voiceSource,
    readiness_source: resolution.source,
    selected_voice: state.selectedVoice || ""
  });
  return resolution;
}

function requireFreshVoiceReady(result) {
  if (!result || result.cancelled || !Array.isArray(result.freshVoices) || !result.freshVoices.length) {
    throw VoiceReadiness.transientError("最新のVoice一覧を取得するまでAPIをready扱いにしません。", { stage: "voice-list-refresh" });
  }
  return result;
}

async function waitForFreshVoiceReady(config, options = {}) {
  const attempts = Math.max(1, Number(options.attempts) || 4);
  const delayMs = Math.max(0, Number(options.delayMs) || VOICE_RETRY_BASE_DELAY_MS);
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const result = await ensureVoiceReady(config, { ...options, force: true });
      if (result.cancelled) return result;
      return requireFreshVoiceReady(result);
    } catch (error) {
      if (error && error.voiceResolutionPermanent) throw error;
      lastError = error;
      if (attempt < attempts) await startupDelay(delayMs);
    }
  }
  throw lastError || VoiceReadiness.transientError("最新のVoice一覧を取得できません。", { stage: "voice-list-refresh" });
}

function scheduleVoiceRetry(job, error) {
  job.voiceResolutionAttempts = Number(job.voiceResolutionAttempts || 0) + 1;
  const delay = Math.min(4000, VOICE_RETRY_BASE_DELAY_MS * (2 ** Math.min(job.voiceResolutionAttempts - 1, 4)));
  job.nextAttemptAt = Date.now() + delay;
  job.voiceResolutionError = String(error && error.message || error || "Voice準備待ち");
  setTimeout(() => {
    if (state.queue.includes(job) && job.status === "queued") runSchedulers();
  }, delay);
  console.info("[Irodori] voice-resolution-retry", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    delay_ms: delay,
    reason: job.voiceResolutionError
  });
}

function skipVoiceJobAsFinalInsurance(job, error) {
  const record = job.answerRecord;
  job.status = "skipped";
  job.skipReason = String(error && error.message || "利用可能なVoiceが0件です。");
  job.voiceResolutionPermanent = true;
  if (record) {
    record.nextPlayIndex = Math.max(record.nextPlayIndex, job.chunkIndex + 1);
    record.state = "queued";
    if (record.planningDone && record.nextPlayIndex >= record.jobs.length) completeAnswer(record);
  }
  console.warn("[Irodori] voice-resolution-final-skip", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    reason: job.skipReason
  });
}

async function fetchManagedVoiceNames(config = null) {
  // An external Irodori-compatible API is self-contained: its /voices
  // response is the source of truth and must not require Native Messaging.
  if (config && config.apiMode === "custom") return {};
  if (!nativeMessagingAvailable()) return {};
  try {
    const result = await nativeControl("voice_state");
    return Object.fromEntries((result.voices || [])
      .filter((voice) => voice && voice.id && voice.name)
      .map((voice) => [String(voice.id), String(voice.name)]));
  } catch (_) {
    return {};
  }
}

function mergeVoiceDisplayNames(voices, managedNames) {
  return (Array.isArray(voices) ? voices : []).map((voice) => {
    const id = String(voice && voice.id || "");
    const managedName = managedNames && managedNames[id];
    return managedName ? { ...voice, name: managedName } : voice;
  });
}

async function initializeSettings() {
  await globalThis.IrodoriInstallReady;
  let stored = await browserApi.storage.local.get(null);
  if (!settingsShapeIsValid(stored)) {
    const backup = stored[SETTINGS_BACKUP_KEY];
    if (backup && settingsShapeIsValid(backup.settings)) {
      stored = { ...stored, ...backup.settings };
      await browserApi.storage.local.set({ ...backup.settings, [SETTINGS_RECOVERY_KEY]: {
        recoveredAt: new Date().toISOString(), reason: "invalid-settings-shape"
      } });
    } else {
      await browserApi.storage.local.set({ [SETTINGS_RECOVERY_KEY]: {
        detectedAt: new Date().toISOString(), reason: "invalid-settings-no-backup"
      } });
    }
  }
  const updates = {};
  Object.assign(updates, Processor.migrateSettings(stored));
  // Legacy apiUrl values are ignored; mergedSettings derives the endpoint
  // from engine so initialization never writes a second, stale source of truth.
  if ((stored.settingsVersion || 0) < Processor.DEFAULT_SETTINGS.settingsVersion) {
    updates.settingsVersion = Processor.DEFAULT_SETTINGS.settingsVersion;
  }
  for (const [key, value] of Object.entries(Processor.DEFAULT_SETTINGS)) {
    if (stored[key] === undefined && updates[key] === undefined) updates[key] = value;
  }
  // Builds before the custom mode only persisted apiUrl, whose value was
  // derived from engine.  Keep those profiles in managed mode; only an
  // explicit customApiUrl/apiMode pair is migrated to the external mode.
  if (stored.apiMode === undefined && stored.customApiUrl) updates.apiMode = "custom";
  if (String(stored.apiMode || updates.apiMode || "managed").toLowerCase() === "custom" &&
             stored.customApiUrl === undefined && stored.apiUrl) {
    updates.customApiUrl = String(stored.apiUrl).trim();
  }
  const config = Processor.mergedSettings({ ...stored, ...updates });
  let availableVoices = Array.isArray(stored.cachedVoices) ? stored.cachedVoices : [];
  try {
    availableVoices = await fetchVoices(config);
    const managedNames = await fetchManagedVoiceNames(config);
    availableVoices = mergeVoiceDisplayNames(availableVoices, managedNames);
    updates.cachedVoices = availableVoices;
    if (Object.keys(managedNames).length) {
      updates.voiceDisplayNames = { ...(stored.voiceDisplayNames || {}), ...managedNames };
    }
  } catch (_) {
    // API may legitimately be stopped when Firefox starts. Cached voices remain usable.
  }
  const ids = availableVoices.map((voice) => voice.id);
  if (!stored.defaultVoice || (ids.length && !ids.includes(stored.defaultVoice))) {
    updates.defaultVoice = ids.includes("yui") ? "yui" : (ids[0] || config.defaultVoice || "yui");
  }
  if (Object.keys(updates).length) await browserApi.storage.local.set(updates);
  const merged = Processor.mergedSettings({ ...stored, ...updates });
  state.apiMode = merged.apiMode;
  state.customApiUrl = merged.customApiUrl;
  state.engine = merged.engine;
  state.voices = VoiceReadiness ? VoiceReadiness.normalizeVoices(availableVoices) : availableVoices;
  state.selectedVoice = String(merged.defaultVoice || "");
  state.timingDebug = Boolean(merged.timingDebug);
  return { settings: merged, voices: availableVoices };
}

function ensureInitialized() {
  if (!initializationPromise) initializationPromise = initializeSettings().then(async (result) => {
    await loadCompletedAnswers();
    return result;
  }).catch((error) => {
    initializationPromise = null;
    throw error;
  });
  return initializationPromise;
}

function startupTokenIsCurrent(epoch, engine) {
  return epoch === state.startupEpoch && (!state.requestedEngine || state.requestedEngine === engine);
}

function invalidateEngineStartup(reason, requestedEngine = null) {
  const previousEpoch = state.startupEpoch;
  state.startupEpoch += 1;
  if (apiStartupMonitor) {
    apiStartupMonitor.cancelled = true;
    apiStartupMonitor = null;
  }
  // The promise itself cannot be force-cancelled, but removing it from the
  // shared slot prevents a new switch from waiting for an obsolete startup.
  engineReadyPromise = null;
  state.requestedEngine = requestedEngine;
  state.engineStarting = false;
  console.info("[Irodori] engine-startup-invalidated", {
    reason,
    previous_epoch: previousEpoch,
    startup_epoch: state.startupEpoch,
    requested_engine: requestedEngine
  });
  return state.startupEpoch;
}

function ensureEngineReady() {
  if (engineReadyPromise) return engineReadyPromise;
  const promise = (async () => {
    // Capture the startup generation before initialization can yield.  The
    // initial settings snapshot may be stale when a user changes engines
    // while voice discovery is still in progress.
    const startupEpoch = state.startupEpoch;
    const initialized = await ensureInitialized();
    const config = await settings();
    if (!startupTokenIsCurrent(startupEpoch, config.engine)) return { ...initialized, cancelled: true };
    state.engine = config.engine;
    state.apiMode = config.apiMode;
    state.customApiUrl = config.customApiUrl;
    if (config.apiMode === "custom") {
      // Custom mode never asks Native Messaging to start/stop/switch an
      // engine.  It only verifies the user-supplied compatible API endpoint.
      const endpoint = endpointForConfig(config);
      const response = await fetch(`${apiBase(endpoint)}/health`, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `既存Irodori-TTS APIエラー: ${response.status}`);
      const currentConfig = await settings();
      if (!startupTokenIsCurrent(startupEpoch, config.engine) ||
          currentConfig.apiMode !== "custom" || currentConfig.customApiUrl !== config.customApiUrl) {
        return { ...initialized, cancelled: true };
      }
      const voiceResult = await waitForFreshVoiceReady(currentConfig, { allowTransition: true, reason: "custom-api-ready" });
      if (voiceResult.cancelled) return { ...initialized, cancelled: true };
      const voiceReady = requireFreshVoiceReady(voiceResult);
      state.engineReady = true;
      state.engineStarting = false;
      state.lastError = "";
      state.engineStatusRevision += 1;
      await publishStatus();
      return { ...initialized, settings: currentConfig, voices: voiceReady.voices };
    }
    if (!nativeMessagingAvailable()) {
      // Unit/test environments without Native Messaging keep the historical
      // behavior; Firefox itself always has the host permission available.
      state.engineReady = true;
      return initialized;
    }
    const status = await nativeControl("status", { engine: config.engine });
    if (!startupTokenIsCurrent(startupEpoch, config.engine)) return { ...initialized, cancelled: true };
    console.info("[Irodori] engine-startup-status", {
      storage_engine: config.engine,
      derived_apiUrl: Processor.apiUrlForEngine(config.engine),
      current_engine: status.currentEngine,
      selected_running: Boolean(status.running),
      backend_running: Boolean(status.backendRunning)
    });
    if (status.running && status.currentEngine === config.engine) {
      const voiceResult = await waitForFreshVoiceReady(config, { allowTransition: true, reason: "engine-already-running" });
      // A switch may invalidate the startup refresh while this promise is
      // still pending.  That is a cancellation, not a missing Voice.
      if (voiceResult.cancelled) return { ...initialized, cancelled: true };
      const voiceReady = requireFreshVoiceReady(voiceResult);
      state.engineReady = true;
      state.lastError = "";
      state.engineStatusRevision += 1;
      await publishStatus();
      return { ...initialized, voices: voiceReady.voices };
    }
    state.engineReady = false;
    const startResponse = await startApiNonBlocking({ engine: config.engine, reason: "extension_initialize", startupEpoch });
    if (startResponse.cancelled) return { ...initialized, cancelled: true };
    const monitor = apiStartupMonitor;
    if (!startResponse.running && monitor && monitor.epoch === startupEpoch) {
      const ready = await monitor.promise;
      if (ready && ready.cancelled) return { ...initialized, cancelled: true };
      if (!ready || !ready.running) {
        throw new Error("保存済みの生成エンジンが接続済みになりませんでした。");
      }
    }
    return { ...initialized, settings: await settings() };
  })();
  engineReadyPromise = promise;
  promise.then(
    () => { if (engineReadyPromise === promise) engineReadyPromise = null; },
    () => { if (engineReadyPromise === promise) engineReadyPromise = null; }
  );
  return promise;
}

function nativeEngineFullyReady(status, engine) {
  const raw = status && status.engines && status.engines[engine];
  return raw
    ? Boolean(raw.running && raw.api && raw.api.running && raw.backend && raw.backend.running)
    : Boolean(status && status.running && status.backendRunning && status.currentEngine === engine);
}

function nativeEngineFullyStopped(status, engine) {
  // normal/safe are logical modes of one shared API; switching must not wait
  // for an "opposite" process to disappear.
  return true;
}

async function nativeControl(command, payload = {}) {
  let response;
  try {
    response = await globalThis.IrodoriFirefoxNativeMessage(NATIVE_HOST, { command, ...payload });
  } catch (error) {
    throw new Error(`Irodoriローカルヘルパーに接続できません。導入ツールを実行してください。（${error.message}）`);
  }
  if (!response || response.ok === false) {
    throw new Error((response && response.error) || "ローカルヘルパーから応答がありません。");
  }
  return response;
}

async function refreshVoiceCatalog(reason = "manual-voice-refresh") {
  const config = await settings();
  invalidateVoiceReadiness(reason);
  try {
    const ready = await ensureVoiceReady(config, { force: true, reason });
    const managedNames = await fetchManagedVoiceNames(config);
    const voices = mergeVoiceDisplayNames(ready.voices, managedNames);
    const updates = { cachedVoices: voices };
    if (Object.keys(managedNames).length) {
      updates.voiceDisplayNames = { ...(config.voiceDisplayNames || {}), ...managedNames };
    }
    await browserApi.storage.local.set(updates);
    state.voices = voices;
    return { voices, source: ready.source, stale: Boolean(ready.stale) };
  } catch (error) {
    if (error.voiceResolutionPermanent) return { voices: [], source: "api", stale: false };
    const cached = Array.isArray(config.cachedVoices) ? config.cachedVoices : [];
    if (cached.length) return { voices: cached, source: "cache", warning: error.message };
    throw new Error(`Irodori APIに接続できません。${endpointForConfig(config)} を起動してから再接続してください。`);
  }
}

function datasetSource(source) {
  if (source === "chatgpt") return "chatgpt_auto";
  if (source === "gemini") return "gemini_auto";
  if (source === "muse") return "muse_auto";
  if (source === "selection") return "selection";
  if (source === "manual_retry") return "manual_retry";
  return String(source || "manual");
}

function datasetRequestId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") return globalThis.crypto.randomUUID();
  return `request-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function datasetVoiceInfo(config, voiceId) {
  const id = String(voiceId || "");
  const displayNames = config && config.voiceDisplayNames && typeof config.voiceDisplayNames === "object"
    ? config.voiceDisplayNames : {};
  const values = Array.isArray(config.cachedVoices) ? config.cachedVoices : [];
  const match = values.find((voice) => voice && String(voice.id) === id);
  return {
    id,
    // Voice-management names are authoritative. API /voices is the fallback
    // for custom APIs or hosts that cannot expose voice_state.
    name: String(displayNames[id] || (match && match.name) || id),
    type: String((match && (match.type || match.voice_type || match.reference_mode)) || "unknown")
  };
}

async function resolveDatasetVoiceInfo(config, voiceId) {
  const initial = datasetVoiceInfo(config, voiceId);
  if (initial.name !== initial.id || !nativeMessagingAvailable()) return initial;
  // A temporary add-on/profile can have cached /voices entries but no
  // voiceDisplayNames yet.  Refresh the management source at capture time so
  // metadata does not silently fall back to the internal ID.
  const managedNames = await fetchManagedVoiceNames(config);
  const managedName = managedNames[initial.id];
  if (!managedName) return initial;
  const merged = { ...(config.voiceDisplayNames || {}), [initial.id]: managedName };
  await browserApi.storage.local.set({ voiceDisplayNames: merged }).catch(() => {});
  return { ...initial, id: initial.id, name: managedName };
}

function datasetCharacterForJob(job, config, resolvedVoice = null) {
  const tag = String(job.tag || "").trim().replace(/^【|】$/g, "");
  if (tag) return { character: tag, tag: `【${tag}】` };
  const voice = resolvedVoice || datasetVoiceInfo(config, job.voice);
  return { character: voice.name || voice.id || "_Untagged_", tag: "" };
}

function datasetWavIsValid(blob) {
  if (!blob || typeof blob.size !== "number" || blob.size < 44 || typeof blob.slice !== "function") return Promise.resolve(false);
  return blob.slice(0, 12).arrayBuffer().then((buffer) => {
    const header = new Uint8Array(buffer);
    return header.length >= 12 && String.fromCharCode(...header.slice(0, 4)) === "RIFF" && String.fromCharCode(...header.slice(8, 12)) === "WAVE";
  }).catch(() => false);
}

function bytesToBase64(bytes) {
  let binary = "";
  const blockSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += blockSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + blockSize, bytes.length)));
  }
  return btoa(binary);
}

async function validateDatasetDirectory(directory) {
  const value = String(directory || "").trim();
  if (!value) {
    const error = new Error("保存先フォルダーを指定してください。");
    error.datasetDirectory = true;
    throw error;
  }
  if (!nativeMessagingAvailable()) {
    const error = new Error("保存先フォルダーを確認するにはNative Messaging Hostが必要です。");
    error.datasetDirectory = true;
    throw error;
  }
  const result = await nativeControl("validate_dataset_directory", { base_dir: value });
  if (!result.valid) {
    const error = new Error(result.error || "保存先フォルダーが存在しません。");
    error.datasetDirectory = true;
    throw error;
  }
  return result;
}

async function importSettings(settingsValue) {
  if (!settingsShapeIsValid(settingsValue)) throw new Error("インポートする設定JSONの形式が正しくありません。");
  const before = await settings();
  const imported = { ...settingsValue };
  if (Array.isArray(imported.voiceDefinitions)) {
    imported.voiceDisplayNames = {
      ...(imported.voiceDisplayNames || {}),
      ...Object.fromEntries(imported.voiceDefinitions
        .filter((voice) => voice && voice.id)
        .map((voice) => [String(voice.id), String(voice.name || voice.id)]))
    };
    delete imported.voiceDefinitions;
  }
  const migrated = { ...imported, ...Processor.migrateSettings(imported) };
  const requestedEngine = Processor.normalizeEngine(migrated.engine, before.engine);
  delete migrated.engine;
  delete migrated.apiUrl;
  await persistSettingsPatch(migrated, before);
  if (before.apiMode !== "custom" && requestedEngine !== before.engine) {
    await switchEngine(requestedEngine);
  }
  return settings();
}

async function captureTtsResult(job, blob) {
  const config = await settings();
  if (!config.wavSaveEnabled || !String(config.wavSaveDirectory || "").trim()) return { skipped: true };
  if (job.engineEpoch !== state.engineEpoch || state.engineTransition || state.engineStarting) return { skipped: true, stale: true };
  if (!(await datasetWavIsValid(blob))) throw new Error("生成WAVが正常ではないため保存しませんでした。");
  await validateDatasetDirectory(config.wavSaveDirectory);
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const voice = await resolveDatasetVoiceInfo(config, job.voice);
  const character = datasetCharacterForJob(job, config, voice);
  const record = job.answerRecord;
  const payload = {
    command: "save_tts_dataset",
    base_dir: String(config.wavSaveDirectory).trim(),
    request_id: record.captureRequestId,
    character: character.character,
    metadata: {
      character: character.character,
      tag: character.tag,
      voice_id: voice.id,
      voice_name: voice.name,
      engine: config.apiMode === "custom" ? "custom" : config.engine,
      engine_mode: config.apiMode === "custom" ? "custom" : (config.engine === "voice_stable" ? "stable" : "normal"),
      reference_mode: voice.type,
      source: datasetSource(job.source),
      source_url: record.sourceUrl || "",
      created_at: record.captureCreatedAt,
      timestamp: record.captureCreatedAt,
      original_text: record.originalText,
      raw_text: record.originalText,
      cleaned_text: record.cleanedText || record.originalText,
      request_id: record.captureRequestId,
      session_id: record.conversationId || "manual"
    },
    segment: { index: job.chunkIndex + 1, chunk_index: job.chunkIndex + 1, text: job.text, wav_base64: bytesToBase64(bytes) }
  };
  const result = await nativeControl("save_tts_dataset", payload);
  console.info("[Irodori] dataset-saved", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    request_id: record.captureRequestId,
    character: character.character,
    segment_count: result.segmentCount
  });
  return result;
}

function captureTtsResultDetached(job, blob) {
  if (job.captureStarted) return;
  job.captureStarted = true;
  const startedAt = timingNow();
  timingLog("save_start", { message_id: job.messageId, chunk_index: job.chunkIndex + 1 });
  captureTtsResult(job, blob).then(() => {
    timingLog("save_complete", {
      message_id: job.messageId,
      chunk_index: job.chunkIndex + 1,
      elapsed_ms: Number((timingNow() - startedAt).toFixed(1))
    });
  }).catch(async (error) => {
    timingLog("save_error", {
      message_id: job.messageId,
      chunk_index: job.chunkIndex + 1,
      elapsed_ms: Number((timingNow() - startedAt).toFixed(1)),
      error: error.message
    });
    if (job.answerRecord.captureWarningShown) return;
    job.answerRecord.captureWarningShown = true;
    if (error.datasetDirectory) {
      await browserApi.storage.local.set({ wavSaveEnabled: false }).catch(() => {});
    }
    await visibleError(`WAV保存に失敗しました。${error.message}`, job.tabId).catch(() => {});
  });
}

function startupDelay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function monitorApiStartup(startResponse, engine = "standard", startupEpoch = state.startupEpoch, options = {}) {
  const timeoutMs = Math.max(1000, Number(startResponse.startupTimeoutMs) || API_START_TIMEOUT_MS);
  const pollIntervalMs = Math.max(250, Number(startResponse.pollIntervalMs) || API_START_POLL_INTERVAL_MS);
  const requestToken = String(startResponse.requestToken || "");
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (!startupTokenIsCurrent(startupEpoch, engine)) return { cancelled: true, engine };
    await startupDelay(pollIntervalMs);
    try {
      if (!startupTokenIsCurrent(startupEpoch, engine)) return { cancelled: true, engine };
      const status = await nativeControl("status", { engine });
      if (requestToken && status.requestToken && status.requestToken !== requestToken) return { cancelled: true, engine };
      if (status.startupError) {
        console.error("[Irodori] startup-failed", {
          engine,
          startupPid: status.startupPid,
          startupState: status.startupState,
          startupExitCode: status.startupExitCode,
          startupOutput: status.startupOutput
        });
        const startupError = new Error(status.startupError);
        startupError.startupFailure = true;
        throw startupError;
      }
      if (nativeEngineFullyReady(status, engine)
          && status.currentEngine === engine && !status.engineTransition) {
        console.info("[Irodori] startup-health", {
          engine,
          endpoint: Processor.apiUrlForEngine(engine),
          current_engine: status.engine,
          running: true
        });
        if (options.deferVoiceReadiness) return { ...status, voiceReady: false, voiceReadinessDeferred: true };
        const readyConfig = options.config || await settings();
        const voiceResult = await waitForFreshVoiceReady(readyConfig, { allowTransition: true, reason: "api-startup-complete" });
        if (voiceResult.cancelled) return { cancelled: true, engine };
        const voiceReady = requireFreshVoiceReady(voiceResult);
        state.lastError = "";
        await publishStatus();
        return { ...status, voiceReady: true, voices: voiceReady.voices };
      }
      lastError = null;
    } catch (error) {
      if (error && error.startupFailure) throw error;
      lastError = error;
    }
  }
  const detail = lastError ? `（${lastError.message}）` : "";
  throw new Error(`Irodori APIの起動が${Math.round(timeoutMs / 1000)}秒以内に完了しませんでした。${detail}`);
}

function attachApiStartupMonitor(startResponse, engine, startupEpoch, options = {}) {
  const monitor = {
    engine,
    epoch: startupEpoch,
    cancelled: false,
    promise: null
  };
  state.engineStarting = true;
  state.engineReady = false;
  state.paused = true;
  state.engineStatusRevision += 1;
  const promise = monitorApiStartup(startResponse, engine, startupEpoch, options);
  monitor.promise = promise.then(async (status) => {
    if (monitor.cancelled || !startupTokenIsCurrent(startupEpoch, engine)) return { cancelled: true, engine };
    if (options.deferReady) return status;
    state.engineStarting = false;
    state.engineReady = true;
    state.engineStatusRevision += 1;
    state.lastError = "";
    await publishStatus();
    if (!state.engineTransition) {
      state.paused = false;
      runSchedulers();
    }
    return status;
  }).catch(async (error) => {
    if (monitor.cancelled || !startupTokenIsCurrent(startupEpoch, engine)) return { cancelled: true, engine };
    state.engineStarting = false;
    state.engineReady = false;
    state.engineStatusRevision += 1;
    state.lastError = error.message;
    // A user-triggered switch reports the contextual error in switchEngine().
    // Avoid emitting a second generic notification from the shared monitor.
    if (!state.engineTransition) {
      await visibleError(`Irodori APIの自動起動に失敗しました。${error.message}`);
    }
    throw error;
  }).finally(() => {
    if (apiStartupMonitor === monitor) apiStartupMonitor = null;
  });
  apiStartupMonitor = monitor;
  return monitor;
}

async function startApiNonBlocking(payload = {}) {
  const config = await settings();
  if (config.apiMode === "custom") throw new Error("既存Irodori-TTS API使用中はAPIを自動起動できません。指定URLへ接続してください。");
  const engine = payload.engine || config.engine;
  const command = payload.async === false ? "start" : "start_engine_async";
  const startupEpoch = Number.isInteger(payload.startupEpoch) ? payload.startupEpoch : state.startupEpoch;
  if (!startupTokenIsCurrent(startupEpoch, engine)) return { ok: true, cancelled: true, engine, starting: false };
  let response;
  try {
    response = await nativeControl(command, { ...payload, engine });
  } catch (error) {
    if (!startupTokenIsCurrent(startupEpoch, engine)) return { ok: true, cancelled: true, engine, starting: false };
    throw error;
  }
  if (!startupTokenIsCurrent(startupEpoch, engine)) return { ...response, cancelled: true, engine };
  if (response.running) {
    state.engine = engine;
    const readyConfig = await settings();
    const voiceResult = await waitForFreshVoiceReady(readyConfig, { allowTransition: true, reason: "api-start-response" });
    if (voiceResult.cancelled) return { ...response, cancelled: true, engine };
    requireFreshVoiceReady(voiceResult);
    state.engineReady = true;
    state.lastError = "";
    await publishStatus();
    return response;
  }
  state.engine = engine;
  if (!apiStartupMonitor) attachApiStartupMonitor(response, engine, startupEpoch);
  await publishStatus();
  return response;
}

function publicStatus() {
  const active = state.queue.filter((job) => !["completed", "failed", "skipped"].includes(job.status));
  const generating = state.queue.find((job) => job.status === "generating") || null;
  const statusConfig = { engine: state.engine, apiMode: state.apiMode, customApiUrl: state.customApiUrl };
  return {
    queued: active.length,
    current: state.current ? {
      text: state.current.text,
      voice: state.current.voice,
      voiceSource: state.current.voiceSource || state.current.voiceResolution?.voiceSource || "default",
      source: state.current.source,
      messageId: state.current.messageId,
      chunkIndex: state.current.chunkIndex + 1,
      chunkCount: state.current.answerRecord.chunkCount
    } : null,
    generating: generating ? {
      voice: generating.voice,
      voiceSource: generating.voiceSource || generating.voiceResolution?.voiceSource || "default",
      messageId: generating.messageId,
      chunkIndex: generating.chunkIndex + 1,
      chunkCount: generating.answerRecord.chunkCount
    } : null,
    pipelineState: state.current ? (generating ? "playing_and_generating" : "playing") : (generating ? "generating" : (state.paused ? "paused" : "idle")),
    lastTimingSummary: state.lastTimingSummary,
    running: state.running,
    paused: state.paused,
    apiMode: state.apiMode,
    customApiUrl: state.customApiUrl,
    engine: state.requestedEngine || state.engine,
    storageEngine: state.engine,
    requestedEngine: state.requestedEngine,
    endpoint: state.apiMode === "custom"
      ? Processor.apiUrlForSettings(statusConfig)
      : Processor.apiUrlForEngine(state.requestedEngine || state.engine),
    engineTransition: Boolean(state.engineTransition) || state.engineTransitionState === "switching",
    engineTransitionState: state.engineTransitionState,
    engineTransitionError: state.engineTransitionError,
    engineStarting: Boolean(state.engineStarting),
    engineReady: Boolean(state.engineReady),
    voiceReady: Boolean(state.voiceReady),
    voiceState: { ...state.voiceState },
    voiceCount: state.voices.length,
    selectedVoice: state.selectedVoice || null,
    engineStatusRevision: state.engineStatusRevision,
    timingDebug: state.timingDebug,
    timingLogFileName: state.timingLogFileName,
    timingLogPath: state.timingLogPath,
    lastError: state.lastError,
    answerStates: [...state.answerRecords.values()].slice(-20).map((record) => ({
      messageId: record.messageId,
      state: record.state,
      textHash: record.textHash,
      chunkCount: record.chunkCount
    }))
  };
}

async function loadCompletedAnswers() {
  const stored = await browserApi.storage.local.get({ irodoriCompletedAnswers: [] });
  for (const item of stored.irodoriCompletedAnswers || []) {
    if (!item || !item.key) continue;
    state.answerRecords.set(item.key, {
      key: item.key,
      source: item.source || (String(item.key).startsWith("selection|") ? "selection" : "restored"),
      messageId: item.messageId || item.key,
      conversationId: item.conversationId || "",
      textHash: item.textHash || "",
      state: "completed",
      sequence: 0,
      jobs: [],
      chunkCount: item.chunkCount || 0,
      planning: false,
      planningDone: true,
      nextPlayIndex: item.chunkCount || 0
    });
  }
}

async function publishStatus() {
  const status = publicStatus();
  await browserApi.browserAction.setBadgeText({ text: status.current ? String(status.queued + 1) : (status.queued ? String(status.queued) : "") });
  await browserApi.browserAction.setBadgeBackgroundColor({ color: state.lastError ? "#b42318" : "#7357d9" });
  const tabs = await browserApi.tabs.query({});
  for (const tab of tabs) {
    if (tab.id) browserApi.tabs.sendMessage(tab.id, { type: "queue-status", status }).catch(() => {});
  }
}

async function visibleError(message, tabId) {
  state.lastError = String(message || "不明なエラー");
  await browserApi.notifications.create({
    type: "basic",
    title: "Irodori Voice Reader",
    message: state.lastError
  });
  if (tabId) browserApi.tabs.sendMessage(tabId, { type: "show-error", message: state.lastError }).catch(() => {});
  await publishStatus();
}

function apiBase(value) {
  const url = new URL(String(value || Processor.apiUrlForEngine("standard")));
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname)) {
    throw new Error("API URLはローカルHTTP（127.0.0.1 または localhost）のみ指定できます。");
  }
  return url.href.replace(/\/$/, "");
}

function decodeTimingDiagnostics(value) {
  if (!value) return null;
  try {
    const normalized = String(value).replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(normalized);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return JSON.parse(new TextDecoder("utf-8").decode(bytes));
  } catch (_) {
    return null;
  }
}

function logBackendTiming(job, response) {
  if (!state.timingDebug || !response || !response.headers || typeof response.headers.get !== "function") return;
  const meta = { message_id: job.messageId, chunk_index: job.chunkIndex + 1 };
  const diagnostics = decodeTimingDiagnostics(response.headers.get("x-irodori-timing-diagnostics"));
  const lock = diagnostics && diagnostics.lock ? diagnostics.lock : {};
  if (lock.wait_start || response.headers.get("x-irodori-timing-lock-wait-ms")) {
    timingLog("synthesis_lock_wait_start", {
      ...meta,
      backend_timestamp: lock.wait_start || null,
      observed_after_response: true
    });
    timingLog("synthesis_lock_acquired", {
      ...meta,
      backend_timestamp: lock.acquired || null,
      wait_ms: lock.wait_ms != null ? Number(lock.wait_ms) : Number(response.headers.get("x-irodori-timing-lock-wait-ms")),
      observed_after_response: true
    });
    timingLog("synthesis_lock_released", {
      ...meta,
      backend_timestamp: lock.released || null,
      hold_ms: lock.hold_ms != null ? Number(lock.hold_ms) : Number(response.headers.get("x-irodori-timing-lock-hold-ms")),
      observed_after_response: true
    });
  }
  const stable = diagnostics && diagnostics.stable;
  state.lastTimingSummary = {
    messageId: job.messageId,
    chunkIndex: job.chunkIndex + 1,
    lockWaitMs: lock.wait_ms == null ? null : Number(lock.wait_ms),
    lockHoldMs: lock.hold_ms == null ? null : Number(lock.hold_ms),
    speakerRetries: 0,
    audioQualityRetries: 0
  };
  if (!stable || !Array.isArray(stable.attempts)) return;
  for (const item of stable.attempts) {
    if (!item || typeof item !== "object") continue;
    const attempt = Number(item.attempt);
    const fields = {
      ...meta,
      attempt: Number.isFinite(attempt) ? attempt : null,
      seed: item.seed == null ? null : Number(item.seed),
      speaker_rescue_attempt: item.speaker_rescue_attempt == null ? null : Number(item.speaker_rescue_attempt),
      audio_quality_rescue_attempt: item.audio_quality_rescue_attempt == null ? null : Number(item.audio_quality_rescue_attempt),
      backend_trace_timestamp: item.trace_start || null
    };
    timingLog("stable_attempt_start", fields);
    timingLog("stable_attempt_end", {
      ...fields,
      elapsed_ms: item.elapsed_ms == null ? null : Number(item.elapsed_ms),
      elapsed_precision: item.elapsed_precision || null,
      result: item.result || "complete",
      speaker_probe_rms: item.speaker_probe_rms == null ? null : Number(item.speaker_probe_rms),
      speaker_probe_threshold: item.speaker_probe_threshold == null ? null : Number(item.speaker_probe_threshold),
      audio_quality_max_sample_jump: item.audio_quality_max_sample_jump == null ? null : Number(item.audio_quality_max_sample_jump),
      audio_quality_non_finite_samples: item.audio_quality_non_finite_samples == null ? null : Number(item.audio_quality_non_finite_samples)
    });
    if (item.retry_reason) {
      if (item.retry_reason === "speaker_rescue") state.lastTimingSummary.speakerRetries += 1;
      if (item.retry_reason === "audio_quality_rescue") state.lastTimingSummary.audioQualityRetries += 1;
      timingLog("stable_retry", {
        ...fields,
        reason: String(item.retry_reason)
      });
    }
  }
  if (stable.session_wall_ms != null) {
    timingLog("stable_synthesis_summary", {
      ...meta,
      session_wall_ms: Number(stable.session_wall_ms),
      attempt_count: stable.attempts.length,
      trace_end: stable.trace_end || null
    });
  }
}

async function requestTts(job, suppliedController = null) {
  if (state.engineTransition || state.engineStarting || (nativeMessagingAvailable() && !state.engineReady)) throw engineTransitionError();
  const expectedEpoch = Number.isInteger(job.engineEpoch) ? job.engineEpoch : state.engineEpoch;
  const config = await settings();
  if (expectedEpoch !== state.engineEpoch || state.engineTransition || state.engineStarting || (nativeMessagingAvailable() && !state.engineReady)) throw engineTransitionError();
  const endpoint = endpointForConfig(config);
  const timingStarted = timingNow();
  job.timing = job.timing || {};
  job.timing.ttsStartedAt = timingStarted;
  timingLog("tts_start", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    chunk_count: job.answerRecord.chunkCount || job.answerRecord.jobs.length || null,
    endpoint
  });
  console.info("[Irodori] tts-request", {
    storage_engine: config.engine,
    api_mode: config.apiMode,
    derived_apiUrl: endpoint,
    endpoint,
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    voice: job.voice,
    voice_source: job.voiceSource || job.voiceResolution?.voiceSource || "default"
  });
  voiceRoutingLog("tts_request", {
    message_id: job.messageId,
    raw_text: job.answerRecord && job.answerRecord.originalText,
    text: job.text,
    character_tag: job.voiceRoute?.tag || job.tag || "",
    mapped_voice: job.voiceRoute?.mappedVoice || "",
    tag_resolution: job.voiceRoute?.tagResolution || "",
    dialogue: Boolean(job.voiceRoute?.dialogue),
    narration: Boolean(job.voiceRoute?.narration),
    resolved_voice: job.voice,
    queue_voice: job.voice,
    tts_voice: job.voice,
    voice_source: job.voiceSource || job.voiceResolution?.voiceSource || "default",
    endpoint
  });
  const controller = suppliedController || new AbortController();
  state.abortController = controller;
  let response;
  try {
    response = await fetch(`${apiBase(endpoint)}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({
        text: job.text,
        voice: job.voice,
        ...(config.apiMode === "custom" ? {} : { engine: config.engine }),
        split_options: {
          enabled: config.longTextEnabled,
          target_seconds: config.longTextTargetSeconds,
          max_chars: config.longTextMaxChars,
          preserve_dialogue: config.longTextPreserveDialogue,
          keep_character_tags_separate: config.longTextKeepCharacterTagsSeparate
        },
        // Firefox has already resolved registered character tags locally.
        // Do not let the generic KurisuModel parser reinterpret prose such as
        // 【100点！】 as a speaker tag in the API process.
        speaker_tag_policy: "preserve",
        timing_debug: Boolean(config.timingDebug),
        timing_context: {
          message_id: job.messageId,
          chunk_index: job.chunkIndex + 1,
          chunk_count: job.answerRecord.chunkCount || job.answerRecord.jobs.length || null
        }
      }),
      signal: controller.signal
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    const classified = Processor.classifyApiError(error, 0);
    const failure = new Error(`${classified.message} ${endpoint}（${error.message}）`);
    failure.retryable = Boolean(classified.retryable);
    failure.httpStatus = 0;
    throw failure;
  } finally {
    state.abortController = null;
  }
  timingLog("tts_response_headers", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    status: response.status,
    content_type: response.headers && response.headers.get ? response.headers.get("content-type") : "",
    content_length: response.headers && response.headers.get ? response.headers.get("content-length") : "",
    elapsed_ms: Number((timingNow() - timingStarted).toFixed(1))
  });
  logBackendTiming(job, response);
  if (expectedEpoch !== state.engineEpoch || state.engineTransition) throw engineTransitionError();
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let errorBody = null;
    try {
      errorBody = await response.json();
      detail = errorBody.message || errorBody.error || detail;
    } catch (_) {}
    const classified = Processor.classifyApiError(new Error(detail), response.status, errorBody);
    const failure = new Error(`${classified.message}（${detail}）`);
    failure.retryable = Boolean(classified.retryable);
    failure.httpStatus = response.status;
    throw failure;
  }
  let blob;
  try {
    blob = await response.blob();
  } catch (error) {
    error.retryable = true;
    throw error;
  }
  timingLog("tts_blob_complete", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    blob_size: blob.size,
    elapsed_ms: Number((timingNow() - timingStarted).toFixed(1))
  });
  if (blob.size < 44) throw new Error("Irodori APIから正常なWAVが返りませんでした。");
  return blob;
}

async function requestPlan(text, voice, config, expectedEpoch = state.engineEpoch, timingMeta = {}) {
  if (expectedEpoch !== state.engineEpoch || state.engineTransition || state.engineStarting || (nativeMessagingAvailable() && !state.engineReady)) throw engineTransitionError();
  const endpoint = endpointForConfig(config);
  const timingStarted = timingNow();
  timingLog("plan_start", { ...timingMeta, endpoint });
  console.info("[Irodori] plan-request", { storage_engine: config.engine, api_mode: config.apiMode, derived_apiUrl: endpoint, endpoint });
  const response = await fetch(`${apiBase(endpoint)}/v1/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      text,
      voice,
      ...(config.apiMode === "custom" ? {} : { engine: config.engine }),
      split_options: {
        enabled: config.longTextEnabled,
        target_seconds: config.longTextTargetSeconds,
        max_chars: config.longTextMaxChars,
        preserve_dialogue: config.longTextPreserveDialogue,
        keep_character_tags_separate: config.longTextKeepCharacterTagsSeparate
      },
      speaker_tag_policy: "preserve"
    })
  });
  if (expectedEpoch !== state.engineEpoch || state.engineTransition || state.engineStarting || (nativeMessagingAvailable() && !state.engineReady)) throw engineTransitionError();
  if (!response.ok) throw new Error(`分割計画エラー: ${response.status}`);
  const body = await response.json();
  const chunks = Array.isArray(body.chunks) ? body.chunks : [];
  timingLog("plan_end", { ...timingMeta, chunk_count: chunks.length, elapsed_ms: Number((timingNow() - timingStarted).toFixed(1)) });
  return chunks;
}

function textHash(value) {
  let hash = 2166136261;
  for (const char of String(value || "")) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function answerKeyFor(source, answer, text) {
  if (answer && answer.messageId) {
    return `${source}|${answer.conversationId || ""}|${answer.messageId}`;
  }
  if (source === "selection") {
    return `${source}|manual|${textHash(text)}`;
  }
  return `${source}|manual|${Date.now()}|${textHash(text)}|${++state.answerSequence}`;
}

function findSelectionRecord(text) {
  const hash = textHash(text);
  return [...state.answerRecords.values()].find((record) => record.source === "selection" && record.textHash === hash);
}

function debugTransition(record, job, nextState, flags = {}) {
  const event = {
    message_id: record.messageId,
    state: nextState,
    text_hash: record.textHash,
    chunk_text_hash: job.textHash,
    chunk_index: job.chunkIndex + 1,
    chunk_count: record.chunkCount || record.jobs.length || null,
    generation_started: Boolean(flags.generation_started),
    generation_completed: Boolean(flags.generation_completed),
    play_started: Boolean(flags.play_started),
    play_completed: Boolean(flags.play_completed)
  };
  if (nextState === "generated" && job.generatedLogged) {
    console.error("[Irodori] duplicate generation detected", event);
  }
  if (nextState === "generated") job.generatedLogged = true;
  console.info("[Irodori] answer-transition", event);
}

function playbackKey(record, job) {
  return `${record.source}|${record.conversationId}|${record.messageId}|${job.chunkIndex}`;
}

function playbackLifecycle(record, job, stage) {
  console.info("[Irodori] playback-lifecycle", {
    stage,
    message_id: record.messageId,
    state: job.status,
    text_hash: record.textHash,
    chunk_index: job.chunkIndex + 1,
    chunk_count: record.chunkCount || record.jobs.length || null
  });
}

function makeAnswerRecord(source, answer, text) {
  const key = answerKeyFor(source, answer, text);
  let existing = state.answerRecords.get(key);
  if (!existing && source === "selection") existing = findSelectionRecord(text);
  if (existing) return existing;
  const record = {
    key,
    source,
    messageId: answer && answer.messageId ? String(answer.messageId) : key,
    conversationId: answer && answer.conversationId ? String(answer.conversationId) : "manual",
    textHash: answer && answer.textHash ? String(answer.textHash) : textHash(text),
    originalText: String(text || ""),
    cleanedText: "",
    sourceUrl: answer && answer.sourceUrl ? String(answer.sourceUrl) : "",
    captureRequestId: datasetRequestId(),
    captureCreatedAt: new Date().toISOString(),
    captureWarningShown: false,
    state: "queued",
    sequence: ++state.answerSequence,
    jobs: [],
    chunkCount: null,
    planning: false,
    planningDone: false,
    nextPlayIndex: 0
  };
  state.answerRecords.set(key, record);
  state.answerOrder.push(key);
  return record;
}

function addJob(record, text, voice, source, tabId, chunkIndex, segment = null, fallbackVoice = "") {
  const existing = record.jobs.find((candidate) => candidate.chunkIndex === chunkIndex);
  if (existing || state.playedKeys.has(`${record.source}|${record.conversationId}|${record.messageId}|${chunkIndex}`)) {
    console.error("[Irodori] duplicate enqueue prevented", {
      message_id: record.messageId,
      chunk_index: chunkIndex + 1
    });
    return existing || null;
  }
  const routeSource = String((segment && segment.voiceSource) || (segment && segment.voiceRoute && segment.voiceRoute.source) || "default");
  const route = {
    ...(segment && segment.voiceRoute ? segment.voiceRoute : {}),
    voice: String(voice || ""),
    source: routeSource,
    tag: String((segment && segment.voiceRoute && segment.voiceRoute.tag) || (segment && segment.tag) || ""),
    mappedVoice: String((segment && segment.voiceRoute && segment.voiceRoute.mappedVoice) || ""),
    fallbackVoice: routeSource === "character_tag"
      ? ""
      : String((segment && segment.voiceRoute && segment.voiceRoute.fallbackVoice) || fallbackVoice || ""),
    dialogue: Boolean(segment && segment.dialogue),
    narration: Boolean(segment && segment.narration),
    locked: true
  };
  const job = {
    answerKey: record.key,
    answerRecord: record,
    messageId: record.messageId,
    textHash: textHash(text),
    text,
    voice: route.voice,
    requestedVoice: route.voice,
    voiceSource: route.source,
    voiceRoute: route,
    tag: route.tag,
    character: route.source === "character_tag" ? route.tag : "",
    source,
    tabId,
    chunkIndex,
    engineEpoch: state.engineEpoch,
    voiceRevision: state.voiceRevision,
    status: "queued",
    ttsRetryAttempts: 0,
    blob: null,
    controller: null,
    generatedLogged: false,
    playCallCount: 0,
    captureStarted: false
  };
  record.jobs.push(job);
  state.queue.push(job);
  timingLog("job_queued", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    chunk_count: record.chunkCount || null,
    source: job.source,
    voice: job.voice,
    voice_source: job.voiceSource
  });
  voiceRoutingLog("queue", {
    message_id: job.messageId,
    raw_text: record.originalText,
    text: job.text,
    character_tag: job.voiceRoute.tag,
    mapped_voice: job.voiceRoute.mappedVoice,
    tag_resolution: job.voiceRoute.tagResolution || "",
    dialogue: job.voiceRoute.dialogue,
    narration: job.voiceRoute.narration,
    queue_voice: job.voice,
    resolved_voice: job.voice,
    voice_source: job.voiceSource,
    chunk_index: job.chunkIndex + 1
  });
  playbackLifecycle(record, job, "enqueue");
  debugTransition(record, job, "queued");
  return job;
}

function setRecordState(record, nextState, job) {
  record.state = nextState;
  if (job) debugTransition(record, job, nextState);
}

function nextPlaybackJob() {
  for (const key of state.answerOrder) {
    const record = state.answerRecords.get(key);
    if (!record || record.state === "completed" || record.state === "failed") continue;
    while (record.nextPlayIndex < record.jobs.length) {
      const candidate = record.jobs[record.nextPlayIndex];
      const candidateKey = playbackKey(record, candidate);
      if (["completed", "skipped"].includes(candidate.status) || state.playedKeys.has(candidateKey)) {
        if (candidate.status !== "skipped") candidate.status = "completed";
        state.inFlight.delete(candidateKey);
        record.nextPlayIndex += 1;
        continue;
      }
      break;
    }
    const job = record.jobs[record.nextPlayIndex];
    if (!job) {
      if (record.planning || !record.planningDone) return null;
      if (record.nextPlayIndex >= record.jobs.length) completeAnswer(record);
      continue;
    }
    if (job.status === "failed") {
      record.nextPlayIndex += 1;
      continue;
    }
    return job.status === "generated" ? job : null;
  }
  return null;
}

function completeAnswer(record) {
  if (record.completionLogged || record.state === "completed") return;
  record.completionLogged = true;
  record.state = "completed";
  console.info("[Irodori] answer-transition", {
    message_id: record.messageId,
    state: "completed",
    text_hash: record.textHash,
    chunk_index: record.chunkCount || 0,
    chunk_count: record.chunkCount || 0,
    generation_started: false,
    generation_completed: false,
    play_started: false,
    play_completed: true
  });
  persistCompletedAnswer(record).catch(() => {});
}

async function persistCompletedAnswer(record) {
  const stored = await browserApi.storage.local.get({ irodoriCompletedAnswers: [] });
  const summary = {
    key: record.key,
    source: record.source,
    messageId: record.messageId,
    conversationId: record.conversationId,
    textHash: record.textHash,
    state: "completed",
    chunkCount: record.chunkCount,
    completedAt: Date.now()
  };
  const values = [summary, ...(stored.irodoriCompletedAnswers || []).filter((item) => item && item.key !== record.key)].slice(0, 300);
  await browserApi.storage.local.set({ irodoriCompletedAnswers: values });
}

function traceAudio(action, job = null, extra = {}) {
  const controller = state.playbackController;
  const event = {
    audio_id: state.audioId || null,
    action,
    message_id: job ? job.messageId : (controller && controller.job.messageId),
    chunk_index: job ? job.chunkIndex : (controller ? controller.job.chunkIndex : null),
    ...extra
  };
  console.info("[Irodori][audio]", event);
}

function audioDiagnostic(error, session, reason = "") {
  const audio = state.audio;
  console.error("[Irodori][audio][exception]", {
    exception_name: error && error.name,
    exception_message: error && error.message,
    message_id: session && session.job.messageId,
    chunk_index: session ? session.job.chunkIndex : null,
    reason,
    current_audio_state: audio ? {
      paused: audio.paused,
      ended: audio.ended,
      currentTime: audio.currentTime,
      readyState: audio.readyState,
      networkState: audio.networkState,
      src: audio.src || ""
    } : null,
    paused: state.paused,
    ended: Boolean(audio && audio.ended),
    currentTime: audio ? audio.currentTime : null,
    readyState: audio ? audio.readyState : null,
    networkState: audio ? audio.networkState : null,
    queue_length: state.queue.length,
    inFlight: state.inFlight.size
  });
}

function ensureAudioInstance() {
  if (state.audio) return state.audio;
  const audio = new Audio();
  state.audio = audio;
  state.audioId += 1;
  audio.loop = false;
  audio.autoplay = false;
  audio.preload = "auto";
  traceAudio("create");
  audio.addEventListener("srcchange", () => traceAudio("srcchange"));
  audio.addEventListener("playing", () => {
    const controller = state.playbackController;
    const job = controller && controller.job;
    const now = timingNow();
    if (job) {
      job.timing = job.timing || {};
      job.timing.playingAt = now;
      timingLog("playing", {
        message_id: job.messageId,
        chunk_index: job.chunkIndex + 1,
        generated_to_playing_ms: job.timing.generatedAt ? Number((now - job.timing.generatedAt).toFixed(1)) : null,
        playback_gap_ms: state.lastAudioEndedAt == null || state.lastAudioEndedAnswerKey !== job.answerKey ? null : Number((now - state.lastAudioEndedAt).toFixed(1)),
        generation_gap_ms: job.timing.generatedAt == null || state.lastAudioEndedAt == null || state.lastAudioEndedAnswerKey !== job.answerKey ? null : Number(Math.max(0, job.timing.generatedAt - state.lastAudioEndedAt).toFixed(1))
      });
    }
    traceAudio("playing");
  });
  audio.addEventListener("pause", () => {
    traceAudio("pause");
    const controller = state.playbackController;
    if (controller && controller.stopRequested && !controller.settled) {
      settleAudioSession(controller, Object.assign(new Error("cancelled"), { cancelled: true }));
    }
  });
  audio.addEventListener("ended", () => {
    const controller = state.playbackController;
    const now = timingNow();
    state.lastAudioEndedAt = now;
    state.lastAudioEndedAnswerKey = controller && controller.job ? controller.job.answerKey : null;
    if (controller && controller.job) {
      timingLog("ended", {
        message_id: controller.job.messageId,
        chunk_index: controller.job.chunkIndex + 1,
        elapsed_ms: controller.job.timing && controller.job.timing.playingAt ? Number((now - controller.job.timing.playingAt).toFixed(1)) : null
      });
    }
    traceAudio("ended");
    if (controller && !controller.settled) settleAudioSession(controller, null);
  });
  audio.addEventListener("error", () => {
    const controller = state.playbackController;
    const error = new Error("FirefoxでWAVを再生できませんでした。");
    error.name = "AudioError";
    traceAudio("error", controller && controller.job);
    if (controller && !controller.settled) settleAudioSession(controller, error);
  });
  audio.addEventListener("canplay", () => traceAudio("canplay"));
  audio.addEventListener("loadeddata", () => traceAudio("loadeddata"));
  audio.addEventListener("loadstart", () => traceAudio("loadstart"));
  audio.addEventListener("emptied", () => traceAudio("emptied"));
  return audio;
}

function settleAudioSession(session, error) {
  if (!session || session.settled) return;
  session.settled = true;
  const audio = state.audio;
  if (state.playbackController === session) state.playbackController = null;
  state.cancelPlayback = null;
  if (session.url) {
    URL.revokeObjectURL(session.url);
    traceAudio("revoke", session.job, { url: session.url });
    session.url = null;
  }
  if (state.objectUrl) state.objectUrl = null;
  if (error) {
    session.reject(error);
  } else {
    session.resolve();
  }
}

function playJobOnce(job) {
  const key = playbackKey(job.answerRecord, job);
  if (state.playedKeys.has(key)) return Promise.resolve();
  if (state.playbackController) throw new Error("再生controllerが既に使用中です。");
  const audio = ensureAudioInstance();
  const url = URL.createObjectURL(job.blob);
  const session = {
    key,
    job,
    url,
    settled: false,
    stopRequested: false,
    playCalls: (job.playCallCount || 0) + 1,
    resolve: null,
    reject: null
  };
  state.playbackController = session;
  job.playCallCount = session.playCalls;
  state.objectUrl = url;
  const promise = new Promise((resolve, reject) => {
    session.resolve = resolve;
    session.reject = reject;
  });
  try {
    traceAudio("src_set", job, { url });
    audio.src = url;
    timingLog("src_set", { message_id: job.messageId, chunk_index: job.chunkIndex + 1 });
    traceAudio("load_call", job);
    audio.load();
    timingLog("audio_load", { message_id: job.messageId, chunk_index: job.chunkIndex + 1 });
  } catch (error) {
    audioDiagnostic(error, session, "src_or_load");
    settleAudioSession(session, error);
    return promise;
  }
  const stack = new Error().stack;
  timingLog("play_call", {
    message_id: job.messageId,
    chunk_index: job.chunkIndex + 1,
    generated_to_play_call_ms: job.timing && job.timing.generatedAt ? Number((timingNow() - job.timing.generatedAt).toFixed(1)) : null
  });
  traceAudio("play_call", job, { play_call: session.playCalls, caller: stack });
  let playPromise;
  try {
    playPromise = audio.play();
  } catch (error) {
    audioDiagnostic(error, session, "play_call_throw");
    settleAudioSession(session, error);
    return promise;
  }
  Promise.resolve(playPromise).then(() => {
    traceAudio("play_resolved", job, { play_call: session.playCalls });
  }).catch((error) => {
    settleAudioSession(session, error);
  });
  return promise;
}

async function generateJob(job) {
  const record = job.answerRecord;
  state.generationWorkers += 1;
  job.status = "voice_waiting";
  record.state = "queued";
  await publishStatus();
  const controller = new AbortController();
  job.controller = controller;
  state.generationControllers.add(controller);
  try {
    await ensureJobVoiceReady(job);
    if (job.engineEpoch !== state.engineEpoch || state.engineTransition || state.engineStarting) {
      throw VoiceReadiness.transientError("Voice確認後にengineが切り替わりました。", { stage: "engine-transition" });
    }
    job.status = "generating";
    record.state = "generating";
    debugTransition(record, job, "generating", { generation_started: true });
    const voiceRevision = state.voiceRevision;
    const blob = await requestTts(job, controller);
    if (voiceRevision !== state.voiceRevision) {
      job.status = "queued";
      job.nextAttemptAt = 0;
      record.state = "queued";
      return;
    }
    job.blob = blob;
    job.status = "generated";
    job.ttsRetryAttempts = 0;
    job.nextAttemptAt = 0;
    job.voiceResolutionAttempts = 0;
    record.state = "generated";
    const generationKey = `${record.source}|${record.conversationId}|${record.messageId}|${job.chunkIndex}`;
    if (state.generatedKeys.has(generationKey)) {
      console.error("[Irodori] duplicate message_id + chunk_index generation", generationKey);
    }
    state.generatedKeys.add(generationKey);
    job.timing = job.timing || {};
    job.timing.generatedAt = timingNow();
    timingLog("generated", {
      message_id: job.messageId,
      chunk_index: job.chunkIndex + 1,
      chunk_count: record.chunkCount || record.jobs.length || null,
      elapsed_ms: job.timing.ttsStartedAt ? Number((job.timing.generatedAt - job.timing.ttsStartedAt).toFixed(1)) : null
    });
    debugTransition(record, job, "generated", { generation_completed: true });
    // Persist the exact successful WAV asynchronously.  Saving is deliberately
    // detached from playback so a filesystem/Native Messaging error cannot
    // delay or fail the normal FIFO player.
    captureTtsResultDetached(job, blob);
  } catch (error) {
    if (error.voiceResolution && error.voiceResolutionPermanent) {
      skipVoiceJobAsFinalInsurance(job, error);
    } else if (error.voiceResolution || VoiceReadiness.isVoiceNotFoundError(error)) {
      if (VoiceReadiness.isVoiceNotFoundError(error)) invalidateVoiceReadiness("tts-reported-voice-missing");
      job.status = "queued";
      record.state = "queued";
      scheduleVoiceRetry(job, error);
    } else if (error.name === "AbortError" && state.paused) {
      job.status = "queued";
      record.state = "queued";
    } else if (error.retryable && job.ttsRetryAttempts < MAX_TTS_RETRIES) {
      job.ttsRetryAttempts += 1;
      const delay = TTS_RETRY_BASE_DELAY_MS * (2 ** (job.ttsRetryAttempts - 1));
      job.nextAttemptAt = Date.now() + delay;
      job.status = "queued";
      record.state = "queued";
      state.lastError = `音声生成を再試行します（区間${job.chunkIndex + 1}、${job.ttsRetryAttempts}/${MAX_TTS_RETRIES}回目）: ${error.message}`;
      console.warn("[Irodori] tts-retry-scheduled", {
        message_id: job.messageId,
        chunk_index: job.chunkIndex + 1,
        retry: job.ttsRetryAttempts,
        max_retries: MAX_TTS_RETRIES,
        delay_ms: delay,
        http_status: error.httpStatus || 0
      });
      setTimeout(() => {
        if (state.queue.includes(job) && job.status === "queued") runSchedulers();
      }, delay);
    } else {
      job.status = "failed";
      record.state = "failed";
      state.lastError = `区間${job.chunkIndex + 1}の音声生成に失敗しました。残りの区間を完了扱いにはしません: ${error.message}`;
      if (!error.cancelled) await visibleError(state.lastError, job.tabId);
    }
  } finally {
    job.controller = null;
    state.generationControllers.delete(controller);
    state.generationWorkers -= 1;
    await publishStatus();
    runSchedulers();
  }
}

function startGenerators() {
  if (state.paused) return;
  while (state.generationWorkers < MAX_GENERATION_WORKERS) {
    const job = state.queue.find((candidate) => candidate.status === "queued"
      && (!candidate.nextAttemptAt || candidate.nextAttemptAt <= Date.now())
      && candidate.answerRecord && !["completed", "failed"].includes(candidate.answerRecord.state));
    if (!job) break;
    generateJob(job).catch((error) => visibleError(error.message, job.tabId));
  }
}

async function playNextLoop() {
  if (state.playbackLoopRunning || state.paused) return;
  state.playbackLoopRunning = true;
  state.playbackRunning = true;
  state.running = true;
  try {
    while (!state.paused) {
      const job = nextPlaybackJob();
      if (!job) break;
      const key = playbackKey(job.answerRecord, job);
      if (state.playedKeys.has(key) || job.status === "completed") {
        job.status = "completed";
        state.inFlight.delete(key);
        job.answerRecord.nextPlayIndex += 1;
        continue;
      }
      if (state.playStartedKeys.has(key)) {
        console.error("[Irodori] duplicate play_start prevented", {
          message_id: job.messageId,
          chunk_index: job.chunkIndex + 1
        });
        job.status = "completed";
        state.playedKeys.add(key);
        state.inFlight.delete(key);
        playbackLifecycle(job.answerRecord, job, "completed");
        playbackLifecycle(job.answerRecord, job, "removed");
        job.answerRecord.nextPlayIndex += 1;
        continue;
      }
      state.queue = state.queue.filter((candidate) => candidate !== job);
      state.inFlight.set(key, job);
      playbackLifecycle(job.answerRecord, job, "dequeue");
      state.current = job;
      state.lastError = "";
      job.status = "playing";
      job.answerRecord.state = "playing";
      state.playStartedKeys.add(key);
      playbackLifecycle(job.answerRecord, job, "play_start");
      debugTransition(job.answerRecord, job, "playing", { play_started: true });
      await publishStatus();
      try {
        await playJobOnce(job);
        playbackLifecycle(job.answerRecord, job, "ended");
        job.status = "completed";
        state.playedKeys.add(key);
        debugTransition(job.answerRecord, job, "chunk_completed", { play_completed: true });
        job.answerRecord.nextPlayIndex += 1;
        state.inFlight.delete(key);
        playbackLifecycle(job.answerRecord, job, "completed");
        if (job.answerRecord.planningDone && job.answerRecord.nextPlayIndex >= job.answerRecord.jobs.length) {
          completeAnswer(job.answerRecord);
        }
      } catch (error) {
        if (error.cancelled && state.paused) {
          job.status = "generated";
          state.playStartedKeys.delete(key);
          state.inFlight.delete(key);
        } else if (error.name === "AbortError") {
          // An aborted play is terminal for this automatic attempt. It must not
          // flow back through the normal scheduler and start the same chunk.
          job.status = "failed";
          state.inFlight.delete(key);
          job.answerRecord.nextPlayIndex += 1;
          audioDiagnostic(error, { job }, "playback_catch");
          await visibleError(`WAV再生が中断されました。自動再試行は行いません。（${error.message}）`, job.tabId);
        } else if (!error.cancelled) {
          job.status = "failed";
          state.inFlight.delete(key);
          job.answerRecord.nextPlayIndex += 1;
          await visibleError(error.message, job.tabId);
        } else if (state.paused) {
          job.status = "generated";
          state.playStartedKeys.delete(key);
          state.inFlight.delete(key);
        }
      } finally {
        if (job.status === "failed") state.inFlight.delete(key);
        if (job.status === "completed" || job.status === "failed") playbackLifecycle(job.answerRecord, job, "removed");
        state.current = null;
        await publishStatus();
      }
      startGenerators();
    }
  } finally {
    state.playbackLoopRunning = false;
    state.playbackRunning = false;
    state.running = Boolean(state.generationWorkers || state.inFlight.size || state.queue.some((job) => job.status !== "completed"));
    await publishStatus();
    if (!state.paused) startGenerators();
  }
}

function runSchedulers() {
  startGenerators();
  if (!state.playbackLoopRunning && !state.paused) {
    playNextLoop().catch((error) => visibleError(error.message));
  }
}

function cancelCurrent() {
  if (state.abortController) state.abortController.abort();
  for (const controller of state.generationControllers) controller.abort();
  const playback = state.playbackController;
  if (playback && state.audio) {
    playback.stopRequested = true;
    traceAudio("pause_call", playback.job, { caller: new Error().stack, reason: "cancelCurrent" });
    state.audio.pause();
    if (!playback.settled) settleAudioSession(playback, Object.assign(new Error("cancelled"), { cancelled: true }));
  }
}

async function stopForEngineSwitch() {
  state.engineEpoch += 1;
  state.paused = true;
  const currentJob = state.current;
  cancelCurrent();
  if (currentJob && !["completed", "failed", "skipped"].includes(currentJob.status) && !state.queue.includes(currentJob)) {
    state.queue.unshift(currentJob);
    if (currentJob.answerRecord) {
      currentJob.answerRecord.nextPlayIndex = Math.min(currentJob.answerRecord.nextPlayIndex, currentJob.chunkIndex);
    }
  }
  for (const job of state.queue) {
    if (["completed", "failed", "skipped"].includes(job.status)) continue;
    job.status = "queued";
    job.engineEpoch = state.engineEpoch;
    job.blob = null;
    job.controller = null;
    job.nextAttemptAt = 0;
    job.voiceResolution = null;
  }
  for (const record of state.answerRecords.values()) {
    if (! ["completed", "failed"].includes(record.state)) record.state = "queued";
  }
  state.inFlight.clear();
  state.current = null;
  state.running = false;
  invalidateVoiceReadiness("engine-switch");
  await publishStatus();
}

async function setApiMode(mode, customApiUrl = "") {
  const requestedMode = String(mode || "managed").toLowerCase() === "custom" ? "custom" : "managed";
  let customEndpoint = "";
  if (requestedMode === "custom") {
    if (!String(customApiUrl || "").trim()) throw new Error("既存Irodori-TTS APIのURLを入力してください。");
    customEndpoint = apiBase(customApiUrl);
  }
  const switchId = ++state.engineSwitchSequence;
  invalidateEngineStartup("api_mode_switch", null);
  state.engineReady = false;
  state.engineStarting = false;
  state.engineStatusRevision += 1;
  await stopForEngineSwitch();
  if (switchId !== state.engineSwitchSequence) return { cancelled: true };
  const current = await settings();
  if (requestedMode === "custom") {
    await browserApi.storage.local.set({ apiMode: "custom", customApiUrl: customEndpoint, apiUrl: customEndpoint });
    state.apiMode = "custom";
    state.customApiUrl = customEndpoint;
    state.engineReady = false;
    invalidateVoiceReadiness("custom-api-selected");
    let ready = false;
    try {
      const voiceResult = await waitForFreshVoiceReady(await settings(), { allowTransition: true, reason: "custom-api-selected" });
      if (voiceResult.cancelled) return { cancelled: true, apiMode: "custom", endpoint: customEndpoint };
      requireFreshVoiceReady(voiceResult);
      state.engineReady = true;
      state.paused = false;
      runSchedulers();
      ready = true;
    } catch (error) {
      state.lastError = error.message;
    }
    await publishStatus();
    return { settings: await settings(), apiMode: "custom", endpoint: customEndpoint, ready };
  }
  const patch = Processor.engineStoragePatch(current.engine);
  await browserApi.storage.local.set({ ...patch, apiMode: "managed", customApiUrl: current.customApiUrl || "" });
  state.apiMode = "managed";
  state.customApiUrl = current.customApiUrl || "";
  const ready = await ensureEngineReady();
  if (ready.cancelled) return { ...ready, settings: ready.settings || await settings(), apiMode: "managed", endpoint: Processor.apiUrlForEngine(current.engine), ready: false };
  state.paused = false;
  runSchedulers();
  return { settings: ready.settings || await settings(), apiMode: "managed", endpoint: Processor.apiUrlForEngine(current.engine), ready: true };
}

async function switchEngine(engine, options = {}) {
  const config = await settings();
  if (config.apiMode === "custom") {
    throw new Error("既存Irodori-TTS API使用中は生成エンジンを切り替えられません。");
  }
  if (!Object.prototype.hasOwnProperty.call(Processor.ENGINE_ENDPOINTS, engine)) {
    throw new Error("未対応の生成エンジンです。");
  }
  const switchId = ++state.engineSwitchSequence;
  const startupEpoch = invalidateEngineStartup("user_engine_switch", engine);
  const requestedEndpoint = Processor.apiUrlForEngine(engine);
  state.engineReady = false;
  state.engineStarting = true;
  state.engineTransitionState = "switching";
  state.engineTransitionError = "";
  state.lastError = "";
  state.engineStatusRevision += 1;
  console.info("[Irodori] engine-switch-start", {
    switch_id: switchId,
    requested_engine: engine,
    requested_endpoint: requestedEndpoint,
    previous_engine: state.engine
  });
  const transition = (async () => {
    await stopForEngineSwitch();
    if (switchId !== state.engineSwitchSequence) return { ok: true, cancelled: true, engine };
    const requestToken = `firefox-${switchId}-${Date.now()}`;
    let result;
    try {
      result = await nativeControl("switch_engine_async", { engine, requestId: requestToken });
    } catch (error) {
      if (switchId !== state.engineSwitchSequence) return { ok: true, cancelled: true, engine };
      throw error;
    }
    console.info("[Irodori] engine-switch-native-result", {
      switch_id: switchId,
      requested_engine: engine,
      requested_endpoint: requestedEndpoint,
      current_engine: result.engine,
      running: Boolean(result.running),
      starting: Boolean(result.starting),
      restarted: Boolean(result.restarted),
      startup_pid: result.startupPid
    });
    if (switchId !== state.engineSwitchSequence) return { ok: true, cancelled: true, engine };
    let ready = result;
    if (!result.running) {
      const monitor = attachApiStartupMonitor(result, engine, startupEpoch, { deferReady: true, deferVoiceReadiness: true });
      ready = await monitor.promise;
      if (!ready || ready.cancelled || switchId !== state.engineSwitchSequence) {
        return { ok: true, cancelled: true, engine };
      }
    }
    if (!ready.running || ready.currentEngine && ready.currentEngine !== engine) {
      throw new Error("切替後のAPIヘルスチェックに失敗しました。");
    }
    const finalStatus = await nativeControl("status", { engine });
    if (switchId !== state.engineSwitchSequence) return { ok: true, cancelled: true, engine };
    if (!nativeEngineFullyReady(finalStatus, engine)
        || finalStatus.currentEngine !== engine || finalStatus.engineTransition) {
      throw new Error("切替後の共通API readyとcurrentEngine一致を確認できませんでした。");
    }
    const patch = Processor.engineStoragePatch(engine);
    const nextConfig = Processor.mergedSettings({ ...(await settings()), ...patch, apiMode: "managed" });
    const voiceResult = await waitForFreshVoiceReady(nextConfig, { allowTransition: true, reason: "engine-switch-complete" });
    if (voiceResult.cancelled || switchId !== state.engineSwitchSequence) {
      return { ok: true, cancelled: true, engine };
    }
    const voiceReady = requireFreshVoiceReady(voiceResult);
    if (switchId !== state.engineSwitchSequence) return { ok: true, cancelled: true, engine };
    // Do not let an obsolete switch persist its engine after a newer request
    // has started.  The generation check must happen immediately before the
    // storage write as well as after Voice readiness.
    await browserApi.storage.local.set(patch);
    if (switchId !== state.engineSwitchSequence) return { ok: true, cancelled: true, engine };
    state.engine = engine;
    state.requestedEngine = null;
    state.engineStarting = false;
    state.engineReady = true;
    state.voices = voiceReady.voices;
    state.paused = false;
    state.engineTransitionState = "ready";
    state.engineTransitionError = "";
    state.engineStatusRevision += 1;
    state.lastError = "";
    console.info("[Irodori] engine-switch-complete", {
      switch_id: switchId,
      engine,
      endpoint: patch.apiUrl,
      current_engine: finalStatus.currentEngine,
      health: Boolean(finalStatus.running)
    });
    runSchedulers();
    return { ...result, ...patch, running: true, currentEngine: engine, ready: true };
  })();
  state.engineTransition = transition;
  try {
    return await transition;
  } catch (error) {
    if (switchId === state.engineSwitchSequence) {
      state.engineReady = false;
      state.engineStarting = false;
      state.lastError = error.message;
      state.engineTransitionState = "error";
      state.engineTransitionError = error.message;
      await visibleError(`${engine === "voice_stable" ? "声質安定版" : "通常版"}への切替に失敗しました。${error.message}`);
    }
    throw error;
  } finally {
    if (state.engineTransition === transition) state.engineTransition = null;
    if (switchId === state.engineSwitchSequence) {
      state.requestedEngine = null;
      state.engineStarting = false;
      if (state.engineTransitionState !== "ready") state.paused = true;
    }
    await publishStatus();
  }
}

async function enqueueSegments(text, source, tabId, answer = null, usePlanner = false) {
  if (state.engineTransition) {
    try { await state.engineTransition; }
    catch (error) { console.info("[Irodori] enqueue-kept-after-engine-transition-error", { source, error: error.message }); }
  }
  const engineEpoch = state.engineEpoch;
  const config = await settings();
  if (source === "chatgpt" && !config.chatgptEnabled) return { ignored: true };
  if (source === "gemini" && !config.geminiEnabled) return { ignored: true };
  if (source === "muse" && !config.museEnabled) return { ignored: true };
  if ((source === "chatgpt" || source === "gemini" || source === "muse") && !config.autoPlay) return { ignored: true };
  // Voice readiness keeps the authoritative live /voices response in state.
  // A temporary/profile storage read can legitimately have no cachedVoices;
  // do not let that make an ID/displayName tag look like ordinary prose.
  const liveVoices = state.voices.length
    ? state.voices
    : (Array.isArray(config.cachedVoices) ? config.cachedVoices : []);
  const routingConfig = liveVoices.length
    ? { ...config, cachedVoices: liveVoices, voiceDefinitions: liveVoices }
    : config;
  const segments = Processor.processText(text, routingConfig);
  if (!segments.length) throw new Error("設定された除外処理後に読み上げる文章がありません。");
  const record = makeAnswerRecord(source, answer, text);
  record.cleanedText = segments.map((segment) => segment.text).join("\n");
  if (record.state !== "queued" || record.planning || record.jobs.length) {
    return { ignored: true, duplicate: true, state: record.state };
  }
  timingLog("enqueue", {
    message_id: record.messageId,
    text_hash: record.textHash,
    source,
    chunk_count: segments.length
  });
  record.planning = usePlanner;
  record.planningDone = !usePlanner;
  if (!usePlanner) {
    let index = 0;
    for (const segment of segments) addJob(record, segment.text, segment.voice || config.defaultVoice, source, tabId, index++, segment, state.selectedVoice || config.defaultVoice);
    record.chunkCount = record.jobs.length;
  } else {
    (async () => {
      let index = 0;
      try {
        for (const segment of segments) {
          let planned = [];
          try {
            planned = await requestPlan(segment.text, segment.voice || config.defaultVoice, config, engineEpoch, {
              message_id: record.messageId,
              segment_index: index + 1,
              source
            });
          } catch (_) {
            // If the optional planner is unavailable, the API still accepts this chunk.
          }
          if (engineEpoch !== state.engineEpoch) {
            console.info("[Irodori] planner-epoch-changed-preserving-text", {
              message_id: record.messageId,
              segment_index: index + 1,
              old_epoch: engineEpoch,
              current_epoch: state.engineEpoch
            });
            planned = [];
          }
          if (!planned.length) planned = [{ text: segment.text }];
          // A planner response containing only empty/skip markers must not
          // delete the original prose.  Keep one fallback job so planner
          // metadata cannot turn a non-empty segment into a silent hole.
          if (!planned.some((plannedChunk) => String(plannedChunk?.text || "").trim())) {
            planned = [{ text: segment.text, planner_fallback: true }];
          }
          for (const plannedChunk of planned) {
            const chunkText = String(plannedChunk.text || "").trim();
            if (!chunkText) continue;
            if (plannedChunk && plannedChunk.skip_tts) {
              console.warn("[Irodori] planner requested skip_tts for non-empty text; preserving queue item", {
                message_id: record.messageId,
                chunk_index: index + 1
              });
            }
            addJob(record, chunkText, segment.voice || config.defaultVoice, source, tabId, index++, segment, state.selectedVoice || config.defaultVoice);
          }
          runSchedulers();
        }
      } catch (error) {
        record.state = "failed";
        state.lastError = error.message;
        await visibleError(error.message, tabId);
      } finally {
        record.planning = false;
        record.planningDone = true;
        record.chunkCount = record.jobs.length;
        if (!record.jobs.length) record.state = "failed";
        runSchedulers();
      }
    })();
  }
  state.paused = false;
  await publishStatus();
  runSchedulers();
  return { queued: usePlanner ? true : record.jobs.length, messageId: record.messageId, answerKey: record.key };
}

async function enqueueText(text, source, tabId, answer = null, requestEpoch = state.engineEpoch) {
  // Capture the request generation before waiting for a startup or switch.
  // A request that was already pending when the user changed engines must
  // not resume against the newly selected endpoint after that wait.
  const requestEngineEpoch = requestEpoch;
  if (nativeMessagingAvailable() && !state.engineReady) {
    try {
      if (state.engineTransition) await state.engineTransition;
      else await ensureEngineReady();
    } catch (error) {
      // Queue the text even while API startup/Voice discovery is transiently
      // unavailable.  The per-job Voice gate will retry without discarding it.
      console.info("[Irodori] enqueue-kept-before-engine-ready", {
        source,
        error_name: error?.name || null,
        error_message: error?.message || String(error)
      });
    }
  }
  if (requestEngineEpoch !== state.engineEpoch) {
    console.info("[Irodori] enqueue-rebased-after-engine-epoch", {
      source,
      request_epoch: requestEngineEpoch,
      current_epoch: state.engineEpoch
    });
  }
  const usePlanner = source === "selection" || Boolean(answer && ["chatgpt", "gemini", "muse"].includes(source));
  return enqueueSegments(text, source, tabId, answer, usePlanner);
}

async function selectedTextFromActiveTab() {
  const [tab] = await browserApi.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("対象タブを取得できません。");
  const reply = await browserApi.tabs.sendMessage(tab.id, { type: "get-selection" });
  return { text: reply && reply.text, tabId: tab.id };
}

browserApi.runtime.onMessage.addListener((message, sender) => {
  const type = message && message.type;
  if (type === "initialize") return ensureInitialized().then(async (initialized) => {
    const current = await settings();
    return { ...initialized, settings: current, voices: state.voices.length ? state.voices : (current.cachedVoices || []) };
  });
  if (type === "get-settings") return settings();
  if (type === "save-settings") return persistSettingsPatch(message.patch || {}, message.base || {});
  if (type === "export-settings") return settings().then(async (value) => {
    let voices = Array.isArray(value.cachedVoices) ? value.cachedVoices : [];
    try { voices = (await nativeControl("voice_state")).voices || voices; } catch (_) {}
    return { ...Processor.settingsExport(value), voiceDefinitions: voices.map((voice) => ({
      id: String(voice.id || ""), name: String(voice.name || voice.id || ""), type: String(voice.type || "unknown"), managed: Boolean(voice.managed)
    })).filter((voice) => voice.id) };
  });
  if (type === "import-settings") {
    return importSettings(message.settings);
  }
  if (type === "timing-log-status") return Promise.resolve({
    enabled: state.timingDebug,
    fileName: state.timingLogFileName,
    path: state.timingLogPath
  });
  if (type === "open-timing-log-folder") {
    return nativeControl("open_timing_log_folder");
  }
  if (type === "timing-log-event") {
    if (state.timingDebug && message.event && typeof message.event === "object") appendTimingRecord(message.event);
    return Promise.resolve({ ok: true });
  }
  if (type === "apply-preset") {
    return settings().then(async (config) => {
      const preset = Processor.presetSettings(message.preset, config.presets);
      if (!preset) throw new Error("プリセットが見つかりません。");
      return persistSettingsPatch({ ...preset, currentPreset: message.preset }, config);
    });
  }
  if (type === "enqueue") {
    const requestEpoch = state.engineEpoch;
    return ensureEngineReady().then(() => enqueueText(
      message.text,
      message.source || "manual",
      sender.tab && sender.tab.id,
      { ...(message.answer || {}), sourceUrl: (message.answer && message.answer.sourceUrl) || (sender.tab && sender.tab.url) || "" },
      requestEpoch
    ));
  }
  if (type === "status") return Promise.resolve(publicStatus());
  if (type === "set-api-mode") {
    return ensureInitialized().then(() => setApiMode(message.apiMode, message.customApiUrl));
  }
  if (type === "native-control") {
    if (!["status", "start", "start_engine_async", "stop", "start_standard", "stop_standard", "start_stable", "stop_stable", "switch_engine", "switch_engine_async", "voice_state", "voice_import", "voice_update", "voice_delete", "validate_dataset_directory", "choose_dataset_directory", "save_tts_dataset", "timing_log_append", "open_timing_log_folder"].includes(message.command)) throw new Error("未対応のローカル操作です。");
    const { type: _, command, ...payload } = message;
    if (["start", "start_engine_async", "stop", "start_standard", "stop_standard", "start_stable", "stop_stable", "switch_engine", "switch_engine_async"].includes(command)) {
      return settings().then((config) => {
        if (config.apiMode === "custom") throw new Error("既存Irodori-TTS API使用中はNative HostでAPIを起動・停止・切替しません。");
        return command === "start" ? startApiNonBlocking(payload) : nativeControl(command, payload);
      });
    }
    if (["voice_import", "voice_update", "voice_delete"].includes(command)) {
      return nativeControl(command, payload).then(async (result) => {
        invalidateVoiceReadiness("voice-registration-changed");
        invalidatePendingVoiceJobs();
        try {
          const refreshed = await refreshVoiceCatalog("voice-registration-changed");
          result.voiceRefreshWarning = refreshed.warning || "";
        } catch (error) {
          // Saving may succeed while the API is stopped, or the last Voice
          // may have been deleted. Never relabel that successful save a failure.
          await browserApi.storage.local.set({ cachedVoices: [] });
          state.voices = [];
          result.voiceRefreshWarning = error.message;
        }
        runSchedulers();
        await browserApi.runtime.sendMessage({ type: "voice-catalog-updated" }).catch(() => {});
        return result;
      });
    }
    return command === "start" ? startApiNonBlocking(payload) : nativeControl(command, payload);
  }
  if (type === "switch-engine") return ensureInitialized().then(() => switchEngine(String(message.engine || "standard")));
  if (type === "stop") {
    state.paused = true;
    cancelCurrent();
    return publishStatus().then(publicStatus);
  }
  if (type === "resume") {
    state.paused = false;
    runSchedulers();
    return Promise.resolve(publicStatus());
  }
  if (type === "skip") {
    if (state.current) {
      const skipped = state.current;
      skipped.skipRequested = true;
      skipped.status = "skipped";
      skipped.answerRecord.nextPlayIndex += 1;
      skipped.answerRecord.state = "queued";
      state.current = null;
    }
    state.paused = false;
    cancelCurrent();
    runSchedulers();
    return Promise.resolve(publicStatus());
  }
  if (type === "clear") {
    state.queue = [];
    state.answerRecords.clear();
    state.answerOrder = [];
    state.paused = false;
    cancelCurrent();
    invalidateVoiceReadiness("queue-clear");
    return publishStatus().then(publicStatus);
  }
  if (type === "read-active-selection") {
    return selectedTextFromActiveTab().then(({ text, tabId }) => {
      if (!text || !text.trim()) throw new Error("読み上げるテキストを選択してください。");
      return enqueueText(text, "selection", tabId);
    });
  }
  if (type === "health") {
    return settings().then(async (config) => {
      const endpoint = endpointForConfig(config);
      console.info("[Irodori] health-request", { storage_engine: config.engine, derived_apiUrl: endpoint, endpoint });
      const response = await fetch(`${apiBase(endpoint)}/health`, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `APIエラー: ${response.status}`);
      return body;
    });
  }
  if (type === "engine-health") {
    return settings().then((config) => engineHealthStatuses(config));
  }
  if (type === "voices") {
    return refreshVoiceCatalog();
  }
  return undefined;
});

browserApi.menus.create({ id: "irodori-read-selection", title: "Irodoriで読み上げ", contexts: ["selection"] });
browserApi.menus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "irodori-read-selection" && info.selectionText) {
    enqueueText(info.selectionText, "selection", tab && tab.id).catch((error) => visibleError(error.message, tab && tab.id));
  }
});

browserApi.commands.onCommand.addListener(async (command) => {
  try {
    if (command === "read-selection") {
      const { text, tabId } = await selectedTextFromActiveTab();
      if (!text || !text.trim()) throw new Error("読み上げるテキストを選択してください。");
      await enqueueText(text, "selection", tabId);
    } else if (command === "stop-reading") {
      state.paused = true;
      cancelCurrent();
    }
  } catch (error) {
    await visibleError(error.message);
  }
});

browserApi.browserAction.onClicked.addListener(() => {
  browserApi.sidebarAction.toggle().catch((error) => visibleError(`サイドバーを開けません。（${error.message}）`));
});

ensureEngineReady().catch((error) => {
  state.lastError = error.message;
  const label = state.apiMode === "custom"
    ? `既存Irodori-TTS APIに接続できません。${error.message}`
    : `保存済みの生成エンジンを起動できませんでした。${error.message}`;
  if (!nativeMessagingAvailable() && state.apiMode !== "custom") return;
  visibleError(label).catch(() => {});
});
