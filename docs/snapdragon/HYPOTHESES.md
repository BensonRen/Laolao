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
| H-100 | A Whisper ONNX export runs end-to-end under onnxruntime CPU EP on ARM64 and returns correct text | greedy encoder+decoder loop on a known WAV | **CONFIRMED** | `onnx-community/whisper-tiny` on CPU EP, WER **0.000** on both English fixtures, 396–485 ms |
| H-101 | A Whisper tokenizer usable without Rust wheels exists | check wheel availability | **CONFIRMED** | `tokenizers-0.23.1-cp310-abi3-win_arm64.whl` exists — `tiktoken` does **not**. Round-trip `你好 grandma` verified. This was the sharpest risk in the whole lane and it resolved in our favour. |
| H-102 | `onnxruntime-genai` supports Whisper and handles the decode loop | inspect API, attempt a run | **CONFIRMED, not adopted** | genai 0.15.2 win-arm64 transcribes JFK correctly in 1.158 s — ~9× slower than the NPU path, and it bundles a conflicting onnxruntime |
| H-103 | Whisper encoder runs on QNNExecutionProvider (Hexagon NPU) with correct output | compare vs CPU EP | **CONFIRMED** | fp32 encoder QNN-vs-CPU **cos=1.000000**, maxabs 6e-4; character-identical text on 3/3 Mandarin clips |
| H-104 | NATIVE latency meets A4 | timed harness | **CONFIRMED** | NPU base **p50 121 ms / p95 123 ms**; large-v3-turbo p50 480 / p95 486. Harness A4: 88 ms / 93 ms. |
| H-105 | Chinese/Mandarin accuracy is acceptable on the chosen model size | zh fixture CER | **CONFIRMED (turbo) / PARTIAL (base)** | turbo mean CER **0.049** FLEURS, **0.000** AISHELL-1; base 0.196. Caveat from WS-A: only 3 FLEURS clips + 1 AISHELL utterance, so read 0.049 as "turbo is clearly better", not a calibrated WER. |

### Three traps WS-A hit that would cost the next person the same time

1. **ORT 2.x moved QNN to a plugin EP.** `onnxruntime-qnn` 2.4.0 no longer patches `onnxruntime`. You must `register_execution_provider_library(...)`, and then passing `providers=[("QNNExecutionProvider", …)]` **silently falls back to CPU** — you have to use `so.add_provider_for_devices([npu_device], …)` with no `providers=` argument. Silent CPU fallback is the recurring failure mode of this whole platform.
2. **The default thread count is a trap.** ORT's default 18 threads is ~2× slower than 3 on this heterogeneous part (tiny encoder: 605 ms @1 → 383 @3 → 674 @12).
3. **`platform.processor()` cannot distinguish X2 from X.** It returns `ARMv8 (64-bit) Family 8 Model 2`; the chipset name lives only in the registry `ProcessorNameString`.

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
| H-300 | OBS ARM64 portable zip runs on this machine | extract and launch | **CONFIRMED** | D3D11 on Adreno X2-90; obs-browser CEF 127, win-dshow, obs-websocket all native ARM64 |
| H-301 | OBS ARM64's virtual camera can be registered without an installer | register the filter by hand | **CONFIRMED — and without admin** | HKCU registration works fully (verified with HKLM removed). Big for A10. **You cannot `regsvr32` the x64 filter on ARM64** — there is no x64 regsvr32 host; it exits 0 and writes nothing. Registration must be hand-written registry keys. |
| H-302 | The registered virtual camera works for **ARM64** consumers | bind-test from ARM64 | **CONFIRMED, but mutually exclusive with H-303** | see below |
| H-303 | The virtual camera works for **x64-emulated** call apps | bind-test from x64 | **CONFIRMED, but mutually exclusive with H-302** | see below |
| H-304 | `overlay/index.html` works as an OBS **browser source** with transparency | add source, check compositing | **CONFIRMED (needs a CSS override)** | The overlay is **not** transparent by default — `html, body { background:#000 }` plus an inline JS body background made the caption layer completely hide the webcam. Fixed with Custom CSS on the source; **no repo change needed**. |
| H-305 | OBS browser source + Start Virtual Camera fully replaces `virtual_cam.py`/pyvirtualcam | end-to-end pixel check | **CONFIRMED** | Evidence image `findings/WS-C-evidence-vcam-x64-frame.jpg`: a frame pulled *out of the virtual camera by an x64 process*, showing the live webcam with 「你好，奶奶！我今天很好。」 composited on it |
| H-306 | The Qualcomm Spectra ISP webcam is openable by a DirectShow consumer | open it and read a frame | **CONFIRMED** | OBS's Video Capture Device source drives it; WS-D also opened it via Chromium getUserMedia |

