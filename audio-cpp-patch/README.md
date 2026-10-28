# audio.cpp speaker.safetensors 直読パッチ

GTX 1070実機で生成成功した差分を、audio.cppソースへ最小限に再適用します。
`.speaker.safetensors` 内の `speaker_embedding`（F32、形状 `[16, speaker_dim]`）を
WAVエンコードせず直接条件として利用します。通常の参照WAV経路は維持されます。

## 適用とビルド

Visual Studio Build Tools（C++）、Windows SDK、CMake、Ninja、CUDA Toolkitを用意します。
GTX 1070はPascal compute capability 6.1です。

```powershell
python .\audio-cpp-patch\apply_speaker_embedding_patch.py C:\src\audio.cpp
cd C:\src\audio.cpp
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 `
  -Preset windows-cuda-release -Target audiocpp_server -CudaArchitectures 61
python D:\path\to\irodori-voice-studio\audio-cpp-patch\apply_speaker_embedding_patch.py . --check
```

生成された `build\windows-cuda-release\bin` のDLL一式と
`audiocpp_server.exe` を `irodori-api\audio-cpp\bin` へ配置します。

リクエスト最上位の `speaker_embedding_path` がサーバーのoptionsへ渡ります。
トレースで以下を確認できます。

```text
irodori_tts.speaker_embedding.direct=1
irodori_tts.reference.path_mode=speaker_embedding
irodori_tts.reference.wav_encode=0
```

通常 `.safetensors` はVoiceメタデータとして登録できますが、この直読経路の対象は
`.speaker.safetensors` だけです。
