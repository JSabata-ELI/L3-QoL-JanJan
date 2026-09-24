"""Announcer — the look, in one importable place.

The program is LIGHT: light panels, dark ink. None of that comes from Windows.
This PC runs Windows in dark mode, so any widget that states neither a
background nor a foreground comes out black on black, and a screenshot taken
without `install_app_look` lies about every colour in it. Every test and every
render harness calls it too, for that reason.

The values are the ones Image Tools and CSS Logger use, so the three programs
look like one family.

Icons are DRAWN here rather than typed as characters. A row of Unicode glyphs
means three typefaces in one strip — the emoji ones ignore the stylesheet colour
and stay full-colour on a blue button, the symbol ones are hairlines, and two of
them are not guaranteed to exist at all. A painted icon has an exact stroke
weight, one look across the set, and a real white repaint for a checked button.
"""

from __future__ import annotations

from PySide6.QtCore import (QEvent, QObject, QPointF, QRectF, QSize, Qt,
                            QTimer)
from PySide6.QtGui import (QColor, QFont, QIcon, QPainter, QPainterPath, QPen,
                           QPalette, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QPushButton, QStyle,
                               QStyledItemDelegate, QStyleOptionButton,
                               QStyleOptionViewItem, QWidget)

# ─────────────────────────────────────────────────────────────────────────────
# The palette, as strings
# ─────────────────────────────────────────────────────────────────────────────
# Strings, not QColor. A QColor in a module global is destroyed after the
# QApplication has gone and takes the interpreter down with it on exit
# (0xC0000005). The QColor is built where it is used.

PAPER      = "#f3f3f3"
INK        = "#111111"
CELL       = "#ffffff"
ZEBRA      = "#f5f7fa"
LINE       = "#dfe4ea"
QUIET      = "#666666"

# One pair per verdict: a ground and the ink that goes on it. A background is
# never set without deciding its foreground — a dark tint with black text is the
# case this exists to prevent.
STATE_COLORS = {
    "ok":      ("#d4edda", "#155724"),
    "warn":    ("#fff3cd", "#856404"),
    "trip":    ("#f8d7da", "#8d1c12"),
    "stale":   ("#e7ebf0", "#37404a"),
    "unknown": ("#eceff3", "#4a5560"),
    "off":     ("#f4f4f4", "#8a8a8a"),
}
# The row for the thing that actually raised the alarm. Painted on the item,
# never left to Qt's selection colour: the selection is where the operator last
# clicked, which is not the same thing as what went wrong.
FIRED_BAND = ("#ffd9d6", "#7f0f08")

ACCENT     = "#2d7dff"
DANGER     = "#b71c1c"
GOOD       = "#2e7d32"


# ─────────────────────────────────────────────────────────────────────────────
# Stylesheets
# ─────────────────────────────────────────────────────────────────────────────

SCROLLBAR_QSS = """
QScrollBar:vertical { background: #d8dce2; width: 16px; margin: 0px; border: none; }
QScrollBar::handle:vertical { background: #6c7580; min-height: 28px;
    border-radius: 4px; margin: 2px; }
QScrollBar::handle:vertical:hover   { background: #4a5566; }
QScrollBar::handle:vertical:pressed { background: #2f3a49; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px; background: none; border: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal { background: #d8dce2; height: 16px; margin: 0px; border: none; }
QScrollBar::handle:horizontal { background: #6c7580; min-width: 28px;
    border-radius: 4px; margin: 2px; }
QScrollBar::handle:horizontal:hover   { background: #4a5566; }
QScrollBar::handle:horizontal:pressed { background: #2f3a49; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px; background: none; border: none; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
"""

TABLE_QSS = (
    "QTableWidget { background: #ffffff; alternate-background-color: #f5f7fa;"
    " color: #16202c; gridline-color: #dfe4ea; font-size: 11px;"
    " selection-background-color: #cfe0f7; selection-color: #10243c; }"
    "QHeaderView::section { background: #eef1f5; color: #16202c; font-weight: 600;"
    " border: 0px; border-right: 1px solid #dfe4ea;"
    " border-bottom: 1px solid #cfd6de; padding: 4px 6px; }"
    "QTableCornerButton::section { background: #eef1f5; border: 0px; }"
) + SCROLLBAR_QSS

BTN_QSS = (
    "QPushButton { padding: 4px 9px; border: 1px solid #b6b6b6; border-radius: 3px;"
    " background: #efefef; color: #111; }"
    "QPushButton:hover { background: #d9e8ff; }"
    "QPushButton:pressed { background: #b9d0f5; }"
    "QPushButton:disabled { background: #f4f4f4; color: #9a9a9a;"
    " border-color: #dcdcdc; }"
)

