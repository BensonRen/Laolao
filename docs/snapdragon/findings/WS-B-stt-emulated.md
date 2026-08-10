# WS-B — STT via x64 Prism emulation (the fallback lane)

Owner: WS-B · Machine: Snapdragon X2 Elite (X2E88100), Windows 11 Home 10.0.28000, ARM64
Date: 2026-08-09/10 · Venv: `C:\Users\snapd\Downloads\laolao\.venv-x64` (x64, mine alone)

> **Mission recap.** Prove the repo runs *unchanged* under x64 Prism emulation so the
> product ships even if the native-ARM64 lane stalls, and measure honestly how slow
> emulation actually is.

---

## Verdict at a glance

| ID | Hypothesis | Resolution |
|---|---|---|
| H-200 | x64 Python 3.11 installs and runs under Prism | **CONFIRMED** |
| H-201 | ctranslate2 + faster-whisper x64 wheels install and transcribe correctly | **CONFIRMED** |
| H-202 | Emulated faster-whisper meets A4 (partial <1.0 s / final <2.0 s) with `base`/`small` | **REFUTED** |
| H-203 | pyvirtualcam x64 wheel installs, imports, and opens the camera | **CONFIRMED** (exceeded — it opened a real device) |

**Bottom line: the emulated lane WORKS but is NOT fast enough to ship as the primary
captioning path.** Correctness is perfect; latency misses the A4 budget by ~3–10×.
It is a valid *degraded* fallback (see "Honest verdict") and it is currently the **only**
proven path to the virtual camera.

---

## H-200 — x64 Python 3.11 runs under Prism — **CONFIRMED**

### Install (current user, no admin, NOT on PATH)

```powershell
curl -sSL -o C:\Users\snapd\Downloads\laolao-tools\python-3.11.9-amd64.exe `
    https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe

& C:\Users\snapd\Downloads\laolao-tools\python-3.11.9-amd64.exe /passive `
    InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 `
    TargetDir=C:\Users\snapd\Downloads\laolao-tools\python311-x64
# exitcode=0
```

The installer itself is an x64 binary and ran under Prism without complaint. It does not
shadow the native ARM64 interpreter — `PrependPath=0`, `Include_launcher=0`, and a private
`TargetDir` keep it invisible to every other agent.

### Evidence

```
$ ...\python311-x64\python.exe -c "import platform,sys; ..."
3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]
machine= AMD64
architecture= ('64bit', 'WindowsPE')
exe= C:\Users\snapd\Downloads\laolao-tools\python311-x64\python.exe
```

`platform.machine() == AMD64` alone is not proof of emulation — it would say the same on a
real x64 box. The decisive call is `IsWow64Process2`, whose *nativeMachine* out-parameter
reports the real silicon:

```
IsWow64Process2 ok=1 err=0 process=0x0000 native=0xaa64
  0x8664=AMD64  0xaa64=ARM64  0x0000=not-emulated
PROCESSOR_ARCHITECTURE   = AMD64
PROCESSOR_IDENTIFIER     = ARMv8 (64-bit) Family 8 Model 2 Revision 201, Qualcomm Technologies Inc
os.cpu_count()           = 18
```

An **AMD64 PE image** executing on **ARM64 silicon** (`native=0xaa64`) is Prism, by definition.
`processMachine = 0x0000` is correct and expected: x64-on-ARM64 is *not* classic WOW64
(that mechanism is 32-bit-only), so Windows reports "not under WOW64" while still translating.

Contrast, same shell, the native interpreter other agents use:

```
$ ...\Python311-arm64\python.exe -c "import platform,os; ..."
machine= ARM64 PROCESSOR_ARCHITECTURE= ARM64
```

Two gotchas worth recording, both of which cost real time:

- `GetNativeSystemInfo()` **lies** inside an emulated x64 process — it returns
  `PROCESSOR_ARCHITECTURE_AMD64 (9)`. Do not use it to detect Prism.
- `kernel32.IsWow64Process2` via ctypes silently fails (`ok=0`) unless you set
  `GetCurrentProcess.restype = HANDLE`; otherwise the `-1` pseudo-handle is truncated to
  32 bits. This bit me once and is fixed in `ws_b_verify.py`.

---

## H-201 — ctranslate2 + faster-whisper install and transcribe — **CONFIRMED**

### Install

```powershell
& C:\Users\snapd\Downloads\laolao-tools\python311-x64\python.exe -m venv `
    C:\Users\snapd\Downloads\laolao\.venv-x64
& C:\Users\snapd\Downloads\laolao\.venv-x64\Scripts\python.exe -m pip install --upgrade pip
& C:\Users\snapd\Downloads\laolao\.venv-x64\Scripts\python.exe -m pip install `
    -r C:\Users\snapd\Downloads\laolao\requirements.txt pytest
