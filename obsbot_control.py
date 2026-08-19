#!/usr/bin/env python3
"""
OBSBOT Tiny 2 Lite — control library + CLI (no vendor SDK).

Talks to the camera over its standard UVC/USB interface:

  * V4L2 controls   -> gimbal pan/tilt (absolute), zoom, focus, exposure,
                       brightness/contrast/saturation/hue/sharpness, WB.
  * UVC XU unit 2   -> vendor commands (wake/sleep, recenter, AI tracking,
                       HDR, FOV, presets) via UVCIOC_CTRL_QUERY.

Command framing (CRC-16/USB) and command table per plasticparticle's
protocol notes, verified live on a Tiny 2 Lite.

Requires only Python 3 stdlib + kernel uvcvideo.
"""

import argparse
import ctypes
import errno
import glob
import struct
import sys

V4L2_CID_BRIGHTNESS = 0x00980900
V4L2_CID_CONTRAST = 0x00980901
V4L2_CID_SATURATION = 0x00980902
V4L2_CID_HUE = 0x00980903
V4L2_CID_WHITE_BALANCE_TEMPERATURE = 0x0098091A
V4L2_CID_SHARPNESS = 0x0098091B
V4L2_CID_BACKLIGHT_COMPENSATION = 0x0098091C
V4L2_CID_GAIN = 0x00980913
V4L2_CID_AUTO_WHITE_BALANCE = 0x0098090C
V4L2_CID_POWER_LINE_FREQUENCY = 0x00980918

V4L2_CID_AUTO_EXPOSURE = 0x009A0901
V4L2_CID_EXPOSURE = 0x009A0902
V4L2_CID_PAN_ABSOLUTE = 0x009A0908
V4L2_CID_TILT_ABSOLUTE = 0x009A0909
V4L2_CID_FOCUS_ABSOLUTE = 0x009A090A
V4L2_CID_FOCUS_AUTO = 0x009A090C
V4L2_CID_ZOOM_ABSOLUTE = 0x009A090D
V4L2_CID_PAN_SPEED = 0x009A0920
V4L2_CID_TILT_SPEED = 0x009A0921

V4L2_CTRL_FLAG_DISABLED = 0x0001

UVC_SET_CUR = 0x01
UVC_GET_CUR = 0x81
UVC_GET_MIN = 0x82
UVC_GET_MAX = 0x83
UVC_GET_LEN = 0x85

V4L2_CAP_METADATA_CAPTURE = 0x00800000

# ---- ioctl encoding (mirrors kernel _IOC macros) ----
_IOC_NRBITS, _IOC_TYPEBITS, _IOC_SIZEBITS = 8, 8, 14
_IOC_DIRSHIFT = 30
_IOC_TYPESHIFT = 8
_IOC_SIZESHIFT = 16
_IOC_READ, _IOC_WRITE = 2, 1


def _ioc(direction, ctype, nr, size):
    return (direction << _IOC_DIRSHIFT) | (ord(ctype) << _IOC_TYPESHIFT) \
        | (nr << 0) | (size << _IOC_SIZESHIFT)


def _ior(ctype, nr, size):
    return _ioc(_IOC_READ, ctype, nr, size)


def _iowr(ctype, nr, size):
    return _ioc(_IOC_READ | _IOC_WRITE, ctype, nr, size)


# v4l2_capability is 16+32+32+4+4+4+12 = 104 bytes
VIDIOC_QUERYCAP = _ior(b"V", 0, 104)
VIDIOC_G_CTRL = _iowr(b"V", 27, ctypes.sizeof(ctypes.c_uint32) * 2)
VIDIOC_S_CTRL = _iowr(b"V", 28, ctypes.sizeof(ctypes.c_uint32) * 2)
VIDIOC_QUERYCTRL = _iowr(b"V", 36, 68)  # struct v4l2_queryctrl
VIDIOC_ENUM_FMT = _iowr(b"V", 2, 64)  # struct v4l2_fmtdesc

