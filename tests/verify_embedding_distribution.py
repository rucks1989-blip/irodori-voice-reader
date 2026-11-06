"""Opt-in full ZIP/Firefox test. Requires explicit permission to switch the
current user's Firefox host temporarily. Its original default is restored even
on test failure. No existing application data is replaced.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import winreg
import zipfile

import requests

from firefox_marionette import Marionette


KEY = r"Software\Mozilla\NativeMessagingHosts\io.github.irodori.voice_reader.firefox"
HERE = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(function, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = function()
            if last:
                return last
        except (requests.RequestException, ConnectionError):
            pass
        time.sleep(.3)
    raise TimeoutError(f"Test condition was not reached: {last}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--existing-api", type=Path, required=True)
    parser.add_argument("--expected-firefox-host", required=True)
    parser.add_argument("--embedding-a", type=Path, required=True)
    parser.add_argument("--embedding-b", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--firefox", type=Path, required=True)
    parser.add_argument("--allow-temporary-host-switch", action="store_true", required=True)
    args = parser.parse_args()
    if args.test_root.exists():
        raise FileExistsError("Use a new test folder")
    args.test_root.mkdir(parents=True)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        original_value, original_type = winreg.QueryValueEx(key, "")
        original_values = [winreg.EnumValue(key, i) for i in range(winreg.QueryInfoKey(key)[1])]
    if original_value != args.expected_firefox_host:
        raise RuntimeError("Firefox registration differs from the explicitly authorized original")
    write(args.test_root / "original-host.json", {"value": original_value, "type": original_type})
    original_files = list((args.existing_api / "config").glob("*.json"))
    original_files += [p for p in args.embedding_a.parent.iterdir() if p.is_file()]
    original_files += [p for p in (args.existing_api / "IrodoriVoiceReader/voices").rglob("*") if p.is_file()]
    baseline = {str(p): sha(p) for p in original_files}
    write(args.test_root / "original-hashes.json", baseline)
    unpacked = args.test_root / "unpacked"
    with zipfile.ZipFile(args.zip) as archive:
        # The trusted locally produced distribution is still checked for path
        # escape before extraction into the new test folder.
        for name in archive.namelist():
            if not (unpacked / name).resolve().is_relative_to(unpacked.resolve()):
                raise ValueError("Unsafe distribution ZIP member")
        archive.extractall(unpacked)
    source = unpacked / "IrodoriVoiceReader"
    install_test = args.test_root / "setup"
    installed = None; new_manifest = None; base = None; identity = None
    firefox = None; marionette = None; browser_log = None
    results = []
    try:
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(HERE / "verify_embedding_zip_setup.ps1"), "-SourceRoot", str(source), "-TestRoot", str(install_test),
                   "-ExistingApiRoot", str(args.existing_api), "-ReferenceWav", str(args.wav), "-ExpectedFirefoxHost", original_value]
        subprocess.run(command, check=True, timeout=1500)
        session = json.loads((install_test / "installed.json").read_text(encoding="utf-8-sig"))
        installed = Path(session["installRoot"])
        new_manifest = str(installed / "firefox-host/io.github.irodori.voice_reader.firefox.json")
        api = installed / "irodori-api"
        base = f"http://127.0.0.1:{session['apiPort']}"
        identity = json.loads((installed / "chrome-identity.json").read_text(encoding="utf-8-sig"))
        settings = api / "config/voice_sources.json"
        with settings.open("x", encoding="utf8") as stream:
            json.dump({"speaker_embedding_dirs": [str(args.embedding_a.parent.resolve())]}, stream)
        distribution = json.loads((source / "distribution.json").read_text(encoding="utf8"))
        spec = next(a for a in distribution["assets"] if a.get("role") == "signed-install")
        xpi = installed / spec["name"]
        assert sha(xpi) == spec["sha256"]
        profile = args.test_root / "firefox-profile"; profile.mkdir()
        debugging_port = free_port()
        prefs = {"marionette.port": debugging_port, "browser.shell.checkDefaultBrowser": False,
                 "browser.aboutwelcome.enabled": False, "browser.startup.homepage_override.mstone": "ignore"}
        (profile / "user.js").write_text("\n".join("user_pref(" + json.dumps(k) + "," + json.dumps(v) + ");" for k, v in prefs.items()), encoding="utf8")
        browser_log = (args.test_root / "firefox.log").open("wb")
        firefox = subprocess.Popen([str(args.firefox), "-no-remote", "-profile", str(profile), "-marionette", "about:blank"],
                                   env=dict(os.environ, MOZ_REMOTE_ALLOW_SYSTEM_ACCESS="1"), stdout=browser_log, stderr=browser_log)
        marionette = Marionette(debugging_port, firefox)
        browser_session = marionette.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
        marionette.command("WebDriver:SetTimeouts", {"script": 600000})
        marionette.command("Marionette:SetContext", {"value": "chrome"})
        addon = marionette.command("Addon:Install", {"path": str(xpi), "temporary": False})
        signed = marionette.script("""
          const done=arguments[arguments.length-1];
          ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs').AddonManager.getAddonByID('irodori-voice-reader@local').then(
            a=>done({id:a.id,version:a.version,isActive:a.isActive,signedState:a.signedState}));
        """, asynchronous=True)
        assert signed["isActive"] and signed["signedState"] >= 1 and signed["version"] == spec["version"]
        extension_url = wait_for(lambda: marionette.script('const p=WebExtensionPolicy.getByID("irodori-voice-reader@local");return p ? "moz-extension://"+p.mozExtensionHostname+"/sidebar.html" : null;'), 30)
        marionette.command("Marionette:SetContext", {"value": "content"})
        marionette.command("WebDriver:Navigate", {"url": extension_url})
        wait_for(lambda: marionette.script('return !!document.getElementById("apiStart");'), 30)
        start = marionette.command("WebDriver:FindElement", {"using": "css selector", "value": "#apiStart"})
        element = start.get("value", start)
        element_id = element.get("element-6066-11e4-a52e-4f735466cecf") or element.get("ELEMENT")
        marionette.command("WebDriver:ElementClick", {"id": element_id})
        wait_for(lambda: marionette.message({"type": "status"}).get("engineReady"), 300)
        info = marionette.script("""
          const done=arguments[arguments.length-1];
          browser.runtime.sendNativeMessage('io.github.irodori.voice_reader.firefox',{command:'installation_info'}).then(done,e=>done({error:String(e)}));
        """, asynchronous=True)
        assert info["apiBase"] == base
        for voice_id, file, kind in (("embedding-a", args.embedding_a, "speaker_embedding"),
                                     ("embedding-b", args.embedding_b, "speaker_embedding"), ("wav-test", args.wav, "reference_wav")):
            imported = marionette.message({"type": "native-control", "command": "voice_import", "id": voice_id,
                        "name": voice_id, "voiceType": kind, "fileName": file.name,
                        "dataBase64": base64.b64encode(file.read_bytes()).decode("ascii")})
            assert imported.get("ok"), imported
        marionette.message({"type": "voices"})
        for index, voice_id in enumerate(("embedding-a", "embedding-b", "wav-test", "embedding-a")):
            current = marionette.message({"type": "get-settings"})
            marionette.message({"type": "save-settings", "base": current,
                                "patch": {"defaultVoice": voice_id, "autoPlay": True, "engine": "standard"}})
            marionette.message({"type": "voices"})
            message_id = f"embedding-zip-verification-{index}"
            reply = marionette.message({"type": "enqueue", "text": "こんにちは。音声の動作確認です。", "source": "manual",
                         "answer": {"messageId": message_id, "conversationId": "isolated-verification"}})
            def complete():
                status = marionette.message({"type": "status"})
                if status.get("lastError"):
                    raise RuntimeError(status["lastError"])
                completed = [a for a in status.get("answerStates", []) if a.get("state") == "completed"]
                return status if len(completed) >= index + 1 and status.get("queued") == 0 else None
            status = wait_for(complete, 300)
            assert status["engine"] == "standard" and status["selectedVoice"] == voice_id
            results.append({"voice": voice_id, "enqueue": reply, "status": status})
        assert sha(xpi) == spec["sha256"]
        write(args.test_root / "browser-results.json", {"session": browser_session, "addon": addon, "signature": signed, "nativeHost": info,
              "signedXpiSha256": sha(xpi), "playback": results, "zipSha256": sha(args.zip), "installation": session})
        print(json.dumps({"installedRoot": str(installed), "firefoxPlaybacks": len(results), "zipSha256": sha(args.zip)}))
    finally:
        if marionette:
            try:
                marionette.message({"type": "clear"})
                marionette.command("WebDriver:DeleteSession")
            except Exception:
                pass
            marionette.close()
        if firefox and firefox.poll() is None:
            firefox.terminate(); firefox.wait(timeout=20)
        if browser_log:
            browser_log.close()
        # Stop only the isolated API, and only after validating both endpoint
        # configurations target its dedicated backend port.
        if installed and base and identity:
            own_api = installed / "irodori-api"
            expected = f"http://127.0.0.1:{session['backendPort']}/v1/audio/speech"
            safe = all(json.loads((own_api / "config" / name).read_text(encoding="utf-8-sig"))["irodori_audiocpp_api_url"] == expected
                       for name in ("config.json", "config.voice_stable.json"))
            if safe:
                try:
                    requests.post(base + "/shutdown", json={}, headers={"Origin": "chrome-extension://" + identity["extensionId"]}, timeout=30)
                except requests.RequestException:
                    pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            current, _ = winreg.QueryValueEx(key, "")
            if new_manifest is None:
                saved = install_test / "session/firefox-session.json"
                if saved.is_file():
                    record = json.loads(saved.read_text(encoding="utf-8-sig"))
                    new_manifest = str(Path(record["installRoot"]) / "firefox-host/io.github.irodori.voice_reader.firefox.json")
            if current == new_manifest:
                winreg.SetValueEx(key, "", 0, original_type, original_value)
            elif current != original_value:
                raise RuntimeError("Another program changed the Firefox host; refusing to overwrite its registration")
            assert winreg.QueryValueEx(key, "") == (original_value, original_type)
            assert [winreg.EnumValue(key, i) for i in range(winreg.QueryInfoKey(key)[1])] == original_values
        for path, expected_hash in baseline.items():
            assert sha(path) == expected_hash, f"Original data changed: {path}"
        write(args.test_root / "restored.json", {"firefoxRegistrationRestored": True, "originalFilesUnchanged": True})


if __name__ == "__main__":
    main()