DANGER_QSS = (
    "QPushButton { padding: 4px 9px; border: 1px solid #c07070; border-radius: 3px;"
    " background: #fdecea; color: #8d1c12; }"
    "QPushButton:hover { background: #f8d3ce; }"
    "QPushButton:disabled { background: #f6f0f0; color: #bb9a97;"
    " border-color: #e3d2d0; }"
)

PRIMARY_QSS = (
    "QPushButton { padding: 6px 14px; border: 1px solid #1f5fc4; border-radius: 3px;"
    " background: #2d7dff; color: #ffffff; font-weight: 700; }"
    "QPushButton:hover { background: #1a6aee; }"
    "QPushButton:disabled { background: #aab4c0; color: #eef1f5;"
    " border-color: #99a3ae; }"
)

# Every colour stated, like everything else here. A right-click menu is the one
# widget nothing else in the program styles, so left to itself it comes up in
# the Windows DARK theme this PC is set to — light grey text on near-black,
# inside a white window.
MENU_QSS = (
    "QMenu { background: #ffffff; color: #111111;"
    " border: 1px solid #b6b6b6; padding: 3px; }"
    "QMenu::item { padding: 5px 22px 5px 26px; color: #111111;"
    " background: transparent; }"
    "QMenu::item:selected { background: #cfe0f7; color: #10243c; }"
    "QMenu::item:disabled { color: #9a9a9a; }"
    "QMenu::separator { height: 1px; background: #dfe4ea; margin: 3px 6px; }"
    "QMenu::icon { padding-left: 6px; }"
)

# The indicator only, never a border on QCheckBox itself — a border there frames
# the label as well and reads as a text box.
CHK_QSS = """
QCheckBox { spacing: 6px; padding: 2px 4px; color: #111; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
QCheckBox::indicator:disabled { border: 2px solid #bbbbbb; background: #f0f0f0; }
"""

APP_QSS = """
QWidget      { background: #f3f3f3; color: #111; font-family: "Segoe UI", Arial, sans-serif; }
QLabel       { background: transparent; }
QPushButton  { padding: 5px 8px; }
QComboBox    { padding: 3px 6px; background: #ffffff; color: #111;
               border: 1px solid #b6b6b6; border-radius: 3px; }
QComboBox QAbstractItemView { background: #ffffff; color: #111;
               selection-background-color: #cfe0f7; selection-color: #10243c; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
               background: #ffffff; color: #111;
               border: 1px solid #b6b6b6; border-radius: 3px; padding: 2px 4px; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
               background: #f0f0f0; color: #999; }
/* The stock arrows on a number box are a two-pixel sliver squeezed against the
   right edge — a target nobody can hit and a mark nobody can see. Given a width
   and a dividing line they read as the two buttons they are. The ::up-arrow /
   ::down-arrow marks themselves are left to Qt, which still draws them once the
   button has room. */
QSpinBox::up-button, QDoubleSpinBox::up-button {
               subcontrol-origin: border; subcontrol-position: top right;
               width: 17px; border-left: 1px solid #b6b6b6;
               border-top-right-radius: 3px; background: #ececec; }
QSpinBox::down-button, QDoubleSpinBox::down-button {
               subcontrol-origin: border; subcontrol-position: bottom right;
               width: 17px; border-left: 1px solid #b6b6b6;
               border-bottom-right-radius: 3px; background: #ececec; }
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
               background: #d9e8ff; }
QComboBox::drop-down { subcontrol-origin: padding;
               subcontrol-position: top right; width: 20px;
               border-left: 1px solid #b6b6b6; background: #ececec;
               border-top-right-radius: 3px; border-bottom-right-radius: 3px; }
QComboBox::drop-down:hover { background: #d9e8ff; }
/* Measured by rendering: the moment a sub-control is given a style of its own,
   Qt stops drawing its native arrow, and both boxes came out with a blank grey
   square where the mark should be. So the marks are drawn here and handed over
   as images — the same trick the sibling program uses for a list tick, which a
   QIcon cannot reach either. */
QComboBox::down-arrow { image: url(__ARROW_DOWN__); width: 9px; height: 7px; }
QComboBox::down-arrow:disabled { image: url(__ARROW_DOWN_OFF__); }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
               image: url(__ARROW_DOWN__); width: 9px; height: 7px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
               image: url(__ARROW_UP__); width: 9px; height: 7px; }
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
               image: url(__ARROW_DOWN_OFF__); }
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {
               image: url(__ARROW_UP_OFF__); }
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
/* Styling a QGroupBox border switches off Qt's own room for the caption, so
   without the second rule the caption is painted straight over the first row of
   whatever is inside the box. Reserved here once, for every box. */
QGroupBox { border: 1px solid #ccc; border-radius: 4px; font-weight: 600;
            margin-top: 9px; padding: 14px 6px 6px 6px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 8px; padding: 0 4px; background: #f3f3f3; }
QSplitter::handle { background: #c8c8c8; }
QSplitter::handle:hover { background: #2d7dff; }
QHeaderView { background: #eef1f5; }
""" + CHK_QSS + SCROLLBAR_QSS


