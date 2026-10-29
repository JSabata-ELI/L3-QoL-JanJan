"""
main.py — Pulser Monitor

Scans camera images over a date range and detects "dead pulsers":
rectangular regions that are less bright than expected.

Run standalone: python main.py
"""

import csv
import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image as PilImage

try:
    from zoneinfo import ZoneInfo
    TZ_PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    TZ_PRAGUE = None

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import (BoundaryNorm, LinearSegmentedColormap, ListedColormap,
                               Normalize, to_rgba)
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

from PySide6.QtCore import (
    Qt, QDate, QObject, Signal, QEvent, QPoint, QRect,
    QRunnable, QThreadPool,
)
from PySide6.QtGui import (
    QColor, QTextCharFormat, QPixmap, QImage, QPainter, QPen, QBrush,
    QFont, QIcon, QCursor, QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QApplication, QButtonGroup,
    QCalendarWidget, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QSplitter, QStatusBar, QStyledItemDelegate,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

# ── CONSTANTS ──────────────────────────────────────────────────────────────────

PRIMARY     = "#1565C0"
PRIMARY_HOV = "#0D47A1"
SUCCESS     = "#2E7D32"
SUCCESS_HOV = "#1B5E20"
DANGER      = "#B71C1C"
DANGER_HOV  = "#7F0000"
NODATA_CLR  = "#9E9E9E"   # gray — frame had no real array data

IMAGES_ROOT_OPTIONS = {
    "Lab":    Path(r"//users-L3.tier0.lcs.local/cpva-image-2026"),
    "Office": Path(r"\\users-L3.tier0.lcs.local\cpva-image-2026"),
}

# Per-user config: persists ROI selection per camera across app restarts
CONFIG_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "PulserMonitor" / "rois.json"
# Config schema version. Older files stored reference levels that have to be re-measured:
# v1 used the wrong (MaxValue) intensity scale — see `_to_unit_scale`; v2 measured contrast
# against a thin ring — see `_tile_floor_contrast`. Loading either re-measures the levels
# from the bundled reference images. ROI boxes are never touched.
CONFIG_VERSION = 3

# Fixed cameras — display name → CPVA folder name
CAMERAS = {
    "PD1M1": "C03-015-PD1M1DF-_-IMG",
    "PD2M1": "C03-019-PD2M1DF-_-IMG",
    "PD3M1": "C03-023-PD3M1DF-_-IMG",
    "PD4M1": "C03-027-PD4M1DF-_-IMG",
}

# Minimum file size (bytes) for a valid image on each camera
# Smallest file that could be a real frame. This is ONLY a "did the file get written at
# all" check — content is judged from the pixels, never from the file size.
#
# It used to be a per-camera threshold set just under the size of a normal running frame
# (350 kB on PD1M1, 1900 kB on PD2M1, …), which quietly discarded 37.9 % of every day on
# every camera. A PNG of a dim frame compresses much better than a bright one, so those
# thresholds threw away precisely the frames worth analysing: the whole warm-up stretch
# (186 kB vs 398 kB on PD1M1 — 38 minutes on 13 Aug) and the blank frames written during
# an array trip. Trips then survived only as gaps in the timeline, and warm-up could not
# be measured at all. Measured over a full day, genuine frames start at 186 kB while the
# only broken ones are 13 files of zero length, so this sits far below anything real.
MIN_IMAGE_BYTES = 64 * 1024

# Column labels left→right per camera (PD2M1 columns are mirrored)
CAM_COLS = {
    "PD1M1": ["A", "B", "C", "D", "E"],
    "PD2M1": ["E", "D", "C", "B", "A"],
    "PD3M1": ["A", "B", "C", "D", "E"],
    "PD4M1": ["A", "B", "C", "D", "E"],
}
_ROW_LABELS = ["1", "2", "3", "4", "5", "6", "7", "8"]

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
_NS_19_RE  = re.compile(r"\d{19}")

# The archiver stretches each camera's full scale onto the 16-bit range, so 65535 is the
# camera-independent absolute scale for every frame. See `_to_unit_scale`.
_FULL_SCALE_16 = 65535.0

_ROI_COLORS = [
    "#FF9800", "#2196F3", "#4CAF50", "#E91E63",
    "#9C27B0", "#00BCD4", "#FF5722", "#FFEB3B",
]


def _app_dir() -> Path:
    """Directory holding the app's bundled data files.

    Under PyInstaller the data files are unpacked to `sys._MEIPASS`, not next to the exe
    — same fallback the other tools in this repo use for their icons."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent


def _bundled_ref(cam_key: str, kind: str) -> "Path | None":
    """Locate the hand-picked reference frame shipped alongside the app.

    Named `<camera prefix>*-<kind>.png`, e.g. `C03-015-PD1M1DF-16022026-allgood.png`
    (all pulsers lit) or `...-warmup.png` (whole array in warm-up mode). Matching is by
    the camera's `C03-nnn-PDxMyDF` prefix and the suffix, so the date in the middle can
    be anything and a newer reference can simply be dropped in beside the old one — the
    most recently modified match wins."""
    folder_name = CAMERAS.get(cam_key)
    if not folder_name:
        return None
    prefix = folder_name.split("-_-")[0]      # "C03-015-PD1M1DF"
    hits = []
    for d in (_app_dir(), Path(__file__).resolve().parent):
        try:
            hits.extend(p for p in d.glob(f"{prefix}*{kind}.png") if p.is_file())
        except Exception:
            pass
    if not hits:
        return None
    return max(set(hits), key=lambda p: p.stat().st_mtime)

# ── Per-pulser grid-detection tunables ─────────────────────────────────────────
# The pulser array is detected cell-by-cell: a regular comb locates each cell, then
# the actual bright tile is cut out in 2-D inside that cell's window, so a camera
# where every diode differs in size/position/brightness still aligns tightly.
_WINDOW_FRAC     = 0.6   # half-window for the outermost cells, in units of pitch
_CELL_BLUR_FRAC  = 0.9   # box-blur baseline size (× pitch) for per-cell illumination flatten
_CELL_THR_LO_PCT = 25    # dark (separator) level percentile inside a cell window
_CELL_THR_HI_PCT = 92    # bright (tile) level percentile inside a cell window
_CELL_THR_FRAC   = 0.45  # tile threshold placed this far from dark toward bright
_CELL_TRIM_PCT   = 4     # trim this %% of lit pixels off each side of the bbox
                         # (discards the thin diagonal reflection and stray specks)
_CELL_MIN_BRIGHT = 0.05  # min lit-pixel fraction for a cell to count as a live pulser
_CELL_ABS_FLOOR_FRAC = 0.16  # raw-intensity floor (× global lit range) a pixel must
                             # clear to count as tile — kills the black image border
                             # that the illumination flatten would otherwise pass as lit
_ROI_INSET_FRAC  = 0.02  # small margin pulled inward from the detected tile edge

_BTN = (
    f"QPushButton {{background:{PRIMARY};color:#fff;border:none;"
    f"border-radius:4px;padding:5px 10px;}}"
    f"QPushButton:hover{{background:{PRIMARY_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_BTN_SM = (
    f"QPushButton {{background:{PRIMARY};color:#fff;border:none;"
    f"border-radius:4px;padding:3px 8px;font-size:11px;}}"
    f"QPushButton:hover{{background:{PRIMARY_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_BTN_DANGER_STYLE = (
    f"QPushButton {{background:{DANGER};color:#fff;border:none;"
    f"border-radius:4px;padding:5px 10px;}}"
    f"QPushButton:hover{{background:{DANGER_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_BTN_SUCCESS_STYLE = (
    f"QPushButton {{background:{SUCCESS};color:#fff;border:none;"
    f"border-radius:4px;padding:5px 10px;}}"
    f"QPushButton:hover{{background:{SUCCESS_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_CHECKBOX_STYLE = (
    "QCheckBox::indicator{width:14px;height:14px;border:2px solid #aaa;"
    "border-radius:2px;background:#fff;}"
    "QCheckBox::indicator:checked{border:2px solid #2d7dff;background:#2d7dff;}"
)
# Every table: light cells, dark ink, a visibly darker header band, the selection spelled
# out — nothing left to the style or the theme. The ink is set on the table, not on
# `::item`: a colour there would override the grey of a deliberate stop and the red of a
# death, which rows set for themselves.
_TABLE_CSS = (
    "QTableWidget{background:#fff;gridline-color:#e0e0e0;color:#111;}"
    "QTableWidget::item{background:#fff;padding:2px 4px;}"
    "QTableWidget::item:alternate{background:#e8f0fe;}"
    "QTableWidget::item:selected{background:#1565C0;color:#fff;}"
    "QHeaderView::section{background:#e2e2e2;color:#111;font-weight:600;"
    "border:none;border-right:1px solid #c8c8c8;border-bottom:1px solid #b8b8b8;"
    "padding:3px 4px;}"
    "QTableCornerButton::section{background:#e2e2e2;border:none;}"
)
_CAL_STYLE = """
QCalendarWidget QWidget { background: #f6f6f6; color: #111; }
QCalendarWidget QAbstractItemView {
    background: #fcfcfc; color: #111;
    selection-background-color: #2d7dff; selection-color: #fff;
    alternate-background-color: #f2f2f2; gridline-color: #d8d8d8; }
QCalendarWidget QTableView {
    background: #fcfcfc;
    selection-background-color: #2d7dff; selection-color: #fff;
    gridline-color: #d8d8d8; outline: 0; }
QCalendarWidget QToolButton {
    background: #efefef; border: 1px solid #c8c8c8;
    padding: 3px 6px; border-radius: 4px; color: #111; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #efefef; }
QCalendarWidget QAbstractItemView:enabled { color: #111; }
"""

# ── DATA CLASSES ──────────────────────────────────────────────────────────────

@dataclass
class RoiDefinition:
    name: str
    x: int
    y: int
    w: int
    h: int
    ref_brightness: float = 0.0   # ROI median in the all-alive reference image
    ref_contrast: float = 0.0     # tile − local dark floor in the reference image
    # Same two measurements taken on the WARM-UP reference image, used to learn how far
    # below normal the whole array sits while warming. 0.0 = no warm-up reference yet.
    warm_brightness: float = 0.0
    warm_contrast: float = 0.0
    color: str = "#FF9800"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RoiDefinition":
        return cls(
            name=str(d.get("name", "ROI")),
            x=int(d.get("x", 0)), y=int(d.get("y", 0)),
            w=int(d.get("w", 10)), h=int(d.get("h", 10)),
            ref_brightness=float(d.get("ref_brightness", 0.0)),
            ref_contrast=float(d.get("ref_contrast", 0.0)),
            warm_brightness=float(d.get("warm_brightness", 0.0)),
            warm_contrast=float(d.get("warm_contrast", 0.0)),
            color=str(d.get("color", "#FF9800")),
        )


@dataclass
class SamplePoint:
    """One measured frame. Everything is on the 0..1 full-scale (see `_to_unit_scale`).

    Deliberately carries no per-ROI alive flag: whether a pulser was lit is decided later
    by `analyze_camera`, against that pulser's own learnt level. An earlier build stored a
    fixed-threshold `roi_alive` here and exported it to CSV, which is why the old
    test01072026 CSV disagrees with what the Map tab shows."""
    ts_ns: int
    path: "Path | None"
    roi_means: list
    frame_mean: float = 1.0     # mean brightness of the whole frame (for no-data detection)
    roi_contrasts: list = field(default_factory=list)  # tile − local floor per ROI (glow-free signal)


# ── ACTIVE-TIME GATE ──────────────────────────────────────────────────────────
# Two rules decide which parts of the scanned window are worth judging at all:
#
#   1. Time of day — only 07:00–21:00 Prague time. Outside those hours nobody is
#      running the laser, so a dark pulser array is the expected state, not a fault.
#   2. L3-SIS-KEY:HighPowerEnable — the operator key. While it reads 0 the diodes are
#      not meant to be firing, and the array is dark for exactly the same reason.
#
# Both are EXCLUSIONS, never detections. A period failing either rule is dropped from
# the analysis instead of being reported as an array trip or as forty simultaneous
# dropouts — which is what used to happen, because the archiver keeps writing frames of
# a dark array all night and the pattern-correlation test throws them away, leaving a
# gap that the trip-from-gap logic reads as an outage.
#
# Excluded time is accounted for explicitly rather than quietly deleted: it is
# subtracted from spans, uptime denominators and downtime, it never opens or extends an
# outage event, and it is shaded on every graph. A gated result therefore cannot be
# mistaken for a window in which nothing happened.

GATE_HOUR_START = 7      # first hour of the working day, Prague local time
GATE_HOUR_END   = 21     # first hour NO LONGER counted (exclusive)

# The operator's high-power key. Measured facts about this channel (17 Aug 2026):
#   - it is an ENUM, and the archiver returns its value as a one-element list, [0]/[1];
#   - only TRANSITIONS are archived, so a window's opening state has to be found by
#     looking back before it — the longest observed gap between two samples is 11.6
#     days, which is what `_HPE_LOOKBACK_DAYS` is sized against;
#   - the "<channel>.value" alias that some channels need does NOT exist here — asking
#     for it returns HTTP 400, so it is never tried.
HPE_CHANNEL = "L3-SIS-KEY:HighPowerEnable"

EXCLUDED_CLR = "#5C6BC0"   # indigo — outside the analysed hours / high power off

_NS_PER_S = 1_000_000_000
_NS_PER_DAY = 86_400 * _NS_PER_S


# ── span algebra ──────────────────────────────────────────────────────────────
# Half-open [start, end) integer-ns intervals throughout.

def _merge_spans(spans) -> list:
    """Sorted, non-overlapping, non-touching spans. Empty/reversed spans are dropped."""
    out: list = []
    for a, b in sorted((int(a), int(b)) for a, b in spans if int(b) > int(a)):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _clip_spans(spans, lo: int, hi: int) -> list:
    out: list = []
    for a, b in spans:
        a2, b2 = max(int(a), lo), min(int(b), hi)
        if b2 > a2:
            out.append((a2, b2))
    return out


def _invert_spans(spans, lo: int, hi: int) -> list:
    """The complement of `spans` inside [lo, hi)."""
    out: list = []
    cur = lo
    for a, b in _merge_spans(_clip_spans(spans, lo, hi)):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < hi:
        out.append((cur, hi))
    return out


def _intersect_spans(a_spans, b_spans) -> list:
    a_list, b_list = _merge_spans(a_spans), _merge_spans(b_spans)
    out: list = []
    i = j = 0
    while i < len(a_list) and j < len(b_list):
        lo = max(a_list[i][0], b_list[j][0])
        hi = min(a_list[i][1], b_list[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a_list[i][1] <= b_list[j][1]:
            i += 1
        else:
            j += 1
    return out


class _SpanSet:
    """Merged spans with O(log n) membership and overlap queries.

    Membership is asked once per scanned frame and overlap once per outage event, so
    both go through a prefix sum of covered time rather than a scan over the spans."""

    def __init__(self, spans=()):
        self.spans = _merge_spans(spans)
        self._starts = [a for a, _ in self.spans]
        self._cum = [0]
        for a, b in self.spans:
            self._cum.append(self._cum[-1] + (b - a))

    def __bool__(self) -> bool:
        return bool(self.spans)

    def __len__(self) -> int:
        return len(self.spans)

    @property
    def total_ns(self) -> int:
        return self._cum[-1]

    def contains(self, ts: int) -> bool:
        k = bisect_right(self._starts, ts) - 1
        return k >= 0 and ts < self.spans[k][1]

    def _covered_before(self, x: int) -> int:
        """Total covered time strictly below `x`."""
        k = bisect_right(self._starts, x) - 1
        if k < 0:
            return 0
        a, b = self.spans[k]
        return self._cum[k] + max(0, min(b, x) - a)

    def overlap_ns(self, lo: int, hi: int) -> int:
        return max(0, self._covered_before(hi) - self._covered_before(lo))

    def clip_back(self, ts: int) -> int:
        """`ts` pulled back to the start of the span it falls in, else unchanged.

        Used to keep a reported array-trip end from running past the moment the
        observation window closed — a trip that began at 20:00 lasted until 21:00, not
        until the next morning."""
        k = bisect_right(self._starts, ts) - 1
        if k >= 0 and ts < self.spans[k][1]:
            return self.spans[k][0]
        return ts


def daytime_spans(start_ns: int, end_ns: int,
                  hour_start: int = GATE_HOUR_START,
                  hour_end: int = GATE_HOUR_END) -> list:
    """[hour_start:00, hour_end:00) Prague local time, for every day the window touches.

    Built day by day from tz-aware local datetimes rather than by arithmetic on UTC, so
    the boundaries stay at the right wall-clock hour across a DST change."""
    tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
    if hour_end <= hour_start:
        return []
    day = datetime.fromtimestamp(start_ns / 1e9, tz=tz).date()
    last = datetime.fromtimestamp(end_ns / 1e9, tz=tz).date()
    out: list = []
    while day <= last:
        a = datetime(day.year, day.month, day.day, hour_start, tzinfo=tz)
        if hour_end >= 24:
            b = datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(days=1)
        else:
            b = datetime(day.year, day.month, day.day, hour_end, tzinfo=tz)
        out.append((int(round(a.timestamp())) * _NS_PER_S,
                    int(round(b.timestamp())) * _NS_PER_S))
        day += timedelta(days=1)
    return _clip_spans(out, start_ns, end_ns)


# ── archiver query for the high-power key ──────────────────────────────────────
# Deliberately a small self-contained query rather than Image Tools' `cpva_client`:
# that module belongs to a different program in this repo, and its connection pool,
# retry policy and per-day sample cache exist for a GUI that fans out across eight
# channels continuously. This needs ONE query per scan, so importing across program
# folders (and having to keep it in the frozen bundle) would cost more than it saves.
_CPVA_HOST = "10.78.0.57:8443"
_CPVA_BASE = "/api/1.0/cpva"
_HPE_TIMEOUT_S = 25.0
# Staged look-back for the state at the window start. Most scans are of recent days and
# find a transition within 2; the longer stages exist for a window that opens during a
# shutdown (longest measured gap between transitions: 11.6 days).
_HPE_LOOKBACK_DAYS = (2, 14, 45)


def _cpva_samples(channel: str, start_ns: int, end_ns: int,
                  timeout: float = _HPE_TIMEOUT_S) -> list:
    """Sorted (ts_ns, value) samples for one channel. Raises on any transport/HTTP error.

    Samples whose severity says `hasValue: False` are skipped — a disconnect marker
    carries no reading, and treating its value as 0 would invent a high-power-off
    period out of an archiver restart."""
    qs = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(int(start_ns)),
        "end": str(int(end_ns)),
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(_CPVA_HOST, timeout=timeout, context=ctx)
    try:
        conn.request("GET", f"{_CPVA_BASE}/samples?{qs}",
                     headers={"Accept": "application/json"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"archiver returned HTTP {resp.status}")
        data = json.loads(body.decode("utf-8"))
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not isinstance(data, list):
        return []
    out: list = []
    for s in data:
        if not isinstance(s, dict):
            continue
        sev = s.get("severity")
        if isinstance(sev, dict) and sev.get("hasValue") is False:
            continue
        val = s.get("value")
        if isinstance(val, list):
            val = val[0] if val else None
        try:
            out.append((int(s.get("time")), float(val)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda tv: tv[0])
    return out


def high_power_spans(start_ns: int, end_ns: int,
                     channel: str = HPE_CHANNEL) -> "tuple[list, str]":
    """(spans inside [start_ns, end_ns) where the key reads 1, one-line note).

    Raises RuntimeError if the archiver cannot be reached or knows nothing about the
    channel — the caller must then say so rather than gate on a guess."""
    samples: list = []
    reached = _HPE_LOOKBACK_DAYS[-1]
    for days in _HPE_LOOKBACK_DAYS:
        reached = days
        samples = _cpva_samples(channel, start_ns - days * _NS_PER_DAY, end_ns)
        if any(t <= start_ns for t, _ in samples):
            break
    if not samples:
        raise RuntimeError(
            f"no samples at all in the {reached} days up to the end of the window")

    prior = [v for t, v in samples if t <= start_ns]
    opening_unknown = not prior
    # Nothing archived before the window even after the longest look-back: there is no
    # record of the key having been on, so the stretch before the first transition is
    # treated as off. Said out loud in the note — it is an inference, not a reading.
    state_on = bool(prior and int(prior[-1]) == 1)

    spans: list = []
    cur = start_ns if state_on else None
    n_trans = 0
    for t, v in samples:
        if t <= start_ns or t >= end_ns:
            continue
        n_trans += 1
        on = int(v) == 1
        if on and cur is None:
            cur = t
        elif not on and cur is not None:
            spans.append((cur, t))
            cur = None
    if cur is not None:
        spans.append((cur, end_ns))

    spans = _merge_spans(spans)
    on_ns = sum(b - a for a, b in spans)
    note = (f"{channel} = 1 for {_fmt_dur(on_ns)} in {len(spans)} period(s), "
            f"{n_trans} transition(s) inside the window")
    if opening_unknown:
        note += (f"; nothing archived in the {reached} days before the window, so it "
                 f"is taken as 0 until its first sample")
    return spans, note


def active_spans(start_ns: int, end_ns: int, *,
                 gate_hours: bool = True, gate_high_power: bool = True,
                 hour_start: int = GATE_HOUR_START,
                 hour_end: int = GATE_HOUR_END) -> tuple:
    """(active _SpanSet, excluded _SpanSet, notes, hpe_failed) for one scan window.

    `hpe_failed` is True when high-power gating was asked for but the archiver could not
    answer. The hour gate still applies in that case and the reason is in `notes` — the
    alternative, refusing to scan, would leave the operator with nothing at all because
    the archiver is on a network the office PC cannot always reach."""
    notes: list = []
    active: list = [(int(start_ns), int(end_ns))]
    if gate_hours:
        active = _intersect_spans(
            active, daytime_spans(start_ns, end_ns, hour_start, hour_end))
        notes.append(f"only {hour_start:02d}:00–{hour_end:02d}:00 Prague time")
    hpe_failed = False
    if gate_high_power:
        try:
            hp, note = high_power_spans(start_ns, end_ns)
            active = _intersect_spans(active, hp)
            notes.append(note)
        except Exception as exc:
            hpe_failed = True
            notes.append(f"{HPE_CHANNEL} UNAVAILABLE ({exc}) — high-power gating was "
                         f"NOT applied, results still include high-power-off periods")
    return (_SpanSet(active), _SpanSet(_invert_spans(active, start_ns, end_ns)),
            notes, hpe_failed)


# ── FULL-POWER GATE ───────────────────────────────────────────────────────────
# A blink is only a blink while the array runs its 520 A script at full intensity. Two
# archiver channels say when that is, both measured on 19-23 Oct 2026:
#
#   L3-PC<k>-PC<k>:VoltageInternalDumpBus — the power converter's dump-bus voltage.
#       92-106 V at full power (it ripples with the shots), 76-83 V while warming up,
#       near zero when the diodes are off. All four converters switch together.
#       It does NOT see the array's low-power mode: on 20 Oct 16:44-17:04 all four arrays
#       ran at ~25 % while the voltage stayed above 88 V.
#   HAPLS-ENER_IN_PD<k>M1_LT7_DIAG2:Energy — the array's output energy, ~3.3 Hz. This one
#       sees the low-power mode, and it sees every blink: a pulser going dark takes
#       3-5 % off the array's energy for one or two shots, a partial blink ~0.4 %, against
#       a noise floor of ~0.44 % (p0.1). The archive of PD2M1's meter is dead (two
#       samples per ten minutes, its MSS energy channel in fault), so PD2M1 is read frame
#       by frame instead.
#
# The voltage decides which frames are read and judged at all; the energy then says WHEN
# something happened, so on the arrays whose energy is archived only the frames around
# its dips need decoding. WHICH pulser it was is still read off the frames.
#
# Both channels give HTTP 500 for a day-long query, so they are fetched in chunks.
DUMPBUS_CHANNEL = "L3-PC{k}-PC{k}:VoltageInternalDumpBus"
ENERGY_CHANNEL = "HAPLS-ENER_IN_{cam}_LT7_DIAG2:Energy"
FULL_POWER_V = 88.0     # dump bus at/above this = full power
LIT_POWER_V = 70.0      # at/above this the diodes fire at all — full power or warming
_POWER_MERGE_S = 3.0    # samples closer than this belong to one stretch
_POWER_MIN_SPAN_S = 60.0
_DUMPBUS_CHUNK_S = 3600
_ENERGY_CHUNK_S = 1800
_POWER_THREADS = 8
# An array's energy needs at least this many samples per second of full power to be used;
# PD2M1's dead archive has ~0.003.
_ENERGY_MIN_RATE = 1.0


def _pc_of(cam_key: str) -> str:
    """'PD3M1' → '3': power converter PC<k> drives diode array PD<k>M1."""
    m = re.match(r"PD(\d)", cam_key)
    return m.group(1) if m else "1"


def _cpva_chunk(channel: str, a: int, b: int, depth: int = 0) -> list:
    """One chunk, split in two and retried if the archiver refuses it (HTTP 500 on a
    query it finds too big), down to two levels."""
    try:
        return _cpva_samples(channel, a, b)
    except Exception:
        if depth >= 2 or b - a <= 120 * _NS_PER_S:
            raise
        mid = (a + b) // 2
        return (_cpva_chunk(channel, a, mid, depth + 1)
                + _cpva_chunk(channel, mid, b, depth + 1))


def _cpva_samples_chunked(channel: str, spans, chunk_s: int,
                          threads: int = _POWER_THREADS) -> list:
    """Sorted, de-duplicated (ts_ns, value) samples over `spans`, fetched in chunks of
    `chunk_s` on a few threads. Raises if any chunk fails for good."""
    step = int(chunk_s) * _NS_PER_S
    parts = []
    for a, b in _merge_spans(spans):
        x = a
        while x < b:
            parts.append((x, min(b, x + step)))
            x += step
    if not parts:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(threads, len(parts)))) as ex:
        res = list(ex.map(lambda p: _cpva_chunk(channel, p[0], p[1]), parts))
    merged: dict = {}
    for r in res:
        for t, v in r:
            merged[t] = v
    return sorted(merged.items())


def _spans_where(samples: list, test, merge_ns: int, min_ns: int) -> list:
    """[(first, last)] sample times of the runs where `test(value)` holds. Runs whose
    samples lie within `merge_ns` of each other are one run, so a single low ripple
    sample does not split a stretch; runs shorter than `min_ns` are dropped."""
    out: list = []
    cur = None
    for t, v in samples:
        if not test(v):
            continue
        if cur is not None and t - cur[1] <= merge_ns:
            cur[1] = t
        else:
            if cur is not None:
                out.append((cur[0], cur[1]))
            cur = [t, t]
    if cur is not None:
        out.append((cur[0], cur[1]))
    return [(a, b) for a, b in out if b - a >= min_ns]


def _running_dev(values: np.ndarray, k: int = 15) -> np.ndarray:
    """Each sample relative to the median of the 2k+1 samples around it, minus one: a
    blink is a one- or two-sample dip, so a 31-sample median is the array's level
    without it."""
    v = np.asarray(values, dtype=np.float64)
    if v.size == 0:
        return v
    pad = np.pad(v, k, mode="edge")
    med = np.median(np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1), axis=1)
    return v / np.where(med == 0, 1.0, med) - 1.0


@dataclass
class CamPower:
    """What the archiver says about one diode array over the scan window."""
    cam: str
    full_spans: list = field(default_factory=list)    # dump bus ≥ full_power_v
    lit_spans: list = field(default_factory=list)     # dump bus ≥ LIT_POWER_V
    judged_spans: list = field(default_factory=list)  # full spans less their two ramps
    has_energy: bool = False
    energy_samples: int = 0
    low_spans: list = field(default_factory=list)     # this array's own low-power stretches
    dips: list = field(default_factory=list)          # [(t0, t1, depth)] energy dips
    note: str = ""
    failed: bool = False


@dataclass
class PowerInfo:
    """The full-power gate for one scan: every array, plus the low-power stretches of all
    of them together. The arrays change level together, so a low stretch seen on any of
    them applies to every one — including PD2M1, whose own energy is not archived."""
    cams: dict = field(default_factory=dict)     # cam_key → CamPower
    low_all: list = field(default_factory=list)  # merged low-power stretches, all arrays
    notes: list = field(default_factory=list)
    failed: bool = False                         # no dump bus at all → no full-power gate


def _energy_low_spans(et: np.ndarray, ev: np.ndarray, spans: list, frac: float) -> list:
    """Stretches inside each full-power span where the energy sat below `frac` × that
    span's median — the low-power mode the dump bus cannot see."""
    out: list = []
    for a, b in spans:
        m = (et >= a) & (et <= b)
        if not m.any():
            continue
        thr = frac * float(np.median(ev[m]))
        low = [(int(t), float(v)) for t, v in zip(et[m], ev[m])]
        out += _spans_where(low, lambda v, _t=thr: v < _t,
                            int(_POWER_MERGE_S * _NS_PER_S), 0)
    return out


def _energy_dips(et: np.ndarray, dev: np.ndarray, spans: list, max_dev: float) -> list:
    """[(t0, t1, depth)] — runs of energy samples below `max_dev` of their running
    median inside `spans`, samples within a second of each other merged."""
    out: list = []
    for a, b in spans:
        m = (et >= a) & (et <= b) & (dev < max_dev)
        for t, d in zip(et[m], dev[m]):
            t = int(t)
            if out and t - out[-1][1] <= _NS_PER_S:
                out[-1] = (out[-1][0], t, min(out[-1][2], float(d)))
            else:
                out.append((t, t, float(d)))
    return out


def _cam_power(cam_key: str, windows: list, params: "AnalysisParams") -> CamPower:
    """Dump bus and energy of one array over `windows`. Never raises: a failure is
    recorded on the result and the caller falls back to reading every frame."""
    cp = CamPower(cam=cam_key)
    ch = DUMPBUS_CHANNEL.format(k=_pc_of(cam_key))
    try:
        volts = _cpva_samples_chunked(ch, windows, _DUMPBUS_CHUNK_S)
    except Exception as exc:
        cp.failed, cp.note = True, f"{ch} unavailable ({exc})"
        return cp
    if not volts:
        cp.failed, cp.note = True, f"{ch}: no samples in the window"
        return cp
    merge = int(_POWER_MERGE_S * _NS_PER_S)
    min_span = int(_POWER_MIN_SPAN_S * _NS_PER_S)
    cp.full_spans = _spans_where(volts, lambda v: v >= params.full_power_v, merge, min_span)
    cp.lit_spans = _spans_where(volts, lambda v: v >= LIT_POWER_V, merge, 0)
    e0 = int(params.edge_start_s * _NS_PER_S)
    e1 = int(params.edge_end_s * _NS_PER_S)
    cp.judged_spans = [(a + e0, b - e1) for a, b in cp.full_spans if b - e1 > a + e0]

    ech = ENERGY_CHANNEL.format(cam=cam_key)
    try:
        energy = _cpva_samples_chunked(ech, cp.full_spans, _ENERGY_CHUNK_S)
    except Exception as exc:
        energy = []
        cp.note = f"{ech} unavailable ({exc})"
    full_s = sum(b - a for a, b in cp.full_spans) / _NS_PER_S
    cp.energy_samples = len(energy)
    cp.has_energy = full_s > 0 and len(energy) / full_s >= _ENERGY_MIN_RATE
    if cp.has_energy:
        et = np.array([t for t, _ in energy], dtype=np.int64)
        ev = np.array([v for _, v in energy], dtype=np.float64)
        cp.low_spans = _energy_low_spans(et, ev, cp.full_spans, params.low_power_frac)
        cp.dips = _energy_dips(et, _running_dev(ev), cp.judged_spans, params.dip_dev)
    full_txt = _fmt_dur(int(full_s * _NS_PER_S))
    if cp.has_energy:
        cp.note = (f"{cam_key}: full power {full_txt} in {len(cp.full_spans)} stretch(es); "
                   f"{len(cp.dips)} energy dip(s), {len(cp.low_spans)} low-power "
                   f"stretch(es)")
    else:
        cp.note = (f"{cam_key}: full power {full_txt} in {len(cp.full_spans)} stretch(es); "
                   f"energy not archived ({cp.energy_samples} sample(s)) — every frame "
                   f"at full power is read")
    return cp


def full_power_info(start_ns: int, end_ns: int, params: "AnalysisParams",
                    active: "_SpanSet | None" = None,
                    cams=None) -> PowerInfo:
    """The full-power gate for every array (all four by default — PD2M1 needs the
    others' low-power stretches), queried only inside the active-time gate."""
    windows = active.spans if active is not None else [(int(start_ns), int(end_ns))]
    info = PowerInfo()
    keys = list(cams) if cams is not None else list(CAMERAS)
    with ThreadPoolExecutor(max_workers=len(keys) or 1) as ex:
        results = list(ex.map(lambda k: _cam_power(k, windows, params), keys))
    for cp in results:
        info.cams[cp.cam] = cp
        info.notes.append(cp.note)
    info.failed = all(cp.failed for cp in results)
    info.low_all = _merge_spans(s for cp in results for s in cp.low_spans)
    return info


def plan_reading(files: list, cp: "CamPower | None", params: "AnalysisParams") -> tuple:
    """(files to read, one-line note) for one camera.

    Nothing outside the dump bus's lit time is read — the diodes were not firing, and the
    gap it leaves is read as the array being down, which is what it was. Inside full
    power: every frame where the energy is not archived (PD2M1), otherwise the frames
    within `dip_window_s` of each energy dip plus one every `sparse_step_s` — the regular
    frames carry the stretch references, the dead pulsers and the graphs, the dense ones
    the blinks. The last `stop_dense_s` before full power ends are read frame by frame on
    every array: that is where a pulser that takes the array down is seen. Warm-up and the
    ramp at the start get one frame every `sparse_step_s`, enough to measure them and never
    judged."""
    if cp is None or cp.failed:
        return list(files), f"all {len(files)} frame(s) read (no full-power gate)"
    lit = _SpanSet(cp.lit_spans)
    judged = _SpanSet(cp.judged_spans)
    dense = not (params.energy_guided and cp.has_energy)
    w = int(params.dip_window_s * _NS_PER_S)
    near = _SpanSet([(a - w, b + w + 1) for a, b, _d in cp.dips])
    tail = int(params.stop_dense_s * _NS_PER_S)
    ends = _SpanSet([(b - tail, b + 1) for _a, b in cp.full_spans])
    step = int(params.sparse_step_s * _NS_PER_S)
    kept: list = []
    n_dense = 0
    last_grid = None
    for ts, p in files:
        if not lit.contains(ts):
            continue
        if ends.contains(ts) or (judged.contains(ts) and (dense or near.contains(ts))):
            kept.append((ts, p))
            n_dense += 1
            continue
        if last_grid is None or ts - last_grid >= step:
            kept.append((ts, p))
            last_grid = ts
    if dense:
        note = (f"{len(kept)} of {len(files)} frame(s) read: every frame at full power, "
                f"one per {params.sparse_step_s:g} s while warming")
    else:
        note = (f"{len(kept)} of {len(files)} frame(s) read: {n_dense} around "
                f"{len(cp.dips)} energy dip(s) and in the last "
                f"{params.stop_dense_s:g} s before each stop, the rest one per "
                f"{params.sparse_step_s:g} s")
    return kept, note


# ── STATE MODEL & ANALYSIS ──────────────────────────────────────────────────────
# Per-pulser, per-frame state:
STATE_OFF    = 0    # array present but this pulser is dark  → counts toward dropouts
STATE_ON     = 1    # pulser lit
STATE_NODATA = -1   # whole frame is dark / no real array data → a gap (trip), never a dropout

# Event kinds — how a dark run of one pulser is classified. Every dark run is exactly one
# of these four, and the operator's own definitions decide which:
#   dropout — a flicker, short enough that nothing else happened;
#   fault   — dark for a while, THE ARRAY KEPT RUNNING, and it came back;
#   trip    — dark for a while and THE ARRAY WENT DOWN right after: this pulser took it out;
#   dead    — dark and not recoverable (survived neither the recoveries nor the clock).
# The fault/trip split is decided by looking FORWARD a bounded number of frames from the
# moment the pulser went dark, not by whether a trip happens to overlap the dark run
# somewhere. The old "trip_dropout" did the latter and so counted a pulser that merely sat
# dark through someone else's outage as if it had caused one.
KIND_DROPOUT = "dropout"   # dark ≤ dropout_frames, came back
KIND_FAULT   = "fault"     # dark > dropout_frames, array stayed up, came back
KIND_TRIP    = "trip"      # it was dark right up to an array stop: it took the array down
KIND_DEAD    = "dead"      # took the array down and never lit again, or never lit at all

# What an array STOP was — the time from full power ending to full power coming back,
# warm-up included. The operator's rules, 26-27 Oct 2026, decided first by whether a pulser
# took the array down (the CULPRIT: lit, then dark for at least `culprit_fast_s` /
# `culprit_slow_s` right up to the stop) and what became of it, then by how long it lasted:
#
#   culprit never lit again after the restarts, or lit
#   only after a pause of `replaced_min` (a new pulser)  → GAP_DEAD     dead pulser
#   culprit lit again after a shorter pause:
#       back within `outage_max_min`                     → GAP_TRIP     pulser trip
#       longer                                           → GAP_BIGGER   bigger problem
#   culprit, and nothing was switched on again in the
#   window (no restart, so nothing shown about it)       → GAP_TRIP
#   no culprit:
#       back within `manual_max_min`                     → GAP_MANUAL   manual stop
#       back within `outage_max_min`                     → GAP_UNKNOWN  outage, no pulser
#       longer, or never back                            → GAP_OFF      switched off
#
# A pause in the data shorter than `trip_min_s` inside a stretch is not a stop at all.
GAP_TRIP     = "trip"            # a pulser took the array down and lit again
GAP_DEAD     = "dead_pulser"     # a pulser took the array down and is dead
GAP_BIGGER   = "bigger_problem"  # a pulser took it down, and it stayed down past outage_max
GAP_UNKNOWN  = "unexplained"     # down for minutes, nothing dark first
GAP_MANUAL   = "manual"          # nothing dark first, back within minutes — our own hands
GAP_OFF      = "diodes_off"      # nothing dark first, off for long — switched off
GAP_TRIGGER  = GAP_MANUAL        # the old name of a short stop of ours


@dataclass
class AnalysisParams:
    """Tunables for turning raw brightness into states + classified events.

    Classification is *per pulser and per stretch*: within each stretch at full power,
    every pulser is scored against its own level at the START of that stretch (see
    `_stretch_scores`), then divided by the frame's array level so the whole array
    brightening or dimming together cancels. A pulser dark already when the stretch began
    has no lit level of its own there, so it is scored against the February all-alive
    reference instead — which is what still lets it read as dead.

    Measured over 20-22 Oct 2026 at full power: a blink takes a pulser to 0.25-0.55 of its
    stretch level (full) or 0.51-0.75 (partial — PD1 A8, PD4 E4/E7, where the array's energy
    dips only ~0.4 %), while healthy pulsers stay within a few percent of 1.0. Hence one line
    at 0.80 for "dark" and a second at 0.55 to tell a full blink from a partial one. There is
    no hysteresis band: a frame below the line is a dark frame."""
    blink_below: float = 0.80   # score below this → the pulser is dark in this frame
    full_below: float = 0.55    # a dark run whose lowest score is below this is a FULL blink
    # Frame mean below this × the window's normal frame → no-data. Must sit between the
    # blank level (≈0.04 of a running frame) and the warm-up level (≈0.31 of one); 0.12
    # is roughly the geometric middle, ~2.8x clear of blank and ~2.6x clear of warm-up.
    # Anything near 0.3 would start reading warm-up stretches as array trips.
    nodata_frac: float = 0.12
    gap_factor: float = 4.0   # a time gap > gap_factor × cadence has no data (see trip_min_s)
    debounce: int = 1         # OFF runs shorter than this many data frames are noise → ON
    use_reference: bool = True  # blend per-pulser reference brightness into the alive level
    dropout_frames: int = 5   # dark runs no longer than this are dropouts (flickers)
    # ── What an array stop was (all in wall-clock, never in frames) ───────────────
    # Frame-counted limits silently change meaning by 16x between the 5 s archive cadence
    # and the 3.3 Hz stream, which is how a 1 s power-down ramp came to be read as eleven
    # dead pulsers. Anything describing the array is therefore measured in seconds.
    trip_min_s: float = 60.0     # a pause in the data shorter than this is not a stop
    # The culprit of a stop: lit earlier in the stretch, then dark for at least this long
    # right up to it. The operator's numbers: 2-5 s on the 3.3 Hz stream, 30 s on the old
    # 0.2 Hz archive — longer than the ~1 s in which the whole array dims as it goes off.
    culprit_fast_s: float = 3.0
    culprit_slow_s: float = 30.0
    manual_max_min: float = 3.0   # nothing dark first and back within this → our own stop
    outage_max_min: float = 8.0   # an ordinary outage is cleared within this
    # A culprit that lights again only after a pause at least this long was REPLACED — a
    # new pulser takes 40-90 min to put in, or it happened overnight. After a shorter pause
    # it revived and was not dead.
    replaced_min: float = 30.0
    # ── When a dark pulser is beyond recovery ─────────────────────────────────────
    # For a pulser that went dark WITHOUT taking the array down: the operator often fires
    # several recoveries back to back, so one still dark after the first is not yet dead.
    dead_restarts: int = 3        # failed this many recoveries → dead
    dead_confirm_min: float = 20.0  # or stayed dark this long while the array ran → dead
    warm_level: float = 0.0   # array level counting as warm-up; 0 = derive from reference
    warm_min_frames: int = 5  # a warm-up window must last this many frames to be reported
    # Minimum correlation with the reference's per-pulser pattern for a frame to count as
    # showing the array at all. Measured: noise frames -0.22..-0.08, warm-up 0.891,
    # running 0.897 — so anything in the middle separates them. See `_pattern_correlation`.
    pattern_min: float = 0.50
    # Active-time gate settings, kept here so a result can say what it was gated by.
    # The spans themselves are computed once per scan and handed to `analyze_camera`.
    gate_hours: bool = False
    gate_high_power: bool = False
    gate_hour_start: int = GATE_HOUR_START
    gate_hour_end: int = GATE_HOUR_END
    # ── Full power and blinks (see the FULL-POWER GATE section) ─────────────────────
    gate_full_power: bool = False   # judge only while the dump bus says full power
    # Read only the frames around the array's energy dips (plus one every
    # `sparse_step_s`) on the arrays whose energy is archived. Off = read every frame.
    energy_guided: bool = True
    full_power_v: float = FULL_POWER_V
    edge_start_s: float = 30.0    # ramp-up after full power begins — not judged
    edge_end_s: float = 15.0      # power-down ramp before the voltage falls — not judged
    low_power_frac: float = 0.80  # energy below this × its stretch median = below full power
    low_power_pad_s: float = 5.0  # and this far either side of such a stretch
    dip_dev: float = -0.0035      # energy this far below its running median = a candidate
    dip_window_s: float = 1.5     # frames read either side of an energy dip
    unexplained_dev: float = -0.007  # a dip this deep with no pulser seen is reported
    sparse_step_s: float = 5.0    # frame spacing away from the dips, energy-guided reading
    stop_dense_s: float = 30.0    # every frame this long before full power ends — the culprit
    ref_len_s: float = 60.0       # a stretch's reference = median of its first this-many s
    dead_rel: float = 0.55        # reference / February level below this = dark from start
    array_level_min: float = 0.85  # frame level below this = the array, not a pulser
    array_down_n: int = 4         # this many pulsers dark in one frame = the array changed


@dataclass
class DropoutEvent:
    """One dark run of one pulser, with the verdict reached by looking forward from it.

    `recovery_ns` is set for every kind that came back — including `KIND_DEAD`, where it
    means the pulser was replaced. A dead pulser is not necessarily dead at the end of the
    day: replacing one takes an hour or two, and then it lights again. The count of dead
    pulsers answers "how many died today", so a replaced one stays in it, reported with both
    times."""
    start_ns: int                    # ts of the first dark frame of this run
    recovery_ns: "int | None" = None  # ts of the first ON frame after it (None = never came back)
    dark_frames: int = 0             # number of OFF frames in the run
    nodata_frames: int = 0           # number of no-data frames the run spans
    kind: str = KIND_DROPOUT         # KIND_DROPOUT | KIND_FAULT | KIND_TRIP | KIND_DEAD
    array_fell: bool = False         # the array went down inside the look-ahead of this run
    restarts: int = 0                # array recoveries that happened while it stayed dark
    ended_by: str = "recovered"      # "recovered" | "replaced" | "end of window"
    # Lowest score the pulser reached in this run, and whether that makes it a PARTIAL
    # blink: dark (below `blink_below`) but never below `full_below`.
    min_score: float = float("nan")
    partial: bool = False

    @property
    def is_blink(self) -> bool:
        """Every dark run that is not a death is a blink — the flicker, the longer fault and
        the one the array fell after alike. What the operator counts is the pulser going
        out; how long it stayed out and what followed are separate columns."""
        return self.kind in (KIND_DROPOUT, KIND_FAULT, KIND_TRIP)

    def duration_ns(self, end_fallback_ns: int) -> int:
        end = self.recovery_ns if self.recovery_ns is not None else end_fallback_ns
        return max(0, end - self.start_ns)


@dataclass
class ArrayTrip:
    """One array STOP: from full power ending to full power coming back (warm-up belongs
    to the stop). `kind` is one of the GAP_* verdicts above.

    The deliberate kinds are deliberately kept in this list rather than thrown away: the
    operator needs to see when the array was down and why, they just must not be counted as
    outages. Everything that consumes trips therefore filters on `is_outage`. A pause in the
    data shorter than `trip_min_s` inside a stretch never becomes an ArrayTrip at all."""
    start_ns: int
    end_ns: int
    kind: str = GAP_TRIP
    recovered: list = field(default_factory=list)  # pulser names ON again after the trip
    died: list = field(default_factory=list)        # pulser names alive before, never after
    # The culprits: lit earlier in the stretch, then dark right up to the stop for at least
    # the culprit time. Ordered longest-dark first.
    caused_by: list = field(default_factory=list)   # [(name, dark_frames), ...]
    # What became of each culprit: "revived" / "dead" / "replaced" / "not restarted".
    fates: dict = field(default_factory=dict)
    restarted: bool = False    # data resumed after this trip → the diodes were switched on
    back: bool = True          # False = the array was not back at full power in the window

    @property
    def duration_ns(self) -> int:
        return max(0, self.end_ns - self.start_ns)

    @property
    def is_outage(self) -> bool:
        """True for the kinds that count as the array going down against its will.

        Time we stopped it ourselves — a manual stop, the diodes switched off — is not
        downtime: nobody was trying to run the array, so it is never counted as an outage,
        never a restart, and never a reason to call a pulser dead."""
        return self.kind in (GAP_TRIP, GAP_DEAD, GAP_BIGGER, GAP_UNKNOWN)

    @property
    def is_deliberate(self) -> bool:
        return self.kind in (GAP_MANUAL, GAP_OFF)

    @property
    def cause_names(self) -> list:
        return [n for n, _f in self.caused_by]

    @property
    def kind_label(self) -> str:
        return _GAP_LABEL.get(self.kind, self.kind)


_GAP_LABEL = {GAP_TRIP: "pulser trip", GAP_DEAD: "dead pulser",
              GAP_BIGGER: "bigger problem", GAP_UNKNOWN: "outage, no pulser",
              GAP_MANUAL: "manual stop", GAP_OFF: "switched off"}


@dataclass
class PulserStats:
    """Per-pulser summary. Counts are of dark runs by kind; times are wall-clock.

    There is no `dead_from_start` any more. A dead pulser is a dead pulser — when it started
    being dead is `dead_since_ns`, a timestamp, not a separate category. And `is_dead` does
    not mean "still dark": a pulser that died and was replaced during the day is still one of
    the day's dead, with `dead_until_ns` saying when it came back."""
    name: str
    dropouts: int = 0                # flickers (dark ≤ dropout_frames)
    faults: int = 0                  # dark longer, array stayed up, came back
    trips: int = 0                   # dark longer and the array fell right after
    partial_blinks: int = 0          # blinks that never went below `full_below`
    caused_trips: int = 0            # array outages this pulser was dark right up to
    is_dead: bool = False            # died at some point in this window
    dead_since_ns: "int | None" = None   # when it went dark for the last/only time
    dead_until_ns: "int | None" = None   # when it lit again (replaced); None = still dark
    death_ns: "int | None" = None    # last time it was ON before dying
    longest_run_ns: int = 0          # longest run without a dropout (wall-clock, incl. no-data)
    longest_run_active_ns: int = 0   # same run but with no-data gaps subtracted
    # Uptime is ON time over the ARRAY-UP time of the whole camera, so all forty pulsers
    # share one denominator. Frame-count ratios per pulser gave every pulser 100 % whatever
    # its run length, because a pulser seen in fewer frames simply got a smaller denominator.
    uptime_pct: float = 0.0
    on_time_ns: int = 0              # time lit while the array was running at full power
    dark_time_ns: int = 0            # time dark while the array was running at full power
    total_down_ns: int = 0
    frames: int = 0                  # frames actually judged (gate-excluded ones are not)
    on_frames: int = 0
    off_frames: int = 0
    nodata_frames: int = 0
    excluded_frames: int = 0         # frames dropped by the active-time gate
    first_drop_ns: "int | None" = None
    last_drop_ns: "int | None" = None
    span_ns: int = 0                 # ts[last] - ts[first]
    current_state: int = STATE_NODATA
    events: list = field(default_factory=list)

    @property
    def total_dropouts(self) -> int:
        """Real outages only. Flickers are counted separately — mixing them in would swamp
        the events that matter."""
        return self.faults + self.trips

    @property
    def total_off_events(self) -> int:
        return self.dropouts + self.faults + self.trips

    @property
    def blinks(self) -> int:
        """Every time the pulser went out at full power, full and partial alike."""
        return self.total_off_events

    @property
    def full_blinks(self) -> int:
        return self.blinks - self.partial_blinks

    @property
    def mtbf_ns(self) -> "int | None":
        """Mean time between outages (observed span / number of outages)."""
        if self.total_dropouts <= 0:
            return None
        return int(self.span_ns / self.total_dropouts)

    def gaps_between_dropouts_ns(self) -> list:
        """Time between consecutive fault/trip starts (excludes flickers and 'dead')."""
        starts = [ev.start_ns for ev in self.events
                  if ev.kind in (KIND_FAULT, KIND_TRIP)]
        return [b - a for a, b in zip(starts, starts[1:])]

    @property
    def was_replaced(self) -> bool:
        return self.is_dead and self.dead_until_ns is not None

    @property
    def status_str(self) -> str:
        if self.was_replaced:
            return "dead — replaced"
        if self.is_dead:
            return "dead"
        return _state_str(self.current_state)


@dataclass
class StretchSummary:
    """One stretch at full power: what was judged in it and what blinked.

    A stretch is the unit the scores are taken against — every pulser's reference is its
    own level in the first `ref_len_s` of it — so it is also the unit the counts are
    reported in, the way the operator checks a day: stretch by stretch."""
    index: int                    # 1-based
    start_ns: int                 # full power began (dump bus), or the first judged frame
    end_ns: int
    judged_ns: int = 0            # array-up time actually judged inside it
    low_power_n: int = 0          # low-power stretches inside it (not judged)
    low_power_ns: int = 0
    dark_from_start: list = field(default_factory=list)   # pulser names, no lit level
    blinks: int = 0               # full blinks
    partial: int = 0              # partial blinks
    per_pulser: dict = field(default_factory=dict)       # name → blinks (full + partial)
    unexplained: int = 0          # deep energy dips with no pulser seen
    ref_frames: int = 0           # frames the reference was taken from
    judged: bool = True           # False = no usable reference, nothing judged in it
    note: str = ""


@dataclass
class CameraAnalysis:
    stats: list                       # PulserStats per ROI, aligned to the rois order
    state_matrix: "np.ndarray"        # shape (n_rois, n_frames): STATE_ON/OFF/NODATA
    frame_nodata: list                # bool per frame (whole array down)
    times_ns: list                    # ts_ns per frame (augmented with trip-gap markers)
    trips: list = field(default_factory=list)      # ArrayTrip list
    baselines: list = field(default_factory=list)  # learnt alive contrast level per ROI
    signal_matrix: "np.ndarray | None" = None      # (n_frames, n_rois) contrast signal
    score_matrix: "np.ndarray | None" = None       # (n_frames, n_rois) signal / alive level
    warnings: list = field(default_factory=list)   # human-readable analysis warnings
    # The settings this result was produced with, so labels and tooltips can quote the
    # thresholds actually in force rather than repeating hard-coded defaults.
    params: "AnalysisParams | None" = None
    # ── Array-level (not per-pulser) results ────────────────────────────────────
    frame_warmup: list = field(default_factory=list)   # bool per frame — array is warming
    array_level: list = field(default_factory=list)    # per-frame level vs normal (~1.0)
    warmup_windows: list = field(default_factory=list)  # [(start_ns, end_ns), ...]
    warm_level_used: float = 0.0    # the warm-up level actually applied (0 = none found)
    warm_level_source: str = "none"  # "reference" | "manual" | "none"
    # Frames inside a real outage, and frames where the diodes were simply off. Both are
    # subsets of `frame_nodata`; the remainder of it is short archive gaps.
    frame_trip: list = field(default_factory=list)
    frame_diodes_off: list = field(default_factory=list)
    # The one thing every per-pulser judgement is made on: the array was there, at full
    # power, and we were allowed to look. Not warming, not tripped, not switched off, not
    # gated out. This is also the uptime denominator, shared by all forty pulsers.
    frame_judge: list = field(default_factory=list)
    array_up_ns: int = 0
    # ── Active-time gate ────────────────────────────────────────────────────────
    frame_excluded: list = field(default_factory=list)   # bool per frame — gated out
    excluded: "_SpanSet | None" = None    # the excluded spans themselves
    gate_notes: list = field(default_factory=list)       # which rules were applied
    gate_hpe_failed: bool = False   # high-power gating asked for but archiver unreachable
    # ── Full power and blinks ───────────────────────────────────────────────────
    # Frames with the array lit but NOT at full power: the ramps either end of a stretch,
    # the energy's low-power stretches, and single frames in which the whole array changed
    # level. None of them is judged; unlike a warm-up they do not end a stretch.
    frame_below_full: list = field(default_factory=list)
    stretches: list = field(default_factory=list)          # StretchSummary per stretch
    unexplained_dips: list = field(default_factory=list)   # [(ts_ns, depth)]
    power: "CamPower | None" = None       # the archiver's view of this array, if read
    power_failed: bool = False            # full-power gating asked for but not possible

    @property
    def excluded_total_ns(self) -> int:
        return self.excluded.total_ns if self.excluded is not None else 0

    @property
    def observed_total_ns(self) -> int:
        """Wall-clock time the result actually describes.

        Only the excluded time INSIDE the frame span may be subtracted. `excluded_total_ns`
        covers the whole scan window, most of which can lie beyond the first and last frame
        — when the archiver stops writing at the end of a shift, the night is excluded time
        that the timeline never reaches, and subtracting it left this reading at zero for a
        result that plainly described two hours of running."""
        if not self.times_ns:
            return 0
        t0, t1 = self.times_ns[0], self.times_ns[-1]
        inside = self.excluded.overlap_ns(t0, t1) if self.excluded is not None else 0
        return max(0, (t1 - t0) - inside)

    @property
    def warmup_total_ns(self) -> int:
        return sum(max(0, b - a) for a, b in self.warmup_windows)

    @property
    def outages(self) -> list:
        """Trips that mean the array went down. Diodes-off stretches live in `trips` too so
        they can be reported, but they are not outages and must never be counted as such."""
        return [t for t in self.trips if t.is_outage]

    @property
    def diodes_off_total_ns(self) -> int:
        return sum(t.duration_ns for t in self.trips if t.kind == GAP_OFF)

    @property
    def array_restarts(self) -> int:
        """How many times the diodes had to be switched on again — i.e. outages that were
        followed by real data resuming. Switching the diodes back on after a deliberate stop
        is not a recovery from anything, so diodes-off stretches do not count."""
        return sum(1 for t in self.outages if t.restarted)

    @property
    def blinks_total(self) -> int:
        return sum(s.blinks for s in self.stats)

    @property
    def partial_total(self) -> int:
        return sum(s.partial_blinks for s in self.stats)

    def stop_summary(self) -> str:
        """'2 pulser trip · 1 dead pulser · 3 manual stop' — the array's stops by kind, in
        the order of the GAP_* table, outages first."""
        order = (GAP_DEAD, GAP_TRIP, GAP_BIGGER, GAP_UNKNOWN, GAP_MANUAL, GAP_OFF)
        counts = {k: 0 for k in order}
        for t in self.trips:
            counts[t.kind] = counts.get(t.kind, 0) + 1
        return " · ".join(f"{counts[k]} {_GAP_LABEL[k]}" for k in order if counts[k])

    def worst_blinkers(self, n: int = 3) -> list:
        """[(name, blinks)] of the pulsers that blinked most, most first."""
        ranked = sorted(((s.name, s.blinks) for s in self.stats if s.blinks),
                        key=lambda nb: -nb[1])
        return ranked[:n]


def _fmt_dur(ns: "int | None") -> str:
    """Human-readable duration from nanoseconds, e.g. '3d 4h', '5h 12m', '45m', '30s'."""
    if ns is None:
        return "—"
    s = int(ns // 1_000_000_000)
    if s < 0:
        s = 0
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


_REF_VALID_FRAC = 0.05  # ref_contrast below this fraction of ref_brightness ⇒ broken ROI box


def _pick_signal_and_ref(rois: list, mean_matrix: np.ndarray, contrast_matrix: np.ndarray,
                         frame_nodata: list) -> tuple:
    """Choose contrast or plain brightness as the classification signal, per pulser.

    Floor-subtracted contrast (see `_tile_floor_contrast`) is glow-robust and is what
    tells a truly dead pulser apart from an intrinsically dark corner, so it is the
    signal of choice. It needs SOME dark separator to exist within reach of the box
    though; where a pulser is packed so tightly that its window never reaches one, the
    measured contrast is a near-zero number divided by a near-zero number, i.e. noise.
    Detected via the reference image, where every pulser is lit by definition: a usable
    ROI has ref_contrast at least a few % of ref_brightness. Anything far below that is
    a measurement problem, not a real pulser property — fall back to plain brightness.

    Returns (signal_matrix, ref_values, use_contrast, warnings) — ref_values[r] is
    ref_contrast or ref_brightness, matching whichever signal was chosen for that pulser,
    and use_contrast[r] says which one that was (the warm-up reference has to be read off
    the same signal)."""
    n_rois = mean_matrix.shape[1] if mean_matrix.size else len(rois)
    use_contrast = np.ones(n_rois, dtype=bool)
    warnings: list = []
    up = np.array([not nd for nd in frame_nodata], dtype=bool)

    for r, roi in enumerate(rois):
        ref_b = float(getattr(roi, "ref_brightness", 0.0) or 0.0)
        ref_c = float(getattr(roi, "ref_contrast", 0.0) or 0.0)
        broken = False
        if ref_b > 0:
            broken = ref_c < _REF_VALID_FRAC * ref_b
        elif up.any() and mean_matrix.size:
            # No reference at all: fall back to a data-only sanity check — a working
            # contrast signal must vary at least somewhat; if its spread is tiny next
            # to the brightness spread, the ring is stuck on a fixed nearby value.
            c_col, m_col = contrast_matrix[up, r], mean_matrix[up, r]
            c_spread = float(np.percentile(c_col, 90) - np.percentile(c_col, 10))
            m_spread = float(np.percentile(m_col, 90) - np.percentile(m_col, 10))
            broken = m_spread > 0 and c_spread < 0.1 * m_spread
        if broken:
            use_contrast[r] = False
            warnings.append(
                f"{roi.name}: contrast measurement looks unreliable (its ROI box likely "
                f"has no visible dark margin around it) — using brightness instead for "
                f"this pulser. Redraw its box in the ROI editor with a small gap from "
                f"neighbours for the normal glow-robust detection.")

    if mean_matrix.size:
        signal = np.where(use_contrast[None, :], contrast_matrix, mean_matrix)
    else:
        signal = contrast_matrix
    ref_values = [float(getattr(r, "ref_contrast", 0.0) or 0.0) if use_contrast[i]
                 else float(getattr(r, "ref_brightness", 0.0) or 0.0)
                 for i, r in enumerate(rois)]
    return signal, ref_values, list(use_contrast), warnings


def _learn_baselines(signal_matrix: np.ndarray, ref_values: list, frame_nodata: list,
                     use_reference: bool, frame_warmup: "list | None" = None) -> list:
    """Per-pulser "alive" signal level used to score every frame relative to that
    pulser's own bright state — the heart of adaptive classification.

    Data-driven: a high percentile of the pulser's signal over frames where the array
    was up (its typical lit level). Reference-driven: the pulser's level in the
    all-alive reference image, rescaled to the day's exposure. Reference wins when
    available because it also knows the true level of a pulser that stayed dead the
    whole window (whose data percentile would be its dead level).

    Warm-up frames are excluded: the array runs at roughly a third of its normal level
    while warming, so counting those frames would drag every pulser's "alive" level down
    and make genuinely dim pulsers look healthy. A window that is warm-up throughout has
    nothing else to learn from and falls back to using all of it."""
    n_rois = signal_matrix.shape[1] if signal_matrix.size else len(ref_values)
    up = np.array([not nd for nd in frame_nodata], dtype=bool)
    if frame_warmup is not None and len(frame_warmup) == len(up):
        running = up & ~np.asarray(frame_warmup, dtype=bool)
        if running.any():
            up = running
    if signal_matrix.size == 0 or not up.any():
        return [max(1e-6, float(v) or 1e-6) for v in ref_values]
    up_signal = signal_matrix[up]                     # (n_up, n_rois)
    data_base = np.percentile(up_signal, 80, axis=0)  # typical lit level per pulser
    ref = np.asarray(ref_values, dtype=np.float64)

    base = data_base.astype(np.float64).copy()
    if use_reference and np.any(ref > 0):
        # Rescale the reference to the day's exposure using the pulsers that are clearly
        # alive (upper half of data/ref ratios), so dead/dropped pulsers don't drag it.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = np.where(ref > 0, data_base / np.maximum(ref, 1e-9), np.nan)
        good = ratios[np.isfinite(ratios) & (ratios > 0)]
        if good.size:
            scale = float(np.median(good[good >= np.median(good)]))
        else:
            scale = 1.0
        scaled_ref = ref * scale
        # Where a reference exists, trust the larger of (data level, scaled reference):
        # a full-day-dead pulser keeps its bright reference level and so reads as dead.
        for r in range(n_rois):
            if ref[r] > 0:
                base[r] = max(data_base[r], scaled_ref[r])
    return [max(1e-6, float(b)) for b in base]


def _augment_timeline(sample_points: list, n_rois: int, gap_factor: float,
                      excluded: "_SpanSet | None" = None,
                      edge_ns: "tuple | None" = None):
    """Return (times, contrast_matrix, mean_matrix, frame_means, synth, excl, cadence_ns).

    Both the floor-subtracted contrast and the plain ROI mean are kept per frame — the caller picks
    whichever is reliable per pulser (see `_pick_signal_and_ref`).

    Inserts synthetic no-data markers into gaps in the image stream: when the array
    trips and stops writing images, the raw samples just jump in time. Without markers
    the analyser would join ON→ON across the gap and think the pulser ran the whole
    time. Two markers per gap make the trip explicit for both stats and the timeline.

    `edge_ns` is the span of the ORIGINAL samples, before the frames that showed no array
    were dropped. Without it, frames dropped at the START or END of a window leave no trace
    at all: markers are only planted BETWEEN two surviving samples, so a day that ends with
    the diodes off simply ends at the last good frame. The array was demonstrably down for
    those two hours, and the timeline said nothing — while every pulser's last observed state
    was "dark", which is precisely the shape of a false death.

    Excluded time (see the ACTIVE-TIME GATE section) changes what a gap MEANS, so it has
    to be handled right here rather than filtered afterwards. A gate that removes
    21:00–07:00 leaves a ten-hour hole between two frames, and read as an image-stream
    gap that hole becomes a ten-hour array trip every single night — the exact false
    reading the gate exists to prevent. So a gap is measured by its OPEN time (its length
    minus whatever the gate excluded), and only that decides whether it looks like a
    trip. Every gap overlapping excluded time additionally gets one marker planted inside
    the excluded part, flagged rather than counted: it still breaks the run, so a pulser
    is never joined ON→ON across a night it was not observed, but it is not an outage."""
    def _row(vals):
        m = list(vals) if vals else [0.0] * n_rois
        if len(m) < n_rois:
            m = m + [0.0] * (n_rois - len(m))
        return m[:n_rois]

    ts0 = [sp.ts_ns for sp in sample_points]
    cadence = float(np.median(np.diff(ts0))) if len(ts0) >= 2 else 0.0

    times: list = []
    contrasts: list = []
    means: list = []
    fmeans: list = []
    synth: list = []
    excl: list = []

    def _marker(t: int, is_excluded: bool):
        times.append(t)
        contrasts.append([0.0] * n_rois)
        means.append([0.0] * n_rois)
        fmeans.append(0.0)
        synth.append(True)
        excl.append(is_excluded)

    # A stretch of dropped frames at the START of the window: mark it before anything else,
    # so the first surviving frame is not silently treated as the beginning of the day.
    if edge_ns and sample_points and cadence > 0:
        first_kept = sample_points[0].ts_ns
        e0 = edge_ns[0]
        if e0 is not None and first_kept - e0 > gap_factor * cadence:
            _marker(e0, bool(excluded.contains(e0)) if excluded else False)

    for i, sp in enumerate(sample_points):
        times.append(sp.ts_ns)
        contrasts.append(_row(getattr(sp, "roi_contrasts", None) or sp.roi_means))
        means.append(_row(sp.roi_means))
        fmeans.append(float(getattr(sp, "frame_mean", 1.0)))
        synth.append(False)
        excl.append(bool(excluded.contains(sp.ts_ns)) if excluded else False)
        if i + 1 >= len(sample_points):
            continue
        nxt = sample_points[i + 1].ts_ns
        covered = excluded.overlap_ns(sp.ts_ns, nxt) if excluded else 0
        pending: list = []
        # Only the part of the gap that was actually being observed can look like a trip.
        if cadence > 0 and (nxt - sp.ts_ns) - covered > gap_factor * cadence:
            step = int(cadence)
            pending += [(t, False) for t in (sp.ts_ns + step, nxt - step)
                        if sp.ts_ns < t < nxt]
        if covered > 0:
            # Plant the break in the middle of the excluded stretch this gap falls in, so
            # it lands inside the exclusion however the surrounding frames are spaced.
            for a, b in excluded.spans:
                lo, hi = max(a, sp.ts_ns + 1), min(b, nxt)
                if hi > lo:
                    pending.append(((lo + hi) // 2, True))
                    break
        for t, is_ex in sorted(set(pending)):
            _marker(t, is_ex)

    # …and the same at the END. This is the common case: the working day finishes, the diodes
    # go off, and the archiver keeps writing noise-only frames for hours. All of them are
    # dropped, so without this marker the series stops at the last good frame — every pulser
    # frozen in whatever state it happened to be in, with nothing to say the array had been
    # switched off. Two markers, so the stretch has a width rather than being a single point.
    if edge_ns and sample_points and cadence > 0:
        last_kept = sample_points[-1].ts_ns
        e1 = edge_ns[1]
        if e1 is not None and e1 - last_kept > gap_factor * cadence:
            step = int(cadence)
            for t in (last_kept + step, e1):
                if t > last_kept:
                    _marker(t, bool(excluded.contains(t)) if excluded else False)

    contrast_matrix = (np.asarray(contrasts, dtype=np.float32)
                       if contrasts else np.zeros((0, n_rois), dtype=np.float32))
    mean_matrix = (np.asarray(means, dtype=np.float32)
                  if means else np.zeros((0, n_rois), dtype=np.float32))
    return times, contrast_matrix, mean_matrix, fmeans, synth, excl, cadence


# Measured whole-frame means, as a fraction of full scale, on all four cameras:
#   running ≈ 0.40–0.47   ·   warming ≈ 0.125–0.161   ·   diodes off ≈ 0.018
# This constant only has to separate "the array was never up in this window" from any
# working state, so it sits between the blank level and the warm-up level with a wide
# margin on both sides (≈2.8x above blank, ≈2.5x below warm-up) — see `_detect_nodata`.
_BLANK_ABS = 0.05


def _pattern_correlation(contrast_matrix: np.ndarray, ref_contrast: list) -> "np.ndarray | None":
    """Per-frame correlation between the measured per-ROI contrasts and the reference's.

    Answers "is the pulser array visible in this frame at all?", which is a different
    question from "how bright is it". A frame with the diodes off is not dark — the
    camera's gain lifts pure sensor noise to a mid-grey, measured at 0.36 of full scale
    against 0.42 for a running frame — so no brightness test can find it. What it lacks is
    STRUCTURE: no tiles, no separators, so its per-ROI contrasts bear no relation to the
    reference's characteristic bright-column-A / dark-column-E pattern.

    Correlation is scale-free, so it cannot be fooled by the array simply being dim:
    measured on 13 Aug, noise frames score -0.08 to -0.22 while both warm-up (0.891) and
    running (0.897) frames score the same high value despite differing threefold in level.
    That is exactly the separation needed, and neither the frame mean (0.36 vs 0.42) nor
    the array level (0.22 vs 0.27) provides it."""
    if contrast_matrix.size == 0:
        return None
    ref = np.asarray(ref_contrast, dtype=np.float64)
    use = ref > 0
    if use.sum() < 4:      # too few anchors for a meaningful correlation
        return None
    r = ref[use]
    r = r - r.mean()
    r_norm = float(np.sqrt((r * r).sum()))
    if r_norm <= 0:
        return None
    m = contrast_matrix[:, use].astype(np.float64)
    m = m - m.mean(axis=1, keepdims=True)
    m_norm = np.sqrt((m * m).sum(axis=1))
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = (m @ r) / (m_norm * r_norm)
    return np.nan_to_num(corr, nan=0.0)


def _detect_nodata(frame_means: list, synth: list, nodata_frac: float,
                   excl: "list | None" = None) -> tuple:
    """(frame_nodata, cutoff) — which frames carry no real array data.

    The test is RELATIVE to how bright a normal frame is in this very window: a blank
    frame is only noise and pedestal (a few percent of full scale) while a running array
    sits near half scale, so `frame_mean < nodata_frac × median(frame_mean)` separates
    them cleanly on any camera and any exposure. It used to be an absolute constant,
    which silently never matched — the only trips ever found were the ones inferred from
    gaps in the image stream, so a trip where the archiver kept writing empty frames was
    invisible. `median` is taken over the real (non-synthetic) frames only, and is robust
    as long as the array is up for more than half the window; a window that is mostly
    blank falls back to the largest observed frame instead.

    A relative test cannot, even in principle, describe a window in which the array was
    never up: with nothing bright to compare against, every frame looks "normal" and the
    result would be 40 pulsers reported dead rather than an honest "no data here". That
    one case is caught by an absolute check on the window as a whole.

    Gate-excluded frames are left out of "normal" as well. They are frames of a dark
    array by definition, so counting them would drag the median down towards the blank
    level and raise the bar for what still counts as real data — a night-heavy window
    would end up judging its own daytime frames against the night."""
    ex = list(excl) if excl else [False] * len(frame_means)
    real = [fm for fm, sy, e in zip(frame_means, synth, ex) if not sy and not e]
    if not real:
        return [True] * len(frame_means), 0.0
    peak = float(max(real))
    if peak < _BLANK_ABS:
        # The array was never up anywhere in this window.
        return [True] * len(frame_means), _BLANK_ABS
    normal = float(np.median(real))
    # A mostly-blank window drags the median down onto the blank level itself; the peak
    # is then the only honest estimate of what "array up" looked like.
    if normal < nodata_frac * peak:
        normal = peak
    cutoff = nodata_frac * normal
    return ([bool(sy) or bool(e) or (fm < cutoff)
             for fm, sy, e in zip(frame_means, synth, ex)], cutoff)


def _detect_warmup(signal_matrix: np.ndarray, ref_values: list, frame_nodata: list,
                   warm_ref: list, params: "AnalysisParams") -> tuple:
    """(frame_warmup, array_level, windows, level_used, source).

    Warm-up is an ARRAY-level condition, not a per-pulser one: the whole field sits a few
    percent below its normal level and simply stays there — it never ramps. So it cannot
    be found from any single pulser; it is read off the median of all pulsers relative to
    the all-alive reference, which is unaffected by one or two dark pulsers.

    The warm-up level itself comes from a dedicated warm-up reference image (the same
    boxes measured on a frame taken while the array was warming), or from a manual
    override when the two references turn out not to separate cleanly."""
    n = len(frame_nodata)
    ref = np.asarray(ref_values, dtype=np.float64)
    usable = ref > 0
    if signal_matrix.size == 0 or not usable.any():
        return [False] * n, [0.0] * n, [], 0.0, "none", None

    # Per-frame array level: 1.0 while running normally, lower while warming.
    lvl = np.median(signal_matrix[:, usable].astype(np.float64) / ref[usable][None, :],
                    axis=1)
    array_level = [float(v) for v in lvl]

    # Per-pulser warm ratio. Warming does not dim every pulser by the same amount — the
    # spread across an array is 0.24 to 0.40 — so each one is corrected by its own ratio,
    # not by the array median. Pulsers with no usable warm reference fall back to it.
    warm = np.asarray(warm_ref, dtype=np.float64)
    both = usable & (warm > 0)
    if params.warm_level > 0:
        w, source = float(params.warm_level), "manual"
        per_roi = np.full(len(ref), w, dtype=np.float64)
        if both.any():
            # Keep the measured shape, rescaled so its median matches the manual level.
            meas = warm[both] / ref[both]
            med = float(np.median(meas))
            if med > 0:
                per_roi[both] = meas * (w / med)
    else:
        if not both.any():
            return [False] * n, array_level, [], 0.0, "none", None
        meas = warm[both] / ref[both]
        w, source = float(np.median(meas)), "reference"
        per_roi = np.full(len(ref), w, dtype=np.float64)
        per_roi[both] = meas
    # A warm-up reference that is not actually dimmer tells us nothing.
    if not (0.0 < w < 1.0):
        return [False] * n, array_level, [], w, source, None
    # A per-pulser ratio outside a sane band is a bad measurement, not a real one.
    per_roi = np.clip(per_roi, 0.05, 0.99)

    cut = (1.0 + w) / 2.0          # midpoint between warm and normal
    raw = [(not nd) and (0.0 < lvl[i] < cut) for i, nd in enumerate(frame_nodata)]
    # Require a sustained run so a single dim frame is not a "warm-up window".
    windows: list = []
    frame_warmup = [False] * n
    i = 0
    while i < n:
        if raw[i]:
            j = i
            while j < n and raw[j]:
                j += 1
            if (j - i) >= params.warm_min_frames:
                for k in range(i, j):
                    frame_warmup[k] = True
                # End index is the first frame that is NOT warming (exclusive), so the
                # last warm frame's own interval is included — the array was still warm
                # for it. Same convention as `_classify_nodata_runs`, so the durations add up.
                windows.append((i, min(j, n - 1)))
            i = j
        else:
            i += 1
    return frame_warmup, array_level, windows, w, source, per_roi


def _native_cadence_ns(times: list, synth: list) -> int:
    """The camera's own frame spacing, not the reading plan's. With energy-guided reading
    most of the frames read are 5 s apart and only the bursts around the dips are at the
    camera's 0.3 s, so the median would say 5 s; the 10th percentile of the spacing finds
    the bursts. A day from the old 0.2 Hz archive has no bursts and reads 5 s."""
    t = np.asarray([x for x, sy in zip(times, synth) if not sy], dtype=np.int64)
    if t.size < 3:
        return 0
    d = np.diff(t)
    d = d[d > 0]
    return int(np.percentile(d, 10)) if d.size else 0


def _find_culprits(stop_start: int, k: int, score_sid: np.ndarray, score: np.ndarray,
                   valid: np.ndarray, t_arr: np.ndarray, skip: set,
                   blink_below: float, min_dark_ns: int) -> list:
    """[(roi index, first frame of its final dark run, dark frames)] — the pulsers that
    took the array down at a stop ending stretch `k`.

    The operator's rule: a pulser that was lit earlier in the stretch, then went dark and
    stayed dark right up to the stop for at least `min_dark_ns`. Looked for in every frame
    of the stretch that carries a score — the ramp before the voltage falls included, since
    that is exactly where a pulser going out shows before the converter trips. A frame in
    which the whole array changed at once (`valid` False) is skipped: that is the array
    going down, not a pulser. Pulsers dark already when the stretch began (`skip`) never
    count — a pulser dead since the morning did not take down the afternoon."""
    idx = np.where((score_sid == k) & valid & (t_arr <= stop_start))[0]
    if idx.size == 0:
        return []
    out: list = []
    for r in range(score.shape[1]):
        if r in skip:
            continue
        col = score[idx, r]
        if not (col[-1] < blink_below):
            continue
        j = col.size - 1
        while j >= 0 and col[j] < blink_below:
            j -= 1
        if j < 0:
            continue          # never seen lit in this stretch
        first = int(idx[j + 1])
        # Dark since some moment after the last frame that showed it lit. Measured from that
        # frame: at one frame per 5 s, six dark frames are the operator's "30 s".
        if stop_start - int(t_arr[idx[j]]) < min_dark_ns:
            continue
        out.append((r, first, int(col.size - 1 - j)))
    out.sort(key=lambda c: -c[2])
    return out


def _array_stops(rois: list, times: list, synth: list, bounds: list, judged: list,
                 state_matrix: np.ndarray, score: np.ndarray, score_sid: np.ndarray,
                 valid: np.ndarray, frame_judge: list, dark_from_start: list,
                 excluded: "_SpanSet | None", params: "AnalysisParams",
                 window_end_ns: "int | None" = None) -> tuple:
    """(trips, frame_trip, frame_off, frame_restart, hints, forced) — every array stop.

    A stop runs from the end of one stretch at full power to the start of the next: with
    the dump bus that is the voltage falling below full power and coming back to it, warm-up
    included, which is how the operator measures how long the array was down. After the
    last stretch there is a stop that never ends when there was time left in the window
    after it (`window_end_ns`, else the last frame); a window that simply ends while the
    array runs has no stop at its end.

    What each stop was is decided in the order the operator decides it (see the GAP_*
    table): first whether a pulser took the array down (`_find_culprits`) and what became
    of it after the restarts, then how long it lasted. A culprit's fate is read forward:
    the first time it is seen properly lit again (longer than a flicker) — after a pause of
    `replaced_min` or more it is a new pulser and the old one died; after a shorter one it
    revived. Never lit again although the array was restarted: dead. No restart in the
    window at all: nothing was shown about it, so the stop stays a pulser trip.

    Returns, besides the stops and their frame masks, `hints` — {roi: {frame: kind}} that
    tells `_compute_pulser_stats` what the culprit's dark run was — and `forced`, the
    culprit frames in the unjudged ramp that must read OFF so that dark run exists at all."""
    n = len(times)
    n_rois = state_matrix.shape[0] if state_matrix.size else len(rois)
    t_arr = np.asarray(times, dtype=np.int64)
    frame_trip = [False] * n
    frame_off = [False] * n
    frame_restart = [False] * n
    hints: dict = {}
    forced: list = []
    trips: list = []
    if n == 0 or not bounds:
        t0, t1 = (int(t_arr[0]), int(t_arr[-1])) if n else (0, 0)
        seen = (t1 - t0) - (excluded.overlap_ns(t0, t1) if excluded is not None else 0)
        if n and not any(frame_judge) and seen > 0:
            # Nothing at full power anywhere we looked: one switched-off stretch.
            trips.append(ArrayTrip(start_ns=t0, end_ns=t1, kind=GAP_OFF, back=False))
            frame_off = [True] * n
        return trips, frame_trip, frame_off, frame_restart, hints, forced

    cad = _native_cadence_ns(times, synth)
    fast = 0 < cad <= _NS_PER_S
    min_dark = int((params.culprit_fast_s if fast else params.culprit_slow_s) * _NS_PER_S)
    manual_ns = int(params.manual_max_min * 60 * _NS_PER_S)
    outage_ns = int(params.outage_max_min * 60 * _NS_PER_S)
    replaced_ns = int(params.replaced_min * 60 * _NS_PER_S)
    drop_max = max(1, int(params.dropout_frames))
    # Consecutive ON frames from each index on — "properly lit" means more than a flicker.
    on_run = np.zeros((n_rois, n + 1), dtype=np.int32)
    for i in range(n - 1, -1, -1):
        on_run[:, i] = np.where(state_matrix[:, i] == STATE_ON, on_run[:, i + 1] + 1, 0)

    stops = []
    for k, (a, b) in enumerate(bounds):
        nxt = bounds[k + 1][0] if k + 1 < len(bounds) else None
        stops.append((k, int(b), None if nxt is None else int(nxt)))

    gap_ns = int(max(0.0, params.trip_min_s) * _NS_PER_S)
    for k, s0, s1 in stops:
        s_end = s1 if s1 is not None else max(s0, int(t_arr[-1]), int(window_end_ns or 0))
        if excluded is not None and s1 is None:
            s_end = max(s0, excluded.clip_back(s_end))
        # The part of the stop anybody was looking at: up to where gated-out time begins.
        seen_end = s_end
        if excluded is not None:
            for ea, _eb in excluded.spans:
                if s0 <= ea < s_end:
                    seen_end = ea
                    break
        if s1 is None and s_end - s0 <= gap_ns:
            continue          # the window just ended while the array ran
        cul = []
        if k < len(judged) and judged[k]:
            skip = set(dark_from_start[k]) if k < len(dark_from_start) else set()
            cul = _find_culprits(s0, k, score_sid, score, valid, t_arr, skip,
                                 params.blink_below, min_dark)
        fates: dict = {}
        for r, first, _nd in cul:
            name = rois[r].name if r < len(rois) else str(r)
            if s1 is None:
                fates[name] = "not restarted"
                continue
            i0 = int(np.searchsorted(t_arr, s1, side="left"))
            lit = np.where(on_run[r, i0:n] > drop_max)[0]
            if lit.size == 0:
                fates[name] = "dead"
                continue
            t_lit = int(t_arr[i0 + int(lit[0])])
            before = [(x0, x1) for _k, x0, x1 in stops if x1 is not None and x1 <= t_lit]
            pause = (before[-1][1] - before[-1][0]) if before else 0
            fates[name] = "replaced" if pause >= replaced_ns else "revived"
        dur = (s1 - s0) if s1 is not None else None
        ex_overlap = seen_end < s_end
        if ex_overlap and not cul and seen_end - s0 <= gap_ns:
            # The gate closed as the array stopped (the night, the key turned off): nobody
            # was looking, so there is no stop to report — only a new start afterwards.
            continue
        if cul:
            if any(f in ("dead", "replaced") for f in fates.values()):
                kind = GAP_DEAD
            elif dur is None or dur <= outage_ns:
                kind = GAP_TRIP
            else:
                kind = GAP_BIGGER
        elif dur is None or ex_overlap:
            kind = GAP_OFF
        elif dur <= manual_ns:
            kind = GAP_MANUAL
        elif dur <= outage_ns:
            kind = GAP_UNKNOWN
        else:
            kind = GAP_OFF
        trip = ArrayTrip(start_ns=s0, end_ns=s_end if cul else seen_end, kind=kind,
                         back=s1 is not None and not (ex_overlap and not cul),
                         caused_by=[(rois[r].name if r < len(rois) else str(r), nd)
                                    for r, _f, nd in cul],
                         fates=fates)
        trips.append(trip)

        for r, first, _nd in cul:
            fate = fates[rois[r].name if r < len(rois) else str(r)]
            hints.setdefault(r, {})[first] = (KIND_DEAD if fate in ("dead", "replaced")
                                              else KIND_TRIP)
            # The ramp before the stop is not judged, so the culprit's dark frames there
            # carry no state — give them one, or the dark run that took the array down
            # would not exist. The lit frame before it likewise, so the run starts here.
            for i in range(first, n):
                if t_arr[i] > s0:
                    break
                if state_matrix[r, i] == STATE_NODATA and valid[i] \
                        and score[i, r] < params.blink_below:
                    forced.append((r, i, STATE_OFF))
            if first > 0 and state_matrix[r, first - 1] == STATE_NODATA and valid[first - 1]:
                forced.append((r, first - 1, STATE_ON))

        hi = s1 if s1 is not None else s_end + 1
        inside = np.where((t_arr > s0) & (t_arr < hi))[0]
        for i in inside:
            if trip.is_outage:
                frame_trip[i] = True
            elif kind == GAP_OFF:
                frame_off[i] = True
        if trip.is_outage and s1 is not None:
            i = int(np.searchsorted(t_arr, s1, side="left"))
            while i < n and not frame_judge[i]:
                i += 1
            if i < n:
                frame_restart[i] = True
                trip.restarted = True
    return trips, frame_trip, frame_off, frame_restart, hints, forced


def _debounce_states(raw: list, debounce: int) -> None:
    """Flip too-short OFF runs (among data frames) back to ON, in place."""
    if debounce <= 1:
        return
    n = len(raw)
    data_idx = [i for i in range(n) if raw[i] != STATE_NODATA]
    k, m = 0, len(data_idx)
    while k < m:
        if raw[data_idx[k]] == STATE_OFF:
            j = k
            while j < m and raw[data_idx[j]] == STATE_OFF:
                j += 1
            if (j - k) < debounce:
                for t in range(k, j):
                    raw[data_idx[t]] = STATE_ON
            k = j
        else:
            k += 1


def _compute_pulser_stats(name: str, st: list, times: list,
                          params: "AnalysisParams",
                          judge: "list | None" = None,
                          frame_trip: "list | None" = None,
                          frame_off: "list | None" = None,
                          frame_restart: "list | None" = None,
                          array_up_ns: int = 0,
                          excl: "list | None" = None,
                          excluded: "_SpanSet | None" = None,
                          score: "np.ndarray | None" = None,
                          hints: "dict | None" = None) -> PulserStats:
    """Per-pulser statistics + classified events from a cleaned state sequence `st`.

    Every dark run is classified into exactly one of four kinds, in this order of priority:

      DEAD     the operator's two routes, and only these two:
                 - it stayed dark through `dead_restarts` array recoveries. Somebody tried
                   to bring it back and it did not come back.
                 - it never lit at all — dark from the first frame we were allowed to judge,
                   and still dark after `dead_confirm_min` minutes of array-up time. The
                   minutes are only a confirmation that we watched long enough to say so;
                   they are NOT a second way of dying.
               A pulser that ran, then went dark and stayed dark while nobody ever restarted
               the array is a FAULT, however long it lasts. Nothing was demonstrated about
               it: it was never asked to come back. This is the operator's own definition
               and the reason the time limit no longer stands on its own.
      TRIP     it was dark right up to an array stop and took the array with it, and lit
               again afterwards — decided by the stop (`_array_stops`, via `hints`).
      DROPOUT  dark for no more than `dropout_frames` frames. A flicker.
      FAULT    anything else: dark for a while, the array kept running, and it came back.

    THE VERDICT IS REACHED BY LOOKING FORWARD, AND IS WRITTEN BACK ONTO AN EVENT THAT BEGAN
    EARLIER. Neither of the two decisions can be made when a dark run starts. Whether the
    array fell is only known a few frames later; whether the pulser is recoverable is only
    known after the operator has finished trying, and they routinely fire several recoveries
    back to back — a pulser still dark after the first one is not yet dead. The old code
    fixed the kind at the moment a run closed and called any run still open at the last
    sample DEAD, which is why the array being switched off at 17:07 killed eleven healthy
    pulsers: each of them read OFF for about a second during the power-down ramp.

    DEATH CAN END. A dead pulser gets replaced, which takes an hour or two, and then it
    lights again. Such a run keeps `KIND_DEAD` and gets a `recovery_ns`; the pulser stays in
    the day's dead count, because the count answers "how many died today", not "how many are
    broken right now". `is_dead` therefore does not imply still dark — `dead_until_ns` says.

    Gate-excluded frames (`excl`) and DIODES-OFF frames both act as a HARD BREAK, not as
    no-data. Neither is something a pulser "rode through": nothing may be carried across, so
    an open dark run is closed at the last frame really observed and runs restart afterwards.
    Without the break a pulser dark at 20:59 and lit at 07:05 would be credited with a
    ten-hour outage nobody watched, and — the case that actually bit — a pulser dark for the
    last second of the day would be credited with dying, because the diodes going off looks
    from the inside exactly like never coming back.

    Uptime is ON time over `array_up_ns`, the camera's own full-power time, so all forty
    pulsers share one denominator. The old per-pulser frame ratio gave 100 % to a pulser that
    ran 7.5 hours and to one that ran 5.75, because each got its own denominator.

    A DEATH CARRIES ACROSS A BREAK. A dead pulser is still dead after the lunch-time
    switch-off, and a stretch after it can be shorter than `dead_confirm_min` — judged on its
    own, the same dead pulser came out as a fault in every short afternoon stretch. So a dark
    run that opens while the pulser's last death is still unanswered (it has not lit since)
    is the same death: it extends that event instead of starting a new one.

    `score` (the pulser's score per frame) only records how deep each dark run went, which
    is what tells a full blink from a partial one."""
    n = len(st)
    ps = PulserStats(name=name)
    if n == 0:
        return ps
    def _mask(src, default=False) -> list:
        m = list(src) if src else [default] * n
        return (m + [default] * (n - len(m)))[:n] if len(m) != n else m

    ex = _mask(excl)
    jud = _mask(judge, True) if judge else [s != STATE_NODATA for s in st]
    off = _mask(frame_off)
    rst = _mask(frame_restart)
    # Both kinds of break mean "nothing may be carried across this". Gate-excluded time is
    # time nobody was looking; diodes-off time is time nobody was running the array. In
    # neither case has a dark pulser been shown to be anything.
    brk = [a or b for a, b in zip(ex, off)]
    seen = [i for i in range(n) if not ex[i]]
    ps.excluded_frames = n - len(seen)
    if not seen:
        # The gate removed the whole window for this camera. Nothing was observed, so
        # there is nothing to say about any pulser — reporting 0 % uptime here would be
        # an assertion about time we deliberately refused to look at.
        return ps
    i_first, i_last = seen[0], seen[-1]
    t_first, t_last = times[i_first], times[i_last]

    ps.frames = len(seen)
    ps.on_frames = sum(1 for i in seen if st[i] == STATE_ON)
    ps.off_frames = sum(1 for i in seen if st[i] == STATE_OFF)
    ps.nodata_frames = sum(1 for i in seen if st[i] == STATE_NODATA)
    ps.span_ns = max(0, (t_last - t_first)
                     - (excluded.overlap_ns(t_first, t_last) if excluded else 0))

    # Time-weighted ON / dark time over the full-power frames only, and the running total of
    # that time so a dark run can ask "how long has the array been up since I went out?".
    # Frame counts cannot answer that: the frames of a trip are not time the array was up,
    # and at 3.3 Hz a run of three of them is under a second.
    up_cum = [0] * (n + 1)
    on_t = dark_t = 0
    for i in range(n):
        dt = (times[i + 1] - times[i]) if i + 1 < n else 0
        if jud[i]:
            up_cum[i + 1] = up_cum[i] + dt
            if st[i] == STATE_ON:
                on_t += dt
            elif st[i] == STATE_OFF:
                dark_t += dt
        else:
            up_cum[i + 1] = up_cum[i]
    ps.on_time_ns = on_t
    ps.dark_time_ns = dark_t
    denom = array_up_ns if array_up_ns > 0 else up_cum[n]
    ps.uptime_pct = (100.0 * on_t / denom) if denom > 0 else 0.0

    # Running count of array recoveries, so a dark run can ask how many it has survived.
    rs_cum = [0] * (n + 1)
    for i in range(n):
        rs_cum[i + 1] = rs_cum[i] + (1 if rst[i] else 0)

    # Prefix sum of no-data interval time so a run can subtract the gaps inside it.
    nd_cum = [0] * n
    acc = 0
    for i in range(n - 1):
        dt = times[i + 1] - times[i]
        if st[i] == STATE_NODATA:
            acc += dt
        nd_cum[i + 1] = acc

    def nd_between(i0: int, i1: int) -> int:
        return nd_cum[i1] - nd_cum[i0]

    dead_ns = int(max(0.0, params.dead_confirm_min) * 60 * 1_000_000_000)
    dead_rs = max(1, int(params.dead_restarts))
    hint_at = dict(hints or {})
    drop_max = max(1, int(params.dropout_frames))

    # How many judged frames the pulser is ON for, starting at each index. Needed to answer
    # "has it really come back?" — see the sustained-return rule in the ON branch below.
    on_run = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        on_run[i] = (on_run[i + 1] + 1) if st[i] == STATE_ON else 0

    longest = longest_active = 0
    events: list = []
    first_drop = last_drop = None
    run_start = None            # start index of the current run (broken only by a real drop)

    # Dark-run accumulator. Unlike the old version this opens even on LEADING dark, before
    # the pulser has ever been seen lit: with warm-up no longer judged, the first full-power
    # frame is a real measurement, so a pulser dark there is really dark and is exactly the
    # case the day's two genuinely dead pulsers fall into.
    in_ep = False
    ep_start = None
    ep_dark = 0
    ep_nodata = 0
    run_start_before_ep = None   # so a flicker can hand the run back untouched
    # Has this pulser ever been seen properly lit? "Properly" means the same sustained
    # stretch a recovery has to produce (> drop_max frames) — one bright frame inside hours
    # of dark is noise, not evidence that the pulser ran. This is what separates the two
    # ways of being dead: a pulser dark from the first judged frame NEVER ran that day and
    # is dead on its own; one that did run has to fail actual recoveries to earn the word.
    ever_lit = False
    ep_lit_before = False        # value of `ever_lit` when the open dark run started
    ep_min = float("nan")        # lowest score in the open dark run
    sc = np.full(n, np.nan)
    if score is not None:
        s_in = np.asarray(score, dtype=np.float64)[:n]
        sc[:len(s_in)] = s_in
    full_below = float(getattr(params, "full_below", 0.55))
    # The last DEAD event while the pulser has not lit since — a later dark run is the same
    # death (see "A DEATH CARRIES ACROSS A BREAK" above). `ep_carry` is its value when the
    # open dark run started.
    dead_open = None
    ep_carry = None

    def _close_run(end_idx: int) -> None:
        """Record the run ending at `end_idx` as a candidate longest-run."""
        nonlocal longest, longest_active
        if run_start is None or end_idx <= run_start:
            return
        wall = times[end_idx] - times[run_start]
        longest = max(longest, wall)
        longest_active = max(longest_active, wall - nd_between(run_start, end_idx))

    def _verdict(end_idx: int) -> tuple:
        """(kind, array_fell, restarts) for the run from `ep_start` to `end_idx`.

        This is the look-forward: by the time it is called, everything that happened after
        the pulser went dark is known, and the answer is stamped onto an event that started
        earlier. `up_dark` is the time the ARRAY spent running while this pulser was out —
        not wall-clock, so an outage or a switch-off in the middle does not age it.

        Whether this run took the array down — and so whether it is a trip or a death — is
        not decided here but by the stop it ended in (`_array_stops`), which has looked at
        the frames right up to the voltage falling and at what became of the pulser after
        the restarts; `hints` carries that verdict in."""
        up_dark = up_cum[min(end_idx, n)] - up_cum[ep_start]
        restarts = rs_cum[min(end_idx, n)] - rs_cum[ep_start]
        hint = hint_at.get(ep_start)
        fell = hint is not None
        if hint is not None:
            return hint, fell, restarts
        # Still the same death, carried across a break it never lit through.
        if ep_carry is not None:
            return KIND_DEAD, fell, restarts
        # Route one: somebody tried to bring it back, several times, and it did not come.
        if restarts >= dead_rs:
            return KIND_DEAD, fell, restarts
        # Route two: it never lit in the first place. `dead_confirm_min` is the proof that
        # we watched the array run long enough to be sure, not a clock that kills a pulser
        # which ran this morning — the array being up while nobody restarts it demonstrates
        # nothing about a pulser, and letting the minutes stand alone reported a harmless
        # long fault as a death.
        if not ep_lit_before and up_dark >= dead_ns:
            return KIND_DEAD, fell, restarts
        if ep_dark <= drop_max:
            return KIND_DROPOUT, fell, restarts
        return KIND_FAULT, fell, restarts

    def _close(end_idx: int, recovered: bool) -> str:
        """Emit the event for the run ending at `end_idx`.

        `recovered` distinguishes "the pulser lit again" from "we stopped being able to
        see". A run closed at a boundary still gets its honest kind — including DEAD if it
        had already outlasted the recoveries and the clock before the lights went out."""
        nonlocal first_drop, last_drop, dead_open
        kind, fell, restarts = _verdict(end_idx)
        if recovered:
            ended = "replaced" if kind == KIND_DEAD else "recovered"
        else:
            ended = "end of window"
        rec_ns = times[min(end_idx, n - 1)] if recovered else None
        if kind == KIND_DEAD and ep_carry is not None:
            # The same death, seen again after a break: extend it rather than add a second.
            ev = ep_carry
            ev.dark_frames += ep_dark
            ev.nodata_frames += ep_nodata
            ev.restarts += restarts
            ev.array_fell = ev.array_fell or fell
            ev.recovery_ns, ev.ended_by = rec_ns, ended
            if not np.isnan(ep_min):
                ev.min_score = (ep_min if np.isnan(ev.min_score)
                                else min(ev.min_score, ep_min))
            dead_open = None if recovered else ev
            return kind
        partial = (kind != KIND_DEAD and not np.isnan(ep_min) and ep_min >= full_below)
        ev = DropoutEvent(
            start_ns=times[ep_start], recovery_ns=rec_ns,
            dark_frames=ep_dark, nodata_frames=ep_nodata, kind=kind,
            array_fell=fell, restarts=restarts, ended_by=ended,
            min_score=ep_min, partial=partial)
        events.append(ev)
        if kind == KIND_DEAD:
            dead_open = None if recovered else ev
        # A flicker must not become the pulser's "first/last outage", or one dim frame
        # would rewrite the outage history.
        if kind != KIND_DROPOUT:
            if first_drop is None:
                first_drop = times[ep_start]
            last_drop = times[ep_start]
        return kind

    prev_seen = None            # last observed, non-broken index visited
    for i, s in enumerate(st):
        if brk[i]:
            # A hard break. Wind up whatever was open at the last frame actually observed,
            # then start from nothing — the run and any open dark run are unobservable now.
            if prev_seen is not None:
                if in_ep:
                    _close(prev_seen, recovered=False)
                    in_ep = False
                else:
                    _close_run(prev_seen)
            run_start = None
            continue
        prev_seen = i
        if s == STATE_ON:
            # A DEAD pulser has to come back CONVINCINGLY. Coming back means it was
            # physically replaced, and a replacement then stays lit; a single frame reading ON
            # in the middle of seven hours of dark is noise. Without this, one such frame in a
            # day's record split C5 into "dead 09:13 → 10:40 (replaced)" plus a second death
            # at 10:40 — two events, a fictional repair, and a death dated three hours late.
            if in_ep and on_run[i] <= drop_max and _verdict(i)[0] == KIND_DEAD:
                if run_start is None:
                    run_start = i
                continue
            # Same bar as the sustained return above, for the same reason: this is the
            # record that the pulser genuinely ran, and a single bright frame must not be
            # able to write it.
            if on_run[i] > drop_max:
                ever_lit = True
                dead_open = None
            if run_start is None:
                run_start = i
            if in_ep:
                # A flicker does not end the run — a pulser that dimmed for one frame and
                # came straight back has not stopped running.
                if _close(i, recovered=True) == KIND_DROPOUT:
                    run_start = run_start_before_ep
                else:
                    run_start = i
                in_ep = False
        elif s == STATE_OFF:
            if not in_ep:
                # a drop ends the current run — record its length for longest-run
                _close_run(i)
                in_ep = True
                ep_start = i
                run_start_before_ep = run_start
                ep_lit_before = ever_lit
                ep_carry = dead_open
                ep_dark = ep_nodata = 0
                ep_min = float("nan")
            ep_dark += 1
            if not np.isnan(sc[i]) and (np.isnan(ep_min) or sc[i] < ep_min):
                ep_min = float(sc[i])
        else:  # STATE_NODATA — no data; counts only if a dark run is already open
            if in_ep:
                ep_nodata += 1

    if in_ep:
        # Still dark at the last observed frame. Whether that is death is decided by the
        # same look-forward as any other run — NOT by the fact that the samples ran out.
        # This is the single change that stops the array being switched off for the evening
        # from being recorded as eleven pulsers dying at 17:07.
        _close(i_last, recovered=False)
        in_ep = False
    else:
        _close_run(i_last)

    # Death is a property of the pulser's history, read back off the events. A pulser can
    # have several dead runs in a day if it was replaced and the replacement also failed;
    # the first one is when it died, the last one says whether it is dark right now.
    dead_evs = [ev for ev in events if ev.kind == KIND_DEAD]
    if dead_evs:
        ps.is_dead = True
        ps.dead_since_ns = dead_evs[0].start_ns
        ps.dead_until_ns = dead_evs[-1].recovery_ns
        ps.death_ns = dead_evs[0].start_ns

    ps.dropouts = sum(1 for ev in events if ev.kind == KIND_DROPOUT)
    ps.faults = sum(1 for ev in events if ev.kind == KIND_FAULT)
    ps.trips = sum(1 for ev in events if ev.kind == KIND_TRIP)
    ps.partial_blinks = sum(1 for ev in events if ev.is_blink and ev.partial)
    ps.longest_run_ns = longest
    ps.longest_run_active_ns = longest_active
    # Only a never-recovered outage can still straddle excluded time (it runs to the end
    # of the window by definition); every other event was cut at the gate boundary.
    down = 0
    for ev in events:
        stop = ev.recovery_ns if ev.recovery_ns is not None else t_last
        d = max(0, stop - ev.start_ns)
        if excluded is not None:
            d -= excluded.overlap_ns(ev.start_ns, stop)
        down += max(0, d)
    ps.total_down_ns = down
    ps.events = events
    ps.first_drop_ns = first_drop
    ps.last_drop_ns = last_drop
    # Current state = last real (non-no-data) state; a trailing trip keeps the prior state.
    ps.current_state = next((st[i] for i in reversed(seen) if st[i] != STATE_NODATA),
                            STATE_NODATA)
    return ps


def _runs_of(mask, min_len: int) -> list:
    """[(first, end)] index pairs of runs of True at least `min_len` long; `end` is the
    first index after the run (clamped to the last frame), the convention of
    `_detect_warmup`, so a window's last frame keeps its own interval."""
    n = len(mask)
    out: list = []
    i = 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= min_len:
                out.append((i, min(j, n - 1)))
            i = j
        else:
            i += 1
    return out


def _stretch_ids(times: list, judge: list, frame_warmup: list, frame_excluded: list,
                 power: "CamPower | None", params: "AnalysisParams",
                 trip_min_ns: int) -> tuple:
    """(stretch index per frame, -1 for none; [(start_ns, end_ns)] per stretch).

    With the archiver's view, a stretch is one full-power stretch of the dump bus, and its
    bounds are the voltage's own. Without it, a stretch is a run of judged frames that no
    warm-up, no gated-out time and no gap longer than an outage interrupts — the same thing
    seen from the images."""
    n = len(times)
    sid = np.full(n, -1, dtype=int)
    bounds: list = []
    t_arr = np.asarray(times, dtype=np.int64)
    if power is not None:
        e0 = int(params.edge_start_s * _NS_PER_S)
        e1 = int(params.edge_end_s * _NS_PER_S)
        for k, (a, b) in enumerate(power.judged_spans):
            i0 = int(np.searchsorted(t_arr, a, side="left"))
            i1 = int(np.searchsorted(t_arr, b, side="left"))
            sid[i0:i1] = k
            bounds.append((a - e0, b + e1))
        return sid, bounds
    k = -1
    last_t = None
    broke = True
    for i in range(n):
        if frame_warmup[i] or frame_excluded[i]:
            broke = True
            continue
        if not judge[i]:
            continue
        t = int(t_arr[i])
        if broke or last_t is None or t - last_t > trip_min_ns:
            k += 1
            bounds.append([t, t])
        sid[i] = k
        bounds[k][1] = t
        last_t = t
        broke = False
    return sid, [(a, b) for a, b in bounds]


def _rolling_median(v: np.ndarray, k: int = 15) -> np.ndarray:
    """Median of the 2k+1 values around each one, NaNs ignored (an all-NaN window gives
    NaN, silently)."""
    import warnings as _warnings   # the name `warnings` is a local list in analyze_camera
    if v.size == 0:
        return v
    pad = np.pad(v, k, mode="edge")
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1), axis=1)


def _stretch_scores(signal_matrix: np.ndarray, feb_score: np.ndarray, judge: list,
                    times: list, sid: np.ndarray, n_stretch: int, ref_values: list,
                    params: "AnalysisParams", score_sid: "np.ndarray | None" = None,
                    scorable: "np.ndarray | None" = None) -> tuple:
    """(score_matrix, frame_change, info per stretch) — the score every state is decided on.

    The operator spots a blink by comparing a frame with how the array looked when the
    run began: the difference shows exactly which pulser went out. This does the same with
    numbers. Per stretch:

      reference  — each pulser's median signal over the stretch's first `ref_len_s` of
                   judged frames;
      score      — the frame's signal over that reference, divided by the frame's array
                   level (the median of that ratio over the lit pulsers), so the whole
                   array brightening or dimming together cancels and only one pulser
                   moving against the rest remains.

    Measured this way a healthy pulser stays within a few percent of 1.0 for hours, which
    is what makes a partial blink to 0.6 visible at all; against the February all-alive
    reference the same pulser wanders by tens of percent from day to day.

    A pulser already dark when the stretch began has no lit level of its own to compare
    with — its reference IS its dark level, and every frame would read 1.0. So a pulser
    whose reference sits below `dead_rel` of what February predicts (relative to the rest
    of the array) keeps the February score, which is what lets it be called dead.

    A frame where the array as a whole moved — its level `array_level_min` below the level
    around it, or `array_down_n` pulsers dark at once — is the array changing, not a pulser
    blinking, and is not judged (`frame_change`). A stretch with fewer than three judged
    frames has no reference and is not judged at all.

    `score_sid` / `scorable` widen which frames get a SCORE beyond the judged ones — the
    ramps either end of a full-power stretch. They are never judged, but the frames right
    before the voltage falls are where a pulser that takes the array down shows."""
    n = signal_matrix.shape[0] if signal_matrix.size else len(times)
    n_rois = len(ref_values)
    score = np.full((n, n_rois), np.nan)
    change = np.zeros(n, dtype=bool)
    infos: list = []
    if signal_matrix.size == 0:
        return score, change, [{"ref_frames": 0, "dark": [], "judged": False}
                               for _ in range(n_stretch)]
    feb = np.asarray(ref_values, dtype=np.float64)
    t_arr = np.asarray(times, dtype=np.int64)
    jud = np.asarray(judge, dtype=bool)
    ref_len = int(params.ref_len_s * _NS_PER_S)
    for k in range(n_stretch):
        info = {"ref_frames": 0, "dark": [], "judged": False}
        infos.append(info)
        idx = np.where((sid == k) & jud)[0]
        if idx.size < 3:
            continue
        ref_idx = idx[t_arr[idx] <= t_arr[idx[0]] + ref_len]
        if ref_idx.size < 3:
            ref_idx = idx[:3]
        if score_sid is not None and scorable is not None:
            # Judged frames plus the stretch's own ramps; all of them in time order.
            idx = np.union1d(idx, np.where((score_sid == k) & scorable)[0])
        sig = signal_matrix[idx].astype(np.float64)
        ref = np.median(signal_matrix[ref_idx].astype(np.float64), axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.where(feb > 0, ref / np.where(feb > 0, feb, 1.0), np.nan)
            med = np.nanmedian(rel) if np.isfinite(rel).any() else np.nan
            dark = (np.isfinite(rel) & (rel / med < params.dead_rel)
                    if np.isfinite(med) and med > 0 else np.zeros(n_rois, dtype=bool))
        alive = ~dark & (ref > 1e-9)
        if alive.sum() < 2:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = sig / np.where(ref > 1e-9, ref, np.nan)[None, :]
            lvl = np.nanmedian(ratio[:, alive], axis=1)
            s = ratio / np.where(lvl > 0, lvl, np.nan)[:, None]
        local = _rolling_median(lvl)
        with np.errstate(divide="ignore", invalid="ignore"):
            lvl_rel = lvl / np.where(local > 0, local, np.nan)
        down = np.sum(np.nan_to_num(s[:, alive], nan=1.0) < params.blink_below, axis=1)
        ch = (~(lvl_rel >= params.array_level_min) | ~(lvl >= 0.5)
              | (down >= params.array_down_n))
        score[idx] = np.where(alive[None, :], s, feb_score[idx])
        change[idx[ch]] = True
        info.update(ref_frames=int(ref_idx.size), dark=[int(r) for r in np.where(dark)[0]],
                    judged=True)
    return score, change, infos


def _summarise_stretches(rois: list, stats: list, times: list, judge: list,
                         sid: np.ndarray, bounds: list, infos: list,
                         power: "CamPower | None", low_power: list,
                         params: "AnalysisParams") -> tuple:
    """([StretchSummary], [(ts_ns, depth)] unexplained energy dips)."""
    n = len(times)
    t_arr = np.asarray(times, dtype=np.int64)
    dt = np.zeros(n, dtype=np.int64)
    if n > 1:
        dt[:-1] = np.diff(t_arr)
    jud = np.asarray(judge, dtype=bool)
    blinks = [(ev.start_ns, st.name, ev.partial)
              for st in stats for ev in st.events if ev.is_blink]
    blink_t = np.array(sorted(t for t, _n, _p in blinks), dtype=np.int64)
    w = int(params.dip_window_s * _NS_PER_S)
    pad = int(params.low_power_pad_s * _NS_PER_S)
    has_dips = power is not None and power.has_energy
    out: list = []
    unexplained: list = []
    for k, (a, b) in enumerate(bounds):
        info = infos[k] if k < len(infos) else {"ref_frames": 0, "dark": [], "judged": False}
        ss = StretchSummary(index=k + 1, start_ns=int(a), end_ns=int(b),
                            ref_frames=info["ref_frames"], judged=info["judged"])
        ss.judged_ns = int(dt[(sid == k) & jud].sum())
        lows = [(max(lo, a), min(hi, b)) for lo, hi in low_power if hi >= a and lo <= b]
        ss.low_power_n = len(lows)
        ss.low_power_ns = int(sum(max(0, hi - lo) for lo, hi in lows))
        ss.dark_from_start = [rois[r].name for r in info["dark"] if r < len(rois)]
        if not ss.judged:
            ss.note = "no frames to take a reference from — not judged"
            ss.unexplained = -1
            out.append(ss)
            continue
        for t, name, part in blinks:
            if a <= t <= b:
                if part:
                    ss.partial += 1
                else:
                    ss.blinks += 1
                ss.per_pulser[name] = ss.per_pulser.get(name, 0) + 1
        if has_dips:
            for t0, t1, depth in power.dips:
                if not (a <= t0 <= b) or depth >= params.unexplained_dev:
                    continue
                if any(lo - pad <= t0 <= hi + pad for lo, hi in low_power):
                    continue
                i0 = int(np.searchsorted(blink_t, t0 - w, side="left"))
                if i0 < blink_t.size and blink_t[i0] <= t1 + w:
                    continue
                ss.unexplained += 1
                unexplained.append((int(t0), float(depth)))
        else:
            ss.unexplained = -1
        out.append(ss)
    return out, unexplained


def analyze_camera(rois: list, sample_points: list,
                   params: "AnalysisParams",
                   excluded: "_SpanSet | None" = None,
                   gate_notes: "list | None" = None,
                   gate_hpe_failed: bool = False,
                   power: "CamPower | None" = None,
                   low_power: "list | None" = None,
                   gate_power_failed: bool = False,
                   window_ns: "tuple | None" = None) -> CameraAnalysis:
    """Turn raw brightness samples into adaptive ON/OFF/NODATA states, classified
    dropout/trip/dead events, per-pulser stats and array-level trips.

    - Time gaps in the image stream and dark whole-frames both become no-data (trips).
    - Each pulser is scored against its own level at the start of its stretch at full
      power (`_stretch_scores`): dark when the score is below `blink_below`.
    - OFF runs shorter than `debounce` data frames are treated as noise → ON.
    - `excluded` (see ACTIVE-TIME GATE) marks time nobody was running the laser. Those
      frames are never judged, never form or extend an array trip, and are subtracted
      from every duration and denominator. Pass None to analyse the raw window.
    - `power` (see FULL-POWER GATE) is the archiver's view of this array: only frames in
      its full-power stretches are judged, and `low_power` (every array's low-power
      stretches together) is left out of them. None = full power is read off the images.
    - `window_ns` (start, end) of the scan: with it, the array going off before the
      window ends is a stop even when no frame was read after it.
    """
    n_rois = len(rois)
    if not sample_points or n_rois == 0:
        return CameraAnalysis(stats=[], state_matrix=np.zeros((n_rois, 0), dtype=int),
                              frame_nodata=[], times_ns=[], trips=[], baselines=[],
                              excluded=excluded, gate_notes=list(gate_notes or []),
                              gate_hpe_failed=gate_hpe_failed)

    # Frames that do not show the array at all are removed from the series before
    # anything else happens — see `_pattern_correlation`. They are not measurements of a
    # dark array, they are not measurements at all, and the archiver writes them on a
    # separate, much faster schedule: on 13 Aug a 0.30 s noise stream ran alongside the
    # real 5.00 s acquisition and overlapped it for 13 minutes. Kept as samples they made
    # the array look like it was tripping and recovering between consecutive frames —
    # 1896 "trips" in one morning. Dropped, the gap they leave behind is picked up by the
    # normal trip-from-gap logic, which is what an absence of data actually means.
    dropped = 0
    never_up = False
    # The span the frames originally covered, kept so the stretches dropped at either END of
    # the window still get a marker — see `_augment_timeline`.
    edge_ns = ((sample_points[0].ts_ns, sample_points[-1].ts_ns) if sample_points
               else (None, None))
    if sample_points:
        raw_contrast = np.asarray(
            [(list(getattr(sp, "roi_contrasts", None) or sp.roi_means) + [0.0] * n_rois)[:n_rois]
             for sp in sample_points], dtype=np.float32)
        corr = _pattern_correlation(
            raw_contrast, [float(getattr(r, "ref_contrast", 0.0) or 0.0) for r in rois])
        if corr is not None:
            keep = [sp for sp, c in zip(sample_points, corr) if c >= params.pattern_min]
            dropped = len(sample_points) - len(keep)
            if keep:
                sample_points = keep
            else:
                # The array was never up anywhere in this window. Keep the frames so the
                # span is still described — as one long outage, not as an empty result.
                never_up, dropped = True, 0

    times, contrast_matrix, mean_matrix, frame_means, synth, frame_excluded, _cad = \
        _augment_timeline(sample_points, n_rois, params.gap_factor, excluded, edge_ns)
    n = len(times)
    frame_nodata, nodata_abs = _detect_nodata(frame_means, synth, params.nodata_frac,
                                              frame_excluded)
    if never_up:
        frame_nodata = [True] * n

    signal_matrix, ref_values, contrast_used, warnings = _pick_signal_and_ref(
        rois, mean_matrix, contrast_matrix, frame_nodata)

    if excluded:
        n_ex_real = sum(1 for e, sy in zip(frame_excluded, synth) if e and not sy)
        warnings.append(
            f"Active-time gate: {_fmt_dur(excluded.total_ns)} in {len(excluded)} "
            f"period(s) left out of the analysis "
            f"({', '.join(gate_notes or []) or 'no rules recorded'}). "
            f"{n_ex_real} scanned frame(s) fall inside it; nothing in those periods is "
            f"counted as a dropout, a trip or downtime.")
    if gate_hpe_failed:
        warnings.append(
            f"{HPE_CHANNEL} could not be read, so high-power gating was NOT applied — "
            f"periods with the key off are still being judged and may show up as trips "
            f"or dead pulsers. Check the archiver connection before trusting this result.")

    if dropped:
        warnings.append(
            f"{dropped} frame(s) did not show the pulser array (sensor noise only, no "
            f"tiles) and were excluded from the series; the periods they cover are "
            f"reported as array outages. This is normal — the archiver keeps writing "
            f"while the diodes are off.")

    if params.use_reference and not any(v > 0 for v in ref_values):
        warnings.append(
            "Reference baselines missing — open the ROI editor, load the all-alive "
            "reference image and press 'Capture reference from this image' (then OK) to "
            "store them. A pulser that is dead for the whole window cannot be flagged "
            "without a reference.")

    # Warm-up is found BEFORE anything is classified, because while the array warms it runs
    # at roughly a third of its normal level — far below the OFF threshold. Judged against
    # the normal level, every single pulser would read as dropped out.
    #
    # Nothing is classified INSIDE a warm-up stretch either (see `frame_judge` below). The
    # previous approach — divide each pulser's score by its own warm ratio and carry on
    # judging — looks reasonable and is quietly disastrous: the ratio is around 0.28, so the
    # correction multiplies the score by about 3.6, and a genuinely dead pulser comes out
    # looking alive. That fake ON is why the day's two dead pulsers were reported as having
    # died at a mid-morning trip instead of having been dark from the first frame. Warming is
    # hard to judge, so it is not judged: it contributes minutes, and nothing else.
    warm_ref = [float(getattr(roi, "warm_contrast", 0.0) or 0.0)
                if use_c else float(getattr(roi, "warm_brightness", 0.0) or 0.0)
                for roi, use_c in zip(rois, contrast_used)]
    # The per-ROI warm ratios are still measured (they are what identifies a warm stretch)
    # but deliberately no longer applied to the score — see the note above.
    (frame_warmup, array_level, warm_idx, warm_lvl, warm_src,
     _warm_per_roi) = _detect_warmup(
        signal_matrix, ref_values, frame_nodata, warm_ref, params)
    warmup_windows = [(times[a], times[b]) for a, b in warm_idx]
    if params.warm_level <= 0 and warm_src == "none" and any(v > 0 for v in ref_values):
        warnings.append(
            "No warm-up reference for this camera — warm-up time cannot be measured. "
            "Put a frame taken while the array was warming next to the app as "
            "'<camera prefix>-…-warmup.png', or set 'Warm-up level' by hand.")

    baselines = _learn_baselines(signal_matrix, ref_values, frame_nodata,
                                 params.use_reference, frame_warmup)

    # Every per-pulser judgement is made on these frames and no others: the array was
    # there, at full power, and we were allowed to look. One mask instead of a check
    # scattered through each decision, so there is exactly one place to be wrong.
    # `frame_diodes_off` is added below, once the no-data runs have been classified — it is
    # a subset of `frame_nodata`, so it cannot change what is judged, only what a dark run
    # is allowed to be carried across.
    # ── Full power ──────────────────────────────────────────────────────────────
    # With the archiver's view of this array, only frames inside a full-power stretch (less
    # its two ramps) and outside every low-power stretch are judged. The voltage also
    # overrules the image on warm-up: at full voltage the array is not warming, however dim
    # it looks — that is the low-power mode, which the energy reports.
    use_power = power is not None and not power.failed
    t_arr = np.asarray(times, dtype=np.int64)
    real = ~np.asarray(synth, dtype=bool)
    frame_ramp = np.zeros(n, dtype=bool)
    frame_low = np.zeros(n, dtype=bool)
    if use_power:
        judged_set = _SpanSet(power.judged_spans)
        full_set = _SpanSet(power.full_spans)
        pad = int(params.low_power_pad_s * _NS_PER_S)
        low_set = _SpanSet([(a - pad, b + pad + 1) for a, b in (low_power or [])])
        for i in range(n):
            if not real[i] or frame_nodata[i]:
                continue
            t = int(t_arr[i])
            if full_set.contains(t) and frame_warmup[i]:
                frame_warmup[i] = False
            if not judged_set.contains(t) and not frame_warmup[i]:
                frame_ramp[i] = True
            elif low_set.contains(t):
                frame_low[i] = True
        warm_idx = _runs_of(frame_warmup, params.warm_min_frames)
        warmup_windows = [(times[a], times[b]) for a, b in warm_idx]
        if power.note:
            warnings.append(f"Full power: {power.note}")
    elif gate_power_failed:
        warnings.append(
            "The full-power gate could not be read from the archiver, so every frame was "
            "read and full power is judged from the images alone (warm-up and outages "
            "excluded). Low-power stretches the images cannot tell from warm-up may be "
            "missed.")

    # Every per-pulser judgement is made on these frames and no others: the array was
    # there, at full power, and we were allowed to look. One mask instead of a check
    # scattered through each decision, so there is exactly one place to be wrong.
    # `frame_diodes_off` is added below, once the no-data runs have been classified — it is
    # a subset of `frame_nodata`, so it cannot change what is judged, only what a dark run
    # is allowed to be carried across.
    frame_judge = [not (nd or w or e or rp or lo)
                   for nd, w, e, rp, lo in zip(frame_nodata, frame_warmup, frame_excluded,
                                               frame_ramp, frame_low)]

    # The February score: each pulser against its own alive level, divided by a per-frame
    # global factor so a uniform exposure drift does not read as a wave of dark pulsers. It
    # is what a pulser with no lit level in its stretch is judged by (see below).
    base = np.asarray(baselines, dtype=np.float64)
    ratio = signal_matrix.astype(np.float64) / base[None, :] if signal_matrix.size \
        else np.zeros((n, n_rois))
    # 70th percentile (not median) so the drift estimate sits inside the *alive*
    # cluster of ratios even when a third of the pulsers are dark. Only residual drift
    # is left to absorb here, hence the tight clamp.
    g = np.percentile(ratio, 70, axis=1) if ratio.size else np.ones(n)
    g = np.clip(g, 0.5, 2.0)
    g[g <= 0] = 1.0
    feb_score = ratio / g[:, None]

    # The score the states are decided on: each pulser against its own level at the start
    # of its stretch. See `_stretch_scores`.
    trip_min_ns = int(max(0.0, params.trip_min_s) * _NS_PER_S)
    sid, bounds = _stretch_ids(times, frame_judge, frame_warmup, frame_excluded,
                               power if use_power else None, params, trip_min_ns)
    # Frames that get a score: the judged ones, plus — with the dump bus — the ramps either
    # end of each stretch, where a pulser taking the array down is seen (`_array_stops`).
    scorable = real & ~np.asarray(frame_nodata, dtype=bool)
    score_sid = sid.copy()
    if use_power:
        for k, (a, b) in enumerate(bounds):
            i0 = int(np.searchsorted(t_arr, a, side="left"))
            i1 = int(np.searchsorted(t_arr, b, side="right"))
            score_sid[i0:i1] = k
    score_matrix, frame_change, s_infos = _stretch_scores(
        signal_matrix, feb_score, frame_judge, times, sid, len(bounds), ref_values, params,
        score_sid=score_sid, scorable=scorable)
    unjudged_stretch = np.zeros(n, dtype=bool)
    for k, info in enumerate(s_infos):
        if not info["judged"]:
            unjudged_stretch |= (sid == k)
    frame_below_full = [bool(rp or lo or (ch and jd) or (uj and jd))
                        for rp, lo, ch, uj, jd in zip(frame_ramp, frame_low, frame_change,
                                                      unjudged_stretch, frame_judge)]
    frame_judge = [jd and not ch and not uj for jd, ch, uj
                   in zip(frame_judge, frame_change, unjudged_stretch)]
    score_valid = np.isfinite(score_matrix).any(axis=1) & ~frame_change

    state_matrix = np.full((n_rois, n), STATE_NODATA, dtype=int)
    jud_arr = np.asarray(frame_judge, dtype=bool)
    for r in range(n_rois):
        col = score_matrix[:, r]
        raw = np.where(jud_arr, np.where(col >= params.blink_below, STATE_ON, STATE_OFF),
                       STATE_NODATA).astype(int).tolist()
        _debounce_states(raw, params.debounce)
        state_matrix[r, :] = raw

    # Only now can one array stop be told from another: whether a pulser took the array down
    # is what separates a trip from a stop of ours, and that needs the scores — and what
    # became of the culprit needs the states after it. Hence the order: states first, then
    # the array's stops, then the per-pulser verdicts that depend on them. A recovery is
    # the first judged frame after an OUTAGE; switching the diodes back on after a stop of
    # ours is not a recovery from anything.
    trips, frame_trip, frame_diodes_off, frame_restart, hints, forced = _array_stops(
        rois, times, synth, bounds, [i["judged"] for i in s_infos], state_matrix,
        score_matrix, score_sid, score_valid, frame_judge,
        [i["dark"] for i in s_infos], excluded, params,
        window_end_ns=window_ns[1] if window_ns else None)
    for r, i, s in forced:
        state_matrix[r, i] = s

    # Array-up time: the shared uptime denominator. Summed per judged frame, so the
    # intervals covering a trip, a warm-up or a switch-off are simply never added.
    array_up_ns = sum((times[i + 1] - times[i])
                      for i in range(n - 1) if frame_judge[i])

    stats = [_compute_pulser_stats(
                 rois[r].name, list(state_matrix[r]), times, params,
                 judge=frame_judge, frame_trip=frame_trip,
                 frame_off=frame_diodes_off, frame_restart=frame_restart,
                 array_up_ns=array_up_ns, excl=frame_excluded, excluded=excluded,
                 score=score_matrix[:, r], hints=hints.get(r))
             for r in range(n_rois)]
    by_name = {st.name: st for st in stats}
    for t in trips:
        for name in t.cause_names:
            if name in by_name:
                by_name[name].caused_trips += 1

    _attribute_trips(trips, stats, state_matrix, times, frame_nodata, frame_excluded)

    stretches, unexplained = _summarise_stretches(
        rois, stats, times, frame_judge, sid, bounds, s_infos,
        power if use_power else None, low_power or [], params)
    n_unjudged = sum(1 for s in stretches if not s.judged)
    if n_unjudged:
        warnings.append(
            f"{n_unjudged} stretch(es) at full power had no frames to take a reference from "
            f"and were not judged — see the Stretches table on the Statistics tab.")

    return CameraAnalysis(stats=stats, state_matrix=state_matrix,
                          frame_nodata=frame_nodata, times_ns=times,
                          trips=trips, baselines=baselines,
                          signal_matrix=signal_matrix, score_matrix=score_matrix,
                          warnings=warnings, frame_warmup=frame_warmup,
                          frame_trip=frame_trip, frame_diodes_off=frame_diodes_off,
                          frame_judge=frame_judge, array_up_ns=array_up_ns,
                          array_level=array_level, warmup_windows=warmup_windows,
                          warm_level_used=warm_lvl, warm_level_source=warm_src,
                          params=params, frame_excluded=frame_excluded,
                          excluded=excluded, gate_notes=list(gate_notes or []),
                          gate_hpe_failed=gate_hpe_failed,
                          frame_below_full=frame_below_full, stretches=stretches,
                          unexplained_dips=unexplained,
                          power=power if use_power else None,
                          power_failed=bool(gate_power_failed or
                                            (power is not None and power.failed)))


def _attribute_trips(trips: list, stats: list, state_matrix: np.ndarray,
                     times: list, frame_nodata: list,
                     excl: "list | None" = None) -> None:
    """For each array outage: which pulsers lit before it came back after it, and which
    did not. Who CAUSED it is decided where the stop is classified (`_array_stops`).

    Gate-excluded time ends an up-period as firmly as a trip does. Otherwise a pulser's
    morning could be measured from yesterday afternoon's frames, with the whole unobserved
    night silently in the middle.

    "Died at this trip" is measured PER UP-PERIOD. Asking whether a pulser was ever ON
    at any point before the trip meant that a pulser which failed at 10:40 was re-reported as
    dying at every one of the day's later trips — the same two names against ten rows."""
    if not trips or state_matrix.size == 0:
        return
    times_arr = np.asarray(times)
    n = state_matrix.shape[1]
    n_rois = state_matrix.shape[0]
    ex = list(excl) if excl else [False] * n
    if len(ex) < n:
        ex = ex + [False] * (n - len(ex))
    # First frame index of the observed stretch each position belongs to: one past the
    # most recent excluded frame.
    seg_start = [0] * n
    s = 0
    for i in range(n):
        seg_start[i] = s
        if ex[i]:
            s = i + 1

    prev_trip_end = 0     # first frame index belonging to the current up-period
    for trip in trips:
        # first frame index at/after the trip end that has data
        after = int(np.searchsorted(times_arr, trip.end_ns, side="left"))
        before = int(np.searchsorted(times_arr, trip.start_ns, side="left"))
        # Data frames of the up-period that this trip ended.
        lo = max(prev_trip_end, seg_start[min(before, n - 1)])
        if not trip.is_outage:
            # The diodes being off is not an outage: nobody caused it, nobody recovered
            # from it and nobody died at it. It still ends the up-period.
            prev_trip_end = after
            continue

        for r in range(n_rois):
            row = state_matrix[r]
            name = stats[r].name if r < len(stats) else str(r)

            # Alive during THIS up-period, not "ever". Otherwise a pulser that failed once
            # is named as dying again at every later trip of the day.
            was_alive = bool(np.any(row[lo:before] == STATE_ON)) if before > lo else False
            if not was_alive:
                continue
            alive_after = np.any(row[after:] == STATE_ON) if after < len(row) else False
            if alive_after:
                trip.recovered.append(name)
            else:
                trip.died.append(name)

        # Everything from here on belongs to the next up-period.
        prev_trip_end = after


# ── HELPERS ───────────────────────────────────────────────────────────────────

_SHARE_YEAR_RE = re.compile(r"(cpva-image-)(\d{4})", re.IGNORECASE)


def _images_root_for_year(root: Path, year: int) -> Path:
    """Swap the year inside the share NAME, e.g. …/cpva-image-2026 → …/cpva-image-2025.

    Each year lives in its own SMB share, and on a UNC path the share is part of the
    anchor — `root.parent` would walk off the top of the path rather than give the host.
    So the year is rewritten textually. Same approach as Image Tools/sf_t.py."""
    s = str(root)
    new, n = _SHARE_YEAR_RE.subn(lambda m: f"{m.group(1)}{year}", s, count=1)
    return Path(new) if n else root


def _parse_ts_from_path(p: Path) -> "int | None":
    for m in _NS_19_RE.finditer(p.stem):
        ts = int(m.group())
        if 946684800_000_000_000 <= ts <= 4102444800_000_000_000:
            return ts
    return None


def _read_tiff_max_sample(path: Path) -> "int | None":
    """MaxSampleValue (TIFF tag 281) — the DECLARED range of a TIFF, which is what a
    divisor has to be.

    The PNG `MaxValue` tEXt chunk is deliberately NOT read: it is a per-frame peak, not a
    range — see `_to_unit_scale`."""
    if path.suffix.lower() not in (".tif", ".tiff"):
        return None
    try:
        with PilImage.open(str(path)) as pil:
            tag_data = pil.tag_v2 if hasattr(pil, "tag_v2") else getattr(pil, "tag", {})
            val = tag_data.get(281)
            if val is not None:
                if isinstance(val, (list, tuple)):
                    val = val[0]
                v = int(val)
                return v if v > 0 else None
    except Exception:
        pass
    return None


def _to_unit_scale(arr: np.ndarray, max_val: "float | None" = None) -> np.ndarray:
    """Scale a raw frame to 0..1 by dividing by the full 16-bit range.

    The archiver writes 16-bit frames with the CAMERA's full scale stretched onto 65535
    (`stored = raw_counts * 65535/(2**bits - 1)`, a FIXED factor — measured ×16.0037 on
    these 12-bit Baslers), so 65535 is THE camera-independent absolute scale.

    The PNGs also carry a `MaxValue` tEXt chunk, and dividing by it was the bug here: a
    normal frame came out at ~7.5 instead of ~0.47, i.e. 16× too large. Nothing
    downstream noticed, because every score is a ratio and the factor cancels — except
    the no-data test, which compares an absolute level and therefore never fired on a
    blank frame.

    `MaxValue` is the PEAK OF THAT FRAME in raw counts, not the sensor's range: the
    reference frames here read 3378 / 3391 / 3806 on warmup and 4095 only when the
    sensor is genuinely saturated. It looked like a range because a saturated 12-bit
    frame's peak IS 4095. Either way it is the wrong divisor.
    (Image Tools reached the same conclusion — see `Image Tools/img_scale.py`, which owns
    this rule for every tab, and `Image Tools/testing/test_scale_invariance.py`, which checks it
    against the archive.)"""
    scale = float(max_val) if (max_val and max_val > 0) else _FULL_SCALE_16
    return arr / scale


def _load_as_float32_gray(path: Path) -> "np.ndarray | None":
    try:
        with PilImage.open(str(path)) as pil:
            arr = np.array(pil.convert("I"), dtype=np.float32)
        return _to_unit_scale(arr, _read_tiff_max_sample(path))
    except Exception:
        return None


def _ns_to_dt(ts_ns: int) -> datetime:
    tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
    return datetime.fromtimestamp(ts_ns / 1e9, tz=tz)


# Matplotlib's date2num converts tz-aware datetimes to UTC floats; axis formatters
# and locators must be given the display tz explicitly or they render UTC times.
_PLOT_TZ = TZ_PRAGUE if TZ_PRAGUE else timezone.utc


def _date_fmt(fmt: str) -> "mdates.DateFormatter":
    return mdates.DateFormatter(fmt, tz=_PLOT_TZ)


def _date_loc() -> "mdates.AutoDateLocator":
    return mdates.AutoDateLocator(tz=_PLOT_TZ)


def _make_mpl_toolbar(nav_cls, canvas, parent=None):
    """Build a matplotlib NavigationToolbar whose icons are actually visible.

    matplotlib tints the toolbar icons ONCE, AT CONSTRUCTION, and only when the palette
    background is dark: it recolours the black PNGs to the palette foreground. Under Fusion
    on a dark Windows theme that foreground is near-white, so the buttons come out
    white-on-light and read as blank. It never re-tints, so fixing the palette afterwards
    does nothing — the parent has to be right before the toolbar exists.

    So: parent it to a host widget carrying a LIGHT palette (tinting is skipped, the original
    black icons survive), then paint a light toolbar background to match. Use this for every
    matplotlib toolbar in this file rather than constructing one directly.

    (Image Tools/if_t.py has the same helper for the same reason. Copied rather than imported
    — this app deliberately shares no code across program folders, for the same reason it does
    not import cpva_client.)"""
    host = QWidget(parent)
    hp = host.palette()
    hp.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    hp.setColor(QPalette.ColorRole.Button, QColor("#f0f0f0"))
    hp.setColor(QPalette.ColorRole.WindowText, QColor("#202020"))
    hp.setColor(QPalette.ColorRole.ButtonText, QColor("#202020"))
    host.setPalette(hp)

    toolbar = nav_cls(canvas, host)
    toolbar.setStyleSheet(
        "QToolBar { background: #f0f0f0; border: none; spacing: 1px; }"
        "QToolButton { background: transparent; padding: 3px; }"
        "QToolButton:hover { background: #d6d6d6; border-radius: 3px; }"
        "QLabel { color: #202020; }")
    # THE HOST HAS DONE ITS ONE JOB and must now get out of the way. The caller puts
    # the TOOLBAR into a layout, which re-parents it away and leaves this widget
    # behind as a child no layout owns — and such a child sits at (0, 0) of its
    # parent at its default size. Painted in the window's own ground it is invisible
    # until something scrolls under it, and it stands still while the panel moves.
    host.setFixedSize(0, 0)
    host.hide()
    return toolbar


def _find_latest_cam_folder(images_root: Path, camera: str) -> "Path | None":
    now = datetime.now(timezone.utc)
    for delta in range(0, 24 * 7):
        cur = now - timedelta(hours=delta)
        folder = (_images_root_for_year(images_root, cur.year) / str(cur.year)
                  / str(cur.month) / str(cur.day) / str(cur.hour) / camera)
        try:
            if folder.exists() and folder.is_dir():
                with os.scandir(str(folder)) as it:
                    for e in it:
                        if e.is_file() and Path(e.path).suffix.lower() in IMAGE_EXTS:
                            return folder
        except Exception:
            pass
    return None


def _find_ref_image(images_root: Path, cam_key: str) -> "Path | None":
    """Return the first qualifying image (by file size) from the most recent folder.
    Falls back to any image found if nothing meets the size threshold."""
    folder_name = CAMERAS.get(cam_key)
    if not folder_name:
        return None
    min_bytes = MIN_IMAGE_BYTES
    folder = _find_latest_cam_folder(images_root, folder_name)
    if not folder:
        return None
    fallback = None
    try:
        with os.scandir(str(folder)) as it:
            for e in it:
                if not e.is_file():
                    continue
                p = Path(e.path)
                if p.suffix.lower() not in IMAGE_EXTS:
                    continue
                if fallback is None:
                    fallback = p
                try:
                    if p.stat().st_size >= min_bytes:
                        return p
                except Exception:
                    continue
    except Exception:
        pass
    return fallback


def _make_default_rois(cam_key: str, img_w: int, img_h: int) -> list:
    """Create a default 5-column × 8-row ROI grid for the given camera."""
    cols = CAM_COLS.get(cam_key, ["A", "B", "C", "D", "E"])
    rows = _ROW_LABELS
    n_cols, n_rows = len(cols), len(rows)
    margin_x = int(img_w * 0.06)
    margin_y = int(img_h * 0.06)
    usable_w = img_w - 2 * margin_x
    usable_h = img_h - 2 * margin_y
    cell_w = usable_w // n_cols
    cell_h = usable_h // n_rows
    pad = 2
    rois = []
    for ri, row_lbl in enumerate(rows):
        for ci, col_lbl in enumerate(cols):
            x = margin_x + ci * cell_w + pad
            y = margin_y + ri * cell_h + pad
            w = max(4, cell_w - 2 * pad)
            h = max(4, cell_h - 2 * pad)
            name = f"{col_lbl}{row_lbl}"
            color = _ROI_COLORS[(ri * n_cols + ci) % len(_ROI_COLORS)]
            rois.append(RoiDefinition(name=name, x=x, y=y, w=w, h=h, color=color))
    return rois


def _smooth1d(prof: np.ndarray, k: int) -> np.ndarray:
    """Simple odd-length moving-average smoothing with edge padding."""
    if k < 3:
        return prof
    if k % 2 == 0:
        k += 1
    pad = k // 2
    padded = np.pad(prof, pad, mode="edge")
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(padded, kernel, mode="valid")


def _detrend(prof: np.ndarray, win: int) -> np.ndarray:
    """Divide a 1-D profile by its heavily-smoothed baseline to remove a slow
    illumination gradient, so the dark grid-line dips become comparable in depth
    across the whole array (the lit pulsers are far brighter on one side)."""
    baseline = _smooth1d(prof, win)
    return prof / np.maximum(baseline, 1e-6)


def _axis_edges(prof: np.ndarray, n_segments: int) -> list:
    """Return n_segments+1 boundary positions along one axis by fitting a
    *regular* grid (constant pitch + offset) — pulser arrays are evenly spaced,
    so a regular fit aligns every cell at once and avoids the per-boundary drift
    that leaves some ROIs covering half a pulser or spilling into black.

    The fit searches pitch and offset to minimise the (detrended) brightness
    sampled at the n_segments+1 predicted grid-line positions, i.e. it slides a
    rigid comb until its teeth sit on the dark separators."""
    n = len(prof)
    if n_segments < 1 or n <= n_segments:
        return [int(round(n * k / max(1, n_segments))) for k in range(n_segments + 1)]

    lo_v, hi_v = float(prof.min()), float(prof.max())
    if hi_v <= lo_v:
        return [int(round(n * k / n_segments)) for k in range(n_segments + 1)]
    # Inclusive border crop: keep anything above 18% of the lit range
    thr = lo_v + (hi_v - lo_v) * 0.18
    lit = np.where(prof >= thr)[0]
    lo, hi = (int(lit[0]), int(lit[-1]) + 1) if len(lit) >= 2 else (0, n)
    if hi - lo <= n_segments:
        lo, hi = 0, n

    nominal = (hi - lo) / n_segments
    detr = _detrend(prof, max(3, int(nominal * 1.5)))

    def sample(pos: float) -> float:
        i = int(round(pos))
        a, b = max(0, i - 1), min(n, i + 2)
        return float(detr[a:b].min()) if b > a else 1e9

    # Slide a rigid comb of n_segments+1 teeth: vary pitch ±18% and offset (the
    # left border) ±0.6 cell around the cropped extent; pick the darkest fit.
    best_cost, best = 1e18, (lo, nominal)
    p_lo, p_hi = nominal * 0.82, nominal * 1.18
    n_pitch = 25
    for pi in range(n_pitch + 1):
        pitch = p_lo + (p_hi - p_lo) * pi / n_pitch
        o_start = lo - 0.6 * nominal
        o_stop = lo + 0.6 * nominal
        steps = max(8, int((o_stop - o_start)))
        for si in range(steps + 1):
            off = o_start + (o_stop - o_start) * si / steps
            last = off + pitch * n_segments
            if off < -1 or last > n + 1:
                continue
            cost = sum(sample(off + pitch * k) for k in range(n_segments + 1))
            if cost < best_cost:
                best_cost, best = cost, (off, pitch)

    off, pitch = best
    edges = [int(round(min(max(0, off + pitch * k), n))) for k in range(n_segments + 1)]
    # Guarantee strictly increasing edges
    for k in range(1, len(edges)):
        if edges[k] <= edges[k - 1]:
            edges[k] = min(n, edges[k - 1] + 1)
    return edges


def _box_blur2d(a: np.ndarray, k: int) -> np.ndarray:
    """Fast separable box blur via an integral image (edge-padded). Builds a
    per-cell illumination baseline so thresholding is immune to the lighting
    gradient across a tile."""
    if k < 3:
        return a
    if k % 2 == 0:
        k += 1
    pad = k // 2
    ap = np.pad(a, pad, mode="edge")
    cs = np.cumsum(np.cumsum(ap, 0), 1)
    cs = np.pad(cs, ((1, 0), (1, 0)), mode="constant")
    H, W = a.shape
    y0 = np.arange(H)[:, None]; x0 = np.arange(W)[None, :]
    y1 = y0 + k; x1 = x0 + k
    tot = cs[y1, x1] - cs[y0, x1] - cs[y1, x0] + cs[y0, x0]
    return (tot / (k * k)).astype(np.float32)


def _cell_centers(edges: list) -> list:
    """Midpoints of consecutive comb edges — the coarse centre of each cell."""
    return [(edges[k] + edges[k + 1]) * 0.5 for k in range(len(edges) - 1)]


def _neighbor_clamps(centers: list, pitch: float, n: int) -> list:
    """For every cell return (lo, hi): the boundary halfway to its neighbours
    (and ±_WINDOW_FRAC·pitch for the two outermost cells), clamped to [0, n].

    These bounds serve double duty — they are both the local search window for
    the cell *and* the hard limit its box may not cross, so a cell can physically
    never read or claim pixels past the midpoint to a neighbour."""
    m = len(centers)
    clamps = []
    for k in range(m):
        lo = (centers[k - 1] + centers[k]) * 0.5 if k > 0 else centers[k] - _WINDOW_FRAC * pitch
        hi = (centers[k] + centers[k + 1]) * 0.5 if k < m - 1 else centers[k] + _WINDOW_FRAC * pitch
        clamps.append((int(max(0, round(lo))), int(min(n, round(hi)))))
    return clamps


def _cell_bbox(block: np.ndarray, pitch: float, abs_floor: float = 0.0) -> "tuple | None":
    """Find the actual bright pulser patch inside one cell window in 2-D and
    return its (x0, y0, x1, y1) in the block's local coords, or None if the
    window holds no clear patch (a dark/off pulser).

    The block is flattened for illumination (divided by a heavy box blur) so the
    lit tile stands out from the dark separators regardless of the lighting
    gradient; a threshold placed between the dark and bright levels gives the tile
    mask. The bounding box is taken from the inner percentile of the lit
    coordinates, which trims off the thin diagonal reflection and stray specks
    rather than letting them stretch the box out to the window edge.

    `abs_floor` is a raw-intensity gate: near-black pixels (e.g. the image border
    that the leftmost/top cells reach into) become ~1.0 after the flatten and would
    pass the relative threshold, so they must also clear this floor to count."""
    bh, bw = block.shape
    if bh < 4 or bw < 4:
        return None
    base = _box_blur2d(block, max(5, int(pitch * _CELL_BLUR_FRAC)))
    norm = block / np.maximum(base, 1e-6)
    lo = float(np.percentile(norm, _CELL_THR_LO_PCT))
    hi = float(np.percentile(norm, _CELL_THR_HI_PCT))
    if hi <= lo:
        return None
    mask = (norm > lo + (hi - lo) * _CELL_THR_FRAC) & (block > abs_floor)
    ys, xs = np.where(mask)
    if len(xs) < bh * bw * _CELL_MIN_BRIGHT:
        return None
    x0, x1 = np.percentile(xs, [_CELL_TRIM_PCT, 100 - _CELL_TRIM_PCT])
    y0, y1 = np.percentile(ys, [_CELL_TRIM_PCT, 100 - _CELL_TRIM_PCT])
    return int(x0), int(y0), int(x1), int(y1)


def _consensus_spans(spans: list, detected: list,
                     lo_w: float = 0.65, hi_w: float = 1.4,
                     tol_c: float = 0.4) -> list:
    """Snap outlier 1-D spans along a grid line to the line's median span.

    Pulsers in a column share one horizontal extent and pulsers in a row share
    one vertical extent, so a cell whose detected span is much wider/narrower or
    badly off-centre — e.g. an edge cell that grabbed the dim image border instead
    of the (smaller) edge tile, or a dark/off pulser with no detection — is replaced
    by the robust median box of its well-detected neighbours. Lines with fewer than
    two detected cells are left untouched (no consensus to trust)."""
    idx = [i for i, d in enumerate(detected) if d]
    if len(idx) < 2:
        return list(spans)
    widths = sorted(spans[i][1] - spans[i][0] for i in idx)
    centers = sorted((spans[i][0] + spans[i][1]) * 0.5 for i in idx)
    med_w = widths[len(widths) // 2]
    med_c = centers[len(centers) // 2]
    if med_w <= 0:
        return list(spans)
    out = []
    for i, (a, b) in enumerate(spans):
        w, c = b - a, (a + b) * 0.5
        ok = (detected[i] and lo_w * med_w <= w <= hi_w * med_w
              and abs(c - med_c) <= tol_c * med_w)
        out.append((a, b) if ok else (med_c - med_w / 2, med_c + med_w / 2))
    return out


def _sane_span(a: float, b: float, center: float, pitch: float,
               lo: float, hi: float) -> "tuple[int, int]":
    """Keep a detected 1-D span sane: never thinner than half the pitch and
    centred within 30 % of the pitch of the comb centre, clamped to [lo, hi].
    A safety net so a stray detection can't collapse or drift a box."""
    width = min(max(b - a, pitch * 0.5), hi - lo)
    c = (a + b) * 0.5
    c = min(max(c, center - pitch * 0.30), center + pitch * 0.30)
    a, b = c - width / 2, c + width / 2
    if a < lo:
        a, b = lo, lo + width
    if b > hi:
        a, b = hi - width, hi
    return int(round(max(lo, a))), int(round(min(hi, b)))


def _detect_grid_rois(arr: np.ndarray, cam_key: str) -> list:
    """Detect individual pulser ROIs from a reference image.

    1) Fit a regular comb over the global row/column profiles to locate the cell
       centres + pitch consistently, robust even when several pulsers are dark
       (the skeleton).
    2) For EACH cell, search a local window clamped to the midpoints with its
       neighbours and cut the box to the actual bright tile in 2-D (`_cell_bbox`).
       Every diode gets its own size/position, the box fills the tile up to the
       dark separators, and it can never reach into a neighbour.
    3) A cell with no clear patch (a dark/off pulser) falls back to the regular
       comb cell, so it still gets a full-size, correctly-placed ROI to nudge.
    `arr` is a float32 grayscale image (values roughly 0..1)."""
    cols = CAM_COLS.get(cam_key, ["A", "B", "C", "D", "E"])
    rows = _ROW_LABELS
    n_cols, n_rows = len(cols), len(rows)
    h, w = arr.shape
    if w < n_cols * 4 or h < n_rows * 4:
        return _make_default_rois(cam_key, w, h)

    # ── Skeleton: regular comb over the global profiles ────────────────────────
    col_prof = _smooth1d(arr.mean(axis=0).astype(np.float32), max(3, w // 120))
    row_prof = _smooth1d(arr.mean(axis=1).astype(np.float32), max(3, h // 120))
    x_edges = _axis_edges(col_prof, n_cols)
    y_edges = _axis_edges(row_prof, n_rows)

    x_centers = _cell_centers(x_edges)
    y_centers = _cell_centers(y_edges)
    x_pitch = (x_centers[-1] - x_centers[0]) / max(1, n_cols - 1) if n_cols > 1 else float(w)
    y_pitch = (y_centers[-1] - y_centers[0]) / max(1, n_rows - 1) if n_rows > 1 else float(h)
    x_clamps = _neighbor_clamps(x_centers, x_pitch, w)
    y_clamps = _neighbor_clamps(y_centers, y_pitch, h)
    pitch = min(x_pitch, y_pitch)

    # Raw-intensity floor separating the black image border from lit tiles, so the
    # outermost cells (col A / row 1) can't grab the dark border as part of a tile.
    g_lo = float(np.percentile(arr, 5))
    g_hi = float(np.percentile(arr, 95))
    abs_floor = g_lo + (g_hi - g_lo) * _CELL_ABS_FLOOR_FRAC

    # ── Pass 1: detect each cell's tile box independently ──────────────────────
    xspan = [[None] * n_cols for _ in range(n_rows)]
    yspan = [[None] * n_cols for _ in range(n_rows)]
    detected = [[False] * n_cols for _ in range(n_rows)]
    for ri in range(n_rows):
        cy = y_centers[ri]
        wy0, wy1 = y_clamps[ri]
        for ci in range(n_cols):
            cx = x_centers[ci]
            wx0, wx1 = x_clamps[ci]
            bbox = _cell_bbox(arr[wy0:wy1, wx0:wx1], pitch, abs_floor)
            if bbox is not None:
                ix0, ix1 = wx0 + bbox[0], wx0 + bbox[2]
                iy0, iy1 = wy0 + bbox[1], wy0 + bbox[3]
                # Safety net: keep the box a sane cell, never crossing a neighbour.
                ix0, ix1 = _sane_span(ix0, ix1, cx, x_pitch, wx0, wx1)
                iy0, iy1 = _sane_span(iy0, iy1, cy, y_pitch, wy0, wy1)
                detected[ri][ci] = True
            else:
                # No clear patch (dark/off pulser) → full comb cell as a placeholder;
                # consensus below pulls it onto the column/row's real tile box.
                ix0, ix1, iy0, iy1 = wx0, wx1, wy0, wy1
            xspan[ri][ci] = (ix0, ix1)
            yspan[ri][ci] = (iy0, iy1)

    # ── Pass 2: consensus — a column shares one x-extent, a row shares one y ────
    # Repairs edge cells that grabbed the dim border and off pulsers with no patch.
    for ci in range(n_cols):
        col_x = _consensus_spans([xspan[ri][ci] for ri in range(n_rows)],
                                 [detected[ri][ci] for ri in range(n_rows)])
        for ri in range(n_rows):
            xspan[ri][ci] = col_x[ri]
    for ri in range(n_rows):
        row_y = _consensus_spans([yspan[ri][ci] for ci in range(n_cols)],
                                 [detected[ri][ci] for ci in range(n_cols)])
        for ci in range(n_cols):
            yspan[ri][ci] = row_y[ci]

    # ── Emit ───────────────────────────────────────────────────────────────────
    rois = []
    for ri in range(n_rows):
        for ci in range(n_cols):
            name = f"{cols[ci]}{rows[ri]}"
            color = _ROI_COLORS[(ri * n_cols + ci) % len(_ROI_COLORS)]
            ix0, ix1 = xspan[ri][ci]
            iy0, iy1 = yspan[ri][ci]
            in_x = max(1, int((ix1 - ix0) * _ROI_INSET_FRAC))
            in_y = max(1, int((iy1 - iy0) * _ROI_INSET_FRAC))
            x0, y0, x1, y1 = ix0 + in_x, iy0 + in_y, ix1 - in_x, iy1 - in_y
            x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
            x1, y1 = min(w, int(round(x1))), min(h, int(round(y1)))
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            rois.append(RoiDefinition(name=name, x=x0, y=y0,
                                      w=x1 - x0, h=y1 - y0, color=color))
    if not rois:
        return _make_default_rois(cam_key, w, h)
    return rois


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color:#ccc;margin:2px 0;")
    return f


def _group_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "font-size:10px;color:#777;font-weight:700;letter-spacing:1px;padding-top:4px;"
    )
    return lbl


# ── CALENDAR COMPONENTS ───────────────────────────────────────────────────────

class _WeekendDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        col = index.column()
        if col < 1:
            return
        date_val = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(date_val, QDate) and date_val.isValid():
            if date_val.dayOfWeek() in (6, 7):
                option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
                option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))
            return
        if col in (6, 7):
            option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
            option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))


class _NoScrollCalendar(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._noscroll = set()

    def _install(self):
        for child in self.findChildren(QAbstractScrollArea):
            if id(child) not in self._noscroll:
                child.installEventFilter(self)
                child.viewport().installEventFilter(self)
                self._noscroll.add(id(child))

    def showEvent(self, event):
        super().showEvent(event)
        self._install()

    def wheelEvent(self, event):
        event.accept()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.accept()
            return True
        return super().eventFilter(obj, event)


class _NoScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


# ── TIME WINDOW DIALOG ────────────────────────────────────────────────────────

class _TimeWindowDialog(QDialog):
    def __init__(self, start_dt: "datetime | None" = None,
                 end_dt: "datetime | None" = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Time window")
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        now = datetime.now(tz)
        if start_dt is None:
            start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if end_dt is None:
            end_dt = now

        def _make_cal(init_dt: datetime) -> _NoScrollCalendar:
            cal = _NoScrollCalendar()
            cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
            cal.setGridVisible(True)
            cal.setNavigationBarVisible(True)
            cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
            if view:
                view.setItemDelegate(_WeekendDelegate(view))
            hf = QTextCharFormat()
            hf.setForeground(QColor("#111111"))
            cal.setHeaderTextFormat(hf)
            wf = QTextCharFormat()
            wf.setForeground(QColor("#111111"))
            for day in [Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                        Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday]:
                cal.setWeekdayTextFormat(day, wf)
            wf_we = QTextCharFormat()
            wf_we.setForeground(QColor("#cc0000"))
            for day in [Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday]:
                cal.setWeekdayTextFormat(day, wf_we)
            cal.setStyleSheet(_CAL_STYLE)
            cal.setSelectedDate(QDate(init_dt.year, init_dt.month, init_dt.day))
            return cal

        grp_s = QGroupBox("Start point")
        sl = QVBoxLayout(grp_s)
        self._cal_start = _make_cal(start_dt)
        self._hour_start = QSpinBox()
        self._hour_start.setRange(0, 23)
        self._hour_start.setValue(start_dt.hour)
        self._hour_start.setFixedWidth(70)
        hr_s = QHBoxLayout()
        hr_s.addWidget(QLabel("Hour:"))
        hr_s.addWidget(self._hour_start)
        hr_s.addStretch(1)
        sl.addWidget(self._cal_start)
        sl.addLayout(hr_s)

        grp_e = QGroupBox("End point")
        el = QVBoxLayout(grp_e)
        self._cal_end = _make_cal(end_dt)
        self._hour_end = QSpinBox()
        self._hour_end.setRange(0, 23)
        self._hour_end.setValue(end_dt.hour)
        self._hour_end.setFixedWidth(70)
        btn_now = QPushButton("Now")
        btn_now.setFixedWidth(48)
        btn_now.clicked.connect(self._go_now)
        hr_e = QHBoxLayout()
        hr_e.addWidget(QLabel("Hour:"))
        hr_e.addWidget(self._hour_end)
        hr_e.addWidget(btn_now)
        hr_e.addStretch(1)
        el.addWidget(self._cal_end)
        el.addLayout(hr_e)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(grp_s)
        row.addWidget(grp_e)
        main = QVBoxLayout(self)
        main.addLayout(row)
        main.addWidget(btns)

    def _go_now(self):
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        now = datetime.now(tz)
        self._cal_end.setSelectedDate(QDate(now.year, now.month, now.day))
        self._hour_end.setValue(now.hour)

    def _on_accept(self):
        s, e = self.selected_range()
        if e < s:
            QMessageBox.warning(self, "Invalid range", "End must be after start.")
            return
        self.accept()

    def _get_start(self) -> datetime:
        d = self._cal_start.selectedDate()
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        return datetime(d.year(), d.month(), d.day(), self._hour_start.value(), 0, 0, tzinfo=tz)

    def _get_end(self) -> datetime:
        d = self._cal_end.selectedDate()
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        return datetime(d.year(), d.month(), d.day(), self._hour_end.value(), 59, 59, tzinfo=tz)

    def selected_range(self) -> "tuple[datetime, datetime]":
        return self._get_start(), self._get_end()


# ── SCAN SIGNALS & WORKERS ────────────────────────────────────────────────────

class _FileScanSignals(QObject):
    files_ready = Signal(object)  # list of (ts_ns, Path)
    error       = Signal(str)


class _GateSignals(QObject):
    ready = Signal(object, object, object, bool)  # active, excluded, notes, hpe_failed


class _GateWorker(QRunnable):
    """Works out the active/excluded spans for a scan window, off the GUI thread.

    Only the high-power rule needs the network, but it needs it badly enough to matter:
    the archiver query has a 25 s timeout and an unreachable host on this site stalls for
    roughly 48 s before failing, so doing this inline would freeze the window for the
    best part of a minute every time someone pressed Scan from the office."""

    def __init__(self, sig: _GateSignals, start_ns: int, end_ns: int,
                 gate_hours: bool, gate_high_power: bool,
                 hour_start: int, hour_end: int, gen_box: list, my_gen: int):
        super().__init__()
        self._sig = sig
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._gate_hours = gate_hours
        self._gate_hp = gate_high_power
        self._h0 = hour_start
        self._h1 = hour_end
        self._gen_box = gen_box
        self._my_gen = my_gen
        self.setAutoDelete(True)

    def run(self):
        try:
            active, excluded, notes, failed = active_spans(
                self._start_ns, self._end_ns,
                gate_hours=self._gate_hours, gate_high_power=self._gate_hp,
                hour_start=self._h0, hour_end=self._h1)
        except Exception as exc:
            # active_spans already swallows an archiver failure; anything reaching here is
            # a bug in the span algebra, so scan the raw window rather than nothing.
            active, excluded, notes, failed = (
                _SpanSet([(self._start_ns, self._end_ns)]), _SpanSet(),
                [f"gate failed ({exc}) — scanning the whole window"], True)
        if self._gen_box[0] == self._my_gen:
            self._sig.ready.emit(active, excluded, notes, failed)


class _PowerSignals(QObject):
    ready = Signal(object)   # PowerInfo


class _PowerWorker(QRunnable):
    """Reads the full-power gate (dump bus + energy of every array) off the GUI thread.

    About a hundred archiver queries for a day — chunked, because the archiver refuses a
    day in one piece — so this takes seconds, and an unreachable archiver tens of them."""

    def __init__(self, sig: _PowerSignals, start_ns: int, end_ns: int,
                 params: "AnalysisParams", active: "_SpanSet | None",
                 gen_box: list, my_gen: int):
        super().__init__()
        self._sig = sig
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._params = params
        self._active = active
        self._gen_box = gen_box
        self._my_gen = my_gen
        self.setAutoDelete(True)

    def run(self):
        try:
            info = full_power_info(self._start_ns, self._end_ns, self._params, self._active)
        except Exception as exc:
            info = PowerInfo(failed=True, notes=[f"full-power gate failed ({exc})"])
        if self._gen_box[0] == self._my_gen:
            self._sig.ready.emit(info)


# ── Multiprocessing frame measurement ──────────────────────────────────────────
# Each worker process opens an image ONCE, computes per-ROI signal and the whole-frame
# mean, and returns a tiny tuple. ROI rectangles are pushed once via the pool
# initializer, not per task.
_W_ROI: list = []


# How far outside the ROI box to look for the local dark floor, as a fraction of the box
# size. Has to reach past the tile edge to the separator even when the box is drawn a
# little off-centre, without reaching into the next tile but one.
_FLOOR_PAD = 0.35


def _tile_floor_contrast(arr: np.ndarray, x: int, y: int, w: int, h: int) -> tuple:
    """(tile, floor, contrast) for one ROI rect.

    tile  = 75th percentile inside the box — the lit part of it. A percentile rather than
            the median because a box is often drawn a little larger than the pulser tile,
            or slightly off it, so part of its interior is the dark separator; the median
            then sits between the two levels and moves with the box's alignment rather
            than with the pulser. The top quarter is the tile wherever the box sits, and
            it still ignores the bright diagonal reflection and hot specks.
    floor = 10th percentile of a window reaching `_FLOOR_PAD` beyond the box — the dark
            separator around the pulser, found wherever it happens to be instead of being
            assumed to lie in a thin ring hugging the box.
    contrast = tile − floor.

    This is deliberately insensitive to how the ROI boxes are drawn. The previous version
    took the median of the box minus the median of a 2-6 px ring just outside it, which
    silently required every box to trace its tile almost exactly: a box overlapping the
    separator pulled the median down, and a box smaller than its tile put the ring ON the
    lit tile, so the "glow" being subtracted was the pulser's own light. Both collapse the
    contrast of a perfectly healthy pulser — measured on PD4M1, five boxes read 0.00-0.57
    of their reference while their brightness had not moved at all, indistinguishable from
    the 0.00-0.33 of genuinely dead ones. With this measure the same five read 0.89-1.05
    against 0.25-0.43 for the dead, so the two no longer overlap.

    Subtracting a LOCAL floor also handles the optical crosstalk between pulsers: each one
    spills light onto its neighbours' tiles, so when one dies its neighbourhood dims too.
    Because the floor is measured next to each pulser rather than globally, that shared
    dimming cancels out of the difference."""
    h_img, w_img = arr.shape
    x0 = max(0, x); y0 = max(0, y)
    x1 = min(w_img, x + w); y1 = min(h_img, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0, 0.0
    tile = float(np.percentile(arr[y0:y1, x0:x1], 75))
    mx = int((x1 - x0) * _FLOOR_PAD) + 3
    my = int((y1 - y0) * _FLOOR_PAD) + 3
    win = arr[max(0, y0 - my):min(h_img, y1 + my),
              max(0, x0 - mx):min(w_img, x1 + mx)]
    floor = float(np.percentile(win, 10)) if win.size else 0.0
    return tile, floor, max(0.0, tile - floor)


def _pool_init(roi_rects: list) -> None:
    global _W_ROI
    _W_ROI = roi_rects


def _measure_frame(task: tuple) -> tuple:
    """(ts_ns, path_str) → (ts_ns, roi_means | None, frame_mean, roi_contrasts).
    Runs in a worker process."""
    ts_ns, path_str = task
    try:
        with PilImage.open(path_str) as pil:
            arr = np.asarray(pil.convert("I"), dtype=np.float32)
        arr = _to_unit_scale(arr, _read_tiff_max_sample(Path(path_str)))
        h, w = arr.shape
        means = []
        contrasts = []
        for (rx, ry, rw, rh) in _W_ROI:
            x0 = max(0, rx); y0 = max(0, ry)
            x1 = min(w, rx + rw); y1 = min(h, ry + rh)
            means.append(float(arr[y0:y1, x0:x1].mean()) if (x1 > x0 and y1 > y0) else 0.0)
            contrasts.append(_tile_floor_contrast(arr, rx, ry, rw, rh)[2])
        # frame mean on a strided grid — plenty for the no-data floor, much cheaper.
        frame_mean = float(arr[::4, ::4].mean())
        return (ts_ns, means, frame_mean, contrasts)
    except Exception:
        return (ts_ns, None, 0.0, None)


class _ScanSignals(QObject):
    progress = Signal(int, int)   # done, total
    samples  = Signal(list)       # batch of SamplePoint
    finished = Signal()
    error    = Signal(str)
    log_msg  = Signal(str)


class _FileScanWorker(QRunnable):
    def __init__(self, sig: _FileScanSignals, images_root: Path, camera: str,
                 start_ns: int, end_ns: int, min_size: int,
                 gen_box: list, my_gen: int, active: "_SpanSet | None" = None):
        super().__init__()
        self._sig = sig
        self._root = images_root
        self._camera = camera
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._min_size = min_size
        self._gen_box = gen_box
        self._my_gen = my_gen
        # Frames the active-time gate would throw away are never read at all. This is
        # also where most of the gate's speed comes from: an hour folder that lies wholly
        # outside the active spans is not even listed, and a listing is almost pure SMB
        # latency, so a 07:00–21:00 gate cuts the enumeration of a multi-day window by
        # over half before a single image is opened.
        self._active = active
        self.setAutoDelete(True)

    # Hour folders are listed concurrently. Each listing is almost pure network latency on
    # the SMB share — a day's worth took ~30 s one folder at a time — and threads overlap
    # that wait without needing the GIL. Same approach as Image Tools' camera enumeration.
    _LIST_THREADS = 12

    def _scan_hour(self, hour_dt: datetime) -> list:
        """Every qualifying image in one hour folder. Never raises: a missing or
        unreachable folder is simply an empty hour."""
        folder = (_images_root_for_year(self._root, hour_dt.year)
                  / str(hour_dt.year) / str(hour_dt.month)
                  / str(hour_dt.day) / str(hour_dt.hour) / self._camera)
        found = []
        try:
            with os.scandir(str(folder)) as it:
                for entry in it:
                    if not entry.is_file():
                        continue
                    p = Path(entry.path)
                    if p.suffix.lower() not in IMAGE_EXTS:
                        continue
                    if self._min_size > 0:
                        try:
                            if entry.stat().st_size < self._min_size:
                                continue
                        except OSError:
                            continue
                    ts = _parse_ts_from_path(p)
                    if ts is None or not (self._start_ns <= ts <= self._end_ns):
                        continue
                    if self._active is not None and not self._active.contains(ts):
                        continue
                    found.append((ts, p))
        except (OSError, ValueError):
            pass
        return found

    def run(self):
        try:
            utc = timezone.utc
            start_utc = datetime.fromtimestamp(self._start_ns / 1e9, tz=utc)
            end_utc = datetime.fromtimestamp(self._end_ns / 1e9, tz=utc)
            cur = start_utc.replace(minute=0, second=0, microsecond=0)
            end_hour = end_utc.replace(minute=0, second=0, microsecond=0)
            hours = []
            while cur <= end_hour:
                # Folder names are UTC while the gate is in Prague local time, so the
                # overlap test is done on absolute ns and DST never enters into it.
                h0 = int(round(cur.timestamp())) * _NS_PER_S
                if (self._active is None
                        or self._active.overlap_ns(h0, h0 + 3600 * _NS_PER_S) > 0):
                    hours.append(cur)
                cur += timedelta(hours=1)

            files: list = []
            with ThreadPoolExecutor(max_workers=self._LIST_THREADS) as ex:
                for batch in ex.map(self._scan_hour, hours):
                    if self._gen_box[0] != self._my_gen:
                        return
                    files.extend(batch)
            files.sort(key=lambda x: x[0])
            if self._gen_box[0] == self._my_gen:
                self._sig.files_ready.emit(files)
        except Exception as exc:
            self._sig.error.emit(str(exc))


class _ScanWorker(QRunnable):
    _BATCH = 200      # SamplePoints per GUI signal — keeps the event loop responsive
    _MP_MIN = 64      # below this many files, the pool overhead isn't worth it
    # Progress is reported on a clock, not per sample batch. A day at 3.3 Hz is ~110k
    # frames; tying the signal to the 200-sample flush leaves the bar frozen for seconds
    # at a time, and it stalls completely across a run of unreadable frames, which never
    # reach `buf` at all. 0.2 s ≈ 5 updates a second, cheap next to decoding a frame.
    _PROGRESS_S = 0.2

    def __init__(self, sig: _ScanSignals, files: list, rois: list,
                 gen_box: list, my_gen: int):
        super().__init__()
        self._sig = sig
        self._files = files
        self._rois = rois
        self._gen_box = gen_box
        self._my_gen = my_gen
        self.setAutoDelete(True)

    @staticmethod
    def _make_sample(ts_ns: int, means: list, frame_mean: float,
                     contrasts: list, path: "Path | None") -> SamplePoint:
        return SamplePoint(ts_ns, path, means, frame_mean,
                           roi_contrasts=list(contrasts or []))

    def run(self):
        total = len(self._files)
        rects = [(r.x, r.y, r.w, r.h) for r in self._rois]
        tasks = [(ts, str(path)) for ts, path in self._files]
        buf: list = []
        done = 0

        last_tick = time.monotonic()

        def flush():
            if buf:
                self._sig.samples.emit(list(buf))
                buf.clear()

        def tick(force: bool = False):
            """Report progress at most every `_PROGRESS_S`, independent of the flush."""
            nonlocal last_tick
            now = time.monotonic()
            if force or (now - last_tick) >= self._PROGRESS_S:
                last_tick = now
                self._sig.progress.emit(done, total)

        try:
            executor = None
            if total >= self._MP_MIN:
                try:
                    n_workers = max(1, (os.cpu_count() or 2) - 1)
                    executor = ProcessPoolExecutor(
                        max_workers=n_workers, initializer=_pool_init,
                        initargs=(rects,))
                except Exception as exc:
                    self._sig.log_msg.emit(f"Multiprocessing unavailable ({exc}); using 1 core")
                    executor = None

            # `ex.map` yields results in submission order, so zipping with `tasks` pairs
            # each measurement back with the file it came from.
            if executor is not None:
                with executor as ex:
                    results = zip(ex.map(_measure_frame, tasks, chunksize=16), tasks)
                    for (ts_ns, means, frame_mean, contrasts), (_ts, src) in results:
                        if self._gen_box[0] != self._my_gen:
                            return
                        done += 1
                        if means is not None:
                            buf.append(self._make_sample(ts_ns, means, frame_mean,
                                                         contrasts, Path(src)))
                        if len(buf) >= self._BATCH:
                            flush()
                        tick()
            else:
                _pool_init(rects)
                for task in tasks:
                    if self._gen_box[0] != self._my_gen:
                        return
                    ts_ns, means, frame_mean, contrasts = _measure_frame(task)
                    done += 1
                    if means is not None:
                        buf.append(self._make_sample(ts_ns, means, frame_mean,
                                                     contrasts, Path(task[1])))
                    if len(buf) >= self._BATCH:
                        flush()
                    tick()

            flush()
            tick(force=True)
            if self._gen_box[0] == self._my_gen:
                self._sig.finished.emit()
        except Exception as exc:
            self._sig.error.emit(str(exc))


# ── ROI EDITOR ────────────────────────────────────────────────────────────────

class _ImageCanvas(QWidget):
    roi_selected = Signal(int)
    roi_created  = Signal()

    _HANDLE_PX = 7  # hit-test radius around resize handles in screen pixels

    def __init__(self, rois: list, parent=None):
        super().__init__(parent)
        self._rois = rois
        self._pm: "QPixmap | None" = None
        self._img_w = 0
        self._img_h = 0
        self._scale = 1.0
        self._off_x = 0
        self._off_y = 0
        self._selected_idx: "int | None" = None

        # Interaction state machine
        self._mode: str = ""          # "draw" | "move" | "resize" | ""
        self._drag_start: "QPoint | None" = None   # screen pos at press
        self._drag_last:  "QPoint | None" = None   # screen pos at last move (move mode)
        self._draw_end:   "QPoint | None" = None   # current rubber-band end
        self._resize_handle: str = ""              # e.g. "TL", "B", "R"
        self._drag_roi_origin: tuple = (0, 0, 0, 0)  # (x,y,w,h) when drag started

        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    @property
    def img_w(self) -> int:
        return self._img_w

    @property
    def img_h(self) -> int:
        return self._img_h

    def set_image(self, arr8: np.ndarray):
        h, w = arr8.shape[:2]
        if arr8.ndim == 2:
            qimg = QImage(bytes(arr8.data), w, h, w, QImage.Format.Format_Grayscale8)
        else:
            rgb = np.ascontiguousarray(arr8[:, :, :3])
            qimg = QImage(bytes(rgb.data), w, h, w * 3, QImage.Format.Format_RGB888)
        self._pm = QPixmap.fromImage(qimg.copy())
        self._img_w = w
        self._img_h = h
        self._update_tf()
        self.update()

    def _update_tf(self):
        if self._img_w == 0 or self._img_h == 0:
            return
        scale = min(self.width() / self._img_w, self.height() / self._img_h)
        self._scale = scale
        self._off_x = int((self.width() - self._img_w * scale) / 2)
        self._off_y = int((self.height() - self._img_h * scale) / 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_tf()
        self.update()

    # ── coordinate helpers ────────────────────────────────────────────────────

    def _d2i(self, px: int, py: int) -> "tuple[int, int]":
        if self._scale == 0:
            return 0, 0
        return int((px - self._off_x) / self._scale), int((py - self._off_y) / self._scale)

    def _i2d(self, ix: int, iy: int) -> QPoint:
        return QPoint(int(ix * self._scale + self._off_x),
                      int(iy * self._scale + self._off_y))

    def _roi_drect(self, roi: RoiDefinition) -> QRect:
        tl = self._i2d(roi.x, roi.y)
        br = self._i2d(roi.x + roi.w, roi.y + roi.h)
        return QRect(tl, br)

    def _hit_roi(self, px: int, py: int) -> "int | None":
        ix, iy = self._d2i(px, py)
        for i in range(len(self._rois) - 1, -1, -1):
            r = self._rois[i]
            if r.x <= ix < r.x + r.w and r.y <= iy < r.y + r.h:
                return i
        return None

    # ── handle helpers ────────────────────────────────────────────────────────

    def _handle_positions(self, roi: RoiDefinition) -> dict:
        """Returns {name: (screen_x, screen_y)} for the 8 resize handles."""
        tl = self._i2d(roi.x, roi.y)
        br = self._i2d(roi.x + roi.w, roi.y + roi.h)
        mx = (tl.x() + br.x()) // 2
        my = (tl.y() + br.y()) // 2
        return {
            "TL": (tl.x(), tl.y()), "T": (mx, tl.y()), "TR": (br.x(), tl.y()),
            "R":  (br.x(), my),
            "BR": (br.x(), br.y()), "B": (mx, br.y()), "BL": (tl.x(), br.y()),
            "L":  (tl.x(), my),
        }

    def _hit_handle(self, px: int, py: int, roi_idx: int) -> str:
        hs = self._HANDLE_PX
        for name, (hx, hy) in self._handle_positions(self._rois[roi_idx]).items():
            if abs(px - hx) <= hs and abs(py - hy) <= hs:
                return name
        return ""

    @staticmethod
    def _cursor_for_handle(handle: str) -> Qt.CursorShape:
        return {
            "TL": Qt.CursorShape.SizeFDiagCursor,
            "BR": Qt.CursorShape.SizeFDiagCursor,
            "TR": Qt.CursorShape.SizeBDiagCursor,
            "BL": Qt.CursorShape.SizeBDiagCursor,
            "T":  Qt.CursorShape.SizeVerCursor,
            "B":  Qt.CursorShape.SizeVerCursor,
            "L":  Qt.CursorShape.SizeHorCursor,
            "R":  Qt.CursorShape.SizeHorCursor,
        }.get(handle, Qt.CursorShape.SizeAllCursor)

    # ── mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        px, py = int(event.position().x()), int(event.position().y())

        # 1) Handle hit on selected ROI → resize
        if self._selected_idx is not None and 0 <= self._selected_idx < len(self._rois):
            handle = self._hit_handle(px, py, self._selected_idx)
            if handle:
                roi = self._rois[self._selected_idx]
                self._mode = "resize"
                self._resize_handle = handle
                self._drag_start = QPoint(px, py)
                self._drag_roi_origin = (roi.x, roi.y, roi.w, roi.h)
                self.setCursor(QCursor(self._cursor_for_handle(handle)))
                return

        # 2) Interior hit → select + prepare move
        hit = self._hit_roi(px, py)
        if hit is not None:
            self._selected_idx = hit
            self.roi_selected.emit(hit)
            roi = self._rois[hit]
            self._mode = "move"
            self._drag_start = QPoint(px, py)
            self._drag_last  = QPoint(px, py)
            self._drag_roi_origin = (roi.x, roi.y, roi.w, roi.h)
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            self.update()
            return

        # 3) Empty area → draw new ROI
        self._selected_idx = None
        self.roi_selected.emit(-1)
        self._mode = "draw"
        self._drag_start = QPoint(px, py)
        self._draw_end   = QPoint(px, py)
        self.update()

    def mouseMoveEvent(self, event):
        px, py = int(event.position().x()), int(event.position().y())

        if self._mode == "draw":
            self._draw_end = QPoint(px, py)
            self.update()
            return

        if self._mode == "move" and self._selected_idx is not None and self._drag_last:
            # Incremental delta from last position to avoid rounding drift
            ddx = px - self._drag_last.x()
            ddy = py - self._drag_last.y()
            dix = int(ddx / self._scale) if self._scale else 0
            diy = int(ddy / self._scale) if self._scale else 0
            if dix != 0 or diy != 0:
                roi = self._rois[self._selected_idx]
                roi.x = max(0, min(self._img_w - roi.w, roi.x + dix))
                roi.y = max(0, min(self._img_h - roi.h, roi.y + diy))
                self._drag_last = QPoint(px, py)
                self.update()
            return

        if self._mode == "resize" and self._selected_idx is not None and self._drag_start:
            # Compute total delta from drag start against stored origin
            dx_total = int((px - self._drag_start.x()) / self._scale) if self._scale else 0
            dy_total = int((py - self._drag_start.y()) / self._scale) if self._scale else 0
            ox, oy, ow, oh = self._drag_roi_origin
            x, y, w, h = ox, oy, ow, oh
            handle = self._resize_handle
            if "L" in handle:
                nw = ow - dx_total
                if nw >= 4:
                    x, w = ox + dx_total, nw
            if "R" in handle:
                nw = ow + dx_total
                if nw >= 4:
                    w = nw
            if "T" in handle:
                nh = oh - dy_total
                if nh >= 4:
                    y, h = oy + dy_total, nh
            if "B" in handle:
                nh = oh + dy_total
                if nh >= 4:
                    h = nh
            x = max(0, x); y = max(0, y)
            if self._img_w > 0: w = min(w, self._img_w - x)
            if self._img_h > 0: h = min(h, self._img_h - y)
            roi = self._rois[self._selected_idx]
            roi.x, roi.y, roi.w, roi.h = x, y, max(4, w), max(4, h)
            self.update()
            return

        # Hover (no button held) → update cursor
        if self._mode == "":
            self._update_hover_cursor(px, py)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._mode == "draw":
            end_px = int(event.position().x())
            end_py = int(event.position().y())
            if self._drag_start:
                dx = abs(end_px - self._drag_start.x())
                dy = abs(end_py - self._drag_start.y())
                if dx >= 5 and dy >= 5:
                    x0, y0 = self._d2i(min(self._drag_start.x(), end_px),
                                       min(self._drag_start.y(), end_py))
                    x1, y1 = self._d2i(max(self._drag_start.x(), end_px),
                                       max(self._drag_start.y(), end_py))
                    x0 = max(0, x0); y0 = max(0, y0)
                    if self._img_w > 0:
                        x1 = min(self._img_w, x1); y1 = min(self._img_h, y1)
                    roi = RoiDefinition(
                        name=f"Pulser_{len(self._rois) + 1}",
                        x=x0, y=y0, w=max(1, x1 - x0), h=max(1, y1 - y0),
                        color=_ROI_COLORS[len(self._rois) % len(_ROI_COLORS)],
                    )
                    self._rois.append(roi)
                    self._selected_idx = len(self._rois) - 1
                    self.roi_created.emit()
                    self.roi_selected.emit(self._selected_idx)
        self._mode = ""
        self._drag_start = self._drag_last = self._draw_end = None
        self._update_hover_cursor(int(event.position().x()), int(event.position().y()))
        self.update()

    def _update_hover_cursor(self, px: int, py: int):
        if self._selected_idx is not None and 0 <= self._selected_idx < len(self._rois):
            handle = self._hit_handle(px, py, self._selected_idx)
            if handle:
                self.setCursor(QCursor(self._cursor_for_handle(handle)))
                return
        hit = self._hit_roi(px, py)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor if hit is not None
                               else Qt.CursorShape.CrossCursor))

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        if self._pm and not self._pm.isNull():
            p.drawPixmap(self._off_x, self._off_y,
                         int(self._img_w * self._scale),
                         int(self._img_h * self._scale), self._pm)
        else:
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Load a reference image to define ROIs\n"
                       "(drag on empty area to draw · click to select · drag to move)")
        _ROI_BORDER = QColor(204, 0, 0)
        for i, roi in enumerate(self._rois):
            rect = self._roi_drect(roi)
            is_sel = (i == self._selected_idx)
            c = QColor(roi.color)
            p.setPen(QPen(_ROI_BORDER, 5 if is_sel else 2))
            p.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 55 if is_sel else 25)))
            p.drawRect(rect)
            font = QFont()
            font.setPointSize(8)
            font.setBold(is_sel)
            p.setFont(font)
            p.setPen(QColor(255, 255, 255) if is_sel else _ROI_BORDER)
            p.drawText(rect.adjusted(3, 2, 0, 0),
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, roi.name)
            # Draw handles on selected ROI
            if is_sel:
                p.setPen(QPen(QColor(255, 255, 255), 1))
                p.setBrush(QBrush(_ROI_BORDER))
                for hx, hy in self._handle_positions(roi).values():
                    p.drawRect(QRect(hx - 4, hy - 4, 8, 8))
        # Rubber band while drawing
        if self._mode == "draw" and self._drag_start and self._draw_end:
            p.setPen(QPen(_ROI_BORDER, 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            x0 = min(self._drag_start.x(), self._draw_end.x())
            y0 = min(self._drag_start.y(), self._draw_end.y())
            p.drawRect(QRect(x0, y0,
                             abs(self._draw_end.x() - self._drag_start.x()),
                             abs(self._draw_end.y() - self._drag_start.y())))
        p.end()

    def select(self, idx: "int | None"):
        self._selected_idx = idx
        self.update()

    def refresh(self):
        self.update()


class _RefImageSignals(QObject):
    ready  = Signal(object, object)  # (Path, np.ndarray)
    failed = Signal(str)


class _RefImageWorker(QRunnable):
    """Locate the latest reference image on the (possibly network) share and
    load it — off the UI thread so opening the editor never blocks the GUI."""
    def __init__(self, sig: _RefImageSignals, images_root: Path, cam_key: str, gen: int):
        super().__init__()
        self._sig = sig
        self._root = images_root
        self._cam_key = cam_key
        self._gen = gen
        self.setAutoDelete(True)

    def run(self):
        try:
            ref = _find_ref_image(self._root, self._cam_key)
            if not ref:
                self._sig.failed.emit("no-image")
                return
            arr = _load_as_float32_gray(ref)
            if arr is None:
                self._sig.failed.emit("load-failed")
                return
            self._sig.ready.emit(ref, arr)
        except Exception as exc:
            self._sig.failed.emit(str(exc))


class ROIEditorDialog(QDialog):
    def __init__(self, rois: list, parent=None,
                 cam_key: str = "", images_root: "Path | None" = None):
        super().__init__(parent)
        title = f"ROI Editor — {cam_key}" if cam_key else "ROI Editor"
        self.setWindowTitle(title)
        # Resizable + minimise/maximise buttons (QDialog hides them by default)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowMaximizeButtonHint)
        self.setSizeGripEnabled(True)
        self.resize(1100, 760)
        import copy
        self._rois: list = copy.deepcopy(rois)
        # Geometry as it was on open, so `result_rois` can tell which boxes actually moved
        # and refresh only those references.
        self._orig_geom: dict = {r.name: (r.x, r.y, r.w, r.h) for r in self._rois}
        self._selected_idx: "int | None" = None
        self._cam_key: str = cam_key
        self._images_root: "Path | None" = images_root
        self._gray_arr: "np.ndarray | None" = None  # last loaded image (float32 gray)
        # Whether the loaded image is a genuine all-alive reference. Only a reference may
        # be measured into ref_brightness/ref_contrast — see `_capture_reference`.
        self._loaded_is_reference: bool = False
        self._ref_sig: "_RefImageSignals | None" = None
        self._ref_gen: int = 0
        self._ref_loading: bool = False
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        tb = QHBoxLayout()
        btn_img = QPushButton("Load image")
        btn_img.setStyleSheet(_BTN_SM)
        btn_img.setToolTip(
            "Open any frame to check the boxes against. Viewing an image never changes\n"
            "the stored reference levels — use the capture buttons for that.")
        btn_img.clicked.connect(self._load_image_manual)
        tb.addWidget(btn_img)
        if self._cam_key and self._images_root:
            btn_auto_img = QPushButton("Load latest from share")
            btn_auto_img.setStyleSheet(_BTN_SM)
            btn_auto_img.setToolTip(
                "Fetch the most recent frame for this camera off the share, to see how\n"
                "the array looks right now. This frame may itself contain dead pulsers,\n"
                "so it is never used as a reference.")
            btn_auto_img.clicked.connect(lambda: self._auto_load_ref(initial=False))
            tb.addWidget(btn_auto_img)
        tb.addSpacing(6)
        if self._cam_key:
            btn_grid = QPushButton("Auto-create grid")
            btn_grid.setStyleSheet(_BTN_SM)
            btn_grid.setToolTip(
                "Discard the current boxes and detect a fresh grid from the loaded image.\n"
                "Asks first — the existing boxes are hand-tuned and cannot be recovered.")
            btn_grid.clicked.connect(self._auto_grid)
            tb.addWidget(btn_grid)
        btn_cap = QPushButton("Capture reference")
        btn_cap.setStyleSheet(_BTN_SM)
        btn_cap.setToolTip(
            "Store each box's level from the currently loaded image as this camera's\n"
            "reference — either the all-alive reference (every pulser lit) or the\n"
            "warm-up reference (whole array in warm-up mode).\n"
            "Boxes are not moved; only the measured levels are written.")
        btn_cap.clicked.connect(self._capture_reference)
        tb.addWidget(btn_cap)
        tb.addSpacing(6)
        tb.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setFixedWidth(110)
        self._name_edit.setPlaceholderText("ROI name")
        tb.addWidget(self._name_edit)
        btn_rename = QPushButton("Rename")
        btn_rename.setFixedWidth(68)
        btn_rename.clicked.connect(self._rename)
        tb.addWidget(btn_rename)
        tb.addSpacing(6)
        self._btn_del = QPushButton("Delete")
        self._btn_del.setStyleSheet(_BTN_DANGER_STYLE)
        self._btn_del.setFixedWidth(68)
        self._btn_del.clicked.connect(self._delete)
        self._btn_del.setEnabled(False)
        tb.addWidget(self._btn_del)
        tb.addStretch(1)
        btn_load_j = QPushButton("Load configuration")
        btn_save_j = QPushButton("Save configuration")
        btn_load_j.clicked.connect(self._load_json)
        btn_save_j.clicked.connect(self._save_json)
        tb.addWidget(btn_load_j)
        tb.addWidget(btn_save_j)

        self._hint = QLabel(self._hint_text())
        self._hint.setStyleSheet("font-size:10px;color:#555;")

        self._canvas = _ImageCanvas(self._rois, self)
        self._canvas.roi_selected.connect(self._on_selected)
        self._canvas.roi_created.connect(self._on_created)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay.addLayout(tb)
        lay.addWidget(self._hint)
        lay.addWidget(self._canvas, 1)
        lay.addWidget(btns)

    def showEvent(self, event):
        super().showEvent(event)
        if self._canvas.img_w != 0:
            return
        # Prefer the bundled all-alive reference: it is local (instant), it is the image
        # the stored levels were measured on, and unlike the newest frame off the share
        # it is guaranteed to have every pulser lit.
        ref = _bundled_ref(self._cam_key, "allgood")
        if ref is not None:
            self._load_image_from_path(ref, is_reference=True)
            return
        if self._cam_key and self._images_root:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(80, lambda: self._auto_load_ref(initial=True))

    def _hint_text(self) -> str:
        return (f"Drag to draw ROIs · Click to select · "
                f"{len(self._rois)} ROI(s) defined")

    def _auto_load_ref(self, initial: bool = False):
        """Find + load the latest reference image on a background thread so the
        (possibly slow, network) lookup never freezes the editor window.
        `initial` marks the one-shot lookup fired when the editor opens; its late
        result is discarded if an image has meanwhile been loaded, so it never
        overrides what the user is working on."""
        if not (self._cam_key and self._images_root) or self._ref_loading:
            return
        self._ref_loading = True
        self._ref_gen += 1
        self._hint.setText("Searching for reference image… (network)")
        self._ref_sig = _RefImageSignals(self)
        self._ref_sig.ready.connect(
            lambda path, arr, g=self._ref_gen, ini=initial: self._on_ref_ready(path, arr, g, ini))
        self._ref_sig.failed.connect(
            lambda why, g=self._ref_gen: self._on_ref_failed(why, g))
        worker = _RefImageWorker(self._ref_sig, self._images_root, self._cam_key, self._ref_gen)
        QThreadPool.globalInstance().start(worker)

    def _on_ref_ready(self, path: Path, arr, gen: int, initial: bool):
        if gen != self._ref_gen:
            return
        self._ref_loading = False
        # An initial (auto-on-open) load must not clobber an image the user has
        # already loaded or started working on.
        if initial and self._canvas.img_w != 0:
            self._hint.setText(self._hint_text())
            return
        self._display_image(path, arr)
        if not self._rois:
            self._auto_grid()

    def _on_ref_failed(self, why: str, gen: int):
        if gen != self._ref_gen:
            return
        self._ref_loading = False
        self._hint.setText(
            f"No qualifying image found for {self._cam_key} "
            f"(nothing above {MIN_IMAGE_BYTES // 1024} kB). "
            f"Load manually.")

    def _load_image_manual(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open image", "",
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*)")
        if path:
            self._load_image_from_path(Path(path))

    def _load_image_from_path(self, path: Path, is_reference: bool = False):
        try:
            arr = _load_as_float32_gray(path)
            if arr is None:
                raise ValueError("Could not read image")
            self._display_image(path, arr, is_reference)
        except Exception as exc:
            QMessageBox.warning(self, "Load image", f"Failed:\n{exc}")

    def _display_image(self, path: Path, arr: np.ndarray, is_reference: bool = False):
        # Bump generation so any still-running background auto-load is ignored
        self._ref_gen += 1
        self._ref_loading = False
        self._gray_arr = arr
        self._loaded_is_reference = is_reference
        lo, hi = float(arr.min()), float(arr.max())
        if hi > lo:
            arr8 = np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
        else:
            arr8 = np.zeros(arr.shape, dtype=np.uint8)
        self._canvas.set_image(arr8)
        tag = "all-alive reference" if is_reference else "not a reference"
        self._hint.setText(
            f"Loaded: {path.name}  ({arr.shape[1]}×{arr.shape[0]}, {tag})  —  "
            f"{self._hint_text()}")

    def _auto_grid(self):
        w, h = self._canvas.img_w, self._canvas.img_h
        if w == 0 or h == 0:
            QMessageBox.information(self, "Auto-create grid",
                                    "Load an image first.")
            return
        # The stored boxes are hand-tuned per camera and auto-detection cannot reproduce
        # them, so this is never allowed to happen by accident.
        if self._rois:
            reply = QMessageBox.question(
                self, "Auto-create grid",
                f"This discards all {len(self._rois)} existing ROI boxes for "
                f"{self._cam_key} and detects a new grid from the loaded image.\n\n"
                f"Hand-adjusted positions cannot be recovered. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        if self._gray_arr is not None and self._gray_arr.shape[:2] == (h, w):
            new_rois = _detect_grid_rois(self._gray_arr, self._cam_key)
            mode = "detected from image"
        else:
            new_rois = _make_default_rois(self._cam_key, w, h)
            mode = "even grid"
        self._rois.clear()
        self._rois.extend(new_rois)
        captured = ""
        if self._loaded_is_reference:
            self._fill_ref_brightness("allgood")
            captured = "; reference levels captured"
        self._selected_idx = None
        self._name_edit.clear()
        self._btn_del.setEnabled(False)
        self._canvas.select(None)
        self._canvas.refresh()
        self._hint.setText(
            f"Grid {mode}{captured} (cols: "
            f"{' '.join(CAM_COLS.get(self._cam_key, []))}, "
            f"rows: 1–{len(_ROW_LABELS)})  —  {self._hint_text()}")

    def _capture_reference(self):
        """Store the loaded image's per-box levels as the all-alive or warm-up reference.

        Capturing is an explicit action, never a side effect of loading or of pressing OK.
        It used to run automatically on OK, which meant opening the editor — which loads
        the newest frame off the share — and accepting silently replaced the all-alive
        reference with whatever the array happened to look like at that moment, dead
        pulsers included."""
        if self._gray_arr is None:
            QMessageBox.information(self, "Capture reference", "Load an image first.")
            return
        if not self._rois:
            QMessageBox.information(self, "Capture reference", "No ROI boxes defined.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Capture reference")
        box.setText("Store the levels measured in the loaded image as which reference "
                    f"for {self._cam_key}?")
        box.setInformativeText(
            "All-alive — a frame with every pulser lit. Sets each pulser's normal level,\n"
            "which every dropout is judged against.\n\n"
            "Warm-up — a frame with the whole array in warm-up mode. Sets how far below\n"
            "normal 'warming' sits, so warm-up time can be measured.")
        b_all = box.addButton("All-alive", QMessageBox.ButtonRole.AcceptRole)
        b_warm = box.addButton("Warm-up", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_all:
            kind, what = "allgood", "all-alive"
        elif clicked is b_warm:
            kind, what = "warmup", "warm-up"
        else:
            return
        self._fill_ref_brightness(kind)
        self._hint.setText(
            f"{what.capitalize()} reference captured for {len(self._rois)} ROIs  —  "
            f"{self._hint_text()}")

    def _fill_ref_brightness(self, kind: str = "allgood"):
        """Record each ROI's brightness AND ring-contrast in the loaded image.

        `kind='allgood'` stores the all-alive levels every pulser is scored against;
        `kind='warmup'` stores the same measurements taken while the array was warming.
        No-op if no image is loaded."""
        arr = self._gray_arr
        if arr is None:
            return
        for roi in self._rois:
            tile, _ring, contrast = _tile_floor_contrast(arr, roi.x, roi.y, roi.w, roi.h)
            if kind == "warmup":
                roi.warm_brightness = tile
                roi.warm_contrast = contrast
            else:
                roi.ref_brightness = tile
                roi.ref_contrast = contrast

    def _on_selected(self, idx: int):
        self._selected_idx = idx if idx >= 0 else None
        self._name_edit.setText(self._rois[idx].name if idx >= 0 else "")
        self._btn_del.setEnabled(idx >= 0)

    def _on_created(self):
        self._selected_idx = len(self._rois) - 1
        self._name_edit.setText(self._rois[self._selected_idx].name)
        self._hint.setText(self._hint_text())

    def _rename(self):
        if self._selected_idx is None:
            return
        name = self._name_edit.text().strip()
        if name:
            self._rois[self._selected_idx].name = name
            self._canvas.refresh()

    def _delete(self):
        if self._selected_idx is None:
            return
        del self._rois[self._selected_idx]
        self._selected_idx = None
        self._name_edit.clear()
        self._btn_del.setEnabled(False)
        self._canvas.select(None)
        self._hint.setText(self._hint_text())

    def _save_json(self):
        default = f"rois_{self._cam_key or 'cam'}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ROIs", default, "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "cam_key": self._cam_key,
                           "rois": [r.to_dict() for r in self._rois]}, f, indent=2)
        except Exception as exc:
            QMessageBox.warning(self, "Save ROIs", f"Failed:\n{exc}")

    def _load_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROIs", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            new_rois = [RoiDefinition.from_dict(r) for r in data.get("rois", [])]
            self._rois.clear()
            self._rois.extend(new_rois)
            self._selected_idx = None
            self._name_edit.clear()
            self._btn_del.setEnabled(False)
            self._canvas.select(None)
            self._canvas.refresh()
            self._hint.setText(self._hint_text())
        except Exception as exc:
            QMessageBox.warning(self, "Load ROIs", f"Failed:\n{exc}")

    def result_rois(self) -> list:
        """The edited ROIs, with the reference level refreshed for MOVED boxes only.

        This used to re-measure every box against whatever image was on screen, which
        quietly destroyed the all-alive reference whenever the editor was opened on a
        live frame and accepted. Now an untouched box keeps its stored level, and a box
        that was moved or resized — whose old level no longer describes the pixels it
        covers — is re-measured, but only against a genuine reference image."""
        moved = [r for r in self._rois
                 if self._orig_geom.get(r.name) != (r.x, r.y, r.w, r.h)]
        if not moved:
            return list(self._rois)
        if self._loaded_is_reference and self._gray_arr is not None:
            for roi in moved:
                tile, _ring, contrast = _tile_floor_contrast(
                    self._gray_arr, roi.x, roi.y, roi.w, roi.h)
                roi.ref_brightness = tile
                roi.ref_contrast = contrast
        elif any(r.ref_brightness for r in moved):
            QMessageBox.warning(
                self, "Reference out of date",
                f"{len(moved)} box(es) were moved or resized while a non-reference image "
                f"was loaded, so their stored reference levels no longer match the pixels "
                f"they cover.\n\nLoad the all-alive reference image and press "
                f"'Capture reference…' to bring them up to date.")
        return list(self._rois)


# ── RESULT TABS ───────────────────────────────────────────────────────────────

def _state_str(s: int) -> str:
    return {STATE_ON: "ON", STATE_OFF: "OFF", STATE_NODATA: "no-data"}.get(s, "—")


def _shade_excluded(ax, analysis: "CameraAnalysis | None", *, label: bool = True) -> bool:
    """Hatch the stretches the active-time gate left out. Returns True if any were drawn.

    Drawn on every time axis rather than left implicit: a gated result has real holes in
    it, and an unmarked hole reads as "we looked and nothing happened" instead of "we
    deliberately did not look". Hatched rather than merely tinted so it stays distinct
    from the solid grey of an actual array trip, which means the opposite thing."""
    if analysis is None or not getattr(analysis, "excluded", None):
        return False
    for j, (a_ns, b_ns) in enumerate(analysis.excluded.spans):
        ax.axvspan(mdates.date2num(_ns_to_dt(a_ns)), mdates.date2num(_ns_to_dt(b_ns)),
                   facecolor=EXCLUDED_CLR, alpha=0.13, hatch="///", edgecolor=EXCLUDED_CLR,
                   linewidth=0, zorder=0,
                   label="not analysed" if (label and j == 0) else None)
    return True


# Target width, in columns, for the state image on the Run Graph. A day at 3.3 Hz is about
# 110 000 frames; times forty pulsers that is 4.4 million quads for a `pcolormesh` that ends
# up a couple of thousand pixels wide, and it cost seconds of frozen UI on every redraw.
_STATE_MAX_COLS = 2400

# Which state wins when several frames share one column, worst first. WORST-STATE-WINS, not
# an average: a one-frame dropout inside a bucket of fifty must not be averaged away, because
# it is exactly the thing being looked for. The only cost is that a short event looks a little
# wider than it is, which is the right way round for something you are hunting.
_STATE_PRIORITY = (0, 3, 5, 2, 4, 6, 1)   # OFF, excluded, diodes-off, no-data, warm-up,
                                          # below full power, ON


def _decimate_states(disp: np.ndarray, x: np.ndarray) -> tuple:
    """(image, times) reduced to about `_STATE_MAX_COLS` columns, worst state per bucket."""
    n = disp.shape[1]
    if n <= _STATE_MAX_COLS or n == 0:
        return disp, x
    step = int(np.ceil(n / _STATE_MAX_COLS))
    pad = (-n) % step
    if pad:
        # Pad with ON so the tail bucket cannot invent an outage out of nothing.
        disp = np.concatenate([disp, np.full((disp.shape[0], pad), 1.0)], axis=1)
        x = np.concatenate([x, np.full(pad, x[-1])])
    cols = disp.shape[1] // step
    blocks = disp.reshape(disp.shape[0], cols, step)
    out = np.full((disp.shape[0], cols), 1.0)
    # Walk the priorities from least to most severe so the worst one written last wins.
    for value in reversed(_STATE_PRIORITY):
        hit = (blocks == value).any(axis=2)
        out[hit] = value
    return out, x.reshape(cols, step)[:, 0]


def _kind_str(kind: str) -> str:
    return {KIND_DROPOUT: "dropout", KIND_FAULT: "fault", KIND_TRIP: "trip",
            KIND_DEAD: "dead"}.get(kind, kind)


def _event_kind_str(ev) -> str:
    """The kind, plus "partial" for a blink that dimmed the pulser without putting it out."""
    base = _kind_str(ev.kind)
    return f"{base} (partial)" if getattr(ev, "partial", False) else base


def _fmt_ms(ts_ns: "int | None") -> str:
    """Prague time to the millisecond — a blink lasts one or two frames of 0.3 s, so to the
    second two of them in a row are indistinguishable."""
    if not ts_ns:
        return ""
    return _ns_to_dt(ts_ns).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _fmt_ms_in(ts_ns: "int | None", times_ns: "list | None") -> str:
    """`_fmt_ms`, without the date when the whole result lies within one day — a table
    column then has room for the milliseconds instead of cutting the time off."""
    if not ts_ns:
        return ""
    full = _fmt_ms(ts_ns)
    if times_ns and len(times_ns) > 1:
        if _ns_to_dt(times_ns[0]).date() == _ns_to_dt(times_ns[-1]).date():
            return full[11:]
    return full


def _fmt_dur_short(ns: "int | None") -> str:
    """`_fmt_dur`, but under a minute with tenths of a second: a blink is 0.3-1 s, and
    '0s' for every one of them says nothing."""
    if ns is not None and 0 <= ns < 60 * _NS_PER_S:
        return f"{ns / _NS_PER_S:.1f}s"
    return _fmt_dur(ns)


def _dead_label(st) -> str:
    """`NAME † 09:11` for a pulser still dark, `NAME † 09:11 → 15:40 (replaced)` for one
    that was swapped out and came back. Both are among the day's dead."""
    since = (_ns_to_dt(st.dead_since_ns).strftime("%m-%d %H:%M")
             if st.dead_since_ns else "?")
    if st.dead_until_ns:
        back = _ns_to_dt(st.dead_until_ns).strftime("%H:%M")
        return f"{st.name} † {since} → {back} (replaced)"
    return f"{st.name} † {since}"


# Status colouring for the map: green when a pulser never even flickered, through yellow and
# orange, to a light red for the worst one in this array — and full dark red for a pulser
# that died. Death is NOT the top of the gradient: it is a different statement, so it gets
# its own colour rather than "worst so far".
_STATUS_GRADIENT = ("#2E7D32", "#9CCC65", "#FDD835", "#FB8C00", "#EF5350")


# Colour per event kind, shared by the map timeline and the run graph. Ordered by severity,
# so a glance at a timeline reads the same way as a glance at the map.
_KIND_COLOR = {KIND_DROPOUT: "#FDD835", KIND_FAULT: "#FB8C00",
               KIND_TRIP: "#E64A19", KIND_DEAD: "#000000"}
WARMUP_CLR = "#FFB300"   # amber — array lit but below its normal level
# Slate — the diodes were simply not switched on. Deliberately unlike both the grey of a
# trip and the indigo of gate-excluded time: "warming", "it fell over" and "nobody switched
# it on" are three different things, and reading one as another is what this all came from.
DIODES_OFF_CLR = "#546E7A"
# Light cyan — lit, but not at full power: the ramps either end of a stretch, the low-power
# mode, a frame where the whole array changed level. Not judged, and unlike everything else
# here not a stop either.
BELOW_FULL_CLR = "#4DD0E1"


def _grid_layout_for(cam_key: str, rois: list):
    """Return (rows, cols, cell_index) mapping the physical 5x8 grid onto ROI indices.

    rows = ordered list of row labels, cols = ordered list of column labels,
    cell_index[(ri, ci)] = index into `rois` (missing pairs are simply absent).
    Falls back to a compact left-to-right / top-to-bottom grid if names don't parse.
    """
    name_to_idx = {r.name: i for i, r in enumerate(rois)}
    parsed = {}      # name -> (col_letters, row_number)
    row_nums, col_letters = set(), []
    ok = True
    for r in rois:
        m = re.match(r"^([A-Za-z]+)(\d+)$", r.name)
        if not m:
            ok = False
            break
        col, num = m.group(1), int(m.group(2))
        parsed[r.name] = (col, num)
        row_nums.add(num)
        if col not in col_letters:
            col_letters.append(col)
    if ok and parsed:
        cols = [c for c in CAM_COLS.get(cam_key, []) if c in col_letters]
        for c in col_letters:              # append any extra columns not in CAM_COLS
            if c not in cols:
                cols.append(c)
        rows = [str(n) for n in sorted(row_nums)]
        cell_index = {}
        for ri, rlbl in enumerate(rows):
            for ci, clbl in enumerate(cols):
                name = f"{clbl}{rlbl}"
                if name in name_to_idx:
                    cell_index[(ri, ci)] = name_to_idx[name]
        return rows, cols, cell_index
    # Fallback: pack sequentially into a grid sized from CAM_COLS.
    cols = CAM_COLS.get(cam_key, ["A", "B", "C", "D", "E"])
    n_cols = max(1, len(cols))
    n_rows = (len(rois) + n_cols - 1) // n_cols
    rows = [str(i + 1) for i in range(n_rows)]
    cell_index = {(i // n_cols, i % n_cols): i for i in range(len(rois))}
    return rows, cols, cell_index


class _MapTab(QWidget):
    """Interactive pulser map (heatmap of dropout counts) + dropout table + timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam_key = ""
        self._rois: list = []
        self._analysis: "CameraAnalysis | None" = None
        self._stats: list = []          # PulserStats aligned to rois
        self._rows_lbl: list = []
        self._cols_lbl: list = []
        self._cell_index: dict = {}
        self._sel_roi: "int | None" = None
        self._events: list = []         # events of the selected pulser
        self._sel_event = 0
        self._start_ns: "int | None" = None
        self._end_ns: "int | None" = None
        self._annot = None
        self._hint: "QLabel | None" = None
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # LEFT — map + colorbar
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        # One-line overview of the whole camera, always visible above the map so the
        # important totals (dead pulsers, array trips) are seen without opening Run Graph.
        self._map_summary = QLabel("No scan data yet")
        self._map_summary.setStyleSheet("font-weight:bold;color:#111;")
        self._map_summary.setWordWrap(True)
        left.addWidget(self._map_summary)
        self._map_fig = Figure(figsize=(5, 4))
        self._map_canvas = FigureCanvasQTAgg(self._map_fig)
        self._map_ax = self._map_fig.add_subplot(111)
        self._map_canvas.mpl_connect("motion_notify_event", self._on_hover)
        self._map_canvas.mpl_connect("figure_leave_event", self._hide_hint)
        self._map_canvas.mpl_connect("button_press_event", self._on_click)
        left.addWidget(self._map_canvas, 1)
        metric_row = QHBoxLayout()
        metric_row.addWidget(QLabel("Heatmap:"))
        self._metric_combo = _NoScrollComboBox()
        for label in ("Status", "Blinks", "Partial blinks", "Dropouts", "Faults", "Trips",
                      "Caused trips", "Total outages", "Uptime %"):
            self._metric_combo.addItem(label)
        self._metric_combo.setToolTip(
            "What the cell colour means:\n"
            "Status — dead (red) through to never-a-flicker (green), shaded by how many\n"
            "  times the pulser went out. A pulser that died and was replaced is red\n"
            "  with a hatch; an x marks one that took the whole array down.\n"
            "Blinks — every time the pulser went out at full power, full and partial\n"
            "  together; the cell says how many of them were partial.\n"
            "Partial blinks — the pulser dimmed below 'Dark below' but never below\n"
            "  'Full blink below'.\n"
            "Dropouts — brief flickers that came straight back.\n"
            "Faults — dark for longer while the array kept running, then back.\n"
            "Trips — dark for longer and the array went down right after.\n"
            "Caused trips — array outages this pulser was dark right up to.\n"
            "Total outages — faults + trips (flickers excluded).\n"
            "Uptime % — share of the array's full-power time the pulser was lit for.")
        # Default to Status: it now carries both the deaths and the event counts, so it is
        # the only view that answers "is this array healthy" in one glance.
        self._metric_combo.setCurrentText("Status")
        self._metric_combo.currentTextChanged.connect(lambda _t: self._draw_map())
        metric_row.addWidget(self._metric_combo, 1)
        left.addLayout(metric_row)
        map_btns = QHBoxLayout()
        btn_map = QPushButton("Export map (PNG)")
        btn_map.setStyleSheet(_BTN)
        btn_map.clicked.connect(self._export_map)
        map_btns.addWidget(btn_map)
        map_btns.addStretch(1)
        left.addLayout(map_btns)

        # RIGHT — dropout table + timeline
        right_w = QWidget()
        right = QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        self._sel_lbl = QLabel("Click a pulser in the map")
        self._sel_lbl.setStyleSheet("font-weight:bold;color:#111;")
        right.addWidget(self._sel_lbl)
        self._tbl = QTableWidget(0, 6)
        self._tbl.setHorizontalHeaderLabels(
            ["#", "Kind", "Start", "Recovery", "Duration", "Lowest score"])
        self._tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.verticalHeader().setVisible(False)     # the "#" column says it already
        self._tbl.setStyleSheet(_TABLE_CSS)
        self._tbl.itemSelectionChanged.connect(self._on_table_sel)
        right.addWidget(self._tbl, 1)

        self._tl_fig = Figure(figsize=(5, 1.4))
        self._tl_canvas = FigureCanvasQTAgg(self._tl_fig)
        self._tl_ax = self._tl_fig.add_subplot(111)
        right.addWidget(self._tl_canvas)

        nav = QHBoxLayout()
        btn_prev = QPushButton("◀")
        btn_prev.setFixedWidth(36)
        btn_prev.clicked.connect(lambda: self._step_event(-1))
        btn_next = QPushButton("▶")
        btn_next.setFixedWidth(36)
        btn_next.clicked.connect(lambda: self._step_event(1))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setEnabled(False)
        self._slider.valueChanged.connect(self._on_slider)
        self._ev_lbl = QLabel("—")
        self._ev_lbl.setStyleSheet("font-size:10px;color:#555;")
        nav.addWidget(btn_prev)
        nav.addWidget(self._slider, 1)
        nav.addWidget(btn_next)
        nav.addWidget(self._ev_lbl)
        right.addLayout(nav)

        btn_csv = QPushButton("Export events (CSV)")
        btn_csv.setStyleSheet(_BTN)
        btn_csv.clicked.connect(self._export_csv)
        right.addWidget(btn_csv)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left_w)
        split.addWidget(right_w)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split)

    # ── data / drawing ──────────────────────────────────────────────────────
    def update_data(self, cam_key: str, rois: list, analysis: "CameraAnalysis | None",
                    start_ns: "int | None", end_ns: "int | None"):
        self._cam_key = cam_key
        self._rois = rois
        self._analysis = analysis
        self._stats = analysis.stats if analysis else []
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._sel_roi = None
        self._events = []
        self._sel_event = 0
        self._draw_map()
        self._clear_selection()

    def _metric_value(self, st) -> float:
        metric = self._metric_combo.currentText()
        if metric == "Blinks":
            return float(st.blinks)
        if metric == "Partial blinks":
            return float(st.partial_blinks)
        if metric == "Dropouts":
            return float(st.dropouts)
        if metric == "Faults":
            return float(st.faults)
        if metric == "Trips":
            return float(st.trips)
        if metric == "Caused trips":
            return float(st.caused_trips)
        if metric == "Total outages":
            return float(st.total_dropouts)
        if metric == "Uptime %":
            return float(st.uptime_pct)
        if metric == "Status":
            # Severity on one scale: every dark run this pulser had, whatever its kind.
            # Death is handled separately in `_draw_map` — it is not a point on a gradient.
            return float(st.total_off_events)
        return float(st.dropouts)

    def _update_summary(self):
        """One-line camera overview shown above the map, so the key totals are visible
        without switching to Statistics."""
        stats = self._stats
        a = self._analysis
        n_dead = sum(1 for s in stats if s.is_dead)
        n_repl = sum(1 for s in stats if s.was_replaced)
        n_alive = sum(1 for s in stats if not s.is_dead)
        n_drop = sum(s.dropouts for s in stats)
        n_fault = sum(s.faults for s in stats)
        n_trip = sum(s.trips for s in stats)
        # Array outages and per-pulser trips are DIFFERENT NUMBERS and both used to be
        # called "trips": nine rows in the table next to a bar reading four. One counts
        # stretches where the array was down, the other counts pulsers that took it down —
        # one outage can be caused by two pulsers, or by none we can see. Both are labelled
        # for what they are, wherever they appear together.
        n_out = len(a.outages) if a else 0
        n_blink = sum(s.blinks for s in stats)
        n_part = sum(s.partial_blinks for s in stats)
        parts = [
            f"{self._cam_key}:",
            f"{n_blink} blinks" + (f" ({n_part} partial)" if n_part else ""),
            f"{n_alive} alive",
            f"{n_dead} dead" + (f" ({n_repl} replaced)" if n_repl else ""),
            f"{n_drop} dropouts",
            f"{n_fault} faults",
            f"{n_trip} pulser trips",
            f"{n_out} array outages",
            f"{a.array_restarts if a else 0} restarts",
        ]
        if a and a.diodes_off_total_ns:
            parts.append(f"switched off {_fmt_dur(a.diodes_off_total_ns)}")
        if a and a.warmup_windows:
            parts.append(f"warm-up {_fmt_dur(a.warmup_total_ns)} ({len(a.warmup_windows)}×)")
        text = "   ·   ".join(parts)
        worst = a.worst_blinkers(5) if a else []
        if worst:
            text += "\nBlinked most: " + ", ".join(f"{nm} {k}×" for nm, k in worst)
        if a and a.trips:
            text += "\nArray stops: " + a.stop_summary()
        if a and a.stretches:
            judged = sum(1 for s in a.stretches if s.judged)
            text += (f"\nJudged at full power: {_fmt_dur(a.array_up_ns)} in {judged} "
                     f"stretch(es)")
            if a.power is None:
                text += " (full power read off the images)"
        dead_bits = [_dead_label(s) for s in stats if s.is_dead]
        if dead_bits:
            text += f"\nDead: {', '.join(dead_bits)}"
        # Naming the suspected cause here is the whole point of the trip metric — it is
        # the first thing worth knowing when the array went down.
        blamed = [f"{n} ({t.start_ns and _ns_to_dt(t.start_ns).strftime('%m-%d %H:%M')})"
                  for t in (a.outages if a else []) for n in t.cause_names[:1]]
        if blamed:
            text += f"\nOutages likely caused by: {', '.join(blamed)}"
        self._map_summary.setText(text)

    def _draw_map(self):
        self._map_ax.clear()
        self._map_fig.clf()
        self._map_ax = self._map_fig.add_subplot(111)
        if not self._rois or not self._stats:
            self._map_summary.setText("No scan data yet")
            self._map_ax.text(0.5, 0.5, "No scan data yet", transform=self._map_ax.transAxes,
                              ha="center", va="center", color="#888", fontsize=12)
            self._map_canvas.draw()
            return
        self._update_summary()
        rows, cols, cell_index = _grid_layout_for(self._cam_key, self._rois)
        self._rows_lbl, self._cols_lbl, self._cell_index = rows, cols, cell_index
        n_rows, n_cols = len(rows), len(cols)
        metric = self._metric_combo.currentText()
        mat = np.full((n_rows, n_cols), np.nan)
        for (ri, ci), idx in cell_index.items():
            if idx < len(self._stats):
                mat[ri, ci] = self._metric_value(self._stats[idx])

        status_mode = (metric == "Status")
        if status_mode:
            # Two statements in one picture, so they get two colour treatments. How OFTEN a
            # pulser went out is a quantity, and gets a green-to-light-red gradient scaled to
            # the worst pulser IN THIS ARRAY. Whether it DIED is not a quantity, and gets its
            # own full dark red — putting death at the top of the same gradient would make it
            # depend on how bad the rest of the array happened to be that day.
            worst = max([s.total_off_events for s in self._stats] or [0])
            grad = LinearSegmentedColormap.from_list("pulser_status", _STATUS_GRADIENT)
            norm = Normalize(vmin=0, vmax=max(1, worst))
            rgba = np.zeros((n_rows, n_cols, 4))
            rgba[:] = to_rgba("#dddddd")          # no ROI for this cell
            dead_rgba = to_rgba(DANGER)
            for (ri, ci), idx in cell_index.items():
                if idx >= len(self._stats):
                    continue
                st = self._stats[idx]
                rgba[ri, ci] = dead_rgba if st.is_dead else grad(norm(mat[ri, ci]))
            im = self._map_ax.imshow(rgba, aspect="auto", interpolation="nearest",
                                     extent=[-0.5, n_cols - 0.5, n_rows - 0.5, -0.5])
            self._status_rgba, self._status_worst = rgba, worst
        else:
            cmap = matplotlib.colormaps[
                "YlGn" if metric == "Uptime %" else "YlOrRd"].copy()
            cmap.set_bad("#dddddd")
            vmax = np.nanmax(mat) if np.isfinite(np.nanmax(mat)) else 1
            im = self._map_ax.imshow(np.ma.masked_invalid(mat), cmap=cmap, aspect="auto",
                                     vmin=0, vmax=max(1, vmax), interpolation="nearest",
                                     extent=[-0.5, n_cols - 0.5, n_rows - 0.5, -0.5])
        self._map_ax.set_xticks(range(n_cols))
        self._map_ax.set_xticklabels(cols)
        self._map_ax.set_yticks(range(n_rows))
        self._map_ax.set_yticklabels(rows)
        self._map_ax.set_title(f"{self._cam_key} — {metric.lower()} per pulser  (click a cell)")
        # Label colour is picked per cell from that cell's own brightness. A fixed dark
        # label disappears on the dark end of YlGn/YlOrRd — which is exactly where the
        # interesting cells are (100 % uptime, or the highest dropout count).
        for (ri, ci), idx in cell_index.items():
            v = mat[ri, ci]
            if status_mode:
                cell = tuple(self._status_rgba[ri, ci])
            else:
                cell = im.cmap(im.norm(v)) if np.isfinite(v) else (1, 1, 1, 1)
            lum = 0.299 * cell[0] + 0.587 * cell[1] + 0.114 * cell[2]
            fg = "#fff" if lum < 0.55 else "#111"
            label = self._rois[idx].name
            if status_mode and idx < len(self._stats):
                # The count belongs in the cell: the shade says "worse than its neighbours",
                # the number says how much worse, and neither is guesswork from a colorbar.
                n_ev = self._stats[idx].total_off_events
                if n_ev:
                    label = f"{label}\n{n_ev}"
            elif idx < len(self._stats) and np.isfinite(v):
                st = self._stats[idx]
                if metric == "Uptime %":
                    label = f"{label}\n{v:.0f}%"
                elif metric == "Blinks" and st.partial_blinks:
                    label = f"{label}\n{int(v)} ({st.partial_blinks}p)"
                elif v:
                    label = f"{label}\n{int(v)}"
            self._map_ax.text(ci, ri, label, ha="center", va="center",
                              fontsize=7, color=fg, linespacing=1.4)
        # A pulser that died and was replaced is still red — it died today — but it is not
        # the same thing as one that is dark right now, so it is hatched rather than plain.
        for (ri, ci), idx in cell_index.items():
            if idx < len(self._stats) and self._stats[idx].was_replaced:
                self._map_ax.add_patch(Rectangle(
                    (ci - 0.5, ri - 0.5), 1, 1, fill=False, hatch="///",
                    edgecolor="#ffffff", linewidth=0, zorder=12))
        # Pulsers that took the whole array down get an X. On the non-status metrics the X
        # marks the dead instead: a "Dropouts" view cannot reveal them (a dead pulser has no
        # dropouts by definition), so without it a dead pulser would be invisible there.
        for (ri, ci), idx in cell_index.items():
            if idx >= len(self._stats):
                continue
            st = self._stats[idx]
            if (st.trips > 0) if status_mode else st.is_dead:
                self._map_ax.plot(ci, ri, marker="x", markersize=14, markeredgewidth=2.5,
                                  color="#000000", zorder=15)
        if status_mode:
            worst = self._status_worst
            grad = LinearSegmentedColormap.from_list("pulser_status", _STATUS_GRADIENT)
            handles = [Patch(color=DANGER, label="dead"),
                       Patch(facecolor=DANGER, hatch="///", edgecolor="#fff",
                             label="dead — replaced"),
                       Patch(color=grad(0.0), label="never out")]
            if worst > 0:
                handles.append(Patch(color=grad(1.0), label=f"worst ({worst}×)"))
            handles.append(Line2D([], [], marker="x", color="#000", linestyle="none",
                                  markersize=8, markeredgewidth=2, label="tripped the array"))
            self._map_ax.legend(handles=handles, fontsize=7, ncol=3, loc="upper center",
                                bbox_to_anchor=(0.5, -0.06))
        else:
            cbar = self._map_fig.colorbar(im, ax=self._map_ax, fraction=0.046, pad=0.04)
            cbar.set_label(metric)
        self._map_fig.tight_layout()
        self._map_canvas.draw()

    def _cell_at(self, event):
        if event.inaxes != self._map_ax or event.xdata is None or event.ydata is None:
            return None
        ci = int(round(event.xdata))
        ri = int(round(event.ydata))
        return self._cell_index.get((ri, ci))

    def _tooltip_text(self, idx: int) -> str:
        st = self._stats[idx]
        last_drop = (_ns_to_dt(st.last_drop_ns).strftime("%Y-%m-%d %H:%M")
                     if st.last_drop_ns else "—")
        a = self._analysis
        n_dropf = a.params.dropout_frames if a and a.params else 5
        lines = [
            f"{st.name}   [{st.status_str}]",
            f"Blinks: {st.blinks}"
            + (f"  ({st.full_blinks} full, {st.partial_blinks} partial)"
               if st.partial_blinks else ""),
            f"Dropouts (≤ {n_dropf} frames): {st.dropouts}",
            f"Faults: {st.faults}   Trips: {st.trips}",
            f"Caused array outages: {st.caused_trips}",
        ]
        if st.is_dead:
            lines.append("Dead: " + _dead_label(st).split(" † ", 1)[-1])
        if a is not None and a.score_matrix is not None and idx < a.score_matrix.shape[1]:
            # The score the STATES were decided from — including the per-frame drift
            # normalisation. Reading `signal/baseline` here instead showed a number that no
            # decision was ever made on.
            base = max(a.baselines[idx], 1e-9) if idx < len(a.baselines) else 1.0
            jud = [i for i, ok in enumerate(a.frame_judge or []) if ok]
            if jud:
                lines.append(f"Alive level: {base:.4f}   "
                             f"Last score: {float(a.score_matrix[jud[-1], idx]):.2f}")
        lines += [
            f"Longest run: {_fmt_dur(st.longest_run_ns)}",
            f"Longest run (no gaps): {_fmt_dur(st.longest_run_active_ns)}",
            f"Uptime (of array-up time): {st.uptime_pct:.1f}%",
            f"Dark while the array ran: {_fmt_dur(st.dark_time_ns)}",
            f"Last outage: {last_drop}",
            f"Total downtime: {_fmt_dur(st.total_down_ns)}",
            f"MTBF: {_fmt_dur(st.mtbf_ns)}",
            f"Frames: {st.frames}",
        ]
        if a is not None and a.warmup_windows:
            # Warm-up is a property of the whole array, so it is the same figure on every
            # cell — but it belongs here too, since it explains why this pulser's score
            # sat low for that stretch without ever being a dropout.
            lines.append(f"Array warm-up in window: {_fmt_dur(a.warmup_total_ns)} "
                         f"({len(a.warmup_windows)}×)")
        return "\n".join(lines)

    # The hover box used to be drawn inside the map itself, so a cell near the top or the
    # right edge pushed it outside the picture and it was cut off. It is now a plain label
    # laid over the whole window, kept inside the window on every side.
    def _ensure_hint(self) -> QLabel:
        if self._hint is None or self._hint.parent() is not self.window():
            self._hint = QLabel(self.window())
            self._hint.setObjectName("pulserHoverHint")
            self._hint.setTextFormat(Qt.PlainText)
            self._hint.setStyleSheet(
                "QLabel#pulserHoverHint{background:#ffffe0;color:#111111;"
                "border:1px solid #888888;padding:5px 7px;}")
            f = self._hint.font()
            f.setPointSizeF(8.5)
            self._hint.setFont(f)
            self._hint.hide()
        return self._hint

    def _hide_hint(self, *_a):
        if self._hint is not None:
            self._hint.hide()

    def _on_hover(self, event):
        idx = self._cell_at(event)
        if idx is None:
            self._hide_hint()
            return
        self._show_hint_at(self._tooltip_text(idx),
                           self.window().mapFromGlobal(QCursor.pos()))

    def _show_hint_at(self, text: str, pos: QPoint):
        hint = self._ensure_hint()
        hint.setText(text)
        hint.adjustSize()
        win = self.window()
        w, h = hint.width(), hint.height()
        x = pos.x() + 16
        y = pos.y() + 16
        if x + w > win.width() - 4:
            x = max(4, pos.x() - 16 - w)
        if y + h > win.height() - 4:
            y = max(4, pos.y() - 16 - h)
        hint.move(max(4, x), max(4, y))
        hint.raise_()
        hint.show()

    def hideEvent(self, event):
        self._hide_hint()
        super().hideEvent(event)

    def leaveEvent(self, event):
        self._hide_hint()
        super().leaveEvent(event)

    def _on_click(self, event):
        idx = self._cell_at(event)
        if idx is None:
            return
        self._select_pulser(idx)

    def _select_pulser(self, idx: int):
        self._sel_roi = idx
        st = self._stats[idx]
        self._events = list(st.events)
        self._sel_event = 0
        caused = f", caused {st.caused_trips} array outage(s)" if st.caused_trips else ""
        part = f" ({st.partial_blinks} partial)" if st.partial_blinks else ""
        self._sel_lbl.setText(
            f"{st.name} [{st.status_str}] — {st.blinks} blink(s){part}: "
            f"{st.dropouts} dropout(s), {st.faults} fault(s), {st.trips} trip(s), "
            f"uptime {st.uptime_pct:.1f}%{caused}")
        # fill table
        self._tbl.blockSignals(True)
        self._tbl.setRowCount(0)
        for i, ev in enumerate(self._events):
            r = self._tbl.rowCount()
            self._tbl.insertRow(r)
            tl = self._analysis.times_ns if self._analysis else None
            start_s = _fmt_ms_in(ev.start_ns, tl)
            rec_s = _fmt_ms_in(ev.recovery_ns, tl) if ev.recovery_ns else "— (still down)"
            dur_s = _fmt_dur_short(ev.duration_ns(self._end_ns or ev.start_ns))
            depth = f"{ev.min_score:.2f}" if np.isfinite(ev.min_score) else "—"
            for c, txt in enumerate([str(i + 1), _event_kind_str(ev), start_s, rec_s,
                                     dur_s, depth]):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._tbl.setItem(r, c, it)
        self._tbl.blockSignals(False)
        # slider
        self._slider.blockSignals(True)
        self._slider.setEnabled(bool(self._events))
        self._slider.setRange(0, max(0, len(self._events) - 1))
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._draw_timeline()
        if self._events:
            self._set_event(0)

    def _clear_selection(self):
        self._tbl.setRowCount(0)
        self._sel_lbl.setText("Click a pulser in the map")
        self._ev_lbl.setText("—")
        self._slider.setEnabled(False)
        self._draw_timeline()

    def _draw_timeline(self):
        """Events of the selected pulser, over its score.

        The score line is here rather than on the Run Graph because it is only readable one
        pulser at a time — forty of them in one axes was a smear. Here a pulser is already
        selected, so the line answers the question it is for: was this pulser judged fairly,
        and do the thresholds sit where they should for it?"""
        self._tl_ax.clear()
        if not self._events or self._start_ns is None or self._end_ns is None:
            self._tl_ax.set_yticks([])
            self._tl_ax.text(0.5, 0.5, "No events", transform=self._tl_ax.transAxes,
                             ha="center", va="center", color="#aaa", fontsize=9)
            self._tl_fig.tight_layout()
            self._tl_canvas.draw()
            return
        t0 = mdates.date2num(_ns_to_dt(self._start_ns))
        t1 = mdates.date2num(_ns_to_dt(self._end_ns))
        a = self._analysis
        self._tl_ax.set_xlim(t0, t1)
        # Warm-up stretches sit behind the events, so a dip that happened while the whole
        # array was below its normal level is visibly explained rather than mysterious.
        _shade_excluded(self._tl_ax, a, label=False)
        for w0, w1 in (a.warmup_windows if a else []):
            self._tl_ax.axvspan(mdates.date2num(_ns_to_dt(w0)),
                                mdates.date2num(_ns_to_dt(w1)),
                                color=WARMUP_CLR, alpha=0.18, zorder=0)
        for tr in (a.trips if a else []):
            self._tl_ax.axvspan(mdates.date2num(_ns_to_dt(tr.start_ns)),
                                mdates.date2num(_ns_to_dt(tr.end_ns)),
                                color=NODATA_CLR if tr.is_outage else DIODES_OFF_CLR,
                                alpha=0.22, zorder=0)
        drew_score = self._draw_score_strip()
        if not drew_score:
            self._tl_ax.set_ylim(0, 1)
            self._tl_ax.set_yticks([])
        for i, ev in enumerate(self._events):
            x = mdates.date2num(_ns_to_dt(ev.start_ns))
            is_sel = (i == self._sel_event)
            self._tl_ax.axvline(x, color=PRIMARY if is_sel else _KIND_COLOR.get(ev.kind, DANGER),
                                linewidth=2.5 if is_sel else 1.2,
                                alpha=1.0 if is_sel else 0.7)
        # adaptive ticks
        span_days = max(1e-6, t1 - t0)
        loc = _date_loc()
        self._tl_ax.xaxis.set_major_locator(loc)
        if span_days <= 1.5:
            fmt = "%H:%M"
        elif span_days <= 8:
            fmt = "%m-%d %Hh"
        else:
            fmt = "%m-%d"
        self._tl_ax.xaxis.set_major_formatter(_date_fmt(fmt))
        self._tl_ax.tick_params(labelsize=8)
        for lbl in self._tl_ax.get_xticklabels():
            lbl.set_rotation(20)
        self._tl_fig.tight_layout()
        self._tl_canvas.draw()

    def _draw_score_strip(self) -> bool:
        """The selected pulser's decision score, with the two thresholds. True if drawn.

        Plots `score_matrix` — the number the states were actually decided from, drift
        normalisation included. The view this replaces plotted `signal / baseline` instead, so
        its threshold lines corresponded to no decision the analyser had ever made."""
        a = self._analysis
        idx = self._sel_roi
        if (a is None or idx is None or a.score_matrix is None
                or idx >= a.score_matrix.shape[1] or not a.times_ns):
            return False
        x = np.array([mdates.date2num(_ns_to_dt(t)) for t in a.times_ns])
        judged = np.asarray((a.frame_judge or [])[:len(x)], dtype=bool)
        if judged.size < len(x):
            judged = np.concatenate([judged, np.zeros(len(x) - judged.size, dtype=bool)])
        if not judged.any():
            return False
        # Unjudged frames become NaN rather than 0: the score there was never used for
        # anything, and drawing it as a plunge to zero invents outages that did not happen.
        score = np.where(judged, a.score_matrix[:len(x), idx], np.nan)
        if not np.isfinite(score).any():
            return False
        p = a.params
        hi = p.blink_below if p else 0.80
        lo = p.full_below if p else 0.55
        top = float(np.nanmax(score))
        self._tl_ax.set_ylim(0, max(1.25, min(2.5, top * 1.1)))
        # Markers as well as the line: a blink is one or two frames, and a line alone draws
        # it as a hairline spike that vanishes at day scale.
        self._tl_ax.plot(x, score, color="#1565C0", linewidth=0.9, zorder=3)
        dark = np.where(score < hi, score, np.nan)
        self._tl_ax.plot(x, dark, linestyle="none", marker="o", markersize=3,
                         color=DANGER, zorder=4)
        self._tl_ax.axhline(hi, color=SUCCESS, linestyle="--", linewidth=1.0, zorder=2)
        self._tl_ax.axhline(lo, color=DANGER, linestyle="--", linewidth=1.0, zorder=2)
        self._tl_ax.set_yticks([0, lo, hi, 1.0])
        self._tl_ax.set_yticklabels(["0", f"full {lo:.2f}", f"dark {hi:.2f}", "1"],
                                    fontsize=7)
        self._tl_ax.set_ylabel("score", fontsize=7)
        return True

    def _set_event(self, idx: int):
        if not self._events:
            return
        self._sel_event = max(0, min(len(self._events) - 1, idx))
        ev = self._events[self._sel_event]
        self._ev_lbl.setText(f"{self._sel_event + 1}/{len(self._events)}  "
                             f"{_ns_to_dt(ev.start_ns).strftime('%m-%d %H:%M')}")
        self._slider.blockSignals(True)
        self._slider.setValue(self._sel_event)
        self._slider.blockSignals(False)
        self._tbl.blockSignals(True)
        self._tbl.selectRow(self._sel_event)
        self._tbl.blockSignals(False)
        self._draw_timeline()

    def _step_event(self, delta: int):
        self._set_event(self._sel_event + delta)

    def _on_slider(self, v: int):
        self._set_event(v)

    def _on_table_sel(self):
        r = self._tbl.currentRow()
        if r >= 0:
            self._set_event(r)

    # ── exports ─────────────────────────────────────────────────────────────
    def _export_map(self):
        if not self._rois or not self._stats:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export map", f"pulser_map_{self._cam_key}.png",
            "PNG (*.png);;All files (*)")
        if not path:
            return
        try:
            self._map_fig.savefig(path, dpi=150, bbox_inches="tight")
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")

    def _export_csv(self):
        if self._sel_roi is None:
            QMessageBox.information(self, "Export", "Click a pulser first.")
            return
        st = self._stats[self._sel_roi]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export events", f"events_{self._cam_key}_{st.name}.csv",
            "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["#", "kind", "partial", "start", "recovery", "duration_s",
                            "lowest_score", "dark_frames", "nodata_frames"])
                for i, ev in enumerate(st.events):
                    w.writerow([
                        i + 1, ev.kind, 1 if ev.partial else 0,
                        _fmt_ms(ev.start_ns), _fmt_ms(ev.recovery_ns),
                        f"{ev.duration_ns(self._end_ns or ev.start_ns) / _NS_PER_S:.1f}",
                        f"{ev.min_score:.3f}" if np.isfinite(ev.min_score) else "",
                        ev.dark_frames, ev.nodata_frames])
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")


class _RunGraphTab(QWidget):
    """Per-pulser state over time, with a time cursor.

    There used to be a second view here, "Show score lines": all forty pulsers' scores as
    lines in one axes. It is gone. Over a 110 000-frame day it drew forty smears along the
    bottom of an axis auto-scaled to 41 by a single outlier, under a legend covering a third
    of the plot, and it took seconds to redraw. It also plotted `signal / baseline` — without
    the per-frame drift normalisation the states were actually decided from — so the two
    threshold lines on it did not correspond to any decision the analyser had made. The one
    question it was for ("why was THIS pulser judged dead, and are the thresholds right for
    it?") is answerable one pulser at a time, and now lives under the map's timeline, where a
    pulser is already selected."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sample_points: list = []
        self._rois: list = []
        self._analysis: "CameraAnalysis | None" = None
        self._thr_high = 0.6
        self._thr_low = 0.4
        self._x: "np.ndarray | None" = None   # frame times as matplotlib date numbers
        self._cursor = None
        self._cur_annot = None
        self._pinned = False
        self._fig = Figure(figsize=(10, 4))
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._toolbar = _make_mpl_toolbar(NavigationToolbar2QT, self._canvas, self)
        self._ax = self._fig.add_subplot(111)
        self._canvas.mpl_connect("motion_notify_event", self._on_motion)
        self._canvas.mpl_connect("button_press_event", self._on_press)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        hint = QLabel("Hover for a time readout · click to pin it")
        hint.setStyleSheet("color:#444;")
        top.addWidget(hint)
        top.addStretch(1)
        btn_png = QPushButton("Export graph (PNG)")
        btn_png.setStyleSheet(_BTN)
        btn_png.clicked.connect(self._export_png)
        top.addWidget(btn_png)
        lay.addLayout(top)
        lay.addWidget(self._toolbar)
        lay.addWidget(self._canvas, 1)

    def update_data(self, sample_points: list, rois: list,
                    analysis: "CameraAnalysis | None", thr_high: float,
                    thr_low: float = 0.4):
        self._sample_points = sample_points
        self._rois = rois
        self._analysis = analysis
        self._thr_high = thr_high
        self._thr_low = thr_low
        self._pinned = False
        self._x = None          # new camera → the cached date numbers no longer apply
        self._redraw()

    def _redraw(self):
        self._ax.clear()
        self._cursor = self._cur_annot = None
        if not self._sample_points or not self._rois:
            self._ax.text(0.5, 0.5, "No scan data yet", transform=self._ax.transAxes,
                          ha="center", va="center", color="#888", fontsize=12)
            self._canvas.draw_idle()
            return
        self._draw_states()
        self._fig.tight_layout()
        self._canvas.draw_idle()

    def _draw_states(self):
        if self._analysis is None:
            self._ax.text(0.5, 0.5, "No analysis", transform=self._ax.transAxes,
                          ha="center", va="center", color="#888", fontsize=12)
            return
        a = self._analysis
        sm = a.state_matrix
        n_rois, n_times = sm.shape
        # OFF=0→0, ON=1→1, NODATA=-1→2, then the array-wide conditions overwrite whole
        # columns. Ordered least- to most-specific, so the more informative label wins:
        # "no data" is true of a warm-up frame as well, but "warming" says more, and
        # "nobody was looking" trumps everything because it is not an observation at all.
        disp = np.where(sm == STATE_NODATA, 2, sm).astype(float)

        def _col_mask(name) -> np.ndarray:
            src = list(getattr(a, name, []) or [])
            m = np.asarray(src[:n_times], dtype=bool)
            if m.size < n_times:
                m = np.concatenate([m, np.zeros(n_times - m.size, dtype=bool)])
            return m

        warm_m = _col_mask("frame_warmup")
        off_m = _col_mask("frame_diodes_off")
        gate_m = _col_mask("frame_excluded")
        below_m = _col_mask("frame_below_full")
        # Warm-up spans the FULL HEIGHT, not a band along the top edge. It is a property of
        # the whole array — every pulser is warming at once — and drawing it as a stripe over
        # one row said the opposite.
        disp[:, below_m] = 6
        disp[:, warm_m] = 4
        disp[:, off_m] = 5
        disp[:, gate_m] = 3
        # `x` stays at full resolution for everything drawn AT a frame — the lines and the
        # event markers index frames, and the decimated axis is a different length.
        x = self._frame_x()
        disp, xcell = _decimate_states(disp, x)
        # Real-time cell edges, so outage gaps (no-data columns) show at true width.
        if len(xcell) > 1:
            mid = (xcell[:-1] + xcell[1:]) / 2.0
            xedges = np.concatenate(([xcell[0] - (mid[0] - xcell[0])], mid,
                                     [xcell[-1] + (xcell[-1] - mid[-1])]))
        else:
            xedges = np.array([xcell[0] - 0.001, xcell[0] + 0.001])
        yedges = np.arange(n_rois + 1) - 0.5
        cmap = ListedColormap([DANGER, SUCCESS, NODATA_CLR, EXCLUDED_CLR,
                               WARMUP_CLR, DIODES_OFF_CLR, BELOW_FULL_CLR])
        norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5], cmap.N)
        self._ax.pcolormesh(xedges, yedges, disp, cmap=cmap, norm=norm, shading="flat")

        # Where the array was up at all: the two ends of the working period.
        jud = [i for i, ok in enumerate(getattr(a, "frame_judge", []) or []) if ok]
        if jud:
            for i, lbl in ((jud[0], "array up"), (jud[-1], "array down")):
                self._ax.axvline(x[i], color="#1565C0", linewidth=1.2, linestyle="--",
                                 alpha=0.9, zorder=6)
                self._ax.annotate(lbl, xy=(x[i], -0.5), xytext=(2, 2),
                                  textcoords="offset points", fontsize=7, color="#1565C0",
                                  rotation=90, va="bottom", zorder=6)
        # Only real outages get a line, and an unexplained one is drawn lighter so the ones
        # with a named culprit stand out. Deliberate stops are already a whole colour band.
        for trip in getattr(a, "trips", []):
            if not trip.is_outage:
                continue
            solid = bool(trip.caused_by)
            self._ax.axvline(mdates.date2num(_ns_to_dt(trip.start_ns)), color=DANGER,
                             linewidth=1.4 if solid else 0.9,
                             linestyle="-" if solid else (0, (4, 3)),
                             alpha=0.9 if solid else 0.55, zorder=7)
        # One marker per event on that pulser's own row, so the map's event list and this
        # graph can be read against each other without counting cells.
        by_kind: dict = {}
        t_ns = np.asarray(a.times_ns)
        for r, st in enumerate(a.stats[:n_rois]):
            for ev in st.events:
                k = int(np.searchsorted(t_ns, ev.start_ns, side="left"))
                key = "partial" if ev.partial else ev.kind
                by_kind.setdefault(key, []).append((x[min(k, len(x) - 1)], r))
                if ev.kind == KIND_DEAD and ev.recovery_ns:
                    kr = int(np.searchsorted(np.asarray(a.times_ns), ev.recovery_ns,
                                             side="left"))
                    by_kind.setdefault("replaced", []).append((x[min(kr, len(x) - 1)], r))
        for kind, pts in by_kind.items():
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if kind == "replaced":
                self._ax.plot(xs, ys, marker="o", linestyle="none", markersize=5,
                              markerfacecolor="none", markeredgecolor="#000000",
                              markeredgewidth=1.2, zorder=9)
            elif kind == "partial":
                # Hollow: the pulser dimmed, it did not go out.
                self._ax.plot(xs, ys, marker="v", linestyle="none", markersize=6,
                              markerfacecolor="#ffffff", markeredgecolor="#222",
                              markeredgewidth=0.9, zorder=9)
            else:
                self._ax.plot(xs, ys, marker="v", linestyle="none", markersize=6,
                              color=_KIND_COLOR.get(kind, DANGER),
                              markeredgecolor="#222", markeredgewidth=0.6, zorder=9)

        self._ax.set_ylim(n_rois - 0.5, -0.5)
        self._ax.set_yticks(range(n_rois))
        self._ax.set_yticklabels([r.name for r in self._rois], fontsize=8)
        self._ax.xaxis.set_major_formatter(_date_fmt("%m-%d %H:%M"))
        self._ax.xaxis.set_major_locator(_date_loc())
        self._fig.autofmt_xdate(rotation=30)
        part = f", {a.partial_total} partial" if a.partial_total else ""
        self._ax.set_title(
            f"Pulser state over time  ({a.blinks_total} blink(s){part}, "
            f"{len(a.outages)} array outage(s), {a.array_restarts} restart(s))", pad=30)
        self._ax.set_xlabel("Prague time")
        handles = [
            Patch(color=SUCCESS, label="ON"),
            Patch(color=DANGER, label="OFF"),
            Patch(color=NODATA_CLR, label="no data"),
        ]
        if below_m.any():
            handles.append(Patch(color=BELOW_FULL_CLR, label="below full power"))
        if warm_m.any():
            handles.append(Patch(color=WARMUP_CLR, label="warming up"))
        if off_m.any():
            handles.append(Patch(color=DIODES_OFF_CLR, label="switched off"))
        if gate_m.any():
            handles.append(Patch(color=EXCLUDED_CLR, label="not analysed"))
        if a.outages:
            handles.append(Line2D([], [], color=DANGER, linewidth=1.4, label="outage starts"))
        # One entry per kind of marker actually on the graph, in its own colour.
        for kind, lbl in ((KIND_DROPOUT, "blink (dropout)"), (KIND_FAULT, "blink (fault)"),
                          (KIND_TRIP, "blink (trip)"), (KIND_DEAD, "dead")):
            if kind in by_kind:
                handles.append(Line2D([], [], marker="v", color=_KIND_COLOR[kind],
                                      markeredgecolor="#222", linestyle="none",
                                      markersize=6, label=lbl))
        if "partial" in by_kind:
            handles.append(Line2D([], [], marker="v", markerfacecolor="#ffffff",
                                  markeredgecolor="#222", linestyle="none", markersize=6,
                                  label="partial blink"))
        # Outside the axes: every cell carries state, so a legend placed inside sits on top
        # of real data.
        self._ax.legend(handles=handles, fontsize=7, ncol=len(handles),
                        loc="lower center", bbox_to_anchor=(0.5, 1.0),
                        frameon=False, borderaxespad=0.2)

    def _frame_x(self) -> np.ndarray:
        """Frame times as matplotlib date numbers, computed once per analysis.

        A day at 3.3 Hz is ~110 000 frames, and this used to be a Python list
        comprehension re-run on every redraw — one of the two reasons a click cost seconds."""
        a = self._analysis
        if self._x is None or len(self._x) != len(a.times_ns):
            self._x = np.array([mdates.date2num(_ns_to_dt(t)) for t in a.times_ns])
        return self._x

    # ── TIME CURSOR ───────────────────────────────────────────────────────────
    def _readout(self, xdata: float, ydata: float) -> str:
        """Prague time under the cursor, plus what that pulser was doing then."""
        a = self._analysis
        x = self._frame_x()
        if a is None or x is None or not len(x):
            return ""
        i = int(np.clip(np.searchsorted(x, xdata), 0, len(x) - 1))
        if i > 0 and abs(x[i - 1] - xdata) < abs(x[i] - xdata):
            i -= 1
        lines = [_fmt_ms(a.times_ns[i])]
        # What the ARRAY was doing — the same question the colour band answers, in words.
        t_i = a.times_ns[i]
        stop = next((t for t in a.trips if t.start_ns < t_i <= max(t.end_ns, t.start_ns)),
                    None)
        if i < len(a.frame_excluded) and a.frame_excluded[i]:
            lines.append("not analysed")
        elif stop is not None and ((i < len(a.frame_diodes_off) and a.frame_diodes_off[i])
                                   or (i < len(a.frame_trip) and a.frame_trip[i])):
            names = [f"{n} ({stop.fates.get(n, '?')})" for n in stop.cause_names]
            who = (" — " + ", ".join(names)) if names else ""
            lines.append(f"array stop: {stop.kind_label}{who}")
        elif i < len(a.frame_warmup) and a.frame_warmup[i]:
            lines.append("warming up")
        elif i < len(a.frame_nodata) and a.frame_nodata[i]:
            lines.append("no data (archive gap)")
        elif i < len(a.frame_below_full) and a.frame_below_full[i]:
            lines.append("below full power — not judged")
        r = int(round(ydata))
        if 0 <= r < len(a.stats) and r < a.state_matrix.shape[0]:
            st = a.stats[r]
            state = _state_str(int(a.state_matrix[r, i]))
            sc = (a.score_matrix[i, r] if a.score_matrix is not None
                  and i < a.score_matrix.shape[0] and r < a.score_matrix.shape[1]
                  else np.nan)
            lines.append(f"{st.name}: {state}"
                         + (f"  (score {float(sc):.2f})" if np.isfinite(sc) else ""))
            # The nearest event on this row, so a click lands on something nameable.
            near = min((ev for ev in st.events),
                       key=lambda ev: abs(ev.start_ns - a.times_ns[i]), default=None)
            if near is not None:
                dt_s = abs(near.start_ns - a.times_ns[i]) / _NS_PER_S
                if dt_s <= 120:
                    lines.append(f"→ {_event_kind_str(near)} at "
                                 f"{_fmt_ms(near.start_ns)[11:]}")
        return "\n".join(lines)

    def _on_motion(self, event):
        if self._pinned or self._analysis is None:
            return
        self._move_cursor(event)

    def _on_press(self, event):
        """Click pins the readout, so a timestamp can be read off and kept while zooming —
        and clicking again releases it."""
        if self._analysis is None:
            return
        if event.inaxes != self._ax:
            return
        self._pinned = not self._pinned
        if self._pinned:
            self._move_cursor(event, force=True)

    def _move_cursor(self, event, force: bool = False):
        if event.inaxes != self._ax or event.xdata is None or event.ydata is None:
            if self._cursor is not None and not force:
                self._cursor.set_visible(False)
                if self._cur_annot is not None:
                    self._cur_annot.set_visible(False)
                self._canvas.draw_idle()
            return
        if self._cursor is None:
            self._cursor = self._ax.axvline(event.xdata, color="#0d47a1", linewidth=1.0,
                                            alpha=0.85, zorder=20)
            self._cur_annot = self._ax.annotate(
                "", xy=(0, 0), xytext=(12, -12), textcoords="offset points",
                bbox=dict(boxstyle="round", fc="#ffffe0", ec="#888", alpha=0.95),
                fontsize=8, zorder=21, annotation_clip=False)
        self._cursor.set_xdata([event.xdata, event.xdata])
        self._cursor.set_visible(True)
        self._cursor.set_linewidth(2.0 if self._pinned else 1.0)
        self._cur_annot.xy = (event.xdata, event.ydata)
        self._cur_annot.set_text(self._readout(event.xdata, event.ydata)
                                 + ("\n(pinned — click to release)" if self._pinned else ""))
        self._cur_annot.set_visible(True)
        self._canvas.draw_idle()

    def _export_png(self):
        if not self._sample_points:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export graph", "pulser_run_graph.png", "PNG (*.png);;All files (*)")
        if not path:
            return
        try:
            self._fig.savefig(path, dpi=150, bbox_inches="tight")
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")


class _NumericTableItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a separate numeric key while displaying
    formatted text.

    Qt's default QTableWidgetItem treats Qt::EditRole and Qt::DisplayRole as the
    SAME storage slot ("the default implementation treats Qt::EditRole and
    Qt::DisplayRole as referring to the same data" — Qt docs). Calling
    setData(EditRole, raw_number) after setText(formatted_text) therefore silently
    overwrites the formatted text with the raw number — which is why timestamps and
    durations were showing up as raw nanosecond integers instead of dates. This
    subclass keeps the two independent by overriding the sort comparison directly."""

    def __init__(self, text: str, sort_key=0):
        super().__init__(text)
        self._sort_key = sort_key

    def __lt__(self, other):
        if isinstance(other, _NumericTableItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)


# `Avail %` is gone. It read "(ON+OFF) / all frames", which nobody could interpret, and its
# neighbour `Uptime %` used a per-pulser denominator that handed 100 % to a pulser that ran
# 7.5 hours and to one that ran 5.75. Uptime is now over the array's own full-power time —
# one denominator for all forty — and the absolute dark time replaces the availability column.
# Two-line headings where a name is long: sixteen columns share the width equally, and a
# one-line "Longest run (no gaps)" was cut to "gest run (no g".
_ALLDATA_COLS = ["Array", "Pulser", "Blinks", "Partial", "Dropouts", "Faults", "Trips",
                 "Caused\noutages", "Uptime\n%", "Dark\ntime", "Longest run\n(no gaps)",
                 "First\noutage", "Last\noutage", "Died", "Back", "Status"]


def _alldata_row_cells(d: dict) -> list:
    """(display_text, sort_key_or_None) per column for one pulser's summary row."""
    def _t(key, fmt="%m-%d %H:%M"):
        return (_ns_to_dt(d[key]).strftime(fmt) if d[key] else "—")
    return [
        (d["array"], None), (d["name"], None),
        (str(d["blinks"]), d["blinks"]),
        (str(d["partial"]), d["partial"]),
        (str(d["dropouts"]), d["dropouts"]),
        (str(d["faults"]), d["faults"]),
        (str(d["trips"]), d["trips"]),
        (str(d["caused_trips"]), d["caused_trips"]),
        (f"{d['uptime']:.1f}%", d["uptime"]),
        (_fmt_dur(d["dark_time"]), d["dark_time"]),
        (_fmt_dur(d["longest_active"]), d["longest_active"]),
        (_t("first"), d["first"] or 0), (_t("last"), d["last"] or 0),
        (_t("died"), d["died"] or 0),
        (_t("back", "%H:%M"), d["back"] or 0),
        (d["status"], None),
    ]


def _write_alldata_csv(path: str, rows: list):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Array", "Pulser", "Blinks", "Partial blinks", "Dropouts", "Faults",
                    "Trips", "Caused array outages", "Uptime % (of array-up time)",
                    "Dark time", "Dark time (s)",
                    "Longest run no-gaps", "Longest run no-gaps (s)",
                    "First outage", "Last outage", "Died", "Back (replaced)", "Status"])
        for d in rows:
            def _t(key, fmt="%Y-%m-%d %H:%M"):
                return (_ns_to_dt(d[key]).strftime(fmt) if d[key] else "")
            w.writerow([
                d["array"], d["name"], d["blinks"], d["partial"],
                d["dropouts"], d["faults"], d["trips"],
                d["caused_trips"], f"{d['uptime']:.1f}",
                _fmt_dur(d["dark_time"]), int(d["dark_time"] // 1_000_000_000),
                _fmt_dur(d["longest_active"]), int(d["longest_active"] // 1_000_000_000),
                _t("first"), _t("last"), _t("died"), _t("back"), d["status"]])


class _AllDataTab(QWidget):
    """Per-pulser stats: one table for the camera currently being viewed, and a
    master table with every scanned camera together. All timestamps are Prague
    local time (via `_ns_to_dt`), never raw Unix nanoseconds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list = []          # every pulser, every scanned camera
        self._cam_key = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        self._sub_tabs = QTabWidget()
        self._tbl_cam = self._make_table()
        self._tbl_master = self._make_table()
        self._sub_tabs.addTab(self._tbl_cam, "This camera")
        self._sub_tabs.addTab(self._tbl_master, "All cameras (master)")
        lay.addWidget(self._sub_tabs, 1)

        btn_row = QHBoxLayout()
        btn_cam = QPushButton("Export this camera CSV")
        btn_cam.setStyleSheet(_BTN)
        btn_cam.clicked.connect(self._export_camera)
        btn_master = QPushButton("Export master CSV (all cameras)")
        btn_master.setStyleSheet(_BTN)
        btn_master.clicked.connect(self._export_master)
        btn_row.addWidget(btn_cam)
        btn_row.addWidget(btn_master)
        lay.addLayout(btn_row)

    @staticmethod
    def _make_table() -> QTableWidget:
        t = QTableWidget(0, len(_ALLDATA_COLS))
        t.setHorizontalHeaderLabels(_ALLDATA_COLS)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        t.setStyleSheet(_TABLE_CSS)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        return t

    def update_data(self, analysis_by_camera: dict):
        self._data = []
        for cam_key, analysis in analysis_by_camera.items():
            if not analysis:
                continue
            for st in analysis.stats:
                self._data.append({
                    "array": cam_key, "name": st.name,
                    "blinks": st.blinks, "partial": st.partial_blinks,
                    "dropouts": st.dropouts,
                    "faults": st.faults, "trips": st.trips,
                    "caused_trips": st.caused_trips,
                    "longest_active": st.longest_run_active_ns,
                    "uptime": st.uptime_pct, "dark_time": st.dark_time_ns,
                    "first": st.first_drop_ns, "last": st.last_drop_ns,
                    "died": st.dead_since_ns, "back": st.dead_until_ns,
                    "status": st.status_str,
                })
        self._fill(self._tbl_master, self._data)
        self.set_current_camera(self._cam_key)

    def set_current_camera(self, cam_key: str):
        """Called whenever the 'Viewing camera' selector changes — no rescan needed,
        just re-filter the already-computed master data."""
        self._cam_key = cam_key
        rows = [d for d in self._data if d["array"] == cam_key]
        self._fill(self._tbl_cam, rows)

    @staticmethod
    def _fill(table: QTableWidget, rows: list):
        table.setSortingEnabled(False)
        table.setRowCount(0)
        for d in rows:
            r = table.rowCount()
            table.insertRow(r)
            for c, (txt, sortval) in enumerate(_alldata_row_cells(d)):
                it = (_NumericTableItem(txt, sortval) if sortval is not None
                     else QTableWidgetItem(txt))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(r, c, it)
        table.setSortingEnabled(True)

    def _export_camera(self):
        rows = [d for d in self._data if d["array"] == self._cam_key]
        if not rows:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export this camera", f"pulser_all_data_{self._cam_key}.csv",
            "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            _write_alldata_csv(path, rows)
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")

    def _export_master(self):
        if not self._data:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export master data", "pulser_all_data.csv", "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            self.export_to(path)
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")

    def export_to(self, path: str):
        """Master export (every scanned camera) — kept as a plain method so
        'Export all (folder)' can call it directly."""
        _write_alldata_csv(path, self._data)


def _write_events_csv(path: str, cam_key: str, analysis: "CameraAnalysis"):
    """Per-event log: one row per dropout / fault / trip / dead run of every pulser.

    `array_fell` and `restarts` are the two look-forward measurements the kind was decided
    from, and `ended_by` says how the run finished. Together they make the verdict checkable
    from the CSV alone, which matters because the kind depends on what happened AFTER the
    event started."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["array", "pulser", "kind", "blink", "partial", "start", "recovery",
                    "duration_s", "lowest_score", "dark_frames", "nodata_frames",
                    "array_fell", "restarts", "ended_by"])
        end_ns = analysis.times_ns[-1] if analysis.times_ns else 0
        rows = sorted(((st, ev) for st in analysis.stats for ev in st.events),
                      key=lambda se: se[1].start_ns)
        for st, ev in rows:
            w.writerow([
                cam_key, st.name, ev.kind, 1 if ev.is_blink else 0,
                1 if ev.partial else 0,
                _fmt_ms(ev.start_ns), _fmt_ms(ev.recovery_ns),
                f"{ev.duration_ns(end_ns) / _NS_PER_S:.1f}",
                f"{ev.min_score:.3f}" if np.isfinite(ev.min_score) else "",
                ev.dark_frames, ev.nodata_frames,
                1 if ev.array_fell else 0, ev.restarts, ev.ended_by])


def _write_stretches_csv(path: str, cam_key: str, analysis: "CameraAnalysis"):
    """One row per stretch at full power — the table the operator checks a day by."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["array", "stretch", "from", "to", "length_s", "judged_s",
                    "low_power_n", "low_power_s", "dark_from_start", "blinks_full",
                    "blinks_partial", "unexplained_energy_dips", "blinks_per_pulser",
                    "reference_frames", "judged", "note"])
        for s in analysis.stretches:
            per = " ".join(f"{n}:{k}" for n, k in
                           sorted(s.per_pulser.items(), key=lambda nk: -nk[1]))
            w.writerow([
                cam_key, s.index, _fmt_ms(s.start_ns), _fmt_ms(s.end_ns),
                int(max(0, s.end_ns - s.start_ns) // _NS_PER_S),
                int(s.judged_ns // _NS_PER_S), s.low_power_n,
                f"{s.low_power_ns / _NS_PER_S:.1f}", " ".join(s.dark_from_start),
                s.blinks, s.partial, "" if s.unexplained < 0 else s.unexplained,
                per, s.ref_frames, 1 if s.judged else 0, s.note])
        if analysis.unexplained_dips:
            w.writerow([])
            w.writerow(["unexplained_energy_dip_at", "depth_pct"])
            for t, d in analysis.unexplained_dips:
                w.writerow([_fmt_ms(t), f"{d * 100:+.2f}"])


def _write_trips_csv(path: str, cam_key: str, analysis: "CameraAnalysis"):
    """Per array stop: what it was, when, for how long, who caused it and what became of
    them, who came back.

    `kind` and `is_outage` are what keep the deliberate stops out of the outage count while
    still recording them — reading this file, a row is an outage only when `is_outage` is 1.
    `back` 0 means full power never came back inside the window, so `duration_s` is only
    how long it was watched."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["array", "kind", "is_outage", "trip_start", "trip_end", "duration_s",
                    "restarted", "culprit", "culprit_dark_frames",
                    "n_recovered", "n_died", "died_pulsers", "kind_label", "culprit_fate",
                    "back"])
        for tr in analysis.trips:
            w.writerow([
                cam_key, tr.kind, 1 if tr.is_outage else 0,
                _ns_to_dt(tr.start_ns).strftime("%Y-%m-%d %H:%M:%S"),
                _ns_to_dt(tr.end_ns).strftime("%Y-%m-%d %H:%M:%S"),
                int(tr.duration_ns // 1_000_000_000),
                1 if tr.restarted else 0,
                " ".join(tr.cause_names),
                " ".join(str(f) for _n, f in tr.caused_by),
                len(tr.recovered), len(tr.died), " ".join(tr.died), tr.kind_label,
                " ".join(tr.fates.get(n, "") for n in tr.cause_names),
                1 if tr.back else 0])


def _write_gate_csv(path: str, start_ns: int, end_ns: int,
                    analysis: "CameraAnalysis") -> None:
    """What the exported figures actually cover.

    Every other file in an export is per OBSERVED time, and nothing in them says so — a
    trip log with two entries reads the same whether the window was watched throughout or
    was two thirds excluded. This file is what makes the rest of the bundle interpretable
    later, so it is written whenever a scan produced an analysis, gated or not."""
    ex = getattr(analysis, "excluded", None)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "value"])
        w.writerow(["window_start", _ns_to_dt(start_ns).strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["window_end", _ns_to_dt(end_ns).strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["window_s", int(max(0, end_ns - start_ns) // _NS_PER_S)])
        w.writerow(["gated", 0 if ex is None else 1])
        w.writerow(["observed_s", int(analysis.observed_total_ns // _NS_PER_S)])
        w.writerow(["excluded_s", int(analysis.excluded_total_ns // _NS_PER_S)])
        w.writerow(["high_power_gating_failed", 1 if analysis.gate_hpe_failed else 0])
        p = analysis.params
        if p is not None:
            w.writerow(["rule_hours", 1 if p.gate_hours else 0])
            w.writerow(["rule_hours_from", p.gate_hour_start])
            w.writerow(["rule_hours_to", p.gate_hour_end])
            w.writerow(["rule_high_power", 1 if p.gate_high_power else 0])
        for note in analysis.gate_notes:
            w.writerow(["note", note])
        w.writerow([])
        w.writerow(["excluded_from", "excluded_to", "duration_s"])
        for a_ns, b_ns in (ex.spans if ex is not None else []):
            w.writerow([_ns_to_dt(a_ns).strftime("%Y-%m-%d %H:%M:%S"),
                        _ns_to_dt(b_ns).strftime("%Y-%m-%d %H:%M:%S"),
                        int(max(0, b_ns - a_ns) // _NS_PER_S)])


def _write_warmup_csv(path: str, cam_key: str, analysis: "CameraAnalysis"):
    """Per warm-up window: when the whole array sat below its normal running level."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["array", "warmup_start", "warmup_end", "duration_s",
                    "warm_level", "level_source"])
        for a_ns, b_ns in analysis.warmup_windows:
            w.writerow([
                cam_key,
                _ns_to_dt(a_ns).strftime("%Y-%m-%d %H:%M:%S"),
                _ns_to_dt(b_ns).strftime("%Y-%m-%d %H:%M:%S"),
                int(max(0, b_ns - a_ns) // 1_000_000_000),
                f"{analysis.warm_level_used:.4f}", analysis.warm_level_source])


class _StatsTab(QWidget):
    """Per-array statistics: per-pulser event counts, events over time, the distribution of
    time between them, the array's own down-time log, and the full per-pulser fault log."""

    TRIP_COLS = ["Kind", "Start", "Duration", "Restarted", "Culprit",
                 "Recovered (#)", "Died (#)", "Died pulsers"]
    TRIP_COL_TIPS = [
        "What this stop of the array WAS — from full power ending to full power being\n"
        "back, warm-up included:\n"
        "  pulser trip — a pulser went dark right before it and lit again afterwards;\n"
        "  dead pulser — a pulser went dark right before it and never lit again after\n"
        "     the restarts, or only after a long pause (replaced);\n"
        "  bigger problem — a pulser took it down, and it stayed down past 'Outage up to';\n"
        "  manual stop — nothing went dark first, back within 'Manual stop up to';\n"
        "  outage, no pulser — nothing went dark first, back within 'Outage up to';\n"
        "  switched off — nothing went dark first and it stayed off longer (or for the\n"
        "     rest of the window).\n"
        "Manual stops and switched-off time are ours and are not outages; they are listed\n"
        "in grey so they can be seen without being counted.",
        "When full power ended.",
        "How long until full power was back. '— (not back)' when the window ended first.",
        "Yes = full power came back after this outage, i.e. the diodes had to be\n"
        "switched on again. Switching them back on after a stop of ours is not a\n"
        "recovery and is not counted as a restart.",
        "The pulser that took the array down: lit earlier, then dark right up to the stop\n"
        "for at least 'Culprit dark' — with what became of it: revived (lit again after a\n"
        "short pause), dead (never lit again after the restarts), replaced (lit again only\n"
        "after 'Replaced if back after'), not restarted (nothing switched on again in the\n"
        "window). '—' means no pulser took it down.",
        "How many pulsers that were alive before this outage were alive again after it.",
        "How many pulsers were alive before this outage and never came back after it.\n"
        "0 = the array fully recovered. Counted per up-period, so a pulser that failed\n"
        "this morning is named here once, not against every later outage of the day.",
        "Names of the pulsers counted in 'Died (#)'. '—' when none died here.\n"
        "A pulser that was already dark before this outage began is not counted — that is\n"
        "why the Recovered count can be short of the total pulser count.",
    ]

    FAULT_COLS = ["Pulser", "Kind", "Start", "Duration", "Lowest score", "Dark frames",
                  "Took array down", "Recoveries", "Ended by"]
    FAULT_COL_TIPS = [
        "Which pulser went dark.",
        "dropout — a flicker, no longer than 'Dropout up to' frames.\n"
        "fault — dark for longer, the array kept running, and it came back.\n"
        "trip — dark for longer and the array went down right after: this pulser took it.\n"
        "dead — either it survived 'Dead after (recoveries)' restarts without lighting, or\n"
        "it never lit at all today (see 'Never lit, dead after').\n"
        "Dropout, fault and trip are all blinks; '(partial)' marks one where the pulser\n"
        "dimmed but never went below 'Full blink below'.",
        "When the pulser went dark, to the millisecond.",
        "How long it stayed dark. For a death that ends the window, up to the last frame.",
        "The lowest score the pulser reached in this run — how far it went out, as a\n"
        "fraction of its own level at the start of the stretch. Below 'Full blink below'\n"
        "it went out; between that and 'Dark below' it only dimmed (a partial blink).",
        "How many frames of real data showed it dark. Compare with the duration: a big\n"
        "duration with few dark frames means most of that time had no data at all.",
        "Whether this pulser took the whole array down — it was dark right up to an array\n"
        "stop. This is what separates a 'trip' (or a death) from a 'fault'.",
        "How many array recoveries happened while this pulser stayed dark. Three is\n"
        "enough to call it dead — the operator often fires several in a row, so one\n"
        "failed recovery proves nothing.",
        "recovered — it lit again.\n"
        "replaced — it was dead and lit again anyway, so it was swapped out.\n"
        "end of window — it was still dark when the scan ended.",
    ]

    STRETCH_COLS = ["#", "From – to", "Length", "Low power inside", "Dark from start",
                    "Full blinks", "Partial", "Unexplained dips", "Blinks per pulser"]
    STRETCH_COL_TIPS = [
        "Stretch number. A stretch is one unbroken run at full power: every pulser is\n"
        "judged against its own brightness in the first minute of it.",
        "When full power began and ended (dump-bus voltage). The first 30 s and the\n"
        "last 15 s are the ramps and are not judged.",
        "How long the stretch lasted.",
        "Low-power stretches inside it (the array's energy below 80 % of its usual\n"
        "level, seen on any array) — how many, how long in total. Not judged.",
        "Pulsers already dark when the stretch began. They have no lit level of their\n"
        "own, so they are judged against the February all-alive reference — which is\n"
        "what calls them dead.",
        "Blinks in which the pulser went out (below 'Full blink below').",
        "Blinks in which the pulser only dimmed (below 'Dark below', never below\n"
        "'Full blink below').",
        "Dips in the array's energy deeper than 0.7 % in which no pulser was seen dark.\n"
        "The same dip on several arrays at once is the laser, not a pulser.\n"
        "'—' when the array's energy is not archived (PD2M1).",
        "Which pulsers blinked in this stretch, and how many times (full + partial).",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam_key = ""
        self._analysis: "CameraAnalysis | None" = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        top = QHBoxLayout()
        self._summary = QLabel("No scan data yet")
        self._summary.setStyleSheet("font-weight:bold;color:#111;")
        self._summary.setWordWrap(True)
        top.addWidget(self._summary, 1)
        btn_exp = QPushButton("Export stats (CSV)")
        btn_exp.setStyleSheet(_BTN)
        btn_exp.clicked.connect(self._export)
        top.addWidget(btn_exp)
        lay.addLayout(top)

        self._fig = Figure(figsize=(10, 3.2))
        self._canvas = FigureCanvasQTAgg(self._fig)
        lay.addWidget(self._canvas, 3)

        # Two logs, side by side, because they answer different questions and conflating
        # them is what made "9 trips" sit next to a bar reading "4": the left one is about
        # the ARRAY being down, the right one about a PULSER being dark.
        logs = QTabWidget()
        self._tbl = self._make_log(self.TRIP_COLS, self.TRIP_COL_TIPS)
        self._tbl_f = self._make_log(self.FAULT_COLS, self.FAULT_COL_TIPS)
        self._tbl_f.setSortingEnabled(True)
        self._tbl_s = self._make_log(self.STRETCH_COLS, self.STRETCH_COL_TIPS)
        self._tbl_s.verticalHeader().setVisible(False)   # the "#" column says it already
        # The per-pulser list is the one column that can run long; it takes the slack.
        hdr = self._tbl_s.horizontalHeader()
        for c in range(len(self.STRETCH_COLS) - 1):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(len(self.STRETCH_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        logs.addTab(self._tbl_s, "Stretches at full power")
        logs.addTab(self._tbl_f, "Pulser events")
        logs.addTab(self._tbl, "Array stops")
        lay.addWidget(logs, 2)

    @staticmethod
    def _make_log(cols: list, tips: list) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        for c, tip in enumerate(tips):
            hdr_item = t.horizontalHeaderItem(c)
            if hdr_item is not None:
                hdr_item.setToolTip(tip)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setStyleSheet(_TABLE_CSS)
        return t

    def update_data(self, cam_key: str, analysis: "CameraAnalysis | None"):
        self._cam_key = cam_key
        self._analysis = analysis
        self._redraw()
        self._fill_trips()

    @staticmethod
    def _gate_line(a: "CameraAnalysis") -> str:
        """What the numbers above actually cover.

        Stated first and unconditionally, because every count on this tab is per observed
        time: without it, "2 dropouts in a week" is unreadable — it could mean a quiet
        week or a week of which six days were never looked at."""
        if a.gate_hpe_failed:
            return ("\n⚠ Analysed time: high-power gating FAILED — "
                    + "; ".join(a.gate_notes or ["archiver unreachable"])
                    + ". The figures below still include high-power-off time.")
        if not getattr(a, "excluded", None):
            return "\nAnalysed time: the whole scanned window (no gate)"
        return (f"\nAnalysed time: {_fmt_dur(a.observed_total_ns)} observed, "
                f"{_fmt_dur(a.excluded_total_ns)} left out in {len(a.excluded)} period(s) "
                f"— {'; '.join(a.gate_notes) if a.gate_notes else 'gated'}. "
                f"Every figure below is per observed time.")

    @staticmethod
    def _power_line(a: "CameraAnalysis") -> str:
        """Where "full power" came from, and how much of it was judged. Without it a count
        of blinks cannot be read: the same number means something else over two judged
        hours than over seven."""
        p = a.params
        judged = sum(1 for s in a.stretches if s.judged)
        base = (f"\nJudged at full power: {_fmt_dur(a.array_up_ns)} in {judged} "
                f"stretch(es)")
        if a.power is not None:
            src = "dump bus"
            if a.power.has_energy:
                src += (f" + energy ({len(a.power.dips)} dip(s), "
                        f"{len(a.unexplained_dips)} with no pulser seen)")
            else:
                src += " (energy not archived — every frame read)"
            low_n = sum(s.low_power_n for s in a.stretches)
            low = (f"; {low_n} low-power stretch(es), "
                   f"{_fmt_dur(sum(s.low_power_ns for s in a.stretches))}, not judged"
                   if low_n else "")
            return f"{base} — from the {src}{low}"
        if a.power_failed:
            return (f"{base} — ⚠ the archiver could not say when the array was at full "
                    f"power; read off the images instead")
        if p is not None and not p.gate_full_power:
            return f"{base} — full-power gate off, read off the images"
        return base

    @staticmethod
    def _warm_line(a: "CameraAnalysis") -> str:
        """One line on warm-up, including the level actually used and where it came from.

        The separation between warming and running is only a few percent, so quoting the
        numbers is what lets the operator judge whether the split is trustworthy at all
        — a measured level that sits on top of 1.00 means the two references do not
        separate, and the figure should not be believed."""
        if a.warm_level_source == "none":
            return ("\nWarm-up: not measured — no warm-up reference image for this "
                    "camera (set 'Warm-up level' by hand to measure it anyway)")
        lvls = [v for v, nd in zip(a.array_level, a.frame_nodata) if not nd and v > 0]
        obs = ""
        if lvls:
            arr = np.asarray(lvls)
            obs = (f"; array level p5–p95 {np.percentile(arr, 5):.3f}–"
                   f"{np.percentile(arr, 95):.3f}")
        return (f"\nWarm-up: {_fmt_dur(a.warmup_total_ns)} in {len(a.warmup_windows)} "
                f"window(s) · level {a.warm_level_used:.3f} ({a.warm_level_source}){obs}")

    def _redraw(self):
        self._fig.clf()
        a = self._analysis
        if a is None or not a.stats:
            ax = self._fig.add_subplot(111)
            ax.text(0.5, 0.5, "No scan data yet", transform=ax.transAxes,
                    ha="center", va="center", color="#888", fontsize=12)
            ax.set_axis_off()
            self._canvas.draw()
            self._summary.setText("No scan data yet")
            return

        n_drop = sum(s.dropouts for s in a.stats)
        n_fault = sum(s.faults for s in a.stats)
        n_trip = sum(s.trips for s in a.stats)
        n_dead = sum(1 for s in a.stats if s.is_dead)
        n_repl = sum(1 for s in a.stats if s.was_replaced)
        n_alive = sum(1 for s in a.stats if not s.is_dead)
        n_out = len(a.outages)
        n_blink, n_part = a.blinks_total, a.partial_total
        dead_bits = [_dead_label(s) for s in a.stats if s.is_dead]
        dead_line = ("\nDead: " + ", ".join(dead_bits)) if dead_bits else ""
        worst = a.worst_blinkers(5)
        worst_line = ("\nBlinked most: " + ", ".join(f"{n} {k}×" for n, k in worst)
                      if worst else "")
        if a.trips:
            worst_line += "\nArray stops: " + a.stop_summary()
        warm_line = self._warm_line(a)
        gate_line = self._gate_line(a)
        off_bit = (f" · switched off {_fmt_dur(a.diodes_off_total_ns)}"
                   if a.diodes_off_total_ns else "")
        self._summary.setText(
            f"{self._cam_key}:  {n_blink} blinks"
            f"{f' ({n_part} partial)' if n_part else ''} · {n_alive} alive · {n_dead} dead"
            f"{f' ({n_repl} replaced)' if n_repl else ''} "
            f"· {n_drop} dropouts · {n_fault} faults · {n_trip} pulser trips "
            f"· {n_out} array outages · {a.array_restarts} restarts{off_bit}"
            f"{gate_line}{self._power_line(a)}{warm_line}{worst_line}{dead_line}")

        # Blinks per pulser, full and partial stacked — the question this tab is for first.
        ax1 = self._fig.add_subplot(131)
        ranked = sorted((s for s in a.stats if s.blinks), key=lambda s: -s.blinks)[:12]
        if ranked:
            names = [s.name for s in ranked]
            full = [s.full_blinks for s in ranked]
            part = [s.partial_blinks for s in ranked]
            ax1.bar(names, full, color=DANGER, label="full")
            ax1.bar(names, part, bottom=full, color="#FFCDD2", edgecolor=DANGER,
                    linewidth=0.6, label="partial")
            for i, s in enumerate(ranked):
                ax1.text(i, s.blinks, str(s.blinks), ha="center", va="bottom", fontsize=8)
            if any(part):
                ax1.legend(fontsize=7, loc="upper right")
            for lbl in ax1.get_xticklabels():
                lbl.set_rotation(45)
        else:
            ax1.text(0.5, 0.5, "no blinks", transform=ax1.transAxes,
                     ha="center", va="center", color="#aaa", fontsize=9)
        ax1.set_title(f"Blinks per pulser ({n_blink})", fontsize=9)
        ax1.set_ylabel("blinks", fontsize=8)
        ax1.tick_params(labelsize=8)

        # Blinks over time, across all pulsers, full and partial stacked.
        ax2 = self._fig.add_subplot(132)
        evs = [ev for s in a.stats for ev in s.events if ev.is_blink]
        if evs:
            xs_full = [mdates.date2num(_ns_to_dt(ev.start_ns)) for ev in evs
                       if not ev.partial]
            xs_part = [mdates.date2num(_ns_to_dt(ev.start_ns)) for ev in evs if ev.partial]
            lo = min(xs_full + xs_part)
            hi = max(xs_full + xs_part)
            if a.times_ns:
                lo = min(lo, mdates.date2num(_ns_to_dt(a.times_ns[0])))
                hi = max(hi, mdates.date2num(_ns_to_dt(a.times_ns[-1])))
            bins = np.linspace(lo, hi if hi > lo else lo + 1e-3, 25)
            ax2.hist([xs_full, xs_part], bins=bins, stacked=True,
                     color=[DANGER, "#FFCDD2"], edgecolor="#5a1010", linewidth=0.5,
                     label=["full", "partial"])
            ax2.xaxis.set_major_formatter(_date_fmt("%H:%M"))
            ax2.xaxis.set_major_locator(_date_loc())
            for lbl in ax2.get_xticklabels():
                lbl.set_rotation(25)
        else:
            ax2.text(0.5, 0.5, "no blinks", transform=ax2.transAxes,
                     ha="center", va="center", color="#aaa", fontsize=9)
        _shade_excluded(ax2, a, label=False)
        ax2.set_title("Blinks over time", fontsize=9)
        ax2.set_xlabel("Prague time", fontsize=8)
        ax2.set_ylabel("blinks", fontsize=8)
        ax2.tick_params(labelsize=8)

        # How long a pulser typically runs between two of its blinks — the GAPS BETWEEN
        # them, nothing to do with how long a blink lasted.
        ax3 = self._fig.add_subplot(133)
        gaps_min = []
        for s in a.stats:
            ts = [ev.start_ns for ev in s.events if ev.is_blink]
            gaps_min += [(b - c) / 6e10 for c, b in zip(ts, ts[1:])]
        if gaps_min:
            ax3.hist(gaps_min, bins=20, color="#1565C0", edgecolor="#0b3c7a")
        else:
            ax3.text(0.5, 0.5, "fewer than two blinks per pulser", transform=ax3.transAxes,
                     ha="center", va="center", color="#aaa", fontsize=9)
        ax3.set_title("Time between a pulser's blinks", fontsize=9)
        ax3.set_xlabel("minutes between consecutive blinks", fontsize=8)
        ax3.set_ylabel("how many times", fontsize=8)
        ax3.tick_params(labelsize=8)

        self._fig.tight_layout()
        self._canvas.draw()

    def _fill_trips(self):
        self._tbl.setRowCount(0)
        a = self._analysis
        if a is None:
            return
        for tr in a.trips:
            r = self._tbl.rowCount()
            self._tbl.insertRow(r)
            cause = (", ".join(f"{n} ({tr.fates.get(n, '?')})" for n in tr.cause_names)
                     if tr.caused_by else "—")
            cells = [
                tr.kind_label,
                _fmt_ms_in(tr.start_ns, a.times_ns)[:-4],
                _fmt_dur(tr.duration_ns) if tr.back else "— (not back)",
                "yes" if tr.restarted else "no",
                cause,
                str(len(tr.recovered)),
                str(len(tr.died)),
                " ".join(tr.died) if tr.died else "—",
            ]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if c != len(cells) - 1:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Grey the rows the array was down on purpose: they are here to be seen,
                # not to be counted among the outages.
                if tr.is_deliberate:
                    it.setForeground(QColor("#666"))
                self._tbl.setItem(r, c, it)
        self._fill_faults()
        self._fill_stretches()

    def _fill_stretches(self):
        """One row per stretch at full power — the day the way the operator checks it."""
        self._tbl_s.setRowCount(0)
        a = self._analysis
        if a is None:
            return
        for s in a.stretches:
            r = self._tbl_s.rowCount()
            self._tbl_s.insertRow(r)
            span = (f"{_ns_to_dt(s.start_ns).strftime('%H:%M:%S')} – "
                    f"{_ns_to_dt(s.end_ns).strftime('%H:%M:%S')}")
            low = (f"{s.low_power_n}×, {_fmt_dur_short(s.low_power_ns)}"
                   if s.low_power_n else "—")
            if s.judged:
                per = ", ".join(f"{n} {k}×" for n, k in
                                sorted(s.per_pulser.items(), key=lambda nk: -nk[1])) or "—"
                cells = [str(s.index), span, _fmt_dur(s.end_ns - s.start_ns), low,
                         ", ".join(s.dark_from_start) or "—", str(s.blinks), str(s.partial),
                         "—" if s.unexplained < 0 else str(s.unexplained), per]
            else:
                cells = [str(s.index), span, _fmt_dur(s.end_ns - s.start_ns), low,
                         "—", "—", "—", "—", s.note]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if c != len(cells) - 1:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if not s.judged:
                    it.setForeground(QColor("#666"))
                self._tbl_s.setItem(r, c, it)

    def _fill_faults(self):
        """Every dark run of every pulser, with the times and lengths — the log to check a
        fault against a trip against a death, which no aggregate count can answer."""
        # Sorting off while filling: Qt re-sorts on every insertRow, which moves rows out
        # from under the loop and leaves half-populated ones behind.
        self._tbl_f.setSortingEnabled(False)
        self._tbl_f.setRowCount(0)
        a = self._analysis
        if a is None:
            self._tbl_f.setSortingEnabled(True)
            return
        end_ns = a.times_ns[-1] if a.times_ns else 0
        # Every event, the flickers included: the blinks ARE the flickers, mostly — a
        # pulser going out for one or two frames — and a log without them listed nothing
        # but the deaths.
        rows = sorted(((s, ev) for s in a.stats for ev in s.events),
                      key=lambda se: se[1].start_ns)
        for st, ev in rows:
            r = self._tbl_f.rowCount()
            self._tbl_f.insertRow(r)
            dur = ev.duration_ns(end_ns)
            low = ev.min_score if np.isfinite(ev.min_score) else -1.0
            cells = [
                (st.name, None),
                (_event_kind_str(ev), None),
                (_fmt_ms_in(ev.start_ns, a.times_ns), ev.start_ns),
                (_fmt_dur_short(dur), dur),
                (f"{ev.min_score:.2f}" if low >= 0 else "—", low),
                (str(ev.dark_frames), ev.dark_frames),
                ("yes" if ev.array_fell else "no", int(ev.array_fell)),
                (str(ev.restarts), ev.restarts),
                (ev.ended_by, None),
            ]
            for c, (txt, key) in enumerate(cells):
                it = (_NumericTableItem(txt, key) if key is not None
                      else QTableWidgetItem(txt))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if ev.kind == KIND_DEAD:
                    it.setForeground(QColor(DANGER))
                self._tbl_f.setItem(r, c, it)
        self._tbl_f.setSortingEnabled(True)
        # In time order to start with; re-enabling sorting otherwise applies whatever column
        # was last clicked — on a fresh table, the pulser names backwards.
        self._tbl_f.sortItems(2, Qt.SortOrder.AscendingOrder)

    def _export(self):
        if self._analysis is None or not self._analysis.stats:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Export stats — choose folder")
        if not folder:
            return
        try:
            base = Path(folder)
            _write_events_csv(str(base / f"pulser_events_{self._cam_key}.csv"),
                              self._cam_key, self._analysis)
            _write_trips_csv(str(base / f"pulser_trips_{self._cam_key}.csv"),
                             self._cam_key, self._analysis)
            _write_stretches_csv(str(base / f"pulser_stretches_{self._cam_key}.csv"),
                                 self._cam_key, self._analysis)
            self._fig.savefig(str(base / f"pulser_stats_{self._cam_key}.png"),
                              dpi=150, bbox_inches="tight")
            QMessageBox.information(self, "Export", f"Saved stats for {self._cam_key} to:\n{folder}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")


# ── MAIN WIDGET ───────────────────────────────────────────────────────────────

class PulserMonitorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        now = datetime.now(tz)
        self._start_dt: datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._end_dt: datetime = now
        self._images_root: Path = IMAGES_ROOT_OPTIONS["Lab"]

        # Per-camera ROI definitions
        self._rois_by_camera: dict = {cam: [] for cam in CAMERAS}
        # Per-camera scan results
        self._results_by_camera: dict = {}
        # Per-camera analysis (states + stats), computed after each scan
        self._analysis_by_camera: dict = {}
        # Currently displayed results and ROIs (set when viewing a camera)
        self._results: list = []
        self._rois: list = []

        self._gen_box: list = [0]
        self._file_sig: "object | None" = None
        self._scan_sig: "object | None" = None
        self._gate_sig: "object | None" = None
        self._scan_queue: list = []          # cameras still to MEASURE
        self._enum_queue: list = []          # cameras still to ENUMERATE (phase 1)
        self._files_by_camera: dict = {}     # cam_key -> [(ts_ns, path), ...]
        self._total_frames = 0               # the progress bar's denominator: the whole scan
        self._frames_done = 0                # frames finished on the cameras already done
        self._cam_frames = 0                 # frames listed for the camera being measured
        self._current_scan_cam_key: str = ""
        # Active-time gate for the current scan (see the ACTIVE-TIME GATE section).
        # None = no gate, i.e. judge the whole window.
        self._active_spans: "_SpanSet | None" = None
        self._excluded_spans: "_SpanSet | None" = None
        self._gate_notes: list = []
        self._gate_hpe_failed: bool = False
        # Full-power gate for the current scan (see the FULL-POWER GATE section). None =
        # not asked for; a PowerInfo with `failed` = asked for, archiver could not say.
        self._power_info: "PowerInfo | None" = None
        self._power_sig: "object | None" = None
        self._scan_params: "AnalysisParams | None" = None

        self._build_ui()
        self._load_rois_config()

    def _build_ui(self):
        root_lay = QHBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── LEFT PANEL ────────────────────────────────────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(295)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lw = QWidget()
        left_scroll.setWidget(lw)
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(4)

        # Cameras
        lv.addWidget(_group_label("Cameras"))
        self._cam_checks: dict = {}
        self._roi_rows: dict = {}
        grid_w = QWidget()
        grid = QHBoxLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        col_a = QVBoxLayout()
        col_b = QVBoxLayout()
        for i, cam_key in enumerate(CAMERAS):
            chk = QCheckBox(cam_key)
            chk.setChecked(True)
            chk.setStyleSheet(_CHECKBOX_STYLE)
            chk.setToolTip(
                f"Include camera {cam_key} in the scan.\n"
                "Unticked cameras are skipped entirely (not read, not analysed).")
            chk.stateChanged.connect(
                lambda state, k=cam_key: self._set_roi_row_visible(k, state > 0))
            self._cam_checks[cam_key] = chk
            (col_a if i % 2 == 0 else col_b).addWidget(chk)
        grid.addLayout(col_a)
        grid.addLayout(col_b)
        grid.addStretch(1)
        lv.addWidget(grid_w)

        # Time window
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Time window"))
        btn_tw = QPushButton("Set time window")
        btn_tw.setStyleSheet(_BTN)
        btn_tw.setToolTip(
            "Pick the start and end date/hour of the period to scan.\n"
            "Only images whose timestamp falls inside this window are analysed.")
        btn_tw.clicked.connect(self._open_tw)
        lv.addWidget(btn_tw)
        self._tw_lbl = QLabel(self._tw_text())
        self._tw_lbl.setStyleSheet("font-size:10px;color:#555;")
        self._tw_lbl.setWordWrap(True)
        lv.addWidget(self._tw_lbl)

        # ROIs — one edit button per camera
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("ROIs  (click to edit per camera)"))
        self._roi_count_lbls: dict = {}
        for cam_key in CAMERAS:
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            btn = QPushButton(f"Edit {cam_key}")
            btn.setStyleSheet(_BTN_SM)
            btn.setFixedWidth(110)
            btn.setToolTip(
                f"Open the ROI editor for camera {cam_key}: draw/move the boxes marking\n"
                "each pulser tile and set the all-alive reference (Auto-create grid).\n"
                "The scan measures brightness inside these boxes.")
            btn.clicked.connect(lambda checked=False, k=cam_key: self._open_roi_editor(k))
            lbl = QLabel("0 ROIs")
            lbl.setStyleSheet("font-size:10px;color:#555;")
            self._roi_count_lbls[cam_key] = lbl
            row.addWidget(btn)
            row.addWidget(lbl)
            row.addStretch(1)
            self._roi_rows[cam_key] = row_w
            lv.addWidget(row_w)

        roi_json_row = QHBoxLayout()
        btn_load_all = QPushButton("Load configuration")
        btn_load_all.setFixedWidth(130)
        btn_load_all.setToolTip(
            "Load a saved ROI configuration (box positions + reference levels) for all\n"
            "cameras from a JSON file, replacing the current ROIs.")
        btn_load_all.clicked.connect(self._load_rois_json)
        roi_json_row.addWidget(btn_load_all)
        roi_json_row.addStretch(1)
        lv.addLayout(roi_json_row)
        hint_json = QLabel("Saves/loads ROI positions per camera.")
        hint_json.setStyleSheet("font-size:9px;color:#888;")
        hint_json.setWordWrap(True)
        lv.addWidget(hint_json)

        # Detection
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Detection"))
        # Each pulser is scored against its own level at the start of its stretch at full
        # power, divided by the array's level in that frame (see `_stretch_scores`).
        _tip_alive = (
            "A pulser is DARK in a frame when its score is below this.\n"
            "score = its brightness now / its brightness at the start of this stretch at\n"
            "full power, with the whole array's level in this frame divided out — so the\n"
            "array brightening or dimming together never reads as a blink, only one\n"
            "pulser moving against the rest.\n"
            "Every dark run of a pulser that was lit is one blink.\n"
            "\n"
            "Accepts 0.30 to 0.98, in steps of 0.05.\n"
            "Measured at full power: healthy pulsers stay within a few percent of 1.00,\n"
            "partial blinks (PD1 A8, PD4 E4/E7) fall to 0.51-0.75, full ones to\n"
            "0.25-0.55. Above ~0.90 ordinary ripple starts counting; below ~0.65 the\n"
            "partial blinks are missed.\n"
            "Default 0.80.")
        thr_row = QHBoxLayout()
        _lbl_alive = QLabel("Dark below:")
        _lbl_alive.setToolTip(_tip_alive)
        thr_row.addWidget(_lbl_alive)
        self._thr_sb = QDoubleSpinBox()
        self._thr_sb.setRange(0.30, 0.98)
        self._thr_sb.setSingleStep(0.05)
        self._thr_sb.setDecimals(2)
        self._thr_sb.setValue(0.80)
        self._thr_sb.setFixedWidth(75)
        self._thr_sb.setToolTip(_tip_alive)
        thr_row.addWidget(self._thr_sb)
        thr_row.addStretch(1)
        lv.addLayout(thr_row)
        _tip_off = (
            "A blink whose lowest score is below this is a FULL blink — the pulser went\n"
            "out. One that was dark (below 'Dark below') but never went below this is a\n"
            "PARTIAL blink: the pulser dimmed, it did not go out.\n"
            "Only sorts blinks into the two kinds; it does not change how many there are.\n"
            "\n"
            "Accepts 0.05 to 0.95, in steps of 0.05. Must stay below 'Dark below'.\n"
            "Measured: full blinks reach 0.25-0.55, partial ones 0.51-0.75.\n"
            "Default 0.55.")
        thr_lo_row = QHBoxLayout()
        _lbl_off = QLabel("Full blink below:")
        _lbl_off.setToolTip(_tip_off)
        thr_lo_row.addWidget(_lbl_off)
        self._thr_low_sb = QDoubleSpinBox()
        self._thr_low_sb.setRange(0.05, 0.95)
        self._thr_low_sb.setSingleStep(0.05)
        self._thr_low_sb.setDecimals(2)
        self._thr_low_sb.setValue(0.55)
        self._thr_low_sb.setFixedWidth(75)
        self._thr_low_sb.setToolTip(_tip_off)
        thr_lo_row.addWidget(self._thr_low_sb)
        thr_lo_row.addStretch(1)
        lv.addLayout(thr_lo_row)
        # The full-blink line has to stay under the dark line, or no blink could be partial
        # and the split would silently mean nothing.
        self._thr_sb.valueChanged.connect(
            lambda v: self._thr_low_sb.setValue(min(self._thr_low_sb.value(), v - 0.05)))
        self._thr_low_sb.valueChanged.connect(
            lambda v: self._thr_sb.setValue(max(self._thr_sb.value(), v + 0.05)))
        # Use the all-alive reference image's per-pulser brightness as the alive level.
        self._use_ref_chk = QCheckBox("Use reference baselines")
        self._use_ref_chk.setStyleSheet(_CHECKBOX_STYLE)
        self._use_ref_chk.setChecked(True)
        self._use_ref_chk.setToolTip(
            "Blend each pulser's brightness in the all-alive reference image into its\n"
            "alive level. Needed to flag a pulser that stayed dead the whole window\n"
            "(with no reference, a pulser dark the entire time looks like its own\n"
            "normal level and cannot be told apart from a live-but-dim one).\n"
            "Set the reference in the ROI editor via 'Auto-create grid'.\n"
            "\n"
            "On (default): recommended whenever the ROI configuration carries reference\n"
            "levels — without it a whole-window failure is invisible.\n"
            "Off: each pulser learns its alive level from the scanned window alone. Use\n"
            "only when the reference is stale (optics moved, camera gain changed), where\n"
            "it would otherwise drag every score off and fake dropouts across the array.\n"
            "Has no effect if the loaded configuration holds no reference levels.")
        lv.addWidget(self._use_ref_chk)
        # ── Active-time gate ──────────────────────────────────────────────────
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Analysed time"))
        _tip_hours = (
            "Judge only what happened between these hours, Prague local time.\n"
            "Outside them nobody is running the laser, so the array is dark for a\n"
            "perfectly good reason — and because the archiver keeps writing frames of it,\n"
            "an ungated overnight scan reports the whole night as an array trip and every\n"
            "pulser as dead. Time outside the window is left out of the analysis: it\n"
            "produces no dropouts and no trips, and it is subtracted from uptime,\n"
            "availability and downtime instead of counting against them.\n"
            "It also makes the scan faster — those hours are never read off the share.\n"
            "The excluded stretches are shaded on the Run Graph so nothing is hidden.")
        self._gate_hours_chk = QCheckBox(f"Only {GATE_HOUR_START:02d}:00–"
                                         f"{GATE_HOUR_END:02d}:00 (Prague)")
        self._gate_hours_chk.setChecked(True)
        self._gate_hours_chk.setToolTip(_tip_hours)
        lv.addWidget(self._gate_hours_chk)
        hours_row = QHBoxLayout()
        hours_row.addSpacing(18)
        self._gate_h0_sb = QSpinBox()
        self._gate_h0_sb.setRange(0, 23)
        self._gate_h0_sb.setValue(GATE_HOUR_START)
        self._gate_h0_sb.setFixedWidth(55)
        self._gate_h0_sb.setToolTip(_tip_hours)
        self._gate_h1_sb = QSpinBox()
        self._gate_h1_sb.setRange(1, 24)
        self._gate_h1_sb.setValue(GATE_HOUR_END)
        self._gate_h1_sb.setFixedWidth(55)
        self._gate_h1_sb.setToolTip(_tip_hours)
        # An end at or before the start would gate the entire window away and report
        # "nothing observed", so the two spin boxes are kept apart by at least an hour.
        self._gate_h0_sb.valueChanged.connect(
            lambda v: self._gate_h1_sb.setValue(max(self._gate_h1_sb.value(), v + 1)))
        self._gate_h1_sb.valueChanged.connect(
            lambda v: self._gate_h0_sb.setValue(min(self._gate_h0_sb.value(), v - 1)))
        hours_row.addWidget(QLabel("from"))
        hours_row.addWidget(self._gate_h0_sb)
        hours_row.addWidget(QLabel("to"))
        hours_row.addWidget(self._gate_h1_sb)
        hours_row.addStretch(1)
        lv.addLayout(hours_row)
        self._gate_hours_chk.toggled.connect(self._gate_h0_sb.setEnabled)
        self._gate_hours_chk.toggled.connect(self._gate_h1_sb.setEnabled)
        # The checkbox names the hours it is gating on, so both spin boxes have to refresh
        # it — otherwise the box keeps advertising 07:00–21:00 after they are changed.
        self._gate_hours_chk.toggled.connect(self._refresh_gate_label)
        self._gate_h0_sb.valueChanged.connect(self._refresh_gate_label)
        self._gate_h1_sb.valueChanged.connect(self._refresh_gate_label)

        _tip_hpe = (
            f"Judge only the time the operator's high-power key was on, read from the\n"
            f"archiver channel {HPE_CHANNEL}.\n"
            f"While it reads 0 the diodes are not meant to be firing, so a dark array is\n"
            f"the expected state and reporting it as a fault is simply wrong. This is the\n"
            f"stronger of the two rules — the key is usually switched on mid-morning and\n"
            f"off in the early evening, so it also catches days that started late or\n"
            f"ended early, which a fixed hour window cannot.\n"
            f"The channel only archives changes, so its history is fetched for the scanned\n"
            f"window plus a look-back for the state the window opened in.\n"
            f"Needs the archiver at {_CPVA_HOST}. If it cannot be reached the scan still\n"
            f"runs, with a warning in the log and on the Statistics tab — the results then\n"
            f"include high-power-off time and must be read with that in mind.")
        self._gate_hpe_chk = QCheckBox("Only while high power is enabled")
        self._gate_hpe_chk.setChecked(True)
        self._gate_hpe_chk.setToolTip(_tip_hpe)
        lv.addWidget(self._gate_hpe_chk)

        _tip_fp = (
            "Judge only while the array runs at full power, read from the archiver:\n"
            f"  {DUMPBUS_CHANNEL.format(k='<k>')} — at or above\n"
            f"  {FULL_POWER_V:g} V is full power (warm-up sits at 76-83 V);\n"
            f"  {ENERGY_CHANNEL.format(cam='PD<k>M1')} — catches the low-power\n"
            "  mode the voltage does not see, and every blink as a dip in the energy.\n"
            "The first 30 s and the last 15 s of each full-power stretch are the ramps and\n"
            "are not judged. Warm-up is read one frame per 5 s, just to measure it; while\n"
            "the voltage says the diodes are off, nothing is read at all.\n"
            "If the archiver cannot be reached, every frame is read and full power is\n"
            "judged from the images — the log and the Statistics tab then say so.")
        self._gate_fp_chk = QCheckBox("Only at full power (dump bus)")
        self._gate_fp_chk.setChecked(True)
        self._gate_fp_chk.setToolTip(_tip_fp)
        lv.addWidget(self._gate_fp_chk)
        _tip_eg = (
            "On the arrays whose energy is archived (PD1, PD3, PD4), read only the frames\n"
            "within 1.5 s of a dip in the energy, plus one frame every 5 s — every blink\n"
            "shows up as such a dip, so the blinks are all still read, at a fraction of the\n"
            "time. PD2M1's energy is not archived, so PD2M1 is always read frame by frame.\n"
            "Off: every frame at full power is read on every array — slower; use it to\n"
            "check a day against the fast reading.")
        self._energy_guided_chk = QCheckBox("Read only around energy dips (faster)")
        self._energy_guided_chk.setChecked(True)
        self._energy_guided_chk.setToolTip(_tip_eg)
        lv.addWidget(self._energy_guided_chk)
        self._gate_fp_chk.toggled.connect(self._energy_guided_chk.setEnabled)

        lv.addWidget(_hsep())
        # Gap factor: a jump in time > this × the median cadence = a trip (no images).
        _tip_gap = (
            "Trip detection from gaps in the image stream.\n"
            "The app measures the median spacing (cadence) between consecutive frames.\n"
            "If the time jump to the next image exceeds this many times that cadence,\n"
            "the array is assumed to have stopped acquiring (an array trip / no-data),\n"
            "not that every pulser dropped out. Lower = more sensitive to short gaps;\n"
            "higher = only long outages count as trips.\n"
            "\n"
            "Accepts 1.5 to 50.0, in steps of 0.5. It is a multiple of the measured\n"
            "cadence, so it holds whatever the frame rate is.\n"
            "Useful range 3-8. Below ~2.5 the normal jitter of the image stream is read\n"
            "as a trip on its own; above ~10 a short outage is silently swallowed and\n"
            "reported as every pulser dropping out at once instead.\n"
            "Default 4.0.")
        gap_row = QHBoxLayout()
        _lbl_gap = QLabel("Gap factor:")
        _lbl_gap.setToolTip(_tip_gap)
        gap_row.addWidget(_lbl_gap)
        self._gap_sb = QDoubleSpinBox()
        self._gap_sb.setRange(1.5, 50.0)
        self._gap_sb.setSingleStep(0.5)
        self._gap_sb.setDecimals(1)
        self._gap_sb.setValue(4.0)
        self._gap_sb.setFixedWidth(75)
        self._gap_sb.setToolTip(_tip_gap)
        gap_row.addWidget(self._gap_sb)
        gap_row.addStretch(1)
        lv.addLayout(gap_row)
        # No-data level: whole-frame brightness below this fraction of a NORMAL frame
        # means the frame carries no array data (blank / noise only).
        _tip_nd = (
            "Blank-frame threshold, as a fraction of a normal frame in this window.\n"
            "The app takes the median whole-frame brightness over the scanned window as\n"
            "'normal'; any frame dimmer than this fraction of it is treated as no-data /\n"
            "array-down rather than as every pulser dropping out at once. That is how a\n"
            "run of empty, noise-only images is recognised as an array trip.\n"
            "Relative, so it works regardless of camera or exposure.\n"
            "Keep it well below the warm-up level: while warming, the array sits near\n"
            "0.30 of normal, and a no-data level anywhere near that would report every\n"
            "warm-up stretch as an array trip.\n"
            "\n"
            "Accepts 0.01 to 0.90, in steps of 0.01.\n"
            "Useful range 0.08-0.18, i.e. the gap between the two measured levels: blank\n"
            "frames sit at ~0.04 of normal and warm-up at ~0.31. Below ~0.06 a noisy blank\n"
            "frame is no longer recognised as one; from ~0.25 up, every warm-up is\n"
            "misreported as an array trip.\n"
            "Default 0.12.")
        nd_row = QHBoxLayout()
        _lbl_nd = QLabel("No-data level:")
        _lbl_nd.setToolTip(_tip_nd)
        nd_row.addWidget(_lbl_nd)
        self._nodata_sb = QDoubleSpinBox()
        self._nodata_sb.setRange(0.01, 0.9)
        self._nodata_sb.setSingleStep(0.01)
        self._nodata_sb.setDecimals(2)
        self._nodata_sb.setValue(0.12)
        self._nodata_sb.setFixedWidth(75)
        self._nodata_sb.setToolTip(_tip_nd)
        nd_row.addWidget(self._nodata_sb)
        nd_row.addWidget(QLabel("× normal"))
        nd_row.addStretch(1)
        lv.addLayout(nd_row)
        # ── What a pulser's dark run is called ────────────────────────────────
        _tip_drop = (
            "A dark run of no more than this many data frames is a 'dropout' — a flicker:\n"
            "the pulser dimmed for a frame or two and came straight back. Longer than this\n"
            "and it is a 'fault' if the array kept running, or a 'trip' if the array went\n"
            "down right after (see 'Trip look-ahead').\n"
            "Nothing is ever discarded; this only decides which list a dark run lands in.\n"
            "\n"
            "Accepts 1 to 20 frames. Counted in data FRAMES, not seconds, because that is\n"
            "how the failure is described — but it does mean the wall-clock length of a\n"
            "dropout depends on the cadence of the scanned window (5 frames is ~1.5 s on\n"
            "the 3.3 Hz stream and ~25 s on the 5 s archive).\n"
            "Useful range 3-8. At 1 every single dark frame is reported as a fault; high\n"
            "values hide real outages inside the flicker count.\n"
            "Default 5.")
        drop_row = QHBoxLayout()
        _lbl_drop = QLabel("Dropout up to (frames):")
        _lbl_drop.setToolTip(_tip_drop)
        drop_row.addWidget(_lbl_drop)
        self._drop_sb = QSpinBox()
        self._drop_sb.setRange(1, 20)
        self._drop_sb.setValue(5)
        self._drop_sb.setFixedWidth(55)
        self._drop_sb.setToolTip(_tip_drop)
        drop_row.addWidget(self._drop_sb)
        drop_row.addStretch(1)
        lv.addLayout(drop_row)
        # The culprit of an array stop: dark for at least this long right up to it.
        _tip_cul = (
            "The CULPRIT of an array stop: a pulser that was lit earlier in the stretch,\n"
            "then went dark and stayed dark right up to the stop for at least this long.\n"
            "A stop with a culprit is a pulser trip (or a dead pulser, or a bigger\n"
            "problem) however short it was; one without is ours or unexplained.\n"
            "A pulser dark since the stretch began is never the culprit.\n"
            "Two values, because the frames come at two speeds: the 3.3 Hz stream, and\n"
            "the old archive at one frame per 5 s. The operator's numbers: 2-5 s on the\n"
            "fast stream, 30 s on the slow one — longer than the second or so in which\n"
            "the whole array dims as it is switched off.\n"
            "\n"
            "Accepts 0.5 to 120 s. Defaults 3 s and 30 s.")
        cul_row = QHBoxLayout()
        _lbl_cul = QLabel("Culprit dark at 3.3 Hz (s):")
        _lbl_cul.setToolTip(_tip_cul)
        cul_row.addWidget(_lbl_cul)
        self._cul_fast_sb = QDoubleSpinBox()
        self._cul_fast_sb.setRange(0.5, 120.0)
        self._cul_fast_sb.setSingleStep(0.5)
        self._cul_fast_sb.setDecimals(1)
        self._cul_fast_sb.setValue(3.0)
        self._cul_fast_sb.setFixedWidth(60)
        self._cul_fast_sb.setToolTip(_tip_cul)
        cul_row.addWidget(self._cul_fast_sb)
        cul_row.addStretch(1)
        lv.addLayout(cul_row)
        cul2_row = QHBoxLayout()
        _lbl_cul2 = QLabel("Culprit dark at 0.2 Hz (s):")
        _lbl_cul2.setToolTip(_tip_cul)
        cul2_row.addWidget(_lbl_cul2)
        self._cul_slow_sb = QDoubleSpinBox()
        self._cul_slow_sb.setRange(0.5, 120.0)
        self._cul_slow_sb.setSingleStep(5.0)
        self._cul_slow_sb.setDecimals(0)
        self._cul_slow_sb.setValue(30.0)
        self._cul_slow_sb.setFixedWidth(60)
        self._cul_slow_sb.setToolTip(_tip_cul)
        cul2_row.addWidget(self._cul_slow_sb)
        cul2_row.addStretch(1)
        lv.addLayout(cul2_row)
        # ── When a dark pulser is beyond recovery ────────────────────────────
        _tip_deadr = (
            "For a pulser that went dark WITHOUT taking the array down: how many array\n"
            "recoveries it may fail to come back from before it is called dead.\n"
            "One failed recovery proves nothing: the operator routinely fires several in a\n"
            "row, so a pulser still dark right after the first one may well light on the\n"
            "second or third.\n"
            "A pulser that DID take the array down is judged by what became of it instead:\n"
            "never lit again after the restarts — dead; lit again only after a long pause\n"
            "(see 'Replaced if back after') — dead, and replaced; lit again sooner —\n"
            "revived. The other way to be dead is never having lit at all (see 'Never lit,\n"
            "dead after').\n"
            "Switching the diodes back on after a stop of ours is not a recovery and is\n"
            "not counted here.\n"
            "\n"
            "Accepts 1 to 20 recoveries.\n"
            "Useful range 2-4. Default 3.")
        deadr_row = QHBoxLayout()
        _lbl_deadr = QLabel("Dead after (recoveries):")
        _lbl_deadr.setToolTip(_tip_deadr)
        deadr_row.addWidget(_lbl_deadr)
        self._deadr_sb = QSpinBox()
        self._deadr_sb.setRange(1, 20)
        self._deadr_sb.setValue(3)
        self._deadr_sb.setFixedWidth(55)
        self._deadr_sb.setToolTip(_tip_deadr)
        deadr_row.addWidget(self._deadr_sb)
        deadr_row.addStretch(1)
        lv.addLayout(deadr_row)
        _tip_deadm = (
            "The SECOND way of being dead: a pulser that never lit at all.\n"
            "It applies only to a pulser that is dark from the first frame we are allowed\n"
            "to judge and has never been properly lit since — it did not run today. This\n"
            "is how long the array must be seen running at full power before that is\n"
            "called dead rather than 'not seen yet'.\n"
            "It does NOT apply to a pulser that ran and then went dark. That one is a\n"
            "fault until somebody restarts the array and it fails to come back (see 'Dead\n"
            "after (recoveries)') — the array simply being up while nobody tries anything\n"
            "demonstrates nothing about it, however many hours pass.\n"
            "Measured in MINUTES, not frames, deliberately. Every frame-counted limit\n"
            "changes meaning by 16x between the 5 s archive cadence and the 3.3 Hz stream,\n"
            "and that is exactly how the one-second dimming ramp as the array was switched\n"
            "off for the evening came to be reported as eleven pulsers dying at 17:07.\n"
            "Time inside an outage or a switch-off does not count: the array was not\n"
            "running, so nothing was being demonstrated about the pulser.\n"
            "\n"
            "Accepts 0.5 to 240 minutes, in steps of 5.\n"
            "Useful range 10-40. Lower and a scan of a short window calls every pulser\n"
            "that has not lit yet dead; much higher and a pulser that plainly never ran\n"
            "all morning is still only a fault. Default 20.")
        deadm_row = QHBoxLayout()
        _lbl_deadm = QLabel("Never lit, dead after (min):")
        _lbl_deadm.setToolTip(_tip_deadm)
        deadm_row.addWidget(_lbl_deadm)
        self._deadm_sb = QDoubleSpinBox()
        self._deadm_sb.setRange(0.5, 240.0)
        self._deadm_sb.setSingleStep(5.0)
        self._deadm_sb.setDecimals(1)
        self._deadm_sb.setValue(20.0)
        self._deadm_sb.setFixedWidth(75)
        self._deadm_sb.setToolTip(_tip_deadm)
        deadm_row.addWidget(self._deadm_sb)
        deadm_row.addStretch(1)
        lv.addLayout(deadm_row)
        _tip_repl = (
            "A pulser that took the array down and lights again only after a pause at\n"
            "least this long was REPLACED — putting in a new pulser takes 40-90 minutes,\n"
            "or it happens overnight or over a weekend. It is counted among the dead,\n"
            "with the time it came back. After a shorter pause it revived and was not\n"
            "dead: it sometimes takes the second or third restart.\n"
            "\n"
            "Accepts 5 to 600 minutes. Default 30.")
        repl_row = QHBoxLayout()
        _lbl_repl = QLabel("Replaced if back after (min):")
        _lbl_repl.setToolTip(_tip_repl)
        repl_row.addWidget(_lbl_repl)
        self._repl_sb = QDoubleSpinBox()
        self._repl_sb.setRange(5.0, 600.0)
        self._repl_sb.setSingleStep(5.0)
        self._repl_sb.setDecimals(0)
        self._repl_sb.setValue(30.0)
        self._repl_sb.setFixedWidth(65)
        self._repl_sb.setToolTip(_tip_repl)
        repl_row.addWidget(self._repl_sb)
        repl_row.addStretch(1)
        lv.addLayout(repl_row)
        # ── What a stop of the array was ─────────────────────────────────────
        _tip_outmin = (
            "How long the data must stop, inside a stretch, for it to count as the array\n"
            "being down at all. Anything shorter is a hiccup in the archive: it still\n"
            "breaks a run, so nothing is joined across it, but it is not a stop.\n"
            "With the dump bus read from the archiver, a stop is wherever the voltage\n"
            "left full power, and this only applies inside a stretch.\n"
            "This is the single most important setting on the 3.3 Hz stream. The cadence\n"
            "there is 0.30 s, so without a floor in real seconds a 1.2 s stumble in the\n"
            "archive became an 'array trip' — one day reported 17 of them, and 17 restarts,\n"
            "where there had been about eight.\n"
            "\n"
            "Accepts 1 to 600 seconds.\n"
            "Useful range 30-120. Real outages last minutes; below ~10 s the archive's own\n"
            "stumbles reappear as outages. Default 60.")
        outmin_row = QHBoxLayout()
        _lbl_outmin = QLabel("Outage from (s):")
        _lbl_outmin.setToolTip(_tip_outmin)
        outmin_row.addWidget(_lbl_outmin)
        self._outmin_sb = QDoubleSpinBox()
        self._outmin_sb.setRange(1.0, 600.0)
        self._outmin_sb.setSingleStep(5.0)
        self._outmin_sb.setDecimals(0)
        self._outmin_sb.setValue(60.0)
        self._outmin_sb.setFixedWidth(75)
        self._outmin_sb.setToolTip(_tip_outmin)
        outmin_row.addWidget(self._outmin_sb)
        outmin_row.addStretch(1)
        lv.addLayout(outmin_row)
        _tip_trig = (
            "No pulser took the array down and full power is back within this — somebody\n"
            "switched it off by hand. Recorded, shown on the graph, and NOT counted as an\n"
            "outage or a restart. A stop is measured from the voltage leaving full power\n"
            "to it being back at full power, warm-up included.\n"
            "\n"
            "Accepts 0.5 to 30 minutes. Default 3 (the operator's number).")
        trig_row = QHBoxLayout()
        _lbl_trig = QLabel("Manual stop up to (min):")
        _lbl_trig.setToolTip(_tip_trig)
        trig_row.addWidget(_lbl_trig)
        self._trig_sb = QDoubleSpinBox()
        self._trig_sb.setRange(0.5, 30.0)
        self._trig_sb.setSingleStep(0.5)
        self._trig_sb.setDecimals(1)
        self._trig_sb.setValue(3.0)
        self._trig_sb.setFixedWidth(65)
        self._trig_sb.setToolTip(_tip_trig)
        trig_row.addWidget(self._trig_sb)
        trig_row.addStretch(1)
        lv.addLayout(trig_row)
        _tip_off = (
            "An ordinary outage is cleared within this. Beyond it, a stop a pulser caused\n"
            "is a BIGGER PROBLEM, and one nobody caused means the diodes were switched off\n"
            "— for the day, or on purpose. Switched off is not downtime: nobody was trying\n"
            "to run the array, so it is never an outage, never a restart, and never a\n"
            "reason to call a pulser dead.\n"
            "Between 'Manual stop up to' and this, a stop nobody caused is an OUTAGE with\n"
            "no pulser to blame.\n"
            "\n"
            "Accepts 1 to 120 minutes. Default 8 (the operator's number).")
        off_row = QHBoxLayout()
        _lbl_off = QLabel("Outage up to (min):")
        _lbl_off.setToolTip(_tip_off)
        off_row.addWidget(_lbl_off)
        self._off_sb = QDoubleSpinBox()
        self._off_sb.setRange(1.0, 120.0)
        self._off_sb.setSingleStep(1.0)
        self._off_sb.setDecimals(0)
        self._off_sb.setValue(8.0)
        self._off_sb.setFixedWidth(65)
        self._off_sb.setToolTip(_tip_off)
        off_row.addWidget(self._off_sb)
        off_row.addStretch(1)
        lv.addLayout(off_row)
        # The manual band has to stay inside the outage band.
        self._trig_sb.valueChanged.connect(
            lambda v: self._off_sb.setValue(max(self._off_sb.value(), v)))
        self._off_sb.valueChanged.connect(
            lambda v: self._trig_sb.setValue(min(self._trig_sb.value(), v)))
        # Warm-up level: array level below this (relative to normal) = warming up.
        _tip_warm = (
            "Warm-up level, as a fraction of the array's normal running level.\n"
            "Warm-up is not a ramp — the whole array simply sits at a second, much lower\n"
            "level: measured at 0.27–0.35 of normal on all four cameras, and repeatable\n"
            "to better than a percent from day to day. Frames whose array level falls\n"
            "below the midpoint between this value and 1.0 count as warm-up time.\n"
            "Nothing about a pulser is judged inside a warm-up stretch: warm-up contributes\n"
            "minutes and nothing else. Scoring pulsers against the warm level instead looks\n"
            "reasonable and is quietly disastrous — the correction multiplies the score by\n"
            "about 3.6, so a genuinely dead pulser comes out looking alive, and its death is\n"
            "then dated to whenever the array next tripped.\n"
            "\n"
            "Accepts 'auto' (the lowest setting) or 0.001 to 0.999, in steps of 0.01.\n"
            "'auto' derives it from the per-camera warm-up reference image next to the app\n"
            "('<camera prefix>-…-warmup.png'); any other value overrides that and is what\n"
            "you need when no such reference exists for the camera.\n"
            "Useful range 0.25-0.40, the band measured on all four cameras. Set it too low\n"
            "and warm-up frames read as forty simultaneous dropouts; too high (near 0.6+)\n"
            "and normal running time is misreported as warm-up. It must also stay well\n"
            "above 'No-data level', or blank frames are counted as warm-up.\n"
            "The Statistics tab shows the level actually measured, so you can check that\n"
            "the two levels really do separate.\n"
            "Default 'auto'.")
        warm_row = QHBoxLayout()
        _lbl_warm = QLabel("Warm-up level:")
        _lbl_warm.setToolTip(_tip_warm)
        warm_row.addWidget(_lbl_warm)
        self._warm_sb = QDoubleSpinBox()
        self._warm_sb.setRange(0.0, 0.999)
        self._warm_sb.setSingleStep(0.01)
        self._warm_sb.setDecimals(3)
        self._warm_sb.setValue(0.0)
        self._warm_sb.setSpecialValueText("auto")
        self._warm_sb.setFixedWidth(75)
        self._warm_sb.setToolTip(_tip_warm)
        warm_row.addWidget(self._warm_sb)
        warm_row.addWidget(QLabel("× normal"))
        warm_row.addStretch(1)
        lv.addLayout(warm_row)
        # Debounce: OFF must persist this many data frames to count at all.
        _tip_db = (
            "Noise suppression: OFF runs shorter than this many data frames are flipped\n"
            "back to ON and vanish from the results entirely.\n"
            "Unlike 'Dropout up to', which only decides how a dark run is labelled, this\n"
            "deletes it: anything erased here is counted nowhere.\n"
            "\n"
            "Accepts 1 to 20 frames.\n"
            "1 = off (default) — every dark frame is kept, short ones being reported as\n"
            "dropouts. Raise this ONLY to silence sensor noise; at 2 or more, single-frame\n"
            "flickers are erased before they can be counted, and each further step hides\n"
            "longer real outages. Keep it at or below 'Dropout up to', or dropouts cannot\n"
            "occur at all.")
        db_row = QHBoxLayout()
        _lbl_db = QLabel("Debounce (frames):")
        _lbl_db.setToolTip(_tip_db)
        db_row.addWidget(_lbl_db)
        self._debounce_sb = QSpinBox()
        self._debounce_sb.setRange(1, 20)
        self._debounce_sb.setValue(1)
        self._debounce_sb.setFixedWidth(55)
        self._debounce_sb.setToolTip(_tip_db)
        db_row.addWidget(self._debounce_sb)
        db_row.addStretch(1)
        lv.addLayout(db_row)

        # Scan buttons
        lv.addWidget(_hsep())
        self._btn_scan = QPushButton("Scan")
        self._btn_scan.setStyleSheet(_BTN_SUCCESS_STYLE)
        self._btn_scan.clicked.connect(self._start_scan)
        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setStyleSheet(_BTN_DANGER_STYLE)
        self._btn_stop.clicked.connect(self.cancel_scan)
        self._btn_stop.setEnabled(False)
        lv.addWidget(self._btn_scan)
        lv.addWidget(self._btn_stop)
        self._prog = QProgressBar()
        self._prog.setVisible(False)
        self._prog.setTextVisible(True)
        # Spell the percentage out: the bar spans a whole day of frames, so the fill
        # alone is far too coarse to read a number off.
        self._prog.setFormat("%p %")
        self._prog.setAlignment(Qt.AlignCenter)
        self._prog.setMinimumHeight(18)
        self._prog.setToolTip(
            "Frames measured in the camera currently being scanned.\n"
            "Updated 5× a second; the line below gives the exact frame counts.")
        lv.addWidget(self._prog)
        self._status_lbl = QLabel("Idle")
        self._status_lbl.setStyleSheet("font-size:10px;color:#555;")
        lv.addWidget(self._status_lbl)

        # Export
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Export"))
        self._btn_csv = QPushButton("Export full CSV")
        self._btn_csv.setStyleSheet(_BTN)
        self._btn_csv.clicked.connect(self._export_csv)
        self._btn_csv.setEnabled(False)
        lv.addWidget(self._btn_csv)
        self._btn_export_all = QPushButton("Export all (folder)")
        self._btn_export_all.setStyleSheet(_BTN)
        self._btn_export_all.clicked.connect(self._export_all)
        self._btn_export_all.setEnabled(False)
        lv.addWidget(self._btn_export_all)

        # Log
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Log"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(130)
        self._log.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        lv.addWidget(self._log)
        lv.addStretch(1)

        # ── RIGHT PANEL ───────────────────────────────────────────────────────
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 4, 0, 0)
        right_lay.setSpacing(2)

        cam_view_row = QHBoxLayout()
        cam_view_row.addWidget(QLabel("Viewing camera:"))
        self._result_cam_combo = _NoScrollComboBox()
        self._result_cam_combo.setEnabled(False)
        self._result_cam_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._result_cam_combo.currentTextChanged.connect(self._on_result_cam_changed)
        cam_view_row.addWidget(self._result_cam_combo, 1)
        right_lay.addLayout(cam_view_row)

        self._tabs = QTabWidget()
        self._tab_map = _MapTab()
        self._tab_stats = _StatsTab()
        self._tab_all = _AllDataTab()
        self._tab_run = _RunGraphTab()
        self._tabs.addTab(self._tab_map, "Pulser Map")
        self._tabs.addTab(self._tab_stats, "Statistics")
        self._tabs.addTab(self._tab_all, "All Data")
        self._tabs.addTab(self._tab_run, "Run Graph")
        right_lay.addWidget(self._tabs, 1)

        root_lay.addWidget(left_scroll)
        root_lay.addWidget(right_w, 1)

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _refresh_gate_label(self, *_a):
        if self._gate_hours_chk.isChecked():
            self._gate_hours_chk.setText(
                f"Only {self._gate_h0_sb.value():02d}:00–"
                f"{self._gate_h1_sb.value():02d}:00 (Prague)")
        else:
            self._gate_hours_chk.setText("Only these hours (Prague)")

    def _tw_text(self) -> str:
        return (f"{self._start_dt.strftime('%Y-%m-%d %Hh')} → "
                f"{self._end_dt.strftime('%Y-%m-%d %Hh')}")

    def _log_msg(self, msg: str):
        self._log.appendPlainText(msg)

    def _save_rois_config(self):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"version": CONFIG_VERSION, "cameras": {
                cam_key: [r.to_dict() for r in rois]
                for cam_key, rois in self._rois_by_camera.items()
            }}
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            self._log_msg(f"Could not save ROI config: {exc}")

    def _load_rois_config(self):
        try:
            if not CONFIG_PATH.exists():
                return
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            cams = data.get("cameras", {})
            loaded = 0
            for cam_key in CAMERAS:
                rois = cams.get(cam_key)
                if isinstance(rois, list):
                    self._rois_by_camera[cam_key] = [RoiDefinition.from_dict(r) for r in rois]
                    loaded += len(self._rois_by_camera[cam_key])
                self._update_roi_lbl(cam_key)
            if loaded:
                self._log_msg(f"Loaded saved ROI selection ({loaded} ROIs) from {CONFIG_PATH}")
            if int(data.get("version", 1)) < CONFIG_VERSION:
                self._migrate_reference_levels()
        except Exception as exc:
            self._log_msg(f"Could not load saved ROI config: {exc}")

    def _migrate_reference_levels(self):
        """Re-measure the stored reference levels after the full-scale fix (A1).

        v1 configs recorded ref_brightness/ref_contrast on the old, ~16x-too-large scale
        (see `_to_unit_scale`). The BOXES are hand-tuned and are never touched — only the
        two derived numbers are recomputed, by re-measuring each unchanged box against the
        camera's bundled all-alive reference image. The old file is kept as a backup."""
        try:
            backup = CONFIG_PATH.with_name("rois.backup-v1.json")
            if not backup.exists():
                backup.write_bytes(CONFIG_PATH.read_bytes())
                self._log_msg(f"Backed up the previous ROI config to {backup.name}")
        except Exception as exc:
            self._log_msg(f"Could not back up the ROI config ({exc}) — leaving it as is")
            return

        done, missing = 0, []
        for cam_key, rois in self._rois_by_camera.items():
            if not rois:
                continue
            ref_path = _bundled_ref(cam_key, "allgood")
            arr = _load_as_float32_gray(ref_path) if ref_path else None
            if arr is None:
                missing.append(cam_key)
                continue
            warm_arr = None
            warm_path = _bundled_ref(cam_key, "warmup")
            if warm_path:
                warm_arr = _load_as_float32_gray(warm_path)
            for roi in rois:
                tile, _r, contrast = _tile_floor_contrast(arr, roi.x, roi.y, roi.w, roi.h)
                roi.ref_brightness, roi.ref_contrast = tile, contrast
                if warm_arr is not None:
                    wt, _wr, wc = _tile_floor_contrast(warm_arr, roi.x, roi.y, roi.w, roi.h)
                    roi.warm_brightness, roi.warm_contrast = wt, wc
            done += len(rois)
        if missing:
            self._log_msg(
                f"No bundled all-alive reference for {', '.join(missing)} — their stored "
                f"levels are still on the old scale. Load the reference image in the ROI "
                f"editor and press 'Capture reference…'.")
        if done:
            self._log_msg(f"Re-measured reference levels for {done} ROIs on the corrected "
                          f"full-scale; ROI positions unchanged.")
            self._save_rois_config()

    def _update_roi_lbl(self, cam_key: str):
        n = len(self._rois_by_camera.get(cam_key, []))
        lbl = self._roi_count_lbls.get(cam_key)
        if lbl:
            lbl.setText(f"{n} ROI{'s' if n != 1 else ''}")

    def _set_roi_row_visible(self, cam_key: str, visible: bool):
        row_w = self._roi_rows.get(cam_key)
        if row_w:
            row_w.setVisible(visible)

    def _selected_cam_keys(self) -> list:
        return [k for k, chk in self._cam_checks.items() if chk.isChecked()]

    # ── SLOTS ─────────────────────────────────────────────────────────────────

    def _open_tw(self):
        dlg = _TimeWindowDialog(self._start_dt, self._end_dt, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._start_dt, self._end_dt = dlg.selected_range()
            self._tw_lbl.setText(self._tw_text())

    def _open_roi_editor(self, cam_key: str):
        current_rois = self._rois_by_camera.get(cam_key, [])
        dlg = ROIEditorDialog(
            current_rois, self,
            cam_key=cam_key,
            images_root=self._images_root,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._rois_by_camera[cam_key] = dlg.result_rois()
            self._update_roi_lbl(cam_key)
            self._save_rois_config()

    def _load_rois_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROIs", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # Two shapes exist in the wild: the multi-camera file this app auto-saves
            # ({"cameras": {cam: [...]}}) and the single-camera file the ROI editor's
            # "Save configuration…" writes ({"cam_key": …, "rois": [...]}). Accept both —
            # only the second used to be understood, so loading a backup of the app's own
            # config silently produced zero ROIs.
            loaded: dict = {}
            cams = data.get("cameras")
            if isinstance(cams, dict):
                for cam_key, rois in cams.items():
                    if cam_key in self._rois_by_camera and isinstance(rois, list):
                        loaded[cam_key] = [RoiDefinition.from_dict(r) for r in rois]
                unknown = [k for k in cams if k not in self._rois_by_camera]
            elif isinstance(data.get("rois"), list):
                cam_key = data.get("cam_key", "")
                unknown = [] if cam_key in self._rois_by_camera else [cam_key]
                if not unknown:
                    loaded[cam_key] = [RoiDefinition.from_dict(r)
                                       for r in data.get("rois", [])]
            else:
                QMessageBox.warning(
                    self, "Load ROIs",
                    "Unrecognised file: expected either a 'cameras' map or a 'rois' list.")
                return

            if not loaded:
                QMessageBox.warning(
                    self, "Load ROIs",
                    f"No known camera in this file (found: {', '.join(unknown) or 'none'}).\n"
                    f"Known cameras: {', '.join(CAMERAS)}")
                return
            for cam_key, rois in loaded.items():
                self._rois_by_camera[cam_key] = rois
                self._update_roi_lbl(cam_key)
            self._save_rois_config()
            summary = ", ".join(f"{k}: {len(v)}" for k, v in loaded.items())
            self._log_msg(f"Loaded ROIs from {Path(path).name} — {summary}")
            if unknown:
                self._log_msg(f"Ignored unknown camera(s) in that file: {', '.join(unknown)}")
        except Exception as exc:
            QMessageBox.warning(self, "Load ROIs", f"Failed:\n{exc}")

    # ── SCAN ──────────────────────────────────────────────────────────────────

    def _start_scan(self):
        cam_keys = self._selected_cam_keys()
        if not cam_keys:
            QMessageBox.warning(self, "Scan", "Check at least one camera checkbox.")
            return
        missing = [k for k in cam_keys if not self._rois_by_camera.get(k)]
        if missing:
            ans = QMessageBox.question(
                self, "Scan — missing ROIs",
                f"The following cameras have no ROIs defined:\n  {', '.join(missing)}\n\n"
                f"They will be skipped. Continue with the rest?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
            cam_keys = [k for k in cam_keys if self._rois_by_camera.get(k)]
        if not cam_keys:
            QMessageBox.warning(self, "Scan", "No cameras with ROIs to scan.")
            return

        self._results.clear()
        self._results_by_camera.clear()
        self._analysis_by_camera.clear()
        self._scan_queue = list(cam_keys)
        self._enum_queue = list(cam_keys)
        self._files_by_camera = {}
        self._total_frames = 0
        self._frames_done = 0
        self._cam_frames = 0
        self._gen_box[0] += 1
        self._btn_scan.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_csv.setEnabled(False)
        self._btn_export_all.setEnabled(False)
        self._prog.setValue(0)
        self._prog.setVisible(True)
        self._log_msg(
            f"Scan started: {len(cam_keys)} camera(s): {', '.join(cam_keys)}, "
            f"{self._start_dt.strftime('%Y-%m-%d %H:%M')} → "
            f"{self._end_dt.strftime('%Y-%m-%d %H:%M')}"
        )

        # Work the active-time gate out ONCE for the whole scan, before any camera is
        # enumerated: the hour rule is the same for all of them and the high-power rule
        # costs an archiver query, so doing it per camera would repeat it four times.
        start_ns = int(self._start_dt.timestamp() * 1_000_000_000)
        end_ns = int(self._end_dt.timestamp() * 1_000_000_000)
        # The settings are frozen for the whole scan: the reading plan is made from them
        # long before the analysis runs, and the two must agree.
        self._scan_params = self._analysis_params()
        self._power_info = None
        gate_hours = self._gate_hours_chk.isChecked()
        gate_hpe = self._gate_hpe_chk.isChecked()
        if not (gate_hours or gate_hpe):
            self._active_spans = None
            self._excluded_spans = None
            self._gate_notes = []
            self._gate_hpe_failed = False
            self._log_msg("Active-time gate off — the whole window will be judged")
            self._read_power_then_enumerate()
            return
        self._status_lbl.setText("Working out the analysed time…")
        my_gen = self._gen_box[0]
        self._gate_sig = _GateSignals(self)
        self._gate_sig.ready.connect(
            lambda a, e, n, f, _g=my_gen: self._on_gate_ready(a, e, n, f, _g))
        QThreadPool.globalInstance().start(_GateWorker(
            self._gate_sig, start_ns, end_ns, gate_hours, gate_hpe,
            self._gate_h0_sb.value(), self._gate_h1_sb.value(),
            self._gen_box, my_gen))

    def _on_gate_ready(self, active, excluded, notes: list, hpe_failed: bool,
                       my_gen: int):
        if self._gen_box[0] != my_gen:
            return
        self._active_spans = active
        self._excluded_spans = excluded
        self._gate_notes = list(notes)
        self._gate_hpe_failed = bool(hpe_failed)
        for note in notes:
            self._log_msg(f"Gate: {note}")
        self._log_msg(
            f"Gate: analysing {_fmt_dur(active.total_ns)} in {len(active)} period(s); "
            f"{_fmt_dur(excluded.total_ns)} left out")
        if not active:
            self._btn_scan.setEnabled(True)
            self._btn_stop.setEnabled(False)
            self._prog.setVisible(False)
            self._status_lbl.setText("Nothing to analyse — see log")
            self._scan_queue.clear()
            QMessageBox.information(
                self, "Nothing to analyse",
                "The active-time gate removed the entire time window, so there is "
                "nothing left to judge.\n\n" + "\n".join(f"• {n}" for n in notes) +
                "\n\nWiden the hours, pick a window with the high-power key on, or turn "
                "the gate off under 'Analysed time'.")
            return
        self._read_power_then_enumerate()

    def _read_power_then_enumerate(self):
        """Read the full-power gate if it is asked for, then list the frames."""
        params = self._scan_params or self._analysis_params()
        if not params.gate_full_power:
            self._enumerate_next_camera()
            return
        self._status_lbl.setText("Reading full power and energy from the archiver…")
        my_gen = self._gen_box[0]
        self._power_sig = _PowerSignals(self)
        self._power_sig.ready.connect(lambda info, _g=my_gen: self._on_power_ready(info, _g))
        QThreadPool.globalInstance().start(_PowerWorker(
            self._power_sig,
            int(self._start_dt.timestamp() * 1_000_000_000),
            int(self._end_dt.timestamp() * 1_000_000_000),
            params, getattr(self, "_active_spans", None), self._gen_box, my_gen))

    def _on_power_ready(self, info: "PowerInfo", my_gen: int):
        if self._gen_box[0] != my_gen:
            return
        self._power_info = info
        for note in info.notes:
            if note:
                self._log_msg(f"Full power: {note}")
        if info.failed:
            self._log_msg("Full power: the archiver could not say — every frame will be read "
                          "and full power judged from the images")
        elif info.low_all:
            self._log_msg(f"Full power: {len(info.low_all)} low-power stretch(es) across the "
                          f"arrays, "
                          f"{_fmt_dur(sum(b - a for a, b in info.low_all))} — not judged")
        self._enumerate_next_camera()

    def _power_for(self, cam_key: str) -> "CamPower | None":
        info = self._power_info
        if info is None or info.failed:
            return None
        cp = info.cams.get(cam_key)
        return None if cp is None or cp.failed else cp

    # ── PHASE 1: enumerate every camera, so the progress bar has a real denominator ──
    #
    # The bar used to be set to one camera's file count and then filled from zero for each
    # camera in turn, so finishing the first of four cameras read 100 % and the bar filled
    # four times over. Listing is the cheap half of a scan — twelve threads of almost pure
    # SMB latency — so doing all of it first costs little and buys an exact total.

    def _enumerate_next_camera(self):
        if not self._enum_queue:
            self._begin_measuring()
            return
        cam_key = self._enum_queue.pop(0)
        self._current_scan_cam_key = cam_key
        my_gen = self._gen_box[0]
        done = len(self._files_by_camera) + 1
        total = done + len(self._enum_queue)
        self._status_lbl.setText(f"Finding frames [{done}/{total}] {cam_key}…")
        start_ns = int(self._start_dt.timestamp() * 1_000_000_000)
        end_ns = int(self._end_dt.timestamp() * 1_000_000_000)
        min_size = MIN_IMAGE_BYTES
        folder_name = CAMERAS[cam_key]
        self._file_sig = _FileScanSignals(self)
        self._file_sig.files_ready.connect(
            lambda files, _g=my_gen, _c=cam_key: self._on_files_listed(files, _c, _g))
        self._file_sig.error.connect(self._on_scan_error)
        QThreadPool.globalInstance().start(_FileScanWorker(
            self._file_sig, self._images_root, folder_name,
            start_ns, end_ns, min_size, self._gen_box, my_gen,
            getattr(self, "_active_spans", None)))

    def _on_files_listed(self, files: list, cam_key: str, my_gen: int):
        if self._gen_box[0] != my_gen:
            return
        cp = self._power_for(cam_key)
        if cp is not None and files:
            files, note = plan_reading(files, cp, self._scan_params or self._analysis_params())
            self._log_msg(f"{cam_key}: {note}")
        self._files_by_camera[cam_key] = list(files)
        if files:
            self._log_msg(f"{cam_key}: {len(files)} files to scan")
        else:
            min_kb = MIN_IMAGE_BYTES // 1024
            self._log_msg(f"{cam_key}: no qualifying images (min {min_kb} kB) in the window")
        self._enumerate_next_camera()

    def _begin_measuring(self):
        """One range for the whole scan: the sum of every camera's frame count.

        A camera whose listing found nothing contributes 0, so it neither shifts the total
        nor leaves a dead stretch in the bar."""
        self._scan_queue = [k for k in self._scan_queue if self._files_by_camera.get(k)]
        self._total_frames = sum(len(v) for v in self._files_by_camera.values())
        self._frames_done = 0
        for cam_key, files in self._files_by_camera.items():
            if not files:
                self._results_by_camera[cam_key] = []
        if not self._scan_queue:
            self._finish_all_scans()
            return
        self._prog.setRange(0, max(1, self._total_frames))
        self._prog.setValue(0)
        self._log_msg(f"Measuring {self._total_frames} frame(s) across "
                      f"{len(self._scan_queue)} camera(s)")
        self._scan_next_camera()

    # ── PHASE 2: measure ────────────────────────────────────────────────────────
    def _scan_next_camera(self):
        cam_key = self._scan_queue.pop(0)
        self._current_scan_cam_key = cam_key
        self._on_files_ready(list(self._files_by_camera.get(cam_key, [])),
                             self._gen_box[0])

    def _on_files_ready(self, files: list, my_gen: int):
        if self._gen_box[0] != my_gen:
            return
        cam_key = self._current_scan_cam_key
        rois = list(self._rois_by_camera.get(cam_key, []))
        self._cam_frames = len(files)
        self._status_lbl.setText(f"{cam_key}: 0 / {len(files)}…")
        self._scan_sig = _ScanSignals(self)
        self._scan_sig.progress.connect(self._on_progress)
        self._scan_sig.samples.connect(self._on_samples)
        self._scan_sig.finished.connect(self._on_finished)
        self._scan_sig.error.connect(self._on_scan_error)
        self._scan_sig.log_msg.connect(self._log_msg)
        worker = _ScanWorker(self._scan_sig, files, rois, self._gen_box, my_gen)
        QThreadPool.globalInstance().start(worker)

    def _on_progress(self, done: int, total: int):
        """`done`/`total` are this camera's; the bar is the whole scan's.

        `_frames_done` counts the cameras already finished, so the bar advances once across
        every selected camera. One of four equal cameras finished reads 25 %."""
        overall = min(self._total_frames, self._frames_done + done)
        self._prog.setValue(overall)
        n_done = len(self._results_by_camera)
        n_total = n_done + 1 + len(self._scan_queue)
        pct = (100.0 * overall / self._total_frames) if self._total_frames else 0.0
        self._status_lbl.setText(
            f"[{n_done + 1}/{n_total}] {self._current_scan_cam_key}: "
            f"{done:,} / {total:,}  ·  overall {overall:,} / {self._total_frames:,} "
            f"({pct:.1f} %)…".replace(",", " "))

    def _on_samples(self, samples: list):
        self._results.extend(samples)

    def _on_finished(self):
        cam_key = self._current_scan_cam_key
        self._results_by_camera[cam_key] = list(self._results)
        self._log_msg(f"Camera {cam_key}: {len(self._results)} samples")
        self._results.clear()
        # Jump the counter to this camera's full share rather than to the number of samples
        # kept: frames rejected by the size gate never reach `_on_progress`, and without
        # this the bar would end a little short of full on every camera.
        self._frames_done = min(self._total_frames,
                                self._frames_done + getattr(self, "_cam_frames", 0))
        self._prog.setValue(self._frames_done)
        if self._scan_queue:
            self._scan_next_camera()
        else:
            self._finish_all_scans()

    def _finish_all_scans(self):
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._prog.setVisible(False)
        n_cameras = len(self._results_by_camera)
        total_samples = sum(len(v) for v in self._results_by_camera.values())
        self._status_lbl.setText(f"Done — {n_cameras} camera(s), {total_samples} samples")
        self._log_msg(f"All done: {n_cameras} cameras, {total_samples} total samples")
        self._result_cam_combo.blockSignals(True)
        self._result_cam_combo.clear()
        for cam_key in self._results_by_camera:
            self._result_cam_combo.addItem(cam_key)
        self._result_cam_combo.setEnabled(n_cameras > 1)
        self._result_cam_combo.blockSignals(False)
        # Analyse every scanned camera (states + per-pulser stats).
        self._analysis_by_camera = {}
        for cam_key, results in self._results_by_camera.items():
            rois = self._rois_by_camera.get(cam_key, [])
            if results and rois:
                params = self._scan_params or self._analysis_params()
                info = self._power_info
                analysis = analyze_camera(
                    rois, results, params,
                    excluded=getattr(self, "_excluded_spans", None),
                    gate_notes=getattr(self, "_gate_notes", None),
                    gate_hpe_failed=getattr(self, "_gate_hpe_failed", False),
                    power=self._power_for(cam_key),
                    low_power=info.low_all if info is not None else None,
                    gate_power_failed=bool(params.gate_full_power
                                           and self._power_for(cam_key) is None),
                    window_ns=(int(self._start_dt.timestamp() * 1_000_000_000),
                               int(self._end_dt.timestamp() * 1_000_000_000)))
                self._analysis_by_camera[cam_key] = analysis
                for warn in analysis.warnings:
                    self._log_msg(f"WARNING ({cam_key}): {warn}")
            else:
                self._analysis_by_camera[cam_key] = None
        self._tab_all.update_data(self._analysis_by_camera)
        if self._results_by_camera:
            self._btn_csv.setEnabled(True)
            self._btn_export_all.setEnabled(True)
            first_cam = next(iter(self._results_by_camera))
            self._result_cam_combo.setCurrentText(first_cam)
            self._results = list(self._results_by_camera[first_cam])
            self._rois = list(self._rois_by_camera.get(first_cam, []))
            self._render_all_tabs()

    def _analysis_params(self) -> "AnalysisParams":
        """Adaptive-classification tunables from the Detection panel."""
        return AnalysisParams(
            blink_below=self._thr_sb.value(),
            full_below=self._thr_low_sb.value(),
            gate_full_power=self._gate_fp_chk.isChecked(),
            energy_guided=self._energy_guided_chk.isChecked(),
            nodata_frac=self._nodata_sb.value(),
            gap_factor=self._gap_sb.value(),
            debounce=self._debounce_sb.value(),
            use_reference=self._use_ref_chk.isChecked(),
            dropout_frames=self._drop_sb.value(),
            culprit_fast_s=self._cul_fast_sb.value(),
            culprit_slow_s=self._cul_slow_sb.value(),
            replaced_min=self._repl_sb.value(),
            trip_min_s=self._outmin_sb.value(),
            manual_max_min=self._trig_sb.value(),
            outage_max_min=self._off_sb.value(),
            dead_restarts=self._deadr_sb.value(),
            dead_confirm_min=self._deadm_sb.value(),
            warm_level=self._warm_sb.value(),
            gate_hours=self._gate_hours_chk.isChecked(),
            gate_high_power=self._gate_hpe_chk.isChecked(),
            gate_hour_start=self._gate_h0_sb.value(),
            gate_hour_end=self._gate_h1_sb.value(),
        )

    def _on_scan_error(self, msg: str):
        cam_key = self._current_scan_cam_key
        self._scan_queue.clear()
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._prog.setVisible(False)
        self._status_lbl.setText("Error — see log")
        self._log_msg(f"ERROR ({cam_key}): {msg}")

    def cancel_scan(self):
        self._gen_box[0] += 1
        self._scan_queue.clear()
        self._results.clear()
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._prog.setVisible(False)
        self._status_lbl.setText("Cancelled")
        self._log_msg("Scan cancelled")

    def _on_result_cam_changed(self, cam_key: str):
        if cam_key and cam_key in self._results_by_camera:
            self._results = list(self._results_by_camera[cam_key])
            self._rois = list(self._rois_by_camera.get(cam_key, []))
            self._render_all_tabs()

    def _render_all_tabs(self):
        cam_key = self._result_cam_combo.currentText()
        analysis = self._analysis_by_camera.get(cam_key)
        start_ns = self._results[0].ts_ns if self._results else None
        end_ns = self._results[-1].ts_ns if self._results else None
        self._tab_map.update_data(cam_key, self._rois, analysis, start_ns, end_ns)
        self._tab_stats.update_data(cam_key, analysis)
        self._tab_all.set_current_camera(cam_key)
        self._tab_run.update_data(self._results, self._rois, analysis,
                                  self._thr_sb.value(), self._thr_low_sb.value())

    # ── CSV EXPORT ────────────────────────────────────────────────────────────

    @staticmethod
    def _write_full_csv(path: str, cam_key: str, rois: list, results: list,
                        analysis: "CameraAnalysis | None" = None):
        """Per-frame CSV: raw brightness, the score it was judged by, and the resulting
        state — all three straight out of the analysis, so the file and the Map tab can
        never disagree. (The old export wrote an `_alive` flag from a separate fixed
        threshold that the app had long stopped using for anything.)

        `analysis.times_ns` carries extra synthetic markers for gaps in the image stream;
        rows are matched back to real frames by timestamp so those markers are skipped."""
        idx_of = {}
        if analysis is not None and analysis.times_ns:
            idx_of = {t: i for i, t in enumerate(analysis.times_ns)}
        smat = analysis.score_matrix if analysis is not None else None
        stmat = analysis.state_matrix if analysis is not None else None

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            header = ["camera", "timestamp_ns", "datetime_prague", "frame_mean",
                      "array_level", "frame_state"]
            for roi in rois:
                header += [f"{roi.name}_brightness", f"{roi.name}_score", f"{roi.name}_state"]
            w.writerow(header)
            for sp in results:
                fi = idx_of.get(sp.ts_ns)
                if analysis is None:
                    frame_state, level = "", ""
                elif fi is None:
                    # Measured, then dropped from the series because the frame showed no
                    # array at all. Named rather than blanked, so the file accounts for
                    # every frame that was read.
                    frame_state, level = "no-array", ""
                elif (fi < len(analysis.frame_excluded) and analysis.frame_excluded[fi]):
                    # Read and measured, but outside the analysed time — so its per-pulser
                    # states below are "no-data" by construction, not an observation.
                    frame_state, level = "excluded", ""
                elif analysis.frame_nodata[fi]:
                    # Three different reasons for having no data, named apart: the array fell
                    # over, we switched it off, or the archive skipped a beat.
                    if fi < len(analysis.frame_trip) and analysis.frame_trip[fi]:
                        frame_state = "outage"
                    elif (fi < len(analysis.frame_diodes_off)
                          and analysis.frame_diodes_off[fi]):
                        frame_state = "switched-off"
                    else:
                        frame_state = "data-gap"
                    level = ""
                elif fi < len(analysis.frame_warmup) and analysis.frame_warmup[fi]:
                    frame_state = "warmup"
                    level = (f"{analysis.array_level[fi]:.4f}"
                             if fi < len(analysis.array_level) else "")
                else:
                    frame_state = ("below-full-power"
                                   if (fi < len(analysis.frame_below_full)
                                       and analysis.frame_below_full[fi]) else "normal")
                    level = (f"{analysis.array_level[fi]:.4f}"
                             if fi < len(analysis.array_level) else "")
                row = [cam_key, sp.ts_ns, _fmt_ms(sp.ts_ns),
                       f"{getattr(sp, 'frame_mean', 1.0):.6f}", level, frame_state]
                for i in range(len(rois)):
                    mean_v = sp.roi_means[i] if i < len(sp.roi_means) else 0.0
                    if (fi is not None and smat is not None and i < smat.shape[1]
                            and np.isfinite(smat[fi, i])):
                        score_s = f"{float(smat[fi, i]):.4f}"
                    else:
                        score_s = ""
                    if fi is not None and stmat is not None and i < stmat.shape[0]:
                        state_s = _state_str(int(stmat[i, fi]))
                    else:
                        state_s = ""
                    row += [f"{mean_v:.6f}", score_s, state_s]
                w.writerow(row)

    def _export_csv(self):
        if not self._results:
            return
        cam_key = self._result_cam_combo.currentText() or "pulser"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", f"pulser_{cam_key}.csv", "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            self._write_full_csv(path, cam_key, self._rois, self._results,
                                 self._analysis_by_camera.get(cam_key))
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")

    def _export_all(self):
        if not self._results_by_camera:
            QMessageBox.information(self, "Export all", "Nothing to export — run a scan first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Export all — choose folder")
        if not folder:
            return
        folder_p = Path(folder)
        written = []
        errors = []
        # 1) All-data summary (across every camera)
        try:
            p = folder_p / "pulser_all_data.csv"
            self._tab_all.export_to(str(p))
            written.append(p.name)
        except Exception as exc:
            errors.append(f"all_data: {exc}")
        # 1b) What time the whole bundle describes. The gate is per scan, not per camera,
        # so the first analysis that exists can speak for all of them.
        first_an = next((a for a in self._analysis_by_camera.values() if a is not None),
                        None)
        if first_an is not None:
            try:
                p = folder_p / "pulser_analysed_time.csv"
                _write_gate_csv(str(p),
                                int(self._start_dt.timestamp() * 1_000_000_000),
                                int(self._end_dt.timestamp() * 1_000_000_000), first_an)
                written.append(p.name)
            except Exception as exc:
                errors.append(f"analysed_time: {exc}")
        # 2) Per-camera: full CSV + map PNG + run-graph PNG
        keep_cam = self._result_cam_combo.currentText()
        for cam_key, results in self._results_by_camera.items():
            rois = self._rois_by_camera.get(cam_key, [])
            if not results or not rois:
                continue
            try:
                p = folder_p / f"pulser_{cam_key}.csv"
                self._write_full_csv(str(p), cam_key, rois, results,
                                     self._analysis_by_camera.get(cam_key))
                written.append(p.name)
            except Exception as exc:
                errors.append(f"{cam_key} csv: {exc}")
            analysis = self._analysis_by_camera.get(cam_key)
            if analysis is not None:
                try:
                    p = folder_p / f"pulser_events_{cam_key}.csv"
                    _write_events_csv(str(p), cam_key, analysis)
                    written.append(p.name)
                    p = folder_p / f"pulser_trips_{cam_key}.csv"
                    _write_trips_csv(str(p), cam_key, analysis)
                    written.append(p.name)
                    p = folder_p / f"pulser_stretches_{cam_key}.csv"
                    _write_stretches_csv(str(p), cam_key, analysis)
                    written.append(p.name)
                    if analysis.warmup_windows:
                        p = folder_p / f"pulser_warmup_{cam_key}.csv"
                        _write_warmup_csv(str(p), cam_key, analysis)
                        written.append(p.name)
                except Exception as exc:
                    errors.append(f"{cam_key} events/trips: {exc}")
            # Render this camera into the tabs, then save the figures.
            self._result_cam_combo.blockSignals(True)
            self._result_cam_combo.setCurrentText(cam_key)
            self._result_cam_combo.blockSignals(False)
            self._results = list(results)
            self._rois = list(rois)
            self._render_all_tabs()
            try:
                p = folder_p / f"pulser_map_{cam_key}.png"
                self._tab_map._map_fig.savefig(str(p), dpi=150, bbox_inches="tight")
                written.append(p.name)
            except Exception as exc:
                errors.append(f"{cam_key} map: {exc}")
            try:
                p = folder_p / f"pulser_run_{cam_key}.png"
                self._tab_run._fig.savefig(str(p), dpi=150, bbox_inches="tight")
                written.append(p.name)
            except Exception as exc:
                errors.append(f"{cam_key} run: {exc}")
            try:
                p = folder_p / f"pulser_stats_{cam_key}.png"
                self._tab_stats._fig.savefig(str(p), dpi=150, bbox_inches="tight")
                written.append(p.name)
            except Exception as exc:
                errors.append(f"{cam_key} stats: {exc}")
        # Restore the previously viewed camera.
        if keep_cam:
            self._result_cam_combo.setCurrentText(keep_cam)
        msg = f"Saved {len(written)} file(s) to:\n{folder}"
        if errors:
            msg += "\n\nProblems:\n" + "\n".join(errors)
        QMessageBox.information(self, "Export all", msg)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def _app_version():
    """Version of the running build, e.g. "v1.0.0", or "" when run from source.
    The builder names every exe "<Program> vX.Y.Z.exe", so the exe's own file
    name is the one place that always tells the truth."""
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", Path(sys.executable).stem)
    return m.group(1) if m else ""


def _icon_file():
    """The icon sits next to the exe. In a frozen build __file__ points into
    the bundle, not the exe folder, so searching only there silently finds
    nothing and the app ends up with the generic Windows icon."""
    search_dirs = []
    if getattr(sys, "frozen", False):
        search_dirs.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            search_dirs.append(Path(meipass))
    else:
        search_dirs.append(Path(__file__).resolve().parent)
    for here in search_dirs:
        for icon_name in ("icon.ico", "icon.png", "pulser_monitor.ico"):
            p = here / icon_name
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


def install_wheel_guard(app=None):
    """A value must never change just because the pointer crossed its control.

    Number boxes, drop-downs and sliders answer the wheel only after they were
    CLICKED (or reached with Tab), and only while the pointer is over them --
    Qt delivers a wheel notch to the control under the pointer, so a selected
    field elsewhere on the page is never touched. Moving the pointer away keeps
    the field selected (the keyboard still edits it); the wheel works on it
    again when the pointer comes back. Selecting another control ends it. An
    unselected field passes the notch to the panel behind it, so a settings
    panel still scrolls when the pointer happens to pass over a field. The
    focus a freshly opened window HANDS its first field does not count.

    The metric / camera drop-downs (_NoScrollComboBox) and the calendars
    (_NoScrollCalendar) keep refusing the wheel altogether on their own.
    """
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider,
                                   QAbstractSpinBox, QApplication, QComboBox,
                                   QScrollBar)

    app = app or QApplication.instance()
    if app is None or getattr(app, "_wheel_guard", None) is not None:
        return

    class _WheelGuard(QObject):
        _GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                   Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)
        # Focus that leaves only for a moment and comes back to the same field:
        # another window was activated, or the drop-down's own list opened.
        _KEEP = (Qt.FocusReason.ActiveWindowFocusReason,
                 Qt.FocusReason.PopupFocusReason)

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
            if t == QEvent.Type.Polish and isinstance(obj, self._GUARDED):
                # Spin boxes and drop-downs come with WheelFocus: Qt hands them
                # the focus, as a MOUSE focus, the moment a wheel notch lands on
                # them -- before the notch itself is delivered. That made every
                # untouched field "clicked" just in time to take the notch.
                # StrongFocus still takes a click or Tab, never the wheel.
                if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                    obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                return False
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                # A click is what selects the field -- also the field that
                # already HAD the focus (the first one of a window is handed
                # it), where no FocusIn follows the click. The press lands on
                # the spin box's own text line, so look one or two levels up.
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
    app._wheel_guard = guard


def main():
    # Before QApplication: Windows reads the identity when the first taskbar
    # button is created. See _icon_app_id() for the content tag.
    _aumid = _icon_app_id("ELIBeamlines.PulserMonitor", _icon_file())
    if _aumid:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    install_wheel_guard(app)
    apply_app_style(app)

    win = QMainWindow()
    ver = _app_version()
    win.setWindowTitle(f"Pulser Monitor {ver}" if ver else "Pulser Monitor (source)")
    win.setMinimumSize(1100, 700)

    widget = PulserMonitorWidget()
    win.setCentralWidget(widget)

    status_bar = QStatusBar()
    win.setStatusBar(status_bar)
    btn_stop = QPushButton("Stop Scan")
    btn_stop.setStyleSheet(
        f"QPushButton{{background:{DANGER};color:#fff;border:none;"
        f"border-radius:3px;padding:3px 8px;font-size:11px;}}"
    )
    btn_stop.clicked.connect(widget.cancel_scan)
    status_bar.addPermanentWidget(btn_stop)

    try:
        icon_path = _icon_file()
        if icon_path:
            app.setWindowIcon(QIcon(str(icon_path)))
            win.setWindowIcon(QIcon(str(icon_path)))
    except Exception:
        pass

    win.showMaximized()
    _nudge_taskbar_icon(int(win.winId()))
    sys.exit(app.exec())


def apply_app_style(app) -> None:
    """The application-wide look — also used by the render checks in testing/, so what
    they draw is what the program shows."""
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget     { background: #f3f3f3; color: #111; }
        QLabel      { background: transparent; }
        QPushButton { padding: 5px 8px; }
        QComboBox   { padding: 3px 6px; }
        QGroupBox   { font-weight: 600; border: 1px solid #ccc; border-radius: 4px;
                      margin-top: 6px; padding-top: 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; }
        QProgressBar { background: #e4e4e4; border: 1px solid #bbb; border-radius: 3px;
                       color: #111; font-size: 11px; font-weight: 600; text-align: center; }
        /* Light fill on purpose: Qt paints the percentage in one colour over both the
           groove and the fill, so a dark chunk would swallow the text it crosses. */
        QProgressBar::chunk { background: #90CAF9; border-radius: 2px; }
        QScrollBar:vertical { width: 8px; background: #f0f0f0; border: none; }
        QScrollBar::handle:vertical { background: #bbb; border-radius: 4px; min-height: 20px; }
        QTabWidget::pane { border: 1px solid #ccc; }
        QTabBar::tab { padding: 5px 14px; }
        QTabBar::tab:selected { background: #fff; border-bottom: 2px solid #1565C0;
                                font-weight: 600; }
    """)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # safe no-op unless frozen (PyInstaller etc.)
    main()
