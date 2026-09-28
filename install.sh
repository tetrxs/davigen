#!/bin/zsh
# davigen installer / updater.
#
#   curl -fsSL https://raw.githubusercontent.com/tetrxs/davigen/main/install.sh | zsh
#
# Everything lives in one folder (default ~/Applications/davigen): the code, a private Python, exiftool and
# davigen's data. Outside that folder only three small things are written, all removed by uninstall.sh:
#   · the menu entries ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/davigen.py
#                      and "davigen Basic Correction.py" next to it
#   · a LaunchAgent    ~/Library/LaunchAgents/com.davigen.python.plist  (tells Resolve where davigen's Python is)
#   · baked LUTs       /Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davigen/
#
# Run it again to update – your projects list, settings, catalog and LUTs in data/ are kept.
#
# Options (environment variables):
#   DAVIGEN_HOME=/path        install somewhere else
#   DAVIGEN_REF=v1.2.0        install a tag or branch instead of main
#   DAVIGEN_PYTHON=3.12       use another Python minor version (for older Resolve versions)
#   DAVIGEN_NO_AUTOSTART=1    skip the LaunchAgent (Resolve then finds Python only until the next reboot)
set -euo pipefail

REPO="tetrxs/davigen"
REF="${DAVIGEN_REF:-main}"
SCRIPTS="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
AGENT="$HOME/Library/LaunchAgents/com.davigen.python.plist"
LUTS="/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davigen"

# pinned, checksum-verified downloads
PY_RELEASE="20260924"
PY_VERSION="3.14.7"
typeset -A PY_SHA256=(
  aarch64  d3da099bb2bdd57e2f5ff8496cb9827f7d92eee332b09f8dc93706dabfc51a96
)
EXIFTOOL_VERSION="13.59"
EXIFTOOL_SHA256="668ea3acececb7235fbd0f4900e72d5f12c9b07e5c778fd36cb1e9b5828fd65a"

