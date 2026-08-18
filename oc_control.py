#!/usr/bin/env python3
# Copyright (c) 2026 Stephen's Studio
# Licensed under the MIT License. See LICENSE file for details.
"""
OC Control — overclock + fan control for the whole PC.

GPU  (NVIDIA, via nvidia-settings / nvidia-smi)
  - core & memory clock offsets   (no root needed)
  - fan: manual target speed or automatic   (no root needed)
  - power limit        (uses pkexec -> popup password dialog)

CPU  (Intel intel_pstate, via cpupower / sysfs)
  - governor: performance / powersave
  - min / max frequency clamp
  - note: the i7-9700K multiplier is locked by the BIOS; from userspace
    we can only force it to sit at its max turbo frequency.

RGB  (via the OpenRGB backend — covers motherboard, RAM, GPU and fans)
  - solid colors + presets for every detected device
  - breathing / rainbow animations
  - fan LED counts per port (Corsair Commander Core)
  - uses the openrgb-python SDK over the OpenRGB server; if the server
    is not running it is started from ~/.local/bin/openrgb.

Fan  CPU / motherboard fans are not exposed over sysfs on this machine,
     so they stay BIOS-controlled.  GPU fan is fully controllable.

Safety
  - live temp limits; when exceeded the GPU is reset to stock clocks,
    the fan returns to automatic, the power limit returns to default,
    and the CPU governor drops back to powersave.

Usage:
    python3 oc_control.py
"""
import colorsys
import json
import os
import shutil
import socket
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

RGB_PORT = 6742
RGB_OPENRGB = os.path.expanduser("~/.local/bin/openrgb")
RGB_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "oc_rgb.json")

# openrgb-python lives in the project venv; add it to sys.path when the
# app is launched with plain system python3.
try:
    from openrgb import OpenRGBClient
    from openrgb.utils import RGBColor
except ImportError:
    venv_py = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".venv", "bin", "python")
    try:
        out = subprocess.run(
            [venv_py, "-c", "import site;print(site.getsitepackages()[0])"],
            capture_output=True, text=True, timeout=10)
        sp = out.stdout.strip()
        if sp and os.path.isdir(sp) and sp not in sys.path:
            sys.path.insert(0, sp)
    except Exception:
        pass
    from openrgb import OpenRGBClient
    from openrgb.utils import RGBColor

GPU_ID = 0
DEFAULT_POWER = 270
POWER_MIN = 100
POWER_MAX = 300
CORE_MIN, CORE_MAX = -200, 300
MEM_MIN, MEM_MAX = -1000, 2000
FREQ_MIN, FREQ_MAX = 800, 4900

COLOR_BG = "#101020"
COLOR_TEXT = "#ffffff"
COLOR_ACCENT = "#00e08a"
COLOR_WARN = "#ffb000"
COLOR_BAD = "#ff5555"
COLOR_MUTED = "#8a8a9a"


