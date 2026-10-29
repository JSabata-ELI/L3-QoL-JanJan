"""Render the Launcher window to PNGs, without putting anything on screen.

Run from the Launcher folder:  python testing/render_window.py <out folder> [source path]

QT_QPA_PLATFORM=minimal:enable_fonts has the real Windows fonts and shows
nothing. With a source path the real programs there are scanned; without one,
made-up programs are used. Writes window.png (Main scripts open, one update
highlighted), window_open.png (every group open) and group_menu.png.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

import l  # noqa: E402

TMP = Path(tempfile.mkdtemp())
l.CONFIG_PATH = TMP / "config.json"
l.Launcher._auto_select_root = lambda self: None
l.Launcher._schedule_version_poll = lambda self: None


def fake_programs() -> dict:
    icon = Path(l.__file__).resolve().parent / "icon.ico"
    progs = {}
    for name, full in (("Image Tools", True), ("Screenshots", True), ("Announcer", False),
                       ("CSS Logger", True), ("Chiller Log", True),
                       ("Launcher", True), ("Diagnostic Webex listener", True),
                       ("Calibrations", False), ("Internal Builder", False)):
        d = TMP / "root" / name
        exe = d / f"{name} v1.0.0.exe"
        progs[name] = {
            "exe_path": exe, "readme_path": d / "ReadMe.txt",
            "readme_full_path": (d / "Full.txt") if full else None,
            "label": name, "program_dir": d, "icon_path": icon,
            "archive_versions": ([{"exe_path": exe, "py_path": None,
                                   "has_internal": False, "label": "v1.0.0"}]
                                 if name != "Announcer" else []),
        }
    return progs


def main(out: str, source: str | None):
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    l.apply_app_style(app)
    win = l.Launcher()
    win.show()
    if source:
        win._root_options = [("Source", Path(source), False)]
        win._set_root_idx(0)
        win.refresh()
        gen = win._scan_gen
        t0 = time.time()
        while not win.programs and time.time() - t0 < 60:
            app.processEvents()
            time.sleep(0.05)
        print("scanned", len(win.programs), "programs, gen", gen)
    else:
        win._apply_programs(fake_programs(), "Test", TMP / "root", win._scan_gen)
        win._update_available.add("Screenshots")
        win._set_update_style("Screenshots")
    app.processEvents()
    win.grab().save(str(out_dir / "window.png"))

    for _t, gkey in l.GROUP_ORDER:
        if win._group_items.get(gkey) and not win._group_expanded.get(gkey):
            win._toggle_group(gkey)
    app.processEvents()
    win.resize(win.width(), 900)
    app.processEvents()
    win.grab().save(str(out_dir / "window_open.png"))

    # The right-click menu, as built by _show_group_menu (not opened).
    name = next(iter(win._prog_buttons))
    menu = QMenu()
    menu.addAction("Acknowledge update")
    menu.addSeparator()
    head = menu.addAction("Move to group:")
    head.setEnabled(False)
    head.setFont(l._ui_font(9, bold=True))
    menu.addSeparator()
    for title, gkey in l.GROUP_ORDER:
        act = menu.addAction(title)
        if gkey == win._group_of(name):
            act.setIcon(l.drawn_icon("tick"))
    menu.adjustSize()
    menu.grab().save(str(out_dir / "group_menu.png"))
    print("window", win.width(), "x", win.height(), "min", win.minimumWidth())
    win.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
