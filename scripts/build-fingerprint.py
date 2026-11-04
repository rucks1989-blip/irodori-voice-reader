"""Content fingerprint of a managed source tree, excluding compiler outputs."""
import hashlib
import os
from pathlib import Path
import sys


def fingerprint(root):
    digest = hashlib.sha256()
    paths = []
    for directory, folders, files in os.walk(root):
        folders[:] = [f for f in folders if f not in {".git", "build", "__pycache__"}]
        paths.extend(Path(directory) / name for name in files)
    for path in sorted(paths):
        relative = path.relative_to(root)
        digest.update(relative.as_posix().encode("utf8") + b"\0")
        with path.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


if __name__ == "__main__":
    print(fingerprint(Path(sys.argv[1])))
