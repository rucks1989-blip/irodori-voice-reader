"""Extract a previously hash-verified ZIP, with traversal/link protection.

Windows extended paths avoid PowerShell 5.1 Expand-Archive's MAX_PATH failure
on the pinned audio.cpp source tree. Never overwrite an existing source tree.
"""
import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile


def extract(archive, destination, strip_root=""):
    destination = Path(destination).resolve()
    root = str(destination)
    if os.name == "nt" and not root.startswith("\\\\?\\"):
        root = "\\\\?\\" + root
    root = Path(root)
    with zipfile.ZipFile(archive) as source:
        members = []
        for entry in source.infolist():
            name = entry.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or any(":" in p for p in parts):
                raise ValueError("Unsafe ZIP member path")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("ZIP symlinks are not accepted")
            if strip_root:
                if not parts or parts[0] != strip_root:
                    raise ValueError("ZIP root does not match the pinned commit")
                parts = parts[1:]
            if parts:
                members.append((entry, root.joinpath(*parts)))
        # Validate the complete central directory before writing anything.
        for entry, target in members:
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
    print(f"Extracted {len(members)} verified ZIP entries")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--strip-root", default="")
    args = parser.parse_args()
    extract(args.archive, args.destination, args.strip_root)
