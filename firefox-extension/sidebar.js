"use strict";
const api = browser;
const Processor = globalThis.IrodoriTextProcessor;
const ApiState = globalThis.IrodoriApiState;
const $ = (id) => document.getElementById(id);
let current = Processor.mergedSettings();
let voices = [];
let saveTimer = 0;
let pendingEngine = null;
let storageSyncSequence = 0;
let lastEngineStatusRevision = -1;
let lastSavedSettings = null;
let settingsEditorReady = false;

function message(text, error = false) {
  $("message").textContent = text || "";
  $("message").className = error ? "error" : "";
}

function renderTimingLogStatus(status) {
  const element = $("timingLogStatus");
  if (!element) return;
  element.textContent = status && status.fileName ? `計測ログ: ${status.fileName}` : "計測ログ: 未開始";
}

async function refreshTimingLogStatus() {
  try {
    renderTimingLogStatus(await api.runtime.sendMessage({ type: "timing-log-status" }));
  } catch (_) {
    renderTimingLogStatus(null);
  }
}

function lines(id) {
  return $(id).value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
}

function voiceStatusLabel(voiceId) {
  if (!voiceId) return "なし";
  const voice = voices.find((item) => String(item.id) === String(voiceId));
  return voice ? `${voice.name || voice.id} — ${voice.id}` : String(voiceId);
}

function setToggle(id, value) {
  const input = $(id);
  input.checked = Boolean(value);
  const label = input.closest(".switch-row");
  if (label) label.querySelector("b").textContent = input.checked ? "ON" : "OFF";
}

async function validateWavSaveDirectory(directory) {
  const value = String(directory || "").trim();
  if (!value) return { valid: false, error: "保存先フォルダーを指定してください。" };
  try {
    return await api.runtime.sendMessage({ type: "native-control", command: "validate_dataset_directory", base_dir: value });
  } catch (error) {
    return { valid: false, error: error.message };
  }
}

function setVoiceOptions(select, selected) {
  const known = voices.length ? voices : [{ id: selected || "yui", name: selected || "yui" }];
  const values = [...known];
  if (selected && !values.some((voice) => voice.id === selected)) values.unshift({ id: selected, name: `${selected}（未確認）` });
  select.replaceChildren(...values.map((voice) => {
    const option = document.createElement("option");
    option.value = voice.id;
    option.textContent = `${voice.name} — ${voice.id}`;
    return option;
  }));
  select.value = selected || (values[0] && values[0].id) || "";
}

function fillVoiceSelects() {
  for (const select of document.querySelectorAll(".voice-select, .mapping-row select")) {
    setVoiceOptions(select, select.dataset.selected || select.value || current.defaultVoice);
  }
}

function updateMappingSummary(row) {
  const tag = row.querySelector('[data-key="tag"]')?.value.trim() || "タグ未設定";
  const select = row.querySelector('[data-key="voice"]');
  const voice = select?.selectedOptions?.[0]?.textContent || select?.value || "Voice未選択";
  row.querySelector('[data-view="tag"]').textContent = tag;
  row.querySelector('[data-view="voice"]').textContent = voice;
  row.setAttribute("aria-label", `${tag} / ${voice}`);
}

function updateMappingGroups() {
  const activeRows = $("activeMappingRows");
  const inactiveRows = $("inactiveMappingRows");
  const activeCount = $("activeMappingCount");
  const inactiveCount = $("inactiveMappingCount");
  const empty = $("activeMappingEmpty");
  const missing = [["activeMappingRows", activeRows], ["inactiveMappingRows", inactiveRows], ["activeMappingCount", activeCount], ["inactiveMappingCount", inactiveCount], ["activeMappingEmpty", empty]].filter(([, node]) => !node).map(([id]) => id);
  if (missing.length) {
    console.error("[Irodori][firefox][sidebar] DOM contract violation", { missing, script: "sidebar.js" });
    message(`画面のHTMLとsidebar.jsが一致しません: ${missing.join(", ")}`, true);
    return false;
  }
  const active = activeRows.children.length;
  const inactive = inactiveRows.children.length;
  activeCount.textContent = String(active);
  inactiveCount.textContent = String(inactive);
  empty.hidden = active !== 0;
  return true;
}

