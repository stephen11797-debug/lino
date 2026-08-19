# Copyright (c) 2026 Stephen's Studio
# Licensed under the MIT License. See LICENSE file for details.
import gi
import math
import array
import json
import os
import re
import subprocess
import threading
import time

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

import re
import interface_control as ic

PRESET_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "interface_preset.json")

INPUT_DEFS = [
    ("In 1/2", [0, 1], True),
    ("In 3", [2], False),
    ("In 4", [3], False),
    ("In 5", [4], False),
    ("In 6", [5], False),
    ("In 7", [6], False),
    ("In 8", [7], False),
    ("Spdif", [8, 9], True),
    ("V 1/2", [18, 19], True),
    ("V 3", [20], False),
    ("V 4", [21], False),
    ("V 5", [22], False),
    ("V 6", [23], False),
    ("V 7", [24], False),
    ("V 8", [25], False),
]

BUS_NAMES = [
    "Main + HP1",
    "HP2 (Line 3-4)",
    "Line 5-6",
    "Line 7-8",
]

BUTTON_DEFS = [
    (ic.Button.PHANTOM, "48V"),
    (ic.Button.LINE, "Line 1/2"),
    (ic.Button.MONO, "Mono Main"),
    (ic.Button.MUTE, "Mute Main"),
]

FADER_MIN = -60.0
FADER_MAX = 6.0

CSS = """
window { background-color: #14141a; color: #e6e6ec; }
.header-bar { background-color: #1d1d27; color: #e6e6ec; border-bottom: 1px solid #2c2c38; }
frame { background-color: #20202b; border: 1px solid #303040; border-radius: 8px; }
frame > label { color: #7fb0ff; font-weight: bold; padding: 2px 6px; }
GtkLabel { color: #c9c9d4; }
.tiny { color: #8a8a96; font-size: 10px; }
GtkButton { background-color: #2b2b37; color: #e6e6ec; border-radius: 6px;
            border: 1px solid #3a3a48; padding: 6px 10px; }
GtkButton:hover { background-color: #343443; }
GtkToggleButton:checked { background-color: #3a6ea5; color: #ffffff; border-color: #4a82c4; }
.mix-btn { padding: 8px 12px; font-weight: bold; }
.mix-btn:checked { background-color: #2f5d8a; }
.send-strip { background-color: #1b1b24; border: 1px solid #2a2a36;
              border-radius: 6px; padding: 5px; }
.master-strip { background-color: #23232f; border: 1px solid #3a3a4c;
                border-radius: 6px; padding: 5px; }
.main-strip { background-color: #2a2233; border: 1px solid #5a3a6a;
              border-radius: 6px; padding: 5px; }
GtkScale trough { background-color: #0a0a10; border-radius: 4px; min-height: 32px; }
GtkScale trough highlight { background-color: #4a90d9; border-radius: 4px; }
GtkScale slider { background-color: #c0c0cc; min-width: 18px; min-height: 24px;
                  border-radius: 4px; border: 1px solid #888899; }
GtkScale slider:hover { background-color: #e0e0ee; }
GtkComboBox { background-color: #2b2b37; color: #e6e6ec; }
GtkInfoBar { background-color: #3a2f1a; color: #ffd9a0; }
GtkCheckButton { color: #c9c9d4; }
.ms-btn { padding: 1px 5px; font-size: 9px; font-weight: bold; }
"""


def _scan_alsa_cards():
    cards = []
    try:
        out = subprocess.run(
            ["arecord", "-l"], capture_output=True, text=True
        ).stdout
    except Exception:
        return cards
    for m in re.finditer(
        r"card (\d+): (\S+) \[([^\]]+)\], device (\d+): ([^\]]+)\[([^\]]+)\]", out
    ):
        card_num = int(m.group(1))
        card_id = m.group(2)
        card_name = m.group(3)
        dev_num = int(m.group(4))
        dev_id = m.group(5)
        dev_name = m.group(6).strip()
        cards.append({
            "card": card_num,
            "id": card_id,
            "name": card_name,
            "device": dev_num,
            "dev_name": dev_name,
            "hw": "hw:%s,%d" % (card_id, dev_num),
            "label": "Card %d: %s - %s" % (card_num, card_name, dev_name),
        })
    return cards


def _download_card_icon(card_id):
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".card_icons")
    os.makedirs(cache_dir, exist_ok=True)
    safe_id = card_id.replace("/", "_").replace(":", "_")
    png_path = os.path.join(cache_dir, "%s.png" % safe_id)
    if os.path.exists(png_path):
        return png_path
    def _find_gtk_icon():
        icon_names = ["audio-card", "audio-input-mic", "multimedia-audio-playback", "drive-removable-media"]
        for name in icon_names:
            icon = Gtk.IconTheme.get_default().lookup_icon(name, 48, 0)
            if icon:
                return icon.get_filename()
        return None
    icon_path = _find_gtk_icon()
    if icon_path:
        import shutil
        shutil.copy(icon_path, png_path)
        return png_path
    return None


_CARD_COLORS = ["#4a90d9", "#d94a4a", "#4ad94a", "#d9a04a", "#a04ad9", "#4ad9d9"]

def _make_card_icon_surface(card_id, card_num, label):
    try:
        from cairo import ImageSurface, FORMAT_ARGB32, Context
        size = 64
        surface = ImageSurface(FORMAT_ARGB32, size, size)
        ctx = Context(surface)
        color_hex = _CARD_COLORS[card_num % len(_CARD_COLORS)]
        r = int(color_hex[1:3], 16) / 255.0
        g = int(color_hex[3:5], 16) / 255.0
        b = int(color_hex[5:7], 16) / 255.0
        ctx.set_source_rgb(r, g, b)
        ctx.arc(size / 2, size / 2, size / 2 - 2, 0, 2 * 3.14159)
        ctx.fill()
        ctx.set_source_rgb(1.0, 1.0, 1.0)
        ctx.select_font_face("Sans", 0, 1)
        ctx.set_font_size(14)
        short = label.split()[0][:6]
        ext = ctx.text_extents(short)
        ctx.move_to(size / 2 - ext.width / 2, size / 2 + ext.height / 3)
        ctx.show_text(short)
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".card_icons")
        os.makedirs(cache_dir, exist_ok=True)
        safe_id = card_id.replace("/", "_").replace(":", "_")
        png_path = os.path.join(cache_dir, "%s.png" % safe_id)
        surface.write_to_png(png_path)
        return png_path
    except Exception:
        return None


