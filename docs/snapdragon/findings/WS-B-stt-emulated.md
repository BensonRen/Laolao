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
captioning path.** Correctness is perfect — transcripts are character-exact in English and
near-exact in Mandarin, and 53 of 56 applicable repo tests pass. Latency is the problem:

| model | partial (need <1 000 ms) | final (need <2 000 ms) |
|---|---:|---:|
| `small` (shipped `config.json` default) | **12 578 ms** | **14 124 ms** |
| `base` (CLAUDE.md default) | **2 553–3 053 ms** | **3 007–3 039 ms** |
| `tiny` | **1 171 ms** | **1 261 ms** ✅ |

`tiny` is the only configuration that comes close, and it meets the *final* budget. The
lane is therefore a valid **degraded** fallback (see "Honest verdict"), and — because OBS's
ARM64 build ships an **x64-only** camera filter — it is currently the only proven path to
the virtual camera at all.

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

### Isolation check (no other agent is affected)

```
PATH entries containing python311-x64 : 0
Get-Command python -> C:\Users\snapd\AppData\Local\Programs\Python\Python311-arm64\python.exe
default `python` machine = ARM64
HKCU PEP514 PythonCore keys: 3.11 (new, x64)   3.11-arm64 (pre-existing)
```

The **only** side effect on the shared machine is the PEP 514 registry key `3.11` under
`HKCU\SOFTWARE\Python\PythonCore`. It affects nothing here because the `py` launcher was
not installed (`Include_launcher=0`) and the interpreter is not on `PATH`. Bare `python`
still resolves to the native ARM64 build that WS-A/WS-C/WS-D rely on.

### Two gotchas worth recording, both of which cost real time:

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

**Utterance under test:** `asr_example_zh.wav` (5.55 s of continuous Mandarin) and
`jfk.wav` (11.00 s of continuous English), sliced to the leading window shown.

### `base` — median ms per transcription pass

| Pass shape (window) | zh | en | A4 budget | Verdict |
|---|---:|---:|---:|---|
| partial, 1.0 s buffer | **3 474** | **4 768** | 1 000 | ❌ 3.5–4.8× over |
| partial, 2.0 s buffer | **3 652** | **3 220** | 1 000 | ❌ 3.2–3.7× over |
| final, 4.0 s window | **3 165** | **3 019** | 2 000 | ❌ 1.5–1.6× over |
| final, 5.0 s window | **3 178** | **3 386** | 2 000 | ❌ 1.6–1.7× over |

Spread (min…max over the 5 timed passes): zh partial-2.0 s 3 067…8 268 ms;
en final-4.0 s 2 820…11 422 ms. Cold model load: **20.5 s** on first ever run
(includes the 142 MB download), **6.4 s** from warm cache.

### `small` — median ms per transcription pass

| Pass shape (window) | zh | en | A4 budget | Verdict |
|---|---:|---:|---:|---|
| partial, 1.0 s buffer | **13 911** | **12 537** | 1 000 | ❌ 12.5–13.9× over |
| partial, 2.0 s buffer | **12 578** | **12 132** | 1 000 | ❌ 12.1–12.6× over |
| final, 4.0 s window | **14 124** | **12 321** | 2 000 | ❌ 6.2–7.1× over |
| final, 5.0 s window | *not captured* | *not captured* | 2 000 | process died, see below |

Cold model load: **12.9 s** from warm cache (461 MB download preceded it).

The `small` run terminated with **exit 255** during the last probe. Cause was memory
pressure, not a code fault — `Get-CimInstance Win32_OperatingSystem` reported only
**2 639 MB free physical RAM** at that moment, with four agents' Python processes
resident simultaneously. The three probes that did complete already settle the
hypothesis by an order of magnitude, so I did not re-run it.

> **Measurement honesty.** These runs shared the machine with the other workstreams'
> agents; sampled CPU load was **96–99 %** for much of the benchmark, so the medians are
> an **upper bound**, not a quiet-machine figure. That caveat cannot rescue the result:
> even the fastest single pass ever observed (`base`, en, final-4.0 s) was **2 820 ms**,
> still over the 2 000 ms final budget, and `base` would have to get **3.5× faster** to
> meet the partial budget. The clean re-measurement below isolates the machine-contention
> and thread-count variables directly.

### Why the numbers are flat