function addMapping(item = { enabled: true, tag: "", voice: current.defaultVoice }, target) {
  const row = $("mappingTemplate").content.firstElementChild.cloneNode(true);
  row.querySelector('[data-key="enabled"]').checked = item.enabled !== false;
  row.querySelector('[data-key="tag"]').value = item.tag || "";
  const select = row.querySelector('[data-key="voice"]');
  select.dataset.selected = item.voice || current.defaultVoice;
  setVoiceOptions(select, select.dataset.selected);
  row.dataset.manual = item.manual ? "true" : "false";
  if (!item.manual) select.disabled = true;
  const details = row.querySelector(".mapping-details");
  row.querySelector('[data-action="toggle-details"]').addEventListener("click", () => {
    const open = details.hidden;
    details.hidden = !open;
    row.querySelector('[data-action="toggle-details"]').textContent = open ? "⌄" : "›";
    row.querySelector('[data-action="toggle-details"]').setAttribute("aria-expanded", String(open));
  });
  row.querySelector('[data-key="enabled"]').addEventListener("change", () => {
    const destination = row.querySelector('[data-key="enabled"]').checked ? $("activeMappingRows") : $("inactiveMappingRows");
    destination.appendChild(row);
    updateMappingGroups();
    scheduleSave();
  });
  row.querySelector('[data-key="tag"]').addEventListener("input", () => updateMappingSummary(row));
  select.addEventListener("change", () => updateMappingSummary(row));
  row.querySelector('[data-action="remove"]').addEventListener("click", () => { row.remove(); updateMappingGroups(); scheduleSave(); });
  row.querySelector('[data-action="remove"]').hidden = !item.manual;
  updateMappingSummary(row);
  row.addEventListener("change", scheduleSave);
  row.addEventListener("input", scheduleSave);
  (target || (item.enabled !== false ? $("activeMappingRows") : $("inactiveMappingRows"))).appendChild(row);
}

function renderMappingGroups() {
  $("activeMappingRows").replaceChildren();
  $("inactiveMappingRows").replaceChildren();
  const sourceVoices = voices.length ? voices : (current.cachedVoices || []);
  const candidates = Processor.characterMappingCandidates(sourceVoices.map((voice) => ({ ...voice, name: (current.voiceDisplayNames || {})[String(voice.id)] || voice.name })), current.characterMappings);
  const groups = Processor.characterMappingGroups(candidates);
  for (const item of groups.active) addMapping(item, $("activeMappingRows"));
  for (const item of groups.inactive) addMapping(item, $("inactiveMappingRows"));
  updateMappingGroups();
}

function addRegex(item = { enabled: true, pattern: "", flags: "gu", replacement: "" }) {
  const row = $("regexTemplate").content.firstElementChild.cloneNode(true);
  for (const key of ["pattern", "flags", "replacement"]) row.querySelector(`[data-key="${key}"]`).value = item[key] || (key === "flags" ? "gu" : "");
  row.querySelector('[data-key="enabled"]').checked = item.enabled !== false;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => { row.remove(); scheduleSave(); });
  row.addEventListener("change", scheduleSave);
  row.addEventListener("input", scheduleSave);
  $("regexRows").appendChild(row);
}

function renderPresets() {
  const values = Object.entries(Processor.BUILTIN_PRESETS).map(([id, preset]) => ({ value: `builtin:${id}`, label: preset.label }));
  for (const name of Object.keys(current.presets || {}).sort()) values.push({ value: `custom:${name}`, label: name });
  $("preset").replaceChildren(...values.map((item) => {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    return option;
  }));
  $("preset").value = current.currentPreset || "builtin:chat";
}

function renderApiMode() {
  const custom = current.apiMode === "custom";
  const modeInput = document.querySelector(`input[name="apiMode"][value="${custom ? "custom" : "managed"}"]`);
  if (modeInput) modeInput.checked = true;
  $("managedApiControls").hidden = custom;
  $("customApiControls").hidden = !custom;
  $("customApiUrl").value = current.customApiUrl || "";
  for (const input of document.querySelectorAll('input[name="engine"]')) input.disabled = custom;
  $("apiStart").disabled = custom;
  $("apiStop").disabled = custom;
}

