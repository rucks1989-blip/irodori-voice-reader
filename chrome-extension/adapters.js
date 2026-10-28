"use strict";
globalThis.IvrAdapters = (() => {
  const CHAT_BODY = '[data-chatgpt-selection-message-id], [data-markdown-text-style="assistant-message"], .markdown, [class*="prose"]';
  const MODERN_UNIT = '[data-chatgpt-search-unit-key], [data-content-search-unit-key], [data-talvt-turn-id], [data-talvt-turn-state], article[data-testid^="conversation-turn-"]';
  function cleanBody(node, readCodeBlocks = false, extraExcluded = "") {
    if (!node) return "";
    const clone = node.cloneNode(true);
    clone.querySelectorAll('button, nav, script, style, [hidden], [aria-hidden="true"], .sr-only, .visually-hidden, .cdk-visually-hidden, [data-testid*="thought"], [data-testid*="reasoning"], model-thoughts, .thoughts-container').forEach((element) => element.remove());
    if (extraExcluded) clone.querySelectorAll(extraExcluded).forEach((element) => element.remove());
    // Preserve block boundaries without attaching clones to (and mutating) the site.
    clone.querySelectorAll("br").forEach((element) => element.replaceWith("\n"));
    clone.querySelectorAll("pre").forEach((element) => {
      element.replaceWith(readCodeBlocks ? `\n${element.textContent}\n` : "\n");
    });
    clone.querySelectorAll("p, div, li, h1, h2, h3, h4, blockquote, tr").forEach((element) => element.append("\n"));
    return (clone.textContent || "").replace(/[ \t]+/g, " ").replace(/\n[ \t]+/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  }
  function chatCandidates(doc) {
    const roots = new Set();
    // The current logged-out web UI exposes a separate, explicit transcript.
    for (const node of doc.querySelectorAll('[data-conversation-transcript] [data-message-role="assistant"]')) {
      if (node.querySelector('[data-assistant-markdown-root]')) roots.add(node);
    }
    for (const role of doc.querySelectorAll('[data-message-author-role="assistant"], [data-conversation-role="assistant"]')) {
      const root = role.closest(MODERN_UNIT) || (role.matches('[data-message-author-role="assistant"]') ? role : role.parentElement);
      if (root && (role.matches('[data-message-author-role="assistant"]') || root.matches(CHAT_BODY) || root.querySelector(CHAT_BODY))) roots.add(root);
    }
    // New renderers can omit the role attribute on the content itself. Only an
    // explicit assistant-body attribute is safe; never guess from .markdown alone.
    for (const body of doc.querySelectorAll('[data-markdown-text-style="assistant-message"], [data-chatgpt-selection-message-id]')) {
      const root = body.closest(MODERN_UNIT) || body.closest('[data-message-author-role="assistant"]') || (body.matches('[data-markdown-text-style="assistant-message"]') ? body : null);
      if (!root || root.matches('[data-message-author-role="user"], [data-conversation-role="user"]') || root.querySelector('[data-message-author-role="user"], [data-conversation-role="user"]')) continue;
      roots.add(root);
    }
    const list = [...roots].filter((root) => ![...roots].some((other) => other !== root && other.contains(root)));
    return list.sort((a, b) => a.compareDocumentPosition(b) & 2 ? 1 : -1);
  }
  function chatUsers(doc) {
    const roots = new Set();
    for (const node of doc.querySelectorAll('[data-conversation-transcript] [data-message-role="user"]')) roots.add(node);
    for (const role of doc.querySelectorAll('[data-message-author-role="user"], [data-conversation-role="user"]')) {
      const root = role.closest(MODERN_UNIT) || (role.matches('[data-message-author-role="user"]') ? role : role.parentElement);
      if (root) roots.add(root);
    }
    return [...roots].filter((root) => ![...roots].some((other) => other !== root && other.contains(root)))
      .sort((a, b) => a.compareDocumentPosition(b) & 2 ? 1 : -1);
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
          const body = node.querySelector('[data-user-message-copy]') || role || node.querySelector('[data-chatgpt-selection-message-id], [data-markdown-text-style="user-message"]') || node;
          return cleanBody(body, true);
        },
        text(node) {
          const role = node.matches('[data-message-author-role="assistant"]') ? node : node.querySelector('[data-message-author-role="assistant"]');
          const owner = role || node;
          const explicit = '[data-markdown-text-style="assistant-message"], [data-chatgpt-selection-message-id]';
          const fallback = [...owner.querySelectorAll('.markdown, [class*="prose"]')].find((element) => !element.closest('[data-testid*="thought"], [data-testid*="reasoning"]'));
          const body = node.matches(CHAT_BODY) ? node : (owner.querySelector('[data-assistant-markdown-root]') || owner.querySelector(explicit) || fallback || role);
          return cleanBody(body, options.readCodeBlocks);
        },
        messageId(node) {
          const inner = node.querySelector('[data-message-id], [data-chatgpt-selection-message-id]');
          return node.getAttribute("data-message-id") || node.getAttribute("data-chatgpt-selection-message-id") || inner?.getAttribute("data-message-id") || inner?.getAttribute("data-chatgpt-selection-message-id") || node.getAttribute("data-turn-id") || node.getAttribute("data-talvt-turn-id") || node.getAttribute("data-chatgpt-search-unit-key") || node.getAttribute("data-content-search-unit-key") || node.getAttribute("data-testid") || node.id || "";
        },
        conversationId() { return loc.pathname.match(/\/c\/([^/]+)/)?.[1] || loc.pathname; },
        streaming(node) {
          if (node.matches('[data-message-role="assistant"]') && (!node.hasAttribute("data-message-complete") || node.hasAttribute("data-message-streaming"))) return true;
          if (node.matches('[aria-busy="true"], [data-is-streaming="true"]') || node.querySelector('[data-is-streaming="true"], .result-streaming')) return true;
          const state = node.closest('[data-talvt-turn-state]')?.getAttribute("data-talvt-turn-state");
          if (state && !["complete", "completed", "done", "idle"].includes(state)) return true;
          const stop = doc.querySelector('[data-testid="stop-button"], [data-testid="stop-generating-button"], button[aria-label*="Stop generating" i], button[aria-label*="生成を停止"], button[aria-label*="応答を停止"]');
          return Boolean(stop && candidates.at(-1) === node);
        }
      };
    }
    if (loc.hostname === "gemini.google.com") {
      let candidates = [];
      return {
        name: "gemini", enabledKey: "geminiEnabled",
        candidates() {
          const roots = [...doc.querySelectorAll('model-response, [data-test-id="model-response"]')];
          candidates = roots.length ? roots.filter((node) => !roots.some((other) => other !== node && other.contains(node))) : [...doc.querySelectorAll(".model-response-text, .response-container .markdown-main-panel")];
          return candidates;
        },
        text(node) { return cleanBody(node.querySelector("message-content, .markdown-main-panel, .response-content") || node, options.readCodeBlocks); },
        messageId(node) { return node.getAttribute("data-message-id") || node.getAttribute("data-response-id") || node.id || ""; },
        conversationId() { return loc.pathname.match(/\/app\/([^/]+)/)?.[1] || loc.pathname; },
        streaming(node) {
          if (node.matches('[aria-busy="true"]') || node.querySelector('mat-progress-spinner, .loading, [aria-busy="true"]')) return true;
          return Boolean(doc.querySelector('button[aria-label*="Stop response" i], button[aria-label*="Stop generating" i], button[aria-label*="回答を停止"], button[aria-label*="停止"]') && candidates.at(-1) === node);
        }
      };
    }
    return null;
  }
  return { forPage, cleanBody, chatCandidates };
})();
