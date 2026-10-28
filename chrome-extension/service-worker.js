/* Chrome MV3 lifecycle/API adapter; TTS and queue logic remain in background.js. */
"use strict";
const OFFSCREEN_URL = chrome.runtime.getURL("offscreen.html");
let creating = null;
let lastQueueStatus = "";

async function ensureBackground() {
  if (creating) return creating;
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"], documentUrls: [OFFSCREEN_URL]
  });
  if (contexts.length) return;
  if (creating) return creating;
  creating = chrome.offscreen.createDocument({
    url: "offscreen.html",
    // The unchanged queue retains WAV Blobs/object URLs during generation,
    // playback and pauses. AUDIO_PLAYBACK alone expires after 30 idle seconds.
    reasons: ["BLOBS"],
    justification: "Retain and play local TTS WAV Blobs and object URLs, including queued long-text chunks and paused playback."
  });
  try { await creating; } finally { creating = null; }
}

async function forward(message, sender = {}, event = null, args = null) {
  await ensureBackground();
  return chrome.runtime.sendMessage({
    __irodoriBridge: "background", message, sender, event, args
  });
}

async function invoke(method, args) {
  switch (method) {
    case "storage.local.get": return chrome.storage.local.get(...args);
    case "storage.local.set": return chrome.storage.local.set(...args);
    case "storage.local.remove": return chrome.storage.local.remove(...args);
    case "tabs.query": return chrome.tabs.query(...args);
    case "tabs.sendMessage": {
      // A Chrome side panel is an extension page, not a tab content script.
      // Deliver the unchanged Firefox queue notifications to that page too.
      if (args[1]?.type === "queue-status") {
        const signature = JSON.stringify(args[1].status);
        if (signature !== lastQueueStatus) {
          lastQueueStatus = signature;
          await chrome.runtime.sendMessage({ ...args[1], __irodoriBroadcast: true }).catch(() => {});
        }
      }
      return chrome.tabs.sendMessage(...args);
    }
    case "action.setBadgeText": return chrome.action.setBadgeText(...args);
    case "action.setBadgeBackgroundColor": return chrome.action.setBadgeBackgroundColor(...args);
    case "notifications.create": {
      const options = { ...args[0], iconUrl: chrome.runtime.getURL("icons/irodori.png") };
      return chrome.notifications.create(options);
    }
    case "runtime.sendNativeMessage": {
      if (args[0] !== "io.github.irodori.voice_reader") throw new Error("Unsupported Native Host");
      const request = args[1];
      // Firefox and Chrome share one already-running API/audio.cpp process.
      // Its last selected mode belongs to the other client; a fresh Chrome
      // profile must not replace that client's startup intent just to connect.
      if (request?.command === "start_engine_async" && request.reason === "extension_initialize") {
        const status = await chrome.runtime.sendNativeMessage("io.github.irodori.voice_reader.chrome", {
          command: "status", engine: request.engine
        });
        const engine = status?.engines?.[request.engine];
        if (status.ok && status.sharedApi && engine?.running && engine.api?.running && engine.backend?.running) {
          return { ...status, engine: request.engine, running: true, reusedSharedApi: true };
        }
      }
      return chrome.runtime.sendNativeMessage("io.github.irodori.voice_reader.chrome", args[1]);
    }
    case "runtime.broadcast":
      await chrome.runtime.sendMessage({ ...args[0], __irodoriBroadcast: true }).catch(() => {});
      return null;
    default: throw new Error(`Unsupported Chrome bridge API: ${method}`);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id) return false;
  if (message?.__irodoriBridge === "background" || message?.__irodoriBroadcast) return false;
  let task;
  if (message?.__irodoriBridge === "rpc") {
    if (sender.url !== OFFSCREEN_URL) return false;
    task = invoke(message.method, message.args || []).then((value) => ({
      __irodoriResult: true, value: value ?? null
    }));
  } else {
    task = forward(message, { id: sender.id, tab: sender.tab, url: sender.url, frameId: sender.frameId });
  }
  task.then(sendResponse, (error) => sendResponse({
    __irodoriResult: true, error: { name: error.name, message: error.message }
  }));
  return true;
});

chrome.storage.onChanged.addListener((changes, area) => {
  forward(null, {}, "storage.onChanged", [changes, area]).catch(console.error);
});
chrome.contextMenus.onClicked.addListener((info, tab) => {
  forward(null, {}, "contextMenus.onClicked", [info, tab]).catch(console.error);
});
chrome.commands.onCommand.addListener((command) => {
  forward(null, {}, "commands.onCommand", [command]).catch(console.error);
});
chrome.action.onClicked.addListener((tab) => {
  // Chrome requires open() to run directly in the user-gesture handler.
  chrome.sidePanel.open({ windowId: tab.windowId }).catch(console.error);
});
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "irodori-read-selection", title: "Irodoriで読み上げ", contexts: ["selection"] });
  });
  ensureBackground().catch(console.error);
});
chrome.runtime.onStartup.addListener(() => ensureBackground().catch(console.error));
ensureBackground().catch(console.error);
