# WS-E — Independent verification

**Role:** try to break the nine claimed passes. Everything below is a command
and its real output, run on the target machine (Snapdragon X2 Elite, Windows 11
build 28000 ARM64) on the native lane (`.venv-arm64`, python 3.11.9 ARM64).

**Headline:** the STT port is real and better than claimed once measured
honestly — end-to-end caption latency through a WebSocket is **0.88 s**, and a
caption really does reach a WeChat-architecture consumer's pixels. But **two of
the ten checks were passing for reasons that had nothing to do with the product**,
a third could not have failed, and the **Electron shell — the actual application
— does not start on this machine at all**.

---

## Verdict table

| # | Claimed | My verdict | Evidence |
|---|---|---|---|
| A1 | ✅ PASS | ✅ **PASS** | `backend=QnnWhisperBackend arch=ARM64 ctranslate2_loaded=False`, reproduced |
| A2 | ✅ PASS | ✅ **PASS** (check was weak, now fixed) | CER **0.0 %** vs ground truth. Old rule was substring containment, which passes a one-word transcript |
| A3 | ✅ PASS | ⚠️ **PASS, but the check was unsound** | CER **7.7 %** (`停滞`→`停止`), never previously computed. The Traditional detector **cannot detect Traditional** — proven below |
| A4 | ✅ PASS 88/93 ms | ✅ **PASS, and it survives an honest test** | Real end-to-end over WS: first partial **+0.84 s** after speech onset, final **+0.88 s** after the last speech sample (median of 5, real-time paced). 88 ms was compute time only |
| A5 | ✅ PASS | ⚠️ **DOWNGRADE → PARTIAL** | Only proves "digital zeros are not speech". EnergyVAD fires on **any** steady noise above −42 dBFS |
| A6 | ✅ PASS | ⚠️ **PASS, but the check can't tell whose server it graded** | Reproduced. It port-probes 8765 and grades whatever answers — three different servers held that port during my run |
| A7 | ✅ PASS | ✅ **PASS, and now proven at the pixel level** | Live webcam + Chinese caption read **out of the virtual camera by an x64 process** |
| A8 | ✅ PASS | ✅ **PASS, but the harness said FAIL** — harness bug | Real `CoCreateInstance`: `AMD64 → 0x00000000 (S_OK)`, `ARM64 → 0x800700C1` |
| A9 | ✅ PASS | ⚠️ **PASS for steady state; the check misses a live network path** | Under a hard socket block A2/A3/A4 pass with **zero** egress. But `HF_HUB_OFFLINE` does **not** gate the 180 MB S3 QNN download — I watched it fetch |
| A10 | ⬜ (WS-G) | ✅ PASS as of WS-G's run | out of my scope; noted that its engine is not the `.venv-arm64` interpreter |

**Net: 0 downgrades to FAIL, 1 downgrade to PARTIAL (A5), 3 checks I judged
unsound and rewrote, and 1 product-level defect the criteria do not cover
(Electron).**

---

## Harness bugs found (this makes four; the brief listed three)

### #4 — A7/A8 read a registry hive COM does not consult

My first full run:

```
[FAIL ] A8  OBS Virtual Camera: dll=...\obs-virtualcam-module32.dll machine=i386(x86)
            registry_views=32  ->  NO x64 filter — emulated call apps cannot load it
```

But the filter loads fine. `_dshow_filters()` enumerated only
`HKLM\SOFTWARE\Classes`, while the no-admin install this port ships registers
per-user:

```
HKLM\...\CLSID\{A3FCE0F5-...}\InprocServer32              -> ABSENT
HKLM\...\WOW6432Node\CLSID\{A3FCE0F5-...}\InprocServer32  -> obs-virtualcam-module32.dll
HKCU\...\CLSID\{A3FCE0F5-...}\InprocServer32              -> obs-virtualcam-module64.dll   <-- the real one
```

COM resolves through `HKEY_CLASSES_ROOT` = HKCU merged **over** HKLM. Reading
HKLM alone is the same class of mistake as the original `Win32_PnPEntity` one:
a confident verdict from the wrong namespace.

