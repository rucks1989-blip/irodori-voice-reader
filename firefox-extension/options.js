"use strict";
const api = browser;
const Processor = globalThis.IrodoriTextProcessor;
const ApiState = globalThis.IrodoriApiState;
const $ = (id) => document.getElementById(id);
let current = Processor.mergedSettings();
let voices = [];
let configuredVoices = new Map();
let disabledAutoVoices = [];
let editingVoiceId = "";
let deletingVoice = null;
let engineStorageSyncSequence = 0;
let lastSavedSettings = null;
let settingsEditorReady = false;

const scalarIds = ["unknownTagBehavior", "longTextTargetSeconds", "longTextMaxChars", "wavSaveDirectory"];
const checkIds = ["chatgptEnabled", "geminiEnabled", "museEnabled", "autoPlay", "wavSaveEnabled", "timingDebug", "narrationEnabled", "characterTagPriority", "removeMarkdown", "readUrls", "readCodeBlocks", "longTextEnabled", "longTextPreserveDialogue", "longTextKeepCharacterTagsSeparate"];
const voiceIds = ["defaultVoice", "narratorVoice", "dialogueVoice"];

function setResult(text, ok = true) {
  $("apiResult").textContent = text;
  $("apiResult").className = `result ${ok ? "ok" : "error"}`;
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

async function validateWavSaveDirectory(directory) {
  const value = String(directory || "").trim();
  if (!value) return { valid: false, error: "保存先フォルダーを指定してください。" };
  try {
    return await api.runtime.sendMessage({ type: "native-control", command: "validate_dataset_directory", base_dir: value });
  } catch (error) {
    return { valid: false, error: error.message };
  }
}

async function validateWavSaveSetting(value) {
  if (!value.wavSaveEnabled) return value;
  const result = await validateWavSaveDirectory(value.wavSaveDirectory);
  if (result && result.valid) {
    $("wavSaveStatus").textContent = `保存先を確認しました: ${result.path || value.wavSaveDirectory}`;
    return value;
  }
  value.wavSaveEnabled = false;
  $("wavSaveEnabled").checked = false;
  $("wavSaveStatus").textContent = result && result.error ? result.error : "保存先フォルダーを指定してください。";
  setResult($("wavSaveStatus").textContent, false);
  return value;
}

function setVoiceOptions(select, selected) {
  // A narration tag is a control token; "narrator" is not a built-in Voice
  // ID.  When /voices is temporarily unavailable, show only real fallback
  // choices and preserve a saved role setting as an unverified value below.
  const known = voices.length ? voices : [
    { id: "yui", name: "yui" }, { id: "kurisu", name: "kurisu" }
  ];
  const values = [...known];
  if (selected && !values.some((voice) => voice.id === selected)) values.unshift({ id: selected, name: `${selected}（未確認）` });
  select.replaceChildren(...values.map((voice) => {
    const option = document.createElement("option");
    option.value = String(voice.id);
    option.textContent = `${voice.name} — ${voice.id}`;
    option.selected = voice.id === selected;
    return option;
  }));
  select.value = selected;
}

function fillVoiceSelects() {
  document.querySelectorAll("select.voice-select").forEach((select) => {
    const selected = select.value || select.dataset.selected || current[select.id] || current.defaultVoice;
    setVoiceOptions(select, selected);
  });
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
    console.error("[Irodori][firefox][options] DOM contract violation", { missing, script: "options.js" });
    setResult(`画面のHTMLとoptions.jsが一致しません: ${missing.join(", ")}`, false);
    return false;
  }
  const active = activeRows.children.length;
  const inactive = inactiveRows.children.length;
  activeCount.textContent = String(active);
  inactiveCount.textContent = String(inactive);
  empty.hidden = active !== 0;
  return true;
}

