import re
import sys
import argparse
from pathlib import Path

if getattr(sys, "frozen", False):
    _internal = Path(sys.executable).resolve().parent / "_internal"
    # Odstraň uživatelské site-packages které by mohly přebít _internal
    sys.path = [p for p in sys.path if "site-packages" not in p.lower()]
    if str(_internal) not in sys.path:
        sys.path.insert(0, str(_internal))

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QPushButton
from PySide6.QtCore import Qt, QTimer

# ── version from exe name ─────────────────────────────────────────────────────
_VER_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)

def _detect_version() -> str:
    try:
        name = Path(sys.executable).name if getattr(sys, "frozen", False) \
               else Path(__file__).name
        m = _VER_RE.search(name)
        if m:
            return f"v{m.group(1)}.{m.group(2)}.{m.group(3)}"
    except Exception:
        pass
    return ""

APP_VERSION = _detect_version()
APP_TITLE   = f"Image Tools {APP_VERSION}".strip()


# ── icon helpers ──────────────────────────────────────────────────────────────
def _icon_file() -> Path | None:
    """Locate icon.ico next to the exe (frozen) or the script, with a
    PyInstaller _MEIPASS fallback for one-file builds."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    else:
        cands.append(Path(__file__).resolve().parent / "icon.ico")
    for p in cands:
        if p.exists():
            return p
    return None


def _icon_app_id(prefix, ico_path):
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


# Note: do NOT add a WM_SETICON / SetClassLongPtr "force taskbar icon" helper
# here. Measured on Win11: with the window icon and the window-class icon
# deliberately set to two different images, the taskbar draws the *window*
# icon, so Qt's setWindowIcon is already sufficient and forcing the class icon
# changes nothing. (That helper is only needed for the Tk apps, where
# iconbitmap leaves the small slots on Tk's default feather.)
# _nudge_taskbar_icon above is a different thing: it sets no new picture, it
# only takes the window's own icon away and back so Explorer reads it again.


# ── wheel guard ───────────────────────────────────────────────────────────────
def install_wheel_guard(app):
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

    A control meant to take the wheel at any time carries the "wheelAlways"
    property -- the frame sliders set it, because stepping through shots with
    the wheel is the whole point of them. Scroll bars are left out: they are
    sliders too, and the wheel is how a pane gets scrolled.

    One app-wide filter, installed once, so a dialog built much later is
    covered as well. It is spelled out in every entry point rather than
    imported once: each tab also runs on its own, and a sibling module would
    have to survive the frozen build (the same reason `_import_img_scale` is
    copied into each tab). The copies in main.py, is_t.py, if_t.py and sf_t.py
    are identical (testing/test_wheel_guard.py checks it).
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


# ── main window ───────────────────────────────────────────────────────────────
def build_main_window(folder_arg: Path | None = None) -> QMainWindow:
    """
    Build and return the main window.že to 
    Separated from main() so it can be called from tests or other scripts.
    """
    # if.py and is.py cannot be imported with normal 'import' because
    # 'if' and 'is' are Python keywords. We use importlib instead.
    import importlib.util, traceback as _tb

    def _load_module(module_name: str, filename: str):
        here = (Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent)
        full_path = here / filename
        spec = importlib.util.spec_from_file_location(module_name, full_path)
        if spec is None:
            raise ImportError(f"Cannot find module file: {full_path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as _e:
            sys.modules.pop(module_name, None)
            raise ImportError(f"{filename} failed to load:\n{_tb.format_exc()}") from _e
        return mod

    # Load the slider FIRST: if_t/sf_t borrow its helpers (GRADIENTS, PNG
    # metadata copy) via sys.modules["image_slider"] — loading it first means
    # they reuse this instance instead of exec'ing is_t.py a second time.
    try:
        _is = _load_module("image_slider", "is_t.py")
    except Exception as e:
        raise RuntimeError(f"is_t.py error: {e}") from e
    try:
        _if = _load_module("image_finder", "if_t.py")
    except Exception as e:
        raise RuntimeError(f"if_t.py error: {e}") from e
    try:
        _sf = _load_module("shot_finder", "sf_t.py")
    except Exception as e:
        raise RuntimeError(f"sf_t.py error: {e}") from e
    try:
        _wk = _load_module("workshop", "wk_t.py")
    except Exception as e:
        raise RuntimeError(f"wk_t.py error: {e}") from e
    try:
        _st = _load_module("slider_tabs", "slider_tabs.py")
    except Exception as e:
        raise RuntimeError(f"slider_tabs.py error: {e}") from e
    # One Moment is GONE, merged into the Image Finder (2026-09-04). Everything it
    # did lives there now: click the PV graph to pick moments — every click adds one
    # more, on any marked day — and the wall shows every picked camera at every
    # picked moment, with the range statistics, the formulas over time, the
    # prev/next shot arrows and the two Send-to-Slider flavours in the Finder's own
    # panel. `om_t.py` was deleted; its design spec is folded into if_t.py's header.
    ShotFinderWidget  = _sf.ShotFinderWidget
    ImageFinderWidget = _if.ImageFinderWidget
    Viewer            = _is.Viewer
    WorkshopWidget    = _wk.WorkshopWidget

    win = QMainWindow()
    win.setWindowTitle(APP_TITLE)
    try:
        from PySide6.QtGui import QIcon
        _icon_path = _icon_file()
        if _icon_path:
            win.setWindowIcon(QIcon(str(_icon_path)))
    except Exception:
        pass
    win.setMinimumSize(800, 1000)

    class _MainTabs(QTabWidget):
        def paintEvent(self, _ev):
            # In document mode all QTabWidget paints is a tab-bar base line under
            # each corner widget — with the wide tab-row strip below that came out
            # as a stray dark line over its left half. Nothing else is lost.
            pass

    tabs = _MainTabs()
    tabs.setTabPosition(QTabWidget.TabPosition.North)
    tabs.setDocumentMode(True)   # cleaner look, similar to VS Code tabs

    finder = ImageFinderWidget()
    # The Image Slider tab holds layout tabs, one Viewer each (slider_tabs.py). The
    # other tabs talk to it exactly as they talked to the single Viewer before.
    viewer = _st.SliderTabs(Viewer)
    shot_finder = ShotFinderWidget()
    workshop = WorkshopWidget()

    # The Finder's job is comparing ONE camera across MANY days — the transpose of the
    # Slider, which compares many cameras at one moment. The tab keeps the name the
    # operators know it by: "Image Finder".
    tabs.addTab(finder, "Image Finder")
    tabs.addTab(viewer, "Image Slider")
    tabs.addTab(shot_finder, "Shot Finder")

    tabs.addTab(workshop, "Workshop")

    # Wire up the integration: finder can switch to slider tab and load folder
    finder._slider_ref  = viewer
    finder._tab_widget  = tabs
    shot_finder._slider_ref = viewer
    shot_finder._btn_open_slider.setVisible(True)
    shot_finder._tab_widget = tabs

    # ...and the other way round: the Slider and the Shot Finder hand a MOMENT to
    # the Image Finder ("Send to Image Finder"), which is the tab that puts one
    # instant side by side for every camera and every day. Nothing is copied — the
    # Finder looks the frames up itself (finder.open_moments).
    finder_idx = tabs.indexOf(finder)
    workshop_idx = tabs.indexOf(workshop)
    viewer.inject(_finder_ref=finder, _finder_tab_idx=finder_idx, _tab_widget=tabs,
                  _workshop_ref=workshop, _workshop_tab_idx=workshop_idx)
    shot_finder._finder_ref     = finder
    shot_finder._finder_tab_idx = finder_idx

    # Wire up Workshop — each tab gets a reference so it can send images
    finder._workshop_ref      = workshop
    finder._workshop_tab_idx  = workshop_idx
    finder._tab_widget        = tabs
    shot_finder._workshop_ref     = workshop
    shot_finder._workshop_tab_idx = workshop_idx
    # "Send moment" / "Send + cameras": the Image Finder finds the shot, the Slider
    # is where it gets slid through (viewer.open_moment). This used to be One
    # Moment's handoff.
    finder._slider_tab_idx = tabs.indexOf(viewer)
    shot_finder._slider_tab_idx = tabs.indexOf(viewer)

    # ...and the way back: "Show in Image Slider" opens the folder a Workshop frame
    # came from. Only the folder — the Slider browses files on the share, so an edited
    # picture is not something it can be handed.
    workshop._slider_ref     = viewer
    workshop._slider_tab_idx = tabs.indexOf(viewer)
    workshop._tab_widget     = tabs

    # The program's own tabs: big, blue, dark-rimmed all round, so they read apart
    # from the Slider's layout tabs (same shape, smaller and pale grey) that share
    # the row. On the bar only: tabs inside the panels keep the app style.
    tabs.tabBar().setStyleSheet(
        "QTabBar::tab{background:#E3F2FD;color:#1565C0;border:2px solid #0D47A1;"
        "border-radius:0px;border-top-left-radius:4px;border-top-right-radius:4px;"
        "padding:6px 18px;margin:0px 2px 2px 0px;font-weight:600;}"
        "QTabBar::tab:selected{background:#1565C0;color:white;}"
        "QTabBar::tab:hover:!selected{background:#c6e0fa;}")

    win.setCentralWidget(tabs)

    # Stop All tlačítko v řádku záložek (corner widget) — status bar se nepoužívá,
    # takže tabs vyplní celou výšku okna až po spodní hranu.
    btn_stop_all = QPushButton("⏹ Stop All")
    btn_stop_all.setToolTip("Stop all running background operations")
    btn_stop_all.setStyleSheet(
        "QPushButton { background: #cc3300; color: #fff; font-weight: 700; "
        "padding: 3px 12px; border-radius: 3px; margin: 2px; }"
        "QPushButton:hover { background: #aa2200; }")
    # The tab row: main tabs | the Image Slider's layout tabs (only while the Slider
    # is on screen) ... Stop All at the far right. QTabWidget gives a corner widget
    # exactly its size hint, so the strip asks for the whole gap after the main tabs.
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget
    from PySide6.QtCore import QSize

    class _TabRowStrip(QWidget):
        def sizeHint(self):
            # Exactly the gap — many layout tabs scroll inside it rather than
            # squeezing the main tabs.
            base = super().sizeHint()
            bar = tabs.tabBar()
            w = tabs.width() - bar.sizeHint().width() - 2
            return QSize(max(self.minimumSizeHint().width(), w),
                         max(base.height(), bar.sizeHint().height()))

    strip = _TabRowStrip()
    strip_lay = QHBoxLayout(strip)
    strip_lay.setContentsMargins(14, 0, 0, 0)
    strip_lay.setSpacing(6)
    # The layout tabs stand on the main tabs' bottom line, after a gap. Their
    # holder has no frame of its own; it is only shown/hidden with the Slider.
    slider_box = QFrame()
    slider_box.setObjectName("sessionTray")
    # The app-wide `QWidget { background }` would paint the bar and its holders
    # as grey slabs; the scroll arrows keep the window colour so no tab shows
    # through them.
    slider_box.setStyleSheet("#sessionTray{background:transparent;border:none;}"
                             "#sessionTray QWidget{background:transparent;}"
                             "#sessionTray QTabBar QToolButton{background:#f3f3f3;}")
    slider_box_lay = QHBoxLayout(slider_box)
    slider_box_lay.setContentsMargins(0, 0, 0, 0)
    slider_box_lay.setSpacing(0)
    strip_lay.addWidget(slider_box, 0, Qt.AlignmentFlag.AlignBottom)
    strip_lay.addStretch(1)
    strip_lay.addWidget(btn_stop_all, 0, Qt.AlignmentFlag.AlignVCenter)
    tabs.setCornerWidget(strip, Qt.Corner.TopRightCorner)
    viewer.move_chrome_into(slider_box_lay)

    def _sync_tab_row(idx):
        slider_box.setVisible(tabs.widget(idx) is viewer)
        # The bold current tab changes the main bar's width: re-ask for the gap.
        strip.updateGeometry()
    _sync_tab_row(tabs.currentIndex())

    def _stop_all():
        try: finder.cancel_scan()
        except Exception: pass
        try: viewer.cancel_scan()
        except Exception: pass
        try: viewer.stop()
        except Exception: pass
        try:
            shot_finder._search_running = False
            shot_finder._btn_search.setEnabled(True)
            shot_finder._prog.setVisible(False)
            shot_finder._result_lbl.setText("Stopped.")
        except Exception: pass

    btn_stop_all.clicked.connect(_stop_all)

    # Only the layout tab on screen works, and only while the Image Slider itself is
    # the tab on screen. The first time it is shown, its current layout tab loads
    # what it had before the restart — or opens the Time window dialog.
    tabs.currentChanged.connect(
        lambda idx: viewer.set_outer_visible(tabs.widget(idx) is viewer))
    tabs.currentChanged.connect(_sync_tab_row)

    if folder_arg is not None:
        QTimer.singleShot(200, lambda: _open_folder_in_slider(viewer, tabs, folder_arg))

    return win


def _open_folder_in_slider(viewer, tabs: QTabWidget, folder: Path):
    """Switch to Slider tab and load folder (called after startup)."""
    if folder.exists() and folder.is_dir():
        viewer.open_folder_path(folder)
        tabs.setCurrentWidget(viewer)


# ── crash log ─────────────────────────────────────────────────────────────────
_CRASH_LOG_FH = None      # kept open for the whole run: faulthandler writes to the fd


def install_crash_log() -> None:
    """On a hard crash (access violation inside Qt), write the Python stack of
    EVERY thread to %APPDATA%\\ELI_ImageTools\\crash.log.

    These crashes leave no traceback and the Windows crash dump holds no heap, so
    the dump alone cannot say which Python line was running — the crash of
    5 Oct 2026 could only be narrowed down to "a worker thread freed a widget".
    With this log the next one names the thread and the line."""
    global _CRASH_LOG_FH
    import faulthandler
    import os
    from datetime import datetime
    try:
        d = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "crash.log"
        # Keep the log small: start over once it passes 1 MB.
        mode = "w" if p.exists() and p.stat().st_size > 1_000_000 else "a"
        fh = open(p, mode, encoding="utf-8")
        fh.write(f"\n=== Image Tools {_detect_version()} started "
                 f"{datetime.now():%Y-%m-%d %H:%M:%S} (pid {os.getpid()}) ===\n")
        fh.flush()
        faulthandler.enable(file=fh, all_threads=True)
        _CRASH_LOG_FH = fh
    except Exception:
        pass


# ── garbage collection on the GUI thread only ────────────────────────────────
def install_gui_thread_gc(app) -> None:
    """Run Python's cycle collector from a GUI-thread timer instead of letting it
    fire wherever an allocation happens to cross the threshold.

    Measured from the crash dump of 2 Oct 2026 (python.exe.17892.dmp): a plain
    Python worker thread did an attribute assignment, the
    allocation ran the collector, and the collector freed a cycle holding a
    Python-owned widget tree with a table view inside. Its C++ destructor then
    ran on the worker thread and crashed in QObject::disconnect. Any thread can
    trigger an automatic collection, so the only safe place for it is here.

    The thresholds mimic Python's own (700 / 10 / 10) — INCLUDING its brake on full
    collections, which the first version left out. CPython runs a full (generation 2)
    pass only once the objects promoted since the last one exceed a quarter of those
    that survived it; without that brake a long live session swept the whole heap from
    the GUI thread every ~25 s, and the sweep grows with the heap.

    The same timer measures the GUI thread: a tick that arrives late means the event
    loop was blocked that long. Both numbers land in app._gui_gc_stats, which the
    Slider's per-minute diag line reads and resets (stall=, gcMs=, gcFull=)."""
    import gc
    import time as _time
    gc.disable()

    stats = {"stall_max": 0.0, "gc_ms": 0.0, "gc_max_ms": 0.0, "full": 0,
             "long_lived": 0}
    state = {"last": _time.monotonic(), "pending": 0, "total": 0}
    app._gui_gc_stats = stats
    interval_s = 0.25

    def _timed(gen: int):
        t0 = _time.perf_counter()
        gc.collect(gen)
        ms = (_time.perf_counter() - t0) * 1000.0
        stats["gc_ms"] += ms
        stats["gc_max_ms"] = max(stats["gc_max_ms"], ms)

    def _tick():
        now = _time.monotonic()
        stats["stall_max"] = max(stats["stall_max"], now - state["last"] - interval_s)
        state["last"] = now
        c0, c1, c2 = gc.get_count()
        if c2 >= 10 and state["pending"] > state["total"] // 4:
            _timed(2)
            stats["full"] += 1
            state["pending"] = 0
            # One list build per FULL pass (minutes apart), not per minute.
            state["total"] = stats["long_lived"] = len(gc.get_objects(2))
        elif c1 >= 10:
            # Everything still alive in generation 1 is promoted to 2 by this pass —
            # the "pending" count CPython's brake is made of. Generation 1 is small.
            state["pending"] += len(gc.get_objects(1))
            _timed(1)
        elif c0 >= 700:
            _timed(0)

    t = QTimer(app)
    t.setInterval(int(interval_s * 1000))
    t.timeout.connect(_tick)
    t.start()
    app._gui_gc_timer = t


# ── entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Image Tools — Image Finder + Image Slider + Shot Finder + Workshop")
    ap.add_argument(
        "folder", nargs="?", default=None,
        help="Open this folder in Image Slider at startup"
    )
    args = ap.parse_args()
    folder_arg = Path(args.folder) if args.folder else None
    install_crash_log()

    # Nastav AppUserModelID před vytvořením QApplication — Windows použije
    # toto ID pro groupování v taskbaru a zobrazení správné ikony.
    # See _icon_app_id() for why the id is new on every launch.
    _aumid = _icon_app_id("ELIBeamlines.ImageTools", _icon_file())
    if _aumid:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    install_wheel_guard(app)
    install_gui_thread_gc(app)

    # Nastav ikonu na úrovni aplikace — platí pro taskbar i alt-tab
    _ico = _icon_file()
    try:
        from PySide6.QtGui import QIcon
        if _ico:
            app.setWindowIcon(QIcon(str(_ico)))
    except Exception:
        pass

    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget      { background: #f3f3f3; color: #111; }
        QLabel       { background: transparent; }
        QPushButton  { padding: 5px 8px; }
        /* `QWidget { color: #111 }` above applies to a DISABLED widget too — a
           stylesheet overrides the palette, disabled state included — so a greyed-out
           button came out in full black text with only a paler border, i.e. it looked
           exactly like a live one and clicking it simply did nothing. Measured on the
           Image Slider's Remove ref button: 26/255 of difference, all of it in the
           border. Same rule, same reason, as _CHECKBOX_STYLE in is_t.py. A button that
           paints itself (Stop All, Delete mode) sets its own stylesheet and is
           unaffected. */
        QPushButton:disabled { color: #9a9a9a; border-color: #d0d0d0; }
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
        QToolTip {
            background: #ffffcc; color: #111;
            border: 1px solid #aaa; padding: 4px;
        }
        /* Scroll bars, app wide. The plain ones are a pale grey sliver on a pale
           grey panel: nothing to see and little to grab. This is the Workshop bar
           — a track that is visibly a track, a handle dark enough to read against
           it, and no end arrows, which are two more tiny targets nobody uses.
           Anything that sets its own bar (the tab panels do) overrides this. */
        QScrollBar:vertical {
            background: #d8dce2; width: 16px; margin: 0px; border: none;
        }
        QScrollBar::handle:vertical {
            background: #6c7580; min-height: 28px; border-radius: 4px; margin: 2px;
        }
        QScrollBar::handle:vertical:hover   { background: #4a5566; }
        QScrollBar::handle:vertical:pressed { background: #2f3a49; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px; background: none; border: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }
        QScrollBar:horizontal {
            background: #d8dce2; height: 16px; margin: 0px; border: none;
        }
        QScrollBar::handle:horizontal {
            background: #6c7580; min-width: 28px; border-radius: 4px; margin: 2px;
        }
        QScrollBar::handle:horizontal:hover   { background: #4a5566; }
        QScrollBar::handle:horizontal:pressed { background: #2f3a49; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px; background: none; border: none;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: none;
        }
    """)

    win = build_main_window(folder_arg)
    win.showMaximized()   # start maximized; minimum size is 1000×800
    _nudge_taskbar_icon(int(win.winId()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()