### ⚠ H-302 / H-303 are mutually exclusive — there is ONE 64-bit CLSID slot

The ARM64 zip ships three filter DLLs (ARM64, x64, x86), so an x64 filter *exists*. But
**Windows-on-ARM64 gives x64-emulated processes no registry redirection** — WS-C proved
this by reading the same key from both architectures and by writing the x64 DLL into
`Wow6464Node` and watching COM ignore it.

| Registered DLL | ARM64 consumer | x64 consumer (WeChat / Zoom) |
|---|---|---|
| `...module-arm64.dll` | `S_OK`, pins=1 | `ERROR_BAD_EXE_FORMAT` |
| `...module64.dll` | `ERROR_BAD_EXE_FORMAT` | `S_OK`, pins=1 |

WeChat and Zoom are x64, so **register the x64 filter** (the setup script's default).
Serving both at once would need an ARM64X DLL, which OBS does not ship and we cannot
build (no MSVC). **Failure mode worth documenting for users:** the wrong architecture
still *appears in the camera picker* and then delivers a dead feed — it looks like a
broken app, not a wrong setting.

Two more OBS gotchas: `virtual-camera.type2` must be `3` (Program) — `1` yields a
healthy-looking 1280×720 NV12 stream that is entirely black. And `.sentinel` is a
*directory*; leftover run-markers block startup behind an invisible safe-mode dialog,
with no `--disable-shutdown-check` flag in OBS 32.

**Open risk:** if the user's Zoom is the ARM64-native build rather than x64, they must
re-run setup with `-Arch arm64`. Worth confirming against the real apps before shipping.
The x86 filter is registered too but was never bind-tested (no 32-bit consumer available),
so it is recorded as unverified rather than claimed to work.
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
| H-400 | Node.js ships a Windows ARM64 build installable here | download, `node -v` | **CONFIRMED** | Node v24.19.0 portable win-arm64, `process.arch = arm64`, npm 11.17.0 |
| H-401 | `npm install` in `electron/` resolves on ARM64 | run it | **CONFIRMED** | 310 packages in 35 s, no native builds. **Electron 29.4.6, PE machine `0xAA64` = ARM64.** Caveat: npm 11's `allow-scripts` gate warns on Electron's postinstall — setup must verify `node_modules/electron/dist/electron.exe` exists. |
| H-402 | Electron runs as win32-arm64 and opens both windows | `npm start` | **CONFIRMED (with a caveat, now fixed)** | Both windows open, capture loop sustains 30 fps. But `checkObs()` was awaited *before* any window was created, so a vanilla first `npm start` produced **zero windows** — just a modal dialog forever. Fixed. Also: the GPU process crashes on every launch (`exit_code=34`) on the Adreno X2-90 and Chromium falls back to software rendering — unresolved, but not blocking. |
| H-403 | `capturePage()` → TCP frame sink works without pyvirtualcam | frames arrive at a receiver | **CONFIRMED twice** | Stub sink: 780 frames / 98.9 MB in ~28 s (≈28 fps), all 1280×720, post-camera frames mean 79–85, stdev ~61, 92–94 % of pixels above black. Then the **real** path: unmodified `virtual_cam.py` on the emulated x64 interpreter, fed by native ARM64 Electron over TCP, read back by a separate x64 DirectShow consumer — 60/60 reads succeeded, mean ~94.8, carrying a live Chinese caption produced by the native ARM64 caption server. |

### ⚠ H-410 — the silent caption-eating bug (found by WS-D, fixed in `main.js`)

This desktop's DIP work area is 1024×768, so Chromium **clamps the output window at
construction** → it is born 1008×720 instead of 1280×720 → `capturePage()` returns
1008×720 → the "aspect-safe" center-crop discards **the top and bottom 21 %** and
upscales. The caption bar is at the bottom, so **the captions are cropped off the frame
the far end receives** — while `black=false` and a correct 1280×720 arrive at the sink,
so every diagnostic still reads healthy.

Eight window variants were tested: `offscreen`, `frame:false` and `minWidth` all still
clamp. **`setContentSize(CAM_W, CAM_H)` after construction** is the one that holds.

This is the most dangerous class of bug in this project: the product is broken in exactly
the way the user cares about, and every instrument says it is fine.
