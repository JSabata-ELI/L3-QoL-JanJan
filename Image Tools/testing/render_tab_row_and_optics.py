"""Render the main tab row and the Pointing Optics window (05.10.2026).

  * Image Finder on screen: no layout tabs in the tab row.
  * Image Slider on screen: its layout tabs ("Tab 1", "Tab 2", "+") right after
    "Workshop", Stop All at the far right.
  * the Optics window (equations, Pixel / M / f) and the Pointing sidebar row.

Windows are shown without taking the keyboard focus; the program's own state is
left alone (a temporary APPDATA). PNGs land next to this script.

    python testing/render_tab_row_and_optics.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eli_it_tabrow_")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.argv = sys.argv[:1]

from PySide6.QtCore import Qt, QTimer                       # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow     # noqa: E402

import main as it_main                                      # noqa: E402

from importlib.machinery import SourceFileLoader            # noqa: E402

_orig_exec_module = SourceFileLoader.exec_module


def _exec_module(self, mod):
    # main.py loads is_t.py by file; patch its Viewer as soon as it exists.
    _orig_exec_module(self, mod)
    if getattr(mod, "__name__", "") == "image_slider" and hasattr(mod, "Viewer"):
        # No Time window dialog, no scan — only the widgets are needed.
        mod.Viewer.auto_start_online = lambda self, *a, **k: None
        mod.Viewer.start_from_tab_state = lambda self, *a, **k: True


SourceFileLoader.exec_module = _exec_module


def _quiet(w):
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)


STATE = {}


def _show(self):
    _quiet(self)
    self.resize(1500, 900)
    self.move(40, 40)
    self.show()
    STATE["win"] = self


QMainWindow.showMaximized = _show


def _save(w, name):
    path = HERE / name
    w.grab().save(str(path))
    print("saved", path)


def _save_rect(w, y, h, name):
    from PySide6.QtCore import QRect
    path = HERE / name
    w.grab(QRect(0, max(0, y), w.width(), h)).save(str(path))
    print("saved", path)


def _run(app):
    win = STATE["win"]
    tabs = win.centralWidget()
    viewer = tabs.widget(1)

    def step1():
        tabs.setCurrentIndex(0)
        print("main bar", tabs.tabBar().geometry(), "corner",
              tabs.cornerWidget(Qt.Corner.TopRightCorner).geometry())
        for x in (450, 600, 800):
            w = win.childAt(x, 1)
            print("at", x, type(w).__name__ if w else None,
                  w.objectName() if w else "", w.geometry() if w else "")
        _save(win, "tabrow_finder.png")
        tabs.setCurrentWidget(viewer)

    def step2():
        viewer.new_tab("Tab 2")
        app.processEvents()

    def step3():
        _save(win, "tabrow_slider.png")
        v = viewer.active()
        v._set_all_sections(True)
        dlg = v._pointing_optics_dlg
        _quiet(dlg)
        v._open_pointing_optics()
        STATE["v"] = v

    def step4():
        v = STATE["v"]
        _save(v._pointing_optics_dlg, "pointing_optics_dialog.png")
        b = v.btn_pointing_optics
        box = b.parentWidget()
        top = b.mapTo(box, b.rect().topLeft())
        _save_rect(box, top.y() - 40, 200, "pointing_sidebar.png")
        v._pointing_optics_dlg.close()
        app.quit()

    for i, fn in enumerate((step1, step2, step3, step4)):
        QTimer.singleShot(2500 + i * 2000, fn)


_orig_exec = QApplication.exec


def _exec(self=None):
    app = QApplication.instance()
    _run(app)
    return _orig_exec()


QApplication.exec = _exec

it_main.main()
