#!/usr/bin/env python3
"""
Launch both apps (SnapChat + GroupMe) from one place.

Usage:
    python3 run_apps.py [snapchat_port] [groupme_port] [--arrange]

--arrange  also applies wm.conf to put each app window on the right monitor.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))


def arrange_windows(retry=10):
    """Apply wm.conf layout (ArcoLinux-style) to the studio windows."""
    script = os.path.join(HERE, "arrange_windows.py")
    if os.path.exists(script):
        subprocess.run([sys.executable, script, "--retry", str(retry)])


def open_in_chrome(url, profile):
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome:
        udd = os.path.join(os.path.expanduser("~"), ".config", profile + "-chrome")
        subprocess.Popen([chrome, "--app=" + url,
                          "--user-data-dir=" + udd,
                          "--no-first-run"])
    else:
        webbrowser.open(url)

def _run_one(app, port):
    try:
        p = subprocess.Popen([sys.executable, os.path.join(HERE, app, "server.py"), port])
        time.sleep(1.5)
        url = "http://localhost:{0}".format(port)
        print("{0} : {1}".format(app, url))
        print("Press Ctrl+C to stop.")
        open_in_chrome(url, app)
        p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=3)
        except Exception:
            pass
        print("\nStopped.")


def main():
    args = sys.argv[1:]
    if args and args[0] in ("snapchat", "groupme"):
        app = args[0]
        port = args[1] if len(args) > 1 else ("8001" if app == "snapchat" else "8002")
        _run_one(app, port)
        return

    sc_port = args[0] if args else "8001"
    gm_port = args[1] if len(args) > 1 else "8002"
    do_arrange = "--arrange" in args

    procs = []
    try:
        sc = subprocess.Popen([sys.executable, os.path.join(HERE, "snapchat", "server.py"), sc_port])
        gm = subprocess.Popen([sys.executable, os.path.join(HERE, "groupme", "server.py"), gm_port])
        procs = [sc, gm]

        time.sleep(1.5)
        print("SnapChat : http://localhost:{0}".format(sc_port))
        print("GroupMe  : http://localhost:{0}".format(gm_port))
        print("Press Ctrl+C to stop both apps.")

        open_in_chrome("http://localhost:{0}".format(sc_port), "snapchat")
        open_in_chrome("http://localhost:{0}".format(gm_port), "groupme")

        if do_arrange:
            print("Arranging windows from wm.conf ...")
            arrange_windows()

        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            try:
                p.send_signal(signal.SIGINT)
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print("\nApps stopped.")

if __name__ == "__main__":
    main()
