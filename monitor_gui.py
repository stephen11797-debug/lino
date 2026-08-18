#!/usr/bin/env python3
"""
Monitor config GUI — Windows 11-style display settings.
Click a monitor on the map to change its settings inline.
"""
import configparser
import json
import os
import re
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib


HERE = os.path.dirname(os.path.abspath(__file__))
ARRANGE = os.path.join(HERE, "arrange_windows.py")


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def parse_monitors():
    result = {}
    r = _run(["xrandr", "--query"])
    for line in r.stdout.splitlines():
        m = re.match(
            r"(\S+) connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", line)
        if m:
            result[m.group(1)] = (
                int(m.group(4)), int(m.group(5)),
                int(m.group(2)), int(m.group(3)))
    return result


def parse_all_outputs():
    active = parse_monitors()
    off = {}
    r = _run(["xrandr", "--query"])
    for line in r.stdout.splitlines():
        m = re.match(r"(\S+) connected (?:primary )?\(", line)
        if m:
            name = m.group(1)
            if name not in active:
                off[name] = True
    return active, off


def has_nvidia_gpu():
    try:
        out = _run(["nvidia-smi", "--query-gpu=name",
                     "--format=csv,noheader"]).stdout.strip()
        return bool(out)
    except Exception:
        return False


def _apply_xrandr_with_nvidia_fallback(cmd):
    result = _run(cmd)
    if result.returncode == 0:
        return True
    if not has_nvidia_gpu():
        return False
    mode_map = _parse_xrandr_current()
    new_mode, new_name, new_x, new_y = _parse_xrandr_cmd(cmd)
    if new_name and new_mode:
        mode_map[new_name] = (new_mode, new_x, new_y)
    parts = ["%s:%s+%d+%d" % (n, m, x, y)
             for n, (m, x, y) in mode_map.items()]
    if parts:
        _run(["nvidia-settings", "--assign",
              "CurrentMetaMode=%s" % ", ".join(parts)])
        return True
    return False


def _parse_xrandr_current():
    result = {}
    r = _run(["xrandr", "--query"])
    name, mode, x, y = None, None, 0, 0
    for line in r.stdout.splitlines():
        m = re.match(
            r"(\S+) connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", line)
        if m:
            if name and mode:
                result[name] = (mode, x, y)
            name = m.group(1)
            x, y = int(m.group(4)), int(m.group(5))
            mode = None
            continue
        if name and not mode and line.startswith(" ") and "*" in line:
            toks = line.split()
            if toks:
                mode = toks[0]
    if name and mode:
        result[name] = (mode, x, y)
    return result


def _parse_xrandr_cmd(cmd):
    name, mode, x, y = None, "nvidia-auto-select", 0, 0
    i = 0
    while i < len(cmd):
        if cmd[i] == "--output" and i + 1 < len(cmd):
            name = cmd[i + 1]; i += 2
        elif cmd[i] == "--mode" and i + 1 < len(cmd):
            mode = cmd[i + 1]; i += 2
        elif cmd[i] == "--pos" and i + 1 < len(cmd):
            pos = cmd[i + 1]
            sep = "x" if "x" in pos else ","
            xy = pos.split(sep)
            try:
                x, y = int(xy[0]), int(xy[1])
            except (ValueError, IndexError):
                pass
            i += 2
        elif cmd[i] == "--auto":
            mode = "nvidia-auto-select"; i += 1
        else:
            i += 1
    return mode, name, x, y


COMMON_RESOLUTIONS = [
    "640x480", "720x480", "720x576", "800x600",
    "1024x768", "1152x864", "1280x720", "1280x960",
    "1280x1024", "1440x900", "1600x900", "1680x1050",
    "1920x1080", "1920x1200", "2560x1440",
    "3840x2160",
]


def xrandr_modes(output):
    modes = []
    r = _run(["xrandr", "--query"])
    in_out = False
    for line in r.stdout.splitlines():
        if line.startswith(output):
            in_out = True
            continue
        if in_out:
            if not line.startswith(" "):
                break
            toks = line.split()
            if toks:
                modes.append((toks[0], "*" in line))
    known = {m[0] for m in modes}
    for res in COMMON_RESOLUTIONS:
        if res not in known:
            modes.append((res, False))
    return modes


def xrandr_rates_for_mode(output, mode):
    rates = []
    r = _run(["xrandr", "--query"])
    in_out = False
    found_mode = False
    for line in r.stdout.splitlines():
        if line.startswith(output):
            in_out = True
            continue
        if in_out:
            if not line.startswith(" "):
                break
            toks = line.split()
            if toks and toks[0] == mode:
                found_mode = True
                for t in toks[1:]:
                    t = t.rstrip("*+")
                    try:
                        rates.append((float(t), "*" in t or "+" in t))
                    except ValueError:
                        pass
            elif found_mode:
                break
    return rates


def monitor_hz():
    hz = {}
    r = _run(["xrandr", "--current"])
    cur = None
    for line in r.stdout.splitlines():
        m = re.match(r"(\S+) connected", line)
        if m:
            cur = m.group(1)
            continue
        if cur and cur not in hz and "*" in line:
            for tok in line.split():
                if "*" in tok:
                    try:
                        hz[cur] = float(tok.rstrip("*+"))
                    except ValueError:
                        pass
                    break
    return hz


def get_brightness(output):
    try:
        r = _run(["xrandr", "--verbose"])
        cur = None
        for line in r.stdout.splitlines():
            m = re.match(r"(\S+) connected", line)
            if m:
                cur = m.group(1)
                continue
            if cur == output and "Brightness:" in line:
                return float(line.split(":")[-1].strip())
    except Exception:
        pass
    return 1.0


def set_brightness(output, value):
    _apply_xrandr_with_nvidia_fallback(
        ["xrandr", "--output", output, "--brightness", "%.2f" % value])


def get_night_light():
    try:
        r = _run(["gsettings", "get",
                   "org.gnome.settings-daemon.plugins.color",
                   "night-light-enabled"])
        return "true" in r.stdout.strip()
    except Exception:
        return False


def set_night_light(enabled):
    val = "true" if enabled else "false"
    _run(["gsettings", "set",
          "org.gnome.settings-daemon.plugins.color",
          "night-light-enabled", val])


def get_night_light_temp():
    try:
        r = _run(["gsettings", "get",
                   "org.gnome.settings-daemon.plugins.color",
                   "night-light-temperature"])
        return int(r.stdout.strip())
    except Exception:
        return 2700


def set_night_light_temp(temp):
    _run(["gsettings", "set",
          "org.gnome.settings-daemon.plugins.color",
          "night-light-temperature", str(temp)])


