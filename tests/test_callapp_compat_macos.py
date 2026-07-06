"""
Call-app compatibility e2e for the Laolao virtual camera (macOS).

Where test_virtualcam_macos.py checks the *running Laolao pipeline*, this test
is self-contained: it starts its own pyvirtualcam producer (same parameters as
virtual_cam.py — 1280x720 @ 30 fps, RGB), then verifies the exact chain a video
call app (WeChat, Zoom, FaceTime) depends on:

  1. The "OBS Virtual Camera" device is enumerable via AVFoundation
     DiscoverySession — this is literally what WeChat's camera picker sees.
  2. A distinctive test pattern pushed through pyvirtualcam survives a
     round-trip capture from a *separate consumer process* (Swift +
     AVCaptureVideoDataOutput) — proving apps receive real pixels, not black.
  3. (Informational) WeChat is detected and its version printed as a reminder
     that final verification is a manual call — see docs/COMPAT.md.

Run:
    ./venv/bin/python -m pytest tests/test_callapp_compat_macos.py -v -s

Requires:
  - macOS with a GUI session (virtual cameras do not work over bare SSH)
  - OBS Studio 28+ installed once (provides the macOS Camera Extension)
  - Xcode CLI tools (`swift`)
  - Camera permission for the terminal app running the test
"""

from __future__ import annotations

import platform
import plistlib
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest

# Reuse the Swift-based enumeration/capture helpers from the pipeline e2e.
from tests.test_virtualcam_macos import _capture_frame, _list_devices

pytestmark = [
    pytest.mark.macos_only,
    pytest.mark.skipif(platform.system() != 'Darwin',
                       reason='macOS call-app compatibility test'),
]

# Must mirror virtual_cam.py / electron/main.js
CAM_W, CAM_H, CAM_FPS = 1280, 720, 30
VCAM_DEVICE_RE = 'obs'          # case-insensitive substring match

WECHAT_APP = Path('/Applications/WeChat.app')

# ── Test pattern: magenta field with a centered white square ──────────────
SQUARE_FRAC = 0.25              # square side as fraction of frame height


def _make_pattern() -> np.ndarray:
    frame = np.zeros((CAM_H, CAM_W, 3), dtype=np.uint8)
    frame[:, :] = (255, 0, 255)                       # magenta
    side = int(CAM_H * SQUARE_FRAC)
    y0 = (CAM_H - side) // 2
    x0 = (CAM_W - side) // 2
    frame[y0:y0 + side, x0:x0 + side] = (255, 255, 255)
    return frame


def _no_gui_session() -> bool:
    """Virtual cameras need a real login session; bare SSH has none."""
    import os
    return bool(os.environ.get('SSH_CONNECTION')) and not os.environ.get('DISPLAY')


class _Producer:
    """Owns a pyvirtualcam Camera and feeds the test pattern at CAM_FPS."""

    def __init__(self) -> None:
        import pyvirtualcam
        self._cam = pyvirtualcam.Camera(
            width=CAM_W, height=CAM_H, fps=CAM_FPS,
            fmt=pyvirtualcam.PixelFormat.RGB,
        )
        self.device_name = self._cam.device
        self._frame = _make_pattern()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='compat-vcam-feeder')
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._cam.send(self._frame)
            self._cam.sleep_until_next_frame()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        self._cam.close()


@pytest.fixture(scope='module')
def producer():
    """A live pattern-feeding virtual camera producer, or a skip with the
    exact reason a call app would also fail."""
    if _no_gui_session():
        pytest.skip('No GUI session (SSH) — virtual camera needs a login session')
    pyvirtualcam = pytest.importorskip(
        'pyvirtualcam', reason='pyvirtualcam not installed in venv')
    try:
        prod = _Producer()
    except Exception as exc:  # noqa: BLE001 — pyvirtualcam raises bare RuntimeError
        msg = str(exc).lower()
        if 'in use' in msg or 'busy' in msg:
            pytest.skip(f'Virtual camera already owned by another producer '
                        f'(Laolao running?) — quit it and re-run. ({exc})')
        pytest.skip(f'Could not start virtual camera — OBS Camera Extension '
                    f'missing? Install OBS Studio 28+ once. ({exc})')
    yield prod
    prod.close()


# ── 1. Enumeration: what WeChat's camera picker sees ──────────────────────

def test_virtual_camera_enumerates_for_call_apps(producer):
    devices = _list_devices()
    print(f'\n  Producer device: "{producer.device_name}"')
    print(f'  Cameras visible to AVFoundation: {devices}')
    matches = [d for d in devices if VCAM_DEVICE_RE in d.lower()]
    assert matches, (
        f'No OBS virtual camera in the AVFoundation device list {devices}. '
        'WeChat/Zoom/FaceTime enumerate this exact list, so they will not '
        'see Laolao either. Check System Settings → General → Login Items & '
        'Extensions → Camera Extensions.'
    )


# ── 2. Frame content: apps get our pixels, not black ──────────────────────

def test_pattern_survives_to_consumer(producer):
    # Unique per-run name: the shared helper unlinks pre-existing files, which
    # races against a previous run's lingering Swift process on a fixed name.
    import os
    out = Path(tempfile.gettempdir()) / f'laolao_compat_frame_{os.getpid()}.png'
    time.sleep(1.0)   # let the extension pick up the new producer
    frame, status = _capture_frame(producer.device_name, out_path=out)
    if status == 'tcc-denied':
        pytest.skip('Terminal lacks Camera permission '
                    '(System Settings → Privacy & Security → Camera)')
    assert frame is not None, (
        f'Consumer captured no frame ({status}) although the producer is live '
        '— a call app selecting this camera would show black.'
    )

    h, w = frame.shape[:2]
    f = frame.astype(np.float32)
    side = int(h * SQUARE_FRAC)
    y0, x0 = (h - side) // 2, (w - side) // 2
    center = f[y0:y0 + side, x0:x0 + side]
    border = f[:, : int(w * 0.15)]                    # left magenta strip

    c_mean = center.mean(axis=(0, 1))
    b_mean = border.mean(axis=(0, 1))
    print(f'\n  Captured {w}x{h} → {out}')
    print(f'  center square mean RGB: {np.round(c_mean, 1)} (expect ~white)')
    print(f'  border strip  mean RGB: {np.round(b_mean, 1)} (expect ~magenta)')

    assert c_mean.min() > 160, f'Center square not white: {c_mean}'
    assert b_mean[0] > 150 and b_mean[2] > 150 and b_mean[1] < 110, (
        f'Border not magenta: {b_mean}. Pixels reached the consumer but were '
        'corrupted (pixel-format/color mismatch in the vcam pipeline).'
    )


# ── 3. WeChat presence probe (informational, never fails) ─────────────────

def test_wechat_probe():
    if not WECHAT_APP.exists():
        pytest.skip('WeChat not installed on this machine — manual '
                    'verification must happen on a machine that has it.')
    version = '?'
    try:
        with open(WECHAT_APP / 'Contents' / 'Info.plist', 'rb') as fh:
            info = plistlib.load(fh)
        version = info.get('CFBundleShortVersionString', '?')
    except Exception as exc:  # noqa: BLE001
        print(f'\n  Could not read WeChat Info.plist: {exc}')
    print(f'\n  WeChat.app detected, version {version}.')
    print('  REMINDER: automated tests prove the camera is enumerable and '
          'delivers pixels via AVFoundation — the same API WeChat uses — but '
          'the final check is a manual call. See docs/COMPAT.md for the '
          'procedure, and record the result in the matrix there.')
