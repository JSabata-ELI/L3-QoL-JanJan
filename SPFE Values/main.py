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
import re
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow

from spfe_t import SPFEValuesWidget

_APP_ID_PREFIX = "ELI.SPFEValues"


def _app_version() -> str:
    """Version of the running build, e.g. "v1.0.0", or "" when run from source.
    The builder names every exe "<Program> vX.Y.Z.exe", so the exe's own file
    name is the one place that always tells the truth."""
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", Path(sys.executable).stem)
    return m.group(1) if m else ""


def install_wheel_guard(app=None):
    """A value must never change just because the pointer crossed its control.

    Number boxes, drop-downs and sliders answer the wheel only after they were
    CLICKED (or reached with Tab), and only while the pointer is over them --
    Qt delivers a wheel notch to the control under the pointer, so a selected
    field elsewhere on the page is never touched. Moving the pointer away keeps
    the field selected (the keyboard still edits it); the wheel works on it
    again when the pointer comes back. Selecting another control ends it. An
    unselected field passes the notch to the panel behind it, so a settings
    panel still scrolls when the pointer happens to pass over a field. The
    focus a freshly opened window HANDS its first field does not count.
    """
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider,
                                   QAbstractSpinBox, QApplication, QComboBox,
                                   QScrollBar)

    app = app or QApplication.instance()
    if app is None or getattr(app, "_wheel_guard", None) is not None:
        return

    class _WheelGuard(QObject):
        _GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                   Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)
        # Focus that leaves only for a moment and comes back to the same field:
        # another window was activated, or the drop-down's own list opened.
        _KEEP = (Qt.FocusReason.ActiveWindowFocusReason,
                 Qt.FocusReason.PopupFocusReason)

        def eventFilter(self, obj, ev):
            """EVERY event in the program passes through here, so it may not
            raise -- ever. An exception escaping a Python override on the whole
            application leaves PySide6 in an error state and the process dies
            later with an access violation (measured 2026-09-17), and `obj` can
            be a widget Qt is tearing down. A stray scroll is the cheaper loss.
            """
            try:
                return self._filter(obj, ev)
            except Exception:
                return False

        def _filter(self, obj, ev):
            t = ev.type()
            if t == QEvent.Type.Polish and isinstance(obj, self._GUARDED):
                # Spin boxes and drop-downs come with WheelFocus: Qt hands them
                # the focus, as a MOUSE focus, the moment a wheel notch lands on
                # them -- before the notch itself is delivered. That made every
                # untouched field "clicked" just in time to take the notch.
                # StrongFocus still takes a click or Tab, never the wheel.
                if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                    obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                return False
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                # A click is what selects the field -- also the field that
                # already HAD the focus (the first one of a window is handed
                # it), where no FocusIn follows the click. The press lands on
                # the spin box's own text line, so look one or two levels up.
                w = obj
                for _ in range(3):
                    if isinstance(w, self._GUARDED):
                        w.setProperty("wheelReady", True)
                        break
                    w = w.parentWidget() if hasattr(w, "parentWidget") else None
                    if w is None:
                        break
                return False
            if t == QEvent.Type.FocusIn and isinstance(obj, self._GUARDED):
                if ev.reason() in self._EARNED:
                    obj.setProperty("wheelReady", True)
                return False
            if t == QEvent.Type.FocusOut and isinstance(obj, self._GUARDED):
                if ev.reason() not in self._KEEP:
                    obj.setProperty("wheelReady", False)
                return False
            if t != QEvent.Type.Wheel:
                return False
            if not isinstance(obj, self._GUARDED) or isinstance(obj, QScrollBar):
                return False
            if (obj.property("wheelAlways")
                    or (obj.hasFocus() and obj.property("wheelReady"))):
                return False
            pane = obj.parentWidget()
            while pane is not None and not isinstance(pane, QAbstractScrollArea):
                pane = pane.parentWidget()
            if pane is not None:
                QApplication.sendEvent(pane.viewport(), ev)
            return True

    guard = _WheelGuard(app)
    app.installEventFilter(guard)
    app._wheel_guard = guard


def _icon_file() -> Path | None:
    """icon.ico next to the exe, then the copy bundled inside it (the builder
    adds it with --add-data), then next to this file."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    cands.append(Path(__file__).resolve().parent / "icon.ico")
    for p in cands:
        if p.exists():
            return p
    return None


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


def _nudge_taskbar_icon(hwnd) -> None:
    """Make the taskbar re-read this window's icon a few times after start.

    Explorer reads a taskbar button's picture once, when it creates the
    button, and does not ask again by itself. When that one read misses, the
    button keeps the blank window placeholder for the whole run although the
    window holds the right icon -- measured on Diagnostic v1.3.4, 15.10.2026:
    started from C:, a fresh per-launch id, WM_GETICON answering with the
    magnifier, the button blank. Re-sending the same icon changed nothing;
    taking the icon away and giving it back made Explorer re-read it, and the
    same running window drew the magnifier at once. So that is done here a few
    times while the button may still be appearing (a slow start, a busy
    Explorer after logon). Runs on a daemon thread; the window's own icon
    handles are put back, so nothing is created or leaked.
    """
    if not hwnd:
        return
    import threading

    def run():
        import ctypes
        import ctypes.wintypes as wt
        import time
        user32 = ctypes.windll.user32
        send = user32.SendMessageTimeoutW
        send.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT,
                         wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_size_t
        res = ctypes.c_size_t()
        for delay in (2, 8, 20, 30):          # at about 2, 10, 30 and 60 s
            time.sleep(delay)
            try:
                if not user32.IsWindow(wt.HWND(hwnd)):
                    return
                held = {}
                for which in (1, 0):          # ICON_BIG, ICON_SMALL
                    res.value = 0
                    # SMTO_ABORTIFHUNG: a frozen window just skips this round
                    if send(hwnd, 0x007F, which, 0, 0x0002, 2000,
                            ctypes.byref(res)) and res.value:
                        held[which] = res.value
                for which in held:
                    send(hwnd, 0x0080, which, 0, 0x0002, 2000, ctypes.byref(res))
                for which, h in held.items():
                    send(hwnd, 0x0080, which, h, 0x0002, 2000, ctypes.byref(res))
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name="taskbar-icon").start()


class SPFEValuesWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ver = _app_version()
        self.setWindowTitle(f"SPFE Values {ver}" if ver else "SPFE Values (source)")
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
    _nudge_taskbar_icon(int(win.winId()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