_ARROW_CACHE = {}


def _arrow_url(direction, colour):
    """A small triangle as a PNG on disk, for a stylesheet `image:` to point at.

    Painted once per (direction, colour) into the temporary folder. A stylesheet
    cannot take a QIcon, and nothing may be drawn before the QApplication
    exists, so this cannot live in a module constant.
    """
    key = (direction, colour)
    if key in _ARROW_CACHE:
        return _ARROW_CACHE[key]
    import tempfile
    from pathlib import Path
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    w, h = 9, 7
    pm = QPixmap(w * 3, h * 3)          # drawn at 3x so it stays crisp scaled
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(colour))
    if direction == "down":
        pts = [QPointF(1, 5), QPointF(w * 3 - 1, 5), QPointF(w * 1.5, h * 3 - 3)]
    else:
        pts = [QPointF(1, h * 3 - 5), QPointF(w * 3 - 1, h * 3 - 5),
               QPointF(w * 1.5, 3)]
    p.drawPolygon(QPolygonF(pts))
    p.end()
    path = Path(tempfile.gettempdir()) / f"announcer_arrow_{direction}_" \
        f"{colour.lstrip('#')}.png"
    try:
        pm.save(str(path), "PNG")
    except Exception:
        return ""
    url = path.as_posix()
    _ARROW_CACHE[key] = url
    return url


def app_stylesheet():
    """The application sheet, with the drawn arrows filled in."""
    qss = APP_QSS
    for token, direction, colour in (
            ("__ARROW_DOWN__", "down", "#1e2530"),
            ("__ARROW_UP__", "up", "#1e2530"),
            ("__ARROW_DOWN_OFF__", "down", "#a0a8b2"),
            ("__ARROW_UP_OFF__", "up", "#a0a8b2")):
        url = _arrow_url(direction, colour)
        qss = qss.replace(token, url)
    return qss


def install_app_look(app=None):
    """Fusion, the stylesheet, the light palette and the wheel guard.

    Deliberately importable and idempotent. Anything that puts this program's
    widgets on a screen — the program, a test, a screenshot harness — calls this
    first, or it gets whatever theme Windows is in.
    """
    app = app or QApplication.instance()
    if app is None:
        return
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor("#f3f3f3"))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor("#111111"))
    pal.setColor(QPalette.ColorRole.Base,            QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor("#f5f7fa"))
    pal.setColor(QPalette.ColorRole.Text,            QColor("#111111"))
    pal.setColor(QPalette.ColorRole.Button,          QColor("#efefef"))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor("#111111"))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#ffffcc"))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor("#111111"))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor("#cfe0f7"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#10243c"))
    app.setPalette(pal)
    install_wheel_guard(app)


def install_wheel_guard(app=None):
    """A value must never change just because the pointer crossed its control.

    Number boxes, drop-downs and sliders answer the wheel only once they have
    been CLICKED. Until then the notch goes to the panel behind them, so a
    settings panel still scrolls when the pointer happens to pass over a field
    on the way down. The focus a freshly opened window HANDS its first field does
    not count, or the top field of a panel would answer the wheel before it had
    ever been touched.
    """
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider,
                                   QAbstractSpinBox, QComboBox, QScrollBar)

    app = app or QApplication.instance()
    if app is None or getattr(app, "_ann_wheel_guard", None) is not None:
        return

    class _WheelGuard(QObject):
        _GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                   Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)
        _GIVEN = (Qt.FocusReason.ActiveWindowFocusReason,
                  Qt.FocusReason.OtherFocusReason)

        def eventFilter(self, obj, ev):
            """EVERY event in the program passes through here, so it may not
            raise — ever.

            This is installed on the whole application, which makes it the one
            piece of Python that Qt calls for every mouse move, every timer,
            every deferred delete, on any object in the program. Two measured
            reasons for the blanket try/except:

              * An exception raised inside a Python override does not stop at
                the boundary. PySide6 leaves the exception set, every override
                called afterwards fails with `SystemError`, and the process goes
                down with an access violation in the next garbage collection —
                measured on 2026-09-17, nowhere near the line at fault.
              * `obj` here can be an object Qt is in the middle of tearing
                down. `isinstance` and `property()` on a wrapper whose C++ side
                has gone raise `RuntimeError: Internal C++ object already
                deleted`, and this filter sits on the very code path — posted
                event delivery — where the crash dumps caught it.

            Letting an event through unfiltered costs a stray scroll. Raising
            costs the program.
            """
            try:
                return self._filter(obj, ev)
            except Exception:
                return False

        def _filter(self, obj, ev):
            t = ev.type()
            if t == QEvent.Type.FocusIn and isinstance(obj, self._GUARDED):
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

    guard = _WheelGuard(app)
    app.installEventFilter(guard)
    app._ann_wheel_guard = guard


