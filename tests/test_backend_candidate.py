"""Candidate verification rejects ordinary runtimes that only expose model aliases."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CandidateTests(unittest.TestCase):
    def check_candidate(self, supports_stable):
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder) / 'trace.log'
            requests = []

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    requests.append(body)
                    with trace.open('a', encoding='utf8') as stream:
                        if supports_stable:
                            for key, value in body['options'].items():
                                if key != 'no_ref':
                                    stream.write('irodori_tts.chunk.0.' + key + ' ' + json.dumps(value) + '\n')
                        else:
                            stream.write('irodori_tts.reference.used 0\n')
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'RIFF' + bytes(100))

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([sys.executable, str(ROOT/'scripts/verify-backend.py'),
                                         'http://127.0.0.1:'+str(server.server_port), str(trace)],
                                        capture_output=True, timeout=15)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            return result, requests

    def test_ordinary_wav_generation_does_not_prove_stable_support(self):
        result, requests = self.check_candidate(False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'ordinary WAV generation alone is insufficient', result.stderr)

    def test_both_effective_option_sets_are_required(self):
        result, requests = self.check_candidate(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(requests), 2)
        self.assertFalse(requests[0]['options']['speaker_rescue_enabled'])
        self.assertTrue(requests[1]['options']['speaker_rescue_enabled'])


if __name__ == '__main__':
    unittest.main()
