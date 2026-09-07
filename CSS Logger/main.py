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
    QTextEdit, QProgressBar, QProgressDialog, QLayout,
    QAbstractSpinBox, QAbstractScrollArea, QSlider,
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

# ── Non-UI helpers from cpva_core ──────────────────────────────────────────
from cpva_core import (
    TZ_PRAGUE, now_ns, dt_to_ns, ns_to_local_str, _fmt_cursor_value,
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
    APP_DIR,
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
try:
    from sp_t import _AxisLimitsDialog
except ImportError:      # pragma: no cover
    _AxisLimitsDialog = None


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
    return toolbar


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
    "padding:10px;border-radius:4px;font-size:13px;border:none;}"
    "QPushButton:hover{background:#1B5E20;}"
    "QPushButton:disabled{background:#bbb;color:#888;}")
_LIVE_BTN_ON_STYLE = (
    "QPushButton{background:#F57F17;color:white;font-weight:700;"
    "padding:10px;border-radius:4px;font-size:13px;border:none;}"
    "QPushButton:hover{background:#EF6C00;}"
    "QPushButton:disabled{background:#bbb;color:#888;}")

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
        vmin, vmax = cond.get("min"), cond.get("max")
        if vmin is not None and v < vmin:
            return False
        if vmax is not None and v > vmax:
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


# ── Signal helpers ─────────────────────────────────────────────────────────

