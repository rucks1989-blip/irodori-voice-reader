// Signed packages cannot be edited per installation. Discover only the local
// API endpoint through a dedicated Firefox Native Host with the stable ID.
globalThis.IrodoriInstallConfig = Object.freeze({apiBase: "http://127.0.0.1:8765", nativeHost: "io.github.irodori.voice_reader.firefox"});
globalThis.IrodoriInstallReady = browser.runtime.sendNativeMessage(globalThis.IrodoriInstallConfig.nativeHost, {command:"installation_info"}).then((response) => {
  if (!response || response.ok !== true) throw new Error("Firefox用ローカルヘルパーをsetup.batで登録してください。");
  const url = new URL(response.apiBase);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("ローカルAPIの接続先を検証できません。");
  }
  globalThis.IrodoriInstallConfig = Object.freeze({apiBase:url.origin,nativeHost:"io.github.irodori.voice_reader.firefox"});
});
// Keep rejection handled until each page/background awaits it and displays a
// useful error. Never fall back silently to a different installed API.
globalThis.IrodoriInstallReady.catch(() => {});
globalThis.IrodoriFirefoxNativeMessage = async (host, request) => {
  await globalThis.IrodoriInstallReady;
  if (host !== globalThis.IrodoriInstallConfig.nativeHost) throw new Error("未登録のNative Hostです。");
  if (request.command === "start_engine_async" && request.reason === "extension_initialize") {
    const status = await browser.runtime.sendNativeMessage(host, {command:"status",engine:request.engine});
    const selected = status?.engines?.[request.engine];
    if (status.ok && status.sharedApi && selected?.running && selected.api?.running && selected.backend?.running) {
      return {...status,engine:request.engine,running:true,reusedSharedApi:true};
    }
  }
  return browser.runtime.sendNativeMessage(host, request);
};