function addMapping(item = { tag: "", voice: current.defaultVoice, enabled: true }, target) {
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
  });
  row.querySelector('[data-key="tag"]').addEventListener("input", () => updateMappingSummary(row));
  select.addEventListener("change", () => updateMappingSummary(row));
  row.querySelector('[data-action="remove"]').addEventListener("click", () => { row.remove(); updateMappingGroups(); });
  row.querySelector('[data-action="remove"]').hidden = !item.manual;
  updateMappingSummary(row);
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

function addRegex(item = { pattern: "", flags: "gu", replacement: "", enabled: true }) {
  const row = $("regexTemplate").content.firstElementChild.cloneNode(true);
  for (const key of ["pattern", "flags", "replacement"]) row.querySelector(`[data-key="${key}"]`).value = item[key] || "";
  row.querySelector('[data-key="enabled"]').checked = item.enabled !== false;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => row.remove());
  $("regexRows").appendChild(row);
}

function renderApiMode() {
  const custom = current.apiMode === "custom";
  const modeInput = document.querySelector(`input[name="apiMode"][value="${custom ? "custom" : "managed"}"]`);
  if (modeInput) modeInput.checked = true;
  $("managedApiControls").hidden = custom;
  $("customApiControls").hidden = !custom;
  $("customApiUrl").value = current.customApiUrl || "";
  for (const input of document.querySelectorAll('input[name="engine"]')) input.disabled = custom;
}

function render(value) {
  current = Processor.mergedSettings(value);
  $("apiUrl").value = Processor.apiUrlForSettings(current);
  renderApiMode();
  const engineInput = document.querySelector(`input[name="engine"][value="${current.engine}"]`);
  if (engineInput) engineInput.checked = true;
  for (const id of scalarIds) $(id).value = current[id];
  for (const id of checkIds) $(id).checked = Boolean(current[id]);
  $("wavSaveStatus").textContent = current.wavSaveEnabled
    ? `保存先: ${current.wavSaveDirectory || "未指定"}`
    : "WAV保存はOFFです。";
  $("excludedStrings").value = (current.excludedStrings || []).join("\n");
  $("unwantedUiStrings").value = (current.unwantedUiStrings || []).join("\n");
  $("dialoguePairs").value = (current.dialoguePairs || []).join("\n");
  for (const id of voiceIds) {
    $(id).dataset.selected = current[id];
    setVoiceOptions($(id), current[id]);
  }
  renderMappingGroups();
  $("regexRows").replaceChildren();
  for (const item of current.regexRules || []) addRegex(item);
  renderPresetList();
  renderVoiceCards();
}

function lines(id) {
  return $(id).value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
}