Latency barely moves between a 1 s and a 5 s window. That is not a measurement error:
Whisper **always pads its input to a 30-second mel spectrogram**, so the encoder does an
identical amount of work regardless of how much real audio you hand it. Under emulation
that fixed encoder cost dominates everything else, which means:

- shortening `rolling_window_s` or `partial_interval_s` **cannot** buy latency back;
- RTF is a misleading metric here — the honest number is *milliseconds per pass*.

### Clean re-measurement: does thread count or a smaller model rescue it?

Two obvious escape hatches had to be ruled out before declaring H-202 refuted.

**Escape hatch 1 — we're only using 4 of 18 cores.** Real: `faster_whisper.WhisperModel`
defaults to `cpu_threads=0`, which it passes to CTranslate2 as `intra_threads=0`, and
CTranslate2's own default is **4 threads** (its docstring says so: *"Number of threads to
use when running on CPU (4 by default)"*). On an 18-core machine the repo leaves 14 cores
idle. Worth testing.

**Escape hatch 2 — use `tiny`.**

Both measured together, `asr_example_zh.wav`, median of 5 timed passes after warm-up,
on a quieter machine:

| model | cpu_threads | partial (2.0 s) | final (4.0 s) |
|---|---:|---:|---:|
| `tiny` | 4 (default) | 1 170 ms | 1 321 ms |
| `tiny` | 8 | 1 202 ms | 1 381 ms |
| `tiny` | 12 | 1 173 ms | 1 350 ms |
| `tiny` | 18 | **1 171 ms** | **1 261 ms** |
| `base` | 4 (default) | 3 053 ms | 3 007 ms |
| `base` | 8 | 3 172 ms | 3 689 ms |
| `base` | 12 | 2 634 ms | 2 809 ms |
| `base` | 18 | **2 553 ms** | **3 039 ms** |

**Escape hatch 1 is closed.** Going from 4 → 18 threads buys `tiny` nothing at all
(1 170 → 1 171 ms) and `base` about 16 % at best (3 053 → 2 553 ms), well inside run-to-run
noise for the `final` column. The workload does not scale with cores here, which tells us
the bottleneck is **per-core throughput of Prism-translated SIMD**, not parallelism —
exactly what you would expect when every AVX2 int8 GEMM instruction has to be translated.
Nothing in a config file is going to fix that.

This re-measurement also validates the contaminated numbers above: clean `base`@4 is
3 053 / 3 007 ms versus 3 652 / 3 165 ms under load — the other agents cost ~5–20 %, not
the 3–10× that would be needed to change any verdict.

**Escape hatch 2 is a partial win and the one genuinely interesting result:**

| `tiny` @ emulated | measured | A4 budget | |
|---|---:|---:|---|
| partial | 1 171 ms | 1 000 ms | ❌ misses by 17 % |
| final | 1 261 ms | 2 000 ms | ✅ **passes** |

`tiny` is the only configuration that comes anywhere near shippable — it *meets* the final
budget and misses the partial budget narrowly. But `tiny` is also the weakest model for
Mandarin, which is this product's primary language, so buying latency this way costs
exactly the accuracy the product exists to provide. It is a real option, not a good one.

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

**Later in the session the same call started failing** — worth recording so nobody thinks
it regressed:

```
OPEN FAILED: RuntimeError 'obs' backend: virtual camera output could not be started
              'unitycapture' backend: No camera registered. Did you install any camera?
filter still registered -> ...\obs-arm64\...\obs-virtualcam-module64.dll
```

The filter is still registered; the *slot* is busy. `Get-Process` shows WS-C had by then
launched `obs64.exe` from `laolao-tools\obs-arm64\bin\64bit\`. This is the documented
single-producer constraint (README: *"Only one app can use 'OBS Virtual Camera' at a
time"*), not an emulation problem — and it is a live coordination hazard for WS-E's
end-to-end run: **OBS Studio's own virtual camera and `virtual_cam.py` cannot both be
active.**

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

### Fast tests

```
$ .venv-x64\Scripts\python.exe -m pytest tests/ -m "not slow" -q
3 failed, 43 passed, 9 skipped, 11 deselected, 2 warnings in 37.13s