# uvc_xu_control_query: 3*u8 + u16 + ptr, natural alignment = 16 bytes
UVCIOC_CTRL_QUERY = _iowr(b"u", 0x21, 16)


class V4l2Control(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("value", ctypes.c_int32)]


class V4l2QueryCtrl(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("ctrl_type", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
        ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32),
        ("step", ctypes.c_int32),
        ("default_value", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    ]


class V4l2FmtDesc(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("description", ctypes.c_char * 32),
        ("pixelformat", ctypes.c_uint32),
        ("mbus_code", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


V4L2_PIX_FMT_MJPEG = 0x47504A4D  # 'MJPG'
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1


class UvcXuQuery(ctypes.Structure):
    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_void_p),
    ]


class CtrlRange:
    def __init__(self, minimum, maximum, step, default_value, name):
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.default_value = default_value
        self.name = name

    def __repr__(self):
        return "<CtrlRange {} [{},{}] step {} default {}>".format(
            self.name, self.minimum, self.maximum, self.step, self.default_value)


# ---- CRC-16/USB (poly 0xA001 reflected, init 0xFFFF, xorout 0xFFFF) ----
def crc16_usb(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def build_v3(cmd, receiver, payload, seq=1, sender=0x0A):
    """Build a 60-byte framed V3 command for XU selector 2."""
    data = bytes(payload)
    len2 = len(data)
    if len2 > 44:
        raise ValueError("payload too long for V3 frame: %d" % len2)
    frame = bytearray(60)
    frame[0] = 0xAA
    frame[1] = 0x25
    struct.pack_into("<H", frame, 2, seq & 0xFFFF)
    struct.pack_into("<H", frame, 4, 0x0C)
    frame[8] = sender
    frame[9] = receiver
    struct.pack_into("<H", frame, 10, cmd)
    struct.pack_into("<H", frame, 12, len2)
    # token2: CRC over len2 u16 + zeroed token2 + len2 data bytes
    token2 = crc16_usb(frame[12:14] + b"\x00\x00" + data)
    struct.pack_into("<H", frame, 14, token2)
    frame[16:16 + len2] = data
    # token1: CRC over frame[0:12] with token field zeroed
    token = crc16_usb(frame[0:6] + b"\x00\x00" + frame[8:12])
    struct.pack_into("<H", frame, 6, token)
    return bytes(frame)


# ---- XU selector 6, simple commands (SET_CUR) ----
#   HDR  : 01 01 00/01
#   FOV  : 04 01 01/02/03   (wide/normal/narrow)
#   face-priority AE: 03 01 00/01
#   AI   : 16 02 <m> <n>
AI_MODES = {
    "none": (0, 0), "group": (1, 0), "normal": (2, 0),
    "upper-body": (2, 1), "close-up": (2, 2), "headless": (2, 3),
    "lower-body": (2, 4), "whiteboard": (4, 0), "desk": (5, 0),
    "hand": (6, 0),
}
AI_MODES_REV = {v: k for k, v in AI_MODES.items()}

FOV_MODES = {"wide": 1, "normal": 2, "narrow": 3}
FOV_MODES_REV = {v: k for k, v in FOV_MODES.items()}

# V3 command table (cmd, receiver)
CMD_WAKE = (0xA0C2, 0x02)
CMD_RECENTER = (0x00C3, 0x03)
CMD_GIMBAL_SPEED = (0x6484, 0x04)
CMD_TRACKING_SPEED = (0x0CC4, 0x04)
CMD_PRESET_ADD = (0x3944, 0x04)
CMD_PRESET_UPDATE = (0x3E04, 0x04)
CMD_PRESET_RECALL = (0x39C4, 0x04)
CMD_PRESET_DELETE = (0x3984, 0x04)


class ObsbotError(Exception):
    pass


class Obsbot:
    def __init__(self, devnode, seq=1):
        self.devnode = devnode
        self.seq = seq
        self.fd = os_open(devnode)
        self.cap = self._querycap()

    # ---- discovery ----
    @staticmethod
    def find():
        cams = []
        for path in sorted(glob.glob("/dev/video*")):
            try:
                fd = os_open(path)
            except OSError:
                continue
            cap = _querycap_fd(fd)
            if not cap:
                os_close(fd)
                continue
            card, bus = cap["card"].decode("utf-8", "replace"), cap["bus_info"].decode(
                "utf-8", "replace")
            if "OBSBOT" not in card.upper() and "OBSBOT" not in bus.upper():
                os_close(fd)
                continue
            if cap["device_caps"] & V4L2_CAP_METADATA_CAPTURE:
                os_close(fd)
                continue
            cams.append(Obsbot(path))
        return cams

    def _querycap(self):
        return _querycap_fd(self.fd)

    def describe(self):
        return "{}  ({}, bus {})".format(self.devnode, self.cap["card"].decode(
            "utf-8", "replace").rstrip("\x00"),
            self.cap["bus_info"].decode("utf-8", "replace").rstrip("\x00"))

    # ---- V4L2 controls ----
    def get_ctrl(self, cid):
        c = V4l2Control(cid, 0)
        if _ioctl(self.fd, VIDIOC_G_CTRL, c) != 0:
            raise ObsbotError("VIDIOC_G_CTRL 0x%08x failed" % cid)
        return c.value

    def set_ctrl(self, cid, value):
        c = V4l2Control(cid, int(value))
        if _ioctl(self.fd, VIDIOC_S_CTRL, c) != 0:
            raise ObsbotError("VIDIOC_S_CTRL 0x%08x = %d failed" % (cid, value))
        return c.value

    def query_ctrl(self, cid):
        q = V4l2QueryCtrl()
        q.id = cid
        if _ioctl(self.fd, VIDIOC_QUERYCTRL, q) != 0:
            return None
        if q.flags & V4L2_CTRL_FLAG_DISABLED:
            return None
        return CtrlRange(q.minimum, q.maximum, q.step, q.default_value,
                         q.name.decode("utf-8", "replace").strip())

    def ctrl_list(self):
        out = []
        for cid, label in COMMON_IDS.items():
            meta = _ctrl_meta(self.fd, cid, label)
            if meta is not None:
                out.append((cid, meta[0], meta[1]))
        return out

    # ---- UVC XU ----
    def _xu(self, selector, query, buf, retries=3):
        import time as _t
        last = None
        for attempt in range(retries + 1):
            arr = (ctypes.c_char * len(buf)).from_buffer_copy(buf)
            q = UvcXuQuery()
            q.unit = 2
            q.selector = selector
            q.query = query
            q.size = len(buf)
            q.data = ctypes.addressof(arr)
            rc = _ioctl(self.fd, UVCIOC_CTRL_QUERY, q)
            if rc == 0:
                return arr.raw
            last = rc
            if attempt < retries:
                _t.sleep(0.1 * (attempt + 1))
        raise ObsbotError("UVCIOC XU %d/%d q=%02x failed (%s)"
                          % (selector, query, query,
                             errno.errorcode.get(last, last)))

    def _xu_len(self, selector):
        buf = b"\x00\x00"
        out = self._xu(selector, UVC_GET_LEN, buf)
        return struct.unpack("<H", out[:2])[0]

    def get_cur(self, selector, length=None):
        if length is None:
            length = self._xu_len(selector)
        out = self._xu(selector, UVC_GET_CUR, b"\x00" * length)
        return out

    def set_cur(self, selector, data, length=None):
        if length is None:
            length = self._xu_len(selector)
        buf = bytes(data)
        if len(buf) < length:
            buf += b"\x00" * (length - len(buf))
        return self._xu(selector, UVC_SET_CUR, buf)

    # ---- simple XU commands (selector 6) ----
    def _simple(self, cmd_bytes):
        self.set_cur(0x06, cmd_bytes)

    def set_hdr(self, on):
        self._simple(b"\x01\x01" + (b"\x01" if on else b"\x00"))

    def set_face_ae(self, on):
        self._simple(b"\x03\x01" + (b"\x01" if on else b"\x00"))

    def set_fov(self, mode):
        if mode not in FOV_MODES:
            raise ValueError("fov must be one of %s" % list(FOV_MODES))
        self._simple(b"\x04\x01" + bytes([FOV_MODES[mode]]))

    def set_ai_mode(self, mode):
        if mode not in AI_MODES:
            raise ValueError("ai mode must be one of %s" % list(AI_MODES))
        m, n = AI_MODES[mode]
        self._simple(b"\x16\x02" + bytes([m, n]))

    def set_noise_reduction(self, on):
        self._simple(b"\x0a\x01" + (b"\x01" if on else b"\x00"))

    # ---- framed V3 commands (selector 2) ----
    def _v3(self, cmd, receiver, payload, wake_first=True):
        if wake_first:
            try:
                st = self.status()
                if st.get("sleep"):
                    self.set_cur(0x02, build_v3(*CMD_WAKE, b"\x00"))
                    time_sleep(0.3)
            except ObsbotError:
                pass
        frame = build_v3(cmd, receiver, payload, seq=self.seq)
        self.seq = (self.seq + 1) & 0xFFFF
        self.set_cur(0x02, frame)

    def wake(self):
        self._v3(*CMD_WAKE, b"\x00\x00\x00\x00", wake_first=False)

    def sleep(self):
        self._v3(*CMD_WAKE, b"\x01\x00\x00\x00", wake_first=False)

    def recenter(self):
        self._v3(*CMD_RECENTER, b"\x00\x00\x00\x00\x00\x00")

    def set_tracking_speed(self, speed):
        """0 = standard, 2 = sport."""
        self._v3(*CMD_TRACKING_SPEED, bytes([speed]))

    def set_gimbal_speed(self, roll=0.0, pitch=0.0, yaw=0.0):
        self._v3(*CMD_GIMBAL_SPEED, struct.pack("<fff", roll, pitch, yaw))

    # ---- presets ----
    def _preset_pose(self, pan_deg, pitch_deg, roll_deg, zoom_pct):
        return struct.pack("<fffff", float(pan_deg), float(pitch_deg),
                           float(roll_deg), float(zoom_pct), -1000.0)

    def preset_save(self, slot, pan_deg=None, pitch_deg=None, zoom_pct=None):
        if pan_deg is None:
            pan_deg = self.get_ctrl(V4L2_CID_PAN_ABSOLUTE) / 3600.0
        if pitch_deg is None:
            pitch_deg = -self.get_ctrl(V4L2_CID_TILT_ABSOLUTE) / 3600.0
        if zoom_pct is None:
            zoom_pct = float(self.get_ctrl(V4L2_CID_ZOOM_ABSOLUTE))
        payload = struct.pack("<I", slot) + self._preset_pose(pan_deg, pitch_deg, 0.0, zoom_pct)
        self._v3(*CMD_PRESET_ADD, payload)

    def preset_update(self, slot, pan_deg=None, pitch_deg=None, zoom_pct=None):
        if pan_deg is None:
            pan_deg = self.get_ctrl(V4L2_CID_PAN_ABSOLUTE) / 3600.0
        if pitch_deg is None:
            pitch_deg = -self.get_ctrl(V4L2_CID_TILT_ABSOLUTE) / 3600.0
        if zoom_pct is None:
            zoom_pct = float(self.get_ctrl(V4L2_CID_ZOOM_ABSOLUTE))
        payload = struct.pack("<I", slot) + self._preset_pose(pan_deg, pitch_deg, 0.0, zoom_pct)
        self._v3(*CMD_PRESET_UPDATE, payload)

    def preset_recall(self, slot):
        payload = struct.pack("<I", slot) + struct.pack("<ffff", 1.0, 1.0, 1.0, 1.0)
        self._v3(*CMD_PRESET_RECALL, payload)

    def preset_delete(self, slot):
        self._v3(*CMD_PRESET_DELETE, struct.pack("<I", slot))

    def preset_list(self):
        """Read presets via flat selectors 12/13 (returns list of dicts)."""
        count = self.get_cur(12, 60)
        n = count[0]
        slots = list(count[1:1 + n])
        if slots:
            self.set_cur(12, count[:1 + n], 60)
        out = []
        for _ in range(len(slots)):
            e = self.get_cur(13, 60)
            if e[0] == 0x02:
                break
            pitch = struct.unpack("<h", e[4:6])[0] / 100.0
            yaw = struct.unpack("<h", e[6:8])[0] / 100.0
            zoom = e[8] / 100.0
            name = e[10:].split(b"\x00")[0].decode("utf-8", "replace")
            out.append({
                "slot": e[1] + 1, "pan": yaw, "pitch": pitch,
                "zoom": zoom, "name": name,
            })
        return out

    # ---- status ----
    def status(self):
        st = self.get_cur(0x06)
        m, n = st[0x18], st[0x1C]
        return {
            "sleep": bool(st[0x02]),
            "hdr": bool(st[0x06]),
            "ai_mode": AI_MODES_REV.get((m, n), "%d,%d" % (m, n)),
            "ai_m": m, "ai_n": n,
            "track_speed": st[0x21],
        }


# common control ids probed for dynamic UIs (V4L2 type: 0=INTEGER,
# 1=BOOLEAN, 2=MENU, 8=INTEGER_MENU)
COMMON_IDS = {
    V4L2_CID_BRIGHTNESS: "Brightness",
    V4L2_CID_CONTRAST: "Contrast",
    V4L2_CID_SATURATION: "Saturation",
    V4L2_CID_HUE: "Hue",
    0x0098090C: "Auto white balance",
    V4L2_CID_GAIN: "Gain",
    V4L2_CID_POWER_LINE_FREQUENCY: "Power line frequency",
    V4L2_CID_WHITE_BALANCE_TEMPERATURE: "White balance",
    V4L2_CID_SHARPNESS: "Sharpness",
    V4L2_CID_BACKLIGHT_COMPENSATION: "Backlight compensation",
    V4L2_CID_AUTO_EXPOSURE: "Auto exposure",
    V4L2_CID_EXPOSURE: "Exposure",
    V4L2_CID_FOCUS_ABSOLUTE: "Focus",
    V4L2_CID_FOCUS_AUTO: "Autofocus",
    V4L2_CID_ZOOM_ABSOLUTE: "Zoom",
}

V4L2_CTRL_FLAG_READONLY = 0x0002
V4L2_CTRL_TYPE_INTEGER = 0
V4L2_CTRL_TYPE_BOOLEAN = 1
V4L2_CTRL_TYPE_MENU = 2
V4L2_CTRL_TYPE_INTEGER_MENU = 8


def _ctrl_meta(fd, cid, label):
    """Return (CtrlRange, ctrl_type) for a control, or None if unsupported."""
    q = V4l2QueryCtrl()
    q.id = cid
    if _ioctl(fd, VIDIOC_QUERYCTRL, q) != 0:
        return None
    if q.flags & (V4L2_CTRL_FLAG_DISABLED | V4L2_CTRL_FLAG_READONLY):
        return None
    name = label or q.name.decode("utf-8", "replace").strip()
    r = CtrlRange(q.minimum, q.maximum, q.step, q.default_value, name)
    return r, q.ctrl_type


class Webcam:
    """Generic UVC webcam: V4L2 controls + discovery, no OBSBOT extras."""

    def __init__(self, devnode):
        self.devnode = devnode
        self.fd = os_open(devnode)
        self.cap = self._querycap()

    def _querycap(self):
        return _querycap_fd(self.fd)

    def describe(self):
        return "{}  ({}, bus {})".format(self.devnode, self.cap["card"].decode(
            "utf-8", "replace").rstrip("\x00"),
            self.cap["bus_info"].decode("utf-8", "replace").rstrip("\x00"))

    def get_ctrl(self, cid):
        c = V4l2Control(cid, 0)
        if _ioctl(self.fd, VIDIOC_G_CTRL, c) != 0:
            raise ObsbotError("VIDIOC_G_CTRL 0x%08x failed" % cid)
        return c.value

    def set_ctrl(self, cid, value):
        c = V4l2Control(cid, int(value))
        if _ioctl(self.fd, VIDIOC_S_CTRL, c) != 0:
            raise ObsbotError("VIDIOC_S_CTRL 0x%08x = %d failed" % (cid, value))
        return c.value

    def query_ctrl(self, cid):
        r = _ctrl_meta(self.fd, cid, None)
        return r[0] if r else None

    def ctrl_list(self):
        out = []
        for cid, label in COMMON_IDS.items():
            meta = _ctrl_meta(self.fd, cid, label)
            if meta is not None:
                out.append((cid, meta[0], meta[1]))
        return out

    def status(self):
        return {"sleep": False, "hdr": None, "ai_mode": "none",
                "ai_m": 0, "ai_n": 0, "track_speed": 0}


def discover():
    """Return (devnode, kind, cap) for every V4L2 video-capture device."""
    found = []
    for path in sorted(glob.glob("/dev/video*")):
        try:
            fd = os_open(path)
        except OSError:
            continue
        cap = _querycap_fd(fd)
        if not cap:
            os_close(fd)
            continue
        if cap["device_caps"] & V4L2_CAP_METADATA_CAPTURE:
            os_close(fd)
            continue
        if not cap["device_caps"] & 0x00000001:  # V4L2_CAP_VIDEO_CAPTURE
            os_close(fd)
            continue
        card = cap["card"].decode("utf-8", "replace")
        bus = cap["bus_info"].decode("utf-8", "replace")
        kind = "obsbot" if ("OBSBOT" in card.upper()
                            or "OBSBOT" in bus.upper()) else "generic"
        os_close(fd)
        found.append((path, kind, cap))
    return found


def os_open(path):
    import os
    try:
        return os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        if e.errno == errno.EACCES:
            raise ObsbotError("permission denied opening %s (are you in the "
                              "video group?)" % path)
        raise


def os_close(fd):
    import os
    try:
        os.close(fd)
    except OSError:
        pass


def has_mjpeg(devnode):
    """True if the device exposes the MJPG (Motion-JPEG) capture format."""
    fd = None
    try:
        fd = os_open(devnode)
        desc = V4l2FmtDesc()
        i = 0
        while True:
            desc.index = i
            desc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
            if _ioctl(fd, VIDIOC_ENUM_FMT, desc) != 0:
                break
            if desc.pixelformat == V4L2_PIX_FMT_MJPEG:
                return True
            i += 1
        return False
    finally:
        if fd is not None:
            os_close(fd)


def _ioctl(fd, request, arg):
    import fcntl
    try:
        fcntl.ioctl(fd, request, arg)
        return 0
    except OSError as e:
        return e.errno


def _querycap_fd(fd):
    buf = bytearray(104)
    import fcntl
    try:
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf, True)
    except OSError:
        return None
    driver, card, bus = struct.unpack_from("<16s32s32s", bytes(buf[:80]))
    version, capabilities, device_caps = struct.unpack_from("<III", bytes(buf), 80)
    return {
        "driver": driver, "card": card, "bus_info": bus,
        "version": version, "capabilities": capabilities,
        "device_caps": device_caps,
    }


def time_sleep(s):
    import time
    time.sleep(s)


# ---- CLI ----
def _pick(args):
    cams = Obsbot.find()
    if not cams:
        raise ObsbotError("no OBSBOT camera found")
    if args.device is not None:
        for c in cams:
            if args.device in c.devnode or args.device in c.cap["bus_info"].decode():
                return c
        raise ObsbotError("no OBSBOT camera matching %r" % args.device)
    if len(cams) > 1:
        print("multiple cameras found, using %s" % cams[0].describe(),
              file=sys.stderr)
    return cams[0]


def cmd_list(args):
    cams = Obsbot.find()
    if not cams:
        print("no OBSBOT cameras found")
        return 1
    for c in cams:
        print(c.describe())
        try:
            st = c.status()
            print("    sleep=%s hdr=%s ai=%s track_speed=%s" %
                  (st["sleep"], st["hdr"], st["ai_mode"], st["track_speed"]))
        except ObsbotError as e:
            print("    status: %s" % e)
    return 0


def cmd_status(args):
    c = _pick(args)
    print("camera: %s" % c.describe())
    st = c.status()
    for k, v in st.items():
        print("  %s: %s" % (k, v))
    try:
        print("  pan: %d asec (%.2f deg)" % (c.get_ctrl(V4L2_CID_PAN_ABSOLUTE),
              c.get_ctrl(V4L2_CID_PAN_ABSOLUTE) / 3600.0))
        print("  tilt: %d asec (%.2f deg)" % (c.get_ctrl(V4L2_CID_TILT_ABSOLUTE),
              c.get_ctrl(V4L2_CID_TILT_ABSOLUTE) / 3600.0))
        print("  zoom: %d" % c.get_ctrl(V4L2_CID_ZOOM_ABSOLUTE))
        print("  focus: %d (auto=%d)" % (c.get_ctrl(V4L2_CID_FOCUS_ABSOLUTE),
              c.get_ctrl(V4L2_CID_FOCUS_AUTO)))
    except ObsbotError as e:
        print("  v4l2: %s" % e)
    return 0


def cmd_ctrls(args):
    c = _pick(args)
    for cid in [V4L2_CID_BRIGHTNESS, V4L2_CID_CONTRAST, V4L2_CID_SATURATION,
                V4L2_CID_HUE, V4L2_CID_SHARPNESS, V4L2_CID_GAIN,
                V4L2_CID_BACKLIGHT_COMPENSATION, V4L2_CID_PAN_ABSOLUTE,
                V4L2_CID_TILT_ABSOLUTE, V4L2_CID_ZOOM_ABSOLUTE,
                V4L2_CID_FOCUS_ABSOLUTE, V4L2_CID_EXPOSURE]:
        r = c.query_ctrl(cid)
        if not r:
            continue
        try:
            val = c.get_ctrl(cid)
        except ObsbotError:
            val = None
        print("%-30s %6s  [%d..%d] step %d  default %d" %
              (r.name, val, r.minimum, r.maximum, r.step, r.default_value))
    return 0


def _deg_set(c, attr, value):
    if attr == "pan":
        asec = round(float(value) * 3600)
        r = c.query_ctrl(V4L2_CID_PAN_ABSOLUTE)
        asec = max(r.minimum, min(r.maximum, asec))
        c.set_ctrl(V4L2_CID_PAN_ABSOLUTE, asec)
    elif attr == "tilt":
        asec = round(float(value) * 3600)
        r = c.query_ctrl(V4L2_CID_TILT_ABSOLUTE)
        asec = max(r.minimum, min(r.maximum, asec))
        c.set_ctrl(V4L2_CID_TILT_ABSOLUTE, asec)
    elif attr == "zoom":
        value = int(value)
        r = c.query_ctrl(V4L2_CID_ZOOM_ABSOLUTE)
        c.set_ctrl(V4L2_CID_ZOOM_ABSOLUTE, max(r.minimum, min(r.maximum, value)))
    elif attr == "focus":
        c.set_ctrl(V4L2_CID_FOCUS_ABSOLUTE, int(value))
    elif attr == "focus_auto":
        c.set_ctrl(V4L2_CID_FOCUS_AUTO, 1 if value else 0)
    elif attr == "exposure":
        c.set_ctrl(V4L2_CID_EXPOSURE, int(value))
    else:
        raise ValueError("unknown %r" % attr)


def cmd_set(args):
    c = _pick(args)
    _deg_set(c, args.attr, args.value)
    return 0


def cmd_pan(args):
    c = _pick(args)
    _deg_set(c, "pan", args.value)


def cmd_tilt(args):
    c = _pick(args)
    _deg_set(c, "tilt", args.value)


def cmd_zoom(args):
    c = _pick(args)
    _deg_set(c, "zoom", args.value)


def cmd_ai(args):
    c = _pick(args)
    c.set_ai_mode(args.mode)


def cmd_hdr(args):
    c = _pick(args)
    c.set_hdr(args.on in ("1", "on"))


def cmd_fov(args):
    c = _pick(args)
    c.set_fov(args.mode)


def cmd_preset(args):
    c = _pick(args)
    if args.action == "list":
        for p in c.preset_list():
            print("slot %d: pan=%.2f pitch=%.2f zoom=%.2f name=%s" %
                  (p["slot"], p["pan"], p["pitch"], p["zoom"], p["name"]))
        return 0
    slot = int(args.slot) - 1
    if args.action == "save":
        c.preset_save(slot)
    elif args.action == "update":
        c.preset_update(slot)
    elif args.action == "recall":
        c.preset_recall(slot)
    elif args.action == "delete":
        c.preset_delete(slot)
    print("preset %s slot %d ok" % (args.action, slot + 1))
    return 0


def build_argparse():
    p = argparse.ArgumentParser(description="OBSBOT Tiny 2 Lite control")
    p.add_argument("-d", "--device", default=None,
                   help="camera devnode (e.g. /dev/video2) or bus id substring")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="enumerate cameras")
    sub.add_parser("status", help="show camera status")
    sub.add_parser("ctrls", help="list V4L2 control ranges/values")

    s = sub.add_parser("set", help="set pan/tilt/zoom/focus/exposure")
    s.add_argument("attr", choices=["pan", "tilt", "zoom", "focus", "focus_auto", "exposure"])
    s.add_argument("value")
    s.set_defaults(fn=cmd_set)

    sub.add_parser("wake").set_defaults(fn=lambda a: (_pick(a).wake(), 0)[1])
    sub.add_parser("sleep").set_defaults(fn=lambda a: (_pick(a).sleep(), 0)[1])
    sub.add_parser("recenter").set_defaults(fn=lambda a: (_pick(a).recenter(), 0)[1])

    s = sub.add_parser("pan")
    s.add_argument("value", help="pan angle in degrees")
    s.set_defaults(fn=cmd_pan)
    s = sub.add_parser("tilt")
    s.add_argument("value", help="tilt angle in degrees (positive = up)")
    s.set_defaults(fn=cmd_tilt)
    s = sub.add_parser("zoom")
    s.add_argument("value", help="zoom 0-100")
    s.set_defaults(fn=cmd_zoom)

    s = sub.add_parser("ai")
    s.add_argument("mode", choices=list(AI_MODES))
    s.set_defaults(fn=cmd_ai)

    s = sub.add_parser("hdr")
    s.add_argument("on", choices=["0", "1", "on", "off"])
    s.set_defaults(fn=cmd_hdr)
    s = sub.add_parser("fov")
    s.add_argument("mode", choices=list(FOV_MODES))
    s.set_defaults(fn=cmd_fov)

    s = sub.add_parser("preset")
    s.add_argument("action", choices=["save", "update", "recall", "delete", "list"])
    s.add_argument("slot", nargs="?", default=None)
    s.set_defaults(fn=cmd_preset)

    p.set_defaults(fn=cmd_status)
    return p


def main():
    args = build_argparse().parse_args()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "ctrls":
        return cmd_ctrls(args)
    try:
        return args.fn(args)
    except (ObsbotError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
