"""Prepare an isolated real-setup UI test; never changes the checkout's manifest.

The injected curl fails with exit 35 for certifi and missing test URLs. Python
still makes real, certificate-verified HTTPS requests. Normal artifacts remain
pinned to production hashes. Run the resulting setup.bat through the UI driver.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("test_root", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--cpu-zip", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    target = args.test_root.resolve()
    if target.exists():
        raise ValueError("Choose a fresh test root; existing test/user files will not be reset")
    source = target / "source"
    source.mkdir(parents=True)
    inventory = json.loads((ROOT / "package-files.json").read_text(encoding="utf8"))
    for entry in inventory["files"]:
        path = source / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / entry["path"], path)
    manifest_path = source / "distribution.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    for path, spec in [(args.model, manifest["model"]),
                       (args.cpu_zip, next(a for a in manifest["assets"] if a["name"].endswith("cpu-portable.zip")))]:
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != spec["sha256"]:
                raise ValueError("Test input does not match production hash")
    model = manifest["model"]
    model["url"] = "https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/a70a4ecb70ee38ef43a5aeae9fbefb512778e262/IVR-MISSING-model.gguf"
    model["alternateUrl"] = "https://github.com/0xShug0/audio.cpp/releases/download/v0.9.3/IVR-MISSING-model.gguf"
    cpu = next(a for a in manifest["assets"] if a["name"].endswith("cpu-portable.zip"))
    cpu["url"] = "https://github.com/0xShug0/audio.cpp/releases/download/v0.9.3/IVR-MISSING-cpu.zip"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    for entry in inventory["files"]:
        entry["sha256"] = hashlib.sha256((source / entry["path"]).read_bytes()).hexdigest()
    (source / "package-files.json").write_text(json.dumps(inventory, indent=2), encoding="utf8")
    bad = target / "wrong-folder"
    bad.mkdir()
    (bad / model["name"]).write_bytes(b"wrong model; must be rejected")
    # A different volume from the D: test installation exercises real cross-
    # drive folder import without copying another gigabyte-sized GGUF.
    selected = Path(os.environ["LOCALAPPDATA"]) / ("IrodoriFolderValidation-" + target.name)
    if selected.exists():
        raise ValueError("Expanded-folder test location already exists")
    subprocess.run([sys.executable, str(ROOT / "scripts/extract-archive.py"), str(args.cpu_zip), str(selected)], check=True)
    (selected / "untrusted-extra.exe").write_bytes(b"must never be imported")
    fault = target / "fault-bin"
    fault.mkdir()
    real_curl = shutil.which("curl.exe")
    if not real_curl:
        raise ValueError("Windows curl is required for this controlled fault test")
    cs = r'''using System;using System.Diagnostics;
class FaultCurl {
  static int Main(string[] args) {
    foreach(string arg in args) {
      if(arg.Contains("IVR-MISSING") || arg.Contains("/certifi-")) {
        Console.Error.WriteLine("Controlled test fault: curl: (35) schannel: CRYPT_E_REVOCATION_OFFLINE (0x80092013)");return 35;
      }
    }
    string[] quoted=Array.ConvertAll(args,x=>"\""+x.Replace("\"","\\\"")+"\"");
    var info=new ProcessStartInfo(Environment.GetEnvironmentVariable("IVR_TEST_REAL_CURL"),String.Join(" ",quoted));
    info.UseShellExecute=false;info.CreateNoWindow=true;
    using(var p=Process.Start(info)){p.WaitForExit();return p.ExitCode;}
  }
}'''
    (target / "fault-curl.cs").write_text(cs, encoding="utf8")
    code = "Add-Type -Path '" + str(target / "fault-curl.cs").replace("'", "''") + "' -OutputAssembly '" + str(fault / "curl.exe").replace("'", "''") + "' -OutputType ConsoleApplication"
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", code], check=True)
    settings = {"root":str(target),"source":str(source),"model":str(args.model.resolve()),
                "reference":str(args.reference.resolve()),"cpuFolder":str(selected),"realCurl":real_curl}
    (target / "test-inputs.json").write_text(json.dumps(settings, indent=2), encoding="utf8")
    print(json.dumps(settings))


if __name__ == "__main__":
    main()
