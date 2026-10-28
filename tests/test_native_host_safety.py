import importlib.util
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("irodori_native_host_test", ROOT / "native-host" / "irodori_native_host.py")
native_host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_host)


class NativeHostSafetyTests(unittest.TestCase):
    def _write_voices(self, root, record):
        path = root / "config" / "voices.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "voices": [record]}), encoding="utf-8")
        return path

    def test_managed_voice_delete_unlinks_only_registered_owned_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            voice_dir = root / "IrodoriVoiceReader" / "voices" / "isolatedVoice"
            voice_dir.mkdir(parents=True)
            managed_file = voice_dir / "reference.wav"
            unrelated_file = voice_dir / "user-notes.txt"
            managed_file.write_bytes(b"isolated test data")
            unrelated_file.write_text("must remain", encoding="utf-8")
            config_path = self._write_voices(root, {
                "id": "isolatedVoice",
                "name": "Isolated test Voice",
                "managed_by": "irodori-voice-reader",
                "reference_wav": "IrodoriVoiceReader/voices/isolatedVoice/reference.wav",
            })

            with patch.object(native_host, "_reload_api", return_value={"status": "stopped", "applied": False}):
                result = native_host.delete_voice({"id": "isolatedVoice", "deleteManagedFile": True}, {"root": root})

            self.assertEqual(result["deletedManagedFiles"], ["reference.wav"])
            self.assertFalse(managed_file.exists())
            self.assertEqual(unrelated_file.read_text(encoding="utf-8"), "must remain")
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["voices"], [])

    def test_unmanaged_files_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "voice_refs" / "original.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"original")
            config_path = self._write_voices(root, {
                "id": "originalVoice",
                "name": "Original",
                "reference_wav": "voice_refs/original.wav",
            })
            with self.assertRaises(ValueError):
                native_host.delete_voice({"id": "originalVoice", "deleteManagedFile": True}, {"root": root})
            self.assertTrue(source.is_file())
            self.assertEqual(len(json.loads(config_path.read_text(encoding="utf-8"))["voices"]), 1)

    def test_path_escape_is_rejected_without_touching_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            external = root / "outside.wav"
            external.write_bytes(b"must remain")
            config_path = self._write_voices(root, {
                "id": "escapeVoice",
                "name": "Escape",
                "managed_by": "irodori-voice-reader",
                "reference_wav": "../outside.wav",
            })
            with self.assertRaises(ValueError):
                native_host.delete_voice({"id": "escapeVoice", "deleteManagedFile": True}, {"root": root})
            self.assertTrue(external.is_file())
            self.assertEqual(len(json.loads(config_path.read_text(encoding="utf-8"))["voices"]), 1)

    def test_reload_reports_running_api_and_stopped_api(self):
        observed = {}

        class ApiHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"protocol": "irodori_voice"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                observed["path"] = self.path
                observed["origin"] = self.headers.get("Origin")
                body = json.dumps({"status": "reloaded"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = native_host._reload_api({"health_url": f"http://127.0.0.1:{server.server_port}/health"})
            self.assertEqual(result, {"status": "reloaded", "applied": True, "httpStatus": 200})
            self.assertEqual(observed["path"], "/v1/reload")
            self.assertEqual(observed["origin"], "chrome-extension://aeaichhflmacmifblidbpojgfemjijlb")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        self.assertEqual(
            native_host._reload_api({"health_url": f"http://127.0.0.1:{closed_port}/health"}),
            {"status": "stopped", "applied": False},
        )


if __name__ == "__main__":
    unittest.main()
