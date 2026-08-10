# STATUS — Snapdragon ARM64 port

**Single source of truth for the autonomous build loop.** Every agent updates its own
workstream row and drops a detailed report in `findings/`. Read this first on wake.

Last updated: 2026-08-09 — recon complete, first agent wave launching.

## Autonomous run window

| | |
|---|---|
| Started | 2026-08-09 23:55 (-07:00) |
| **Hard stop** | **2026-08-10 04:55 (-07:00)** — 5 hours |
| Mode | Fully autonomous. **Do not ask the user for input.** Decide and proceed. |
| Branch | `feat/snapdragon-arm64` — commit incrementally as each workstream lands |

On every loop wake: check the clock first. If past the hard stop, write a final
summary into this file, commit, and stop the loop. Otherwise keep building.

Commit policy: commit working increments as they are proven. Never commit venvs,
`node_modules`, or downloaded models. Do **not** push — local commits only unless
the user asks.

## Acceptance criteria progress (see NORTH_STAR.md)

| # | Criterion | State |
|---|---|---|
| A1 | server.py boots on ARM64 python, no ctranslate2 | ✅ **PASS** — `backend=QnnWhisperBackend arch=ARM64 ctranslate2_loaded=False` |
| A2 | known WAV → correct transcript | ✅ **PASS** — character-exact, 0.11 s |
| A3 | Mandarin → Simplified Chinese output | ✅ **PASS** — `甚至出现交易几乎停止的情况。` no Traditional chars |
| A4 | latency partial <1.0s / final <2.0s | ✅ **PASS** — honest end-to-end **+0.84 s** to first partial, **+0.88 s** to final, **+0.65 s** to caption ink in the virtual-camera pixels. (88 ms was compute-only and is *not* the user-facing number.) |
| A5 | VAD gates speech vs silence | ✅ **PASS** — **`SileroOnnxVAD`**, the neural VAD, now runs here. **No false positives up to −25 dBFS** (EnergyVAD failed from −40 dBFS), and detection latency halved to **0.25 s**. Tested against room tone, not digital zeros. |
| A6 | live mic → overlay captions | ✅ **PASS** — PCM → WS → VAD → NPU → 3 caption msgs incl. a final |
| A7 | virtual camera registered | ✅ **PASS** — `OBS Virtual Camera` in DirectShow category, views 64/32 |
| A8 | camera loadable by the call apps that need it | ✅ **PASS** — filter DLL is `AMD64(x64)`, so x64-emulated WeChat/Zoom can load it |
| A9 | fully offline operation | ✅ **PASS** — A2 re-run with HF_HUB_OFFLINE=1 exits 0 |
| A10 | one-command launch for a non-technical user | ✅ **PASS** — `Laolao-arm64.bat`, cold start to running in ~6 s, per-user (`HKCU=True HKLM=False`), no admin |

> **A7/A8 caveat, and why they are graded separately.** Registration is not
> loadability. The filter is registered in the 64-bit registry view, so a
> *native ARM64* app enumerates "OBS Virtual Camera" happily and then fails at
> `CoCreateInstance`, because the DLL behind it is AMD64. Visible-but-unusable
> is a real failure mode; A8 exists to catch it.
>
> **The harness itself was wrong first.** A7 originally enumerated
> `Win32_PnPEntity` and confidently reported FAIL — but a virtual camera is a
> COM filter, not a PnP device, so that check could never have passed no matter
> how correct the install was. Fixed to enumerate
> `CLSID\{860BB310-5D01-11D0-BD3B-00A0C911CE86}\Instance`. Worth remembering:
> a green/red light from the wrong namespace is worse than no light at all.

Legend: ⬜ not started · 🔄 in progress · ✅ proven with evidence · ❌ blocked · ⚠️ partial

## Workstreams

