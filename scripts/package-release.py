"""Package source and bundled assets; pinned public assets can download during setup."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile
from privacy_scan import ROOT, main as privacy_check

if privacy_check():
    sys.exit("Refusing to package unverified source")
manifest = json.loads((ROOT / "distribution.json").read_text(encoding="utf8"))
inventory = json.loads((ROOT / "package-files.json").read_text(encoding="utf8"))
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
assets = []
for asset in manifest["assets"]:
    path = dist / asset["name"]
    if not path.is_file() and asset.get("url"):
        continue  # setup downloads this pinned upstream artifact and dependencies
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
        sys.exit(f"Missing/mismatched release asset: {asset['name']}")
    assets.append(path)
destination = dist / "IrodoriVoiceReader-Windows-test.zip"
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for entry in inventory["files"]:
        archive.write(ROOT / entry["path"], "IrodoriVoiceReader/" + entry["path"])
    archive.write(ROOT / "package-files.json", "IrodoriVoiceReader/package-files.json")
    for asset in assets:
        archive.write(asset, "IrodoriVoiceReader/assets/" + asset.name)
hashes = []
for path in [destination, *assets]:
    hashes.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
(dist / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")
print(destination)
