#!/usr/bin/env python3
"""
Media Control — unified camera hub tray app.

A dark GTK3 control panel (plus a tray icon) that drives OBSBOT Tiny 2 Lite
cameras through obsbot_control.py — gimbal pan/tilt, zoom, AI tracking,
HDR/FOV, presets and image controls — and any other UVC webcam via standard
V4L2 controls, with zero vendor SDK.

Usage:
    python3 media_control.py
"""

import os
import sys
import threading
import time
import base64

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, GLib, Gdk, Gst, GdkPixbuf

import numpy as np

PREVIEW_W, PREVIEW_H = 300, 169
PREVIEW_TICK_MS = 50

import obsbot_control as oc

COLOR_BG = "#101020"
COLOR_PANEL = "#161630"
COLOR_ACCENT = "#00e08a"
COLOR_TEXT = "#ffffff"
COLOR_MUTED = "#8a8a9a"

NUDGE_DEG = 10.0
POLL_MS = 3000

CSS = """
window { background-color: %(bg)s; color: %(text)s; }
toolbar, headerbar { background-color: %(panel)s; border: none; }
grid, box, notebook { background-color: %(bg)s; color: %(text)s; }
label { color: %(text)s; }
label.muted { color: %(muted)s; }
label.title { font-size: 15px; font-weight: bold; }
label.section { color: %(accent)s; font-weight: bold; font-size: 13px; }
notebook tab { background-color: %(panel)s; color: %(muted)s; padding: 6px 14px; }
notebook tab:checked { color: %(accent)s; border-bottom: 2px solid %(accent)s; }
button { background-color: %(panel)s; color: %(text)s; border: 1px solid #2a2a4a;
         border-radius: 6px; padding: 6px 10px; }
button:hover { border-color: %(accent)s; }
button:disabled { color: %(muted)s; }
button.primary { background-color: %(accent)s; color: #101020; font-weight: bold; }
button.primary:hover { background-color: #2ef0a3; }
button.power_on { background-color: %(accent)s; color: #101020; font-weight: bold; font-size: 15px; }
button.power_on:hover { background-color: #2ef0a3; }
button.power_off { background-color: #d43d3d; color: #ffffff; font-weight: bold; font-size: 15px; }
button.power_off:hover { background-color: #e55353; }
button.pad { font-size: 20px; padding: 0px; min-width: 56px; min-height: 48px; }
scale trough { background-color: #23233f; min-height: 6px; }
scale highlight { background-color: %(accent)s; min-height: 6px; }
scale slider { background-color: %(accent)s; }
combobox, combobox > button { background-color: %(panel)s; }
entry { background-color: %(panel)s; color: %(text)s; border: 1px solid #2a2a4a; }
statusbar { background-color: %(panel)s; }
""" % {
    "bg": COLOR_BG,
    "panel": COLOR_PANEL,
    "accent": COLOR_ACCENT,
    "text": COLOR_TEXT,
    "muted": COLOR_MUTED,
}

AI_NAMES = ["none", "normal", "upper-body", "close-up", "headless",
            "lower-body", "group", "whiteboard", "desk", "hand"]
TRACK_SPEEDS = ["standard", "sport"]
FOV_NAMES = ["wide", "normal", "narrow"]


class ControlError(Exception):
    pass


class CameraManager:
    """Resolves cameras, keeps selection stable across refreshes."""

    def __init__(self):
        self.by_bus = {}
        self.kinds = {}
    def refresh(self):
        found = []
        seen = set()
        for path, kind, cap in oc.discover():
            bus = cap["bus_info"].decode("utf-8", "replace").strip("\x00")
            seen.add(bus)
            old = self.by_bus.get(bus)
            if old is None:
                old = oc.Obsbot(path) if kind == "obsbot" else oc.Webcam(path)
                self.by_bus[bus] = old
            self.kinds[bus] = kind
            found.append((bus, kind))
        for bus in list(self.by_bus):
            if bus not in seen:
                oc.os_close(self.by_bus[bus].fd)
                del self.by_bus[bus]
                del self.kinds[bus]
        return found

    def get(self, bus):
        return self.by_bus.get(bus)

    def kind(self, bus):
        return self.kinds.get(bus, "generic")

    def name(self, bus):
        cam = self.by_bus.get(bus)
        if cam is None:
            return bus
        return cam.cap["card"].decode("utf-8", "replace").strip("\x00")

    def devnode(self, bus):
        cam = self.by_bus.get(bus)
        return cam.devnode if cam else None


