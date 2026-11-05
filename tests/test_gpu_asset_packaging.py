"""Maintainer packaging is architecture-specific and never regenerates CPU/XPI assets."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GpuAssetTests(unittest.TestCase):
    def fixture(self, root, stable=True):
        (root/'scripts').mkdir()
        for name in ('prepare-assets.py', 'make-download-catalog.py'):
            shutil.copyfile(ROOT/'scripts'/name, root/'scripts'/name)
        (root/'audio-cpp-patch').mkdir()
        patch = b'fixture shared-session patch'
        (root/'audio-cpp-patch/apply_shared_session_patch.py').write_bytes(patch)
        (root/'THIRD_PARTY_NOTICES.md').write_text('fixture notice', encoding='utf8')
        (root/'LICENSE').write_text('fixture upstream license', encoding='utf8')
        original = [{'name': 'existing-cpu.zip', 'kind': 'backend', 'role': 'stable', 'backend': 'cpu'},
                    {'name': 'existing-signed.xpi', 'kind': 'firefox', 'role': 'signed-install'}]
        manifest = {'audioCppCommit': 'a'*40, 'audioCppSource': {'name': 'source.zip'}, 'assets': original}
        (root/'distribution.json').write_text(json.dumps(manifest), encoding='utf8')
        backend = root/'runtime'
        backend.mkdir()
        exe = b'fixture executable, not a runtime verification'
        (backend/'audiocpp_server.exe').write_bytes(exe)
        (backend/'fixture.dll').write_bytes(b'fixture DLL')
        metadata = {'commit': 'a'*40, 'backend': 'cuda', 'arch': '89', 'stableSupported': stable,
                    'standardOnly': not stable, 'sharedSessionPatchSha256': hashlib.sha256(patch).hexdigest(),
                    'exeSha256': hashlib.sha256(exe).hexdigest()}
        (backend/'build.json').write_text(json.dumps(metadata), encoding='utf8')
        return original, backend

    def run_packaging(self, root, backend):
        return subprocess.run([sys.executable, str(root/'scripts/prepare-assets.py'),
                               '--gpu-backend-dir', str(backend), '--upstream-license', str(root/'LICENSE')],
                              capture_output=True, timeout=20)

    def test_gpu_mode_preserves_existing_assets_and_merge_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original, backend = self.fixture(root)
            result = self.run_packaging(root, backend)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((root/'distribution.json').read_text(encoding='utf8'))
            self.assertEqual(manifest['assets'][:2], original)
            asset = manifest['assets'][-1]
            self.assertEqual(asset['arch'], '89')
            self.assertEqual(asset['role'], 'stable')
            self.assertEqual(asset['backend'], 'cuda')
            self.assertIn('sm89', asset['name'])
            catalog = root/'scripts/download-catalog.json'
            catalog.write_text(json.dumps({'schema': 1, 'archives': {'existing': {'files': {}}}}), encoding='utf8')
            archive = root/'dist'/asset['name']
            result = subprocess.run([sys.executable, str(root/'scripts/make-download-catalog.py'), '--merge',
                                     '--archive', asset['name']+'='+str(archive)], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            records = json.loads(catalog.read_text(encoding='utf8'))['archives']
            self.assertIn('existing', records)
            self.assertEqual(set(records[asset['sha256']]['files']), {'audiocpp_server.exe', 'fixture.dll'})

    def test_standard_gpu_metadata_is_not_packaged_as_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, backend = self.fixture(root, stable=False)
            before = (root/'distribution.json').read_bytes()
            result = self.run_packaging(root, backend)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root/'distribution.json').read_bytes(), before)
            self.assertEqual(list((root/'dist').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
