# Hypothesis Ledger — Snapdragon ARM64 port

Append-only. Every direction taken must appear here **before** effort is spent, and be
resolved with evidence. Status: `OPEN` → `TESTING` → `CONFIRMED` / `REFUTED` / `PARTIAL`.

Evidence = a command and its actual output, or a file path. Never "should work".

---

## Resolved during initial recon (2026-08-09)

| ID | Hypothesis | Status | Evidence |
|---|---|---|---|
| H-000 | Machine is Windows-on-ARM64, not x64 | **CONFIRMED** | `PROCESSOR_ARCHITECTURE=ARM64`; `Snapdragon(R) X2 Elite - X2E88100` |
| H-001 | `ctranslate2` installs on win-arm64 py311 | **REFUTED** | `pip index versions ctranslate2` → `ERROR: No matching distribution found` |
| H-002 | `pyvirtualcam` has a win-arm64 wheel | **REFUTED** | `pip install --dry-run --only-binary=:all:` → NOWHEEL |
| H-003 | `torch` has a win-arm64 distribution | **REFUTED** | `pip index versions torch` → no matching distribution |
| H-004 | `onnxruntime` runs natively on this box | **CONFIRMED** | `ort 1.24.4`, providers `['QNNExecutionProvider','AzureExecutionProvider','CPUExecutionProvider']` |
| H-005 | Hexagon NPU is reachable from Python | **CONFIRMED** (provider present) | `QNNExecutionProvider` listed — *does not yet prove a Whisper model runs on it* |
| H-006 | `sherpa-onnx` ships a win-arm64 wheel | **REFUTED** | pip dry-run → NOWHEEL |
| H-007 | `pywhispercpp` ships a win-arm64 wheel | **REFUTED** | pip dry-run → NOWHEEL |
| H-008 | whisper.cpp upstream ships a Windows-ARM64 binary | **REFUTED** | `gh api repos/ggml-org/whisper.cpp/releases/latest` → only Win32/x64 |
| H-009 | OBS Studio ships a native Windows ARM64 build | **CONFIRMED** | `gh api repos/obsproject/obs-studio/releases/latest` → `OBS-Studio-32.2.1-Windows-arm64.zip` |
| H-010 | A C++ toolchain exists locally for source builds | **REFUTED** | no `cmake`, no `cl`, no `vswhere`, no Visual Studio |
| H-011 | `silero-vad` installs on win-arm64 | **CONFIRMED** (wheel resolves) | pip dry-run → `silero-vad-6.2.1` |

---

## Open hypotheses — Lane NATIVE (ONNX / QNN)

| ID | Hypothesis | Test | Status | Evidence |
|---|---|---|---|---|
| H-100 | A Whisper ONNX export runs end-to-end under onnxruntime CPU EP on ARM64 and returns correct text | download `onnx-community/whisper-base`, run encoder+decoder greedy loop on a known WAV | OPEN | |
| H-101 | A Whisper tokenizer usable without Rust wheels exists (`tokenizers`/`tiktoken` may not build) | check wheel availability; else pure-python BPE from `tokenizer.json`/vocab | OPEN | |
| H-102 | `onnxruntime-genai` supports Whisper and handles the decode loop for us | inspect API, attempt a run | OPEN | |
| H-103 | Whisper encoder runs on QNNExecutionProvider (Hexagon NPU) with correct output | run encoder with QNN EP, compare logits vs CPU EP | OPEN | |
| H-104 | NATIVE latency meets A4 (partial <1.0s, final <2.0s) | timed harness on 5s utterance | OPEN | |
| H-105 | Chinese/Mandarin accuracy is acceptable on the chosen model size | zh fixture WER check | OPEN | |

## Open hypotheses — Lane EMULATED (x64 Prism)

| ID | Hypothesis | Test | Status | Evidence |
|---|---|---|---|---|
| H-200 | x64 Python 3.11 installs and runs under Prism on this box | install, `python -c "import platform;print(platform.machine())"` | OPEN | |
| H-201 | `ctranslate2` + `faster-whisper` x64 wheels install and transcribe under emulation | run repo's own tests | OPEN | |
| H-202 | Emulated faster-whisper meets A4 latency with `base`/`small` | `./run.bat --benchmark` | OPEN | |
| H-203 | `pyvirtualcam` x64 wheel installs and opens the OBS camera under emulation | open device, push frames | OPEN | |

## Open hypotheses — Virtual camera / OBS

| ID | Hypothesis | Test | Status | Evidence |
|---|---|---|---|---|
| H-300 | OBS ARM64 portable zip runs on this machine | extract, launch `obs64.exe`/`obs-arm64.exe` | OPEN | |
| H-301 | OBS ARM64's virtual camera can be registered without an installer | `virtualcam-install.bat` / regsvr32 the DirectShow filter | OPEN | |
| H-302 | The registered virtual camera enumerates for **ARM64** consumers | enumerate DirectShow/MediaFoundation devices | OPEN | |
| H-303 | The virtual camera enumerates for **x64-emulated** call apps (WeChat/Zoom are x64) | needs an x64/ARM64EC filter DLL registered too — verify | OPEN | |
| H-304 | `overlay/index.html` works as an OBS **browser source** with transparency | add source, confirm captions render over webcam | OPEN | |
| H-305 | OBS "Start Virtual Camera" + browser source fully replaces `virtual_cam.py`/pyvirtualcam | end-to-end pixel check | OPEN | |

## Open hypotheses — Electron shell

| ID | Hypothesis | Test | Status | Evidence |
|---|---|---|---|---|
| H-400 | Node.js ships a Windows ARM64 build installable here | download, `node -v` | OPEN | |
| H-401 | `npm install` in `electron/` resolves on ARM64 | run it | OPEN | |
| H-402 | Electron runs as win32-arm64 and opens both windows | `npm start` | OPEN | |
| H-403 | `capturePage()` → TCP frame sink works without pyvirtualcam (sink swapped) | frames arrive at a stub receiver | OPEN | |