def paint_dialog(dlg, white=False):
    """Give a dialog an explicit light ground and dark ink.

    The application stylesheet already makes it light inside the program — but
    this PC is in Windows dark mode, so the same dialog opened by a test or a
    render script comes up dark, and #111 text on it cannot be read. Nothing is
    left to the theme.
    """
    ground = "#ffffff" if white else "#f3f3f3"
    dlg.setStyleSheet(
        f"QDialog {{ background: {ground}; color: #111111; }}"
        "QLabel { background: transparent; color: #111111; }"
        + CHK_QSS)


# ─────────────────────────────────────────────────────────────────────────────
# Drawn icons
# ─────────────────────────────────────────────────────────────────────────────
# Every recipe draws inside a 20-unit grid; the painter is scaled to whatever
# size is asked for, so one recipe serves every monitor scaling.

_ICON_BOX = 20.0
ICON_PX = 18
_ICON_SIZES = (18, 27, 36)          # 100 % / 150 % / 200 % display scaling
_INK_NORMAL = "#1e2530"
_INK_CHECKED = "#ffffff"
_INK_DISABLED = "#a0a8b2"
_ICON_CACHE = {}


def _pen(color, w=2.0):
    """One pen helper, so the whole set shares a single weight vocabulary."""
    p = QPen(QColor(color))
    p.setWidthF(w)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


def _i_plus(p, ink):
    p.setPen(_pen(ink, 2.4))
    p.drawLine(QPointF(10, 4), QPointF(10, 16))
    p.drawLine(QPointF(4, 10), QPointF(16, 10))


def _i_minus(p, ink):
    p.setPen(_pen(ink, 2.4))
    p.drawLine(QPointF(4, 10), QPointF(16, 10))


def _i_pencil(p, ink):
    p.setPen(_pen(ink, 1.8))
    p.drawLine(QPointF(4.5, 15.5), QPointF(5.5, 12.5))
    p.drawLine(QPointF(5.5, 12.5), QPointF(13, 5))
    p.drawLine(QPointF(13, 5), QPointF(15.5, 7.5))
    p.drawLine(QPointF(15.5, 7.5), QPointF(8, 15))
    p.drawLine(QPointF(8, 15), QPointF(4.5, 15.5))


def _i_copy(p, ink):
    p.setPen(_pen(ink, 1.7))
    p.drawRect(QRectF(3.5, 3.5, 9, 9))
    p.drawRect(QRectF(7.5, 7.5, 9, 9))


def _i_trash(p, ink):
    p.setPen(_pen(ink, 1.7))
    p.drawLine(QPointF(3.5, 6), QPointF(16.5, 6))
    p.drawLine(QPointF(8, 6), QPointF(8.5, 3.5))
    p.drawLine(QPointF(8.5, 3.5), QPointF(11.5, 3.5))
    p.drawLine(QPointF(11.5, 3.5), QPointF(12, 6))
    p.drawLine(QPointF(5.5, 6), QPointF(6.5, 16.5))
    p.drawLine(QPointF(6.5, 16.5), QPointF(13.5, 16.5))
    p.drawLine(QPointF(13.5, 16.5), QPointF(14.5, 6))
    p.drawLine(QPointF(10, 8.5), QPointF(10, 14))


def _i_refresh(p, ink):
    # An arc with a solid head on one end. The head is placed by eye against
    # testing/icons.png — an arrow head computed from the tangent kept landing
    # off the stroke, and a circular arrow with no head is just a letter C.
    p.setPen(_pen(ink, 1.9))
    path = QPainterPath()
    path.arcMoveTo(QRectF(3.5, 3.5, 13, 13), 60)
    path.arcTo(QRectF(3.5, 3.5, 13, 13), 60, 280)
    p.drawPath(path)
    p.setBrush(QColor(ink))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(QPolygonF([QPointF(13.3, 4.4), QPointF(10.6, 1.6),
                             QPointF(15.2, 0.9)]))


def _i_play(p, ink):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ink))
    p.drawPolygon(QPolygonF([QPointF(5.5, 3.5), QPointF(16, 10),
                             QPointF(5.5, 16.5)]))


def _i_stop(p, ink):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ink))
    p.drawRoundedRect(QRectF(5, 5, 10, 10), 1.5, 1.5)


def _i_camera(p, ink):
    p.setPen(_pen(ink, 1.7))
    p.drawRoundedRect(QRectF(2.5, 6, 15, 10), 2, 2)
    p.drawLine(QPointF(7, 6), QPointF(8.5, 3.8))
    p.drawLine(QPointF(8.5, 3.8), QPointF(11.5, 3.8))
    p.drawLine(QPointF(11.5, 3.8), QPointF(13, 6))
    p.drawEllipse(QPointF(10, 11), 3.0, 3.0)


