# Copyright (c) 2026 Stephen's Studio
# Licensed under the MIT License. See LICENSE file for details.
import math
import struct

import usb.core
import usb.util

VENDOR_ID = 0x194f
PRODUCT_ID = 0x010d

MODE_BUTTON = 0x00
MODE_CHANNEL = 0x64
MODE_BUS = 0x65

FIX1_CMD = 0x50617269
FIX2_CMD = 0x14
FIX1_STATE = 0x64656d73
FIX2_STATE = 0xf4

UNITY = 0x01000000
MUTED = 0x00000000
ZERO_DBFS = 0x80000000

REQ_SET = 160
REQ_STATE_OUT = 161
REQ_STATE_IN = 162


class Button:
    LINE = 0x00
    MUTE = 0x01
    MONO = 0x02
    PHANTOM = 0x04


class Channel:
    LEFT = 0x00
    RIGHT = 0x01


def db_to_gain(db):
    db = max(-120.0, min(10.0, db))
    return int(UNITY * (10.0 ** (db / 20.0)))


def gain_to_db(value):
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(value / ZERO_DBFS)


class Value:
    class _DB:
        def __init__(self, d):
            self.d = d

        def to_gain(self):
            return db_to_gain(self.d)

    class _Gain:
        def __init__(self, g):
            self.g = g

        def to_gain(self):
            return self.g

    @staticmethod
    def DB(d):
        return Value._DB(d)

    @staticmethod
    def Gain(g):
        return Value._Gain(g)

    @staticmethod
    def Unity():
        return Value._Gain(UNITY)

    @staticmethod
    def Muted():
        return Value._Gain(MUTED)


def _coerce(value):
    if isinstance(value, int):
        return value
    if hasattr(value, "to_gain"):
        return value.to_gain()
    raise TypeError(value)


class Command:
    def __init__(self):
        self.mode = MODE_CHANNEL
        self.input_strip = 0
        self.output_bus = 0
        self.output_channel = Channel.LEFT
        self.button = Button.LINE
        self.value = 0

    def set_button(self, button, on):
        self.mode = MODE_BUTTON
        self.input_strip = 0
        self.output_bus = 0
        self.button = button
        self.value = 1 if on else 0
        return self

    def set_input_fader(self, inp, out, channel, value):
        self.mode = MODE_CHANNEL
        self.input_strip = max(0, min(35, inp))
        self.output_bus = max(0, min(8, out))
        self.output_channel = channel
        self.value = _coerce(value)
        return self

    def set_output_fader(self, out, value):
        self.mode = MODE_BUS
        self.output_bus = max(0, min(8, out))
        self.value = _coerce(value)
        return self

    def array(self):
        arr = bytearray(28)
        if self.mode == MODE_BUTTON:
            v = 0
        elif self.mode == MODE_CHANNEL:
            v = self.input_strip
        else:
            v = self.output_bus
        struct.pack_into("<I", arr, 0, self.mode)
        struct.pack_into("<I", arr, 4, v)
        struct.pack_into("<I", arr, 8, FIX1_CMD)
        struct.pack_into("<I", arr, 12, FIX2_CMD)
        struct.pack_into("<I", arr, 16, self.output_bus)
        if self.mode == MODE_BUTTON:
            v2 = self.button
        elif self.mode == MODE_CHANNEL:
            v2 = self.output_channel
        else:
            v2 = Channel.LEFT
        struct.pack_into("<I", arr, 20, v2)
        struct.pack_into("<I", arr, 24, self.value)
        return arr


