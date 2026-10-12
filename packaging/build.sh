#!/usr/bin/env bash
# Build the Porens desktop app into a standalone executable with PyInstaller.
#
#   dist/Porens        (Linux)
#   dist/Porens.exe    (Windows)
#   dist/Porens.app    (macOS, when built with --windowed)
#
# Requires: a Python with Tkinter (the standard python.org builds have it),
# plus `pip install pyinstaller`. Run from anywhere; paths resolve to the repo.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m pip install --upgrade pyinstaller >/dev/null
python -m pip install . >/dev/null   # install porens so PyInstaller can bundle it

pyinstaller --noconfirm --onefile --windowed --name Porens \
  --collect-submodules porens \
  packaging/app_entry.py

echo
echo "Built: $(ls -1 dist/Porens* 2>/dev/null)"
echo "On macOS/Windows the file is Porens.app / Porens.exe."
