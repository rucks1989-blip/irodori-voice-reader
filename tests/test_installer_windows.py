"""Read-only preflight and isolated helper tests; never register hosts/install tools."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

def ps(script):
    code = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$ProgressPreference='SilentlyContinue';" + script
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", base64.b64encode(code.encode("utf-16le")).decode()], cwd=ROOT, capture_output=True, timeout=40)
    return result.returncode, result.stdout.decode("utf-8-sig", errors="replace"), result.stderr.decode("utf8", errors="replace")

@unittest.skipUnless(os.name == "nt", "Windows installer tests")
class InstallerWindowsTests(unittest.TestCase):
    def test_preflight_is_read_only(self):
        state = Path(os.environ["LOCALAPPDATA"]) / "IrodoriVoiceReader/Installer/state.json"
        before = state.read_bytes() if state.exists() else None
        code, out, err = ps("& '.\\scripts\\setup.ps1' -CheckOnly")
        self.assertEqual(code, 0, out + err)
        self.assertIn("診断のみ完了", out)
        self.assertEqual(state.read_bytes() if state.exists() else None, before)

    def test_gpu_and_python_probes_are_total(self):
        code, out, err = ps(". '.\\scripts\\common.ps1'; @{gpu=(Get-Gpu); python=([bool](Get-Python))} | ConvertTo-Json -Compress")
        self.assertEqual(code, 0, out + err)
        value = json.loads(out)
        self.assertIn("name", value["gpu"])
        self.assertIsInstance(value["python"], bool)
        if value["gpu"]["arch"]:
            self.assertRegex(value["gpu"]["arch"], r"^\d{2,3}$")

    def test_support_redacts_and_does_not_copy_automatically(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            state = {"step": "test", "error": "Unable to use " + os.environ.get("USERPROFILE", "") + "\\private.wav\nsecret=example-private-value", "completed": False}
            (Path(folder) / "state.json").write_text(json.dumps(state), encoding="utf8")
            escaped = folder.replace("'", "''")
            code, out, err = ps(f"& '.\\scripts\\support.ps1' -StateRoot '{escaped}' -NoOpen")
            self.assertEqual(code, 0, out + err)
            content = (Path(folder) / "AI-support.txt").read_text(encoding="utf8")
            self.assertNotIn("example-private-value", content)
            self.assertNotIn(os.environ["USERPROFILE"], content)
            self.assertIn("停止した工程: test", content)

    def test_command_failure_keeps_real_error(self):
        code, out, err = ps(". '.\\scripts\\common.ps1'; try { Run 'cmd.exe' @('/d','/c','echo fixture-error 1>&2 & exit /b 7') } catch { Write-Output $_.Exception.Message; exit 0 }; exit 1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("fixture-error", out)
        self.assertIn("7", out)

if __name__ == "__main__":
    unittest.main()
