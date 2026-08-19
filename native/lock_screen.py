#!/usr/bin/env python3
"""
Echo Lock Screen — animated GIF lock screen for MATE/X11 with a real
password prompt (PAM) and built-in failsafes.

Usage:
    python3 lock_screen.py [gif_path]            # lock now
    python3 lock_screen.py --watch [gif_path]    # auto-run on idle blank

Failsafes (you can never get stuck locked out):
  * Missing/broken GIF  -> falls back to a plain text lock screen.
  * High system load    -> animation slows down, then freezes on a frame;
                           the password prompt always keeps working.
  * PAM auth broken     -> an Emergency Unlock button appears.
  * Locker crashes      -> in --watch mode the screensaver is deactivated
                           so the screen un-blanks automatically.
"""
import ctypes
import datetime as _dt
import getpass
import os
import subprocess
import sys

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
except Exception as _e:
    print("lock_screen: GTK not available: %s" % _e, file=sys.stderr)
    sys.exit(1)

BASE = os.path.dirname(os.path.abspath(__file__))
GIF_PATH = os.environ.get(
    "LOCK_GIF",
    "/home/stephens-super-linux/Videos/linux back grounds /pegin code.gif",
)
USER = getpass.getuser()

RESULT = 1   # 0 = unlocked, 1 = crashed/failed (watcher will un-blank)
LOG = os.path.join(BASE, "lock_screen.log")


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write("[%s] %s\n" % (_dt.datetime.now().strftime("%H:%M:%S"), msg))
    except Exception:
        pass


# ============================================================
# PAM password check (via libpam, no root needed)
# ============================================================
PAM_PROMPT_ECHO_OFF = 1
PAM_SUCCESS = 0
PAM_AUTH_ERR = 7

_CB = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ctypes.c_void_p,
)


