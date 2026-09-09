# Windows on ARM64 (Snapdragon) — launcher internals

Everything a Snapdragon PC needs to run Laolao lives in this folder. A user
never runs these directly; `Laolao-arm64.bat` at the repository root calls
them.

| File | Role |
|---|---|
| `launch.ps1` | What the double-click does: runs setup if anything is missing, starts the caption engine and OBS, and prints which camera to pick. `-Stop`, `-Status`, `-Setup`, `-Arch` flags. |
| `setup-arm64.ps1` | First-run setup: finds a native ARM64 Python, builds `.venv-arm64`, installs `requirements/arm64.txt` and `requirements/arm64-nodeps.txt`, pre-downloads the NPU model, delegates the camera to the script below. Idempotent. |
| `laolao-vcam-setup.ps1` | Downloads portable OBS ARM64 and registers its virtual camera **per user, without administrator rights**. Writes the OBS profile and scene. |
| `laolao-obs-scene.json` | The OBS scene the camera script installs: your webcam plus `overlay/index.html` as a browser source. |

Why this platform has its own lane, in one paragraph: `faster-whisper` needs
`ctranslate2` and `openai-whisper` needs `torch`, and neither publishes a
Windows-ARM64 build, so the only inference runtime available is `onnxruntime`.
Its `onnxruntime-qnn` package reaches the Hexagon NPU, and Qualcomm AI Hub
publishes a precompiled Whisper for it. `pyvirtualcam` has no ARM64 wheel
either, so OBS composites the captions and owns the camera instead of the
Electron app. The backend that implements this is
`backends/onnx_whisper_backend.py`; the decision logic that selects it, and
loudly rewrites config values that are wrong on this platform, is in
`backends/__init__.py`.

The verification behind the README's support claim is
`tests/acceptance_arm64.py`: ten end-to-end criteria from "boots without
ctranslate2" through a live WebSocket caption round trip, a registered and
loadable virtual camera, fully offline operation, and the launcher itself.
Run it on the device with `server.py --no-mic` already started.