function render(config, rebuildRows = true) {
  current = Processor.mergedSettings(config);
  $("apiUrl").textContent = Processor.apiUrlForSettings(current);
  renderApiMode();
  const engineInput = document.querySelector(`input[name="engine"][value="${current.engine}"]`);
  if (engineInput) engineInput.checked = true;
  for (const id of ["chatgptEnabled", "geminiEnabled", "museEnabled", "autoPlay", "wavSaveEnabled"]) setToggle(id, current[id]);
  $("timingDebug").checked = Boolean(current.timingDebug);
  for (const id of ["narrationEnabled", "characterTagPriority", "removeMarkdown", "readUrls", "readCodeBlocks", "longTextEnabled"]) $(id).checked = Boolean(current[id]);
  for (const id of ["defaultVoice", "narratorVoice", "dialogueVoice"]) setVoiceOptions($(id), current[id]);
  $("unknownTagBehavior").value = current.unknownTagBehavior;
  $("dialoguePairs").value = (current.dialoguePairs || []).join("\n");
  $("excludedStrings").value = (current.excludedStrings || []).join("\n");
  $("unwantedUiStrings").value = (current.unwantedUiStrings || []).join("\n");
  $("longTextStatus").textContent = `長文分割: ${current.longTextEnabled ? "ON" : "OFF"}`;
  $("longTextSummary").textContent = `目安${current.longTextTargetSeconds}秒 / ${current.longTextMaxChars}文字。実際の分割はIrodori側で行います。`;
  $("wavSaveDirectory").value = current.wavSaveDirectory || "";
  $("wavSaveStatus").textContent = current.wavSaveEnabled
    ? `保存先: ${current.wavSaveDirectory || "未指定"}`
    : "WAV保存はOFFです。";
  renderPresets();
  if (rebuildRows) {
    renderMappingGroups();
    $("regexRows").replaceChildren();
    for (const item of current.regexRules || []) addRegex(item);
  }
}

function collect(validate = true) {
  const value = { ...current };
  for (const id of ["chatgptEnabled", "geminiEnabled", "museEnabled", "autoPlay", "wavSaveEnabled", "narrationEnabled", "characterTagPriority", "removeMarkdown", "readUrls", "readCodeBlocks", "longTextEnabled"]) value[id] = $(id).checked;
  value.timingDebug = $("timingDebug").checked;
  for (const id of ["defaultVoice", "narratorVoice", "dialogueVoice", "unknownTagBehavior"]) value[id] = $(id).value;
  value.wavSaveDirectory = $("wavSaveDirectory").value.trim();
  value.dialoguePairs = lines("dialoguePairs");
  value.excludedStrings = lines("excludedStrings");
  value.unwantedUiStrings = lines("unwantedUiStrings");
  value.characterMappings = [...document.querySelectorAll(".mapping-item")].map((row) => ({
    enabled: row.querySelector('[data-key="enabled"]').checked,
    tag: row.querySelector('[data-key="tag"]').value.trim().replace(/^【|】$/g, ""),
    voice: row.querySelector('[data-key="voice"]').value,
    manual: row.dataset.manual === "true"
  })).filter((item) => item.tag);
  value.regexRules = [...$("regexRows").children].map((row) => ({
    enabled: row.querySelector('[data-key="enabled"]').checked,
    pattern: row.querySelector('[data-key="pattern"]').value,
    flags: row.querySelector('[data-key="flags"]').value || "gu",
    replacement: row.querySelector('[data-key="replacement"]').value
  })).filter((item) => item.pattern);
  if (validate) for (const rule of value.regexRules) new RegExp(rule.pattern, rule.flags);
  return Processor.mergedSettings(value);
}

async function syncSettingsEditor() {
  if (!settingsEditorReady) return;
  const sequence = ++storageSyncSequence;
  let draft;
  try { draft = collect(false); } catch (_) { draft = current; }
  const remote = Processor.mergedSettings(await api.runtime.sendMessage({ type: "get-settings" }));
  if (sequence !== storageSyncSequence) return;
  const reconciled = Processor.reconcileSettingsEditor(remote, draft, lastSavedSettings || current);
  lastSavedSettings = remote;
  current = Processor.mergedSettings(reconciled.settings);
  render(current);
}

