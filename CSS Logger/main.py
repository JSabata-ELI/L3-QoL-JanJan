"""
CPVA Suite  —  CSS Logger + Spectra in one PySide6 window.
Run:  python main.py   (from L3-QoL-JanJan/CSS Logger/)
"""
import sys
import pathlib

_HERE = pathlib.Path(__file__).parent          # CSS Logger/
_ROOT = _HERE.parent                           # L3-QoL-JanJan/
# cssl.py is in same folder (_HERE) — already on sys.path via __file__ location
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT / "Spectra"))     # for sp_t / SpectraWidget

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
import csv
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

# ── PySide6 ────────────────────────────────────────────────────────────────
from PySide6.QtCore import (
    Qt, QObject, QTimer, Signal, QDate, QLocale, QRect, QSize, QPoint,
    QPointF, QEvent,
)
from PySide6.QtGui import (
    QColor, QIcon, QPalette, QPainter, QPen, QShortcut, QKeySequence,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QListWidget, QListWidgetItem,
    QTabWidget, QSplitter, QScrollArea, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCheckBox, QGroupBox, QFrame, QDialog, QDialogButtonBox,
    QInputDialog, QFileDialog, QMessageBox, QSizePolicy,
    QDoubleSpinBox, QSpinBox, QToolButton, QMenu, QColorDialog,
    QCalendarWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTextEdit, QProgressBar, QLayout,
    QAbstractSpinBox, QAbstractScrollArea, QSlider,
)

# ── matplotlib ─────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("QtAgg")
matplotlib.rcParams["axes.facecolor"]   = "white"
matplotlib.rcParams["figure.facecolor"] = "white"
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.widgets import SpanSelector, RectangleSelector

# ── Non-UI helpers from cpva_core ──────────────────────────────────────────
from cpva_core import (
    TZ_PRAGUE, now_ns, dt_to_ns, ns_to_local_str, _fmt_cursor_value,
    parse_user_datetime, shorten_pv_name, make_pv_query_matcher,
    cpva_fetch_samples, cpva_fetch_samples_chunked, cpva_decode_value,
    cpva_fetch_channels, _chunk_is_night, safe_divide,
    load_config, save_config, load_presets, save_presets,
    load_condition_presets, save_condition_presets,
    load_custom_pvs, save_custom_pvs, load_ramping_repository,
    CONFIG_FILE, PRESETS_FILE, CONDITIONS_PRESETS_FILE, CUSTOM_PVS_FILE,
    CHUNK_SIZE_NS, CPVA_HTTP_TIMEOUT, MASTER_RAMP_PV, SAMPLE_HOLD_MIN_GAP_MS,
    RAMPING_PV_MAP, PV_TO_RAMPING,
    _open_path, _looks_like_image_path, _image_file_size, _resolve_image_path,
    APP_DIR,
)

try:
    from cpva_core import DATA_REPOSITORY_DIR
except ImportError:
    DATA_REPOSITORY_DIR = _ROOT / "Diagnostic" / "DataRepository"

from sp_t import SpectraWidget