class _LoadSig(QObject):
    done     = Signal(object)
    error    = Signal(str)
    progress = Signal(str)
    pct      = Signal(int)    # 0-100 for progress bar
    note     = Signal(str)    # one line for the Log tab, while the load runs
    partial  = Signal(object)  # what has arrived so far, for a growing picture

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
        # follow_page: this side's value is the one selected day, so paging the
        # calendar must move it — see _make_calendar.
        frame, cal = _make_calendar(q_init, follow_page=True)
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
        # No title bar: "focus mode" means the graph and nothing else, the same on
        # both tabs and the same as the Image Slider. Esc / F11 dock it back, and
        # closeEvent below catches Alt+F4, so losing the tab is not a risk.
        super().__init__(None, Qt.WindowType.Window
                               | Qt.WindowType.FramelessWindowHint)
        self._owner = owner
        self.setWindowTitle("CSS Logger — Graph")
        self._drag_start = None

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
        # No title bar to grab, so a press on the window's own background (not on a
        # child widget) drags it. Only needed in the windowed F11 mode; harmless
        # when fullscreen.
        if ev.button() == Qt.MouseButton.LeftButton and not self.isFullScreen():
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(ev)

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
        self._pv_time_condition_rows: list = []
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
        self._live_mode     = False
        self._live_last_ts  = None
        self._live_window_span = None
        # Live pacing: the graph and the table each keep their own last-run stamp
        # and their own measured cost, so the slow one cannot hold the fast one back.
        self._live_last_rebuild_ns = 0   # last table rebuild
        self._live_last_graph_ns   = 0   # last graph refresh
        self._live_table_cost_ns   = 0   # measured cost of one table rebuild
        self._live_graph_cost_ns   = 0   # measured cost of one graph refresh
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

        # The PV Time tab offers the channels that are in the PV list right now,
        # and the list changes while the user is looking at another tab, so its
        # drop-downs are refilled the moment that tab comes to the front.
        self._notebook.currentChanged.connect(self._on_main_tab_changed)

        self._lbl_status = QLabel("Ready.")
        self._lbl_status.setStyleSheet("color:#777;padding:2px 6px;")
        root_lay.addWidget(self._lbl_status)

    def _on_main_tab_changed(self, idx):
        if self._notebook.widget(idx) is self._tab_pv_time:
            self._refresh_pv_time_choices()

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
        self._pv_list.setMaximumHeight(220)
        self._pv_list.itemDoubleClicked.connect(self._on_pv_double_click)
        bar.addWidget(self._pv_list)

        pv_btns = QHBoxLayout()
        self._btn_browse = QPushButton("Browse")
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

        # There is no "Load data" button any more: data is fetched on its own
        # whenever the channels, the time window or the preset change, so the only
        # thing left to decide is whether the view keeps following "now".
        self._btn_live = QPushButton("⏵ Live mode")
        self._btn_live.clicked.connect(self._toggle_live_mode)
        self._btn_live.setToolTip(
            "Keep following new values as they arrive. Off = the chosen time "
            "window stays put. Data is loaded automatically either way.")
        self._btn_live.setStyleSheet(_LIVE_BTN_OFF_STYLE)
        bar.addWidget(self._btn_live)

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
        bar.addWidget(self._btn_stop_load)

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

        # Zoom, pan, view history, margins and Save all live on the graph toolbar
        # built below (the same one the Spectra tab uses), so this row no longer
        # carries its own Back / Save graph — two view histories side by side
        # would disagree with each other.
        b_clean = QPushButton("Clean graph"); b_clean.clicked.connect(self._clean_graph); ctrl.addWidget(b_clean)
        self._btn_clear_sel = QPushButton("Clear selection")
        self._btn_clear_sel.setToolTip(
            "Remove the blue selected region and its statistics")
        self._btn_clear_sel.clicked.connect(self._clear_selection)
        ctrl.addWidget(self._btn_clear_sel)
        self._btn_graph_view = QPushButton("View ▾")
        self._btn_graph_view.setToolTip(
            "Reset the view, set axis limits by hand, hide all grids")
        self._btn_graph_view.clicked.connect(self._open_graph_view_menu)
        ctrl.addWidget(self._btn_graph_view)
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

        # Graph toolbar (zoom, pan, view history, margins, save). The toolbar is
        # tied to one canvas and the canvas is rebuilt on every redraw, so only an
        # empty holder is created here and refilled by _install_graph_toolbar.
        self._graph_tb_holder = QWidget()
        _tb_lay = QHBoxLayout(self._graph_tb_holder)
        _tb_lay.setContentsMargins(0, 0, 0, 0)
        _tb_lay.setSpacing(0)
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

        hdr = QHBoxLayout()
        h = QLabel("Axis settings")
        h.setStyleSheet("font-weight:700;color:#1565C0;")
        self._axis_hdr_label = h
        hdr.addWidget(h)
        hdr.addSpacing(12)
        b_reset_cols = QPushButton("Reset columns")
        b_reset_cols.setToolTip("Put the columns back in their original order, width "
                                "and selection.\nDrag a column heading to move it; "
                                "right-click the headings to show or hide columns.")
        b_reset_cols.clicked.connect(self._reset_axis_columns)
        hdr.addWidget(b_reset_cols)
        b_styles = QToolButton()
        b_styles.setText("Styles")
        b_styles.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b_styles.setToolTip("Save or load the whole look of the graph: colours, line "
                            "and point styles, reference lines and the column layout.")
        b_styles.setMenu(self._build_styles_menu(b_styles))
        hdr.addWidget(b_styles)
        hdr.addStretch()
        lay.addLayout(hdr)

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
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._open_axis_column_menu)
        self._apply_default_axis_columns()

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
        menu.clear()
        menu.addAction("Save current look as", self._save_style_preset)
        load_menu = menu.addMenu("Load")
        del_menu  = menu.addMenu("Delete")
        names = sorted(self._style_presets)
        if names:
            for name in names:
                load_menu.addAction(name, lambda n=name: self._load_style_preset(n))
                del_menu.addAction(name, lambda n=name: self._delete_style_preset(n))
        else:
            load_menu.setEnabled(False)
            del_menu.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Export to file", self._export_style_preset)
        menu.addAction("Import from file", self._import_style_preset)
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
        self._lbl_status.setText(f"Saved look “{name}”")

    def _load_style_preset(self, name):
        data = self._style_presets.get(name)
        if not data:
            return
        self._apply_style_payload(data)
        self._lbl_status.setText(f"Loaded look “{name}”")

    def _delete_style_preset(self, name):
        if QMessageBox.question(self, "Delete look", f"Delete the saved look “{name}”?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._style_presets.pop(name, None)
        self._save_runtime_state()

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
        b_cpv   = QPushButton("Add custom PV"); b_cpv.clicked.connect(self._open_custom_pv_dialog); ctrl.addWidget(b_cpv)
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

        self._btn_pv_time_plot = QPushButton("Plot")
        self._btn_pv_time_plot.setStyleSheet(_BTN_SUCCESS)
        self._btn_pv_time_plot.clicked.connect(self._plot_pv_time)
        ctrl.addWidget(self._btn_pv_time_plot)
        b_add_cond = QPushButton("+ Condition"); b_add_cond.clicked.connect(self._pv_time_add_condition_row)
        ctrl.addWidget(b_add_cond)
        ctrl.addStretch()
        lay.addLayout(ctrl)

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

        self._lbl_pv_time_info = QLabel("Pick the days and a Y channel, then press Plot.")
        self._lbl_pv_time_info.setStyleSheet("color:#555555;")
        lay.addWidget(self._lbl_pv_time_info)

        cond_grp = QGroupBox("Conditions")
        cond_grp.setStyleSheet("QGroupBox{font-weight:700;padding-top:14px;margin-top:8px;"
                                "border:1px solid #ccc;border-radius:4px;}"
                                "QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;}")
        self._pv_time_cond_layout = QVBoxLayout(cond_grp)
        lay.addWidget(cond_grp)
        self._pv_time_add_condition_row()

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

    def _refresh_pv_time_choices(self):
        """Fill the Y and condition drop-downs from the PV LIST on the left —
        the real channels plus every derived (ƒ) one. The tab reads the archiver
        itself, so it is not limited to what the Graph tab happens to have
        loaded; it is limited to what the user has queued."""
        names = self._real_pv_names() + [d.get("name", "") for d in
                                         (getattr(self, "_custom_pvs", None) or [])
                                         if d.get("name")]
        custom_names = {d.get("name", "") for d in (getattr(self, "_custom_pvs", None) or [])}
        choices, self._pv_time_choice_map = [], {}
        for p in names:
            label = p if p in custom_names else shorten_pv_name(p)
            if label in self._pv_time_choice_map:      # same short name twice
                label = p
            choices.append(label)
            self._pv_time_choice_map[label] = p
        combos = [self._pv_time_y_combo] + [r["variable"] for r in self._pv_time_condition_rows]
        for combo in combos:
            keep = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(choices)
            idx = combo.findText(keep)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _pv_time_add_condition_row(self):
        row_w = QWidget()
        row_lay = QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 0, 0, 0)
        # A new row starts switched OFF. The channel list is whatever the user
        # has queued, so a row that filtered on "the first channel, 70 ± 10 %"
        # the moment it appeared would empty the plot for no stated reason.
        chk = QCheckBox(); chk.setChecked(False)
        var_combo = QComboBox(); var_combo.setMinimumWidth(200)
        target_edit = QLineEdit("70"); target_edit.setFixedWidth(60)
        tol_edit    = QLineEdit("10"); tol_edit.setFixedWidth(60)
        b_del = QPushButton("Remove"); b_del.setFixedWidth(70)
        row_lay.addWidget(chk)
        row_lay.addWidget(var_combo)
        row_lay.addWidget(QLabel("target ±")); row_lay.addWidget(target_edit)
        row_lay.addWidget(QLabel("%")); row_lay.addWidget(tol_edit)
        row_lay.addWidget(b_del)
        row_lay.addStretch()
        self._pv_time_cond_layout.addWidget(row_w)
        row = {"widget": row_w, "enabled": chk,
               "variable": var_combo, "target": target_edit, "tolerance": tol_edit}
        self._pv_time_condition_rows.append(row)
        b_del.clicked.connect(lambda: self._pv_time_remove_condition_row(row))
        self._refresh_pv_time_choices()

    def _pv_time_remove_condition_row(self, row):
        if row not in self._pv_time_condition_rows:
            return
        self._pv_time_condition_rows.remove(row)
        row["widget"].setParent(None)
        row["widget"].deleteLater()

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
        self._btn_export = QPushButton("Export table")
        self._btn_export.clicked.connect(self._open_export_dialog)
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
        self._refresh_pv_time_choices()   # the list exists now, so fill its drop-downs

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
        # The canvas the user is clicking on is about to be thrown away, so a
        # half-finished reference-line placement has to end here.
        if self._ref_pick:
            self._cancel_ref_pick()
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
        self._plot_thinned = 0

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
                    # Remember it: over a long period this quietly drops most of
                    # the points, and the status line has to own up to that
                    # rather than let the user read the trace as complete.
                    self._plot_thinned = max(
                        getattr(self, "_plot_thinned", 0) or 0, int(ts_a.size))
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

            # Per-signal look: line style, point style, point size, transparency.
            style_kw = self._pv_style_kwargs(pv_setting, times_num.size, _markers)

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
                dmin = float(np.nanmin(values)); dmax = float(np.nanmax(values))
            else:
                dmin, dmax = 0.0, 1.0
            autosc  = pv_setting.get("auto_scale", False)
            if not autosc:
                if ymin_pv is not None: dmin = ymin_pv
                if ymax_pv is not None: dmax = ymax_pv
            lo, hi = self._band_ylim(i, n, dmin, dmax, autosc)
            ax.set_ylim(lo, hi)

        # Grids, one per ticked channel. Every channel has its own Y scale, so a
        # single shared grid could only ever line up with one of them — which is
        # why ticking the second, third, … Grid box used to do nothing visible.
        # Each ticked channel now gets a horizontal grid on ITS OWN axis, in its
        # own colour and its own line style, so several can be read apart at once.
        # The vertical time lines stay single: all channels share one time axis.
        _grid_pvs = [pv for pv in pvs_for_axes
                     if self._pv_settings.get(pv, {}).get("grid", False)]
        # Remembered so the tick box's tooltip can name the style a channel
        # ACTUALLY got. Working it out again from _pv_order would drift by one
        # whenever a shown channel carries no numeric data (it gets no axis here).
        self._grid_pvs_drawn = list(_grid_pvs)
        for i, (pv, ax) in enumerate(zip(pvs_for_axes, axes)):
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
        # Same size as the Y numbers (one notch below the label font), not the
        # full label size: the time stamps are the widest text on the plot, and
        # the two-line form over several days needs the room. Rotation stays 0 —
        # the second line is what buys the space, not slanted text.
        ax0.tick_params(axis="x", which="major", labelsize=_ticksize, rotation=0)
        ax0.tick_params(axis="x", which="minor", length=3, labelsize=0)

        self._draw_ref_lines(ax0, axes, pvs_for_axes, _fsize)

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

        # Put the remembered selection back: the band on the new selector, the
        # numbers recomputed from the new arrays.
        self._restore_selection_band()
        if self._sel_range:
            self._recompute_stats()

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
        # Left-drag selection over the plot → remember the region, then compute.
        # Only the REGION is remembered (absolute time); the numbers are always
        # derived from it, so they survive a replot and follow new live data.
        if xmax - xmin < 1e-9 or not self._graph_pvs:
            self._clear_selection()
            return
        self._sel_range = (float(xmin), float(xmax))
        self._recompute_stats()

    def _clear_selection(self):
        """Forget the selected region and empty the statistics strip."""
        self._sel_range = None
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
        xmin, xmax = self._sel_range

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
        # Warn when the window no longer covers the whole region, so the numbers
        # are never silently taken for the full selection.
        _note = ""
        if self._graph_axes:
            try:
                _vlo, _vhi = self._graph_axes[0].get_xlim()
                if xmin < _vlo - 1e-9 or xmax > _vhi + 1e-9:
                    _note = "   ⚠ partly outside the shown window"
            except Exception:
                pass
        self._stats_title.setText(
            f"Selection statistics  ·  {t0.strftime('%Y-%m-%d %H:%M:%S')} → "
            f"{t1.strftime('%H:%M:%S')}  ({span_s:,.1f} s){_note}")
        self._stats_title.show()
        self._stats_scroll.show()

    def _on_zoom_select(self, xmin, xmax):
        # Right-drag = zoom in time. The view we are leaving is pushed onto the
        # TOOLBAR's history, so its Back / Home undo a right-drag zoom just like
        # they undo one made with the Zoom button.
        if xmax - xmin < 1e-6: return
        if not self._graph_axes: return
        t0 = mdates.num2date(xmin, tz=TZ_PRAGUE)
        t1 = mdates.num2date(xmax, tz=TZ_PRAGUE)
        self._push_graph_view()
        self._graph_axes[0].set_xlim(t0, t1)
        self._user_zoomed = True
        self._retick_from_current_xlim()
        if self._mpl_canvas:
            self._mpl_canvas.draw_idle()

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
        # Margins changed in the toolbar's dialog: keep them, otherwise the next
        # redraw would put the stored ones straight back.
        if hasattr(tb, "subplot_params_changed"):
            try:
                tb.subplot_params_changed.connect(self._adopt_toolbar_margins)
            except Exception:
                pass
        self._sync_graph_interaction_mode()

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

    def _adopt_toolbar_margins(self):
        """Copy the margins set in the toolbar's dialog into the saved settings.

        Every redraw re-applies the stored margins, so without this the sliders
        would be undone by the next redraw. The LEFT margin is deliberately not
        copied: it is computed from how many channels have their own axis.
        """
        fig = self._mpl_figure
        if fig is None:
            return
        try:
            sp = fig.subplotpars
            self._graph_opts["margin_right"]  = float(1.0 - sp.right)
            self._graph_opts["margin_top"]    = float(sp.top)
            self._graph_opts["margin_bottom"] = float(sp.bottom)
            self.config["graph_opts"] = dict(self._graph_opts)
            self._lbl_status.setText("Plot margins updated.")
        except Exception:
            pass

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
        act_lim = menu.addAction("Axis limits") if _AxisLimitsDialog else None
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
        elif act_lim is not None and chosen is act_lim:
            try:
                _AxisLimitsDialog(self._graph_axes[0], canvas, self).exec()
                self._retick_from_current_xlim()
            except Exception:
                pass
        elif chosen is act_grid_off:
            for pv in list(self._pv_settings):
                self._pv_settings[pv]["grid"] = False
            self._refresh_axis_settings_tv()
            self._schedule_replot()

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
        scroll again, and the stamps must match the restored range."""
        self._user_zoomed = False
        self._retick_from_current_xlim()

    def _on_xy_rect_zoom_push(self):
        tb = self._xy_toolbar
        if tb is None:
            return
        try:
            tb.push_current()
        except Exception:
            pass

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
        self._graph_tb_holder.hide()
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
        self._graph_tb_holder.show()
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
        _want  = max(2, int(_opts.get("x_ticks_max", 8)))
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
        self._load_in_flight = True
        self._load_epoch += 1
        epoch = self._load_epoch
        self._lbl_status.setText("Loading…")
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._btn_stop_load.show()
        start_ns = dt_to_ns(self._dt_from)
        end_ns   = dt_to_ns(self._dt_to)
        # Read the target-points value on the UI thread (Qt widgets are not
        # thread-safe). >0 → ask the archiver to decimate; 0 → every raw sample.
        avg_target = self._avg_target_points()
        timeout_base = self._http_timeout()
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
                                       cpva_fetch_last_before_many)
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

                # ── show the period filling in while it loads ──────────────
                # The requests are served newest first, so the picture grows
                # from the right-hand (present) edge backwards. Snapshots are
                # kept well apart in time: redrawing a dozen signals costs far
                # more than one request does.
                acc = {pv: [] for pv in pvs}
                acc_lock = threading.Lock()
                last_snap = [time.monotonic()]

                def _on_chunk(pv, raw):
                    rows = _decode(raw)
                    if not rows:
                        return
                    with acc_lock:
                        acc.setdefault(pv, []).extend(rows)
                        if time.monotonic() - last_snap[0] < 1.5:
                            return
                        last_snap[0] = time.monotonic()
                        snap = {}
                        for p, v in acc.items():
                            by_ts = {}
                            for row in v:
                                by_ts[row[0]] = row
                            snap[p] = [by_ts[k] for k in sorted(by_ts)]
                    sig.partial.emit((snap, list(pvs), start_ns, end_ns, epoch))

                # ── progress ───────────────────────────────────────────────
                # "total" grows as the archiver refuses a range and it is split,
                # so the percentage can stall; the counts are the honest part.
                # Throttled, or a long load floods Qt with tens of thousands of
                # signal emissions and freezes the window on its own.
                shown = [-1, 0.0]
                t_start = time.monotonic()
                what = f"{len(pvs)} signal{'' if len(pvs) == 1 else 's'}"

                def _report(done, total):
                    if not total:
                        return
                    pct = int(done / total * 100)
                    now = time.monotonic()
                    if pct == shown[0] and now - shown[1] < 0.2:
                        return
                    shown[0], shown[1] = pct, now
                    sig.pct.emit(pct)
                    txt = f"Reading {what} · {done}/{total} requests"
                    if done:
                        left = (now - t_start) / done * (total - done)
                        if left > 5:
                            txt += f" · about {left:.0f} s left"
                    sig.progress.emit(txt)

                sig.progress.emit(f"Reading {what}…")
                raw_by_pv, chunk_errors, report = cpva_fetch_many_adaptive(
                    pvs, start_ns, end_ns,
                    count=(avg_target if avg_target > 0 else None),
                    timeout=timeout_base,
                    progress_fn=_report, cancel_fn=cancel,
                    log_fn=sig.note.emit, chunk_fn=_on_chunk)

                for pv in pvs:
                    if pv in chunk_errors:
                        errors.append(f"{shorten_pv_name(pv)}: {chunk_errors[pv]}")
                        print(f"[CSS Logger]   → ERROR {pv}: {chunk_errors[pv]}")
                    samples_by_pv[pv] = _decode(raw_by_pv.get(pv, []))
                    pv_order.append(pv)
                    print(f"[CSS Logger]   {pv} → {len(samples_by_pv[pv])} samples")

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
                last_map = cpva_fetch_last_before_many(
                    need_lookback, start_ns, timeout_base, cancel_fn=cancel)
                for pv, last_s in last_map.items():
                    last_ts = last_s.get("time")
                    if last_ts:
                        pre_window_vals[pv] = (
                            int(last_ts), cpva_decode_value(last_s),
                            (last_s.get("metaData") or {}).get("units", "") or "")
                sig.pct.emit(100)
                print(f"[CSS Logger] Emitting done: {len(pv_order)} PVs, errors={errors}")
                sig.done.emit((samples_by_pv, pv_order, errors, start_ns, end_ns,
                               pre_window_vals,
                               {"report": report, "epoch": epoch}))
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
            snap, pvs, start_ns, end_ns, epoch = payload
        except Exception:
            return
        if epoch != self._load_epoch or not self._load_in_flight:
            return                      # a stale worker, or the load has ended
        if self._partial_busy:
            return                      # the previous repaint has not finished
        self._partial_busy = True
        try:
            self._pv_stats_cache.clear()
            self._plot_window_ns = None
            for pv in snap:
                snap[pv].sort(key=lambda s: s[0])
            self._samples_by_pv = {
                pv: [(ts, v, u) for ts, v, u in snap.get(pv, [])
                     if start_ns <= ts <= end_ns]
                for pv in pvs}
            self._pv_order      = list(pvs)
            self._base_pv_order = list(pvs)
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
            self._plot_graph()
            self._refresh_axis_settings_tv()
            self._populate_table()
        except Exception as exc:
            import traceback; traceback.print_exc()
            self._log(f"[partial redraw skipped] {exc}")
        finally:
            self._partial_busy = False

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

        self._plot_graph()
        self._refresh_axis_settings_tv()   # always show PV list, even with 0 samples
        self._populate_table()
        self._refresh_xy_choices()

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
            bits.append(f"graph shows 25,000 of {thinned:,} pts")
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
        avg_target = self._avg_target_points()   # read on UI thread
        timeout_base = self._http_timeout()
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
        self._progress_bar.setValue(0)
        self._progress_bar.show()

        def _worker():
            try:
                from cpva_core import (cpva_fetch_many_adaptive, cpva_decode_value,
                                       cpva_fetch_last_before_many)
                samples_by_pv = {}
                pv_order = []
                pre_window_vals = {}

                shown = [-1, 0.0]

                def _report(done, total):
                    if not total:
                        return
                    pct = int(done / total * 100)
                    now = time.monotonic()
                    if pct == shown[0] and now - shown[1] < 0.2:
                        return
                    shown[0], shown[1] = pct, now
                    sig.pct.emit(pct)
                    sig.progress.emit(f"Live start · {done}/{total} requests")

                sig.progress.emit(
                    f"Live start: reading {len(pvs)} "
                    f"signal{'' if len(pvs) == 1 else 's'}…")
                raw_by_pv, _errs, _report_obj = cpva_fetch_many_adaptive(
                    pvs, start_ns, end_ns,
                    count=(avg_target if avg_target > 0 else None),
                    timeout=timeout_base,
                    progress_fn=_report, cancel_fn=cancel,
                    log_fn=sig.note.emit)

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
                sig.done.emit((samples_by_pv, pv_order, [], start_ns, end_ns, pre_window_vals))
            except Exception as exc:
                sig.error.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

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
            # Each one still keeps at least ~2× its own measured cost free, so a
            # slow machine backs off instead of leaving the window no idle time.
            _graph_interval_ns = max(_graph_floor_ns,
                                     2 * getattr(self, "_live_graph_cost_ns", 0))
            _table_interval_ns = max(_table_floor_ns,
                                     2 * getattr(self, "_live_table_cost_ns", 0))
            graph_due = (now - getattr(self, "_live_last_graph_ns", 0)) >= _graph_interval_ns
            table_due = (now - getattr(self, "_live_last_rebuild_ns", 0)) >= _table_interval_ns
            refresh_due = table_due            # the table rebuild, as before
            if graph_due:
                self._live_last_graph_ns = now
                # Advance the carry-forward / plot window so a full replot (and the
                # held "last value" line) tracks "now" instead of the initial load.
                if self._live_window_span:
                    span_ns = int(self._live_window_span.total_seconds() * 1e9)
                    self._plot_window_ns = (now - span_ns, now)
            if table_due:
                self._live_last_rebuild_ns = now

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

                if table_due:
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
                    self._populate_table()
                    self._flush_axis_measured()   # Last/Min/Max/… keep up with Live
                    self._live_table_cost_ns = int(
                        (time.perf_counter() - _table_t0) * 1e9)
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

            # Graph on its own, faster clock. If the graph has no lines yet or a
            # channel just gained its first numeric data, the existing lines can't
            # represent it — do a full redraw; otherwise just move the existing
            # ones (the cheap path).
            if graph_due:
                _graph_t0 = time.perf_counter()
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
                # Cost of the graph refresh alone; paces the next one. The canvas
                # repaint itself happens later via draw_idle, so add a rough
                # allowance for it rather than under-counting the work.
                self._live_graph_cost_ns = int(
                    (time.perf_counter() - _graph_t0) * 1.6e9)
            else:
                # Between graph refreshes, still slide the time axis so the window
                # glides instead of jumping once per refresh. This touches no data:
                # new range, fresh time stamps, repaint.
                self._scroll_live_time_axis()
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

    def _live_poll_ms(self) -> int:
        """How often the archive is asked for new values (Graph settings)."""
        try:
            return max(50, int(self._graph_opts.get("live_poll_ms", 300)))
        except Exception:
            return 300

    def _schedule_live_tick(self):
        if not self._live_mode: return
        interval_ms = self._live_poll_ms()
        self._live_countdown_elapsed_ms = 0
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

    def _cpv_plan(self):
        """What every custom PV is computed from: ``(name, code, [(letter, pv)])``
        in definition order, plus the set of names that cannot be computed at
        all. Problems are collected into ``_cpv_diag`` instead of swallowed."""
        defs = [d for d in self._custom_pvs if d.get("name") and d.get("expr")]
        self._cpv_diag = []
        if not defs:
            return [], set()
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
        return plan, broken

    def _compute_custom_pvs_in_rows(self, rows):
        """Evaluate every custom PV for each row.

        Each formula resolves its variables through its own ``bindings``
        (letter -> PV name), so its values follow the PVs even after the list is
        reordered or another preset is loaded. A letter with no binding falls
        back to the old positional lookup. Customs are computed in definition
        order, so a later custom can read an earlier one — its binding simply
        names that custom channel."""
        plan, broken = self._cpv_plan()
        if not plan:
            return
        SAFE = _CPV_SAFE_ENV
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
        return (name or "").strip() or shorten_pv_name(pv)

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
        self._table_display_offset = 0
        if not self._table_rows:
            self._table_widget.setRowCount(0)
            self._table_widget.setColumnCount(1)
            self._table_widget.setHorizontalHeaderLabels(["Timestamp"])
            self._table_cols_cache = ["Timestamp"]
            self._lbl_table_info.setText("No rows.")
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
        # Channels that have their own grid, in the order the plot gave them their
        # styles. Taken from the last redraw when there is one, so the tooltip
        # never names a style the channel did not actually get.
        _grid_on = getattr(self, "_grid_pvs_drawn", None)
        if not _grid_on:
            _grid_on = [pv for pv in self._pv_order
                        if self._pv_settings.get(pv, {}).get("show", True)
                        and self._pv_settings.get(pv, {}).get("grid", False)]
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
                    # Columns can be dragged around, and a span then paints from
                    # wherever its first column ended up. Give every other cell of
                    # the divider the same blue so the row reads as one solid band
                    # no matter what order the user chose.
                    for col_j in range(1, ncol):
                        pad = QTableWidgetItem("")
                        pad.setBackground(QColor("#E3F2FD"))
                        pad.setFlags(Qt.ItemFlag.ItemIsEnabled)
                        self._axis_tv.setItem(row_i, col_j, pad)
                    self._axis_tv.setSpan(row_i, 0, 1, ncol)
                    continue
                pv  = payload
                idx = self._pv_order.index(pv)
                s = self._pv_settings.get(pv, self._get_pv_default_settings(pv, idx))
                stats = self._pv_stats(pv)
                for col_j, col_name in enumerate(_COL):
                    val = s.get(col_name, "")
                    if col_name == "blank":
                        item = QTableWidgetItem("")
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # non-editable spacer
                        self._axis_tv.setItem(row_i, col_j, item)
                    elif col_name in ("show", "auto_scale", "grid"):
                        chk = QTableWidgetItem()
                        chk.setCheckState(Qt.CheckState.Checked if val else Qt.CheckState.Unchecked)
                        if col_name == "grid":
                            # Name the line style this channel's grid gets, so the
                            # lines on the plot can be matched to their channel.
                            chk.setToolTip(
                                f"Grid for this signal — {self._grid_style_name(pv, _grid_on)} lines "
                                f"in the signal's own colour"
                                if val else
                                "Tick to give this signal its own grid (each one "
                                "gets a different kind of line)")
                        self._axis_tv.setItem(row_i, col_j, chk)
                    elif col_name == "color":
                        item = QTableWidgetItem("")
                        color_hex = s.get("color", _GRAPH_COLORS[idx % len(_GRAPH_COLORS)])
                        item.setData(Qt.ItemDataRole.UserRole, color_hex)
                        self._axis_tv.setItem(row_i, col_j, item)
                    else:
                        if col_name in stats:            # measured, not typed
                            txt = stats[col_name]
                        else:
                            txt = "" if val is None else str(val)
                        item = QTableWidgetItem(txt)
                        if col_name in _AXIS_READONLY_COLS:
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
        # The graph comes first: it always keeps at least half of the height the
        # two panes share (and never less than the absolute floor), so the PV
        # list can only take the smaller half, however many PVs are in it.
        keep  = max(self._AXIS_PANE_MIN_GRAPH, int(total * 0.5))
        room  = total - spl.handleWidth() - keep
        floor = chrome + tv.horizontalHeader().height() + 26   # header + one row
        want  = max(floor, min(need + chrome, max(floor, room)))

        pane.setMaximumHeight(want)          # can be dragged smaller, never bigger
        spl.setSizes([max(1, total - spl.handleWidth() - want), want])

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
        # Un-ticking Show takes the channel off the graph AND out of the table,
        # so the two never disagree about what is on screen.
        if self._table_pvs() != cols_before:
            self._populate_table()
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
        key = (pv, len(samples))
        hit = self._pv_stats_cache.get(key)
        if hit is not None:
            return hit
        unit = ""
        for _ts, _v, u in reversed(samples):
            if u:
                unit = str(u)
                break
        vals = np.array([v for _ts, v, _u in samples
                         if isinstance(v, (int, float))], dtype=float)
        if vals.size:
            out = {"unit": unit,
                   "last":  _fmt_cursor_value(float(vals[-1])),
                   "min":   _fmt_cursor_value(float(np.nanmin(vals))),
                   "max":   _fmt_cursor_value(float(np.nanmax(vals))),
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

        xs, ys, n_rows = self._xy_pairs(x_pv, y_pv)

        if not xs:
            self._lbl_xy_info.setText(
                "No data to plot — neither channel has values in this window.")
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
        _size = 24 if _n < 300 else (12 if _n < 2000 else (6 if _n < 20000 else 3))
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
        self._install_xy_toolbar(canvas)
        canvas.draw()

        self._xy_rect_selector = RectangleSelector(
            ax, self._on_xy_rect_select, useblit=False, button=3,
            props=dict(alpha=0.2, facecolor="#FF6600"))

        self._lbl_xy_info.setText(
            f"{_n:,} points plotted (from {n_rows:,} rows).")

    def _xy_pairs(self, x_pv, y_pv):
        """Pair two channels up into (x, y) points.

        A point needs a value for BOTH channels at the same moment. Insisting on
        both landing in the very same row is far too strict: two channels are
        rarely archived at the same instant, so that gave a nearly empty plot. Each
        channel's LAST KNOWN value is carried forward instead — the same thing the
        time graph does with its held lines — up to a staleness limit, so a channel
        that stopped reporting cannot go on inventing points forever.
        """
        rows = self._table_rows
        if not rows:
            return [], [], 0
        # Staleness limit: a value may stand in for at most this long. Derived from
        # the window, so it scales with what is on screen, and clamped to a sane range.
        span_s = max(1.0, (self._dt_to - self._dt_from).total_seconds())
        max_hold_ns = int(min(max(span_s * 0.02, 60.0), 1800.0) * 1e9)

        xs, ys = [], []
        last_x = last_y = None          # (ts_ns, value)
        for ts, row_dict in rows:
            for pv, slot in ((x_pv, "x"), (y_pv, "y")):
                if pv not in row_dict:
                    continue
                v, _ = row_dict[pv]
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if slot == "x":
                    last_x = (ts, fv)
                else:
                    last_y = (ts, fv)
            if last_x is None or last_y is None:
                continue
            if (ts - last_x[0]) > max_hold_ns or (ts - last_y[0]) > max_hold_ns:
                continue                # one of them has been silent too long
            xs.append(last_x[1]); ys.append(last_y[1])
        return xs, ys, len(rows)

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

    def _on_xy_rect_select(self, eclick, erelease):
        if not self._xy_figure or not self._xy_figure.axes: return
        ax = self._xy_figure.axes[0]
        x0, x1 = sorted([eclick.xdata, erelease.xdata])
        y0, y1 = sorted([eclick.ydata, erelease.ydata])
        if x1 - x0 < 1e-12 or y1 - y0 < 1e-12: return
        # The view being left goes onto the toolbar's history, so its Back and Home
        # undo a right-drag zoom exactly like one made with the Zoom button.
        self._on_xy_rect_zoom_push()
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
        """The condition rows as numbers. A row whose target or tolerance is not
        a number is reported in the Log and skipped, rather than quietly
        filtering nothing."""
        out = []
        for row in self._pv_time_condition_rows:
            label = row["variable"].currentText()
            ch    = self._pv_time_choice_map.get(label)
            if not ch:
                continue
            try:
                target = float(row["target"].text().replace(",", "."))
                tol    = float(row["tolerance"].text().replace(",", "."))
            except ValueError:
                self._log(f"PV Time Plot: condition on {label} skipped — "
                          f"'{row['target'].text()} ± {row['tolerance'].text()} %' "
                          f"is not a number.")
                continue
            lo, hi = sorted((target * (1 - tol / 100.0), target * (1 + tol / 100.0)))
            out.append({"label": label, "channel": ch,
                        "enabled": row["enabled"].isChecked(),
                        "target": target, "tol": tol, "lo": lo, "hi": hi})
        return out

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
        order = self._real_pv_names() + list(defs)
        positional = {self._col_letter(i): name for i, name in enumerate(order)}
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
                src = bindings.get(letter) or positional.get(letter)
                if not src:
                    problems.append(f"'{name}': {letter} is not a known channel.")
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

        self._pv_time_sig = _LoadSig()          # keep a reference until done
        sig = self._pv_time_sig
        sig.progress.connect(self._lbl_pv_time_info.setText)
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
                    res, errors, report = cpva_fetch_many_adaptive(
                        pvs, day_ns, dt_to_ns(d0 + timedelta(days=1)),
                        count=None, max_workers=16, cancel_fn=cancel, log_fn=None)
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
            idx = np.searchsorted(part["ts"], grid, side="right") - 1
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
        if y is None or not len(grid):
            return np.empty(0), np.empty(0, dtype=np.int64), 0
        keep  = np.isfinite(y)
        total = int(keep.sum())
        for c in req["conds"]:
            if not c["enabled"]:
                continue
            v = cols.get(c["channel"])
            if v is None:
                continue
            keep &= np.isfinite(v) & (v >= c["lo"]) & (v <= c["hi"])
        return y[keep], grid[keep], total

    # -- drawing ------------------------------------------------------------

    def _pv_time_draw(self, req):
        y_label, conds = req["y_label"], req["conds"]
        per_day, kept, total = [], 0, 0
        for day in self._pv_time_days:
            vals, ts, n_all = self._pv_time_day_values(day, req)
            total += n_all
            kept  += len(vals)
            per_day.append((day, vals, ts))

        cond_txt = " · ".join(
            f"{c['label']} {c['target']:g} ±{c['tol']:g} %"
            for c in conds if c["enabled"]) or "none"

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
            labels.append(day.strftime("%Y-%m-%d"))
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

        ax.set_xticks(range(1, len(labels) + 1))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
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
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        ax.grid(True, alpha=0.3)
        ax.tick_params(colors="#222")
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(45)
            lbl.set_horizontalalignment("right")

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
        # A preset's saved period is a fixed stretch of the past, so it leaves
        # live mode for the same reason the calendar does. A preset that carries
        # no period only changes the channel list and does not touch live.
        if (dt_from or dt_to) and self._live_mode:
            self._stop_live("Live mode off — the preset brought a fixed period.")
        if p.get("master_pv") and self._master_pv_edit is not None:
            self._master_pv_edit.setText(p["master_pv"])
        if "master_multiple" in p and self._master_multiple_edit is not None:
            self._master_multiple_edit.setText(str(p["master_multiple"]))
        self._refresh_time_labels()
        self._log(f"Preset '{p['name']}' loaded.")
        # The new channel set and window are fetched on their own; the preset also
        # brings a different time range, so a selection from the old one may no
        # longer mean anything.
        self._user_zoomed = False
        self._drop_selection_if_outside()
        self._request_reload(delay_ms=0, reason="preset loaded")

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
            if added:
                # A channel added mid-session has no history yet: the live tick only
                # fetches from the last-seen timestamp forward, so it would stay
                # blank. A full reload gets it over the whole window (all channels
                # load together in one pool, so this is quick).
                self._request_reload(reason="channel added")

    def _remove_selected_pvs(self):
        removed = False
        for item in reversed(self._pv_list.selectedItems()):
            self._pv_list.takeItem(self._pv_list.row(item))
            removed = True
        # Nothing is selected after a Remove. Qt keeps the current row where it
        # was, so the channel that moved up into the freed place came out
        # highlighted and the next Remove would have taken it too.
        self._pv_list.setCurrentRow(-1)
        self._pv_list.clearSelection()
        self._update_pv_count()
        if removed:
            # Several rows can go in one click, so the reload is debounced.
            self._request_reload(reason="channel removed")

    def _clear_pv_list(self):
        ans = QMessageBox.question(self, "Clear PVs", "Remove all PVs from list?")
        if ans == QMessageBox.StandardButton.Yes:
            self._pv_list.clear()
            self._sync_pv_list_customs()   # keep custom PVs on show after a clear
            self._update_pv_count()
            self._request_reload(reason="channel list cleared")

    def _on_pv_double_click(self, item):
        if item.data(Qt.ItemDataRole.UserRole):
            return                         # custom/divider row — not editable here
        pv, ok = QInputDialog.getText(self, "Edit PV", "PV name:", text=item.text())
        if ok and pv.strip() and pv.strip() != item.text():
            item.setText(pv.strip())
            self._request_reload(reason="channel renamed")

    def _real_pv_names(self):
        """The real archiver PVs queued for fetching — the only ones sent to the
        archiver, saved into presets, or counted. Custom (derived) PVs are shown
        in the list too but tagged (UserRole set), so they are skipped here."""
        return [self._pv_list.item(i).text()
                for i in range(self._pv_list.count())
                if not self._pv_list.item(i).data(Qt.ItemDataRole.UserRole)]

    def _sync_pv_list_customs(self):
        """Mirror the defined custom PVs into the PV LIST (above the Live button) as
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
            self._drop_selection_if_outside()
            # Live follows the clock, so it only makes sense for a period that
            # ends at "now". Choosing a period that ends at a fixed moment means
            # "show me that stretch of the past", and Live used to quietly
            # reinterpret it as a WIDTH — asking for yesterday 08:00-12:00 gave
            # the last four hours instead. The Live button is still the manual
            # override in both directions.
            if self._live_mode and not dlg.ends_at_now():
                self._stop_live("Live mode off — a fixed period was chosen.")
            self._user_zoomed = False
            self._request_reload(delay_ms=0, reason="time window changed")

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
            name = self._pv_settings.get(pv, {}).get("display_name", shorten_pv_name(pv))
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
        name = self._pv_settings.get(pv, {}).get("display_name", shorten_pv_name(pv))
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

        def _on_done(payload):
            n_rows, n_slices, cancelled = payload
            prog.close()
            what = "cancelled after" if cancelled else "wrote"
            self._log(f"Export {what} {n_rows} shots from {n_slices} "
                      f"slice{'' if n_slices == 1 else 's'} → {path}")
            self._lbl_status.setText(
                f"Export {'cancelled — partial file kept' if cancelled else 'finished'}: "
                f"{n_rows} shots.")
            if not cancelled:
                QMessageBox.information(
                    self, "Export finished",
                    f"{n_rows} shots written to\n{path}")

        def _on_error(msg):
            prog.close()
            self._log(f"Export failed: {msg}")
            QMessageBox.critical(self, "Export error", msg)

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
    """Horizontal reference lines: name, which signal they belong to, height,
    colour, style and thickness — plus reordering, deleting, and placing one by
    clicking in the graph."""

    _NO_PV = "— no signal (first Y scale) —"

    def __init__(self, ref_lines, pv_choices=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reference Lines")
        self.resize(900, 360)
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
                            ("Style", 100), ("Width", 60), ("", 26)):
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

        b_del = QPushButton("✕"); b_del.setFixedWidth(26)
        b_del.setStyleSheet("QPushButton{background:#B71C1C;color:white;border-radius:3px;}")

        for w in (b_up, b_dn, lbl_e, pv_cb, y_e, b_pick, col_btn, style_cb, w_e, b_del):
            rl.addWidget(w)
        rl.addStretch()

        rec = {"widget": row_w, "label": lbl_e, "pv": pv_cb, "y": y_e,
               "color": col_hex, "btn": col_btn, "style": style_cb, "width": w_e}
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
            ref_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            ref_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            # Without this the first cell wears a stray focus box.
            ref_tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ref_tbl.setMaximumHeight(186)
            ref_tbl.setStyleSheet(self._tbl_css)
            ref_tbl.verticalHeader().setDefaultSectionSize(24)
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


def _icon_app_id(prefix, ico_path):
    """Taskbar identity for `prefix`, tagged with the icon *and* this build.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it: an id that was once seen without a usable icon keeps drawing the
    generic placeholder for good, whatever icon the window later carries --
    that is exactly how this program ended up on a blank button and why the id
    once had to be hand-bumped to ".2".

    Hashing the icon's own bytes into the id was the next fix, but a
    content-only id can be poisoned just as well, and then it never recovers
    because it only changes when the picture is redrawn. Measured again on
    2026-09-03: this program, Calibrations, Git Work and Image Tools all drew
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
# icon, so Qt's setWindowIcon is already sufficient. The real bug was that
# _HERE is not frozen-aware, so the frozen build never found icon.ico at all.

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
        # Stop live streaming and any analysis still fetching, so the background
        # threads are not left running while the window goes away.
        try:
            self._spectra.cancel_scan()
        except Exception:
            pass
        try:
            # A running table export writes to a file — let it notice the window
            # is gone instead of finishing into a dead dialog.
            self._css_logger._export_epoch = getattr(
                self._css_logger, "_export_epoch", 0) + 1
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
    sys.exit(app.exec())


if __name__ == "__main__":
    _run()