async function saveNow() {
  clearTimeout(saveTimer);
  try {
    const formValue = collect();
    if (formValue.wavSaveEnabled) {
      const checked = await validateWavSaveDirectory(formValue.wavSaveDirectory);
      if (!checked.valid) {
        formValue.wavSaveEnabled = false;
        $("wavSaveEnabled").checked = false;
        $("wavSaveStatus").textContent = checked.error || "保存先フォルダーが存在しません。";
        message($("wavSaveStatus").textContent, true);
      } else {
        $("wavSaveStatus").textContent = `保存先を確認しました: ${checked.path || formValue.wavSaveDirectory}`;
      }
    }
    const baseline = lastSavedSettings || current;
    const patch = Processor.settingsDelta(formValue, baseline);
    const savedDraft = Processor.mergedSettings({ ...baseline, ...patch });
    // The background stores changed fields only and three-way merges complex
    // mappings/presets against this page's last-confirmed snapshot.
    const saved = await api.runtime.sendMessage({ type: "save-settings", patch, base: baseline });
    const laterEdits = Processor.settingsDelta(collect(false), savedDraft);
    lastSavedSettings = Processor.mergedSettings(saved || { ...baseline, ...patch });
    current = Processor.mergedSettings(Processor.mergeSettingsPatch(lastSavedSettings, laterEdits, savedDraft));
    $("saveState").textContent = "保存済み";
    setTimeout(() => { $("saveState").textContent = "設定は自動保存されます"; }, 1200);
  } catch (error) {
    $("saveState").textContent = `保存失敗: ${error.message}`;
    message(error.message, true);
  }
}

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 250);
}

async function refreshVoices() {
  try {
    const result = await api.runtime.sendMessage({ type: "voices" });
    voices = result.voices || [];
    current.cachedVoices = voices;
    const ids = voices.map((voice) => voice.id);
    if (!ids.includes(current.defaultVoice)) current.defaultVoice = ids.includes("yui") ? "yui" : (ids[0] || current.defaultVoice);
    fillVoiceSelects();
    renderMappingGroups();
    return result.source;
  } catch (_) {
    voices = Array.isArray(current.cachedVoices) ? current.cachedVoices : [];
    fillVoiceSelects();
    renderMappingGroups();
    return "unavailable";
  }
}