FAILED tests/test_windows_headless.py::test_server_starts_and_binds  - FileNotFoundError
FAILED tests/test_windows_headless.py::test_server_accepts_websocket - FileNotFoundError
FAILED tests/test_windows_headless.py::test_virtual_cam_tcp_port     - FileNotFoundError
```

**All three failures are one bug, and it is not an emulation bug.**
`tests/test_windows_headless.py` hardcodes the interpreter path:

```python
venv_py = ROOT / ("venv/Scripts/python.exe" if IS_WIN else "venv/bin/python")
```

My venv is `.venv-x64`, per the isolation rules in `STATUS.md`, so `CreateProcess` raises
`[WinError 2] The system cannot find the file specified`. Proof — point `venv` at it and
re-run the same three tests unchanged:

```
$ New-Item -ItemType Junction -Path .\venv -Target .\.venv-x64     # no admin needed
$ .venv-x64\Scripts\python.exe -m pytest tests/test_windows_headless.py -q `
      -k "server_starts or accepts_websocket or virtual_cam_tcp"
2 passed, 1 skipped, 16 deselected in 32.56s
```

So under emulation **`server.py` really does boot, bind :8765, complete a WebSocket
handshake and emit its first JSON message** — that is acceptance criterion **A1** satisfied
on this lane (with faster-whisper rather than "no ctranslate2"). The 1 skip is
`test_virtual_cam_tcp_port`, whose `@vcam_available` guard evaluated False at import time
because OBS Studio had grabbed the camera slot (above).

I removed the `venv` junction afterwards so it cannot confuse WS-A/WS-D/WS-E. Recreate it
with the one-liner above if you want a green headless run. A cleaner long-term fix is for
the test to honour a `LAOLAO_PYTHON` env var or fall back to `sys.executable` — but I was
told not to modify repo files, so I did not.

The 9 skips in the fast run are the macOS-only tests plus `silero_vad`/optional guards.

### Slow (inference) tests

```
$ .venv-x64\Scripts\python.exe -m pytest tests/ -m slow -q
10 passed, 1 skipped, 55 deselected, 2 warnings in 40.20s
```

**Every inference test passes under emulation.** The single skip is
`test_backend_chinese_tts_contains_chinese`, which is `@pytest.mark.macos_only`.

The repo's own latency harness, with its printed numbers (`tiny` model, per the
`backend_cfg` fixture):

```
$ .venv-x64\Scripts\python.exe -m pytest tests/test_latency.py -m slow -q -s
[latency] baseline: 3 s silence → 1.515s (RTF 0.51x)
[latency] chunk 1/6: buffer=0.5s, transcription=1.210s, total_wall=1.210s, result=''
[latency] chunk 2/6: buffer=1.0s, transcription=1.214s, total_wall=2.424s, result=''
[latency] chunk 3/6: buffer=1.5s, transcription=1.165s, total_wall=3.589s, result=''
[latency] chunk 4/6: buffer=2.0s, transcription=1.179s, total_wall=4.769s, result=''
[latency] chunk 5/6: buffer=2.5s, transcription=1.187s, total_wall=5.956s, result=''
[latency] chunk 6/6: buffer=3.0s, transcription=1.152s, total_wall=7.108s, result=''
[latency] 1s audio → 1.275s (RTF 1.27x)
[latency] 2s audio → 1.182s (RTF 0.59x)
[latency] 3s audio → 1.160s (RTF 0.39x)
3 passed in 18.14s
```

Note what these green ticks actually mean: the repo's latency tests assert a **10-second**
upper bound ("conservative so it passes on slow CI"), not the 1 s / 2 s product target.
They independently reproduce my `tiny` figure (~1.15–1.28 s per pass) and they pass — while
the product requirement does not. **A green `pytest` run is not evidence that A4 is met.**

### Test-suite summary

| Run | Result |
|---|---|
| `-m "not slow"` | 43 passed, 3 failed, 9 skipped — all 3 failures are the `venv/` path assumption |
| `-m "not slow"` with `venv` junction | those 3 → **2 passed, 1 skipped** (skip = camera slot busy) |
| `-m slow` | **10 passed**, 1 skipped (macOS-only) |
| `tests/test_latency.py -m slow -s` | **3 passed** (against a 10 s bound, not the 1 s/2 s target) |

---

## Honest verdict — is emulation fast enough to ship to a real user?

**No — not as the primary captioning path. Yes — as a fallback, and only with `tiny`.**

