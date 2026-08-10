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
| H-011 | `silero-vad` installs on win-arm64 | ~~CONFIRMED~~ → **REFUTED** | **I got this wrong.** The dry run used `--no-deps`, which only proved silero-vad's own wheel resolves. A real install fails: every version `depends on torch>=1.12.0`, and torch has no win-arm64 distribution (H-003). Actual output: `ResolutionImpossible ... some packages in these conflicts have no matching distributions available for your environment: torch`. **Consequence: EnergyVAD is the only VAD on this platform.** `vad: auto` degrades to it correctly (`silero-vad not installed; falling back to energy VAD`), and A5 passes on EnergyVAD. Lesson: `--no-deps` availability is not installability. |

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
| H-200 | x64 Python 3.11 installs and runs under Prism on this box | install, check arch | **CONFIRMED** | `IsWow64Process2` → `native=0xaa64` (ARM64 silicon) running an `AMD64` PE. Note: `GetNativeSystemInfo()` *lies* inside an emulated process (reports AMD64) — do not use it to detect Prism. |
| H-201 | `ctranslate2` + `faster-whisper` x64 wheels install and transcribe under emulation | run repo's own tests | **CONFIRMED** | Stock `requirements.txt` installed verbatim, all prebuilt `win_amd64` wheels, no compiler. `jfk.wav` → character-exact. Mandarin `asr_example_zh.wav` → correct but `达摩院`→`打模院` (a `base`-model homophone slip, **not** an emulation artifact). Output already Simplified. **Emulation causes zero accuracy regression.** |
| H-202 | Emulated faster-whisper meets A4 latency with `base`/`small` | timed passes | **REFUTED** | Misses the A4 budget by ~3–10×. Key insight: Whisper always pads to a **30 s mel spectrogram**, so encoder cost is constant regardless of window size — shrinking `rolling_window_s`/`partial_interval_s` cannot buy latency back. |
| H-203 | `pyvirtualcam` x64 wheel installs and opens the OBS camera under emulation | open device, push frames | **CONFIRMED (exceeded)** | Not just imported — *opened* `OBS Virtual Camera` and accepted a 1280×720 RGB frame. |

### ⚠ H-210 — architectural constraint discovered by WS-B (changes the whole design)

**The OBS *ARM64* distribution ships its DirectShow virtual-camera filter as x64 and x86 only.
There is no ARM64 build of the filter.**

```
CLSID\{A3FCE0F5-3493-419F-958A-ABA1250EC20B}\InprocServer32 -> obs-virtualcam-module64.dll
WOW6432Node\CLSID\{A3FCE0F5-...}\InprocServer32             -> obs-virtualcam-module32.dll

obs-virtualcam-module64.dll   machine=0x8664  AMD64(x64)
obs-virtualcam-module32.dll   machine=0x014c  i386
```

A DirectShow filter is loaded **in-process** by whoever uses it. Therefore:

- ✅ emulated-x64 producers can load it — *demonstrated*, H-203
- ✅ x64 call apps (WeChat, Zoom) can load it — strong signal for **H-303**
- ❌ a **native ARM64** process can **never** load it — so a native-ARM64 producer has no
  in-process route to this camera

**Consequence — the target architecture is mixed-architecture, and the repo already
supports it.** `virtual_cam.py` is a *separate process* fed over TCP :8766 by design
(`[4-byte BE length][JPEG]`). That process boundary is an architecture boundary for free:

```
native ARM64  Electron  ──capturePage→JPEG──▶ TCP :8766 ──▶ emulated x64  virtual_cam.py ──▶ OBS DShow filter ──▶ WeChat/Zoom
native ARM64  server.py (ONNX/QNN STT)  ──WebSocket :8765──▶ overlay
```

Only the thin frame-sink runs emulated; STT and UI stay native. This is strictly better
than emulating everything, because H-202 shows STT is exactly what emulation ruins and
H-203 shows the sink is exactly what emulation handles fine.

## Open hypotheses — Virtual camera / OBS

| ID | Hypothesis | Test | Status | Evidence |
|---|---|---|---|---|
| H-300 | OBS ARM64 portable zip runs on this machine | extract, launch `obs64.exe`/`obs-arm64.exe` | OPEN | |
| H-301 | OBS ARM64's virtual camera can be registered without an installer | `virtualcam-install.bat` / regsvr32 the DirectShow filter | OPEN | |
| H-302 | The registered virtual camera enumerates for **ARM64** consumers | enumerate DirectShow/MediaFoundation devices | OPEN | |
| H-303 | The virtual camera enumerates for **x64-emulated** call apps (WeChat/Zoom are x64) | needs an x64/ARM64EC filter DLL registered too — verify | OPEN | |
| H-304 | `overlay/index.html` works as an OBS **browser source** with transparency | add source, confirm captions render over webcam | OPEN | |
| H-305 | OBS "Start Virtual Camera" + browser source fully replaces `virtual_cam.py`/pyvirtualcam | end-to-end pixel check | OPEN | |
| H-306 | The built-in webcam (Qualcomm Spectra ISP) is openable by a **DirectShow** consumer, not MediaFoundation-only | open it from a DShow consumer and read a real frame | OPEN | On Snapdragon the camera runs through the Spectra ISP/AVStream stack, historically MF-first. If DShow cannot open it, OBS's Video Capture Device source sees nothing and the whole compositing plan needs an MF path. |

### Pre-state baseline (before any install) — `acceptance/check.py --only A7 A10`

```
[FAIL ] A7  virtual=[]  all_devices=[
      'Qualcomm(R) Spectra(TM)  ISP Camera Platform Device',
      'Qualcomm(R) Spectra(TM)  ISP Camera MipiCsi Device',
      'Qualcomm(R) Spectra(TM)  ISP Camera Front Sensor Device',
      'Qualcomm(R) Spectra(TM)  ISP Camera JPEG Encoder Device',
      'Qualcomm(R) Spectra(TM)  ISP Camera Device',
      'Qualcomm(R) Spectra(TM)  ISP Camera Flash Device',
      'Qualcomm(R) Spectra(TM)  ISP Camera AVStream Device',
      'Qualcomm(R) Spectra(TM)  ISP Camera Auxiliary Sensor Device']
[skip ] A10  launchers present: ['run.bat']   (x86 path only)
```

Confirms the A7 pre-state is a true negative: no virtual camera exists yet, so any
later PASS is attributable to work done here and not to a pre-existing OBS install.

## Open hypotheses — Electron shell

| ID | Hypothesis | Test | Status | Evidence |
|---|---|---|---|---|
| H-400 | Node.js ships a Windows ARM64 build installable here | download, `node -v` | OPEN | |
| H-401 | `npm install` in `electron/` resolves on ARM64 | run it | OPEN | |
| H-402 | Electron runs as win32-arm64 and opens both windows | `npm start` | OPEN | |
| H-403 | `capturePage()` → TCP frame sink works without pyvirtualcam (sink swapped) | frames arrive at a stub receiver | OPEN | |