class FaceWatcher:
    """Watches a webcam for a person/face. Reuses the GStreamer capture
    pipeline (same as Preview) and detects faces with OpenCV. Call
    `check()` periodically from a GLib timeout; it returns True when a
    face appears. Keeps the latest frame as a base64 PNG for OCR/vision."""

    def __init__(self, device="/dev/video0"):
        self.device = device
        self.pipe = None
        self.sink = None
        self.last_frame_b64 = None
        self.face_count = 0
        self._faces_seen = False

    def _start(self):
        if self.pipe is not None:
            return
        Gst.init(None)
        desc = ("v4l2src device=%s ! videoscale ! video/x-raw,width=640,height=360 ! "
                "videoconvert ! video/x-raw,format=RGB ! appsink "
                "max-buffers=1 drop=true sync=false" % self.device)
        self.pipe = Gst.parse_launch(desc)
        self.sink = self.pipe.get_by_name("sink")
        self.pipe.set_state(Gst.State.PLAYING)

    def stop(self):
        if self.pipe is not None:
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None
            self.sink = None
        self._faces_seen = False

    def _frame_b64(self):
        self._start()
        time.sleep(0.3)
        sample = self.sink.props.last_sample
        if sample is None:
            return None
        import base64
        buf = sample.get_buffer()
        caps = sample.get_caps()
        s = caps.get_structure(0)
        ok, w = s.get_int("width")
        ok, h = s.get_int("height")
        size = buf.get_size()
        if not (w and h and size >= w * h * 3):
            return None
        pb = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(buf.extract_dup(0, size)),
            GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)
        png = pb.save_to_bufferv("png", [], [])
        self.last_frame_b64 = base64.b64encode(png[0]).decode("ascii")
        return self.last_frame_b64

    def grab_b64(self):
        """Return the latest frame as base64 PNG (or None)."""
        return self._frame_b64()

    def check(self):
        """Scan for a face. Returns True if at least one face is visible."""
        try:
            import cv2
            import numpy as np
            b64 = self._frame_b64()
            if not b64:
                self.face_count = 0
                return False
            raw = np.frombuffer(base64.b64decode(b64), np.uint8)
            img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if img is None:
                self.face_count = 0
                return False
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = cascade.detectMultiScale(gray, 1.1, 5)
            self.face_count = len(faces)
            self._faces_seen = self.face_count > 0
            return self._faces_seen
        except Exception:
            self.face_count = 0
            return False

    def ocr(self):
        """Read text from the latest frame with tesseract (offline)."""
        import base64
        b64 = self._frame_b64()
        if not b64:
            return None
        import subprocess, tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(base64.b64decode(b64))
            tmp = f.name
        try:
            p = subprocess.run(["tesseract", tmp, "-", "--psm", "3"],
                               capture_output=True, text=True, timeout=30)
            txt = p.stdout.strip()
            return txt or None
        except Exception:
            return None
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class Preview:
    """Live camera feed shown in a Gtk.Image via GStreamer appsink -> pixbuf."""

    def __init__(self, device):
        Gst.init(None)
        self.device = device
        self.last_black = None
        self.last_luma = None
        self._last_frame_t = time.monotonic()
        self.image = Gtk.Image()
        self._mjpg = self._can_mjpg()
        self.pipe, self.src, self.sink = self._make_pipe()
        self._want_play = True
        GLib.timeout_add(PREVIEW_TICK_MS, self._tick)

    def _can_mjpg(self):
        try:
            return oc.has_mjpeg(self.device)
        except Exception:
            return False

    def _make_pipe(self):
        if self._mjpg:
            desc = (
                "v4l2src name=src device=%s ! "
                "image/jpeg,width=1920,height=1080,framerate=30/1; "
                "image/jpeg ! jpegdec ! videoscale ! "
                "video/x-raw,width=%d,height=%d ! videoconvert ! "
                "video/x-raw,format=RGB ! appsink name=sink "
                "max-buffers=1 drop=true sync=false"
                % (self.device, PREVIEW_W, PREVIEW_H))
        else:
            desc = (
                "v4l2src name=src device=%s ! videoscale ! "
                "video/x-raw,width=%d,height=%d ! videoconvert ! "
                "video/x-raw,format=RGB ! appsink name=sink "
                "max-buffers=1 drop=true sync=false"
                % (self.device, PREVIEW_W, PREVIEW_H))
        pipe = Gst.parse_launch(desc)
        src = pipe.get_by_name("src")
        sink = pipe.get_by_name("sink")
        pipe.set_state(Gst.State.PLAYING)
        return pipe, src, sink

    def _tick(self):
        if self._want_play:
            try:
                st = self.pipe.get_state(0)[1]
                if st != Gst.State.PLAYING:
                    if st in (Gst.State.ERROR, Gst.State.NULL):
                        self.pipe.set_state(Gst.State.NULL)
                    self.pipe.set_state(Gst.State.PLAYING)
            except Exception:
                pass
        try:
            sample = self.sink.props.last_sample
            if sample is not None:
                buf = sample.get_buffer()
                caps = sample.get_caps()
                s = caps.get_structure(0)
                ok, w = s.get_int("width")
                ok, h = s.get_int("height")
                size = buf.get_size()
                if w and h and size >= w * h * 3:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                        GLib.Bytes.new(buf.extract_dup(0, size)),
                        GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)
                    self.last_black = (max(pixbuf.get_pixels()[::127]) == 0)
                    self.last_luma = self._luma(pixbuf)
                    self._last_frame_t = time.monotonic()
                    self.image.set_from_pixbuf(pixbuf)
        except Exception as e:
            print("preview tick error: %s" % e)
        return True

    def _luma(self, pixbuf):
        """Downsample the RGB pixbuf to a 128x72 float luminance array."""
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        arr = np.frombuffer(pixbuf.get_pixels(), dtype=np.uint8)
        rgb = arr.reshape(h, w, 3)[::5, ::5]
        return (rgb[:, :, 0].astype(np.float32) * 0.299 +
                rgb[:, :, 1].astype(np.float32) * 0.587 +
                rgb[:, :, 2].astype(np.float32) * 0.114)

    def set_device(self, dev):
        if dev is None or dev == self.device:
            return
        self.device = dev
        mjpg = self._can_mjpg()
        if mjpg != self._mjpg:
            self._mjpg = mjpg
            if self.pipe is not None:
                self.pipe.set_state(Gst.State.NULL)
            self.pipe, self.src, self.sink = self._make_pipe()
        else:
            self.pipe.set_state(Gst.State.NULL)
            self.src.set_property("device", dev)
            self.pipe.set_state(Gst.State.PLAYING)
        self._last_frame_t = time.monotonic()
        print("preview: switched to %s" % dev)

    def stop(self):
        self._want_play = False
        self.pipe.set_state(Gst.State.NULL)

    def start(self):
        self._want_play = True
        self.pipe.set_state(Gst.State.PLAYING)