def _i_area(p, ink):
    """A dashed rectangle with corner marks — "drag an area out"."""
    dashed = _pen(ink, 1.5)
    dashed.setStyle(Qt.PenStyle.DashLine)
    p.setPen(dashed)
    p.drawRect(QRectF(3.5, 4.5, 13, 11))
    p.setPen(_pen(ink, 2.2))
    for x, y in ((3.5, 4.5), (16.5, 4.5), (3.5, 15.5), (16.5, 15.5)):
        p.drawPoint(QPointF(x, y))


def _i_monitor(p, ink):
    p.setPen(_pen(ink, 1.7))
    p.drawRoundedRect(QRectF(2.5, 4, 15, 10), 1.5, 1.5)
    p.drawLine(QPointF(7, 17), QPointF(13, 17))
    p.drawLine(QPointF(10, 14), QPointF(10, 17))


def _i_eye(p, ink):
    p.setPen(_pen(ink, 1.7))
    path = QPainterPath(QPointF(2.5, 10))
    path.quadTo(QPointF(10, 3.2), QPointF(17.5, 10))
    path.quadTo(QPointF(10, 16.8), QPointF(2.5, 10))
    p.drawPath(path)
    p.setBrush(QColor(ink))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(10, 10), 2.2, 2.2)


def _i_bell(p, ink):
    p.setPen(_pen(ink, 1.7))
    path = QPainterPath(QPointF(5, 14))
    path.lineTo(QPointF(5, 9.5))
    path.arcTo(QRectF(5, 3.5, 10, 10), 180, -180)
    path.lineTo(QPointF(15, 14))
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(3.5, 14), QPointF(16.5, 14))
    p.drawArc(QRectF(8.2, 15.0, 3.6, 3.0), 0, -180 * 16)


def _i_check(p, ink):
    p.setPen(_pen(ink, 2.4))
    p.drawPolyline(QPolygonF([QPointF(4, 10.5), QPointF(8, 14.5),
                              QPointF(16, 5.5)]))


def _i_cross(p, ink):
    p.setPen(_pen(ink, 2.4))
    p.drawLine(QPointF(5, 5), QPointF(15, 15))
    p.drawLine(QPointF(15, 5), QPointF(5, 15))


def _i_gear(p, ink):
    # Six short THICK teeth on a ring. Eight thin ones came out as a sun.
    import math
    p.setPen(_pen(ink, 1.7))
    p.drawEllipse(QPointF(10, 10), 3.4, 3.4)
    p.setPen(_pen(ink, 2.8))
    for k in range(6):
        a = math.radians(k * 60)
        p.drawLine(QPointF(10 + 4.6 * math.cos(a), 10 + 4.6 * math.sin(a)),
                   QPointF(10 + 7.2 * math.cos(a), 10 + 7.2 * math.sin(a)))


def _i_save(p, ink):
    p.setPen(_pen(ink, 1.7))
    p.drawRoundedRect(QRectF(3.5, 3.5, 13, 13), 1.5, 1.5)
    p.drawRect(QRectF(7, 3.5, 6, 4.5))
    p.drawRect(QRectF(6, 11, 8, 5.5))


_RECIPES = {
    "plus": _i_plus, "minus": _i_minus, "pencil": _i_pencil, "copy": _i_copy,
    "trash": _i_trash, "refresh": _i_refresh, "play": _i_play, "stop": _i_stop,
    "camera": _i_camera, "area": _i_area, "monitor": _i_monitor, "eye": _i_eye,
    "bell": _i_bell, "check": _i_check, "cross": _i_cross, "gear": _i_gear,
    "save": _i_save,
}

ICON_NAMES = tuple(sorted(_RECIPES))


def _render(name, px, ink):
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(px / _ICON_BOX, px / _ICON_BOX)
    _RECIPES[name](p, ink)
    p.end()
    return pm


def icon(name, ink=_INK_NORMAL):
    """One icon, carrying its own normal, checked and greyed artwork.

    Spelling out every mode is not optional. Left to itself Qt invents the
    disabled version by fading the normal one until it is barely there, and it
    keeps the dark artwork while a button is checked and its background has
    turned blue. Both are exactly the "can't see it" failures this replaces.

    Must not be called before the QApplication exists — QPixmap needs it.
    """
    if name not in _RECIPES:
        return QIcon()
    key = (name, ink)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    ic = QIcon()
    for mode, state, colour in (
            (QIcon.Mode.Normal,   QIcon.State.Off, ink),
            (QIcon.Mode.Active,   QIcon.State.Off, ink),
            (QIcon.Mode.Normal,   QIcon.State.On,  _INK_CHECKED),
            (QIcon.Mode.Active,   QIcon.State.On,  _INK_CHECKED),
            (QIcon.Mode.Selected, QIcon.State.On,  _INK_CHECKED),
            (QIcon.Mode.Disabled, QIcon.State.Off, _INK_DISABLED),
            (QIcon.Mode.Disabled, QIcon.State.On,  _INK_DISABLED)):
        for px in _ICON_SIZES:
            ic.addPixmap(_render(name, px, colour), mode, state)
    _ICON_CACHE[key] = ic
    return ic


