#!/usr/bin/env python3
"""
Snapchat Tray — opens Snapchat web with scheduler extension.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf, GLib

CHROME = "/usr/bin/google-chrome"
DISPLAY_ENV = ":0"
SERVER = "http://localhost:8001"
HERE = os.path.dirname(os.path.abspath(__file__))
EXT_DIR = os.path.join(HERE, "snapchat-extension")

APP = {
    "name": "Snapchat",
    "url": "https://web.snapchat.com/",
    "profile": os.path.expanduser("~/.config/snapchat-web-chrome"),
    "pos": (1280, 0),
    "size": (1200, 650),
}

_icon = None
pending = []
_counter = 0


def api(path, body=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        SERVER + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def notify(title, body):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Snapchat", "-i", "mail-send", title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _wmctrl(args):
    env = dict(os.environ)
    env["DISPLAY"] = DISPLAY_ENV
    try:
        out = subprocess.check_output(
            ["wmctrl"] + args, env=env, stderr=subprocess.DEVNULL, timeout=3
        )
        return out.decode("utf-8", errors="replace")
    except Exception:
        return ""


def find_snapchat_window():
    out = _wmctrl(["-lG"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 7 and "Snapchat" in " ".join(parts[6:]):
            return parts[0]
    return None


def focus_snapchat():
    wid = find_snapchat_window()
    if wid:
        _wmctrl(["-i", "-a", wid])
        return True
    return False


def turn_off():
    wid = find_snapchat_window()
    if wid:
        _wmctrl(["-i", "-c", wid])
    try:
        subprocess.Popen(
            ["pkill", "-f", APP["profile"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    Gtk.main_quit()


def launch():
    if focus_snapchat():
        return
    def run():
        x, y = APP["pos"]
        w, h = APP["size"]
        cmd = [
            CHROME,
            f"--app={APP['url']}",
            f"--user-data-dir={APP['profile']}",
            "--no-first-run",
            f"--load-extension={EXT_DIR}",
            f"--window-position={x},{y}",
            f"--window-size={w},{h}",
        ]
        env = dict(os.environ)
        env["DISPLAY"] = DISPLAY_ENV
        subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=run, daemon=True).start()


def make_icon():
    icon_path = os.path.join(HERE, "snapchat", "icon.png")
    if os.path.isfile(icon_path):
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 48, 48, True)
    import cairo, io
    w, h = 48, 48
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    ctx.set_source_rgb(1.0, 1.0, 0.0)
    ctx.rectangle(2, 2, w - 4, h - 4)
    ctx.fill()
    ctx.set_source_rgb(0.0, 0.0, 0.0)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(22)
    ctx.move_to(14, 34)
    ctx.show_text("S")
    png = io.BytesIO()
    surf.write_to_png(png)
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png.getvalue())
    loader.close()
    return loader.get_pixbuf()


def update_icon():
    if _icon is None:
        return True
    wid = find_snapchat_window()
    _icon.set_tooltip_text("Snapchat" + (" (running)" if wid else " (click to open)"))
    return True


# ---------------- scheduler window ----------------

class ScheduleWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Schedule Message")
        self.set_default_size(360, 480)
        self.set_resizable(False)
        self.set_border_width(12)
        self.connect("delete-event", lambda w, e: self.hide() or True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(box)

        lbl = Gtk.Label()
        lbl.set_markup("<b>Schedule a Snapchat Message</b>")
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)

        u_box = Gtk.Box(spacing=6)
        box.pack_start(u_box, False, False, 0)
        u_box.pack_start(Gtk.Label(label="From:"), False, False, 0)
        self.user_entry = Gtk.Entry(placeholder_text="Your username")
        self.user_entry.set_hexpand(True)
        self.user_entry.connect("changed", lambda e: self.load_friends())
        u_box.pack_start(self.user_entry, True, True, 0)

        f_box = Gtk.Box(spacing=6)
        box.pack_start(f_box, False, False, 0)
        f_box.pack_start(Gtk.Label(label="To:"), False, False, 0)
        self.friend_combo = Gtk.ComboBoxText()
        self.friend_combo.set_hexpand(True)
        f_box.pack_start(self.friend_combo, True, True, 0)

        box.pack_start(Gtk.Label(label="Message:"), False, False, 0)
        self.msg_entry = Gtk.Entry(placeholder_text="Type your message...")
        self.msg_entry.set_hexpand(True)
        box.pack_start(self.msg_entry, False, False, 0)

        now = datetime.now()
        box.pack_start(Gtk.Label(label="Date:"), False, False, 0)
        self.calendar = Gtk.Calendar()
        self.calendar.select_month(now.month - 1, now.year)
        self.calendar.select_day(now.day)
        box.pack_start(self.calendar, False, False, 0)

        t_box = Gtk.Box(spacing=6)
        box.pack_start(t_box, False, False, 0)
        t_box.pack_start(Gtk.Label(label="Time:"), False, False, 0)
        self.hour_spin = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.hour_spin.set_value(now.hour)
        self.hour_spin.set_width_chars(3)
        t_box.pack_start(self.hour_spin, False, False, 0)
        t_box.pack_start(Gtk.Label(label=":"), False, False, 0)
        self.min_spin = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.min_spin.set_value(now.minute)
        self.min_spin.set_width_chars(3)
        t_box.pack_start(self.min_spin, False, False, 0)

        self.sched_btn = Gtk.Button(label="Schedule")
        self.sched_btn.get_style_context().add_class("suggested-action")
        self.sched_btn.connect("clicked", self.do_schedule)
        box.pack_start(self.sched_btn, False, False, 4)

        self.status = Gtk.Label()
        self.status.set_halign(Gtk.Align.START)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)

        box.pack_start(Gtk.Label(label="<b>Pending:</b>"), False, False, 0)
        self.pending_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add(self.pending_box)
        box.pack_start(scroll, True, True, 0)

    def load_friends(self):
        user = self.user_entry.get_text().strip()
        self.friend_combo.remove_all()
        if not user:
            return
        r = api("/api/friends", {"user": user})
        for f in r.get("friends", []):
            self.friend_combo.append_text(f)

    def do_schedule(self, btn):
        user = self.user_entry.get_text().strip()
        friend = self.friend_combo.get_active_text()
        if friend:
            friend = friend.strip()
        text = self.msg_entry.get_text().strip()

        if not all([user, friend, text]):
            self.status.set_text("Fill in all fields")
            return

        year, month, day = self.calendar.get_date()
        hour = int(self.hour_spin.get_value())
        minute = int(self.min_spin.get_value())
        try:
            dt = datetime(year, month + 1, day, hour, minute)
            send_at = dt.timestamp()
        except ValueError:
            self.status.set_text("Invalid date/time")
            return

        if send_at <= time.time():
            self.status.set_text("Pick a future time")
            return

        global _counter
        _counter += 1
        entry = {"id": str(_counter), "from": user, "to": friend,
                 "text": text, "send_at": send_at}

        delay = send_at - time.time()

        def fire(e=entry):
            r = api("/api/chat/send", {
                "from": e["from"], "to": e["to"],
                "text": e["text"], "ttl": 0,
            })
            if r.get("ok"):
                notify("Message sent!", "To @" + e["to"] + ": " + e["text"][:50])
            else:
                notify("Send failed", str(r.get("error", "unknown")))
            pending[:] = [p for p in pending if p["id"] != e["id"]]
            GLib.idle_add(self.refresh_pending)

        entry["timer"] = threading.Timer(delay, fire)
        entry["timer"].daemon = True
        entry["timer"].start()
        pending.append(entry)

        self.status.set_text("Scheduled for " + dt.strftime("%m/%d %H:%M"))
        self.msg_entry.set_text("")
        self.refresh_pending()

    def refresh_pending(self):
        for child in self.pending_box.get_children():
            self.pending_box.remove(child)
        for s in pending:
            row = Gtk.Box(spacing=6)
            remaining = int(s["send_at"] - time.time())
            if remaining > 60:
                when = "in {}m".format(remaining // 60)
            elif remaining > 0:
                when = "in {}s".format(remaining)
            else:
                when = "sending..."
            lbl = Gtk.Label(
                label="-> @{}: '{}' ({})".format(s["to"], s["text"][:30], when))
            lbl.set_halign(Gtk.Align.START)
            lbl.set_hexpand(True)
            row.pack_start(lbl, True, True, 0)
            cancel = Gtk.Button(label="Cancel")
            cancel.connect("clicked", lambda b, e=s: self.cancel_msg(e))
            row.pack_start(cancel, False, False, 0)
            self.pending_box.pack_start(row, False, False, 0)
        self.pending_box.show_all()

    def cancel_msg(self, entry):
        entry["timer"].cancel()
        pending[:] = [p for p in pending if p["id"] != entry["id"]]
        self.status.set_text("Cancelled")
        self.refresh_pending()


# ---------------- tray ----------------

def build_menu():
    menu = Gtk.Menu()
    wid = find_snapchat_window()
    if wid:
        item = Gtk.MenuItem(label="Snapchat (focus)")
        item.connect("activate", lambda w: focus_snapchat())
    else:
        item = Gtk.MenuItem(label="Open Snapchat")
        item.connect("activate", lambda w: launch())
    menu.append(item)
    sched_item = Gtk.MenuItem(label="Schedule Message")
    sched_item.connect("activate", lambda w: show_scheduler())
    menu.append(sched_item)
    sep = Gtk.SeparatorMenuItem()
    menu.append(sep)
    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda w: Gtk.main_quit())
    menu.append(quit_item)
    menu.show_all()
    return menu


sched_win = None

def show_scheduler():
    global sched_win
    if sched_win is None:
        sched_win = ScheduleWindow()
    sched_win.show_all()
    sched_win.present()


def main():
    global _icon
    if "DISPLAY" not in os.environ:
        print("No DISPLAY", file=sys.stderr)
        sys.exit(1)
    _icon = Gtk.StatusIcon()
    _icon.set_from_pixbuf(make_icon())
    _icon.set_tooltip_text("Snapchat")
    _icon.connect("activate", lambda i: launch())
    _icon.connect("popup-menu", lambda i, b, t: build_menu().popup(
        None, None, None, None, b, t))
    GLib.timeout_add(3000, update_icon)
    Gtk.main()


if __name__ == "__main__":
    main()
