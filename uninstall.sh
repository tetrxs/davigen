#!/bin/zsh
# Removes davigen from this Mac. Your projects and footage are not touched.
#
#   ~/Applications/davigen/uninstall.sh            remove menu entry and autostart, move the folder to the Trash
#   ~/Applications/davigen/uninstall.sh --keep     same, but leave the davigen folder where it is
#   ~/Applications/davigen/uninstall.sh --purge    also move davigen's LUTs out of Resolve's LUT folder
#
# By default the LUTs stay: the color groups of projects you already made point to them.
set -euo pipefail

ROOT="${0:A:h}"
SCRIPTS="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
AGENT="$HOME/Library/LaunchAgents/com.davigen.python.plist"
LUTS="/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davigen"

trash() {  # move to the Trash (recoverable) instead of deleting
  [[ -e "$1" ]] || return 0
  osascript -e 'on run argv' -e 'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)' \
    -e 'end run' "$1" >/dev/null
}

launchctl unload "$AGENT" 2>/dev/null || true
launchctl unsetenv PYTHON3HOME
rm -f "$AGENT" "$SCRIPTS/davigen.py" "$SCRIPTS/davigen Basic Correction.py" "$SCRIPTS/Travel Creator.py"
rm -f "$HOME/.davigen/python" && rmdir "$HOME/.davigen" 2>/dev/null || true
echo "✓ Menu entry and autostart removed"
if [[ "${1:-}" == "--purge" ]]; then
  trash "$LUTS"
  echo "✓ LUTs moved to the Trash"
fi

if [[ "${1:-}" != "--keep" && -f "$ROOT/davigen/server.py" ]]; then
  trash "$ROOT"
  echo "✓ $ROOT moved to the Trash"
fi
echo "davigen is uninstalled. Restart Resolve to drop the menu entry."