| ID | Workstream | Owner | State | Notes |
|---|---|---|---|---|
| WS-A | STT native ARM64 (ONNX CPU → QNN NPU) | agent | ✅ **done — lane winner** | Whisper on the **Hexagon NPU** via `onnxruntime-qnn` + Qualcomm AI Hub's precompiled export for `qualcomm-snapdragon-x2-elite`. base ~130 ms, large-v3-turbo ~530 ms. H-100..H-105 all confirmed. |
| WS-B | STT via x64 Prism emulation (fallback) | agent | ✅ done — **not shippable as primary** | Accurate but 3–12× over the latency budget. `tiny` is the only near-viable size. Kept as a documented degraded fallback. Found the x64-only DShow filter constraint. |
| WS-C | OBS ARM64 + virtual camera | agent | ✅ done | OBS ARM64 composites webcam + overlay browser source and publishes the camera itself — `pyvirtualcam` not needed. Registration works **per-user, no admin**. |
| WS-D | Node.js + Electron ARM64 shell | agent | ✅ done | Node/Electron/electron-builder all native arm64. Proved the full path: ARM64 Electron → TCP → emulated x64 sink → OBS filter → x64 consumer. Found the silent caption-cropping bug. |
| WS-F | Ground-truth audio fixtures | agent | ✅ done | Added a Windows SAPI path to the macOS-only generator; sourced AISHELL-1 (Apache-2.0) for Mandarin ground truth, parsed from the corpus transcript rather than typed in. |
| WS-G | One-click launcher (A10) | agent | 🔄 running | The last unproven criterion. |
| WS-E | Independent adversarial verification | agent | 🔄 running | Auditing the harness as much as the port — it has already been wrong three times. |

Wave 1 launched 2026-08-09 23:56; wave 2 (WS-G, WS-E) 2026-08-10 00:50. Each agent
writes only to its own `findings/WS-<id>-*.md`; the orchestrator merges results into
this file and `HYPOTHESES.md` to avoid concurrent-edit conflicts.

## Decided architecture

```
native ARM64  server.py  ──WebSocket :8765──▶  overlay (captions)
   └─ QnnWhisperBackend on the Hexagon NPU, EnergyVAD

camera, either:
 (a) OBS ARM64 composites webcam + overlay browser source → its own virtual camera
 (b) native ARM64 Electron ──JPEG/TCP :8766──▶ emulated x64 virtual_cam.py → OBS DShow filter
                                                        ▲ only this thin sink is emulated
```

Emulation is used for exactly one thing — the frame sink — because that is the one
part it handles well (A8) and STT is the part it ruins (A4).

## Outcome — all 10 acceptance criteria pass

Independently re-run by the orchestrator: **PASS=10 FAIL=0 SKIP=0 BLOCKED=0**.

Laolao runs on Snapdragon X2 with Whisper on the **Hexagon NPU**. The native lane
was not the risky option that barely worked — it beat x64 emulation by ~25× and is
the only lane that meets the latency budget at all.

### Shipping configuration

`Laolao-arm64.bat` → `docs/snapdragon/launch.ps1` → **OBS composites** the webcam plus
`overlay/index.html` as a browser source and publishes the virtual camera itself.
Cold start to running ≈ 6 s. Per-user registration (`HKCU`), **no admin**.

The Electron path (native ARM64 Electron → JPEG/TCP → emulated x64 `virtual_cam.py`)
is the better *product* — it has the control window and toolbar — and after the fixes
in this branch it does run end-to-end:

```
[obs] detection: ...\obs-arm64\data\obs-plugins\win-dshow
[startup] virtual camera ready on :8766
[vcam] socket connected — frames now reach the virtual camera
capturePage: first frame 1280x720        ← clamp fix holds (was 1008x720)
capturePage: FIRST NON-BLACK frame at frame #19
```

Camera acquisition was investigated and **fixed** (see
`findings/WS-H-camera-reliability.md`). The hang was real but not general: three
consecutive cold launches now succeed (`getUserMedia` 648–669 ms, 240–245 frames /
20 s). It wedges only when the device is still held — Windows releases a USB webcam
lazily — and `startCamera()` now races a 12 s timeout, retrying without the saved
`deviceId` before walking the format ladder, so a busy camera degrades to a logged,
recoverable failure instead of an unbounded black screen.

