"use strict";

const api = typeof browser !== "undefined" ? browser : chrome;
const trackedSite = ["chatgpt.com", "chat.openai.com", "gemini.google.com", "muse.ai", "www.muse.ai"].includes(location.hostname);
const pending = new Map();
const seen = new Set();
const candidateLogKeys = new WeakMap();
const geminiNodeIds = new WeakMap();
const streamingNodes = new WeakSet();
const recentTextCandidates = new Map();
const RECENT_TEXT_WINDOW_MS = 3000;
// The timer is re-armed whenever the answer text changes and the callback
// rechecks the latest text/streaming state.  Keeping it aligned with the
// baseline settle window removes an unnecessary fixed second of latency while
// retaining the delayed-history protection.
const STABLE_WAIT_MS = 1200;
// Both sites can append historical assistant nodes asynchronously after load
// or SPA navigation.  The settle window only baselines non-streaming nodes.
const BASELINE_SETTLE_MS = 1200;
let initialized = false;
let currentUrl = location.href;
let liveSettings = { chatgptEnabled: false, geminiEnabled: false, timingDebug: false };
let baselineActive = true;
let baselineTimer = 0;
let geminiNodeSequence = 0;

function timingNow() {
  return typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now();
}

function timingLog(event, fields = {}) {
  if (!liveSettings.timingDebug) return;
  const record = {
    event,
    timestamp: new Date().toISOString(),
    perf_ms: Number(timingNow().toFixed(3)),
    ...fields
  };
  console.info("[Irodori][timing]", record);
  if (typeof api.runtime.sendMessage === "function") {
    api.runtime.sendMessage({ type: "timing-log-event", event: record }).catch(() => {});
  }
}

function showToast(message, isError = false) {
  let toast = document.getElementById("irodori-reader-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "irodori-reader-toast";
    Object.assign(toast.style, {
      position: "fixed", right: "18px", bottom: "18px", zIndex: "2147483647",
      maxWidth: "420px", padding: "12px 16px", borderRadius: "10px",
      color: "white", font: "14px/1.5 system-ui, sans-serif", boxShadow: "0 8px 28px #0005",
      transition: "opacity .2s"
    });
    document.documentElement.appendChild(toast);
  }
  toast.style.background = isError ? "#b42318" : "#3b2d73";
  toast.textContent = String(message);
  toast.style.opacity = "1";
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => { toast.style.opacity = "0"; }, isError ? 9000 : 3500);
}

