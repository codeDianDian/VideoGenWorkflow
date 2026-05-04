#!/usr/bin/env bash
# Install vidforge watcher as a per-user launchd service so it auto-starts at login.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
BIN="$VENV/bin/vidforge"
LABEL="com.vidforge.watcher"
PLIST_SRC="$ROOT/scripts/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$BIN" ]; then
  echo "Error: $BIN not found. Run 'make install' first." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed \
  -e "s|__VIDFORGE_BIN__|$BIN|g" \
  -e "s|__VIDFORGE_ROOT__|$ROOT|g" \
  -e "s|__VIDFORGE_VENV__|$VENV|g" \
  "$PLIST_SRC" > "$PLIST_DST"

launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "Installed: $PLIST_DST"
echo "Status:    launchctl list | grep $LABEL"
echo "Logs:      tail -f $ROOT/runs/_watcher.log"
echo "Stop:      make uninstall-launchd"
