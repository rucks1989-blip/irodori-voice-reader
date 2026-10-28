import difflib
import json
import re
from pathlib import Path


_CACHE = {}


def _key(value):
    return re.sub(r"[\s\u3000_\-・･.。]+", "", str(value or "")).casefold()


def clear_speaker_cache():
    _CACHE.clear()


def _resolve_path(value, root):
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    return path.resolve()


def resolve_speaker(name, profile, root, default_voice_path=""):
    name = str(name or "").strip()
    aliases = profile.get("speaker_aliases") or {}
    aliases = aliases if isinstance(aliases, dict) else {}
    voice_root = _resolve_path(profile.get("speaker_voice_dir") or "speaker_voice_refs", root)
    cache_key = (str(voice_root), name, json.dumps(aliases, ensure_ascii=False, sort_keys=True))
    if cache_key in _CACHE:
        return dict(_CACHE[cache_key])
    result = {"speaker_name": name, "voice_ref_path": str(default_voice_path or ""), "speaker_match": "default"}
    if aliases.get(name):
        candidate = _resolve_path(aliases[name], root)
        if candidate.is_file():
            result.update(voice_ref_path=str(candidate), speaker_match="alias")
            _CACHE[cache_key] = dict(result)
            return result
    files = sorted(voice_root.rglob("*.wav")) if profile.get("speaker_auto_search", True) and voice_root.is_dir() else []
    wanted = _key(name)
    scored = []
    for path in files:
        stem = _key(path.stem)
        if stem == wanted:
            score, kind = 1.0, "exact"
        elif stem.startswith(wanted) or stem.endswith(wanted) or wanted.startswith(stem) or wanted.endswith(stem):
            score, kind = 0.92, "prefix_suffix"
        elif wanted in stem or stem in wanted:
            score, kind = 0.82, "partial"
        else:
            score, kind = difflib.SequenceMatcher(None, wanted, stem).ratio(), "fuzzy"
        scored.append((score, kind, path))
    scored.sort(key=lambda item: (-item[0], len(item[2].stem), str(item[2])))
    if scored:
        score, kind, path = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        safe = kind != "fuzzy" or (
            profile.get("speaker_fuzzy_search", True)
            and score >= float(profile.get("speaker_fuzzy_threshold") or 0.62)
            and score - second >= 0.08
        )
        if safe:
            result.update(voice_ref_path=str(path.resolve()), speaker_match=kind, speaker_score=round(score, 3))
    fallback = str(profile.get("speaker_fallback") or "default")
    if result["speaker_match"] == "default" and fallback == "skip":
        result["skip_tts"] = True
    elif result["speaker_match"] == "default" and fallback == "error":
        raise RuntimeError(f"話者「{name}」に一致するWAVが見つかりません。")
    _CACHE[cache_key] = dict(result)
    return result


def split_speaker_segments(text, profile, root, default_voice_path=""):
    source = str(text or "")
    if not profile.get("speaker_switch_enabled", True):
        return [(source, {})]
    start = str(profile.get("speaker_tag_start") or "【")
    end = str(profile.get("speaker_tag_end") or "】")
    if not start or not end:
        return [(source, {})]
    matches = list(re.finditer(re.escape(start) + r"(.*?)" + re.escape(end), source))
    if not matches:
        return [(source, {})]
    segments = []
    if matches[0].start() > 0:
        segments.append((source[:matches[0].start()], {}))
    for index, match in enumerate(matches):
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        name = match.group(1).strip()
        meta = resolve_speaker(name, profile, root, default_voice_path) if name else {}
        segments.append((source[match.end():body_end], meta))
    return segments