**Fixed** — `_dshow_filters()` now enumerates
`Registry::HKEY_CLASSES_ROOT\CLSID` and `...\WOW6432Node\CLSID`. A7/A8 both
report `views=64/32` and `machine=AMD64(x64)` after the change.

### #5 — A3's Traditional-character detector cannot detect Traditional

A3 intersected the transcript with a hand-written 21-character list.
Run the repo's own Mandarin fixture through `small` on the CPU EP
(`qnn_strict:false`) and the output is *entirely Traditional*:

```
transcribe -> '甚至出現交易幾乎停滯的情況'   (2892 ms, OnnxWhisperBackend, CPU EP)

trad_markers = set("繁體東車馬語說們個過還發沒學國會來時對開關")
'甚至出現交易幾乎停滯的情況' -> A3 verdict PASS   trad_hits=[]
```

OpenCC disagrees — it rewrites four characters: `現 幾 滯 況`. None are on the
list. **A3 would have reported PASS on fully Traditional output**, which is the
one thing A3 exists to catch.

**Fixed** — `traditional_chars()` now asks OpenCC (already installed in this
venv) instead of guessing:

```
traditional_chars('甚至出現交易幾乎停滯的情況') = ['幾', '況', '滯', '現']
traditional_chars('甚至出现交易几乎停止的情况。') = []
```

### #6 — A2/A3 graded accuracy by substring containment

`norm(expect) in norm(got) or norm(got) in norm(expect)`. So:

```
cer("Hello grandma, I miss you very much.", "Hello") = 0.821  -> old substring rule said PASS
```

A backend emitting one correct word out of eight scored a pass.

**Fixed** — both now compute character error rate (A2 budget 15 %, A3 budget
20 %) and print it. Current real numbers: **A2 CER 0.0 %**, **A3 CER 7.7 %**.

### #7 — A8 inferred loadability from a PE header

It never called `CoCreateInstance`. **Fixed** — A8 now runs the real thing
in-process and, if an x64 interpreter exists, out-of-process under it:

```
CoCreateInstance probes: ['ARM64 0x800700C1', 'AMD64 0x00000000']
```

If no x64 interpreter is present the check now says so explicitly
(`x64 loadability is INFERRED from the PE header, not measured`) rather than
quietly passing on inference.

All four fixes are in `docs/snapdragon/acceptance/check.py`. **Every one makes
the check stricter.** No check was weakened. Post-fix full run: `PASS=9 FAIL=0
SKIP=0 BLOCKED=1` (A6 blocked only because I had shut my server down; it passes
with one running).

---

## A3 — does the transcript actually match? (the brief's specific question)

Ground truth `tests/fixtures/chinese_speech.txt` (AISHELL-1 BAC009S0764W0121):

```
raw              ref='甚至出现交易几乎停滞的情况' (13) hyp='甚至出现交易几乎停止的情况。' (14) edits=2 CER=15.4%
punct-stripped   ref='甚至出现交易几乎停滞的情况' (13) hyp='甚至出现交易几乎停止的情况' (13) edits=1 CER=7.7%
```

**One substitution: 停滞 → 停止.** Semantically near-identical ("stalled" vs
"stopped"), a normal `whisper-base` slip. So the claim "A3 passes" is true and
the transcript is genuinely good — but the harness had no idea, because it
never looked. The live streaming path is slightly worse: it consistently
produces `停制`, which is not a word:

```
[final] 甚至出现交易几乎停制的情况。      (server.py rolling-window path, 20+ consecutive runs)
```

Worth knowing: the direct `.transcribe()` A3 grades and the rolling-window path
the user actually gets do not produce the same text.

### A3 never exercises OpenCC at all

`t2s` lives in `server.py`'s `UtteranceProcessor` (lines 208–253). A3 calls
`be.transcribe()` directly, which bypasses it. A3 passes because the QNN `base`
model happens to emit Simplified natively — not because the port's
Traditional→Simplified conversion works. NORTH_STAR says A3 proves "OpenCC
applied". It does not.

