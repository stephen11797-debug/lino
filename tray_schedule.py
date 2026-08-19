#!/usr/bin/env python3
"""
Snapchat Schedule Tray — schedules timed messages using the
existing Snapchat server API. No server modifications needed.

Run:
    python3 tray_schedule.py            # port 8001
    python3 tray_schedule.py 9000       # custom port
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
from gi.repository import Gtk, GdkPixbuf

SERVER = "http://localhost:8001"
pending = []  # [{id, from, to, text, send_at, timer}]
_counter = 0


def api(path, body=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        SERVER + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def notify(title, body):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Snapchat Schedule", "-i", "mail-send", title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def make_icon():
    import cairo
    import io

    w, h = 48, 48
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    ctx.set_source_rgb(1.0, 0.82, 0.0)
    ctx.rectangle(2, 2, w - 4, h - 4)
    ctx.fill()
    ctx.set_source_rgb(0.0, 0.0, 0.0)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(16)
    ctx.move_to(8, 33)
    ctx.show_text("SC")
    png = io.BytesIO()
    surf.write_to_png(png)
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png.getvalue())
    loader.close()
    return loader.get_pixbuf()


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

        # User
        u_box = Gtk.Box(spacing=6)
        box.pack_start(u_box, False, False, 0)
        u_box.pack_start(Gtk.Label(label="From:"), False, False, 0)
        self.user_entry = Gtk.Entry(placeholder_text="Your username")
        self.user_entry.set_hexpand(True)
        self.user_entry.connect("changed", lambda e: self.load_friends())
        u_box.pack_start(self.user_entry, True, True, 0)

        # Friend dropdown
        f_box = Gtk.Box(spacing=6)
        box.pack_start(f_box, False, False, 0)
        f_box.pack_start(Gtk.Label(label="To:"), False, False, 0)
        self.friend_combo = Gtk.ComboBoxText()
        self.friend_combo.set_hexpand(True)
        f_box.pack_start(self.friend_combo, True, True, 0)

        # Message
        box.pack_start(Gtk.Label(label="Message:"), False, False, 0)
        self.msg_entry = Gtk.Entry(placeholder_text="Type your message...")
        self.msg_entry.set_hexpand(True)
        box.pack_start(self.msg_entry, False, False, 0)

        # Date/Time
        box.pack_start(Gtk.Label(label="Send at:"), False, False, 0)
        self.time_entry = Gtk.Entry()
        self.time_entry.set_text(datetime.now().strftime("%Y-%m-%dT%H:%M"))
        self.time_entry.set_placeholder_text("YYYY-MM-DDTHH:MM")
        box.pack_start(self.time_entry, False, False, 0)

        # Schedule button
        self.sched_btn = Gtk.Button(label="Schedule")
        self.sched_btn.get_style_context().add_class("suggested-action")
        self.sched_btn.connect("clicked", self.do_schedule)
        box.pack_start(self.sched_btn, False, False, 4)

        # Status
        self.status = Gtk.Label()
        self.status.set_halign(Gtk.Align.START)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)

        # Pending list
        box.pack_start(Gtk.Label(label="<b>Pending:</b>"), False, False, 0)
        self.pending_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add(self.pending_box)
        box.pack_start(scroll, True, True, 0)

        self.refresh_pending()

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
        time_str = self.time_entry.get_text().strip()

        if not all([user, friend, text, time_str]):
            self.status.set_text("Fill in all fields")
            return

        try:
            dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")
            send_at = dt.timestamp()
        except ValueError:
            self.status.set_text("Bad date format — use YYYY-MM-DDTHH:MM")
            return

        if send_at <= time.time():
            self.status.set_text("Pick a future time")
            return

        global _counter
        _counter += 1
        entry = {
            "id": str(_counter),
            "from": user,
            "to": friend,
            "text": text,
            "send_at": send_at,
        }

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
            ts = datetime.fromtimestamp(s["send_at"]).strftime("%m/%d %H:%M")
            remaining = int(s["send_at"] - time.time())
            if remaining > 0:
                mins = remaining // 60
                secs = remaining % 60
                when = "in {}m{}s".format(mins, secs)
            else:
                when = "sending..."
            lbl = Gtk.Label(
                label="-> @{}: '{}' ({})".format(s["to"], s["text"][:30], when)
            )
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


from gi.repository import GLib


def main():
    if "DISPLAY" not in os.environ:
        print("No DISPLAY", file=sys.stderr)
        sys.exit(1)

    global SERVER
    args = sys.argv[1:]
    if args:
        try:
            SERVER = "http://localhost:" + str(int(args[0]))
        except ValueError:
            pass

    win = ScheduleWindow()
    win.show_all()

    icon = Gtk.StatusIcon()
    icon.set_from_pixbuf(make_icon())
    icon.set_tooltip_text("Snapchat Schedule")
    icon.connect("activate", lambda i: win.show_all() if not win.get_visible() else None)

    menu = Gtk.Menu()
    open_item = Gtk.MenuItem(label="Open Scheduler")
    open_item.connect("activate", lambda w: win.show_all())
    menu.append(open_item)
    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda w: Gtk.main_quit())
    menu.append(quit_item)
    menu.show_all()

    icon.connect("popup-menu", lambda i, b, t: menu.popup(None, None, None, None, b, t))

    def tick():
        win.refresh_pending()
        return True

    GLib.timeout_add_seconds(5, tick)

    Gtk.main()


if __name__ == "__main__":
    main()
