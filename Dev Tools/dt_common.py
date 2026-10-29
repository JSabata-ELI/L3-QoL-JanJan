# dt_common.py
"""Qt pieces shared by both tabs: look, log, thread hand-over, dialogs."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

import dt_core as core

# ----------------- LOOK (copied from Image Tools) -----------------
# The house look of Image Tools, copied and not imported — Image Tools copies
# these constants between its own modules the same way. Every colour stated:
# Windows here runs in dark mode, and anything left to the theme comes out
# black on black.

ACCENT = "#2d7dff"          # Image Tools' blue: ticks, accent buttons, hover
SELECT_BG = "#cfe0f7"       # selected row / popup item
SELECT_INK = "#10243c"

# Section accents, the same colours Image Tools gives its panels.
SEC_SOURCE = "#2f6fd0"      # blue
SEC_SAVE = "#c0392b"        # red
SEC_TIMELINE = "#2e9e5b"    # green
SEC_DISPLAY = "#7a4fc0"     # purple
SEC_PVS = "#1a9e9e"         # teal

# Image Tools/main.py — the application sheet, scroll bars included.
_IT_APP_QSS = """
QWidget      { background: #f3f3f3; color: #111; }
QLabel       { background: transparent; }
QPushButton  { padding: 5px 8px; }
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
"""

# Workshop's _BTN_QSS (wk_t.py) for every button; the accent button is Image
# Slider's Apply (is_t.py), with a disabled state of its own.
BTN_QSS = """
QPushButton { padding: 4px 9px; border: 1px solid #b6b6b6; border-radius: 3px;
    background: #efefef; color: #111; }
QPushButton:hover { background: #d9e8ff; }
QPushButton:pressed { background: #b9d0f5; }
QPushButton:disabled { background: #f4f4f4; color: #9a9a9a; border-color: #dcdcdc; }
QPushButton:default { border: 2px solid #2d7dff; }
QPushButton#primary { background: #2d7dff; color: #fff; font-weight: 700;
    border: 1px solid #1a6aee; border-radius: 3px; padding: 4px 18px; }
QPushButton#primary:hover { background: #1a6aee; }
QPushButton#primary:pressed { background: #1458c8; }
QPushButton#primary:disabled { background: #a9c6ee; color: #eef4fc; border-color: #a9c6ee; }
"""

# Shot Finder's combo (sf_t.py) with the popup colours of its small combo.
COMBO_QSS = f"""
QComboBox {{ padding: 3px 6px; background: #fff; color: #16202c;
    border: 1px solid #ccc; border-radius: 4px; }}
QComboBox:disabled {{ background: #ededed; color: #808890; }}
QComboBox QAbstractItemView {{ background: #ffffff; color: #16202c;
    selection-background-color: {SELECT_BG}; selection-color: {SELECT_INK}; }}
"""

# Image Slider's _CHECKBOX_STYLE (is_t.py).
CHECKBOX_QSS = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; background: transparent; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
QCheckBox:disabled { color: #9a9a9a; }
QCheckBox::indicator:disabled { border: 2px solid #c9c9c9; background: #f0f0f0; }
QCheckBox::indicator:checked:disabled { border: 2px solid #a9c6ee; background: #a9c6ee; }
"""

# Shot Finder's _TABLE_QSS (sf_t.py) for the program lists, and the tick boxes
# of Image Finder's camera list (if_t.py) for the ticks inside them.
TREE_QSS = f"""
QTreeWidget {{ background: #ffffff; alternate-background-color: #f5f7fa;
    color: #16202c; border: 1px solid #b0b0b0;
    selection-background-color: {SELECT_BG}; selection-color: {SELECT_INK}; }}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:selected {{ background: {SELECT_BG}; color: {SELECT_INK}; }}
QHeaderView::section {{ background: #eef1f5; color: #16202c; font-weight: 600;
    border: 0px; border-right: 1px solid #dfe4ea;
    border-bottom: 1px solid #cfd6de; padding: 4px 6px; }}
QHeaderView {{ background: #eef1f5; }}
QTreeView::indicator {{ width: 15px; height: 15px;
    border: 2px solid #4a4a4a; border-radius: 3px; background: #ffffff; }}
QTreeView::indicator:hover {{ border: 2px solid #2d7dff; background: #f4f8ff; }}
"""

OTHER_QSS = f"""
QLineEdit {{ background: #ffffff; color: #111; border: 1px solid #9aa3ad;
    border-radius: 2px; padding: 2px 4px; }}
QLineEdit[readOnly="true"] {{ background: #eef1f5; color: #333; }}
QPlainTextEdit {{ background: #ffffff; color: #111; border: 1px solid #b0b0b0;
    font-family: Consolas, monospace; }}
QProgressBar {{ color: #111; border: 1px solid #b0b0b0; border-radius: 2px;
    text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QDialog, QMessageBox {{ background: #f4f5f7; }}
QMenu {{ background: #ffffff; color: #16202c; border: 1px solid #c3c9d2; padding: 3px; }}
QMenu::item {{ padding: 5px 22px 5px 22px; }}
QMenu::item:selected {{ background: {SELECT_BG}; color: {SELECT_INK}; }}
"""

# The program tabs (Builder / Copy Manager): Image Tools' big blue main tabs.
MAIN_TABS_QSS = (
    "QTabBar::tab{background:#E3F2FD;color:#1565C0;border:2px solid #0D47A1;"
    "border-radius:0px;border-top-left-radius:4px;border-top-right-radius:4px;"
    "padding:6px 18px;margin:0px 2px 2px 0px;font-weight:600;}"
    "QTabBar::tab:selected{background:#1565C0;color:white;}"
    "QTabBar::tab:hover:!selected{background:#c6e0fa;}")


def _tick_image_url() -> str:
    """The white tick inside a ticked list box (Image Finder's _tick_image_url).

    A stylesheet that sizes the indicator takes it over completely, tick
    included, so the tick has to be a picture. Drawn once into %TEMP%.
    """
    import tempfile
    from PySide6.QtGui import QPainter, QPen
    path = Path(tempfile.gettempdir()) / "dt_tick_w.png"
    try:
        if not path.exists():
            pm = QPixmap(15, 15)
            pm.fill(QColor(0, 0, 0, 0))
            pt = QPainter(pm)
            pt.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(QColor("#ffffff"))
            pen.setWidthF(2.6)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            pt.setPen(pen)
            pt.drawPolyline([QPointF(3.0, 8.0), QPointF(6.2, 11.4), QPointF(12.0, 4.0)])
            pt.end()
            pm.save(str(path), "PNG")
    except Exception:
        return ""
    return f' image: url("{path.as_posix()}");' if path.exists() else ""


def app_stylesheet() -> str:
    """The whole sheet. Needs a QApplication (draws the tick picture)."""
    # The same white tick in the stand-alone tick boxes: a plain blue square
    # beside a ticked list row read as a different kind of control.
    mark = _tick_image_url()
    tick = (f"QTreeView::indicator:checked {{ border: 2px solid #0D47A1;"
            f" background: {ACCENT};{mark} }}"
            f"QCheckBox::indicator:checked {{{mark} }}")
    return (_IT_APP_QSS + BTN_QSS + COMBO_QSS + CHECKBOX_QSS + TREE_QSS
            + tick + OTHER_QSS)


def apply_app_style(app) -> None:
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(app_stylesheet())


def _shade(hex_colour: str, f: float) -> str:
    """f < 1 darkens toward black, f > 1 lightens toward white."""
    c = QColor(hex_colour)
    out = []
    for v in (c.red(), c.green(), c.blue()):
        out.append(round(v * f) if f < 1 else round(v + (255 - v) * min(1.0, f - 1)))
    return "#%02x%02x%02x" % tuple(max(0, min(255, v)) for v in out)


class Section(QWidget):
    """A panel with Image Tools' section look (CollapsibleSection, is_t.py):
    a coloured uppercase header bar and a body with a stripe in the same
    colour. It does not fold — Dev Tools' panels are always needed."""

    def __init__(self, title: str, accent: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QLabel(title.upper())
        head.setStyleSheet(
            "QLabel { border-radius: 4px; border-bottom-left-radius: 0px;"
            " border-bottom-right-radius: 0px; padding: 7px 9px;"
            " font-weight: 700; font-size: 11px; letter-spacing: 1px;"
            f" color: #fff; background: {accent}; }}")
        lay.addWidget(head)
        self.body = QWidget()
        self.body.setObjectName("secBody")
        self.body.setAttribute(Qt.WA_StyledBackground, True)
        self.body.setStyleSheet(
            f"#secBody {{ border-left: 3px solid {_shade(accent, 1.35)};"
            f" background: {_shade(accent, 1.93)};"
            " border-bottom-right-radius: 4px; }")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(8, 6, 6, 7)
        self.body_layout.setSpacing(6)
        lay.addWidget(self.body, 1)


def light_palette() -> QPalette:
    pal = QPalette()
    for role, colour in ((QPalette.Window, "#f3f3f3"), (QPalette.WindowText, "#111111"),
                         (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f7f7f7"),
                         (QPalette.Text, "#111111"), (QPalette.Button, "#e8e8e8"),
                         (QPalette.ButtonText, "#111111"), (QPalette.ToolTipBase, "#ffffcc"),
                         (QPalette.ToolTipText, "#111111"), (QPalette.Highlight, "#1565C0"),
                         (QPalette.HighlightedText, "#ffffff"),
                         (QPalette.PlaceholderText, "#777777")):
        pal.setColor(role, QColor(colour))
    return pal


# ----------------- THREAD HAND-OVER -----------------
class _Invoker(QObject):
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @staticmethod
    def _run(fn):
        fn()


_invoker: _Invoker | None = None


def init_ui_thread():
    """Create the hand-over object on the UI thread (call once, after QApplication)."""
    global _invoker
    if _invoker is None:
        _invoker = _Invoker()


def on_ui(fn: Callable, *args, **kwargs):
    """Run fn on the UI thread — from any thread. Widgets may only be touched there."""
    _invoker.call.emit(lambda: fn(*args, **kwargs))


def ask_on_ui(fn: Callable, *args):
    """Run fn on the UI thread and wait for its answer (a worker asking a question)."""
    if threading.current_thread() is threading.main_thread():
        return fn(*args)
    box = {}
    done = threading.Event()

    def _call():
        try:
            box["v"] = fn(*args)
        finally:
            done.set()
    on_ui(_call)
    done.wait()
    return box.get("v")


# ----------------- LOG -----------------
class LogHub(QObject):
    """One log shown in every registered pane, fed from any thread.

    A build prints thousands of lines; each one written on its own made the
    window lag behind the build. Lines are queued and written every 40 ms in one
    go. A pane follows the end only while its view is at the bottom, so reading
    further up is not yanked away — a final report (force) always scrolls.
    """

    FLUSH_MS = 40
    MAX_LINES = 50000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._force = False
        self._views: list[QPlainTextEdit] = []
        self._timer = QTimer(self)
        self._timer.setInterval(self.FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def new_view(self, parent=None) -> QPlainTextEdit:
        view = QPlainTextEdit(parent)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        view.setMaximumBlockCount(self.MAX_LINES)
        view.setFont(QFont("Consolas", 8))
        if self._views:
            view.setPlainText(self._views[0].toPlainText())
        self._views.append(view)
        return view

    def write(self, text: str, force_scroll: bool = False):
        print(text)
        with self._lock:
            self._pending.append(text)
            if force_scroll:
                self._force = True

    def clear(self):
        with self._lock:
            self._pending.clear()
        for v in self._views:
            v.clear()

    def _flush(self):
        with self._lock:
            if not self._pending:
                return
            text = "\n".join(self._pending)
            self._pending.clear()
            force, self._force = self._force, False
        for v in self._views:
            bar = v.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 2
            hbar = v.horizontalScrollBar()
            hpos = hbar.value()
            v.appendPlainText(text)
            hbar.setValue(hpos)
            if at_end or force:
                bar.setValue(bar.maximum())


def log_box(hub: LogHub, title: str = "Log") -> "Section":
    box = Section(title, SEC_PVS)
    lay = box.body_layout
    lay.addWidget(hub.new_view(box), 1)
    row = QHBoxLayout()
    clear = QPushButton("Clear")
    clear.clicked.connect(hub.clear)
    row.addWidget(clear)
    row.addStretch(1)
    lay.addLayout(row)
    return box


# ----------------- SMALL HELPERS -----------------
class WheelGuard(QObject):
    """A wheel turn changes a combo box only after it was clicked into.

    Otherwise scrolling the list over a Version box silently changed which
    version would be copied (the same trap the Tk version guarded against).
    The turn goes on to the list underneath instead."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and isinstance(obj, QComboBox) and not obj.hasFocus():
            event.ignore()
            return True
        return False


_wheel_guard = None


def guard_wheel(combo: QComboBox):
    global _wheel_guard
    if _wheel_guard is None:
        _wheel_guard = WheelGuard()
    combo.setFocusPolicy(Qt.StrongFocus)
    combo.installEventFilter(_wheel_guard)


def wrap_path(path: str) -> str:
    """Let a long path wrap at its separators instead of stretching the window."""
    return path.replace("\\", "\\​").replace("/", "/​")


def message(parent, kind: str, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setIcon({"info": QMessageBox.Information, "warning": QMessageBox.Warning,
                 "error": QMessageBox.Critical}[kind])
    box.setWindowTitle(title)
    box.setText(text)
    box.exec()


def ask_yes_no(parent, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


def ask_dist_missing(parent, dist: Path) -> str:
    """'create', 'choose' or 'cancel'."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("dist folder missing")
    box.setText(f"Output folder does not exist:\n{dist}")
    create = box.addButton("Create it", QMessageBox.AcceptRole)
    choose = box.addButton("Choose another folder", QMessageBox.ActionRole)
    box.addButton("Cancel build", QMessageBox.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    return "create" if clicked is create else "choose" if clicked is choose else "cancel"


# ----------------- DIALOGS -----------------
def icon_compare_dialog(parent, program: str, existing: Path, incoming: Path) -> bool:
    """Two icons side by side. True = replace, False = keep the existing one."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"Icon conflict – {program}")
    lay = QVBoxLayout(dlg)
    head = QLabel(f"Program:  {program}")
    head.setStyleSheet("font-weight: bold; font-size: 10pt;")
    lay.addWidget(head, 0, Qt.AlignHCenter)
    lay.addWidget(QLabel("Icon already exists with a different file size. Replace it?"),
                  0, Qt.AlignHCenter)
    pics = QHBoxLayout()
    for caption, path in (("Existing", existing), ("Incoming", incoming)):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        box = Section(f"{caption}  ({size} B)", SEC_SOURCE if caption == "Existing" else SEC_TIMELINE)
        bl = box.body_layout
        img = QLabel()
        img.setFixedSize(136, 136)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background: white; border: 1px solid #c4c4c4;")
        # Through QIcon, not QPixmap: QPixmap reads only the first picture in
        # an .ico (often 16 px) and the preview came out as a blur of blocks.
        # QIcon picks the sharpest size there is.
        pm = QIcon(str(path)).pixmap(128, 128)
        if pm.isNull():
            img.setText("(preview unavailable)")
        else:
            img.setPixmap(pm)
        bl.addWidget(img)
        pics.addWidget(box)
    lay.addLayout(pics)
    for caption, path in (("Existing", existing), ("Incoming", incoming)):
        lbl = QLabel(f"{caption}:  {wrap_path(str(path))}")
        lbl.setStyleSheet("color: #555;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
    btns = QHBoxLayout()
    btns.addStretch(1)
    keep = QPushButton("Keep existing")
    replace = QPushButton("Replace")
    keep.clicked.connect(dlg.reject)
    replace.clicked.connect(dlg.accept)
    btns.addWidget(keep)
    btns.addWidget(replace)
    btns.addStretch(1)
    lay.addLayout(btns)
    return dlg.exec() == QDialog.Accepted


_PATH_FIELDS = (
    ("root_folder", "Programs folder"),
    ("dist_root", "Dist output folder"),
    ("scratch", "Scratch (Software) folder"),
    ("sharepoint", "Sharepoint (QoL) folder"),
)


def set_paths_dialog(parent) -> bool:
    """Edit the shared paths. True when saved."""
    cfg = core.load_config()
    dlg = QDialog(parent)
    dlg.setWindowTitle("Set paths")
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("Paths for Dev Tools (Builder + Copy Manager)."))
    if core.PRACTICE:
        note = QLabel(f"Practice mode — saved in {core.CONFIG_PATH}")
        note.setStyleSheet("color: #8a4b00;")
        lay.addWidget(note)
    grid = QGridLayout()
    edits: dict[str, QLineEdit] = {}
    # Shown with the Windows separator whatever the file holds, and every box as
    # wide as the longest path, so no path is cut at either end.
    shown = {key: (str(Path(cfg[key])) if cfg.get(key) else "") for key, _ in _PATH_FIELDS}
    fm = dlg.fontMetrics()
    box_w = max([460] + [fm.horizontalAdvance(v) + 30 for v in shown.values()])
    for row, (key, label) in enumerate(_PATH_FIELDS):
        grid.addWidget(QLabel(label + ":"), row, 0)
        edit = QLineEdit(shown[key])
        edit.setReadOnly(True)
        edit.setMinimumWidth(box_w)
        edit.setCursorPosition(0)
        if key == "dist_root":
            edit.setPlaceholderText("empty = next to the programs folder")
        edits[key] = edit
        grid.addWidget(edit, row, 1)
        browse = QPushButton("Browse")

        def _browse(_=False, e=edit):
            chosen = QFileDialog.getExistingDirectory(dlg, "Select folder",
                                                      e.text() or str(Path.home()))
            if chosen:
                e.setText(str(Path(chosen)))
        browse.clicked.connect(_browse)
        grid.addWidget(browse, row, 2)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda _=False, e=edit: e.clear())
        grid.addWidget(clear, row, 3)
    lay.addLayout(grid)
    btns = QHBoxLayout()
    btns.addStretch(1)
    cancel = QPushButton("Cancel")
    save = QPushButton("Save")
    save.setObjectName("primary")
    cancel.clicked.connect(dlg.reject)
    btns.addWidget(cancel)
    btns.addWidget(save)
    lay.addLayout(btns)

    def _save():
        warnings = [f"  {label}:\n  {edits[key].text()}"
                    for key, label in _PATH_FIELDS
                    if key != "dist_root" and edits[key].text().strip()
                    and Path(edits[key].text().strip()).name.lower() == "dist"]
        if warnings and not ask_yes_no(
                dlg, "Suspicious path",
                "These paths end with a 'dist' folder — should it be the parent folder?\n\n"
                + "\n\n".join(warnings) + "\n\nSave anyway?"):
            return
        for key, edit in edits.items():
            val = edit.text().strip()
            if val:
                cfg[key] = val
            else:
                cfg.pop(key, None)
        core.save_config(cfg)
        dlg.accept()
    save.clicked.connect(_save)
    return dlg.exec() == QDialog.Accepted


def path_row(parent: QWidget) -> tuple[QHBoxLayout, QLabel]:
    """The 'Root folder' line shown on both tabs."""
    row = QHBoxLayout()
    lbl = QLabel()
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lbl.setStyleSheet("background: white; border: 1px solid #b0b0b0; padding: 3px 5px;")
    row.addWidget(lbl, 1)
    return row, lbl
