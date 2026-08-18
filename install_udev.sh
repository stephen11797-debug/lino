#!/bin/bash
set -e
RULES_SRC="$(dirname "$(readlink -f "$0")")/99-studio-interface.rules"
DEST="/etc/udev/rules.d/99-studio-interface.rules"

cp "$RULES_SRC" "$DEST"
udevadm control --reload-rules
udevadm trigger

echo "udev rule installed: $DEST"
echo "Now replug the Studio 1824c (or it should re-enumerate automatically)."
echo "Then restart the app as your normal user (no root needed) and the faders will be live."