def button(text, tip="", *, icon_name="", danger=False, primary=False):
    """A house button. No trailing ellipsis, ever — not even on one that opens
    a dialog. Every non-obvious one carries a tooltip written as a sentence."""
    b = QPushButton(text)
    b.setStyleSheet(PRIMARY_QSS if primary else
                    (DANGER_QSS if danger else BTN_QSS))
    if tip:
        b.setToolTip(tip)
    if icon_name:
        # A red button gets a red icon, so the mark is not the one dark thing
        # in a red row.
        b.setIcon(icon(icon_name, "#8d1c12" if danger else
                       ("#ffffff" if primary else _INK_NORMAL)))
        b.setIconSize(QSize(ICON_PX, ICON_PX))
    return b


class StatusLight(QWidget):
    """The coloured circle — the whole state machine in one glance.

    grey    nothing to watch
    orange  something to watch, but not watching
    green   watching
    red     something fired

    The precedence matters and is the old program's: watching beats fired, so
    the circle says what the program is doing now rather than what it did.
    Clicking it is the same as pressing the button next to it, and it is the
    only control left on screen while watching — which is why it is a real
    widget rather than a coloured label.
    """

    from PySide6.QtCore import Signal as _Signal
    clicked = _Signal()

    _COLOURS = {
        "grey":   ("#888888", None),
        "orange": ("#ddaa00", "#886600"),
        "green":  ("#22cc22", "#117711"),
        "red":    ("#cc2222", "#881111"),
    }

    def __init__(self, diameter=28, parent=None):
        super().__init__(parent)
        self._colour = "grey"
        self._d = int(diameter)
        self.setFixedSize(self._d + 6, self._d + 6)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_colour(self, name):
        if name != self._colour:
            self._colour = name
            self.update()

    def colour(self):
        return self._colour

    def paintEvent(self, _ev):
        # try/finally, like every paintEvent in this program: an exception that
        # escapes here leaves the painter active on the widget and the program
        # dies on the next window move, not here where it could be understood.
        p = QPainter(self)
        try:
            fill, outline = self._COLOURS.get(self._colour,
                                              self._COLOURS["grey"])
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(3, 3, self._d, self._d)
            if outline:
                pen = QPen(QColor(outline))
                pen.setWidthF(2.0)
                p.setPen(pen)
            else:
                p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(fill))
            p.drawEllipse(rect)
        finally:
            p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class AlertMark(QWidget):
    """A red exclamation mark, drawn on nothing, beside the circle.

    It means one thing and says nothing else: the readings have not been
    arriving for longer than `C.NO_DATA_S`. Asked for on 2026-09-21 — the
    sentences beside the circle used to carry every failed read, and a column of
    "not read yet" over the operator's own work is noise. A mark he can see from
    across the room, and the log for the details, is what he wanted instead.

    No background, on purpose. It is drawn with a dark red outline so it stays
    readable over a white document and over a dark photograph alike — an
    outline is not a background.
    """

    _FILL    = "#d40f0f"
    _OUTLINE = "#5e0505"

    def __init__(self, height=28, parent=None):
        super().__init__(parent)
        self._h = int(height)
        self.setFixedSize(max(10, round(self._h * 0.46)), self._h)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setToolTip("Nothing has been read for over five minutes.")

    def paintEvent(self, _ev):
        # try/finally, like every paintEvent in this program: an exception that
        # escapes here leaves the painter active and kills the program later,
        # somewhere that cannot be understood.
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self.width(), self.height()
            bar_w = max(3.0, w * 0.44)
            x = (w - bar_w) / 2.0
            top = h * 0.06
            bar_h = h * 0.58
            gap = h * 0.10
            dot = bar_w * 1.02
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, top, bar_w, bar_h),
                                bar_w / 2.4, bar_w / 2.4)
            path.addEllipse(QRectF((w - dot) / 2.0, top + bar_h + gap,
                                   dot, dot))
            pen = QPen(QColor(self._OUTLINE))
            pen.setWidthF(max(1.0, h * 0.045))
            p.setPen(pen)
            p.setBrush(QColor(self._FILL))
            p.drawPath(path)
        finally:
            p.end()


