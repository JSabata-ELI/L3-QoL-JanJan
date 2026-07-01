"""
pulser_monitor.py — Pulser Monitor

Scans camera images over a date range and detects "dead pulsers":
rectangular regions that are less bright than expected.

Run standalone: python pulser_monitor.py
"""

import csv
import json
import os
import re
import sys
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
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from PySide6.QtCore import (
    Qt, QDate, QObject, Signal, QEvent, QPoint, QRect,
    QRunnable, QThreadPool,
)
from PySide6.QtGui import (
    QColor, QTextCharFormat, QPixmap, QImage, QPainter, QPen, QBrush,
    QFont, QIcon, QCursor,
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

# Fixed cameras — display name → CPVA folder name
CAMERAS = {
    "PD1M1": "C03-015-PD1M1DF-_-IMG",
    "PD2M1": "C03-019-PD2M1DF-_-IMG",
    "PD3M1": "C03-023-PD3M1DF-_-IMG",
    "PD4M1": "C03-027-PD4M1DF-_-IMG",
}

# Minimum file size (bytes) for a valid image on each camera
CAM_MIN_BYTES = {
    "PD1M1": 350 * 1024,
    "PD2M1": 1900 * 1024,
    "PD3M1": 400 * 1024,
    "PD4M1": 400 * 1024,
}

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

_ROI_COLORS = [
    "#FF9800", "#2196F3", "#4CAF50", "#E91E63",
    "#9C27B0", "#00BCD4", "#FF5722", "#FFEB3B",
]

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
    ref_brightness: float = 0.0
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
            color=str(d.get("color", "#FF9800")),
        )


@dataclass
class SamplePoint:
    ts_ns: int
    path: Path
    roi_means: list
    roi_norms: list
    roi_alive: list
    frame_mean: float = 1.0   # mean brightness of the whole frame (for no-data detection)


# ── STATE MODEL & ANALYSIS ──────────────────────────────────────────────────────
# Per-pulser, per-frame state:
STATE_OFF    = 0    # array present but this pulser is dark  → counts toward dropouts
STATE_ON     = 1    # pulser lit
STATE_NODATA = -1   # whole frame is dark / no real array data → a gap, never a dropout


@dataclass
class DropoutEvent:
    start_ns: int                    # ts of the first OFF frame of this dropout
    recovery_ns: "int | None" = None  # ts of the first ON frame after it (None = still down)
    dark_frames: int = 0             # number of OFF frames in the dropout
    nodata_frames: int = 0           # number of no-data frames spanned by the dropout

    def duration_ns(self, end_fallback_ns: int) -> int:
        end = self.recovery_ns if self.recovery_ns is not None else end_fallback_ns
        return max(0, end - self.start_ns)


@dataclass
class PulserStats:
    name: str
    dropouts: int = 0
    longest_run_ns: int = 0          # longest run without a dropout (wall-clock, incl. no-data)
    longest_run_active_ns: int = 0   # same run but with no-data gaps subtracted
    uptime_pct: float = 0.0          # ON / (ON+OFF) frames
    total_down_ns: int = 0
    frames: int = 0
    on_frames: int = 0
    off_frames: int = 0
    nodata_frames: int = 0
    first_drop_ns: "int | None" = None
    last_drop_ns: "int | None" = None
    span_ns: int = 0                 # ts[last] - ts[first]
    current_state: int = STATE_NODATA
    events: list = field(default_factory=list)

    @property
    def mtbf_ns(self) -> "int | None":
        """Mean time between dropouts (observed span / number of dropouts)."""
        if self.dropouts <= 0:
            return None
        return int(self.span_ns / self.dropouts)


@dataclass
class CameraAnalysis:
    stats: list                       # PulserStats per ROI, aligned to the rois order
    state_matrix: "np.ndarray"        # shape (n_rois, n_frames): STATE_ON/OFF/NODATA
    frame_nodata: list                # bool per frame
    times_ns: list                    # ts_ns per frame


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


def _compute_pulser_stats(name: str, st: list, times: list) -> PulserStats:
    """Compute per-pulser statistics from a cleaned per-frame state sequence `st`."""
    n = len(st)
    ps = PulserStats(name=name)
    if n == 0:
        return ps
    ps.frames = n
    ps.on_frames = sum(1 for s in st if s == STATE_ON)
    ps.off_frames = sum(1 for s in st if s == STATE_OFF)
    ps.nodata_frames = sum(1 for s in st if s == STATE_NODATA)
    data_frames = ps.on_frames + ps.off_frames
    ps.uptime_pct = 100.0 * ps.on_frames / data_frames if data_frames else 0.0
    ps.span_ns = times[-1] - times[0]

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

    longest = longest_active = 0
    dropouts = 0
    events: list = []
    first_drop = last_drop = None
    in_dropout = False
    run_start = None
    last_data_state = STATE_NODATA

    for i, s in enumerate(st):
        if s == STATE_ON:
            last_data_state = STATE_ON
            if run_start is None:
                run_start = i
            if in_dropout:
                in_dropout = False
                events[-1].recovery_ns = times[i]
                run_start = i
        elif s == STATE_OFF:
            last_data_state = STATE_OFF
            if not in_dropout:
                if run_start is not None:
                    wall = times[i] - times[run_start]
                    active = wall - nd_between(run_start, i)
                    longest = max(longest, wall)
                    longest_active = max(longest_active, active)
                in_dropout = True
                dropouts += 1
                events.append(DropoutEvent(start_ns=times[i]))
                if first_drop is None:
                    first_drop = times[i]
                last_drop = times[i]
            events[-1].dark_frames += 1
        else:  # STATE_NODATA
            if in_dropout:
                events[-1].nodata_frames += 1

    if not in_dropout and run_start is not None:
        wall = times[-1] - times[run_start]
        active = wall - nd_between(run_start, n - 1)
        longest = max(longest, wall)
        longest_active = max(longest_active, active)

    total_down = sum(ev.duration_ns(times[-1]) for ev in events)

    ps.dropouts = dropouts
    ps.longest_run_ns = longest
    ps.longest_run_active_ns = longest_active
    ps.total_down_ns = total_down
    ps.events = events
    ps.first_drop_ns = first_drop
    ps.last_drop_ns = last_drop
    ps.current_state = last_data_state if st[-1] == STATE_NODATA else st[-1]
    return ps


