"use strict";
// Speak only an answer produced by a user turn submitted after this conversation
// was already on screen. ChatGPT and Gemini share this watcher because both
// append optimistic user turns and stream a new response into the transcript.
globalThis.IvrChatgptWatcher = (() => {
  const normalize = (value) => String(value || "").normalize("NFC").replace(/\s+/gu, " ").trim();
  const SITES = {
    chatgpt: {
      editors: '#prompt-textarea, textarea[data-mobile-composer-prompt], textarea[name="prompt-textarea"], [contenteditable="true"][data-lexical-editor], [contenteditable="true"][data-composer-markdown]',
      composer: 'form, [data-composer-root], [data-testid="composer"]',
      sendButtons: '[data-testid="send-button"], [data-composer-submit], #composer-submit-button',
      sendLabels: /^(?:send(?: message| prompt)?|submit|送信(?:する)?|メッセージを送信(?:する)?)$/iu
    },
    gemini: {
      editors: 'rich-textarea [contenteditable="true"], rich-textarea textarea, .ql-editor[contenteditable="true"], textarea[aria-label*="prompt" i], textarea[placeholder*="Gemini" i], [contenteditable="true"][role="textbox"]',
      composer: 'form, rich-textarea, .input-area, [data-test-id="composer"], [data-testid="composer"]',
      sendButtons: '.send-button, [data-test-id="send-button"], [data-testid="send-button"]',
      sendLabels: /^(?:send(?: message| prompt)?|submit|送信(?:する)?|メッセージを送信(?:する)?)$/iu
    }
  };
  function start({
    api, doc, win, getAdapter, request, hash, toast,
    settleMs = 1200, scanMs = 100, baselineQuietMs = 2400, inferredGuardMs = 5000
  }) {
    let settings = { chatgptEnabled: false, geminiEnabled: false, readCodeBlocks: false, timingDebug: false };
    let siteName = "", route = "", chatPath = "", routeChangedAt = 0;
    let pending = null, draft = null, serial = 0, timer = 0, scanTimer = 0, baselineTimer = 0;
    let baselineReady = false, observer = null, interval = 0, stopped = false;
    const fallbackIds = new WeakMap();
    const knownUsers = new Set();
    const knownUserPositions = new Set();
    const knownAnswers = new Set();
    const seenAnswers = new Set();
    let fallbackSequence = 0;
    const adapter = () => getAdapter(settings);
    const siteConfig = () => SITES[adapter()?.name] || SITES.chatgpt;
    const enabled = () => {
      const a = adapter();
      return Boolean(a && settings[a.enabledKey]);
    };
    function trace(event, fields = {}) {
      if (!settings.timingDebug) return;
      request("timing-log-event", {
        event: { event, timestamp: new Date().toISOString(), site: siteName || adapter()?.name || "", ...fields }
      }).catch(() => {});
    }
    function visible(node) {
      if (!node || !node.isConnected) return false;
      if (node.closest?.('[hidden], [aria-hidden="true"]')) return false;
      const rects = typeof node.getClientRects === "function" ? node.getClientRects() : null;
      return !rects || rects.length > 0;
    }
    function nodeId(a, node) {
      const id = a.messageId(node);
      if (id) return id;
      if (!fallbackIds.has(node)) fallbackIds.set(node, `node-${++fallbackSequence}`);
      return fallbackIds.get(node);
    }
    function userSignature(a, node, index) {
      return `${index}|${normalize(a.userText(node))}`;
    }
    function answerIdentity(a, node, userIndex) {
      const id = a.messageId(node);
      if (id) return id;
      // Gemini can re-render a response without a stable message ID. The turn
      // position is stable across re-renders, unlike a per-node fallback ID.
      return `turn-${userIndex}`;
    }
    function resetPending() {
      serial += 1;
      pending = null;
      win.clearTimeout(timer);
      timer = 0;
    }
    function rememberUser(a, node, index) {
      knownUsers.add(nodeId(a, node));
      knownUserPositions.add(userSignature(a, node, index));
    }
    function markCurrentKnown(a) {
      if (!a) return;
      a.userMessages().forEach((node, index) => rememberUser(a, node, index));
      a.candidates().forEach((node) => knownAnswers.add(nodeId(a, node)));
    }
    function snapshotKnown() {
      const a = adapter();
      if (!a) return;
      a.userMessages().forEach((node, index) => rememberUser(a, node, index));
      a.candidates().forEach((node) => knownAnswers.add(nodeId(a, node)));
    }
    function beginBaseline() {
      win.clearTimeout(baselineTimer);
      baselineTimer = 0;
      knownUsers.clear();
      knownUserPositions.clear();
      knownAnswers.clear();
      baselineReady = false;
      routeChangedAt = Date.now();
      snapshotKnown();
      baselineTimer = win.setTimeout(() => {
        baselineTimer = 0;
        if (stopped) return;
        snapshotKnown();
        baselineReady = true;
        trace(`${siteName}_baseline_ready`, { conversation_id: route, path: chatPath });
        queueScan();
      }, baselineQuietMs);
    }
    function routeContinuesNewConversation(previousPath, path) {
      if (!pending || Date.now() - pending.sentAt > 2 * 60 * 1000) return false;
      if (siteName === "chatgpt") {
        // Chrome's current renderer first assigns /c/local-chatgpt%3A<uuid>,
        // then replaces it with the server conversation ID. Both transitions
        // belong to the send we are already tracking, not a history navigation.
        const provisional = /^\/c\/local-chatgpt(?::|%3a)[^/?#]+\/?$/i.test(previousPath);
        return (provisional || !/\/(?:c|uc)\/[^/?#]+/.test(previousPath)) &&
          /\/(?:c|uc)\/[^/?#]+/.test(path);
      }
      if (siteName === "gemini") {
        return !/\/app\/[^/?#]+/.test(previousPath) && /\/app\/[^/?#]+/.test(path);
      }
      return false;
    }
    function context() {
      const a = adapter();
      if (!a) return;
      if (siteName !== a.name) {
        siteName = a.name;
        route = a.conversationId() || "";
        chatPath = win.location.pathname;
        resetPending();
        draft = null;
        beginBaseline();
        return;
      }
      const current = a.conversationId() || "";
      const path = win.location.pathname;
      if (current === route && path === chatPath) return;
      const previousPath = chatPath;
      const preserve = routeContinuesNewConversation(previousPath, path) ||
        Boolean(pending && !route && current && path === previousPath);
      route = current;
      chatPath = path;
      if (preserve) {
        pending.conversation = current;
        pending.routeTransition = true;
        trace(`${siteName}_route_transition_preserved`, {
          attempt: pending.serial, conversation_id: current, path
        });
      } else {
        resetPending();
        draft = null;
        trace(`${siteName}_context_reset`, { conversation_id: current, path });
      }
      beginBaseline();
    }
    function readEditor(editor) {
      return normalize(editor?.value ?? editor?.innerText ?? editor?.textContent);
    }
    function editorFor(target, explicitSend = false) {
      const config = siteConfig();
      if (target?.matches?.(config.editors)) return target;
      const root = target?.closest?.(config.composer);
      const within = root?.querySelector?.(config.editors);
      if (within) return within;
      const editors = [...doc.querySelectorAll(config.editors)].filter(visible);
      return explicitSend ? editors.at(-1) || null : null;
    }
    function begin(editor, trigger, explicitSend = false) {
      context();
      const a = adapter();
      if (!a || !enabled() || !editor) return;
      const text = readEditor(editor);
      if (!text && !explicitSend) return;
      // A single send can emit keydown, click and submit in quick succession.
      if (pending && pending.text === text && Date.now() - pending.sentAt < 2500) return;
      const users = a.userMessages();
      const answers = a.candidates();
      resetPending();
      pending = {
        text,
        sentAt: Date.now(),
        started: Date.now(),
        conversation: a.conversationId() || route,
        beforeUsers: new Set(users.map((node) => nodeId(a, node))),
        beforeAnswers: new Set(answers.map((node) => nodeId(a, node))),
        userId: "",
        userNode: null,
        userIndex: -1,
        userText: "",
        signature: "",
        serial: ++serial,
        trigger,
        inferred: false
      };
      if (text) draft = { text, at: Date.now() };
      trace(`${siteName}_prompt_sent`, { attempt: pending.serial, trigger, text_length: text.length });
      queueScan();
    }
    function clicked(event) {
      if (!event.isTrusted) return;
      const button = event.target?.closest?.('button, [role="button"]');
      if (!button || button.disabled || button.getAttribute("aria-disabled") === "true") return;
      const config = siteConfig();
      const editor = editorFor(button, true);
      if (!editor) return;
      const label = `${button.getAttribute("aria-label") || ""} ${button.getAttribute("title") || ""}`.trim();
      const explicit = button.matches(config.sendButtons) || config.sendLabels.test(label);
      const submit = button.matches('button[type="submit"], input[type="submit"]') &&
        Boolean(button.form?.contains?.(editor) || button.closest?.("form")?.contains?.(editor));
      if (!explicit && !submit) return;
      begin(editor, "click", true);
    }
    function keydown(event) {
      if (!event.isTrusted || event.key !== "Enter" || event.isComposing || event.shiftKey) return;
      const editor = event.target?.closest?.(siteConfig().editors);
      if (!editor) return;
      begin(editor, "enter");
    }
    function submit(event) {
      if (!event.isTrusted) return;
      begin(editorFor(event.target), "submit");
    }
    function input(event) {
      if (!event.isTrusted) return;
      const editor = event.target?.closest?.(siteConfig().editors);
      if (!editor) return;
      const text = readEditor(editor);
      draft = text ? { text, at: Date.now() } : null;
    }
    function pendingUser(a) {
      const users = a.userMessages();
      if (!users.length) return null;
      if (pending.userId) {
        const byId = users.find((node) => nodeId(a, node) === pending.userId);
        if (byId) return { node: byId, index: users.indexOf(byId) };
      }
      if (pending.userIndex >= 0 && users[pending.userIndex]) {
        const samePosition = users[pending.userIndex];
        if (!pending.beforeUsers.has(nodeId(a, samePosition)) &&
            normalize(a.userText(samePosition)) === pending.userText) {
          return { node: samePosition, index: pending.userIndex };
        }
      }
      const latest = users.at(-1);
      if (!latest || pending.beforeUsers.has(nodeId(a, latest))) return null;
      const latestText = normalize(a.userText(latest));
      if (siteName === "chatgpt" && pending.text && latestText !== pending.text) return null;
      // Without a re-render ID, accept the newest turn.  An already matched
      // turn must still match its text so an unrelated later turn is ignored.
      if (pending.userId && latestText !== pending.userText) return null;
      return { node: latest, index: users.length - 1 };
    }
    function answerAfter(a, user) {
      // A later question must never donate its answer to an older pending send.
      if (siteName === "chatgpt" && a.userMessages().at(-1) !== user) return null;
      let answer = null;
      for (const node of a.candidates()) {
        if (node === user || user.contains(node) || node.contains(user)) continue;
        if (user.compareDocumentPosition(node) & 4) answer = node;
      }
      return answer;
    }
    function rememberAnswer(key) {
      seenAnswers.add(key);
      api.storage.local.get({ irodoriSeenTurnAnswers: [] }).then((stored) => {
        const previous = Array.isArray(stored.irodoriSeenTurnAnswers) ? stored.irodoriSeenTurnAnswers : [];
        return api.storage.local.set({
          irodoriSeenTurnAnswers: [key, ...previous.filter((value) => value !== key)].slice(0, 300)
        });
      }).catch(() => {});
    }
    function armFromUser(a, node, index) {
      const users = a.userMessages();
      const text = normalize(a.userText(node));
      resetPending();
      pending = {
        text,
        sentAt: Date.now(),
        started: Date.now(),
        conversation: a.conversationId() || route,
        beforeUsers: new Set(users.filter((item) => item !== node).map((item) => nodeId(a, item))),
        beforeAnswers: new Set(a.candidates().map((item) => nodeId(a, item))),
        userId: nodeId(a, node),
        userNode: node,
        userIndex: index,
        userText: text,
        signature: "",
        serial: ++serial,
        trigger: "new_user_turn",
        inferred: true
      };
      rememberUser(a, node, index);
      trace(`${siteName}_user_inferred`, { attempt: pending.serial, user_text_length: text.length, index });
    }
    function considerNewUserTurn() {
      if (!baselineReady || pending) return;
      const a = adapter();
      if (!a) return;
      const users = a.userMessages();
      if (!users.length) return;
      const unknown = [];
      users.forEach((node, index) => {
        const id = nodeId(a, node);
        const signature = userSignature(a, node, index);
        if (!knownUsers.has(id) && !knownUserPositions.has(signature)) unknown.push({ node, index, id, signature });
      });
      if (!unknown.length) return;
      if (unknown.length !== 1 || unknown[0].index !== users.length - 1) {
        for (const item of unknown) {
          knownUsers.add(item.id);
          knownUserPositions.add(item.signature);
        }
        trace(`${siteName}_history_batch_ignored`, {
          new_user_count: unknown.length, conversation_id: route
        });
        return;
      }
      const candidate = unknown[0];
      const recentDraft = draft && Date.now() - draft.at < 2 * 60 * 1000;
      if (!recentDraft && Date.now() - routeChangedAt < inferredGuardMs) {
        knownUsers.add(candidate.id);
        knownUserPositions.add(candidate.signature);
        return;
      }
      // A lazy history turn normally arrives with an already completed answer.
      // Do not infer a send merely because one old turn was appended late.
      const liveAnswer = a.candidates().some((node) => {
        if (!(candidate.node.compareDocumentPosition(node) & 4)) return false;
        return !a.text(node) || a.streaming(node);
      });
      if (!recentDraft && !liveAnswer) {
        markCurrentKnown(a);
        trace(`${siteName}_history_turn_ignored`, { conversation_id: route, index: candidate.index });
        return;
      }
      armFromUser(a, candidate.node, candidate.index);
      queueScan();
    }
    function scan() {
      context();
      const a = adapter();
      if (stopped || !a || !enabled()) return;
      if (pending) {
        if (Date.now() - pending.started > 5 * 60 * 1000) {
          trace(`${siteName}_detection_timeout`, { attempt: pending.serial, matchedUser: Boolean(pending.userId) });
          resetPending();
          markCurrentKnown(a);
          return;
        }
        if (pending.finalizing) return;
        const user = pendingUser(a);
        if (!user) return;
        if (!pending.userId) {
          pending.userId = nodeId(a, user.node);
          pending.userIndex = user.index;
          pending.userText = normalize(a.userText(user.node));
          pending.userNode = user.node;
          trace(`${siteName}_user_matched`, { attempt: pending.serial });
        }
        const answer = answerAfter(a, user.node);
        if (!answer) {
          if (timer) {
            win.clearTimeout(timer);
            timer = 0;
          }
          pending.signature = "";
          return;
        }
        const text = a.text(answer);
        if (!text || a.streaming(answer)) {
          if (timer) {
            win.clearTimeout(timer);
            timer = 0;
          }
          pending.signature = "";
          return;
        }
        const signature = `${answerIdentity(a, answer, user.index)}|${text}`;
        if (timer && pending.signature === signature) return;
        win.clearTimeout(timer);
        pending.signature = signature;
        const token = pending.serial;
        timer = win.setTimeout(() => finalize(token, signature), settleMs);
        return;
      }
      considerNewUserTurn();
    }
    async function finalize(token, expectedSignature) {
      timer = 0;
      context();
      if (stopped || !enabled() || !pending || pending.serial !== token) return;
      pending.finalizing = true;
      const retry = () => {
        if (pending && pending.serial === token) {
          pending.finalizing = false;
          pending.signature = "";
        }
        queueScan();
      };
      const a = adapter();
      if (!a) return retry();
      const user = pendingUser(a);
      if (!user) return retry();
      const answer = answerAfter(a, user.node);
      if (!answer) return retry();
      const text = a.text(answer);
      const signature = `${answerIdentity(a, answer, user.index)}|${text}`;
      if (!text || a.streaming(answer) || signature !== expectedSignature) return retry();
      try {
        const textHash = await hash(text);
        context();
        if (stopped || !enabled() || !pending || pending.serial !== token) return;
        const refreshed = adapter();
        if (!refreshed) return retry();
        const refreshedUser = pendingUser(refreshed);
        const refreshedAnswer = refreshedUser && answerAfter(refreshed, refreshedUser.node);
        if (!refreshedUser || !refreshedAnswer) return retry();
        const refreshedText = refreshed.text(refreshedAnswer);
        const refreshedSignature = `${answerIdentity(refreshed, refreshedAnswer, refreshedUser.index)}|${refreshedText}`;
        if (refreshedText !== text || refreshedSignature !== expectedSignature || refreshed.streaming(refreshedAnswer)) return retry();
        const conversation = pending.conversation || refreshed.conversationId() || route;
        const messageId = answerIdentity(refreshed, refreshedAnswer, refreshedUser.index);
        const answerKey = `${siteName}|${conversation}|${messageId}`;
        if (seenAnswers.has(answerKey)) {
          trace(`${siteName}_duplicate_skip`, { attempt: token, answer_key: answerKey });
          resetPending();
          markCurrentKnown(refreshed);
          return;
        }
        trace(`${siteName}_answer_detected`, { attempt: token, conversation_id: conversation, message_id: messageId });
        const answerPayload = { conversationId: conversation, messageId, textHash };
        const result = await request("enqueue", { text, source: siteName, answer: answerPayload });
        rememberAnswer(answerKey);
        // Engine startup can delay enqueue. Do not erase a newer user turn
        // submitted while this answer was waiting for the background reply.
        if (pending && pending.serial === token) {
          pending = null;
          draft = null;
          markCurrentKnown(refreshed);
        }
        trace(`${siteName}_enqueue_result`, {
          attempt: token, queued: Boolean(result?.queued), ignored: Boolean(result?.ignored), duplicate: Boolean(result?.duplicate)
        });
        if (result?.queued) toast(`${siteName === "gemini" ? "Gemini" : "ChatGPT"}の最新回答を読み上げます。`);
      } catch (error) {
        retry();
        trace(`${siteName}_enqueue_error`, { attempt: token, error: String(error?.message || error) });
        toast(error.message || "Irodori読み上げに失敗しました。", true);
      }
    }
    function queueScan() {
      if (!stopped && !scanTimer) scanTimer = win.setTimeout(() => { scanTimer = 0; scan(); }, scanMs);
    }
    function changed(changes, area) {
      if (area !== "local") return;
      if (changes.timingDebug) settings.timingDebug = Boolean(changes.timingDebug.newValue);
      let reset = false;
      for (const key of ["chatgptEnabled", "geminiEnabled", "readCodeBlocks"]) {
        if (changes[key]) {
          settings[key] = Boolean(changes[key].newValue);
          reset = true;
        }
      }
      if (reset) {
        resetPending();
        draft = null;
        beginBaseline();
      }
      queueScan();
    }
    doc.addEventListener("input", input, true);
    doc.addEventListener("click", clicked, true);
    doc.addEventListener("keydown", keydown, true);
    doc.addEventListener("submit", submit, true);
    win.addEventListener("popstate", queueScan);
    api.storage.onChanged.addListener(changed);
    api.storage.local.get({
      chatgptEnabled: false, geminiEnabled: false, readCodeBlocks: false, timingDebug: false, irodoriSeenTurnAnswers: []
    }).then((value) => {
      if (stopped) return;
      settings = { ...settings, ...value };
      for (const key of Array.isArray(value.irodoriSeenTurnAnswers) ? value.irodoriSeenTurnAnswers : []) seenAnswers.add(key);
      const a = adapter();
      siteName = a?.name || "";
      route = a?.conversationId() || "";
      chatPath = win.location.pathname;
      beginBaseline();
      observer = new win.MutationObserver(queueScan);
      observer.observe(doc.documentElement, {
        childList: true, subtree: true, characterData: true, attributes: true,
        attributeFilter: ["id", "data-message-id", "data-turn-id", "data-response-id", "data-message-role",
          "data-message-author-role", "data-conversation-role", "data-message-streaming", "data-message-complete",
          "data-talvt-turn-state", "data-is-streaming", "data-conversation-id", "aria-busy", "hidden"]
      });
      interval = win.setInterval(queueScan, 1500);
      queueScan();
    }).catch((error) => toast(error.message, true));
    return { stop() {
      stopped = true;
      resetPending();
      win.clearTimeout(scanTimer); win.clearTimeout(baselineTimer); win.clearInterval(interval);
      observer?.disconnect();
      doc.removeEventListener("input", input, true);
      doc.removeEventListener("click", clicked, true);
      doc.removeEventListener("keydown", keydown, true);
      doc.removeEventListener("submit", submit, true);
      win.removeEventListener("popstate", queueScan);
      api.storage.onChanged.removeListener(changed);
    } };
  }
  return { start };
})();