class RowMenu(QObject):
    """A right-click menu on a table's rows.

    Two things it has to get right, both of them about a picked SET of rows:

    * right-clicking a row that is **already** one of several picked rows must
      leave that selection alone — otherwise the menu quietly throws away the
      six rows the operator spent a moment picking, and does its work on one.
    * right-clicking anywhere else picks that row first, so the menu is never
      about something other than what was clicked.

    `entries` is a list of `(label, icon_name, slot)`, or `None` for a
    separator. A label may be a callable taking the number of picked rows, for
    "Remove these 3" — a menu that says how many it is about is the only place
    the operator can check before pressing it.
    """

    def __init__(self, table, entries):
        # Parented to the table, and a QObject, so QT owns it. Left as a plain
        # object with nobody holding a reference, this would be collected the
        # moment the tab finished building and the menu would simply never
        # appear again.
        super().__init__(table)
        from PySide6.QtWidgets import QAbstractItemView
        self._table = table
        self._entries = list(entries)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show)
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)

    def labels(self, count):
        """What the menu would say for `count` picked rows. For the tests."""
        return [None if e is None else (e[0](count) if callable(e[0]) else e[0])
                for e in self._entries]

    def rows_at(self, pos):
        """Which rows the menu is about, having settled the selection first.

        Returns the row numbers, or None for a right-click on empty space.
        Separate from `_show` so it can be tested on its own: `QMenu.exec`
        blocks on a real event loop and cannot be stubbed out (PySide6 will not
        let its methods be replaced), so a test that opened the menu for real
        would simply hang.
        """
        table = self._table
        index = table.indexAt(pos)
        if not index.isValid():
            return None                     # empty space below the last row
        rows = {i.row() for i in table.selectedIndexes()}
        if index.row() not in rows:
            table.selectRow(index.row())
            rows = {index.row()}
        return rows

    def _show(self, pos):
        from PySide6.QtWidgets import QMenu
        table = self._table
        rows = self.rows_at(pos)
        if rows is None:
            return
        count = len(rows)
        menu = QMenu(table)
        menu.setStyleSheet(MENU_QSS)
        doing = {}
        for entry in self._entries:
            if entry is None:
                menu.addSeparator()
                continue
            label, icon_name, slot = entry
            text = label(count) if callable(label) else label
            act = (menu.addAction(icon(icon_name), text) if icon_name
                   else menu.addAction(text))
            doing[act] = slot
        # The work is done AFTER the menu has gone, never from `triggered`
        # while the popup is still up. Every one of these entries opens a
        # window — Remove asks "are you sure", Edit opens the editor — and a
        # modal window opened underneath a menu that still holds Windows'
        # mouse grab is a window that cannot be answered: the operator's Yes
        # went nowhere and Remove looked as though it did nothing. Reported
        # 2026-09-21. `singleShot(0)` puts it in the next turn of the event
        # loop, by which time the popup has been taken down.
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        slot = doing.get(chosen)
        if slot is not None:
            QTimer.singleShot(0, slot)


class ColumnFitter(QObject):
    """Every column as wide as its widest cell — and still draggable.

    `QHeaderView.ResizeMode.ResizeToContents` sizes a column correctly and then
    **refuses to be dragged at all**: the operator could not widen "Say when it
    fires" to read a long sentence, and the pointer did not even change over the
    divider. `Stretch` is worse — it hands the column whatever room is left
    over, which at the window's smallest was four pixels less than the heading
    itself needed, so the heading came out as "Say when it fire".

    So every section is `Interactive`, which is the only mode that can be
    dragged, and the width is COMPUTED instead: `fit()` after each reload sets
    each column to its own contents, heading included.

    A column the operator has dragged himself is then left alone for the rest of
    the session — his width is a decision, not a leftover. `reset()` gives them
    all back to the contents.

    The room left over at the RIGHT edge goes to the last column. Without that
    the table ended at its contents and the rest of the width was a grey strip
    the operator could neither use nor get rid of — reported 18.9.2026. It is
    added here, after the contents have been measured, and never takes a column
    below what is written in it; Qt's own `stretchLastSection` does the
    opposite, which is why it stays off.
    """

    def __init__(self, table):
        from PySide6.QtWidgets import QHeaderView
        super().__init__(table)
        self._table = table
        self._touched = set()       # columns the operator sized himself
        self._busy = False          # True while WE are setting a width
        hh = table.horizontalHeader()
        for col in range(table.columnCount()):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        # Off, or the last column silently ignores its own contents and eats
        # the leftover room instead.
        hh.setStretchLastSection(False)
        hh.sectionResized.connect(self._on_resized)
        # A window made wider or narrower has to re-fill the last column, and
        # nothing else tells us: the table is not reloaded on a resize.
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Resize
                and obj is self._table.viewport() and not self._busy):
            self.fit()
        return False

    def _on_resized(self, col, _old, _new):
        if not self._busy:
            self._touched.add(int(col))

    # Breathing room, so the longest text never touches the divider.
    _PAD = 6

    def _width_for(self, col):
        """The widest thing in one column: its heading, or one of its cells.

        Measured cell by cell rather than with `resizeColumnToContents`, and
        that is not fussiness — Qt's own version measures EVERY cell in the
        column including the ones that are part of a **span**. The Watch table
        puts a heading line across the full width, and the empty-table note is
        a whole sentence spanning every column; either one would have made the
        "On" tick-box column as wide as a sentence. A spanned cell belongs to no
        single column, so it is skipped here.
        """
        table = self._table
        width = table.horizontalHeader().sectionSizeHint(col)
        model = table.model()
        for row in range(table.rowCount()):
            if table.columnSpan(row, col) != 1 or table.rowSpan(row, col) != 1:
                continue
            width = max(width, table.sizeHintForIndex(model.index(row, col)).width())
        return width + self._PAD

    def fit(self):
        """Contents for every column, and the leftover room to the last one."""
        table = self._table
        cols = table.columnCount()
        if not cols:
            return
        self._busy = True
        try:
            for col in range(cols):
                if col not in self._touched:
                    table.setColumnWidth(col, self._width_for(col))
            last = cols - 1
            used = sum(table.columnWidth(c) for c in range(last))
            spare = table.viewport().width() - used
            if spare > table.columnWidth(last):
                table.setColumnWidth(last, spare)
        finally:
            self._busy = False

    def reset(self):
        self._touched.clear()
        self.fit()


