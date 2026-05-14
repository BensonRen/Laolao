"""
virtual_cam.py — receives JPEG frames from the Electron main process via a
local TCP socket and pushes them to the OBS Virtual Camera DAL plugin via
pyvirtualcam, making the composited overlay appear as a real system camera.

Frame protocol (length-prefixed):
  [4 bytes big-endian uint32 = JPEG length] [JPEG bytes]

Run: python virtual_cam.py
Requires: pyvirtualcam, Pillow, numpy
          OBS Virtual Camera plugin installed at
          /Library/CoreMediaIO/Plug-Ins/DAL/obs-mac-virtualcam.plugin
"""

import io
import logging
import socket
import struct

import numpy as np
import pyvirtualcam
from PIL import Image

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)

HOST     = '127.0.0.1'
PORT     = 8766
WIDTH    = 1280
HEIGHT   = 720
FPS      = 30


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('socket closed')
        buf += chunk
    return bytes(buf)


def handle(conn: socket.socket, cam: pyvirtualcam.Camera) -> None:
    log.info('Electron connected — streaming frames to %s', cam.device)
    try:
        while True:
            length = struct.unpack('>I', recv_exact(conn, 4))[0]
            jpeg   = recv_exact(conn, length)

            img   = Image.open(io.BytesIO(jpeg)).convert('RGB')
            if img.size != (WIDTH, HEIGHT):
                img = img.resize((WIDTH, HEIGHT), Image.BILINEAR)

            cam.send(np.asarray(img))
    except (ConnectionError, OSError):
        log.info('Electron disconnected')
    finally:
        conn.close()


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    log.info('Frame server listening on %s:%d', HOST, PORT)

    try:
        with pyvirtualcam.Camera(
            width=WIDTH, height=HEIGHT, fps=FPS,
            fmt=pyvirtualcam.PixelFormat.RGB,
        ) as cam:
            log.info('Virtual camera ready: %s  (%dx%d @ %dfps)', cam.device, WIDTH, HEIGHT, FPS)
            while True:
                conn, addr = srv.accept()
                handle(conn, cam)
    except Exception as e:
        log.error('Virtual camera error: %s', e)
        log.error('Is obs-mac-virtualcam.plugin installed at /Library/CoreMediaIO/Plug-Ins/DAL/?')
        raise
    finally:
        srv.close()


if __name__ == '__main__':
    main()
