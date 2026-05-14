"""
virtual_cam.py — receives JPEG frames from Electron via TCP and pushes them
to the OBS Virtual Camera DAL plugin using the compiled vcam/libvcam.dylib.

No pyvirtualcam required; the dylib wraps OBSDALMachServer directly.

Frame protocol: [4-byte big-endian uint32 length] [JPEG bytes]

Usage: python virtual_cam.py [width] [height] [fps]
"""

import ctypes
import io
import logging
import os
import socket
import struct
import sys

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)

HOST    = '127.0.0.1'
PORT    = 8766
WIDTH   = int(sys.argv[1]) if len(sys.argv) > 1 else 1280
HEIGHT  = int(sys.argv[2]) if len(sys.argv) > 2 else 720
FPS     = int(sys.argv[3]) if len(sys.argv) > 3 else 30

DYLIB   = os.path.join(os.path.dirname(__file__), 'vcam', 'libvcam.dylib')


def load_vcam() -> ctypes.CDLL:
    lib = ctypes.CDLL(DYLIB)
    lib.vcam_start.argtypes     = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    lib.vcam_start.restype      = ctypes.c_int
    lib.vcam_send_frame.argtypes = [ctypes.POINTER(ctypes.c_uint8)]
    lib.vcam_send_frame.restype  = ctypes.c_int
    lib.vcam_stop.argtypes      = []
    lib.vcam_stop.restype       = ctypes.c_void_p
    return lib


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('socket closed')
        buf += chunk
    return bytes(buf)


def jpeg_to_bgra(jpeg: bytes) -> bytes:
    img = Image.open(io.BytesIO(jpeg)).convert('RGBA').resize((WIDTH, HEIGHT), Image.BILINEAR)
    # PIL RGBA is R,G,B,A — macOS wants B,G,R,A (BGRA)
    r, g, b, a = img.split()
    bgra = Image.merge('RGBA', (b, g, r, a))
    return bgra.tobytes()


def handle(conn: socket.socket, lib: ctypes.CDLL) -> None:
    log.info('Electron connected — streaming frames to virtual camera')
    try:
        while True:
            length = struct.unpack('>I', recv_exact(conn, 4))[0]
            jpeg   = recv_exact(conn, length)

            bgra = jpeg_to_bgra(jpeg)
            ptr  = (ctypes.c_uint8 * len(bgra)).from_buffer_copy(bgra)
            lib.vcam_send_frame(ptr)

    except (ConnectionError, OSError):
        log.info('Electron disconnected')
    finally:
        conn.close()


def main() -> None:
    lib = load_vcam()
    log.info('Loaded %s', DYLIB)

    rc = lib.vcam_start(WIDTH, HEIGHT, FPS)
    if rc != 0:
        log.error('vcam_start failed (rc=%d) — is the OBS DAL plugin installed?', rc)
        log.error('Expected at: /Library/CoreMediaIO/Plug-Ins/DAL/obs-mac-virtualcam.plugin')
        sys.exit(1)
    log.info('Virtual camera ready (%dx%d @ %d fps) — "OBS Virtual Camera" should now appear in call apps', WIDTH, HEIGHT, FPS)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    log.info('Frame server listening on %s:%d', HOST, PORT)

    try:
        while True:
            conn, addr = srv.accept()
            handle(conn, lib)
    finally:
        lib.vcam_stop()
        srv.close()


if __name__ == '__main__':
    main()
