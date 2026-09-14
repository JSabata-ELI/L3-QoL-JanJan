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
    """A taskbar identity derived from the icon's bytes and this run's file name.

    Windows caches the taskbar picture per AppUserModelID and never re-reads it,
    so an id once seen without a usable icon keeps drawing the blank placeholder
    for good. Returns None when there is no icon to give, in which case no id is
    set at all and Windows keys the button on the process itself.
    """
    icon = _icon_file()
    if icon is None:
        return None
    try:
        data = icon.read_bytes()
    except OSError:
        return None
    build = os.path.basename(sys.argv[0] or __file__)
    digest = hashlib.md5(data + b"\x00"
                         + build.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{_APP_ID_PREFIX}.{digest}"


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
