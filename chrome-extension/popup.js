"use strict";
const api = browser;
const Processor = globalThis.IrodoriTextProcessor;
const $ = (id) => document.getElementById(id);
let popupSettings = Processor.mergedSettings();
let popupVoices = [];

function showMessage(value, error = false) {
  $("message").textContent = value || "";
  $("message").className = error ? "error" : "";
}

function updateToggle(id, checked) {
  $(id).checked = Boolean(checked);
  document.querySelector(`[data-for="${id}"]`).textContent = checked ? "ON" : "OFF";
}

function setOptions(select, values, selected) {
  select.replaceChildren(...values.map((item) => {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    option.selected = item.value === selected;
    return option;
  }));
  if (selected && !values.some((item) => item.value === selected)) {
    const option = document.createElement("option");
    option.value = selected;
    option.textContent = `${selected}（未確認）`;
    select.prepend(option);
    select.value = selected;
  }
}

function renderSettings(config) {
  popupSettings = Processor.mergedSettings(config);
  $("apiUrl").textContent = popupSettings.apiUrl;
  updateToggle("chatgptEnabled", popupSettings.chatgptEnabled);
  updateToggle("geminiEnabled", popupSettings.geminiEnabled);
  updateToggle("museEnabled", popupSettings.museEnabled);
  setOptions($("voice"), popupVoices.map((voice) => ({ value: voice.id, label: `${voice.name} — ${voice.id}` })), popupSettings.defaultVoice);
  const modes = Object.entries(Processor.BUILTIN_PRESETS).map(([id, preset]) => ({ value: `builtin:${id}`, label: preset.label }));
  for (const name of Object.keys(popupSettings.presets || {}).sort()) modes.push({ value: `custom:${name}`, label: name });
  setOptions($("mode"), modes, popupSettings.currentPreset || "builtin:chat");
}

async function checkHealth() {
  const card = $("apiCard");
  card.className = "api-card checking";
  $("apiState").textContent = "API接続確認中";
  try {
    const health = await api.runtime.sendMessage({ type: "health" });
    card.className = "api-card connected";
    $("apiState").textContent = "API接続済み";
    $("apiGuide").hidden = true;
    $("read").disabled = false;
    $("apiState").title = `${health.device || ""} ${health.model || ""}`.trim();
    return true;
  } catch (_) {
    card.className = "api-card disconnected";
    $("apiState").textContent = "API未接続";
    $("apiGuide").hidden = false;
    $("read").disabled = true;
    showMessage(`Irodori APIに接続できません。${popupSettings.apiUrl}`, true);
    return false;
  }
}

async function refreshVoices() {
  try {
    const result = await api.runtime.sendMessage({ type: "voices" });
    popupVoices = result.voices || [];
    const latest = await api.runtime.sendMessage({ type: "get-settings" });
    renderSettings(latest);
    if (result.source === "cache") showMessage("API未接続のため、前回取得したvoice一覧を表示しています。", true);
    else if (popupVoices.length) showMessage(`${popupVoices.length}件のvoiceを読み込みました。`);
  } catch (error) {
    popupVoices = Array.isArray(popupSettings.cachedVoices) ? popupSettings.cachedVoices : [];
    renderSettings(popupSettings);
    showMessage(error.message, true);
  }
}

async function refreshQueue(status) {
  status = status || await api.runtime.sendMessage({ type: "status" });
  $("queueCount").textContent = `キュー: ${status.queued + (status.current ? 1 : 0)}`;
  $("playState").textContent = status.current ? "再生中" : (status.paused ? "停止中" : "待機中");
  $("now").textContent = status.current ? `${status.current.voice} / ${status.current.text}` : "再生待ちの音声はありません";
  if (status.lastError) showMessage(status.lastError, true);
}

async function saveToggle(id) {
  const value = $(id).checked;
  await api.storage.local.set({ [id]: value });
  popupSettings[id] = value;
  updateToggle(id, value);
  showMessage(`${{ chatgptEnabled: "ChatGPT", geminiEnabled: "Gemini", museEnabled: "Muse" }[id]}自動読み上げを${value ? "ON" : "OFF"}にしました。`);
}

for (const id of ["chatgptEnabled", "geminiEnabled", "museEnabled"]) $(id).addEventListener("change", () => saveToggle(id).catch((error) => showMessage(error.message, true)));

$("voice").addEventListener("change", async () => {
  popupSettings.defaultVoice = $("voice").value;
  await api.storage.local.set({ defaultVoice: popupSettings.defaultVoice });
  showMessage(`voiceを ${popupSettings.defaultVoice} に変更しました。`);
});

$("mode").addEventListener("change", async () => {
  try {
    popupSettings = await api.runtime.sendMessage({ type: "apply-preset", preset: $("mode").value });
    renderSettings(popupSettings);
    showMessage(`「${$("mode").selectedOptions[0].textContent}」を適用しました。`);
  } catch (error) { showMessage(error.message, true); }
});

$("read").addEventListener("click", async () => {
  try {
    const result = await api.runtime.sendMessage({ type: "read-active-selection" });
    showMessage(`${result.queued}件を読み上げキューへ追加しました。`);
    await refreshQueue();
  } catch (error) {
    const text = /選択/.test(error.message) ? "読み上げる文章が選択されていません。" : error.message;
    showMessage(text, true);
  }
});

for (const type of ["stop", "resume", "skip", "clear"]) {
  $(type).addEventListener("click", () => api.runtime.sendMessage({ type }).then(refreshQueue).catch((error) => showMessage(error.message, true)));
}
$("reconnect").addEventListener("click", async () => { showMessage(""); if (await checkHealth()) await refreshVoices(); });
$("options").addEventListener("click", () => api.runtime.openOptionsPage());

api.runtime.onMessage.addListener((message) => {
  if (message.type === "queue-status") refreshQueue(message.status);
});
api.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  for (const [key, change] of Object.entries(changes)) popupSettings[key] = change.newValue;
  if (changes.cachedVoices && Array.isArray(changes.cachedVoices.newValue)) popupVoices = changes.cachedVoices.newValue;
  renderSettings(popupSettings);
});

(async () => {
  await globalThis.IrodoriInstallReady;
  const initialized = await api.runtime.sendMessage({ type: "initialize" });
  popupSettings = initialized.settings;
  popupVoices = initialized.voices || popupSettings.cachedVoices || [];
  renderSettings(popupSettings);
  await Promise.all([checkHealth(), refreshVoices(), refreshQueue()]);
})().catch((error) => showMessage(`初期化に失敗しました: ${error.message}`, true));
