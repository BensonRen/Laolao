# North Star — Laolao feature parity on Qualcomm Snapdragon (Windows ARM64)

## Goal

A non-technical user on a **Snapdragon X2 Elite / Windows 11 ARM64** machine can launch
Laolao and have their speech appear as live Chinese captions composited over their webcam,
selectable as a camera inside WeChat / Zoom / FaceTime — **fully offline, no cloud**.

Same promise the README makes for Apple Silicon and x86-64 Windows, now on Qualcomm.

## Target machine (verified 2026-08-09)

| Property | Value |
|---|---|
| CPU | Snapdragon(R) X2 Elite — X2E88100 — Qualcomm Oryon |
| Arch | ARM64 (`PROCESSOR_ARCHITECTURE=ARM64`) |
| OS | Windows 11 Home, build 10.0.28000 |
| Python | 3.11.9 **native ARM64** (`...\Python311-arm64\python.exe`) |
| onnxruntime | 1.24.4 (`onnxruntime-qnn`), providers: **QNN**, Azure, CPU |
| Node.js | ❌ not installed |
| OBS Studio | ❌ not installed |
| C++ toolchain | ❌ no cmake, no MSVC, no vswhere, no winget |

## Blockers that define this port

| Dependency | win-arm64 | Consequence |
|---|---|---|
| `ctranslate2` | ❌ no distribution | `faster-whisper` — the repo's only Windows backend — cannot install |
| `pyvirtualcam` | ❌ no wheel | virtual-camera sink dead |
| `torch` | ❌ no distribution | `openai-whisper` / `transformers` routes dead |
| `sherpa-onnx` | ❌ no wheel | — |
| `pywhispercpp` | ❌ no wheel | source build needs a compiler we don't have |
| `onnxruntime` / `-qnn` / `-genai` | ✅ wheels | **the viable STT lane** |
| `silero-vad` | ✅ wheel | VAD survives |
| numpy, sounddevice, websockets, opencc, Pillow | ✅ | fine |

whisper.cpp upstream ships **no** Windows-ARM64 release binary (Win32/x64 only).
OBS Studio 32.2.1 **does** ship `OBS-Studio-32.2.1-Windows-arm64.zip` (native ARM64).

## Definition of Done — end-to-end acceptance criteria

Each must be **demonstrated by a verification agent**, not asserted.

| # | Criterion | How it is proven |
|---|---|---|
| A1 | `server.py` boots on ARM64 Python with no `ctranslate2` import | process starts, logs a backend name |
| A2 | Known test WAV → correct transcript | WER/substring check against expected text |
| A3 | Chinese (Mandarin) transcription works, Simplified output | zh fixture transcribes, OpenCC applied |
| A4 | Latency: partial < 1.0 s, final < 2.0 s | `--benchmark` / timed harness |
| A5 | VAD gates speech vs silence | `pytest tests/test_vad.py` |
| A6 | Live mic → WebSocket → captions in overlay | browser overlay shows text while speaking |
| A7 | Webcam + captions composited into a virtual camera | camera enumerates and shows pixels |
| A8 | A call app (or AVFoundation/DirectShow enumerator) can select it | picker lists the device |
| A9 | Fully offline after first model download | run with network disabled |
| A10 | One-command / double-click launch for a non-technical user | documented + scripted |

## Strategy — two lanes, race them

- **Lane NATIVE**: Whisper on `onnxruntime` ARM64 CPU, then QNN/Hexagon NPU acceleration.
  Best battery + speed; highest uncertainty (decode loop + tokenizer availability).
- **Lane EMULATED**: x64 Python under Windows Prism emulation running stock
  `faster-whisper` + `pyvirtualcam` + x64 OBS. Near-certain to work; slower.
  Guarantees grandma gets captions even if NATIVE stalls.

Ship whichever lands first; keep the other as the documented fallback.

## Rules of engagement

1. **Every direction is a hypothesis with a test.** No "should work" — record evidence.
2. Log every result in `HYPOTHESES.md`, pass or fail. Failures are as valuable as passes.
3. Keep `STATUS.md` current; it is the single source of truth for the loop.
4. Never mark an acceptance criterion met without a reproducible command + output.
