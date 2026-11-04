import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("privacy", ROOT / "scripts/privacy_scan.py")
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)

class DistributionTests(unittest.TestCase):
    def test_inventory(self):
        entries = json.loads((ROOT / "package-files.json").read_text(encoding="utf8"))["files"]
        self.assertEqual(privacy.inspect(entries), [])

    def test_private_paths_and_weights_are_rejected(self):
        for name in ["voice.wav", "models/model.gguf", "irodori-api/config/voices.json", "native-host/config.json", "logs/test.txt", "../outside.py", "assets/signed.xpi", ".git/config"]:
            self.assertFalse(privacy.allowed(name), name)

    def test_chrome_shared_code_syntax(self):
        for name in ["background.js", "text-processor.js", "voice-readiness.js", "sidebar.js", "options.js", "service-worker.js"]:
            result = subprocess.run(["node", "--check", str(ROOT / "chrome-extension" / name)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_text_processor_routing(self):
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "tests" / "test_text_processor.js")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))
        self.assertIn('"今日はいい天気" → kurisu', result.stdout)
        self.assertIn("ON/OFF matrix", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_custom_speaker_delimiters(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/test_speaker_delimiters.js")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_dialogue_speaker_scope(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/test_dialogue_scope.js")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_extension_ids_and_versions(self):
        chrome = json.loads((ROOT / "chrome-extension/manifest.json").read_text(encoding="utf8"))
        firefox = json.loads((ROOT / "firefox-extension/manifest.json").read_text(encoding="utf8"))
        distribution = json.loads((ROOT / "distribution.json").read_text(encoding="utf8"))
        self.assertEqual(chrome["version"], distribution["chromeExtensionVersion"])
        self.assertEqual(firefox["version"], distribution["firefoxExtensionVersion"])
        self.assertEqual(firefox["browser_specific_settings"]["gecko"]["id"], "irodori-voice-reader@local")
        for manifest in [chrome, firefox]:
            self.assertIn("muse-watcher.js", manifest["content_scripts"][0]["js"])
            self.assertIn("chatgpt-watcher.js", manifest["content_scripts"][0]["js"])

    def test_no_sm61_only_validation(self):
        builder = (ROOT / "scripts/build-backend.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("sm_61", builder)
        self.assertNotIn("reset --hard", builder)
        self.assertNotIn("clean -fdx", builder)
        self.assertIn('"compute_$CudaArch"', builder)

    def test_setup_defaults_are_non_destructive(self):
        setup = (ROOT / "scripts/setup.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("if($CheckOnly)", setup)
        self.assertLess(setup.index("if($CheckOnly)"), setup.index("Save-Json $statePath", setup.index("try{")))
        host = (ROOT / "scripts/register-hosts.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("Register-ScheduledTask", host)
        self.assertIn("HKCU:", host)
        self.assertIn(".venv", host)

    def test_model_is_pinned(self):
        model = json.loads((ROOT / "distribution.json").read_text(encoding="utf8"))["model"]
        self.assertRegex(model["url"], r"/resolve/[a-f0-9]{40}/")
        self.assertRegex(model["sha256"], r"^[a-f0-9]{64}$")

    def test_all_recorded_stable_patch_sources_are_packaged(self):
        provenance = json.loads((ROOT / "provenance.json").read_text(encoding="utf8"))["importedFiles"]
        names = {entry["path"] for entry in json.loads((ROOT / "package-files.json").read_text(encoding="utf8"))["files"]}
        for name, expected in provenance.items():
            if name.startswith("audio-cpp-stable-patch/added-files/"):
                self.assertIn(name, names)
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, name)

    def test_no_live_configuration(self):
        for name in privacy.LIVE_CONFIGS:
            self.assertFalse((ROOT / name).exists(), name)

    def test_shared_session_patch_is_idempotent_and_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            target = source / 'app/server/runtime.cpp'
            target.parent.mkdir(parents=True)
            target.write_text('void load() {\n        load_voice_presets(*loaded);\n        if (!loaded->config.lazy) {\n        }\n}', encoding='utf8')
            patch = ROOT / 'audio-cpp-patch/apply_shared_session_patch.py'
            for arguments in [[], [], ['--check']]:
                result = subprocess.run([sys.executable, str(patch), str(source), *arguments], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            text = target.read_text(encoding='utf8')
            self.assertEqual(text.count('ivr_shared_session_v1:'), 1)
            self.assertIn('existing.session_options == alias.session_options', text)
            target.write_text(text.replace('existing.path == alias.path', 'true'), encoding='utf8')
            result = subprocess.run([sys.executable, str(patch), str(source), '--check'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

if __name__ == "__main__":
    unittest.main()
