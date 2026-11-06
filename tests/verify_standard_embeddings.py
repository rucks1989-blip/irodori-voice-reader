"""Opt-in real inference, isolated ports/processes; never register Native Hosts.

All personal paths are command-line inputs. Outputs stay in a new test folder.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import soundfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "irodori-api"))
from kurisu_voice.voices import _slug


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def ready(base, process, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Test process exited: {process.pid}, code={process.returncode}")
        try:
            if requests.get(base + "/health", timeout=2).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(.2)
    raise TimeoutError(f"Test service did not start: {base}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-api", type=Path, required=True)
    parser.add_argument("--embedding-a", type=Path, required=True)
    parser.add_argument("--embedding-b", type=Path, required=True)
    parser.add_argument("--reference-wav", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    args = parser.parse_args()
    if args.test_root.exists():
        raise FileExistsError("Use a new test folder; existing files will not be overwritten")
    args.test_root.mkdir(parents=True)
    personal = [args.embedding_a.resolve(), args.embedding_b.resolve(), args.reference_wav.resolve()]
    configs = list((args.installed_api / "config").glob("*.json"))
    learning_files = [p for p in args.embedding_a.parent.iterdir() if p.is_file()]
    managed_files = [p for p in (args.installed_api / "IrodoriVoiceReader/voices").rglob("*") if p.is_file()]
    originals = {str(p): digest(p) for p in personal + configs + learning_files + managed_files}
    save(args.test_root / "original-hashes.json", originals)
    server_config = json.loads((args.installed_api / "config/audio_cpp_server.shared.json").read_text(encoding="utf-8-sig"))
    runtime = json.loads((args.installed_api / "config/runtime.json").read_text(encoding="utf-8-sig"))
    exe = Path(runtime["audio_cpp_shared_executable"])
    if not exe.is_absolute():
        exe = args.installed_api / exe
    backend_port = port()
    model = next(m for m in server_config["models"] if m["id"] == "irodori-500m-v3-q8")
    model = dict(model)
    model_path = Path(model["path"])
    model["path"] = str(model_path if model_path.is_absolute() else args.installed_api / model_path)
    server_config.update(port=backend_port, host="127.0.0.1", models=[model])
    config_path = args.test_root / "backend.json"
    save(config_path, server_config)
    target = f"http://127.0.0.1:{backend_port}"
    records = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/health":
                self.send_error(404); return
            reply = requests.get(target + self.path, timeout=5)
            self.send_response(reply.status_code); self.end_headers(); self.wfile.write(reply.content)

        def do_POST(self):
            if self.path != "/v1/audio/speech":
                self.send_error(404); return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reference = payload.get("speaker_embedding_path") or payload.get("voice_ref")
            records.append({"payload": payload, "referenceSha256": digest(reference) if reference else None})
            reply = requests.post(target + self.path, json=payload, timeout=600)
            self.send_response(reply.status_code)
            self.send_header("Content-Type", reply.headers.get("Content-Type", "application/octet-stream"))
            self.end_headers(); self.wfile.write(reply.content)

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    processes, logs, results = [], [], []
    environment = dict(os.environ)
    cache = args.test_root / "cache"; cache.mkdir()
    environment.update(TEMP=str(cache), TMP=str(cache))
    environment["PATH"] = str(exe.parent) + os.pathsep + environment.get("PATH", "")

    def launch(command, cwd, name):
        output = (args.test_root / (name + ".log")).open("wb"); logs.append(output)
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=0x08000000 if os.name == "nt" else 0)
        processes.append(process)
        return process

    try:
        backend = launch([str(exe), "--config", str(config_path)], exe.parent, "backend")
        ready(target, backend)
        for number, name in enumerate(("日本語の導入先", "other-installation")):
            installed = args.test_root / name
            api = installed / "irodori-api"
            shutil.copytree(ROOT / "irodori-api", api, ignore=shutil.ignore_patterns("__pycache__", ".venv", "models", "logs"))
            save(installed / ".ivr-install.json", {"state": "runtime-ready"})
            save(installed / "chrome-identity.json", {"extensionId": "a" * 32})
            api_port = port(); base = f"http://127.0.0.1:{api_port}"
            save(api / "config/api.json", {"host": "127.0.0.1", "port": api_port})
            save(api / "config/capabilities.json", {"standard": True, "voice_stable": False})
            save(api / "config/config.json", {"irodori_audiocpp_api_url": f"http://127.0.0.1:{proxy.server_port}/v1/audio/speech",
                                              "irodori_audiocpp_model": model["id"]})
            save(api / "config/audio_cpp_server.shared.json", server_config)
            save(api / "config/voices.json", {"voices": []})
            directory = str(args.embedding_a.parent.resolve()) if number == 0 else os.path.relpath(args.embedding_a.parent.resolve(), api)
            save(api / "config/voice_sources.json", {"speaker_embedding_dirs": [directory]})
            worker = launch([sys.executable, str(api / "kurisu_voice_api.py")], args.test_root, f"api-{number}")
            ready(base, worker)
            headers = {"Origin": "moz-extension://01234567-89ab-cdef-0123-456789abcdef"}

            def post(path, payload):
                return requests.post(base + path, json=payload, headers=headers, timeout=600)

            for voice_id, file, kind in (("embedding-a", personal[0], "speaker_embedding"),
                                         ("embedding-b", personal[1], "speaker_embedding"),
                                         ("wav-test", personal[2], "reference_wav")):
                response = post("/voices/import", {"id": voice_id, "name": voice_id, "voiceType": kind,
                    "fileName": file.name, "dataBase64": base64.b64encode(file.read_bytes()).decode("ascii")})
                if not response.ok:
                    raise RuntimeError(response.text)
            auto_id = _slug(personal[0].name[:-len(".speaker.safetensors")])
            voices = {v["id"]: v for v in requests.get(base + "/voices", timeout=5).json()["voices"]}
            assert Path(voices[auto_id]["reference"]).resolve() == personal[0]
            cases = [(auto_id, personal[0], "speaker_embedding_path"),
                     ("embedding-a", personal[0], "speaker_embedding_path"), ("embedding-b", personal[1], "speaker_embedding_path"),
                     ("wav-test", personal[2], "voice_ref"), ("embedding-a", personal[0], "speaker_embedding_path")]
            for index, (voice_id, source, field) in enumerate(cases):
                response = post("/tts", {"text": "こんにちは。動作確認です。", "voice": voice_id,
                                         "engine": "standard", "speaker_tag_policy": "preserve"})
                if not response.ok:
                    raise RuntimeError(f"{name}/{voice_id}: {response.status_code}: {response.text}")
                assert response.headers["X-Irodori-Engine"] == "standard"
                assert response.headers["X-Irodori-Model"] == model["id"]
                wav = args.test_root / f"test-{number}-{index}-{voice_id}.wav"; wav.write_bytes(response.content)
                audio, rate = soundfile.read(wav)
                assert len(audio) > 0 and abs(audio).max() > 0, "Empty/silent audio"
                record = records[-1]
                assert record["referenceSha256"] == digest(source)
                assert field in record["payload"]
                assert ("voice_ref" if field == "speaker_embedding_path" else "speaker_embedding_path") not in record["payload"]
                assert record["payload"]["model"] == model["id"]
                results.append({"installation": name, "voice": voice_id, "file": wav.name,
                                "seconds": len(audio) / rate, "referenceSha256": record["referenceSha256"]})
            before = len(records)
            unknown = post("/tts", {"text": "test", "voice": "not-registered", "engine": "standard"})
            assert unknown.status_code == 404 and len(records) == before
            missing = api / "IrodoriVoiceReader/voices/embedding-b/speaker.safetensors"
            missing.unlink()  # This is a newly imported test-owned copy only.
            invalid = post("/tts", {"text": "test", "voice": "embedding-b", "engine": "standard"})
            assert invalid.status_code == 400 and "Speaker Embedding" in invalid.text and len(records) == before
            worker.terminate(); worker.wait(timeout=15)
        for path, value in originals.items():
            assert digest(path) == value, f"Original file changed: {path}"
        save(args.test_root / "results.json", {"engine": "standard", "inference": results, "nativeRequests": records,
                                               "originalFilesUnchanged": True, "nativeHostRegistrationChanged": False})
        print(json.dumps({"successfulWavs": len(results), "testRoot": str(args.test_root), "originalFilesUnchanged": True}))
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate(); process.wait(timeout=15)
        proxy.shutdown(); proxy.server_close()
        for output in logs:
            output.close()


if __name__ == "__main__":
    main()
