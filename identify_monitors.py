#!/usr/bin/env python3
"""Show a big labeled window on every connected monitor so you can tell
which physical screen is which port."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

from arrange_windows import parse_monitors


def main():
    monitors = parse_monitors()
    if not monitors:
        print("no monitors found")
        return
    wins = []
    for name, (x, y, w, h) in monitors.items():
        win = Gtk.Window(title="Identify monitor")
        win.set_decorated(False)
        win.set_default_size(int(w * 0.7), int(h * 0.7))
        win.move(int(x + w * 0.15), int(y + h * 0.15))
        lbl = Gtk.Label()
        lbl.set_markup(
            "<span size='40000' weight='bold'>%s</span>\n"
            "<span size='20000'>%dx%d @ %d,%d</span>" % (name, w, h, x, y)
        )
        lbl.set_halign(Gtk.Align.CENTER)
        lbl.set_valign(Gtk.Align.CENTER)
        lbl.modify_bg(Gtk.StateType.NORMAL, Gdk.color_parse("#101020"))
        lbl.modify_fg(Gtk.StateType.NORMAL, Gdk.color_parse("#00e08a"))
        win.add(lbl)
        win.show_all()
        wins.append(win)
        print("showing on %s (%dx%d)" % (name, w, h))
    print("close all windows or press Ctrl+C to quit")
    Gtk.main()


if __name__ == "__main__":
    main()
