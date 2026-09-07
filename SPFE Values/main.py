"""
main.py  --  SPFE Values, standalone.

The whole program is SPFEValuesWidget in spfe_t.py; this file only gives it a
window, an icon and a taskbar identity. When the widget becomes a tab of CSS
Logger, this file is simply not copied over.

Run with:  python main.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow

from spfe_t import SPFEValuesWidget

_APP_ID_PREFIX = "ELI.SPFEValues"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _icon_file() -> Path | None:
    p = _app_dir() / "icon.ico"
    return p if p.exists() else None


def _icon_app_id() -> str | None:
    """A taskbar identity derived from the icon's bytes and this build's name.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so an id once seen without a usable icon keeps drawing the blank
    window placeholder for good. Hashing the icon alone is not enough --
    measured 2026-09-03 on four programs that did exactly that -- because such
    an id only changes when the picture is redrawn. The running build's own
    file name, which carries the version, goes in as well, so every rebuild
    runs under an id Windows has never seen.

    Returns None when there is no icon to give: the caller then sets no id at
    all rather than burning the plain prefix on a run with no picture.
    """
    # A frozen build gets no taskbar identity at all, deliberately.
    # Windows caches the taskbar picture per AppUserModelID and never re-reads
    # it, so one bad cache entry breaks that build for good; tagging the id
    # with the build's file name only postponed it (Diagnostic v1.1.3's id
    # drew the blank placeholder within a day of the build). Measured
    # 2026-09-04 with three otherwise identical windows: the app's own id ->
    # placeholder, a never-seen id -> the right icon, no id at all -> the icon
    # from the exe's own resource, which the builder always embeds (verified
    # on a purpose-built PyInstaller exe). With no id Windows keys the button
    # on the exe itself, so there is no per-id cache left to go stale. An id
    # is still worth having when running from source, where the process is
    # python.exe and would otherwise wear the Python icon.
    import sys as _sys
    if getattr(_sys, "frozen", False):
        return None
    icon = _icon_file()
    if icon is None:
        return None
    try:
        data = icon.read_bytes()
    except OSError:
        return None
    build = os.path.basename(sys.executable if getattr(sys, "frozen", False)
                             else (sys.argv[0] or __file__))
    digest = hashlib.md5(data + b"\x00"
                         + build.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{_APP_ID_PREFIX}.{digest}"


class SPFEValuesWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SPFE Values")
        icon = _icon_file()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1400, 880)
        self.setMinimumSize(1000, 640)
        self._widget = SPFEValuesWidget()
        self.setCentralWidget(self._widget)
        # The window's own ground, for the moments the central widget has not
        # been laid out over it yet. Windows here runs in dark mode, and
        # anything left to the theme comes out black.
        self.setStyleSheet("QMainWindow{background:#F5F5F5;}")

    def closeEvent(self, event):
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
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                app_id)
        except Exception:
            pass

    app = QApplication(sys.argv)
    icon = _icon_file()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))
    win = SPFEValuesWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
