# ssq_common.py
"""Qt pieces shared by every window: look, log, thread hand-over, drawn
button icons, the wheel guard, small dialogs and the PIL → Qt picture bridge."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QIcon, QImage, QPainter, QPainterPath,
                           QPalette, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox,
                               QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QScrollBar, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

PANEL = "#f3f3f3"
INK = "#111111"
DISABLED_INK = "#9a9a9a"
ACCENT = "#2f6fb5"

# Every colour stated: Windows here runs in dark mode, and anything left to the
# theme comes out black on black. The group box keeps room for its caption
# (otherwise the caption is drawn over the first widget inside).
APP_STYLESHEET = f"""
QWidget {{ background: {PANEL}; color: {INK}; font-family: "Segoe UI"; font-size: 9pt; }}
QLabel {{ background: transparent; }}
QLabel:disabled {{ color: {DISABLED_INK}; }}
QGroupBox {{ border: 1px solid #c4c4c4; border-radius: 4px; margin-top: 9px;
             padding: 14px 6px 6px 6px; background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;
                    left: 8px; padding: 0 4px; color: {INK}; font-weight: bold;
                    background: {PANEL}; }}
QPushButton, QToolButton {{ background: #e8e8e8; color: {INK}; border: 1px solid #a8a8a8;
               border-radius: 3px; padding: 3px 9px; }}
