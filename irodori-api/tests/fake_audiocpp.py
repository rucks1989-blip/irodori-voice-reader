"""Test-only audio.cpp-compatible server used to verify a clean checkout."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import struct
import wave

def silent_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(struct.pack("<h", 0) * 240)
    return output.getvalue()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def send_json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health":
            self.send_json({"status": "ok"})
        elif self.path == "/v1/models":
            self.send_json({"data": [{"id": "irodori-500m-v3-q8"}]})
        else:
            self.send_error(404)
    def do_POST(self):
        if self.path != "/v1/audio/speech":
            self.send_error(404); return
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length) or b"{}")
        body = silent_wav()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

ThreadingHTTPServer(("127.0.0.1", 7863), Handler).serve_forever()
