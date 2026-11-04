"""Maintainer: pin expanded payload hashes from already verified upstream ZIPs.

Usage: --archive "manifest-name.zip=local-path.zip" (repeat for each ZIP).
No download or extraction, and no binary payloads are written to the repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="append", required=True)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "distribution.json").read_text(encoding="utf8"))
    specs = [manifest["audioCppSource"]]
    for asset in manifest["assets"]:
        if asset.get("kind") == "backend":
            specs.append(asset)
            specs.extend(asset.get("dependencies", []))
    paths = dict(item.split("=", 1) for item in args.archive)
    archives = {}
    for spec in specs:
        path = Path(paths[spec["name"]])
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != spec["sha256"] or path.stat().st_size != spec["bytes"]:
            raise ValueError(f"Original ZIP failed pinned verification: {spec['name']}")
        is_source = spec["name"] == manifest["audioCppSource"]["name"]
        files = {}
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not is_source and not (Path(name).name.lower() == "audiocpp_server.exe" or name.lower().endswith(".dll")):
                    continue
                with archive.open(info) as stream:
                    file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                files[name] = {"bytes": info.file_size, "sha256": file_hash}
        archives[digest] = {"name": spec["name"],
                            "rootPrefix": "audio.cpp-" + manifest["audioCppCommit"] + "/" if is_source else "",
                            "files": dict(sorted(files.items()))}
        print(f"Pinned {spec['name']}: {len(files)} payload files")
    output = ROOT / "scripts/download-catalog.json"
    output.write_text(json.dumps({"schema": 1, "archives": archives}, indent=2) + "\n", encoding="utf8", newline="\n")


if __name__ == "__main__":
    main()
