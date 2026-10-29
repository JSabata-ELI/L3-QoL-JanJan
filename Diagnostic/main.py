import atexit
import re
import sys
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QLabel, QProxyStyle, QStyle,
)
from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QPalette, QColor

# NOTE: matplotlib (via monitor_tab) is the heavy part of startup — on a cold
# OS file cache its import can take tens of seconds. monitor_tab is imported
# lazily inside main() after the splash is shown, so the window/splash paints
# before that cost is paid instead of after.

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).parent


def _app_version():
    """Version of the running build, e.g. "v1.3.7", or "" when run from source.

    The builder names every exe "Diagnostic vX.Y.Z.exe", so the running exe's
    own file name is the one place that always tells the truth — also for an
    older version started from the archive.
    """
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", Path(sys.executable).stem)
    return m.group(1) if m else ""

# PID lock file: lets remote_launcher.py tell whether the app is already
# running (so a Webex "run diagnostic" command doesn't spawn a second copy).
#
# Written in TWO places. This one sits next to the program and is kept only so
# an older watcher still finds it; the one that counts is the shared status file
# in %APPDATA%\Diagnostic (alerting.run_status_path). Next to the program is not
# a usable meeting point any more: the app now starts either from here or from a
# built version under C:\Dev\dist\Diagnostic\vX.Y.Z, and each of those would keep
# its own lock — so a watcher started from one folder would never see a copy
# launched from the other, and would open a second one.
LOCK_FILE = APP_DIR / "diagnostic.lock"


def _acquire_lock():
    # Imported here, not at the top: this runs before the splash, and alerting
    # pulls in requests. Same lazy-import rule as matplotlib above —
    # nothing that only matters later may delay the first painted pixel.
    import alerting
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    # monitoring=False is the honest starting point: the window is coming up but
    # nothing is armed yet. monitor_tab flips it when the switch actually moves,
    # which is what remote_launcher waits for before it reports "running".
    alerting.write_run_status(pid=os.getpid(), exe=str(sys.executable),
                              monitoring=False)


def _release_lock():
    import alerting
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    alerting.clear_run_status()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

APP_STYLESHEET = """
    QWidget      { background: #f3f3f3; color: #111; }
    QLabel       { background: transparent; }
    QPushButton  { padding: 5px 8px; }
    QComboBox    { padding: 3px 6px; }
    QProgressBar { background: #fff; }
    QTabWidget::pane { border: 1px solid #ccc; }
    QTabBar::tab {
        background: #e8e8e8; color: #444;
        padding: 6px 18px; border: 1px solid #ccc;
        border-bottom: none; border-radius: 3px 3px 0 0;
        margin-right: 2px;
    }
    QTabBar::tab:selected { background: #f3f3f3; color: #111; font-weight: 600; }
    QTabBar::tab:hover    { background: #d8e8ff; }
    QTableView { background: #ffffff; gridline-color: #ddd; }
    QHeaderView::section {
        background: #e8e8e8; color: #333;
        padding: 4px; border: 1px solid #ccc;
    }
    QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget {
        background: #ffffff; color: #111; border: 1px solid #bbb;
    }
    QTableView::indicator, QCheckBox::indicator {
        width: 16px; height: 16px;
        border: 1px solid #888; border-radius: 3px; background: #fff;
    }
    QTableView::indicator:checked, QCheckBox::indicator:checked {
        background: #1565C0; border-color: #1565C0;
    }
    /* A selected row is painted blue, so the blue box above would vanish into
       it: on a selected row the box gets a white rim -- dark navy = off,
       white = on. */
    QTableView::indicator:selected {
        border: 2px solid #ffffff; background: #0d2a5c;
    }
    QTableView::indicator:checked:selected {
        border: 2px solid #ffffff; background: #ffffff;
    }
    QToolTip {
        background: #ffffcc; color: #111;
        border: 1px solid #aaa; padding: 4px;
    }
"""

