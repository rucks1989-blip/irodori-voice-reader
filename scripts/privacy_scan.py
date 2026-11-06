"""Fail closed: inspect only the declared release-source inventory, not the PC."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DENIED_PARTS = {".git", ".venv", "__pycache__", "logs", "models", "downloads", "build-cache", "build-output", "dist", ".state", "ai-support", "assets"}
DENIED_SUFFIXES = {".wav", ".mp3", ".flac", ".safetensors", ".gguf", ".ckpt", ".pth", ".exe", ".dll", ".xpi", ".zip", ".log", ".pyc"}
LIVE_CONFIGS = {"irodori-api/config/capabilities.json","irodori-api/config/api.json", "irodori-api/config/config.json", "irodori-api/config/config.voice_stable.json", "irodori-api/config/voices.json", "irodori-api/config/voice_sources.json", "irodori-api/config/runtime.json", "irodori-api/config/audio_cpp_server.shared.json", "native-host/config.json"}
PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)[A-Z]:[\\/]Users[\\/](?!YOUR_USER|<)[^\s\\/]+"),
    re.compile(r"(?i)[A-Z]:[\\/]KurisuModel(?:[\\/]|\b)"),
]

def allowed(name):
    p = PurePosixPath(name)
    source_model_code = (name.startswith("audio-cpp-stable-patch/added-files/src/models/")
                         or name.startswith("audio-cpp-stable-patch/added-files/include/engine/models/")) and p.suffix.lower() in {".cpp", ".h"}
    denied = DENIED_PARTS - {"models"} if source_model_code else DENIED_PARTS
    return (not p.is_absolute() and ".." not in p.parts and not any(part.lower() in denied for part in p.parts)
            and p.suffix.lower() not in DENIED_SUFFIXES and name not in LIVE_CONFIGS
            and name not in {".env", ".env.local"})

def inspect(files):
    findings = []
    for item in files:
        name = item["path"]
        if not allowed(name):
            findings.append(f"Forbidden release-source file: {name}")
            continue
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            findings.append(f"Missing or linked source: {name}")
            continue
        data = path.read_bytes()
        if len(data) > 5 * 1024 * 1024:
            findings.append(f"Unexpected large source: {name}")
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            findings.append(f"Inventory hash mismatch: {name}")
        if path.suffix.lower() == ".png":
            continue  # Only extension brand icons are allowed below.
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            findings.append(f"Non-UTF-8 text: {name}")
            continue
        for pattern in PATTERNS:
            if pattern.search(text):
                findings.append(f"Private data pattern in: {name}")
                break
    return findings

def main():
    inventory = json.loads((ROOT / "package-files.json").read_text(encoding="utf8"))
    files = inventory["files"]
    names = [x["path"] for x in files]
    errors = inspect(files)
    provenance = json.loads((ROOT / "provenance.json").read_text(encoding="utf8"))["importedFiles"]
    for name, expected in provenance.items():
        if name.startswith("audio-cpp-stable-patch/added-files/"):
            path = ROOT / name
            if name not in names or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                errors.append("Missing or mismatched stable build source: " + name)
    if len(names) != len(set(names)):
        errors.append("Duplicate inventory entry")
    pngs = [n for n in names if n.endswith(".png")]
    if any(not re.fullmatch(r"(?:chrome|firefox)-extension/icons/[A-Za-z0-9_.-]+\.png", n) for n in pngs):
        errors.append("Only extension brand icons may be binary source")
    for error in errors:
        print(error)
    print(f"Privacy/inventory check: {len(files)} files, {len(errors)} findings")
    return bool(errors)

if __name__ == "__main__":
    sys.exit(main())
