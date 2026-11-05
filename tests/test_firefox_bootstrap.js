"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const root = path.join(__dirname, "../firefox-extension");

async function bootstrap(endpoint) {
  const calls = [];
  const context = vm.createContext({ URL, browser: { runtime: { sendNativeMessage: async (host, request) => {
    calls.push({host,request});
    if (request.command === "installation_info") return {ok:true,apiBase:endpoint};
    if (request.command === "status") return {ok:true,sharedApi:true,engines:{standard:{running:true,api:{running:true},backend:{running:true}}}};
    return {ok:true};
  } } } });
  vm.runInContext(fs.readFileSync(path.join(root,"install-config.js"),"utf8"),context);
  // The processor is loaded before the async handshake resolves, just as it
  // is in the signed background/options/sidebar. Getters must see the result.
  vm.runInContext(fs.readFileSync(path.join(root,"text-processor.js"),"utf8"),context);
  await context.IrodoriInstallReady;
  return {context,calls};
}

(async () => {
  const {context,calls} = await bootstrap("http://127.0.0.1:19876");
  assert.equal(context.IrodoriTextProcessor.apiUrlForEngine("standard"),"http://127.0.0.1:19876");
  assert.equal(context.IrodoriTextProcessor.mergedSettings({engine:"voice_stable"}).apiUrl,"http://127.0.0.1:19876");
  const reused = await context.IrodoriFirefoxNativeMessage("io.github.irodori.voice_reader.firefox",{command:"start_engine_async",engine:"standard",reason:"extension_initialize"});
  assert(reused.reusedSharedApi);
  assert(!calls.some(call=>call.request.command==="start_engine_async"),"Firefox startup must not restart an existing shared API or switch Chrome's intent");
  await assert.rejects(context.IrodoriFirefoxNativeMessage("another.host",{command:"status"}));
  for (const endpoint of ["https://example.com","http://127.0.0.1.evil.example:8765","http://user:secret@127.0.0.1:8765","http://127.0.0.1:8765/private","http://127.0.0.1:8765?token=x"]) {
    await assert.rejects(bootstrap(endpoint));
  }
  console.log("Firefox signed bootstrap: fixed Native Host, discovered local port, shared-API reuse and hostile endpoint rejection passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
