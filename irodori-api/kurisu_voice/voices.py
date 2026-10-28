import json
import re
from pathlib import Path


def _slug(value):
    value = re.sub(r"\.speaker$", "", str(value or ""), flags=re.I)
    value = re.sub(r"[^0-9A-Za-z_\-\u3040-\u30ff\u3400-\u9fff]+", "-", value).strip("-")
    return value.casefold() or "voice"


class VoiceRegistry:
    def __init__(self, root, config_path=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "config" / "voices.json")
        self._voices = {}
        self.reload()

    def _resolve(self, value):
        path = Path(str(value or "")).expanduser()
        return (self.root / path).resolve() if not path.is_absolute() else path.resolve()

    def _add(self, voice_id, name, kind, path, source="auto"):
        path = self._resolve(path)
        if not path.is_file():
            return
        voice_id = _slug(voice_id or path.stem)
        if voice_id in self._voices:
            return
        reference = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
        self._voices[voice_id] = {
            "id": voice_id,
            "name": str(name or path.stem).replace(".speaker", ""),
            "type": kind,
            "reference": reference.replace("\\", "/"),
            "path": str(path),
            "source": source,
        }

    def reload(self):
        self._voices = {}
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            raw = {}
        disabled_values = raw.get("disabled_auto_voices", []) if isinstance(raw, dict) else []
        if not isinstance(disabled_values, list):
            disabled_values = []
        disabled_auto_voices = {_slug(item) for item in disabled_values if isinstance(item, (str, int)) and str(item).strip()}
        for item in raw.get("voices", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or item.get("enabled", True) is False:
                continue
            if item.get("speaker_embedding"):
                self._add(item.get("id"), item.get("name"), "speaker_embedding", item["speaker_embedding"], "config")
            elif item.get("reference_wav"):
                self._add(item.get("id"), item.get("name"), "reference_wav", item["reference_wav"], "config")
            elif item.get("safetensors"):
                self._add(item.get("id"), item.get("name"), "safetensors", item["safetensors"], "config")
        for folder in (self.root / "voice_refs", self.root / "speaker_voice_refs"):
            if folder.is_dir():
                for path in sorted(folder.rglob("*.wav")):
                    if _slug(path.stem) in disabled_auto_voices:
                        continue
                    self._add(path.stem, path.stem, "reference_wav", path)
        embedding_dir = self.root / "irodori_embed_profiles"
        if embedding_dir.is_dir():
            for path in sorted(embedding_dir.glob("*.speaker.safetensors")):
                voice_id = path.name.removesuffix(".speaker.safetensors")
                if _slug(voice_id) in disabled_auto_voices:
                    continue
                self._add(voice_id, voice_id, "speaker_embedding", path)

    def list(self):
        self.reload()
        return [
            {**{key: value for key, value in item.items() if key != "path"}, "tts_supported": item["type"] != "safetensors"}
            for item in self._voices.values()
        ]

    def get(self, voice_id):
        self.reload()
        voice_id = _slug(voice_id)
        if voice_id not in self._voices:
            raise KeyError(f"voiceが見つかりません: {voice_id}")
        return dict(self._voices[voice_id])
