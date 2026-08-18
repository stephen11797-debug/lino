#!/usr/bin/env python3
"""Open the real Snapchat web app in Google Chrome."""
import os
import shutil
import subprocess

URL = "https://web.snapchat.com/"
PROFILE = os.path.join(os.path.expanduser("~"), ".config", "snapchat-web-chrome")

def main():
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if not chrome:
        print("Google Chrome not found.")
        return
    subprocess.Popen([chrome, "--new-window", URL,
                      "--user-data-dir=" + PROFILE, "--no-first-run"])

if __name__ == "__main__":
    main()