---

## A4 — the honest end-to-end number (the brief's specific question)

88 ms was `backend.transcribe()` on a warm backend with the whole utterance in
hand. I streamed the fixture at **real-time pace**, as a microphone would, and
measured how far behind the audio each caption arrives
(`scratchpad/e2e_latency.py`, 5 reps, against my own `.venv-arm64` server, with
OBS + the virtual camera running):

```
clip duration                     4.20s
first-partial after speech start  median 0.839s  min 0.827  max 0.850   (n=5)
FINAL after last speech sample    median 0.876s  min 0.853  max 0.887   (n=5)
A4 budget: final < 2.0s  ->  PASS
```

Then the last leg — caption to *pixels in the far end's video* — measured by an
x64 process reading the virtual camera while the utterance played, counting
caption glyph pixels in the lower band:

```
first YELLOW (partial) ink in vcam pixels : +0.648s after speech start
```

(Partials render `#ffe066`, finals `#ffffff`. My first attempt counted only
white pixels and I wrongly concluded partials never reached the camera — my
error, corrected.)

**Honest verdict: A4 passes on the real measurement, not just the synthetic
one.** Caption ink is in the outgoing video ~0.65 s after the speaker starts,
and the complete final text lands ~0.88 s after they stop. The synthetic 88 ms
is ~10× optimistic as a description of user experience, but the user experience
still fits the budget.

---

## A8 — a real x64 consumer, real pixels (the brief's specific question)

`scratchpad/com_probe.py`, raw ctypes, same calls a call app makes:

```
############ ARM64 native consumer ############
[direct] CoCreateInstance(OBS VCam, IBaseFilter) -> 0x800700C1 ERROR_BAD_EXE_FORMAT
  ASUS FHD webcam       BindToObject=0x00000000 S_OK                pins=1
  OBS Virtual Camera    BindToObject=0x800700C1 ERROR_BAD_EXE_FORMAT pins=-

############ x64 emulated consumer (WeChat/Zoom arch) ############
[direct] CoCreateInstance(OBS VCam, IBaseFilter) -> 0x00000000 S_OK
[direct] EnumPins -> 0x00000000
  ASUS FHD webcam       BindToObject=0x00000000 S_OK   pins=1
  OBS Virtual Camera    BindToObject=0x00000000 S_OK   pins=1
```

Confirms WS-C's mutual-exclusivity finding exactly, from an independent
implementation. Then real pixels via PyAV under x64:

```
opened: rawvideo nv12 1280x720
frames=40 mean=102.2 std=88.4 non_black_pixels=740428/921600 (80.3%)
VERDICT: NON-BLACK REAL FRAME
```

**Trap worth recording: "non-black frame" is not a valid test.** With OBS *not
running at all* the same read returns a 99.2 %-non-black frame — it is the OBS
placeholder logo (`scratchpad/vcam_noobs.png`). A check that only asserts
non-black would pass on a machine where nothing works.

The real proof is `scratchpad/vcam_caption.png`: live webcam with
「甚至出现交易几乎停制的情况。」 composited over it, captured by an
x64-emulated process out of "OBS Virtual Camera", with the caption produced by
`server.py` on the Hexagon NPU from streamed audio. That is the whole product
in one frame.

---

## A9 — other network paths (the brief's specific question)

**Yes, there is one, and it is live.** `backends/onnx_whisper_backend.py:566`
uses `urllib.request.urlretrieve` against
`https://qaihub-public-assets.s3.us-west-2.amazonaws.com/...`. `HF_HUB_OFFLINE`
has no effect on urllib. Proven — this run had A9's exact env vars set and
still fetched 180 MB:

```
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ... (empty model dir)
INFO:laolao.backends.onnx:Downloading Qualcomm AI Hub asset https://qaihub-public-assets.s3...
  -> asset.zip  180,942,461 bytes on disk
```

