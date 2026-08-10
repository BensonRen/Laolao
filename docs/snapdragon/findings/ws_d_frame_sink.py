"""
ws_d_frame_sink.py — a pyvirtualcam-free stand-in for virtual_cam.py.

Speaks the exact wire protocol electron/main.js writes on TCP :8766:

    [4-byte big-endian uint32 JPEG length][JPEG bytes]   ... repeated

...but instead of pushing frames into a virtual camera it just counts them and
writes a handful to disk as .jpg files. That isolates the Electron half of the
pipeline (capturePage -> nativeImage.toJPEG -> TCP) from the camera sink, which
on Windows-ARM64 is dead because pyvirtualcam ships no arm64 wheel (H-002).

Stdlib only — no numpy, no Pillow, no pyvirtualcam — so it runs on the bare
ARM64 interpreter with zero installs. Verify the saved JPEGs separately.

Usage:
    python ws_d_frame_sink.py --out FRAMES_DIR [--port 8766] [--save 5]
                              [--save-every 30] [--duration 30]
                              [--max-frames 0] [--json-summary summary.json]

Exits 0 if at least one complete frame was received, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time

MAX_FRAME_BYTES = 32 * 1024 * 1024  # sanity guard against a desynced stream


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('socket closed mid-frame')
        buf += chunk
    return bytes(buf)


def log(msg: str) -> None:
    print(f'[sink] {msg}', flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8766)
    ap.add_argument('--out', default='frames', help='directory for saved .jpg files')
    ap.add_argument('--save', type=int, default=5, help='how many frames to write to disk')
    ap.add_argument('--save-every', type=int, default=30,
                    help='save 1 frame out of every N received')
    ap.add_argument('--duration', type=float, default=30.0,
                    help='seconds to run before shutting down (0 = forever)')
    ap.add_argument('--max-frames', type=int, default=0,
                    help='stop after this many frames (0 = unlimited)')
    ap.add_argument('--json-summary', default='', help='write a JSON summary here')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    srv.settimeout(1.0)
    log(f'listening on {args.host}:{args.port} (protocol: [4B BE len][JPEG])')

    deadline = time.time() + args.duration if args.duration else float('inf')
    frames = 0
    saved: list[dict] = []
    total_bytes = 0
    first_frame_at = None
    last_frame_at = None
    bad_magic = 0

    try:
        while time.time() < deadline:
            try:
                conn, peer = srv.accept()
            except socket.timeout:
                continue
            log(f'client connected from {peer}')
            conn.settimeout(2.0)
            try:
                while time.time() < deadline:
                    if args.max_frames and frames >= args.max_frames:
                        break
                    try:
                        length = struct.unpack('>I', recv_exact(conn, 4))[0]
                    except socket.timeout:
                        continue
                    if length == 0 or length > MAX_FRAME_BYTES:
                        log(f'ABORT: implausible frame length {length} — stream desync')
                        break
                    jpeg = recv_exact(conn, length)
                    frames += 1
                    total_bytes += length
                    now = time.time()
                    if first_frame_at is None:
                        first_frame_at = now
                        log(f'FIRST frame: {length} bytes, '
                            f'magic={jpeg[:2].hex()} tail={jpeg[-2:].hex()}')
                    last_frame_at = now

                    if jpeg[:2] != b'\xff\xd8' or jpeg[-2:] != b'\xff\xd9':
                        bad_magic += 1

                    if len(saved) < args.save and frames % args.save_every == 1 % max(args.save_every, 1):
                        p = os.path.join(args.out, f'frame_{frames:05d}.jpg')
                        with open(p, 'wb') as fh:
                            fh.write(jpeg)
                        saved.append({'path': os.path.abspath(p),
                                      'frame_index': frames,
                                      'bytes': length})
                        log(f'saved {p} ({length} bytes)')

                    if frames % 60 == 0:
                        log(f'{frames} frames, {total_bytes} bytes total')
            except (ConnectionError, OSError) as e:
                log(f'client gone: {e}')
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            if args.max_frames and frames >= args.max_frames:
                break
    except KeyboardInterrupt:
        log('interrupted')
    finally:
        srv.close()

    fps = None
    if first_frame_at and last_frame_at and last_frame_at > first_frame_at and frames > 1:
        fps = (frames - 1) / (last_frame_at - first_frame_at)

    summary = {
        'frames': frames,
        'total_bytes': total_bytes,
        'mean_frame_bytes': (total_bytes // frames) if frames else 0,
        'observed_fps': round(fps, 2) if fps else None,
        'malformed_jpeg_markers': bad_magic,
        'saved': saved,
    }
    log('SUMMARY ' + json.dumps(summary))
    if args.json_summary:
        with open(args.json_summary, 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, indent=2)

    return 0 if frames else 1


if __name__ == '__main__':
    sys.exit(main())
