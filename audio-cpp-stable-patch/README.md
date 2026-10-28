# Stable rescue patch set

This patch set reconstructs the verified voice-stable source from audio.cpp
commit `e12fc743ccb294753dcdced0be7778c76a178f95`.

`scripts/reconstruct_voice_stable_source.ps1` applies `stable-rescue.patch`,
copies `added-files/`, then applies and verifies the speaker-direct patch.
No models, WAVs, embeddings, binaries, logs, or local configuration are stored.

## speaker_embedding_dump tool

`speaker-embedding-dump.patch` adds the `speaker_embedding_dump` CMake target
(CMakeLists.txt), and `added-files/tests/irodori_tts/speaker_embedding_dump.cpp`
provides the tool itself. Both are applied by the reconstruction script above.

### Purpose

Converts a reference WAV into a `speaker.safetensors` speaker embedding that is
compatible with `load_speaker_embedding` (validated shape `[16, speaker_dim]`,
F32). The voice-stable API accepts this file through the `speaker_embedding_path`
field of a `/tts` request, replacing the built-in speaker reference.

### Build

Requires the MSVC + CUDA toolchain used for the Windows CUDA build
(vcvars64.bat, cmake, ninja, CUDA 12.x). From the reconstructed source root:

```powershell
# in a shell with vcvars64.bat loaded (sets INCLUDE/LIB)
cmake --build build\windows-cuda-release --target speaker_embedding_dump
```

The binary is produced at `build\windows-cuda-release\bin\speaker_embedding_dump.exe`.
At runtime, the CUDA `Library\bin` directory must be on `PATH` (otherwise the
process fails with 0xC0000135, DLL not found).

### Usage

```text
speaker_embedding_dump.exe --model <irodori-tts-500m-v3-q8_0.gguf> --wav <ref.wav> --out <out.speaker.safetensors> [--tokens N]
```

- `--wav`: reference audio (32 kHz mono WAV; other rates are resampled).
- `--out`: destination safetensors file (shape `[16, speaker_dim]`).
- `--tokens N`: compress a variable-length speaker condition to exactly N time
  tokens. The first token (the masked-mean summary of the whole reference) is
  always kept; the remaining time tokens are evenly subsampled with indices
  `round(i*(M-1)/(N-1))` for `i = 0..N-1`. Default 16.

### Notes

- Without `--tokens`, the output token count equals the reference length
  (e.g. 0.60 s -> 16 tokens, 0.5 s -> 14 tokens, 4.19 s -> 128 tokens).
  Any count other than 16 will be rejected by `load_speaker_embedding`, so use
  `--tokens 16` for non-0.60 s references.
- A 60 s reference encode requires ~6.3 GB VRAM and fails on a GTX 1070 8 GB;
  multi-material references around 6 s are safe.