So A9's env-var sandbox is not a network sandbox. I wrote a real one
(`scratchpad/netblock/sitecustomize.py`) that refuses every non-loopback
`connect`/`connect_ex`/`create_connection`/`getaddrinfo`. Two runs:

**Warm cache, hard block, and no `HF_HUB_OFFLINE` at all:**

```
[NETBLOCK] active — only loopback is reachable
[PASS ] A2  ... CER=0.0%
[PASS ] A3  ... CER=7.7%
[PASS ] A4  partial=92ms final=96ms
PASS=3  FAIL=0
```

Zero egress attempts. **A9's claim is true, and stronger than the harness
proves.**

**Cold cache, hard block — what a fresh machine reaches for:**

```
[NETBLOCK] outbound connection attempted to ('qaihub-public-assets.s3.us-west-2.amazonaws.com', 443)
[NETBLOCK] outbound connection attempted to ('huggingface.co', 443)
```

Two hosts: the S3 QNN asset and the HF tokenizer repo. Cold start with the
network available works fine and is fast:

```
COLD LOAD OK backend= QnnWhisperBackend 33s
TRANSCRIBE: 'Hello grandma, I miss you very much.' 128ms
```

**Recommendation:** A9 should run the child under the socket block, not just
the env vars. Env vars only discipline `huggingface_hub`.

---

## Downgrade: A5

The check feeds six chunks of **digital zeros** as "silence". Real rooms are
not digital zeros. EnergyVAD is a bare RMS threshold at `silence_rms=0.008`:

```
chunk=4000 samples, silence_rms threshold=0.008
  room noise @  -45 dBFS (rms=0.0056) -> is_speech [F,F,F,F,F,F]
  room noise @  -40 dBFS (rms=0.0100) -> is_speech [F,T,T,T,T,T]  <== FALSE SPEECH
  room noise @  -30 dBFS (rms=0.0316) -> is_speech [F,T,T,T,T,T]  <== FALSE SPEECH
  room noise @  -20 dBFS (rms=0.1000) -> is_speech [F,T,T,T,T,T]  <== FALSE SPEECH
```

Any steady noise above about −42 dBFS — a fan, a fridge, an air conditioner,
mic gain set a bit high — holds the gate permanently open, and Whisper is then
asked to transcribe noise. That is not hypothetical: the very first A6 run of
this session produced

```
texts=['Thank you very much.', 'Thank you very much. Hello grandma. I miss you.', ...]
```

`Thank you very much.` is Whisper's canonical hallucination on non-speech.
A5 as written can never see this, because zeros are the one input an energy VAD
is guaranteed to reject. **A5 → PARTIAL.** And note Silero cannot be installed
here at all (H-011), so there is no better VAD to fall back to.

A real A5 would feed a **recorded quiet room** (not zeros), a non-speech noise
clip (typing, traffic), and speech, and require the first two to stay closed.

---

## Regressions and defects found

### D1 — Electron does not start the engine on this machine. (blocking)

`electron/main.js:27` hardcodes the venv:

```js
function venvPyFor(root) {
  return IS_WIN ? path.join(root, 'venv', 'Scripts', 'python.exe') : ...
}
```

There is no `venv/` in this repo — the port created `.venv-arm64` and
`.venv-x64`. `node --check` passes on both `main.js` and `preload.js`, and both
windows do open, but:

```
[startup] engine root: C:\Users\snapd\Downloads\laolao
[server]      spawn error: spawn C:\Users\snapd\Downloads\laolao\venv\Scripts\python.exe ENOENT
[virtual_cam] spawn error: spawn C:\Users\snapd\Downloads\laolao\venv\Scripts\python.exe ENOENT
[server] crashed 5× in a row — giving up.
[virtual_cam] crashed 5× in a row — giving up.
```

The Electron app never runs a caption engine or a frame sink on this port.
Nothing in A1–A10 covers this, because every criterion drives the Python
directly.

Two more problems in the same log:

```
[startup] caption server ready on :8765
```

— that was **another process's** server. `waitForPort()` cannot distinguish
"our child bound the port" from "something else did", so Electron reported the
engine healthy while its own engine was in a crash loop. Same failure shape as
harness bug #3.