def analyze_camera(rois: list, sample_points: list, thr_high: float,
                   thr_low: float, nodata_floor: float, debounce: int) -> CameraAnalysis:
    """Turn raw brightness samples into stabilised ON/OFF/NODATA states and per-pulser stats.

    - A frame whose whole-frame brightness < nodata_floor is NODATA for every pulser.
    - Otherwise a pulser is ON when norm >= thr_high, OFF when norm <= thr_low, and holds
      its previous state in between (hysteresis) — this kills flicker near the threshold.
    - OFF runs shorter than `debounce` consecutive data frames are treated as ON (noise).
    """
    n_rois = len(rois)
    n = len(sample_points)
    times = [sp.ts_ns for sp in sample_points]
    frame_nodata = [float(getattr(sp, "frame_mean", 1.0)) < nodata_floor for sp in sample_points]
    state_matrix = np.full((n_rois, n), STATE_NODATA, dtype=int)
    stats: list = []

    for r in range(n_rois):
        raw = [STATE_NODATA] * n
        prev = STATE_ON
        for i, sp in enumerate(sample_points):
            if frame_nodata[i]:
                raw[i] = STATE_NODATA
                continue
            norm = sp.roi_norms[r] if r < len(sp.roi_norms) else 0.0
            if norm >= thr_high:
                s = STATE_ON
            elif norm <= thr_low:
                s = STATE_OFF
            else:
                s = prev
            raw[i] = s
            prev = s

        # Debounce: flip too-short OFF runs (among data frames) back to ON.
        if debounce > 1:
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

        for i in range(n):
            state_matrix[r, i] = raw[i]
        stats.append(_compute_pulser_stats(rois[r].name, raw, times))

    return CameraAnalysis(stats=stats, state_matrix=state_matrix,
                          frame_nodata=frame_nodata, times_ns=times)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _parse_ts_from_path(p: Path) -> "int | None":
    for m in _NS_19_RE.finditer(p.stem):
        ts = int(m.group())
        if 946684800_000_000_000 <= ts <= 4102444800_000_000_000:
            return ts
    return None


def _read_image_max_sample(path: Path) -> "int | None":
    ext = path.suffix.lower()
    try:
        with PilImage.open(str(path)) as pil:
            if ext in (".tif", ".tiff"):
                tag_data = pil.tag_v2 if hasattr(pil, "tag_v2") else getattr(pil, "tag", {})
                val = tag_data.get(281)
                if val is not None:
                    if isinstance(val, (list, tuple)):
                        val = val[0]
                    return int(val)
            elif ext == ".png":
                info = pil.info
                for key in ("MaxValue", "max_value", "MaxSampleValue", "max_sample_value",
                            "BitDepthMax", "bit_depth_max"):
                    v = info.get(key)
                    if v is not None:
                        try:
                            return int(float(v))
                        except (ValueError, TypeError):
                            pass
                for v in info.values():
                    if isinstance(v, str) and v.strip().isdigit():
                        n = int(v.strip())
                        if 256 <= n <= 65535:
                            return n
    except Exception:
        pass
    return None


def _load_as_float32_gray(path: Path, fallback_bit_depth: int = 12) -> "np.ndarray | None":
    try:
        with PilImage.open(str(path)) as pil:
            arr = np.array(pil.convert("I"), dtype=np.float32)
        max_meta = _read_image_max_sample(path)
        if max_meta and max_meta > 0:
            max_val = float(max_meta)
        else:
            max_val = float((1 << fallback_bit_depth) - 1)
            arr_max = float(arr.max())
            if arr_max > max_val:
                max_val = arr_max or 1.0
        return arr / max_val
    except Exception:
        return None


def _normalize(roi_means: list, norm_mode: str, rois: list) -> list:
    if norm_mode == "median":
        med = float(np.median(roi_means)) if roi_means else 1.0
        if med <= 0:
            med = 1.0
        return [m / med for m in roi_means]
    elif norm_mode == "reference":
        return [m / (roi.ref_brightness or 1.0) for m, roi in zip(roi_means, rois)]
    else:
        return list(roi_means)


def _ns_to_dt(ts_ns: int) -> datetime:
    tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
    return datetime.fromtimestamp(ts_ns / 1e9, tz=tz)


def _find_latest_cam_folder(images_root: Path, camera: str) -> "Path | None":
    now = datetime.now(timezone.utc)
    for delta in range(0, 24 * 7):
        cur = now - timedelta(hours=delta)
        folder = (images_root / str(cur.year) / str(cur.month)
                  / str(cur.day) / str(cur.hour) / camera)
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
    min_bytes = CAM_MIN_BYTES.get(cam_key, 0)
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


class _ScanSignals(QObject):
    progress = Signal(int, int)   # done, total
    sample   = Signal(object)     # SamplePoint
    finished = Signal()
    error    = Signal(str)
    log_msg  = Signal(str)