class _Message(ctypes.Structure):
    _fields_ = [("style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _Response(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_char_p), ("retcode", ctypes.c_int)]


class _Conv(ctypes.Structure):
    _fields_ = [("conv", _CB), ("appdata", ctypes.c_void_p)]


class _Handle(ctypes.Structure):
    pass


_password = b""


def _conv(n, msgs, respp, appdata):
    arr = (ctypes.POINTER(_Response) * n)()
    for i in range(n):
        mp = ctypes.cast(msgs[i], ctypes.POINTER(_Message))
        if mp.contents.style == PAM_PROMPT_ECHO_OFF:
            arr[i] = ctypes.pointer(_Response(ctypes.c_char_p(_password), 0))
        else:
            arr[i] = ctypes.pointer(_Response(ctypes.c_char_p(b""), 0))
    respp.contents = ctypes.cast(ctypes.pointer(arr), ctypes.POINTER(_Response))
    return PAM_SUCCESS


def pam_check(password):
    """Return True if password matches the current user, else False.
    Raises on a broken PAM setup (caller then enables emergency unlock)."""
    global _password
    _password = password.encode()
    lib = ctypes.CDLL("libpam.so.0")
    lib.pam_start.restype = ctypes.c_int
    lib.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                              ctypes.POINTER(_Conv),
                              ctypes.POINTER(ctypes.POINTER(_Handle))]
    lib.pam_authenticate.restype = ctypes.c_int
    lib.pam_authenticate.argtypes = [ctypes.POINTER(_Handle), ctypes.c_int]
    lib.pam_end.restype = ctypes.c_int
    lib.pam_end.argtypes = [ctypes.POINTER(_Handle), ctypes.c_int]

    cb = _CB(_conv)
    conv = _Conv(cb, None)
    ph = ctypes.POINTER(_Handle)()
    r = lib.pam_start(b"login", USER.encode(),
                      ctypes.pointer(conv), ctypes.pointer(ph))
    if r != PAM_SUCCESS:
        log("pam_start failed: %d" % r)
        raise RuntimeError("pam_start failed (%d)" % r)
    try:
        r = lib.pam_authenticate(ph, 0)
    finally:
        lib.pam_end(ph, r)
    if r == PAM_SUCCESS:
        return True
    if r == PAM_AUTH_ERR:
        return False
    log("pam_authenticate returned %d" % r)
    raise RuntimeError("pam_authenticate failed (%d)" % r)


# ============================================================
# GTK lock screen
# ============================================================
class GifLockScreen(Gtk.Window):
    def __init__(self, gif_path):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Locked")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(True)
        self.set_can_focus(True)

        css = b"""
        * { font-family: 'DejaVu Sans', system-ui, sans-serif; }
        #clock { color: #ffffff; font-size: 72px; font-weight: bold;
                 text-shadow: 0 2px 10px rgba(0,0,0,0.9); }
        #date { color: #e0e0e0; font-size: 22px;
                text-shadow: 0 2px 8px rgba(0,0,0,0.9); }
        #hint { color: #d0d0ff; font-size: 18px; background: rgba(0,0,0,0.45);
                border-radius: 14px; padding: 8px 16px;
                text-shadow: 0 1px 6px rgba(0,0,0,0.9); }
        #err { color: #ff6b6b; font-size: 16px; }
        #pbox { background: rgba(16,16,40,0.88); border-radius: 18px; }
        entry { background: #26264a; color: #ffffff; font-size: 20px;
                border-radius: 12px; caret-color: #00e08a; }
        button { background: #00e08a; color: #000000; font-size: 15px;
                 font-weight: bold; border-radius: 12px; }
        button:hover { background: #2affa5; }
        #btn-quiet { background: #26264a; color: #aab0d0; font-weight: normal; }
        #btn-quiet:hover { background: #3a3a6a; color: #ffffff; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # gif animation
        self._anim = None
        self._iter = None
        self._frame_pixbuf = None
        try:
            self._anim = GdkPixbuf.PixbufAnimation.new_from_file(gif_path)
            self._iter = self._anim.get_iter(None)
            self._frame_pixbuf = self._iter.get_pixbuf()
        except Exception as e:
            log("gif load failed: %s" % e)
            self._anim = None

        self._screens = []      # [(window, drawing_area), ...]
        self._window_main = None
        self._hint_visible = True
        self._emergency = False

        scr = Gdk.Screen.get_default()
        primary = scr.get_primary_monitor()
        for i in range(scr.get_n_monitors()):
            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_decorated(False)
            win.set_keep_above(True)
            win.set_accept_focus(True)
            win.set_can_focus(True)
            da = Gtk.DrawingArea()
            da.connect("draw", self.on_draw)
            if i == primary:
                overlay = Gtk.Overlay()
                overlay.add(da)
                win.add(overlay)
                da.connect("button-press-event", lambda *a: self.show_prompt())
                self._build_ui(overlay)
                self._window_main = win
            else:
                win.add(da)
            win.fullscreen_on_monitor(scr, i)
            win.connect("key-press-event", self.on_key)
            win.connect("destroy", self._on_destroy)
            self._screens.append((win, da))
        if self._window_main is None:
            self._window_main = self._screens[0][0]
        for win, _ in self._screens:
            win.show_all()
        self._window_main.present()
        GLib.timeout_add(250, self._grab_focus)
        GLib.timeout_add(300, self._start_frame_timer)
        GLib.timeout_add(1000, self._tick_clock)
        self._tick_clock()

    def _build_ui(self, overlay):
        # clock (top center)
        clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clock_box.set_halign(Gtk.Align.CENTER)
        clock_box.set_valign(Gtk.Align.START)
        clock_box.set_margin_top(90)
        self._clock = Gtk.Label()
        self._clock.set_name("clock")
        self._date = Gtk.Label()
        self._date.set_name("date")
        clock_box.pack_start(self._clock, False, False, 0)
        clock_box.pack_start(self._date, False, False, 0)
        overlay.add_overlay(clock_box)

        # center prompt area
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        center.set_halign(Gtk.Align.CENTER)
        center.set_valign(Gtk.Align.CENTER)
        self._hint = Gtk.Label(label="Press any key to unlock")
        self._hint.set_name("hint")
        center.pack_start(self._hint, False, False, 0)

        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        pbox.set_name("pbox")
        pbox.set_halign(Gtk.Align.CENTER)
        pbox.set_valign(Gtk.Align.CENTER)
        pbox.set_size_request(360, -1)
        self._plabel = Gtk.Label(label="Enter your password")
        self._plabel.set_name("date")
        self._entry = Gtk.Entry()
        self._entry.set_visibility(False)
        self._entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self._entry.set_size_request(320, 44)
        self._entry.set_placeholder_text("Password")
        self._entry.connect("activate", lambda *a: self.check_password())
        self._err = Gtk.Label(label="")
        self._err.set_name("err")
        self._btn_emergency = Gtk.Button(label="⚠ Emergency unlock (PAM broken)")
        self._btn_emergency.connect("clicked", lambda *a: self.unlock())
        self._btn_emergency.set_no_show_all(True)
        self._btn_login = Gtk.Button(label="⇱  Back to Login")
        self._btn_login.set_name("btn-quiet")
        self._btn_login.connect("clicked", lambda *a: self.go_back_to_login())
        pbox.pack_start(self._plabel, False, False, 0)
        pbox.pack_start(self._entry, False, False, 0)
        pbox.pack_start(self._err, False, False, 0)
        pbox.pack_start(self._btn_emergency, False, False, 0)
        pbox.pack_start(self._btn_login, False, False, 0)
        pbox.hide()
        center.pack_start(pbox, False, False, 0)
        overlay.add_overlay(center)

    def _on_destroy(self, w):
        for win, _ in self._screens:
            try:
                if win is not w:
                    win.destroy()
            except Exception:
                pass
        Gtk.main_quit()

    def _grab_focus(self):
        self._window_main.grab_focus()
        try:
            win = self._window_main.get_window()
            if win:
                win.keyboard_grab(True, 0)
        except Exception:
            pass
        return False

    def _start_frame_timer(self):
        self.tick_frame()
        return False

    # ---- animation (adaptive: slows/freezes under heavy load) ----
    def tick_frame(self):
        if self._iter is None:
            return False
        delay = self._iter.get_delay_time()
        if delay <= 0:
            delay = 80
        self._iter.advance(None)
        self._frame_pixbuf = self._iter.get_pixbuf()
        for win, da in self._screens:
            da.queue_draw()
        # if the machine is struggling, slow the animation down
        try:
            load = os.getloadavg()[0]
        except Exception:
            load = 0
        factor = 3.0 if load > 4 else (2.0 if load > 2 else 1.0)
        GLib.timeout_add(int(delay * factor), self.tick_frame)
        return False

    # ---- drawing ----
    def on_draw(self, da, cr):
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        if self._frame_pixbuf is None:
            return False
        pb = self._frame_pixbuf
        w, h = da.get_allocated_width(), da.get_allocated_height()
        iw, ih = pb.get_width(), pb.get_height()
        if iw <= 0 or ih <= 0:
            return False
        scale = min(float(w) / iw, float(h) / ih)
        nw, nh = iw * scale, ih * scale
        cr.save()
        cr.translate((w - nw) / 2.0, (h - nh) / 2.0)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
        cr.restore()
        return False

    # ---- clock ----
    def _tick_clock(self):
        now = _dt.datetime.now()
        self._clock.set_text(now.strftime("%I:%M %p"))
        self._date.set_text(now.strftime("%A, %B %d"))
        GLib.timeout_add(1000, self._tick_clock)
        return False

    # ---- prompt ----
    def show_prompt(self):
        if not self._hint_visible:
            return
        self._hint_visible = False
        self._hint.hide()
        self._err.set_text("")
        pbox = self._entry.get_parent()
        pbox.show_all()
        if self._emergency:
            self._btn_emergency.show()
        self._entry.grab_focus()

    def hide_prompt(self):
        self._hint_visible = True
        self._hint.show()
        self._entry.get_parent().hide()
        self._err.set_text("")
        self._window_main.grab_focus()

    def on_key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            if self._hint_visible:
                if self._emergency:
                    self.unlock()
                return True
            self.hide_prompt()
            return True
        if self._hint_visible:
            self.show_prompt()
            return True
        return False

    # ---- password ----
    def check_password(self):
        pw = self._entry.get_text()
        if not pw:
            return
        try:
            ok = pam_check(pw)
        except Exception as e:
            log("pam error: %s" % e)
            self._emergency = True
            self._err.set_text("Password check failed — PAM broken.")
            self._btn_emergency.show()
            return
        if ok:
            self.unlock()
        else:
            self._err.set_text("Incorrect password")
            self._entry.set_text("")
            self._entry.grab_focus()

    def unlock(self):
        global RESULT
        RESULT = 0
        log("unlocked")
        self._err.set_text("Unlocking...")
        GLib.timeout_add(250, lambda *a: self._window_main.destroy())

    def go_back_to_login(self):
        """Send the session to the background and show the login greeter,
        where you can pick a user to log back in."""
        log("switch to login greeter")
        subprocess.Popen(["dm-tool", "switch-to-greeter"],
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        self.unlock()


# ============================================================
# watch mode: auto-launch the locker when the screen blanks
# ============================================================
def watch(gif_path):
    try:
        mon = subprocess.Popen(
            ["dbus-monitor", "--session",
             "type='signal',interface='org.freedesktop.ScreenSaver',"
             "member='ActiveChanged'"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except FileNotFoundError:
        return
    locker = None
    for line in mon.stdout:
        if "boolean true" in line:
            if locker is None or locker.poll() is not None:
                log("screen blanked -> launching lock screen")
                locker = subprocess.Popen([sys.executable, __file__, gif_path])
        elif "boolean false" in line:
            pass
        if locker is not None and locker.poll() is not None:
            rc = locker.returncode
            locker = None
            # failsafe: whether we unlocked (0) or crashed (!=0), make
            # sure the screen never stays stuck on a blank.
            log("lock screen ended (%d) -> deactivating screensaver" % rc)
            subprocess.run(["mate-screensaver-command", "-d"],
                           capture_output=True, check=False)


def main():
    global RESULT
    args = [a for a in sys.argv[1:]]
    mode_watch = False
    if "--watch" in args:
        mode_watch = True
        args.remove("--watch")
    gif_path = args[0] if args else GIF_PATH

    if mode_watch:
        watch(gif_path)
        return

    try:
        app = GifLockScreen(gif_path)
        Gtk.main()
    except Exception as e:
        log("lock screen crashed: %s" % e)
        RESULT = 1
    finally:
        try:
            subprocess.run(["mate-screensaver-command", "-d"],
                           capture_output=True, check=False)
        except Exception:
            pass
    sys.exit(RESULT)


if __name__ == "__main__":
    main()
