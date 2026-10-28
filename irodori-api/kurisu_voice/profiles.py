import json
from pathlib import Path


def default_profile():
    return {
        "target_chars": 250,
        "storyteller_target_chars": 120,
        "backtrack_chars": 50,
        "forward_chars": 20,
        "split_adjacent_dialogue": True,
        "silence_sentence_ms": 120,
        "silence_dialogue_ms": 220,
        "silence_paragraph_ms": 300,
        "silence_hard_ms": 80,
        "fade_ms": 5,
        "ignored_texts": [],
        "speaker_switch_enabled": True,
        "speaker_recognition_mode": "standard",
        "speaker_voice_dir": "speaker_voice_refs",
        "speaker_tag_start": "【",
        "speaker_tag_end": "】",
        "speaker_auto_search": True,
        "speaker_fuzzy_search": True,
        "speaker_fuzzy_threshold": 0.62,
        "speaker_fallback": "default",
        "speaker_aliases": {},
        "audio_cpp_text_chunk_mode": "japanese",
    }


def load_profiles(path):
    path = Path(path)
    fallback = {"標準250文字": default_profile()}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        profiles = raw.get("profiles", raw) if isinstance(raw, dict) else {}
        cleaned = {}
        for name, value in profiles.items():
            if str(name).strip() and isinstance(value, dict):
                cleaned[str(name).strip()] = dict(default_profile(), **value)
        return cleaned or fallback
    except Exception:
        return fallback


def save_profiles(path, profiles):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "profiles": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")


def profile_from_config(config):
    config = config or {}
    profile = default_profile()
    mapping = {
        "target_chars": "chunk_target_chars",
        "storyteller_target_chars": "storyteller_target_chars",
        "backtrack_chars": "chunk_backtrack_chars",
        "split_adjacent_dialogue": "chunk_split_adjacent_dialogue",
        "silence_sentence_ms": "chunk_silence_sentence_ms",
        "silence_dialogue_ms": "chunk_silence_dialogue_ms",
        "silence_paragraph_ms": "chunk_silence_paragraph_ms",
        "silence_hard_ms": "chunk_silence_hard_ms",
        "fade_ms": "chunk_fade_ms",
        "ignored_texts": "chunk_ignored_texts",
        "speaker_switch_enabled": "speaker_switch_enabled",
        "speaker_recognition_mode": "speaker_recognition_mode",
        "speaker_voice_dir": "speaker_voice_dir",
        "speaker_tag_start": "speaker_tag_start",
        "speaker_tag_end": "speaker_tag_end",
        "speaker_auto_search": "speaker_auto_search",
        "speaker_fuzzy_search": "speaker_fuzzy_search",
        "speaker_fuzzy_threshold": "speaker_fuzzy_threshold",
        "speaker_fallback": "speaker_fallback",
        "speaker_aliases": "speaker_aliases",
        "audio_cpp_text_chunk_mode": "audio_cpp_text_chunk_mode",
    }
    for key, config_key in mapping.items():
        if config_key in config:
            profile[key] = config[config_key]
    profile["default_voice_path"] = config.get("irodori_audiocpp_voice_ref_path", "")
    return profile