function readForm(validate = true) {
  const value = { presets: current.presets || {}, currentPreset: current.currentPreset || "builtin:chat" };
  value.apiMode = current.apiMode;
  value.customApiUrl = current.customApiUrl;
  value.engine = document.querySelector('input[name="engine"]:checked')?.value || current.engine || "standard";
  for (const id of scalarIds) value[id] = id.startsWith("longText") ? Number($(id).value) : $(id).value.trim();
  for (const id of checkIds) value[id] = $(id).checked;
  for (const id of voiceIds) value[id] = $(id).value;
  value.excludedStrings = lines("excludedStrings");
  value.unwantedUiStrings = lines("unwantedUiStrings");
  value.dialoguePairs = lines("dialoguePairs");
  value.characterMappings = [...document.querySelectorAll(".mapping-item")].map((row) => ({
    enabled: row.querySelector('[data-key="enabled"]').checked,
    tag: row.querySelector('[data-key="tag"]').value.trim(),
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
  const sequence = ++engineStorageSyncSequence;
  let draft;
  try { draft = readForm(false); } catch (_) { draft = current; }
  const stored = await api.runtime.sendMessage({ type: "get-settings" });
  if (sequence !== engineStorageSyncSequence) return;
  const remote = Processor.mergedSettings(stored);
  const reconciled = Processor.reconcileSettingsEditor(remote, draft, lastSavedSettings || current);
  lastSavedSettings = remote;
  current = Processor.mergedSettings(reconciled.settings);
  render(current);
  setResult(current.apiMode === "custom" ? "既存Irodori-TTS APIを使用中" : `${current.engine === "voice_stable" ? "声質安定版" : "通常版"}を使用中`, true);
}

async function selectEngine(engine) {
  if (current.apiMode === "custom") {
    setResult("既存Irodori-TTS API使用中は生成エンジンを切り替えられません。", false);
    return;
  }
  const previous = current.engine;
  for (const input of document.querySelectorAll('input[name="engine"]')) input.disabled = true;
  setResult(`${engine === "voice_stable" ? "声質安定版" : "通常版"}へ切り替えています…`, true);
  try {
    const result = await api.runtime.sendMessage({ type: "switch-engine", engine });
    if (result && result.cancelled) {
      const input = document.querySelector(`input[name="engine"][value="${previous}"]`);
      if (input) input.checked = true;
      return;
    }
    current = Processor.mergedSettings({ ...current, engine: result.engine, apiUrl: result.apiUrl });
    render(current);
    await refreshVoices();
    setResult(`${result.label || (engine === "voice_stable" ? "声質安定版" : "通常版")} 接続済み`, true);
  } catch (error) {
    const input = document.querySelector(`input[name="engine"][value="${previous}"]`);
    if (input) input.checked = true;
    setResult(`切替失敗: ${error.message}`, false);
  } finally {
    for (const input of document.querySelectorAll('input[name="engine"]')) input.disabled = false;
  }
}

async function selectApiMode(mode, customApiUrl = $("customApiUrl").value) {
  const previous = current;
  for (const input of document.querySelectorAll('input[name="apiMode"], input[name="engine"]')) input.disabled = true;
  setResult(mode === "custom" ? "既存Irodori-TTS APIへ切り替えています…" : "Voice Reader管理APIへ切り替えています…", true);
  try {
    const result = await api.runtime.sendMessage({ type: "set-api-mode", apiMode: mode, customApiUrl });
    current = Processor.mergedSettings(result.settings || { ...current, apiMode: mode, customApiUrl });
    render(current);
    setResult(mode === "custom" ? "既存APIモードを保存しました。接続確認を行ってください。" : "Voice Reader管理APIへ切り替えました。", true);
    if (mode === "custom") await checkApiConnection();
    else await refreshVoices();
    return true;
  } catch (error) {
    current = previous;
    render(current);
    setResult(`API利用方法の変更に失敗しました: ${error.message}`, false);
    return false;
  } finally {
    for (const input of document.querySelectorAll('input[name="apiMode"], input[name="engine"]')) input.disabled = current.apiMode === "custom" && input.name === "engine";
  }
}

async function save() {
  try {
    const formValue = await validateWavSaveSetting(readForm());
    const baseline = lastSavedSettings || current;
    const patch = Processor.settingsDelta(formValue, baseline);
    const savedDraft = Processor.mergedSettings({ ...baseline, ...patch });
    // The engine is switched by background.switchEngine() only.  Saving this
    // page must not replay an older radio-button/apiUrl snapshot.
    const saved = await api.runtime.sendMessage({ type: "save-settings", patch, base: baseline });
    const after = readForm(false);
    const laterEdits = Processor.settingsDelta(after, savedDraft);
    lastSavedSettings = Processor.mergedSettings(saved || { ...baseline, ...patch });
    current = Processor.mergedSettings(Processor.mergeSettingsPatch(lastSavedSettings, laterEdits, savedDraft));
    $("saveState").textContent = "保存しました";
    setTimeout(() => { $("saveState").textContent = ""; }, 2500);
  } catch (error) {
    $("saveState").textContent = `保存失敗: ${error.message}`;
  }
}

async function refreshVoices() {
  try {
    const body = await api.runtime.sendMessage({ type: "voices" });
    voices = body.voices || [];
    await api.storage.local.set({ cachedVoices: voices });
    fillVoiceSelects();
    await refreshVoiceState();
    renderVoiceCards();
    setResult(`${voices.length}件のvoiceを取得しました。`, true);
  } catch (error) { setResult(`voice一覧を取得できません: ${error.message}`, false); }
}

async function refreshVoiceState() {
  try {
    const result = await api.runtime.sendMessage({ type: "native-control", command: "voice_state" });
    configuredVoices = new Map((result.voices || []).map((voice) => [voice.id, voice]));
    disabledAutoVoices = Array.isArray(result.disabledAutoVoices) ? result.disabledAutoVoices : [];
    const latest = Processor.mergedSettings(await api.runtime.sendMessage({ type: "get-settings" }));
    const voiceDisplayNames = { ...(latest.voiceDisplayNames || {}) };
    for (const voice of result.voices || []) {
      if (voice && voice.id && voice.name) voiceDisplayNames[String(voice.id)] = String(voice.name);
    }
    const patch = Processor.settingsDelta({ voiceDisplayNames }, latest);
    if (Object.keys(patch).length) {
      await api.runtime.sendMessage({ type: "save-settings", patch, base: latest });
    }
    current.voiceDisplayNames = voiceDisplayNames;
  } catch (_) {
    configuredVoices = new Map();
    disabledAutoVoices = [];
  }
  renderMappingGroups();
  const button = $("restoreAutoVoices");
  if (button) { button.hidden = !disabledAutoVoices.length; button.textContent = disabledAutoVoices.length ? `自動検出Voiceを再表示（${disabledAutoVoices.length}件）` : "自動検出Voiceを再表示"; }
}

function voiceTypeLabel(type) {
  if (type === "speaker_embedding") return "speaker.safetensors";
  if (type === "safetensors") return "safetensors（生成未対応）";
  return "WAV";
}

function renderVoiceCards() {
  if (!$(`voiceCards`)) return;
  const cards = voices.map((voice) => {
    const configVoice = configuredVoices.get(voice.id);
    const card = document.createElement("div");
    card.className = "voice-card";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = voice.name || voice.id;
    const details = document.createElement("p");
    details.textContent = `ID: ${voice.id}　種類: ${voiceTypeLabel(voice.type)}${voice.source === "auto" ? "　自動検出" : (configVoice ? (configVoice.managed ? "　管理コピー" : "　既存登録") : "　設定登録")}`;
    text.append(title, details);
    card.append(text);
    if (voice.source === "auto" || configVoice) {
      const actions = document.createElement("div");
      actions.className = "actions";
      if (configVoice) {
        const edit = document.createElement("button");
        edit.textContent = "変更";
        edit.addEventListener("click", () => showVoiceForm(voice, configVoice));
        actions.append(edit);
      }
      const remove = document.createElement("button");
      remove.textContent = voice.source === "auto" ? "一覧から外す" : "削除";
      remove.className = "danger";
      remove.addEventListener("click", () => showDeleteVoice(voice, configVoice));
      actions.append(remove);
      card.append(actions);
    }
    return card;
  });
  $("voiceCards").replaceChildren(...cards);
}

function showVoiceForm(voice = null, configVoice = null) {
  editingVoiceId = voice ? voice.id : "";
  $("voiceFormTitle").textContent = voice ? "Voiceを変更" : "Voiceを追加";
  $("voiceName").value = voice ? voice.name : "";
  $("voiceId").value = voice ? voice.id : "";
  $("voiceId").disabled = Boolean(voice);
  $("voiceFile").value = "";
  const type = voice ? voice.type : "reference_wav";
  const radio = document.querySelector(`input[name="voiceType"][value="${type}"]`);
  if (radio) radio.checked = true;
  $("voiceFileHint").textContent = voice ? "名前だけ変更する場合、ファイル選択は不要です。選択すると管理コピーを置き換えます。" : "登録時はファイルを選択してください。";
  $("registerVoice").textContent = voice ? "変更を保存" : "登録";
  $("voiceForm").hidden = false;
  $("deleteVoicePanel").hidden = true;
  $("voiceForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Voiceファイルを読み取れませんでした。"));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(file);
  });
}

