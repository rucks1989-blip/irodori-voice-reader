/* Browser-neutral API state formatting.  Network/process probing stays in
 * each browser background, while both UIs use the same state vocabulary. */
(function (root) {
  "use strict";

  const LABELS = Object.freeze({ standard: "通常版", voice_stable: "声質安定版" });
  const ENDPOINTS = Object.freeze({
    standard: root.IrodoriInstallConfig?.apiBase || "http://127.0.0.1:8765",
    voice_stable: root.IrodoriInstallConfig?.apiBase || "http://127.0.0.1:8765"
  });

  function label(engine) {
    return LABELS[engine] || String(engine || "生成エンジン");
  }

  function endpoint(engine) {
    return ENDPOINTS[engine] || ENDPOINTS.standard;
  }

  function ready(value) {
    return Boolean(value && (value.running ?? value.available ?? value.ok));
  }

  function normalize(engine, value) {
    const source = value && typeof value === "object" ? value : {};
    const api = source.api || (Object.prototype.hasOwnProperty.call(source, "available")
      ? { running: source.available, status: source.status, error: source.error }
      : null);
    const backend = source.backend || null;
    // HTTP /health is the shared service truth.  A backend-only process is
    // still "starting", while an API that answers is connected even when a
    // separate backend probe is temporarily unavailable.
    const apiReady = ready(api);
    const backendReady = backend ? ready(backend) : null;
    const connected = apiReady;
    const fullyReady = backendReady === null ? apiReady : apiReady && backendReady;
    const starting = Boolean(source.starting || source.engineTransition || source.transition ||
      (!apiReady && backendReady === true));
    // A refused localhost connection is the normal observation for a stopped
    // engine. Keep the transport diagnostic, but do not present it as a model
    // startup failure; the UI can still expose error/fetchUrl in its tooltip.
    const networkStopped = source.errorCode === "network_error";
    const state = (source.error && !networkStopped) || source.startupError
      ? "error"
      : connected
        ? "connected"
        : starting ? "starting" : "stopped";
    return {
      engine,
      label: source.label || label(engine),
      endpoint: source.endpoint || endpoint(engine),
      state,
      connected,
      fullyReady,
      starting,
      api: api || { running: false, status: 0, error: source.error || null },
      backend,
      apiReady,
      backendReady,
      error: source.error || source.startupError || null,
      errorCode: source.errorCode || null,
      errorStage: source.errorStage || null,
      fetchUrl: source.fetchUrl || null,
      currentEngine: source.currentEngine || null,
      startupPid: source.startupPid || null,
      startupState: source.startupState || null,
      startupActive: Boolean(source.startupActive)
    };
  }

  function text(value) {
    const state = typeof value === "string" ? value : value?.state;
    return ({ connected: "接続済み", starting: "起動中", stopped: "停止中", error: "起動失敗" })[state] || "確認中";
  }

  function display(engine, value) {
    const normalized = normalize(engine, value);
    return `${normalized.label} ${text(normalized)}`;
  }

  const api = { LABELS, ENDPOINTS, label, endpoint, normalize, text, display };
  root.IrodoriApiState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