def get_night_light_schedule():
    try:
        r = _run(["gsettings", "get",
                   "org.gnome.settings-daemon.plugins.color",
                   "night-light-schedule-automatic"])
        auto = "true" in r.stdout.strip()
        if auto:
            return "auto", 0, 0
        r2 = _run(["gsettings", "get",
                    "org.gnome.settings-daemon.plugins.color",
                    "night-light-schedule-from"])
        r3 = _run(["gsettings", "get",
                    "org.gnome.settings-daemon.plugins.color",
                    "night-light-schedule-to"])
        fr = float(r2.stdout.strip())
        to = float(r3.stdout.strip())
        return "manual", fr, to
    except Exception:
        return "auto", 0, 0


def set_night_light_schedule(mode, fr=0, to=0):
    auto = "true" if mode == "auto" else "false"
    _run(["gsettings", "set",
          "org.gnome.settings-daemon.plugins.color",
          "night-light-schedule-automatic", auto])
    if mode == "manual":
        _run(["gsettings", "set",
              "org.gnome.settings-daemon.plugins.color",
              "night-light-schedule-from", str(fr)])
        _run(["gsettings", "set",
              "org.gnome.settings-daemon.plugins.color",
              "night-light-schedule-to", str(to)])


def primary_monitor():
    r = _run(["xrandr", "--query"])
    for line in r.stdout.splitlines():
        if " connected primary " in line:
            m = re.match(r"(\S+) connected", line)
            if m:
                return m.group(1)
    return None


def monitor_hardware():
    hw = {}
    try:
        display = Gdk.Display.get_default()
        for mon in display.get_monitors():
            model = mon.get_model()
            g = mon.get_geometry()
            for name, (x, y, w, h) in parse_monitors().items():
                if model and (g.x, g.y, g.width, g.height) == (x, y, w, h):
                    hw[name] = model
    except Exception:
        pass
    try:
        r = _run(["xrandr", "--verbose"])
    except Exception:
        return hw
    cur, blob, in_edid = None, [], False
    for line in r.stdout.splitlines():
        m = re.match(r"(\S+) connected", line)
        if m:
            if cur and cur not in hw and blob:
                name = _edid_name("".join(blob))
                if name:
                    hw[cur] = name
            cur, blob, in_edid = m.group(1), [], False
            continue
        if cur and "EDID:" in line:
            in_edid = True
            continue
        if in_edid and line.strip() and re.match(r"\s+[0-9a-f]{32}\s*$", line):
            blob.append(line.strip())
    if cur and cur not in hw and blob:
        name = _edid_name("".join(blob))
        if name:
            hw[cur] = name
    return hw


def _edid_name(blob):
    try:
        b = bytes.fromhex("".join(blob.split()))
        for off in (0x36, 0x48, 0x5A, 0x6C):
            if b[off + 3] == 0xFC:
                name = b[off + 5:off + 18].decode("ascii", "replace")
                return name.rstrip("\n\x00 ").strip()
    except Exception:
        pass
    return None


def monitor_scales():
    scales = {}
    try:
        display = Gdk.Display.get_default()
        for mon in display.get_monitors():
            g = mon.get_geometry()
            for name, (x, y, w, h) in parse_monitors().items():
                if (g.x, g.y, g.width, g.height) == (x, y, w, h):
                    scales[name] = mon.get_scale_factor()
    except Exception:
        pass
    return scales


def gpu_info():
    try:
        out = _run(["nvidia-smi", "--query-gpu=name,driver_version",
                     "--format=csv,noheader"]).stdout.strip()
        if "," in out:
            gpu, drv = out.split(",", 1)
            return gpu.strip(), drv.strip()
    except Exception:
        pass
    return None, None


def get_physical_size(output):
    r = _run(["xrandr", "--query"])
    for line in r.stdout.splitlines():
        m = re.match(r"(\S+) connected.*?(\d+)mm x (\d+)mm", line)
        if m and m.group(1) == output:
            return int(m.group(2)), int(m.group(3))
    return 0, 0