class _FileScanWorker(QRunnable):
    def __init__(self, sig: _FileScanSignals, images_root: Path, camera: str,
                 start_ns: int, end_ns: int, min_size: int,
                 gen_box: list, my_gen: int):
        super().__init__()
        self._sig = sig
        self._root = images_root
        self._camera = camera
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._min_size = min_size
        self._gen_box = gen_box
        self._my_gen = my_gen
        self.setAutoDelete(True)

    def run(self):
        try:
            utc = timezone.utc
            start_utc = datetime.fromtimestamp(self._start_ns / 1e9, tz=utc)
            end_utc = datetime.fromtimestamp(self._end_ns / 1e9, tz=utc)
            cur = start_utc.replace(minute=0, second=0, microsecond=0)
            end_hour = end_utc.replace(minute=0, second=0, microsecond=0)
            files = []
            while cur <= end_hour:
                if self._gen_box[0] != self._my_gen:
                    return
                folder = (self._root / str(cur.year) / str(cur.month)
                          / str(cur.day) / str(cur.hour) / self._camera)
                try:
                    if folder.exists() and folder.is_dir():
                        with os.scandir(str(folder)) as it:
                            for entry in it:
                                if not entry.is_file():
                                    continue
                                p = Path(entry.path)
                                if p.suffix.lower() not in IMAGE_EXTS:
                                    continue
                                if self._min_size > 0:
                                    try:
                                        if p.stat().st_size < self._min_size:
                                            continue
                                    except Exception:
                                        continue
                                ts = _parse_ts_from_path(p)
                                if ts is not None and self._start_ns <= ts <= self._end_ns:
                                    files.append((ts, p))
                except Exception:
                    pass
                cur += timedelta(hours=1)
            files.sort(key=lambda x: x[0])
            if self._gen_box[0] == self._my_gen:
                self._sig.files_ready.emit(files)
        except Exception as exc:
            self._sig.error.emit(str(exc))


class _ScanWorker(QRunnable):
    def __init__(self, sig: _ScanSignals, files: list, rois: list,
                 norm_mode: str, threshold: float, fallback_bits: int,
                 gen_box: list, my_gen: int):
        super().__init__()
        self._sig = sig
        self._files = files
        self._rois = rois
        self._norm_mode = norm_mode
        self._threshold = threshold
        self._fallback_bits = fallback_bits
        self._gen_box = gen_box
        self._my_gen = my_gen
        self.setAutoDelete(True)

    def run(self):
        total = len(self._files)
        try:
            for idx, (ts_ns, path) in enumerate(self._files):
                if self._gen_box[0] != self._my_gen:
                    return
                arr = _load_as_float32_gray(path, self._fallback_bits)
                if arr is None:
                    self._sig.log_msg.emit(f"Skip {path.name}: load failed")
                    self._sig.progress.emit(idx + 1, total)
                    continue
                h, w = arr.shape
                roi_means = []
                for roi in self._rois:
                    x0 = max(0, roi.x); y0 = max(0, roi.y)
                    x1 = min(w, roi.x + roi.w); y1 = min(h, roi.y + roi.h)
                    if x1 <= x0 or y1 <= y0:
                        roi_means.append(0.0)
                    else:
                        roi_means.append(float(arr[y0:y1, x0:x1].mean()))
                roi_norms = _normalize(roi_means, self._norm_mode, self._rois)
                roi_alive = [n >= self._threshold for n in roi_norms]
                frame_mean = float(arr.mean())
                self._sig.sample.emit(
                    SamplePoint(ts_ns, path, roi_means, roi_norms, roi_alive, frame_mean))
                self._sig.progress.emit(idx + 1, total)
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
        self._selected_idx: "int | None" = None
        self._cam_key: str = cam_key
        self._images_root: "Path | None" = images_root
        self._gray_arr: "np.ndarray | None" = None  # last loaded image (float32 gray)
        self._ref_sig: "_RefImageSignals | None" = None
        self._ref_gen: int = 0
        self._ref_loading: bool = False
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        tb = QHBoxLayout()
        btn_img = QPushButton("Load image…")
        btn_img.setStyleSheet(_BTN_SM)
        btn_img.clicked.connect(self._load_image_manual)
        tb.addWidget(btn_img)
        if self._cam_key and self._images_root:
            btn_auto_img = QPushButton("Auto-load latest")
            btn_auto_img.setStyleSheet(_BTN_SM)
            btn_auto_img.clicked.connect(lambda: self._auto_load_ref(initial=False))
            tb.addWidget(btn_auto_img)
        tb.addSpacing(6)
        if self._cam_key:
            btn_grid = QPushButton("Auto-create grid")
            btn_grid.setStyleSheet(_BTN_SM)
            btn_grid.clicked.connect(self._auto_grid)
            tb.addWidget(btn_grid)
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
        btn_load_j = QPushButton("Load configuration…")
        btn_save_j = QPushButton("Save configuration…")
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
        if self._cam_key and self._images_root and self._canvas.img_w == 0:
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
            f"(min {CAM_MIN_BYTES.get(self._cam_key, 0) // 1024} kB). "
            f"Load manually.")

    def _load_image_manual(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open reference image", "",
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*)")
        if path:
            self._load_image_from_path(Path(path))

    def _load_image_from_path(self, path: Path):
        try:
            arr = _load_as_float32_gray(path)
            if arr is None:
                raise ValueError("Could not read image")
            self._display_image(path, arr)
        except Exception as exc:
            QMessageBox.warning(self, "Load image", f"Failed:\n{exc}")

    def _display_image(self, path: Path, arr: np.ndarray):
        # Bump generation so any still-running background auto-load is ignored
        self._ref_gen += 1
        self._ref_loading = False
        self._gray_arr = arr
        lo, hi = float(arr.min()), float(arr.max())
        if hi > lo:
            arr8 = np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
        else:
            arr8 = np.zeros(arr.shape, dtype=np.uint8)
        self._canvas.set_image(arr8)
        self._hint.setText(
            f"Loaded: {path.name}  ({arr.shape[1]}×{arr.shape[0]})  —  "
            f"{self._hint_text()}")

    def _auto_grid(self):
        w, h = self._canvas.img_w, self._canvas.img_h
        if w == 0 or h == 0:
            QMessageBox.information(self, "Auto-create grid",
                                    "Load a reference image first.")
            return
        if self._gray_arr is not None and self._gray_arr.shape[:2] == (h, w):
            new_rois = _detect_grid_rois(self._gray_arr, self._cam_key)
            mode = "detected from image"
        else:
            new_rois = _make_default_rois(self._cam_key, w, h)
            mode = "even grid"
        self._rois.clear()
        self._rois.extend(new_rois)
        self._selected_idx = None
        self._name_edit.clear()
        self._btn_del.setEnabled(False)
        self._canvas.select(None)
        self._canvas.refresh()
        self._hint.setText(
            f"Grid {mode} (cols: {' '.join(CAM_COLS.get(self._cam_key, []))}, "
            f"rows: 1–{len(_ROW_LABELS)})  —  {self._hint_text()}")

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
        return list(self._rois)