async function refreshApi() {
  const card = $("apiCard");
  card.className = "card api checking";
  $("apiState").textContent = "API確認中";
  let runtimeStatus = null;
  try {
    runtimeStatus = await api.runtime.sendMessage({ type: "status" });
    lastEngineStatusRevision = runtimeStatus.engineStatusRevision ?? lastEngineStatusRevision;
  } catch (_) {}
  const transition = Boolean(runtimeStatus && (runtimeStatus.engineTransition || runtimeStatus.engineStarting));
  const apiMode = (runtimeStatus && runtimeStatus.apiMode) || current.apiMode;
  if (apiMode === "custom") {
    const endpoint = (runtimeStatus && runtimeStatus.endpoint) || Processor.apiUrlForSettings(current);
    $("nativeState").textContent = "既存APIモード（Native Host不要）";
    $("apiStart").disabled = true;
    $("apiStop").disabled = true;
    $("apiRefresh").disabled = false;
    try {
      const health = await api.runtime.sendMessage({ type: "health" });
      card.className = "card api connected";
      $("apiState").textContent = "既存Irodori-TTS API 接続済み";
      $("apiGuide").textContent = `${endpoint} / ${health.backend || "API"}`;
      await refreshVoices();
    } catch (error) {
      card.className = "card api disconnected";
      $("apiState").textContent = "既存Irodori-TTS API 未接続";
      $("apiGuide").textContent = `${endpoint}（${error.message}）`;
    }
    return;
  }
  const selectedEngine = (runtimeStatus && (runtimeStatus.requestedEngine || runtimeStatus.storageEngine || runtimeStatus.engine)) || current.engine;
  const selectedLabel = selectedEngine === "voice_stable" ? "声質安定版" : "通常版";
  let statuses = null;
  try { statuses = await api.runtime.sendMessage({ type: "engine-health" }); } catch (_) {}
  const selectedState = statuses?.[selectedEngine] || ApiState.normalize(selectedEngine, {});
  if (statuses?.standard && statuses?.voice_stable) {
    $("standardHealth").textContent = `通常版: ${ApiState.text(statuses.standard)}（8765）`;
    $("stableHealth").textContent = `声質安定版: ${ApiState.text(statuses.voice_stable)}（共通入口8765）`;
  }
  if (transition || (selectedState.state === "starting" && selectedState.startupActive)) {
    for (const id of ["apiStart", "apiStop", "apiRefresh"]) $(id).disabled = true;
    card.className = "card api checking";
    $("apiState").textContent = `${selectedLabel} 起動中`;
    $("apiGuide").textContent = `${runtimeStatus.endpoint || Processor.apiUrlForEngine(selectedEngine)} に接続しています…`;
  }
  let helper = null;
  try {
    helper = await api.runtime.sendMessage({ type: "native-control", command: "status", engine: selectedEngine });
    $("nativeState").textContent = "ローカルヘルパー接続済み";
    if (transition) {
      $("apiStart").disabled = true;
      $("apiStop").disabled = true;
    }
  } catch (_) {
    $("nativeState").textContent = "ローカルヘルパー未導入";
    $("apiStart").disabled = true;
    $("apiStop").disabled = true;
  }
  if (transition || (selectedState.state === "starting" && selectedState.startupActive)) return;
  if (selectedState.state === "connected") try {
    const health = await api.runtime.sendMessage({ type: "health" });
    card.className = "card api connected";
    $("apiState").textContent = `${selectedLabel} 接続済み`;
    $("apiGuide").textContent = `${health.backend} / ${health.device} / ${health.model}`;
    $("apiStart").disabled = true;
    $("apiStop").disabled = false;
    await refreshVoices();
  } catch (_) {
    card.className = "card api disconnected";
    $("apiState").textContent = `${selectedLabel} 起動失敗`;
    $("apiGuide").textContent = helper ? "「API起動」を押すと音声モデルを常駐起動します。" : "ローカルヘルパーを導入するとFirefoxから起動できます。";
    $("apiStart").disabled = !helper;
    $("apiStop").disabled = true;
  } else if (selectedState.state === "error") {
    card.className = "card api disconnected";
    $("apiState").textContent = `${selectedLabel} 起動失敗`;
    $("apiGuide").textContent = selectedState.error || "起動処理に失敗しました。";
    $("apiStart").disabled = !helper;
    $("apiStop").disabled = true;
  } else if (selectedState.state === "starting") {
    card.className = "card api checking";
    $("apiState").textContent = `${selectedLabel} 起動中`;
    $("apiGuide").textContent = "backendは応答していますが、API health待ちです。必要なら起動を再試行できます。";
    $("apiStart").disabled = !helper;
    $("apiStop").disabled = true;
  } else {
    card.className = "card api disconnected";
    $("apiState").textContent = `${selectedLabel} 停止中`;
    $("apiGuide").textContent = helper ? "「API起動」を押すと音声モデルを常駐起動します。" : "ローカルヘルパーを導入するとFirefoxから起動できます。";
    $("apiStart").disabled = !helper;
    $("apiStop").disabled = true;
  }
}

async function refreshEngineHealth() {
  try {
    const result = await api.runtime.sendMessage({ type: "engine-health" });
    $("standardHealth").textContent = `通常版: ${ApiState.text(result.standard)}（8765）`;
    $("stableHealth").textContent = `声質安定版: ${ApiState.text(result.voice_stable)}（共通入口8765）`;
  } catch (_) {
    $("standardHealth").textContent = "通常版: 確認失敗";
    $("stableHealth").textContent = "声質安定版: 確認失敗";
  }
}

