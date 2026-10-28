import os
import tempfile
from pathlib import Path

from .audio_join import combine_wavs
from .models import SynthesisResult
from .planner import build_plan


class KurisuVoice:
    def __init__(self, root, profile, backend):
        self.root = Path(root).resolve()
        self.profile = dict(profile)
        self.backend = backend

    def _split_profile(self, text, split_options=None):
        profile = dict(self.profile)
        split_options = dict(split_options or {})
        if split_options.get("enabled") is False:
            profile["target_chars"] = max(len(str(text)), int(profile.get("target_chars", 250)))
            profile["storyteller_target_chars"] = profile["target_chars"]
            profile["hard_max_chars"] = len(str(text))
        elif split_options:
            character_limit = max(20, min(2000, int(split_options.get("max_chars") or profile.get("target_chars", 250))))
            target_seconds = max(5, min(60, int(split_options.get("target_seconds") or 25)))
            duration_limit = max(20, round(target_seconds * 4.8))
            maximum = min(character_limit, duration_limit)
            profile["target_chars"] = maximum
            profile["storyteller_target_chars"] = maximum
            profile["hard_max_chars"] = character_limit
            profile["preserve_dialogue"] = bool(split_options.get("preserve_dialogue", True))
            profile["keep_character_tags_separate"] = bool(split_options.get("keep_character_tags_separate", True))
        return profile

    def plan(self, text, split_options=None, trace=None):
        return build_plan(text, self._split_profile(text, split_options), self.root, trace=trace)

    def synthesize(self, text, options=None, trace=None):
        options = dict(options or {})
        split_options = options.pop("split_options", None)
        # Keep profile/voice-id defaults, but let the API override the
        # reference for this request without changing existing callers.
        direct_voice_ref = str(options.pop("voice_ref_path", "") or "")
        direct_speaker_embedding = str(options.pop("speaker_embedding_path", "") or "")
        if direct_voice_ref and direct_speaker_embedding:
            raise ValueError("reference WAV and speaker embedding cannot be used together")
        effective_profile = self._split_profile(text, split_options)
        plan = build_plan(text, effective_profile, self.root, trace=trace)
        wav_paths, generated = [], []
        output = None
        try:
            for item in plan:
                if item.get("skip_tts"):
                    continue
                chunk_options = {
                    "text_chunk_size": effective_profile.get("target_chars", 250),
                    "text_chunk_mode": self.profile.get("audio_cpp_text_chunk_mode", "japanese"),
                    **options,
                }
                voice_ref = "" if direct_speaker_embedding else direct_voice_ref or item.get("voice_ref_path") or self.profile.get("default_voice_path") or ""
                if direct_speaker_embedding:
                    chunk_options["speaker_embedding_path"] = direct_speaker_embedding
                if not voice_ref and not direct_speaker_embedding:
                    chunk_options.setdefault(
                        "speaker_embedding_path",
                        self.profile.get("default_speaker_embedding_path") or "",
                    )
                wav = self.backend.synthesize_chunk(
                    item["text"],
                    voice_ref,
                    chunk_options,
                )
                fd, path = tempfile.mkstemp(prefix="kurisu_chunk_", suffix=".wav")
                os.close(fd)
                Path(path).write_bytes(wav)
                wav_paths.append(path)
                generated.append(item)
            if not wav_paths:
                raise RuntimeError("読み上げ対象がありません。")
            output = combine_wavs(wav_paths, [item.get("silence_after_ms", 0) for item in generated], self.profile.get("fade_ms", 0))
            return SynthesisResult(Path(output), generated)
        finally:
            for path in wav_paths:
                if str(path) != str(output):
                    Path(path).unlink(missing_ok=True)
