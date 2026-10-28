#!/usr/bin/env python3
"""Apply the proven Irodori speaker.safetensors direct-read changes to audio.cpp."""
from pathlib import Path
import argparse

MARKERS = (
    "irodori_tts.speaker_embedding.direct",
    "irodori_tts.reference.wav_encode",
    "speaker_embedding_path",
)

def replace_once(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"expected source anchor not found: {path}\n{old[:100]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.source.resolve()
    session = root / "src/models/irodori_tts/session.cpp"
    required = [session, root / "include/engine/models/irodori_tts/types.h",
                root / "app/server/runtime.cpp", root / "app/cli/request.cpp"]
    if args.check:
        missing = [str(p) for p in required if not p.exists()]
        if missing: raise RuntimeError("missing audio.cpp files: " + ", ".join(missing))
        session_text = session.read_text(encoding="utf-8")
        combined = "\n".join(p.read_text(encoding="utf-8") for p in required)
        if not all(marker in combined for marker in MARKERS) or "load_speaker_embedding" not in session_text:
            raise RuntimeError("speaker embedding patch is incomplete")
        print("speaker embedding patch: present")
        return

    replace_once(root / "include/engine/models/irodori_tts/types.h",
        "  bool no_ref = true;\n  runtime::AudioBuffer reference_audio;",
        "  bool no_ref = true;\n  std::string speaker_embedding_path;\n  runtime::AudioBuffer reference_audio;")
    replace_once(session, '#include "engine/framework/debug/profiler.h"',
        '#include "engine/framework/debug/profiler.h"\n#include "engine/framework/debug/trace.h"\n#include "engine/framework/io/json.h"')
    replace_once(session, "#include <algorithm>\n", "#include <algorithm>\n#include <array>\n")
    replace_once(session, "#include <vector>\n", "#include <vector>\n\n#define WIN32_LEAN_AND_MEAN\n#define NOMINMAX\n#include <windows.h>\n")
    loader = (Path(__file__).parent / "speaker_embedding_loader.inc").read_text(encoding="utf-8")
    replace_once(session, "void validate_weight_storage(", loader + "\n\nvoid validate_weight_storage(")
    replace_once(session,
        "  bool reference_cache_hit = false;\n  if (!first_request.no_ref) {",
        "  bool reference_cache_hit = false;\n  bool direct_speaker_embedding = false;\n"
        "  if (!first_request.speaker_embedding_path.empty()) {\n"
        "    speaker = load_speaker_embedding(first_request.speaker_embedding_path, assets_->config);\n"
        "    direct_speaker_embedding = true;\n"
        "    debug::trace_log_scalar(\"irodori_tts.reference.path_mode\", std::string(\"speaker_embedding\"));\n"
        "  } else if (!first_request.no_ref) {")
    replace_once(session,
        '  debug::trace_log_scalar("irodori_tts.reference.used", !first_request.no_ref);',
        '  debug::trace_log_scalar("irodori_tts.reference.used",\n                          !first_request.no_ref || direct_speaker_embedding);\n'
        '  debug::trace_log_scalar("irodori_tts.reference.wav_encode",\n                          !direct_speaker_embedding && !first_request.no_ref);')
    replace_once(session,
        '  if (request.voice.has_value() && request.voice->speaker.has_value() &&',
        '  if (const auto value = runtime::find_option(request.options, {"speaker_embedding_path"})) {\n'
        '    out.speaker_embedding_path = *value;\n'
        '    if (out.speaker_embedding_path.empty()) throw std::runtime_error("speaker_embedding_path must not be empty");\n'
        '    out.no_ref = false;\n'
        '  }\n'
        '  if (request.voice.has_value() && request.voice->speaker.has_value() &&')
    runtime = root / "app/server/runtime.cpp"
    runtime_text = runtime.read_text(encoding="utf-8")
    anchor = '    add_option_from_json(request.options, body, "num_inference_steps", "num_inference_steps");'
    replace_once(runtime, anchor, anchor + '\n    add_option_from_json(request.options, body, "speaker_embedding_path", "speaker_embedding_path");')
    replace_once(root / "app/cli/request.cpp",
        "    request.options = json_options_map(value.find(\"options\"));",
        "    request.options = json_options_map(value.find(\"options\"));\n"
        "    if (const auto path = json_optional_string(value, \"speaker_embedding_path\")) {\n"
        "        set_option(request.options, \"speaker_embedding_path\", *path);\n    }")
    print("speaker embedding patch applied")

if __name__ == "__main__":
    main()
