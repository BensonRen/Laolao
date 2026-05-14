"""
Headless smoke tests for Windows — all runnable over SSH with no GUI required.
Run with: venv/Scripts/python -m pytest tests/test_windows_headless.py -v

Covers: imports, audio devices, virtual camera, WebSocket server, transcription pipeline.

Notes:
  - pyvirtualcam tests require OBS VirtualCam DirectShow filter (OBS <= 27 or standalone plugin).
    OBS 28+ dropped the DirectShow filter; mark those tests with -m vcam to skip by default.
  - slow tests download Whisper models on first run.
"""

import json
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
IS_WIN = platform.system() == "Windows"

# ── 1. Dependency imports ──────────────────────────────────────────────────────

def test_sounddevice_import():
    import sounddevice  # noqa: F401

def test_faster_whisper_import():
    import faster_whisper  # noqa: F401

def test_pyvirtualcam_import():
    import pyvirtualcam  # noqa: F401

def test_websockets_import():
    import websockets  # noqa: F401

def test_opencc_import():
    import opencc  # noqa: F401

def test_numpy_import():
    import numpy  # noqa: F401

def test_pillow_import():
    from PIL import Image  # noqa: F401

# ── 2. Audio device enumeration ───────────────────────────────────────────────

def test_audio_devices_listed():
    import sounddevice as sd
    devices = sd.query_devices()
    assert len(devices) > 0, "No audio devices found"

def test_input_device_exists():
    import sounddevice as sd
    inputs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    assert len(inputs) > 0, "No microphone / input device found"

def test_default_input_device():
    import sounddevice as sd
    dev = sd.query_devices(kind="input")
    assert dev["max_input_channels"] > 0

# ── 3. Virtual camera ─────────────────────────────────────────────────────────

def _vcam_available():
    """Return True if pyvirtualcam can actually open a camera on this machine."""
    try:
        import pyvirtualcam, numpy as np
        with pyvirtualcam.Camera(width=320, height=240, fps=10,
                                  fmt=pyvirtualcam.PixelFormat.RGB):
            pass
        return True
    except Exception:
        return False

vcam_available = pytest.mark.skipif(
    not _vcam_available(),
    reason="No virtual camera driver installed (OBS 28+ dropped the DirectShow filter; "
           "install the standalone OBS-VirtualCam plugin or Unity Capture)"
)

@vcam_available
def test_pyvirtualcam_open():
    import pyvirtualcam, numpy as np
    with pyvirtualcam.Camera(width=1280, height=720, fps=30,
                              fmt=pyvirtualcam.PixelFormat.RGB) as cam:
        assert cam.device, "Camera device name should not be empty"
        cam.send(np.zeros((720, 1280, 3), dtype=np.uint8))

@vcam_available
def test_pyvirtualcam_send_frame():
    import pyvirtualcam, numpy as np
    with pyvirtualcam.Camera(width=1280, height=720, fps=30,
                              fmt=pyvirtualcam.PixelFormat.RGB) as cam:
        for i in range(5):
            cam.send(np.full((720, 1280, 3), i * 50, dtype=np.uint8))

# ── 4. Faster-whisper backend ─────────────────────────────────────────────────

@pytest.mark.slow
def test_whisper_model_loads():
    from faster_whisper import WhisperModel
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    assert model is not None

@pytest.mark.slow
def test_whisper_transcribes_silence():
    import numpy as np
    from faster_whisper import WhisperModel
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    # 2 s of silence — should produce empty or near-empty output without crashing
    audio = np.zeros(32000, dtype=np.float32)
    segments, _ = model.transcribe(audio, language="zh")
    result = "".join(s.text for s in segments)
    assert isinstance(result, str)

# ── 5. Server startup (subprocess) ────────────────────────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def test_server_starts_and_binds():
    """Launch server.py on a random port, confirm it listens within 10 s."""
    port = _free_port()
    venv_py = ROOT / ("venv/Scripts/python.exe" if IS_WIN else "venv/bin/python")
    proc = subprocess.Popen(
        [str(venv_py), str(ROOT / "server.py"), f"--port={port}", "--model=tiny"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 60  # model may need to download on first run
        connected = False
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    connected = True
                    break
            except OSError:
                time.sleep(0.5)
        assert connected, "server.py did not open WebSocket port within 30 s"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_server_accepts_websocket():
    """server.py accepts a WebSocket connection and sends a stats message."""
    import asyncio
    import websockets as ws

    port = _free_port()
    venv_py = ROOT / ("venv/Scripts/python.exe" if IS_WIN else "venv/bin/python")
    proc = subprocess.Popen(
        [str(venv_py), str(ROOT / "server.py"), f"--port={port}", "--model=tiny"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    async def connect_and_receive():
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                async with ws.connect(f"ws://127.0.0.1:{port}", open_timeout=2) as conn:
                    msg = await asyncio.wait_for(conn.recv(), timeout=10)
                    return json.loads(msg)
            except Exception:
                await asyncio.sleep(0.5)
        return None

    try:
        msg = asyncio.run(connect_and_receive())
        assert msg is not None, "Never received a message from server.py"
        assert "type" in msg, f"Unexpected message shape: {msg}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

# ── 6. virtual_cam.py TCP frame server ───────────────────────────────────────

def test_virtual_cam_tcp_port():
    """virtual_cam.py opens TCP port 8766 and accepts a connection."""
    import struct
    from PIL import Image
    import io

    venv_py = ROOT / ("venv/Scripts/python.exe" if IS_WIN else "venv/bin/python")
    proc = subprocess.Popen(
        [str(venv_py), str(ROOT / "virtual_cam.py")],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 15
        sock = None
        while time.time() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", 8766), timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        assert sock is not None, "virtual_cam.py did not open port 8766 within 15 s"

        # Send one JPEG frame
        img = Image.new("RGB", (1280, 720), color=(128, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        jpeg = buf.getvalue()
        sock.sendall(struct.pack(">I", len(jpeg)) + jpeg)
        sock.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)

# ── 7. OpenCC conversion ──────────────────────────────────────────────────────

def test_opencc_traditional_to_simplified():
    import opencc
    converter = opencc.OpenCC("t2s")
    result = converter.convert("歡迎使用老老")
    assert "欢迎使用老老" == result

# ── 8. Config file ────────────────────────────────────────────────────────────

def test_config_json_valid():
    config_path = ROOT / "config.json"
    assert config_path.exists(), "config.json not found"
    with open(config_path) as f:
        config = json.load(f)
    for key in ("model", "language", "device", "ws_port"):
        assert key in config, f"Missing key in config.json: {key}"
