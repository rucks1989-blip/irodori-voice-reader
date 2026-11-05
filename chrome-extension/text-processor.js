(function (root) {
  "use strict";

  const ENGINE_ENDPOINTS = Object.freeze({
    get standard() { return root.IrodoriInstallConfig?.apiBase || "http://127.0.0.1:8765"; },
    get voice_stable() { return root.IrodoriInstallConfig?.apiBase || "http://127.0.0.1:8765"; }
  });

  const LEGACY_ENGINE_ENDPOINTS = Object.freeze({
    voice_stable: "http://127.0.0.1:8766"
  });

  const ENGINE_ALIASES = Object.freeze({
    normal: "standard",
    default: "standard",
    standard_api: "standard",
    standardapi: "standard",
    stable: "voice_stable",
    safe: "voice_stable",
    voice_stable_api: "voice_stable",
    voice_stable_api_v2: "voice_stable",
    voice_stable_speaker: "voice_stable",
    voicestable: "voice_stable"
  });

  const DEFAULT_SETTINGS = {
    schemaVersion: 1,
    apiMode: "managed",
    customApiUrl: "",
    // Optional timing diagnostics for adapter/planner/TTS/playback latency.
    timingDebug: false,
    engine: "standard",
    apiUrl: ENGINE_ENDPOINTS.standard,
    chatgptEnabled: false,
    geminiEnabled: false,
    museEnabled: false,
    autoPlay: true,
    wavSaveEnabled: false,
    wavSaveDirectory: "",
    // Kept for storage compatibility; the selected engine is now started
    // automatically during background initialization without a user toggle.
    apiAutoStart: true,
    defaultVoice: "yui",
    removeMarkdown: true,
    readUrls: false,
    readCodeBlocks: false,
    excludedStrings: [],
    regexRules: [],
    unwantedUiStrings: ["コピーする", "編集する", "高評価", "低評価", "共有する"],
    characterMappings: [],
    // Unregistered 【...】 text is ordinary prose by default.  Keep the
    // legacy "default" value as an alias when reading older settings.
    unknownTagBehavior: "keep",
    narrationEnabled: false,
    // "ナレーション" is a control tag, not a runtime Voice ID.  Keep this
    // role unset by default so a new profile falls back to its configured
    // defaultVoice until the user selects a narration Voice.
    narratorVoice: "",
    dialogueVoice: "yui",
    characterTagPriority: true,
    speakerTagOpen: "【",
    speakerTagClose: "】",
    dialoguePairs: ["「」", "『』"],
    longTextEnabled: true,
    longTextTargetSeconds: 25,
    longTextMaxChars: 120,
    longTextPreserveDialogue: true,
    longTextKeepCharacterTagsSeparate: true,
    presets: {},
    // Reserved now so per-Voice generation policy can be added without
    // changing the character/tag schema. Values: inherit/standard/voice_stable.
    voiceEngineOverrides: {},
    currentPreset: "builtin:chat",
    settingsVersion: 6
  };

  const BUILTIN_PRESETS = {
    chat: {
      label: "通常チャット",
      settings: { narrationEnabled: false, removeMarkdown: true, readUrls: false, readCodeBlocks: false }
    },
    character: {
      label: "キャラクター会話",
      settings: { narrationEnabled: false, characterTagPriority: true, removeMarkdown: true, readUrls: false, readCodeBlocks: false }
    },
    story: {
      label: "ストーリーテラー",
      settings: { narrationEnabled: true, characterTagPriority: true, removeMarkdown: true, readUrls: false, readCodeBlocks: false }
    },
    novel: {
      label: "小説朗読",
      settings: { narrationEnabled: true, characterTagPriority: true, removeMarkdown: false, readUrls: false, readCodeBlocks: false }
    }
  };

  function normalizeEngine(value, fallback = "standard") {
    const raw = String(value ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
    if (Object.prototype.hasOwnProperty.call(ENGINE_ENDPOINTS, raw)) return raw;
    if (Object.prototype.hasOwnProperty.call(ENGINE_ALIASES, raw)) return ENGINE_ALIASES[raw];
    return fallback;
  }

  function normalizeApiMode(value, fallback = "managed") {
    return String(value ?? "").trim().toLowerCase() === "custom" ? "custom" : fallback;
  }

  function engineForEndpoint(value, fallback = null) {
    const endpoint = String(value || "").trim().replace(/\/$/, "");
    for (const [engine, url] of Object.entries(LEGACY_ENGINE_ENDPOINTS)) {
      if (endpoint === url) return engine;
    }
    for (const [engine, url] of Object.entries(ENGINE_ENDPOINTS)) {
      if (endpoint === url) return engine;
    }
    return fallback;
  }

  function migrateSettings(settings) {
    const source = settings && typeof settings === "object" && !Array.isArray(settings) ? settings : {};
    const patch = {};
    const mode = source.apiMode === undefined
      ? (source.customApiUrl ? "custom" : null)
      : normalizeApiMode(source.apiMode);
    if (mode && source.apiMode !== mode) patch.apiMode = mode;
    const endpointEngine = engineForEndpoint(source.apiUrl);
    const engine = normalizeEngine(source.engine, endpointEngine || "standard");
    if (source.engine !== undefined && source.engine !== engine) patch.engine = engine;
    if (source.engine === undefined && endpointEngine) patch.engine = endpointEngine;
    if (mode !== "custom" && source.apiUrl !== ENGINE_ENDPOINTS[engine]) {
      patch.apiUrl = ENGINE_ENDPOINTS[engine];
    }
    if (mode === "custom" && source.customApiUrl === undefined && source.apiUrl) {
      patch.customApiUrl = String(source.apiUrl).trim();
    }
    return patch;
  }

  function mergedSettings(settings) {
    const source = settings && typeof settings === "object" && !Array.isArray(settings) ? settings : {};
    const merged = Object.assign({}, DEFAULT_SETTINGS, source);
    merged.apiMode = normalizeApiMode(source.apiMode === undefined ? merged.apiMode : source.apiMode);
    const endpointEngine = engineForEndpoint(source.apiUrl);
    merged.engine = normalizeEngine(source.engine, endpointEngine || "standard");
    merged.customApiUrl = String(merged.customApiUrl || "").trim();
    merged.apiUrl = merged.apiMode === "custom"
      ? (merged.customApiUrl || String(merged.apiUrl || "").trim())
      : ENGINE_ENDPOINTS[merged.engine];
    const behavior = String(merged.unknownTagBehavior || "keep").toLowerCase();
    merged.unknownTagBehavior = behavior === "skip" ? "skip" : (behavior === "read" ? "read" : "keep");
    // Blank/invalid delimiters fall back as a pair for legacy settings.
    const delimiters = [merged.speakerTagOpen, merged.speakerTagClose];
    const valid = delimiters.every((value) => typeof value === "string" && value.trim() && !/[\r\n]/.test(value));
    merged.speakerTagOpen = valid ? delimiters[0] : "【";
    merged.speakerTagClose = valid ? delimiters[1] : "】";
    merged.schemaVersion = 1;
    merged.characterMappings = Array.isArray(merged.characterMappings)
      ? merged.characterMappings.filter((item) => item && item.tag && item.voice).map((item) => ({
        enabled: item.enabled !== false,
        tag: String(item.tag).trim().replace(/^【|】$/g, ""),
        voice: String(item.voice),
        manual: Boolean(item.manual)
      })) : [];
    merged.voiceEngineOverrides = merged.voiceEngineOverrides && typeof merged.voiceEngineOverrides === "object"
      ? { ...merged.voiceEngineOverrides } : {};
    return merged;
  }

  function normalizeCharacterTag(value) {
    return String(value == null ? "" : value).trim().replace(/^\u3010|\u3011$/g, "").trim();
  }

  const NARRATION_TAGS = new Set(["ナレーション", "narration", "narrator"]);

  function isNarrationTag(value) {
    const tag = normalizeCharacterTag(value);
    return NARRATION_TAGS.has(tag) || NARRATION_TAGS.has(tag.toLowerCase());
  }

  function voiceDefinitionsForSettings(settings) {
    const source = settings && typeof settings === "object" ? settings : {};
    const displayNames = source.voiceDisplayNames && typeof source.voiceDisplayNames === "object" && !Array.isArray(source.voiceDisplayNames)
      ? source.voiceDisplayNames : {};
    const values = [];
    if (Array.isArray(source.cachedVoices)) values.push(...source.cachedVoices);
    if (Array.isArray(source.voiceDefinitions)) values.push(...source.voiceDefinitions);
    const result = [];
    const seen = new Set();
    for (const value of values) {
      if (!value || typeof value !== "object") continue;
      const id = String(value.id == null ? "" : value.id).trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const configuredName = displayNames[id];
      const fallbackName = value.displayName || value.display_name || value.name || id;
      const displayName = String(configuredName == null ? fallbackName : configuredName).trim();
      result.push({ id, displayName });
    }
    for (const [idValue, nameValue] of Object.entries(displayNames)) {
      const id = String(idValue || "").trim();
      const displayName = String(nameValue == null ? "" : nameValue).trim();
      if (id && displayName && !seen.has(id)) {
        seen.add(id);
        result.push({ id, displayName });
      }
    }
    return result;
  }

  function resolveCharacterTag(tag, rawSettings) {
    const settings = mergedSettings(rawSettings);
    const normalizedTag = normalizeCharacterTag(tag);
    if (!normalizedTag) return null;
    // Narration is a reserved control token.  It must never be resolved from
    // /voices, display names, or characterMappings as if it were a character.
    if (isNarrationTag(normalizedTag)) return null;
    const definitions = voiceDefinitionsForSettings(settings);
    // IDs are the stable internal key and therefore have priority over all
    // human-readable names and user-defined aliases.
    const idMatch = definitions.find((voice) => voice.id === normalizedTag);
    if (idMatch) return { voice: idMatch.id, kind: "id", tag: normalizedTag };
    // A duplicate display name is not safe to guess.  An explicit alias may
    // still resolve it below, but no partial or first-match selection occurs.
    const nameMatches = definitions.filter((voice) => voice.displayName === normalizedTag);
    if (nameMatches.length === 1) {
      return { voice: nameMatches[0].id, kind: "displayName", tag: normalizedTag };
    }
    const alias = (Array.isArray(settings.characterMappings) ? settings.characterMappings : [])
      .find((item) => item && item.enabled !== false
        && normalizeCharacterTag(item.tag) === normalizedTag
        && String(item.voice || "").trim());
    if (alias) return { voice: String(alias.voice).trim(), kind: "alias", tag: normalizedTag };
    return null;
  }

  function mappedVoiceForTag(tag, settings) {
    const resolution = resolveCharacterTag(tag, settings);
    return resolution ? resolution.voice : "";
  }

  function configuredDialoguePairs(settings) {
    const configured = settings && Array.isArray(settings.dialoguePairs)
      ? settings.dialoguePairs : ["「」", "『』"];
    return configured.map((value, index) => {
      const characters = Array.from(String(value || "").trim());
      return characters.length === 2
        ? { open: characters[0], close: characters[1], index }
        : null;
    }).filter(Boolean);
  }

  function isConfiguredDialogueToken(match, settings) {
    const token = String(match && match[0] || "");
    return configuredDialoguePairs(settings).some((pair) =>
      token.startsWith(pair.open) && token.endsWith(pair.close) && token.length > pair.open.length + pair.close.length);
  }

  // Resolve the semantic route once, before a segment enters either browser's
  // queue.  Later stages may validate the selected ID against /voices, but
  // they must not reinterpret the segment using the current global Voice.
  function voiceRouteForSegment(segment, rawSettings) {
    const settings = mergedSettings(rawSettings);
    const value = segment && typeof segment === "object" ? segment : {};
    const tag = normalizeCharacterTag(value.tag);
    const tagResolution = resolveCharacterTag(tag, settings);
    const mappedVoice = String(value.mappedVoice || (tagResolution && tagResolution.voice) || "").trim();
    const fallbackVoice = String(value.voice || settings.defaultVoice || "").trim();
    const dialogue = Boolean(value.dialogue);
    const narration = Boolean(value.narration);

    // An explicit narration tag is a state as well. It must be resolved
    // before dialogue because quoted text inside 【ナレーション】 is still
    // narration.
    if (narration) {
      return {
        voice: String(value.explicitSpeaker
          ? (value.voice || settings.narratorVoice || settings.defaultVoice || fallbackVoice)
          : (settings.narratorVoice || settings.defaultVoice || fallbackVoice)).trim(),
        source: "narration",
        tag: "",
        mappedVoice: "",
        dialogue: false,
        narration: true,
        tagResolution: "narration"
      };
    }
    if (value.tagged && tag && mappedVoice) {
      // The switch is an independent priority rule. With narration enabled,
      // tagged dialogue falls back to dialogueVoice when priority is OFF.
      if (settings.characterTagPriority && (!settings.narrationEnabled || dialogue)) {
        return {
          voice: mappedVoice,
          source: "character_tag",
          tag,
          mappedVoice,
          dialogue,
          narration: false,
          tagResolution: String(value.tagResolution || (tagResolution && tagResolution.kind) || "")
        };
      }
      if (settings.narrationEnabled && dialogue) {
        return {
          voice: String(settings.dialogueVoice || settings.defaultVoice || "").trim(),
          source: "untagged_dialogue",
          tag: "",
          mappedVoice: "",
          dialogue: true,
          narration: false,
          tagResolution: ""
        };
      }
      if (!settings.narrationEnabled) {
        return {
          voice: String(settings.defaultVoice || fallbackVoice).trim(),
          source: "default",
          tag: "",
          mappedVoice: "",
          dialogue: false,
          narration: false,
          tagResolution: ""
        };
      }
    }
    if (dialogue) {
      return {
        voice: String(settings.dialogueVoice || fallbackVoice || settings.defaultVoice || "").trim(),
        source: "untagged_dialogue",
        tag: "",
        mappedVoice: "",
        dialogue: true,
        narration: false,
        tagResolution: ""
      };
    }
    return {
      voice: fallbackVoice || String(settings.defaultVoice || "").trim(),
      source: "default",
      tag: tag && value.tagged ? tag : "",
      mappedVoice: "",
      dialogue: false,
      narration: false,
      tagResolution: ""
    };
  }

  function characterMappingCandidates(voices, mappings) {
    const known = Array.isArray(voices) ? voices.filter((voice) => voice && voice.id) : [];
    const entries = (Array.isArray(mappings) ? mappings : []).filter((item) => item && item.tag && item.voice);
    const saved = new Map(entries.filter((item) => item.manual !== true).map((item) => [String(item.voice), item]));
    const knownIds = new Set(known.map((voice) => String(voice.id)));
    const candidates = known.map((voice) => {
      const id = String(voice.id);
      const item = saved.get(id);
      saved.delete(id);
      return {
        enabled: Boolean(item && item.enabled !== false),
        tag: String((item && item.tag) || id),
        voice: id,
        voiceName: String(voice.name || id),
        manual: false
      };
    });
    // A manual tag is an additional alias, not a replacement for the
    // Voice's automatic display-name tag. Keep it as a separate editable row
    // even when it points to a Voice already present in /voices.
    for (const item of entries.filter((value) => value.manual === true)) {
      candidates.push({
        ...item,
        enabled: item.enabled !== false,
        voiceName: String(known.find((voice) => String(voice.id) === String(item.voice))?.name || item.voice),
        manual: true,
        missing: !knownIds.has(String(item.voice))
      });
    }
    for (const item of saved.values()) {
      candidates.push({ ...item, enabled: item.enabled !== false, voiceName: String(item.voice), manual: true, missing: true });
    }
    return candidates.sort((left, right) => Number(right.enabled) - Number(left.enabled) || left.voiceName.localeCompare(right.voiceName, "ja"));
  }

  // Keep the UI grouping rule browser-neutral: enabled mappings are shown in
  // the compact "使用中" list, while disabled mappings stay collapsed below.
  // The returned arrays are new so callers cannot mutate saved settings.
  function characterMappingGroups(candidates) {
    const items = Array.isArray(candidates) ? candidates : [];
    return {
      active: items.filter((item) => item && item.enabled !== false),
      inactive: items.filter((item) => item && item.enabled === false)
    };
  }

  function settingsExport(settings) {
    const value = mergedSettings(settings);
    const keys = ["schemaVersion", "settingsVersion", "apiMode", "customApiUrl", "engine", "defaultVoice",
      "voiceDisplayNames", "voiceEngineOverrides", "characterMappings", "unknownTagBehavior", "narrationEnabled",
      "narratorVoice", "dialogueVoice", "characterTagPriority", "speakerTagOpen", "speakerTagClose", "dialoguePairs", "wavSaveEnabled",
      "wavSaveDirectory", "presets", "currentPreset"];
    return Object.fromEntries(keys.filter((key) => value[key] !== undefined).map((key) => [key, value[key]]));
  }

  function classifyApiError(error, status = 0, body = null) {
    const message = String((body && (body.error || body.message)) || (error && error.message) || "");
    const lower = message.toLowerCase();
    const retryableStatus = status === 408 || status === 425 || status === 429 || status >= 500;
    if (!status || /failed to fetch|networkerror|接続/.test(lower)) return { code: "api_unreachable", message: "Irodori APIへ接続できません。", retryable: true };
    if (/backend/.test(lower)) return { code: "backend_unreachable", message: "音声生成backendへ接続できません。", retryable: retryableStatus };
    if (/voice.*not found|unknown voice|voice不存在/.test(lower)) return { code: "voice_not_found", message: "指定したVoiceが見つかりません。", retryable: false };
    if (/speaker.*(embedding|safetensor).*(load|fail|error)/.test(lower)) return { code: "speaker_embedding", message: "speaker embeddingの読み込みに失敗しました。", retryable: false };
    if (/reference.*wav|wav.*reference/.test(lower)) return { code: "reference_wav", message: "参照WAVの読み込みに失敗しました。", retryable: false };
    if (/cuda.*out of memory|cuda oom|out of memory/.test(lower)) return { code: "cuda_oom", message: "GPUメモリが不足しています。", retryable: false };
    if (/timeout|timed out/.test(lower) || status === 408 || status === 504) return { code: "timeout", message: "音声生成がタイムアウトしました。", retryable: true };
    if (/rescue.*exhaust/.test(lower)) return { code: "stable_rescue_exhausted", message: "声質安定化の再試行上限に達しました。", retryable: false };
    if (/decode|riff|wave|invalid response/.test(lower)) return { code: "invalid_audio", message: "生成音声の形式が不正です。", retryable: false };
    return {
      code: "tts_failed",
      message: message || `音声生成に失敗しました（HTTP ${status}）。`,
      retryable: retryableStatus
    };
  }

  function apiUrlForEngine(engine) {
    return ENGINE_ENDPOINTS[engine] || ENGINE_ENDPOINTS.standard;
  }

  function apiUrlForSettings(settings) {
    const merged = mergedSettings(settings);
    return merged.apiMode === "custom" ? (merged.customApiUrl || merged.apiUrl) : apiUrlForEngine(merged.engine);
  }

  // engine is the single source of truth for the API endpoint.  UI pages may
  // persist ordinary form settings, but they must never write a stale engine
  // or apiUrl snapshot back after a switch has completed.
  function settingsPatchWithoutEngine(settings) {
    const patch = Object.assign({}, settings || {});
    delete patch.engine;
    delete patch.apiUrl;
    // API mode and custom endpoint are changed through the background
    // set-api-mode action, so ordinary form saves cannot race a mode switch.
    delete patch.apiMode;
    delete patch.customApiUrl;
    // The live Voice catalog belongs to the background/API, not an editor's
    // possibly older form snapshot. Ordinary saves must not resurrect it.
    delete patch.cachedVoices;
    return patch;
  }

  function engineStoragePatch(engine) {
    const selected = normalizeEngine(engine);
    return { engine: selected, apiUrl: ENGINE_ENDPOINTS[selected] };
  }

  function stableSettingsValue(value) {
    if (Array.isArray(value)) return value.map(stableSettingsValue);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableSettingsValue(value[key])]));
    }
    return value;
  }

  function settingsValueEqual(left, right) {
    return JSON.stringify(stableSettingsValue(left)) === JSON.stringify(stableSettingsValue(right));
  }

  // Form pages send only fields the user actually changed. The base snapshot
  // lets the background merge edits made in parallel by the other settings UI.
  function settingsDelta(value, baseline) {
    const source = settingsPatchWithoutEngine(value || {});
    const base = baseline || {};
    return Object.fromEntries(Object.entries(source).filter(([key, item]) =>
      item !== undefined && !settingsValueEqual(item, base[key])));
  }

  function mappingKey(item) {
    if (!item || typeof item !== "object") return "";
    const voice = String(item.voice || "").trim();
    const tag = normalizeCharacterTag(item.tag);
    if (item.manual === true && tag) return `manual:${tag}:${voice}`;
    if (voice) return `voice:${voice}`;
    return tag ? `tag:${tag}` : "";
  }

  function mergeKeyedRows(currentRows, baseRows, editedRows, keyFor) {
    if (!Array.isArray(currentRows) || !Array.isArray(baseRows) || !Array.isArray(editedRows)) return editedRows;
    const toMap = (rows) => {
      const result = new Map();
      for (const row of rows) {
        const key = keyFor(row);
        if (!key || result.has(key)) return null;
        result.set(key, row);
      }
      return result;
    };
    const current = toMap(currentRows);
    const base = toMap(baseRows);
    const edited = toMap(editedRows);
    // Legacy lists with duplicate or unkeyed rows are replaced as a whole
    // rather than guessed at or partially discarded.
    if (!current || !base || !edited) return editedRows;
    const merged = currentRows.map((row) => ({ ...row }));
    for (const key of new Set([...base.keys(), ...edited.keys()])) {
      const before = base.get(key);
      const after = edited.get(key);
      if (settingsValueEqual(before, after)) continue;
      const index = merged.findIndex((row) => keyFor(row) === key);
      if (after === undefined) {
        if (index >= 0) merged.splice(index, 1);
      } else if (index >= 0) {
        merged[index] = { ...after };
      } else {
        merged.push({ ...after });
      }
    }
    return merged;
  }

  function mergeObjectChanges(currentValue, baseValue, editedValue) {
    if (!currentValue || typeof currentValue !== "object" || Array.isArray(currentValue)
      || !baseValue || typeof baseValue !== "object" || Array.isArray(baseValue)
      || !editedValue || typeof editedValue !== "object" || Array.isArray(editedValue)) return editedValue;
    const merged = { ...currentValue };
    for (const key of new Set([...Object.keys(baseValue), ...Object.keys(editedValue)])) {
      const before = baseValue[key];
      const after = editedValue[key];
      if (settingsValueEqual(before, after)) continue;
      if (Object.prototype.hasOwnProperty.call(editedValue, key)) merged[key] = after;
      else delete merged[key];
    }
    return merged;
  }

  function mergeSettingsPatch(currentValue, patchValue, baseValue = {}) {
    const patch = settingsPatchWithoutEngine(patchValue || {});
    const current = currentValue || {};
    const base = baseValue || {};
    const merged = { ...current, ...patch };
    if (Array.isArray(patch.characterMappings) && Array.isArray(base.characterMappings)) {
      merged.characterMappings = mergeKeyedRows(
        current.characterMappings || [], base.characterMappings, patch.characterMappings, mappingKey);
    }
    for (const key of ["presets", "voiceDisplayNames", "voiceEngineOverrides"]) {
      if (patch[key] && base[key]) merged[key] = mergeObjectChanges(current[key] || {}, base[key], patch[key]);
    }
    return merged;
  }

  function reconcileSettingsEditor(remoteValue, draftValue, baselineValue) {
    const patch = settingsDelta(draftValue, baselineValue);
    return { patch, settings: mergeSettingsPatch(remoteValue, patch, baselineValue) };
  }

  function cleanText(input, rawSettings) {
    const settings = mergedSettings(rawSettings);
    let text = String(input || "").replace(/\r\n?/g, "\n");
    text = settings.readCodeBlocks
      ? text.replace(/```(?:[^\n]*)\n?([\s\S]*?)```/g, "$1")
      : text.replace(/```[\s\S]*?```/g, " ");
    if (!settings.readUrls) text = text.replace(/(?:https?|ftp):\/\/[^\s<>()]+/gi, " ");
    for (const value of [...(settings.unwantedUiStrings || []), ...(settings.excludedStrings || [])]) {
      if (String(value)) text = text.split(String(value)).join(" ");
    }
    for (const rule of settings.regexRules || []) {
      if (!rule || rule.enabled === false || !rule.pattern) continue;
      try {
        text = text.replace(new RegExp(rule.pattern, rule.flags || "gu"), rule.replacement || " ");
      } catch (_) {
        // Invalid user rules are reported by the options page and ignored at runtime.
      }
    }
    if (settings.removeMarkdown) {
      text = text
        .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
        .replace(/^\s{0,3}#{1,6}\s+/gm, "")
        .replace(/^\s*>+\s?/gm, "")
        .replace(/^\s*[-*+]\s+/gm, "")
        .replace(/^\s*\d+[.)]\s+/gm, "")
        .replace(/[*_~`]{1,3}/g, "")
        .replace(/^\s*[-=_]{3,}\s*$/gm, " ");
    }
    return text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  }

  function tagSegments(text, settings) {
    // Literal scanning supports regex punctuation and multi-character markers.
    const matches = [];
    const open = settings.speakerTagOpen;
    const close = settings.speakerTagClose;
    let offset = 0;
    while (offset < text.length) {
      const start = text.indexOf(open, offset);
      if (start < 0) break;
      const end = text.indexOf(close, start + open.length);
      if (end < 0) break;
      const tag = text.slice(start + open.length, end);
      if (tag.trim() && !/[\r\n]/.test(tag)) {
        const match = [text.slice(start, end + close.length), tag];
        match.index = start;
        matches.push(match);
      }
      offset = end + close.length;
    }
    if (!matches.length) return [{ text, voice: settings.defaultVoice, tagged: false }];
    const unknownBehavior = settings.unknownTagBehavior === "skip" || settings.unknownTagBehavior === "read"
      ? settings.unknownTagBehavior : "keep";
    const resolutionFor = (rawTag) => resolveCharacterTag(rawTag, settings);
    const unresolvedTags = new Set();
    for (const match of matches) {
      const tag = normalizeCharacterTag(match[1]);
      if (!isNarrationTag(tag) && !resolutionFor(tag) && !isConfiguredDialogueToken(match, settings)) unresolvedTags.add(tag);
    }
    if (unresolvedTags.size && typeof console !== "undefined" && typeof console.warn === "function") {
      for (const tag of unresolvedTags) {
        console.warn("[Irodori] unresolved speaker tag", { tag, behavior: unknownBehavior });
      }
    }
    // In the default/keep mode only an explicitly registered character tag
    // or the built-in narration tag is a control marker. Other 【...】 text
    // remains part of the prose.
    const controls = matches.filter((match) => {
      const tag = normalizeCharacterTag(match[1]);
      return isNarrationTag(tag) || Boolean(resolutionFor(tag))
        || (unknownBehavior !== "keep" && !isConfiguredDialogueToken(match, settings));
    });
    if (!controls.length) return [{ text, voice: settings.defaultVoice, tagged: false }];
    const output = [];
    let currentSpeaker = null;
    let cursor = 0;
    const appendStatefulText = (body) => {
      if (!body) return;
      if (!currentSpeaker) {
        output.push({ text: body, voice: settings.defaultVoice, tagged: false });
        return;
      }
      output.push({
        text: body,
        voice: currentSpeaker.voice,
        tag: currentSpeaker.tag,
        tagged: true,
        mappedVoice: currentSpeaker.mappedVoice,
        explicitSpeaker: true,
        speakerState: currentSpeaker.tag,
        narration: currentSpeaker.narration,
        dialogue: false,
        tagResolution: currentSpeaker.tagResolution
      });
    };
    for (let index = 0; index < controls.length; index += 1) {
      const match = controls[index];
      appendStatefulText(text.slice(cursor, match.index));
      const tag = normalizeCharacterTag(match[1]);
      const resolution = resolutionFor(tag);
      if (isNarrationTag(tag) || resolution) {
        const narration = isNarrationTag(tag);
        currentSpeaker = {
          tag,
          voice: narration
            ? (settings.narratorVoice || settings.defaultVoice)
            : resolution.voice,
          mappedVoice: narration ? "" : resolution.voice,
          narration,
          tagResolution: narration ? "narration" : resolution.kind
        };
        cursor = match.index + match[0].length;
        continue;
      }
      const bodyEnd = controls[index + 1] ? controls[index + 1].index : text.length;
      let body = text.slice(match.index + match[0].length, bodyEnd);
      if (unknownBehavior === "skip") body = "";
      if (unknownBehavior === "read") body = [tag, body].join("。");
      if (body) output.push({ text: body, voice: settings.defaultVoice, tag, tagged: false });
      currentSpeaker = null;
      cursor = bodyEnd;
    }
    appendStatefulText(text.slice(cursor));
    return output;
  }

  function narrationSegments(segment, settings) {
    if (!settings.narrationEnabled) return [segment];
    const pairs = configuredDialoguePairs(settings);
    const explicitSpeaker = Boolean(segment && segment.explicitSpeaker);
    // A character tag owns only the immediately following dialogue group.
    // Whitespace separates quotes visually; prose ends the speaker scope.
    let speakerActive = explicitSpeaker;
    const makePart = (text, dialogue) => {
      if (!dialogue && text.trim() && !segment.narration) speakerActive = false;
      if (dialogue && explicitSpeaker && !speakerActive) {
        return {
          ...segment,
          text,
          voice: settings.dialogueVoice || settings.defaultVoice,
          tag: "",
          tagged: false,
          mappedVoice: "",
          explicitSpeaker: false,
          speakerState: "",
          tagResolution: "",
          narration: false,
          dialogue: true
        };
      }
      if (explicitSpeaker) {
        if (segment.narration || !dialogue) {
          return {
            ...segment,
            text,
            voice: settings.narratorVoice || settings.defaultVoice,
            tag: "",
            tagged: false,
            mappedVoice: "",
            explicitSpeaker: false,
            narration: true,
            dialogue: false
          };
        }
        return {
          ...segment,
          text,
          voice: segment.voice,
          narration: false,
          dialogue: true
        };
      }
      if (dialogue) {
        return {
          ...segment,
          text,
          voice: segment.tagged
            ? segment.voice
            : (settings.dialogueVoice || segment.voice || settings.defaultVoice),
          narration: false,
          dialogue: true
        };
      }
      return {
        ...segment,
        text,
        voice: settings.narratorVoice || settings.defaultVoice,
        tag: "",
        tagged: false,
        mappedVoice: "",
        narration: true,
        dialogue: false
      };
    };
    if (!pairs.length) return [makePart(String(segment.text || ""), false)];
    const source = String(segment.text || "");
    const output = [];
    let position = 0;
    while (position < source.length) {
      let nearest = -1;
      let selectedPair = null;
      for (const pair of pairs) {
        const at = source.indexOf(pair.open, position);
        if (at >= 0 && (nearest < 0 || at < nearest
          || (at === nearest && pair.open.length > selectedPair.open.length)
          || (at === nearest && pair.open.length === selectedPair.open.length && pair.index < selectedPair.index))) {
          nearest = at;
          selectedPair = pair;
        }
      }
      if (nearest < 0) {
        output.push(makePart(source.slice(position), false));
        break;
      }
      if (nearest > position) output.push(makePart(source.slice(position, nearest), false));
      const bodyStart = nearest + selectedPair.open.length;
      const end = source.indexOf(selectedPair.close, bodyStart);
      if (end < 0) {
        // An unmatched opener is ordinary prose, not a reason to drop the
        // opener or classify the rest of the page as dialogue.
        output.push(makePart(source.slice(nearest), false));
        break;
      }
      output.push(makePart(source.slice(bodyStart, end), true));
      position = end + selectedPair.close.length;
    }
    return output;
  }

  function processText(input, rawSettings) {
    const settings = mergedSettings(rawSettings);
    const cleaned = cleanText(input, settings);
    if (!cleaned) return [];
    return tagSegments(cleaned, settings)
      .flatMap((segment) => narrationSegments(segment, settings))
      .map((segment) => {
        const text = String(segment.text || "").trim();
        const route = voiceRouteForSegment(segment, settings);
        return { ...segment, text, voice: route.voice, voiceSource: route.source, voiceRoute: route };
      })
      .filter((segment) => segment.text);
  }

  function presetSettings(presetId, customPresets) {
    const [kind, name] = String(presetId || "builtin:chat").split(":");
    if (kind === "builtin" && BUILTIN_PRESETS[name]) return settingsPatchWithoutEngine(BUILTIN_PRESETS[name].settings);
    if (kind === "custom" && customPresets && customPresets[name]) return settingsPatchWithoutEngine(customPresets[name]);
    return null;
  }

  const api = { ENGINE_ENDPOINTS, ENGINE_ALIASES, DEFAULT_SETTINGS, BUILTIN_PRESETS, normalizeEngine, normalizeApiMode, engineForEndpoint, migrateSettings, mergedSettings, normalizeCharacterTag, isNarrationTag, resolveCharacterTag, mappedVoiceForTag, voiceRouteForSegment, characterMappingCandidates, characterMappingGroups, settingsExport, classifyApiError, apiUrlForEngine, apiUrlForSettings, settingsPatchWithoutEngine, engineStoragePatch, settingsDelta, mergeSettingsPatch, reconcileSettingsEditor, presetSettings, cleanText, processText };
  root.IrodoriTextProcessor = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
