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

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QPushButton, QLabel, QStatusBar
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


# ── main window ───────────────────────────────────────────────────────────────
def build_main_window(folder_arg: Path | None = None) -> QMainWindow:
    """
    Build and return the main window.
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
        try:
            spec.loader.exec_module(mod)
        except Exception as _e:
            raise ImportError(f"{filename} failed to load:\n{_tb.format_exc()}") from _e
        sys.modules[module_name] = mod
        return mod

    try:
        _if = _load_module("image_finder", "if_t.py")
    except Exception as e:
        raise RuntimeError(f"if_t.py error: {e}") from e
    try:
        _is = _load_module("image_slider", "is_t.py")
    except Exception as e:
        raise RuntimeError(f"is_t.py error: {e}") from e
    try:
        _sf = _load_module("shot_finder", "sf_t.py")
    except Exception as e:
        raise RuntimeError(f"sf_t.py error: {e}") from e
    try:
        _wk = _load_module("workshop", "wk_t.py")
    except Exception as e:
        raise RuntimeError(f"wk_t.py error: {e}") from e
    ShotFinderWidget  = _sf.ShotFinderWidget
    ImageFinderWidget = _if.ImageFinderWidget
    Viewer            = _is.Viewer
    WorkshopWidget    = _wk.WorkshopWidget

    win = QMainWindow()
    win.setWindowTitle(APP_TITLE)
    try:
        from PySide6.QtGui import QIcon
        if getattr(sys, "frozen", False):
            _base = Path(sys.executable).resolve().parent
        else:
            _base = Path(__file__).resolve().parent
        _icon_path = _base / "icon.ico"
        if _icon_path.exists():
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

    win.setCentralWidget(tabs)

    # Stop All tlačítko ve status baru
    status_bar = QStatusBar()
    win.setStatusBar(status_bar)

    btn_stop_all = QPushButton("⏹ Stop All")
    btn_stop_all.setToolTip("Stop all running background operations")
    btn_stop_all.setStyleSheet(
        "QPushButton { background: #cc3300; color: #fff; font-weight: 700; "
        "padding: 3px 12px; border-radius: 3px; margin: 2px; }"
        "QPushButton:hover { background: #aa2200; }")

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
    status_bar.addPermanentWidget(btn_stop_all)

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
    ap = argparse.ArgumentParser(description="Image Tools — Image Finder + Image Slider")
    ap.add_argument(
        "folder", nargs="?", default=None,
        help="Open this folder in Image Slider at startup"
    )
    args = ap.parse_args()
    folder_arg = Path(args.folder) if args.folder else None

    app = QApplication.instance() or QApplication(sys.argv)
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