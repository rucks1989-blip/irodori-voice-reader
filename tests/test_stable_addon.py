"""Isolated stable addition tests: no real engines, GPUs, voices or toolchains."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_installer_windows import ps

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stable_addon", ROOT / "scripts/stable-addon.py")
addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon)


class StableRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidate = self.root / "installer-state/candidate"
        self.candidate.mkdir(parents=True)
        (self.candidate / "audiocpp_server.exe").write_bytes(b"verified mock runtime")
        api = self.root / "irodori-api"
        self.model = api / "models/existing.gguf"
        self.model.parent.mkdir(parents=True)
        self.model.write_bytes(b"existing model")
        values = {
            addon.FILES[0]: {"audio_cpp_shared_executable": "old.exe", "custom": 42},
            addon.FILES[1]: {"backend": "cuda", "port": 19091, "models": [
                {"id": "normal", "path": str(self.model), "family": "irodori_tts",
                 "session_options": {"irodori_tts.mem_saver": "true"}}]},
            addon.FILES[2]: {"standard": True, "voice_stable": False, "extra": 7},
            addon.FILES[3]: {"state": "runtime-ready", "version": "kept"},
            "irodori-api/config/config.json": {"irodori_audiocpp_model": "normal", "irodori_audiocpp_api_url": "http://127.0.0.1:19091/v1/audio/speech"},
            "irodori-api/config/config.voice_stable.json": {"irodori_audiocpp_model": "stable", "irodori_audiocpp_api_url": "http://127.0.0.1:19091/v1/audio/speech", "irodori_audiocpp_request_options": {"speaker_rescue_enabled": True}},
            "irodori-api/config/voices.json": {"voices": [{"id": "existing", "reference_wav": "unchanged.wav"}]},
        }
        for name, value in values.items():
            addon.write(self.root / name, addon.payload(value))
        self.before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_addition_shares_model_and_preserves_all_other_files_and_options(self):
        addon.apply(self.root, self.candidate)
        server = addon.read(self.root / addon.FILES[1])
        normal, stable = server["models"]
        self.assertEqual({**normal, "id": "stable"}, stable)
        self.assertEqual(addon.read(self.root / addon.FILES[0])["custom"], 42)
        for name, data in self.before.items():
            if name not in addon.FILES:
                self.assertEqual((self.root / name).read_bytes(), data)
        first = {name: (self.root / name).read_bytes() for name in addon.FILES}
        addon.apply(self.root, self.candidate)
        self.assertEqual(first, {name: (self.root / name).read_bytes() for name in addon.FILES})

    def test_existing_custom_stable_model_is_preserved(self):
        path = self.root / addon.FILES[1]
        server = addon.read(path)
        server["models"].append({"id": "stable", "path": "custom.gguf", "session_options": {"custom": 9}})
        addon.write(path, addon.payload(server))
        addon.apply(self.root, self.candidate)
        self.assertEqual(addon.read(path), server)

    def test_apply_failure_restores_exact_original_bytes(self):
        real_write = addon.write
        failed = False

        def fail_once(path, data):
            nonlocal failed
            if path == self.root / addon.FILES[1] and not failed:
                failed = True
                raise OSError("simulated replacement failure")
            real_write(path, data)

        with patch.object(addon, "write", side_effect=fail_once):
            with self.assertRaises(OSError):
                addon.apply(self.root, self.candidate)
        for name in addon.FILES:
            self.assertEqual((self.root / name).read_bytes(), self.before[name])

    def test_crash_recovery_and_independent_edits(self):
        addon.apply(self.root, self.candidate)
        journal = next((self.root / "installer-state/stable-addon").glob("*/transaction.json"))
        state = addon.read(journal)
        state["state"] = "prepared"  # A process interrupted before commit.
        addon.write(journal, addon.payload(state))
        path = self.root / addon.FILES[2]
        saved = path.read_bytes()
        path.write_text('{"independently_edited":true}', encoding="utf8")
        with self.assertRaises(ValueError):
            addon.recover(self.root)
        self.assertEqual(addon.read(path), {"independently_edited": True})
        path.write_bytes(saved)
        addon.recover(self.root)
        for name in addon.FILES:
            self.assertEqual((self.root / name).read_bytes(), self.before[name])

    def test_missing_model_does_not_change_routing(self):
        self.model.unlink()
        with self.assertRaises(ValueError):
            addon.apply(self.root, self.candidate)
        for name in addon.FILES:
            self.assertEqual((self.root / name).read_bytes(), self.before[name])


@unittest.skipUnless(os.name == "nt", "Windows PowerShell helpers")
class StableAcquisitionTests(unittest.TestCase):
    def test_reuse_and_validation_receipt_invalidation_without_builds(self):
        code, out, err = ps(r"""
        . './scripts/common.ps1'; . './scripts/stable-addon.ps1'
        $temp=Join-Path $env:TEMP ([guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory $temp | Out-Null
        try{
            $source=Join-Path $temp 'source';New-Item -ItemType Directory $source | Out-Null
            [IO.File]::WriteAllText((Join-Path $source 'audiocpp_server.exe'),'mock')
            [IO.File]::WriteAllText((Join-Path $source 'ggml.dll'),'mock-dll')
            $script:probes=0
            function Test-StableCandidate {$script:probes++}
            function Run {throw 'No external build or setup commands are allowed'}
            function Get-RequiredDownload {throw 'No download needed'}
            $args=@{SourceRoot=(Get-Location).Path;StateRoot=$temp;Python='unused';Backend='cuda';Arch='86';Directory=$source;Model=(Join-Path $temp 'existing.gguf');Port=19091;Unattended=$true}
            $first=Prepare-StableBackend @args
            $args.Directory='';$args.ExistingDirectories=@($first)
            $second=Prepare-StableBackend @args
            if($first -ne $second -or $script:probes -ne 1){throw 'Completed runtime was not reused'}
            [IO.File]::WriteAllText((Join-Path $first 'ggml.dll'),'changed')
            if(Test-StableValidation $first cuda 86 $args.Model){throw 'Changed DLL accepted'}
            # An explicit legacy artifact must be probed again after DLL changes.
            $args.Directory=$first
            [void](Prepare-StableBackend @args)
            if($script:probes -ne 2){throw 'Changed DLL was not rechecked'}
            if(Test-StableValidation $first cuda 61 $args.Model){throw 'Different GPU accepted without verification'}
            $legacy=Join-Path $source 'audiocpp_server_voice_stable_sm61_speaker_direct.exe'
            Move-Item -LiteralPath (Join-Path $source 'audiocpp_server.exe') -Destination $legacy
            $args.Directory=$source
            [void](Prepare-StableBackend @args)
            if(-not(Test-Path -LiteralPath $legacy)){throw 'Original legacy runtime was changed'}
        }finally{Remove-Item -LiteralPath $temp -Recurse -Force}
        """)
        self.assertEqual(code, 0, out + err)

    def test_unsupported_gpu_stops_without_build_or_cpu_fallback(self):
        code, out, err = ps(r"""
        . './scripts/common.ps1'; . './scripts/stable-addon.ps1'
        function Run {throw 'Unexpected external command'}
        function Get-RequiredDownload {throw 'Unexpected download'}
        try{
            Get-StableCompletedRuntime -SourceRoot (Get-Location).Path -StateRoot $env:TEMP -Python unused -Backend cuda -Arch 61 -Unattended
            throw 'Unexpected success'
        }catch{if($_.Exception.Message -notlike '*backend=cuda / GPU=61*'){throw}}
        Write-Output 'Unsupported GPU safely rejected'
        """)
        self.assertEqual(code, 0, out + err)


if __name__ == "__main__":
    unittest.main()
