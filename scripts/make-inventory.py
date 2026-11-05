"""Maintainer-only: generate a precise inventory before signing/publishing."""
from pathlib import Path
import hashlib
import json
from privacy_scan import ROOT, allowed

files = []
# User-supplied standalone prototype, reviewed but not adopted as an installer
# entry point. Preserve it locally without silently publishing it in releases.
excluded_prototypes = {
    "download-irodori.ps1", "README.txt", "ダウンロード.bat",
    # Existing local maintainer work is preserved, but installation never
    # needs signing credentials or a second, unused maintenance implementation.
    "Firefox拡張を署名する.bat", "scripts/sign-firefox.py",
    "scripts/sign_distribution_firefox.ps1", "scripts/stable-maintenance.py",
}
for path in sorted(ROOT.rglob("*")):
    if not path.is_file():
        continue
    name = path.relative_to(ROOT).as_posix()
    if name == "package-files.json" or name in excluded_prototypes or not allowed(name):
        continue
    files.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(ROOT / "package-files.json").write_text(json.dumps({"files": files}, indent=2) + "\n", encoding="utf8", newline="\n")
print(f"Inventory: {len(files)} explicit files")