QToolButton {{ padding: 2px; }}
QPushButton:hover, QToolButton:hover {{ background: #d9e8ff; }}
QPushButton:pressed, QToolButton:pressed {{ background: #c4d8f5; }}
QPushButton:disabled, QToolButton:disabled {{ background: #eeeeee; color: {DISABLED_INK};
                                              border-color: #d0d0d0; }}
QPushButton:checked, QToolButton:checked {{ background: {ACCENT}; color: white;
                                            border-color: #1f4f85; }}
QPushButton[presetOn="true"] {{ background: #d4e4f7; border: 2px solid {ACCENT};
                                font-weight: bold; padding: 2px 8px; }}
QPushButton#primary {{ background: #1565C0; color: white; border-color: #0d47a1;
                       font-weight: bold; }}
QPushButton#primary:hover {{ background: #1976D2; }}
QPushButton#primary:disabled {{ background: #9fb6d3; color: #eef3f9; }}
QPushButton#danger {{ background: #fde8e8; color: #9b1c1c; border: 1px solid #c62828; }}
QPushButton#danger:hover {{ background: #f9d0d0; }}
QPushButton#danger:pressed {{ background: #f3b9b9; }}
QPushButton#danger:disabled {{ background: #eeeeee; color: {DISABLED_INK}; border-color: #d0d0d0; }}
QLineEdit {{ background: white; color: {INK}; border: 1px solid #b0b0b0;
             border-radius: 2px; padding: 2px 4px; }}
QLineEdit:disabled {{ background: #ececec; color: {DISABLED_INK}; }}
QSpinBox, QDoubleSpinBox {{ background: white; color: {INK}; border: 1px solid #b0b0b0;
                            border-radius: 2px; padding: 1px 2px; }}
QComboBox {{ background: white; color: {INK}; border: 1px solid #b0b0b0;
             border-radius: 2px; padding: 1px 4px; }}
QComboBox:disabled {{ background: #ececec; color: {DISABLED_INK}; }}
QComboBox QAbstractItemView {{ background: white; color: {INK};
                               selection-background-color: {ACCENT};
                               selection-color: white; }}
QCheckBox, QRadioButton {{ background: transparent; color: {INK}; spacing: 5px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {DISABLED_INK}; }}
QPlainTextEdit, QTextEdit {{ background: white; color: {INK}; border: 1px solid #b0b0b0; }}
QListWidget {{ background: white; color: {INK}; border: 1px solid #b0b0b0; }}
QListWidget::item {{ padding: 3px 4px; }}
QListWidget::item:selected {{ background: {ACCENT}; color: white; }}
QScrollArea {{ border: none; background: {PANEL}; }}
QScrollBar:vertical {{ background: #e6e6e6; width: 14px; margin: 0; }}
QScrollBar:horizontal {{ background: #e6e6e6; height: 14px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #b4b4b4; min-height: 24px; border-radius: 3px;
                               margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #b4b4b4; min-width: 24px; border-radius: 3px;
                                 margin: 2px; }}
QScrollBar::handle:hover {{ background: #8f8f8f; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ background: #c8c8c8; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; margin: -6px 0;
                              border-radius: 6px; }}
QSlider::handle:horizontal:disabled {{ background: #b8b8b8; }}
QSlider::sub-page:horizontal {{ background: #8fb3dc; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: #d4d4d4; }}
QProgressBar {{ background: white; color: {INK}; border: 1px solid #b0b0b0;
                border-radius: 2px; text-align: center; }}
QProgressBar::chunk {{ background: #1565C0; }}
QToolTip {{ background: #ffffcc; color: {INK}; border: 1px solid #aaa; padding: 4px; }}
QMessageBox, QDialog {{ background: {PANEL}; color: {INK}; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: #c4c4c4; }}
"""


def _check_mark_file() -> Path:
    """A dark tick drawn once into %TEMP%: a styled indicator draws no tick of its own."""
    path = Path(tempfile.gettempdir()) / "ssq_check_mark_v1.png"
    if not path.exists():
        pm = QPixmap(28, 28)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(INK), 4.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(6, 14.5), QPointF(11.5, 20), QPointF(22, 8)])
        p.end()
        pm.save(str(path))
    return path


def _radio_dot_file() -> Path:
    path = Path(tempfile.gettempdir()) / "ssq_radio_dot_v1.png"
    if not path.exists():
        pm = QPixmap(28, 28)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(INK))
        p.drawEllipse(QRectF(8, 8, 12, 12))
        p.end()
        pm.save(str(path))
    return path


def _arrow_file(direction: str, colour: str) -> Path:
    """A small triangle as a PNG for a stylesheet `image:` — once a spin box
    button or a drop-down is styled, Qt no longer draws its arrow itself."""
    path = Path(tempfile.gettempdir()) / f"ssq_arrow_{direction}_{colour.strip('#')}_v1.png"
    if not path.exists():
        pm = QPixmap(18, 14)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colour))
        pts = ([QPointF(2, 3), QPointF(16, 3), QPointF(9, 11.5)] if direction == "down"
               else [QPointF(2, 11), QPointF(16, 11), QPointF(9, 2.5)])
        p.drawPolygon(pts)
        p.end()
        pm.save(str(path))
    return path


def indicator_stylesheet() -> str:
    """Tick boxes, radio buttons, spin box and drop-down arrows, all drawn
    here — left to the app style they came out white on white or as a
    two-pixel sliver. Needs a QApplication (draws pictures)."""
    tick = _check_mark_file().as_posix()
    dot = _radio_dot_file().as_posix()
    down = _arrow_file("down", INK).as_posix()
    up = _arrow_file("up", INK).as_posix()
    down_off = _arrow_file("down", DISABLED_INK).as_posix()
    up_off = _arrow_file("up", DISABLED_INK).as_posix()
    return (f"""
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 17px;
    border-left: 1px solid #b6b6b6; border-bottom: 1px solid #d0d0d0;
    background: #ececec; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 17px;
    border-left: 1px solid #b6b6b6; background: #ececec; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: #d9e8ff; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 9px; height: 7px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down}"); width: 9px; height: 7px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url("{up_off}"); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: url("{down_off}"); }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
    width: 20px; border-left: 1px solid #b6b6b6; background: #ececec; }}
QComboBox::drop-down:hover {{ background: #d9e8ff; }}
QComboBox::down-arrow {{ image: url("{down}"); width: 9px; height: 7px; }}
QComboBox::down-arrow:disabled {{ image: url("{down_off}"); }}"""
    + f"""
QCheckBox::indicator {{ width: 14px; height: 14px; background: white;
    border: 1px solid #555555; border-radius: 2px; }}
QCheckBox::indicator:hover {{ border-color: #1565C0; }}
QCheckBox::indicator:checked {{ image: url("{tick}"); }}
QCheckBox::indicator:disabled {{ background: #eeeeee; border-color: #b4b4b4; }}
QRadioButton::indicator {{ width: 14px; height: 14px; background: white;
    border: 1px solid #555555; border-radius: 7px; }}
QRadioButton::indicator:hover {{ border-color: #1565C0; }}
QRadioButton::indicator:checked {{ image: url("{dot}"); }}
QRadioButton::indicator:disabled {{ background: #eeeeee; border-color: #b4b4b4; }}
""")


def light_palette() -> QPalette:
    pal = QPalette()
    for role, colour in ((QPalette.Window, PANEL), (QPalette.WindowText, INK),
                         (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f7f7f7"),
                         (QPalette.Text, INK), (QPalette.Button, "#e8e8e8"),
                         (QPalette.ButtonText, INK), (QPalette.ToolTipBase, "#ffffcc"),
                         (QPalette.ToolTipText, INK), (QPalette.Highlight, ACCENT),
                         (QPalette.HighlightedText, "#ffffff"),
                         (QPalette.PlaceholderText, "#777777")):
        pal.setColor(role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(DISABLED_INK))
    return pal


def apply_app_style(app) -> None:
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(APP_STYLESHEET + indicator_stylesheet())
    install_wheel_guard(app)


# ----------------- THREAD HAND-OVER -----------------
class _Invoker(QObject):
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except Exception as e:   # a slot must never raise into Qt
            print(f"[UI] {e!r}")


_invoker: _Invoker | None = None


def init_ui_thread():
    """Create the hand-over object on the UI thread (call once, after QApplication)."""
    global _invoker
    if _invoker is None:
        _invoker = _Invoker()


def on_ui(fn: Callable, *args, **kwargs):
    """Run fn on the UI thread — from any thread. Widgets may only be touched there."""
    _invoker.call.emit(lambda: fn(*args, **kwargs))


# ----------------- LOG -----------------
class LogHub(QObject):
    """The Diagnostics log, fed from any thread.

    Lines are queued and written every 40 ms in one go; the view follows the
    end only while it is at the bottom, so reading further up is not yanked away.
    """

    FLUSH_MS = 40
    MAX_LINES = 20000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setMaximumBlockCount(self.MAX_LINES)
        self.view.setFont(QFont("Consolas", 9))
        self.view.setStyleSheet(f"QPlainTextEdit {{ background: white; color: {INK};"
                                ' font-family: Consolas; font-size: 9pt; }')
        self._timer = QTimer(self)
        self._timer.setInterval(self.FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def write(self, msg: str):
        from datetime import datetime
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        try:
            print(line, flush=True)
        except Exception:
            pass
        with self._lock:
            self._pending.append(line)

    def _flush(self):
        with self._lock:
            if not self._pending:
                return
            text = "\n".join(self._pending)
            self._pending.clear()
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        hbar = self.view.horizontalScrollBar()
        hpos = hbar.value()
        self.view.appendPlainText(text)
        hbar.setValue(hpos)
        if at_end:
            bar.setValue(bar.maximum())


# ----------------- WHEEL GUARD -----------------
def install_wheel_guard(app=None):
    """A value must never change just because the pointer crossed its control.

    Number boxes, drop-downs and sliders answer the wheel only after they were
    CLICKED (or reached with Tab), and only while the pointer is over them.
    Moving the pointer away keeps the field selected; the wheel works on it
    again when the pointer comes back. Selecting another control ends it. An
    unselected field passes the notch to the scroll area behind it, so a page
    still scrolls when the pointer happens to pass over a field. (The same
    guard as Announcer's ann_ui.install_wheel_guard.)
    """
    app = app or QApplication.instance()
    if app is None or getattr(app, "_wheel_guard", None) is not None:
        return

    class _WheelGuard(QObject):
        _GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                   Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)
        _KEEP = (Qt.FocusReason.ActiveWindowFocusReason,
                 Qt.FocusReason.PopupFocusReason)

        def eventFilter(self, obj, ev):
            # Installed on the whole application: it may never raise.
            try:
                return self._filter(obj, ev)
            except Exception:
                return False

        def _filter(self, obj, ev):
            t = ev.type()
            if t == QEvent.Type.Polish and isinstance(obj, self._GUARDED):
                # WheelFocus hands the field the focus as a MOUSE focus the
                # moment a notch lands on it; StrongFocus never takes the wheel.
                if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                    obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                return False
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
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
            if obj.hasFocus() and obj.property("wheelReady"):
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


# ----------------- DRAWN BUTTON ICONS -----------------
# Symbols are drawn, never typed: an emoji glyph ignores the button's ink and
# a symbol-font glyph comes out hair-thin. Every recipe draws on a 20-unit grid.
def _pen(p: QPainter, ink: QColor, w: float = 2.0):
    pen = QPen(ink, w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _ico_check(p, ink):
    _pen(p, ink, 2.6)
    p.drawPolyline([QPointF(4, 10.5), QPointF(8.5, 15), QPointF(16.5, 5.5)])


def _ico_trash(p, ink):
    _pen(p, ink, 1.8)
    p.drawLine(QPointF(3.5, 5), QPointF(16.5, 5))
    p.drawPolyline([QPointF(8, 5), QPointF(8, 3), QPointF(12, 3), QPointF(12, 5)])
    path = QPainterPath()
    path.moveTo(5, 5)
    path.lineTo(6, 17)
    path.lineTo(14, 17)
    path.lineTo(15, 5)
    p.drawPath(path)
    p.drawLine(QPointF(8.5, 8), QPointF(8.7, 14.5))
    p.drawLine(QPointF(11.5, 8), QPointF(11.3, 14.5))


def _ico_reset(p, ink):
    """↺ — the retry arrow mirrored: back to where it was."""
    p.translate(20, 0)
    p.scale(-1, 1)
    _ico_retry(p, ink)


def _ico_retry(p, ink):
    _pen(p, ink, 2.0)
    p.drawArc(QRectF(4, 4, 12, 12), 30 * 16, 300 * 16)
    p.setBrush(ink)
    p.setPen(Qt.NoPen)
    path = QPainterPath()
    path.moveTo(16.8, 3.2)
    path.lineTo(16.8, 9.2)
    path.lineTo(11.2, 7.4)
    path.closeSubpath()
    p.drawPath(path)


def _tri(p, ink, pts):
    p.setPen(Qt.NoPen)
    p.setBrush(ink)
    path = QPainterPath()
    path.moveTo(*pts[0])
    for pt in pts[1:]:
        path.lineTo(*pt)
    path.closeSubpath()
    p.drawPath(path)


def _ico_prev(p, ink):
    _tri(p, ink, [(13.5, 4), (13.5, 16), (5, 10)])


def _ico_next(p, ink):
    _tri(p, ink, [(6.5, 4), (6.5, 16), (15, 10)])


def _ico_down(p, ink):
    _tri(p, ink, [(4, 6.5), (16, 6.5), (10, 15)])


def _ico_play(p, ink):
    _tri(p, ink, [(6, 3.5), (6, 16.5), (16, 10)])


def _ico_stop(p, ink):
    p.setPen(Qt.NoPen)
    p.setBrush(ink)
    p.drawRoundedRect(QRectF(5, 5, 10, 10), 1.2, 1.2)


def _ico_record(p, ink):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#c62828") if ink.name() == QColor(INK).name() else ink)
    p.drawEllipse(QRectF(4.5, 4.5, 11, 11))


def _ico_folder(p, ink):
    _pen(p, ink, 1.7)
    path = QPainterPath()
    path.moveTo(2.5, 6)
    path.lineTo(2.5, 16)
    path.lineTo(17.5, 16)
    path.lineTo(17.5, 7.5)
    path.lineTo(9.5, 7.5)
    path.lineTo(8, 5)
    path.lineTo(3.5, 5)
    path.closeSubpath()
    p.drawPath(path)


def _ico_clear(p, ink):
    _pen(p, ink, 2.2)
    p.drawLine(QPointF(5.5, 5.5), QPointF(14.5, 14.5))
    p.drawLine(QPointF(14.5, 5.5), QPointF(5.5, 14.5))


_RECIPES = {
    "check": _ico_check, "trash": _ico_trash, "reset": _ico_reset,
    "retry": _ico_retry, "prev": _ico_prev, "next": _ico_next, "down": _ico_down,
    "play": _ico_play, "stop": _ico_stop, "record": _ico_record,
    "folder": _ico_folder, "clear": _ico_clear,
}


def _render_icon(name: str, px: int, ink: QColor) -> QPixmap:
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(px / 20.0, px / 20.0)
    _RECIPES[name](p, ink)
    p.end()
    return pm


_ICON_CACHE: dict[str, QIcon] = {}


def icon(name: str) -> QIcon:
    """A drawn symbol with every mode spelled out: dark ink, grey when
    disabled, white on the blue checked background. Never call at import
    time — a QPixmap needs a live QApplication."""
    ico = _ICON_CACHE.get(name)
    if ico is not None:
        return ico
    ico = QIcon()
    for px in (16, 20, 30, 40):
        for mode, ink in ((QIcon.Normal, INK), (QIcon.Active, INK),
                          (QIcon.Disabled, DISABLED_INK), (QIcon.Selected, "#ffffff")):
            ico.addPixmap(_render_icon(name, px, QColor(ink)), mode, QIcon.Off)
        ico.addPixmap(_render_icon(name, px, QColor("#ffffff")), QIcon.Normal, QIcon.On)
        ico.addPixmap(_render_icon(name, px, QColor(DISABLED_INK)), QIcon.Disabled, QIcon.On)
    _ICON_CACHE[name] = ico
    return ico


def icon_button(name: str, text: str = "", tip: str = "", tool: bool = False):
    """A button carrying a drawn symbol (and optional text)."""
    from PySide6.QtWidgets import QPushButton
    btn = QToolButton() if tool else QPushButton(text)
    if tool and text:
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    btn.setIcon(icon(name))
    btn.setIconSize(QSize(16, 16))
    if tip:
        btn.setToolTip(tip)
    return btn


# ----------------- COLLAPSIBLE SECTION -----------------
class CollapsibleSection(QWidget):
    """A camera category: ▼/▶ button, title, a slot for its "All" box, content."""

    def __init__(self, title: str, expanded: bool = False, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.btn = QToolButton()
        self.btn.setFixedSize(26, 22)
        self.btn.setIconSize(QSize(14, 14))
        self.btn.setIcon(icon("down" if expanded else "next"))
        self.btn.setToolTip("Show / hide the cameras of this section")
        self.btn.clicked.connect(self.toggle)
        head.addWidget(self.btn)
        self.lbl = QLabel(title)
        self.lbl.setStyleSheet("font-weight: bold;")
        head.addWidget(self.lbl)
        self.right_slot = QHBoxLayout()
        self.right_slot.setContentsMargins(8, 0, 0, 0)
        head.addLayout(self.right_slot)
        head.addStretch(1)
        lay.addLayout(head)
        self.content = QWidget()
        self.content.setAttribute(Qt.WA_StyledBackground, True)
        lay.addWidget(self.content)
        self._expanded = expanded
        self.content.setVisible(expanded)

    @property
    def expanded(self) -> bool:
        return self._expanded

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool):
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self.btn.setIcon(icon("down" if expanded else "next"))
        self.content.setVisible(expanded)


# ----------------- DIALOG HELPERS -----------------
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


def paint_window(w: QWidget, white: bool = False):
    """An explicit light ground and dark ink on a top-level window, so a test
    render (no app stylesheet) looks like the program does."""
    w.setAttribute(Qt.WA_StyledBackground, True)
    w.setPalette(light_palette())


def hint_label(text: str = "") -> QLabel:
    """Small grey status text (still dark enough to read)."""
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #555555; font-size: 8pt;")
    return lbl


def check_box(text: str = "", checked: bool = False) -> QCheckBox:
    cb = QCheckBox(text)
    cb.setChecked(checked)
    return cb


# ----------------- PICTURES -----------------
def pil_to_qimage(img) -> QImage:
    """An RGB / RGBA PIL picture as a QImage that owns its own pixels."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    if img.mode == "RGBA":
        data = img.tobytes("raw", "RGBA")
        return QImage(data, w, h, 4 * w, QImage.Format_RGBA8888).copy()
    data = img.tobytes("raw", "RGB")
    return QImage(data, w, h, 3 * w, QImage.Format_RGB888).copy()


def pil_to_pixmap(img) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(img))


def expanding(w: QWidget, horizontal=True, vertical=False) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding if horizontal else QSizePolicy.Preferred,
                    QSizePolicy.Expanding if vertical else QSizePolicy.Preferred)
    return w
