# Packaging Porens as a desktop app

Porens ships as a GUI you can run without the command line:

```bash
porens gui          # if installed via pip
python -m porens gui
```

To hand someone a **standalone application** with no Python install:

```bash
./packaging/build.sh
```

This uses [PyInstaller](https://pyinstaller.org) to produce a single file in
`dist/`:

| OS      | Output          | Notes                                   |
|---------|-----------------|-----------------------------------------|
| Windows | `Porens.exe`    | double-click to run                     |
| macOS   | `Porens.app`    | `--windowed` makes a proper app bundle  |
| Linux   | `Porens`        | a self-contained ELF executable         |

Build on the OS you are targeting (PyInstaller does not cross-compile). The
Python you build with must include Tkinter — the official python.org
installers and most distro packages do (`sudo apt install python3-tk` on
Debian/Ubuntu).

The entry point is `packaging/app_entry.py`, which just calls
`porens.gui.launch()`. `--collect-submodules porens` ensures the lazily
imported recovery modules (`porens.formats.*`, `porens.filesystems.*`) are
bundled.
