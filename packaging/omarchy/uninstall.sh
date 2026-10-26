#!/usr/bin/env bash
# Remove what install.sh added. Leaves system packages and your settings in
# ~/.config/winduo2 alone; delete that directory too for a clean slate.
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/winduo2"
HYPR="${XDG_CONFIG_HOME:-$HOME/.config}/hypr"

pkill -f "python -m winduo2" 2>/dev/null || true
rm -rf "$DATA"
rm -f "$HOME/.local/bin/winduo2" "${XDG_DATA_HOME:-$HOME/.local/share}/applications/winduo2.desktop"
for file in "$HYPR/autostart.lua" "$HYPR/hyprland.conf"; do
    [ -f "$file" ] || continue
    sed -i '/added by the WinDuo2 installer/d;/winduo2/d' "$file"
done
echo "WinDuo2 removed."