class MonitorMap(Gtk.DrawingArea):

    def __init__(self, monitors, off_monitors=None,
                 on_clicked=None, on_dragged=None):
        super().__init__()
        self.monitors = monitors
        self.off_monitors = off_monitors or {}
        self.on_clicked = on_clicked
        self.on_dragged = on_dragged
        self.selected = None
        self._last_req = -1
        self._drag = None
        self._hz = monitor_hz()
        self._scales = monitor_scales()
        self._hw = monitor_hardware()
        self.set_hexpand(True)
        self.set_size_request(-1, 180)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("size-allocate", self._on_resize)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)

    def _all_rects(self):
        rects = dict(self.monitors)
        for name in self.off_monitors:
            if name not in rects:
                rects[name] = (0, 0, 0, 0)
        return rects

    def _aspect(self):
        ar = self._all_rects()
        if not ar:
            return 16 / 9
        xs = [g[0] for g in ar.values() if g[2] > 0]
        ys = [g[1] for g in ar.values() if g[3] > 0]
        xw = [g[0] + g[2] for g in ar.values() if g[2] > 0]
        yh = [g[1] + g[3] for g in ar.values() if g[3] > 0]
        if not xs:
            return 16 / 9
        w = max(xw) - min(xs)
        h = max(yh) - min(ys)
        return (w / h) if h else 16 / 9

    def refresh_info(self):
        self._hz = monitor_hz()
        self._scales = monitor_scales()
        self._hw = monitor_hardware()
        self.queue_draw()

    def _on_resize(self, widget, alloc):
        h = int(alloc.width / self._aspect()) + 14
        h = max(110, min(280, h))
        if h != self._last_req:
            self._last_req = h
            self.set_size_request(-1, h)

    def _scale(self):
        ar = self._all_rects()
        ao = {k: v for k, v in ar.items() if v[2] > 0}
        if not ao:
            return 1.0
        xs = [g[0] for g in ao.values()]
        ys = [g[1] for g in ao.values()]
        xw = [g[0] + g[2] for g in ao.values()]
        yh = [g[1] + g[3] for g in ao.values()]
        return min(self.get_allocated_width() / (max(xw) - min(xs) or 1),
                   self.get_allocated_height() / (max(yh) - min(ys) or 1))

    def _layout(self):
        ar = self._all_rects()
        ao = {k: v for k, v in ar.items() if v[2] > 0}
        if not ao:
            return []
        xs = [g[0] for g in ao.values()]
        ys = [g[1] for g in ao.values()]
        xw = [g[0] + g[2] for g in ao.values()]
        yh = [g[1] + g[3] for g in ao.values()]
        min_x, max_x = min(xs), max(xw)
        min_y, max_y = min(ys), max(yh)
        sc = self._scale()
        rects = []
        for name, (x, y, w, h) in sorted(ao.items(),
                                          key=lambda kv: kv[1][0]):
            is_off = name in self.off_monitors
            rects.append((name,
                          (x - min_x) * sc + 8, (y - min_y) * sc + 8,
                          w * sc - 4, h * sc - 4, is_off))
        return rects

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r):
        r = min(r, w / 2, h / 2)
        pi = 3.14159
        cr.move_to(x + r, y)
        cr.arc(x + w - r, y + r, r, -90 * pi / 180, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 90 * pi / 180)
        cr.arc(x + r, y + h - r, r, 90 * pi / 180, 180 * pi / 180)
        cr.arc(x + r, y + r, r, 180 * pi / 180, 270 * pi / 180)
        cr.close_path()

    def _draw(self, widget, cr):
        cr.set_source_rgb(0.06, 0.06, 0.12)
        cr.paint()
        pi = 3.14159
        for idx, (name, dx, dy, dw, dh,
                  is_off) in enumerate(self._layout(), 1):
            sel = name == self.selected
            if is_off:
                cr.set_dash([4, 3])
                cr.set_source_rgba(0.5, 0.5, 0.6, 0.5)
                cr.set_line_width(1.5)
                self._rounded_rect(cr, dx, dy, dw, dh, 8)
                cr.stroke()
                cr.set_dash([])
                self._rounded_rect(cr, dx, dy, dw, dh, 8)
                cr.set_source_rgba(0.3, 0.3, 0.35, 0.15)
                cr.fill()
                bx, by, br = dx + 7, dy + 7, 9
                cr.arc(bx + br, by + br, br, 0, 2 * pi)
                cr.set_source_rgb(0.5, 0.5, 0.6)
                cr.fill()
                txt = str(idx)
                cr.set_source_rgb(0.0, 0.0, 0.0)
                cr.set_font_size(11)
                tw = cr.text_extents(txt)[4]
                cr.move_to(bx + br - tw / 2, by + br + 4)
                cr.show_text(txt)
                cr.set_source_rgb(0.7, 0.7, 0.8)
                cr.set_font_size(10)
                cr.move_to(bx + 2 * br + 4, by + br + 4)
                cr.show_text(name)
                hw = self._hw.get(name)
                if hw:
                    cr.set_source_rgb(0.5, 0.5, 0.6)
                    cr.set_font_size(8)
                    cr.move_to(bx + 2 * br + 4, by + br + 15)
                    cr.show_text(hw)
                cr.set_source_rgb(0.9, 0.3, 0.3)
                cr.set_font_size(10)
                off_txt = "OFF — click to turn on"
                tw = cr.text_extents(off_txt)[4]
                cr.move_to(dx + (dw - tw) / 2, dy + dh - 8)
                cr.show_text(off_txt)
                cr.set_dash([])
            else:
                self._rounded_rect(cr, dx, dy, dw, dh, 8)
                if sel:
                    cr.set_source_rgb(0.0, 0.9, 0.55)
                    cr.set_line_width(2.5)
                else:
                    cr.set_source_rgb(0.0, 0.55, 0.35)
                    cr.set_line_width(1.5)
                cr.stroke()
                self._rounded_rect(cr, dx, dy, dw, dh, 8)
                if sel:
                    cr.set_source_rgba(0.0, 0.55, 0.35, 0.30)
                else:
                    cr.set_source_rgba(0.0, 0.55, 0.35, 0.20)
                cr.fill()
                bx, by, br = dx + 7, dy + 7, 9
                cr.arc(bx + br, by + br, br, 0, 2 * pi)
                cr.set_source_rgb(0.0, 0.9, 0.55)
                cr.fill()
                txt = str(idx)
                cr.set_source_rgb(0.0, 0.0, 0.0)
                cr.set_font_size(11)
                tw = cr.text_extents(txt)[4]
                cr.move_to(bx + br - tw / 2, by + br + 4)
                cr.show_text(txt)
                cr.set_source_rgb(1, 1, 1)
                cr.set_font_size(10)
                cr.move_to(bx + 2 * br + 4, by + br + 4)
                cr.show_text(name)
                hw = self._hw.get(name)
                if hw:
                    cr.set_source_rgb(0.6, 0.65, 0.75)
                    cr.set_font_size(8)
                    cr.move_to(bx + 2 * br + 4, by + br + 15)
                    cr.show_text(hw)
                w, h = self.monitors[name][2], self.monitors[name][3]
                hz = self._hz.get(name)
                sc = self._scales.get(name)
                res = "%dx%d" % (w, h)
                if hz:
                    res += " @ %gHz" % hz
                if sc:
                    res += "  %d%%" % (sc * 100)
                cr.set_source_rgb(1, 1, 1)
                cr.set_font_size(9)
                tw = cr.text_extents(res)[4]
                cr.move_to(dx + (dw - tw) / 2, dy + dh - 8)
                cr.show_text(res)
        return False

    def _on_press(self, widget, ev):
        if ev.button != 1:
            return False
        for name, dx, dy, dw, dh, is_off in self._layout():
            if dx <= ev.x <= dx + dw and dy <= ev.y <= dy + dh:
                self.selected = name
                self.queue_draw()
                if is_off:
                    if self.on_clicked:
                        self.on_clicked(name)
                    return True
                sx, sy = self.monitors[name][0], self.monitors[name][1]
                self._drag = {"name": name, "px": ev.x, "py": ev.y,
                              "sx": sx, "sy": sy, "moved": False}
                return True
        return False

    def _on_motion(self, widget, ev):
        if not self._drag or not (ev.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        name = self._drag["name"]
        sc = self._scale()
        dx = int(round((ev.x - self._drag["px"]) / sc))
        dy = int(round((ev.y - self._drag["py"]) / sc))
        if abs(ev.x - self._drag["px"]) > 4 or abs(ev.y - self._drag["py"]) > 4:
            self._drag["moved"] = True
        nx = max(-20000, min(20000, self._drag["sx"] + dx))
        ny = max(-20000, min(20000, self._drag["sy"] + dy))
        g = self.monitors[name]
        self.monitors[name] = (nx, ny, g[2], g[3])
        self.queue_draw()
        return True

    def _on_release(self, widget, ev):
        if ev.button != 1 or not self._drag:
            return False
        name = self._drag["name"]
        moved = self._drag["moved"]
        x, y = self.monitors[name][0], self.monitors[name][1]
        self._drag = None
        if moved:
            _apply_xrandr_with_nvidia_fallback(
                ["xrandr", "--output", name, "--pos", "%d,%d" % (x, y)])
            self.queue_draw()
            if self.on_dragged:
                self.on_dragged(name)
        elif self.on_clicked:
            self.on_clicked(name)
        return False


class MonitorConfigWindow(Gtk.Window):

    def __init__(self):
        super().__init__(title="Display Settings")
        self.set_default_size(900, 700)
        self.monitors, self.off_monitors = parse_all_outputs()
        self._identify_windows = []
        self._selected = None
        self._apply_css()

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_border_width(18)
        self.add(vbox)

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title = Gtk.Label(xalign=0)
        title.set_markup(
            "<span size='x-large' weight='bold'>Display</span>")
        title.get_style_context().add_class("title")
        title.set_halign(Gtk.Align.START)
        hdr.pack_start(title, True, True, 0)
        vbox.pack_start(hdr, False, False, 0)

        gpu, drv = gpu_info()
        gpu_label = Gtk.Label(xalign=0)
        gpu_label.set_halign(Gtk.Align.START)
        gpu_label.get_style_context().add_class("subtitle")
        total = len(self.monitors) + len(self.off_monitors)
        gpu_label.set_markup(
            "<span size='small' color='#8a8a9a'>GPU: %s%s  ·  %d display(s)</span>"
            % (gpu or "unknown",
               "  ·  Driver: %s" % drv if drv else "",
               total))
        vbox.pack_start(gpu_label, False, False, 0)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_detect = Gtk.Button(label="Detect")
        btn_detect.connect("clicked", lambda *_: self._refresh())
        toolbar.pack_start(btn_detect, False, False, 0)
        btn_fix = Gtk.Button(label="Fix")
        btn_fix.connect("clicked", lambda *_: self._manual_fix())
        toolbar.pack_start(btn_fix, False, False, 0)
        btn_off = Gtk.Button(label="Single Display")
        btn_off.connect("clicked", lambda *_: self._single_display())
        toolbar.pack_start(btn_off, False, False, 0)
        btn_identify = Gtk.Button(label="Identify")
        btn_identify.connect("clicked", lambda *_: self._toggle_identify())
        toolbar.pack_start(btn_identify, False, False, 0)
        toolbar.pack_start(Gtk.Label(), True, True, 0)

        preset_combo = Gtk.ComboBoxText()
        preset_combo.set_size_request(120, -1)
        self._preset_combo = preset_combo
        self._load_preset_list()
        preset_combo.connect("changed", self._on_preset_selected)
        toolbar.pack_start(preset_combo, False, False, 0)
        btn_preset_save = Gtk.Button(label="Save Preset")
        btn_preset_save.connect("clicked", lambda *_: self._save_preset())
        toolbar.pack_start(btn_preset_save, False, False, 0)
        btn_preset_load = Gtk.Button(label="Load")
        btn_preset_load.connect("clicked", lambda *_: self._load_preset())
        toolbar.pack_start(btn_preset_load, False, False, 0)

        btn_save = Gtk.Button(label="Save")
        btn_save.connect("clicked", lambda *_: self._save())
        toolbar.pack_start(btn_save, False, False, 0)
        btn_apply = Gtk.Button(label="Apply All")
        btn_apply.get_style_context().add_class("suggested-action")
        btn_apply.connect("clicked", lambda *_: self._apply_all())
        toolbar.pack_start(btn_apply, False, False, 0)
        vbox.pack_start(toolbar, False, False, 0)

        map_card = Gtk.Box()
        map_card.get_style_context().add_class("card")
        self.map = MonitorMap(
            self.monitors, off_monitors=self.off_monitors,
            on_clicked=self._select_monitor,
            on_dragged=self._on_map_dragged)
        map_card.pack_start(self.map, True, True, 0)
        vbox.pack_start(map_card, False, False, 0)

        self._controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=0)
        self._controls.get_style_context().add_class("card")
        self._controls.set_border_width(0)
        ph_box = Gtk.Box()
        ph_box.set_border_width(12)
        ph = Gtk.Label(xalign=0,
                       label="Click a monitor above to change its settings")
        ph.get_style_context().add_class("subtitle")
        ph_box.pack_start(ph, False, False, 0)
        self._controls.pack_start(ph_box, False, False, 0)
        vbox.pack_start(self._controls, False, False, 0)

        self._night_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                  spacing=12)
        self._night_box.get_style_context().add_class("card")
        self._night_box.set_border_width(12)
        self._build_night_light_section()
        vbox.pack_start(self._night_box, False, False, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_halign(Gtk.Align.START)
        self.status.set_ellipsize(3)
        self.status.get_style_context().add_class("subtitle")
        vbox.pack_start(self.status, False, False, 0)

        self.show_all()
        oi = " — %d off" % len(self.off_monitors) if self.off_monitors else ""
        self._set_status("Ready — %d display(s)%s" % (len(self.monitors), oi))

    def _apply_css(self):
        css = b"""
            window { background: #131318; }
            .title { color: #f5f5f7; }
            .subtitle { color: #8a8a9a; }
            .card { background: #1b1b23; border: 1px solid #2a2a36;
                    border-radius: 8px; }
            button { background: #252530; color: #e0e0e8;
                     border: 1px solid #3a3a48;
                     border-radius: 6px; padding: 5px 14px; }
            button:hover { background: #30303e; }
            .suggested-action { background: #007a4d; color: white; }
            .suggested-action:hover { background: #009a5d; }
            combobox, spinbutton { background: #252530; color: #e0e0e8;
                                   border: 1px solid #3a3a48;
                                   border-radius: 4px; }
            entry { background: #252530; color: #e0e0e8; }
            label { color: #e0e0e8; }
            scale { color: #00e08a; }
            separator { background: #2a2a36; min-height: 1px; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _set_status(self, text, accent=False):
        markup = "<b>%s</b>" % text if accent else text
        self.status.set_markup(markup)

    def _section_label(self, text):
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>%s</b>" % text)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_margin_top(8)
        lbl.set_margin_bottom(4)
        lbl.set_margin_start(12)
        return lbl

    def _build_night_light_section(self):
        for c in self._night_box.get_children():
            self._night_box.remove(c)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>Night light</b>")
        lbl.set_halign(Gtk.Align.START)
        left.pack_start(lbl, False, False, 0)
        desc = Gtk.Label(xalign=0,
                         label="Reduce blue light to help you sleep")
        desc.get_style_context().add_class("subtitle")
        desc.set_halign(Gtk.Align.START)
        left.pack_start(desc, False, False, 0)
        self._night_box.pack_start(left, False, False, 0)
        self._night_box.pack_start(Gtk.Label(), True, True, 0)
        self._night_switch = Gtk.Switch()
        self._night_switch.set_active(get_night_light())
        self._night_switch.connect("notify::active", self._on_night_toggle)
        self._night_box.pack_end(self._night_switch, False, False, 0)

    def _on_night_toggle(self, switch, pspec):
        set_night_light(switch.get_active())
        state = "enabled" if switch.get_active() else "disabled"
        self._set_status("Night light %s" % state, accent=True)

    def _select_monitor(self, name):
        if name in self.off_monitors:
            self._show_off_controls(name)
            return
        self._selected = name
        self.map.selected = name
        self.map.queue_draw()
        self._show_controls(name)

    def _show_off_controls(self, name):
        self._clear_controls()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_border_width(12)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>%s</b> — connected but not active" % name)
        box.pack_start(lbl, False, False, 0)
        box.pack_start(Gtk.Label(), True, True, 0)
        btn = Gtk.Button(label="Turn On")
        btn.get_style_context().add_class("suggested-action")
        btn.connect("clicked", lambda *_: self._turn_on(name))
        box.pack_start(btn, False, False, 0)
        self._controls.pack_start(box, False, False, 0)
        self._controls.show_all()

    def _turn_on(self, name):
        _apply_xrandr_with_nvidia_fallback(
            ["xrandr", "--output", name, "--auto"])
        self._refresh()
        self._set_status("Turned on %s" % name, accent=True)

    def _show_controls(self, name):
        self._clear_controls()
        geo = self.monitors.get(name)
        if not geo:
            return
        x, y, w, h = geo
        prim = primary_monitor()
        hw = monitor_hardware().get(name, "")
        br = get_brightness(name)
        hz = monitor_hz().get(name)
        sc = monitor_scales().get(name)
        pw, ph_mm = get_physical_size(name)

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hdr.set_border_width(12)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>%s</b>" % name)
        hdr.pack_start(lbl, False, False, 0)
        if hw:
            hl = Gtk.Label(xalign=0, label=hw)
            hl.get_style_context().add_class("subtitle")
            hdr.pack_start(hl, False, False, 0)
        hdr.pack_start(Gtk.Label(), True, True, 0)
        if name == prim:
            pl = Gtk.Label(xalign=0)
            pl.set_markup(
                "<span size='small' color='#00e08a'><b>PRIMARY</b></span>")
            hdr.pack_start(pl, False, False, 0)
        on_label = Gtk.Label(xalign=0, label="On")
        on_label.get_style_context().add_class("subtitle")
        hdr.pack_start(on_label, False, False, 0)
        on_switch = Gtk.Switch()
        on_switch.set_active(True)
        on_switch.connect("notify::active", self._on_monitor_toggle, name)
        hdr.pack_start(on_switch, False, False, 0)
        self._controls.pack_start(hdr, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self._controls.pack_start(sep, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(10)
        grid.set_border_width(12)
        row = 0

        lbl = Gtk.Label(xalign=0, label="Display resolution")
        lbl.set_size_request(140, -1)
        grid.attach(lbl, 0, row, 1, 1)
        modes = xrandr_modes(name)
        mode_combo = Gtk.ComboBoxText()
        active_idx = 0
        for i, (mode, is_cur) in enumerate(modes):
            mode_combo.append_text(mode)
            if is_cur:
                active_idx = i
        mode_combo.set_active(active_idx)
        grid.attach(mode_combo, 1, row, 1, 1)

        btn_custom = Gtk.Button(label="Custom...")
        btn_custom.connect("clicked", self._on_custom_resolution,
                           name, mode_combo)
        grid.attach(btn_custom, 2, row, 1, 1)
        row += 1

        lbl = Gtk.Label(xalign=0, label="Display orientation")
        lbl.set_size_request(140, -1)
        grid.attach(lbl, 0, row, 1, 1)
        rot_combo = Gtk.ComboBoxText()
        for r in ("Landscape", "Portrait", "Landscape (flipped)",
                  "Portrait (flipped)"):
            rot_combo.append_text(r)
        rot_combo.set_active(0)
        grid.attach(rot_combo, 1, row, 2, 1)
        row += 1

        lbl = Gtk.Label(xalign=0, label="Scale")
        lbl.set_size_request(140, -1)
        grid.attach(lbl, 0, row, 1, 1)
        sc_label = Gtk.Label(xalign=0)
        if sc:
            sc_label.set_text("%d%% (system)" % (sc * 100))
        else:
            sc_label.set_text("100% (system)")
        sc_label.get_style_context().add_class("subtitle")
        grid.attach(sc_label, 1, row, 2, 1)
        row += 1

        lbl = Gtk.Label(xalign=0, label="Brightness")
        lbl.set_size_request(140, -1)
        grid.attach(lbl, 0, row, 1, 1)
        br_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        br_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                            0.1, 1.0, 0.05)
        br_scale.set_value(br)
        br_scale.set_size_request(200, -1)
        br_scale.set_show_fill_level(True)
        br_val = Gtk.Label(xalign=0, label="%.0f%%" % (br * 100))
        br_val.set_size_request(40, -1)
        br_scale.connect("value-changed", self._on_brightness,
                         name, br_val)
        br_box.pack_start(br_scale, True, True, 0)
        br_box.pack_start(br_val, False, False, 0)
        grid.attach(br_box, 1, row, 2, 1)
        row += 1

        lbl = Gtk.Label(xalign=0, label="Refresh rate")
        lbl.set_size_request(140, -1)
        grid.attach(lbl, 0, row, 1, 1)
        rate_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        current_mode = mode_combo.get_active_text()
        rates = xrandr_rates_for_mode(name, current_mode) if current_mode else []
        rate_combo = Gtk.ComboBoxText()
        active_rate_idx = 0
        for i, (r, is_cur) in enumerate(rates):
            rate_combo.append_text("%g Hz" % r)
            if is_cur:
                active_rate_idx = i
        if rates:
            rate_combo.set_active(active_rate_idx)
        rate_box.pack_start(rate_combo, True, True, 0)
        grid.attach(rate_box, 1, row, 2, 1)
        row += 1

        lbl = Gtk.Label(xalign=0, label="Position")
        lbl.set_size_request(140, -1)
        grid.attach(lbl, 0, row, 1, 1)
        pos_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        x_spin = Gtk.SpinButton.new_with_range(-20000, 20000, 10)
        x_spin.set_value(x)
        x_spin.set_size_request(80, -1)
        y_spin = Gtk.SpinButton.new_with_range(-20000, 20000, 10)
        y_spin.set_value(y)
        y_spin.set_size_request(80, -1)
        pos_box.pack_start(Gtk.Label(label="X"), False, False, 0)
        pos_box.pack_start(x_spin, True, True, 0)
        pos_box.pack_start(Gtk.Label(label="Y"), False, False, 0)
        pos_box.pack_start(y_spin, True, True, 0)
        grid.attach(pos_box, 1, row, 2, 1)
        row += 1

        self._controls.pack_start(grid, False, False, 0)

        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self._controls.pack_start(sep2, False, False, 0)

        adv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        adv.set_border_width(12)
        adv_lbl = Gtk.Label(xalign=0)
        adv_lbl.set_markup("<b>Advanced display</b>")
        adv.pack_start(adv_lbl, False, False, 0)
        info_lines = ["%dx%d" % (w, h)]
        if hz:
            info_lines[0] += "  @  %g Hz" % hz
        info_lines.append("Position: %d, %d" % (x, y))
        if pw and ph_mm:
            info_lines.append("Physical size: %d x %d mm" % (pw, ph_mm))
        if sc:
            info_lines.append("Scale factor: %d%%" % (sc * 100))
        info_lines.append("Output: %s" % name)
        if hw:
            info_lines.append("Hardware: %s" % hw)
        for line in info_lines:
            il = Gtk.Label(xalign=0, label=line)
            il.get_style_context().add_class("subtitle")
            adv.pack_start(il, False, False, 0)
        self._controls.pack_start(adv, False, False, 0)

        sep3 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self._controls.pack_start(sep3, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_row.set_border_width(12)
        btn_primary = Gtk.Button(label="Make this the main display")
        if name == prim:
            btn_primary.set_sensitive(False)
        btn_primary.connect("clicked", lambda *_: self._set_primary(name))
        btn_row.pack_start(btn_primary, False, False, 0)
        btn_off = Gtk.Button(label="Turn Off")
        btn_off.connect("clicked", lambda *_: self._turn_off_monitor(name))
        btn_row.pack_start(btn_off, False, False, 0)
        btn_row.pack_start(Gtk.Label(), True, True, 0)
        btn_apply = Gtk.Button(label="Apply")
        btn_apply.get_style_context().add_class("suggested-action")
        btn_apply.connect("clicked", lambda *_: self._apply_monitor(
            name, mode_combo, x_spin, y_spin, rot_combo, rate_combo))
        btn_row.pack_start(btn_apply, False, False, 0)
        self._controls.pack_start(btn_row, False, False, 0)

        self._controls.show_all()

    def _clear_controls(self):
        for c in self._controls.get_children():
            self._controls.remove(c)

    def _on_brightness(self, scale, output, val_label):
        val = scale.get_value()
        val_label.set_text("%.0f%%" % (val * 100))
        set_brightness(output, val)

    def _single_display(self):
        prim = primary_monitor()
        cmd = ["xrandr"]
        for name in self.monitors:
            if name == prim:
                cmd += ["--output", name, "--auto"]
            else:
                cmd += ["--output", name, "--off"]
        _apply_xrandr_with_nvidia_fallback(cmd)
        self._refresh()
        self._set_status("Showing only %s" % prim, accent=True)

    def _apply_monitor(self, name, mode_combo, x_spin, y_spin,
                       rot_combo, rate_combo):
        mode = mode_combo.get_active_text() or "nvidia-auto-select"
        px, py = int(x_spin.get_value()), int(y_spin.get_value())
        rot_map = {"Landscape": "normal", "Portrait": "left",
                   "Landscape (flipped)": "inverted",
                   "Portrait (flipped)": "right"}
        rotation = rot_map.get(rot_combo.get_active_text(), "normal")
        rate_text = ""
        rt = rate_combo.get_active_text()
        if rt:
            rate_text = rt.split()[0]
        native_modes = {m[0] for m in xrandr_modes(name)
                        if m[0].startswith(mode.split("_")[0])}
        is_native = mode in {m for m, _ in
                             [(m, c) for m, c in xrandr_modes(name)]}
        if not is_native and "x" in mode:
            w, h = mode.split("x")
            hz = rate_text or "60"
            cvt_out = _run(["cvt", w, h, str(int(float(hz)))])
            cm = re.search(r'Modeline\s+"([^"]+)"\s+(.*)', cvt_out)
            if cm:
                mode_name = cm.group(1)
                mode_params = cm.group(2)
                _run(["xrandr", "--newmode", mode_name] + mode_params.split())
                _run(["xrandr", "--addmode", name, mode_name])
                mode = mode_name
        cmd = ["xrandr", "--output", name, "--mode", mode,
               "--pos", "%d,%d" % (px, py), "--rotate", rotation]
        if rate_text:
            cmd += ["--rate", rate_text]
        ok = _apply_xrandr_with_nvidia_fallback(cmd)
        if not ok and has_nvidia_gpu() and "x" in mode_combo.get_active_text():
            meta = "%s:%s+%d+%d" % (name, mode, px, py)
            _run(["nvidia-settings", "--assign", "CurrentMetaMode=%s" % meta])
        self._refresh()
        if self._selected:
            if self._selected in self.monitors:
                self._show_controls(self._selected)
        self._set_status("Applied %s" % name, accent=True)

    def _on_custom_resolution(self, button, output_name, mode_combo):
        dialog = Gtk.Dialog(title="Custom Resolution", transient_for=self,
                            modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apply", Gtk.ResponseType.OK)
        dialog.set_default_size(350, -1)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)

        COMMON = [
            ("640x480", 60),
            ("720x480", 60),
            ("720x576", 50),
            ("800x600", 60),
            ("1024x768", 60),
            ("1024x768", 75),
            ("1152x864", 75),
            ("1280x720", 60),
            ("1280x960", 60),
            ("1280x1024", 60),
            ("1280x1024", 75),
            ("1440x900", 60),
            ("1600x900", 60),
            ("1680x1050", 60),
            ("1920x1080", 60),
            ("1920x1200", 60),
            ("2560x1440", 60),
            ("3840x2160", 60),
            ("3840x2160", 120),
        ]

        lbl = Gtk.Label(xalign=0, label="Pick a resolution:")
        box.pack_start(lbl, False, False, 0)

        liststore = Gtk.ListStore(str, int)
        for res, hz in COMMON:
            liststore.append(["%s @ %d Hz" % (res, hz), hz])
        treeview = Gtk.TreeView(model=liststore)
        tvcol = Gtk.TreeViewColumn("Resolution", Gtk.CellRendererText(), text=0)
        treeview.append_column(tvcol)
        treeview.set_headers_visible(False)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(250)
        scroll.add(treeview)
        box.pack_start(scroll, True, True, 0)

        custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        custom_box.pack_start(Gtk.Label(label="Custom:"), False, False, 0)
        custom_entry = Gtk.Entry()
        custom_entry.set_placeholder_text("1024x768")
        custom_entry.set_size_request(120, -1)
        custom_box.pack_start(custom_entry, False, False, 0)
        custom_hz = Gtk.Entry()
        custom_hz.set_text("60")
        custom_hz.set_size_request(40, -1)
        custom_box.pack_start(Gtk.Label(label="Hz"), False, False, 0)
        custom_box.pack_start(custom_hz, False, False, 0)
        box.pack_start(custom_box, False, False, 0)

        dialog.show_all()
        resp = dialog.run()

        selected_res = None
        selected_hz = 60
        path_info = treeview.get_selection().get_selected_rows()
        if path_info[1]:
            model, iter = path_info
            selected_res = model[iter][0].split(" @ ")[0]
            selected_hz = model[iter][1]

        custom_text = custom_entry.get_text().strip()
        custom_hz_text = custom_hz.get_text().strip()
        dialog.destroy()

        if resp != Gtk.ResponseType.OK:
            return

        if custom_text and "x" in custom_text:
            selected_res = custom_text
            try:
                selected_hz = int(custom_hz_text)
            except ValueError:
                selected_hz = 60
        elif not selected_res:
            self._set_status("Pick a resolution or type one", accent=False)
            return

        cvt_out = _run(["cvt", selected_res.split("x")[0],
                        selected_res.split("x")[1], str(selected_hz)])
        m = re.search(r'Modeline\s+"([^"]+)"\s+(.*)', cvt_out)
        if not m:
            self._set_status("cvt failed", accent=False)
            return
        mode_name = m.group(1)
        mode_params = m.group(2)
        _run(["xrandr", "--newmode", mode_name] + mode_params.split())
        _run(["xrandr", "--addmode", output_name, mode_name])
        result = _apply_xrandr_with_nvidia_fallback(
            ["xrandr", "--output", output_name, "--mode", mode_name])
        if not result and has_nvidia_gpu():
            px, py = 0, 0
            cur = _parse_xrandr_current()
            if output_name in cur:
                _, px, py = cur[output_name]
            meta = "%s:%s+%d+%d" % (output_name, mode_name, px, py)
            _run(["nvidia-settings", "--assign", "CurrentMetaMode=%s" % meta])
        self._refresh()
        if self._selected:
            self._show_controls(self._selected)
        self._set_status("Added %s to %s" % (mode_name, output_name),
                         accent=True)

    def _on_monitor_toggle(self, switch, pspec, name):
        if switch.get_active():
            _apply_xrandr_with_nvidia_fallback(
                ["xrandr", "--output", name, "--auto"])
            self._set_status("Turned on %s" % name, accent=True)
        else:
            _apply_xrandr_with_nvidia_fallback(
                ["xrandr", "--output", name, "--off"])
            self._set_status("Turned off %s" % name, accent=True)
        self._refresh()

    def _turn_off_monitor(self, name):
        _apply_xrandr_with_nvidia_fallback(
            ["xrandr", "--output", name, "--off"])
        self._refresh()
        self._set_status("Turned off %s" % name, accent=True)

    def _auto_fix(self):
        _, off = parse_all_outputs()
        if off:
            for name in off:
                _apply_xrandr_with_nvidia_fallback(
                    ["xrandr", "--output", name, "--auto"])
            self._refresh()
        return True

    def _manual_fix(self):
        active, off = parse_all_outputs()
        if not off:
            self._set_status("All monitors are on")
            return
        for name in off:
            _apply_xrandr_with_nvidia_fallback(
                ["xrandr", "--output", name, "--auto"])
        import time
        time.sleep(2)
        active2, off2 = parse_all_outputs()
        if off2 and has_nvidia_gpu():
            parts = []
            for n, (x, y, w, h) in active2.items():
                parts.append("%s:nvidia-auto-select+%d+%d" % (n, x, y))
            for n in off2:
                parts.append("%s:nvidia-auto-select+0+0" % n)
            _run(["nvidia-settings", "--assign",
                  "CurrentMetaMode=%s" % ", ".join(parts)])
            time.sleep(2)
            active2, off2 = parse_all_outputs()
        fixed = len(off) - len(off2)
        if off2:
            self._set_status("Fixed %d, still off: %s" % (
                fixed, ", ".join(sorted(off2))), accent=True)
        else:
            self._set_status("Fixed all %d monitor(s)" % len(off),
                             accent=True)
        self._refresh()

    def _set_primary(self, name):
        _apply_xrandr_with_nvidia_fallback(
            ["xrandr", "--output", name, "--primary"])
        self._refresh()
        if self._selected:
            if self._selected in self.monitors:
                self._show_controls(self._selected)
        self._set_status("Set %s as primary" % name, accent=True)

    def _toggle_identify(self):
        if self._identify_windows:
            for win in self._identify_windows:
                win.destroy()
            self._identify_windows = []
            self._set_status("Labels hidden")
            return
        monitors = parse_monitors()
        if not monitors:
            self._set_status("No monitors found")
            return
        for name, (x, y, w, h) in monitors.items():
            win = Gtk.Window(title="Identify")
            win.set_decorated(False)
            win.set_default_size(200, 80)
            win.move(int(x + 10), int(y + 10))
            lbl = Gtk.Label()
            lbl.set_markup(
                "<span size='20000' weight='bold'>%s</span>" % name)
            lbl.set_halign(Gtk.Align.CENTER)
            lbl.set_valign(Gtk.Align.CENTER)
            lbl.modify_bg(Gtk.StateType.NORMAL,
                          Gdk.color_parse("#101020"))
            lbl.modify_fg(Gtk.StateType.NORMAL,
                          Gdk.color_parse("#00e08a"))
            win.add(lbl)
            win.show_all()
            self._identify_windows.append(win)
        GLib.timeout_add(2000, self._hide_identify)

    def _hide_identify(self):
        for win in self._identify_windows:
            win.destroy()
        self._identify_windows = []
        return False

    def _on_map_dragged(self, name):
        x, y = self.monitors[name][0], self.monitors[name][1]
        self._set_status("%s moved to %d,%d" % (name, x, y), accent=True)
        if self._selected == name and name in self.monitors:
            self._show_controls(name)

    def _refresh(self):
        self.monitors, self.off_monitors = parse_all_outputs()
        self.map.monitors = self.monitors
        self.map.off_monitors = self.off_monitors
        self.map.refresh_info()
        self.map._last_req = -1
        self.map.queue_resize()
        self.map.queue_draw()
        if self._selected:
            if self._selected in self.monitors:
                self._show_controls(self._selected)
            elif self._selected in self.off_monitors:
                self._show_off_controls(self._selected)
            else:
                self._selected = None
                self._clear_controls()
                ph_box2 = Gtk.Box()
                ph_box2.set_border_width(12)
                ph2 = Gtk.Label(
                    xalign=0,
                    label="Click a monitor above to change its settings")
                ph2.get_style_context().add_class("subtitle")
                ph_box2.pack_start(ph2, False, False, 0)
                self._controls.pack_start(ph_box2, False, False, 0)
                self._controls.show_all()
        oi = (" — %d off" % len(self.off_monitors)
              if self.off_monitors else "")
        self._set_status("Refreshed — %d display(s)%s"
                         % (len(self.monitors), oi))

    def _persist_layout(self):
        script = os.path.expanduser("~/.config/monitor-layout.sh")
        desktop = os.path.expanduser(
            "~/.config/autostart/monitor-layout.desktop")
        r = _run(["xrandr", "--query"])
        outputs = []
        cur, cur_mode, cur_rate, cur_rot = None, None, None, "normal"
        for line in r.stdout.splitlines():
            m = re.match(
                r"(\S+) connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)",
                line)
            if m:
                if cur and cur_mode:
                    outputs.append((cur, cur_mode, cur_rate, cur_rot))
                cur, cur_mode, cur_rate = m, None, None
                cur_rot = "normal"
                rm = re.search(r"\((normal|left|right|inverted)", line)
                if rm:
                    cur_rot = rm.group(1)
                continue
            if cur and line.startswith(" ") and "*" in line:
                toks = line.split()
                if toks:
                    cur_mode = toks[0]
                for t in toks:
                    if t.endswith("*"):
                        cur_rate = t.rstrip("*")
                        break
        if cur and cur_mode:
            outputs.append((cur, cur_mode, cur_rate, cur_rot))

        body = ["#!/bin/bash",
                "# generated by monitor_gui.py"]
        for m, mode, rate, rot in outputs:
            nm = m.group(1)
            xp, yp = m.group(4), m.group(5)
            cmd = ("xrandr --output %s --mode %s --pos %s,%s "
                   "--rotate %s" % (nm, mode, xp, yp, rot))
            if rate:
                cmd += " --rate %s" % rate
            body.append(cmd)
        os.makedirs(os.path.dirname(script), exist_ok=True)
        with open(script, "w") as f:
            f.write("\n".join(body) + "\n")
        os.chmod(script, 0o755)
        os.makedirs(os.path.dirname(desktop), exist_ok=True)
        with open(desktop, "w") as f:
            f.write("[Desktop Entry]\nType=Application\n"
                    "Name=Monitor layout\nExec=%s\n"
                    "X-GNOME-Autostart-enabled=true\n" % script)

    def _save(self):
        self._persist_layout()
        self._set_status("Saved to ~/.config/monitor-layout.sh",
                         accent=True)

    def _apply_all(self):
        self._set_status("Applying layout...", accent=True)
        t = threading.Thread(target=self._run_arrange, daemon=True)
        t.start()

    def _run_arrange(self):
        _run([sys.executable, ARRANGE, "--retry", "10"])

        def done():
            self._set_status("Layout applied", accent=True)
        GLib.idle_add(done)

    # ── presets ──

    def _preset_dir(self):
        d = os.path.expanduser("~/.config/monitor-presets")
        os.makedirs(d, exist_ok=True)
        return d

    def _load_preset_list(self):
        self._preset_combo.append_text("Select preset…")
        self._preset_combo.set_active(0)
        for fn in sorted(os.listdir(self._preset_dir())):
            if fn.endswith(".json"):
                self._preset_combo.append_text(fn.replace(".json", ""))

    def _on_preset_selected(self, combo):
        pass

    def _load_preset(self):
        name = self._preset_combo.get_active_text()
        if not name or name == "Select preset\u2026":
            self._set_status("Pick a preset first", accent=False)
            return
        path = os.path.join(self._preset_dir(), name + ".json")
        if not os.path.exists(path):
            self._set_status("Preset not found: %s" % name, accent=False)
            return
        with open(path) as f:
            preset = json.load(f)
        nvidia_parts = []
        for m in preset.get("outputs", []):
            if m.get("off"):
                _apply_xrandr_with_nvidia_fallback(
                    ["xrandr", "--output", m["name"], "--off"])
            else:
                mode = m.get("mode", "nvidia-auto-select")
                px, py = m.get("x", 0), m.get("y", 0)
                cmd = ["xrandr", "--output", m["name"],
                       "--mode", mode,
                       "--pos", "%d,%d" % (px, py),
                       "--rotate", m.get("rotate", "normal")]
                if m.get("rate"):
                    cmd += ["--rate", str(m["rate"])]
                _apply_xrandr_with_nvidia_fallback(cmd)
                nvidia_parts.append("%s:%s+%d+%d" % (m["name"], mode, px, py))
        if nvidia_parts and has_nvidia_gpu():
            _run(["nvidia-settings", "--assign",
                  "CurrentMetaMode=%s" % ", ".join(nvidia_parts)])
        self._refresh()
        self._set_status("Loaded preset: %s" % name, accent=True)

    def _save_preset(self):
        dialog = Gtk.Dialog(title="Save Preset", transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(12)
        lbl = Gtk.Label(label="Preset name:")
        box.pack_start(lbl, False, False, 0)
        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. studio, dual, tv")
        box.pack_start(entry, False, False, 0)
        dialog.show_all()
        resp = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        if resp != Gtk.ResponseType.OK or not name:
            return
        hz_map = monitor_hz()
        cur_modes = _parse_xrandr_current()
        outputs = []
        for mname, (x, y, w, h) in self.monitors.items():
            mode = cur_modes.get(mname, ("nvidia-auto-select", x, y))[0]
            rate = hz_map.get(mname)
            outputs.append({
                "name": mname,
                "mode": mode,
                "x": x, "y": y,
                "rotate": "normal",
                "rate": rate,
                "off": False
            })
        for mname in self.off_monitors:
            outputs.append({"name": mname, "off": True})
        path = os.path.join(self._preset_dir(), name + ".json")
        with open(path, "w") as f:
            json.dump({"outputs": outputs}, f, indent=2)
        self._preset_combo.append_text(name)
        self._set_status("Saved preset: %s" % name, accent=True)


if __name__ == "__main__":
    win = MonitorConfigWindow()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()
