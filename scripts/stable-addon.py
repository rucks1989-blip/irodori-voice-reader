"""Install routing for a verified dual-profile audio.cpp, without rebuilding setup.

The caller holds stable-add.lock and requires this instance to be stopped.
Only FILES are mutable; existing API profiles, models and voices are read-only.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

FILES = ("irodori-api/config/runtime.json", "irodori-api/config/audio_cpp_server.shared.json",
         "irodori-api/config/capabilities.json", ".ivr-install.json")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def payload(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf8")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".ivr-" + uuid.uuid4().hex)
    temp.write_bytes(data)
    os.replace(temp, path)


def resolve_api_path(api, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (api / path).resolve()


def plan(root, candidate):
    root, candidate = root.resolve(), candidate.resolve()
    candidate.relative_to(root)
    executable = candidate / "audiocpp_server.exe"
    if not executable.is_file():
        raise ValueError("Staged runtime is missing")
    runtime, server, capabilities, marker = [read(root / name) for name in FILES]
    if marker.get("state") != "runtime-ready":
        raise ValueError("Complete the normal installation first")
    api = root / "irodori-api"
    normal = read(api / "config/config.json")
    stable = read(api / "config/config.voice_stable.json")
    if normal["irodori_audiocpp_api_url"] != stable["irodori_audiocpp_api_url"]:
        raise ValueError("Existing profiles use different endpoints; no API settings were changed")
    normal_id, stable_id = [item["irodori_audiocpp_model"] for item in (normal, stable)]
    if normal_id == stable_id:
        raise ValueError("Existing profile IDs must remain distinct")
    model = next((item for item in server["models"] if item["id"] == normal_id), None)
    if model is None or not resolve_api_path(api, model["path"]).is_file():
        raise ValueError("Existing normal model is missing")
    alias = next((item for item in server["models"] if item["id"] == stable_id), None)
    if alias is None:
        alias = copy.deepcopy(model)
        alias["id"] = stable_id
        server["models"].append(alias)
    # Preserve an existing customized stable alias, including its model/options.
    runtime["audio_cpp_shared_executable"] = str(executable)
    runtime["audio_cpp_shared_cuda_bin"] = str(candidate)
    capabilities["voice_stable"] = True
    marker["voiceStable"] = True
    return dict(zip(FILES, (runtime, server, capabilities, marker)))


def rollback(root, transaction):
    state = read(transaction / "transaction.json")
    # Preflight every file before restoring any; independent edits are preserved.
    for record in state["files"]:
        if record["path"] not in FILES:
            raise ValueError("Unexpected transaction path")
        if digest(root / record["path"]) not in (record["before"], record["after"]):
            raise ValueError("Configuration changed independently; recovery stopped: " + record["path"])
    for record in state["files"]:
        write(root / record["path"], (transaction / record["path"]).read_bytes())
    state["state"] = "rolled-back"
    write(transaction / "transaction.json", payload(state))


def recover(root):
    for journal in sorted((root / "installer-state/stable-addon").glob("*/transaction.json")):
        if read(journal)["state"] == "prepared":
            rollback(root, journal.parent)


def apply(root, candidate):
    values = plan(root, candidate)  # Validate everything before writing.
    transaction = root / "installer-state/stable-addon" / uuid.uuid4().hex
    records = []
    for name, value in values.items():
        target = transaction / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
        records.append({"path": name, "before": digest(target),
                        "after": hashlib.sha256(payload(value)).hexdigest()})
    state = {"state": "prepared", "files": records}
    write(transaction / "transaction.json", payload(state))
    try:
        for name, value in values.items():
            record = next(item for item in records if item["path"] == name)
            if digest(root / name) != record["before"]:
                raise ValueError("Configuration changed during installation: " + name)
            # Do not rewrite already identical JSON (including its formatting).
            if read(root / name) != value:
                write(root / name, payload(value))
        state["state"] = "committed"
        write(transaction / "transaction.json", payload(state))
    except BaseException:
        rollback(root, transaction)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("recover", "check", "apply"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "recover":
        recover(root)
    elif args.action == "check":
        plan(root, args.candidate)
    else:
        apply(root, args.candidate)


if __name__ == "__main__":
    main()
