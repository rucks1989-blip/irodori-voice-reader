/* Chrome-only adapter for the unchanged Firefox extension pages/content script. */
"use strict";
(() => {
  const unpack = (response) => {
    if (!response?.__irodoriResult) return response;
    if (response.error) {
      const error = new Error(response.error.message);
      error.name = response.error.name || "Error";
      throw error;
    }
    return response.value;
  };
  const listeners = new Map();
  const onMessage = {
    addListener(listener) {
      if (listeners.has(listener)) return;
      const wrapped = (message, sender, sendResponse) => {
        if (message?.__irodoriBridge) return false;
        try {
          const result = listener(message, sender);
          if (result === undefined) return false;
          Promise.resolve(result).then(sendResponse, (error) => sendResponse({
            __irodoriResult: true, error: { name: error.name, message: error.message }
          }));
          return true;
        } catch (error) {
          sendResponse({ __irodoriResult: true, error: { name: error.name, message: error.message } });
          return false;
        }
      };
      listeners.set(listener, wrapped);
      chrome.runtime.onMessage.addListener(wrapped);
    },
    removeListener(listener) {
      const wrapped = listeners.get(listener);
      if (wrapped) chrome.runtime.onMessage.removeListener(wrapped);
      listeners.delete(listener);
    },
    hasListener(listener) { return listeners.has(listener); }
  };
  globalThis.browser = {
    ...chrome,
    runtime: {
      ...chrome.runtime,
      onMessage,
      sendMessage: (...args) => chrome.runtime.sendMessage(...args).then(unpack)
    },
    menus: chrome.contextMenus,
    browserAction: chrome.action
  };
})();
