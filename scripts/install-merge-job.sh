#!/usr/bin/env bash
# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
#
# Install (or reinstall) the daily merge of another machine's benchmark results
# on macOS: builds the launcher (scripts/omega-merge-launcher.c) into
# ~/Library/Application Support/OMEGA/omega-merge, signs it ad hoc, fills in
# scripts/com.klukas.omega-merge.plist and loads it with launchd. The merge's
# settings belong in ~/.config/omega-merge.env (see scripts/merge-results.sh).
#
# When the repository is on an external volume, macOS may ask once whether the
# launcher may access it; otherwise grant it under System Settings > Privacy &
# Security > Full Disk Access. A rebuild changes the ad hoc signature, so macOS
# may ask again.
#
#   scripts/install-merge-job.sh [HH:MM]   install and load (default 07:23)
#   scripts/install-merge-job.sh --remove  unload and delete the launcher
set -euo pipefail
cd "$(dirname "$0")/.."

LABEL=com.klukas.omega-merge
DIR="$HOME/Library/Application Support/OMEGA"
BIN="$DIR/omega-merge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/omega-merge.log"
domain="gui/$(id -u)"

launchctl bootout "$domain/$LABEL" 2>/dev/null || true
if [ "${1:-}" = --remove ]; then
    rm -f "$PLIST" "$BIN"
    echo "removed $LABEL"
    exit 0
fi
time=${1:-07:23}
hour=$((10#${time%%:*}))
minute=$((10#${time##*:}))

[ -f "${OMEGA_MERGE_CONFIG:-$HOME/.config/omega-merge.env}" ] \
    || echo "note: no ~/.config/omega-merge.env yet; the job fails until it names the host and folder"

# The job's PATH: where python3 and ckdocs are now, plus the system folders.
path="$(dirname "$(command -v python3)"):$(dirname "$(command -v ckdocs)"):/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$DIR" "$(dirname "$PLIST")"
clang -O2 -Wall -DSCRIPT="\"$PWD/scripts/merge-results.sh\"" -o "$BIN" scripts/omega-merge-launcher.c
codesign --force --sign - --identifier "$LABEL" "$BIN"
sed -e "s|@LAUNCHER@|$BIN|" -e "s|@REPO@|$PWD|" -e "s|@PATH@|$path|" -e "s|@LOG@|$LOG|g" \
    -e "s|@HOUR@|$hour|" -e "s|@MINUTE@|$minute|" scripts/$LABEL.plist > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$domain" "$PLIST"
printf 'installed %s: runs %s daily at %02d:%02d; log %s\n' "$LABEL" "$BIN" "$hour" "$minute" "$LOG"
echo "run it now with: launchctl kickstart $domain/$LABEL"
