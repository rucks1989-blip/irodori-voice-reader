"""Maintainer: package a fresh CPU build and already-signed Firefox XPI only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--backend-dir", type=Path, required=True)
parser.add_argument("--upstream-license", type=Path, required=True)
parser.add_argument("--signed-firefox", type=Path, required=True)
args = parser.parse_args()
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
metadata = json.loads((args.backend_dir / "build.json").read_text(encoding="utf8"))
manifest = json.loads((ROOT / "distribution.json").read_text(encoding="utf8"))
if metadata["commit"] != manifest["audioCppCommit"] or metadata["backend"] != "cpu":
    raise SystemExit("Only the fixed-source CPU build is allowed in the first release")
shared_patch = ROOT / "audio-cpp-patch/apply_shared_session_patch.py"
if metadata.get("sharedSessionPatchSha256", "").lower() != hashlib.sha256(shared_patch.read_bytes()).hexdigest():
    raise SystemExit("Shared-session patch hash does not match this source")
exe = args.backend_dir / "audiocpp_server.exe"
if hashlib.sha256(exe.read_bytes()).hexdigest().lower() != metadata["exeSha256"].lower():
    raise SystemExit("Backend hash does not match build metadata")
cpu = dist / "audio-cpp-stable-cpu-x64.zip"
with zipfile.ZipFile(cpu, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(args.backend_dir.iterdir()):
        if path.is_file() and (path.suffix.lower() in {".exe", ".dll"} or path.name == "build.json"):
            archive.write(path, path.name)
    archive.write(args.upstream_license, "audio.cpp-LICENSE.txt")
    for path in args.upstream_license.parent.joinpath("external").rglob("*"):
        if path.is_file() and path.name.lower().startswith(("license", "copying", "notice")):
            archive.write(path, "licenses/" + path.relative_to(args.upstream_license.parent).as_posix())
    archive.write(ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
xpi = dist / ("IrodoriVoiceReader-Firefox-" + manifest["firefoxExtensionVersion"] + "-signed.xpi")
with zipfile.ZipFile(args.signed_firefox) as archive:
    package_manifest = json.loads(archive.read("manifest.json"))
    if package_manifest["browser_specific_settings"]["gecko"]["id"] != "irodori-voice-reader@local" or package_manifest["version"] != manifest["firefoxExtensionVersion"]:
        raise SystemExit("Unexpected signed extension ID/version")
    if not any(name in archive.namelist() for name in ["META-INF/mozilla.rsa", "META-INF/cose.sig"]):
        raise SystemExit("XPI signature files missing")
    # This pinned signed XPI is a legacy reference, not the isolated extension.
    # Never change or re-sign the existing artifact in this helper.
    expected = next(a for a in manifest["assets"] if a["kind"] == "firefox")
    if hashlib.sha256(args.signed_firefox.read_bytes()).hexdigest() != expected["sha256"]:
        raise SystemExit("Signed XPI differs from pinned release asset")
shutil.copyfile(args.signed_firefox, xpi)
remote_assets = [asset for asset in manifest["assets"] if asset.get("url")]
manifest["assets"] = remote_assets + [
    {"name": cpu.name, "kind": "backend", "backend": "cpu", "arch": "", "sha256": hashlib.sha256(cpu.read_bytes()).hexdigest()},
    {"name": xpi.name, "kind": "firefox", "sha256": hashlib.sha256(xpi.read_bytes()).hexdigest()},
]
(ROOT / "distribution.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
for path in [cpu, xpi]:
    print(f"Prepared {path.name}: {path.stat().st_size} bytes")
