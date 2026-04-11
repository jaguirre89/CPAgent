#!/usr/bin/env bash
# CPAgent shortcut installer.
#
# Generates an icon, attaches it to scripts/CPAgent.command via Rez, and
# installs a copy to ~/Applications/CPAgent.command so it shows up in the
# Finder. Also offers to drop a symlink on the Desktop.
#
# Safe to re-run — rebuilds the icon and re-copies the shortcut.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_COMMAND="$SCRIPT_DIR/CPAgent.command"
ICONSET="$SCRIPT_DIR/CPAgent.iconset"
ICNS="$SCRIPT_DIR/CPAgent.icns"

if [ ! -x "$SRC_COMMAND" ]; then
  echo "CPAgent.command not found or not executable at $SRC_COMMAND" >&2
  exit 1
fi

echo "1. Generating icon…"
/usr/bin/python3 "$SCRIPT_DIR/_generate_icon.py"

if [ -d "$ICONSET" ]; then
  echo "2. Building CPAgent.icns from iconset…"
  /usr/bin/iconutil -c icns "$ICONSET" -o "$ICNS" || true
fi

echo "3. Attaching icon to CPAgent.command (via Rez)…"
if command -v Rez >/dev/null 2>&1 && command -v DeRez >/dev/null 2>&1 && [ -f "$ICNS" ]; then
  TMP_RSRC="$(mktemp).rsrc"
  # Create a tmp png that carries the icns as a resource fork via sips
  TMP_PNG="$(mktemp).png"
  /usr/bin/sips -s format png "$ICNS" --out "$TMP_PNG" >/dev/null 2>&1 || cp "$SCRIPT_DIR/CPAgent_icon.png" "$TMP_PNG"
  /usr/bin/sips -i "$ICNS" >/dev/null 2>&1 || true
  if /usr/bin/DeRez -only icns "$ICNS" > "$TMP_RSRC" 2>/dev/null && [ -s "$TMP_RSRC" ]; then
    /usr/bin/Rez -append "$TMP_RSRC" -o "$SRC_COMMAND" 2>/dev/null || true
    /usr/bin/SetFile -a C "$SRC_COMMAND" 2>/dev/null || true
    echo "   ✓ icon attached"
  else
    echo "   ⚠ DeRez/Rez not available — .command will use generic icon (still works)"
  fi
  rm -f "$TMP_RSRC" "$TMP_PNG"
else
  echo "   ⚠ Rez/DeRez not found (Xcode Command Line Tools not fully installed)"
  echo "   You can still set the icon manually: open CPAgent_icon.png in Preview,"
  echo "   Cmd+A, Cmd+C, then right-click CPAgent.command → Get Info, click the"
  echo "   icon in the top-left, Cmd+V."
fi

echo "4. Installing shortcut to ~/Applications/CPAgent.command …"
mkdir -p "$HOME/Applications"
DEST="$HOME/Applications/CPAgent.command"

# Substitute the absolute APP_DIR into the installed copy so it works from
# anywhere (otherwise the fallback resolves ~/Applications/.. which is ~/).
sed "s|__APP_DIR__|$APP_DIR|g" "$SRC_COMMAND" > "$DEST"
chmod +x "$DEST"

# Copy the resource fork too (ditto preserves extended attrs incl. icon)
if command -v /usr/bin/ditto >/dev/null 2>&1; then
  # Only copy the resource fork; overwrite data fork with our substituted version
  /usr/bin/xattr -w com.apple.FinderInfo "$(/usr/bin/xattr -p com.apple.FinderInfo "$SRC_COMMAND" 2>/dev/null)" "$DEST" 2>/dev/null || true
  /usr/bin/xattr -wx com.apple.ResourceFork "$(/usr/bin/xattr -px com.apple.ResourceFork "$SRC_COMMAND" 2>/dev/null)" "$DEST" 2>/dev/null || true
fi

echo
echo "✓ Shortcut installed at $DEST"
echo "  Open Finder → Go → Home → Applications and you'll see CPAgent.command there."
echo "  Drag it to your Dock for one-click access."
echo
echo "  Double-click it to start CPAgent and open the dashboard in your browser."
