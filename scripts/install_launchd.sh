#!/usr/bin/env bash
# Install CPAgent as a macOS LaunchAgent so it runs at login and survives
# reboots. Writes a templated copy of com.cpagent.app.plist into
# ~/Library/LaunchAgents/ with absolute paths substituted in.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$SCRIPT_DIR/com.cpagent.app.plist"
DEST="$HOME/Library/LaunchAgents/com.cpagent.app.plist"
LOGS_DIR="$APP_DIR/logs"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

if [ ! -f "$TEMPLATE" ]; then
  echo "Template plist not found: $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$LOGS_DIR"
mkdir -p "$HOME/Library/LaunchAgents"

# Substitute absolute paths (use | as delimiter to survive slashes)
sed \
  -e "s|__PYTHON__|$PYTHON_BIN|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  "$TEMPLATE" > "$DEST"

chmod 644 "$DEST"

# Unload first in case it's already installed
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "✓ LaunchAgent installed: $DEST"
echo "  Python: $PYTHON_BIN"
echo "  WorkingDir: $APP_DIR"
echo "  Logs: $LOGS_DIR"
echo
echo "Verify with: launchctl list | grep cpagent"
echo "Browse to:   http://localhost:5050"
