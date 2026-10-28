"use strict";

(function (root, factory) {
  const api = factory(root);
  root.IrodoriVoiceReadiness = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  function normalizeVoiceId(value) {
    return String(value == null ? "" : value).trim();
  }

  function normalizeTag(value) {
    return normalizeVoiceId(value).replace(/^【|】$/g, "").trim();
  }

  function isNarrationTag(value) {
    const tag = normalizeTag(value);
    return tag === "ナレーション" || ["narration", "narrator"].includes(tag.toLowerCase());
  }

  function normalizeVoices(values) {
    const seen = new Set();
    const result = [];
    for (const value of Array.isArray(values) ? values : []) {
      if (!value || typeof value !== "object") continue;
      const id = normalizeVoiceId(value.id);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      result.push({ ...value, id });
    }
    return result;
  }

  function findVoice(voices, id) {
    const wanted = normalizeVoiceId(id);
    return wanted ? normalizeVoices(voices).find((voice) => voice.id === wanted) || null : null;
  }

  function resolveCharacterTag(tag, settings) {
    const processor = root && root.IrodoriTextProcessor;
    if (processor && typeof processor.resolveCharacterTag === "function") {
      return processor.resolveCharacterTag(tag, settings);
    }
    const normalizedTag = normalizeTag(tag);
    if (!normalizedTag) return null;
    // This is a reserved control token, never a Voice ID or character alias.
    if (isNarrationTag(normalizedTag)) return null;
    const source = settings && typeof settings === "object" ? settings : {};
    const displayNames = source.voiceDisplayNames && typeof source.voiceDisplayNames === "object" && !Array.isArray(source.voiceDisplayNames)
      ? source.voiceDisplayNames : {};
    const values = [];
    if (Array.isArray(source.cachedVoices)) values.push(...source.cachedVoices);
    if (Array.isArray(source.voiceDefinitions)) values.push(...source.voiceDefinitions);
    const definitions = [];
    const seen = new Set();
    for (const value of values) {
      if (!value || typeof value !== "object") continue;
      const id = normalizeVoiceId(value.id);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const configuredName = displayNames[id];
      definitions.push({ id, displayName: String(configuredName == null
        ? (value.displayName || value.display_name || value.name || id)
        : configuredName).trim() });
    }
    for (const [idValue, nameValue] of Object.entries(displayNames)) {
      const id = normalizeVoiceId(idValue);
      const displayName = normalizeTag(nameValue);
      if (id && displayName && !seen.has(id)) {
        seen.add(id);
        definitions.push({ id, displayName });
      }
    }
    const idMatch = definitions.find((voice) => voice.id === normalizedTag);
    if (idMatch) return { voice: idMatch.id, kind: "id", tag: normalizedTag };
    const nameMatches = definitions.filter((voice) => voice.displayName === normalizedTag);
    if (nameMatches.length === 1) return { voice: nameMatches[0].id, kind: "displayName", tag: normalizedTag };
    const alias = (Array.isArray(source.characterMappings) ? source.characterMappings : [])
      .find((item) => item && item.enabled !== false
        && normalizeTag(item.tag) === normalizedTag
        && normalizeVoiceId(item.voice));
    return alias ? { voice: normalizeVoiceId(alias.voice), kind: "alias", tag: normalizedTag } : null;
  }

  function mappingForTag(tag, settings) {
    const resolution = resolveCharacterTag(tag, settings);
    return resolution ? normalizeVoiceId(resolution.voice) : "";
  }

  function routeForSegment(segment, settings) {
    const value = segment && typeof segment === "object" ? segment : {};
    const snapshot = value.voiceRoute && typeof value.voiceRoute === "object" ? value.voiceRoute : null;
    if (snapshot && normalizeVoiceId(snapshot.voice)) {
      return {
        voice: normalizeVoiceId(snapshot.voice),
        source: ["character_tag", "untagged_dialogue", "narration", "default"].includes(String(snapshot.source))
          ? String(snapshot.source) : "default",
        tag: normalizeTag(snapshot.tag || value.tag),
        mappedVoice: normalizeVoiceId(snapshot.mappedVoice),
        fallbackVoice: normalizeVoiceId(snapshot.fallbackVoice),
        dialogue: Boolean(snapshot.dialogue),
        narration: Boolean(snapshot.narration),
        tagResolution: String(snapshot.tagResolution || "")
      };
    }

    const tag = normalizeTag(value.tag);
    const tagResolution = resolveCharacterTag(tag, settings);
    const mappedVoice = mappingForTag(tag, settings);
    const narrationTag = tag === "ナレーション" || ["narration", "narrator"].includes(tag.toLowerCase());
    if (narrationTag && value.tagged !== false) return {
      voice: normalizeVoiceId(settings && settings.narratorVoice) || normalizeVoiceId(settings && settings.defaultVoice) || normalizeVoiceId(value.voice),
      source: "narration", tag, mappedVoice: "", fallbackVoice: "", dialogue: false, narration: true, tagResolution: "narration"
    };
    // Jobs created before voiceRoute was introduced have a tag but no tagged
    // boolean.  Treat that legacy shape as an explicit mapping, while a
    // narration segment is deliberately excluded so its tag cannot leak.
    const explicitTag = Boolean(tag && value.narration !== true && value.tagged !== false && mappedVoice);
    if (explicitTag) return { voice: mappedVoice, source: "character_tag", tag, mappedVoice, fallbackVoice: "", dialogue: Boolean(value.dialogue), narration: false, tagResolution: String(tagResolution && tagResolution.kind || "") };
    if (value.dialogue) return {
      voice: normalizeVoiceId(settings && settings.dialogueVoice) || normalizeVoiceId(value.voice) || normalizeVoiceId(settings && settings.defaultVoice),
      source: "untagged_dialogue", tag: "", mappedVoice: "", fallbackVoice: "", dialogue: true, narration: false, tagResolution: ""
    };
    if (value.narration) return {
      voice: normalizeVoiceId(settings && settings.narratorVoice) || normalizeVoiceId(settings && settings.defaultVoice) || normalizeVoiceId(value.voice),
      source: "narration", tag: "", mappedVoice: "", fallbackVoice: "", dialogue: false, narration: true, tagResolution: "narration"
    };
    return {
      voice: normalizeVoiceId(value.voice) || normalizeVoiceId(settings && settings.defaultVoice),
      source: "default", tag: "", mappedVoice: "", fallbackVoice: "", dialogue: false, narration: false, tagResolution: ""
    };
  }

  function mappingVoiceForSegment(segment, settings) {
    return routeForSegment(segment, settings).voice;
  }

  function candidateIds(requestedVoice, currentVoice, defaultVoice, voices, allowFallback = true, allowFirstAvailable = true) {
    const candidates = [];
    const add = (id, role) => {
      const value = normalizeVoiceId(id);
      if (value && !candidates.some((item) => item.id === value)) candidates.push({ id: value, role });
    };
    add(requestedVoice, "requested");
    if (!allowFallback) return candidates;
    add(currentVoice, "current");
    add(defaultVoice, "default");
    if (allowFirstAvailable) {
      const first = normalizeVoices(voices)[0];
      if (first) add(first.id, "first_available");
    }
    return candidates;
  }

  function resolveVoice({ requestedVoice, currentVoice, defaultVoice, voices, cachedVoices, allowFallback = true, allowFirstAvailable = true } = {}) {
    const fresh = normalizeVoices(voices);
    const cached = normalizeVoices(cachedVoices);
    const candidates = candidateIds(requestedVoice, currentVoice, defaultVoice, fresh, allowFallback, allowFirstAvailable);
    for (const candidate of candidates) {
      const voice = findVoice(fresh, candidate.id);
      if (voice) return {
        ok: true,
        voice,
        voiceId: voice.id,
        role: candidate.role,
        source: "api",
        requestedVoice: normalizeVoiceId(requestedVoice),
        currentVoice: normalizeVoiceId(currentVoice),
        defaultVoice: normalizeVoiceId(defaultVoice),
        listState: "ready"
      };
    }
    if (!fresh.length) {
      const cachedCandidates = candidateIds(requestedVoice, currentVoice, defaultVoice, cached, allowFallback, allowFirstAvailable);
      for (const candidate of cachedCandidates) {
        const voice = findVoice(cached, candidate.id);
        if (voice) return {
          ok: true,
          voice,
          voiceId: voice.id,
          role: candidate.role,
          source: "cache",
          stale: true,
          requestedVoice: normalizeVoiceId(requestedVoice),
          currentVoice: normalizeVoiceId(currentVoice),
          defaultVoice: normalizeVoiceId(defaultVoice),
          listState: "stale"
        };
      }
    }
    return {
      ok: false,
      requestedVoice: normalizeVoiceId(requestedVoice),
      currentVoice: normalizeVoiceId(currentVoice),
      defaultVoice: normalizeVoiceId(defaultVoice),
      listState: fresh.length ? "ready" : (cached.length ? "stale" : "empty"),
      reason: fresh.length ? "requested_and_fallback_voice_not_found" : "voice_list_empty"
    };
  }

  function resolveJobVoice(job, settings, voices, cachedVoices, currentVoice) {
    const route = routeForSegment(job, settings);
    const requested = route.voice || normalizeVoiceId(job && job.voice);
    // An explicit character mapping is immutable even for a legacy persisted
    // job that predates voiceRoute.  Falling through to the current selected
    // Voice there would recreate the A -> B leakage during recovery.
    const locked = route.source === "character_tag"
      || Boolean(job && job.voiceRoute && job.voiceRoute.locked !== false)
      || Boolean(job && job.voiceLocked);
    const lockedFallback = locked && route.source !== "character_tag" ? normalizeVoiceId(route.fallbackVoice) : "";
    const current = locked ? lockedFallback : (normalizeVoiceId(currentVoice) || normalizeVoiceId(settings && settings.defaultVoice));
    const result = resolveVoice({
      requestedVoice: requested,
      currentVoice: current,
      defaultVoice: locked ? "" : normalizeVoiceId(settings && settings.defaultVoice),
      voices,
      cachedVoices,
      allowFallback: locked ? Boolean(lockedFallback) : true,
      allowFirstAvailable: !locked
    });
    return {
      ...result,
      requestedVoice: requested,
      tag: route.tag || normalizeTag(job && job.tag),
      voiceSource: route.source,
      mappedVoice: route.mappedVoice,
      fallbackVoice: route.fallbackVoice,
      route,
      locked
    };
  }

  function transientError(message, details = {}) {
    const error = new Error(message || "Voiceの準備が完了していません。");
    error.voiceResolution = true;
    error.voiceResolutionTransient = true;
    Object.assign(error, details);
    return error;
  }

  function unavailableError(message, details = {}) {
    const error = new Error(message || "利用可能なVoiceがありません。");
    error.voiceResolution = true;
    error.voiceResolutionPermanent = true;
    Object.assign(error, details);
    return error;
  }

  function isVoiceNotFoundError(error) {
    const message = String(error && error.message || error || "").toLowerCase();
    return /voice(?:\s|_|-)*(not found|missing|unknown)|unknown voice|voiceが見つかりません|指定したvoice/.test(message);
  }

  async function refresh(fetcher, cachedVoices, options = {}) {
    const attempts = Math.max(1, Number(options.attempts) || 3);
    const delayMs = Math.max(0, Number(options.delayMs) || 80);
    const cached = normalizeVoices(cachedVoices);
    let lastError = null;
    let observedEmpty = false;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        const voices = normalizeVoices(await fetcher());
        if (voices.length) return { voices, source: "api", fresh: true, attempts: attempt };
        observedEmpty = true;
        lastError = new Error("/voices が空のVoice一覧を返しました。");
      } catch (error) {
        lastError = error;
      }
      if (attempt < attempts && delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    if (observedEmpty) {
      let apiReady = true;
      if (typeof options.readyCheck === "function") {
        try { apiReady = Boolean(await options.readyCheck()); } catch (_) { apiReady = false; }
      }
      if (apiReady) throw unavailableError("APIに利用可能なVoiceがありません。", { cause: lastError, attempts });
      if (cached.length) return {
        voices: cached,
        source: "cache",
        fresh: false,
        stale: true,
        transient: true,
        observedEmpty,
        error: lastError,
        attempts
      };
      throw transientError("API起動中でVoice一覧がまだ利用できません。", { cause: lastError, attempts });
    }
    if (cached.length) return {
      voices: cached,
      source: "cache",
      fresh: false,
      stale: true,
      transient: true,
      observedEmpty,
      error: lastError,
      attempts
    };
    throw transientError("Voice一覧を取得できません。API接続が安定するまで待機します。", { cause: lastError, attempts });
  }

  return {
    normalizeVoiceId,
    normalizeTag,
    normalizeVoices,
    findVoice,
    resolveCharacterTag,
    routeForSegment,
    mappingVoiceForSegment,
    resolveVoice,
    resolveJobVoice,
    transientError,
    unavailableError,
    isVoiceNotFoundError,
    refresh
  };
});