class ToolTipDelayStyle(QProxyStyle):
    """Show tooltips after a 0.5 s hover (Qt's default is ~0.7 s)."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return 500
        return super().styleHint(hint, option, widget, returnData)


class _WheelGuard(QObject):
    """The mouse wheel must never change a number box, drop-down or slider just
    because the pointer happens to be over it.

    Number boxes, drop-downs and sliders answer the wheel only after they were
    CLICKED (or reached with Tab), and only while the pointer is over them --
    Qt delivers a wheel notch to the control under the pointer, so a selected
    field elsewhere on the page is never touched. Moving the pointer away keeps
    the field selected (the keyboard still edits it); the wheel works on it
    again when the pointer comes back. Selecting another control ends it. An
    unselected field passes the notch to the panel behind it (the PV table, a
    settings page), which scrolls as expected. The focus a freshly opened
    dialog HANDS its first field does not count. Scroll bars are left out:
    they are sliders too, and the wheel is how a pane gets scrolled. Same rule
    as the other programs' guards.
    """

    _ARMED = "wheelReady"

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider,
                                       QAbstractSpinBox, QComboBox, QScrollBar)
        self._targets = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        self._scrollbar = QScrollBar
        self._scroll_area = QAbstractScrollArea
        self._earned = (Qt.FocusReason.MouseFocusReason,
                        Qt.FocusReason.TabFocusReason,
                        Qt.FocusReason.BacktabFocusReason,
                        Qt.FocusReason.ShortcutFocusReason)
        # Focus that leaves only for a moment and comes back to the same field:
        # another window was activated, or the drop-down's own list opened.
        self._keep = (Qt.FocusReason.ActiveWindowFocusReason,
                      Qt.FocusReason.PopupFocusReason)

    def _is_field(self, obj):
        return isinstance(obj, self._targets) and \
            not isinstance(obj, self._scrollbar)

    def _target_of(self, obj):
        """The guarded field ``obj`` belongs to: itself, or the spin box /
        combo whose inner line edit received the event."""
        w = obj
        for _ in range(3):
            if w is None or not isinstance(w, QWidget):
                return None
            if self._is_field(w):
                return w
            w = w.parentWidget()
        return None

    def eventFilter(self, obj, ev):
        """Every event in the program passes through here, so it must never
        raise: an exception escaping a Python override leaves PySide in a
        broken state and the process dies later (measured in Laser settings,
        2026-09-17), and ``obj`` may be half torn down. A stray scroll is the
        worst a swallowed error can cost."""
        try:
            return self._filter(obj, ev)
        except Exception:
            return False

    def _filter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Polish:
            # Born WheelFocus: Qt would hand the field focus from the wheel
            # itself, before this filter sees the notch.
            if self._is_field(obj) and \
                    obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            return False
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            # A click is what selects the field -- also the field that already
            # HAD the focus, where no FocusIn follows the click. The press lands
            # on the spin box's own text line, so look one or two levels up.
            tgt = self._target_of(obj)
            if tgt is not None:
                tgt.setProperty(self._ARMED, True)
            return False
        if t == QEvent.Type.FocusIn and self._is_field(obj):
            if ev.reason() in self._earned:
                obj.setProperty(self._ARMED, True)
            return False
        if t == QEvent.Type.FocusOut and self._is_field(obj):
            # The pointer leaving does not end it; selecting something else does.
            if ev.reason() not in self._keep:
                obj.setProperty(self._ARMED, False)
            return False
        if t != QEvent.Type.Wheel:
            return False
        if not self._is_field(obj):
            return False
        if (obj.property("wheelAlways")
                or (obj.property(self._ARMED) and obj.hasFocus())):
            return False                    # clicked into it -> normal behaviour
        w = obj.parentWidget()
        while w is not None:                # pass the scroll on to the panel
            if isinstance(w, self._scroll_area):
                QApplication.sendEvent(w.viewport(), ev)
                break
            w = w.parentWidget()
        return True


def install_wheel_guard(app=None):
    """Install _WheelGuard once for the whole application."""
    app = app or QApplication.instance()
    if app is None or getattr(app, "_wheel_guard", None) is not None:
        return
    guard = _WheelGuard(app)
    app._wheel_guard = guard
    app.installEventFilter(guard)


# ===========================================================================
# Main window
# ===========================================================================

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        ver = _app_version()
        self.setWindowTitle(f"Diagnostic {ver}" if ver else "Diagnostic (source)")
        # Fallback size for when the window is un-maximized; on launch the
        # window opens maximized (see main()). Wide enough that the PV table
        # shows all columns without a horizontal scrollbar.
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1720, screen.width() - 40),
                    min(950, screen.height() - 60))

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.North)

        # Lazy import: constructing the monitor tab pulls in matplotlib. Done
        # here so main() can show the splash first (see main()).
        from monitor_tab import MonitorWidget
        self._monitor_tab = MonitorWidget()
        tabs.addTab(self._monitor_tab, "PV Monitor")

        # Log stays the last tab — add any new tabs above this line.
        tabs.addTab(self._monitor_tab.log, "Log")

        self.setCentralWidget(tabs)

    def closeEvent(self, event):
        try:
            self._monitor_tab.shutdown()
        except Exception:
            pass
        super().closeEvent(event)


# ===========================================================================
# Entry point
# ===========================================================================

def _light_palette():
    """A light QPalette so Qt (and matplotlib's toolbar, which reads the palette
    to pick its icon color) match the light APP_STYLESHEET even when Windows is
    in dark mode. Without this, matplotlib sees a dark palette and paints the
    toolbar icons white -> invisible on our light background."""
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#f3f3f3"))
    pal.setColor(QPalette.WindowText, QColor("#111111"))
    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor("#f3f3f3"))
    pal.setColor(QPalette.Text, QColor("#111111"))
    pal.setColor(QPalette.Button, QColor("#e8e8e8"))
    pal.setColor(QPalette.ButtonText, QColor("#111111"))
    pal.setColor(QPalette.ToolTipBase, QColor("#ffffcc"))
    pal.setColor(QPalette.ToolTipText, QColor("#111111"))
    pal.setColor(QPalette.Highlight, QColor("#1565C0"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    return pal


def _icon_file() -> Path | None:
    """icon.ico sits next to the exe. APP_DIR is __file__-based, which in a
    frozen build points into the bundle rather than the exe folder."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    cands.append(APP_DIR / "icon.ico")
    for p in cands:
        if p.exists():
            return p
    return None


