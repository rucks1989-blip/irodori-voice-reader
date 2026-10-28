import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
_sandbox = tempfile.TemporaryDirectory(prefix="irodori-api-security-")
_old_environment = {key: os.environ.get(key) for key in (
    "IRODORI_VOICE_CONFIG", "IRODORI_STABLE_VOICE_CONFIG", "IRODORI_API_CONFIG",
    "IRODORI_VOICES_CONFIG", "IRODORI_API_PID", "IRODORI_API_PORT",
)}
_sandbox_path = Path(_sandbox.name)
os.environ.update({
    "IRODORI_VOICE_CONFIG": str(_sandbox_path / "standard.json"),
    "IRODORI_STABLE_VOICE_CONFIG": str(_sandbox_path / "stable.json"),
    "IRODORI_API_CONFIG": str(_sandbox_path / "api.json"),
    "IRODORI_VOICES_CONFIG": str(_sandbox_path / "voices.json"),
    "IRODORI_API_PID": str(_sandbox_path / "server.pid"),
    "IRODORI_API_PORT": "18765",
})
sys.path.insert(0, str(API_ROOT))
api = importlib.import_module("kurisu_voice_api")
sys.path.remove(str(API_ROOT))
for _key, _value in _old_environment.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value
_sandbox.cleanup()


class ApiRequestSecurityTests(unittest.TestCase):
    def test_only_known_extension_origins_are_trusted(self):
        self.assertTrue(api.trusted_extension_origin(api.TRUSTED_CHROME_ORIGIN))
        self.assertTrue(api.trusted_extension_origin("moz-extension://01234567-89ab-cdef-0123-456789abcdef"))
        self.assertFalse(api.trusted_extension_origin("https://example.com"))
        self.assertFalse(api.trusted_extension_origin("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
        self.assertFalse(api.trusted_extension_origin("moz-extension://not-a-profile-id"))

    def test_post_is_denied_before_body_processing_for_web_origin(self):
        request = api.Handler.__new__(api.Handler)
        request.headers = {"Host": "127.0.0.1:18765", "Origin": "https://example.com"}
        request.path = "/tts"
        responses = []
        request._send_json = lambda status, body: responses.append((status, body))
        request._request_json = lambda: self.fail("unauthorized POST body was read")

        request.do_POST()

        self.assertEqual(responses[0][0], 403)
        self.assertEqual(responses[0][1]["error"], "origin_forbidden")

    def test_post_requires_extension_origin_and_expected_local_host(self):
        cases = [
            ({"Host": "127.0.0.1:18765"}, "extension_origin_required"),
            ({"Host": "evil.example:18765", "Origin": api.TRUSTED_CHROME_ORIGIN}, "host_forbidden"),
        ]
        for headers, expected in cases:
            request = api.Handler.__new__(api.Handler)
            request.headers = headers
            responses = []
            request._send_json = lambda status, body, target=responses: target.append((status, body))
            self.assertFalse(request._authorize_local_request(require_extension_origin=True))
            self.assertEqual(responses[0][0], 403)
            self.assertEqual(responses[0][1]["error"], expected)

        request = api.Handler.__new__(api.Handler)
        request.headers = {"Host": "localhost:18765", "Origin": api.TRUSTED_CHROME_ORIGIN}
        request._send_json = lambda *_: self.fail("valid local extension request was rejected")
        self.assertTrue(request._authorize_local_request(require_extension_origin=True))


if __name__ == "__main__":
    unittest.main()
