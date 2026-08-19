#!/usr/bin/env python3
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf

CHROME = "/usr/bin/google-chrome"
DISPLAY = ":0"
MID_X = 2304
MID_W = 1680

APPS = [
    {
        "name": "Snapchat",
        "url": "https://web.snapchat.com/",
        "profile": "/home/stephens-super-linux/.config/snapchat-web-chrome",
        "pos": (MID_X, 0),
        "size": (MID_W, 515),
    },
    {
        "name": "GroupMe",
        "url": "https://web.groupme.com/",
        "profile": "/home/stephens-super-linux/.config/groupme-web-chrome",
        "pos": (MID_X, 520),
        "size": (MID_W, 515),
    },
]


def launch(app):
    def run():
        x, y = app["pos"]
        w, h = app["size"]
        cmd = [
            CHROME,
            f"--app={app['url']}",
            f"--user-data-dir={app['profile']}",
            "--no-first-run",
            f"--window-position={x},{y}",
            f"--window-size={w},{h}",
        ]
        env = dict(__import__("os").environ)
        env["DISPLAY"] = DISPLAY
        subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    threading.Thread(target=run, daemon=True).start()


def make_icon():
    import cairo
    import io

    w, h = 48, 48
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    ctx.set_source_rgb(0.0, 0.63, 1.0)
    ctx.rectangle(2, 2, w - 4, h - 4)
    ctx.fill()
    ctx.set_source_rgb(1.0, 1.0, 1.0)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(20)
    ctx.move_to(9, 33)
    ctx.show_text("S")
    ctx.move_to(25, 33)
    ctx.show_text("G")
    png = io.BytesIO()
    surf.write_to_png(png)
    data = png.getvalue()
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(data)
    loader.close()
    return loader.get_pixbuf()


def on_quit(widget):
    Gtk.main_quit()


def build_menu():
    menu = Gtk.Menu()
    for app in APPS:
        item = Gtk.MenuItem(label=app["name"])
        item.connect("activate", lambda w, a=app: launch(a))
        menu.append(item)
    sep = Gtk.SeparatorMenuItem()
    menu.append(sep)
    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", on_quit)
    menu.append(quit_item)
    menu.show_all()
    return menu


def main():
    if "DISPLAY" not in __import__("os").environ:
        print("No DISPLAY", file=sys.stderr)
        sys.exit(1)
    icon = Gtk.StatusIcon()
    icon.set_from_pixbuf(make_icon())
    icon.set_tooltip_text("Snapchat & GroupMe")
    icon.connect("popup-menu", lambda i, b, t: build_menu().popup(None, None, None, None, b, t))
    icon.connect("activate", lambda i: build_menu().popup(None, None, None, None, 0, Gtk.get_current_event_time()))
    Gtk.main()


if __name__ == "__main__":
    main()
