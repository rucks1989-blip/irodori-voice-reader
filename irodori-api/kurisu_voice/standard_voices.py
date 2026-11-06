"""Standard-mode voice sources. Never rewrite registrations or original files."""
import json
from pathlib import Path

from .voices import VoiceRegistry, _slug


class StandardVoiceRegistry(VoiceRegistry):
    def __init__(self, root, config_path=None, sources_path=None):
        self.sources_path = Path(sources_path or Path(root) / "config" / "voice_sources.json")
        self.source_errors = []
        self.reference_cache_dir = None
        super().__init__(root, config_path)

    def _add(self, voice_id, name, kind, path, source="auto"):
        resolved = self._resolve(path)
        key = _slug(voice_id or resolved.stem)
        if source == "config" and not resolved.is_file():
            # Keep an explicit but broken registration visible. Otherwise an
            # auto-discovered file with the same ID silently replaces it.
            self._voices.setdefault(key, {
                "id": key, "name": str(name or key), "type": kind,
                "reference": str(path).replace("\\", "/"), "path": str(resolved), "source": source,
            })
            return
        super()._add(voice_id, name, kind, resolved, source)

    def reload(self):
        self.source_errors = []
        self.reference_cache_dir = None
        super().reload()
        try:
            sources = json.loads(self.sources_path.read_text(encoding="utf-8-sig"))
            directories = sources.get("speaker_embedding_dirs", [])
            if not isinstance(directories, list) or any(not isinstance(p, str) or not p.strip() for p in directories):
                raise ValueError("speaker_embedding_dirsはフォルダーのパスの配列で指定してください。")
            cache = sources.get("reference_cache_dir")
            if cache is not None:
                if not isinstance(cache, str) or not cache.strip():
                    raise ValueError("reference_cache_dirは書き込み可能な英数字のパスで指定してください。")
                self.reference_cache_dir = self._resolve(cache)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.source_errors.append(f"Speaker Embeddingの保存先設定が正しくありません: {self.sources_path}: {exc}")
            return
        seen = set()
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}
        disabled_values = raw.get("disabled_auto_voices", []) if isinstance(raw, dict) else []
        disabled = {_slug(v) for v in disabled_values} if isinstance(disabled_values, list) else set()
        for value in directories:
            directory = self._resolve(value)
            if directory in seen:
                continue
            seen.add(directory)
            if not directory.is_dir():
                self.source_errors.append(f"Speaker Embeddingの保存先フォルダーが見つかりません: {directory}")
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file() and path.name.casefold().endswith(".speaker.safetensors"):
                    voice_id = path.name[:-len(".speaker.safetensors")]
                    # Respect the existing per-installation disable list.
                    if _slug(voice_id) not in disabled:
                        self._add(voice_id, voice_id, "speaker_embedding", path)

    def list(self):
        values = super().list()
        if self.source_errors:
            raise ValueError("\n".join(self.source_errors))
        return [{**item, "fileExists": Path(self._voices[item["id"]]["path"]).is_file()} for item in values]

    def get(self, voice_id):
        self.reload()
        key = _slug(voice_id)
        if key not in self._voices:
            if self.source_errors:
                raise ValueError("\n".join(self.source_errors))
            raise KeyError(f"voiceが見つかりません: {key}")
        voice = self._voices[key]
        if not Path(voice["path"]).is_file():
            kind = "Speaker Embedding" if voice["type"] == "speaker_embedding" else "参照WAV"
            raise ValueError(f"Voice '{key}' の{kind}が見つかりません: {voice['path']}")
        return dict(voice)
