#!/usr/bin/env python3
"""
Laolao (老老) — Real-time Chinese captions for video calls via OBS.

Fully open source, runs offline. Chinese-first.
GitHub: https://github.com/YOUR_USERNAME/laolao

Usage:
    python server.py                        # use config.json defaults
    python server.py --model small          # better Chinese accuracy
    python server.py --language yue         # Cantonese
    python server.py --list-devices         # show microphones
    python server.py --benchmark            # measure transcription speed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import websockets
from websockets.server import serve

# ──────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULTS: dict = {
    # Transcription
    "model": "base",           # tiny|base|small|medium|large-v3
    "language": "zh",          # zh|yue|en|ja|ko|auto
    "device": "auto",          # auto|cpu|cuda|mlx
    "compute_type": "int8",    # int8|float16|float32

    # VAD
    "vad": "auto",             # auto|silero|energy
    "silence_rms": 0.008,      # energy VAD threshold (ignored when silero is used)

    # Latency tuning
    "chunk_ms": 300,           # audio chunk size in ms
    "rolling_window_s": 4.0,   # max audio buffer fed to Whisper per pass
    "silence_chunks": 4,       # silence chunks before finalising utterance
    "show_partial": True,      # show in-progress text (yellow in overlay)
    "partial_interval_s": 0.5, # how often to re-transcribe while speaking

    # I/O
    "ws_port": 8765,
    "mic_device": None,        # None = system default; or int index / str name
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    return cfg


# ──────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("laolao")


# ──────────────────────────────────────────────────────────────
# WebSocket layer
# ──────────────────────────────────────────────────────────────

_clients: set = set()
_ws_loop: Optional[asyncio.AbstractEventLoop] = None


async def _broadcast(msg: dict) -> None:
    if not _clients:
        return
    payload = json.dumps(msg, ensure_ascii=False)
    dead = set()
    for ws in _clients:
        try:
            await ws.send(payload)
        except websockets.ConnectionClosed:
            dead.add(ws)
    _clients.difference_update(dead)


def push(msg: dict) -> None:
    """Thread-safe: schedule a broadcast from the audio thread."""
    if _ws_loop and not _ws_loop.is_closed():
        asyncio.run_coroutine_threadsafe(_broadcast(msg), _ws_loop)


async def _ws_handler(ws) -> None:
    log.debug("WS client connected: %s", ws.remote_address)
    _clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        _clients.discard(ws)
        log.debug("WS client disconnected.")


# ──────────────────────────────────────────────────────────────
# Utterance processor (rolling partial + final transcription)
# ──────────────────────────────────────────────────────────────

class UtteranceProcessor:
    """
    Accumulates audio chunks while speech is detected, then transcribes.

    Partial results: emitted every `partial_interval_s` while speaking.
    Final result:    emitted after `silence_chunks` consecutive silent chunks.

    Both are pushed as WebSocket messages:
        {"type": "partial", "text": "…"}   ← yellow in overlay
        {"type": "final",   "text": "…"}   ← white in overlay
    """

    def __init__(self, backend, vad, cfg: dict) -> None:
        self._backend = backend
        self._vad = vad
        self._sr = 16_000

        self._chunk_samples = int(self._sr * cfg["chunk_ms"] / 1000)
        self._rolling_samples = int(self._sr * cfg["rolling_window_s"])
        self._silence_limit = int(cfg["silence_chunks"])
        self._partial_interval = float(cfg["partial_interval_s"])
        self._show_partial = bool(cfg["show_partial"])
        self._language: Optional[str] = (
            cfg["language"] if cfg.get("language") != "auto" else None
        )

        self._buffer = np.array([], dtype=np.int16)
        self._in_utterance = False
        self._silence_count = 0
        self._last_partial_t = 0.0
        self._last_partial_text = ""

    def feed(self, chunk: np.ndarray) -> None:
        """Process one audio chunk (int16, 16 kHz)."""
        is_speech = self._vad.is_speech(chunk)

        if is_speech:
            self._silence_count = 0
            self._in_utterance = True
            self._buffer = np.concatenate([self._buffer, chunk])
            if len(self._buffer) > self._rolling_samples:
                self._buffer = self._buffer[-self._rolling_samples:]

            if self._show_partial:
                now = time.monotonic()
                if now - self._last_partial_t >= self._partial_interval:
                    self._last_partial_t = now
                    text = self._backend.transcribe(self._buffer, self._language)
                    if text and text != self._last_partial_text:
                        self._last_partial_text = text
                        push({"type": "partial", "text": text})
                        log.info("~  %s", text)

        elif self._in_utterance:
            # Accumulate a bit of trailing silence so Whisper hears sentence end
            self._buffer = np.concatenate([self._buffer, chunk])
            self._silence_count += 1

            if self._silence_count >= self._silence_limit:
                self._flush()

    def _flush(self) -> None:
        """Finalise current utterance."""
        self._in_utterance = False
        self._silence_count = 0
        self._last_partial_text = ""
        self._last_partial_t = 0.0

        text = self._backend.transcribe(self._buffer, self._language)
        self._buffer = np.array([], dtype=np.int16)
        self._vad.reset()

        if text:
            push({"type": "final", "text": text})
            log.info("✓  %s", text)


# ──────────────────────────────────────────────────────────────
# Audio capture
# ──────────────────────────────────────────────────────────────

_audio_q: queue.Queue = queue.Queue()


def _audio_cb(indata: np.ndarray, frames: int, t, status) -> None:
    if status:
        log.warning("Audio status: %s", status)
    chunk = (indata[:, 0] * 32767).astype(np.int16)
    _audio_q.put(chunk.copy())


def _audio_loop(processor: UtteranceProcessor,
                device: Optional[int | str],
                chunk_samples: int) -> None:
    log.info("Opening microphone (device=%s)…", device)
    with sd.InputStream(
        samplerate=16_000,
        channels=1,
        dtype="float32",
        blocksize=chunk_samples,
        device=device,
        callback=_audio_cb,
    ):
        log.info("Listening. Speak into your microphone…")
        while _running:
            try:
                chunk = _audio_q.get(timeout=0.5)
                processor.feed(chunk)
            except queue.Empty:
                continue


# ──────────────────────────────────────────────────────────────
# CLI helpers
# ──────────────────────────────────────────────────────────────

def _list_devices() -> None:
    print("\nAvailable audio input devices:")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            default = " ← default" if i == sd.default.device[0] else ""
            print(f"  [{i:2d}]  {d['name']}{default}")
    print()


def _benchmark(cfg: dict) -> None:
    """Quick transcription speed test."""
    from backends import get_backend
    import time

    log.info("Running benchmark…")
    backend = get_backend(cfg)
    lang = cfg["language"] if cfg["language"] != "auto" else None

    # 3 seconds of silence → should return empty string quickly
    audio = np.zeros(16_000 * 3, dtype=np.int16)
    runs = 5
    t0 = time.perf_counter()
    for _ in range(runs):
        backend.transcribe(audio, lang)
    elapsed = (time.perf_counter() - t0) / runs * 1000

    audio_ms = 3000
    rtf = elapsed / audio_ms
    log.info("Backend: %s", backend.name)
    log.info("Avg transcription time for 3 s audio: %.0f ms  (RTF=%.2f)", elapsed, rtf)
    if rtf < 0.2:
        log.info("Excellent! Real-time factor < 0.2 — low latency operation.")
    elif rtf < 0.5:
        log.info("Good. Consider --model base for lower latency.")
    else:
        log.info("Slow CPU. Consider --model tiny or enabling GPU.")


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

_running = True


def _handle_signal(sig, frame) -> None:
    global _running
    log.info("Shutting down…")
    _running = False
    sys.exit(0)


async def _run_server(cfg: dict) -> None:
    global _ws_loop
    _ws_loop = asyncio.get_running_loop()

    port = cfg["ws_port"]
    overlay_path = (Path(__file__).parent / "overlay" / "index.html").resolve()

    async with serve(_ws_handler, "localhost", port):
        log.info("WebSocket:  ws://localhost:%d", port)
        log.info("OBS source: file://%s", overlay_path)
        log.info("Press Ctrl+C to stop.")
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Laolao — real-time Chinese captions for OBS",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model",    help="Whisper model (tiny/base/small/medium/large-v3)")
    parser.add_argument("--language", help="Language code (zh/yue/en/auto)")
    parser.add_argument("--device",   help="Compute device (auto/cpu/cuda/mlx)")
    parser.add_argument("--port",     type=int, help="WebSocket port")
    parser.add_argument("--mic",      help="Mic device index or name substring")
    parser.add_argument("--vad",      choices=["auto", "silero", "energy"],
                        help="VAD backend (default: auto)")
    parser.add_argument("--list-devices", action="store_true",
                        help="Print audio devices and exit")
    parser.add_argument("--benchmark", action="store_true",
                        help="Measure transcription speed and exit")
    args = parser.parse_args()

    if args.list_devices:
        _list_devices()
        return

    cfg = load_config()
    if args.model:    cfg["model"]    = args.model
    if args.language: cfg["language"] = args.language
    if args.device:   cfg["device"]   = args.device
    if args.port:     cfg["ws_port"]  = args.port
    if args.vad:      cfg["vad"]      = args.vad
    if args.mic:
        cfg["mic_device"] = int(args.mic) if args.mic.isdigit() else args.mic

    if args.benchmark:
        _benchmark(cfg)
        return

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("Laolao  model=%s  lang=%s  device=%s  vad=%s",
             cfg["model"], cfg["language"], cfg["device"], cfg["vad"])

    from backends import get_backend
    from vad import get_vad

    backend   = get_backend(cfg)
    vad       = get_vad(cfg)
    processor = UtteranceProcessor(backend, vad, cfg)

    chunk_samples = int(16_000 * cfg["chunk_ms"] / 1000)
    audio_thread = threading.Thread(
        target=_audio_loop,
        args=(processor, cfg["mic_device"], chunk_samples),
        daemon=True,
    )
    audio_thread.start()

    asyncio.run(_run_server(cfg))


if __name__ == "__main__":
    main()