class CenteredCheckDelegate(QStyledItemDelegate):
    """A tick box in the MIDDLE of its column, not glued to the left edge.

    `setTextAlignment(AlignHCenter)` moves the text of a cell and nothing
    else — measured on this Qt, a tick-only cell draws its box at x 5..14 of a
    120 px column whichever alignment is asked for. So the box is drawn here by
    hand, centred under its heading, and the click is taken in that same
    rectangle instead of the empty space on the left.

    Every import is at the top of the module on purpose: an import inside
    `paint` runs on every repaint of every row.
    """

    def _box(self, option, index, widget):
        """The centred rectangle the tick is drawn in and clicked in."""
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = widget.style() if widget is not None else QApplication.style()
        box = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, widget)
        box.moveCenter(option.rect.center())
        return box

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        # The cell itself first — its ground, the zebra stripe, the selection
        # and any text — but without the indicator the style puts on the left.
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter,
                          widget)
        if state is None:       # a cell with no tick at all, e.g. the note row
            return
        box = QStyleOptionButton()
        box.rect = self._box(option, index, widget)
        box.state = QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On
            if Qt.CheckState(state) == Qt.CheckState.Checked
            else QStyle.StateFlag.State_Off)
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, box,
            painter, widget)

    def editorEvent(self, event, model, option, index):
        flags = index.flags()
        if not (flags & Qt.ItemFlag.ItemIsUserCheckable
                and flags & Qt.ItemFlag.ItemIsEnabled):
            return False
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        if state is None:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            if not self._box(option, index, option.widget).contains(
                    event.position().toPoint()):
                return False
        elif kind == QEvent.Type.KeyPress:
            if event.key() not in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                return False
        else:
            # Presses and double clicks are left alone, so the row still
            # selects itself and still opens its editor on a double click.
            return False
        turned = (Qt.CheckState.Unchecked
                  if Qt.CheckState(state) == Qt.CheckState.Checked
                  else Qt.CheckState.Checked)
        return model.setData(index, turned, Qt.ItemDataRole.CheckStateRole)


def empty_table_note(table, text):
    """Put one greyed, unclickable line in an empty table, never a blank box.

    A blank table says nothing about whether there is nothing to show or
    something went wrong. Returns True when the note was placed.
    """
    from PySide6.QtWidgets import QTableWidgetItem
    if table.rowCount() != 0:
        return False
    table.setRowCount(1)
    cell = QTableWidgetItem(text)
    cell.setFlags(Qt.ItemFlag.NoItemFlags)
    cell.setForeground(QColor("#8a8a8a"))
    table.setItem(0, 0, cell)
    for col in range(1, table.columnCount()):
        filler = QTableWidgetItem("")
        filler.setFlags(Qt.ItemFlag.NoItemFlags)
        table.setItem(0, col, filler)
    table.setSpan(0, 0, 1, table.columnCount())
    return True


def name_list(items, fallback="this one", most=12):
    """The names of what is about to be changed, one per line.

    For a question like "take these 6 off the list?". A count on its own is not
    something anybody can say yes to safely — the names are the whole point of
    asking. Long selections are cut off with a count of the rest rather than
    growing the dialog off the screen.
    """
    names = [(it.get("name") or fallback) for it in items]
    shown = names[:most]
    text = "\n".join(f"  · {n}" for n in shown)
    if len(names) > most:
        text += f"\n  · … and {len(names) - most} more"
    return text


def section_label(text):
    """A small heading without a box around it."""
    from PySide6.QtWidgets import QLabel
    lbl = QLabel(text.upper())
    lbl.setStyleSheet("color: #444; font-size: 10px; font-weight: 700;"
                      " letter-spacing: 1px; background: transparent;")
    return lbl


def hsep():
    from PySide6.QtWidgets import QFrame
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #ccc; margin: 2px 0;")
    return f


def mono_font(size=10):
    f = QFont("Consolas")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(size)
    return f
