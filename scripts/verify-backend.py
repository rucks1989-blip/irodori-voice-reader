"""Verify candidate kernels and parsed stable options, not perceived audio quality."""
import json
from pathlib import Path
import re
import sys
import urllib.request

trace = Path(sys.argv[2])
for stable, model in enumerate(["irodori-500m-v3-q8", "irodori-500m-v3-q8-voice-stable-speaker"]):
    offset = trace.stat().st_size if trace.exists() else 0
    options = {"no_ref": True, "speaker_attention_bias": 0.75 if stable else 0,
               "speaker_rescue_enabled": bool(stable), "audio_quality_rescue_enabled": bool(stable)}
    data = json.dumps({"model": model, "input": "確認。", "response_format": "wav", "options": options}).encode("utf8")
    request = urllib.request.Request(sys.argv[1]+"/v1/audio/speech", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        wav = response.read()
    if wav[:4] != b"RIFF" or len(wav) <= 44:
        raise ValueError("Invalid WAV")
    effective = {}
    text = trace.read_bytes()[offset:].decode("utf8", errors="replace")
    for key, value in re.findall(r"\.(speaker_attention_bias|speaker_rescue_enabled|audio_quality_rescue_enabled)\s+([^\s]+)", text):
        try:
            effective[key] = float(value) if value not in {"true", "false"} else int(value == "true")
        except ValueError:
            pass
    if any(effective.get(key) != value for key, value in options.items() if key != "no_ref"):
        raise ValueError("This runtime did not confirm stable processing options; ordinary WAV generation alone is insufficient")
print("Both model routes generated WAV; native trace confirmed normal options OFF and stable options ON. No audio quality evaluation performed.")
