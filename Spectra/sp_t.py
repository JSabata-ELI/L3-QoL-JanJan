"""
sp_t.py — SPIDER spectrometer spectral analysis

Archive workflow:
  1. Click "Load day" -> pick a date (or a multi-day range). The "Search data by"
     PV (SBW4 energy by default; add your own, e.g. Alpha output energy / GDD / TOD)
     is loaded into the top search graph.
  2. Drag on the search graph to select a time region (= one spectrum); repeat.
  3. Click "Analyze" -> averaged spectra appear in the bottom graph. Colour them by
     selection order or by GDD / TOD on a rainbow scale.

Live workflow:
  1. Switch to Live -> click "Start Live".
  2. The bottom graph shows the newest shot plus the average of the last N spectra.

Standalone:   python sp_t.py
Integration:  class SpectraWidget, method cancel_scan()
"""

from __future__ import annotations

import csv, json, os, ssl, sys, threading, urllib.parse, urllib.request
from collections import deque
from datetime import date, datetime, timezone

import numpy as np

try:
    from zoneinfo import ZoneInfo
    _PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    _PRAGUE = None

from PySide6.QtCore import Qt, QObject, QTimer, Signal, QDate, QLocale
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QScrollArea, QSizePolicy, QButtonGroup,
    QFileDialog, QDialog, QDialogButtonBox, QFrame, QSplitter,
    QCalendarWidget, QMessageBox, QMainWindow, QTabWidget, QComboBox,
    QProgressBar, QStyledItemDelegate, QAbstractItemView, QInputDialog,
    QToolButton, QMenu, QStyle, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QListWidget, QListWidgetItem,
)

import matplotlib
matplotlib.use("QtAgg")
matplotlib.rcParams['axes.facecolor']   = 'white'
matplotlib.rcParams['figure.facecolor'] = 'white'
import matplotlib.cm as _mpl_cm
import matplotlib.colors as _mpl_colors
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector

# ── Constants ─────────────────────────────────────────────────────────────────
PV_ENERGY = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"
PV_SPEC_X = "L3-SBDP-SPIDER:SpecDomain_Int_X"
PV_SPEC_Y = "L3-SBDP-SPIDER:SpecDomain_Int_Y"
CPVA_URL  = "https://10.78.0.57:8443/api/1.0/cpva"

# Extra scalar PVs averaged per region and shown in the region details.
# (label, channel) — add more entries here in the future, the UI/export adapt.
ORDER_PVS = [
    ("GDD", "L3-SPFE-AOD03-002:Order2_RB"),
    ("TOD", "L3-SPFE-AOD03-002:Order3_RB"),
    ("FOD", "L3-SPFE-AOD03-002:Order4_RB"),
]

PV_ALPHA_ENERGY = "HAPLS-ENER_IN_GPL_LT2_DIAG2:Energy"

# Scalar PVs the user can plot in the top "search" graph to pick time regions.
# This list is user-editable in the UI (add / remove / presets) and persisted to JSON.
DEFAULT_SEARCH_PVS = [
    ("SBW4 energy [J]",  PV_ENERGY),
    ("Alpha energy [J]", PV_ALPHA_ENERGY),
    ("GDD (Order 2)",    "L3-SPFE-AOD03-002:Order2_RB"),
    ("TOD (Order 3)",    "L3-SPFE-AOD03-002:Order3_RB"),
]


def _search_pv_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "search_pvs.json")

def _layout_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "layout.json")

def _preset_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "search_presets.json")

def _spec_pvs_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "spec_pvs.json")

def _strip_xy_suffix(ch: str) -> str:
    """Return the base PV name by stripping a trailing _X or _Y suffix."""
    for suffix in ("_X", "_Y"):
        if ch.endswith(suffix):
            return ch[:-len(suffix)]
    return ch

def _load_search_presets() -> list:
    try:
        with open(_preset_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [p for p in data
                    if isinstance(p, dict) and p.get("name") and isinstance(p.get("pvs"), list)]
    except Exception:
        pass
    return []

def _save_search_presets(presets: list):
    try:
        p = _preset_config_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=2)
    except Exception:
        pass

LIVE_INTERVAL_S = 3            # poll period in live mode
LIVE_BUF_MAX    = 2000         # max spectra kept in the rolling buffer
DEFAULT_LIVE_N  = 100          # default "average last N" value
LIVE_HISTORY_S  = 600          # on Start Live, preload this many seconds of recent shots
MAX_INDIVIDUAL_LINES = 400     # cap when overlaying a region's individual spectra

_REGION_COLORS = [
    "#C62828", "#2E7D32", "#EF6C00", "#6A1B9A",
    "#00838F", "#4E342E", "#AD1457", "#37474F",
]

_CHK_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; }
QCheckBox::indicator {
    width: 17px; height: 17px;
    border: 2px solid #4a4a4a; border-radius: 3px; background: #fff;
}
QCheckBox::indicator:hover  { border-color: #1565C0; background: #e8f0fe; }
QCheckBox::indicator:checked { border-color: #1565C0; background: #1565C0; }
"""

_GROUP_STYLE = """
QGroupBox {
    font-weight: 700; padding-top: 14px; margin-top: 8px;
    border: 1px solid #ccc; border-radius: 4px;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
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

_TB_STYLE = (
    "QToolBar { background: white; border: none; } "
    "QToolButton { background: transparent; color: black; } "
    "QToolButton:hover { background: #e8f0fe; } "
    "QToolButton:checked { background: #bbd4f8; border-radius: 3px; } "
    "QToolButton:checked:hover { background: #a8c6f5; border-radius: 3px; }"
)
_TB_HINTS = {
    "Home":     "Reset view",
    "Back":     "Previous view",
    "Forward":  "Next view",
    "Pan":      "Pan (drag left btn) / zoom (drag right btn)",
    "Zoom":     "Zoom to selection",
    "Subplots": "Adjust margins",
    "Save":     "Save as image",
}


class _CustomToolbar(NavigationToolbar2QT):
    """Custom toolbar. Permanently removes 'Export values' from the Subplots
    settings dialog and emits subplot_params_changed when that dialog closes."""
    subplot_params_changed = Signal()

    def configure_subplots(self):
        super().configure_subplots()
        QTimer.singleShot(0, self._patch_subplots_dialog)

    def _patch_subplots_dialog(self):
        dlg = getattr(self, "_subplot_dialog", None)
        if dlg is None:
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QDialog) and w.isVisible():
                    dlg = w
                    break
        if dlg is None:
            return
        for btn in dlg.findChildren(QPushButton):
            if btn.text().strip() == "Export values":
                btn.setParent(None)
                break
        dlg.adjustSize()
        try:
            dlg.finished.connect(lambda _: self.subplot_params_changed.emit())
        except Exception:
            pass


# ── SSL + CPVA helpers ────────────────────────────────────────────────────────
def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _cpva_fetch(channel: str, start_ns: int, end_ns: int) -> list:
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": int(start_ns),
        "end":   int(end_ns),
    })
    req = urllib.request.urlopen(
        f"{CPVA_URL}/samples?{params}", context=_ssl_ctx(), timeout=30
    )
    return json.loads(req.read())


def _fetch_waveforms(channel: str, start_ns: int, end_ns: int) -> list[tuple[int, np.ndarray]]:
    try:
        samples = _cpva_fetch(channel, start_ns, end_ns)
    except Exception:
        return []
    result = []
    for s in samples:
        t = s.get("time")
        v = s.get("value")
        if isinstance(v, list) and v:
            try:
                arr = np.array(v, dtype=float)
                if t is not None:
                    result.append((int(t), arr))
            except (ValueError, TypeError):
                pass
    return result


_cpva_channel_cache: list = []   # module-level cache shared between dialogs

def _cpva_load_all_channels() -> list:
    """Fetch every archived channel name from CPVA (no filter — filter locally).
    Returns a sorted list of strings; empty on failure."""
    url = f"{CPVA_URL}/channels"
    try:
        req = urllib.request.urlopen(url, context=_ssl_ctx(), timeout=15)
        data = json.loads(req.read())
        if isinstance(data, list):
            return sorted(str(x) for x in data if x)
    except Exception:
        pass
    return []


def _fetch_scalars(channel: str, start_ns: int, end_ns: int) -> list[tuple[int, float]]:
    try:
        samples = _cpva_fetch(channel, start_ns, end_ns)
    except Exception:
        return []
    result = []
    for s in samples:
        t = s.get("time")
        v = s.get("value")
        if isinstance(v, list):
            v = v[0] if v else None
        if t is not None and v is not None:
            try:
                result.append((int(t), float(v)))
            except (TypeError, ValueError):
                pass
    return result


def _trimmed_mean(stack: np.ndarray, frac: float = 0.1) -> np.ndarray:
    """Mean after dropping the lowest and highest `frac` of values per column."""
    n = stack.shape[0]
    k = int(n * frac)
    if n - 2 * k < 1:
        return stack.mean(axis=0)
    s = np.sort(stack, axis=0)
    return s[k:n - k].mean(axis=0)


def _sigma_clipped_mean(stack: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    """Mean per column after masking points beyond `sigma` standard deviations."""
    mean = stack.mean(axis=0)
    std  = stack.std(axis=0)
    lo, hi = mean - sigma * std, mean + sigma * std
    masked = np.where((stack >= lo) & (stack <= hi), stack, np.nan)
    with np.errstate(invalid="ignore"):
        clipped = np.nanmean(masked, axis=0)
    return np.where(np.isnan(clipped), mean, clipped)


def _compute_stats(arrs: list[np.ndarray]) -> dict | None:
    """Combine a list of waveforms (keeping only the most common length).

    Computes every averaging method up front so the user can switch the method
    afterwards without re-fetching the data.
    """
    if not arrs:
        return None
    lens = [len(a) for a in arrs]
    common = max(set(lens), key=lens.count)
    arrs = [a for a in arrs if len(a) == common]
    if not arrs:
        return None
    stack = np.vstack(arrs)
    return {
        "mean":    stack.mean(axis=0),
        "median":  np.median(stack, axis=0),
        "trimmed": _trimmed_mean(stack, 0.1),
        "sigma":   _sigma_clipped_mean(stack, 3.0),
        "std":     stack.std(axis=0),
        "stack":   stack,          # individual spectra (feature: show all spectra)
        "n":       len(arrs),
    }


# user-facing dropdown label -> stat key
_METHODS = {
    "Mean":               "mean",
    "Median":             "median",
    "Trimmed mean 10%":   "trimmed",
    "Sigma-clipped mean": "sigma",
}


def _ns_to_dt(t_ns: int) -> datetime:
    dt = datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc)
    return dt.astimezone(_PRAGUE) if _PRAGUE else dt


def _fmt_hms(t_ns: int) -> str:
    return _ns_to_dt(t_ns).strftime("%H:%M:%S")


def _fmt_date(t_ns: int) -> str:
    return _ns_to_dt(t_ns).strftime("%Y-%m-%d")


def _fmt_dur(t0_ns: int, t1_ns: int) -> str:
    secs = max(0, int((t1_ns - t0_ns) / 1e9))
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m{secs % 60:02d}s"


def _day_range_ns(qdate: QDate) -> tuple[int, int]:
    tz = _PRAGUE or timezone.utc
    d = date(qdate.year(), qdate.month(), qdate.day())
    start = datetime(d.year, d.month, d.day,  0,  0,  0, tzinfo=tz)
    end   = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
    return int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)


# ── Cross-thread signal carrier ───────────────────────────────────────────────
class _Sig(QObject):
    done       = Signal(object)
    error      = Signal(str)
    progress   = Signal(str)
    progress_n = Signal(int, int)   # (done, total) for the progress bar


