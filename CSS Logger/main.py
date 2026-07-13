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
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from datetime import datetime, timedelta, timezone

import numpy as np

# ── PySide6 ────────────────────────────────────────────────────────────────
from PySide6.QtCore import (
    Qt, QObject, QTimer, Signal, QDate, QLocale,
)
from PySide6.QtGui import (
    QColor, QIcon, QPalette, QPainter, QPen,
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
    QTextEdit, QProgressBar,
)

# ── matplotlib ─────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("QtAgg")
matplotlib.rcParams["axes.facecolor"]   = "white"
matplotlib.rcParams["figure.facecolor"] = "white"
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector, RectangleSelector

# ── Non-UI helpers from cpva_core ──────────────────────────────────────────
from cpva_core import (
    TZ_PRAGUE, now_ns, dt_to_ns, ns_to_local_str, _fmt_cursor_value,
    parse_user_datetime, shorten_pv_name, _matches_wildcard,
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

    def _date_for_index(self, index):
        if index.row() == 0:
            return None
        year, month = self._cal.yearShown(), self._cal.monthShown()
        first = QDate(year, month, 1)
        start = first.addDays(-(first.dayOfWeek() - 1))
        return start.addDays((index.row() - 1) * 7 + index.column())

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
        is_weekend = index.column() in (5, 6)
        d = self._date_for_index(index)
        if d is None:
            super().paint(painter, option, index)
            return
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
        lay.addWidget(QLabel("Search for a CPVA channel (type part of the name):"))
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("e.g. HAPLS-ENER or L3-SPFE or Energy")
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

    def _filter(self, text: str):
        self._lst.clear()
        q = text.strip().lower()
        shown = 0
        for ch in self._all_channels:
            if not q or q in ch.lower():
                self._lst.addItem(ch)
                shown += 1
                if shown >= 500:
                    break

    def _on_add(self):
        items = self._lst.selectedItems()
        if not items:
            return
        self.selected_pvs = [it.text() for it in items]
        self.accept()

# ── CSSLoggerWidget ────────────────────────────────────────────────────────

class CSSLoggerWidget(QWidget):
    """Full PySide6 port of CPVAExplorerApp from cssl.py."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_state()
        self._build_ui()
        self._populate_ui()
        # Graph tab defaults to live mode — start it shortly after the window shows
        # (deferred so the UI is fully realised and the Operation preset PVs are loaded).
        QTimer.singleShot(600, self._maybe_autostart_live)

    def _maybe_autostart_live(self):
        if self._live_mode:
            return
        pvs = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
        if pvs:
            self._toggle_live_mode()

    # ── State init ─────────────────────────────────────────────────────────

    def _init_state(self):
        self.config = load_config()
        self._samples_by_pv: dict = {}
        self._table_rows:    list = []
        self._pv_order:      list = []
        self._base_pv_order: list = []
        self._last_pv_search: str = ""
        self._col_full_names: dict = {}
        self._presets:           list = load_presets()
        self._condition_presets: list = load_condition_presets()
        self._custom_pvs:        list = load_custom_pvs()
        self._compile_custom_pvs()
        self._repository_overlays = []
        self._repository_artists  = []
        self._custom_pv_counter   = 1
        self._mpl_canvas  = None
        self._mpl_figure  = None
        self._graph_axes  = []
        self._graph_lines: list = []
        self._graph_pvs:   list = []
        self._graph_raw:   list = []
        self._graph_raw_np: list = []
        self._graph_spine_xpos: list = []
        self._span_selector  = None
        self._zoom_selector  = None
        self._zoom_history:  list = []
        self._crosshair_vlines = []
        self._crosshair_hlines = []
        self._crosshair_texts  = []
        self._x_cursor_ann     = None
        self._crosshair_cid    = None
        self._blit_bg          = None
        self._mouse_pending    = False
        self._mouse_last_event = None
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
        self._live_autoscroll  = True
        self._live_programmatic_scroll = False
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
        self._ramping_repository = load_ramping_repository()
        self._data_repository: list = []
        self._ref_lines: list      = []
        self._pre_window_vals: dict = {}  # pv → (ts_ns, value, units) before window start
        self._plot_window_ns = None       # explicit carry-forward window (live mode)
        self._conditions: list = copy(self.config.get("conditions", []))
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
    # ── Graph tab ──────────────────────────────────────────────────────────

    def _build_graph_tab(self):
        tab = self._tab_graph
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        # Controls row
        ctrl = QHBoxLayout()
        lay.addLayout(ctrl)

        b_clean = QPushButton("Clean graph"); b_clean.clicked.connect(self._clean_graph); ctrl.addWidget(b_clean)
        b_save  = QPushButton("Save graph"); b_save.clicked.connect(self._save_graph); ctrl.addWidget(b_save)
        self._btn_zoom_back = QPushButton("Back")
        self._btn_zoom_back.clicked.connect(self._zoom_back)
        self._btn_zoom_back.setEnabled(False)
        ctrl.addWidget(self._btn_zoom_back)
        b_ref  = QPushButton("Reference lines"); b_ref.clicked.connect(self._open_ref_lines_dialog); ctrl.addWidget(b_ref)
        b_cond = QPushButton("Conditions"); b_cond.clicked.connect(self._open_conditions_dialog); ctrl.addWidget(b_cond)
        b_cpv  = QPushButton("Add custom PV"); b_cpv.clicked.connect(self._open_custom_pv_dialog); ctrl.addWidget(b_cpv)

        ctrl.addWidget(QLabel("Font:"))
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(5, 24); self._font_size_spin.setValue(11); self._font_size_spin.setFixedWidth(48)
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

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet("font-size:10px;padding:2px 6px;")
        self._stats_label.setWordWrap(True)
        top_lay.addWidget(self._stats_label)

        self._graph_v_splitter.addWidget(top_pane)

        # Bottom pane: axis settings table
        axis_pane = QWidget()
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
        hdr.addWidget(h)
        b_apply = QPushButton("Apply changes")
        b_apply.clicked.connect(self._apply_axis_settings)
        hdr.addStretch()
        hdr.addWidget(b_apply)
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
        self._axis_tv.setStyleSheet(
            "QTableWidget{background:#ffffff;}"
            "QTableWidget QTableCornerButton::section{background:#ffffff;}")
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

        # Color swatch delegate on column 3
        self._color_delegate = _ColorSwatchDelegate(self._axis_tv)
        self._color_delegate.color_changed.connect(self._on_axis_color_changed)
        self._axis_tv.setItemDelegateForColumn(3, self._color_delegate)

        self._axis_tv.cellDoubleClicked.connect(self._on_axis_tv_double_click)
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
        b_clean = QPushButton("Clean XY"); b_clean.clicked.connect(self._clear_xy_plot); ctrl.addWidget(b_clean)
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

        self._update_pv_count()
        self._refresh_time_labels()

    def _refresh_time_labels(self):
        self._lbl_tw_from.setText("From: " + self._dt_from.strftime("%Y-%m-%d  %H:%M"))
        self._lbl_tw_to.setText(  "To:   " + self._dt_to.strftime("%Y-%m-%d  %H:%M"))
        if self._live_mode:
            self._lbl_tw_live.setText("Live: ON")
            self._lbl_tw_live.setStyleSheet("color:#2E7D32;font-size:11px;font-weight:700;")
        else:
            self._lbl_tw_live.setText("Live: OFF")
            self._lbl_tw_live.setStyleSheet("color:#555;font-size:11px;")

    # ── Graph plotting ──────────────────────────────────────────────────────

    @staticmethod
    def _band_ylim(idx, n, dmin, dmax, autoscale):
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
            margin = 0.12 * band_h                 # gap so traces don't touch band edges
            f_top  = 1.0 - idx * band_h - margin
            f_bot  = 1.0 - (idx + 1) * band_h + margin
        denom = (f_top - f_bot) or 1.0
        span  = dmax - dmin
        lo = dmin - f_bot / denom * span
        hi = dmax + (1.0 - f_top) / denom * span
        return lo, hi

    def _plot_graph(self):
        """Safe wrapper: a plotting failure must never crash the app or leave a
        stale graph — clear the canvas and show a message instead."""
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

        try:
            _fsize = max(5, self._font_size_spin.value())
        except Exception:
            _fsize = 7

        # ── Single banded graph: ONE plot rectangle, one left Y axis per PV.
        # Each PV occupies its own vertical band (see _band_ylim); a PV with
        # Autoscale ticked spans the full graph height instead. Grid / zoom / pan
        # act on the whole graph at once.
        _fig_w_px = _cw
        _axis_px  = _fsize * 1.9 + 25   # room for one left axis column (label + ticks + gap)
        STEP_fig  = max(0.020, _axis_px / _fig_w_px)

        BASE_L = 0.04; BASE_R = 0.015
        left_margin  = min(0.60, BASE_L + (n - 1) * STEP_fig)
        right_margin = 1.0 - BASE_R
        fig.subplots_adjust(left=left_margin, right=right_margin, top=0.97, bottom=0.12)

        axes_width = max(0.05, right_margin - left_margin)
        STEP_ax    = STEP_fig / axes_width

        axes = [fig.add_subplot(111)]
        for _ in range(1, n):
            axes.append(axes[0].twinx())

        # Order the Y-axis columns left → right in PV order (first PV = leftmost
        # column, last PV nearest the plot) so reading the axes left-to-right
        # matches the top-to-bottom order of the traces and the table.
        self._graph_spine_xpos = []
        for i, ax in enumerate(axes):
            ax.yaxis.set_label_position("left"); ax.yaxis.tick_left()
            xfrac = -(n - 1 - i) * STEP_ax
            ax.spines["left"].set_position(("axes", xfrac))
            if i > 0:
                ax.spines["right"].set_visible(False)  # twins must not draw over the plot
            self._graph_spine_xpos.append((xfrac, "left"))
        ax_x = axes[0]
        self._graph_x_ax = ax_x

        def _moving_avg(vals, w):
            if w <= 1 or len(vals) < w: return vals
            padded = vals[:w-1][::-1] + vals
            return [sum(padded[j:j+w]) / w for j in range(len(vals))]

        all_times = []
        self._graph_lines = []; self._graph_pvs = list(pvs_for_axes); self._graph_raw = []

        for i, (pv, ax) in enumerate(zip(pvs_for_axes, axes)):
            pairs = []
            for ts_ns, row_dict in self._table_rows:
                if pv not in row_dict: continue
                value, _ = row_dict[pv]
                if isinstance(value, (int, float)):
                    pairs.append((ts_ns, value))

            # Downsample: time-binned mean to the user's target (keeps the trace
            # shape while cutting point count). 0 = off → only a hard safety cap.
            avg_target = self._avg_target_points()
            if avg_target > 0:
                pairs = self._downsample_pairs_mean(pairs, avg_target)
            else:
                MAX_PTS = 25000
                if len(pairs) > MAX_PTS:
                    step = max(1, len(pairs) // MAX_PTS)
                    pairs = pairs[::step]

            # Carry-forward / hold (like CS Studio): fill gaps at the window
            # edges so the trace spans the whole window instead of breaking off.
            # • Prepend the last value seen BEFORE the window at the start edge.
            # • Extend the last in-window value flat out to the window end.
            if _use_window:
                pre = self._pre_window_vals.get(pv)
                pre_val = pre[1] if (pre and isinstance(pre[1], (int, float))) else None
                if not pairs:
                    if pre_val is not None:
                        pairs = [(_win_start_ns, pre_val), (_win_end_ns, pre_val)]
                else:
                    if pre_val is not None and pairs[0][0] > _win_start_ns:
                        pairs.insert(0, (_win_start_ns, pre_val))
                    if pairs[-1][0] < _win_end_ns:
                        pairs.append((_win_end_ns, pairs[-1][1]))

            times  = [datetime.fromtimestamp(ts / 1e9, tz=timezone.utc) for ts, v in pairs]
            values = [float("nan") if v is None else v for ts, v in pairs]
            all_times.extend(times)

            pv_setting = self._pv_settings.get(pv, self._get_pv_default_settings(pv, i))
            line_color = pv_setting.get("color", _GRAPH_COLORS[i % len(_GRAPH_COLORS)])
            line_width = pv_setting.get("width", None)
            pv_smooth  = pv_setting.get("smooth", None)
            disp_name  = pv_setting.get("display_name", shorten_pv_name(pv))
            visible    = pv_setting.get("show", True)
            eff_smooth = pv_smooth if pv_smooth is not None else 1

            times_num = mdates.date2num(times)
            self._graph_raw.append((times_num, values))

            if eff_smooth > 1 and len(values) >= eff_smooth:
                lw_raw = (line_width * 0.5) if line_width is not None else 0.8
                raw_line, = ax.plot(times, values, color=line_color, linewidth=lw_raw,
                                    alpha=0.3, drawstyle="steps-post", zorder=1)
                raw_line.set_visible(visible)
                smoothed = _moving_avg(values, eff_smooth)
                lw_sm = line_width if line_width is not None else 1.8
                sm_line, = ax.plot(times, smoothed, color=line_color, linewidth=lw_sm,
                                   drawstyle="steps-post",
                                   label=f"{disp_name} (avg {eff_smooth})", zorder=2)
                sm_line.set_visible(visible)
                self._graph_lines.append([raw_line, sm_line])
            else:
                lw = line_width if line_width is not None else 1.2
                line, = ax.plot(times, values, color=line_color, linewidth=lw,
                                marker="." if len(times) < 200 else None, markersize=3,
                                drawstyle="steps-post", label=disp_name, zorder=2)
                line.set_visible(visible)
                self._graph_lines.append([line])

            # Rotated (vertical) Y label + tick numbers, one narrow column per PV.
            ax.set_ylabel(disp_name, color=line_color, fontsize=_fsize, rotation=90, labelpad=1)
            ax.tick_params(axis="y", labelcolor=line_color, labelsize=max(5, _fsize - 1),
                           pad=2, labelrotation=90)
            from matplotlib.ticker import AutoMinorLocator, MaxNLocator
            ax.yaxis.set_major_locator(MaxNLocator(6))
            ax.yaxis.set_minor_locator(AutoMinorLocator(5))
            ax.tick_params(axis="y", which="minor", length=3, labelsize=0)
            # No floating "1e6" multiplier — show plain tick numbers instead.
            try:
                ax.ticklabel_format(axis="y", style="plain", useOffset=False)
            except Exception:
                pass

            # Position this PV's data into its vertical band (or full height if
            # Autoscale is on). Manual Y min/max override the data range that is
            # mapped into the band.
            ymin_pv = pv_setting.get("ymin"); ymax_pv = pv_setting.get("ymax")
            vals_clean = [v for v in values if v == v]   # drop NaNs
            if vals_clean:
                dmin, dmax = min(vals_clean), max(vals_clean)
            else:
                dmin, dmax = 0.0, 1.0
            autosc  = pv_setting.get("auto_scale", False)
            if not autosc:
                if ymin_pv is not None: dmin = ymin_pv
                if ymax_pv is not None: dmax = ymax_pv
            lo, hi = self._band_ylim(i, n, dmin, dmax, autosc)
            ax.set_ylim(lo, hi)

        # One shared grid for the whole graph (driven by the first visible PV).
        first_pv = pvs_for_axes[0] if pvs_for_axes else None
        show_grid = self._pv_settings.get(first_pv, {}).get("grid", True) if first_pv else True
        axes[0].grid(show_grid)

        # X-axis (the single shared bottom axis, axes[0])
        ax0 = ax_x
        if all_times and len(all_times) > 1:
            t_min, t_max = min(all_times), max(all_times)
            total_seconds = (t_max - t_min).total_seconds()
        else:
            t_min = t_max = (all_times[0] if all_times else datetime.now(tz=timezone.utc))
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

        def _make_x_ticks(t_lo, t_hi):
            span_s = (t_hi - t_lo).total_seconds()
            _STEPS = [5,10,15,30,60,120,300,600,900,1800,3600,7200,10800,21600,43200,86400,172800]
            step_s = _STEPS[-1]
            for s in _STEPS:
                if span_s / s <= 8: step_s = s; break
            import math
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
            # Minor ticks: subdivide the regular major step by 5. Built here as a
            # fixed list so matplotlib never infers spacing from the irregular
            # endpoint ticks (which would blow past Locator.MAXTICKS).
            minor_step = step_s / 5.0
            minors = []
            m_s = first_s - step_s
            while True:
                dt_m = epoch + timedelta(seconds=m_s)
                if dt_m > t_hi: break
                if dt_m >= t_lo: minors.append(mdates.date2num(dt_m))
                m_s += minor_step
            return majors, minors

        if total_seconds > 0:
            _ticks, _minor_ticks = _make_x_ticks(
                t_min.astimezone(TZ_PRAGUE), t_max.astimezone(TZ_PRAGUE))
        else:
            _ticks, _minor_ticks = [], []
        if _ticks:
            ax0.xaxis.set_major_locator(FixedLocator(_ticks))
        else:
            ax0.xaxis.set_major_locator(mdates.AutoDateLocator(tz=TZ_PRAGUE, minticks=5, maxticks=8))

        def _fmt_x(x, _):
            try:
                dt = mdates.num2date(x, tz=TZ_PRAGUE)
            except Exception:
                return ""
            if same_day: return dt.strftime("%H:%M:%S")
            return dt.strftime("%m-%d\n00:00") if (dt.hour == 0 and dt.minute == 0) else dt.strftime("%H:%M:%S")

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

        # Crosshair
        self._crosshair_vlines = []; self._crosshair_hlines = []; self._crosshair_texts = []
        self._x_cursor_ann = None
        from matplotlib.transforms import blended_transform_factory as _btf
        for ax_i, ax in enumerate(axes):
            # animated=True keeps these out of the normal draw() so the blit
            # background stays clean — otherwise each redraw bakes in a ghost.
            vl = ax.axvline(color="#888", linewidth=0.8, linestyle="--",
                            visible=False, animated=True)
            hl = ax.axhline(color="#888", linewidth=0.8, linestyle="--",
                            visible=False, animated=True)
            self._crosshair_vlines.append(vl)
            self._crosshair_hlines.append(hl)
            pv_c  = pvs_for_axes[ax_i] if ax_i < len(pvs_for_axes) else None
            p_col = self._pv_settings.get(pv_c, {}).get("color", "#555") if pv_c else "#555"
            sf, ss = (self._graph_spine_xpos[ax_i] if ax_i < len(self._graph_spine_xpos) else (0.0, "left"))
            ha_s  = "left" if ss == "right" else "right"
            blend = _btf(ax.transAxes, ax.transData)
            ann   = ax.text(sf, 0, "", ha=ha_s, va="center", fontsize=_fsize,
                            color=p_col, zorder=10, visible=False, transform=blend,
                            clip_on=False, animated=True,
                            bbox=dict(boxstyle="round,pad=0.15", fc="white",
                                      ec=p_col, alpha=0.85, linewidth=0.6))
            self._crosshair_texts.append(ann)

        self._x_cursor_ann = ax_x.text(
            0, -0.01, "", ha="center", va="top", fontsize=_fsize,
            color="#333", zorder=10, visible=False, animated=True,
            transform=ax_x.get_xaxis_transform(), clip_on=False,
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#888", alpha=0.9, linewidth=0.6))

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

        def _moving_avg(vals, w):
            if w <= 1 or len(vals) < w: return vals
            padded = vals[:w-1][::-1] + vals
            return [sum(padded[j:j+w]) / w for j in range(len(vals))]

        all_times = []
        new_raw   = []
        for i, (pv, ax, pv_lines) in enumerate(zip(self._graph_pvs, self._graph_axes, self._graph_lines)):
            samples = self._samples_by_pv.get(pv, [])
            times   = [datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
                       for ts, v, _ in samples if isinstance(v, (int, float))]
            values  = [v for _, v, _ in samples if isinstance(v, (int, float))]
            if not times:
                new_raw.append(self._graph_raw[i] if i < len(self._graph_raw) else ([], []))
                continue
            all_times.extend(times)
            times_num = mdates.date2num(times)
            new_raw.append((times_num, values))
            pv_setting = self._pv_settings.get(pv, {})
            pv_smooth  = pv_setting.get("smooth", None)
            eff_smooth = pv_smooth if pv_smooth is not None else 1
            if len(pv_lines) == 2:
                pv_lines[0].set_xdata(times); pv_lines[0].set_ydata(values)
                sm = _moving_avg(values, eff_smooth) if eff_smooth > 1 and len(values) >= eff_smooth else values
                pv_lines[1].set_xdata(times); pv_lines[1].set_ydata(sm)
            elif pv_lines:
                pv_lines[0].set_xdata(times); pv_lines[0].set_ydata(values)
            # Re-apply this PV's band (or full height if Autoscale on).
            vals_clean = [v for v in values if v == v]
            if vals_clean:
                dmin, dmax = min(vals_clean), max(vals_clean)
                autosc = pv_setting.get("auto_scale", False)
                if not autosc:
                    ymin_pv = pv_setting.get("ymin"); ymax_pv = pv_setting.get("ymax")
                    if ymin_pv is not None: dmin = ymin_pv
                    if ymax_pv is not None: dmax = ymax_pv
                lo, hi = self._band_ylim(i, len(self._graph_pvs), dmin, dmax, autosc)
                ax.set_ylim(lo, hi)

        self._graph_raw = new_raw
        if all_times and len(all_times) > 1 and self._graph_axes:
            ax0 = self._graph_axes[0]
            from matplotlib.ticker import FuncFormatter as _FF
            t_min, t_max = min(all_times), max(all_times)
            total_s = (t_max - t_min).total_seconds()
            t_min_l = t_min.astimezone(TZ_PRAGUE) if total_s > 0 else datetime.now(TZ_PRAGUE)
            t_max_l = t_max.astimezone(TZ_PRAGUE) if total_s > 0 else t_min_l
            same_d  = t_min_l.date() == t_max_l.date()

            def _fmt_x_live(x, _p):
                try:
                    dt = mdates.num2date(x, tz=TZ_PRAGUE)
                except Exception:
                    return ""
                if same_d: return dt.strftime("%H:%M:%S")
                return dt.strftime("%m-%d\n00:00") if (dt.hour == 0 and dt.minute == 0) else dt.strftime("%H:%M")

            ax0.xaxis.set_major_formatter(_FF(_fmt_x_live))
            if not self._zoom_history:
                span = self._live_window_span
                if span and span.total_seconds() > 0:
                    now_utc = datetime.now().astimezone(timezone.utc)
                    ax0.set_xlim(now_utc - span, now_utc)

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
            QTimer.singleShot(16, self._process_mouse_move)

    def _process_mouse_move(self):
        # Never let a hover-frame render escape as an exception: a replot/resize
        # can tear the figure down between the queued move and this call,
        # leaving detached artists (matplotlib then raises 'NoneType has no
        # attribute dpi' while drawing a Text). It's harmless — just swallow it.
        try:
            self._process_mouse_move_impl()
        except Exception:
            self._mouse_pending = False

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

        for vl in self._crosshair_vlines:
            vl.set_xdata([x_f, x_f]); vl.set_visible(True)
            if bg is not None: vl.axes.draw_artist(vl)

        for ax_i, (ax, hl) in enumerate(zip(self._graph_axes, self._crosshair_hlines)):
            pv = self._graph_pvs[ax_i] if ax_i < len(self._graph_pvs) else None
            try:
                y_mouse = float(ax.transData.inverted().transform((disp_x, disp_y))[1])
            except Exception:
                hl.set_visible(False); continue
            hl.set_ydata([y_mouse, y_mouse]); hl.set_visible(True)
            if bg is not None: ax.draw_artist(hl)

            snap_val = None
            if raw_np and ax_i < len(raw_np):
                arr, vals = raw_np[ax_i]
                if len(arr):
                    # arr (times) is sorted ascending → binary search the nearest
                    # sample instead of scanning the whole array each frame.
                    pos = int(np.searchsorted(arr, x_f))
                    if pos <= 0:
                        idx = 0
                    elif pos >= len(arr):
                        idx = len(arr) - 1
                    else:
                        idx = pos if (arr[pos] - x_f) < (x_f - arr[pos - 1]) else pos - 1
                    snap_val = float(vals[idx])
            if pv and pv in self._pv_settings:
                self._pv_settings[pv]["cursor_val"] = (
                    _fmt_cursor_value(snap_val) if snap_val is not None else "")

            if ax_i < len(self._crosshair_texts):
                ann = self._crosshair_texts[ax_i]
                if ann is not None:
                    sf_info = (self._graph_spine_xpos[ax_i]
                               if ax_i < len(self._graph_spine_xpos) else (0.0, "left"))
                    xfrac, side = sf_info
                    val_str = _fmt_cursor_value(y_mouse)
                    txt = f" {val_str}" if side == "right" else f"{val_str} "
                    y_ann = y_mouse
                    # Stagger every other axis label onto a second row, with
                    # enough vertical padding that the two rows' boxes clear
                    # each other (box height ≈ font size + bbox padding).
                    if ax_i % 2 == 1:
                        try:
                            ylo, yhi = ax.get_ylim()
                            h_px = ax.get_window_extent().height
                            pad_px = ann.get_fontsize() + 16
                            if h_px > 0: y_ann += (yhi - ylo) / h_px * pad_px
                        except Exception:
                            pass
                    ann.set_position((xfrac, y_ann)); ann.set_text(txt); ann.set_visible(True)
                    if bg is not None: ax.draw_artist(ann)

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

        self._cursor_active = True
        if bg is not None: canvas.blit(self._mpl_figure.bbox)
        else: canvas.draw_idle()

        # Push the per-PV cursor values into the axis-settings table only once the
        # mouse settles (rewriting QTableWidget items every move stutters).
        self._cursor_tbl_timer.start(120)

    def _flush_cursor_table(self):
        """Write the latest cursor values (already cached in _pv_settings by the
        hover handler) into the axis-settings table. Coalesced via a timer."""
        try:
            val_idx = list(self._axis_tv_cols).index("cursor_val")
        except ValueError:
            return
        for row in range(self._axis_tv.rowCount()):
            pv_item = self._axis_tv.item(row, 1)
            if not pv_item:
                continue
            cv = self._pv_settings.get(pv_item.text(), {}).get("cursor_val", "")
            item = self._axis_tv.item(row, val_idx)
            if item:
                item.setText(cv)

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
        self._blit_bg      = None
        self._cursor_active = False   # no crosshair to re-assert on next draw

    def _apply_font_size(self):
        if self._mpl_figure and self._samples_by_pv:
            self._plot_graph()

    # ── Span / zoom handlers ────────────────────────────────────────────────

    def _on_span_select(self, xmin, xmax):
        pass  # just visual selection feedback

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
        self._clear_graph()
        self._lbl_graph_info.setText("")

    # ── Downsampling / averaging ──────────────────────────────────────────────

    def _avg_target_points(self) -> int:
        spin = getattr(self, "_avg_target_spin", None)
        if spin is not None:
            return int(spin.value())
        return int(self.config.get("avg_target_points", 2000))

    def _on_avg_target_changed(self, _val):
        self.config["avg_target_points"] = self._avg_target_points()
        if self._samples_by_pv:
            self._plot_graph()

    @staticmethod
    def _downsample_pairs_mean(pairs, target):
        """Bin (ts_ns, value) pairs into ~`target` equal-time bins and average.

        Values are assumed numeric (callers pre-filter). One linear pass; empty
        bins are simply skipped, so the result may hold fewer than `target`
        points. Returns pairs unchanged when already at/under target.
        """
        n = len(pairs)
        if target <= 0 or n <= target:
            return pairs
        t0 = pairs[0][0]
        t1 = pairs[-1][0]
        span = t1 - t0
        if span <= 0:
            return pairs
        bin_w = span / target
        last_bin = target - 1
        out = []
        cur_bin = -1
        acc_t = 0.0
        acc_v = 0.0
        cnt = 0
        for ts, v in pairs:
            b = min(last_bin, int((ts - t0) / bin_w))
            if b != cur_bin:
                if cnt:
                    out.append((int(acc_t / cnt), acc_v / cnt))
                acc_t = 0.0
                acc_v = 0.0
                cnt = 0
                cur_bin = b
            acc_t += ts
            acc_v += v
            cnt += 1
        if cnt:
            out.append((int(acc_t / cnt), acc_v / cnt))
        return out

    # ── Load data ───────────────────────────────────────────────────────────

    def _on_load_clicked(self):
        pvs = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
        if not pvs:
            QMessageBox.warning(self, "No PVs", "Add at least one PV to the list."); return
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
                                       cpva_fetch_last_before)
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
                for pv in pvs:
                    samples = samples_by_pv.get(pv, [])
                    first_ts = min((t for t, _, _ in samples), default=None)
                    if first_ts is None or first_ts > start_ns:
                        last_s = cpva_fetch_last_before(pv, start_ns)
                        if last_s:
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

        # Master-multiple filter + conditions
        unfiltered_count = len(self._table_rows_unfiltered)
        rows = self._filter_master_multiple_rows(self._table_rows_unfiltered)
        self._table_rows = self._apply_conditions_to_rows(rows)

        # Build default pv_settings if not set
        for i, pv in enumerate(self._pv_order):
            if pv not in self._pv_settings:
                self._pv_settings[pv] = self._get_pv_default_settings(pv, i)

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
        if unfiltered_count > 0 and len(self._table_rows) == 0:
            mpv = self._get_master_pv(); mmul = self._get_master_multiple()
            filter_note = f"  ⚠ All rows filtered by master-multiple ({mpv} / {mmul})"
            self._log(f"WARNING: {unfiltered_count} rows fetched but all removed by "
                      f"master-multiple filter (master PV={mpv}, multiple={mmul}). "
                      "Check 'Keep multiples of' setting or master PV is not loaded.")
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

    def _toggle_live_mode(self):
        if self._live_mode:
            self._live_mode = False
            self._live_timer.stop()
            self._countdown_timer.stop()
            self._btn_live.setText("⏵ Live")
            self._btn_live.setStyleSheet("")
            # Stopping live must immediately free the LOAD DATA button, even if
            # the initial live fetch is still running (its callback bails out
            # once _live_mode is False and would otherwise leave LOAD disabled).
            self._btn_load.setEnabled(True)
            self._progress_bar.hide()
            self._plot_window_ns = None
            self._refresh_time_labels()
            self._lbl_status.setText("Live mode stopped.")
        else:
            pvs = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
            if not pvs:
                QMessageBox.warning(self, "No PVs", "Add PVs before starting live mode."); return
            self._live_mode = True
            # Use the user's time window if it makes sense, else last hour
            window_diff = (self._dt_to - self._dt_from).total_seconds()
            if 60 <= window_diff <= _LIVE_MAX_SPAN_S:
                self._live_window_span = timedelta(seconds=window_diff)
            else:
                self._live_window_span = timedelta(hours=1)
            self._btn_live.setText("⏹ Stop Live")
            self._btn_live.setStyleSheet(
                "QPushButton{background:#F57F17;color:white;font-weight:700;border-radius:3px;padding:5px;}")
            self._refresh_time_labels()
            self._lbl_status.setText("Live: initial load…")
            self._btn_load.setEnabled(False)
            self._live_last_ts = None
            self._live_initial_load()

    def _live_initial_load(self):
        now_utc = datetime.now().astimezone(timezone.utc)
        t_from  = now_utc - self._live_window_span
        start_ns = dt_to_ns(t_from)
        end_ns   = dt_to_ns(now_utc)
        pvs      = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
        avg_target = self._avg_target_points()   # read on UI thread
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
                                       cpva_fetch_last_before)
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
                        pvs, start_ns, end_ns, avg_target, progress_fn=_report)
                else:
                    # Raw: all PVs' 1-hour chunks in one shared pool.
                    def _report(done, total_chunks):
                        if total_chunks:
                            sig.pct.emit(int(done / total_chunks * 100))
                            sig.progress.emit(
                                f"Live init: {done}/{total_chunks} chunks")

                    sig.progress.emit(f"Live init: fetching {len(pvs)} PVs (raw)…")
                    raw_by_pv, _errs = cpva_fetch_many_chunked(
                        pvs, start_ns, end_ns, progress_fn=_report)

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
                for pv in pvs:
                    samples = samples_by_pv.get(pv, [])
                    first_ts = min((t for t, _, _ in samples), default=None)
                    if first_ts is None or first_ts > start_ns:
                        try:
                            last_s = cpva_fetch_last_before(pv, start_ns)
                        except Exception:
                            last_s = None
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
        """Live initial load failed — stop live cleanly so the UI isn't stuck."""
        self._log(f"Live initial load error: {e}")
        self._live_mode = False
        self._live_timer.stop()
        self._countdown_timer.stop()
        self._btn_live.setText("⏵ Live")
        self._btn_live.setStyleSheet("")
        self._btn_load.setEnabled(True)
        self._progress_bar.hide()
        self._refresh_time_labels()
        self._lbl_status.setText("Live init failed — see Log tab.")

    def _after_live_initial_load(self, result):
        if not self._live_mode:
            self._progress_bar.hide(); return
        self._btn_load.setEnabled(True)
        self._progress_bar.hide()
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
            self._pv_order      = pv_order
            self._base_pv_order = list(pv_order)
            self._numeric_pvs = {
                pv for pv, s in self._samples_by_pv.items()
                if any(isinstance(v, (int, float)) for _, v, _ in s)
            }
            self._table_rows_unfiltered = self._build_table_rows(samples_by_pv, pv_order)
            self._rebuild_custom_pvs()          # add + compute derived channels
            self._table_rows = self._apply_conditions_to_rows()
            for i, pv in enumerate(self._pv_order):
                if pv not in self._pv_settings:
                    self._pv_settings[pv] = self._get_pv_default_settings(pv, i)
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
        pvs = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
        if not pvs:
            self._toggle_live_mode(); return
        start_ns = self._live_last_ts if self._live_last_ts else (now_ns() - int(60e9))
        # Never look back further than the visible window (bounds the query while
        # the archiver is idle), but always re-query from the last KNOWN sample
        # forward so ingestion lag doesn't make us skip newly-archived points.
        if self._live_window_span:
            min_start = now_ns() - int(self._live_window_span.total_seconds() * 1e9)
            if start_ns < min_start:
                start_ns = min_start
        end_ns   = now_ns()
        self._inc_sig = _IncSig()
        sig = self._inc_sig
        sig.done.connect(self._on_incremental_finished)

        def _worker():
            try:
                from cpva_core import cpva_fetch_samples_chunked, cpva_decode_value
                new_samples: dict = {}
                added_count = 0
                errors: list = []
                for pv in pvs:
                    try:
                        raw = cpva_fetch_samples_chunked(pv, start_ns, end_ns)
                        new_pts = []
                        for s in raw:
                            t_ns = s.get("time")
                            if t_ns is None or int(t_ns) <= start_ns: continue
                            value = cpva_decode_value(s)
                            units = (s.get("metaData") or {}).get("units", "") or ""
                            new_pts.append((int(t_ns), value, units))
                        if new_pts:
                            new_samples[pv] = new_pts
                            added_count += len(new_pts)
                    except Exception as exc:
                        errors.append(f"{shorten_pv_name(pv)}: {exc}")
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

                self._table_rows_unfiltered = self._build_table_rows(
                    self._samples_by_pv, self._base_pv_order or self._pv_order)
                self._rebuild_custom_pvs()          # recompute derived channels live
                self._table_rows = self._apply_conditions_to_rows()
                self._populate_table()
                total_pts = sum(len(v) for v in self._samples_by_pv.values())
                self._lbl_status.setText(
                    f"Live +{added_count} pts  |  total {total_pts}")
                if errors:
                    self._lbl_status.setText(
                        f"Live +{added_count} pts  |  ⚠ {len(errors)} fetch errors")
            else:
                # No new points — show that polling is alive and why nothing moved
                # so an idle archiver isn't mistaken for a frozen app.
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

            # Always refresh the graph each tick so the live window scrolls to
            # "now" even when no new points arrived (otherwise it looks frozen).
            # If the graph has no lines yet or a PV just gained its first numeric
            # data, the existing artists can't represent it — do a full replot;
            # otherwise just mutate the existing artists (fast path).
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
        """Filter rows by active conditions. Returns filtered list."""
        if rows is None:
            rows = self._table_rows_unfiltered
        if not self._conditions:
            return list(rows)
        return [
            (ts, row_dict) for ts, row_dict in rows
            if self._row_matches_conditions(row_dict)
        ]

    def _row_matches_conditions(self, row_dict):
        # Old cssl.py semantics: {pv, min, max}. A condition PV that is absent
        # from the row excludes the row; present values must be within [min, max].
        if not self._conditions:
            return True
        for cond in self._conditions:
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
        tolerance = max(1e-6, abs(multiple) * 1e-9)
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

    def _channel_letters(self) -> list:
        """Ordered ``[(letter, name, display_name)]`` for every channel currently
        in ``_pv_order`` (real PVs first, then custom channels). These letters are
        the variables used in custom-PV expressions (e.g. ``B/D``)."""
        custom_names = {d.get("name", "") for d in self._custom_pvs}
        out = []
        for i, pv in enumerate(self._pv_order):
            if pv in custom_names:
                disp = pv
            else:
                disp = self._pv_settings.get(pv, {}).get("display_name", shorten_pv_name(pv))
            out.append((self._col_letter(i), pv, disp))
        return out

    def _compute_custom_pvs_in_rows(self, rows):
        """Evaluate every custom PV expression for each row, using the channel
        letters as variables. Customs are computed in definition order so a later
        custom can reference an earlier one by its letter."""
        defs = [(d.get("name", ""), d.get("expr", "")) for d in self._custom_pvs
                if d.get("name") and d.get("expr")]
        if not defs:
            return
        SAFE = {"__builtins__": {}, "abs": abs, "min": min,
                "max": max, "round": round, "math": math}
        letters = self._channel_letters()
        name_to_letter = {name: letter for letter, name, _ in letters}
        compiled = {}
        for name, expr in defs:
            try:
                compiled[name] = compile(expr, "<cpv>", "eval")
            except Exception:
                compiled[name] = None
        for _ts_ns, row_dict in rows:
            ns = {}
            for letter, name, _ in letters:
                v = row_dict.get(name, (None,))[0]
                ns[letter] = v if isinstance(v, (int, float)) else None
            for name, _expr in defs:
                code = compiled.get(name)
                if code is None:
                    continue
                try:
                    result = eval(code, SAFE, ns)
                except Exception:
                    result = None
                row_dict[name] = (result, "")
                lt = name_to_letter.get(name)
                if lt is not None:
                    ns[lt] = result if isinstance(result, (int, float)) else None

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
        self.config["pv_list"] = [
            self._pv_list.item(i).text()
            for i in range(self._pv_list.count())
        ]
        self.config["conditions"]      = copy(self._conditions)
        self.config["master_pv"]       = self._get_master_pv()
        self.config["master_multiple"] = self._get_master_multiple()
        self.config["avg_target_points"] = self._avg_target_points()
        self.config["time_from"] = self._dt_from.strftime("%Y-%m-%d %H:%M:%S")
        self.config["time_to"]   = self._dt_to.strftime("%Y-%m-%d %H:%M:%S")
        save_config(self.config)

    # ── Table population ────────────────────────────────────────────────────

    def _populate_table(self):
        if not self._table_rows:
            self._table_widget.setRowCount(0)
            self._table_widget.setColumnCount(1)
            self._table_widget.setHorizontalHeaderLabels(["Timestamp"])
            self._lbl_table_info.setText("No rows.")
            return

        pvs = self._pv_order
        cols = ["Timestamp"] + [shorten_pv_name(p) for p in pvs]
        self._table_widget.setUpdatesEnabled(False)
        self._table_widget.setColumnCount(len(cols))
        self._table_widget.setHorizontalHeaderLabels(cols)
        self._table_widget.setRowCount(len(self._table_rows))

        for row_i, (ts, row_dict) in enumerate(self._table_rows):
            ts_item = QTableWidgetItem(ns_to_local_str(ts))
            ts_item.setFlags(ts_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table_widget.setItem(row_i, 0, ts_item)
            for col_j, pv in enumerate(pvs, 1):
                val_raw, severity = row_dict.get(pv, (None, ""))
                txt = self._format_value(val_raw)
                item = QTableWidgetItem(txt)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if severity in ("MINOR",):
                    item.setForeground(QColor("#FFA000"))
                elif severity in ("MAJOR", "INVALID"):
                    item.setForeground(QColor("#C62828"))
                self._table_widget.setItem(row_i, col_j, item)

        self._table_widget.setUpdatesEnabled(True)
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
        pvs = self._pv_order
        self._axis_tv.setRowCount(len(pvs))
        _COL = list(self._axis_tv_cols)
        for row_i, pv in enumerate(pvs):
            s = self._pv_settings.get(pv, self._get_pv_default_settings(pv, row_i))
            for col_j, col_name in enumerate(_COL):
                val = s.get(col_name, "")
                if col_name == "blank":
                    item = QTableWidgetItem("")
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # non-editable spacer
                    self._axis_tv.setItem(row_i, col_j, item)
                elif col_name == "show":
                    chk = QTableWidgetItem()
                    chk.setCheckState(Qt.CheckState.Checked if val else Qt.CheckState.Unchecked)
                    self._axis_tv.setItem(row_i, col_j, chk)
                elif col_name == "color":
                    item = QTableWidgetItem("")
                    color_hex = s.get("color", _GRAPH_COLORS[row_i % len(_GRAPH_COLORS)])
                    item.setData(Qt.ItemDataRole.UserRole, color_hex)
                    self._axis_tv.setItem(row_i, col_j, item)
                elif col_name == "auto_scale":
                    chk = QTableWidgetItem()
                    chk.setCheckState(Qt.CheckState.Checked if val else Qt.CheckState.Unchecked)
                    self._axis_tv.setItem(row_i, col_j, chk)
                elif col_name == "grid":
                    chk = QTableWidgetItem()
                    chk.setCheckState(Qt.CheckState.Checked if val else Qt.CheckState.Unchecked)
                    self._axis_tv.setItem(row_i, col_j, chk)
                else:
                    txt = "" if val is None else str(val)
                    item = QTableWidgetItem(txt)
                    if col_name in ("pv", "cursor_val"):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._axis_tv.setItem(row_i, col_j, item)

    def _on_axis_tv_double_click(self, row, col):
        col_name = list(self._axis_tv_cols)[col] if col < len(self._axis_tv_cols) else ""
        if col_name in ("pv", "cursor_val"): return

    def _on_axis_color_changed(self, row, color_hex):
        if row < len(self._pv_order):
            pv = self._pv_order[row]
            if pv not in self._pv_settings:
                self._pv_settings[pv] = self._get_pv_default_settings(pv, row)
            self._pv_settings[pv]["color"] = color_hex
            # Update graph line immediately
            if row < len(self._graph_lines) and self._graph_lines[row]:
                for ln in self._graph_lines[row]:
                    ln.set_color(color_hex)
                if row < len(self._graph_axes):
                    ax = self._graph_axes[row]
                    ax.yaxis.label.set_color(color_hex)
                    ax.tick_params(axis="y", labelcolor=color_hex)
                if self._mpl_canvas:
                    self._mpl_canvas.draw_idle()

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
        for row_i in range(self._axis_tv.rowCount()):
            pv_item = self._axis_tv.item(row_i, 1)
            if not pv_item: continue
            pv = pv_item.text()
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
        self._plot_graph()

    def _get_pv_default_settings(self, pv, idx):
        return {
            "show":         True,
            "pv":           pv,
            "display_name": shorten_pv_name(pv),
            "color":        _GRAPH_COLORS[idx % len(_GRAPH_COLORS)],
            "cursor_val":   "",
            "side":         "left",
            "ymin":         None,
            "ymax":         None,
            "auto_scale":   False,
            "width":        None,
            "smooth":       1,
            "grid":         idx == 0,
        }

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

    def _save_preset(self):
        idx = self._preset_combo.currentIndex()
        if idx < 0:
            self._save_preset_as(); return
        pvs  = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
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
        pvs  = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
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
            for pv in dlg.selected_pvs:
                # avoid duplicates
                existing = [self._pv_list.item(i).text()
                            for i in range(self._pv_list.count())]
                if pv not in existing:
                    self._pv_list.addItem(pv)
            self._update_pv_count()

    def _remove_selected_pvs(self):
        for item in reversed(self._pv_list.selectedItems()):
            self._pv_list.takeItem(self._pv_list.row(item))
        self._update_pv_count()

    def _clear_pv_list(self):
        ans = QMessageBox.question(self, "Clear PVs", "Remove all PVs from list?")
        if ans == QMessageBox.StandardButton.Yes:
            self._pv_list.clear()
            self._update_pv_count()

    def _on_pv_double_click(self, item):
        pv, ok = QInputDialog.getText(self, "Edit PV", "PV name:", text=item.text())
        if ok and pv.strip():
            item.setText(pv.strip())

    def _update_pv_count(self):
        n = self._pv_list.count()
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
                window_diff = (self._dt_to - self._dt_from).total_seconds()
                self._live_window_span = (timedelta(seconds=window_diff)
                                          if 60 <= window_diff <= _LIVE_MAX_SPAN_S
                                          else timedelta(hours=1))
                self._live_last_ts = None
                self._zoom_history.clear()
                self._lbl_status.setText("Live: reloading window…")
                self._live_initial_load()
            else:
                # Not live: load the newly chosen window now.
                self._on_load_clicked()

    # ── Conditions dialog ────────────────────────────────────────────────────

    def _open_conditions_dialog(self):
        pvs = [self._pv_list.item(i).text() for i in range(self._pv_list.count())]
        dlg = _ConditionsDialog(self._conditions, pvs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._conditions = dlg.result_conditions
            self._apply_conditions_to_rows()
            self._populate_table()

    # ── Reference lines dialog ───────────────────────────────────────────────

    def _open_ref_lines_dialog(self):
        dlg = _RefLinesDialog(self._ref_lines, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._ref_lines = dlg.result_lines
            if self._samples_by_pv:
                self._plot_graph()

    # ── Custom PV dialog ─────────────────────────────────────────────────────

    def _open_custom_pv_dialog(self):
        dlg = _CustomPVDialog(self._custom_pvs, self._channel_letters(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._custom_pvs = dlg.result_pvs
            save_custom_pvs(self._custom_pvs)
            self._compile_custom_pvs()
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

    def _compile_custom_pvs(self):
        self._custom_pv_exprs = {}
        for entry in self._custom_pvs:
            name = entry.get("name", "")
            expr = entry.get("expr", "")
            if name and expr:
                self._custom_pv_exprs[name] = expr

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
                     "its value lies within [Min, Max]. Leave Min or Max blank "
                     "for an open bound.")
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


# ── Custom PV Dialog ─────────────────────────────────────────────────────────

class _CustomPVDialog(QDialog):
    def __init__(self, custom_pvs, channel_letters=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom PVs (derived channels)")
        self.resize(820, 580)
        self._rows: list = []
        self.result_pvs: list = []

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Define new virtual PVs as Python expressions.\n"
            "Use the letters below as variables (e.g. B/D, A*0.749)."))

        # ── Reference table: which letter is which channel ──────────────────
        channel_letters = channel_letters or []
        ref_box = QGroupBox("Available channels")
        ref_lay = QVBoxLayout(ref_box)
        if channel_letters:
            ref_tbl = QTableWidget(len(channel_letters), 2)
            ref_tbl.setHorizontalHeaderLabels(["Var", "Channel"])
            ref_tbl.verticalHeader().setVisible(False)
            ref_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            ref_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            ref_tbl.setMaximumHeight(150)
            for r, (letter, _name, disp) in enumerate(channel_letters):
                it_l = QTableWidgetItem(letter)
                it_l.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                ref_tbl.setItem(r, 0, it_l)
                ref_tbl.setItem(r, 1, QTableWidgetItem(disp))
            ref_tbl.setColumnWidth(0, 50)
            ref_tbl.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch)
            ref_lay.addWidget(ref_tbl)
        else:
            hint = QLabel("Load data first to see the PV letters.")
            hint.setStyleSheet("color:#777;")
            ref_lay.addWidget(hint)
        lay.addWidget(ref_box)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget()
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setSpacing(4)
        self._inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, stretch=1)

        for cpv in custom_pvs:
            self._add_row(cpv)

        ctrl = QHBoxLayout()
        b_add = QPushButton("+ Add"); b_add.clicked.connect(lambda: self._add_row())
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
        name_e = QLineEdit(existing.get("name","") if existing else "")
        name_e.setMinimumWidth(220); name_e.setPlaceholderText("Name")
        expr_e = QLineEdit(existing.get("expr","") if existing else "")
        expr_e.setMinimumWidth(260); expr_e.setPlaceholderText("Expression (Python)")
        b_del = QPushButton("✕"); b_del.setFixedWidth(26)
        b_del.setStyleSheet("QPushButton{background:#B71C1C;color:white;border-radius:3px;}")
        b_del.clicked.connect(lambda: self._remove_row(row_w, rec))
        rl.addWidget(name_e); rl.addWidget(expr_e); rl.addWidget(b_del)
        rec = {"widget": row_w, "name": name_e, "expr": expr_e}
        self._rows.append(rec)
        self._inner_lay.insertWidget(self._inner_lay.count() - 1, row_w)

    def _remove_row(self, row_w, rec):
        self._rows.remove(rec)
        row_w.deleteLater()

    def _accept(self):
        self.result_pvs = [
            {"name": r["name"].text().strip(), "expr": r["expr"].text().strip()}
            for r in self._rows
            if r["name"].text().strip() and r["expr"].text().strip()
        ]
        self.accept()


# ── Combined main window ─────────────────────────────────────────────────────

class CPVASuiteWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CPVA Suite  —  CSS Logger + Spectra")
        self.resize(1600, 950)
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
            if geom:
                x, y, w, h = geom
                self.setGeometry(x, y, w, h)
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
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(_APP_STYLESHEET)
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