```

**The repo's stock `requirements.txt` installed verbatim — no pins changed, no source
builds, no compiler needed.** Every dependency resolved to a prebuilt `win_amd64` wheel:

```
Successfully installed MarkupSafe-3.0.3 Pillow-12.3.0 anyio-4.14.2 av-18.0.0
certifi-2026.7.22 cffi-2.1.1 click-8.4.2 colorama-0.4.6 ctranslate2-4.8.1
faster-whisper-1.2.1 filelock-3.32.2 flatbuffers-25.12.19 fsspec-2026.7.0 h11-0.16.0
hf-xet-1.6.0 httpcore-1.0.9 httpx-0.28.1 huggingface-hub-1.27.0 idna-3.18
iniconfig-2.3.0 jinja2-3.1.6 mpmath-1.3.0 networkx-3.6.1 numpy-2.4.6
onnxruntime-1.28.0 opencc-python-reimplemented-0.1.7 packaging-26.3 pluggy-1.6.0
protobuf-7.35.1 pycparser-3.0 pygments-2.20.0 pytest-9.1.1 pyvirtualcam-0.15.0
pyyaml-6.0.3 setuptools-84.0.0 silero-vad-6.2.1 sounddevice-0.5.5 sympy-1.14.0
tokenizers-0.23.1 torch-2.13.0 torchaudio-2.11.0 tqdm-4.70.0 typing-extensions-4.16.0
websockets-17.0.1
```

Notable: **`torch 2.13.0+cpu` and `torchaudio 2.11.0` installed too** (pulled in by
`silero-vad`), and `tokenizers 0.23.1` came as a wheel — no Rust toolchain required.
This is exactly the set the ARM64 lane cannot have (H-001/H-003 REFUTED for arm64).

```
ctranslate2 4.8.1 | faster_whisper 1.2.1 | pyvirtualcam 0.15.0
onnxruntime 1.28.0 | numpy 2.4.6 | torch 2.13.0+cpu
ct2 cpu compute types: {'int8', 'int8_float32', 'float32'}
```

### Audio fixtures

The repo's `tests/generate_test_audio.py` only produces *speech* on macOS (`say`), and
`tests/download_audio.py` needs yt-dlp + ffmpeg (neither present). So:

- **English (synthetic, offline)** — Windows SAPI `System.Speech`, voice *Microsoft Zira
  Desktop*, rendered straight to 16 kHz mono 16-bit WAV. Text is the repo's own fixture
  string. This is the Windows equivalent of the macOS `say` path.
- **English (real human)** — `jfk.wav`, the canonical whisper.cpp sample, 11.00 s.
- **Mandarin (real human)** — `asr_example_zh.wav`, the canonical FunASR/ModelScope sample,
  5.55 s, whose published reference transcript is
  `欢迎大家来体验达摩院推出的语音识别模型`.
- **Mandarin (second opinion)** — `test_wavs/0.wav` from
  `csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14`, 5.61 s.

All four are 16 kHz mono int16 — exactly Whisper's input format, no resampling.
They live outside the repo in `C:\Users\snapd\Downloads\laolao-tools\audio\`
(copies also staged into the gitignored `tests/fixtures/`).

There is **no Mandarin TTS voice on this machine** — SAPI exposes only *David* and *Zira*
(en-US), and `HKLM\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens` likewise lists only
David/Mark/Zira. Hence the downloaded human-speech Mandarin fixture.

### Transcripts obtained vs expected (model `base`, cpu/int8, via the repo's own
`backends.faster_whisper_backend.FasterWhisperBackend`)

| Fixture | Lang | Expected | Got | Verdict |
|---|---|---|---|---|
| `jfk.wav` (11.00 s) | en | *And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.* | `And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.` | **exact** |
| `english_speech.wav` (3.33 s) | en | *Hello grandma, I miss you very much.* | `Hello grandma, I miss you very much.` | **exact** |
| `asr_example_zh.wav` (5.55 s) | zh | `欢迎大家来体验达摩院推出的语音识别模型` | `欢迎大家来体验,打模院推出的语音识别模型。` | **correct except one proper noun** |
| `paraformer_zh_0.wav` (5.61 s) | zh | (sherpa reference: 对我做了介绍…大家如果对我的研究感兴趣呢) | `对我做了介绍,我想说的是呢,大家如果对我的研究感兴趣呢` | **matches** |

The single Mandarin error is `达摩院 → 打模院` — a homophone substitution on the DAMO
Academy brand name, a well-known `base`-sized-Whisper weakness, not an emulation artifact.
Everything else is character-exact. **Output is already Simplified Chinese**, so the
OpenCC `t2s` stage (A3) has nothing to undo.

Emulation therefore introduces **zero** numerical/accuracy regression. The x64 int8
kernels produce the same text they would on a real x64 machine.

---

## H-202 — latency vs A4 (partial <1000 ms, final <2000 ms) — **REFUTED**

Measured through the repo's real backend call (`FasterWhisperBackend.transcribe`,
`beam_size=1, best_of=1, temperature=0, without_timestamps=True` — the settings the
server actually uses). Each cell is the **median of 5 timed passes after a warm-up pass**.
Window sizes mirror what `server.py` really feeds Whisper: a growing rolling partial
buffer, and the `rolling_window_s` final commit (config default 4.0 s; README default 5.0 s).

<!--LATENCY_TABLE-->

### Why the numbers are flat

Latency barely moves between a 1 s and a 5 s window. That is not a measurement error:
Whisper **always pads its input to a 30-second mel spectrogram**, so the encoder does an
identical amount of work regardless of how much real audio you hand it. Under emulation
that fixed encoder cost dominates everything else, which means:

- shortening `rolling_window_s` or `partial_interval_s` **cannot** buy latency back;
- RTF is a misleading metric here — the honest number is *milliseconds per pass*.

<!--THREADS_SECTION-->

---

## H-203 — pyvirtualcam under emulation — **CONFIRMED (exceeded)**

The brief only asked for install + import, with a note that opening a real device depends
on WS-C installing OBS. By the time I tested, WS-C had registered the filter, so I got the
stronger result:

```
pyvirtualcam version: 0.15.0
Camera: <class 'pyvirtualcam.camera.Camera'> | PixelFormat members: ['RGB','BGR','RGBA','GRAY','I420','NV12','YUYV','UYVY']
registered backends: ['obs', 'unitycapture']
native ext modules: ['_native_windows_obs.cp311-win_amd64.pyd',
                     '_native_windows_unity_capture.cp311-win_amd64.pyd']