def _icon_app_id(prefix: str, ico_path) -> str | None:
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
    import os.path
    if not ico_path or not os.path.exists(str(ico_path)):
        return None
    import uuid
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


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


def main():
    _acquire_lock()
    atexit.register(_release_lock)

    # Give the taskbar button its own identity instead of grouping under the
    # generic host process, and hand it our icon. Must happen before the first
    # window exists, so the icon is resolved here rather than further down.
    # See _icon_app_id() for why the id is not a fixed string.
    _ico = _icon_file()
    _aumid = _icon_app_id("ELI.Diagnostic", _ico)
    if _aumid:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass

    app = QApplication(sys.argv)
    # Every background job in the app (PV polls, graph backfill, Webex polls,
    # channel lists, chart rendering) is a QRunnable on this one shared pool,
    # and Qt sizes it to the number of CPU cores. A runnable cannot be
    # cancelled, so any job that hangs on a network read holds its slot for the
    # rest of the session. On a small machine a handful of such zombies is
    # enough to leave no slot free, at which point every new job only queues
    # and the whole app looks frozen while the window still responds. Give the
    # pool plenty of headroom so a few stuck jobs stay harmless.
    from PySide6.QtCore import QThreadPool
    QThreadPool.globalInstance().setMaxThreadCount(
        max(32, QThreadPool.globalInstance().maxThreadCount()))
    if _ico:
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(_ico)))
    app.setStyle(ToolTipDelayStyle("Fusion"))
    app.setPalette(_light_palette())
    app.setStyleSheet(APP_STYLESHEET)
    install_wheel_guard(app)

    # Cold-start feedback: building MainWindow imports matplotlib (and touches
    # a large compiled dependency tree that Windows may re-scan on a cold file
    # cache), which can take tens of seconds. Paint a splash first and force it
    # on screen with processEvents() so the user isn't staring at nothing while
    # that happens — only PySide6 is loaded at this point, so the splash is
    # near-instant. It never takes the keyboard focus and does not sit above
    # other programs: the user may be typing elsewhere while this loads.
    splash = QLabel("Loading Diagnostics…")
    splash.setAlignment(Qt.AlignCenter)
    splash.setWindowFlags(Qt.SplashScreen | Qt.WindowDoesNotAcceptFocus)
    splash.setAttribute(Qt.WA_ShowWithoutActivating, True)
    splash.setFixedSize(360, 120)
    splash.setStyleSheet(
        "background:#f3f3f3; color:#1565C0; font-size:16px; font-weight:600; "
        "border:1px solid #ccc;")
    splash.show()
    app.processEvents()

    window = MainWindow()
    if _ico:
        from PySide6.QtGui import QIcon
        window.setWindowIcon(QIcon(str(_ico)))
    window.showMaximized()
    _nudge_taskbar_icon(int(window.winId()))
    splash.close()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
