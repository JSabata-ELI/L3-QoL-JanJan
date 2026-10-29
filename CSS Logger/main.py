"""
CPVA Suite  —  CSS Logger + Spectra in one PySide6 window.
Run:  python main.py   (from L3-QoL-JanJan/CSS Logger/)
"""
import sys
import pathlib

_HERE = pathlib.Path(__file__).parent          # CSS Logger/
_ROOT = _HERE.parent                           # L3-QoL-JanJan/
# Every module of the suite (sp_t.py, cpva_core.py) lives in _HERE, which is
# already on sys.path via __file__. Never add another folder here: sp_t.py used
# to be kept in a separate Spectra/ folder and put on sys.path from this line,
# so running from source used that copy while the build silently used the stale
# copy sitting next to main.py. One folder = one file = no such split.
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Make debug print() bullet-proof: a Windows console using cp1250 raises
# UnicodeEncodeError on the "→" used in our log lines, and a frozen windowed
# build has sys.stdout == None — either one would crash a handler mid-action.
import io as _io
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is None:
        setattr(sys, _stream_name, _io.StringIO())
    else:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ── stdlib ─────────────────────────────────────────────────────────────────
import bisect
import csv
import functools
import html
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from datetime import datetime, timedelta, timezone

import numpy as np

# Python's garbage collector, at its default setting, re-scans every object
# the program holds each few hundred allocations. This program holds millions
# of small objects (every archived sample is a tuple), so a refresh that merely
# builds a list of rows spent most of its time in those scans: measured on a
# 60 000-row custom-PV compute next to a million samples, 300 ms at the default
# and 65 ms with this setting. Reference counting frees almost everything at
# once; only the youngest generation is made lazier, the older two keep their
# default ratios, so the cycles a discarded matplotlib figure leaves behind are
# still collected regularly.
import gc as _gc
_gc.set_threshold(50_000, 20, 10)

# ── PySide6 ────────────────────────────────────────────────────────────────
from PySide6.QtCore import (
    Qt, QObject, QTimer, Signal, QDate, QLocale, QRect, QSize, QPoint,
    QPointF, QEvent, QDateTime, QTime,
)
from PySide6.QtGui import (
    QColor, QIcon, QPalette, QPainter, QPen, QShortcut, QKeySequence,
    QPolygonF, QPixmap, QBrush, QPainterPath, QImage,
)
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QListWidget, QListWidgetItem,
    QTabWidget, QSplitter, QScrollArea, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCheckBox, QGroupBox, QFrame, QDialog, QDialogButtonBox,
    QInputDialog, QFileDialog, QMessageBox, QSizePolicy,
    QDoubleSpinBox, QSpinBox, QToolButton, QMenu, QColorDialog,
    QCalendarWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTextEdit, QProgressBar, QProgressDialog, QLayout,
    QAbstractSpinBox, QAbstractScrollArea, QSlider, QDateTimeEdit, QTimeEdit,
    QTabBar, QAbstractSlider, QScrollBar,
)

# ── matplotlib ─────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("QtAgg")
matplotlib.rcParams["axes.facecolor"]   = "white"
matplotlib.rcParams["figure.facecolor"] = "white"
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.widgets import SpanSelector, RectangleSelector


class _EdgeSpanSelector(SpanSelector):
    """SpanSelector whose drag keeps going when the mouse leaves the plot.

    matplotlib replaces an event outside the axes (no xdata) by the last one
    inside, so a drag past the edge froze a few pixels short of it and the
    selection missed the "follow the newest point" edge. Here the position is
    taken from the pixels instead and clipped to the plot, so running off the
    side lands exactly on the edge.
    """

    def _clean_event(self, event):
        if event.xdata is None and getattr(event, "x", None) is not None:
            import copy
            event = copy.copy(event)
            event.inaxes = None        # _get_data_coords then reads the pixels
            event.xdata = event.ydata = 0.0
            event.xdata, event.ydata = self._get_data(event)
            # The drag handler reads the position again; as an event "in" the
            # plot it takes the clipped xdata instead of the raw pixels.
            event.inaxes = self.ax
            self._prev_event = event
            return event
        return super()._clean_event(event)

# ── Non-UI helpers from cpva_core ──────────────────────────────────────────
from cpva_core import (
    TZ_PRAGUE, now_ns, dt_to_ns, ns_to_local_str, _fmt_cursor_value,
    _fmt_percent_of,
    parse_user_datetime, shorten_pv_name, make_pv_query_matcher,
    cpva_fetch_samples, cpva_fetch_samples_chunked, cpva_decode_value,
    cpva_fetch_channels, safe_divide,
    load_config, save_config, load_presets, save_presets,
    load_condition_presets, save_condition_presets,
    load_custom_pvs, save_custom_pvs, load_ramping_repository,
    CONFIG_FILE, PRESETS_FILE, CONDITIONS_PRESETS_FILE, CUSTOM_PVS_FILE,
    CHUNK_SIZE_NS, CPVA_HTTP_TIMEOUT, MASTER_RAMP_PV, SAMPLE_HOLD_MIN_GAP_MS,
    RAMPING_PV_MAP, PV_TO_RAMPING,
    _open_path, _looks_like_image_path, _image_file_size, _resolve_image_path,
    APP_DIR, find_shots,
)

try:
    from cpva_core import DATA_REPOSITORY_DIR
except ImportError:
    DATA_REPOSITORY_DIR = _ROOT / "Diagnostic" / "DataRepository"

from sp_t import SpectraWidget

# Spectra's graph toolbar, reused here so both programs behave the same. It is a
# plain NavigationToolbar2QT subclass with no Spectra state in it — it only drops
# "Export values" from the margins dialog and reports when that dialog closes.
# If Spectra ever moves it, fall back to the stock toolbar rather than failing.
try:
    from sp_t import _CustomToolbar as _MplToolbar, _TB_STYLE, _TB_HINTS
except ImportError:      # pragma: no cover - Spectra layout changed
    _MplToolbar = NavigationToolbar2QT
    _TB_STYLE   = ""
    _TB_HINTS   = {}
# (Spectra's _AxisLimitsDialog is not used here: the banded graph has one Y
# axis per PV and a time X axis — see _GraphAxisLimitsDialog.)


def _make_mpl_toolbar(nav_cls, canvas, parent=None):
    """Build a matplotlib toolbar whose icons are actually visible.

    matplotlib tints the toolbar icons ONCE, AT CONSTRUCTION, and only when the
    palette background is dark: it recolours the black PNGs to the palette
    foreground. On a dark Windows theme that foreground is near-white, so the
    buttons come out white-on-light and read as blank. It never re-tints, so
    fixing the palette afterwards does nothing — the parent has to be right
    before the toolbar exists.

    So: parent it to a host widget carrying a LIGHT palette (tinting is skipped,
    the original black icons survive), then paint a light toolbar background to
    match.

    (Pulser Monitor and Image Tools carry the same helper for the same reason.)
    """
    host = QWidget(parent)
    hp = host.palette()
    hp.setColor(QPalette.ColorRole.Window,     QColor("#ffffff"))
    hp.setColor(QPalette.ColorRole.Base,       QColor("#ffffff"))
    hp.setColor(QPalette.ColorRole.Button,     QColor("#ffffff"))
    hp.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    hp.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
    hp.setColor(QPalette.ColorRole.Text,       QColor("#000000"))
    host.setPalette(hp)

    toolbar = nav_cls(canvas, host)
    toolbar.setPalette(hp)
    toolbar.setStyleSheet(_TB_STYLE or
                          "QToolBar{background:white;border:none;}"
                          "QToolButton{background:transparent;color:black;}")
    # Plain-language tooltips instead of matplotlib's terse defaults.
    for act in toolbar.actions():
        hint = _TB_HINTS.get(act.text())
        if hint:
            act.setToolTip(hint)
    # THE HOST HAS DONE ITS ONE JOB and must now get out of the way. The caller puts
    # the TOOLBAR into a layout, which re-parents it away and leaves this widget
    # behind as a child no layout owns — and such a child sits at (0, 0) of its
    # parent at its default size. Painted in the window's own ground it is invisible
    # until something scrolls under it, and it stands still while the panel moves.
    host.setFixedSize(0, 0)
    host.hide()
    return toolbar


# ── Graph tool strip: drawn icons ──────────────────────────────────────────
# The strip above the graph shows matplotlib's own toolbar pictures, re-inked
# here in a fixed colour for every button state (left to matplotlib, a dark
# Windows theme turns them white on the white bar). The QPainter drawings below
# are the fallback when a picture is missing, and the loupe's only artwork.
_GICON_BOX = 20.0
_GICON_PX = 24
_GICON_SIZES = (24, 36, 48)            # 100 % / 150 % / 200 % display scaling
_GINK_NORMAL = "#000000"
_GINK_CHECKED = "#000000"              # the checked background is light blue
_GINK_DISABLED = "#a0a8b2"
_GICON_CACHE: dict = {}

# The look of the matplotlib toolbar the graph always had: flat buttons on the
# white bar, a light-blue tint on hover and while a mode is on.
_GRAPH_TOOLBTN_QSS = (
    "QToolButton { padding: 0px; margin: 0px; border: none;"
    " border-radius: 3px; background: transparent; color: #000000; }"
    "QToolButton:hover { background: #e8f0fe; }"
    "QToolButton:checked { background: #bbd4f8; }"
    "QToolButton:checked:hover { background: #a8c6f5; }"
    "QToolButton:pressed { background: #bbd4f8; }"
    "QToolButton::menu-indicator { image: none; width: 0px; }"
)


def _gpen(c, w=2.0, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin,
          dash=None):
    p = QPen(QColor(c))
    p.setWidthF(w)
    p.setCapStyle(cap)
    p.setJoinStyle(join)
    if dash:
        p.setDashPattern(dash)
    return p


def _gicon_head(p, c, tip, dx, dy, size=3.1):
    """Filled arrow head at `tip`, pointing along the unit vector (dx, dy)."""
    base = QPointF(tip.x() - dx * size * 1.5, tip.y() - dy * size * 1.5)
    px, py = -dy, dx
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(c)))
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(base.x() + px * size * 0.82, base.y() + py * size * 0.82),
        QPointF(base.x() - px * size * 0.82, base.y() - py * size * 0.82)]))


def _gico_home(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.0))
    p.drawPolyline(QPolygonF([QPointF(2.8, 10.2), QPointF(10.0, 3.4),
                              QPointF(17.2, 10.2)]))
    p.drawPolyline(QPolygonF([QPointF(5.2, 8.6), QPointF(5.2, 16.6),
                              QPointF(14.8, 16.6), QPointF(14.8, 8.6)]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(c)))
    p.drawRect(QRectF(8.4, 11.4, 3.2, 5.2))          # the door


def _gico_back(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.4))
    p.drawLine(QPointF(16.6, 10.0), QPointF(8.0, 10.0))
    _gicon_head(p, c, QPointF(3.2, 10.0), -1.0, 0.0, 3.4)


def _gico_forward(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.4))
    p.drawLine(QPointF(3.4, 10.0), QPointF(12.0, 10.0))
    _gicon_head(p, c, QPointF(16.8, 10.0), 1.0, 0.0, 3.4)


def _gico_pan(p, c):
    # Solid palm with three fingers and a thumb (hollow turns to mush at 20 px).
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(c)))
    path = QPainterPath()
    path.addRoundedRect(QRectF(5.0, 8.0, 10.0, 9.5), 3.2, 3.2)
    p.drawPath(path)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.6))
    p.drawLine(QPointF(7.0, 9.0), QPointF(7.0, 5.2))
    p.drawLine(QPointF(10.0, 9.0), QPointF(10.0, 3.6))
    p.drawLine(QPointF(13.0, 9.0), QPointF(13.0, 5.6))
    p.drawLine(QPointF(5.4, 12.2), QPointF(3.2, 9.6))


def _gico_zoom(p, c):
    # Magnifying glass: drag a rectangle to zoom into it.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.0))
    p.drawEllipse(QPointF(8.6, 8.6), 5.0, 5.0)
    p.setPen(_gpen(c, 2.6))
    p.drawLine(QPointF(12.5, 12.5), QPointF(17.0, 17.0))


def _gico_loupe(p, c):
    # A small framed window with a cross-hair in it — the preview that follows
    # the mouse. Deliberately NOT a magnifying glass: that one is Zoom.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.0, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(2.6, 3.6, 14.8, 12.8), 1.6, 1.6)
    p.setPen(_gpen(c, 1.6))
    p.drawLine(QPointF(10.0, 5.8), QPointF(10.0, 8.4))
    p.drawLine(QPointF(10.0, 11.6), QPointF(10.0, 14.2))
    p.drawLine(QPointF(4.8, 10.0), QPointF(8.2, 10.0))
    p.drawLine(QPointF(11.8, 10.0), QPointF(15.2, 10.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(c)))
    p.drawEllipse(QPointF(10.0, 10.0), 1.4, 1.4)


def _gico_margins(p, c):
    # Outer frame = the figure, dashed inner frame = the plot area inside it.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.0, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRect(QRectF(2.6, 2.6, 14.8, 14.8))
    p.setPen(_gpen(c, 1.5, cap=Qt.PenCapStyle.FlatCap,
                   join=Qt.PenJoinStyle.MiterJoin, dash=[1.6, 1.2]))
    p.drawRect(QRectF(6.6, 6.0, 7.6, 7.6))


def _gico_save(p, c):
    # A floppy disk: body, shutter at the top, label at the bottom.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 2.0, join=Qt.PenJoinStyle.MiterJoin))
    p.drawPolygon(QPolygonF([QPointF(3.0, 3.0), QPointF(14.2, 3.0),
                             QPointF(17.0, 5.8), QPointF(17.0, 17.0),
                             QPointF(3.0, 17.0)]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(c)))
    p.drawRect(QRectF(6.2, 3.0, 6.6, 4.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_gpen(c, 1.6, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRect(QRectF(6.0, 11.0, 8.0, 6.0))


_GICON_RECIPES = {
    "home": _gico_home, "back": _gico_back, "forward": _gico_forward,
    "pan": _gico_pan, "zoom": _gico_zoom, "loupe": _gico_loupe,
    "margins": _gico_margins, "save": _gico_save,
}

# The strip shows matplotlib's own toolbar pictures (the look the graph always
# had); the drawn recipes above are the fallback when a picture is missing, and
# the only artwork for the loupe, which matplotlib has no picture for.
_MPL_ICON_FILES = {
    "home": "home", "back": "back", "forward": "forward", "pan": "move",
    "zoom": "zoom_to_rect", "margins": "subplots", "save": "filesave",
}


def _mpl_icon_image(name):
    """matplotlib's large toolbar picture for strip button `name`, or None."""
    fn = _MPL_ICON_FILES.get(name)
    if not fn:
        return None
    try:
        import matplotlib as _mpl
        path = os.path.join(_mpl.get_data_path(), "images", f"{fn}_large.png")
        img = QImage(path)
    except Exception:
        return None
    if img.isNull():
        return None
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _graph_icon(name):
    """The icon for one strip button, with its own artwork for every state.

    Every mode is spelled out: left alone, Qt fades the disabled version until
    it is barely there and keeps the dark artwork on the blue checked button."""
    ic = _GICON_CACHE.get(name)
    if ic is not None:
        return ic
    ic = QIcon()
    src = _mpl_icon_image(name)
    for mode, state, ink in (
            (QIcon.Mode.Normal,   QIcon.State.Off, _GINK_NORMAL),
            (QIcon.Mode.Active,   QIcon.State.Off, _GINK_NORMAL),
            (QIcon.Mode.Selected, QIcon.State.Off, _GINK_NORMAL),
            (QIcon.Mode.Normal,   QIcon.State.On,  _GINK_CHECKED),
            (QIcon.Mode.Active,   QIcon.State.On,  _GINK_CHECKED),
            (QIcon.Mode.Selected, QIcon.State.On,  _GINK_CHECKED),
            (QIcon.Mode.Disabled, QIcon.State.Off, _GINK_DISABLED),
            (QIcon.Mode.Disabled, QIcon.State.On,  _GINK_DISABLED)):
        for px in _GICON_SIZES:
            pm = QPixmap(px, px)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            if src is not None:
                # matplotlib's own artwork, its shape only, re-inked here so a
                # dark theme can never turn it white.
                p.drawImage(QRectF(0, 0, px, px), src)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                p.fillRect(QRectF(0, 0, px, px), QColor(ink))
            else:
                p.scale(px / _GICON_BOX, px / _GICON_BOX)
                _GICON_RECIPES[name](p, ink)
            p.end()
            ic.addPixmap(pm, mode, state)
    _GICON_CACHE[name] = ic
    return ic


# ── Styles ─────────────────────────────────────────────────────────────────
_APP_STYLESHEET = """
QWidget      { background: #f3f3f3; color: #111;
               font-family: "Segoe UI", Arial, sans-serif; }
QLabel       { background: transparent; }
QPushButton  { padding: 5px 8px; border-radius: 3px; border: 1px solid #bbb; }
QPushButton:hover { background: #dde8ff; }
/* Styling a QGroupBox border switches off Qt's own room for the caption, so
   without these two rules the caption is painted straight over the first row
   of whatever is inside the box. Reserve the space here, once, for every box
   in the program. */
QGroupBox    { border: 1px solid #ccc; border-radius: 4px; font-weight: 600;
               margin-top: 9px; padding: 14px 6px 6px 6px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 8px; padding: 0 4px; background: #f3f3f3; }
QTabWidget::pane { border: 1px solid #ccc; }
QTabBar::tab {
    background: #e8e8e8; color: #444;
    padding: 6px 18px; border: 1px solid #ccc;
    border-bottom: none; border-radius: 3px 3px 0 0;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #f3f3f3; color: #111; font-weight: 600; }
QTabBar::tab:hover    { background: #d8e8ff; }
QToolTip { background: #ffffcc; color: #111; border: 1px solid #aaa; padding: 4px; }
/* The QWidget rule above also paints every menu entry, which wiped out the
   hover highlight and made greyed-out entries look live. */
QMenu { background: #ffffff; color: #111; border: 1px solid #bbb; padding: 2px; }
QMenu::item { background: transparent; padding: 4px 22px 4px 18px; }
QMenu::item:selected { background: #DCEBFF; color: #111; }
QMenu::item:disabled { color: #9a9a9a; }
QMenu::separator { height: 1px; background: #d0d0d0; margin: 3px 6px; }
QScrollBar:vertical { width: 8px; background: #eee; }
QScrollBar::handle:vertical { background: #bbb; border-radius: 4px; }
/* Item-view check indicators (Show / Auto / Grid columns) must stay visible
   against the table background — Fusion's default indicator blends in. */
QTableView::indicator, QTreeView::indicator {
    width: 16px; height: 16px;
    border: 2px solid #4a4a4a; border-radius: 3px; background: #ffffff;
}
QTableView::indicator:hover, QTreeView::indicator:hover {
    border-color: #1565C0; background: #e8f0fe;
}
QTableView::indicator:checked, QTreeView::indicator:checked {
    border-color: #1565C0; background: #1565C0;
}
"""

_BTN_PRIMARY = (
    "QPushButton { background:#1565C0; color:white; font-weight:700; "
    "padding:7px 10px; border-radius:4px; }"
    "QPushButton:hover { background:#0D47A1; }"
    "QPushButton:disabled { background:#bbb; color:#888; }"
)
_BTN_SUCCESS = (
    "QPushButton { background:#2E7D32; color:white; font-weight:700; "
    "padding:7px 10px; border-radius:4px; }"
    "QPushButton:hover { background:#1B5E20; }"
    "QPushButton:disabled { background:#bbb; color:#888; }"
)
_BTN_DANGER = (
    "QPushButton { background:#B71C1C; color:white; font-weight:700; "
    "padding:7px 10px; border-radius:4px; }"
    "QPushButton:hover { background:#7F0000; }"
)

_CHK_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; }
QCheckBox::indicator { width: 17px; height: 17px;
    border: 2px solid #4a4a4a; border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover  { border-color: #1565C0; background: #e8f0fe; }
QCheckBox::indicator:checked { border-color: #1565C0; background: #1565C0; }
"""

_CAL_STYLE = """
QCalendarWidget QWidget { background: #ffffff; color: #111; }
QCalendarWidget QAbstractItemView:enabled {
    background: #ffffff; color: #111;
    selection-background-color: #1565C0; selection-color: white;
}
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #eeeeee; }
QCalendarWidget QToolButton {
    color: #222; background: transparent; font-weight: 700; font-size: 13px;
    border-radius: 3px; padding: 3px 6px;
}
QCalendarWidget QToolButton:hover { background: #d0d0d0; }
QCalendarWidget QSpinBox { color: #222; background: #eeeeee; border: none; font-weight: 700; }
"""

_GRAPH_COLORS = [
    "#1976D2", "#E53935", "#43A047", "#FB8C00", "#8E24AA",
    "#00ACC1", "#F4511E", "#3949AB", "#00897B", "#FFB300",
    "#D81B60", "#6D4C41", "#546E7A", "#039BE5", "#7CB342",
]

# ── Graph appearance / performance options ─────────────────────────────────
# Everything the user can tune from the "Graph settings" dialog (Graph tab).
# Stored under config["graph_opts"]; every key here is also the default, so an
# older config file simply picks the defaults up for keys it does not have.
# Distances are in PIXELS at the figure's 96 dpi unless noted otherwise.
_GRAPH_OPTS_DEFAULTS = {
    # Fonts (pt)
    "font_size":        11,     # Y-axis titles + X label (mirrors the Font spin)
    "tick_font_delta":  -1,     # tick-number size = font_size + this
    "cursor_font_delta": 0,     # cursor readout size = font_size + this
    # Left axis columns
    # Measured with testing/probe_y_axis_columns.py and looked at: the title's
    # capitals reach the edge of its text box, so 0 px puts the neighbour's
    # spine ON the letters; 3 px is a clear gap at every font size. The title
    # side towards its own numbers carries the descender space, so 1 px is
    # enough there. (Were 6 / 5 / 8 — a column was ~20 % wider.)
    "axis_gap_px":       5,     # whitespace between a title and the axis left of it
    "label_pad_px":      0,     # gap between a title and its OWN tick numbers
    "outer_margin_px":   4,     # whitespace left of the outermost title
    "show_axis_titles":  True,
    "y_ticks_max":       6,     # upper bound on major Y ticks per axis
    # Minor Y ticks are decoration, but matplotlib builds a full Tick object
    # (2 lines + 2 texts) for every one of them — with a dozen stacked axes that
    # is ~400 objects and by far the largest single cost of a redraw, so they are
    # off by default and can be switched back on in Graph settings.
    "y_minor_ticks":     False,
    # Time axis
    # Upper bound on time stamps across the plot. 12 (was 8) puts the round step
    # one rung finer: 1 h → every 5 min instead of 10, 1 day → every 2 h, not 3.
    "x_ticks_max":       12,
    "x_tick_seconds":    0,     # fixed spacing in s; 0 = pick a round one to fit
    "x_time_format":     "auto",  # auto | hms (12:34:56) | hm (12:34)
    # Plot rectangle (figure fractions)
    "margin_right":      0.015,
    "margin_top":        0.97,
    "margin_bottom":     0.12,
    "band_pad_frac":     0.12,  # gap inside each PV band (fraction of band height)
    # Cursor / performance
    "cursor_value_boxes": True,  # per-PV value box at the crosshair
    "cursor_boxes_max":   20,    # boxes are dropped above this many visible PVs
    "line_markers":       True,  # dots on short traces
    # Live mode pacing (ms). The graph and the table are refreshed on separate
    # clocks: the graph update is cheap and runs almost every poll, while
    # rebuilding the table walks the whole accumulated history and needs a slower
    # one. Sharing a single clock is what made the whole window feel ~5 s slow.
    "live_poll_ms":       300,   # how often the archive is asked for new values
    "live_graph_min_ms":  300,   # shortest gap between graph refreshes
    "live_table_ms":     1500,   # shortest gap between table rebuilds
}

# Colour ramp for the XY plot, marking how far through the window each point is.
# Built by hand rather than taken from matplotlib: the stock ramps (plasma,
# viridis, …) end in a pale yellow that is almost invisible on white, so the newest
# points — the interesting ones — were the hardest to see. This one sweeps the
# whole hue circle (green → teal → blue → purple → magenta → red) so neighbouring
# points are easy to tell apart, and every stop is deliberately dark or fully
# saturated: nothing in the ramp ever goes pale on a white plot.
_XY_CMAP = LinearSegmentedColormap.from_list(
    "csslog_xy",
    ["#1B5E20",   # dark green  — oldest
     "#00695C",   # teal
     "#0D47A1",   # deep blue
     "#4A148C",   # purple
     "#AD1457",   # magenta
     "#D50000"],  # red         — newest
    N=256)

# The one mode switch in the sidebar. Green = following "now" is off and the
# chosen window stands still; orange = following. Same size and shape either way,
# so the button does not jump when it is pressed.
_LIVE_BTN_OFF_STYLE = (
    "QPushButton{background:#2E7D32;color:white;font-weight:700;"
    "padding:4px 6px;border-radius:4px;font-size:13px;border:none;}"
    "QPushButton:hover{background:#1B5E20;}"
    "QPushButton:disabled{background:#bbb;color:#444;}")
_LIVE_BTN_ON_STYLE = (
    "QPushButton{background:#F57F17;color:white;font-weight:700;"
    "padding:4px 6px;border-radius:4px;font-size:13px;border:none;}"
    "QPushButton:hover{background:#EF6C00;}"
    "QPushButton:disabled{background:#bbb;color:#444;}")
# Height of the Time window / Live pair at the top of the sidebar.
_TW_ROW_BTN_H = 36

# Grid line styles, handed out in this order to the channels that have Grid
# ticked, so several grids on one plot can be told apart. The colour already says
# which channel a grid belongs to; the style is the second, colour-blind-safe cue
# (and it survives two channels whose colours happen to sit close together).
_GRID_STYLES = [
    ("solid",              "-"),
    ("dashed",             "--"),
    ("dotted",             ":"),
    ("dash-dot",           "-."),
    ("long dashes",        (0, (6, 2))),
    ("dash-dot-dot",       (0, (5, 1, 1, 1, 1, 1))),
    ("dot-dot-dash",       (0, (1, 1, 1, 1, 5, 2))),
    ("wide dashes",        (0, (9, 3))),
]

# Fixed spacings offered for the time stamps on the X axis (label, seconds).
# 0 = let the graph pick a round step that fits the window.
_X_TICK_STEPS = [
    ("Automatic", 0), ("1 s", 1), ("2 s", 2), ("5 s", 5), ("10 s", 10),
    ("15 s", 15), ("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300),
    ("10 min", 600), ("15 min", 900), ("30 min", 1800), ("1 h", 3600),
    ("2 h", 7200), ("3 h", 10800), ("6 h", 21600), ("12 h", 43200),
    ("1 day", 86400),
]

# ── Custom-PV expression variables ─────────────────────────────────────────
# Channel letters (A, B, … AA) used as variables in custom-PV expressions.
# The lookarounds keep the tokens away from anything that only *looks* like a
# letter: the E in 1E5, and the lowercase safe names (math, abs, min, max,
# round) exposed to eval. True/False/None are not all-caps, so they are safe.
_CPV_VAR_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z]+(?![A-Za-z0-9_])")

# Letter → PV mapping that the pre-"bindings" custom_pvs.json entries were
# written against (the startup / "Operation" PV order). Used once, by
# _migrate_custom_pv_bindings, to give those entries explicit bindings — kept
# as a literal so the migration cannot be corrupted by whichever preset
# happens to be loaded at the time. New entries always store their own.
_CPV_LEGACY_LETTERS = {
    "A": "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "B": "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "C": "L3-PFWP6-MTR03-1:RawPos",
    "D": "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "E": "L3-PM03-023:Energy",
    "F": "L3-PM03-025:Energy",
    "G": "L3-VCS-LN36:OPEN",
    "H": "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
}


# Everything a custom-PV formula may reach. No builtins: an expression comes
# from the user, but it must not be able to touch the file system or the app.
_CPV_SAFE_ENV = {"__builtins__": {}, "abs": abs, "min": min,
                 "max": max, "round": round, "math": math}


def _np_min(*a):
    return functools.reduce(np.minimum, a)


def _np_max(*a):
    return functools.reduce(np.maximum, a)


# The same names over whole columns. The builtins fail on an array (and
# np.minimum's third argument is ``out``, so min(A, B, C) needs the reduce).
_CPV_NP_ENV = {"__builtins__": {}, "abs": np.abs, "min": _np_min,
               "max": _np_max, "round": np.round, "math": np}


def _cpv_numeric(v):
    """A source value as a formula sees it: a number, else missing (NaN)."""
    return float(v) if isinstance(v, (int, float)) else np.nan


def _cpv_eval_columns(code, cols, n):
    """Evaluate one compiled formula over whole columns at once.

    ``cols`` maps each letter to a float array of length ``n`` with NaN where
    the source has no number. Returns a float array, NaN where there is no
    result. A formula numpy cannot take as a whole (``A if A > 0 else B``,
    ``math.factorial``) is evaluated element by element with the scalar
    environment instead — the answer is the same, only slower.
    """
    if n == 0:
        return np.empty(0)
    with np.errstate(all="ignore"):
        try:
            res = eval(code, _CPV_NP_ENV, dict(cols))
            res = np.asarray(res, dtype=np.float64)
            if res.shape != (n,):
                res = np.broadcast_to(res, (n,)).astype(np.float64)
            return np.where(np.isfinite(res), res, np.nan)
        except Exception:
            pass
    out = np.full(n, np.nan)
    letters = list(cols)
    arrs = [cols[k] for k in letters]
    for i in range(n):
        ns = {}
        for k, a in zip(letters, arrs):
            x = a[i]
            ns[k] = None if x != x else float(x)
        try:
            r = eval(code, _CPV_SAFE_ENV, ns)
        except Exception:
            continue
        if isinstance(r, (int, float)) and math.isfinite(r):
            out[i] = float(r)
    return out


def _held_column(ts_list, samples, pre_val):
    """Each grid time → the value ``samples`` held at it (the last sample at or
    before it), ``pre_val`` before the first one. ``ts_list`` is an int64 array
    of sorted ns times; ``samples`` the PV's ``(ts_ns, value, units)`` list."""
    n = ts_list.size
    if not samples:
        return np.full(n, _cpv_numeric(pre_val) if pre_val is not None else np.nan)
    m = len(samples)
    if isinstance(samples, _SampleArrays):
        s_ts, s_v = samples.ts.astype(np.int64), samples.v
    else:
        s_ts = np.fromiter((s[0] for s in samples), dtype=np.int64, count=m)
        s_v = np.fromiter((_cpv_numeric(s[1]) for s in samples),
                          dtype=np.float64, count=m)
    if m > 1 and np.any(s_ts[1:] < s_ts[:-1]):
        order = np.argsort(s_ts, kind="stable")
        s_ts, s_v = s_ts[order], s_v[order]
    idx = np.searchsorted(s_ts, ts_list, side="right") - 1
    out = s_v[np.maximum(idx, 0)]
    if np.any(idx < 0):
        out = out.copy()
        out[idx < 0] = _cpv_numeric(pre_val) if pre_val is not None else np.nan
    return out


def _cpv_vars(expr: str) -> list:
    """Ordered unique channel-letter variables appearing in ``expr``."""
    out = []
    for m in _CPV_VAR_RE.finditer(expr or ""):
        if m.group(0) not in out:
            out.append(m.group(0))
    return out


def _cpv_rewrite(expr: str, letter_map: dict) -> str:
    """Rename the channel letters in ``expr`` via ``letter_map`` (old -> new).

    Tokens missing from the map are left untouched, so this is safe to run in
    either direction (stored letters -> currently displayed letters and back).
    """
    if not expr or not letter_map:
        return expr or ""
    return _CPV_VAR_RE.sub(lambda m: letter_map.get(m.group(0), m.group(0)), expr)


def _cpv_to_display(entry: dict, letter_by_pv: dict):
    """Rewrite a stored expression into the letters that are valid right now.

    Returns ``(expr_text, unbound)``; ``unbound`` lists the letters carrying no
    binding — they read nothing until a PV is picked for them.
    """
    expr     = entry.get("expr", "") or ""
    bindings = entry.get("bindings") or {}
    lmap = {}
    for letter, pv in bindings.items():
        cur = letter_by_pv.get(pv)
        if cur:
            lmap[letter] = cur
    unbound = [v for v in _cpv_vars(expr) if v not in bindings]
    return _cpv_rewrite(expr, lmap), unbound


def _cpv_from_display(expr_text: str, pv_by_letter: dict):
    """Canonicalise an expression typed in the dialog into ``(expr, bindings)``:
    the letters stay as written, and each one records the PV it stands for."""
    expr = (expr_text or "").strip()
    bindings = {}
    for letter in _cpv_vars(expr):
        pv = pv_by_letter.get(letter)
        if pv:
            bindings[letter] = pv
    return expr, bindings

# ── Table export ───────────────────────────────────────────────────────────

# Channels whose timestamps say nothing about when a shot happened: a rate
# readback, a timing/status word, a beam fate, a valve or shutter state, a motor
# position. They are written at their own pace, so letting them mark shots turns
# a shot table into a table of "something was archived here". They are still
# exported — held at their last value — just never used as the grid.
_NON_SHOT_PV_HINTS = (
    "sysrate", "sys_rate", "rate", "timing", "beamfate", "beam_fate", "fate",
    "state", "status", "mode", "enable", "open", "close", "shutter", "valve",
    "heartbeat", "rawpos", "position", "temp", "press", "flow", "alarm",
)

# One export slice. The window is read a slice at a time and appended to the
# file, so a year-long export costs one slice of samples in memory, not a year.
#
# Two hours, and the size matters for two reasons that pull the same way:
#   * RAM. Raw samples arrive as one dict per sample (with its own metaData
#     dict), so a whole day of a 3 Hz channel is ~150 000 of them — a dozen
#     channels at once is most of a gigabyte. Two hours is tens of megabytes.
#   * it costs nothing. The number of HTTP requests is set by the span the
#     archiver will actually serve, not by the slice, and a dozen channels
#     already fill the fetch pool, so a small slice is just as parallel.
# Keep it at or below four hours: above that the adaptive fetch adds a
# reachability probe per slice.
_EXPORT_SLICE_NS = 2 * 3600 * 1_000_000_000

# The raw read behind the selection statistics stops above this many samples
# (all channels together, about 16 bytes each in memory): a raw read of many
# channels over days once took 9 GB and froze the whole PC.
_SEL_RAW_MAX_SAMPLES = 20_000_000


def _export_slices(start_ns: int, end_ns: int) -> list:
    """[start, end) cut into contiguous slices. The last one includes ``end``."""
    if end_ns <= start_ns:
        return []
    out = []
    lo = int(start_ns)
    while lo < end_ns:
        hi = min(int(end_ns), lo + _EXPORT_SLICE_NS)
        out.append((lo, hi))
        lo = hi
    # Make the final slice inclusive of the window end, or a sample landing
    # exactly on it is dropped.
    lo, hi = out[-1]
    out[-1] = (lo, hi + 1)
    return out


def _export_slice_rows(samples, basis, channels, held, gap_ns, lo_ns, hi_ns):
    """Turn one slice of samples into shot rows.

    ``samples`` is ``{pv: [(ts_ns, value), …]}``, time-ordered. The rows are the
    ``basis`` channels' own sample times, merged whenever they sit closer
    together than ``gap_ns`` (the same rule the table on screen uses), so the
    channels that are written at their own pace cannot add rows of their own.
    Every channel is then read at its newest sample at or before each shot, and
    where it has none it keeps the value it was holding — that is what pairs a
    slow channel onto a shot instead of leaving a hole.

    Returns ``(rows, held)``; ``held`` carries the last value of every channel
    over into the next slice.
    """
    ts_by_pv, val_by_pv = {}, {}
    for pv in channels:
        s = samples.get(pv) or []
        ts_by_pv[pv]  = np.array([t for t, _v in s], dtype=np.int64)
        val_by_pv[pv] = [v for _t, v in s]
    parts = [ts_by_pv[pv] for pv in basis if ts_by_pv.get(pv) is not None
             and ts_by_pv[pv].size]
    rows = []
    if parts:
        all_ts = np.sort(np.concatenate(parts))
        fresh = np.ones(all_ts.size, dtype=bool)
        if all_ts.size > 1:
            fresh[1:] = np.diff(all_ts) > gap_ns
        starts = np.flatnonzero(fresh)
        ends   = np.append(starts[1:], all_ts.size) - 1
        shots  = all_ts[ends]
        # The archiver also returns the sample just before a request's own
        # start, so a shot can fall outside this slice — it belongs to the
        # neighbour that owns that stretch of time.
        shots = shots[(shots >= lo_ns) & (shots < hi_ns)]
        if shots.size:
            idx = {}
            for pv in channels:
                ts = ts_by_pv[pv]
                idx[pv] = (np.searchsorted(ts, shots, side="right") - 1
                           if ts.size else np.full(shots.size, -1, dtype=np.int64))
            for i in range(shots.size):
                vals = {}
                for pv in channels:
                    j = int(idx[pv][i])
                    if j >= 0:
                        v = val_by_pv[pv][j]
                        held[pv] = v
                    else:
                        v = held.get(pv)
                    vals[pv] = v
                rows.append((int(shots[i]), vals))
    # Whatever arrived after the last shot is still the value the next slice
    # starts out holding.
    for pv in channels:
        if val_by_pv.get(pv):
            held[pv] = val_by_pv[pv][-1]
    return rows, held


def _export_eval(code, srcs, vals):
    """One custom-PV formula on one exported row, or None if it cannot be read."""
    if code is None:
        return None
    ns = {}
    for letter, pv in srcs:
        v = vals.get(pv)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
        ns[letter] = v
    try:
        return eval(code, _CPV_SAFE_ENV, ns)
    except Exception:
        return None


def _export_row_ok(vals, conditions) -> bool:
    """Same rule as the Conditions filter on screen: every condition channel
    must be present and numeric and inside its range."""
    for cond in conditions:
        pv = cond.get("pv")
        if not pv:
            continue
        v = vals.get(pv)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False
        if not _cond_value_passes(v, cond.get("min"), cond.get("max"),
                                  cond.get("outside", False)):
            return False
    return True


def _export_fmt(val, decimal_comma: bool) -> str:
    """One cell. Numbers keep their precision, a word value goes out as it is
    (the csv writer quotes it), and nothing is ever silently emptied."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return ""
        txt = f"{val:.10g}"
        return txt.replace(".", ",") if decimal_comma else txt
    if isinstance(val, int):
        return str(val)
    return str(val)


# ── Day cache ───────────────────────────────────────────────────────────────

_DAY_CACHE = None


def _day_cache():
    """The one day cache every load (and every tab) shares — see cpva_cache.

    On in the built program, where it lives on the scratch share. A run from
    source (development, and every test) leaves it off unless CPVA_CACHE_DIR
    names a folder: a test's fake archive must never land in the shared cache
    everybody else reads. CPVA_CACHE_DIR="" switches it off in the program too.
    """
    global _DAY_CACHE
    if _DAY_CACHE is None:
        from cpva_cache import DayCache
        env = os.environ.get("CPVA_CACHE_DIR")
        if env is not None:
            root = env
        elif getattr(sys, "frozen", False):
            root = None            # look for the scratch share, in the background
        else:
            root = ""
        _DAY_CACHE = DayCache(root)
    return _DAY_CACHE


# ── Signal helpers ─────────────────────────────────────────────────────────

class _LoadSig(QObject):
    done     = Signal(object)
    error    = Signal(str)
    progress = Signal(str)
    pct      = Signal(int)    # 0-100 for progress bar
    note     = Signal(str)    # one line for the Log tab, while the load runs
    partial  = Signal(object)  # what has arrived so far, for a growing picture

class _PartialFeed:
    """What has arrived so far during a read, sent to the window as a growing
    picture now and then (``sig.partial``). The fetch threads only APPEND, under
    a lock held for a moment; merging happens in the feed's own thread. A new
    picture goes out only when the window has finished drawing the last one
    (``ready``) and never sooner than ``gap_fn()`` seconds after it."""

    def __init__(self, sig, pvs, start_ns, end_ns, epoch, cancel, decode,
                 gap_fn):
        self._sig, self._pvs = sig, list(pvs)
        self._start_ns, self._end_ns, self._epoch = start_ns, end_ns, epoch
        self._cancel, self._decode, self._gap_fn = cancel, decode, gap_fn
        self._acc = {pv: [] for pv in pvs}
        self._acc_mm = {pv: [] for pv in pvs}
        self._lock = threading.Lock()
        self.ready = threading.Event()
        self.ready.set()
        self._last = time.monotonic()
        self.cov = 0.0
        self.edge = end_ns
        self._dirty = False
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True,
                         name="css-logger-snapshots").start()

    def on_chunk(self, pv, raw):
        rows = self._decode(raw)
        if not rows:
            return
        mm = _minmax_arrays(raw)
        with self._lock:
            self._acc.setdefault(pv, []).append(rows)
            if mm is not None:
                self._acc_mm.setdefault(pv, []).append(mm)
            self._dirty = True

    def on_tile(self, e):
        self.edge = e
        self._dirty = True

    def on_cover(self, frac):
        self.cov = frac

    def stop(self):
        self._stop.set()

    @staticmethod
    def _merge(chunks):
        rows = [r for c in chunks for r in c]
        rows.sort(key=lambda r: r[0])
        out, last = [], None
        for r in rows:
            if r[0] != last:
                out.append(r)
                last = r[0]
        return out

    @staticmethod
    def _join_mm(parts):
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        ts = np.concatenate([p[0] for p in parts])
        order = np.argsort(ts, kind="stable")
        return (ts[order], np.concatenate([p[1] for p in parts])[order],
                np.concatenate([p[2] for p in parts])[order])

    def _run(self):
        while not self._stop.wait(0.25):
            if not self._dirty or not self.ready.is_set() or self._cancel():
                continue
            # Nearly read: the full picture is moments away.
            if self.cov >= 0.9 or time.monotonic() - self._last < self._gap_fn():
                continue
            with self._lock:
                self._dirty = False
                parts = {p: list(v) for p, v in self._acc.items()}
                mm_parts = {p: list(v) for p, v in self._acc_mm.items()}
            snap = {p: self._merge(v) for p, v in parts.items()}
            snap_mm = {p: self._join_mm(v) for p, v in mm_parts.items()}
            self.ready.clear()
            self._last = time.monotonic()
            self._sig.partial.emit((snap, list(self._pvs), self._start_ns,
                                    self._end_ns, self._epoch, self.cov, snap_mm,
                                    self.ready, self.edge, None))


class _IncSig(QObject):
    done = Signal(object)

class _ChanSig(QObject):
    done  = Signal(list)
    error = Signal(str)


class _ShowWatch(QObject):
    """Calls ``on_show`` whenever the watched widget is shown (its tab picked,
    its window docked back) — for work that waits until it can be seen."""

    def __init__(self, on_show, parent=None):
        super().__init__(parent)
        self._on_show = on_show

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show:
            try:
                self._on_show()
            except Exception:
                pass
        return False


# ── Color swatch delegate ──────────────────────────────────────────────────

class _WheelGuard(QObject):
    """The mouse wheel must never change a number box or a drop-down just
    because the pointer happens to hover over it.

    Number boxes, drop-downs and sliders answer the wheel only after they were
    CLICKED (or reached with Tab), and only while the pointer is over them --
    Qt delivers a wheel notch to the control under the pointer, so a selected
    field elsewhere on the page is never touched. Moving the pointer away keeps
    the field selected (the keyboard still edits it); the wheel works on it
    again when the pointer comes back. Selecting another control ends it. An
    unselected field passes the notch to the panel behind it, so the toolbar
    or the page scrolls the way the user expects. The focus a freshly opened
    window HANDS its first field does not count. Scroll bars are left out:
    they are sliders too, and the wheel is how a pane gets scrolled.

    Spin boxes and drop-downs come with Qt's WheelFocus policy, which hands
    them the focus FROM THE WHEEL ITSELF before any event filter sees the
    notch, so every such field is switched to StrongFocus when it is polished.

    Tab bars and calendars never take the wheel at all: over a tab bar it
    switched tabs, over a calendar's day grid it flipped the month — and in
    the time-window dialog that silently moved the From/To day.
    """

    _TARGETS = (QAbstractSpinBox, QComboBox, QAbstractSlider)
    _NEVER = (QTabBar, QCalendarWidget)
    _ARMED = "wheelReady"
    _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
               Qt.FocusReason.BacktabFocusReason,
               Qt.FocusReason.ShortcutFocusReason)
    # Focus that leaves only for a moment and comes back to the same field:
    # another window was activated, or the drop-down's own list opened.
    _KEEP = (Qt.FocusReason.ActiveWindowFocusReason,
             Qt.FocusReason.PopupFocusReason)

    @classmethod
    def _is_field(cls, obj):
        return isinstance(obj, cls._TARGETS) and not isinstance(obj, QScrollBar)

    @classmethod
    def _never_target(cls, obj):
        """True for a tab bar, or any part of a calendar (its day grid is a
        view nested two levels inside the QCalendarWidget)."""
        w = obj
        for _ in range(4):
            if w is None or not isinstance(w, QWidget):
                return False
            if isinstance(w, cls._NEVER):
                return True
            w = w.parentWidget()
        return False

    @staticmethod
    def _in_calendar(w):
        for _ in range(4):
            w = w.parentWidget() if w is not None else None
            if isinstance(w, QCalendarWidget):
                return True
        return False

    @classmethod
    def _pass_to_panel(cls, obj, ev):
        w = obj.parentWidget()
        while w is not None:                # pass the scroll on to the panel
            # The calendar's own day grid is a scroll area too: skip it.
            if isinstance(w, QAbstractScrollArea) and not cls._in_calendar(w):
                QApplication.sendEvent(w.viewport(), ev)
                break
            w = w.parentWidget()

    def _target_of(self, obj):
        """The guarded field ``obj`` belongs to — itself, or the spin box /
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
        if t == QEvent.Type.Polish:
            if self._is_field(obj) and \
                    obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            return False
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            # A click is what selects the field -- also the field that already
            # HAD the focus (the first one of a window is handed it), where no
            # FocusIn follows the click. The press lands on the spin box's own
            # text line, so look one or two levels up.
            tgt = self._target_of(obj)
            if tgt is not None:
                tgt.setProperty(self._ARMED, True)
            return False
        if t == QEvent.Type.FocusIn and self._is_field(obj):
            if ev.reason() in self._EARNED:
                obj.setProperty(self._ARMED, True)
            return False
        if t == QEvent.Type.FocusOut and self._is_field(obj):
            # Pointer leaving or the field being hidden does NOT end it; only
            # selecting something else does.
            if ev.reason() not in self._KEEP:
                obj.setProperty(self._ARMED, False)
            return False
        if t != QEvent.Type.Wheel:
            return False
        if not self._is_field(obj):
            if not self._never_target(obj):
                return False
            self._pass_to_panel(obj, ev)
            return True
        if (obj.property("wheelAlways")
                or (obj.property(self._ARMED) and obj.hasFocus())):
            return False                    # clicked into it -> normal behaviour
        self._pass_to_panel(obj, ev)
        return True


def install_app_look(app):
    """The program's whole appearance: Fusion, the app stylesheet and the light
    palette, in one place.

    This is deliberately importable. The program is LIGHT — light panels, dark
    ink — and none of that comes from Windows: a test or a probe that builds a
    window without calling this gets whatever theme Windows is in, which on a
    machine set to dark mode means every colour is judged against a black
    background that the real program never shows. Screenshots taken that way
    lie, and a colour "fixed" against them comes out unreadable in the app.
    Call this first in anything that puts this program's widgets on screen."""
    app.setStyle("Fusion")
    app.setStyleSheet(_APP_STYLESHEET)
    _install_wheel_guard()
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor("#F5F5F5"))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor("#212121"))
    pal.setColor(QPalette.ColorRole.Base,            QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor("#E3F2FD"))
    pal.setColor(QPalette.ColorRole.Button,          QColor("#E3F2FD"))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor("#1565C0"))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor("#1565C0"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(pal)


def _install_wheel_guard():
    """Install _WheelGuard once for the whole application."""
    app = QApplication.instance()
    if app is None or getattr(app, "_wheel_guard", None) is not None:
        return
    guard = _WheelGuard(app)
    app._wheel_guard = guard
    app.installEventFilter(guard)


class _HeaderDropMarker(QObject):
    """Blue upright line showing where a dragged column heading will land.

    Qt moves the section on release but draws no marker on the way. This only
    watches the header's mouse events (it never takes one away from Qt) and
    works out the landing place with Qt's own rule (qheaderview.cpp, the
    MoveSection branch of mouseMoveEvent)."""

    _WIDTH = 3

    def __init__(self, table):
        super().__init__(table)
        self._tv   = table
        self._hdr  = table.horizontalHeader()
        self._line = QFrame(table)
        self._line.setStyleSheet("QFrame{background:#1565C0;border:none;}")
        self._line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._line.hide()
        self._press = None          # (x, logical section) of a press that may become a move
        self._hdr.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress:
            self._press = None
            x = int(ev.position().x())
            sec = self._hdr.logicalIndexAt(x)
            # A press on a column edge starts a resize, not a move.
            resizing = (self._hdr.viewport().cursor().shape()
                        == Qt.CursorShape.SplitHCursor)
            if (ev.button() == Qt.MouseButton.LeftButton and sec >= 0
                    and not resizing and self._hdr.sectionsMovable()):
                self._press = (x, sec)
        elif t == QEvent.Type.MouseMove:
            if self._press is not None and ev.buttons() & Qt.MouseButton.LeftButton:
                self._update(int(ev.position().x()))
        elif t in (QEvent.Type.MouseButtonRelease, QEvent.Type.Hide):
            self._press = None
            self._line.hide()
        return False

    def _update(self, x):
        x0, sec = self._press
        if abs(x - x0) < QApplication.startDragDistance():
            return
        hdr    = self._hdr
        visual = hdr.visualIndexAt(x)
        if visual < 0:
            return                   # past the last column: Qt keeps the old target too
        moving    = hdr.visualIndex(sec)
        under     = hdr.logicalIndex(visual)
        threshold = hdr.sectionViewportPosition(under) + hdr.sectionSize(under) // 2
        if visual < moving:
            target = under if x < threshold else hdr.logicalIndex(visual + 1)
        elif visual > moving:
            target = under if x > threshold else hdr.logicalIndex(visual - 1)
        else:
            target = sec
        tv = hdr.visualIndex(target)
        if target < 0 or tv == moving:
            self._line.hide()
            return
        edge = hdr.sectionViewportPosition(target)
        if tv > moving:
            edge += hdr.sectionSize(target)
        edge = max(0, min(edge, hdr.viewport().width()))
        left = hdr.x() + edge - self._WIDTH // 2
        self._line.setGeometry(left, hdr.y(), self._WIDTH,
                               self._tv.height() - hdr.y())
        self._line.raise_()
        self._line.show()


class _CenteredCheckDelegate(QStyledItemDelegate):
    """Draws a tick box in the MIDDLE of its column instead of hard against the
    left cell edge (Show / Autoscale / Grid columns of the PV list).

    Qt always lays the check indicator out on the left, and the app stylesheet
    re-positions it again, so the box is painted here by hand: that is the only
    way to know exactly where it sits — and the click area has to match it.
    """

    _SIZE = 18          # outer size of the box, in pixels

    def _box(self, rect: QRect) -> QRect:
        b = QRect(0, 0, self._SIZE, self._SIZE)
        b.moveCenter(rect.center())
        return b

    def paint(self, painter: QPainter, option, index):
        if index.data(Qt.ItemDataRole.CheckStateRole) is None:
            super().paint(painter, option, index)   # e.g. a group divider row
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style  = widget.style() if widget else QApplication.style()
        checked = opt.checkState == Qt.CheckState.Checked
        # Row background / selection only — the built-in (left-hugging)
        # indicator and the empty text are dropped.
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        box = self._box(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        edge = QColor("#1565C0") if checked else QColor("#4a4a4a")
        painter.setPen(QPen(edge, 2))
        painter.setBrush(QColor("#1565C0") if checked else QColor("#ffffff"))
        painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 3, 3)
        if checked:
            painter.setPen(QPen(QColor("#ffffff"), 2))
            c = box.center()
            painter.drawPolyline(QPolygonF([QPointF(c.x() - 3.5, c.y() + 0.5),
                                            QPointF(c.x() - 0.5, c.y() + 3.5),
                                            QPointF(c.x() + 4.0, c.y() - 3.5)]))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        flags = index.flags()
        if not (flags & Qt.ItemFlag.ItemIsUserCheckable) or not (flags & Qt.ItemFlag.ItemIsEnabled):
            return False
        etype = event.type()
        if etype in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonDblClick):
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            # Hit area = the drawn box, grown a little so it is easy to hit.
            if not self._box(option.rect).adjusted(-5, -3, 5, 3).contains(event.position().toPoint()):
                return False
            if etype != QEvent.Type.MouseButtonRelease:
                return True                      # swallow press / double click
        elif etype == QEvent.Type.KeyPress:
            if event.key() not in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                return False
        else:
            return False
        state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole) or 0)
        new   = (Qt.CheckState.Unchecked if state == Qt.CheckState.Checked
                 else Qt.CheckState.Checked)
        return model.setData(index, new, Qt.ItemDataRole.CheckStateRole)


class _ColorSwatchDelegate(QStyledItemDelegate):
    """Paints axis-settings table Color cells; opens QColorDialog on click."""

    color_changed = Signal(int, str)  # (row, hex_color)

    def paint(self, painter: QPainter, option, index):
        color_str = index.data(Qt.ItemDataRole.UserRole)
        if color_str:
            painter.save()
            painter.fillRect(option.rect.adjusted(2, 2, -2, -2), QColor(color_str))
            painter.setPen(QPen(QColor("#555"), 1))
            painter.drawRect(option.rect.adjusted(2, 2, -3, -3))
            painter.restore()
        else:
            super().paint(painter, option, index)

    def editorEvent(self, event, model, option, index):
        # Only a left click that starts AND ends on this cell opens the picker:
        # a right/middle click, or the end of a row drag dropped here, used to
        # open it too.
        etype = event.type()
        if etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            if event.button() != Qt.MouseButton.LeftButton:
                return False
        if etype == QEvent.Type.MouseButtonPress:
            self._pressed = (index.row(), index.column())
            return False
        if etype == QEvent.Type.MouseButtonRelease:
            pressed, self._pressed = getattr(self, "_pressed", None), None
            if pressed != (index.row(), index.column()):
                return False
            current = index.data(Qt.ItemDataRole.UserRole) or "#1976D2"
            view = option.widget
            parent = view.window() if isinstance(view, QWidget) else None
            color = QColorDialog.getColor(QColor(current), parent, "Choose color")
            if color.isValid():
                model.setData(index, color.name(), Qt.ItemDataRole.UserRole)
                self.color_changed.emit(index.row(), color.name())
            return True
        return False


# ── Per-signal line / point styling ──────────────────────────────────────────
# Left of the arrow is what the PV list shows, right of it what matplotlib wants.
_LINE_STYLES = {
    "solid":    "-",
    "dashed":   "--",
    "dotted":   ":",
    "dash-dot": "-.",
    "none":     "None",
}
# "auto" = the old behaviour: a small dot only when the global markers switch in
# Graph settings is on AND the trace is short enough to still be readable.
_MARKER_STYLES = {
    "auto": None,
    "none": "None",
    "●": "o", "○": "o", "▲": "^", "■": "s", "✕": "x", "+": "+",
}
_MARKER_HOLLOW = {"○"}          # same glyph as ●, drawn with a white face

# Reference lines offer the same line styles, minus "none" (an invisible
# reference line is never what anyone wants).
_REF_LINE_STYLES = {k: v for k, v in _LINE_STYLES.items() if k != "none"}
# Which side of a reference line counts as crossing it (Limit crossings).
_REF_CROSSING = ("above", "below", "off")

# PV-list columns the user cannot type into: either the PV name itself or a
# number the program measures and fills in.
_AXIS_READONLY_COLS = ("pv", "cursor_val", "unit", "last", "min", "max",
                       "mean", "count")
# Columns hidden until switched on from the right-click menu on the header.
# Everything that was visible before this feature stays visible.
_AXIS_HIDDEN_BY_DEFAULT = ("alpha", "unit", "last", "min", "max", "mean", "count")
# Columns that must always stay on screen (the name, and the spacer that soaks
# up leftover width).
_AXIS_ALWAYS_SHOWN = ("pv", "blank")


class _ComboBoxDelegate(QStyledItemDelegate):
    """Cell with a fixed list of choices (the Style and Points columns).

    The list drops open on the first click instead of needing a second one to
    unfold it — picking a line style is a single gesture that way.
    """

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self._choices = list(choices)

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.addItems(self._choices)
        QTimer.singleShot(0, cb.showPopup)
        return cb

    def setEditorData(self, editor, index):
        txt = index.data(Qt.ItemDataRole.DisplayRole) or ""
        i = editor.findText(str(txt))
        editor.setCurrentIndex(i if i >= 0 else 0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


# ── Calendar helpers (copied from sp_t.py) ────────────────────────────────

class _WeekendDelegate(QStyledItemDelegate):
    def __init__(self, cal: QCalendarWidget):
        super().__init__(cal)
        self._cal = cal
        self._selected_keys: set = set()

    def _first_cell(self) -> "tuple[int, int]":
        """Row/column of the first *day* cell. Qt drops the header row when
        NoHorizontalHeader is set and the week-number column when
        NoVerticalHeader is set, so the grid does not always start at (1, 1)."""
        first_row = 1
        if (self._cal.horizontalHeaderFormat()
                == QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader):
            first_row = 0
        first_col = 1
        if (self._cal.verticalHeaderFormat()
                == QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader):
            first_col = 0
        return first_row, first_col

    def _date_for_index(self, index):
        """Return the QDate for a model cell, or None for a header/week-number cell."""
        # The model knows the real date for in-month cells — always prefer it.
        d = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(d, QDate) and d.isValid():
            return d
        first_row, first_col = self._first_cell()
        if index.row() < first_row or index.column() < first_col:
            return None                       # header row / week-number column
        first = QDate(self._cal.yearShown(), self._cal.monthShown(), 1)
        if not first.isValid():
            return None
        # Column offset of the 1st within the first displayed week.
        offset = (first.dayOfWeek() - self._cal.firstDayOfWeek().value) % 7
        row = index.row() - first_row
        # Qt shifts the whole grid one week back when the 1st sits in the very
        # first column (QCalendarModel::dateForCell, MinimumDayOffset = 1), so
        # row 0 then shows the PREVIOUS week. Without this the painted days are
        # a week off (clicking one day highlighted a different one).
        if offset < 1:
            row -= 1
        start = first.addDays(-offset)
        return start.addDays(row * 7 + (index.column() - first_col))

    def set_selected(self, dates):
        self._selected_keys = {(d.year(), d.month(), d.day()) for d in dates}
        view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view is not None:
            view.viewport().update()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        d = self._date_for_index(index)
        if d is not None and (d.year(), d.month(), d.day()) not in self._selected_keys:
            option.state = option.state & ~QStyle.StateFlag.State_Selected

    def paint(self, painter, option, index):
        d = self._date_for_index(index)
        if d is None:
            super().paint(painter, option, index)
            return
        is_weekend = d.dayOfWeek() in (6, 7)
        is_sel = (d.year(), d.month(), d.day()) in self._selected_keys
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if is_sel:
            painter.save()
            painter.fillRect(option.rect, QColor("#1565C0"))
            painter.setPen(QColor("#ffcccc") if is_weekend else QColor("#ffffff"))
            painter.setFont(option.font)
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()
        else:
            super().paint(painter, option, index)
            if is_weekend:
                painter.save()
                painter.setPen(QColor("#cc0000"))
                painter.setFont(option.font)
                painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
                painter.restore()


def _make_calendar(initial=None, follow_page=False):
    """House-style calendar (Monday first, red weekends) plus its own nav row.

    ``follow_page`` is for a picker whose value IS the single selected day: the
    selection then moves onto whatever month is paged into view, so the
    highlighted day always agrees with what the dialog will hand back. Without
    it, paging to February and pressing OK returns the day the dialog opened on
    — which is how a months-long period came back as "the last hour". A
    multi-day picker keeps its own explicit list of days and must NOT use it.
    """
    cal = QCalendarWidget()
    cal.setGridVisible(True)
    cal.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader)
    if initial:
        cal.setSelectedDate(initial)
    cal.setStyleSheet(_CAL_STYLE)
    nav_internal = cal.findChild(QWidget, "qt_calendar_navigationbar")
    if nav_internal:
        nav_internal.hide()
    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    if view is not None:
        cal._wk_delegate = _WeekendDelegate(cal)
        view.setItemDelegate(cal._wk_delegate)

    _MONTHS = ["January","February","March","April","May","June",
               "July","August","September","October","November","December"]
    nav_row = QWidget()
    nav_row.setAutoFillBackground(True)
    nav_pal = nav_row.palette()
    nav_pal.setColor(QPalette.ColorRole.Window, QColor("#eeeeee"))
    nav_row.setPalette(nav_pal)
    nav_lay = QHBoxLayout(nav_row)
    nav_lay.setContentsMargins(4, 3, 4, 3)
    nav_lay.setSpacing(4)
    prev_btn = QToolButton(); prev_btn.setText("◀")
    prev_btn.setStyleSheet("QToolButton{border:none;font-weight:bold;font-size:18px;padding:1px 6px;}"
                           "QToolButton:hover{background:#d0d0d0;border-radius:3px;}")
    month_btn = QPushButton()
    month_btn.setMinimumWidth(100)
    month_btn.setStyleSheet("QPushButton{border:1px solid #aaa;border-radius:3px;background:#f5f5f5;"
                            "font-weight:bold;font-size:12px;padding:2px 10px;}"
                            "QPushButton:hover{background:#e0e0e0;}")
    year_spin = QSpinBox()
    year_spin.setRange(2000, 2100)
    year_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    year_spin.setStyleSheet("QSpinBox{border:1px solid #aaa;border-radius:3px;background:#f5f5f5;"
                            "padding:1px 4px;font-weight:bold;font-size:12px;}")
    year_spin.setFixedWidth(60)
    next_btn = QToolButton(); next_btn.setText("▶")
    next_btn.setStyleSheet("QToolButton{border:none;font-weight:bold;font-size:18px;padding:1px 6px;}"
                           "QToolButton:hover{background:#d0d0d0;border-radius:3px;}")
    nav_lay.addWidget(prev_btn); nav_lay.addStretch()
    nav_lay.addWidget(month_btn); nav_lay.addWidget(year_spin)
    nav_lay.addStretch(); nav_lay.addWidget(next_btn)

    def _update_nav():
        m, y = cal.monthShown(), cal.yearShown()
        month_btn.setText(_MONTHS[m - 1])
        year_spin.blockSignals(True); year_spin.setValue(y); year_spin.blockSignals(False)

    def _on_month_btn():
        menu = QMenu(month_btn)
        for i, name in enumerate(_MONTHS, 1):
            menu.addAction(name).setData(i)
        chosen = menu.exec(month_btn.mapToGlobal(month_btn.rect().bottomLeft()))
        if chosen:
            cal.setCurrentPage(cal.yearShown(), chosen.data())

    def _sync_selection_to_page():
        """Keep the selected day inside the month on screen (see follow_page)."""
        y, m = cal.yearShown(), cal.monthShown()
        cur = cal.selectedDate()
        if cur.isValid() and cur.year() == y and cur.month() == m:
            return
        day = min(cur.day() if cur.isValid() else 1, QDate(y, m, 1).daysInMonth())
        d = QDate(y, m, day)
        if not d.isValid():
            return
        cal.setSelectedDate(d)
        deleg = getattr(cal, "_wk_delegate", None)
        if deleg is not None:
            deleg.set_selected([d])

    def _on_page_changed(_y=None, _m=None):
        _update_nav()
        if follow_page:
            _sync_selection_to_page()

    prev_btn.clicked.connect(cal.showPreviousMonth)
    next_btn.clicked.connect(cal.showNextMonth)
    month_btn.clicked.connect(_on_month_btn)
    year_spin.valueChanged.connect(lambda y: cal.setCurrentPage(y, cal.monthShown()))
    cal.currentPageChanged.connect(_on_page_changed)
    _update_nav()

    hdr_row = QWidget()
    hdr_row.setAutoFillBackground(True)
    hdr_pal = hdr_row.palette()
    hdr_pal.setColor(QPalette.ColorRole.Window, QColor("#757575"))
    hdr_row.setPalette(hdr_pal)
    hdr_lay = QHBoxLayout(hdr_row)
    hdr_lay.setContentsMargins(0, 0, 0, 0); hdr_lay.setSpacing(0)
    for name in ("Mon","Tue","Wed","Thu","Fri","Sat","Sun"):
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color:#111111;font-weight:700;padding:4px 0;")
        hdr_lay.addWidget(lbl, stretch=1)

    wrapper = QFrame()
    wrapper.setStyleSheet("QFrame{border:1px solid #b0b0b0;border-radius:3px;}")
    w_lay = QVBoxLayout(wrapper)
    w_lay.setContentsMargins(0,0,0,0); w_lay.setSpacing(0)
    w_lay.addWidget(nav_row); w_lay.addWidget(hdr_row); w_lay.addWidget(cal)
    return wrapper, cal


class DatePickerDialog(QDialog):
    def __init__(self, parent=None, initial=None):
        super().__init__(parent)
        self.setWindowTitle("Select day(s)")
        self.setWindowIcon(QIcon())
        self.setModal(True)
        self.setMinimumWidth(330)
        self._last_click = None
        self._dates = []
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self._frame, self._cal = _make_calendar(initial)
        lay.addWidget(self._frame)
        self._lbl_info = QLabel("")
        self._lbl_info.setStyleSheet("color:#555;font-size:11px;")
        lay.addWidget(self._lbl_info)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        clear_btn = btns.addButton("Clear", QDialogButtonBox.ButtonRole.ResetRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        clear_btn.clicked.connect(lambda: self._apply_selection([]))
        lay.addWidget(btns)
        self._cal.clicked.connect(self._on_date_clicked)
        if initial and initial.isValid():
            self._apply_selection([initial])
            self._last_click = initial

    def _on_date_clicked(self, d):
        mods  = QApplication.keyboardModifiers()
        ctrl  = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        new_dates = self._compute_click(d, ctrl, shift)
        self._last_click = d
        if new_dates is None:
            self._lbl_info.setText("Weekends can only be selected by a plain click.")
            return
        self._apply_selection(new_dates)

    def _compute_click(self, d, ctrl, shift):
        cur = list(self._dates)
        cur_keys = {self._key(x) for x in cur}
        if ctrl and shift:
            anchor = self._last_click if self._last_click is not None else d
            rng = [x for x in self._date_range(anchor, d) if not self._is_weekend(x)]
            rng_keys = {self._key(x) for x in rng}
            keep = [x for x in cur if self._key(x) not in rng_keys]
            add  = [x for x in rng if self._key(x) not in cur_keys]
            new_dates = keep + add
        elif ctrl:
            if self._is_weekend(d):
                return None
            if self._key(d) in cur_keys:
                new_dates = [x for x in cur if self._key(x) != self._key(d)]
            else:
                new_dates = cur + [d]
        else:
            new_dates = [d]
        return sorted(new_dates, key=lambda x: (x.year(), x.month(), x.day()))

    @staticmethod
    def _key(d): return (d.year(), d.month(), d.day())
    @staticmethod
    def _is_weekend(d): return d.dayOfWeek() >= 6
    @staticmethod
    def _date_range(d1, d2):
        if d2 < d1: d1, d2 = d2, d1
        days, d = [], d1
        while d <= d2:
            days.append(d); d = d.addDays(1)
        return days

    def _apply_selection(self, dates):
        self._dates = dates
        delegate = getattr(self._cal, "_wk_delegate", None)
        if delegate is not None:
            delegate.set_selected(dates)
        if dates:
            self._cal.setSelectedDate(dates[-1])
        n = len(dates)
        if n == 0:
            self._lbl_info.setText("Click = 1 day · Ctrl+click = multi · Ctrl+Shift+click = range")
        elif n == 1:
            self._lbl_info.setText(f"Selected: {dates[0].toString('yyyy-MM-dd')}")
        else:
            self._lbl_info.setText(
                f"Selected: {dates[0].toString('yyyy-MM-dd')} → "
                f"{dates[-1].toString('yyyy-MM-dd')}  ({n} days)")

    def selected_date(self):
        return self._dates[0] if self._dates else self._cal.selectedDate()

    def selected_dates(self):
        return list(self._dates) if self._dates else [self._cal.selectedDate()]


# ── Time window dialog ─────────────────────────────────────────────────────

# Max span (seconds) accepted for a live rolling window. Longer windows than
# this fall back to 1 hour. 366 days keeps year-long relative windows working.
_LIVE_MAX_SPAN_S = 86400 * 366

# A *live* window is re-merged, re-filtered and re-drawn continuously, so a wide
# one costs on every refresh. It used to be clamped to 12 h, which meant Live
# quietly showed something other than the period on screen. It is no longer
# clamped — Live now only ever starts for a period that ends at "now", and above
# this span the cost is stated in the Log instead. See _live_span_from_window.
_LIVE_SLOW_SPAN_S = 24 * 3600
# A setting tab that is not on screen still follows "now" in Live, but asks the
# archiver only this often (the user's choice, 12.10.2026: slow in the
# background, caught up at once when the tab is shown). Well inside
# _LIVE_TICK_LOOKBACK_NS, so a hidden tick never misses a stretch.
HIDDEN_LIVE_POLL_MS = 10_000

# How far back a single live tick may ask for data. _live_last_ts only advances
# when new samples actually arrive, so on a quiet archiver it stays pinned to the
# last real sample and the range [last_sample → now] grows without bound — every
# 300 ms tick then re-downloaded the whole live window again, split into 1-hour
# chunks per PV (a 49 h window × 8 PVs ≈ 250 HTTPS requests per tick, forever,
# in 16 threads fighting the GUI for the GIL). Capping the look-back at one
# chunk keeps a tick at exactly one request per PV whatever the window is.
_LIVE_TICK_LOOKBACK_NS = int(120e9)          # 2 min ≤ CHUNK_SIZE_NS (1 h)

# When a tick brings nothing, the cursor is still moved to (now − this), so the
# next tick asks for a small slice instead of re-asking for everything since the
# last sample. The overlap is what covers archiver ingestion lag.
_LIVE_TICK_OVERLAP_NS = int(30e9)
# Every tick also re-asks this much from before the newest sample of any PV, so
# a slower channel's sample that is archived a moment late is still picked up.
# Duplicates cannot result: each PV keeps only samples newer than its own last.
_LIVE_LATE_ARRIVAL_NS = int(5e9)
# Once a minute a tick reaches this far back and takes in every sample it does
# not have yet — one the archiver stored later than the few seconds above
# (slow ingestion, a channel catching up) would otherwise be missing from the
# graph and the selection statistics for good.
_LIVE_BACKFILL_EVERY_NS = int(60e9)
_LIVE_BACKFILL_NS = int(600e9)

# Rebuilding the whole QTableWidget on every live tick gets visibly laggy once
# the row count grows large, so only the most recent rows are ever rendered.
# Export/graph/XY-plot always use the full, untruncated _table_rows.
_MAX_TABLE_ROWS = 5000

# Table cell colours per alarm severity; anything else keeps the default palette
# colour. Kept as strings, not QColor: a QColor living in a module global is
# destroyed after the QApplication is gone, which crashes the interpreter on exit
# (0xC0000005). The QColor is built only when a cell's severity actually changes.
_TABLE_SEVERITY_FG = {
    "MINOR":   "#FFA000",
    "MAJOR":   "#C62828",
    "INVALID": "#C62828",
}

# The live tick polls every 300 ms (see _schedule_live_tick), but re-merging +
# re-filtering + re-plotting the WHOLE accumulated history (which only grows
# over a live session) is too expensive to redo every single tick — on a long
# session it starts taking longer than the tick period itself, so the GUI
# thread never gets an idle moment and the app appears frozen. New samples are
# still appended every tick; the expensive rebuild is throttled to this cadence.
_LIVE_REBUILD_MIN_INTERVAL_NS = int(1.0 * 1e9)

# ── ns timestamps → matplotlib date numbers ────────────────────────────────
# Building one datetime per sample (and letting matplotlib convert it) used to
# dominate every replot and every live refresh: 15 PVs × 2000 points meant 30k
# datetime objects a second. A matplotlib date number is just "days since the
# epoch", so the whole conversion is one numpy division.
_NS_PER_DAY = 86_400_000_000_000.0
_MPL_EPOCH_NUM: float | None = None


def _mpl_epoch_num() -> float:
    """Date number of 1970-01-01 UTC for matplotlib's configured epoch."""
    global _MPL_EPOCH_NUM
    if _MPL_EPOCH_NUM is None:
        _MPL_EPOCH_NUM = float(mdates.date2num(datetime(1970, 1, 1, tzinfo=timezone.utc)))
    return _MPL_EPOCH_NUM


def _pairs_to_ns_arrays(pairs):
    """(ts_ns, value) pairs → (ns timestamps, values) float64 arrays.
    None values become NaN so a gap stays a gap."""
    if not pairs:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    ts   = np.asarray([p[0] for p in pairs], dtype=np.float64)
    vals = np.asarray([(p[1] if isinstance(p[1], (int, float)) else float("nan"))
                       for p in pairs], dtype=np.float64)
    return ts, vals


class _SampleArrays:
    """A channel's numeric samples held as two arrays (ns times, values)
    instead of a list of tuples: a raw read of a long selection is millions of
    samples, and a tuple costs ten times the memory of two array cells. Reads
    like a sample list wherever the statistics only ask for its arrays, its
    length or its last sample."""
    __slots__ = ("ts", "v")

    def __init__(self, ts, v):
        self.ts = np.asarray(ts, dtype=np.float64)
        self.v = np.asarray(v, dtype=np.float64)
        self.ts.setflags(write=False)
        self.v.setflags(write=False)

    def __len__(self):
        return int(self.ts.size)

    def __getitem__(self, i):
        return (int(self.ts[i]), float(self.v[i]), "")


def _samples_to_ns_arrays(samples):
    """(ts_ns, value, units) triples → (ns timestamps, values) float64 arrays.

    Fast path: np.fromiter reads the two columns in a single pass with no
    intermediate Python lists — this runs over every sample of every PV on every
    live refresh. A non-numeric value makes numpy raise, and only then is the
    filtering slow path used.

    Remembered per list (the same list, same length, same first and last
    sample): the end of a load converted every signal five times over —
    measured 12.10.2026, 2 s of a day's 770 000 samples. The arrays handed out
    are read-only, since every caller shares them."""
    if isinstance(samples, _SampleArrays):
        return samples.ts, samples.v
    n = len(samples)
    if not n:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    key = id(samples)
    hit = _NS_ARRAYS_MEMO.get(key)
    if (hit is not None and hit[0] is samples and hit[1] == n
            and hit[2] == samples[0][0] and hit[3] == samples[-1][0]):
        return hit[4], hit[5]
    ts = vals = None
    # Live grows the same list at the end and trims it at the start on every
    # tick, so the memo above always missed and every refresh re-read every
    # sample of every PV. When the remembered part is still there, in place,
    # only the new end is read and the two are joined.
    if (hit is not None and hit[0] is samples and hit[6]
            and hit[4].size == hit[1]):
        old_ts, old_v = hit[4], hit[5]
        k = int(np.searchsorted(old_ts, float(samples[0][0]), side="left"))
        kept = hit[1] - k                    # old samples still in the list
        if (0 < kept <= n and old_ts[k] == samples[0][0]
                and samples[kept - 1][0] == hit[3]):
            tail = samples[kept:]
            try:
                t_new = np.fromiter((s[0] for s in tail), dtype=np.float64,
                                    count=len(tail))
                v_new = np.fromiter((s[1] for s in tail), dtype=np.float64,
                                    count=len(tail))
                ts = np.concatenate((old_ts[k:], t_new))
                vals = np.concatenate((old_v[k:], v_new))
            except (TypeError, ValueError):
                ts = vals = None
    exact = True                    # one array entry per sample, in order
    if ts is None:
        try:
            ts   = np.fromiter((s[0] for s in samples), dtype=np.float64, count=n)
            vals = np.fromiter((s[1] for s in samples), dtype=np.float64, count=n)
        except (TypeError, ValueError):
            exact = False
            ts, vals = _pairs_to_ns_arrays([(t, v) for t, v, _ in samples
                                            if isinstance(v, (int, float))])
    ts.setflags(write=False)
    vals.setflags(write=False)
    # The list itself is held, so its id cannot be reused by another one —
    # and a list nobody else holds any more (the previous load's) is let go,
    # or every old load would stay in memory.
    for k in [k for k, e in _NS_ARRAYS_MEMO.items() if sys.getrefcount(e[0]) <= 2]:
        _NS_ARRAYS_MEMO.pop(k, None)
    if len(_NS_ARRAYS_MEMO) >= 64:
        _NS_ARRAYS_MEMO.clear()
    _NS_ARRAYS_MEMO[key] = (samples, n, samples[0][0], samples[-1][0], ts, vals,
                            exact)
    return ts, vals


_NS_ARRAYS_MEMO: dict = {}


def _ns_to_num(ts_arr):
    """ns timestamps → matplotlib date numbers."""
    return _mpl_epoch_num() + ts_arr / _NS_PER_DAY


def _downsample_arrays_mean(ts, vals, target: int):
    """Bin (ns timestamps, values) arrays into ~`target` equal-time bins and
    average — keeps the trace shape while cutting the point count. Empty bins are
    dropped, so the result can be shorter than `target`; arrays already at or
    under the target are returned untouched."""
    n = ts.size
    if target <= 0 or n <= target:
        return ts, vals
    t0   = ts[0]
    rel  = ts - t0                      # keeps the sums small → full precision
    span = rel[-1]
    if not (span > 0):
        return ts, vals
    idx  = np.minimum((rel / (span / target)).astype(np.int64), target - 1)
    cnt  = np.bincount(idx, minlength=target)
    keep = cnt > 0
    c    = cnt[keep].astype(np.float64)
    sum_t = np.bincount(idx, weights=rel,  minlength=target)[keep]
    sum_v = np.bincount(idx, weights=vals, minlength=target)[keep]
    return t0 + sum_t / c, sum_v / c


# ── Min/max envelope ───────────────────────────────────────────────────────
# A thinned point from the archiver carries the mean of its interval AND the
# lowest and highest value really archived in it (measured 2026-09-30 on PCM4:
# raw day max 38.8465, the means peak at 38.8229, the maxima at 38.8465 exactly).
# Drawing the mean alone hides every spike, so the graph draws the band from
# minimum to maximum under the line, the way CS Studio's "Area" trace does.

def _num1(x):
    """A one-element list (how the archiver wraps scalars) → the element."""
    if isinstance(x, list) and len(x) == 1:
        x = x[0]
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _minmax_arrays(raw_samples):
    """Archiver sample dicts → (ts, lo, hi) float64 arrays, sorted by time.

    Every numeric sample is kept, so a lookup always lands on the channel's own
    sample: a thinned one brings its minimum/maximum, a raw one its value twice.
    None when nothing in the list carries a real spread (a raw-only channel),
    so a plain channel costs no memory.
    """
    ts, lo, hi = [], [], []
    spread = False
    for s in raw_samples:
        t = s.get("time")
        v = _num1(s.get("value"))
        if t is None or v is None:
            continue
        mn, mx = _num1(s.get("minimum")), _num1(s.get("maximum"))
        if mn is None or mx is None:
            mn = mx = v
        elif mn != mx:
            spread = True
        ts.append(t); lo.append(min(mn, v)); hi.append(max(mx, v))
    if not spread:
        return None
    ts_a = np.asarray(ts, dtype=np.float64)
    order = np.argsort(ts_a, kind="stable")
    return (ts_a[order], np.asarray(lo, dtype=np.float64)[order],
            np.asarray(hi, dtype=np.float64)[order])


def _envelope_for(ts, vals, mm):
    """Low/high edge for each (ts, value) of a trace, from the channel's own
    samples (`mm` from _minmax_arrays). A held value (a row where another
    channel moved) gets the envelope of the sample it is held from. No `mm`,
    or a point before the first sample: the edges are the value itself."""
    lo = vals.copy(); hi = vals.copy()
    if mm is None or not ts.size:
        return lo, hi
    m_ts, m_lo, m_hi = mm
    idx = np.searchsorted(m_ts, ts, side="right") - 1
    ok = idx >= 0
    if ok.any():
        j = idx[ok]
        lo[ok] = np.fmin(m_lo[j], vals[ok])
        hi[ok] = np.fmax(m_hi[j], vals[ok])
    return lo, hi


def _downsample_arrays_envelope(ts, vals, lo, hi, target: int):
    """Like _downsample_arrays_mean, but each bin also keeps the LOWEST low
    edge and the HIGHEST high edge that fell into it — so thinning for the
    screen never loses a spike. Arrays at or under the target are untouched."""
    n = ts.size
    if target <= 0 or n <= target:
        return ts, vals, lo, hi
    t0   = ts[0]
    rel  = ts - t0
    span = rel[-1]
    if not (span > 0):
        return ts, vals, lo, hi
    idx  = np.minimum((rel / (span / target)).astype(np.int64), target - 1)
    cnt  = np.bincount(idx, minlength=target)
    keep = cnt > 0
    c    = cnt[keep].astype(np.float64)
    sum_t = np.bincount(idx, weights=rel,  minlength=target)[keep]
    sum_v = np.bincount(idx, weights=vals, minlength=target)[keep]
    # ts is sorted, so each bin is one contiguous run: reduceat over its starts.
    starts = np.flatnonzero(np.diff(idx, prepend=-1))
    b_lo = np.fmin.reduceat(lo, starts)
    b_hi = np.fmax.reduceat(hi, starts)
    return t0 + sum_t / c, sum_v / c, b_lo, b_hi


def _has_spread(lo, hi) -> bool:
    """True when any point's band is wider than a line."""
    with np.errstate(invalid="ignore"):
        return bool(lo.size) and bool(np.any(hi > lo))


# ── Limit crossings ────────────────────────────────────────────────────────
# Found in two steps. The thinned points say WHERE a crossing can be (an
# interval whose maximum is over the line — exact, since the maximum is the
# archived one); only those intervals are then read raw, which gives the exact
# time and value of every shot over the line at a small cost.

_CROSS_RAW_MAX_RANGES = 400             # more candidate stretches than this ...
_CROSS_RAW_MAX_NS     = 7 * 86400 * 10**9   # ... or more time: interval-level only


def _crosses(vals, y, side):
    with np.errstate(invalid="ignore"):
        return vals > y if side == "above" else vals < y


def _crossing_candidates(ts, lo, hi, y, side, end_ns):
    """Stretches [a, b) of a sample-held series in which the line may be
    crossed: a sample's interval runs until the next sample (the last one to
    `end_ns`). Touching stretches are merged."""
    if not ts.size:
        return []
    edge = hi if side == "above" else lo
    hit = np.flatnonzero(_crosses(edge, y, side))
    out = []
    for i in hit:
        a = int(ts[i])
        b = int(ts[i + 1]) if i + 1 < ts.size else int(end_ns)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, max(a + 1, b)))
    return out


def _crossing_episodes(ts, vals, y, side, end_ns, exact=True, lo=None, hi=None):
    """Runs of consecutive samples on the wrong side of the line.

    Each episode: start (first sample over), end (first sample back, or None
    if still over at `end_ns`), peak value and its time, sample count, and
    whether it is exact (raw samples) or only to the interval (thinned).
    """
    if not ts.size:
        return []
    edge = vals
    if not exact:
        edge = hi if side == "above" else lo
    over = _crosses(edge, y, side)
    eps = []
    i, n = 0, ts.size
    while i < n:
        if not over[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and over[j + 1]:
            j += 1
        seg = edge[i:j + 1]
        k = int(np.nanargmax(seg) if side == "above" else np.nanargmin(seg))
        eps.append({
            "start": int(ts[i]),
            "end":   int(ts[j + 1]) if j + 1 < n else None,
            "peak":  float(seg[k]),
            "peak_at": int(ts[i + k]),
            "samples": j - i + 1,
            "exact": exact,
        })
        i = j + 1
    if eps and eps[-1]["end"] is None and end_ns is not None:
        eps[-1]["open_until"] = int(end_ns)
    return eps


def _moving_avg_np(vals, w: int):
    """Trailing moving average of a float array, same length as the input.
    NaNs only affect the windows they fall into (a cumsum-based version would
    smear a single NaN across the whole rest of the trace)."""
    if w <= 1 or vals.size < w:
        return vals
    ext = np.concatenate((vals[:w - 1][::-1], vals))
    return np.convolve(ext, np.full(w, 1.0 / w), mode="valid")


_TW_BTN_STYLE = (
    "QPushButton{background:#f5f5f5;color:#111111;border:1px solid #aaaaaa;"
    "border-radius:3px;padding:3px 8px;}"
    "QPushButton:hover{background:#e3e3e3;}"
    "QPushButton:checked{background:#1565C0;color:#ffffff;border-color:#0D47A1;}"
    "QPushButton:disabled{color:#9e9e9e;}")
_TW_TIME_STYLE = (
    "QTimeEdit{background:#ffffff;color:#111111;border:1px solid #aaaaaa;"
    "border-radius:3px;padding:2px 4px;}"
    "QTimeEdit:disabled{color:#9e9e9e;background:#f2f2f2;}")


_TW_TABS_STYLE = (
    "QTabWidget::pane{border:1px solid #9e9e9e;background:#f7f7f7;top:-1px;}"
    "QTabBar::tab{background:#e3e3e3;color:#111111;border:1px solid #9e9e9e;"
    "border-bottom:none;padding:4px 12px;margin-right:2px;"
    "border-top-left-radius:3px;border-top-right-radius:3px;}"
    "QTabBar::tab:selected{background:#f7f7f7;color:#111111;font-weight:600;}"
    "QTabBar::tab:!selected{margin-top:2px;}"
    "QTabBar::tab:hover{background:#ececec;}")
_TW_SPIN_STYLE = (
    "QSpinBox{background:#ffffff;color:#111111;border:1px solid #aaaaaa;"
    "border-radius:3px;padding:2px 4px;}")
_TW_FIELD_STYLE = (
    "QLineEdit{background:#ffffff;color:#111111;border:1px solid #aaaaaa;"
    "border-radius:3px;padding:2px 4px;}")


class TimeWindowDialog(QDialog):
    """CS-Studio-style Start/End time picker, in the Image Tools look.

    Two panes side by side, Start and End, each with two tabs like CS Studio:
    "Absolute" (calendar + clock) and "Relative" (a span before/after now in
    years … seconds, plus quick spans). Both tabs of a pane take the same room
    and the window has one fixed size, so switching a tab never makes the
    window jump. While a pane is relative, its calendar and clock follow the
    moment the span gives, so switching to Absolute starts from there. The
    bottom line mirrors CS Studio ("Start Time: - 1 hours" / "End Time: now").
    """

    _PRESETS = [("30 min", 0, 0, 30), ("1 h", 0, 1, 0), ("2 h", 0, 2, 0),
                ("6 h", 0, 6, 0), ("12 h", 0, 12, 0), ("1 day", 1, 0, 0),
                ("3 days", 3, 0, 0), ("7 days", 7, 0, 0)]
    # The span boxes, laid out as CS Studio does: two columns of three.
    _UNIT_ROWS = (("Years", "Hours"), ("Months", "Minutes"), ("Days", "Secs"))
    # Quick times under each calendar: midnight and the usual shift moments.
    _ABS_QUICK = ("Now", "00:00", "07:00", "13:00", "17:00")

    def __init__(self, parent, dt_from: datetime, dt_to: datetime):
        super().__init__(parent)
        self.setWindowTitle("Start/End Time")
        self.setModal(True)
        self._result_from = dt_from
        self._result_to   = dt_to
        self._sides: dict = {}
        self._syncing = False
        self._live_requested = False
        self._build_ui(dt_from, dt_to)
        self._refresh_status()
        # One size for every tab and every text in the status fields.
        self.setFixedSize(self.sizeHint())

    # ── construction ──────────────────────────────────────────────────────
    def _build_ui(self, dt_from, dt_to):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(8)

        row = QHBoxLayout(); row.setSpacing(10); lay.addLayout(row)

        # Default relative span: Start = window length before now, End = now.
        # Decompose into days/hours/minutes/seconds so the user's units survive a
        # round-trip (17 days must reopen as 17 days, not 408 hours).
        total_s = max(1, int(round((dt_to - dt_from).total_seconds())))
        rel_from = {
            "Days":    total_s // 86400,
            "Hours":  (total_s % 86400) // 3600,
            "Minutes": (total_s % 3600) // 60,
            "Secs":    total_s % 60,
        }

        row.addWidget(self._build_side("from", dt_from, rel_from, is_start=True))
        row.addWidget(self._build_side("to",   dt_to,   {},       is_start=False))

        # What the two panes say, in words — read-only fields of a fixed
        # width, so a longer text never widens the window.
        st_row = QHBoxLayout(); st_row.setSpacing(6)
        self._lbl_start_status = QLineEdit()
        self._lbl_end_status   = QLineEdit()
        for caption, fld in (("Start Time:", self._lbl_start_status),
                             ("End Time:", self._lbl_end_status)):
            cap = QLabel(caption)
            cap.setStyleSheet("color:#111111;font-weight:600;")
            fld.setReadOnly(True)
            fld.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            fld.setStyleSheet(_TW_FIELD_STYLE)
            fld.setSizePolicy(QSizePolicy.Policy.Ignored,
                              QSizePolicy.Policy.Fixed)
            st_row.addWidget(cap)
            st_row.addWidget(fld, stretch=1)
        lay.addLayout(st_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        for b in btns.buttons():
            b.setStyleSheet(_TW_BTN_STYLE)
            b.setMinimumWidth(80)
        lay.addWidget(btns)

        # Default to the relative mode (matches CS Studio: "-1 hours" / "now").
        # Done last so the status labels already exist when currentChanged fires.
        self._sides["from"]["tabs"].setCurrentIndex(1)
        self._sides["to"]["tabs"].setCurrentIndex(1)
        self._apply_modes()

    def _build_side(self, which, init_dt, rel_values, is_start):
        word = "Start" if is_start else "End"
        tabs = QTabWidget()
        tabs.setStyleSheet(_TW_TABS_STYLE)

        # ── Absolute tab: calendar, clock, quick times ──
        abs_page = QWidget()
        v = QVBoxLayout(abs_page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        q_init = QDate(init_dt.year, init_dt.month, init_dt.day)
        # follow_page: this side's value is the one selected day, so paging the
        # calendar must move it — see _make_calendar.
        frame, cal = _make_calendar(q_init, follow_page=True)
        cal.setMinimumSize(260, 160)
        # Make clicks register visually (highlight) — the weekend delegate paints
        # selection from its own list, so we must feed it on every click.
        def _on_click(d, c=cal):
            deleg = getattr(c, "_wk_delegate", None)
            if deleg is not None: deleg.set_selected([d])
            c.setSelectedDate(d)
            self._refresh_status()
        cal.clicked.connect(_on_click)
        # Also fires when paging moves the selection, so the status line never
        # disagrees with the highlighted day.
        cal.selectionChanged.connect(self._refresh_status)
        deleg = getattr(cal, "_wk_delegate", None)
        if deleg is not None: deleg.set_selected([q_init])
        v.addWidget(frame, stretch=1)

        t_row = QHBoxLayout(); t_row.setSpacing(6)
        t_row.addStretch()
        t_lbl = QLabel("Time:"); t_lbl.setStyleSheet("color:#111111;")
        t_row.addWidget(t_lbl)
        tedit = QTimeEdit(QTime(init_dt.hour, init_dt.minute, init_dt.second))
        tedit.setDisplayFormat("HH:mm:ss")
        tedit.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
        tedit.setStyleSheet(_TW_TIME_STYLE)
        tedit.setFixedWidth(74)
        tedit.timeChanged.connect(self._refresh_status)
        t_row.addWidget(tedit)
        t_row.addStretch()
        v.addLayout(t_row)

        q_row = QHBoxLayout(); q_row.setSpacing(4)
        q_row.addStretch()
        abs_btns = []
        for lbl in self._ABS_QUICK:
            b = QPushButton(lbl); b.setStyleSheet(_TW_BTN_STYLE)
            b.clicked.connect(self._mk_abs_quick(which, lbl))
            q_row.addWidget(b)
            abs_btns.append(b)
        if not is_start:
            # End follows the clock and the graph goes live on OK; Start gets a
            # default of one hour back, which the user may change afterwards.
            live_b = QPushButton("Live"); live_b.setStyleSheet(_TW_BTN_STYLE)
            live_b.setToolTip("End = now, following the clock; Start = 1 hour "
                              "before now (can be changed). OK starts Live mode.")
            live_b.clicked.connect(self._on_live)
            q_row.addWidget(live_b)
            abs_btns.append(live_b)
        # Never squeezed below their own size: six buttons on End did not fit
        # the pane and the first one lost its left edge. Now the pane (and the
        # fixed-size window) grows to fit them instead.
        for b in abs_btns:
            b.setMinimumWidth(b.sizeHint().width())
        q_row.addStretch()
        v.addLayout(q_row)

        # ── Relative tab: the span, quick spans, before/after, now ──
        rel_page = QWidget()
        rv = QVBoxLayout(rel_page)
        rv.setContentsMargins(10, 10, 10, 8)
        rv.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6); grid.setVerticalSpacing(6)
        grid.setColumnMinimumWidth(2, 14)
        rel_spins = {}
        for r, pair in enumerate(self._UNIT_ROWS):
            for k, name in enumerate(pair):
                lbl = QLabel(f"{name}:"); lbl.setStyleSheet("color:#111111;")
                sp = QSpinBox(); sp.setRange(0, 9999)
                sp.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                sp.setAlignment(Qt.AlignmentFlag.AlignRight)
                sp.setFixedWidth(64)
                sp.setStyleSheet(_TW_SPIN_STYLE)
                sp.valueChanged.connect(self._refresh_status)
                grid.addWidget(lbl, r, 3 * k)
                grid.addWidget(sp, r, 3 * k + 1)
                rel_spins[name] = sp
        grid.setColumnStretch(5, 1)
        rv.addLayout(grid)
        for name, val in (rel_values or {}).items():
            if name in rel_spins:
                rel_spins[name].setValue(val)

        # Quick spans. On Start they mean "the last N, up to now" (End is put
        # on now as well); on End they set End's own span.
        qg = QGridLayout(); qg.setSpacing(4)
        for i, (text, d, h, m) in enumerate(self._PRESETS):
            b = QPushButton(text); b.setStyleSheet(_TW_BTN_STYLE)
            if is_start:
                b.setToolTip(f"From {text} ago up to now (follows the clock)")
                b.clicked.connect(self._mk_preset(d, h, m))
            else:
                b.setToolTip(f"End {text} before now")
                b.clicked.connect(self._mk_rel_span(which, d, h, m))
            qg.addWidget(b, i // 4, i % 4)
        rv.addLayout(qg)

        b_row = QHBoxLayout(); b_row.setSpacing(6)
        before = QCheckBox("Before now")
        before.setToolTip("Ticked: the span is counted back from now; "
                          "unticked: forward from now")
        before.setChecked(True)
        before.setStyleSheet(_CHK_STYLE)
        before.toggled.connect(self._refresh_status)
        b_row.addWidget(before)
        b_row.addStretch()
        now_b = QPushButton("Now"); now_b.setStyleSheet(_TW_BTN_STYLE)
        now_b.setToolTip(f"Zero span: {word} is \"now\" and follows the clock")
        now_b.clicked.connect(self._mk_rel_now(which))
        b_row.addWidget(now_b)
        rv.addLayout(b_row)
        rv.addStretch(1)

        tabs.addTab(abs_page, f"Absolute {word}")
        tabs.addTab(rel_page, f"Relative {word}")
        tabs.currentChanged.connect(lambda _i: self._refresh_status())

        self._sides[which] = {
            "tabs": tabs, "cal": cal, "frame": frame, "time": tedit,
            "abs_btns": abs_btns, "rel": rel_spins, "before": before,
            "rel_widgets": list(rel_spins.values()) + [before, now_b],
        }
        return tabs

    def _apply_modes(self):
        """Each mode has its own tab now, so nothing needs greying out or
        hiding — kept so the quick buttons can still call it."""
        return

    # ── quick-button factories ────────────────────────────────────────────
    def _mk_preset(self, days, hours, minutes):
        def _cb():
            for which in ("from", "to"):
                sd = self._sides[which]
                for sp in sd["rel"].values():
                    sp.setValue(0)
                sd["before"].setChecked(True)
                sd["tabs"].setCurrentIndex(1)
            r = self._sides["from"]["rel"]
            r["Days"].setValue(days); r["Hours"].setValue(hours)
            r["Minutes"].setValue(minutes)
            self._apply_modes()
            self._refresh_status()
        return _cb

    def _mk_abs_quick(self, which, kind):
        def _cb():
            sd = self._sides[which]
            if kind == "Now":
                t = datetime.now()
                sd["cal"].setSelectedDate(QDate(t.year, t.month, t.day))
                deleg = getattr(sd["cal"], "_wk_delegate", None)
                if deleg is not None: deleg.set_selected([QDate(t.year, t.month, t.day)])
                sd["time"].setTime(QTime(t.hour, t.minute, t.second))
            else:                                   # "HH:MM" on the chosen day
                hh, mm = (int(x) for x in kind.split(":"))
                sd["time"].setTime(QTime(hh, mm, 0))
            self._refresh_status()
        return _cb

    def _mk_rel_quick(self, which, hours):
        def _cb():
            sd = self._sides[which]
            for k in ("Years", "Months", "Days", "Hours", "Minutes", "Secs"):
                sd["rel"][k].setValue(0)
            sd["rel"]["Days"].setValue(hours // 24)
            sd["rel"]["Hours"].setValue(hours % 24)
            sd["before"].setChecked(True)
            self._refresh_status()
        return _cb

    def _mk_rel_span(self, which, days, hours, minutes):
        def _cb():
            sd = self._sides[which]
            for sp in sd["rel"].values():
                sp.setValue(0)
            sd["rel"]["Days"].setValue(days)
            sd["rel"]["Hours"].setValue(hours)
            sd["rel"]["Minutes"].setValue(minutes)
            sd["before"].setChecked(True)
            self._refresh_status()
        return _cb

    def _mk_rel_now(self, which):
        def _cb():
            sd = self._sides[which]
            for sp in sd["rel"].values(): sp.setValue(0)
            self._refresh_status()
        return _cb

    def _on_live(self):
        """The "Live" button on Absolute End: End becomes "now" (its Relative
        tab, every box zero) and Start "- 1 hours". Start stays editable; OK
        then starts Live mode as long as End is still "now"."""
        self._live_requested = True
        for which, hours in (("to", 0), ("from", 1)):
            sd = self._sides[which]
            for sp in sd["rel"].values():
                sp.setValue(0)
            sd["rel"]["Hours"].setValue(hours)
            sd["before"].setChecked(True)
            sd["tabs"].setCurrentIndex(1)
        self._refresh_status()

    def wants_live(self) -> bool:
        """Was "Live" pressed, and does the period still end at "now"?"""
        return self._live_requested and self.ends_at_now()

    # ── computation ───────────────────────────────────────────────────────
    def _compute(self, which):
        sd = self._sides[which]
        if sd["tabs"].currentIndex() == 0:          # Absolute
            d = sd["cal"].selectedDate()
            t = sd["time"].time()
            return datetime(d.year(), d.month(), d.day(),
                            t.hour(), t.minute(), t.second())
        # Relative — approximate years/months as 365/30 days
        r = sd["rel"]
        delta = timedelta(
            days=r["Days"].value() + r["Months"].value() * 30 + r["Years"].value() * 365,
            hours=r["Hours"].value(), minutes=r["Minutes"].value(), seconds=r["Secs"].value())
        now = datetime.now()
        return (now - delta) if sd["before"].isChecked() else (now + delta)

    def _rel_text(self, which):
        sd = self._sides[which]
        r = sd["rel"]
        parts = []
        for name in ("Years", "Months", "Days", "Hours", "Minutes", "Secs"):
            v = r[name].value()
            if v: parts.append(f"{v} {name.lower()}")
        if not parts:
            return "now"
        sign = "-" if sd["before"].isChecked() else "+"
        return sign + " " + " ".join(parts)

    def _refresh_status(self):
        if len(self._sides) < 2 or self._syncing:   # guards construction
            return
        f = self._compute("from"); t = self._compute("to")
        # A relative side shows on its (greyed) calendar and clock the moment
        # its span gives, so both calendars always say where the period lies.
        self._syncing = True
        try:
            for which, dt in (("from", f), ("to", t)):
                sd = self._sides[which]
                if sd["tabs"].currentIndex() != 1:
                    continue
                qd = QDate(dt.year, dt.month, dt.day)
                sd["cal"].setSelectedDate(qd)
                sd["cal"].setCurrentPage(dt.year, dt.month)
                deleg = getattr(sd["cal"], "_wk_delegate", None)
                if deleg is not None:
                    deleg.set_selected([qd])
                sd["time"].setTime(QTime(dt.hour, dt.minute, dt.second))
        finally:
            self._syncing = False
        f_txt = (self._rel_text("from") if self._sides["from"]["tabs"].currentIndex() == 1
                 else f.strftime("%Y-%m-%d %H:%M:%S"))
        t_txt = (self._rel_text("to") if self._sides["to"]["tabs"].currentIndex() == 1
                 else t.strftime("%Y-%m-%d %H:%M:%S"))
        self._lbl_start_status.setText(f_txt)
        self._lbl_end_status.setText(t_txt)
        self._lbl_start_status.setCursorPosition(0)
        self._lbl_end_status.setCursorPosition(0)

    def ends_at_now(self) -> bool:
        """Does the chosen period end at "now" — i.e. should it follow the clock?

        True exactly when the End side is on its Relative tab with every box at
        zero, which is the state the "Now" button there leaves behind and what
        the status line already shows as "End Time: now".

        The caller needs this because OK hands back two frozen timestamps, and
        "end = this instant, keep up with it" and "end = this instant, stand
        still" are the same pair of numbers. Same idea as
        daypicker.DayTimePicker.is_online_mode().
        """
        sd = self._sides.get("to")
        if not sd or sd["tabs"].currentIndex() != 1:
            return False                       # an absolute end is a fixed end
        return all(sp.value() == 0 for sp in sd["rel"].values())

    def _on_ok(self):
        try:
            dt_from = self._compute("from")
            dt_to   = self._compute("to")
        except Exception as e:
            QMessageBox.critical(self, "Invalid time", str(e)); return
        if dt_to <= dt_from:
            QMessageBox.critical(self, "Invalid range",
                                 "END TIME must be later than START TIME."); return
        self._result_from = dt_from
        self._result_to   = dt_to
        self.accept()


# ── PV Browser dialog ──────────────────────────────────────────────────────

_pv_channel_cache: list = []


class PVBrowserDialog(QDialog):
    def __init__(self, parent, timeout=10.0):
        super().__init__(parent)
        self.setWindowTitle("Browse CPVA channels")
        self.setMinimumSize(500, 420)
        self._timeout = timeout
        self._all_channels: list = list(_pv_channel_cache)
        self.selected_pvs: list = []
        self._build_ui()
        if self._all_channels:
            self._lbl_status.setText(f"{len(self._all_channels)} channels loaded. Type to filter.")
            self._filter(self._edit.text())
        # Asked again on every opening, even with a list in hand: channels added
        # to the archiver while the program is open must show up without a
        # restart, and the whole list comes back in ~0.03 s.
        self._kick_load()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Search for a CPVA channel — several words, separated by spaces, "
            "in any order:"))
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("e.g. 023 l3   or   L3-SPFE   or   Energy")
        self._edit.textEdited.connect(self._filter)
        lay.addWidget(self._edit)
        self._lbl_status = QLabel("Loading channels from CPVA…")
        self._lbl_status.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(self._lbl_status)
        self._lst = QListWidget()
        self._lst.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._lst.setAlternatingRowColors(False)
        self._lst.itemDoubleClicked.connect(self._on_add)
        lay.addWidget(self._lst, stretch=1)
        note = QLabel("Click to select (Ctrl+click for multiple), then click Add.")
        note.setStyleSheet("font-size:10px;color:#555;")
        lay.addWidget(note)
        row = QHBoxLayout()
        self._btn_add = QPushButton("Add selected")
        self._btn_add.setStyleSheet(_BTN_SUCCESS)
        self._btn_add.clicked.connect(self._on_add)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(self._btn_add); row.addWidget(btn_cancel)
        lay.addLayout(row)

    def _kick_load(self):
        sig = _ChanSig(self)
        sig.done.connect(self._on_loaded)
        sig.error.connect(self._on_load_failed)
        def _work():
            try:
                channels = cpva_fetch_channels(timeout=self._timeout)
                sig.done.emit(channels)
            except Exception as exc:
                sig.error.emit(str(exc))
        threading.Thread(target=_work, daemon=True).start()

    def _on_load_failed(self, err: str):
        if not self._all_channels:      # an older list on screen stays usable
            self._lbl_status.setText(f"Load failed: {err}")

    def _on_loaded(self, channels):
        global _pv_channel_cache
        if not channels and self._all_channels:
            return
        _pv_channel_cache = channels
        if channels == self._all_channels:
            return                      # nothing new — keep the list and the picks
        picked = {i.text() for i in self._lst.selectedItems()}
        self._all_channels = channels
        self._lbl_status.setText(
            f"{len(channels)} channels loaded. Type to filter."
            if channels else "CPVA returned no channels.")
        self._filter(self._edit.text())
        for i in range(self._lst.count()):
            if self._lst.item(i).text() in picked:
                self._lst.item(i).setSelected(True)

    _MAX_SHOWN = 500

    def _filter(self, text: str):
        self._lst.clear()
        match = make_pv_query_matcher(text)
        hits = [ch for ch in self._all_channels if match(ch)]
        for ch in hits[:self._MAX_SHOWN]:
            self._lst.addItem(ch)
        if not text.strip():
            self._lbl_status.setText(
                f"{len(self._all_channels)} channels loaded. Type to filter."
                if self._all_channels else "CPVA returned no channels.")
        elif len(hits) > self._MAX_SHOWN:
            self._lbl_status.setText(
                f"{len(hits)} matches — showing the first {self._MAX_SHOWN}.")
        else:
            self._lbl_status.setText(f"{len(hits)} matches.")

    def _on_add(self):
        items = self._lst.selectedItems()
        if not items:
            return
        self.selected_pvs = [it.text() for it in items]
        self.accept()


def _fmt_span(seconds):
    """A period the way a person says it: 45 s, 20 min, 6 h, 1 d 6 h, 30 d."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        m, r = divmod(s, 60)
        return f"{m} min" + (f" {r} s" if r else "")
    if s < 86400:
        h, r = divmod(s, 3600)
        m = r // 60
        return f"{h} h" + (f" {m} min" if m else "")
    d, r = divmod(s, 86400)
    h = r // 3600
    return f"{d} d" + (f" {h} h" if h else "")


def _fmt_span_full(seconds):
    """A period to the second, every unit written out: 1 d 2 h 5 min 3 s.
    Under a minute it keeps one decimal (12.4 s)."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    s = int(round(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return " ".join(f"{n} {unit}" for n, unit in
                    ((d, "d"), (h, "h"), (m, "min"), (s, "s")) if n)


# The built-in preset with no PVs in it (see CSSLoggerWidget._presets).
_BLANK_PRESET = "Blank"


MASTER_STEP_DEFAULT = "1000"


def _saved_step(d: dict) -> "str | None":
    """The "Keep steps every" value a preset, a saved tab or the config holds,
    or None when it holds none. The key was "master_multiple" before 12.10.2026
    and was saved empty by default — an empty old value is no choice, so it
    leaves the field (default 1000) alone; "master_step" is always taken."""
    if "master_step" in d:
        return str(d.get("master_step") or "").strip()
    old = str(d.get("master_multiple") or "").strip()
    return old or None


def _read_presets_file() -> list:
    """The preset file as the combo shows it: built-in Blank first, then one
    entry per name. The file is shared by every station, and an older build
    let "Save as new" add a second preset under an existing name; the program
    then opened the first copy while Save had written into the second, so
    saved PVs seemed lost. Duplicates collapse here: the later copy's content
    in the earlier copy's place."""
    merged: dict = {}
    for p in load_presets():
        name = str(p.get("name", "")).strip()
        if not name or name == _BLANK_PRESET:
            continue
        merged[name] = dict(p, name=name)   # dict keeps first position
    out = [{"name": _BLANK_PRESET, "pvs": [], "builtin": True}]
    out.extend(merged.values())
    return out

_GRAPH_STALE_NOTES = ("Load data first.", "No PVs loaded.",
                      "Graph error — see Log tab.")


class _SidebarText(QLabel):
    """A wrapped label for the narrow sidebar that never changes its width.

    A word-wrapped QLabel asks for at least the width of its longest word, and
    a PV name is one 40-character word — so a status line naming one pushed
    the whole sidebar wider than its viewport, and every message reflowed
    every button. This label ignores its own width hint (it takes the width
    the panel gives it), offers a line break after ``:`` ``_`` ``-`` ``/``
    ``.`` inside long names (zero-width spaces, display only), and with
    ``max_lines`` keeps a fixed height and ends a longer text with "…".
    ``text()`` always returns exactly what was set; the full text is the
    tooltip whenever it had to be shortened.
    """

    _BREAK_AFTER = re.compile(r"([:_\-/.])(?=\S)")

    def __init__(self, text="", max_lines=0, parent=None):
        super().__init__("", parent)
        self._raw = ""
        self._max_lines = int(max_lines)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Fixed if self._max_lines
                           else QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setStyleSheet(self, css):
        super().setStyleSheet(css)
        self._fit_height()

    def _fit_height(self):
        if not self._max_lines:
            return
        m = self.contentsMargins()
        self.ensurePolished()
        fm = self.fontMetrics()
        self.setFixedHeight(fm.lineSpacing() * self._max_lines
                            + m.top() + m.bottom() + 2 * self.margin()
                            + 2 * self.frameWidth() + 8)

    def text(self):
        return self._raw

    def _display(self, text):
        return self._BREAK_AFTER.sub("\\1\u200b", text)

    def setText(self, text):
        self._raw = "" if text is None else str(text)
        self._render()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._max_lines:
            self._render()

    def _render(self):
        shown = self._display(self._raw)
        cut = False
        if self._max_lines and shown:
            w = self.contentsRect().width() - 2 * self.margin() - 10
            if w > 20:
                fm = self.fontMetrics()
                limit = fm.lineSpacing() * self._max_lines
                flags = int(Qt.TextFlag.TextWordWrap)

                def _h(s):
                    return fm.boundingRect(QRect(0, 0, w, 100000), flags, s).height()

                if _h(shown) > limit:
                    lo, hi = 0, len(shown)
                    while lo < hi:               # longest prefix that fits with "…"
                        mid = (lo + hi + 1) // 2
                        if _h(shown[:mid].rstrip() + "…") <= limit:
                            lo = mid
                        else:
                            hi = mid - 1
                    shown = shown[:lo].rstrip() + "…"
                    cut = True
        super().setText(shown)
        self.setToolTip(self._raw if cut else "")


def _bin_icon(px=18):
    """A waste-bin drawn in dark ink. The 🗑 emoji came out as a pale grey
    outline that read as a disabled, empty button on the light panel."""
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, "#263238"), (QIcon.Mode.Active, "#263238"),
                      (QIcon.Mode.Selected, "#263238"), (QIcon.Mode.Disabled, "#9E9E9E")):
        for scale in (1, 2):
            s = px * scale
            pm = QPixmap(s, s)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(ink)); pen.setWidthF(1.5 * scale)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            u = s / 18.0
            p.drawLine(QPointF(3 * u, 4.5 * u), QPointF(15 * u, 4.5 * u))      # lid
            p.drawLine(QPointF(7 * u, 2.5 * u), QPointF(11 * u, 2.5 * u))      # handle
            body = QPolygonF([QPointF(4.5 * u, 6 * u), QPointF(13.5 * u, 6 * u),
                              QPointF(12.6 * u, 16 * u), QPointF(5.4 * u, 16 * u)])
            p.drawPolygon(body)
            for x in (7.3, 9.0, 10.7):                                     # ribs
                p.drawLine(QPointF(x * u, 8 * u), QPointF(x * u, 14 * u))
            p.end()
            pm.setDevicePixelRatio(scale)
            icon.addPixmap(pm, mode)
    return icon


class _InfoLabel(_SidebarText):
    """A label that takes no room while it has nothing to say."""

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class _GraphAxisLimitsDialog(QDialog):
    """Axis limits for the banded Graph tab.

    The generic Spectra dialog showed the time as a bare number and edited one
    unnamed Y axis — on this graph every PV has its own. Here the time range is
    a date and a clock, and the Y range is set per PV picked from a list. The Y
    numbers are the PV's own Y min / Y max (the same cells as in the PV table),
    so they survive the next redraw instead of being overwritten by it.
    """

    # The boxes take the program's own look: a border set here would cost the
    # date boxes their up/down arrows (a styled spin box drops its arrows).
    _FMT = "yyyy-MM-dd HH:mm:ss"

    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowTitle("Axis limits")
        self.setModal(True)
        self._owner = owner
        self._pvs = list(owner._graph_pvs)
        # pv → [ymin text, ymax text]; kept per PV so several can be set in one go.
        self._edits = {}
        self._cur_pv = None

        lay = QVBoxLayout(self)

        # ── Time ──
        gb_t = QGroupBox("Time axis")
        gt = QGridLayout(gb_t)
        lo_n, hi_n = owner._graph_axes[0].get_xlim()
        self._dt_from = self._dt_edit(lo_n)
        self._dt_to   = self._dt_edit(hi_n)
        gt.addWidget(QLabel("From:"), 0, 0); gt.addWidget(self._dt_from, 0, 1)
        gt.addWidget(QLabel("To:"),   1, 0); gt.addWidget(self._dt_to,   1, 1)
        gt.setColumnStretch(1, 1)
        lay.addWidget(gb_t)

        # ── Y ──
        gb_y = QGroupBox("Y axis")
        gy = QGridLayout(gb_y)
        self._combo = QComboBox()
        for i, pv in enumerate(self._pvs):
            st = owner._pv_settings.get(pv, {})
            name = st.get("display_name") or shorten_pv_name(pv)
            pm = QPixmap(12, 12)
            pm.fill(QColor(st.get("color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])))
            self._combo.addItem(QIcon(pm), name, pv)
            self._edits[pv] = [self._num_txt(st.get("ymin")), self._num_txt(st.get("ymax"))]
        self._e_ymin = QLineEdit(); self._e_ymax = QLineEdit()
        self._lbl_range = QLabel("")
        self._lbl_range.setStyleSheet("color:#444444;")
        gy.addWidget(QLabel("Signal:"), 0, 0); gy.addWidget(self._combo, 0, 1, 1, 2)
        gy.addWidget(QLabel("Y min:"), 1, 0); gy.addWidget(self._e_ymin, 1, 1, 1, 2)
        gy.addWidget(QLabel("Y max:"), 2, 0); gy.addWidget(self._e_ymax, 2, 1, 1, 2)
        gy.addWidget(self._lbl_range, 3, 1)
        b_fit = QPushButton("Fit to data")
        b_fit.setToolTip("Empty both boxes: this signal's band follows its data again")
        b_fit.clicked.connect(self._fit_to_data)
        gy.addWidget(b_fit, 3, 2)
        gy.setColumnStretch(1, 1)
        lay.addWidget(gb_y)
        self._combo.currentIndexChanged.connect(self._on_pv_changed)
        if self._pvs:
            self._on_pv_changed(0)
        else:
            gb_y.setEnabled(False)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.setMinimumWidth(420)

    # ── helpers ──
    def _dt_edit(self, num):
        e = QDateTimeEdit()
        e.setDisplayFormat(self._FMT)
        d = mdates.num2date(num, tz=TZ_PRAGUE)
        e.setDateTime(QDateTime(QDate(d.year, d.month, d.day),
                                QTime(d.hour, d.minute, d.second)))
        return e

    @staticmethod
    def _qdt_to_num(qdt):
        d, t = qdt.date(), qdt.time()
        dt = datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(),
                      t.second(), tzinfo=TZ_PRAGUE)
        return mdates.date2num(dt)

    @staticmethod
    def _num_txt(v):
        return "" if v is None else f"{v:.10g}"

    def _data_range(self, pv):
        try:
            i = self._owner._graph_pvs.index(pv)
            v = self._owner._graph_raw_np[i][1]
            v = v[np.isfinite(v)]
            if v.size:
                return float(v.min()), float(v.max())
        except Exception:
            pass
        return None

    def _store_current(self):
        if self._cur_pv is not None:
            self._edits[self._cur_pv] = [self._e_ymin.text(), self._e_ymax.text()]

    def _on_pv_changed(self, idx):
        self._store_current()
        pv = self._combo.itemData(idx)
        self._cur_pv = pv
        lo_t, hi_t = self._edits.get(pv, ["", ""])
        self._e_ymin.setText(lo_t); self._e_ymax.setText(hi_t)
        rng = self._data_range(pv)
        if rng:
            self._e_ymin.setPlaceholderText(f"data: {_fmt_cursor_value(rng[0])}")
            self._e_ymax.setPlaceholderText(f"data: {_fmt_cursor_value(rng[1])}")
            self._lbl_range.setText("")
        else:
            self._e_ymin.setPlaceholderText(""); self._e_ymax.setPlaceholderText("")
            self._lbl_range.setText("No values on screen")

    def _fit_to_data(self):
        self._e_ymin.clear(); self._e_ymax.clear()

    def _apply(self):
        self._store_current()
        x_lo = self._qdt_to_num(self._dt_from.dateTime())
        x_hi = self._qdt_to_num(self._dt_to.dateTime())
        if x_hi <= x_lo:
            QMessageBox.warning(self, "Axis limits", "'To' must be later than 'From'.")
            return
        parsed = {}
        for pv, (lo_t, hi_t) in self._edits.items():
            lo = CSSLoggerWidget._safe_float(lo_t, fallback="bad")
            hi = CSSLoggerWidget._safe_float(hi_t, fallback="bad")
            st = self._owner._pv_settings.get(pv, {})
            name = st.get("display_name") or shorten_pv_name(pv)
            if lo == "bad" or hi == "bad":
                QMessageBox.warning(self, "Axis limits", f"{name}: not a number.")
                return
            if lo is not None and hi is not None and hi <= lo:
                QMessageBox.warning(self, "Axis limits",
                                    f"{name}: Y max must be above Y min.")
                return
            parsed[pv] = (lo, hi)
        self._owner._apply_axis_limits_dialog((x_lo, x_hi), parsed)
        self.accept()

# ── Flow layout (wraps children to the next row, never overflows) ───────────

class _ResizeHook(QObject):
    """Calls `callback()` whenever the watched widget changes size. Watch a
    widget whose size the callback itself does not change, or it loops."""

    def __init__(self, widget, callback):
        super().__init__(widget)
        self._cb = callback
        self._w = None
        widget.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            w = (obj.width(), obj.height())
            if w != self._w:
                self._w = w
                try:
                    self._cb()
                except Exception:
                    pass
        return False


class _GraphLoupe(QFrame):
    """A small magnified view of the spot under the mouse.

    It shows ONE signal — the one whose trace is nearest the mouse — and only
    the patch of graph around the mouse: a box `magnification` times smaller
    than the loupe, in both time and value, so the picture is the main graph
    magnified evenly. The trace is redrawn from the main line's own data (not
    a blown-up screenshot), so it stays sharp and follows live updates. A
    cross-hair marks the mouse; the title names the signal, the time and the
    value there.

    A child widget of the graph area, never a window of its own: it cannot take
    the keyboard focus, and it lets every mouse event through to the graph."""

    W, H = 380, 230

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("graphLoupe")
        self.setStyleSheet("QFrame#graphLoupe { background: #ffffff;"
                           " border: 2px solid #2f6fb5; border-radius: 3px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self._fig = Figure(figsize=(self.W / 100.0, self.H / 100.0), dpi=100)
        self._fig.patch.set_facecolor("white")
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._canvas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self._canvas)
        self.setFixedSize(self.W, self.H)
        self._key = None                 # (main figure, source line) it was built for
        self._ax = None
        self._line = None
        self._vline = None
        self._hline = None
        self._title = None
        self._sel_patch = None
        self.hide()

    @staticmethod
    def _value_at(xs, ys, x):
        """The value holding at time `x` (steps-post: the last sample at or
        before it), or None before the first sample."""
        if xs.size == 0:
            return None
        i = int(np.searchsorted(xs, x, side="right")) - 1
        if i < 0:
            return None
        v = float(ys[i])
        return v if np.isfinite(v) else None

    @classmethod
    def nearest_trace(cls, x, y_px, main_axes, main_lines):
        """Index of the visible trace whose value at time `x` is drawn closest
        (in screen pixels, vertically) to the mouse at height `y_px`."""
        best, best_d = None, None
        for i, ax in enumerate(main_axes):
            if i >= len(main_lines) or not main_lines[i]:
                continue
            src = main_lines[i][-1]
            if not src.get_visible():
                continue
            try:
                xs = np.asarray(src.get_xdata(), dtype=float)
                ys = np.asarray(src.get_ydata(), dtype=float)
            except Exception:
                continue
            v = cls._value_at(xs, ys, x)
            if v is None:
                continue
            d = abs(float(ax.transData.transform((x, v))[1]) - float(y_px))
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    def _rebuild(self, src):
        self._fig.clear()
        ax = self._fig.add_axes([0.01, 0.02, 0.98, 0.86])
        ax.set_navigate(False)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_color("#9e9e9e")
        self._sel_patch = ax.axvspan(0, 0, color="#1565C0", alpha=0.15, lw=0)
        # The same look as the main trace: colour, width, line and point style.
        self._line, = ax.plot(
            [], [], color=src.get_color(), linewidth=float(src.get_linewidth()),
            linestyle=src.get_linestyle(), drawstyle=src.get_drawstyle(),
            marker=src.get_marker(), markersize=float(src.get_markersize()),
            alpha=src.get_alpha(), zorder=2)
        # Neutral grey dashes: a coloured cross-hair could match the trace.
        self._vline = ax.axvline(0, color="#555555", lw=0.9, ls=(0, (4, 3)), zorder=10)
        self._hline = ax.axhline(0, color="#555555", lw=0.9, ls=(0, (4, 3)), zorder=10)
        self._title = self._fig.text(0.5, 0.985, "", ha="center", va="top",
                                     fontsize=9, fontweight="bold", color="#111111")
        self._ax = ax

    def update_at(self, x, main_axes, main_lines, magnification, sel_range,
                  y_px=None, names=None):
        """Show the spot around the mouse at date number `x` and screen height
        `y_px` (matplotlib display pixels; None = the middle of the graph).
        `names[i]` labels trace i. Returns False if there is nothing to show."""
        if not main_axes:
            self.hide()
            return False
        ax0 = main_axes[0]
        if y_px is None:
            y_px = float(ax0.bbox.y0 + ax0.bbox.height / 2.0)
        i = self.nearest_trace(x, y_px, main_axes, main_lines)
        if i is None:
            self.hide()
            return False
        src, ax_main = main_lines[i][-1], main_axes[i]
        key = (id(ax0.figure), id(src))
        if key != self._key:
            self._rebuild(src)
            self._key = key
        # The box shown: the loupe's own size shrunk by the magnification, in
        # screen pixels around the mouse, read back into this signal's data.
        mag = max(1.0, float(magnification))
        try:
            dpr = float(getattr(ax0.figure.canvas, "device_pixel_ratio", 1.0) or 1.0)
        except Exception:
            dpr = 1.0
        x_px = float(ax_main.transData.transform((x, 0.0))[0])
        hw = self.W * dpr / mag / 2.0
        hh = self.H * dpr / mag / 2.0
        inv = ax_main.transData.inverted()
        (lo, y_lo), (hi, y_hi) = inv.transform([(x_px - hw, y_px - hh),
                                                (x_px + hw, y_px + hh)])
        y_mouse = float(inv.transform((x_px, y_px))[1])
        try:
            xs = np.asarray(src.get_xdata(), dtype=float)
            ys = np.asarray(src.get_ydata(), dtype=float)
        except Exception:
            xs = ys = np.empty(0)
        if xs.size:
            # One sample either side of the box: with steps-post the one before
            # is the value still holding at the left edge.
            a = max(0, int(np.searchsorted(xs, lo, side="right")) - 1)
            b = min(xs.size, int(np.searchsorted(xs, hi, side="right")) + 1)
            self._line.set_data(xs[a:b], ys[a:b])
        else:
            self._line.set_data([], [])
        self._ax.set_xlim(lo, hi)
        self._ax.set_ylim(min(y_lo, y_hi), max(y_lo, y_hi))
        self._vline.set_xdata([x, x])
        self._hline.set_ydata([y_mouse, y_mouse])
        if sel_range:
            s0, s1 = sel_range
            self._sel_patch.set_visible(True)
            try:
                self._sel_patch.set_x(s0)
                self._sel_patch.set_width(s1 - s0)
            except AttributeError:       # older matplotlib: a Polygon
                self._sel_patch.set_xy([[s0, 0], [s0, 1], [s1, 1], [s1, 0], [s0, 0]])
        else:
            self._sel_patch.set_visible(False)
        name = (names[i] if names and i < len(names) else "") or ""
        v = self._value_at(xs, ys, x)
        try:
            t = mdates.num2date(x, tz=TZ_PRAGUE)
            when = f"{t.strftime('%H:%M:%S')}.{t.microsecond // 1000:03d}"
        except Exception:
            when = ""
        parts = [p for p in (name, when,
                             _fmt_cursor_value(v) if v is not None else "",
                             f"{int(mag)}×") if p]
        self._title.set_text("   ".join(parts))
        self._canvas.draw()
        return True

    def place_near(self, pos):
        """Put the loupe beside `pos` (parent coordinates), never under it."""
        p = self.parentWidget()
        if p is None:
            return
        gap = 24
        x = pos.x() + gap
        y = pos.y() + gap
        if x + self.width() > p.width():
            x = pos.x() - gap - self.width()
        if y + self.height() > p.height():
            y = pos.y() - gap - self.height()
        self.move(max(0, x), max(0, y))
        if not self.isVisible():
            self.show()
        self.raise_()


class _RowDragger(QObject):
    """Lets the user drag table rows up and down with the mouse.

    Qt's own drag-and-drop for a QTableWidget moves CELLS, overwrites the
    target and needs every cell to be drag-enabled; here a row is a channel
    and the table is rebuilt from an order list anyway. So the drag is done
    by hand: press on a row, move a few pixels, and a blue line shows where the
    row(s) will land; releasing calls `on_drop(rows, before_row)`, where `rows`
    are the selected rows (or the pressed one) and `before_row` is the row the
    line sits above (rowCount() = after the last row). `can_drag(row)` says
    whether a row may be picked up at all (divider rows may not)."""

    def __init__(self, table, can_drag, on_drop):
        super().__init__(table)
        self._tv = table
        self._can_drag = can_drag
        self._on_drop = on_drop
        self._press = None              # (row, QPoint) of the left press
        self._dragging = False
        self._before = -1
        self._line = QFrame(table.viewport())
        self._line.setStyleSheet("background:#1565C0;border:none;")
        self._line.setFixedHeight(3)
        self._line.hide()
        table.viewport().installEventFilter(self)

    def _gap_at(self, y):
        """The row boundary nearest to viewport height `y`."""
        tv = self._tv
        n = tv.rowCount()
        row = tv.rowAt(y)
        if row < 0:
            return n if y > 0 else 0
        top = tv.rowViewportPosition(row)
        return row if y < top + tv.rowHeight(row) / 2 else row + 1

    def _place_line(self, before):
        tv = self._tv
        n = tv.rowCount()
        if n == 0:
            self._line.hide()
            return
        if before >= n:
            y = tv.rowViewportPosition(n - 1) + tv.rowHeight(n - 1)
        else:
            y = tv.rowViewportPosition(before)
        self._line.setGeometry(0, max(0, y - 1), tv.viewport().width(), 3)
        self._line.show()
        self._line.raise_()

    def eventFilter(self, obj, ev):
        t = ev.type()
        tv = self._tv
        if t == QEvent.Type.MouseButtonPress:
            self._press = None
            if (ev.button() == Qt.MouseButton.LeftButton
                    and tv.state() != QAbstractItemView.State.EditingState):
                pos = ev.position().toPoint()
                row = tv.rowAt(pos.y())
                if row >= 0 and self._can_drag(row):
                    self._press = (row, pos)
            return False
        if t == QEvent.Type.MouseMove and self._press is not None:
            if not (ev.buttons() & Qt.MouseButton.LeftButton):
                self._press = None
                return False
            pos = ev.position().toPoint()
            if not self._dragging:
                if ((pos - self._press[1]).manhattanLength()
                        < QApplication.startDragDistance()):
                    return False
                self._dragging = True
                tv.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            self._before = self._gap_at(pos.y())
            self._place_line(self._before)
            return True                 # no rubber-band selection while dragging
        if t == QEvent.Type.MouseButtonRelease and self._press is not None:
            row0 = self._press[0]
            self._press = None
            if not self._dragging:
                return False
            self._dragging = False
            self._line.hide()
            tv.viewport().unsetCursor()
            rows = sorted({i.row() for i in tv.selectionModel().selectedRows()})
            if row0 not in rows:
                rows = [row0]
            rows = [r for r in rows if self._can_drag(r)]
            if rows:
                try:
                    self._on_drop(rows, self._before)
                except Exception as exc:      # never let a drop crash the app
                    print(f"[row drag] {exc!r}")
            return True
        return False


class _FlowLayout(QLayout):
    """A left-to-right layout that wraps onto a new row when it runs out of
    width — so stat cards line up side by side and nothing spills past the
    right edge of the panel."""

    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        """Full height (margins included) the items need at this width."""
        return self._do_layout(QRect(0, 0, width, 0), True)

    def first_row_height(self, width):
        """Height (margins included) of the FIRST row only at this width — what
        the statistics strip shows when it is folded to one row."""
        return self._do_layout(QRect(0, 0, width, 0), True, rows=1)

    def rows_height(self, width, rows):
        """Height (margins included) of the first ``rows`` rows at this width."""
        return self._do_layout(QRect(0, 0, width, 0), True, rows=rows)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        # The size this layout really wants at its current width. Returning the
        # minimum (the largest single card) made the strip's height a guess —
        # too tall for one row of cards, leaving a dead band under them.
        g = self.geometry()
        w = g.width() if g.width() > 0 else 600
        return QSize(w, self.heightForWidth(w))

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only, rows=None):
        m = self.contentsMargins()
        inner = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = inner.x(), inner.y()
        line_height = 0
        spacing = self.spacing()
        row = 1
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            # QRect.right() is the last pixel INSIDE, so a card ending exactly
            # on the edge still fits (it used to be pushed to a new row).
            if next_x - spacing > inner.x() + inner.width() and line_height > 0:
                if rows is not None and row >= rows:
                    break
                row += 1
                x = inner.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


# ── Floating graph window (F11 fullscreen / Ctrl+F11 windowed) ──────────────

class _GraphPopupWindow(QWidget):
    """Top-level window that hosts the Graph tab when popped out. Closing it (or
    pressing Esc / F11) hands the tab back to the notebook instead of destroying
    it."""

    _BORDER = 8          # px of frame round the graph that doubles as the resize grip

    def __init__(self, owner):
        # No title bar: "focus mode" means the graph and nothing else, the same on
        # both tabs and the same as the Image Slider. Esc / F11 dock it back, and
        # closeEvent below catches Alt+F4, so losing the tab is not a risk.
        super().__init__(None, Qt.WindowType.Window
                               | Qt.WindowType.FramelessWindowHint)
        self._owner = owner
        self.setWindowTitle("CSS Logger — Graph")
        self._resize = None         # (edges, press point, start geometry) of a manual resize
        self.setObjectName("graphFocus")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#graphFocus { background: #202020; }")
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        # Any left press on the graph moves the window (the statistics drag is
        # given up in focus mode — Spectra and Image Slider behave the same).
        # Watched application-wide rather than per child because the graph's
        # canvas is rebuilt on every replot, and a new canvas would escape a
        # filter installed once.
        QApplication.instance().installEventFilter(self)

    def set_framed(self, framed: bool):
        """Windowed: a frame to grab for resizing. Fullscreen: no frame."""
        b = self._BORDER if framed else 0
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(b, b, b, b)

    def stop_watching(self):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.MouseButtonPress
                and ev.button() == Qt.MouseButton.LeftButton
                and not self.isFullScreen()
                and isinstance(obj, QWidget) and obj is not self
                and self.isAncestorOf(obj)):
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return True
        return False

    def _edges_at(self, pos) -> Qt.Edge:
        if self.isFullScreen():
            return Qt.Edge(0)
        b, edges = self._BORDER, Qt.Edge(0)
        if pos.x() <= b:                    edges |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - b:     edges |= Qt.Edge.RightEdge
        if pos.y() <= b:                    edges |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - b:    edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges) -> Qt.CursorShape:
        left  = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top   = bool(edges & Qt.Edge.TopEdge)
        bot   = bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bot):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bot):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bot:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self._owner._restore_graph_from_popup(); return
        if ev.key() == Qt.Key.Key_F11:
            if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._owner._graph_popout(windowed=False)
            else:
                self._owner._graph_popout(windowed=True)
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        # Only the frame reaches here (the graph's own presses are taken by the
        # filter above): the frame resizes, its corners resize both ways.
        if ev.button() != Qt.MouseButton.LeftButton or self.isFullScreen():
            return super().mousePressEvent(ev)
        edges  = self._edges_at(ev.position().toPoint())
        handle = self.windowHandle()
        if edges and handle is not None and handle.startSystemResize(edges):
            return
        if edges:
            # The window manager declined; do the arithmetic ourselves.
            self._resize = (edges, ev.globalPosition().toPoint(), QRect(self.geometry()))
            return
        if handle is not None:
            handle.startSystemMove()

    def mouseMoveEvent(self, ev):
        if self._resize is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            edges, g0, r0 = self._resize
            d = ev.globalPosition().toPoint() - g0
            g = QRect(r0)
            if edges & Qt.Edge.LeftEdge:   g.setLeft(r0.left() + d.x())
            if edges & Qt.Edge.RightEdge:  g.setRight(r0.right() + d.x())
            if edges & Qt.Edge.TopEdge:    g.setTop(r0.top() + d.y())
            if edges & Qt.Edge.BottomEdge: g.setBottom(r0.bottom() + d.y())
            if g.width() >= self.minimumWidth() and g.height() >= self.minimumHeight():
                self.setGeometry(g)
            return
        self.setCursor(self._cursor_for(self._edges_at(ev.position().toPoint())))
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._resize = None
        super().mouseReleaseEvent(ev)

    def closeEvent(self, ev):
        # Never lose the tab — reparent it back into the notebook.
        ev.ignore()
        self._owner._restore_graph_from_popup()


# ── CSSLoggerWidget ────────────────────────────────────────────────────────

class CSSLoggerWidget(QWidget):
    """Full PySide6 port of CPVAExplorerApp from cssl.py."""

    def __init__(self, parent=None, tab_state=None):
        super().__init__(parent)
        _day_cache()          # starts looking for the share now, not at the first load
        # A setting tab restored from logger_tabs.json (see logger_tabs.py), or
        # None for the classic start from the config file.
        self._tab_state = dict(tab_state) if tab_state else None
        self._suspended = False
        try:
            self._custom_pvs_mtime = CUSTOM_PVS_FILE.stat().st_mtime
        except OSError:
            self._custom_pvs_mtime = None
        self._init_state()
        self._build_ui()
        self._populate_ui()
        if self._tab_state:
            self._apply_tab_state(self._tab_state)
        self._install_graph_shortcuts()
        # Graph tab defaults to live mode — start it shortly after the window shows
        # (deferred so the UI is fully realised and the Operation preset PVs are loaded).
        QTimer.singleShot(600, self._maybe_autostart_live)

    def _maybe_autostart_live(self):
        if self._live_mode:
            return
        pvs = self._real_pv_names()
        if not pvs:
            return
        if self._tab_state is not None and not self._tab_state.get("live", True):
            # A tab saved standing still comes back on its own window.
            self._request_reload(delay_ms=0, reason="tab restored")
            return
        self._toggle_live_mode()

    # ── Setting tabs (logger_tabs.py) ────────────────────────────────────────

    def export_tab_state(self) -> dict:
        """What a setting tab remembers across a restart: its channels, preset,
        master filter, conditions, and its window — live with its span, or the
        fixed From/To."""
        span = (self._live_window_span.total_seconds()
                if self._live_mode and self._live_window_span else None)
        return {
            "preset": self._preset_combo.currentText(),
            "pvs": self._real_pv_names(),
            **self._ramp_settings(),
            "conditions": [dict(c) for c in self._conditions_view()],
            "conditions_on": bool(getattr(self, "_conditions_on", True)),
            "live": bool(self._live_mode),
            "live_span_s": span,
            "time_from": self._dt_from.strftime("%Y-%m-%d %H:%M:%S"),
            "time_to": self._dt_to.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _apply_tab_state(self, st: dict):
        """Put a saved tab's setting on screen (before anything is loaded)."""
        name = st.get("preset") or ""
        idx = self._preset_combo.findText(name) if name else -1
        if idx >= 0:
            self._preset_combo.blockSignals(True)
            self._preset_combo.setCurrentIndex(idx)
            self._preset_combo.blockSignals(False)
        pvs = st.get("pvs")
        if isinstance(pvs, list):
            self._pv_list.clear()
            for pv in pvs:
                if isinstance(pv, str) and pv:
                    self._pv_list.addItem(self._make_pv_item(pv))
            self._sync_pv_list_customs()
            self._update_pv_count()
        self._apply_ramp_settings(st)
        if isinstance(st.get("conditions"), list):
            self._conditions_on = bool(st.get("conditions_on", True))
            chk = getattr(self, "_chk_cond_on", None)
            if chk is not None:
                chk.blockSignals(True)
                chk.setChecked(self._conditions_on)
                chk.blockSignals(False)
            self._set_conditions(st["conditions"])
        span = st.get("live_span_s")
        if st.get("live") and isinstance(span, (int, float)) and span > 0:
            now = datetime.now()
            self._dt_from, self._dt_to = now - timedelta(seconds=float(span)), now
        else:
            dt_from = parse_user_datetime(st.get("time_from") or "")
            dt_to = parse_user_datetime(st.get("time_to") or "")
            if dt_from and dt_to and dt_to > dt_from:
                self._dt_from, self._dt_to = dt_from, dt_to
        self._refresh_time_labels()

    def suspend(self):
        """This tab is no longer on screen. Live keeps reading — every
        HIDDEN_LIVE_POLL_MS instead of every few hundred milliseconds — but
        nothing is redrawn until the tab is shown again."""
        if self._suspended:
            return
        self._suspended = True
        self._countdown_timer.stop()
        if self._live_mode and self._live_timer.isActive():
            # Re-arm at the slow pace straight away.
            self._live_timer.start(self._live_poll_ms())

    def resume(self):
        """This tab is on screen again: bring Live up to now at once, and draw
        what was read while it was hidden."""
        if not self._suspended:
            return
        self._suspended = False
        self._reload_custom_pvs_if_changed()
        if self._live_mode:
            # Rebuild the table and the graph on the next tick, whatever their
            # clocks say, and make that tick happen now.
            self._live_rows_stale = True
            self._live_last_rebuild_ns = 0
            self._live_last_graph_ns = 0
            if self._live_timer.isActive():
                self._live_timer.stop()
                self._live_tick()
        elif getattr(self, "_hidden_dirty", False) and self._samples_by_pv:
            self._hidden_dirty = False
            self._plot_graph()
            self._populate_table()

    def _reload_custom_pvs_if_changed(self):
        """custom_pvs.json is shared by every tab: another tab may have changed
        the custom PVs while this one was hidden."""
        try:
            mtime = CUSTOM_PVS_FILE.stat().st_mtime
        except OSError:
            return
        seen = getattr(self, "_custom_pvs_mtime", None)
        self._custom_pvs_mtime = mtime
        if mtime == seen:
            return
        self._custom_pvs = load_custom_pvs()
        self._sync_pv_list_customs()
        self._update_pv_count()
        self._cpv_last_diag = None
        if self._table_rows_unfiltered:
            self._rebuild_custom_pvs()
            rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
            self._table_rows = self._apply_conditions_to_rows(rows)
            self._plot_graph()
            self._populate_table()

    # ── State init ─────────────────────────────────────────────────────────

    def _init_state(self):
        self.config = load_config()
        # Graph appearance / performance options (see _GRAPH_OPTS_DEFAULTS).
        # Unknown keys from an older/newer config are ignored, missing ones
        # fall back to the default.
        self._graph_opts = dict(_GRAPH_OPTS_DEFAULTS)
        for k, v in (self.config.get("graph_opts") or {}).items():
            if k in self._graph_opts:
                self._graph_opts[k] = v
        # A saved 8 is the old default written back on close, not a choice —
        # moved once to the new default; an 8 picked after that is kept.
        if not self.config.get("x_ticks_default_12"):
            if self._graph_opts.get("x_ticks_max") == 8:
                self._graph_opts["x_ticks_max"] = _GRAPH_OPTS_DEFAULTS["x_ticks_max"]
            self.config["x_ticks_default_12"] = True
        # Same for the Y-axis column spacing: the old defaults written back on
        # close move to the tighter ones once; a value set by hand is kept.
        if not self.config.get("y_axis_tight_v1"):
            for _k, _old in (("axis_gap_px", 6), ("label_pad_px", 5),
                             ("outer_margin_px", 8)):
                if self._graph_opts.get(_k) == _old:
                    self._graph_opts[_k] = _GRAPH_OPTS_DEFAULTS[_k]
            self.config["y_axis_tight_v1"] = True
        # Second step: a little more air towards the neighbouring axis, one
        # pixel less between a title and its own numbers.
        if not self.config.get("y_axis_spacing_v2"):
            for _k, _old in (("axis_gap_px", 3), ("label_pad_px", 1)):
                if self._graph_opts.get(_k) == _old:
                    self._graph_opts[_k] = _GRAPH_OPTS_DEFAULTS[_k]
            self.config["y_axis_spacing_v2"] = True
        self._samples_by_pv: dict = {}
        self._table_rows:    list = []
        self._pv_order:      list = []
        # The user's own order of the PV table rows and the graph's Y axes, set
        # by dragging rows in the table. Independent of the PV list on the left.
        self._plot_order:    list = [str(p) for p in
                                     (self.config.get("plot_order") or [])]
        self._base_pv_order: list = []
        self._last_pv_search: str = ""
        self._col_full_names: dict = {}
        # "Blank" (no PVs) is built in and always first. It is never written to
        # the preset file (_store_presets), so it cannot be saved over or lost.
        self._presets: list = _read_presets_file()
        self._condition_presets: list = load_condition_presets()
        self._custom_pvs:        list = load_custom_pvs()
        self._cpv_diag:          list = []    # problems from the last compute
        self._cpv_last_diag           = None  # last set logged (anti-spam)
        self._repository_overlays = []
        self._repository_artists  = []
        self._mpl_canvas  = None
        self._mpl_figure  = None
        self._graph_axes  = []
        self._graph_lines: list = []
        self._graph_pvs:   list = []
        self._graph_raw:   list = []
        self._graph_raw_np: list = []
        self._pairs_cache  = None   # (token, {pv: [(ts_ns, value), …]}) for replots
        self._graph_spine_xpos: list = []
        self._span_selector  = None
        self._zoom_selector  = None
        # Zoom state. The view history itself belongs to the graph toolbar (Home /
        # Back / Forward), so there is only ONE history — a second, hand-kept one
        # would drift out of step with it. This flag just records "the user is
        # looking somewhere of their own choosing", which stops the live window
        # from scrolling the view out from under them.
        self._user_zoomed  = False
        self._grid_pvs_drawn: list = []   # channels that got a grid, in style order
        self._graph_toolbar = None
        self._xy_toolbar    = None
        self._reticking     = False
        # The statistics selection, as (xmin, xmax) matplotlib date numbers, i.e.
        # ABSOLUTE time. It has to live here and not only inside the SpanSelector:
        # every full replot destroys the figure (and with it the selector and the
        # stat cards), which is why the selected region and its numbers used to
        # disappear on a reload, a font change or any axis-table edit.
        self._sel_range = None
        # True while the selection was dragged out past the right-hand edge: its
        # end then follows the newest sample (_follow_live_selection).
        self._sel_live = False
        self._crosshair_vlines = []
        self._crosshair_hlines = []
        self._crosshair_texts  = []
        self._x_cursor_ann     = None
        self._y_cursor_ann     = None
        self._y_cursor_ann_ax  = None
        self._crosshair_cid    = None
        self._blit_bg          = None
        self._mouse_pending    = False
        self._mouse_last_event = None
        self._cursor_frame_ms  = 16.0   # adaptive crosshair frame budget
        self._cursor_active    = False
        self._in_cursor_redraw = False
        self._xy_canvas = None
        self._xy_figure = None
        self._xy_rows   = []
        self._xy_scatter = None
        self._xy_rect_selector = None
        # (No hand-kept XY view history: the XY toolbar owns Home / Back / Forward.)
        self._xy_choice_map: dict = {}
        # Single banded graph: every PV lives in its own vertical band of one
        # shared plot (CS-Studio style). A PV with Autoscale on spans full height.
        self._pv_time_canvas  = None
        self._pv_time_figure  = None
        self._pv_time_toolbar = None
        self._pv_time_cond_counts: dict = {}   # condition → (kept, total) shots
        self._pv_time_auto_ready = False         # True once the tab has been opened
        # The PV Time tab reads the ARCHIVER itself — whole days of the channels
        # the user has in the PV list — instead of the pre-built day files it
        # used to read (those stopped being written in June 2026). Every day it
        # has already read stays in _pv_time_cache for the session, keyed by the
        # day and the exact set of PVs asked for, so changing the Y channel or a
        # condition redraws at once instead of fetching the days again.
        self._pv_time_days: list = []            # dates picked in the calendar
        self._pv_time_cache: dict = {}
        self._pv_time_choice_map: dict = {}      # label shown -> channel name
        self._pv_time_sig    = None
        self._pv_time_epoch  = 0                 # bumped to abandon a fetch
        self._pv_time_busy   = False
        self._pv_settings: dict = {}
        # Names the user gave PVs (PV list rename or the PV table's Display
        # Name), kept across restarts. Only the ones that differ from the
        # automatic short name are stored.
        self._pv_display_names: dict = dict(self.config.get("pv_display_names") or {})
        self._live_mode     = False
        self._live_last_ts  = None
        self._live_window_span = None
        # Live pacing: the graph and the table each keep their own last-run stamp
        # and their own measured cost, so the slow one cannot hold the fast one back.
        self._live_last_rebuild_ns = 0   # last table rebuild
        self._live_last_graph_ns   = 0   # last graph refresh
        self._live_table_cost_ns   = 0   # measured cost of one table rebuild
        self._live_graph_cost_ns   = 0   # measured cost of one graph refresh
        # A zoom switched Live off; Home switches it back on (_live_off_by_zoom).
        self._live_stopped_by_zoom = False
        self._tb_press_xlim = None       # range a toolbar Zoom / Pan drag began at
        # Cancel token for background fetches. Every fetch worker captures the
        # epoch it was started in and its pool aborts as soon as the epoch moves,
        # because stopping a QTimer cannot stop an already-running ThreadPool:
        # "Stop Live" used to leave hundreds of queued requests running, so the
        # app kept stuttering for a long while after it claimed to be idle.
        self._live_epoch = 0
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._live_tick)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(50)
        self._countdown_timer.timeout.connect(self._live_countdown_tick)
        self._live_countdown_elapsed_ms = 0
        # Automatic loading. There is no "Load data" button: anything that changes
        # WHAT should be shown (channels, time window, preset) asks for a reload,
        # and these collapse a burst of changes into one fetch.
        self._load_in_flight  = False
        self._reload_pending  = False
        self._pending_reload_reason = ""
        # The table export runs on its own, beside the load: its own cancel
        # token and its own progress window.
        self._export_epoch    = 0
        self._export_prog     = None
        # Bumped whenever a load is started, stopped or superseded. A worker
        # compares it against the value it captured, so a load that is no longer
        # wanted drops every request it has not started yet.
        self._load_epoch      = 0
        self._last_fetch_report = None
        self._partial_busy    = False
        self._loading_from_ns = None   # load in progress: older than this is unread
        self._partial_table_done = False
        # pv -> (ts, lo, hi) arrays: the lowest/highest value archived behind
        # each thinned point (see _minmax_arrays). The graph draws it as a band.
        self._minmax_by_pv: dict = {}
        # Finer data read for a zoomed-in range: {"a", "b", "epoch", "pvs":
        # {pv: (ts, val, lo, hi)}}. Spliced into the traces, never the table.
        self._zoom_detail = None
        self._detail_in_flight = False
        self._graph_bands: list = []
        self._graph_env: list = []
        self._graph_base: list = []
        self._autoload_timer = QTimer(self)
        self._autoload_timer.setSingleShot(True)
        self._autoload_timer.timeout.connect(self._do_auto_reload)
        # Coalesced cursor-table refresh: the QTableWidget is only rewritten when
        # the mouse settles, so dragging the crosshair stays smooth (the blit
        # crosshair itself updates every move).
        self._cursor_tbl_timer = QTimer(self)
        self._cursor_tbl_timer.setSingleShot(True)
        self._cursor_tbl_timer.timeout.connect(self._flush_cursor_table)
        # Coalesced replot: rebuilding the whole figure is expensive, so rapid
        # axis-table edits (ticking Show, editing Y min/max, …) are collapsed into
        # a single redraw once the user stops clicking, keeping the UI responsive.
        self._replot_timer = QTimer(self)
        self._replot_timer.setSingleShot(True)
        self._replot_timer.timeout.connect(self._plot_graph)
        self._ramping_repository = load_ramping_repository()
        self._data_repository: list = []
        self._ref_lines: list      = []
        self._pre_window_vals: dict = {}  # pv → (ts_ns, value, units) before window start
        self._plot_window_ns = None       # explicit carry-forward window (live mode)
        # The last shot before the window that passes the Conditions, held
        # across a window whose own shots (and the value just before it) the
        # Conditions reject — see _maybe_hunt_last_good.
        self._last_good = None
        self._lg_epoch = 0
        self._lg_sig = None
        # Every condition row, switched on or not (what the editor shows), and
        # the switched-on ones with their effective min/max (what filters).
        self._conditions_all: list = [_cond_normalize(c) for c in
                                      (self.config.get("conditions") or [])]
        # "Apply conditions" in the sidebar: off shows everything (trigger shots
        # included) without losing the rows. _conditions is always the list
        # that actually filters, so every filter path obeys the switch.
        self._conditions_on: bool = bool(self.config.get("conditions_enabled", True))
        self._conditions: list = ([c for c in self._conditions_all if c["enabled"]]
                                  if self._conditions_on else [])
        self._cond_last_diag = None   # de-dupes the per-refresh conditions log
        # Live selection: its own untrimmed copy of the samples since its start
        # (the live window trims older ones), and the "restart after a pause".
        self._sel_samples: dict = {}
        self._sel_pre: dict = {}
        self._sel_restart_note = ""
        self._sel_pause_on: bool = bool(self.config.get("sel_pause_restart", False))
        self._sel_pause_s: float = float(self.config.get("sel_pause_s", 15.0) or 15.0)
        self._sel_fixed_restart_note = ""
        # Dead shots (energy ~0 at an unchanged waveplate) left out of the
        # selection statistics — see _dead_shot_mask.
        self._dead_shot_on: bool = bool(self.config.get("dead_shot_skip", True))
        self._dead_energy_pv: str = str(self.config.get(
            "dead_shot_energy_pv", "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy") or "")
        self._dead_energy_thr: float = float(
            self.config.get("dead_shot_energy_max", 0.05) or 0.0)
        self._table_rows_unfiltered: list = []
        self._numeric_pvs: set = set()
        self._master_pv: str       = self.config.get("master_pv", MASTER_RAMP_PV)
        _step = _saved_step(self.config)
        self._master_multiple: str = MASTER_STEP_DEFAULT if _step is None else _step
        # Qt string vars (set by sidebar widgets after _build_ui)
        self._master_pv_edit        = None
        self._master_multiple_edit  = None
        now = datetime.now()
        self._dt_from = now - timedelta(hours=1)
        self._dt_to   = now
        self._font_size: int = 11
        self._axis_tv_cols = ("show", "pv", "display_name", "color", "cursor_val",
                               "ymin", "ymax", "auto_scale",
                               "width", "style", "marker", "marker_size", "alpha",
                               "smooth", "grid",
                               "unit", "last", "min", "max", "mean", "count",
                               "blank")
        self._axis_last_clicked_row = -1
        self._axis_row_pv: list = []     # table row → PV name (None for divider rows)
        # Measured values per PV, keyed by (pv, sample count) so a replot does not
        # walk 100k samples again for a number that cannot have changed.
        self._pv_stats_cache: dict = {}
        # Reference-line placement by clicking (None = not picking). See
        # _start_ref_pick: {"stage": 1|2, "pv": str|None, "dialog": _RefLinesDialog}
        self._ref_pick = None
        self._ref_line_artists: list = []
        self._ref_dialog = None
        # Named looks the user chose to keep (colours, line/point styles,
        # reference lines, column layout). Nothing is stored automatically.
        self._style_presets: dict = dict(self.config.get("style_presets") or {})
        # Name of the look last loaded, saved or imported ("" = none yet).
        # Not kept between runs: the current look itself is not kept either,
        # so a fresh start is always "none".
        self._style_current: str = ""
        self._graph_popup = None       # floating graph window (F11 / Ctrl+F11)
        self._load_data_repository()

    # ── Top-level layout ───────────────────────────────────────────────────

    def _build_ui(self):
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(4, 4, 4, 4)
        root_lay.setSpacing(2)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_lay.addWidget(splitter, stretch=1)

        # Sidebar
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFixedWidth(310)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Always there, so the panel's inner width never changes: a scrollbar
        # that came and went with the height of the INFO text reflowed the
        # whole sidebar each time.
        sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._sidebar_widget = QWidget()
        sidebar_scroll.setWidget(self._sidebar_widget)
        # INFO sits under the scrolling part, pinned to the window's bottom
        # edge: inside the scroll area it fell below the fold whenever the
        # sidebar was taller than the window, and the status went unseen.
        sidebar_col = QWidget()
        sidebar_col.setFixedWidth(310)
        _col_lay = QVBoxLayout(sidebar_col)
        _col_lay.setContentsMargins(0, 0, 0, 0)
        _col_lay.setSpacing(0)
        _col_lay.addWidget(sidebar_scroll, 1)
        self._sidebar_info = QWidget()
        _col_lay.addWidget(self._sidebar_info, 0)
        splitter.addWidget(sidebar_col)

        # Main notebook
        self._notebook = QTabWidget()
        splitter.addWidget(self._notebook)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # Tabs
        self._tab_graph    = QWidget()
        self._tab_xy       = QWidget()
        self._tab_pv_time  = QWidget()
        self._tab_table    = QWidget()
        self._tab_log      = QWidget()

        self._notebook.addTab(self._tab_graph,   "  Graph  ")
        self._notebook.addTab(self._tab_xy,      "  XY Plot  ")
        self._notebook.addTab(self._tab_pv_time, "  PV Time Plot  ")
        self._notebook.addTab(self._tab_table,   "  Table  ")
        self._notebook.addTab(self._tab_log,     "  Log  ")

        self._build_sidebar()
        self._build_graph_tab()
        self._build_xy_tab()
        self._build_pv_time_tab()
        self._build_table_tab()
        self._build_log_tab()

        # The PV Time tab offers the channels that are in the PV list right now,
        # and the list changes while the user is looking at another tab, so its
        # drop-downs are refilled the moment that tab comes to the front.
        self._notebook.currentChanged.connect(self._on_main_tab_changed)
        # The status line is in the sidebar's INFO section (_build_sidebar).

    def _on_main_tab_changed(self, idx):
        if self._notebook.widget(idx) is self._tab_pv_time:
            self._refresh_pv_time_choices()
            # Opened for the first time, or something changed while it was
            # hidden: draw without waiting for a click.
            self._pv_time_auto_ready = True
            QTimer.singleShot(0, self._on_notebook_tab_changed_pv_time)
        elif self._notebook.widget(idx) is self._tab_xy:
            # Live ticks and condition changes only redraw the XY cloud while it
            # is the visible tab; a hidden one is marked and caught up here, so
            # what appears on screen is never yesterday's picture.
            if getattr(self, "_xy_dirty", False):
                try:
                    self._xy_update_points()
                except Exception:
                    import traceback
                    self._log(f"[xy_refresh]\n{traceback.format_exc()}")
                    self._xy_dirty = False

    # ── Sidebar ────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        bar = QVBoxLayout(self._sidebar_widget)
        bar.setContentsMargins(6, 6, 6, 6)
        bar.setSpacing(4)

        h = QLabel("TIME WINDOW")
        h.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h)

        # The two things that decide WHICH stretch of time is shown sit side by
        # side, same size: pick a window, or follow "now". What they set is
        # spelled out underneath.
        tw_row = QHBoxLayout()
        tw_row.setSpacing(6)
        btn_tw = QPushButton("\U0001f4c5 Time window")
        btn_tw.setFixedHeight(_TW_ROW_BTN_H)
        btn_tw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_tw.setToolTip("Pick the start and end of the period to show")
        btn_tw.clicked.connect(self._open_time_window_dialog)
        tw_row.addWidget(btn_tw, 1)

        # There is no "Load data" button: data is fetched on its own whenever
        # the channels, the time window or the preset change, so the only thing
        # left to decide is whether the view keeps following "now".
        self._btn_live = QPushButton("⏵ Live mode")
        self._btn_live.setFixedHeight(_TW_ROW_BTN_H)
        self._btn_live.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_live.clicked.connect(self._toggle_live_mode)
        self._btn_live.setToolTip(
            "Keep following new values as they arrive. Off = the chosen time "
            "window stays put. Data is loaded automatically either way.")
        self._btn_live.setStyleSheet(_LIVE_BTN_OFF_STYLE)
        tw_row.addWidget(self._btn_live, 1)
        bar.addLayout(tw_row)

        tw_col = QVBoxLayout()
        tw_col.setContentsMargins(2, 2, 0, 0)
        tw_col.setSpacing(1)
        self._lbl_tw_from = QLabel("From: --")
        self._lbl_tw_from.setStyleSheet("color:#333;font-size:11px;")
        self._lbl_tw_to   = QLabel("To:   --")
        self._lbl_tw_to.setStyleSheet("color:#333;font-size:11px;")
        self._lbl_tw_live = QLabel("Live: OFF")
        self._lbl_tw_live.setStyleSheet("color:#555;font-size:11px;")
        tw_col.addWidget(self._lbl_tw_from)
        tw_col.addWidget(self._lbl_tw_to)
        tw_col.addWidget(self._lbl_tw_live)
        bar.addLayout(tw_col)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(sep2)

        h2 = QLabel("PRESETS")
        h2.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h2)

        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preset_combo.currentIndexChanged.connect(self._load_preset)
        # A word, not a bare ✕: the small red cross was not read as "delete".
        btn_del_preset = QPushButton("Delete")
        btn_del_preset.setToolTip("Delete the selected preset")
        btn_del_preset.setStyleSheet(
            "QPushButton{background:#B71C1C;color:white;border-radius:3px;"
            "padding:3px 8px;font-weight:600;}"
            "QPushButton:hover{background:#7F0000;}")
        btn_del_preset.clicked.connect(self._delete_preset)
        preset_row.addWidget(self._preset_combo)
        preset_row.addWidget(btn_del_preset)
        bar.addLayout(preset_row)

        preset_btns = QHBoxLayout()
        b_load = QPushButton("Load"); b_load.setStyleSheet(_BTN_PRIMARY)
        b_load.clicked.connect(self._load_preset)
        b_save = QPushButton("Save"); b_save.clicked.connect(self._save_preset)
        b_new  = QPushButton("Save as new"); b_new.clicked.connect(self._save_preset_as)
        preset_btns.addWidget(b_load); preset_btns.addWidget(b_save); preset_btns.addWidget(b_new)
        bar.addLayout(preset_btns)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(sep3)

        h3 = QLabel("PV LIST")
        h3.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h3)

        self._pv_list = QListWidget()
        # The ink is stated with the paper: this box is painted white, so its
        # text colour is set here too instead of being inherited from whatever
        # is in force. Only the widget colour, never ::item — the derived (ƒ)
        # rows paint their own foreground and background from code, and an
        # ::item rule would override those and flatten them.
        self._pv_list.setStyleSheet(
            "QListWidget{background:#ffffff;color:#222222;"
            "border:1px solid #b0b0b0;border-radius:3px;}")
        self._pv_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._pv_list.setMinimumHeight(120)
        self._pv_list.setMaximumHeight(360)
        # PVs are put in order by dragging them (or Ctrl+Up / Ctrl+Down); the
        # graph's bands and the table's columns follow.
        self._pv_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._pv_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._pv_list.model().rowsMoved.connect(
            lambda *_: QTimer.singleShot(0, self._on_pv_list_dropped))
        # Rows show the channel's NAME (the one the graph and the PV table use);
        # the full PV is the tooltip. A long PV never needs a sideways scroll.
        self._pv_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._pv_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pv_list.itemDoubleClicked.connect(self._on_pv_double_click)
        self._pv_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._pv_list.customContextMenuRequested.connect(self._open_pv_list_menu)
        for key, slot in (("F2", self._rename_current_pv),
                          ("Delete", self._remove_selected_pvs),
                          ("Ctrl+Up", lambda: self._move_selected_pvs(-1)),
                          ("Ctrl+Down", lambda: self._move_selected_pvs(1)),
                          ("Ctrl+C", self._copy_selected_pv_names),
                          ("Ctrl+V", self._paste_pv_names)):
            sc = QShortcut(QKeySequence(key), self._pv_list)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(slot)
        bar.addWidget(self._pv_list)

        pv_btns = QHBoxLayout()
        pv_btns.setSpacing(4)
        self._btn_browse = QPushButton("+ Add PV")
        self._btn_browse.setStyleSheet(_BTN_SUCCESS)
        self._btn_browse.setToolTip("Find archiver PVs and add them to the list")
        self._btn_browse.clicked.connect(self._open_pv_browser)
        # The one place custom PVs are defined — the tab rows no longer carry
        # their own copy of this button.
        btn_add_custom = QPushButton("Custom PV")
        btn_add_custom.setToolTip("Define, edit or delete PVs calculated from "
                                  "other PVs with a formula (the ƒ rows)")
        btn_add_custom.clicked.connect(self._open_custom_pv_dialog)
        btn_remove = QPushButton("✕ Remove")
        btn_remove.setToolTip("Remove the selected PVs (Delete key)")
        btn_remove.clicked.connect(self._remove_selected_pvs)
        btn_clear  = QPushButton()
        btn_clear.setIcon(_bin_icon())
        btn_clear.setFixedWidth(32)
        btn_clear.setToolTip("Remove every PV from the list")
        btn_clear.clicked.connect(self._clear_pv_list)
        pv_btns.addWidget(self._btn_browse)
        pv_btns.addWidget(btn_add_custom)
        pv_btns.addWidget(btn_remove)
        pv_btns.addWidget(btn_clear)
        bar.addLayout(pv_btns)

        self._lbl_pv_count = QLabel("0 PVs")
        self._lbl_pv_count.setStyleSheet("color:#777;font-size:10px;")
        self._lbl_pv_count.setToolTip(
            "Double-click or F2 renames a PV · drag to reorder · Ctrl+C / "
            "Ctrl+V copy and paste PV names · right-click for more")
        bar.addWidget(self._lbl_pv_count)

        sep_m = QFrame(); sep_m.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(sep_m)

        # The ramp: one waveplate PV ("master") marks the steps. It drives three
        # things, in this order — rows where only the waveplate moved are
        # dropped, "Keep steps every" keeps the rows on whole steps, and the
        # dead-shot rule leaves zero-energy shots out of the statistics.
        h_m = QLabel("RAMP / WAVEPLATE")
        h_m.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h_m)
        n_m = QLabel("The waveplate position marks the steps of a ramp.")
        n_m.setWordWrap(True)
        n_m.setStyleSheet("color:#555555;")
        bar.addWidget(n_m)

        mpv_row = QHBoxLayout()
        mpv_row.addWidget(QLabel("Waveplate PV:"))
        self._master_pv_edit = QLineEdit(self._master_pv)
        self._master_pv_edit.setPlaceholderText(MASTER_RAMP_PV)
        self._master_pv_edit.setToolTip(
            "The waveplate (ramp master) PV. A row where only this PV changed "
            "is dropped; Keep steps every and the dead-shot rule read it too. "
            "Empty = " + MASTER_RAMP_PV)
        mpv_row.addWidget(self._master_pv_edit)
        bar.addLayout(mpv_row)

        mm_row = QHBoxLayout()
        mm_row.addWidget(QLabel("Keep steps every:"))
        self._master_multiple_edit = QLineEdit(self._master_multiple)
        self._master_multiple_edit.setFixedWidth(70)
        self._master_multiple_edit.setPlaceholderText("all rows")
        self._master_multiple_edit.setToolTip(
            "Keep only the rows where the waveplate PV stands on a multiple of "
            f"this number ({MASTER_STEP_DEFAULT} = every whole step of "
            f"{MASTER_STEP_DEFAULT}). Applies to the graph, the table, the XY "
            "plot, the statistics and the export. Empty = keep every row. When "
            "no row is on a step, every row is kept and the Log says so.")
        mm_row.addWidget(self._master_multiple_edit)
        mm_row.addStretch()
        bar.addLayout(mm_row)

        # Dead shots: the waveplate did not move but the energy fell to ~0 —
        # a trigger shot or a dropout, not a measurement. Ticked = they are
        # left out of the selection statistics; unticked = counted like any
        # other shot, for when the trigger shots are what is being looked at.
        self._chk_dead_shot = QCheckBox("Leave dead shots out of statistics")
        self._chk_dead_shot.setStyleSheet(_CHK_STYLE)
        self._chk_dead_shot.setChecked(self._dead_shot_on)
        _dead_tip = ("A dead shot: the energy below is under the limit while the "
                     "waveplate stayed where it was on the shot before — a "
                     "trigger shot or a dropout. Ticked, those shots are left out "
                     "of the Selection statistics only; the graph and the table "
                     "still show them. Untick to count them too.")
        self._chk_dead_shot.setToolTip(_dead_tip)
        bar.addWidget(self._chk_dead_shot)
        de_row = QHBoxLayout()
        de_row.setContentsMargins(18, 0, 0, 0)
        de_row.addWidget(QLabel("Energy:"))
        self._dead_energy_edit = QLineEdit(self._dead_energy_pv)
        self._dead_energy_edit.setToolTip(
            "Energy channel the dead-shot rule reads. It must be in the PV "
            "list, and so must the waveplate PV.")
        de_row.addWidget(self._dead_energy_edit, 1)
        de_row.addWidget(QLabel("below"))
        self._dead_energy_spin = QDoubleSpinBox()
        self._dead_energy_spin.setDecimals(3)
        self._dead_energy_spin.setRange(0.0, 1e6)
        self._dead_energy_spin.setSingleStep(0.01)
        self._dead_energy_spin.setValue(self._dead_energy_thr)
        self._dead_energy_spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._dead_energy_spin.setFixedWidth(58)
        self._dead_energy_spin.setToolTip("An energy below this counts as zero")
        de_row.addWidget(self._dead_energy_spin)
        bar.addLayout(de_row)

        def _dead_changed(*_):
            self._dead_shot_on = self._chk_dead_shot.isChecked()
            self._dead_energy_pv = self._dead_energy_edit.text().strip()
            self._dead_energy_thr = float(self._dead_energy_spin.value())
            self._dead_energy_edit.setEnabled(self._dead_shot_on)
            self._dead_energy_spin.setEnabled(self._dead_shot_on)
            if self._sel_range:
                self._recompute_stats()
        self._chk_dead_shot.toggled.connect(_dead_changed)
        self._dead_energy_edit.editingFinished.connect(_dead_changed)
        self._dead_energy_spin.valueChanged.connect(_dead_changed)
        self._dead_energy_edit.setEnabled(self._dead_shot_on)
        self._dead_energy_spin.setEnabled(self._dead_shot_on)
        # A long PV name in a narrow box shows its start, not a cut-off tail.
        self._dead_energy_edit.setCursorPosition(0)
        self._master_pv_edit.setCursorPosition(0)

        # A new step or waveplate takes effect at once, not at the next load.
        self._last_step_txt = self._master_multiple_edit.text().strip()
        self._last_master_txt = self._master_pv_edit.text().strip()
        self._master_multiple_edit.editingFinished.connect(self._on_step_edited)
        self._master_pv_edit.editingFinished.connect(self._on_master_pv_edited)

        def _section(title):
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
            bar.addWidget(sep)
            hdr = QLabel(title)
            hdr.setStyleSheet("font-weight:700;color:#1565C0;")
            bar.addWidget(hdr)

        # ── Info: what the program is doing right now ─────────────────────
        # Used to be a grey line under the whole window; here it sits next to
        # the progress bar it talks about, and wraps instead of running off.
        # It lives in its own holder under the scrolling sidebar, so it is
        # always on screen at the bottom of the window.
        info_lay = QVBoxLayout(self._sidebar_info)
        info_lay.setContentsMargins(6, 0, 6, 6)
        info_lay.setSpacing(4)
        _info_sep = QFrame(); _info_sep.setFrameShape(QFrame.Shape.HLine)
        info_lay.addWidget(_info_sep)
        _info_hdr = QLabel("INFO")
        _info_hdr.setStyleSheet("font-weight:700;color:#1565C0;")
        info_lay.addWidget(_info_hdr)
        # Fixed width AND fixed height: the status changes several times a
        # second while loading, and a box that grew and shrank with it moved
        # every control below it (and a long PV name widened the sidebar).
        # A longer message ends in "…"; the whole of it is the tooltip.
        self._lbl_status = _SidebarText("Ready.", max_lines=4)
        self._lbl_status.setStyleSheet(
            "QLabel{background:#ffffff;color:#222222;border:1px solid #c8c8c8;"
            "border-radius:3px;padding:3px 5px;font-size:11px;}")
        info_lay.addWidget(self._lbl_status)
        # Graph-specific notes (zoom mode, "load data first", …) — formerly the
        # grey text at the top right of the graph. Hidden while empty.
        self._lbl_graph_info = _InfoLabel("")
        self._lbl_graph_info.setWordWrap(True)
        self._lbl_graph_info.setStyleSheet("color:#444444;font-size:11px;padding:0 2px;")
        self._lbl_graph_info.hide()
        info_lay.addWidget(self._lbl_graph_info)
        # Custom PVs that cannot be computed right now, and why.
        self._lbl_cpv_info = _InfoLabel("")
        self._lbl_cpv_info.setWordWrap(True)
        self._lbl_cpv_info.setStyleSheet(
            "QLabel{background:#FFF3E0;color:#3E2723;border:1px solid #E65100;"
            "border-radius:3px;padding:3px 5px;font-size:11px;}")
        self._lbl_cpv_info.hide()
        info_lay.addWidget(self._lbl_cpv_info)

        # ── Progress bar ───────────────────────────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v %")
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #bbb;border-radius:3px;"
            "background:#eee;color:#111;text-align:center;font-size:10px;}"
            "QProgressBar::chunk{background:#81C784;border-radius:2px;}")
        self._progress_bar.hide()
        info_lay.addWidget(self._progress_bar)

        # Stop: a long period is loaded newest-first and painted as it arrives,
        # so the user must be able to say "that is enough" and keep what is
        # already on screen instead of waiting the whole period out.
        self._btn_stop_load = QPushButton("Stop loading")
        self._btn_stop_load.setStyleSheet(
            "QPushButton{background:#FFF3E0;color:#111;border:1px solid #E65100;"
            "border-radius:3px;padding:2px 6px;font-size:11px;}"
            "QPushButton:hover{background:#FFE0B2;}")
        self._btn_stop_load.clicked.connect(self._stop_loading)
        self._btn_stop_load.hide()
        info_lay.addWidget(self._btn_stop_load)

        # ── The controls that used to sit in a row above the graph ────────
        # Grouped by what they act on. Conditions serve the Graph, XY and Table
        # tabs alike, so a place outside any one tab is where they belong.
        def _grid(*buttons):
            g = QGridLayout()
            g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(4)
            g.setVerticalSpacing(4)
            g.setColumnStretch(0, 1)
            g.setColumnStretch(1, 1)
            for i, b in enumerate(buttons):
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                g.addWidget(b, i // 2, i % 2)
            bar.addLayout(g)

        _section("DATA FILTER")
        b_cond = QPushButton("Conditions")
        b_cond.setToolTip("Keep only the rows where chosen signals are within limits")
        b_cond.clicked.connect(self._open_conditions_dialog)
        b_cross = QPushButton("Limit crossings")
        b_cross.setToolTip("Every time a signal went above (or below) one of its "
                           "reference lines: when, how long, how far")
        b_cross.clicked.connect(self._open_limit_crossings)
        _grid(b_cond, b_cross)
        # One switch for all conditions: off shows everything again (trigger
        # shots, ramps) without losing the condition rows.
        self._chk_cond_on = QCheckBox("Apply conditions")
        self._chk_cond_on.setStyleSheet(_CHK_STYLE)
        self._chk_cond_on.setChecked(self._conditions_on)
        self._chk_cond_on.toggled.connect(self._set_conditions_on)
        bar.addWidget(self._chk_cond_on)
        self._sync_conditions_tick()
        # A live selection starts again at the first shot after a firing pause.
        p_row = QHBoxLayout()
        p_row.setContentsMargins(0, 0, 0, 0)
        self._chk_sel_pause = QCheckBox("Restart after pause >")
        self._chk_sel_pause.setStyleSheet(_CHK_STYLE)
        self._chk_sel_pause.setChecked(self._sel_pause_on)
        self._chk_sel_pause.setToolTip(
            "The selection statistics count only from the first shot after the "
            "newest pause in firing longer than this. A live selection (dragged "
            "out past the right edge) moves its start there.")
        self._spin_sel_pause = QSpinBox()
        self._spin_sel_pause.setRange(1, 3600)
        self._spin_sel_pause.setSuffix(" s")
        self._spin_sel_pause.setValue(int(round(self._sel_pause_s)))
        self._spin_sel_pause.setToolTip(self._chk_sel_pause.toolTip())

        def _pause_changed(*_):
            self._sel_pause_on = self._chk_sel_pause.isChecked()
            self._sel_pause_s = float(self._spin_sel_pause.value())
            if self._sel_range:
                self._recompute_stats()
        self._chk_sel_pause.toggled.connect(_pause_changed)
        self._spin_sel_pause.valueChanged.connect(_pause_changed)
        p_row.addWidget(self._chk_sel_pause)
        p_row.addWidget(self._spin_sel_pause)
        p_row.addStretch()
        bar.addLayout(p_row)

        _section("GRAPH")
        b_ref = QPushButton("Reference lines")
        b_ref.clicked.connect(self._open_ref_lines_dialog)
        b_gset = QPushButton("Graph settings")
        b_gset.setToolTip("Fonts, axis-column spacing, time stamps, plot margins, "
                          "cursor readouts")
        b_gset.clicked.connect(self._open_graph_settings_dialog)
        self._btn_graph_view = QPushButton("View ▾")
        self._btn_graph_view.setToolTip(
            "Reset the view, set axis limits by hand, hide all grids")
        self._btn_graph_view.clicked.connect(self._open_graph_view_menu)
        b_clean = QPushButton("Clean graph")
        b_clean.setToolTip("Remove the plotted points from the graph")
        b_clean.clicked.connect(self._clean_graph)
        _grid(b_ref, b_gset, self._btn_graph_view, b_clean)

        num_row = QGridLayout()
        num_row.setContentsMargins(0, 2, 0, 0)
        num_row.setHorizontalSpacing(4)
        num_row.addWidget(QLabel("Font:"), 0, 0)
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(5, 24)
        self._font_size_spin.setValue(int(self._graph_opts.get("font_size", 11)))
        self._font_size_spin.setFixedWidth(100)   # same as Detail below
        self._font_size_spin.valueChanged.connect(self._apply_font_size)
        num_row.addWidget(self._font_size_spin, 0, 1)
        num_row.addWidget(QLabel("pt"), 0, 2)

        # How finely a period is read: at most N points per PV, each keeping
        # the lowest and highest value of its stretch (the band), so no spike
        # is lost. 0 = every sample as archived. Was called "Avg to", which
        # read as averaging — it never was (12.10.2026).
        num_row.addWidget(QLabel("Detail:"), 1, 0)
        self._avg_target_spin = QSpinBox()
        # Above ~20 000 a PV gains nothing visible, only a slower load and
        # bigger days in the day cache.
        self._avg_target_spin.setRange(0, 20000)
        # "all" — the row then reads "Detail: all points per PV" ("all
        # samples" did not fit the box).
        self._avg_target_spin.setSpecialValueText("all")
        self._avg_target_spin.setValue(int(self.config.get("avg_target_points", 2000)))
        self._avg_target_spin.setSingleStep(500)
        # 100 px: at 90 the five digits of 20000 were cut by the arrows.
        self._avg_target_spin.setFixedWidth(100)
        self._avg_target_spin.setToolTip(
            "How many points per PV to read for the period. Spikes are never "
            "lost: each point keeps the highest and lowest value of its "
            "stretch. More = finer, slower. 'all' = every sample as "
            "archived (slow for long periods). Zooming in reads finer detail "
            "by itself.")
        self._avg_target_spin.valueChanged.connect(self._on_avg_target_changed)
        num_row.addWidget(self._avg_target_spin, 1, 1)
        num_row.addWidget(QLabel("points per PV"), 1, 2)
        num_row.setColumnStretch(3, 1)
        bar.addLayout(num_row)

        # The PV table under the graph: its columns and its whole look.
        _section("PV TABLE")
        b_reset_cols = QPushButton("Reset columns")
        b_reset_cols.setToolTip("Put the columns back in their original order, width "
                                "and selection.\nDrag a column heading to move it; "
                                "right-click the headings to show or hide columns.")
        b_reset_cols.clicked.connect(self._reset_axis_columns)
        b_styles = QPushButton("Styles")
        b_styles.setToolTip("Save or load the whole look of the graph: colours, line "
                            "and point styles, reference lines and the column layout.")
        b_styles.setMenu(self._build_styles_menu(b_styles))
        _grid(b_reset_cols, b_styles)
        # Which saved look is on the graph now. Wraps rather than cutting a
        # long name short.
        self._lbl_style_current = QLabel()
        self._lbl_style_current.setWordWrap(True)
        self._lbl_style_current.setStyleSheet("color:#222222;font-size:11px;")
        bar.addWidget(self._lbl_style_current)
        self._show_style_current()

        _section("SELECTION")
        self._btn_clear_sel = QPushButton("Clear selection")
        self._btn_clear_sel.setToolTip(
            "Remove the blue selected region and its statistics")
        self._btn_clear_sel.clicked.connect(self._clear_selection)
        _grid(self._btn_clear_sel)

        bar.addStretch()
        self._refresh_preset_combo()
    def resizeEvent(self, ev):
        # The cap on the PV list height follows the window height.
        super().resizeEvent(ev)
        try:
            self._autosize_axis_pane()
        except Exception:
            pass

    # ── Graph tab ──────────────────────────────────────────────────────────

    def _build_graph_tab(self):
        tab = self._tab_graph
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        # The row of controls that used to sit here (Conditions, Reference lines,
        # View, Font, Detail, …) lives in the sidebar now — see _build_sidebar.

        # Graph tool strip (view history, pan/zoom/loupe, margins, save). It is
        # built once and stays; the matplotlib toolbar behind it is tied to one
        # canvas and rebuilt on every redraw, so it lives in a HIDDEN holder and
        # only serves as the engine the strip's buttons drive.
        self._graph_strip = self._build_graph_strip()
        lay.addWidget(self._graph_strip)
        self._graph_tb_holder = QWidget()
        _tb_lay = QHBoxLayout(self._graph_tb_holder)
        _tb_lay.setContentsMargins(0, 0, 0, 0)
        _tb_lay.setSpacing(0)
        self._graph_tb_holder.hide()
        lay.addWidget(self._graph_tb_holder)

        # Vertical splitter: graph canvas (top) + axis settings (bottom).
        # A wide, clearly-shaded handle tells the user where to grab to resize.
        self._graph_v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._graph_v_splitter.setHandleWidth(8)
        self._graph_v_splitter.setStyleSheet(
            "QSplitter::handle{background:#c8c8c8;"
            "border-top:1px solid #8a8a8a;border-bottom:1px solid #8a8a8a;}"
            "QSplitter::handle:hover{background:#1565C0;}")
        lay.addWidget(self._graph_v_splitter, stretch=1)

        # Top pane: canvas container + stats strip
        top_pane = QWidget()
        top_lay  = QVBoxLayout(top_pane)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(0)

        # Step-by-step guide, shown only while a reference line is being placed
        # by clicking. It says what the next click does and offers a way out.
        self._pick_bar = QWidget()
        pick_lay = QHBoxLayout(self._pick_bar)
        pick_lay.setContentsMargins(8, 4, 8, 4)
        # A plain QWidget only paints a stylesheet background with this attribute.
        self._pick_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._pick_bar.setStyleSheet("background:#1565C0;")
        self._lbl_pick_hint = QLabel("")
        self._lbl_pick_hint.setStyleSheet(
            "background:transparent;color:#ffffff;font-weight:700;")
        self._lbl_pick_hint.setWordWrap(True)
        pick_lay.addWidget(self._lbl_pick_hint, stretch=1)
        b_pick_cancel = QPushButton("Cancel")
        b_pick_cancel.setStyleSheet(
            "QPushButton{background:#ffffff;color:#0D47A1;border:1px solid #ffffff;"
            "border-radius:3px;padding:3px 10px;font-weight:700;}"
            "QPushButton:hover{background:#BBDEFB;}")
        b_pick_cancel.clicked.connect(self._cancel_ref_pick)
        pick_lay.addWidget(b_pick_cancel)
        self._pick_bar.setVisible(False)
        top_lay.addWidget(self._pick_bar)

        # Visible border around the graph area so its extent is obvious.
        self._graph_container = QWidget()
        self._graph_container.setStyleSheet(
            "background:#ffffff;border:1px solid #9e9e9e;")
        gc_lay = QVBoxLayout(self._graph_container)
        gc_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.addWidget(self._graph_container, stretch=1)

        # Selection statistics strip: appears under the graph when the user
        # drags out a region (left-drag) on the plot. One compact card per PV,
        # laid out side by side and wrapping onto new rows so nothing spills
        # past the panel edge; the whole strip scrolls if it gets too tall.
        # Heading row: the title on the left, the All / One row / Hide switch on
        # the right. The strip itself is exactly as tall as its cards (see
        # _fit_stats_strip) — no dead band under them.
        self._stats_head = QWidget()
        sh_lay = QHBoxLayout(self._stats_head)
        sh_lay.setContentsMargins(0, 0, 4, 0)
        sh_lay.setSpacing(2)
        self._stats_title = QLabel("")
        self._stats_title.setStyleSheet(
            "font-size:10px;font-weight:700;color:#1565C0;padding:2px 6px 0 6px;")
        self._stats_title.hide()
        sh_lay.addWidget(self._stats_title, stretch=1)
        # The selection's two edges, to type or step one shot at a time — on a
        # two-hour graph a pixel is several seconds, too coarse to put an edge
        # exactly on the first shot after a short pause by dragging.
        _edge_css = ("QDateTimeEdit{background:#ffffff;color:#111;border:1px solid "
                     "#b6b6b6;border-radius:3px;padding:0 2px;font-size:10px;}")
        _step_css = ("QToolButton{background:#efefef;color:#111;border:1px solid "
                     "#b6b6b6;border-radius:3px;padding:0px 4px;font-size:10px;}"
                     "QToolButton:hover{background:#dce9fb;border-color:#8fb4de;}")
        self._sel_edge_edits = {}
        for edge, label in (("start", "Start"), ("end", "End")):
            lbl = QLabel(label + ":")
            lbl.setStyleSheet("font-size:10px;color:#333;padding-left:6px;")
            sh_lay.addWidget(lbl)
            b_prev = QToolButton(); b_prev.setText("-1")
            b_prev.setToolTip(f"Move the {label.lower()} one shot earlier")
            b_prev.setStyleSheet(_step_css)
            b_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b_prev.clicked.connect(lambda _c=False, e=edge: self._step_sel_edge(e, -1))
            ed = QDateTimeEdit()
            ed.setDisplayFormat("HH:mm:ss")
            ed.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            ed.setFixedWidth(62)
            ed.setStyleSheet(_edge_css)
            ed.setToolTip(f"Selection {label.lower()} — type a time and press Enter")
            ed.editingFinished.connect(lambda e=edge: self._on_sel_edge_typed(e))
            b_next = QToolButton(); b_next.setText("+1")
            b_next.setToolTip(f"Move the {label.lower()} one shot later")
            b_next.setStyleSheet(_step_css)
            b_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b_next.clicked.connect(lambda _c=False, e=edge: self._step_sel_edge(e, 1))
            for w in (b_prev, ed, b_next):
                sh_lay.addWidget(w)
            self._sel_edge_edits[edge] = ed
        sh_lay.addSpacing(8)
        self._stats_mode = str(self.config.get("stats_strip_mode", "all"))
        if self._stats_mode not in ("all", "row", "hide"):
            self._stats_mode = "all"
        self._stats_mode_btns = {}
        for key, text, tip in (
                ("all", "All", "Show every statistics card"),
                ("row", "One row", "Show only the first row of cards; scroll for "
                                   "the rest"),
                ("hide", "Hide", "Fold the cards away, keep only this heading")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setChecked(key == self._stats_mode)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(
                "QToolButton{background:#efefef;color:#111;border:1px solid #b6b6b6;"
                "border-radius:3px;padding:0px 6px;font-size:10px;}"
                "QToolButton:hover{background:#dce9fb;border-color:#8fb4de;}"
                "QToolButton:checked{background:#2f6fb5;color:#ffffff;"
                "border-color:#255a94;}")
            b.clicked.connect(lambda _c=False, k=key: self._set_stats_mode(k))
            self._stats_mode_btns[key] = b
            sh_lay.addWidget(b)
        self._stats_head.hide()
        top_lay.addWidget(self._stats_head)

        self._stats_scroll = QScrollArea()
        self._stats_scroll.setWidgetResizable(True)
        self._stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._stats_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._stats_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._stats_container = QWidget()
        self._stats_flow = _FlowLayout(self._stats_container, margin=4, spacing=6)
        self._stats_scroll.setWidget(self._stats_container)
        self._stats_scroll.hide()
        top_lay.addWidget(self._stats_scroll)
        self._stats_top_pane = top_pane
        # Re-fit when the window gets wider or narrower: cards re-wrap.
        self._stats_resize_hook = _ResizeHook(top_pane, self._fit_stats_strip)

        self._graph_v_splitter.addWidget(top_pane)

        # Bottom pane: axis settings table
        axis_pane = QWidget()
        self._graph_axis_pane = axis_pane
        self._build_axis_settings_panel(axis_pane)
        self._graph_v_splitter.addWidget(axis_pane)
        # Give all spare vertical space to the graph; keep the table compact and
        # docked right beneath it (no dead gap between graph and table).
        self._graph_v_splitter.setStretchFactor(0, 1)
        self._graph_v_splitter.setStretchFactor(1, 0)
        # Roughly 3:1 — the graph keeps three quarters, the PV list opens on a few
        # rows. _autosize_axis_pane refines it once the real height is known.
        self._graph_v_splitter.setSizes([1000, 330])
        # A drag of the handle is the user's decision and outranks the autosizer
        # from then on; without this the next resize or table refresh undid it.
        self._graph_v_splitter.splitterMoved.connect(self._on_graph_splitter_moved)

    # ── Axis settings panel ────────────────────────────────────────────────

    # Heading text and starting width of every column of the PV list. Both are
    # also what the "Reset columns" button restores.
    _AXIS_COL_HEADS = {
        "show":         "Show", "pv": "PV", "display_name": "Display Name",
        "color":        "Color", "cursor_val": "Cursor",
        "ymin": "Y min", "ymax": "Y max",
        "auto_scale":   "Autoscale", "width": "Width",
        "style":        "Style", "marker": "Points", "marker_size": "Size",
        "alpha":        "Alpha %",
        "smooth":       "Smooth", "grid": "Grid",
        "unit":         "Unit", "last": "Last", "min": "Min", "max": "Max",
        "mean":         "Mean", "count": "Count",
        "blank":        "",
    }
    _AXIS_COL_WIDTHS = {
        "show":40, "pv":270, "display_name":130, "color":40,
        "cursor_val":90, "ymin":55, "ymax":55,
        "auto_scale":70, "width":45,
        "style":80, "marker":60, "marker_size":45, "alpha":55,
        "smooth":50, "grid":35,
        "unit":55, "last":80, "min":80, "max":80, "mean":80, "count":60,
        "blank":0,
    }

    def _build_axis_settings_panel(self, parent: QWidget):
        lay = QVBoxLayout(parent)
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(2)
        # Reset columns / Styles live in the sidebar's PV TABLE section; the
        # table starts right under the splitter handle.

        _COLS   = list(self._axis_tv_cols)
        _HEADS  = self._AXIS_COL_HEADS
        _COL_W  = self._AXIS_COL_WIDTHS

        self._axis_tv = QTableWidget(0, len(_COLS))
        self._axis_tv.setHorizontalHeaderLabels([_HEADS[c] for c in _COLS])
        self._axis_tv.verticalHeader().setVisible(False)
        self._axis_tv.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._axis_tv.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                       QAbstractItemView.EditTrigger.SelectedClicked)
        # White background (incl. the empty area beneath the rows) and let the
        # table fill the pane so no dead gap opens between the heading and it.
        # Lighter-blue row selection (the default palette Highlight is a strong
        # #1565C0 that reads as "sytě modrá"); dark text keeps it legible.
        self._axis_tv.setStyleSheet(
            "QTableWidget{background:#ffffff;}"
            "QTableWidget QTableCornerButton::section{background:#ffffff;}"
            "QTableWidget::item:selected{background:#BBDEFB;color:#0D47A1;}")
        # The table fills its pane; how tall that pane is follows the number of
        # PV rows (see _autosize_axis_pane).
        self._axis_tv.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # With this many columns a small screen cannot show them all — let the
        # table slide sideways instead of squeezing everything to nothing. The
        # blank spacer collapses first, so the bar only shows up when it is
        # really needed (_autosize_axis_pane_now already reserves its height).
        self._axis_tv.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._axis_tv.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hdr = self._axis_tv.horizontalHeader()
        # Column headings can be dragged into any order; "Reset columns" undoes it.
        hdr.setSectionsMovable(True)
        hdr.setFirstSectionMovable(True)
        self._axis_drop_marker = _HeaderDropMarker(self._axis_tv)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._open_axis_column_menu)
        self._apply_default_axis_columns()

        # Rows can be dragged into the user's own order; the graph follows.
        self._axis_row_drag = _RowDragger(
            self._axis_tv,
            lambda r: 0 <= r < len(getattr(self, "_axis_row_pv", []))
            and self._axis_row_pv[r] is not None,
            self._on_axis_rows_dropped)

        self._axis_tv.cellDoubleClicked.connect(self._on_axis_tv_double_click)
        # Second click on an already-selected row clears the highlight (toggle).
        self._axis_tv.clicked.connect(self._on_axis_tv_clicked)
        # Auto-apply: any edit takes effect immediately (no Apply button).
        self._axis_tv.itemChanged.connect(self._on_axis_item_changed)
        lay.addWidget(self._axis_tv)

    def _apply_default_axis_columns(self):
        """Widths, resize behaviour, cell editors and the default set of visible
        columns. Called once when the table is built and again by "Reset columns"."""
        _COLS = list(self._axis_tv_cols)
        hdr   = self._axis_tv.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # PV + Display Name auto-fit their content so the full text is always visible.
        hdr.setSectionResizeMode(_COLS.index("pv"),
                                 QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COLS.index("display_name"),
                                 QHeaderView.ResizeMode.ResizeToContents)
        # Trailing blank column absorbs leftover width and collapses when space is tight.
        hdr.setSectionResizeMode(_COLS.index("blank"),
                                 QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(True)

        for i, col in enumerate(_COLS):
            self._axis_tv.setColumnWidth(i, self._AXIS_COL_WIDTHS.get(col, 60))
            self._axis_tv.setColumnHidden(i, col in _AXIS_HIDDEN_BY_DEFAULT)

        # Tick boxes sit in the middle of the Show / Autoscale / Grid columns.
        if getattr(self, "_check_delegate", None) is None:
            self._check_delegate = _CenteredCheckDelegate(self._axis_tv)
            for _c in ("show", "auto_scale", "grid"):
                self._axis_tv.setItemDelegateForColumn(_COLS.index(_c),
                                                       self._check_delegate)
            # Colour swatch — resolved by name, never by a literal column number.
            self._color_delegate = _ColorSwatchDelegate(self._axis_tv)
            self._color_delegate.color_changed.connect(self._on_axis_color_changed)
            self._axis_tv.setItemDelegateForColumn(_COLS.index("color"),
                                                   self._color_delegate)
            # Line style and point style are picked from a list, not typed.
            self._style_delegate = _ComboBoxDelegate(list(_LINE_STYLES), self._axis_tv)
            self._axis_tv.setItemDelegateForColumn(_COLS.index("style"),
                                                   self._style_delegate)
            self._marker_delegate = _ComboBoxDelegate(list(_MARKER_STYLES), self._axis_tv)
            self._axis_tv.setItemDelegateForColumn(_COLS.index("marker"),
                                                   self._marker_delegate)

    def _reset_axis_columns(self):
        """Put every column back where it started: original order, original
        widths, original show/hide selection."""
        hdr   = self._axis_tv.horizontalHeader()
        _COLS = list(self._axis_tv_cols)
        hdr.blockSignals(True)
        try:
            for logical in range(len(_COLS)):
                visual = hdr.visualIndex(logical)
                if visual != logical:
                    hdr.moveSection(visual, logical)
        finally:
            hdr.blockSignals(False)
        self._apply_default_axis_columns()
        self._axis_tv.horizontalScrollBar().setValue(0)
        self._autosize_axis_pane()

    # ── Saved looks ("style presets") ────────────────────────────────────────

    def _build_styles_menu(self, parent):
        menu = QMenu(parent)
        menu.aboutToShow.connect(lambda: self._fill_styles_menu(menu))
        self._fill_styles_menu(menu)
        return menu

    def _fill_styles_menu(self, menu):
        # Every entry runs after the popup has closed: a yes/no box or file
        # dialog opened while the menu still holds the mouse cannot be answered,
        # and the entry looks dead.
        def later(fn):
            return lambda: QTimer.singleShot(0, fn)
        menu.clear()
        menu.setToolTipsVisible(True)
        menu.addAction("Save current look as", later(self._save_style_preset))
        # menu.addMenu("Load") hands back a sub-menu that PySide destroys as
        # soon as this function returns — Load / Delete then opened nothing.
        # Own them explicitly and drop the previous pair (clear() leaves them).
        for old in getattr(menu, "_style_submenus", ()):
            old.deleteLater()
        load_menu = QMenu("Load", menu)
        del_menu  = QMenu("Delete", menu)
        menu._style_submenus = (load_menu, del_menu)
        menu.addMenu(load_menu)
        menu.addMenu(del_menu)
        names = sorted(self._style_presets)
        if names:
            for name in names:
                load_menu.addAction(name, later(lambda n=name: self._load_style_preset(n)))
                del_menu.addAction(name, later(lambda n=name: self._delete_style_preset(n)))
        else:
            for sub in (load_menu, del_menu):
                sub.setEnabled(False)
                sub.menuAction().setToolTip(
                    "No saved looks yet. Use “Save current look as” first.")
        menu.addSeparator()
        menu.addAction("Export to file", later(self._export_style_preset))
        menu.addAction("Import from file", later(self._import_style_preset))
        menu.addSeparator()
        menu.addAction("Reset columns", self._reset_axis_columns)

    def _current_style_payload(self):
        """Everything that makes up the current look of the graph."""
        hdr   = self._axis_tv.horizontalHeader()
        _COLS = list(self._axis_tv_cols)
        pv_styles = {}
        for pv, s in self._pv_settings.items():
            pv_styles[pv] = {k: s[k] for k in self._PV_STYLE_KEYS if k in s}
        return {
            "pv_settings": pv_styles,
            "ref_lines":   [dict(rl) for rl in self._ref_lines],
            "columns": {
                "order":  [_COLS[hdr.logicalIndex(v)] for v in range(len(_COLS))],
                "widths": {c: self._axis_tv.columnWidth(i) for i, c in enumerate(_COLS)},
                "hidden": [c for i, c in enumerate(_COLS)
                           if self._axis_tv.isColumnHidden(i)],
            },
        }

    def _apply_style_payload(self, data):
        for pv, s in (data.get("pv_settings") or {}).items():
            if pv not in self._pv_settings:
                continue                    # not loaded now — leave it in the preset
            self._pv_settings[pv].update(
                {k: v for k, v in s.items() if k in self._PV_STYLE_KEYS})
        if "ref_lines" in data:
            self._ref_lines = [dict(rl) for rl in (data.get("ref_lines") or [])]
        cols = data.get("columns") or {}
        _COLS = list(self._axis_tv_cols)
        hdr   = self._axis_tv.horizontalHeader()
        if cols:
            for col, w in (cols.get("widths") or {}).items():
                if col in _COLS:
                    self._axis_tv.setColumnWidth(_COLS.index(col), int(w))
            hidden = set(cols.get("hidden") or [])
            for i, col in enumerate(_COLS):
                self._axis_tv.setColumnHidden(
                    i, col in hidden and col not in _AXIS_ALWAYS_SHOWN)
            # Put the columns in the saved left-to-right order; any column the
            # preset does not mention keeps its place at the end.
            order = [c for c in (cols.get("order") or []) if c in _COLS]
            hdr.blockSignals(True)
            try:
                for target, col in enumerate(order):
                    logical = _COLS.index(col)
                    hdr.moveSection(hdr.visualIndex(logical), target)
            finally:
                hdr.blockSignals(False)
        self._refresh_axis_settings_tv()
        if self._samples_by_pv:
            self._plot_graph()

    def _save_style_preset(self):
        name, ok = QInputDialog.getText(self, "Save look", "Name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self._style_presets and QMessageBox.question(
                self, "Save look", f"Replace the saved look “{name}”?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._style_presets[name] = self._current_style_payload()
        self._save_runtime_state()
        self._set_style_current(name)
        self._lbl_status.setText(f"Saved look “{name}”")

    def _load_style_preset(self, name):
        data = self._style_presets.get(name)
        if not data:
            return
        self._apply_style_payload(data)
        self._set_style_current(name)
        self._lbl_status.setText(f"Loaded look “{name}”")

    def _delete_style_preset(self, name):
        if QMessageBox.question(self, "Delete look", f"Delete the saved look “{name}”?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._style_presets.pop(name, None)
        self._save_runtime_state()
        if name == self._style_current:
            self._set_style_current("")
        self._lbl_status.setText(f"Deleted look “{name}”")

    def _set_style_current(self, name):
        """Remember which look is on the graph and show it under Styles."""
        self._style_current = name
        self._show_style_current()

    def _show_style_current(self):
        lbl = getattr(self, "_lbl_style_current", None)
        if lbl is None:
            return
        name = self._style_current
        lbl.setText(f"Loaded style: <b>{html.escape(name)}</b>" if name
                    else "Loaded style: none")

    def _export_style_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export look", "css_logger_look.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._current_style_payload(), fh, indent=2)
            self._lbl_status.setText(f"Look exported to {path}")
        except OSError as exc:
            QMessageBox.warning(self, "Export look", f"Could not write the file:\n{exc}")

    def _import_style_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import look", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import look", f"Could not read the file:\n{exc}")
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "Import look", "That file is not a saved look.")
            return
        self._apply_style_payload(data)
        self._set_style_current(f"{pathlib.Path(path).stem} (file)")
        self._lbl_status.setText(f"Look imported from {path}")

    def _open_axis_column_menu(self, pos):
        """Right-click on the headings: tick which columns are on screen."""
        hdr   = self._axis_tv.horizontalHeader()
        _COLS = list(self._axis_tv_cols)
        menu  = QMenu(self._axis_tv)
        # Offer them in the order they currently appear, so the menu matches
        # what the eye sees.
        for visual in range(len(_COLS)):
            logical = hdr.logicalIndex(visual)
            col     = _COLS[logical]
            if col in _AXIS_ALWAYS_SHOWN:
                continue
            act = menu.addAction(self._AXIS_COL_HEADS.get(col, col))
            act.setCheckable(True)
            act.setChecked(not self._axis_tv.isColumnHidden(logical))
            act.toggled.connect(
                lambda on, li=logical: (self._axis_tv.setColumnHidden(li, not on),
                                        self._flush_axis_measured(),
                                        self._autosize_axis_pane()))
        menu.addSeparator()
        menu.addAction("Reset columns", self._reset_axis_columns)
        menu.exec(hdr.mapToGlobal(pos))

    # ── XY tab ─────────────────────────────────────────────────────────────

    def _build_xy_tab(self):
        tab = self._tab_xy
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("X axis:"))
        self._xy_x_combo = QComboBox(); self._xy_x_combo.setMinimumWidth(200)
        self._xy_x_combo.currentIndexChanged.connect(self._on_xy_axis_changed)
        ctrl.addWidget(self._xy_x_combo)
        ctrl.addWidget(QLabel("Y axis:"))
        self._xy_y_combo = QComboBox(); self._xy_y_combo.setMinimumWidth(200)
        self._xy_y_combo.currentIndexChanged.connect(self._on_xy_axis_changed)
        ctrl.addWidget(self._xy_y_combo)

        b_plot = QPushButton("Plot XY"); b_plot.setStyleSheet(_BTN_SUCCESS)
        b_plot.clicked.connect(self._plot_xy); ctrl.addWidget(b_plot)
        b_clean = QPushButton("Clean XY"); b_clean.clicked.connect(self._clean_xy); ctrl.addWidget(b_clean)
        b_cond  = QPushButton("Conditions"); b_cond.clicked.connect(self._open_conditions_dialog); ctrl.addWidget(b_cond)
        ctrl.addStretch()
        lay.addLayout(ctrl)

        # Zoom / pan / view history / save for the XY plot, from the same toolbar
        # the time graph uses. The old hand-made "XY Back" is gone — the toolbar's
        # Back and Home cover it, and one history is better than two.
        self._xy_tb_holder = QWidget()
        _xytb = QHBoxLayout(self._xy_tb_holder)
        _xytb.setContentsMargins(0, 0, 0, 0)
        _xytb.setSpacing(0)
        lay.addWidget(self._xy_tb_holder)

        self._xy_canvas_container = QWidget()
        self._xy_canvas_container.setStyleSheet("background:#f5f5f5;")
        xy_inner = QVBoxLayout(self._xy_canvas_container)
        xy_inner.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._xy_canvas_container, stretch=1)

        self._lbl_xy_info = QLabel("Choose an X and a Y channel — data loads on its own.")
        self._lbl_xy_info.setStyleSheet("color:#777;")
        lay.addWidget(self._lbl_xy_info)

    # ── PV Time Plot tab ────────────────────────────────────────────────────

    def _build_pv_time_tab(self):
        tab = self._tab_pv_time
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Y Variable:"))
        self._pv_time_y_combo = QComboBox()
        self._pv_time_y_combo.setMinimumWidth(200)
        ctrl.addWidget(self._pv_time_y_combo)

        # Whole days, picked on the house calendar (click = one day,
        # Ctrl+click = several, Ctrl+Shift+click = a stretch). Two typed date
        # boxes were the old way and could not express "these five shifts".
        self._btn_pv_time_days = QPushButton("Pick days")
        self._btn_pv_time_days.clicked.connect(self._pick_pv_time_days)
        ctrl.addWidget(self._btn_pv_time_days)
        self._lbl_pv_time_days = QLabel("")
        self._lbl_pv_time_days.setStyleSheet("color:#0D47A1;font-weight:700;")
        ctrl.addWidget(self._lbl_pv_time_days)

        self._pv_time_mode_combo = QComboBox()
        self._pv_time_mode_combo.addItems(["Daily distribution", "Raw shots"])
        ctrl.addWidget(self._pv_time_mode_combo)

        # The plot follows every change on its own; this only re-reads and
        # redraws on request (e.g. after a long wait, for today's new shots).
        self._btn_pv_time_plot = QPushButton("Refresh")
        self._btn_pv_time_plot.setToolTip(
            "Redraw now. The plot also redraws by itself whenever the channel, "
            "the days, the mode or a condition changes.")
        self._btn_pv_time_plot.clicked.connect(self._plot_pv_time)
        ctrl.addWidget(self._btn_pv_time_plot)
        self._btn_pv_time_cond = QPushButton("Conditions")
        self._btn_pv_time_cond.setToolTip(
            "Which shots are plotted — this tab's own condition rows")
        self._btn_pv_time_cond.clicked.connect(self._open_pv_time_conditions)
        ctrl.addWidget(self._btn_pv_time_cond)
        ctrl.addStretch()
        lay.addLayout(ctrl)

        # One collapsed redraw per burst of changes; a hidden tab only notes
        # that it is out of date and redraws when it is opened.
        self._pv_time_auto_timer = QTimer(self)
        self._pv_time_auto_timer.setSingleShot(True)
        self._pv_time_auto_timer.setInterval(400)
        self._pv_time_auto_timer.timeout.connect(self._pv_time_auto_plot)
        self._pv_time_stale = False
        self._pv_time_y_combo.currentIndexChanged.connect(
            lambda _i: self._pv_time_request_plot())
        self._pv_time_mode_combo.currentIndexChanged.connect(
            lambda _i: self._pv_time_request_plot())

        # Zoom / pan / save for this plot, from the same toolbar the time graph
        # uses. A toolbar belongs to one canvas, so it is rebuilt with it.
        self._pv_time_tb_holder = QWidget()
        _pvtb = QHBoxLayout(self._pv_time_tb_holder)
        _pvtb.setContentsMargins(0, 0, 0, 0)
        _pvtb.setSpacing(0)
        lay.addWidget(self._pv_time_tb_holder)

        self._pv_time_canvas_container = QWidget()
        self._pv_time_canvas_container.setStyleSheet("background:#f5f5f5;")
        pv_inner = QVBoxLayout(self._pv_time_canvas_container)
        pv_inner.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._pv_time_canvas_container, stretch=1)

        # How far the reading of the days has got — shown only while reading.
        # Same look as the Graph tab's bar.
        self._pv_time_progress = QProgressBar()
        self._pv_time_progress.setRange(0, 100)
        self._pv_time_progress.setTextVisible(True)
        self._pv_time_progress.setFormat("%v %")
        self._pv_time_progress.setFixedHeight(14)
        self._pv_time_progress.setStyleSheet(
            "QProgressBar{border:1px solid #bbb;border-radius:3px;"
            "background:#eee;color:#111;text-align:center;font-size:10px;}"
            "QProgressBar::chunk{background:#81C784;border-radius:2px;}")
        self._pv_time_progress.hide()
        lay.addWidget(self._pv_time_progress)

        self._lbl_pv_time_info = QLabel("Pick the days and a Y channel, then press Plot.")
        self._lbl_pv_time_info.setStyleSheet("color:#555555;")
        lay.addWidget(self._lbl_pv_time_info)

        # The conditions live in their own window (a "Conditions" button in the
        # row above), like the Graph's. The window is built once and only shown
        # and hidden, so the rows exist — and filter — while it is closed.
        dlg = QDialog(self)
        dlg.setWindowTitle("PV Time Plot — Conditions")
        dlg.resize(760, 340)
        self._pv_time_cond_dialog = dlg
        self._pv_time_cond_layout = QVBoxLayout(dlg)
        note = QLabel("A shot is plotted only when every condition that is switched "
                      "on holds for it. These rows belong to the PV Time Plot alone: "
                      "it reads whole days of its own, independent of the Graph's "
                      "period. Changes redraw the plot at once.")
        note.setStyleSheet("color:#444444;")
        note.setWordWrap(True)
        self._pv_time_cond_layout.addWidget(note)
        # The same editor as the Conditions window. Its own rows, because this
        # tab reads whole days of its own, independent of the Graph's window.
        # A new row starts switched OFF: a row that filtered the moment it
        # appeared would empty the plot for no stated reason.
        self._pv_time_cond_editor = _ConditionEditor(
            [], self.config.get("pv_time_conditions") or [],
            count_fn=self._pv_time_cond_count, new_row_enabled=False)
        self._pv_time_cond_editor.changed.connect(self._pv_time_request_plot)
        self._pv_time_cond_editor.changed.connect(self._refresh_pv_time_cond_button)
        # One switch for every row (like the sidebar's for the Graph), and the
        # Graph's own rows brought over in one go instead of typed twice.
        pvt_cond_top = QHBoxLayout()
        pvt_cond_top.setContentsMargins(0, 0, 0, 0)
        self._pv_time_cond_apply = bool(self.config.get("pv_time_conditions_on", True))
        self._chk_pv_time_cond = QCheckBox("Apply conditions")
        self._chk_pv_time_cond.setStyleSheet(_CHK_STYLE)
        self._chk_pv_time_cond.setChecked(self._pv_time_cond_apply)
        self._chk_pv_time_cond.setToolTip(
            "Untick to plot every shot (trigger shots included) without losing "
            "the condition rows")
        self._chk_pv_time_cond.toggled.connect(self._set_pv_time_cond_apply)
        b_copy_cond = QPushButton("Copy from Graph conditions")
        b_copy_cond.setToolTip("Replace these rows with the rows of the "
                               "Conditions window (Graph, XY and Table tabs)")
        b_copy_cond.clicked.connect(self._copy_graph_conditions_to_pv_time)
        pvt_cond_top.addWidget(self._chk_pv_time_cond)
        pvt_cond_top.addSpacing(12)
        pvt_cond_top.addWidget(b_copy_cond)
        pvt_cond_top.addStretch()
        self._pv_time_cond_layout.addLayout(pvt_cond_top)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        hl = QVBoxLayout(holder); hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self._pv_time_cond_editor)
        hl.addStretch()
        scroll.setWidget(holder)
        self._pv_time_cond_layout.addWidget(scroll, stretch=1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self._close_pv_time_conditions)
        self._pv_time_cond_layout.addWidget(close)
        self._refresh_pv_time_cond_button()

        # A fresh tab opens on the last five working days: enough for a daily
        # pattern to be visible, and about a dozen seconds to read.
        self._pv_time_days = self._recent_workdays(5)
        self._refresh_pv_time_days_label()

    @staticmethod
    def _recent_workdays(n: int) -> list:
        """The last ``n`` weekdays, today included. Weekends carry no shots —
        the archiver writes nothing there — so they are never offered."""
        out, d = [], datetime.now().date()
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d)
            d -= timedelta(days=1)
        return sorted(out)

    def _refresh_pv_time_days_label(self):
        days = self._pv_time_days
        if not days:
            self._lbl_pv_time_days.setText("no day picked")
        elif len(days) == 1:
            self._lbl_pv_time_days.setText(days[0].strftime("%Y-%m-%d"))
        else:
            self._lbl_pv_time_days.setText(
                f"{days[0]:%Y-%m-%d} → {days[-1]:%Y-%m-%d}  ({len(days)} days)")

    def _pick_pv_time_days(self):
        last = self._pv_time_days[-1] if self._pv_time_days else None
        init = QDate(last.year, last.month, last.day) if last else QDate.currentDate()
        dlg = DatePickerDialog(self, init)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        days = sorted({datetime(d.year(), d.month(), d.day()).date()
                       for d in dlg.selected_dates()})
        if not days:
            return
        self._pv_time_days = days
        self._refresh_pv_time_days_label()
        self._pv_time_request_plot()

    def _refresh_pv_time_choices(self):
        """Fill the Y and condition drop-downs from the PV LIST on the left —
        the real channels plus every derived (ƒ) one. The tab reads the archiver
        itself, so it is not limited to what the Graph tab happens to have
        loaded; it is limited to what the user has queued."""
        names = self._real_pv_names() + [d.get("name", "") for d in
                                         (getattr(self, "_custom_pvs", None) or [])
                                         if d.get("name")]
        custom_names = {d.get("name", "") for d in (getattr(self, "_custom_pvs", None) or [])}
        old_map = dict(self._pv_time_choice_map or {})
        choices, self._pv_time_choice_map = [], {}
        for p in names:
            label = p if p in custom_names else self._col_label(p)
            if label in self._pv_time_choice_map:      # same short name twice
                label = p
            choices.append(label)
            self._pv_time_choice_map[label] = p
        label_of = {p: lb for lb, p in self._pv_time_choice_map.items()}
        combo = self._pv_time_y_combo
        # Kept by the CHANNEL, not by its text: a rename must not lose it.
        keep = old_map.get(combo.currentText(), combo.currentText())
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(choices)
        idx = combo.findText(label_of.get(keep, keep))
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        ed = getattr(self, "_pv_time_cond_editor", None)
        if ed is not None:
            ed.set_choices([(lb, p) for lb, p in self._pv_time_choice_map.items()])
        # A different channel list can change what Y is, so the plot follows.
        if combo.currentText() != label_of.get(keep, keep):
            self._pv_time_request_plot()

    def _pv_time_add_condition_row(self):
        return self._pv_time_cond_editor.add_row()

    # -- redrawing on its own -------------------------------------------------

    def _pv_time_tab_visible(self):
        nb = getattr(self, "_notebook", None)
        return nb is not None and nb.currentWidget() is self._tab_pv_time \
            and self._tab_pv_time.isVisible()

    def _pv_time_request_plot(self):
        """Something the plot depends on changed: redraw shortly (collapsed
        over a burst), or — on a hidden tab — remember to do it when shown."""
        if not getattr(self, "_pv_time_auto_ready", False):
            return                       # still building / no plot asked for yet
        if self._pv_time_tab_visible():
            self._pv_time_auto_timer.start()
        else:
            self._pv_time_stale = True

    def _pv_time_auto_plot(self):
        if self._pv_time_busy:
            # A read is running; it draws when done. Ask again after it.
            self._pv_time_auto_timer.start(600)
            return
        self._pv_time_stale = False
        self._plot_pv_time()

    def _on_notebook_tab_changed_pv_time(self, _idx=0):
        if self._pv_time_tab_visible() and (self._pv_time_stale
                                            or self._pv_time_canvas is None):
            self._pv_time_stale = False
            self._pv_time_auto_timer.start(0)

    # ── Table tab ───────────────────────────────────────────────────────────

    def _build_table_tab(self):
        tab = self._tab_table
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)

        self._table_widget = QTableWidget(0, 1)
        # Live leaves a hidden table unwritten; it is written when shown.
        self._table_show_watch = _ShowWatch(self._on_table_shown, self)
        self._table_widget.installEventFilter(self._table_show_watch)
        self._table_widget.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table_widget.setSortingEnabled(False)
        self._table_widget.verticalHeader().setVisible(False)
        self._table_widget.setAlternatingRowColors(True)
        self._table_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table_widget.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table_widget.cellDoubleClicked.connect(self._on_table_double_click)
        self._table_widget.verticalScrollBar().valueChanged.connect(self._on_table_scroll)
        lay.addWidget(self._table_widget, stretch=1)

        info_row = QHBoxLayout()
        self._lbl_table_info = QLabel("No data - click 'Load data'.")
        self._lbl_table_info.setStyleSheet("color:#777;")
        info_row.addWidget(self._lbl_table_info)
        info_row.addStretch()
        b_cond = QPushButton("Conditions"); b_cond.clicked.connect(self._open_conditions_dialog)
        self._btn_export = QPushButton("Export table")
        self._btn_export.clicked.connect(self._open_export_dialog)
        info_row.addWidget(b_cond); info_row.addWidget(self._btn_export)
        lay.addLayout(info_row)

    # ── Log tab ─────────────────────────────────────────────────────────────

    def _build_log_tab(self):
        tab = self._tab_log
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)
        self._log_area = QPlainTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setStyleSheet(
            "QPlainTextEdit{background:#1e1e1e;color:#d4d4d4;"
            "font-family:'Consolas','Courier New',monospace;font-size:11px;}")
        lay.addWidget(self._log_area, stretch=1)
        b_clear = QPushButton("Clear log"); b_clear.setIcon(_bin_icon())
        b_clear.clicked.connect(self._clear_log)
        lay.addWidget(b_clear)

    # ── Populate UI from config ─────────────────────────────────────────────

    def _populate_ui(self):
        if self._master_pv_edit is not None:
            self._master_pv_edit.setText(self._master_pv)
        if self._master_multiple_edit is not None:
            self._master_multiple_edit.setText(self._master_multiple)

        # Auto-select "operations/operace" preset (or first preset) and load its PVs
        # Time window stays as "last hour" — do NOT adopt preset's saved times
        _auto_idx = -1
        for i, p in enumerate(self._presets):
            nm = p.get("name", "").lower()
            if "operac" in nm or "operat" in nm:
                _auto_idx = i
                break
        if _auto_idx < 0:
            # The first saved preset — never the built-in Blank, which would
            # open the program on an empty list.
            _auto_idx = next((i for i, p in enumerate(self._presets)
                              if not p.get("builtin")), -1)

        if _auto_idx >= 0:
            # Block signal so currentIndexChanged doesn't trigger _load_preset here
            self._preset_combo.blockSignals(True)
            self._preset_combo.setCurrentIndex(_auto_idx)
            self._preset_combo.blockSignals(False)
            p = self._presets[_auto_idx]
            for pv in p.get("pvs", []):
                self._pv_list.addItem(self._make_pv_item(pv))
            # Also apply the preset's ramp settings, if it saved any
            self._apply_ramp_settings(p)
            # Do NOT override _dt_from / _dt_to — keep "last hour"
        else:
            # Fallback: restore last PV list from config
            for pv in self.config.get("pv_list", []):
                self._pv_list.addItem(self._make_pv_item(pv))

        self._migrate_custom_pv_bindings()  # pre-bindings entries -> explicit PVs
        self._sync_pv_list_customs()   # show defined custom PVs alongside the real ones
        self._update_pv_count()
        self._refresh_time_labels()
        self._refresh_pv_time_choices()   # the list exists now, so fill its drop-downs

    def _refresh_time_labels(self):
        if self._live_mode and self._live_window_span:
            # Live plots a window that rolls with "now", and it is not always the
            # From/To it was started from (a too-wide window is clamped — see
            # _live_span_from_window), so show the span that is really on screen
            # instead of two timestamps that would both be wrong a minute later.
            span = _fmt_span(self._live_window_span.total_seconds())
            self._lbl_tw_from.setText(f"From: now − {span}")
            self._lbl_tw_to.setText(  "To:   now")
        else:
            self._lbl_tw_from.setText("From: " + self._dt_from.strftime("%Y-%m-%d  %H:%M"))
            self._lbl_tw_to.setText(  "To:   " + self._dt_to.strftime("%Y-%m-%d  %H:%M"))
        if self._live_mode:
            self._lbl_tw_live.setText("Live: ON")
            self._lbl_tw_live.setStyleSheet("color:#2E7D32;font-size:11px;font-weight:700;")
        else:
            self._lbl_tw_live.setText("Live: OFF")
            self._lbl_tw_live.setStyleSheet("color:#555;font-size:11px;")

    # ── Graph plotting ──────────────────────────────────────────────────────

    def _band_ylim(self, idx, n, dmin, dmax, autoscale):
        """Y-limits that place a PV's data range [dmin, dmax] into its vertical
        band of the shared plot. Band ``idx`` (0 = top) of ``n`` occupies the
        fraction [f_bot, f_top] of the axes height; an Autoscale'd PV uses the
        full height instead so it overlays the banded ones."""
        if not (dmax > dmin):                      # flat / single value
            c = dmin
            pad = abs(c) * 0.1 or 1.0
            dmin, dmax = c - pad, c + pad
        band_h = 1.0 / max(n, 1)
        pad_f  = min(0.45, max(0.0, float(
            self._graph_opts.get("band_pad_frac", 0.12))))
        if autoscale:
            # Full height, with the same gap in screen terms as a band has, so
            # "Gap inside band" reaches the Autoscale'd PVs too.
            margin = max(0.005, pad_f * band_h)
            f_bot, f_top = margin, 1.0 - margin
        else:
            margin = pad_f * band_h                # gap so traces don't touch band edges
            f_top  = 1.0 - idx * band_h - margin
            f_bot  = 1.0 - (idx + 1) * band_h + margin
        denom = (f_top - f_bot) or 1.0
        span  = dmax - dmin
        lo = dmin - f_bot / denom * span
        hi = dmax + (1.0 - f_top) / denom * span
        return lo, hi

    def _schedule_replot(self, delay_ms: int = 150):
        """Request a graph redraw, coalescing bursts of rapid edits into one.
        Restarting the single-shot timer on each call means a redraw only fires
        once the user pauses, so clicking many controls in a row no longer queues
        one expensive full rebuild per click."""
        self._replot_timer.start(delay_ms)

    # ── Trace arrays: envelope, zoom detail, window hold ─────────────────────

    def _thin_trace(self, pv, ts, vals, avg_target):
        """(ts, values) → (ts, values, low edge, high edge), thinned for the
        screen. Each bin keeps the lowest and highest value behind it, so no
        spike is ever averaged away. 0 = no target: only the 25 000 cap.
        With conditions on the archiver's bin min/max is used too: the bins
        that reach here passed the Conditions by that very range, and without
        it the one day with the event was a flat line of means over a long
        window (12.10.2026). Zoom in and the detail is judged shot by shot."""
        lo, hi = _envelope_for(ts, vals, self._minmax_by_pv.get(pv))
        target = avg_target if avg_target > 0 else 25000
        if avg_target <= 0 and ts.size > 25000:
            self._plot_thinned = max(getattr(self, "_plot_thinned", 0) or 0,
                                     int(ts.size))
        return _downsample_arrays_envelope(ts, vals, lo, hi, target)

    def _splice_detail(self, pv, ts, vals, lo, hi):
        """Replace the part of a trace inside the zoom-detail range with the
        finer data read for it (see _load_zoom_detail). Untouched without one."""
        d = self._zoom_detail
        if not d or d.get("epoch") != self._load_epoch or pv not in d["pvs"]:
            return ts, vals, lo, hi
        d_ts, d_v, d_lo, d_hi = d["pvs"][pv]
        keep = (ts < d["a"]) | (ts > d["b"])
        ts2 = np.concatenate((ts[keep], d_ts))
        order = np.argsort(ts2, kind="stable")
        return (ts2[order], np.concatenate((vals[keep], d_v))[order],
                np.concatenate((lo[keep], d_lo))[order],
                np.concatenate((hi[keep], d_hi))[order])

    def _hold_to_window(self, pv, ts, vals, lo, hi, win_start_ns, win_end_ns):
        """Carry-forward / hold (like CS Studio): the value seen BEFORE the
        window is drawn from its left edge, the last value out to its right
        edge, so the trace spans the whole window instead of breaking off.
        The value from before the window is a shot like any other: when the
        conditions reject it, it is not drawn."""
        pre = self._pre_window_vals.get(pv)
        pre_val = pre[1] if (pre and isinstance(pre[1], (int, float))) else None
        if self._conditions and (pre_val is None or not self._pre_window_ok()):
            # Rejected (laser off): hold the last shot the Conditions accept.
            pre_val = self._last_good_value(pv, win_start_ns)
        if not ts.size:
            if pre_val is None:
                return ts, vals, lo, hi
            two = np.array([pre_val, pre_val], dtype=np.float64)
            return (np.array([win_start_ns, win_end_ns], dtype=np.float64),
                    two, two.copy(), two.copy())
        if pre_val is not None and ts[0] > win_start_ns:
            ts   = np.concatenate(([win_start_ns], ts))
            vals = np.concatenate(([pre_val], vals))
            lo   = np.concatenate(([pre_val], lo))
            hi   = np.concatenate(([pre_val], hi))
        if ts[-1] < win_end_ns:
            ts   = np.concatenate((ts, [win_end_ns]))
            vals = np.concatenate((vals, vals[-1:]))
            lo   = np.concatenate((lo, lo[-1:]))
            hi   = np.concatenate((hi, hi[-1:]))
        return ts, vals, lo, hi

    def _replace_band(self, i, ax, times_num, lo, hi):
        """Redraw trace i's min–max band for new arrays, in place (a filled
        area cannot be given new data, so the old one is removed)."""
        while len(self._graph_bands) <= i:
            self._graph_bands.append(None)
        old = self._graph_bands[i]
        pv = self._graph_pvs[i] if i < len(self._graph_pvs) else None
        st = self._pv_settings.get(pv, {}) if pv else {}
        visible = st.get("show", True) if old is None else old.get_visible()
        color = st.get("color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])
        alpha = self._pv_style_kwargs(st, times_num.size, True).get("alpha", 1.0) \
            if st else 1.0
        # Still a band: give the same one the new arrays (matplotlib 3.10+).
        # Removing and re-creating 15 of them was part of every Live refresh.
        if (old is not None and hasattr(old, "set_data")
                and _has_spread(lo, hi)):
            try:
                old.set_data(times_num, lo, hi)
                old.set_alpha(max(0.08, 0.28 * float(alpha)))
                return
            except Exception:
                pass                         # fall back to a new one
        if old is not None:
            try:
                old.remove()
            except Exception:
                pass
        self._graph_bands[i] = self._draw_band(ax, times_num, lo, hi, color,
                                               alpha, visible)

    @staticmethod
    def _draw_band(ax, times_num, lo, hi, color, alpha, visible):
        """The light min–max band under a trace, or None when the trace has no
        spread (raw data: every point is a single archived value)."""
        if not _has_spread(lo, hi):
            return None
        band = ax.fill_between(times_num, lo, hi, step="post", facecolor=color,
                               edgecolor="none", linewidth=0,
                               alpha=max(0.08, 0.28 * float(alpha)), zorder=1)
        band.set_visible(visible)
        return band

    def _plot_graph(self):
        """Safe wrapper: a plotting failure must never crash the app or leave a
        stale graph — clear the canvas and show a message instead."""
        # A direct plot supersedes any pending coalesced one.
        self._replot_timer.stop()
        # The canvas the user is clicking on is about to be thrown away, so a
        # half-finished reference-line placement has to end here.
        if self._ref_pick:
            self._cancel_ref_pick()
        # A zoomed-in view survives a rebuild: changing a channel's look must
        # not throw the user back to the whole window.
        self._keep_xlim = None
        if self._user_zoomed and self._graph_axes:
            try:
                self._keep_xlim = tuple(self._graph_axes[0].get_xlim())
            except Exception:
                pass
        # The old canvas and toolbar are thrown away and new ones built: with
        # the window allowed to paint in between, every rebuild showed an empty
        # graph area and a missing toolbar for a moment — the blink on every
        # picture while a load fills in. Painting waits until the new graph is
        # in place, so the swap is one frame.
        _was_painting = self.updatesEnabled()
        if _was_painting:
            self.setUpdatesEnabled(False)
        try:
            self._plot_graph_impl()
        except Exception:
            import traceback
            self._log(f"[plot_graph]\n{traceback.format_exc()}")
            try:
                self._clear_graph()
            except Exception:
                pass
            self._lbl_graph_info.setText("Graph error — see Log tab.")
            try:
                self._refresh_axis_settings_tv()
            except Exception:
                pass
        finally:
            if _was_painting:
                self.setUpdatesEnabled(True)

    def _plot_window(self):
        """(use window, start ns, end ns) the traces are held out to. Live
        supplies its own window via _plot_window_ns so it need not clobber the
        user's chosen From/To."""
        _win_override = getattr(self, "_plot_window_ns", None)
        if _win_override:
            _win_start_ns, _win_end_ns = _win_override
            return _win_end_ns > _win_start_ns, _win_start_ns, _win_end_ns
        return (self._dt_to > self._dt_from, dt_to_ns(self._dt_from),
                dt_to_ns(self._dt_to))

    def _plot_pv_lists(self, win_start_ns):
        """(numeric PVs, visible PVs) in the order the graph stacks them."""
        def _pre_numeric(pv):
            p = self._pre_window_vals.get(pv)
            return ((p is not None and isinstance(p[1], (int, float)))
                    or self._last_good_value(pv, win_start_ns) is not None)

        # PVs with actual numeric samples (or a numeric last-known value before
        # the window, so a fully-empty window still shows the held line).
        # The user's own order from the PV table (dragged rows), not the order
        # of the PV list on the left — that one only decides what is loaded.
        _plot_pvs = self._ordered_for_plot(self._pv_order)
        numeric_pvs = [
            pv for pv in _plot_pvs
            if self._pv_settings.get(pv, {}).get("show", True)
            and (any(isinstance(v, (int, float)) for _, v, _ in self._samples_by_pv.get(pv, []))
                 or _pre_numeric(pv))
        ]
        # All visible PVs (for axis layout — includes PVs with no data yet)
        visible_pvs = [
            pv for pv in _plot_pvs
            if self._pv_settings.get(pv, {}).get("show", True)
        ]
        return numeric_pvs, visible_pvs

    def _plot_pairs(self, pvs_for_axes):
        """Every PV's (ts, value) pairs from the table rows, in ONE pass over
        the (possibly 100k-row) table instead of re-scanning the whole table
        once per PV — with a dozen PVs that scan was the bulk of a replot. The
        result is cached against the table's identity, so a replot triggered by
        an appearance change (font, spacing, Y limits, Show) reuses it."""
        _rows  = self._table_rows
        _token = (len(_rows), _rows[-1][0] if _rows else 0, tuple(pvs_for_axes))
        _cached = getattr(self, "_pairs_cache", None)
        if _cached is not None and _cached[0] == _token:
            return _cached[1]
        _pairs_by_pv = {pv: [] for pv in pvs_for_axes}
        for ts_ns, row_dict in _rows:
            for pv, entry in row_dict.items():
                lst = _pairs_by_pv.get(pv)
                if lst is None:
                    continue
                value = entry[0]
                if isinstance(value, (int, float)):
                    lst.append((ts_ns, value))
        self._pairs_cache = (_token, _pairs_by_pv)
        return _pairs_by_pv

    def _draw_loading_span(self, ax0, fsize):
        """While a load runs, the part of the window not read yet is greyed, so
        the user can see how far back it has got and what is still missing.
        Replaces the previous grey area, if any."""
        for art in getattr(self, "_loading_artists", None) or []:
            try:
                art.remove()
            except Exception:
                pass
        self._loading_artists = []
        _unread_to = getattr(self, "_loading_from_ns", None)
        _use_window, _win_start_ns, _ = self._plot_window()
        if not (_use_window and self._load_in_flight and _unread_to
                and _unread_to > _win_start_ns):
            return
        t_min = datetime.fromtimestamp(_win_start_ns / 1e9, timezone.utc)
        _edge = datetime.fromtimestamp(_unread_to / 1e9, timezone.utc)
        self._loading_artists = [
            ax0.axvspan(t_min, _edge, facecolor="#e4e4e4", edgecolor="none",
                        zorder=0),
            ax0.text(t_min + (_edge - t_min) / 2, 0.5, "loading",
                     transform=ax0.get_xaxis_transform(), ha="center",
                     va="center", color="#555555", fontsize=fsize, zorder=1)]

    def _draw_graph_note(self, ax0, use_window, win_start_ns, fsize, axes_px_w,
                         dpi=96):
        """The yellow note at the top of the plot (last good shot, skipped
        Conditions). Replaces the previous one, if any."""
        old = getattr(self, "_graph_note_artist", None)
        self._graph_note_artist = None
        if old is not None:
            try:
                old.remove()
            except Exception:
                pass
        # A held line looks exactly like a live one, so a window drawn only from
        # the last good shot says so on the plot itself.
        _note = self._last_good_note(win_start_ns) if use_window else ""
        # A condition that could not judge this window lets everything through,
        # so the plot says which one — else it reads as "these shots passed".
        _skipped = [shorten_pv_name(p) for p in
                    (getattr(self, "_cond_skipped", None) or [])]
        if _skipped and self._conditions:
            _note = "\n".join(filter(None, [
                _note, "Not applied (no data in this period): "
                       + ", ".join(_skipped)]))
        if not _note:
            return
        # Never cut: the text size gives way to the plot's width.
        _longest = max(len(s) for s in _note.split("\n"))
        _note_pt = min(fsize, axes_px_w / (0.58 * _longest * dpi / 72.0))
        self._graph_note_artist = ax0.text(
            0.5, 0.985, _note, transform=ax0.transAxes, ha="center",
            va="top", color="#111111", fontsize=max(5.0, _note_pt),
            zorder=10, linespacing=1.3,
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#FFF3CD",
                      edgecolor="#B58100", linewidth=1.0))

    def _refresh_graph_in_place(self, sync=False):
        """A new picture while a load fills in, drawn into the graph that is
        already on screen: new data for the same lines, bands and Y ranges,
        the grey "loading" area moved, one repaint. A full rebuild throws the
        canvas and the toolbar away and lays every axis out again — on every
        piece that arrived, which made the graph blink and stall the window.

        Returns False when the graph cannot take the new data as it is (no
        graph yet, another set of axes needed); the caller rebuilds then.
        ``sync`` renders before returning, so the caller can time the whole
        picture (the load paces its next picture by that)."""
        if (self._mpl_canvas is None or not self._graph_axes
                or not self._graph_lines or not self._samples_by_pv):
            return False
        _use_window, _win_start_ns, _win_end_ns = self._plot_window()
        numeric_pvs, visible_pvs = self._plot_pv_lists(_win_start_ns)
        pvs_for_axes = numeric_pvs if numeric_pvs else visible_pvs
        if (not numeric_pvs or list(pvs_for_axes) != list(self._graph_pvs)
                or len(self._graph_lines) != len(pvs_for_axes)):
            return False
        _pairs_by_pv = self._plot_pairs(pvs_for_axes)
        avg_target = self._avg_target_points()
        _markers = bool(self._graph_opts.get("line_markers", True))
        n = len(pvs_for_axes)
        self._plot_thinned = 0
        new_raw, new_env, new_base = [], [], []
        for i, (pv, ax, pv_lines) in enumerate(
                zip(pvs_for_axes, self._graph_axes, self._graph_lines)):
            ts_a, values = _pairs_to_ns_arrays(_pairs_by_pv.get(pv, []))
            ts_a, values, lo_a, hi_a = self._thin_trace(pv, ts_a, values, avg_target)
            new_base.append((ts_a, values, lo_a, hi_a))
            ts_a, values, lo_a, hi_a = self._splice_detail(pv, ts_a, values, lo_a, hi_a)
            if _use_window:
                ts_a, values, lo_a, hi_a = self._hold_to_window(
                    pv, ts_a, values, lo_a, hi_a, _win_start_ns, _win_end_ns)
            times_num = _ns_to_num(ts_a)
            new_raw.append((times_num, values))
            new_env.append((lo_a, hi_a))
            pv_setting = self._pv_settings.get(pv, self._get_pv_default_settings(pv, i))
            eff_smooth = pv_setting.get("smooth", None) or 1
            style_kw = self._pv_style_kwargs(pv_setting, times_num.size, _markers)
            if len(pv_lines) == 2:
                pv_lines[0].set_data(times_num, values)
                pv_lines[1].set_data(
                    times_num, _moving_avg_np(values, eff_smooth)
                    if eff_smooth > 1 and values.size >= eff_smooth else values)
            elif pv_lines:
                pv_lines[0].set_data(times_num, values)
            # A short trace gets its dots, a long one loses them, as in a rebuild.
            pv_lines[-1].set_marker(style_kw.get("marker", "None"))
            self._replace_band(i, ax, times_num, lo_a, hi_a)
            if values.size and not np.all(np.isnan(values)):
                dmin = float(np.nanmin(lo_a)); dmax = float(np.nanmax(hi_a))
            else:
                dmin, dmax = 0.0, 1.0
            autosc = pv_setting.get("auto_scale", False)
            if not autosc:
                if pv_setting.get("ymin") is not None: dmin = pv_setting["ymin"]
                if pv_setting.get("ymax") is not None: dmax = pv_setting["ymax"]
            ax.set_ylim(*self._band_ylim(i, n, dmin, dmax, autosc))
        self._graph_raw, self._graph_env, self._graph_base = new_raw, new_env, new_base
        self._graph_raw_np = [
            (np.asarray(t, dtype=float), np.asarray(v, dtype=float))
            for t, v in self._graph_raw]
        try:
            _fsize = max(5, self._font_size_spin.value())
        except Exception:
            _fsize = max(5, int(self._graph_opts.get("font_size", 11)))
        ax0 = self._graph_axes[0]
        self._draw_loading_span(ax0, _fsize)
        _axes_px_w = max(1.0, ax0.get_position().width
                         * float(self._mpl_figure.get_figwidth()) * 96)
        self._draw_graph_note(ax0, _use_window, _win_start_ns, _fsize, _axes_px_w)
        if self._sel_range:
            self._recompute_stats()
        self._flush_axis_measured()
        self._blit_bg = None
        if sync:
            self._mpl_canvas.draw()
        else:
            self._mpl_canvas.draw_idle()
        return True

    def _plot_graph_impl(self):
        # Always start from a clean canvas so an early return never leaves a
        # stale graph sitting under a "no data" message.
        self._clear_graph()

        # In Live an empty start (no channel has said anything yet) still gets
        # its empty axes, so the user can drag a selection that waits for the
        # first shot.
        if not self._samples_by_pv and not self._live_mode:
            self._lbl_graph_info.setText("Load data first.")
            # Every way out of here refreshes the PV table, so callers need not
            # (they used to, and every load rebuilt it twice).
            self._refresh_axis_settings_tv()
            return

        # Carry-forward window bounds (ns) — used to draw the last known value
        # across any gaps at the window edges (see per-PV pairs building below).
        _use_window, _win_start_ns, _win_end_ns = self._plot_window()
        numeric_pvs, visible_pvs = self._plot_pv_lists(_win_start_ns)

        # Use numeric PVs for axes when data exists, else all visible PVs (empty axes)
        pvs_for_axes = numeric_pvs if numeric_pvs else visible_pvs

        if not pvs_for_axes:
            self._lbl_graph_info.setText("No PVs loaded.")
            self._refresh_axis_settings_tv()
            return

        no_data = len(numeric_pvs) == 0

        _dpi = 96
        # The figure is laid out for the size it will REALLY have on screen.
        # The pixel gaps of the axis columns become fractions of this width,
        # and matplotlib keeps fractions when the canvas is stretched — so
        # laying out for a made-up 960 px (whenever the graph was narrower)
        # shrank every gap the user set. Only a container that has not been
        # laid out yet (a few px) falls back to a typical size.
        _cw  = self._graph_container.width()  - 8
        _ch  = self._graph_container.height() - 8
        if _cw < 150:
            _cw = 960
        if _ch < 120:
            _ch = 480
        self._graph_layout_w = _cw

        fig = Figure(figsize=(_cw / _dpi, _ch / _dpi), dpi=_dpi)
        n   = len(pvs_for_axes)

        _opts = self._graph_opts
        try:
            _fsize = max(5, self._font_size_spin.value())
        except Exception:
            _fsize = max(5, int(_opts.get("font_size", 11)))

        # ── Single banded graph: ONE plot rectangle, one left Y axis per PV.
        # Each PV occupies its own vertical band (see _band_ylim); a PV with
        # Autoscale ticked spans the full graph height instead. Grid / zoom / pan
        # act on the whole graph at once.
        _fig_w_px = _cw
        _PX_PER_PT   = _dpi / 72.0
        _show_titles = bool(_opts.get("show_axis_titles", True))
        # Whitespace each per-PV column reserves BETWEEN its rotated title and the
        # axis to its left, and between the title and its own tick numbers. Both
        # are user-tunable (Graph settings) because the "right" spacing depends on
        # the font size and how many PVs share the width.
        _GAP_PX      = max(0.0, float(_opts.get(
            "axis_gap_px", _GRAPH_OPTS_DEFAULTS["axis_gap_px"])))
        # May be negative: the rotated tick numbers keep a couple of pixels of
        # empty margin around their digits, so a small negative value moves the
        # title closer without touching any ink.
        _MIN_PAD_PX  = max(-8.0, float(_opts.get(
            "label_pad_px", _GRAPH_OPTS_DEFAULTS["label_pad_px"])))
        _ticksize    = max(5, _fsize + int(_opts.get("tick_font_delta", -1)))
        _ticklen_px  = 3.5 * _PX_PER_PT          # rcParams ytick.major.size
        _tickpad_px  = 2 * _PX_PER_PT            # tick_params(pad=2) below
        _ticknum_px  = _ticksize * _PX_PER_PT    # rotated number ≈ one glyph high
        _title_px    = (_fsize * _PX_PER_PT) if _show_titles else 0.0
        _pad_px      = _MIN_PAD_PX if _show_titles else 0.0
        # Geometry of one column, right → left, all in px:
        #   spine │ tick │ pad │ ⟨number⟩ │ label_pad │ ⟨title⟩ │ gap │ next spine
        # The rotated tick numbers are anchor-centred on their tick mark, i.e. on
        # (spine − ticklen − tickpad), so they may stick out slightly PAST their
        # own spine into the column to the right; whatever they overhang has to be
        # added on top of the requested gap.
        _num_x0_off   = _ticklen_px + _tickpad_px + _ticknum_px * 0.5   # numbers' left edge
        _num_overhang = max(0.0, _ticknum_px * 0.5 - _ticklen_px - _tickpad_px)
        _axis_px      = (_num_x0_off + _pad_px + _title_px
                         + _GAP_PX + _num_overhang)
        STEP_fig      = max(0.010, _axis_px / _fig_w_px)
        # Offset (px, leftwards from its own spine) of the title's ANCHOR. A y
        # label is rotated 90° about its anchor with the default rotation mode, so
        # the anchor ends up on the title's RIGHT edge — placing it explicitly
        # (set_label_coords below) instead of via labelpad is what makes the gaps
        # predictable: labelpad is measured from matplotlib's own tick-label bbox,
        # which is centred on the spine and therefore wider than the space
        # actually free — that mismatch was the extra whitespace between a title
        # and the axis to its left.
        _title_off_px = _num_x0_off + _pad_px
        _col_need_px  = _title_off_px + _title_px      # spine → title's left edge

        # Y tick numbers are rotated 90°, so each one stacks VERTICALLY (its
        # footprint height ≈ the text length, not the glyph height). Forcing 6 of
        # them per axis makes the numbers overprint each other on a short plot or
        # when many bands share the height. Budget the major-tick count from the
        # actual plot height and a ~7-glyph label extent so they always clear.
        _TOP    = min(1.0, max(0.5, float(_opts.get("margin_top", 0.97))))
        # Decide the two-line stamps BEFORE the margins: the room they need is
        # what the bottom gap has to reserve, and _apply_x_ticks does not run
        # until much further down. A period crossing midnight is exactly when
        # the stamps carry a date line (see _apply_x_ticks).
        if _use_window:
            self._x_labels_two_line = (
                datetime.fromtimestamp(_win_start_ns / 1e9, TZ_PRAGUE).date()
                != datetime.fromtimestamp(_win_end_ns / 1e9, TZ_PRAGUE).date())
        _BOTTOM = min(_TOP - 0.05,
                      max(0.02,
                          float(_opts.get("margin_bottom", 0.12)),
                          self._bottom_margin_floor(_ch)))
        _plot_h_px    = max(1.0, _ch * (_TOP - _BOTTOM))
        _ylbl_ext_px  = 7 * 0.6 * _ticksize * _PX_PER_PT   # ≈ "−1.234M" rotated
        _tick_cap     = max(2, int(_opts.get("y_ticks_max", 6)))
        _n_yticks     = max(2, min(_tick_cap, int(_plot_h_px / (_ylbl_ext_px * 1.3))))

        # Space left of the OUTERMOST column: exactly what its own title needs
        # plus a small margin (a fixed 4 % of the figure used to leave a wide
        # unused band there and stole plot width).
        BASE_R = max(0.0, min(0.3, float(_opts.get("margin_right", 0.015))))
        BASE_L = max(0.005, (_col_need_px
                             + max(0.0, float(_opts.get(
                                 "outer_margin_px",
                                 _GRAPH_OPTS_DEFAULTS["outer_margin_px"])))) / _fig_w_px)
        left_margin  = min(0.60, BASE_L + (n - 1) * STEP_fig)
        right_margin = 1.0 - BASE_R
        fig.subplots_adjust(left=left_margin, right=right_margin, top=_TOP, bottom=_BOTTOM)

        axes_width = max(0.05, right_margin - left_margin)
        _axes_px_w = max(1.0, axes_width * _fig_w_px)   # plot rect width in px
        # Distribute the stacked left-axis columns EVENLY across the actual (possibly
        # capped) left margin. When many PVs hit the 0.60 cap, the old fixed STEP_fig
        # pushed the leftmost columns off the canvas so they piled up and their
        # numbers overlapped; deriving the step from the real available width keeps
        # every column on-canvas and evenly spaced. Uncapped, this equals the old
        # value, so the normal-case layout is unchanged.
        if n > 1:
            STEP_ax = ((left_margin - BASE_L) / (n - 1)) / axes_width
        else:
            STEP_ax = STEP_fig / axes_width

        axes = [fig.add_subplot(111)]
        for _ in range(1, n):
            axes.append(axes[0].twinx())

        # Order the Y-axis columns left → right in PV order (first PV = leftmost
        # column, last PV nearest the plot) so reading the axes left-to-right
        # matches the top-to-bottom order of the traces and the table.
        self._graph_spine_xpos = []
        xfrac_of = []
        for i, ax in enumerate(axes):
            ax.yaxis.set_label_position("left"); ax.yaxis.tick_left()
            xfrac = -(n - 1 - i) * STEP_ax
            xfrac_of.append(xfrac)
            ax.spines["left"].set_position(("axes", xfrac))
            if i > 0:
                ax.spines["right"].set_visible(False)  # twins must not draw over the plot
            self._graph_spine_xpos.append((xfrac, "left"))
        ax_x = axes[0]
        self._graph_x_ax = ax_x
        # Traces are plotted as raw date NUMBERS (see _pairs_to_arrays), so the
        # date unit converter is registered explicitly here — otherwise the first
        # set_xlim(datetime) would have nothing to convert with. The locators and
        # formatters set further down replace the defaults this installs.
        ax_x.xaxis.axis_date(tz=timezone.utc)

        _pairs_by_pv = self._plot_pairs(pvs_for_axes)

        _tmin_num = _tmax_num = None
        _n_times  = 0
        _markers  = bool(_opts.get("line_markers", True))
        self._graph_lines = []; self._graph_pvs = list(pvs_for_axes); self._graph_raw = []
        self._graph_bands = []; self._graph_env = []; self._graph_base = []
        self._graph_hold = (_use_window, _win_start_ns, _win_end_ns)
        self._plot_thinned = 0

        avg_target = self._avg_target_points()
        for i, (pv, ax) in enumerate(zip(pvs_for_axes, axes)):
            # Everything from here on is numpy: binning and the window edges over
            # 100k+ samples per PV in Python lists was a large part of a replot.
            ts_a, values = _pairs_to_ns_arrays(_pairs_by_pv.get(pv, []))
            # Thinned for the screen, but every bin keeps its lowest and highest
            # value (never a plain mean — that hid every spike), then any finer
            # zoom detail is spliced in, then held out to the window edges.
            ts_a, values, lo_a, hi_a = self._thin_trace(pv, ts_a, values, avg_target)
            self._graph_base.append((ts_a, values, lo_a, hi_a))
            ts_a, values, lo_a, hi_a = self._splice_detail(pv, ts_a, values, lo_a, hi_a)
            if _use_window:
                ts_a, values, lo_a, hi_a = self._hold_to_window(
                    pv, ts_a, values, lo_a, hi_a, _win_start_ns, _win_end_ns)

            # X in matplotlib date numbers (plain floats) — no datetime objects.
            times_num = _ns_to_num(ts_a)
            if times_num.size:
                t_lo = float(times_num[0]); t_hi = float(times_num[-1])
                _tmin_num = t_lo if _tmin_num is None else min(_tmin_num, t_lo)
                _tmax_num = t_hi if _tmax_num is None else max(_tmax_num, t_hi)
                _n_times += times_num.size

            pv_setting = self._pv_settings.get(pv, self._get_pv_default_settings(pv, i))
            line_color = pv_setting.get("color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])
            line_width = pv_setting.get("width", None)
            pv_smooth  = pv_setting.get("smooth", None)
            disp_name  = pv_setting.get("display_name", shorten_pv_name(pv))
            visible    = pv_setting.get("show", True)
            eff_smooth = pv_smooth if pv_smooth is not None else 1

            self._graph_raw.append((times_num, values))
            self._graph_env.append((lo_a, hi_a))

            # Per-signal look: line style, point style, point size, transparency.
            style_kw = self._pv_style_kwargs(pv_setting, times_num.size, _markers)
            self._graph_bands.append(self._draw_band(
                ax, times_num, lo_a, hi_a, line_color,
                style_kw.get("alpha", 1.0), visible))

            if eff_smooth > 1 and values.size >= eff_smooth:
                lw_raw = (line_width * 0.5) if line_width is not None else 0.8
                # The faint raw trace under a smoothed one stays faint, but never
                # darker than the signal's own transparency setting.
                raw_kw = dict(style_kw)
                raw_kw["alpha"] = 0.3 * style_kw.get("alpha", 1.0)
                raw_line, = ax.plot(times_num, values, color=line_color, linewidth=lw_raw,
                                    drawstyle="steps-post", zorder=1, **raw_kw)
                raw_line.set_visible(visible)
                smoothed = _moving_avg_np(values, eff_smooth)
                lw_sm = line_width if line_width is not None else 1.8
                sm_line, = ax.plot(times_num, smoothed, color=line_color, linewidth=lw_sm,
                                   drawstyle="steps-post",
                                   label=f"{disp_name} (avg {eff_smooth})", zorder=2,
                                   **style_kw)
                sm_line.set_visible(visible)
                self._graph_lines.append([raw_line, sm_line])
            else:
                lw = line_width if line_width is not None else 1.2
                line, = ax.plot(times_num, values, color=line_color, linewidth=lw,
                                drawstyle="steps-post", label=disp_name, zorder=2,
                                **style_kw)
                line.set_visible(visible)
                self._graph_lines.append([line])

            # Rotated (vertical) Y label + tick numbers, one narrow column per PV.
            # The title is pinned EXPLICITLY at a known pixel offset left of its
            # own spine (set_label_coords, axes fractions) rather than through
            # labelpad: labelpad is measured from the rendered tick-label bbox, so
            # it could not be reconciled with the column budget above and left a
            # visible extra gap towards the neighbouring axis.
            if _show_titles:
                ax.set_ylabel(disp_name, color=line_color, fontsize=_fsize, rotation=90)
                ax.yaxis.set_label_coords(xfrac_of[i] - _title_off_px / _axes_px_w, 0.5)
            else:
                ax.set_ylabel("")
            ax.tick_params(axis="y", labelcolor=line_color, labelsize=_ticksize,
                           pad=2, labelrotation=90)
            from matplotlib.ticker import AutoMinorLocator, MaxNLocator, EngFormatter
            ax.yaxis.set_major_locator(MaxNLocator(_n_yticks))
            # Minor ticks are pure decoration here and every one of them is another
            # artist to lay out and draw — with a dozen stacked axes that adds up,
            # so they can be switched off in Graph settings.
            if _opts.get("y_minor_ticks", True):
                ax.yaxis.set_minor_locator(AutoMinorLocator(5))
                ax.tick_params(axis="y", which="minor", length=3, labelsize=0)
            else:
                from matplotlib.ticker import NullLocator
                ax.yaxis.set_minor_locator(NullLocator())
            # SI-prefix ticks (k, M, µ, ...) instead of a floating "1e6" multiplier
            # or long plain numbers — keeps the narrow per-PV column readable.
            ax.yaxis.set_major_formatter(EngFormatter(sep=""))

            # Position this PV's data into its vertical band (or full height if
            # Autoscale is on). Manual Y min/max override the data range that is
            # mapped into the band.
            ymin_pv = pv_setting.get("ymin"); ymax_pv = pv_setting.get("ymax")
            if values.size and not np.all(np.isnan(values)):
                # The band's edges, not the means: a spike must fit on screen.
                dmin = float(np.nanmin(lo_a)); dmax = float(np.nanmax(hi_a))
            else:
                dmin, dmax = 0.0, 1.0
            autosc  = pv_setting.get("auto_scale", False)
            if not autosc:
                if ymin_pv is not None: dmin = ymin_pv
                if ymax_pv is not None: dmax = ymax_pv
            lo, hi = self._band_ylim(i, n, dmin, dmax, autosc)
            ax.set_ylim(lo, hi)

        self._apply_grids(pvs_for_axes, axes)

        # X-axis (the single shared bottom axis, axes[0])
        ax0 = ax_x
        if _n_times > 1 and _tmax_num is not None and _tmax_num > _tmin_num:
            t_min = mdates.num2date(_tmin_num, tz=timezone.utc)
            t_max = mdates.num2date(_tmax_num, tz=timezone.utc)
            total_seconds = (t_max - t_min).total_seconds()
        else:
            t_min = t_max = (mdates.num2date(_tmin_num, tz=timezone.utc)
                             if _tmin_num is not None else datetime.now(tz=timezone.utc))
            total_seconds = 0

        using_window = self._dt_to > self._dt_from
        if using_window:
            t_min = self._dt_from.astimezone(timezone.utc)
            t_max = self._dt_to.astimezone(timezone.utc)
            total_seconds = (t_max - t_min).total_seconds()

        t_min_local = t_min.astimezone(TZ_PRAGUE) if total_seconds > 0 else datetime.now(TZ_PRAGUE)
        t_max_local = t_max.astimezone(TZ_PRAGUE) if total_seconds > 0 else t_min_local
        same_day    = t_min_local.date() == t_max_local.date()

        # Time stamps: one shared routine, so the full redraw, the live scroll and
        # a zoom all label the axis the same way (see _apply_x_ticks).
        if total_seconds > 0:
            self._apply_x_ticks(ax0, t_min_local, t_max_local)
        else:
            ax0.xaxis.set_major_locator(
                mdates.AutoDateLocator(tz=TZ_PRAGUE, minticks=5, maxticks=8))
        ax0.set_xlabel(
            f"Time (Prague)  {t_min_local.strftime('%Y-%m-%d')}" if same_day else "Time (Prague)",
            fontsize=_fsize)
        if using_window:
            ax0.set_xlim(t_min, t_max)
        elif total_seconds > 0:
            pad = timedelta(seconds=max(total_seconds * 0.02, 5))
            ax0.set_xlim(t_min - pad, t_max + pad)
        self._loading_artists = []
        self._draw_loading_span(ax0, _fsize)
        # Same size as the Y numbers (one notch below the label font), not the
        # full label size: the time stamps are the widest text on the plot, and
        # the two-line form over several days needs the room. Rotation stays 0 —
        # the second line is what buys the space, not slanted text.
        ax0.tick_params(axis="x", which="major", labelsize=_ticksize, rotation=0)
        ax0.tick_params(axis="x", which="minor", length=3, labelsize=0)

        self._draw_ref_lines(ax0, axes, pvs_for_axes, _fsize)

        self._graph_note_artist = None
        self._draw_graph_note(ax0, _use_window, _win_start_ns, _fsize,
                              _axes_px_w, _dpi)

        # (Carry-forward is now drawn as a solid held line in the per-PV loop
        # above, so no separate dotted "last known value" overlay is needed.)

        # Embed canvas — force it to expand and fill the container so the graph
        # border hugs the plot (no white padding band inside the border).
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        canvas.setMinimumSize(0, 0)
        # Every render is timed: with a dozen stacked axes one render is the
        # dearest thing a Live refresh does, and Live paces itself by it.
        _plain_draw = canvas.draw

        def _timed_draw(*a, **k):
            t0 = time.perf_counter()
            try:
                return _plain_draw(*a, **k)
            finally:
                self._last_render_s = time.perf_counter() - t0
        canvas.draw = _timed_draw
        layout = self._graph_container.layout()
        layout.addWidget(canvas)
        self._mpl_canvas = canvas
        self._mpl_figure = fig
        self._graph_axes = axes
        self._install_graph_toolbar(canvas)
        # No right-click menu on the canvas on purpose: right-drag is the zoom, and
        # a context menu would swallow it. Those entries live on the "View ▾" button.

        # Centre the rotated Y-tick numbers exactly on their tick. rotation_mode
        # "anchor" applies ha/va AFTER the 90° rotation, so ha="center"/va="center"
        # pins the label's middle to the tick (default alignment left it hanging
        # to one side). Text.update_from does not carry rotation_mode over, so a
        # tick label matplotlib creates later would lose it — hence the re-run on
        # every draw.
        #
        # It walks the ALREADY CREATED Tick objects (yaxis.majorTicks) instead of
        # get_yticklabels(): the latter re-runs the locators and instantiates the
        # whole minor-tick pool, which cost well over a second per redraw with a
        # dozen stacked axes and made the graph feel frozen. Minor labels are
        # drawn at labelsize 0, so they need no alignment at all.
        def _center_y_ticklabels(_evt=None):
            for _ax in axes:
                _ticks = getattr(_ax.yaxis, "majorTicks", None)
                if _ticks is None:                     # matplotlib internals moved
                    _ticks = _ax.yaxis.get_major_ticks()
                for _tk in _ticks:
                    _lbl = _tk.label1
                    if getattr(_lbl, "_cpva_anchored", False):
                        continue
                    _lbl.set_rotation(90)
                    _lbl.set_rotation_mode("anchor")
                    _lbl.set_horizontalalignment("center")
                    _lbl.set_verticalalignment("center")
                    _lbl._cpva_anchored = True
        _center_y_ticklabels()
        canvas.mpl_connect("draw_event", _center_y_ticklabels)

        # Crosshair. All axes share one plot rectangle, so per-axis cursor lines
        # would land on exactly the same pixels — ONE vertical + ONE horizontal
        # line is drawn instead (this used to be 2 artists per PV, all redrawn on
        # every mouse-move frame). The vertical line spans the axes height, the
        # horizontal one the axes width, both in blended coords so they need no
        # per-axis data conversion.
        self._crosshair_texts = []
        self._x_cursor_ann = None; self._y_cursor_ann = None; self._y_cursor_ann_ax = None
        from matplotlib.transforms import blended_transform_factory as _btf
        _cur_fs = max(5, _fsize + int(_opts.get("cursor_font_delta", 0)))
        # animated=True keeps these out of the normal draw() so the blit
        # background stays clean — otherwise each redraw bakes in a ghost.
        _vl = Line2D([0, 0], [0, 1], color="#888", linewidth=0.8, linestyle="--",
                     transform=ax_x.get_xaxis_transform(), visible=False,
                     animated=True, zorder=9)
        _hl = Line2D([0, 1], [0, 0], color="#888", linewidth=0.8, linestyle="--",
                     transform=ax_x.transAxes, visible=False, animated=True, zorder=9)
        ax_x.add_line(_vl); ax_x.add_line(_hl)
        _vl.set_clip_on(False); _hl.set_clip_on(False)
        self._crosshair_vlines = [_vl]
        self._crosshair_hlines = [_hl]

        # Per-PV value box at the crosshair. Each one is a rounded text box, i.e.
        # a path plus a text layout on every hover frame — with many PVs that is
        # the single most expensive part of the crosshair, so it can be turned off
        # (and is dropped automatically above cursor_boxes_max PVs).
        _want_boxes = (bool(_opts.get("cursor_value_boxes", True))
                       and n <= max(1, int(_opts.get("cursor_boxes_max", 20))))
        for ax_i, ax in enumerate(axes):
            if not _want_boxes:
                self._crosshair_texts.append(None)
                continue
            pv_c  = pvs_for_axes[ax_i] if ax_i < len(pvs_for_axes) else None
            p_col = self._pv_settings.get(pv_c, {}).get("color", "#555") if pv_c else "#555"
            # Value label sits AT the crosshair intersection (vertical cursor line
            # × this PV's trace): both coords are data coords and X is set to the
            # cursor X each frame. ha="left" keeps the box just right of the line.
            ann   = ax.text(0, 0, "", ha="left", va="center", fontsize=_cur_fs,
                            color=p_col, zorder=10, visible=False, transform=ax.transData,
                            clip_on=False, animated=True,
                            bbox=dict(boxstyle="round,pad=0.15", fc="white",
                                      ec=p_col, alpha=0.85, linewidth=0.6))
            self._crosshair_texts.append(ann)

        self._x_cursor_ann = ax_x.text(
            0, -0.01, "", ha="center", va="top", fontsize=_cur_fs,
            color="#333", zorder=10, visible=False, animated=True,
            transform=ax_x.get_xaxis_transform(), clip_on=False,
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#888", alpha=0.9, linewidth=0.6))

        # Y-axis cursor readout: a white, always-legible box where the horizontal
        # cursor line meets the Y axis NEAREST the plot (its spine sits at the plot's
        # left edge → x-fraction closest to 0). Shows the raw cursor Y in that axis.
        self._y_cursor_ann = None; self._y_cursor_ann_ax = None
        if self._graph_spine_xpos:
            _ny_i  = max(range(len(self._graph_spine_xpos)),
                         key=lambda k: self._graph_spine_xpos[k][0])
            _ny_ax = axes[_ny_i]
            _ny_xf = self._graph_spine_xpos[_ny_i][0]
            self._y_cursor_ann_ax = _ny_ax
            self._y_cursor_ann = _ny_ax.text(
                _ny_xf, 0, "", ha="left", va="center", fontsize=_cur_fs,
                color="#333", zorder=11, visible=False, animated=True,
                transform=_btf(_ny_ax.transAxes, _ny_ax.transData), clip_on=False,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#888",
                          alpha=1.0, linewidth=0.6))

        self._graph_raw_np = [
            (np.asarray(t, dtype=float), np.asarray(v, dtype=float))
            for t, v in self._graph_raw]

        self._mouse_pending = False; self._blit_bg = None
        canvas.mpl_connect("draw_event", self._on_canvas_draw)
        # The graph gets shorter whenever the statistics strip or the settings
        # table opens; the time-axis title has to stay readable through that.
        canvas.mpl_connect("resize_event", self._keep_x_label_visible)
        self._crosshair_cid = canvas.mpl_connect("motion_notify_event", self._on_graph_mouse_move)
        # The loupe disappears the moment the mouse leaves the graph.
        canvas.mpl_connect("figure_leave_event",
                           lambda _e: getattr(self, "_loupe", None) is not None
                           and self._loupe.hide())

        try:
            # Left button only: without button= matplotlib takes every button,
            # so the right-drag zoom below also started a statistics region.
            self._span_selector = _EdgeSpanSelector(
                ax_x, self._on_span_select, direction="horizontal",
                useblit=False, button=1,
                props=dict(alpha=0.15, facecolor="#1565C0"), interactive=True)
        except Exception:
            self._span_selector = None
        try:
            self._zoom_selector = _EdgeSpanSelector(
                ax_x, self._on_zoom_select, direction="horizontal",
                useblit=False, button=3, props=dict(alpha=0.20, facecolor="#FF6600"))
        except Exception:
            self._zoom_selector = None

        # The zoom the user had before this rebuild (see _plot_graph). Put back
        # before the xlim callback is connected: the detail for that range is
        # already spliced in, nothing needs to be read again.
        _keep = getattr(self, "_keep_xlim", None)
        self._keep_xlim = None
        if _keep and self._user_zoomed:
            self._push_graph_view()     # Home / Back still reach the full window
            ax_x.set_xlim(*_keep)
            self._push_graph_view()
            try:
                self._retick_from_current_xlim()
            except Exception:
                pass

        # Any change of the time range (right-drag, toolbar zoom, pan, Back,
        # Home) may call for finer data — see _maybe_load_zoom_detail.
        ax_x.callbacks.connect("xlim_changed", self._on_graph_xlim_changed)

        # Put the remembered selection back: the band on the new selector, the
        # numbers recomputed from the new arrays.
        self._restore_selection_band()
        if self._sel_range:
            self._recompute_stats()

        # A graph is on screen, so "Load data first" / "No PVs loaded" / an
        # earlier error no longer apply.
        if self._lbl_graph_info.text() in _GRAPH_STALE_NOTES:
            self._lbl_graph_info.setText("")

        canvas.draw()
        self._refresh_axis_settings_tv()

    def _apply_grids(self, pvs=None, axes=None):
        """Grids, one per ticked channel. Every channel has its own Y scale, so a
        single shared grid could only ever line up with one of them — which is
        why ticking the second, third, … Grid box used to do nothing visible.
        Each ticked channel gets a horizontal grid on ITS OWN axis, in its own
        colour and its own line style, so several can be read apart at once.
        The vertical time lines stay single: all channels share one time axis.

        Touches only the grid lines, so the Grid tick box switches them on the
        graph as it is — no rebuild, the view and the zoom stay where they are."""
        pvs  = list(self._graph_pvs if pvs is None else pvs)
        axes = list(self._graph_axes if axes is None else axes)
        if not axes:
            return
        _grid_pvs = [pv for pv in pvs
                     if self._pv_settings.get(pv, {}).get("grid", False)]
        # Remembered so the tick box's tooltip can name the style a channel
        # ACTUALLY got. Working it out again from _pv_order would drift by one
        # whenever a shown channel carries no numeric data (it gets no axis here).
        self._grid_pvs_drawn = list(_grid_pvs)
        for i, (pv, ax) in enumerate(zip(pvs, axes)):
            # Grids must never sit on top of the traces. twinx axes are drawn in
            # order, so a later channel's grid would otherwise cross an earlier
            # channel's line.
            ax.set_axisbelow(True)
            ax.yaxis.grid(False)
            if pv not in _grid_pvs:
                continue
            _st = self._grid_style_for(pv, _grid_pvs)
            _c  = self._pv_settings.get(pv, {}).get(
                "color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])
            ax.yaxis.grid(True, which="major", linestyle=_st, linewidth=0.9,
                          color=_c, alpha=0.35, zorder=0)
        # One shared set of vertical time lines, faint grey, drawn on the axis that
        # owns the time axis. The style arguments are only passed when the lines are
        # actually wanted: matplotlib switches a grid ON regardless of the first
        # argument as soon as any line property comes with it, so passing them
        # alongside False would draw a grid nobody asked for.
        if _grid_pvs:
            axes[0].xaxis.grid(True, which="major", linestyle="-",
                               linewidth=0.8, color="#9e9e9e", alpha=0.30, zorder=0)
        else:
            axes[0].xaxis.grid(False)

    def _regrid_in_place(self):
        """Grid settings changed: redraw only the grid lines."""
        if self._mpl_canvas is None or not self._graph_axes:
            return
        self._apply_grids()
        self._blit_bg = None
        self._mpl_canvas.draw_idle()

    def _update_graph_data(self):
        if self._mpl_canvas is None or not self._graph_lines:
            return

        # Carry-forward window edges (CS-Studio style): hold each PV's last value
        # flat out to "now", and show a PV with no in-window data at its last-known
        # value across the whole window instead of dropping the trace.
        now_utc   = datetime.now().astimezone(timezone.utc)
        span      = self._live_window_span
        win_start = (now_utc - span) if (span and span.total_seconds() > 0) else None
        win_start_ns = dt_to_ns(win_start) if win_start is not None else None
        now_ns_      = dt_to_ns(now_utc)
        avg_target   = self._avg_target_points()
        # The conditions apply here too: this path reads the samples, not the
        # filtered table rows, and used to put every rejected shot back on the
        # graph at the next live refresh.
        cond_tables  = self._condition_tables()

        t_lo_num = t_hi_num = None
        new_raw   = []
        new_env   = []
        for i, (pv, ax, pv_lines) in enumerate(zip(self._graph_pvs, self._graph_axes, self._graph_lines)):
            ts_a, values = _samples_to_ns_arrays(self._samples_by_pv.get(pv, []))
            if cond_tables and ts_a.size:
                _keep = self._condition_mask(ts_a, cond_tables)
                ts_a, values = ts_a[_keep], values[_keep]
            # Thin (in numpy) BEFORE anything else. The live fast path used to
            # push EVERY raw sample to matplotlib on every ~1 s refresh with no
            # cap, so a 100k+ point window rebuilt 100k datetimes and redrew 100k
            # vertices per PV per tick — the source of the judder. Binning to the
            # same target the full replot uses keeps each refresh light, and
            # every bin keeps its min and max, so no spike is averaged away.
            ts_a, values, lo_a, hi_a = self._thin_trace(pv, ts_a, values, avg_target)
            if not ts_a.size and (win_start_ns is None
                                  or (self._pre_window_vals.get(pv) is None
                                      and self._last_good_value(pv, win_start_ns) is None)):
                new_raw.append(self._graph_raw[i] if i < len(self._graph_raw)
                               else (np.empty(0), np.empty(0)))
                new_env.append(self._graph_env[i] if i < len(self._graph_env)
                               else (np.empty(0), np.empty(0)))
                continue
            # Extend to the window edges so the trace spans start → now.
            ts_a, values, lo_a, hi_a = self._hold_to_window(
                pv, ts_a, values, lo_a, hi_a,
                win_start_ns if win_start_ns is not None else ts_a[0], now_ns_)
            new_env.append((lo_a, hi_a))
            # Arithmetic ns → date-number conversion (no datetime objects, see
            # _ns_to_num): this runs for every PV on every live refresh.
            times_num = _ns_to_num(ts_a)
            if times_num.size:
                _lo = float(times_num[0]); _hi = float(times_num[-1])
                t_lo_num = _lo if t_lo_num is None else min(t_lo_num, _lo)
                t_hi_num = _hi if t_hi_num is None else max(t_hi_num, _hi)
            new_raw.append((times_num, values))
            pv_setting = self._pv_settings.get(pv, {})
            pv_smooth  = pv_setting.get("smooth", None)
            eff_smooth = pv_smooth if pv_smooth is not None else 1
            if len(pv_lines) == 2:
                pv_lines[0].set_data(times_num, values)
                sm = (_moving_avg_np(values, eff_smooth)
                      if eff_smooth > 1 and values.size >= eff_smooth else values)
                pv_lines[1].set_data(times_num, sm)
            elif pv_lines:
                pv_lines[0].set_data(times_num, values)
            self._replace_band(i, ax, times_num, lo_a, hi_a)
            # Re-apply this PV's band (or full height if Autoscale on).
            if values.size and not np.all(np.isnan(values)):
                dmin = float(np.nanmin(lo_a)); dmax = float(np.nanmax(hi_a))
                autosc = pv_setting.get("auto_scale", False)
                if not autosc:
                    ymin_pv = pv_setting.get("ymin"); ymax_pv = pv_setting.get("ymax")
                    if ymin_pv is not None: dmin = ymin_pv
                    if ymax_pv is not None: dmax = ymax_pv
                lo, hi = self._band_ylim(i, len(self._graph_pvs), dmin, dmax, autosc)
                ax.set_ylim(lo, hi)

        self._graph_raw = new_raw
        self._graph_env = new_env
        if self._graph_axes:
            ax0 = self._graph_axes[0]
            live_span = self._live_window_span
            # Auto-scrolling live window: re-derive the X range AND rebuild its
            # ticks for "now" on every refresh. Previously only xlim scrolled while
            # the FixedLocator tick positions stayed frozen at the initial window —
            # once "now" moved past them the axis showed no timestamps at all (the
            # reported blank time axis). When zoomed in, leave the user's view be.
            if live_span and live_span.total_seconds() > 0 and not self._user_zoomed:
                x_lo, x_hi = now_utc - live_span, now_utc
                ax0.set_xlim(x_lo, x_hi)
                lo_l = x_lo.astimezone(TZ_PRAGUE); hi_l = x_hi.astimezone(TZ_PRAGUE)
            elif t_hi_num is not None and t_hi_num > t_lo_num:
                lo_l = mdates.num2date(t_lo_num, tz=TZ_PRAGUE)
                hi_l = mdates.num2date(t_hi_num, tz=TZ_PRAGUE)
            else:
                lo_l = hi_l = None

            if lo_l is not None and (hi_l - lo_l).total_seconds() > 0:
                self._apply_x_ticks(ax0, lo_l, hi_l)

        # Keep the crosshair snap data in sync, and force a fresh blit background
        # on the next draw so the cursor boxes never ghost over stale pixels.
        self._graph_raw_np = [
            (np.asarray(t, dtype=float), np.asarray(v, dtype=float))
            for t, v in self._graph_raw]
        # Live data just changed under the selection — refresh its numbers instead
        # of leaving them describing samples that have since been trimmed away.
        if self._sel_range:
            self._recompute_stats()
        self._blit_bg = None
        self._mpl_canvas.draw_idle()

    def _on_canvas_draw(self, *_):
        if self._mpl_canvas is None or self._mpl_figure is None: return
        for vl in self._crosshair_vlines: vl.set_visible(False)
        for hl in self._crosshair_hlines: hl.set_visible(False)
        for ann in self._crosshair_texts:
            if ann is not None: ann.set_visible(False)
        if self._x_cursor_ann is not None: self._x_cursor_ann.set_visible(False)
        if getattr(self, "_y_cursor_ann", None) is not None: self._y_cursor_ann.set_visible(False)
        self._blit_bg = self._mpl_canvas.copy_from_bbox(self._mpl_figure.bbox)
        # A stray full redraw (matplotlib schedules one when artists go stale,
        # e.g. while the mouse sits still) repaints without the animated
        # crosshair and would make it vanish after a second or two. If the
        # cursor is still parked over the plot, re-assert the overlay now.
        # Guard against a stale event left over from a previous figure (a
        # replot fires draw_event while _mouse_last_event still points at the
        # old canvas — redrawing then hits detached artists → dpi/None crash).
        ev = self._mouse_last_event
        if (getattr(self, "_cursor_active", False)
                and ev is not None
                and getattr(ev, "canvas", None) is self._mpl_canvas
                and not getattr(self, "_in_cursor_redraw", False)):
            self._in_cursor_redraw = True
            try:
                self._process_mouse_move()
            except Exception:
                pass
            finally:
                self._in_cursor_redraw = False

    def _on_graph_mouse_move(self, event):
        self._mouse_last_event = event
        if not self._mouse_pending:
            self._mouse_pending = True
            # Adaptive throttle: a crosshair frame with a dozen PVs (each with its
            # own value box) can take longer than a fixed 16 ms budget, and then
            # every finished frame is immediately followed by the next queued one —
            # the GUI thread never idles and the whole app feels stuck. Waiting
            # roughly as long as the last frame actually took keeps the cursor
            # responsive while leaving time for everything else.
            QTimer.singleShot(int(self._cursor_frame_ms), self._process_mouse_move)

    def _process_mouse_move(self):
        # Never let a hover-frame render escape as an exception: a replot/resize
        # can tear the figure down between the queued move and this call,
        # leaving detached artists (matplotlib then raises 'NoneType has no
        # attribute dpi' while drawing a Text). It's harmless — just swallow it.
        _t0 = time.perf_counter()
        try:
            self._process_mouse_move_impl()
        except Exception:
            self._mouse_pending = False
        # Feed the measured frame time back into the throttle (see
        # _on_graph_mouse_move), smoothed so one slow frame can't jam the cursor.
        _ms = (time.perf_counter() - _t0) * 1000.0
        self._cursor_frame_ms = min(80.0, max(
            16.0, 0.7 * self._cursor_frame_ms + 0.3 * _ms * 1.2))

    def _process_mouse_move_impl(self):
        self._mouse_pending = False
        event = self._mouse_last_event
        if event is None or self._mpl_canvas is None: return

        canvas = self._mpl_canvas
        bg     = self._blit_bg

        if getattr(self, "_loupe_on", False):
            try:
                self._update_loupe(event)
            except Exception:
                pass

        if event.inaxes is None:
            self._cursor_active = False
            if bg is not None:
                canvas.restore_region(bg); canvas.blit(self._mpl_figure.bbox)
            else:
                canvas.draw_idle()
            return

        x = event.xdata
        if x is None: return
        x_f = float(x); disp_x = event.x; disp_y = event.y
        raw_np = self._graph_raw_np

        if bg is not None: canvas.restore_region(bg)

        # One vertical + one horizontal cursor line for the whole plot (all axes
        # share the rectangle). The horizontal one lives in axes coordinates, so
        # the mouse Y only has to be converted once.
        for vl in self._crosshair_vlines:
            vl.set_xdata([x_f, x_f]); vl.set_visible(True)
            if bg is not None: vl.axes.draw_artist(vl)
        _ax0 = self._graph_axes[0] if self._graph_axes else None
        if _ax0 is not None:
            try:
                y_frac = float(_ax0.transAxes.inverted().transform((disp_x, disp_y))[1])
            except Exception:
                y_frac = None
            for hl in self._crosshair_hlines:
                if y_frac is None:
                    hl.set_visible(False); continue
                hl.set_ydata([y_frac, y_frac]); hl.set_visible(True)
                if bg is not None: hl.axes.draw_artist(hl)

        _boxes = []          # (axis, value box, height it wants) — placed below
        for ax_i, ax in enumerate(self._graph_axes):
            pv = self._graph_pvs[ax_i] if ax_i < len(self._graph_pvs) else None
            snap_val = None
            snap_span = None
            if raw_np and ax_i < len(raw_np):
                arr, vals = raw_np[ax_i]
                if len(arr):
                    # The curves are drawn with drawstyle="steps-post", i.e. a
                    # sample's value holds until the *next* sample. So report the
                    # last sample at or before the cursor, never the nearest one:
                    # snapping to the nearest sample would show the next value
                    # from the midpoint of a gap onwards, while the line still
                    # draws the previous one (a step that looks invisible until
                    # the cursor reaches it). arr (times) is sorted ascending, so
                    # binary-search instead of scanning every frame.
                    pos = int(np.searchsorted(arr, x_f, side="right"))
                    idx = pos - 1 if pos > 0 else 0
                    snap_val = float(vals[idx])
                    # A thinned point stands for an interval: say what really
                    # happened in it, lowest to highest, not just its mean.
                    env = self._graph_env[ax_i] if ax_i < len(self._graph_env) else None
                    if env is not None and idx < len(env[0]):
                        e_lo, e_hi = float(env[0][idx]), float(env[1][idx])
                        if e_hi > e_lo:
                            snap_span = (e_lo, e_hi)
            if pv and pv in self._pv_settings:
                self._pv_settings[pv]["cursor_val"] = (
                    _fmt_cursor_value(snap_val) if snap_val is not None else "")

            if ax_i < len(self._crosshair_texts):
                ann = self._crosshair_texts[ax_i]
                if ann is not None:
                    # Anchor the label at the crosshair intersection: X on the
                    # vertical cursor line (x_f), Y at the PV's own value at the
                    # hovered X (snap_val) — not the arbitrary raw mouse Y.
                    has_data = snap_val is not None
                    val_str = _fmt_cursor_value(snap_val) if has_data else "—"
                    if snap_span is not None:
                        val_str += (f"  (min {_fmt_cursor_value(snap_span[0])}"
                                    f" · max {_fmt_cursor_value(snap_span[1])})")
                    ann.set_text(f" {val_str}")
                    if has_data:
                        y_ann = snap_val
                    else:
                        # No sample under the cursor: park the box on the mouse
                        # row (y_frac is the mouse Y in axes fractions).
                        _ylo, _yhi = ax.get_ylim()
                        y_ann = _ylo + (_yhi - _ylo) * (y_frac if y_frac is not None else 0.5)
                    # The final height is decided once every box is known, so
                    # boxes that would land on top of each other can be spread
                    # apart (see _place_cursor_boxes).
                    _boxes.append((ax, ann, y_ann))

        if _boxes:
            self._place_cursor_boxes(_boxes, x_f, bg)

        if self._x_cursor_ann is not None:
            try:
                dt_cursor = mdates.num2date(x_f, tz=TZ_PRAGUE)
                # The date used to be printed at the top right of the graph;
                # that label is gone, so a view spanning more than one day
                # puts it in this box instead.
                _xl = self._x_cursor_ann.axes.get_xlim()
                if int(min(_xl)) != int(max(_xl)) or (max(_xl) - min(_xl)) >= 1.0:
                    ts_str = dt_cursor.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    ts_str = dt_cursor.strftime("%H:%M:%S")
                self._x_cursor_ann.set_position((x_f, -0.01))
                self._x_cursor_ann.set_text(ts_str)
                # Centred under the cursor normally, but at either end of the
                # axis half the time would hang outside the window, so the box
                # tucks itself in against the edge it is nearest.
                self._x_cursor_ann.set_ha(
                    self._edge_align(self._x_cursor_ann, x_f, centred=True))
                self._x_cursor_ann.set_visible(True)
                if bg is not None: self._x_cursor_ann.axes.draw_artist(self._x_cursor_ann)
            except Exception:
                dt_cursor = None

        if self._y_cursor_ann is not None and self._y_cursor_ann_ax is not None:
            try:
                yax   = self._y_cursor_ann_ax
                y_cur = float(yax.transData.inverted().transform((disp_x, disp_y))[1])
                # The number is the one under the mouse, but the box itself is
                # kept inside the plot rectangle — without this it slid out past
                # the top and bottom spines along with the mouse.
                _bb   = yax.get_window_extent()
                _pad  = self._y_cursor_ann.get_fontsize() * self._mpl_figure.dpi / 72.0
                _dy   = min(max(disp_y, _bb.y0 + _pad), _bb.y1 - _pad)
                y_box = float(yax.transData.inverted().transform((disp_x, _dy))[1])
                xf    = self._y_cursor_ann.get_position()[0]
                self._y_cursor_ann.set_position((xf, y_box))
                self._y_cursor_ann.set_text(f" {_fmt_cursor_value(y_cur)}")
                self._y_cursor_ann.set_visible(True)
                if bg is not None: yax.draw_artist(self._y_cursor_ann)
            except Exception:
                pass

        self._cursor_active = True
        if bg is not None: canvas.blit(self._mpl_figure.bbox)
        else: canvas.draw_idle()

        # Push the per-PV cursor values into the axis-settings table only once the
        # mouse settles (rewriting QTableWidget items every move stutters).
        self._cursor_tbl_timer.start(120)

    def _place_cursor_boxes(self, boxes, x_f, bg):
        """Put every value box exactly where the cursor line crosses its own
        trace, and keep boxes from covering each other.

        Boxes used to be pushed onto two alternating rows, which moved half of
        them away from their curve even when there was nothing in the way. Here
        each box starts on its own curve; only boxes that would really overlap
        are treated as one stack and spread out around the average height the
        stack asked for. So a box moves only when it has to, and only as far as
        it has to.
        """
        items = []
        for ax, ann, y_data in boxes:
            try:
                y_px = float(ax.transData.transform((0.0, y_data))[1])
            except Exception:
                y_px = float("nan")
            if not np.isfinite(y_px):
                ann.set_visible(False)
                continue
            # Box height from the font size (every box shares it): one text line
            # ≈ 1.2 × font size, plus the rounded frame, plus a little air so two
            # stacked boxes do not touch.
            fs_px = ann.get_fontsize() * self._mpl_figure.dpi / 72.0
            items.append((ax, ann, y_px, fs_px * 1.5 + 2.0))
        if not items:
            return
        items.sort(key=lambda it: it[2])

        # Walk the boxes bottom to top. Each one starts as its own stack; while a
        # stack still runs into the one below it the two are merged and the merged
        # stack is re-centred on the average of the heights its members wanted.
        stacks = []   # [bottom px, total height px, sum of wanted px, members]
        for it in items:
            stacks.append([it[2] - it[3] / 2.0, it[3], it[2], [it]])
            while len(stacks) > 1 and stacks[-2][0] + stacks[-2][1] > stacks[-1][0]:
                top = stacks.pop(); low = stacks[-1]
                low[1] += top[1]; low[2] += top[2]; low[3] += top[3]
                low[0] = low[2] / len(low[3]) - low[1] / 2.0

        try:
            _bb  = items[0][0].get_window_extent()
            lo_px, hi_px = float(_bb.y0), float(_bb.y1)
            x0_px, x1_px = float(_bb.x0), float(_bb.x1)
        except Exception:
            lo_px = hi_px = x0_px = x1_px = None

        # Which side of the cursor line the boxes grow to. They are drawn without
        # clipping, so at the right-hand edge a box that grows rightwards runs off
        # the plot and off the window — which is where the numbers used to
        # disappear. Flip the whole set to the left there, and only there, so the
        # boxes stay together and stop jumping about.
        try:
            x_px = float(items[0][0].transData.transform((x_f, 0.0))[0])
        except Exception:
            x_px = None
        side = "left"
        if x_px is not None and x1_px is not None:
            widest = max(self._cursor_box_width(ann) for _a, ann, _y, _h in items)
            if x_px + widest > x1_px and (x_px - widest) >= x0_px:
                side = "right"      # ha="right" = the text ends at the cursor

        for st in stacks:
            # Keep the stack inside the plot rectangle (unless it is taller).
            if lo_px is not None and (hi_px - lo_px) > st[1]:
                st[0] = min(max(st[0], lo_px), hi_px - st[1])
            y = st[0]
            for ax, ann, _wanted, h in st[3]:
                try:
                    y_data = float(ax.transData.inverted().transform(
                        (0.0, y + h / 2.0))[1])
                except Exception:
                    ann.set_visible(False); y += h; continue
                y += h
                # The artists live between frames, so the side has to be set every
                # time, not only when it changes.
                ann.set_ha(side)
                ann.set_text((" " if side == "left" else "")
                             + ann.get_text().strip()
                             + ("" if side == "left" else " "))
                ann.set_position((x_f, y_data))
                ann.set_visible(True)
                if bg is not None:
                    ax.draw_artist(ann)

    def _edge_align(self, ann, x_f, centred=False):
        """Which way a cursor box should grow so it stays inside the plot.

        "left" means the text starts at the cursor and runs right; "right" means
        it ends there and runs left. `centred` boxes stay centred until an edge
        is close enough to push them off the window.
        """
        try:
            ax = ann.axes
            bb = ax.get_window_extent()
            x_px = float(ax.transData.transform((x_f, 0.0))[0])
        except Exception:
            return "center" if centred else "left"
        w = self._cursor_box_width(ann)
        head = w / 2.0 if centred else w
        if x_px + head > bb.x1:
            return "right"
        if centred and x_px - head < bb.x0:
            return "left"
        return "center" if centred else "left"

    def _cursor_box_width(self, ann):
        """Roughly how wide a cursor value box is, in pixels.

        Measured from the text length and the font size, the same cheap way the
        box height is estimated above. Asking matplotlib for the real extent
        would need a renderer on every mouse move, and the estimate only has to
        be good enough to decide which side of the cursor the box goes on.
        """
        fs_px = ann.get_fontsize() * self._mpl_figure.dpi / 72.0
        return (len(ann.get_text()) + 1) * fs_px * 0.62 + 8.0

    def _flush_cursor_table(self):
        """Write the latest cursor values (already cached in _pv_settings by the
        hover handler) into the axis-settings table. Coalesced via a timer."""
        try:
            val_idx = list(self._axis_tv_cols).index("cursor_val")
        except ValueError:
            return
        # These programmatic updates must not trip the auto-apply itemChanged
        # handler (it would replot on every 120 ms cursor tick).
        row_pv = getattr(self, "_axis_row_pv", [])
        self._axis_tv.blockSignals(True)
        try:
            for row in range(self._axis_tv.rowCount()):
                # Which signal a row belongs to comes from _axis_row_pv, never from
                # a fixed column number — columns can be dragged into any order.
                pv = row_pv[row] if row < len(row_pv) else None
                if not pv:
                    continue
                cv = self._pv_settings.get(pv, {}).get("cursor_val", "")
                item = self._axis_tv.item(row, val_idx)
                if item:
                    item.setText(cv)
        finally:
            self._axis_tv.blockSignals(False)

    _AXIS_MEASURED_COLS = ("unit", "last", "min", "max", "mean", "count")

    def _flush_axis_measured(self):
        """Refresh the read-only Unit / Last / Min / Max / Mean / Count cells.

        Only writes columns that are actually on screen, and only when the table
        already has rows — during Live this runs on every table refresh.
        """
        tv = getattr(self, "_axis_tv", None)
        if tv is None or not tv.rowCount():
            return
        _COL = list(self._axis_tv_cols)
        idxs = [(c, _COL.index(c)) for c in self._AXIS_MEASURED_COLS
                if not tv.isColumnHidden(_COL.index(c))]
        if not idxs:
            return
        row_pv = getattr(self, "_axis_row_pv", [])
        tv.blockSignals(True)
        try:
            for row in range(tv.rowCount()):
                pv = row_pv[row] if row < len(row_pv) else None
                if not pv:
                    continue
                stats = self._pv_stats(pv)
                for col, j in idxs:
                    item = tv.item(row, j)
                    if item:
                        item.setText(stats.get(col, ""))
        finally:
            tv.blockSignals(False)

    def _clear_graph(self):
        if self._mpl_canvas is not None:
            layout = self._graph_container.layout()
            if layout:
                layout.removeWidget(self._mpl_canvas)
            self._mpl_canvas.setParent(None)
            self._mpl_canvas.deleteLater()
            self._mpl_canvas = None
        # The toolbar belongs to that canvas — drop it with it, or its buttons act
        # on a figure that no longer exists.
        if getattr(self, "_graph_toolbar", None) is not None:
            try:
                self._graph_toolbar.setParent(None)
                self._graph_toolbar.deleteLater()
            except Exception:
                pass
            self._graph_toolbar = None
        self._mpl_figure  = None
        self._graph_axes  = []
        self._graph_lines = []
        self._graph_bands = []
        self._graph_env   = []
        self._graph_base  = []
        self._graph_pvs   = []
        self._graph_raw   = []
        self._graph_raw_np = []
        self._crosshair_vlines = []
        self._crosshair_hlines = []
        self._crosshair_texts  = []
        self._x_cursor_ann = None
        self._y_cursor_ann = None
        self._y_cursor_ann_ax = None
        self._blit_bg      = None
        self._loading_artists = []
        self._graph_note_artist = None
        self._cursor_active = False   # no crosshair to re-assert on next draw
        self._clear_stats()

    def _apply_font_size(self):
        # The toolbar spin and the Graph-settings dialog edit the same value.
        try:
            self._graph_opts["font_size"] = int(self._font_size_spin.value())
        except Exception:
            pass
        if self._mpl_figure and self._samples_by_pv:
            self._schedule_replot()   # coalesce rapid spinner clicks into one redraw

    # ── Graph settings dialog ───────────────────────────────────────────────

    def _open_graph_settings_dialog(self):
        dlg = _GraphSettingsDialog(self._graph_opts, self)
        dlg.applied.connect(self._apply_graph_opts)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_graph_opts(dlg.result_opts)
        elif dlg.applied_once:
            # Cancel must undo anything a live "Apply" already painted.
            self._apply_graph_opts(dlg.original_opts)

    def _open_margins_dialog(self):
        """Margins and spacing — a small window that applies every change at
        once. Not modal: the graph stays usable while it is open."""
        dlg = getattr(self, "_margins_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            return
        dlg = _MarginsDialog(self._graph_opts, self)
        dlg.changed.connect(self._apply_graph_opts)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.destroyed.connect(lambda *_: setattr(self, "_margins_dlg", None))
        self._margins_dlg = dlg
        dlg.show()

    def _apply_graph_opts(self, opts: dict):
        if all(self._graph_opts.get(k) == v for k, v in opts.items()):
            return                                  # nothing to repaint
        self._graph_opts.update(opts)
        self.config["graph_opts"] = dict(self._graph_opts)
        fs = int(self._graph_opts.get("font_size", 11))
        if self._font_size_spin.value() != fs:
            self._font_size_spin.blockSignals(True)
            self._font_size_spin.setValue(fs)
            self._font_size_spin.blockSignals(False)
        if self._samples_by_pv:
            self._schedule_replot(60)

    # ── Span / zoom handlers ────────────────────────────────────────────────

    def _clear_stats(self):
        """Empty the selection-statistics strip and hide it."""
        flow = getattr(self, "_stats_flow", None)
        if flow is not None:
            while flow.count():
                item = flow.takeAt(0)
                w = item.widget() if item else None
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        if getattr(self, "_stats_scroll", None) is not None:
            self._stats_scroll.hide()
        if getattr(self, "_stats_title", None) is not None:
            self._stats_title.hide()
        if getattr(self, "_stats_head", None) is not None:
            self._stats_head.hide()
        # What the cards showed, for the saved image: (name, colour, stats).
        self._stats_rows = []

    def _set_stats_mode(self, mode):
        """All / One row / Hide switch above the statistics cards."""
        self._stats_mode = mode
        self.config["stats_strip_mode"] = mode
        for k, b in getattr(self, "_stats_mode_btns", {}).items():
            b.setChecked(k == mode)
        self._fit_stats_strip()

    def _fit_stats_strip(self):
        """Make the statistics strip exactly as tall as the cards it shows.

        All → every row of cards; One row → the first row only (the rest is
        one scroll away); Hide → no cards, only the heading. Never more than
        about half of the graph pane: beyond that a scroll bar appears, so many
        PVs cannot squeeze the graph to nothing."""
        scroll = getattr(self, "_stats_scroll", None)
        flow = getattr(self, "_stats_flow", None)
        head = getattr(self, "_stats_head", None)
        if scroll is None or flow is None:
            return
        has_cards = flow.count() > 0 and bool(getattr(self, "_stats_rows", None))
        if head is not None:
            head.setVisible(has_cards)
        if not has_cards or self._stats_mode == "hide":
            scroll.hide()
            return
        sb_w = scroll.verticalScrollBar().sizeHint().width()
        width = max(50, scroll.width() if scroll.isVisible() else
                    self._stats_top_pane.width())
        full = flow.heightForWidth(width)
        if full > 0 and self._stats_mode == "row":
            want = flow.first_row_height(width - sb_w)
        else:
            want = full
        cap = max(60, int(self._stats_top_pane.height() * 0.45))
        # Two rows of cards are always shown whole (up to most of the pane):
        # a second row cut in half behind a scroll bar hid half the numbers.
        if (self._stats_mode != "row" and full > cap
                and full <= flow.rows_height(width, 2)):
            cap = max(cap, min(full, int(self._stats_top_pane.height() * 0.7)))
        if want > cap or (self._stats_mode == "row" and want < full):
            # A scroll bar takes width, so the cards re-wrap a little narrower.
            want = min(cap, flow.first_row_height(width - sb_w)
                       if self._stats_mode == "row" else
                       flow.heightForWidth(width - sb_w))
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        else:
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(max(1, want))
        was_hidden = not scroll.isVisible()
        scroll.show()
        if was_hidden:
            # The cards were measured before they were ever shown, when their
            # small fonts were not applied yet — so they measured taller, and
            # the strip kept an empty band under them. Measure once more after
            # they are on screen.
            QTimer.singleShot(0, self._fit_stats_strip)

    def _make_stat_card(self, disp_name, color, stats):
        """Build one compact per-PV statistics card for the selection strip."""
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:#ffffff;border:1px solid #cfcfcf;"
            f"border-top:3px solid {color};border-radius:4px;}}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(7, 4, 7, 5)
        cl.setSpacing(1)

        title = QLabel(disp_name)
        title.setStyleSheet(
            f"border:none;color:{color};font-weight:700;font-size:11px;")
        title.setToolTip(disp_name)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(0)
        rows = [
            ("N",   stats["n"],   "Number of samples inside the selected region."),
            ("Avg", stats["avg"], "Average (arithmetic mean) of the selected values."),
            ("Std", stats["std"], "Standard deviation — how much the values scatter "
                                  "around the average (population σ), as a "
                                  "percentage of the average."),
            ("Min", stats["min"], "Smallest value in the selection."),
            ("Max", stats["max"], "Largest value in the selection."),
            ("P-P", stats["ptp"], "Peak-to-peak = Max − Min (total spread of the "
                                  "values), as a percentage of the average."),
        ]
        for r, (lbl, val, tip) in enumerate(rows):
            k = QLabel(lbl + ":")
            k.setStyleSheet("border:none;color:#666;font-size:10px;")
            k.setToolTip(tip)
            v = QLabel(val)
            v.setStyleSheet("border:none;color:#111;font-size:10px;font-weight:600;")
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            v.setToolTip(tip)
            # Selectable so the number can be copied with the mouse (Ctrl+C).
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setCursor(Qt.CursorShape.IBeamCursor)
            grid.addWidget(k, r, 0)
            grid.addWidget(v, r, 1)
        cl.addLayout(grid)

        # A "Copy" action on right-click / button copies the whole card as a
        # tab-separated block that pastes cleanly into Excel / a spreadsheet.
        tsv = (disp_name + "\n"
               + "\n".join(f"{lbl}\t{val}" for lbl, val, _ in rows))
        card.setToolTip("Right-click to copy these statistics")
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda _pos, t=tsv: self._copy_stats_text(t))
        return card

    def _copy_stats_text(self, text):
        QApplication.clipboard().setText(text)
        self._lbl_status.setText("Statistics copied to clipboard.")

    def _on_span_select(self, xmin, xmax):
        # Left-drag selection over the plot → remember the region, then compute.
        # Only the REGION is remembered (absolute time); the numbers are always
        # derived from it, so they survive a replot and follow new live data.
        if xmax - xmin < 1e-9 or not self._graph_pvs:
            self._clear_selection()
            return
        # Dragged out past the right-hand edge → a "live" selection: the start
        # stays where it was put, the end keeps moving onto the newest sample.
        # The selector stops at the last position the mouse had inside the plot,
        # so "at the edge" is a few pixels' tolerance, not an exact match. A view
        # zoomed into the past (newest data off to the right) is not "the end".
        self._sel_live = False
        try:
            ax0 = self._graph_axes[0]
            v_lo, v_hi = ax0.get_xlim()
            w_px = max(1.0, float(ax0.bbox.width))
            tol = (v_hi - v_lo) * max(8.0 / w_px, 0.01)
            newest = self._newest_sample_num()
            if xmax >= v_hi - tol and (newest is None or newest <= v_hi + tol):
                self._sel_live = True
        except Exception:
            pass
        xmin, xmax = self._snap_sel_edges(float(xmin), float(xmax))
        self._sel_range = (float(xmin), float(xmax))
        if self._span_selector is not None:
            try:
                self._span_selector.extents = self._sel_range
            except Exception:
                pass
        # A new region: its own copy of the samples starts afresh.
        self._sel_samples = {}
        self._sel_restart_note = ""
        self._recompute_stats()

    # ── Selection edges: snapping, typing, one shot at a time ────────────────

    def _all_shot_times_ns(self):
        """(first, last) sample time of every shot in the loaded window that
        counts (see _shot_times). The channels of one shot are archived a few
        ms apart; samples closer than the merge gap are one shot, as in the
        table rows."""
        sbp, pre = self._samples_by_pv, self._pre_window_vals
        t = self._shot_times(sbp, pre, self._condition_tables(sbp, pre),
                             -np.inf, np.inf)
        if not t.size:
            return t, t
        new = np.concatenate(([True], np.diff(t) > SAMPLE_HOLD_MIN_GAP_MS * 1e6))
        firsts = t[new]
        lasts = t[np.concatenate((new[1:], [True]))]
        return firsts, lasts

    def _snap_sel_edges(self, xmin, xmax):
        """Put a dragged edge exactly on the start (or end) of a burst of shots
        when it was let go within a few pixels of one. On a long graph a pixel
        is seconds wide, so "the first shot after that short pause" could not
        be hit by hand. A pause is at least 2 s, 3 px and three times the
        usual spacing of the shots."""
        try:
            ax0 = self._graph_axes[0]
            v_lo, v_hi = ax0.get_xlim()
            px_num = (v_hi - v_lo) / max(1.0, float(ax0.bbox.width))
            firsts, lasts = self._all_shot_times_ns()
        except Exception:
            return xmin, xmax
        if firsts.size < 2:
            return xmin, xmax
        f_num, l_num = _ns_to_num(firsts), _ns_to_num(lasts)
        gaps = f_num[1:] - l_num[:-1]
        # A pause is clearly longer than the usual spacing of the shots; with
        # evenly spaced shots there is none and nothing snaps.
        pause = gaps > max(2.0 / 86400.0, 3 * px_num, 3 * float(np.median(gaps)))
        starts = f_num[np.concatenate(([True], pause))]
        ends = l_num[np.concatenate((pause, [True]))]
        tol = 8 * px_num
        one_ms = 1e-3 / 86400.0

        def _near(x, cands):
            j = int(np.argmin(np.abs(cands - x)))
            c = float(cands[j])
            if abs(c - x) > tol:
                return None
            # Never jump over another shot: the edge may only take in (or let
            # go of) the one shot it lands on.
            a, b = min(c, x), max(c, x)
            between = int(np.count_nonzero((f_num >= a) & (f_num <= b)))
            return c if between <= 1 else None

        s = _near(xmin, starts)
        if s is not None:
            xmin = s - one_ms
        if not getattr(self, "_sel_live", False):
            e = _near(xmax, ends)
            if e is not None:
                xmax = e + one_ms
        return xmin, max(xmin, xmax)

    def _set_selection_edges(self, xmin, xmax):
        """Move the selection to [xmin, xmax] (date numbers) from a typed time
        or a one-shot step, and redraw its band and numbers."""
        if xmax <= xmin:
            return
        self._sel_range = (float(xmin), float(xmax))
        self._sel_samples = {}
        self._sel_restart_note = ""
        if self._span_selector is not None:
            try:
                self._span_selector.set_visible(True)
                self._span_selector.extents = self._sel_range
            except Exception:
                pass
        self._recompute_stats()
        if self._mpl_canvas is not None:
            self._blit_bg = None
            self._mpl_canvas.draw_idle()

    def _step_sel_edge(self, edge, step):
        """Move one edge of the selection onto the next (+1) or the previous
        (−1) shot."""
        if not self._sel_range:
            return
        firsts, lasts = self._all_shot_times_ns()
        if not firsts.size:
            return
        f_num, l_num = _ns_to_num(firsts), _ns_to_num(lasts)
        one_ms = 1e-3 / 86400.0
        lo, hi = self._sel_range
        if edge == "start":
            # From the first shot inside the selection to its neighbour.
            i = int(np.searchsorted(f_num, lo, side="left"))
            i = min(f_num.size - 1, i + 1) if step > 0 else max(0, i - 1)
            lo = float(f_num[i]) - one_ms
        else:
            self._sel_live = False
            k = int(np.searchsorted(l_num, hi, side="right")) - 1  # last shot inside
            k = min(l_num.size - 1, k + 1) if step > 0 else max(0, k - 1)
            hi = float(l_num[k]) + one_ms
        self._set_selection_edges(lo, hi)

    def _on_sel_edge_typed(self, edge):
        """A time was typed into the Start or End box."""
        ed = self._sel_edge_edits.get(edge)
        if ed is None or not self._sel_range:
            return
        try:
            dt = ed.dateTime().toPython().replace(tzinfo=TZ_PRAGUE)
            x = float(mdates.date2num(dt))
        except Exception:
            return
        lo, hi = self._sel_range
        if edge == "start":
            if abs(x - lo) < 0.5 / 86400.0:
                return
            lo = x
        else:
            if abs(x - hi) < 0.5 / 86400.0:
                return
            self._sel_live = False
            hi = x
        self._set_selection_edges(lo, hi)

    def _sync_sel_edge_edits(self):
        """Show the selection's edges in the Start / End boxes."""
        edits = getattr(self, "_sel_edge_edits", None) or {}
        if not self._sel_range or not edits:
            return
        for edge, x in zip(("start", "end"), self._sel_range):
            ed = edits.get(edge)
            if ed is None or ed.hasFocus():
                continue
            dt = mdates.num2date(x, tz=TZ_PRAGUE).replace(tzinfo=None)
            ed.blockSignals(True)
            ed.setDateTime(dt)
            ed.blockSignals(False)

    def _newest_sample_num(self):
        """Time of the newest real sample on the graph, as a date number (the
        traces are padded out to "now", so their arrays cannot answer this)."""
        newest = None
        for pv in self._graph_pvs:
            s = self._samples_by_pv.get(pv)
            if s:
                t = s[-1][0]
                if newest is None or t > newest:
                    newest = t
        if newest is None:
            return None
        return float(_ns_to_num(np.float64(newest)))

    def _follow_live_selection(self):
        """Move a live selection's end onto the newest sample (see
        _on_span_select). Returns True when the region changed."""
        if not (self._sel_range and getattr(self, "_sel_live", False)):
            return False
        newest = self._newest_sample_num()
        lo, hi = self._sel_range
        # The end sits ON the newest point — not on wherever the mouse left
        # the plot — and moves with every new one. Before the first shot after
        # the start arrives, it follows the clock, so a live selection over a
        # quiet stretch is visibly live from the moment it is made.
        if newest is None or newest <= lo:
            newest = float(_ns_to_num(np.float64(time.time_ns())))
            if newest <= lo:
                return False
        if newest == hi:
            return False
        self._sel_range = (lo, newest)
        if self._span_selector is not None:
            try:
                self._span_selector.extents = self._sel_range
            except Exception:
                pass
        return True

    def _clear_selection(self):
        """Forget the selected region and empty the statistics strip."""
        self._sel_range = None
        self._sel_live = False
        # A raw read still on its way for it is not wanted any more.
        self._sel_raw_seq = getattr(self, "_sel_raw_seq", 0) + 1
        self._sel_raw_want = None
        self._sel_samples = {}
        self._sel_restart_note = ""
        sel = getattr(self, "_span_selector", None)
        if sel is not None:
            try:
                sel.set_visible(False)
                if self._mpl_canvas is not None:
                    self._mpl_canvas.draw_idle()
            except Exception:
                pass
        self._clear_stats()

    def _drop_selection_if_outside(self):
        """Forget the selection when the newly chosen time window does not touch
        it at all. A window that still overlaps keeps it (the region is absolute
        time, so it lands on the same data); only a completely unrelated window
        makes the numbers meaningless."""
        if not self._sel_range:
            return
        try:
            w_lo = mdates.date2num(self._dt_from)
            w_hi = mdates.date2num(self._dt_to)
        except Exception:
            return
        s_lo, s_hi = self._sel_range
        if s_hi < w_lo or s_lo > w_hi:
            self._clear_selection()

    def _restore_selection_band(self):
        """Paint the remembered region back onto a freshly built SpanSelector.

        The selector is recreated with every figure, so without this the blue
        band vanishes even when the numbers are still valid.
        """
        if not self._sel_range or self._span_selector is None:
            return
        try:
            self._span_selector.extents = self._sel_range
        except Exception:
            pass

    def _recompute_stats(self):
        """Rebuild the per-PV statistics cards for ``self._sel_range``.

        Safe to call after any redraw or live update: it reads the region from
        state and the samples from the current arrays, so nothing is lost when
        the figure is rebuilt and the numbers track newly arrived data.
        """
        if not self._sel_range or not self._graph_pvs:
            self._clear_stats()
            return
        live = bool(getattr(self, "_sel_live", False))
        # Live mode trims samples older than its span, so any selection made
        # while live (following or fixed) keeps its own copy of the shots.
        own_copy = live or bool(self._live_mode)
        if own_copy:
            self._sel_sync()
        else:
            # Out of live mode the full samples are all there; a copy kept from
            # an earlier live run would miss what came in while live was off.
            self._sel_samples = {}
        if live:
            self._sel_restart_after_pause()
        self._follow_live_selection()
        xmin, xmax = self._sel_range

        self._clear_stats()
        # The numbers come from each channel's OWN samples — never from the
        # trace drawn on screen. The drawn trace is thinned to a few thousand
        # bins whose edges move with every live tick, and it carries held
        # values from other channels' rows, so counting it gave a shot count
        # that wandered (709, 710, 709) over a long window.
        sbp = self._sel_samples if own_copy else self._samples_by_pv
        pre = (getattr(self, "_sel_pre", {}) if own_copy
               else self._pre_window_vals)
        lo_ns = (xmin - _mpl_epoch_num()) * _NS_PER_DAY
        # A live selection has no upper bound: its end IS the newest shot, and
        # a date-number round trip could shave that shot off by a microsecond.
        hi_ns = (np.inf if live
                 else (xmax - _mpl_epoch_num()) * _NS_PER_DAY + 1e6)
        # A thinned load holds one averaged point per time bin, not one per
        # shot: the numbers are then taken from every archived sample of the
        # selection, read on purpose (see _request_sel_raw). Until it is here
        # the cards say so instead of showing numbers that are not exact.
        raw = None
        raw_note = ""
        if not own_copy and self._sel_needs_raw():
            raw = self._sel_raw_covering(lo_ns, hi_ns)
            if raw is None:
                fail = self._sel_raw_failure(lo_ns, hi_ns)
                if fail is None:
                    self._request_sel_raw(lo_ns, hi_ns)
                    self._show_stats_reading(xmin, xmax)
                    return
                # The exact read failed: the thinned numbers, plainly marked.
                raw_note = f"   ⚠ {fail}"
            else:
                sbp, pre = raw["samples"], raw["pre"]
        minmax = {} if raw is not None else (self._minmax_by_pv or {})
        tables = self._condition_tables(sbp, pre, minmax=minmax)
        self._sel_fixed_restart_note = ""
        if not live:
            lo_ns = self._fixed_restart_after_pause(sbp, pre, tables, lo_ns, hi_ns)
        shots_txt, shots_tip = self._sel_shot_count(
            sbp, pre, tables, lo_ns, hi_ns, raw, own_copy)
        shots_txt += raw_note
        for i, pv in enumerate(self._graph_pvs):
            sel = np.empty(0)
            _lo = _hi = None
            t_s, v_s = _samples_to_ns_arrays(sbp.get(pv) or [])
            if t_s.size:
                keep = (t_s >= lo_ns) & (t_s <= hi_ns) & np.isfinite(v_s)
                keep &= self._stats_keep_mask(t_s, tables, sbp, pre)
                sel = v_s[keep]
                # Points the archiver averaged for a long window: the extremes
                # are the min/max it sent with them (only without conditions —
                # those extremes include the shots the conditions reject).
                mm = None if self._conditions else minmax.get(pv)
                if sel.size and mm is not None:
                    e_lo, e_hi = _envelope_for(t_s[keep], sel, mm)
                    _lo = float(np.nanmin(e_lo))
                    _hi = float(np.nanmax(e_hi))
            if sel.size:
                _mean = float(np.mean(sel))
                _lo = float(np.min(sel)) if _lo is None else min(_lo, float(np.min(sel)))
                _hi = float(np.max(sel)) if _hi is None else max(_hi, float(np.max(sel)))
                stats = {
                    "n":   f"{sel.size:,}",
                    "avg": _fmt_cursor_value(_mean),
                    # Scatter and spread are read as "how steady is it", which is
                    # a share of the average, not a count of the channel's units.
                    "std": _fmt_percent_of(float(np.std(sel)), _mean),
                    "min": _fmt_cursor_value(_lo),
                    "max": _fmt_cursor_value(_hi),
                    "ptp": _fmt_percent_of(_hi - _lo, _mean),
                }
            else:
                # No shot in the region (yet): the card still stands, with
                # zero shots, so the selection is visibly there.
                stats = {"n": "0", "avg": "—", "std": "—",
                         "min": "—", "max": "—", "ptp": "—"}
            pv_setting = self._pv_settings.get(pv, {})
            disp_name = self._col_label(pv)
            color     = pv_setting.get("color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])
            self._stats_flow.addWidget(self._make_stat_card(disp_name, color, stats))
            self._stats_rows.append((disp_name, color, stats))
        self._set_stats_title(xmin, xmax, live, shots_txt, shots_tip)

    def _set_stats_title(self, xmin, xmax, live, shots_txt, shots_tip=""):
        """The line above the cards: when, how long, how many shots."""
        t0 = mdates.num2date(xmin, tz=TZ_PRAGUE)
        t1 = mdates.num2date(xmax, tz=TZ_PRAGUE)
        span_s = (t1 - t0).total_seconds()
        # Warn when the window no longer covers the whole region, so the numbers
        # are never silently taken for the full selection. A live selection
        # counts its own copy of the shots, so its start may well lie left of
        # the graph — that is said, not warned about.
        _note = ""
        if self._graph_axes:
            try:
                _vlo, _vhi = self._graph_axes[0].get_xlim()
                if live:
                    if xmin < _vlo - 1e-9:
                        _note = "   (starts left of the graph)"
                elif xmin < _vlo - 1e-9 or xmax > _vhi + 1e-9:
                    _note = "   ⚠ partly outside the shown window"
            except Exception:
                pass
        _live = "   ● end follows the newest point" if live else ""
        if live and self._sel_restart_note:
            _restart = f"   ↻ {self._sel_restart_note}"
        elif not live and self._sel_fixed_restart_note:
            _restart = f"   ↻ {self._sel_fixed_restart_note}"
        else:
            _restart = ""
        end_fmt = ('%H:%M:%S' if t1.date() == t0.date() else '%Y-%m-%d %H:%M:%S')
        self._stats_title.setText(
            f"Selection statistics  ·  {t0.strftime('%Y-%m-%d %H:%M:%S')} → "
            f"{t1.strftime(end_fmt)}  ·  {_fmt_span_full(span_s)}  ·  "
            f"{shots_txt}{_live}{_restart}{_note}")
        self._stats_title.setToolTip(shots_tip)
        self._stats_title.show()
        self._sync_sel_edge_edits()
        self._fit_stats_strip()          # shows the strip at its cards' height

    def _show_stats_reading(self, xmin, xmax):
        """The cards while every sample of the selection is being read: the
        channels are there, the numbers are not yet."""
        self._sel_fixed_restart_note = ""
        for i, pv in enumerate(self._graph_pvs):
            stats = {k: "…" for k in ("n", "avg", "std", "min", "max", "ptp")}
            color = self._pv_settings.get(pv, {}).get(
                "color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])
            disp_name = self._col_label(pv)
            self._stats_flow.addWidget(self._make_stat_card(disp_name, color, stats))
            self._stats_rows.append((disp_name, color, stats))
        st = getattr(self, "_sel_raw_status", "") or "reading every sample in the selection"
        self._set_stats_title(xmin, xmax, False, st,
                              "The window was loaded thinned (one averaged point "
                              "per time step). The exact numbers come from every "
                              "archived sample of the selection, read now.")

    def _sel_shot_count(self, sbp, pre, tables, lo_ns, hi_ns, raw, own_copy):
        """("1,080 shots" or "950 of 1,080 shots", tooltip) for the selection.

        A shot is a moment when most channels running on the shot rate wrote
        (cpva_core.find_shots). The count goes through the same filters as the
        cards — Conditions, "Keep multiples of", dead shots — judged at the
        last write of each shot, where its energy has been archived too."""
        if raw is not None:
            starts, ends, gap, members = raw["shots"]
        elif own_copy:
            w = self._window_shots()
            starts, ends, gap, members = find_shots(
                self._shot_ts_by_pv(sbp), rate=(w[2], w[3]))
        else:
            starts, ends, gap, members = self._window_shots()
        if not gap or not members:
            return ("shots: —", "No shot rate found: no channel has enough "
                    "samples, or every channel runs at its own pace.")
        inside = (starts >= lo_ns) & (starts <= hi_ns)
        n_all = int(np.count_nonzero(inside))
        ends_in = ends[inside].astype(np.float64)
        n_ok = int(np.count_nonzero(self._stats_keep_mask(ends_in, tables, sbp, pre)))
        txt = (f"{n_all:,} shots" if n_ok == n_all
               else f"{n_ok:,} of {n_all:,} shots")
        names = ", ".join(shorten_pv_name(p) for p in sorted(members))
        tip = (f"A shot is a moment when at least half of the channels running "
               f"at the shot rate wrote a value.\nShot rate: one every "
               f"{gap / 1e9:,.3g} s.\nDeciding channels ({len(members)}): {names}")
        if n_ok != n_all:
            tip += (f"\n{n_all - n_ok:,} shots are left out by the Conditions, "
                    f"\"Keep multiples of\" or the dead-shot rule.")
        return txt, tip

    # ── Exact selection numbers: every archived sample of the region ─────────

    def _sel_needs_raw(self):
        """Was the window loaded thinned? Then a selection is counted from its
        own raw read. (Live keeps every sample it polls — nothing to read.)"""
        return bool(getattr(self, "_load_thinned", False)) and not self._live_mode

    def _sel_raw_covering(self, lo_ns, hi_ns):
        """The raw read that covers [lo_ns, hi_ns] in this load, with the custom
        PVs computed on its own shots — or None."""
        d = getattr(self, "_sel_raw", None)
        if (not d or d["epoch"] != self._load_epoch
                or d["pvs"] != tuple(self._fetch_pv_names())
                or d["a"] > lo_ns or d["b"] < hi_ns):
            return None
        samples = dict(d["samples"])
        starts, ends = d["shots"][0], d["shots"][1]
        plan = getattr(self, "_cpv_last_plan", None) or []
        if plan and starts.size:
            for name, (t, v) in self._custom_series_on_shots(
                    plan, samples, d["pre"], starts, ends).items():
                ok = np.isfinite(v)
                samples[name] = _SampleArrays(t[ok], v[ok])
        return {"samples": samples, "pre": d["pre"], "shots": d["shots"]}

    def _sel_raw_failure(self, lo_ns, hi_ns):
        """Why the raw read of this very selection failed, or None."""
        f = getattr(self, "_sel_raw_failed", None)
        if f and f[0] == (int(math.floor(lo_ns)), int(math.ceil(hi_ns)),
                          self._load_epoch):
            return f[1]
        return None

    def _request_sel_raw(self, lo_ns, hi_ns):
        """Ask for the raw read of the selection, once the edges have stopped
        moving for a moment (typing a time or stepping shot by shot)."""
        want = (int(math.floor(lo_ns)), int(math.ceil(hi_ns)), self._load_epoch)
        if getattr(self, "_sel_raw_want", None) == want:
            return                                   # already on its way
        self._sel_raw_want = want
        self._sel_raw_seq = getattr(self, "_sel_raw_seq", 0) + 1
        self._sel_raw_status = ""
        timer = getattr(self, "_sel_raw_timer", None)
        if timer is None:
            timer = self._sel_raw_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._start_sel_raw_read)
        timer.start(300)

    def _start_sel_raw_read(self):
        want = getattr(self, "_sel_raw_want", None)
        if not want or want[2] != self._load_epoch:
            return
        a, b, epoch = want
        seq = self._sel_raw_seq
        pvs = list(self._fetch_pv_names())      # hidden PVs are not read
        if not pvs:
            return
        timeout = self._http_timeout()
        w = self._window_shots()
        rate = (w[2], w[3])
        sig = self._sel_raw_sig = _LoadSig()
        sig.done.connect(self._on_sel_raw_loaded)
        sig.progress.connect(self._on_sel_raw_progress)
        cancel = lambda: self._sel_raw_seq != seq or self._load_epoch != epoch  # noqa: E731

        def _worker():
            try:
                from cpva_core import cpva_fetch_many_adaptive, cpva_decode_value
                slices = _export_slices(a, b)
                ts_parts = {pv: [] for pv in pvs}
                v_parts = {pv: [] for pv in pvs}
                pre_raw = {}
                total = 0
                errors = {}
                for k, (lo, hi) in enumerate(slices):
                    if cancel():
                        return
                    raw_by_pv, errs, _rep = cpva_fetch_many_adaptive(
                        pvs, lo, hi, count=None, timeout=timeout,
                        preflight=False, cancel_fn=cancel)
                    if cancel():
                        return
                    errors.update(errs or {})
                    for pv in pvs:
                        tl, vl = [], []
                        for s in raw_by_pv.get(pv) or ():
                            t = s.get("time")
                            if t is None:
                                continue
                            t = int(t)
                            v = cpva_decode_value(s)
                            if t < a:
                                # The sample every request also returns from
                                # just before its start: the held value.
                                if v is not None and (pv not in pre_raw
                                                      or t > pre_raw[pv][0]):
                                    pre_raw[pv] = (t, v, "")
                                continue
                            if t < lo or t >= hi:
                                continue           # the neighbour slice's
                            if (isinstance(v, (int, float))
                                    and not isinstance(v, bool)
                                    and math.isfinite(v)):
                                tl.append(t)
                                vl.append(float(v))
                        if tl:
                            ts_parts[pv].append(np.asarray(tl, dtype=np.int64))
                            v_parts[pv].append(np.asarray(vl, dtype=np.float64))
                            total += len(tl)
                    del raw_by_pv
                    if total > _SEL_RAW_MAX_SAMPLES:
                        sig.done.emit({"seq": seq, "want": (a, b, epoch),
                                       "error": f"over {_SEL_RAW_MAX_SAMPLES:,} "
                                                f"samples — select a shorter range "
                                                f"for exact numbers"})
                        return
                    sig.progress.emit(f"reading every sample in the selection  "
                                      f"{k + 1}/{len(slices)}")
                samples, ts_by = {}, {}
                for pv in pvs:
                    if not ts_parts[pv]:
                        continue
                    t = np.concatenate(ts_parts[pv])
                    v = np.concatenate(v_parts[pv])
                    t, first = np.unique(t, return_index=True)   # sorts + dedups
                    samples[pv] = _SampleArrays(t, v[first])
                    ts_by[pv] = samples[pv].ts
                shots = find_shots(ts_by, rate=rate)
                sig.done.emit({"seq": seq, "want": (a, b, epoch), "a": a, "b": b,
                               "epoch": epoch, "pvs": tuple(pvs),
                               "samples": samples, "pre_raw": pre_raw,
                               "shots": shots, "errors": errors})
            except Exception as exc:
                sig.done.emit({"seq": seq, "want": (a, b, epoch),
                               "error": f"the exact read failed ({exc})"})

        threading.Thread(target=_worker, daemon=True).start()

    def _on_sel_raw_progress(self, text):
        self._sel_raw_status = text
        if self._sel_range and getattr(self, "_sel_raw_want", None):
            xmin, xmax = self._sel_range
            self._clear_stats()
            self._show_stats_reading(xmin, xmax)

    def _on_sel_raw_loaded(self, d):
        if d.get("seq") != getattr(self, "_sel_raw_seq", None):
            return                                   # an older selection's
        self._sel_raw_want = None
        self._sel_raw_status = ""
        if d.get("error") or d.get("errors"):
            msg = d.get("error") or (
                "exact read failed for " + ", ".join(
                    shorten_pv_name(p) for p in sorted(d["errors"]))
                + " — numbers from the thinned load")
            self._sel_raw_failed = (d["want"], msg)
            self._log(f"Selection statistics: {msg}")
        else:
            # What each channel held just before the selection: the archived
            # sample the read brought along, else (a quiet channel, every
            # custom PV) what the loaded window held there.
            pre = self._sel_raw_base_pre(d["a"])
            pre.update(d["pre_raw"])
            self._sel_raw = {"a": d["a"], "b": d["b"], "epoch": d["epoch"],
                             "pvs": d["pvs"], "samples": d["samples"],
                             "pre": pre, "shots": d["shots"]}
            self._sel_raw_failed = None
        self._recompute_stats()

    def _sel_raw_base_pre(self, a):
        """Each loaded channel's value just before ``a`` in the loaded window."""
        out = {}
        for pv, s in self._samples_by_pv.items():
            s = s or []
            i = bisect.bisect_left(s, a, key=lambda x: x[0])
            if i > 0:
                out[pv] = s[i - 1]
            elif self._pre_window_vals.get(pv) is not None:
                out[pv] = self._pre_window_vals[pv]
        return out

    def _sel_sync(self):
        """Copy every sample newer than the live selection's copy into it.

        Runs before the live window trims old samples and on every stats
        refresh (custom channels grow outside the live tick). The first call
        takes everything from the selection's start on, plus each channel's
        value just before it (for the conditions)."""
        if not self._sel_range:
            return
        lo_ns = (self._sel_range[0] - _mpl_epoch_num()) * _NS_PER_DAY
        first = not self._sel_samples
        if first:
            self._sel_pre = {}
        for pv, s in self._samples_by_pv.items():
            if not s:
                # Nothing in the window yet — but its value from before the
                # window still judges the shots (a condition channel that is
                # quiet would otherwise drop out of the conditions entirely).
                if pv not in self._sel_pre and \
                        self._pre_window_vals.get(pv) is not None:
                    self._sel_pre[pv] = self._pre_window_vals[pv]
                continue
            mine = self._sel_samples.get(pv)
            if mine:
                i = bisect.bisect_right(s, mine[-1][0], key=lambda x: x[0])
                if i < len(s):
                    mine.extend(s[i:])
                continue
            i = bisect.bisect_left(s, lo_ns, key=lambda x: x[0])
            self._sel_samples[pv] = list(s[i:])
            if first or pv not in self._sel_pre:
                if i > 0:
                    self._sel_pre[pv] = s[i - 1]
                elif self._pre_window_vals.get(pv) is not None:
                    self._sel_pre[pv] = self._pre_window_vals[pv]

    def _stats_keep_mask(self, ts, tables, sbp, pre):
        """Which sample times count in the selection statistics: inside the
        conditions, on a master-PV multiple (when that filter is set), and not
        a zero-energy shot at an unchanged waveplate (when that is ticked)."""
        keep = np.ones(ts.size, dtype=bool)
        if not ts.size:
            return keep
        if tables:
            keep &= self._condition_mask(ts, tables)
        on_step = self._master_multiple_mask(ts, sbp, pre)
        if on_step is not None:
            keep &= on_step
        dead = self._dead_shot_mask(ts, sbp, pre)
        if dead is not None:
            keep &= ~dead
        return keep

    @staticmethod
    def _held_at(ts, samples, pre_entry):
        """Value a channel holds at each time of ``ts`` (ns), read the way the
        table rows read it: its newest sample up to one shot-merge gap later,
        else the value it had before the window (NaN when there is none)."""
        c_ts, c_v = _samples_to_ns_arrays(samples or [])
        p_val = (float(pre_entry[1]) if pre_entry is not None
                 and isinstance(pre_entry[1], (int, float)) else np.nan)
        v = np.full(ts.size, p_val)
        if c_ts.size:
            gap = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
            idx = np.searchsorted(c_ts, ts + gap, side="right") - 1
            has = idx >= 0
            v[has] = c_v[idx[has]]
        return v

    def _master_multiple_mask(self, ts, sbp, pre):
        """The "Keep multiples of" filter for single samples: True where the
        master PV sits on a multiple. None when the filter is off or the master
        PV has no data (the rows skip it then too)."""
        try:
            multiple = float(self._get_master_multiple())
        except (TypeError, ValueError):
            return None
        if multiple <= 0:
            return None
        master = self._get_master_pv()
        if not (sbp.get(master) or pre.get(master) is not None):
            return None
        v = self._held_at(ts, sbp.get(master), pre.get(master))
        tolerance = max(1e-6, abs(multiple) * 5e-3)
        with np.errstate(invalid="ignore"):
            return np.abs(v - np.round(v / multiple) * multiple) <= tolerance

    def _step_filter_applies(self) -> bool:
        """Is "Keep multiples of" acting on this window — a number in the box
        AND the master PV has a value here (else the rows skip it too)?"""
        try:
            multiple = float(self._get_master_multiple())
        except (TypeError, ValueError):
            return False
        if multiple <= 0:
            return False
        master = self._get_master_pv()
        return bool(master and (self._samples_by_pv.get(master)
                                or self._pre_window_vals.get(master) is not None))

    def _dead_shot_mask(self, ts, sbp, pre):
        """True for the samples of a dead shot: the energy channel read below
        its threshold while the waveplate (the master PV) stood where it stood
        on the shot before. Such a shot is a trigger shot or a dropout, not a
        measurement at a new setting. None when the rule is switched off or the
        energy channel has no data."""
        if not getattr(self, "_dead_shot_on", False):
            return None
        e_pv = (getattr(self, "_dead_energy_pv", "") or "").strip()
        if not e_pv:
            return None
        e_t, e_v = _samples_to_ns_arrays(sbp.get(e_pv) or [])
        if not e_t.size:
            return None
        master = self._get_master_pv()
        wp = self._held_at(e_t, sbp.get(master), pre.get(master))
        p = pre.get(master)
        wp_before = (float(p[1]) if p is not None
                     and isinstance(p[1], (int, float)) else np.nan)
        prev = np.concatenate(([wp_before], wp[:-1]))
        with np.errstate(invalid="ignore"):
            same = ((np.abs(wp - prev) <= 1e-9 * np.maximum(1.0, np.abs(wp)))
                    | (np.isnan(wp) & np.isnan(prev)))
            dead_e = same & np.isfinite(e_v) & (
                e_v < float(getattr(self, "_dead_energy_thr", 0.05)))
        # Each sample belongs to the energy reading of its own shot: the
        # newest one up to a merge gap after it, if that is also no more than
        # a merge gap before it.
        gap = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
        j = np.searchsorted(e_t, ts + gap, side="right") - 1
        near = j >= 0
        near[near] &= e_t[j[near]] >= ts[near] - gap
        out = np.zeros(ts.size, dtype=bool)
        out[near] = dead_e[j[near]]
        return out

    def _shot_times(self, sbp, pre, tables, lo_ns, hi_ns):
        """Times of the shots between lo_ns and hi_ns that count. A shot is a
        sample of a condition channel (the energies — a slow housekeeping
        channel keeps ticking through a pause), or without conditions a sample
        of a shot-type graph channel."""
        if self._conditions:
            src = [c.get("pv") for c in self._conditions if c.get("pv")]
        else:
            src = self._export_shot_channels(self._graph_pvs) or list(self._graph_pvs)
        parts = []
        for pv in dict.fromkeys(src):
            t_s, v_s = _samples_to_ns_arrays(sbp.get(pv) or [])
            if not t_s.size:
                continue
            keep = np.isfinite(v_s) & (t_s >= lo_ns) & (t_s <= hi_ns)
            keep &= self._stats_keep_mask(t_s, tables, sbp, pre)
            parts.append(t_s[keep])
        if not parts:
            return np.empty(0)
        return np.unique(np.concatenate(parts))

    def _last_pause(self, sbp, pre, tables, lo_ns, hi_ns):
        """(1 ms before the first shot after the newest firing pause longer
        than the set seconds, that pause in s) — or None without one."""
        shots = self._shot_times(sbp, pre, tables, lo_ns, hi_ns)
        if shots.size < 2:
            return None
        gaps = np.diff(shots)
        big = np.nonzero(gaps > float(self._sel_pause_s) * 1e9)[0]
        if not big.size:
            return None
        k = int(big[-1])
        return float(shots[k + 1]) - 1e6, float(gaps[k]) / 1e9

    def _fixed_restart_after_pause(self, sbp, pre, tables, lo_ns, hi_ns):
        """A fixed selection + "Restart after pause": count only from the first
        shot after the newest pause inside it. The blue band stays where it was
        put (the region is the user's), the title says where counting began."""
        if not getattr(self, "_sel_pause_on", False):
            return lo_ns
        found = self._last_pause(sbp, pre, tables, lo_ns, hi_ns)
        if found is None:
            return lo_ns
        new_lo_ns, pause_s = found
        at = mdates.num2date(float(_ns_to_num(np.float64(new_lo_ns))),
                             tz=TZ_PRAGUE).strftime("%H:%M:%S")
        self._sel_fixed_restart_note = (
            f"counted from {at}, after a {pause_s:,.0f} s pause")
        return new_lo_ns

    def _sel_restart_after_pause(self):
        """Live selection + "restarts after pause": begin again at the first
        shot after the newest pause in firing longer than the set seconds.

        A shot is a sample of a condition channel that passes the conditions
        (the energies — a slow housekeeping channel keeps ticking through a
        pause), or without conditions a sample of a shot-type graph channel."""
        if not (getattr(self, "_sel_pause_on", False) and self._sel_samples
                and self._sel_range):
            return
        pre = getattr(self, "_sel_pre", {})
        tables = self._condition_tables(self._sel_samples, pre)
        lo_ns = (self._sel_range[0] - _mpl_epoch_num()) * _NS_PER_DAY
        found = self._last_pause(self._sel_samples, pre, tables, lo_ns, np.inf)
        if found is None:
            return
        new_lo_ns, pause_s = found
        for pv, s in self._sel_samples.items():
            i = bisect.bisect_left(s, new_lo_ns, key=lambda x: x[0])
            if i:
                self._sel_pre[pv] = s[i - 1]
                del s[:i]
        new_lo = float(_ns_to_num(np.float64(new_lo_ns)))
        self._sel_range = (new_lo, max(new_lo, self._sel_range[1]))
        at = mdates.num2date(new_lo, tz=TZ_PRAGUE).strftime("%H:%M:%S")
        self._sel_restart_note = f"restarted at {at} after a {pause_s:,.0f} s pause"
        self._log(f"Live selection {self._sel_restart_note}.")
        if self._span_selector is not None:
            try:
                self._span_selector.extents = self._sel_range
            except Exception:
                pass

    def _on_zoom_select(self, xmin, xmax):
        # Right-drag = zoom in time. BOTH views go onto the TOOLBAR's history —
        # the one being left so Back returns to it, the one being entered so
        # Forward comes back to here — exactly as matplotlib's own Zoom button
        # does it. Pushing only the first left Forward with nowhere to go.
        if xmax - xmin < 1e-6: return
        if not self._graph_axes: return
        t0 = mdates.num2date(xmin, tz=TZ_PRAGUE)
        t1 = mdates.num2date(xmax, tz=TZ_PRAGUE)
        # A zoom means "let me look at this stretch": Live stops, so new data
        # no longer arrives underneath it. Home turns it back on.
        self._live_off_by_zoom()
        self._push_graph_view()
        self._graph_axes[0].set_xlim(t0, t1)
        self._push_graph_view()
        self._user_zoomed = True
        self._retick_from_current_xlim()
        if self._mpl_canvas:
            self._mpl_canvas.draw_idle()

    # ── Zoom detail: read the zoomed range again, finer ──────────────────────
    # A long period arrives as thinned points (every ~10 min over a year). Their
    # min–max band already shows every spike, but WHEN exactly and HOW MUCH needs
    # the finer data — so, like CS Studio's Data Browser, a zoom asks the
    # archiver for the visible range again at the same point count. Far enough
    # in (an hour or less) the archiver hands back every archived sample.

    def _on_graph_xlim_changed(self, _ax=None):
        if self._live_mode or self._load_in_flight:
            return
        timer = getattr(self, "_detail_timer", None)
        if timer is None:
            timer = self._detail_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._maybe_load_zoom_detail)
        timer.start(450)

    def _zoom_detail_wanted(self):
        """The (start, end) ns range to read finer, or None when the current
        view needs nothing more."""
        if (self._live_mode or self._load_in_flight or not self._graph_axes
                or self._avg_target_points() <= 0):
            return None                 # raw mode already has every sample
        # The Conditions are applied to the detail itself (see the worker).
        # The step filter is not: only when it really acts on this window
        # does it stop the detail — its default "1000" used to switch the zoom
        # detail off for everyone, so a zoomed day of a long period stayed a
        # few wide steps.
        if self._step_filter_applies():
            return None
        if any(c.get("pv") and c["pv"] not in set(self._real_pv_names())
               and self._has_window_data(c["pv"])
               for c in self._conditions):
            return None                 # a custom PV condition: rows only
        try:
            v0, v1 = self._graph_axes[0].get_xlim()
        except Exception:
            return None
        e0 = _mpl_epoch_num()
        a = int((v0 - e0) * _NS_PER_DAY); b = int((v1 - e0) * _NS_PER_DAY)
        w0, w1 = dt_to_ns(self._dt_from), dt_to_ns(self._dt_to)
        a, b = max(a, w0), min(b, w1)
        view = b - a
        if view <= 0 or view >= 0.5 * (w1 - w0):
            return None                 # (almost) the whole window: base is fine
        d = self._zoom_detail
        if (d and d.get("epoch") == self._load_epoch and d["a"] <= a
                and d["b"] >= b and d["b"] - d["a"] <= 3 * view):
            return None                 # already read, and fine enough
        pad = view // 4                 # a little either side, for panning
        return max(w0, a - pad), min(w1, b + pad)

    def _maybe_load_zoom_detail(self):
        rng = self._zoom_detail_wanted()
        if rng is None:
            return
        if self._detail_in_flight:
            self._on_graph_xlim_changed()      # try again once this one lands
            return
        real = set(self._real_pv_names())
        pvs = [pv for pv in self._graph_pvs if pv in real]
        if not pvs:
            return
        a, b = rng
        epoch = self._load_epoch
        target = self._avg_target_points()
        # Zoomed to about an hour: ask for every archived sample outright. The
        # archiver does this by itself at an hour or less, but only just —
        # a range a hair over the hour would come back thinned.
        count = None if b - a <= int(1.1 * 3600e9) else target
        timeout = self._http_timeout()
        # The Conditions judge the detail as they judge the rows: the condition
        # channels are read over the same range, each starting from the value it
        # had just before it (from the window already loaded).
        conds = [c for c in self._conditions
                 if c.get("pv") and self._has_window_data(c["pv"])]
        cond_pre = {}
        for c in conds:
            c_ts, c_v = _samples_to_ns_arrays(self._samples_by_pv.get(c["pv"]) or [])
            j = int(np.searchsorted(c_ts, a, side="left")) - 1
            if j >= 0:
                cond_pre[c["pv"]] = float(c_v[j])
            else:
                p = self._pre_window_vals.get(c["pv"])
                if p is not None and isinstance(p[1], (int, float)):
                    cond_pre[c["pv"]] = float(p[1])
        fetch = pvs + [c["pv"] for c in conds if c["pv"] not in pvs]
        self._detail_in_flight = True
        self._detail_prev_status = self._lbl_status.text()
        self._lbl_status.setText(
            f"Reading the zoomed range in finer detail  "
            f"({ns_to_local_str(a)[:16]} → {ns_to_local_str(b)[:16]})")
        sig = self._detail_sig = _LoadSig()
        sig.done.connect(self._on_zoom_detail_loaded)

        def _worker():
            out = {}
            try:
                from cpva_core import cpva_fetch_many_adaptive, cpva_decode_value
                raw_by_pv, _err, _rep = cpva_fetch_many_adaptive(
                    fetch, a, b, count=count, timeout=timeout, preflight=False,
                    cancel_fn=lambda: self._load_epoch != epoch)

                def _arrays(pv):
                    raw = [s for s in raw_by_pv.get(pv, [])
                           if s.get("time") is not None and a <= s["time"] <= b]
                    pairs = [(int(s["time"]), cpva_decode_value(s)) for s in raw]
                    ts, vals = _pairs_to_ns_arrays(pairs)
                    lo, hi = _envelope_for(ts, vals, _minmax_arrays(raw))
                    return ts, vals, lo, hi

                tables = []
                for c in conds:
                    c_ts, c_v, c_lo, c_hi = _arrays(c["pv"])
                    p_val = cond_pre.get(c["pv"])
                    if not c_ts.size and p_val is None:
                        continue
                    tables.append((c_ts, c_v, p_val, c.get("min"), c.get("max"),
                                   bool(c.get("outside", False)), c_lo, c_hi))
                for pv in pvs:
                    ts, vals, lo, hi = _arrays(pv)
                    if tables and ts.size:
                        keep = self._condition_mask(ts, tables)
                        ts, vals, lo, hi = ts[keep], vals[keep], lo[keep], hi[keep]
                    if not ts.size:
                        continue
                    out[pv] = _downsample_arrays_envelope(ts, vals, lo, hi, target)
            except Exception as exc:
                out = {"_error": str(exc)}
            sig.done.emit({"a": a, "b": b, "epoch": epoch, "pvs": out})

        threading.Thread(target=_worker, daemon=True).start()

    def _on_zoom_detail_loaded(self, d):
        self._detail_in_flight = False
        err = d["pvs"].pop("_error", None)
        if self._lbl_status.text().startswith("Reading the zoomed range"):
            self._lbl_status.setText(getattr(self, "_detail_prev_status", ""))
        if err:
            self._log(f"Zoom detail could not be read: {err}")
            return
        if d["epoch"] != self._load_epoch or self._live_mode or not d["pvs"]:
            return
        self._zoom_detail = d
        self._apply_detail_in_place()
        self._maybe_load_zoom_detail()        # the view may have moved meanwhile

    def _apply_detail_in_place(self):
        """Put the zoom detail into the drawn traces without rebuilding the
        figure, so the zoom, the toolbar's Back/Forward and the selection stay."""
        if self._mpl_canvas is None or not self._graph_lines:
            return
        use_win, ws, we = getattr(self, "_graph_hold", (False, 0, 0))
        for i, pv in enumerate(self._graph_pvs):
            if i >= len(self._graph_base) or i >= len(self._graph_lines):
                break
            ts, v, lo, hi = self._splice_detail(pv, *self._graph_base[i])
            if use_win:
                ts, v, lo, hi = self._hold_to_window(pv, ts, v, lo, hi, ws, we)
            times_num = _ns_to_num(ts)
            lines = self._graph_lines[i]
            if len(lines) == 2:
                smooth = self._pv_settings.get(pv, {}).get("smooth") or 1
                lines[0].set_data(times_num, v)
                lines[1].set_data(times_num, _moving_avg_np(v, smooth)
                                  if smooth > 1 and v.size >= smooth else v)
            elif lines:
                lines[0].set_data(times_num, v)
            self._replace_band(i, self._graph_axes[i], times_num, lo, hi)
            if i < len(self._graph_raw):
                self._graph_raw[i] = (times_num, v)
            if i < len(self._graph_env):
                self._graph_env[i] = (lo, hi)
        self._graph_raw_np = [(np.asarray(t, dtype=float), np.asarray(x, dtype=float))
                              for t, x in self._graph_raw]
        if self._sel_range:
            self._recompute_stats()
        self._blit_bg = None
        self._mpl_canvas.draw_idle()

    # ── Limit crossings ─────────────────────────────────────────────────────

    def _crossing_lines(self):
        """Reference lines that take part: tied to a loaded signal, not off."""
        real = set(self._real_pv_names())
        return [rl for rl in self._ref_lines
                if rl.get("pv") in real and rl.get("crossing", "above") != "off"]

    def _open_limit_crossings(self):
        if not self._samples_by_pv:
            QMessageBox.information(self, "Limit crossings", "Load data first.")
            return
        lines = self._crossing_lines()
        if not lines:
            QMessageBox.information(
                self, "Limit crossings",
                "No reference line is tied to a loaded signal.\n\n"
                "Add one in Reference lines, pick its Signal, and set Crossing "
                "to above or below.")
            return
        if getattr(self, "_cross_in_flight", False):
            return
        self._cross_in_flight = True
        start_ns, end_ns = dt_to_ns(self._dt_from), dt_to_ns(self._dt_to)
        timeout = self._http_timeout()
        epoch = self._load_epoch
        series = {}
        for rl in lines:
            pv = rl["pv"]
            if pv in series:
                continue
            ts, vals = _samples_to_ns_arrays(
                [s for s in self._samples_by_pv.get(pv, [])
                 if start_ns <= s[0] <= end_ns])
            series[pv] = (ts, vals, self._minmax_by_pv.get(pv))
        self._cross_prev_status = self._lbl_status.text()
        self._lbl_status.setText("Looking for limit crossings")
        # The window opens now, at the click, and fills in when the search
        # ends — never pops up on its own later.
        old = getattr(self, "_cross_dialog", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        dlg = _LimitCrossingsDialog(self._conditions_active_text(), self)
        dlg.zoom_to.connect(self._zoom_to_ns)
        self._cross_dialog = dlg
        dlg.show()                       # not exec(): the graph stays usable
        sig = self._cross_sig = _LoadSig()
        sig.progress.connect(self._lbl_status.setText)
        sig.progress.connect(dlg.set_progress)
        sig.done.connect(self._on_limit_crossings_found)

        def _worker():
            rows, notes = [], []
            try:
                from concurrent.futures import ThreadPoolExecutor
                from cpva_core import cpva_fetch_many_adaptive, cpva_decode_value

                def _raw(pv, a, b):
                    got, err, _rep = cpva_fetch_many_adaptive(
                        [pv], a, b, count=None, timeout=timeout, max_workers=4,
                        preflight=False, verify=False,
                        cancel_fn=lambda: self._load_epoch != epoch)
                    if err.get(pv) and not got.get(pv):
                        raise RuntimeError(err[pv])
                    pairs = [(int(s["time"]), cpva_decode_value(s))
                             for s in got.get(pv, []) if s.get("time") is not None]
                    return _pairs_to_ns_arrays(sorted(pairs))

                for n_line, rl in enumerate(lines, start=1):
                    pv, y = rl["pv"], float(rl["y"])
                    side = rl.get("crossing", "above")
                    ts, vals, mm = series[pv]
                    name = (rl.get("label") or "").strip() or _fmt_cursor_value(y)
                    if mm is None:                     # read raw already: exact
                        eps = _crossing_episodes(ts, vals, y, side, end_ns)
                    else:
                        lo, hi = _envelope_for(ts, vals, mm)
                        cands = _crossing_candidates(ts, lo, hi, y, side, end_ns)
                        total = sum(b - a for a, b in cands)
                        if (len(cands) > _CROSS_RAW_MAX_RANGES
                                or total > _CROSS_RAW_MAX_NS):
                            eps = _crossing_episodes(ts, vals, y, side, end_ns,
                                                     exact=False, lo=lo, hi=hi)
                            notes.append(
                                f"{shorten_pv_name(pv)} / {name}: over the line "
                                f"{len(cands)}× across {total / 3.6e12:.0f} h — "
                                f"too much to read every sample, so times are to "
                                f"the thinned interval. Zoom in or shorten the "
                                f"period for exact ones.")
                        else:
                            eps = []
                            done = [0]

                            def _one(rng):
                                a, b = rng
                                try:
                                    r_ts, r_v = _raw(pv, a, b)
                                    ep = _crossing_episodes(r_ts, r_v, y, side, b)
                                    return [e for e in ep
                                            if a <= e["start"] <= b] or []
                                except Exception:
                                    # Unreadable raw: keep the interval-level
                                    # answer rather than lose the crossing.
                                    m = (ts >= a) & (ts < b)
                                    return _crossing_episodes(
                                        ts[m], vals[m], y, side, b, exact=False,
                                        lo=lo[m], hi=hi[m])
                                finally:
                                    done[0] += 1
                                    sig.progress.emit(
                                        f"Limit crossings · line {n_line}/{len(lines)} "
                                        f"· reading {done[0]}/{len(cands)} stretches "
                                        f"sample by sample")

                            with ThreadPoolExecutor(max_workers=6) as ex:
                                for part in ex.map(_one, cands):
                                    eps.extend(part)
                    for e in eps:
                        if e["start"] > end_ns or (e["end"] or end_ns) < start_ns:
                            continue
                        rows.append(dict(e, line=name, pv=pv, y=y, side=side))
                rows.sort(key=lambda r: r["start"])
            except Exception as exc:
                notes.append(f"Search stopped: {exc}")
            sig.done.emit({"rows": rows, "notes": notes, "end_ns": end_ns,
                           "lines": len(lines)})

        threading.Thread(target=_worker, daemon=True).start()

    def _on_limit_crossings_found(self, res):
        self._cross_in_flight = False
        self._lbl_status.setText(getattr(self, "_cross_prev_status", ""))
        for n in res["notes"]:
            self._log(f"Limit crossings: {n}")
        n = len(res["rows"])
        self._log(f"Limit crossings: {n} found.")
        dlg = getattr(self, "_cross_dialog", None)
        if dlg is not None:
            try:
                dlg.set_result(res)      # a closed window stays closed
            except RuntimeError:
                pass                     # already deleted

    def _conditions_active_text(self):
        return ("Conditions are not applied here: every archived sample counts."
                if self._conditions else "")

    def _zoom_to_ns(self, a_ns, b_ns):
        """Show [a, b] in the graph with a margin, the same way a right-drag
        zoom does (so Back returns, and the range is read finer)."""
        if not self._graph_axes:
            return
        span = max(b_ns - a_ns, int(60e9))
        pad = max(span // 5, int(120e9))
        a_ns, b_ns = a_ns - pad, b_ns + pad
        e0 = _mpl_epoch_num()
        try:
            self._notebook.setCurrentIndex(0)          # the Graph tab
        except Exception:
            pass
        self._on_zoom_select(e0 + a_ns / _NS_PER_DAY, e0 + b_ns / _NS_PER_DAY)

    # ── Graph toolbar ───────────────────────────────────────────────────────

    def _install_graph_toolbar(self, canvas):
        """Put a fresh toolbar above the graph for the canvas just built.

        Both the figure and the canvas are recreated by every full redraw and a
        matplotlib toolbar is bound to one canvas, so the old one is thrown away
        and a new one takes its place in the permanent holder.
        """
        holder = getattr(self, "_graph_tb_holder", None)
        if holder is None:
            return
        lay = holder.layout()
        old = getattr(self, "_graph_toolbar", None)
        if old is not None:
            try:
                old.setParent(None); old.deleteLater()
            except Exception:
                pass
        self._graph_toolbar = None
        # Drop whatever is left in the holder (the toolbar's light-palette host).
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.setParent(None); w.deleteLater()

        try:
            tb = _make_mpl_toolbar(_MplToolbar, canvas, holder)
        except Exception:
            self._log("[toolbar] could not be built — graph works without it.")
            return
        self._graph_toolbar = tb
        # A toolbar Zoom / Pan drag that moves the view ends Live (see
        # _on_graph_tb_release).
        canvas.mpl_connect("button_press_event", self._on_graph_tb_press)
        canvas.mpl_connect("button_release_event", self._on_graph_tb_release)
        # The light-palette host only had to exist while the toolbar was being
        # built (that is when the icons are tinted, once and never again), so the
        # toolbar can be moved into the visible row now with its icons intact.
        lay.addWidget(tb)
        tb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # "Customize" opens matplotlib's own styling dialog, which knows nothing
        # about this graph's stacked axes and would fight every setting made in
        # Graph settings. Spectra drops it for the same reason.
        for act in list(tb.actions()):
            if act.text() == "Customize":
                tb.removeAction(act)

        # Home hands the view back to the app (the live window may scroll again).
        for act in tb.actions():
            if act.text() == "Home":
                act.triggered.connect(self._on_graph_view_home)
            elif act.text() in ("Back", "Forward"):
                act.triggered.connect(self._retick_from_current_xlim)
            elif act.text() in ("Zoom", "Pan"):
                act.toggled.connect(self._sync_graph_interaction_mode)
            # Back / Forward greyed out when there is nowhere to go: the strip
            # button follows the toolbar's own action.
            act.changed.connect(self._sync_graph_strip)
        # A redraw builds a new toolbar, which starts with Pan and Zoom off. The
        # user pressed one of them in and has not let go — keep it pressed.
        keep = getattr(self, "_graph_tb_mode_wanted", "")
        if keep == "pan":
            tb.pan()
        elif keep == "zoom":
            tb.zoom()
        self._sync_graph_interaction_mode()

    def _graph_tb_action(self, text):
        """The hidden matplotlib toolbar's action called `text`, or None."""
        tb = getattr(self, "_graph_toolbar", None)
        if tb is None:
            return None
        for act in tb.actions():
            if act.text() == text:
                return act
        return None

    def _graph_tb_trigger(self, text):
        """A strip button was pressed: fire the matching toolbar action, so every
        hook connected to it (Home, Back/Forward re-stamping) runs as before."""
        act = self._graph_tb_action(text)
        if act is not None and act.isEnabled():
            act.trigger()
        self._sync_graph_strip()

    def _build_graph_strip(self):
        """The bar above the graph: icon buttons in three groups split by thin
        lines — View (Home, Back, Forward), Navigate (Pan, Zoom, Loupe) and
        Graph (Margins & spacing, Save image). Same look as the Workshop strip
        in Image Tools."""
        box = QFrame()
        box.setObjectName("graphStrip")
        box.setStyleSheet("QFrame#graphStrip { background: #ffffff; border: none; }")
        row = QHBoxLayout(box)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(2)
        self._gstrip_btns = {}

        def _btn(key, tip, checkable=False):
            b = QToolButton()
            b.setIcon(_graph_icon(key))
            b.setIconSize(QSize(_GICON_PX, _GICON_PX))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            b.setToolTip(tip)
            b.setAccessibleName(tip.split("\n")[0])
            b.setCheckable(checkable)
            b.setFixedSize(32, 32)
            b.setStyleSheet(_GRAPH_TOOLBTN_QSS)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._gstrip_btns[key] = b
            row.addWidget(b)
            return b

        def _sep():
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet("color: #c3c9d2;")
            row.addSpacing(4)
            row.addWidget(sep)
            row.addSpacing(4)

        _btn("home", "Reset view").clicked.connect(
            lambda: self._graph_tb_trigger("Home"))
        _btn("back", "Previous view").clicked.connect(
            lambda: self._graph_tb_trigger("Back"))
        _btn("forward", "Next view").clicked.connect(
            lambda: self._graph_tb_trigger("Forward"))
        _sep()
        _btn("pan", "Pan: left drag moves the graph, right drag stretches it",
             checkable=True).clicked.connect(
            lambda: self._on_graph_strip_mode("pan"))
        _btn("zoom", "Zoom: drag a rectangle to zoom into it",
             checkable=True).clicked.connect(
            lambda: self._on_graph_strip_mode("zoom"))
        _btn("loupe", "Loupe: the spot around the mouse, magnified, for the "
                      "signal nearest the mouse — handy for placing a selection "
                      "edge exactly",
             checkable=True).toggled.connect(self._set_loupe_on)
        # Magnification of the loupe, picked from a short list.
        self._btn_loupe_mag = QToolButton()
        self._btn_loupe_mag.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._btn_loupe_mag.setFixedSize(40, 32)
        self._btn_loupe_mag.setStyleSheet(_GRAPH_TOOLBTN_QSS +
                                          "QToolButton { font-weight: 600; }")
        self._btn_loupe_mag.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_loupe_mag.setToolTip("How much the loupe magnifies")
        mag_menu = QMenu(self._btn_loupe_mag)
        for m in (5, 10, 20, 50):
            mag_menu.addAction(f"{m}×", lambda m=m: self._set_loupe_mag(m))
        self._btn_loupe_mag.setMenu(mag_menu)
        row.addWidget(self._btn_loupe_mag)
        _sep()
        _btn("margins", "Margins and spacing: plot margins, the gap between a "
                        "Y-axis title and its numbers, the gap between Y axes"
             ).clicked.connect(self._open_margins_dialog)
        b_save = _btn("save", "Save the graph as an image")
        b_save.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        save_menu = QMenu(b_save)
        save_menu.addAction("Save graph", lambda: self._save_graph_image(False))
        save_menu.addAction("Save graph with selection",
                            lambda: self._save_graph_image(True))
        b_save.setMenu(save_menu)
        row.addStretch(1)

        self._loupe_mag = int(self.config.get("loupe_mag", 10))
        if self._loupe_mag not in (5, 10, 20, 50):
            self._loupe_mag = 10
        self._btn_loupe_mag.setText(f"{self._loupe_mag}×")
        self._graph_tb_mode_wanted = ""
        return box

    def _set_loupe_on(self, on):
        """Loupe button: a magnified view that follows the mouse over the graph."""
        self._loupe_on = bool(on)
        loupe = getattr(self, "_loupe", None)
        if on and loupe is None and getattr(self, "_graph_container", None) is not None:
            self._loupe = _GraphLoupe(self._graph_container)
        elif not on and loupe is not None:
            loupe.hide()

    def _set_loupe_mag(self, mag):
        self._loupe_mag = int(mag)
        self.config["loupe_mag"] = self._loupe_mag
        self._btn_loupe_mag.setText(f"{self._loupe_mag}×")
        ev = getattr(self, "_mouse_last_event", None)
        if ev is not None and getattr(self, "_loupe_on", False):
            self._update_loupe(ev)

    def _update_loupe(self, event):
        """Move the loupe to the mouse and redraw it (called from the throttled
        mouse handler, so it costs no more frames than the crosshair)."""
        loupe = getattr(self, "_loupe", None)
        if loupe is None:
            return
        if (event is None or event.inaxes is None or event.xdata is None
                or not self._graph_axes):
            loupe.hide()
            return
        # While a region is being dragged out, show where its edges are now.
        sel_range = self._sel_range
        sel = getattr(self, "_span_selector", None)
        if sel is not None:
            try:
                if any(a.get_visible() for a in getattr(sel, "artists", [])):
                    lo, hi = sel.extents
                    if hi > lo:
                        sel_range = (float(lo), float(hi))
            except Exception:
                pass
        names = [self._pv_settings.get(pv, {}).get("display_name")
                 or shorten_pv_name(pv) for pv in self._graph_pvs]
        if not loupe.update_at(float(event.xdata), self._graph_axes,
                               self._graph_lines, self._loupe_mag, sel_range,
                               y_px=float(event.y), names=names):
            return
        from PySide6.QtGui import QCursor
        loupe.place_near(self._graph_container.mapFromGlobal(QCursor.pos()))

    def _on_graph_strip_mode(self, mode):
        """Pan or Zoom pressed on the strip: hand it to the toolbar, which turns
        the other one off by itself."""
        self._graph_tb_trigger("Pan" if mode == "pan" else "Zoom")
        self._sync_graph_interaction_mode()

    def _sync_graph_strip(self, *_):
        """Strip buttons show what the toolbar is doing: Pan/Zoom pressed in,
        Back/Forward greyed out when there is no view to go to."""
        btns = getattr(self, "_gstrip_btns", None)
        if not btns:
            return
        tb = getattr(self, "_graph_toolbar", None)
        mode = ""
        if tb is not None:
            try:
                mode = str(getattr(tb.mode, "name", tb.mode) or "").lower()
            except Exception:
                mode = ""
        for key in ("pan", "zoom"):
            b = btns.get(key)
            if b is not None:
                b.blockSignals(True)
                b.setChecked(key in mode)
                b.blockSignals(False)
        for key, text in (("back", "Back"), ("forward", "Forward"),
                          ("home", "Home")):
            act = self._graph_tb_action(text)
            b = btns.get(key)
            if b is not None:
                b.setEnabled(act is not None and act.isEnabled())

    def _sync_graph_interaction_mode(self, *_):
        """Decide what a LEFT drag on the graph does.

        Zoom or Pan pressed in on the toolbar → left drag belongs to the toolbar.
        Neither pressed in → left drag selects a region and shows its statistics.
        Right-drag zoom keeps working either way.
        """
        tb = self._graph_toolbar
        mode = ""
        if tb is not None:
            try:
                mode = str(tb.mode or "")
            except Exception:
                mode = ""
        busy = bool(mode.strip())
        # Remembered so a redraw (which builds a new toolbar) keeps it pressed.
        low = mode.lower()
        self._graph_tb_mode_wanted = ("pan" if "pan" in low else
                                      "zoom" if "zoom" in low else "")
        self._sync_graph_strip()
        sel = getattr(self, "_span_selector", None)
        if sel is not None:
            try:
                sel.set_active(not busy)
            except Exception:
                pass

        if busy:
            # Zoom or pan engaged: the user is inspecting a particular stretch, so
            # the live window must stop dragging the view along underneath them.
            self._user_zoomed = True
        else:
            # Both let go again. If the view happens to be back on the live window,
            # following "now" may resume — otherwise the flag would stick and the
            # graph would never scroll again short of pressing Home.
            self._user_zoomed = not self._view_matches_live_window()

        lbl = getattr(self, "_lbl_graph_info", None)
        if lbl is not None:
            lbl.setText("Zoom/pan on — left drag zooms instead of taking statistics"
                        if busy else "")

    def _view_matches_live_window(self):
        """Is the visible range (near enough) the rolling live window?"""
        span = self._live_window_span
        if not self._graph_axes or not span or span.total_seconds() <= 0:
            return False
        try:
            lo_n, hi_n = self._graph_axes[0].get_xlim()
            shown_s = (hi_n - lo_n) * 86400.0        # date numbers are in days
            return abs(shown_s - span.total_seconds()) <= 0.02 * span.total_seconds()
        except Exception:
            return False

    def _open_graph_view_menu(self):
        """The "View ▾" menu: reset, axis limits, hide grids.

        Deliberately a button and not a right-click menu on the graph — right-drag
        on the graph is the zoom, and a context menu there would swallow it.
        """
        canvas = self._mpl_canvas
        if canvas is None or not self._graph_axes:
            return
        menu = QMenu(self)
        act_reset = menu.addAction("Reset view")
        act_clear = menu.addAction("Clear selection")
        act_clear.setEnabled(bool(self._sel_range))
        menu.addSeparator()
        act_lim = menu.addAction("Axis limits")
        menu.addSeparator()
        act_grid_off = menu.addAction("Hide all grids")
        btn = getattr(self, "_btn_graph_view", None)
        _at = (btn.mapToGlobal(btn.rect().bottomLeft()) if btn is not None
               else self.mapToGlobal(QPoint(0, 0)))
        chosen = menu.exec(_at)
        if chosen is None:
            return
        if chosen is act_reset:
            tb = self._graph_toolbar
            if tb is not None:
                try:
                    tb.home()
                except Exception:
                    pass
            self._on_graph_view_home()
        elif chosen is act_clear:
            self._clear_selection()
        elif chosen is act_lim:
            try:
                _GraphAxisLimitsDialog(self).exec()
            except Exception:
                import traceback
                self._log(f"[axis_limits]\n{traceback.format_exc()}")
        elif chosen is act_grid_off:
            for pv in list(self._pv_settings):
                self._pv_settings[pv]["grid"] = False
            self._refresh_axis_settings_tv()
            self._regrid_in_place()

    def _apply_axis_limits_dialog(self, x_range, y_by_pv):
        """OK in the Axis limits dialog: the time range becomes the view (with
        Back/Forward history, like a zoom), the Y numbers become each PV's own
        Y min / Y max, exactly as if typed into the PV table."""
        y_changed = False
        for pv, (lo, hi) in y_by_pv.items():
            st = self._pv_settings.setdefault(pv, {})
            if st.get("ymin") == lo and st.get("ymax") == hi:
                continue
            st["ymin"], st["ymax"] = lo, hi
            if lo is not None or hi is not None:
                st["auto_scale"] = False     # Autoscale would ignore the numbers
            y_changed = True
        if not self._graph_axes:
            return
        x_before = tuple(self._graph_axes[0].get_xlim())
        was_zoomed = self._user_zoomed
        # The boxes show whole seconds, so an untouched range comes back up to
        # a second off — that is not a request to move the view.
        _tol = 1.5 / 86400.0
        x_moved = (abs(x_range[0] - x_before[0]) > _tol
                   or abs(x_range[1] - x_before[1]) > _tol)
        if y_changed:
            # A full redraw builds a new figure on the window's own range, so it
            # runs first and the time range is put back on top of it.
            self._plot_graph()          # also refreshes the PV table
            if not self._graph_axes:
                return
        target = x_range if x_moved else (x_before if was_zoomed else None)
        if target is None:
            return
        ax0 = self._graph_axes[0]
        if x_moved:
            self._push_graph_view()
        ax0.set_xlim(*target)
        if x_moved:
            self._push_graph_view()
        self._user_zoomed = True
        self._retick_from_current_xlim()
        if self._mpl_canvas is not None:
            self._blit_bg = None
            self._mpl_canvas.draw_idle()

    def _push_graph_view(self):
        """Remember the current view in the toolbar history (so Back returns to it)."""
        tb = self._graph_toolbar
        if tb is None:
            return
        try:
            tb.push_current()
        except Exception:
            pass

    def _on_graph_view_home(self):
        """Home pressed: the user gave the view back, so the live window may
        scroll again, and the stamps must match the restored range. Live that
        a zoom switched off comes back on."""
        self._user_zoomed = False
        self._retick_from_current_xlim()
        if getattr(self, "_live_stopped_by_zoom", False) and not self._live_mode:
            self._live_stopped_by_zoom = False
            if self._real_pv_names():
                self._toggle_live_mode()

    def _live_off_by_zoom(self):
        """A zoom in the graph (right-drag, or the toolbar's Zoom / Pan) ends
        Live. The window that was on screen becomes the fixed From / To, so
        the labels, a reload and a later Live all mean what was shown."""
        if not self._live_mode:
            return
        span = self._live_window_span
        if span and span.total_seconds() > 0:
            now = datetime.now()
            self._dt_from, self._dt_to = now - span, now
        self._stop_live("Live mode off — zoomed in. Home or ⏵ Live mode "
                        "turns it back on.")
        self._live_stopped_by_zoom = True

    def _on_graph_tb_press(self, _event=None):
        """Remember the time range a toolbar Zoom / Pan drag starts from."""
        tb = self._graph_toolbar
        try:
            busy = tb is not None and bool(str(tb.mode or "").strip())
        except Exception:
            busy = False
        self._tb_press_xlim = (tuple(self._graph_axes[0].get_xlim())
                               if busy and self._graph_axes else None)

    def _on_graph_tb_release(self, _event=None):
        """A toolbar Zoom / Pan drag ended: if it really moved the time range,
        Live stops. Pressing the tool in alone changes nothing."""
        before = getattr(self, "_tb_press_xlim", None)
        self._tb_press_xlim = None
        if before is None or not self._live_mode:
            return
        # The toolbar applies its zoom in its own release handler, which may
        # run after this one — look once it has.
        QTimer.singleShot(0, lambda: self._live_off_if_view_moved(before))

    def _live_off_if_view_moved(self, before):
        if not self._live_mode or not self._graph_axes:
            return
        try:
            after = tuple(self._graph_axes[0].get_xlim())
        except Exception:
            return
        tol = 1e-6 * max(abs(before[1] - before[0]), 1e-9)
        if abs(after[0] - before[0]) > tol or abs(after[1] - before[1]) > tol:
            self._live_off_by_zoom()
            # The new range was set while Live held the finer read back.
            self._on_graph_xlim_changed()

    def _xy_push_view(self):
        """Put the XY plot's current view on the toolbar's history.

        Back and Forward are enabled purely from where the toolbar sits in that
        history, so a view that is never pushed is a view they cannot reach.
        """
        tb = getattr(self, "_xy_toolbar", None)
        if tb is None:
            return
        try:
            tb.push_current()
        except Exception:
            pass

    def _on_xy_view_home(self):
        """Home pressed on the XY plot: the view is the program's again, so a
        Live refresh may resume fitting the axes to the cloud."""
        self._xy_user_zoomed = False

    def _on_xy_toolbar_mode(self, checked):
        """Zoom or Pan switched on: whatever happens next is the user's view."""
        if checked:
            self._xy_user_zoomed = True

    # ── Save / clean graph ──────────────────────────────────────────────────

    def _save_graph_image(self, with_selection=True):
        """Save the graph as an image, the way it looks on screen.

        with_selection=True: a selected region is saved together with its
        statistics, as a table under the graph (the cards are Qt widgets, not
        part of the figure, so they have to be drawn into it for the save).
        with_selection=False: the blue band is left out and so are the numbers.
        """
        fig = self._mpl_figure
        if fig is None:
            QMessageBox.information(self, "No graph", "No graph to save.")
            return
        start_dir = str(self.config.get("graph_save_dir") or "")
        # A name that says what is in the picture: the period shown and when
        # it was saved, so a folder of saved graphs sorts and reads by itself.
        start_path = start_dir
        try:
            lo, hi = self._graph_axes[0].get_xlim()
            t0 = mdates.num2date(lo, tz=TZ_PRAGUE)
            t1 = mdates.num2date(hi, tz=TZ_PRAGUE)
            end_fmt = "%H%M%S" if t0.date() == t1.date() else "%Y-%m-%d %H%M%S"
            name = (f"CSS Logger {t0:%Y-%m-%d %H%M%S} to {t1.strftime(end_fmt)}"
                    f"{'' if with_selection else ' no selection'}"
                    f" (saved {datetime.now():%Y-%m-%d %H%M%S}).png")
            start_path = str(pathlib.Path(start_dir) / name) if start_dir else name
        except Exception:
            pass
        path, _flt = QFileDialog.getSaveFileName(
            self, "Save graph image", start_path,
            "PNG image (*.png);;PDF document (*.pdf);;SVG drawing (*.svg)")
        if not path:
            return
        try:
            self.config["graph_save_dir"] = str(pathlib.Path(path).parent)
        except Exception:
            pass
        try:
            self._write_graph_image(path, with_selection)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed",
                                f"The image could not be saved:\n{exc}")
            return
        self._lbl_status.setText(f"Graph saved: {path}")
        self._log(f"Graph saved: {path}")

    def _write_graph_image(self, path, with_selection=True, dpi=150):
        """The save itself, without any dialog (also what the tests call)."""
        fig = self._mpl_figure
        sel = getattr(self, "_span_selector", None)
        sel_artists = list(getattr(sel, "artists", []) or []) if sel is not None else []
        has_sel = bool(self._sel_range)
        hidden, extra = [], []
        try:
            if not with_selection:
                for a in sel_artists:
                    if a.get_visible():
                        a.set_visible(False)
                        hidden.append(a)
            elif has_sel and getattr(self, "_stats_rows", None):
                extra = self._add_stats_table_to_figure(fig)
            extra = self._add_header_to_figure(fig) + list(extra)
            fig.savefig(path, dpi=dpi, bbox_inches="tight",
                        bbox_extra_artists=extra or None,
                        facecolor="white")
        finally:
            for a in hidden:
                a.set_visible(True)
            for t in extra:
                try:
                    t.remove()
                except Exception:
                    pass
            self._blit_bg = None
            if self._mpl_canvas is not None:
                self._mpl_canvas.draw_idle()

    def _add_header_to_figure(self, fig):
        """Draw a heading ABOVE the figure for the saved image: the period
        shown, when it was saved, and the PVs on the graph in their trace
        colours. Returns the text artists (the caller removes them)."""
        from matplotlib.transforms import offset_copy
        f_pt = max(7.0, float(self._graph_opts.get("font_size", 11)) - 1.0)
        line_pt = f_pt * 1.55
        renderer = fig.canvas.get_renderer()
        texts = []

        def _txt(x_pt, y_pt, s, **kw):
            tr = offset_copy(fig.transFigure, fig=fig, x=x_pt, y=y_pt, units="points")
            t = fig.text(0.0, 1.0, s, transform=tr, fontsize=f_pt, va="bottom", **kw)
            texts.append(t)
            return t

        try:
            lo, hi = self._graph_axes[0].get_xlim()
            t0 = mdates.num2date(lo, tz=TZ_PRAGUE)
            t1 = mdates.num2date(hi, tz=TZ_PRAGUE)
            end_fmt = ("%H:%M:%S" if t0.date() == t1.date()
                       else "%Y-%m-%d %H:%M:%S")
            period = f"{t0:%Y-%m-%d %H:%M:%S} → {t1.strftime(end_fmt)}"
        except Exception:
            period = ""
        width_pt = fig.get_size_inches()[0] * 72.0
        # PV names, wrapped onto as many lines as the figure width needs.
        names = []
        for i, pv in enumerate(self._graph_pvs):
            st = self._pv_settings.get(pv, {})
            if not st.get("show", True):
                continue
            names.append((self._col_label(pv),
                          st.get("color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])))
        rows, cur, x = [], [], 6.0
        for name, color in names:
            t = _txt(0, 0, name, color=color, fontweight="bold")
            w = t.get_window_extent(renderer).width * 72.0 / fig.dpi
            if cur and x + w > width_pt - 6.0:
                rows.append(cur); cur, x = [], 6.0
            cur.append((t, x))
            x += w + 16.0
        if cur:
            rows.append(cur)
        # Bottom-up: the last row of names sits just above the figure.
        y = 4.0
        for row in reversed(rows):
            for t, x0 in row:
                t.set_transform(offset_copy(fig.transFigure, fig=fig,
                                            x=x0, y=y, units="points"))
            y += line_pt
        _txt(6.0, y, f"CSS Logger  ·  {period}   (saved "
                     f"{datetime.now():%Y-%m-%d %H:%M:%S})",
             color="#111111", fontweight="bold")
        return texts

    def _add_stats_table_to_figure(self, fig):
        """Draw the selection statistics as a small table BELOW the figure and
        return the text artists (the caller removes them after saving).

        Placed under the figure's bottom edge; savefig's tight box grows to
        take them in, so the graph itself keeps exactly its on-screen size."""
        from matplotlib.transforms import offset_copy
        rows = list(self._stats_rows)
        heads = ["PV", "N", "Avg", "Std", "Min", "Max", "P-P"]
        keys = ["n", "avg", "std", "min", "max", "ptp"]
        f_pt = max(7.0, float(self._graph_opts.get("font_size", 11)) - 1.0)
        line_pt = f_pt * 1.55
        renderer = fig.canvas.get_renderer()
        texts = []

        def _txt(x_pt, y_pt, s, **kw):
            tr = offset_copy(fig.transFigure, fig=fig, x=x_pt, y=y_pt, units="points")
            t = fig.text(0.0, 0.0, s, transform=tr, fontsize=f_pt,
                         va="top", **kw)
            texts.append(t)
            return t

        # Below the time-axis title: start under the lowest thing already drawn.
        try:
            low_px = min(a.get_tightbbox(renderer).y0 for a in fig.axes
                         if a.get_visible())
            top_pt = min(0.0, low_px * 72.0 / fig.dpi) - 6.0
        except Exception:
            top_pt = -6.0
        title = self._stats_title.text() if getattr(self, "_stats_title", None) else ""
        if title:
            _txt(6.0, top_pt, title, color="#1565C0", fontweight="bold")
            top_pt -= line_pt * 1.2

        # Column texts first, then line them up by their measured widths.
        grid = [[_txt(0, 0, h, color="#555555", fontweight="bold") for h in heads]]
        for name, color, stats in rows:
            cells = [_txt(0, 0, name, color=color, fontweight="bold")]
            cells += [_txt(0, 0, str(stats.get(k, "")), color="#111111") for k in keys]
            grid.append(cells)
        widths = [0.0] * len(heads)
        for line in grid:
            for j, t in enumerate(line):
                w_pt = t.get_window_extent(renderer).width * 72.0 / fig.dpi
                widths[j] = max(widths[j], w_pt)
        x = 6.0
        xs = []
        for w in widths:
            xs.append(x)
            x += w + 14.0
        for i, line in enumerate(grid):
            y = top_pt - i * line_pt
            for j, t in enumerate(line):
                # Numbers right-aligned in their column, names left-aligned.
                if j == 0:
                    t.set_transform(offset_copy(fig.transFigure, fig=fig,
                                                x=xs[j], y=y, units="points"))
                else:
                    t.set_ha("right")
                    t.set_transform(offset_copy(fig.transFigure, fig=fig,
                                                x=xs[j] + widths[j], y=y,
                                                units="points"))
        return texts

    def _clean_graph(self):
        # Clear only the plotted data points — keep the plot frame (axes,
        # gridlines, labels) visible so "Clean graph" empties the graph rather
        # than tearing the whole canvas down.
        if self._mpl_canvas is not None and self._graph_lines:
            for line_group in self._graph_lines:
                for ln in line_group:
                    try:
                        ln.set_data([], [])
                    except Exception:
                        pass
            for band in self._graph_bands:
                if band is not None:
                    try:
                        band.remove()
                    except Exception:
                        pass
            self._graph_bands  = []
            self._graph_env    = []
            self._graph_raw    = []
            self._graph_raw_np = []
            self._clear_selection()   # deliberate act: drop the region too
            self._mpl_canvas.draw_idle()
            self._lbl_graph_info.setText("Graph cleared — data points removed.")
        else:
            self._clear_graph()
            self._sel_range = None
            self._lbl_graph_info.setText("")

    # ── Fullscreen / windowed graph (F11 / Ctrl+F11) ──────────────────────────

    def _install_graph_shortcuts(self):
        # Application-scoped so they fire from the popup window too. Matches the
        # image slider: F11 = free-floating resizable window, Ctrl+F11 = fullscreen.
        sc_f11 = QShortcut(QKeySequence("F11"), self)
        sc_f11.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_f11.activated.connect(lambda: self._graph_popout(windowed=True))
        sc_ctrl_f11 = QShortcut(QKeySequence("Ctrl+F11"), self)
        sc_ctrl_f11.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_ctrl_f11.activated.connect(lambda: self._graph_popout(windowed=False))

    def _graph_popout(self, windowed: bool):
        """Show the Graph tab in its own window. F11 → resizable window,
        Ctrl+F11 → fullscreen. Pressing the same shortcut again docks it back."""
        # F11 is registered application-wide, so it also fires while another tab is
        # in front. Hand it to that tab instead of doing nothing — the Spectra tab
        # has its own focus mode. Keeping ONE owner of the key sequence is what
        # stops Qt from reporting an ambiguous shortcut and calling neither.
        if self._graph_popup is None and not self.isVisible():
            spectra = getattr(self.window(), "_spectra", None)
            toggle = getattr(spectra, "toggle_focus_mode", None)
            if toggle is not None and spectra.isVisible():
                toggle()
            return

        if self._graph_popup is not None:
            is_full = self._graph_popup.isFullScreen()
            # Same mode again → dock back; different mode → switch.
            if windowed and not is_full:
                self._restore_graph_from_popup(); return
            if (not windowed) and is_full:
                self._restore_graph_from_popup(); return
            self._graph_popup.set_framed(windowed)
            if windowed:
                self._graph_popup.showNormal()
                self._graph_popup.resize(1200, 800)
            else:
                self._graph_popup.showFullScreen()
            return

        # Pop the Graph tab out of the notebook into a floating window.
        idx = self._notebook.indexOf(self._tab_graph)
        self._graph_tab_index = idx if idx >= 0 else 0
        self._graph_tab_label = self._notebook.tabText(self._graph_tab_index)
        popup = _GraphPopupWindow(self)
        pl = QVBoxLayout(popup)
        popup.set_framed(windowed)
        self._notebook.removeTab(self._graph_tab_index)
        pl.addWidget(self._tab_graph)
        self._tab_graph.show()
        # "Graph only": hide the toolbar and the axis-settings table.
        self._graph_strip.hide()
        self._graph_axis_pane.hide()
        self._graph_popup = popup
        if windowed:
            popup.resize(1200, 800)
            popup.show()
        else:
            popup.showFullScreen()
        self._lbl_status.setText("Graph popped out — F11/Ctrl+F11/Esc to dock back.")

    def _restore_graph_from_popup(self):
        if self._graph_popup is None:
            return
        popup = self._graph_popup
        self._graph_popup = None
        popup.stop_watching()
        lay = popup.layout()
        if lay is not None:
            lay.removeWidget(self._tab_graph)
        self._tab_graph.setParent(None)
        # Restore the toolbar and axis-settings table hidden during pop-out.
        self._graph_strip.show()
        self._graph_axis_pane.show()
        self._notebook.insertTab(self._graph_tab_index, self._tab_graph,
                                 self._graph_tab_label)
        self._notebook.setCurrentWidget(self._tab_graph)
        popup.deleteLater()
        # The pane was hidden, so whatever the user had dragged is gone with it —
        # start again from the default split rather than from a stale size.
        self._axis_pane_user_sized = False
        QTimer.singleShot(0, self._autosize_axis_pane)   # pane was hidden
        self._lbl_status.setText("Graph docked back.")

    # ── Downsampling / averaging ──────────────────────────────────────────────

    def _avg_target_points(self) -> int:
        spin = getattr(self, "_avg_target_spin", None)
        if spin is not None:
            return int(spin.value())
        return int(self.config.get("avg_target_points", 2000))

    def _http_timeout(self) -> float:
        """Seconds to allow a SMALL archiver request (the settings file's value).

        A big request is allowed proportionally longer — see
        cpva_core._timeout_for_span — so this is the base, not the ceiling.
        """
        try:
            val = float(self.config.get("http_timeout", CPVA_HTTP_TIMEOUT))
        except (TypeError, ValueError):
            return CPVA_HTTP_TIMEOUT
        return val if 1.0 <= val <= 600.0 else CPVA_HTTP_TIMEOUT

    def _on_avg_target_changed(self, _val):
        self.config["avg_target_points"] = self._avg_target_points()
        if self._samples_by_pv:
            self._schedule_replot()   # coalesce rapid spinner clicks into one redraw

    def _grid_style_index(self, pv, grid_pvs):
        """Position of ``pv`` among the channels that have Grid ticked.

        Counted over the TICKED channels, not over every row, so the first grid is
        always a solid line and a style does not change just because some unrelated
        channel above it was hidden or added.
        """
        try:
            return list(grid_pvs).index(pv)
        except ValueError:
            return 0

    def _grid_style_for(self, pv, grid_pvs):
        """matplotlib line style for this channel's grid."""
        idx = self._grid_style_index(pv, grid_pvs)
        return _GRID_STYLES[idx % len(_GRID_STYLES)][1]

    def _grid_style_name(self, pv, grid_pvs):
        """Plain-language name of this channel's grid style, for the tooltip."""
        idx = self._grid_style_index(pv, grid_pvs)
        return _GRID_STYLES[idx % len(_GRID_STYLES)][0]

    def _bottom_margin_floor(self, fig_h_px, dpi=96):
        """Smallest bottom gap (as a figure fraction) that still fits the time
        stamps and the "Time (Prague)" title below them.

        The gap is set as a PERCENTAGE of the figure, but the text is a fixed
        number of POINTS tall — so on a short graph pane the percentage stops
        being enough. It cost the axis title: opening the statistics strip
        shortens the graph by well over a third, and the title was cut in half.

        A period spanning more than one day stamps the axis on TWO lines (date
        above time), so the room the stamps need is not a constant — ask for
        the extra line whenever they are in use, or the title is clipped again.
        """
        _opts = getattr(self, "_graph_opts", None) or {}
        f_pt  = float(_opts.get("font_size", 11))
        t_pt  = max(4.0, f_pt + float(_opts.get("tick_font_delta", -1)))
        lines = 2.0 if getattr(self, "_x_labels_two_line", False) else 1.0
        need_px = (t_pt * lines + f_pt) * (dpi / 72.0) * 1.6 + 6.0
        return min(0.5, need_px / max(1.0, float(fig_h_px)))

    def _keep_x_label_visible(self, *_):
        """Re-apply the bottom-gap floor after the canvas changes size.

        Called on every canvas resize: the margins matplotlib keeps are
        fractions, so a shorter canvas silently gives the time axis fewer pixels
        than its text needs. The gap grows when the graph shrinks and goes back to
        the user's own setting when the room returns, so no space is left unused.
        """
        fig = self._mpl_figure
        if fig is None or self._mpl_canvas is None:
            return
        # The axis columns were laid out in pixels for one width. When the
        # canvas ends up clearly wider or narrower (window, splitter or the
        # statistics strip moved), lay it out again once the resizing stops,
        # so the gaps set in Margins and spacing stay the size they were set.
        try:
            w = float(self._mpl_canvas.width())
            laid = float(getattr(self, "_graph_layout_w", 0) or 0)
            if laid > 0 and w > 150 and abs(w - laid) / laid > 0.03 \
                    and w != getattr(self, "_graph_relayout_asked", None):
                self._graph_relayout_asked = w
                self._schedule_replot(300)
        except Exception:
            pass
        try:
            h_px = float(fig.get_size_inches()[1] * fig.dpi)
            top  = float(fig.subplotpars.top)
            _opts = getattr(self, "_graph_opts", None) or {}
            want = min(top - 0.05,
                       max(0.02,
                           float(_opts.get("margin_bottom", 0.12)),
                           self._bottom_margin_floor(h_px, fig.dpi)))
            if abs(want - float(fig.subplotpars.bottom)) > 0.002:
                fig.subplots_adjust(bottom=want)
                self._blit_bg = None
                self._mpl_canvas.draw_idle()
        except Exception:
            pass

    def _apply_x_ticks(self, ax, t_lo_local, t_hi_local):
        """Put fresh time stamps on ``ax`` for the local window [t_lo, t_hi].

        The single place that installs the time-axis locators and label format.
        It has to be re-run after EVERY change of the visible range — a full
        redraw, a live scroll, a zoom, a Back — because the positions are a fixed
        list: keep the old list over a new range and the stamps drift out of view
        (a narrow zoom used to end up with barely a label on it).
        """
        from matplotlib.ticker import FuncFormatter, AutoMinorLocator, FixedLocator
        if t_lo_local is None or t_hi_local is None:
            return
        span_s = (t_hi_local - t_lo_local).total_seconds()
        if span_s <= 0:
            ax.xaxis.set_major_locator(
                mdates.AutoDateLocator(tz=TZ_PRAGUE, minticks=5, maxticks=8))
            return

        majors, minors, step_s = self._compute_x_ticks(t_lo_local, t_hi_local)
        if majors:
            ax.xaxis.set_major_locator(FixedLocator(majors))
        else:
            ax.xaxis.set_major_locator(
                mdates.AutoDateLocator(tz=TZ_PRAGUE, minticks=5, maxticks=8))
        if minors:
            ax.xaxis.set_minor_locator(FixedLocator(minors))
        else:
            ax.xaxis.set_minor_locator(AutoMinorLocator(5))

        # Clock format. "auto" now really is automatic: it drops the seconds as
        # soon as the stamps are a whole minute apart, and the clock altogether
        # once they are whole days apart. It used to be a synonym for
        # "12:34:56", so a two-day view printed eight-glyph stamps like
        # "06:00:00" and they ran into each other. "hms"/"hm" stay the user's
        # explicit choice (Graph settings → Time axis).
        _fmt_choice = str(self._graph_opts.get("x_time_format", "auto"))
        if _fmt_choice == "hm":
            _clock = "%H:%M"
        elif _fmt_choice == "hms":
            _clock = "%H:%M:%S"
        elif step_s >= 86400:
            _clock = ""                        # date only
        elif step_s >= 60 and step_s % 60 == 0:
            _clock = "%H:%M"
        else:
            _clock = "%H:%M:%S"
        same_day = t_lo_local.date() == t_hi_local.date()
        # Two lines, date above the time, and the date only where the day
        # changes — so a period spanning several days says which day each stamp
        # belongs to without repeating it under every single one.
        #
        # The labels are worked out HERE, once, in tick order, and the formatter
        # only looks them up: matplotlib calls the formatter repeatedly and not
        # necessarily left to right, so deciding "is this a new day?" inside it
        # made the dates come and go between redraws.
        # Over a few months the day and month alone are ambiguous — a year-long
        # period would open and close on the same "09-02" — so the year joins in.
        _span_s = (t_hi_local - t_lo_local).total_seconds()
        _date_fmt = "%Y-%m-%d" if _span_s > 120 * 86400 else "%m-%d"
        labels = {}
        if not same_day:
            _edges = {majors[0], majors[-1]} if majors else set()
            prev_day = None
            for num in sorted(majors):
                dt = mdates.num2date(num, tz=TZ_PRAGUE)
                date_txt = dt.strftime(_date_fmt)
                if not _clock:
                    # Stamps a whole day or more apart are dates. The two window
                    # edges are arbitrary instants, though, so they also say the
                    # time — otherwise an edge and the midnight beside it read as
                    # the very same stamp written twice.
                    labels[num] = (f"{date_txt}\n{dt.strftime('%H:%M')}"
                                   if num in _edges else date_txt)
                elif dt.date() != prev_day:
                    labels[num] = f"{date_txt}\n{dt.strftime(_clock)}"
                else:
                    labels[num] = dt.strftime(_clock)
                prev_day = dt.date()
        # The bottom gap has to know: two-line stamps need a second line's worth
        # of room or the "Time (Prague)" title below them is cut off.
        self._x_labels_two_line = any("\n" in t for t in labels.values())

        def _fmt_x(x, _p, _same=same_day, _c=_clock, _lab=labels):
            try:
                dt = mdates.num2date(x, tz=TZ_PRAGUE)
            except Exception:
                return ""
            if _same:
                return dt.strftime(_c or "%H:%M")
            hit = _lab.get(x)
            if hit is not None:
                return hit
            # Not one of our own ticks (matplotlib probing a cursor position):
            # answer without the date line, which is only meaningful in context.
            return dt.strftime(_c) if _c else dt.strftime("%m-%d")

        ax.xaxis.set_major_formatter(FuncFormatter(_fmt_x))
        # A stamp centred on the very edge of the plot hangs half of itself off
        # the figure — the right-hand one was the visible casualty, written over
        # the plot edge. Pin the outer two labels inside instead.
        #
        # Every label is reset to centred FIRST. matplotlib recycles the label
        # objects by position, so the alignment set here outlives the tick set it
        # was meant for: once a live scroll or a zoom produced one more stamp
        # than the redraw before, the stamp that used to be the right-hand edge
        # was now an interior one still glued to its right edge, i.e. printed
        # noticeably left of its own tick mark (the "15:20 is off its tick" case).
        try:
            ticks = ax.get_xticklabels()
            for t in ticks:
                t.set_horizontalalignment("center")
            if len(ticks) >= 2:
                ticks[0].set_horizontalalignment("left")
                ticks[-1].set_horizontalalignment("right")
        except Exception:
            pass

    def _retick_from_current_xlim(self):
        """Re-stamp the time axis for whatever range is on screen right now.

        Called after a zoom, a Back/Forward/Home or a pan, so the stamps always
        belong to the view being shown.
        """
        if not self._graph_axes:
            return
        ax0 = self._graph_axes[0]
        if getattr(self, "_reticking", False):
            return
        self._reticking = True
        try:
            lo_n, hi_n = ax0.get_xlim()
            if hi_n > lo_n:
                self._apply_x_ticks(ax0,
                                    mdates.num2date(lo_n, tz=TZ_PRAGUE),
                                    mdates.num2date(hi_n, tz=TZ_PRAGUE))
        except Exception:
            pass
        finally:
            self._reticking = False

    def _compute_x_ticks(self, t_lo, t_hi):
        """Major + minor X-tick positions and the step, for the local window
        [t_lo, t_hi].

        The endpoints are always ticked; interior majors land on a 'nice' step
        chosen so at most "Max time stamps" (Graph settings) fit, or on the
        fixed spacing set there. Shared by the full replot and the live fast
        path so a scrolling live window keeps FRESH ticks — otherwise the
        initial FixedLocator ticks scroll out of view and the axis goes blank.

        Returns ``(majors, minors, step_s)``; the step decides how much of the
        clock the labels need to show (see _apply_x_ticks).
        """
        span_s = (t_hi - t_lo).total_seconds()
        _opts  = getattr(self, "_graph_opts", None) or {}
        _want  = max(2, int(_opts.get("x_ticks_max", 12)))
        _fixed = max(0, int(_opts.get("x_tick_seconds", 0)))
        # The ladder used to stop at two days, so a week asked for ~85 stamps
        # and a year for ~180 — all written over each other. It now runs out to
        # a year, since a year is a period the user may legitimately ask for.
        _DAY = 86400
        _STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
                  3600, 7200, 10800, 21600, 43200,
                  _DAY, 2 * _DAY, 3 * _DAY, 7 * _DAY, 14 * _DAY,
                  30 * _DAY, 61 * _DAY, 91 * _DAY, 182 * _DAY, 365 * _DAY]
        if _fixed > 0:
            # A fixed spacing on a long window could ask for thousands of time
            # stamps (matplotlib gives up past ~1000 and the axis goes blank), so
            # the spacing is doubled until the count is sane.
            step_s = float(_fixed)
            while span_s / step_s > 120: step_s *= 2.0
        else:
            step_s = _STEPS[-1]
            for s in _STEPS:
                if span_s / s <= _want: step_s = s; break
        epoch    = datetime(t_lo.year, t_lo.month, t_lo.day, tzinfo=t_lo.tzinfo)
        offset_s = (t_lo - epoch).total_seconds()
        # Both window edges are always stamped, so an interior stamp too close
        # to one of them is simply the same instant written twice, one label on
        # top of the other. It used to need only a quarter of a step of
        # clearance — at a six-hour step over two days that is 3 % of the width,
        # and the pair at the right-hand edge always collided. Ask for a real
        # gap instead, and drop the interior stamp when there isn't one.
        _CLEAR = 0.6
        first_s  = math.ceil((offset_s + step_s * _CLEAR) / step_s) * step_s
        ticks_dt = []
        cur_s    = first_s
        while True:
            dt_tick = epoch + timedelta(seconds=cur_s)
            if dt_tick >= t_hi - timedelta(seconds=step_s * _CLEAR): break
            ticks_dt.append(dt_tick); cur_s += step_s
        majors = ([mdates.date2num(t_lo)]
                  + [mdates.date2num(d) for d in ticks_dt]
                  + [mdates.date2num(t_hi)])
        # Minor ticks: subdivide the major step by 5, as a fixed list so matplotlib
        # never infers spacing from the irregular endpoint ticks (that would blow
        # past Locator.MAXTICKS).
        minor_step = step_s / 5.0
        minors = []
        m_s = first_s - step_s
        while True:
            dt_m = epoch + timedelta(seconds=m_s)
            if dt_m > t_hi: break
            if dt_m >= t_lo: minors.append(mdates.date2num(dt_m))
            m_s += minor_step
        return majors, minors, step_s

    # (Time-binned averaging lives in the module-level _downsample_arrays_mean:
    # every caller feeds matplotlib, so the whole path stays in numpy arrays.)

    # ── Automatic loading ───────────────────────────────────────────────────

    def _finish_load(self):
        """A fetch has ended (well or badly): let the next one start, and run one
        that was asked for while this was busy."""
        self._load_in_flight = False
        self._loading_from_ns = None
        btn = getattr(self, "_btn_stop_load", None)
        if btn is not None:
            btn.hide()
        if getattr(self, "_reload_pending", False):
            self._reload_pending = False
            self._request_reload(delay_ms=0)

    def _stop_loading(self):
        """Abandon the running fetch and keep whatever has already arrived.

        A whole year is a legitimate period to ask for, so the answer to "this
        is taking too long" must be a button, not a wait. The requests that have
        not started are dropped and the ranges they covered are reported as
        unread — nothing already drawn is thrown away.
        """
        self._load_epoch += 1
        self._lbl_status.setText("Loading stopped — showing what arrived.")
        self._log("Loading stopped by the user.")
        self._finish_load()
        self._progress_bar.hide()

    def _set_load_pct(self, pct: int):
        """Move the progress bar, never backwards.

        The number of requests is not known up front: a range the archiver
        refuses is split into smaller ones, so the total grows while the load
        runs and the raw percentage can fall. The bar holds its high-water mark
        and the status line carries the honest "done / total".
        """
        try:
            self._progress_bar.setValue(max(self._progress_bar.value(), int(pct)))
        except Exception:
            pass

    def _request_reload(self, delay_ms=400, reason=""):
        """Ask for the current channels + window to be (re)loaded.

        Everything that changes WHAT should be on screen calls this instead of a
        button: adding or removing a channel, a new time window, a new preset.
        Several changes in a row collapse into one fetch, and a fetch that is
        already running is allowed to finish first.
        """
        if reason:
            self._pending_reload_reason = reason
        if getattr(self, "_load_in_flight", False):
            self._reload_pending = True     # run it when the current one lands
            return
        timer = getattr(self, "_autoload_timer", None)
        if timer is None:
            return
        timer.start(max(0, int(delay_ms)))

    def _do_auto_reload(self):
        """The debounced reload itself. Live restarts its window; otherwise the
        chosen window is fetched. Quiet when no channel is selected yet."""
        if not self._real_pv_names():
            return
        if getattr(self, "_load_in_flight", False):
            self._reload_pending = True
            return
        why = getattr(self, "_pending_reload_reason", "") or "selection changed"
        self._pending_reload_reason = ""
        self._log(f"Auto reload ({why}).")
        if self._live_mode:
            self._live_timer.stop()
            self._countdown_timer.stop()
            self._live_window_span = self._live_span_from_window()
            # The span just changed: say so next to the button. Without this the
            # label kept the span Live started with ("now − 1 h") while the
            # graph showed the month picked since.
            self._refresh_time_labels()
            self._live_last_ts = None
            self._live_last_rebuild_ns = 0
            self._live_last_graph_ns   = 0
            self._live_init_retries = 0
            self._lbl_status.setText("Live: reloading…")
            self._live_initial_load()
        else:
            self._on_load_clicked(silent=True)

    # ── Load data ───────────────────────────────────────────────────────────

    def _on_load_clicked(self, silent=False):
        """Fetch the chosen window. Reached from the automatic reload, not a button.

        ``silent`` keeps it quiet when nothing is selected yet (the automatic path
        must not pop a warning up while the user is still choosing channels).
        """
        pvs = self._real_pv_names()
        if not pvs:
            if not silent:
                QMessageBox.warning(self, "No PVs", "Add at least one PV to the list.")
            return
        # Live is NOT stopped here any more: it is the only mode switch the user
        # has, so a reload must never turn it off behind their back.
        # A PV with Show unticked is not read (see _fetch_pv_names); it keeps
        # its row in the PV table with no samples.
        fetch = self._fetch_pv_names(pvs)
        fetch_set = set(fetch)
        self._load_in_flight = True
        self._load_epoch += 1
        epoch = self._load_epoch
        self._lbl_status.setText("Loading…")
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._btn_stop_load.show()
        self._partial_table_done = False
        self._zoom_detail = None          # belongs to the previous load
        start_ns = dt_to_ns(self._dt_from)
        end_ns   = dt_to_ns(self._dt_to)
        # Read the target-points value on the UI thread (Qt widgets are not
        # thread-safe). >0 → ask the archiver to decimate; 0 → every raw sample.
        avg_target = self._avg_target_points()
        self._load_avg_target = avg_target
        timeout_base = self._http_timeout()
        day_cache = _day_cache()
        self._partial_cost = 0.0
        print(f"[CSS Logger] Load clicked: {len(pvs)} PVs, window {start_ns} → {end_ns}, "
              f"avg_target={avg_target}")
        self._load_sig = _LoadSig()          # keep alive until done
        sig = self._load_sig
        sig.done.connect(self._on_load_finished)
        sig.error.connect(self._on_load_error)
        sig.progress.connect(self._lbl_status.setText)
        sig.pct.connect(self._set_load_pct)
        sig.note.connect(self._log)
        sig.partial.connect(self._on_load_partial)

        def _worker():
            try:
                from cpva_core import (cpva_fetch_many_adaptive, cpva_decode_value,
                                       cpva_fetch_last_before_many,
                                       LAST_BEFORE_DEADLINE_S)
                samples_by_pv   = {}
                pv_order        = []
                errors          = []
                pre_window_vals = {}
                cancel = lambda: self._load_epoch != epoch     # noqa: E731

                def _decode(raw):
                    out = []
                    for s in raw:
                        t_ns = s.get("time")
                        if t_ns is None:
                            continue
                        out.append((int(t_ns), cpva_decode_value(s),
                                    (s.get("metaData") or {}).get("units", "") or ""))
                    return out

                def _seed_tuple(s):
                    return (int(s.get("time") or 0), cpva_decode_value(s),
                            (s.get("metaData") or {}).get("units", "") or "")

                # ── show the period filling in while it loads ──────────────
                # Whole days are read newest first, so the picture grows from
                # the right-hand (present) edge backwards. A new picture is made
                # only when the window has finished drawing the last one, and
                # never sooner than one and a half times what that one cost:
                # the redraw runs on the window's own thread, and back-to-back
                # redraws of months of data left it no time to answer at all.
                # The fetch threads only APPEND here, under a lock held for a
                # moment. Building a picture (merging, sorting) happens in its
                # own thread: done inside the fetch threads, under the lock, it
                # re-sorted everything read so far on every arrival and held up
                # the whole pool — measured, a 20 s load took 146 s.
                acc = {pv: [] for pv in pvs}          # pv -> [decoded chunk, …]
                acc_mm = {pv: [] for pv in pvs}       # pv -> [(ts, lo, hi) arrays]
                acc_lock = threading.Lock()
                gui_ready = threading.Event()     # set by _on_load_partial when done
                gui_ready.set()
                last_snap = [time.monotonic()]
                cov = [0.0]           # share of the period read, 0..1
                edge = [end_ns]       # everything newer than this is read
                dirty = [False]       # something arrived since the last picture
                from_cache = [0.0]    # share of the period served from cache/memory
                sent = [False]        # a picture went out already
                snap_stop = threading.Event()

                def _join_mm(parts):
                    parts = [p for p in parts if p is not None]
                    if not parts:
                        return None
                    ts = np.concatenate([p[0] for p in parts])
                    order = np.argsort(ts, kind="stable")
                    return (ts[order],
                            np.concatenate([p[1] for p in parts])[order],
                            np.concatenate([p[2] for p in parts])[order])

                def _merge(chunks):
                    rows = [r for c in chunks for r in c]
                    rows.sort(key=lambda r: r[0])
                    out, last = [], None
                    for r in rows:
                        if r[0] != last:
                            out.append(r)
                            last = r[0]
                    return out

                def _snapshotter():
                    """Send the window a picture of what has arrived, now and
                    then. Never while it is still drawing the last one, and
                    never sooner than three times what that one cost: the
                    redraw runs on the window's own thread, and back-to-back
                    redraws of months of data starve the fetch threads too."""
                    while not snap_stop.wait(0.05 if from_cache[0] >= 0.9
                                             and not sent[0] else 0.25):
                        if not dirty[0] or not gui_ready.is_set() or cancel():
                            continue
                        # Almost all of it came from the cache or from memory
                        # (a reload): show that AT ONCE. The rest is the last
                        # few minutes, and the archiver may take seconds to
                        # answer even for those.
                        if not (from_cache[0] >= 0.9 and not sent[0]):
                            # Nearly read: the full picture is moments away,
                            # and a partial one now would only delay it
                            # (measured: a seven-second redraw started at 98 %).
                            if cov[0] >= 0.9:
                                continue
                            # A picture now goes into the graph on screen
                            # (no rebuild), so a second is enough of a floor.
                            gap = max(1.0, 3.0 * float(getattr(self, "_partial_cost", 0.0)))
                            if time.monotonic() - last_snap[0] < gap:
                                continue
                        sent[0] = True
                        with acc_lock:
                            dirty[0] = False
                            parts = {p: list(v) for p, v in acc.items()}
                            mm_parts = {p: list(v) for p, v in acc_mm.items()}
                        snap = {p: _merge(v) for p, v in parts.items()}
                        snap_mm = {p: _join_mm(v) for p, v in mm_parts.items()}
                        pre = None      # hunted after the read, see below
                        gui_ready.clear()
                        last_snap[0] = time.monotonic()
                        # The cache's share is known before the coverage
                        # callback has caught up with it.
                        sig.partial.emit((snap, list(pvs), start_ns, end_ns, epoch,
                                          max(cov[0], from_cache[0]), snap_mm,
                                          gui_ready, edge[0], pre))

                def _on_chunk(pv, raw):
                    rows = _decode(raw)
                    if not rows:
                        return
                    mm = _minmax_arrays(raw)
                    with acc_lock:
                        acc.setdefault(pv, []).append(rows)
                        if mm is not None:
                            acc_mm.setdefault(pv, []).append(mm)
                        dirty[0] = True

                def _on_tile(e):
                    edge[0] = e
                    dirty[0] = True

                threading.Thread(target=_snapshotter, daemon=True,
                                 name="css-logger-snapshots").start()

                # ── progress ───────────────────────────────────────────────
                # Driven by the share of the period READ, not by the number of
                # requests: a few huge requests used to hold the bar on one
                # number for minutes. Throttled, or a long load floods Qt with
                # signal emissions and freezes the window on its own.
                shown = [-1, 0.0]
                t_start = time.monotonic()
                what = f"{len(fetch)} signal{'' if len(fetch) == 1 else 's'}"
                span = max(1, end_ns - start_ns)

                def _cover(frac):
                    cov[0] = frac
                    pct = int(frac * 95)              # the last 5 % is the tidy-up
                    now = time.monotonic()
                    if pct == shown[0] and now - shown[1] < 0.5:
                        return
                    shown[0], shown[1] = pct, now
                    sig.pct.emit(pct)
                    reached = ns_to_local_str(max(start_ns, edge[0]))[:16]
                    txt = f"Reading {what} · back to {reached}"
                    if frac > 0.02:
                        left = (now - t_start) / frac * (1 - frac)
                        if left > 5:
                            txt += (f" · about {left / 60:.0f} min left" if left > 90
                                    else f" · about {left:.0f} s left")
                    sig.progress.emit(txt)

                sig.progress.emit(f"Reading {what}")
                raw_by_pv, chunk_errors, report = cpva_fetch_many_adaptive(
                    fetch, start_ns, end_ns,
                    count=(avg_target if avg_target > 0 else None),
                    timeout=timeout_base,
                    coverage_fn=_cover, cancel_fn=cancel,
                    phase_fn=sig.progress.emit,
                    log_fn=sig.note.emit, chunk_fn=_on_chunk,
                    tile_fn=_on_tile, cache=day_cache,
                    cached_fn=lambda f: from_cache.__setitem__(0, f))
                snap_stop.set()
                sig.note.emit(f"timeline: archive read finished after "
                              f"{time.monotonic() - t_start:.1f} s")
                sig.pct.emit(96)
                if report is not None and getattr(report, "cached", 0):
                    sig.note.emit(f"{report.cached} signal-day"
                                  f"{'' if report.cached == 1 else 's'} came from "
                                  f"the day cache")

                minmax_by_pv = {}
                for pv in pvs:
                    if pv not in fetch_set:          # hidden: not read
                        samples_by_pv[pv] = []
                        minmax_by_pv[pv] = None
                        pv_order.append(pv)
                        continue
                    if pv in chunk_errors:
                        errors.append(f"{shorten_pv_name(pv)}: {chunk_errors[pv]}")
                        print(f"[CSS Logger]   → ERROR {pv}: {chunk_errors[pv]}")
                    samples_by_pv[pv] = _decode(raw_by_pv.get(pv, []))
                    minmax_by_pv[pv] = _minmax_arrays(raw_by_pv.get(pv, []))
                    pv_order.append(pv)
                    print(f"[CSS Logger]   {pv} → {len(samples_by_pv[pv])} samples")

                # The value each signal had when the window starts — only for
                # a signal whose first sample sits after the start (the others
                # carry it in the read). This step used to have no limit: on a
                # three-month load it sat at "92 %" until the program was
                # killed. It now stops at a deadline and says what it missed.
                # (Hunted here, not beside the read: wide look-backs for every
                # signal at once slowed the archiver for the read itself.)
                need = [pv for pv in fetch
                        if not samples_by_pv.get(pv)
                        or min(t for t, _v, _u in samples_by_pv[pv]) > start_ns]
                if need and not cancel():
                    sig.progress.emit("Reading the last value before the window")
                    t_hunt = time.monotonic()
                    seeds = cpva_fetch_last_before_many(
                        need, start_ns, timeout_base, cancel_fn=cancel,
                        deadline_s=LAST_BEFORE_DEADLINE_S)
                    for pv, last_s in seeds.items():
                        if last_s and last_s.get("time"):
                            pre_window_vals[pv] = _seed_tuple(last_s)
                    missing = [pv for pv in need if pv not in pre_window_vals]
                    sig.note.emit(f"timeline: value before the window for "
                                  f"{len(need)} signal{'' if len(need) == 1 else 's'}"
                                  f" took {time.monotonic() - t_hunt:.1f} s")
                    if (missing and time.monotonic() - t_hunt
                            >= LAST_BEFORE_DEADLINE_S - 0.5):
                        sig.note.emit("value before the window not found in time "
                                      "for: " + ", ".join(shorten_pv_name(p)
                                                          for p in missing))
                sig.pct.emit(100)
                print(f"[CSS Logger] Emitting done: {len(pv_order)} PVs, errors={errors}")
                sig.done.emit((samples_by_pv, pv_order, errors, start_ns, end_ns,
                               pre_window_vals,
                               {"report": report, "epoch": epoch,
                                "minmax": minmax_by_pv,
                                "fetched": tuple(fetch)}))
            except Exception as exc:
                import traceback; traceback.print_exc()
                sig.error.emit(str(exc))
            finally:
                stop = locals().get("snap_stop")
                if stop is not None:
                    stop.set()

        try:
            threading.Thread(target=_worker, daemon=True).start()
        except Exception as exc:
            # Worker never started — make sure the button/progress are restored
            # so the user can retry instead of facing a permanently disabled LOAD.
            import traceback; traceback.print_exc()
            self._on_load_error(f"Could not start loader thread: {exc}")

    def _on_load_error(self, e: str):
        self._log(f"Load error: {e}")
        self._lbl_status.setText("Error — see Log tab.")
        self._finish_load()
        self._progress_bar.hide()

    def _on_load_partial(self, payload):
        """Draw what has arrived so far, without ending the load.

        A long period is read newest first, so this fills the graph and the
        table in from the present backwards instead of leaving the user with an
        empty picture and a progress bar. Deliberately does NOT touch
        _load_in_flight, the progress bar or the summary line — the load is
        still running.
        """
        try:
            snap, pvs, start_ns, end_ns, epoch, frac, snap_mm = payload[:7]
        except Exception:
            return
        ready = payload[7] if len(payload) > 7 else None
        edge = payload[8] if len(payload) > 8 else None
        pre = payload[9] if len(payload) > 9 else None
        try:
            if epoch != self._load_epoch or not self._load_in_flight:
                return                  # a stale worker, or the load has ended
            if getattr(self, "_suspended", False):
                return                  # a hidden tab draws once, when it ends
            t0 = time.monotonic()
            self._draw_load_partial(snap, pvs, start_ns, end_ns, frac, snap_mm,
                                    edge, pre)
            # What a picture costs sets how soon the worker may send the next.
            self._partial_cost = time.monotonic() - t0
            if self._partial_cost >= 1.0:
                # The bar cannot move while a picture is drawn: say so, or a
                # stuck percentage looks like a stuck archive.
                self._log(f"timeline: a picture during loading took "
                          f"{self._partial_cost:.1f} s (at {frac:.0%} read)")
        finally:
            # Tell the worker the next picture may be made.
            if ready is not None:
                ready.set()

    def _draw_load_partial(self, snap, pvs, start_ns, end_ns, frac, snap_mm,
                           edge=None, pre=None, window=None):
        if self._partial_busy:
            return                      # the previous repaint has not finished
        self._partial_busy = True
        try:
            self._pv_stats_cache.clear()
            # Live start holds the traces out to its own window, a load to From/To.
            self._plot_window_ns = window
            # The value each signal had before the window, once the hunt for it
            # has answered. Never the PREVIOUS load's values: those belong to a
            # different start and would hold a wrong line at the left edge.
            self._pre_window_vals = dict(pre) if pre else {}
            for pv in snap:
                snap[pv].sort(key=lambda s: s[0])
            self._samples_by_pv = {
                pv: [(ts, v, u) for ts, v, u in snap.get(pv, [])
                     if start_ns <= ts <= end_ns]
                for pv in pvs}
            self._pv_order      = list(pvs)
            self._base_pv_order = list(pvs)
            self._minmax_by_pv  = snap_mm or {}
            self._numeric_pvs = {
                pv for pv, s in self._samples_by_pv.items()
                if any(isinstance(v, (int, float)) for _, v, _ in s)}
            rows = self._build_table_rows(self._samples_by_pv, list(pvs),
                                          self._pre_window_vals)
            rows = self._remove_master_only_rows(rows)
            self._table_rows_unfiltered = rows
            self._rebuild_custom_pvs()
            rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
            self._table_rows = self._apply_conditions_to_rows(rows)
            self._resync_pv_colors()
            # Everything older than this is still to come: the graph greys it.
            # The edge is where the read is complete for EVERY signal (whole
            # days, newest first); the old average over signals greyed less
            # than was really missing.
            self._loading_from_ns = (int(edge) if edge is not None
                                     else end_ns - int(frac * (end_ns - start_ns)))
            # Into the graph already on screen when it has the same axes: a
            # full rebuild per piece made the graph blink and the window stall.
            if not self._refresh_graph_in_place(sync=True):
                self._plot_graph()          # also refreshes the PV table
            # The table is filled from the first piece (so it never shows the
            # previous load's rows) and then once more when the load ends:
            # rebuilding it every second and a half made a long load feel frozen.
            if not self._partial_table_done:
                self._partial_table_done = True
                if frac >= 0.9:
                    # Nearly read (a load served from the cache): the end
                    # fills it in moments. Filling it here cost 5.7 s on the
                    # window's thread at "94 %" (3 months, 15 signals) — only
                    # the previous load's rows are cleared.
                    self._table_widget.setRowCount(0)
                else:
                    self._populate_table()
        except Exception as exc:
            import traceback; traceback.print_exc()
            self._log(f"[partial redraw skipped] {exc}")
        finally:
            self._partial_busy = False

    def _on_load_finished(self, result):
        print(f"[CSS Logger] _on_load_finished called, result type={type(result)}, len={len(result) if result else 0}")
        try:
            t0 = time.monotonic()
            self.__on_load_finished_inner(result)
            took = time.monotonic() - t0
            if took >= 1.0:
                self._log(f"timeline: drawing the loaded data took {took:.1f} s")
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[CSS Logger] ERROR in _on_load_finished:\n{tb}")
            self._log(f"[ERROR in _on_load_finished]\n{tb}")
            self._lbl_status.setText(f"Internal error — see Log tab: {exc}")
            self._finish_load()
            self._progress_bar.hide()

    def __on_load_finished_inner(self, result):
        # A fresh range can hold the same NUMBER of samples as the last one, so
        # the measured-value cache has to be dropped outright, not just re-keyed.
        self._pv_stats_cache.clear()
        meta = {}
        if len(result) == 7:
            (samples_by_pv, pv_order, errors, start_ns, end_ns,
             pre_window_vals, meta) = result
        elif len(result) == 6:
            samples_by_pv, pv_order, errors, start_ns, end_ns, pre_window_vals = result
        else:
            samples_by_pv, pv_order, errors, start_ns, end_ns = result
            pre_window_vals = {}
        # A load that was stopped still delivers what it read, and we want it —
        # but not on top of a NEWER load that has already started.
        if meta.get("epoch") not in (None, self._load_epoch) and self._load_in_flight:
            return
        report = meta.get("report")
        self._last_fetch_report = report
        self._pre_window_vals = pre_window_vals
        self._minmax_by_pv = meta.get("minmax") or {}
        # Asked for thinned points: the archiver averages anything over about
        # an hour, so a selection is then counted from its own raw read.
        self._load_thinned = (int(getattr(self, "_load_avg_target", 0) or 0) > 0
                              and end_ns - start_ns > int(3600e9))
        self._sel_raw = None
        self._sel_raw_failed = None
        self._plot_window_ns = None   # regular load uses the user's From/To
        self._finish_load()
        self._progress_bar.hide()

        # Sort every PV by timestamp (parallel fetch can arrive out of order)
        for pv in samples_by_pv:
            samples_by_pv[pv].sort(key=lambda s: s[0])

        # Seed the pre-window value from the last fetched sample BEFORE the window
        # start, so the trace begins held at the left edge instead of "in the
        # middle". The archiver sometimes returns an anchor/decimated sample at or
        # before start_ns; the explicit lookback then skips that PV (first_ts <=
        # start) and the trim below would discard the sample, leaving no seed.
        # Reuse it here (no extra HTTP) whenever no seed is set yet.
        # ANY value counts, not only a number: a channel carrying a word (a beam
        # fate, a state) also has to be held forward into the table, and taking
        # numbers only left its first rows empty.
        for pv, samples in samples_by_pv.items():
            cur = self._pre_window_vals.get(pv)
            if cur is not None and cur[1] is not None:
                continue
            for ts, v, u in reversed(samples):
                if ts >= start_ns:
                    continue
                if v is not None:
                    self._pre_window_vals[pv] = (ts, v, u)
                    break

        # Trim to requested window
        graph_samples: dict = {}
        for pv, samples in samples_by_pv.items():
            graph_samples[pv] = [(ts, v, u) for ts, v, u in samples
                                 if start_ns <= ts <= end_ns]
        self._samples_by_pv = graph_samples
        self._pv_order      = pv_order
        self._base_pv_order = list(pv_order)   # real fetched PVs (no custom channels)
        # Which of them were really read (Show unticked → not read).
        self._fetched_pvs   = set(meta.get("fetched", pv_order))

        self._numeric_pvs = {
            pv for pv, s in self._samples_by_pv.items()
            if any(isinstance(v, (int, float)) for _, v, _ in s)
        }

        # Build merged rows via sample-hold
        rows = self._build_table_rows(self._samples_by_pv, pv_order,
                                      self._pre_window_vals)

        # Apply master-PV deduplication filters
        rows = self._remove_master_only_rows(rows)
        rows = self._remove_fake_hour_boundary_rows(
            rows, boundaries=(report.boundaries if report is not None else None))
        self._table_rows_unfiltered = rows

        # Custom PVs — append the defined channels and compute their values.
        self._rebuild_custom_pvs()

        # Master-multiple filter + conditions (both fall back to showing
        # everything rather than silently blanking the table — see their
        # own _log() calls if that fallback triggers).
        rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
        self._table_rows = self._apply_conditions_to_rows(rows)

        # Build default pv_settings if not set, and keep auto-colors distinct
        # for whatever PVs are currently displayed (see _resync_pv_colors).
        self._resync_pv_colors()

        self._maybe_hunt_last_good()
        self._plot_graph()      # refreshes the PV list too, even with 0 samples
        self._populate_table()
        self._refresh_xy_choices()
        # A PV ticked Show while this was reading is read now.
        QTimer.singleShot(0, self._catch_up_shown_pvs)

        n_real = len(pv_order)
        n_custom = len(self._pv_order) - n_real
        total_pts = sum(len(v) for v in self._samples_by_pv.values())
        if errors:
            self._log("Could not read everything:\n" + "\n".join(errors))
        extra = f" + {n_custom} custom" if n_custom else ""
        self._log(f"Loaded {n_real}{extra} PVs, {total_pts} samples, "
                  f"{len(self._table_rows)} merged rows"
                  + (f" in {report.elapsed_s:.1f} s ({report.requests} requests, "
                     f"{report.splits} split)" if report is not None else "") + ".")
        suffix = self._load_truth_suffix(report, total_pts)
        all_failed = (total_pts == 0 and report is not None and n_real > 0
                      and all(report.gaps.get(pv) for pv in pv_order))
        if all_failed:
            # Never show this as "Loaded 0 pts": nothing was read, which is not
            # the same as an archive that holds nothing for this period.
            self._lbl_status.setText("Nothing could be read — see the Log tab.")
        else:
            self._lbl_status.setText(
                f"Loaded {total_pts} pts  |  {len(self._table_rows)} rows  |  "
                f"{n_real} PVs{extra}" + suffix)

    def _load_truth_suffix(self, report, total_pts: int) -> str:
        """What the status line must admit about the load that just ended.

        "Loaded 0 pts" used to be shown both when the archive genuinely holds
        nothing and when every single request had failed — which is exactly how
        a period longer than about half a day looked. These are different
        answers and the line now says which one it is.
        """
        if report is None:
            return ""
        bits = []
        incomplete = {pv: g for pv, g in (report.gaps or {}).items() if g}
        if incomplete:
            unread_h = sum(b - a for g in incomplete.values() for a, b in g) / 3.6e12
            bits.append(f"⚠ {len(incomplete)} "
                        f"signal{'' if len(incomplete) == 1 else 's'} incomplete "
                        f"({unread_h:.1f} h unread)")
            self._log("Unread ranges:\n" + "\n".join(
                f"  {shorten_pv_name(pv)}: " + ", ".join(
                    f"{ns_to_local_str(a)[:16]}→{ns_to_local_str(b)[:16]}"
                    for a, b in g[:6])
                + ("  …" if len(g) > 6 else "")
                for pv, g in incomplete.items()))
        if report.cancelled:
            bits.append("stopped")
        if report.over_budget:
            bits.append("period too long to read in full")
        if report.decimated is False:
            bits.append("raw data — the archiver does not thin it out")
        n_rows_all = len(getattr(self, "_table_rows", []) or [])
        if n_rows_all > _MAX_TABLE_ROWS:
            bits.append(f"table shows the last {_MAX_TABLE_ROWS:,} of "
                        f"{n_rows_all:,} rows")
        thinned = int(getattr(self, "_plot_thinned", 0) or 0)
        if thinned > 25000:
            bits.append(f"graph draws {thinned:,} pts as 25,000 min–max bins")
        return ("  |  " + "  |  ".join(bits)) if bits else ""

    def _build_table_rows(self, samples_by_pv, pv_order, pre_vals=None):
        """Merge multi-PV sample streams into time-aligned rows (sample-hold).

        ``pre_vals`` is ``_pre_window_vals`` — the last value each channel had
        BEFORE the window. It seeds the hold, so a channel that simply did not
        change inside the window carries its old value in every row instead of
        leaving an empty column (and taking every formula built on it down with
        it). This is the same carry-forward the graph draws; the table used to
        start every channel empty and fill it in only from its first sample.
        No row is invented: the seed only fills the rows that exist.
        """
        if not samples_by_pv: return []
        events = []
        for pv_name in pv_order:
            for ts_ns, value, units in samples_by_pv.get(pv_name, []):
                events.append((ts_ns, pv_name, value, units))
        if not events: return []
        events.sort(key=lambda e: e[0])
        merge_gap_ns = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
        rows = []
        last_values = {}
        for pv_name in pv_order:
            pre = (pre_vals or {}).get(pv_name)
            if pre is not None and pre[1] is not None:
                last_values[pv_name] = (pre[1], pre[2] if len(pre) > 2 else "")
        group_last_ts = None
        group_events  = []

        def _flush():
            if not group_events: return
            for _ts, pv_name, value, units in group_events:
                last_values[pv_name] = (value, units)
            rows.append((group_last_ts, copy(last_values)))

        for ts_ns, pv_name, value, units in events:
            if group_last_ts is None:
                group_last_ts = ts_ns
                group_events  = [(ts_ns, pv_name, value, units)]
            elif ts_ns - group_last_ts <= merge_gap_ns:
                group_last_ts = ts_ns
                group_events.append((ts_ns, pv_name, value, units))
            else:
                _flush()
                group_last_ts = ts_ns
                group_events  = [(ts_ns, pv_name, value, units)]

        _flush()
        return rows

    # ── Live mode ───────────────────────────────────────────────────────────

    def _live_span_from_window(self) -> timedelta:
        """The rolling span Live should use for the current From/To window.

        The window is used as asked — there is no cap. A wide live window is a
        real request, not a mistake: it just costs, because Live keeps the whole
        span merged, filtered and plotted on every refresh. Above a day that is
        said out loud in the Log rather than quietly shortened behind the user's
        back. Only a nonsensical span (under a minute, or beyond the archive's
        own reach) falls back to an hour.
        """
        window_diff = (self._dt_to - self._dt_from).total_seconds()
        if not (60 <= window_diff <= _LIVE_MAX_SPAN_S):
            return timedelta(hours=1)
        if window_diff > _LIVE_SLOW_SPAN_S:
            self._log(f"Live window is {window_diff/3600:.1f} h. Every refresh "
                      f"reads and redraws the whole of it, so expect it to feel "
                      f"slower than a short window.")
        return timedelta(seconds=window_diff)

    def _toggle_live_mode(self):
        if self._live_mode:
            self._stop_live()
        else:
            pvs = self._real_pv_names()
            if not pvs:
                QMessageBox.warning(self, "No PVs", "Add PVs before starting live mode."); return
            self._live_mode = True
            self._live_stopped_by_zoom = False
            self._live_window_span = self._live_span_from_window()
            self._btn_live.setText("⏹ Stop live")
            self._btn_live.setStyleSheet(_LIVE_BTN_ON_STYLE)
            self._refresh_time_labels()
            self._lbl_status.setText("Live: initial load…")
            self._load_in_flight = True
            self._live_last_ts = None
            self._live_last_rebuild_ns = 0
            self._live_last_graph_ns   = 0
            self._live_init_retries = 0      # fresh retry budget for this session
            self._live_initial_load()

    def _stop_live(self, status="Live mode stopped."):
        """Stop following "now" and show the chosen window standing still.

        Safe to call when not live. Data keeps being loaded automatically either
        way — this only decides whether the window follows the clock.
        """
        self._live_mode = False
        self._live_stopped_by_zoom = False   # _live_off_by_zoom sets it after
        # Abandon whatever is in flight, not just the next tick: a running fetch
        # pool ignores the timers entirely and would keep the GUI stuttering.
        self._live_epoch += 1
        self._live_timer.stop()
        self._countdown_timer.stop()
        self._btn_live.setText("⏵ Live mode")
        self._btn_live.setStyleSheet(_LIVE_BTN_OFF_STYLE)
        # Let a fetch start again even if the initial live fetch is still running
        # (its callback bails once _live_mode is False).
        self._finish_load()
        self._progress_bar.hide()
        self._plot_window_ns = None
        self._refresh_time_labels()
        self._lbl_status.setText(status)

    def _live_initial_load(self):
        if not self._live_mode or not self._live_window_span:
            return
        self._load_in_flight = True
        now_utc = datetime.now().astimezone(timezone.utc)
        t_from  = now_utc - self._live_window_span
        start_ns = dt_to_ns(t_from)
        end_ns   = dt_to_ns(now_utc)
        pvs      = self._real_pv_names()
        fetch    = self._fetch_pv_names(pvs)     # Show unticked → not read
        fetch_set = set(fetch)
        self._inflight_fetch = set(fetch)
        avg_target = self._avg_target_points()   # read on UI thread
        timeout_base = self._http_timeout()
        day_cache = _day_cache()
        # This starts a fresh live session, so anything still fetching for the
        # previous one (a preset switch or a new time window restarts the load) is
        # now stale and must abort instead of competing for the GIL.
        self._live_epoch += 1
        epoch = self._live_epoch
        cancel = lambda: self._live_epoch != epoch
        self._live_init_sig = _LoadSig()
        sig = self._live_init_sig
        sig.done.connect(self._after_live_initial_load)
        sig.error.connect(self._on_live_init_error)
        sig.progress.connect(lambda m: self._lbl_status.setText(m))
        sig.pct.connect(self._set_load_pct)
        sig.note.connect(self._log)
        sig.partial.connect(self._on_live_partial)
        self._partial_cost = 0.0
        self._partial_table_done = False
        self._progress_bar.setValue(0)
        self._progress_bar.show()

        def _worker():
            feed = None
            try:
                from cpva_core import (cpva_fetch_many_adaptive, cpva_decode_value,
                                       cpva_fetch_last_before_many)
                samples_by_pv = {}
                pv_order = []
                pre_window_vals = {}

                def _decode(raw):
                    out = []
                    for s in raw:
                        t_ns = s.get("time")
                        if t_ns is None:
                            continue
                        out.append((int(t_ns), cpva_decode_value(s),
                                    (s.get("metaData") or {}).get("units", "") or ""))
                    return out

                # Each signal goes on the graph as soon as it has arrived: the
                # archiver can take many seconds for the last one, and the graph
                # used to stay empty until every single request had answered.
                feed = _PartialFeed(
                    sig, fetch, start_ns, end_ns, epoch, cancel, _decode,
                    lambda: max(1.0, 3.0 * float(getattr(self, "_partial_cost", 0.0))))

                shown = [-1, 0.0]

                def _report(done, total):
                    if not total:
                        return
                    feed.on_cover(done / total)
                    pct = int(done / total * 100)
                    now = time.monotonic()
                    if pct == shown[0] and now - shown[1] < 0.2:
                        return
                    shown[0], shown[1] = pct, now
                    sig.pct.emit(pct)
                    sig.progress.emit(f"Live start · {done}/{total} requests")

                sig.progress.emit(
                    f"Live start: reading {len(fetch)} "
                    f"signal{'' if len(fetch) == 1 else 's'}…")
                raw_by_pv, _errs, _report_obj = cpva_fetch_many_adaptive(
                    fetch, start_ns, end_ns,
                    count=(avg_target if avg_target > 0 else None),
                    timeout=timeout_base,
                    progress_fn=_report, cancel_fn=cancel,
                    log_fn=sig.note.emit, cache=day_cache,
                    chunk_fn=feed.on_chunk, tile_fn=feed.on_tile)
                feed.stop()

                if cancel():
                    return                      # session ended while fetching

                minmax_by_pv = {}
                for pv in pvs:
                    samples = []
                    for s in raw_by_pv.get(pv, []):
                        t_ns = s.get("time")
                        if t_ns is None: continue
                        value = cpva_decode_value(s)
                        units = (s.get("metaData") or {}).get("units", "") or ""
                        samples.append((int(t_ns), value, units))
                    samples_by_pv[pv] = samples
                    minmax_by_pv[pv] = _minmax_arrays(raw_by_pv.get(pv, []))
                    pv_order.append(pv)

                # Carry-forward: hold the last known value so a PV without a fresh
                # sample still draws a line instead of leaving the graph empty.
                sig.progress.emit("Live init: filling gaps…")
                need_lookback = []
                for pv in fetch:
                    first_ts = min((t for t, _, _ in samples_by_pv.get(pv, [])),
                                   default=None)
                    if first_ts is None or first_ts > start_ns:
                        need_lookback.append(pv)
                try:
                    last_map = cpva_fetch_last_before_many(
                        need_lookback, start_ns, timeout_base, cancel_fn=cancel)
                except Exception:
                    last_map = {}
                if cancel():
                    return
                for pv, last_s in last_map.items():
                    if last_s and last_s.get("time"):
                        pre_window_vals[pv] = (
                            int(last_s["time"]), cpva_decode_value(last_s),
                            (last_s.get("metaData") or {}).get("units", "") or "")
                sig.pct.emit(100)
                sig.done.emit((samples_by_pv, pv_order, [], start_ns, end_ns,
                               pre_window_vals, minmax_by_pv))
            except Exception as exc:
                sig.error.emit(str(exc))
            finally:
                if feed is not None:
                    feed.stop()

        threading.Thread(target=_worker, daemon=True).start()

    def _on_live_partial(self, payload):
        """Live start: draw what has arrived so far (see _PartialFeed)."""
        try:
            snap, pvs, start_ns, end_ns, epoch, frac, snap_mm = payload[:7]
            ready, edge = payload[7], payload[8]
        except Exception:
            return
        try:
            if (epoch != self._live_epoch or not self._live_mode
                    or not self._load_in_flight
                    or getattr(self, "_suspended", False)):
                return
            t0 = time.monotonic()
            # No grey "loading" area: the signals arrive one by one, not
            # newest day first, so there is no edge to grey up to.
            self._draw_load_partial(snap, pvs, start_ns, end_ns, frac, snap_mm,
                                    start_ns, None, window=(start_ns, end_ns))
            self._partial_cost = time.monotonic() - t0
        finally:
            ready.set()

    def _on_live_init_error(self, e: str):
        """Live initial load failed — retry a couple of times before giving up so
        a transient archiver hiccup doesn't leave Live looking permanently
        broken."""
        self._log(f"Live initial load error: {e}")
        if not self._live_mode:
            # Live was switched off meanwhile. Release the fetch lock anyway or no
            # automatic reload could ever start again.
            self._finish_load()
            self._progress_bar.hide(); return
        retries = getattr(self, "_live_init_retries", 0)
        if retries < 2:
            self._live_init_retries = retries + 1
            self._lbl_status.setText(f"Live init failed — retrying "
                                     f"({self._live_init_retries}/2)…")
            QTimer.singleShot(1500, self._live_initial_load)
            return
        self._stop_live("Live init failed after retries — check archiver, see Log tab.")

    def _after_live_initial_load(self, result):
        if not self._live_mode:
            self._finish_load()
            self._progress_bar.hide(); return
        self._finish_load()
        self._progress_bar.hide()
        self._live_init_retries = 0          # success clears the retry budget
        try:
            self._zoom_detail = None
            if len(result) == 7:
                (samples_by_pv, pv_order, _, start_ns, end_ns, pre_window_vals,
                 self._minmax_by_pv) = result
            elif len(result) == 6:
                samples_by_pv, pv_order, _, start_ns, end_ns, pre_window_vals = result
                self._minmax_by_pv = {}
            else:
                samples_by_pv, pv_order, _, start_ns, end_ns = result
                pre_window_vals = {}
            self._pre_window_vals = pre_window_vals
            # Anchor the carry-forward window to the live window (without
            # clobbering the user's chosen From/To) so the held line spans it.
            self._plot_window_ns = (start_ns, end_ns)
            self._samples_by_pv = samples_by_pv
            self._pv_stats_cache.clear()      # measured values start over
            # Same pre-window seed safety net as the historical load path: if the
            # fetch already holds a sample before the window start, use it as the
            # held value so Live doesn't start the trace mid-window.
            for pv, samples in samples_by_pv.items():
                cur = self._pre_window_vals.get(pv)
                if cur is not None and cur[1] is not None:
                    continue
                best = None
                for ts, v, u in samples:
                    if ts < start_ns and v is not None:
                        if best is None or ts > best[0]:
                            best = (ts, v, u)
                if best is not None:
                    self._pre_window_vals[pv] = best
            self._pv_order      = pv_order
            self._base_pv_order = list(pv_order)
            self._fetched_pvs   = set(getattr(self, "_inflight_fetch", pv_order))
            self._numeric_pvs = {
                pv for pv, s in self._samples_by_pv.items()
                if any(isinstance(v, (int, float)) for _, v, _ in s)
            }
            rows = self._build_table_rows(samples_by_pv, pv_order,
                                          self._pre_window_vals)
            rows = self._remove_master_only_rows(rows)
            rows = self._remove_fake_hour_boundary_rows(rows)
            self._table_rows_unfiltered = rows
            self._rebuild_custom_pvs()          # add + compute derived channels
            rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
            self._table_rows = self._apply_conditions_to_rows(rows)
            self._resync_pv_colors()
            # Find latest ts for incremental fetch
            max_ts = 0
            for samples in samples_by_pv.values():
                if samples: max_ts = max(max_ts, samples[-1][0])
            self._live_last_ts = max_ts if max_ts else end_ns
            self._maybe_hunt_last_good()
            self._plot_graph()
            self._populate_table()
            self._refresh_xy_choices()
            # A PV ticked Show while this was reading is read now.
            QTimer.singleShot(0, self._catch_up_shown_pvs)
        except Exception:
            import traceback
            self._log(f"[live initial load]\n{traceback.format_exc()}")
        finally:
            # Always keep the live loop alive even if this paint failed.
            if self._live_mode:
                self._schedule_live_tick()

    def _merge_late_live_samples(self, late):
        """Put samples the archiver stored late into their place in time — in
        the window's samples and in a live selection's own copy — so the graph,
        the table and the selection count them like any other."""
        _key = lambda x: x[0]                                    # noqa: E731
        n = 0
        sel_copy = getattr(self, "_sel_samples", None) or {}
        for pv, pts in late.items():
            existing = self._samples_by_pv.get(pv)
            if not existing:
                continue
            mine = sel_copy.get(pv)
            for p in pts:
                if p[0] < existing[0][0]:
                    continue                      # older than the live window
                bisect.insort(existing, p, key=_key)
                n += 1
                if mine and p[0] >= mine[0][0]:
                    bisect.insort(mine, p, key=_key)
        if n:
            self._live_rows_stale = True
            self._log(f"Live: {n} sample{'' if n == 1 else 's'} archived late "
                      f"put in place")

    def _live_tick(self):
        if not self._live_mode: return
        if not self._real_pv_names():
            self._toggle_live_mode(); return
        # Only the PVs that are watched (Show ticked) or still needed.
        pvs = self._fetch_pv_names()
        if not pvs:
            self._schedule_live_tick(); return
        end_ns   = now_ns()
        start_ns = self._live_last_ts if self._live_last_ts else (end_ns - int(60e9))
        # Re-query from the last KNOWN sample forward so ingestion lag doesn't
        # make us skip newly-archived points — but never look back further than
        # _LIVE_TICK_LOOKBACK_NS. Without that cap the range is unbounded on a
        # quiet archiver (see the constant), and one tick fans out into dozens of
        # 1-hour chunks per PV every 300 ms.
        # Each PV keeps its own "newest sample I have". The cursor above is the
        # newest of ALL of them, and filtering every PV by it threw away a slow
        # channel's sample that reached the archiver a moment after a faster
        # channel's newer one — lost for good, and a custom PV built on it held
        # a stale value until the next full load. The query reaches a few
        # seconds further back to catch such late arrivals.
        own_last = {pv: (self._samples_by_pv.get(pv) or [(None,)])[-1][0]
                    for pv in pvs}
        start_ns -= _LIVE_LATE_ARRIVAL_NS
        min_start = end_ns - _LIVE_TICK_LOOKBACK_NS
        if start_ns < min_start:
            start_ns = min_start
        # Now and then: reach further back for samples archived late (see
        # _LIVE_BACKFILL_NS). What each PV already holds there is noted here,
        # on the window's thread, so the worker can tell the missing ones.
        have = None
        if end_ns - getattr(self, "_live_last_backfill_ns", 0) >= _LIVE_BACKFILL_EVERY_NS:
            self._live_last_backfill_ns = end_ns
            start_ns = min(start_ns, end_ns - _LIVE_BACKFILL_NS)
            have = {}
            for pv in pvs:
                s = self._samples_by_pv.get(pv) or []
                i = bisect.bisect_left(s, start_ns, key=lambda x: x[0])
                have[pv] = {x[0] for x in s[i:]}
        epoch  = self._live_epoch
        cancel = lambda: self._live_epoch != epoch
        self._inc_sig = _IncSig()
        sig = self._inc_sig
        sig.done.connect(self._on_incremental_finished)

        def _worker():
            try:
                from cpva_core import cpva_fetch_many_chunked, cpva_decode_value
                new_samples: dict = {}
                late: dict = {}             # pv -> samples archived late
                added_count = 0
                errors: list = []
                # Fetch every PV concurrently in one shared thread pool instead of
                # one HTTP request after another — with many PVs the sequential
                # loop made each tick take (n_pvs × latency), so 12 PVs updated
                # only ~every 3 s. This matches how CS Studio stays responsive.
                raw_by_pv, fetch_errs = cpva_fetch_many_chunked(
                    pvs, start_ns, end_ns, cancel_fn=cancel)
                if cancel():
                    return                  # Live was stopped mid-tick
                for pv in pvs:
                    if pv in fetch_errs:
                        errors.append(f"{shorten_pv_name(pv)}: {fetch_errs[pv]}")
                    new_pts = []
                    floor_ns = own_last.get(pv)
                    if floor_ns is None:
                        floor_ns = start_ns
                    late_pts = []
                    have_pv = have.get(pv) if have is not None else None
                    for s in raw_by_pv.get(pv, []):
                        t_ns = s.get("time")
                        if t_ns is None:
                            continue
                        t_ns = int(t_ns)
                        if t_ns <= floor_ns:
                            if (have_pv is not None and t_ns >= start_ns
                                    and t_ns not in have_pv):
                                late_pts.append((t_ns, cpva_decode_value(s),
                                                 (s.get("metaData") or {}).get("units", "") or ""))
                            continue
                        value = cpva_decode_value(s)
                        units = (s.get("metaData") or {}).get("units", "") or ""
                        new_pts.append((t_ns, value, units))
                    if new_pts:
                        new_samples[pv] = new_pts
                        added_count += len(new_pts)
                    if late_pts:
                        late[pv] = late_pts
                sig.done.emit((new_samples, added_count, end_ns, errors, late))
            except Exception as exc:
                sig.done.emit(({}, 0, end_ns, [str(exc)]))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_incremental_finished(self, result):
        if not self._live_mode: return
        try:
            # Worker emits a 5-tuple (errors, late samples); tolerate older ones.
            late = {}
            if len(result) == 5:
                new_samples, added_count, end_ns, errors, late = result
            elif len(result) == 4:
                new_samples, added_count, end_ns, errors = result
            else:
                new_samples, added_count, end_ns = result; errors = []
            if late:
                self._merge_late_live_samples(late)
            self._live_tick_count = getattr(self, "_live_tick_count", 0) + 1

            # The graph and the table are refreshed on SEPARATE clocks.
            #
            # They used to share one, so the slower of the two set the pace for
            # both: rebuilding the table means re-merging the whole accumulated
            # history, and with a dozen channels that alone can take about a
            # second — which then held the graph back to a redraw every ~5 s.
            # Now the cheap graph update runs as often as it can keep up while the
            # costly table rebuild keeps its own, slower schedule.
            now = now_ns()
            _graph_floor_ns = int(max(50, int(
                self._graph_opts.get("live_graph_min_ms", 300))) * 1e6)
            _table_floor_ns = int(max(200, int(
                self._graph_opts.get("live_table_ms", 1500))) * 1e6)
            # Each one keeps the window free most of the time: the graph's cost
            # is its refresh PLUS the render it causes (timed, see
            # _plot_graph_impl) — with a dozen stacked axes the render alone is
            # half a second, and refreshing every 0.3 s left the window no
            # time to answer the mouse at all.
            _graph_interval_ns = max(_graph_floor_ns,
                                     3 * getattr(self, "_live_graph_cost_ns", 0))
            _table_interval_ns = max(_table_floor_ns,
                                     2 * getattr(self, "_live_table_cost_ns", 0))
            _since_graph = now - getattr(self, "_live_last_graph_ns", 0)
            graph_due = _since_graph >= _graph_interval_ns
            table_due = (now - getattr(self, "_live_last_rebuild_ns", 0)) >= _table_interval_ns
            # Nothing new arrived: the picture only needs to move once the
            # window has slid by about a pixel (and at most once a second).
            if added_count > 0 or late:
                self._live_graph_news = True
            if graph_due and not getattr(self, "_live_graph_news", True):
                graph_due = _since_graph >= self._live_pixel_ns()
            hidden = getattr(self, "_suspended", False)
            if hidden:
                # A setting tab that is not on screen only collects: the merge,
                # the table and the graph wait for resume().
                graph_due = table_due = False
            elif (self.isVisible() and self._mpl_canvas is not None
                  and not self._mpl_canvas.isVisible()):
                # Another tab (Table, XY …) is in front: no graph to render.
                # It catches up on the first tick after it is shown again.
                graph_due = False
            refresh_due = table_due            # the table rebuild, as before
            if graph_due:
                self._live_last_graph_ns = now
                self._live_graph_news = False
                # Advance the carry-forward / plot window so a full replot (and the
                # held "last value" line) tracks "now" instead of the initial load.
                if self._live_window_span:
                    span_ns = int(self._live_window_span.total_seconds() * 1e9)
                    self._plot_window_ns = (now - span_ns, now)
            # The table clock is restarted only by a rebuild that really ran
            # (below). It used to restart on every due tick, so a due tick that
            # happened to bring nothing skipped the rebuild AND postponed the
            # next one — with sparse channels the custom PVs, which are only
            # recomputed here, fell further and further behind.

            if added_count > 0:
                # Advance the cursor to the newest sample we actually received —
                # NOT to wall-clock "now" — so archiver ingestion lag can't make
                # the next query start ahead of where data exists (which would
                # silently stall live updates forever).
                maxrecv = max((pts[-1][0] for pts in new_samples.values() if pts),
                              default=0)
                if maxrecv:
                    self._live_last_ts = max(self._live_last_ts or 0, maxrecv)
                cutoff_ns = None
                if self._live_window_span:
                    cutoff_ns = int((datetime.now().astimezone(timezone.utc)
                                     - self._live_window_span).timestamp() * 1e9)
                for pv, pts in new_samples.items():
                    existing = self._samples_by_pv.get(pv)
                    if existing is None:
                        existing = self._samples_by_pv[pv] = []
                    existing.extend(pts)
                # A live selection keeps its own copy of everything since its
                # start — taken BEFORE the trim, which would otherwise make it
                # stop counting once its start is older than the graph's span.
                if self._sel_range and getattr(self, "_sel_live", False):
                    self._sel_sync()
                for pv in new_samples:
                    existing = self._samples_by_pv[pv]
                    # Trim to the window. The list is in time order, so the cut
                    # is one bisect instead of a copy of the whole list per tick.
                    if cutoff_ns is not None and existing and existing[0][0] < cutoff_ns:
                        del existing[:bisect.bisect_left(existing, cutoff_ns,
                                                         key=lambda s: s[0])]
                # New samples the table has not merged yet. A due rebuild runs
                # as soon as there is something to merge — also on a later tick
                # that brings nothing itself.
                self._live_rows_stale = True
                total_pts = sum(len(v) for v in self._samples_by_pv.values())
                self._lbl_status.setText(
                    f"Live +{added_count} pts  |  total {total_pts}")
                if errors:
                    self._lbl_status.setText(
                        f"Live +{added_count} pts  |  ⚠ {len(errors)} fetch errors")
            else:
                # Nothing arrived: still move the cursor forward (minus the
                # ingestion-lag overlap), otherwise it stays pinned to the last
                # real sample and every following tick asks for an ever-wider
                # range — the reason a quiet archiver used to bring the app to
                # its knees instead of costing nothing.
                self._live_last_ts = max(self._live_last_ts or 0,
                                         end_ns - _LIVE_TICK_OVERLAP_NS)
                # Show that polling is alive and why nothing moved, so an idle
                # archiver isn't mistaken for a frozen app.
                last_ns = max((s[-1][0] for s in self._samples_by_pv.values() if s),
                              default=0)
                last_str = ns_to_local_str(last_ns)[:19] if last_ns else "—"
                if errors:
                    self._lbl_status.setText(f"Live: ⚠ {len(errors)} fetch errors — "
                                             f"see Log  |  last data {last_str}")
                else:
                    self._lbl_status.setText(f"Live: polling… no new data  |  "
                                             f"last data {last_str}")

            rebuilt = False
            if table_due and getattr(self, "_live_rows_stale", False):
                self._live_rows_stale = False
                self._live_last_rebuild_ns = now
                _table_t0 = time.perf_counter()
                rows = self._build_table_rows(
                    self._samples_by_pv, self._base_pv_order or self._pv_order,
                    self._pre_window_vals)
                rows = self._remove_master_only_rows(rows)
                rows = self._remove_fake_hour_boundary_rows(rows)
                self._table_rows_unfiltered = rows
                self._rebuild_custom_pvs()          # recompute derived channels live
                rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
                self._table_rows = self._apply_conditions_to_rows(rows)
                self._populate_table_if_seen()
                self._flush_axis_measured()   # Last/Min/Max/… keep up with Live
                self._xy_refresh_live()       # …and so does the XY cloud
                self._live_table_cost_ns = int(
                    (time.perf_counter() - _table_t0) * 1e9)
                rebuilt = True

            # Log fetch errors and an occasional heartbeat so the Log tab reveals
            # whether live polling is actually running and reaching the archiver.
            if errors:
                self._log("Live fetch errors: " + " | ".join(errors[:4])
                          + (" …" if len(errors) > 4 else ""))
            elif self._live_tick_count % 20 == 0:   # ~every 6 s at 300 ms ticks
                last_ns = max((s[-1][0] for s in self._samples_by_pv.values() if s),
                              default=0)
                self._log(f"Live polling (tick {self._live_tick_count}); "
                          f"last data {ns_to_local_str(last_ns)[:19] if last_ns else '—'}")

            # Graph on its own, faster clock. If the graph has no lines yet or a
            # channel just gained its first numeric data, the existing lines can't
            # represent it — do a full redraw; otherwise just move the existing
            # ones (the cheap path).
            if graph_due:
                _graph_t0 = time.perf_counter()
                # Custom traces are computed from the merged rows, which only the
                # (slower) table rebuild makes. Between rebuilds their newest
                # stretch is computed straight from the sources, so a derived
                # trace moves with the real ones instead of lagging behind them.
                if not rebuilt and self._custom_pvs:
                    try:
                        self._extend_custom_series_live()
                    except Exception:
                        import traceback
                        self._log(f"[live custom PVs]\n{traceback.format_exc()}")
                numeric_now = {
                    pv for pv in self._pv_order
                    if self._pv_settings.get(pv, {}).get("show", True)
                    and any(isinstance(v, (int, float))
                            for _, v, _ in self._samples_by_pv.get(pv, []))
                }
                # With no shot anywhere yet there is nothing new to draw. A full
                # rebuild on every tick (which "no lines yet" used to mean)
                # threw away the canvas under the mouse, so a selection could
                # not be dragged until the first shot came. Just slide the time
                # axis instead.
                if self._mpl_canvas is None or \
                        not numeric_now.issubset(set(self._graph_pvs)) or \
                        (not self._graph_lines and numeric_now):
                    self._plot_graph()
                elif not self._graph_lines:
                    self._scroll_live_time_axis()
                    if self._sel_range:
                        self._recompute_stats()
                else:
                    self._update_graph_data()
                # Cost of this refresh plus the render it causes; paces the
                # next one. The render runs a moment later (draw_idle), so the
                # last one measured stands in for it.
                self._live_graph_cost_ns = int(
                    (time.perf_counter() - _graph_t0
                     + getattr(self, "_last_render_s", 0.0)) * 1e9)
            # No separate slide of the time axis between refreshes any more:
            # each slide was a full render of every axis, several times a
            # second, for a move of less than a pixel.
        except Exception:
            # A failed incremental update must NOT kill the live loop or crash
            # the Qt timer callback — log and keep polling.
            import traceback
            self._log(f"[live tick]\n{traceback.format_exc()}")
        finally:
            self._schedule_live_tick()

    def _scroll_live_time_axis(self):
        """Slide the live window to "now" without touching the data.

        The costly part of a live refresh is rebuilding the traces; moving the
        visible range and re-labelling the time axis is nearly free. Doing that on
        every poll makes the window glide smoothly instead of jumping forward once
        per refresh. Left alone while the user is zoomed in on something.
        """
        if self._mpl_canvas is None or not self._graph_axes:
            return
        if self._user_zoomed or not self._live_mode:
            return
        span = self._live_window_span
        if not span or span.total_seconds() <= 0:
            return
        try:
            now_utc = datetime.now().astimezone(timezone.utc)
            x_lo, x_hi = now_utc - span, now_utc
            ax0 = self._graph_axes[0]
            ax0.set_xlim(x_lo, x_hi)
            self._apply_x_ticks(ax0, x_lo.astimezone(TZ_PRAGUE),
                                x_hi.astimezone(TZ_PRAGUE))
            self._blit_bg = None
            self._mpl_canvas.draw_idle()
        except Exception:
            pass

    def _live_pixel_ns(self) -> int:
        """How long the live window takes to slide by one pixel of the graph
        (at least a second) — a refresh with no new data sooner than this
        would draw the same picture again."""
        span = self._live_window_span
        width = 1000.0
        try:
            width = max(1.0, float(self._graph_axes[0].bbox.width))
        except Exception:
            pass
        span_ns = span.total_seconds() * 1e9 if span else 0.0
        return int(max(1e9, span_ns / width))

    def _populate_table_if_seen(self):
        """Live: write the table only when it is on screen. Writing every cell
        of thousands of rows is most of a table refresh; a hidden table is
        written once, when it is shown (see _on_table_shown)."""
        tw = self._table_widget
        if self.isVisible() and not tw.isVisible():
            self._table_unwritten = True
            return
        self._populate_table()

    def _on_table_shown(self):
        if getattr(self, "_table_unwritten", False):
            QTimer.singleShot(0, self._populate_table)

    def _live_poll_ms(self) -> int:
        """How often the archive is asked for new values (Graph settings).
        A setting tab that is not on screen asks only every
        HIDDEN_LIVE_POLL_MS — it only has to be up to date when it is shown."""
        try:
            ms = max(50, int(self._graph_opts.get("live_poll_ms", 300)))
        except Exception:
            ms = 300
        if getattr(self, "_suspended", False):
            ms = max(ms, HIDDEN_LIVE_POLL_MS)
        return ms

    def _schedule_live_tick(self):
        if not self._live_mode: return
        interval_ms = self._live_poll_ms()
        self._live_countdown_elapsed_ms = 0
        if not getattr(self, "_suspended", False):
            self._countdown_timer.start()
        self._live_timer.start(interval_ms)

    def _live_countdown_tick(self):
        if not self._live_mode:
            self._countdown_timer.stop(); return
        self._live_countdown_elapsed_ms += 50
        interval_ms = self._live_poll_ms()
        remaining   = max(0, interval_ms - self._live_countdown_elapsed_ms)
        self._lbl_tw_live.setText(
            f"Live  {remaining/1000:.1f}s")
        if remaining <= 0:
            self._countdown_timer.stop()

    # ── Conditions ──────────────────────────────────────────────────────────

    def _apply_conditions_to_rows(self, rows=None):
        """Filter rows by active conditions. Returns filtered list.

        A row survives only if every condition PV is present and inside its
        [min, max]. There is deliberately no "0 matched, show everything anyway"
        fallback: an empty table IS the answer when the whole window is out of
        range (shutter closed, laser off), and hiding that behind the full data
        set is exactly what the Conditions button exists to prevent.

        The one case that used to need that fallback — a condition on a PV that
        carries no data in this window, which would otherwise reject every row —
        is handled by skipping that condition and saying so in the log.
        """
        # Every rebuild of _table_rows goes through here, and the row dicts
        # themselves can be recomputed in place (custom PVs), so this is the one
        # place that must drop the graph's per-PV sample cache.
        self._pairs_cache = None
        if rows is None:
            rows = self._table_rows_unfiltered
        self._cond_skipped = []
        if not self._conditions or not rows:
            self._cond_drop_counts = (len(rows), len(rows))
            return list(rows)
        # Only a channel with samples INSIDE the window can judge it. The rows
        # also carry the value held from before the window, but a condition
        # judged on that alone threw away a whole month of 2025 at the end of
        # the load: PTM1/PCM4/PAP1 have no sample at all there, the partial
        # pictures (no held values yet) showed the data, and the final one
        # rejected every row on a months-old reading.
        available = {pv for pv in rows[-1][1] if self._has_window_data(pv)}
        active  = [c for c in self._conditions
                   if c.get("pv") and c["pv"] in available]
        skipped = [c["pv"] for c in self._conditions
                   if c.get("pv") and c["pv"] not in available]
        self._cond_skipped = skipped
        keep = self._rows_condition_mask(rows, active)
        filtered = [r for r, k in zip(rows, keep) if k]
        # Remembered so an empty table and an empty XY plot can say WHY they are
        # empty instead of leaving the Log tab as the only place that knows.
        self._cond_drop_counts = (len(rows), len(filtered))
        self._log_conditions_diag(skipped, len(rows), len(filtered))
        return filtered

    def _rows_condition_mask(self, rows, conds):
        """Which rows pass ``conds``. A thinned point is judged by the min/max
        of the shots behind it (:func:`_cond_range_passes`), not by its mean:
        over a long window a bin holding a few good shots among laser-off ones
        has a mean outside the range, and the day with the event vanished."""
        keep = np.ones(len(rows), dtype=bool)
        if not conds or not rows:
            return keep
        ts_i = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        sbp = getattr(self, "_samples_by_pv", None) or {}
        if all(sbp.get(c["pv"]) for c in conds):
            # Every condition channel has its samples: look them up at the
            # row times (numpy, as the graph does) instead of walking the row
            # dicts — 1.3 s on a 3-month load's 62 000 rows.
            tables = self._condition_tables(conds=conds)
            if len(tables) == len(conds):
                return self._condition_mask(ts_i, tables)
        ts = ts_i.astype(np.float64)
        mm_by = getattr(self, "_minmax_by_pv", None) or {}
        nan = float("nan")
        for c in conds:
            pv = c["pv"]
            v = np.fromiter(
                (x[0] if isinstance(x[0], (int, float)) else nan
                 for x in (rd.get(pv, (nan, "")) for _, rd in rows)),
                dtype=np.float64, count=len(rows))
            lo, hi = _envelope_for(ts, v, mm_by.get(pv))
            # No value in the row never passes (the envelope would fill it in).
            keep &= np.isfinite(v) & _cond_range_passes(
                lo, hi, c.get("min"), c.get("max"), bool(c.get("outside", False)))
        return keep

    def _has_window_data(self, pv) -> bool:
        """True when ``pv`` has at least one sample inside the window. A custom
        PV counts when one of its sources does: its own series is computed from
        the rows and so is never empty while a source is held from before."""
        sbp = getattr(self, "_samples_by_pv", None) or {}
        if not any(sbp.values()):
            return True                  # rows without their samples: trust the rows
        for d in self._custom_pvs:
            if d.get("name") == pv:
                return any(sbp.get(src) for src in
                           (d.get("bindings") or {}).values())
        return bool(sbp.get(pv))

    def _no_rows_message(self) -> str:
        """Why there are no rows, named after whatever caused it.

        A bare "No rows." reads as "the archiver had nothing" and sends the user
        hunting for a network fault, when the usual cause is their own
        Conditions throwing every row away — which is exactly what happens over
        an hour when the laser was off.
        """
        n_in, n_out = getattr(self, "_cond_drop_counts", (0, 0))
        if self._conditions and n_in and not n_out:
            pvs = ", ".join(c.get("pv", "?") for c in self._conditions
                            if c.get("pv"))
            return (f"No rows — the Conditions rejected all {n_in:,} of them "
                    f"({pvs}). Switch the Conditions off to see the data.")
        return "No rows."

    def _log_conditions_diag(self, skipped, n_in, n_out):
        """Report what the conditions did — once per distinct outcome, because
        this runs on every live refresh."""
        msgs = []
        if skipped:
            msgs.append("Conditions skipped (no data for these PVs in this "
                        "window): " + ", ".join(skipped))
        if n_out == 0 and n_in:
            pv_list = ", ".join(c.get("pv", "?") for c in self._conditions)
            msgs.append(f"Conditions discarded all {n_in} rows (active PVs: "
                        f"{pv_list}) — nothing in this window is inside the "
                        "configured ranges.")
        sig = (tuple(skipped), n_out == 0 and bool(n_in))
        if sig == getattr(self, "_cond_last_diag", None):
            return
        self._cond_last_diag = sig
        for m in msgs:
            self._log(m)

    def _row_matches_conditions(self, row_dict, conditions=None):
        # Old cssl.py semantics: {pv, min, max}. A condition PV that is absent
        # from the row excludes the row; present values must be within [min, max].
        conds = self._conditions if conditions is None else conditions
        if not conds:
            return True
        for cond in conds:
            pv = cond.get("pv")
            if not pv:
                continue
            if pv not in row_dict:
                return False
            value, _ = row_dict[pv]
            if not self._condition_value_ok(value, cond.get("min"), cond.get("max"),
                                            cond.get("outside", False)):
                return False
        return True

    def _condition_value_ok(self, value, vmin, vmax, outside=False):
        if not isinstance(value, (int, float)):
            return False
        return _cond_value_passes(value, vmin, vmax, outside)

    def _condition_tables(self, samples_by_pv=None, pre_vals=None, conds=None,
                          minmax=None):
        """Per active condition: (its samples' times, values, value before them,
        min, max) — what _condition_mask looks shots up in. Built once per
        refresh; a condition whose channel has nothing at all is skipped, the
        same rule the table rows follow. ``conds``: only these conditions.
        ``minmax``: the thinned points' extremes (default: the loaded
        window's); ``{}`` for raw samples, which are their own extremes."""
        if minmax is None:
            minmax = self._minmax_by_pv or {}
        conds = self._conditions if conds is None else conds
        if not conds:
            return []
        sbp = self._samples_by_pv if samples_by_pv is None else samples_by_pv
        pre = self._pre_window_vals if pre_vals is None else pre_vals
        tables = []
        for c in conds:
            pv = c.get("pv")
            if not pv:
                continue
            c_ts, c_v = _samples_to_ns_arrays(sbp.get(pv) or [])
            p = pre.get(pv)
            p_val = (float(p[1]) if p is not None and isinstance(p[1], (int, float))
                     else None)
            if (not c_ts.size and p_val is None) or not self._has_window_data(pv):
                continue                 # no sample in the window: skipped
            # Thinned points carry the min/max of their shots: judged by that.
            c_lo, c_hi = _envelope_for(c_ts, c_v, minmax.get(pv))
            tables.append((c_ts, c_v, p_val, c.get("min"), c.get("max"),
                           bool(c.get("outside", False)), c_lo, c_hi))
        return tables

    def _condition_mask(self, ts, tables):
        """Which of the sample times ``ts`` (ns) pass the conditions. Each
        condition channel is read as the table rows read it: its newest value up
        to one shot-merge gap after the moment (the energy of a shot is often
        archived a few ms after the other channels of that shot)."""
        keep = np.ones(ts.size, dtype=bool)
        if not tables or not ts.size:
            return keep
        gap = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
        for c_ts, c_v, p_val, vmin, vmax, outside, c_lo, c_hi in tables:
            idx = np.searchsorted(c_ts, ts + gap, side="right") - 1
            lo = np.full(ts.size, np.nan if p_val is None else p_val)
            hi = lo.copy()
            has = idx >= 0
            if has.any():
                lo[has] = c_lo[idx[has]]
                hi[has] = c_hi[idx[has]]
            keep &= _cond_range_passes(lo, hi, vmin, vmax, outside)
        return keep

    def _pre_window_ok(self):
        """Do the values from before the window pass the conditions? (They
        are drawn from the window's left edge.)"""
        row = {pv: (p[1], p[2] if len(p) > 2 else "")
               for pv, p in self._pre_window_vals.items() if p is not None}
        active = [c for c in self._conditions if c.get("pv") in row]
        return self._row_matches_conditions(row, active)

    # ── Last shot that passes the Conditions ────────────────────────────────

    def _cond_key(self):
        return tuple((c["pv"], c.get("min"), c.get("max"),
                      bool(c.get("outside", False)))
                     for c in self._conditions if c.get("pv"))

    def _window_start_ns(self):
        ov = getattr(self, "_plot_window_ns", None)
        return ov[0] if ov else dt_to_ns(self._dt_from)

    def _last_good_value(self, pv, win_start_ns):
        """``pv``'s value at the last shot before the window that passed the
        Conditions, or None (no such shot found, or none hunted for this set
        of Conditions)."""
        lg = getattr(self, "_last_good", None)
        if (not lg or lg.get("ts") is None or lg.get("conds") != self._cond_key()
                or win_start_ns is None or lg["ts"] >= win_start_ns):
            return None
        return lg["vals"].get(pv)

    def _last_good_note(self, win_start_ns):
        """What the plot must say when its lines come from the last good shot
        (or when even that could not be found). Empty when the window has rows
        of its own."""
        if not self._conditions or self._table_rows:
            return ""
        lg = getattr(self, "_last_good", None)
        if not lg or lg.get("conds") != self._cond_key():
            return ""
        if lg.get("hunting"):
            return ("Nothing in this period passes the Conditions\n"
                    "Looking back for the last shot that did")
        if lg.get("ts") is None:
            days = _LAST_PASS_DEPTH_NS // int(86400e9)
            return ("Nothing in this period passes the Conditions\n"
                    f"nor in the {days} days before it")
        if win_start_ns is None or lg["ts"] >= win_start_ns:
            return ""
        when = datetime.fromtimestamp(lg["ts"] / 1e9, TZ_PRAGUE)
        age_s = max(0, int(time.time() - lg["ts"] / 1e9))
        age = (f"{age_s // 86400} d {age_s % 86400 // 3600} h" if age_s >= 86400
               else f"{age_s // 3600} h {age_s % 3600 // 60} min" if age_s >= 3600
               else f"{age_s // 60} min")
        return ("Nothing in this period passes the Conditions\n"
                f"Lines hold the last shot that did: "
                f"{when:%d.%m.%Y %H:%M:%S} ({age} ago)")

    def _seed_rejected(self):
        """True when the window's left edge has no value the Conditions accept:
        a condition channel with no value before the window, or one whose
        value there fails."""
        # A condition skipped for this window (its channel has no sample in
        # it) has nothing to look back for either.
        conds = [c for c in self._conditions
                 if c.get("pv") and self._has_window_data(c["pv"])]
        if not conds:
            return False
        row = {pv: (p[1], "") for pv, p in self._pre_window_vals.items()
               if p is not None}
        if any(c["pv"] not in row for c in conds):
            return True
        return not self._row_matches_conditions(row, conds)

    def _maybe_hunt_last_good(self):
        """After a load (or a change of the Conditions): when the Conditions
        reject the value the window starts from, find the newest shot before
        the window that they accept, in the background, and redraw with it
        held across the window. Without this a period with the laser off drew
        nothing at all — every shot in it fails the energy conditions, and so
        does the laser-off value from just before it."""
        key = self._cond_key()
        start = self._window_start_ns()
        if not key or not self._seed_rejected():
            self._lg_epoch += 1               # nothing needed; stop any hunt
            self._last_good = None
            return
        lg = self._last_good
        if (lg and lg.get("conds") == key and lg.get("before") == start
                and not lg.get("failed")):
            return                            # already found (or being found)
        self._lg_epoch += 1
        epoch = self._lg_epoch
        cancel = lambda: self._lg_epoch != epoch
        self._last_good = {"conds": key, "before": start, "ts": None,
                           "vals": {}, "hunting": True}
        real = list(self._fetch_pv_names())      # hidden PVs are not read
        timeout = self._http_timeout()
        gap_ns = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
        sig = _LoadSig()
        self._lg_sig = sig                    # keep it alive until it fires
        sig.done.connect(lambda res, e=epoch: self._after_last_good(e, res))
        sig.progress.connect(self._log)

        def _worker():
            from cpva_core import cpva_fetch_last_before_many
            try:
                t = _last_passing_moment(key, start, gap_ns, timeout, cancel)
                vals = {}
                if t is not None and not cancel():
                    got = cpva_fetch_last_before_many(real, t + gap_ns + 1,
                                                      timeout, cancel_fn=cancel)
                    for pv, s in got.items():
                        v = cpva_decode_value(s)
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            vals[pv] = float(v)
                sig.done.emit((t, vals, None))
            except Exception as exc:
                sig.done.emit((None, {}, str(exc)))

        self._log("The Conditions reject the value this period starts from — "
                  "looking back for the last shot they accept.")
        # Started once the window has finished what it is doing now (the end of
        # a load draws the graph and fills the table right after this call):
        # its archive reads running alongside made that drawing several times
        # slower (measured: 11.5 s instead of 1.6 s at the end of a 3-month load).
        QTimer.singleShot(0, lambda: threading.Thread(
            target=_worker, daemon=True, name="css-logger-last-good").start())

    def _after_last_good(self, epoch, result):
        if epoch != self._lg_epoch:
            return                            # a newer hunt (or none) took over
        t, vals, err = result
        lg = dict(self._last_good or {})
        lg.update(ts=t, vals=dict(vals), hunting=False, failed=bool(err))
        if t is not None:
            lg["vals"].update(self._customs_from(lg["vals"]))
        self._last_good = lg
        if err:
            self._log(f"Looking back for the last good shot failed: {err}")
        elif t is None:
            self._log("No shot passes the Conditions in this period or the "
                      f"{_LAST_PASS_DEPTH_NS // int(86400e9)} days before it.")
        else:
            self._log("Last shot that passes the Conditions: "
                      f"{ns_to_local_str(t)} — held across the period.")
        self._lg_sig = None
        self._plot_graph()

    def _customs_from(self, vals):
        """Every custom channel evaluated on one set of real values, in
        definition order (a later formula can read an earlier one)."""
        plan, _broken = self._cpv_plan()
        known = dict(vals)
        out = {}
        for name, code, srcs in plan:
            if code is None:
                continue
            ns = {}
            for letter, pv in srcs:
                v = known.get(pv)
                if not isinstance(v, (int, float)):
                    ns = None
                    break
                ns[letter] = v
            if ns is None:
                continue
            try:
                val = eval(code, _CPV_SAFE_ENV, ns)
            except Exception:
                continue
            if isinstance(val, (int, float)) and not isinstance(val, bool) \
                    and val == val:
                out[name] = known[name] = float(val)
        return out

    # ── Master PV filters ────────────────────────────────────────────────────

    def _get_master_pv(self) -> str:
        if self._master_pv_edit is not None:
            return self._master_pv_edit.text().strip() or MASTER_RAMP_PV
        return self._master_pv or MASTER_RAMP_PV

    def _get_master_multiple(self) -> str:
        if self._master_multiple_edit is not None:
            return self._master_multiple_edit.text().strip()
        return self._master_multiple

    def _on_step_edited(self):
        """"Keep steps every" changed: filter the loaded rows again now."""
        txt = self._master_multiple_edit.text().strip()
        if txt == getattr(self, "_last_step_txt", None):
            return
        self._last_step_txt = txt
        self._log(f"Keep steps every: {txt or 'empty (every row)'}.")
        self._set_conditions(self._conditions_view())
        if self._sel_range:
            self._recompute_stats()

    def _on_master_pv_edited(self):
        """A different waveplate PV changes which rows are duplicates, which is
        decided when the rows are built — so read the period again."""
        txt = self._master_pv_edit.text().strip()
        if txt == getattr(self, "_last_master_txt", None):
            return
        self._last_master_txt = txt
        self._request_reload(delay_ms=0, reason="waveplate PV changed")

    def _apply_ramp_settings(self, d: dict):
        """Waveplate PV, step and dead-shot rule from a preset or a saved tab.
        Keys the dict does not carry leave the field as it is."""
        if d.get("master_pv") and self._master_pv_edit is not None:
            self._master_pv_edit.setText(str(d["master_pv"]))
            self._last_master_txt = self._master_pv_edit.text().strip()
        step = _saved_step(d)
        if step is not None and self._master_multiple_edit is not None:
            self._master_multiple_edit.setText(step)
            self._last_step_txt = step
        if "dead_shot_skip" in d and getattr(self, "_chk_dead_shot", None) is not None:
            self._chk_dead_shot.setChecked(bool(d["dead_shot_skip"]))
        if d.get("dead_shot_energy_pv") and getattr(self, "_dead_energy_edit", None) is not None:
            self._dead_energy_edit.setText(str(d["dead_shot_energy_pv"]))
            self._dead_energy_pv = self._dead_energy_edit.text().strip()
        if "dead_shot_energy_max" in d and getattr(self, "_dead_energy_spin", None) is not None:
            try:
                self._dead_energy_spin.setValue(float(d["dead_shot_energy_max"]))
            except (TypeError, ValueError):
                pass

    def _ramp_settings(self) -> dict:
        """What a preset or a saved tab stores of the RAMP / WAVEPLATE group."""
        return {
            "master_pv": self._get_master_pv(),
            "master_step": self._get_master_multiple(),
            "dead_shot_skip": bool(self._dead_shot_on),
            "dead_shot_energy_pv": self._dead_energy_pv,
            "dead_shot_energy_max": float(self._dead_energy_thr),
        }

    def _remove_master_only_rows(self, rows: list) -> list:
        master_pv = self._get_master_pv()
        if not rows or not master_pv:
            return rows
        filtered = [rows[0]]
        for ts_ns, row_dict in rows[1:]:
            _prev_ts, prev_row = filtered[-1]
            # The cheap test first: only a row where the master moved can be a
            # master-only row, and comparing every other channel of every row
            # was two seconds at the end of a three-month load.
            if prev_row.get(master_pv) != row_dict.get(master_pv):
                keys = set(prev_row.keys()) | set(row_dict.keys())
                if all(prev_row.get(pv) == row_dict.get(pv)
                       for pv in keys if pv != master_pv):
                    continue
            filtered.append((ts_ns, row_dict))
        return filtered

    def _remove_fake_hour_boundary_rows(self, rows: list, boundaries=None) -> list:
        """Drop the duplicate row each request boundary leaves behind.

        Every archiver request also returns the sample just before its own
        start, so a row can repeat at the seam between two requests.

        ``boundaries`` is {channel: [request start, …]} from the fetch. It
        matters now that the requests are not a fixed hourly grid any more: the
        "roughly 3600 s apart" test alone would, over a long period of thinned
        data whose points naturally sit about an hour apart, match nearly every
        row and empty the table. With the real boundaries known, only a row that
        actually sits on one is ever considered.
        """
        if len(rows) < 2:
            return rows
        master_pv     = self._get_master_pv()
        MIN_DIFF_NS   = int(3599 * 1e9)
        MAX_DIFF_NS   = int(3601 * 1e9)
        edges = None
        if boundaries:
            edges = sorted({int(b) for lst in boundaries.values() for b in lst})
            if not edges:
                return rows
        TOL_NS = int(1e9)

        def _on_boundary(ts_ns) -> bool:
            if edges is None:
                # No boundary list (a live tick, or an older payload): fall back
                # to the old rule, which the fixed hourly grid made safe.
                return True
            i = bisect.bisect_left(edges, ts_ns - TOL_NS)
            return i < len(edges) and edges[i] <= ts_ns + TOL_NS

        filtered = [rows[0]]
        for ts_ns, row_dict in rows[1:]:
            prev_ts, prev_row = filtered[-1]
            dt_ns = ts_ns - prev_ts
            if MIN_DIFF_NS <= dt_ns <= MAX_DIFF_NS and _on_boundary(ts_ns):
                shared = set(prev_row.keys()) & set(row_dict.keys())
                same_count = sum(
                    1 for pv in shared
                    if pv != master_pv and prev_row[pv][0] == row_dict[pv][0]
                )
                if same_count >= 2:
                    continue
            filtered.append((ts_ns, row_dict))
        return filtered

    def _filter_master_multiple_rows(self, rows: list) -> list:
        master_pv    = self._get_master_pv()
        multiple_txt = self._get_master_multiple()
        if not rows or not multiple_txt:
            return rows
        try:
            multiple = float(multiple_txt)
        except ValueError:
            return rows
        if multiple <= 0:
            return rows
        # If master PV has no data in the loaded set, skip filtering silently
        if not any(master_pv in row_dict for _, row_dict in rows):
            return rows
        # Tolerance scales with the multiple itself (0.5%) so a real-world
        # master PV (encoder jitter, float rounding) can still land "on" a
        # step — a fixed near-zero epsilon only ever matches a bit-exact value.
        tolerance = max(1e-6, abs(multiple) * 5e-3)
        filtered = []
        for ts_ns, row_dict in rows:
            if master_pv not in row_dict:
                continue
            value, _ = row_dict[master_pv]
            if not isinstance(value, (int, float)):
                continue
            nearest = round(value / multiple) * multiple
            if abs(value - nearest) <= tolerance:
                filtered.append((ts_ns, row_dict))
        if not filtered:
            # Never let this filter blank a table that otherwise has data —
            # fall back to showing everything (matches what the graph shows).
            self._log(f"Master-multiple filter matched 0/{len(rows)} rows "
                      f"(master={master_pv}, multiple={multiple}) — showing all rows instead.")
            return rows
        return filtered

    # ── Custom PVs ───────────────────────────────────────────────────────────

    @staticmethod
    def _col_letter(i: int) -> str:
        """Excel-style column letter for index i (0 -> A, 25 -> Z, 26 -> AA)."""
        s = ""; i += 1
        while i > 0:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    def _channel_letters(self, names=None) -> list:
        """Ordered ``[(letter, name, display_name)]`` for every channel currently
        in ``_pv_order`` (real PVs first, then custom channels), or for ``names``
        if given. These letters are the variables the user types in custom-PV
        expressions (e.g. ``B/D``) — they are positional and therefore change
        whenever the PV list does, which is why each formula stores its own
        letter -> PV bindings."""
        custom_names = {d.get("name", "") for d in self._custom_pvs}
        out = []
        for i, pv in enumerate(self._pv_order if names is None else names):
            if pv in custom_names:
                disp = pv
            else:
                disp = self._col_label(pv)
            out.append((self._col_letter(i), pv, disp))
        return out

    def _cpv_dialog_channels(self) -> list:
        """Channel rows for the custom-PV dialog:
        ``[(letter, pv_name, display_name, loaded)]`` — every currently loaded
        channel, followed by every PV a formula is bound to that is *not* loaded
        right now. The unloaded ones still get a letter so expressions stay
        readable; the dialog marks them and spells out their PV name."""
        loaded = self._channel_letters()
        if not loaded:
            # Before the first load ``_pv_order`` is still empty — letter the
            # sidebar list instead, so the dialog matches what the user sees.
            loaded = self._channel_letters(
                self._real_pv_names()
                + [d.get("name", "") for d in self._custom_pvs if d.get("name")])
        rows = [(lt, pv, disp, True) for lt, pv, disp in loaded]
        seen = {pv for _lt, pv, _d in loaded}
        for d in self._custom_pvs:
            for pv in (d.get("bindings") or {}).values():
                if pv and pv not in seen:
                    seen.add(pv)
                    rows.append((self._col_letter(len(rows)), pv,
                                 shorten_pv_name(pv), False))
        return rows

    def _migrate_custom_pv_bindings(self):
        """One-time upgrade of custom_pvs.json entries saved before bindings
        existed: their letters are interpreted through _CPV_LEGACY_LETTERS (the
        PV order they were written against) and stored explicitly, so they keep
        their meaning from now on no matter how the PV list is reordered."""
        migrated = []
        for d in self._custom_pvs:
            if d.get("bindings") or not d.get("name") or not d.get("expr"):
                continue
            b = {lt: _CPV_LEGACY_LETTERS[lt] for lt in _cpv_vars(d["expr"])
                 if lt in _CPV_LEGACY_LETTERS}
            if not b:
                continue
            d["bindings"] = b
            migrated.append((d["name"], b))
        if not migrated:
            return
        save_custom_pvs(self._custom_pvs)
        self._log("Custom PV letters bound to PV names (from the legacy channel order):")
        for name, b in migrated:
            self._log("    {}: {}".format(
                name, ", ".join(f"{lt} = {pv}" for lt, pv in b.items())))

    def _cpv_plan(self):
        """What every custom PV is computed from: ``(name, code, [(letter, pv)])``
        in definition order, plus the set of names that cannot be computed at
        all. Problems are collected into ``_cpv_diag`` instead of swallowed."""
        defs = [d for d in self._custom_pvs if d.get("name") and d.get("expr")]
        self._cpv_diag = []
        self._cpv_short = []           # the same, short enough for INFO
        if not defs:
            return [], set()
        loaded     = set(self._pv_order)
        plan   = []                    # (name, code, [(letter, source_pv)])
        broken = set()                 # names already reported as not computable
        for d in defs:
            name, expr = d["name"], d["expr"]
            bindings   = d.get("bindings") or {}
            srcs, missing, unknown = [], [], []
            # INFO speaks only of the custom PVs ticked Show in the PV table:
            # the list is shared by every preset, and a formula nobody watches
            # here is not news. The Log still gets every one.
            watched = self._pv_settings.get(name, {}).get("show", True)
            short = self._cpv_short if watched else []
            for letter in _cpv_vars(expr):
                # The binding only. A letter's place in today's list is not a
                # fallback: it names a different PV after every preset switch.
                pv = bindings.get(letter)
                if not pv:
                    unknown.append(letter)
                    continue
                srcs.append((letter, pv))
                if pv not in loaded:
                    missing.append(f"{letter} = {pv}")
            if missing:
                self._cpv_diag.append(
                    f"Custom PV '{name}': not computed — "
                    f"{', '.join(missing)} not in the loaded PV list.")
                short.append(
                    f"{name}: not computed — "
                    + ", ".join(f"{m.split(' = ')[0]} = {shorten_pv_name(m.split(' = ', 1)[1])}"
                                for m in missing)
                    + " is not in the PV list")
            if unknown:
                self._cpv_diag.append(
                    f"Custom PV '{name}': not computed — "
                    f"{', '.join(unknown)} is not bound to a PV.")
                short.append(
                    f"{name}: not computed — {', '.join(unknown)} has no PV")
            try:
                code = compile(expr, "<cpv>", "eval")
            except Exception as exc:
                code = None
                self._cpv_diag.append(
                    f"Custom PV '{name}': invalid expression — {exc}")
                short.append(f"{name}: the formula is not valid")
            if missing or unknown or code is None:
                broken.add(name)
            plan.append((name, code, srcs))
        return plan, broken

    def cpv_defs_for_spectra(self) -> list:
        """The custom PVs as the Spectra tab's shot filter needs them.

        ``[{name, expr, code, srcs: [(letter, source)], problem}]`` — the same
        letter resolution as ``_cpv_plan`` (each letter's own binding, never its
        place in the list), but nothing is required to be loaded here: Spectra
        fetches the sources itself, per shot. A source may be another custom
        PV's name. ``problem`` is a short sentence when the formula cannot be
        computed at all (unbound letter, bad expression), else ``""``."""
        defs = [d for d in self._custom_pvs if d.get("name") and d.get("expr")]
        if not defs:
            return []
        out = []
        for d in defs:
            name, expr = d["name"], d["expr"]
            bindings = d.get("bindings") or {}
            srcs, unknown = [], []
            for letter in _cpv_vars(expr):
                pv = bindings.get(letter)
                if pv:
                    srcs.append((letter, pv))
                else:
                    unknown.append(letter)
            problem = ""
            try:
                code = compile(expr, "<cpv>", "eval")
            except Exception:
                code = None
                problem = "the formula is not valid"
            if unknown:
                problem = f"{', '.join(unknown)} is not bound to a PV"
            out.append({"name": name, "expr": expr, "code": code,
                        "srcs": srcs, "problem": problem})
        return out

    def _compute_custom_pvs_in_rows(self, rows):
        """Evaluate every custom PV for each row.

        Each formula resolves its variables through its own ``bindings``
        (letter -> PV name), so its values follow the PVs even after the list is
        reordered or another preset is loaded. A letter with no binding leaves
        the custom PV empty (and says so) — it never borrows whichever PV holds
        that letter today. Customs are computed in definition
        order, so a later custom can read an earlier one — its binding simply
        names that custom channel."""
        # Whole columns at a time. This used to be one eval() per row per
        # formula (100 000 rows × 3 formulas ≈ a second on the GUI thread, on
        # every live table refresh). Each source is read out of the rows once
        # into an array, and the formula runs once over the arrays.
        plan, broken = self._cpv_plan()
        self._cpv_last_plan = plan
        self._cpv_cols = {}
        if not plan:
            return
        n = len(rows)
        grid = np.array([r[0] for r in rows], dtype=np.int64)
        cols = {}                      # custom name → its computed column
        read = {}                      # real PV → its column, read from the rows

        _none = (None,)
        _nan = float("nan")

        def _column(pv):
            if pv in cols:
                return cols[pv]
            if pv not in read:
                # Plain comprehensions, no function call per row: this read is
                # the whole cost of the compute (the formula itself is one
                # numpy call), so it is kept as tight as Python allows.
                # (Not np.array(raw, float): that would turn a word value "5"
                # into the number 5, where the formula must see "no number".)
                raw = [rd.get(pv, _none)[0] for _t, rd in rows]
                read[pv] = np.array(
                    [v if isinstance(v, (int, float)) else _nan for v in raw],
                    dtype=np.float64)
            return read[pv]

        for name, code, srcs in plan:
            if code is None or n == 0:
                res = np.full(n, np.nan)
            else:
                src_cols = {letter: _column(pv) for letter, pv in srcs}
                res = _cpv_eval_columns(code, src_cols, n)
                if name not in broken:
                    # Rows where every source had a number and still no result:
                    # the formula itself fails there (division by zero, …). The
                    # first such row is evaluated once more, plainly, to name
                    # the error.
                    have = (np.logical_and.reduce([np.isfinite(c) for c in src_cols.values()])
                            if src_cols else np.ones(n, dtype=bool))
                    bad_idx = np.flatnonzero(have & ~np.isfinite(res))
                    if bad_idx.size:
                        i0 = int(bad_idx[0])
                        first = "no result"
                        try:
                            eval(code, _CPV_SAFE_ENV,
                                 {k: float(c[i0]) for k, c in src_cols.items()})
                        except Exception as exc:
                            first = type(exc).__name__
                        self._cpv_diag.append(
                            f"Custom PV '{name}': {bad_idx.size} of {n} rows could "
                            f"not be evaluated ({first}).")
            cols[name] = res
            vals = res.tolist()
            for (_ts, row_dict), v in zip(rows, vals):
                row_dict[name] = (None if v != v else v, "")
        self._cpv_cols = {name: (grid, cols[name]) for name in cols}

    def _seed_custom_pv_pre_window(self):
        """Give every custom channel the value it had BEFORE the window starts.

        A custom PV is computed from the merged rows, so it had nothing to hold
        where there are no rows yet: the derived traces began at the first shot
        of the window (and an archived window in which none of the sources
        happened to change left them empty from end to end), while the real
        channels held their last known value across the whole of it. Evaluating
        the formula on the sources' own pre-window values gives the derived
        channel the same left edge as the channels it is built from.

        Runs in definition order, so a formula reading another custom channel
        picks up the value seeded just above it.
        """
        plan, _broken = self._cpv_plan()
        for name, code, srcs in plan:
            val, ts = None, None
            if code is not None:
                ns = {}
                for letter, pv in srcs:
                    pre = self._pre_window_vals.get(pv)
                    v = pre[1] if pre else None
                    if not isinstance(v, (int, float)):
                        ns = None
                        break
                    ns[letter] = v
                    ts = pre[0] if ts is None else max(ts, pre[0])
                if ns is not None:
                    try:
                        val = eval(code, _CPV_SAFE_ENV, ns)
                    except Exception:
                        val = None
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                self._pre_window_vals[name] = (ts or 0, val, "")
            else:
                self._pre_window_vals.pop(name, None)

    def _emit_custom_pv_diag(self):
        """Log what the last compute found wrong — once. This runs on every live
        refresh, so an unchanged set of messages is not logged again."""
        msgs = list(getattr(self, "_cpv_diag", []) or [])
        # The ones that leave a custom PV empty are also said in INFO: a custom
        # PV whose source is not in the PV list (another preset was loaded)
        # used to vanish with only a line in the Log.
        lbl = getattr(self, "_lbl_cpv_info", None)
        if lbl is not None:
            lbl.setText("\n".join(getattr(self, "_cpv_short", []) or []))
        sig  = "\n".join(msgs)
        if sig == getattr(self, "_cpv_last_diag", None):
            return
        self._cpv_last_diag = sig
        for m in msgs:
            self._log(m)

    def _rebuild_custom_pvs(self):
        """Refresh the custom channels: re-sync ``_pv_order`` to base PVs + the
        currently-defined customs, compute their values into the unfiltered rows,
        and rebuild their derived sample series."""
        base = getattr(self, "_base_pv_order", None)
        if base is None:
            custom_names = {d.get("name", "") for d in self._custom_pvs}
            base = [p for p in self._pv_order if p not in custom_names]
            self._base_pv_order = list(base)
        self._pv_order = list(base)
        for d in self._custom_pvs:
            name = d.get("name", "")
            if name and name not in self._pv_order:
                self._pv_order.append(name)
                self._pv_settings.setdefault(name, self._get_pv_default_settings(
                    name, len(self._pv_order) - 1))
        # drop derived series for customs that no longer exist
        cur = {d.get("name", "") for d in self._custom_pvs}
        for name in [p for p in list(self._samples_by_pv) if p not in self._base_pv_order]:
            if name not in cur:
                self._samples_by_pv.pop(name, None)
                self._numeric_pvs.discard(name)

        # Hold-forward first: the derived channels get the value their sources
        # had before the window, so they span it exactly like a real PV does.
        self._seed_custom_pv_pre_window()
        self._compute_custom_pvs_in_rows(self._table_rows_unfiltered)
        plan = getattr(self, "_cpv_last_plan", None) or []
        self._emit_custom_pv_diag()

        # The traces: one point per real shot. The table rows above are one per
        # moment ANY channel wrote (a slow housekeeping reading adds rows of its
        # own), so a custom PV built on them had more points than there were
        # shots. Without a shot rate (only slow channels loaded) the rows stay.
        series = {}
        starts, ends, _gap, _members = self._window_shots()
        if plan and starts.size:
            series = self._custom_series_on_shots(
                plan, self._samples_by_pv, self._pre_window_vals, starts, ends)
        cpv_cols = getattr(self, "_cpv_cols", {}) or {}
        for d in self._custom_pvs:
            name = d.get("name", "")
            if not name:
                continue
            if name in series:
                grid, res = series[name]
            elif name in cpv_cols:
                grid, res = cpv_cols[name]
            else:
                grid = res = None
            if grid is not None:
                ok = np.isfinite(res)
                self._samples_by_pv[name] = [
                    (t, v, "") for t, v in zip(grid[ok].tolist(), res[ok].tolist())]
            else:
                self._samples_by_pv[name] = []
            if self._samples_by_pv[name]:
                self._numeric_pvs.add(name)

    def _shot_ts_by_pv(self, sbp):
        """Sample times of every real (archived) channel in ``sbp`` — what the
        shot finder reads. Custom channels are derived, they cast no vote."""
        real = set(self._base_pv_order or self._real_pv_names())
        out = {}
        for pv in real:
            s = sbp.get(pv)
            if s:
                ts, _v = _samples_to_ns_arrays(s)
                if ts.size:
                    out[pv] = ts
        return out

    def _window_shots(self):
        """The shots of the loaded window: ``(starts, ends, gap_ns, members)``
        (see cpva_core.find_shots). Remembered until a channel's samples change,
        so the many refreshes of one load find them once."""
        sbp = self._samples_by_pv
        real = set(self._base_pv_order or self._real_pv_names())
        key = tuple(sorted((pv, id(s), len(s), s[0][0] if s else None,
                            s[-1][0] if s else None)
                           for pv, s in sbp.items() if pv in real))
        hit = getattr(self, "_window_shots_memo", None)
        if hit is not None and hit[0] == key:
            return hit[1]
        res = find_shots(self._shot_ts_by_pv(sbp))
        self._window_shots_memo = (key, res)
        return res

    def _custom_series_on_shots(self, plan, sbp, pre, starts, ends):
        """{custom name: (shot times, values)} — every formula evaluated once per
        shot. Each source is read at the END of its shot (its newest sample up to
        the last write of that moment, else the value it held), so a channel
        archived a little after the others still counts for its own shot. A
        later formula may read an earlier custom channel: it gets that one's
        value on the same shot."""
        out = {}
        n = int(starts.size)
        ends_i = np.asarray(ends, dtype=np.int64)
        for name, code, srcs in plan:
            if code is None or not srcs:
                out[name] = (starts, np.full(n, np.nan))
                continue
            cols = {}
            for letter, pv in srcs:
                if pv in out:
                    cols[letter] = out[pv][1]
                else:
                    p = pre.get(pv)
                    cols[letter] = _held_column(ends_i, sbp.get(pv) or [],
                                                p[1] if p is not None else None)
            out[name] = (starts, _cpv_eval_columns(code, cols, n))
        return out

    def _extend_custom_series_live(self):
        """Append the newest stretch of every custom trace, computed straight
        from its sources' samples (Live, between two table rebuilds).

        Only the part after the trace's last point is computed, so a tick costs
        a few points per formula. With a shot rate known (from the last table
        rebuild) the new points are the new shots, as in the rebuild; without
        one, the sources' own timestamps merged by the table's gap rule. The
        next table rebuild replaces the whole series again.
        """
        plan, broken = self._cpv_plan()
        if not plan:
            return
        memo = getattr(self, "_window_shots_memo", None)
        gap, members = (memo[1][2], memo[1][3]) if memo else (None, [])
        if gap and members:
            self._extend_custom_on_new_shots(plan, broken, gap, members)
            return
        gap_ns = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
        pre = self._pre_window_vals or {}
        _key = lambda s: s[0]
        for name, code, srcs in plan:
            if code is None or name in broken or not srcs:
                continue
            mine = self._samples_by_pv.get(name)
            if mine is None:
                mine = self._samples_by_pv[name] = []
            if mine:
                t_tail = mine[-1][0]
            elif self._table_rows_unfiltered:
                t_tail = self._table_rows_unfiltered[-1][0]
            else:
                t_tail = None
            # The new moments: every source sample after the tail.
            new_ts = []
            for _letter, pv in srcs:
                s = self._samples_by_pv.get(pv) or []
                i = 0 if t_tail is None else bisect.bisect_right(s, t_tail, key=_key)
                new_ts.extend(x[0] for x in s[i:])
            if not new_ts:
                continue
            ts = np.unique(np.asarray(new_ts, dtype=np.int64))
            if ts.size > 1:
                ts = ts[np.r_[np.diff(ts) > gap_ns, True]]
            cols = {}
            for letter, pv in srcs:
                s = self._samples_by_pv.get(pv) or []
                # From the sample holding at the tail onwards: enough to read
                # the held value at every new moment.
                i = 0 if t_tail is None else max(
                    0, bisect.bisect_right(s, t_tail, key=_key) - 1)
                p = pre.get(pv)
                cols[letter] = _held_column(ts, s[i:], p[1] if p is not None else None)
            res = _cpv_eval_columns(code, cols, ts.size)
            ok = np.isfinite(res)
            if ok.any():
                mine.extend((t, v, "") for t, v in zip(ts[ok].tolist(), res[ok].tolist()))
                self._numeric_pvs.add(name)

    def _extend_custom_on_new_shots(self, plan, broken, gap, members):
        """Live, between two table rebuilds: one new custom point per new shot
        after the trace's last one. A moment that most deciding channels have
        not written yet is not a shot yet — it is looked at again next tick."""
        from cpva_core import shot_moments, SHOT_JOIN_FRACTION
        pre = self._pre_window_vals or {}
        tol = SHOT_JOIN_FRACTION * float(gap)
        sbp = self._samples_by_pv
        for name, code, srcs in plan:
            if code is None or name in broken or not srcs:
                continue
            mine = sbp.get(name)
            if mine is None:
                mine = sbp[name] = []
            # The writes of the last counted shot sit up to a join gap after
            # its start; past that, everything is new.
            t_tail = mine[-1][0] + tol if mine else None
            ts_by = {}
            for pv in members:
                ts, _v = _samples_to_ns_arrays(sbp.get(pv) or [])
                if t_tail is not None:
                    ts = ts[ts > t_tail]
                if ts.size:
                    ts_by[pv] = ts
            if not ts_by:
                continue
            starts, ends = shot_moments(ts_by, gap, members)
            if not starts.size:
                continue
            res = self._custom_series_on_shots(
                [(name, code, srcs)], sbp, pre, starts, ends)[name][1]
            ok = np.isfinite(res)
            if ok.any():
                mine.extend((t, v, "") for t, v in
                            zip(starts[ok].tolist(), res[ok].tolist()))
                self._numeric_pvs.add(name)

    # ── Save runtime state ────────────────────────────────────────────────────

    def _save_runtime_state(self):
        self.config["pv_list"] = self._real_pv_names()
        self.config["pv_display_names"] = dict(self._pv_display_names)
        self.config["conditions"]      = [dict(c) for c in self._conditions_view()]
        self.config["conditions_enabled"] = bool(getattr(self, "_conditions_on", True))
        self.config["sel_pause_restart"] = bool(getattr(self, "_sel_pause_on", False))
        self.config["dead_shot_skip"] = bool(getattr(self, "_dead_shot_on", True))
        self.config["dead_shot_energy_pv"] = getattr(self, "_dead_energy_pv", "")
        self.config["dead_shot_energy_max"] = float(
            getattr(self, "_dead_energy_thr", 0.05))
        self.config["sel_pause_s"]       = float(getattr(self, "_sel_pause_s", 15.0))
        ed = getattr(self, "_pv_time_cond_editor", None)
        if ed is not None:
            self.config["pv_time_conditions"] = ed.conditions()
        self.config["master_pv"]       = self._get_master_pv()
        self.config["master_step"]     = self._get_master_multiple()
        self.config.pop("master_multiple", None)
        self.config["avg_target_points"] = self._avg_target_points()
        self.config["graph_opts"]        = dict(self._graph_opts)
        # Named looks only — the CURRENT colours, styles and reference lines are
        # deliberately not carried over to the next start.
        self.config["style_presets"]     = dict(self._style_presets)
        self.config["time_from"] = self._dt_from.strftime("%Y-%m-%d %H:%M:%S")
        self.config["time_to"]   = self._dt_to.strftime("%Y-%m-%d %H:%M:%S")
        save_config(self.config)

    # ── Table population ────────────────────────────────────────────────────

    def _set_table_cell(self, row: int, col: int, text: str, severity: str):
        """Write one cell, reusing the QTableWidgetItem that is already there.

        A live refresh rewrites the whole visible slice, and allocating a fresh
        item per cell (5000 rows × ~10 columns) was the most expensive thing on
        the UI thread during a live session. Reusing the item — and touching
        text / colour only when they actually change — keeps the same output for
        a fraction of the cost. The severity is cached on the item itself so the
        colour can be reset when a cell stops being alarming.
        """
        item = self._table_widget.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table_widget.setItem(row, col, item)
        elif item.text() != text:
            item.setText(text)
        if item.data(Qt.ItemDataRole.UserRole) != severity:
            item.setData(Qt.ItemDataRole.UserRole, severity)
            colour = _TABLE_SEVERITY_FG.get(severity)
            if colour is None:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
            else:
                item.setForeground(QColor(colour))

    def _col_label(self, pv: str) -> str:
        """The heading for one channel — the name it wears on the graph, so a
        renamed channel is called the same thing everywhere (and in the file)."""
        name = (self._pv_settings.get(pv, {}) or {}).get("display_name", "")
        return ((name or "").strip()
                or (getattr(self, "_pv_display_names", {}).get(pv) or "").strip()
                or shorten_pv_name(pv))

    def _table_pvs(self) -> list:
        """The channels the table (and the export) shows — the ones on the graph.

        Two rules, both "show what is really there":
          * a channel unticked in the PV list under the graph is not drawn, so it
            is not a column either;
          * a channel with nothing to show in this window — no sample and no
            held value from before it — would only be an empty column.
        Non-numeric channels (a word value such as a beam fate) stay: they are
        not drawn on the graph, but they belong in a table of shots.
        """
        out = []
        for pv in self._pv_order:
            if not self._pv_settings.get(pv, {}).get("show", True):
                continue
            if self._samples_by_pv.get(pv) or pv in self._pre_window_vals:
                out.append(pv)
        return out

    def _populate_table(self):
        self._table_unwritten = False         # see _populate_table_if_seen
        self._table_display_offset = 0
        if not self._table_rows:
            self._table_widget.setRowCount(0)
            self._table_widget.setColumnCount(1)
            self._table_widget.setHorizontalHeaderLabels(["Timestamp"])
            self._table_cols_cache = ["Timestamp"]
            msg = self._no_rows_message()
            self._lbl_table_info.setText(msg)
            # With an empty grid this line is the whole answer, so when it has a
            # reason to give it is written in dark ink rather than the usual grey.
            self._lbl_table_info.setStyleSheet(
                "color:#8a3b00; font-weight:600;" if msg != "No rows."
                else "color:#777;")
            return

        pvs = self._table_pvs()
        cols = ["Timestamp"] + [self._col_label(p) for p in pvs]
        shown_rows = self._table_rows[-_MAX_TABLE_ROWS:]
        self._table_display_offset = len(self._table_rows) - len(shown_rows)
        self._table_widget.setUpdatesEnabled(False)
        # Only rebuild the grid when the columns really changed — dropping the
        # rows is what forces every item to be re-created, so a PV set that
        # stayed the same keeps its items (see _set_table_cell).
        if getattr(self, "_table_cols_cache", None) != cols:
            self._table_widget.setRowCount(0)
            self._table_widget.setColumnCount(len(cols))
            self._table_widget.setHorizontalHeaderLabels(cols)
            self._table_cols_cache = list(cols)
        self._table_widget.setRowCount(len(shown_rows))

        for row_i, (ts, row_dict) in enumerate(shown_rows):
            self._set_table_cell(row_i, 0, ns_to_local_str(ts), "")
            for col_j, pv in enumerate(pvs, 1):
                val_raw, severity = row_dict.get(pv, (None, ""))
                self._set_table_cell(row_i, col_j,
                                     self._format_value(val_raw), severity)

        self._table_widget.setUpdatesEnabled(True)
        self._lbl_table_info.setStyleSheet("color:#777;")   # back from a warning
        if len(shown_rows) < len(self._table_rows):
            self._lbl_table_info.setText(
                f"Showing last {len(shown_rows)} of {len(self._table_rows)} rows  "
                f"({len(pvs)} PVs)  |  Export CSV still uses all rows.")
        else:
            self._lbl_table_info.setText(
                f"{len(self._table_rows)} rows  ({len(pvs)} PVs)")

    def _format_value(self, val):
        if val is None: return ""
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val): return str(val)
            if abs(val) >= 1e6 or (abs(val) < 1e-3 and val != 0.0):
                return _fmt_cursor_value(val)
            return f"{val:.6g}"
        return str(val)

    def _on_table_scroll(self, _):
        pass

    def _on_table_context_menu(self, pos):
        menu = QMenu(self)
        act_copy = menu.addAction("Copy row")
        act_img  = menu.addAction("Open image")
        action   = menu.exec(self._table_widget.mapToGlobal(pos))
        if action == act_copy:
            rows = sorted(set(i.row() for i in self._table_widget.selectedIndexes()))
            lines = []
            for r in rows:
                row_txt = "\t".join(
                    (self._table_widget.item(r, c) or QTableWidgetItem()).text()
                    for c in range(self._table_widget.columnCount()))
                lines.append(row_txt)
            QApplication.clipboard().setText("\n".join(lines))
        elif action == act_img:
            self._try_open_image_at_row(
                self._table_widget.currentRow())

    def _on_table_double_click(self, row, col):
        self._try_open_image_at_row(row)

    def _try_open_image_at_row(self, row):
        row += getattr(self, "_table_display_offset", 0)
        if row < 0 or row >= len(self._table_rows): return
        ts, row_dict = self._table_rows[row]
        for pv, (val, _) in row_dict.items():
            if _looks_like_image_path(str(val)):
                path = _resolve_image_path(str(val))
                if path and os.path.exists(path):
                    try:
                        _open_path(path)
                    except Exception as exc:
                        self._log(f"Could not open image:\n{path}\n{exc}")
                        QMessageBox.warning(self, "Cannot open image", f"{path}\n\n{exc}")
                    return
        self._log("No image path found in this row.")

    # ── Axis settings table ─────────────────────────────────────────────────

    def _retry_axis_settings_tv(self):
        self._axis_tv_refresh_waiting = False
        try:
            self._refresh_axis_settings_tv()
        except RuntimeError:                 # window already closed
            pass

    def _ordered_for_plot(self, pvs):
        """`pvs` in the user's own order (rows dragged in the PV table): those in
        that order first, as dragged; any the user has not placed yet keep their
        current order and come after them, so a newly added PV lands at the end."""
        pvs = list(pvs)
        order = getattr(self, "_plot_order", None) or []
        if not order:
            return pvs
        present = set(pvs)
        placed = [pv for pv in order if pv in present]
        seen = set(placed)
        return placed + [pv for pv in pvs if pv not in seen]

    def _on_axis_rows_dropped(self, rows, before):
        """Rows of the PV table were dragged to sit above row `before`.

        Only rows of one section move together: an original PV cannot be
        dropped among the custom ones or the other way round — the divider rows
        stay where they are. The new order is remembered and the graph is
        redrawn in it (no reload: the data is already here)."""
        row_pv = list(getattr(self, "_axis_row_pv", []))
        custom_names = {d.get("name", "") for d in self._custom_pvs}

        def _section(pv):
            return pv in custom_names

        moved = [row_pv[r] for r in rows if 0 <= r < len(row_pv) and row_pv[r]]
        if not moved:
            return
        # The section the line sits in is decided by the row just ABOVE it:
        # a PV row → that PV's section; a divider → the section it heads (the
        # first divider heads the originals); nothing above → the first section.
        before = max(0, min(before, len(row_pv)))
        above = before - 1
        if above < 0:
            sect = False
        elif row_pv[above] is not None:
            sect = _section(row_pv[above])
        else:
            sect = above > 0           # row 0 = "Original PV", later = "Custom PV"
        moved = [pv for pv in moved if _section(pv) == sect]
        if not moved:
            self._lbl_status.setText(
                "Original and custom PVs stay in their own groups.")
            return
        # Position inside the section: count the section's unmoved PVs above the line.
        sect_rows = [pv for pv in row_pv if pv and _section(pv) == sect]
        keep = [pv for pv in sect_rows if pv not in moved]
        at = sum(1 for r in range(min(before, len(row_pv)))
                 if row_pv[r] and _section(row_pv[r]) == sect
                 and row_pv[r] not in moved)
        new_sect = keep[:at] + moved + keep[at:]
        full = self._ordered_for_plot(self._pv_order)
        others = [pv for pv in full if _section(pv) != sect]
        new_full = (others + new_sect) if sect else (new_sect + others)
        if new_full == full:
            return
        self._plot_order = new_full
        self.config["plot_order"] = list(new_full)
        self._refresh_axis_settings_tv()
        # Keep the dragged rows selected where they landed.
        try:
            tv = self._axis_tv
            tv.clearSelection()
            sm = tv.selectionModel()
            flags = sm.SelectionFlag.Select | sm.SelectionFlag.Rows
            for r, pv in enumerate(getattr(self, "_axis_row_pv", [])):
                if pv in moved:
                    sm.select(tv.model().index(r, 0), flags)
        except Exception:
            pass
        if self._samples_by_pv:
            self._schedule_replot()

    def _refresh_axis_settings_tv(self):
        # A cell being typed into (a Display Name, a limit) would lose the
        # typed text to a refresh — a replot or a live tick can ask for one at
        # any moment. Wait until the edit is finished.
        if self._axis_tv.state() == QAbstractItemView.State.EditingState:
            if not getattr(self, "_axis_tv_refresh_waiting", False):
                self._axis_tv_refresh_waiting = True
                QTimer.singleShot(200, self._retry_axis_settings_tv)
            return
        # Group rows under "Original PV" / "Custom PV" divider rows when custom
        # (derived) PVs exist, so derived channels are clearly separated from real
        # ones. self._axis_row_pv maps each table row → PV name (None for a
        # divider row) so the rest of the code never assumes row == _pv_order idx.
        custom_names = {d.get("name", "") for d in self._custom_pvs}
        # Rows follow the user's own order (dragged in this table), which is
        # also the order of the Y axes on the graph — see _ordered_for_plot.
        _ordered  = self._ordered_for_plot(self._pv_order)
        originals = [pv for pv in _ordered if pv not in custom_names]
        customs   = [pv for pv in _ordered if pv in custom_names]
        plan = []                       # ("header", label) | ("pv", pv)
        if customs:
            plan.append(("header", "Original PV"))
            plan += [("pv", pv) for pv in originals]
            plan.append(("header", "Custom PV"))
            plan += [("pv", pv) for pv in customs]
        else:
            plan += [("pv", pv) for pv in originals]
        self._axis_row_pv = [payload if kind == "pv" else None
                             for kind, payload in plan]

        _COL = list(self._axis_tv_cols)
        ncol = len(_COL)
        # Channels that have their own grid, in the order the plot gave them their
        # styles. Taken from the last redraw when there is one, so the tooltip
        # never names a style the channel did not actually get.
        _grid_on = getattr(self, "_grid_pvs_drawn", None)
        if not _grid_on:
            _grid_on = [pv for pv in self._pv_order
                        if self._pv_settings.get(pv, {}).get("show", True)
                        and self._pv_settings.get(pv, {}).get("grid", False)]
        tv = self._axis_tv
        # The cells are updated in place, not rebuilt: a new item for every
        # cell made the whole table blink (and jump to the top for a moment)
        # on every change of a PV or a custom PV. A cell gets a new item only
        # when it changes kind (divider <-> PV row); otherwise only what
        # differs is written, and nothing is painted until all of it is done.
        _KIND = Qt.ItemDataRole.UserRole + 41
        _DEF_FLAGS = QTableWidgetItem().flags()
        _ENABLED = Qt.ItemFlag.ItemIsEnabled
        _BAND_BG, _BAND_FG = QColor("#E3F2FD"), QColor("#0D47A1")

        def _cell(r, c, kind, text="", flags=_DEF_FLAGS, check=None,
                  color=None, tip=None):
            it = tv.item(r, c)
            if it is None or it.data(_KIND) != kind:
                it = QTableWidgetItem(text)
                it.setData(_KIND, kind)
                if kind in ("band", "band_pad"):
                    it.setBackground(_BAND_BG)
                if kind == "band":
                    fnt = it.font(); fnt.setBold(True); it.setFont(fnt)
                    it.setForeground(_BAND_FG)
                tv.setItem(r, c, it)
            elif it.text() != text:
                it.setText(text)
            if it.flags() != flags:
                it.setFlags(flags)
            # A fresh item has NO check state, which checkState() reports as
            # Unchecked — skipping it then leaves the cell without a tick box.
            if check is not None and (
                    it.data(Qt.ItemDataRole.CheckStateRole) is None
                    or it.checkState() != check):
                it.setCheckState(check)
            if color is not None and it.data(Qt.ItemDataRole.UserRole) != color:
                it.setData(Qt.ItemDataRole.UserRole, color)
            if tip is not None and it.toolTip() != tip:
                it.setToolTip(tip)

        # Writing cells fires itemChanged for every setText/setCheckState —
        # block it so the auto-apply handler doesn't run mid-refresh.
        tv.blockSignals(True)
        _vbar, _hbar = tv.verticalScrollBar(), tv.horizontalScrollBar()
        _vpos, _hpos = _vbar.value(), _hbar.value()
        tv.setUpdatesEnabled(False)
        try:
            tv.clearSpans()
            if tv.rowCount() != len(plan):
                tv.setRowCount(len(plan))
            for row_i, (kind, payload) in enumerate(plan):
                if kind == "header":
                    # not selectable/editable
                    _cell(row_i, 0, "band", f"  {payload}", _ENABLED)
                    # Columns can be dragged around, and a span then paints from
                    # wherever its first column ended up. Give every other cell of
                    # the divider the same blue so the row reads as one solid band
                    # no matter what order the user chose.
                    for col_j in range(1, ncol):
                        _cell(row_i, col_j, "band_pad", "", _ENABLED)
                    tv.setSpan(row_i, 0, 1, ncol)
                    continue
                pv  = payload
                idx = self._pv_order.index(pv)
                s = self._pv_settings.get(pv, self._get_pv_default_settings(pv, idx))
                stats = self._pv_stats(pv)
                for col_j, col_name in enumerate(_COL):
                    val = s.get(col_name, "")
                    if col_name == "blank":
                        _cell(row_i, col_j, "blank", "", _ENABLED)   # non-editable spacer
                    elif col_name in ("show", "auto_scale", "grid"):
                        tip = ""
                        if col_name == "grid":
                            # Name the line style this channel's grid gets, so the
                            # lines on the plot can be matched to their channel.
                            tip = (f"Grid for this signal — {self._grid_style_name(pv, _grid_on)} lines "
                                   f"in the signal's own colour"
                                   if val else
                                   "Tick to give this signal its own grid (each one "
                                   "gets a different kind of line)")
                        _cell(row_i, col_j, "check",
                              check=(Qt.CheckState.Checked if val
                                     else Qt.CheckState.Unchecked),
                              tip=tip)
                    elif col_name == "color":
                        color_hex = s.get("color", _GRAPH_COLORS[idx % len(_GRAPH_COLORS)])
                        _cell(row_i, col_j, "color", color=color_hex)
                    else:
                        if col_name in stats:            # measured, not typed
                            txt = stats[col_name]
                        else:
                            txt = "" if val is None else str(val)
                        fl = _DEF_FLAGS
                        if col_name in _AXIS_READONLY_COLS:
                            fl = _DEF_FLAGS & ~Qt.ItemFlag.ItemIsEditable
                        _cell(row_i, col_j, "text", txt, fl)
            # Adding, removing or editing a PV refreshes the rows; the view
            # stays where the user had scrolled it.
            _vbar.setValue(min(_vpos, _vbar.maximum()))
            _hbar.setValue(min(_hpos, _hbar.maximum()))
        finally:
            tv.blockSignals(False)
            tv.setUpdatesEnabled(True)
        self._autosize_axis_pane()
        _CustomPVDialog._restore_scroll_later(_vbar, _vpos)
        _CustomPVDialog._restore_scroll_later(_hbar, _hpos)

    # Never let the PV list eat the whole tab: the graph keeps at least this
    # much height, so the buttons above it always stay on screen.
    _AXIS_PANE_MIN_GRAPH = 240
    # How many PV rows the list shows when the tab opens. The graph is the reason
    # the tab exists, so the list starts at about a quarter of the height (3:1)
    # and is dragged taller when more rows are wanted.
    _AXIS_PANE_DEFAULT_ROWS = 5
    _AXIS_PANE_MIN_ROWS     = 4

    def _on_graph_splitter_moved(self, _pos=0, _index=0):
        """The user dragged the handle between the graph and the PV list.

        From here on the autosizer only maintains the maximum height; it must not
        move the handle back, or the drag would be undone by the next window
        resize or table refresh.
        """
        if getattr(self, "_axis_pane_sizing", False):
            return                           # our own setSizes, not a real drag
        self._axis_pane_user_sized = True

    def _autosize_axis_pane(self):
        """Make the PV list exactly as tall as the rows it holds.

        Fewer PVs -> a shorter table and a bigger graph; many PVs -> it grows
        only until the graph is down to its minimum, then the table scrolls.
        """
        pane = getattr(self, "_graph_axis_pane", None)
        tv   = getattr(self, "_axis_tv", None)
        spl  = getattr(self, "_graph_v_splitter", None)
        if pane is None or tv is None or spl is None or pane.isHidden():
            return
        if getattr(self, "_axis_pane_sizing", False):
            return                           # re-entry from our own relayout
        self._axis_pane_sizing = True
        try:
            self._autosize_axis_pane_now(pane, tv, spl)
        finally:
            self._axis_pane_sizing = False

    def _autosize_axis_pane_now(self, pane, tv, spl):

        need = tv.horizontalHeader().height() + 2 * tv.frameWidth() + 2
        for r in range(tv.rowCount()):
            need += tv.rowHeight(r)
        if tv.horizontalScrollBar().isVisible():
            need += tv.horizontalScrollBar().sizeHint().height()

        lay = pane.layout()
        m   = lay.contentsMargins()
        chrome = m.top() + m.bottom()

        total = spl.height()
        if total <= 1:                       # not laid out yet
            QTimer.singleShot(0, self._autosize_axis_pane)
            return
        # The graph comes first: it always keeps at least half of the height the
        # two panes share (and never less than the absolute floor), so the PV
        # list can only take the smaller half, however many PVs are in it.
        keep  = max(self._AXIS_PANE_MIN_GRAPH, int(total * 0.5))
        room  = total - spl.handleWidth() - keep
        floor = chrome + tv.horizontalHeader().height() + 26   # header + one row
        want  = max(floor, min(need + chrome, max(floor, room)))

        pane.setMaximumHeight(want)          # can be dragged smaller, never bigger
        if getattr(self, "_axis_pane_user_sized", False):
            return                           # the handle is the user's now

        # Opening height: a few rows, about a quarter of the pane. A real row
        # height is used where there is one, so the count is right at any font.
        row_h = tv.rowHeight(0) if tv.rowCount() else 26
        hdr   = chrome + tv.horizontalHeader().height() + 2 * tv.frameWidth() + 2
        start = min(want,
                    max(hdr + self._AXIS_PANE_MIN_ROWS * row_h,
                        min(hdr + self._AXIS_PANE_DEFAULT_ROWS * row_h,
                            int(total * 0.25))))
        spl.setSizes([max(1, total - spl.handleWidth() - start), start])

    def _on_axis_tv_double_click(self, row, col):
        col_name = list(self._axis_tv_cols)[col] if col < len(self._axis_tv_cols) else ""
        if col_name in _AXIS_READONLY_COLS: return

    def _on_axis_tv_clicked(self, index):
        # Toggle behaviour: clicking an already-selected row again clears the
        # highlight. Skip the checkbox/color columns so ticking Show/Auto/Grid or
        # picking a colour doesn't fight with (de)selecting the row.
        col_name = (list(self._axis_tv_cols)[index.column()]
                    if index.column() < len(self._axis_tv_cols) else "")
        if col_name in ("show", "auto_scale", "grid", "color"):
            self._axis_last_clicked_row = index.row()
            return
        row = index.row()
        selected = {i.row() for i in self._axis_tv.selectionModel().selectedRows()}
        if row == self._axis_last_clicked_row and row in selected:
            self._axis_tv.clearSelection()
            self._axis_last_clicked_row = -1
        else:
            self._axis_last_clicked_row = row

    def _on_axis_color_changed(self, row, color_hex):
        row_pv = getattr(self, "_axis_row_pv", [])
        pv = row_pv[row] if 0 <= row < len(row_pv) else None
        if not pv:
            return                       # divider/header row
        if pv not in self._pv_settings:
            idx = self._pv_order.index(pv) if pv in self._pv_order else 0
            self._pv_settings[pv] = self._get_pv_default_settings(pv, idx)
        self._pv_settings[pv]["color"]      = color_hex
        self._pv_settings[pv]["color_auto"] = False
        # Update the graph immediately. Graph artists are indexed by *plotted*
        # PVs (hidden ones are skipped), so map PV → line index by name rather
        # than reusing the table row (which now also contains divider rows).
        if pv in self._graph_pvs:
            gi = self._graph_pvs.index(pv)
            if gi < len(self._graph_lines) and self._graph_lines[gi]:
                for ln in self._graph_lines[gi]:
                    ln.set_color(color_hex)
                if gi < len(self._graph_bands) and self._graph_bands[gi] is not None:
                    self._graph_bands[gi].set_facecolor(color_hex)
                if gi < len(self._graph_axes):
                    ax = self._graph_axes[gi]
                    ax.yaxis.label.set_color(color_hex)
                    ax.tick_params(axis="y", labelcolor=color_hex)
                if self._mpl_canvas:
                    self._mpl_canvas.draw_idle()

    def _on_axis_item_changed(self, item):
        # Auto-apply: any edit in the axis table takes effect at once so the graph
        # never drifts out of sync with the table. Colour is handled live by the
        # swatch delegate (_on_axis_color_changed); cursor_val is a derived display
        # column written by the crosshair — skip both to avoid a redundant replot.
        row_pv = getattr(self, "_axis_row_pv", [])
        row = item.row()
        if row >= len(row_pv) or row_pv[row] is None:
            return                       # divider/header row
        col_name = (list(self._axis_tv_cols)[item.column()]
                    if item.column() < len(self._axis_tv_cols) else "")
        if col_name in ("color", "cursor_val"):
            return
        if col_name == "grid":
            # Grid on/off is only the grid lines: no rebuild, so the view, the
            # zoom and the toolbar history stay exactly as they are.
            self._pv_settings.setdefault(row_pv[row], {})["grid"] = (
                item.checkState() == Qt.CheckState.Checked)
            self._regrid_in_place()
            # The tick boxes' tooltips name each grid's line style. Only those
            # are rewritten: rebuilding the whole PV table here recomputed every
            # channel's statistics and re-sized the table, which re-sized and
            # redrew the graph — the "refresh" seen on every Grid tick.
            QTimer.singleShot(0, self._refresh_grid_tooltips)
            return
        # The look of one channel changes on the graph as it is — no rebuild,
        # so the change shows at once and the zoom and history stay put. Only
        # what changes the axis layout (Show) still rebuilds.
        if col_name in self._AXIS_INPLACE_COLS and \
                self._axis_cell_in_place(row_pv[row], col_name, item):
            return
        self._apply_axis_settings()

    def _refresh_grid_tooltips(self):
        """Rewrite only the Grid column's tooltips (see _refresh_axis_settings_tv)."""
        tv = getattr(self, "_axis_tv", None)
        cols = list(getattr(self, "_axis_tv_cols", []) or [])
        if tv is None or "grid" not in cols:
            return
        c = cols.index("grid")
        grid_on = getattr(self, "_grid_pvs_drawn", None) or []
        tv.blockSignals(True)
        try:
            for r, pv in enumerate(getattr(self, "_axis_row_pv", [])):
                it = tv.item(r, c)
                if not pv or it is None:
                    continue
                on = self._pv_settings.get(pv, {}).get("grid", False)
                tip = (f"Grid for this signal — {self._grid_style_name(pv, grid_on)} "
                       f"lines in the signal's own colour" if on else
                       "Tick to give this signal its own grid (each one gets a "
                       "different kind of line)")
                if it.toolTip() != tip:
                    it.setToolTip(tip)
        finally:
            tv.blockSignals(False)

    _AXIS_INPLACE_COLS = ("width", "style", "marker", "marker_size", "alpha",
                          "ymin", "ymax", "auto_scale", "smooth", "display_name")

    def _axis_cell_in_place(self, pv, col_name, item):
        """Store one edited PV-table cell and show it on the graph in place.
        False = the change needs the full rebuild after all."""
        s = self._pv_settings.setdefault(pv, {})
        if col_name == "auto_scale":
            s[col_name] = (item.checkState() == Qt.CheckState.Checked)
        elif col_name in ("ymin", "ymax", "width", "marker_size"):
            s[col_name] = self._safe_float(item.text(), s.get(col_name))
        elif col_name == "alpha":
            a = self._safe_float(item.text(), s.get("alpha", 100))
            s[col_name] = 100 if a is None else max(0.0, min(100.0, a))
        elif col_name == "style":
            txt = item.text().strip().lower()
            s[col_name] = txt if txt in _LINE_STYLES else "solid"
        elif col_name == "marker":
            txt = item.text().strip()
            s[col_name] = txt if txt in _MARKER_STYLES else "auto"
        elif col_name == "smooth":
            txt = item.text().strip()
            s[col_name] = int(txt) if txt.isdigit() else 1
        elif col_name == "display_name":
            s[col_name] = item.text().strip()
            self._remember_display_names()
            self._refresh_pv_time_choices()
            if self._table_widget.columnCount() > 1:
                self._populate_table()
            return self._rename_on_graph_in_place(pv)
        if self._mpl_canvas is None or pv not in self._graph_pvs:
            return True                  # not on the graph: nothing to redraw
        gi = self._graph_pvs.index(pv)
        if gi >= len(self._graph_lines) or gi >= len(self._graph_axes):
            return False
        if col_name in ("ymin", "ymax", "auto_scale"):
            ok = self._rescale_pv_in_place(gi, pv)
        elif col_name == "smooth":
            ok = self._resmooth_pv_in_place(gi, pv)
        else:
            ok = self._restyle_pv_in_place(gi, pv)
        if ok:
            self._blit_bg = None
            self._mpl_canvas.draw_idle()
        return ok

    def _restyle_pv_in_place(self, gi, pv):
        """Width / line style / points / point size / transparency."""
        s = self._pv_settings.get(pv, {})
        lines = self._graph_lines[gi]
        if not lines:
            return False
        n_pts = (self._graph_raw[gi][0].size if gi < len(self._graph_raw) else 0)
        kw = self._pv_style_kwargs(s, n_pts,
                                   bool(self._graph_opts.get("line_markers", True)))
        width = s.get("width", None)
        alpha = kw.get("alpha", 1.0)
        if len(lines) == 2:
            looks = ((lines[0], (width * 0.5) if width is not None else 0.8,
                      0.3 * alpha),
                     (lines[1], width if width is not None else 1.8, alpha))
        else:
            looks = ((lines[0], width if width is not None else 1.2, alpha),)
        for ln, lw, a in looks:
            ln.set_linewidth(lw)
            ln.set_linestyle(kw["linestyle"])
            ln.set_marker(kw["marker"])
            ln.set_markersize(kw["markersize"])
            ln.set_markerfacecolor(kw.get("markerfacecolor", ln.get_color()))
            ln.set_alpha(a)
        band = self._graph_bands[gi] if gi < len(self._graph_bands) else None
        if band is not None:
            band.set_alpha(max(0.08, 0.28 * float(alpha)))
        return True

    def _rescale_pv_in_place(self, gi, pv):
        """Y min / Y max / Autoscale: the same limits a rebuild would give."""
        s = self._pv_settings.get(pv, {})
        if gi >= len(self._graph_env) or gi >= len(self._graph_raw):
            return False
        lo_a, hi_a = self._graph_env[gi]
        values = self._graph_raw[gi][1]
        if values.size and not np.all(np.isnan(values)):
            dmin = float(np.nanmin(lo_a)); dmax = float(np.nanmax(hi_a))
        else:
            dmin, dmax = 0.0, 1.0
        autosc = s.get("auto_scale", False)
        if not autosc:
            if s.get("ymin") is not None: dmin = s["ymin"]
            if s.get("ymax") is not None: dmax = s["ymax"]
        lo, hi = self._band_ylim(gi, len(self._graph_pvs), dmin, dmax, autosc)
        self._graph_axes[gi].set_ylim(lo, hi)
        return True

    def _resmooth_pv_in_place(self, gi, pv):
        """A new averaging length on a trace that is already smoothed. Turning
        smoothing on or off adds or drops the faint raw line: that rebuilds."""
        s = self._pv_settings.get(pv, {})
        eff = s.get("smooth") or 1
        lines = self._graph_lines[gi]
        if len(lines) != 2 or eff <= 1 or gi >= len(self._graph_raw):
            return False
        times_num, values = self._graph_raw[gi]
        if values.size < eff:
            return False
        lines[1].set_data(times_num, _moving_avg_np(values, eff))
        disp = s.get("display_name") or shorten_pv_name(pv)
        lines[1].set_label(f"{disp} (avg {eff})")
        return True

    def _rename_on_graph_in_place(self, pv):
        """A new display name: the axis title and the trace label, nothing else."""
        if self._mpl_canvas is None or pv not in self._graph_pvs:
            return True
        gi = self._graph_pvs.index(pv)
        if gi >= len(self._graph_axes) or gi >= len(self._graph_lines):
            return False
        s = self._pv_settings.get(pv, {})
        disp = s.get("display_name") or shorten_pv_name(pv)
        ax = self._graph_axes[gi]
        if self._graph_opts.get("show_axis_titles", True):
            ax.yaxis.label.set_text(disp)
        lines = self._graph_lines[gi]
        if len(lines) == 2:
            lines[1].set_label(f"{disp} (avg {s.get('smooth') or 1})")
        elif lines:
            lines[0].set_label(disp)
        self._blit_bg = None
        self._mpl_canvas.draw_idle()
        return True

    @staticmethod
    def _safe_float(txt, fallback=None):
        """Parse a user-entered float; bad/blank input falls back instead of raising."""
        txt = (txt or "").strip().replace(",", ".")
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return fallback

    def _apply_axis_settings(self):
        _COL = list(self._axis_tv_cols)
        row_pv = getattr(self, "_axis_row_pv", [])
        cols_before = self._table_pvs()
        for row_i in range(self._axis_tv.rowCount()):
            pv = row_pv[row_i] if row_i < len(row_pv) else None
            if not pv:                       # divider/header row
                continue
            s  = self._pv_settings.setdefault(pv, {})
            for col_j, col_name in enumerate(_COL):
                if col_name == "blank": continue
                item = self._axis_tv.item(row_i, col_j)
                if not item: continue
                if col_name in ("show", "auto_scale", "grid"):
                    s[col_name] = (item.checkState() == Qt.CheckState.Checked)
                elif col_name == "color":
                    c = item.data(Qt.ItemDataRole.UserRole)
                    if c: s[col_name] = c
                elif col_name in _AXIS_READONLY_COLS:
                    pass                     # the PV name and the measured numbers
                elif col_name in ("ymin", "ymax", "width", "marker_size"):
                    # Bad numeric input keeps the previous value instead of crashing.
                    s[col_name] = self._safe_float(item.text(), s.get(col_name))
                elif col_name == "alpha":
                    a = self._safe_float(item.text(), s.get("alpha", 100))
                    s[col_name] = 100 if a is None else max(0.0, min(100.0, a))
                elif col_name == "style":
                    txt = item.text().strip().lower()
                    s[col_name] = txt if txt in _LINE_STYLES else "solid"
                elif col_name == "marker":
                    txt = item.text().strip()
                    s[col_name] = txt if txt in _MARKER_STYLES else "auto"
                elif col_name == "smooth":
                    txt = item.text().strip()
                    s[col_name] = int(txt) if txt.isdigit() else 1
                else:
                    s[col_name] = item.text().strip()
        # A Display Name typed here is the PV's name everywhere: the PV list,
        # the table heading, the next start.
        names_before = {pv: it.text() for pv, it in
                        ((self._item_pv(self._pv_list.item(i)), self._pv_list.item(i))
                         for i in range(self._pv_list.count()))}
        self._remember_display_names()
        if any(self._pv_list.item(i).text() != names_before.get(
                self._item_pv(self._pv_list.item(i)))
               for i in range(self._pv_list.count())):
            self._refresh_pv_time_choices()
            if self._table_widget.columnCount() > 1:
                self._populate_table()
        # Un-ticking Show takes the channel off the graph AND out of the table,
        # so the two never disagree about what is on screen.
        if self._table_pvs() != cols_before:
            self._populate_table()
        # The INFO box names only the custom PVs ticked Show — re-judge it.
        self._cpv_plan()
        self._emit_custom_pv_diag()
        # Show unticked: the PV is no longer read; ticked again: read it now.
        self._sync_fetched_to_show()
        self._schedule_replot()

    def _get_pv_default_settings(self, pv, idx):
        return {
            "show":         True,
            "pv":           pv,
            "display_name": (getattr(self, "_pv_display_names", {}).get(pv)
                             or shorten_pv_name(pv)),
            "color":        _GRAPH_COLORS[idx % len(_GRAPH_COLORS)],
            "color_auto":   True,   # False once the user hand-picks a color
            "cursor_val":   "",
            "side":         "left",
            "ymin":         None,
            "ymax":         None,
            "auto_scale":   False,
            "width":        None,
            "style":        "solid",
            "marker":       "auto",     # follow the global markers switch
            "marker_size":  3,
            "alpha":        100,        # percent
            "smooth":       1,
            # Each ticked signal draws its OWN grid now, so starting the first one
            # ticked would put a grid on the plot nobody asked for.
            "grid":         False,
        }

    @staticmethod
    def _pv_style_kwargs(pv_setting, n_points, global_markers):
        """Turn one signal's Style / Points / Size / Alpha into matplotlib
        keywords.

        A signal can never end up invisible: switching both the line and the
        points off brings the line back as solid.
        """
        style_name = str(pv_setting.get("style", "solid") or "solid").lower()
        if style_name not in _LINE_STYLES:
            style_name = "solid"
        marker_name = str(pv_setting.get("marker", "auto") or "auto")
        if marker_name not in _MARKER_STYLES:
            marker_name = "auto"

        if marker_name == "auto":
            # Unchanged behaviour: a small dot only while the global markers
            # switch is on and the trace is short enough to stay readable.
            marker = "." if (global_markers and n_points < 200) else "None"
        else:
            marker = _MARKER_STYLES[marker_name]

        if style_name == "none" and marker == "None":
            style_name = "solid"

        try:
            size = max(0.5, float(pv_setting.get("marker_size", 3)))
        except (TypeError, ValueError):
            size = 3.0
        try:
            alpha = max(0.0, min(100.0, float(pv_setting.get("alpha", 100)))) / 100.0
        except (TypeError, ValueError):
            alpha = 1.0

        kw = {"linestyle": _LINE_STYLES[style_name], "marker": marker,
              "markersize": size, "alpha": alpha}
        if marker_name in _MARKER_HOLLOW:
            kw["markerfacecolor"] = "none"
        return kw

    # Keys of _pv_settings that describe how a signal LOOKS. These are what a
    # style preset carries; cursor_val is a live readout and never saved.
    _PV_STYLE_KEYS = ("display_name", "color", "color_auto", "show",
                      "ymin", "ymax", "auto_scale", "width", "style",
                      "marker", "marker_size", "alpha", "smooth", "grid")

    def _pv_stats(self, pv):
        """Unit and the measured numbers of one signal over the loaded range.

        Cached on the sample count, so hovering or replotting never walks the
        samples again for a number that cannot have changed.
        """
        samples = self._samples_by_pv.get(pv) or []
        # The conditions decide which shots count, so they are part of the key.
        cond_sig = tuple((c.get("pv"), c.get("min"), c.get("max"),
                          c.get("outside", False),
                          len(self._samples_by_pv.get(c.get("pv")) or []))
                         for c in self._conditions)
        key = (pv, len(samples), cond_sig)
        hit = self._pv_stats_cache.get(key)
        if hit is not None:
            return hit
        unit = ""
        for _ts, _v, u in reversed(samples):
            if u:
                unit = str(u)
                break
        ts_all, vals = _samples_to_ns_arrays(samples)
        tables = self._condition_tables()
        if tables and ts_all.size:
            keep = self._condition_mask(ts_all, tables)
            ts_all, vals = ts_all[keep], vals[keep]
        fin = np.isfinite(vals)
        ts_all, vals = ts_all[fin], vals[fin]
        if vals.size:
            # Thinned samples are interval means: the real extremes are the
            # minimum/maximum the archiver sent with them — of the kept
            # samples only, the same bins the graph's band is drawn from.
            lo, hi = _envelope_for(ts_all.astype(np.float64), vals,
                                   self._minmax_by_pv.get(pv))
            v_lo, v_hi = float(np.nanmin(lo)), float(np.nanmax(hi))
            out = {"unit": unit,
                   "last":  _fmt_cursor_value(float(vals[-1])),
                   "min":   _fmt_cursor_value(v_lo),
                   "max":   _fmt_cursor_value(v_hi),
                   "mean":  _fmt_cursor_value(float(np.nanmean(vals))),
                   "count": str(vals.size)}
        else:
            out = {"unit": unit, "last": "", "min": "", "max": "", "mean": "",
                   "count": "0"}
        # One entry per signal: a new sample count replaces the old answer.
        for stale in [k for k in self._pv_stats_cache if k[0] == pv]:
            self._pv_stats_cache.pop(stale, None)
        self._pv_stats_cache[key] = out
        return out

    def _resync_pv_colors(self):
        """Re-assign palette colors by current display position so that two
        adjacent PVs never end up with the same color. A PV keeps its old
        color slot only if that color was picked by hand (color_auto is
        False); colors are otherwise recycled from ``_GRAPH_COLORS`` in the
        order the PVs currently appear, so this must run whenever
        ``self._pv_order`` changes (PVs added/removed/reordered)."""
        for i, pv in enumerate(self._pv_order):
            s = self._pv_settings.get(pv)
            if s is None:
                s = self._get_pv_default_settings(pv, i)
                self._pv_settings[pv] = s
            if s.get("color_auto", True):
                s["color"] = _GRAPH_COLORS[i % len(_GRAPH_COLORS)]

    # ── XY plot ─────────────────────────────────────────────────────────────

    def _refresh_xy_choices(self):
        pvs = self._pv_order
        custom_names = {d.get("name", "") for d in self._custom_pvs}
        old_map = dict(getattr(self, "_xy_choice_map", {}) or {})
        keep = [old_map.get(c.currentText())
                for c in (self._xy_x_combo, self._xy_y_combo)]
        choices = []
        self._xy_choice_map = {}
        for p in pvs:
            # The channel's own name — the one on the graph and in the PV list.
            label = p if p in custom_names else self._col_label(p)
            if label in self._xy_choice_map:      # two channels, one name
                label = p
            choices.append(label)
            self._xy_choice_map[label] = p
        label_of = {p: lb for lb, p in self._xy_choice_map.items()}
        # Repopulate without firing the auto-replot signal for every added item.
        for combo in (self._xy_x_combo, self._xy_y_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(choices)
            combo.blockSignals(False)
        # The channels picked before stay picked while they are still there;
        # only a pick that is gone falls back to the first two channels.
        # Both picks are set quietly and the cloud is drawn ONCE afterwards —
        # setting them one by one drew it twice in a row.
        for combo, pv, default in ((self._xy_x_combo, keep[0], 0),
                                   (self._xy_y_combo, keep[1], 1)):
            idx = combo.findText(label_of.get(pv, "\0")) if pv else -1
            if idx < 0 and len(choices) > default:
                idx = default
            if idx >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        if choices:
            self._on_xy_axis_changed()

    def _on_xy_axis_changed(self, _idx=0):
        # Auto-replot when the user picks a different X or Y channel.
        if self._table_rows:
            self._plot_xy()

    def _set_xy_info(self, text: str, warn: bool = False):
        """The line under the XY plot. A "nothing to draw" answer is the only
        thing on the tab at that moment, so it is written in dark ink instead of
        the usual grey — otherwise the tab looks like the button did nothing."""
        self._lbl_xy_info.setText(text)
        self._lbl_xy_info.setStyleSheet(
            "color:#8a3b00; font-weight:600;" if warn else "color:#777;")

    def _xy_empty_message(self, x_pv, y_pv) -> str:
        """Why the cloud has no points. With no rows at all the reason belongs
        to the table, and it is nearly always the Conditions; otherwise it is
        the two chosen channels themselves."""
        if not self._table_rows:
            return self._no_rows_message()
        sbp = self._samples_by_pv or {}
        if not sbp.get(x_pv) and not sbp.get(y_pv):
            return "No data to plot — neither channel has values in this window."
        if not sbp.get(x_pv) or not sbp.get(y_pv):
            return "No data to plot — one of the channels has no values in this window."
        return ("No data to plot — no shot in this window where both channels "
                "wrote a value (and that the Conditions keep).")

    def _xy_info(self, n: int, n_shots: int):
        """(text, warn) for the line under a drawn cloud. A window loaded
        thinned holds 1-minute archive averages, not shots, and a cloud of
        averages hides every single strong shot — said in warning ink."""
        text = (f"{n:,} points plotted — shots where both channels wrote "
                f"(of {n_shots:,} shots).")
        if self._sel_needs_raw():
            return (text + "  Window longer than 1 h: each point is a 1-minute "
                    "archive average, not a single shot. Load 1 h or less for "
                    "shot-by-shot values."), True
        return text, False

    def _plot_xy(self):
        try:
            self._plot_xy_impl()
        except Exception:
            import traceback
            self._log(f"[plot_xy]\n{traceback.format_exc()}")
            try:
                self._clear_xy_plot()
            except Exception:
                pass
            self._set_xy_info("XY plot error — see Log tab.", warn=True)

    def _plot_xy_impl(self):
        x_label = self._xy_x_combo.currentText()
        y_label = self._xy_y_combo.currentText()
        x_pv    = self._xy_choice_map.get(x_label)
        y_pv    = self._xy_choice_map.get(y_label)
        if not x_pv or not y_pv:
            self._set_xy_info("Select X and Y variables first.", warn=True); return

        xs, ys, n_shots = self._xy_pairs(x_pv, y_pv)

        if not xs:
            self._set_xy_info(self._xy_empty_message(x_pv, y_pv), warn=True)
            return

        self._clear_xy_plot()

        _dpi = 96
        _cw  = max(self._xy_canvas_container.width()  - 8, 800)
        _ch  = max(self._xy_canvas_container.height() - 8, 500)
        fig  = Figure(figsize=(_cw / _dpi, _ch / _dpi), dpi=_dpi)
        ax   = fig.add_subplot(111)

        # Dense clouds hide their own points, so the dots shrink as the count grows
        # and every one keeps a hairline dark edge — overlapping dots stay
        # countable instead of merging into one blob.
        _n = len(xs)
        _size = self._xy_dot_size(_n)
        sc   = ax.scatter(xs, ys, s=_size, alpha=0.85,
                          linewidths=0.3, edgecolors="#00000055",
                          c=range(_n), cmap=_XY_CMAP)
        ax.set_xlabel(x_label, fontsize=10)
        ax.set_ylabel(y_label, fontsize=10)
        ax.set_title(f"{x_label}  vs  {y_label}")
        fig.colorbar(sc, ax=ax, label="Sample order (dark = first, red = last)")
        ax.grid(True, alpha=0.3)

        canvas = FigureCanvasQTAgg(fig)
        layout = self._xy_canvas_container.layout()
        if layout: layout.addWidget(canvas)
        self._xy_canvas = canvas
        self._xy_figure = fig
        # What is on screen, so a Live refresh cannot silently swap in a
        # different pair of channels behind the user's back.
        self._xy_scatter  = sc
        self._xy_plotted  = (x_label, y_label, x_pv, y_pv)
        self._xy_user_zoomed = False
        self._xy_dirty = False
        self._install_xy_toolbar(canvas)
        canvas.draw()
        # Record the full view now that it exists, so Home and Back have
        # somewhere to return to from the very first zoom.
        self._xy_push_view()

        self._xy_rect_selector = RectangleSelector(
            ax, self._on_xy_rect_select, useblit=False, button=3,
            props=dict(alpha=0.2, facecolor="#FF6600"))

        self._set_xy_info(*self._xy_info(_n, n_shots))

    @staticmethod
    def _xy_dot_size(n):
        """Dot area for a cloud of n points.

        Dense clouds hide their own points, so the dots shrink as the count
        grows. Shared by the first plot and every live refresh, so a cloud that
        grows past a threshold while Live is running thins out the same way a
        freshly plotted one would.
        """
        return 24 if n < 300 else (12 if n < 2000 else (6 if n < 20000 else 3))

    @staticmethod
    def _xy_fit_axes(ax, xs, ys):
        """Frame the whole cloud with a little air around it."""
        for setter, vals in ((ax.set_xlim, xs), (ax.set_ylim, ys)):
            lo, hi = float(min(vals)), float(max(vals))
            pad = (hi - lo) * 0.05 or (abs(hi) * 0.05 or 1.0)
            setter(lo - pad, hi + pad)

    def _xy_refresh_live(self):
        """Keep the XY cloud in step with Live.

        Only the visible tab is redrawn; a hidden one is marked and caught up by
        _on_main_tab_changed. The figure, canvas, toolbar and colour bar are
        reused — rebuilding them on every tick would throw away the zoom history
        and the user's current view a few times a second.
        """
        if self._xy_canvas is None or self._xy_scatter is None:
            return
        if self._notebook.currentWidget() is not self._tab_xy:
            self._xy_dirty = True
            return
        try:
            self._xy_update_points()
        except Exception:
            import traceback
            self._log(f"[xy_live]\n{traceback.format_exc()}")
            self._xy_dirty = False

    def _xy_update_points(self):
        """Put the current rows into the existing scatter."""
        plotted = getattr(self, "_xy_plotted", None)
        if not plotted or self._xy_canvas is None or self._xy_scatter is None:
            self._xy_dirty = False       # nothing is drawn — nothing to catch up
            return
        x_label, y_label, x_pv, y_pv = plotted
        # The combos may have moved on since; then this is a different plot and
        # the full path has to run.
        if (self._xy_x_combo.currentText() != x_label
                or self._xy_y_combo.currentText() != y_label):
            self._plot_xy()
            return

        xs, ys, n_shots = self._xy_pairs(x_pv, y_pv)
        n  = len(xs)
        sc = self._xy_scatter
        ax = self._xy_figure.axes[0]
        if n == 0:
            sc.set_offsets(np.empty((0, 2)))
            self._set_xy_info(self._xy_empty_message(x_pv, y_pv), warn=True)
        else:
            sc.set_offsets(np.column_stack((np.asarray(xs, dtype=float),
                                            np.asarray(ys, dtype=float))))
            sc.set_array(np.arange(n))
            sc.set_clim(0, max(n - 1, 1))
            sc.set_sizes(np.full(n, self._xy_dot_size(n), dtype=float))
            self._set_xy_info(*self._xy_info(n, n_shots))
            # A zoom is the user's choice of what to look at; only an untouched
            # view follows the growing cloud. ax.relim() is no use here — it
            # ignores collections, and a scatter is a collection — so the limits
            # are taken from the points themselves, with the 5 % air matplotlib
            # would have left.
            if not getattr(self, "_xy_user_zoomed", False):
                self._xy_fit_axes(ax, xs, ys)
        self._xy_dirty = False
        self._xy_canvas.draw_idle()

    def _xy_pairs(self, x_pv, y_pv):
        """Pair two channels into (x, y) points: one per shot in which BOTH
        channels wrote a value. Returns ``(xs, ys, n_shots)``.

        Nothing is held forward. The old pairing made a point out of every
        table row — rows another channel had written — carrying X and Y forward
        for minutes, so a low SBW4 reading from one shot was paired with a
        full-power PTM1 reading from another: points that never happened.

        The shots are the window's own (cpva_core.find_shots); without a shot
        rate they are the two channels' writes merged within the table's
        sample-hold gap. A sample belongs to a shot when it lies within one
        merge gap of the shot's first and last write (an energy is often
        archived a few ms late); two in one shot → the later one. The points go
        through the same filters as the selection's shot count: Conditions,
        "Keep multiples of", dead shots.
        """
        sbp = self._samples_by_pv or {}
        tx, vx = _samples_to_ns_arrays(sbp.get(x_pv) or [])
        ty, vy = _samples_to_ns_arrays(sbp.get(y_pv) or [])
        if not tx.size or not ty.size:
            return [], [], 0
        gap = float(SAMPLE_HOLD_MIN_GAP_MS * 1_000_000)
        starts, ends, _gap, _members = self._window_shots()
        if not starts.size:
            starts, ends = self._xy_own_moments(tx, ty, gap)
        starts = np.asarray(starts, dtype=np.float64)
        ends = np.asarray(ends, dtype=np.float64)
        xv = self._xy_value_per_shot(tx, vx, starts, ends, gap)
        yv = self._xy_value_per_shot(ty, vy, starts, ends, gap)
        both = np.isfinite(xv) & np.isfinite(yv)
        if both.any():
            both[both] = self._stats_keep_mask(
                ends[both], self._condition_tables(), sbp, self._pre_window_vals)
        return xv[both].tolist(), yv[both].tolist(), int(starts.size)

    @staticmethod
    def _xy_own_moments(tx, ty, gap):
        """(starts, ends) of the moments X or Y wrote, writes closer together
        than ``gap`` merged — the table's rule, for a window with no shot rate."""
        t = np.unique(np.concatenate((tx, ty)))
        new = np.empty(t.size, dtype=bool)
        new[0] = True
        new[1:] = np.diff(t) > gap
        last = np.empty(t.size, dtype=bool)
        last[:-1] = new[1:]
        last[-1] = True
        return t[new], t[last]

    @staticmethod
    def _xy_value_per_shot(ts, vals, starts, ends, gap):
        """Each shot's value of one channel (NaN where it did not write): its
        last sample from one ``gap`` before the shot's first write to one
        ``gap`` after its last."""
        out = np.full(starts.size, np.nan)
        if not ts.size or not starts.size:
            return out
        k = np.searchsorted(starts, ts + gap, side="right") - 1
        ok = (k >= 0) & np.isfinite(vals)
        ok[ok] = ts[ok] <= ends[k[ok]] + gap
        kk, vv = k[ok], vals[ok]
        if kk.size:
            # Samples are time-ordered, so the last of each run is the later one.
            last = np.empty(kk.size, dtype=bool)
            last[:-1] = kk[1:] != kk[:-1]
            last[-1] = True
            out[kk[last]] = vv[last]
        return out

    def _install_xy_toolbar(self, canvas):
        """Same toolbar as the main graph, for the XY plot's own canvas."""
        holder = getattr(self, "_xy_tb_holder", None)
        if holder is None:
            return
        lay = holder.layout()
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.setParent(None); w.deleteLater()
        self._xy_toolbar = None
        try:
            tb = _make_mpl_toolbar(_MplToolbar, canvas, holder)
        except Exception:
            return
        self._xy_toolbar = tb
        lay.addWidget(tb)
        tb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Same two housekeeping jobs as the main graph's toolbar: drop the
        # "Customize" dialog, and let the app know when the view stopped being
        # its own to rescale (see _xy_update_points).
        for act in list(tb.actions()):
            if act.text() == "Customize":
                tb.removeAction(act)
        for act in tb.actions():
            if act.text() == "Home":
                act.triggered.connect(self._on_xy_view_home)
            elif act.text() in ("Zoom", "Pan"):
                act.toggled.connect(self._on_xy_toolbar_mode)

    def _on_xy_rect_select(self, eclick, erelease):
        if not self._xy_figure or not self._xy_figure.axes: return
        ax = self._xy_figure.axes[0]
        x0, x1 = sorted([eclick.xdata, erelease.xdata])
        y0, y1 = sorted([eclick.ydata, erelease.ydata])
        if x1 - x0 < 1e-12 or y1 - y0 < 1e-12: return
        # BOTH sides of the change go onto the toolbar's history, the way
        # matplotlib's own Zoom button does it: the view being left so Back can
        # return to it, and the view being entered so Forward can come back to
        # here. Pushing only the first left Back and Forward greyed out.
        self._xy_push_view()
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
        self._xy_push_view()
        self._xy_user_zoomed = True
        self._xy_canvas.draw_idle()

    def _clean_xy(self):
        # Clear only the plotted points — keep the axes frame visible. Falls back
        # to a full teardown if there is nothing drawn yet.
        if self._xy_canvas is not None and self._xy_figure and self._xy_figure.axes:
            ax = self._xy_figure.axes[0]
            for coll in list(ax.collections):
                try:
                    coll.remove()
                except Exception:
                    pass
            self._xy_rect_selector = None
            self._xy_canvas.draw_idle()
            self._set_xy_info("XY cleared — points removed.")
        else:
            self._clear_xy_plot()

    def _clear_xy_plot(self):
        if self._xy_canvas is not None:
            layout = self._xy_canvas_container.layout()
            if layout:
                layout.removeWidget(self._xy_canvas)
            self._xy_canvas.setParent(None)
            self._xy_canvas.deleteLater()
            self._xy_canvas = None
        # The toolbar belongs to that canvas — drop it too.
        if getattr(self, "_xy_toolbar", None) is not None:
            try:
                self._xy_toolbar.setParent(None)
                self._xy_toolbar.deleteLater()
            except Exception:
                pass
            self._xy_toolbar = None
        self._xy_figure = None; self._xy_rect_selector = None

    # ── PV Time Plot ────────────────────────────────────────────────────────

    def _plot_pv_time(self):
        try:
            self._plot_pv_time_impl()
        except Exception:
            import traceback
            self._log(f"[plot_pv_time]\n{traceback.format_exc()}")
            self._pv_time_finish()
            self._lbl_pv_time_info.setText("PV time plot error — see Log tab.")

    # -- what the tab is being asked for -----------------------------------

    def _pv_time_conditions(self):
        """The condition rows as the plot uses them: channel, on/off and the
        effective bounds (an open side is ±inf). A box that is not a number is
        shown red in the row and left out, as an empty box would be."""
        ed = self._pv_time_cond_editor
        apply_on = bool(getattr(self, "_pv_time_cond_apply", True))
        out = []
        for c in ed.conditions():
            label = ed.label_of(c["pv"])
            out.append({"label": label, "channel": c["pv"],
                        "enabled": c["enabled"] and apply_on, "kind": c["kind"],
                        "lo": -np.inf if c["min"] is None else c["min"],
                        "hi": np.inf if c["max"] is None else c["max"],
                        "min": c["min"], "max": c["max"],
                        "outside": c.get("outside", False),
                        "key": (c["pv"], c["kind"], c["a"], c["b"]),
                        "text": _cond_text(c, label)})
        return out

    def _set_pv_time_cond_apply(self, on):
        """The PV Time Plot's "Apply conditions" tick box."""
        self._pv_time_cond_apply = bool(on)
        self.config["pv_time_conditions_on"] = self._pv_time_cond_apply
        self._refresh_pv_time_cond_button()
        self._pv_time_request_plot()

    def _open_pv_time_conditions(self):
        """Show the PV Time Plot's Conditions window (not modal: the plot
        behind it redraws as the rows change)."""
        dlg = self._pv_time_cond_dialog
        self._pv_time_cond_editor.refresh_counts()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _close_pv_time_conditions(self):
        # A change still waiting out the editor's short collapse is applied.
        ed = self._pv_time_cond_editor
        if ed._timer.isActive():
            ed._timer.stop()
            ed.changed.emit()
        self._pv_time_cond_dialog.hide()

    def _refresh_pv_time_cond_button(self):
        """The button says how many conditions filter the plot right now."""
        btn = getattr(self, "_btn_pv_time_cond", None)
        ed = getattr(self, "_pv_time_cond_editor", None)
        if btn is None or ed is None:
            return
        n = sum(1 for c in ed.conditions() if c.get("enabled") and c.get("pv"))
        if n and not getattr(self, "_pv_time_cond_apply", True):
            btn.setText("Conditions (off)")
        elif n:
            btn.setText(f"Conditions ({n})")
        else:
            btn.setText("Conditions")

    def _copy_graph_conditions_to_pv_time(self):
        """Replace the PV Time Plot's condition rows with the Conditions
        window's rows, each with its on/off state."""
        rows = [dict(c) for c in self._conditions_view() if c.get("pv")]
        if not rows:
            self._lbl_pv_time_info.setText(
                "The Conditions window has no rows to copy.")
            return
        ed = self._pv_time_cond_editor
        ed.clear()
        for c in rows:
            ed.add_row(c, announce=False)
        ed._touched()

    def _pv_time_cond_count(self, cond):
        """``(kept, total)`` shots of the last drawing for one condition on its
        own; ``None`` when its channel has not been read yet."""
        counts = getattr(self, "_pv_time_cond_counts", None) or {}
        return counts.get((cond["pv"], cond["kind"], cond["a"], cond["b"]))

    def _pv_time_resolve(self, channels):
        """Split the channels the plot needs into real archiver PVs and the
        derived ones to be computed from them.

        Returns ``(real_pvs, plan, problems)``, where ``plan`` is
        ``[(name, code, [(letter, source channel)])]`` in the order it has to be
        evaluated — a derived channel may read an earlier derived one, exactly as
        in the Graph tab. It is built here, on the GUI thread, so the drawing
        never reads the custom-PV definitions while the user is editing them."""
        defs = {d.get("name", ""): d for d in (getattr(self, "_custom_pvs", None) or [])
                if d.get("name") and d.get("expr")}
        real, plan, problems, done = [], [], [], set()

        def _add(name, trail):
            if name in done:
                return
            if name not in defs:
                if name not in real:
                    real.append(name)
                done.add(name)
                return
            if name in trail:                      # a formula that reads itself
                problems.append(f"'{name}' is defined in terms of itself.")
                done.add(name)
                return
            d = defs[name]
            bindings = d.get("bindings") or {}
            srcs = []
            for letter in _cpv_vars(d["expr"]):
                src = bindings.get(letter)
                if not src:
                    problems.append(f"'{name}': {letter} is not bound to a PV.")
                    continue
                _add(src, trail | {name})
                srcs.append((letter, src))
            try:
                code = compile(d["expr"], "<cpv>", "eval")
            except Exception as exc:
                problems.append(f"'{name}': invalid formula — {exc}")
                code = None
            done.add(name)
            if code is not None:
                plan.append((name, code, srcs))

        for ch in channels:
            _add(ch, frozenset())
        return real, plan, problems

    def _plot_pv_time_impl(self):
        self._refresh_pv_time_choices()
        y_label = self._pv_time_y_combo.currentText()
        y_ch    = self._pv_time_choice_map.get(y_label)
        if not y_ch:
            self._lbl_pv_time_info.setText(
                "Add a channel to the PV list on the left first.")
            return
        if not self._pv_time_days:
            self._lbl_pv_time_info.setText("Pick at least one day.")
            return

        conds = self._pv_time_conditions()
        # Every condition channel is read, switched on or not: ticking a box or
        # retyping a target then redraws from what is already in memory instead
        # of going back to the archiver for the same days.
        wanted, seen, channels = [y_ch] + [c["channel"] for c in conds], set(), []
        for ch in wanted:
            if ch not in seen:
                seen.add(ch); channels.append(ch)

        real, plan, problems = self._pv_time_resolve(channels)
        for p in problems:
            self._log(f"PV Time Plot: {p}")
        if not real:
            self._lbl_pv_time_info.setText(
                "Nothing to read — see the Log tab for what is missing.")
            return

        # One point per measurement of the Y channel — that and nothing else is
        # what a distribution of Y is made of. The conditions are read AT those
        # moments (holding their last value), so a slow channel like the
        # waveplate filters the shots without adding points of its own: if it
        # did, a held-forward value would be counted as another shot and the
        # same number would appear in the distribution several times over.
        basis = self._pv_time_resolve([y_ch])[0]
        req = {"y_label": y_label, "y_ch": y_ch, "conds": conds,
               "real": real, "plan": plan, "basis": basis}

        missing = {}
        for day in self._pv_time_days:
            need = [pv for pv in real if (day, pv) not in self._pv_time_cache]
            if need:
                missing[day] = need
        if not missing:
            self._pv_time_finish()
            self._pv_time_draw(req)
            return
        self._pv_time_fetch(missing, req)

    # -- reading the days --------------------------------------------------

    def _pv_time_fetch(self, missing, req):
        """Read whole days of the channels that are not in memory yet.

        A day of eight channels is about fifty requests and a couple of seconds,
        so a week is read while you watch and a month is worth starting and
        leaving alone. Each finished day is handed over the moment it is done,
        so pressing Plot again mid-read keeps everything already fetched and only
        the unread days are asked for. Reading, merging into shots and the
        carry-forward all happen off the GUI thread.
        """
        self._pv_time_epoch += 1
        epoch = self._pv_time_epoch
        self._pv_time_busy = True
        self._btn_pv_time_plot.setEnabled(False)
        n_days = len(missing)
        self._lbl_pv_time_info.setText(f"Reading {n_days} day(s) from the archive…")
        self._pv_time_progress.setValue(0)
        self._pv_time_progress.show()

        self._pv_time_sig = _LoadSig()          # keep a reference until done
        sig = self._pv_time_sig
        sig.progress.connect(self._lbl_pv_time_info.setText)

        def _pct(p):
            if epoch != self._pv_time_epoch:
                return                          # a newer Plot owns the bar
            bar = self._pv_time_progress
            bar.setValue(max(bar.value(), min(100, int(p))))   # never backwards
        sig.pct.connect(_pct)
        sig.note.connect(self._log)
        sig.error.connect(self._on_pv_time_error)

        def _store(item):
            if epoch != self._pv_time_epoch:
                return                          # abandoned by a newer Plot
            day, per_pv = item
            for pv, data in per_pv.items():
                self._pv_time_cache[(day, pv)] = data
            self._pv_time_trim_cache()

        def _finished(_payload):
            if epoch != self._pv_time_epoch:
                return
            self._pv_time_finish()
            self._pv_time_draw(req)

        sig.partial.connect(_store)
        sig.done.connect(_finished)

        def _worker():
            try:
                from cpva_core import (cpva_fetch_many_adaptive,
                                       cpva_fetch_last_before_many)
                cancel = lambda: epoch != self._pv_time_epoch      # noqa: E731
                for i, (day, pvs) in enumerate(sorted(missing.items()), 1):
                    if cancel():
                        return
                    sig.progress.emit(f"Reading {day:%Y-%m-%d} — {len(pvs)} "
                                      f"channel(s), day {i} of {n_days}…")
                    d0     = datetime(day.year, day.month, day.day)
                    day_ns = dt_to_ns(d0)
                    # The bar moves with the part of each day already read,
                    # not only once per finished day.
                    last_pct = [-1]

                    def _cover(frac, _i=i):
                        p = int(((_i - 1) + 0.95 * frac) / n_days * 100)
                        if p != last_pct[0]:
                            last_pct[0] = p
                            sig.pct.emit(p)
                    res, errors, report = cpva_fetch_many_adaptive(
                        pvs, day_ns, dt_to_ns(d0 + timedelta(days=1)),
                        count=None, max_workers=16, cancel_fn=cancel, log_fn=None,
                        coverage_fn=_cover)
                    if cancel():
                        return
                    for pv, msg in (errors or {}).items():
                        sig.note.emit(f"PV Time Plot {day:%Y-%m-%d} "
                                      f"{shorten_pv_name(pv)}: {msg}")
                    # A channel that only changes now and then (the waveplate,
                    # a valve) can have its last sample hours before the day
                    # starts. Its value still holds, so the value from before
                    # the day is carried in rather than leaving the morning's
                    # shots blank and having a condition drop them all.
                    seeds = cpva_fetch_last_before_many(
                        [pv for pv in pvs if not res.get(pv)] +
                        [pv for pv in pvs
                         if res.get(pv) and int(res[pv][0].get("time", 0)) > day_ns],
                        day_ns, cancel_fn=cancel)
                    if cancel():
                        return
                    per_pv, counts = {}, []
                    for pv in pvs:
                        per_pv[pv] = self._pv_time_pv_arrays(res.get(pv, []),
                                                             seeds.get(pv))
                        counts.append(f"{shorten_pv_name(pv)} {len(per_pv[pv]['ts'])}")
                    sig.partial.emit((day, per_pv))
                    sig.pct.emit(int(i / n_days * 100))
                    sig.note.emit(f"PV Time Plot {day:%Y-%m-%d}: "
                                  f"{report.requests} requests · " + ", ".join(counts))
                sig.done.emit(None)
            except Exception:
                import traceback
                sig.error.emit(traceback.format_exc())

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _pv_time_pv_arrays(samples, seed_sample):
        """One channel's day as two plain arrays plus its carry-forward value.

        Kept per channel and per day, not per set of channels, so adding a
        condition on a ninth channel reads that channel alone instead of every
        day all over again."""
        ts, val = [], []
        for s in samples:
            t_ns = s.get("time")
            if t_ns is None:
                continue
            v = cpva_decode_value(s)
            ts.append(int(t_ns))
            val.append(float(v) if isinstance(v, (int, float))
                       and not isinstance(v, bool) else np.nan)
        seed = np.nan
        if seed_sample is not None:
            v = cpva_decode_value(seed_sample)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                seed = float(v)
        return {"ts": np.array(ts, dtype=np.int64),
                "val": np.array(val, dtype=np.float64),
                "seed": seed}

    def _pv_time_trim_cache(self, keep: int = 500):
        """Days already read stay in memory for the session, but not without
        limit: one channel-day is a few hundred kilobytes, so the oldest reads
        are dropped once the tab is holding a few hundred of them."""
        while len(self._pv_time_cache) > keep:
            self._pv_time_cache.pop(next(iter(self._pv_time_cache)))

    def _pv_time_finish(self):
        self._pv_time_busy = False
        self._btn_pv_time_plot.setEnabled(True)
        self._pv_time_progress.hide()

    def _on_pv_time_error(self, tb):
        self._log(f"[pv_time_fetch]\n{tb}")
        self._pv_time_finish()
        self._lbl_pv_time_info.setText("Reading the archive failed — see Log tab.")

    # -- one shot per row, from the cached channels -------------------------

    def _pv_time_day_columns(self, day, req):
        """One day of cached channels turned into shots: a time for every shot
        and one value per channel, plus the derived channels.

        The shots come from the times the Y channel itself reported, merged the
        way the Table merges them — samples less than
        :data:`SAMPLE_HOLD_MIN_GAP_MS` apart are one shot — and every other
        channel is then read at the end of each shot, holding its previous value
        where it did not report. So a condition channel filters the shots
        without inventing any."""
        real, plan = req["real"], req["plan"]
        parts  = [self._pv_time_cache.get((day, pv)) for pv in real]
        basis  = set(req["basis"]) or set(real)
        stamps = [p["ts"] for pv, p in zip(real, parts)
                  if pv in basis and p is not None and len(p["ts"])]
        if not stamps:
            return np.empty(0, dtype=np.int64), {}
        all_ts = np.sort(np.concatenate(stamps))
        gap    = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
        # A shot starts where the step from the previous sample is bigger than
        # the merge gap; the shot's time is its LAST sample, so every channel is
        # read at the moment the shot was complete.
        new    = np.empty(len(all_ts), dtype=bool)
        new[0] = True
        if len(all_ts) > 1:
            np.greater(np.diff(all_ts), gap, out=new[1:])
        ends = np.flatnonzero(np.append(new[1:], True))
        grid = all_ts[ends]

        cols = {}
        for pv, part in zip(real, parts):
            if part is None or not len(part["ts"]):
                cols[pv] = np.full(len(grid), part["seed"] if part else np.nan)
                continue
            # A channel outside the basis (the SBW4 energy of a condition) is
            # often archived a little after the shot's other channels. Reading
            # it exactly at the shot took the PREVIOUS shot's energy, so a
            # trigger shot passed on the real shot before it. It is read up to
            # one merge gap later — the rule the Graph's conditions use.
            look = 0 if pv in basis else gap
            idx = np.searchsorted(part["ts"], grid + look, side="right") - 1
            out = np.where(idx >= 0, part["val"][np.maximum(idx, 0)], part["seed"])
            cols[pv] = out
        # Derived channels are evaluated over the whole column at once. The
        # helpers are the array versions on purpose: round() and max() of two
        # plain numbers would fail on a column of ten thousand shots.
        SAFE = {"__builtins__": {}, "abs": np.abs, "min": np.minimum,
                "max": np.maximum, "round": np.round, "math": np}
        for name, code, srcs in plan:
            ns = {letter: cols.get(src) for letter, src in srcs}
            if any(v is None for v in ns.values()):
                cols[name] = np.full(len(grid), np.nan)
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                try:
                    res = eval(code, SAFE, ns)
                except Exception as exc:
                    self._log(f"PV Time Plot: '{name}' could not be computed "
                              f"for {day:%Y-%m-%d} — {exc}")
                    res = np.nan
            arr = np.asarray(res, dtype=np.float64)
            cols[name] = np.full(len(grid), float(arr)) if arr.ndim == 0 else arr
        return grid, cols

    def _pv_time_day_values(self, day, req):
        """The Y values of one day that pass every switched-on condition."""
        grid, cols = self._pv_time_day_columns(day, req)
        y = cols.get(req["y_ch"])
        tally = req.setdefault("tally", {})
        if y is None or not len(grid):
            return np.empty(0), np.empty(0, dtype=np.int64), 0
        base  = np.isfinite(y)
        keep  = base.copy()
        total = int(base.sum())
        for c in req["conds"]:
            v = cols.get(c["channel"])
            if v is None:
                continue
            ok = _cond_mask_passes(np.asarray(v, dtype=np.float64),
                                   c.get("min"), c.get("max"),
                                   c.get("outside", False))
            # How many shots this condition keeps on its own, for its row.
            k, t = tally.get(c["key"], (0, 0))
            tally[c["key"]] = (k + int((ok & base).sum()), t + total)
            if c["enabled"]:
                keep &= ok
        return y[keep], grid[keep], total

    # -- drawing ------------------------------------------------------------

    def _pv_time_draw(self, req):
        y_label, conds = req["y_label"], req["conds"]
        per_day, kept, total = [], 0, 0
        req["tally"] = {}
        for day in self._pv_time_days:
            vals, ts, n_all = self._pv_time_day_values(day, req)
            total += n_all
            kept  += len(vals)
            per_day.append((day, vals, ts))
        self._pv_time_cond_counts = dict(req["tally"])
        self._pv_time_cond_editor.refresh_counts()

        cond_txt = " · ".join(c["text"] for c in conds if c["enabled"]) or "none"

        if not kept:
            self._clear_pv_time_plot()
            self._lbl_pv_time_info.setText(
                f"Nothing left to draw: {total} shots read in "
                f"{len(self._pv_time_days)} day(s), none of them passed the "
                f"conditions ({cond_txt}).")
            return

        self._clear_pv_time_plot()
        _dpi = 96
        _cw  = max(self._pv_time_canvas_container.width()  - 8, 800)
        _ch  = max(self._pv_time_canvas_container.height() - 8, 500)
        fig  = Figure(figsize=(_cw / _dpi, _ch / _dpi), dpi=_dpi)
        ax   = fig.add_subplot(111)

        if self._pv_time_mode_combo.currentText() == "Raw shots":
            self._draw_pv_time_raw(ax, per_day, y_label)
            skipped = 0
        else:
            skipped = self._draw_daily_distribution(ax, per_day, y_label)

        ax.set_facecolor("white")
        fig.tight_layout()

        canvas = FigureCanvasQTAgg(fig)
        layout = self._pv_time_canvas_container.layout()
        if layout:
            layout.addWidget(canvas)
        self._pv_time_canvas = canvas
        self._pv_time_figure = fig
        self._install_pv_time_toolbar(canvas)
        canvas.draw()

        msg = (f"{kept} of {total} shots in {len(self._pv_time_days)} day(s) · "
               f"conditions: {cond_txt}")
        if skipped:
            msg += f" · {skipped} day(s) left out (fewer than two shots)"
        self._lbl_pv_time_info.setText(msg)

    def _draw_daily_distribution(self, ax, per_day, y_label):
        """One violin per day, median marked — the shape of the day rather than
        just its average: a day that ran at two different levels shows as two
        humps, which a mean and an error bar hide completely.

        Returns how many days were left out for having fewer than two shots (a
        single point has no distribution to draw)."""
        values, labels, skipped = [], [], 0
        for day, vals, _ts in per_day:
            if len(vals) < 2:
                skipped += 1
                continue
            values.append(vals)
            labels.append(day.strftime("%Y-%m-%d\n%a"))
        if not values:
            ax.text(0.5, 0.5, "No day has two shots to compare.",
                    ha="center", va="center", color="#555", transform=ax.transAxes)
            return skipped

        parts = ax.violinplot(values, showmedians=True, showextrema=True, widths=0.8)
        # Nothing is left to matplotlib's own colours: the bodies are the house
        # blue, the median a dark red line that stays visible inside them.
        for body in parts["bodies"]:
            body.set_facecolor("#1565C0")
            body.set_edgecolor("#0D3C6E")
            body.set_alpha(0.55)
            body.set_linewidth(0.8)
        for name, colour in (("cmedians", "#B71C1C"), ("cmins", "#0D3C6E"),
                             ("cmaxes", "#0D3C6E"), ("cbars", "#0D3C6E")):
            part = parts.get(name)
            if part is not None:
                part.set_color(colour)
                part.set_linewidth(1.4 if name == "cmedians" else 0.8)

        # Upright stamps, two lines (date over weekday). When there are more
        # days than room, only every k-th day is written; the violins all stay.
        fig = ax.figure
        ax_w_px = fig.get_figwidth() * fig.dpi * 0.85
        lbl_px  = 0.62 * 9 * fig.dpi / 72.0 * 10 + 12     # "2026-10-05" + a gap
        k = max(1, math.ceil(len(labels) * lbl_px / max(1.0, ax_w_px)))
        shown = [lb if i % k == 0 else "" for i, lb in enumerate(labels)]
        ax.set_xticks(range(1, len(labels) + 1))
        ax.set_xticklabels(shown, rotation=0, ha="center", fontsize=9)
        ax.set_xlim(0.4, len(labels) + 0.6)
        ax.set_ylabel(y_label, fontsize=10)
        ax.set_title(f"{y_label} — daily distribution")
        ax.grid(True, alpha=0.3, axis="y")
        ax.tick_params(colors="#222")
        return skipped

    def _draw_pv_time_raw(self, ax, per_day, y_label):
        """Every shot of the picked days on a real time axis. The days need not
        be next to each other, so each day is its own run of points and no line
        is dragged across the gap between two of them."""
        for day, vals, ts in per_day:
            if not len(vals):
                continue
            xs = [datetime.fromtimestamp(t / 1e9) for t in ts]
            ax.plot(xs, vals, "-o", ms=2.5, linewidth=0.7, color="#1565C0",
                    markeredgecolor="#0D3C6E", markeredgewidth=0.3)
        ax.set_ylabel(y_label, fontsize=10)
        ax.set_xlabel("Time")
        ax.set_title(f"{y_label} — every shot")
        # Upright two-line stamps (time over date), as many as fit side by side.
        fig = ax.figure
        ax_w_px = fig.get_figwidth() * fig.dpi * 0.85
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(
            minticks=3, maxticks=max(3, int(ax_w_px / 80))))
        from matplotlib.ticker import FuncFormatter

        def _stamp(x, _pos=None):
            d = mdates.num2date(x)
            # A stamp at midnight is a day boundary: the date alone says it.
            return d.strftime("%m-%d") if (d.hour, d.minute) == (0, 0) \
                else d.strftime("%H:%M\n%m-%d")
        ax.xaxis.set_major_formatter(FuncFormatter(_stamp))
        ax.grid(True, alpha=0.3)
        ax.tick_params(colors="#222")
        ax.tick_params(axis="x", labelrotation=0)
        for lbl in ax.get_xticklabels():
            lbl.set_horizontalalignment("center")

    def _install_pv_time_toolbar(self, canvas):
        """Same toolbar as the main graph, rebuilt with the canvas it drives."""
        holder = getattr(self, "_pv_time_tb_holder", None)
        if holder is None:
            return
        lay = holder.layout()
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.setParent(None); w.deleteLater()
        self._pv_time_toolbar = None
        try:
            tb = _make_mpl_toolbar(_MplToolbar, canvas, holder)
        except Exception:
            return
        self._pv_time_toolbar = tb
        lay.addWidget(tb)
        tb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _clear_pv_time_plot(self):
        if self._pv_time_canvas is not None:
            layout = self._pv_time_canvas_container.layout()
            if layout:
                layout.removeWidget(self._pv_time_canvas)
            self._pv_time_canvas.setParent(None)
            self._pv_time_canvas.deleteLater()
            self._pv_time_canvas = None
        if self._pv_time_toolbar is not None:
            self._pv_time_toolbar.setParent(None)
            self._pv_time_toolbar.deleteLater()
            self._pv_time_toolbar = None
        self._pv_time_figure = None

    # ── Repository ──────────────────────────────────────────────────────────

    def _load_data_repository(self):
        try:
            from cpva_core import load_ramping_repository
            self._ramping_repository = load_ramping_repository()
            self._log(f"Repository: {len(self._ramping_repository)} entries loaded.")
        except Exception as exc:
            self._log(f"Repository load failed: {exc}")

    # ── Presets ─────────────────────────────────────────────────────────────

    def _refresh_preset_combo(self):
        current = self._preset_combo.currentText()
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for p in self._presets:
            self._preset_combo.addItem(p.get("name", "?"))
        idx = self._preset_combo.findText(current)
        if idx >= 0: self._preset_combo.setCurrentIndex(idx)
        self._preset_combo.blockSignals(False)

    def _load_preset(self):
        name = self._preset_combo.currentText()
        if not name: return
        # Read the shared file again, so Load brings what another station (or
        # an older copy still running) saved after this one started.
        self._presets = _read_presets_file()
        self._refresh_preset_combo()
        idx = self._preset_combo.findText(name)
        if idx < 0 or idx >= len(self._presets): return
        p = self._presets[idx]
        self._pv_list.clear()
        for pv in p.get("pvs", []):
            self._pv_list.addItem(self._make_pv_item(pv))
        self._sync_pv_list_customs()   # keep custom rows pinned at the bottom
        self._update_pv_count()
        dt_from = parse_user_datetime(p.get("time_from", ""))
        dt_to   = parse_user_datetime(p.get("time_to",   ""))
        if dt_from: self._dt_from = dt_from
        if dt_to:   self._dt_to   = dt_to
        # A preset's saved period is a fixed stretch of the past, so it leaves
        # live mode for the same reason the calendar does. A preset that carries
        # no period only changes the channel list and does not touch live.
        if (dt_from or dt_to) and self._live_mode:
            self._stop_live("Live mode off — the preset brought a fixed period.")
        if dt_from or dt_to:
            self._live_stopped_by_zoom = False   # a new period: Home stays Home
        self._apply_ramp_settings(p)
        self._refresh_time_labels()
        self._log(f"Preset '{p['name']}' loaded.")
        # The new channel set and window are fetched on their own; the preset also
        # brings a different time range, so a selection from the old one may no
        # longer mean anything.
        self._user_zoomed = False
        self._drop_selection_if_outside()
        self._request_reload(delay_ms=0, reason="preset loaded")

    def _store_presets(self, name: str, preset: "dict | None"):
        """Write one preset change (preset=None deletes it) into the file.

        The file is shared by every station and each running copy holds its own
        list, so writing that whole list back would undo whatever another
        station saved since it started. Re-read the file, change only this one
        name, write, and take the merged list as the new combo content."""
        presets = _read_presets_file()
        pos = next((i for i, p in enumerate(presets)
                    if p.get("name") == name), -1)
        if preset is None:
            if pos >= 0:
                presets.pop(pos)
        elif pos >= 0:
            presets[pos] = preset
        else:
            presets.append(preset)
        save_presets([p for p in presets if not p.get("builtin")])
        self._presets = presets
        self._refresh_preset_combo()
        idx = self._preset_combo.findText(name)
        if preset is not None and idx >= 0:
            self._preset_combo.blockSignals(True)
            self._preset_combo.setCurrentIndex(idx)
            self._preset_combo.blockSignals(False)

    def _current_preset_payload(self, name: str) -> dict:
        """What Save writes: the PV list and period on screen, plus the master
        PV and multiple that Load applies — so a save never strips them."""
        p = {
            "name": name, "pvs": self._real_pv_names(),
            "time_from": self._dt_from.strftime("%Y-%m-%d %H:%M"),
            "time_to":   self._dt_to.strftime("%Y-%m-%d %H:%M"),
        }
        if self._master_pv_edit is not None:
            p.update(self._ramp_settings())
            p["master_pv"] = self._master_pv_edit.text().strip()
        return p

    def _save_preset(self):
        idx = self._preset_combo.currentIndex()
        if idx < 0 or self._presets[idx].get("builtin"):
            # "Blank" stays blank: saving onto it asks for a new name instead.
            self._save_preset_as(); return
        name = self._presets[idx].get("name", "?")
        # Keys this tab does not edit (e.g. conditions) ride along unchanged.
        preset = dict(self._presets[idx])
        preset.update(self._current_preset_payload(name))
        self._store_presets(name, preset)
        self._log(f"Preset '{name}' saved ({len(preset['pvs'])} PVs).")

    def _save_preset_as(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip(): return
        name = name.strip()
        if name == _BLANK_PRESET:
            QMessageBox.warning(self, "Save preset",
                                f"'{_BLANK_PRESET}' is the built-in empty preset. "
                                "Choose another name.")
            return
        if any(p.get("name") == name for p in _read_presets_file()):
            ans = QMessageBox.question(
                self, "Save preset",
                f"A preset named '{name}' already exists. Overwrite it?")
            if ans != QMessageBox.StandardButton.Yes: return
        preset = self._current_preset_payload(name)
        self._store_presets(name, preset)
        self._log(f"Preset '{name}' saved ({len(preset['pvs'])} PVs).")

    def _delete_preset(self):
        idx = self._preset_combo.currentIndex()
        if idx < 0 or idx >= len(self._presets): return
        name = self._presets[idx].get("name", "?")
        if self._presets[idx].get("builtin"):
            self._lbl_status.setText(f"'{name}' is built in and cannot be deleted.")
            return
        ans  = QMessageBox.question(
            self, "Delete preset", f"Delete preset '{name}'?")
        if ans != QMessageBox.StandardButton.Yes: return
        self._store_presets(name, None)
        self._log(f"Preset '{name}' deleted.")

    # ── PV list operations ───────────────────────────────────────────────────

    def _open_pv_browser(self):
        dlg = PVBrowserDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            added = False
            for pv in dlg.selected_pvs:
                # avoid duplicates
                if pv not in self._real_pv_names():
                    self._pv_list.addItem(self._make_pv_item(pv))
                    added = True
            self._sync_pv_list_customs()   # keep custom rows pinned at the bottom
            self._update_pv_count()
            if added:
                # A channel added mid-session has no history yet: the live tick only
                # fetches from the last-seen timestamp forward, so it would stay
                # blank. A full reload gets it over the whole window (all channels
                # load together in one pool, so this is quick).
                self._request_reload(reason="channel added")

    def _paste_pv_names(self):
        """Add the PV names on the clipboard (one or many — one per line, or
        split by commas, semicolons, tabs or spaces). Names already in the
        list are skipped; nothing is checked against the archiver, a wrong
        name simply loads empty and says so like any other."""
        text = QApplication.clipboard().text() or ""
        names = [t for t in re.split(r"[\s,;]+", text) if t]
        have = set(self._real_pv_names())
        new = []
        for pv in names:
            if pv not in have:
                have.add(pv)
                new.append(pv)
        if not new:
            self._lbl_status.setText("Nothing to paste — no new PV names on "
                                     "the clipboard.")
            return
        for pv in new:
            self._pv_list.addItem(self._make_pv_item(pv))
        self._sync_pv_list_customs()
        self._update_pv_count()
        self._lbl_status.setText(
            f"Pasted {len(new)} PV name{'s' if len(new) != 1 else ''}.")
        self._request_reload(reason="channel added")

    def _remove_selected_pvs(self):
        lst = self._pv_list
        sel = lst.selectedItems()
        first = min((lst.row(it) for it in sel), default=-1)
        removed = False
        for item in sorted(sel, key=lst.row, reverse=True):
            lst.takeItem(lst.row(item))
            removed = True
        # After a Remove the highlight moves to the PV that slid up into the
        # first freed place, so several PVs can be removed one click after
        # another. When the last PV went, the one above it is taken instead.
        # Custom (ƒ) rows sit at the bottom and cannot be selected, so they are
        # skipped the same way; with no PV left nothing is highlighted.
        new = first if removed else -1
        if new >= lst.count():
            new = lst.count() - 1
        while new >= 0 and lst.item(new).data(Qt.ItemDataRole.UserRole):
            new -= 1
        lst.clearSelection()
        lst.setCurrentRow(new)
        self._update_pv_count()
        if removed and not self._reshape_from_memory():
            # Several rows can go in one click, so the reload is debounced.
            self._request_reload(reason="channel removed")

    def _clear_pv_list(self):
        ans = QMessageBox.question(self, "Clear PVs", "Remove all PVs from list?")
        if ans == QMessageBox.StandardButton.Yes:
            self._pv_list.clear()
            self._sync_pv_list_customs()   # keep custom PVs on show after a clear
            self._update_pv_count()
            self._request_reload(reason="channel list cleared")

    # A real PV row's text is its NAME (what the graph calls it); the PV itself
    # rides along in this role. A row added as plain text (older code, tests)
    # has no role and its text is the PV.
    _PV_ROLE = Qt.ItemDataRole.UserRole + 1

    @classmethod
    def _item_pv(cls, item):
        return item.data(cls._PV_ROLE) or item.text()

    def _make_pv_item(self, pv):
        it = QListWidgetItem(self._col_label(pv))
        it.setData(self._PV_ROLE, pv)
        it.setToolTip(pv)
        return it

    def _refresh_pv_list_names(self):
        """Re-read every real row's name — after a rename in the list or in the
        PV table's Display Name column, so both always say the same."""
        for i in range(self._pv_list.count()):
            it = self._pv_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole):
                continue                    # ƒ row
            pv = self._item_pv(it)
            it.setData(self._PV_ROLE, pv)
            name = self._col_label(pv)
            if it.text() != name:
                it.setText(name)
            it.setToolTip(pv if name == pv else f"{name}\n{pv}")

    def _remember_display_names(self):
        """Keep the names the user chose (and only those) for the next start."""
        changed = False
        for pv in list(self._pv_order) + self._real_pv_names():
            s = self._pv_settings.get(pv)
            if s is None or "display_name" not in s:
                continue
            name = (s.get("display_name") or "").strip()
            auto = shorten_pv_name(pv)
            if not name:
                s["display_name"] = auto
                name = auto
            if name == auto:
                changed |= self._pv_display_names.pop(pv, None) is not None
            elif self._pv_display_names.get(pv) != name:
                self._pv_display_names[pv] = name
                changed = True
        if changed:
            self.config["pv_display_names"] = dict(self._pv_display_names)
        self._refresh_pv_list_names()

    def _set_pv_display_name(self, pv, name):
        name = (name or "").strip() or shorten_pv_name(pv)
        s = self._pv_settings.setdefault(
            pv, self._get_pv_default_settings(
                pv, self._pv_order.index(pv) if pv in self._pv_order else 0))
        s["display_name"] = name
        self._remember_display_names()
        if pv in self._pv_order:
            self._refresh_axis_settings_tv()
            if self._table_widget.columnCount() > 1:
                self._populate_table()
            self._refresh_xy_choices()
            self._refresh_pv_time_choices()
            if not self._rename_on_graph_in_place(pv):
                self._schedule_replot()

    def _rename_current_pv(self):
        it = self._pv_list.currentItem()
        if it is not None:
            self._on_pv_double_click(it)

    def _on_pv_double_click(self, item):
        """Double-click (or F2) renames the PV: the name shown here, on the
        graph, in the PV table and in the table export. The PV itself is
        changed from the right-click menu."""
        if item.data(Qt.ItemDataRole.UserRole):
            self._open_custom_pv_dialog()  # ƒ row → its own editor
            return
        pv = self._item_pv(item)
        name, ok = QInputDialog.getText(
            self, "Rename PV",
            f"{pv}\n\nName (empty = automatic, "
            f"“{shorten_pv_name(pv)}”):",
            text=self._col_label(pv))
        if ok:
            self._set_pv_display_name(pv, name)

    def _change_pv_of_item(self, item):
        """Point a row at a different archiver PV (the old double-click)."""
        old = self._item_pv(item)
        pv, ok = QInputDialog.getText(self, "Change PV", "PV name:", text=old)
        pv = pv.strip()
        if not ok or not pv or pv == old:
            return
        if pv in self._real_pv_names():
            QMessageBox.information(self, "Change PV", f"{pv} is already in the list.")
            return
        item.setData(self._PV_ROLE, pv)
        self._refresh_pv_list_names()
        self._request_reload(reason="channel renamed")

    def _selected_real_rows(self):
        lst = self._pv_list
        return sorted(lst.row(it) for it in lst.selectedItems()
                      if not it.data(Qt.ItemDataRole.UserRole))

    def _move_selected_pvs(self, delta):
        """Move the selected PVs one place up (−1) or down (+1). Their order is
        the order of the graph's bands and of the table's columns."""
        lst = self._pv_list
        rows = self._selected_real_rows()
        n_real = len(self._real_pv_names())
        if not rows or (delta < 0 and rows[0] == 0) or \
                (delta > 0 and rows[-1] >= n_real - 1):
            return
        bar = lst.verticalScrollBar(); pos = bar.value()
        order = rows if delta < 0 else list(reversed(rows))
        moved = []
        for r in order:
            it = lst.takeItem(r)
            lst.insertItem(r + delta, it)
            moved.append(it)
        lst.clearSelection()
        for it in moved:
            it.setSelected(True)
        lst.setCurrentItem(moved[-1])
        bar.setValue(pos)
        lst.scrollToItem(moved[-1])
        self._apply_pv_list_order()

    def _apply_pv_list_order(self):
        """The PV list was reordered (Move up/down or a drag): the graph and
        the table follow it. The order once dragged in the PV table is
        replaced for the real PVs — otherwise it kept winning and moving a PV
        in the list no longer moved its band."""
        real = self._real_pv_names()
        if getattr(self, "_plot_order", None):
            customs = {d.get("name", "") for d in self._custom_pvs}
            rest = [pv for pv in self._ordered_for_plot(self._pv_order)
                    if pv in customs]
            self._plot_order = real + rest
            self.config["plot_order"] = list(self._plot_order)
        if not self._reshape_from_memory():
            self._request_reload(reason="channel order changed")

    def _on_pv_list_dropped(self, *_):
        """A drag inside the PV list finished. Custom (ƒ) rows always stay
        under the real PVs, so they are put back at the end first."""
        if getattr(self, "_pv_list_drop_busy", False):
            return
        self._pv_list_drop_busy = True
        try:
            self._sync_pv_list_customs()
        finally:
            self._pv_list_drop_busy = False
        new = self._real_pv_names()
        if new != list(getattr(self, "_base_pv_order", None) or new):
            self._apply_pv_list_order()

    def _reshape_from_memory(self):
        """Channels were only reordered or removed: rebuild the table and the
        graph from the data already loaded instead of reading the archive
        again. Live and a running load still take the reload path.
        False = not possible here, the caller reloads."""
        if (getattr(self, "_load_in_flight", False) or self._live_mode
                or not self._samples_by_pv):
            return False
        base_old = list(getattr(self, "_base_pv_order", None) or [])
        new = self._real_pv_names()
        if not new or not set(new) <= set(base_old):
            return False                 # a channel was added: it must be read
        try:
            for pv in set(base_old) - set(new):
                self._samples_by_pv.pop(pv, None)
                self._minmax_by_pv.pop(pv, None)
                self._pre_window_vals.pop(pv, None)
                self._numeric_pvs.discard(pv)
            self._base_pv_order = list(new)
            self._pv_order = list(new)
            # The same steps a finished load runs (see __on_load_finished_inner).
            rows = self._build_table_rows(
                {pv: self._samples_by_pv.get(pv, []) for pv in new}, new,
                self._pre_window_vals)
            rows = self._remove_master_only_rows(rows)
            report = getattr(self, "_last_fetch_report", None)
            rows = self._remove_fake_hour_boundary_rows(
                rows, boundaries=(report.boundaries if report is not None else None))
            self._table_rows_unfiltered = rows
            self._rebuild_custom_pvs()
            rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
            self._table_rows = self._apply_conditions_to_rows(rows)
            self._resync_pv_colors()
            self._plot_graph()
            self._populate_table()
            self._refresh_xy_choices()
        except Exception:
            import traceback
            self._log(f"[reshape]\n{traceback.format_exc()}")
            return False
        return True

    # ── Hidden PVs are not read ─────────────────────────────────────────────
    # A PV with Show unticked in the PV table is left out of every read (see
    # _fetch_pv_names). _fetched_pvs is what the loaded window really holds;
    # these keep it in step when Show, the Conditions or the custom PVs change.

    def _remerge_rows(self):
        """Rebuild the table rows from the samples in memory — the same steps a
        finished load runs. Live leaves it to its own next table refresh."""
        if self._live_mode:
            self._live_rows_stale = True
            return
        order = list(self._base_pv_order or self._real_pv_names())
        rows = self._build_table_rows(
            {pv: self._samples_by_pv.get(pv, []) for pv in order}, order,
            self._pre_window_vals)
        rows = self._remove_master_only_rows(rows)
        report = getattr(self, "_last_fetch_report", None)
        rows = self._remove_fake_hour_boundary_rows(
            rows, boundaries=(report.boundaries if report is not None else None))
        self._table_rows_unfiltered = rows
        self._rebuild_custom_pvs()
        rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
        self._table_rows = self._apply_conditions_to_rows(rows)
        self._populate_table()

    def _sync_fetched_to_show(self):
        """Show changed in the PV table: a PV no longer needed lets its samples
        go (not merged, not counted); one needed again is read."""
        fetched = getattr(self, "_fetched_pvs", None)
        if fetched is None or not self._samples_by_pv:
            return              # nothing loaded: the next load reads the right ones
        want = set(self._fetch_pv_names())
        drop = [pv for pv in fetched if pv not in want]
        if drop:
            for pv in drop:
                self._samples_by_pv[pv] = []
                self._minmax_by_pv.pop(pv, None)
                self._pre_window_vals.pop(pv, None)
                self._numeric_pvs.discard(pv)
                fetched.discard(pv)
            self._pv_stats_cache.clear()
            self._log("Not read any more (Show unticked): "
                      + ", ".join(shorten_pv_name(p) for p in drop))
            self._remerge_rows()
        self._catch_up_shown_pvs()

    def _catch_up_token(self):
        return (self._load_epoch, self._live_epoch, bool(self._live_mode))

    def _catch_up_shown_pvs(self):
        """Read the PVs that are wanted but not in the loaded window — Show
        ticked again, or needed by a new condition or custom PV — over the
        window on screen, in the background, and draw them in."""
        fetched = getattr(self, "_fetched_pvs", None)
        if fetched is None or self._load_in_flight:
            return              # a running load calls this again when it ends
        missing = [pv for pv in self._fetch_pv_names() if pv not in fetched]
        if not missing:
            return
        if getattr(self, "_catchup_busy", False):
            self._catchup_again = True
            return
        if self._live_mode and self._live_window_span:
            end_ns = now_ns()
            start_ns = end_ns - int(self._live_window_span.total_seconds() * 1e9)
        else:
            start_ns, end_ns = dt_to_ns(self._dt_from), dt_to_ns(self._dt_to)
        avg_target = self._avg_target_points()
        timeout = self._http_timeout()
        day_cache = _day_cache()
        token = self._catch_up_token()
        cancel = lambda: self._catch_up_token() != token        # noqa: E731
        self._catchup_busy = True
        self._catchup_again = False
        self._lbl_status.setText(
            "Reading " + ", ".join(shorten_pv_name(p) for p in missing) + "…")
        sig = _LoadSig()
        self._catchup_sig = sig                 # keep it alive until it fires
        sig.done.connect(lambda res, t=token: self._after_catch_up(t, res))

        def _worker():
            out, mm, pre, errs = {}, {}, {}, {}
            try:
                from cpva_core import (cpva_fetch_many_adaptive, cpva_decode_value,
                                       cpva_fetch_last_before_many,
                                       LAST_BEFORE_DEADLINE_S)
                raw_by_pv, errs, _rep = cpva_fetch_many_adaptive(
                    missing, start_ns, end_ns,
                    count=(avg_target if avg_target > 0 else None),
                    timeout=timeout, cancel_fn=cancel, cache=day_cache)
                for pv in missing:
                    raw = raw_by_pv.get(pv, [])
                    rows = [(int(s["time"]), cpva_decode_value(s),
                             (s.get("metaData") or {}).get("units", "") or "")
                            for s in raw if s.get("time") is not None]
                    rows.sort(key=lambda r: r[0])
                    out[pv] = rows
                    mm[pv] = _minmax_arrays(raw)
                need = [pv for pv in missing
                        if not out[pv] or out[pv][0][0] > start_ns]
                if need and not cancel():
                    seeds = cpva_fetch_last_before_many(
                        need, start_ns, timeout, cancel_fn=cancel,
                        deadline_s=LAST_BEFORE_DEADLINE_S)
                    for pv, s in seeds.items():
                        if s and s.get("time"):
                            pre[pv] = (int(s["time"]), cpva_decode_value(s),
                                       (s.get("metaData") or {}).get("units", "")
                                       or "")
            except Exception as exc:
                errs = dict(errs or {}, **{"*": str(exc)})
            sig.done.emit((missing, out, mm, pre, start_ns, end_ns, errs))

        threading.Thread(target=_worker, daemon=True).start()

    def _after_catch_up(self, token, res):
        self._catchup_busy = False
        missing, out, mm, pre, start_ns, end_ns, errs = res
        for pv, e in (errs or {}).items():
            self._log(f"Could not read {shorten_pv_name(pv)}: {e}")
        if token != self._catch_up_token() or getattr(self, "_fetched_pvs", None) is None:
            # A newer load or a Live start/stop replaced the window meanwhile.
            QTimer.singleShot(0, self._catch_up_shown_pvs)
            return
        want = set(self._fetch_pv_names())
        got_any = False
        for pv in missing:
            if pv not in want or pv not in out:
                continue                   # hidden again while it was read
            rows = out[pv]
            if pv not in pre:
                before = [r for r in rows if r[0] < start_ns and r[1] is not None]
                if before:
                    pre[pv] = before[-1]
            rows = [r for r in rows if start_ns <= r[0] <= end_ns]
            if self._live_mode:
                # Live may already have polled this PV's newest samples.
                last = rows[-1][0] if rows else start_ns
                rows += [r for r in (self._samples_by_pv.get(pv) or [])
                         if r[0] > last]
            self._samples_by_pv[pv] = rows
            self._minmax_by_pv[pv] = mm.get(pv)
            if pv in pre:
                self._pre_window_vals[pv] = pre[pv]
            if any(isinstance(v, (int, float)) for _t, v, _u in rows):
                self._numeric_pvs.add(pv)
            self._fetched_pvs.add(pv)
            got_any = True
        if got_any:
            self._pv_stats_cache.clear()
            self._remerge_rows()
            self._plot_graph()
            self._lbl_status.setText(
                "Read " + ", ".join(shorten_pv_name(p) for p in missing))
        if getattr(self, "_catchup_again", False):
            self._catchup_again = False
            QTimer.singleShot(0, self._catch_up_shown_pvs)

    def _copy_selected_pv_names(self):
        rows = self._selected_real_rows()
        if not rows and self._pv_list.currentItem() is not None:
            rows = [self._pv_list.currentRow()]
        pvs = [self._item_pv(self._pv_list.item(r)) for r in rows
               if not self._pv_list.item(r).data(Qt.ItemDataRole.UserRole)]
        if pvs:
            QApplication.clipboard().setText("\n".join(pvs))
            self._lbl_status.setText(
                f"Copied {len(pvs)} PV name{'s' if len(pvs) != 1 else ''}.")

    def _open_pv_list_menu(self, pos):
        lst = self._pv_list
        item = lst.itemAt(pos)
        menu = QMenu(self)

        def _later(fn):
            # Anything that opens a dialog runs once the menu has closed and
            # let go of the mouse (a modal under an open QMenu opens late).
            return lambda: QTimer.singleShot(0, fn)

        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            menu.addAction("Edit custom PVs", _later(self._open_custom_pv_dialog))
        elif item is not None:
            if not item.isSelected():
                lst.clearSelection(); item.setSelected(True)
                lst.setCurrentItem(item)
            many = len(self._selected_real_rows()) > 1
            a = menu.addAction("Rename\tF2",
                               _later(lambda: self._on_pv_double_click(item)))
            a.setEnabled(not many)
            a = menu.addAction("Change PV",
                               _later(lambda: self._change_pv_of_item(item)))
            a.setEnabled(not many)
            menu.addAction("Copy PV name" + ("s" if many else "") + "\tCtrl+C",
                           self._copy_selected_pv_names)
            menu.addAction("Paste PV names\tCtrl+V", self._paste_pv_names)
            menu.addSeparator()
            menu.addAction("Move up\tCtrl+Up", lambda: self._move_selected_pvs(-1))
            menu.addAction("Move down\tCtrl+Down", lambda: self._move_selected_pvs(1))
            menu.addSeparator()
            menu.addAction("Remove\tDel", self._remove_selected_pvs)
        else:
            menu.addAction("+ Add PV", _later(self._open_pv_browser))
            menu.addAction("Paste PV names\tCtrl+V", self._paste_pv_names)
            menu.addAction("Custom PV", _later(self._open_custom_pv_dialog))
        menu.exec(lst.viewport().mapToGlobal(pos))

    def _real_pv_names(self):
        """The real archiver PVs queued for fetching — the only ones sent to the
        archiver, saved into presets, or counted. Custom (derived) PVs are shown
        in the list too but tagged (UserRole set), so they are skipped here."""
        return [self._item_pv(self._pv_list.item(i))
                for i in range(self._pv_list.count())
                if not self._pv_list.item(i).data(Qt.ItemDataRole.UserRole)]

    def _fetch_pv_names(self, pvs=None):
        """The real PVs actually read from the archiver: those with Show
        ticked, plus a hidden one the program still needs — a Conditions
        channel, the source of a shown custom (ƒ) PV, the Waveplate (master)
        PV. A PV nobody watches costs nothing: not read, not merged, not
        counted. Order follows ``pvs`` (default: the PV list)."""
        pvs = self._real_pv_names() if pvs is None else list(pvs)
        shown = lambda p: self._pv_settings.get(p, {}).get("show", True)  # noqa: E731
        needed = {c.get("pv") for c in (self._conditions or []) if c.get("pv")}
        try:
            master = self._get_master_pv()
        except Exception:
            master = None
        if master:
            needed.add(master)
        # Sources of every shown custom PV, through custom PVs built on others.
        defs = {d.get("name", ""): d for d in self._custom_pvs if d.get("name")}
        todo = [n for n in defs if shown(n) or n in needed]
        seen = set()
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            for src in (defs[name].get("bindings") or {}).values():
                if src in defs:
                    todo.append(src)
                elif src:
                    needed.add(src)
        return [pv for pv in pvs if shown(pv) or pv in needed]

    def _sync_pv_list_customs(self):
        """Mirror the defined custom PVs into the PV LIST (above the Live button) as
        read-only, visually distinct rows so they sit alongside the real PVs.
        Source of truth stays ``self._custom_pvs``; these rows are tagged and
        excluded from every fetch/save path via :meth:`_real_pv_names`."""
        _bar = self._pv_list.verticalScrollBar()
        _CustomPVDialog._restore_scroll_later(_bar, _bar.value())
        # Remove any previously-injected divider/custom rows (idempotent).
        for i in range(self._pv_list.count() - 1, -1, -1):
            if self._pv_list.item(i).data(Qt.ItemDataRole.UserRole):
                self._pv_list.takeItem(i)
        channels = self._cpv_dialog_channels()
        for d in getattr(self, "_custom_pvs", []) or []:
            name = d.get("name", "")
            if not name:
                continue
            it = QListWidgetItem(f"ƒ  {name}")           # ƒ marks a derived PV
            it.setData(Qt.ItemDataRole.UserRole, "custom")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)            # visible, not selectable/editable
            it.setForeground(QColor("#5C6BC0"))
            fnt = it.font(); fnt.setItalic(True); it.setFont(fnt)
            it.setBackground(QColor("#EEF1FB"))
            it.setToolTip(self._custom_pv_tooltip(d, channels))
            self._pv_list.addItem(it)

    @staticmethod
    def _custom_pv_tooltip(entry: dict, channels: list) -> str:
        """``name = expr`` in the letters valid right now, followed by the PV
        behind every letter — so the formula stays readable after the PV list has
        been reordered or a preset swapped. ``channels`` comes from
        :meth:`_cpv_dialog_channels`."""
        name = entry.get("name", "")
        if not entry.get("expr"):
            return name
        pv_by_letter = {lt: pv for lt, pv, _d, _ld in channels}
        letter_by_pv = {pv: lt for lt, pv, _d, _ld in channels}
        loaded_by_pv = {pv: ld for _lt, pv, _d, ld in channels}
        expr, _unbound = _cpv_to_display(entry, letter_by_pv)
        lines = [f"{name} = {expr}"]
        for letter in _cpv_vars(expr):
            pv = pv_by_letter.get(letter)
            if not pv:
                lines.append(f"    {letter} = ?  (no such channel)")
            elif not loaded_by_pv.get(pv, True):
                lines.append(f"    {letter} = {pv}   (not loaded)")
            else:
                lines.append(f"    {letter} = {pv}")
        return "\n".join(lines)

    def _update_pv_count(self):
        n = len(self._real_pv_names())
        self._lbl_pv_count.setText(f"{n} PV{'s' if n != 1 else ''}")

    # ── Time window dialog ───────────────────────────────────────────────────

    def _open_time_window_dialog(self):
        dlg = TimeWindowDialog(self, self._dt_from, self._dt_to)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._dt_from, self._dt_to = dlg._result_from, dlg._result_to
            self._refresh_time_labels()
            self._drop_selection_if_outside()
            # Live follows the clock, so it only makes sense for a period that
            # ends at "now". Choosing a period that ends at a fixed moment means
            # "show me that stretch of the past", and Live used to quietly
            # reinterpret it as a WIDTH — asking for yesterday 08:00-12:00 gave
            # the last four hours instead. The Live button is still the manual
            # override in both directions.
            if self._live_mode and not dlg.ends_at_now():
                self._stop_live("Live mode off — a fixed period was chosen.")
            self._live_stopped_by_zoom = False   # a new period: Home stays Home
            self._user_zoomed = False
            # The dialog's own "Live" button asks for Live mode as well, not
            # just for a period ending at "now".
            if dlg.wants_live() and not self._live_mode and self._real_pv_names():
                self._toggle_live_mode()
                return
            self._request_reload(delay_ms=0, reason="time window changed")

    # ── Conditions dialog ────────────────────────────────────────────────────

    def _open_conditions_dialog(self):
        # Custom channels are filtered exactly like real PVs (they are computed
        # before the conditions run), so they belong in the dropdown too.
        choices = [(self._col_label(pv), pv) for pv in self._real_pv_names()]
        choices += [(d["name"], d["name"]) for d in self._custom_pvs if d.get("name")]
        dlg = _ConditionsDialog(self._conditions_view(), choices, self,
                                on_change=self._set_conditions,
                                count_fn=self._condition_count)
        dlg.exec()

    def _conditions_view(self):
        """The rows the editor shows. ``_conditions`` is what filters; when
        something set it directly (a test, older code) it wins."""
        allc = list(getattr(self, "_conditions_all", []) or [])
        on = [(c.get("pv"), c.get("min"), c.get("max")) for c in allc if c.get("enabled", True)]
        now = [(c.get("pv"), c.get("min"), c.get("max")) for c in self._conditions]
        if on == now or not getattr(self, "_conditions_on", True):
            return allc
        return [_cond_normalize(c) for c in self._conditions]

    def _set_conditions_on(self, on):
        """The sidebar's "Apply conditions" tick box."""
        rows = self._conditions_view()      # read while the old state holds
        self._conditions_on = bool(on)
        self._set_conditions(rows)

    def _sync_conditions_tick(self):
        """Tooltip of "Apply conditions": how many rows it switches."""
        chk = getattr(self, "_chk_cond_on", None)
        if chk is None:
            return
        n = sum(1 for c in getattr(self, "_conditions_all", []) or []
                if c.get("enabled") and c.get("pv"))
        chk.setToolTip(
            f"{n} condition(s) switched on in Conditions. Untick to see every "
            "shot, including trigger shots and values outside the conditions.")

    def _set_conditions(self, conds):
        """Apply a new set of condition rows at once: table, graph and XY."""
        self._conditions_all = [_cond_normalize(c) for c in conds]
        self._conditions = ([c for c in self._conditions_all if c["enabled"]]
                            if getattr(self, "_conditions_on", True) else [])
        self._cond_last_diag = None     # report the new set's effect again
        self._sync_conditions_tick()
        rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
        self._table_rows = self._apply_conditions_to_rows(rows)
        self._populate_table()
        # The graph draws from _table_rows as well — without this it would
        # keep showing the rows the new conditions just discarded.
        if self._samples_by_pv:
            self._maybe_hunt_last_good()
            self._plot_graph()
        # …and so does the XY cloud. The Conditions button sits on the XY tab
        # too, and pressing it there used to change everything except the
        # plot the user was looking at.
        self._xy_dirty = True
        self._xy_refresh_live()
        # A condition on a hidden PV needs that PV read.
        self._catch_up_shown_pvs()

    def _condition_count(self, cond):
        """``(kept, total)`` rows of the loaded window for one condition on its
        own, or ``None`` when nothing is loaded."""
        rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
        if not rows:
            return None
        pv = cond.get("pv")
        if not pv or pv not in rows[-1][1]:
            return 0, 0
        lo, hi = cond.get("min"), cond.get("max")
        outside = cond.get("outside", False)
        kept = 0
        for _ts, row in rows:
            e = row.get(pv)
            if e is not None and self._condition_value_ok(e[0], lo, hi, outside):
                kept += 1
        return kept, len(rows)

    # ── Reference lines ──────────────────────────────────────────────────────

    def _open_ref_lines_dialog(self):
        """Open the Reference lines window, and keep re-opening it while the user
        goes off to place a line by clicking in the graph."""
        while True:
            dlg = _RefLinesDialog(self._ref_lines, self._graph_ref_pv_choices(), self)
            self._ref_dialog = dlg
            accepted = (dlg.exec() == QDialog.DialogCode.Accepted)
            self._ref_dialog = None
            if dlg.pick_request is not None and not accepted:
                # "Pick in graph" was pressed: keep the rows the user has typed so
                # far, run the two clicks, then come straight back to the window.
                self._ref_lines = dlg.result_lines
                placed = self._run_ref_pick()
                if placed is not None:
                    self._ref_lines.append(placed)
                if self._samples_by_pv:
                    self._plot_graph()
                continue
            if accepted:
                self._ref_lines = dlg.result_lines
                if self._samples_by_pv:
                    self._plot_graph()
            return

    def _graph_ref_pv_choices(self):
        """Signals a reference line can be tied to — the ones currently drawn
        first, then the rest of the loaded list."""
        drawn = [pv for pv in getattr(self, "_graph_pvs", []) if pv]
        rest  = [pv for pv in self._pv_order if pv not in drawn]
        return drawn + rest

    def _ref_axis_for(self, pv, ax0, axes, pvs_for_axes):
        """The axis a reference line belongs on.

        Every signal has its own Y scale squeezed into its own horizontal band, so
        a line tied to a signal must be drawn on THAT signal's axis — otherwise it
        sits at the height of signal #1 and means nothing for any other trace.
        """
        if pv and pv in pvs_for_axes:
            return axes[list(pvs_for_axes).index(pv)]
        return None if pv else ax0

    def _draw_ref_lines(self, ax0, axes, pvs_for_axes, fsize):
        from matplotlib.transforms import blended_transform_factory as _btf
        self._ref_line_artists = []
        for rl in self._ref_lines:
            ax_r = self._ref_axis_for(rl.get("pv"), ax0, axes, pvs_for_axes)
            if ax_r is None:
                continue          # tied to a signal that is hidden or has no data
            color = rl.get("color") or "#000000"
            style = _REF_LINE_STYLES.get(rl.get("style", "dashed"), "--")
            try:
                width = max(0.2, float(rl.get("width", 1.2)))
            except (TypeError, ValueError):
                width = 1.2
            ln = ax_r.axhline(y=rl["y"], color=color, linewidth=width,
                              linestyle=style, zorder=8)
            self._ref_line_artists.append(ln)
            # The Graph tab draws no legend, so the name is written next to the
            # line itself — otherwise a reference line is anonymous.
            label = (rl.get("label") or "").strip()
            txt = f"{label} {_fmt_cursor_value(rl['y'])}".strip() if label \
                else _fmt_cursor_value(rl["y"])
            ann = ax_r.text(0.004, rl["y"], txt, ha="left", va="bottom",
                            fontsize=max(5, fsize - 2), color=color, zorder=9,
                            transform=_btf(ax_r.transAxes, ax_r.transData),
                            clip_on=True)
            self._ref_line_artists.append(ann)

    # ── Placing a reference line by clicking in the graph ────────────────────

    def _run_ref_pick(self):
        """Two guided clicks: first the signal, then the height.

        Returns the new reference-line record, or None if the user backed out.
        Runs its own event loop so the Reference lines window can simply wait.
        """
        if not self._mpl_canvas or not self._graph_pvs:
            QMessageBox.information(self, "Reference lines",
                                    "Plot the graph first — there is nothing to click on yet.")
            return None
        # The graph can be floating in its own window (F11); only switch tabs
        # when it is actually still one of the tabs.
        if self._notebook.indexOf(self._tab_graph) >= 0:
            self._notebook.setCurrentWidget(self._tab_graph)

        loop_holder = {"result": None}
        self._ref_pick = {"stage": 1, "pv": None, "holder": loop_holder}

        # A click must not also start a statistics span or a zoom.
        for sel in (getattr(self, "_span_selector", None),
                    getattr(self, "_zoom_selector", None)):
            if sel is not None:
                try:
                    sel.set_active(False)
                except Exception:
                    pass

        canvas = self._mpl_canvas
        canvas.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        canvas.setFocus()
        self._ref_pick["cids"] = [
            canvas.mpl_connect("button_press_event", self._on_ref_pick_click),
            canvas.mpl_connect("key_press_event", self._on_ref_pick_key),
        ]
        self._set_ref_pick_step(1)

        from PySide6.QtCore import QEventLoop
        loop = QEventLoop(self)
        self._ref_pick["loop"] = loop
        loop.exec()
        return loop_holder["result"]

    _REF_PICK_HINTS = {
        1: ("Step 1 of 2 — click the Y axis of the signal you want "
            "(or click its curve).   Esc cancels."),
        2: ("Step 2 of 2 — now click in the graph at the height the line "
            "should sit.   Esc cancels."),
    }

    def _set_ref_pick_step(self, stage):
        """Show the instruction for this step and grey out everything the user is
        NOT supposed to click, so it is obvious where the next click goes."""
        if not self._ref_pick:
            return
        self._ref_pick["stage"] = stage
        hint = self._REF_PICK_HINTS[stage]
        if stage == 2 and self._ref_pick.get("pv"):
            pv   = self._ref_pick["pv"]
            name = self._col_label(pv)
            hint += f"      Signal: {name}"
        self._lbl_pick_hint.setText(hint)
        self._pick_bar.setVisible(True)
        # Step 1 dims the plot and leaves the Y-axis columns bright; step 2 does
        # the opposite. This is a plain overlay rectangle on top of the figure —
        # no real curve, tick or crosshair is touched, so nothing can break.
        self._clear_ref_pick_dim()
        fig = self._mpl_figure
        if fig is None:
            return
        from matplotlib.patches import Rectangle
        try:
            box = self._graph_axes[0].get_position()
        except Exception:
            return
        if stage == 1:
            rects = [(box.x0, box.y0, box.width, box.height)]      # dim the plot
        else:
            rects = [(0.0, 0.0, max(0.0, box.x0), 1.0)]            # dim the axis strip
        self._ref_pick["dim"] = []
        for x, y, w, h in rects:
            if w <= 0 or h <= 0:
                continue
            patch = Rectangle((x, y), w, h, transform=fig.transFigure,
                              facecolor="white", alpha=0.62, edgecolor="none",
                              zorder=50)
            fig.add_artist(patch)
            self._ref_pick["dim"].append(patch)
        self._mpl_canvas.draw_idle()

    def _clear_ref_pick_dim(self):
        for patch in (self._ref_pick or {}).get("dim", []) or []:
            try:
                patch.remove()
            except Exception:
                pass
        if self._ref_pick:
            self._ref_pick["dim"] = []

    def _end_ref_pick(self, result=None):
        """Leave pick mode, whatever the reason, and put the graph back exactly
        as it was — overlay gone, span and zoom working again."""
        pick = self._ref_pick
        if not pick:
            return
        self._clear_ref_pick_dim()
        self._ref_pick = None
        canvas = self._mpl_canvas
        for cid in pick.get("cids", []):
            try:
                canvas.mpl_disconnect(cid)
            except Exception:
                pass
        for sel in (getattr(self, "_span_selector", None),
                    getattr(self, "_zoom_selector", None)):
            if sel is not None:
                try:
                    sel.set_active(True)
                except Exception:
                    pass
        self._pick_bar.setVisible(False)
        if canvas is not None:
            canvas.draw_idle()
        pick["holder"]["result"] = result
        loop = pick.get("loop")
        if loop is not None:
            loop.quit()

    def _cancel_ref_pick(self):
        self._end_ref_pick(None)

    def _on_ref_pick_key(self, event):
        if event.key == "escape":
            self._cancel_ref_pick()

    def _on_ref_pick_click(self, event):
        if not self._ref_pick or event.button != 1:
            return
        if event.x is None or event.y is None:
            return
        if self._ref_pick["stage"] == 1:
            pv = self._pv_at_click(event.x, event.y)
            if pv is None:
                return                       # missed everything — stay on step 1
            self._ref_pick["pv"] = pv
            self._set_ref_pick_step(2)
            return
        pv = self._ref_pick["pv"]
        try:
            gi = self._graph_pvs.index(pv)
            ax = self._graph_axes[gi]
            y  = float(ax.transData.inverted().transform((event.x, event.y))[1])
        except Exception:
            self._cancel_ref_pick()
            return
        name = self._col_label(pv)
        self._end_ref_pick({"label": name, "pv": pv, "y": y,
                            "color": "#000000", "style": "dashed", "width": 1.2})

    def _pv_at_click(self, px, py):
        """Which signal did this click mean?

        Left of the plot the answer is the nearest Y axis; inside the plot it is
        the nearest curve, compared in screen height so signals on wildly
        different scales compete fairly.
        """
        axes = getattr(self, "_graph_axes", []) or []
        pvs  = getattr(self, "_graph_pvs", []) or []
        if not axes or not pvs:
            return None
        try:
            box = axes[0].get_window_extent()
        except Exception:
            return None

        if px < box.x0:                       # the stacked Y-axis columns
            best, best_d = None, None
            for i, ax in enumerate(axes):
                if i >= len(pvs):
                    break
                try:
                    sx = float(ax.transAxes.transform(
                        (self._graph_spine_xpos[i][0], 0.0))[0])
                except Exception:
                    continue
                d = abs(px - sx)
                if best_d is None or d < best_d:
                    best, best_d = pvs[i], d
            return best

        raw_np = getattr(self, "_graph_raw_np", []) or []
        best, best_d = None, None
        for i, ax in enumerate(axes):
            if i >= len(pvs) or i >= len(raw_np):
                break
            if not self._pv_settings.get(pvs[i], {}).get("show", True):
                continue
            arr, vals = raw_np[i]
            if not len(arr):
                continue
            try:
                x_data = float(ax.transData.inverted().transform((px, py))[0])
            except Exception:
                continue
            # steps-post: the sample at or before the cursor is the one drawn.
            pos = int(np.searchsorted(arr, x_data, side="right"))
            idx = pos - 1 if pos > 0 else 0
            try:
                y_px = float(ax.transData.transform((arr[idx], vals[idx]))[1])
            except Exception:
                continue
            d = abs(py - y_px)
            if best_d is None or d < best_d:
                best, best_d = pvs[i], d
        # A click nowhere near any curve is a miss, not a silent wrong guess.
        if best_d is not None and best_d > 60:
            return None
        return best


    # ── Custom PV dialog ─────────────────────────────────────────────────────

    def open_custom_pv_editor(self, parent=None) -> bool:
        """The "Add custom" editor, opened from another tab (Spectra's shot
        filter). True when the user pressed OK."""
        return self._open_custom_pv_dialog(parent)

    def _open_custom_pv_dialog(self, parent=None) -> bool:
        # A button's clicked(bool) lands in `parent` — only a widget counts.
        owner = parent if isinstance(parent, QWidget) else self
        dlg = _CustomPVDialog(self._custom_pvs, self._cpv_dialog_channels(), owner)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        # Channel names typed over in the dialog: the same Display Name as the
        # PV table's, so it shows everywhere and is remembered.
        for pv, name in dlg.result_display_names.items():
            self._set_pv_display_name(pv, name)
        self._custom_pvs = dlg.result_pvs
        save_custom_pvs(self._custom_pvs)
        self._sync_pv_list_customs()   # reflect new/removed customs in the PV LIST
        self._update_pv_count()
        self._cpv_last_diag = None     # report problems for the new set again
        if self._table_rows_unfiltered:
            # Recompute customs and re-run the filter pipeline so the new
            # channels appear with real values everywhere (graph + table).
            self._rebuild_custom_pvs()
            rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
            self._table_rows = self._apply_conditions_to_rows(rows)
            self._plot_graph()          # also refreshes the PV table
            self._populate_table()
            self._refresh_xy_choices()
            self._xy_dirty = True
            self._xy_refresh_live()
        # A custom PV built on a hidden PV needs that PV read.
        self._catch_up_shown_pvs()
        return True

    # ── Table export ─────────────────────────────────────────────────────────

    def _export_shot_channels(self, channels) -> list:
        """Which of ``channels`` may define a shot, by default.

        A shot table needs a grid of moments, and only the channels that are
        written once per shot can supply it. Everything else — a rate readback,
        a timing or state word, a beam fate, a valve position, the ramp master —
        is written whenever it feels like it and would add rows that are not
        shots. Those channels are still exported, held at their last value.
        The dialog shows this choice and the user can change it.
        """
        customs = {d.get("name", "") for d in self._custom_pvs}
        master  = self._get_master_pv()
        out = []
        for pv in channels:
            if pv in customs or pv == master:
                continue
            # A word value is never a shot mark. A number is — including one
            # that only ever appears as a held value here, because the choice
            # must not change with the window that happens to be loaded.
            pre = self._pre_window_vals.get(pv)
            numeric = (pv in self._numeric_pvs
                       or (pre is not None and isinstance(pre[1], (int, float))))
            if not numeric:
                continue
            low = pv.lower()
            if any(h in low for h in _NON_SHOT_PV_HINTS):
                continue
            out.append(pv)
        return out

    def _open_export_dialog(self):
        channels = [pv for pv in self._pv_order]
        if not channels:
            QMessageBox.information(self, "No channels",
                                    "Add channels to the list first."); return
        dlg = _ExportTableDialog(
            parent=self,
            channels=channels,
            display={pv: self._col_label(pv) for pv in channels},
            customs=[d.get("name", "") for d in self._custom_pvs],
            checked=self._table_pvs(),
            shot_channels=self._export_shot_channels(self._table_pvs()),
            dt_from=self._dt_from, dt_to=self._dt_to,
            has_conditions=bool(self._conditions),
            has_rows=bool(self._table_rows))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = dlg.result_cfg
        if not cfg["channels"]:
            QMessageBox.warning(self, "Nothing selected",
                                "Tick at least one channel to export."); return
        if cfg["source"] == "archive" and not cfg["basis"]:
            QMessageBox.warning(
                self, "No shot channels",
                "Tick at least one channel that defines a shot — those "
                "timestamps are the rows of the table."); return
        stamp = f"{self._dt_from:%Y%m%d-%H%M}_{self._dt_to:%Y%m%d-%H%M}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export table", f"CSS-Logger_{stamp}.csv", "CSV (*.csv)")
        if not path:
            return
        if cfg["source"] == "screen":
            self._export_rows_on_screen(path, cfg)
        else:
            self._export_from_archive(path, cfg)

    def _export_rows_on_screen(self, path, cfg):
        """Write the rows that are already merged and on screen."""
        rows = self._table_rows or []
        if not rows:
            QMessageBox.information(self, "No data", "No rows on screen."); return
        chans = cfg["channels"]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";", lineterminator="\n")
                f.write("sep=;\n")
                w.writerow(["Timestamp"] + [self._col_label(p) for p in chans])
                for ts, row_dict in rows:
                    w.writerow([ns_to_local_str(ts)] + [
                        _export_fmt(row_dict.get(pv, (None,))[0], cfg["comma"])
                        for pv in chans])
        except Exception as exc:
            QMessageBox.critical(self, "Export error", str(exc)); return
        self._log(f"Exported {len(rows)} rows on screen to {path}")
        self._lbl_status.setText(f"Exported {len(rows)} rows.")

    def _export_from_archive(self, path, cfg):
        """Read the whole chosen window again and write every shot in it.

        Runs in its own thread, one slice of time at a time, and appends to the
        file as it goes — a year of shots is a legitimate request and must never
        have to fit in memory. The dialog on top only reports and cancels.
        """
        prev = getattr(self, "_export_prog", None)
        if prev is not None and prev.isVisible():
            if getattr(prev, "_finished", False):
                prev.close()                 # last export's result window
            else:
                QMessageBox.information(
                    self, "Export running",
                    "An export is already running. Cancel it first."); return
        if self._live_mode:
            self._log("Export started while Live is on — both are reading the "
                      "archive, so switching Live off makes the export quicker.")
        chans   = list(cfg["channels"])
        basis   = list(cfg["basis"])
        customs = {d.get("name", "") for d in self._custom_pvs}
        real    = [pv for pv in chans if pv not in customs]
        # Custom channels are computed here, not read: their formulas need every
        # source loaded even when the source itself is not being exported.
        # Every formula is computed, not only the exported ones: a formula can
        # read another custom channel, and that one need not be a column.
        plan, _broken = self._cpv_plan()
        for _n, _c, srcs in plan:
            for _lt, src in srcs:
                if src not in real and src not in customs:
                    real.append(src)
        start_ns = dt_to_ns(self._dt_from)
        end_ns   = dt_to_ns(self._dt_to)
        conds    = [dict(c) for c in self._conditions] if cfg["conditions"] else []
        timeout  = self._http_timeout()
        headers  = ["Timestamp"] + [self._col_label(p) for p in chans]

        self._export_epoch = getattr(self, "_export_epoch", 0) + 1
        epoch  = self._export_epoch
        cancel = lambda: self._export_epoch != epoch          # noqa: E731

        prog = QProgressDialog("Reading the archive…", "Cancel", 0, 100, self)
        prog.setWindowTitle("Export table")
        prog.setWindowModality(Qt.WindowModality.NonModal)
        prog.setAutoClose(False); prog.setAutoReset(False)
        prog.setMinimumDuration(0)
        prog.setMinimumWidth(420)
        prog.canceled.connect(lambda: setattr(self, "_export_epoch", epoch + 1))
        prog.show()
        self._export_prog = prog
        sig = _LoadSig()
        self._export_sig = sig                     # keep alive until it lands

        def _on_progress(msg):
            if not prog.isHidden():
                prog.setLabelText(msg)

        def _on_pct(pct):
            if not prog.isHidden():
                prog.setValue(max(0, min(100, int(pct))))

        # The result is written into the progress window the user already has
        # open, never into a new message box: an export can end minutes later,
        # and a box popping up then takes the keyboard from whatever the user
        # is typing elsewhere.
        def _show_result(text):
            prog._finished = True
            if prog.isHidden():
                return
            prog.setLabelText(text)
            prog.setCancelButtonText("Close")

        def _on_done(payload):
            n_rows, n_slices, cancelled = payload
            what = "cancelled after" if cancelled else "wrote"
            self._log(f"Export {what} {n_rows} shots from {n_slices} "
                      f"slice{'' if n_slices == 1 else 's'} → {path}")
            self._lbl_status.setText(
                f"Export {'cancelled — partial file kept' if cancelled else 'finished'}: "
                f"{n_rows} shots.")
            if cancelled:
                prog.close()
            else:
                prog.setValue(100)
                _show_result(f"Finished: {n_rows} shots written to\n{path}")

        def _on_error(msg):
            self._log(f"Export failed: {msg}")
            self._lbl_status.setText("Export failed — see Log.")
            _show_result(f"Export failed:\n{msg}")

        sig.progress.connect(_on_progress)
        sig.pct.connect(_on_pct)
        sig.note.connect(self._log)
        sig.done.connect(_on_done)
        sig.error.connect(_on_error)

        def _worker():
            try:
                from cpva_core import (cpva_fetch_many_adaptive, cpva_decode_value,
                                       cpva_fetch_last_before_many)
                gap_ns = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
                slices = _export_slices(start_ns, end_ns)
                n_rows = 0
                done_slices = 0
                held: dict = {}
                last_ts = None
                reported: set = set()
                sig.progress.emit("Reading the last value before the window…")
                try:
                    seed = cpva_fetch_last_before_many(
                        real, start_ns, timeout, cancel_fn=cancel)
                except Exception:
                    seed = {}
                for pv, s in (seed or {}).items():
                    held[pv] = cpva_decode_value(s)
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f, delimiter=";", lineterminator="\n")
                    f.write("sep=;\n")
                    w.writerow(headers)
                    for i, (s_lo, s_hi) in enumerate(slices):
                        if cancel():
                            break
                        sig.progress.emit(
                            f"{ns_to_local_str(s_lo)[:10]} · slice {i + 1}/"
                            f"{len(slices)} · {n_rows} shots so far")
                        sig.pct.emit(int(i / max(1, len(slices)) * 100))
                        raw, errs, _rep = cpva_fetch_many_adaptive(
                            real, s_lo, s_hi, count=None, timeout=timeout,
                            cancel_fn=cancel, log_fn=sig.note.emit)
                        if cancel():
                            break
                        for pv, err in (errs or {}).items():
                            # Once per channel, not once per slice: a channel
                            # the archiver refuses would otherwise write one
                            # line per slice into the Log for a whole year.
                            if pv in reported:
                                continue
                            reported.add(pv)
                            sig.note.emit(f"Export · {shorten_pv_name(pv)}: {err}")
                        samples = {
                            pv: [(int(s["time"]), cpva_decode_value(s))
                                 for s in raw.get(pv, []) if s.get("time")]
                            for pv in real}
                        rows, held = _export_slice_rows(
                            samples, basis, real, held, gap_ns, s_lo, s_hi)
                        for ts, vals in rows:
                            if last_ts is not None and ts - last_ts <= gap_ns:
                                continue      # the seam duplicate between slices
                            # Moved on for every shot, not only the written
                            # ones, or a row dropped by the conditions would
                            # leave the seam guard comparing against an older
                            # moment and drop the next real shot with it.
                            last_ts = ts
                            for name, code, srcs in plan:
                                vals[name] = _export_eval(code, srcs, vals)
                            if conds and not _export_row_ok(vals, conds):
                                continue
                            w.writerow([ns_to_local_str(ts)] + [
                                _export_fmt(vals.get(pv), cfg["comma"])
                                for pv in chans])
                            n_rows += 1
                        done_slices = i + 1
                        f.flush()
                sig.pct.emit(100)
                sig.done.emit((n_rows, done_slices, bool(cancel())))
            except Exception as exc:
                import traceback; traceback.print_exc()
                sig.error.emit(f"{type(exc).__name__}: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    # ── Log ──────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        if not hasattr(self, '_log_area'):
            return
        self._log_area.appendPlainText(
            f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")
        self._log_area.ensureCursorVisible()

    def _clear_log(self):
        self._log_area.clear()

    # ── Status ───────────────────────────────────────────────────────────────

    def _update_status(self, msg: str):
        self._lbl_status.setText(msg)


# ── Export dialog ────────────────────────────────────────────────────────────

_EXPORT_LIST_STYLE = (
    "QListWidget { background:#ffffff; color:#111; border:1px solid #b0b0b0; }"
    "QListWidget::item { padding:2px 3px; }"
    "QListWidget::item:selected { background:#cfe3ff; color:#111; }"
    "QListWidget::indicator { width:15px; height:15px; border:2px solid #4a4a4a;"
    " border-radius:3px; background:#ffffff; }"
    "QListWidget::indicator:checked { border-color:#1565C0; background:#1565C0; }"
)


class _ExportTableDialog(QDialog):
    """What to export, and what counts as a shot.

    Two lists, because they answer two different questions. The left one is the
    columns of the file. The right one is its ROWS: only the channels written
    once per shot can give the table its moments, and a channel that is written
    at its own pace (a rate, a state word, a beam fate) would otherwise fill the
    file with rows where nothing was actually fired.
    """

    def __init__(self, parent, channels, display, customs, checked,
                 shot_channels, dt_from, dt_to, has_conditions, has_rows):
        super().__init__(parent)
        self.setWindowTitle("Export table")
        self.setModal(True)
        self.setMinimumSize(760, 520)
        self.result_cfg = {}
        self._customs = set(c for c in customs if c)
        self._has_conditions = bool(has_conditions)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        hdr = QLabel(f"{dt_from:%Y-%m-%d %H:%M}  →  {dt_to:%Y-%m-%d %H:%M}")
        hdr.setStyleSheet("color:#1565C0;font-weight:700;font-size:12px;")
        lay.addWidget(hdr)

        row = QHBoxLayout(); lay.addLayout(row, stretch=1)

        left = QGroupBox("Channels to export")
        l_lay = QVBoxLayout(left)
        self._lst_cols = self._make_list(channels, display, set(checked),
                                         allow_custom=True)
        l_lay.addWidget(self._lst_cols, stretch=1)
        l_lay.addLayout(self._all_none_row(self._lst_cols))
        row.addWidget(left, stretch=1)

        right = QGroupBox("Channels that define a shot")
        r_lay = QVBoxLayout(right)
        self._lst_basis = self._make_list(
            [pv for pv in channels if pv not in self._customs], display,
            set(shot_channels), allow_custom=False)
        r_lay.addWidget(self._lst_basis, stretch=1)
        r_lay.addLayout(self._all_none_row(self._lst_basis))
        row.addWidget(right, stretch=1)

        src_row = QHBoxLayout(); lay.addLayout(src_row)
        src_row.addWidget(QLabel("Rows:"))
        self._cmb_src = QComboBox()
        self._cmb_src.addItem("Every shot in the window — reads the archive again",
                              "archive")
        self._cmb_src.addItem("The rows already on screen", "screen")
        self._cmb_src.setMinimumWidth(340)
        if not has_rows:
            self._cmb_src.model().item(1).setEnabled(False)
        src_row.addWidget(self._cmb_src)
        src_row.addStretch()

        self._chk_cond = QCheckBox("Keep only rows that pass the Conditions")
        self._chk_cond.setStyleSheet(_CHK_STYLE)
        self._chk_cond.setChecked(has_conditions)
        self._chk_cond.setEnabled(has_conditions)
        lay.addWidget(self._chk_cond)

        self._chk_comma = QCheckBox("Decimal comma")
        self._chk_comma.setStyleSheet(_CHK_STYLE)
        lay.addWidget(self._chk_comma)

        self._cmb_src.currentIndexChanged.connect(self._sync_enabled)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        btns.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(_BTN_PRIMARY)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._sync_enabled()

    # ── helpers ───────────────────────────────────────────────────────────
    def _make_list(self, channels, display, checked, allow_custom):
        lst = QListWidget()
        lst.setStyleSheet(_EXPORT_LIST_STYLE)
        lst.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # Nothing here is selectable, so the current-item frame on the first row
        # would only look like a selection that cannot be cleared.
        lst.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for pv in channels:
            it = QListWidgetItem(display.get(pv, pv))
            it.setData(Qt.ItemDataRole.UserRole, pv)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if pv in checked
                             else Qt.CheckState.Unchecked)
            if allow_custom and pv in self._customs:
                it.setForeground(QColor("#3949AB"))
                fnt = it.font(); fnt.setItalic(True); it.setFont(fnt)
                it.setText("ƒ  " + display.get(pv, pv))
            it.setToolTip(pv)
            lst.addItem(it)
        return lst

    @staticmethod
    def _all_none_row(lst):
        r = QHBoxLayout()
        b_all  = QPushButton("All")
        b_none = QPushButton("None")
        for b in (b_all, b_none):
            b.setFixedWidth(64)
        def _set(state):
            for i in range(lst.count()):
                lst.item(i).setCheckState(state)
        b_all.clicked.connect(lambda: _set(Qt.CheckState.Checked))
        b_none.clicked.connect(lambda: _set(Qt.CheckState.Unchecked))
        r.addWidget(b_all); r.addWidget(b_none); r.addStretch()
        return r

    @staticmethod
    def _ticked(lst):
        return [lst.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(lst.count())
                if lst.item(i).checkState() == Qt.CheckState.Checked]

    def _sync_enabled(self):
        # The rows on screen are already merged and already filtered, so
        # neither the shot rule nor the Conditions tick can change them — both
        # belong to the re-read.
        archive = self._cmb_src.currentData() == "archive"
        self._lst_basis.setEnabled(archive)
        self._chk_cond.setEnabled(archive and self._has_conditions)

    def _accept(self):
        self.result_cfg = {
            "channels":   self._ticked(self._lst_cols),
            "basis":      self._ticked(self._lst_basis),
            "source":     self._cmb_src.currentData(),
            "conditions": self._chk_cond.isChecked() and self._chk_cond.isEnabled(),
            "comma":      self._chk_comma.isChecked(),
        }
        self.accept()


# ── Conditions Dialog ────────────────────────────────────────────────────────

_COND_KINDS = (("between", "Between"),
               ("pct",     "Target ± %"),
               ("abs",     "Target ± value"),
               ("above",   "At least"),
               ("below",   "At most"),
               ("outside", "Outside"))


def _cond_value_passes(v, vmin, vmax, outside=False):
    """One number against one condition's bounds (``None`` = open side). An
    "Outside" condition keeps what lies beyond the bounds; with no bounds at
    all it keeps everything, like an empty Between does."""
    if outside and vmin is None and vmax is None:
        return True
    inside = (vmin is None or v >= vmin) and (vmax is None or v <= vmax)
    return (not inside) if outside else inside


def _cond_mask_passes(v, vmin, vmax, outside=False):
    """Array version of :func:`_cond_value_passes`; NaN never passes."""
    ok = np.isfinite(v)
    if outside and vmin is None and vmax is None:
        return ok
    inside = np.ones(v.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        if vmin is not None:
            inside &= v >= vmin
        if vmax is not None:
            inside &= v <= vmax
    return ok & (~inside if outside else inside)


def _cond_range_passes(lo, hi, vmin, vmax, outside=False):
    """:func:`_cond_mask_passes` for thinned points: each point stands for a
    stretch of shots from ``lo`` to ``hi``, and passes when ANY part of that
    stretch could — so a bin mixing laser-off and laser-on shots is kept
    instead of being judged (and thrown away) by its mean. A raw point has
    lo == hi and is judged exactly as before. NaN never passes."""
    ok = np.isfinite(lo) & np.isfinite(hi)
    if outside and vmin is None and vmax is None:
        return ok
    with np.errstate(invalid="ignore"):
        if outside:
            beyond = np.zeros(lo.shape, dtype=bool)
            if vmin is not None:
                beyond |= lo < vmin
            if vmax is not None:
                beyond |= hi > vmax
            return ok & beyond
        touch = np.ones(lo.shape, dtype=bool)
        if vmin is not None:
            touch &= hi >= vmin
        if vmax is not None:
            touch &= lo <= vmax
    return ok & touch


# How far back, and in what steps, the last shot that passes the Conditions is
# hunted when a period holds none. Six hours of a 5 s channel is ~4 300 raw
# samples, which the archiver serves in one go; two weeks covers a long
# shutdown without letting a channel that never passes read for ever.
_LAST_PASS_STEP_NS  = int(6 * 3600 * 1e9)
_LAST_PASS_DEPTH_NS = int(14 * 86400 * 1e9)


def _last_passing_moment(conds, before_ns, gap_ns, timeout=None, cancel_fn=None,
                         progress_fn=None, fetch_fn=None):
    """Newest moment before ``before_ns`` at which every condition passes.

    ``conds`` is ``[(pv, min, max, outside), …]``. Walks back in
    ``_LAST_PASS_STEP_NS`` steps, reading only the condition channels, and
    judges each of their sample times the way the table rows do: every
    condition channel at its newest value up to ``gap_ns`` after that moment.
    Returns the moment (ns) or None when nothing passes within
    ``_LAST_PASS_DEPTH_NS`` (or the archiver cannot be read).

    This is what keeps a period with the laser off from drawing nothing: the
    Conditions reject every shot in it, and the value from just before it is
    a laser-off value too, so the last value worth showing is older still.
    """
    if fetch_fn is None:
        from cpva_core import cpva_fetch_many_adaptive as fetch_fn
    pvs = list(dict.fromkeys(c[0] for c in conds))
    if not pvs:
        return None
    hi = int(before_ns)
    floor = hi - _LAST_PASS_DEPTH_NS
    while hi > floor:
        if cancel_fn is not None and cancel_fn():
            return None
        lo = max(floor, hi - _LAST_PASS_STEP_NS)
        if progress_fn is not None:
            progress_fn(lo)
        raw, errs, _rep = fetch_fn(pvs, lo, hi, count=None, timeout=timeout,
                                   cancel_fn=cancel_fn, preflight=False,
                                   verify=False)
        if errs and not any(raw.get(pv) for pv in pvs):
            return None                    # unreadable, not "nothing passes"
        arrays, cand = {}, []
        for pv in pvs:
            pts = []
            for s in raw.get(pv) or []:
                t = s.get("time")
                # The archiver brackets the range: a sample after `hi` is
                # newer than what is being judged and must not answer.
                if not t or int(t) >= hi:
                    continue
                v = cpva_decode_value(s)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    pts.append((int(t), float(v)))
            pts.sort()
            ts = np.array([p[0] for p in pts], dtype=np.int64)
            arrays[pv] = (ts, np.array([p[1] for p in pts], dtype=np.float64))
            cand.append(ts[ts >= lo])
        cand = np.unique(np.concatenate(cand)) if cand else np.empty(0, np.int64)
        if cand.size:
            keep = np.ones(cand.size, dtype=bool)
            for pv, vmin, vmax, outside in conds:
                c_ts, c_v = arrays[pv]
                idx = np.searchsorted(c_ts, cand + gap_ns, side="right") - 1
                v = np.full(cand.size, np.nan)
                has = idx >= 0
                if has.any():
                    v[has] = c_v[idx[has]]
                keep &= _cond_mask_passes(v, vmin, vmax, outside)
            if keep.any():
                return int(cand[keep].max())
        hi = lo
    return None


def _cond_bounds(kind, a, b):
    """``(lo, hi)`` of one condition, ``None`` for an open side."""
    if kind == "pct":
        if a is None or b is None:
            return None, None
        return tuple(sorted((a * (1 - b / 100.0), a * (1 + b / 100.0))))
    if kind == "abs":
        if a is None or b is None:
            return None, None
        return a - abs(b), a + abs(b)
    if kind == "above":
        return a, None
    if kind == "below":
        return None, a
    if a is not None and b is not None and b < a:
        a, b = b, a
    return a, b


def _cond_normalize(c):
    """Any stored condition → ``{pv, enabled, kind, a, b, min, max}``.

    ``min`` / ``max`` are the effective bounds and are what every filter reads,
    so the table, graph, XY, export and the PV Time Plot apply one rule. Old
    entries load as they were meant: ``{pv, min, max}`` (the Conditions window)
    as Between, ``{target, tol}`` (the PV Time Plot's rows) as Target ± %."""
    c = dict(c or {})
    kind = c.get("kind")
    if kind not in dict(_COND_KINDS):
        if "target" in c:
            kind, a, b = "pct", c.get("target"), c.get("tol")
        else:
            kind, a, b = "between", c.get("min"), c.get("max")
    else:
        a, b = c.get("a"), c.get("b")
    lo, hi = _cond_bounds(kind, a, b)
    return {"pv": c.get("pv", ""), "enabled": bool(c.get("enabled", True)),
            "kind": kind, "a": a, "b": b, "min": lo, "max": hi,
            "outside": kind == "outside"}


def _cond_text(c, label=None):
    """A condition as a person would say it: ``Energy 70 ± 10 %``."""
    name = label or shorten_pv_name(c.get("pv", ""))
    k, a, b = c.get("kind", "between"), c.get("a"), c.get("b")
    f = lambda v: "?" if v is None else f"{v:g}"       # noqa: E731
    if k == "pct":
        return f"{name} {f(a)} ± {f(b)} %"
    if k == "abs":
        return f"{name} {f(a)} ± {f(b)}"
    if k == "above":
        return f"{name} ≥ {f(a)}"
    if k == "below":
        return f"{name} ≤ {f(a)}"
    if k == "outside":
        lo, hi = _cond_bounds(k, a, b)
        if lo is None and hi is None:
            return f"{name} any value"
        if lo is None:
            return f"{name} > {f(hi)}"
        if hi is None:
            return f"{name} < {f(lo)}"
        return f"{name} outside {f(lo)} … {f(hi)}"
    if a is None and b is None:
        return f"{name} has a value"
    if a is None:
        return f"{name} ≤ {f(b)}"
    if b is None:
        return f"{name} ≥ {f(a)}"
    return f"{name} {f(a)} … {f(b)}"


class _ConditionEditor(QWidget):
    """The condition rows — one editor for the Conditions window and for the
    PV Time Plot, so both say and filter the same thing.

    A row: on/off · channel · kind (Between / Target ± % / Target ± value /
    At least / At most) · the one or two numbers that kind needs · how many
    rows/shots it keeps on its own · ✕. Every edit is announced through
    ``changed`` (collapsed over 350 ms), so the caller can apply it at once.
    ``count_fn(cond) -> (kept, total) | None`` fills the "keeps" column.
    """

    changed = Signal()

    _FIELD_LABELS = {          # kind → (first box, joiner, second box or None)
        "between": ("min", "…", "max"),
        "pct":     ("target", "±", "%"),
        "abs":     ("target", "±", "±"),
        "above":   ("min", "", None),
        "below":   ("max", "", None),
        "outside": ("min", "…", "max"),
    }

    def __init__(self, choices, conditions=(), count_fn=None, parent=None,
                 new_row_enabled=True):
        super().__init__(parent)
        self._rows: list = []
        self._count_fn = count_fn
        self._new_enabled = bool(new_row_enabled)
        self._choices: list = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self.changed.emit)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        self._rows_lay = QVBoxLayout()
        self._rows_lay.setSpacing(3)
        outer.addLayout(self._rows_lay)
        self._lbl_empty = QLabel("No conditions — every row is kept.")
        self._lbl_empty.setStyleSheet("color:#555555;")
        outer.addWidget(self._lbl_empty)
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 2, 0, 0)
        b_add = QPushButton("+ Add condition")
        b_add.clicked.connect(lambda: self.add_row())
        self._btn_clear = QPushButton("Remove all")
        self._btn_clear.clicked.connect(self.clear)
        ctrl.addWidget(b_add); ctrl.addWidget(self._btn_clear); ctrl.addStretch()
        outer.addLayout(ctrl)

        self.set_choices(choices)
        for c in conditions or ():
            self.add_row(c, announce=False)
        self._refresh_empty()

    # -- channels ---------------------------------------------------------
    @staticmethod
    def _as_pairs(choices):
        out = []
        for ch in choices or ():
            if isinstance(ch, (tuple, list)):
                out.append((str(ch[0]), str(ch[1])))
            else:
                out.append((str(ch), str(ch)))
        return out

    def set_choices(self, choices):
        """New channel list; every row keeps its channel (added at the end,
        marked "not in the list", when it is no longer offered)."""
        self._choices = self._as_pairs(choices)
        for rec in self._rows:
            self._fill_channel_combo(rec["pv"], self._row_pv(rec))

    def _fill_channel_combo(self, cb, pv):
        cb.blockSignals(True)
        cb.clear()
        for label, p in self._choices:
            cb.addItem(label, p)
            cb.setItemData(cb.count() - 1, p, Qt.ItemDataRole.ToolTipRole)
        if pv:
            i = cb.findData(pv)
            if i < 0:
                cb.addItem(f"{shorten_pv_name(pv)} (not in the PV list)", pv)
                i = cb.count() - 1
            cb.setCurrentIndex(i)
        elif cb.count():
            cb.setCurrentIndex(0)
        cb.blockSignals(False)

    def combos(self):
        return [rec["pv"] for rec in self._rows]

    @staticmethod
    def _row_pv(rec):
        d = rec["pv"].currentData()
        return d if d else rec["pv"].currentText().strip()

    # -- rows -------------------------------------------------------------
    @staticmethod
    def _num_txt(v):
        return "" if v is None else f"{v:g}"

    def add_row(self, existing=None, announce=True):
        c = _cond_normalize(existing) if existing else {
            "pv": "", "enabled": self._new_enabled, "kind": "between",
            "a": None, "b": None}
        row_w = QWidget()
        rl = QHBoxLayout(row_w)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        chk = QCheckBox()
        chk.setChecked(bool(c.get("enabled", True)))
        chk.setToolTip("Switch this condition on or off without losing it")
        pv_cb = QComboBox()
        pv_cb.setMinimumWidth(170)
        pv_cb.setMaximumWidth(380)
        pv_cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        pv_cb.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        pv_cb.setMinimumContentsLength(14)
        kind_cb = QComboBox()
        for key, text in _COND_KINDS:
            kind_cb.addItem(text, key)
        kind_cb.setCurrentIndex(max(0, kind_cb.findData(c.get("kind", "between"))))
        a_e = QLineEdit(self._num_txt(c.get("a"))); a_e.setFixedWidth(72)
        join = QLabel(""); join.setFixedWidth(14)
        join.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b_e = QLineEdit(self._num_txt(c.get("b"))); b_e.setFixedWidth(72)
        unit = QLabel(""); unit.setFixedWidth(14)
        count = QLabel(""); count.setMinimumWidth(120)
        count.setStyleSheet("color:#333333;")
        b_del = QPushButton("✕"); b_del.setFixedWidth(26)
        b_del.setToolTip("Delete this condition")
        b_del.setStyleSheet("QPushButton{background:#B71C1C;color:white;"
                            "border-radius:3px;}"
                            "QPushButton:hover{background:#7F0000;}")
        # The delete button sits right after the numbers it removes, not out at
        # the far edge of a wide window.
        for wdg in (chk, pv_cb, kind_cb, a_e, join, b_e, unit, b_del, count):
            rl.addWidget(wdg)
        rl.addStretch(1)
        # A one-number kind hides the second box but keeps its place, so the
        # columns of every row stay lined up.
        sp = b_e.sizePolicy(); sp.setRetainSizeWhenHidden(True); b_e.setSizePolicy(sp)
        rec = {"widget": row_w, "enabled": chk, "pv": pv_cb, "kind": kind_cb,
               "a": a_e, "b": b_e, "join": join, "unit": unit, "count": count}
        self._rows.append(rec)
        self._fill_channel_combo(pv_cb, c.get("pv", ""))
        self._apply_kind(rec)
        self._rows_lay.addWidget(row_w)

        chk.toggled.connect(lambda _v: self._touched())
        pv_cb.currentIndexChanged.connect(lambda _i: self._touched())
        kind_cb.currentIndexChanged.connect(lambda _i, r=rec: (self._apply_kind(r),
                                                               self._touched()))
        # A row that is off and has no numbers yet (a new row, or a saved one
        # left empty) switches itself on once a number is typed into it —
        # typing a limit means "use it". A row with limits that the user has
        # switched off by hand stays off.
        rec["auto_on"] = not chk.isChecked() and (
            existing is None or (c.get("a") is None and c.get("b") is None))
        chk.clicked.connect(lambda _v, r=rec: r.__setitem__("auto_on", False))

        def _typed(txt, r=rec):
            if r.get("auto_on") and txt.strip():
                r["auto_on"] = False
                r["enabled"].setChecked(True)
            self._touched()
        a_e.textChanged.connect(_typed)
        b_e.textChanged.connect(_typed)
        b_del.clicked.connect(lambda: self.remove_row(rec))
        self._refresh_empty()
        if announce:
            self._touched()
        return rec

    def _apply_kind(self, rec):
        kind = rec["kind"].currentData() or "between"
        first, joiner, second = self._FIELD_LABELS[kind]
        rec["a"].setPlaceholderText(first)
        rec["join"].setText(joiner if second else "")
        rec["b"].setVisible(second is not None)
        rec["b"].setPlaceholderText(second or "")
        rec["unit"].setText("%" if kind == "pct" else "")
        tips = {"between": "Kept when the value is between min and max "
                           "(leave one empty for an open side)",
                "pct":     "Kept when the value is within ± that many percent "
                           "of the target",
                "abs":     "Kept when the value is within ± that amount of "
                           "the target",
                "above":   "Kept when the value is at least this",
                "below":   "Kept when the value is at most this",
                "outside": "Kept when the value is below min or above max "
                           "(leave one empty for one side only)"}
        rec["kind"].setToolTip(tips[kind])

    def remove_row(self, rec):
        if rec not in self._rows:
            return
        self._rows.remove(rec)
        rec["widget"].hide()
        self._rows_lay.removeWidget(rec["widget"])
        rec["widget"].deleteLater()
        self._refresh_empty()
        self._touched()

    def clear(self):
        for rec in list(self._rows):
            self._rows.remove(rec)
            rec["widget"].hide()
            self._rows_lay.removeWidget(rec["widget"])
            rec["widget"].deleteLater()
        self._refresh_empty()
        self._touched()

    def _refresh_empty(self):
        self._lbl_empty.setVisible(not self._rows)
        self._btn_clear.setEnabled(bool(self._rows))

    def _touched(self):
        self._mark_bad()
        self._timer.start()

    # -- values -----------------------------------------------------------
    @staticmethod
    def _parse(txt):
        txt = (txt or "").strip().replace(",", ".")
        if not txt:
            return None, True
        try:
            return float(txt), True
        except ValueError:
            return None, False

    def _row_condition(self, rec):
        pv = self._row_pv(rec)
        if not pv:
            return None
        kind = rec["kind"].currentData() or "between"
        a = self._parse(rec["a"].text())[0]
        b = (None if self._FIELD_LABELS[kind][2] is None
             else self._parse(rec["b"].text())[0])
        return _cond_normalize({"pv": pv, "enabled": rec["enabled"].isChecked(),
                                "kind": kind, "a": a, "b": b})

    def _mark_bad(self):
        for rec in self._rows:
            for key in ("a", "b"):
                _v, ok = self._parse(rec[key].text())
                rec[key].setStyleSheet("" if ok else
                                       "QLineEdit{background:#FFEBEE;color:#111;"
                                       "border:1px solid #C62828;}")
                rec[key].setToolTip("" if ok else "Not a number — ignored")

    def conditions(self):
        """Every row that names a channel, switched on or not."""
        return [c for c in (self._row_condition(r) for r in self._rows) if c]

    def active_conditions(self):
        return [c for c in self.conditions() if c["enabled"]]

    def label_of(self, pv):
        for label, p in self._choices:
            if p == pv:
                return label
        return shorten_pv_name(pv)

    def refresh_counts(self):
        """Fill each row's "keeps N of M" from ``count_fn``."""
        for rec in self._rows:
            c = self._row_condition(rec)
            res = self._count_fn(c) if (c and self._count_fn) else None
            if res is None:
                rec["count"].setText("")
                rec["count"].setToolTip("")
                continue
            kept, total = res
            if total <= 0:
                rec["count"].setText("no data")
                rec["count"].setToolTip("This channel has no values here, so "
                                        "nothing can be judged")
                continue
            pct = 100.0 * kept / total
            rec["count"].setText(f"keeps {kept:,} of {total:,}")
            rec["count"].setToolTip(f"On its own this condition keeps {pct:.1f} %")
            rec["count"].setStyleSheet("color:#B71C1C;font-weight:600;" if kept == 0
                                       else "color:#333333;")


class _ConditionsDialog(QDialog):
    """The Conditions window. Edits apply as they are made (``on_change``),
    so the graph, table and XY behind it show the effect at once."""

    def __init__(self, conditions, pvs, parent=None, on_change=None, count_fn=None):
        super().__init__(parent)
        self.setWindowTitle("Conditions")
        self.resize(760, 360)
        self._on_change = on_change
        lay = QVBoxLayout(self)
        lbl = QLabel("A row is kept only when every condition that is switched "
                     "on holds for it. Changes apply at once to the graph, the "
                     "table, the XY plot and the export. A condition on a PV "
                     "with no data in the loaded window is skipped.")
        lbl.setStyleSheet("color:#444444;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        hl = QVBoxLayout(holder); hl.setContentsMargins(0, 0, 0, 0)
        self.editor = _ConditionEditor(pvs, conditions, count_fn=count_fn)
        hl.addWidget(self.editor)
        hl.addStretch()
        scroll.setWidget(holder)
        lay.addWidget(scroll, stretch=1)
        self.editor.changed.connect(self._changed)
        self.editor.refresh_counts()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self._close)
        buttons.accepted.connect(self._close)
        lay.addWidget(buttons)

    @property
    def result_conditions(self):
        return self.editor.conditions()

    def _changed(self):
        if self._on_change is not None:
            self._on_change(self.editor.conditions())
        self.editor.refresh_counts()

    def _close(self):
        # A change still waiting out the 350 ms collapse is applied, not lost.
        if self.editor._timer.isActive():
            self.editor._timer.stop()
            self._changed()
        self.accept()

    def reject(self):
        self._close()


# ── Reference Lines Dialog ──────────────────────────────────────────────────

class _RefLinesDialog(QDialog):
    """Horizontal reference lines: name, which signal they belong to, height,
    colour, style and thickness — plus reordering, deleting, and placing one by
    clicking in the graph."""

    _NO_PV = "— no signal (first Y scale) —"

    def __init__(self, ref_lines, pv_choices=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reference Lines")
        self.resize(1010, 360)
        self._rows: list = []
        self._pv_choices = list(pv_choices or [])
        self.result_lines: list = []
        # Set to True when the user asks to place a line by clicking; the caller
        # then closes this window, runs the two clicks and re-opens it.
        self.pick_request = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "A line tied to a signal is drawn on that signal's own Y scale, so it "
            "keeps its meaning even though every signal has its own scale."))

        heads = QHBoxLayout()
        heads.setContentsMargins(0, 0, 0, 0)
        for text, width in (("", 52), ("Label", 140), ("Signal", 210),
                            ("Y value", 90), ("", 30), ("Colour", 40),
                            ("Style", 100), ("Width", 60), ("Crossing", 80),
                            ("", 26)):
            h = QLabel(text)
            h.setFixedWidth(width)
            h.setStyleSheet("font-weight:700;color:#1565C0;")
            heads.addWidget(h)
        heads.addStretch()
        lay.addLayout(heads)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget()
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setSpacing(4)
        self._inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, stretch=1)

        for rl in ref_lines:
            self._add_row(rl)

        ctrl = QHBoxLayout()
        b_add = QPushButton("+ Add line"); b_add.clicked.connect(lambda: self._add_row())
        ctrl.addWidget(b_add)
        b_pick = QPushButton("+ Add by clicking in the graph")
        b_pick.setToolTip("Close this window, click the signal's Y axis, then click "
                          "the height. The line comes back here to be named.")
        b_pick.clicked.connect(self._request_pick)
        ctrl.addWidget(b_pick)
        ctrl.addStretch()
        lay.addLayout(ctrl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # ── rows ────────────────────────────────────────────────────────────────

    def _add_row(self, existing=None):
        existing = existing or {}
        row_w = QWidget()
        rl    = QHBoxLayout(row_w); rl.setContentsMargins(0, 0, 0, 0)

        b_up = QToolButton(); b_up.setText("▲"); b_up.setFixedWidth(24)
        b_up.setToolTip("Move up")
        b_dn = QToolButton(); b_dn.setText("▼"); b_dn.setFixedWidth(24)
        b_dn.setToolTip("Move down")

        lbl_e = QLineEdit(existing.get("label", "")); lbl_e.setFixedWidth(140)
        lbl_e.setPlaceholderText("Label")

        pv_cb = QComboBox(); pv_cb.setFixedWidth(210)
        pv_cb.addItem(self._NO_PV)
        pv_cb.addItems(self._pv_choices)
        pv = existing.get("pv")
        if pv:
            i = pv_cb.findText(pv)
            if i < 0:                       # a signal that is no longer loaded
                pv_cb.addItem(pv); i = pv_cb.count() - 1
            pv_cb.setCurrentIndex(i)

        y_val = existing.get("y", "")
        y_e   = QLineEdit("" if y_val == "" else str(y_val)); y_e.setFixedWidth(90)
        y_e.setPlaceholderText("Y value")

        b_pick = QToolButton(); b_pick.setText("⊹"); b_pick.setFixedWidth(26)
        b_pick.setToolTip("Pick this line's signal and height by clicking in the graph")

        col_btn = QPushButton(); col_btn.setFixedWidth(36)
        col_hex = existing.get("color") or "#000000"

        style_cb = QComboBox(); style_cb.setFixedWidth(100)
        style_cb.addItems(list(_REF_LINE_STYLES))
        si = style_cb.findText(str(existing.get("style", "dashed")))
        style_cb.setCurrentIndex(si if si >= 0 else style_cb.findText("dashed"))

        w_e = QLineEdit(str(existing.get("width", 1.2))); w_e.setFixedWidth(60)
        w_e.setPlaceholderText("Width")

        # Which side of the line counts as crossing it, for Limit crossings.
        cross_cb = QComboBox(); cross_cb.setFixedWidth(80)
        cross_cb.addItems(list(_REF_CROSSING))
        ci = cross_cb.findText(str(existing.get("crossing", "above")))
        cross_cb.setCurrentIndex(ci if ci >= 0 else 0)
        cross_cb.setToolTip("Limit crossings lists the times the signal went "
                            "above (or below) this line")

        b_del = QPushButton("✕"); b_del.setFixedWidth(26)
        b_del.setStyleSheet("QPushButton{background:#B71C1C;color:white;border-radius:3px;}")

        for w in (b_up, b_dn, lbl_e, pv_cb, y_e, b_pick, col_btn, style_cb, w_e,
                  cross_cb, b_del):
            rl.addWidget(w)
        rl.addStretch()

        rec = {"widget": row_w, "label": lbl_e, "pv": pv_cb, "y": y_e,
               "color": col_hex, "btn": col_btn, "style": style_cb, "width": w_e,
               "crossing": cross_cb}
        col_btn.setStyleSheet(f"background:{col_hex};border-radius:3px;")
        col_btn.clicked.connect(lambda: self._pick_color(col_btn, rec))
        b_del.clicked.connect(lambda: self._remove_row(row_w, rec))
        b_up.clicked.connect(lambda: self._move_row(rec, -1))
        b_dn.clicked.connect(lambda: self._move_row(rec, +1))
        b_pick.clicked.connect(self._request_pick)

        self._rows.append(rec)
        self._inner_lay.insertWidget(self._inner_lay.count() - 1, row_w)

    def _pick_color(self, btn, rec):
        col = QColorDialog.getColor(QColor(rec["color"]), self, "Pick color")
        if col.isValid():
            rec["color"] = col.name()
            btn.setStyleSheet(f"background:{col.name()};border-radius:3px;")

    def _remove_row(self, row_w, rec):
        self._rows.remove(rec)
        self._inner_lay.removeWidget(row_w)
        row_w.setParent(None)
        row_w.deleteLater()

    def _move_row(self, rec, delta):
        i = self._rows.index(rec)
        j = i + delta
        if not (0 <= j < len(self._rows)):
            return
        self._rows[i], self._rows[j] = self._rows[j], self._rows[i]
        # Re-stack the widgets in the new order (the stretch stays last).
        for w in [r["widget"] for r in self._rows]:
            self._inner_lay.removeWidget(w)
        for k, r in enumerate(self._rows):
            self._inner_lay.insertWidget(k, r["widget"])

    def _request_pick(self):
        """Hand back what is typed so far and ask the caller to run the two
        clicks in the graph."""
        self.pick_request = True
        self.result_lines = self._collect()[0]
        self.reject()

    # ── result ──────────────────────────────────────────────────────────────

    def _collect(self):
        result, bad = [], []
        for n, rec in enumerate(self._rows, start=1):
            y = CSSLoggerWidget._safe_float(rec["y"].text())
            if y is None:
                if rec["y"].text().strip() or rec["label"].text().strip():
                    bad.append(n)
                continue
            pv = rec["pv"].currentText()
            width = CSSLoggerWidget._safe_float(rec["width"].text(), 1.2)
            result.append({
                "label": rec["label"].text().strip(),
                "pv":    None if pv == self._NO_PV else pv,
                "y":     y,
                "color": rec["color"] or "#000000",
                "style": rec["style"].currentText(),
                "width": max(0.2, width if width else 1.2),
                "crossing": rec["crossing"].currentText(),
            })
        return result, bad

    def _accept(self):
        result, bad = self._collect()
        if bad:
            # Silently dropping a row the user filled in is how a reference line
            # "disappears for no reason" — say which one instead.
            QMessageBox.warning(
                self, "Reference Lines",
                "These lines have no usable Y value and will be dropped:\n"
                + ", ".join(f"line {n}" for n in bad))
        self.result_lines = result
        self.accept()


# ── Limit crossings window ───────────────────────────────────────────────────

def _fmt_duration_ns(ns):
    s = max(0.0, ns / 1e9)
    if s < 60:
        return f"{s:.1f} s"
    if s < 3600:
        return f"{int(s // 60)} min {int(s % 60)} s"
    if s < 86400:
        return f"{int(s // 3600)} h {int(s % 3600 // 60)} min"
    return f"{int(s // 86400)} d {int(s % 86400 // 3600)} h"


class _LimitCrossingsDialog(QDialog):
    """Every stretch in which a signal was over (or under) one of its reference
    lines. Double-click a row to show that moment in the graph."""

    zoom_to = Signal(object, object)          # (start ns, end ns)

    _HEAD = ("Line", "Signal", "Start", "End", "Duration", "Peak", "Peak at",
             "Samples")

    def __init__(self, cond_note="", parent=None):
        # Opened at the click, empty, and filled by set_result() when the
        # search ends — so no window ever appears on its own later.
        super().__init__(parent)
        self.setWindowTitle("Limit crossings")
        self.resize(1000, 460)
        # Every colour set here: this PC runs Windows in dark mode, and an
        # unpainted dialog comes out black behind dark text.
        self.setStyleSheet(
            "QDialog{background:#f3f3f3;}"
            "QLabel{color:#111;background:transparent;}"
            "QPushButton{background:#ffffff;color:#111;border:1px solid #9aa3ad;"
            "border-radius:3px;padding:3px 14px;}"
            "QPushButton:hover{background:#e8f0fb;}")
        self._rows = []
        lay = QVBoxLayout(self)
        top = self._top = QLabel("Looking for limit crossings")
        top.setStyleSheet("font-weight:700;color:#111;")
        lay.addWidget(top)
        if cond_note:
            lay.addWidget(QLabel(cond_note))

        tv = self._tv = QTableWidget(0, len(self._HEAD))
        tv.setHorizontalHeaderLabels(list(self._HEAD))
        tv.verticalHeader().setVisible(False)
        tv.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tv.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tv.setAlternatingRowColors(True)
        # Light cells, dark text, a header that reads as a header — never left
        # to the (dark) system theme.
        tv.setStyleSheet(
            "QTableWidget{background:#ffffff;alternate-background-color:#f4f6f8;"
            "color:#111;gridline-color:#d6d6d6;"
            "selection-background-color:#cfe3fb;selection-color:#111;}"
            "QHeaderView::section{background:#e3e6ea;color:#111;font-weight:600;"
            "border:1px solid #c8ccd2;padding:3px 6px;}")
        tv.horizontalHeader().setStretchLastSection(True)   # no dead strip
        tv.cellDoubleClicked.connect(self._on_double_click)
        lay.addWidget(tv, stretch=1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Double-click a row to show it in the graph."))
        bottom.addStretch()
        b_close = QPushButton("Close"); b_close.clicked.connect(self.close)
        bottom.addWidget(b_close)
        lay.addLayout(bottom)

    def set_progress(self, text):
        if not self._rows:
            self._top.setText(text)

    def set_result(self, res):
        self._rows = rows = res["rows"]
        n = len(rows)
        approx = sum(1 for r in rows if not r["exact"])
        head = (f"{n} crossing{'' if n == 1 else 's'} of "
                f"{res['lines']} reference line{'' if res['lines'] == 1 else 's'}")
        if approx:
            head += (f"  ·  {approx} only to the thinned interval "
                     f"(marked ≈, see the Log tab)")
        self._top.setText(head)
        tv = self._tv
        tv.setRowCount(n)
        for i, r in enumerate(rows):
            mark = "" if r["exact"] else "≈ "
            end = r["end"]
            cells = (
                r["line"] + ("  ↑" if r["side"] == "above" else "  ↓"),
                shorten_pv_name(r["pv"]),
                mark + ns_to_local_str(r["start"])[:19],
                (mark + ns_to_local_str(end)[:19]) if end else "still over at the end",
                mark + _fmt_duration_ns((end or r.get("open_until") or r["start"])
                                        - r["start"]),
                _fmt_cursor_value(r["peak"]),
                mark + ns_to_local_str(r["peak_at"])[:19],
                str(r["samples"]) if r["exact"] else "",
            )
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if j in (4, 5, 7):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
                tv.setItem(i, j, it)
        tv.resizeColumnsToContents()

    def _on_double_click(self, row, _col):
        if 0 <= row < len(self._rows):
            r = self._rows[row]
            self.zoom_to.emit(r["start"], r["end"] or r.get("open_until") or r["start"])


# ── Graph settings dialog ────────────────────────────────────────────────────

class _GraphSettingsDialog(QDialog):
    """Fonts, axis-column spacing, plot margins and cursor options for the
    banded Graph tab. Apply repaints the live graph without closing, Cancel
    restores whatever was in effect when the dialog opened."""

    applied = Signal(dict)

    # Edge gaps are shown as percentages of the figure, which is how a user
    # thinks about them; the stored values are matplotlib subplot fractions
    # (margin_top is the TOP of the plot rect, hence 100 − gap).
    def __init__(self, opts: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Graph settings")
        self.original_opts = dict(opts)
        self.result_opts   = dict(opts)
        self.applied_once  = False      # was a live Apply already painted?

        lay  = QVBoxLayout(self)
        cols = QHBoxLayout(); lay.addLayout(cols)
        left = QVBoxLayout(); right = QVBoxLayout()
        cols.addLayout(left); cols.addLayout(right)

        def _spin(vmin, vmax, val, suffix="", step=1, tip=""):
            w = QSpinBox(); w.setRange(vmin, vmax); w.setValue(int(round(val)))
            w.setSingleStep(step); w.setFixedWidth(80)
            if suffix: w.setSuffix(" " + suffix)
            if tip: w.setToolTip(tip)
            return w

        def _pct(vmin, vmax, frac, tip=""):
            """Percent-of-figure spin box for a stored 0–1 fraction."""
            w = QDoubleSpinBox(); w.setRange(vmin, vmax); w.setDecimals(1)
            w.setSingleStep(0.5); w.setSuffix(" %"); w.setFixedWidth(80)
            w.setValue(round(frac * 100.0, 1))
            if tip: w.setToolTip(tip)
            return w

        def _check(val, tip=""):
            w = QCheckBox(); w.setChecked(bool(val))
            if tip: w.setToolTip(tip)
            return w

        def _group(title, rows, box_lay):
            gb = QGroupBox(title)
            g  = QGridLayout(gb); g.setVerticalSpacing(4)
            for r, (text, widget) in enumerate(rows):
                g.addWidget(QLabel(text), r, 0)
                g.addWidget(widget, r, 1)
            g.setColumnStretch(0, 1)
            box_lay.addWidget(gb)

        o = opts
        self.w_font   = _spin(5, 24, o.get("font_size", 11), "pt")
        self.w_tickd  = _spin(-6, 6, o.get("tick_font_delta", -1), "pt",
                              tip="Tick-number size relative to the axis-title size")
        self.w_curd   = _spin(-6, 6, o.get("cursor_font_delta", 0), "pt",
                              tip="Cursor readout size relative to the axis-title size")
        _group("Fonts", [("Axis titles", self.w_font),
                         ("Tick numbers", self.w_tickd),
                         ("Cursor readouts", self.w_curd)], left)

        self.w_titles = _check(o.get("show_axis_titles", True),
                               "Rotated PV name next to each Y axis")
        _d = _GRAPH_OPTS_DEFAULTS
        self.w_gap    = _spin(0, 60, o.get("axis_gap_px", _d["axis_gap_px"]), "px",
                              tip="Whitespace between an axis title and the axis "
                                  "to its left — this is the gap that made the "
                                  "columns wide")
        self.w_pad    = _spin(-8, 40, o.get("label_pad_px", _d["label_pad_px"]), "px",
                              tip="Gap between an axis title and its own tick "
                                  "numbers. Negative values pull the title into "
                                  "the empty margin around the rotated numbers, "
                                  "which is the last bit of slack there is.")
        self.w_outer  = _spin(0, 80, o.get("outer_margin_px", _d["outer_margin_px"]), "px",
                              tip="Whitespace left of the outermost axis title")
        self.w_yticks = _spin(2, 12, o.get("y_ticks_max", 6),
                              tip="Upper bound; fewer are used if they would overlap")
        self.w_minor  = _check(o.get("y_minor_ticks", True),
                               "Small unlabelled ticks between the major ones. "
                               "Turning them off speeds up drawing with many PVs.")
        _group("Y axis columns", [("Show axis titles", self.w_titles),
                                  ("Gap to next axis", self.w_gap),
                                  ("Title ↔ numbers", self.w_pad),
                                  ("Outer margin", self.w_outer),
                                  ("Max Y ticks", self.w_yticks),
                                  ("Minor ticks", self.w_minor)], left)

        # Time axis. "Every" overrides the automatic spacing; the list is the same
        # set of round steps the automatic choice picks from.
        self.w_xticks = _spin(2, 24, o.get("x_ticks_max", 12),
                              tip="Upper bound on how many time stamps are drawn "
                                  "across the plot (ignored when a fixed spacing "
                                  "is chosen below)")
        self.w_xstep  = QComboBox(); self.w_xstep.setFixedWidth(110)
        self.w_xstep.setToolTip("Fixed spacing between time stamps. Automatic "
                                "picks a round step that fits the window.")
        for _lbl, _sec in _X_TICK_STEPS:
            self.w_xstep.addItem(_lbl, _sec)
        _cur_step = int(o.get("x_tick_seconds", 0))
        _idx = self.w_xstep.findData(_cur_step)
        self.w_xstep.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.w_xfmt   = QComboBox(); self.w_xfmt.setFixedWidth(110)
        for _lbl, _key in (("Automatic", "auto"), ("12:34:56", "hms"), ("12:34", "hm")):
            self.w_xfmt.addItem(_lbl, _key)
        _idx = self.w_xfmt.findData(str(o.get("x_time_format", "auto")))
        self.w_xfmt.setCurrentIndex(_idx if _idx >= 0 else 0)
        _group("Time axis", [("Max time stamps", self.w_xticks),
                             ("Every", self.w_xstep),
                             ("Clock format", self.w_xfmt)], right)

        self.w_mright = _pct(0, 30, float(o.get("margin_right", 0.015)))
        self.w_mtop   = _pct(0, 40, 1.0 - float(o.get("margin_top", 0.97)))
        self.w_mbot   = _pct(2, 40, float(o.get("margin_bottom", 0.12)),
                             tip="Must stay tall enough for the time axis labels")
        self.w_band   = _pct(0, 45, float(o.get("band_pad_frac", 0.12)),
                             tip="Empty space kept above and below each PV's trace "
                                 "inside its own band")
        _group("Plot margins", [("Right gap", self.w_mright),
                                ("Top gap", self.w_mtop),
                                ("Bottom gap", self.w_mbot),
                                ("Gap inside band", self.w_band)], right)

        self.w_boxes  = _check(o.get("cursor_value_boxes", True),
                               "Value box per PV at the crosshair. The most "
                               "expensive part of hovering with many PVs.")
        self.w_bmax   = _spin(1, 64, o.get("cursor_boxes_max", 20),
                              tip="Above this many PVs the value boxes are dropped "
                                  "automatically")
        self.w_marks  = _check(o.get("line_markers", True),
                               "Dots on traces with fewer than 200 points")
        _group("Cursor & performance", [("Value boxes at cursor", self.w_boxes),
                                        ("…up to this many PVs", self.w_bmax),
                                        ("Point markers", self.w_marks)], right)

        # Live speed. Kept as three separate numbers on purpose: the graph refresh
        # is cheap and the table rebuild is not, and tying them together is what
        # made both of them wait for the slower one.
        self.w_poll   = _spin(50, 5000, o.get("live_poll_ms", 300), "ms", step=50,
                              tip="How often the archive is asked for new values")
        self.w_gmin   = _spin(50, 5000, o.get("live_graph_min_ms", 300), "ms", step=50,
                              tip="Shortest gap between graph refreshes. The graph "
                                  "slows itself down on its own if the computer "
                                  "cannot keep up.")
        self.w_tabms  = _spin(200, 20000, o.get("live_table_ms", 1500), "ms", step=250,
                              tip="Shortest gap between table rebuilds. Rebuilding "
                                  "the table is the expensive part, so it is kept "
                                  "slower than the graph.")
        _group("Live speed", [("Ask archive every", self.w_poll),
                              ("Graph refresh, at most every", self.w_gmin),
                              ("Table refresh, at most every", self.w_tabms)], right)
        right.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel
                                | QDialogButtonBox.StandardButton.Apply
                                | QDialogButtonBox.StandardButton.RestoreDefaults)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self._emit_apply)
        btns.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults)
        lay.addWidget(btns)

    def _emit_apply(self):
        self.applied_once = True
        self.applied.emit(self._collect())

    def _collect(self) -> dict:
        return {
            "font_size":          self.w_font.value(),
            "tick_font_delta":    self.w_tickd.value(),
            "cursor_font_delta":  self.w_curd.value(),
            "show_axis_titles":   self.w_titles.isChecked(),
            "axis_gap_px":        self.w_gap.value(),
            "label_pad_px":       self.w_pad.value(),
            "outer_margin_px":    self.w_outer.value(),
            "y_ticks_max":        self.w_yticks.value(),
            "y_minor_ticks":      self.w_minor.isChecked(),
            "x_ticks_max":        self.w_xticks.value(),
            "x_tick_seconds":     int(self.w_xstep.currentData() or 0),
            "x_time_format":      str(self.w_xfmt.currentData() or "auto"),
            "margin_right":       round(self.w_mright.value() / 100.0, 5),
            "margin_top":         round(1.0 - self.w_mtop.value() / 100.0, 5),
            "margin_bottom":      round(self.w_mbot.value() / 100.0, 5),
            "band_pad_frac":      round(self.w_band.value() / 100.0, 5),
            "cursor_value_boxes": self.w_boxes.isChecked(),
            "cursor_boxes_max":   self.w_bmax.value(),
            "line_markers":       self.w_marks.isChecked(),
            "live_poll_ms":       self.w_poll.value(),
            "live_graph_min_ms":  self.w_gmin.value(),
            "live_table_ms":      self.w_tabms.value(),
        }

    def _restore_defaults(self):
        d = _GRAPH_OPTS_DEFAULTS
        self.w_font.setValue(d["font_size"])
        self.w_tickd.setValue(d["tick_font_delta"])
        self.w_curd.setValue(d["cursor_font_delta"])
        self.w_titles.setChecked(d["show_axis_titles"])
        self.w_gap.setValue(d["axis_gap_px"])
        self.w_pad.setValue(d["label_pad_px"])
        self.w_outer.setValue(d["outer_margin_px"])
        self.w_yticks.setValue(d["y_ticks_max"])
        self.w_minor.setChecked(d["y_minor_ticks"])
        self.w_xticks.setValue(d["x_ticks_max"])
        self.w_xstep.setCurrentIndex(max(0, self.w_xstep.findData(d["x_tick_seconds"])))
        self.w_xfmt.setCurrentIndex(max(0, self.w_xfmt.findData(d["x_time_format"])))
        self.w_mright.setValue(round(d["margin_right"] * 100, 1))
        self.w_mtop.setValue(round((1.0 - d["margin_top"]) * 100, 1))
        self.w_mbot.setValue(round(d["margin_bottom"] * 100, 1))
        self.w_band.setValue(round(d["band_pad_frac"] * 100, 1))
        self.w_boxes.setChecked(d["cursor_value_boxes"])
        self.w_bmax.setValue(d["cursor_boxes_max"])
        self.w_marks.setChecked(d["line_markers"])
        self.w_poll.setValue(d["live_poll_ms"])
        self.w_gmin.setValue(d["live_graph_min_ms"])
        self.w_tabms.setValue(d["live_table_ms"])
        self._emit_apply()

    def _accept(self):
        self.result_opts = self._collect()
        self.accept()


class _MarginsDialog(QDialog):
    """Margins and spacing of the graph, applied as soon as a number changes.

    Replaces matplotlib's own "subplots" dialog, whose horizontal / vertical
    spacing sliders space several separate plots apart — this graph is one plot
    with stacked bands, so they did nothing. These are the gaps that exist here.
    Same stored keys as Graph settings, so the two dialogs always agree."""

    changed = Signal(dict)

    def __init__(self, opts: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Margins and spacing")
        self.original_opts = {k: opts.get(k, _GRAPH_OPTS_DEFAULTS[k])
                              for k in self._KEYS}
        lay = QVBoxLayout(self)

        def _spin(vmin, vmax, val, tip):
            w = QSpinBox(); w.setRange(vmin, vmax); w.setValue(int(round(val)))
            w.setSuffix(" px"); w.setFixedWidth(80); w.setToolTip(tip)
            return w

        def _pct(vmin, vmax, frac, tip=""):
            w = QDoubleSpinBox(); w.setRange(vmin, vmax); w.setDecimals(1)
            w.setSingleStep(0.5); w.setSuffix(" %"); w.setFixedWidth(80)
            w.setValue(round(frac * 100.0, 1))
            if tip: w.setToolTip(tip)
            return w

        def _group(title, rows):
            gb = QGroupBox(title)
            g = QGridLayout(gb); g.setVerticalSpacing(4)
            for r, (text, widget) in enumerate(rows):
                g.addWidget(QLabel(text), r, 0)
                g.addWidget(widget, r, 1)
            g.setColumnStretch(0, 1)
            lay.addWidget(gb)

        o = self.original_opts
        self.w_pad = _spin(-8, 40, o["label_pad_px"],
                           "Gap between a Y-axis title and its own numbers. "
                           "Negative values pull the title closer.")
        self.w_gap = _spin(0, 60, o["axis_gap_px"],
                           "Gap between a Y-axis title and the axis to its left")
        self.w_outer = _spin(0, 80, o["outer_margin_px"],
                             "Gap left of the outermost Y-axis title")
        _group("Y axes", [("Title ↔ numbers", self.w_pad),
                          ("Gap between Y axes", self.w_gap),
                          ("Left edge", self.w_outer)])

        self.w_mright = _pct(0, 30, float(o["margin_right"]))
        self.w_mtop = _pct(0, 40, 1.0 - float(o["margin_top"]))
        self.w_mbot = _pct(2, 40, float(o["margin_bottom"]),
                           "Must stay tall enough for the time axis labels")
        self.w_band = _pct(0, 45, float(o["band_pad_frac"]),
                           "Empty space above and below each PV's trace "
                           "inside its own band")
        _group("Plot margins", [("Right gap", self.w_mright),
                                ("Top gap", self.w_mtop),
                                ("Bottom gap", self.w_mbot),
                                ("Gap inside band", self.w_band)])

        for w in (self.w_pad, self.w_gap, self.w_outer):
            w.valueChanged.connect(self._emit)
        for w in (self.w_mright, self.w_mtop, self.w_mbot, self.w_band):
            w.valueChanged.connect(self._emit)

        row = QHBoxLayout()
        b_def = QPushButton("Defaults")
        b_def.setToolTip("Put every value here back to the program's default")
        b_def.clicked.connect(lambda: self._load(_GRAPH_OPTS_DEFAULTS))
        b_undo = QPushButton("Undo changes")
        b_undo.setToolTip("Back to the values from when this window was opened")
        b_undo.clicked.connect(lambda: self._load(self.original_opts))
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.close)
        row.addWidget(b_def); row.addWidget(b_undo); row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)

    _KEYS = ("label_pad_px", "axis_gap_px", "outer_margin_px", "margin_right",
             "margin_top", "margin_bottom", "band_pad_frac")

    def collect(self) -> dict:
        return {
            "label_pad_px":    self.w_pad.value(),
            "axis_gap_px":     self.w_gap.value(),
            "outer_margin_px": self.w_outer.value(),
            "margin_right":    round(self.w_mright.value() / 100.0, 5),
            "margin_top":      round(1.0 - self.w_mtop.value() / 100.0, 5),
            "margin_bottom":   round(self.w_mbot.value() / 100.0, 5),
            "band_pad_frac":   round(self.w_band.value() / 100.0, 5),
        }

    def _emit(self, *_):
        self.changed.emit(self.collect())

    def _load(self, src):
        widgets = (self.w_pad, self.w_gap, self.w_outer, self.w_mright,
                   self.w_mtop, self.w_mbot, self.w_band)
        for w in widgets:
            w.blockSignals(True)
        self.w_pad.setValue(int(src["label_pad_px"]))
        self.w_gap.setValue(int(src["axis_gap_px"]))
        self.w_outer.setValue(int(src["outer_margin_px"]))
        self.w_mright.setValue(round(float(src["margin_right"]) * 100, 1))
        self.w_mtop.setValue(round((1.0 - float(src["margin_top"])) * 100, 1))
        self.w_mbot.setValue(round(float(src["margin_bottom"]) * 100, 1))
        self.w_band.setValue(round(float(src["band_pad_frac"]) * 100, 1))
        for w in widgets:
            w.blockSignals(False)
        self._emit()


# ── Custom PV Dialog ─────────────────────────────────────────────────────────

class _CustomPVDialog(QDialog):
    """Editor for the derived channels.

    ``channels`` is ``[(letter, pv_name, display_name, loaded)]`` — the channels
    the letters currently stand for, including the PVs a formula is bound to but
    that are not loaded right now. Expressions are shown in *current* letters
    (rewritten from each entry's stored bindings) and are canonicalised back on
    OK, so a formula keeps its PVs across preset switches and list edits.

    Two tables spell the mapping out. "Available channels" is the automatic
    letter assignment for the currently loaded list (read-only — it follows the
    list, it is not a setting). "What each letter means" lists every letter of
    every formula with the PV behind it and lets that PV be changed: picking a
    different channel there swaps the letter inside the expression, so the
    binding follows the choice.
    """

    def __init__(self, custom_pvs, channels=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom PVs (derived channels)")
        self.resize(900, 780)
        self._rows: list = []
        self.result_pvs: list = []
        # {pv: name} for the Channel names typed over in the top table.
        self.result_display_names: dict = {}
        # Owned by the dialog, so a queued rebuild cannot fire on a table that
        # has already been destroyed (Cancel while the user was still typing).
        self._bind_timer = QTimer(self)
        self._bind_timer.setSingleShot(True)
        self._bind_timer.timeout.connect(self._refresh_bindings_table)

        channels = list(channels or [])
        self._channels     = channels
        self._pv_by_letter = {lt: pv for lt, pv, _d, _ld in channels}
        self._letter_by_pv = {pv: lt for lt, pv, _d, _ld in channels}
        self._loaded_by_pv = {pv: ld for _lt, pv, _d, ld in channels}
        self._disp_by_pv   = {pv: d for _lt, pv, d, _ld in channels}

        lay = QVBoxLayout(self)
        lbl = QLabel(
            "Define new virtual PVs as Python expressions over the channel "
            "letters (e.g. B/D, A*0.749). Every formula remembers the PV behind "
            "each letter, so the letters are re-assigned to follow the PVs when "
            "the loaded list changes — the same formula can read A*0.749 today "
            "and H*0.749 tomorrow and still mean the same PV.")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        # The program-wide grey background also lands on table cells, which
        # leaves the text sitting on grey next to white editors. Both tables in
        # this dialog get white cells and a visibly darker heading band.
        self._tbl_css = (
            # Only the view itself is painted white: a `::item` rule would beat
            # the per-item colours (the red "not loaded" text, the block band).
            "QTableWidget{background:#ffffff;gridline-color:#d8d8d8;}"
            "QHeaderView::section{background:#e4e4e4;color:#111;font-weight:600;"
            "padding:4px;border:0;border-right:1px solid #c8c8c8;"
            "border-bottom:1px solid #c8c8c8;}")

        # ── Reference table: which letter is which PV ────────────────────────
        ref_box = QGroupBox("Available channels — letters follow the loaded PV list")
        ref_lay = QVBoxLayout(ref_box)
        if channels:
            ref_tbl = QTableWidget(len(channels), 4)
            ref_tbl.setHorizontalHeaderLabels(["Var", "Channel", "PV name", "State"])
            ref_tbl.verticalHeader().setVisible(False)
            # The Channel name of a real PV can be typed over right here (the
            # same Display Name as in the PV table). Everything else is
            # read-only — the letters follow the list, they are not a setting.
            ref_tbl.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
            ref_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            # Without this the first cell wears a stray focus box.
            ref_tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ref_tbl.setMaximumHeight(186)
            ref_tbl.setStyleSheet(
                self._tbl_css
                + "QTableWidget QLineEdit{background:#ffffff;color:#111111;"
                  "border:1px solid #1565C0;padding:0 2px;}")
            ref_tbl.verticalHeader().setDefaultSectionSize(24)
            custom_names = {d.get("name", "") for d in (custom_pvs or [])}
            _ro = ~Qt.ItemFlag.ItemIsEditable
            for r, (letter, pv, disp, loaded) in enumerate(channels):
                it_l = QTableWidgetItem(letter)
                it_l.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it_d = QTableWidgetItem(disp)
                it_d.setData(Qt.ItemDataRole.UserRole, pv)
                cells = [it_l, it_d, QTableWidgetItem(pv),
                         QTableWidgetItem("loaded" if loaded else "not loaded")]
                for c, it in enumerate(cells):
                    # A custom PV is renamed in its Name box below.
                    if c != 1 or pv in custom_names:
                        it.setFlags(it.flags() & _ro)
                    if not loaded:
                        # Text only — the row must stay readable on the light
                        # table background.
                        it.setForeground(QColor("#B71C1C"))
                    ref_tbl.setItem(r, c, it)
            ref_tbl.itemChanged.connect(self._on_channel_renamed)
            self._ref_tbl = ref_tbl
            ref_tbl.setColumnWidth(0, 46)
            ref_tbl.setColumnWidth(1, 190)
            ref_tbl.setColumnWidth(3, 84)
            ref_tbl.horizontalHeader().setSectionResizeMode(
                2, QHeaderView.ResizeMode.Stretch)
            ref_lay.addWidget(ref_tbl)
        else:
            hint = QLabel("No PVs in the list yet — add some to get channel letters.")
            hint.setStyleSheet("color:#777;")
            ref_lay.addWidget(hint)
        lay.addWidget(ref_box)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget()
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setSpacing(4)
        self._inner_lay.addStretch()
        scroll.setWidget(inner)
        # The formula list is what the dialog is for — the two reference tables
        # below must not squeeze it down to a couple of visible rows.
        scroll.setMinimumHeight(210)
        lay.addWidget(scroll, stretch=1)
        self._formula_scroll = scroll

        for cpv in custom_pvs:
            self._add_row(cpv)

        ctrl = QHBoxLayout()
        b_add = QPushButton("+ Add"); b_add.clicked.connect(lambda: self._add_row())
        ctrl.addWidget(b_add); ctrl.addStretch()
        lay.addLayout(ctrl)

        # ── Bindings table: one row per letter per formula, PV editable ──────
        bind_box = QGroupBox("What each letter means — change a PV to re-bind that letter")
        bind_lay = QVBoxLayout(bind_box)
        self._bind_tbl = QTableWidget(0, 4)
        self._bind_tbl.setHorizontalHeaderLabels(
            ["Custom PV", "Var", "Stands for (PV)", "State"])
        self._bind_tbl.verticalHeader().setVisible(False)
        self._bind_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._bind_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._bind_tbl.setMinimumHeight(150)
        self._bind_tbl.setMaximumHeight(250)
        self._bind_tbl.setStyleSheet(self._tbl_css)
        self._bind_tbl.setColumnWidth(0, 220)
        self._bind_tbl.setColumnWidth(1, 46)
        self._bind_tbl.setColumnWidth(3, 84)
        self._bind_tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        bind_lay.addWidget(self._bind_tbl)
        lay.addWidget(bind_box)
        self._refresh_bindings_table()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _on_channel_renamed(self, item):
        """A Channel name typed over in the top table: remembered for OK, and
        the bindings table's tooltips follow it at once."""
        if item.column() != 1:
            return
        pv = item.data(Qt.ItemDataRole.UserRole)
        if not pv:
            return
        name = item.text().strip()
        if not name:
            # Empty = back to the automatic name, shown as such.
            name = shorten_pv_name(pv)
            tbl = item.tableWidget()
            tbl.blockSignals(True)
            item.setText(name)
            tbl.blockSignals(False)
        self._disp_by_pv[pv] = name
        self.result_display_names[pv] = name
        self._queue_bindings_refresh()

    # ── Bindings table ──────────────────────────────────────────────────────

    def _queue_bindings_refresh(self):
        """Rebuild the bindings table once the current signal has unwound.

        The rebuild deletes the very combo boxes whose signal asks for it, so it
        must not run inside the handler. Restarting the timer also collapses a
        burst of requests (one per keystroke) into a single rebuild.
        """
        self._bind_timer.start(0)

    def _refresh_bindings_table(self):
        tbl = getattr(self, "_bind_tbl", None)
        if tbl is None:      # queued from _add_row before the table was built
            return
        # The rebuild empties the table, which throws the view back to the
        # top — after every pick, keystroke and removal. Put it back where the
        # user was (clamped by Qt if the table got shorter).
        bar = tbl.verticalScrollBar()
        pos = bar.value()
        self._restore_scroll_later(bar, pos)
        tbl.setRowCount(0)
        # Only channels that actually have a letter can be picked — a letter is
        # how the expression names them. Already-saved custom PVs are in here
        # too (they are part of the channel list), so one custom PV can be built
        # on another; one added in this dialog gets its letter after OK.
        options = [pv for _lt, pv, _d, _ld in self._channels]
        tbl.clearSpans()
        for rec in self._rows:
            name    = rec["name"].text().strip() or "(unnamed)"
            expr    = rec["expr"].text().strip()
            letters = _cpv_vars(expr)
            if not letters:
                continue
            first = tbl.rowCount()
            for letter in letters:
                pv     = self._pv_by_letter.get(letter)
                loaded = self._loaded_by_pv.get(pv, True) if pv else False
                row = tbl.rowCount(); tbl.insertRow(row)
                tbl.setRowHeight(row, 26)

                it_var = QTableWidgetItem(letter)
                it_var.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if pv is None:
                    state = "no channel"
                elif not loaded:
                    state = "not loaded"
                else:
                    state = "loaded"
                it_state = QTableWidgetItem(state)
                for c, it in ((1, it_var), (3, it_state)):
                    if state != "loaded":
                        it.setForeground(QColor("#B71C1C"))
                    tbl.setItem(row, c, it)

                cb = QComboBox()
                cb.addItems(options)
                if pv:
                    if pv not in options:
                        cb.addItem(pv)
                    cb.setCurrentText(pv)
                    cb.setToolTip(self._disp_by_pv.get(pv, pv))
                else:
                    cb.setCurrentIndex(-1)
                # `activated` fires only on a real user pick — currentIndexChanged
                # would also fire while the box is being filled above.
                cb.activated.connect(
                    lambda _i, rc=rec, lt=letter, box=cb:
                    self._on_binding_picked(rc, lt, box.currentText()))
                tbl.setCellWidget(row, 2, cb)

            # One block per formula: the name is written once and the cell is
            # merged over the formula's letters, so a formula with two letters
            # no longer reads as the same custom PV listed twice. The expression
            # sits under the name to show where those letters come from.
            it_name = QTableWidgetItem(f"{name}\n{expr}" if len(letters) > 1
                                       else name)
            it_name.setToolTip(f"{name}   =   {expr}")
            it_name.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignVCenter)
            f = it_name.font(); f.setBold(True); it_name.setFont(f)
            # A deliberate light band with dark ink, so the merged cell reads as
            # the heading of its block and not as an unpainted gap.
            it_name.setBackground(QColor("#E8EEF8"))
            it_name.setForeground(QColor("#10305E"))
            if len(letters) > 1:
                tbl.setSpan(first, 0, len(letters), 1)
            tbl.setItem(first, 0, it_name)

    def _on_binding_picked(self, rec, letter, new_pv):
        """Re-point one letter of one formula at ``new_pv``.

        The binding is derived from the letters in the expression, so re-binding
        *is* swapping the letter for the one the chosen channel currently has —
        which also keeps the shown formula honest about what it reads.
        """
        if not new_pv:
            return
        new_letter = self._letter_by_pv.get(new_pv)
        if not new_letter or new_letter == letter:
            self._queue_bindings_refresh()
            return
        rec["expr"].setText(
            _cpv_rewrite(rec["expr"].text(), {letter: new_letter}))

    def _add_row(self, existing=None):
        row_w = QWidget()
        rl    = QHBoxLayout(row_w); rl.setContentsMargins(0,0,0,0)
        name_e = QLineEdit(existing.get("name","") if existing else "")
        name_e.setMinimumWidth(220); name_e.setPlaceholderText("Name")
        # Stored letters -> the letters valid right now.
        expr_txt = ""
        if existing:
            expr_txt, _unbound = _cpv_to_display(existing, self._letter_by_pv)
        expr_e = QLineEdit(expr_txt)
        expr_e.setMinimumWidth(260); expr_e.setPlaceholderText("Expression (Python)")
        warn = QLabel("⚠"); warn.setFixedWidth(18)
        warn.setStyleSheet("color:#B71C1C;font-weight:700;")
        b_del = QPushButton("✕"); b_del.setFixedWidth(26)
        b_del.setStyleSheet("QPushButton{background:#B71C1C;color:white;border-radius:3px;}")
        b_del.clicked.connect(lambda: self._remove_row(row_w, rec))
        rl.addWidget(name_e); rl.addWidget(expr_e); rl.addWidget(warn); rl.addWidget(b_del)
        # orig_name: the name the entry was saved under, so a rename can be
        # carried into the customs that are built on this one.
        rec = {"widget": row_w, "name": name_e, "expr": expr_e, "warn": warn,
               "orig_name": (existing.get("name", "") if existing else "")}
        self._rows.append(rec)
        expr_e.textChanged.connect(lambda _t, r=rec: self._refresh_row_state(r))
        # The bindings table lists a row per letter per formula, so both the
        # letters and the name column follow whatever is typed here.
        expr_e.textChanged.connect(lambda _t: self._queue_bindings_refresh())
        name_e.textChanged.connect(lambda _t: self._queue_bindings_refresh())
        self._refresh_row_state(rec)
        self._inner_lay.insertWidget(self._inner_lay.count() - 1, row_w)
        self._queue_bindings_refresh()

    def _refresh_row_state(self, rec):
        """Spell out what each letter in the row means, and flag the row when a
        letter has no channel or its PV is not loaded."""
        lines, bad = [], False
        for letter in _cpv_vars(rec["expr"].text()):
            pv = self._pv_by_letter.get(letter)
            if not pv:
                lines.append(f"{letter} = ?  (no such channel)")
                bad = True
            elif not self._loaded_by_pv.get(pv, True):
                lines.append(f"{letter} = {pv}   (not loaded)")
                bad = True
            else:
                lines.append(f"{letter} = {pv}")
        rec["expr"].setToolTip("\n".join(lines))
        rec["warn"].setVisible(bad)
        rec["warn"].setToolTip("\n".join(lines) if bad else "")

    @staticmethod
    def _restore_scroll_later(bar, pos):
        """Set ``bar`` back to ``pos`` now and once more after the pending
        relayout (the range is only known once the rows are measured)."""
        if pos <= 0:
            return                          # at the top already: nothing to keep

        def _set():
            # Only undo a jump to an end — never fight the user's own scroll.
            try:
                v = bar.value()
                if v != pos and (v == 0 or v == bar.maximum()):
                    bar.setValue(min(pos, bar.maximum()))
            except RuntimeError:            # dialog already closed
                pass
        QTimer.singleShot(0, _set)
        QTimer.singleShot(30, _set)

    def _remove_row(self, row_w, rec):
        self._rows.remove(rec)
        bar = self._formula_scroll.verticalScrollBar()
        self._restore_scroll_later(bar, bar.value())
        row_w.hide()
        self._inner_lay.removeWidget(row_w)
        row_w.deleteLater()
        self._queue_bindings_refresh()

    def _accept(self):
        out = []
        for r in self._rows:
            name = r["name"].text().strip()
            expr, bindings = _cpv_from_display(r["expr"].text(), self._pv_by_letter)
            if name and expr:
                out.append({"name": name, "expr": expr, "bindings": bindings})
        # A custom built on another custom is bound to that one's NAME, so a
        # rename here would leave it reading a channel that no longer exists.
        renamed = {r["orig_name"]: r["name"].text().strip() for r in self._rows
                   if r.get("orig_name") and r["name"].text().strip()
                   and r["name"].text().strip() != r["orig_name"]}
        if renamed:
            for d in out:
                d["bindings"] = {lt: renamed.get(pv, pv)
                                 for lt, pv in d["bindings"].items()}
        self.result_pvs = out
        self.accept()


# ── Icon helpers ─────────────────────────────────────────────────────────────

def _icon_file():
    """Locate icon.ico next to the exe (frozen) or the script. _HERE points at
    the source folder, which in a frozen build is not where icon.ico lives."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(pathlib.Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(pathlib.Path(meipass) / "icon.ico")
    cands.append(_HERE / "icon.ico")
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
# icon, so Qt's setWindowIcon is already sufficient. The real bug was that
# _HERE is not frozen-aware, so the frozen build never found icon.ico at all.
# _nudge_taskbar_icon above is a different thing: it sets no new picture, it
# only takes the window's own icon away and back so Explorer reads it again.

# Windows keeps a cached taskbar icon per AppUserModelID, and it does not
# refresh it when the app's icon changes. This app ran for months as
# "ELI.CSSLogger" without an icon.ico, so that identity is stuck on the
# placeholder Windows cached back then: the exe's icon, the title bar and every
# WM_GETICON slot show the LOG icon, while the taskbar button shows a generic
# window. Measured with a discriminating experiment on 2026-08-21 — two
# processes, same icon file, only the id different: the old id drew the generic
# window, a fresh id drew LOG. So the fix is a new id, not more icon code.
# The suffix used to be bumped by hand whenever the taskbar kept an old
# picture; _icon_app_id() now appends a hash of icon.ico instead, so the id
# retires itself the day the icon changes and no bumping is needed. Nothing
# else depends on the string: the app registers no shortcut, so a new id only
# starts a new taskbar group.
_APP_ID_PREFIX = "ELI.CSSLogger"


def _app_version():
    """Version of the running build, e.g. "v1.4.2", or "" when run from source.

    The builder names every exe "CSS Logger vX.Y.Z.exe", so the running exe's
    own file name is the one place that always tells the truth — also for an
    older version started from the archive.
    """
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", pathlib.Path(sys.executable).stem)
    return m.group(1) if m else ""


# ── Combined main window ─────────────────────────────────────────────────────

class CPVASuiteWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ver = _app_version()
        self.setWindowTitle(f"CSS Logger {ver}" if ver else "CSS Logger (source)")
        icon_path = _icon_file()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1600, 950)
        _install_wheel_guard()
        self._build_ui()
        self._restore_geometry()

    def _build_ui(self):
        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.North)
        tabs.setStyleSheet(
            "QTabBar::tab{padding:8px 18px;font-size:12px;font-weight:600;}"
            "QTabBar::tab:selected{background:#1565C0;color:white;border-radius:4px 4px 0 0;}"
            "QTabBar::tab:!selected{background:#E3F2FD;color:#1565C0;}"
        )
        # The CSS Logger is a row of setting tabs (logger_tabs.py), each one a
        # complete CSSLoggerWidget with its own channels, conditions and window.
        from logger_tabs import LoggerTabs
        self._logger_tabs = LoggerTabs(lambda st: CSSLoggerWidget(tab_state=st))
        tabs.addTab(self._logger_tabs, "  CSS Logger  ")
        try:
            from sp_t import SpectraWidget
            self._spectra = SpectraWidget()
            # The shot filter offers the same custom PVs (formulas) as the CSS
            # Logger tab — one list, one editor, one custom_pvs.json. Whichever
            # setting tab is on screen answers.
            self._spectra.set_custom_pv_host(
                lambda *a, **k: self._logger_tabs.active().cpv_defs_for_spectra(*a, **k),
                lambda *a, **k: self._logger_tabs.active().open_custom_pv_editor(*a, **k),
                _CPV_SAFE_ENV)
            tabs.addTab(self._spectra, "  Spectra  ")
        except Exception as exc:
            err_w = QLabel(f"Spectra tab unavailable:\n{exc}")
            err_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            err_w.setStyleSheet("color:#B71C1C;font-size:13px;")
            tabs.addTab(err_w, "  Spectra (error)  ")

        # A dark rim all round the program's own tabs (this bar only — the
        # stylesheet above also reaches the tabs inside the panels), so they
        # read apart from the setting tabs: same shape, smaller and pale grey,
        # on the same row. Each program tab has its own colour: CSS Logger the
        # green of its buttons, Spectra a lighter teal.
        tabs.tabBar().setStyleSheet(
            "QTabBar::tab{border:2px solid;border-radius:0px;"
            "border-top-left-radius:4px;border-top-right-radius:4px;"
            "margin:0px 2px 2px 0px;}"
            "QTabBar::tab:first{border-color:#1B5E20;background:#E8F5E9;color:#1B5E20;}"
            "QTabBar::tab:first:selected{background:#2E7D32;color:white;}"
            "QTabBar::tab:first:hover:!selected{background:#C8E6C9;}"
            "QTabBar::tab:last{border-color:#00695C;background:#E0F2F1;color:#00695C;}"
            "QTabBar::tab:last:selected{background:#00897B;color:white;}"
            "QTabBar::tab:last:hover:!selected{background:#B2DFDB;}")

        # The tab row: main tabs | the setting tabs in their tray (only while
        # the CSS Logger is on screen). QTabWidget gives a corner widget exactly
        # its size hint, so the strip asks for the whole gap after the main tabs.
        class _TabRowStrip(QWidget):
            def sizeHint(self):
                base = super().sizeHint()
                bar = tabs.tabBar()
                w = tabs.width() - bar.sizeHint().width() - 2
                return QSize(max(self.minimumSizeHint().width(), w),
                             max(base.height(), bar.sizeHint().height()))

        strip = _TabRowStrip()
        strip_lay = QHBoxLayout(strip)
        strip_lay.setContentsMargins(14, 0, 0, 0)
        strip_lay.setSpacing(6)
        logger_box = QFrame()
        logger_box.setObjectName("sessionTray")
        # The setting tabs stand on the main tabs' bottom line, after a gap.
        # The holder has no frame of its own and stays see-through; the scroll
        # arrows keep the window colour so no tab shows through them.
        logger_box.setStyleSheet("#sessionTray{background:transparent;border:none;}"
                                 "#sessionTray QWidget{background:transparent;}"
                                 "#sessionTray QTabBar QToolButton{background:#f3f3f3;}")
        box_lay = QHBoxLayout(logger_box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(0)
        strip_lay.addWidget(logger_box, 0, Qt.AlignmentFlag.AlignBottom)
        strip_lay.addStretch(1)
        tabs.setCornerWidget(strip, Qt.Corner.TopRightCorner)
        self._logger_tabs.move_chrome_into(box_lay)

        def _sync_tab_row(idx):
            on = tabs.widget(idx) is self._logger_tabs
            logger_box.setVisible(on)
            self._logger_tabs.set_outer_visible(on)
            # The bold current tab changes the main bar's width: re-ask.
            strip.updateGeometry()

        tabs.currentChanged.connect(_sync_tab_row)
        _sync_tab_row(tabs.currentIndex())
        self._main_tabs = tabs
        self.setCentralWidget(tabs)

    @property
    def _css_logger(self):
        """The first setting tab: it owns the config file, as the one CSS
        Logger did before there were tabs."""
        return self._logger_tabs.first()

    def _restore_geometry(self):
        try:
            cfg = load_config()
            geom = cfg.get("suite_geometry")
            if not geom:
                return
            x, y, w, h = geom
            # A saved position from a different monitor layout can point at a
            # screen that no longer exists, opening the window off-screen.
            # If the title-bar area isn't on any current screen, recentre it
            # on the primary monitor.
            on_screen = any(
                s.availableGeometry().contains(QPoint(int(x) + 40, int(y) + 20))
                for s in QApplication.screens()
            )
            if not on_screen:
                avail = QApplication.primaryScreen().availableGeometry()
                w = min(w, avail.width())
                h = min(h, avail.height())
                x = avail.x() + (avail.width()  - w) // 2
                y = avail.y() + (avail.height() - h) // 2
            self.setGeometry(int(x), int(y), int(w), int(h))
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            # Every tab's setting goes to logger_tabs.json; the config file
            # keeps what it always held (graph options, looks, …), from the
            # first tab — never built just for this.
            self._logger_tabs.save()
            built = self._logger_tabs.loggers()
            if built:
                built[0]._save_runtime_state()
        except Exception:
            pass
        # Stop live streaming and any analysis still fetching, so the background
        # threads are not left running while the window goes away.
        try:
            self._spectra.cancel_scan()
        except Exception:
            pass
        try:
            # Live, loads, and a running table export (which writes to a file
            # and must notice the window is gone) — in every tab.
            self._logger_tabs.shutdown()
        except Exception:
            pass
        try:
            cfg = load_config()
            # Maximized: store the underlying normal size, not the screen-filling
            # one, so un-maximizing later still gives a windowed size back.
            g   = self.normalGeometry() if self.isMaximized() else self.geometry()
            cfg["suite_geometry"] = [g.x(), g.y(), g.width(), g.height()]
            save_config(cfg)
        except Exception:
            pass
        super().closeEvent(event)


# ── Entry point ──────────────────────────────────────────────────────────────

def _run():
    _ico = _icon_file()
    _aumid = _icon_app_id(_APP_ID_PREFIX, _ico)
    if _aumid:
        try:
            import ctypes as _ct
            _ct.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    if _ico:
        app.setWindowIcon(QIcon(str(_ico)))   # taskbar + alt-tab
    install_app_look(app)
    win = CPVASuiteWindow()
    win.showMaximized()   # start maximized; the restored size is what un-maximizing gives
    _nudge_taskbar_icon(int(win.winId()))
    sys.exit(app.exec())


if __name__ == "__main__":
    _run()