def _bg(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


# ── DatePickerDialog ──────────────────────────────────────────────────────────
# Calendar styling: gray day-name header, blue navigation bar (matches Image Tools).
_CAL_STYLE = """
QCalendarWidget QWidget { background: #ffffff; color: #111; }
QCalendarWidget QAbstractItemView:enabled {
    background: #ffffff; color: #111;
    selection-background-color: #1565C0; selection-color: white;
}
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #eeeeee; }
QCalendarWidget QToolButton {
    color: #222; background: transparent;
    font-weight: 700; font-size: 13px;
    border-radius: 3px; padding: 3px 6px;
}
QCalendarWidget QToolButton:hover { background: #d0d0d0; }
QCalendarWidget QToolButton#qt_calendar_monthbutton {
    border: 1px solid #aaaaaa; background: #f5f5f5; padding: 2px 8px;
}
QCalendarWidget QToolButton#qt_calendar_monthbutton:hover { background: #e0e0e0; }
QCalendarWidget QSpinBox {
    color: #222; background: #eeeeee; border: none; font-weight: 700;
}
QCalendarWidget QMenu { color: #111; background: #fff; }
"""


class _WeekendDelegate(QStyledItemDelegate):
    """Paint calendar cells: selected=blue background, Sat/Sun=red text.

    Weekend detection uses index.column() (col 5=Sat, 6=Sun, Monday-first layout)
    so spillover-month cells are coloured correctly too.
    initStyleOption strips State_Selected for cells not in _selected_keys so that
    Qt's own selection highlight (today after Clear, etc.) never bleeds through.
    """
    def __init__(self, cal: QCalendarWidget):
        super().__init__(cal)
        self._cal = cal
        self._selected_keys: set = set()   # (year, month, day) tuples

    def _date_for_index(self, index) -> "QDate | None":
        """Return the QDate for a model cell, or None for the hidden header row (row 0)."""
        if index.row() == 0:
            return None
        year, month = self._cal.yearShown(), self._cal.monthShown()
        first = QDate(year, month, 1)
        start = first.addDays(-(first.dayOfWeek() - 1))   # Monday of first displayed week
        return start.addDays((index.row() - 1) * 7 + index.column())

    def set_selected(self, dates: "list[QDate]"):
        self._selected_keys = {(d.year(), d.month(), d.day()) for d in dates}
        view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view is not None:
            view.viewport().update()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        d = self._date_for_index(index)
        if d is not None and (d.year(), d.month(), d.day()) not in self._selected_keys:
            # Strip Qt's internal selection highlight (e.g. today's cell after Clear).
            option.state = option.state & ~QStyle.StateFlag.State_Selected

    def paint(self, painter, option, index):
        is_weekend = index.column() in (5, 6)   # Mon=0 … Sat=5, Sun=6
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


def _make_calendar(initial: "QDate | None" = None) -> "tuple[QFrame, QCalendarWidget]":
    """Return (wrapper_frame, cal).

    The wrapper contains:
      1. A custom gray QWidget row with day-name QLabels (Mon…Sun) — 100% reliable
         gray header regardless of PySide6/Fusion stylesheet interactions.
      2. The QCalendarWidget with the built-in header hidden.

    Weekend cells (Sat/Sun) — including spillover-month cells — are coloured red
    via _WeekendDelegate which detects weekends by column index (col 5=Sat, 6=Sun).
    """
    # ── QCalendarWidget (both built-in headers hidden — we supply our own nav + day row) ──
    cal = QCalendarWidget()
    cal.setGridVisible(True)
    cal.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader)
    if initial:
        cal.setSelectedDate(initial)
    cal.setStyleSheet(_CAL_STYLE)

    # Hide built-in navigation bar — we draw our own below so hdr_row sits directly above the grid.
    nav_internal = cal.findChild(QWidget, "qt_calendar_navigationbar")
    if nav_internal:
        nav_internal.hide()

    # Install weekend+selection delegate on the grid view.
    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    if view is not None:
        cal._wk_delegate = _WeekendDelegate(cal)
        view.setItemDelegate(cal._wk_delegate)

    # ── Custom navigation bar (light gray, month as button, year spinbox) ────
    _MONTHS = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

    nav_row = QWidget()
    nav_row.setAutoFillBackground(True)
    nav_pal = nav_row.palette()
    nav_pal.setColor(QPalette.ColorRole.Window, QColor("#eeeeee"))
    nav_row.setPalette(nav_pal)
    nav_lay = QHBoxLayout(nav_row)
    nav_lay.setContentsMargins(4, 3, 4, 3)
    nav_lay.setSpacing(4)

    prev_btn = QToolButton()
    prev_btn.setText("◀")
    prev_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")

    month_btn = QPushButton()
    month_btn.setMinimumWidth(100)
    month_btn.setStyleSheet(
        "QPushButton { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " font-weight: bold; font-size: 12px; padding: 2px 10px; }"
        "QPushButton:hover { background: #e0e0e0; }"
    )

    year_spin = QSpinBox()
    year_spin.setRange(2000, 2100)
    year_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    year_spin.setStyleSheet(
        "QSpinBox { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " padding: 1px 4px; font-weight: bold; font-size: 12px; }"
    )
    year_spin.setFixedWidth(60)

    next_btn = QToolButton()
    next_btn.setText("▶")
    next_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")

    nav_lay.addWidget(prev_btn)
    nav_lay.addStretch()
    nav_lay.addWidget(month_btn)
    nav_lay.addWidget(year_spin)
    nav_lay.addStretch()
    nav_lay.addWidget(next_btn)

    def _update_nav():
        m, y = cal.monthShown(), cal.yearShown()
        month_btn.setText(_MONTHS[m - 1])
        year_spin.blockSignals(True)
        year_spin.setValue(y)
        year_spin.blockSignals(False)

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

    # ── Custom gray day-name header ──────────────────────────────────────────
    hdr_row = QWidget()
    hdr_row.setAutoFillBackground(True)
    hdr_pal = hdr_row.palette()
    hdr_pal.setColor(QPalette.ColorRole.Window, QColor("#757575"))
    hdr_row.setPalette(hdr_pal)
    hdr_lay = QHBoxLayout(hdr_row)
    hdr_lay.setContentsMargins(0, 0, 0, 0)
    hdr_lay.setSpacing(0)
    for name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #111111; font-weight: 700; padding: 4px 0;")
        hdr_lay.addWidget(lbl, stretch=1)

    # ── Wrapper frame: [nav_row] [hdr_row] [cal grid] ───────────────────────
    wrapper = QFrame()
    wrapper.setStyleSheet("QFrame { border: 1px solid #b0b0b0; border-radius: 3px; }")
    w_lay = QVBoxLayout(wrapper)
    w_lay.setContentsMargins(0, 0, 0, 0)
    w_lay.setSpacing(0)
    w_lay.addWidget(nav_row)
    w_lay.addWidget(hdr_row)
    w_lay.addWidget(cal)

    return wrapper, cal


class DatePickerDialog(QDialog):
    """Single-calendar date picker.

    Plain click   → toggle that day (adds if unselected, removes if selected).
    Ctrl+click    → add the inclusive range from the last click (or today if
                    this is the first interaction) to the clicked day.

    Uses QCalendarWidget.clicked(QDate) — reliable across all PySide6 versions.
    """
    def __init__(self, parent=None, initial: QDate | None = None):
        super().__init__(parent)
        self.setWindowTitle("Select day(s)")
        self.setWindowIcon(QIcon())
        self.setModal(True)
        self.setMinimumWidth(330)

        self._last_click: QDate | None = None
        self._dates: list[QDate] = []

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self._frame, self._cal = _make_calendar(initial)
        lay.addWidget(self._frame)

        self._lbl_info = QLabel("")
        self._lbl_info.setStyleSheet("color: #555; font-size: 11px;")
        lay.addWidget(self._lbl_info)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        clear_btn = btns.addButton("Clear", QDialogButtonBox.ButtonRole.ResetRole)
        clear_btn.setToolTip("Deselect all days")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        clear_btn.clicked.connect(lambda: self._apply_selection([]))
        lay.addWidget(btns)

        self._cal.clicked.connect(self._on_date_clicked)

        if initial and initial.isValid():
            self._apply_selection([initial])
            self._last_click = initial

    # ── Click handler ─────────────────────────────────────────────────────────
    def _on_date_clicked(self, d: QDate):
        ctrl = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl:
            # Ctrl+click: ADD the range [anchor … d] to existing selection.
            anchor = self._last_click if self._last_click is not None else QDate.currentDate()
            new_dates = sorted(
                set(self._dates) | set(self._date_range(anchor, d)),
                key=lambda x: (x.year(), x.month(), x.day()),
            )
        else:
            # Plain click: toggle d.
            key = (d.year(), d.month(), d.day())
            existing_keys = {(x.year(), x.month(), x.day()) for x in self._dates}
            if key in existing_keys:
                new_dates = [x for x in self._dates
                             if (x.year(), x.month(), x.day()) != key]
            else:
                new_dates = sorted(
                    self._dates + [d],
                    key=lambda x: (x.year(), x.month(), x.day()),
                )
        self._last_click = d
        self._apply_selection(new_dates)

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _date_range(d1: QDate, d2: QDate) -> "list[QDate]":
        if d2 < d1:
            d1, d2 = d2, d1
        days, d = [], d1
        while d <= d2:
            days.append(d)
            d = d.addDays(1)
        return days

    def _apply_selection(self, dates: "list[QDate]"):
        self._dates = dates
        delegate = getattr(self._cal, "_wk_delegate", None)
        if delegate is not None:
            delegate.set_selected(dates)
        if dates:
            self._cal.setSelectedDate(dates[-1])
        n = len(dates)
        if n == 0:
            self._lbl_info.setText("No date selected")
        elif n == 1:
            self._lbl_info.setText(f"Selected: {dates[0].toString('yyyy-MM-dd')}")
        else:
            self._lbl_info.setText(
                f"Selected: {dates[0].toString('yyyy-MM-dd')} → "
                f"{dates[-1].toString('yyyy-MM-dd')}  ({n} days)"
            )

    # ── Public API ────────────────────────────────────────────────────────────
    def selected_date(self) -> QDate:
        return self._dates[0] if self._dates else self._cal.selectedDate()

    def selected_dates(self) -> "list[QDate]":
        """Chronologically sorted list of selected days."""
        return list(self._dates) if self._dates else [self._cal.selectedDate()]


