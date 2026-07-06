"""
virtual_cam.py — receives JPEG frames from Electron via TCP and pushes them
to the OBS Camera Extension using pyvirtualcam (0.15+, arm64 native).

The OBS Camera Extension (com.obsproject.obs-studio.mac-camera-extension)
is what Zoom / FaceTime / WeChat actually enumerate — not the old DAL plugin.

Frame protocol: [4-byte big-endian uint32 length] [JPEG bytes]

Usage: python virtual_cam.py [width] [height] [fps]
"""

import io
import logging
import os
import socket
import struct
import sys
import tempfile

import numpy as np
import pyvirtualcam
from PIL import Image

log = logging.getLogger(__name__)
_log_path = os.path.join(tempfile.gettempdir(), 'laolao_vcam.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(_log_path),
    ],
)

HOST   = '127.0.0.1'
PORT   = 8766
WIDTH  = int(sys.argv[1]) if len(sys.argv) > 1 else 1280
HEIGHT = int(sys.argv[2]) if len(sys.argv) > 2 else 720
FPS    = int(sys.argv[3]) if len(sys.argv) > 3 else 30


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('socket closed')
        buf += chunk
    return bytes(buf)


def jpeg_to_rgb(jpeg: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(jpeg)).convert('RGB')
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def handle(conn: socket.socket, cam: pyvirtualcam.Camera) -> None:
    log.info('Electron connected — streaming frames to virtual camera')
    try:
        while True:
            length = struct.unpack('>I', recv_exact(conn, 4))[0]
            jpeg   = recv_exact(conn, length)
            frame  = jpeg_to_rgb(jpeg)
            cam.send(frame)
    except (ConnectionError, OSError):
        log.info('Electron disconnected')
    finally:
        conn.close()


def main() -> None:
    log.info('Starting pyvirtualcam %s (%dx%d @ %d fps)', pyvirtualcam.__version__, WIDTH, HEIGHT, FPS)

    with pyvirtualcam.Camera(width=WIDTH, height=HEIGHT, fps=FPS, fmt=pyvirtualcam.PixelFormat.RGB) as cam:
        log.info('Virtual camera ready — "%s" now appears in Zoom / FaceTime / WeChat', cam.device)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen(1)
        log.info('Frame server listening on %s:%d', HOST, PORT)

        try:
            while True:
                conn, _ = srv.accept()
                handle(conn, cam)
        finally:
            srv.close()


if __name__ == '__main__':
    main()