async function registerVoice() {
  const id = $("voiceId").value.trim();
  const name = $("voiceName").value.trim();
  const file = $("voiceFile").files[0];
  let operation;
  if (!name || !id) throw new Error("表示名とVoice IDを入力してください。");
  if (!/^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$/.test(id)) throw new Error("Voice IDは半角英数字で始まる64文字以内（英数字・_・-）にしてください。");
  if (editingVoiceId && !file) {
    operation = await api.runtime.sendMessage({ type: "native-control", command: "voice_update", id, name });
  } else {
    if (!file) throw new Error("Voiceファイルを選択してください。");
    if (file.size > 64 * 1024 * 1024) throw new Error("Voiceファイルは64MB以下にしてください。");
    const voiceType = document.querySelector('input[name="voiceType"]:checked').value;
    const lower = file.name.toLowerCase();
    if (voiceType === "reference_wav" && !lower.endsWith(".wav")) throw new Error("Reference WAVには.wavを選択してください。");
    if (voiceType === "speaker_embedding" && !lower.endsWith(".speaker.safetensors")) throw new Error("Speaker Embeddingには.speaker.safetensorsを選択してください。");
    if (voiceType === "safetensors" && (!lower.endsWith(".safetensors") || lower.endsWith(".speaker.safetensors"))) throw new Error("Safetensorsには通常の.safetensorsを選択してください。");
    operation = await api.runtime.sendMessage({ type: "native-control", command: "voice_import", id, name, voiceType, fileName: file.name, dataBase64: await fileAsBase64(file) });
  }
  $("voiceForm").hidden = true;
  await refreshVoices();
  const reloadNote = operation?.apiReload?.status === "stopped" ? " API停止中のため、次回起動時に反映されます。" : "";
  setResult(`Voice「${name} / ${id}」を反映しました。${reloadNote}`, true);
}

