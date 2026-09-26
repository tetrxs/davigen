#!/bin/zsh
# Double-click: set up davigen from this folder (Resolve menu entry, Python, autostart).
cd "${0:A:h}" && ./install.sh
echo; read -k1 "?Press any key to close…"
