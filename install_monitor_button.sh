#!/usr/bin/env bash
# Add/refresh the "Monitor Config" button on the right side of the MATE panel.
set -e

DESKTOP="$HOME/.local/share/applications/monitor-config.desktop"
ID="monitor-config"

if [ ! -f "$DESKTOP" ]; then
    echo "missing $DESKTOP" >&2
    exit 1
fi

# 1) make sure the object id is in the panel's object list
LIST=$(dconf read /org/mate/panel/general/object-id-list)
if ! echo "$LIST" | grep -q "'$ID'"; then
    NEW=$(echo "$LIST" | sed "s/\]/,\ '$ID']/")
    dconf write /org/mate/panel/general/object-id-list "$NEW"
fi

# 2) configure the launcher object (right-stick, like gif-control)
dconf write "/org/mate/panel/objects/$ID/launcher-location" "'file://$DESKTOP'"
dconf write "/org/mate/panel/objects/$ID/locked" "true"
dconf write "/org/mate/panel/objects/$ID/object-type" "'launcher'"
dconf write "/org/mate/panel/objects/$ID/panel-right-stick" "true"
dconf write "/org/mate/panel/objects/$ID/position" "0"
dconf write "/org/mate/panel/objects/$ID/relative-to-edge" "'end'"
dconf write "/org/mate/panel/objects/$ID/toplevel-id" "'bottom'"

echo "Monitor Config button added to the right side of the panel."
