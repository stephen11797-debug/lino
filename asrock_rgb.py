#!/usr/bin/env python3
# Copyright (c) 2026 Stephen's Studio
# Licensed under the MIT License. See LICENSE file for details.
"""Direct control of the ASRock Polychrome V1 RGB controller (i2c-0 @ 0x6A).

Zones:
  0 = RGB LED 1 Header (12V)   3 = IO Cover (onboard)
  1 = RGB LED 2 Header (12V)   4 = Audio
  2 = PCH                       5 = Addressable Header (5V ARGB)

Usage:
  asrock_rgb.py 5 ff0000   # set zone 5 static color (hex RGB bytes)
  asrock_rgb.py            # dump all zones
"""
import ctypes
import fcntl
import os
import sys
import time

I2C_SLAVE_FORCE = 0x0706
I2C_SMBUS = 0x0720
I2C_SMBUS_BLOCK_DATA = 5


class _S(ctypes.Structure):
    _fields_ = [("rw", ctypes.c_uint8), ("cmd", ctypes.c_uint8),
                ("size", ctypes.c_uint32),
                ("data", ctypes.POINTER(ctypes.c_uint8))]


def _blk(fd, rw, cmd, buf):
    a = (ctypes.c_uint8 * 33)()
    a[0] = len(buf)
    for i, b in enumerate(buf):
        a[i + 1] = b
    d = _S(rw, cmd, I2C_SMBUS_BLOCK_DATA,
           ctypes.cast(a, ctypes.POINTER(ctypes.c_uint8)))
    fcntl.ioctl(fd, I2C_SMBUS, d)
    return bytes(a[1:1 + a[0]])


def _open():
    fd = os.open("/dev/i2c-0", os.O_RDWR)
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, 0x6A)
    return fd


def set_zone(fd, zone, color, mode=0x11):
    blk = lambda rw, cmd, b: _blk(fd, rw, cmd, b)
    blk(0, 0x32, b"\x00"); time.sleep(0.005)
    blk(0, 0x31, bytes([zone])); time.sleep(0.005)
    blk(0, 0x30, bytes([mode])); time.sleep(0.005)
    blk(0, mode, bytes(color)); time.sleep(0.005)


def get_zone(fd, zone):
    blk = lambda rw, cmd, b: _blk(fd, rw, cmd, b)
    blk(0, 0x31, bytes([zone])); time.sleep(0.005)
    mode = blk(1, 0x30, b"\x00")
    if not mode:
        return None
    col = blk(1, mode[0], b"\x00" * 3)
    return mode[0], col[:3]


def main():
    fd = _open()
    if len(sys.argv) == 3:
        zone = int(sys.argv[1])
        color = bytes.fromhex(sys.argv[2])
        set_zone(fd, zone, color)
        m, c = get_zone(fd, zone)
        print(f"zone {zone}: mode=0x{m:02x} color={c.hex()}")
    else:
        sizes = _blk(fd, 1, 0x33, b"\x00" * 6)
        print("zone sizes:", list(sizes))
        for z in range(6):
            g = get_zone(fd, z)
            if g:
                print(f"zone {z}: mode=0x{g[0]:02x} color={g[1].hex()}")
    os.close(fd)


if __name__ == "__main__":
    main()
