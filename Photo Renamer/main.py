"""
main.py  --  Photo Renamer, standalone.

The whole program is PhotoRenamerWidget in pr_t.py; this file only gives it a
window, an application stylesheet and a taskbar identity.

Deliberately never built. The Launcher lists a folder only when it finds an exe
in it, so as long as this stays source-only it never shows up there.

Run with:  python main.py     (or double-click run.bat)
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from pr_t import PhotoRenamerWidget, load_ui_state, save_ui_state

_APP_ID_PREFIX = "ELI.PhotoRenamer"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _icon_file() -> Path | None:
    p = _app_dir() / "icon.ico"
    return p if p.exists() else None


def _icon_app_id() -> str | None:
    """A taskbar identity that is new on every launch.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so every *stable* id tried here eventually picked up a bad cache entry
    and then drew the blank window placeholder for good: a fixed string, a hash
    of the icon, and a hash tagged with the build's file name each broke within
    days. Setting no id at all was no better -- Windows then keys the button on
    the exe path and caches the picture there instead (Diagnostic v1.3.1,
    measured 2026-09-17: the window icon, the exe's own icon resource and the
    shell's own file icon all correct, the taskbar button blank).

    An id Windows has never seen has no cache entry, so the button falls back
    to the window icon, which every program here sets itself -- measured on a
    fresh id on 2026-09-04 and again on 2026-09-17. A random suffix per launch
    makes every run a first-time id, which is why this is the one form that
    cannot go stale. Nothing here needs a stable identity: no program registers
    a shortcut, pins itself or sends Windows toasts. The one cost is pinning a
    *running* taskbar button -- that pin would carry this run's id and would
    not start the program again, so pin the exe instead.

    Returns None when there is no icon at all; the caller then sets no id and
    the button keeps taking the exe's own picture.
    """
    if _icon_file() is None:
        return None
    import uuid
    return f"{_APP_ID_PREFIX}.{uuid.uuid4().hex[:12]}"


class PhotoRenamerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Photo Renamer")
        icon = _icon_file()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        state = load_ui_state()
        w = state.get("win_w")
        h = state.get("win_h")
        self.resize(w if isinstance(w, int) and w > 600 else 1320,
                    h if isinstance(h, int) and h > 400 else 860)
        # Only the height is fixed here. The narrowest useful width is decided by
        # the widget itself, from what its top bar needs, so no button can ever
        # be pushed out of the window.
        self.setMinimumHeight(520)
        self._widget = PhotoRenamerWidget()
        self.setCentralWidget(self._widget)
        # The window's own ground, for the moments before the central widget has
        # been laid out over it. Windows here runs in dark mode, and anything
        # left to the theme comes out black.
        self.setStyleSheet("QMainWindow { background: #f3f3f3; }")

    def closeEvent(self, event):
        if self._widget.has_pending_changes():
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved changes")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText("There are renames or deletions that have not been "
                        "saved.\nClose anyway and throw them away?")
            box.setStyleSheet("QMessageBox { background: #f3f3f3; color: #111; }"
                              "QLabel { color: #111; }"
                              "QPushButton { padding: 5px 11px;"
                              " border: 1px solid #b6b6b6; border-radius: 3px;"
                              " background: #efefef; color: #111; }"
                              "QPushButton:hover { background: #d9e8ff; }")
            box.setStandardButtons(QMessageBox.StandardButton.Yes
                                   | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        try:
            state = load_ui_state()
            state["win_w"] = self.width()
            state["win_h"] = self.height()
            save_ui_state(state)
        except Exception:
            pass
        try:
            self._widget.shutdown()
        except Exception:
            pass
        super().closeEvent(event)


def main() -> int:
    app_id = _icon_app_id() if os.name == "nt" else None
    if app_id:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Every colour stated. Left to the theme, this comes out white on white on a
    # light machine and black on black on a dark one.
    app.setStyleSheet("""
        QWidget      { background: #f3f3f3; color: #111; }
        QLabel       { background: transparent; }
        QPushButton  { padding: 5px 8px; }
        QToolTip {
            background: #ffffcc; color: #111;
            border: 1px solid #aaa; padding: 4px;
        }
    """)
    icon = _icon_file()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))
    win = PhotoRenamerWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
