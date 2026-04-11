#!/usr/bin/env bash
# Remove the CPAgent LaunchAgent.

set -euo pipefail

DEST="$HOME/Library/LaunchAgents/com.cpagent.app.plist"

if [ ! -f "$DEST" ]; then
  echo "Nothing to uninstall — plist not found at $DEST"
  exit 0
fi

launchctl unload "$DEST" 2>/dev/null || true
rm "$DEST"

echo "✓ LaunchAgent removed."
echo "  To fully stop: pkill -f 'python.*app.py' (if still running)"