# ── Styles ─────────────────────────────────────────────────────────────────
_APP_STYLESHEET = """
QWidget      { background: #f3f3f3; color: #111;
               font-family: "Segoe UI", Arial, sans-serif; }
QLabel       { background: transparent; }
QPushButton  { padding: 5px 8px; border-radius: 3px; border: 1px solid #bbb; }
QPushButton:hover { background: #dde8ff; }
QGroupBox    { border: 1px solid #ccc; border-radius: 4px; }
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
    "axis_gap_px":       6,     # whitespace between a title and the axis left of it
    "label_pad_px":      5,     # gap between a title and its OWN tick numbers
    "outer_margin_px":   8,     # whitespace left of the outermost title
    "show_axis_titles":  True,
    "y_ticks_max":       6,     # upper bound on major Y ticks per axis
    # Minor Y ticks are decoration, but matplotlib builds a full Tick object
    # (2 lines + 2 texts) for every one of them — with a dozen stacked axes that
    # is ~400 objects and by far the largest single cost of a redraw, so they are
    # off by default and can be switched back on in Graph settings.
    "y_minor_ticks":     False,
    # Time axis
    "x_ticks_max":       8,     # upper bound on time stamps across the plot
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
}

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
    binding (entries saved before bindings existed), which stay positional.
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

# ── Signal helpers ─────────────────────────────────────────────────────────

class _LoadSig(QObject):
    done     = Signal(object)
    error    = Signal(str)
    progress = Signal(str)
    pct      = Signal(int)    # 0-100 for progress bar

class _IncSig(QObject):
    done = Signal(object)

class _ChanSig(QObject):
    done  = Signal(list)
    error = Signal(str)


# ── Color swatch delegate ──────────────────────────────────────────────────

class _WheelGuard(QObject):
    """The mouse wheel must never change a number box or a drop-down just
    because the pointer happens to hover over it.

    A field only reacts to the wheel once it has been clicked into (focused);
    otherwise the scroll is handed to the panel underneath, so the toolbar or
    the page scrolls the way the user expects.
    """

    _TARGETS = (QAbstractSpinBox, QComboBox, QSlider)

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.Type.Wheel or not isinstance(obj, self._TARGETS):
            return False
        if obj.hasFocus():
            return False                    # clicked into it -> normal behaviour
        w = obj.parentWidget()
        while w is not None:                # pass the scroll on to the panel
            if isinstance(w, QAbstractScrollArea):
                QApplication.sendEvent(w.viewport(), ev)
                break
            w = w.parentWidget()
        return True


def _install_wheel_guard():
    """Install _WheelGuard once for the whole application."""
    app = QApplication.instance()
    if app is None or getattr(app, "_wheel_guard", None) is not None:
        return
    guard = _WheelGuard(app)
    app._wheel_guard = guard
    app.installEventFilter(guard)


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
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.MouseButtonRelease:
            current = index.data(Qt.ItemDataRole.UserRole) or "#1976D2"
            color = QColorDialog.getColor(QColor(current), None, "Choose color")
            if color.isValid():
                model.setData(index, color.name(), Qt.ItemDataRole.UserRole)
                self.color_changed.emit(index.row(), color.name())
            return True
        return False


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


def _make_calendar(initial=None):
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

    prev_btn.clicked.connect(cal.showPreviousMonth)
    next_btn.clicked.connect(cal.showNextMonth)
    month_btn.clicked.connect(_on_month_btn)
    year_spin.valueChanged.connect(lambda y: cal.setCurrentPage(y, cal.monthShown()))
    cal.currentPageChanged.connect(lambda _y, _m: _update_nav())
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

# …but a *live* window is also re-merged, re-filtered and re-drawn continuously,
# and the config remembers the last From/To, so a multi-day historical window
# silently became the live window on the next start (the saved window grows by a
# day every day it is used). Clamp what Live inherits; the user can still load
# any span historically. See _live_span_from_window.
_LIVE_MAX_INIT_SPAN_S = 12 * 3600

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


def _samples_to_ns_arrays(samples):
    """(ts_ns, value, units) triples → (ns timestamps, values) float64 arrays.

    Fast path: np.fromiter reads the two columns in a single pass with no
    intermediate Python lists — this runs over every sample of every PV on every
    live refresh. A non-numeric value makes numpy raise, and only then is the
    filtering slow path used."""
    n = len(samples)
    if not n:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    try:
        ts   = np.fromiter((s[0] for s in samples), dtype=np.float64, count=n)
        vals = np.fromiter((s[1] for s in samples), dtype=np.float64, count=n)
    except (TypeError, ValueError):
        return _pairs_to_ns_arrays([(t, v) for t, v, _ in samples
                                    if isinstance(v, (int, float))])
    return ts, vals


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


def _moving_avg_np(vals, w: int):
    """Trailing moving average of a float array, same length as the input.
    NaNs only affect the windows they fall into (a cumsum-based version would
    smear a single NaN across the whole rest of the trace)."""
    if w <= 1 or vals.size < w:
        return vals
    ext = np.concatenate((vals[:w - 1][::-1], vals))
    return np.convolve(ext, np.full(w, 1.0 / w), mode="valid")


class TimeWindowDialog(QDialog):
    """CS-Studio-style Start/End time picker.

    Each side (Start, End) has an Absolute tab (calendar + clock) and a Relative
    tab (years…seconds before/after now). The bottom status line mirrors CS Studio
    ("Start Time: -1 hours 0.0 seconds" / "End Time: now").
    """

    def __init__(self, parent, dt_from: datetime, dt_to: datetime):
        super().__init__(parent)
        self.setWindowTitle("Start/End Time")
        self.setMinimumSize(720, 470)
        self.setModal(True)
        self._result_from = dt_from
        self._result_to   = dt_to
        self._sides: dict = {}
        self._build_ui(dt_from, dt_to)
        self._refresh_status()

    # ── construction ──────────────────────────────────────────────────────
    def _build_ui(self, dt_from, dt_to):
        lay = QVBoxLayout(self)
        row = QHBoxLayout(); lay.addLayout(row, stretch=1)

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

        # Status strip
        st_row = QHBoxLayout()
        self._lbl_start_status = QLabel("Start Time:")
        self._lbl_end_status   = QLabel("End Time:")
        for l in (self._lbl_start_status, self._lbl_end_status):
            l.setStyleSheet("color:#1565C0;font-weight:600;")
        st_row.addWidget(self._lbl_start_status, stretch=1)
        st_row.addWidget(self._lbl_end_status,   stretch=1)
        lay.addLayout(st_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        # Default to the Relative view (matches CS Studio: "-1 hours" / "now").
        # Done last so the status labels already exist when currentChanged fires.
        self._sides["from"]["tabs"].setCurrentIndex(1)
        self._sides["to"]["tabs"].setCurrentIndex(1)

    def _build_side(self, which, init_dt, rel_values, is_start):
        tabs = QTabWidget()
        abs_lbl = "Absolute Start" if is_start else "Absolute End"
        rel_lbl = "Relative Start" if is_start else "Relative End"

        # ── Absolute tab ──
        abs_w = QWidget(); abs_lay = QVBoxLayout(abs_w)
        q_init = QDate(init_dt.year, init_dt.month, init_dt.day)
        frame, cal = _make_calendar(q_init)
        # Make clicks register visually (highlight) — the weekend delegate paints
        # selection from its own list, so we must feed it on every click.
        def _on_click(d, c=cal):
            deleg = getattr(c, "_wk_delegate", None)
            if deleg is not None: deleg.set_selected([d])
            c.setSelectedDate(d)
            self._refresh_status()
        cal.clicked.connect(_on_click)
        deleg = getattr(cal, "_wk_delegate", None)
        if deleg is not None: deleg.set_selected([q_init])
        abs_lay.addWidget(frame)

        t_row = QHBoxLayout(); t_row.addWidget(QLabel("Time:"))
        h = QSpinBox(); h.setRange(0, 23); h.setValue(init_dt.hour); h.setFixedWidth(52)
        m = QSpinBox(); m.setRange(0, 59); m.setValue(init_dt.minute); m.setFixedWidth(52)
        s = QSpinBox(); s.setRange(0, 59); s.setValue(init_dt.second); s.setFixedWidth(52)
        for sp in (h, m, s): sp.valueChanged.connect(self._refresh_status)
        t_row.addWidget(h); t_row.addWidget(QLabel(":")); t_row.addWidget(m)
        t_row.addWidget(QLabel(":")); t_row.addWidget(s); t_row.addStretch()
        abs_lay.addLayout(t_row)

        q_row = QHBoxLayout()
        for lbl in ("Now", "00:00", "12:00"):
            b = QPushButton(lbl); b.setFixedWidth(56)
            b.clicked.connect(self._mk_abs_quick(which, lbl))
            q_row.addWidget(b)
        q_row.addStretch()
        abs_lay.addLayout(q_row)
        tabs.addTab(abs_w, abs_lbl)

        # ── Relative tab ──
        rel_w = QWidget(); rel_lay = QVBoxLayout(rel_w)
        grid = QGridLayout()
        specs = [("Years", 0, 0), ("Hours", 0, 2), ("Months", 1, 0),
                 ("Minutes", 1, 2), ("Days", 2, 0), ("Secs", 2, 2)]
        rel_spins = {}
        for name, r, c in specs:
            sp = QSpinBox(); sp.setRange(0, 9999); sp.setFixedWidth(70)
            sp.valueChanged.connect(self._refresh_status)
            grid.addWidget(QLabel(name + ":"), r, c)
            grid.addWidget(sp, r, c + 1)
            rel_spins[name] = sp
        for name, val in (rel_values or {}).items():
            if name in rel_spins:
                rel_spins[name].setValue(val)
        rel_lay.addLayout(grid)

        qr = QHBoxLayout()
        for lbl, hrs in [("12 h", 12), ("1 Day", 24), ("3 Days", 72), ("7 Days", 168)]:
            b = QPushButton(lbl); b.setFixedWidth(58)
            b.clicked.connect(self._mk_rel_quick(which, hrs))
            qr.addWidget(b)
        rel_lay.addLayout(qr)

        br = QHBoxLayout()
        before = QCheckBox("Before…"); before.setChecked(True)
        before.setStyleSheet(_CHK_STYLE)
        before.toggled.connect(self._refresh_status)
        now_b = QPushButton("Now"); now_b.setFixedWidth(56)
        now_b.clicked.connect(self._mk_rel_now(which))
        br.addWidget(before); br.addStretch(); br.addWidget(now_b)
        rel_lay.addLayout(br)
        rel_lay.addStretch()
        tabs.addTab(rel_w, rel_lbl)

        tabs.currentChanged.connect(self._refresh_status)

        self._sides[which] = {
            "tabs": tabs, "cal": cal, "h": h, "m": m, "s": s,
            "rel": rel_spins, "before": before,
        }
        return tabs

    # ── quick-button factories ────────────────────────────────────────────
    def _mk_abs_quick(self, which, kind):
        def _cb():
            sd = self._sides[which]
            if kind == "Now":
                t = datetime.now()
                sd["cal"].setSelectedDate(QDate(t.year, t.month, t.day))
                deleg = getattr(sd["cal"], "_wk_delegate", None)
                if deleg is not None: deleg.set_selected([QDate(t.year, t.month, t.day)])
                sd["h"].setValue(t.hour); sd["m"].setValue(t.minute); sd["s"].setValue(t.second)
            elif kind == "00:00":
                sd["h"].setValue(0); sd["m"].setValue(0); sd["s"].setValue(0)
            elif kind == "12:00":
                sd["h"].setValue(12); sd["m"].setValue(0); sd["s"].setValue(0)
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

    def _mk_rel_now(self, which):
        def _cb():
            sd = self._sides[which]
            for sp in sd["rel"].values(): sp.setValue(0)
            self._refresh_status()
        return _cb

    # ── computation ───────────────────────────────────────────────────────
    def _compute(self, which):
        sd = self._sides[which]
        if sd["tabs"].currentIndex() == 0:          # Absolute
            d = sd["cal"].selectedDate()
            return datetime(d.year(), d.month(), d.day(),
                            sd["h"].value(), sd["m"].value(), sd["s"].value())
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
        if len(self._sides) < 2:        # both sides must exist (guards construction)
            return
        f = self._compute("from"); t = self._compute("to")
        f_txt = (self._rel_text("from") if self._sides["from"]["tabs"].currentIndex() == 1
                 else f.strftime("%Y-%m-%d %H:%M:%S"))
        t_txt = (self._rel_text("to") if self._sides["to"]["tabs"].currentIndex() == 1
                 else t.strftime("%Y-%m-%d %H:%M:%S"))
        self._lbl_start_status.setText(f"Start Time:  {f_txt}")
        self._lbl_end_status.setText(f"End Time:  {t_txt}")

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
        else:
            self._kick_load()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Search for a CPVA channel — several words match in order, "
            "like *word1*word2*:"))
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
        sig.error.connect(lambda e: self._lbl_status.setText(f"Load failed: {e}"))
        def _work():
            try:
                channels = cpva_fetch_channels(timeout=self._timeout)
                sig.done.emit(channels)
            except Exception as exc:
                sig.error.emit(str(exc))
        threading.Thread(target=_work, daemon=True).start()

    def _on_loaded(self, channels):
        global _pv_channel_cache
        _pv_channel_cache = channels
        self._all_channels = channels
        self._lbl_status.setText(
            f"{len(channels)} channels loaded. Type to filter."
            if channels else "CPVA returned no channels.")
        self._filter(self._edit.text())

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

# ── Flow layout (wraps children to the next row, never overflows) ───────────

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
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x, y = rect.x(), rect.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


# ── Floating graph window (F11 fullscreen / Ctrl+F11 windowed) ──────────────

class _GraphPopupWindow(QWidget):
    """Top-level window that hosts the Graph tab when popped out. Closing it (or
    pressing Esc / F11) hands the tab back to the notebook instead of destroying
    it."""

    def __init__(self, owner):
        super().__init__(None)
        self._owner = owner
        self.setWindowTitle("CSS Logger — Graph")

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

    def closeEvent(self, ev):
        # Never lose the tab — reparent it back into the notebook.
        ev.ignore()
        self._owner._restore_graph_from_popup()


# ── CSSLoggerWidget ────────────────────────────────────────────────────────

class CSSLoggerWidget(QWidget):
    """Full PySide6 port of CPVAExplorerApp from cssl.py."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_state()
        self._build_ui()
        self._populate_ui()
        self._install_graph_shortcuts()
        # Graph tab defaults to live mode — start it shortly after the window shows
        # (deferred so the UI is fully realised and the Operation preset PVs are loaded).
        QTimer.singleShot(600, self._maybe_autostart_live)

    def _maybe_autostart_live(self):
        if self._live_mode:
            return
        pvs = self._real_pv_names()
        if pvs:
            self._toggle_live_mode()

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
        self._samples_by_pv: dict = {}
        self._table_rows:    list = []
        self._pv_order:      list = []
        self._base_pv_order: list = []
        self._last_pv_search: str = ""
        self._col_full_names: dict = {}
        self._presets:           list = load_presets()
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
        self._zoom_history:  list = []
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
        self._xy_zoom_history: list = []
        self._xy_choice_map: dict = {}
        # Single banded graph: every PV lives in its own vertical band of one
        # shared plot (CS-Studio style). A PV with Autoscale on spans full height.
        self._pv_time_canvas = None
        self._pv_time_figure = None
        self._pv_time_df     = None
        self._pv_time_condition_rows: list = []
        self._pv_time_columns = [
            "waveplate", "ptm1", "pcm2", "pcm4", "pap1", "sbw4",
            "green", "sbw4_green", "green_ptm1", "ptm1_pap1",
            "pcm2_green", "sbw4_ptm1",
        ]
        self._pv_settings: dict = {}
        self._live_mode     = False
        self._live_last_ts  = None
        self._live_window_span = None
        self._live_last_rebuild_ns = 0
        self._live_rebuild_cost_ns = 0   # measured cost of one live refresh
        self._live_autoscroll  = True
        self._live_programmatic_scroll = False
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
        self._conditions: list = copy(self.config.get("conditions", []))
        self._cond_last_diag = None   # de-dupes the per-refresh conditions log
        self._table_rows_unfiltered: list = []
        self._numeric_pvs: set = set()
        self._master_pv: str       = self.config.get("master_pv", MASTER_RAMP_PV)
        self._master_multiple: str = self.config.get("master_multiple", "")
        # Qt string vars (set by sidebar widgets after _build_ui)
        self._master_pv_edit        = None
        self._master_multiple_edit  = None
        now = datetime.now()
        self._dt_from = now - timedelta(hours=1)
        self._dt_to   = now
        self._font_size: int = 11
        self._axis_tv_cols = ("show", "pv", "display_name", "color", "cursor_val",
                               "ymin", "ymax", "auto_scale", "width", "smooth", "grid",
                               "blank")
        self._axis_last_clicked_row = -1
        self._axis_row_pv: list = []     # table row → PV name (None for divider rows)
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
        self._sidebar_widget = QWidget()
        sidebar_scroll.setWidget(self._sidebar_widget)
        splitter.addWidget(sidebar_scroll)

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

        self._lbl_status = QLabel("Ready.")
        self._lbl_status.setStyleSheet("color:#777;padding:2px 6px;")
        root_lay.addWidget(self._lbl_status)

    # ── Sidebar ────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        bar = QVBoxLayout(self._sidebar_widget)
        bar.setContentsMargins(6, 6, 6, 6)
        bar.setSpacing(4)

        h = QLabel("TIME WINDOW")
        h.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h)

        btn_tw = QPushButton("\U0001f4c5 Set time window")
        btn_tw.clicked.connect(self._open_time_window_dialog)
        bar.addWidget(btn_tw)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(sep1)

        self._lbl_tw_from = QLabel("From: --")
        self._lbl_tw_from.setStyleSheet("color:#555;font-size:11px;")
        self._lbl_tw_to   = QLabel("To:   --")
        self._lbl_tw_to.setStyleSheet("color:#555;font-size:11px;")
        self._lbl_tw_live = QLabel("Live: OFF")
        self._lbl_tw_live.setStyleSheet("color:#555;font-size:11px;")
        bar.addWidget(self._lbl_tw_from)
        bar.addWidget(self._lbl_tw_to)
        bar.addWidget(self._lbl_tw_live)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(sep2)

        h2 = QLabel("PRESETS")
        h2.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h2)

        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preset_combo.currentIndexChanged.connect(self._load_preset)
        btn_del_preset = QPushButton("✕")
        btn_del_preset.setFixedWidth(28)
        btn_del_preset.setStyleSheet(
            "QPushButton{background:#B71C1C;color:white;border-radius:3px;padding:3px 4px;}"
            "QPushButton:hover{background:#7F0000;}")
        btn_del_preset.clicked.connect(self._delete_preset)
        preset_row.addWidget(self._preset_combo)
        preset_row.addWidget(btn_del_preset)
        bar.addLayout(preset_row)

        preset_btns = QHBoxLayout()
        b_load = QPushButton("Load"); b_load.setStyleSheet(_BTN_PRIMARY)
        b_load.clicked.connect(self._load_preset)
        b_save = QPushButton("Save"); b_save.clicked.connect(self._save_preset)
        b_new  = QPushButton("Save as new…"); b_new.clicked.connect(self._save_preset_as)
        preset_btns.addWidget(b_load); preset_btns.addWidget(b_save); preset_btns.addWidget(b_new)
        bar.addLayout(preset_btns)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(sep3)

        h3 = QLabel("PV LIST")
        h3.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h3)

        self._pv_list = QListWidget()
        self._pv_list.setStyleSheet(
            "QListWidget{background:#ffffff;border:1px solid #b0b0b0;border-radius:3px;}")
        self._pv_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._pv_list.setMinimumHeight(120)
        self._pv_list.setMaximumHeight(220)
        self._pv_list.itemDoubleClicked.connect(self._on_pv_double_click)
        bar.addWidget(self._pv_list)

        pv_btns = QHBoxLayout()
        self._btn_browse = QPushButton("Browse...")
        self._btn_browse.setStyleSheet(_BTN_SUCCESS)
        self._btn_browse.clicked.connect(self._open_pv_browser)
        btn_remove = QPushButton("✕ Remove")
        btn_remove.clicked.connect(self._remove_selected_pvs)
        btn_clear  = QPushButton("\U0001f5d1")
        btn_clear.setFixedWidth(32)
        btn_clear.clicked.connect(self._clear_pv_list)
        pv_btns.addWidget(self._btn_browse)
        pv_btns.addWidget(btn_remove)
        pv_btns.addWidget(btn_clear)
        bar.addLayout(pv_btns)

        self._lbl_pv_count = QLabel("0 PVs")
        self._lbl_pv_count.setStyleSheet("color:#777;font-size:10px;")
        bar.addWidget(self._lbl_pv_count)

        self._btn_load = QPushButton("LOAD DATA")
        self._btn_load.setStyleSheet(
            "QPushButton{background:#2E7D32;color:white;font-weight:700;"
            "padding:10px;border-radius:4px;font-size:13px;border:none;}"
            "QPushButton:hover{background:#1B5E20;}"
            "QPushButton:disabled{background:#bbb;color:#888;}")
        self._btn_load.clicked.connect(self._on_load_clicked)
        bar.addWidget(self._btn_load)

        live_row = QHBoxLayout()
        self._btn_live = QPushButton("⏵ Live")
        self._btn_live.clicked.connect(self._toggle_live_mode)
        self._btn_live.setToolTip("Toggle live mode — polls for new values every 300 ms")
        live_row.addWidget(self._btn_live)
        live_row.addStretch()
        bar.addLayout(live_row)

        sep_m = QFrame(); sep_m.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(sep_m)

        h_m = QLabel("MASTER PV / FILTER")
        h_m.setStyleSheet("font-weight:700;color:#1565C0;")
        bar.addWidget(h_m)

        mpv_row = QHBoxLayout()
        mpv_row.addWidget(QLabel("Master PV:"))
        self._master_pv_edit = QLineEdit(self._master_pv)
        self._master_pv_edit.setPlaceholderText(MASTER_RAMP_PV)
        self._master_pv_edit.setToolTip("Ramp master PV — used for row deduplication")
        mpv_row.addWidget(self._master_pv_edit)
        bar.addLayout(mpv_row)

        mm_row = QHBoxLayout()
        mm_row.addWidget(QLabel("Keep multiples of:"))
        self._master_multiple_edit = QLineEdit(self._master_multiple)
        self._master_multiple_edit.setFixedWidth(70)
        self._master_multiple_edit.setPlaceholderText("e.g. 5")
        self._master_multiple_edit.setToolTip("Keep only rows where master PV is a multiple of this value")
        mm_row.addWidget(self._master_multiple_edit)
        mm_row.addStretch()
        bar.addLayout(mm_row)

        # ── Progress bar ───────────────────────────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v %")
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #bbb;border-radius:3px;"
            "background:#eee;text-align:center;font-size:10px;}"
            "QProgressBar::chunk{background:#2E7D32;border-radius:2px;}")
        self._progress_bar.hide()
        bar.addWidget(self._progress_bar)

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

        # Controls row (wrapped in a container so it can be hidden in the
        # fullscreen / windowed "graph only" pop-out).
        self._graph_ctrl_bar = QWidget()
        ctrl = QHBoxLayout(self._graph_ctrl_bar)
        ctrl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._graph_ctrl_bar)

        b_clean = QPushButton("Clean graph"); b_clean.clicked.connect(self._clean_graph); ctrl.addWidget(b_clean)
        b_save  = QPushButton("Save graph"); b_save.clicked.connect(self._save_graph); ctrl.addWidget(b_save)
        self._btn_zoom_back = QPushButton("Back")
        self._btn_zoom_back.clicked.connect(self._zoom_back)
        self._btn_zoom_back.setEnabled(False)
        ctrl.addWidget(self._btn_zoom_back)
        b_ref  = QPushButton("Reference lines"); b_ref.clicked.connect(self._open_ref_lines_dialog); ctrl.addWidget(b_ref)
        b_cond = QPushButton("Conditions"); b_cond.clicked.connect(self._open_conditions_dialog); ctrl.addWidget(b_cond)
        b_cpv  = QPushButton("Add custom PV"); b_cpv.clicked.connect(self._open_custom_pv_dialog); ctrl.addWidget(b_cpv)
        b_gset = QPushButton("Graph settings")
        b_gset.setToolTip("Fonts, axis-column spacing, time stamps, plot margins, "
                          "cursor readouts")
        b_gset.clicked.connect(self._open_graph_settings_dialog); ctrl.addWidget(b_gset)

        ctrl.addWidget(QLabel("Font:"))
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(5, 24)
        self._font_size_spin.setValue(int(self._graph_opts.get("font_size", 11)))
        self._font_size_spin.setFixedWidth(48)
        self._font_size_spin.valueChanged.connect(self._apply_font_size)
        ctrl.addWidget(self._font_size_spin)
        ctrl.addWidget(QLabel("pt"))

        # Averaging / downsampling: bin each trace to at most N points per PV so
        # long windows stay responsive. 0 = off (plot raw, capped only by a
        # safety limit). Kept client-side until a server-side option exists.
        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("Avg to:"))
        self._avg_target_spin = QSpinBox()
        self._avg_target_spin.setRange(0, 200000)
        self._avg_target_spin.setValue(int(self.config.get("avg_target_points", 2000)))
        self._avg_target_spin.setSingleStep(500)
        self._avg_target_spin.setFixedWidth(78)
        self._avg_target_spin.setToolTip(
            "Target points per PV. >0 fetches server-side DECIMATED data "
            "(fast, ~this many points, like CS Studio 'Optimized'); the trace is "
            "then trimmed to exactly this many by time-binned mean. "
            "0 = fetch RAW samples (slow for long windows). Applies on next Load.")
        self._avg_target_spin.valueChanged.connect(self._on_avg_target_changed)
        ctrl.addWidget(self._avg_target_spin)
        ctrl.addWidget(QLabel("pts"))
        ctrl.addStretch()
        self._lbl_graph_info = QLabel("")
        self._lbl_graph_info.setStyleSheet("color:#777;")
        ctrl.addWidget(self._lbl_graph_info)

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
        self._stats_title = QLabel("")
        self._stats_title.setStyleSheet(
            "font-size:10px;font-weight:700;color:#1565C0;padding:2px 6px 0 6px;")
        self._stats_title.hide()
        top_lay.addWidget(self._stats_title)

        self._stats_scroll = QScrollArea()
        self._stats_scroll.setWidgetResizable(True)
        self._stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._stats_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._stats_scroll.setMaximumHeight(150)
        self._stats_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._stats_container = QWidget()
        self._stats_flow = _FlowLayout(self._stats_container, margin=4, spacing=6)
        self._stats_scroll.setWidget(self._stats_container)
        self._stats_scroll.hide()
        top_lay.addWidget(self._stats_scroll)

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
        self._graph_v_splitter.setSizes([900, 190])

    # ── Axis settings panel ────────────────────────────────────────────────

    def _build_axis_settings_panel(self, parent: QWidget):
        lay = QVBoxLayout(parent)
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(2)

        hdr = QHBoxLayout()
        h = QLabel("Axis settings")
        h.setStyleSheet("font-weight:700;color:#1565C0;")
        self._axis_hdr_label = h
        hdr.addWidget(h)
        hdr.addStretch()
        lay.addLayout(hdr)

        _COLS   = list(self._axis_tv_cols)
        _HEADS  = {
            "show":         "Show", "pv": "PV", "display_name": "Display Name",
            "color":        "Color", "cursor_val": "Cursor",
            "ymin": "Y min", "ymax": "Y max",
            "auto_scale":   "Autoscale", "width": "Width", "smooth": "Smooth", "grid": "Grid",
            "blank":        "",
        }
        _COL_W  = {
            "show":40, "pv":270, "display_name":130, "color":40,
            "cursor_val":90, "ymin":55, "ymax":55,
            "auto_scale":70, "width":45, "smooth":50, "grid":35,
            "blank":0,
        }

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
        hdr = self._axis_tv.horizontalHeader()
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
            self._axis_tv.setColumnWidth(i, _COL_W.get(col, 60))

        # Tick boxes sit in the middle of the Show / Autoscale / Grid columns.
        self._check_delegate = _CenteredCheckDelegate(self._axis_tv)
        for _c in ("show", "auto_scale", "grid"):
            if _c in _COLS:
                self._axis_tv.setItemDelegateForColumn(_COLS.index(_c),
                                                       self._check_delegate)

        # Color swatch delegate on column 3
        self._color_delegate = _ColorSwatchDelegate(self._axis_tv)
        self._color_delegate.color_changed.connect(self._on_axis_color_changed)
        self._axis_tv.setItemDelegateForColumn(3, self._color_delegate)

        self._axis_tv.cellDoubleClicked.connect(self._on_axis_tv_double_click)
        # Second click on an already-selected row clears the highlight (toggle).
        self._axis_tv.clicked.connect(self._on_axis_tv_clicked)
        # Auto-apply: any edit takes effect immediately (no Apply button).
        self._axis_tv.itemChanged.connect(self._on_axis_item_changed)
        lay.addWidget(self._axis_tv)

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
        self._btn_xy_back = QPushButton("↩ XY Back"); self._btn_xy_back.clicked.connect(self._xy_zoom_back); ctrl.addWidget(self._btn_xy_back)
        b_cpv   = QPushButton("Add custom PV"); b_cpv.clicked.connect(self._open_custom_pv_dialog); ctrl.addWidget(b_cpv)
        ctrl.addStretch()
        lay.addLayout(ctrl)

        self._xy_canvas_container = QWidget()
        self._xy_canvas_container.setStyleSheet("background:#f5f5f5;")
        xy_inner = QVBoxLayout(self._xy_canvas_container)
        xy_inner.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._xy_canvas_container, stretch=1)

        self._lbl_xy_info = QLabel("Load data first, then choose X and Y variables.")
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
        self._pv_time_y_combo.addItems(self._pv_time_columns)
        ctrl.addWidget(self._pv_time_y_combo)

        ctrl.addWidget(QLabel("From:"))
        self._pv_time_from_edit = QLineEdit("2026-01-01"); self._pv_time_from_edit.setFixedWidth(90)
        ctrl.addWidget(self._pv_time_from_edit)
        ctrl.addWidget(QLabel("To:"))
        self._pv_time_to_edit = QLineEdit(datetime.now().strftime("%Y-%m-%d"))
        self._pv_time_to_edit.setFixedWidth(90)
        ctrl.addWidget(self._pv_time_to_edit)

        self._pv_time_mode_combo = QComboBox()
        self._pv_time_mode_combo.addItems(["Daily distribution", "Raw shots"])
        ctrl.addWidget(self._pv_time_mode_combo)

        b_plot = QPushButton("Plot"); b_plot.setStyleSheet(_BTN_SUCCESS)
        b_plot.clicked.connect(self._plot_pv_time); ctrl.addWidget(b_plot)
        b_add_cond = QPushButton("+ Condition"); b_add_cond.clicked.connect(self._pv_time_add_condition_row)
        ctrl.addWidget(b_add_cond)
        ctrl.addStretch()
        lay.addLayout(ctrl)

        self._pv_time_canvas_container = QWidget()
        self._pv_time_canvas_container.setStyleSheet("background:#f5f5f5;")
        pv_inner = QVBoxLayout(self._pv_time_canvas_container)
        pv_inner.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._pv_time_canvas_container, stretch=1)

        cond_grp = QGroupBox("Conditions")
        cond_grp.setStyleSheet("QGroupBox{font-weight:700;padding-top:14px;margin-top:8px;"
                                "border:1px solid #ccc;border-radius:4px;}"
                                "QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;}")
        self._pv_time_cond_layout = QVBoxLayout(cond_grp)
        lay.addWidget(cond_grp)
        self._pv_time_add_condition_row()

    def _pv_time_add_condition_row(self):
        row_w = QWidget()
        row_lay = QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 0, 0, 0)
        chk = QCheckBox(); chk.setChecked(True)
        var_combo = QComboBox(); var_combo.addItems(self._pv_time_columns)
        target_edit = QLineEdit("70"); target_edit.setFixedWidth(60)
        tol_edit    = QLineEdit("10"); tol_edit.setFixedWidth(60)
        row_lay.addWidget(chk)
        row_lay.addWidget(var_combo)
        row_lay.addWidget(QLabel("target ±")); row_lay.addWidget(target_edit)
        row_lay.addWidget(QLabel("%")); row_lay.addWidget(tol_edit)
        row_lay.addStretch()
        self._pv_time_cond_layout.addWidget(row_w)
        self._pv_time_condition_rows.append({
            "widget": row_w, "enabled": chk,
            "variable": var_combo, "target": target_edit, "tolerance": tol_edit,
        })

    # ── Table tab ───────────────────────────────────────────────────────────

    def _build_table_tab(self):
        tab = self._tab_table
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)

        self._table_widget = QTableWidget(0, 1)
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
        b_cpv  = QPushButton("Add custom PV"); b_cpv.clicked.connect(self._open_custom_pv_dialog)
        self._btn_export = QPushButton("Export CSV…"); self._btn_export.clicked.connect(self._export_csv)
        info_row.addWidget(b_cond); info_row.addWidget(b_cpv); info_row.addWidget(self._btn_export)
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
        b_clear = QPushButton("🗑 Clear log"); b_clear.clicked.connect(self._clear_log)
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
        if _auto_idx < 0 and self._presets:
            _auto_idx = 0

        if _auto_idx >= 0:
            # Block signal so currentIndexChanged doesn't trigger _load_preset here
            self._preset_combo.blockSignals(True)
            self._preset_combo.setCurrentIndex(_auto_idx)
            self._preset_combo.blockSignals(False)
            p = self._presets[_auto_idx]
            for pv in p.get("pvs", []):
                self._pv_list.addItem(pv)
            # Also apply master PV/multiple from preset if saved
            if p.get("master_pv") and self._master_pv_edit is not None:
                self._master_pv_edit.setText(p["master_pv"])
            if "master_multiple" in p and self._master_multiple_edit is not None:
                self._master_multiple_edit.setText(str(p["master_multiple"]))
            # Do NOT override _dt_from / _dt_to — keep "last hour"
        else:
            # Fallback: restore last PV list from config
            for pv in self.config.get("pv_list", []):
                self._pv_list.addItem(pv)

        self._migrate_custom_pv_bindings()  # pre-bindings entries -> explicit PVs
        self._sync_pv_list_customs()   # show defined custom PVs alongside the real ones
        self._update_pv_count()
        self._refresh_time_labels()

    def _refresh_time_labels(self):
        if self._live_mode and self._live_window_span:
            # Live plots a window that rolls with "now", and it is not always the
            # From/To it was started from (a too-wide window is clamped — see
            # _live_span_from_window), so show the span that is really on screen
            # instead of two timestamps that would both be wrong a minute later.
            hours = self._live_window_span.total_seconds() / 3600.0
            span = f"{hours:.0f} h" if hours >= 1 else \
                   f"{self._live_window_span.total_seconds():.0f} s"
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
        if autoscale:
            f_bot, f_top = 0.02, 0.98
        else:
            band_h = 1.0 / max(n, 1)
            pad_f  = min(0.45, max(0.0, float(
                self._graph_opts.get("band_pad_frac", 0.12))))
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

    def _plot_graph(self):
        """Safe wrapper: a plotting failure must never crash the app or leave a
        stale graph — clear the canvas and show a message instead."""
        # A direct plot supersedes any pending coalesced one.
        self._replot_timer.stop()
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

    def _plot_graph_impl(self):
        # Always start from a clean canvas so an early return never leaves a
        # stale graph sitting under a "no data" message.
        self._clear_graph()

        if not self._samples_by_pv:
            self._lbl_graph_info.setText("Load data first.")
            return

        # Carry-forward window bounds (ns) — used to draw the last known value
        # across any gaps at the window edges (see per-PV pairs building below).
        # Live mode supplies an explicit window via _plot_window_ns so it need
        # not clobber the user's chosen From/To.
        _win_override = getattr(self, "_plot_window_ns", None)
        if _win_override:
            _win_start_ns, _win_end_ns = _win_override
            _use_window = _win_end_ns > _win_start_ns
        else:
            _win_start_ns = dt_to_ns(self._dt_from)
            _win_end_ns   = dt_to_ns(self._dt_to)
            _use_window   = self._dt_to > self._dt_from

        def _pre_numeric(pv):
            p = self._pre_window_vals.get(pv)
            return p is not None and isinstance(p[1], (int, float))

        # PVs with actual numeric samples (or a numeric last-known value before
        # the window, so a fully-empty window still shows the held line).
        numeric_pvs = [
            pv for pv in self._pv_order
            if self._pv_settings.get(pv, {}).get("show", True)
            and (any(isinstance(v, (int, float)) for _, v, _ in self._samples_by_pv.get(pv, []))
                 or _pre_numeric(pv))
        ]

        # All visible PVs (for axis layout — includes PVs with no data yet)
        visible_pvs = [
            pv for pv in self._pv_order
            if self._pv_settings.get(pv, {}).get("show", True)
        ]

        # Use numeric PVs for axes when data exists, else all visible PVs (empty axes)
        pvs_for_axes = numeric_pvs if numeric_pvs else visible_pvs

        if not pvs_for_axes:
            self._lbl_graph_info.setText("No PVs loaded.")
            self._refresh_axis_settings_tv()
            return

        no_data = len(numeric_pvs) == 0

        _dpi = 96
        _cw  = max(self._graph_container.width()  - 8, 960)
        _ch  = max(self._graph_container.height() - 8, 480)

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
        _GAP_PX      = max(0.0, float(_opts.get("axis_gap_px", 6)))
        # May be negative: the rotated tick numbers keep a couple of pixels of
        # empty margin around their digits, so a small negative value moves the
        # title closer without touching any ink.
        _MIN_PAD_PX  = max(-8.0, float(_opts.get("label_pad_px", 5)))
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
        _BOTTOM = min(_TOP - 0.05, max(0.02, float(_opts.get("margin_bottom", 0.12))))
        _plot_h_px    = max(1.0, _ch * (_TOP - _BOTTOM))
        _ylbl_ext_px  = 7 * 0.6 * _ticksize * _PX_PER_PT   # ≈ "−1.234M" rotated
        _tick_cap     = max(2, int(_opts.get("y_ticks_max", 6)))
        _n_yticks     = max(2, min(_tick_cap, int(_plot_h_px / (_ylbl_ext_px * 1.3))))

        # Space left of the OUTERMOST column: exactly what its own title needs
        # plus a small margin (a fixed 4 % of the figure used to leave a wide
        # unused band there and stole plot width).
        BASE_R = max(0.0, min(0.3, float(_opts.get("margin_right", 0.015))))
        BASE_L = max(0.005, (_col_need_px
                             + max(0.0, float(_opts.get("outer_margin_px", 8)))) / _fig_w_px)
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

        # Collect every PV's samples in ONE pass over the (possibly 100k-row)
        # table instead of re-scanning the whole table once per PV — with a dozen
        # PVs that scan was the bulk of a replot. The result is cached against the
        # table's identity, so a replot triggered by an appearance change (font,
        # spacing, Y limits, Show) reuses it instead of walking the table again.
        _rows  = self._table_rows
        _token = (len(_rows), _rows[-1][0] if _rows else 0, tuple(pvs_for_axes))
        _cached = getattr(self, "_pairs_cache", None)
        if _cached is not None and _cached[0] == _token:
            _pairs_by_pv = _cached[1]
        else:
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

        _tmin_num = _tmax_num = None
        _n_times  = 0
        _markers  = bool(_opts.get("line_markers", True))
        self._graph_lines = []; self._graph_pvs = list(pvs_for_axes); self._graph_raw = []

        avg_target = self._avg_target_points()
        for i, (pv, ax) in enumerate(zip(pvs_for_axes, axes)):
            # Everything from here on is numpy: binning and the window edges over
            # 100k+ samples per PV in Python lists was a large part of a replot.
            ts_a, values = _pairs_to_ns_arrays(_pairs_by_pv.get(pv, []))

            # Downsample: time-binned mean to the user's target (keeps the trace
            # shape while cutting point count). 0 = off → only a hard safety cap.
            if avg_target > 0:
                ts_a, values = _downsample_arrays_mean(ts_a, values, avg_target)
            else:
                MAX_PTS = 25000
                if ts_a.size > MAX_PTS:
                    step = max(1, ts_a.size // MAX_PTS)
                    ts_a = ts_a[::step]; values = values[::step]

            # Carry-forward / hold (like CS Studio): fill gaps at the window
            # edges so the trace spans the whole window instead of breaking off.
            # • Prepend the last value seen BEFORE the window at the start edge.
            # • Extend the last in-window value flat out to the window end.
            if _use_window:
                pre = self._pre_window_vals.get(pv)
                pre_val = pre[1] if (pre and isinstance(pre[1], (int, float))) else None
                if not ts_a.size:
                    if pre_val is not None:
                        ts_a   = np.array([_win_start_ns, _win_end_ns], dtype=np.float64)
                        values = np.array([pre_val, pre_val], dtype=np.float64)
                else:
                    if pre_val is not None and ts_a[0] > _win_start_ns:
                        ts_a   = np.concatenate(([_win_start_ns], ts_a))
                        values = np.concatenate(([pre_val], values))
                    if ts_a[-1] < _win_end_ns:
                        ts_a   = np.concatenate((ts_a, [_win_end_ns]))
                        values = np.concatenate((values, values[-1:]))

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

            if eff_smooth > 1 and values.size >= eff_smooth:
                lw_raw = (line_width * 0.5) if line_width is not None else 0.8
                raw_line, = ax.plot(times_num, values, color=line_color, linewidth=lw_raw,
                                    alpha=0.3, drawstyle="steps-post", zorder=1)
                raw_line.set_visible(visible)
                smoothed = _moving_avg_np(values, eff_smooth)
                lw_sm = line_width if line_width is not None else 1.8
                sm_line, = ax.plot(times_num, smoothed, color=line_color, linewidth=lw_sm,
                                   drawstyle="steps-post",
                                   label=f"{disp_name} (avg {eff_smooth})", zorder=2)
                sm_line.set_visible(visible)
                self._graph_lines.append([raw_line, sm_line])
            else:
                lw = line_width if line_width is not None else 1.2
                line, = ax.plot(times_num, values, color=line_color, linewidth=lw,
                                marker="." if (_markers and times_num.size < 200) else None,
                                markersize=3,
                                drawstyle="steps-post", label=disp_name, zorder=2)
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
                dmin = float(np.nanmin(values)); dmax = float(np.nanmax(values))
            else:
                dmin, dmax = 0.0, 1.0
            autosc  = pv_setting.get("auto_scale", False)
            if not autosc:
                if ymin_pv is not None: dmin = ymin_pv
                if ymax_pv is not None: dmax = ymax_pv
            lo, hi = self._band_ylim(i, n, dmin, dmax, autosc)
            ax.set_ylim(lo, hi)

        # This is a single shared plot (one X axis, stacked Y bands), so a grid
        # can only line up with ONE Y scale — a per-PV grid is meaningless here.
        # Treat the Grid column as a global toggle: draw the shared grid if ANY
        # visible PV has Grid ticked (previously only the FIRST PV's checkbox was
        # read, so ticking other rows appeared to do nothing).
        show_grid = any(
            self._pv_settings.get(pv, {}).get("grid", False) for pv in pvs_for_axes)
        axes[0].grid(show_grid, which="major", alpha=0.4)

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

        from matplotlib.ticker import FuncFormatter, AutoMinorLocator, FixedLocator

        t_min_local = t_min.astimezone(TZ_PRAGUE) if total_seconds > 0 else datetime.now(TZ_PRAGUE)
        t_max_local = t_max.astimezone(TZ_PRAGUE) if total_seconds > 0 else t_min_local
        same_day    = t_min_local.date() == t_max_local.date()

        if total_seconds > 0:
            _ticks, _minor_ticks = self._compute_x_ticks(
                t_min.astimezone(TZ_PRAGUE), t_max.astimezone(TZ_PRAGUE))
        else:
            _ticks, _minor_ticks = [], []
        if _ticks:
            ax0.xaxis.set_major_locator(FixedLocator(_ticks))
        else:
            ax0.xaxis.set_major_locator(mdates.AutoDateLocator(tz=TZ_PRAGUE, minticks=5, maxticks=8))

        # Clock format of the time stamps: "auto" keeps seconds, the other two
        # are the user's fixed choice (Graph settings → Time axis).
        _xfmt   = str(_opts.get("x_time_format", "auto"))
        _clock  = "%H:%M" if _xfmt == "hm" else "%H:%M:%S"

        def _fmt_x(x, _):
            try:
                dt = mdates.num2date(x, tz=TZ_PRAGUE)
            except Exception:
                return ""
            if same_day: return dt.strftime(_clock)
            return dt.strftime("%m-%d\n00:00") if (dt.hour == 0 and dt.minute == 0) else dt.strftime(_clock)

        ax0.xaxis.set_major_formatter(FuncFormatter(_fmt_x))
        if _minor_ticks:
            ax0.xaxis.set_minor_locator(FixedLocator(_minor_ticks))
        else:
            ax0.xaxis.set_minor_locator(AutoMinorLocator(5))
        ax0.set_xlabel(
            f"Time (Prague)  {t_min_local.strftime('%Y-%m-%d')}" if same_day else "Time (Prague)",
            fontsize=_fsize)
        if using_window:
            ax0.set_xlim(t_min, t_max)
        elif total_seconds > 0:
            pad = timedelta(seconds=max(total_seconds * 0.02, 5))
            ax0.set_xlim(t_min - pad, t_max + pad)
        ax0.tick_params(axis="x", which="major", labelsize=_fsize, rotation=0)
        ax0.tick_params(axis="x", which="minor", length=3, labelsize=0)

        for rl in self._ref_lines:
            ax0.axhline(y=rl["y"], color=rl["color"], linewidth=1.2, linestyle="--",
                        label=rl.get("label") or f"y={rl['y']}")

        # (Carry-forward is now drawn as a solid held line in the per-PV loop
        # above, so no separate dotted "last known value" overlay is needed.)

        # Embed canvas — force it to expand and fill the container so the graph
        # border hugs the plot (no white padding band inside the border).
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        canvas.setMinimumSize(0, 0)
        layout = self._graph_container.layout()
        layout.addWidget(canvas)
        self._mpl_canvas = canvas
        self._mpl_figure = fig
        self._graph_axes = axes

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
        self._crosshair_cid = canvas.mpl_connect("motion_notify_event", self._on_graph_mouse_move)

        try:
            self._span_selector = SpanSelector(
                ax_x, self._on_span_select, direction="horizontal",
                useblit=False, props=dict(alpha=0.15, facecolor="#1565C0"), interactive=True)
        except Exception:
            self._span_selector = None
        try:
            self._zoom_selector = SpanSelector(
                ax_x, self._on_zoom_select, direction="horizontal",
                useblit=False, button=3, props=dict(alpha=0.20, facecolor="#FF6600"))
        except Exception:
            self._zoom_selector = None

        canvas.draw()
        self._refresh_axis_settings_tv()

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

        t_lo_num = t_hi_num = None
        new_raw   = []
        for i, (pv, ax, pv_lines) in enumerate(zip(self._graph_pvs, self._graph_axes, self._graph_lines)):
            ts_a, values = _samples_to_ns_arrays(self._samples_by_pv.get(pv, []))
            # Downsample (in numpy) BEFORE anything else. The live fast path used
            # to push EVERY raw sample to matplotlib on every ~1 s refresh with no
            # cap, so a 100k+ point window rebuilt 100k datetimes and redrew 100k
            # vertices per PV per tick — the source of the judder. Binning to the
            # same target the full replot uses keeps each refresh light.
            if avg_target > 0:
                ts_a, values = _downsample_arrays_mean(ts_a, values, avg_target)
            elif ts_a.size > 25000:
                step = max(1, ts_a.size // 25000)
                ts_a = ts_a[::step]; values = values[::step]
            pre     = self._pre_window_vals.get(pv)
            pre_val = pre[1] if (pre and isinstance(pre[1], (int, float))) else None
            if not ts_a.size:
                # No data in the window: draw the last-known value flat across it.
                if pre_val is not None and win_start_ns is not None:
                    ts_a   = np.array([win_start_ns, now_ns_], dtype=np.float64)
                    values = np.array([pre_val, pre_val], dtype=np.float64)
                else:
                    new_raw.append(self._graph_raw[i] if i < len(self._graph_raw)
                                   else (np.empty(0), np.empty(0)))
                    continue
            else:
                # Extend to the window edges so the trace spans start → now.
                if win_start_ns is not None and pre_val is not None and ts_a[0] > win_start_ns:
                    ts_a   = np.concatenate(([win_start_ns], ts_a))
                    values = np.concatenate(([pre_val], values))
                if ts_a[-1] < now_ns_:
                    ts_a   = np.concatenate((ts_a, [now_ns_]))
                    values = np.concatenate((values, values[-1:]))
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
            # Re-apply this PV's band (or full height if Autoscale on).
            if values.size and not np.all(np.isnan(values)):
                dmin = float(np.nanmin(values)); dmax = float(np.nanmax(values))
                autosc = pv_setting.get("auto_scale", False)
                if not autosc:
                    ymin_pv = pv_setting.get("ymin"); ymax_pv = pv_setting.get("ymax")
                    if ymin_pv is not None: dmin = ymin_pv
                    if ymax_pv is not None: dmax = ymax_pv
                lo, hi = self._band_ylim(i, len(self._graph_pvs), dmin, dmax, autosc)
                ax.set_ylim(lo, hi)

        self._graph_raw = new_raw
        if self._graph_axes:
            ax0 = self._graph_axes[0]
            from matplotlib.ticker import FuncFormatter as _FF, FixedLocator as _FL
            live_span = self._live_window_span
            # Auto-scrolling live window: re-derive the X range AND rebuild its
            # ticks for "now" on every refresh. Previously only xlim scrolled while
            # the FixedLocator tick positions stayed frozen at the initial window —
            # once "now" moved past them the axis showed no timestamps at all (the
            # reported blank time axis). When zoomed in, leave the user's view be.
            if live_span and live_span.total_seconds() > 0 and not self._zoom_history:
                x_lo, x_hi = now_utc - live_span, now_utc
                ax0.set_xlim(x_lo, x_hi)
                lo_l = x_lo.astimezone(TZ_PRAGUE); hi_l = x_hi.astimezone(TZ_PRAGUE)
            elif t_hi_num is not None and t_hi_num > t_lo_num:
                lo_l = mdates.num2date(t_lo_num, tz=TZ_PRAGUE)
                hi_l = mdates.num2date(t_hi_num, tz=TZ_PRAGUE)
            else:
                lo_l = hi_l = None

            if lo_l is not None and (hi_l - lo_l).total_seconds() > 0:
                same_d = lo_l.date() == hi_l.date()
                majors, minors = self._compute_x_ticks(lo_l, hi_l)
                if majors:
                    ax0.xaxis.set_major_locator(_FL(majors))
                if minors:
                    ax0.xaxis.set_minor_locator(_FL(minors))

                _clk = ("%H:%M" if str(self._graph_opts.get("x_time_format", "auto")) == "hm"
                        else "%H:%M:%S")

                def _fmt_x_live(x, _p, _same=same_d, _c=_clk):
                    try:
                        dt = mdates.num2date(x, tz=TZ_PRAGUE)
                    except Exception:
                        return ""
                    if _same: return dt.strftime(_c)
                    return (dt.strftime("%m-%d\n00:00")
                            if (dt.hour == 0 and dt.minute == 0) else dt.strftime(_c))
                ax0.xaxis.set_major_formatter(_FF(_fmt_x_live))

        # Keep the crosshair snap data in sync, and force a fresh blit background
        # on the next draw so the cursor boxes never ghost over stale pixels.
        self._graph_raw_np = [
            (np.asarray(t, dtype=float), np.asarray(v, dtype=float))
            for t, v in self._graph_raw]
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
                ts_str    = dt_cursor.strftime("%H:%M:%S")
                self._x_cursor_ann.set_position((x_f, -0.01))
                self._x_cursor_ann.set_text(ts_str); self._x_cursor_ann.set_visible(True)
                if bg is not None: self._x_cursor_ann.axes.draw_artist(self._x_cursor_ann)
                self._lbl_graph_info.setText(dt_cursor.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                dt_cursor = None

        if self._y_cursor_ann is not None and self._y_cursor_ann_ax is not None:
            try:
                yax   = self._y_cursor_ann_ax
                y_cur = float(yax.transData.inverted().transform((disp_x, disp_y))[1])
                xf    = self._y_cursor_ann.get_position()[0]
                self._y_cursor_ann.set_position((xf, y_cur))
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
        except Exception:
            lo_px = hi_px = None
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
                ann.set_position((x_f, y_data))
                ann.set_visible(True)
                if bg is not None:
                    ax.draw_artist(ann)

    def _flush_cursor_table(self):
        """Write the latest cursor values (already cached in _pv_settings by the
        hover handler) into the axis-settings table. Coalesced via a timer."""
        try:
            val_idx = list(self._axis_tv_cols).index("cursor_val")
        except ValueError:
            return
        # These programmatic updates must not trip the auto-apply itemChanged
        # handler (it would replot on every 120 ms cursor tick).
        self._axis_tv.blockSignals(True)
        try:
            for row in range(self._axis_tv.rowCount()):
                pv_item = self._axis_tv.item(row, 1)
                if not pv_item:
                    continue
                cv = self._pv_settings.get(pv_item.text(), {}).get("cursor_val", "")
                item = self._axis_tv.item(row, val_idx)
                if item:
                    item.setText(cv)
        finally:
            self._axis_tv.blockSignals(False)

    def _clear_graph(self):
        if self._mpl_canvas is not None:
            layout = self._graph_container.layout()
            if layout:
                layout.removeWidget(self._mpl_canvas)
            self._mpl_canvas.setParent(None)
            self._mpl_canvas.deleteLater()
            self._mpl_canvas = None
        self._mpl_figure  = None
        self._graph_axes  = []
        self._graph_lines = []
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
                                  "around the average (population σ)."),
            ("Min", stats["min"], "Smallest value in the selection."),
            ("Max", stats["max"], "Largest value in the selection."),
            ("P-P", stats["ptp"], "Peak-to-peak = Max − Min (total spread of the values)."),
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
        # Left-drag selection over the plot → per-PV statistics for the region.
        if xmax - xmin < 1e-9 or not self._graph_pvs:
            self._clear_stats()
            return

        self._clear_stats()
        any_card = False
        for i, pv in enumerate(self._graph_pvs):
            if i >= len(self._graph_raw_np):
                continue
            t_arr, v_arr = self._graph_raw_np[i]
            if t_arr.size == 0:
                continue
            mask = (t_arr >= xmin) & (t_arr <= xmax) & np.isfinite(v_arr)
            sel = v_arr[mask]
            if sel.size == 0:
                continue
            stats = {
                "n":   f"{sel.size:,}",
                "avg": _fmt_cursor_value(float(np.mean(sel))),
                "std": _fmt_cursor_value(float(np.std(sel))),
                "min": _fmt_cursor_value(float(np.min(sel))),
                "max": _fmt_cursor_value(float(np.max(sel))),
                "ptp": _fmt_cursor_value(float(np.max(sel) - np.min(sel))),
            }
            pv_setting = self._pv_settings.get(pv, {})
            disp_name = pv_setting.get("display_name", shorten_pv_name(pv))
            color     = pv_setting.get("color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])
            self._stats_flow.addWidget(self._make_stat_card(disp_name, color, stats))
            any_card = True

        if not any_card:
            self._clear_stats()
            return

        t0 = mdates.num2date(xmin, tz=TZ_PRAGUE)
        t1 = mdates.num2date(xmax, tz=TZ_PRAGUE)
        span_s = (t1 - t0).total_seconds()
        self._stats_title.setText(
            f"Selection statistics  ·  {t0.strftime('%Y-%m-%d %H:%M:%S')} → "
            f"{t1.strftime('%H:%M:%S')}  ({span_s:,.1f} s)")
        self._stats_title.show()
        self._stats_scroll.show()

    def _on_zoom_select(self, xmin, xmax):
        if xmax - xmin < 1e-6: return
        if not self._graph_axes: return
        t0 = mdates.num2date(xmin, tz=TZ_PRAGUE)
        t1 = mdates.num2date(xmax, tz=TZ_PRAGUE)
        old_xlim = self._graph_axes[0].get_xlim()
        self._zoom_history.append(old_xlim)
        self._graph_axes[0].set_xlim(t0, t1)
        self._btn_zoom_back.setEnabled(True)
        if self._mpl_canvas:
            self._mpl_canvas.draw_idle()

    def _zoom_back(self):
        if not self._zoom_history or not self._graph_axes: return
        xlim = self._zoom_history.pop()
        self._graph_axes[0].set_xlim(xlim)
        if not self._zoom_history:
            self._btn_zoom_back.setEnabled(False)
        if self._mpl_canvas:
            self._mpl_canvas.draw_idle()

    def _xy_zoom_back(self):
        if not self._xy_zoom_history or self._xy_canvas is None: return
        xlim, ylim = self._xy_zoom_history.pop()
        if self._xy_figure and self._xy_figure.axes:
            ax = self._xy_figure.axes[0]
            ax.set_xlim(xlim); ax.set_ylim(ylim)
            self._xy_canvas.draw_idle()

    # ── Save / clean graph ──────────────────────────────────────────────────

    def _save_graph(self):
        if self._mpl_figure is None:
            QMessageBox.information(self, "No graph", "No graph to save."); return
        path, _ = QFileDialog.getSaveFileName(self, "Save graph", "", "PNG (*.png);;PDF (*.pdf)")
        if path:
            self._mpl_figure.savefig(path, dpi=150, bbox_inches="tight")
            self._log(f"Graph saved: {path}")

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
            self._graph_raw    = []
            self._graph_raw_np = []
            self._clear_stats()
            self._mpl_canvas.draw_idle()
            self._lbl_graph_info.setText("Graph cleared — data points removed.")
        else:
            self._clear_graph()
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
        # Ignore the app-wide shortcut when the user is on another tool's tab and
        # nothing is popped out yet.
        if self._graph_popup is None and not self.isVisible():
            return

        if self._graph_popup is not None:
            is_full = self._graph_popup.isFullScreen()
            # Same mode again → dock back; different mode → switch.
            if windowed and not is_full:
                self._restore_graph_from_popup(); return
            if (not windowed) and is_full:
                self._restore_graph_from_popup(); return
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
        pl.setContentsMargins(0, 0, 0, 0)
        self._notebook.removeTab(self._graph_tab_index)
        pl.addWidget(self._tab_graph)
        self._tab_graph.show()
        # "Graph only": hide the toolbar and the axis-settings table.
        self._graph_ctrl_bar.hide()
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
        lay = popup.layout()
        if lay is not None:
            lay.removeWidget(self._tab_graph)
        self._tab_graph.setParent(None)
        # Restore the toolbar and axis-settings table hidden during pop-out.
        self._graph_ctrl_bar.show()
        self._graph_axis_pane.show()
        self._notebook.insertTab(self._graph_tab_index, self._tab_graph,
                                 self._graph_tab_label)
        self._notebook.setCurrentWidget(self._tab_graph)
        popup.deleteLater()
        QTimer.singleShot(0, self._autosize_axis_pane)   # pane was hidden
        self._lbl_status.setText("Graph docked back.")

    # ── Downsampling / averaging ──────────────────────────────────────────────

    def _avg_target_points(self) -> int:
        spin = getattr(self, "_avg_target_spin", None)
        if spin is not None:
            return int(spin.value())
        return int(self.config.get("avg_target_points", 2000))

    def _on_avg_target_changed(self, _val):
        self.config["avg_target_points"] = self._avg_target_points()
        if self._samples_by_pv:
            self._schedule_replot()   # coalesce rapid spinner clicks into one redraw

    def _compute_x_ticks(self, t_lo, t_hi):
        """Major + minor X-tick positions (matplotlib date numbers) for the local
        time window [t_lo, t_hi]. The endpoints are always ticked; interior majors
        land on a 'nice' step chosen so at most "Max time stamps" (Graph settings)
        fit, or on the fixed spacing set there. Shared by the full replot and the
        live fast path so a scrolling live window keeps FRESH ticks — otherwise the
        initial FixedLocator ticks scroll out of view and the time axis goes blank.
        """
        span_s = (t_hi - t_lo).total_seconds()
        _opts  = getattr(self, "_graph_opts", None) or {}
        _want  = max(2, int(_opts.get("x_ticks_max", 8)))
        _fixed = max(0, int(_opts.get("x_tick_seconds", 0)))
        _STEPS = [1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200,10800,21600,43200,86400,172800]
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
        first_s  = math.ceil((offset_s + step_s * 0.25) / step_s) * step_s
        ticks_dt = []
        cur_s    = first_s
        while True:
            dt_tick = epoch + timedelta(seconds=cur_s)
            if dt_tick >= t_hi - timedelta(seconds=step_s * 0.25): break
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
        return majors, minors

    # (Time-binned averaging lives in the module-level _downsample_arrays_mean:
    # every caller feeds matplotlib, so the whole path stays in numpy arrays.)

    # ── Load data ───────────────────────────────────────────────────────────

    def _on_load_clicked(self):
        pvs = self._real_pv_names()
        if not pvs:
            QMessageBox.warning(self, "No PVs", "Add at least one PV to the list."); return
        # A manual load freezes the view in the past: stop live first so its
        # auto-scrolling window doesn't fight the historical data being shown.
        if self._live_mode:
            self._stop_live("Live stopped — showing loaded window.")
        self._btn_load.setEnabled(False)
        self._lbl_status.setText("Loading…")
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        start_ns = dt_to_ns(self._dt_from)
        end_ns   = dt_to_ns(self._dt_to)
        # Read the target-points value on the UI thread (Qt widgets are not
        # thread-safe). >0 → server-side decimated fetch; 0 → raw chunked fetch.
        avg_target = self._avg_target_points()
        print(f"[CSS Logger] Load clicked: {len(pvs)} PVs, window {start_ns} → {end_ns}, "
              f"avg_target={avg_target}")
        self._load_sig = _LoadSig()          # keep alive until done
        sig = self._load_sig
        sig.done.connect(self._on_load_finished)
        sig.error.connect(self._on_load_error)
        sig.progress.connect(self._lbl_status.setText)
        sig.pct.connect(self._progress_bar.setValue)

        def _worker():
            try:
                from cpva_core import (cpva_fetch_many_chunked,
                                       cpva_fetch_many_optimized, cpva_decode_value,
                                       cpva_fetch_last_before_many)
                samples_by_pv   = {}
                pv_order        = []
                errors          = []
                pre_window_vals = {}

                if avg_target > 0:
                    # Server-side decimation: one request per PV over the whole
                    # window (no 1-hour chunking). Fast for long windows.
                    def _report(done, total_pvs):
                        if total_pvs:
                            sig.pct.emit(int(done / total_pvs * 100))
                            sig.progress.emit(
                                f"Fetching {total_pvs} PVs (optimized): {done}/{total_pvs}")

                    sig.progress.emit(f"Fetching {len(pvs)} PVs (optimized, ≤{avg_target} pts)…")
                    raw_by_pv, chunk_errors = cpva_fetch_many_optimized(
                        pvs, start_ns, end_ns, avg_target, progress_fn=_report)
                else:
                    # Raw: fetch every PV's 1-hour chunks in one shared pool so
                    # the PVs load concurrently instead of one after another.
                    def _report(done, total_chunks):
                        if total_chunks:
                            sig.pct.emit(int(done / total_chunks * 100))
                            sig.progress.emit(
                                f"Fetching {len(pvs)} PVs: {done}/{total_chunks} chunks")

                    sig.progress.emit(f"Fetching {len(pvs)} PVs (raw)…")
                    raw_by_pv, chunk_errors = cpva_fetch_many_chunked(
                        pvs, start_ns, end_ns, progress_fn=_report)

                for pv in pvs:
                    if pv in chunk_errors:
                        errors.append(f"{pv}: {chunk_errors[pv]}")
                        print(f"[CSS Logger]   → ERROR {pv}: {chunk_errors[pv]}")
                    raw = raw_by_pv.get(pv, [])
                    samples = []
                    for s in raw:
                        t_ns = s.get("time")
                        if t_ns is None: continue
                        value = cpva_decode_value(s)
                        units = (s.get("metaData") or {}).get("units", "") or ""
                        samples.append((int(t_ns), value, units))
                    samples_by_pv[pv] = samples
                    pv_order.append(pv)
                    print(f"[CSS Logger]   {pv} → {len(samples)} samples")

                # Look back for the last sample BEFORE the window start whenever a
                # PV has no data or its first sample sits after start_ns, so the
                # trace can start at the window edge instead of jumping in later.
                sig.progress.emit("Filling gaps at window start…")
                need_lookback = []
                for pv in pvs:
                    first_ts = min((t for t, _, _ in samples_by_pv.get(pv, [])),
                                   default=None)
                    if first_ts is None or first_ts > start_ns:
                        need_lookback.append(pv)
                last_map = cpva_fetch_last_before_many(need_lookback, start_ns)
                for pv, last_s in last_map.items():
                    last_ts = last_s.get("time")
                    if last_ts:
                        pre_window_vals[pv] = (
                            int(last_ts), cpva_decode_value(last_s),
                            (last_s.get("metaData") or {}).get("units", "") or "")
                sig.pct.emit(100)
                print(f"[CSS Logger] Emitting done: {len(pv_order)} PVs, errors={errors}")
                sig.done.emit((samples_by_pv, pv_order, errors, start_ns, end_ns, pre_window_vals))
            except Exception as exc:
                import traceback; traceback.print_exc()
                sig.error.emit(str(exc))

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
        self._btn_load.setEnabled(True)
        self._progress_bar.hide()

    def _on_load_finished(self, result):
        print(f"[CSS Logger] _on_load_finished called, result type={type(result)}, len={len(result) if result else 0}")
        try:
            self.__on_load_finished_inner(result)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[CSS Logger] ERROR in _on_load_finished:\n{tb}")
            self._log(f"[ERROR in _on_load_finished]\n{tb}")
            self._lbl_status.setText(f"Internal error — see Log tab: {exc}")
            self._btn_load.setEnabled(True)
            self._progress_bar.hide()

    def __on_load_finished_inner(self, result):
        if len(result) == 6:
            samples_by_pv, pv_order, errors, start_ns, end_ns, pre_window_vals = result
        else:
            samples_by_pv, pv_order, errors, start_ns, end_ns = result
            pre_window_vals = {}
        self._pre_window_vals = pre_window_vals
        self._plot_window_ns = None   # regular load uses the user's From/To
        self._btn_load.setEnabled(True)
        self._progress_bar.hide()

        # Sort every PV by timestamp (parallel fetch can arrive out of order)
        for pv in samples_by_pv:
            samples_by_pv[pv].sort(key=lambda s: s[0])

        # Seed the pre-window value from the last fetched sample BEFORE the window
        # start, so the trace begins held at the left edge instead of "in the
        # middle". The archiver sometimes returns an anchor/decimated sample at or
        # before start_ns; the explicit lookback then skips that PV (first_ts <=
        # start) and the trim below would discard the sample, leaving no seed.
        # Reuse it here (no extra HTTP) whenever a numeric seed isn't already set.
        for pv, samples in samples_by_pv.items():
            cur = self._pre_window_vals.get(pv)
            if cur is not None and isinstance(cur[1], (int, float)):
                continue
            for ts, v, u in reversed(samples):
                if ts >= start_ns:
                    continue
                if isinstance(v, (int, float)):
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

        self._numeric_pvs = {
            pv for pv, s in self._samples_by_pv.items()
            if any(isinstance(v, (int, float)) for _, v, _ in s)
        }

        # Build merged rows via sample-hold
        rows = self._build_table_rows(self._samples_by_pv, pv_order)

        # Apply master-PV deduplication filters
        rows = self._remove_master_only_rows(rows)
        rows = self._remove_fake_hour_boundary_rows(rows)
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

        self._plot_graph()
        self._refresh_axis_settings_tv()   # always show PV list, even with 0 samples
        self._populate_table()
        self._refresh_xy_choices()

        n_real = len(pv_order)
        n_custom = len(self._pv_order) - n_real
        total_pts = sum(len(v) for v in self._samples_by_pv.values())
        if errors:
            self._log("Load errors:\n" + "\n".join(errors))
        extra = f" + {n_custom} custom" if n_custom else ""
        filter_note = ""
        self._log(f"Loaded {n_real}{extra} PVs, {total_pts} samples, "
                  f"{len(self._table_rows)} merged rows.")
        self._lbl_status.setText(
            f"Loaded {total_pts} pts  |  {len(self._table_rows)} rows  |  "
            f"{n_real} PVs{extra}{filter_note}")

    def _build_table_rows(self, samples_by_pv, pv_order):
        """Merge multi-PV sample streams into time-aligned rows (sample-hold)."""
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

        Live keeps the whole span merged, filtered and plotted at all times, so
        an unreasonably wide window is not just slow to load — it makes every
        refresh expensive for the rest of the session. A window that is too
        short, absent or wider than `_LIVE_MAX_INIT_SPAN_S` is clamped, and the
        clamp is logged so it never looks like Live simply ignored the window.
        """
        window_diff = (self._dt_to - self._dt_from).total_seconds()
        if not (60 <= window_diff <= _LIVE_MAX_SPAN_S):
            return timedelta(hours=1)
        if window_diff > _LIVE_MAX_INIT_SPAN_S:
            self._log(f"Live window {window_diff/3600:.1f} h is wider than the "
                      f"{_LIVE_MAX_INIT_SPAN_S/3600:.0f} h live limit — "
                      f"using {_LIVE_MAX_INIT_SPAN_S/3600:.0f} h "
                      f"(LOAD DATA still loads the full window).")
            return timedelta(seconds=_LIVE_MAX_INIT_SPAN_S)
        return timedelta(seconds=window_diff)

    def _toggle_live_mode(self):
        if self._live_mode:
            self._stop_live()
        else:
            pvs = self._real_pv_names()
            if not pvs:
                QMessageBox.warning(self, "No PVs", "Add PVs before starting live mode."); return
            self._live_mode = True
            self._live_window_span = self._live_span_from_window()
            self._btn_live.setText("⏹ Stop Live")
            self._btn_live.setStyleSheet(
                "QPushButton{background:#F57F17;color:white;font-weight:700;border-radius:3px;padding:5px;}")
            self._refresh_time_labels()
            self._lbl_status.setText("Live: initial load…")
            self._btn_load.setEnabled(False)
            self._live_last_ts = None
            self._live_last_rebuild_ns = 0
            self._live_init_retries = 0      # fresh retry budget for this session
            self._live_initial_load()

    def _stop_live(self, status="Live mode stopped."):
        """Stop live polling and restore the idle UI. Safe to call when not live
        (used by the Live toggle and by LOAD DATA so a manual load freezes the
        view instead of fighting the auto-scrolling live window)."""
        self._live_mode = False
        # Abandon whatever is in flight, not just the next tick: a running fetch
        # pool ignores the timers entirely and would keep the GUI stuttering.
        self._live_epoch += 1
        self._live_timer.stop()
        self._countdown_timer.stop()
        self._btn_live.setText("⏵ Live")
        self._btn_live.setStyleSheet("")
        # Free the LOAD DATA button even if the initial live fetch is still
        # running (its callback bails once _live_mode is False).
        self._btn_load.setEnabled(True)
        self._progress_bar.hide()
        self._plot_window_ns = None
        self._refresh_time_labels()
        self._lbl_status.setText(status)

    def _live_initial_load(self):
        now_utc = datetime.now().astimezone(timezone.utc)
        t_from  = now_utc - self._live_window_span
        start_ns = dt_to_ns(t_from)
        end_ns   = dt_to_ns(now_utc)
        pvs      = self._real_pv_names()
        avg_target = self._avg_target_points()   # read on UI thread
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
        sig.pct.connect(self._progress_bar.setValue)
        self._progress_bar.setValue(0)
        self._progress_bar.show()

        def _worker():
            try:
                from cpva_core import (cpva_fetch_many_chunked,
                                       cpva_fetch_many_optimized, cpva_decode_value,
                                       cpva_fetch_last_before_many)
                samples_by_pv = {}
                pv_order = []
                pre_window_vals = {}

                if avg_target > 0:
                    # Server-side decimation: one request per PV over the window.
                    def _report(done, total_pvs):
                        if total_pvs:
                            sig.pct.emit(int(done / total_pvs * 100))
                            sig.progress.emit(
                                f"Live init (optimized): {done}/{total_pvs} PVs")

                    sig.progress.emit(f"Live init: fetching {len(pvs)} PVs (optimized)…")
                    raw_by_pv, _errs = cpva_fetch_many_optimized(
                        pvs, start_ns, end_ns, avg_target, progress_fn=_report,
                        cancel_fn=cancel)
                else:
                    # Raw: all PVs' 1-hour chunks in one shared pool.
                    def _report(done, total_chunks):
                        if total_chunks:
                            sig.pct.emit(int(done / total_chunks * 100))
                            sig.progress.emit(
                                f"Live init: {done}/{total_chunks} chunks")

                    sig.progress.emit(f"Live init: fetching {len(pvs)} PVs (raw)…")
                    raw_by_pv, _errs = cpva_fetch_many_chunked(
                        pvs, start_ns, end_ns, progress_fn=_report,
                        cancel_fn=cancel)

                if cancel():
                    return                      # session ended while fetching

                for pv in pvs:
                    samples = []
                    for s in raw_by_pv.get(pv, []):
                        t_ns = s.get("time")
                        if t_ns is None: continue
                        value = cpva_decode_value(s)
                        units = (s.get("metaData") or {}).get("units", "") or ""
                        samples.append((int(t_ns), value, units))
                    samples_by_pv[pv] = samples
                    pv_order.append(pv)

                # Carry-forward: hold the last known value so a PV without a fresh
                # sample still draws a line instead of leaving the graph empty.
                sig.progress.emit("Live init: filling gaps…")
                need_lookback = []
                for pv in pvs:
                    first_ts = min((t for t, _, _ in samples_by_pv.get(pv, [])),
                                   default=None)
                    if first_ts is None or first_ts > start_ns:
                        need_lookback.append(pv)
                try:
                    last_map = cpva_fetch_last_before_many(need_lookback, start_ns,
                                                           cancel_fn=cancel)
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
                sig.done.emit((samples_by_pv, pv_order, [], start_ns, end_ns, pre_window_vals))
            except Exception as exc:
                sig.error.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_live_init_error(self, e: str):
        """Live initial load failed — retry a couple of times before giving up so
        a transient archiver hiccup doesn't make Live look like it needs a manual
        LOAD DATA first."""
        self._log(f"Live initial load error: {e}")
        if not self._live_mode:
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
            self._progress_bar.hide(); return
        self._btn_load.setEnabled(True)
        self._progress_bar.hide()
        self._live_init_retries = 0          # success clears the retry budget
        try:
            if len(result) == 6:
                samples_by_pv, pv_order, _, start_ns, end_ns, pre_window_vals = result
            else:
                samples_by_pv, pv_order, _, start_ns, end_ns = result
                pre_window_vals = {}
            self._pre_window_vals = pre_window_vals
            # Anchor the carry-forward window to the live window (without
            # clobbering the user's chosen From/To) so the held line spans it.
            self._plot_window_ns = (start_ns, end_ns)
            self._samples_by_pv = samples_by_pv
            # Same pre-window seed safety net as the historical load path: if the
            # fetch already holds a sample before the window start, use it as the
            # held value so Live doesn't start the trace mid-window.
            for pv, samples in samples_by_pv.items():
                cur = self._pre_window_vals.get(pv)
                if cur is not None and isinstance(cur[1], (int, float)):
                    continue
                best = None
                for ts, v, u in samples:
                    if ts < start_ns and isinstance(v, (int, float)):
                        if best is None or ts > best[0]:
                            best = (ts, v, u)
                if best is not None:
                    self._pre_window_vals[pv] = best
            self._pv_order      = pv_order
            self._base_pv_order = list(pv_order)
            self._numeric_pvs = {
                pv for pv, s in self._samples_by_pv.items()
                if any(isinstance(v, (int, float)) for _, v, _ in s)
            }
            rows = self._build_table_rows(samples_by_pv, pv_order)
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
            self._plot_graph()
            self._populate_table()
            self._refresh_xy_choices()
        except Exception:
            import traceback
            self._log(f"[live initial load]\n{traceback.format_exc()}")
        finally:
            # Always keep the live loop alive even if this paint failed.
            if self._live_mode:
                self._schedule_live_tick()

    def _live_tick(self):
        if not self._live_mode: return
        pvs = self._real_pv_names()
        if not pvs:
            self._toggle_live_mode(); return
        end_ns   = now_ns()
        start_ns = self._live_last_ts if self._live_last_ts else (end_ns - int(60e9))
        # Re-query from the last KNOWN sample forward so ingestion lag doesn't
        # make us skip newly-archived points — but never look back further than
        # _LIVE_TICK_LOOKBACK_NS. Without that cap the range is unbounded on a
        # quiet archiver (see the constant), and one tick fans out into dozens of
        # 1-hour chunks per PV every 300 ms.
        min_start = end_ns - _LIVE_TICK_LOOKBACK_NS
        if start_ns < min_start:
            start_ns = min_start
        epoch  = self._live_epoch
        cancel = lambda: self._live_epoch != epoch
        self._inc_sig = _IncSig()
        sig = self._inc_sig
        sig.done.connect(self._on_incremental_finished)

        def _worker():
            try:
                from cpva_core import cpva_fetch_many_chunked, cpva_decode_value
                new_samples: dict = {}
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
                    for s in raw_by_pv.get(pv, []):
                        t_ns = s.get("time")
                        if t_ns is None or int(t_ns) <= start_ns: continue
                        value = cpva_decode_value(s)
                        units = (s.get("metaData") or {}).get("units", "") or ""
                        new_pts.append((int(t_ns), value, units))
                    if new_pts:
                        new_samples[pv] = new_pts
                        added_count += len(new_pts)
                sig.done.emit((new_samples, added_count, end_ns, errors))
            except Exception as exc:
                sig.done.emit(({}, 0, end_ns, [str(exc)]))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_incremental_finished(self, result):
        if not self._live_mode: return
        try:
            # Worker emits a 4-tuple (errors added); tolerate the old 3-tuple too.
            if len(result) == 4:
                new_samples, added_count, end_ns, errors = result
            else:
                new_samples, added_count, end_ns = result; errors = []
            self._live_tick_count = getattr(self, "_live_tick_count", 0) + 1

            # Re-merging + re-filtering + re-plotting the WHOLE accumulated
            # history is too expensive to redo on every 300 ms tick once a
            # live session has run for a while — throttle it to at most once
            # a second. New samples are still appended below on every tick;
            # only the expensive rebuild/redraw is deferred.
            now = now_ns()
            # Adaptive cadence: with many PVs one rebuild+redraw can take a
            # sizeable fraction of a second, and firing it again every second
            # leaves the GUI thread almost no idle time (the app feels stuck even
            # though it is working). Keep at least ~3× the measured cost between
            # rebuilds, so the graph stays live but the UI keeps breathing.
            _interval_ns = max(_LIVE_REBUILD_MIN_INTERVAL_NS,
                               3 * getattr(self, "_live_rebuild_cost_ns", 0))
            refresh_due = (now - self._live_last_rebuild_ns) >= _interval_ns
            _rebuild_t0 = time.perf_counter()
            if refresh_due:
                self._live_last_rebuild_ns = now
                # Advance the carry-forward / plot window so a full replot (and the
                # held "last value" line) tracks "now" instead of the initial load.
                if self._live_window_span:
                    span_ns = int(self._live_window_span.total_seconds() * 1e9)
                    self._plot_window_ns = (now - span_ns, now)

            if added_count > 0:
                # Advance the cursor to the newest sample we actually received —
                # NOT to wall-clock "now" — so archiver ingestion lag can't make
                # the next query start ahead of where data exists (which would
                # silently stall live updates forever).
                maxrecv = max((pts[-1][0] for pts in new_samples.values() if pts),
                              default=0)
                if maxrecv:
                    self._live_last_ts = max(self._live_last_ts or 0, maxrecv)
                for pv, pts in new_samples.items():
                    existing = self._samples_by_pv.get(pv, [])
                    existing.extend(pts)
                    # Trim to window
                    if self._live_window_span:
                        cutoff_ns = int((datetime.now().astimezone(timezone.utc)
                                         - self._live_window_span).timestamp() * 1e9)
                        self._samples_by_pv[pv] = [s for s in existing if s[0] >= cutoff_ns]
                    else:
                        self._samples_by_pv[pv] = existing

                if refresh_due:
                    rows = self._build_table_rows(
                        self._samples_by_pv, self._base_pv_order or self._pv_order)
                    rows = self._remove_master_only_rows(rows)
                    rows = self._remove_fake_hour_boundary_rows(rows)
                    self._table_rows_unfiltered = rows
                    self._rebuild_custom_pvs()          # recompute derived channels live
                    rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
                    self._table_rows = self._apply_conditions_to_rows(rows)
                    self._populate_table()
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

            # Refresh the graph on the same throttled cadence as the table so
            # the live window keeps scrolling to "now" without redoing the
            # (expensive, full-history) redraw every 300 ms tick.
            # If the graph has no lines yet or a PV just gained its first numeric
            # data, the existing artists can't represent it — do a full replot;
            # otherwise just mutate the existing artists (fast path).
            if refresh_due:
                numeric_now = {
                    pv for pv in self._pv_order
                    if self._pv_settings.get(pv, {}).get("show", True)
                    and any(isinstance(v, (int, float))
                            for _, v, _ in self._samples_by_pv.get(pv, []))
                }
                if not self._graph_lines or not numeric_now.issubset(set(self._graph_pvs)):
                    self._plot_graph()
                else:
                    self._update_graph_data()
                # Cost of this whole refresh (table + graph); paces the next one.
                # The canvas repaint itself happens later via draw_idle, so add a
                # rough allowance for it rather than under-counting the work.
                self._live_rebuild_cost_ns = int(
                    (time.perf_counter() - _rebuild_t0) * 1.6e9)
        except Exception:
            # A failed incremental update must NOT kill the live loop or crash
            # the Qt timer callback — log and keep polling.
            import traceback
            self._log(f"[live tick]\n{traceback.format_exc()}")
        finally:
            self._schedule_live_tick()

    def _schedule_live_tick(self):
        if not self._live_mode: return
        interval_ms = 300  # fixed 300 ms poll interval
        self._live_countdown_elapsed_ms = 0
        self._countdown_timer.start()
        self._live_timer.start(interval_ms)

    def _live_countdown_tick(self):
        if not self._live_mode:
            self._countdown_timer.stop(); return
        self._live_countdown_elapsed_ms += 50
        interval_ms = 300  # fixed 300 ms poll interval
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
        if not self._conditions or not rows:
            return list(rows)
        # Rows are built by sample-hold, so the last one carries every channel
        # that appeared anywhere in the window — the exact set of PVs the
        # conditions can actually judge.
        available = set(rows[-1][1])
        active  = [c for c in self._conditions
                   if c.get("pv") and c["pv"] in available]
        skipped = [c["pv"] for c in self._conditions
                   if c.get("pv") and c["pv"] not in available]
        filtered = [
            (ts, row_dict) for ts, row_dict in rows
            if self._row_matches_conditions(row_dict, active)
        ]
        self._log_conditions_diag(skipped, len(rows), len(filtered))
        return filtered

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
            if not self._condition_value_ok(value, cond.get("min"), cond.get("max")):
                return False
        return True

    def _condition_value_ok(self, value, vmin, vmax):
        if not isinstance(value, (int, float)):
            return False
        if vmin is not None and value < vmin:
            return False
        if vmax is not None and value > vmax:
            return False
        return True

    # ── Master PV filters ────────────────────────────────────────────────────

    def _get_master_pv(self) -> str:
        if self._master_pv_edit is not None:
            return self._master_pv_edit.text().strip() or MASTER_RAMP_PV
        return self._master_pv or MASTER_RAMP_PV

    def _get_master_multiple(self) -> str:
        if self._master_multiple_edit is not None:
            return self._master_multiple_edit.text().strip()
        return self._master_multiple

    def _remove_master_only_rows(self, rows: list) -> list:
        master_pv = self._get_master_pv()
        if not rows or not master_pv:
            return rows
        filtered = [rows[0]]
        for ts_ns, row_dict in rows[1:]:
            _prev_ts, prev_row = filtered[-1]
            keys = set(prev_row.keys()) | set(row_dict.keys())
            same_other = all(
                prev_row.get(pv) == row_dict.get(pv)
                for pv in keys if pv != master_pv
            )
            master_changed = prev_row.get(master_pv) != row_dict.get(master_pv)
            if master_changed and same_other:
                continue
            filtered.append((ts_ns, row_dict))
        return filtered

    def _remove_fake_hour_boundary_rows(self, rows: list) -> list:
        if len(rows) < 2:
            return rows
        master_pv     = self._get_master_pv()
        MIN_DIFF_NS   = int(3599 * 1e9)
        MAX_DIFF_NS   = int(3601 * 1e9)
        filtered = [rows[0]]
        for ts_ns, row_dict in rows[1:]:
            prev_ts, prev_row = filtered[-1]
            dt_ns = ts_ns - prev_ts
            if MIN_DIFF_NS <= dt_ns <= MAX_DIFF_NS:
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
                disp = self._pv_settings.get(pv, {}).get("display_name", shorten_pv_name(pv))
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

    def _compute_custom_pvs_in_rows(self, rows):
        """Evaluate every custom PV for each row.

        Each formula resolves its variables through its own ``bindings``
        (letter -> PV name), so its values follow the PVs even after the list is
        reordered or another preset is loaded. A letter with no binding falls
        back to the old positional lookup. Customs are computed in definition
        order, so a later custom can read an earlier one — its binding simply
        names that custom channel. Problems are collected in ``_cpv_diag``
        instead of being swallowed."""
        defs = [d for d in self._custom_pvs if d.get("name") and d.get("expr")]
        self._cpv_diag = []
        if not defs:
            return
        SAFE = {"__builtins__": {}, "abs": abs, "min": min,
                "max": max, "round": round, "math": math}
        positional = {lt: pv for lt, pv, _ in self._channel_letters()}
        loaded     = set(self._pv_order)
        plan   = []                    # (name, code, [(letter, source_pv)])
        broken = set()                 # names already reported as not computable
        for d in defs:
            name, expr = d["name"], d["expr"]
            bindings   = d.get("bindings") or {}
            srcs, missing, unknown = [], [], []
            for letter in _cpv_vars(expr):
                pv = bindings.get(letter) or positional.get(letter)
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
            if unknown:
                self._cpv_diag.append(
                    f"Custom PV '{name}': not computed — "
                    f"{', '.join(unknown)} is not a known channel.")
            try:
                code = compile(expr, "<cpv>", "eval")
            except Exception as exc:
                code = None
                self._cpv_diag.append(
                    f"Custom PV '{name}': invalid expression — {exc}")
            if missing or unknown or code is None:
                broken.add(name)
            plan.append((name, code, srcs))
        errs = {}                      # name -> [row count, first exception]
        for _ts_ns, row_dict in rows:
            for name, code, srcs in plan:
                result = None
                if code is not None:
                    ns = {}
                    for letter, pv in srcs:
                        v = row_dict.get(pv, (None,))[0]
                        ns[letter] = v if isinstance(v, (int, float)) else None
                    try:
                        result = eval(code, SAFE, ns)
                    except Exception as exc:
                        e = errs.setdefault(name, [0, type(exc).__name__])
                        e[0] += 1
                row_dict[name] = (result, "")
        for name, (cnt, first) in errs.items():
            # A formula with a missing source fails on every row by definition —
            # it was already reported above, don't repeat it as a row count.
            if name not in broken:
                self._cpv_diag.append(
                    f"Custom PV '{name}': {cnt} of {len(rows)} rows could not be "
                    f"evaluated ({first}).")

    def _emit_custom_pv_diag(self):
        """Log what the last compute found wrong — once. This runs on every live
        refresh, so an unchanged set of messages is not logged again."""
        msgs = list(getattr(self, "_cpv_diag", []) or [])
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

        self._compute_custom_pvs_in_rows(self._table_rows_unfiltered)
        self._emit_custom_pv_diag()

        for d in self._custom_pvs:
            name = d.get("name", "")
            if not name:
                continue
            self._samples_by_pv[name] = [
                (ts, v, u)
                for ts, row_dict in self._table_rows_unfiltered
                if name in row_dict
                for v, u in [row_dict[name]]
                if isinstance(v, (int, float))
            ]
            if self._samples_by_pv[name]:
                self._numeric_pvs.add(name)

    # ── Save runtime state ────────────────────────────────────────────────────

    def _save_runtime_state(self):
        self.config["pv_list"] = self._real_pv_names()
        self.config["conditions"]      = copy(self._conditions)
        self.config["master_pv"]       = self._get_master_pv()
        self.config["master_multiple"] = self._get_master_multiple()
        self.config["avg_target_points"] = self._avg_target_points()
        self.config["graph_opts"]        = dict(self._graph_opts)
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

    def _populate_table(self):
        self._table_display_offset = 0
        if not self._table_rows:
            self._table_widget.setRowCount(0)
            self._table_widget.setColumnCount(1)
            self._table_widget.setHorizontalHeaderLabels(["Timestamp"])
            self._table_cols_cache = ["Timestamp"]
            self._lbl_table_info.setText("No rows.")
            return

        pvs = self._pv_order
        cols = ["Timestamp"] + [shorten_pv_name(p) for p in pvs]
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
        act_img  = menu.addAction("Open image…")
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

    def _refresh_axis_settings_tv(self):
        # Group rows under "Original PV" / "Custom PV" divider rows when custom
        # (derived) PVs exist, so derived channels are clearly separated from real
        # ones. self._axis_row_pv maps each table row → PV name (None for a
        # divider row) so the rest of the code never assumes row == _pv_order idx.
        custom_names = {d.get("name", "") for d in self._custom_pvs}
        originals = [pv for pv in self._pv_order if pv not in custom_names]
        customs   = [pv for pv in self._pv_order if pv in custom_names]
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
        # Rebuilding fires itemChanged for every setItem/setCheckState — block it
        # so the auto-apply handler doesn't run mid-rebuild.
        self._axis_tv.blockSignals(True)
        try:
            self._axis_tv.clearSpans()
            self._axis_tv.setRowCount(len(plan))
            for row_i, (kind, payload) in enumerate(plan):
                if kind == "header":
                    hdr = QTableWidgetItem(f"  {payload}")
                    fnt = hdr.font(); fnt.setBold(True); hdr.setFont(fnt)
                    hdr.setBackground(QColor("#E3F2FD"))
                    hdr.setForeground(QColor("#0D47A1"))
                    hdr.setFlags(Qt.ItemFlag.ItemIsEnabled)   # not selectable/editable
                    self._axis_tv.setItem(row_i, 0, hdr)
                    self._axis_tv.setSpan(row_i, 0, 1, ncol)
                    continue
                pv  = payload
                idx = self._pv_order.index(pv)
                s = self._pv_settings.get(pv, self._get_pv_default_settings(pv, idx))
                for col_j, col_name in enumerate(_COL):
                    val = s.get(col_name, "")
                    if col_name == "blank":
                        item = QTableWidgetItem("")
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # non-editable spacer
                        self._axis_tv.setItem(row_i, col_j, item)
                    elif col_name in ("show", "auto_scale", "grid"):
                        chk = QTableWidgetItem()
                        chk.setCheckState(Qt.CheckState.Checked if val else Qt.CheckState.Unchecked)
                        self._axis_tv.setItem(row_i, col_j, chk)
                    elif col_name == "color":
                        item = QTableWidgetItem("")
                        color_hex = s.get("color", _GRAPH_COLORS[idx % len(_GRAPH_COLORS)])
                        item.setData(Qt.ItemDataRole.UserRole, color_hex)
                        self._axis_tv.setItem(row_i, col_j, item)
                    else:
                        txt = "" if val is None else str(val)
                        item = QTableWidgetItem(txt)
                        if col_name in ("pv", "cursor_val"):
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        self._axis_tv.setItem(row_i, col_j, item)
        finally:
            self._axis_tv.blockSignals(False)
        self._autosize_axis_pane()

    # Never let the PV list eat the whole tab: the graph keeps at least this
    # much height, so the buttons above it always stay on screen.
    _AXIS_PANE_MIN_GRAPH = 240

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
        chrome = (m.top() + m.bottom() + lay.spacing() +
                  self._axis_hdr_label.sizeHint().height())

        total = spl.height()
        if total <= 1:                       # not laid out yet
            QTimer.singleShot(0, self._autosize_axis_pane)
            return
        room  = total - spl.handleWidth() - self._AXIS_PANE_MIN_GRAPH
        floor = chrome + tv.horizontalHeader().height() + 26   # header + one row
        want  = max(floor, min(need + chrome, max(floor, room)))

        pane.setMaximumHeight(want)          # can be dragged smaller, never bigger
        spl.setSizes([max(1, total - spl.handleWidth() - want), want])

    def _on_axis_tv_double_click(self, row, col):
        col_name = list(self._axis_tv_cols)[col] if col < len(self._axis_tv_cols) else ""
        if col_name in ("pv", "cursor_val"): return

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
        self._apply_axis_settings()

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
                elif col_name in ("pv", "cursor_val"):
                    pass
                elif col_name in ("ymin", "ymax", "width"):
                    # Bad numeric input keeps the previous value instead of crashing.
                    s[col_name] = self._safe_float(item.text(), s.get(col_name))
                elif col_name == "smooth":
                    txt = item.text().strip()
                    s[col_name] = int(txt) if txt.isdigit() else 1
                else:
                    s[col_name] = item.text().strip()
        self._schedule_replot()

    def _get_pv_default_settings(self, pv, idx):
        return {
            "show":         True,
            "pv":           pv,
            "display_name": shorten_pv_name(pv),
            "color":        _GRAPH_COLORS[idx % len(_GRAPH_COLORS)],
            "color_auto":   True,   # False once the user hand-picks a color
            "cursor_val":   "",
            "side":         "left",
            "ymin":         None,
            "ymax":         None,
            "auto_scale":   False,
            "width":        None,
            "smooth":       1,
            "grid":         idx == 0,
        }

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
        choices = []
        self._xy_choice_map = {}
        for p in pvs:
            label = p if p in custom_names else shorten_pv_name(p)
            choices.append(label)
            self._xy_choice_map[label] = p
        # Repopulate without firing the auto-replot signal for every added item.
        for combo in (self._xy_x_combo, self._xy_y_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(choices)
            combo.blockSignals(False)
        if len(choices) > 1:
            self._xy_x_combo.setCurrentIndex(0)
            self._xy_y_combo.setCurrentIndex(1)

    def _on_xy_axis_changed(self, _idx=0):
        # Auto-replot when the user picks a different X or Y channel.
        if self._table_rows:
            self._plot_xy()

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
            self._lbl_xy_info.setText("XY plot error — see Log tab.")

    def _plot_xy_impl(self):
        x_label = self._xy_x_combo.currentText()
        y_label = self._xy_y_combo.currentText()
        x_pv    = self._xy_choice_map.get(x_label)
        y_pv    = self._xy_choice_map.get(y_label)
        if not x_pv or not y_pv:
            self._lbl_xy_info.setText("Select X and Y variables first."); return

        xs, ys = [], []
        for ts, row_dict in self._table_rows:
            xval = None
            yval = None
            if x_pv in row_dict:
                v, _ = row_dict[x_pv]
                try: xval = float(v)
                except (TypeError, ValueError): pass
            if y_pv in row_dict:
                v, _ = row_dict[y_pv]
                try: yval = float(v)
                except (TypeError, ValueError): pass
            if xval is not None and yval is not None:
                xs.append(xval); ys.append(yval)

        if not xs:
            self._lbl_xy_info.setText("No data to plot."); return

        self._clear_xy_plot()

        _dpi = 96
        _cw  = max(self._xy_canvas_container.width()  - 8, 800)
        _ch  = max(self._xy_canvas_container.height() - 8, 500)
        fig  = Figure(figsize=(_cw / _dpi, _ch / _dpi), dpi=_dpi)
        ax   = fig.add_subplot(111)

        sc   = ax.scatter(xs, ys, s=12, alpha=0.7,
                          c=range(len(xs)), cmap="plasma")
        ax.set_xlabel(x_label, fontsize=10)
        ax.set_ylabel(y_label, fontsize=10)
        ax.set_title(f"{x_label}  vs  {y_label}")
        fig.colorbar(sc, ax=ax, label="Sample index")
        ax.grid(True, alpha=0.3)

        canvas = FigureCanvasQTAgg(fig)
        layout = self._xy_canvas_container.layout()
        if layout: layout.addWidget(canvas)
        self._xy_canvas = canvas
        self._xy_figure = fig
        canvas.draw()

        self._xy_rect_selector = RectangleSelector(
            ax, self._on_xy_rect_select, useblit=False, button=3,
            props=dict(alpha=0.2, facecolor="#FF6600"))

        self._lbl_xy_info.setText(f"{len(xs)} points plotted.")

    def _on_xy_rect_select(self, eclick, erelease):
        if not self._xy_figure or not self._xy_figure.axes: return
        ax = self._xy_figure.axes[0]
        x0, x1 = sorted([eclick.xdata, erelease.xdata])
        y0, y1 = sorted([eclick.ydata, erelease.ydata])
        if x1 - x0 < 1e-12 or y1 - y0 < 1e-12: return
        self._xy_zoom_history.append((ax.get_xlim(), ax.get_ylim()))
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
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
            self._xy_zoom_history = []
            self._xy_canvas.draw_idle()
            self._lbl_xy_info.setText("XY cleared — points removed.")
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
        self._xy_figure = None; self._xy_rect_selector = None

    # ── PV Time Plot ────────────────────────────────────────────────────────

    def _plot_pv_time(self):
        try:
            self._plot_pv_time_impl()
        except Exception:
            import traceback
            self._log(f"[plot_pv_time]\n{traceback.format_exc()}")
            try:
                self._clear_pv_time_plot()
            except Exception:
                pass
            QMessageBox.warning(
                self, "PV Time Plot",
                "Could not build the PV-time plot — see Log tab.\n"
                "(Reading the ramping repository may require the 'pyarrow' package.)")

    def _plot_pv_time_impl(self):
        from cpva_core import load_ramping_repository
        y_col  = self._pv_time_y_combo.currentText()
        t_from = self._pv_time_from_edit.text().strip()
        t_to   = self._pv_time_to_edit.text().strip()
        mode   = self._pv_time_mode_combo.currentText()

        try:
            dt_from = datetime.strptime(t_from, "%Y-%m-%d")
            dt_to   = datetime.strptime(t_to,   "%Y-%m-%d")
        except ValueError:
            QMessageBox.warning(self, "Date error", "Use YYYY-MM-DD format."); return

        repo = self._ramping_repository
        if not repo:
            QMessageBox.information(self, "No repository",
                                    "No ramping repository loaded."); return

        rows = [r for r in repo
                if dt_from <= r.get("dt", datetime.min) <= dt_to]

        if not rows:
            QMessageBox.information(self, "No data",
                                    "No repository rows in range."); return

        self._clear_pv_time_plot()
        _dpi = 96
        _cw  = max(self._pv_time_canvas_container.width()  - 8, 800)
        _ch  = max(self._pv_time_canvas_container.height() - 8, 500)
        fig  = Figure(figsize=(_cw / _dpi, _ch / _dpi), dpi=_dpi)
        ax   = fig.add_subplot(111)

        if mode == "Daily distribution":
            self._draw_daily_distribution(ax, rows, y_col, dt_from, dt_to)
        else:
            dates = [r.get("dt", datetime.min) for r in rows]
            vals  = []
            for r in rows:
                v = r.get(y_col)
                try: vals.append(float(v))
                except (TypeError, ValueError): vals.append(float("nan"))
            ax.plot(dates, vals, "o-", ms=4, color="#1565C0", linewidth=1.2)
            ax.set_ylabel(y_col); ax.set_xlabel("Date"); ax.grid(True, alpha=0.3)
            fig.autofmt_xdate()

        canvas = FigureCanvasQTAgg(fig)
        layout = self._pv_time_canvas_container.layout()
        if layout: layout.addWidget(canvas)
        self._pv_time_canvas = canvas
        self._pv_time_figure = fig
        canvas.draw()

    def _draw_daily_distribution(self, ax, rows, y_col, dt_from, dt_to):
        from collections import defaultdict
        daily = defaultdict(list)
        for r in rows:
            dt = r.get("dt")
            if not dt: continue
            v  = r.get(y_col)
            try: daily[dt.date()].append(float(v))
            except (TypeError, ValueError): pass
        days  = sorted(daily.keys())
        means = [sum(daily[d]) / len(daily[d]) if daily[d] else 0 for d in days]
        stds  = [(sum((x - m) ** 2 for x in daily[d]) / max(len(daily[d]), 1)) ** 0.5
                 for d, m in zip(days, means)]
        ax.bar(days, means, yerr=stds, capsize=3, color="#1565C0", alpha=0.7)
        ax.set_ylabel(y_col + " (daily mean ± σ)")
        ax.set_xlabel("Date")
        ax.grid(True, alpha=0.3, axis="y")
        ax.tick_params(axis="x", rotation=45)

    def _clear_pv_time_plot(self):
        if self._pv_time_canvas is not None:
            layout = self._pv_time_canvas_container.layout()
            if layout:
                layout.removeWidget(self._pv_time_canvas)
            self._pv_time_canvas.setParent(None)
            self._pv_time_canvas.deleteLater()
            self._pv_time_canvas = None
        self._pv_time_figure = None

    def _pv_time_add_features(self):
        pass

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
        idx = self._preset_combo.currentIndex()
        if idx < 0 or idx >= len(self._presets): return
        p = self._presets[idx]
        self._pv_list.clear()
        for pv in p.get("pvs", []):
            self._pv_list.addItem(pv)
        self._sync_pv_list_customs()   # keep custom rows pinned at the bottom
        self._update_pv_count()
        dt_from = parse_user_datetime(p.get("time_from", ""))
        dt_to   = parse_user_datetime(p.get("time_to",   ""))
        if dt_from: self._dt_from = dt_from
        if dt_to:   self._dt_to   = dt_to
        if p.get("master_pv") and self._master_pv_edit is not None:
            self._master_pv_edit.setText(p["master_pv"])
        if "master_multiple" in p and self._master_multiple_edit is not None:
            self._master_multiple_edit.setText(str(p["master_multiple"]))
        self._refresh_time_labels()
        self._log(f"Preset '{p['name']}' loaded.")
        # Reflect the new PV set in the graph immediately, honouring the current
        # mode: restart the live window, or reload the historical window — so the
        # user no longer has to press LOAD DATA after switching presets.
        if self._live_mode:
            self._live_timer.stop()
            self._countdown_timer.stop()
            self._live_window_span = self._live_span_from_window()
            self._live_last_ts = None
            self._live_init_retries = 0
            self._zoom_history.clear()
            self._lbl_status.setText("Live: reloading preset…")
            self._live_initial_load()
        else:
            self._on_load_clicked()

    def _save_preset(self):
        idx = self._preset_combo.currentIndex()
        if idx < 0:
            self._save_preset_as(); return
        pvs  = self._real_pv_names()
        name = self._presets[idx].get("name", "?")
        self._presets[idx] = {
            "name": name, "pvs": pvs,
            "time_from": self._dt_from.strftime("%Y-%m-%d %H:%M"),
            "time_to":   self._dt_to.strftime("%Y-%m-%d %H:%M"),
        }
        save_presets(self._presets)
        self._log(f"Preset '{name}' saved.")

    def _save_preset_as(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip(): return
        pvs  = self._real_pv_names()
        self._presets.append({
            "name": name.strip(), "pvs": pvs,
            "time_from": self._dt_from.strftime("%Y-%m-%d %H:%M"),
            "time_to":   self._dt_to.strftime("%Y-%m-%d %H:%M"),
        })
        save_presets(self._presets)
        self._refresh_preset_combo()
        self._preset_combo.setCurrentIndex(len(self._presets) - 1)
        self._log(f"Preset '{name.strip()}' created.")

    def _delete_preset(self):
        idx = self._preset_combo.currentIndex()
        if idx < 0 or idx >= len(self._presets): return
        name = self._presets[idx].get("name", "?")
        ans  = QMessageBox.question(
            self, "Delete preset", f"Delete preset '{name}'?")
        if ans != QMessageBox.StandardButton.Yes: return
        self._presets.pop(idx)
        save_presets(self._presets)
        self._refresh_preset_combo()
        self._log(f"Preset '{name}' deleted.")

    # ── PV list operations ───────────────────────────────────────────────────

    def _open_pv_browser(self):
        dlg = PVBrowserDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            added = False
            for pv in dlg.selected_pvs:
                # avoid duplicates
                if pv not in self._real_pv_names():
                    self._pv_list.addItem(pv)
                    added = True
            self._sync_pv_list_customs()   # keep custom rows pinned at the bottom
            self._update_pv_count()
            if added and self._live_mode:
                # A PV added mid-session has no history yet. The live tick only
                # fetches from the last-seen timestamp forward, so the new PV used
                # to stay blank until Live was stopped and LOAD DATA pressed. Re-run
                # the live initial load so it is fetched over the whole window right
                # away (all PVs load concurrently in one pool, so this is quick).
                self._live_timer.stop()
                self._countdown_timer.stop()
                self._live_last_ts = None
                self._lbl_status.setText("Live: loading added PV…")
                self._live_initial_load()

    def _remove_selected_pvs(self):
        for item in reversed(self._pv_list.selectedItems()):
            self._pv_list.takeItem(self._pv_list.row(item))
        self._update_pv_count()

    def _clear_pv_list(self):
        ans = QMessageBox.question(self, "Clear PVs", "Remove all PVs from list?")
        if ans == QMessageBox.StandardButton.Yes:
            self._pv_list.clear()
            self._sync_pv_list_customs()   # keep custom PVs on show after a clear
            self._update_pv_count()

    def _on_pv_double_click(self, item):
        if item.data(Qt.ItemDataRole.UserRole):
            return                         # custom/divider row — not editable here
        pv, ok = QInputDialog.getText(self, "Edit PV", "PV name:", text=item.text())
        if ok and pv.strip():
            item.setText(pv.strip())

    def _real_pv_names(self):
        """The real archiver PVs queued for fetching — the only ones sent to the
        archiver, saved into presets, or counted. Custom (derived) PVs are shown
        in the list too but tagged (UserRole set), so they are skipped here."""
        return [self._pv_list.item(i).text()
                for i in range(self._pv_list.count())
                if not self._pv_list.item(i).data(Qt.ItemDataRole.UserRole)]

    def _sync_pv_list_customs(self):
        """Mirror the defined custom PVs into the PV LIST (above LOAD DATA) as
        read-only, visually distinct rows so they sit alongside the real PVs.
        Source of truth stays ``self._custom_pvs``; these rows are tagged and
        excluded from every fetch/save path via :meth:`_real_pv_names`."""
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
            if self._live_mode:
                # Live mode tracks "now": the chosen window sets the live span
                # (graph width). Restart the live load so the new span takes
                # effect and data is re-fetched immediately.
                self._live_timer.stop()
                self._countdown_timer.stop()
                self._live_window_span = self._live_span_from_window()
                self._live_last_ts = None
                self._zoom_history.clear()
                self._lbl_status.setText("Live: reloading window…")
                self._live_initial_load()
            else:
                # Not live: load the newly chosen window now.
                self._on_load_clicked()

    # ── Conditions dialog ────────────────────────────────────────────────────

    def _open_conditions_dialog(self):
        # Custom channels are filtered exactly like real PVs (they are computed
        # before the conditions run), so they belong in the dropdown too.
        pvs = self._real_pv_names() + [d["name"] for d in self._custom_pvs
                                       if d.get("name")]
        dlg = _ConditionsDialog(self._conditions, pvs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._conditions = dlg.result_conditions
            self._cond_last_diag = None     # report the new set's effect again
            rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
            self._table_rows = self._apply_conditions_to_rows(rows)
            self._populate_table()
            # The graph draws from _table_rows as well — without this it would
            # keep showing the rows the new conditions just discarded.
            if self._samples_by_pv:
                self._plot_graph()

    # ── Reference lines dialog ───────────────────────────────────────────────

    def _open_ref_lines_dialog(self):
        dlg = _RefLinesDialog(self._ref_lines, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._ref_lines = dlg.result_lines
            if self._samples_by_pv:
                self._plot_graph()

    # ── Custom PV dialog ─────────────────────────────────────────────────────

    def _open_custom_pv_dialog(self):
        dlg = _CustomPVDialog(self._custom_pvs, self._cpv_dialog_channels(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
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
                self._plot_graph()
                self._refresh_axis_settings_tv()
                self._populate_table()
                self._refresh_xy_choices()

    # ── CSV Export ───────────────────────────────────────────────────────────

    def _export_csv(self):
        if not self._table_rows:
            QMessageBox.information(self, "No data", "No data to export."); return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "", "CSV (*.csv)")
        if not path: return
        try:
            pvs   = self._pv_order
            heads = ["Timestamp"] + [shorten_pv_name(p) for p in pvs]
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write("sep=;\n")
                f.write(";".join(heads) + "\n")
                for ts, row_dict in self._table_rows:
                    row_vals = [ns_to_local_str(ts)]
                    for pv in pvs:
                        v_raw, _ = row_dict.get(pv, (None, ""))
                        row_vals.append("" if v_raw is None else
                                        str(v_raw).replace(",", "."))
                    f.write(";".join(row_vals) + "\n")
            self._log(f"Exported {len(self._table_rows)} rows to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export error", str(exc))

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


# ── Conditions Dialog ────────────────────────────────────────────────────────

class _ConditionsDialog(QDialog):
    def __init__(self, conditions, pvs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter Conditions")
        self.resize(600, 350)
        self._rows: list = []
        self.result_conditions: list = []

        lay = QVBoxLayout(self)
        lbl = QLabel("Rows are kept only if every condition PV is present and "
                     "its value lies within [Min, Max]; anything outside is "
                     "discarded from the table, graph and export. Leave Min or "
                     "Max blank for an open bound. A condition on a PV with no "
                     "data in the loaded window is skipped, not applied.")
        lbl.setStyleSheet("color:#555;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        # Column header row
        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0)
        h_pv  = QLabel("PV");  h_pv.setStyleSheet("font-weight:700;color:#1565C0;")
        h_min = QLabel("Min"); h_min.setStyleSheet("font-weight:700;color:#1565C0;"); h_min.setFixedWidth(90)
        h_max = QLabel("Max"); h_max.setStyleSheet("font-weight:700;color:#1565C0;"); h_max.setFixedWidth(90)
        head.addWidget(h_pv, stretch=1); head.addWidget(h_min); head.addWidget(h_max)
        head.addSpacing(30)
        lay.addLayout(head)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget()
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setSpacing(4)
        self._inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, stretch=1)

        self._pvs = pvs
        for c in conditions:
            self._add_row(pvs, c)

        ctrl = QHBoxLayout()
        b_add = QPushButton("+ Add"); b_add.clicked.connect(lambda: self._add_row(pvs))
        b_clear = QPushButton("Clear all"); b_clear.clicked.connect(self._clear_all)
        ctrl.addWidget(b_add); ctrl.addWidget(b_clear); ctrl.addStretch()
        lay.addLayout(ctrl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def _num_str(v):
        if v is None:
            return ""
        return str(v)

    def _add_row(self, pvs, existing=None):
        row_w = QWidget()
        rl    = QHBoxLayout(row_w); rl.setContentsMargins(0,0,0,0)
        pv_cb = QComboBox()
        pv_cb.setEditable(True); pv_cb.addItems(pvs)
        if existing: pv_cb.setCurrentText(existing.get("pv", ""))
        min_e = QLineEdit(self._num_str(existing.get("min")) if existing else "")
        min_e.setFixedWidth(90); min_e.setPlaceholderText("min")
        max_e = QLineEdit(self._num_str(existing.get("max")) if existing else "")
        max_e.setFixedWidth(90); max_e.setPlaceholderText("max")
        b_del = QPushButton("✕"); b_del.setFixedWidth(26)
        b_del.setStyleSheet("QPushButton{background:#B71C1C;color:white;border-radius:3px;}")
        b_del.clicked.connect(lambda: self._remove_row(row_w, rec))
        rl.addWidget(pv_cb, stretch=1); rl.addWidget(min_e); rl.addWidget(max_e)
        rl.addWidget(b_del)
        rec = {"widget": row_w, "pv": pv_cb, "min": min_e, "max": max_e}
        self._rows.append(rec)
        # Insert before the stretch
        self._inner_lay.insertWidget(self._inner_lay.count() - 1, row_w)

    def _remove_row(self, row_w, rec):
        self._rows.remove(rec)
        row_w.deleteLater()

    def _clear_all(self):
        for rec in list(self._rows):
            self._remove_row(rec["widget"], rec)

    def _accept(self):
        def _parse(txt):
            txt = txt.strip()
            if not txt:
                return None
            try:
                return float(txt)
            except ValueError:
                return None
        result = []
        for r in self._rows:
            pv = r["pv"].currentText().strip()
            if not pv:
                continue
            result.append({
                "pv":  pv,
                "min": _parse(r["min"].text()),
                "max": _parse(r["max"].text()),
            })
        self.result_conditions = result
        self.accept()


# ── Reference Lines Dialog ──────────────────────────────────────────────────

class _RefLinesDialog(QDialog):
    def __init__(self, ref_lines, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reference Lines")
        self.resize(440, 300)
        self._rows: list = []
        self.result_lines: list = []

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Add horizontal reference lines to the graph:"))

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
        ctrl.addWidget(b_add); ctrl.addStretch()
        lay.addLayout(ctrl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add_row(self, existing=None):
        row_w = QWidget()
        rl    = QHBoxLayout(row_w); rl.setContentsMargins(0,0,0,0)
        lbl_e = QLineEdit(existing.get("label","") if existing else ""); lbl_e.setFixedWidth(120)
        lbl_e.setPlaceholderText("Label")
        y_e   = QLineEdit(str(existing.get("y","")) if existing else ""); y_e.setFixedWidth(80)
        y_e.setPlaceholderText("Y value")
        col_btn = QPushButton(); col_btn.setFixedWidth(36)
        col_hex = existing.get("color","#FF5722") if existing else "#FF5722"
        col_btn.setStyleSheet(f"background:{col_hex};border-radius:3px;")
        col_btn.clicked.connect(lambda: self._pick_color(col_btn, rec))
        b_del = QPushButton("✕"); b_del.setFixedWidth(26)
        b_del.setStyleSheet("QPushButton{background:#B71C1C;color:white;border-radius:3px;}")
        b_del.clicked.connect(lambda: self._remove_row(row_w, rec))
        rl.addWidget(lbl_e); rl.addWidget(y_e); rl.addWidget(col_btn); rl.addWidget(b_del)
        rec = {"widget": row_w, "label": lbl_e, "y": y_e, "color": col_hex, "btn": col_btn}
        self._rows.append(rec)
        self._inner_lay.insertWidget(self._inner_lay.count() - 1, row_w)

    def _pick_color(self, btn, rec):
        col = QColorDialog.getColor(QColor(rec["color"]), self, "Pick color")
        if col.isValid():
            rec["color"] = col.name()
            btn.setStyleSheet(f"background:{col.name()};border-radius:3px;")

    def _remove_row(self, row_w, rec):
        self._rows.remove(rec)
        row_w.deleteLater()

    def _accept(self):
        result = []
        for rec in self._rows:
            try:
                y = float(rec["y"].text())
            except ValueError:
                continue
            result.append({"label": rec["label"].text().strip(),
                           "y": y, "color": rec["color"]})
        self.result_lines = result
        self.accept()


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
        self.w_gap    = _spin(0, 60, o.get("axis_gap_px", 4), "px",
                              tip="Whitespace between an axis title and the axis "
                                  "to its left — this is the gap that made the "
                                  "columns wide")
        self.w_pad    = _spin(-8, 40, o.get("label_pad_px", 4), "px",
                              tip="Gap between an axis title and its own tick "
                                  "numbers. Negative values pull the title into "
                                  "the empty margin around the rotated numbers, "
                                  "which is the last bit of slack there is.")
        self.w_outer  = _spin(0, 80, o.get("outer_margin_px", 6), "px",
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
        self.w_xticks = _spin(2, 24, o.get("x_ticks_max", 8),
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
        self._emit_apply()

    def _accept(self):
        self.result_opts = self._collect()
        self.accept()


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

        # ── Reference table: which letter is which PV ────────────────────────
        ref_box = QGroupBox("Available channels — letters follow the loaded PV list")
        ref_lay = QVBoxLayout(ref_box)
        if channels:
            ref_tbl = QTableWidget(len(channels), 4)
            ref_tbl.setHorizontalHeaderLabels(["Var", "Channel", "PV name", "State"])
            ref_tbl.verticalHeader().setVisible(False)
            ref_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            ref_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            ref_tbl.setMaximumHeight(150)
            for r, (letter, pv, disp, loaded) in enumerate(channels):
                it_l = QTableWidgetItem(letter)
                it_l.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cells = [it_l, QTableWidgetItem(disp), QTableWidgetItem(pv),
                         QTableWidgetItem("loaded" if loaded else "not loaded")]
                for c, it in enumerate(cells):
                    if not loaded:
                        # Text only — the row must stay readable on the light
                        # table background.
                        it.setForeground(QColor("#B71C1C"))
                    ref_tbl.setItem(r, c, it)
            ref_tbl.setColumnWidth(0, 50)
            ref_tbl.setColumnWidth(3, 80)
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
        self._bind_tbl.setMaximumHeight(230)
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
        tbl.setRowCount(0)
        # Only channels that actually have a letter can be picked — a letter is
        # how the expression names them. Already-saved custom PVs are in here
        # too (they are part of the channel list), so one custom PV can be built
        # on another; one added in this dialog gets its letter after OK.
        options = [pv for _lt, pv, _d, _ld in self._channels]
        for rec in self._rows:
            name = rec["name"].text().strip() or "(unnamed)"
            for letter in _cpv_vars(rec["expr"].text()):
                pv     = self._pv_by_letter.get(letter)
                loaded = self._loaded_by_pv.get(pv, True) if pv else False
                row = tbl.rowCount(); tbl.insertRow(row)

                it_name = QTableWidgetItem(name)
                it_name.setToolTip(name)       # the column elides long names
                it_var  = QTableWidgetItem(letter)
                it_var.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if pv is None:
                    state = "no channel"
                elif not loaded:
                    state = "not loaded"
                else:
                    state = "loaded"
                it_state = QTableWidgetItem(state)
                for c, it in enumerate((it_name, it_var, None, it_state)):
                    if it is None:
                        continue
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
        rec = {"widget": row_w, "name": name_e, "expr": expr_e, "warn": warn}
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

    def _remove_row(self, row_w, rec):
        self._rows.remove(rec)
        row_w.deleteLater()
        self._queue_bindings_refresh()

    def _accept(self):
        out = []
        for r in self._rows:
            name = r["name"].text().strip()
            expr, bindings = _cpv_from_display(r["expr"].text(), self._pv_by_letter)
            if name and expr:
                out.append({"name": name, "expr": expr, "bindings": bindings})
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


# Note: do NOT add a WM_SETICON / SetClassLongPtr "force taskbar icon" helper
# here. Measured on Win11: with the window icon and the window-class icon
# deliberately set to two different images, the taskbar draws the *window*
# icon, so Qt's setWindowIcon is already sufficient. The real bug was that
# _HERE is not frozen-aware, so the frozen build never found icon.ico at all.


# ── Combined main window ─────────────────────────────────────────────────────

class CPVASuiteWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSS Logger")
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
        self._css_logger = CSSLoggerWidget()
        tabs.addTab(self._css_logger, "  CSS Logger  ")
        try:
            from sp_t import SpectraWidget
            self._spectra = SpectraWidget()
            tabs.addTab(self._spectra, "  Spectra  ")
        except Exception as exc:
            err_w = QLabel(f"Spectra tab unavailable:\n{exc}")
            err_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            err_w.setStyleSheet("color:#B71C1C;font-size:13px;")
            tabs.addTab(err_w, "  Spectra (error)  ")
        self.setCentralWidget(tabs)

        status = self.statusBar()
        status.showMessage("CPVA Suite ready.")
        status.setStyleSheet("QStatusBar{background:#E3F2FD;color:#555;font-size:11px;}")

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
            self._css_logger._save_runtime_state()
        except Exception:
            pass
        try:
            cfg = load_config()
            g   = self.geometry()
            cfg["suite_geometry"] = [g.x(), g.y(), g.width(), g.height()]
            save_config(cfg)
        except Exception:
            pass
        super().closeEvent(event)


# ── Entry point ──────────────────────────────────────────────────────────────

def _run():
    try:
        import ctypes as _ct
        _ct.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ELI.CSSLogger")
    except Exception:
        pass
    app = QApplication.instance() or QApplication(sys.argv)
    _ico = _icon_file()
    if _ico:
        app.setWindowIcon(QIcon(str(_ico)))   # taskbar + alt-tab
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
    win = CPVASuiteWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    _run()