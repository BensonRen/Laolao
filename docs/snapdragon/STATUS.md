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
| A4 | latency partial <1.0s / final <2.0s | ✅ **PASS** — **88 ms / 93 ms** on the Hexagon NPU (11× and 21× inside budget) |
| A5 | VAD gates speech vs silence | ✅ **PASS** (EnergyVAD; Silero not yet installed natively) |
| A6 | live mic → overlay captions | ✅ **PASS** — PCM → WS → VAD → NPU → 3 caption msgs incl. a final |
| A7 | virtual camera registered | ✅ **PASS** — `OBS Virtual Camera` in DirectShow category, views 64/32 |
| A8 | camera loadable by the call apps that need it | ✅ **PASS** — filter DLL is `AMD64(x64)`, so x64-emulated WeChat/Zoom can load it |
| A9 | fully offline operation | ✅ **PASS** — A2 re-run with HF_HUB_OFFLINE=1 exits 0 |
| A10 | one-click launch for a non-technical user | ⬜ not started |

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