class InstrumentTracker:
    """Tracks a drawn region across frames via zero-mean NCC (FFT-based)."""

    SEARCH_R = 30

    def __init__(self):
        self.template = None
        self.tmpl_mean = 0.0
        self.tmpl_norm = 0.0
        self.pos = None
        self.conf = 0.0

    def clear(self):
        self.template = None
        self.pos = None
        self.conf = 0.0

    def set_target(self, frame, rect):
        h, w = frame.shape
        y0, x0, y1, x1 = rect
        y0 = max(0, min(y0, h - 1))
        y1 = max(y0 + 1, min(y1, h))
        x0 = max(0, min(x0, w - 1))
        x1 = max(x0 + 1, min(x1, w))
        t = frame[y0:y1, x0:x1].astype(np.float64)
        self.template = t
        self.tmpl_mean = t.mean()
        self.tmpl_norm = ((t - self.tmpl_mean) ** 2).sum()
        self.pos = ((y0 + y1) // 2, (x0 + x1) // 2)
        self.conf = 1.0

    def match(self, frame):
        if self.template is None or self.pos is None:
            return self.pos, 0.0
        th, tw = self.template.shape
        cy, cx = self.pos
        r = self.SEARCH_R
        H, W = frame.shape
        y0 = max(0, cy - r)
        x0 = max(0, cx - r)
        y1 = min(H - th, cy + r)
        x1 = min(W - tw, cx + r)
        if y0 >= y1 or x0 >= x1:
            return self.pos, 0.0
        S = frame[y0:y1 + th, x0:x1 + tw].astype(np.float64)
        t = self.template - self.tmpl_mean
        t2 = (t * t).sum()
        if t2 <= 0:
            return self.pos, 0.0
        n, m = S.shape
        corr = np.fft.ifft2(np.fft.fft2(S) * np.conj(np.fft.fft2(t, (n, m)))).real
        cw, ch = m - tw + 1, n - th + 1
        corr = corr[:ch, :cw]
        SI = np.zeros((n + 1, m + 1))
        SI[1:, 1:] = S.cumsum(0).cumsum(1)
        sums = (SI[th:, tw:] - SI[:n - th + 1, tw:] -
                SI[th:, :m - tw + 1] + SI[:n - th + 1, :m - tw + 1])
        S2 = S * S
        SI2 = np.zeros((n + 1, m + 1))
        SI2[1:, 1:] = S2.cumsum(0).cumsum(1)
        sums2 = (SI2[th:, tw:] - SI2[:n - th + 1, tw:] -
                 SI2[th:, :m - tw + 1] + SI2[:n - th + 1, :m - tw + 1])
        var = sums2 - sums * sums / (th * tw)
        denom = np.sqrt(var * t2)
        z = np.where(denom > 1e-6, corr / denom, 0.0)
        best = np.unravel_index(np.argmax(z), z.shape)
        self.conf = float(z[best])
        self.pos = (y0 + best[0] + th // 2, x0 + best[1] + tw // 2)
        return self.pos, self.conf


class MediaApp:
    def __init__(self):
        self.mgr = CameraManager()
        self.bus = None
        self.presets_page = None
        self._buses = []
        self._tried_black = set()
        self._tracker = InstrumentTracker()
        self._tracking_on = False
        self._power_on = True
        self._drag_rect = None
        self._track_pos = None
        self._lock = threading.Lock()
        self.kind = "generic"
        self._kinds = {}
        self._last_buses = None
        self._build_ui()
        self._apply_css()
        self.refresh_cameras()
        GLib.timeout_add(POLL_MS, self._poll)
        GLib.timeout_add(4000, self._auto_refresh)
        GLib.timeout_add(1500, self._preview_guard)
        GLib.timeout_add(120, self._track_tick)

    # ---------- UI ----------
    def _build_ui(self):
        self.window = Gtk.Window(title="Media Control — Camera Hub")
        self.window.set_default_size(600, 400)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("delete-event", self._on_window_delete)
        self.window.connect("destroy", self._on_destroy)
        self.window.connect("map-event", self._compact_once)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(vbox)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_margin_top(10)
        top.set_margin_bottom(6)
        top.set_margin_start(12)
        top.set_margin_end(12)
        title = Gtk.Label(label="Camera Hub")
        title.get_style_context().add_class("title")
        top.pack_start(title, False, False, 0)

        self.cam_combo = Gtk.ComboBoxText()
        self.cam_combo.connect("changed", self._on_camera_changed)
        top.pack_start(self.cam_combo, True, True, 0)

        self.btn_refresh = Gtk.Button(label="Rescan")
        self.btn_refresh.connect("clicked", lambda *a: self.refresh_cameras())
        top.pack_end(self.btn_refresh, False, False, 0)

        self._btn_power = Gtk.ToggleButton(label="GO")
        self._btn_power.set_size_request(84, -1)
        self._btn_power.connect("toggled", self._on_power)
        self._btn_power.set_tooltip_text("GO — controls active / OFF — stop "
                                        "everything and release the camera")
        top.pack_end(self._btn_power, False, False, 0)
        vbox.pack_start(top, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mid.set_margin_start(10)
        mid.set_margin_end(10)
        mid.set_margin_bottom(4)
        vbox.pack_start(mid, True, True, 0)

        self._tab_live()
        mid.pack_start(self._preview_box, False, False, 0)

        nb = Gtk.Notebook()
        nb.connect("switch-page", self._on_tab_switched)
        nb.set_size_request(250, 300)
        self._nb = nb
        mid.pack_start(nb, True, True, 0)

        def page(widget):
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_shadow_type(Gtk.ShadowType.NONE)
            sw.add(widget)
            return sw

        nb.append_page(page(self._tab_gimbal()), Gtk.Label(label="Gimbal & AI"))
        nb.append_page(page(self._tab_image()), Gtk.Label(label="Image"))
        self.presets_page, self.presets_box = self._tab_presets()
        self._presets_sw = page(self.presets_page)
        nb.append_page(self._presets_sw, Gtk.Label(label="Presets"))

        self.status = Gtk.Statusbar()
        self.status_ctx = self.status.get_context_id("mc")
        vbox.pack_start(self.status, False, False, 0)

        self._btn_power.set_active(True)

        self._tray()
        self.window.show_all()
        self._place_on_pointer_monitor()
        if os.environ.get("MC_NO_UI") != "1":
            self.window.present()

    def _place_on_pointer_monitor(self):
        """Open the window on the monitor the cursor is on, centered."""
        try:
            screen = Gdk.Screen.get_default()
            mono = screen.get_monitor_at_pointer()
            if mono < 0:
                return
            g = screen.get_monitor_geometry(mono)
            w, h = self.window.get_size()
            self.window.move(g.x + (g.width - w) // 2,
                             g.y + (g.height - h) // 2)
        except Exception:
            pass

    def _compact_once(self, *a):
        m = self.window.get_preferred_size()[0]
        self.window.resize(m.width, m.height)
        return False

    def _tab_live(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(0)
        box.set_margin_end(0)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_halign(Gtk.Align.START)
        box.set_valign(Gtk.Align.START)
        self._preview_box = box

        self.preview = Preview("/dev/video0")
        self.preview.image.set_size_request(PREVIEW_W, PREVIEW_H)

        ov = Gtk.Overlay()
        ov.add(self.preview.image)
        self.track_da = Gtk.DrawingArea()
        self.track_da.set_hexpand(True)
        self.track_da.set_vexpand(True)
        self.track_da.set_halign(Gtk.Align.FILL)
        self.track_da.set_valign(Gtk.Align.FILL)
        self.track_da.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                Gdk.EventMask.BUTTON_RELEASE_MASK |
                                Gdk.EventMask.POINTER_MOTION_MASK)
        self.track_da.connect("draw", self._on_overlay_draw)
        self.track_da.connect("button-press-event", self._on_drag_press)
        self.track_da.connect("motion-notify-event", self._on_drag_motion)
        self.track_da.connect("button-release-event", self._on_drag_release)
        ov.add_overlay(self.track_da)
        box.pack_start(ov, False, True, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._btn_track = Gtk.ToggleButton(label="Track instrument")
        self._btn_track.connect("toggled", self._on_track_toggle)
        self._btn_clear = Gtk.Button(label="Clear target")
        self._btn_clear.connect("clicked", lambda *a: self._on_clear_target())
        self._lbl_conf = Gtk.Label(label="")
        self._lbl_conf.get_style_context().add_class("muted")
        row.pack_start(self._btn_track, False, False, 0)
        row.pack_start(self._btn_clear, False, False, 0)
        row.pack_end(self._lbl_conf, False, False, 0)
        box.pack_start(row, False, False, 0)

        hint = Gtk.Label(label="Drag a box around the instrument, then toggle "
                               "Track.")
        hint.get_style_context().add_class("muted")
        hint.set_line_wrap(True)
        hint.set_max_width_chars(36)
        box.pack_start(hint, False, False, 0)
        return box

    # ---------- instrument tracking ----------
    def _on_overlay_draw(self, da, cr):
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        if self._drag_rect:
            x0, y0, x1, y1 = self._drag_rect
            cr.set_line_width(2.0)
            cr.set_source_rgb(0.0, 0.88, 0.54)
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.stroke()
        if self._track_pos and self._tracking_on:
            lw = lh = None
            if self.preview.last_luma is not None:
                lh, lw = self.preview.last_luma.shape
            tx = self._track_pos[1] * (w / lw if lw else 1.0)
            ty = self._track_pos[0] * (h / lh if lh else 1.0)
            cr.set_line_width(2.0)
            cr.set_source_rgb(0.0, 0.88, 0.54)
            cr.arc(tx, ty, 8, 0, 6.28318)
            cr.stroke()
            cr.move_to(tx - 14, ty)
            cr.line_to(tx + 14, ty)
            cr.move_to(tx, ty - 14)
            cr.line_to(tx, ty + 14)
            cr.stroke()
        return False

    def _on_drag_press(self, da, ev):
        self._drag_rect = (ev.x, ev.y, ev.x, ev.y)
        return True

    def _on_drag_motion(self, da, ev):
        if self._drag_rect:
            x0, y0, _, _ = self._drag_rect
            self._drag_rect = (x0, y0, ev.x, ev.y)
            da.queue_draw()
        return True

    def _on_drag_release(self, da, ev):
        if self._drag_rect:
            x0, y0, x1, y1 = self._drag_rect
            self._drag_rect = None
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if (x1 - x0) >= 30 and (y1 - y0) >= 20 and self.preview.last_luma is not None:
                w = da.get_allocated_width()
                h = da.get_allocated_height()
                luma = self.preview.last_luma
                lh, lw = luma.shape
                sx, sy = lw / w, lh / h
                rect = (int(y0 * sy), int(x0 * sx),
                        max(int(y1 * sy), int(y0 * sy) + 4),
                        max(int(x1 * sx), int(x0 * sx) + 4))
                self._tracker.set_target(luma, rect)
                self._track_pos = self._tracker.pos
                if self.kind == "obsbot":
                    self._tracking_on = True
                    self._btn_track.set_active(True)
                    self._status("target locked — tracking")
                else:
                    self._tracking_on = False
                    self._status("target locked — enable Track (OBSBOT only)")
                da.queue_draw()
        return True

    def _on_track_toggle(self, btn):
        if btn.get_active() and self.kind != "obsbot":
            btn.set_active(False)
            self._status("instrument tracking needs an OBSBOT camera")
            return
        self._tracking_on = btn.get_active()
        if self._tracking_on and self._tracker.template is None:
            self._status("draw a box around the instrument first")
            self._tracking_on = False
            btn.set_active(False)
            return
        self._status("tracking %s" % ("on" if self._tracking_on else "off"))

    def _on_clear_target(self):
        self._tracker.clear()
        self._track_pos = None
        self._tracking_on = False
        self._btn_track.set_active(False)
        self._lbl_conf.set_text("")
        self.track_da.queue_draw()
        self._status("target cleared")

    def _track_tick(self):
        if not self._power_on:
            return True
        if self._tracking_on and self._tracker.template is not None:
            luma = self.preview.last_luma
            if luma is not None:
                try:
                    pos, conf = self._tracker.match(luma)
                except Exception:
                    return True
                self._track_pos = pos
                self._lbl_conf.set_text("match %.0f%%" % (conf * 100))
                self.track_da.queue_draw()
                if conf < 0.35:
                    self._status("target lost — holding position")
                else:
                    cy = luma.shape[0] // 2
                    cx = luma.shape[1] // 2
                    ex = pos[1] - cx
                    ey = pos[0] - cy
                    if abs(ex) > 3 or abs(ey) > 3:
                        self._run(self._guard(lambda: self._track_nudge(ex, ey)))
        return True

    def _track_nudge(self, ex, ey):
        c = self.cam()
        arcsec_per_px = 90.0 / 640.0 * 5.0 * 3600.0
        dpan = int(-ex * arcsec_per_px * 0.35)
        dtilt = int(-ey * arcsec_per_px * 0.35)
        pan = c.get_ctrl(oc.V4L2_CID_PAN_ABSOLUTE) + dpan
        tilt = c.get_ctrl(oc.V4L2_CID_TILT_ABSOLUTE) + dtilt
        for cid, val in ((oc.V4L2_CID_PAN_ABSOLUTE, pan),
                         (oc.V4L2_CID_TILT_ABSOLUTE, tilt)):
            r = c.query_ctrl(cid)
            if r:
                val = max(r.minimum, min(r.maximum, val))
            c.set_ctrl(cid, int(val))

    def _tab_gimbal(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(14)
        box.set_margin_end(14)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        sect = Gtk.Label(label="Gimbal")
        sect.get_style_context().add_class("section")
        box.pack_start(sect, False, False, 0)

        self._gimbal_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                       spacing=10)
        self._gimbal_na = Gtk.Label(label="Gimbal, AI tracking and presets "
                                           "are OBSBOT-only controls — not "
                                           "available on this webcam.")
        self._gimbal_na.get_style_context().add_class("muted")
        self._gimbal_na.set_halign(Gtk.Align.CENTER)
        self._gimbal_na.set_visible(False)
        box.pack_start(self._gimbal_content, True, True, 0)
        box.pack_start(self._gimbal_na, False, False, 0)
        content = self._gimbal_content

        pad = Gtk.Grid()
        pad.set_halign(Gtk.Align.CENTER)
        pad.set_hexpand(True)

        def mk_btn(label, fn, tooltip=None):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("pad")
            b.connect("clicked", lambda *a: self._run(self._guard(fn)))
            if tooltip:
                b.set_tooltip_text(tooltip)
            return b

        self._b_up = mk_btn("▲", lambda: self._nudge_tilt(NUDGE_DEG), "Tilt up")
        self._b_dn = mk_btn("▼", lambda: self._nudge_tilt(-NUDGE_DEG), "Tilt down")
        self._b_lf = mk_btn("◀", lambda: self._nudge_pan(NUDGE_DEG), "Pan left")
        self._b_rt = mk_btn("▶", lambda: self._nudge_pan(-NUDGE_DEG), "Pan right")

        pad.attach(self._b_lf, 0, 1, 1, 1)
        pad.attach(self._b_up, 1, 0, 1, 1)
        pad.attach(self._b_dn, 1, 2, 1, 1)
        pad.attach(self._b_rt, 2, 1, 1, 1)

        self._b_home = Gtk.Button(label="◉ Recenter")
        self._b_home.connect("clicked", lambda *a: self._run(self._guard(self._recenter)))
        pad.attach(self._b_home, 1, 1, 1, 1)
        content.pack_start(pad, False, False, 0)

        self._lbl_pose = Gtk.Label(label="pan 0.0°   tilt 0.0°   zoom 0")
        self._lbl_pose.get_style_context().add_class("muted")
        self._lbl_pose.set_halign(Gtk.Align.CENTER)
        content.pack_start(self._lbl_pose, False, False, 0)

        zoom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        zl = Gtk.Label(label="Zoom")
        zl.get_style_context().add_class("muted")
        zoom_row.pack_start(zl, False, False, 0)
        self.zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.zoom.set_hexpand(True)
        self.zoom.connect("value-changed", self._on_zoom_slider)
        self.zoom.connect("button-release-event", self._zoom_commit_now)
        self.zoom.connect("key-release-event", self._zoom_commit_now)
        zoom_row.pack_start(self.zoom, True, True, 0)
        content.pack_start(zoom_row, False, False, 0)

        power = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._b_wake = Gtk.Button(label="Wake")
        self._b_wake.connect("clicked", lambda *a: self._run(self._guard(self._wake)))
        self._b_sleep = Gtk.Button(label="Sleep")
        self._b_sleep.connect("clicked", lambda *a: self._run(self._guard(self._sleep)))
        power.pack_start(self._b_wake, True, True, 0)
        power.pack_start(self._b_sleep, True, True, 0)
        content.pack_start(power, False, False, 0)

        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rl = Gtk.Label(label="AI tracking")
        rl.get_style_context().add_class("muted")
        self.ai_combo = Gtk.ComboBoxText()
        for m in AI_NAMES:
            self.ai_combo.append_text(m)
        self.ai_combo.set_active(0)
        self.ai_combo.connect("changed", self._on_ai_changed)
        row1.pack_start(rl, False, False, 0)
        row1.pack_start(self.ai_combo, True, True, 0)
        content.pack_start(row1, False, False, 0)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rl2 = Gtk.Label(label="Speed")
        rl2.get_style_context().add_class("muted")
        self.speed_combo = Gtk.ComboBoxText()
        for s in TRACK_SPEEDS:
            self.speed_combo.append_text(s)
        self.speed_combo.set_active(0)
        self.speed_combo.connect("changed", self._on_speed_changed)
        row2.pack_start(rl2, False, False, 0)
        row2.pack_end(self.speed_combo, False, False, 0)
        content.pack_start(row2, False, False, 0)

        return box

    def _tab_image(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(14)
        box.set_margin_end(14)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        sect = Gtk.Label(label="Imaging")
        sect.get_style_context().add_class("section")
        box.pack_start(sect, False, False, 0)

        self._image_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                      spacing=8)
        box.pack_start(self._image_content, False, False, 0)

        self._image_na = Gtk.Label(label="No image controls available for this "
                                         "camera.")
        self._image_na.get_style_context().add_class("muted")
        self._image_na.set_halign(Gtk.Align.CENTER)
        self._image_na.set_visible(False)
        box.pack_start(self._image_na, False, False, 0)
        return box

    def _populate_image_tab(self):
        content = getattr(self, "_image_content", None)
        if content is None or self.bus is None:
            return
        for child in list(content.get_children()):
            content.remove(child)
        try:
            c = self.cam()
            ctrls = c.ctrl_list()
        except (oc.ObsbotError, ControlError, AttributeError):
            self._image_na.show()
            content.hide()
            return
        self._image_na.hide()
        content.show()
        self._sliders = {}
        for cid, r, ctype in ctrls:
            if cid == oc.V4L2_CID_FOCUS_AUTO:
                continue
            is_bool = (r.minimum, r.maximum, r.step) == (0, 1, 1)
            if ctype in (oc.V4L2_CTRL_TYPE_MENU,
                         oc.V4L2_CTRL_TYPE_INTEGER_MENU) and not is_bool:
                continue
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=r.name, xalign=0)
            lbl.get_style_context().add_class("muted")
            lbl.set_size_request(120, -1)
            row.pack_start(lbl, False, False, 0)
            try:
                cur = c.get_ctrl(cid)
            except oc.ObsbotError:
                cur = None
            if is_bool:
                sw = Gtk.Switch()
                sw.set_active(bool(cur))
                sw.connect("state-set", lambda w, s, c=cid: self._run(
                    lambda: self.cam().set_ctrl(c, 1 if s else 0)))
                row.pack_end(sw, False, False, 0)
                self._sliders[r.name] = sw
            else:
                sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                              r.minimum, r.maximum,
                                              max(1, r.step))
                sc.set_hexpand(True)
                sc.connect("value-changed", lambda w, c=cid: self._debounced_write(
                    lambda: self.cam().set_ctrl(c, int(w.get_value()))))
                if cur is not None:
                    sc.set_value(cur)
                row.pack_start(sc, True, True, 0)
                self._sliders[r.name] = sc
            content.pack_start(row, False, False, 0)

        if self.kind == "obsbot":
            hdr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hl = Gtk.Label(label="HDR / WDR")
            hl.get_style_context().add_class("muted")
            self.hdr_sw = Gtk.Switch()
            self.hdr_sw.connect("state-set", lambda w, s: self._run(
                self._guard(lambda: self.cam().set_hdr(bool(s)))))
            hdr_row.pack_start(hl, False, False, 0)
            hdr_row.pack_end(self.hdr_sw, False, False, 0)
            content.pack_start(hdr_row, False, False, 0)

            fov_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            fov_lbl = Gtk.Label(label="Field of view")
            fov_lbl.get_style_context().add_class("muted")
            self.fov_combo = Gtk.ComboBoxText()
            for f in FOV_NAMES:
                self.fov_combo.append_text(f)
            self.fov_combo.set_active(0)
            self.fov_combo.connect("changed", self._on_fov_changed)
            fov_row.pack_start(fov_lbl, False, False, 0)
            fov_row.pack_end(self.fov_combo, False, False, 0)
            content.pack_start(fov_row, False, False, 0)

        has_focus_auto = any(cid == oc.V4L2_CID_FOCUS_AUTO for cid, _, _ in ctrls)
        if has_focus_auto:
            fa_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            fal = Gtk.Label(label="Autofocus")
            fal.get_style_context().add_class("muted")
            self.focus_auto = Gtk.Switch()
            self.focus_auto.set_active(True)
            self.focus_auto.connect("state-set", lambda w, s: self._run(
                lambda: self.cam().set_ctrl(oc.V4L2_CID_FOCUS_AUTO,
                                            1 if s else 0)))
            fa_row.pack_start(fal, False, False, 0)
            fa_row.pack_end(self.focus_auto, False, False, 0)
            content.pack_start(fa_row, False, False, 0)

        content.show_all()
        return False

    def _tab_presets(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_start(10)
        page.set_margin_end(10)
        page.set_margin_top(10)
        page.set_margin_bottom(10)

        sect = Gtk.Label(label="On-camera presets")
        sect.get_style_context().add_class("section")
        page.pack_start(sect, False, False, 0)

        self._presets_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                        spacing=10)
        self._presets_na = Gtk.Label(label="On-camera presets are OBSBOT-only "
                                          "— not available on this webcam.")
        self._presets_na.get_style_context().add_class("muted")
        self._presets_na.set_halign(Gtk.Align.CENTER)
        self._presets_na.set_visible(False)
        page.pack_start(self._presets_content, True, True, 0)
        page.pack_start(self._presets_na, False, False, 0)
        content = self._presets_content

        self._preset_ui = {}
        for i in range(1, 4):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label="Slot %d" % i, xalign=0)
            lbl.set_size_request(38, -1)
            row.pack_start(lbl, False, False, 0)
            info = Gtk.Label(label="—", xalign=0)
            info.get_style_context().add_class("muted")
            info.set_hexpand(True)
            row.pack_start(info, True, True, 0)
            b_save = Gtk.Button(label="Save")
            b_save.connect("clicked", lambda *a, s=i: self._run(
                self._guard(lambda s=s: self.cam().preset_save(s - 1))))
            b_rec = Gtk.Button(label="Load")
            b_rec.connect("clicked", lambda *a, s=i: self._run(
                self._guard(lambda s=s: self.cam().preset_recall(s - 1))))
            b_del = Gtk.Button(label="Del")
            b_del.connect("clicked", lambda *a, s=i: self._run(
                self._guard(lambda s=s: self.cam().preset_delete(s - 1))))
            row.pack_start(b_save, False, False, 0)
            row.pack_start(b_rec, False, False, 0)
            row.pack_start(b_del, False, False, 0)
            self._preset_ui[i] = (info, b_save, b_rec, b_del)
            content.pack_start(row, False, False, 0)

        ref = Gtk.Button(label="Refresh preset list")
        ref.connect("clicked", lambda *a: self._load_presets())
        content.pack_start(ref, False, False, 0)
        return page, None

    def _tray(self):
        self.tray = Gtk.StatusIcon()
        self.tray.set_from_icon_name("camera-photo")
        self.tray.set_tooltip_text("Media Control")
        self.tray.connect("activate", lambda i: self._toggle())
        self.tray.connect("popup-menu", self._tray_menu)

    def _tray_menu(self, icon, button, time):
        m = Gtk.Menu()
        show = Gtk.MenuItem(label="Open Media Control")
        show.connect("activate", lambda *a: self._show())
        m.append(show)
        sep = Gtk.SeparatorMenuItem()
        m.append(sep)
        quit_i = Gtk.MenuItem(label="Quit")
        quit_i.connect("activate", lambda *a: Gtk.main_quit())
        m.append(quit_i)
        m.show_all()
        m.popup(None, None, None, None, button, time)

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ---------- camera access ----------
    def cam(self):
        if self.bus is None:
            raise ControlError("no camera selected")
        c = self.mgr.get(self.bus)
        if c is None:
            raise ControlError("camera disconnected — hit Rescan")
        return c

    def _ensure_awake(self):
        """Wake the camera if asleep — sleeping cameras ignore writes."""
        c = self.cam()
        try:
            if c.status()["sleep"]:
                c.wake()
                time.sleep(0.5)
        except oc.ObsbotError:
            pass

    def _guard(self, fn):
        """Wrap a handler: wake first, then run."""
        def guarded():
            self._ensure_awake()
            fn()
        return guarded

    def _run(self, fn):
        if not self._power_on:
            return
        def worker():
            try:
                with self._lock:
                    self._ensure_awake()
                    fn()
            except (oc.ObsbotError, ControlError, ValueError, KeyError) as e:
                GLib.idle_add(self._status, "error: %s" % e)
        threading.Thread(target=worker, daemon=True).start()

    def _status(self, msg):
        self.status.push(self.status_ctx, msg)
        return False

    def _on_power(self, btn):
        on = btn.get_active()
        self._power_on = on
        if on:
            self.preview.start()
            self._status("power on")
        else:
            self._tracking_on = False
            if getattr(self, "_btn_track", None) is not None:
                self._btn_track.set_active(False)
            self.preview.stop()
            self._status("power off — camera released")
        ctx = btn.get_style_context()
        ctx.remove_class("power_on")
        ctx.remove_class("power_off")
        ctx.add_class("power_on" if on else "power_off")
        btn.set_label("GO" if on else "OFF")

    def _debounced_write(self, fn):
        # coalesce rapid slider changes into a single write 250ms later;
        # drop it if the camera changed before it fires.
        bus = self.bus
        if hasattr(self, "_debounce_src") and self._debounce_src is not None:
            GLib.source_remove(self._debounce_src)
        self._debounce_src = GLib.timeout_add(250, self._do_debounced, bus, fn)
        return False

    def _do_debounced(self, bus, fn):
        self._debounce_src = None
        if bus != self.bus:
            return False
        self._run(fn)
        return False

    def _zoom_commit_now(self, *a):
        return self._debounced_write(lambda: self.cam().set_ctrl(
            oc.V4L2_CID_ZOOM_ABSOLUTE, int(self.zoom.get_value())))

    # ---------- actions ----------
    def _nudge_pan(self, delta):
        c = self.cam()
        cur = c.get_ctrl(oc.V4L2_CID_PAN_ABSOLUTE)
        target = cur + int(delta * 3600)
        r = c.query_ctrl(oc.V4L2_CID_PAN_ABSOLUTE)
        if r:
            target = max(r.minimum, min(r.maximum, target))
        c.set_ctrl(oc.V4L2_CID_PAN_ABSOLUTE, target)
        GLib.idle_add(self._status, "pan %.0f°" % (target / 3600.0))

    def _nudge_tilt(self, delta):
        c = self.cam()
        cur = c.get_ctrl(oc.V4L2_CID_TILT_ABSOLUTE)
        target = cur + int(delta * 3600)
        r = c.query_ctrl(oc.V4L2_CID_TILT_ABSOLUTE)
        if r:
            target = max(r.minimum, min(r.maximum, target))
        c.set_ctrl(oc.V4L2_CID_TILT_ABSOLUTE, target)
        GLib.idle_add(self._status, "tilt %.0f°" % (target / 3600.0))

    def _recenter(self):
        c = self.cam()
        c.recenter()
        c.set_ctrl(oc.V4L2_CID_PAN_ABSOLUTE, 0)
        c.set_ctrl(oc.V4L2_CID_TILT_ABSOLUTE, 0)
        GLib.idle_add(self._status, "recentered")

    def _wake(self):
        self.cam().wake()
        GLib.idle_add(self._status, "woke camera")

    def _sleep(self):
        self.cam().sleep()
        GLib.idle_add(self._status, "camera sleeping")

    def _on_ai_changed(self, combo):
        mode = combo.get_active_text()
        if mode:
            self._run(self._guard(lambda m=mode: self.cam().set_ai_mode(m)))

    def _on_speed_changed(self, combo):
        s = combo.get_active_text()
        if s:
            self._run(self._guard(lambda s=s: self.cam().set_tracking_speed(
                {"standard": 0, "sport": 2}[s])))

    def _on_fov_changed(self, combo):
        f = combo.get_active_text()
        if f:
            self._run(self._guard(lambda f=f: self.cam().set_fov(f)))

    def _on_zoom_slider(self, scale):
        self._zoom_commit_now()

    # ---------- discovery / polling ----------
    def _cam_label(self, bus):
        name = self.mgr.name(bus).strip()
        if ":" in name:
            a, b = [p.strip() for p in name.split(":", 1)]
            if a and (a == b or a.lower() in b.lower() or b.lower() in a.lower()):
                name = a
        tag = bus.rsplit("-", 1)[-1]
        return "%s (%s)" % (name, tag)

    def refresh_cameras(self, found=None):
        if found is None:
            try:
                found = self.mgr.refresh()
            except oc.ObsbotError as e:
                self._status("error: %s" % e)
                return
        buses = [b for b, _ in found]
        kinds = dict(found)
        self._buses = buses
        self._kinds = kinds
        self._last_buses = list(buses)
        self.cam_combo.handler_block_by_func(self._on_camera_changed)
        self.cam_combo.remove_all()
        for b, _ in found:
            self.cam_combo.append_text(self._cam_label(b))
        if buses:
            idx = buses.index(self.bus) if self.bus in buses else 0
            self.cam_combo.set_active(idx)
            self.bus = buses[idx]
        self.cam_combo.handler_unblock_by_func(self._on_camera_changed)
        self.kind = kinds.get(self.bus, "generic")
        self._set_cam_kind(self.kind)
        self.preview.set_device(self.mgr.devnode(self.bus))
        self._status("cameras: %d" % len(buses))

    def _auto_refresh(self):
        """Pick up newly plugged-in cameras without a manual rescan."""
        try:
            found = self.mgr.refresh()
        except oc.ObsbotError:
            return True
        buses = [b for b, _ in found]
        if buses != self._last_buses:
            self.refresh_cameras(found)
            self._status("cameras changed: %d" % len(buses))
        return True

    def _set_cam_kind(self, kind):
        self.kind = kind
        obsbot = kind == "obsbot"
        if getattr(self, "_gimbal_content", None) is not None:
            self._gimbal_content.set_visible(obsbot)
            self._gimbal_na.set_visible(not obsbot)
        if getattr(self, "_presets_content", None) is not None:
            self._presets_content.set_visible(obsbot)
            self._presets_na.set_visible(not obsbot)
        self._populate_image_tab()

    def _on_camera_changed(self, combo):
        idx = combo.get_active()
        if idx < 0 or idx >= len(self._buses):
            return
        self.bus = self._buses[idx]
        if getattr(self, "_debounce_src", None) is not None:
            GLib.source_remove(self._debounce_src)
            self._debounce_src = None
        self._tried_black = set()
        self._on_clear_target()
        self._set_cam_kind(self._kinds.get(self.bus, "generic"))
        self.preview.set_device(self.mgr.devnode(self.bus))
        self._poll()

    def _preview_guard(self):
        """If the selected camera stops delivering frames, hop to another."""
        if not self._power_on:
            return True
        pv = self.preview
        if self.bus and time.monotonic() - pv._last_frame_t > 4:
            candidates = [b for b in self._buses
                          if b != self.bus and b not in self._tried_black]
            if candidates:
                self._tried_black.add(self.bus)
                self.bus = candidates[0]
                self.cam_combo.set_active(self._buses.index(self.bus))
                self._tried_black.add(self.bus)
                self.preview.set_device(self.mgr.devnode(self.bus))
                self._status("no picture on current camera — switched to %s"
                             % self.bus)
        return True

    def _on_tab_switched(self, nb, page, idx):
        if getattr(self, "_presets_sw", None) is not None and page is self._presets_sw:
            self._load_presets()

    def _load_presets(self):
        if self.kind != "obsbot":
            return
        def worker():
            try:
                with self._lock:
                    presets = self.cam().preset_list()
            except (oc.ObsbotError, ControlError, ValueError) as e:
                GLib.idle_add(self._status, "error: %s" % e)
                return
            GLib.idle_add(self._apply_presets, presets)
        threading.Thread(target=worker, daemon=True).start()

    def _apply_presets(self, presets):
        occupied = {p["slot"]: p for p in presets}
        for i in range(1, 4):
            info, *_ = self._preset_ui[i]
            if i in occupied:
                p = occupied[i]
                info.set_text("pan %.1f°  pitch %.1f°  zoom %.2fx  %s" %
                              (p["pan"], p["pitch"], p["zoom"], p["name"] or ""))
            else:
                info.set_text("—")
        return False

    def _poll(self):
        def worker():
            try:
                if not self._lock.acquire(blocking=False):
                    return
                try:
                    c = self.cam()
                    st = c.status()
                    if self.kind == "obsbot":
                        pose = (c.get_ctrl(oc.V4L2_CID_PAN_ABSOLUTE),
                                c.get_ctrl(oc.V4L2_CID_TILT_ABSOLUTE),
                                c.get_ctrl(oc.V4L2_CID_ZOOM_ABSOLUTE))
                    else:
                        pose = None
                    try:
                        focus_auto = c.get_ctrl(oc.V4L2_CID_FOCUS_AUTO)
                    except oc.ObsbotError:
                        focus_auto = None
                finally:
                    self._lock.release()
            except (oc.ObsbotError, ControlError, ValueError) as e:
                GLib.idle_add(self._status, "error: %s" % e)
                return
            GLib.idle_add(self._apply_status, st, pose, focus_auto)
        threading.Thread(target=worker, daemon=True).start()
        return True

    def _apply_status(self, st, pose, focus_auto):
        if pose is not None:
            pan, tilt, zoom = pose
            self._lbl_pose.set_text("pan %.1f°   tilt %.1f°   zoom %d" %
                                    (pan / 3600.0, tilt / 3600.0, zoom))
            self.zoom.set_value(zoom)
        if self.kind == "obsbot":
            if st["ai_mode"] in AI_NAMES:
                self.ai_combo.set_active(AI_NAMES.index(st["ai_mode"]))
            self.hdr_sw.set_active(bool(st["hdr"]))
        if focus_auto is not None and hasattr(self, "focus_auto"):
            self.focus_auto.set_active(bool(focus_auto))
        if self.kind == "obsbot":
            msg = ("awake: %s   hdr: %s   tracking: %s" %
                   ("yes" if not st["sleep"] else "no",
                    "on" if st["hdr"] else "off", st["ai_mode"]))
        else:
            msg = "webcam — %s" % self.mgr.name(self.bus)
        self._status(msg)
        return False

    # ---------- window / tray ----------
    def _toggle(self):
        if self.window.get_visible():
            self.window.hide()
        else:
            self._show()

    def _show(self):
        self.window.present()
        self.window.deiconify()

    def _on_window_delete(self, *a):
        self.window.hide()
        return True

    def _on_destroy(self, *a):
        if getattr(self, "preview", None) is not None:
            self.preview.stop()
        Gtk.main_quit()


def main():
    if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
        print("no display", file=sys.stderr)
        return 1
    app = MediaApp()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
