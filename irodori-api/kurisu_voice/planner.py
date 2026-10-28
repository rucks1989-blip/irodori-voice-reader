from pathlib import Path

from .filters import normalize_markers, remove_ignored_texts
from .profiles import default_profile
from .speakers import split_speaker_segments
from .splitter import dialogue_blocks, natural_cut, stable_story_blocks


def build_plan(text, profile=None, root=None, trace=None):
    profile = dict(default_profile(), **(profile or {}))
    root = Path(root or Path.cwd()).resolve()
    target = max(1, int(profile.get("target_chars") or 250))
    storyteller = str(profile.get("speaker_recognition_mode") or "standard") == "storyteller"
    quality_target = max(1, min(target, int(profile.get("storyteller_target_chars") or 120)))
    backtrack = max(0, min(target - 1, int(profile.get("backtrack_chars") or 50)))
    forward = max(0, int(profile.get("forward_chars") or 20))
    normalized = normalize_markers(text)
    source = remove_ignored_texts(normalized, profile.get("ignored_texts")).strip()
    if trace is not None:
        trace.update({
            "input": str(text),
            "normalized_text": normalized,
            "filtered_text": source,
            "internal": {
                "speaker_switch_enabled": bool(profile.get("speaker_switch_enabled", True)),
                "speaker_tag_start": str(profile.get("speaker_tag_start") or ""),
                "speaker_tag_end": str(profile.get("speaker_tag_end") or ""),
                "speaker_recognition_mode": str(profile.get("speaker_recognition_mode") or "standard"),
                "ignored_texts": list(profile.get("ignored_texts") or []),
            },
            "speaker_segments": [],
            "chunks": [],
        })
    if not source:
        return []
    silence = {
        "sentence": int(profile.get("silence_sentence_ms") or 0),
        "exclamation": int(profile.get("silence_sentence_ms") or 0),
        "comma": int(profile.get("silence_hard_ms") or 0),
        "hard": int(profile.get("silence_hard_ms") or 0),
        "dialogue": int(profile.get("silence_dialogue_ms") or 0),
        "paragraph": int(profile.get("silence_paragraph_ms") or 0),
        "speaker": int(profile.get("silence_dialogue_ms") or 0),
        "end": 0,
    }
    plan = []
    for speaker_text, speaker_meta in split_speaker_segments(source, profile, root, profile.get("default_voice_path") or ""):
        if trace is not None:
            trace["speaker_segments"].append({"text": speaker_text, "meta": dict(speaker_meta)})
        structural_blocks = dialogue_blocks(speaker_text.strip(), profile.get("split_adjacent_dialogue", True))
        if storyteller:
            structural_blocks = [
                (piece, reason)
                for block, reason in structural_blocks
                for piece in stable_story_blocks(block, quality_target)
            ]
        for block, structural_reason in structural_blocks:
            remaining = block.strip()
            split_limit = quality_target if storyteller else target
            split_backtrack = min(backtrack, split_limit - 1)
            while len(remaining) > split_limit:
                hard_limit = min(len(remaining), max(split_limit, int(profile.get("hard_max_chars") or split_limit)))
                cut, reason = natural_cut(
                    remaining,
                    split_limit,
                    split_backtrack,
                    profile.get("preserve_dialogue", True),
                    forward,
                    hard_limit,
                )
                piece = remaining[:cut].strip()
                if piece:
                    plan.append({"text": piece, "reason": reason, "length": len(piece), "silence_after_ms": silence[reason], **speaker_meta})
                remaining = remaining[cut:].strip()
            if remaining:
                plan.append({"text": remaining, "reason": structural_reason, "length": len(remaining), "silence_after_ms": silence[structural_reason], **speaker_meta})
    for index in range(len(plan) - 1):
        current = (plan[index].get("speaker_name"), plan[index].get("voice_ref_path"))
        following = (plan[index + 1].get("speaker_name"), plan[index + 1].get("voice_ref_path"))
        if current != following and plan[index]["reason"] == "end":
            plan[index].update(reason="speaker", silence_after_ms=silence["speaker"])
    if plan:
        plan[-1].update(reason="end", silence_after_ms=0)
    if trace is not None:
        trace["chunks"] = [dict(item) for item in plan]
    return plan
