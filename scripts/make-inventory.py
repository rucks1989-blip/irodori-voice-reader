"""Maintainer-only: generate a precise inventory before signing/publishing."""
from pathlib import Path
import hashlib
import json
from privacy_scan import ROOT, allowed

files = []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file():
        continue
    name = path.relative_to(ROOT).as_posix()
    if name == "package-files.json" or not allowed(name):
        continue
    files.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(ROOT / "package-files.json").write_text(json.dumps({"files": files}, indent=2) + "\n", encoding="utf8")
print(f"Inventory: {len(files)} explicit files")
