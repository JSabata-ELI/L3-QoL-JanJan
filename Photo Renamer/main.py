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
import re
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from pr_t import PhotoRenamerWidget, load_ui_state, save_ui_state

_APP_ID_PREFIX = "ELI.PhotoRenamer"


def _app_version() -> str:
    """Version of the running build, e.g. "v1.0.0", or "" when run from source.
    The builder names every exe "<Program> vX.Y.Z.exe", so the exe's own file
    name is the one place that always tells the truth."""
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", Path(sys.executable).stem)
    return m.group(1) if m else ""


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


class PhotoRenamerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ver = _app_version()
        self.setWindowTitle(f"Photo Renamer {ver}" if ver else "Photo Renamer (source)")
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
            box.setText("There are renames, edits or deletions that have not "
                        "been saved.\nClose anyway and throw them away?")
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
    _nudge_taskbar_icon(int(win.winId()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