class InterfaceApp:
    def __init__(self):
        self.dev = None
        self.state = ic.State()
        self.current_mix = 0
        self.meter_widgets = []
        self.peak = {}
        self.peak_hold = False
        self.button_toggles = {}
        self.mix_buttons = []
        self._in_select = False
        self._in_refresh = False
        self.sends = [[[0.0] * 2 for _ in range(26)] for _ in range(4)]
        self.mute_set = set()
        self.solo_set = set()
        self.cap_db = [-120.0] * 18
        self.cap2_db = [-120.0] * 18
        self.cap_running = False
        self.cap2_running = False
        self.cap_thread = None
        self.cap2_thread = None
        self.sample_rate = 48000
        self._poll_alive = False
        self.alsa_cards = _scan_alsa_cards()
        print("Available capture cards:", flush=True)
        for c in self.alsa_cards:
            print("  %s" % c["label"], flush=True)
        self.selected_card_hw = None
        self.linked_card_hw = None
        self.mix_linked = False
        self.virtuadaw_wires = []
        self.net_source = ""
        self.net_enabled = False
        self.route_name = ""
        self.route_enabled = False
        self._skip_welcome = False

        self._load_css()

        self.window = Gtk.Window(title="Studio Interface Manager")
        self.window.set_border_width(0)
        self.window.connect("destroy", self._on_destroy)
        self.window.set_default_size(1100, 700)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self._win_shown = False

        try:
            with open(PRESET_PATH) as f:
                _data = json.load(f)
            if _data.get("skip_welcome"):
                self._skip_welcome = True
        except Exception:
            pass

        if self._skip_welcome:
            self._build_ui()
            self._load_preset()
            self._build_strips(self.current_mix)
            self._refresh_buttons()
            self._try_connect()
            GLib.timeout_add(2000, self._auto_reconnect)
            self.window.show_all()
        else:
            self._build_welcome()

    def _build_welcome(self):
        for c in self.window.get_children():
            self.window.remove(c)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(20)
        self.window.add(vbox)

        title = Gtk.Label()
        title.set_markup('<span size="xx-large" weight="bold" color="#7fb0ff">'
                         'Studio 1824c Setup</span>')
        vbox.pack_start(title, False, False, 0)

        sub = Gtk.Label(label="Select your cards, aggregate mode, and device.")
        sub.get_style_context().add_class("tiny")
        vbox.pack_start(sub, False, False, 0)

        # --- Primary Card ---
        grp = Gtk.Frame(label="Primary Card")
        grp.set_border_width(4)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(8)

        self._w_card_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._w_card_btns = {}
        self._w_selected = None

        none_btn = Gtk.ToggleButton(label="None")
        none_btn.set_relief(Gtk.ReliefStyle.NORMAL)
        none_btn.set_tooltip_text("No card selected")
        none_btn.set_active(True)
        none_btn.connect("toggled", self._w_on_card_select, None)
        self._w_card_box.add(none_btn)
        self._w_card_btns[None] = none_btn

        for i, c in enumerate(self.alsa_cards):
            icon_path = _download_card_icon(c["id"])
            if not icon_path or not os.path.exists(icon_path):
                icon_path = _make_card_icon_surface(c["id"], i, c["name"])
            btn = Gtk.ToggleButton()
            if icon_path and os.path.exists(icon_path):
                img = Gtk.Image.new_from_file(icon_path)
                btn.set_image(img)
                btn.set_always_show_image(True)
            else:
                btn.set_label(c["label"][:12])
            btn.set_relief(Gtk.ReliefStyle.NORMAL)
            btn.set_tooltip_text("%s\nhw: %s\n%s" % (c["label"], c["hw"], c["dev_name"]))
            btn.connect("toggled", self._w_on_card_select, c["hw"])
            self._w_card_box.add(btn)
            self._w_card_btns[c["hw"]] = btn

        inner.add(self._w_card_box)

        self._w_card_info = Gtk.Label(label="No card selected")
        self._w_card_info.get_style_context().add_class("tiny")
        inner.add(self._w_card_info)

        grp.add(inner)
        vbox.pack_start(grp, False, False, 0)

        # --- Aggregate ---
        agg = Gtk.Frame(label="Aggregate / Link")
        agg.set_border_width(4)
        agg_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        agg_inner.set_border_width(8)

        agg_inner.add(Gtk.Label(label="2nd Card:"))
        self._w_link_combo = Gtk.ComboBoxText()
        self._w_link_combo.append_text("None")
        for c in self.alsa_cards:
            self._w_link_combo.append_text(c["label"])
        self._w_link_combo.set_active(0)
        agg_inner.add(self._w_link_combo)

        self._w_mix_cb = Gtk.CheckButton(label="Mix (aggregate)")
        self._w_mix_cb.set_active(True)
        agg_inner.add(self._w_mix_cb)

        agg.add(agg_inner)
        vbox.pack_start(agg, False, False, 0)

        # --- Device Settings ---
        dev = Gtk.Frame(label="Device")
        dev.set_border_width(4)
        dev_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dev_inner.set_border_width(8)

        dev_inner.add(Gtk.Label(label="Clock:"))
        self._w_clock_combo = Gtk.ComboBoxText()
        for n in ("Internal", "S/PDIF", "ADAT"):
            self._w_clock_combo.append_text(n)
        try:
            cn = ic.alsa_numid(ic.CLOCK_NAME)
            cur = ic.alsa_enum_get(cn) or 0
            self._w_clock_combo.set_active(cur)
        except Exception:
            self._w_clock_combo.set_active(0)
        dev_inner.add(self._w_clock_combo)

        dev_inner.add(Gtk.Label(label="Rate:"))
        self._w_rate_combo = Gtk.ComboBoxText()
        for r in (44100, 48000, 96000, 192000):
            self._w_rate_combo.append_text("%d" % r)
        self._w_rate_combo.set_active(1)
        dev_inner.add(self._w_rate_combo)

        dev.add(dev_inner)
        vbox.pack_start(dev, False, False, 0)

        # --- VirtuaDAW Wires ---
        wd = Gtk.Frame(label="VirtuaDAW Wires")
        wd.set_border_width(4)
        wd_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wd_inner.set_border_width(8)

        self._w_wire_list = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        wd_inner.add(self._w_wire_list)

        wire_add = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._w_wire_src = Gtk.ComboBoxText()
        self._w_wire_src.append_text("Pri")
        self._w_wire_src.append_text("Link")
        for c in self.alsa_cards:
            self._w_wire_src.append_text(c["id"])
        self._w_wire_src.set_active(0)
        wire_add.add(self._w_wire_src)
        self._w_wire_src_ch = Gtk.ComboBoxText()
        for i in range(1, 19):
            self._w_wire_src_ch.append_text("O%d" % i)
        self._w_wire_src_ch.set_active(0)
        wire_add.add(self._w_wire_src_ch)
        wire_add.add(Gtk.Label(label="\u2192"))
        self._w_wire_dst = Gtk.ComboBoxText()
        self._w_wire_dst.append_text("Pri")
        self._w_wire_dst.append_text("Link")
        for c in self.alsa_cards:
            self._w_wire_dst.append_text(c["id"])
        self._w_wire_dst.set_active(0)
        wire_add.add(self._w_wire_dst)
        self._w_wire_dst_ch = Gtk.ComboBoxText()
        for i in range(1, 19):
            self._w_wire_dst_ch.append_text("I%d" % i)
        self._w_wire_dst_ch.set_active(0)
        wire_add.add(self._w_wire_dst_ch)
        wadd = Gtk.Button(label="+")
        wadd.set_size_request(28, -1)
        wadd.connect("clicked", self._w_add_wire)
        wire_add.add(wadd)
        wclr = Gtk.Button(label="Clr")
        wclr.connect("clicked", self._w_clear_wires)
        wire_add.add(wclr)
        wd_inner.add(wire_add)

        wd.add(wd_inner)
        vbox.pack_start(wd, False, False, 0)

        # --- NDI ---
        ndi = Gtk.Frame(label="NDI")
        ndi.set_border_width(4)
        ndi_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ndi_inner.set_border_width(8)

        ndi_r1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ndi_r1.add(Gtk.Label(label="In:"))
        self._w_ndi_in = Gtk.Entry()
        self._w_ndi_in.set_placeholder_text("ndi://host/source")
        self._w_ndi_in.set_text(self.net_source)
        ndi_r1.add(self._w_ndi_in)
        self._w_ndi_in_cb = Gtk.CheckButton(label="Enable")
        self._w_ndi_in_cb.set_active(self.net_enabled)
        ndi_r1.add(self._w_ndi_in_cb)
        ndi_inner.add(ndi_r1)

        ndi_r2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ndi_r2.add(Gtk.Label(label="Out:"))
        self._w_ndi_out = Gtk.Entry()
        self._w_ndi_out.set_placeholder_text("stream name")
        self._w_ndi_out.set_text(self.route_name)
        ndi_r2.add(self._w_ndi_out)
        self._w_ndi_out_cb = Gtk.CheckButton(label="Enable")
        self._w_ndi_out_cb.set_active(self.route_enabled)
        ndi_r2.add(self._w_ndi_out_cb)
        ndi_inner.add(ndi_r2)

        ndi.add(ndi_inner)
        vbox.pack_start(ndi, False, False, 0)

        skip_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._w_skip_cb = Gtk.CheckButton(label="Don't show again (skip to mixer next time)")
        self._w_skip_cb.set_active(self._skip_welcome)
        skip_row.add(self._w_skip_cb)
        vbox.pack_start(skip_row, False, False, 0)

        # --- Launch ---
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        launch = Gtk.Button(label="Launch Mixer")
        launch.get_style_context().add_class("mix-btn")
        launch.connect("clicked", self._w_launch)
        btn_row.pack_start(launch, True, True, 0)
        vbox.pack_start(btn_row, False, False, 0)

        self.window.show_all()

    def _w_on_card_select(self, btn, hw):
        if btn.get_active():
            self._w_selected = hw
            for b in self._w_card_btns.values():
                if b != btn:
                    b.set_active(False)
            if hw is None:
                self._w_card_info.set_text("No card selected")
            else:
                for c in self.alsa_cards:
                    if c["hw"] == hw:
                        self._w_card_info.set_text(
                            "hw: %s  |  %s  |  %s" % (c["hw"], c["name"], c["dev_name"]))
                        break
        else:
            if self._w_selected == hw:
                self._w_selected = None
                self._w_card_info.set_text("No card selected")

    def _w_add_wire(self, *_):
        src_names = ["Pri", "Link"] + [c["id"] for c in self.alsa_cards]
        dst_names = ["Pri", "Link"] + [c["id"] for c in self.alsa_cards]
        self.virtuadaw_wires.append({
            "src_card": src_names[self._w_wire_src.get_active()],
            "src_ch": self._w_wire_src_ch.get_active(),
            "dst_card": dst_names[self._w_wire_dst.get_active()],
            "dst_ch": self._w_wire_dst_ch.get_active(),
        })
        self._w_refresh_wires()

    def _w_clear_wires(self, *_):
        self.virtuadaw_wires.clear()
        self._w_refresh_wires()

    def _w_refresh_wires(self):
        for c in self._w_wire_list.get_children():
            self._w_wire_list.remove(c)
        for i, w in enumerate(self.virtuadaw_wires):
            lbl = Gtk.Label(label="%sO%d\u2192%sI%d " % (
                w["src_card"], w["src_ch"] + 1, w["dst_card"], w["dst_ch"] + 1))
            self._w_wire_list.add(lbl)
        self._w_wire_list.show_all()

    def _w_launch(self, *_):
        self.selected_card_hw = self._w_selected
        link_idx = self._w_link_combo.get_active()
        self.linked_card_hw = None if link_idx <= 0 else self.alsa_cards[link_idx - 1]["hw"]
        self.mix_linked = self._w_mix_cb.get_active()
        try:
            self.sample_rate = int(self._w_rate_combo.get_active_text())
        except (ValueError, TypeError):
            self.sample_rate = 48000
        try:
            cn = ic.alsa_numid(ic.CLOCK_NAME)
            if cn is not None:
                ic.alsa_enum_set(cn, self._w_clock_combo.get_active())
        except Exception:
            pass
        self.net_source = self._w_ndi_in.get_text().strip()
        self.net_enabled = self._w_ndi_in_cb.get_active()
        self.route_name = self._w_ndi_out.get_text().strip()
        self.route_enabled = self._w_ndi_out_cb.get_active()
        self._skip_welcome = self._w_skip_cb.get_active()

        self._save_preset()

        for c in self.window.get_children():
            self.window.remove(c)
        self._build_ui()
        self._load_preset()
        self._build_strips(self.current_mix)
        self._refresh_buttons()
        self._try_connect()
        GLib.timeout_add(2000, self._auto_reconnect)
        self.window.show_all()

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _dbg(self, msg):
        try:
            with open("/tmp/iface_dbg.log", "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def _try_connect(self):
        if self.dev is not None:
            return True
        try:
            self.dev = ic.Device()
            self.state.counter = 1
            self.dev_error = None
            self.status_lbl.set_text("FADERS LIVE")
            self._poll_alive = True
            self._poll_thread = threading.Thread(
                target=self._poll_loop, daemon=True)
            self._poll_thread.start()
            self._stop_capture()
            self._build_strips(self.current_mix)
            self._dbg("connected")
            return True
        except Exception as e:
            self.dev = None
            self.dev_error = str(e)
            self.status_lbl.set_text("WAITING FOR DEVICE...")
            if not self.cap_running:
                self._start_capture()
                self._start_capture2()
                GLib.timeout_add(100, self._update_meters)
            return False

    def _auto_reconnect(self):
        if self.dev is None:
            self._try_connect()
        return True

    def _on_reconnect(self, *_):
        self._poll_alive = False
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        self._try_connect()

    def _on_test_mode(self, btn):
        if btn.get_active():
            self._pre_test_sends = [row[:] for row in self.sends[self.current_mix]]
            for name, idxs, stereo in INPUT_DEFS:
                for ch, ix in enumerate(idxs):
                    self.sends[self.current_mix][ix][ch] = 0.0
            self._apply_mix(self.current_mix)
            self._build_strips(self.current_mix)
        else:
            if hasattr(self, '_pre_test_sends'):
                self.sends[self.current_mix] = self._pre_test_sends
            self._apply_mix(self.current_mix)
            self._build_strips(self.current_mix)

    def _show_dev_banner(self):
        bar = Gtk.InfoBar()
        bar.set_message_type(Gtk.MessageType.WARNING)
        bar.set_show_close_button(False)
        lab = Gtk.Label(
            "Meters are live via ALSA capture. Moving the faders still needs "
            "root or the udev rule (99-studio-interface.rules).")
        lab.set_line_wrap(True)
        bar.get_content_area().add(lab)
        self.vbox.pack_start(bar, False, False, 0)
        bar.show_all()

    def _build_ui(self):
        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(self.vbox)

        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb.set_border_width(4)
        hb.get_style_context().add_class("header-bar")
        self.status_lbl = Gtk.Label(
            label="FADERS LIVE" if self.dev else "BUTTONS ONLY (no USB)")
        self.status_lbl.get_style_context().add_class("tiny")
        hb.pack_end(self.status_lbl, False, False, 0)
        route = Gtk.Button(label="Routing")
        route.connect("clicked", self._show_routing)
        hb.pack_end(route, False, False, 0)
        save = Gtk.Button(label="Save")
        save.connect("clicked", lambda *_: self._save_preset())
        hb.pack_end(save, False, False, 0)
        reconnect = Gtk.Button(label="Reconnect")
        reconnect.connect("clicked", self._on_reconnect)
        hb.pack_end(reconnect, False, False, 0)
        self.test_btn = Gtk.ToggleButton(label="Test Mode")
        self.test_btn.connect("toggled", self._on_test_mode)
        hb.pack_end(self.test_btn, False, False, 0)
        ph = Gtk.CheckButton(label="Peak Hold")
        ph.connect("toggled", self._on_peak_hold)
        hb.pack_end(ph, False, False, 0)
        self.vbox.pack_start(hb, False, False, 0)

        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_bar.set_border_width(4)
        status_bar.get_style_context().add_class("send-strip")

        hw_label = "None"
        if self.selected_card_hw:
            for c in self.alsa_cards:
                if c["hw"] == self.selected_card_hw:
                    hw_label = c["name"]
                    break
        link_label = "None"
        if self.linked_card_hw:
            for c in self.alsa_cards:
                if c["hw"] == self.linked_card_hw:
                    link_label = c["name"]
                    break

        info_parts = ["Card: %s" % hw_label]
        if self.linked_card_hw:
            info_parts.append("Linked: %s" % link_label)
        if self.mix_linked:
            info_parts.append("Mix")
        info_parts.append("%d Hz" % self.sample_rate)
        if self.virtuadaw_wires:
            info_parts.append("%d wire(s)" % len(self.virtuadaw_wires))
        if self.net_enabled and self.net_source:
            info_parts.append("NDI In")
        if self.route_enabled and self.route_name:
            info_parts.append("NDI Out")

        info_lbl = Gtk.Label(label="  |  ".join(info_parts))
        info_lbl.get_style_context().add_class("tiny")
        status_bar.pack_start(info_lbl, False, False, 0)

        setup_btn = Gtk.Button(label="Setup...")
        setup_btn.connect("clicked", self._relaunch_welcome)
        status_bar.pack_end(setup_btn, False, False, 0)

        self.vbox.pack_start(status_bar, False, False, 0)

        fp = Gtk.Frame(label="Front Panel")
        fp_grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fp_grid.set_border_width(8)
        for bid, label in BUTTON_DEFS:
            t = Gtk.ToggleButton(label=label)
            t.connect("toggled", self._on_button, bid)
            self.button_toggles[bid] = t
            fp_grid.add(t)
        fp.add(fp_grid)
        self.vbox.pack_start(fp, False, False, 0)

        dev = Gtk.Frame(label="Device")
        dev_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        dev_vbox.set_border_width(8)

        dev_row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dev_row1.add(Gtk.Label(label="Clock:"))
        self.clock_combo = Gtk.ComboBoxText()
        for n in ("Internal", "S/PDIF", "ADAT"):
            self.clock_combo.append_text(n)
        try:
            cn = ic.alsa_numid(ic.CLOCK_NAME)
            cur = ic.alsa_enum_get(cn) or 0
            self.clock_combo.set_active(cur)
        except Exception:
            self.clock_combo.set_active(0)
        self.clock_combo.connect("changed", self._on_clock)
        dev_row1.add(self.clock_combo)
        dev_row1.add(Gtk.Label(label="Sample Rate:"))
        self.rate_combo = Gtk.ComboBoxText()
        for r in (44100, 48000, 96000, 192000):
            self.rate_combo.append_text("%d" % r)
        self.rate_combo.set_active(1)
        self.rate_combo.connect("changed", self._on_rate)
        dev_row1.add(self.rate_combo)
        dev_vbox.add(dev_row1)

        dev.add(dev_vbox)
        self.vbox.pack_start(dev, False, False, 0)

        mx = Gtk.Frame(label="Monitor Mix")
        mx_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mx_vbox.set_border_width(8)
        mx_grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for b in range(4):
            t = Gtk.ToggleButton(label=BUS_NAMES[b])
            t.get_style_context().add_class("mix-btn")
            t.connect("toggled", self._on_mix_select, b)
            self.mix_buttons.append(t)
            mx_grid.add(t)
        mx_vbox.add(mx_grid)

        copy_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        copy_row.add(Gtk.Label(label="Copy:"))
        self.src_combo = Gtk.ComboBoxText()
        for n in BUS_NAMES:
            self.src_combo.append_text(n)
        self.src_combo.set_active(0)
        copy_row.add(self.src_combo)
        copy_row.add(Gtk.Label(label="\u2192"))
        self.dst_combo = Gtk.ComboBoxText()
        for n in BUS_NAMES:
            self.dst_combo.append_text(n)
        self.dst_combo.set_active(1)
        copy_row.add(self.dst_combo)
        cb = Gtk.Button(label="Copy Mix")
        cb.connect("clicked", self._copy_mix)
        copy_row.add(cb)
        mx_vbox.add(copy_row)
        mx.add(mx_vbox)
        self.vbox.pack_start(mx, False, False, 0)

        self.strip_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.strip_area.set_border_width(8)
        self.vbox.pack_start(self.strip_area, True, True, 0)

        self._build_strips(0)
        self.mix_buttons[0].set_active(True)
        self.window.show_all()

    def _make_fader(self, initial, handler, *args):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        adj = Gtk.Adjustment(
            value=initial, lower=FADER_MIN, upper=FADER_MAX, step_increment=0.5)
        sc = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, adjustment=adj)
        sc.set_inverted(True)
        sc.set_size_request(40, 230)
        sc.set_digits(0)
        lab = Gtk.Label(label=f"{initial:+.0f}")
        lab.get_style_context().add_class("tiny")

        def on_change(s):
            v = s.get_value()
            lab.set_text(f"{v:+.0f}")
            handler(s, *args)

        sc.connect("value-changed", on_change)
        box.add(sc)
        box.add(lab)
        return box, sc

    def _build_strips(self, mix):
        self.meter_widgets = []
        self.peak = {}
        new = []

        for name, idxs, stereo in INPUT_DEFS:
            strip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            strip.get_style_context().add_class("send-strip")
            top = Gtk.Label(label=name)
            top.get_style_context().add_class("tiny")
            top.set_justify(Gtk.Justification.CENTER)
            strip.add(top)

            if stereo:
                mbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
                for ix in idxs:
                    m = Gtk.ProgressBar(orientation=Gtk.Orientation.VERTICAL)
                    m.set_inverted(True)
                    m.set_size_request(6, 100)
                    self.meter_widgets.append((m, ix))
                    mbox.add(m)
                strip.add(mbox)
            else:
                ix = idxs[0]
                m = Gtk.ProgressBar(orientation=Gtk.Orientation.VERTICAL)
                m.set_inverted(True)
                m.set_size_request(8, 100)
                self.meter_widgets.append((m, ix))
                strip.add(m)

            if stereo:
                fbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
                for ch, ix in enumerate(idxs):
                    fb, _ = self._make_fader(
                        self.sends[mix][ix][ch], self._on_send, mix, ix, ch)
                    fbox.add(fb)
                strip.add(fbox)
            else:
                ix = idxs[0]
                fb, _ = self._make_fader(
                    self.sends[mix][ix][0], self._on_send, mix, ix, 0)
                strip.add(fb)
            ix = idxs[0]
            mb = Gtk.ToggleButton(label="M")
            mb.set_active((mix, ix) in self.mute_set)
            mb.connect("toggled", self._on_mute, mix, ix)
            mb.get_style_context().add_class("ms-btn")
            sb = Gtk.ToggleButton(label="S")
            sb.set_active((mix, ix) in self.solo_set)
            sb.connect("toggled", self._on_solo, mix, ix)
            sb.get_style_context().add_class("ms-btn")
            mrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            mrow.add(mb)
            mrow.add(sb)
            strip.add(mrow)
            new.append(strip)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_size_request(2, -1)
        new.append(sep)

        out_lab = Gtk.Label(label="OUTPUTS")
        out_lab.get_style_context().add_class("tiny")
        out_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        out_box.add(out_lab)
        new.append(out_box)

        for m in range(4):
            mstrip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            mstrip.get_style_context().add_class(
                "master-strip" if m == mix else "send-strip")
            ml2 = Gtk.Label(label=BUS_NAMES[m].split(" ")[0].upper())
            ml2.get_style_context().add_class("tiny")
            mstrip.add(ml2)
            mbox, _ = self._make_fader(0, self._on_bus_master, m)
            mstrip.add(mbox)
            new.append(mstrip)

        for c in self.strip_area.get_children():
            self.strip_area.remove(c)
        for w in new:
            self.strip_area.add(w)
        self.strip_area.show_all()
        self._dbg("built mix %d -> %d widgets" % (mix, len(new)))

    def _on_mix_select(self, btn, mix):
        if self._in_select:
            return
        self._in_select = True
        if not btn.get_active():
            btn.set_active(True)
        self.current_mix = mix
        for i, b in enumerate(self.mix_buttons):
            if i != mix:
                b.set_active(False)
        self._in_select = False
        self._build_strips(mix)

    def _send_usb(self, build):
        if self.dev is None:
            return
        try:
            cmd = ic.Command()
            build(cmd)
            self.dev.send(cmd)
        except Exception as e:
            print("usb send failed:", e)

    def _on_button(self, toggle, bid):
        if self._in_refresh:
            return
        self._send_usb(lambda c: c.set_button(bid, toggle.get_active()))

    def _on_peak_hold(self, btn):
        self.peak_hold = btn.get_active()
        if not self.peak_hold:
            self.peak = {}

    def _on_card_icon(self, btn, card):
        if self.card_combo is not None:
            idx = self.alsa_cards.index(card) + 1
            self.card_combo.set_active(idx)

    def _on_card_picker_select(self, btn, hw):
        if btn.get_active():
            self.selected_card_hw = hw
            self._restart_capture()

    def _on_link_check(self, btn):
        pass

    def _on_mix_check(self, btn):
        self.mix_linked = btn.get_active()

    def _on_card_primary_combo(self, combo):
        idx = combo.get_active()
        if idx <= 0:
            self.selected_card_hw = None
            if self._card_none_btn:
                self._card_none_btn.set_active(True)
        else:
            self.selected_card_hw = self.alsa_cards[idx - 1]["hw"]
            if self._card_none_btn:
                self._card_none_btn.set_active(False)
            btn = self._card_btns.get(self.selected_card_hw)
            if btn:
                btn.set_active(True)
        self._restart_capture()

    def _on_card_link_combo(self, combo):
        idx = combo.get_active()
        if idx <= 0:
            self.linked_card_hw = None
        else:
            self.linked_card_hw = self.alsa_cards[idx - 1]["hw"]
        self._restart_capture()

    def _on_card_select(self, combo):
        idx = combo.get_active()
        if idx <= 0:
            self.selected_card_hw = None
        else:
            self.selected_card_hw = self.alsa_cards[idx - 1]["hw"]
        self._restart_capture()

    def _on_link_select(self, combo):
        idx = combo.get_active()
        if idx <= 0:
            self.linked_card_hw = None
        else:
            self.linked_card_hw = self.alsa_cards[idx - 1]["hw"]
        self._restart_capture()

    def _on_mix_toggled(self, btn):
        self.mix_linked = btn.get_active()

    def _on_card_icon(self, btn, card):
        if self.card_combo is not None:
            idx = self.alsa_cards.index(card) + 1
            self.card_combo.set_active(idx)

    def _on_net_toggle(self, btn):
        if btn.get_active():
            src = self.net_source.strip()
            if src:
                self._start_net_capture(src)

    def _start_net_capture(self, src):
        def _run():
            try:
                cmd = ["ffmpeg", "-i", src, "-f", "s16le", "-acodec", "pcm_s16le",
                       "-ac", "18", "-ar", str(self.sample_rate), "-"]
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                ch = 18
                frame_bytes = ch * 2
                buf = bytearray()
                while self.cap_running and self.net_enabled:
                    data = p.stdout.read(8192)
                    if not data:
                        break
                    buf.extend(data)
                    usable = (len(buf) // frame_bytes) * frame_bytes
                    if usable == 0:
                        continue
                    arr = array.array("h")
                    arr.frombytes(bytes(buf[:usable]))
                    del buf[:usable]
                    nframes = len(arr) // ch
                    sums = [0.0] * ch
                    for f in range(nframes):
                        base = f * ch
                        for c in range(ch):
                            v = arr[base + c]
                            sums[c] += v * v
                    for c in range(ch):
                        rms = math.sqrt(sums[c] / nframes) if nframes else 0.0
                        db = 20.0 * math.log10(rms / 32768.0) if rms > 0 else -120.0
                        self.cap_db[c] = max(-120.0, min(10.0, db))
            except Exception as e:
                print("net capture failed:", e)
        threading.Thread(target=_run, daemon=True).start()

    def _on_route_toggle(self, btn):
        if btn.get_active():
            route = self.route_name.strip()
            if route:
                self._apply_routing(route)

    def _apply_routing(self, route_str):
        for part in route_str.split(","):
            part = part.strip()
            if "->" not in part:
                continue
            src_str, dst_str = part.split("->", 1)
            src_str = src_str.strip()
            dst_str = dst_str.strip()
            try:
                src_bus = int(src_str)
                dst_bus = int(dst_str)
                cmd = ic.Command()
                cmd.set_input_fader(src_bus, dst_bus, 0, ic.Value.Unity())
                if self.dev:
                    self.dev.send(cmd)
                print("Routed In%d -> Bus%d" % (src_bus, dst_bus), flush=True)
            except ValueError:
                pass

    def _on_clock(self, combo):
        try:
            cn = ic.alsa_numid(ic.CLOCK_NAME)
            if cn is not None:
                ic.alsa_enum_set(cn, combo.get_active())
            else:
                print("clock: ALSA numid not found, skipping")
        except Exception as e:
            print("clock set failed:", e)

    def _restart_capture(self):
        was_running = self.cap_running
        self._stop_capture()
        self._stop_capture2()
        if was_running:
            self._start_capture()
            self._start_capture2()

    def _on_rate(self, combo):
        try:
            self.sample_rate = int(combo.get_active_text())
        except (ValueError, TypeError):
            self.sample_rate = 48000
        self._restart_capture()

    def _on_bus_master(self, scale, mix):
        self._send_usb(lambda c: c.set_output_fader(mix, ic.Value.DB(scale.get_value())))

    def _solo_active(self, mix):
        return any((mix, ix) in self.solo_set for ix in range(26))

    def _effective(self, mix, ix, ch):
        muted = (mix, ix) in self.mute_set
        if not muted and self._solo_active(mix):
            muted = (mix, ix) not in self.solo_set
        if muted:
            return ic.Value.Muted()
        return ic.Value.DB(self.sends[mix][ix][ch])

    def _apply_channel(self, mix, ix, ch):
        self._send_usb(
            lambda c: c.set_input_fader(ix, mix, ch, self._effective(mix, ix, ch)))

    def _apply_mix(self, mix):
        for name, idxs, stereo in INPUT_DEFS:
            for ch, ix in enumerate(idxs):
                self._apply_channel(mix, ix, ch)

    def _on_send(self, scale, mix, ix, ch):
        self.sends[mix][ix][ch] = scale.get_value()
        self._apply_channel(mix, ix, ch)

    def _on_mute(self, btn, mix, ix):
        if btn.get_active():
            self.mute_set.add((mix, ix))
        else:
            self.mute_set.discard((mix, ix))
        self._apply_mix(mix)

    def _on_solo(self, btn, mix, ix):
        if btn.get_active():
            self.solo_set.add((mix, ix))
        else:
            self.solo_set.discard((mix, ix))
        self._apply_mix(mix)

    def _copy_mix(self, *_):
        src = self.src_combo.get_active()
        dst = self.dst_combo.get_active()
        if src == dst:
            return
        for name, idxs, stereo in INPUT_DEFS:
            for ch, ix in enumerate(idxs):
                val = self.sends[src][ix][ch]
                self.sends[dst][ix][ch] = val
                self._send_usb(
                    lambda c, ix=ix, dst=dst, ch=ch, val=val:
                    c.set_input_fader(ix, dst, ch, ic.Value.DB(val)))
        for ix in range(26):
            self.mute_set.add((dst, ix)) if (src, ix) in self.mute_set \
                else self.mute_set.discard((dst, ix))
            self.solo_set.add((dst, ix)) if (src, ix) in self.solo_set \
                else self.solo_set.discard((dst, ix))
        self._apply_mix(dst)
        if dst == self.current_mix:
            self._build_strips(dst)

    def _meter_norm(self, idx):
        if 0 <= idx <= 7:
            raw = self.state.mic[idx]
        elif 8 <= idx <= 9:
            raw = self.state.spdif[idx - 8]
        elif 18 <= idx <= 25:
            raw = self.state.daw[idx - 18]
        else:
            raw = 0
        return min(1.0, max(0.0, (ic.gain_to_db(raw) + 60) / 66))

    def _proto_to_cap(self, idx):
        if 0 <= idx <= 9:
            return idx
        if 18 <= idx <= 25:
            return idx - 18 + 10
        return None

    def _cap_level_norm(self, idx):
        cap = self._proto_to_cap(idx)
        if cap is None or cap >= len(self.cap_db):
            return 0.0
        lvl1 = min(1.0, max(0.0, (self.cap_db[cap] + 60) / 66))
        if self.mix_linked and self.linked_card_hw and cap < len(self.cap2_db):
            lvl2 = min(1.0, max(0.0, (self.cap2_db[cap] + 60) / 66))
            return max(lvl1, lvl2)
        return lvl1

    def _start_capture(self):
        if self.cap_running or self.selected_card_hw is None:
            return
        self.cap_running = True
        self.cap_thread = threading.Thread(
            target=self._capture_loop, daemon=True)
        self.cap_thread.start()

    def _stop_capture(self):
        self.cap_running = False

    def _start_capture2(self):
        if self.cap2_running or self.linked_card_hw is None:
            return
        self.cap2_running = True
        self.cap2_thread = threading.Thread(
            target=self._capture_loop2, daemon=True)
        self.cap2_thread.start()

    def _stop_capture2(self):
        self.cap2_running = False

    def _capture_loop(self):
        hw = self.selected_card_hw
        cmd = ["arecord", "-D", hw, "-r", str(self.sample_rate),
               "-f", "S32_LE", "-c", "18", "-t", "raw", "-"]
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.cap_running = False
            print("capture start failed:", e)
            return
        ch = 18
        frame_bytes = ch * 4
        buf = bytearray()
        while self.cap_running:
            data = p.stdout.read(8192)
            if not data:
                break
            buf.extend(data)
            usable = (len(buf) // frame_bytes) * frame_bytes
            if usable == 0:
                continue
            arr = array.array("i")
            arr.frombytes(bytes(buf[:usable]))
            del buf[:usable]
            nframes = len(arr) // ch
            sums = [0.0] * ch
            for f in range(nframes):
                base = f * ch
                for c in range(ch):
                    v = arr[base + c]
                    sums[c] += v * v
            for c in range(ch):
                rms = math.sqrt(sums[c] / nframes) if nframes else 0.0
                db = 20.0 * math.log10(rms / 2147483648.0) if rms > 0 else -120.0
                self.cap_db[c] = max(-120.0, min(10.0, db))
        try:
            p.terminate()
        except Exception:
            pass

    def _capture_loop2(self):
        hw = self.linked_card_hw
        cmd = ["arecord", "-D", hw, "-r", str(self.sample_rate),
               "-f", "S32_LE", "-c", "18", "-t", "raw", "-"]
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.cap2_running = False
            print("capture2 start failed:", e)
            return
        ch = 18
        frame_bytes = ch * 4
        buf = bytearray()
        while self.cap2_running:
            data = p.stdout.read(8192)
            if not data:
                break
            buf.extend(data)
            usable = (len(buf) // frame_bytes) * frame_bytes
            if usable == 0:
                continue
            arr = array.array("i")
            arr.frombytes(bytes(buf[:usable]))
            del buf[:usable]
            nframes = len(arr) // ch
            sums = [0.0] * ch
            for f in range(nframes):
                base = f * ch
                for c in range(ch):
                    v = arr[base + c]
                    sums[c] += v * v
            for c in range(ch):
                rms = math.sqrt(sums[c] / nframes) if nframes else 0.0
                db = 20.0 * math.log10(rms / 2147483648.0) if rms > 0 else -120.0
                self.cap2_db[c] = max(-120.0, min(10.0, db))
        try:
            p.terminate()
        except Exception:
            pass

    def _refresh_buttons(self):
        self._in_refresh = True
        vals = {
            ic.Button.PHANTOM: bool(self.state.phantom),
            ic.Button.LINE: bool(self.state.line),
            ic.Button.MONO: bool(self.state.mono),
            ic.Button.MUTE: bool(self.state.mute),
        }
        for bid, _ in BUTTON_DEFS:
            val = vals.get(bid)
            t = self.button_toggles.get(bid)
            if t is not None and val is not None and t.get_active() != val:
                t.set_active(val)
        self._in_refresh = False

    def _poll_loop(self):
        while self._poll_alive and self.dev is not None:
            try:
                self.dev.poll(self.state)
            except Exception:
                self._poll_alive = False
                GLib.idle_add(self._on_disconnect)
                return
            GLib.idle_add(self._update_meters)
            time.sleep(0.1)

    def _on_disconnect(self):
        self.dev = None
        self.status_lbl.set_text("DEVICE DISCONNECTED - reconnecting...")
        if not self.cap_running:
            self._start_capture()
            self._start_capture2()
            GLib.timeout_add(100, self._update_meters)

    def _update_meters(self):
        for w, idx in self.meter_widgets:
            if self.dev is not None:
                lvl = self._meter_norm(idx)
            else:
                lvl = self._cap_level_norm(idx)
            key = id(w)
            if self.peak_hold:
                self.peak[key] = max(self.peak.get(key, 0.0), lvl)
            else:
                self.peak[key] = lvl
            w.set_fraction(self.peak[key])
            if lvl > 0.3:
                INPUT_NAMES = ["In1","In2","In3","In4","In5","In6","In7","In8",
                               "SpdL","SpdR","??9","??10","??11","??12",
                               "??13","??14","??15","??16",
                               "V1","V2","V3","V4","V5","V6","V7","V8"]
                name = INPUT_NAMES[idx] if idx < len(INPUT_NAMES) else str(idx)
                print("METER ch%d(%s)=%.2f" % (idx, name, lvl), flush=True)

    def _relaunch_welcome(self, *_):
        self._poll_alive = False
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        self._stop_capture()
        self._stop_capture2()
        for c in self.window.get_children():
            self.window.remove(c)
        self._build_welcome()

    def _show_routing(self, *_):
        try:
            with open(
                "/home/stephens-super-linux/Documents/Default Project/routing_schematic.txt"
            ) as f:
                txt = f.read()
        except Exception:
            txt = ""
        dlg = Gtk.Dialog(title="Routing", parent=self.window, flags=0)
        dlg.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        tv = Gtk.TextView()
        tv.get_buffer().set_text(txt)
        tv.set_editable(False)
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.add(tv)
        scroll.set_size_request(540, 380)
        dlg.get_content_area().add(scroll)
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    def _save_preset(self):
        data = {
            "sends": self.sends,
            "mute": [list(k) for k in self.mute_set],
            "solo": [list(k) for k in self.solo_set],
            "sample_rate": self.sample_rate,
            "selected_card_hw": self.selected_card_hw,
            "linked_card_hw": self.linked_card_hw,
            "mix_linked": self.mix_linked,
            "virtuadaw_wires": self.virtuadaw_wires,
            "net_source": self.net_source,
            "net_enabled": self.net_enabled,
            "route_name": self.route_name,
            "route_enabled": self.route_enabled,
            "skip_welcome": self._skip_welcome,
        }
        try:
            with open(PRESET_PATH, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print("save preset failed:", e)

    def _load_preset(self):
        try:
            with open(PRESET_PATH) as f:
                data = json.load(f)
        except Exception:
            data = None
        if data:
            try:
                if "sends" in data:
                    self.sends = data["sends"]
                if "mute" in data:
                    self.mute_set = set(tuple(k) for k in data["mute"])
                if "solo" in data:
                    self.solo_set = set(tuple(k) for k in data["solo"])
                if "sample_rate" in data:
                    self.sample_rate = data["sample_rate"]
                if "selected_card_hw" in data:
                    self.selected_card_hw = data["selected_card_hw"]
                if "linked_card_hw" in data:
                    self.linked_card_hw = data["linked_card_hw"]
                if "mix_linked" in data:
                    self.mix_linked = data["mix_linked"]
                if "virtuadaw_wires" in data:
                    self.virtuadaw_wires = data["virtuadaw_wires"]
                if "net_source" in data:
                    self.net_source = data["net_source"]
                if "net_enabled" in data:
                    self.net_enabled = data["net_enabled"]
                if "route_name" in data:
                    self.route_name = data["route_name"]
                if "route_enabled" in data:
                    self.route_enabled = data["route_enabled"]
                if "skip_welcome" in data:
                    self._skip_welcome = data["skip_welcome"]
                if self.dev is not None:
                    for m in range(4):
                        self._apply_mix(m)
            except Exception as e:
                self._dbg("load_preset apply error: %r" % e)

    def _set_combo_by_hw(self, combo, hw):
        if hw is None:
            combo.set_active(0)
            return
        for i, c in enumerate(self.alsa_cards):
            if c["hw"] == hw:
                combo.set_active(i + 1)
                return
        combo.set_active(0)

    def _on_destroy(self, *_):
        self._save_preset()
        self._stop_capture()
        self._stop_capture2()
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
        Gtk.main_quit()

    def run(self):
        Gtk.main()


def main():
    app = InterfaceApp()
    app.run()


if __name__ == "__main__":
    main()