The mission was to prove the repo runs unchanged under emulation. It does, completely:
stock `requirements.txt`, no pins touched, no compiler, no source builds, correct
transcripts in both languages, 53 of 56 applicable tests green and the 3 red ones red for
a path-string reason that has nothing to do with ARM64. As an *engineering* result the
lane is a success.

As a *product* result it fails, and the failure is not marginal:

| Config | partial (need <1 000 ms) | final (need <2 000 ms) | Shippable? |
|---|---:|---:|---|
| `small` (the repo's shipped `config.json` default) | 12 578 ms | 14 124 ms | No — 12× over |
| `base` (CLAUDE.md default) | 2 553–3 053 ms | 3 007–3 039 ms | No — 3× over |
| `tiny` | 1 171 ms | 1 261 ms | Borderline |

Three things make this worse than the raw numbers suggest:

1. **The shipped default is the worst case.** `config.json` says `"model": "small"` and
   `"device": "mlx"`. On this machine `mlx` is unavailable, so `get_backend()` falls
   through to CPU faster-whisper with `small` — the 12–14 s column. A grandma who
   double-clicks the app today gets captions **~13 seconds behind the conversation**.
   That is not "slow captioning", it is a broken experience.
2. **You cannot tune your way out.** Whisper always encodes a padded 30-second mel window,
   so shrinking `rolling_window_s`/`partial_interval_s` changes nothing (my 1 s and 5 s
   windows cost the same). And 4→18 threads buys ~0–16 %. The cost is per-core translated
   SIMD throughput.
3. **`partial_interval_s` is 0.35 s.** At 3 s/pass the transcription worker is
   oversubscribed ~9×. It won't fall over — `_enqueue_transcribe()` coalesces pending
   partials and only finals are guaranteed — but the user sees a partial roughly every
   3 s instead of every 0.35 s. The live-typing effect the product is built around
   disappears.

**What I would actually ship on this machine today**, if forced to ship now:
`tiny`, with `device: "cpu"` pinned so nothing tries MLX. Final captions land ~1.3 s after
you stop speaking, which meets A4's final budget, and partials arrive ~1.2 s apart — laggy
but genuinely usable. The cost is `tiny`-grade Mandarin accuracy, which is the product's
main language. Call it a *degraded mode*, document it as such, and do not let it become
the default.

**Where the emulated lane is unambiguously the winner: the virtual camera.** OBS's
Windows-ARM64 distribution ships its DirectShow filter as an **x64** DLL only. Only an
emulated-x64 (or x86) process can load it, and I demonstrated an emulated x64 Python
opening "OBS Virtual Camera" and pushing a 1280×720 frame. A native-ARM64 producer has no
in-process route to that camera at all. So even if WS-A's native lane wins on STT speed,
**the sink side may still have to run emulated**, or go through OBS Studio's own
out-of-process virtual camera instead of `virtual_cam.py`.

### Recommendation to the orchestrator

- Treat this lane as **the guaranteed floor**, exactly as intended — it is proven and
  it will produce captions. Keep it.
- **Do not ship it with the current defaults.** If the emulated lane becomes the shipping
  lane, `model` must move to `tiny` and `device` to `cpu`.
- **Prioritise WS-A (native ONNX/QNN).** The A4 gap here is 3–12×; nothing on the emulated
  side closes that. Only native ARM64 CPU, or the Hexagon NPU, plausibly can.
- **Feed the x64-filter finding into WS-C/WS-E now** (H-302/H-303) — it constrains the
  architecture of whatever lane wins.

### Loose ends I did not chase

- `small`'s `final 5.0 s` cell (process died at 2.6 GB free RAM). Conclusion unaffected.
- Whether `float32` instead of `int8` is faster under Prism — int8 leans hardest on the
  emulated SIMD paths, so `float32` is not an obvious loss. Untested; low expected value
  given the size of the gap.
- Live mic → WebSocket → overlay (A6) — belongs to WS-E, and `server.py`'s WebSocket path
  is already proven working here.

---

## Reproducing

```powershell
C:\Users\snapd\Downloads\laolao\.venv-x64\Scripts\python.exe `
    C:\Users\snapd\Downloads\laolao\docs\snapdragon\findings\ws_b_verify.py
```

Re-proves H-200…H-203 from scratch, re-downloading the audio fixtures if they are missing.
Exit code 0 means nothing came out REFUTED.
