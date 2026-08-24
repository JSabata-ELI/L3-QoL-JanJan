# if_t.py — Image Finder (PySide6 port)

import bisect
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image as PilImage

from PySide6.QtCore import Qt, QTimer, QDate, QRunnable, QThreadPool, QObject, Signal, QPointF, QRect, QLocale
from PySide6.QtGui import (QColor, QTextCharFormat, QPixmap, QImage, QFont, QCursor,
                           QPainter, QPen, QPalette, QImageWriter)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QSlider,
    QScrollArea, QFrame, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCalendarWidget, QDialog,
    QFileDialog, QMessageBox, QLineEdit, QMainWindow, QStyledItemDelegate,
    QDialogButtonBox, QSizePolicy, QSplitter, QTabWidget, QProgressBar,
    QButtonGroup, QSpinBox, QToolButton, QMenu, QStyle,
    QListWidget, QListWidgetItem, QGroupBox, QStackedWidget, QColorDialog,
)

try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    import warnings
    warnings.warn("zoneinfo not available; falling back to UTC for Prague time", RuntimeWarning)
    PRAGUE = timezone.utc

import socket as _socket

def _detect_is_lab() -> bool:
    h = _socket.gethostname().upper()
    return any(h.startswith(p) for p in ("OPR1", "OPR2", "OPR3", "VIS01", "VIS02"))

_IS_LAB = _detect_is_lab()

# ── CONFIG ────────────────────────────────────────────────────────────────────
IMAGES_ROOT_BASE = r"//users-L3.tier0.lcs.local"

RAMPING_CANDIDATES = [
    ("Lab",    r"//hapls-share.cs.eli-beams.eu/scratch/Salvation/2026_alldata"),
    ("Office", r"Z:\Salvation\2026_alldata"),
]
DEFAULT_RAMPING_SOURCE = 0 if _IS_LAB else 1
RAMPING_CSV_GLOB       = "*.csv"

MAX_SCAN_FILES      = 2000  # max files to stat() per folder (network perf)

ACT_MAX_GAP_S      = 120
MIN_SEG_ROWS       = 25
MIN_SEG_DURATION_S = 10 * 60

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
CAM_33HZ   = {65, 66, 67, 63, 64, 60, 57, 58, 53, 54, 55, 56,
              31, 26, 22, 21, 25, 13, 14}

FINAL_RE  = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{3}|\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d{6})$")
SOURCE_RE = re.compile(r"(\d+)$")

# ── ENERGY CSV CONFIG ─────────────────────────────────────────────────────────
# Root folder where daily CSV files live
# Salvation is NOT running (as of 2026-08-19), so no new daily CSV is written and
# this fallback yields nothing for recent days — energy comes from the CPVA archiver
# alone. Kept wired up on purpose: historical days still have their CSV, and the
# fallback costs nothing until the archiver answers a day with no samples.
ENERGY_CSV_ROOT = r"//hapls-share.cs.eli-beams.eu/scratch/Salvation/2026_alldata"

# File name pattern: dataof{year}{MonthAbbr}_{day}  e.g. dataof2026Mar_24
# Python strftime format used to build the filename from a datetime:
ENERGY_CSV_NAME_FMT = "dataof%Y%b_%d"   # e.g. dataof2026Mar_24

# Columns available for annotation — edit this list to add/remove columns.
# These must match the CSV header exactly (case-sensitive).
ENERGY_COLUMNS_AVAILABLE = [
    "waveplate", "ptm1", "pcm2", "pcm4", "pap1", "sbw4",
    "CampOn", "E2_Open", "E3_Open", "E4_Open", "E5_Open", "Back_Ref",
]

# Default selected columns shown pre-checked in the column picker dialog
ENERGY_COLUMNS_DEFAULT = []

# The CSV-column → label table that used to live here is gone: what a PV is CALLED now
# comes from the shared registry (_pv_label_for → PV_LABELS), which is also where the
# operator's own name for it is typed. Two label tables meant a PV could be "SBW4" in
# one tab and "Compressed SBW4" in the other while both read the same channel.

# Match tolerance in seconds: |t_image - t_csv| must be ≤ this value.
# The daily energy CSV is logged irregularly (~20 s median between rows, gaps up
# to several minutes), NOT per shot — so a 2 s window left most images showing
# "—". The energies (SBW4/PTM1/…) change slowly, so the nearest sample within a
# couple of minutes is a faithful value for the image's shot.
ENERGY_MATCH_TOL_S = 120.0
# API (archiver) data is per-shot, not the sparse ~20 s CSV log — with a 120 s
# window a dark frame could inherit the PREVIOUS shot's energy. Revert to 120.0
# if operators prefer the old behaviour.
ENERGY_MATCH_TOL_API_S = 30.0

# ── CPVA ARCHIVER API ─────────────────────────────────────────────────────────
CPVA_BASE_URL     = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_HTTP_TIMEOUT = 10.0   # seconds per request

# Channel used to find the best shot (highest energy = real shot, not dark/empty)
CPVA_SHOT_CHANNEL = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"

def _import_cpva_client():
    """Load the shared CPVA client (sibling cpva_client.py). Reuses an
    already-loaded instance so every tool (and re-exec'd module copy) shares
    one connection pool and one day cache."""
    import importlib.util as _ilu
    mod = sys.modules.get("cpva_client")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "cpva_client.py"
    spec = _ilu.spec_from_file_location("cpva_client", p)
    mod = _ilu.module_from_spec(spec)
    sys.modules["cpva_client"] = mod   # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


cpva = _import_cpva_client()


def _import_img_scale():
    """Load the shared intensity-scale helper (sibling img_scale.py) the same way
    as cpva_client: one instance per process, registered before exec."""
    import importlib.util as _ilu
    mod = sys.modules.get("img_scale")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "img_scale.py"
    spec = _ilu.spec_from_file_location("img_scale", p)
    mod = _ilu.module_from_spec(spec)
    sys.modules["img_scale"] = mod     # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


img_scale = _import_img_scale()

# Maps energy CSV column name → CPVA archiver channel name for API lookup
CPVA_CHANNEL_MAP: dict[str, str] = cpva.CHANNEL_MAP
# Was a second hard-coded literal up with CPVA_SHOT_CHANNEL and went stale when
# SBW4 was renamed — the channel name now comes from cpva_client only.
CPVA_SBW4_CHANNEL = cpva.SBW4_CHANNEL

# ── PV registry, shared with the Image Slider ────────────────────────────────
# The Slider module OWNS the registry — the presets, the PVs the operator added, the
# names given to them, their units and the formulas — and its picker is the one editor
# for it (PvConfigDialog). This tab reads that same module-level registry instead of
# keeping a second list of CSV column names beside it, which is what let "SBW4" mean
# one number here and a different one there.
#
# A picked PV is therefore identified by its REGISTRY NAME ("SBW4", "Compressed SBW4",
# an added channel's own name, a formula's name) — not by a CSV column name as it used
# to be. The CSV columns that only ever existed in Salvation's file (CampOn, E2..E5
# Open) are still reachable: type the column name into the picker's search box and the
# CSV fallback below finds it.
def _pv_all_names() -> "list[str]":
    return _get_slider_module().pv_all_names()


def _pv_channel_for(name: str) -> "str | None":
    """The archiver channel a picked PV reads. None for a formula (computed, not read)
    and for a CSV-only column. An added PV's name IS its channel."""
    sl = _get_slider_module()
    if sl.pv_is_derived(name):
        return None
    ch = sl.pv_channel_for(name)
    if ch:
        return ch
    return None if name in ENERGY_COLUMNS_AVAILABLE else name


def _pv_csv_col(name: str) -> "str | None":
    """The daily-CSV column for a picked PV, or None if it has none.

    Salvation stopped writing on 2026-08-19, so this only decides what a HISTORICAL
    day can still serve — and an arbitrary archiver PV was never in that file."""
    col = _get_slider_module().PV_DISPLAY_TO_COL.get(name)
    if col in ENERGY_COLUMNS_AVAILABLE:
        return col
    return name if name in ENERGY_COLUMNS_AVAILABLE else None


def _pv_scale_for(name: str) -> float:
    """The registry's own factor for a NAMED PV.

    1.0 for everything today: the one entry that carried a factor ("Compressed SBW4")
    is a FORMULA in the shared registry now, so the conversion is visible in the picker
    instead of being applied out of sight. This stays as the single place a
    registry-wide factor would live — never a number typed per tab, which is how this
    tab came to apply 0.749 to SBW4 and report a different SBW4 than every other tab."""
    try:
        return float(_get_slider_module().PV_SCALE.get(name, 1.0)) or 1.0
    except (TypeError, ValueError):
        return 1.0


def _pv_label_for(name: str) -> str:
    return _get_slider_module().pv_label_for(name)


def _pv_unit_for(name: str) -> str:
    return _get_slider_module().pv_units_for(name)


_SLIDER_MOD = None


def _get_slider_module():
    """Borrow helpers (GRADIENTS, _copy_metadata_into_png, …) from the Image
    Slider module WITHOUT re-executing 13k lines of is_t.py on every use —
    prefer the instance main.py already loaded, else load once and cache."""
    global _SLIDER_MOD
    mod = sys.modules.get("image_slider")
    if mod is not None:
        return mod
    if _SLIDER_MOD is None:
        import importlib.util as _ilu
        p = Path(__file__).resolve().parent / "is_t.py"
        spec = _ilu.spec_from_file_location("is_t_helpers", p)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SLIDER_MOD = mod
    return _SLIDER_MOD


def _cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                        timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    """Fetch archiver samples via the shared pooled client (kept as a thin
    wrapper so existing call sites stay unchanged). Raises cpva.CpvaError."""
    return cpva.fetch_samples(channel, start_ns, end_ns, timeout=timeout)


def _cam_totalpower_channel(cam_name: str) -> "str | None":
    """
    Derive the CPVA TotalPower channel from a camera folder name.
    e.g. "C03-033-PAM1FF-_-IMG" → "C03-033-PAM1FF:TotalPower"
         "C03-033-PAM1FF_-_IMG"  → "C03-033-PAM1FF:TotalPower"
    Returns None if pattern not recognised.
    """
    m = re.match(r"^(C\d{2}-\d{2,3}-[A-Za-z0-9]+)[-_]", cam_name)
    if m:
        return f"{m.group(1)}:TotalPower"
    return None


def _cpva_active_windows_ns(channel: str, start_ns: int, end_ns: int,
                             timeout: float = CPVA_HTTP_TIMEOUT,
                             merge_gap_ns: int = 300_000_000_000,
                             ref_start_ns: "int | None" = None,
                             ref_end_ns:   "int | None" = None,
                             active_from_ns: "int | None" = None,
                             debug_log=None) -> "list[tuple[int,int]]":
    """
    Query TotalPower channel and return merged time windows where camera was active.

    Threshold is derived dynamically from a reference window (6–7h Prague time,
    passed as ref_start_ns / ref_end_ns).  Samples in that window represent
    background noise.  Only samples whose value exceeds 10× the reference median
    (and at least 10× the global minimum) are considered active.

    If no reference samples are available the absolute minimum across the whole
    day is used as the baseline.

    Gaps shorter than merge_gap_ns (default 5 min) are bridged.
    Returns list of (window_start_ns, window_end_ns) tuples, empty on failure.
    """
    dbg = debug_log or (lambda *_: None)
    try:
        samples = _cpva_fetch_samples(channel, start_ns, end_ns, timeout=timeout)
        if not isinstance(samples, list) or not samples:
            dbg(f"  TotalPower debug: no samples returned (got {type(samples).__name__})")
            return []

        # Parse all values
        parsed: list[tuple[int, float]] = []  # (t_ns, value)
        for s in samples:
            t_ns = s.get("time")
            if t_ns is None:
                continue
            val = s.get("value")
            if isinstance(val, list):
                val = val[0] if len(val) == 1 else None
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            parsed.append((int(t_ns), v))

        if not parsed:
            dbg(f"  TotalPower debug: {len(samples)} raw samples, 0 parseable")
            return []

        # ── Determine threshold from data distribution ────────────────────────
        import statistics as _stats

        all_vals = sorted(v for _, v in parsed)
        n = len(all_vals)

        # Use the 5th-percentile as background baseline (robust against cameras
        # that run all day — their 6–7h ref window would be high too, so a
        # fixed ref window is unreliable).
        p05_idx = max(0, int(n * 0.05))
        p95_idx = min(n - 1, int(n * 0.95))
        baseline  = all_vals[p05_idx]
        p95_val   = all_vals[p95_idx]

        dynamic_range = p95_val - baseline

        dbg(f"  TotalPower debug: {len(parsed)} samples | p05={baseline:.3e} p95={p95_val:.3e} ratio={p95_val/baseline:.2f}" if baseline > 0 else f"  TotalPower debug: {len(parsed)} samples | p05={baseline:.3e} p95={p95_val:.3e}")

        if baseline > 0 and p95_val / baseline < 1.5:
            # Signal is flat — camera runs at constant power all day (or is always off).
            # Treat entire queried range as one active window so the caller picks
            # a timestamp from the middle of the day.
            dbg(f"  TotalPower debug: flat signal (ratio<1.5) — treating whole day as active")
            window_start = parsed[0][0]
            window_end   = parsed[-1][0]
            result = [(window_start, window_end)]
            if active_from_ns is not None:
                result = [(max(ws, active_from_ns), we) for ws, we in result if we >= active_from_ns]
            return result

        if dynamic_range > baseline * 0.5 and dynamic_range > 1e-9:
            # Clear on/off signal: threshold at baseline + 30% of dynamic range
            threshold = baseline + dynamic_range * 0.30
        else:
            # Small dynamic range but not flat — use 10× floor
            threshold = max(baseline * 10.0, 1e-6)

        # ── Collect active timestamps ─────────────────────────────────────────
        active_ts = [t for t, v in parsed if v > threshold]
        if not active_ts:
            dbg(f"  TotalPower debug: 0 active samples above threshold={threshold:.3e}")
            return []
        active_ts.sort()

        # ── Merge into windows ────────────────────────────────────────────────
        windows: list[tuple[int, int]] = []
        w_start = active_ts[0]
        w_end   = active_ts[0]
        for t in active_ts[1:]:
            if t - w_end <= merge_gap_ns:
                w_end = t
            else:
                windows.append((w_start, w_end))
                w_start = t
                w_end   = t
        windows.append((w_start, w_end))

        # Drop windows that end before active_from_ns (reference-only period)
        if active_from_ns is not None:
            windows = [(ws, we) for ws, we in windows if we >= active_from_ns]

        return windows
    except Exception as _e:
        dbg(f"  TotalPower debug: exception — {type(_e).__name__}: {_e}")
        return []


def _energy_shots_ranked(channel: str, date_key: str, start_ns: int, end_ns: int,
                         timeout: float = CPVA_HTTP_TIMEOUT,
                         debug_log=None) -> "list[tuple[int, float]]":
    """Every shot the laser fired on one energy channel that day, strongest first.

    This is what tells the multi-day search WHEN the machine was actually running. It
    reads through `cpva.get_day`, which caches by Prague day, so a 15-day × 40-camera
    search asks the archiver 15 times per channel instead of 600 — the same series was
    previously re-fetched for every camera.

    An energy channel reads ~0 between shots, so "there was signal" is simply "a sample
    stands well above the day's floor". The cut is 2 % of the day's peak: high enough
    that readout noise is not mistaken for a shot, low enough that a weak but real day
    still counts. An empty list therefore means "the laser did not fire on this channel
    today", not "the query failed" — failures are logged and also come back empty, which
    is what makes the caller fall through to the next channel.
    """
    dbg = debug_log or (lambda *_: None)
    try:
        res = cpva.get_day(channel, date_key, timeout=timeout)
    except Exception as e:
        dbg(f"  {channel}: query failed — {type(e).__name__}: {e}")
        return []
    samples = list(getattr(res, "samples", None) or ())
    if not samples:
        dbg(f"  {channel}: no samples ({getattr(res, 'status', '?')})")
        return []
    shots = []
    for t_ns, val in samples:
        if not (start_ns <= int(t_ns) <= end_ns):
            continue
        try:
            v = float(val[0] if isinstance(val, (list, tuple)) else val)
        except (TypeError, ValueError):
            continue
        shots.append((int(t_ns), v))
    if not shots:
        dbg(f"  {channel}: {len(samples)} sample(s), none inside the searched hours")
        return []
    peak = max(v for _, v in shots)
    if peak <= 0:
        dbg(f"  {channel}: all samples zero — laser did not fire")
        return []
    cut = peak * 0.02
    shots = [s for s in shots if s[1] > cut]
    shots.sort(key=lambda s: s[1], reverse=True)
    dbg(f"  {channel}: {len(shots)} shot(s) above {cut:.4g}, peak {peak:.4g}")
    return shots


# Annotation bar appearance
ENERGY_BAR_HEIGHT_PX    = 40    # height of white bar added below image
ENERGY_BAR_FONT_SIZE_PT = 24   # font size for annotation text
ENERGY_BAR_BG_COLOR     = (255, 255, 255)   # RGB white
ENERGY_BAR_TEXT_COLOR   = (0,   0,   0)     # RGB black

INFO_TEXT = """\
Image Finder — Image Tools

Steps:
  1. Pick a date — auto-hour is chosen from ramping CSV.
     If "Lab time?" is unchecked: program looks for folders in Prague time.
       Example: for 19:00, it looks in folder 18:00 in the archiver.
     If "Lab time?" is checked: looks in lab time (1 hour later).
  2. Click "Cameras..." and pick one or more cameras.
     Search by name or number; a click adds or removes a camera.
  3. The picked cameras are listed under the Workshop button.
     Click one to preview it, double-click it to unpick it.
  4. Click "Load data" (Source group) to read the images, or Save As...
     to copy them to a folder.
  5. Enable "Auto-open in Slider" to automatically switch to the
     Image Slider tab and load the first selected camera folder.
  6. Use Add to A / Add to B + Compare A vs B for diff comparison.
"""

# ── GRADIENTS ─────────────────────────────────────────────────────────────────
def _make_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]; t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                lut[i] = tuple(int(c0[k] + f * (c1[k] - c0[k])) for k in range(3))
                break
    return lut

def _make_binary_lut():
    lut = np.zeros((256, 3), dtype=np.uint8)
    lut[128:] = 255
    return lut

def _make_stepped_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        color = stops[-1][1]
        for j in range(len(stops) - 1):
            if t < stops[j + 1][0]:
                color = stops[j][1]
                break
        lut[i] = color
    return lut

# NI Vision "Binary", measured off the real viewer — same table and same rule as in
# is_t.py, where how it was measured is written down. 15 colours, black below the first
# band, one colour per 1024 stored 16-bit units.
_NI_BINARY_CYCLE = [
    (255,0,0), (0,255,0), (0,0,255), (255,255,0), (255,0,255), (0,255,255),
    (255,127,0), (255,0,127), (127,255,0), (127,0,255), (0,127,255), (0,255,127),
    (255,127,127), (127,255,127), (127,127,255),
]
NI_BINARY_BAND = 1024
_NI_BINARY_TABLE = np.array([(0, 0, 0)] + _NI_BINARY_CYCLE, dtype=np.uint8)

def _make_ni_binary_lut():
    """The NI Binary rule as a 256-entry LUT over the ABSOLUTE 8-bit scale.

    This tab renders from 8-bit images, so unlike the Image Slider it cannot take the
    exact 16-bit path: one code is 257 stored units against a 1024-unit band, so a code
    on a band edge can land one colour out."""
    codes = np.arange(256, dtype=np.int64) * 257
    band = codes // NI_BINARY_BAND
    idx = np.where(band <= 0, 0, (band - 1) % 15 + 1)
    return _NI_BINARY_TABLE[idx]

# "False Colors" (same definition as in is_t.py): dark blue → violet → purple →
# magenta → pink → white, with the stops crowded at the bottom so faint detail gets
# most of the colour range. One colour family on purpose — a spectrum here only
# duplicates Gradient / Jet / Turbo.
_FALSE_COLORS_STOPS = [
    (0.00, (0,0,0)), (0.03, (25,0,70)), (0.07, (45,0,120)), (0.12, (70,0,160)),
    (0.20, (100,0,180)), (0.30, (130,5,185)), (0.42, (160,20,180)),
    (0.55, (190,40,175)), (0.68, (215,70,170)), (0.80, (235,105,170)),
    (0.90, (247,150,185)), (0.96, (252,200,215)), (1.00, (255,255,255)),
]

# "Rainbow" (same definition as in is_t.py): the NI Vision palette — blue to red with
# a prominent green middle, 0 black and 255 white.
_RAINBOW_STOPS = [
    (0.00, (0,0,0)), (0.04, (0,0,200)), (0.14, (0,40,255)), (0.26, (0,150,255)),
    (0.36, (0,230,180)), (0.46, (0,255,80)), (0.56, (90,255,0)), (0.66, (190,255,0)),
    (0.76, (255,220,0)), (0.86, (255,120,0)), (0.94, (255,0,0)), (1.00, (255,255,255)),
]

GRADIENTS = {
    "Default":         None,
    "Grayscale":       None,
    "Gradient":        _make_lut([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
    "Binary":          _make_ni_binary_lut(),
    "False Colors":    _make_lut(_FALSE_COLORS_STOPS),
    "Rainbow":         _make_lut(_RAINBOW_STOPS),
    # Red / yellow lowered, pale yellow added, white kept at the top; see is_t.py.
    "Hot":             _make_lut([(0,(0,0,0)),(0.27,(255,0,0)),(0.53,(255,255,0)),(0.78,(255,255,190)),(1,(255,255,255))]),
    "Black and White": _make_binary_lut(),
    "Viridis":         _make_lut([(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))]),
    "Plasma":          _make_lut([(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))]),
    "Inferno":         _make_lut([(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))]),
    "Jet":             _make_lut([(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))]),
    "Turbo":           _make_lut([(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))]),
}
GRADIENT_NAMES = list(GRADIENTS.keys())

# Palettes mapped onto the frame's own p0.5..p99.5 window instead of the absolute
# 0..255 scale — see is_t.ADAPTIVE_PALETTES for why this one and no others.
ADAPTIVE_PALETTES = frozenset({"False Colors"})
# Cyclic palettes are ABSOLUTE: NI's Binary bands are fixed at 1024 stored units and do
# not follow the frame, so no stretch may run first (see is_t.CYCLIC_PALETTES).
CYCLIC_PALETTES = frozenset({"Binary"})


def _palette_normalize(arr):
    """uint8 frame → uint8 spread over its OWN p0.5..p99.5 window."""
    a = arr if arr.size <= 250_000 else np.ravel(arr)[::(arr.size // 250_000) | 1]
    lo = float(np.percentile(a, 0.5))
    hi = float(np.percentile(a, 99.5))
    if hi <= lo:
        lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        return arr
    return np.clip((arr.astype(np.float32) - lo) * (255.0 / (hi - lo)),
                   0, 255).astype(np.uint8)


def _lut_pixels(lut, arr, name: str):
    """RGB pixels for `arr` under `lut`.

    The adaptive palettes are spread over p0.5..p99.5; everything else, cyclic palettes
    included, is the raw absolute scale."""
    if name in ADAPTIVE_PALETTES:
        arr = _palette_normalize(arr)
    return lut[arr]

_CHECKBOX_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
"""

# ── STANDALONE HELPERS ────────────────────────────────────────────────────────
def _app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def load_readme_text() -> str:
    base = _app_dir()
    for fname in ("README.txt", "README.md", "readme.txt", "readme.md"):
        p = base / fname
        try:
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return INFO_TEXT


def _read_img_max_value(path: Path) -> "float | None":
    """The frame's PEAK in raw counts, from PNG tEXt metadata (`MaxValue`).

    Not the sensor's range — it reads 4095 only because a saturated 12-bit frame's
    peak IS 4095. Used here for the empty-frame test; the display scale does not need
    it at all (see img_scale).

    This used to take tEXt chunk number 12 (the Matlab `imgMeta.OtherText{12,2}`
    idiom), i.e. it identified the tag by POSITION: correct for the files we have and
    an arbitrary other number for anything written by a different IMAQ version, which
    then decided "empty" for a perfectly good frame. Now looked up by name."""
    return img_scale.read_max_value(path)


# ── Contrast / Brightness / Gamma rows ─────────────────────────────────────
# Built exactly like the Image Slider's block, down to the short names and the readout
# widths: the same three controls with the same rule (an Auto checkbox overrides its own
# row's slider) have to look the same in both tabs, or the operator learns them twice.
# The names are shortened to Con / Bri / Gam so a numeric readout of the value actually
# in use fits on the same row — acceptable only because the full name and the meaning
# are one hover away: the tooltip sits on the name label, the slider AND the readout.
_BC_NAME_W = 34          # room for "Con:" / "Bri:" / "Gam:" so the three sliders align
_BC_VALUE_W = 38         # room for "-127", "-255", "0.10" without the row jittering

_TT_CONTRAST = (
    "Contrast (-127 to +127) — multiplicative gain around the frame's own black level.\n"
    "0 = untouched; positive spreads the values apart, negative squeezes them together.")
_TT_BRIGHTNESS = (
    "Brightness (-255 to +255) — additive offset: the number is added to every pixel.\n"
    "0 = untouched; positive lifts the whole frame, negative darkens it.")
_TT_GAMMA = (
    f"Gamma ({img_scale.GAMMA_MIN:.2f}–{img_scale.GAMMA_MAX:.2f}) — the number shown is "
    "the exponent of the display curve.\n"
    "1.00 = linear absolute scale. Below 1 lifts the dark end (0.50 is the usable "
    "working point on these cameras); above 1 darkens.\n"
    "Comparability survives — the same pixel value always gives the same colour. "
    "What changes is that equal count differences stop looking equally big.")


def _bc_value_label(text: str, tooltip: str) -> "QLabel":
    """Read-only numeric readout for a Contrast/Brightness/Gamma row."""
    lbl = QLabel(text)
    lbl.setFixedWidth(_BC_VALUE_W)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setToolTip(tooltip)
    lbl.setStyleSheet(
        "QLabel { font-weight: 700; color: #111; }"
        "QLabel:disabled { color: #9a9a9a; }")
    return lbl


def _render_u8(arr, auto: bool, full_scale: float = None, gamma=None,
               contrast: int = 0, offset: int = 0, out: "dict | None" = None):
    """Decoded frame → uint8 for display. Every render path in this tab goes through
    here, so the Finder cannot drift from the Slider on what an intensity means.

    Absolute unless an Auto box is ticked — see img_scale.render_u8. `contrast` and
    `offset` are the manual pair, applied on top of the mapping. `gamma` is in
    slider units and bends the absolute curve without costing comparability. What this
    replaced was `MaxValue * arr / arr.max()` followed by `/4095`, which was the right
    answer only for 12-bit cameras: it rendered the 6–11 bit diode cameras nearly black
    and, with the tEXt chunk missing, blew every frame out to white."""
    if full_scale is None:
        full_scale = img_scale.FULL_SCALE_16
    return img_scale.render_u8(arr, auto, full_scale, gamma, contrast, offset, out)


def _scale_note(info: dict, arr, auto: bool, full_scale: float = None, gamma=None,
                path=None, pil_mode: str = None,
                contrast: int = 0, offset: int = 0,
                gamma_applied: "float | None" = None) -> str:
    """One line saying what the displayed intensities mean, for the label under the
    preview. `info` is an already-open image's `.info` — never re-open the file for it,
    a share read costs 130–160 ms.

    A gamma other than 1 is named here because it moves which count a colour sits on;
    Auto gamma is resolved from the frame so the number shown is the one applied. A
    manual contrast or brightness is named for exactly the same reason. Pass
    `gamma_applied` when the render already resolved Auto gamma, so this does not repeat
    the median pass over the frame.

    "8-bit source" is decided by the PIL MODE when it is given: a 16-bit frame drawn on
    its camera's reference range also has a full_scale of its own (see img_scale), so
    testing the number alone would label it an 8-bit file."""
    applied = None
    if not auto:
        applied = gamma_applied
        if applied is None:
            applied = (img_scale.auto_gamma(arr, full_scale or img_scale.FULL_SCALE_16)
                       if img_scale.is_auto_gamma(gamma)
                       else img_scale.gamma_from_slider(gamma))
    is_8bit = (pil_mode not in ("I", "I;16")) if pil_mode is not None else (
        full_scale is not None and full_scale != img_scale.FULL_SCALE_16)
    if is_8bit:
        mode = "auto stretch" if auto else "absolute scale"
        if applied is not None and abs(applied - img_scale.GAMMA_NEUTRAL) > 0.005:
            mode += f"  ·  gamma {applied:.2f}"
        if contrast:
            mode += f"  ·  contrast {int(contrast):+d}"
        if offset:
            mode += f"  ·  brightness {int(offset):+d}"
        return f"8-bit source  ·  {mode}"
    return img_scale.meta_from_info(info, arr).scale_note(
        auto, gamma, applied,
        img_scale.current_reference_bits(img_scale.camera_from_path(path))
        if path is not None else None,
        contrast, offset)


# Frames whose physical max pixel value is below this are considered "empty"
# (dark frame, no beam). Camera dark noise is typically tens of counts on the
# 12/16-bit sensors here; real shots reach thousands. Tune if a camera differs.
EMPTY_IMG_MAX_THRESHOLD = 100.0
# Pixel-fallback: minimum (max − median) contrast in raw counts to call a
# downscaled decode non-empty.
EMPTY_IMG_CONTRAST_MIN = 50.0


def _image_is_nonempty(path: Path, log=None) -> bool:
    """True when the image plausibly contains a beam (not a dark frame).

    imgMaxValue PNG metadata first (no decode); pixel fallback decodes a
    downscaled copy and checks max−median contrast. Validation failures count
    as EMPTY so the caller moves on to the next candidate."""
    try:
        mv = _read_img_max_value(path)
        if mv is not None:
            ok = mv > EMPTY_IMG_MAX_THRESHOLD
            if log and not ok:
                log(f"  {path.name}: imgMaxValue={mv:.0f} ≤ {EMPTY_IMG_MAX_THRESHOLD:.0f} → empty")
            return ok
        import numpy as _np
        with PilImage.open(str(path)) as pil:
            pil.draft("L", (256, 256))
            if pil.mode in ("I", "I;16"):
                arr = _np.asarray(pil, dtype=_np.float32)
            else:
                arr = _np.asarray(pil.convert("L"), dtype=_np.float32)
        contrast = float(arr.max()) - float(_np.median(arr))
        ok = contrast > EMPTY_IMG_CONTRAST_MIN
        if log and not ok:
            log(f"  {path.name}: pixel contrast {contrast:.0f} ≤ "
                f"{EMPTY_IMG_CONTRAST_MIN:.0f} → empty")
        return ok
    except Exception as e:
        if log:
            log(f"  {path.name}: validation failed ({type(e).__name__}) → treated as empty")
        return False


# ── CALENDAR WEEKEND DELEGATE ─────────────────────────────────────────────────

class _WeekendDelegate(QStyledItemDelegate):
    """
    Copied from is.py WeekendDelegate.
    Colours So/Ne columns red for ALL days — including out-of-month days
    that setWeekdayTextFormat() does not affect.

    QCalendarWidget internal table (with week numbers col 0):
      col 0 = week numbers  col 1=Mo  col 2=Tu  col 3=We
      col 4=Th  col 5=Fr  col 6=Sa  col 7=Su

    Primary check: Qt UserRole gives a QDate — use dayOfWeek() 6/7.
    Fallback: col 6 or 7 are always Sa/Su regardless of locale setting
              because we force setFirstDayOfWeek(Monday).
    """
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        col = index.column()
        if col < 1:
            return
        date = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(date, QDate) and date.isValid():
            if date.dayOfWeek() in (6, 7):
                option.palette.setColor(
                    option.palette.ColorRole.Text, QColor("#cc0000"))
                option.palette.setColor(
                    option.palette.ColorRole.ButtonText, QColor("#cc0000"))
            return
        # Fallback for days where UserRole is not a valid QDate
        if col in (6, 7):
            option.palette.setColor(
                option.palette.ColorRole.Text, QColor("#cc0000"))
            option.palette.setColor(
                option.palette.ColorRole.ButtonText, QColor("#cc0000"))


class _CalBorderDelegate(_WeekendDelegate):
    """WeekendDelegate + zelený/červený border pro From/To datum výběru."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._from_date: "QDate | None" = None
        self._to_date:   "QDate | None" = None

    def set_bounds(self, from_date: "QDate | None", to_date: "QDate | None"):
        self._from_date = from_date
        self._to_date   = to_date

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        date = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(date, QDate) or not date.isValid():
            return
        if date == self._from_date:
            pen = QPen(QColor("#00bb00")); pen.setWidth(3)
            painter.save(); painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(2, 2, -2, -2))
            painter.restore()
        elif date == self._to_date:
            pen = QPen(QColor("#cc0000")); pen.setWidth(3)
            painter.save(); painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(2, 2, -2, -2))
            painter.restore()


class _NoScrollCalendar(QCalendarWidget):
    """QCalendarWidget whose internal view ignores mousewheel scrolling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._noscroll_installed = set()

    def _install_on_all_children(self):
        """Install event filter on every QAbstractItemView child."""
        from PySide6.QtWidgets import QAbstractScrollArea
        for child in self.findChildren(QAbstractScrollArea):
            if id(child) not in self._noscroll_installed:
                child.installEventFilter(self)
                child.viewport().installEventFilter(self)
                self._noscroll_installed.add(id(child))

    def showEvent(self, event):
        super().showEvent(event)
        self._install_on_all_children()

    def wheelEvent(self, event):
        event.accept()   # consume — do NOT propagate

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.Wheel:
            event.accept()   # consume — do NOT propagate
            return True
        return super().eventFilter(obj, event)


# ── STANDARD CALENDAR LOOK ────────────────────────────────────────────────────
# The house style for every QCalendarWidget in this app. Without it a calendar
# inherits the app's dark stylesheet and comes out with a red/brown background
# and unreadable cells. Rules: Monday first, white day cells, Sat/Sun in red,
# and day-name header + week-number column on a slightly darker GRAY band.
_STD_CAL_STYLE = """
QCalendarWidget QWidget { background: #f6f6f6; color: #111; }
QCalendarWidget QAbstractItemView {
    background: #fcfcfc; color: #111;
    selection-background-color: #2d7dff; selection-color: #fff;
    alternate-background-color: #f0f0f0; gridline-color: #d0d0d0; }
QCalendarWidget QTableView {
    background: #fcfcfc;
    selection-background-color: #2d7dff; selection-color: #fff;
    gridline-color: #d0d0d0; outline: 0; }
QCalendarWidget QHeaderView { background: #e8e8e8; }
QCalendarWidget QHeaderView::section {
    background: #e8e8e8; color: #222;
    font-weight: bold; font-size: 10pt;
    padding: 3px 0px; border: none; border-bottom: 1px solid #bbb; }
QCalendarWidget QToolButton {
    background: #efefef; border: 1px solid #c8c8c8;
    padding: 4px 8px; border-radius: 4px; color: #111;
    font-size: 10pt; font-weight: bold; }
QCalendarWidget QSpinBox, QCalendarWidget QComboBox {
    background: #fff; border: 1px solid #c8c8c8;
    padding: 2px 6px; color: #111; font-size: 10pt; font-weight: bold; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #e4e4e4; }
QCalendarWidget QAbstractItemView:enabled { color: #111; }
"""


def _style_calendar(cal: QCalendarWidget) -> None:
    """Apply the house calendar look to `cal`: Monday first, gray header band,
    white readable cells, weekends (Sat/Sun) in red. Use on every calendar so
    none of them inherit the app's dark stylesheet."""
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setGridVisible(True)

    hf = QTextCharFormat()
    hf.setForeground(QColor("#222"))
    hf.setFontWeight(QFont.Weight.Bold)
    cal.setHeaderTextFormat(hf)

    wf = QTextCharFormat()
    wf.setForeground(QColor("#111"))
    for day in (Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday):
        cal.setWeekdayTextFormat(day, wf)
    wf_we = QTextCharFormat()
    wf_we.setForeground(QColor("#cc0000"))
    for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
        cal.setWeekdayTextFormat(day, wf_we)

    cal.setStyleSheet(_STD_CAL_STYLE)


def _make_mpl_toolbar(nav_cls, canvas, parent=None):
    """Build a matplotlib NavigationToolbar with visible icons.

    matplotlib tints the toolbar icons *at construction* and only when the
    palette background is dark — recolouring them to the (light) foreground,
    which under the app's dark palette makes the icons invisible. It never
    re-tints afterwards, so the palette must be right before the toolbar is
    created. We give it a light-palette host parent → tinting is skipped and
    the original black icons survive → then paint a light toolbar background.

    Belt AND braces: the icons are then REPAINTED here in a fixed dark ink, so
    no palette, no style and no Windows dark mode can turn them white again.
    Relying on matplotlib's own decision has already failed once."""
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
    _repaint_mpl_toolbar_icons(toolbar)
    return toolbar


def _repaint_mpl_toolbar_icons(toolbar, ink: str = "#1e2530",
                               ink_off: str = "#9aa0a8"):
    """Redraw every toolbar action's icon from matplotlib's own artwork in `ink`.

    The artwork is black on transparent, so painting the ink THROUGH its own alpha
    keeps the shape and replaces only the colour. Every QIcon mode is spelled out:
    left alone, Qt invents a disabled icon by fading the normal one until it is
    barely there."""
    try:
        from matplotlib import cbook
        from PySide6.QtGui import QIcon, QPainter, QPixmap
    except Exception:
        return
    by_text = {a.text(): a for a in toolbar.actions() if a.text()}
    for item in getattr(toolbar, "toolitems", ()):
        text, _tip, image_file, _cb = item
        act = by_text.get(text)
        if act is None or not image_file:
            continue
        try:
            path = cbook._get_data_path("images", image_file + ".png")
            large = path.with_name(path.name.replace(".png", "_large.png"))
            base = QPixmap(str(large if large.exists() else path))
            if base.isNull():
                continue
            icon = QIcon()
            for mode, col in ((QIcon.Mode.Normal, ink), (QIcon.Mode.Active, ink),
                              (QIcon.Mode.Selected, ink),
                              (QIcon.Mode.Disabled, ink_off)):
                pm = QPixmap(base.size())
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.drawPixmap(0, 0, base)
                p.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceIn)
                p.fillRect(pm.rect(), QColor(col))
                p.end()
                for state in (QIcon.State.Off, QIcon.State.On):
                    icon.addPixmap(pm, mode, state)
            act.setIcon(icon)
        except Exception:
            continue


# ── MULTI-SELECT CALENDAR (ported from CSS Logger/sp_t.py) ────────────────────
_MS_CAL_STYLE = """
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
QCalendarWidget QSpinBox {
    color: #222; background: #eeeeee; border: none; font-weight: 700;
}
QCalendarWidget QMenu { color: #111; background: #fff; }
"""


class _MultiSelectDelegate(QStyledItemDelegate):
    """Paint calendar cells: selected days = blue fill, Sat/Sun = red text, the
    focused day = blue outline. initStyleOption strips State_Selected from every
    cell that is not in the selection, so Qt's own highlight never bleeds through
    and the painted days are exactly the ones the caller selected.

    One widget, one behaviour: this is the Image Slider's calendar delegate, so a
    day looks and clicks the same in every tab."""

    def __init__(self, cal: QCalendarWidget):
        super().__init__(cal)
        self._cal = cal
        self._selected_keys: set = set()     # (year, month, day)
        self._focus_key = None               # (year, month, day) | None

    def _first_cell(self) -> "tuple[int, int]":
        """Row/column of the first *day* cell. Qt drops the header row when
        NoHorizontalHeader is set and the week-number column when
        NoVerticalHeader is set, so the day grid does not always start at (1,1).
        Reading the actual header formats keeps the mapping correct regardless."""
        first_row = 1
        if (self._cal.horizontalHeaderFormat()
                == QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader):
            first_row = 0
        first_col = 1
        if (self._cal.verticalHeaderFormat()
                == QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader):
            first_col = 0
        return first_row, first_col

    def _date_for_index(self, index) -> "QDate | None":
        # The model knows the real date for in-month cells — always prefer it.
        d = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(d, QDate) and d.isValid():
            return d
        first_row, first_col = self._first_cell()
        if index.row() < first_row or index.column() < first_col:
            return None   # header row / week-number column
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

    def _repaint(self):
        view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view is not None:
            view.viewport().update()

    def set_selected(self, dates: "list[QDate]"):
        self._selected_keys = {(d.year(), d.month(), d.day()) for d in dates}
        self._repaint()

    def set_focus_date(self, d: "QDate | None"):
        self._focus_key = None if d is None else (d.year(), d.month(), d.day())
        self._repaint()

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
        key = (d.year(), d.month(), d.day())
        is_weekend = d.dayOfWeek() in (6, 7)     # 6=Sat, 7=Sun
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if key in self._selected_keys:
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
        if key == self._focus_key:
            painter.save()
            painter.setPen(QPen(QColor("#1565C0"), 2))
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.restore()


def _make_multiselect_calendar(initial: "QDate | None" = None) -> "tuple[QFrame, QCalendarWidget]":
    """Return (wrapper_frame, cal) — calendar with a custom gray day-name header,
    a light-gray nav bar (month button + year spinbox) and the multi-select
    weekend delegate installed. Selection state is layered on top by the caller
    via cal._wk_delegate.set_selected()."""
    cal = QCalendarWidget()
    cal.setGridVisible(True)
    cal.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader)
    if initial:
        cal.setSelectedDate(initial)
    cal.setStyleSheet(_MS_CAL_STYLE)

    nav_internal = cal.findChild(QWidget, "qt_calendar_navigationbar")
    if nav_internal:
        nav_internal.hide()

    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    if view is not None:
        cal._wk_delegate = _MultiSelectDelegate(cal)
        view.setItemDelegate(cal._wk_delegate)

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

    prev_btn = QToolButton(); prev_btn.setText("◀")
    prev_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")
    month_btn = QPushButton(); month_btn.setMinimumWidth(100)
    month_btn.setStyleSheet(
        "QPushButton { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " color: #111; font-weight: bold; font-size: 12px; padding: 2px 10px; }"
        "QPushButton:hover { background: #e0e0e0; }")
    year_spin = QSpinBox()
    year_spin.setRange(2000, 2100)
    year_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    year_spin.setStyleSheet(
        "QSpinBox { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " color: #111; padding: 1px 4px; font-weight: bold; font-size: 12px; }")
    year_spin.setFixedWidth(60)
    next_btn = QToolButton(); next_btn.setText("▶")
    next_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")

    nav_lay.addWidget(prev_btn); nav_lay.addStretch()
    nav_lay.addWidget(month_btn); nav_lay.addWidget(year_spin)
    nav_lay.addStretch(); nav_lay.addWidget(next_btn)

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

    hdr_row = QWidget()
    hdr_row.setAutoFillBackground(True)
    hdr_pal = hdr_row.palette()
    hdr_pal.setColor(QPalette.ColorRole.Window, QColor("#bdbdbd"))
    hdr_row.setPalette(hdr_pal)
    hdr_lay = QHBoxLayout(hdr_row)
    hdr_lay.setContentsMargins(0, 0, 0, 0)
    hdr_lay.setSpacing(0)
    for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        colour = "#cc0000" if i >= 5 else "#111111"
        lbl.setStyleSheet(f"color: {colour}; font-weight: 700; padding: 4px 0;")
        hdr_lay.addWidget(lbl, stretch=1)

    wrapper = QFrame()
    wrapper.setStyleSheet("QFrame { border: 1px solid #b0b0b0; border-radius: 3px; }")
    w_lay = QVBoxLayout(wrapper)
    w_lay.setContentsMargins(0, 0, 0, 0)
    w_lay.setSpacing(0)
    w_lay.addWidget(nav_row)
    w_lay.addWidget(hdr_row)
    w_lay.addWidget(cal)
    return wrapper, cal


# ── NO-SCROLL COMBOBOX ────────────────────────────────────────────────────────
class _NoScrollComboBox(QComboBox):
    """QComboBox that ignores mousewheel — prevents accidental value changes."""
    def wheelEvent(self, event):
        event.ignore()

# ── UI HELPERS ────────────────────────────────────────────────────────────────
def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #ccc; margin: 2px 0;")
    return f

def _group_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "font-size: 10px; color: #777; font-weight: 700; "
        "letter-spacing: 1px; padding-top: 2px;"
    )
    return lbl

# Panel group boxes: the Image Slider's own CollapsibleSection, so a group here has
# the same coloured header, the same click-to-fold behaviour and the same pale wash
# of the header colour behind its controls as the groups in the Slider and Workshop.
_SECTION_ACCENTS = {
    "time":     "#2f6fd0",   # blue
    "pv":       "#1a9e9e",   # teal
    "actions":  "#c0392b",   # red
    "display":  "#7a4fc0",   # purple
    "compare":  "#d08a1e",   # amber
}

def _section_cls():
    return _get_slider_module().CollapsibleSection

# ── LOGIC HELPERS (unchanged from original) ───────────────────────────────────
def is_valid_image_file(name: str) -> bool:
    n = name.strip()
    if not n or n.startswith("."): return False
    low = n.lower()
    if low in ("thumbs.db", "desktop.ini"): return False
    return Path(low).suffix in IMAGE_EXTS

def extract_display_label(folder_name: str) -> str:
    """Strip -_-IMG suffix (and variants) from camera folder names, keep the rest."""
    s = folder_name.strip()
    # Remove trailing -_-IMG or _-_IMG (case-insensitive), keep everything before it
    cleaned = re.sub(r"[-_]+IMG$", "", s, flags=re.IGNORECASE).rstrip("-_")
    return cleaned

def extract_folder_number(folder_name: str) -> str:
    s = folder_name.strip()
    # 3-digit: C03-047-...  or  2-digit: C03-47-...
    m = re.match(r"^C\d{2}-(\d{2,3})-", s, flags=re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r"-(?:C\d{2})-(\d{2,3})(?:-|$)", s, flags=re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r"-(C\d{2,3})(?:-|$)", s, flags=re.IGNORECASE)
    if m: return m.group(1).upper()
    return ""

def extract_ns_from_stem(stem: str):
    m = SOURCE_RE.search(stem)
    if not m: return None
    try: return int(m.group(1))
    except: return None

def convert_timestamp(ns: int, use_prague_time: bool) -> str:
    dt_utc = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
    dt = dt_utc.astimezone(PRAGUE) if use_prague_time else dt_utc
    ms = (ns % 1_000_000_000) // 1_000_000
    return dt.strftime("%Y-%m-%d_%H-%M-%S-") + f"{ms:03d}"

_CAM_IMG_MARK_RE = re.compile(r"[-_]+IMG(?=$|[-_])", re.IGNORECASE)
_CAM_CONTAINER_RE = re.compile(r"^C\d{2}[-_]", re.IGNORECASE)

def clean_cam_for_filename(cam: str) -> str:
    """Camera token as it should appear in a saved file name:
    'C03-040-PFM13NF-_-IMG' -> '040-PFM13NF'.

    The '-IMG' marker and the leading container code carry no information for the
    person looking at the file. Cameras without a 'Cxx-' prefix keep whatever
    they have."""
    s = _CAM_IMG_MARK_RE.sub("", cam).strip("-_")
    return _CAM_CONTAINER_RE.sub("", s, count=1).strip("-_")

def build_new_name(stem: str, use_prague_time: bool):
    # cam token + trailing ns timestamp → "<clean cam>_<timestamp>"
    if FINAL_RE.search(stem): return None, "already_converted"
    m = SOURCE_RE.search(stem)
    if not m: return None, "no_trailing_number"
    ns = int(m.group(1))
    cam = clean_cam_for_filename(stem[:m.start(1)])
    time_str = convert_timestamp(ns, use_prague_time)
    return (f"{cam}_{time_str}" if cam else time_str), None


# ── ENERGY CSV ENGINE ────────────────────────────────────────────────────────

def _energy_csv_path(dt: datetime) -> Path:
    """
    Build the path to the daily CSV file for a given datetime.
    Pattern: ENERGY_CSV_ROOT / dataof{year}{MonthAbbr}_{day}
    Example: dataof2026Mar_24  (month abbreviation capitalised as in strftime)
    """
    fname = dt.strftime(ENERGY_CSV_NAME_FMT) + ".csv"   # e.g. "dataof2026Mar_24.csv"
    return Path(ENERGY_CSV_ROOT) / fname


class _EnergyRow:
    """One parsed row from the daily CSV."""
    __slots__ = ("ts_dt", "values")

    def __init__(self, ts_dt: datetime, values: dict[str, str]):
        self.ts_dt  = ts_dt
        self.values = values


def _load_energy_csv(csv_path: Path) -> list[_EnergyRow]:
    """
    Load a daily CSV file and return a list of _EnergyRow sorted by timestamp.
    Returns [] on any error (file missing, wrong format, network issue).
    Timestamp column: 'Timestamp', format: '2026-03-24 09:55:15.152'
    """
    rows: list[_EnergyRow] = []
    try:
        raw = csv_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return rows

    if not raw.strip():
        return rows

    # Auto-detect delimiter
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=[",", ";", "\t"])
        delim = dialect.delimiter
    except Exception:
        delim = ","

    reader = csv.DictReader(raw.splitlines(), delimiter=delim)
    if reader.fieldnames is None:
        return rows

    # Strip whitespace from field names
    fieldnames_stripped = [f.strip() for f in reader.fieldnames]

    for r in reader:
        # Re-key with stripped names
        row_clean = {k.strip(): v for k, v in r.items() if k is not None}

        ts_str = row_clean.get("Timestamp", "").strip()
        if not ts_str:
            continue
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            try:
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        rows.append(_EnergyRow(dt, row_clean))

    rows.sort(key=lambda r: r.ts_dt)
    return rows


def _energy_api_for_day(
    dt: datetime,
    cols: list[str],
    csv_root: "str | None" = None,
    log=None,
) -> "tuple[list[_EnergyRow], dict, bool]":
    """
    Query CPVA archiver for the given day and return (_EnergyRow list, per_col dict,
    had_error). Falls back column-by-column to CSV when API returns nothing.
    dt should be a naive Prague-local datetime (used only for the date).

    Returns:
      - merged: list[_EnergyRow] sorted by timestamp (merged across all channels)
      - per_col: dict[str, list[_EnergyRow]] mapping each column to sorted rows that
                 have a value for that column (used for per-column closest-timestamp
                 lookup). Also carries precomputed "_ns:<col>" → list[int] arrays so
                 the per-file lookup is a pure bisect instead of O(rows) rebuilds.
      - had_error: True if any column's API fetch FAILED (as opposed to genuinely
                   having no data) and CSV had nothing either — the caller must NOT
                   cache such a day, so the next lookup retries.
    """
    date_key = dt.strftime("%Y-%m-%d")

    def _log(msg):
        if log is not None:
            log(msg)

    # Collect per-column rows: {t_ns: {col: value, ...}}
    by_ts: dict[int, dict] = {}
    # Per-column row lists: col -> list of (t_ns, dt_local, val)
    per_col_raw: dict[str, list[tuple[int, datetime, str]]] = {}
    err_flags: dict[str, bool] = {}
    src_flags: dict[str, str] = {}   # col → "api" | "csv"

    def _fetch_one_col(col: str) -> "tuple[str, list[tuple[int, datetime, str]], bool, str]":
        # `col` is a registry NAME. get(col, col) is what makes an arbitrary archiver
        # channel work at all: it used to be get(col) alone, so anything outside the
        # eight presets resolved to None and fell straight through to a CSV that has
        # never heard of it.
        channel = _pv_channel_for(col)
        col_rows: list[tuple[int, datetime, str]] = []
        had_error = False
        src = "api"

        if channel is not None:
            res = cpva.get_day(channel, date_key, timeout=CPVA_HTTP_TIMEOUT)
            if res.status == "error":
                had_error = True
                _log(f"  API {col} ({channel}) FETCH FAILED — will retry on next lookup")
            elif res.status == "stale":
                _log(f"  API {col} ({channel}): fetch failed, showing "
                     f"{len(res.samples)} samples from {res.age_s:.0f}s ago")
            for t_ns, v in res.samples:
                if PRAGUE is not None:
                    dt_local = datetime.fromtimestamp(
                        t_ns / 1e9, tz=timezone.utc).astimezone(PRAGUE).replace(tzinfo=None)
                else:
                    dt_local = datetime.utcfromtimestamp(t_ns / 1e9)
                col_rows.append((t_ns, dt_local, str(v)))
            if res.samples:
                _log(f"  API {col} ({channel}): {len(res.samples)} samples")
        else:
            _log(f"  {col}: no CPVA channel mapping, trying CSV only")

        # CSV fallback if API returned nothing — only for a PV that HAS a column in
        # that file. An arbitrary archiver channel has none, and looking for it used to
        # mean opening the day's CSV once per such PV to find nothing.
        csv_col = _pv_csv_col(col)
        if not col_rows and csv_col is not None:
            root = csv_root if csv_root is not None else ENERGY_CSV_ROOT
            fname = dt.strftime(ENERGY_CSV_NAME_FMT) + ".csv"
            csv_path = Path(root) / fname
            csv_rows = _load_energy_csv(csv_path)
            for r in csv_rows:
                if csv_col in r.values:
                    if PRAGUE is not None:
                        t_ns = int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1_000_000_000)
                    else:
                        t_ns = int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000)
                    # Stored under the REGISTRY NAME, not under the CSV column name:
                    # everything downstream looks a value up by the name that was picked.
                    col_rows.append((t_ns, r.ts_dt, r.values[csv_col]))
            if col_rows:
                had_error = False   # CSV covered the outage
                src = "csv"
                _log(f"  CSV fallback {col}: {len(col_rows)} rows")

        return col, col_rows, had_error, src

    with ThreadPoolExecutor(max_workers=max(1, len(cols))) as _aex:
        _col_futs = {_aex.submit(_fetch_one_col, c): c for c in cols}
        for _fut in as_completed(_col_futs):
            try:
                _col, _col_rows, _col_err, _col_src = _fut.result()
                err_flags[_col] = _col_err
                src_flags[_col] = _col_src
                if _col_rows:
                    per_col_raw[_col] = _col_rows
                for t_ns, dt_local, val in _col_rows:
                    if t_ns not in by_ts:
                        by_ts[t_ns] = {}
                    by_ts[t_ns].setdefault("_dt", dt_local)
                    by_ts[t_ns][_col] = val
            except Exception as exc:
                err_flags[_col_futs[_fut]] = True
                _log(f"  col fetch ERROR: {type(exc).__name__}: {exc}")

    # Convert to _EnergyRow objects sorted by timestamp
    result: list[_EnergyRow] = []
    for t_ns in sorted(by_ts):
        entry = by_ts[t_ns]
        dt_local = entry.get("_dt", datetime.utcfromtimestamp(t_ns / 1e9))
        values = {k: v for k, v in entry.items() if k != "_dt"}
        result.append(_EnergyRow(dt_local, values))

    # Build per_col: col -> sorted list of _EnergyRow that have only that col's
    # value, plus parallel "_ns:<col>" sorted int arrays for direct bisect and
    # "_src:<col>" data-source markers (API rows use a tighter match tolerance).
    per_col: dict = {}
    for col, rows_raw in per_col_raw.items():
        rows_raw_sorted = sorted(rows_raw, key=lambda x: x[0])
        per_col[col] = [
            _EnergyRow(dt_local, {col: val})
            for t_ns, dt_local, val in rows_raw_sorted
        ]
        per_col[f"_ns:{col}"] = [t_ns for t_ns, _dt, _v in rows_raw_sorted]
        per_col[f"_src:{col}"] = src_flags.get(col, "csv")

    return result, per_col, any(err_flags.values())


def _find_energy_match(
    rows: list[_EnergyRow],
    img_ts_ns: int,
    tol_s: float = ENERGY_MATCH_TOL_S,
) -> tuple[_EnergyRow | None, _EnergyRow | None, _EnergyRow | None]:
    if not rows:
        return None, None, None

    # Convert image UTC nanosecond timestamp to Prague local datetime
    img_dt = datetime.fromtimestamp(img_ts_ns / 1_000_000_000, tz=timezone.utc)
    if PRAGUE:
        img_dt = img_dt.astimezone(PRAGUE).replace(tzinfo=None)
    else:
        img_dt = img_dt.replace(tzinfo=None)

    # Binary search by datetime
    ts_list = [r.ts_dt for r in rows]
    idx = bisect.bisect_left(ts_list, img_dt)

    candidates = []
    if idx > 0: candidates.append(rows[idx - 1])
    if idx < len(rows): candidates.append(rows[idx])

    best = min(candidates, key=lambda r: abs((r.ts_dt - img_dt).total_seconds()), default=None)
    if best and abs((best.ts_dt - img_dt).total_seconds()) <= tol_s:
        return best, None, None

    before = rows[idx - 1] if idx > 0 else None
    after  = rows[idx]     if idx < len(rows) else None
    return None, before, after

def _find_closest_per_col_value(
    per_col: "dict[str, list[_EnergyRow]]",
    col: str,
    target_ns: int,
    tol_s: float = 2.0,
) -> str:
    """
    Find the closest-timestamp value for `col` in per_col within tol_s seconds of target_ns.
    per_col maps column name -> sorted list of _EnergyRow objects that have a value for that col.
    Returns the formatted-raw value string, or "—" if no row is within tolerance.
    """
    rows = per_col.get(col)
    if not rows:
        return "—"
    # Use the precomputed ns array when present ("_ns:<col>" sidecar built by
    # _energy_api_for_day / _build_per_col_from_rows) — rebuilding it here made
    # the lookup O(files × cols × rows).
    ns_list = per_col.get(f"_ns:{col}")
    if ns_list is None or len(ns_list) != len(rows):
        # Fallback for dicts built elsewhere — compute locally, do NOT write
        # back into per_col (it is shared across worker threads via the cache).
        ns_list = []
        for r in rows:
            if PRAGUE is not None:
                r_ns = int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1_000_000_000)
            else:
                r_ns = int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000)
            ns_list.append(r_ns)
    idx = bisect.bisect_left(ns_list, target_ns)
    best_val = None
    best_diff = float("inf")
    for i in [idx - 1, idx]:
        if 0 <= i < len(rows):
            diff = abs(ns_list[i] - target_ns)
            if diff < best_diff:
                best_diff = diff
                best_val = rows[i].values.get(col, "—")
    tol_ns = int(tol_s * 1_000_000_000)
    if best_val is not None and best_diff <= tol_ns:
        return best_val
    return "—"


def _build_per_col_from_rows(
    rows: "list[_EnergyRow]",
) -> "dict[str, list[_EnergyRow]]":
    """
    Build the per-column closest-timestamp lookup table from plain CSV rows.

    Returns {col: [rows with a non-empty value for col]} — each list keeps the
    chronological order of `rows` (which _load_energy_csv already sorts by ts),
    so _find_closest_per_col_value can binary-search it directly.
    """
    per_col: dict = {}
    for r in rows:
        for col, val in r.values.items():
            if col == "Timestamp":
                continue
            if val is not None and str(val).strip() not in ("", "—"):
                per_col.setdefault(col, []).append(r)
    # Same "_ns:<col>"/"_src:<col>" sidecars as _energy_api_for_day builds, so
    # _find_closest_per_col_value never needs its O(rows) rebuild fallback.
    for col in [c for c in per_col if not c.startswith("_")]:
        ns_list = []
        for r in per_col[col]:
            if PRAGUE is not None:
                ns_list.append(int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1_000_000_000))
            else:
                ns_list.append(int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000))
        per_col[f"_ns:{col}"] = ns_list
        per_col[f"_src:{col}"] = "csv"
    return per_col


def _format_energy_diff_s(diff_s: float) -> str:
    """Format a time difference in seconds to a readable string."""
    diff_s = abs(diff_s)
    if diff_s < 1.0:
        return f"{diff_s*1000:.0f} ms"
    return f"{diff_s:.1f} s"

# Energies small enough to be read in mJ. House convention, kept: these two are
# fractions of a joule and "0.0031 J" is harder to compare at a glance than "3.10 mJ".
_MJ_NAMES = {"Back_Ref", "PAP1"}
# Columns that only ever lived in Salvation's CSV and hold a 0/1 flag.
_YESNO_NAMES = {"CampOn", "E2_Open", "E3_Open", "E4_Open", "E5_Open"}


def _format_energy_value(name: str, raw_val: str) -> str:
    """One PV's value as text, keyed by its REGISTRY NAME.

    SBW4 used to be multiplied by 0.749 here, so this tab printed the compressed
    energy under the name of the channel that reads the uncompressed one — a number
    that matched no other tab and no archiver query. A PV now reports what the
    archiver holds; the only factor left is the registry's own for an entry that
    exists to BE a conversion ("Compressed SBW4")."""
    v = raw_val.strip() if raw_val else "—"
    if v == "—" or v == "":
        return "—"
    if name in _YESNO_NAMES:
        try:
            return "YES" if int(float(v)) == 1 else "NO"
        except Exception:
            return v
    # Waveplate — plain number, no unit, snapped onto its 1000-count grid. The
    # waveplate is only ever commanded to whole multiples of 1000, so anything else
    # is the motor readback caught mid-travel. int() also TRUNCATED, so a settled
    # 349 999.6 printed as 349 999 — one count below a position that does exist.
    if name == "Waveplate" or name == "waveplate":
        try:
            ch = _pv_channel_for(name) or "waveplate"
            return f"{cpva.quantize(ch, float(v))[0]:.0f}"
        except Exception:
            return v
    try:
        v_f = float(v) * _pv_scale_for(name)
    except Exception:
        return v
    if name in _MJ_NAMES:
        return f"{v_f * 1000:.2f} mJ"
    unit = _pv_unit_for(name)
    if unit == "J":
        return f"{v_f:.3f} J"
    return f"{v_f:.4g} {unit}".strip() if unit else f"{v_f:.4g}"

def _annotate_image_with_energy(
    src: Path,
    dst: Path,
    match_row: _EnergyRow | None,
    no_match_before: _EnergyRow | None,
    no_match_after: _EnergyRow | None,
    img_ts_ns: int,
    selected_cols: list[str],
) -> None:
    """
    Add a white annotation bar below the image and save to dst.
    If match_row is given: write selected column values.
    Otherwise: write a "No match" message with nearest timestamps.
    """
    from PIL import Image as _Img, ImageDraw, ImageFont as _IF

    img = _Img.open(src)

    # Build annotation text
    if match_row is not None:
        parts = []
        for col in selected_cols:
            val   = _format_energy_value(col, match_row.values.get(col, "—"))
            parts.append(f"{_pv_label_for(col)}: {val}")
        text = "   |   ".join(parts) if parts else "(no columns selected)"
    else:
        # PV values only — no timestamps or extra info in the bar
        parts = [f"{_pv_label_for(col)}: n/a" for col in selected_cols]
        text = "   |   ".join(parts) if parts else "n/a"

    # Create bar — dynamický počet řádků, font a výška se přizpůsobí obsahu
    w, h = img.size
    _tmp_draw = ImageDraw.Draw(_Img.new("RGB", (1, 1)))

    # Načti font
    font = None
    for _fname in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            font = _IF.truetype(_fname, ENERGY_BAR_FONT_SIZE_PT)
            break
        except Exception:
            continue
    if font is None:
        font = _IF.load_default()

    # Rozděl text na části podle separátoru
    parts_list = text.split("   |   ")

    # Najdi font size a počet řádků tak aby se vše vešlo
    chosen_font = font
    display_lines = [text]  # fallback
    for fsize in range(ENERGY_BAR_FONT_SIZE_PT, 7, -1):
        # Načti font v této velikosti
        _f = None
        for _fname in (
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "DejaVuSans.ttf",
        ):
            try:
                _f = _IF.truetype(_fname, fsize)
                break
            except Exception:
                continue
        if _f is None:
            _f = _IF.load_default()

        # Zkus nejprve jeden řádek
        try:
            bbox = _tmp_draw.textbbox((0, 0), text, font=_f)
            if (bbox[2] - bbox[0]) <= w - 20:
                chosen_font = _f
                display_lines = [text]
                break
        except Exception:
            pass

        # Zkus rozdělit na více řádků (2, 3, 4...)
        fitted = False
        for n_lines in range(2, len(parts_list) + 1):
            # Rozdělení parts_list rovnoměrně do n_lines řádků
            chunk = max(1, len(parts_list) // n_lines)
            lines = []
            for i in range(0, len(parts_list), chunk):
                lines.append("   |   ".join(parts_list[i:i + chunk]))
            # Zkontroluj šířku nejdelšího řádku
            max_w = 0
            try:
                for line in lines:
                    bb = _tmp_draw.textbbox((0, 0), line, font=_f)
                    max_w = max(max_w, bb[2] - bb[0])
            except Exception:
                max_w = w
            if max_w <= w - 20:
                chosen_font = _f
                display_lines = lines
                fitted = True
                break
        if fitted:
            break

    # Změř výšku jednoho řádku
    try:
        bb = _tmp_draw.textbbox((0, 0), "Ag", font=chosen_font)
        line_h = bb[3] - bb[1]
    except Exception:
        line_h = ENERGY_BAR_FONT_SIZE_PT + 4
    padding = 10
    bar_h = max(ENERGY_BAR_HEIGHT_PX, line_h * len(display_lines) + padding * (len(display_lines) + 1))

    bar = _Img.new("RGB", (w, bar_h), ENERGY_BAR_BG_COLOR)
    draw = ImageDraw.Draw(bar)

    # Kresli každý řádek vycentrovaný
    total_text_h = line_h * len(display_lines) + padding * (len(display_lines) - 1)
    y = (bar_h - total_text_h) // 2
    for line in display_lines:
        try:
            bb = draw.textbbox((0, 0), line, font=chosen_font)
            text_w = bb[2] - bb[0]
        except Exception:
            text_w = 0
        x = max(8, (w - text_w) // 2)
        draw.text((x, y), line, fill=ENERGY_BAR_TEXT_COLOR, font=chosen_font)
        y += line_h + padding

    combined = _Img.new("RGB", (w, h + bar_h), ENERGY_BAR_BG_COLOR)
    combined.paste(img.convert("RGB"), (0, 0))
    combined.paste(bar, (0, h))
    combined.save(dst)


def _write_annotated_with_text(src: Path, dst: Path, text: str) -> None:
    """
    Add a white annotation bar below image with arbitrary text, save to dst.
    Used when saving a frame with its camera name, timestamp and PV values burnt in.
    """
    from PIL import Image as _Img, ImageDraw as _ID, ImageFont as _IF

    img = _Img.open(src)
    w, h = img.size

    fsize = ENERGY_BAR_FONT_SIZE_PT
    font = None
    for _fname in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            font = _IF.truetype(_fname, fsize)
            break
        except Exception:
            continue
    if font is None:
        font = _IF.load_default()

    parts_list = text.split("   |   ")
    # Fit parts onto 1 or 2 lines
    tmp_draw = _ID.Draw(_Img.new("RGB", (1, 1)))
    line_w = max((tmp_draw.textlength(p, font=font) for p in parts_list), default=0)
    padding = 6
    line_h = fsize + padding

    if line_w <= w - padding * 2:
        display_lines = ["   |   ".join(parts_list)]
    else:
        # Split roughly in half
        mid = len(parts_list) // 2 or 1
        display_lines = [
            "   |   ".join(parts_list[:mid]),
            "   |   ".join(parts_list[mid:]),
        ]

    bar_h = line_h * len(display_lines) + padding
    bar = _Img.new("RGB", (w, bar_h), (255, 255, 255))
    draw = _ID.Draw(bar)
    y = padding // 2
    for line in display_lines:
        draw.text((padding, y), line, font=font, fill=(0, 0, 0))
        y += line_h

    combined = _Img.new("RGB", (w, h + bar_h), (255, 255, 255))
    combined.paste(img.convert("RGB"), (0, 0))
    combined.paste(bar, (0, h))
    combined.save(dst)


def _write_annotated_from_pil(img: "PilImage.Image", dst: Path, text: str) -> None:
    """
    Add a white annotation bar below an already-rendered PIL image and save to dst.
    Font size is auto-scaled so all PV parts fill the bar as large as possible,
    splitting into multiple lines as needed (same logic as Shot Finder).
    """
    from PIL import Image as _Img, ImageDraw as _ID, ImageFont as _IF

    img = img.convert("RGB")
    w, h = img.size

    _FONT_CANDIDATES = (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "DejaVuSans.ttf",
    )
    parts_list = [p for p in text.split("   |   ") if p]
    tmp_draw = _ID.Draw(_Img.new("RGB", (1, 1)))
    padding = 8

    chosen_font = None
    display_lines = [text]

    for fsize in range(28, 7, -1):
        _f = None
        for _fname in _FONT_CANDIDATES:
            try:
                _f = _IF.truetype(_fname, fsize)
                break
            except Exception:
                continue
        if _f is None:
            _f = _IF.load_default()

        # Try fitting everything on one line first
        try:
            bb = tmp_draw.textbbox((0, 0), text, font=_f)
            if (bb[2] - bb[0]) <= w - padding * 2:
                chosen_font = _f
                display_lines = [text]
                break
        except Exception:
            pass

        # Try splitting into increasing number of lines
        fitted = False
        for n_lines in range(2, len(parts_list) + 1):
            chunk = max(1, len(parts_list) // n_lines)
            lines = []
            for i in range(0, len(parts_list), chunk):
                lines.append("   |   ".join(parts_list[i:i + chunk]))
            max_w = 0
            try:
                for line in lines:
                    bb2 = tmp_draw.textbbox((0, 0), line, font=_f)
                    max_w = max(max_w, bb2[2] - bb2[0])
            except Exception:
                max_w = w
            if max_w <= w - padding * 2:
                chosen_font = _f
                display_lines = lines
                fitted = True
                break
        if fitted:
            break

    if chosen_font is None:
        for _fname in _FONT_CANDIDATES:
            try:
                chosen_font = _IF.truetype(_fname, 8)
                break
            except Exception:
                continue
        if chosen_font is None:
            chosen_font = _IF.load_default()
        display_lines = [text]

    try:
        bb_ref = tmp_draw.textbbox((0, 0), "Ag", font=chosen_font)
        line_h = bb_ref[3] - bb_ref[1]
    except Exception:
        line_h = 14

    bar_h = max(30, line_h * len(display_lines) + padding * (len(display_lines) + 1))
    bar = _Img.new("RGB", (w, bar_h), (255, 255, 255))
    draw = _ID.Draw(bar)
    total_text_h = line_h * len(display_lines) + padding * (len(display_lines) - 1)
    y = (bar_h - total_text_h) // 2
    for line in display_lines:
        try:
            bb = draw.textbbox((0, 0), line, font=chosen_font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = 0
        x = max(padding, (w - tw) // 2)
        draw.text((x, y), line, font=chosen_font, fill=(0, 0, 0))
        y += line_h + padding

    combined = _Img.new("RGB", (w, h + bar_h), (255, 255, 255))
    combined.paste(img, (0, 0))
    combined.paste(bar, (0, h))
    combined.save(str(dst))


# ── ENERGY CSV LOADER (async) ─────────────────────────────────────────────────

class _EnergyLoadSignals(QObject):
    finished = Signal(list)   # list[_EnergyRow]

class _EnergyLoadTask(QRunnable):
    """Load CSV in a background thread."""
    def __init__(self, csv_path: Path, signals: "_EnergyLoadSignals"):
        super().__init__()
        self._path   = csv_path
        self._signals = signals

    def run(self):
        rows = _load_energy_csv(self._path)
        self._signals.finished.emit(rows)


# ── COLUMN PICKER DIALOG ──────────────────────────────────────────────────────

# EnergyColumnDialog used to live here: a fixed list of the twelve Salvation CSV
# columns as tick boxes. It is gone — the PV picker is the Image Slider's
# PvConfigDialog now (see _pick_energy_columns), which reads the shared registry, can
# search the whole archiver, takes a channel typed in full, and carries the names,
# units and formulas. Two pickers over one registry is what let the two tabs disagree
# about what "SBW4" means.


class _LoadSignals(QObject):
    done      = Signal(list, dict, int)
    not_found = Signal(object)
    error     = Signal(str)
    log_msg   = Signal(str)

class _CollectSignals(QObject):
    done = Signal(list)

class _CompareSignals(QObject):
    done  = Signal(object, object, object, object)
    error = Signal(str)

class _AutoHourSignals(QObject):
    """Signals for _apply_auto_hour_for_selected_day worker."""
    apply   = Signal(str, int, int, bool)  # msg, ui_hour, day_shift, use_lab
    log_msg = Signal(str)

class _LogSignals(QObject):
    msg = Signal(str)

class _PreviewSignals(QObject):
    ready = Signal(object, int)   # (QPixmap, gen)


class _TryAgainSignals(QObject):
    progress  = Signal(str, str)                        # main label, hour label
    cell_done = Signal(str, object, int, object, str)   # cam, date, hour, path|None, log line
    finished  = Signal(list)                            # summary lines


# ── CAMERA PICKER ─────────────────────────────────────────────────────────────
class _CameraPickDialog(QDialog):
    """Pick the cameras to search — the Image Slider's camera picker, over the
    cameras the day scan actually found.

    This replaces the always-on camera table that used to fill half the tab. The
    list is only interesting while you are choosing, so it lives in a dialog and
    the panel keeps just the short list of what is picked.

    The presets are the SAME file the Image Slider writes (cam_presets.json), so a
    set of cameras saved in one tab is offered in the other. A preset can name a
    camera that this day has no folder for; loading it picks what is there and says
    how many were missing.
    """

    _PRESETS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" / "cam_presets.json"

    def __init__(self, cams: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select cameras")
        self.resize(760, 640)

        # (name, num, label) per camera, in the order the scan found them
        self._cams = [(c["name"], c.get("num", ""), c.get("label", c["name"]))
                      for c in cams]
        self._selected: list[str] = [c["name"] for c in cams if c.get("checked")]
        self._shown: list[tuple] = list(self._cams)
        self._presets: dict = self._load_presets()

        lay = QVBoxLayout(self)
        lay.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        # ── Left: search + the cameras this day has ──────────────────────────
        left = QVBoxLayout(); left.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search cameras…")
        self._search.setToolTip(
            "Type any part of the name or the camera number. Several words are\n"
            "all required, in any position — \"pt near\" finds \"PT_NearField\".")
        self._search.textChanged.connect(self._filter)
        left.addWidget(self._search)

        self._list = QTableWidget(0, 2)
        self._list.setHorizontalHeaderLabels(["#", "Camera"])
        self._list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.verticalHeader().setVisible(False)
        self._list.cellClicked.connect(self._on_row_clicked)
        left.addWidget(self._list, 1)

        all_row = QHBoxLayout()
        btn_all = QPushButton("Select all")
        btn_all.setToolTip("Every camera the search box currently shows")
        btn_all.clicked.connect(self._select_all_shown)
        btn_none = QPushButton("Clear")
        btn_none.setToolTip("Unpick every camera")
        btn_none.clicked.connect(self._clear_all)
        all_row.addWidget(btn_all); all_row.addWidget(btn_none)
        all_row.addStretch(1)
        left.addLayout(all_row)

        self._status = QLabel("")
        self._status.setStyleSheet("font-size: 10px; color: #555;")
        self._status.setWordWrap(True)
        left.addWidget(self._status)

        top_row.addLayout(left, 3)

        # ── Right: presets, shared with the Image Slider ─────────────────────
        right = QVBoxLayout(); right.setSpacing(4)
        preset_lbl = QLabel("Presets")
        preset_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #333;")
        right.addWidget(preset_lbl)

        self._preset_list = QTableWidget(0, 1)
        self._preset_list.setHorizontalHeaderLabels(["Name"])
        self._preset_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._preset_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._preset_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._preset_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preset_list.verticalHeader().setVisible(False)
        self._preset_list.setToolTip(
            "Click a preset to pick its cameras. The same presets the Image Slider "
            "has — saving one here offers it there too.")
        self._preset_list.itemSelectionChanged.connect(self._on_preset_load)
        right.addWidget(self._preset_list, 1)

        btn_save = QPushButton("Save")
        btn_save.setToolTip("Save the cameras picked right now under a name")
        btn_rename = QPushButton("Rename")
        btn_delete = QPushButton("Delete")
        for b in (btn_save, btn_rename, btn_delete):
            b.setFixedHeight(24)
            right.addWidget(b)
        btn_save.clicked.connect(self._on_preset_save)
        btn_rename.clicked.connect(self._on_preset_rename)
        btn_delete.clicked.connect(self._on_preset_delete)
        right.addStretch()

        top_row.addLayout(right, 2)
        lay.addLayout(top_row, 1)

        sel_lbl = QLabel("Selected cameras:")
        sel_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #333;")
        lay.addWidget(sel_lbl)

        self._sel_table = QTableWidget(0, 2)
        self._sel_table.setHorizontalHeaderLabels(["Camera", ""])
        self._sel_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._sel_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed)
        self._sel_table.setColumnWidth(1, 28)
        self._sel_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._sel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sel_table.verticalHeader().setVisible(False)
        self._sel_table.setMaximumHeight(180)
        lay.addWidget(self._sel_table)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self._note = ""
        self._refresh_preset_list()
        self._fill_list()
        self._refresh_sel_table()

    # ── presets (shared file with the Image Slider) ────────────────────────────
    def _load_presets(self) -> dict:
        """{name: {"cameras": [...], …}}. An old preset is a bare list of names."""
        raw = {}
        try:
            if self._PRESETS_PATH.exists():
                raw = json.loads(self._PRESETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        out = {}
        for name, val in (raw or {}).items():
            if isinstance(val, list):
                out[name] = {"cameras": [str(c) for c in val], "auto": True}
            elif isinstance(val, dict):
                entry = dict(val)
                entry["cameras"] = [str(c) for c in entry.get("cameras", [])]
                out[name] = entry
        return out

    def _save_presets(self):
        try:
            self._PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._PRESETS_PATH.write_text(
                json.dumps(self._presets, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Presets", f"Could not save the presets:\n{e}")

    def _refresh_preset_list(self):
        self._preset_list.blockSignals(True)
        self._preset_list.setRowCount(0)
        for name in sorted(self._presets.keys(), key=str.lower):
            r = self._preset_list.rowCount()
            self._preset_list.insertRow(r)
            self._preset_list.setItem(r, 0, QTableWidgetItem(name))
        self._preset_list.blockSignals(False)

    def _selected_preset_name(self) -> "str | None":
        rows = self._preset_list.selectedItems()
        return rows[0].text() if rows else None

    def _on_preset_load(self, *_):
        name = self._selected_preset_name()
        if not name or name not in self._presets:
            return
        wanted = [str(c) for c in (self._presets[name].get("cameras") or [])]
        have   = {n for n, _num, _lbl in self._cams}
        self._selected = [c for c in wanted if c in have]
        missing = len(wanted) - len(self._selected)
        self._note = (f"Preset “{name}”: {len(self._selected)} picked"
                      + (f", {missing} not recorded in the selected day(s)" if missing else ""))
        self._highlight()
        self._refresh_sel_table()
        self._fill_list()

    def _on_preset_save(self):
        from PySide6.QtWidgets import QInputDialog
        current = self._selected_preset_name() or ""
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", text=current)
        if not ok or not name.strip():
            return
        name = name.strip()
        old = self._presets.get(name) or {}
        entry = {"cameras": list(self._selected)}
        # Overwriting a preset the Image Slider saved with an arrangement: the
        # arrangement is kept only while the camera set is the same one it was made
        # for, otherwise those tiles belong to nothing and the preset goes back to
        # arranging itself.
        if sorted(old.get("cameras", [])) == sorted(self._selected) and not old.get("auto", True):
            entry.update({k: old[k] for k in ("auto", "cam_order", "tiles") if k in old})
        else:
            entry["auto"] = True
        self._presets[name] = entry
        self._save_presets()
        self._refresh_preset_list()
        for r in range(self._preset_list.rowCount()):
            if self._preset_list.item(r, 0).text() == name:
                self._preset_list.blockSignals(True)
                self._preset_list.selectRow(r)
                self._preset_list.blockSignals(False)
                break

    def _on_preset_rename(self):
        from PySide6.QtWidgets import QInputDialog
        name = self._selected_preset_name()
        if not name:
            return
        new_name, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=name)
        if not ok or not new_name.strip() or new_name.strip() == name:
            return
        self._presets[new_name.strip()] = self._presets.pop(name)
        self._save_presets()
        self._refresh_preset_list()

    def _on_preset_delete(self):
        name = self._selected_preset_name()
        if not name:
            return
        if QMessageBox.question(self, "Delete preset", f"Delete preset '{name}'?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        self._presets.pop(name, None)
        self._save_presets()
        self._refresh_preset_list()

    # ── list ──────────────────────────────────────────────────────────────────
    def _label_for(self, name: str) -> str:
        for n, _num, lbl in self._cams:
            if n == name:
                return lbl or n
        return name

    def _filter(self, text: str):
        toks = [t for t in text.strip().lower().split() if t]
        if not toks:
            self._shown = list(self._cams)
        else:
            self._shown = [c for c in self._cams
                           if all(t in f"{c[1]} {c[2]} {c[0]}".lower() for t in toks)]
        self._fill_list()

    def _fill_list(self):
        self._list.setRowCount(0)
        for name, num, label in self._shown:
            r = self._list.rowCount()
            self._list.insertRow(r)
            self._list.setItem(r, 0, QTableWidgetItem(num))
            it = QTableWidgetItem(label or name)
            it.setData(Qt.ItemDataRole.UserRole, name)
            self._list.setItem(r, 1, it)
        self._highlight()
        txt = f"{len(self._shown)} of {len(self._cams)} cameras"
        if getattr(self, "_note", ""):
            txt += "  ·  " + self._note
        self._status.setText(txt)

    def _highlight(self):
        for r in range(self._list.rowCount()):
            it = self._list.item(r, 1)
            if it is None:
                continue
            on = it.data(Qt.ItemDataRole.UserRole) in self._selected
            bg = QColor("#d0e8ff") if on else QColor("#ffffff")
            for c in range(self._list.columnCount()):
                cell = self._list.item(r, c)
                if cell:
                    cell.setBackground(bg)
                    f = cell.font(); f.setBold(on); cell.setFont(f)

    def _on_row_clicked(self, row: int, _col: int):
        it = self._list.item(row, 1)
        if it is None:
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        if name in self._selected:
            self._selected.remove(name)
        else:
            self._selected.append(name)
        self._highlight()
        self._refresh_sel_table()
        self._list.clearSelection()

    def _select_all_shown(self):
        for name, _num, _lbl in self._shown:
            if name not in self._selected:
                self._selected.append(name)
        self._highlight()
        self._refresh_sel_table()

    def _clear_all(self):
        self._selected.clear()
        self._highlight()
        self._refresh_sel_table()

    # ── selected list ─────────────────────────────────────────────────────────
    def _refresh_sel_table(self):
        self._sel_table.setRowCount(0)
        for name in self._selected:
            r = self._sel_table.rowCount()
            self._sel_table.insertRow(r)
            self._sel_table.setItem(r, 0, QTableWidgetItem(self._label_for(name)))
            btn = QPushButton("✕")
            btn.setFixedSize(24, 24)
            btn.setStyleSheet("font-size: 10px; padding: 0;")
            btn.clicked.connect(lambda _checked=False, n=name: self._remove(n))
            self._sel_table.setCellWidget(r, 1, btn)

    def _remove(self, name: str):
        if name in self._selected:
            self._selected.remove(name)
        self._highlight()
        self._refresh_sel_table()

    def selected_names(self) -> list:
        return list(self._selected)


# ── MAIN WIDGET ───────────────────────────────────────────────────────────────
class ImageFinderWidget(QWidget):
    """
    PySide6 port of the original tkinter FolderPickerApp.
    All original logic preserved. Only UI layer changed.

    Embed via main.py (set ._slider_ref and ._tab_widget after construction),
    or run standalone via main() at the bottom of this file.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Integration — set by main.py after construction
        self._slider_ref = None   # Viewer instance (is.py)
        self._tab_widget = None   # QTabWidget for tab switching

        # ── same state as original FolderPickerApp ────────────────────────────
        # The cameras the day scan found, and which of them are picked. This list is
        # the single source of truth — it used to be the rows of a big on-screen
        # table, which is now a picker dialog plus the short "selected" list.
        # Each entry: {path, name, num, label, hz33, qty, checked}.
        #   hz33 — "YES" for the 3.3+ Hz cameras (CAM_33HZ). Not shown any more;
        #          kept because the scan knows it and a future read may want it.
        #   qty  — how many frames to take from the camera. Always 1 today; the
        #          per-camera count column is gone, the plumbing that honours it
        #          is not.
        self._cams: list[dict] = []
        self._load_gen = 0
        self.primary_files: list[Path] = []
        self._namecache: dict = {}
        self._collect_busy = False

        self._mem_a: Path | None = None
        self._mem_b: Path | None = None

        self._view_temp_dir: str | None = None
        self._view_temp_paths: list[Path] = []
        atexit.register(self._cleanup_view_temp)

        self._user_has_selected_day = False
        self._autoload_timer: QTimer | None = None
        self._auto_hour_last_day = None

        self.RAMPING_ROOT: Path | None = None
        self._ramping_cache: dict = {}
        self._ramping_source: str = RAMPING_CANDIDATES[DEFAULT_RAMPING_SOURCE][0]

        # ── energy CSV state ─────────────────────────────────────────────────
        # Selected columns — loaded from ENERGY_COLUMNS_DEFAULT, user can change
        # Registry NAMES (see _pv_channel_for), not CSV column names. Restored from
        # this tab's own state at the end of the build, once the widgets it paints into
        # exist.
        self._energy_selected_cols: list[str] = list(ENERGY_COLUMNS_DEFAULT)
        self._energy_hidden_pvs: set = set()
        # Cached CSV rows for the last loaded day: {date_str: list[_EnergyRow]}
        self._energy_cache: dict[str, list[_EnergyRow]] = {}
        # Per-column cache: {date_str: dict[str, list[_EnergyRow]]}
        self._energy_per_col_cache: dict[str, dict] = {}
        # Last energy lookup results: list of (Path, match|None, before|None, after|None)
        self._energy_results: list[tuple] = []
        self._energy_csv_offset: int = 0   # offset from matched row when navigating outside image set¨
        self._energy_csv_anchor_idx: int | None = None  # csv_rows index anchor for CSV navigation mode
        self._energy_csv_anchor_rows: list = []          # csv_rows for current anchor image
        # Background thread pool for CSV loading
        self._energy_pool = QThreadPool()
        self._energy_pool.setMaxThreadCount(1)

        self._log_sig = _LogSignals()
        self._log_sig.msg.connect(self._log)

        self._preview_sig = _PreviewSignals()
        self._preview_sig.ready.connect(self._on_preview_ready)

        self._preview_gen: int = 0
        self._preview_paths: list = []
        self._preview_cam_names: list[str] = []  # per-file cam name (parallel to _preview_paths)
        self._preview_idx: int = 0
        self._preview_cam: str = ""
        self._preview_from_view: bool = False  # True when preview was loaded by View button
        self._tp_dead_channels: set[str] = set()  # channels that timed out → skip next time
        self._last_save_dir: "Path | None" = None

        self._build_ui()
        # Trigger today's load after the event loop starts
        QTimer.singleShot(0, self._auto_select_today)

    # ── LOGGING ───────────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool):
        for btn in [self._btn_view, self._btn_save,
                    self._btn_open_folder, self._btn_cameras,
                    self._btn_time_window,
                    self._btn_compare, self._btn_energy_cols,
                    self._gradient_cb, self._hour_cb,
                    self._lab_time_cb, self._cal]:
            btn.setEnabled(not busy)
    
    def _log(self, msg: str):
        """Main-thread log. Safe to call from any thread via _log_safe."""
        print(msg)
        if hasattr(self, "_log_box"):
            self._log_box.appendPlainText(str(msg))
            sb = self._log_box.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _log_safe(self, msg: str):
        """Thread-safe: routes through Qt signal so Qt widget is only touched on main thread."""
        try:
            self._log_sig.msg.emit(str(msg))
        except Exception:
            print(msg)

    # ── DEBOUNCED AUTOLOAD ────────────────────────────────────────────────────
    def _schedule_autoload(self, delay_ms: int = 150):
        if self._autoload_timer is not None:
            self._autoload_timer.stop()
        t = QTimer(self); t.setSingleShot(True)
        t.timeout.connect(self.load_folders)
        t.start(delay_ms)
        self._autoload_timer = t

    # ── UI BUILD ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Main layout: one row + the log under the whole width
        #   ROW:  left panel (fixed) | view (stretch, full height)
        #   LOG:  fixed height, spans the tab — it used to sit under the panel only,
        #         where with the camera table gone it would be 268 px of wrapped text
        page = QVBoxLayout(self)
        page.setContentsMargins(6, 6, 6, 6); page.setSpacing(4)
        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(6)
        page.addLayout(outer, 1)

        # Left side container
        left_side = QWidget()
        left_side.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(left_side)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(4)

        # Inner horizontal row: left_scroll + table
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0); top_row.setSpacing(6)

        # ════ LEFT PANEL ═════════════════════════════════════════════════════
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(268)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet(
            "QScrollArea{background:transparent;}QScrollBar:vertical{width:10px;}")

        lw = QWidget(); lw.setMinimumWidth(240)
        ll = QVBoxLayout(lw); ll.setContentsMargins(0, 0, 4, 0); ll.setSpacing(4)
        # The panel layout the groups themselves sit in. `ll` below is re-pointed at
        # each group's body in turn, so everything after a group banner lands inside
        # that group.
        panel_lay = ll

        # ── Collapsible groups ───────────────────────────────────────────────
        # Same groups, same colours and the same remembered open/closed state as the
        # Image Slider and Workshop panels. Which groups are open is kept in this
        # tab's own state file next to the PV selection.
        self._ui_state = self._load_ui_state()
        self._sections: dict = {}

        def _add_section(key, title, default_expanded=True):
            cls = _section_cls()
            sec = cls(title, key,
                      bool(self._ui_state.get(f"sec_{key}", default_expanded)),
                      accent=_SECTION_ACCENTS.get(key, "#4a78c0"))
            sec.toggled.connect(self._on_section_toggled)
            self._sections[key] = sec
            panel_lay.addWidget(sec)
            return sec

        _exp_row = QHBoxLayout(); _exp_row.setSpacing(4)
        _btn_exp = QPushButton("Expand all")
        _btn_exp.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_exp.clicked.connect(lambda: self._set_all_sections(True))
        _btn_col = QPushButton("Collapse all")
        _btn_col.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_col.clicked.connect(lambda: self._set_all_sections(False))
        _exp_row.addWidget(_btn_exp); _exp_row.addWidget(_btn_col)
        panel_lay.addLayout(_exp_row)

        # No group of its own for the cameras: the button sits in Source (the day and
        # the cameras are the one "what to search" question, the way the Slider's
        # Source group asks it) and the list of picked cameras sits in Actions.
        # Source first, then Actions right under it: the panel is read top-down as
        # "what to search" → "Load data" → "what to do with what came back". PV Values
        # is a reading of the result, so it sits below both.
        s_time = _add_section("time",     "Source",           True)
        s_act  = _add_section("actions",  "Actions",          True)
        s_pv   = _add_section("pv",       "PV Values",        True)
        s_disp = _add_section("display",  "Image / Display",  False)
        s_cmp  = _add_section("compare",  "Comparison",       False)

        # ══════════════════ Group: SOURCE ════════════════════════════════════
        # The Image Slider's Source pattern: one button opens the calendar, the
        # button next to it picks the cameras, PV Search sits underneath. The panel
        # itself stays short — the calendar is only interesting while choosing.
        ll = s_time.body_layout

        src_row = QHBoxLayout(); src_row.setSpacing(4)
        self._btn_time_window = QPushButton("Time window")
        self._btn_time_window.setToolTip(
            "Pick the day (or days) and the hour to search.")
        self._btn_time_window.clicked.connect(self._open_time_window)
        self._btn_cameras = QPushButton("📷  Cameras…")
        self._btn_cameras.setToolTip(
            "Choose which cameras to search. The list is the cameras found in the "
            "selected day(s); presets are shared with the Image Slider.")
        self._btn_cameras.clicked.connect(self._open_camera_picker)
        src_row.addWidget(self._btn_time_window)
        src_row.addWidget(self._btn_cameras)
        ll.addLayout(src_row)

        # PV Search — under the two pickers it works with.
        self._btn_pv_search = QPushButton("🎯 PV Search…")
        self._btn_pv_search.setToolTip(
            "Plot a PV for a day, drag to mark time regions, and pull camera "
            "frames from the peak of each region.")
        self._btn_pv_search.clicked.connect(self._open_pv_region_search)
        ll.addWidget(self._btn_pv_search)

        # What the Time window button is currently set to — the calendar is behind
        # the button now, so the panel has to say what was picked.
        self._time_summary = QLabel("")
        self._time_summary.setWordWrap(True)
        self._time_summary.setStyleSheet("font-size: 10px; color: #333; padding: 1px 0;")
        # Scan state stays in the PANEL, not in the dialog: it says whether the
        # camera list for the picked days is ready, and that has to be readable
        # with the calendar closed.
        self._status_dot = QLabel("⬤")
        self._status_dot.setToolTip("Green = the camera list for the picked day(s) is ready.")
        self._status_dot.setStyleSheet("color: green; font-size: 12px;")
        sum_row = QHBoxLayout(); sum_row.setSpacing(4)
        sum_row.addWidget(self._time_summary, 1)
        sum_row.addWidget(self._status_dot, 0, Qt.AlignmentFlag.AlignTop)
        ll.addLayout(sum_row)

        # Load data — the button that actually goes and reads the frames for the
        # day, hour and cameras picked above. It belongs to those pickers, not to
        # the things you do afterwards, so it closes this group instead of opening
        # Actions.
        self._btn_view = QPushButton("Load data")
        self._btn_view.setToolTip(
            "Read the frames for the picked day(s), hour and cameras, and show "
            "them in the view on the right.")
        self._btn_view.clicked.connect(self.view_primary_files)
        ll.addWidget(self._btn_view)

        # ── The Time window dialog's contents ────────────────────────────────
        # Built here, exactly as before, but into a pane of its own instead of into
        # the panel. _open_time_window puts this pane in a dialog. Every widget below
        # keeps its name, so all the day/hour logic is untouched.
        self._time_pane = QWidget()
        _tl = QVBoxLayout(self._time_pane)
        _tl.setContentsMargins(0, 0, 0, 0); _tl.setSpacing(4)
        self._time_dlg: "QDialog | None" = None
        ll = _tl

        # Embedded multi-select calendar (ported from Spectra).
        # Plain click = toggle a day; Ctrl+click = add the range from the last click.
        # self._cal stays a QCalendarWidget so all existing selectedDate()/
        # setSelectedDate()/yearShown() calls keep working; the multi-day set is
        # tracked in self._selected_days and painted by the delegate.
        _today = QDate.currentDate()
        self._cal_frame, self._cal = _make_multiselect_calendar(_today)
        self._cal.setMinimumWidth(280)
        self._selected_days: list[QDate] = [_today]
        self._last_cal_click: QDate = _today
        self._cal.clicked.connect(self._on_calendar_clicked)
        self._apply_day_selection([_today], schedule=False)
        ll.addWidget(self._cal_frame)

        # hour + lab time
        hour_row = QHBoxLayout(); hour_row.addWidget(QLabel("Hour:"))
        self._hour_cb = _NoScrollComboBox()
        for h in range(24): self._hour_cb.addItem(f"{h:02d}", h)
        self._hour_cb.setCurrentIndex(9); self._hour_cb.setFixedWidth(60)
        self._hour_cb.currentIndexChanged.connect(self._on_hour_change)
        hour_row.addWidget(self._hour_cb)
        self._lab_time_cb = QCheckBox("Lab time?")
        self._lab_time_cb.setStyleSheet(_CHECKBOX_STYLE)
        self._lab_time_cb.stateChanged.connect(self._on_labtime_toggle)
        hour_row.addWidget(self._lab_time_cb)
        hour_row.addStretch(1)
        ll.addLayout(hour_row)

        # Weekday gate for Ctrl+drag range selection. Only weekdays checked here
        # are added when Ctrl+clicking a range; a plain click still selects ANY
        # day (incl. weekends). Sat/Sun off by default so ranges skip weekends.
        # Day names sit above each checkbox (weekends red, matching the calendar).
        ll.addWidget(QLabel("Ctrl+Shift range adds:"))
        wd_grid = QGridLayout()
        wd_grid.setHorizontalSpacing(2); wd_grid.setVerticalSpacing(1)
        wd_grid.setContentsMargins(0, 0, 0, 0)
        _wd_tip = ("Click = one day (any, incl. weekends).\n"
                   "Ctrl+click = toggle one weekday (weekends skipped).\n"
                   "Ctrl+Shift+click = range from last click — only the weekdays\n"
                   "checked here — XOR-ed into the selection (repeat deselects).")
        self._wd_checks: list[QCheckBox] = []
        for i, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            name_lbl = QLabel(nm)
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setStyleSheet(
                "font-size:9px; font-weight:700; color:%s;"
                % ("#cc0000" if i >= 5 else "#555"))
            name_lbl.setToolTip(_wd_tip)
            wd_grid.addWidget(name_lbl, 0, i)
            cb = QCheckBox()
            cb.setChecked(i < 5)   # Mon–Fri on, Sat/Sun off
            cb.setStyleSheet(_CHECKBOX_STYLE)
            cb.setToolTip(_wd_tip)
            wd_grid.addWidget(cb, 1, i, alignment=Qt.AlignmentFlag.AlignCenter)
            wd_grid.setColumnStretch(i, 1)
            self._wd_checks.append(cb)
        ll.addLayout(wd_grid)

        # ══════════════════ Group: PV VALUES ═════════════════════════════════
        ll = s_pv.body_layout

        self._energy_info = QPlainTextEdit()
        self._energy_info.setReadOnly(True)
        # The PV list. Literally the Image Slider's widget (PvValueTable) over the
        # Slider's registry, so one PV cannot read one way in this tab and another way
        # there. The eye takes a PV off the picture without stopping it being read.
        self._pv_table = _get_slider_module().PvValueTable()
        self._pv_table.setVisible(False)
        self._pv_table.eye_clicked.connect(self._pv_toggle_eye)
        ll.addWidget(self._pv_table)
        self._pv_no_pv_lbl = QLabel("No PVs selected. Click PVs to choose.")
        self._pv_no_pv_lbl.setStyleSheet("font-size: 10px; color: #888; padding: 2px 0;")
        self._pv_no_pv_lbl.setWordWrap(True)
        ll.addWidget(self._pv_no_pv_lbl)

        self._energy_info.setMaximumHeight(120)
        self._energy_info.setPlaceholderText(
            "Energy values appear here after Load data.")
        self._energy_info.setStyleSheet(
            "font-family:Consolas,monospace;font-size:11px;"
            "background:#f9f9f9;border:1px solid #ddd;")
        ll.addWidget(self._energy_info)
        # The picked PVs come back now that the table they paint into exists. Before
        # this the tab started every session reading NOTHING until the picker was
        # opened by hand, which reads exactly like "the PVs are there but it ignores
        # them".
        self._load_pv_state()
        self._pv_refresh_table()
        # Navigation row for energy results
        # Row 1: ◀ label ▶  |  PVs
        nav_row = QHBoxLayout()
        self._btn_energy_prev = QPushButton("◀")
        self._btn_energy_prev.setFixedWidth(28)
        self._btn_energy_prev.setToolTip("Previous shot (energy data)")
        self._btn_energy_prev.clicked.connect(self._energy_nav_prev)
        self._btn_energy_next = QPushButton("▶")
        self._btn_energy_next.setFixedWidth(28)
        self._btn_energy_next.setToolTip("Next shot (energy data)")
        self._btn_energy_next.clicked.connect(self._energy_nav_next)
        self._energy_nav_lbl = QLabel("0 / 0")
        self._energy_nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._energy_nav_lbl.setStyleSheet("font-size: 10px; color: #555;")
        nav_row.addWidget(self._btn_energy_prev)
        nav_row.addWidget(self._energy_nav_lbl, 1)
        nav_row.addWidget(self._btn_energy_next)
        # separator
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color: #ccc;")
        nav_row.addWidget(sep)
        self._btn_energy_cols = QPushButton("PVs")
        self._btn_energy_cols.setFixedWidth(36)
        self._btn_energy_cols.setToolTip(
            "Pick the PVs to read: the presets, any archiver channel (search it or "
            "type its name in full), and formulas.\n"
            "The same picker the Image Slider has, over the same list.")
        self._btn_energy_cols.clicked.connect(self._pick_energy_columns)
        nav_row.addWidget(self._btn_energy_cols)
        ll.addLayout(nav_row)

        # Row 2: Navigate images  |  Annotate energies
        cb_row = QHBoxLayout()
        self._cb_pv_preview = QCheckBox("PVs in preview")
        self._cb_pv_preview.setChecked(True)
        self._cb_pv_preview.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_pv_preview.setToolTip(
            "Show a white bar with the selected PV values below the image\n"
            "in the preview above. Does not change the saved files.")
        self._cb_pv_preview.stateChanged.connect(self._on_pv_preview_toggle)
        self._cb_annotate = QCheckBox("attach PVs")
        self._cb_annotate.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_annotate.setToolTip(
            "Bake a white bar with the selected PV values below each\n"
            "image when saving (Save As…).")
        cb_row.addWidget(self._cb_pv_preview, 1)
        cb_row.addWidget(self._cb_annotate, 1)
        ll.addLayout(cb_row)

        # Energy navigation mode toggle
        cb_row2 = QHBoxLayout()
        self._cb_nav_images = QCheckBox("Browse energy rows")
        self._cb_nav_images.setChecked(False)
        self._cb_nav_images.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_nav_images.setToolTip(
            "Unchecked (default): the ◀ ▶ arrows step through the loaded "
            "images, showing each image's PV values.\n"
            "Checked: the arrows scroll through the CSV energy rows around "
            "the current image, without changing the displayed image.")
        self._cb_nav_images.stateChanged.connect(self._on_nav_mode_changed)
        cb_row2.addWidget(self._cb_nav_images, 1)
        ll.addLayout(cb_row2)

        # ══════════════════ Group: ACTIONS ═══════════════════════════════════
        ll = s_act.body_layout

        # action buttons  row0=[Save As|Folder]  row1=[Workshop]
        # Load data moved up into Source — this group is only what you do with the
        # frames once they are loaded.
        btn_grid = QGridLayout(); btn_grid.setSpacing(4)
        self._btn_save = QPushButton("Save As...")
        self._btn_save.clicked.connect(self.save_primary_files_as)
        self._btn_open_folder = QPushButton("📁 Folder")
        self._btn_open_folder.clicked.connect(self.open_folder_in_explorer)
        btn_grid.addWidget(self._btn_save, 0, 0)
        btn_grid.addWidget(self._btn_open_folder, 0, 1)

        self._btn_send_workshop = QPushButton("➤ Workshop")
        self._btn_send_workshop.setToolTip("Send currently selected images to Workshop tab for editing")
        self._btn_send_workshop.clicked.connect(self._send_to_workshop)
        btn_grid.addWidget(self._btn_send_workshop, 1, 0, 1, 2)
        ll.addLayout(btn_grid)

        # The cameras that will be searched — just the list, no banner and no count
        # line: the count is on the Cameras… button and the rest is in the tooltip.
        self._sel_table = QTableWidget(0, 2)
        self._sel_table.setHorizontalHeaderLabels(["Cam #", "Camera"])
        self._sel_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._sel_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._sel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._sel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sel_table.setMaximumHeight(180)
        self._sel_table.setToolTip(
            "The cameras that will be searched.\n"
            "Click a camera to preview its first frame, double-click to unpick it.")
        self._sel_table.clicked.connect(self._on_sel_table_clicked)
        self._sel_table.doubleClicked.connect(self._on_sel_table_double_clicked)
        ll.addWidget(self._sel_table)

        # The frame arrows are NOT here any more — they are under the picture on the
        # One frame page, where the frame they step through is.

        # ══════════════════ Group: IMAGE / DISPLAY ═══════════════════════════
        ll = s_disp.body_layout

        # Gradient — the palette the preview image is drawn with
        grad_row = QHBoxLayout(); grad_row.addWidget(QLabel("Gradient:"))
        self._gradient_cb = _NoScrollComboBox()
        for name in GRADIENT_NAMES: self._gradient_cb.addItem(name)
        self._gradient_cb.setCurrentText("Gradient")
        self._gradient_cb.currentTextChanged.connect(self._on_gradient_changed)
        grad_row.addWidget(self._gradient_cb, 1)
        ll.addLayout(grad_row)

        # Contrast / Brightness / Gamma — the Image Slider's three-row block, control
        # for control. Moving a slider re-renders the previewed frame, which is a read
        # off the share, so the three of them share one debounce instead of firing on
        # every pixel of a drag.
        self._bc_debounce = QTimer(self)
        self._bc_debounce.setSingleShot(True)
        self._bc_debounce.setInterval(120)
        self._bc_debounce.timeout.connect(self._bc_reshow_preview)

        # CONTRAST. Its Auto box is the percentile auto-stretch this tab used to show as
        # a separate "Auto stretch" checkbox — the same operation, now sitting on the row
        # it overrides, the way the Slider has always had it. Absolute scale stays the
        # default (see img_scale): one palette colour = one intensity, so frames and
        # cameras are comparable. Auto trades that away for legibility on the dim
        # cameras, which is why it is visible and off by default rather than something
        # the viewer does behind the operator's back.
        row_con = QHBoxLayout()
        self._lbl_contrast_name = QLabel("Con:")
        self._lbl_contrast_name.setMinimumWidth(_BC_NAME_W)
        self._lbl_contrast_name.setToolTip(_TT_CONTRAST)
        row_con.addWidget(self._lbl_contrast_name)
        self._contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self._contrast_slider.setRange(img_scale.CONTRAST_MIN, img_scale.CONTRAST_MAX)
        self._contrast_slider.setValue(0)
        self._contrast_slider.setToolTip(_TT_CONTRAST)
        self._contrast_slider.valueChanged.connect(self._on_contrast_slider_changed)
        # The manual value to come back to when Auto is switched off — the greyed-out
        # slider is overwritten while Auto is on (see _park_auto_bc).
        self._contrast_manual = 0
        row_con.addWidget(self._contrast_slider, 1)
        self._lbl_contrast_val = _bc_value_label("0", _TT_CONTRAST)
        row_con.addWidget(self._lbl_contrast_val)
        self._btn_contrast_reset = QPushButton("↺")
        self._btn_contrast_reset.setFixedWidth(26)
        self._btn_contrast_reset.setToolTip("Reset contrast")
        self._btn_contrast_reset.clicked.connect(self._reset_contrast_slider)
        row_con.addWidget(self._btn_contrast_reset)
        self._cb_auto_stretch = QCheckBox("Auto")
        self._cb_auto_stretch.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_auto_stretch.setToolTip(
            "Auto contrast — stretch each frame over its own p0.5–p99.5 window. "
            "Overrides the Contrast slider.\n"
            "OFF: absolute scale — pixel value / camera full scale. Brightness is "
            "comparable between frames and between cameras.\n"
            "ON: a dim frame becomes readable, but colours no longer mean the same "
            "intensity from frame to frame.\n"
            "The Binary and False Colors palettes always map per frame, by design.")
        self._cb_auto_stretch.toggled.connect(self._on_auto_stretch_toggled)
        row_con.addWidget(self._cb_auto_stretch)
        ll.addLayout(row_con)

        # BRIGHTNESS — an additive offset, never a gain (that is what Contrast is).
        # Its Auto box is the auto LEVEL: the same p0.5–p99.5 window as Auto contrast,
        # placed on the data. No additive rule can do that job — a shift cannot spread a
        # narrow range — which is why Auto brightness and Auto contrast are one pass and
        # ticking both does not level the frame twice.
        row_bri = QHBoxLayout()
        self._lbl_bright_name = QLabel("Bri:")
        self._lbl_bright_name.setMinimumWidth(_BC_NAME_W)
        self._lbl_bright_name.setToolTip(_TT_BRIGHTNESS)
        row_bri.addWidget(self._lbl_bright_name)
        self._bright_slider = QSlider(Qt.Orientation.Horizontal)
        self._bright_slider.setRange(img_scale.BRIGHTNESS_MIN, img_scale.BRIGHTNESS_MAX)
        self._bright_slider.setValue(0)
        self._bright_slider.setToolTip(_TT_BRIGHTNESS)
        self._bright_slider.valueChanged.connect(self._on_bright_slider_changed)
        self._bright_manual = 0
        row_bri.addWidget(self._bright_slider, 1)
        self._lbl_bright_val = _bc_value_label("0", _TT_BRIGHTNESS)
        row_bri.addWidget(self._lbl_bright_val)
        self._btn_bright_reset = QPushButton("↺")
        self._btn_bright_reset.setFixedWidth(26)
        self._btn_bright_reset.setToolTip("Reset brightness")
        self._btn_bright_reset.clicked.connect(self._reset_bright_slider)
        row_bri.addWidget(self._btn_bright_reset)
        self._cb_bright_auto = QCheckBox("Auto")
        self._cb_bright_auto.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_bright_auto.setToolTip(
            "Auto brightness — auto level: the frame's p0.5–p99.5 window mapped onto "
            "the full range. Overrides the Brightness slider.\n"
            "Per-frame, so it gives up comparability the same way Auto contrast does.")
        self._cb_bright_auto.toggled.connect(self._on_bright_auto_toggled)
        row_bri.addWidget(self._cb_bright_auto)
        ll.addLayout(row_bri)

        # GAMMA — the third member of the pattern, and the only one that does NOT cost
        # comparability: the curve depends on the pixel value alone, so one colour still
        # means one intensity (see img_scale). It is the answer to "the absolute scale is
        # right but the frame is dark" that Auto contrast answers by giving that up.
        row_gam = QHBoxLayout()
        self._lbl_gamma = QLabel("Gam:")
        self._lbl_gamma.setMinimumWidth(_BC_NAME_W)
        self._lbl_gamma.setToolTip(_TT_GAMMA)
        row_gam.addWidget(self._lbl_gamma)
        self._gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self._gamma_slider.setRange(img_scale.GAMMA_SLIDER_MIN, img_scale.GAMMA_SLIDER_MAX)
        self._gamma_slider.setValue(img_scale.GAMMA_SLIDER_NEUTRAL)
        self._gamma_slider.setToolTip(_TT_GAMMA)
        self._gamma_slider.valueChanged.connect(self._on_gamma_slider_changed)
        self._gamma_manual = img_scale.GAMMA_SLIDER_NEUTRAL
        row_gam.addWidget(self._gamma_slider, 1)
        self._lbl_gamma_val = _bc_value_label("1.00", _TT_GAMMA)
        row_gam.addWidget(self._lbl_gamma_val)
        self._btn_gamma_reset = QPushButton("↺")
        self._btn_gamma_reset.setFixedWidth(26)
        self._btn_gamma_reset.setToolTip("Reset gamma to 1.00 (linear)")
        self._btn_gamma_reset.clicked.connect(self._reset_gamma_slider)
        row_gam.addWidget(self._btn_gamma_reset)
        self._cb_gamma_auto = QCheckBox("Auto")
        self._cb_gamma_auto.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_gamma_auto.setToolTip(
            "Auto gamma — the curve that lands THIS frame's median at "
            f"{int(img_scale.AUTO_GAMMA_TARGET * 100)} % of the range.\n"
            "Per-frame, so it overrides the slider and gives up comparability, same as "
            "Auto contrast.")
        self._cb_gamma_auto.toggled.connect(self._on_gamma_auto_toggled)
        row_gam.addWidget(self._cb_gamma_auto)
        ll.addLayout(row_gam)
        # All three rows exist now, so put them in the state their checkboxes call for.
        self._sync_bc_enabled()

        # ══════════════════ Group: COMPARISON ════════════════════════════════
        ll = s_cmp.body_layout

        # memory A/B
        ab_row = QHBoxLayout()
        btn_add_a = QPushButton("Add to A"); btn_add_a.clicked.connect(lambda: self._save_to_memory("A"))
        btn_add_b = QPushButton("Add to B"); btn_add_b.clicked.connect(lambda: self._save_to_memory("B"))
        ab_row.addWidget(btn_add_a); ab_row.addWidget(btn_add_b); ll.addLayout(ab_row)

        self._lbl_mem_a = QLabel("A: —"); self._lbl_mem_b = QLabel("B: —")
        a_row = QHBoxLayout(); a_row.addWidget(self._lbl_mem_a, 1)
        btn_ca = QPushButton("Clear A"); btn_ca.setFixedWidth(65)
        btn_ca.clicked.connect(lambda: self._clear_slot("A")); a_row.addWidget(btn_ca); ll.addLayout(a_row)
        b_row = QHBoxLayout(); b_row.addWidget(self._lbl_mem_b, 1)
        btn_cb_w = QPushButton("Clear B"); btn_cb_w.setFixedWidth(65)
        btn_cb_w.clicked.connect(lambda: self._clear_slot("B")); b_row.addWidget(btn_cb_w); ll.addLayout(b_row)

        self._btn_compare = QPushButton("Compare A vs B")
        self._btn_compare.setEnabled(False)
        self._btn_compare.clicked.connect(self._compare_memory)
        ll.addWidget(self._btn_compare)

        panel_lay.addStretch(1)
        left_scroll.setWidget(lw)

        # The camera table used to sit here, to the right of the panel, taking a
        # column of the window for a list that only matters while choosing. It is
        # now the Cameras… picker; the panel is the whole left side and every pixel
        # it freed goes to the pictures.
        top_row.addWidget(left_scroll)
        root.addLayout(top_row, 1)

        # ── Log box — full width of the tab, fixed height ─────────────────────
        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(100)
        self._log_box.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        self._log_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._log_box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        page.addWidget(self._log_box, 0)

        # left_side uses stretch=0 so it stays at the panel's own width;
        # preview_col gets all remaining space via stretch=1
        outer.addWidget(left_side, 0)

        # ── View — right column, full height ──────────────────────────────────
        # One result set, several ways to read it, as tabs: the close-up of a single
        # frame, one wall per camera (its days next to each other), and the day-by-day
        # wall where each row is a day and each column the same camera throughout.
        # These used to be split between this tab and a pop-up window that opened after
        # every search; there is now one place to look.
        preview_col = QWidget()
        preview_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pcl = QVBoxLayout(preview_col)
        pcl.setContentsMargins(0, 0, 0, 0); pcl.setSpacing(2)

        self._wall_shared = _WallShared()
        self._cam_walls: "dict[str, _DayWall]" = {}
        self._wall_pages: "dict[int, _DayWall]" = {}   # tab index → wall
        self._last_wall_tab = 1

        view_row = QHBoxLayout(); view_row.setSpacing(4)
        view_row.addWidget(QLabel("Reference day:"))
        self._baseline_cb = _NoScrollComboBox()
        self._baseline_cb.setMinimumWidth(120)
        self._baseline_cb.currentIndexChanged.connect(self._on_baseline_changed)
        view_row.addWidget(self._baseline_cb)

        self._btn_save_wall = QPushButton("Save comparison…")
        self._btn_save_wall.setToolTip(
            "The whole wall as one picture, day captions included.")
        self._btn_save_wall.clicked.connect(self._save_wall)
        view_row.addWidget(self._btn_save_wall)

        sep_v = QLabel("|"); sep_v.setStyleSheet("color:#777; padding:0 4px;")
        view_row.addWidget(sep_v)
        self._build_overlay_row(view_row)
        view_row.addStretch(1)
        pcl.addLayout(view_row, 0)

        # The "current" wall — whichever tab is showing. It always exists, so every
        # caller (Stop All, the display sliders, the tests) has something to talk to
        # before the first search has run.
        self._wall = _DayWall(shared=self._wall_shared)
        self._wire_wall(self._wall)

        single_page = QWidget()
        spl = QVBoxLayout(single_page)
        spl.setContentsMargins(0, 0, 0, 0); spl.setSpacing(2)

        self._view_tabs = QTabWidget()
        self._view_tabs.setDocumentMode(True)
        self._view_tabs.addTab(single_page, "One frame")          # index 0
        self._view_tabs.addTab(self._wrap_scroll(self._wall), "Days side by side")
        self._wall_pages = {1: self._wall}
        self._view_tabs.currentChanged.connect(self._on_view_tab_changed)
        pcl.addWidget(self._view_tabs, 1)

        pcl = spl        # everything below builds the single-frame page

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setStyleSheet("background:#1a1a1a; border-radius:3px;")
        self._preview_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_lbl.setMinimumSize(100, 100)
        # Scale pixmap to fit the label automatically when label is resized
        self._preview_lbl.setScaledContents(False)
        pcl.addWidget(self._preview_lbl, 1)

        lbl_row = QHBoxLayout()
        lbl_row.setContentsMargins(0, 0, 0, 0)
        lbl_row.setSpacing(2)
        self._preview_cam_lbl = QLabel("")
        self._preview_cam_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._preview_cam_lbl.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #eee; background: #444; "
            "padding: 2px 6px; border-radius: 2px;")
        self._preview_cam_lbl.setFixedHeight(34)
        self._preview_ts_lbl = QLabel("")
        self._preview_ts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._preview_ts_lbl.setStyleSheet(
            "font-size: 20px; color: #ffd54f; background: #333; "
            "padding: 2px 6px; border-radius: 2px;")
        self._preview_ts_lbl.setFixedHeight(34)
        lbl_row.addWidget(self._preview_cam_lbl, 1)
        lbl_row.addWidget(self._preview_ts_lbl, 2)
        pcl.addLayout(lbl_row, 0)

        # What the displayed intensities MEAN. Without it a frame at 3 % of full scale
        # is indistinguishable from a broken render, and a palette is decoration rather
        # than a reading.
        self._preview_scale_lbl = QLabel("")
        self._preview_scale_lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                                            | Qt.AlignmentFlag.AlignVCenter)
        self._preview_scale_lbl.setStyleSheet(
            "font-size: 11px; color: #bbb; background: transparent; padding: 0 6px;")
        pcl.addWidget(self._preview_scale_lbl, 0)

        # Step through the loaded frames. These arrows lived in the panel on the left,
        # far from the picture they move — nobody found them, and the page looked like
        # it could only ever show one frame. They belong under the frame.
        nav_prev_row = QHBoxLayout(); nav_prev_row.setSpacing(4)
        nav_prev_row.addStretch(1)
        self._prev_btn = QPushButton("◀"); self._prev_btn.setFixedWidth(40)
        self._prev_btn.setToolTip("Previous loaded frame")
        self._prev_btn.clicked.connect(self._preview_prev)
        self._next_btn = QPushButton("▶"); self._next_btn.setFixedWidth(40)
        self._next_btn.setToolTip("Next loaded frame")
        self._next_btn.clicked.connect(self._preview_next)
        # "0 / 0" from the start, not an empty label: the row has to read as frame
        # navigation before anything is loaded, or it looks like decoration again.
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._preview_counter = QLabel("0 / 0")
        self._preview_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_counter.setMinimumWidth(90)
        self._preview_counter.setStyleSheet("font-size:11px; color:#333;")
        nav_prev_row.addWidget(self._prev_btn)
        nav_prev_row.addWidget(self._preview_counter, 0)
        nav_prev_row.addWidget(self._next_btn)
        nav_prev_row.addStretch(1)
        pcl.addLayout(nav_prev_row, 0)

        outer.addWidget(preview_col, 1)

        # The day was applied before the hour combo existed, so the line under the
        # buttons is written once here, with every widget in place.
        self._sync_time_summary()
        self._log("READY. No network scan on startup.")
        self._log(f"IMAGES_ROOT_BASE = {IMAGES_ROOT_BASE}")
        self._log(f"Network source: {'Lab' if _IS_LAB else 'Office'} (hostname: {_socket.gethostname()})")
        self._log(f"Ramping source = {self._ramping_source}")
        self._log("Select a day to start ramping auto-hour + load folders.")

    # ── CAMERA LIST HELPERS ───────────────────────────────────────────────────
    def _picked_cams(self) -> list:
        """The picked cameras, in the order the scan found them."""
        return [c for c in self._cams if c.get("checked")]

    def _cam_by_name(self, name: str) -> "dict | None":
        for c in self._cams:
            if c["name"] == name:
                return c
        return None

    def _open_camera_picker(self):
        """The Cameras… button — pick which cameras the search runs on."""
        if not self._cams:
            QMessageBox.information(
                self, "No cameras yet",
                "No cameras have been found for the selected day(s) yet.\n\n"
                "Open Time window, pick a day, and wait for the scan to finish "
                "(the dot next to the day/hour line turns green).")
            return
        dlg = _CameraPickDialog(self._cams, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = set(dlg.selected_names())
        for c in self._cams:
            c["checked"] = c["name"] in picked
        self._refresh_selected_table()
        self._log(f"Cameras: {len(picked)} selected")

    # ── TIME WINDOW ───────────────────────────────────────────────────────────
    def _open_time_window(self):
        """The Time window button — the calendar, the hour and the range gate, in a
        window of their own. Picking a day still takes effect the moment it is
        clicked, so the dialog is modeless and has nothing to confirm: it can stay
        open next to the pictures it is changing."""
        if self._time_dlg is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Time window")
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(6)
            lay.addWidget(self._time_pane)
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            btns.rejected.connect(dlg.close)
            lay.addWidget(btns)
            self._time_dlg = dlg
        self._time_dlg.show()
        self._time_dlg.raise_()
        self._time_dlg.activateWindow()

    def _sync_time_summary(self):
        """One line under the buttons saying what the Time window is set to."""
        if not hasattr(self, "_time_summary") or not hasattr(self, "_hour_cb"):
            return
        days = list(self._selected_days)
        hour = self._hour_cb.currentIndex()
        zone = "lab time" if self._lab_time_cb.isChecked() else "Prague time"
        if not days:
            self._time_summary.setText("No day picked")
            return
        first = days[0].toString("dd.MM.yyyy")
        if len(days) == 1:
            head = first
        else:
            head = f"{len(days)} days ({first} … {days[-1].toString('dd.MM.yyyy')})"
        self._time_summary.setText(f"{head}  ·  {hour:02d}:00  ·  {zone}")

    def _sync_cameras_button(self):
        # No counts on the button. "0/92" said nothing useful — nobody knows which
        # 92 cameras a given day happens to hold, and the picked ones are listed
        # right below in Actions.
        self._btn_cameras.setText("📷  Cameras…")

    # ── Day wall ──────────────────────────────────────────────────────────────
    def _wrap_scroll(self, wall: "_DayWall") -> QWidget:
        """A wall inside a scroll area. Days side by side always fits the window, but
        day-by-day is as tall as there are days, so it has to be able to scroll."""
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(wall)
        wall._scroll_host = sc
        return sc

    def _wire_wall(self, wall: "_DayWall"):
        wall.tile_clicked.connect(self._on_wall_tile_clicked)
        wall.tile_context.connect(self._on_wall_context)
        wall.selection_changed.connect(self._on_wall_selection_changed)

    def _all_walls(self) -> list:
        return list(self._wall_pages.values())

    def _set_view_mode(self, idx: int):
        """Kept for the callers that only ever meant "show a wall" (0) or "show the
        close-up" (1). The close-up is tab 0; a wall is whichever wall tab was last
        looked at."""
        self._view_tabs.setCurrentIndex(0 if idx else self._last_wall_tab)

    def _on_view_tab_changed(self, idx: int):
        wall = self._wall_pages.get(idx)
        on_wall = wall is not None
        if on_wall:
            self._last_wall_tab = idx
            self._wall = wall
            self._sync_wall_display()
            # The reference day names a tile, and every tab holds a different set of
            # them, so the list is rebuilt for the wall now on screen.
            self._rebuild_baseline_combo(wall.cells())
        for w in (self._baseline_cb, self._btn_save_wall):
            w.setEnabled(on_wall)
        for w in self._overlay_widgets:
            w.setEnabled(on_wall)

    def fill_wall(self, results: dict, cameras: "list | None" = None):
        """Put a finished search on the walls: one tile per day per camera.

        `results` is the shape the multi-day search already produces —
        {cam_folder_name: [(day, hour, path, meta, status), …]}.

        One tab per camera holds that camera's days next to each other, and one more
        holds every camera arranged a day per row. They share one frame cache and one
        set of per-frame adjustments (`_WallShared`), so a frame is read from the share
        once however many tabs it appears in."""
        cells = []
        for cam_name in sorted(results.keys()):
            for day, _hour, path, meta, status in results[cam_name]:
                if status != "found" or not path:
                    continue
                cells.append({
                    "day": day,
                    "cam": extract_display_label(cam_name),
                    "cam_folder": cam_name,
                    "meta": dict(meta or {}),
                    "path": Path(path),
                    "ts_ns": extract_ns_from_stem(Path(path).stem),
                    "status": status,
                })
        cells.sort(key=lambda c: (c["cam"], str(c["day"])))
        self._build_wall_tabs(cells)
        self._log(f"WALL: {len(cells)} day(s) on the wall.")

    def _build_wall_tabs(self, cells: list):
        # Tab 0 (the close-up) is never rebuilt — it holds the preview widgets.
        self._view_tabs.blockSignals(True)
        while self._view_tabs.count() > 1:
            page = self._view_tabs.widget(1)
            self._view_tabs.removeTab(1)
            page.deleteLater()
        self._wall_pages.clear()
        self._cam_walls.clear()

        cams = sorted({c["cam"] for c in cells})
        for cam in cams:
            w = _DayWall(shared=self._wall_shared)
            self._wire_wall(w)
            w.set_cells([c for c in cells if c["cam"] == cam])
            idx = self._view_tabs.addTab(self._wrap_scroll(w), cam)
            self._wall_pages[idx] = w
            self._cam_walls[cam] = w

        day_wall = _DayWall(shared=self._wall_shared)
        self._wire_wall(day_wall)
        if cells:
            day_wall.set_layout_mode("rows")
            day_wall.set_cells(cells)
            title = "Day by day"
        else:
            # Nothing found. One empty wall still goes up, so every caller — Stop All,
            # the display sliders — has a wall to talk to, and it says so on its face.
            title = "Days side by side"
        idx = self._view_tabs.addTab(self._wrap_scroll(day_wall), title)
        self._wall_pages[idx] = day_wall
        self._day_wall = day_wall
        self._view_tabs.blockSignals(False)

        first_wall = min(self._wall_pages) if self._wall_pages else 0
        self._last_wall_tab = first_wall
        self._wall = self._wall_pages.get(first_wall, self._wall)
        self._sync_wall_display()
        self._rebuild_baseline_combo(self._wall.cells())
        self._view_tabs.setCurrentIndex(first_wall)
        self._on_view_tab_changed(first_wall)
        self._on_wall_selection_changed()

    def _rebuild_baseline_combo(self, cells: list):
        self._baseline_cb.blockSignals(True)
        self._baseline_cb.clear()
        self._baseline_cb.addItem("none", None)
        multi_cam = len({c["cam"] for c in cells}) > 1
        for i, c in enumerate(cells):
            day = c["day"]
            txt = day.strftime("%d.%m.%Y") if hasattr(day, "strftime") else str(day)
            if multi_cam:
                txt = f"{c['cam']}  {txt}"
            self._baseline_cb.addItem(txt, i)
        self._baseline_cb.blockSignals(False)

    def _on_baseline_changed(self, _idx: int):
        self._wall.set_baseline(self._baseline_cb.currentData())

    # ── Overlay / rotation / undo row ─────────────────────────────────────────
    def _build_overlay_row(self, row: QHBoxLayout):
        """Marking up a frame and turning it round — carried over from the pop-up window
        that used to open after every search."""
        self._overlay_widgets: list = []

        def add(w):
            row.addWidget(w)
            self._overlay_widgets.append(w)
            return w

        self._wall_draw_btns: dict = {}
        for kind, label, col in (("circle", "Circle", QColor(255, 255, 0, 230)),
                                 ("square", "Square", QColor(0, 200, 255, 230)),
                                 ("cross",  "Cross",  QColor(0, 255, 0, 220))):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(f"Draw a {label.lower()} on a frame: drag on it, then drag "
                           "the handles to move or resize. Shift keeps it round/square.")
            btn.toggled.connect(lambda on, k=kind: self._on_draw_mode_toggled(k, on))
            add(btn)
            self._wall_draw_btns[kind] = btn
            cb = QPushButton()
            cb.setFixedSize(16, 16)
            cb.setToolTip(f"{label} colour")
            cb.setStyleSheet(f"background:{col.name()}; border:1px solid #888; "
                             "border-radius:2px;")
            cb.clicked.connect(lambda _=False, k=kind: self._pick_shape_color(k))
            add(cb)
            self._wall_shape_colors = getattr(self, "_wall_shape_colors", {})
            self._wall_shape_colors[kind] = col
            self._wall_color_btns = getattr(self, "_wall_color_btns", {})
            self._wall_color_btns[kind] = cb

        b = add(QPushButton("Clear marks"))
        b.setToolTip("Remove every drawn mark from every frame.")
        b.clicked.connect(self._clear_wall_overlays)

        b = add(QPushButton("↺ 90°"))
        b.setToolTip("Turn the selected frames counter-clockwise (all of them if none "
                     "is selected).")
        b.clicked.connect(lambda: self._rotate_wall(-90))
        b = add(QPushButton("↻ 90°"))
        b.setToolTip("Turn the selected frames clockwise (all of them if none is "
                     "selected).")
        b.clicked.connect(lambda: self._rotate_wall(+90))

        b = add(QPushButton("↩ Undo"))
        b.setToolTip("Step back through the changes made to individual frames.")
        b.clicked.connect(self._undo_wall_edit)
        b = add(QPushButton("Reset…"))
        b.setToolTip("Put every frame back on the shared brightness and remove all marks.")
        b.clicked.connect(self._reset_wall_edits)

        self._sel_wall_lbl = QLabel("0 frames selected")
        self._sel_wall_lbl.setStyleSheet("color:#888; font-size:11px;")
        add(self._sel_wall_lbl)

    def _on_draw_mode_toggled(self, kind: str, on: bool):
        # At most one shape mode at a time, as in the window this came from.
        if on:
            for k, btn in self._wall_draw_btns.items():
                if k != kind and btn.isChecked():
                    btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
        mode = kind if on else ""
        for w in self._all_walls():
            w.set_draw_mode(mode)

    def _pick_shape_color(self, kind: str):
        cur = self._wall_shape_colors.get(kind, QColor("#ffffff"))
        col = QColorDialog.getColor(cur, self, f"{kind.capitalize()} colour")
        if not col.isValid():
            return
        self._wall_shape_colors[kind] = col
        self._wall_color_btns[kind].setStyleSheet(
            f"background:{col.name()}; border:1px solid #888; border-radius:2px;")
        for w in self._all_walls():
            w.set_shape_color(kind, col)

    def _clear_wall_overlays(self):
        self._wall_shared.push_undo()
        for w in self._all_walls():
            w.clear_overlays()

    def _rotate_wall(self, delta: int):
        paths = self._wall_target_paths()
        if not paths:
            return
        self._wall_shared.push_undo()
        for w in self._all_walls():
            w.rotate(paths, delta)

    def _undo_wall_edit(self):
        if not self._wall_shared.pop_undo():
            return
        for w in self._all_walls():
            w.refresh_edits()
        self._on_wall_selection_changed()

    def _reset_wall_edits(self):
        self._wall_shared.push_undo()
        self._wall_shared.clear_edits()
        for w in self._all_walls():
            w.refresh_edits()

    # ── selection ─────────────────────────────────────────────────────────────
    def _wall_target_paths(self) -> list:
        """Which frames a display control aims at: the selected ones, or every frame on
        the wall when nothing is selected. Same rule as the Image Slider — the scope is
        read at the moment the control is used, so picking a frame afterwards never
        drags somebody else's settings onto it."""
        sel = set(self._wall_shared.sel)
        if sel:
            return list(sel)
        return [c["path"] for c in self._wall.cells() if c.get("path") is not None]

    def _on_wall_selection_changed(self):
        n = len(self._wall_shared.sel)
        if hasattr(self, "_sel_wall_lbl"):
            self._sel_wall_lbl.setText(
                "0 frames selected" if n == 0 else
                ("1 frame selected" if n == 1 else f"{n} frames selected"))
        for w in self._all_walls():
            w.update()

    def _sync_wall_display(self):
        """Push the display controls onto the walls.

        With nothing selected this is the shared setting every tile renders on — which
        is what keeps the days comparable. With frames selected the values become THOSE
        frames' own, and they are marked as adjusted; see the note on _DayWall."""
        if not hasattr(self, "_wall"):
            return
        auto, gamma, contrast, offset = self._bc_args()
        grad = self._gradient_cb.currentText() if hasattr(self, "_gradient_cb") else "Grayscale"
        sel = list(self._wall_shared.sel)
        if sel:
            # Scoped: the slider moved THESE frames and must leave the rest of the wall
            # exactly where it was, so the shared values are held at what they were.
            shared = getattr(self, "_wall_display_shared", (auto, gamma, contrast, offset))
        else:
            shared = (auto, gamma, contrast, offset)
        self._wall_display_shared = shared
        # The palette is a colour scheme, not an intensity, so it always applies to the
        # whole wall — a per-frame palette would be a second legend on the same picture.
        for w in self._all_walls() or [self._wall]:
            w.set_display(grad, *shared)
            if sel:
                w.apply_adjust(sel, contrast, offset, gamma)

    def _on_wall_tile_clicked(self, idx: int):
        """A tile is the way into the close-up: the wall answers "which day is
        different", the One frame tab answers "what exactly does it look like"."""
        cells = self._wall.cells()
        if not (0 <= idx < len(cells)):
            return
        cell = cells[idx]
        self._preview_set_files([cell["path"]], cell.get("cam", ""),
                                [cell.get("cam", "")])
        self._set_view_mode(1)

    def _on_wall_context(self, idx: int, gpos):
        cells = self._wall.cells()
        if not (0 <= idx < len(cells)):
            return
        cell = cells[idx]
        menu = QMenu(self)
        act_open = menu.addAction("Open in One frame")
        act_again = menu.addAction("↻ Search again…")
        act_pick = menu.addAction("📂 Pick image from folder…")
        menu.addSeparator()
        is_base = (self._wall.baseline_idx() == idx)
        act_ref = menu.addAction("Clear reference day" if is_base else "Set as reference day")
        act_clear = menu.addAction("Clear marks on this frame")
        chosen = menu.exec(gpos)
        if chosen is None:
            return
        if chosen is act_open:
            self._on_wall_tile_clicked(idx)
        elif chosen is act_again:
            self._wall_search_again(cell)
        elif chosen is act_pick:
            self._wall_pick_from_folder(cell)
        elif chosen is act_ref:
            self._baseline_cb.setCurrentIndex(0 if is_base else idx + 1)
        elif chosen is act_clear:
            self._wall_shared.push_undo()
            for w in self._all_walls():
                w.clear_overlays([cell.get("path")])

    # ── Another try at one day's frame ────────────────────────────────────────
    def _replace_cell_frame(self, cam: str, day, new_path: Path):
        """Put a different picture in one (camera, day) tile, on every wall showing it."""
        new_path = Path(new_path)
        for w in self._all_walls():
            touched = False
            for c in w._cells:
                if c.get("cam") == cam and c.get("day") == day:
                    c["path"] = new_path
                    c["ts_ns"] = extract_ns_from_stem(new_path.stem)
                    c["meta"] = dict(c.get("meta") or {}, source="manual")
                    touched = True
            if touched:
                w._pix.clear()
                w._load_gen += 1
                w._kick_load(w._load_gen)
                w._relayout()
                w.update()
        self._rebuild_baseline_combo(self._wall.cells())

    def _wall_search_again(self, cell: dict):
        """Look for a different frame for this camera on this day, starting from an hour
        the user names. Carried over from the pop-up window, where it was the one thing
        the wall could not do."""
        from PySide6.QtWidgets import QInputDialog
        cam_folder = cell.get("cam_folder") or cell.get("cam", "")
        day = cell.get("day")
        if not cam_folder or day is None:
            return
        self._try_hour = getattr(self, "_try_hour", {})
        key = (cam_folder, day)
        cur_h = None
        ts = cell.get("ts_ns")
        if ts:
            try:
                cur_h = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)\
                                .astimezone(PRAGUE).hour
            except Exception:
                cur_h = None
        last_tried = self._try_hour.get(key, cur_h if cur_h is not None else -1)
        default_h = max(0, last_tried + 1) if last_tried < 23 else 0
        cur_str = f"{cur_h:02d}:00" if cur_h is not None else "unknown"
        chosen_hour, ok = QInputDialog.getInt(
            self, "Search again",
            f"{cell.get('cam', '')}  {day.strftime('%d.%m.%Y')}\n"
            f"Currently showing: {cur_str}  |  Last tried: {last_tried:02d}:00\n"
            f"Search from hour:",
            default_h, 0, 23)
        if not ok:
            return
        self._try_hour[key] = chosen_hour
        self._run_search_again(cell, cam_folder, day, chosen_hour)

    def _sa_candidates(self, cam_folder: str, day, real_h: int,
                       exclude: "set[str]", log) -> list:
        """Ranked candidate frames for one (camera, day, Prague hour): the energy-anchored
        picks first, then the file-size heuristic."""
        if PRAGUE is not None:
            dt_p = datetime(day.year, day.month, day.day, real_h, tzinfo=PRAGUE)
            dt_eff = dt_p.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            dt_eff = datetime(day.year, day.month, day.day, real_h)
        folder = self._build_target_path(dt_eff) / cam_folder
        try:
            if not folder.exists():
                return []
        except Exception:
            return []
        out, seen = [], set(exclude)

        def _add(paths):
            for p in paths or []:
                sp = str(p)
                if sp not in seen:
                    seen.add(sp)
                    out.append(p)

        try:
            start_ns = int(datetime(dt_eff.year, dt_eff.month, dt_eff.day, dt_eff.hour,
                                    tzinfo=timezone.utc).timestamp() * 1_000_000_000)
            _add(self._select_by_totalpower(folder, 3, _cam_totalpower_channel(cam_folder),
                                            log, start_ns,
                                            start_ns + 3_600_000_000_000 - 1))
        except Exception as e:
            log(f"  energy anchor failed ({type(e).__name__}: {e}) — size fallback")
        try:
            _add(self.select_images_from_folder(folder, 3))
        except Exception:
            pass
        return out

    def _run_search_again(self, cell: dict, cam_folder: str, day, chosen_hour: int):
        prog = QDialog(self)
        prog.setWindowTitle("Searching…")
        prog.setMinimumWidth(400)
        pv = QVBoxLayout(prog)
        plbl = QLabel(f"{cell.get('cam','')}  {day.strftime('%d.%m.%Y')}")
        plbl.setWordWrap(True)
        hour_lbl = QLabel()
        hour_lbl.setStyleSheet("font-size:10px; color:#555;")
        btn_cancel = QPushButton("Cancel")
        pv.addWidget(plbl); pv.addWidget(hour_lbl)
        pv.addWidget(btn_cancel, 0, Qt.AlignmentFlag.AlignRight)

        cancel_ev = threading.Event()
        btn_cancel.clicked.connect(cancel_ev.set)
        prog.rejected.connect(cancel_ev.set)

        self._try_sig = _TryAgainSignals()
        sig = self._try_sig
        sig.progress.connect(lambda _m, h: hour_lbl.setText(h))

        def _on_cell(_cam, _date, real_h, path, line):
            if path:
                self._try_hour[(cam_folder, day)] = real_h
                self._replace_cell_frame(cell.get("cam", ""), day, Path(path))
            self._log(f"SEARCH AGAIN: {line}")

        def _on_finished(summary):
            try:
                prog.accept()
            except Exception:
                pass
            if summary:
                QMessageBox.information(self, "Search again", "\n".join(summary))

        sig.cell_done.connect(_on_cell)
        sig.finished.connect(_on_finished)

        cur = cell.get("path")
        excl = {str(cur)} if cur else set()

        def worker():
            logs: list = []
            found_path = found_h = None
            tried = empty = 0
            for real_h in range(chosen_hour, 24):
                if cancel_ev.is_set():
                    break
                sig.progress.emit("", f"Scanning hour {real_h:02d}:00")
                for cand in self._sa_candidates(cam_folder, day, real_h, excl, logs.append):
                    if cancel_ev.is_set():
                        break
                    tried += 1
                    excl.add(str(cand))
                    if _image_is_nonempty(cand, log=logs.append):
                        found_path, found_h = cand, real_h
                        break
                    empty += 1
                if found_path is not None:
                    break
            if found_path is not None:
                line = (f"{cell.get('cam','')} {day.strftime('%d.%m')}: found "
                        f"{found_h:02d}:00 ({tried} candidate(s), {empty} empty)")
                sig.cell_done.emit(cam_folder, day, found_h, str(found_path), line)
            else:
                line = (f"{cell.get('cam','')} {day.strftime('%d.%m')}: no non-empty "
                        f"image from {chosen_hour:02d}:00 ({tried} candidate(s), "
                        f"{empty} empty)")
                sig.cell_done.emit(cam_folder, day, chosen_hour, None, line)
            sig.finished.emit([line])

        threading.Thread(target=worker, daemon=True).start()
        prog.exec()

    def _wall_pick_from_folder(self, cell: dict):
        """Browse for any picture and put it in this tile."""
        cam_folder = cell.get("cam_folder") or cell.get("cam", "")
        day = cell.get("day")
        cur = cell.get("path")
        start_dir = str(Path(cur).parent) if cur else ""
        if not start_dir and day is not None:
            for real_h in range(24):
                if PRAGUE is not None:
                    dt_eff = datetime(day.year, day.month, day.day, real_h,
                                      tzinfo=PRAGUE).astimezone(timezone.utc)\
                                     .replace(tzinfo=None)
                else:
                    dt_eff = datetime(day.year, day.month, day.day, real_h)
                f = self._build_target_path(dt_eff) / cam_folder
                if f.exists():
                    start_dir = str(f)
                    break
        fname, _ = QFileDialog.getOpenFileName(
            self, f"Pick image — {cell.get('cam','')}  "
                  f"{day.strftime('%d.%m.%Y') if day else ''}",
            start_dir, "Images (*.png *.tif *.tiff *.jpg *.jpeg *.bmp)")
        if not fname or not Path(fname).exists():
            return
        self._replace_cell_frame(cell.get("cam", ""), day, Path(fname))

    def _save_wall(self):
        img = self._wall.composite_image()
        if img is None:
            QMessageBox.information(self, "Save comparison", "Nothing on the wall yet.")
            return
        cells = self._wall.cells()
        cams = sorted({c.get("cam", "") for c in cells})
        default = f"compare_{'_'.join(cams)[:40]}_{len(cells)}days.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save comparison", default, "PNG image (*.png)")
        if not path:
            return
        try:
            writer = QImageWriter(path, b"png")
            # Provenance: the picture has to say which camera, which days and how the
            # frames were picked, or it is just an anonymous collage in a report.
            writer.setText("Camera", ", ".join(cams))
            writer.setText("Days", ", ".join(
                (c["day"].strftime("%Y-%m-%d") if hasattr(c["day"], "strftime")
                 else str(c["day"])) for c in cells))
            base = self._wall.baseline_idx()
            if base is not None and 0 <= base < len(cells):
                bd = cells[base]["day"]
                writer.setText("Reference day", bd.strftime("%Y-%m-%d")
                               if hasattr(bd, "strftime") else str(bd))
            writer.setText("Scale", "absolute, per-camera sensor range (img_scale)")
            if not writer.write(img):
                raise RuntimeError(writer.errorString())
            self._log(f"WALL: saved {path}")
        except Exception as e:
            QMessageBox.warning(self, "Save comparison", f"Could not save:\n{e}")

    def cancel_scan(self):
        """Stop whatever this tab has running. main.py's 'Stop All' has always called
        this — it just never existed, inside a bare except, so Stop All silently did
        nothing here."""
        self._load_gen += 1
        self._preview_gen = getattr(self, "_preview_gen", 0) + 1
        for w in (self._all_walls() if hasattr(self, "_wall_pages") else []):
            w._load_gen += 1
        if hasattr(self, "_wall"):
            self._wall._load_gen += 1
        self._log("STOP: cancelled pending scans.")

    # ── Inline preview panel ──────────────────────────────────────────────────
    def _preview_load_cam(self, cam: dict):
        """Enumerate one camera's folder in the background, then show its first
        image. Called from the picked-cameras list."""
        folder = cam.get("path")
        if folder is None:
            return
        cam_name = cam.get("label") or folder.name
        self._preview_from_view = False

        self._preview_gen += 1
        scan_gen = self._preview_gen

        def _scan():
            try:
                if not folder.exists():
                    return
                files = sorted(
                    [p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS],
                    key=lambda p: p.name)
            except Exception:
                files = []

            def _on_done():
                if getattr(self, "_preview_gen", 0) != scan_gen:
                    return
                if not files:
                    self._preview_counter.setText("0 / 0")
                    return
                self._preview_paths = files
                self._preview_cam_names = []
                self._preview_idx   = 0
                self._preview_cam   = cam_name
                self._preview_show()

            QTimer.singleShot(0, _on_done)

        threading.Thread(target=_scan, daemon=True).start()

    def _preview_set_files(self, files: list, cam_name: str = "",
                           cam_names: "list[str] | None" = None):
        """Set the preview to a specific file list (e.g. after View)."""
        if not files:
            return
        self._preview_paths    = list(files)
        # Per-file cam names: use provided list, else derive from parent folder name
        if cam_names and len(cam_names) == len(files):
            self._preview_cam_names = list(cam_names)
        else:
            self._preview_cam_names = [f.parent.name for f in files]
        self._preview_idx      = 0
        self._preview_cam      = cam_name
        self._preview_from_view = True
        self._preview_show()

    def _preview_prev(self):
        if not self._preview_paths:
            return
        self._preview_idx = (self._preview_idx - 1) % len(self._preview_paths)
        self._preview_show()

    def _preview_next(self):
        if not self._preview_paths:
            return
        self._preview_idx = (self._preview_idx + 1) % len(self._preview_paths)
        self._preview_show()

    def _on_preview_ready(self, pm: QPixmap, gen: int):
        """Slot called on main thread when background thread finishes loading."""
        if gen != self._preview_gen:
            return
        energy_text = getattr(self, "_preview_energy_text", "")
        if energy_text:
            pm = self._paint_pv_bar(pm, energy_text)
        self._preview_scale_lbl.setText(getattr(self, "_preview_scale_note", ""))
        # Park the greyed-out sliders on what the Auto passes actually applied to this
        # frame, so the number on screen is the number in the picture (same contract as
        # the Slider tab's Auto controls).
        self._park_auto_bc(getattr(self, "_bc_applied", None))
        lbl = self._preview_lbl
        avail_w = max(lbl.width(),  200)
        avail_h = max(lbl.height(), 200)
        pm = pm.scaled(avail_w, avail_h,
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        lbl.setPixmap(pm)

    @staticmethod
    def _paint_pv_bar(pm: QPixmap, energy_text: str) -> QPixmap:
        """Return a new pixmap = image + a white bar with centered black PV text.
        Adaptive font fit / 2-line split, ported from Shot Finder."""
        from PySide6.QtGui import QFontMetrics
        available_w = pm.width() - 20
        font = QFont()
        display_text = energy_text
        fitted = False
        for fsize in range(22, 8, -1):
            font.setPixelSize(fsize)
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(energy_text) <= available_w:
                fitted = True
                break
        if not fitted:
            parts_split = energy_text.split("  |  ")
            mid = len(parts_split) // 2
            display_text = ("  |  ".join(parts_split[:mid]) + "\n" +
                            "  |  ".join(parts_split[mid:]))
            for fsize in range(18, 8, -1):
                font.setPixelSize(fsize)
                fm = QFontMetrics(font)
                max_line = max(fm.horizontalAdvance(l) for l in display_text.split("\n"))
                if max_line <= available_w:
                    break
        fm = QFontMetrics(font)
        line_count = display_text.count("\n") + 1
        bar_h = max(38, fm.height() * line_count + 16)
        combined = QPixmap(pm.width(), pm.height() + bar_h)
        combined.fill(QColor(255, 255, 255))
        painter = QPainter(combined)
        painter.drawPixmap(0, 0, pm)
        bar_rect = QRect(0, pm.height(), pm.width(), bar_h)
        painter.fillRect(bar_rect, QColor(255, 255, 255))
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(bar_rect,
                         Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                         display_text)
        painter.end()
        return combined

    def _preview_show(self):
        if not self._preview_paths:
            return
        idx   = self._preview_idx
        total = len(self._preview_paths)
        path  = self._preview_paths[idx]
        self._preview_counter.setText(f"{idx + 1} / {total}")
        self._prev_btn.setEnabled(total > 1)
        self._next_btn.setEnabled(total > 1)
        # Use per-file cam name if available, fallback to folder name
        per_file_cam = (self._preview_cam_names[idx]
                        if self._preview_cam_names and idx < len(self._preview_cam_names)
                        else None)
        raw_cam = per_file_cam or self._preview_cam or path.parent.name
        self._preview_cam_lbl.setText(extract_display_label(raw_cam))
        ns = extract_ns_from_stem(path.stem)
        if ns is not None:
            try:
                dt_utc = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                try:
                    from zoneinfo import ZoneInfo as _ZI
                    dt_local = dt_utc.astimezone(_ZI("Europe/Prague"))
                except Exception:
                    dt_local = dt_utc
                ts_str = dt_local.strftime("%Y-%m-%d  %H:%M:%S.") + \
                         f"{dt_local.microsecond // 1000:03d}"
            except Exception:
                ts_str = path.stem
        else:
            ts_str = path.stem
        self._preview_ts_lbl.setText(ts_str)

        # PV overlay text for the bar painted in _on_preview_ready (main thread).
        self._preview_energy_text = ""
        # Cleared here so a failed load shows no note rather than the previous frame's.
        self._preview_scale_note = ""
        if (self._cb_pv_preview.isChecked() and self._pv_visible_cols()):
            entry = self._energy_entry_for_path(path)
            if entry is not None:
                parts = self._energy_parts_for_path(path, entry)
                self._preview_energy_text = "  |  ".join(parts)
        # The table reports on THIS frame, so it follows the preview.
        self._pv_refresh_table()

        self._preview_gen += 1
        gen = self._preview_gen
        sig = self._preview_sig
        grad_name = self._gradient_cb.currentText()
        # Read the widgets HERE: _load runs on a worker thread and touching a widget
        # from one is not safe.
        auto, gamma, contrast, offset = self._bc_args()

        def _load():
            try:
                img = PilImage.open(path)
                if img.mode in ("I", "I;16"):
                    arr = np.array(img, dtype=np.float32)
                else:
                    arr = np.array(img.convert("L"), dtype=np.float32)
                # The camera's reference range for a 16-bit frame, 255 for an 8-bit one —
                # see img_scale.full_scale_for_pil.
                full_scale = img_scale.full_scale_for_pil(path, img.info, img.mode)
                # `bc_out` comes back with what the Auto passes actually applied, so
                # the greyed-out sliders can be parked on it and Auto gamma costs no
                # second median pass over the frame.
                bc_out: dict = {}
                arr8 = _render_u8(arr, auto, full_scale, gamma, contrast, offset, bc_out)
                self._bc_applied = bc_out
                g_applied = (bc_out.get("gamma")
                             if (not auto and img_scale.is_auto_gamma(gamma)) else None)
                self._preview_scale_note = _scale_note(img.info, arr, auto, full_scale,
                                                       gamma, path, img.mode,
                                                       contrast, offset, g_applied)
                lut = GRADIENTS.get(grad_name)
                if lut is not None:
                    pil_img = PilImage.fromarray(
                        _lut_pixels(lut, arr8, grad_name).astype(np.uint8), mode="RGB")
                else:
                    pil_img = PilImage.fromarray(arr8, "L").convert("RGB")
                raw = bytes(pil_img.tobytes("raw", "RGB"))
                w2, h2 = pil_img.size
                qimg = QImage(raw, w2, h2, w2 * 3, QImage.Format.Format_RGB888)
                pm = QPixmap.fromImage(qimg)
                # Emit Signal — guaranteed delivery on main thread
                sig.ready.emit(pm, gen)
            except Exception as _e:
                import traceback as _tb
                self._log_safe(f"PREVIEW ERROR: {_e}\n{_tb.format_exc()}")

        threading.Thread(target=_load, daemon=True).start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._preview_paths:
            QTimer.singleShot(50, self._preview_show)

    def _capture_selection_state(self) -> dict[str, int]:
        """Save the picked cameras as {folder_name: qty} so a re-scan (another day,
        another hour) comes back with the same cameras picked."""
        return {c["name"]: int(c.get("qty", 1)) for c in self._cams if c.get("checked")}

    def _refresh_selected_table(self):
        """Fill the short list of picked cameras under the Cameras… button."""
        self._sel_table.setRowCount(0)
        rows = [(c.get("num", ""), c.get("label") or c["name"], c["name"])
                for c in self._picked_cams()]
        rows.sort(key=lambda t: (t[0].lower(), t[1].lower(), t[2].lower()))
        for num, label, name in rows:
            row = self._sel_table.rowCount(); self._sel_table.insertRow(row)
            self._sel_table.setItem(row, 0, QTableWidgetItem(num))
            it = QTableWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, name)
            self._sel_table.setItem(row, 1, it)
        self._sync_cameras_button()

    def _sel_table_name(self, row: int) -> str:
        it = self._sel_table.item(row, 1)
        return "" if it is None else (it.data(Qt.ItemDataRole.UserRole) or "")

    def _on_sel_table_clicked(self, index):
        """Single click in the picked list → preview that camera's first frame."""
        cam = self._cam_by_name(self._sel_table_name(index.row()))
        if cam is not None:
            self._preview_load_cam(cam)

    def _on_sel_table_double_clicked(self, index):
        """Double-click in the picked list → unpick that camera."""
        cam = self._cam_by_name(self._sel_table_name(index.row()))
        if cam is not None:
            cam["checked"] = False
            self._refresh_selected_table()

    # ── EVENTS ────────────────────────────────────────────────────────────────
    @staticmethod
    def _date_range(d1: QDate, d2: QDate) -> "list[QDate]":
        if d2 < d1:
            d1, d2 = d2, d1
        days, d = [], d1
        while d <= d2:
            days.append(d)
            d = d.addDays(1)
        return days

    def _apply_day_selection(self, dates: "list[QDate]", schedule: bool = True):
        """Update the calendar's multi-day selection, repaint, set the primary
        day (last clicked) and — when schedule=True — reload the camera table."""
        dates = sorted(dates, key=lambda x: (x.year(), x.month(), x.day()))
        self._selected_days = dates
        delegate = getattr(self._cal, "_wk_delegate", None)
        if delegate is not None:
            delegate.set_selected(dates)
            # Outline the primary day — the one the hour picker and the single-day
            # search work on — so a multi-day selection still says which day leads.
            delegate.set_focus_date(dates[-1] if dates else None)
        if dates:
            self._cal.blockSignals(True)
            self._cal.setSelectedDate(dates[-1])
            self._cal.blockSignals(False)
        self._sync_time_summary()
        if schedule and dates:
            self._user_has_selected_day = True
            self._status_dot.setStyleSheet("color: gray; font-size: 12px;")
            # Pick a sensible hour for the primary day (does not reload the table).
            self._apply_auto_hour_for_selected_day()
            # Reload the camera table as the union over all effective days.
            self._schedule_autoload(150)

    def _effective_days(self) -> "list[QDate]":
        """The days actually used (camera-table union + View) — the explicit
        selection. Weekends only appear if the user added them by hand."""
        return list(self._selected_days)

    @staticmethod
    def _qkey(d: QDate) -> tuple:
        return (d.year(), d.month(), d.day())

    @staticmethod
    def _is_weekend(d: QDate) -> bool:
        return d.dayOfWeek() >= 6   # 6=Sat, 7=Sun

    def _on_calendar_clicked(self, d: QDate):
        """Selection logic mirrored from Spectra's DatePickerDialog:
          plain click      → exactly that one day (replaces the selection)
          Ctrl+click       → toggle one weekday in/out (weekends plain-click only)
          Ctrl+Shift+click → range from last click, XOR-ed into the selection;
                             gated to the weekdays checked below (default Mon–Fri).
        """
        mods  = QApplication.keyboardModifiers()
        ctrl  = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        cur      = list(self._selected_days)
        cur_keys = {self._qkey(x) for x in cur}

        if ctrl and shift:
            anchor  = self._last_cal_click or d
            allowed = {i for i, cb in enumerate(self._wd_checks) if cb.isChecked()}
            rng     = [x for x in self._date_range(anchor, d)
                       if (x.dayOfWeek() - 1) in allowed]
            # XOR the gated range into the selection (so a repeat deselects what
            # it selected) — but the anchor (the first click) is NEVER touched:
            # click Mon then Ctrl+Shift Fri keeps Mon and adds Tue–Fri.
            result = {self._qkey(x): x for x in cur}
            akey   = self._qkey(anchor)
            for x in rng:
                k = self._qkey(x)
                if k == akey:
                    continue            # anchor is handled below — never toggled off
                if k in result:
                    del result[k]       # was selected → toggle off
                else:
                    result[k] = x       # was not selected → toggle on
            result[akey] = anchor       # anchor always stays selected
            new_dates = list(result.values())
        elif ctrl:
            if self._is_weekend(d):
                self._log("Calendar: weekends can only be selected by a plain click.")
                return
            if self._qkey(d) in cur_keys:
                new_dates = [x for x in cur if self._qkey(x) != self._qkey(d)]
            else:
                new_dates = cur + [d]
        else:
            new_dates = [d]   # plain click — exactly one day (weekends allowed)

        self._last_cal_click = d
        self._apply_day_selection(sorted(new_dates, key=self._qkey))
        self._log(f"Calendar: {len(new_dates)} day(s) selected")

    def _auto_select_today(self):
        """Called once after startup — simulate selecting today's date."""
        self._user_has_selected_day = True
        qd = self._cal.selectedDate()
        self._log(f"Auto-selecting today: {qd.day():02d}.{qd.month():02d}.{qd.year()}")
        self._apply_auto_hour_for_selected_day()
        self._schedule_autoload(150)

    def _on_hour_change(self):
        # No auto-load: the camera table lists cameras across all hours, so the
        # hour selector does not change it. Search starts only from View.
        self._log_selected_datetime_preview()

    def _on_labtime_toggle(self):
        self._log(f"Lab time toggled -> {self._lab_time_cb.isChecked()}")
        self._auto_hour_last_day = None
        if self._user_has_selected_day:
            self._apply_auto_hour_for_selected_day()
        else:
            self._log_selected_datetime_preview()

    def _on_gradient_changed(self, name: str):
        # The directory-listing cache used to be cleared here too. A palette has
        # nothing to do with which files are in a folder, and throwing the listing
        # away cost a re-read of the share for nothing.
        self._log(f"GRADIENT -> {name}")
        self._sync_wall_display()
        if self._preview_paths:
            self._preview_show()

    def _sync_bc_enabled(self):
        """Enable/disable the Contrast, Brightness and Gamma controls. Every
        enable/disable of them goes through here, so the rules cannot overwrite
        each other:
          - Auto on → that row's own slider and reset button are greyed out (the
            app-wide 'checkbox beats slider' rule).
          - Either Auto contrast or Auto brightness on → the whole Gamma row goes dead.
            Both of them set the frame's two ends themselves, so gamma has nothing left
            to bend and the render drops it (see img_scale.to_u8). The Slider tab keeps
            its gamma row alive under Auto brightness because its subtraction mode still
            uses it; this tab has no such mode, so a live-but-ignored slider would just
            be a lie.
        The name labels and the numeric readouts stay live either way: while Auto is on,
        the readout is exactly what the user wants to see — the value Auto picked, put
        there by _park_auto_bc."""
        c_live = not self._cb_auto_stretch.isChecked()
        b_live = not self._cb_bright_auto.isChecked()
        self._contrast_slider.setEnabled(c_live)
        self._btn_contrast_reset.setEnabled(c_live)
        self._bright_slider.setEnabled(b_live)
        self._btn_bright_reset.setEnabled(b_live)
        gamma_usable = c_live and b_live
        g_live = gamma_usable and not self._cb_gamma_auto.isChecked()
        self._cb_gamma_auto.setEnabled(gamma_usable)
        self._lbl_gamma.setEnabled(gamma_usable)
        self._lbl_gamma_val.setEnabled(gamma_usable)
        self._gamma_slider.setEnabled(g_live)
        self._btn_gamma_reset.setEnabled(g_live)

    def _bc_args(self):
        """(auto, gamma, contrast, offset) for the render — read on the MAIN thread and
        passed into the workers; never read a widget from one.

        Honours the 'Auto checkbox overrides its own slider' rule of each pair, exactly
        as the Slider tab does: Auto contrast zeroes the manual contrast, Auto brightness
        zeroes the manual offset, and each leaves the other one live. Both Autos are the
        same percentile pass (see img_scale.stretch_u8), so `auto` is set by either and
        ticking both does not level the frame twice."""
        auto_c = self._cb_auto_stretch.isChecked()
        auto_b = self._cb_bright_auto.isChecked()
        auto = auto_c or auto_b
        if auto:
            gamma = img_scale.GAMMA_SLIDER_NEUTRAL
        elif self._cb_gamma_auto.isChecked():
            gamma = img_scale.GAMMA_SLIDER_AUTO
        else:
            gamma = int(self._gamma_slider.value())
        contrast = 0 if auto_c else int(self._contrast_slider.value())
        offset = 0 if auto_b else int(self._bright_slider.value())
        return auto, gamma, contrast, offset

    def _sync_bc_value_labels(self):
        """Write the three numeric readouts from the sliders themselves.

        The sliders are the single source of truth for what is on screen — Auto
        included, because the Auto pass parks its own value there (_park_auto_bc). That
        parking blocks signals, so the handlers do not run and this is called instead."""
        self._lbl_contrast_val.setText(str(int(self._contrast_slider.value())))
        self._lbl_bright_val.setText(str(int(self._bright_slider.value())))
        self._lbl_gamma_val.setText(
            f"{img_scale.gamma_from_slider(int(self._gamma_slider.value())):.2f}")

    def _park_auto_bc(self, applied: "dict | None"):
        """Park each greyed-out slider on the value its Auto pass actually applied to the
        previewed frame, instead of leaving it at 0 while the picture is clearly changed.
        Signals are blocked: no reload.

        DISPLAY ONLY. Unticking an Auto box restores the user's own value instead of
        keeping what was parked (see the toggle handlers) — an Auto checkbox has to be
        undoable, and the parked number is Auto's, not the user's."""
        if not applied:
            return
        c = applied.get("contrast")
        if c is not None and self._cb_auto_stretch.isChecked():
            self._contrast_slider.blockSignals(True)
            self._contrast_slider.setValue(int(c))
            self._contrast_slider.blockSignals(False)
        o = applied.get("offset")
        if o is not None and self._cb_bright_auto.isChecked():
            self._bright_slider.blockSignals(True)
            self._bright_slider.setValue(int(o))
            self._bright_slider.blockSignals(False)
        g = applied.get("gamma")
        if g is not None and self._cb_gamma_auto.isChecked():
            self._gamma_slider.blockSignals(True)
            self._gamma_slider.setValue(img_scale.slider_from_gamma(float(g)))
            self._gamma_slider.blockSignals(False)
        self._sync_bc_value_labels()

    def _bc_reshow_preview(self):
        # The one funnel for every Contrast / Brightness / Gamma change — the three
        # sliders debounce into it and all three Auto boxes call it — so the wall is
        # kept in step from here rather than from six separate handlers.
        self._sync_wall_display()
        if self._preview_paths:
            self._preview_show()

    def _on_contrast_slider_changed(self, value: int):
        # Only user moves reach this (_park_auto_bc blocks signals), so this is the value
        # to come back to when Auto is switched off.
        self._contrast_manual = int(value)
        self._lbl_contrast_val.setText(str(int(value)))
        self._bc_debounce.start()

    def _reset_contrast_slider(self):
        self._contrast_slider.setValue(0)

    def _on_auto_stretch_toggled(self, on: bool):
        self._log(f"CONTRAST -> {'auto stretch (per frame)' if on else 'absolute'}")
        self._sync_bc_enabled()
        if not on:
            # Auto off → the slider is live again, so it must not be left on the number
            # Auto parked there: the stretch of a dim frame needs a gain the slider
            # cannot reach, so the parked value saturates and does not reproduce the
            # picture.
            self._contrast_slider.blockSignals(True)
            self._contrast_slider.setValue(int(self._contrast_manual))
            self._contrast_slider.blockSignals(False)
        self._sync_bc_value_labels()
        self._bc_reshow_preview()

    def _on_bright_slider_changed(self, value: int):
        self._bright_manual = int(value)
        self._lbl_bright_val.setText(str(int(value)))
        self._bc_debounce.start()

    def _reset_bright_slider(self):
        self._bright_slider.setValue(0)

    def _on_bright_auto_toggled(self, on: bool):
        self._log(f"BRIGHTNESS -> {'auto level (per frame)' if on else 'manual'}")
        self._sync_bc_enabled()
        if not on:
            # Auto off → back to the user's own offset, not the black level Auto parked
            # on the greyed-out slider. Same rule as contrast: an Auto checkbox has to be
            # undoable.
            self._bright_slider.blockSignals(True)
            self._bright_slider.setValue(int(self._bright_manual))
            self._bright_slider.blockSignals(False)
        self._sync_bc_value_labels()
        self._bc_reshow_preview()

    def _on_gamma_slider_changed(self, value: int):
        self._gamma_manual = int(value)
        self._lbl_gamma_val.setText(f"{img_scale.gamma_from_slider(value):.2f}")
        self._bc_debounce.start()

    def _reset_gamma_slider(self):
        self._gamma_slider.setValue(img_scale.GAMMA_SLIDER_NEUTRAL)

    def _on_gamma_auto_toggled(self, on: bool):
        self._log(f"GAMMA -> {'auto (per frame)' if on else 'manual'}")
        self._sync_bc_enabled()
        if not on:
            # Auto off → the user's own value, not the one Auto parked on the greyed-out
            # slider. An Auto checkbox has to be undoable.
            self._gamma_slider.blockSignals(True)
            self._gamma_slider.setValue(int(self._gamma_manual))
            self._gamma_slider.blockSignals(False)
        self._sync_bc_value_labels()
        self._bc_reshow_preview()

    # ── ENERGY CSV METHODS ────────────────────────────────────────────────────

    def _pick_energy_columns(self):
        """The Image Slider's PV picker — one dialog, one registry, for both tabs.

        What comes back is split in two: the REGISTRY (added PVs, formulas, names,
        units) is shared and goes to the shared store, while the selection and the eye
        state belong to this tab alone."""
        sl = _get_slider_module()
        dlg = sl.PvConfigDialog(self._energy_selected_cols, sl.PV_CUSTOM_CHANNELS,
                               sl.PV_DERIVED, sl.PV_LABELS,
                               hidden=self._energy_hidden_pvs,
                               units=sl.PV_CUSTOM_UNITS,
                               # The alarm limits are part of the shared registry, so
                               # they have to be handed in and taken back out here too.
                               # Opening this picker with them missing would show empty
                               # Min/Max boxes for PVs that DO have a limit, and wipe
                               # them on OK.
                               limits=sl.PV_LIMITS, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sl.PV_CUSTOM_CHANNELS.clear()
        sl.PV_CUSTOM_CHANNELS.update(dlg.custom_channels())
        sl.PV_DERIVED[:] = dlg.derived_defs()
        sl.PV_LABELS.clear()
        sl.PV_LABELS.update(dlg.labels())
        sl.PV_CUSTOM_UNITS.clear()
        sl.PV_CUSTOM_UNITS.update(dlg.custom_units())
        sl.PV_LIMITS.clear()
        sl.PV_LIMITS.update(dlg.limits())
        sl.pv_registry_save()
        self._energy_selected_cols = dlg.selected_names()
        self._energy_hidden_pvs = (set(dlg.hidden_names())
                                   & set(self._energy_selected_cols))
        self._save_pv_state()
        self._log(f"PVs: {self._energy_selected_cols}")
        # The cached days were fetched for the PREVIOUS list, so a PV added just now
        # would read "n/a" until the day happened to be re-fetched for another reason.
        self._energy_cache.clear()
        self._energy_per_col_cache.clear()
        if hasattr(self, "_energy_cache_time"):
            self._energy_cache_time.clear()
        if hasattr(self, "_energy_error_days"):
            self._energy_error_days.clear()
        self._pv_refresh_table()
        if self._energy_results:
            self._refresh_energy_info()

    # ── the PV list: selection, eye, saved state ──────────────────────────
    def _pv_visible_cols(self) -> "list[str]":
        """The picked PVs whose value is PRINTED — in the info panel, in the bar under
        the preview and in a burned-in image. Everything picked is still read; the eye
        only decides what is shown, exactly as in the Slider."""
        return [c for c in self._energy_selected_cols
                if c not in self._energy_hidden_pvs]

    def _pv_fetch_cols(self) -> "list[str]":
        """What has to be READ for the picked list: every picked PV that is a channel,
        plus the sources of every picked formula — a formula built on a PV that is not
        itself picked would otherwise read n/a."""
        sl = _get_slider_module()
        out = [c for c in self._energy_selected_cols if not sl.pv_is_derived(c)]
        for src in sl.pv_source_names(list(self._energy_selected_cols)):
            if src not in out:
                out.append(src)
        return out

    def _pv_toggle_eye(self, name: str):
        """Eye column of the PV table: take this PV off the picture (or put it back).
        It keeps being read and stays listed either way."""
        if name not in self._energy_selected_cols:
            return
        if name in self._energy_hidden_pvs:
            self._energy_hidden_pvs.discard(name)
        else:
            self._energy_hidden_pvs.add(name)
        self._save_pv_state()
        self._pv_refresh_table()
        # The bar under the preview follows the eye, so the frame has to be repainted.
        if self._preview_paths:
            self._preview_show()

    def _current_preview_path(self) -> "Path | None":
        """The frame the preview is showing, or None."""
        paths = getattr(self, "_preview_paths", None) or []
        idx = getattr(self, "_preview_idx", 0)
        return paths[idx] if 0 <= idx < len(paths) else None

    def _pv_refresh_table(self):
        """Repaint the PV table for the frame currently previewed."""
        tbl = getattr(self, "_pv_table", None)
        if tbl is None:
            return
        has = bool(self._energy_selected_cols)
        tbl.setVisible(has)
        self._pv_no_pv_lbl.setVisible(not has)
        if not has:
            return
        vals: dict = {}
        path = self._current_preview_path()
        entry = self._energy_entry_for_path(path) if path is not None else None
        if entry is not None:
            img_ns = extract_ns_from_stem(path.stem) or 0
            per_col = entry[6] if len(entry) > 6 else {}
            match = entry[1] if len(entry) > 1 else None
            for col, raw, state in self._pv_values_for_ns(
                    img_ns, self._energy_selected_cols, per_col, match=match,
                    allow_network=False):
                vals[col] = self._format_pv_state(col, raw, state)

        def _value_of(name: str):
            txt = vals.get(name)
            if txt is None:
                # No frame previewed yet, or this PV was not resolved for it — say
                # "not read yet" in grey rather than printing a bare dash that reads
                # like a PV with no data.
                return "…", True, ("No frame selected yet — pick a result row."
                                   if entry is None else
                                   "Not resolved for this frame.")
            grey = txt in (cpva.PV_TEXT_NOT_FOUND, cpva.PV_TEXT_ERROR, "—")
            tip = ""
            if txt == cpva.PV_TEXT_ERROR:
                tip = ("The archiver fetch failed for this PV. It is retried on the "
                       "next lookup.")
            elif txt == cpva.PV_TEXT_NOT_FOUND:
                tip = ("Nothing was archived near this frame's time for this PV "
                       "(the day loaded fine).")
            return txt, grey, tip

        tbl.refresh(self._energy_selected_cols, self._energy_hidden_pvs, _value_of)

    _PV_STATE_PATH = (Path(os.environ.get("APPDATA", Path.home()))
                      / "ELI_ImageTools" / "finder_ui_state.json")

    def _load_pv_state(self):
        """This tab's own selection. The PVs themselves come from the shared registry
        (see is_t.pv_registry_load) — what is stored here is only WHICH of them this
        tab shows, and which of those are off the picture."""
        sl = _get_slider_module()
        sl.pv_registry_load()
        try:
            data = json.loads(self._PV_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        hid = data.get("pv_hidden")
        if isinstance(hid, list):
            self._energy_hidden_pvs = {str(n) for n in hid}
        sel = data.get("pv_selected")
        if isinstance(sel, list):
            # A recipe preset saved by an older version (the "Compressed SBW4"
            # channel) becomes its source PV plus the formula that converts it. Before
            # the filter: the recipe name is no longer a channel, so `known` would drop
            # it first. The eye set is handed in because the recipe takes its source
            # off the picture.
            sel = sl.pv_migrate_preset_recipes([str(n) for n in sel],
                                               self._energy_hidden_pvs)
            # Iterating the SAVED list keeps the operator's order; the filter drops a
            # PV that no longer exists, so a removed channel cannot come back.
            known = set(sl.pv_all_names()) | set(ENERGY_COLUMNS_AVAILABLE)
            self._energy_selected_cols = [n for n in sel if n in known]
        self._energy_hidden_pvs &= set(self._energy_selected_cols)

    def _read_ui_state_file(self) -> dict:
        try:
            data = json.loads(self._PV_STATE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_ui_state_file(self, updates: dict):
        """Merge into the state file rather than replacing it. The PV selection and the
        open/closed panel groups share one document, so a writer that rewrites the whole
        thing silently throws away the other one's half."""
        data = self._read_ui_state_file()
        data.update(updates)
        try:
            self._PV_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._PV_STATE_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load_ui_state(self) -> dict:
        """Which panel groups were left open. Read before the panel is built, so it must
        not touch any widget."""
        return self._read_ui_state_file()

    def _on_section_toggled(self, key: str, expanded: bool):
        self._ui_state[f"sec_{key}"] = bool(expanded)
        self._write_ui_state_file({f"sec_{key}": bool(expanded)})

    def _set_all_sections(self, expanded: bool):
        """Expand all / Collapse all — one write for the whole panel."""
        upd = {}
        for key, sec in getattr(self, "_sections", {}).items():
            sec.set_expanded(expanded)
            self._ui_state[f"sec_{key}"] = bool(expanded)
            upd[f"sec_{key}"] = bool(expanded)
        self._write_ui_state_file(upd)

    def _save_pv_state(self):
        self._write_ui_state_file({"pv_selected": list(self._energy_selected_cols),
                                   "pv_hidden": sorted(self._energy_hidden_pvs)})

    def _get_energy_rows_for_dt(self, dt: datetime) -> list[_EnergyRow]:
        """
        Return cached energy rows for the date of dt.
        Tries CPVA API first; falls back to CSV. Called from background thread.
        Also populates self._energy_per_col_cache for per-column closest-timestamp lookups.
        """
        day_key = dt.strftime("%Y-%m-%d")
        today_key = datetime.now(PRAGUE).strftime("%Y-%m-%d") if PRAGUE else \
            datetime.utcnow().strftime("%Y-%m-%d")
        if not hasattr(self, "_energy_cache_time"):
            self._energy_cache_time = {}     # day_key → time.monotonic() of fetch
        if not hasattr(self, "_energy_error_days"):
            self._energy_error_days = set()  # day_keys whose last fetch had a failure
        if day_key in self._energy_cache and day_key not in self._energy_error_days:
            # Past days are immutable; TODAY keeps filling in — refetch after a
            # short TTL (cheap: the shared client's day cache does the real work,
            # only today's channels are actually re-hit).
            if day_key != today_key:
                return self._energy_cache[day_key]
            age = time.monotonic() - self._energy_cache_time.get(day_key, 0.0)
            if age < 30.0:
                return self._energy_cache[day_key]
        # Read the sources of the picked formulas too — see _pv_fetch_cols.
        cols = self._pv_fetch_cols()
        if cols:
            rows, per_col, had_error = _energy_api_for_day(dt, cols, log=self._log_safe)
            if had_error:
                # A fetch FAILED (≠ "day has no data"). Show what we got, but mark
                # the day for retry — caching the gap silently meant one outage
                # showed "—" for the whole session. Retry only re-hits the failed
                # channel (successful ones are served from the shared day cache).
                self._energy_error_days.add(day_key)
                self._log_safe(f"ENERGY: API fetch FAILED for {day_key} — will retry on next lookup")
            else:
                self._energy_error_days.discard(day_key)
            if rows:
                self._log_safe(f"ENERGY: {len(rows)} rows via API for {day_key}")
                self._energy_cache[day_key] = rows
                self._energy_per_col_cache[day_key] = per_col
                self._energy_cache_time[day_key] = time.monotonic()
                return rows
            if had_error:
                return self._energy_cache.get(day_key, [])
        # CSV fallback
        csv_path = _energy_csv_path(dt)
        rows = _load_energy_csv(csv_path)
        self._energy_cache[day_key] = rows
        self._energy_cache_time[day_key] = time.monotonic()
        # Build the per-column closest-timestamp lookup from the CSV rows so that
        # _find_closest_per_col_value works identically for CSV and API data.
        self._energy_per_col_cache[day_key] = _build_per_col_from_rows(rows)
        if rows:
            self._log_safe(f"ENERGY: {len(rows)} rows from CSV {csv_path.name}")
        else:
            self._log_safe(f"ENERGY: no data (API + CSV) for {day_key}")
        return rows

    def _lookup_energy_for_files(self, files: list[Path]) -> list[tuple]:
        """
        For each file, look up a matching CSV row.
        Returns list of (path, match, before, after, csv_rows, match_idx, per_col) tuples.
        csv_rows: full list of _EnergyRow for that day
        match_idx: index in csv_rows of the matched row (or nearest before), or None
        per_col: dict[str, list[_EnergyRow]] mapping each column to sorted per-column rows
        """
        results = []
        for path in files:
            ns = extract_ns_from_stem(path.stem)
            if ns is None:
                results.append((path, None, None, None, [], None, {}))
                continue
            dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
            if PRAGUE:
                dt = dt.astimezone(PRAGUE).replace(tzinfo=None)
            else:
                dt = dt.replace(tzinfo=None)
            rows = self._get_energy_rows_for_dt(dt)
            day_key = dt.strftime("%Y-%m-%d")
            per_col = self._energy_per_col_cache.get(day_key, {})
            match, before, after = _find_energy_match(rows, ns)

            # Find match_idx — index of matched row, or index of 'before' row
            match_idx = None
            if match is not None and rows:
                try:
                    match_idx = rows.index(match)
                except ValueError:
                    pass
            elif before is not None and rows:
                try:
                    match_idx = rows.index(before)
                except ValueError:
                    pass

            # Warm the slow-PV look-back cache HERE (background thread) so the
            # UI-thread display path (allow_network=False) gets cache hits for
            # waveplate-like channels whose last sample is hours/days old.
            try:
                self._pv_values_for_ns(ns, self._energy_selected_cols, per_col,
                                       match=match, allow_network=True)
            except Exception:
                pass

            results.append((path, match, before, after, rows, match_idx, per_col))
        return results

    def _pv_values_for_ns(self, img_ns: int, cols: "list[str]",
                          per_col: dict, match=None,
                          allow_network: bool = False
                          ) -> "list[tuple[str, str, str]]":
        """
        THE single matching implementation: resolve each column's value at the
        image timestamp. Returns [(col, raw, state)] with state:
          "ok"        — per-column sample within tolerance (or merged-row /
                        slow-PV look-back hit); raw is a valid value string
          "error"     — archiver fetch failed (display "ERR")
          "not_found" — data loaded fine, nothing matches (display "n/a")

        Per-column nearest match first (30 s API / 120 s CSV window), then the
        merged row, then — for slow PVs only (waveplate: archived on-change, so
        the last sample can be days old) — the archiver's last-at-or-before
        look-back. allow_network=False (UI thread) still serves look-back
        cache hits.
        """
        sl = _get_slider_module()
        want = list(cols)
        # A formula is COMPUTED, so it is never looked up here — but its sources are,
        # whether or not they are picked themselves.
        read_names = [c for c in want if not sl.pv_is_derived(c)]
        for src in sl.pv_source_names(want):
            if src not in read_names:
                read_names.append(src)
        resolved: dict = {}
        for col in read_names:
            # API rows are per-shot → tight window; sparse CSV keeps the wide one.
            tol = (ENERGY_MATCH_TOL_API_S if per_col.get(f"_src:{col}") == "api"
                   else ENERGY_MATCH_TOL_S)
            raw_val = _find_closest_per_col_value(per_col, col, img_ns, tol_s=tol)
            if raw_val and raw_val != "—":
                resolved[col] = (raw_val, "ok")
                continue
            if match is not None:
                mv = match.values.get(col, "")
                if mv and mv != "—":
                    resolved[col] = (mv, "ok")
                    continue
            channel = _pv_channel_for(col)
            if channel and channel in cpva.FORWARD_CHANNELS:
                res = cpva.value_at_or_before(channel, int(img_ns),
                                              timeout=CPVA_HTTP_TIMEOUT,
                                              network_ok=allow_network)
                if res.value is not None:
                    resolved[col] = (str(res.value), "ok")
                    continue
                if res.status == "error":
                    resolved[col] = ("", "error")
                    continue
            resolved[col] = ("", "not_found")

        # Formulas, evaluated in definition order from the numbers just resolved —
        # the SAME evaluator the Slider uses (pv_eval_derived), so a formula cannot
        # mean one thing here and another there. It wants values with the registry's
        # own factor already applied.
        if any(sl.pv_is_derived(c) for c in want):
            raw_num: dict = {}
            statuses: dict = {}
            for _n, (_rw, _st) in resolved.items():
                try:
                    raw_num[_n] = float(_rw) * _pv_scale_for(_n)
                except (TypeError, ValueError):
                    raw_num[_n] = None
                statuses[_n] = _st
            for _n, (_val, _st) in sl.pv_eval_derived(want, raw_num, statuses).items():
                resolved[_n] = ("" if _val is None else f"{_val}", _st)

        # Answer in the order that was ASKED for, and only for what was asked: a
        # formula's sources were read as a means, not because the caller wants them
        # printed.
        return [(c, *resolved.get(c, ("", "not_found"))) for c in want]

    @staticmethod
    def _format_pv_state(col: str, raw: str, state: str) -> str:
        """Tri-state display: real value (incl. genuine 0) / "ERR" / "n/a"."""
        if state == "error":
            return cpva.PV_TEXT_ERROR
        if state != "ok" or raw == "":
            return cpva.PV_TEXT_NOT_FOUND
        return _format_energy_value(col, raw)

    def _energy_parts_for_path(self, path: Path, entry: tuple) -> list[str]:
        """
        Return ['<label>=<value>', ...] for the selected PV columns of one image.
        Delegates to _pv_values_for_ns — single source of truth for the info
        panel, the preview overlay and saves.
        """
        match    = entry[1] if len(entry) > 1 else None
        per_col  = entry[6] if len(entry) > 6 else {}
        img_ns   = extract_ns_from_stem(path.stem) or 0
        parts: list[str] = []
        for col, raw, state in self._pv_values_for_ns(
                img_ns, self._pv_visible_cols(), per_col, match=match,
                allow_network=False):
            val   = self._format_pv_state(col, raw, state)
            parts.append(f"{_pv_label_for(col)}={val}")
        return parts

    def _energy_entry_for_path(self, path: Path) -> "tuple | None":
        """Return the energy-results entry (path, match, …, per_col) for a file."""
        for entry in (self._energy_results or []):
            if entry and entry[0] == path:
                return entry
        return None

    def _on_pv_preview_toggle(self):
        """Re-render the current preview so the PV bar appears/disappears."""
        if self._preview_paths:
            self._preview_show()

    def _refresh_energy_info(self):
        """Reset navigation to first result and display it."""
        self._energy_nav_index = 0
        self._energy_csv_offset = 0
        self._energy_csv_anchor_idx = None
        self._energy_csv_anchor_rows = []
        self._refresh_energy_info_single()
        # PV values may now be available — repaint the preview bar.
        if self._preview_paths:
            self._preview_show()

    def _on_nav_mode_changed(self):
        """Reset CSV offset when switching navigation mode."""
        self._energy_csv_offset = 0
        self._energy_csv_anchor_idx = None
        self._energy_csv_anchor_rows = []
        self._refresh_energy_info_single()

    def _energy_nav_prev(self):
        if not self._energy_results:
            return
        if not self._cb_nav_images.isChecked():
            # Step-through-images mode (checkbox OFF = default)
            if self._energy_nav_index > 0:
                self._energy_nav_index -= 1
                self._energy_csv_offset = 0
                self._energy_csv_anchor_idx = None
                self._energy_csv_anchor_rows = []
        else:
            # CSV navigation mode — move through CSV rows around current image
            self._energy_csv_offset -= 1
        self._refresh_energy_info_single()

    def _energy_nav_next(self):
        if not self._energy_results:
            return
        if not self._cb_nav_images.isChecked():
            # Step-through-images mode (checkbox OFF = default)
            if self._energy_nav_index < len(self._energy_results) - 1:
                self._energy_nav_index += 1
                self._energy_csv_offset = 0
                self._energy_csv_anchor_idx = None
                self._energy_csv_anchor_rows = []
        else:
            # CSV navigation mode — move through CSV rows around current image
            self._energy_csv_offset += 1
        self._refresh_energy_info_single()

    def _refresh_energy_info_single(self):
        if not self._energy_results:
            self._energy_info.setPlainText("")
            self._energy_nav_lbl.setText("0 / 0")
            self._btn_energy_prev.setEnabled(False)
            self._btn_energy_next.setEnabled(False)
            return

        n = len(self._energy_results)
        idx = max(0, min(self._energy_nav_index, n - 1))
        self._energy_nav_index = idx

        entry = self._energy_results[idx]
        path  = entry[0]
        match = entry[1]
        before = entry[2]
        after  = entry[3]
        csv_rows: list = entry[4] if len(entry) > 4 else []
        match_idx = entry[5] if len(entry) > 5 else None
        per_col_for_entry: dict = entry[6] if len(entry) > 6 else {}

        # Set anchor when entering CSV mode or switching images
        if self._energy_csv_anchor_idx is None or self._energy_csv_anchor_rows is not csv_rows:
            self._energy_csv_anchor_idx = match_idx
            self._energy_csv_anchor_rows = csv_rows

        nav_images = not self._cb_nav_images.isChecked()  # checkbox OFF = step images
        csv_offset = self._energy_csv_offset
        base_idx   = self._energy_csv_anchor_idx

        # ── Navigation label ──────────────────────────────────────────────
        if nav_images or csv_offset == 0:
            self._energy_nav_lbl.setText(f"📷 {idx + 1} / {n}")
        elif csv_offset < 0:
            self._energy_nav_lbl.setText(f"◄ {abs(csv_offset)} before #{idx + 1}")
        else:
            self._energy_nav_lbl.setText(f"► {csv_offset} after #{idx + 1}")

        # ── Button enable/disable ─────────────────────────────────────────
        if nav_images:
            self._btn_energy_prev.setEnabled(idx > 0)
            self._btn_energy_next.setEnabled(idx < n - 1)
        else:
            can_prev = base_idx is None or (base_idx + csv_offset - 1) >= 0
            can_next = (base_idx is None or not csv_rows or
                        (base_idx + csv_offset + 1) < len(csv_rows))
            self._btn_energy_prev.setEnabled(can_prev)
            self._btn_energy_next.setEnabled(can_next)

        lines = []

        if not nav_images and csv_offset != 0 and base_idx is not None and csv_rows:
            # ── CSV browse mode — show a neighbouring CSV row (by time) ────
            target_csv_idx = base_idx + csv_offset
            if 0 <= target_csv_idx < len(csv_rows):
                row = csv_rows[target_csv_idx]
                parts = []
                for col in self._pv_visible_cols():
                    val   = _format_energy_value(col, row.values.get(col, "—"))
                    parts.append(f"{_pv_label_for(col)}={val}")
                ts = row.ts_dt.strftime("%H:%M:%S.%f")[:-3]
                lines.append(f"{ts}  (CSV row)\n  " + "  |  ".join(parts))
            else:
                lines.append("(no more CSV rows)")

        else:
            # ── Current image — PV values only ────────────────────────────
            parts = self._energy_parts_for_path(path, entry)
            lines.append("  |  ".join(parts) if parts else "(no PVs selected)")

        self._energy_info.setPlainText("\n\n".join(lines))

    # ── RAMPING CSV ───────────────────────────────────────────────────────────
    def _ensure_ramping_root(self):
        name = self._ramping_source
        for n, p in RAMPING_CANDIDATES:
            if n == name: self.RAMPING_ROOT = Path(p); break
        else:
            self.RAMPING_ROOT = Path(RAMPING_CANDIDATES[0][1])
        self._log(f"RAMPING_ROOT = {self.RAMPING_ROOT} (CSV mode)")

    def _parse_timestamp(self, s: str):
        if s is None: return None
        s = str(s).strip().strip("'").strip('"')
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
            "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
        ):
            try: return datetime.strptime(s, fmt)
            except: pass
        return None

    def _read_ramping_csv_rows(self, csv_path: Path):
        rows = []
        try: raw = csv_path.read_text(encoding="utf-8", errors="ignore")
        except:
            try: raw = csv_path.read_text(encoding="latin-1", errors="ignore")
            except: return rows
        if not raw.strip(): return rows
        try:
            dialect = csv.Sniffer().sniff(raw[:4096], delimiters=[",", ";", "\t"])
            delim = dialect.delimiter
        except: delim = "\t"
        reader = csv.DictReader(raw.splitlines(), delimiter=delim)
        if reader.fieldnames is None: return rows
        fieldmap = {fn.strip(): fn for fn in reader.fieldnames if fn is not None}
        need = ["Timestamp", "waveplate", "sbw4", "ptm1"]
        if not all(n in fieldmap for n in need): return rows
        has_campon = "CampOn" in fieldmap
        for r in reader:
            try:
                dt = self._parse_timestamp(r.get(fieldmap["Timestamp"]))
                if dt is None: continue
                # Snapped onto the 1000-count grid, not truncated — same rule as
                # _format_energy_value. Feeds the ramping-segment analysis, which
                # compares waveplate positions between rows.
                wp = int(cpva.quantize(cpva.CHANNEL_MAP.get("waveplate", "waveplate"),
                                       float(r.get(fieldmap["waveplate"])))[0])
                sb = float(r.get(fieldmap["sbw4"]))
                p1 = float(r.get(fieldmap["ptm1"]))
                campon = None
                if has_campon:
                    try: campon = int(float(r.get(fieldmap["CampOn"])))
                    except: pass
                rows.append((dt, wp, sb, p1, campon))
            except: continue
        return rows

    def _get_ramping_for_day_cached(self, day):
        key = day.isoformat()
        if key in self._ramping_cache: return self._ramping_cache[key]
        if not self.RAMPING_ROOT: self._ramping_cache[key] = []; return []

        # Strategy (same as original tkinter if.py):
        # 1) Try exact filename first (fast, no network listing)
        # 2) Fallback: glob("*.csv") and filter by date (slow but reliable)
        # All exists()/glob() calls run in a sub-thread with timeout to avoid hanging.

        stem = day.strftime("dataof%Y%b_%d")   # e.g. dataof2026Mar_10
        found = [None]

        def probe():
            # Step 1: try exact filename variants
            for candidate_name in (
                stem + ".csv",
                stem,
                stem.lower() + ".csv",
                stem.lower(),
            ):
                try:
                    p = self.RAMPING_ROOT / candidate_name
                    if p.exists():
                        found[0] = [p]
                        return
                except Exception:
                    continue

            # Step 2: fallback — glob all CSVs (original tkinter behaviour)
            try:
                csvs = list(self.RAMPING_ROOT.glob("*.csv"))
                if csvs:
                    found[0] = csvs
            except Exception:
                pass

        t = threading.Thread(target=probe, daemon=True)
        t.start()
        t.join(timeout=1.0)

        if found[0] is None:
            if t.is_alive():
                self._log_safe(f"RAMPING: timeout reaching {self.RAMPING_ROOT}")
            else:
                self._log_safe(f"RAMPING: no CSV files found in {self.RAMPING_ROOT}")
            self._ramping_cache[key] = []
            return []

        out = []
        for cp in found[0]:
            try:
                for dt, wp, sb, p1, campon in self._read_ramping_csv_rows(cp):
                    if dt.date() == day:
                        out.append((dt, wp, sb, p1, campon))
            except Exception:
                continue

        if not out:
            names = [p.name for p in found[0][:3]]
            self._log_safe(f"RAMPING: files found {names} but no rows match {day}")

        out.sort(key=lambda t: t[0])
        self._ramping_cache[key] = out
        return out

    _DEFAULT_HOUR = 14

    # PVs consulted for the start-up hour, in priority order: the first one that shows a
    # real signal that day decides. They are alternatives, not a committee — sbw4 sits flat
    # for whole days (median -0.108, never above zero on 05. and 06.08.2026), and on those
    # days the choice simply falls through to ptm1, then to the waveplate.
    _AUTOHOUR_PVS = ("sbw4", "ptm1", "waveplate")
    # Energy channels read a small NEGATIVE offset when idle (sbw4 -0.108, ptm1 -1.69) and
    # the archiver logs ~4000 such samples an hour around the clock, so sample count says
    # nothing. A shot puts real joules on them, and energy cannot be negative — so "> 0" is
    # the signal test, and it needs no tuning. Measured over 31.07-07.08.2026: sbw4 never
    # once rose above zero (max -0.091, i.e. the channel is currently dead), ptm1 reached
    # 18.4 / 13.2 / 109.6 J on 06. / 05. / 04.08 and stayed idle on 31.07 and 07.08.
    #
    # Using a threshold relative to the day's own span instead — which is right for a
    # position — made a dead channel look busy: sbw4's idle jitter then registered
    # "activity" in all 24 hours and picked 02:00 on 05.08.
    _AUTOHOUR_ENERGY_PVS = ("sbw4", "ptm1", "pcm2", "pcm4", "pap1", "Back_Ref")
    # The waveplate is a POSITION, where zero means nothing. There the signal is movement
    # away from the day's resting position.
    _AUTOHOUR_ACTIVE_FRAC = 0.25

    def _pv_hour_activity(self, day, pv_key) -> dict:
        """{hour: number of samples where this one PV is genuinely active}, or {} if the
        channel is flat / missing / unreadable for that day."""
        channel = CPVA_CHANNEL_MAP.get(pv_key)
        if not channel:
            return {}
        try:
            res = cpva.get_day(channel, day.isoformat())
            samples = getattr(res, "samples", None) or []
        except Exception:
            return {}
        vals, per_hour = [], {}
        for ts_ns, v in samples:
            if v is None:
                continue
            v = float(v)
            vals.append(v)
            hr = datetime.fromtimestamp(ts_ns / 1e9, PRAGUE).hour if PRAGUE                 else datetime.fromtimestamp(ts_ns / 1e9).hour
            per_hour.setdefault(hr, []).append(v)
        if not vals:
            return {}
        if pv_key in self._AUTOHOUR_ENERGY_PVS:
            cut = 0.0                               # energy: anything positive is a shot
        else:
            vals.sort()
            base = vals[len(vals) // 2]             # this channel's resting position today
            span = vals[-1] - base
            if span <= 0:
                return {}                           # never moved -> no signal
            cut = base + span * self._AUTOHOUR_ACTIVE_FRAC
        active = {hr: sum(1 for x in hv if x > cut) for hr, hv in per_hour.items()}
        return {hr: n for hr, n in active.items() if n}

    def _pick_hour_from_pv(self, day):
        """Start-up hour from the PV signal: first PV in _AUTOHOUR_PVS that has any signal
        that day wins, and within it the hour holding the most of it.

        Returns (datetime, message), or (None, message) when none of the PVs shows
        anything — in which case the caller keeps the default hour."""
        for pv_key in self._AUTOHOUR_PVS:
            act = self._pv_hour_activity(day, pv_key)
            if not act:
                continue
            hr = max(act, key=act.get)
            ranked = ", ".join(f"{h:02d}:00={n}"
                               for h, n in sorted(act.items(), key=lambda kv: -kv[1])[:5])
            return (datetime(day.year, day.month, day.day, hr),
                    f"AUTO-HOUR: {pv_key} -> {hr:02d}:00 "
                    f"({act[hr]} active samples; top hours: {ranked})")
        tried = ", ".join(self._AUTOHOUR_PVS)
        return None, (f"AUTO-HOUR: no signal on {tried} for {day} — keeping the default "
                      f"hour {self._DEFAULT_HOUR:02d}:00")

    def _pick_best_block_real_hour(self, day):
        rows = self._get_ramping_for_day_cached(day)
        if not rows:
            default_dt = datetime(day.year, day.month, day.day, self._DEFAULT_HOUR)
            return default_dt, f"RAMPING: no CSV data for {day} — using default hour {self._DEFAULT_HOUR:02d}:00"
        has_any = any(r[4] is not None for r in rows)
        if has_any:
            on = [r for r in rows if r[4] == 1]
            if on: rows = on
        segs = []; cur = [rows[0]]
        for r in rows[1:]:
            if (r[0] - cur[-1][0]).total_seconds() <= ACT_MAX_GAP_S: cur.append(r)
            else: segs.append(cur); cur = [r]
        if cur: segs.append(cur)

        def percentile(vals, p):
            if not vals: return 0
            v = sorted(vals); idx = int(round((p / 100.0) * (len(v) - 1)))
            return v[max(0, min(len(v) - 1, idx))]

        # TAIL_RATIO: step back from the last hour if it has fewer than this fraction
        # of rows compared to the previous hour. E.g. 0.15 means: if last hour has
        # less than 15% of the rows of the previous hour, step back.
        TAIL_RATIO = 0.15

        best = None; best_score = None; best_seg = None
        for seg in segs:
            start = seg[0][0]; end = seg[-1][0]
            dur_s = (end - start).total_seconds(); n = len(seg)
            wps = [x[1] for x in seg]
            wp_p90 = percentile(wps, 90); wp_med = percentile(wps, 50)
            if not (n >= MIN_SEG_ROWS or dur_s >= MIN_SEG_DURATION_S): continue
            end_sec = end.hour * 3600 + end.minute * 60 + end.second
            score = wp_p90 * 1.0 + wp_med * 0.3 + dur_s * 0.8 + end_sec * 5.0
            if best_score is None or score > best_score:
                best_score = score
                best = (start, end, n, dur_s, wp_med, wp_p90)
                best_seg = seg

        if best is None:
            # No segment met min criteria — pick by (median_wp, duration)
            def seg_score_fb(s):
                wps = [r[1] for r in s]
                return (percentile(wps, 50), (s[-1][0] - s[0][0]).total_seconds(), len(s))
            best_seg = max(segs, key=seg_score_fb)
            best_row = best_seg[-1]
            hr_dt = best_row[0].replace(minute=0, second=0, microsecond=0)
            return hr_dt, (
                f"RAMPING AUTO (fallback segment): "
                f"rows={len(best_seg)} end={best_row[0].strftime('%H:%M:%S')} "
                f"wp={best_row[1]} -> real hour {hr_dt.strftime('%H:00')}"
            )

        start, end, n, dur_s, wp_med, wp_p90 = best

        # If the last hour has far fewer rows than the previous hour, step back.
        tail_note = ""
        end_hour = end.hour
        tail_rows_count = sum(1 for r in best_seg if r[0].hour == end_hour)
        prev_hour_rows  = [r for r in best_seg if r[0].hour == end_hour - 1]
        prev_rows_count = len(prev_hour_rows)
        # Step back if: previous hour has data AND last hour has < TAIL_RATIO of previous
        should_step_back = (
            prev_rows_count > 0
            and tail_rows_count < prev_rows_count * TAIL_RATIO
        )
        if should_step_back:
            prev_rows = [r for r in best_seg if r[0].hour < end_hour]
            if prev_rows:
                end = prev_rows[-1][0]
                tail_note = (
                    f"\n  tail {end_hour:02d}:xx had {tail_rows_count} rows vs "
                    f"{prev_rows_count} in prev hour "
                    f"(ratio {tail_rows_count/max(prev_rows_count,1):.2f} < {TAIL_RATIO}) "
                    f"-> using {end.hour:02d}:xx instead"
                )

        hr_dt = end.replace(minute=0, second=0, microsecond=0)
        msg = (
            "RAMPING AUTO (best block)\n"
            f"  date:      {day.strftime('%d.%m.%Y')}\n"
            f"  block:     {start.strftime('%H:%M:%S')} -> {best_seg[-1][0].strftime('%H:%M:%S')}\n"
            f"  rows:      {n}\n"
            f"  duration:  {dur_s/60:.1f} min\n"
            f"  wp_med:    {wp_med}\n"
            f"  wp_p90:    {wp_p90}\n"
            f"  chosen:    REAL hour {hr_dt.strftime('%H:00')}"
            + tail_note
        )
        return hr_dt, msg

    def _apply_auto_hour_for_selected_day(self):
        qd = self._cal.selectedDate()
        day = datetime(qd.year(), qd.month(), qd.day()).date()
        if self._auto_hour_last_day == day:
            return
        self._auto_hour_last_day = day

        # Set a sensible default hour now; the camera table load is driven by
        # the day selection, not by the hour, so do not trigger a load here.
        self._hour_cb.blockSignals(True)
        self._hour_cb.setCurrentIndex(self._DEFAULT_HOUR)
        self._hour_cb.blockSignals(False)
        self._sync_time_summary()

        # In background: try CSV auto-hour and update the hour combo if it differs.
        self._ensure_ramping_root()
        self._auto_hour_sig = _AutoHourSignals()
        self._auto_hour_sig.log_msg.connect(self._log)
        self._auto_hour_sig.apply.connect(self._apply_auto_hour_ui)

        def worker():
            try:
                # PV signal first. The RAMPING CSV that used to drive this has not been
                # written since 26.05.2026 (newest file on both the lab and office shares
                # is dataof2026May_26.csv), so for every recent day it returned no rows and
                # the hour fell back to a hard-coded 14:00 — which on 06.08 was the
                # THINNEST hour of the day, 4540 frames against 11985 at 13:00.
                hr_dt, msg = self._pick_hour_from_pv(day)
                if hr_dt is None:
                    self._auto_hour_sig.log_msg.emit(msg)
                    hr_dt, msg = self._pick_best_block_real_hour(day)
                if hr_dt is None:
                    self._auto_hour_sig.log_msg.emit(msg)
                    return
                chosen_real_hour = hr_dt.hour
                use_lab = self._lab_time_cb.isChecked()
                ui_hour   = chosen_real_hour
                day_shift = 0
                self._auto_hour_sig.apply.emit(msg, ui_hour, day_shift, use_lab)
            except Exception as e:
                self._auto_hour_sig.log_msg.emit(f"RAMPING AUTO error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _apply_auto_hour_ui(self, msg: str, ui_hour: int, day_shift: int, use_lab: bool):
        """Slot — runs on main thread. Applies auto-hour result from background CSV lookup."""
        self._log(msg)
        current_hour = self._hour_cb.currentIndex()
        hour_changed = (ui_hour != current_hour) or (day_shift == 1)

        if day_shift == 1:
            qd2 = self._cal.selectedDate().addDays(1)
            self._cal.blockSignals(True)
            self._cal.setSelectedDate(qd2)
            self._cal.blockSignals(False)
            self._log(f"AUTO-HOUR: day shift -> {qd2.toString('dd.MM.yyyy')}")

        self._hour_cb.blockSignals(True)
        self._hour_cb.setCurrentIndex(ui_hour)
        self._hour_cb.blockSignals(False)

        if PRAGUE is not None:
            qd_ = self._cal.selectedDate()
            dt_p = datetime(qd_.year(), qd_.month(), qd_.day(), ui_hour, 0, 0, tzinfo=PRAGUE)
            utc_offset_h = int(dt_p.utcoffset().total_seconds() / 3600)
        else:
            utc_offset_h = 1
        self._log(
            f"AUTO-HOUR APPLIED: Lab time={use_lab} | UI REAL={ui_hour:02d}:00 "
            f"-> effective folder hour will be "
            f"{(ui_hour - (0 if use_lab else utc_offset_h)) % 24:02d}:00"
        )
        self._log_selected_datetime_preview()
        # No reload here: the camera table is hour-independent. The hour combo
        # only affects the effective target path / View collection.

    # ── DATETIME / PATH LOGIC ─────────────────────────────────────────────────
    def _build_datetime(self) -> datetime:
        qd = self._cal.selectedDate()
        hour = self._hour_cb.currentIndex()
        dt_real = datetime(qd.year(), qd.month(), qd.day(), hour, 0, 0)
        if not self._lab_time_cb.isChecked():
            if PRAGUE is not None:
                dt_prague = datetime(qd.year(), qd.month(), qd.day(), hour, 0, 0, tzinfo=PRAGUE)
                utc_offset_h = int(dt_prague.utcoffset().total_seconds() / 3600)
                self._log(f"BUILD_DT: hour={hour} utc_offset_h={utc_offset_h} -> folder hour={(hour - utc_offset_h) % 24}")
                return dt_real - timedelta(hours=utc_offset_h)
            else:
                return dt_real - timedelta(hours=1)
        return dt_real

    def _build_target_path(self, dt: datetime) -> Path:
        year = dt.year
        # Each year lives in its own share: cpva-image-<year> (e.g. 2024 -> cpva-image-2024).
        root = Path(IMAGES_ROOT_BASE) / f"cpva-image-{year}"
        return root / str(year) / str(dt.month) / str(dt.day) / str(dt.hour)

    def _log_selected_datetime_preview(self):
        """Log the effective target path — identical to original."""
        self._sync_time_summary()
        try:
            qd   = self._cal.selectedDate()
            hour = self._hour_cb.currentIndex()
            dt_real = datetime(qd.year(), qd.month(), qd.day(), hour, 0, 0)
            dt_eff  = self._build_datetime()
            target  = self._build_target_path(dt_eff)
            self._log(
                "-------------------------------\n"
                f"Selected - Prague Time:  {dt_real.strftime('%d.%m.%Y %H:00')}\n"
                f"Lab time:                {self._lab_time_cb.isChecked()}\n"
                f"Target path:             {target}\n"
            )
        except Exception as e:
            self._log(f"Preview ERROR: {e}")

    # ── LOAD FOLDERS ──────────────────────────────────────────────────────────
    def load_folders(self):
        if not self._user_has_selected_day:
            return

        # Clear the camera list immediately on the main thread (instant)
        self._status_dot.setStyleSheet("color: gray; font-size: 12px;")
        saved_sel = self._capture_selection_state()
        self._cams = []
        self._refresh_selected_table()
        self._load_gen += 1
        current_gen = self._load_gen; self.primary_files = []

        dt          = self._build_datetime()
        target_path = self._build_target_path(dt)
        # Camera table = union of cameras across ALL effective (weekday-filtered)
        # selected days. A day with fewer cameras no longer hides cameras that
        # appear on other selected days.
        eff_days = [(d.year(), d.month(), d.day()) for d in self._effective_days()]
        if not eff_days:
            eff_days = [(dt.year, dt.month, dt.day)]
        self._log(f"Load -> {len(eff_days)} day(s) | primary target={target_path}")

        self._load_sig = _LoadSignals()
        self._load_sig.done.connect(self._on_load_done)
        self._load_sig.not_found.connect(self._on_load_not_found)
        self._load_sig.error.connect(self._on_load_error)
        self._load_sig.log_msg.connect(self._log)
        _sig = self._load_sig  # local ref — prevents GC if load_folders() called again

        def worker():
            try:
                # Collect camera folders from ALL hours of EVERY effective day.
                # Cameras are deduplicated by folder name across days/hours.
                seen: set[str] = set()
                all_entries: list[Path] = []
                found_any_hour = False

                def _scan_hour(day_dir: Path, h: int) -> tuple[bool, list[Path]]:
                    hour_dir = day_dir / str(h)
                    try:
                        if not hour_dir.exists() or not hour_dir.is_dir():
                            return False, []
                    except Exception:
                        return False, []
                    entries: list[Path] = []
                    try:
                        for e in os.scandir(hour_dir):
                            if e.is_dir():
                                entries.append(Path(e.path))
                    except Exception:
                        pass
                    return True, entries

                for (yy, mm, dd) in eff_days:
                    # Each year lives in its own share: cpva-image-<year>.
                    root = Path(IMAGES_ROOT_BASE) / f"cpva-image-{yy}"
                    day_dir = root / str(yy) / str(mm) / str(dd)
                    with ThreadPoolExecutor(max_workers=12) as _hex:
                        _futs = [_hex.submit(_scan_hour, day_dir, h) for h in range(24)]
                        for _fut in as_completed(_futs):
                            try:
                                _existed, _entries = _fut.result()
                                if _existed:
                                    found_any_hour = True
                                for _p in _entries:
                                    if _p.name not in seen:
                                        seen.add(_p.name)
                                        all_entries.append(_p)
                            except Exception:
                                pass

                if not found_any_hour:
                    _sig.not_found.emit(target_path)
                    return

                subfolders = sorted(all_entries, key=lambda x: x.name.lower())
                _sig.log_msg.emit(f"LOAD: scanned {len(eff_days)} day(s), found {len(subfolders)} cameras")
                _sig.done.emit(subfolders, saved_sel, current_gen)
            except Exception as e:
                _sig.error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=worker, daemon=True).start()


    def _on_load_not_found(self, target_path: Path):
        if not self.isVisible():
            return
        self._log(f"Target folder not found: {target_path}")
        resp = QMessageBox.question(
            self, "Not found",
            f"Folder does not exist:\n{target_path}\n\n"
            "Search by PV region instead? (Plot a PV, mark time regions, and "
            "pull frames from the peak of each region.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        self._refresh_selected_table()
        if resp == QMessageBox.StandardButton.Yes:
            self._open_pv_region_search()

    def _on_load_error(self, err: str):
        if not self.isVisible():
            return
        self._log(f"ERROR: {err}")
        QMessageBox.critical(self, "Error", err)

    def _on_load_done(self, subfolders: list[Path], saved_sel: dict[str, int], gen: int):
        """Called on main thread after background scan — fills the camera list."""
        self._log(f"_on_load_done: start, {len(subfolders)} folders")
        if gen != self._load_gen:
            self._log(f"_on_load_done: ignoring stale load (gen {gen} != {self._load_gen})")
            return
        try:
            # Count how many hours each camera label appears in (for disambiguation)
            label_count: dict[str, int] = {}
            for p in subfolders:
                lbl = extract_display_label(p.name)
                label_count[lbl] = label_count.get(lbl, 0) + 1

            seen: dict[str, int] = {}
            cams: list[dict] = []
            for p in subfolders:
                label = extract_display_label(p.name)
                seen[label] = seen.get(label, 0) + 1
                if label_count.get(label, 1) > 1:
                    try:
                        label_display = f"{label} [h{p.parent.name}]"
                    except Exception:
                        label_display = f"{label} ({seen[label]})"
                else:
                    label_display = label
                num = extract_folder_number(p.name)
                try:
                    cam_int = (int(num) if num.isdigit()
                               else int(num[1:]) if num.upper().startswith("C") and num[1:].isdigit()
                               else None)
                    hz33 = "YES" if (cam_int is not None and cam_int in CAM_33HZ) else ""
                except: hz33 = ""
                cams.append({
                    "path":    p,
                    "name":    p.name,
                    "num":     num,
                    "label":   label_display,
                    "hz33":    hz33,
                    "qty":     int(saved_sel.get(p.name, 1)),
                    "checked": p.name in saved_sel,
                })
            self._cams = cams
            self._refresh_selected_table()
            self._log(f"Subfolders loaded: {len(subfolders)}")
            self._status_dot.setStyleSheet("color: green; font-size: 14px;")
        except Exception as e:
            self._log(f"_on_load_done ERROR: {type(e).__name__}: {e}")
            import traceback
            self._log(traceback.format_exc())


    # ── OPEN IN SLIDER / EXPLORER ─────────────────────────────────────────────
    def open_in_slider(self):
        """Hand the Slider the frames that are actually on the wall.

        This used to open the FIRST checked folder WHOLE and log that it was ignoring
        the rest — the tab did all the work of picking a frame per day and then threw
        the selection away at the door. When there is a wall, the picked frames are
        copied to a temp folder and pushed through the Slider's own public handoff
        (`receive_external_folder`), which is the same route the Shot Finder uses and
        which clears a multi-cam grid, an armed live mode, a subtraction reference and
        focus mode first. With no wall it falls back to opening the folder, as before.
        """
        if self._slider_ref is None or self._tab_widget is None:
            QMessageBox.information(self, "Info", "Image Slider not connected."); return

        cells = self._wall.cells() if hasattr(self, "_wall") else []
        cells = [c for c in cells if c.get("path")]
        if cells:
            self._push_cells_to_slider(cells)
            return

        if not self._user_has_selected_day:
            QMessageBox.information(self, "Info", "Select a day first."); return
        jobs    = self._snapshot_collect_jobs()
        folders = [f for f, _ in jobs if f and f.exists() and f.is_dir()]
        if not folders:
            QMessageBox.information(self, "Info", "No cameras selected."); return
        if len(folders) > 1:
            self._log(f"SLIDER: {len(folders)} folders selected, opening FIRST: {folders[0].name}")
        self._tab_widget.setCurrentIndex(1)
        self._slider_ref.open_folder_path(folders[0])
        self._log(f"SLIDER: opened {folders[0].name}")

    def _push_cells_to_slider(self, cells: list):
        """Copy the picked frames into a temp folder and hand them over as a set.

        Each caption is carried NEXT TO its own file rather than looked up by index
        later: a day without a picture would otherwise shift every following caption
        onto the wrong frame (the mistake the Shot Finder's handoff documents)."""
        try:
            root = Path(tempfile.mkdtemp(prefix="IF_slider_"))
            # img_scale.camera_from_path reads the camera off the PARENT FOLDER, so a
            # flat temp folder would strip it and the Slider would fall back to the
            # plain 65535 range — the same frame would come out ~16× darker there than
            # on the wall. Keeping the camera folder name preserves the sensor range,
            # so the handed-over frames look like what was being compared.
            src_folders = {Path(c["path"]).parent.name for c in cells if c.get("path")}
            new_dir = root / src_folders.pop() if len(src_folders) == 1 else root
            new_dir.mkdir(parents=True, exist_ok=True)
            energy_map: dict = {}
            cams = set()
            for c in cells:
                src = Path(c["path"])
                if not src.exists():
                    continue
                dst = new_dir / src.name
                shutil.copy2(src, dst)
                cams.add(c.get("cam", ""))
                day = c.get("day")
                cap = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
                energy_map[dst.name] = f"{c.get('cam', '')}  {cap}".strip()
            if not energy_map:
                QMessageBox.information(self, "Info", "No frames to send."); return
            cam_name = next(iter(cams)) if len(cams) == 1 else ""
            self._tab_widget.setCurrentIndex(1)
            ok = False
            if hasattr(self._slider_ref, "receive_external_folder"):
                ok = self._slider_ref.receive_external_folder(
                    new_dir, energy_map=energy_map, discrete=True, cam_name=cam_name)
            if not ok:
                # Older is_t.py without the public handoff — set what it reads directly.
                self._slider_ref._sf_energy_map = dict(energy_map)
                self._slider_ref._discrete_mode = True
                self._slider_ref.open_folder_path(new_dir)
            # The previous temp folder is dropped only AFTER the new one is handed
            # over, so a Slider still showing the last send does not lose its files.
            old = getattr(self, "_slider_temp_dir", None)
            if old and Path(old).exists() and Path(old) != root:
                shutil.rmtree(old, ignore_errors=True)
            self._slider_temp_dir = root      # the root, so the camera subfolder goes too
            self._log(f"SLIDER: sent {len(energy_map)} frame(s) as a set.")
        except Exception as e:
            QMessageBox.warning(self, "Image Slider", f"Could not hand over:\n{e}")

    def _open_first_in_slider(self):
        """Internal auto-open: switch to Slider tab and load first checked folder."""
        if self._slider_ref is None or self._tab_widget is None: return
        jobs = self._snapshot_collect_jobs()
        if not jobs: return
        folder, _ = jobs[0]
        if not folder or not folder.exists() or not folder.is_dir():
            self._log(f"SLIDER: folder not found: {folder}"); return
        self._tab_widget.setCurrentIndex(1)
        self._slider_ref.open_folder_path(folder)
        self._log(f"SLIDER: opened {folder.name}")

    def open_folder_in_explorer(self):
        if not self._user_has_selected_day:
            QMessageBox.information(self, "Info", "Select a day first."); return
        jobs    = self._snapshot_collect_jobs()
        folders = [f for f, _ in jobs if f and f.exists() and f.is_dir()]
        if not folders:
            QMessageBox.information(self, "Info", "No folders selected."); return

        import subprocess

        def _open(folder_list):
            for folder in folder_list:
                try:
                    subprocess.Popen(["explorer", str(folder)])
                    self._log(f"EXPLORER: {folder}")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")

        if len(folders) == 1:
            _open(folders); return

        # Multiple cameras — ask which to open
        labels = [extract_display_label(f.name) for f in folders]
        dlg = QDialog(self); dlg.setWindowTitle("Otevřít složku")
        dlg_lay = QVBoxLayout(dlg)
        dlg_lay.addWidget(QLabel("Vyber kameru nebo otevři všechny:"))

        from PySide6.QtWidgets import QRadioButton
        btn_group = QButtonGroup(dlg)
        radio_btns = []
        for lbl in labels:
            rb = QRadioButton(lbl)
            btn_group.addButton(rb)
            dlg_lay.addWidget(rb)
            radio_btns.append(rb)
        radio_btns[0].setChecked(True)

        btns_row = QHBoxLayout()
        btn_selected = QPushButton("Otevřít vybranou")
        btn_all      = QPushButton("Otevřít všechny")
        btn_cancel   = QPushButton("Zrušit")
        btns_row.addWidget(btn_selected)
        btns_row.addWidget(btn_all)
        btns_row.addWidget(btn_cancel)
        dlg_lay.addLayout(btns_row)

        btn_selected.clicked.connect(dlg.accept)
        btn_all.clicked.connect(lambda: (setattr(dlg, "_open_all", True), dlg.accept()))
        btn_cancel.clicked.connect(dlg.reject)
        dlg._open_all = False

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg._open_all:
            _open(folders)
        else:
            checked_idx = next((i for i, rb in enumerate(radio_btns) if rb.isChecked()), 0)
            _open([folders[checked_idx]])

    # ── MULTI-DAY SEARCH ─────────────────────────────────────────────────────

    @staticmethod
    def _blocking_call(fn, cancelled: "threading.Event", poll_s: float = 0.05):
        """
        Run fn() in a daemon thread. Poll cancelled every poll_s seconds.
        If cancelled fires before fn completes, abandon the thread and return None.
        Otherwise return fn()'s result.
        """
        result_box: list = [None]
        done_evt = threading.Event()

        def _run():
            try:
                result_box[0] = fn()
            except Exception:
                pass
            finally:
                done_evt.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        while not done_evt.wait(timeout=poll_s):
            if cancelled.is_set():
                return None          # abandon; daemon thread dies with the process
        return result_box[0]

    def _nearest_file_for_ns(self, cam_folder: Path,
                              target_ns: int,
                              cancelled: "threading.Event | None" = None) -> "Path | None":
        """
        Find the image file in cam_folder whose filename timestamp is closest
        to target_ns (UTC nanoseconds).

        Strategy: probe candidate ns values directly via os.path.exists() rather
        than listing the whole directory.  Files are named {folder_name}_-_{ns}.ext
        (original format) so we can construct the path directly.

        We probe at intervals matching the camera's acquisition period (33 Hz ≈
        30 ms, or 1 Hz ≈ 1 s for slower cameras) spanning ±2 s around target_ns.
        The first hit is returned; if nothing is found within ±2 s we fall back to
        a single scandir pass (no stat calls).
        """
        prefix = cam_folder.name  # folder name == file-name prefix
        cam_num_str = extract_folder_number(prefix)
        try:
            is_33hz = int(cam_num_str) in CAM_33HZ
        except (ValueError, TypeError):
            is_33hz = False

        step_ns   = 30_303_030 if is_33hz else 1_000_000_000   # ~33 Hz or 1 Hz
        window_ns = 2_000_000_000                               # ±2 s search window

        # Probe candidates: start at target and expand outward by one step at a time.
        best_path: "Path | None" = None
        best_delta = window_ns + 1

        steps = int(window_ns / step_ns) + 1
        # Build offsets sorted by absolute distance: 0, ±step, ±2*step, …
        offsets: list[int] = [0]
        for i in range(1, steps + 1):
            offsets.append( i * step_ns)
            offsets.append(-i * step_ns)
        offsets.sort(key=abs)

        for off in offsets:
            if cancelled and cancelled.is_set():
                return None
            if abs(off) > best_delta:
                # All remaining offsets are farther — no improvement possible.
                break
            cand_ns = target_ns + off
            for ext in (".png", ".tif", ".tiff", ".jpg"):
                p = cam_folder / f"{prefix}_-_{cand_ns}{ext}"
                if p.exists():
                    best_delta = abs(off)
                    best_path = p
                    break

        if best_path is not None:
            return best_path

        # Fallback: single scandir pass (no stat calls) — handles converted filenames.
        names_ns = []
        try:
            with os.scandir(cam_folder) as it:
                for e in it:
                    if cancelled and cancelled.is_set():
                        return None
                    name = e.name
                    if not name or name.startswith("."):
                        continue
                    dot = name.rfind(".")
                    if dot < 0 or name[dot:].lower() not in IMAGE_EXTS:
                        continue
                    ns = extract_ns_from_stem(name[:dot])
                    if ns is None:
                        continue
                    names_ns.append((ns, name))
        except Exception:
            return None
        if not names_ns:
            return None
        names_ns.sort(key=lambda x: x[0])
        ns_vals = [x[0] for x in names_ns]
        idx = bisect.bisect_left(ns_vals, target_ns)
        candidates = []
        if idx > 0:
            candidates.append(names_ns[idx - 1])
        if idx < len(names_ns):
            candidates.append(names_ns[idx])
        if not candidates:
            return None
        _, best_name = min(candidates, key=lambda x: abs(x[0] - target_ns))
        return cam_folder / best_name

    def _frame_nearest_ns(self, year: int, month: int, day_n: int,
                          cam_name: str, use_lab: bool, target_ns: int,
                          cancelled: "threading.Event | None" = None,
                          log=None) -> "tuple[Path | None, int | None]":
        """Camera frame nearest target_ns (UTC ns) for (day, cam), tz-aware.

        Mirrors the try_timestamp closure inside _find_image_for_day_cam so both
        the automatic and the PV-region search paths share one code path.
        Returns (path, real_hour) or (None, None) if nothing suitable exists.
        """
        def _log(m):
            if log:
                log(m)

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        if is_cancelled():
            return None, None

        dt_utc = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
        if not use_lab and PRAGUE is not None:
            real_h = dt_utc.astimezone(PRAGUE).hour
        else:
            real_h = dt_utc.hour

        # real hour → folder (archiver/UTC) hour
        if use_lab or PRAGUE is None:
            folder_h = real_h
        else:
            dt_p = datetime(year, month, day_n, real_h, tzinfo=PRAGUE)
            folder_h = real_h - int(dt_p.utcoffset().total_seconds() / 3600)
        if not (0 <= folder_h <= 23):
            return None, None   # maps outside this day's folders

        dt_eff = datetime(year, month, day_n, folder_h)
        cam_folder = self._build_target_path(dt_eff) / cam_name
        _log(f"scan folder h={folder_h:02d}  {cam_folder}")

        if cancelled is not None:
            exists = self._blocking_call(lambda cf=cam_folder: cf.exists(), cancelled)
        else:
            exists = cam_folder.exists()
        if is_cancelled():
            return None, None
        if not exists:
            _log("  folder does not exist")
            return None, None

        if cancelled is not None:
            p = self._blocking_call(
                lambda cf=cam_folder: self._nearest_file_for_ns(cf, target_ns),
                cancelled)
        else:
            p = self._nearest_file_for_ns(cam_folder, target_ns)
        if is_cancelled():
            return None, None
        _log(f"  → {p.name if p else 'nothing'}")
        return (p, real_h) if p else (None, None)

    def _find_image_for_regions(
        self,
        day,                       # datetime.date
        cam_name: str,
        use_lab: bool,
        regions_for_day: "list[tuple[int, int]]",
        primary_channel: str,
        cancelled: "threading.Event | None" = None,
        log_fn=None,
    ) -> "tuple[Path | None, int | None, dict, str]":
        """PV-region driven image lookup.

        For each (t_start_ns, t_end_ns) region on `day`, take the timestamp of the
        PEAK value of primary_channel inside the region as the target time, and
        return the camera frame nearest that time. If the PV has no samples in a
        region, the region midpoint is used instead.

        Returns (path, real_hour, meta, status) with the SAME shape as
        _find_image_for_day_cam, so the wall takes either without knowing which ran.
        """
        def log(msg: str):
            if log_fn:
                log_fn(f"  {msg}")

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        _no_meta: dict = {"ptm1": None, "sbw4": None, "source": None}
        if is_cancelled():
            return None, None, _no_meta, "cancelled"

        year, month, day_n = day.year, day.month, day.day

        for (t_start_ns, t_end_ns) in regions_for_day:
            if is_cancelled():
                return None, None, _no_meta, "cancelled"

            # ── Peak of the primary PV inside the region ──────────────────────
            samples = None
            try:
                if cancelled is not None:
                    samples = self._blocking_call(
                        lambda ch=primary_channel, s=t_start_ns, e=t_end_ns:
                            _cpva_fetch_samples(ch, s, e, timeout=3.0),
                        cancelled)
                else:
                    samples = _cpva_fetch_samples(primary_channel,
                                                  t_start_ns, t_end_ns, timeout=3.0)
            except Exception as _e:
                log(f"PV fetch error: {_e}")
            if is_cancelled():
                return None, None, _no_meta, "cancelled"

            target_ns: "int | None" = None
            peak_val: "float | None" = None
            if isinstance(samples, list):
                for s in samples:
                    t_ns = s.get("time")
                    if t_ns is None:
                        continue
                    val = s.get("value")
                    if isinstance(val, list):
                        val = val[0] if len(val) == 1 else None
                    try:
                        fv = float(val)
                    except (TypeError, ValueError):
                        continue
                    if peak_val is None or fv > peak_val:
                        peak_val = fv
                        target_ns = int(t_ns)

            if target_ns is None:
                target_ns = (t_start_ns + t_end_ns) // 2
                log("region: no PV samples — using midpoint")
            else:
                dt_tgt = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
                if PRAGUE and not use_lab:
                    dt_tgt = dt_tgt.astimezone(PRAGUE)
                log(f"region peak: {dt_tgt.strftime('%H:%M:%S')}  value={peak_val:.4g}")

            p, real_h = self._frame_nearest_ns(year, month, day_n, cam_name,
                                               use_lab, target_ns, cancelled, log)
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            if p is not None:
                meta = dict(_no_meta)
                if peak_val is not None:
                    if primary_channel == CPVA_SBW4_CHANNEL:
                        meta["sbw4"] = peak_val
                    elif primary_channel == CPVA_SHOT_CHANNEL:
                        meta["ptm1"] = peak_val
                    meta["pv_peak"] = peak_val
                meta["source"] = "pv"
                return p, real_h, meta, "found"

        return None, None, _no_meta, "not_found"

    def _find_image_for_day_cam(
        self,
        day,           # datetime.date
        cam_name: str,
        use_lab: bool,
        start_hour_real: int,
        max_hour_real: int,
        cancelled: "threading.Event | None" = None,
        log_fn=None,
    ) -> "tuple[Path | None, int | None, dict, str]":
        """
        Find one representative image for (day, camera).

        The question this answers is "when was the machine actually SHOOTING that day",
        and the laser's own energy readings answer it directly — so they are asked first
        and the camera's own power reading only afterwards:

          1. SBW4 energy. Every shot of the day, strongest first; walk down that list
             until one of them has a frame in this camera's folder.
          2. PTM1 energy, the same way — only if SBW4 recorded nothing at all.
          3. This camera's :TotalPower. Its active windows, best window first, aiming at
             the STRONGEST sample in the window.
          4. The daily energy CSV, then a blind hour-by-hour scan.

        The order used to be the reverse, and that is why a fortnight of searching came
        back with one usable picture. :TotalPower gated everything, so a camera whose
        channel is missing or asleep never got as far as the energy readings; the moment
        inside a window was drawn AT RANDOM rather than aimed at a shot; and the fallback
        below it was the daily energy CSV, which has been dead since 19.08.2026 — leaving
        "first file in the folder", i.e. a dark frame between shots.

        Returns (path, real_hour, meta, status) where:
          status ∈ 'found' | 'inactive' | 'not_found' | 'cancelled'
          meta   = {'ptm1': float|None, 'sbw4': float|None, 'source': str|None}
        `source` names the reading that picked the frame — 'sbw4', 'ptm1', 'totalpower',
        'csv' or 'blind' — and is shown on the tile, so a frame nobody vouched for is
        visible as such instead of looking like a broken camera.
        """
        def log(msg: str):
            if log_fn:
                log_fn(f"  {msg}")

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        _no_meta: dict = {"ptm1": None, "sbw4": None, "source": None}

        class _FallbackToBlindScan(Exception):
            pass

        year, month, day_n = day.year, day.month, day.day

        def real_to_folder_h(real_h: int) -> int:
            if use_lab or PRAGUE is None:
                return real_h
            dt_p = datetime(year, month, day_n, real_h, tzinfo=PRAGUE)
            return real_h - int(dt_p.utcoffset().total_seconds() / 3600)

        def ns_to_real_h(target_ns: int) -> int:
            dt_utc = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
            if not use_lab and PRAGUE is not None:
                return dt_utc.astimezone(PRAGUE).hour
            return dt_utc.hour

        def try_timestamp(target_ns: int) -> "tuple[Path|None, int|None]":
            if is_cancelled():
                return None, None
            real_h = ns_to_real_h(target_ns)
            folder_h = real_to_folder_h(real_h)
            if not (0 <= folder_h <= 23):
                return None, None   # maps outside this day's folders
            dt_eff = datetime(year, month, day_n, folder_h)
            cam_folder = self._build_target_path(dt_eff) / cam_name
            log(f"scan folder h={folder_h:02d}  {cam_folder}")
            exists = bc(lambda cf=cam_folder: cf.exists(), cancelled)
            if is_cancelled():
                return None, None
            if not exists:
                log("  folder does not exist")
                return None, None
            t0 = time.perf_counter()
            p = bc(lambda cf=cam_folder: self._nearest_file_for_ns(cf, target_ns),
                   cancelled)
            if is_cancelled():
                return None, None
            log(f"  scandir {time.perf_counter()-t0:.2f}s  →  {p.name if p else 'nothing'}")
            return (p, real_h) if p else (None, None)

        def try_timestamp_window(w_start_ns: int, w_end_ns: int) -> "tuple[Path|None, int|None]":
            """Pick any file inside the active TotalPower window (midpoint heuristic)."""
            mid_ns = (w_start_ns + w_end_ns) // 2
            return try_timestamp(mid_ns)

        if is_cancelled():
            return None, None, _no_meta, "cancelled"

        bc = self._blocking_call   # shorthand

        # ── Build day time window ─────────────────────────────────────────────
        if not use_lab and PRAGUE is not None:
            t_start = datetime(year, month, day_n, start_hour_real,
                               tzinfo=PRAGUE).astimezone(timezone.utc)
            t_end   = datetime(year, month, day_n, max_hour_real, 59, 59,
                               tzinfo=PRAGUE).astimezone(timezone.utc)
        else:
            t_start = datetime(year, month, day_n, start_hour_real,
                               tzinfo=timezone.utc)
            t_end   = datetime(year, month, day_n, max_hour_real, 59, 59,
                               tzinfo=timezone.utc)
        start_ns = int(t_start.timestamp() * 1e9)
        end_ns   = int(t_end.timestamp()   * 1e9)

        # ── Method 0: the laser's own energy readings ─────────────────────────
        # SBW4 first, PTM1 only if SBW4 recorded nothing. Both name the moments the
        # machine was firing, which is exactly what a comparison of days wants; the
        # camera's own power reading (below) is a proxy for the same thing and is only
        # needed where the energy readings are silent.
        date_key = day.isoformat()
        energy_max_tries = 20

        def try_energy_channel(channel: str, meta_key: str) -> "tuple | None":
            shots = _energy_shots_ranked(channel, date_key, start_ns, end_ns,
                                         timeout=CPVA_HTTP_TIMEOUT, debug_log=log)
            if not shots:
                return None
            # Strongest first, then down the list: the peak shot of the day is the one
            # worth comparing, and if the archive happens to hold no frame at that exact
            # second the next-strongest is still a shot rather than a gap between them.
            for t_ns, val in shots[:energy_max_tries]:
                if is_cancelled():
                    return None
                p, h = try_timestamp(t_ns)
                if p is not None:
                    dt_tgt = datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc)
                    if PRAGUE and not use_lab:
                        dt_tgt = dt_tgt.astimezone(PRAGUE)
                    log(f"  {meta_key.upper()}: {dt_tgt.strftime('%H:%M:%S')} "
                        f"value={val:.4g} → {p.name}")
                    meta = dict(_no_meta)
                    meta[meta_key] = val
                    meta["source"] = meta_key
                    return p, h, meta, "found"
            log(f"  {meta_key.upper()}: {len(shots)} shot(s), but no frame for this camera")
            return None

        log("method0: laser energy (SBW4, then PTM1)")
        for _ch, _key in ((CPVA_SBW4_CHANNEL, "sbw4"), (CPVA_SHOT_CHANNEL, "ptm1")):
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            hit = try_energy_channel(_ch, _key)
            if hit is not None:
                return hit
            if is_cancelled():
                return None, None, _no_meta, "cancelled"

        # ── Method 1: TotalPower → file + additive PTM1/SBW4 ─────────────────
        tp_channel = _cam_totalpower_channel(cam_name)
        _tp_no_windows = False   # set True when TotalPower returns empty → blind-scan fallback
        if tp_channel and tp_channel in self._tp_dead_channels:
            log(f"method1: TotalPower  channel={tp_channel} — skipped (timed out previously)")
            tp_channel = None
        if tp_channel:
            log(f"method1: TotalPower  channel={tp_channel}")
            try:
                # ── Reference window: 6–7h Prague time → used as noise baseline ──
                # Always query from 6h regardless of start_hour_real so that
                # reference samples are always available.
                if not use_lab and PRAGUE is not None:
                    _ref_start = datetime(year, month, day_n, 6, tzinfo=PRAGUE).astimezone(timezone.utc)
                    _ref_end   = datetime(year, month, day_n, 7, tzinfo=PRAGUE).astimezone(timezone.utc)
                else:
                    _ref_start = datetime(year, month, day_n, 6, tzinfo=timezone.utc)
                    _ref_end   = datetime(year, month, day_n, 7, tzinfo=timezone.utc)
                ref_start_ns = int(_ref_start.timestamp() * 1e9)
                ref_end_ns   = int(_ref_end.timestamp()   * 1e9)
                # Extend query start to include 6–7h reference if needed
                query_start_ns = min(start_ns, ref_start_ns)

                t0 = time.perf_counter()
                windows = bc(
                    lambda qs=query_start_ns, rs=ref_start_ns, re=ref_end_ns:
                        _cpva_active_windows_ns(tp_channel, qs, end_ns,
                                                timeout=3.0,
                                                ref_start_ns=rs,
                                                ref_end_ns=re,
                                                active_from_ns=start_ns,
                                                debug_log=log),
                    cancelled)
                elapsed = time.perf_counter() - t0
                if is_cancelled():
                    return None, None, _no_meta, "cancelled"
                if windows is None:
                    if elapsed >= 2.5:
                        # True timeout (not cancellation) — mark channel as dead so
                        # subsequent days skip the 10s wait immediately.
                        self._tp_dead_channels.add(tp_channel)
                        log(f"  TotalPower: timeout ({elapsed:.2f}s) — channel marked dead, skipping for remaining days")
                    else:
                        log(f"  TotalPower: query cancelled/timed out ({elapsed:.2f}s) — falling back to blind scan")
                    _tp_no_windows = True
                    raise _FallbackToBlindScan()
                if not windows:
                    log(f"  TotalPower: no active windows ({elapsed:.2f}s) — falling back to blind scan")
                    _tp_no_windows = True
                    raise _FallbackToBlindScan()

                log(f"  TotalPower: {len(windows)} window(s)  ({elapsed:.2f}s)")

                # Read the power curve once; both the window choice and the target
                # moment come out of it.
                tp_parsed: "list[tuple[int, float]]" = []
                try:
                    for s in (_cpva_fetch_samples(tp_channel, query_start_ns, end_ns,
                                                  timeout=3.0) or ()):
                        t_ns = s.get("time")
                        if t_ns is None:
                            continue
                        v = s.get("value")
                        if isinstance(v, list):
                            v = v[0] if len(v) == 1 else None
                        try:
                            tp_parsed.append((int(t_ns), float(v)))
                        except (TypeError, ValueError):
                            continue
                except Exception as _pe:
                    log(f"  TotalPower samples unavailable: {_pe}")

                # Which window. Where the energy readings did record shots — they just
                # had no frame at those exact seconds — the window holding the most of
                # them is the one the machine was running in. The shot lists come from
                # the per-day cache filled by method 0, so this costs no extra query.
                chosen_window = None
                for ref_ch, ref_key in ((CPVA_SBW4_CHANNEL, "sbw4"),
                                        (CPVA_SHOT_CHANNEL, "ptm1")):
                    if is_cancelled():
                        break
                    ref_shots = _energy_shots_ranked(ref_ch, date_key, start_ns, end_ns,
                                                     timeout=CPVA_HTTP_TIMEOUT)
                    if not ref_shots:
                        continue
                    best_n = 0
                    for ws, we in windows:
                        n_in = sum(1 for t, _ in ref_shots if ws <= t <= we)
                        if n_in > best_n:
                            best_n, chosen_window = n_in, (ws, we)
                    if chosen_window is not None:
                        log(f"  window chosen by {ref_key.upper()}: {best_n} shot(s) inside")
                        break

                if chosen_window is None:
                    if tp_parsed:
                        def _window_avg_power(w):
                            ws, we = w
                            vals = [v for t, v in tp_parsed if ws <= t <= we]
                            return sum(vals) / len(vals) if vals else 0.0
                        chosen_window = max(windows, key=_window_avg_power)
                        log("  window chosen by highest average TotalPower")
                    else:
                        chosen_window = max(windows, key=lambda w: w[1] - w[0])
                        log("  window chosen by length (no samples to weigh)")

                w_start, w_end = chosen_window
                # Aim at the STRONGEST moment in the window, then work down. This used
                # to be a random draw from the window, which on a five-minute-merged
                # window lands between shots as often as on one.
                in_window = sorted((s for s in tp_parsed if w_start <= s[0] <= w_end),
                                   key=lambda s: s[1], reverse=True)
                targets = [t for t, _ in in_window[:energy_max_tries]] or \
                          [(w_start + w_end) // 2]

                for target_ns in targets:
                    if is_cancelled():
                        return None, None, _no_meta, "cancelled"
                    p, h = try_timestamp(target_ns)
                    if p is not None:
                        dt_tgt = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
                        if PRAGUE and not use_lab:
                            dt_tgt = dt_tgt.astimezone(PRAGUE)
                        log(f"  TotalPower: {dt_tgt.strftime('%H:%M:%S')} → {p.name}")
                        meta = dict(_no_meta)
                        meta["source"] = "totalpower"
                        return p, h, meta, "found"

                # The chosen window's hour has no folder/image for this camera —
                # fall through to the CSV-guided + blind scan below instead of
                # giving up, so a camera active in another hour is still found.
                log("  TotalPower window had no image for this camera — trying CSV/blind scan")
                raise _FallbackToBlindScan()

            except _FallbackToBlindScan:
                pass   # no active windows — continue to blind scan below
            except Exception as e:
                log(f"  TotalPower error: {e}")
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            if not _tp_no_windows:
                log("  TotalPower failed, falling back to blind scan")

        # ── Energy-CSV guidance (CPVA dead → use logged SBW4/PTM1) ────────────
        # The daily energy CSV records when the laser was actually firing.
        def _en(r, col):
            try:
                return float((r.values.get(col) or "").strip())
            except Exception:
                return 0.0

        def _row_ns(r):
            if PRAGUE is not None:
                return int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1e9)
            return int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1e9)

        erows = self._energy_rows_for_day_cached(year, month, day_n)
        shots = sorted((r for r in erows if _en(r, "sbw4") > 0 or _en(r, "ptm1") > 0),
                       key=lambda r: (_en(r, "sbw4"), _en(r, "ptm1")), reverse=True)

        # Best (highest-SBW4) shot per folder-hour — used to aim the blind scan.
        best_shot_by_h: dict = {}
        for r in shots:
            fh = real_to_folder_h(ns_to_real_h(_row_ns(r)))
            if fh not in best_shot_by_h:
                best_shot_by_h[fh] = r   # shots is energy-desc, so first = strongest

        # ── Method 2a: jump straight to the strongest shots ───────────────────
        if shots:
            log(f"method2a: CSV-guided, {len(shots)} energetic shots in CSV")
            for r in shots[:15]:
                if is_cancelled():
                    return None, None, _no_meta, "cancelled"
                p, h = try_timestamp(_row_ns(r))
                if p is not None:
                    meta = {"ptm1": _en(r, "ptm1") or None,
                            "sbw4": _en(r, "sbw4") or None,
                            "source": "csv"}
                    log(f"  CSV-guided: shot {r.ts_dt.strftime('%H:%M:%S')} "
                        f"sbw4={_en(r,'sbw4'):.3f} ptm1={_en(r,'ptm1'):.2f}")
                    return p, h, meta, "found"

        # ── Method 2b: blind hour-by-hour scan (guaranteed fallback) ──────────
        # Always return SOMETHING if the camera has any image that day. Where the
        # CSV has a shot in this hour, aim at it (image with laser data) instead
        # of just grabbing the first file in the folder.
        log(f"method2b: blind scan h={start_hour_real}–{max_hour_real}")
        for real_h in range(start_hour_real, max_hour_real + 1):
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            folder_h = real_to_folder_h(real_h)
            if not (0 <= folder_h <= 23):
                continue   # maps to an adjacent day's folder — skip
            dt_eff   = datetime(year, month, day_n, folder_h)
            cam_folder = self._build_target_path(dt_eff) / cam_name
            exists = bc(lambda cf=cam_folder: cf.exists(), cancelled)
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            if not exists:
                continue
            best_r = best_shot_by_h.get(folder_h)
            if best_r is not None:
                tns = _row_ns(best_r)
                found_file = bc(lambda cf=cam_folder, t=tns: self._nearest_file_for_ns(cf, t),
                                cancelled)
                meta = {"ptm1": _en(best_r, "ptm1") or None,
                        "sbw4": _en(best_r, "sbw4") or None,
                        "source": "csv"}
            else:
                found_file = bc(lambda cf=cam_folder: self._any_image_from_folder(cf),
                                cancelled)
                # Nothing vouches for this frame — it is simply the first picture in an
                # hour folder, as likely to sit between shots as on one. The tile says so.
                meta = dict(_no_meta, source="blind")
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            log(f"  blind h={real_h:02d}  →  {found_file.name if found_file else 'nothing'}")
            if found_file:
                return found_file, real_h, meta, "found"

        return None, None, _no_meta, "not_found"

    def _energy_rows_for_day_cached(self, year: int, month: int, day: int) -> "list[_EnergyRow]":
        """Load (and cache) the daily energy CSV rows for the multi-day engine.
        Uses the CSV directly (no CPVA) so it works while the API is dead."""
        key = (year, month, day)
        cache = getattr(self, "_md_energy_cache", None)
        if cache is None:
            cache = self._md_energy_cache = {}
        if key not in cache:
            try:
                cache[key] = _load_energy_csv(_energy_csv_path(datetime(year, month, day)))
            except Exception:
                cache[key] = []
        return cache[key]

    def _open_pv_region_search(self, prefill_qdate: "QDate | None" = None):
        """Open the PV Region Search dialog (proactive button or fail-path popup).
        On accept, run the region-driven multi-day search."""
        cams = self._checked_cameras()
        if not cams:
            QMessageBox.information(
                self, "PV Region Search",
                "Select at least one camera first — use the Cameras… button."); return
        qd = prefill_qdate or self._cal.selectedDate()
        try:
            dlg = PVRegionSearchDialog(cams, qd, self._lab_time_cb.isChecked(), self)
        except Exception as e:
            import traceback
            self._log(f"PV Region Search error: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "PV Region Search", f"Could not open dialog:\n{e}")
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = dlg.get_config()
        self._log(f"VIEW (PV region): {len(cfg['cameras'])} cams × "
                  f"{len(cfg['days'])} day(s), {sum(len(v) for v in cfg['regions'].values())} region(s)")
        self._run_multiday_search(cfg)

    def _run_multiday_search(self, cfg: dict):
        """Run the multi-day image search for the given config and put the results on
        the wall tabs.

        cfg = {cameras: [(folder_name, label, folder)], days: [date,…],
               start_hour: int, max_hour: int, use_lab_time: bool}

        Optional PV-region mode: if cfg["regions"] is a non-empty dict
        {date: [(t_start_ns, t_end_ns), …]} the search pulls one frame per camera
        from the PEAK of cfg["primary_channel"] inside those regions instead of
        the automatic TotalPower/random-hour selection.
        """
        if not cfg["cameras"]:
            QMessageBox.information(self, "Multi-day search", "Select at least one camera."); return
        if not cfg["days"]:
            QMessageBox.information(self, "Multi-day search", "No days selected."); return

        use_lab         = cfg["use_lab_time"]
        start_hour_real = cfg["start_hour"]
        max_hour_real   = cfg["max_hour"]
        regions_by_day  = cfg.get("regions") or {}
        primary_channel = cfg.get("primary_channel")
        region_mode     = bool(regions_by_day) and bool(primary_channel)

        # Launch search in background
        cancel_evt = threading.Event()

        prog_dlg = QDialog(self)
        prog_dlg.setWindowTitle("Searching…")
        prog_dlg.setMinimumWidth(420)
        prog_dlg.setWindowFlags(prog_dlg.windowFlags() &
                                ~Qt.WindowType.WindowCloseButtonHint)
        prog_layout = QVBoxLayout(prog_dlg)
        prog_lbl = QLabel("Starting search…")
        prog_lbl.setWordWrap(True)
        _total_items = len(cfg["days"]) * len(cfg["cameras"])
        prog_bar = QProgressBar()
        prog_bar.setRange(0, _total_items)
        prog_bar.setValue(0)
        prog_bar.setFormat("%v / %m  (%p%)")
        prog_bar.setTextVisible(True)
        prog_layout.addWidget(prog_lbl)
        prog_layout.addWidget(prog_bar)
        btn_cancel_search = QPushButton("Cancel")

        def _do_cancel():
            cancel_evt.set()
            prog_lbl.setText("Cancelling… (finishing current request)")
            btn_cancel_search.setEnabled(False)

        btn_cancel_search.clicked.connect(_do_cancel)
        prog_layout.addWidget(btn_cancel_search)

        class _DoneSignal(QObject):
            done = Signal(dict)
        _sig = _DoneSignal()

        def emit_log(msg: str):
            try:
                _sig.done.emit({"_log": msg})
            except RuntimeError:
                pass

        def worker():
            # results stores (day, hour, path, meta) — path may be None for inactive/not_found days
            results: dict[str, list] = {c[0]: [] for c in cfg["cameras"]}
            done = 0
            for day in cfg["days"]:
                if cancel_evt.is_set():
                    break
                for cam_name, cam_label, _ in cfg["cameras"]:
                    if cancel_evt.is_set():
                        break
                    emit_log(f"[search] {day.strftime('%d.%m.%Y')}  {cam_label}")
                    t0 = time.perf_counter()
                    if region_mode:
                        regions_for_day = regions_by_day.get(day) \
                            or regions_by_day.get(day.isoformat()) or []
                        found_path, found_hour, meta, status = self._find_image_for_regions(
                            day, cam_name, use_lab, regions_for_day, primary_channel,
                            cancelled=cancel_evt, log_fn=emit_log)
                    else:
                        found_path, found_hour, meta, status = self._find_image_for_day_cam(
                            day, cam_name, use_lab, start_hour_real, max_hour_real,
                            cancelled=cancel_evt, log_fn=emit_log)
                    elapsed = time.perf_counter() - t0
                    # Always record an entry — path=None for inactive/not_found so user can retry
                    results[cam_name].append((day, found_hour, found_path, meta, status))
                    if found_path:
                        emit_log(f"  → found  h={found_hour:02d}  ({elapsed:.2f}s)  {found_path.name}")
                    else:
                        emit_log(f"  → {status}  ({elapsed:.2f}s)")
                    done += 1
                    try:
                        _sig.done.emit({"_progress": done,
                                        "_label": f"{day.strftime('%d.%m')} / {cam_label}"})
                    except RuntimeError:
                        pass
            try:
                _sig.done.emit({"_final": results})
            except RuntimeError:
                pass

        def on_signal(data: dict):
            if not self.isVisible():
                return
            if "_log" in data:
                self._log(data["_log"])
                return
            if "_progress" in data:
                prog_bar.setValue(data["_progress"])
                prog_lbl.setText(data["_label"])
                return
            prog_dlg.accept()
            results = data["_final"]
            total_found = sum(1 for v in results.values()
                              for _, _, p, _, _ in v if p is not None)
            if total_found == 0:
                # Nothing matched. Offer the PV-region fallback (unless this WAS
                # already a PV-region search, to avoid looping).
                if not region_mode:
                    resp = QMessageBox.question(
                        self, "Multi-day search",
                        "No images found by the automatic search.\n\n"
                        "Search by PV region instead? (Plot a PV, mark time "
                        "regions, and pull frames from the peak of each region.)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes)
                    if resp == QMessageBox.StandardButton.Yes:
                        first_day = cfg["days"][0] if cfg["days"] else None
                        qd = (QDate(first_day.year, first_day.month, first_day.day)
                              if first_day else None)
                        self._open_pv_region_search(qd)
                else:
                    QMessageBox.information(self, "PV Region Search", "No images found in the marked regions.")
                return
            # Send all found images to inline preview panel
            found_paths = sorted(
                [p for v in results.values() for _, _, p, _, _ in v if p is not None],
                key=lambda x: x.name)
            if found_paths:
                self._preview_set_files(found_paths, "Multi-day")
                # Look up PV values so the preview/info panel show them.
                def _after_energy(res: list):
                    self._energy_results = res
                    self._refresh_energy_info()
                self._energy_info.setPlainText("Loading energy data…")
                self._run_energy_lookup_async(found_paths, on_done=_after_energy)
            # The wall is what this tab is for: the days next to each other, on one
            # shared scale, in the main view. A second window used to open on top of it
            # showing the same frames as thumbnails; everything it could do that the
            # wall could not — search again, marks, rotation, undo — now lives on the
            # wall, so nothing pops up any more.
            self.fill_wall(results, cfg["cameras"])

        _sig.done.connect(on_signal)
        threading.Thread(target=worker, daemon=True).start()
        prog_dlg.exec()

    # ── COLLECT JOBS ──────────────────────────────────────────────────────────
    def _snapshot_collect_jobs(self) -> list[tuple[Path, int]]:
        jobs = []
        for c in self._picked_cams():
            folder = c.get("path")
            if folder is None: continue
            jobs.append((folder, max(0, int(c.get("qty", 1)))))
        return jobs

    # ── SMB LISTING CACHE ─────────────────────────────────────────────────────
    def _get_items_cached(self, folder: Path, debug_log=None, scan_limit: int = None):
        """
        Returns list of (ns, name, size_bytes) for all valid images in folder.
        Two-phase: (1) collect names+ns from scandir without stat(), sort by ns;
        (2) stat() only an evenly-spaced sample of MAX_SCAN_FILES items so that
        network drives don't block on thousands of individual stat calls.
        """
        def log(s: str):
            if debug_log: debug_log(s)

        key  = str(folder)
        key2 = f"{key}::limit={scan_limit or 'ALL'}"
        rec  = self._namecache.get(key2)
        if rec is not None:
            log(f"cache HIT: items={rec['count']}")
            return rec["items"]

        t0 = time.perf_counter()
        # Phase 1: collect names + timestamps only (no stat) — fast even on network drives
        names_ns = []; entries = 0
        try:
            with os.scandir(folder) as it:
                for i, e in enumerate(it):
                    if scan_limit is not None and i >= scan_limit: break
                    entries += 1
                    if not e.is_file() or not is_valid_image_file(e.name): continue
                    ns = extract_ns_from_stem(Path(e.name).stem)
                    if ns is None: continue
                    names_ns.append((ns, e.name))
        except Exception as e:
            log(f"scandir ERROR: {e}")

        names_ns.sort(key=lambda x: x[0])
        t1 = time.perf_counter()

        # Phase 2: stat() a uniform sample — limits network round-trips
        n = len(names_ns)
        limit = MAX_SCAN_FILES if scan_limit is None else min(scan_limit, MAX_SCAN_FILES)
        if n <= limit:
            sample_idx = range(n)
        else:
            # evenly spaced indices across the sorted list
            sample_idx = [round(i * (n - 1) / (limit - 1)) for i in range(limit)]

        sampled_set = set(sample_idx)
        items = []
        for i, (ns, name) in enumerate(names_ns):
            if i in sampled_set:
                try:
                    sz = (folder / name).stat().st_size
                except Exception:
                    sz = 1
            else:
                sz = 0  # not sampled — will be filtered out by threshold anyway
            items.append((ns, name, sz))

        t2 = time.perf_counter()
        sampled = len(sampled_set)
        self._namecache[key2] = {"t": t2, "items": items, "count": len(items)}
        log(f"cache MISS: scandir={t1-t0:.3f}s stat={t2-t1:.3f}s | entries={entries} valid={n} sampled={sampled} usable={len(items)}")
        return items

    def _any_image_from_folder(self, folder: Path) -> "Path | None":
        """Return any image file from folder without stat() calls — used by blind scan."""
        try:
            with os.scandir(folder) as it:
                for e in it:
                    name = e.name
                    if not name or name.startswith("."):
                        continue
                    dot = name.rfind(".")
                    if dot >= 0 and name[dot:].lower() in IMAGE_EXTS:
                        return folder / name
        except Exception:
            pass
        return None

    # ── SELECTION ALGORITHM ───────────────────────────────────────────────────
    def select_images_from_folder(
        self,
        folder: Path,
        how_many: int,
        debug: bool = False,
        debug_log=None,
    ) -> list[Path]:
        """Pick `how_many` frames out of a folder with NO archiver value to aim at.

        The blind fallback: a size threshold taken from the upper half of a sampled
        set of file sizes, the folder split into runs on a 90 s gap, and the quota
        shared out between the runs. It is what answers "show me this hour" when the
        archiver is unreachable or the camera has no TotalPower channel — the one
        selection the Shot Finder cannot make, because its engine needs a target to
        minimise the distance to.

        Four parameters (`window`, `tol_kb`, `sample_step`, `sample_near`) used to sit
        in this signature, dutifully passed by the only call site and read by nothing.
        """
        def log(msg: str):
            if debug and debug_log: debug_log(msg)

        if how_many <= 0: return []

        t0    = time.perf_counter()
        items = self._get_items_cached(folder, debug_log=debug_log if debug else None)
        if not items: log("no items"); return []

        # Threshold computed only from sampled files (sz > 0); unsampled have sz=0
        sampled_sizes = sorted(x[2] for x in items if x[2] > 0); ns = len(sampled_sizes)
        top_half = sampled_sizes[ns // 2:]
        threshold = (top_half[len(top_half) // 2] * 0.75) if top_half else 0

        # Only use files with known size (sz > 0) for selection
        full = [(ts, name) for ts, name, sz in items if sz >= threshold]
        log(f"threshold={threshold:.0f}B full={len(full)}/{len(items)}")
        if not full:
            full = [(ns, name) for ns, name, sz in items]
            log("fallback: using all items (no files above threshold)")

        if how_many >= len(full): return [folder / name for _, name in full]

        SEG_GAP_NS = 90 * 10**9
        segs: list[list] = []; cur_seg = [full[0]]
        for i in range(1, len(full)):
            if full[i][0] - full[i-1][0] > SEG_GAP_NS: segs.append(cur_seg); cur_seg = []
            cur_seg.append(full[i])
        segs.append(cur_seg)
        log(f"segments={len(segs)} sizes={[len(s) for s in segs]}")

        if how_many == 1:
            size_lup = {name: sz for ns, name, sz in items}
            def seg_avg(s): return sum(size_lup.get(nm, 0) for _, nm in s) / len(s)
            best_seg = max(segs, key=seg_avg)
            # Pick the single image with the largest file size in that segment
            best_name = max(best_seg, key=lambda x: size_lup.get(x[1], 0))[1]
            return [folder / best_name]

        total_full = len(full); alloc = []; remaining = how_many
        for i, seg in enumerate(segs):
            if i == len(segs) - 1:
                alloc.append(max(0, remaining))
            else:
                a = round(how_many * len(seg) / total_full)
                a = max(0, min(a, remaining))
                alloc.append(a); remaining -= a
        log(f"alloc={alloc}")

        chosen = []
        for seg, count in zip(segs, alloc):
            if count <= 0: continue
            if count >= len(seg): chosen.extend(folder / name for _, name in seg); continue
            idxs = [round(i * (len(seg)-1) / (count-1)) for i in range(count)] if count > 1 else [len(seg)-1]
            seen_i = set()
            for idx in idxs:
                idx = max(0, min(len(seg)-1, idx))
                if idx not in seen_i: seen_i.add(idx); chosen.append(folder / seg[idx][1])

        log(f"chosen={len(chosen)} total_time={time.perf_counter()-t0:.3f}s")
        return chosen

    # ── TEMP VIEW COPIES ──────────────────────────────────────────────────────
    def _cleanup_view_temp(self):
        try:
            for p in self._view_temp_paths:
                try:
                    if p.exists(): p.unlink()
                except: pass
            self._view_temp_paths = []
            if self._view_temp_dir:
                shutil.rmtree(self._view_temp_dir, ignore_errors=True)
                self._view_temp_dir = None
        except: pass

    def _apply_gradient_to_image(self, img: PilImage.Image, src_path: "Path | None" = None,
                                 grad_name: "str | None" = None,
                                 auto: "bool | None" = None,
                                 gamma=None,
                                 contrast: "int | None" = None,
                                 offset: "int | None" = None) -> PilImage.Image:
        """Apply the selected gradient LUT. Pass grad_name and the display settings when
        calling from a worker thread (reading a widget off the main thread is unsafe)."""
        name = grad_name if grad_name is not None else self._gradient_cb.currentText()
        lut  = GRADIENTS.get(name)
        if lut is None: return img
        if auto is None or gamma is None or contrast is None or offset is None:
            # Called without a snapshot, so this is the main thread: read the controls
            # now. Only the ones the caller left out are taken from the widgets.
            _a, _g, _c, _o = self._bc_args()
            auto = _a if auto is None else auto
            gamma = _g if gamma is None else gamma
            contrast = _c if contrast is None else contrast
            offset = _o if offset is None else offset
        arr = np.array(img)
        if arr.ndim == 3: arr = arr.mean(axis=2)
        arr = arr.astype(np.float32)
        self._log_safe(f"IMG range: min={arr.min():.0f} max={arr.max():.0f} dtype={img.mode} shape={arr.shape}")
        # Same range the preview used, so a saved/exported frame looks like what was on
        # screen — including a camera whose frames are bracketed differently.
        full_scale = (img_scale.full_scale_for_pil(src_path, img.info, img.mode)
                      if src_path is not None
                      else (img_scale.FULL_SCALE_16 if img.mode in ("I", "I;16") else 255.0))
        arr8 = _render_u8(arr, auto, full_scale, gamma, contrast, offset)
        return PilImage.fromarray(
            _lut_pixels(lut, arr8, name).astype(np.uint8), mode="RGB")

    def _make_view_copy_with_readable_name(self, src: Path) -> Path:
        if self._view_temp_dir is None:
            self._view_temp_dir = tempfile.mkdtemp(prefix="IT_view_")
            self._log(f"VIEW temp folder: {self._view_temp_dir}")
        dest_dir = Path(self._view_temp_dir)
        new_stem, reason = build_new_name(src.stem, use_prague_time=True)
        if new_stem is None:
            new_stem = src.stem.replace("-_-","_").replace("_-_","_")
        dst = dest_dir / f"{new_stem}{src.suffix}"
        if dst.exists():
            base = new_stem; i = 1
            while True:
                cand = dest_dir / f"{base}_dup{i}{src.suffix}"
                if not cand.exists(): dst = cand; break
                i += 1
        grad_name = self._gradient_cb.currentText()
        if grad_name != "Grayscale":
            try: self._apply_gradient_to_image(PilImage.open(src), src).save(dst)
            except: shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)
        self._view_temp_paths.append(dst)
        return dst

    # ── ASYNC COLLECT ─────────────────────────────────────────────────────────
    def _collect_primary_files_now(self, jobs: list[tuple[Path, int]]) -> list[Path]:
        self._log_safe(f"_collect_primary_files_now: start, {len(jobs)} jobs")
        if not jobs: return []
        files = []; t0 = time.perf_counter()

        # Derive start_ns/end_ns from the UI-selected datetime (avoids UNC path parsing)
        try:
            dt_utc = self._build_datetime()  # naive UTC datetime
            hour_start = datetime(dt_utc.year, dt_utc.month, dt_utc.day,
                                  dt_utc.hour, 0, 0, tzinfo=timezone.utc)
            hour_end   = datetime(dt_utc.year, dt_utc.month, dt_utc.day,
                                  dt_utc.hour, 59, 59, 999999, tzinfo=timezone.utc)
            query_start_ns = int(hour_start.timestamp() * 1e9)
            query_end_ns   = int(hour_end.timestamp()   * 1e9)
            self._log_safe(f"COLLECT: hour window {hour_start} – {hour_end} UTC")
        except Exception as e:
            self._log_safe(f"COLLECT: cannot build hour window — {e}, using blind scan")
            query_start_ns = None
            query_end_ns   = None

        def mk_debug_log(folder_name: str):
            return lambda s: self._log_safe(f"{folder_name}: {s}")

        def worker(folder: Path, qty: int):
            log = mk_debug_log(folder.name)
            cam_name = folder.name

            # ── Try TotalPower-guided selection first ──────────────────────────
            if query_start_ns is not None:
                tp_channel = _cam_totalpower_channel(cam_name)
                chosen = self._select_by_totalpower(
                    folder, qty, tp_channel, log, query_start_ns, query_end_ns)
                if chosen:
                    return folder, qty, chosen

            # ── Fallback: blind file-size selection ────────────────────────────
            log("TotalPower unavailable — blind file-size fallback")
            chosen = self.select_images_from_folder(
                folder, qty, debug=True, debug_log=log,
            )
            return folder, qty, chosen

        max_workers = min(24, len(jobs))
        self._log_safe(f"COLLECT: parallel scan max_workers={max_workers}")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(worker, f, q) for f, q in jobs]
            for fut in as_completed(futs):
                try:
                    folder, qty, chosen = fut.result()
                    files.extend(chosen)
                    self._log_safe(f"- {folder.name}: selected {len(chosen)}/{qty}")
                except Exception as e:
                    self._log_safe(f"COLLECT worker ERROR: {type(e).__name__}: {e}")
        self._log_safe(f"COLLECT DONE: total files = {len(files)} | total_time={time.perf_counter()-t0:.3f}s")
        return files

    def _select_by_totalpower(self, folder: Path, qty: int,
                               tp_channel: "str | None", log,
                               start_ns: int, end_ns: int) -> "list[Path]":
        """
        Use SBW4/PTM1/TotalPower to find the best laser-active period, then pick
        the qty image files whose timestamps are closest to the best shot timestamp.

        Strategy:
          1. SBW4: find best shot timestamp (highest energy) in the hour
          2. PTM1: same if SBW4 gives nothing
          3. TotalPower: pick window with highest avg power, use its center
          4. If nothing found, return [] so blind fallback takes over

        We deliberately do NOT filter files by the energy window — instead we anchor
        on the best shot timestamp and pick the nearest files.  This is robust against
        the archiver writing images slightly before or after the actual shot time.
        """
        if not tp_channel:
            return []

        log(f"  hour window: {start_ns/1e9:.0f}–{end_ns/1e9:.0f} (UTC)")

        # ── Step 1: SBW4 → PTM1: find best shot timestamp ────────────────────
        best_shot_ns: "int | None" = None
        for ref_ch in (CPVA_SBW4_CHANNEL, CPVA_SHOT_CHANNEL):
            try:
                t = cpva.best_shot_ns(start_ns, end_ns, channel=ref_ch, timeout=4.0)
                if t is not None:
                    best_shot_ns = t
                    log(f"  {ref_ch}: best shot at {t/1e9:.3f} UTC")
                    break
                log(f"  {ref_ch}: no shots in this hour")
            except Exception as e:
                log(f"  {ref_ch} error: {e}")

        # ── Step 2: TotalPower fallback ───────────────────────────────────────
        if best_shot_ns is None:
            log("  SBW4/PTM1 empty — trying TotalPower")
            try:
                tp_windows = _cpva_active_windows_ns(tp_channel, start_ns, end_ns,
                                                     timeout=4.0, debug_log=log)
                if tp_windows:
                    tp_samples = _cpva_fetch_samples(tp_channel, start_ns, end_ns, timeout=3.0)
                    def _window_avg(w):
                        ws, we = w
                        vals = []
                        for s in tp_samples:
                            t = s.get("time")
                            v = s.get("value")
                            if t is None: continue
                            if isinstance(v, list): v = v[0] if v else None
                            try:
                                if ws <= int(t) <= we:
                                    vals.append(float(v))
                            except (TypeError, ValueError):
                                pass
                        return sum(vals) / len(vals) if vals else 0.0
                    best_win = max(tp_windows, key=_window_avg)
                    best_shot_ns = (best_win[0] + best_win[1]) // 2
                    log(f"  TotalPower: best window center at {best_shot_ns/1e9:.3f} UTC")
            except Exception as e:
                log(f"  TotalPower fallback error: {e}")

        if best_shot_ns is None:
            log("  no active period found — returning []")
            return []

        # ── Correct the hour folder based on best_shot_ns ────────────────────
        # load_folders stores the first found hour for each camera (dedup by name),
        # which may differ from the hour containing the actual best shot.
        import time as _time_cf
        best_shot_dt_utc = datetime.fromtimestamp(best_shot_ns / 1e9, tz=timezone.utc)
        correct_h = best_shot_dt_utc.hour
        correct_folder = folder.parent.parent / str(correct_h) / folder.name
        if correct_folder != folder:
            _t_cf = _time_cf.perf_counter()
            _cf_exists = correct_folder.exists()
            log(f"  correct_folder.exists() took {_time_cf.perf_counter()-_t_cf:.3f}s → {_cf_exists}")
            if _cf_exists:
                log(f"  correcting folder h{folder.parent.name} → h{correct_h}")
                folder = correct_folder
            else:
                log(f"  correct_folder h{correct_h} does not exist — using original")

        # ── Step 3: fast exact-match glob → fallback full listdir ────────────
        # Filenames encode nanoseconds at the end of the stem (SOURCE_RE = r"(\d+)$").
        # Try an exact glob first — single SMB lookup, avoids listing huge directories.
        import bisect as _bisect
        import time as _time

        _t0_listdir = _time.perf_counter()

        if qty == 1:
            try:
                exact_hits = list(folder.glob(f"*{best_shot_ns}.*"))
                exact_hits = [p for p in exact_hits if is_valid_image_file(p.name)]
                if exact_hits:
                    log(f"  exact glob hit: {exact_hits[0].name} (Δ=0.0s) in {_time.perf_counter()-_t0_listdir:.3f}s")
                    return [exact_hits[0]]
            except Exception:
                pass

        try:
            raw_names = os.listdir(folder)
        except Exception as e:
            log(f"  listdir error: {e}")
            return []

        log(f"  listdir took {_time.perf_counter()-_t0_listdir:.3f}s, {len(raw_names)} entries")

        # Parse ns from every valid image filename
        items_ns: list[tuple[int, str]] = []
        for n in raw_names:
            if not is_valid_image_file(n):
                continue
            ns = extract_ns_from_stem(Path(n).stem)
            if ns is not None:
                items_ns.append((ns, n))

        if not items_ns:
            log("  folder empty after filter")
            return []

        items_ns.sort()  # sort by ns ascending
        ns_keys = [x[0] for x in items_ns]
        log(f"  listdir: {len(items_ns)} files | anchor={best_shot_ns/1e9:.3f} UTC")

        # Binary search: find insertion point for best_shot_ns
        idx = _bisect.bisect_left(ns_keys, best_shot_ns)
        idx = min(idx, len(items_ns) - 1)

        # Grab candidates around idx — ±half entries by time proximity
        half = max(qty * 4, 20)
        lo = max(0, idx - half)
        hi = min(len(items_ns), idx + half + 1)
        candidates = items_ns[lo:hi]
        candidates.sort(key=lambda x: abs(x[0] - best_shot_ns))

        if qty == 1:
            best_ns, best_name = candidates[0]
            log(f"  chosen: {best_name} (Δ={(best_ns - best_shot_ns)/1e9:.1f}s)")
            return [folder / best_name]

        # Multiple: pick nearest-in-time candidates, then rank by file size
        near = candidates[:max(qty * 4, 20)]
        try:
            near_sz = [(ns, name, (folder / name).stat().st_size) for ns, name in near]
        except Exception:
            near_sz = [(ns, name, 0) for ns, name in near]
        near_sz.sort(key=lambda x: x[2], reverse=True)
        chosen_names = [name for _, name, _ in near_sz[:qty]]
        log(f"  chosen {len(chosen_names)} files by size near anchor")
        return [folder / name for name in chosen_names]

    def _collect_primary_files_async(self, on_done):
        if self._collect_busy: return
        jobs = self._snapshot_collect_jobs()
        if not jobs:
            QMessageBox.information(self, "Info", "No folders selected."); return
        self._collect_busy = True
        self._set_busy(True)

        # Keep signal alive on self — local variable would be GC'd before thread finishes
        self._collect_sig = _CollectSignals()
        _my_sig = self._collect_sig   # capture at start — don't use self._collect_sig later

        def on_sig_done(files: list):
            if not self.isVisible():
                self._collect_busy = False
                return
            self._log(f"on_sig_done: {len(files)} files, calling on_done")
            self._collect_busy = False
            self._set_busy(False)
            self._collect_sig = None
            on_done(files)

        self._collect_sig.done.connect(on_sig_done)

        def worker():
            self._log_safe("collect worker thread: start")
            try:
                files = self._collect_primary_files_now(jobs)
            except Exception as e:
                import traceback
                self._log_safe(f"COLLECT ERROR: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                files = []
            try:
                _my_sig.done.emit(files)
            except RuntimeError:
                pass   # widget already destroyed

        threading.Thread(target=worker, daemon=True).start()

    # ── VIEW / SAVE ───────────────────────────────────────────────────────────
    def _checked_cameras(self) -> list:
        """Return [(folder_name, label, folder)] for every picked camera."""
        cams = []
        for c in self._picked_cams():
            folder = c.get("path")
            if folder is None:
                continue
            cams.append((folder.name, c.get("label") or folder.name, folder))
        return cams

    def view_primary_files(self):
        # Multi-day: more than one effective (weekday-filtered) day selected →
        # run the multi-day search engine instead of the single-day collect.
        eff_days = self._effective_days()
        if len(eff_days) > 1:
            cams = self._checked_cameras()
            if not cams:
                QMessageBox.information(self, "Info", "No cameras selected — use the Cameras… button."); return
            # Search the whole day: the engine uses the energy CSV (sbw4/ptm1) to
            # jump to the hour the laser was firing, and blind-scans the rest as a
            # fallback, so a camera active in any hour is still found.
            cfg = {
                "cameras":      cams,
                "days":         [datetime(d.year(), d.month(), d.day()).date() for d in eff_days],
                "start_hour":   0,
                "max_hour":     23,
                "use_lab_time": self._lab_time_cb.isChecked(),
            }
            self._log(f"VIEW (multi-day): {len(cams)} cams × {len(cfg['days'])} days, full-day search")
            self._run_multiday_search(cfg)
            return

        jobs      = self._snapshot_collect_jobs()
        requested = sum(qty for _, qty in jobs)
        if requested <= 0:
            QMessageBox.information(self, "Info", "No folders selected."); return
        if requested > 5:
            if QMessageBox.question(
                self, "Open many images?",
                f"You are about to open ~{requested} images.\nDo you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return

        def after_collect(files):
            self.primary_files = files
            if not files:
                QMessageBox.information(self, "Info", "No images found."); return

            # Show the files directly in the inline preview panel
            # Use the label of the first picked camera as cam_name
            picked = self._picked_cams()
            cam_name = (picked[0].get("label") or picked[0]["name"]) if picked else ""
            self._preview_set_files(files, cam_name)

            def after_energy(results: list):
                self._energy_results = results
                self._refresh_energy_info()

            self._energy_info.setPlainText("Loading energy data…")
            self._run_energy_lookup_async(files, on_done=after_energy)

        self._collect_primary_files_async(after_collect)

    def _run_energy_lookup_async(self, files: list[Path], on_done=None):
        """Look up energy data for files in a background thread, then update UI."""
        self._energy_sig = _CollectSignals()
        _sig = self._energy_sig  # local ref — prevents GC if called again

        def on_results(results: list):
            if on_done is not None:
                on_done(results)
            else:
                self._energy_results = results
                self._refresh_energy_info()

        _sig.done.connect(on_results)

        def worker():
            try:
                print("ENERGY worker: start")
                results = self._lookup_energy_for_files(files)
                print(f"ENERGY worker: done, {len(results)} results")
                _sig.done.emit(results)
            except Exception as e:
                import traceback
                print(f"Energy lookup error: {e}\n{traceback.format_exc()}")
                _sig.done.emit([])

        threading.Thread(target=worker, daemon=True).start()

    def save_primary_files_as(self):
        def after_collect(files):
            self._log(f"after_collect: start, {len(files)} files")
            self.primary_files = files
            if not files:
                QMessageBox.information(self, "Info", "No images selected yet."); return
            initial_dir = str(self._last_save_dir) if self._last_save_dir else str(Path.home())
            dest = QFileDialog.getExistingDirectory(self, "Select destination folder", initial_dir)
            if not dest: return
            self._last_save_dir = Path(dest)
            dest_path = Path(dest)

            # Snapshot UI state on the main thread; the energy lookup + copy /
            # annotate loop runs in a worker — network I/O, PNG encode and SMB
            # copies used to freeze the whole UI here.
            annotate  = self._cb_annotate.isChecked()
            grad_name = self._gradient_cb.currentText()
            auto, gamma, contrast, offset = self._bc_args()
            sel_cols  = self._pv_visible_cols()

            self._save_as_sig = _CollectSignals()
            _sig = self._save_as_sig  # local ref — prevents GC if called again

            def on_save_done(payload: list):
                QMessageBox.information(self, "Done", payload[0] if payload else "Done")

            _sig.done.connect(on_save_done)
            self._log(f"SAVE AS: saving {len(files)} files to {dest_path} in background…")

            def worker():
                copied = 0; skipped_already = 0; skipped_nomatch = 0; annotated = 0
                energy_map: dict[str, tuple] = {}   # path -> (match, before, after)
                if annotate:
                    # Pre-warm the shared day cache in parallel so the fresh
                    # per-file lookup below is pure in-memory bisects.
                    try:
                        chans = [CPVA_CHANNEL_MAP[c] for c in sel_cols if c in CPVA_CHANNEL_MAP]
                        dkeys = {cpva.date_key_for_ns(ns) for ns in
                                 (extract_ns_from_stem(p.stem) for p in files) if ns}
                        if chans and dkeys:
                            cpva.warm_days(chans, dkeys, timeout=CPVA_HTTP_TIMEOUT)
                    except Exception:
                        pass
                    # Always do a fresh lookup — results may be stale or from different files
                    for entry in self._lookup_energy_for_files(files):
                        path, match, before, after = entry[0], entry[1], entry[2], entry[3]
                        energy_map[str(path)] = (match, before, after)

                try:
                    _copy_meta_fn = _get_slider_module()._copy_metadata_into_png
                except Exception:
                    _copy_meta_fn = None

                for src in files:
                    try:
                        if not src.exists() or not is_valid_image_file(src.name): continue
                        new_stem, reason = build_new_name(src.stem, use_prague_time=True)
                        if new_stem is None and reason == "already_converted":
                            new_stem = src.stem.replace("-_-","_").replace("_-_","_")
                            skipped_already += 1
                        if new_stem is None and reason == "no_trailing_number":
                            new_stem = src.stem; skipped_nomatch += 1
                        if new_stem is None: new_stem = src.stem

                        # Annotated saves always go to PNG (bar is drawn)
                        if annotate:
                            dst = dest_path / f"{new_stem}.png"
                        else:
                            dst = dest_path / f"{new_stem}{src.suffix}"

                        if dst.exists():
                            base = Path(dst).stem; ext = dst.suffix; i = 1
                            while True:
                                cand = dest_path / f"{base}_dup{i}{ext}"
                                if not cand.exists(): dst = cand; break
                                i += 1

                        if annotate:
                            # Apply gradient first to a temp file if needed, then annotate
                            match, before, after = energy_map.get(str(src), (None, None, None))
                            ns = extract_ns_from_stem(src.stem)
                            img_ts_ns = ns if ns is not None else 0
                            if grad_name != "Grayscale":
                                # Save gradient-applied version to temp, then annotate from temp
                                import tempfile as _tf
                                with _tf.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                                    tmp_path = Path(tmp.name)
                                try:
                                    self._apply_gradient_to_image(
                                        PilImage.open(src), src, grad_name=grad_name,
                                        auto=auto, gamma=gamma, contrast=contrast,
                                        offset=offset).save(tmp_path)
                                    _annotate_image_with_energy(
                                        tmp_path, dst, match, before, after,
                                        img_ts_ns, sel_cols)
                                finally:
                                    try: tmp_path.unlink()
                                    except: pass
                            else:
                                _annotate_image_with_energy(
                                    src, dst, match, before, after,
                                    img_ts_ns, sel_cols)
                            if _copy_meta_fn is not None:
                                try: _copy_meta_fn(src, dst, save_txt=False)
                                except Exception: pass
                            annotated += 1
                        else:
                            if grad_name != "Grayscale":
                                try:
                                    self._apply_gradient_to_image(
                                        PilImage.open(src), src, grad_name=grad_name,
                                        auto=auto, gamma=gamma, contrast=contrast,
                                        offset=offset).save(dst)
                                    if _copy_meta_fn is not None:
                                        try: _copy_meta_fn(src, dst, save_txt=False)
                                        except Exception: pass
                                except: shutil.copy2(src, dst)
                            else:
                                shutil.copy2(src, dst)

                        copied += 1
                    except Exception as e:
                        self._log_safe(f"SAVE ERROR: {src} -> {type(e).__name__}: {e}")

                msg = f"Copied {copied} files to:\n{dest_path}"
                if annotated:
                    msg += f"\n- {annotated} files annotated with energy data"
                if skipped_already:
                    msg += f"\n- {skipped_already} files already in final format (kept name)"
                if skipped_nomatch:
                    msg += f"\n- {skipped_nomatch} files had no trailing ns timestamp (kept name)"
                _sig.done.emit([msg])

            threading.Thread(target=worker, daemon=True).start()
        self._collect_primary_files_async(after_collect)


    # ── INFO ──────────────────────────────────────────────────────────────────
    def show_info(self):
        dlg = QDialog(self); dlg.setWindowTitle("Info"); dlg.resize(720, 420)
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit(); txt.setReadOnly(True); txt.setPlainText(load_readme_text())
        lay.addWidget(txt)
        btn = QPushButton("Close"); btn.clicked.connect(dlg.accept); lay.addWidget(btn)
        dlg.exec()

    # ── MEMORY A/B ────────────────────────────────────────────────────────────
    def _save_to_memory(self, slot: str = None):
        # Use the currently displayed preview image if available
        if self._preview_paths and 0 <= self._preview_idx < len(self._preview_paths):
            p = self._preview_paths[self._preview_idx]
            self._log(f"MEMORY: using currently displayed preview [{self._preview_idx + 1}/{len(self._preview_paths)}]")
            self._do_save_to_memory_slot(p, slot)
            return
        QMessageBox.information(self, "Memory",
            "No image displayed in preview. Use View first, then navigate to the desired image.")

    def _send_to_workshop(self):
        """Collect primary files and send all of them to Workshop tab."""
        wk = getattr(self, "_workshop_ref", None)
        if wk is None:
            return

        # The Workshop measures and builds its histogram from the picture it is given,
        # so it gets the plain absolute mapping — never this tab's Auto passes,
        # Contrast, Brightness or Gamma, which would hand it a display curve instead
        # of data.
        def _send_one(src: Path):
            from PIL import Image as _PilImg
            import numpy as _np
            pil = _PilImg.open(str(src))
            if pil.mode in ("I", "I;16"):
                arr_f = _np.array(pil, dtype=_np.float32)
            else:
                arr_f = _np.array(pil.convert("L"), dtype=_np.float32)
            full_scale = img_scale.full_scale_for_pil(src, pil.info, pil.mode)
            arr8 = _render_u8(arr_f, False, full_scale, None)
            cam_name = src.parent.name
            label = f"{cam_name}  |  {src.name}"
            wk.receive_image(arr8, label, source_path=src)

        def after_collect(files):
            if not files:
                QMessageBox.information(self, "Workshop", "No image selected."); return
            sent = 0
            errors = []
            for src in files:
                try:
                    _send_one(src)
                    sent += 1
                except Exception as e:
                    errors.append(f"{src.name}: {e}")
            self._log(f"WORKSHOP: sent {sent} file(s)")
            if errors:
                QMessageBox.warning(self, "Workshop",
                    f"Sent {sent} file(s), {len(errors)} error(s):\n" + "\n".join(errors))

        self._collect_primary_files_async(after_collect)

    def _do_save_to_memory_slot(self, p: Path, slot: str = None):
        def readable_name(path: Path) -> str:
            new_stem, reason = build_new_name(path.stem,
                                              use_prague_time=not self._lab_time_cb.isChecked())
            self._log(f"READABLE_NAME: stem={path.stem!r} -> new_stem={new_stem!r} reason={reason!r}")
            if new_stem:
                m = re.search(r"(\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d+)$", new_stem)
                if m: return m.group(1)
            return path.stem

        target = slot if slot is not None else ("A" if self._mem_a is None else "B")
        if target == "A":
            self._mem_a = p; self._lbl_mem_a.setText(f"A: {readable_name(p)}")
            self._log(f"MEMORY A: {p}")
        else:
            self._mem_b = p; self._lbl_mem_b.setText(f"B: {readable_name(p)}")
            self._log(f"MEMORY B: {p}")
        if self._mem_a is not None and self._mem_b is not None:
            self._btn_compare.setEnabled(True)

    def _clear_slot(self, slot: str):
        if slot == "A":
            self._mem_a = None; self._lbl_mem_a.setText("A: —"); self._log("MEMORY A cleared")
        else:
            self._mem_b = None; self._lbl_mem_b.setText("B: —"); self._log("MEMORY B cleared")
        if self._mem_a is None or self._mem_b is None:
            self._btn_compare.setEnabled(False)

    def _clear_memory(self, win=None):
        """Clear both memory slots. Optionally close the compare dialog."""
        self._clear_slot("A"); self._clear_slot("B")
        self._log("MEMORY cleared")
        if win:
            try: win.accept()
            except: pass

    # ── COMPARE ───────────────────────────────────────────────────────────────
    def _align_images(self, arr_a: np.ndarray, arr_b: np.ndarray) -> np.ndarray:
        """Phase correlation alignment, max ±15px. Identical to original."""
        def to_gray(arr):
            if arr.ndim == 3: return arr.mean(axis=2).astype(np.float32)
            return arr.astype(np.float32)
        ga = to_gray(arr_a); gb = to_gray(arr_b)
        fa = np.fft.fft2(ga); fb = np.fft.fft2(gb)
        cross = fa * np.conj(fb); denom = np.abs(cross); denom[denom == 0] = 1
        ir = np.fft.ifft2(cross / denom).real
        h, w = ir.shape; MAX_SHIFT = 15; best_val = -np.inf; best_dy = best_dx = 0
        for dy in range(-MAX_SHIFT, MAX_SHIFT + 1):
            for dx in range(-MAX_SHIFT, MAX_SHIFT + 1):
                val = ir[dy % h, dx % w]
                if val > best_val: best_val = val; best_dy = dy; best_dx = dx
        self._log(f"ALIGN: shift dy={best_dy} dx={best_dx} (peak={best_val:.2f})")
        if best_dy == 0 and best_dx == 0: return arr_b
        return np.roll(arr_b, (best_dy, best_dx), axis=(0, 1))

    def _compare_memory(self):
        if self._mem_a is None or self._mem_b is None:
            QMessageBox.information(self, "Compare", "Save two images to memory first."); return

        self._set_busy(True)
        self._compare_sig = _CompareSignals()
        self._compare_sig.done.connect(self._show_compare_window)
        def _on_compare_error(msg: str):
            if self.isVisible():
                QMessageBox.critical(self, "Compare error", msg)
        self._compare_sig.error.connect(_on_compare_error)

        def worker():
            try:
                self._log(f"COMPARE: loading A={self._mem_a.name}")
                self._log(f"COMPARE: loading B={self._mem_b.name}")
                img_a = PilImage.open(self._mem_a); img_b = PilImage.open(self._mem_b)
                arr_a = np.array(img_a).astype(np.float32)
                arr_b = np.array(img_b).astype(np.float32)
                if arr_a.ndim == 3: arr_a = arr_a.mean(axis=2)
                if arr_b.ndim == 3: arr_b = arr_b.mean(axis=2)
                if arr_a.shape != arr_b.shape:
                    self._compare_sig.error.emit(f"Images have different dimensions:\nA: {arr_a.shape}\nB: {arr_b.shape}")
                    return
                arr_b_aligned = self._align_images(arr_a, arr_b)
                diff_ab = arr_a - arr_b_aligned
                diff_ba = arr_b_aligned - arr_a

                def norm(d):
                    d_c = np.clip(d, 0, None); mx = float(d_c.max())
                    if mx == 0: return np.zeros_like(d, dtype=np.uint8)
                    return (d_c / mx * 255).astype(np.uint8)

                img_disp_ab = PilImage.fromarray(norm(diff_ab))
                img_disp_ba = PilImage.fromarray(norm(diff_ba))
                self._compare_sig.done.emit(img_disp_ab, img_disp_ba, diff_ab, diff_ba)
            except Exception as e:
                self._compare_sig.error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _show_compare_window(
        self,
        img_ab: PilImage.Image,
        img_ba: PilImage.Image,
        diff_ab_raw: np.ndarray,
        diff_ba_raw: np.ndarray,
    ):
        def readable_label(p: Path) -> str:
            new_stem, _ = build_new_name(p.stem,
                                         use_prague_time=not self._lab_time_cb.isChecked())
            if new_stem:
                m = re.search(r"(\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d+)$", new_stem)
                if m: return m.group(1).replace("__", "_")
            return p.stem

        label_a = readable_label(self._mem_a)
        label_b = readable_label(self._mem_b)
        stem_a  = Path(label_a).stem
        stem_b  = Path(label_b).stem

        win = QDialog(self)
        win.setWindowTitle(f"Comparison: {label_a}  vs  {label_b}")
        win.resize(1100, 700)

        MAX_W = MAX_H = 460
        bright_ab = [1.0]; bright_ba = [1.0]

        def make_qpixmap(diff_raw: np.ndarray, brightness: float) -> QPixmap:
            d   = np.clip(diff_raw, 0, None); mx = float(d.max()) or 1.0
            arr = np.clip(d / mx * 255.0 * brightness, 0, 255).astype(np.uint8)
            img = PilImage.fromarray(arr)
            img.thumbnail((MAX_W, MAX_H), PilImage.Resampling.LANCZOS)
            qimg = QImage(img.tobytes(), img.width, img.height,
                          img.width, QImage.Format.Format_Grayscale8)
            return QPixmap.fromImage(qimg)

        body = QHBoxLayout()

        # A-B column
        col_ab = QFrame(); col_ab.setFrameShape(QFrame.Shape.StyledPanel)
        cl_ab  = QVBoxLayout(col_ab)
        cl_ab.addWidget(QLabel("<b>A \u2212 B</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        lbl_ab = QLabel(); lbl_ab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_ab.setPixmap(make_qpixmap(diff_ab_raw, 1.0)); cl_ab.addWidget(lbl_ab, 1)
        ctrl_ab = QHBoxLayout(); ctrl_ab.addWidget(QLabel("Brightness:"))
        sl_ab = QSlider(Qt.Orientation.Horizontal); sl_ab.setRange(1, 1000); sl_ab.setValue(100)
        lbl_ab_val = QLabel("1.00x"); lbl_ab_val.setFixedWidth(46)
        def update_ab(v):
            b = v / 100.0; bright_ab[0] = b; lbl_ab_val.setText(f"{b:.2f}x")
            lbl_ab.setPixmap(make_qpixmap(diff_ab_raw, b))
        sl_ab.valueChanged.connect(update_ab)
        btn_auto_ab = QPushButton("Auto"); btn_auto_ab.setFixedWidth(46)
        btn_auto_ab.clicked.connect(lambda: sl_ab.setValue(100))
        ctrl_ab.addWidget(sl_ab, 1); ctrl_ab.addWidget(lbl_ab_val); ctrl_ab.addWidget(btn_auto_ab)
        cl_ab.addLayout(ctrl_ab); body.addWidget(col_ab, 1)

        # B-A column
        col_ba = QFrame(); col_ba.setFrameShape(QFrame.Shape.StyledPanel)
        cl_ba  = QVBoxLayout(col_ba)
        cl_ba.addWidget(QLabel("<b>B \u2212 A</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        lbl_ba = QLabel(); lbl_ba.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_ba.setPixmap(make_qpixmap(diff_ba_raw, 1.0)); cl_ba.addWidget(lbl_ba, 1)
        ctrl_ba = QHBoxLayout(); ctrl_ba.addWidget(QLabel("Brightness:"))
        sl_ba = QSlider(Qt.Orientation.Horizontal); sl_ba.setRange(1, 1000); sl_ba.setValue(100)
        lbl_ba_val = QLabel("1.00x"); lbl_ba_val.setFixedWidth(46)
        def update_ba(v):
            b = v / 100.0; bright_ba[0] = b; lbl_ba_val.setText(f"{b:.2f}x")
            lbl_ba.setPixmap(make_qpixmap(diff_ba_raw, b))
        sl_ba.valueChanged.connect(update_ba)
        btn_auto_ba = QPushButton("Auto"); btn_auto_ba.setFixedWidth(46)
        btn_auto_ba.clicked.connect(lambda: sl_ba.setValue(100))
        ctrl_ba.addWidget(sl_ba, 1); ctrl_ba.addWidget(lbl_ba_val); ctrl_ba.addWidget(btn_auto_ba)
        cl_ba.addLayout(ctrl_ba); body.addWidget(col_ba, 1)

        def save_png(diff_raw: np.ndarray, brightness: float, label: str):
            default_name = f"diff_{label}_{stem_a}_vs_{stem_b}.png"
            dst, _ = QFileDialog.getSaveFileName(
                win, f"Save {label}", default_name, "PNG Images (*.png)")
            if not dst: return
            d    = np.clip(diff_raw, 0, None); mx = float(d.max()) or 1.0
            arr8 = np.clip(d / mx * 255.0 * brightness, 0, 255).astype(np.uint8)
            PilImage.fromarray(arr8).save(dst)
            self._log(f"SAVED {label}: {dst}")

        def save_both():
            dest = QFileDialog.getExistingDirectory(win, "Select folder for both results")
            if not dest: return
            dest_path = Path(dest)
            for diff_raw, brightness, label in (
                (diff_ab_raw, bright_ab[0], "A-B"),
                (diff_ba_raw, bright_ba[0], "B-A"),
            ):
                d    = np.clip(diff_raw, 0, None); mx = float(d.max()) or 1.0
                arr8 = np.clip(d / mx * 255.0 * brightness, 0, 255).astype(np.uint8)
                PilImage.fromarray(arr8).save(dest_path / f"diff_{label}_{stem_a}_vs_{stem_b}.png")
                self._log(f"SAVED {label}: {dest_path}")
            QMessageBox.information(win, "Saved", f"Both saved to:\n{dest_path}")

        btn_row = QHBoxLayout()
        btn_row.addWidget(QPushButton("Save A\u2212B", clicked=lambda: save_png(diff_ab_raw, bright_ab[0], "A-B")))
        btn_row.addWidget(QPushButton("Save B\u2212A", clicked=lambda: save_png(diff_ba_raw, bright_ba[0], "B-A")))
        btn_row.addWidget(QPushButton("Save both",       clicked=save_both))
        btn_row.addStretch(1)
        btn_row.addWidget(QPushButton("Clear memory A/B", clicked=lambda: self._clear_memory(win)))
        btn_row.addWidget(QPushButton("Close",            clicked=win.accept))

        main_lay = QVBoxLayout(win)
        main_lay.addWidget(QLabel(f"<b>A:</b> {label_a}   <b>|</b>   <b>B:</b> {label_b}"))
        main_lay.addLayout(body, 1)
        main_lay.addLayout(btn_row)
        self._set_busy(False)
        win.exec()



# ── MULTI-DAY SETUP DIALOG ────────────────────────────────────────────────────
class _MultiDaySetupDialog(QDialog):
    """
    Multi-day search setup.
    Cameras: taken from the checked rows in the main table (passed in as all_cams).
    Calendar: single calendar where user sets From/To range and weekday filter;
              selected days are highlighted with a blue background.
    """
    def __init__(self, all_cams: list, current_qdate, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Multi-day search")
        self.setMinimumWidth(640)
        self.resize(700, 560)
        root = QHBoxLayout(self)
        root.setSpacing(10)

        # ── Left: calendar + weekday filter ──────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(6)

        # Range header: from/to spinboxes above calendar
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("From:"))
        self._from_date_lbl = QLabel()
        self._from_date_lbl.setStyleSheet("font-weight:bold;")
        range_row.addWidget(self._from_date_lbl)
        range_row.addSpacing(16)
        range_row.addWidget(QLabel("To:"))
        self._to_date_lbl = QLabel()
        self._to_date_lbl.setStyleSheet("font-weight:bold;")
        range_row.addWidget(self._to_date_lbl)
        range_row.addStretch()
        left.addLayout(range_row)

        # Instruction label
        hint = QLabel("Left-click: set From  |  Shift+click: set To  |  Right-click: pin/unpin specific day")
        hint.setStyleSheet("font-size:10px;color:#888;")
        left.addWidget(hint)

        # Calendar — full styling matching main calendar
        self._cal = _NoScrollCalendar()
        self._cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self._cal.setGridVisible(True)
        self._cal.setNavigationBarVisible(True)
        self._cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.ISOWeekNumbers)
        self._cal.setMinimumWidth(360)

        # Header row: day names — bold, bigger, visible on light background
        hf = QTextCharFormat()
        hf.setForeground(QColor("#222"))
        hf.setFontWeight(QFont.Weight.Bold)
        hf.setFontPointSize(10)
        self._cal.setHeaderTextFormat(hf)

        # Week-number column: bold
        wf_wk = QTextCharFormat()
        wf_wk.setFontWeight(QFont.Weight.Bold)
        wf_wk.setForeground(QColor("#555"))

        # Weekday formats
        wf = QTextCharFormat()
        wf.setForeground(QColor("#111"))
        for day in [Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                    Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday]:
            self._cal.setWeekdayTextFormat(day, wf)
        wf_we = QTextCharFormat()
        wf_we.setForeground(QColor("#cc0000"))
        for day in [Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday]:
            self._cal.setWeekdayTextFormat(day, wf_we)

        self._cal.setStyleSheet("""
        QCalendarWidget QWidget { background: #f6f6f6; color: #111; }
        QCalendarWidget QAbstractItemView {
            background: #fcfcfc; color: #111;
            selection-background-color: #2d7dff; selection-color: #fff;
            alternate-background-color: #f0f0f0; gridline-color: #d0d0d0; }
        QCalendarWidget QTableView {
            background: #fcfcfc;
            selection-background-color: #2d7dff; selection-color: #fff;
            gridline-color: #d0d0d0; outline: 0; }
        QCalendarWidget QHeaderView {
            background: #e8e8e8; }
        QCalendarWidget QHeaderView::section {
            background: #e8e8e8; color: #222;
            font-weight: bold; font-size: 10pt;
            padding: 3px 0px; border: none;
            border-bottom: 1px solid #bbb; }
        QCalendarWidget QToolButton {
            background: #efefef; border: 1px solid #c8c8c8;
            padding: 4px 8px; border-radius: 4px; color: #111;
            font-size: 10pt; font-weight: bold; }
        QCalendarWidget QSpinBox, QCalendarWidget QComboBox {
            background: #fff; border: 1px solid #c8c8c8;
            padding: 2px 6px; color: #111; font-size: 10pt; font-weight: bold; }
        QCalendarWidget QWidget#qt_calendar_navigationbar {
            background: #e4e4e4; }
        QCalendarWidget QAbstractItemView:enabled { color: #111; }
        """)
        left.addWidget(self._cal, 1)

        # Install border delegate AFTER setStyleSheet — Qt resets item delegates on style change
        _cal_view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        self._cal_view = _cal_view
        if _cal_view:
            self._border_delegate = _CalBorderDelegate(_cal_view)
            _cal_view.setItemDelegate(self._border_delegate)
            _cal_view.viewport().installEventFilter(self)

        # Weekday filter
        left.addWidget(_section_label("Days of week"))
        wd_row = QHBoxLayout()
        self._wd_checks: list[QCheckBox] = []
        for i, label in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            cb = QCheckBox(label)
            cb.setChecked(i < 5)  # Mon–Fri default
            cb.setStyleSheet(_CHECKBOX_STYLE)
            cb.stateChanged.connect(self._refresh_highlight)
            self._wd_checks.append(cb)
            wd_row.addWidget(cb)
        wd_row.addStretch()
        left.addLayout(wd_row)

        # Day count label
        self._day_count_lbl = QLabel()
        self._day_count_lbl.setStyleSheet("font-size:10px;color:#888;")
        left.addWidget(self._day_count_lbl)

        root.addLayout(left, 3)

        # ── Right: cameras + settings ─────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(6)

        right.addWidget(_section_label("Cameras (from main table)"))
        cam_scroll = QScrollArea()
        cam_scroll.setWidgetResizable(True)
        cam_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        cam_inner = QWidget()
        cam_layout = QVBoxLayout(cam_inner)
        cam_layout.setSpacing(2)
        cam_layout.setContentsMargins(4, 4, 4, 4)
        self._cam_checks: list[tuple[QCheckBox, str, str, Path]] = []
        for folder_name, cam_label, folder_path in all_cams:
            cb = QCheckBox(f"{cam_label}")
            cb.setToolTip(folder_name)
            cb.setChecked(True)   # pre-check all — mirrors the main table selection
            cb.setStyleSheet(_CHECKBOX_STYLE)
            self._cam_checks.append((cb, folder_name, cam_label, folder_path))
            cam_layout.addWidget(cb)
        cam_layout.addStretch()
        cam_scroll.setWidget(cam_inner)
        right.addWidget(cam_scroll, 1)

        sel_row = QHBoxLayout()
        btn_all = QPushButton("All"); btn_all.setFixedWidth(50)
        btn_none = QPushButton("None"); btn_none.setFixedWidth(50)
        btn_all.clicked.connect(lambda: [c[0].setChecked(True) for c in self._cam_checks])
        btn_none.clicked.connect(lambda: [c[0].setChecked(False) for c in self._cam_checks])
        sel_row.addWidget(btn_all); sel_row.addWidget(btn_none); sel_row.addStretch()
        right.addLayout(sel_row)

        right.addWidget(_hsep())
        right.addWidget(_section_label("Hour fallback (no CSV data)"))

        hour_row = QHBoxLayout()
        hour_row.addWidget(QLabel("Start:"))
        self._start_hour_sb = QSpinBox()
        self._start_hour_sb.setRange(0, 23)
        self._start_hour_sb.setValue(9)
        self._start_hour_sb.setToolTip(
            "Fallback start hour (real Prague time) used when no CSV data found for the day.\n"
            "If CSV data exists, it overrides this.")
        hour_row.addWidget(self._start_hour_sb)
        hour_row.addSpacing(10)
        hour_row.addWidget(QLabel("Max:"))
        self._max_hour_sb = QSpinBox()
        self._max_hour_sb.setRange(0, 23)
        self._max_hour_sb.setValue(19)
        hour_row.addWidget(self._max_hour_sb)
        hour_row.addStretch()
        right.addLayout(hour_row)

        right.addWidget(_hsep())
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        right.addWidget(btns)

        root.addLayout(right, 2)

        # ── State ─────────────────────────────────────────────────────────────
        # from/to stored as QDate
        self._from_qdate = current_qdate.addDays(-14)
        self._to_qdate   = current_qdate
        self._selecting_from = True   # next click sets From; Shift+click sets To
        self._pinned_days: set = set()  # individually right-clicked dates

        self._cal.clicked.connect(self._on_cal_clicked)
        self._cal.setSelectedDate(self._from_qdate)
        self._refresh_labels()
        self._refresh_highlight()

    def _on_cal_clicked(self, qdate):
        mods = QApplication.keyboardModifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            # Shift+click → set To
            if qdate < self._from_qdate:
                self._from_qdate, self._to_qdate = qdate, self._from_qdate
            else:
                self._to_qdate = qdate
        else:
            # Plain click → set From; if From > current To, reset To = From
            self._from_qdate = qdate
            if self._to_qdate < qdate:
                self._to_qdate = qdate
        self._refresh_labels()
        self._refresh_highlight()

    def _refresh_labels(self):
        self._from_date_lbl.setText(
            self._from_qdate.toString("dd.MM.yyyy"))
        self._to_date_lbl.setText(
            self._to_qdate.toString("dd.MM.yyyy"))

    def _refresh_highlight(self):
        """Colour range days blue, pinned days orange, clear everything else."""
        self._cal.setDateTextFormat(QDate(), QTextCharFormat())

        allowed_wd = {i for i, cb in enumerate(self._wd_checks) if cb.isChecked()}
        fmt_range = QTextCharFormat()
        fmt_range.setBackground(QColor("#3a6fcf"))
        fmt_range.setForeground(QColor("#ffffff"))
        fmt_pinned = QTextCharFormat()
        fmt_pinned.setBackground(QColor("#c06000"))
        fmt_pinned.setForeground(QColor("#ffffff"))

        range_days = self._compute_range_days(allowed_wd)
        for d in range_days:
            self._cal.setDateTextFormat(QDate(d.year, d.month, d.day), fmt_range)
        for d in self._pinned_days:
            self._cal.setDateTextFormat(QDate(d.year, d.month, d.day), fmt_pinned)

        # From/To: border přes delegate (zelený/červený obrys, neclashuje s výběrem dnů)
        if hasattr(self, "_border_delegate"):
            self._border_delegate.set_bounds(self._from_qdate, self._to_qdate)
            if self._cal_view:
                self._cal_view.viewport().update()

        total = len(set(range_days) | self._pinned_days)
        self._day_count_lbl.setText(
            f"{total} days selected  ({len(range_days)} range + {len(self._pinned_days)} pinned)"
            if self._pinned_days else f"{total} days selected")

    def _compute_range_days(self, allowed_wd=None) -> list:
        if allowed_wd is None:
            allowed_wd = {i for i, cb in enumerate(self._wd_checks) if cb.isChecked()}
        fd = self._from_qdate
        td = self._to_qdate
        from_date = datetime(fd.year(), fd.month(), fd.day()).date()
        to_date   = datetime(td.year(), td.month(), td.day()).date()
        days = []
        cur = from_date
        while cur <= to_date:
            if cur.weekday() in allowed_wd:
                days.append(cur)
            cur += timedelta(days=1)
        return days

    def _compute_days(self, allowed_wd=None) -> list:
        return sorted(set(self._compute_range_days(allowed_wd)) | self._pinned_days)

    def _cal_date_at(self, pos) -> "QDate | None":
        """Convert a viewport position to the calendar QDate at that cell."""
        if not self._cal_view:
            return None
        idx = self._cal_view.indexAt(pos)
        if not idx.isValid():
            return None
        row, col = idx.row(), idx.column()
        # Qt calendar model layout:
        #   row 0    = day-name header (Mon/Tue/...) — not a data row
        #   col 0    = week-number column (ISOWeekNumbers) — not a day column
        #   col 1..7 = Mon..Sun
        data_row = row - 1
        day_col  = col - 1
        if data_row < 0 or day_col < 0 or day_col > 6:
            return None
        first = QDate(self._cal.yearShown(), self._cal.monthShown(), 1)
        start_offset = first.dayOfWeek() - 1  # Mon=0, Tue=1 … Sun=6
        d = first.addDays(data_row * 7 + day_col - start_offset)
        return d if d.isValid() else None

    def _toggle_pinned_day(self, qdate: QDate):
        from datetime import date as _date
        d = _date(qdate.year(), qdate.month(), qdate.day())
        if d in self._pinned_days:
            self._pinned_days.discard(d)
        else:
            self._pinned_days.add(d)
        self._refresh_highlight()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        cal_view = getattr(self, "_cal_view", None)
        if (cal_view and obj is cal_view.viewport()
                and event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.RightButton):
            qdate = self._cal_date_at(event.pos())
            if qdate is not None:
                self._toggle_pinned_day(qdate)
            return True
        return super().eventFilter(obj, event)

    def get_config(self) -> dict:
        cameras = [(fn, lbl, fp)
                   for cb, fn, lbl, fp in self._cam_checks if cb.isChecked()]
        return {
            "cameras":    cameras,
            "days":       self._compute_days(),
            "start_hour": self._start_hour_sb.value(),
            "max_hour":   self._max_hour_sb.value(),
            "use_lab_time": False,
        }

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet("font-size:10px;color:#888;font-weight:700;letter-spacing:1px;")
    return lbl

# ── PV-REGION SEARCH ──────────────────────────────────────────────────────────
# Reverse of cpva.CHANNEL_MAP: full archiver channel → short preset label.
_PV_PRESET_LABELS: dict[str, str] = {ch: name.upper()
                                     for name, ch in CPVA_CHANNEL_MAP.items()}
# Cache of every archiver channel name (populated once by the Browse dialog).
_PV_CHANNEL_CACHE: "list[str] | None" = None
_PV_REGION_COLORS = ["#C62828", "#2E7D32", "#EF6C00", "#6A1B9A",
                     "#00838F", "#AD1457", "#1565C0", "#37474F"]


class _PVBrowseDialog(QDialog):
    """Filterable list of every archiver channel (live discovery via
    cpva.fetch_channels('**')). Returns the selected channel names."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Browse PVs")
        self.resize(460, 520)
        lay = QVBoxLayout(self)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Type to filter channels…")
        self._filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self._list, 1)

        self._status = QLabel("Loading channels…")
        self._status.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(self._status)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        class _Sig(QObject):
            done = Signal(object)
        self._sig = _Sig()
        self._sig.done.connect(self._on_channels)

        self._all: list[str] = []
        if _PV_CHANNEL_CACHE is not None:
            self._on_channels(_PV_CHANNEL_CACHE)
        else:
            threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        global _PV_CHANNEL_CACHE
        try:
            chans = cpva.fetch_channels("**")
        except Exception as e:
            try:
                self._sig.done.emit(e)
            except RuntimeError:
                pass
            return
        _PV_CHANNEL_CACHE = sorted(chans)
        try:
            self._sig.done.emit(_PV_CHANNEL_CACHE)
        except RuntimeError:
            pass

    def _on_channels(self, payload):
        if isinstance(payload, Exception):
            self._status.setText(f"Failed to load channels: {payload}")
            return
        self._all = list(payload)
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str):
        text = (text or "").strip().lower()
        self._list.clear()
        shown = 0
        for ch in self._all:
            if text and text not in ch.lower():
                continue
            self._list.addItem(QListWidgetItem(ch))
            shown += 1
            if shown >= 500:
                break
        self._status.setText(f"{shown} shown / {len(self._all)} channels"
                             + ("  (capped at 500 — refine filter)" if shown >= 500 else ""))

    def selected_channels(self) -> list[str]:
        return [it.text() for it in self._list.selectedItems()]


class PVRegionSearchDialog(QDialog):
    """Manual image search: plot one or more PV time-series for a day, drag to
    mark time regions, then pull one camera frame per region from the PEAK of the
    primary PV inside each region.  Modelled on the Spectra tab in the CSS Logger.
    """

    def __init__(self, cams: list, initial_qdate: QDate, use_lab: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PV Region Search")
        self.resize(1040, 660)
        self._cams = cams
        self._use_lab = use_lab
        self._regions: list[dict] = []      # {id, t_start_ns, t_end_ns, color, day}
        self._region_seq = 0
        self._series: dict[str, list] = {}  # channel → [(t_ns, value), …] for current day
        self._load_gen = 0
        self._span = None

        # Lazy matplotlib import (keeps module import time low).
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT)
        from matplotlib.widgets import SpanSelector
        import matplotlib.dates as mdates
        self._Figure, self._FigureCanvas = Figure, FigureCanvas
        self._NavToolbar, self._SpanSelector = NavigationToolbar2QT, SpanSelector
        self._mdates = mdates

        class _Sig(QObject):
            done = Signal(object)
        self._sig = _Sig()
        self._sig.done.connect(self._on_series_loaded)

        self._build_ui(initial_qdate)
        self._seed_default_pvs()
        self._reload_day()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self, initial_qdate: QDate):
        root = QHBoxLayout(self)
        root.setSpacing(8)

        # ── Left sidebar ──────────────────────────────────────────────────
        side = QVBoxLayout()
        side.setSpacing(6)

        # Day navigation
        side.addWidget(_section_label("Day"))
        self._cal = _NoScrollCalendar()
        self._cal.setSelectedDate(initial_qdate)
        self._cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.ISOWeekNumbers)
        _style_calendar(self._cal)            # house look: gray header, red weekends
        self._cal.setMaximumHeight(210)
        self._cal.clicked.connect(lambda *_: self._reload_day())
        side.addWidget(self._cal)

        nav = QHBoxLayout()
        btn_prev = QPushButton("‹ Prev day")
        btn_prev.clicked.connect(lambda: self._step_day(-1))
        btn_next = QPushButton("Next day ›")
        btn_next.clicked.connect(lambda: self._step_day(+1))
        nav.addWidget(btn_prev); nav.addWidget(btn_next)
        side.addLayout(nav)
        self._lbl_tz = QLabel("Lab time" if self._use_lab else "Prague time")
        self._lbl_tz.setStyleSheet("color:#888;font-size:10px;")
        side.addWidget(self._lbl_tz)

        # PV list
        side.addWidget(_section_label("PVs to plot"))
        self._pv_list = QListWidget()
        self._pv_list.setMaximumHeight(150)
        self._pv_list.itemChanged.connect(self._on_pv_checks_changed)
        side.addWidget(self._pv_list)
        pv_btns = QHBoxLayout()
        btn_browse = QPushButton("Browse…")
        btn_browse.setToolTip("Search all archiver channels")
        btn_browse.clicked.connect(self._browse_pvs)
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._remove_selected_pv)
        pv_btns.addWidget(btn_browse); pv_btns.addWidget(btn_remove)
        side.addLayout(pv_btns)

        prim_row = QHBoxLayout()
        prim_row.addWidget(QLabel("Primary (peak):"))
        self._primary_cb = QComboBox()
        self._primary_cb.setToolTip("PV whose peak inside a region defines the target time")
        prim_row.addWidget(self._primary_cb, 1)
        side.addLayout(prim_row)

        # Regions
        side.addWidget(_section_label("Regions"))
        reg_scroll = QScrollArea()
        reg_scroll.setWidgetResizable(True)
        reg_scroll.setMaximumHeight(150)
        self._regions_host = QWidget()
        self._regions_lay = QVBoxLayout(self._regions_host)
        self._regions_lay.setContentsMargins(0, 0, 0, 0)
        self._regions_lay.setSpacing(2)
        reg_scroll.setWidget(self._regions_host)
        side.addWidget(reg_scroll)
        btn_clear = QPushButton("Clear all regions")
        btn_clear.clicked.connect(self._clear_regions)
        side.addWidget(btn_clear)

        side.addStretch()
        self._status = QLabel("Ready.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#555;font-size:10px;")
        side.addWidget(self._status)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._btn_search = bb.addButton("🎯 Search these regions",
                                        QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_search.clicked.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        side.addWidget(bb)

        side_w = QWidget(); side_w.setLayout(side); side_w.setFixedWidth(300)
        root.addWidget(side_w)

        # ── Right: plot ───────────────────────────────────────────────────
        right = QVBoxLayout()
        self._fig = self._Figure(figsize=(6, 4), tight_layout=True)
        self._canvas = self._FigureCanvas(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._toolbar = _make_mpl_toolbar(self._NavToolbar, self._canvas, self)
        right.addWidget(self._toolbar)
        right.addWidget(self._canvas, 1)
        hint = QLabel("Drag on the graph to mark a time region, then click Search.")
        hint.setStyleSheet("color:#666;font-size:11px;")
        right.addWidget(hint)
        right_w = QWidget(); right_w.setLayout(right)
        root.addWidget(right_w, 1)

        self._rebuild_regions_ui()

    def _seed_default_pvs(self):
        # Presets from CHANNEL_MAP; SBW4 pre-checked as the default primary.
        self._pv_list.blockSignals(True)
        for name, ch in CPVA_CHANNEL_MAP.items():
            it = QListWidgetItem(name.upper())
            it.setData(Qt.ItemDataRole.UserRole, ch)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if ch == CPVA_SBW4_CHANNEL
                             else Qt.CheckState.Unchecked)
            self._pv_list.addItem(it)
        self._pv_list.blockSignals(False)
        self._refresh_primary_combo()

    # ── PV list handling ──────────────────────────────────────────────────
    def _checked_channels(self) -> list:
        out = []
        for i in range(self._pv_list.count()):
            it = self._pv_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append((it.text(), it.data(Qt.ItemDataRole.UserRole)))
        return out

    def _refresh_primary_combo(self):
        prev = self._primary_cb.currentData()
        self._primary_cb.blockSignals(True)
        self._primary_cb.clear()
        for label, ch in self._checked_channels():
            self._primary_cb.addItem(label, ch)
        # keep previous primary if still checked, else default to first
        idx = self._primary_cb.findData(prev)
        if idx >= 0:
            self._primary_cb.setCurrentIndex(idx)
        self._primary_cb.blockSignals(False)

    def _on_pv_checks_changed(self, *_):
        self._refresh_primary_combo()
        self._reload_day()

    def _browse_pvs(self):
        dlg = _PVBrowseDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        existing = {self._pv_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self._pv_list.count())}
        self._pv_list.blockSignals(True)
        for ch in dlg.selected_channels():
            if ch in existing:
                continue
            it = QListWidgetItem(_PV_PRESET_LABELS.get(ch, ch))
            it.setData(Qt.ItemDataRole.UserRole, ch)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self._pv_list.addItem(it)
        self._pv_list.blockSignals(False)
        self._refresh_primary_combo()
        self._reload_day()

    def _remove_selected_pv(self):
        for it in self._pv_list.selectedItems():
            self._pv_list.takeItem(self._pv_list.row(it))
        self._refresh_primary_combo()
        self._reload_day()

    # ── Day handling ──────────────────────────────────────────────────────
    def _step_day(self, delta: int):
        self._cal.setSelectedDate(self._cal.selectedDate().addDays(delta))
        self._reload_day()

    def _day_bounds_ns(self) -> "tuple[int, int]":
        qd = self._cal.selectedDate()
        tz = timezone.utc if (self._use_lab or PRAGUE is None) else PRAGUE
        t0 = datetime(qd.year(), qd.month(), qd.day(), 0, 0, 0, tzinfo=tz)
        t1 = datetime(qd.year(), qd.month(), qd.day(), 23, 59, 59, tzinfo=tz)
        return int(t0.timestamp() * 1e9), int(t1.timestamp() * 1e9)

    def _ns_to_num(self, t_ns: int):
        dt = datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc)
        if not self._use_lab and PRAGUE is not None:
            dt = dt.astimezone(PRAGUE)
        return self._mdates.date2num(dt)

    def _reload_day(self):
        channels = [ch for _, ch in self._checked_channels()]
        start_ns, end_ns = self._day_bounds_ns()
        self._load_gen += 1
        gen = self._load_gen
        qd = self._cal.selectedDate()
        self._status.setText(f"Loading {qd.toString('dd.MM.yyyy')} — {len(channels)} PV(s)…")
        if not channels:
            self._series = {}
            self._redraw()
            self._status.setText("No PV selected — check at least one PV to plot.")
            return

        def worker():
            out: dict[str, list] = {}
            err = None
            for ch in channels:
                try:
                    out[ch] = cpva.fetch_values(ch, start_ns, end_ns, timeout=8.0)
                except Exception as e:
                    err = e
                    out[ch] = []
            try:
                self._sig.done.emit({"gen": gen, "series": out, "err": err})
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_series_loaded(self, data: dict):
        if data.get("gen") != self._load_gen:
            return   # a newer request superseded this one
        self._series = data.get("series") or {}
        self._redraw()
        n = sum(len(v) for v in self._series.values())
        err = data.get("err")
        if err is not None and n == 0:
            self._status.setText(f"Archiver error: {err}")
        else:
            self._status.setText(f"Loaded {n} samples.  Drag to mark a region.")

    # ── Plot ────────────────────────────────────────────────────────────────
    def _redraw(self):
        ax = self._ax
        ax.clear()
        label_by_ch = {ch: lbl for lbl, ch in self._checked_channels()}
        any_data = False
        for i, (ch, series) in enumerate(self._series.items()):
            if not series:
                continue
            any_data = True
            times = [self._ns_to_num(t) for t, _ in series]
            vals = np.array([v for _, v in series], dtype=float)
            vmax = np.nanmax(np.abs(vals)) if vals.size else 0.0
            norm = vals / vmax if vmax > 0 else vals
            ax.plot(times, norm, "-", lw=1.0, alpha=0.85, marker=".", ms=2,
                    label=label_by_ch.get(ch, ch))
        start_ns, end_ns = self._day_bounds_ns()
        ax.set_xlim(self._ns_to_num(start_ns), self._ns_to_num(end_ns))
        ax.xaxis.set_major_formatter(self._mdates.DateFormatter(
            "%H:%M", tz=(None if (self._use_lab or PRAGUE is None) else PRAGUE)))
        ax.set_xlabel("Time")
        ax.set_ylabel("PV value (normalized per PV)")
        ax.grid(True, alpha=0.25)
        if any_data:
            ax.legend(loc="upper right", fontsize=8)
        else:
            ax.text(0.5, 0.5, "No PV data for this day",
                    ha="center", va="center", transform=ax.transAxes, color="#999")
        self._paint_region_spans()
        self._fig.autofmt_xdate(rotation=30)
        self._canvas.draw_idle()
        self._install_span()

    def _paint_region_spans(self):
        cur = self._cal.selectedDate()
        cur_day = datetime(cur.year(), cur.month(), cur.day()).date()
        for r in self._regions:
            if r["day"] != cur_day:
                continue
            self._ax.axvspan(self._ns_to_num(r["t_start_ns"]),
                             self._ns_to_num(r["t_end_ns"]),
                             alpha=0.25, color=r["color"], zorder=0)

    def _install_span(self):
        if self._span is not None:
            try:
                self._span.set_active(False)
            except Exception:
                pass
        self._span = self._SpanSelector(
            self._ax, self._on_span, "horizontal", useblit=False,
            props=dict(alpha=0.20, facecolor="#90CAF9"), interactive=False)

    def _on_span(self, xmin: float, xmax: float):
        if xmax - xmin < 1e-9:
            return
        try:
            t_start = int(self._mdates.num2date(xmin).timestamp() * 1e9)
            t_end = int(self._mdates.num2date(xmax).timestamp() * 1e9)
        except Exception:
            return
        cur = self._cal.selectedDate()
        cur_day = datetime(cur.year(), cur.month(), cur.day()).date()
        rid = self._region_seq
        self._region_seq += 1
        color = _PV_REGION_COLORS[rid % len(_PV_REGION_COLORS)]
        self._regions.append({"id": rid, "t_start_ns": t_start, "t_end_ns": t_end,
                              "color": color, "day": cur_day})
        self._ax.axvspan(xmin, xmax, alpha=0.25, color=color, zorder=0)
        self._canvas.draw_idle()
        self._rebuild_regions_ui()

    # ── Regions UI ──────────────────────────────────────────────────────────
    def _fmt_region(self, r: dict) -> str:
        def hms(ns):
            dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
            if not self._use_lab and PRAGUE is not None:
                dt = dt.astimezone(PRAGUE)
            return dt.strftime("%H:%M:%S")
        return f"{r['day'].strftime('%d.%m')}  {hms(r['t_start_ns'])}–{hms(r['t_end_ns'])}"

    def _rebuild_regions_ui(self):
        while self._regions_lay.count():
            item = self._regions_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._regions:
            empty = QLabel("Drag on the graph\nto add a region.")
            empty.setStyleSheet("color:#999;font-size:11px;")
            self._regions_lay.addWidget(empty)
            return
        for r in self._regions:
            row = QHBoxLayout()
            dot = QLabel("■"); dot.setStyleSheet(f"color:{r['color']};")
            lbl = QLabel(self._fmt_region(r))
            lbl.setStyleSheet("font-size:11px;")
            btn = QToolButton(); btn.setText("✕")
            btn.setToolTip("Delete region")
            btn.clicked.connect(lambda _=False, rid=r["id"]: self._delete_region(rid))
            row.addWidget(dot); row.addWidget(lbl, 1); row.addWidget(btn)
            w = QWidget(); w.setLayout(row)
            self._regions_lay.addWidget(w)

    def _delete_region(self, rid: int):
        self._regions = [r for r in self._regions if r["id"] != rid]
        self._redraw()
        self._rebuild_regions_ui()

    def _clear_regions(self):
        self._regions = []
        self._redraw()
        self._rebuild_regions_ui()

    # ── Accept ────────────────────────────────────────────────────────────
    def _on_accept(self):
        if not self._regions:
            QMessageBox.information(self, "PV Region Search",
                                    "Mark at least one region on the graph."); return
        if self._primary_cb.currentData() is None:
            QMessageBox.information(self, "PV Region Search",
                                    "Check at least one PV and pick a primary PV."); return
        if not self._cams:
            QMessageBox.information(self, "PV Region Search",
                                    "No cameras selected — check cameras in the table first."); return
        self.accept()

    def get_config(self) -> dict:
        regions_by_day: dict = {}
        for r in self._regions:
            regions_by_day.setdefault(r["day"], []).append(
                (r["t_start_ns"], r["t_end_ns"]))
        return {
            "cameras":         self._cams,
            "days":            sorted(regions_by_day.keys()),
            "regions":         regions_by_day,
            "primary_channel": self._primary_cb.currentData(),
            "start_hour":      0,
            "max_hour":        23,
            "use_lab_time":    self._use_lab,
        }


# ── MULTI-DAY PREVIEW WINDOW ──────────────────────────────────────────────────
_WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday")


def _fmt_day_long(day) -> str:
    """'Monday  03.08.2026'. Spelled out rather than %A so the banner reads the same on
    a machine with a Czech locale as it does in the rest of the app's English text."""
    try:
        return f"{_WEEKDAYS_EN[day.weekday()]}  {day.strftime('%d.%m.%Y')}"
    except Exception:
        return str(day or "")


class _WallSignals(QObject):
    tile_ready = Signal(int, int)      # (cell index, generation)
    all_done   = Signal(int)           # (generation)


class _WallShared:
    """Everything several walls must agree on, keyed by the frame's own file path.

    One search now fills a stack of walls — one per camera plus the day-by-day view —
    and they show THE SAME frames. Two things follow. A frame must be read from the
    share once, not once per tab (a read over the share is 130-160 ms, and a fortnight
    of ten cameras is 140 of them). And a frame the user adjusted or drew on in its
    camera tab must look adjusted and drawn on in the day-by-day tab too, so none of
    that state may live in a widget.

    Keyed by path rather than by tile index because the index means something different
    on every wall.
    """

    def __init__(self):
        self.raw: "dict[Path, tuple]" = {}      # path → (float32 array, full_scale)
        self.adj: "dict[Path, dict]"  = {}      # path → {contrast, offset, gamma, rot}
        self.ov:  "dict[Path, dict]"  = {}      # path → overlay shapes (see _OverlayState)
        self.sel: "set" = set()                 # selected paths
        self.undo: "list[dict]" = []            # snapshots of adj/ov/sel

    # ── undo ──────────────────────────────────────────────────────────────────
    def push_undo(self):
        """Snapshot before a change. Bounded — an undo stack that grows all session is
        one more slow leak, and nobody steps back more than a handful of edits."""
        self.undo.append({
            "adj": {k: dict(v) for k, v in self.adj.items()},
            "ov":  {k: _copy_overlay(v) for k, v in self.ov.items()},
            "sel": set(self.sel),
        })
        if len(self.undo) > 30:
            del self.undo[0]

    def pop_undo(self) -> bool:
        if not self.undo:
            return False
        st = self.undo.pop()
        self.adj = st["adj"]
        self.ov  = st["ov"]
        self.sel = st["sel"]
        return True

    def clear_edits(self):
        self.adj.clear()
        self.ov.clear()


def _copy_overlay(o: dict) -> dict:
    out = dict(o)
    for k in ("circle", "square", "cross"):
        if isinstance(out.get(k), dict):
            out[k] = dict(out[k])
    return out


class _DayWall(QWidget):
    """Days next to each other — the transpose of the Image Slider's camera grid.

    The Slider answers "what did the machine look like at ONE moment" and tiles many
    cameras. This answers "how did ONE camera change over MANY days" and tiles the
    days. Geometrically that is the same problem, so it borrows the Slider's own
    packer (`compute_camera_layout`) instead of carrying a second one: the tiles are
    a seamless partition of the canvas, the smallest day is made as large as the
    arrangement allows, and two tiles sharing an edge land on the same pixel.

    One display setting drives every tile BY DEFAULT, which is the opposite of the
    Slider's rule. Here the whole point of the view is that one colour means one
    intensity in every day, so a tile is NEVER stretched by its own min/max, and every
    frame goes on ITS CAMERA's fixed sensor range via `img_scale.full_scale_for_pil`.

    Both of those were wrong in the thumbnail grid this replaces: it rendered every
    camera against 65535 instead of the sensor range, and then stretched each
    thumbnail by its own min/max — so two days of visibly different strength came out
    looking equally bright, which is the one thing a comparison view must not do.

    A single frame CAN be given its own brightness, contrast, gamma and rotation: pick
    it and move a control, exactly as in the Slider, where a control hits whatever was
    selected at the moment it was moved. Somebody comparing ten days needs to be able
    to open up the one dim day without flattening the other nine. But a tile rendered
    on different terms from its neighbours is no longer comparable with them, so it has
    to SAY SO — an adjusted tile carries a mark in its caption strip, and Reset puts
    every frame back on the shared setting.

    Auto and the reference-day deviation are deliberately not part of that: both work
    off the raw frames (`_shared_auto_pair`, `set_baseline`) and stay wall-wide, so the
    two readings that must be comparable to mean anything always are.
    """

    tile_clicked = Signal(int)         # index into the cell list
    tile_context = Signal(int, object) # (index, global QPoint) — right-click menu
    selection_changed = Signal()

    _GAP       = 3                    # px between tiles, so captions cannot touch
    _CAPTION_H = 20                   # px reserved under each frame for its day label
    _DAY_HDR_H = 22                   # px for the day banner in rows-by-day mode
    _HANDLE_R  = 7                    # overlay grab handle radius

    def __init__(self, parent=None, shared: "_WallShared | None" = None):
        super().__init__(parent)
        self.setMinimumSize(200, 150)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background:#1a1a1a;")
        self.setMouseTracking(True)

        self._shared = shared if shared is not None else _WallShared()
        self._cells: "list[dict]" = []      # {day, cam, path, ts_ns, status}
        # Alias, not a copy: every wall reads the frames through the same cache.
        self._raw = self._shared.raw            # path → (float32 array, full_scale)
        self._pix:   "dict[int, tuple]" = {}    # cell index → (per-frame key, pixmap)
        self._rects: "list[QRect]" = []
        self._img_rects: "list[QRect]" = []     # where the picture itself landed
        self._render_key = None             # display state the cached pixmaps belong to
        self._baseline_idx: "int | None" = None
        self._hover = -1
        self._load_gen = 0
        self._layout_mode = "pack"          # "pack" | "rows"
        self._row_heads: "list[tuple[QRect, str]]" = []   # day banners in rows mode

        # Display state — the shared values every un-adjusted tile uses.
        self._grad_name = "Grayscale"
        self._auto      = False
        self._gamma     = None
        self._contrast  = 0
        self._offset    = 0
        self._auto_pair = None              # shared (contrast, offset) standing in for Auto

        # Overlay drawing
        self._draw_mode = ""                # "" | "circle" | "square" | "cross"
        self._shape_colors = {"circle": QColor(255, 255, 0, 230),
                              "square": QColor(0, 200, 255, 230),
                              "cross":  QColor(0, 255, 0, 220)}
        self._drag_idx    = -1
        self._drag_handle = ""
        self._drag_start  = None
        self._did_drag    = False

        self._sig = _WallSignals()
        self._sig.tile_ready.connect(self._on_tile_ready)

    # ── contents ──────────────────────────────────────────────────────────────
    def cells(self) -> "list[dict]":
        return list(self._cells)

    def set_cells(self, cells: "list[dict]"):
        """Replace what the wall shows. Frames are read in the background so a slow
        share fills the wall progressively instead of blocking the window."""
        self._load_gen += 1
        self._cells = [dict(c) for c in cells]
        self._pix.clear()
        self._rects = []
        self._auto_pair = None
        self._baseline_idx = None
        self._render_key = None
        self._hover = -1
        self._relayout()
        self.update()
        if self._cells:
            self._kick_load(self._load_gen)

    def _kick_load(self, gen: int):
        paths = [c.get("path") for c in self._cells]

        def worker():
            for i, p in enumerate(paths):
                if gen != self._load_gen:
                    return
                if p is None or p in self._raw:
                    if p is not None:
                        self._sig.tile_ready.emit(i, gen)
                    continue
                try:
                    with PilImage.open(str(p)) as pil:
                        mode = pil.mode
                        info = dict(pil.info or {})
                        if mode in ("I", "I;16"):
                            arr = np.array(pil, dtype=np.float32)
                        else:
                            arr = np.array(pil.convert("L"), dtype=np.float32)
                    full_scale = img_scale.full_scale_for_pil(p, info, mode)
                    self._raw[p] = (arr, float(full_scale))
                except Exception:
                    self._raw[p] = None
                self._sig.tile_ready.emit(i, gen)
            self._sig.all_done.emit(gen)

        threading.Thread(target=worker, daemon=True).start()

    def _on_tile_ready(self, idx: int, gen: int):
        if gen != self._load_gen:
            return
        # A frame's real aspect ratio only becomes known once it is read, so the
        # arrangement is recomputed as they arrive rather than guessed up front. A new
        # frame also widens the pooled Auto window, so that is dropped too.
        self._auto_pair = None
        self._pix.pop(idx, None)
        self._relayout()
        self.update()

    # ── display state ─────────────────────────────────────────────────────────
    def set_display(self, grad_name: str, auto: bool, gamma, contrast: int, offset: int):
        key = (grad_name, bool(auto), gamma, int(contrast), int(offset))
        if key == (self._grad_name, self._auto, self._gamma, self._contrast, self._offset):
            return
        self._grad_name, self._auto, self._gamma, self._contrast, self._offset = (
            grad_name, bool(auto), gamma, int(contrast), int(offset))
        self._auto_pair = None
        self._pix.clear()
        self.update()

    def _shared_auto_pair(self):
        """ONE contrast/brightness pair standing in for Auto across the whole wall.

        `img_scale.stretch_u8` says of itself "NOT comparable between frames", and it is
        right: a percentile stretch taken per tile levels every day to its own content,
        so a strong day and a weak one come out equally bright and the comparison is
        worthless. So the window is taken ONCE over a pooled sample of every day on the
        wall, converted to the equivalent manual pair (the conversion `stretch_u8`
        already documents and returns), and that same pair is then applied to every tile
        through the ordinary absolute path. Auto still does its job — dim cameras become
        visible — without any day being measured against itself."""
        if self._auto_pair is not None:
            return self._auto_pair
        samples = []
        for c in self._cells:
            entry = self._raw.get(c.get("path"))
            if not entry:
                continue
            arr, full_scale = entry
            if not full_scale:
                continue
            # Each day is brought onto ONE common range BEFORE being pooled. The stored
            # values cannot be pooled as they are: the archiver stretched every frame by
            # its own bracket, so a stored 51299 is 400 counts on a dim day and 3200 on
            # a bright one. Pooling them raw asks for the percentiles of a mixture of
            # different units, and the pair that came out saturated every brighter day
            # to white — the exact flattening this whole view exists to avoid.
            scaled = arr.astype(np.float32) * (img_scale.FULL_SCALE_16 / float(full_scale))
            samples.append(img_scale.stat_sample(scaled).ravel())
        if not samples:
            return (0, 0)
        pooled = np.concatenate(samples)
        out: dict = {}
        img_scale.stretch_u8(pooled, full_scale=img_scale.FULL_SCALE_16, out=out)
        self._auto_pair = (int(out.get("contrast", 0)), int(out.get("offset", 0)))
        return self._auto_pair

    def set_baseline(self, idx: "int | None"):
        """Pick one day as the reference; every other tile then shows |day − reference|.
        Honest without any extra bookkeeping because both frames are already on the
        same absolute scale."""
        if idx == self._baseline_idx:
            return
        self._baseline_idx = idx
        self._pix.clear()
        self.update()

    def baseline_idx(self) -> "int | None":
        return self._baseline_idx

    # ── per-frame state ───────────────────────────────────────────────────────
    def set_layout_mode(self, mode: str):
        """"pack" — the free-form partition that makes every day as large as it can be.
        "rows" — one row per day, the cameras always in the same order, for reading down
        a column and seeing one camera change."""
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        self._pix.clear()
        self._relayout()
        self.update()

    def selected_paths(self) -> set:
        return set(self._shared.sel)

    def set_selected(self, path, on: bool):
        if on:
            self._shared.sel.add(path)
        else:
            self._shared.sel.discard(path)
        self.update()
        self.selection_changed.emit()

    def clear_selection(self):
        if not self._shared.sel:
            return
        self._shared.sel.clear()
        self.update()
        self.selection_changed.emit()

    def apply_adjust(self, paths, contrast: int, offset: int, gamma, rot: "int | None" = None):
        """Give these frames their own contrast / brightness / gamma (and rotation).
        `paths` empty means the whole wall goes back to the shared setting."""
        for p in paths:
            cur = dict(self._shared.adj.get(p) or {})
            cur.update({"contrast": int(contrast), "offset": int(offset), "gamma": gamma})
            if rot is not None:
                cur["rot"] = int(rot) % 360
            self._shared.adj[p] = cur
        self._pix.clear()
        self.update()

    def rotate(self, paths, delta: int):
        """Turn these frames by ±90°. With nothing selected the caller passes every
        frame on the wall, so the gesture still reads as 'rotate the pictures'."""
        for p in paths:
            cur = dict(self._shared.adj.get(p) or {})
            cur["rot"] = (int(cur.get("rot", 0)) + delta) % 360
            self._shared.adj[p] = cur
        self._pix.clear()
        self._relayout()          # a turned frame has a different shape
        self.update()

    def clear_adjust(self):
        self._shared.adj.clear()
        self._pix.clear()
        self._relayout()
        self.update()

    def refresh_edits(self):
        """Re-render after somebody else changed the shared adjustments or overlays."""
        self._pix.clear()
        self._relayout()
        self.update()

    # ── overlays ──────────────────────────────────────────────────────────────
    def set_draw_mode(self, mode: str):
        self._draw_mode = mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_shape_color(self, kind: str, color: QColor):
        self._shape_colors[kind] = QColor(color)
        self.update()

    def clear_overlays(self, paths=None):
        if paths is None:
            self._shared.ov.clear()
        else:
            for p in paths:
                self._shared.ov.pop(p, None)
        self.update()

    def _ov_for(self, path) -> dict:
        o = self._shared.ov.get(path)
        if o is None:
            o = self._shared.ov[path] = {}
        return o

    # ── layout ────────────────────────────────────────────────────────────────
    def _rot_of(self, cell: dict) -> int:
        return int((self._shared.adj.get(cell.get("path")) or {}).get("rot", 0)) % 360

    def _aspects(self) -> list:
        out = []
        for c in self._cells:
            entry = self._raw.get(c.get("path"))
            if entry:
                arr = entry[0]
                h, w = arr.shape[:2]
                if self._rot_of(c) in (90, 270):
                    w, h = h, w
                out.append(max(0.05, float(w) / max(1.0, float(h))))
            else:
                out.append(4.0 / 3.0)     # placeholder until the frame is read
        return out

    def _row_order(self) -> "tuple[list, list]":
        """(days top to bottom, cameras left to right). The camera order is the same in
        every row — that is the whole reason to look at the wall this way."""
        days, cams = [], []
        for c in self._cells:
            d, m = c.get("day"), c.get("cam", "")
            if d not in days:
                days.append(d)
            if m not in cams:
                cams.append(m)
        days.sort(key=lambda d: str(d))
        cams.sort()
        return days, cams

    def rows_content_height(self) -> int:
        """How tall the wall needs to be in rows-by-day mode, so the scroll area that
        holds it knows what to scroll."""
        days, cams = self._row_order()
        if not days or not cams:
            return max(1, self.height())
        W = max(1, self.width())
        col_w = max(60, W // len(cams))
        aspects = [a for a in self._aspects() if a > 0]
        mean_a = (sum(aspects) / len(aspects)) if aspects else (4.0 / 3.0)
        tile_h = int(col_w / max(0.2, mean_a)) + self._CAPTION_H + 2 * self._GAP
        tile_h = max(90, min(tile_h, 420))
        return len(days) * (self._DAY_HDR_H + tile_h)

    def _relayout_rows(self):
        """One row per day; one column per camera, in the same order in every row."""
        days, cams = self._row_order()
        self._rects = [QRect() for _ in self._cells]
        self._row_heads = []
        if not days or not cams:
            return
        W = max(1, self.width())
        col_w = max(60, W // len(cams))
        total_h = self.rows_content_height()
        row_h = total_h // len(days) - self._DAY_HDR_H
        pos = {}
        for r, d in enumerate(days):
            y = r * (self._DAY_HDR_H + row_h)
            self._row_heads.append((QRect(0, y, W, self._DAY_HDR_H), _fmt_day_long(d)))
            for cidx, cam in enumerate(cams):
                x0 = int(round(cidx * W / len(cams)))
                x1 = int(round((cidx + 1) * W / len(cams)))
                pos[(d, cam)] = QRect(x0, y + self._DAY_HDR_H, max(20, x1 - x0), row_h)
        for i, c in enumerate(self._cells):
            self._rects[i] = pos.get((c.get("day"), c.get("cam", "")),
                                     QRect(0, 0, 0, 0))
        self.setMinimumHeight(total_h)

    def _relayout(self):
        n = len(self._cells)
        self._rects = []
        self._row_heads = []
        if n == 0:
            self.setMinimumHeight(150)
            return
        if self._layout_mode == "rows":
            self._relayout_rows()
            return
        self.setMinimumHeight(150)
        W, H = max(1, self.width()), max(1, self.height())
        aspects = self._aspects()
        entries = None
        try:
            sl = _get_slider_module()
            entries = sl.compute_camera_layout(
                aspects, W, H, top_px=float(self._CAPTION_H + 2 * self._GAP))
        except Exception:
            entries = None
        if not entries:
            # Fallback: plain near-square grid, still edge-to-edge.
            cols = max(1, int(n ** 0.5 + 0.999))
            rows = max(1, (n + cols - 1) // cols)
            for i in range(n):
                r, c = divmod(i, cols)
                x0 = int(round(c * W / cols)); x1 = int(round((c + 1) * W / cols))
                y0 = int(round(r * H / rows)); y1 = int(round((r + 1) * H / rows))
                self._rects.append(QRect(x0, y0, max(20, x1 - x0), max(20, y1 - y0)))
            return
        try:
            sl = _get_slider_module()
            for e in entries[:n]:
                self._rects.append(sl._entry_rect(e.x, e.y, e.w, e.h, W, H))
        except Exception:
            self._rects = []

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()
        self._pix.clear()          # tiles are rendered to their drawn size

    # ── rendering ─────────────────────────────────────────────────────────────
    def _tile_pixmap(self, idx: int, target: QRect) -> "QPixmap | None":
        # Resolve the shared Auto pair BEFORE the cache key is built — it is part of
        # the key, and computing it afterwards would invalidate the cache on the very
        # next tile and re-render the whole wall on every repaint.
        auto_pair = self._shared_auto_pair() if self._auto else None
        key = (self._grad_name, self._auto, self._gamma, self._contrast,
               self._offset, auto_pair, self._baseline_idx,
               target.width(), target.height())
        if self._render_key != key:
            self._pix.clear()
            self._render_key = key
        cell = self._cells[idx]
        adj = self._shared.adj.get(cell.get("path")) or {}
        # The per-frame values ride in the cache entry, not the wall-wide key: adjusting
        # one tile must not throw away the other forty-nine pixmaps.
        akey = (adj.get("contrast"), adj.get("offset"), adj.get("gamma"), adj.get("rot", 0))
        hit = self._pix.get(idx)
        if hit is not None and hit[0] == akey:
            return hit[1]
        entry = self._raw.get(cell.get("path"))
        if not entry:
            return None
        arr, full_scale = entry
        if self._baseline_idx is not None and self._baseline_idx != idx:
            base = self._raw.get(self._cells[self._baseline_idx].get("path"))
            if base and base[0].shape == arr.shape and full_scale and base[1]:
                # Subtract on ONE common range, never on the stored values. The archiver
                # stretched each frame by its own bracket, so a dim day and a bright one
                # can both sit near 51 000 in the file: subtracting those gives almost
                # zero and the deviation comes out black, while the real difference is
                # the whole point. Converting both to the same range first makes the
                # difference a difference in true intensity.
                k = img_scale.FULL_SCALE_16
                arr = np.abs(arr.astype(np.float32) * (k / float(full_scale))
                             - base[0].astype(np.float32) * (k / float(base[1])))
                full_scale = k
        if adj:
            # This frame was picked out and adjusted by hand. Its own numbers replace
            # the wall's — including Auto, which is a shared reading it has opted out
            # of — and the caption says so, because it is no longer comparable.
            arr8 = _render_u8(arr, False, full_scale, adj.get("gamma"),
                              int(adj.get("contrast", 0)), int(adj.get("offset", 0)))
        elif self._auto:
            # Auto becomes one shared pair on the absolute path — never a per-tile
            # stretch, which would level every day to its own content.
            a_con, a_off = auto_pair or (0, 0)
            arr8 = _render_u8(arr, False, full_scale, self._gamma, a_con, a_off)
        else:
            arr8 = _render_u8(arr, False, full_scale, self._gamma,
                              self._contrast, self._offset)
        rot = int(adj.get("rot", 0)) % 360
        if rot:
            arr8 = np.ascontiguousarray(np.rot90(arr8, k=(4 - rot // 90) % 4))
        h, w = arr8.shape[:2]
        try:
            lut = _get_slider_module().GRADIENTS.get(self._grad_name)
        except Exception:
            lut = None
        if lut is None:
            img = QImage(np.ascontiguousarray(arr8).data, w, h, w,
                         QImage.Format.Format_Grayscale8).copy()
        else:
            # The LUT is applied to the ABSOLUTE code, never to a per-frame window —
            # a palette spread over each day's own range would destroy the comparison.
            rgb = np.ascontiguousarray(np.asarray(lut, dtype=np.uint8)[arr8])
            img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        avail_h = max(8, target.height() - self._CAPTION_H - 2 * self._GAP)
        avail_w = max(8, target.width() - 2 * self._GAP)
        pm = QPixmap.fromImage(img).scaled(
            avail_w, avail_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._pix[idx] = (akey, pm)
        return pm

    # How the frame was chosen, in words the caption has room for. A frame nobody
    # vouched for has to look different from one a shot picked, or a black tile reads
    # as a broken camera instead of as "we just took whatever was in the folder".
    _SOURCE_TAG = {"sbw4": "SBW4", "ptm1": "PTM1", "totalpower": "power",
                   "csv": "CSV", "pv": "PV", "blind": "no shot data"}

    def _caption(self, cell: dict) -> str:
        day = cell.get("day")
        txt = day.strftime("%d.%m.") if hasattr(day, "strftime") else str(day or "")
        ts = cell.get("ts_ns")
        if ts:
            try:
                txt += "  " + datetime.fromtimestamp(
                    ts / 1e9, tz=timezone.utc).astimezone(PRAGUE).strftime("%H:%M:%S")
            except Exception:
                pass
        # Only the WARNING is captioned. Naming the channel the frame was picked by
        # ("[SBW4]") repeated the search on every tile — the operator just chose it,
        # and it is the same for the whole wall. A frame nobody vouched for still has
        # to say so, or a black tile reads as a broken camera.
        if (cell.get("meta") or {}).get("source") == "blind":
            txt += f"  [{self._SOURCE_TAG['blind']}]"
        if self._shared.adj.get(cell.get("path")):
            # Plain words, matching "(reference)" — this has to be legible in the caption
            # strip at any tile size, which a decorative glyph is not.
            txt += "  (adjusted)"
        return txt

    def _paint_into(self, painter: QPainter, rects: list, scale: float = 1.0):
        f = painter.font()
        f.setPointSizeF(max(6.0, 8.5 * scale))
        painter.setFont(f)
        live = (scale == 1.0 and rects is self._rects)
        if live:
            self._img_rects = [QRect() for _ in self._cells]
        if self._layout_mode == "rows":
            hf = painter.font()
            hf.setBold(True)
            for hr, txt in self._row_heads:
                r = (hr if scale == 1.0 else
                     QRect(int(hr.x() * scale), int(hr.y() * scale),
                           int(hr.width() * scale), int(hr.height() * scale)))
                painter.fillRect(r, QColor("#333"))
                painter.setFont(hf)
                painter.setPen(QPen(QColor("#eee")))
                painter.drawText(r.adjusted(int(10 * scale), 0, 0, 0),
                                 Qt.AlignmentFlag.AlignVCenter |
                                 Qt.AlignmentFlag.AlignLeft, txt)
                painter.setFont(f)
        for i, cell in enumerate(self._cells):
            if i >= len(rects):
                break
            r = rects[i]
            if r.width() <= 0 or r.height() <= 0:
                continue
            is_base = (self._baseline_idx == i)
            path = cell.get("path")
            pm = self._tile_pixmap(i, r)
            if pm is not None and not pm.isNull():
                x = r.x() + (r.width() - pm.width()) // 2
                y = r.y() + self._GAP + (
                    r.height() - self._CAPTION_H - 2 * self._GAP - pm.height()) // 2
                painter.drawPixmap(x, y, pm)
                ir = QRect(x, y, pm.width(), pm.height())
                if live:
                    self._img_rects[i] = ir
                self._paint_overlay(painter, path, ir, scale)
            else:
                painter.setPen(QPen(QColor("#666")))
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter,
                                 "no image" if path is None else "…")
            cap_rect = QRect(r.x(), r.y() + r.height() - self._CAPTION_H,
                             r.width(), self._CAPTION_H)
            painter.fillRect(cap_rect, QColor("#2b2b2b" if not is_base else "#4a3b00"))
            painter.setPen(QPen(QColor("#ffd54f" if is_base else "#ddd")))
            painter.drawText(cap_rect, Qt.AlignmentFlag.AlignCenter,
                             self._caption(cell) + ("  (reference)" if is_base else ""))
            if path is not None and path in self._shared.sel:
                painter.setPen(QPen(QColor("#4a9eff"), 3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(r.adjusted(1, 1, -2, -2))
            elif i == self._hover:
                painter.setPen(QPen(QColor("#6cf"), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(r.adjusted(1, 1, -2, -2))

    # ── overlay painting ──────────────────────────────────────────────────────
    def _paint_overlay(self, painter: QPainter, path, ir: QRect, scale: float = 1.0):
        """Shapes are held in fractions of the picture (0-1), so they stay put when the
        tile is re-laid out, saved at double size, or moved to another tab."""
        ov = self._shared.ov.get(path)
        if not ov or ir.width() <= 0 or ir.height() <= 0:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cross = ov.get("cross")
        if cross:
            cx = ir.left() + int(cross["x"] * ir.width())
            cy = ir.top() + int(cross["y"] * ir.height())
            s = max(6, int(18 * scale))
            painter.setPen(QPen(self._shape_colors["cross"], max(1, int(2 * scale))))
            painter.drawLine(cx - s, cy, cx + s, cy)
            painter.drawLine(cx, cy - s, cx, cy + s)
        circ = ov.get("circle")
        if circ:
            cx = ir.left() + int(circ["x"] * ir.width())
            cy = ir.top() + int(circ["y"] * ir.height())
            rx = int(circ["rx"] * ir.width())
            ry = int(circ["ry"] * ir.height())
            painter.setPen(QPen(self._shape_colors["circle"], max(1, int(2 * scale))))
            painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
            if self._draw_mode == "circle" and scale == 1.0:
                self._paint_handles(painter, self._circle_handles(circ, ir),
                                    self._shape_colors["circle"])
        sq = ov.get("square")
        if sq:
            sx = ir.left() + int(sq["l"] * ir.width())
            sy = ir.top() + int(sq["t"] * ir.height())
            sw = int((sq["r"] - sq["l"]) * ir.width())
            sh = int((sq["b"] - sq["t"]) * ir.height())
            painter.setPen(QPen(self._shape_colors["square"], max(1, int(2 * scale))))
            painter.drawRect(sx, sy, sw, sh)
            if self._draw_mode == "square" and scale == 1.0:
                self._paint_handles(painter, self._square_handles(sq, ir),
                                    self._shape_colors["square"])

    def _paint_handles(self, painter: QPainter, handles: dict, color: QColor):
        r = self._HANDLE_R
        painter.setPen(QPen(color))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 120))
        for pt in handles.values():
            painter.drawEllipse(int(pt[0]) - r, int(pt[1]) - r, r * 2, r * 2)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _circle_handles(circ: dict, ir: QRect) -> dict:
        cx = ir.left() + circ["x"] * ir.width()
        cy = ir.top() + circ["y"] * ir.height()
        rx = circ["rx"] * ir.width()
        ry = circ["ry"] * ir.height()
        return {"move": (cx, cy), "n": (cx, cy - ry), "s": (cx, cy + ry),
                "e": (cx + rx, cy), "w": (cx - rx, cy)}

    @staticmethod
    def _square_handles(sq: dict, ir: QRect) -> dict:
        sx = ir.left() + sq["l"] * ir.width()
        sy = ir.top() + sq["t"] * ir.height()
        ex = ir.left() + sq["r"] * ir.width()
        ey = ir.top() + sq["b"] * ir.height()
        mx, my = (sx + ex) / 2, (sy + ey) / 2
        return {"move": (mx, my), "nw": (sx, sy), "ne": (ex, sy), "sw": (sx, ey),
                "se": (ex, ey), "n": (mx, sy), "s": (mx, ey),
                "w": (sx, my), "e": (ex, my)}

    def _hit_handle(self, pos, handles: dict) -> str:
        r = self._HANDLE_R + 3
        for name, pt in handles.items():
            if abs(pos.x() - pt[0]) <= r and abs(pos.y() - pt[1]) <= r:
                return name
        return ""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1a1a1a"))
        if not self._cells:
            painter.setPen(QPen(QColor("#777")))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No days yet — run a search.")
            return
        if len(self._rects) != len(self._cells):
            self._relayout()
        self._paint_into(painter, self._rects)

    def composite_image(self, scale: float = 2.0) -> "QImage | None":
        """The whole wall as ONE image, captions included — the thing neither existing
        tab can produce, since both save frames singly."""
        if not self._cells or not self._rects:
            return None
        W = int(self.width() * scale); H = int(self.height() * scale)
        if W <= 0 or H <= 0:
            return None
        big = [QRect(int(r.x() * scale), int(r.y() * scale),
                     int(r.width() * scale), int(r.height() * scale))
               for r in self._rects]
        img = QImage(W, H, QImage.Format.Format_RGB888)
        img.fill(QColor("#1a1a1a"))
        painter = QPainter(img)
        saved_pix, saved_key = self._pix, self._render_key
        self._pix, self._render_key = {}, None      # render at the larger tile size
        try:
            self._paint_into(painter, big, scale=scale)
        finally:
            painter.end()
            self._pix, self._render_key = saved_pix, saved_key
        return img

    # ── interaction ───────────────────────────────────────────────────────────
    def _hit(self, pos) -> int:
        for i, r in enumerate(self._rects):
            if r.contains(pos):
                return i
        return -1

    def _img_rect_at(self, idx: int) -> "QRect | None":
        if 0 <= idx < len(self._img_rects):
            r = self._img_rects[idx]
            if r.width() > 0 and r.height() > 0:
                return r
        return None

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._drag_idx >= 0 and (event.buttons() & Qt.MouseButton.LeftButton):
            self._did_drag = True
            self._drag_overlay(pos, bool(event.modifiers()
                                         & Qt.KeyboardModifier.ShiftModifier))
            return
        i = self._hit(pos.toPoint())
        if i != self._hover:
            self._hover = i
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        pos = event.position()
        i = self._hit(pos.toPoint())
        if i < 0:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.RightButton:
            # Everything that acts on one frame lives here: the close-up, another try at
            # finding that day's picture, and the reference day (which right click used
            # to set outright, with nothing to say that it had).
            self.tile_context.emit(i, event.globalPosition().toPoint())
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._draw_mode:
            if self._begin_overlay(i, pos):
                return
        if event.button() == Qt.MouseButton.LeftButton:
            # Selecting and opening are the same gesture on purpose: you click the frame
            # you want to look at, and that is the frame the brightness controls should
            # be aiming at when you reach for them.
            path = self._cells[i].get("path")
            if path is not None:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.set_selected(path, path not in self._shared.sel)
                    return
                self._shared.sel.clear()
                self._shared.sel.add(path)
                self.selection_changed.emit()
                self.update()
            self.tile_clicked.emit(i)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_idx = -1
        self._drag_handle = ""
        self._drag_start = None
        self._did_drag = False
        super().mouseReleaseEvent(event)

    # ── overlay editing ───────────────────────────────────────────────────────
    def _begin_overlay(self, idx: int, pos) -> bool:
        ir = self._img_rect_at(idx)
        if ir is None:
            return False
        path = self._cells[idx].get("path")
        if path is None:
            return False
        ov = self._ov_for(path)
        self._drag_idx, self._drag_start, self._did_drag = idx, pos, False
        if self._draw_mode == "cross":
            self._drag_handle = "cross"
            self._set_cross(ov, pos, ir)
            self.update()
            return True
        shape = ov.get(self._draw_mode)
        if shape:
            handles = (self._circle_handles(shape, ir) if self._draw_mode == "circle"
                       else self._square_handles(shape, ir))
            hit = self._hit_handle(pos, handles)
            if hit:
                self._drag_handle = hit
                return True
        self._drag_handle = "new"
        return True

    @staticmethod
    def _clamp(v):
        return max(0.0, min(1.0, v))

    def _set_cross(self, ov: dict, pos, ir: QRect):
        ov["cross"] = {"x": self._clamp((pos.x() - ir.left()) / ir.width()),
                       "y": self._clamp((pos.y() - ir.top()) / ir.height())}

    def _drag_overlay(self, pos, shift: bool):
        idx = self._drag_idx
        ir = self._img_rect_at(idx)
        if ir is None:
            return
        path = self._cells[idx].get("path")
        ov = self._ov_for(path)
        c = self._clamp
        if self._draw_mode == "cross":
            self._set_cross(ov, pos, ir)
            self.update()
            return
        if self._draw_mode == "circle":
            circ = ov.get("circle")
            if self._drag_handle == "new":
                x0, y0 = self._drag_start.x(), self._drag_start.y()
                dx = abs(pos.x() - x0) / ir.width()
                dy = abs(pos.y() - y0) / ir.height()
                if shift:
                    dx = dy = max(dx, dy)
                ov["circle"] = {"x": c((x0 - ir.left()) / ir.width()),
                                "y": c((y0 - ir.top()) / ir.height()),
                                "rx": dx, "ry": dy}
            elif circ and self._drag_handle == "move":
                circ["x"] = c(circ["x"] + (pos.x() - self._drag_start.x()) / ir.width())
                circ["y"] = c(circ["y"] + (pos.y() - self._drag_start.y()) / ir.height())
                self._drag_start = pos
            elif circ and self._drag_handle in ("e", "w"):
                cx = ir.left() + circ["x"] * ir.width()
                circ["rx"] = abs(pos.x() - cx) / ir.width()
                if shift:
                    circ["ry"] = circ["rx"]
            elif circ and self._drag_handle in ("n", "s"):
                cy = ir.top() + circ["y"] * ir.height()
                circ["ry"] = abs(pos.y() - cy) / ir.height()
                if shift:
                    circ["rx"] = circ["ry"]
        elif self._draw_mode == "square":
            sq = ov.get("square")
            nx = c((pos.x() - ir.left()) / ir.width())
            ny = c((pos.y() - ir.top()) / ir.height())
            if self._drag_handle == "new":
                x0 = c((self._drag_start.x() - ir.left()) / ir.width())
                y0 = c((self._drag_start.y() - ir.top()) / ir.height())
                dx, dy = abs(nx - x0), abs(ny - y0)
                if shift:
                    dx = dy = max(dx, dy)
                ov["square"] = {"l": c(x0 - dx), "t": c(y0 - dy),
                                "r": c(x0 + dx), "b": c(y0 + dy)}
            elif sq and self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                w_, h_ = sq["r"] - sq["l"], sq["b"] - sq["t"]
                sq["l"] = c(sq["l"] + dx); sq["t"] = c(sq["t"] + dy)
                sq["r"] = c(sq["l"] + w_); sq["b"] = c(sq["t"] + h_)
            elif sq:
                h = self._drag_handle
                if "w" in h: sq["l"] = min(nx, sq["r"] - 0.01)
                if "e" in h: sq["r"] = max(nx, sq["l"] + 0.01)
                if "n" in h: sq["t"] = min(ny, sq["b"] - 0.01)
                if "s" in h: sq["b"] = max(ny, sq["t"] + 0.01)
                if shift and h in ("nw", "ne", "sw", "se"):
                    side = max(sq["r"] - sq["l"], sq["b"] - sq["t"])
                    if h == "se": sq["r"], sq["b"] = c(sq["l"] + side), c(sq["t"] + side)
                    elif h == "nw": sq["l"], sq["t"] = c(sq["r"] - side), c(sq["b"] - side)
                    elif h == "ne": sq["r"], sq["t"] = c(sq["l"] + side), c(sq["b"] - side)
                    else: sq["l"], sq["b"] = c(sq["r"] - side), c(sq["t"] + side)
        self.update()


# ── wheel guard ───────────────────────────────────────────────────────────────
def install_wheel_guard(app):
    """A value must never change just because the pointer crossed its control.

    Number fields, drop-downs and setting sliders answer the mouse wheel only
    once they have been CLICKED (i.e. they hold the keyboard focus). Until then
    the notch goes to the panel behind them instead, so a settings panel still
    scrolls when the pointer happens to pass over a field on the way down. A
    control that is meant to take the wheel at any time carries the "wheelAlways"
    property.

    Scroll bars are left out: they are sliders too, and the wheel is how a pane
    gets scrolled.

    One app-wide filter, so a dialog built much later is covered as well. It is
    spelled out in every entry point rather than imported once: each tab also
    runs on its own, and a sibling module would have to survive the frozen build
    (the same reason `_import_img_scale` is copied into each tab).
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider,
                                   QAbstractSpinBox, QScrollBar)

    class _WheelGuard(QObject):
        _GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        # Focus the user asked for. The focus a freshly opened window HANDS to its
        # first field (ActiveWindow / Other) does not count, or the top field of a
        # panel would answer the wheel before it had ever been touched.
        _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                   Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)
        _GIVEN = (Qt.FocusReason.ActiveWindowFocusReason,
                  Qt.FocusReason.OtherFocusReason)

        def eventFilter(self, obj, ev):
            t = ev.type()
            if t == QEvent.Type.FocusIn and isinstance(obj, self._GUARDED):
                # Popup and menu reasons are left as they are: closing a drop-down
                # hands the focus back, which must not undo the click that opened it.
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

    app.installEventFilter(_WheelGuard(app))


def main():
    """Run Image Finder as a standalone window (without Image Slider)."""
    app = QApplication.instance() or QApplication(sys.argv)
    install_wheel_guard(app)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget     { background: #f3f3f3; color: #111; }
        QLabel      { background: transparent; }
        QPushButton { padding: 5px 8px; }
        QComboBox   { padding: 3px 6px; }
    """)
    win = QMainWindow()
    win.setWindowTitle("Image Finder")
    win.resize(800, 600)
    widget = ImageFinderWidget()
    win.setCentralWidget(widget)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()