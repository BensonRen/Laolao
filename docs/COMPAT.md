# Call-App Compatibility Matrix

Laolao publishes its caption overlay as **"OBS Virtual Camera"**. This document
tracks whether real call apps can (a) see that camera in their picker and
(b) render its frames once selected.

## Automated pre-check (run this first)

```bash
# macOS — self-contained: starts its own producer, enumerates via AVFoundation
# (the same API WeChat/Zoom/FaceTime use), and round-trips a test pattern.
./venv/bin/python -m pytest tests/test_callapp_compat_macos.py -v -s

# Full-pipeline variant (requires Laolao.app / server + virtual_cam.py running):
./venv/bin/python tests/test_virtualcam_macos.py
```

If the automated test passes, the camera is enumerable and delivers real
pixels — an app failing after that is app-specific behavior, which is what the
matrix below records.

## Matrix

Cells: **picker** = camera visible in the app's picker · **renders** = selected
feed shows Laolao output (not black/frozen). Fill in with ✅ / ❌ / — and the
app version tested.

| App | macOS picker | macOS renders | Windows picker | Windows renders | Notes / versions |
|---|---|---|---|---|---|
| WeChat | ✅ automated 2026-07-05* | ⬜ untested | ⬜ untested | ⬜ untested | *AVFoundation enumeration + pattern round-trip pass on macOS 26.3.1, WeChat 4.1.7 installed; manual in-call check still needed |
| Zoom | ⬜ untested | ⬜ untested | ⬜ untested | ⬜ untested | |
| FaceTime | ⬜ untested | ⬜ untested | — | — | macOS only |
| WhatsApp desktop | ⬜ untested | ⬜ untested | ⬜ untested | ⬜ untested | |
| Messenger (web) | ⬜ untested | ⬜ untested | ⬜ untested | ⬜ untested | Browser getUserMedia picker |

## Manual test procedure per app

General setup: launch Laolao first (so the virtual camera has a live producer),
then open the call app. Speak so captions are visible; verify on the receiving
device (ideally a phone) that face + captions render and remain legible.

- **WeChat (macOS 4.x)** — no camera setting in Preferences; selection is
  in-call. Start a video call, hover the video window and click/hover the
  **camera-switch button** in the call controls, then pick "OBS Virtual
  Camera" from the dropdown. (Verified against third-party docs for WeChat
  4.x on Mac; tested locally with WeChat 4.1.7 present. See
  [CamIn's WeChat guide](https://help.camin.cn/features/camin-virtual-camera/how-to-use-camin-in-wechat).)
- **WeChat (Windows)** — same pattern: during a call, click the **Switch
  Camera** button on the video window and choose the virtual camera.
- **Zoom** — Settings → Video → Camera dropdown, or the **^** next to the
  Start/Stop Video button in a meeting.
- **FaceTime (macOS)** — during a call: **Video menu** in the menu bar →
  camera list.
- **WhatsApp desktop** — Settings → Calls (or the camera selector shown
  in-call) → choose camera.
- **Messenger (web)** — in-call settings gear → camera device dropdown
  (standard browser device picker; also governed by the browser's camera
  permission).

## Known caveats

- **One producer at a time.** Only one app can *feed* "OBS Virtual Camera".
  If OBS Studio (or a stray `virtual_cam.py`) is running, Laolao's producer
  fails with "device in use" — and vice versa. Quit other producers first.
- **Restart apps after first install.** Call apps enumerate cameras at
  launch. After the OBS Camera Extension first registers (or after
  (re)installing OBS), fully quit and reopen the call app or it won't list
  the new device.
- **macOS 14+ requires the OBS *Camera Extension*** (bundled with OBS Studio
  28+; enable under System Settings → General → Login Items & Extensions →
  Camera Extensions). The legacy DAL plugin is deprecated and invisible to
  modern apps.
- **Windows requires OBS Studio 28+** for its DirectShow virtual-camera
  driver, and a GUI session (no SSH).
- **Terminal camera permission** is needed to run the automated capture test
  (System Settings → Privacy & Security → Camera → your terminal app).
- The extension may serve a placeholder frame when no producer is live — a
  visible-but-static feed in an app usually means Laolao isn't running or
  isn't connected to `virtual_cam.py`.

## Latest automated results (macOS)

2026-07-05 · macOS 26.3.1 (arm64) · pyvirtualcam 0.15.0 · OBS.app installed:

- `test_virtual_camera_enumerates_for_call_apps` — **PASS**. AVFoundation
  listed: EMEET SmartCam C950 4K, **OBS Virtual Camera**, MacBook Pro Camera,
  Desk View Camera.
- `test_pattern_survives_to_consumer` — **PASS**. 1280×720 captured; center
  square mean RGB (255, 255, 255), magenta field (255, 40, 255) — pixels
  survive intact (minor green lift from the extension's YUV conversion).
- `test_wechat_probe` — WeChat **4.1.7** installed; manual in-call check
  pending.
