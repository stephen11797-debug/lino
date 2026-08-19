#!/usr/bin/env python3
"""
╔══════════════════════════════════════════╗
║     LINO COCKPIT - GUI INSTALLER         ║
║   © 2026 Stephen's Studio               ║
╚══════════════════════════════════════════╝
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango
import subprocess
import threading
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

class LinoInstaller(Gtk.Window):
    def __init__(self):
        super().__init__(title="Lino Cockpit")
        self.set_default_size(520, 650)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.installing = False
        self.selected = {"system": True, "python": True, "vosk": True, "ollama": True}

        css = """
        window { background: linear-gradient(180deg, #0a1628 0%, #0d2137 50%, #0a1628 100%); }
        .header-box { background: transparent; }
        .title { color: #00e5ff; font-size: 28px; font-weight: bold; font-family: monospace; }
        .subtitle { color: rgba(0,229,255,0.6); font-size: 12px; font-family: monospace; }
        .desc { color: rgba(255,255,255,0.7); font-size: 12px; }
        .opt-btn {
            background-image: none;
            background-color: rgba(0,229,255,0.1);
            color: #00e5ff;
            font-size: 13px;
            padding: 10px 16px;
            border-radius: 8px;
            border: 1px solid rgba(0,229,255,0.3);
            font-family: monospace;
        }
        .opt-btn:hover { background-color: rgba(0,229,255,0.2); }
        .opt-btn.active {
            background-color: rgba(0,229,255,0.25);
            border: 1px solid #00e5ff;
        }
        .install-btn {
            background-image: none;
            background-color: #00e5ff;
            color: #0a1628;
            font-size: 16px;
            font-weight: bold;
            padding: 14px 40px;
            border-radius: 25px;
            border: none;
            font-family: monospace;
        }
        .install-btn:hover { background-color: #33ebff; }
        .install-btn:disabled { background-color: rgba(255,255,255,0.1); color: rgba(255,255,255,0.3); }
        .progress { trough-color: rgba(0,229,255,0.15); progress-color: #00e5ff; min-height: 16px; border-radius: 8px; }
        .status { color: #00e5ff; font-size: 12px; font-family: monospace; }
        .step { color: rgba(255,255,255,0.4); font-size: 11px; font-family: monospace; }
        .done { color: #00e5ff; font-size: 20px; font-weight: bold; font-family: monospace; }
        .copyright { color: rgba(255,255,255,0.3); font-size: 10px; font-family: monospace; }
        .scanline {
            background-image: linear-gradient(transparent 50%, rgba(0,0,0,0.05) 50%);
            background-size: 100% 4px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main)

        # Scanline overlay effect
        overlay_box = Gtk.Box()
        overlay_box.get_style_context().add_class("scanline")
        main.pack_start(overlay_box, True, True, 0)

        # Top accent line
        top_line = Gtk.DrawingArea()
        top_line.set_size_request(520, 3)
        def draw_top(w, cr):
            cr.set_source_rgb(0, 0.9, 1)
            cr.rectangle(0, 0, 520, 3)
            cr.fill()
        top_line.connect("draw", draw_top)
        main.pack_start(top_line, False, False, 0)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header.set_margin_top(20)
        header.set_margin_bottom(15)
        header.set_halign(Gtk.Align.CENTER)
        overlay_box.pack_start(header, False, False, 0)

        # Icon
        icon_path = os.path.join(SCRIPT_DIR, "lino-penguin.png")
        if os.path.exists(icon_path):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 90, 90)
                icon = Gtk.Image.new_from_pixbuf(pb)
                header.pack_start(icon, False, False, 0)
            except: pass

        # Studio tag
        studio = Gtk.Label()
        studio.set_markup('<span foreground="rgba(0,229,255,0.4)" font_family="monospace" size="small">★ Stephen\'s Studio ★</span>')
        header.pack_start(studio, False, False, 0)

        # Title
        title = Gtk.Label()
        title.set_markup('<span foreground="#00e5ff" font_family="monospace" size="x-large" weight="bold">L I N O</span>')
        header.pack_start(title, False, False, 0)

        subtitle = Gtk.Label()
        subtitle.set_markup('<span foreground="rgba(0,229,255,0.5)" font_family="monospace" size="small">Offline Voice Assistant for Linux</span>')
        header.pack_start(subtitle, False, False, 0)

        desc = Gtk.Label()
        desc.set_markup('<span foreground="rgba(255,255,255,0.6)" font_family="monospace" size="x-small">Voice control · AI chat · Screen vision · Neural TTS</span>')
        desc.set_line_wrap(True)
        header.pack_start(desc, False, False, 0)

        # Options grid
        opts = Gtk.Grid()
        opts.set_column_spacing(10)
        opts.set_row_spacing(8)
        opts.set_halign(Gtk.Align.CENTER)
        opts.set_margin_top(10)
        opts.set_margin_bottom(15)
        overlay_box.pack_start(opts, False, False, 0)

        self.opt_btns = {}
        options = [
            ("system", "System Pkgs"),
            ("python", "Python Pkgs"),
            ("vosk", "Vosk Model"),
            ("ollama", "Ollama AI"),
        ]
        for i, (key, label) in enumerate(options):
            btn = Gtk.Button(label=f"  {label}")
            btn.get_style_context().add_class("opt-btn")
            btn.connect("clicked", self.toggle_option, key)
            opts.attach(btn, i % 2, i // 2, 1, 1)
            self.opt_btns[key] = btn
            self.update_btn_style(key)

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_start(40)
        sep.set_margin_end(40)
        sep.set_margin_bottom(10)
        overlay_box.pack_start(sep, False, False, 0)

        # Progress
        prog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        prog_box.set_margin_start(40)
        prog_box.set_margin_end(40)
        overlay_box.pack_start(prog_box, False, False, 0)

        self.progress = Gtk.ProgressBar()
        self.progress.get_style_context().add_class("progress")
        prog_box.pack_start(self.progress, False, False, 0)

        self.status = Gtk.Label(label="Ready")
        self.status.get_style_context().add_class("status")
        self.status.set_halign(Gtk.Align.CENTER)
        prog_box.pack_start(self.status, False, False, 0)

        self.step_lbl = Gtk.Label()
        self.step_lbl.get_style_context().add_class("step")
        self.step_lbl.set_halign(Gtk.Align.CENTER)
        prog_box.pack_start(self.step_lbl, False, False, 0)

        # Install button
        self.install_btn = Gtk.Button(label="INSTALL")
        self.install_btn.get_style_context().add_class("install-btn")
        self.install_btn.set_margin_top(15)
        self.install_btn.set_margin_start(140)
        self.install_btn.set_margin_end(140)
        self.install_btn.connect("clicked", self.start_install)
        overlay_box.pack_start(self.install_btn, False, False, 0)

        # Done label
        self.done_lbl = Gtk.Label()
        self.done_lbl.get_style_context().add_class("done")
        self.done_lbl.set_markup('<span foreground="#00e5ff" font_family="monospace" size="large" weight="bold">✔ INSTALLATION COMPLETE</span>')
        self.done_lbl.set_halign(Gtk.Align.CENTER)
        self.done_lbl.set_no_show_all(True)
        overlay_box.pack_start(self.done_lbl, False, False, 10)

        # Copyright
        cr = Gtk.Label()
        cr.set_markup('<span foreground="rgba(255,255,255,0.25)" font_family="monospace" size="xx-small">© 2026 Stephen\'s Studio</span>')
        cr.set_halign(Gtk.Align.CENTER)
        cr.set_margin_bottom(8)
        main.pack_end(cr, False, False, 0)

    def toggle_option(self, btn, key):
        self.selected[key] = not self.selected[key]
        self.update_btn_style(key)

    def update_btn_style(self, key):
        btn = self.opt_btns[key]
        classes = btn.get_style_context().get_classes()
        if self.selected[key]:
            if "active" not in classes:
                btn.get_style_context().add_class("active")
            btn.set_label(f"✓ {btn.get_label().replace('✓ ', '').replace('  ', '')}")
        else:
            btn.get_style_context().remove_class("active")
            btn.set_label(f"  {btn.get_label().replace('✓ ', '')}")

    def start_install(self, *args):
        if self.installing:
            return
        self.installing = True
        self.install_btn.set_sensitive(False)
        self.install_btn.set_label("Installing...")
        for btn in self.opt_btns.values():
            btn.set_sensitive(False)
        self.done_lbl.hide()
        threading.Thread(target=self.do_install, daemon=True).start()

    def do_install(self):
        steps = []
        if self.selected["system"]:
            steps.append(("Installing system packages...", 
                "sudo apt-get update -qq && sudo apt-get install -y -qq "
                "python3 python3-pip python3-venv python3-gi python3-cairo "
                "gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-pango-1.0 "
                "libcairo2 libxtst6 wmctrl x11-utils pulseaudio alsa-utils "
                "ffmpeg xclip xdotool"))
        if self.selected["python"]:
            steps.append(("Installing Python packages...",
                f"cd {SCRIPT_DIR} && pip3 install --user --break-system-packages -r requirements.txt || "
                f"pip3 install --break-system-packages -r requirements.txt || true"))
        if self.selected["vosk"]:
            if not os.path.exists(os.path.join(SCRIPT_DIR, "model")):
                steps.append(("Downloading Vosk speech model (~40MB)...",
                    "wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip "
                    "-O /tmp/vosk-model.zip && unzip -q /tmp/vosk-model.zip -d /tmp/ && "
                    "mv /tmp/vosk-model-small-en-us-0.15 " + SCRIPT_DIR + "/model && rm /tmp/vosk-model.zip"))
            else:
                steps.append(("Vosk model already exists", "true"))
        if self.selected["ollama"]:
            steps.append(("Checking Ollama...",
                "which ollama && ollama pull qwen2.5:7b && ollama pull qwen2.5vl:7b || true"))

        total = len(steps)
        if total == 0:
            GLib.idle_add(self.finish)
            return

        for i, (label, cmd) in enumerate(steps):
            GLib.idle_add(self.status.set_text, label)
            GLib.idle_add(self.step_lbl.set_text, f"[{i+1}/{total}]")
            frac = (i) / total
            GLib.idle_add(self.progress.set_fraction, frac)
            try:
                subprocess.run(cmd, shell=True, capture_output=True, timeout=600)
            except:
                pass

        GLib.idle_add(self.progress.set_fraction, 1.0)
        GLib.idle_add(self.finish)

    def finish(self):
        self.status.set_text("All done!")
        self.step_lbl.set_text("")
        self.install_btn.set_label("INSTALL")
        self.done_lbl.set_no_show_all(False)
        self.done_lbl.show_all()
        self.installing = False
        for btn in self.opt_btns.values():
            btn.set_sensitive(True)
        self.install_btn.set_sensitive(True)

if __name__ == "__main__":
    win = LinoInstaller()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
