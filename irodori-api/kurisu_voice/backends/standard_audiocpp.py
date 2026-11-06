"""Reference-path handling for the standard engine; stable transport is unchanged."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from .audiocpp import AudioCppBackend


def is_speaker_embedding(path):
    name = Path(path).name.casefold()
    return name == "speaker.safetensors" or name.endswith(".speaker.safetensors")


class StandardAudioCppBackend(AudioCppBackend):
    def __init__(self, *args, reference_root, reference_cache_dir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reference_root = Path(reference_root).resolve()
        self.reference_cache_dir = self._resolve_reference(reference_cache_dir) if reference_cache_dir else None

    def _resolve_reference(self, value):
        path = Path(value).expanduser()
        return (self.reference_root / path).resolve() if not path.is_absolute() else path.resolve()

    def _cache_directory(self):
        if self.reference_cache_dir is not None:
            candidates = [self.reference_cache_dir]
        else:
            candidates = [Path(tempfile.gettempdir()) / "irodori_standard_reference_cache"]
            # Native Host startup deliberately puts TEMP inside the install.
            # When that path is Japanese, use a per-user location instead of
            # copying model/Voice data to a hard-coded drive or public folder.
            if os.environ.get("LOCALAPPDATA"):
                candidates.append(Path(os.environ["LOCALAPPDATA"]) / "IrodoriVoiceReader" / "StandardReferenceCache")
        for cache in candidates:
            try:
                cache = cache.resolve()
                str(cache).encode("ascii")
                cache.mkdir(parents=True, exist_ok=True)
                return cache
            except (UnicodeEncodeError, OSError):
                continue
        raise ValueError("Speaker Embedding用の一時保存先に書き込み可能な英数字のパスが必要です。"
                         "config/voice_sources.jsonのreference_cache_dirに、このPCの専用フォルダーを指定してください。")

    def _voice_path(self, voice_ref):
        path = self._resolve_reference(voice_ref)
        embedding = is_speaker_embedding(path)
        if not path.is_file():
            kind = "Speaker Embedding" if embedding else "参照WAV"
            raise ValueError(f"{kind}が見つかりません: {path}")
        try:
            safe = self._safe_voice_path(path)
        except UnicodeEncodeError:
            safe = None
        if safe is not None and (not embedding or path.name == "speaker.safetensors" or path.name.endswith(".speaker.safetensors")):
            return safe
        else:
            stat = path.stat()
            digest = hashlib.sha256(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf8")).hexdigest()
            cache = self._cache_directory()
            # Managed imports are named speaker.safetensors, not *.speaker.*.
            # Keep their type when making the native engine's ASCII-path copy.
            suffix = ".speaker.safetensors" if embedding else ".wav"
            target = cache / f"voice_{digest}{suffix}"
            if not target.is_file() or target.stat().st_size != stat.st_size:
                fd, staged = tempfile.mkstemp(prefix="embedding-", suffix=".tmp", dir=cache)
                os.close(fd)
                try:
                    shutil.copy2(path, staged)
                    os.replace(staged, target)
                finally:
                    Path(staged).unlink(missing_ok=True)
            str(target).encode("ascii")
            return target

    def synthesize_chunk(self, text, voice_ref="", options=None):
        options = dict(options or {})
        embedding = options.get("speaker_embedding_path")
        if voice_ref and embedding:
            raise ValueError("reference WAV and speaker embedding cannot be used together")
        if embedding:
            path = self._resolve_reference(embedding)
            if not is_speaker_embedding(path):
                raise ValueError(f"Speaker Embeddingには.speaker.safetensorsを指定してください: {path}")
            if not path.is_file():
                raise ValueError(f"Speaker Embeddingが見つかりません: {path}")
            options["speaker_embedding_path"] = str(path)
        if voice_ref:
            path = self._resolve_reference(voice_ref)
            if not path.is_file():
                raise ValueError(f"参照WAVが見つかりません: {path}")
            voice_ref = str(path)
        return super().synthesize_chunk(text, voice_ref, options)
