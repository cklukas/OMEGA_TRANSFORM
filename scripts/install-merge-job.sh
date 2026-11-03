#!/usr/bin/env bash
# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
#
# Install (or reinstall) the daily merge of the Windows benchmark results:
# builds the launcher (scripts/omega-merge-launcher.c) into
# ~/Library/Application Support/OMEGA/omega-merge, signs it ad hoc, and loads
# scripts/com.klukas.omega-merge.plist with launchd.
#
# Once, after the first install: System Settings > Privacy & Security > Full Disk
# Access, "+", choose ~/Library/Application Support/OMEGA/omega-merge (in the file
# dialog, Cmd+Shift+G and paste the path). Rebuilding changes the ad hoc
# signature; macOS may then ask for the permission again.
#
#   scripts/install-merge-job.sh           install and load
#   scripts/install-merge-job.sh --remove  unload and delete the launcher
set -euo pipefail
cd "$(dirname "$0")/.."

LABEL=com.klukas.omega-merge
DIR="$HOME/Library/Application Support/OMEGA"
BIN="$DIR/omega-merge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
domain="gui/$(id -u)"

launchctl bootout "$domain/$LABEL" 2>/dev/null || true
if [ "${1:-}" = --remove ]; then
    rm -f "$PLIST" "$BIN"
    echo "removed $LABEL"
    exit 0
fi

mkdir -p "$DIR"
clang -O2 -Wall -DSCRIPT="\"$PWD/scripts/merge-windows-results.sh\"" \
    -o "$BIN" scripts/omega-merge-launcher.c
codesign --force --sign - --identifier "$LABEL" "$BIN"
sed "s|<string>/bin/bash</string>|<string>$BIN</string>|; \\|merge-windows-results.sh</string>|d" \
    scripts/$LABEL.plist > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$domain" "$PLIST"
echo "installed $LABEL: runs $BIN daily at 07:23; log ~/Library/Logs/omega-merge.log"
echo "run it now with: launchctl kickstart $domain/$LABEL"