class State:
    def __init__(self):
        self.counter = 1
        self.mic = [0] * 8
        self.spdif = [0] * 2
        self.adat = [0] * 8
        self.daw = [0] * 18
        self.bus = [0] * 18
        self.phantom = 0
        self.line = 0
        self.mute = 0
        self.mono = 0

    def reset(self):
        self.mic = [0] * 8
        self.spdif = [0] * 2
        self.adat = [0] * 8
        self.daw = [0] * 18
        self.bus = [0] * 18
        self.phantom = 0
        self.line = 0
        self.mute = 0
        self.mono = 0

    def array(self):
        arr = bytearray(252)
        struct.pack_into("<I", arr, 0, 0)
        struct.pack_into("<I", arr, 4, 0)
        struct.pack_into("<I", arr, 8, FIX1_STATE)
        struct.pack_into("<I", arr, 12, FIX2_STATE)
        for i, m in enumerate(self.mic):
            struct.pack_into("<I", arr, 16 + 4 * i, m)
        for i, m in enumerate(self.spdif):
            struct.pack_into("<I", arr, 48 + 4 * i, m)
        for i, m in enumerate(self.adat):
            struct.pack_into("<I", arr, 56 + 4 * i, m)
        for i, m in enumerate(self.daw):
            struct.pack_into("<I", arr, 88 + 4 * i, m)
        for i, m in enumerate(self.bus):
            struct.pack_into("<I", arr, 160 + 4 * i, m)
        struct.pack_into("<I", arr, 232, self.phantom)
        struct.pack_into("<I", arr, 236, self.line)
        struct.pack_into("<I", arr, 240, self.mute)
        struct.pack_into("<I", arr, 244, self.mono)
        struct.pack_into("<I", arr, 248, 0)
        return arr

    def parse(self, data):
        def u32(o):
            return struct.unpack_from("<I", data, o)[0]

        for i in range(8):
            self.mic[i] = u32(16 + 4 * i)
        for i in range(8):
            self.adat[i] = u32(56 + 4 * i)
        for i in range(2):
            self.spdif[i] = u32(48 + 4 * i)
        for i in range(18):
            self.daw[i] = u32(88 + 4 * i)
        for i in range(18):
            self.bus[i] = u32(160 + 4 * i)
        self.phantom = data[232]
        self.line = data[236]
        self.mute = data[240]
        self.mono = data[244]

    def poll(self, dev):
        self.reset()
        dev.ctrl_transfer(0x40, REQ_STATE_OUT, self.counter, 0, self.array())
        resp = dev.ctrl_transfer(0xC0, REQ_STATE_IN, self.counter, 0, 252)
        if self.counter == 0xFFFF:
            self.counter = 0
        self.counter += 1
        self.parse(resp)


def find_device():
    return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)


class Device:
    def __init__(self):
        self.dev = find_device()
        if self.dev is None:
            raise RuntimeError("Studio 1824c not found")
        self._detached = []
        self._ensure_access()

    def _ensure_access(self):
        try:
            cfg = self.dev.get_active_configuration()
        except Exception:
            cfg = None
        intfs = []
        if cfg is not None:
            for intf in cfg:
                intfs.append(intf.bInterfaceNumber)
        else:
            for cfg in self.dev:
                for intf in cfg:
                    if intf.bInterfaceNumber not in intfs:
                        intfs.append(intf.bInterfaceNumber)
        for n in intfs:
            if self.dev.is_kernel_driver_active(n):
                try:
                    self.dev.detach_kernel_driver(n)
                    self._detached.append(n)
                except Exception:
                    pass

    def send(self, command):
        self.dev.ctrl_transfer(0x40, REQ_SET, 0, 0, command.array())

    def poll(self, state):
        state.poll(self.dev)

    def close(self):
        for n in self._detached:
            try:
                self.dev.attach_kernel_driver(n)
            except Exception:
                pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


import subprocess
import re

ALSA_CARD = "S1824c"

_ALSA_NAME = {
    Button.PHANTOM: "48V Phantom Power On Mic Inputs Switch",
    Button.LINE: "Line 1/2 Source Type",
    Button.MONO: "Mono Main Out Switch",
    Button.MUTE: "Mute Main Out Switch",
}

_ALSA_NUMID = {}


def _resolve_numid(name):
    if name in _ALSA_NUMID:
        return _ALSA_NUMID[name]
    try:
        out = subprocess.run(
            ["amixer", "-c", ALSA_CARD, "controls"],
            capture_output=True, text=True,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        m = re.match(r"numid=(\d+),.*name='([^']+)'", line)
        if m and m.group(2) == name:
            _ALSA_NUMID[name] = int(m.group(1))
            return _ALSA_NUMID[name]
    return None


def _amixer(args):
    return subprocess.run(
        ["amixer", "-c", ALSA_CARD] + args, capture_output=True, text=True
    )


def alsa_get(button):
    name = _ALSA_NAME[button]
    nid = _resolve_numid(name)
    if nid is None:
        return None
    out = _amixer(["cget", "numid=%d" % nid]).stdout
    for line in out.splitlines():
        s = line.strip()
        if s.startswith(": values="):
            val = s.split("=", 1)[1].strip()
            if button == Button.LINE:
                try:
                    return int(val)
                except ValueError:
                    return 1 if val.lower() == "line" else 0
            return val == "on"
    return None


def alsa_set(button, value):
    name = _ALSA_NAME[button]
    nid = _resolve_numid(name)
    if nid is None:
        return
    if button == Button.LINE:
        _amixer(["cset", "numid=%d" % nid, "%d" % (1 if value else 0)])
    else:
        _amixer(["cset", "numid=%d" % nid, "on" if value else "off"])


CLOCK_NAME = "PreSonus Clock Selector Clock Source"


def alsa_numid(name):
    return _resolve_numid(name)


def alsa_enum_get(numid):
    out = _amixer(["cget", "numid=%d" % numid]).stdout
    for line in out.splitlines():
        s = line.strip()
        if s.startswith(": values="):
            try:
                return int(s.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def alsa_enum_set(numid, index):
    _amixer(["cset", "numid=%d" % numid, "%d" % index])