function siteAdapter() {
  const host = location.hostname;
  if (host === "chatgpt.com" || host === "chat.openai.com") {
    let candidateCache = null;
    return {
      name: "chatgpt",
      enabledKey: "chatgptEnabled",
      candidates() {
        const turns = [...document.querySelectorAll('article[data-testid^="conversation-turn-"]')]
          .filter((node) => node.querySelector('[data-message-author-role="assistant"]'));
        if (turns.length) {
          candidateCache = turns;
          return turns;
        }
        const roles = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
          .filter((node) => !node.closest('article[data-testid^="conversation-turn-"]'));
        if (roles.length) {
          candidateCache = roles;
          return roles;
        }
        // Chrome's current ChatGPT UI labels assistant headings and gives
        // each answer its own search/selection unit instead of an article.
        // Keep user turns, action controls and nested markdown out of the queue.
        const modern = [...new Set([...document.querySelectorAll('[data-conversation-role="assistant"]')]
          .map((node) => node.closest('[data-chatgpt-search-unit-key], [data-content-search-unit-key]') || node.parentElement)
          .filter((node) => node && node.querySelector('[data-chatgpt-selection-message-id], [data-markdown-text-style="assistant-message"]')))];
        candidateCache = modern;
        return candidateCache;
      },
      text(node) {
        const body = node.matches('[data-message-author-role="assistant"]') ? node : node.querySelector('[data-message-author-role="assistant"]');
        const content = body ? (body.querySelector(".markdown") || body.querySelector('[class*="prose"]') || body)
          : (node.querySelector('[data-chatgpt-selection-message-id]') || node.querySelector('[data-markdown-text-style="assistant-message"]'));
        return content ? content.innerText.trim() : "";
      },
      messageId(node) {
        const turn = node.closest("article[data-testid^='conversation-turn-']") || node;
        const modern = node.querySelector('[data-chatgpt-selection-message-id]');
        return turn.getAttribute("data-message-id") || turn.getAttribute("data-turn-id") || turn.getAttribute("data-testid")
          || modern?.getAttribute("data-chatgpt-selection-message-id")
          || node.getAttribute("data-chatgpt-search-message-ids")?.trim().split(/\s+/)[0]
          || node.getAttribute("data-chatgpt-search-unit-key") || node.getAttribute("data-content-search-unit-key") || "";
      },
      conversationId() {
        return location.pathname.match(/\/c\/([^/]+)/)?.[1] || location.pathname;
      },
      streaming(node) {
        if (node && node.matches && node.matches('[aria-busy="true"], [data-is-streaming="true"]')) return true;
        const modernTurn = node.closest('[data-talvt-turn-state]');
        if (modernTurn) return modernTurn.getAttribute("data-talvt-turn-state") !== "complete";
        const stopButton = document.querySelector('[data-testid="stop-button"], button[aria-label*="Stop generating" i], button[aria-label*="生成を停止"]');
        if (!stopButton) return false;
        const turns = candidateCache || this.candidates();
        return turns[turns.length - 1] === node;
      }
    };
  }
  if (host === "gemini.google.com") {
    let candidateCache = null;
    return {
      name: "gemini",
      enabledKey: "geminiEnabled",
      candidates() {
        const topLevel = [...new Set(document.querySelectorAll(
          "model-response, [data-test-id='model-response']"
        ))].filter((node) => {
          const owner = node.parentElement && node.parentElement.closest("model-response, [data-test-id='model-response']");
          return !owner || owner === node;
        });
        if (topLevel.length) {
          candidateCache = topLevel;
          return topLevel;
        }
        const fallback = [...new Set(document.querySelectorAll(
          ".model-response-text, .response-container .markdown-main-panel"
        ))].filter((node) => {
          const owner = node.parentElement && node.parentElement.closest("model-response, [data-test-id='model-response']");
          return !owner;
        });
        candidateCache = fallback;
        return fallback;
      },
      text(node) {
        const body = node.querySelector("message-content, .markdown-main-panel, .response-content") || node;
        return cleanGeminiText(body);
      },
      messageId(node) {
        const inner = node.querySelector("[data-message-id], [data-turn-id], [data-response-id]");
        const explicit = node.getAttribute("data-message-id") || node.getAttribute("data-turn-id") ||
          node.getAttribute("data-response-id") || node.id || inner?.getAttribute("data-message-id") ||
          inner?.getAttribute("data-turn-id") || inner?.getAttribute("data-response-id");
        return explicit || "";
      },
      conversationId() {
        return location.pathname.match(/\/app\/([^/]+)/)?.[1] || location.pathname;
      },
      streaming(node) {
        if (node.querySelector("mat-progress-spinner, .loading, [aria-busy=true]")) return true;
        const stopButton = document.querySelector('button[aria-label*="Stop" i], button[aria-label*="停止"]');
        if (!stopButton) return false;
        const candidates = candidateCache || this.candidates();
        return candidates[candidates.length - 1] === node;
      }
    };
  }
  return null;
}

function domDescriptor(node) {
  if (!node) return "unknown";
  const tag = String(node.tagName || node.nodeName || "unknown").toLowerCase();
  const id = node.id ? `#${node.id}` : "";
  const classes = node.className && typeof node.className === "string"
    ? `.${node.className.trim().split(/\s+/).filter(Boolean).slice(0, 3).join(".")}` : "";
  return `${tag}${id}${classes}`;
}

function logCandidate(adapter, node, text, phase = "detected", details = {}) {
  const old = candidateLogKeys.get(node);
  const key = `${phase}|${text}`;
  if (old === key) return;
  candidateLogKeys.set(node, key);
  const conversationId = adapter.conversationId();
  digest(text).then((hash) => {
    console.info("[Irodori][adapter]", {
      phase,
      site: adapter.name,
      conversationId,
      messageId: stableMessageId(adapter, node, hash),
      textHash: hash,
      dom: domDescriptor(node),
      selector: node.getAttribute && (node.getAttribute("data-test-id") || node.getAttribute("data-testid") || ""),
      ...details
    });
  }).catch((error) => console.warn("[Irodori][adapter] hash failed", error));
}