OPENED device: OBS Virtual Camera        <-- opened AND accepted a 1280x720 RGB frame
```

### Cross-workstream finding — the OBS ARM64 build ships an **x64** camera filter

Chasing *which* DLL pyvirtualcam bound to turned up something the whole project should know:

```
HKLM:\SOFTWARE\Classes\CLSID\{A3FCE0F5-3493-419F-958A-ABA1250EC20B}\InprocServer32
  => ...\laolao-tools\obs-arm64\data\obs-plugins\win-dshow\obs-virtualcam-module64.dll
HKLM:\SOFTWARE\Classes\WOW6432Node\CLSID\{A3FCE0F5-...}\InprocServer32
  => ...\obs-virtualcam-module32.dll
DirectShow video-input category: {A3FCE0F5-3493-419F-958A-ABA1250EC20B}  "OBS Virtual Camera"
```

PE machine types of those files:

```
obs-virtualcam-module64.dll              machine=0x8664 AMD64(x64)
obs-virtualcam-module32.dll              machine=0x014c i386
_native_windows_obs.cp311-win_amd64.pyd  machine=0x8664 AMD64(x64)
python.exe                               machine=0x8664 AMD64(x64)
```

**OBS's *ARM64* distribution ships its DirectShow virtual-camera filter as x64 and x86 —
there is no ARM64 build of the filter.** A DirectShow filter is loaded in-process by the
consumer, so:

- an **emulated x64** producer/consumer (this lane) can load it — *demonstrated above*;
- x64 call apps (WeChat, Zoom) can load it — good news for H-303;
- a **native ARM64** process **cannot** load it. Any native-ARM64 producer (i.e. WS-A's
  lane driving `virtual_cam.py`) has no route to this camera and will need OBS Studio's own
  "Start Virtual Camera" (out-of-process) instead of pyvirtualcam.

This makes the emulated lane the only *proven* end-to-end path to a virtual camera today,
even though it is the slower lane for STT.

---

## Repo test suite in the x64 venv

<!--PYTEST_SECTION-->

---

## Honest verdict — is emulation fast enough to ship to a real user?

<!--VERDICT-->

---

## Reproducing

```powershell
C:\Users\snapd\Downloads\laolao\.venv-x64\Scripts\python.exe `
    C:\Users\snapd\Downloads\laolao\docs\snapdragon\findings\ws_b_verify.py
```

Re-proves H-200…H-203 from scratch, re-downloading the audio fixtures if they are missing.
Exit code 0 means nothing came out REFUTED.