function voiceReferences(id) {
  let value = current;
  try { value = readForm(); } catch (_) {}
  const references = [];
  if (value.defaultVoice === id) references.push("デフォルトVoice");
  if (value.narratorVoice === id) references.push("地の文のVoice");
  if (value.dialogueVoice === id) references.push("タグなし会話のVoice");
  for (const item of value.characterMappings || []) if (item.voice === id) references.push(`キャラクタータグ「${item.tag}」`);
  for (const [name, preset] of Object.entries(value.presets || {})) {
    if (JSON.stringify(preset).includes(`"${id}"`)) references.push(`プリセット「${name}」`);
  }
  return [...new Set(references)];
}

function showDeleteVoice(voice, configVoice) {
  deletingVoice = { voice, configVoice };
  const auto = voice.source === "auto";
  $("deleteVoiceTitle").textContent = auto ? `「${voice.name} / ${voice.id}」を一覧から外しますか？` : `「${voice.name} / ${voice.id}」を削除しますか？`;
  const references = voiceReferences(voice.id);
  $("deleteVoiceReferences").textContent = auto
    ? (references.length ? `現在参照中: ${references.join("、")}。一覧から外しても元のWAVやEmbeddingは削除しません。` : "一覧から外しても元のWAVやEmbeddingは削除しません。")
    : (references.length ? `現在参照中: ${references.join("、")}。削除後は各設定を別のVoiceへ変更してください。` : "このVoiceを参照している設定はありません。");
  $("deleteManagedLabel").hidden = auto || !configVoice?.managed;
  $("deleteManagedFile").checked = false;
  $("deleteVoicePanel").hidden = false;
  $("voiceForm").hidden = true;
  $("deleteVoicePanel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function confirmDeleteVoice() {
  if (!deletingVoice) return;
  const { voice, configVoice } = deletingVoice;
  const references = voiceReferences(voice.id);
  const confirmation = voice.source === "auto"
    ? `「${voice.name} / ${voice.id}」は ${references.join("、")} から参照されています。一覧から外しますか？`
    : `「${voice.name} / ${voice.id}」は ${references.join("、")} から参照されています。それでもVoice登録を削除しますか？`;
  if (references.length && !window.confirm(confirmation)) return;
  let operation;
  if (voice.source === "auto") {
    operation = await api.runtime.sendMessage({ type: "native-control", command: "voice_delete", id: voice.id, source: "auto", disableAuto: true });
  } else {
    operation = await api.runtime.sendMessage({ type: "native-control", command: "voice_delete", id: voice.id, source: "config", deleteManagedFile: configVoice?.managed && $("deleteManagedFile").checked });
  }
  deletingVoice = null;
  $("deleteVoicePanel").hidden = true;
  const base = Processor.mergedSettings(await api.runtime.sendMessage({ type: "get-settings" }));
  const patch = { characterMappings: (base.characterMappings || []).filter((item) => item.voice !== voice.id) };
  const saved = await api.runtime.sendMessage({ type: "save-settings", patch, base });
  lastSavedSettings = Processor.mergedSettings(saved || { ...base, ...patch });
  current = Processor.mergedSettings(lastSavedSettings);
  await refreshVoices();
  const reloadNote = operation?.apiReload?.status === "stopped" ? " API停止中のため、次回起動時に反映されます。" : "";
  const fileNote = operation?.retainedManagedFiles?.length
    ? " 管理Voiceファイルの削除に失敗し、ファイルは残っています。"
    : operation?.deletedManagedFile ? " 管理対象ファイルも削除しました。" : "";
  setResult((voice.source === "auto" ? `Voice「${voice.name} / ${voice.id}」を一覧から外しました。元ファイルは削除していません。` : `Voice「${voice.name} / ${voice.id}」の登録を削除しました。`) + fileNote + reloadNote, !operation?.retainedManagedFiles?.length);
}

async function restoreAutoVoices() {
  try {
    await api.runtime.sendMessage({ type: "native-control", command: "voice_delete", restoreAuto: true });
    await refreshVoices();
    setResult("自動検出Voiceを再表示しました。", true);
  } catch (error) { setResult(error.message, false); }
}

const builtins = Object.fromEntries(
  Object.entries(Processor.BUILTIN_PRESETS).map(([id, preset]) => [id, preset.settings])
);

function renderPresetList() {
  const selected = current.currentPreset || $("preset").value;
  $("preset").querySelectorAll('option[data-custom="true"]').forEach((node) => node.remove());
  for (const name of Object.keys(current.presets || {}).sort()) {
    const option = document.createElement("option");
    option.value = `custom:${name}`;
    option.dataset.custom = "true";
    option.textContent = name;
    $("preset").appendChild(option);
  }
  if ([...$("preset").options].some((option) => option.value === selected)) $("preset").value = selected;
}

async function checkApiConnection() {
  try {
    const health = await api.runtime.sendMessage({ type: "health" });
    setResult(`接続成功: ${health.backend || "API"} / ${health.device || ""} / ${health.model || ""}`, true);
    return true;
  } catch (error) { setResult(`接続できません: ${error.message}`, false); return false; }
}

async function refreshEngineHealth() {
  try {
    const result = await api.runtime.sendMessage({ type: "engine-health" });
    $("standardHealth").textContent = `通常版: ${ApiState.text(result.standard)}（8765）`;
    $("stableHealth").textContent = `声質安定版: ${ApiState.text(result.voice_stable)}（共通入口8765）`;
  } catch (error) {
    $("standardHealth").textContent = `通常版: 確認失敗`;
    $("stableHealth").textContent = `声質安定版: 確認失敗`;
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
  $("settingsTransferStatus").textContent = "設定JSONをExportしました。Voice実体は含まれません。";
}

async function importSettings(file) {
  const value = JSON.parse(await file.text());
  current = Processor.mergedSettings(await api.runtime.sendMessage({ type: "import-settings", settings: value }));
  lastSavedSettings = current;
  render(current);
  $("settingsTransferStatus").textContent = "設定をImportしました。既存設定は世代バックアップ済みです。";
}

$("checkApi").addEventListener("click", async () => { await save(); await Promise.all([checkApiConnection(), refreshEngineHealth()]); });
$("exportSettings").addEventListener("click", () => exportSettings().catch((error) => setResult(error.message, false)));
$("importSettings").addEventListener("change", () => {
  const file = $("importSettings").files[0];
  if (file) importSettings(file).catch((error) => setResult(`Import失敗: ${error.message}`, false));
});
for (const input of document.querySelectorAll('input[name="apiMode"]')) input.addEventListener("change", () => {
  if (input.checked && input.value !== current.apiMode) selectApiMode(input.value);
});
$("customApiUrl").addEventListener("change", () => {
  if (current.apiMode === "custom") selectApiMode("custom", $("customApiUrl").value);
});
$("checkCustomApi").addEventListener("click", async () => {
  const switched = current.apiMode === "custom" || await selectApiMode("custom", $("customApiUrl").value);
  if (switched) await checkApiConnection();
});
$("openTimingLogFolder").addEventListener("click", async () => {
  try {
    await api.runtime.sendMessage({ type: "open-timing-log-folder" });
    setResult("計測ログフォルダーを開きました。", true);
  } catch (error) {
    setResult(`計測ログフォルダーを開けません: ${error.message}`, false);
  }
});
$("wavSaveEnabled").addEventListener("change", () => save());
$("wavSaveDirectory").addEventListener("change", () => save());
$("wavSaveDirectory").addEventListener("input", () => {
  $("wavSaveStatus").textContent = "保存先を確認してからWAV保存をONにしてください。";
});
$("validateWavSaveDirectory").addEventListener("click", async () => {
  const result = await validateWavSaveDirectory($("wavSaveDirectory").value);
  if (result.valid) {
    $("wavSaveStatus").textContent = `保存先を確認しました: ${result.path || $("wavSaveDirectory").value.trim()}`;
    setResult("WAV保存先を確認しました。", true);
  } else {
    $("wavSaveStatus").textContent = result.error || "保存先フォルダーが存在しません。";
    setResult($("wavSaveStatus").textContent, false);
    $("wavSaveEnabled").checked = false;
    await save();
  }
});
$("chooseWavSaveDirectory").addEventListener("click", async () => {
  try {
    const selected = await api.runtime.sendMessage({ type: "native-control", command: "choose_dataset_directory" });
    if (!selected || selected.cancelled) return;
    if (!selected.valid || !selected.path) throw new Error(selected.error || "保存先フォルダーを確認できませんでした。");
    $("wavSaveDirectory").value = selected.path;
    $("wavSaveStatus").textContent = `保存先を確認しました: ${selected.path}`;
    setResult("WAV保存先を保存しました。", true);
    await save();
  } catch (error) {
    setResult(`保存先を選択できません: ${error.message}`, false);
  }
});
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
$("refreshVoices").addEventListener("click", async () => { await save(); await refreshVoices(); });
$("addMapping").addEventListener("click", () => addMapping({ enabled: true, tag: "", voice: current.defaultVoice, manual: true }));
$("addRegex").addEventListener("click", () => addRegex());
$("showVoiceForm").addEventListener("click", () => showVoiceForm());
$("restoreAutoVoices").addEventListener("click", restoreAutoVoices);
$("cancelVoiceForm").addEventListener("click", () => { $("voiceForm").hidden = true; });
$("registerVoice").addEventListener("click", () => registerVoice().catch((error) => setResult(error.message, false)));
$("cancelDeleteVoice").addEventListener("click", () => { $("deleteVoicePanel").hidden = true; deletingVoice = null; });
$("confirmDeleteVoice").addEventListener("click", () => confirmDeleteVoice().catch((error) => setResult(error.message, false)));
$("save").addEventListener("click", save);
for (const input of document.querySelectorAll('input[name="engine"]')) input.addEventListener("change", () => {
  if (input.checked && input.value !== current.engine) selectEngine(input.value);
});
$("loadPreset").addEventListener("click", () => {
  const [kind, name] = $("preset").value.split(":");
  const preset = kind === "builtin" ? builtins[name] : (current.presets || {})[name];
  if (preset) render({ ...readForm(), ...Processor.settingsPatchWithoutEngine(preset), currentPreset: $("preset").value, presets: current.presets });
});
$("savePreset").addEventListener("click", async () => {
  const name = $("presetName").value.trim();
  if (!name) { setResult("プリセット名を入力してください。", false); return; }
  const value = readForm();
  const { presets, ...withoutPresets } = value;
  const presetValue = Processor.settingsPatchWithoutEngine(withoutPresets);
  const base = Processor.mergedSettings(await api.runtime.sendMessage({ type: "get-settings" }));
  const patch = { presets: { ...(base.presets || {}), [name]: presetValue } };
  const saved = await api.runtime.sendMessage({ type: "save-settings", patch, base });
  lastSavedSettings = Processor.mergedSettings(saved || { ...base, ...patch });
  current = Processor.mergedSettings({ ...current, presets: lastSavedSettings.presets });
  renderPresetList();
  $("preset").value = `custom:${name}`;
  setResult(`プリセット「${name}」を保存しました。`, true);
});
$("deletePreset").addEventListener("click", async () => {
  const [kind, name] = $("preset").value.split(":");
  if (kind !== "custom") { setResult("組み込みプリセットは削除できません。", false); return; }
  const base = Processor.mergedSettings(await api.runtime.sendMessage({ type: "get-settings" }));
  const presets = { ...(base.presets || {}) };
  delete presets[name];
  const patch = { presets };
  const saved = await api.runtime.sendMessage({ type: "save-settings", patch, base });
  lastSavedSettings = Processor.mergedSettings(saved || { ...base, ...patch });
  current = Processor.mergedSettings({ ...current, presets: lastSavedSettings.presets });
  renderPresetList();
  setResult(`プリセット「${name}」を削除しました。`, true);
});

api.runtime.onMessage.addListener((event) => {
  if (event && event.type === "timing-log-updated") renderTimingLogStatus(event);
});

api.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes) return;
  const settingKeys = new Set([...Object.keys(Processor.DEFAULT_SETTINGS), "voiceDisplayNames"]);
  if (!Object.keys(changes).some((key) => settingKeys.has(key))) return;
  syncSettingsEditor().catch((error) => setResult(`他画面の設定を読み込めません: ${error.message}`, false));
});

(async () => {
  $("extensionVersion").textContent = api.runtime.getManifest().version;
  const stored = await api.storage.local.get({ ...Processor.DEFAULT_SETTINGS, cachedVoices: [] });
  voices = stored.cachedVoices || [];
  lastSavedSettings = Processor.mergedSettings(stored);
  render(stored);
  settingsEditorReady = true;
  if (current.wavSaveEnabled) {
    const checked = await validateWavSaveDirectory(current.wavSaveDirectory);
    if (!checked.valid) {
      current.wavSaveEnabled = false;
      $("wavSaveEnabled").checked = false;
      await api.runtime.sendMessage({ type: "save-settings", patch: { wavSaveEnabled: false }, base: lastSavedSettings });
      $("wavSaveStatus").textContent = checked.error || "保存先フォルダーが存在しないためWAV保存をOFFにしました。";
      setResult($("wavSaveStatus").textContent, false);
    }
  }
  await refreshVoices();
  await refreshEngineHealth();
  await refreshTimingLogStatus();
  if (location.hash === "#voice-management") $("voice-management").scrollIntoView();
})();
