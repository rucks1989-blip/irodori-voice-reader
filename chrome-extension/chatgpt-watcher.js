"use strict";
// Only speak an answer associated with an input actually sent in this tab.
globalThis.IvrChatgptWatcher = (() => {
  const normalize = (value) => String(value || "").normalize("NFC").replace(/\s+/gu, " ").trim();
  const editorSelector = '#prompt-textarea, textarea[data-mobile-composer-prompt], textarea[name="prompt-textarea"], [contenteditable="true"][data-lexical-editor]';
  function start({ api, doc, win, getAdapter, request, hash, toast, settleMs = 1200, scanMs = 100 }) {
    let settings = { chatgptEnabled: false, readCodeBlocks: false, timingDebug: false };
    let route = "", chatPath = "", pending = null, serial = 0, timer = 0, scanTimer = 0, observer = null, interval = 0, stopped = false;
    const fallbackIds = new WeakMap();
    let fallbackSequence = 0;
    const adapter = () => getAdapter(settings);
    function trace(event, fields = {}) {
      if (!settings.timingDebug) return;
      request("timing-log-event", {
        event: { event, timestamp: new Date().toISOString(), site: "chatgpt", ...fields }
      }).catch(() => {});
    }
    const nodeId = (a, node) => {
      const id = a.messageId(node);
      if (id) return id;
      if (!fallbackIds.has(node)) fallbackIds.set(node, `node-${++fallbackSequence}`);
      return fallbackIds.get(node);
    };
    const reset = () => { ++serial; pending = null; win.clearTimeout(timer); timer = 0; };
    function context() {
      const current = adapter()?.conversationId() || "";
      const path = win.location.pathname;
      if (current === route && path === chatPath) return;
      // A brand-new conversation normally gets its ID after submission.
      // Preserve that one transition; switching between existing chats cancels.
      const newConversation = pending && !/\/c\/[^/]+/.test(chatPath) && /\/c\/[^/]+/.test(path);
      route = current;
      chatPath = path;
      if (newConversation) pending.conversation = current;
      else reset();
    }
    const editorFor = (target, explicitSend = false) => {
      if (target?.matches?.(editorSelector)) return target;
      const root = target?.closest?.('form, [data-composer-root], [data-testid="composer"]');
      return root?.querySelector(editorSelector) || (explicitSend ? doc.querySelector("#prompt-textarea") : null);
    };
    function begin(editor) {
      context();
      if (!settings.chatgptEnabled || !editor) return;
      const text = normalize(editor.value ?? editor.innerText ?? editor.textContent);
      if (!text) return;
      // The same send can emit keydown, click and submit in quick succession.
      if (pending && !pending.userId && pending.text === text) return;
      const a = adapter();
      if (!a) return;
      win.clearTimeout(timer); timer = 0;
      pending = { text, before: new Set(a.userMessages().map((node) => nodeId(a, node))),
        conversation: a.conversationId(), serial: ++serial, signature: "", userId: "", started: Date.now() };
      trace("chatgpt_prompt_sent", { attempt: serial });
      queueScan();
    }
    function clicked(event) {
      if (!event.isTrusted) return;
      const button = event.target?.closest?.("button");
      if (!button || button.disabled) return;
      const explicitSend = button.matches('[data-testid="send-button"], [data-composer-submit], #composer-submit-button') ||
        ["aria-label", "title"].some((attribute) => /^(?:send|send message|submit|送信|メッセージを送信(?:します)?)$/iu.test(button.getAttribute(attribute)?.trim() || ""));
      if (!explicitSend && !button.matches('button[type="submit"]')) return;
      begin(editorFor(button, explicitSend));
    }
    function keydown(event) {
      if (!event.isTrusted || event.key !== "Enter" || event.isComposing || event.shiftKey || !event.target?.matches?.(editorSelector)) return;
      begin(event.target);
    }
    function submit(event) { if (event.isTrusted) begin(editorFor(event.target)); }
    function scan() {
      context();
      if (stopped || !settings.chatgptEnabled || !pending) return;
      if (Date.now() - pending.started > 5 * 60 * 1000) {
        trace("chatgpt_detection_timeout", { attempt: pending.serial, matchedUser: Boolean(pending.userId) });
        reset();
        return;
      }
      const a = adapter(), latestUser = a.userMessages().at(-1);
      if (!latestUser) return;
      const userId = nodeId(a, latestUser);
      if (pending.before.has(userId) || normalize(a.userText(latestUser)) !== pending.text) return;
      if (!pending.userId) trace("chatgpt_user_matched", { attempt: pending.serial });
      pending.userId = userId;
      const newest = a.candidates().filter((node) => latestUser.compareDocumentPosition(node) & 4).at(-1);
      if (!newest) return;
      const text = a.text(newest);
      if (!text || a.streaming(newest)) { win.clearTimeout(timer); timer = 0; pending.signature = ""; return; }
      const signature = `${nodeId(a, newest)}|${text}`;
      if (timer && pending.signature === signature) return;
      win.clearTimeout(timer);
      pending.signature = signature;
      const token = pending.serial;
      timer = win.setTimeout(async () => {
        timer = 0; context();
        if (stopped || !settings.chatgptEnabled || !pending || pending.serial !== token) return;
        const current = adapter(), user = current.userMessages().at(-1);
        if (!user || nodeId(current, user) !== pending.userId) { pending.signature = ""; queueScan(); return; }
        const last = current.candidates().filter((node) => user.compareDocumentPosition(node) & 4).at(-1);
        if (!last || !last.isConnected || nodeId(current, last) !== nodeId(a, newest) || current.text(last) !== text || current.streaming(last)) {
          pending.signature = ""; queueScan(); return;
        }
        try {
          const textHash = await hash(text);
          context();
          if (stopped || !settings.chatgptEnabled || !pending || pending.serial !== token || !last.isConnected) return;
          const refreshed = adapter(), refreshedUser = refreshed.userMessages().at(-1);
          const refreshedLast = refreshedUser && refreshed.candidates().filter((node) => refreshedUser.compareDocumentPosition(node) & 4).at(-1);
          if (!refreshedUser || nodeId(refreshed, refreshedUser) !== pending.userId || !refreshedLast ||
              nodeId(refreshed, refreshedLast) !== nodeId(current, last) || refreshed.text(refreshedLast) !== text || refreshed.streaming(refreshedLast)) {
            pending.signature = ""; queueScan(); return;
          }
          const answer = { conversationId: pending.conversation, messageId: nodeId(refreshed, refreshedLast), textHash };
          pending = null;
          trace("chatgpt_answer_detected", { attempt: token });
          const result = await request("enqueue", { text, source: "chatgpt", answer });
          trace("chatgpt_enqueue_result", { attempt: token, queued: Boolean(result?.queued), ignored: Boolean(result?.ignored) });
          if (result?.queued) toast("ChatGPTの最新回答を読み上げます。");
        } catch (error) {
          trace("chatgpt_enqueue_error", { attempt: token });
          toast(error.message, true);
        }
      }, settleMs);
    }
    function queueScan() { if (!stopped && !scanTimer) scanTimer = win.setTimeout(() => { scanTimer = 0; scan(); }, scanMs); }
    function changed(changes, area) {
      if (area !== "local") return;
      if (changes.timingDebug) settings.timingDebug = Boolean(changes.timingDebug.newValue);
      for (const key of ["chatgptEnabled", "readCodeBlocks"]) if (changes[key]) { settings[key] = Boolean(changes[key].newValue); reset(); }
      queueScan();
    }
    doc.addEventListener("click", clicked, true);
    doc.addEventListener("keydown", keydown, true);
    doc.addEventListener("submit", submit, true);
    win.addEventListener("popstate", queueScan);
    api.storage.onChanged.addListener(changed);
    api.storage.local.get(settings).then((value) => {
      if (stopped) return;
      settings = value; context();
      observer = new win.MutationObserver(queueScan);
      observer.observe(doc.documentElement, { childList: true, subtree: true, characterData: true, attributes: true,
        attributeFilter: ["id", "data-message-id", "data-message-role", "data-message-streaming", "data-message-complete", "data-talvt-turn-state", "data-is-streaming", "data-conversation-role", "data-message-author-role", "aria-busy", "hidden"] });
      interval = win.setInterval(queueScan, 1500);
    }).catch((error) => toast(error.message, true));
    return { stop() {
      stopped = true; reset(); win.clearTimeout(scanTimer); win.clearInterval(interval); observer?.disconnect();
      doc.removeEventListener("click", clicked, true);
      doc.removeEventListener("keydown", keydown, true); doc.removeEventListener("submit", submit, true);
      win.removeEventListener("popstate", queueScan); api.storage.onChanged.removeListener(changed);
    } };
  }
  return { start };
})();
