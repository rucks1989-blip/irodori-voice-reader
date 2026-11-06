import hashlib
import shutil
import tempfile
from pathlib import Path

import requests

from .base import TTSBackend


class AudioCppBackend(TTSBackend):
    def __init__(self, url, model, num_steps=40, seed=1234, timeout=600, request_options=None):
        self.url = str(url).rstrip("/")
        self.model = model
        self.num_steps = int(num_steps)
        self.seed = int(seed)
        self.timeout = timeout
        self.request_options = dict(request_options or {})

    def health(self):
        base = self.url.split("/v1/", 1)[0]
        response = requests.get(base + "/health", timeout=3)
        return response.ok

    def _safe_voice_path(self, voice_ref):
        path = Path(voice_ref).resolve()
        str(path).encode("ascii")
        return path

    def _voice_path(self, voice_ref):
        path = Path(voice_ref).resolve()
        if not path.is_file():
            raise RuntimeError(f"参照WAVが見つかりません: {voice_ref}")
        try:
            return self._safe_voice_path(path)
        except UnicodeEncodeError:
            stat = path.stat()
            digest = hashlib.sha1(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")).hexdigest()[:16]
            cache = Path(tempfile.gettempdir()) / "kurisu_speaker_voice_cache"
            cache.mkdir(parents=True, exist_ok=True)
            suffix = ".speaker.safetensors" if path.name.lower().endswith(".speaker.safetensors") else ".wav"
            target = cache / f"voice_{digest}{suffix}"
            if not target.is_file() or target.stat().st_size != stat.st_size:
                shutil.copy2(path, target)
            return target

    def synthesize_chunk(self, text, voice_ref="", options=None):
        options = options or {}
        speaker_embedding_path = str(options.get("speaker_embedding_path") or "")
        if voice_ref and speaker_embedding_path:
            raise RuntimeError("reference WAV and speaker embedding cannot be used together")
        backend_options = dict(self.request_options)
        backend_options.update({
            # speaker_embedding_path is a reference condition by itself. Sending
            # no_ref=true makes audio.cpp reject the request. The top-level
            # embedding field below is for the older voice-stable engine.
            "no_ref": not bool(voice_ref) and not bool(speaker_embedding_path),
            "text_chunk_size": str(options.get("text_chunk_size", 250)),
            "text_chunk_mode": str(options.get("text_chunk_mode", "japanese")),
        })
        payload = {
            "model": self.model,
            "input": str(text),
            "language": "ja",
            "seed": int(options.get("seed", self.seed)),
            "num_inference_steps": int(options.get("num_steps", self.num_steps)),
            "response_format": "wav",
            "options": backend_options,
        }
        if voice_ref:
            payload["voice_ref"] = str(self._voice_path(voice_ref))
        if speaker_embedding_path:
            embedding = self._voice_path(speaker_embedding_path)
            valid_name = embedding.name == "speaker.safetensors" or embedding.name.endswith(".speaker.safetensors")
            if not embedding.is_file() or not valid_name:
                raise RuntimeError(f"speaker.safetensorsが見つかりません: {speaker_embedding_path}")
            # The standard audio.cpp v0.9.3 server reads Irodori-TTS options
            # from the request's "options" object. The top-level field is kept
            # for the older patched voice-stable engine, whose server maps it
            # into options. Without the options entry, the standard engine
            # ignores the embedding and silently generates its default voice.
            backend_options["speaker_embedding_path"] = str(embedding)
            payload["speaker_embedding_path"] = str(embedding)
        response = requests.post(self.url, json=payload, timeout=self.timeout)
        if not response.ok:
            raise RuntimeError(f"audio.cpp error {response.status_code}: {response.text[:500]}")
        if len(response.content) < 44 or response.content[:4] != b"RIFF":
            raise RuntimeError("audio.cppから正常なWAVが返りませんでした。")
        return response.content

    def capabilities(self):
        return {
            "backend": "audiocpp",
            "supports_reference_wav": True,
            "supports_speaker_embedding": True,
            "supports_plain_safetensors": False,
            "max_text_chars": 250,
        }
