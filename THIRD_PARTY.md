# Third-party software and data

Laolao is MIT licensed (see `LICENSE`). It depends on, and at setup time
downloads, work by others under their own terms. Nothing below is bundled in
this repository; it is fetched onto your machine the first time you run setup.

## Speech models

| What | Where it comes from | License |
|---|---|---|
| Whisper weights (`large-v3-turbo`, `base`, …) | OpenAI, via HuggingFace mirrors (`onnx-community/*`, `mlx-community/*`, Systran `faster-whisper-*`) | MIT |
| Precompiled Whisper for the Hexagon NPU (Windows on ARM64) | [Qualcomm AI Hub Models](https://github.com/quic/ai-hub-models), `whisper_large_v3_turbo` / `whisper_base` | BSD-3-Clause, © Qualcomm Technologies, Inc. |
| Silero VAD (ONNX) | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | MIT |

## Runtime libraries

| Library | Role | License |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / CTranslate2 | Whisper on CPU and CUDA | MIT |
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | Whisper on Apple Silicon | MIT |
| [onnxruntime](https://onnxruntime.ai/) and `onnxruntime-qnn` | Whisper on Windows ARM64, CPU and Hexagon NPU | MIT |
| [tokenizers](https://github.com/huggingface/tokenizers) | Whisper vocabulary | Apache-2.0 |
| [OpenCC](https://github.com/BYVoid/OpenCC) (`opencc-python-reimplemented`) | Traditional → Simplified Chinese | Apache-2.0 |
| [sounddevice](https://python-sounddevice.readthedocs.io/) / PortAudio | Microphone capture | MIT |
| [websockets](https://websockets.readthedocs.io/) | Caption transport | BSD-3-Clause |
| [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) | Virtual camera sink (macOS, Windows x64) | GPL-2.0 — used as an unmodified, separately installed Python package; see note |
| [Electron](https://www.electronjs.org/) | Desktop shell | MIT |
| [OBS Studio](https://obsproject.com/) | Provides the virtual camera driver; installed by the user, not bundled | GPL-2.0 |

**Note on GPL components.** OBS Studio is a separate application the user
installs; Laolao talks to its camera driver through the operating system and
ships none of its code. `pyvirtualcam` is installed from PyPI into the user's
own environment at setup time and imported unmodified. Neither is
redistributed in this repository or in a Laolao build.

## Test data

| What | Source | License |
|---|---|---|
| Three Mandarin utterances (`BAC009S0764W0121`–`0123`) used as ground-truth fixtures | AISHELL-1 corpus (OpenSLR SLR33), via the icefall `test_wavs/` samples | Apache-2.0 |

The audio is downloaded by `tests/generate_test_audio.py` and is not stored in
the repository. Full provenance, including checksums and the published
transcript each clip is scored against, lives beside each fixture in
`tests/fixtures/*.source.json`.
