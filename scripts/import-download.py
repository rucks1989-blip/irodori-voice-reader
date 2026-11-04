"""Import arbitrary user-selected folders against the pinned download catalog.

Only catalogued bytes are copied; extra executables/CMake files are never used.
Catalogs are shipped as installer source, not accepted from the selected folder.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid


def filesystem_path(path):
    """Use Windows wide extended paths without changing system policy."""
    path = Path(path).absolute()
    text = str(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        text = "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    return Path(text)


def public_path(path):
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    return text[4:] if text.startswith("\\\\?\\") else text


def safe_file(path, root):
    root = root.absolute()
    path.absolute().relative_to(root)
    for item in [path, *path.parents]:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Links/junctions are not accepted in a selected download folder")
        if item == root:
            break
    return path.is_file()


def matches(path, record, root):
    if not safe_file(path, root) or path.stat().st_size != record["bytes"]:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest().lower() == record["sha256"].lower()


def relative_path(name):
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or name.startswith(("/", "\\")) or ".." in parts or any(":" in p for p in parts):
        raise ValueError("Unsafe path in download catalog")
    return Path(*parts)


def catalog_for(spec, catalog):
    record = catalog.get("archives", {}).get(spec["sha256"].lower())
    if not record or not record.get("files"):
        raise ValueError("No pinned expanded-folder catalog for this ZIP. Select the original ZIP instead.")
    return record


def roots(selected, anchor):
    selected = selected.absolute()
    count = 0
    for directory, folders, files in os.walk(selected, followlinks=False):
        count += 1
        if count > 1500:
            break
        current = Path(directory)
        if len(current.relative_to(selected).parts) >= 5:
            folders[:] = []
        else:
            folders[:] = [n for n in folders if n not in {".git", ".venv", "build", "node_modules"}
                          and not (getattr((current / n).lstat(), "st_file_attributes", 0) & 0x400)
                          and not (current / n).is_symlink()]
        if anchor.name in files:
            root = current
            for _ in anchor.parts[:-1]:
                root = root.parent
            try:
                root.relative_to(selected)
            except ValueError:
                continue
            yield root


def validated_root(selected, record):
    prefix = record.get("rootPrefix", "")
    files = {name[len(prefix):] if prefix and name.startswith(prefix) else name: value
             for name, value in record["files"].items()}
    names = list(files)
    anchor_name = next((n for n in names if n == "audiocpp_server.exe"), None)
    anchor_name = anchor_name or next((n for n in names if n.lower().startswith("cudart") and n.endswith(".dll")), None)
    anchor_name = anchor_name or ("CMakeLists.txt" if "CMakeLists.txt" in files else names[0])
    anchor = relative_path(anchor_name)
    for root in roots(selected, anchor):
        try:
            if not matches(root / anchor, files[anchor_name], root):
                continue
            if all(matches(root / relative_path(name), value, root) for name, value in files.items()):
                return root, files
        except (OSError, ValueError):
            continue
    raise ValueError("The folder does not contain the complete pinned version (size/SHA-256 mismatch or missing files). Select the folder directly containing the downloaded payload, or select its original ZIP. Nothing was installed.")


def copy_checked(source, target, record, root):
    if not safe_file(source, root):
        raise ValueError("Expected regular file")
    if target.exists() and matches(target, record, target.parent):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # Keep staging names short: appending UUIDs to both the archive directory
    # and executable exceeded Windows MAX_PATH in a normal chosen install root.
    temporary = target.with_name(".ivr-" + uuid.uuid4().hex + ".tmp")
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as incoming, temporary.open("xb") as outgoing:
        while data := incoming.read(1024 * 1024):
            outgoing.write(data)
            digest.update(data)
            total += len(data)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if total != record["bytes"] or digest.hexdigest().lower() != record["sha256"].lower():
        raise ValueError("File changed during import; existing files were not replaced")
    if target.exists():
        if matches(target, record, target.parent):
            temporary.unlink()
            return
        target.rename(str(target) + ".rejected-" + uuid.uuid4().hex)
    temporary.rename(target)


def import_selected(spec, catalog, selected, destination):
    selected, destination = filesystem_path(selected), filesystem_path(destination)
    if not selected.is_dir():
        raise ValueError("Select an existing folder")
    if not spec["name"].lower().endswith(".zip"):
        record = {"bytes": spec["bytes"], "sha256": spec["sha256"]}
        for root in roots(selected, Path(spec["name"])):
            candidate = root / spec["name"]
            if matches(candidate, record, root):
                copy_checked(candidate, destination, record, root)
                return destination
        raise ValueError("The required filename/size/SHA-256 was not found. You can select the single GGUF/wheel file directly instead.")
    record = catalog_for(spec, catalog)
    root, files = validated_root(selected, record)
    target = Path(str(destination) + ".verified-folder")
    if target.exists():
        try:
            validated_root(target, record)
            return target
        except (OSError, ValueError):
            pass
    staging = target.parent / (".ivr-import-" + uuid.uuid4().hex)
    prefix = relative_path(record["rootPrefix"]) if record.get("rootPrefix") else Path()
    for name, value in files.items():
        copy_checked(root / relative_path(name), staging / prefix / relative_path(name), value, root)
    if target.exists():
        target.rename(str(target) + ".rejected-" + uuid.uuid4().hex)
    staging.rename(target)
    return target


def place(spec, catalog, selected, destination, strip_root):
    selected, destination = filesystem_path(selected), filesystem_path(destination)
    record = catalog_for(spec, catalog)
    root, files = validated_root(selected, record)
    # Import cache has the archive's canonical root; consumers can strip it.
    prefix = record.get("rootPrefix", "").rstrip("/")
    if strip_root and strip_root != prefix:
        raise ValueError("Requested source root differs from pinned catalog")
    output_prefix = Path() if strip_root or not prefix else relative_path(prefix)
    for name, value in files.items():
        copy_checked(root / relative_path(name), destination / output_prefix / relative_path(name), value, root)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--place", action="store_true")
    parser.add_argument("--strip-root", default="")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    result = (place(spec, catalog, args.selected, args.destination, args.strip_root) if args.place
              else import_selected(spec, catalog, args.selected, args.destination))
    filesystem_path(args.result).write_text(json.dumps({"path": public_path(result)}), encoding="utf8")
    print("Selected folder verified against pinned per-file SHA-256; payload copied safely")


if __name__ == "__main__":
    main()