```
[OUTPUT] startCamera: getUserMedia FAILED NotReadableError: Device in use (format=720p30)
  ... 720p15, 480p30, default all fail ...
[OUTPUT] camera: ladder exhausted — released device, handing off to user
[diag] capturePage: frame #150 1280x720 black=true vcSocket=false
```

OBS's Video Capture Device already owns the single webcam. **The two
architectures shipped by this port collide**: WS-C's design has OBS own the
camera and composite the overlay, while `main.js` still has the Electron output
window open the camera and push frames to `virtual_cam.py`. Run both and
Electron gets black frames forever.

### D2 — `pytest tests/` is red on the target platform

```
FAILED tests/test_windows_headless.py::test_faster_whisper_import  - ModuleNotFoundError
FAILED tests/test_windows_headless.py::test_pyvirtualcam_import    - ModuleNotFoundError
FAILED tests/test_windows_headless.py::test_server_starts_and_binds   - FileNotFoundError [WinError 2]
FAILED tests/test_windows_headless.py::test_server_accepts_websocket  - FileNotFoundError [WinError 2]
4 failed, 37 passed, 14 skipped, 11 deselected
```

The first two assert imports that are **impossible** on win-arm64 (H-001,
H-002 both REFUTED) — the suite asserts the port cannot exist. The last two
hardcode `venv/Scripts/python.exe`, same root cause as D1. `pytest` had to be
installed into `.venv-arm64` first; it was not there.

### D3 — cross-platform dispatch is intact (checked, no regression)

I simulated the platform probes and recorded the first backend module
`get_backend()` reaches for:

```
Darwin   arm64   device=mlx   -> backends.mlx_backend
Darwin   arm64   device=auto  -> backends.mlx_backend
Windows  AMD64   device=mlx   -> backends.faster_whisper_backend
Windows  AMD64   device=auto  -> backends.faster_whisper_backend
Windows  AMD64   device=auto  cuda=True -> backends.faster_whisper_backend
Linux    x86_64  device=auto  -> backends.faster_whisper_backend
Windows  AMD64   device=onnx  -> backends.onnx_whisper_backend
Windows  ARM64   device=auto  -> backends.onnx_whisper_backend
```

The unconditional ARM64 branch does **not** leak. Minor wart: on x86-64/macOS
with `device:"onnx"`, `_arm64_cfg()` still runs and would silently rewrite
`model:"small"` → `"base"` on a machine where `small` is perfectly fine.

### D4 — `qnn_strict:false` works, and is a trap

It does what it says:

```
qnn_strict=True : model 'small' -> 'base', device 'mlx' -> 'auto'
qnn_strict=False: model 'small' -> 'small', device 'mlx' -> 'auto'
backend=OnnxWhisperBackend  loaded in 4.4s
transcribe -> '甚至出現交易幾乎停滯的情況'  in 2892 ms
```

But note what the escape hatch buys: **31× slower** (2892 ms vs 93 ms), *and*
Traditional output — which the pre-fix A3 would have graded PASS. The
documented opt-out silently breaks the Simplified-Chinese promise.

### D5 — QNN asset extraction breaks on long install paths

Installing under a ~200-char path left `asset.zip` unextracted and produced:

```
QNN Whisper backend unavailable ([Errno 2] No such file or directory: '...\metadata.json');
falling back to CPU EP
```

The same install at `C:\lt-cold` works perfectly (33 s, `QnnWhisperBackend`,
128 ms). Classic Windows `MAX_PATH`. The consequence is the bad kind: the
Hexagon NPU — the entire point of this port — silently disengages and the user
gets a ~25× slower CPU path with no error. Extract to a short temp dir, or
enable long paths.

---

## Checks I judge too weak to trust

