"""Regression tests; live transfer and installer UI checks are recorded separately."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from test_installer_windows import ps, ROOT

spec = importlib.util.spec_from_file_location("ivr_download", ROOT / "scripts/download-file.py")
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)
folder_spec = importlib.util.spec_from_file_location("ivr_folder", ROOT / "scripts/import-download.py")
folder = importlib.util.module_from_spec(folder_spec)
folder_spec.loader.exec_module(folder)


class TransferTests(unittest.TestCase):
    def test_deep_selected_payload_uses_windows_extended_paths(self):
        with tempfile.TemporaryDirectory(dir=folder.filesystem_path(tempfile.gettempdir())) as temporary:
            root = Path(temporary)
            selected = root / "selected"
            relative = "/".join(["nested-" + "x" * 55] * 4) + "/payload.dll"
            payload = folder.filesystem_path(selected / Path(relative))
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"pinned deep payload")
            sha = "d" * 64
            spec = {"name":"source.zip", "sha256":sha}
            catalog = {"archives":{sha:{"rootPrefix":"", "files":{relative:{
                "bytes":payload.stat().st_size,"sha256":hashlib.sha256(payload.read_bytes()).hexdigest()}}}}}
            result = folder.import_selected(spec, catalog, selected, root / "cache/source.zip")
            installed = folder.place(spec, catalog, result, root / "installed", "")
            self.assertEqual((installed / Path(relative)).read_bytes(), b"pinned deep payload")

    def test_expanded_import_staging_fits_a_normal_windows_install_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "selected"
            selected.mkdir()
            payload = b"pinned executable"
            (selected / "audiocpp_server.exe").write_bytes(payload)
            sha = "c" * 64
            spec = {"name":"audio-v0.9.3-bin-windows-x64-cpu-portable.zip", "sha256":sha}
            catalog = {"archives":{sha:{"rootPrefix":"", "files":{
                "audiocpp_server.exe":{"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}}}}}
            # Reproduce the chosen-install/session nesting from the real GUI
            # test without requiring long-path policy changes on Windows.
            cache = root / ("x" * max(1, 145 - len(str(root))))
            result = folder.import_selected(spec, catalog, selected, cache / spec["name"])
            self.assertEqual((result / "audiocpp_server.exe").read_bytes(), payload)

    def test_expanded_folder_verification_ignores_extra_code_and_rejects_wrong_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "arbitrary location" / "nested" / "runtime"
            selected.mkdir(parents=True)
            (selected / "audiocpp_server.exe").write_bytes(b"pinned executable")
            (selected / "runtime.dll").write_bytes(b"pinned DLL")
            (selected / "extra.exe").write_bytes(b"not trusted, must not copy")
            archive_sha = "a" * 64
            spec = {"name": "runtime.zip", "sha256": archive_sha, "bytes": 123}
            files = {p.name: {"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
                     for p in selected.iterdir() if p.name != "extra.exe"}
            catalog = {"archives": {archive_sha: {"rootPrefix":"", "files":files}}}
            imported = folder.import_selected(spec, catalog, root / "arbitrary location", root / "cache" / "runtime.zip")
            installed = root / "installed"
            folder.place(spec, catalog, imported, installed, "")
            self.assertEqual((installed / "audiocpp_server.exe").read_bytes(), b"pinned executable")
            self.assertFalse((installed / "extra.exe").exists())
            (selected / "runtime.dll").write_bytes(b"wrong version")
            with self.assertRaises(ValueError):
                folder.import_selected(spec, catalog, selected, root / "other" / "runtime.zip")
            self.assertEqual((installed / "runtime.dll").read_bytes(), b"pinned DLL")

    def test_single_file_folder_and_source_root_are_not_tied_to_manifest_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "elsewhere"
            selected.mkdir()
            (selected / "model.gguf").write_bytes(b"GGUF fixture")
            spec = {"name":"model.gguf","bytes":12,"sha256":hashlib.sha256(b"GGUF fixture").hexdigest()}
            result = folder.import_selected(spec, {}, selected, root / "app" / "models" / "model.gguf")
            self.assertEqual(result.read_bytes(), b"GGUF fixture")
            source = root / "unpacked source with arbitrary name"
            (source / "scripts").mkdir(parents=True)
            (source / "CMakeLists.txt").write_bytes(b"pinned build")
            (source / "scripts/build_windows.ps1").write_bytes(b"pinned script")
            sha = "b" * 64
            files = {"audio.cpp-fixed/"+p.relative_to(source).as_posix():
                     {"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
                     for p in source.rglob("*") if p.is_file()}
            catalog={"archives":{sha:{"rootPrefix":"audio.cpp-fixed/","files":files}}}
            spec={"name":"source.zip","bytes":123,"sha256":sha}
            result=folder.import_selected(spec,catalog,source,root / "cache" / "source.zip")
            folder.place(spec,catalog,result,root / "build-source","audio.cpp-fixed")
            self.assertEqual((root / "build-source/scripts/build_windows.ps1").read_bytes(),b"pinned script")

    def test_https_only_without_embedded_credentials(self):
        for url in ["http://example.com/a", "file:///a", "https://user:password@example.com/a"]:
            with self.assertRaises(ValueError):
                download.require_https(url)
        download.require_https("https://example.com/a?download=true")

    def test_cached_file_checks_size_and_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "file"
            p.write_bytes(b"verified fixture")
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertTrue(download.verified(p, digest, p.stat().st_size))
            self.assertFalse(download.verified(p, digest, p.stat().st_size + 1))
            self.assertFalse(download.verified(p, "0" * 64))

    def test_complete_partial_does_not_contact_network(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "fixture.partial"
            p.write_bytes(b"cached")
            download.transfer("https://example.invalid/unused", p, hashlib.sha256(b"cached").hexdigest(), 6)


@unittest.skipUnless(os.name == "nt", "Windows helpers")
class RecoveryTests(unittest.TestCase):
    def test_recovery_has_no_attempt_limit_or_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)
            sha=hashlib.sha256(b"fixture").hexdigest()
            code,out,err=ps(f"""
            . './scripts/common.ps1'
            $env:IVR_SETUP_LOG_ROOT='{path}'
            $script:calls=0;$script:dialogs=0
            function Get-Verified {{param($Url,$Destination,$Sha256,$Bytes,$Python,[switch]$PythonFirst,$AlternateUrls,[switch]$Restart)
                $script:calls++;if($script:calls -le 10){{throw 'HTTP 404 fixture'}}
                [IO.File]::WriteAllBytes($Destination,[Text.Encoding]::ASCII.GetBytes('fixture'))
            }}
            function Show-SetupDialog($Kind,$Message,$Filter,$Default){{$script:dialogs++;if($Message -notmatch '404' -or $Message -notmatch 'model.gguf'){{throw 'Missing recovery metadata'}};return 'retry'}}
            $spec=[pscustomobject]@{{name='model.gguf';title='Irodori model';url='https://example.invalid/fixture';sha256='{sha}';bytes=7}}
            $watch=[Diagnostics.Stopwatch]::StartNew()
            [void](Get-RequiredDownload $spec '{path / 'model.gguf'}')
            if($script:calls -ne 11 -or $script:dialogs -ne 10 -or $watch.Elapsed.TotalSeconds -gt 5){{throw 'Unexpected limit or delay'}}
            """)
            self.assertEqual(code,0,out+err)

    def test_concurrent_writer_is_rejected_without_touching_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            dest=Path(temporary)/"file.bin"
            code,out,err=ps(f"""
            . './scripts/common.ps1'
            $hold=[IO.File]::Open('{dest}.download.lock','OpenOrCreate','ReadWrite','None')
            try{{
                try{{Get-Verified 'https://example.invalid/unused' '{dest}' ('a'*64);throw 'Second writer allowed'}}
                catch{{if($_.Exception.Message -notmatch '同時書き込み'){{throw}}}}
            }}finally{{$hold.Dispose()}}
            """)
            self.assertEqual(code,0,out+err)
            self.assertFalse(dest.exists())

    def test_import_rejects_wrong_file_and_preserves_original_then_accepts_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            good, bad, dest = [path / n for n in ("model.gguf", "wrong.zip", "installed.gguf")]
            good.write_bytes(b"good model fixture")
            bad.write_bytes(b"not a model")
            dest.write_bytes(b"old invalid data")
            sha = hashlib.sha256(good.read_bytes()).hexdigest()
            code, out, err = ps(f"""
            . './scripts/common.ps1'
            try {{ Import-VerifiedFile '{bad}' '{dest}' '{sha}' {good.stat().st_size};throw 'wrong accepted' }} catch {{ if($_.Exception.Message -eq 'wrong accepted'){{throw}} }}
            Import-VerifiedFile '{good}' '{dest}' '{sha}' {good.stat().st_size}
            Import-VerifiedFile '{good}' '{dest}' '{sha}' {good.stat().st_size}
            """)
            self.assertEqual(code, 0, out + err)
            self.assertEqual(dest.read_bytes(), good.read_bytes())
            self.assertEqual(bad.read_bytes(), b"not a model")
            self.assertEqual(list(path.glob("installed.gguf.rejected-*"))[0].read_bytes(), b"old invalid data")

    def test_revocation_failure_falls_back_without_repeating_curl(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "download.bin"
            sha = hashlib.sha256(b"verified").hexdigest()
            code, out, err = ps(f"""
            . './scripts/common.ps1'
            $script:calls=New-Object 'Collections.Generic.List[string]'
            function Run($Exe,$Arguments) {{
                if($Exe -like '*curl.exe'){{$script:calls.Add('curl');throw 'curl: (35) schannel: CRYPT_E_REVOCATION_OFFLINE (0x80092013)'}}
                $script:calls.Add('python')
                [IO.File]::WriteAllBytes('{dest}.partial',[Text.Encoding]::ASCII.GetBytes('verified'))
            }}
            Get-Verified 'https://example.invalid/fixture' '{dest}' '{sha}' 8 'python.exe'
            if(($script:calls -join ',') -ne 'curl,python'){{throw 'Wrong fallback order'}}
            """)
            self.assertEqual(code, 0, out + err)
            self.assertEqual(dest.read_bytes(), b"verified")

    def test_build_jobs_are_cpu_and_memory_bounded(self):
        code, out, err = ps("""
        $ast=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path './scripts/build-backend.ps1'),[ref]$null,[ref]$null)
        $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-BuildJobs'},$true)
        Invoke-Expression $fn.Extent.Text
        @((Get-BuildJobs 16 32GB 'cuda'),(Get-BuildJobs 6 2GB 'cuda'),(Get-BuildJobs 1 512MB 'cpu'),(Get-BuildJobs 16 32GB 'cpu' 3)) | ConvertTo-Json -Compress
        """)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out), [15, 1, 1, 3])

    def test_ai_prompt_contains_exact_target_and_public_urls_not_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "state.json").write_text(json.dumps({"step": "model download", "error": "curl 35", "completed": False}), encoding="utf8")
            (path / "download-error.json").write_text(json.dumps({"target": {"name": "model.gguf", "title": "Irodori model", "url": "https://example.com/model.gguf", "pageUrl": "https://example.com/page", "sha256": "0"*64, "bytes": 123, "searchWords": "Irodori GGUF"}, "error": "CRYPT_E_REVOCATION_OFFLINE 0x80092013\napi_key=private-key-value\n"+os.environ["USERPROFILE"], "reason": "certificate", "installedFilePresent": False, "partialFilePresent": True}), encoding="utf8")
            code, out, err = ps(f"& './scripts/support.ps1' -StateRoot '{path}' -NoOpen")
            self.assertEqual(code, 0, out + err)
            text = (path / "AI-support.txt").read_text(encoding="utf8")
            for value in ["https://example.com/model.gguf", "model.gguf", "Irodori GGUF", "0x80092013", "CRYPT_E_REVOCATION_OFFLINE"]:
                self.assertIn(value, text)
            self.assertNotIn("private-key-value", text)
            self.assertNotIn(os.environ["USERPROFILE"], text)
            repository = "https://github.com/rucks1989-blip/irodori-voice-reader"
            self.assertIn(repository, text)
            self.assertNotIn(os.environ["USERNAME"], text.replace(repository, ""))


if __name__ == "__main__":
    unittest.main()