Frame rate was **14 fps against a 30 fps target**, now **22 fps** — the cause was not
the GPU at all but `setTimeout(tick, 1000/FPS)` waiting a full frame period *after*
each frame's work, making the real period `work + 33ms`. Fixed to schedule at a fixed
rate. Per-stage costs are now in the heartbeat:

```
frame #600 1281x720 ... | 21.8fps avg capture=10.5ms scale=5.4ms encode=4.6ms
```

**Negative result worth not repeating.** This display has `scaleFactor 1.5`, so
`capturePage()` returns 1920×1080 physical for a 1280×720 DIP window and every frame
was downscaled 2.25×. Sizing the window in physical pixels (with `zoomFactor`
compensation so layout is unchanged) *did* cut capture cost 13 ms → 10.5 ms — and
left frame rate unchanged at 21.8 fps. The bottleneck is `capturePage()`'s IPC round
trip, not pixel throughput, so the change was reverted rather than kept for a
composition risk it did not pay for.

Still requires `LAOLAO_PYTHON_CAMERA` pointed at an x64 interpreter (pyvirtualcam has
no win-arm64 wheel), and only one program may hold the webcam at a time. Promoting
this path to default now depends on closing the last 22 → 30 fps gap, which lives
inside `capturePage`.

### Known limitations — all measured, none hidden

| Limitation | Detail |
|---|---|
| Model sizes | Only `tiny` / `base` / `large-v3-turbo` have Qualcomm exports. `small`/`medium` silently fall back to CPU (~25× slower); `_arm64_cfg()` substitutes loudly. |
| VAD | **Resolved.** The `silero-vad` package needs torch, but the weights are plain ONNX — `SileroOnnxVAD` runs them directly on onnxruntime, no torch, no package. Clean up to −25 dBFS where EnergyVAD failed from −40 dBFS. EnergyVAD remains the fallback if the 2.3 MB model cannot be fetched. |
| Echo cancellation | Absent on the OBS path (Python owns the mic, not Chromium). |
| Call-app arch | One 64-bit CLSID slot; the x64 filter is registered for x64 WeChat/Zoom. An **ARM64-native** Zoom would need `-Arch arm64`. The wrong choice still shows in the picker and delivers a dead feed. |
| First run | Needs network for a ~180 MB QNN asset fetched over plain `urllib`; `HF_HUB_OFFLINE` does not cover it. Steady state is fully offline. |
| Install path | Long paths silently break QNN extraction → CPU fallback with no error. Setup warns past 90 chars. |
| Unverified | Nobody has selected the camera inside real WeChat or Zoom. x64 DirectShow read-back is the proxy. |

### What this port cost in wrong turns

Seven harness bugs, every one of them a confident verdict about nothing:
PnP enumeration for a COM filter; a single-chunk probe against a 3-frame majority
filter; a read-timeout reported as "no server"; a Traditional-Chinese detector that
could not detect Traditional; accuracy graded by substring containment; the wrong
registry hive; and a launcher check that a stub `echo` would pass.

The through-line of this platform is that **failures are silent** — QNN falls back to
CPU without erroring, `device: "mlx"` picked CPU over the NPU, Chromium's window clamp
ate the captions while every diagnostic read healthy. Grading instruments needed as
much scepticism as the code.

## Isolation rules (avoid agents stomping each other)

- WS-A owns venv `.venv-arm64` and dir `docs/snapdragon/findings/`
- WS-B owns venv `.venv-x64` and its own x64 Python install
- WS-C owns `C:\Users\snapd\Downloads\laolao-tools\obs-arm64\` (outside the repo)
- WS-D owns `electron/node_modules` and the Node install
- **Never** `pip install` into the bare system interpreter — always a venv
- Downloads go to `C:\Users\snapd\Downloads\laolao-tools\` (outside the repo, not committed)

## Current blockers

_none recorded yet_

## Next actions

1. Launch WS-A, WS-B, WS-C, WS-D in parallel.
2. Resolve H-100 (ONNX Whisper runs) and H-200 (x64 Prism works) — these decide the lane.
3. Once a lane produces a transcript, wire `server.py` and run WS-E.