| Check | Why | What a real test looks like |
|---|---|---|
| **A5** | Feeds digital zeros; an energy VAD cannot fail that | Recorded quiet room + a non-speech noise clip (typing, traffic) + speech; require the first two to stay closed |
| **A6** | Port-probes 8765 and grades whatever answers. Three different servers held it during my session, one on the bare system interpreter | Spawn the server itself, capture its pid and interpreter path, and assert the caption came from *that* process |
| **A9** | Env vars only discipline `huggingface_hub`; urllib egress is invisible to it | Run the child under the socket-level block (`scratchpad/netblock/sitecustomize.py`) |
| **A4** | Measures compute time on a warm backend with the full utterance in hand — not what a user feels | Real-time-paced stream with a lag measurement (`scratchpad/e2e_latency.py`); ideally assert on caption ink in the virtual camera |
| **A7** | Registry presence only. A registered filter with OBS not running still yields a plausible-looking frame | Read frames from the device and require the caption text to be present — a non-black frame is the OBS placeholder |
| **all** | Nothing in A1–A10 launches `electron/`, which is the actual product | Launch Electron, assert both windows open **and** the engine child process is alive |

---

## Bottom line

**Would I hand this to a non-technical user to call their grandmother today?
No — but the reason is narrow and fixable, and it is not the hard part.**

What genuinely works, verified independently:

- Whisper on the Hexagon NPU, native ARM64, no ctranslate2, no torch,
  no compiler. Cold install in 33 s.
- Correct English (CER 0 %) and correct Mandarin (CER 7.7 %, one homophone).
- **0.88 s** from "stopped speaking" to the complete caption — real
  measurement, real-time paced, with OBS running. Caption ink in the outgoing
  video **0.65 s** after speech starts.
- A caption composited over the live webcam and read out of "OBS Virtual
  Camera" by a **WeChat/Zoom-architecture** process. That is the promise.
- Genuinely offline once the models are cached — verified at the socket layer.

Why grandma cannot use it yet:

1. **The application does not run.** `electron/main.js` looks for `venv/`; the
   port built `.venv-arm64`. Every path I verified was driven by hand from a
   terminal. This is a one-line fix and it is the only thing standing between
   the evidence above and a working app — but until it is fixed, "Laolao works
   on Snapdragon" means "the pieces work when an engineer wires them together."
2. **Two architectures are in the box at once** and they fight over the single
   webcam. Somebody has to decide: OBS composites (WS-C's design, the one that
   demonstrably reaches WeChat), or Electron composites and pushes to
   `virtual_cam.py`. Shipping both gives a black frame.
3. **The VAD will hallucinate in a normal room.** Above −42 dBFS of steady
   noise the gate never closes and Whisper captions the fan. Grandma's kitchen
   is louder than that. Silero cannot be installed on this platform, so this
   needs either a better energy heuristic (adaptive noise floor) or an ONNX
   Silero export.
4. **The camera is x64-only or ARM64-only, never both**, and the failure mode
   is a camera that appears in the picker and shows nothing. Unavoidable
   without an ARM64X filter (H-210) — but it must be in the troubleshooting
   docs in plain language.

Fix #1 and #2 and I would put this in front of a real user with #3 as a known
rough edge. The STT port itself — the part everyone expected to be impossible —
is the strongest thing here, and it stands up to hostile measurement.

---

## Artifacts

Scripts written for this verification (in the session scratchpad, not the repo):
`com_probe.py` (raw-ctypes CoCreateInstance / DirectShow enumeration),
`read_vcam.py` and `band_series.py` (x64 virtual-camera pixel readers),
`e2e_latency.py` (real-time-paced WebSocket latency),
`netblock/sitecustomize.py` (socket-level offline enforcement).

Processes started and stopped: two `server.py` instances (ports 8765, 8791),
OBS via `laolao-vcam-setup.ps1`, one Electron instance, several x64 readers.
All terminated; ports released; the OBS scene collection is back on the default
port 8765.

Files changed by me: `docs/snapdragon/acceptance/check.py` only — four checks
made stricter (`cer()`, `traditional_chars()`, HKCR enumeration, real
`CoCreateInstance`). No check was weakened. `STATUS.md` and `HYPOTHESES.md`
untouched.
