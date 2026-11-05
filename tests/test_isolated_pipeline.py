"""Code-level integration checks. These do not count as browser/device validation."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import urllib.request
import urllib.error
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_installer_windows import ROOT, ps


def quote(path):
    return "'" + str(path).replace("'", "''") + "'"


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.name == "nt", "Windows integration")
class IsolatedPipelineTests(unittest.TestCase):
    def test_inventory_failure_and_foreign_destination_are_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "source"
            source.mkdir()
            (source / "README.md").write_text("valid", encoding="utf8")
            inventory = {"files": [{"path": "README.md", "sha256": "0" * 64}]}
            (source / "package-files.json").write_text(json.dumps(inventory), encoding="utf8")
            destination = base / "new"
            command = f". './scripts/common.ps1'; . './scripts/install-files.ps1'; Copy-Application -SourceRoot {quote(source)} -InstallRoot {quote(destination)} -Version 'fixture'"
            code, out, err = ps(command)
            self.assertNotEqual(code, 0, out + err)
            self.assertFalse(destination.exists())
            inventory["files"][0]["sha256"] = hashlib.sha256(b"valid").hexdigest()
            (source / "package-files.json").write_text(json.dumps(inventory), encoding="utf8")
            destination.mkdir()
            voice = destination / "my-voice.wav"
            voice.write_bytes(b"preserve")
            before = {p.name: p.read_bytes() for p in destination.iterdir()}
            code, out, err = ps(command)
            self.assertNotEqual(code, 0, out + err)
            self.assertEqual(before, {p.name: p.read_bytes() for p in destination.iterdir()})

    def test_copy_configure_resume_and_chrome_contexts(self):
        with tempfile.TemporaryDirectory(prefix="ivr space ") as folder:
            target = Path(folder) / "installed"
            command = f". './scripts/common.ps1'; . './scripts/install-files.ps1'; Copy-Application -SourceRoot {quote(ROOT)} -InstallRoot {quote(target)} -Version 'fixture'; & './scripts/configure-chrome.ps1' -InstallRoot {quote(target)} -ApiPort 19876 -BackendPort 19877"
            code, out, err = ps(command)
            self.assertEqual(code, 0, out + err)
            identity = (target / "chrome-identity.json").read_bytes()
            config = target / "irodori-api/config/config.json"
            value = json.loads(config.read_text(encoding="utf8"))
            value["irodori_audiocpp_seed"] = 5678
            config.write_text(json.dumps(value), encoding="utf8")
            voices = target / "irodori-api/config/voices.json"
            voices.write_text('{"voices": []}', encoding="utf8")
            code, out, err = ps(command)
            self.assertEqual(code, 0, out + err)
            self.assertEqual(identity, (target / "chrome-identity.json").read_bytes())
            self.assertEqual(json.loads(config.read_text(encoding="utf8"))["irodori_audiocpp_seed"], 5678)
            self.assertEqual(voices.read_text(encoding="utf8"), '{"voices": []}')
            self.assertTrue((target / '声質安定版追加する.bat').is_file())
            self.assertTrue((target / 'installer-source/scripts/setup.ps1').is_file())
            # Installed extensions are configured, while repair source stays
            # hash-verifiable. A local repair must not overwrite those files.
            extension = target / 'chrome-extension/manifest.json'
            before_extension = extension.read_bytes()
            code, out, err = ps(f". './scripts/common.ps1'; . './scripts/install-files.ps1'; Copy-Application -SourceRoot {quote(target / 'installer-source')} -InstallRoot {quote(target)} -Version 'fixture'")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(before_extension, extension.read_bytes())
            self.assertEqual(voices.read_text(encoding='utf8'), '{"voices": []}')
            code, out, err = ps(f". './scripts/common.ps1'; . './scripts/install-files.ps1'; Copy-MaintenanceFiles -SourceRoot {quote(ROOT)} -InstallRoot {quote(target)}")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(before_extension, extension.read_bytes())
            self.assertEqual(json.loads(config.read_text(encoding='utf8'))['irodori_audiocpp_seed'], 5678)
            js = """
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const dir=process.argv[1],c={};vm.createContext(c);
for(const name of ['install-config.js','text-processor.js','api-state.js']) vm.runInContext(fs.readFileSync(dir+'/'+name,'utf8'),c);
assert.equal(c.IrodoriTextProcessor.apiUrlForEngine('standard'),'http://127.0.0.1:19876');
assert.equal(c.IrodoriTextProcessor.apiUrlForEngine('voice_stable'),'http://127.0.0.1:19876');
assert.equal(c.IrodoriApiState.endpoint('voice_stable'),'http://127.0.0.1:19876');
assert.equal(c.IrodoriTextProcessor.mergedSettings({apiUrl:'http://127.0.0.1:8765'}).apiUrl,'http://127.0.0.1:19876');
for(const name of ['offscreen.html','sidebar.html','options.html','popup.html']) {
 const html=fs.readFileSync(dir+'/'+name,'utf8');
 assert(html.indexOf('install-config.js')>=0,name);
 assert(html.indexOf('install-config.js')<html.indexOf('text-processor.js'),name);
}
"""
            result = subprocess.run(["node", "-e", js, str(target / "chrome-extension")], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_protocol_and_worker_paths_are_instance_local(self):
        with tempfile.TemporaryDirectory(prefix="ivr host space ") as folder:
            root = Path(folder)
            host = root / "host"
            host.mkdir()
            api = root / "api"
            api.mkdir()
            for name in ["irodori_native_host.py", "chrome_native_host.py"]:
                shutil.copyfile(ROOT / "native-host" / name, host / name)
            config = {"isolated_environment": True, "irodori_root": str(api), "health_url": "http://127.0.0.1:19876/health", "backend_health_url": "http://127.0.0.1:19877/health", "chrome_extension_id": "a" * 32, "firefox_extension_id": "irodori-fixture@isolated.local"}
            (host / "config.json").write_text(json.dumps(config), encoding="utf8")
            core = module_at("isolated_fixture", host / "irodori_native_host.py")
            loaded = core.load_config()
            self.assertEqual(core._native_log_dir(), host / "logs")
            self.assertEqual(core._timing_log_dir(), host / "timing_logs")
            for engine in core.ENGINE_DEFINITIONS.values():
                self.assertEqual(engine["api_health_url"], config["health_url"])
                self.assertEqual(engine["backend_health_url"], config["backend_health_url"])
            message = json.dumps({"command": "voice_state"}).encode()
            result = subprocess.run([sys.executable, str(host / "chrome_native_host.py"), "chrome-extension://" + "a" * 32 + "/"], input=struct.pack("<I", len(message)) + message, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            size, = struct.unpack("<I", result.stdout[:4])
            response = json.loads(result.stdout[4:4 + size])
            self.assertTrue(response["ok"], response)
            rejected = subprocess.run([sys.executable, str(host / "chrome_native_host.py"), "chrome-extension://" + "b" * 32 + "/"], input=struct.pack("<I", len(message)) + message, capture_output=True, timeout=15)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(json.loads(rejected.stdout[4:])["ok"])
            # Exercise the real detached PowerShell launcher with a space-containing path.
            script = api / "start_kurisu_voice_api.ps1"
            script.write_text("[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'started.txt'),'ok')", encoding="utf-8-sig")
            core._launch_script_async(loaded, script.name)
            deadline = time.monotonic() + 10
            while not (api / "started.txt").exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual((api / "started.txt").read_text(encoding="utf8"), "ok")
            core._async_state_path("standard").unlink(missing_ok=True)
            script.write_text("[Console]::Error.WriteLine('fixture-start-failure'); exit 7", encoding="utf-8-sig")
            with self.assertRaisesRegex(RuntimeError, "fixture-start-failure"):
                core._launch_script_async(loaded, script.name)

    def test_browser_registration_plans_and_worker_host_match(self):
        with tempfile.TemporaryDirectory(prefix="ivr plan space ") as folder:
            target = Path(folder)
            shutil.copytree(ROOT / "chrome-extension", target / "chrome-extension")
            shutil.copytree(ROOT / "firefox-extension", target / "firefox-extension")
            shutil.copytree(ROOT / "irodori-api/config", target / "irodori-api/config")
            python = target / "irodori-api/.venv/Scripts/python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"plan only, not executable")
            original_firefox_config = (target / "firefox-extension/install-config.js").read_bytes()
            original_firefox_manifest = (target / "firefox-extension/manifest.json").read_bytes()
            code, out, err = ps(f"& './scripts/configure-chrome.ps1' -InstallRoot {quote(target)} -ApiPort 19876 -BackendPort 19877")
            self.assertEqual(code, 0, out + err)
            identity = json.loads((target / "chrome-identity.json").read_text(encoding="utf8"))
            firefox_manifest = json.loads((target / "firefox-extension/manifest.json").read_text(encoding="utf8"))
            self.assertEqual(firefox_manifest["browser_specific_settings"]["gecko"]["id"], "irodori-voice-reader@local")
            self.assertEqual((target / "firefox-extension/install-config.js").read_bytes(), original_firefox_config)
            self.assertEqual((target / "firefox-extension/manifest.json").read_bytes(), original_firefox_manifest)
            for browser, expected_count in [("chrome", 1), ("firefox", 1), ("both", 2)]:
                identity_path = target / "chrome-identity.json"
                identity["apiPort"] = 19876
                identity["backendPort"] = 19877
                identity_path.write_text(json.dumps(identity), encoding="utf8")
                code, out, err = ps(f"& './scripts/register-hosts.ps1' -InstallRoot {quote(target)} -Browsers {browser} -PlanOnly")
                self.assertEqual(code, 0, out + err)
                specs = json.loads(out)
                self.assertEqual(len(specs), expected_count)
                for spec in specs:
                    self.assertTrue(spec["config"]["isolated_environment"])
                    if spec["folder"] == "firefox-host":
                        self.assertNotEqual(spec["host"], "io.github.irodori.voice_reader")
                        self.assertEqual(spec["host"], "io.github.irodori.voice_reader.firefox")
                        self.assertEqual(spec["manifest"]["allowed_extensions"], ["irodori-voice-reader@local"])
                    self.assertEqual(spec["config"]["health_url"], f"http://127.0.0.1:{identity['apiPort']}/health")
                    self.assertEqual(spec["config"]["backend_health_url"], f"http://127.0.0.1:{identity['backendPort']}/health")
                    self.assertTrue(Path(spec["manifest"]["path"]).is_relative_to(target))
                    if "Google" in spec["registry"]:
                        self.assertNotIn("Mozilla", spec["registry"])
                        self.assertEqual(spec["manifest"]["name"], identity["hostName"])
                        self.assertEqual(spec["manifest"]["allowed_origins"], ["chrome-extension://" + identity["extensionId"] + "/"])
                    else:
                        self.assertNotEqual(browser, "chrome")
                        self.assertEqual(spec["manifest"]["allowed_extensions"], ["irodori-voice-reader@local"])
            self.assertFalse((target / "chrome-host").exists())
            self.assertFalse((target / "firefox-host").exists())
            js = """
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const dir=process.argv[1],sent=[],event={addListener(){}};
const c={console,chrome:{runtime:{getURL:x=>x,getContexts:async()=>[{}],sendNativeMessage:async(host,request)=>{sent.push(host);return {ok:true};},onMessage:event,onInstalled:event,onStartup:event},storage:{onChanged:event},contextMenus:{onClicked:event},commands:{onCommand:event},action:{onClicked:event}}};
vm.createContext(c);c.importScripts=name=>vm.runInContext(fs.readFileSync(dir+'/'+name,'utf8'),c);
vm.runInContext(fs.readFileSync(dir+'/service-worker.js','utf8'),c);
vm.runInContext("invoke('runtime.sendNativeMessage',['io.github.irodori.voice_reader',{command:'status'}])",c).then(()=>{
 assert.deepEqual(sent,[c.IrodoriInstallConfig.nativeHost]);
}).catch(error=>{console.error(error);process.exitCode=1});
"""
            result = subprocess.run(["node", "-e", js, str(target / "chrome-extension")], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_recorded_pid_cannot_stop_unrelated_process(self):
        core = module_at("pid_fixture", ROOT / "native-host/irodori_native_host.py")
        process = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 30"], creationflags=0x08000000)
        try:
            self.assertFalse(core._terminate_recorded_process(process.pid, "standard", "script", {"root": Path(tempfile.gettempdir()) / "different-installation"}))
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_process_sweep_stops_only_exact_installation(self):
        core = module_at("sweep_fixture", ROOT / "native-host/irodori_native_host.py")
        with tempfile.TemporaryDirectory(prefix="ivr process ") as folder:
            base = Path(folder)
            processes = []
            try:
                for name in ["owned", "foreign"]:
                    root = base / name
                    root.mkdir()
                    script = root / "kurisu_voice_api.py"
                    script.write_text("import time;time.sleep(30)", encoding="utf8")
                    processes.append(subprocess.Popen([sys.executable, str(script)], creationflags=0x08000000))
                core._stop_owned_engine_processes({"root": base / "owned"}, "standard")
                processes[0].wait(timeout=10)
                self.assertIsNone(processes[1].poll())
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=10)

    def test_real_api_routes_both_modes_to_own_backend_despite_inherited_env(self):
        requests_seen = []
        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                data = b'{"status":"ok"}'
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
            def do_POST(self):
                if self.path == "/shutdown":
                    self.rfile.read(int(self.headers["Content-Length"]))
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"status":"stopping"}')
                    def stop():
                        self.server.shutdown()
                        self.server.server_close()
                    threading.Thread(target=stop, daemon=True).start()
                    return
                requests_seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                output = io.BytesIO()
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
                    wav.writeframes(b"\0\0" * 2400)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(output.getvalue())
        backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="ivr API space ") as folder:
                target = Path(folder)
                shutil.copytree(ROOT / "irodori-api", target / "irodori-api", ignore=shutil.ignore_patterns("__pycache__"))
                api = target / "irodori-api"
                (target / ".ivr-install.json").write_text('{}')
                (target / "chrome-identity.json").write_text(json.dumps({"extensionId": "a"*32}))
                for name in ["config", "config.voice_stable"]:
                    value = json.loads((api / f"config/{name}.example.json").read_text(encoding="utf8"))
                    value["irodori_audiocpp_api_url"] = f"http://127.0.0.1:{backend.server_port}/v1/audio/speech"
                    (api / f"config/{name}.json").write_text(json.dumps(value), encoding="utf8")
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    port = reservation.getsockname()[1]
                (api / "config/api.json").write_text(json.dumps({"port": port}))
                environment = dict(os.environ, PYTHONIOENCODING="ascii:strict", IRODORI_API_PORT="1", IRODORI_AUDIO_CPP_API_URL="http://127.0.0.1:1/forbidden", IRODORI_VOICES_CONFIG=str(target / "must-not-exist.json"), IRODORI_API_PID=str(target / "must-not-write.pid"))
                with (target / "api.log").open("wb") as log:
                    process = subprocess.Popen([sys.executable, str(api / "kurisu_voice_api.py")], cwd=api, env=environment, stdout=log, stderr=log, creationflags=0x08000000)
                    url = f"http://127.0.0.1:{port}"
                    try:
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            try:
                                health = json.load(urllib.request.urlopen(url + "/health", timeout=1))
                                if health["status"] == "ok":
                                    break
                            except (OSError, urllib.error.URLError):
                                if process.poll() is not None:
                                    break
                                time.sleep(.05)
                        else:
                            self.fail("API startup timeout")
                        self.assertIsNone(process.poll(), (target / "api.log").read_text(encoding="utf8"))
                        for engine in ["standard", "voice_stable"]:
                            health = json.load(urllib.request.urlopen(url + "/health?engine=" + engine))
                            self.assertEqual(health["port"], port)
                            data = json.dumps({"text": "test", "engine": engine}).encode()
                            try:
                                wav = urllib.request.urlopen(urllib.request.Request(url + "/tts", data=data, headers={"Content-Type": "application/json", "Origin": "chrome-extension://"+"a"*32}), timeout=5).read()
                            except urllib.error.HTTPError as error:
                                self.fail(error.read().decode("utf8", errors="replace"))
                            self.assertEqual(wav[:4], b"RIFF")
                            self.assertEqual(wav[8:12], b"WAVE")
                        (api / "config/capabilities.json").write_text(json.dumps({"standard": True, "voice_stable": False}))
                        before = len(requests_seen)
                        rejected = urllib.request.Request(url + "/tts", data=json.dumps({"text": "test", "engine": "voice_stable"}).encode(), headers={"Content-Type": "application/json", "Origin": "chrome-extension://"+"a"*32})
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            urllib.request.urlopen(rejected, timeout=5)
                        self.assertIn("声質安定版はインストールされていない", caught.exception.read().decode("utf8"))
                        self.assertEqual(len(requests_seen), before)
                        self.assertTrue((api / "logs/kurisu_voice_api/server.pid").exists())
                        reply = json.load(urllib.request.urlopen(urllib.request.Request(url + "/shutdown", data=b"{}", headers={"Content-Type": "application/json", "Origin": "chrome-extension://"+"a"*32}), timeout=10))
                        self.assertEqual(reply["status"], "stopping")
                        self.assertEqual(process.wait(timeout=10), 0)
                        self.assertFalse((api / "logs/kurisu_voice_api/server.pid").exists())
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=10)
                self.assertEqual([r["model"] for r in requests_seen], ["irodori-500m-v3-q8", "irodori-500m-v3-q8-voice-stable-speaker"])
                self.assertFalse((target / "must-not-write.pid").exists())
        finally:
            backend.shutdown()
            backend.server_close()