def run(cmd, timeout=10):
    """Run a command, return (ok, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        return p.returncode == 0, out, err
    except subprocess.TimeoutExpired:
        return False, "", "timed out"
    except FileNotFoundError:
        return False, "", f"not found: {cmd[0]}"
    except Exception as e:  # noqa: BLE001
        return False, "", str(e)


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def read_str(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def gpu_stats():
    """(temp, fan, gclk, mclk, util, power, power_limit, clocks_valid)"""
    ok, out, _ = run(["nvidia-smi", "--query-gpu="
                      "temperature.gpu,fan.speed,clocks.gr,clocks.mem,"
                      "utilization.gpu,power.draw,power.limit",
                      "--format=csv,noheader,nounits"])
    if not ok or not out:
        return (None,) * 8
    v = [x.strip() for x in out.split(",")]
    v += [None] * (8 - len(v))
    pl = v[6]
    try:
        pl = int(float(pl))
    except Exception:
        pl = None
    try:
        temp = int(v[0])
    except Exception:
        temp = None
    try:
        fan = int(v[1])
    except Exception:
        fan = None
    try:
        gclk = int(v[2])
    except Exception:
        gclk = None
    try:
        mclk = int(v[3])
    except Exception:
        mclk = None
    try:
        util = int(v[4])
    except Exception:
        util = None
    try:
        power = float(v[5])
    except Exception:
        power = None
    clocks_valid = gclk is not None and gclk > 0
    return temp, fan, gclk, mclk, util, power, pl, clocks_valid


def gpu_offsets():
    """(core_offset, mem_offset)"""
    _, co, _ = run(["nvidia-settings", "-q",
                    f"[gpu:{GPU_ID}]/GPUGraphicsClockOffsetAllPerformanceLevels",
                    "-t"])
    _, mo, _ = run(["nvidia-settings", "-q",
                    f"[gpu:{GPU_ID}]/GPUMemoryTransferRateOffsetAllPerformanceLevels",
                    "-t"])
    try:
        core = int(co)
    except Exception:
        core = None
    try:
        mem = int(mo)
    except Exception:
        mem = None
    return core, mem


def cpu_stats():
    """(package_temp, max_core_temp, cur_freq_mhz, governor, min_mhz, max_mhz)"""
    package = core_max = None
    ok, out, _ = run(["sensors", "-u", "coretemp-isa-0000"])
    if ok:
        label = ""
        for line in out.splitlines():
            if not line or line == "Adapter: ISA adapter":
                continue
            if not line[0].isspace():
                label = line.lower().rstrip(":")
            if "input:" in line:
                try:
                    val = float(line.split(":")[-1].strip())
                except Exception:
                    continue
                if "package" in label and package is None:
                    package = val
                if "core" in label:
                    if core_max is None or val > core_max:
                        core_max = val
    freqs = []
    for i in range(os.cpu_count() or 0):
        f = read_int(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
        if f is not None:
            freqs.append(f)
    cur = max(freqs) // 1000 if freqs else None
    gov = read_str("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    mn = read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq")
    mx = read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq")
    if mn is not None:
        mn //= 1000
    if mx is not None:
        mx //= 1000
    return package, core_max, cur, gov, mn, mx


def fmt(v, suffix=""):
    return "—" if v is None else f"{v}{suffix}"


def _rgb_port_open():
    try:
        with socket.create_connection(("127.0.0.1", RGB_PORT), timeout=1):
            return True
    except OSError:
        return False


def ensure_rgb_server():
    """Start the OpenRGB server if needed. Returns True when ready."""
    if _rgb_port_open():
        return True
    exe = shutil.which("openrgb") or RGB_OPENRGB
    if not os.path.exists(exe):
        return False
    try:
        subprocess.Popen([exe, "--server", "--noautoconnect"],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for _ in range(120):
        if _rgb_port_open():
            return True
        import time
        time.sleep(0.5)
    return False


def _rgb_mode_name(mode):
    return mode.name.lower().replace(" ", "")


def _pick_direct_mode(device):
    """Activate a per-LED friendly mode (Direct/Static)."""
    if not device.modes:
        return
    for m in device.modes:
        if _rgb_mode_name(m) in ("direct", "static"):
            device.set_mode(m.name)
            return
    device.set_mode(device.modes[0].name)


def _rgb_hex(color):
    return "#{:02x}{:02x}{:02x}".format(*color)


class StatBox(Gtk.Box):
    def __init__(self, label, color=COLOR_ACCENT):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.CENTER)
        self.value = Gtk.Label(label="—", xalign=0.0)
        self.value.override_font(Pango_font("bold 26"))
        self.value.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(color))
        cap = Gtk.Label(label=label.upper(), xalign=0.0)
        cap.override_font(Pango_font("sans 9"))
        cap.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_MUTED))
        self.pack_start(self.value, False, False, 0)
        self.pack_start(cap, False, False, 0)


def Pango_font(desc):
    from gi.repository import Pango
    return Pango.FontDescription.from_string(desc)


def Gdk_Color(hexstr):
    from gi.repository import Gdk
    c = Gdk.RGBA()
    c.parse(hexstr)
    return c


class Occ(Gtk.Window):
    def __init__(self):
        super().__init__(title="OC Control — GPU / CPU / Fans / RGB")
        self.set_default_size(760, 900)
        self.set_border_width(12)
        self.log_lines = []
        self._reset_in_progress = False
        self.rgb_client = None
        self.rgb_devices = []
        self.rgb_connected = False
        self._anim_phase = 0
        self._anim_active = False
        self._fan_sizes = {"pump": 29, "ports": [8] * 6}
        self._load_rgb_cfg()
        self._build_ui()
        self.refresh_stats()
        GLib.timeout_add(2000, self.refresh_stats)
        GLib.timeout_add(1000, self._rgb_watch)
        GLib.timeout_add(60, self._rgb_anim_tick)

    # ---------- helpers ----------

    def _slider(self, lo, hi, step, digits=0):
        adj = Gtk.Adjustment(value=lo, lower=lo, upper=hi, step_increment=step,
                             page_increment=step * 10)
        s = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        s.set_digits(digits)
        s.set_size_request(220, -1)
        s.connect("button-release-event", lambda *_: self.apply_gpu_oc())
        return s

    def _log(self, text):
        self.log_lines.append(text)
        self.log_lines = self.log_lines[-500:]
        buf = self.log_view.get_buffer()
        buf.set_text("\n".join(self.log_lines))
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)

    def _hdr(self, text):
        lab = Gtk.Label(label=text, xalign=0.0)
        lab.override_font(Pango_font("bold 14"))
        lab.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_ACCENT))
        return lab

    def _status(self, msg, good=True):
        self.status.set_text(msg)
        self.status.override_color(
            Gtk.StateFlags.NORMAL,
            Gdk_Color(COLOR_ACCENT if good else COLOR_BAD))

    # ---------- ui ----------

    def _build_ui(self):
        self.override_background_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_BG))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(box)

        # header
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="OC CONTROL", xalign=0.0)
        title.override_font(Pango_font("bold 20"))
        title.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_ACCENT))
        self.status = Gtk.Label(label="ready", xalign=0.0)
        self.status.override_font(Pango_font("bold 11"))
        hb.pack_start(title, False, False, 0)
        hb.pack_end(self.status, False, False, 0)
        box.pack_start(hb, False, False, 0)

        # stats row
        st = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        self.s_gpu_temp = StatBox("GPU temp", COLOR_BAD)
        self.s_gpu_fan = StatBox("GPU fan", COLOR_ACCENT)
        self.s_gpu_util = StatBox("GPU util")
        self.s_gpu_clk = StatBox("GPU clk")
        self.s_cpu_temp = StatBox("CPU temp", COLOR_WARN)
        self.s_cpu_freq = StatBox("CPU freq")
        for s in (self.s_gpu_temp, self.s_gpu_fan, self.s_gpu_util,
                  self.s_gpu_clk, self.s_cpu_temp, self.s_cpu_freq):
            st.pack_start(s, False, False, 0)
        box.pack_start(st, False, False, 0)

        # notebook
        nb = Gtk.Notebook()
        nb.override_font(Pango_font("bold 11"))
        nb.append_page(self._tab_gpu(), Gtk.Label("GPU overclock"))
        nb.append_page(self._tab_fan(), Gtk.Label("GPU fan"))
        nb.append_page(self._tab_cpu(), Gtk.Label("CPU"))
        nb.append_page(self._tab_rgb(), Gtk.Label("RGB"))
        nb.append_page(self._tab_safety(), Gtk.Label("Safety"))
        box.pack_start(nb, True, True, 0)

        # log
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(-1, 130)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.override_font(Pango_font("monospace 9"))
        sw.add(self.log_view)
        box.pack_start(sw, False, False, 0)

    def _tab_gpu(self):
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vb.set_margin_top(10)
        vb.pack_start(self._hdr("Core clock offset"), False, False, 0)
        core_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.s_core = self._slider(CORE_MIN, CORE_MAX, 5)
        self.l_core = Gtk.Label(label="+0 MHz")
        self.s_core.connect("value-changed",
                            lambda w: self.l_core.set_text(f"{int(w.get_value()):+d} MHz"))
        core_row.pack_start(self.s_core, True, True, 0)
        core_row.pack_start(self.l_core, False, False, 0)
        vb.pack_start(core_row, False, False, 0)

        vb.pack_start(self._hdr("Memory clock offset"), False, False, 0)
        mem_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.s_mem = self._slider(MEM_MIN, MEM_MAX, 25)
        self.l_mem = Gtk.Label(label="+0 MHz")
        self.s_mem.connect("value-changed",
                           lambda w: self.l_mem.set_text(f"{int(w.get_value()):+d} MHz"))
        mem_row.pack_start(self.s_mem, True, True, 0)
        mem_row.pack_start(self.l_mem, False, False, 0)
        vb.pack_start(mem_row, False, False, 0)

        vb.pack_start(self._hdr("Power limit"), False, False, 0)
        pw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        adj = Gtk.Adjustment(value=DEFAULT_POWER, lower=POWER_MIN,
                             upper=POWER_MAX, step_increment=5)
        self.s_power = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                 adjustment=adj)
        self.s_power.set_size_request(220, -1)
        self.s_power.connect("button-release-event", lambda *_: self.apply_gpu_oc())
        self.l_power = Gtk.Label(label=f"{DEFAULT_POWER} W (default)")
        self.s_power.connect("value-changed",
                             lambda w: self.l_power.set_text(f"{int(w.get_value())} W"))
        pw_row.pack_start(self.s_power, True, True, 0)
        pw_row.pack_start(self.l_power, False, False, 0)
        vb.pack_start(pw_row, False, False, 0)

        note = Gtk.Label(
            label="Apply on release.  Memory value is the transfer-rate "
                  "offset (DDR = x2 the effective offset).  Power limit "
                  "uses a password dialog.",
            xalign=0.0, wrap=True)
        note.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_MUTED))
        vb.pack_start(note, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b_apply = Gtk.Button(label="Apply overclock")
        b_apply.connect("clicked", lambda *_: self.apply_gpu_oc())
        b_reset = Gtk.Button(label="Reset to stock")
        b_reset.connect("clicked", lambda *_: self.reset_gpu())
        btns.pack_start(b_apply, False, False, 0)
        btns.pack_start(b_reset, False, False, 0)
        vb.pack_start(btns, False, False, 0)
        return vb

    def _tab_fan(self):
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vb.set_margin_top(10)
        vb.pack_start(self._hdr("GPU fan mode"), False, False, 0)
        self.fan_mode = Gtk.ComboBoxText()
        self.fan_mode.append("auto", "Automatic (driver curve)")
        self.fan_mode.append("manual", "Manual")
        self.fan_mode.set_active(0)
        self.fan_mode.connect("changed", lambda *_: self._fan_mode_changed())
        vb.pack_start(self.fan_mode, False, False, 0)

        vb.pack_start(self._hdr("Manual speed"), False, False, 0)
        fan_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        adj = Gtk.Adjustment(value=50, lower=0, upper=100, step_increment=5)
        self.s_fan = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                               adjustment=adj)
        self.s_fan.set_size_request(220, -1)
        self.s_fan.connect("button-release-event", lambda *_: self.apply_fan())
        self.l_fan = Gtk.Label(label="50 %")
        self.s_fan.connect("value-changed",
                           lambda w: self.l_fan.set_text(f"{int(w.get_value())} %"))
        fan_row.pack_start(self.s_fan, True, True, 0)
        fan_row.pack_start(self.l_fan, False, False, 0)
        vb.pack_start(fan_row, False, False, 0)

        self.fan_note = Gtk.Label(
            label="Manual fans are loud — keep an eye on temps.",
            xalign=0.0, wrap=True)
        self.fan_note.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_WARN))
        vb.pack_start(self.fan_note, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b = Gtk.Button(label="Apply fan setting")
        b.connect("clicked", lambda *_: self.apply_fan())
        btns.pack_start(b, False, False, 0)
        vb.pack_start(btns, False, False, 0)

        hint = Gtk.Label(
            label="CPU / motherboard fans are BIOS-controlled on this "
                  "machine (no pwm nodes exposed), so they can't be set "
                  "from software.",
            xalign=0.0, wrap=True)
        hint.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_MUTED))
        vb.pack_start(hint, False, False, 0)
        return vb

    def _tab_cpu(self):
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vb.set_margin_top(10)
        vb.pack_start(self._hdr("Governor"), False, False, 0)
        self.cpu_gov = Gtk.ComboBoxText()
        self.cpu_gov.append("performance", "performance (max turbo)")
        self.cpu_gov.append("powersave", "powersave (scales down)")
        self.cpu_gov.set_active(1)
        vb.pack_start(self.cpu_gov, False, False, 0)

        vb.pack_start(self._hdr("Max frequency"), False, False, 0)
        mx_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        adj = Gtk.Adjustment(value=FREQ_MAX, lower=FREQ_MIN, upper=FREQ_MAX,
                             step_increment=100)
        self.s_cpu_max = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                   adjustment=adj)
        self.s_cpu_max.set_size_request(220, -1)
        self.s_cpu_max.connect("button-release-event", lambda *_: self.apply_cpu())
        self.l_cpu_max = Gtk.Label(label=f"{FREQ_MAX} MHz")
        self.s_cpu_max.connect("value-changed",
                               lambda w: self.l_cpu_max.set_text(f"{int(w.get_value())} MHz"))
        mx_row.pack_start(self.s_cpu_max, True, True, 0)
        mx_row.pack_start(self.l_cpu_max, False, False, 0)
        vb.pack_start(mx_row, False, False, 0)

        vb.pack_start(self._hdr("Min frequency"), False, False, 0)
        mn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        adj = Gtk.Adjustment(value=FREQ_MIN, lower=FREQ_MIN, upper=FREQ_MAX,
                             step_increment=100)
        self.s_cpu_min = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                   adjustment=adj)
        self.s_cpu_min.set_size_request(220, -1)
        self.s_cpu_min.connect("button-release-event", lambda *_: self.apply_cpu())
        self.l_cpu_min = Gtk.Label(label=f"{FREQ_MIN} MHz")
        self.s_cpu_min.connect("value-changed",
                               lambda w: self.l_cpu_min.set_text(f"{int(w.get_value())} MHz"))
        mn_row.pack_start(self.s_cpu_min, True, True, 0)
        mn_row.pack_start(self.l_cpu_min, False, False, 0)
        vb.pack_start(mn_row, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b = Gtk.Button(label="Apply CPU settings")
        b.connect("clicked", lambda *_: self.apply_cpu())
        btns.pack_start(b, False, False, 0)
        vb.pack_start(btns, False, False, 0)

        note = Gtk.Label(
            label="Real CPU overclocking (raising the multiplier past "
                  "stock turbo) is a BIOS job — software can only pin the "
                  "chip at its max turbo.  These changes need the "
                  "password dialog.",
            xalign=0.0, wrap=True)
        note.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_MUTED))
        vb.pack_start(note, False, False, 0)
        return vb

    def _tab_rgb(self):
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vb.set_margin_top(10)

        self.rgb_status = Gtk.Label(label="Connecting to RGB backend…", xalign=0.0)
        self.rgb_status.override_font(Pango_font("bold 11"))
        self.rgb_status.override_color(Gtk.StateFlags.NORMAL,
                                       Gdk_Color(COLOR_MUTED))
        vb.pack_start(self.rgb_status, False, False, 0)

        vb.pack_start(self._hdr("Device"), False, False, 0)
        row_dev = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.rgb_dev_combo = Gtk.ComboBoxText()
        self.rgb_dev_combo.set_size_request(340, -1)
        b_refresh = Gtk.Button(label="Refresh")
        b_refresh.connect("clicked", lambda *_: self._rgb_refresh())
        row_dev.pack_start(self.rgb_dev_combo, True, True, 0)
        row_dev.pack_start(b_refresh, False, False, 0)
        vb.pack_start(row_dev, False, False, 0)

        vb.pack_start(self._hdr("Solid color"), False, False, 0)
        row_col = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.rgb_colorbtn = Gtk.ColorButton()
        self.rgb_colorbtn.set_rgba(Gdk_Color("#00e08a"))
        b_color = Gtk.Button(label="Apply color")
        b_color.connect("clicked", lambda *_: self._rgb_apply_picked())
        row_col.pack_start(self.rgb_colorbtn, False, False, 0)
        row_col.pack_start(b_color, False, False, 0)
        vb.pack_start(row_col, False, False, 0)

        vb.pack_start(self._hdr("Presets"), False, False, 0)
        pres = {"Off": (0, 0, 0), "Red": (255, 0, 0), "Green": (0, 255, 0),
                "Blue": (0, 0, 255), "White": (255, 255, 255),
                "Cyan": (0, 255, 255), "Magenta": (255, 0, 255),
                "Yellow": (255, 255, 0)}
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        r1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        r2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for i, (name, col) in enumerate(pres.items()):
            b = Gtk.Button(label=name)
            b.connect("clicked", lambda _b, c=col: self._rgb_apply_color(c))
            (r1 if i < 4 else r2).pack_start(b, True, True, 0)
        rows.pack_start(r1, False, False, 0)
        rows.pack_start(r2, False, False, 0)
        vb.pack_start(rows, False, False, 0)

        b_all = Gtk.Button(label="Set All Devices One Color")
        b_all.connect("clicked", lambda *_: self._rgb_apply_all())
        vb.pack_start(b_all, False, False, 0)

        vb.pack_start(self._hdr("Brightness"), False, False, 0)
        adj = Gtk.Adjustment(value=100, lower=0, upper=100, step_increment=5)
        self.rgb_bright = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                    adjustment=adj)
        vb.pack_start(self.rgb_bright, False, False, 0)

        vb.pack_start(self._hdr("Animation"), False, False, 0)
        row_anim = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.rgb_anim = Gtk.ComboBoxText()
        self.rgb_anim.append("none", "None")
        self.rgb_anim.append("breath", "Breathing")
        self.rgb_anim.append("rainbow", "Rainbow")
        self.rgb_anim.set_active(0)
        self.rgb_anim.connect("changed", lambda *_: self._rgb_anim_start())
        adj = Gtk.Adjustment(value=30, lower=5, upper=80, step_increment=5)
        self.rgb_anim_speed = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                        adjustment=adj)
        self.rgb_anim_speed.set_size_request(160, -1)
        row_anim.pack_start(self.rgb_anim, False, False, 0)
        row_anim.pack_start(self.rgb_anim_speed, True, True, 0)
        vb.pack_start(row_anim, False, False, 0)

        vb.pack_start(self._hdr("Fan LED counts (Commander Core)"), False, False, 0)
        row_fans = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.rgb_fan_spins = []
        for i in range(6):
            lab = Gtk.Label(label=f"P{i+1}")
            sp = Gtk.SpinButton.new_with_range(0, 34, 1)
            sp.set_value(self._fan_sizes["ports"][i])
            boxc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            boxc.pack_start(lab, False, False, 0)
            boxc.pack_start(sp, False, False, 0)
            row_fans.pack_start(boxc, False, False, 0)
            self.rgb_fan_spins.append(sp)
        vb.pack_start(row_fans, False, False, 0)
        b_fans = Gtk.Button(label="Apply fan LED counts")
        b_fans.connect("clicked", lambda *_: self._rgb_fans_apply())
        vb.pack_start(b_fans, False, False, 0)

        note = Gtk.Label(
            label="Common fans: ML 4 LEDs, LL 16, QL 34. Colors light every "
                  "LED on a port; counts matter for per-LED animations.",
            xalign=0.0, wrap=True)
        note.override_color(Gtk.StateFlags.NORMAL, Gdk_Color(COLOR_MUTED))
        vb.pack_start(note, False, False, 0)
        return vb

    def _load_rgb_cfg(self):
        try:
            with open(RGB_CFG) as f:
                cfg = json.load(f)
            self._fan_sizes["pump"] = int(cfg.get("pump", 29))
            ports = [int(x) for x in cfg.get("ports", [8] * 6)]
            self._fan_sizes["ports"] = (ports + [8] * 6)[:6]
        except Exception:
            pass

    def _save_rgb_cfg(self):
        try:
            with open(RGB_CFG, "w") as f:
                json.dump(self._fan_sizes, f, indent=2)
        except Exception as e:
            self._log(f"  RGB cfg save failed: {e}")

    # ---------- rgb backend ----------

    def _rgb_connect(self):
        if self.rgb_connected:
            return True
        if not ensure_rgb_server():
            return False
        try:
            self.rgb_client = OpenRGBClient(port=RGB_PORT)
            self.rgb_connected = True
            self._log("RGB backend connected")
            return True
        except Exception as e:
            self._log(f"  RGB connect failed: {e}")
            return False

    def _rgb_refresh(self, *a):
        if not self._rgb_connect():
            return False
        try:
            self.rgb_devices = list(self.rgb_client.devices)
            combo = self.rgb_dev_combo
            combo.remove_all()
            for i, d in enumerate(self.rgb_devices):
                label = f"{i}: {d.name}"
                counts = {}
                for d2 in self.rgb_devices[:i]:
                    counts[d2.name] = counts.get(d2.name, 0) + 1
                dup = counts.get(d.name, 0)
                if dup:
                    label = f"{i}: {d.name} ({dup + 1})"
                combo.append_text(label)
            if self.rgb_devices:
                combo.set_active(0)
            self.rgb_status.set_text(
                f"{len(self.rgb_devices)} RGB devices found")
            self.rgb_status.override_color(Gtk.StateFlags.NORMAL,
                                           Gdk_Color(COLOR_ACCENT))
            return True
        except Exception as e:
            self.rgb_status.set_text(f"RGB refresh failed: {e}")
            self.rgb_status.override_color(Gtk.StateFlags.NORMAL,
                                           Gdk_Color(COLOR_BAD))
            return False

    def _rgb_watch(self):
        if self.rgb_connected:
            try:
                self.rgb_client.update()
            except Exception:
                self.rgb_connected = False
                self.rgb_client = None
                self._log("  RGB backend dropped, reconnecting…")
                self._rgb_refresh()
        elif self.rgb_status:
            self._rgb_refresh()
        return True

    def _rgb_selected(self):
        if not self.rgb_devices:
            return None
        idx = self.rgb_dev_combo.get_active()
        if idx < 0 or idx >= len(self.rgb_devices):
            return None
        return self.rgb_devices[idx]

    def _rgb_effective_color(self, color):
        b = int(self.rgb_bright.get_value()) / 100.0
        return tuple(int(round(c * b)) for c in color)

    def _rgb_apply_color(self, color):
        if not self._rgb_connect():
            self.rgb_status.set_text("RGB backend not available")
            return
        dev = self._rgb_selected()
        if dev is None:
            return
        try:
            self._anim_active = False
            self.rgb_anim.set_active(0)
            _pick_direct_mode(dev)
            dev.set_color(RGBColor(*self._rgb_effective_color(color)))
            self._log(f"RGB: {dev.name} -> {_rgb_hex(color)}")
            self.rgb_status.set_text(f"Set {dev.name} to {_rgb_hex(color)}")
        except Exception as e:
            self._log(f"  RGB apply failed: {e}")
            self.rgb_status.set_text(f"Apply failed: {e}")

    def _rgb_apply_all(self):
        if not self._rgb_connect():
            self.rgb_status.set_text("RGB backend not available")
            return
        rgba = self.rgb_colorbtn.get_rgba()
        col = (int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))
        applied = []
        for dev in self.rgb_devices:
            try:
                _pick_direct_mode(dev)
                dev.set_color(RGBColor(*self._rgb_effective_color(col)))
                applied.append(dev.name)
            except Exception as e:
                self._log(f"  RGB apply_all failed on {dev.name}: {e}")
        hex_c = _rgb_hex(col)
        for zone in range(6):
            try:
                subprocess.run(
                    ["python3", "asrock_rgb.py", str(zone), hex_c],
                    capture_output=True, timeout=5)
                applied.append(f"asrock_zone{zone}")
            except Exception as e:
                self._log(f"  asrock zone {zone} failed: {e}")
        self.rgb_status.set_text(f"All devices + board zones -> {hex_c} ({len(applied)} targets)")
        self._log(f"RGB: all devices + board zones -> {hex_c}")

    def _rgb_apply_picked(self):
        rgba = self.rgb_colorbtn.get_rgba()
        col = (int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))
        self._rgb_apply_color(col)

    def _rgb_fans_apply(self):
        if not self._rgb_connect():
            return
        sizes = [int(s.get_value()) for s in self.rgb_fan_spins]
        self._fan_sizes["ports"] = sizes
        self._save_rgb_cfg()
        try:
            for d in self.rgb_devices:
                if "Commander" in d.name and len(d.zones) == 7:
                    for z, sz in zip(d.zones[1:], sizes):
                        z.resize(sz)
                    self.rgb_client.update()
                    self._log(f"RGB fans sized {sizes}")
                    self.rgb_status.set_text("Fan LED counts applied")
                    return
            self.rgb_status.set_text("Commander Core not found")
        except Exception as e:
            self._log(f"  Fan sizing failed: {e}")
            self.rgb_status.set_text(f"Fan sizing failed: {e}")

    def _rgb_anim_start(self, *a):
        mode = self.rgb_anim.get_active_id()
        self._anim_active = mode in ("breath", "rainbow")
        self._anim_phase = 0
        self._anim_mode = mode
        if not self._anim_active:
            return

    def _rgb_anim_tick(self):
        if not self._anim_active:
            return True
        if not self._rgb_connect():
            return True
        dev = self._rgb_selected()
        if dev is None:
            return True
        mode = getattr(self, "_anim_mode", "breath")
        speed = int(self.rgb_anim_speed.get_value())
        self._anim_phase += speed * 0.004
        try:
            if mode == "breath":
                r, g, b = self._rgb_effective_color((0, 255, 150))
                f = 0.25 + 0.75 * (0.5 + 0.5 * __import__("math").sin(
                    self._anim_phase * 2 * 3.14159))
                _pick_direct_mode(dev)
                dev.set_color(RGBColor(int(r * f), int(g * f), int(b * f)),
                              fast=True)
            elif mode == "rainbow":
                n = len(dev.leds)
                if n == 0:
                    return True
                br = int(self.rgb_bright.get_value()) / 100.0
                colors = []
                for i in range(n):
                    hue = (self._anim_phase + i / max(n, 1)) % 1.0
                    r, g, blu = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                    colors.append(RGBColor(int(r * 255 * br),
                                           int(g * 255 * br),
                                           int(blu * 255 * br)))
                dev.set_colors(colors, fast=True)
        except Exception:
            pass
        return True

    def _tab_safety(self):
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vb.set_margin_top(10)
        vb.pack_start(self._hdr("Safety limits"), False, False, 0)

        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row1.pack_start(Gtk.Label(label="GPU temp limit (°C):"), False, False, 0)
        self.sp_gpu = Gtk.SpinButton.new_with_range(50, 95, 1)
        self.sp_gpu.set_value(83)
        row1.pack_start(self.sp_gpu, False, False, 0)
        vb.pack_start(row1, False, False, 0)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row2.pack_start(Gtk.Label(label="CPU temp limit (°C):"), False, False, 0)
        self.sp_cpu = Gtk.SpinButton.new_with_range(50, 105, 1)
        self.sp_cpu.set_value(90)
        row2.pack_start(self.sp_cpu, False, False, 0)
        vb.pack_start(row2, False, False, 0)

        self.chk_reset = Gtk.CheckButton(
            label="Auto-reset to stock when a limit is exceeded")
        self.chk_reset.set_active(True)
        vb.pack_start(self.chk_reset, False, False, 0)

        b = Gtk.Button(label="Reset everything now")
        b.connect("clicked", lambda *_: self.reset_gpu() or self.apply_cpu_reset())
        vb.pack_start(b, False, False, 0)
        return vb

    # ---------- actions ----------

    def _nvidia_set(self, attr, value, via_pkexec=False):
        cmd = ["nvidia-settings", "-a", f"[gpu:{GPU_ID}]/{attr}={value}"]
        if via_pkexec:
            cmd = ["pkexec"] + cmd
        ok, out, err = run(cmd)
        if not ok:
            self._log(f"  FAIL {attr}={value}: {err or out}")
        return ok

    def apply_gpu_oc(self, *a):
        core = int(self.s_core.get_value())
        mem = int(self.s_mem.get_value())
        pw = int(self.s_power.get_value())
        self._log(f"GPU oc: core {core:+d}  mem {mem:+d}  power {pw} W")
        self._nvidia_set("GPUGraphicsClockOffsetAllPerformanceLevels", core)
        self._nvidia_set("GPUMemoryTransferRateOffsetAllPerformanceLevels", mem)
        self._nvidia_set("GPUPowerMizerMode", 1)
        ok, _, err = run(["pkexec", "nvidia-smi", "-pl", str(pw)])
        if not ok:
            self._log(f"  FAIL power {pw}W: {err or 'denied'}")
        self._status("GPU overclock applied")
        GLib.idle_add(self.refresh_stats)

    def reset_gpu(self, *a):
        self._log("GPU reset to stock")
        self._nvidia_set("GPUGraphicsClockOffsetAllPerformanceLevels", 0)
        self._nvidia_set("GPUMemoryTransferRateOffsetAllPerformanceLevels", 0)
        self._nvidia_set("GPUPowerMizerMode", 0)
        self._nvidia_set("GPUFanControlState", 0)
        self.fan_mode.set_active(0)
        run(["pkexec", "nvidia-smi", "-pl", str(DEFAULT_POWER)])
        self.s_core.set_value(0)
        self.s_mem.set_value(0)
        self.s_power.set_value(DEFAULT_POWER)
        self._status("Reset to stock", good=False)
        GLib.idle_add(self.refresh_stats)

    def _fan_mode_changed(self, *a):
        self.s_fan.set_sensitive(self.fan_mode.get_active_id() == "manual")
        self.apply_fan()

    def apply_fan(self, *a):
        mode = self.fan_mode.get_active_id()
        if mode == "manual":
            sp = int(self.s_fan.get_value())
            self._log(f"GPU fan manual {sp}%")
            self._nvidia_set("GPUFanControlState", 1)
            self._nvidia_set("GPUTargetFanSpeed", sp, via_pkexec=False)
        else:
            self._log("GPU fan automatic")
            self._nvidia_set("GPUFanControlState", 0)
        GLib.idle_add(self.refresh_stats)

    def apply_cpu(self, *a):
        gov = self.cpu_gov.get_active_id()
        lo = int(self.s_cpu_min.get_value())
        hi = int(self.s_cpu_max.get_value())
        if lo > hi:
            lo, hi = hi, lo
        self._log(f"CPU: governor {gov}  {lo}-{hi} MHz")
        ok, out, err = run(["pkexec", "cpupower", "frequency-set",
                            "-g", gov, "-d", f"{lo}MHz", "-u", f"{hi}MHz"])
        if not ok:
            self._log(f"  FAIL cpu: {err or out}")
            self._status("CPU change failed", good=False)
        else:
            self._status("CPU settings applied")
        GLib.idle_add(self.refresh_stats)

    def apply_cpu_reset(self):
        self._log("CPU reset: powersave governor, default range")
        self.cpu_gov.set_active_id("powersave")
        self.s_cpu_min.set_value(FREQ_MIN)
        self.s_cpu_max.set_value(FREQ_MAX)
        run(["pkexec", "cpupower", "frequency-set",
             "-g", "powersave", "-d", f"{FREQ_MIN}MHz", "-u", f"{FREQ_MAX}MHz"])
        GLib.idle_add(self.refresh_stats)

    # ---------- monitoring ----------

    def refresh_stats(self):
        temp, fan, gclk, mclk, util, power, pl, _valid = gpu_stats()
        co, mo = gpu_offsets()
        pkg, cmax, cfreq, gov, mn, mx = cpu_stats()

        self.s_gpu_temp.value.set_text(fmt(temp, "°C"))
        self.s_gpu_fan.value.set_text(fmt(fan, "%"))
        self.s_gpu_util.value.set_text(fmt(util, "%"))
        self.s_gpu_clk.value.set_text(
            (fmt(gclk, "") if gclk else "—") + (" MHz" if gclk else ""))
        self.s_cpu_temp.value.set_text(
            (fmt(cmax, "°C") if cmax else "—"))
        self.s_cpu_freq.value.set_text(fmt(cfreq, " MHz"))

        sub = []
        sub.append(f"power {fmt(power, 'W')} / {fmt(pl, 'W')}")
        sub.append(f"mem {fmt(mclk, 'MHz')}")
        sub.append(f"offsets +{co or 0}/+{mo or 0}")
        if pkg is not None:
            sub.append(f"pkg {pkg:.0f}°C")
        if gov:
            sub.append(f"gov {gov}")
        if mn and mx:
            sub.append(f"cpu {mn}-{mx} MHz")
        self.set_title("OC Control — GPU / CPU / Fans / RGB")

        if self.chk_reset.get_active() and not self._reset_in_progress:
            gpu_lim = int(self.sp_gpu.get_value())
            cpu_lim = int(self.sp_cpu.get_value())
            over_gpu = temp is not None and temp > gpu_lim
            over_cpu = cmax is not None and cmax > cpu_lim
            if over_gpu or over_cpu:
                self._reset_in_progress = True
                which = []
                if over_gpu:
                    which.append(f"GPU {temp}°C > {gpu_lim}°C")
                if over_cpu:
                    which.append(f"CPU {cmax:.0f}°C > {cpu_lim}°C")
                self._log("!! SAFETY: " + ", ".join(which) + " — resetting")
                self.reset_gpu()
                self.apply_cpu_reset()
                self._reset_in_progress = False
        return True


def main():
    win = Occ()

    def _on_destroy(*a):
        try:
            if win.rgb_client is not None:
                win.rgb_client.disconnect()
        except Exception:
            pass
        Gtk.main_quit()

    win.connect("destroy", _on_destroy)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
