/* Chrome-only API bridge. background.js is the byte-identical Firefox original. */
"use strict";
(() => {
  const events = new Map();
  const event = (name) => {
    const listeners = new Set();
    events.set(name, listeners);
    return {
      addListener: (listener) => listeners.add(listener),
      removeListener: (listener) => listeners.delete(listener),
      hasListener: (listener) => listeners.has(listener)
    };
  };
  const rpc = async (method, ...args) => {
    const response = await chrome.runtime.sendMessage({ __irodoriBridge: "rpc", method, args });
    if (!response?.__irodoriResult) throw new Error(`Chrome API bridge did not respond: ${method}`);
    if (response.error) {
      const error = new Error(response.error.message);
      error.name = response.error.name || "Error";
      throw error;
    }
    return response.value;
  };
  globalThis.browser = {
    storage: {
      local: {
        get: (...args) => rpc("storage.local.get", ...args),
        set: (...args) => rpc("storage.local.set", ...args),
        remove: (...args) => rpc("storage.local.remove", ...args)
      },
      onChanged: event("storage.onChanged")
    },
    runtime: {
      onMessage: event("runtime.onMessage"),
      sendMessage: (message) => rpc("runtime.broadcast", message),
      sendNativeMessage: (host, message) => rpc("runtime.sendNativeMessage", host, message),
      getURL: (path) => chrome.runtime.getURL(path),
      getManifest: () => chrome.runtime.getManifest()
    },
    tabs: {
      query: (...args) => rpc("tabs.query", ...args),
      sendMessage: (...args) => rpc("tabs.sendMessage", ...args)
    },
    browserAction: {
      setBadgeText: (...args) => rpc("action.setBadgeText", ...args),
      setBadgeBackgroundColor: (...args) => rpc("action.setBadgeBackgroundColor", ...args),
      onClicked: event("action.onClicked")
    },
    sidebarAction: { toggle: () => rpc("sidePanel.open") },
    notifications: { create: (...args) => rpc("notifications.create", ...args) },
    menus: { create: (info) => info.id, onClicked: event("contextMenus.onClicked") },
    commands: { onCommand: event("commands.onCommand") }
  };
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || message?.__irodoriBridge !== "background") return false;
    const listeners = events.get(message.event || "runtime.onMessage");
    const args = message.event ? message.args : [message.message, message.sender || {}];
    (async () => {
      for (const listener of listeners || []) {
        const result = listener(...args);
        if (result !== undefined) return await result;
      }
      return null;
    })().then(
      (value) => sendResponse({ __irodoriResult: true, value: value ?? null }),
      (error) => sendResponse({ __irodoriResult: true, error: { name: error.name, message: error.message } })
    );
    return true;
  });
})();
