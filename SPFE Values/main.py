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

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox,
    QMainWindow, QSlider, QWidget,
)

from spfe_t import SPFEValuesWidget

_APP_ID_PREFIX = "ELI.SPFEValues"


class _WheelGuard(QObject):
    """The mouse wheel must never change a number box or a drop-down just
    because the pointer happens to hover over it.

    The same guard as CSS Logger's (CSS Logger/main.py), so the widget behaves
    the same standalone and once it is a tab there. A field reacts to the
    wheel only after it has been clicked, and only while the pointer has
    stayed on it since; otherwise the scroll goes to the panel underneath.

    Every such field is switched from Qt's WheelFocus to StrongFocus when it
    is polished: with WheelFocus the wheel itself focuses the field before
    any filter sees the event, so "has focus" alone proves nothing.
    """

    _TARGETS = (QAbstractSpinBox, QComboBox, QSlider)
    _ARMED = "_wheel_armed"

    def _target_of(self, obj):
        """The guarded field `obj` belongs to -- itself, or the spin box /
        combo whose inner line edit received the event."""
        w = obj
        for _ in range(3):
            if w is None or not isinstance(w, QWidget):
                return None
            if isinstance(w, self._TARGETS):
                return w
            w = w.parentWidget()
        return None

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Polish:
            if isinstance(obj, self._TARGETS) and \
                    obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            return False
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            tgt = self._target_of(obj)
            if tgt is not None:
                tgt.setProperty(self._ARMED, True)
            return False
        if t in (QEvent.Type.Leave, QEvent.Type.FocusOut, QEvent.Type.Hide):
            if isinstance(obj, self._TARGETS) and obj.property(self._ARMED):
                obj.setProperty(self._ARMED, False)
            return False
        if t != QEvent.Type.Wheel or not isinstance(obj, self._TARGETS):
            return False
        if obj.property(self._ARMED) and obj.hasFocus():
            return False                    # clicked into it -> normal behaviour
        w = obj.parentWidget()
        while w is not None:                # pass the scroll on to the panel
            if isinstance(w, QAbstractScrollArea):
                QApplication.sendEvent(w.viewport(), ev)
                break
            w = w.parentWidget()
        return True


def install_wheel_guard(app: QApplication) -> _WheelGuard:
    """Install the guard on the whole application. Kept on the app object as
    well, so it is not collected while the program runs."""
    guard = _WheelGuard(app)
    app.installEventFilter(guard)
    app._spfe_wheel_guard = guard
    return guard


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
    install_wheel_guard(app)
    icon = _icon_file()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))
    win = SPFEValuesWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
