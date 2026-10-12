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


# Note: do NOT add a WM_SETICON / SetClassLongPtr "force taskbar icon" helper
# here. Measured on Win11: with the window icon and the window-class icon
# deliberately set to two different images, the taskbar draws the *window*
# icon, so Qt's setWindowIcon is already sufficient and forcing the class icon
# changes nothing. (That helper is only needed for the Tk apps, where
# iconbitmap leaves the small slots on Tk's default feather.)


# ── wheel guard ───────────────────────────────────────────────────────────────
def install_wheel_guard(app):
    """A value must never change just because the pointer crossed its control.

    Number fields, drop-downs and setting sliders answer the mouse wheel only
    once they have been CLICKED (i.e. they hold the keyboard focus). Until then
    the notch goes to the panel behind them instead, so a settings panel still
    scrolls when the pointer happens to pass over a field on the way down. A
    control that is meant to take the wheel at any time carries the "wheelAlways"
    property — the frame sliders of the Slider tab use it, because stepping
    through shots with the wheel is the whole point of them.

    Scroll bars are left out: they are sliders too, and the wheel is how a pane
    gets scrolled.

    One app-wide filter, so a dialog built much later is covered as well. It is
    spelled out in every entry point rather than imported once: each tab also
    runs on its own, and a sibling module would have to survive the frozen build
    (the same reason `_import_img_scale` is copied into each tab).
    """
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider,
                                   QAbstractSpinBox, QApplication, QComboBox,
                                   QScrollBar)

    class _WheelGuard(QObject):
        _GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        # Focus the user asked for. The focus a freshly opened window HANDS to its
        # first field (ActiveWindow / Other) does not count, or the top field of a
        # panel would answer the wheel before it had ever been touched.
        _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                   Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)
        _GIVEN = (Qt.FocusReason.ActiveWindowFocusReason,
                  Qt.FocusReason.OtherFocusReason)

        def eventFilter(self, obj, ev):
            t = ev.type()
            if t == QEvent.Type.Polish and isinstance(obj, self._GUARDED):
                # Spin boxes and drop-downs come with Qt's WheelFocus: the wheel
                # notch ITSELF hands them the focus, with the same "mouse" reason
                # a click gives, before this filter sees the notch -- so every
                # field the pointer crossed counted as clicked and took the
                # value change. Without WheelFocus only a click or Tab focuses.
                if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                    obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                return False
            if t == QEvent.Type.FocusIn and isinstance(obj, self._GUARDED):
                # Popup and menu reasons are left as they are: closing a drop-down
                # hands the focus back, which must not undo the click that opened it.
                if ev.reason() in self._EARNED:
                    obj.setProperty("wheelReady", True)
                elif ev.reason() in self._GIVEN:
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

    app.installEventFilter(_WheelGuard(app))


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
    strip_lay.setContentsMargins(6, 0, 0, 0)
    strip_lay.setSpacing(6)
    slider_box = QWidget()
    slider_box_lay = QHBoxLayout(slider_box)
    slider_box_lay.setContentsMargins(0, 0, 0, 0)
    slider_box_lay.setSpacing(6)
    sep = QFrame()
    sep.setFixedSize(1, 18)
    sep.setStyleSheet("background: #9aa6b6;")
    slider_box_lay.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
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

    The thresholds mimic Python's own (700 / 10 / 10)."""
    import gc
    gc.disable()

    def _tick():
        c0, c1, c2 = gc.get_count()
        if c2 >= 10:
            gc.collect(2)
        elif c1 >= 10:
            gc.collect(1)
        elif c0 >= 700:
            gc.collect(0)

    t = QTimer(app)
    t.setInterval(250)
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
    sys.exit(app.exec())


if __name__ == "__main__":
    main()