"""
sp_t.py — SPIDER spectrometer spectral analysis

Archive workflow:
  1. Click "Load day" -> pick a date -> SBW4 energy is loaded into the top graph.
  2. Drag on the top graph to select a time region; repeat for more regions.
  3. Click "Analyze" -> choose options -> averaged spectra appear in the bottom graph.

Live workflow:
  1. Switch to Live -> set the start time -> click "Start Live".
  2. The top graph fills with energy, the bottom graph shows the average of the
     last N spectra. You can also drag regions on the live graph and analyze them.

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

from PySide6.QtCore import Qt, QObject, QTimer, Signal, QDate, QTime
from PySide6.QtGui import QTextCharFormat, QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QScrollArea, QSizePolicy, QButtonGroup,
    QFileDialog, QDialog, QDialogButtonBox, QTimeEdit, QFrame, QSplitter,
    QCalendarWidget, QMessageBox, QMainWindow, QTabWidget, QComboBox,
    QProgressBar, QStyledItemDelegate, QAbstractItemView,
)

import matplotlib
matplotlib.use("QtAgg")
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
    ("Order 2", "L3-SPFE-AOD03-002:Order2_RB"),
    ("Order 3", "L3-SPFE-AOD03-002:Order3_RB"),
    ("Order 4", "L3-SPFE-AOD03-002:Order4_RB"),
]

LIVE_INTERVAL_S = 3            # poll period in live mode
LIVE_BUF_MAX    = 2000         # max spectra kept in the rolling buffer
DEFAULT_LIVE_N  = 100          # default "average last N" value
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
    "QToolBar { background: white; border-bottom: 1px solid #ddd; } "
    "QToolButton { background: transparent; color: black; } "
    "QToolButton:hover { background: #e8f0fe; }"
)


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
class _WeekendDelegate(QStyledItemDelegate):
    """Colour Saturday/Sunday date cells red.

    A delegate is used (not setWeekdayTextFormat) because a stylesheet that sets
    a uniform item colour overrides the per-weekday text format; the delegate
    paints the colour per cell, so it always wins. Covers adjacent-month days too.
    """
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        date = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(date, QDate) and date.isValid() and date.dayOfWeek() in (6, 7):
            option.palette.setColor(QPalette.ColorRole.Text, QColor("#cc0000"))
            option.palette.setColor(QPalette.ColorRole.ButtonText, QColor("#cc0000"))


class DatePickerDialog(QDialog):
    def __init__(self, parent=None, initial: QDate | None = None):
        super().__init__(parent)
        self.setWindowTitle("Select day")
        self.setWindowIcon(QIcon())          # no per-dialog icon (looks like a separate app)
        self.setModal(True)
        self.setFixedSize(330, 300)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self._cal = QCalendarWidget()
        self._cal.setGridVisible(True)
        self._cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self._cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        if initial:
            self._cal.setSelectedDate(initial)

        # weekday text: working days dark, weekends red (every Sat/Sun)
        wf = QTextCharFormat()
        wf.setForeground(QColor("#111"))
        for day in (Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                    Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday):
            self._cal.setWeekdayTextFormat(day, wf)
        wf_we = QTextCharFormat()
        wf_we.setForeground(QColor("#cc0000"))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self._cal.setWeekdayTextFormat(day, wf_we)

        self._cal.setStyleSheet("""
            QCalendarWidget QAbstractItemView:enabled {
                background: #ffffff; color: #111;
                selection-background-color: #1565C0; selection-color: white;
            }
            /* day-name header row: light grey (not dark red) */
            QCalendarWidget QHeaderView { background-color: #ececec; }
            QCalendarWidget QHeaderView::section {
                background-color: #ececec; color: #333; font-weight: 700;
                padding: 3px 0; border: none; border-bottom: 1px solid #c8c8c8;
            }
            QCalendarWidget QWidget#qt_calendar_navigationbar { background: #1565C0; }
            QCalendarWidget QToolButton {
                color: white; background: transparent;
                font-weight: 700; font-size: 13px;
                border-radius: 3px; padding: 3px 6px;
            }
            QCalendarWidget QToolButton:hover { background: #0D47A1; }
            QCalendarWidget QSpinBox {
                color: white; background: #1565C0; border: none; font-weight: 700;
            }
            QCalendarWidget QMenu { color: #111; background: #fff; }
        """)
        # weekend colouring via delegate (survives the stylesheet's item colour)
        view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view is not None:
            self._wk_delegate = _WeekendDelegate(self._cal)
            view.setItemDelegate(self._wk_delegate)
        lay.addWidget(self._cal)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_date(self) -> QDate:
        return self._cal.selectedDate()


# ── AnalysisDialog ────────────────────────────────────────────────────────────
class AnalysisDialog(QDialog):
    def __init__(self, regions: list[dict], normalize: bool, std_band: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analyze spectra")
        self.setModal(True)
        self.setMinimumWidth(370)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        hdr = QLabel(f"Selected regions ({len(regions)}):")
        hdr.setStyleSheet("font-weight: 700; font-size: 12px;")
        lay.addWidget(hdr)

        for i, r in enumerate(regions):
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {r['color']}; font-size: 16px;")
            dot.setFixedWidth(22)
            row.addWidget(dot)
            label = r.get("label", f"Region {i+1}")
            row.addWidget(QLabel(
                f"{label}:  {_fmt_date(r['t_start'])} "
                f"{_fmt_hms(r['t_start'])} – {_fmt_hms(r['t_end'])}"
            ))
            row.addStretch()
            lay.addLayout(row)

        note = QLabel(
            "Each region is averaged into one mean spectrum\n"
            "(over all shots in that time window)."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "background:#e8f0fe; border:1px solid #90CAF9; border-radius:4px; "
            "padding:6px; font-size:11px; color:#0D47A1;"
        )
        lay.addWidget(note)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        lay.addWidget(sep)

        lbl_opts = QLabel("Display options:")
        lbl_opts.setStyleSheet("font-weight: 700;")
        lay.addWidget(lbl_opts)

        self._chk_normalize = QCheckBox("Normalize to peak (max = 1)")
        self._chk_std       = QCheckBox("Show std. band (±1σ)")
        self._chk_normalize.setChecked(normalize)
        self._chk_std.setChecked(std_band)
        self._chk_normalize.setStyleSheet(_CHK_STYLE)
        self._chk_std.setStyleSheet(_CHK_STYLE)
        lay.addWidget(self._chk_normalize)
        lay.addWidget(self._chk_std)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Analyze  ▶")
        ok_btn.setStyleSheet(
            "QPushButton { background:#2E7D32; color:white; font-weight:700; "
            "padding:6px 16px; border-radius:4px; }"
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def options(self) -> dict:
        return {
            "normalize": self._chk_normalize.isChecked(),
            "std_band":  self._chk_std.isChecked(),
        }


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
            parts.append(f"{n_regions} region(s)")
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


# ── SpectraWidget ─────────────────────────────────────────────────────────────
class SpectraWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._selected_day:     QDate | None             = None
        self._day_start_ns:     int                      = 0
        self._day_end_ns:       int                      = 0
        self._energy_data:      list[tuple[int, float]]  = []
        self._x_data:           np.ndarray | None        = None
        # Single source of truth for regions. Each region dict carries its own
        # selection (t_start/t_end/color/visible/expanded) and, once analyzed,
        # its results (mean/median/.../stack, energy_avg, orders, n).
        self._regions:          list[dict]               = []
        self._region_seq:       int                      = 0   # monotonic id source
        self._row_widgets:      dict[int, dict]          = {}  # id -> row widget refs
        self._span:             SpanSelector | None      = None
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

        self._build_ui()
        self._connect_signals()

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
        lay.addWidget(self._btn_pick_day)

        # ── Mode ───────────────────────────────────────────────────────
        g_mode = QGroupBox("Mode")
        g_mode.setStyleSheet(_GROUP_STYLE)
        row_m = QHBoxLayout(g_mode)
        self._btn_archive   = QPushButton("Archive")
        self._btn_live_mode = QPushButton("Live")
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

        # ── Live controls ──────────────────────────────────────────────
        self._g_live = QGroupBox("Live")
        self._g_live.setStyleSheet(_GROUP_STYLE)
        live_l = QVBoxLayout(self._g_live)
        live_l.addWidget(QLabel("Start time:"))
        self._time_start = QTimeEdit(QTime.currentTime().addSecs(-600))
        self._time_start.setDisplayFormat("HH:mm:ss")
        live_l.addWidget(self._time_start)
        row_n = QHBoxLayout()
        row_n.addWidget(QLabel("Average last N:"))
        self._sb_live_n = QSpinBox()
        self._sb_live_n.setRange(1, LIVE_BUF_MAX)
        self._sb_live_n.setValue(DEFAULT_LIVE_N)
        row_n.addWidget(self._sb_live_n)
        live_l.addLayout(row_n)
        self._btn_live_start = QPushButton("▶  Start Live")
        self._btn_live_start.setStyleSheet(_BTN_SUCCESS)
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
        self._chk_normalize = QCheckBox("Normalize to peak")
        self._chk_std       = QCheckBox("Std. band (±1σ)")
        self._chk_show_energy = QCheckBox("Show SBW4 energy graph")
        self._chk_show_energy.setChecked(True)
        self._chk_show_energy.setToolTip(
            "Uncheck to minimize the energy graph — the spectra graph then fills "
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
        lay.addWidget(self._btn_export)

        return sb

    def _make_canvas_panel(self, suffix: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        fig = Figure(tight_layout=True)
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
        tb = NavigationToolbar2QT(canvas, w)
        tb.setPalette(pal)
        tb.setStyleSheet(_TB_STYLE)
        v.addWidget(tb)
        v.addWidget(canvas, stretch=1)
        setattr(self, f"_fig_{suffix}",    fig)
        setattr(self, f"_ax_{suffix}",     ax)
        setattr(self, f"_canvas_{suffix}", canvas)
        setattr(self, f"_tb_{suffix}",     tb)
        return w

    def _make_region_panel(self) -> QWidget:
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)

        lbl = QLabel("Selected regions")
        lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        v.addWidget(lbl)

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
        self._btn_analyze    = QPushButton("✓  Analyze")
        self._btn_analyze.setEnabled(False)
        self._btn_analyze.setStyleSheet(_BTN_SUCCESS)
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
        self._btn_analyze.clicked.connect(self._open_analysis_dialog)
        self._btn_clear_regs.clicked.connect(self._clear_regions)
        self._btn_export.clicked.connect(self._export)
        self._chk_normalize.stateChanged.connect(self._redraw_spectra)
        self._chk_std.stateChanged.connect(self._redraw_spectra)
        self._chk_show_energy.toggled.connect(self._update_top_visibility)
        self._cmb_method.currentIndexChanged.connect(self._redraw_spectra)
        self._sb_x_min.valueChanged.connect(self._redraw_spectra)
        self._sb_x_max.valueChanged.connect(self._redraw_spectra)
        self._sb_live_n.valueChanged.connect(self._redraw_spectra)

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
        # In live mode the energy graph is irrelevant; in archive it follows the
        # "Show SBW4 energy graph" checkbox.
        self._update_top_visibility()
        self._redraw_spectra()

    # ── Day picker ────────────────────────────────────────────────────────────
    def _pick_day(self):
        dlg = DatePickerDialog(self, initial=self._selected_day or QDate.currentDate())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # switch UI back to archive mode
        self._btn_archive.setChecked(True)
        self._set_live_mode(False)
        self._selected_day = dlg.selected_date()
        d = self._selected_day
        self._lbl_day.setText(f"Day: {d.year()}-{d.month():02d}-{d.day():02d}")
        # Keep already-selected regions across day changes — they carry absolute
        # timestamps and stay in the list (delete them via the ✕ in the list).
        self._load_day_energy()

    def _load_day_energy(self):
        start_ns, end_ns = _day_range_ns(self._selected_day)
        self._day_start_ns, self._day_end_ns = start_ns, end_ns
        self._set_status("Loading SBW4 energy…")
        self._btn_pick_day.setEnabled(False)

        sig = _Sig(self)
        sig.done.connect(self._on_energy_loaded)
        sig.error.connect(self._on_energy_error)

        def _work():
            try:
                sig.done.emit(_fetch_scalars(PV_ENERGY, start_ns, end_ns))
            except Exception as e:
                sig.error.emit(str(e))

        _bg(_work)

    def _on_energy_loaded(self, data: list):
        self._btn_pick_day.setEnabled(True)
        # Drop the "last value before start" sample EPICS returns, so the axis
        # is clamped to the selected day instead of stretching to the previous day.
        data = [(t, v) for (t, v) in data
                if self._day_start_ns <= t <= self._day_end_ns]
        self._energy_data = data
        if not data:
            self._set_status("No energy data for this day.")
            self._draw_top_empty("No energy data for this day")
            return
        self._set_status(f"Loaded {len(data)} energy samples.")
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
        ax.set_facecolor("#f8f8f8")
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

    def _draw_energy(self):
        ax = self._ax_top
        ax.clear()
        if not self._energy_data:
            return
        # sort by time so the connecting line follows chronological order
        data = sorted(self._energy_data, key=lambda tv: tv[0])
        times = [mdates.date2num(_ns_to_dt(t)) for t, _ in data]
        vals  = [v for _, v in data]
        ax.plot(times, vals, "-", lw=1.0, color="#1565C0", alpha=0.85,
                marker=".", ms=3, mfc="#0D47A1", mec="#0D47A1")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=_PRAGUE))
        # clamp the view to the selected day (issue: previous day used to show)
        ax.set_xlim(mdates.date2num(_ns_to_dt(self._day_start_ns)),
                    mdates.date2num(_ns_to_dt(self._day_end_ns)))
        self._fig_top.autofmt_xdate(rotation=30, ha="right")
        ax.set_xlabel("Time")
        ax.set_ylabel("Energy [J]")
        ax.set_title("SBW4 energy — drag to select time region(s), then click Analyze")
        ax.grid(True, alpha=0.25)
        self._paint_region_spans(ax)
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
            f"Region {len(self._regions)} added: "
            f"{_fmt_hms(t_start)} – {_fmt_hms(t_end)}.  "
            f"Add more or click Analyze."
        )

    # ── Regions UI ────────────────────────────────────────────────────────────
    def _region_label(self, i: int) -> str:
        return f"Region {i + 1}"

    def _find_region(self, rid: int) -> dict | None:
        return next((r for r in self._regions if r["id"] == rid), None)

    def _rebuild_regions_ui(self):
        self._row_widgets.clear()
        while self._regions_lay.count():
            item = self._regions_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._regions:
            empty = QLabel("Drag on the energy graph\nto select a region.")
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
        btn_del.setToolTip("Remove this region")
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
        add(f"<b>Duration:</b> {_fmt_dur(r['t_start'], r['t_end'])}")

        if not r.get("analyzed"):
            note = QLabel("Not analyzed yet — click Analyze.")
            note.setStyleSheet("color: #999; font-style: italic; border: none;")
            lay.addWidget(note)
            return w

        add(f"<b>Spectra:</b> {r.get('n', 0)}")
        ea = r.get("energy_avg")
        if ea is not None:
            add(f"<b>Output energy:</b> {ea:.3f} J  "
                f"<span style='color:#888'>(avg of {r.get('energy_n', 0)})</span>")
        else:
            add("<b>Output energy:</b> n/a")

        orders = r.get("orders") or {}
        for label, _ in ORDER_PVS:
            val = orders.get(label)
            add(f"<b>{label}:</b> {self._fmt_full(val)}"
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

    def _refresh_energy_view(self):
        """Redraw the archive energy graph (live mode has no energy graph)."""
        if self._live:
            return
        if self._energy_data:
            self._draw_energy()
            self._install_span()

    # ── Analysis ──────────────────────────────────────────────────────────────
    def _open_analysis_dialog(self):
        todo = [(i, r) for i, r in enumerate(self._regions) if not r["analyzed"]]
        if not todo:
            return
        dlg_regs = [{**r, "label": self._region_label(i)} for i, r in todo]
        dlg = AnalysisDialog(
            dlg_regs,
            self._chk_normalize.isChecked(),
            self._chk_std.isChecked(),
            self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.options()
        self._chk_normalize.setChecked(opts["normalize"])
        self._chk_std.setChecked(opts["std_band"])
        self._run_analysis()

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

        x_cached = self._x_data

        def _work():
            x_data = x_cached
            if x_data is None:
                sig.progress.emit("Loading spectrometer X axis…")
                r0 = snap[0]
                wf = _fetch_waveforms(
                    PV_SPEC_X,
                    r0["t_start"] - int(10 * 60 * 1e9),
                    r0["t_end"]   + int(10 * 60 * 1e9),
                )
                if wf:
                    x_data = wf[-1][1]

            results = []
            for i, r in enumerate(snap):
                sig.progress.emit(
                    f"Region {i+1}/{len(snap)}: "
                    f"{_fmt_hms(r['t_start'])}–{_fmt_hms(r['t_end'])}…"
                )
                wfs = _fetch_waveforms(PV_SPEC_Y, r["t_start"], r["t_end"])
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
    def _draw_bot_empty(self, msg: str = "Analyze a region in the top graph"):
        ax = self._ax_bot
        ax.clear()
        ax.set_facecolor("#f8f8f8")
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

    def _redraw_spectra(self):
        normalize = self._chk_normalize.isChecked()
        std_band  = self._chk_std.isChecked()
        method    = self._method()
        x_min, x_max = self._sb_x_min.value(), self._sb_x_max.value()
        ax = self._ax_bot
        ax.clear()
        any_drawn = False

        # analyzed regions (skip hidden ones — visibility "eye" toggle)
        for i, r in enumerate(self._regions):
            if not r.get("analyzed") or not r.get("visible", True):
                continue
            center = r.get(method)
            if center is None:
                continue
            if r.get("show_individual") and r.get("stack") is not None:
                ref_peak = self._masked_peak(r.get("x"), center, x_min, x_max)
                self._plot_individual(ax, r.get("x"), r["stack"], r["color"],
                                      normalize, ref_peak)
            self._plot_spectrum(ax, r.get("x"), center, r.get("std"),
                                 r["color"], f"{self._region_label(i)} (n={r.get('n', 0)})",
                                 normalize, std_band)
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
        else:
            self._draw_bot_empty()

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

        qt = self._time_start.time()
        tz = _PRAGUE or timezone.utc
        now_dt = datetime.now(tz)
        start_dt = datetime(now_dt.year, now_dt.month, now_dt.day,
                            qt.hour(), qt.minute(), qt.second(), tzinfo=tz)
        self._live_start_ns = int(start_dt.timestamp() * 1e9)
        self._live_last_ns  = self._live_start_ns

        if self._x_data is None:
            t0 = self._live_start_ns - int(10 * 60 * 1e9)
            t1 = self._live_start_ns + int(60 * 1e9)
            sig_x = _Sig(self)
            sig_x.done.connect(lambda arr: setattr(self, "_x_data", arr) if arr is not None else None)

            def _fetch_x():
                wfs = _fetch_waveforms(PV_SPEC_X, t0, t1)
                sig_x.done.emit(wfs[-1][1] if wfs else None)

            _bg(_fetch_x)

        self._set_status(
            f"Live started from {qt.toString('HH:mm:ss')} — loading history…"
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

        # live mode only needs spectra — the SBW4 energy graph is hidden
        sig_y = _Sig(self)
        sig_y.done.connect(self._on_live_y)
        sig_y.error.connect(self._on_live_y_error)

        def _work():
            try:
                wfs = _fetch_waveforms(PV_SPEC_Y, start_ns, now_ns)
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
                self, "Export", "Nothing to export — analyze a region or start live first."
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
            w.writerow(["# Region details"])
            w.writerow(["Region", "Date", "Start", "End", "Duration", "Spectra",
                        "Method", "Output energy [J]"] + order_labels)
            for i, r in regs:
                d0, d1 = _fmt_date(r["t_start"]), _fmt_date(r["t_end"])
                date_str = d0 if d0 == d1 else f"{d0}…{d1}"
                ea = r.get("energy_avg")
                orders = r.get("orders") or {}
                w.writerow([
                    self._region_label(i), date_str,
                    _fmt_hms(r["t_start"]), _fmt_hms(r["t_end"]),
                    _fmt_dur(r["t_start"], r["t_end"]), r.get("n", 0), method,
                    self._fmt_full(ea) if ea is not None else "",
                ] + [self._fmt_full(orders.get(lbl)) if orders.get(lbl) is not None
                     else "" for lbl in order_labels])
            if live_ok:
                t0, t1 = live_ok[0][0], live_ok[-1][0]
                d0, d1 = _fmt_date(t0), _fmt_date(t1)
                date_str = d0 if d0 == d1 else f"{d0}…{d1}"
                w.writerow([f"Live shots (last {len(live_ok)})", date_str,
                            _fmt_hms(t0), _fmt_hms(t1), _fmt_dur(t0, t1),
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
        self._stop_live()


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
