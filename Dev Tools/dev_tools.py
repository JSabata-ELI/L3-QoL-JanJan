# dev_tools.py
"""Dev Tools — one window, two tabs (Builder, Copy Manager) and one shared log.

PySide6, in the Image Tools look. All logic lives in dt_core.py; dt_builder.py
and dt_copy.py are the two tabs, dt_common.py the shared look, log and dialogs.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

import dt_core as core
from dt_builder import BuilderTab
from dt_common import MAIN_TABS_QSS, LogHub, apply_app_style, init_ui_thread
from dt_copy import CopyTab

_APP_ID_PREFIX = "ELI.DevToolsPractice" if core.PRACTICE else "ELI.DevTools"


def _icon_file():
    """icon.ico: next to the exe, then inside the bundle, then the source folder."""
    return core.icon_file()


def _app_version() -> str:
    """Version of the running build, e.g. "v2.0.0", or "" when run from source.
    The builder names every exe "<Program> vX.Y.Z.exe", so the exe's own file
    name is the one place that always tells the truth."""
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", Path(sys.executable).stem)
    return m.group(1) if m else ""


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


# Note: no WM_SETICON / SetClassLongPtr "force taskbar icon" helper here (the Tk
# version needed one). Measured on Win11: the taskbar draws the *window* icon,
# so Qt's setWindowIcon is enough. _nudge_taskbar_icon above sets no new
# picture; it only takes the window's own icon away and back.


class DevToolsWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ver = _app_version()
        title = f"Dev Tools {ver}" if ver else "Dev Tools (source)"
        if core.PRACTICE:
            title += "  —  practice folders"
        self.setWindowTitle(title)
        icon = core.icon_file()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1280, 860)
        self.setMinimumSize(1060, 620)
        self.setStyleSheet("QMainWindow { background: #f3f3f3; }")

        self.hub = LogHub(self)
        # Practice mode only: its folders and config must exist before the tabs
        # read them. Does nothing in normal use.
        core.ensure_practice_setup(self.hub.write)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setStyleSheet(MAIN_TABS_QSS)
        self.builder = BuilderTab(self.hub)
        self.copy = CopyTab(self.hub)
        self.builder.copy_tab = self.copy
        self.copy.builder = self.builder
        self.tabs.addTab(self.builder, "Builder")
        self.tabs.addTab(self.copy, "Copy Manager")
        self.setCentralWidget(self.tabs)


def main() -> int:
    app_id = _icon_app_id() if os.name == "nt" else None
    if app_id:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass

    app = QApplication(sys.argv)
    apply_app_style(app)
    icon = core.icon_file()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))
    init_ui_thread()
    win = DevToolsWindow()
    win.show()  # normal 1280 x 860 window, not maximized
    _nudge_taskbar_icon(int(win.winId()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