# ── RESULT TABS ───────────────────────────────────────────────────────────────

def _state_str(s: int) -> str:
    return {STATE_ON: "ON", STATE_OFF: "OFF", STATE_NODATA: "no-data"}.get(s, "—")


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
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # LEFT — map + colorbar
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        self._map_fig = Figure(figsize=(5, 4))
        self._map_canvas = FigureCanvasQTAgg(self._map_fig)
        self._map_ax = self._map_fig.add_subplot(111)
        self._map_canvas.mpl_connect("motion_notify_event", self._on_hover)
        self._map_canvas.mpl_connect("button_press_event", self._on_click)
        left.addWidget(self._map_canvas, 1)
        map_btns = QHBoxLayout()
        btn_map = QPushButton("Export map (PNG)…")
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
        self._tbl = QTableWidget(0, 4)
        self._tbl.setHorizontalHeaderLabels(["#", "Dropout start", "Recovery", "Duration"])
        self._tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setStyleSheet(
            "QTableWidget{background:#fff;gridline-color:#e0e0e0;}"
            "QTableWidget::item{background:#fff;color:#111;padding:2px 4px;}"
            "QTableWidget::item:alternate{background:#e8f0fe;color:#111;}"
            "QTableWidget::item:selected{background:#1565C0;color:#fff;}"
        )
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

        btn_csv = QPushButton("Export dropouts (CSV)…")
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
        self._stats = analysis.stats if analysis else []
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._sel_roi = None
        self._events = []
        self._sel_event = 0
        self._draw_map()
        self._clear_selection()

    def _draw_map(self):
        self._map_ax.clear()
        self._map_fig.clf()
        self._map_ax = self._map_fig.add_subplot(111)
        if not self._rois or not self._stats:
            self._map_ax.text(0.5, 0.5, "No scan data yet", transform=self._map_ax.transAxes,
                              ha="center", va="center", color="#888", fontsize=12)
            self._map_canvas.draw()
            return
        rows, cols, cell_index = _grid_layout_for(self._cam_key, self._rois)
        self._rows_lbl, self._cols_lbl, self._cell_index = rows, cols, cell_index
        n_rows, n_cols = len(rows), len(cols)
        mat = np.full((n_rows, n_cols), np.nan)
        for (ri, ci), idx in cell_index.items():
            if idx < len(self._stats):
                mat[ri, ci] = self._stats[idx].dropouts
        cmap = matplotlib.colormaps["YlOrRd"].copy()
        cmap.set_bad("#dddddd")
        vmax = np.nanmax(mat) if np.isfinite(np.nanmax(mat)) else 1
        im = self._map_ax.imshow(np.ma.masked_invalid(mat), cmap=cmap, aspect="auto",
                                 vmin=0, vmax=max(1, vmax), interpolation="nearest",
                                 extent=[-0.5, n_cols - 0.5, n_rows - 0.5, -0.5])
        self._map_ax.set_xticks(range(n_cols))
        self._map_ax.set_xticklabels(cols)
        self._map_ax.set_yticks(range(n_rows))
        self._map_ax.set_yticklabels(rows)
        self._map_ax.set_title(f"{self._cam_key} — dropouts per pulser  (click a cell)")
        for (ri, ci), idx in cell_index.items():
            self._map_ax.text(ci, ri, self._rois[idx].name, ha="center", va="center",
                              fontsize=7, color="#222")
        cbar = self._map_fig.colorbar(im, ax=self._map_ax, fraction=0.046, pad=0.04)
        cbar.set_label("Dropouts")
        self._annot = self._map_ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="#ffffe0", ec="#888", alpha=0.95),
            fontsize=8, zorder=20)
        self._annot.set_visible(False)
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
        return "\n".join([
            f"{st.name}",
            f"Dropouts: {st.dropouts}",
            f"Longest run: {_fmt_dur(st.longest_run_ns)}",
            f"Longest run (no gaps): {_fmt_dur(st.longest_run_active_ns)}",
            f"Uptime: {st.uptime_pct:.1f}%",
            f"State: {_state_str(st.current_state)}",
            f"Last dropout: {last_drop}",
            f"Total downtime: {_fmt_dur(st.total_down_ns)}",
            f"MTBF: {_fmt_dur(st.mtbf_ns)}",
            f"Frames: {st.frames}",
        ])

    def _on_hover(self, event):
        if self._annot is None:
            return
        idx = self._cell_at(event)
        if idx is None:
            if self._annot.get_visible():
                self._annot.set_visible(False)
                self._map_canvas.draw_idle()
            return
        self._annot.xy = (event.xdata, event.ydata)
        self._annot.set_text(self._tooltip_text(idx))
        self._annot.set_visible(True)
        self._map_canvas.draw_idle()

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
        self._sel_lbl.setText(
            f"{st.name} — {st.dropouts} dropout(s), uptime {st.uptime_pct:.1f}%")
        # fill table
        self._tbl.blockSignals(True)
        self._tbl.setRowCount(0)
        for i, ev in enumerate(self._events):
            r = self._tbl.rowCount()
            self._tbl.insertRow(r)
            start_s = _ns_to_dt(ev.start_ns).strftime("%Y-%m-%d %H:%M:%S")
            rec_s = (_ns_to_dt(ev.recovery_ns).strftime("%Y-%m-%d %H:%M:%S")
                     if ev.recovery_ns else "— (still down)")
            dur_s = _fmt_dur(ev.duration_ns(self._end_ns or ev.start_ns))
            for c, txt in enumerate([str(i + 1), start_s, rec_s, dur_s]):
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
        self._tl_ax.clear()
        if not self._events or self._start_ns is None or self._end_ns is None:
            self._tl_ax.set_yticks([])
            self._tl_ax.text(0.5, 0.5, "No dropouts", transform=self._tl_ax.transAxes,
                             ha="center", va="center", color="#aaa", fontsize=9)
            self._tl_fig.tight_layout()
            self._tl_canvas.draw()
            return
        t0 = mdates.date2num(_ns_to_dt(self._start_ns))
        t1 = mdates.date2num(_ns_to_dt(self._end_ns))
        self._tl_ax.set_xlim(t0, t1)
        self._tl_ax.set_ylim(0, 1)
        self._tl_ax.set_yticks([])
        for i, ev in enumerate(self._events):
            x = mdates.date2num(_ns_to_dt(ev.start_ns))
            is_sel = (i == self._sel_event)
            self._tl_ax.axvline(x, color=PRIMARY if is_sel else DANGER,
                                linewidth=2.5 if is_sel else 1.2,
                                alpha=1.0 if is_sel else 0.7)
        # adaptive ticks
        span_days = max(1e-6, t1 - t0)
        loc = mdates.AutoDateLocator()
        self._tl_ax.xaxis.set_major_locator(loc)
        if span_days <= 1.5:
            fmt = "%H:%M"
        elif span_days <= 8:
            fmt = "%m-%d %Hh"
        else:
            fmt = "%m-%d"
        self._tl_ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
        self._tl_ax.tick_params(labelsize=8)
        for lbl in self._tl_ax.get_xticklabels():
            lbl.set_rotation(20)
        self._tl_fig.tight_layout()
        self._tl_canvas.draw()

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
            self, "Export dropouts", f"dropouts_{self._cam_key}_{st.name}.csv",
            "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["#", "dropout_start", "recovery", "duration",
                            "dark_frames", "nodata_frames"])
                for i, ev in enumerate(st.events):
                    rec = (_ns_to_dt(ev.recovery_ns).strftime("%Y-%m-%d %H:%M:%S")
                           if ev.recovery_ns else "")
                    w.writerow([
                        i + 1,
                        _ns_to_dt(ev.start_ns).strftime("%Y-%m-%d %H:%M:%S"),
                        rec, _fmt_dur(ev.duration_ns(self._end_ns or ev.start_ns)),
                        ev.dark_frames, ev.nodata_frames])
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")


