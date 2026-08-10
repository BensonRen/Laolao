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
| A1 | server.py boots on ARM64 python, no ctranslate2 | ⬜ not started |
| A2 | known WAV → correct transcript | ⬜ not started |
| A3 | Mandarin → Simplified Chinese output | ⬜ not started |
| A4 | latency partial <1.0s / final <2.0s | ⬜ not started |
| A5 | VAD gates speech vs silence | ⬜ not started |
| A6 | live mic → overlay captions | ⬜ not started |
| A7 | webcam + captions → virtual camera pixels | ⬜ not started |
| A8 | camera selectable in a call app picker | ⬜ not started |
| A9 | fully offline operation | ⬜ not started |
| A10 | one-click launch for a non-technical user | ⬜ not started |

Legend: ⬜ not started · 🔄 in progress · ✅ proven with evidence · ❌ blocked · ⚠️ partial

## Workstreams

| ID | Workstream | Owner | State | Notes |
|---|---|---|---|---|
| WS-A | STT native ARM64 (ONNX CPU → QNN NPU) | agent | 🔄 launched | H-100..H-105. Highest risk, highest value. |
| WS-B | STT via x64 Prism emulation (fallback) | agent | 🔄 launched | H-200..H-203. Near-certain; guarantees a shippable build. |
| WS-C | OBS ARM64 + virtual camera | agent | 🔄 launched | H-300..H-305. OBS ARM64 zip exists. |
| WS-D | Node.js + Electron ARM64 shell | agent | 🔄 launched | H-400..H-403. |
| WS-E | End-to-end verification | agent | ⬜ | Runs only once A-criteria have candidates. |

Wave 1 launched 2026-08-09. Each agent writes only to its own
`findings/WS-<id>-*.md`; the orchestrator merges results into this file and
`HYPOTHESES.md` to avoid concurrent-edit conflicts.

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
