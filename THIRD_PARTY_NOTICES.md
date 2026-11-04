# Third-party notices

Irodori Voice Reader includes browser extension, local API, and Native Messaging
Host source derived from the author's Irodori Browser Audio / irodori voice studio
projects. The MIT notice is retained in LICENSE. New installer code is MIT.

External components are not relicensed by this repository:

- audio.cpp: https://github.com/0xShug0/audio.cpp — Apache-2.0. Pinned upstream
  commit: `e12fc743ccb294753dcdced0be7778c76a178f95`. Changes are supplied as the
  `audio-cpp-stable-patch` and `audio-cpp-patch` directories. A backend release
  contains these notices and the upstream LICENSE. Build metadata identifies the
  upstream and patch hashes. The selected CPU baseline preset still requires
  SSE4.2/BMI2 as enabled by this pinned upstream; it is not universal x86 support.
  `apply_shared_session_patch.py` shares an identical normal/stable session so
  the two profile IDs do not allocate two sets of model weights.
- Irodori-TTS-500M-v3: https://huggingface.co/Aratako/Irodori-TTS-500M-v3 — MIT
  as declared by the model publisher. The model card includes restrictions on
  unauthorized impersonation, fraud and misinformation. Read the upstream card.
- GGUF conversion: https://huggingface.co/audio-cpp/audio.cpp-gguf . The setup
  downloads the pinned revision and verifies the complete SHA-256; model files
  are not committed or attached to this repository's releases.
- Python: PSF license. Python is obtained from its official package via winget.
- NumPy, Requests, SoundFile and their dependencies retain their licenses.
  Dependencies are installed into a local virtual environment from PyPI.
- Microsoft Visual Studio Build Tools, Windows SDK and C++ runtime: Microsoft
  terms. Optional build prerequisite installation uses Microsoft's winget
  package. No license keys or compiler installations are included here.
- NVIDIA CUDA Toolkit and runtime: NVIDIA CUDA EULA. GPU builds install or use
  an appropriate Toolkit with consent. This first release distributes only a
  CPU backend binary; no CUDA DLLs are redistributed in its release assets.
- Git for Windows: its bundled license terms. Optional prerequisite for source
  building, acquired via winget after consent.

No reference voices, speaker embeddings, personal training weights, generated
audio, browser profiles, or external chat contents are distributed. Use only
reference audio you have permission to use.

## GPU packaging review (integration branch)

No new GPU binary or CUDA DLL is committed or published by this branch.
Before packaging a finished GPU backend, identify the exact imported DLL
versions and retain the corresponding license documents and notices.
NVIDIA identifies redistributable components in the
[CUDA EULA supplement](https://docs.nvidia.com/cuda/eula/index.html).
Microsoft requires comparing dependencies with the applicable REDIST list;
see [Determine which DLLs to redistribute](https://learn.microsoft.com/en-us/cpp/windows/determining-which-dlls-to-redistribute?view=msvc-170)
and [Visual Studio license terms](https://visualstudio.microsoft.com/license-terms/).
Possession of an installed DLL alone is not a completed redistribution review.
Use the official VC++ Redistributable package where appropriate rather than
assuming every DLL copied from an existing application is distributable.
