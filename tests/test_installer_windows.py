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
    environment = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", base64.b64encode(code.encode("utf-16le")).decode()], cwd=ROOT, capture_output=True, timeout=40, env=environment)
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

    def test_unattended_mode_requires_explicit_browser_without_opening_dialog(self):
        code, out, err = ps("& './scripts/setup.ps1' -Unattended -NoOpenGuide")
        self.assertNotEqual(code, 0)
        self.assertIn('-Browsers', out + err)

    def test_gpu_and_python_probes_are_total(self):
        code, out, err = ps(". '.\\scripts\\common.ps1'; @{gpu=(Get-Gpu); python=([bool](Get-Python))} | ConvertTo-Json -Compress")
        self.assertEqual(code, 0, out + err)
        value = json.loads(out)
        self.assertIn("name", value["gpu"])
        self.assertIsInstance(value["python"], bool)
        if value["gpu"]["arch"]:
            self.assertRegex(value["gpu"]["arch"], r"^\d{2,3}$")

    def test_location_selection_helpers_without_installing(self):
        code, out, err = ps(r"""
        . './scripts/common.ps1'
        $ast=[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path './scripts/setup.ps1'),[ref]$null,[ref]$null)
        foreach($name in @('Select-InstallBase','Get-InstallBaseDefault')){
            $fn=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
            Invoke-Expression $fn.Extent.Text
        }
        $script:choices=New-Object 'Collections.Generic.Queue[string]'
        $script:choices.Enqueue('D:\')
        $script:choices.Enqueue('D:\chosen')
        $script:calls=New-Object 'Collections.Generic.List[string]'
        function Show-SetupDialog($Kind,$Message,$Filter,$Default){
            $script:calls.Add($Kind)
            if($Kind -eq 'notice'){return 'ok'}
            return $script:choices.Dequeue()
        }
        function Get-FreeSpace($Path){if($Path -eq 'D:\IrodoriVoiceReader'){return 1GB};return 10GB}
        $Unattended=$false
        $selected=Select-InstallBase 'D:\default' -Prompt
        if($selected -ne 'D:\chosen'){throw 'Selection failed'}
        if(($script:calls -join ',') -ne 'directory,notice,directory'){throw 'Low-space retry failed'}
        $Unattended=$true
        if((Select-InstallBase 'D:\' -KeepRoot) -ne 'D:\'){throw 'Explicit root changed'}
        try{Select-InstallBase 'D:\IrodoriVoiceReader';throw 'Expected low-space rejection'}catch{
            if($_.Exception.Message -notmatch '5GB'){throw}
        }
        Write-Output 'location selection verified'
        """)
        self.assertEqual(code, 0, out + err)
        self.assertIn('location selection verified', out)

    def test_automatic_backend_prefers_detected_nvidia_gpu(self):
        code, out, err = ps(". './scripts/common.ps1'; @( (Get-BackendChoice 'auto' '86'), (Get-BackendChoice 'auto' ''), (Get-BackendChoice 'cpu' '86'), (Get-BackendChoice 'cuda' '86') ) | ConvertTo-Json -Compress")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out), ['cuda', 'cpu', 'cpu', 'cuda'])

    def test_isolated_identity_and_endpoint_configuration(self):
        import shutil, hashlib
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            shutil.copytree(ROOT / "chrome-extension", target / "chrome-extension")
            shutil.copytree(ROOT / "irodori-api/config", target / "irodori-api/config")
            escaped = folder.replace("'", "''")
            command = f"& '.\\scripts\\configure-chrome.ps1' -InstallRoot '{escaped}' -ApiPort 19876 -BackendPort 19877"
            code, out, err = ps(command)
            self.assertEqual(code, 0, out + err)
            identity = json.loads((target / "chrome-identity.json").read_text(encoding="utf8"))
            manifest = json.loads((target / "chrome-extension/manifest.json").read_text(encoding="utf8"))
            self.assertEqual(manifest["name"], "Irodori Voice Reader")
            self.assertNotIn("独立環境", manifest["name"])
            digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
            self.assertEqual(identity["extensionId"], "".join(chr(97 + int(c, 16)) for c in digest))
            self.assertNotEqual(identity["extensionId"], "aeaichhflmacmifblidbpojgfemjijlb")
            self.assertIn(identity["hostName"], (target / "chrome-extension/install-config.js").read_text(encoding="utf8"))
            self.assertIn("127.0.0.1:19876", (target / "chrome-extension/install-config.js").read_text(encoding="utf8"))
            self.assertEqual(json.loads((target / "irodori-api/config/api.json").read_text(encoding="utf8"))["port"], 19876)
            for name in ["config", "config.voice_stable"]:
                config = json.loads((target / f"irodori-api/config/{name}.json").read_text(encoding="utf8"))
                self.assertEqual(config["irodori_audiocpp_api_url"], "http://127.0.0.1:19877/v1/audio/speech")
            code, out, err = ps(command)
            self.assertEqual(code, 0, out + err)
            self.assertEqual(identity, json.loads((target / "chrome-identity.json").read_text(encoding="utf8")))

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

    def test_job_cleanup_preserves_unrelated_process(self):
        code, out, err = ps("""
        . './scripts/common.ps1'
        Add-Type -Path './scripts/setup-job.cs'
        $script:SetupJob=New-Object IvrSetupJob
        $foreign=Start-Process powershell.exe -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 30' -WindowStyle Hidden -PassThru
        [void]$foreign.Handle
        try {
            $own=Add-SetupProcess (Start-Process powershell.exe -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 30' -WindowStyle Hidden -PassThru)
            $script:SetupJob.Dispose();$script:SetupJob=$null
            if(-not $own.WaitForExit(5000)){throw 'Owned child survived'}
            if($foreign.HasExited){throw 'Unrelated child stopped'}
            Write-Output 'ownership verified'
        } finally {
            if(-not $foreign.HasExited){$foreign.Kill();$foreign.WaitForExit()}
            if($script:SetupJob){$script:SetupJob.Dispose()}
        }
        """)
        self.assertEqual(code, 0, out + err)
        self.assertIn("ownership verified", out)

if __name__ == "__main__":
    unittest.main()
