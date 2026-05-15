"""
End-to-end test harness for the macOS virtual camera pipeline.

Uses a tiny Swift program (capture_one_frame.swift) that talks to AVFoundation
directly — the same API Zoom / Meet / FaceTime / WeChat use — so a passing test
means a real video app will see the same frames.

What this verifies:
  1. virtual_cam.py is running and bound to TCP 127.0.0.1:8766
  2. The "OBS Virtual Camera" device is enumerable by AVFoundation
  3. A frame can actually be captured from it via AVCaptureVideoDataOutput
  4. The captured frame has real pixel content — not a black/empty feed

Run:
    pytest tests/test_virtualcam_macos.py -v -s

Or as a standalone script:
    venv/bin/python tests/test_virtualcam_macos.py

Requires:
  - Xcode CLI tools (`swift` in PATH) — already installed if you have brew
  - Pillow, numpy (already in the project venv)
  - Camera permission for the *terminal app you run this in*. Without it,
    AVFoundation delivers zero frames silently. Grant it under
    System Settings → Privacy & Security → Camera.
"""

from __future__ import annotations

import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import pytest
except ImportError:                  # standalone mode: pytest is optional
    pytest = None


VCAM_DEVICE = 'OBS Virtual Camera'
VCAM_TCP_PORT = 8766
BLACK_RMS_THRESHOLD = 2.0       # frames darker than this are considered "no signal"
CAPTURE_TIMEOUT_S = 8           # how long to wait for the first frame
WARMUP_FRAMES = 3               # skip first N frames (camera AGC settle)


# ─── Swift-based capture (shells out to capture_one_frame.swift) ─────────

_HERE = Path(__file__).resolve().parent
_SWIFT_SRC = _HERE / 'capture_one_frame.swift'


def _list_devices() -> list[str]:
    """List video devices via Swift one-liner (matches what AVFoundation sees)."""
    src = '''
    import AVFoundation
    let s = AVCaptureDevice.DiscoverySession(
        deviceTypes: [.external, .builtInWideAngleCamera, .continuityCamera, .deskViewCamera],
        mediaType: .video, position: .unspecified)
    for d in s.devices { print(d.localizedName) }
    '''
    r = subprocess.run(['swift', '-'], input=src, capture_output=True,
                       text=True, timeout=15)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _capture_frame(device_name: str, timeout_s: float = CAPTURE_TIMEOUT_S,
                   warmup: int = WARMUP_FRAMES,
                   out_path: Path | None = None) -> tuple[np.ndarray | None, str]:
    """Capture one frame via Swift+AVFoundation.

    Returns (frame_rgb, status_msg). frame is None on failure; status_msg is a
    short string useful for diagnostics ('ok', 'tcc-denied', 'no-frame', etc.).
    """
    out_path = out_path or Path(tempfile.gettempdir()) / 'laolao_vcam_test.png'
    if out_path.exists():
        out_path.unlink()
    r = subprocess.run(
        ['swift', str(_SWIFT_SRC), device_name, str(out_path),
         str(timeout_s), str(warmup)],
        capture_output=True, text=True, timeout=timeout_s + 10,
    )
    out = r.stdout.strip() + '\n' + r.stderr.strip()
    if r.returncode != 0 or not out_path.exists():
        # seen=0 in stdout is the classic TCC-blocked symptom
        if 'seen=0' in r.stdout:
            return None, 'tcc-denied'
        return None, (r.stdout.strip() or 'unknown-error') + ' / ' + r.stderr.strip()
    arr = np.asarray(Image.open(out_path).convert('RGB'), dtype=np.uint8)
    return arr, 'ok'


def _frame_stats(arr: np.ndarray) -> tuple[float, float, tuple[int, int]]:
    f = arr.astype(np.float32)
    rms = float(np.sqrt((f ** 2).mean()))
    peak = float(f.max())
    return rms, peak, arr.shape[:2]


# ─── Pytest skip guards ───────────────────────────────────────────────────

if pytest is not None:
    pytestmark = [
        pytest.mark.skipif(platform.system() != 'Darwin',
                           reason='macOS virtual camera test'),
    ]


# ─── Tests ────────────────────────────────────────────────────────────────

def _port_is_listening(port: int) -> bool:
    """Non-intrusive: check via lsof. We can't connect() because virtual_cam.py
    uses listen(1) and Laolao already holds the slot."""
    r = subprocess.run(['lsof', '-iTCP', f'-i:{port}', '-sTCP:LISTEN'],
                       capture_output=True, text=True, timeout=3)
    return port == VCAM_TCP_PORT and f':{port}' in r.stdout