class _RunGraphTab(QWidget):
    """Per-pulser run over time: ON/OFF/NODATA state bands, or brightness lines."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sample_points: list = []
        self._rois: list = []
        self._analysis: "CameraAnalysis | None" = None
        self._threshold = 0.5
        self._fig = Figure(figsize=(10, 4))
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self._ax = self._fig.add_subplot(111)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self._chk_bright = QCheckBox("Show brightness lines")
        self._chk_bright.setStyleSheet(_CHECKBOX_STYLE)
        self._chk_bright.stateChanged.connect(lambda _s: self._redraw())
        top.addWidget(self._chk_bright)
        top.addStretch(1)
        btn_png = QPushButton("Export graph (PNG)…")
        btn_png.setStyleSheet(_BTN)
        btn_png.clicked.connect(self._export_png)
        top.addWidget(btn_png)
        lay.addLayout(top)
        lay.addWidget(self._toolbar)
        lay.addWidget(self._canvas, 1)

    def update_data(self, sample_points: list, rois: list,
                    analysis: "CameraAnalysis | None", threshold: float):
        self._sample_points = sample_points
        self._rois = rois
        self._analysis = analysis
        self._threshold = threshold
        self._redraw()

    def _redraw(self):
        self._ax.clear()
        if not self._sample_points or not self._rois:
            self._ax.text(0.5, 0.5, "No scan data yet", transform=self._ax.transAxes,
                          ha="center", va="center", color="#888", fontsize=12)
            self._canvas.draw()
            return
        if self._chk_bright.isChecked():
            self._draw_brightness()
        else:
            self._draw_states()
        self._fig.tight_layout()
        self._canvas.draw()

    def _draw_brightness(self):
        times = [mdates.date2num(_ns_to_dt(sp.ts_ns)) for sp in self._sample_points]
        for i, roi in enumerate(self._rois):
            norms = [sp.roi_norms[i] if i < len(sp.roi_norms) else 0.0
                     for sp in self._sample_points]
            self._ax.plot(times, norms, color=_ROI_COLORS[i % len(_ROI_COLORS)],
                          label=roi.name, linewidth=1, alpha=0.85)
        self._ax.axhline(self._threshold, color="orange", linestyle="--",
                         linewidth=1.5, label=f"Threshold ({self._threshold:.2f})")
        self._ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        self._ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        self._fig.autofmt_xdate(rotation=30)
        self._ax.set_ylabel("Normalized brightness")
        self._ax.set_title("Brightness per pulser over time")
        ncol = max(1, min(len(self._rois), 6))
        self._ax.legend(fontsize=8, ncol=ncol, loc="upper right")

    def _draw_states(self):
        if self._analysis is None:
            self._ax.text(0.5, 0.5, "No analysis", transform=self._ax.transAxes,
                          ha="center", va="center", color="#888", fontsize=12)
            return
        sm = self._analysis.state_matrix
        n_rois, n_times = sm.shape
        # OFF=0→0, ON=1→1, NODATA=-1→2
        disp = np.where(sm == STATE_NODATA, 2, sm)
        times_num = [mdates.date2num(_ns_to_dt(t)) for t in self._analysis.times_ns]
        if len(times_num) > 1:
            dt_half = (times_num[1] - times_num[0]) * 0.5
            t0, t1 = times_num[0] - dt_half, times_num[-1] + dt_half
        else:
            t0, t1 = times_num[0] - 0.001, times_num[0] + 0.001
        cmap = ListedColormap([DANGER, SUCCESS, NODATA_CLR])
        self._ax.imshow(disp, aspect="auto", cmap=cmap, vmin=0, vmax=2,
                        interpolation="nearest", extent=[t0, t1, n_rois - 0.5, -0.5])
        self._ax.set_yticks(range(n_rois))
        self._ax.set_yticklabels([r.name for r in self._rois], fontsize=8)
        self._ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        self._ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        self._fig.autofmt_xdate(rotation=30)
        self._ax.set_title("Pulser state over time")
        self._ax.set_xlabel("Prague time")
        self._ax.legend(handles=[
            Patch(color=SUCCESS, label="ON"),
            Patch(color=DANGER, label="OFF"),
            Patch(color=NODATA_CLR, label="no-data"),
        ], fontsize=8, ncol=3, loc="upper right")

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


class _AllDataTab(QWidget):
    """One exportable table with per-pulser stats across all scanned arrays."""

    COLS = ["Array", "Pulser", "Dropouts", "Longest run", "Longest run (no gaps)",
            "Uptime %", "First dropout", "Last dropout", "State"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list = []          # list of dicts, one per pulser
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self._table = QTableWidget(0, len(self.COLS))
        self._table.setHorizontalHeaderLabels(self.COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setStyleSheet(
            "QTableWidget{background:#fff;gridline-color:#e0e0e0;}"
            "QTableWidget::item{background:#fff;color:#111;padding:2px 4px;}"
            "QTableWidget::item:alternate{background:#e8f0fe;color:#111;}"
            "QTableWidget::item:selected{background:#1565C0;color:#fff;}"
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        btn_exp = QPushButton("Export all data CSV…")
        btn_exp.setStyleSheet(_BTN)
        btn_exp.clicked.connect(self._export)
        lay.addWidget(self._table, 1)
        lay.addWidget(btn_exp)

    def update_data(self, analysis_by_camera: dict):
        self._data = []
        for cam_key, analysis in analysis_by_camera.items():
            if not analysis:
                continue
            for st in analysis.stats:
                self._data.append({
                    "array": cam_key, "name": st.name, "dropouts": st.dropouts,
                    "longest": st.longest_run_ns, "longest_active": st.longest_run_active_ns,
                    "uptime": st.uptime_pct,
                    "first": st.first_drop_ns, "last": st.last_drop_ns,
                    "state": st.current_state,
                })
        self._rebuild()

    def _rebuild(self):
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for d in self._data:
            r = self._table.rowCount()
            self._table.insertRow(r)
            first_s = (_ns_to_dt(d["first"]).strftime("%Y-%m-%d %H:%M") if d["first"] else "—")
            last_s = (_ns_to_dt(d["last"]).strftime("%Y-%m-%d %H:%M") if d["last"] else "—")
            cells = [
                (d["array"], None), (d["name"], None),
                (str(d["dropouts"]), d["dropouts"]),
                (_fmt_dur(d["longest"]), d["longest"]),
                (_fmt_dur(d["longest_active"]), d["longest_active"]),
                (f"{d['uptime']:.1f}%", d["uptime"]),
                (first_s, d["first"] or 0), (last_s, d["last"] or 0),
                (_state_str(d["state"]), None),
            ]
            for c, (txt, sortval) in enumerate(cells):
                it = QTableWidgetItem()
                it.setText(txt)
                if sortval is not None:
                    it.setData(Qt.ItemDataRole.EditRole, sortval)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(r, c, it)
        self._table.setSortingEnabled(True)

    def _export(self):
        if not self._data:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export all data", "pulser_all_data.csv", "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            self.export_to(path)
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")

    def export_to(self, path: str):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Array", "Pulser", "Dropouts", "Longest run", "Longest run",
                        "Longest run (no gaps)", "Longest run (no gaps)",
                        "Uptime %", "First dropout", "Last dropout", "State"])
            w.writerow(["", "", "", "human", "seconds", "human", "seconds",
                        "", "", "", ""])
            for d in self._data:
                first_s = (_ns_to_dt(d["first"]).strftime("%Y-%m-%d %H:%M") if d["first"] else "")
                last_s = (_ns_to_dt(d["last"]).strftime("%Y-%m-%d %H:%M") if d["last"] else "")
                w.writerow([
                    d["array"], d["name"], d["dropouts"],
                    _fmt_dur(d["longest"]), int(d["longest"] // 1_000_000_000),
                    _fmt_dur(d["longest_active"]), int(d["longest_active"] // 1_000_000_000),
                    f"{d['uptime']:.1f}", first_s, last_s, _state_str(d["state"])])


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
        self._scan_queue: list = []          # list of cam_key strings
        self._current_scan_cam_key: str = ""

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
        btn_tw = QPushButton("Set time window…")
        btn_tw.setStyleSheet(_BTN)
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
            btn = QPushButton(f"Edit {cam_key}…")
            btn.setStyleSheet(_BTN_SM)
            btn.setFixedWidth(110)
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
        btn_load_all = QPushButton("Load configuration…")
        btn_load_all.setFixedWidth(130)
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
        thr_row = QHBoxLayout()
        thr_row.addWidget(QLabel("Threshold:"))
        self._thr_sb = QDoubleSpinBox()
        self._thr_sb.setRange(0.01, 5.0)
        self._thr_sb.setSingleStep(0.05)
        self._thr_sb.setDecimals(2)
        self._thr_sb.setValue(0.5)
        self._thr_sb.setFixedWidth(75)
        thr_row.addWidget(self._thr_sb)
        thr_row.addStretch(1)
        lv.addLayout(thr_row)
        # Hysteresis lower threshold: stays alive until it drops below this.
        thr_lo_row = QHBoxLayout()
        thr_lo_row.addWidget(QLabel("Off threshold:"))
        self._thr_low_sb = QDoubleSpinBox()
        self._thr_low_sb.setRange(0.0, 5.0)
        self._thr_low_sb.setSingleStep(0.05)
        self._thr_low_sb.setDecimals(2)
        self._thr_low_sb.setValue(0.30)
        self._thr_low_sb.setFixedWidth(75)
        thr_lo_row.addWidget(self._thr_low_sb)
        thr_lo_row.addStretch(1)
        lv.addLayout(thr_lo_row)
        lv.addWidget(QLabel("Normalization:"))
        self._norm_group = QButtonGroup(self)
        for label, val in [("Median of ROIs  (recommended)", "median"),
                            ("Reference image", "reference"),
                            ("Absolute", "absolute")]:
            rb = QRadioButton(label)
            rb.setProperty("norm_val", val)
            self._norm_group.addButton(rb)
            lv.addWidget(rb)
        self._norm_group.buttons()[0].setChecked(True)
        bd_row = QHBoxLayout()
        bd_row.addWidget(QLabel("Bit depth fallback:"))
        self._bd_sb = QSpinBox()
        self._bd_sb.setRange(8, 16)
        self._bd_sb.setValue(12)
        self._bd_sb.setFixedWidth(55)
        bd_row.addWidget(self._bd_sb)
        bd_row.addWidget(QLabel("bit"))
        bd_row.addStretch(1)
        lv.addLayout(bd_row)
        # No-data floor: whole-frame brightness below this = frame has no array data.
        nd_row = QHBoxLayout()
        nd_row.addWidget(QLabel("No-data floor:"))
        self._nodata_sb = QDoubleSpinBox()
        self._nodata_sb.setRange(0.0, 1.0)
        self._nodata_sb.setSingleStep(0.01)
        self._nodata_sb.setDecimals(3)
        self._nodata_sb.setValue(0.020)
        self._nodata_sb.setFixedWidth(75)
        nd_row.addWidget(self._nodata_sb)
        nd_row.addStretch(1)
        lv.addLayout(nd_row)
        # Debounce: OFF must persist this many data frames to count as a dropout.
        db_row = QHBoxLayout()
        db_row.addWidget(QLabel("Debounce (frames):"))
        self._debounce_sb = QSpinBox()
        self._debounce_sb.setRange(1, 20)
        self._debounce_sb.setValue(2)
        self._debounce_sb.setFixedWidth(55)
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
        lv.addWidget(self._prog)
        self._status_lbl = QLabel("Idle")
        self._status_lbl.setStyleSheet("font-size:10px;color:#555;")
        lv.addWidget(self._status_lbl)

        # Export
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Export"))
        self._btn_csv = QPushButton("Export full CSV…")
        self._btn_csv.setStyleSheet(_BTN)
        self._btn_csv.clicked.connect(self._export_csv)
        self._btn_csv.setEnabled(False)
        lv.addWidget(self._btn_csv)
        self._btn_export_all = QPushButton("Export all (folder)…")
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
        self._tab_all = _AllDataTab()
        self._tab_run = _RunGraphTab()
        self._tabs.addTab(self._tab_map, "Pulser Map")
        self._tabs.addTab(self._tab_all, "All Data")
        self._tabs.addTab(self._tab_run, "Run Graph")
        right_lay.addWidget(self._tabs, 1)

        root_lay.addWidget(left_scroll)
        root_lay.addWidget(right_w, 1)

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _tw_text(self) -> str:
        return (f"{self._start_dt.strftime('%Y-%m-%d %Hh')} → "
                f"{self._end_dt.strftime('%Y-%m-%d %Hh')}")

    def _log_msg(self, msg: str):
        self._log.appendPlainText(msg)

    def _save_rois_config(self):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "cameras": {
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
        except Exception as exc:
            self._log_msg(f"Could not load saved ROI config: {exc}")

    def _update_roi_lbl(self, cam_key: str):
        n = len(self._rois_by_camera.get(cam_key, []))
        lbl = self._roi_count_lbls.get(cam_key)
        if lbl:
            lbl.setText(f"{n} ROI{'s' if n != 1 else ''}")

    def _set_roi_row_visible(self, cam_key: str, visible: bool):
        row_w = self._roi_rows.get(cam_key)
        if row_w:
            row_w.setVisible(visible)

    def _norm_mode(self) -> str:
        for btn in self._norm_group.buttons():
            if btn.isChecked():
                return btn.property("norm_val")
        return "median"

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
            new_rois = [RoiDefinition.from_dict(r) for r in data.get("rois", [])]
            cam_key = data.get("cam_key", "")
            if cam_key in self._rois_by_camera:
                self._rois_by_camera[cam_key] = new_rois
                self._update_roi_lbl(cam_key)
                self._save_rois_config()
                self._log_msg(
                    f"Loaded {len(new_rois)} ROIs for {cam_key} from {Path(path).name}")
            else:
                QMessageBox.warning(
                    self, "Load ROIs",
                    f"JSON has cam_key='{cam_key}' which is not one of the known cameras.\n"
                    f"Known cameras: {', '.join(CAMERAS)}")
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
        self._scan_next_camera()

    def _scan_next_camera(self):
        cam_key = self._scan_queue.pop(0)
        self._current_scan_cam_key = cam_key
        my_gen = self._gen_box[0]
        n_done = len(self._results_by_camera)
        n_total = n_done + 1 + len(self._scan_queue)
        self._status_lbl.setText(f"[{n_done + 1}/{n_total}] {cam_key}: enumerating…")
        start_ns = int(self._start_dt.timestamp() * 1_000_000_000)
        end_ns = int(self._end_dt.timestamp() * 1_000_000_000)
        min_size = CAM_MIN_BYTES.get(cam_key, 0)
        folder_name = CAMERAS[cam_key]
        self._file_sig = _FileScanSignals(self)
        self._file_sig.files_ready.connect(
            lambda files, _g=my_gen: self._on_files_ready(files, _g))
        self._file_sig.error.connect(self._on_scan_error)
        worker = _FileScanWorker(
            self._file_sig, self._images_root, folder_name,
            start_ns, end_ns, min_size, self._gen_box, my_gen)
        QThreadPool.globalInstance().start(worker)

    def _on_files_ready(self, files: list, my_gen: int):
        if self._gen_box[0] != my_gen:
            return
        cam_key = self._current_scan_cam_key
        n_done = len(self._results_by_camera)
        n_total = n_done + 1 + len(self._scan_queue)
        if not files:
            min_kb = CAM_MIN_BYTES.get(cam_key, 0) // 1024
            self._log_msg(
                f"[{n_done + 1}/{n_total}] {cam_key}: no qualifying images "
                f"(min {min_kb} kB) found in time window")
            self._results_by_camera[cam_key] = []
            if self._scan_queue:
                self._scan_next_camera()
            else:
                self._finish_all_scans()
            return
        self._prog.setRange(0, len(files))
        self._status_lbl.setText(
            f"[{n_done + 1}/{n_total}] {cam_key}: scanning 0/{len(files)}…")
        self._log_msg(f"[{n_done + 1}/{n_total}] {cam_key}: {len(files)} files to scan")
        rois = list(self._rois_by_camera.get(cam_key, []))
        self._scan_sig = _ScanSignals(self)
        self._scan_sig.progress.connect(self._on_progress)
        self._scan_sig.sample.connect(self._on_sample)
        self._scan_sig.finished.connect(self._on_finished)
        self._scan_sig.error.connect(self._on_scan_error)
        self._scan_sig.log_msg.connect(self._log_msg)
        worker = _ScanWorker(
            self._scan_sig, files, rois,
            self._norm_mode(), self._thr_sb.value(),
            self._bd_sb.value(), self._gen_box, my_gen,
        )
        QThreadPool.globalInstance().start(worker)

    def _on_progress(self, done: int, total: int):
        self._prog.setValue(done)
        n_done = len(self._results_by_camera)
        n_total = n_done + 1 + len(self._scan_queue)
        self._status_lbl.setText(
            f"[{n_done + 1}/{n_total}] {self._current_scan_cam_key}: {done}/{total}…")

    def _on_sample(self, sp: SamplePoint):
        self._results.append(sp)

    def _on_finished(self):
        cam_key = self._current_scan_cam_key
        self._results_by_camera[cam_key] = list(self._results)
        self._log_msg(f"Camera {cam_key}: {len(self._results)} samples")
        self._results.clear()
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
                self._analysis_by_camera[cam_key] = analyze_camera(
                    rois, results, *self._analysis_params())
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

    def _analysis_params(self) -> tuple:
        """(thr_high, thr_low, nodata_floor, debounce) from the Detection panel."""
        return (self._thr_sb.value(), self._thr_low_sb.value(),
                self._nodata_sb.value(), self._debounce_sb.value())

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
        t = self._thr_sb.value()
        cam_key = self._result_cam_combo.currentText()
        analysis = self._analysis_by_camera.get(cam_key)
        start_ns = self._results[0].ts_ns if self._results else None
        end_ns = self._results[-1].ts_ns if self._results else None
        self._tab_map.update_data(cam_key, self._rois, analysis, start_ns, end_ns)
        self._tab_run.update_data(self._results, self._rois, analysis, t)

    # ── CSV EXPORT ────────────────────────────────────────────────────────────

    @staticmethod
    def _write_full_csv(path: str, cam_key: str, rois: list, results: list):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            header = ["camera", "timestamp_ns", "datetime_prague", "frame_mean"]
            for roi in rois:
                header += [f"{roi.name}_brightness", f"{roi.name}_norm", f"{roi.name}_alive"]
            w.writerow(header)
            for sp in results:
                dt = _ns_to_dt(sp.ts_ns)
                row = [cam_key, sp.ts_ns, dt.strftime("%Y-%m-%d %H:%M:%S"),
                       f"{getattr(sp, 'frame_mean', 1.0):.6f}"]
                for i in range(len(rois)):
                    mean_v = sp.roi_means[i] if i < len(sp.roi_means) else 0.0
                    norm_v = sp.roi_norms[i] if i < len(sp.roi_norms) else 0.0
                    alive_v = 1 if (i < len(sp.roi_alive) and sp.roi_alive[i]) else 0
                    row += [f"{mean_v:.6f}", f"{norm_v:.6f}", str(alive_v)]
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
            self._write_full_csv(path, cam_key, self._rois, self._results)
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
        # 2) Per-camera: full CSV + map PNG + run-graph PNG
        keep_cam = self._result_cam_combo.currentText()
        for cam_key, results in self._results_by_camera.items():
            rois = self._rois_by_camera.get(cam_key, [])
            if not results or not rois:
                continue
            try:
                p = folder_p / f"pulser_{cam_key}.csv"
                self._write_full_csv(str(p), cam_key, rois, results)
                written.append(p.name)
            except Exception as exc:
                errors.append(f"{cam_key} csv: {exc}")
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
        # Restore the previously viewed camera.
        if keep_cam:
            self._result_cam_combo.setCurrentText(keep_cam)
        msg = f"Saved {len(written)} file(s) to:\n{folder}"
        if errors:
            msg += "\n\nProblems:\n" + "\n".join(errors)
        QMessageBox.information(self, "Export all", msg)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def main():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ELIBeamlines.PulserMonitor")
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget     { background: #f3f3f3; color: #111; }
        QLabel      { background: transparent; }
        QPushButton { padding: 5px 8px; }
        QComboBox   { padding: 3px 6px; }
        QGroupBox   { font-weight: 600; border: 1px solid #ccc; border-radius: 4px;
                      margin-top: 6px; padding-top: 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; }
        QScrollBar:vertical { width: 8px; background: #f0f0f0; border: none; }
        QScrollBar::handle:vertical { background: #bbb; border-radius: 4px; min-height: 20px; }
        QTabWidget::pane { border: 1px solid #ccc; }
        QTabBar::tab { padding: 5px 14px; }
        QTabBar::tab:selected { background: #fff; border-bottom: 2px solid #1565C0;
                                font-weight: 600; }
    """)

    win = QMainWindow()
    win.setWindowTitle("Pulser Monitor")
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
        here = Path(__file__).resolve().parent
        for icon_name in ("icon.ico", "icon.png", "pulser_monitor.ico"):
            icon_path = here / icon_name
            if icon_path.exists():
                win.setWindowIcon(QIcon(str(icon_path)))
                break
    except Exception:
        pass

    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