# ── PvSearchDialog ────────────────────────────────────────────────────────────
class PvSearchDialog(QDialog):
    """Load all CPVA channel names once, then filter locally as the user types."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add PV")
        self.setMinimumSize(480, 360)
        self._added: list = []
        self._all_channels: list = list(_cpva_channel_cache)
        self._build_ui()
        if self._all_channels:
            self._lbl_status.setText(
                f"{len(self._all_channels)} channels loaded. Type to filter."
            )
        else:
            self._kick_load()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.addWidget(QLabel("Search for a CPVA channel (type part of the name):"))
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("e.g. HAPLS-ENER or L3-SPFE or GDD")
        self._edit.textEdited.connect(self._filter)
        lay.addWidget(self._edit)
        self._lbl_status = QLabel("Loading channels from CPVA…")
        self._lbl_status.setStyleSheet("color: #666; font-size: 10px;")
        lay.addWidget(self._lbl_status)
        self._lst = QListWidget()
        self._lst.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._lst.setAlternatingRowColors(False)
        lay.addWidget(self._lst, stretch=1)
        note = QLabel("Click to select (Ctrl+click for multiple), then click Add.")
        note.setStyleSheet("font-size: 10px; color: #555;")
        lay.addWidget(note)
        row = QHBoxLayout()
        self._btn_add = QPushButton("Add selected")
        self._btn_add.setStyleSheet(_BTN_PRIMARY)
        self._btn_add.clicked.connect(self._on_add)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(self._btn_add)
        row.addWidget(btn_cancel)
        lay.addLayout(row)

    def _kick_load(self):
        sig = _Sig(self)
        sig.done.connect(self._on_loaded)
        sig.error.connect(lambda e: self._lbl_status.setText(f"Load failed: {e}"))
        def _work():
            try:
                sig.done.emit(_cpva_load_all_channels())
            except Exception as exc:
                sig.error.emit(str(exc))
        _bg(_work)

    def _on_loaded(self, channels: list):
        global _cpva_channel_cache
        _cpva_channel_cache = channels
        self._all_channels = channels
        self._lbl_status.setText(
            f"{len(channels)} channels loaded. Type to filter."
            if channels else "CPVA returned no channels."
        )
        self._filter(self._edit.text())

    def _filter(self, text: str):
        q = text.strip().lower()
        self._lst.clear()
        if not q or not self._all_channels:
            return
        matches = [ch for ch in self._all_channels if q in ch.lower()]
        for ch in matches[:300]:
            self._lst.addItem(ch)
        extra = f" (showing top 300)" if len(matches) > 300 else ""
        self._lbl_status.setText(
            f"{len(matches)} match(es){extra}." if matches else "No matches."
        )

    def _on_add(self):
        items = self._lst.selectedItems()
        if not items:
            QMessageBox.information(self, "Add PV", "Select at least one channel from the list.")
            return
        self._added = [(it.text(), it.text()) for it in items]
        self.accept()

    def added_pvs(self) -> list:
        """Returns list of (label, channel) tuples."""
        return self._added


# ── PresetEditDialog ──────────────────────────────────────────────────────────
class PresetEditDialog(QDialog):
    """Save, load, and delete named PV presets."""
    preset_loaded = Signal(list)   # list of (label, channel) tuples

    def __init__(self, current_pvs: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Presets")
        self.resize(620, 440)
        self._sel_pvs: list = list(current_pvs)   # (label, channel) tuples — working copy
        self._presets = _load_search_presets()
        self._all_channels: list = list(_cpva_channel_cache)
        self._build_ui()
        self._refresh()
        self._refresh_list()
        if self._all_channels:
            self._lbl_search.setText(f"{len(self._all_channels)} channels. Type to filter.")
        else:
            self._kick_load()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        body.setSpacing(10)

        # ── Left: saved presets list ──────────────────────────────────────────
        left_w = QWidget()
        left_w.setFixedWidth(160)
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        left.addWidget(QLabel("Saved presets:"))
        self._lst_presets = QListWidget()
        left.addWidget(self._lst_presets, stretch=1)
        self._btn_load_preset = QPushButton("Load →")
        self._btn_load_preset.setToolTip("Load this preset's PVs into the selection.")
        self._btn_load_preset.clicked.connect(self._on_load_preset)
        left.addWidget(self._btn_load_preset)
        body.addWidget(left_w)

        # ── Right: search bar + combined list ─────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(4)

        right.addWidget(QLabel("Search channels to add (or click a green row to deselect):"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Type part of the channel name…")
        self._search_edit.textEdited.connect(self._refresh_list)
        right.addWidget(self._search_edit)
        self._lbl_search = QLabel("Loading channels from CPVA…")
        self._lbl_search.setStyleSheet("color: #666; font-size: 10px;")
        right.addWidget(self._lbl_search)

        # One combined list: selected PVs (green, top) + search results (below)
        self._lst_pvs = QListWidget()
        self._lst_pvs.itemClicked.connect(self._on_item_clicked)
        right.addWidget(self._lst_pvs, stretch=1)

        body.addLayout(right, stretch=1)
        outer.addLayout(body, stretch=1)

        # ── Bottom buttons ────────────────────────────────────────────────────
        btns = QHBoxLayout()
        self._btn_save = QPushButton("Save PVs as preset…")
        self._btn_del  = QPushButton("Delete preset")
        self._btn_del.setStyleSheet(_BTN_DANGER)
        btn_apply = QPushButton("Apply && Close")
        btn_apply.setStyleSheet(_BTN_PRIMARY)
        btn_close = QPushButton("Close")
        self._btn_save.clicked.connect(self._on_save_preset)
        self._btn_del.clicked.connect(self._on_delete_preset)
        btn_apply.clicked.connect(self._on_apply)
        btn_close.clicked.connect(self.close)
        for b in (self._btn_save, self._btn_del, btn_apply, btn_close):
            btns.addWidget(b)
        outer.addLayout(btns)

    # ── Preset list management ────────────────────────────────────────────────
    def _refresh(self):
        self._lst_presets.blockSignals(True)
        self._lst_presets.clear()
        for p in self._presets:
            self._lst_presets.addItem(p["name"])
        self._lst_presets.blockSignals(False)

    def _on_load_preset(self):
        row = self._lst_presets.currentRow()
        if not (0 <= row < len(self._presets)):
            QMessageBox.information(self, "Load Preset", "Select a preset from the list first.")
            return
        pvs_data = self._presets[row].get("pvs", [])
        pvs = [(d["label"], d["channel"]) for d in pvs_data if d.get("label") and d.get("channel")]
        self._sel_pvs = pvs
        self._refresh_list(self._search_edit.text())

    def _on_save_preset(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        pvs = [{"label": lbl, "channel": ch} for lbl, ch in self._sel_pvs]
        for p in self._presets:
            if p["name"] == name:
                p["pvs"] = pvs
                break
        else:
            self._presets.append({"name": name, "pvs": pvs})
        _save_search_presets(self._presets)
        self._refresh()

    def _on_delete_preset(self):
        row = self._lst_presets.currentRow()
        if not (0 <= row < len(self._presets)):
            QMessageBox.information(self, "Delete Preset", "Select a preset from the list first.")
            return
        name = self._presets[row]["name"]
        if QMessageBox.question(
            self, "Delete Preset", f"Delete preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        del self._presets[row]
        _save_search_presets(self._presets)
        self._refresh()

    def _on_apply(self):
        if self._sel_pvs:
            self.preset_loaded.emit(self._sel_pvs)
        self.close()

    # ── Combined channel list (selected PVs + search results) ─────────────────
    def _refresh_list(self, text: str = ""):
        q = (text.strip().lower() if isinstance(text, str) else "")
        sel_channels = {ch for _, ch in self._sel_pvs}

        self._lst_pvs.blockSignals(True)
        self._lst_pvs.clear()

        # 1. Selected PVs always at top, green background
        for lbl, ch in self._sel_pvs:
            item = QListWidgetItem(f"✓  {lbl}  [{ch}]")
            item.setData(Qt.ItemDataRole.UserRole, ch)
            item.setToolTip(f"Selected — click to deselect\n{ch}")
            item.setBackground(QColor("#c8e6c9"))
            self._lst_pvs.addItem(item)

        # 2. Search results below (not already selected)
        if q and self._all_channels:
            matches = [ch for ch in self._all_channels
                       if q in ch.lower() and ch not in sel_channels]
            for ch in matches[:300]:
                item = QListWidgetItem(ch)
                item.setData(Qt.ItemDataRole.UserRole, ch)
                item.setToolTip(f"Click to add to selection\n{ch}")
                self._lst_pvs.addItem(item)
            extra = " (top 300)" if len(matches) > 300 else ""
            self._lbl_search.setText(f"{len(matches)} match(es){extra}.")
        elif q:
            self._lbl_search.setText("No matches.")

        self._lst_pvs.blockSignals(False)

    def _on_item_clicked(self, item: QListWidgetItem):
        ch = item.data(Qt.ItemDataRole.UserRole)
        # If already selected → remove it
        for i, (lbl, existing_ch) in enumerate(self._sel_pvs):
            if existing_ch == ch:
                del self._sel_pvs[i]
                self._refresh_list(self._search_edit.text())
                return
        # Otherwise → add to selection (use channel as label initially)
        self._sel_pvs.append((ch, ch))
        self._refresh_list(self._search_edit.text())

    # ── Channel loading ───────────────────────────────────────────────────────
    def _kick_load(self):
        sig = _Sig(self)
        sig.done.connect(self._on_ch_loaded)
        sig.error.connect(lambda e: self._lbl_search.setText(f"Load failed: {e}"))
        def _work():
            try:
                sig.done.emit(_cpva_load_all_channels())
            except Exception as exc:
                sig.error.emit(str(exc))
        _bg(_work)

    def _on_ch_loaded(self, channels: list):
        global _cpva_channel_cache
        _cpva_channel_cache = channels
        self._all_channels = channels
        self._lbl_search.setText(
            f"{len(channels)} channels. Type to filter."
            if channels else "CPVA returned no channels."
        )
        self._refresh_list(self._search_edit.text())


# ── ExportDialog ──────────────────────────────────────────────────────────────
class ExportDialog(QDialog):
    """Pick what to export: one CSV (details + curve data) and/or the graph image."""

    def __init__(self, n_regions: int, n_live: int, method: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export results")
        self.setModal(True)
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        parts = []
        if n_regions:
            parts.append(f"{n_regions} spectra")
        if n_live:
            parts.append(f"{n_live} live shot(s)")
        hdr = QLabel("Export " + " + ".join(parts) + ":")
        hdr.setStyleSheet("font-weight: 700; font-size: 12px;")
        lay.addWidget(hdr)

        self._chk_data = QCheckBox(
            f"Data table  →  CSV  (details + wavelength/{method}/std + live shots)"
        )
        self._chk_graph = QCheckBox("Graph image  →  picture of the spectra plot")
        for c in (self._chk_data, self._chk_graph):
            c.setChecked(True)
            c.setStyleSheet(_CHK_STYLE)
            lay.addWidget(c)

        row_fmt = QHBoxLayout()
        row_fmt.addWidget(QLabel("Image format:"))
        self._cmb_fmt = QComboBox()
        self._cmb_fmt.addItems(["png", "pdf", "svg"])
        row_fmt.addWidget(self._cmb_fmt, stretch=1)
        lay.addLayout(row_fmt)

        note = QLabel(
            "One CSV holds a details block (date, time, energy, dispersion orders) "
            "followed by the curve table. A 'sep=;' line and a decimal point let "
            "Excel open it directly in any locale."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "background:#e8f0fe; border:1px solid #90CAF9; border-radius:4px; "
            "padding:6px; font-size:11px; color:#0D47A1;"
        )
        lay.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Export  \U0001F4BE")
        ok_btn.setStyleSheet(
            "QPushButton { background:#1565C0; color:white; font-weight:700; "
            "padding:6px 16px; border-radius:4px; }"
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def options(self) -> dict:
        return {
            "data":      self._chk_data.isChecked(),
            "graph":     self._chk_graph.isChecked(),
            "graph_fmt": self._cmb_fmt.currentText(),
        }


# ── _AxisLabelsDialog ─────────────────────────────────────────────────────────
class _AxisLabelsDialog(QDialog):
    """Edit axis title, X label and Y label."""

    def __init__(self, ax, canvas, parent=None):
        super().__init__(parent)
        self._ax = ax
        self._canvas = canvas
        self.setWindowTitle("Axis labels")
        self.setModal(True)
        self.setMinimumWidth(370)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        grp = QGroupBox("Labels")
        grp.setStyleSheet(_GROUP_STYLE)
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        def _row(label_text, current):
            r = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(65)
            edit = QLineEdit(current)
            r.addWidget(lbl)
            r.addWidget(edit, stretch=1)
            gl.addLayout(r)
            return edit

        self._e_title  = _row("Title:",   ax.get_title())
        self._e_xlabel = _row("X label:", ax.get_xlabel())
        self._e_ylabel = _row("Y label:", ax.get_ylabel())
        lay.addWidget(grp)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _apply(self):
        self._ax.set_title(self._e_title.text())
        self._ax.set_xlabel(self._e_xlabel.text())
        self._ax.set_ylabel(self._e_ylabel.text())
        self._canvas.draw_idle()
        self.accept()


# ── _AxisLimitsDialog ─────────────────────────────────────────────────────────
class _AxisLimitsDialog(QDialog):
    """Edit axis limits.  X limits are disabled for date-based axes."""

    def __init__(self, ax, canvas, parent=None):
        super().__init__(parent)
        self._ax = ax
        self._canvas = canvas
        self.setWindowTitle("Axis limits")
        self.setModal(True)
        self.setMinimumWidth(370)

        _date_x = isinstance(ax.xaxis.get_major_formatter(), mdates.DateFormatter)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        grp = QGroupBox("Limits")
        grp.setStyleSheet(_GROUP_STYLE)
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        self._e_xmin = self._e_xmax = None
        if _date_x:
            note = QLabel("X axis uses datetime — adjust range with zoom/pan tools.")
            note.setStyleSheet("color: #666; font-size: 10px;")
            note.setWordWrap(True)
            gl.addWidget(note)
        else:
            row_x = QHBoxLayout()
            lx = QLabel("X:")
            lx.setFixedWidth(20)
            self._e_xmin = QLineEdit(f"{xlim[0]:.6g}")
            self._e_xmax = QLineEdit(f"{xlim[1]:.6g}")
            row_x.addWidget(lx)
            row_x.addWidget(QLabel("min:"))
            row_x.addWidget(self._e_xmin, stretch=1)
            row_x.addWidget(QLabel("max:"))
            row_x.addWidget(self._e_xmax, stretch=1)
            gl.addLayout(row_x)

        row_y = QHBoxLayout()
        ly = QLabel("Y:")
        ly.setFixedWidth(20)
        self._e_ymin = QLineEdit(f"{ylim[0]:.6g}")
        self._e_ymax = QLineEdit(f"{ylim[1]:.6g}")
        row_y.addWidget(ly)
        row_y.addWidget(QLabel("min:"))
        row_y.addWidget(self._e_ymin, stretch=1)
        row_y.addWidget(QLabel("max:"))
        row_y.addWidget(self._e_ymax, stretch=1)
        gl.addLayout(row_y)

        lay.addWidget(grp)

        btn_auto = QPushButton("Auto (reset to data)")
        btn_auto.setToolTip("Reset both axes to automatic limits based on plotted data.")
        btn_auto.clicked.connect(self._auto)
        lay.addWidget(btn_auto)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _auto(self):
        self._ax.autoscale()
        self._canvas.draw_idle()
        self.accept()

    def _apply(self):
        if self._e_xmin is not None:
            try:
                self._ax.set_xlim(float(self._e_xmin.text()), float(self._e_xmax.text()))
            except ValueError:
                pass
        try:
            self._ax.set_ylim(float(self._e_ymin.text()), float(self._e_ymax.text()))
        except ValueError:
            pass
        self._canvas.draw_idle()
        self.accept()


# ── SpectraWidget ─────────────────────────────────────────────────────────────
class SpectraWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._selected_day:     QDate | None             = None
        self._selected_days:    list[QDate]              = []   # multi-day support
        self._day_start_ns:     int                      = 0
        self._day_end_ns:       int                      = 0
        # User-editable list of scalar PVs to plot in the top "search" graph.
        self._search_pvs:       list[tuple[str, str]]    = self._load_search_pvs()
        # Waveform PVs used for spectrum analysis (X = wavelength axis, Y = intensity).
        # User picks any _X or _Y variant; the base and both axes are auto-derived.
        self._spec_base_pv:     str                      = self._load_spec_base()
        self._spec_x_pv:        str                      = self._spec_base_pv + "_X"
        self._spec_y_pv:        str                      = self._spec_base_pv + "_Y"
        self._color_mode:       str                      = "order"   # order | gdd | tod
        self._energy_data:      list[tuple[int, float]]  = []
        self._x_data:           np.ndarray | None        = None
        # Single source of truth for regions. Each region dict carries its own
        # selection (t_start/t_end/color/visible/expanded) and, once analyzed,
        # its results (mean/median/.../stack, energy_avg, orders, n).
        self._regions:          list[dict]               = []
        self._region_seq:       int                      = 0   # monotonic id source
        self._row_widgets:      dict[int, dict]          = {}  # id -> row widget refs
        self._span:             SpanSelector | None      = None
        self._top_user_xlim:    tuple | None             = None   # (xmin,xmax) when user zoomed
        self._top_user_ylim:    tuple | None             = None
        self._bot_user_xlim:    tuple | None             = None
        self._bot_user_ylim:    tuple | None             = None
        self._live              = False
        self._live_buf:         deque                    = deque(maxlen=LIVE_BUF_MAX)
        self._live_start_ns:    int                      = 0
        self._live_last_ns:     int                      = 0
        self._live_timer        = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._live_tick)
        self._blink_timer       = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_on          = False
        self._busy              = False
        self._cancel            = threading.Event()
        self._colorbar_bot:     object | None            = None
        self._colorbar_info:    dict   | None            = None
        self._last_saved_layout: dict                    = {}

        self._build_ui()
        self._connect_signals()
        self._connect_zoom_tracking()
        self._load_layout()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        root.addWidget(self._make_sidebar())
        root.addWidget(self._make_graphs(), stretch=1)

    def _make_sidebar(self) -> QWidget:
        sb = QWidget()
        sb.setFixedWidth(276)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(6)

        # ── Top status block (live indicator + selected day + messages) ──
        self._lbl_live_ind = QLabel("○  idle")
        self._lbl_live_ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_live_ind.setStyleSheet(
            "font-weight: 700; font-size: 13px; color: #999; "
            "border: 1px solid #ddd; border-radius: 4px; padding: 4px;"
        )
        lay.addWidget(self._lbl_live_ind)

        self._lbl_day = QLabel("No day selected")
        self._lbl_day.setStyleSheet("font-size: 11px; font-weight: 700; color: #333;")
        lay.addWidget(self._lbl_day)

        self._lbl_status = QLabel("Ready.")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet("color: #555; font-size: 10px;")
        lay.addWidget(self._lbl_status)

        sep_top = QFrame()
        sep_top.setFrameShape(QFrame.Shape.HLine)
        sep_top.setStyleSheet("color: #ccc;")
        lay.addWidget(sep_top)

        # ── Day picker ─────────────────────────────────────────────────
        self._btn_pick_day = QPushButton("\U0001F4C5  Load day…")
        self._btn_pick_day.setStyleSheet(_BTN_PRIMARY)
        self._btn_pick_day.setToolTip(
            "Open the calendar and load a day (or a range of days) of data into the search graph."
        )
        lay.addWidget(self._btn_pick_day)

        # ── Search data by PV ──────────────────────────────────────────
        g_search = QGroupBox("Search data by")
        g_search.setStyleSheet(_GROUP_STYLE)
        sl = QVBoxLayout(g_search)
        sl.setSpacing(4)

        # Preset row
        row_preset = QHBoxLayout()
        row_preset.addWidget(QLabel("Preset:"))
        self._cmb_preset = QComboBox()
        self._cmb_preset.setToolTip("Load a saved set of PVs into the list below.")
        self._cmb_preset.addItem("-- select preset --")
        for p in _load_search_presets():
            self._cmb_preset.addItem(p["name"])
        row_preset.addWidget(self._cmb_preset, stretch=1)
        self._btn_edit_presets = QPushButton("Edit…")
        self._btn_edit_presets.setFixedWidth(46)
        self._btn_edit_presets.setToolTip("Save the current PV list as a preset, or load / delete presets.")
        row_preset.addWidget(self._btn_edit_presets)
        sl.addLayout(row_preset)

        # PV table
        self._tbl_pvs = QTableWidget(0, 2)
        self._tbl_pvs.setHorizontalHeaderLabels(["Label", "Channel"])
        self._tbl_pvs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._tbl_pvs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tbl_pvs.setColumnWidth(0, 100)
        self._tbl_pvs.verticalHeader().setVisible(False)
        self._tbl_pvs.verticalHeader().setDefaultSectionSize(22)
        self._tbl_pvs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl_pvs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tbl_pvs.setFixedHeight(90)
        self._tbl_pvs.setToolTip(
            "Click a row to plot that PV in the search graph. "
            "Double-click a label to rename it."
        )
        for i, (lbl, ch) in enumerate(self._search_pvs):
            self._tbl_pvs.insertRow(i)
            lbl_item = QTableWidgetItem(lbl)
            lbl_item.setToolTip(ch)
            ch_item = QTableWidgetItem(ch)
            ch_item.setFlags(ch_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ch_item.setToolTip(ch)
            self._tbl_pvs.setItem(i, 0, lbl_item)
            self._tbl_pvs.setItem(i, 1, ch_item)
        if self._search_pvs:
            self._tbl_pvs.selectRow(0)
        sl.addWidget(self._tbl_pvs)

        # Add / Remove buttons
        row_pv = QHBoxLayout()
        self._btn_add_pv = QPushButton("+ Add PV")
        self._btn_add_pv.setToolTip("Search CPVA channels and add one or more to the list.")
        self._btn_rem_pv = QPushButton("✕ Remove")
        self._btn_rem_pv.setToolTip("Remove the selected PV from the list.")
        self._btn_rem_pv.setEnabled(bool(self._search_pvs))
        row_pv.addWidget(self._btn_add_pv)
        row_pv.addWidget(self._btn_rem_pv)
        sl.addLayout(row_pv)
        lay.addWidget(g_search)

        # ── Mode ───────────────────────────────────────────────────────
        g_mode = QGroupBox("Mode")
        g_mode.setStyleSheet(_GROUP_STYLE)
        row_m = QHBoxLayout(g_mode)
        self._btn_archive   = QPushButton("Archive")
        self._btn_live_mode = QPushButton("Live")
        self._btn_archive.setToolTip("Browse past data: load a day and select spectra from the archive.")
        self._btn_live_mode.setToolTip("Stream the latest spectra in real time and average the last N shots.")
        self._btn_archive.setCheckable(True)
        self._btn_live_mode.setCheckable(True)
        self._btn_archive.setChecked(True)
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.setExclusive(True)
        self._mode_grp.addButton(self._btn_archive)
        self._mode_grp.addButton(self._btn_live_mode)
        row_m.addWidget(self._btn_archive)
        row_m.addWidget(self._btn_live_mode)
        lay.addWidget(g_mode)

        # ── Spectrum PV ────────────────────────────────────────────────
        g_spec = QGroupBox("Spectrum PV")
        g_spec.setStyleSheet(_GROUP_STYLE)
        spec_l = QVBoxLayout(g_spec)
        spec_l.setSpacing(4)

        row_sp = QHBoxLayout()
        self._lbl_spec_base = QLineEdit(self._spec_base_pv)
        self._lbl_spec_base.setReadOnly(True)
        self._lbl_spec_base.setToolTip(
            f"X axis: {self._spec_x_pv}\nY axis: {self._spec_y_pv}"
        )
        self._lbl_spec_base.setStyleSheet("font-size: 9px; color: #555;")
        row_sp.addWidget(self._lbl_spec_base, stretch=1)
        self._btn_spec = QPushButton("Change…")
        self._btn_spec.setFixedWidth(62)
        self._btn_spec.setToolTip(
            "Search CPVA for a spectrum PV. Pick any _X or _Y variant — "
            "both axes are paired automatically."
        )
        row_sp.addWidget(self._btn_spec)
        spec_l.addLayout(row_sp)

        self._lbl_spec_pair = QLabel(f"→ {self._spec_x_pv}  /  {self._spec_y_pv}")
        self._lbl_spec_pair.setStyleSheet("font-size: 9px; color: #888;")
        self._lbl_spec_pair.setWordWrap(True)
        spec_l.addWidget(self._lbl_spec_pair)

        lay.addWidget(g_spec)

        # ── Live controls ──────────────────────────────────────────────
        self._g_live = QGroupBox("Live")
        self._g_live.setStyleSheet(_GROUP_STYLE)
        live_l = QVBoxLayout(self._g_live)
        row_n = QHBoxLayout()
        row_n.addWidget(QLabel("Average last N:"))
        self._sb_live_n = QSpinBox()
        self._sb_live_n.setRange(1, LIVE_BUF_MAX)
        self._sb_live_n.setValue(DEFAULT_LIVE_N)
        self._sb_live_n.setToolTip(
            "How many of the most-recent live spectra to average together "
            "(the newest shot is always drawn on top in red)."
        )
        row_n.addWidget(self._sb_live_n)
        live_l.addLayout(row_n)
        self._btn_live_start = QPushButton("▶  Start Live")
        self._btn_live_start.setStyleSheet(_BTN_SUCCESS)
        self._btn_live_start.setToolTip(
            "Start/stop streaming live spectra. The bottom graph shows the newest shot "
            "plus the average of the last N spectra."
        )
        live_l.addWidget(self._btn_live_start)
        # (the blinking "live is running" indicator lives in the top status block)
        self._g_live.setVisible(False)
        lay.addWidget(self._g_live)

        # (region selection list lives in the collapsible block, not here)

        # ── Display options ────────────────────────────────────────────
        g_disp = QGroupBox("Display")
        g_disp.setStyleSheet(_GROUP_STYLE)
        disp_l = QVBoxLayout(g_disp)
        row_method = QHBoxLayout()
        row_method.addWidget(QLabel("Average:"))
        self._cmb_method = QComboBox()
        self._cmb_method.addItems(list(_METHODS.keys()))
        self._cmb_method.setToolTip(
            "How to combine the spectra in each region:\n"
            "• Mean — plain average\n"
            "• Median — robust against outlier shots\n"
            "• Trimmed mean 10% — drops the 10% lowest/highest values\n"
            "• Sigma-clipped mean — drops points beyond 3σ, then averages"
        )
        row_method.addWidget(self._cmb_method, stretch=1)
        disp_l.addLayout(row_method)
        # ── Colour spectra by ──────────────────────────────────────────
        row_color = QHBoxLayout()
        row_color.addWidget(QLabel("Colour by:"))
        self._cmb_color = QComboBox()
        self._cmb_color.addItems(["Selection order", "GDD", "TOD"])
        self._cmb_color.setToolTip(
            "How to colour the averaged spectra:\n"
            "• Selection order — each spectrum a distinct colour (1, 2, 3 …)\n"
            "• GDD / TOD — rainbow scale by the dispersion value (low → high), so "
            "spectra with similar GDD/TOD share a colour. Needs analyzed spectra."
        )
        row_color.addWidget(self._cmb_color, stretch=1)
        disp_l.addLayout(row_color)
        self._chk_normalize = QCheckBox("Normalize to peak")
        self._chk_std       = QCheckBox("Standard deviation (±1σ)")
        self._chk_show_energy = QCheckBox("Show search graph")
        self._chk_show_energy.setChecked(True)
        self._chk_show_energy.setToolTip(
            "Uncheck to minimize the top search graph — the spectra graph then fills "
            "the whole window."
        )
        for c in (self._chk_normalize, self._chk_std, self._chk_show_energy):
            c.setStyleSheet(_CHK_STYLE)
            disp_l.addWidget(c)
        lay.addWidget(g_disp)

        # ── X range ────────────────────────────────────────────────────
        g_xr = QGroupBox("Spectrum range [nm]")
        g_xr.setStyleSheet(_GROUP_STYLE)
        xr_l = QHBoxLayout(g_xr)
        xr_l.addWidget(QLabel("From:"))
        self._sb_x_min = QSpinBox()
        self._sb_x_min.setRange(-9999, 9999)
        self._sb_x_min.setValue(700)
        xr_l.addWidget(self._sb_x_min)
        xr_l.addWidget(QLabel("To:"))
        self._sb_x_max = QSpinBox()
        self._sb_x_max.setRange(-9999, 9999)
        self._sb_x_max.setValue(900)
        xr_l.addWidget(self._sb_x_max)
        lay.addWidget(g_xr)

        # ── Selected regions (list + analyze + progress) ───────────────
        # Lives here, under "Spectrum range", to save horizontal space.
        lay.addWidget(self._make_region_panel(), stretch=1)

        # ── Export ─────────────────────────────────────────────────────
        self._btn_export = QPushButton("\U0001F4BE  Export results")
        self._btn_export.setEnabled(False)
        self._btn_export.setToolTip(
            "Export the analyzed spectra to a CSV (details + curves) and/or save the plot image."
        )
        lay.addWidget(self._btn_export)

        return sb

    def _make_canvas_panel(self, suffix: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        fig = Figure(tight_layout={"pad": 0.3})
        ax  = fig.add_subplot(111)
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Matplotlib tints toolbar icons from the palette: on a dark inherited
        # palette it makes them white (invisible on our white bar). Force a light
        # palette on the parent BEFORE building the toolbar so icons stay black.
        pal = w.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button):
            pal.setColor(role, QColor("white"))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
                     QPalette.ColorRole.Text):
            pal.setColor(role, QColor("black"))
        w.setPalette(pal)
        tb = _CustomToolbar(canvas, w)
        tb.setPalette(pal)
        tb.setStyleSheet(_TB_STYLE)
        for action in tb.actions():
            hint = _TB_HINTS.get(action.text())
            if hint:
                action.setToolTip(hint)
        # Remove "Customize" button (axis/curve editor) — moved to right-click on the axis
        for action in tb.actions():
            if action.text() == "Customize":
                tb.removeAction(action)
                break
        v.addWidget(tb)
        v.addWidget(canvas, stretch=1)
        setattr(self, f"_fig_{suffix}",    fig)
        setattr(self, f"_ax_{suffix}",     ax)
        setattr(self, f"_canvas_{suffix}", canvas)
        setattr(self, f"_tb_{suffix}",     tb)

        # Right-click inside the axis → "Edit axis…" context menu.
        # Uses Qt CustomContextMenu (reliable); mpl button_press_event misses
        # right-click because Qt intercepts it before matplotlib sees it.
        canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _on_context_menu(pos, _fig=fig, _tb=tb, _canvas=canvas):
            # Convert Qt coords (origin top-left, y↓) → display coords (origin bottom-left, y↑)
            x_disp = pos.x()
            y_disp = _canvas.height() - pos.y()
            for ax in _fig.get_axes():
                if not ax.get_window_extent().contains(x_disp, y_disp):
                    continue
                menu = QMenu(_canvas)

                act_lim    = menu.addAction("Axis limits…")
                act_labels = menu.addAction("Axis labels…")
                menu.addSeparator()

                # Grid
                major_lines = ax.get_xgridlines()
                major_on = bool(major_lines) and major_lines[0].get_visible()
                minor_on = getattr(ax, "_ctx_minor_grid", False)
                act_major = menu.addAction("Major grid")
                act_major.setCheckable(True)
                act_major.setChecked(major_on)
                act_minor = menu.addAction("Minor grid")
                act_minor.setCheckable(True)
                act_minor.setChecked(minor_on)
                menu.addSeparator()

                # Y scale
                y_menu  = menu.addMenu("Y scale")
                act_ylin = y_menu.addAction("Linear")
                act_ylin.setCheckable(True)
                act_ylog = y_menu.addAction("Logarithmic")
                act_ylog.setCheckable(True)
                if ax.get_yscale() == "log":
                    act_ylog.setChecked(True)
                else:
                    act_ylin.setChecked(True)
                menu.addSeparator()

                act_reset = menu.addAction("Reset view")

                chosen = menu.exec(_canvas.mapToGlobal(pos))

                if chosen is act_lim:
                    _AxisLimitsDialog(ax, _canvas, _canvas).exec()
                elif chosen is act_labels:
                    _AxisLabelsDialog(ax, _canvas, _canvas).exec()
                elif chosen is act_major:
                    ax.grid(not major_on, which="major", alpha=0.25)
                    _canvas.draw_idle()
                elif chosen is act_minor:
                    new_minor = not minor_on
                    ax._ctx_minor_grid = new_minor
                    if new_minor:
                        ax.minorticks_on()
                        ax.grid(True, which="minor", alpha=0.10, linestyle=":")
                    else:
                        ax.grid(False, which="minor")
                        ax.minorticks_off()
                    _canvas.draw_idle()
                elif chosen is act_ylin:
                    try:
                        ax.set_yscale("linear")
                        _canvas.draw_idle()
                    except Exception:
                        pass
                elif chosen is act_ylog:
                    try:
                        ax.set_yscale("log")
                        _canvas.draw_idle()
                    except Exception:
                        pass
                elif chosen is act_reset:
                    _tb.home()
                return

        canvas.customContextMenuRequested.connect(_on_context_menu)
        return w

    def _make_region_panel(self) -> QWidget:
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        lbl = QLabel("Selected spectra")
        lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        head.addWidget(lbl, stretch=1)
        self._btn_expand_all = QPushButton("⤢ Expand all")
        self._btn_expand_all.setToolTip("Expand all spectra to show their details (or collapse them all)")
        self._btn_expand_all.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 2px 6px; border: 1px solid #c8c8c8; "
            "border-radius: 3px; background: #f4f4f4; }"
            "QPushButton:hover { background: #e8f0fe; }"
            "QPushButton:disabled { color: #aaa; }"
        )
        self._btn_expand_all.clicked.connect(self._toggle_all_expanded)
        self._btn_expand_all.setEnabled(False)
        head.addWidget(self._btn_expand_all)
        v.addLayout(head)

        self._regions_w   = QWidget()
        self._regions_lay = QVBoxLayout(self._regions_w)
        self._regions_lay.setContentsMargins(0, 0, 0, 0)
        self._regions_lay.setSpacing(0)
        self._regions_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._regions_scroll = QScrollArea()
        self._regions_scroll.setWidget(self._regions_w)
        self._regions_scroll.setWidgetResizable(True)
        self._regions_scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #b0b0b0; border-radius: 4px; background: white; }"
        )
        v.addWidget(self._regions_scroll, stretch=1)

        row_btns = QHBoxLayout()
        self._btn_clear_regs = QPushButton("Clear all")
        self._btn_clear_regs.setToolTip("Remove all selected spectra from the list.")
        self._btn_analyze    = QPushButton("✓  Analyze")
        self._btn_analyze.setEnabled(False)
        self._btn_analyze.setStyleSheet(_BTN_SUCCESS)
        self._btn_analyze.setToolTip(
            "Fetch and average the spectra in every not-yet-analyzed selection, then plot them."
        )
        row_btns.addWidget(self._btn_clear_regs)
        row_btns.addWidget(self._btn_analyze)
        v.addLayout(row_btns)

        # analysis progress bar (hidden until analysis runs)
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("Analyzing  %v / %m  (%p%)")
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { border: 1px solid #b0b0b0; border-radius: 4px; "
            "text-align: center; height: 20px; font-size: 11px; font-weight: 700; }"
            "QProgressBar::chunk { background: #2E7D32; border-radius: 3px; }"
        )
        v.addWidget(self._progress)
        return col

    def _make_graphs(self) -> QWidget:
        # vertical splitter [SBW4 energy (top) | spectra (bottom)]
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._top_container = self._make_canvas_panel("top")
        splitter.addWidget(self._top_container)
        splitter.addWidget(self._make_canvas_panel("bot"))
        splitter.setSizes([440, 320])
        self._splitter = splitter

        self._draw_top_empty()
        self._draw_bot_empty()
        self._rebuild_regions_ui()
        return splitter

    def _update_top_visibility(self):
        """Energy graph shows only in archive mode and when the user wants it;
        otherwise the spectra graph fills the whole area."""
        show = self._btn_archive.isChecked() and self._chk_show_energy.isChecked()
        self._top_container.setVisible(show)
        self._splitter.setSizes([440, 320] if show else [0, max(1, self.height())])

    # ── Signal wiring ─────────────────────────────────────────────────────────
    def _connect_signals(self):
        self._btn_pick_day.clicked.connect(self._pick_day)
        self._btn_archive.clicked.connect(lambda: self._set_live_mode(False))
        self._btn_live_mode.clicked.connect(lambda: self._set_live_mode(True))
        self._btn_live_start.clicked.connect(self._toggle_live)
        self._btn_analyze.clicked.connect(self._run_analysis)
        self._btn_clear_regs.clicked.connect(self._clear_regions)
        self._btn_export.clicked.connect(self._export)
        self._chk_normalize.stateChanged.connect(self._redraw_spectra)
        self._chk_std.stateChanged.connect(self._redraw_spectra)
        self._chk_show_energy.toggled.connect(self._update_top_visibility)
        self._cmb_method.currentIndexChanged.connect(self._redraw_spectra)
        self._sb_x_min.valueChanged.connect(self._redraw_spectra)
        self._sb_x_max.valueChanged.connect(self._redraw_spectra)
        self._sb_live_n.valueChanged.connect(self._redraw_spectra)
        self._tbl_pvs.itemSelectionChanged.connect(self._on_search_pv_changed)
        self._tbl_pvs.itemChanged.connect(self._on_pv_label_edited)
        self._btn_add_pv.clicked.connect(self._open_add_pv_dialog)
        self._btn_rem_pv.clicked.connect(self._remove_selected_pv)
        self._btn_spec.clicked.connect(self._change_spec_pv)
        self._cmb_preset.currentIndexChanged.connect(self._on_preset_combo_changed)
        self._btn_edit_presets.clicked.connect(self._open_edit_presets_dialog)
        self._cmb_color.currentIndexChanged.connect(self._on_color_mode_changed)
        self._splitter.splitterMoved.connect(self._save_layout)
        self._tb_top.subplot_params_changed.connect(self._save_layout)
        self._tb_bot.subplot_params_changed.connect(self._save_layout)

    def _connect_zoom_tracking(self):
        """Save user's pan/zoom state so redraws don't reset it."""
        def _mk_handler(ax, xlim_attr, ylim_attr, redrawing_attr):
            def _on_xlim(a):
                if not getattr(self, redrawing_attr, False):
                    setattr(self, xlim_attr, a.get_xlim())
            def _on_ylim(a):
                if not getattr(self, redrawing_attr, False):
                    setattr(self, ylim_attr, a.get_ylim())
            ax.callbacks.connect('xlim_changed', _on_xlim)
            ax.callbacks.connect('ylim_changed', _on_ylim)
        _mk_handler(self._ax_top, '_top_user_xlim', '_top_user_ylim', '_top_redrawing')
        _mk_handler(self._ax_bot, '_bot_user_xlim', '_bot_user_ylim', '_bot_redrawing')
        # Clear saved zoom when user presses Home (resets to full data view)
        for action in self._tb_top.actions():
            if action.text() == "Home":
                action.triggered.connect(lambda: setattr(self, '_top_user_xlim', None) or
                                                 setattr(self, '_top_user_ylim', None))
        for action in self._tb_bot.actions():
            if action.text() == "Home":
                action.triggered.connect(lambda: setattr(self, '_bot_user_xlim', None) or
                                                 setattr(self, '_bot_user_ylim', None))

        # ── "Select spectra" mode button (top toolbar only) ───────────────────
        # Checked  = drag on top graph selects a new time region (default)
        # Unchecked = Pan or Zoom is active; span selection is suspended
        act_sel = QAction("Select", self._tb_top)
        act_sel.setCheckable(True)
        act_sel.setChecked(True)
        act_sel.setToolTip("Drag on the graph to select a time region (spectrum)")
        first = self._tb_top.actions()[0] if self._tb_top.actions() else None
        self._tb_top.insertAction(first, act_sel)
        self._act_select = act_sel

        _changing = [False]   # guard against recursive toggling

        def _on_select_toggled(checked):
            if _changing[0]:
                return
            _changing[0] = True
            try:
                if not checked:
                    # Don't allow unchecking Select unless pan/zoom is taking over
                    pan_zoom_on = any(
                        a.isChecked() for a in self._tb_top.actions()
                        if a.text() in ("Pan", "Zoom")
                    )
                    if not pan_zoom_on:
                        act_sel.setChecked(True)   # keep Select active
                        return
                if self._span is not None:
                    self._span.set_active(checked)
                if checked:
                    # Cancel any active pan/zoom mode
                    for a in self._tb_top.actions():
                        if a.text() == "Pan" and a.isChecked():
                            self._tb_top.pan()
                        elif a.text() == "Zoom" and a.isChecked():
                            self._tb_top.zoom()
            finally:
                _changing[0] = False

        def _on_pan_zoom_toggled(checked):
            if _changing[0]:
                return
            _changing[0] = True
            try:
                if checked:
                    act_sel.setChecked(False)
                    if self._span is not None:
                        self._span.set_active(False)
                else:
                    # If neither pan nor zoom remains active, restore Select
                    still_on = any(
                        a.isChecked() for a in self._tb_top.actions()
                        if a.text() in ("Pan", "Zoom")
                    )
                    if not still_on:
                        act_sel.setChecked(True)
                        if self._span is not None:
                            self._span.set_active(True)
            finally:
                _changing[0] = False

        act_sel.toggled.connect(_on_select_toggled)
        for a in self._tb_top.actions():
            if a.text() in ("Pan", "Zoom"):
                a.toggled.connect(_on_pan_zoom_toggled)

    # ── Mode ──────────────────────────────────────────────────────────────────
    def _set_live_mode(self, live: bool):
        self._g_live.setVisible(live)
        if not live:
            self._stop_live()
            # back to archive: redraw the loaded day, if any
            if self._energy_data:
                self._draw_energy()
                self._install_span()
            else:
                self._draw_top_empty()
        # In live mode the search graph is irrelevant; in archive it follows the
        # "Show search graph" checkbox.
        self._update_top_visibility()
        self._redraw_spectra()

    # ── Search-PV list (pick / add / remove / persist) ─────────────────────────
    def _load_search_pvs(self) -> "list[tuple[str, str]]":
        try:
            with open(_search_pv_config_path(), encoding="utf-8") as f:
                data = json.load(f)
            pvs = [(str(d["label"]), str(d["channel"]))
                   for d in data if d.get("label") and d.get("channel")]
            if pvs:
                return pvs
        except Exception:
            pass
        return list(DEFAULT_SEARCH_PVS)

    def _save_search_pvs(self):
        try:
            p = _search_pv_config_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump([{"label": lbl, "channel": ch} for lbl, ch in self._search_pvs],
                          f, indent=2)
        except Exception:
            pass

    def _load_spec_base(self) -> str:
        """Load the saved spectrum base PV, stripping any _X/_Y suffix."""
        try:
            with open(_spec_pvs_config_path(), encoding="utf-8") as f:
                data = json.load(f)
            base = str(data.get("base", "") or "").strip()
            if base:
                return base
        except Exception:
            pass
        return _strip_xy_suffix(PV_SPEC_X)

    def _save_spec_base(self):
        try:
            p = _spec_pvs_config_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"base": self._spec_base_pv}, f, indent=2)
        except Exception:
            pass

    def _change_spec_pv(self):
        """Open PvSearchDialog; derive _X/_Y pair from whatever the user picks."""
        dlg = PvSearchDialog(self)
        dlg.setWindowTitle("Select spectrum PV  (pick any _X or _Y variant)")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        added = dlg.added_pvs()
        if not added:
            return
        ch = added[0][1]
        base = _strip_xy_suffix(ch)
        self._spec_base_pv = base
        self._spec_x_pv    = base + "_X"
        self._spec_y_pv    = base + "_Y"
        self._x_data       = None   # invalidate cached X axis
        self._lbl_spec_base.setText(base)
        self._lbl_spec_base.setToolTip(
            f"X axis: {self._spec_x_pv}\nY axis: {self._spec_y_pv}"
        )
        self._lbl_spec_pair.setText(f"→ {self._spec_x_pv}  /  {self._spec_y_pv}")
        self._save_spec_base()
        if self._live:
            self._stop_live()
            self._set_status(f"Spectrum PV changed to {base} — live stopped.")
        else:
            self._set_status(f"Spectrum PV: {base}  (→ _X / _Y)")

    def _save_layout(self, *_):
        """Persist splitter sizes and (when manually adjusted) subplot margins."""
        data: dict = {"splitter": self._splitter.sizes()}
        for suffix, key in (("top", "fig_top"), ("bot", "fig_bot")):
            fig = getattr(self, f"_fig_{suffix}")
            engine = fig.get_layout_engine()
            # Only save subplot params when user has manually adjusted them
            # (tight/constrained layout engine is no longer active).
            if engine is None or type(engine).__name__ == "PlaceHolderLayoutEngine":
                sp = fig.subplotpars
                data[key] = {k: round(getattr(sp, k), 4)
                             for k in ("left", "right", "top", "bottom", "hspace", "wspace")}
        if data == self._last_saved_layout:
            return
        self._last_saved_layout = data
        try:
            p = _layout_config_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_layout(self):
        """Restore splitter sizes and subplot margins saved by _save_layout()."""
        try:
            with open(_layout_config_path(), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if "splitter" in data:
            sizes = data["splitter"]
            if isinstance(sizes, list) and len(sizes) == 2 and all(s >= 0 for s in sizes):
                self._splitter.setSizes([int(s) for s in sizes])
        _sp_keys = ("left", "right", "top", "bottom", "hspace", "wspace")
        for suffix, key in (("top", "fig_top"), ("bot", "fig_bot")):
            params = data.get(key)
            if not params:
                continue
            fig = getattr(self, f"_fig_{suffix}")
            try:
                kwargs = {k: float(params[k]) for k in _sp_keys if k in params}
                fig.set_layout_engine(None)   # disable tight_layout; use saved params
                fig.subplots_adjust(**kwargs)
            except Exception:
                pass

    def _active_search_channel(self) -> str:
        row = self._tbl_pvs.currentRow()
        return self._search_pvs[row][1] if 0 <= row < len(self._search_pvs) else PV_ENERGY

    def _active_search_label(self) -> str:
        row = self._tbl_pvs.currentRow()
        return self._search_pvs[row][0] if 0 <= row < len(self._search_pvs) else "Signal"

    def _refresh_pv_table(self, select_row: int = 0):
        self._tbl_pvs.blockSignals(True)
        self._tbl_pvs.setRowCount(0)
        for lbl, ch in self._search_pvs:
            r = self._tbl_pvs.rowCount()
            self._tbl_pvs.insertRow(r)
            lbl_item = QTableWidgetItem(lbl)
            lbl_item.setToolTip(ch)
            ch_item = QTableWidgetItem(ch)
            ch_item.setFlags(ch_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ch_item.setToolTip(ch)
            self._tbl_pvs.setItem(r, 0, lbl_item)
            self._tbl_pvs.setItem(r, 1, ch_item)
        if self._search_pvs:
            self._tbl_pvs.selectRow(min(max(select_row, 0), len(self._search_pvs) - 1))
        self._tbl_pvs.blockSignals(False)
        self._on_search_pv_changed()

    def _update_preset_combo(self):
        self._cmb_preset.blockSignals(True)
        self._cmb_preset.clear()
        self._cmb_preset.addItem("-- select preset --")
        for p in _load_search_presets():
            self._cmb_preset.addItem(p["name"])
        self._cmb_preset.setCurrentIndex(0)
        self._cmb_preset.blockSignals(False)

    def _on_preset_combo_changed(self, idx: int):
        if idx <= 0:
            return
        presets = _load_search_presets()
        pidx = idx - 1
        if not (0 <= pidx < len(presets)):
            return
        pvs_data = presets[pidx].get("pvs", [])
        pvs = [(d["label"], d["channel"]) for d in pvs_data if d.get("label") and d.get("channel")]
        if pvs:
            self._search_pvs = pvs
            self._save_search_pvs()
            self._refresh_pv_table(select_row=0)
        self._cmb_preset.blockSignals(True)
        self._cmb_preset.setCurrentIndex(0)
        self._cmb_preset.blockSignals(False)

    def _open_add_pv_dialog(self):
        dlg = PvSearchDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        added = dlg.added_pvs()
        existing_channels = {ch for _, ch in self._search_pvs}
        for lbl, ch in added:
            if ch not in existing_channels:
                self._search_pvs.append((lbl, ch))
                existing_channels.add(ch)
        self._save_search_pvs()
        self._refresh_pv_table(select_row=len(self._search_pvs) - 1)

    def _remove_selected_pv(self):
        row = self._tbl_pvs.currentRow()
        if not (0 <= row < len(self._search_pvs)):
            return
        if len(self._search_pvs) <= 1:
            QMessageBox.information(self, "Remove PV", "Keep at least one PV in the list.")
            return
        del self._search_pvs[row]
        self._save_search_pvs()
        self._refresh_pv_table(select_row=min(row, len(self._search_pvs) - 1))

    def _on_pv_label_edited(self, item: QTableWidgetItem):
        if item.column() != 0:
            return
        row = item.row()
        if not (0 <= row < len(self._search_pvs)):
            return
        new_lbl = item.text().strip()
        if not new_lbl:
            self._tbl_pvs.blockSignals(True)
            item.setText(self._search_pvs[row][0])
            self._tbl_pvs.blockSignals(False)
            return
        _, ch = self._search_pvs[row]
        if new_lbl != self._search_pvs[row][0]:
            self._search_pvs[row] = (new_lbl, ch)
            item.setToolTip(ch)
            self._save_search_pvs()

    def _open_edit_presets_dialog(self):
        dlg = PresetEditDialog(self._search_pvs, self)
        dlg.preset_loaded.connect(self._apply_preset)
        dlg.exec()
        self._update_preset_combo()

    def _apply_preset(self, pvs: list):
        self._search_pvs = pvs
        self._save_search_pvs()
        self._refresh_pv_table(select_row=0)

    def _on_search_pv_changed(self, *_):
        self._btn_rem_pv.setEnabled(self._tbl_pvs.currentRow() >= 0)
        if not self._live and self._selected_days:
            self._load_day_energy()

    def _on_color_mode_changed(self, *_):
        idx = self._cmb_color.currentIndex()
        self._color_mode = {0: "order", 1: "gdd", 2: "tod"}.get(idx, "order")
        self._redraw_spectra()

    # ── Day picker ────────────────────────────────────────────────────────────
    def _pick_day(self):
        dlg = DatePickerDialog(self, initial=self._selected_day or QDate.currentDate())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # switch UI back to archive mode
        self._btn_archive.setChecked(True)
        self._set_live_mode(False)
        dates = dlg.selected_dates()
        self._selected_days = dates
        self._selected_day  = dates[0]
        if len(dates) == 1:
            self._lbl_day.setText(f"Day: {dates[0].toString('yyyy-MM-dd')}")
        else:
            self._lbl_day.setText(
                f"Days: {dates[0].toString('yyyy-MM-dd')} → "
                f"{dates[-1].toString('yyyy-MM-dd')}  ({len(dates)} days)")
        # Keep already-selected spectra across day changes — they carry absolute
        # timestamps and stay in the list (delete them via the ✕ in the list).
        self._load_day_energy()

    def _load_day_energy(self):
        days = self._selected_days or ([self._selected_day] if self._selected_day else [])
        if not days:
            return
        start_ns, _ = _day_range_ns(days[0])
        _, end_ns   = _day_range_ns(days[-1])
        self._day_start_ns, self._day_end_ns = start_ns, end_ns
        channel = self._active_search_channel()
        label   = self._active_search_label()
        self._set_status(f"Loading {label}…")
        self._btn_pick_day.setEnabled(False)

        sig = _Sig(self)
        sig.done.connect(self._on_energy_loaded)
        sig.error.connect(self._on_energy_error)

        def _work():
            try:
                sig.done.emit(_fetch_scalars(channel, start_ns, end_ns))
            except Exception as e:
                sig.error.emit(str(e))

        self._cancel.clear()
        _bg(_work)

    def _on_energy_loaded(self, data: list):
        self._btn_pick_day.setEnabled(True)
        if self._cancel.is_set():
            self._set_status("Load cancelled.")
            return
        # Drop the "last value before start" sample EPICS returns, so the axis
        # is clamped to the selected day instead of stretching to the previous day.
        data = [(t, v) for (t, v) in data
                if self._day_start_ns <= t <= self._day_end_ns]
        self._energy_data = data
        label = self._active_search_label()
        if not data:
            self._set_status(f"No data for {label} on this day.")
            self._draw_top_empty(f"No data for {label}")
            return
        self._set_status(f"Loaded {len(data)} samples of {label}.")
        self._draw_energy()
        self._install_span()

    def _on_energy_error(self, err: str):
        self._btn_pick_day.setEnabled(True)
        self._set_status(f"Energy error: {err}")
        self._draw_top_empty("Error loading data")

    # ── Energy graph ──────────────────────────────────────────────────────────
    def _draw_top_empty(self, msg: str = "Select a day first  →  button on the left"):
        ax = self._ax_top
        ax.clear()
        ax.set_facecolor("white")
        ax.text(0.5, 0.5, msg, transform=ax.transAxes,
                ha="center", va="center", color="#aaa", fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        self._canvas_top.draw_idle()

    def _paint_region_spans(self, ax):
        # Regions can now span multiple days; spans outside the current day's
        # x-limits are simply clipped, so painting them all is harmless.
        for r in self._regions:
            ax.axvspan(
                mdates.date2num(_ns_to_dt(r["t_start"])),
                mdates.date2num(_ns_to_dt(r["t_end"])),
                alpha=0.25, color=r["color"], zorder=0,
            )

    def _draw_energy(self, reset_view: bool = True):
        ax = self._ax_top
        if reset_view:
            self._top_user_xlim = None
            self._top_user_ylim = None
        self._top_redrawing = True
        ax.clear()
        if not self._energy_data:
            return
        # sort by time so the connecting line follows chronological order
        data = sorted(self._energy_data, key=lambda tv: tv[0])
        times = [mdates.date2num(_ns_to_dt(t)) for t, _ in data]
        vals  = [v for _, v in data]
        ax.plot(times, vals, "-", lw=1.0, color="#1565C0", alpha=0.85,
                marker=".", ms=3, mfc="#0D47A1", mec="#0D47A1")

        days = self._selected_days or ([self._selected_day] if self._selected_day else [])
        multi = len(days) > 1
        # multi-day: show the date in each tick; single day: just the time
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%m-%d %H:%M" if multi else "%H:%M", tz=_PRAGUE))
        # clamp the view to the selected day/range (issue: previous day used to show)
        ax.set_xlim(mdates.date2num(_ns_to_dt(self._day_start_ns)),
                    mdates.date2num(_ns_to_dt(self._day_end_ns)))
        self._fig_top.autofmt_xdate(rotation=0, ha="center")
        # Task 6: always show the selected date on the axis, even for a single day.
        if days:
            date_str = (f"{days[0].toString('yyyy-MM-dd')} → {days[-1].toString('yyyy-MM-dd')}"
                        if multi else days[0].toString("yyyy-MM-dd"))
            ax.set_xlabel(f"Time   —   {date_str}")
        else:
            ax.set_xlabel("Time")
        label = self._active_search_label()
        ax.set_ylabel(label)
        ax.set_title(f"{label} — drag to select time region(s), then click Analyze")
        ax.grid(True, alpha=0.25)
        self._paint_region_spans(ax)
        self._top_redrawing = False
        if self._top_user_xlim is not None:
            ax.set_xlim(self._top_user_xlim)
        if self._top_user_ylim is not None:
            ax.set_ylim(self._top_user_ylim)
        self._canvas_top.draw_idle()

    def _install_span(self):
        if self._span is not None:
            self._span.set_active(False)
            self._span = None
        self._span = SpanSelector(
            self._ax_top,
            self._on_span,
            "horizontal",
            useblit=False,                 # reliable rendering of the committed span
            props=dict(alpha=0.20, facecolor="#90CAF9"),
            interactive=False,
        )
        # If pan/zoom is active, keep span suspended until Select is re-chosen
        if hasattr(self, '_act_select') and not self._act_select.isChecked():
            self._span.set_active(False)

    def _on_span(self, xmin: float, xmax: float):
        if xmax - xmin < 1e-9:
            return
        try:
            t_start = int(mdates.num2date(xmin).timestamp() * 1e9)
            t_end   = int(mdates.num2date(xmax).timestamp() * 1e9)
        except Exception:
            return
        rid   = self._region_seq
        self._region_seq += 1
        color = _REGION_COLORS[rid % len(_REGION_COLORS)]
        self._regions.append({
            "id": rid, "t_start": t_start, "t_end": t_end, "color": color,
            "visible": True, "expanded": False, "show_individual": False,
            "analyzed": False, "n": 0,
        })
        # add only the new span (keeps current zoom/pan — nothing else changes)
        self._ax_top.axvspan(xmin, xmax, alpha=0.25, color=color, zorder=0)
        self._canvas_top.draw_idle()
        self._rebuild_regions_ui()
        self._update_action_buttons()
        self._set_status(
            f"Spectrum {len(self._regions)} added: "
            f"{_fmt_hms(t_start)} – {_fmt_hms(t_end)}.  "
            f"Add more or click Analyze."
        )

    # ── Regions UI ────────────────────────────────────────────────────────────
    def _region_label(self, i: int) -> str:
        return f"Spectrum {i + 1}"

    def _find_region(self, rid: int) -> dict | None:
        return next((r for r in self._regions if r["id"] == rid), None)

    def _rebuild_regions_ui(self):
        self._row_widgets.clear()
        while self._regions_lay.count():
            item = self._regions_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._regions:
            empty = QLabel("Drag on the search graph\nto select a spectrum.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #999; font-size: 12px; padding: 8px;")
            self._regions_lay.addWidget(empty)
            return

        for i, r in enumerate(self._regions):
            self._regions_lay.addWidget(self._make_region_row(i, r))

    def _make_region_row(self, i: int, r: dict) -> QWidget:
        rid = r["id"]
        box = QFrame()
        box.setStyleSheet(
            "QFrame { border-bottom: 1px solid #e6e6e6; }"
        )
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── header (clickable name + eye + delete) ─────────────────────
        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(4)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {r['color']}; font-size: 15px; border: none;")
        dot.setFixedWidth(16)

        chev = "▾" if r["expanded"] else "▸"
        btn_name = QPushButton(f"{chev}  {self._region_label(i)}")
        btn_name.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_name.setToolTip("Click to show/hide details")
        btn_name.clicked.connect(lambda _, x=rid: self._toggle_region_expanded(x))

        btn_eye = QPushButton("\U0001F441")          # 👁
        btn_eye.setCheckable(True)
        btn_eye.setChecked(r["visible"])
        btn_eye.setFixedSize(34, 30)
        btn_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_eye.toggled.connect(lambda on, x=rid: self._toggle_region_visible(x, on))

        btn_del = QPushButton("✕")              # ✕
        btn_del.setFixedSize(34, 30)
        btn_del.setToolTip("Remove this spectrum")
        btn_del.setStyleSheet(
            "QPushButton { color:#B71C1C; font-weight:700; font-size:18px; "
            "border:none; background:transparent; }"
            "QPushButton:hover { background:#ffe0e0; border-radius:3px; }"
        )
        btn_del.clicked.connect(lambda _, x=rid: self._delete_region(x))

        h.addWidget(dot)
        h.addWidget(btn_name, stretch=1)
        h.addWidget(btn_eye)
        h.addWidget(btn_del)
        v.addWidget(header)

        # ── details (collapsible) ──────────────────────────────────────
        details = self._build_region_details(r)
        details.setVisible(r["expanded"])
        v.addWidget(details)

        self._row_widgets[rid] = {"name": btn_name, "eye": btn_eye, "details": details}
        self._apply_visibility_style(rid, r["visible"])
        return box

    def _build_region_details(self, r: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet(
            "QWidget { background: #fafafa; border: none; }"
            "QLabel { font-size: 12px; color: #333; border: none; }"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 4, 8, 8)
        lay.setSpacing(2)

        d0, d1 = _fmt_date(r["t_start"]), _fmt_date(r["t_end"])
        date_str = d0 if d0 == d1 else f"{d0} → {d1}"

        def add(text: str):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        add(f"<b>Date:</b> {date_str}")
        add(f"<b>Time:</b> {_fmt_hms(r['t_start'])} – {_fmt_hms(r['t_end'])}")

        if not r.get("analyzed"):
            note = QLabel("Not analyzed yet — click Analyze.")
            note.setStyleSheet("color: #999; font-style: italic; border: none;")
            lay.addWidget(note)
            return w

        add(f"<b># of spectra:</b> {r.get('n', 0)}")
        ea = r.get("energy_avg")
        if ea is not None:
            add(f"<b>SBW4 Output energy:</b> {ea:.3f} J  "
                f"<span style='color:#888'>(avg of {r.get('energy_n', 0)})</span>")
        else:
            add("<b>SBW4 Output energy:</b> n/a")

        orders = r.get("orders") or {}
        for label, _ in ORDER_PVS:
            val = orders.get(label)
            add(f"<b>{label}:</b> {round(val)}"
                if val is not None else f"<b>{label}:</b> n/a")

        if r.get("n", 0) > 0 and r.get("stack") is not None:
            chk = QCheckBox(f"Show all {r['n']} spectra in graph")
            chk.setChecked(r.get("show_individual", False))
            chk.setStyleSheet("QCheckBox { font-size: 12px; color: #1565C0; border: none; }")
            chk.toggled.connect(
                lambda on, x=r["id"]: self._toggle_region_individual(x, on)
            )
            lay.addWidget(chk)
        return w

    def _apply_visibility_style(self, rid: int, visible: bool):
        refs = self._row_widgets.get(rid)
        if not refs:
            return
        refs["eye"].setToolTip("Visible — click to hide" if visible
                               else "Hidden — click to show")
        refs["eye"].setStyleSheet(
            "QPushButton { border: none; background: transparent; font-size: 18px;"
            + (" }" if visible else " color: #c4c4c4; }")
            + "QPushButton:hover { background: #e8f0fe; border-radius: 3px; }"
        )
        name_color = "#111" if visible else "#aaa"
        deco = "" if visible else "text-decoration: line-through;"
        refs["name"].setStyleSheet(
            "QPushButton { text-align: left; border: none; background: transparent; "
            f"font-weight: 700; font-size: 13px; color: {name_color}; {deco} padding: 2px; }}"
            "QPushButton:hover { color: #1565C0; }"
        )

    def _toggle_region_expanded(self, rid: int):
        r = self._find_region(rid)
        refs = self._row_widgets.get(rid)
        if not r or not refs:
            return
        r["expanded"] = not r["expanded"]
        refs["details"].setVisible(r["expanded"])
        i = self._regions.index(r)
        chev = "▾" if r["expanded"] else "▸"
        refs["name"].setText(f"{chev}  {self._region_label(i)}")
        self._update_expand_all_btn()

    def _toggle_all_expanded(self):
        """Master button: expand every spectrum's details at once (or collapse all)."""
        if not self._regions:
            return
        expand = any(not r["expanded"] for r in self._regions)
        self._regions_w.setUpdatesEnabled(False)
        for i, r in enumerate(self._regions):
            r["expanded"] = expand
            refs = self._row_widgets.get(r["id"])
            if refs:
                refs["details"].setVisible(expand)
                chev = "▾" if expand else "▸"
                refs["name"].setText(f"{chev}  {self._region_label(i)}")
        self._regions_w.setUpdatesEnabled(True)
        self._update_expand_all_btn()

    def _update_expand_all_btn(self):
        btn = getattr(self, "_btn_expand_all", None)
        if btn is None:
            return
        btn.setEnabled(bool(self._regions))
        all_expanded = bool(self._regions) and all(r["expanded"] for r in self._regions)
        btn.setText("⤡ Collapse all" if all_expanded else "⤢ Expand all")

    def _toggle_region_visible(self, rid: int, visible: bool):
        r = self._find_region(rid)
        if not r:
            return
        r["visible"] = visible
        self._apply_visibility_style(rid, visible)
        self._redraw_spectra()

    def _toggle_region_individual(self, rid: int, show: bool):
        r = self._find_region(rid)
        if not r:
            return
        r["show_individual"] = show
        self._redraw_spectra()

    def _delete_region(self, rid: int):
        r = self._find_region(rid)
        if r is None:
            return
        self._regions.remove(r)
        self._rebuild_regions_ui()
        self._update_action_buttons()
        self._refresh_energy_view()
        self._redraw_spectra()

    def _clear_regions(self):
        self._regions.clear()
        self._region_seq = 0
        self._rebuild_regions_ui()
        self._update_action_buttons()
        self._refresh_energy_view()
        self._draw_bot_empty()

    def _update_action_buttons(self):
        self._btn_analyze.setEnabled(any(not r["analyzed"] for r in self._regions))
        self._btn_export.setEnabled(any(r["analyzed"] for r in self._regions))
        self._update_expand_all_btn()

    def _refresh_energy_view(self):
        """Redraw the archive energy graph (live mode has no energy graph)."""
        if self._live:
            return
        if self._energy_data:
            self._draw_energy(reset_view=False)
            self._install_span()

    # ── Analysis ──────────────────────────────────────────────────────────────
    def _run_analysis(self):
        if self._busy:
            return
        todo = [r for r in self._regions if not r["analyzed"]]
        if not todo:
            return
        self._busy = True
        self._btn_analyze.setEnabled(False)
        self._btn_pick_day.setEnabled(False)
        # snapshot only the immutable selection fields for the worker thread
        snap = [{"id": r["id"], "t_start": r["t_start"], "t_end": r["t_end"]}
                for r in todo]
        self._set_status(f"Loading spectra for {len(snap)} region(s)…")
        self._progress.setRange(0, len(snap))
        self._progress.setValue(0)
        self._progress.setVisible(True)

        sig = _Sig(self)
        sig.done.connect(self._on_analysis_done)
        sig.error.connect(self._on_analysis_error)
        sig.progress.connect(self._set_status)
        sig.progress_n.connect(self._on_analysis_progress)

        x_cached  = self._x_data
        spec_x_ch = self._spec_x_pv
        spec_y_ch = self._spec_y_pv

        def _work():
            x_data = x_cached
            if x_data is None:
                sig.progress.emit("Loading spectrometer X axis…")
                r0 = snap[0]
                wf = _fetch_waveforms(
                    spec_x_ch,
                    r0["t_start"] - int(10 * 60 * 1e9),
                    r0["t_end"]   + int(10 * 60 * 1e9),
                )
                if wf:
                    x_data = wf[-1][1]

            results = []
            for i, r in enumerate(snap):
                if self._cancel.is_set():
                    break
                sig.progress.emit(
                    f"Spectrum {i+1}/{len(snap)}: "
                    f"{_fmt_hms(r['t_start'])}–{_fmt_hms(r['t_end'])}…"
                )
                wfs = _fetch_waveforms(spec_y_ch, r["t_start"], r["t_end"])
                st = _compute_stats([a for _, a in wfs])

                # extra per-region scalars: output energy + dispersion orders
                energy_vals = [v for _, v in _fetch_scalars(
                    PV_ENERGY, r["t_start"], r["t_end"])]
                orders = {}
                for label, pv in ORDER_PVS:
                    vals = [v for _, v in _fetch_scalars(pv, r["t_start"], r["t_end"])]
                    orders[label] = float(np.mean(vals)) if vals else None

                res = {
                    "id": r["id"], "x": x_data, "orders": orders,
                    "energy_avg": float(np.mean(energy_vals)) if energy_vals else None,
                    "energy_n": len(energy_vals),
                }
                if st is None:
                    res.update({"mean": None, "median": None, "trimmed": None,
                                "sigma": None, "std": None, "stack": None, "n": 0})
                else:
                    res.update(st)
                results.append(res)
                sig.progress_n.emit(i + 1, len(snap))

            sig.done.emit((x_data, results))

        self._cancel.clear()
        _bg(_work)

    def _on_analysis_progress(self, done: int, total: int):
        self._progress.setMaximum(total)
        self._progress.setValue(done)

    def _on_analysis_done(self, payload):
        x_data, results = payload
        if x_data is not None:
            self._x_data = x_data
        by_id = {res["id"]: res for res in results}
        for r in self._regions:
            res = by_id.get(r["id"])
            if res is None:
                continue
            r.update(res)
            r["analyzed"] = True
        self._busy = False
        self._btn_pick_day.setEnabled(True)
        self._progress.setVisible(False)
        self._update_action_buttons()
        if self._cancel.is_set():
            self._rebuild_regions_ui()
            self._redraw_spectra()
            self._set_status(f"Analysis cancelled ({len(results)} region(s) done).")
            return
        n_total = sum(res["n"] for res in results)
        empties = [self._region_label(i) for i, r in enumerate(self._regions)
                   if r["id"] in by_id and r.get("n", 0) == 0]
        msg = f"Analysis done. {n_total} spectra total."
        if empties:
            msg += f"  No spectra in: {', '.join(empties)}."
        self._set_status(msg)
        self._rebuild_regions_ui()      # populate details (energy, orders, n)
        self._redraw_spectra()

    def _on_analysis_error(self, err: str):
        self._busy = False
        self._btn_pick_day.setEnabled(True)
        self._progress.setVisible(False)
        self._update_action_buttons()
        self._set_status(f"Error: {err}")

    # ── Spectra graph ─────────────────────────────────────────────────────────
    def _draw_bot_empty(self, msg: str = "Analyze a spectrum in the search graph"):
        ax = self._ax_bot
        ax.clear()
        ax.set_facecolor("white")
        ax.text(0.5, 0.5, msg, transform=ax.transAxes,
                ha="center", va="center", color="#aaa", fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        self._canvas_bot.draw_idle()

    def _plot_spectrum(self, ax, x, avg, std, color, label, normalize, std_band, lw=1.6):
        if x is None or len(x) != len(avg):
            x = np.arange(len(avg))
        x = np.asarray(x, dtype=float)
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp, yp = x[mask], avg[mask]
        ys = std[mask] if std is not None else None
        if normalize and yp.size and yp.max() > 0:
            peak = yp.max()
            yp = yp / peak
            if ys is not None:
                ys = ys / peak
        ax.plot(xp, yp, color=color, label=label, lw=lw)
        if std_band and ys is not None:
            ax.fill_between(xp, yp - ys, yp + ys, alpha=0.18, color=color)

    def _masked_peak(self, x, avg, x_min, x_max) -> float:
        if x is None or len(x) != len(avg):
            x = np.arange(len(avg))
        x = np.asarray(x, dtype=float)
        yp = np.asarray(avg)[(x >= x_min) & (x <= x_max)]
        return float(yp.max()) if yp.size else 0.0

    def _plot_individual(self, ax, x, stack, color, normalize, ref_peak):
        """Overlay the individual spectra of a region as faint thin lines."""
        if stack is None or len(stack) == 0:
            return
        if x is None or len(x) != stack.shape[1]:
            x = np.arange(stack.shape[1])
        x = np.asarray(x, dtype=float)
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp = x[mask]
        scale = ref_peak if (normalize and ref_peak and ref_peak > 0) else 1.0
        # subsample so we never draw thousands of lines
        rows = stack
        if len(stack) > MAX_INDIVIDUAL_LINES:
            step = int(np.ceil(len(stack) / MAX_INDIVIDUAL_LINES))
            rows = stack[::step]
        for row in rows:
            ax.plot(xp, row[mask] / scale, color=color, lw=0.4, alpha=0.15, zorder=0)

    def _method(self) -> str:
        return _METHODS.get(self._cmb_method.currentText(), "mean")

    def _color_order_label(self) -> "str | None":
        """The dispersion-order key the current colour mode maps onto, or None for
        the default selection-order colouring."""
        return {"gdd": "GDD", "tod": "TOD"}.get(self._color_mode)

    def _compute_region_colors(self) -> dict:
        """Map each region id → colour. 'order' keeps each spectrum's own palette
        colour; 'gdd'/'tod' map the dispersion value onto a rainbow scale so spectra
        with similar GDD/TOD share a colour (low value → blue end, high → red end).
        Also stores self._colorbar_info for use by _redraw_spectra()."""
        default = {r["id"]: r["color"] for r in self._regions}
        order_label = self._color_order_label()
        self._colorbar_info = None
        if order_label is None:
            return default
        vis = [r for r in self._regions
               if r.get("analyzed") and r.get("visible", True)
               and (r.get("orders") or {}).get(order_label) is not None]
        if not vis:
            return default
        vals = [float(r["orders"][order_label]) for r in vis]
        vmin, vmax = min(vals), max(vals)
        span = (vmax - vmin) or 1.0
        try:
            cmap = matplotlib.colormaps["rainbow"]
        except Exception:
            cmap = _mpl_cm.get_cmap("rainbow")
        colors = dict(default)
        for r in vis:
            colors[r["id"]] = cmap((float(r["orders"][order_label]) - vmin) / span)
        self._colorbar_info = {"cmap": cmap, "vmin": vmin, "vmax": vmax, "label": order_label}
        return colors

    def _redraw_spectra(self):
        normalize = self._chk_normalize.isChecked()
        std_band  = self._chk_std.isChecked()
        method    = self._method()
        x_min, x_max = self._sb_x_min.value(), self._sb_x_max.value()
        color_for   = self._compute_region_colors()  # also sets self._colorbar_info
        order_label = self._color_order_label()
        ax = self._ax_bot
        self._bot_redrawing = True
        if self._colorbar_bot is not None:
            self._colorbar_bot.remove()
            self._colorbar_bot = None
        ax.clear()
        any_drawn = False

        # analyzed regions (skip hidden ones — visibility "eye" toggle)
        for i, r in enumerate(self._regions):
            if not r.get("analyzed") or not r.get("visible", True):
                continue
            center = r.get(method)
            if center is None:
                continue
            col = color_for.get(r["id"], r["color"])
            label = f"{self._region_label(i)} (n={r.get('n', 0)})"
            if order_label is not None:
                v = (r.get("orders") or {}).get(order_label)
                if v is not None:
                    label = f"{self._region_label(i)}  {order_label}={round(float(v))}"
            if r.get("show_individual") and r.get("stack") is not None:
                ref_peak = self._masked_peak(r.get("x"), center, x_min, x_max)
                self._plot_individual(ax, r.get("x"), r["stack"], col,
                                      normalize, ref_peak)
            self._plot_spectrum(ax, r.get("x"), center, r.get("std"),
                                 col, label, normalize, std_band)
            any_drawn = True

        # live: only the last N shots (newest red, older faint blue, average black)
        if self._live and self._live_buf:
            n_avg  = self._sb_live_n.value()
            buf = list(self._live_buf)[-n_avg:]
            self._plot_live_spectra(ax, buf, normalize)
            st = _compute_stats([a for _, a in buf])
            if st is not None:
                self._plot_spectrum(ax, self._x_data, st[method], st["std"],
                                    "#000000", f"Live {method} (n={st['n']})",
                                    normalize, std_band, lw=2.4)
            any_drawn = True

        if any_drawn:
            ax.set_xlabel("Wavelength [nm]")
            ax.set_ylabel("Intensity (norm.)" if normalize else "Intensity")
            ax.set_title("Live spectra" if self._live else "Averaged spectra")
            ax.set_xlim(x_min, x_max)
            ax.grid(True, alpha=0.25)
            ax.legend(fontsize=9)
            if self._colorbar_info is not None:
                sm = _mpl_cm.ScalarMappable(
                    cmap=self._colorbar_info["cmap"],
                    norm=_mpl_colors.Normalize(
                        vmin=self._colorbar_info["vmin"],
                        vmax=self._colorbar_info["vmax"],
                    ),
                )
                sm.set_array([])
                self._colorbar_bot = self._fig_bot.colorbar(
                    sm, ax=ax, fraction=0.04, pad=0.01, aspect=30
                )
                self._colorbar_bot.set_label(self._colorbar_info["label"], fontsize=9)
                self._colorbar_bot.ax.tick_params(labelsize=8)
        else:
            self._draw_bot_empty()

        self._bot_redrawing = False
        if self._bot_user_xlim is not None:
            ax.set_xlim(self._bot_user_xlim)
        if self._bot_user_ylim is not None:
            ax.set_ylim(self._bot_user_ylim)
        self._canvas_bot.draw_idle()

    def _plot_live_spectra(self, ax, buf, normalize):
        """Older shots faint light-blue; the newest measured spectrum solid red."""
        arrs = [a for _, a in buf]
        if not arrs:
            return
        x = self._x_data
        if x is None or len(x) != len(arrs[-1]):
            x = np.arange(len(arrs[-1]))
        x = np.asarray(x, dtype=float)
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp = x[mask]

        def _y(a):
            yp = a[mask]
            if normalize and yp.size and yp.max() > 0:
                yp = yp / yp.max()
            return yp

        older = arrs[:-1]
        if len(older) > MAX_INDIVIDUAL_LINES:
            step = int(np.ceil(len(older) / MAX_INDIVIDUAL_LINES))
            older = older[::step]
        for a in older:
            if len(a) == len(x):
                # lighter blue, a bit less transparent than before (alpha 0.5 -> 0.6)
                ax.plot(xp, _y(a), color="#6AA0E0", lw=0.6, alpha=0.6, zorder=1)

        newest = arrs[-1]
        if len(newest) == len(x):
            ax.plot(xp, _y(newest), color="#D32F2F", lw=2.7, alpha=1.0,
                    zorder=3, label="Newest live shot")

    # ── Live mode ─────────────────────────────────────────────────────────────
    def _toggle_live(self):
        if self._live:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        self._live = True
        self._live_buf = deque(maxlen=LIVE_BUF_MAX)
        self._btn_live_start.setText("⏹  Stop Live")
        self._btn_live_start.setStyleSheet(_BTN_DANGER)
        self._blink_on = False     # _blink_tick flips it → starts bright
        self._blink_timer.start(600)
        self._blink_tick()

        # Preload a short window of recent history so there is something to average
        # immediately, then keep polling forward. (No "start time" to fiddle with —
        # the only control is "average last N".)
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        self._live_start_ns = now_ns - int(LIVE_HISTORY_S * 1e9)
        self._live_last_ns  = self._live_start_ns

        if self._x_data is None:
            t0 = self._live_start_ns - int(10 * 60 * 1e9)
            t1 = self._live_start_ns + int(60 * 1e9)
            sig_x = _Sig(self)
            sig_x.done.connect(lambda arr: setattr(self, "_x_data", arr) if arr is not None else None)

            spec_x_ch = self._spec_x_pv

            def _fetch_x():
                wfs = _fetch_waveforms(spec_x_ch, t0, t1)
                sig_x.done.emit(wfs[-1][1] if wfs else None)

            _bg(_fetch_x)

        self._set_status(
            f"Live started — preloading last {LIVE_HISTORY_S // 60} min of shots…"
        )
        self._live_tick()

    def _stop_live(self):
        if not self._live:
            return
        self._live = False
        self._live_timer.stop()
        self._blink_timer.stop()
        self._lbl_live_ind.setText("○  idle")
        self._lbl_live_ind.setStyleSheet(
            "font-weight: 700; font-size: 12px; color: #999; padding: 3px;"
        )
        self._btn_live_start.setText("▶  Start Live")
        self._btn_live_start.setStyleSheet(_BTN_SUCCESS)
        self._set_status("Live stopped.")

    def _blink_tick(self):
        """Toggle the 'live is running' indicator so the user sees it is alive."""
        self._blink_on = not self._blink_on
        if self._blink_on:
            self._lbl_live_ind.setText("●  LIVE")
            self._lbl_live_ind.setStyleSheet(
                "font-weight: 700; font-size: 12px; color: #2E7D32; "
                "background: #e3f4e4; border-radius: 3px; padding: 3px;"
            )
        else:
            self._lbl_live_ind.setText("●  LIVE")
            self._lbl_live_ind.setStyleSheet(
                "font-weight: 700; font-size: 12px; color: #bfe0c0; padding: 3px;"
            )

    def _live_tick(self):
        if not self._live:
            return
        now_ns   = int(datetime.now(timezone.utc).timestamp() * 1e9)
        start_ns = self._live_last_ns

        # live mode only needs spectra — the top search graph is hidden
        sig_y = _Sig(self)
        sig_y.done.connect(self._on_live_y)
        sig_y.error.connect(self._on_live_y_error)
        spec_y_ch = self._spec_y_pv

        def _work():
            try:
                wfs = _fetch_waveforms(spec_y_ch, start_ns, now_ns)
                sig_y.done.emit((now_ns, wfs))
            except Exception as ex:
                sig_y.error.emit(str(ex))

        _bg(_work)

    def _on_live_y(self, payload):
        now_ns, wfs = payload
        for t, arr in wfs:
            self._live_buf.append((t, arr))
        self._live_last_ns = now_ns
        self._redraw_spectra()
        n_avg = min(self._sb_live_n.value(), len(self._live_buf))
        self._set_status(
            f"Live: {len(self._live_buf)} spectra buffered, "
            f"averaging last {n_avg}  ({_fmt_hms(now_ns)})"
        )
        if self._live:
            self._live_timer.start(LIVE_INTERVAL_S * 1000)

    def _on_live_y_error(self, err: str):
        self._set_status(f"Live error: {err}")
        if self._live:
            self._live_timer.start((LIVE_INTERVAL_S + 2) * 1000)

    # ── Export ────────────────────────────────────────────────────────────────
    @staticmethod
    def _fmt_full(v) -> str:
        """Plain decimal string with a period — never scientific notation, never
        a comma (a decimal comma would collide with Excel's column separator)."""
        try:
            s = f"{float(v):.6f}"
        except (TypeError, ValueError):
            return ""
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"

    def _live_export_spectra(self) -> list:
        """Live shots currently shown/averaged: the last N from the buffer."""
        if not self._live or not self._live_buf:
            return []
        n = self._sb_live_n.value()
        return list(self._live_buf)[-n:]

    def _export(self):
        analyzed = [(i, r) for i, r in enumerate(self._regions)
                    if r.get("analyzed") and r.get("n", 0) > 0]
        live = self._live_export_spectra()
        if not analyzed and not live:
            QMessageBox.information(
                self, "Export", "Nothing to export — analyze a spectrum or start live first."
            )
            return

        dlg = ExportDialog(len(analyzed), len(live), self._method(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.options()
        if not (opts["data"] or opts["graph"]):
            return

        base, _ = QFileDialog.getSaveFileName(
            self, "Export — choose a base file name", "spectra_export",
            "All files (*)",
        )
        if not base:
            return
        base = os.path.splitext(base)[0]   # we append our own suffixes/extensions

        written: list[str] = []
        try:
            if opts["data"]:
                p = base + ".csv"
                self._export_csv(p, analyzed, live)
                written.append(p)
            if opts["graph"]:
                p = f"{base}_graph.{opts['graph_fmt']}"
                self._fig_bot.savefig(p, dpi=150, bbox_inches="tight")
                written.append(p)
        except Exception as ex:
            QMessageBox.critical(self, "Export error", str(ex))
            return

        self._set_status("Exported: " + ", ".join(os.path.basename(p) for p in written))
        QMessageBox.information(self, "Export done", "Saved:\n" + "\n".join(written))

    def _export_csv(self, path: str, analyzed: list[tuple[int, dict]], live: list):
        """One CSV: a details block, a blank line, then the curve table.

        Columns: each analyzed region's averaged curve, plus every live shot
        (so 7 regions + 100 live shots export as 107 curves).
        """
        method = self._method()
        regs = [(i, r) for i, r in analyzed if r.get(method) is not None]

        x = self._x_data
        if x is None and regs:
            x = regs[0][1].get("x")
        if x is None and regs:
            x = np.arange(len(regs[0][1][method]))
        if x is None and live:
            x = np.arange(len(live[-1][1]))
        if x is None or (not regs and not live):
            raise ValueError("No curve data to export.")
        x = np.asarray(x, dtype=float)
        nx = len(x)
        order_labels = [lbl for lbl, _ in ORDER_PVS]
        # live shots whose length matches the wavelength axis
        live_ok = [(t, np.asarray(a, dtype=float)) for t, a in live if len(a) == nx]

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            f.write("sep=;\r\n")          # tell Excel the delimiter (locale-proof)
            w = csv.writer(f, delimiter=";")

            # ── details block ───────────────────────────────────────────
            w.writerow(["# Spectrum details"])
            w.writerow(["Spectrum", "Date", "Start", "End", "# of spectra",
                        "Method", "SBW4 Output energy [J]"] + order_labels)
            for i, r in regs:
                d0, d1 = _fmt_date(r["t_start"]), _fmt_date(r["t_end"])
                date_str = d0 if d0 == d1 else f"{d0}…{d1}"
                ea = r.get("energy_avg")
                orders = r.get("orders") or {}
                w.writerow([
                    self._region_label(i), date_str,
                    _fmt_hms(r["t_start"]), _fmt_hms(r["t_end"]),
                    r.get("n", 0), method,
                    self._fmt_full(ea) if ea is not None else "",
                ] + [self._fmt_full(orders.get(lbl)) if orders.get(lbl) is not None
                     else "" for lbl in order_labels])
            if live_ok:
                t0, t1 = live_ok[0][0], live_ok[-1][0]
                d0, d1 = _fmt_date(t0), _fmt_date(t1)
                date_str = d0 if d0 == d1 else f"{d0}…{d1}"
                w.writerow([f"Live shots (last {len(live_ok)})", date_str,
                            _fmt_hms(t0), _fmt_hms(t1),
                            len(live_ok), "individual", "", *[""] * len(order_labels)])

            w.writerow([])   # blank separator line

            # ── curve table ─────────────────────────────────────────────
            w.writerow(["# Curve data"])
            header = ["wavelength_nm"]
            for i, _ in regs:
                header.append(f"{self._region_label(i)} ({method})")
                header.append(f"{self._region_label(i)} std")
            for t, _ in live_ok:
                header.append(f"Live {_fmt_hms(t)}")
            w.writerow(header)

            for j in range(nx):
                row = [self._fmt_full(x[j])]
                for _, r in regs:
                    y, s = r.get(method), r.get("std")
                    row.append(self._fmt_full(y[j]) if y is not None and j < len(y) else "")
                    row.append(self._fmt_full(s[j]) if s is not None and j < len(s) else "")
                for _, a in live_ok:
                    row.append(self._fmt_full(a[j]))
                w.writerow(row)

    # ── Misc ──────────────────────────────────────────────────────────────────
    def _set_status(self, msg: str):
        self._lbl_status.setText(msg)

    def cancel_scan(self):
        """Called by the parent Stop All button."""
        self._cancel.set()
        self._stop_live()
        if self._busy:
            self._busy = False
            self._progress.setVisible(False)
            self._btn_pick_day.setEnabled(True)
            self._update_action_buttons()
        self._set_status("Stopped.")


# ── App stylesheet ────────────────────────────────────────────────────────────
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
"""


# ── Standalone entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(_APP_STYLESHEET)

    win = QMainWindow()
    win.setWindowTitle("Spectral analysis")
    win.setMinimumSize(1050, 700)

    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    widget = SpectraWidget()
    tabs.addTab(widget, "SPIDER spectra")
    win.setCentralWidget(tabs)

    from PySide6.QtWidgets import QStatusBar
    status = QStatusBar()
    btn_stop = QPushButton("⏹ Stop")
    btn_stop.setStyleSheet(
        "QPushButton { background:#B71C1C; color:white; font-weight:700; "
        "padding:3px 12px; border-radius:3px; margin:2px; }"
        "QPushButton:hover { background:#7F0000; }"
    )
    btn_stop.clicked.connect(widget.cancel_scan)
    status.addPermanentWidget(btn_stop)
    win.setStatusBar(status)

    win.showMaximized()
    sys.exit(app.exec())