def test_virtual_cam_server_listening():
    """virtual_cam.py should be listening on 127.0.0.1:8766."""
    assert _port_is_listening(VCAM_TCP_PORT), (
        f'virtual_cam.py is not listening on :{VCAM_TCP_PORT}. '
        'Launch Laolao.app first, or run `python virtual_cam.py` manually.'
    )


def test_device_enumerable():
    """OBS Virtual Camera must appear in AVFoundation's device list."""
    devices = _list_devices()
    print(f'\n  Cameras visible to AVFoundation: {devices}')
    assert VCAM_DEVICE in devices, (
        f'"{VCAM_DEVICE}" not found. Devices seen: {devices}. '
        'Check System Settings → Privacy & Security → Camera Extensions.'
    )


def test_frame_capture_succeeds(tmp_path):
    """AVFoundation should be able to grab a frame from the virtual camera."""
    out = tmp_path / 'frame.png'
    frame, status = _capture_frame(VCAM_DEVICE, out_path=out)
    if status == 'tcc-denied':
        pytest.skip(
            'Camera permission not granted to the terminal app. '
            'Grant it under System Settings → Privacy & Security → Camera.'
        )
    assert frame is not None, (
        f'No frame received ({status}). The device is enumerable but produced '
        'no data — virtual_cam.py may not be receiving frames from Electron, '
        'or the OBS Camera Extension is not enabled.'
    )
    print(f'\n  Captured frame {frame.shape} → {out}')


def test_frame_has_signal(tmp_path):
    """The captured frame should have real pixel content, not be black."""
    out = tmp_path / 'frame.png'
    frame, status = _capture_frame(VCAM_DEVICE, out_path=out)
    if status == 'tcc-denied':
        pytest.skip('Camera permission missing — see test_frame_capture_succeeds.')
    assert frame is not None, f'capture failed ({status})'
    rms, peak, shape = _frame_stats(frame)
    print(f'\n  Frame shape={shape}  rms={rms:.2f}  peak={peak:.1f}')
    assert rms > BLACK_RMS_THRESHOLD, (
        f'Frame is black (rms={rms:.2f}). Electron may not be sending frames, '
        'or the Electron window is hidden/minimised.'
    )


# ─── Standalone runner (no pytest needed) ─────────────────────────────────

def _run_standalone() -> int:
    print('Laolao virtual camera diagnostic\n' + '=' * 60)
    if platform.system() != 'Darwin':
        print('Not macOS — skipping.'); return 0

    print('\n[1/4] virtual_cam.py TCP server')
    if _port_is_listening(VCAM_TCP_PORT):
        print(f'  OK — listening on :{VCAM_TCP_PORT}')
    else:
        print(f'  FAIL — nothing listening on :{VCAM_TCP_PORT}')
        return 1

    print('\n[2/4] AVFoundation device list')
    devices = _list_devices()
    for d in devices:
        marker = '  <-- target' if d == VCAM_DEVICE else ''
        print(f'  • {d}{marker}')
    if VCAM_DEVICE not in devices:
        print(f'  FAIL — "{VCAM_DEVICE}" not in list')
        print('  Hint: System Settings → Privacy & Security → Camera Extensions')
        return 1

    print('\n[3/4] Capture a frame via Swift + AVFoundation')
    out = Path(tempfile.gettempdir()) / 'laolao_vcam_test.png'
    t0 = time.time()
    frame, status = _capture_frame(VCAM_DEVICE, out_path=out)
    dt = (time.time() - t0) * 1000
    if status == 'tcc-denied':
        print(f'  BLOCKED — terminal lacks Camera permission (no frames delivered)')
        print('  Fix: System Settings → Privacy & Security → Camera → enable your terminal.')
        print('       Then re-run this script.')
        print('  Quick alternative without terminal permission:')
        print('       Open QuickTime Player → File → New Movie Recording → ▾ → "OBS Virtual Camera"')
        return 2
    if frame is None:
        print(f'  FAIL — {status} ({dt:.0f} ms)')
        print('  Hint: is Electron actually sending frames? Check /var/folders/.../T/laolao_vcam.log')
        return 1
    print(f'  OK — frame {frame.shape} in {dt:.0f} ms → {out}')

    print('\n[4/4] Verify pixel content')
    rms, peak, shape = _frame_stats(frame)
    print(f'  shape={shape}  rms={rms:.2f}  peak={peak:.1f}')
    if rms <= BLACK_RMS_THRESHOLD:
        print(f'  FAIL — frame is black (rms ≤ {BLACK_RMS_THRESHOLD})')
        print('  Hint: is the Electron window open and on a non-black overlay?')
        return 1
    print('  OK — frame has real pixel content')

    print('\n' + '=' * 60)
    print(f'ALL PASSED — open {out} to inspect visually.')
    print('If Google Meet still shows nothing, reload the meeting tab.')
    return 0


if __name__ == '__main__':
    raise SystemExit(_run_standalone())
