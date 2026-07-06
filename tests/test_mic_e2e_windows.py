r"""
End-to-end mic diagnostic for Windows.
Run via interactive scheduled task — NOT over SSH (audio requires GUI session).

Usage (standalone diagnostic, no pytest needed):
  venv\Scripts\python tests\test_mic_e2e_windows.py

Under pytest the checks run as environment-gated smoke tests: conditions that
mean "this machine/session can't support the check" (no mic, muted mic, server
not running) skip with a diagnosis instead of failing.
"""

import json
import socket
import sys
import time

import numpy as np
import sounddevice as sd

try:
    import pytest
except ImportError:                  # standalone mode: pytest is optional
    pytest = None


SAMPLE_RATE  = 16000
RECORD_SECS  = 3
RMS_THRESHOLD = 0.002   # below this = effectively silent


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


# ─── Diagnostic helpers (return values; never named test_* so pytest ───────
# ─── doesn't collect them as tests with fixture-looking args) ──────────────

def _list_input_devices():
    section("1. All audio devices")
    devices = sd.query_devices()
    inputs = []
    for i, d in enumerate(devices):
        tag = "<-- DEFAULT INPUT" if i == sd.default.device[0] else ""
        if d["max_input_channels"] > 0:
            inputs.append(i)
            print(f"  [{i}] {d['name']}  ch={d['max_input_channels']}  {tag}")
    if not inputs:
        print("  ERROR: No input devices found at all!")
    return inputs


def _record_device(index, name):
    """Record RECORD_SECS seconds and return rms, or None on failure."""
    try:
        print(f"  Recording {RECORD_SECS}s from [{index}] {name} ...", end=" ", flush=True)
        buf = sd.rec(int(RECORD_SECS * SAMPLE_RATE),
                     samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                     device=index)
        sd.wait()
        rms = float(np.sqrt(np.mean(buf ** 2)))
        peak = float(np.max(np.abs(buf)))
        status = "OK" if rms >= RMS_THRESHOLD else "SILENT"
        print(f"rms={rms:.5f}  peak={peak:.5f}  [{status}]")
        return rms
    except Exception as e:
        print(f"FAILED: {e}")
        return None


def _record_all_devices(input_indices):
    section("2. Record from every input device")
    results = {}
    for i in input_indices:
        name = sd.query_devices(i)["name"]
        rms = _record_device(i, name)
        results[i] = rms
    return results


def _record_default_device():
    section("3. Record from default input device")
    try:
        dev = sd.query_devices(kind="input")
        print(f"  Default input: [{sd.default.device[0]}] {dev['name']}")
        rms = _record_device(sd.default.device[0], dev["name"])
        return rms
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def _server_reachable(port=8765):
    section("4. Check if server.py WebSocket is reachable")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            print(f"  server.py is listening on port {port}  OK")
            return True
    except OSError:
        print(f"  server.py is NOT reachable on port {port}  (not running?)")
        return False


def _read_server_levels(port=8765, wait_secs=5):
    section("5. Read level messages from server.py")
    try:
        import asyncio, websockets as ws

        async def read_levels():
            msgs = []
            try:
                async with ws.connect(f"ws://127.0.0.1:{port}", open_timeout=3) as conn:
                    deadline = time.time() + wait_secs
                    while time.time() < deadline:
                        try:
                            raw = await asyncio.wait_for(conn.recv(), timeout=1)
                            msg = json.loads(raw)
                            if msg.get("type") == "level":
                                msgs.append(msg)
                        except asyncio.TimeoutError:
                            pass
            except Exception as e:
                print(f"  WebSocket error: {e}")
            return msgs

        msgs = asyncio.run(read_levels())
        if not msgs:
            print(f"  No level messages received in {wait_secs}s")
            return None
        rmss = [m["rms"] for m in msgs]
        avg_rms = sum(rmss) / len(rmss)
        max_rms = max(rmss)
        print(f"  Received {len(msgs)} level msgs  avg_rms={avg_rms:.5f}  max_rms={max_rms:.5f}")
        if max_rms < RMS_THRESHOLD:
            print("  WARNING: server is running but RMS is near zero — mic not reaching Python")
        else:
            print("  OK: server is receiving real audio")
        return avg_rms
    except ImportError:
        print("  websockets not installed — skipping")
        return None


def summarize(input_indices, device_results, default_rms, server_up, server_rms):
    section("SUMMARY & DIAGNOSIS")

    working = [i for i, r in device_results.items() if r is not None and r >= RMS_THRESHOLD]
    silent  = [i for i, r in device_results.items() if r is not None and r < RMS_THRESHOLD]
    failed  = [i for i, r in device_results.items() if r is None]

    print(f"  Devices with signal : {working}")
    print(f"  Devices silent      : {silent}")
    print(f"  Devices failed      : {failed}")
    print(f"  Default device rms  : {default_rms}")
    print(f"  Server running      : {server_up}")
    print(f"  Server avg rms      : {server_rms}")
    print()

    if not input_indices:
        print("DIAGNOSIS: No input devices — sounddevice / PortAudio can't see any mic.")
        print("FIX: Check audio driver, or try reinstalling PortAudio.")
        return

    if not working:
        print("DIAGNOSIS: All devices record silence (rms < threshold).")
        print("  • Microphone may be muted in Windows Sound settings")
        print("  • Wrong device selected (e.g. a loopback or HDMI in)")
        print("  • 'Allow desktop apps to access microphone' may be OFF in Privacy settings")
        if failed:
            print(f"  • Devices {failed} errored — may be exclusive-mode locked by another app")
    else:
        print(f"DIAGNOSIS: Device(s) {working} have working signal.")
        if sd.default.device[0] not in working:
            print(f"  The DEFAULT device ({sd.default.device[0]}) is NOT in the working list.")
            print(f"  FIX: Set mic_device={working[0]} in config.json")
        else:
            print("  Default device is working — server should be getting audio.")

    if server_up and (server_rms is None or server_rms < RMS_THRESHOLD):
        print("  Server is running but not getting audio.")
        print("  FIX: Check mic_device in config.json matches a working device above.")


# ─── Pytest wrappers (environment-gated: skip, don't fail, when the ────────
# ─── machine/session can't support the check) ──────────────────────────────

def test_input_devices_listed():
    inputs = _list_input_devices()
    if not inputs:
        pytest.skip("no audio input devices in this environment (headless?)")


def test_default_device_records():
    rms = _record_default_device()
    if rms is None:
        pytest.skip("could not record from the default input device "
                    "(no mic, no permission, or device busy)")
    if rms < RMS_THRESHOLD:
        pytest.skip(f"default mic records silence (rms={rms:.5f}) — "
                    "muted mic or OS privacy settings, not a code bug")


def test_server_reachable():
    if not _server_reachable():
        pytest.skip("server.py not running on :8765 — start it to run this diagnostic")


def test_server_level_messages():
    if not _server_reachable():
        pytest.skip("server.py not running on :8765 — start it to run this diagnostic")
    avg_rms = _read_server_levels()
    if avg_rms is None:
        pytest.skip("no level messages within 5s — server up but no audio "
                    "source feeding it in this session")


if __name__ == "__main__":
    print("Laolao Windows mic diagnostic")
    print(f"sounddevice {sd.__version__}  |  Python {sys.version.split()[0]}")

    input_indices  = _list_input_devices()
    device_results = _record_all_devices(input_indices)
    default_rms    = _record_default_device()
    server_up      = _server_reachable()
    server_rms     = _read_server_levels() if server_up else None

    summarize(input_indices, device_results, default_rms, server_up, server_rms)
