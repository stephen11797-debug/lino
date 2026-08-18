import gi
import math
import array
import json
import os
import subprocess
import threading
import time

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

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
GtkScale { color: #7fb0ff; }
GtkScale trough { background-color: #101016; border-radius: 4px; }
GtkScale trough highlight { background-color: #4a90d9; }
GtkComboBox { background-color: #2b2b37; color: #e6e6ec; }
GtkInfoBar { background-color: #3a2f1a; color: #ffd9a0; }
GtkCheckButton { color: #c9c9d4; }
.ms-btn { padding: 1px 5px; font-size: 9px; font-weight: bold; }
"""


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
        self.sends = [[[0.0] * 2 for _ in range(26)] for _ in range(4)]
        self.mute_set = set()
        self.solo_set = set()
        self.cap_db = [-120.0] * 18
        self.cap_running = False
        self.cap_thread = None
        self.sample_rate = 48000

        try:
            self.dev = ic.Device()
            self.dev_error = None
        except Exception as e:
            self.dev = None
            self.dev_error = str(e)
        self.state.counter = 1

        self._load_css()

        self.window = Gtk.Window(title="Studio Interface Manager")
        self.window.set_border_width(0)
        self.window.connect("destroy", self._on_destroy)
        self.window.set_default_size(900, 600)

        self._build_ui()
        if self.dev is None:
            self._show_dev_banner()
            self._start_capture()
            GLib.timeout_add(100, self._update_meters)
        else:
            self._poll_thread = threading.Thread(
                target=self._poll_loop, daemon=True)
            self._poll_thread.start()
        self._load_preset()

        self._refresh_buttons()
        self._dbg("init complete; strip widgets=%d; dev=%s"
                  % (len(self.strip_area.get_children()), self.dev is not None))

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

        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.props.title = "Studio Interface Manager"
        self.status_lbl = Gtk.Label(
            label="FADERS LIVE" if self.dev else "BUTTONS ONLY (no USB)")
        self.status_lbl.get_style_context().add_class("tiny")
        hb.pack_end(self.status_lbl)
        route = Gtk.Button(label="Routing")
        route.connect("clicked", self._show_routing)
        hb.pack_end(route)
        save = Gtk.Button(label="Save")
        save.connect("clicked", lambda *_: self._save_preset())
        hb.pack_end(save)
        ph = Gtk.CheckButton(label="Peak Hold")
        ph.connect("toggled", self._on_peak_hold)
        hb.pack_end(ph)
        self.window.set_titlebar(hb)

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
        dev_grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dev_grid.set_border_width(8)
        dev_grid.add(Gtk.Label(label="Clock:"))
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
        dev_grid.add(self.clock_combo)
        dev_grid.add(Gtk.Label(label="Sample Rate:"))
        self.rate_combo = Gtk.ComboBoxText()
        for r in (44100, 48000, 96000, 192000):
            self.rate_combo.append_text("%d" % r)
        self.rate_combo.set_active(1)
        self.rate_combo.connect("changed", self._on_rate)
        dev_grid.add(self.rate_combo)
        dev.add(dev_grid)
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

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.strip_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.strip_area.set_border_width(8)
        scroll.add(self.strip_area)
        self.vbox.pack_start(scroll, True, True, 0)

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
        try:
            ic.alsa_set(bid, toggle.get_active())
        except Exception as e:
            print("alsa set failed:", e)
        self._refresh_buttons()

    def _on_peak_hold(self, btn):
        self.peak_hold = btn.get_active()
        if not self.peak_hold:
            self.peak = {}

    def _on_clock(self, combo):
        try:
            cn = ic.alsa_numid(ic.CLOCK_NAME)
            ic.alsa_enum_set(cn, combo.get_active())
        except Exception as e:
            print("clock set failed:", e)

    def _on_rate(self, combo):
        try:
            self.sample_rate = int(combo.get_active_text())
        except (ValueError, TypeError):
            self.sample_rate = 48000
        if self.cap_running:
            self._stop_capture()
            self._start_capture()

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
        return min(1.0, max(0.0, (self.cap_db[cap] + 60) / 66))

    def _start_capture(self):
        if self.cap_running:
            return
        self.cap_running = True
        self.cap_thread = threading.Thread(
            target=self._capture_loop, daemon=True)
        self.cap_thread.start()

    def _stop_capture(self):
        self.cap_running = False

    def _capture_loop(self):
        cmd = ["arecord", "-D", "hw:S1824c,0", "-r", str(self.sample_rate),
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

    def _refresh_buttons(self):
        for bid, _ in BUTTON_DEFS:
            try:
                val = ic.alsa_get(bid)
            except Exception:
                val = None
            t = self.button_toggles.get(bid)
            if t is not None and val is not None and t.get_active() != bool(val):
                t.set_active(bool(val))

    def _poll_loop(self):
        while self.dev is not None:
            try:
                self.dev.poll(self.state)
            except Exception as e:
                print("poll failed:", e)
            GLib.idle_add(self._update_meters)
            time.sleep(0.1)

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
                    try:
                        self.rate_combo.set_active(
                            [44100, 48000, 96000, 192000].index(self.sample_rate))
                    except ValueError:
                        pass
                if self.dev is not None:
                    for m in range(4):
                        self._apply_mix(m)
                self._build_strips(self.current_mix)
            except Exception as e:
                self._dbg("load_preset apply error: %r" % e)
        self._dbg("after load_preset, strip widgets=%d"
                  % len(self.strip_area.get_children()))

    def _on_destroy(self, *_):
        self._save_preset()
        self._stop_capture()
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
