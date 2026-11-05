"""Maintainer: package verified builds; GPU-only mode leaves CPU and signed XPI intact."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--backend-dir", type=Path)
parser.add_argument("--gpu-backend-dir", type=Path)
parser.add_argument("--upstream-license", type=Path, required=True)
parser.add_argument("--signed-firefox", type=Path)
args = parser.parse_args()
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
if args.gpu_backend_dir:
    metadata = json.loads((args.gpu_backend_dir / "build.json").read_text(encoding="utf8"))
    manifest = json.loads((ROOT / "distribution.json").read_text(encoding="utf8"))
    arch = str(metadata.get("arch", ""))
    if (metadata.get("commit") != manifest["audioCppCommit"] or metadata.get("backend") != "cuda"
            or metadata.get("stableSupported") is not True or metadata.get("standardOnly") is not False
            or not arch.isdigit()):
        sys.exit("GPU packaging requires a fixed-source stable CUDA build with its actual architecture")
    patch = ROOT / "audio-cpp-patch/apply_shared_session_patch.py"
    if metadata.get("sharedSessionPatchSha256", "").lower() != hashlib.sha256(patch.read_bytes()).hexdigest():
        sys.exit("Shared-session patch does not match this source")
    exe = args.gpu_backend_dir / "audiocpp_server.exe"
    if hashlib.sha256(exe.read_bytes()).hexdigest() != metadata.get("exeSha256", "").lower():
        sys.exit("GPU executable differs from its build metadata")
    cuda_license = ROOT / "docs/licenses/NVIDIA-CUDA-12.6-EULA.html"
    if any(p.name.lower().startswith(("cudart", "cublas", "cufft")) for p in args.gpu_backend_dir.glob("*.dll")) and not cuda_license.is_file():
        sys.exit("CUDA runtime redistribution requires its retained license document")
    destination = dist / ("audio-cpp-stable-cuda-sm" + arch + "-x64.zip")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(args.gpu_backend_dir.iterdir()):
            if path.is_file() and (path.suffix.lower() in {".exe", ".dll"} or path.name == "build.json"):
                archive.write(path, path.name)
        archive.write(args.upstream_license, "audio.cpp-LICENSE.txt")
        for path in args.upstream_license.parent.joinpath("external").rglob("*"):
            if path.is_file() and path.name.lower().startswith(("license", "copying", "notice")):
                archive.write(path, "licenses/" + path.relative_to(args.upstream_license.parent).as_posix())
        archive.write(ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
        if cuda_license.is_file():
            archive.write(cuda_license, "licenses/NVIDIA-CUDA-12.6-EULA.html")
    spec = {"name": destination.name, "title": "声質安定対応 CUDA sm" + arch + " 完成品",
            "version": manifest["audioCppCommit"], "kind": "backend", "role": "stable",
            "backend": "cuda", "arch": arch, "url": "", "pageUrl": "",
            "artifactType": "安定化パッチ適用済みWindows x64 CUDA完成品ZIP",
            "purpose": "GPU自動検出で対応アーキテクチャが一致した場合に使用する声質安定版",
            "instructions": "配布ZIP内のassetsに同梱されています。GPU世代が一致する場合だけ自動選択します。他のGPU世代は既存セットアップの任意ビルドを選択してください。通常版GPU完成品で代用しません。",
            "bytes": destination.stat().st_size,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
    manifest["assets"] = [a for a in manifest["assets"]
                          if not (a.get("role") == "stable" and a.get("backend") == "cuda" and a.get("arch") == arch)] + [spec]
    (ROOT / "distribution.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8", newline="\n")
    print("Prepared GPU-only asset (runtime verification is still required): " + str(destination))
    sys.exit(0)
if not args.backend_dir or not args.signed_firefox:
    parser.error("CPU/XPI packaging requires --backend-dir and --signed-firefox; GPU-only packaging uses --gpu-backend-dir")
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
