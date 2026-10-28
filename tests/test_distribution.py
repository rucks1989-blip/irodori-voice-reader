import hashlib
import importlib.util
import json
import re
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

    def test_extension_shared_code(self):
        for name in ["adapters.js", "muse-watcher.js", "chatgpt-watcher.js", "content.js", "background.js", "text-processor.js", "sidebar.js", "popup.js", "options.js"]:
            self.assertEqual((ROOT / "chrome-extension" / name).read_bytes(), (ROOT / "firefox-extension" / name).read_bytes(), name)

    def test_extension_ids_and_versions(self):
        chrome = json.loads((ROOT / "chrome-extension/manifest.json").read_text(encoding="utf8"))
        firefox = json.loads((ROOT / "firefox-extension/manifest.json").read_text(encoding="utf8"))
        self.assertEqual(chrome["version"], "1.2.23")
        self.assertEqual(firefox["version"], chrome["version"])
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
