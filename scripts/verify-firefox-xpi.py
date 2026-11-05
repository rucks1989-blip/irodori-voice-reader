"""Check a pinned Mozilla-returned XPI without claiming to replace Firefox trust verification."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def verify(path, sha256, size, version, extension_id="irodori-voice-reader@local"):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if len(sha256) != 64 or digest.lower() != sha256.lower() or path.stat().st_size != size:
        raise ValueError("Firefox XPI does not match the pinned distribution size/SHA-256")
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["version"] != version or manifest["browser_specific_settings"]["gecko"]["id"] != extension_id:
            raise ValueError("Firefox XPI version/extension ID differs from the registered Native Host")
        if not {"META-INF/mozilla.rsa", "META-INF/mozilla.sf", "META-INF/manifest.mf"}.issubset(archive.namelist()):
            raise ValueError("Unsigned XPI: Mozilla signature material is missing")
    return {"integrityVerified": True, "signatureMaterialPresent": True, "firefoxAcceptanceRequired": True,
            "extensionId": extension_id, "version": version, "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--bytes", type=int, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.path, args.sha256, args.bytes, args.version)))
