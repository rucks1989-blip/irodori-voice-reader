import os
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def combine_wavs(wav_paths, silence_ms=80, fade_ms=0):
    paths = [Path(path) for path in wav_paths if path]
    if not paths:
        raise RuntimeError("音声ファイルがありません。")
    gaps = list(silence_ms) if isinstance(silence_ms, (list, tuple)) else None
    if len(paths) == 1 and not fade_ms and (not gaps or not gaps[0]):
        return str(paths[0])
    arrays, sample_rate, channels, subtype = [], None, None, None
    for index, path in enumerate(paths):
        audio, rate = sf.read(str(path), always_2d=True, dtype="float32")
        info = sf.info(str(path))
        if sample_rate is None:
            sample_rate, channels = rate, audio.shape[1]
            subtype = info.subtype if info.subtype in {"PCM_16", "PCM_24", "PCM_32", "FLOAT"} else "PCM_16"
        elif rate != sample_rate or audio.shape[1] != channels:
            raise RuntimeError("TTS chunks have mismatched WAV parameters.")
        fade_frames = min(len(audio) // 2, int(sample_rate * max(0, int(fade_ms or 0)) / 1000))
        if fade_frames:
            audio[:fade_frames] *= np.linspace(0, 1, fade_frames, dtype=np.float32)[:, None]
            audio[-fade_frames:] *= np.linspace(1, 0, fade_frames, dtype=np.float32)[:, None]
        arrays.append(audio)
        if index < len(paths) - 1 or (gaps and index < len(gaps)):
            gap = gaps[index] if gaps and index < len(gaps) else silence_ms
            frames = int(sample_rate * max(0, int(gap or 0)) / 1000)
            if frames:
                arrays.append(np.zeros((frames, channels), dtype=np.float32))
    fd, output = tempfile.mkstemp(prefix="kurisu_voice_", suffix=".wav")
    os.close(fd)
    sf.write(output, np.concatenate(arrays), sample_rate, subtype=subtype)
    return output

