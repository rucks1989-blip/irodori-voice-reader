"use strict";
globalThis.IvrAdapters = (() => {
  const CHAT_BODY = '[data-assistant-markdown-root], [data-chatgpt-selection-message-id], [data-markdown-text-style="assistant-message"], .markdown, [class*="prose"]';
  const MODERN_UNIT = '[data-chatgpt-search-unit-key], [data-content-search-unit-key], [data-talvt-turn-id], [data-talvt-turn-state], article[data-testid^="conversation-turn-"]';
  const GEMINI_USER = 'user-query, [data-test-id="user-query"], [data-test-id="user-query-content"], .user-query-container, .user-query-text, .query-text';
  function cleanBody(node, readCodeBlocks = false, extraExcluded = "") {
    if (!node) return "";
    const clone = node.cloneNode(true);
    clone.querySelectorAll('button, nav, script, style, [hidden], [aria-hidden="true"], .sr-only, .visually-hidden, .cdk-visually-hidden, [data-testid*="thought"], [data-testid*="reasoning"], [data-message-attribution], [data-assistant-placeholder-slot], [data-streaming-placeholder-dot], [role="status"], model-thoughts, .thoughts-container').forEach((element) => element.remove());
    if (extraExcluded) clone.querySelectorAll(extraExcluded).forEach((element) => element.remove());
    // Preserve block boundaries without attaching clones to (and mutating) the site.
    clone.querySelectorAll("br").forEach((element) => element.replaceWith("\n"));
    clone.querySelectorAll("pre").forEach((element) => {
      element.replaceWith(readCodeBlocks ? `\n${element.textContent}\n` : "\n");
    });
    clone.querySelectorAll("p, div, li, h1, h2, h3, h4, blockquote, tr").forEach((element) => element.append("\n"));
    return (clone.textContent || "").replace(/[ \t]+/g, " ").replace(/\n[ \t]+/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  }
  function visible(node) {
    if (!node || !node.isConnected) return false;
    if (node.closest('[hidden], [aria-hidden="true"]')) return false;
    const rects = typeof node.getClientRects === "function" ? node.getClientRects() : null;
    return !rects || rects.length > 0;
  }
  function documentOrder(nodes) {
    return [...nodes].filter(Boolean)
      .filter((node) => ![...nodes].some((other) => other !== node && other && other.contains(node)))
      .sort((a, b) => a.compareDocumentPosition(b) & 4 ? -1 : 1);
  }
  function chatRoot(node, role) {
    if (node.getAttribute("data-message-role") === role) return node;
    return node.closest(MODERN_UNIT) || node.closest("article") || node.parentElement || node;
  }
  function chatCandidates(doc) {
    const roots = new Set();
    // The current transcript UI marks both roles directly on the message item.
    for (const node of doc.querySelectorAll('[data-message-role="assistant"]')) roots.add(node);
    for (const role of doc.querySelectorAll('[data-message-author-role="assistant"], [data-conversation-role="assistant"]')) {
      const root = chatRoot(role, "assistant");
      if (root && !root.matches('[data-message-role="user"], [data-message-author-role="user"], [data-conversation-role="user"]') &&
          !root.querySelector('[data-message-role="user"], [data-message-author-role="user"], [data-conversation-role="user"]')) roots.add(root);
    }
    // New renderers can omit the role attribute on the content itself. Only an
    // explicit assistant-body attribute is safe; never guess from .markdown alone.
    for (const body of doc.querySelectorAll('[data-markdown-text-style="assistant-message"], [data-chatgpt-selection-message-id]')) {
      const root = body.closest(MODERN_UNIT) || body.closest('[data-message-author-role="assistant"]') || body;
      if (root.matches('[data-message-role="user"], [data-message-author-role="user"], [data-conversation-role="user"]') ||
          root.querySelector('[data-message-role="user"], [data-message-author-role="user"], [data-conversation-role="user"]')) continue;
      roots.add(root);
    }
    return documentOrder([...roots].filter(visible));
  }
  function chatUsers(doc) {
    const roots = new Set();
    for (const node of doc.querySelectorAll('[data-message-role="user"]')) roots.add(node);
    for (const role of doc.querySelectorAll('[data-message-author-role="user"], [data-conversation-role="user"]')) {
      const root = chatRoot(role, "user");
      if (root && !root.matches('[data-message-role="assistant"], [data-message-author-role="assistant"], [data-conversation-role="assistant"]')) roots.add(root);
    }
    for (const body of doc.querySelectorAll('[data-user-message-copy], [data-markdown-text-style="user-message"]')) {
      const root = body.closest('[data-message-role="user"]') || body.closest(MODERN_UNIT) || body.closest("li") || body;
      if (!root.matches("[data-message-role='assistant']")) roots.add(root);
    }
    return documentOrder([...roots].filter(visible));
  }
  function geminiUserMessages(doc) {
    const roots = new Set();
    for (const node of doc.querySelectorAll(GEMINI_USER)) {
      if (node.closest("model-response")) continue;
      const root = node.matches("user-query, [data-test-id='user-query'], .user-query-container")
        ? node
        : (node.closest("user-query, [data-test-id='user-query'], .user-query-container") || node);
      roots.add(root);
    }
    return documentOrder([...roots].filter(visible));
  }
  function geminiCandidates(doc) {
    const roots = [...doc.querySelectorAll('model-response, [data-test-id="model-response"]')];
    const list = roots.length ? roots : [...doc.querySelectorAll(".model-response-text, .response-container .markdown-main-panel")];
    return documentOrder(list.filter(visible));
  }
  function conversationId(loc, doc) {
    // The route changes as soon as a new chat is submitted. Prefer it over an
    // older transcript marker that can remain in the DOM during the transition.
    const routeId = loc.pathname.match(/\/(?:c|uc|app)\/([^/?#]+)/)?.[1] || "";
    if (routeId) return routeId;
    return [...doc.querySelectorAll("[data-conversation-id]")]
      .map((node) => node.getAttribute("data-conversation-id") || "")
      .filter(Boolean).at(-1) || "";
  }
  function forPage(loc = location, doc = document, options = {}) {
    if (["muse.ai", "www.muse.ai"].includes(loc.hostname)) {
      const route = () => {
        const routes = [...doc.querySelectorAll('[data-hatch-chat-content] [data-hatch-route-key]')]
          .filter((node) => !node.closest('[hidden], [aria-hidden="true"], [data-hatch-hidden="true"]'));
        return routes.findLast((node) => node.getClientRects().length) || routes.at(-1) || doc.querySelector('[data-hatch-chat-content]');
      };
      return {
        name: "muse", enabledKey: "museEnabled",
        candidates() { return [...(route()?.querySelectorAll('[data-message-item][data-message-role="assistant"]') || [])]; },
        userMessages() { return [...(route()?.querySelectorAll('[data-message-item][data-message-role="user"]') || [])]; },
        messageId(node) { return node.getAttribute("data-message-id") || ""; },
        conversationId() { return route()?.getAttribute("data-hatch-route-key") || `${loc.pathname}${loc.search || ""}`; },
        userText(node) { return cleanBody(node.querySelector('.hatch-chat-groupable-bubble') || node, true, '[data-copy-exclude], .hidden'); },
        text(node) {
          const body = node.querySelector('[data-hatch-assistant-message-body]');
          if (!body) return "";
          const roots = [...body.querySelectorAll('[data-hatch-plain-text], [data-hatch-markdown-streaming]')];
          return roots.filter((item) => !roots.some((other) => other !== item && other.contains(item)))
            .map((item) => cleanBody(item, options.readCodeBlocks, '[data-copy-exclude], .hidden')).filter(Boolean).join("\n");
        },
        streaming(node) {
          return node.matches('[aria-busy="true"]') || Boolean(node.querySelector('[aria-busy="true"], [data-hatch-markdown-streaming="true"]'));
        }
      };
    }
    if (["chatgpt.com", "chat.openai.com"].includes(loc.hostname)) {
      let candidates = [];
      return {
        name: "chatgpt", enabledKey: "chatgptEnabled",
        candidates() { candidates = chatCandidates(doc); return candidates; },
        userMessages() { return chatUsers(doc); },
        userText(node) {
          const role = node.matches('[data-message-author-role="user"]') ? node : node.querySelector('[data-message-author-role="user"]');
          const bubble = '[data-user-message-copy], [data-markdown-text-style="user-message"], .whitespace-pre-wrap';
          const owner = role || node;
          const body = (owner.matches(bubble) ? owner : owner.querySelector(bubble)) ||
            owner.querySelector('[data-chatgpt-selection-message-id]') || owner;
          return cleanBody(body, true);
        },
        text(node) {
          const role = node.matches('[data-message-author-role="assistant"]') ? node : node.querySelector('[data-message-author-role="assistant"]');
          const owner = role || node;
          const explicit = '[data-assistant-markdown-root], [data-markdown-text-style="assistant-message"], [data-chatgpt-selection-message-id]';
          const fallback = [...owner.querySelectorAll('.markdown, [class*="prose"]')].find((element) => !element.closest('[data-testid*="thought"], [data-testid*="reasoning"]'));
          const body = node.matches(CHAT_BODY) ? node : (owner.querySelector(explicit) || fallback || owner);
          return cleanBody(body, options.readCodeBlocks);
        },
        messageId(node) {
          const inner = node.querySelector('[data-message-id], [data-chatgpt-selection-message-id]');
          return node.getAttribute("data-message-id") || node.getAttribute("data-chatgpt-selection-message-id") ||
            inner?.getAttribute("data-message-id") || inner?.getAttribute("data-chatgpt-selection-message-id") ||
            node.getAttribute("data-turn-id") || node.getAttribute("data-talvt-turn-id") ||
            node.getAttribute("data-chatgpt-search-unit-key") || node.getAttribute("data-content-search-unit-key") ||
            node.id || node.getAttribute("data-testid") || "";
        },
        conversationId() { return conversationId(loc, doc); },
        streaming(node) {
          const role = node.matches('[data-message-role="assistant"]') ? node : node.querySelector('[data-message-role="assistant"]');
          if (role) {
            if (role.hasAttribute("data-message-streaming")) return true;
            if (role.id.startsWith("pending-")) return true;
            if (role.hasAttribute("data-message-complete")) return false;
          }
          if (node.matches('[aria-busy="true"], [data-is-streaming="true"]') ||
              node.querySelector('[aria-busy="true"], [data-is-streaming="true"], .result-streaming')) return true;
          const state = node.closest('[data-talvt-turn-state]')?.getAttribute("data-talvt-turn-state");
          if (state && !["complete", "completed", "done", "idle"].includes(state)) return true;
          const stop = doc.querySelector('[data-testid="stop-button"], [data-testid="stop-generating-button"], button[aria-label*="Stop generating" i], button[aria-label*="Stop streaming" i], button[aria-label*="生成を停止"], button[aria-label*="応答を停止"]');
          return Boolean(stop && candidates.at(-1) === node);
        }
      };
    }
    if (loc.hostname === "gemini.google.com") {
      let candidates = [];
      return {
        name: "gemini", enabledKey: "geminiEnabled",
        candidates() { candidates = geminiCandidates(doc); return candidates; },
        userMessages() { return geminiUserMessages(doc); },
        userText(node) {
          const body = node.matches(GEMINI_USER) ? node : node.querySelector(GEMINI_USER);
          const content = body?.querySelector?.('.query-text, .query-content, message-content, .user-query-text, [data-test-id="user-query-content"]') || body || node;
          return cleanBody(content, true);
        },
        text(node) { return cleanBody(node.querySelector("message-content, .markdown-main-panel, .response-content") || node, options.readCodeBlocks); },
        messageId(node) {
          const inner = node.querySelector('[data-message-id], [data-turn-id], [data-response-id]');
          return node.getAttribute("data-message-id") || node.getAttribute("data-turn-id") ||
            node.getAttribute("data-response-id") || node.id || inner?.getAttribute("data-message-id") ||
            inner?.getAttribute("data-turn-id") || inner?.getAttribute("data-response-id") || "";
        },
        conversationId() { return conversationId(loc, doc); },
        streaming(node) {
          if (node.matches('[aria-busy="true"], [data-is-streaming="true"]') ||
              node.querySelector('mat-progress-spinner:not([hidden]), .loading:not([hidden]), .response-loading, [aria-busy="true"], [data-is-streaming="true"]')) return true;
          const stop = doc.querySelector('button[aria-label*="Stop response" i], button[aria-label*="Stop generating" i], button[aria-label*="Stop streaming" i], button[aria-label*="回答を停止"], button[aria-label*="応答を停止"], button[aria-label*="生成を停止"]');
          return Boolean(stop && candidates.at(-1) === node);
        }
      };
    }
    return null;
  }
  return { forPage, cleanBody, chatCandidates, conversationId };
})();
