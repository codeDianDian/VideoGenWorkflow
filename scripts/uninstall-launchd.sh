#!/usr/bin/env bash
set -euo pipefail
LABEL="com.vidforge.watcher"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ -f "$PLIST_DST" ]; then
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "Removed $PLIST_DST"
else
  echo "Nothing to remove."
fi
