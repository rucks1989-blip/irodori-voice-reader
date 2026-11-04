"""Installer-owned request worker: cancellable through the installer's Job."""
import json
import sys
import urllib.request
from pathlib import Path


def main():
    base, origin, voice, engine, destination = sys.argv[1:]
    payload = {"text": "こんにちは。音声の動作確認です。", "voice": voice,
               "engine": engine, "speaker_tag_policy": "preserve"}
    request = urllib.request.Request(
        base + "/tts", data=json.dumps(payload).encode("utf8"),
        headers={"Content-Type": "application/json", "Origin": origin})
    with urllib.request.urlopen(request, timeout=1200) as response:
        data = response.read()
    if len(data) <= 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Invalid WAV response")
    Path(destination).write_bytes(data)
    print(engine + ": WAV generated; stable algorithm effectiveness requires separate verification")


if __name__ == "__main__":
    main()
