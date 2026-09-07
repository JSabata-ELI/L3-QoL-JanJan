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
    # DEBUG — zapiš sys.path do souboru
    with open(Path(sys.executable).resolve().parent / "debug_syspath.txt", "w") as _f:
        _f.write("\n".join(sys.path))
    try:
        import PIL
        with open(Path(sys.executable).resolve().parent / "debug_pil.txt", "w") as _f:
            _f.write(f"PIL location: {PIL.__file__}\n")
            _f.write(f"PIL path: {PIL.__path__}\n")
    except Exception as e:
        with open(Path(sys.executable).resolve().parent / "debug_pil.txt", "w") as _f:
            _f.write(f"PIL import error: {e}\n")

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
    """Taskbar identity for `prefix`, tagged with the icon *and* this build.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it: an id that was once seen without a usable icon keeps drawing the
    generic placeholder for good, whatever icon the window later carries, and
    clearing the shell icon cache would have to be repeated on every PC.

    Hashing the icon's own bytes into the id was the first fix, but a
    content-only id can be poisoned just as well, and then it never recovers
    because it only changes when the picture is redrawn. Measured again on
    2026-09-03: Calibrations, CSS Logger, Git Work and Image Tools all drew
    the blank window placeholder on the taskbar while their title bars carried
    the right icon, and Diagnostic -- the only one whose id also carried its
    file name -- drew its icon. So the running build's own file name, which
    carries the version, goes into the hash too: every rebuild runs under an
    id Windows has never seen, so it cannot be serving a stale picture for it,
    on this PC or any other.

    Returns None when the icon cannot be read; the caller then sets no id at
    all rather than burning an id on a run that has no picture to give it.
    The same helper sits in every program here.
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
    if not ico_path:
        return None
    import hashlib
    import os
    import sys
    try:
        with open(ico_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    build = os.path.basename(sys.executable if getattr(sys, "frozen", False)
                             else (sys.argv[0] or __file__))
    tag = hashlib.sha1(data + b"\x00" + build.encode("utf-8", "replace"))
    return f"{prefix}.{tag.hexdigest()[:12]}"


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

    tabs = QTabWidget()
    tabs.setTabPosition(QTabWidget.TabPosition.North)
    tabs.setDocumentMode(True)   # cleaner look, similar to VS Code tabs

    finder = ImageFinderWidget()
    viewer = Viewer()
    shot_finder = ShotFinderWidget()
    workshop = WorkshopWidget()
    viewer.setWindowTitle("")    # title is handled by main window

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

    # Wire up Workshop — each tab gets a reference so it can send images
    workshop_idx = tabs.indexOf(workshop)
    finder._workshop_ref      = workshop
    finder._workshop_tab_idx  = workshop_idx
    finder._tab_widget        = tabs
    viewer._workshop_ref      = workshop
    viewer._workshop_tab_idx  = workshop_idx
    shot_finder._workshop_ref     = workshop
    shot_finder._workshop_tab_idx = workshop_idx
    # "Send moment" / "Send + cameras": the Image Finder finds the shot, the Slider
    # is where it gets slid through (viewer.open_moment). This used to be One
    # Moment's handoff.
    finder._slider_tab_idx = tabs.indexOf(viewer)

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
    tabs.setCornerWidget(btn_stop_all, Qt.Corner.TopRightCorner)

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

    if folder_arg is not None:
        QTimer.singleShot(200, lambda: _open_folder_in_slider(viewer, tabs, folder_arg))
    else:
        # On first activation of the Image Slider tab, auto-open Time window dialog
        # pre-set to online mode / current hour (fires only once).
        _slider_auto_started = [False]

        def _on_tab_changed(idx: int):
            if idx == 1 and not _slider_auto_started[0]:
                _slider_auto_started[0] = True
                QTimer.singleShot(0, viewer.auto_start_online)

        tabs.currentChanged.connect(_on_tab_changed)

    return win


def _open_folder_in_slider(viewer, tabs: QTabWidget, folder: Path):
    """Switch to Slider tab and load folder (called after startup)."""
    if folder.exists() and folder.is_dir():
        tabs.setCurrentIndex(1)
        viewer.open_folder_path(folder)


# ── entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Image Tools — Image Finder + Image Slider + Shot Finder + Workshop")
    ap.add_argument(
        "folder", nargs="?", default=None,
        help="Open this folder in Image Slider at startup"
    )
    args = ap.parse_args()
    folder_arg = Path(args.folder) if args.folder else None

    # Nastav AppUserModelID před vytvořením QApplication — Windows použije
    # toto ID pro groupování v taskbaru a zobrazení správné ikony.
    # See _icon_app_id() for why the id carries a hash of the icon.
    _aumid = _icon_app_id("ELIBeamlines.ImageTools", _icon_file())
    if _aumid:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    install_wheel_guard(app)

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
    """)

    win = build_main_window(folder_arg)
    win.showMaximized()   # start maximized; minimum size is 1000×800
    sys.exit(app.exec())


if __name__ == "__main__":
    main()