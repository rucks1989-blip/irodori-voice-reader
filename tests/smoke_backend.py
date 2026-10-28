"""Maintainer smoke test on an unused port; never registers or stops other apps."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
import urllib.error

parser = argparse.ArgumentParser()
parser.add_argument("--backend-dir", required=True, type=Path)
parser.add_argument("--model", required=True, type=Path)
parser.add_argument("--temp-root", required=True, type=Path)
args = parser.parse_args()
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
root = Path(__file__).resolve().parents[1]
example = json.loads((root / "irodori-api/config/audio_cpp_server.shared.example.json").read_text(encoding="utf8"))
example["port"] = port
for model in example["models"]:
    model["path"] = str(args.model.resolve())
with tempfile.TemporaryDirectory(prefix="ivr-cpu-smoke-", dir=args.temp_root) as temporary:
    folder = Path(temporary)
    config = folder / "server.json"
    config.write_text(json.dumps(example), encoding="utf8")
    env = dict(os.environ)
    env["PATH"] = str(args.backend_dir) + os.pathsep + env.get("PATH", "")
    with (folder / "backend.log").open("wb") as log:
        process = subprocess.Popen([str(args.backend_dir / "audiocpp_server.exe"), "--config", str(config)], cwd=args.backend_dir, env=env, stdout=log, stderr=log)
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Backend exited: " + (folder / "backend.log").read_text(encoding="utf8", errors="replace")[-3000:])
                try:
                    with urllib.request.urlopen(base + "/health", timeout=2) as response:
                        health = json.load(response)
                    if health.get("status") == "ok":
                        break
                except OSError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("CPU backend health timeout")
            with urllib.request.urlopen(base + "/v1/models", timeout=5) as response:
                models = json.load(response)
            assert {m["id"] for m in models["data"]} >= {m["id"] for m in example["models"]}
            samples = []
            for model in example["models"]:
                body = {"model": model["id"], "input": "テスト。", "language": "ja", "seed": 1234, "num_inference_steps": 2, "response_format": "wav", "options": {"no_ref": True}}
                if "voice-stable" in model["id"]:
                    stable = json.loads((root / "irodori-api/config/config.voice_stable.example.json").read_text(encoding="utf8"))
                    body["options"].update(stable["irodori_audiocpp_request_options"])
                    # The smoke test uses two RF steps, unlike the 40-step real
                    # profile. Keep its probe/bias bounds inside that range.
                    body["options"]["speaker_rescue_probe_step"] = 1
                    body["options"]["speaker_attention_bias_step_end"] = 2
                request = urllib.request.Request(base + "/v1/audio/speech", data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=300) as response:
                        wav = response.read()
                        assert response.status == 200
                except urllib.error.HTTPError as error:
                    raise RuntimeError(f"TTS {error.code}: {error.read().decode('utf8', errors='replace')[:1500]}") from error
                except OSError as error:
                    tail=(folder / 'backend.log').read_text(encoding='utf8', errors='replace')[-4000:]
                    raise RuntimeError(f"Backend connection failed: {error}\n{tail}") from error
                assert len(wav) > 44 and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
                samples.append({"model": model["id"], "status": 200, "bytes": len(wav), "steps": 2})
            print(json.dumps({"passed": True, "backend": "cpu", "health": "ok", "samples": samples, "note": "2-step smoke test, not a voice quality evaluation"}, indent=2))
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
