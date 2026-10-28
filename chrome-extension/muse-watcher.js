"use strict";
globalThis.IvrMuseWatcher = (() => {
  const normalize = (text) => String(text || "").replace(/\s+/gu, " ").trim();
  function start({ api, doc, win, getAdapter, request, hash, toast, settleMs = 1200, scanMs = 100, allowSyntheticEvents = false }) {
    let settings = { museEnabled: false, readCodeBlocks: false }, route = "", pending = null, draft = "", serial = 0;
    let timer = 0, scanTimer = 0, observer = null, interval = 0, stopped = false;
    const adapter = () => getAdapter(settings);
    const reset = () => { serial++; pending = null; draft = ""; clearTimeout(timer); timer = 0; };
    function context() {
      const current = adapter()?.conversationId() || "";
      if (route !== current) { route = current; reset(); }
    }
    const trusted = (event) => event.isTrusted || allowSyntheticEvents;
    const composer = (node) => node?.closest?.('[data-hatch-composer-root]');
    function textInput(event) {
      if (!settings.museEnabled || !trusted(event) || !event.target?.matches?.('textarea, [contenteditable="true"]') || !composer(event.target)) return;
      draft = normalize(event.target.value ?? event.target.textContent);
    }
    function begin(root) {
      context();
      if (!settings.museEnabled || !root) return;
      const input = root.querySelector('textarea, [contenteditable="true"]');
      const text = normalize(input?.value ?? input?.textContent);
      // Never arm on page load, pre-existing history, a dictation button or a
      // proactive Muse message: a user must actually type and submit text here.
      if (!text || text !== draft) return;
      const a = adapter();
      if (!a) return;
      clearTimeout(timer); timer = 0;
      pending = { text, before: new Set(a.userMessages().map((node) => a.messageId(node)).filter(Boolean)),
        conversation: a.conversationId(), serial: ++serial, userId: "", signature: "" };
      queueScan();
    }
    function clicked(event) {
      if (!trusted(event)) return;
      const button = event.target?.closest?.("button"), root = composer(button);
      if (!button || !root || button.disabled) return;
      const label = (button.getAttribute("aria-label") || button.getAttribute("title") || "").trim();
      if (button.getAttribute("data-pel-click") === "chat_send_message" || /^(?:send|send message|submit|送信|メッセージを送信)$/iu.test(label)) begin(root);
    }
    function keydown(event) {
      // Muse may use Enter or a modifier+Enter shortcut. Merely inserting a
      // newline cannot trigger speech: an actual matching user message must
      // still appear in the conversation before any answer is considered.
      if (!trusted(event) || event.key !== "Enter" || event.isComposing) return;
      if (!event.target?.matches?.('textarea, [contenteditable="true"]')) return;
      begin(composer(event.target));
    }
    function submit(event) { if (trusted(event)) begin(composer(event.target)); }
    function scan() {
      context();
      if (stopped || !settings.museEnabled || !pending) return;
      const a = adapter(), users = a.userMessages(), latestUser = users.at(-1);
      if (!latestUser) return;
      const id = a.messageId(latestUser);
      if (!id || pending.before.has(id) || normalize(a.userText(latestUser)) !== pending.text) return;
      // Muse may replace an optimistic user-message ID when its server ACKs.
      // It still has to be the newest, matching, previously unseen user input.
      pending.userId = id;
      const newest = a.candidates().filter((node) => latestUser.compareDocumentPosition(node) & 4).at(-1);
      if (!newest || !a.messageId(newest)) return;
      const text = a.text(newest);
      if (!text || a.streaming(newest)) { clearTimeout(timer); timer = 0; pending.signature = ""; return; }
      const signature = `${a.messageId(newest)}|${text}`;
      if (timer && pending.signature === signature) return;
      clearTimeout(timer);
      pending.signature = signature;
      const token = pending.serial;
      timer = win.setTimeout(async () => {
        timer = 0; context();
        if (stopped || !settings.museEnabled || !pending || pending.serial !== token) return;
        const current = adapter(), user = current.userMessages().at(-1);
        if (!user || current.messageId(user) !== pending.userId) { reset(); return; }
        const last = current.candidates().filter((node) => user.compareDocumentPosition(node) & 4).at(-1);
        if (!last || !last.isConnected || current.messageId(last) !== a.messageId(newest) || current.text(last) !== text || current.streaming(last)) {
          pending.signature = ""; queueScan(); return;
        }
        try {
          const textHash = await hash(text);
          context();
          if (stopped || !settings.museEnabled || !pending || pending.serial !== token || !last.isConnected) return;
          const refreshed = adapter(), refreshedUser = refreshed.userMessages().at(-1);
          const refreshedLast = refreshedUser && refreshed.candidates().filter((node) => refreshedUser.compareDocumentPosition(node) & 4).at(-1);
          if (!refreshedUser || refreshed.messageId(refreshedUser) !== pending.userId || !refreshedLast ||
              refreshed.messageId(refreshedLast) !== current.messageId(last) || refreshed.text(refreshedLast) !== text || refreshed.streaming(refreshedLast)) {
            pending.signature = ""; queueScan(); return;
          }
          const answer = { conversationId: pending.conversation, messageId: refreshed.messageId(refreshedLast), textHash };
          // Consume once even if Muse subsequently emits a proactive notification.
          pending = null; draft = "";
          const result = await request("enqueue", { text, source: "muse", answer });
          if (result.queued) toast("Museの最新回答を読み上げます。");
        } catch (error) { toast(error.message, true); }
      }, settleMs);
    }
    function queueScan() {
      if (!stopped && !scanTimer) scanTimer = win.setTimeout(() => { scanTimer = 0; scan(); }, scanMs);
    }
    function changed(changes, area) {
      if (area !== "local") return;
      if (changes.museEnabled) { settings.museEnabled = Boolean(changes.museEnabled.newValue); reset(); }
      if (changes.readCodeBlocks) { settings.readCodeBlocks = Boolean(changes.readCodeBlocks.newValue); reset(); }
      queueScan();
    }
    doc.addEventListener("input", textInput, true);
    doc.addEventListener("click", clicked, true);
    doc.addEventListener("keydown", keydown, true);
    doc.addEventListener("submit", submit, true);
    win.addEventListener("popstate", queueScan);
    api.storage.onChanged.addListener(changed);
    api.storage.local.get({ museEnabled: false, readCodeBlocks: false }).then((value) => {
      if (stopped) return;
      settings = value; route = adapter()?.conversationId() || "";
      observer = new win.MutationObserver(queueScan);
      observer.observe(doc.documentElement, { childList: true, subtree: true, characterData: true, attributes: true,
        attributeFilter: ["data-message-id", "data-message-role", "data-hatch-route-key", "data-hatch-markdown-streaming", "aria-busy", "hidden"] });
      interval = win.setInterval(queueScan, 1500);
    }).catch((error) => toast(error.message, true));
    return { stop() {
      stopped = true; reset(); clearTimeout(scanTimer); win.clearInterval(interval); observer?.disconnect();
      doc.removeEventListener("input", textInput, true); doc.removeEventListener("click", clicked, true);
      doc.removeEventListener("keydown", keydown, true); doc.removeEventListener("submit", submit, true);
      win.removeEventListener("popstate", queueScan); api.storage.onChanged.removeListener(changed);
    } };
  }
  return { start };
})();
