#!/usr/bin/env python3
"""
ArcoLinux-style window arranger — reads wm.conf and places the
studio windows on the right monitors.

Usage:
    python3 arrange_windows.py [--list] [--retry N]

    (no args)      apply the layout once
    --list         show current monitors and open windows
    --retry N      keep applying every 1s until every rule matches or N tries
"""
import configparser
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "wm.conf")


def sh(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              check=False).stdout.strip()
    except FileNotFoundError:
        return ""


def parse_monitors():
    """xrandr --current -> {name: (x, y, w, h)} in global screen coords.

    Only connected outputs with a live mode are listed, so converter/
    adapter ports with nothing attached (e.g. DP-1, DP-3, DP-5) never
    appear as phantom monitors.
    """
    out = sh("xrandr", "--current")
    monitors = {}
    for line in out.splitlines():
        m = re.match(r"(\S+) connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", line)
        if m:
            name, w, h, x, y = m.groups()
            w, h, x, y = int(w), int(h), int(x), int(y)
            if w > 0 and h > 0:
                monitors[name] = (x, y, w, h)
    return monitors


def list_windows():
    """wmctrl -l -> [(window_id, title)] for normal windows."""
    out = sh("wmctrl", "-l")
    wins = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            wins.append((parts[0], parts[3]))
    return wins


def _px(token, span, start):
    token = token.strip()
    if token.endswith("%"):
        return start + (int(token[:-1]) * span) // 100
    return start + int(token)


def parse_geom(monitor, pos, size):
    """monitor: (x,y,w,h) -> (x, y, w, h) for this rule."""
    mx, my, mw, mh = monitor
    if size == "full":
        w, h = mw, mh
    elif size == "half":
        w, h = mw // 2, mh
    else:
        sep = "," if "," in size else "x"
        w_t, h_t = size.split(sep)
        w = _px(w_t, mw, 0)
        h = _px(h_t, mh, 0)

    if pos == "center":
        x, y = mx + (mw - w) // 2, my + (mh - h) // 2
    elif pos == "top-center":
        x, y = mx + (mw - w) // 2, my
    elif pos == "bottom-center":
        x, y = mx + (mw - w) // 2, my + mh - h
    else:
        x_t, y_t = pos.split(",")
        x = _px(x_t, mw, mx)
        y = _px(y_t, mh, my)
    return x, y, w, h


def move(window_id, x, y, w, h):
    subprocess.run(["wmctrl", "-r", window_id, "-b",
                    "remove", "maximized_vert,maximized_horz"],
                   capture_output=True, check=False)
    subprocess.run(["wmctrl", "-r", window_id, "-e", "0,%d,%d,%d,%d" % (x, y, w, h)],
                   capture_output=True, check=False)


PROJ_RE = re.compile(r"projector|projection|presentation|present", re.IGNORECASE)


def window_geo(wid):
    """wmctrl -lG -> (x, y, w, h) for a window id, or None."""
    out = sh("wmctrl", "-lG")
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) >= 7 and parts[0] == wid:
            try:
                x, y, w, h = (int(parts[2]), int(parts[3]),
                              int(parts[4]), int(parts[5]))
                return x, y, w, h
            except ValueError:
                return None
    return None


def intersects(geo, mon_geo):
    if not geo:
        return False
    x, y, w, h = geo
    mx, my, mw, mh = mon_geo
    return (x < mx + mw and x + w > mx and
            y < my + mh and y + h > my)


def rescue_strays(wins, matched_ids, monitors, fallback_mon, proj_mon):
    """Move windows that are sitting on a 4K/big monitor somewhere useful,
    unless they are projection windows (OBS projector etc.)."""
    big = [g for g in monitors.values() if g[2] >= 2560]  # 4K screens
    fb = monitors.get(fallback_mon)
    if not big or not fb:
        return
    proj = monitors.get(proj_mon) if proj_mon else None
    for wid, title in wins:
        if wid in matched_ids or not title.strip():
            continue
        geo = window_geo(wid)
        if not any(intersects(geo, g) for g in big):
            continue
        if PROJ_RE.search(title) and proj:
            target = proj
            label = proj_mon
        else:
            target = fb
            label = fallback_mon
        x, y, w, h = parse_geom(target, "center", "full")
        move(wid, x, y, w, h)
        print("  %-16s -> %s (moved off 4K)" % (title[:16], label))


def apply_once(conf, monitors, wins):
    placed = {}
    matched_ids = []
    for match, rule in conf["windows"].items():
        parts = [p.strip() for p in rule.split(":")]
        if len(parts) != 3:
            continue
        mon, pos, size = parts
        geo = monitors.get(mon)
        if not geo:
            continue
        x, y, w, h = parse_geom(geo, pos, size)
        done = False
        rx = re.compile(r"\b" + re.escape(match) + r"\b", re.IGNORECASE)
        for wid, title in wins:
            if rx.search(title):
                move(wid, x, y, w, h)
                placed[match] = True
                done = True
                matched_ids.append(wid)
                print("  %-16s -> %s (%dx%d at %d,%d)" % (match, mon, w, h, x, y))
        if not done:
            print("  %-16s -> %s  (no matching window yet)" % (match, mon))
    return placed, matched_ids


def main():
    args = sys.argv[1:]
    show_list = "--list" in args
    retry = 0
    for a in args:
        if a.startswith("--retry"):
            try:
                retry = int(a.split("=")[1]) if "=" in a else int(args[args.index(a) + 1])
            except (IndexError, ValueError):
                retry = 10

    conf = configparser.ConfigParser(interpolation=None)
    if not conf.read(CONF):
        sys.exit("wm.conf not found next to arrange_windows.py")

    monitors = parse_monitors()
    wins = list_windows()

    if show_list:
        print("MONITORS")
        for name, (x, y, w, h) in sorted(monitors.items(), key=lambda kv: kv[1][0]):
            print("  %-10s %4dx%-4d at %d,%d%s" % (
                name, w, h, x, y, "   <-- primary" if x == 0 and y == 0 else ""))
        print("\nWINDOWS")
        for wid, title in sorted(wins, key=lambda t: t[1].lower()):
            print("  %-12s %s" % (wid, title))
        return

    defaults = conf["defaults"] if conf.has_section("defaults") else {}
    fallback_mon = (defaults.get("monitor") or "").strip()
    proj_mon = (defaults.get("projection-monitor") or "").strip()

    rules = list(conf["windows"].keys())
    tries = max(retry, 1)
    for i in range(tries):
        if retry:
            print("apply attempt %d/%d" % (i + 1, tries))
        else:
            print("applying %s" % CONF)
        placed, matched_ids = apply_once(conf, monitors, wins)
        if fallback_mon:
            rescue_strays(wins, matched_ids, monitors, fallback_mon, proj_mon)
        if len(placed) == len(rules):
            print("all windows placed")
            return
        if retry:
            time.sleep(1)
            wins = list_windows()


if __name__ == "__main__":
    main()