function cleanGeminiText(node) {
  if (!node) return "";
  const copy = node.cloneNode(true);
  copy.querySelectorAll([
    "[hidden]", "[aria-hidden='true']", "[role='presentation']", "[role='img']",
    ".sr-only", ".visually-hidden", ".cdk-visually-hidden", "[class*='sr-only']",
    "[class*='visually-hidden']"
  ].join(",")).forEach((element) => element.remove());
  copy.querySelectorAll("[aria-label]").forEach((element) => {
    const label = String(element.getAttribute("aria-label") || "");
    if (/発言|回答|返答|response|answer|says|said/i.test(label) &&
        element.textContent.trim() === label.trim()) element.remove();
  });
  const metaLine = /^(?:.+?)(?:が発言します|が回答します|が返答します|の回答|の返答|が応答します|が話します|(?:will )?(?:say|answer|respond)s?)\s*[。.!！]?$/i;
  const metaPrefix = /^(?:.+?)(?:が発言します|が回答します|が返答します|の回答|の返答|が応答します|が話します|(?:will )?(?:say|answer|respond)s?)\s*[:：-]?\s*/i;
  return String(copy.innerText || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .map((line) => line.replace(metaPrefix, "").trim())
    .filter((line) => line && !metaLine.test(line))
    .join("\n")
    .trim();
}

async function digest(value) {
  const bytes = new TextEncoder().encode(`${location.pathname}\n${value}`);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function geminiNodeId(node) {
  let id = geminiNodeIds.get(node);
  if (!id) {
    geminiNodeSequence += 1;
    id = `node-${geminiNodeSequence}`;
    geminiNodeIds.set(node, id);
  }
  return id;
}

function stableMessageId(adapter, node, hash) {
  const raw = adapter.messageId(node);
  if (adapter.name !== "gemini") return raw || `text-${hash.slice(0, 16)}`;
  return `gemini-${raw || geminiNodeId(node)}-${hash}`;
}

function armBaseline() {
  const adapter = siteAdapter();
  if (!adapter) {
    baselineActive = false;
    clearTimeout(baselineTimer);
    return;
  }
  baselineActive = true;
  clearTimeout(baselineTimer);
  baselineTimer = setTimeout(() => {
    if (siteAdapter()?.name !== adapter.name) return;
    baselineActive = false;
    console.info("[Irodori][adapter]", {
      phase: "baseline_settle_complete",
      site: adapter.name,
      reason: "settle_window_elapsed"
    });
    scan(false).catch((error) => showToast(`回答検出に失敗しました。（${error.message}）`, true));
  }, BASELINE_SETTLE_MS);
}

async function rescanAfterContextChange() {
  const adapter = siteAdapter();
  clearTimeout(baselineTimer);
  if (adapter) {
    baselineActive = true;
  }
  await scan(true);
  if (adapter) armBaseline();
}

async function remember(key, hash) {
  seen.add(key);
  seen.add(hash);
  const stored = await api.storage.local.get({ irodoriSeenHashes: [], irodoriSeenAnswerKeys: [] });
  const hashes = [hash, ...(stored.irodoriSeenHashes || []).filter((value) => value !== hash)].slice(0, 300);
  const keys = [key, ...(stored.irodoriSeenAnswerKeys || []).filter((value) => value !== key)].slice(0, 300);
  await api.storage.local.set({ irodoriSeenHashes: hashes, irodoriSeenAnswerKeys: keys });
}

function schedule(node, adapter, reason = "new_or_changed_answer") {
  const text = adapter.text(node);
  if (!text || text.length < 2) return;
  timingLog("answer_detected", { site: adapter.name, dom: domDescriptor(node), text_length: text.length, reason });
  logCandidate(adapter, node, text, "detected");
  const old = pending.get(node);
  if (old && old.text === text) return;
  if (old) clearTimeout(old.timer);
  const stableWaitStarted = timingNow();
  timingLog("stable_wait_start", { site: adapter.name, dom: domDescriptor(node), wait_ms: STABLE_WAIT_MS, reason });
  const timer = setTimeout(async () => {
    try {
      timingLog("stable_wait_end", {
        site: adapter.name,
        dom: domDescriptor(node),
        elapsed_ms: Number((timingNow() - stableWaitStarted).toFixed(1)),
        reason
      });
      const latest = adapter.text(node);
      const streaming = adapter.streaming(node);
      timingLog("stable_gate", {
        site: adapter.name,
        dom: domDescriptor(node),
        latest_length: latest.length,
        text_unchanged: latest === text,
        streaming,
        connected: Boolean(node.isConnected),
        reason
      });
      if (!latest || latest !== text || streaming) {
        pending.delete(node);
        if (latest) schedule(node, adapter);
        return;
      }
      pending.delete(node);
      const hash = await digest(latest);
      timingLog("stable_hash_complete", { site: adapter.name, dom: domDescriptor(node), text_hash: hash });
      const messageId = stableMessageId(adapter, node, hash);
      const conversationId = adapter.conversationId();
      const answer = { messageId, conversationId, textHash: hash };
      const answerKey = `${adapter.name}|${conversationId}|${messageId}`;
      console.info("[Irodori][adapter]", {
        phase: "stable",
        site: adapter.name,
        conversationId,
        messageId,
        textHash: hash,
        dom: domDescriptor(node),
        answerKey,
        reason
      });
      const recentKey = `${adapter.name}|${conversationId}|${hash}`;
      const now = Date.now();
      const recent = recentTextCandidates.get(recentKey);
      if (adapter.name !== "gemini" && recent && now - recent.time < RECENT_TEXT_WINDOW_MS && recent.node !== node) {
        console.info("[Irodori][adapter]", {
          phase: "duplicate_text_skip",
          site: adapter.name,
          conversationId,
          messageId,
          textHash: hash,
          dom: domDescriptor(node),
          previousMessageId: recent.messageId,
          answerKey
        });
        return;
      }
      if (seen.has(answerKey) || (adapter.name !== "gemini" && !adapter.messageId(node) && seen.has(hash))) {
        console.info("[Irodori][adapter]", {
          phase: "dedupe_skip",
          site: adapter.name,
          conversationId,
          messageId,
          textHash: hash,
          dom: domDescriptor(node),
          answerKey
        });
        return;
      }
      recentTextCandidates.set(recentKey, { time: now, node, messageId });
      await remember(answerKey, hash);
      timingLog("remember_complete", { site: adapter.name, dom: domDescriptor(node), text_hash: hash });
      timingLog("enqueue", { site: adapter.name, conversation_id: conversationId, message_id: messageId, text_hash: hash });
      console.info("[Irodori][adapter]", {
        phase: "enqueue",
        site: adapter.name,
        conversationId,
        messageId,
        textHash: hash,
        dom: domDescriptor(node),
        answerKey
      });
      const result = await api.runtime.sendMessage({ type: "enqueue", text: latest, source: adapter.name, answer });
      console.info("[Irodori][adapter]", {
        phase: "enqueue_result",
        site: adapter.name,
        conversationId,
        messageId,
        textHash: hash,
        queued: Boolean(result && result.queued),
        duplicate: Boolean(result && result.duplicate),
        answerKey
      });
      if (result && result.queued) showToast(`${adapter.name === "chatgpt" ? "ChatGPT" : "Gemini"}の回答を読み上げキューへ追加しました。`);
    } catch (error) {
      timingLog("schedule_error", {
        site: adapter.name,
        dom: domDescriptor(node),
        stage: "stable_callback",
        error: String(error && error.message ? error.message : error)
      });
      showToast(error.message || "Irodori読み上げに失敗しました。", true);
    }
  }, STABLE_WAIT_MS);
  pending.set(node, { text, timer });
}

async function scan(markBaseline = false) {
  const adapter = siteAdapter();
  if (!adapter) return;
  if (!liveSettings[adapter.enabledKey]) return;
  // Capture streaming state before any hash await so a fast new answer is not baselined as history.
  const scanStarted = timingNow();
  const rawCandidates = adapter.candidates();
  const candidateListedAt = timingNow();
  const candidates = rawCandidates.map((node) => ({ node, text: adapter.text(node), streaming: adapter.streaming(node) }));
  timingLog("scan_candidates", {
    site: adapter.name,
    baseline: markBaseline,
    candidate_count: rawCandidates.length,
    candidate_list_ms: Number((candidateListedAt - scanStarted).toFixed(1)),
    text_extract_ms: Number((timingNow() - candidateListedAt).toFixed(1))
  });
  for (const { node, text, streaming } of candidates) {
    if (!text) continue;
    if (markBaseline) {
      const wasStreaming = streamingNodes.has(node);
      if (streaming) {
        streamingNodes.add(node);
        logCandidate(adapter, node, text, "streaming", { reason: "baseline_streaming_not_baselined" });
        continue;
      }
      if (wasStreaming) {
        streamingNodes.delete(node);
        timingLog("streaming_complete", { site: adapter.name, dom: domDescriptor(node), reason: "schedule_after_streaming" });
        logCandidate(adapter, node, text, "streaming_complete", { reason: "schedule_after_streaming" });
        schedule(node, adapter, "streaming_completed_new_answer");
        continue;
      }
      const hash = await digest(text);
      const id = stableMessageId(adapter, node, hash);
      seen.add(`${adapter.name}|${adapter.conversationId()}|${id}`);
      seen.add(hash);
      logCandidate(adapter, node, text, "baseline", { reason: "settle_existing_history" });
    } else {
      if (streaming) {
        streamingNodes.add(node);
        logCandidate(adapter, node, text, "streaming", { reason: "generation_in_progress" });
        continue;
      }
      if (streamingNodes.has(node)) {
        streamingNodes.delete(node);
        timingLog("streaming_complete", { site: adapter.name, dom: domDescriptor(node), reason: "schedule_after_streaming" });
        logCandidate(adapter, node, text, "streaming_complete", { reason: "schedule_after_streaming" });
      }
      schedule(node, adapter, "post_settle_candidate");
    }
  }
  timingLog("scan_complete", { site: adapter.name, baseline: markBaseline, candidate_count: candidates.length, elapsed_ms: Number((timingNow() - scanStarted).toFixed(1)) });
}

async function initialize() {
  const stored = await api.storage.local.get({ irodoriSeenHashes: [], irodoriSeenAnswerKeys: [], chatgptEnabled: false, geminiEnabled: false });
  liveSettings = { ...liveSettings, ...stored };
  for (const hash of stored.irodoriSeenHashes || []) seen.add(hash);
  for (const key of stored.irodoriSeenAnswerKeys || []) seen.add(key);
  await scan(true);
  initialized = true;
  const observer = new MutationObserver(() => {
    if (!initialized) return;
    const adapter = siteAdapter();
    scan(Boolean(adapter && baselineActive)).catch((error) => showToast(`回答検出に失敗しました。（${error.message}）`, true));
  });
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  armBaseline();
  setInterval(async () => {
    if (location.href !== currentUrl) {
      currentUrl = location.href;
      for (const item of pending.values()) clearTimeout(item.timer);
      pending.clear();
      await rescanAfterContextChange();
    }
  }, 1000);
}

api.runtime.onMessage.addListener((message) => {
  if (message.type === "get-selection") return Promise.resolve({ text: String(window.getSelection() || "").trim() });
  if (message.type === "show-error") showToast(message.message, true);
  return undefined;
});

api.storage.onChanged.addListener((changes, area) => {
  if (trackedSite) return; // The submit-aware watcher owns these sites.
  if (area !== "local") return;
  if (changes.timingDebug) liveSettings.timingDebug = Boolean(changes.timingDebug.newValue);
  let siteToggleChanged = false;
  for (const key of ["chatgptEnabled", "geminiEnabled"]) {
    if (changes[key]) {
      liveSettings[key] = Boolean(changes[key].newValue);
      siteToggleChanged = true;
    }
  }
  if (siteToggleChanged) {
    for (const item of pending.values()) clearTimeout(item.timer);
    pending.clear();
    rescanAfterContextChange().catch((error) => showToast(`設定反映に失敗しました。（${error.message}）`, true));
  }
});

if (trackedSite) {
  if (window.top === window) {
    const watcher = location.hostname === "muse.ai" || location.hostname === "www.muse.ai"
      ? globalThis.IvrMuseWatcher : globalThis.IvrChatgptWatcher;
    watcher.start({ api, doc: document, win: window,
      getAdapter: (settings) => globalThis.IvrAdapters.forPage(location, document, settings),
      request: (type, fields) => api.runtime.sendMessage({ type, ...fields }), hash: digest, toast: showToast });
  }
} else {
  initialize().catch((error) => showToast(`Irodori拡張の初期化に失敗しました。（${error.message}）`, true));
}
