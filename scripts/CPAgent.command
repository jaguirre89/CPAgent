#!/usr/bin/env bash
# CPAgent launcher — double-click from Finder / Desktop / Dock to start.
#
# Behaviour:
#   1. If the server is already responding on localhost:5050, just open the browser.
#   2. Otherwise start the Flask app in the background, wait until it responds,
#      then open the browser.
#   3. The Terminal window closes automatically after a few seconds.
#
# APP_DIR is substituted to an absolute path at install time by install_shortcut.sh.
# If you see "__APP_DIR__" below literally, the installer fell back to relative
# resolution and you should re-run scripts/install_shortcut.sh.

set -e

APP_DIR="__APP_DIR__"

if [ ! -f "$APP_DIR/app.py" ]; then
  # Fallback: resolve relative to this script (works when run from source tree)
  SCRIPT_PATH="${BASH_SOURCE[0]}"
  while [ -L "$SCRIPT_PATH" ]; do
    SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ $SCRIPT_PATH != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
  done
  SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
  APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

cd "$APP_DIR"
mkdir -p logs

PORT=5050
URL="http://localhost:$PORT"

echo "CPAgent launcher"
echo "  App dir: $APP_DIR"
echo "  URL:     $URL"
echo

# Already running?
if curl -s -o /dev/null -w "%{http_code}" "$URL/api/config" | grep -qE '2|3'; then
  echo "✓ CPAgent already running — opening browser."
  open "$URL"
  sleep 2
  exit 0
fi

# Not running — start it
echo "Starting CPAgent…"
nohup /usr/bin/python3 "$APP_DIR/app.py" > "$APP_DIR/logs/cpagent.out.log" 2> "$APP_DIR/logs/cpagent.err.log" &
CPAGENT_PID=$!
echo "  pid=$CPAGENT_PID"
disown $CPAGENT_PID 2>/dev/null || true

# Wait for the server to respond (max 15s)
for i in $(seq 1 30); do
  if curl -s -o /dev/null "$URL/api/config"; then
    echo "✓ CPAgent ready."
    open "$URL"
    sleep 2
    exit 0
  fi
  sleep 0.5
done

echo "✗ CPAgent did not become ready in 15s. Check $APP_DIR/logs/cpagent.err.log"
read -n 1 -s -r -p "Press any key to close…"
exit 1
