#!/bin/bash
# Lino autostart wrapper: waits for the desktop/tray to be ready, then launches.
sleep 5
LOG=/tmp/lino_autostart.log
echo "[$(date)] starting" >> "$LOG"
exec python3 "/home/stephens-super-linux/Documents/Default Project/voice.py" >> "$LOG" 2>&1