say()  { print -P "%B→%b $*"; }
ok()   { print -P "%F{green}✓%f $*"; }
warn() { print -P "%F{yellow}!%f $*"; }
die()  { print -P "%F{red}✗%f $*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "davigen runs on macOS only."

# 0 · where are we? ------------------------------------------------------------------------------
# Started from a davigen folder (./install.sh, install.command) → set up that folder in place.
# Piped from curl → download the code into DAVIGEN_HOME (default ~/Applications/davigen).
SELF="${0:A}"
if [[ -f "${SELF:h}/davigen/server.py" && -z "${DAVIGEN_HOME:-}" ]]; then
  ROOT="${SELF:h}"
else
  ROOT="${DAVIGEN_HOME:-$HOME/Applications/davigen}"
  say "Downloading davigen ($REF) into $ROOT"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" | tar -xz -C "$TMP" --strip-components 1 \
    || die "Download failed – check the internet connection and DAVIGEN_REF."
  mkdir -p "$ROOT"
  # replace the code, keep what belongs to this Mac (data/, runtime/)
  rsync -a --delete --exclude '/data/' --exclude '/runtime/' --exclude '/.venv/' "$TMP/" "$ROOT/"
fi
RUNTIME="$ROOT/runtime"
ARCH="$(uname -m)"
PY_ARCH=$([[ "$ARCH" == "arm64" ]] && echo aarch64 || echo x86_64)
echo "davigen · $ROOT · $ARCH"

# 1 · Python ---------------------------------------------------------------------------------------
# A private python-build-standalone build inside runtime/ – no Homebrew, no system Python touched.
WANT="${DAVIGEN_PYTHON:-${PY_VERSION%.*}}"
PY="$RUNTIME/python/bin/python3"
if [[ ! -x "$PY" ]] || [[ "$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null)" != "$WANT" ]]; then
  BASE="https://github.com/astral-sh/python-build-standalone/releases/download/$PY_RELEASE"
  if [[ "$WANT" == "${PY_VERSION%.*}" ]]; then
    FILE="cpython-${PY_VERSION}+${PY_RELEASE}-${PY_ARCH}-apple-darwin-install_only.tar.gz"
    EXPECTED="${PY_SHA256[$PY_ARCH]:-}"
  else
    FILE=""
  fi
  SUMS=""
  if [[ -z "$FILE" || -z "$EXPECTED" ]]; then
    SUMS="$(curl -fsSL "$BASE/SHA256SUMS")" || die "Couldn't read the Python checksums."
    LINE="$(print -r -- "$SUMS" | awk -v a="$PY_ARCH" -v v="$WANT" \
      '$2 ~ "^cpython-" v "\\.[0-9]+\\+[0-9]+-" a "-apple-darwin-install_only\\.tar\\.gz$" {print; exit}')"
    [[ -n "$LINE" ]] || die "No Python $WANT build for $PY_ARCH in release $PY_RELEASE."
    EXPECTED="${LINE%% *}"
    FILE="${LINE##* }"
  fi
  say "Downloading Python ($FILE)"
  mkdir -p "$RUNTIME/downloads"
  curl -fL --progress-bar -o "$RUNTIME/downloads/$FILE" "$BASE/${FILE//+/%2B}"
  echo "$EXPECTED  $RUNTIME/downloads/$FILE" | shasum -a 256 -c - >/dev/null || die "Python checksum mismatch."
  rm -rf "$RUNTIME/python"
  tar -xzf "$RUNTIME/downloads/$FILE" -C "$RUNTIME"
  rm -f "$RUNTIME/downloads/$FILE"
fi
if ! "$PY" -c "import colour" 2>/dev/null; then
  say "Installing colour-science into davigen's Python"
  "$PY" -m pip install -q --disable-pip-version-check --no-warn-script-location -r "$ROOT/requirements.txt"
fi
ok "Python $("$PY" -W ignore -c 'import sys, colour; print(sys.version.split()[0], "· colour-science", colour.__version__)')"

# 2 · exiftool (pure Perl, runs on the Perl every Mac ships) ---------------------------------------
if [[ ! -f "$RUNTIME/exiftool/exiftool" ]] || [[ "$(/usr/bin/perl "$RUNTIME/exiftool/exiftool" -ver 2>/dev/null)" != "$EXIFTOOL_VERSION" ]]; then
  say "Downloading exiftool $EXIFTOOL_VERSION"
  mkdir -p "$RUNTIME/downloads"
  rm -rf "$RUNTIME/exiftool" && mkdir -p "$RUNTIME/exiftool"
  curl -fsSL -o "$RUNTIME/downloads/exiftool.tar.gz" \
    "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-${EXIFTOOL_VERSION}.tar.gz/download"
  echo "$EXIFTOOL_SHA256  $RUNTIME/downloads/exiftool.tar.gz" | shasum -a 256 -c - >/dev/null || die "exiftool checksum mismatch."
  tar -xzf "$RUNTIME/downloads/exiftool.tar.gz" -C "$RUNTIME/exiftool" --strip-components 1
  rm -f "$RUNTIME/downloads/exiftool.tar.gz"
fi
ok "exiftool $(/usr/bin/perl "$RUNTIME/exiftool/exiftool" -ver)"

# downloads and AirDrop copies carry a quarantine flag that would stop Resolve from loading the Python
xattr -dr com.apple.quarantine "$RUNTIME" 2>/dev/null || true

# 3 · Resolve menu entry ----------------------------------------------------------------------------
mkdir -p "$SCRIPTS"
rm -f "$SCRIPTS/Travel Creator.py"                       # name used by early versions
sed "s#@DAVIGEN_ROOT@#$ROOT#" "$ROOT/resolve_menu/davigen.py.template" > "$SCRIPTS/davigen.py"
sed "s#@DAVIGEN_ROOT@#$ROOT#" "$ROOT/resolve_menu/davigen_basic.py.template" > "$SCRIPTS/davigen Basic Correction.py"
ok "Menu entries: Resolve → Workspace → Scripts → davigen, davigen Basic Correction"

# 4 · LUTs baked on this or another Mac (portable copies live in data/luts) --------------------------
if [[ -d "$ROOT/data/luts" ]] && mkdir -p "$LUTS" 2>/dev/null; then
  cp -n "$ROOT"/data/luts/DAVIGEN_*.cube "$LUTS"/ 2>/dev/null || true
fi

# 5 · tell Resolve where Python lives ----------------------------------------------------------------
# Resolve starts $PYTHON3HOME/bin/python3 through an unquoted shell command, so the path must not
# contain spaces. If it does, a symlink in ~/.davigen stands in for it.
PYHOME="$RUNTIME/python"
if [[ "$PYHOME" == *" "* ]]; then
  mkdir -p "$HOME/.davigen"
  ln -sfn "$PYHOME" "$HOME/.davigen/python"
  PYHOME="$HOME/.davigen/python"
fi
BEFORE="$(launchctl getenv PYTHON3HOME 2>/dev/null || true)"
launchctl setenv PYTHON3HOME "$PYHOME"
if [[ -z "${DAVIGEN_NO_AUTOSTART:-}" ]]; then
  mkdir -p "${AGENT:h}"
  cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.davigen.python</string>
  <key>ProgramArguments</key><array>
    <string>/bin/launchctl</string><string>setenv</string><string>PYTHON3HOME</string><string>$PYHOME</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PLIST
  launchctl unload "$AGENT" 2>/dev/null || true
  launchctl load "$AGENT"
  ok "Resolve finds davigen's Python (also after a restart)"
fi

echo
if [[ "$BEFORE" != "$PYHOME" ]] && pgrep -qf "MacOS/Resolve"; then
  warn "Resolve is running – quit and reopen it once so it picks up davigen's Python."
fi
echo "Done. In Resolve: Workspace → Scripts → davigen"