async function exportSettings() {
  const value = await api.runtime.sendMessage({ type: "export-settings" });
  const blob = new Blob([`${JSON.stringify(value, null, 2)}\n`], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `irodori-voice-reader-settings-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  $("settingsTransferStatus").textContent = "Exportしました。";
}

async function importSettings(file) {
  current = Processor.mergedSettings(await api.runtime.sendMessage({ type: "import-settings", settings: JSON.parse(await file.text()) }));
  lastSavedSettings = current;
  render(current);
  $("settingsTransferStatus").textContent = "Importしました。";
}

async function controlApi(command) {
  for (const id of ["apiStart", "apiStop", "apiRefresh"]) $(id).disabled = true;
  message(command === "start" ? "Irodori APIを起動しています…" : "Irodori APIを停止しています…");
  try {
    const runtimeStatus = await api.runtime.sendMessage({ type: "status" });
    if (runtimeStatus.apiMode === "custom" || current.apiMode === "custom") {
      throw new Error("既存Irodori-TTS API使用中はAPIの起動・停止を行いません。");
    }
    if (runtimeStatus.engineTransition || runtimeStatus.engineStarting) {
      throw new Error("生成エンジン切替中はAPI操作を実行できません。切替完了を待ってください。");
    }
    const engine = runtimeStatus.storageEngine || runtimeStatus.engine || current.engine;
    const helper = await api.runtime.sendMessage({ type: "native-control", command: "status", engine });
    const helperState = helper.engines?.[engine];
    if (command === "start" && (helperState?.state === "connected" || helperState?.state === "starting" || helper.running || helper.currentEngine === engine)) {
      throw new Error("選択中のAPIは既に稼働しています。");
    }
    if (command === "stop" && !helper.running && !helper.backendRunning) {
      throw new Error("選択中のAPIは既に停止しています。");
    }
    const result = command === "start"
      ? await api.runtime.sendMessage({ type: "native-control", command: "start", engine })
      : await api.runtime.sendMessage({ type: "native-control", command: "stop", engine });
    message(result.message || (command === "start" ? "APIを起動しました。" : "APIを停止しました。"));
  } catch (error) {
    message(error.message, true);
  } finally {
    await refreshApi();
    $("apiRefresh").disabled = false;
  }
}

async function selectEngine(engine) {
  if (current.apiMode === "custom") {
    message("既存Irodori-TTS API使用中は生成エンジンを切り替えられません。", true);
    return;
  }
  const previous = current.engine;
  pendingEngine = engine;
  message(`${engine === "voice_stable" ? "声質安定版" : "通常版"}へ切り替えています…`);
  try {
    const result = await api.runtime.sendMessage({ type: "switch-engine", engine });
    if (result && result.cancelled) {
      pendingEngine = null;
      render(current, false);
      return;
    }
    current = Processor.mergedSettings({ ...current, engine: result.engine, apiUrl: result.apiUrl });
    pendingEngine = null;
    render(current, false);
    message(result.message || "生成エンジンを切り替えました。");
    await refreshVoices();
  } catch (error) {
    pendingEngine = null;
    const input = document.querySelector(`input[name="engine"][value="${previous}"]`);
    if (input) input.checked = true;
    message(error.message, true);
  } finally {
    await refreshApi();
  }
}

async function selectApiMode(mode, customApiUrl = $("customApiUrl").value) {
  const previous = current;
  for (const input of document.querySelectorAll('input[name="apiMode"], input[name="engine"]')) input.disabled = true;
  const label = mode === "custom" ? "既存Irodori-TTS API" : "Voice Reader管理API";
  message(`${label}へ切り替えています…`);
  try {
    const result = await api.runtime.sendMessage({ type: "set-api-mode", apiMode: mode, customApiUrl });
    current = Processor.mergedSettings(result.settings || { ...current, apiMode: mode, customApiUrl });
    pendingEngine = null;
    render(current, false);
    message(mode === "custom" ? "既存APIモードを保存しました。接続確認を行ってください。" : "Voice Reader管理APIへ切り替えました。");
    await refreshApi();
    return true;
  } catch (error) {
    current = previous;
    render(current, false);
    message(error.message, true);
    return false;
  } finally {
    for (const input of document.querySelectorAll('input[name="apiMode"], input[name="engine"]')) input.disabled = current.apiMode === "custom" && input.name === "engine";
  }
}

async function refreshQueue(status) {
  status = status || await api.runtime.sendMessage({ type: "status" });
  $("queueCount").textContent = `キュー: ${status.queued + (status.current ? 1 : 0)}`;
  const labels = { playing_and_generating: "再生中・次チャンク生成中", playing: "再生中", generating: "生成中", paused: "停止中", idle: "待機中" };
  $("playState").textContent = labels[status.pipelineState] || (status.current ? "再生中" : "待機中");
  $("now").textContent = status.current ? `${voiceStatusLabel(status.current.voice)} / ${status.current.text}` : "再生待ちの音声はありません";
  $("generatingVoice").textContent = `生成中: ${voiceStatusLabel(status.generating && status.generating.voice)}`;
  $("playingVoice").textContent = `再生中: ${voiceStatusLabel(status.current && status.current.voice)}`;
  const timing = status.lastTimingSummary;
  $("timingSummary").hidden = !current.timingDebug || !timing;
  if (timing) $("timingSummary").textContent = `直近: lock待ち ${timing.lockWaitMs ?? "-"}ms / 生成 ${timing.lockHoldMs ?? "-"}ms / speaker retry ${timing.speakerRetries} / quality retry ${timing.audioQualityRetries}`;
  if (status.lastError) message(status.lastError, true);
}

document.querySelectorAll(".tabs button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.id === `tab-${button.dataset.tab}`));
}));

for (const id of ["chatgptEnabled", "geminiEnabled", "museEnabled", "autoPlay", "wavSaveEnabled"]) $(id).addEventListener("change", () => { setToggle(id, $(id).checked); scheduleSave(); });
$("timingDebug").addEventListener("change", scheduleSave);
$("openTimingLogFolder").addEventListener("click", async () => {
  try {
    await api.runtime.sendMessage({ type: "open-timing-log-folder" });
    message("計測ログフォルダーを開きました。");
  } catch (error) {
    message(`計測ログフォルダーを開けません: ${error.message}`, true);
  }
});
$("wavSaveDirectory").addEventListener("input", () => {
  $("wavSaveStatus").textContent = "保存先を確認してからWAV保存をONにしてください。";
});
$("wavSaveDirectory").addEventListener("change", scheduleSave);
$("chooseWavSaveDirectory").addEventListener("click", async () => {
  try {
    const selected = await api.runtime.sendMessage({ type: "native-control", command: "choose_dataset_directory" });
    if (!selected || selected.cancelled) return;
    if (!selected.valid || !selected.path) throw new Error(selected.error || "保存先フォルダーを確認できませんでした。");
    $("wavSaveDirectory").value = selected.path;
    $("wavSaveStatus").textContent = `保存先を確認しました: ${selected.path}`;
    await saveNow();
    message("WAV保存先を保存しました。", false);
  } catch (error) {
    message(`保存先を選択できません: ${error.message}`, true);
  }
});
$("validateWavSaveDirectory").addEventListener("click", async () => {
  const result = await validateWavSaveDirectory($("wavSaveDirectory").value);
  if (result.valid) {
    $("wavSaveStatus").textContent = `保存先を確認しました: ${result.path || $("wavSaveDirectory").value.trim()}`;
    message("WAV保存先を確認しました。", false);
  } else {
    $("wavSaveEnabled").checked = false;
    setToggle("wavSaveEnabled", false);
    $("wavSaveStatus").textContent = result.error || "保存先フォルダーが存在しません。";
    message($("wavSaveStatus").textContent, true);
    await saveNow();
  }
});
for (const id of ["defaultVoice", "narratorVoice", "dialogueVoice", "narrationEnabled", "characterTagPriority", "unknownTagBehavior", "removeMarkdown", "readUrls", "readCodeBlocks", "longTextEnabled", "dialoguePairs", "excludedStrings", "unwantedUiStrings"]) {
  $(id).addEventListener("change", scheduleSave);
  if (["dialoguePairs", "excludedStrings", "unwantedUiStrings"].includes(id)) $(id).addEventListener("input", scheduleSave);
}
for (const input of document.querySelectorAll('input[name="engine"]')) input.addEventListener("change", () => {
  if (input.checked && input.value !== (pendingEngine || current.engine)) selectEngine(input.value);
});
for (const input of document.querySelectorAll('input[name="apiMode"]')) input.addEventListener("change", () => {
  if (input.checked && input.value !== current.apiMode) selectApiMode(input.value);
});
$("customApiUrl").addEventListener("change", () => {
  if (current.apiMode === "custom") selectApiMode("custom", $("customApiUrl").value);
});
$("checkCustomApi").addEventListener("click", async () => {
  try {
    const switched = current.apiMode === "custom" || await selectApiMode("custom", $("customApiUrl").value);
    if (switched) await refreshApi();
  } catch (error) { message(error.message, true); }
});
$("preset").addEventListener("change", async () => {
  try {
    current = await api.runtime.sendMessage({ type: "apply-preset", preset: $("preset").value });
    lastSavedSettings = Processor.mergedSettings(current);
    render(current);
    message(`「${$("preset").selectedOptions[0].textContent}」を適用しました。`);
  } catch (error) { message(error.message, true); }
});
$("addMapping").addEventListener("click", () => addMapping({ enabled: true, tag: "", voice: current.defaultVoice, manual: true }));
$("addRegex").addEventListener("click", () => addRegex());
$("apiStart").addEventListener("click", () => controlApi("start"));
$("apiStop").addEventListener("click", () => controlApi("stop"));
$("apiRefresh").addEventListener("click", refreshApi);
$("exportSettings").addEventListener("click", () => exportSettings().catch((error) => message(error.message, true)));
$("importSettings").addEventListener("change", () => {
  const file = $("importSettings").files[0];
  if (file) importSettings(file).catch((error) => message(`Import失敗: ${error.message}`, true));
});
$("readSelection").addEventListener("click", async () => {
  try {
    const result = await api.runtime.sendMessage({ type: "read-active-selection" });
    message(`${result.queued}件を読み上げキューへ追加しました。`);
    await refreshQueue();
  } catch (error) { message(/選択/.test(error.message) ? "読み上げる文章が選択されていません。" : error.message, true); }
});
for (const type of ["stop", "resume", "skip", "clear"]) $(type).addEventListener("click", () => api.runtime.sendMessage({ type }).then(refreshQueue).catch((error) => message(error.message, true)));
$("openOptions").addEventListener("click", () => api.runtime.openOptionsPage());
$("manageVoices").addEventListener("click", () => api.tabs.create({ url: api.runtime.getURL("options.html#voice-management") }));
$("toggleCharacterMappings").addEventListener("click", () => {
  const panel = $("characterMappingsPanel");
  const open = panel.hidden;
  panel.hidden = !open;
  $("toggleCharacterMappings").textContent = open ? "▲ 閉じる" : "▼ 開く";
  $("toggleCharacterMappings").setAttribute("aria-expanded", String(open));
});
$("toggleInactiveMappings").addEventListener("click", () => {
  const panel = $("inactiveMappingRows");
  const open = panel.hidden;
  panel.hidden = !open;
  $("toggleInactiveMappings").textContent = `${open ? "▼" : "▶"} 未使用のVoice ${$("inactiveMappingCount").textContent}`;
  $("toggleInactiveMappings").setAttribute("aria-expanded", String(open));
});

api.runtime.onMessage.addListener((event) => {
  if (event.type === "timing-log-updated") renderTimingLogStatus(event);
  if (event.type === "queue-status") {
    refreshQueue(event.status);
    if (event.status && event.status.requestedEngine) pendingEngine = event.status.requestedEngine;
    else if (event.status && !event.status.engineTransition && !event.status.engineStarting) pendingEngine = null;
    if (event.status && event.status.engineStatusRevision !== lastEngineStatusRevision) {
      lastEngineStatusRevision = event.status.engineStatusRevision;
      refreshApi().catch(() => {});
    }
  }
});
api.storage.onChanged.addListener(async (changes, area) => {
  if (area !== "local" || !changes) return;
  const settingKeys = new Set([...Object.keys(Processor.DEFAULT_SETTINGS), "voiceDisplayNames"]);
  if (!Object.keys(changes).some((key) => settingKeys.has(key))) return;
  try { await syncSettingsEditor(); }
  catch (error) { message(`他画面の設定を読み込めません: ${error.message}`, true); }
});

(async () => {
  const initialized = await api.runtime.sendMessage({ type: "initialize" });
  current = initialized.settings;
  lastSavedSettings = Processor.mergedSettings(initialized.settings);
  voices = initialized.voices || current.cachedVoices || [];
  render(current);
  settingsEditorReady = true;
  if (current.wavSaveEnabled) {
    const checked = await validateWavSaveDirectory(current.wavSaveDirectory);
    if (!checked.valid) {
      current.wavSaveEnabled = false;
      $("wavSaveEnabled").checked = false;
      setToggle("wavSaveEnabled", false);
      const saved = await api.runtime.sendMessage({ type: "save-settings", patch: { wavSaveEnabled: false }, base: lastSavedSettings });
      lastSavedSettings = Processor.mergedSettings(saved || { ...lastSavedSettings, wavSaveEnabled: false });
      $("wavSaveStatus").textContent = checked.error || "保存先フォルダーが存在しないためWAV保存をOFFにしました。";
      message($("wavSaveStatus").textContent, true);
    }
  }
  await Promise.all([refreshApi(), refreshQueue(), refreshEngineHealth()]);
  await refreshTimingLogStatus();
})().catch((error) => message(`サイドバー初期化に失敗しました: ${error.message}`, true));
