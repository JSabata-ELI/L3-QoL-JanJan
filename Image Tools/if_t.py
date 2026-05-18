# if_t.py — Image Finder (PySide6 port)

import bisect
import csv
import json
import os
import re
import shutil
import ssl
import sys
import tempfile
import threading
import time
import atexit
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image as PilImage

from PySide6.QtCore import Qt, QTimer, QDate, QRunnable, QThreadPool, QObject, Signal, QPointF, QRect
from PySide6.QtGui import QColor, QTextCharFormat, QPixmap, QImage, QFont, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QSlider,
    QScrollArea, QFrame, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCalendarWidget, QDialog,
    QFileDialog, QMessageBox, QLineEdit, QMainWindow, QStyledItemDelegate,
    QDialogButtonBox, QSizePolicy, QSplitter, QTabWidget, QProgressBar,
    QButtonGroup, QSpinBox,
)

try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    PRAGUE = None

# ── CONFIG ────────────────────────────────────────────────────────────────────
IMAGES_ROOT_BASE = r"//users-L3.tier0.lcs.local"

RAMPING_CANDIDATES = [
    ("Lab",    r"//hapls-share.cs.eli-beams.eu/scratch/Salvation/2026_alldata"),
    ("Office", r"Z:\Salvation\2026_alldata"),
]
DEFAULT_RAMPING_SOURCE = 0
RAMPING_CSV_GLOB       = "*.csv"

DEFAULT_WINDOW      = 200
DEFAULT_SAMPLE_STEP = 40
DEFAULT_SAMPLE_NEAR = 6
DEFAULT_TOL_KB      = 30.0
MAX_SCAN_FILES      = 2000  # max files to stat() per folder (network perf)

MIN_FULL_FILES     = 1
ACT_MAX_GAP_S      = 120
MIN_SEG_ROWS       = 25
MIN_SEG_DURATION_S = 10 * 60

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
CAM_33HZ   = {65, 66, 67, 63, 64, 60, 57, 58, 53, 54, 55, 56,
              31, 26, 22, 21, 25, 13, 14}

FINAL_RE  = re.compile(r"\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d{6}$")
SOURCE_RE = re.compile(r"(\d+)$")

# ── ENERGY CSV CONFIG ─────────────────────────────────────────────────────────
# Root folder where daily CSV files live
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

# Display names for columns in UI and on annotated images.
# Key = exact CSV column name, Value = label shown to user.
# Columns not listed here use their CSV name as-is.
# Edit freely — these names never affect CSV parsing.
ENERGY_COLUMNS_DISPLAY: dict[str, str] = {
    "waveplate": "Waveplate",
    "ptm1":      "PTM1",
    "pcm2":      "PCM2",
    "pcm4":      "PCM4",
    "pap1":      "PAP1",
    "sbw4":      "SBW4",
    "CampOn":    "Camp ON",
    "E2_Open":   "E2 Open",
    "E3_Open":   "E3 Open",
    "E4_Open":   "E4 Open",
    "E5_Open":   "E5 Open",
    "Back_Ref":  "Back Ref",
}

# Match tolerance in seconds: |t_image - t_csv| must be ≤ this value
ENERGY_MATCH_TOL_S = 2.0

# ── CPVA ARCHIVER API ─────────────────────────────────────────────────────────
CPVA_BASE_URL     = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_HTTP_TIMEOUT = 10.0   # seconds per request

# Channel used to find the best shot (highest energy = real shot, not dark/empty)
CPVA_SHOT_CHANNEL = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"
CPVA_SBW4_CHANNEL = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"

# Maps energy CSV column name → CPVA archiver channel name for API lookup
CPVA_CHANNEL_MAP: dict[str, str] = {
    "ptm1":      "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "pcm2":      "HAPLS-ENER_IN_PCM2_LT6_DIAG2:Energy",
    "pcm4":      "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    "pap1":      "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "sbw4":      "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "Back_Ref":  "L3-PM03-023:Energy",
    "waveplate": "L3-PFWP6-MTR03-1:RawPos",
}


def _cpva_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


import http.client as _http_client

_cpva_conn_lock = threading.Lock()   # serialises all CPVA requests (conn is not thread-safe)
_cpva_conn: "_http_client.HTTPSConnection | None" = None
_CPVA_HOST = CPVA_BASE_URL.split("://", 1)[1].split("/")[0]   # "10.78.0.57:8443"


def _cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                        timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    """Fetch archiver samples, reusing a persistent HTTPS connection."""
    global _cpva_conn
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    })
    path = f"/api/1.0/cpva/samples?{params}"
    with _cpva_conn_lock:
        for attempt in range(2):
            try:
                if _cpva_conn is None:
                    _cpva_conn = _http_client.HTTPSConnection(
                        _CPVA_HOST, timeout=timeout, context=_cpva_ssl_ctx())
                else:
                    # Update timeout on existing connection for this request
                    _cpva_conn.timeout = timeout
                _cpva_conn.request("GET", path, headers={"Accept": "application/json"})
                resp = _cpva_conn.getresponse()
                body = resp.read()
                return json.loads(body.decode("utf-8"))
            except Exception:
                # Drop broken connection and retry once with a fresh one.
                try:
                    _cpva_conn.close()
                except Exception:
                    pass
                _cpva_conn = None
                if attempt == 1:
                    raise


def _cpva_best_shot_ns(start_ns: int, end_ns: int,
                       channel: str = CPVA_SHOT_CHANNEL,
                       timeout: float = CPVA_HTTP_TIMEOUT) -> "int | None":
    """
    Query the CPVA archiver for the shot with the highest energy in [start_ns, end_ns].
    Returns the UTC nanosecond timestamp of that shot, or None on any failure.
    Only considers samples with value > 0.
    """
    try:
        samples = _cpva_fetch_samples(channel, start_ns, end_ns, timeout=timeout)
        if not isinstance(samples, list) or not samples:
            return None
        best_t: int | None = None
        best_v: float = 0.0
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
            if v > best_v:
                best_v = v
                best_t = int(t_ns)
        return best_t  # None if no sample with v > 0
    except Exception:
        return None


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


# Annotation bar appearance
ENERGY_BAR_HEIGHT_PX    = 40    # height of white bar added below image
ENERGY_BAR_FONT_SIZE_PT = 24   # font size for annotation text
ENERGY_BAR_BG_COLOR     = (255, 255, 255)   # RGB white
ENERGY_BAR_TEXT_COLOR   = (0,   0,   0)     # RGB black

INFO_TEXT = """\
Image Finder — Image Tools

Steps:
  1. Check Ramping source (Lab / Office).
  2. Pick a date — auto-hour is chosen from ramping CSV.
     If "Lab time?" is unchecked: program looks for folders in Prague time.
       Example: for 19:00, it looks in folder 18:00 in the archiver.
     If "Lab time?" is checked: looks in lab time (1 hour later).
  3. Select one or multiple camera folders in the table.
     Click the row to toggle selection. Double-click qty cell to edit.
  4. Selected folders appear in the "SELECTED" list.
     Cameras marked "YES" in 3.3+Hz? column may take longer.
  5. Click View to open images, or Save As... to copy to a folder.
  6. Enable "Auto-open in Slider" to automatically switch to the
     Image Slider tab and load the first selected camera folder.
  7. Use Add to A / Add to B + Compare A vs B for diff comparison.
     Double-click a row in SELECTED to deselect it.
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

GRADIENTS = {
    "Grayscale":       None,
    "Gradient":        _make_lut([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
    "Hot":             _make_lut([(0,(0,0,0)),(0.33,(255,0,0)),(0.66,(255,255,0)),(1,(255,255,255))]),
    "Binary":          _make_stepped_lut([(0,(0,0,0)),(0.17,(255,0,0)),(0.33,(255,165,0)),(0.5,(255,255,0)),(0.67,(0,255,0)),(0.83,(0,200,255)),(0.92,(0,0,255)),(1,(255,255,255))]),
    "Black and White": _make_binary_lut(),
    "Viridis":         _make_lut([(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))]),
    "Plasma":          _make_lut([(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))]),
    "Inferno":         _make_lut([(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))]),
    "Jet":             _make_lut([(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))]),
    "Turbo":           _make_lut([(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))]),
}
GRADIENT_NAMES = list(GRADIENTS.keys())

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
    """Read imgMaxValue from PNG tEXt metadata — physical maximum pixel value
    recorded by the camera (equivalent to Matlab imgMeta.OtherText{12,2}).
    Returns float or None if not found."""
    if path.suffix.lower() != ".png":
        return None
    try:
        with PilImage.open(str(path)) as pil:
            info = pil.info
            chunks = [(k, v) for k, v in info.items() if isinstance(v, str)]
            if len(chunks) >= 12:
                v = chunks[11][1]
                try:
                    return float(v)
                except (ValueError, TypeError):
                    pass
            for k, v in chunks:
                try:
                    f = float(v)
                    if 0 < f <= 65535:
                        return f
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return None


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

# ── LOGIC HELPERS (unchanged from original) ───────────────────────────────────
def is_valid_image_file(name: str) -> bool:
    n = name.strip()
    if not n or n.startswith("."): return False
    low = n.lower()
    if low in ("thumbs.db", "desktop.ini"): return False
    return Path(low).suffix in IMAGE_EXTS

def extract_display_label(folder_name: str) -> str:
    s = folder_name.strip()
    m = re.match(r"^C\d{2}-\d{3}-(.+)-_-IMG$", s, flags=re.IGNORECASE)
    if m: return m.group(1)
    m = re.match(r"^L3-(.+)-C\d{3}-_-IMG$", s, flags=re.IGNORECASE)
    if m: return m.group(1)
    m = re.match(r"^L3BT-(.+)-_-IMG$", s, flags=re.IGNORECASE)
    if m: return m.group(1)
    return s

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
    dt = dt_utc.astimezone(PRAGUE) if (use_prague_time and PRAGUE) else dt_utc
    return dt.strftime("%Y_%m_%d--%H_%M_%S__%f")

def build_new_name(stem: str, use_prague_time: bool):
    stem_clean = stem.replace("-_-", "_").replace("_-_", "_")
    if FINAL_RE.search(stem_clean): return None, "already_converted"
    m = SOURCE_RE.search(stem_clean)
    if not m: return None, "no_trailing_number"
    ns = int(m.group(1))
    time_str = convert_timestamp(ns, use_prague_time)
    return stem_clean[:m.start(1)] + time_str, None


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
) -> "tuple[list[_EnergyRow], dict[str, list[_EnergyRow]]]":
    """
    Query CPVA archiver for the given day and return (_EnergyRow list, per_col dict).
    Falls back column-by-column to CSV when API returns nothing.
    dt should be a naive Prague-local datetime (used only for the date).

    Returns:
      - merged: list[_EnergyRow] sorted by timestamp (merged across all channels)
      - per_col: dict[str, list[_EnergyRow]] mapping each column to sorted rows that
                 have a value for that column (used for per-column closest-timestamp lookup)
    """
    from datetime import date as _date
    day = _date(dt.year, dt.month, dt.day)

    def _log(msg):
        if log is not None:
            log(msg)

    if PRAGUE is None:
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end   = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        day_start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=PRAGUE)
        day_end   = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=PRAGUE)

    start_ns = int(day_start.timestamp() * 1_000_000_000)
    end_ns   = int(day_end.timestamp()   * 1_000_000_000)

    # Collect per-column rows: {t_ns: {col: value, ...}}
    by_ts: dict[int, dict] = {}
    # Per-column row lists: col -> list of (t_ns, dt_local, val)
    per_col_raw: dict[str, list[tuple[int, datetime, str]]] = {}

    for col in cols:
        channel = CPVA_CHANNEL_MAP.get(col)
        col_rows: list[tuple[int, datetime, str]] = []  # (ns, dt_local, val)

        if channel is not None:
            channels_to_try = [channel]
            if not channel.endswith(".value"):
                channels_to_try.append(channel + ".value")
            for ch_try in channels_to_try:
                try:
                    samples = _cpva_fetch_samples(ch_try, start_ns, end_ns)
                    if not isinstance(samples, list):
                        continue
                    parsed = 0
                    for s in samples:
                        t_ns = s.get("time")
                        if t_ns is None:
                            continue
                        val = s.get("value")
                        if isinstance(val, list):
                            val = val[0] if val else None
                        if val is None:
                            continue
                        t_ns = int(t_ns)
                        if PRAGUE is not None:
                            dt_local = datetime.fromtimestamp(
                                t_ns / 1e9, tz=timezone.utc).astimezone(PRAGUE).replace(tzinfo=None)
                        else:
                            dt_local = datetime.utcfromtimestamp(t_ns / 1e9)
                        col_rows.append((t_ns, dt_local, str(val)))
                        parsed += 1
                    _log(f"  API {col} ({ch_try}): {len(samples)} raw → {parsed} parsed")
                    if parsed > 0:
                        break
                except Exception as exc:
                    _log(f"  API {col} ({ch_try}) ERROR: {type(exc).__name__}: {exc}")
        else:
            _log(f"  {col}: no CPVA channel mapping, trying CSV only")

        # CSV fallback if API returned nothing
        if not col_rows:
            root = csv_root if csv_root is not None else ENERGY_CSV_ROOT
            fname = dt.strftime(ENERGY_CSV_NAME_FMT) + ".csv"
            csv_path = Path(root) / fname
            csv_rows = _load_energy_csv(csv_path)
            for r in csv_rows:
                if col in r.values:
                    # Convert Prague-naive dt to ns
                    if PRAGUE is not None:
                        t_ns = int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1_000_000_000)
                    else:
                        t_ns = int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000)
                    col_rows.append((t_ns, r.ts_dt, r.values[col]))
            if col_rows:
                _log(f"  CSV fallback {col}: {len(col_rows)} rows")

        if col_rows:
            per_col_raw[col] = col_rows

        for t_ns, dt_local, val in col_rows:
            if t_ns not in by_ts:
                by_ts[t_ns] = {}
            by_ts[t_ns].setdefault("_dt", dt_local)
            by_ts[t_ns][col] = val

    # Convert to _EnergyRow objects sorted by timestamp
    result: list[_EnergyRow] = []
    for t_ns in sorted(by_ts):
        entry = by_ts[t_ns]
        dt_local = entry.get("_dt", datetime.utcfromtimestamp(t_ns / 1e9))
        values = {k: v for k, v in entry.items() if k != "_dt"}
        result.append(_EnergyRow(dt_local, values))

    # Build per_col: col -> sorted list of _EnergyRow that have only that col's value
    per_col: dict[str, list[_EnergyRow]] = {}
    for col, rows_raw in per_col_raw.items():
        rows_raw_sorted = sorted(rows_raw, key=lambda x: x[0])
        per_col[col] = [
            _EnergyRow(dt_local, {col: val})
            for t_ns, dt_local, val in rows_raw_sorted
        ]

    return result, per_col


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
    # Build ns list for binary search
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


def _format_energy_diff_s(diff_s: float) -> str:
    """Format a time difference in seconds to a readable string."""
    diff_s = abs(diff_s)
    if diff_s < 1.0:
        return f"{diff_s*1000:.0f} ms"
    return f"{diff_s:.1f} s"

def _format_energy_value(col: str, raw_val: str) -> str:
    """Format a CSV value with appropriate units and conversion."""
    v = raw_val.strip() if raw_val else "—"
    if v == "—" or v == "":
        return "—"
    # YES/NO columns
    if col in ("CampOn", "E2_Open", "E3_Open", "E4_Open", "E5_Open"):
        try:
            return "YES" if int(float(v)) == 1 else "NO"
        except Exception:
            return v
    # mJ columns (value in CSV is in J → multiply by 1000)
    if col in ("Back_Ref", "pap1"):
        try:
            return f"{float(v) * 1000:.2f} mJ"
        except Exception:
            return f"{v} mJ"
    # Plain J columns
    if col in ("ptm1", "pcm2", "pcm4", "sbw4"):
        try:
            v_f = float(v)
            if col == "sbw4":
                v_f = v_f * 0.749   # actual energy after optics
            return f"{v_f:.3f} J"
        except Exception:
            return f"{v} J"
    # Waveplate — plain number, no unit
    if col == "waveplate":
        try:
            return f"{int(float(v))}"
        except Exception:
            return v
    # Fallback
    return v

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
            label = ENERGY_COLUMNS_DISPLAY.get(col, col)
            parts.append(f"{label}: {val}")
        text = "   |   ".join(parts) if parts else "(no columns selected)"
    else:
        img_dt_local = datetime.fromtimestamp(img_ts_ns / 1_000_000_000, tz=timezone.utc)
        if PRAGUE:
            img_dt_local = img_dt_local.astimezone(PRAGUE).replace(tzinfo=None)
        else:
            img_dt_local = img_dt_local.replace(tzinfo=None)
        msg_parts = ["No CSV match"]
        if no_match_before is not None:
            diff = _format_energy_diff_s(abs((img_dt_local - no_match_before.ts_dt).total_seconds()))
            ts_str = no_match_before.ts_dt.strftime("%H:%M:%S.%f")[:-3]
            msg_parts.append(f"before: {ts_str} (−{diff})")
        if no_match_after is not None:
            diff = _format_energy_diff_s(abs((no_match_after.ts_dt - img_dt_local).total_seconds()))
            ts_str = no_match_after.ts_dt.strftime("%H:%M:%S.%f")[:-3]
            msg_parts.append(f"after: {ts_str} (+{diff})")
        text = "   |   ".join(msg_parts)

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
    Used by MultiDayPreviewWindow to annotate with camera name + timestamp + PV values.
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


class _ThumbView(QWidget):
    """
    Thumbnail widget used inside MultiDayPreviewWindow.
    Displays a QPixmap and draws circle / square / cross overlays via QPainter
    using normalised coordinates (0–1), exactly like ImageView in is_t.py.
    Supports drag-to-create and drag-handle interaction for each shape.
    """
    clicked      = Signal()           # left click without drag (selection)
    dbl_clicked  = Signal()
    hovered_in   = Signal()
    hovered_out  = Signal()
    right_clicked = Signal()          # right-click (context menu)

    _HANDLE_R = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix:    "QPixmap | None" = None
        self._scaled: "QPixmap | None" = None
        self._is_selected: bool = False

        # overlay state — normalised [0,1] relative to the displayed image rect
        self.show_circle = False
        self.circle_center_norm: "QPointF | None" = None
        self.circle_rx_norm: "float | None" = None
        self.circle_ry_norm: "float | None" = None

        self.show_square = False
        self.square_rect_norm: "tuple[float,float,float,float] | None" = None

        self.show_cross = False
        self.cross_pos_norm: "QPointF | None" = None
        self.cross_size = 18
        self.cross_thickness = 2

        # colours (same defaults as ImageView in is_t.py)
        self.circle_color = QColor(255, 255, 0, 230)
        self.circle_thick = 2
        self.square_color = QColor(0, 200, 255, 230)
        self.square_thick = 2
        self.cross_color  = QColor(0, 255, 0, 220)

        # draw-mode: "" | "circle" | "square" | "cross"
        self._draw_mode: str = ""
        self._drag_start:  "QPointF | None" = None
        self._drag_handle: str = ""
        self._did_drag = False      # distinguish click vs drag

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── pixmap ────────────────────────────────────────────────────────────────
    def set_pixmap(self, pm: QPixmap):
        self._pix = pm; self._scaled = None; self.update()

    def _ensure_scaled(self):
        if self._pix is None or self._pix.isNull():
            self._scaled = None; return
        if self.width() <= 0 or self.height() <= 0:
            self._scaled = None; return
        if self._scaled is None or self._scaled.size() != self.size():
            self._scaled = self._pix.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

    def resizeEvent(self, event):
        super().resizeEvent(event); self._scaled = None; self.update()

    def _img_rect(self) -> "QRect | None":
        self._ensure_scaled()
        if self._scaled is None or self._scaled.isNull():
            return None
        x0 = (self.width()  - self._scaled.width())  // 2
        y0 = (self.height() - self._scaled.height()) // 2
        return QRect(x0, y0, self._scaled.width(), self._scaled.height())

    # ── draw mode ─────────────────────────────────────────────────────────────
    def set_draw_mode(self, mode: str):
        self._draw_mode = mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode else Qt.CursorShape.ArrowCursor)
        self.update()

    # ── handle helpers ────────────────────────────────────────────────────────
    def _circle_handles(self, ir: QRect) -> dict:
        if self.circle_center_norm is None: return {}
        cx = ir.left() + int(self.circle_center_norm.x() * ir.width())
        cy = ir.top()  + int(self.circle_center_norm.y() * ir.height())
        rx = int((self.circle_rx_norm or 0) * ir.width())
        ry = int((self.circle_ry_norm or 0) * ir.height())
        return {
            "move": QPointF(cx, cy),
            "n":    QPointF(cx, cy - ry),
            "s":    QPointF(cx, cy + ry),
            "e":    QPointF(cx + rx, cy),
            "w":    QPointF(cx - rx, cy),
        }

    def _square_handles(self, ir: QRect) -> dict:
        if self.square_rect_norm is None: return {}
        ln, tn, rn, bn = self.square_rect_norm
        sx = ir.left() + int(ln * ir.width())
        sy = ir.top()  + int(tn * ir.height())
        ex = ir.left() + int(rn * ir.width())
        ey = ir.top()  + int(bn * ir.height())
        mx, my = (sx + ex) // 2, (sy + ey) // 2
        return {
            "move": QPointF(mx, my),
            "nw": QPointF(sx, sy), "ne": QPointF(ex, sy),
            "sw": QPointF(sx, ey), "se": QPointF(ex, ey),
            "n":  QPointF(mx, sy), "s":  QPointF(mx, ey),
            "w":  QPointF(sx, my), "e":  QPointF(ex, my),
        }

    def _hit_handle(self, pos: QPointF, handles: dict) -> str:
        r = self._HANDLE_R + 3
        for name, pt in handles.items():
            if abs(pos.x() - pt.x()) <= r and abs(pos.y() - pt.y()) <= r:
                return name
        return ""

    # ── mouse ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event); return
        ir = self._img_rect()
        if ir is None or ir.width() <= 0 or ir.height() <= 0:
            super().mousePressEvent(event); return
        pos = event.position()
        self._did_drag = False

        if self._draw_mode == "cross":
            self.cross_pos_norm = QPointF(
                max(0.0, min(1.0, (pos.x() - ir.left()) / ir.width())),
                max(0.0, min(1.0, (pos.y() - ir.top())  / ir.height())))
            self.update(); return

        if self._draw_mode == "circle":
            if self.circle_center_norm is not None and self.circle_rx_norm is not None:
                hit = self._hit_handle(pos, self._circle_handles(ir))
                if hit:
                    self._drag_handle = hit; self._drag_start = pos; return
            self._drag_handle = "new"; self._drag_start = pos; return

        if self._draw_mode == "square":
            if self.square_rect_norm is not None:
                hit = self._hit_handle(pos, self._square_handles(ir))
                if hit:
                    self._drag_handle = hit; self._drag_start = pos; return
            self._drag_handle = "new"; self._drag_start = pos; return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        ir = self._img_rect()
        if ir is None or ir.width() <= 0 or ir.height() <= 0: return
        pos = event.position()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # cursor feedback when not dragging
        if self._draw_mode in ("circle", "square") and not self._drag_handle:
            handles = (self._circle_handles(ir) if self._draw_mode == "circle"
                       else self._square_handles(ir))
            hit = self._hit_handle(pos, handles)
            cursors = {
                "move": Qt.CursorShape.SizeAllCursor,
                "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
                "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
            }
            self.setCursor(cursors.get(hit, Qt.CursorShape.CrossCursor))

        if not (event.buttons() & Qt.MouseButton.LeftButton): return
        if self._drag_start is None: return
        self._did_drag = True

        def clamp(v): return max(0.0, min(1.0, v))
        def norm(px, py):
            return clamp((px - ir.left()) / ir.width()), clamp((py - ir.top()) / ir.height())

        if self._draw_mode == "circle":
            if self._drag_handle == "new":
                x0, y0 = self._drag_start.x(), self._drag_start.y()
                dx = (pos.x() - x0) / ir.width()
                dy = (pos.y() - y0) / ir.height()
                if shift:
                    r = max(abs(dx), abs(dy)); rx_n = ry_n = r
                else:
                    rx_n, ry_n = abs(dx), abs(dy)
                self.circle_center_norm = QPointF(*norm(x0, y0))
                self.circle_rx_norm = rx_n; self.circle_ry_norm = ry_n
            elif self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                self.circle_center_norm = QPointF(
                    clamp(self.circle_center_norm.x() + dx),
                    clamp(self.circle_center_norm.y() + dy))
            elif self._drag_handle in ("e", "w"):
                cx_px = ir.left() + self.circle_center_norm.x() * ir.width()
                rx_n = abs(pos.x() - cx_px) / ir.width()
                if shift: self.circle_ry_norm = rx_n
                self.circle_rx_norm = rx_n
            elif self._drag_handle in ("n", "s"):
                cy_px = ir.top() + self.circle_center_norm.y() * ir.height()
                ry_n = abs(pos.y() - cy_px) / ir.height()
                if shift: self.circle_rx_norm = ry_n
                self.circle_ry_norm = ry_n
            self.update()

        elif self._draw_mode == "square":
            if self._drag_handle == "new":
                cx0, cy0 = norm(self._drag_start.x(), self._drag_start.y())
                nx1, ny1 = norm(pos.x(), pos.y())
                dx, dy = abs(nx1 - cx0), abs(ny1 - cy0)
                if shift: dx = dy = max(dx, dy)
                self.square_rect_norm = (
                    clamp(cx0 - dx), clamp(cy0 - dy),
                    clamp(cx0 + dx), clamp(cy0 + dy))
            elif self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                ln, tn, rn, bn = self.square_rect_norm
                w_ = rn - ln; h_ = bn - tn
                ln = clamp(ln + dx); tn = clamp(tn + dy)
                self.square_rect_norm = (ln, tn, clamp(ln + w_), clamp(tn + h_))
            else:
                ln, tn, rn, bn = self.square_rect_norm
                h_ = self._drag_handle
                nx, ny = norm(pos.x(), pos.y())
                if "w" in h_: ln = min(nx, rn - 0.01)
                if "e" in h_: rn = max(nx, ln + 0.01)
                if "n" in h_: tn = min(ny, bn - 0.01)
                if "s" in h_: bn = max(ny, tn + 0.01)
                if shift:
                    if   h_ == "se": side = max(rn-ln, bn-tn); rn = ln+side; bn = tn+side
                    elif h_ == "nw": side = max(rn-ln, bn-tn); ln = rn-side; tn = bn-side
                    elif h_ == "ne": side = max(rn-ln, bn-tn); rn = ln+side; tn = bn-side
                    elif h_ == "sw": side = max(rn-ln, bn-tn); ln = rn-side; bn = tn+side
                self.square_rect_norm = (clamp(ln), clamp(tn), clamp(rn), clamp(bn))
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._did_drag \
                and self._draw_mode == "":
            self.clicked.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
        self._drag_handle = ""; self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dbl_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event):
        self.hovered_in.emit(); super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered_out.emit(); super().leaveEvent(event)

    # ── paint ─────────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        if self._pix is None or self._pix.isNull():
            # Still draw selection border even without image
            if self._is_selected:
                pen = QPen(QColor("#4a9eff")); pen.setWidth(3)
                p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(1, 1, self.width() - 2, self.height() - 2)
            p.end(); return
        self._ensure_scaled()
        if self._scaled is None or self._scaled.isNull():
            p.end(); return

        x0 = (self.width()  - self._scaled.width())  // 2
        y0 = (self.height() - self._scaled.height()) // 2
        p.drawPixmap(x0, y0, self._scaled)
        ir = QRect(x0, y0, self._scaled.width(), self._scaled.height())

        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── cross ──────────────────────────────────────────────────────────
        if self.show_cross:
            if self.cross_pos_norm is not None:
                cx = ir.left() + int(self.cross_pos_norm.x() * ir.width())
                cy = ir.top()  + int(self.cross_pos_norm.y() * ir.height())
            else:
                cx, cy = ir.center().x(), ir.center().y()
            pen = QPen(self.cross_color); pen.setWidth(self.cross_thickness); p.setPen(pen)
            p.drawLine(cx - self.cross_size, cy, cx + self.cross_size, cy)
            p.drawLine(cx, cy - self.cross_size, cx, cy + self.cross_size)

        # ── circle ─────────────────────────────────────────────────────────
        if self.show_circle and self.circle_center_norm is not None \
                and self.circle_rx_norm is not None and self.circle_ry_norm is not None:
            cx = ir.left() + int(self.circle_center_norm.x() * ir.width())
            cy = ir.top()  + int(self.circle_center_norm.y() * ir.height())
            rx = int(self.circle_rx_norm * ir.width())
            ry = int(self.circle_ry_norm * ir.height())
            pen = QPen(self.circle_color); pen.setWidth(self.circle_thick); p.setPen(pen)
            p.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
            if self._draw_mode == "circle":
                for pt in self._circle_handles(ir).values():
                    p.setPen(QPen(self.circle_color))
                    p.setBrush(QColor(self.circle_color.red(), self.circle_color.green(),
                                      self.circle_color.blue(), 120))
                    p.drawEllipse(int(pt.x()) - self._HANDLE_R, int(pt.y()) - self._HANDLE_R,
                                  self._HANDLE_R * 2, self._HANDLE_R * 2)
                    p.setBrush(Qt.BrushStyle.NoBrush)

        # ── square ─────────────────────────────────────────────────────────
        if self.show_square and self.square_rect_norm is not None:
            ln, tn, rn, bn = self.square_rect_norm
            sx = ir.left() + int(ln * ir.width())
            sy = ir.top()  + int(tn * ir.height())
            sw = int((rn - ln) * ir.width())
            sh = int((bn - tn) * ir.height())
            pen = QPen(self.square_color); pen.setWidth(self.square_thick); p.setPen(pen)
            p.drawRect(sx, sy, sw, sh)
            if self._draw_mode == "square":
                for pt in self._square_handles(ir).values():
                    p.setPen(QPen(self.square_color))
                    p.setBrush(QColor(self.square_color.red(), self.square_color.green(),
                                      self.square_color.blue(), 120))
                    p.drawEllipse(int(pt.x()) - self._HANDLE_R, int(pt.y()) - self._HANDLE_R,
                                  self._HANDLE_R * 2, self._HANDLE_R * 2)
                    p.setBrush(Qt.BrushStyle.NoBrush)

        # Selection border — drawn last so it's always on top
        if self._is_selected:
            pen = QPen(QColor("#4a9eff")); pen.setWidth(3)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(1, 1, self.width() - 2, self.height() - 2)

        p.end()

    # ── selection border ──────────────────────────────────────────────────────
    def set_selected(self, on: bool):
        self._is_selected = on
        self.update()  # trigger paintEvent to redraw border


def _write_annotated_from_pil(img: "PilImage.Image", dst: Path, text: str) -> None:
    """
    Add a white annotation bar below an already-rendered PIL image and save to dst.
    Used by MultiDayPreviewWindow so the saved file reflects exactly what is shown
    (palette / brightness / rotation / overlays already applied).
    """
    from PIL import Image as _Img, ImageDraw as _ID, ImageFont as _IF

    img = img.convert("RGB")
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
    tmp_draw = _ID.Draw(_Img.new("RGB", (1, 1)))
    try:
        line_w = max((tmp_draw.textlength(p, font=font) for p in parts_list), default=0)
    except Exception:
        line_w = w  # fallback: always split
    padding = 6
    line_h = fsize + padding

    if line_w <= w - padding * 2:
        display_lines = ["   |   ".join(parts_list)]
    else:
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

class EnergyColumnDialog(QDialog):
    """
    Modal dialog listing available energy columns as checkboxes.
    User selects which columns to annotate on saved images.
    Selection is remembered globally for the session.
    """
    def __init__(self, current_selection: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select energy columns")
        self.setMinimumWidth(300)
        self._checks: dict[str, QCheckBox] = {}

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Select which CSV columns to annotate on saved images.\n"
            "All available columns from today's data file:"))

        for col in ENERGY_COLUMNS_AVAILABLE:
            label = ENERGY_COLUMNS_DISPLAY.get(col, col)
            cb = QCheckBox(label)
            cb.setChecked(col in current_selection)
            cb.setStyleSheet(_CHECKBOX_STYLE)
            self._checks[col] = cb   # key is always the CSV column name
            lay.addWidget(cb)

        # Select all / None buttons
        row = QHBoxLayout()
        btn_all  = QPushButton("Select all")
        btn_none = QPushButton("Select none")
        btn_all.clicked.connect(lambda: [c.setChecked(True)  for c in self._checks.values()])
        btn_none.clicked.connect(lambda: [c.setChecked(False) for c in self._checks.values()])
        row.addWidget(btn_all); row.addWidget(btn_none)
        lay.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_columns(self) -> list[str]:
        return [col for col, cb in self._checks.items() if cb.isChecked()]


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
        self._row_to_path: dict[int, Path] = {}
        self._load_gen = 0
        self._checked: dict[int, bool] = {}
        self.primary_files: list[Path] = []
        self._namecache: dict = {}
        self._collect_busy = False

        self._mem_a: Path | None = None
        self._mem_b: Path | None = None

        self._view_temp_dir: str | None = None
        self._view_temp_paths: list[Path] = []
        atexit.register(self._cleanup_view_temp)

        self._user_has_selected_day = False
        self._range_gen = 0   # generation counter for range searches
        self._autoload_timer: QTimer | None = None
        self._auto_hour_last_day = None

        self.RAMPING_ROOT: Path | None = None
        self._ramping_cache: dict = {}
        self._ramping_source: str = RAMPING_CANDIDATES[DEFAULT_RAMPING_SOURCE][0]

        # column sort state
        self._label_sort_asc = True
        self._num_sort_asc   = True

        # ── energy CSV state ─────────────────────────────────────────────────
        # Selected columns — loaded from ENERGY_COLUMNS_DEFAULT, user can change
        self._energy_selected_cols: list[str] = list(ENERGY_COLUMNS_DEFAULT)
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
        self._preview_idx: int = 0
        self._preview_cam: str = ""
        self._preview_from_view: bool = False  # True when preview was loaded by View button
        self._tp_dead_channels: set[str] = set()  # channels that timed out → skip next time

        self._build_ui()
        # Trigger today's load after the event loop starts
        QTimer.singleShot(0, self._auto_select_today)

    # ── LOGGING ───────────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool):
        for btn in [self._btn_view, self._btn_save, self._btn_range,
                    self._btn_open_folder,
                    self._btn_info, self._btn_compare, self._btn_energy_cols,
                    self._ramping_cb, self._gradient_cb, self._hour_cb,
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
        # Main layout: horizontal
        #   LEFT side:  QVBoxLayout — left_scroll | table (stretch) | log (fixed)
        #   RIGHT side: preview label (stretch, full height)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6); outer.setSpacing(6)

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

        # ramping source
        ll.addWidget(_group_label("Ramping source"))
        rs_row = QHBoxLayout(); rs_row.addWidget(QLabel("Source:"))
        self._ramping_cb = _NoScrollComboBox()
        for name, _ in RAMPING_CANDIDATES: self._ramping_cb.addItem(name)
        self._ramping_cb.setCurrentText(self._ramping_source)
        self._ramping_cb.currentTextChanged.connect(self._on_ramping_source_change)
        rs_row.addWidget(self._ramping_cb, 1); ll.addLayout(rs_row)

        ll.addWidget(_hsep())
        ll.addWidget(_group_label("Time"))

        # calendar — delegate + styling copied from is.py DatePickerDialog
        self._cal = _NoScrollCalendar()
        self._cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self._cal.setGridVisible(True)
        self._cal.setNavigationBarVisible(True)
        self._cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.ISOWeekNumbers)
        self._cal.setMinimumWidth(238)

        # Install weekend delegate on internal table view
        _cal_view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if _cal_view:
            _cal_view.setItemDelegate(_WeekendDelegate(_cal_view))

        hf = QTextCharFormat(); hf.setForeground(QColor("#111111"))
        self._cal.setHeaderTextFormat(hf)

        wf = QTextCharFormat(); wf.setForeground(QColor("#111111"))
        for day in [Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                    Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday]:
            self._cal.setWeekdayTextFormat(day, wf)
        wf_we = QTextCharFormat(); wf_we.setForeground(QColor("#cc0000"))
        for day in [Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday]:
            self._cal.setWeekdayTextFormat(day, wf_we)

        self._cal.setStyleSheet("""
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
            padding: 4px 8px; border-radius: 4px; color: #111; }
        QCalendarWidget QSpinBox, QCalendarWidget QComboBox {
            background: #fff; border: 1px solid #c8c8c8; padding: 2px 6px; color: #111; }
        QCalendarWidget QWidget#qt_calendar_navigationbar {
            background: #efefef; }
        QCalendarWidget QAbstractItemView:enabled { color: #111; }
        """)
        self._cal.selectionChanged.connect(self._on_calendar_selected)
        ll.addWidget(self._cal)

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
        self._status_dot = QLabel("⬤")
        self._status_dot.setStyleSheet("color: green; font-size: 12px;")
        hour_row.addWidget(self._status_dot)
        ll.addLayout(hour_row)

        self._btn_range = QPushButton("Multi-day search...")
        self._btn_range.setToolTip("Search images across multiple days for selected cameras")
        self._btn_range.clicked.connect(self._on_multiday_search)
        ll.addWidget(self._btn_range)

        ll.addWidget(_hsep())
        # ── Info tab (energy data) ────────────────────────────────────────────
        ll.addWidget(_group_label("Energy info"))

        self._energy_info = QPlainTextEdit()
        self._energy_info.setReadOnly(True)
        self._energy_info.setMaximumHeight(120)
        self._energy_info.setPlaceholderText(
            "Energy values appear here after View.")
        self._energy_info.setStyleSheet(
            "font-family:Consolas,monospace;font-size:11px;"
            "background:#f9f9f9;border:1px solid #ddd;")
        ll.addWidget(self._energy_info)
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
        self._btn_energy_cols.setToolTip("Choose which CSV columns to show / annotate")
        self._btn_energy_cols.clicked.connect(self._pick_energy_columns)
        nav_row.addWidget(self._btn_energy_cols)
        ll.addLayout(nav_row)

        # Row 2: Navigate images  |  Annotate energies
        cb_row = QHBoxLayout()
        self._cb_nav_images = QCheckBox("Nav imgs")
        self._cb_nav_images.setChecked(False)
        self._cb_nav_images.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_nav_images.setToolTip(
            "Checked: arrows move between loaded images.\n"
            "Unchecked: arrows move through CSV rows around current image.")
        self._cb_nav_images.stateChanged.connect(self._on_nav_mode_changed)
        self._cb_annotate = QCheckBox("attach PVs")
        self._cb_annotate.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_annotate.setToolTip(
            "Add a white bar with energy values below the image.\n"
            "Applied both when viewing and when saving.")
        cb_row.addWidget(self._cb_nav_images, 1)
        cb_row.addWidget(self._cb_annotate, 1)
        ll.addLayout(cb_row)
        ll.addWidget(_hsep())
        ll.addWidget(_group_label("Controls"))

        # action buttons
        btn_grid = QGridLayout(); btn_grid.setSpacing(4)
        self._btn_view = QPushButton("View")
        self._btn_view.clicked.connect(self.view_primary_files)
        self._btn_save = QPushButton("Save As...")
        self._btn_save.clicked.connect(self.save_primary_files_as)
        btn_grid.addWidget(self._btn_view, 0, 0)
        btn_grid.addWidget(self._btn_save, 0, 1)

        # Open Folder + Info on one row, compact
        self._btn_open_folder = QPushButton("📁 Folder")
        self._btn_open_folder.clicked.connect(self.open_folder_in_explorer)
        self._btn_info = QPushButton("Info")
        self._btn_info.clicked.connect(self.show_info)
        btn_grid.addWidget(self._btn_open_folder, 1, 0)
        btn_grid.addWidget(self._btn_info, 1, 1)

        # Auto-open in Slider at the end of the Controls group
        self._auto_open_cb = QCheckBox("Auto-open in Slider")
        self._auto_open_cb.setStyleSheet(_CHECKBOX_STYLE)
        self._auto_open_cb.setToolTip(
            "After View/Save, switch to Image Slider tab and\n"
            "load the first selected camera folder.")
        btn_grid.addWidget(self._auto_open_cb, 3, 0, 1, 2)

        self._btn_send_workshop = QPushButton("➤ Workshop")
        self._btn_send_workshop.setToolTip("Send currently selected image to Workshop tab for editing")
        self._btn_send_workshop.clicked.connect(self._send_to_workshop)
        btn_grid.addWidget(self._btn_send_workshop, 4, 0, 1, 2)

        # Gradient — in Controls so it affects the preview image
        grad_row = QHBoxLayout(); grad_row.addWidget(QLabel("Gradient:"))
        self._gradient_cb = _NoScrollComboBox()
        for name in GRADIENT_NAMES: self._gradient_cb.addItem(name)
        self._gradient_cb.setCurrentText("Gradient")
        self._gradient_cb.currentTextChanged.connect(self._on_gradient_changed)
        grad_row.addWidget(self._gradient_cb, 1)
        btn_grid.addLayout(grad_row, 5, 0, 1, 2)

        ll.addLayout(btn_grid)

        ll.addWidget(_hsep())
        # selected mini table
        self._sel_count_lbl = QLabel("SELECTED (0)")
        self._sel_count_lbl.setStyleSheet(
            "font-size:10px;color:#777;font-weight:700;letter-spacing:1px;")
        ll.addWidget(self._sel_count_lbl)

        self._sel_table = QTableWidget(0, 3)
        self._sel_table.setHorizontalHeaderLabels(["Cam #", "# imgs", "Camera"])
        self._sel_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._sel_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._sel_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._sel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._sel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sel_table.setMaximumHeight(220)
        self._sel_table.doubleClicked.connect(self._on_sel_table_double_clicked)
        ll.addWidget(self._sel_table)

        ll.addWidget(_hsep())
        
        ll.addWidget(_group_label("Comparison"))

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

        ll.addStretch(1)
        left_scroll.setWidget(lw)

        # ════ TABLE PANEL ════════════════════════════════════════════════════
        rw = QWidget()
        rw.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        rl = QVBoxLayout(rw); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(4)

        # ── Search row + nav arrows in one line ────────────────────────────────
        search_row = QHBoxLayout(); search_row.setSpacing(4)
        search_row.addWidget(QLabel("Subfolders"))
        search_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit(); self._search_edit.setFixedWidth(140)
        self._search_edit.textChanged.connect(self._apply_search)
        search_row.addWidget(self._search_edit)
        btn_clr = QPushButton("X"); btn_clr.setFixedWidth(26)
        btn_clr.clicked.connect(lambda: self._search_edit.clear())
        search_row.addWidget(btn_clr)

        # nav arrows right next to the search/X
        sep_line = QFrame(); sep_line.setFrameShape(QFrame.Shape.VLine)
        sep_line.setFrameShadow(QFrame.Shadow.Sunken); sep_line.setFixedWidth(8)
        search_row.addWidget(sep_line)
        self._prev_btn = QPushButton("◀"); self._prev_btn.setFixedWidth(26)
        self._prev_btn.clicked.connect(self._preview_prev)
        self._next_btn = QPushButton("▶"); self._next_btn.setFixedWidth(26)
        self._next_btn.clicked.connect(self._preview_next)
        self._preview_counter = QLabel("")
        self._preview_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_counter.setStyleSheet("font-size:10px; color:#555;")
        self._preview_counter.setFixedWidth(52)
        search_row.addWidget(self._prev_btn)
        search_row.addWidget(self._preview_counter)
        search_row.addWidget(self._next_btn)
        rl.addLayout(search_row)

        # ── Camera table ───────────────────────────────────────────────────────
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["[ ]", "Cam #", "# of imgs", "3.3+Hz?", "Label"])

        hh = self._table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed);          self._table.setColumnWidth(0, 36)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed);          self._table.setColumnWidth(1, 52)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed);          self._table.setColumnWidth(2, 68)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed);          self._table.setColumnWidth(3, 58)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(False)
        self._table.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        rl.addWidget(self._table, 1)

        # Assemble top_row: left_scroll + table (stretch=0: table sizes to content)
        top_row.addWidget(left_scroll)
        top_row.addWidget(rw, 0)
        root.addLayout(top_row, 1)

        # ── Log box — full width of left side, fixed height ───────────────────
        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(100)
        self._log_box.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        self._log_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._log_box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        root.addWidget(self._log_box, 0)

        # left_side uses stretch=0 so it shrinks to fit its content (table columns);
        # preview_col gets all remaining space via stretch=1
        outer.addWidget(left_side, 0)

        # ── Preview — right column, full height ───────────────────────────────
        preview_col = QWidget()
        preview_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pcl = QVBoxLayout(preview_col)
        pcl.setContentsMargins(0, 0, 0, 0); pcl.setSpacing(2)

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

        outer.addWidget(preview_col, 1)

        self._log("READY. No network scan on startup.")
        self._log(f"IMAGES_ROOT_BASE = {IMAGES_ROOT_BASE}")
        self._log(f"Default ramping source = {self._ramping_source}")
        self._log("Select a day to start ramping auto-hour + load folders.")

    # ── TABLE HELPERS ─────────────────────────────────────────────────────────
    def _get_original(self, row: int) -> str:
        p = self._row_to_path.get(row)
        return p.name if p else ""

    def _get_qty(self, row: int) -> int:
        item = self._table.item(row, 2)
        try: return max(0, int(item.text())) if item else 1
        except: return 1

    def _set_check_visual(self, row: int, checked: bool):
        self._checked[row] = checked
        self._table.blockSignals(True)
        for c in range(self._table.columnCount()):
            it = self._table.item(row, c)
            if it is None:
                continue
            f = it.font()
            f.setBold(checked)
            it.setFont(f)
        item = self._table.item(row, 0)
        if item:
            item.setText("[x]" if checked else "[ ]")
        self._table.blockSignals(False)

    def _on_cell_clicked(self, row: int, col: int):
        # Toggle checkbox when clicking anywhere EXCEPT the qty column (col 2)
        if col != 2:
            self._toggle_row(row)
        self._preview_load_from_row(row)

    def _on_cell_double_clicked(self, row: int, col: int):
        if col == 2:
            self._table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
            self._table.editItem(self._table.item(row, 2))
            self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def _on_cell_changed(self, row: int, col: int):
        if col == 2:
            self._refresh_selected_table()

    def _on_header_clicked(self, col: int):
        if col == 0: self._toggle_all()
        elif col == 1: self.sort_subfolders_by_camnum()
        elif col == 4: self.sort_subfolders_by_label()

    def _toggle_row(self, row: int):
        cur = self._checked.get(row, False)
        self._set_check_visual(row, not cur)
        self._refresh_selected_table()
        self._refresh_master_checkbox()

    def _toggle_all(self):
        n = self._table.rowCount()
        if n == 0: return
        all_checked = all(self._checked.get(r, False) for r in range(n))
        target = not all_checked
        for r in range(n):
            self._set_check_visual(r, target)
        self._refresh_selected_table()
        self._refresh_master_checkbox()

    def _refresh_master_checkbox(self):
        n = self._table.rowCount()
        if n == 0: self._table.setHorizontalHeaderItem(0, QTableWidgetItem("[ ]")); return
        cnt = sum(1 for r in range(n) if self._checked.get(r, False))
        txt = "[ ]" if cnt == 0 else "[x]" if cnt == n else "[-]"
        self._table.setHorizontalHeaderItem(0, QTableWidgetItem(txt))

    # ── Inline preview panel ──────────────────────────────────────────────────
    def _preview_load_from_row(self, row: int):
        """Enumerate folder in background then show first image."""
        folder = self._row_to_path.get(row)
        if folder is None:
            return
        cam_item = self._table.item(row, 4)
        cam_name = cam_item.text() if cam_item else folder.name
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
                self._preview_idx   = 0
                self._preview_cam   = cam_name
                self._preview_show()

            QTimer.singleShot(0, _on_done)

        threading.Thread(target=_scan, daemon=True).start()

    def _preview_set_files(self, files: list, cam_name: str = ""):
        """Set the preview to a specific file list (e.g. after View)."""
        if not files:
            return
        self._preview_paths    = list(files)
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
        lbl = self._preview_lbl
        avail_w = max(lbl.width(),  200)
        avail_h = max(lbl.height(), 200)
        pm = pm.scaled(avail_w, avail_h,
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        lbl.setPixmap(pm)

    def _preview_show(self):
        if not self._preview_paths:
            return
        idx   = self._preview_idx
        total = len(self._preview_paths)
        path  = self._preview_paths[idx]
        self._preview_counter.setText(f"{idx + 1} / {total}")
        self._prev_btn.setEnabled(total > 1)
        self._next_btn.setEnabled(total > 1)
        self._preview_cam_lbl.setText(self._preview_cam or path.parent.name)
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
        self._preview_gen += 1
        gen = self._preview_gen
        sig = self._preview_sig
        grad_name = self._gradient_cb.currentText()

        def _load():
            try:
                img = PilImage.open(path)
                if img.mode in ("I", "I;16"):
                    arr = np.array(img, dtype=np.float32)
                else:
                    arr = np.array(img.convert("L"), dtype=np.float32)
                img_max_val = _read_img_max_value(path)
                arr_px_max = float(arr.max())
                if img_max_val is not None and arr_px_max > 0:
                    arr = img_max_val * arr / arr_px_max
                arr8 = np.clip(arr / 4095.0 * 255.0, 0, 255).astype(np.uint8)
                lut = GRADIENTS.get(grad_name)
                if lut is not None:
                    pil_img = PilImage.fromarray(lut[arr8].astype(np.uint8), mode="RGB")
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
        """Save checked rows as {original_folder_name: qty} — identical to original."""
        saved = {}
        for r in range(self._table.rowCount()):
            if not self._checked.get(r, False): continue
            saved[self._get_original(r)] = self._get_qty(r)
        return saved

    def _refresh_selected_table(self):
        self._sel_table.setRowCount(0)
        rows = []
        for r in range(self._table.rowCount()):
            if not self._checked.get(r, False): continue
            num      = (self._table.item(r, 1) or QTableWidgetItem("")).text()
            qty      = self._get_qty(r)
            label    = (self._table.item(r, 4) or QTableWidgetItem("")).text()
            original = self._get_original(r)
            rows.append((num, qty, label, original))
        rows.sort(key=lambda t: (t[0].lower(), t[2].lower(), t[3].lower()))
        for num, qty, label, _ in rows:
            row = self._sel_table.rowCount(); self._sel_table.insertRow(row)
            self._sel_table.setItem(row, 0, QTableWidgetItem(num))
            self._sel_table.setItem(row, 1, QTableWidgetItem(str(qty)))
            self._sel_table.setItem(row, 2, QTableWidgetItem(label))
        self._sel_count_lbl.setText(f"SELECTED ({len(rows)})")

    def _fit_table_width(self):
        """Cap the camera table width to fit its column contents exactly."""
        hh = self._table.horizontalHeader()
        w = sum(self._table.columnWidth(c) for c in range(self._table.columnCount()))
        w += self._table.verticalHeader().width() if self._table.verticalHeader().isVisible() else 0
        sb = self._table.verticalScrollBar()
        if sb and sb.isVisible():
            w += sb.width()
        else:
            w += 18  # reserve for scrollbar that appears when rows overflow
        self._table.setMaximumWidth(w)

    def _on_sel_table_double_clicked(self, index):
        """Double-click in selected mini table → deselect that camera."""
        row   = index.row()
        label = (self._sel_table.item(row, 2) or QTableWidgetItem("")).text()
        for r in range(self._table.rowCount()):
            lbl_item = self._table.item(r, 4)
            if lbl_item and lbl_item.text() == label and self._checked.get(r, False):
                self._toggle_row(r); break

    def _apply_search(self, query: str):
        q = query.strip().lower()
        for r in range(self._table.rowCount()):
            label    = (self._table.item(r, 4) or QTableWidgetItem("")).text().lower()
            original = self._get_original(r).lower()
            self._table.setRowHidden(r, bool(q) and (q not in label) and (q not in original))

    # ── SORTING ───────────────────────────────────────────────────────────────
    def sort_subfolders_by_label(self):
        asc = self._label_sort_asc; self._label_sort_asc = not asc
        rows = [(self._table.item(r, 4) or QTableWidgetItem("")).text().lower()
                for r in range(self._table.rowCount())]
        order = sorted(range(len(rows)), key=lambda i: rows[i], reverse=not asc)
        self._reorder_table_rows(order)
        arrow = "▲" if asc else "▼"
        self._table.setHorizontalHeaderItem(4, QTableWidgetItem(f"Label {arrow}"))

    def sort_subfolders_by_camnum(self):
        asc = self._num_sort_asc; self._num_sort_asc = not asc

        def key_for_num(s: str):
            s = (s or "").strip().upper()
            if not s: return (2, 0, "")
            if s.startswith("C") and s[1:].isdigit(): return (0, int(s[1:]), s)
            if s.isdigit(): return (0, int(s), s)
            return (1, 0, s)

        rows = [(self._table.item(r, 1) or QTableWidgetItem("")).text()
                for r in range(self._table.rowCount())]
        order = sorted(range(len(rows)), key=lambda i: key_for_num(rows[i]), reverse=not asc)
        self._reorder_table_rows(order)
        arrow = "▲" if asc else "▼"
        self._table.setHorizontalHeaderItem(1, QTableWidgetItem(f"# {arrow}"))

    def _reorder_table_rows(self, new_order: list[int]):
        n = self._table.rowCount()
        if n == 0: return
        # Snapshot
        snapshot = []
        for r in range(n):
            row_data = [(self._table.item(r, c) or QTableWidgetItem("")).text()
                        for c in range(self._table.columnCount())]
            snapshot.append((row_data, self._row_to_path.get(r), self._checked.get(r, False)))
        self._table.blockSignals(True)
        self._row_to_path.clear(); self._checked.clear()
        for new_r, old_r in enumerate(new_order):
            row_data, path, checked = snapshot[old_r]
            for c, text in enumerate(row_data):
                item = QTableWidgetItem(text)
                if c == 5: item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(new_r, c, item)
            self._row_to_path[new_r] = path
            self._checked[new_r]     = checked
        self._table.blockSignals(False)

    # ── EVENTS ────────────────────────────────────────────────────────────────
    def _auto_select_today(self):
        """Called once after startup — simulate selecting today's date."""
        self._user_has_selected_day = True
        qd = self._cal.selectedDate()
        self._log(f"Auto-selecting today: {qd.day():02d}.{qd.month():02d}.{qd.year()}")
        self._apply_auto_hour_for_selected_day()

    def _on_calendar_selected(self):
        self._user_has_selected_day = True
        self._status_dot.setStyleSheet("color: gray; font-size: 12px;")
        qd = self._cal.selectedDate()
        self._log(f"Calendar selected: {qd.day():02d}.{qd.month():02d}.{qd.year()}")
        self._apply_auto_hour_for_selected_day()

    def _on_hour_change(self):
        self._log_selected_datetime_preview()
        if self._user_has_selected_day:
            self._schedule_autoload(150)

    def _on_labtime_toggle(self):
        self._log(f"Lab time toggled -> {self._lab_time_cb.isChecked()}")
        self._auto_hour_last_day = None
        if self._user_has_selected_day:
            self._apply_auto_hour_for_selected_day()
        else:
            self._log_selected_datetime_preview()

    def _on_ramping_source_change(self, name: str):
        self._ramping_source = name
        self._log(f"RAMPING SOURCE -> {name}")
        self._ramping_cache.clear(); self._auto_hour_last_day = None; self.RAMPING_ROOT = None
        if self._user_has_selected_day:
            self._apply_auto_hour_for_selected_day()

    def _on_gradient_changed(self, name: str):
        self._log(f"GRADIENT -> {name}")
        self._namecache.clear()
        if self._preview_paths:
            self._preview_show()

    # ── ENERGY CSV METHODS ────────────────────────────────────────────────────

    def _pick_energy_columns(self):
        """Open the column picker dialog and update selected columns."""
        dlg = EnergyColumnDialog(self._energy_selected_cols, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._energy_selected_cols = dlg.selected_columns()
            self._log(f"ENERGY: columns = {self._energy_selected_cols}")
            # Refresh displayed info with new column selection
            if self._energy_results:
                self._refresh_energy_info()

    def _get_energy_rows_for_dt(self, dt: datetime) -> list[_EnergyRow]:
        """
        Return cached energy rows for the date of dt.
        Tries CPVA API first; falls back to CSV. Called from background thread.
        Also populates self._energy_per_col_cache for per-column closest-timestamp lookups.
        """
        day_key = dt.strftime("%Y-%m-%d")
        if day_key in self._energy_cache:
            return self._energy_cache[day_key]
        cols = self._energy_selected_cols
        if cols:
            rows, per_col = _energy_api_for_day(dt, cols, log=self._log_safe)
            if rows:
                self._log_safe(f"ENERGY: {len(rows)} rows via API for {day_key}")
                self._energy_cache[day_key] = rows
                self._energy_per_col_cache[day_key] = per_col
                return rows
        # CSV fallback
        csv_path = _energy_csv_path(dt)
        rows = _load_energy_csv(csv_path)
        self._energy_cache[day_key] = rows
        self._energy_per_col_cache[day_key] = {}  # no per_col from CSV fallback
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

            results.append((path, match, before, after, rows, match_idx, per_col))
        return results

    def _refresh_energy_info(self):
        """Reset navigation to first result and display it."""
        self._energy_nav_index = 0
        self._energy_csv_offset = 0
        self._energy_csv_anchor_idx = None
        self._energy_csv_anchor_rows = []
        self._refresh_energy_info_single()

    def _on_nav_mode_changed(self):
        """Reset CSV offset when switching navigation mode."""
        self._energy_csv_offset = 0
        self._energy_csv_anchor_idx = None
        self._energy_csv_anchor_rows = []
        self._refresh_energy_info_single()

    def _energy_nav_prev(self):
        if not self._energy_results:
            return
        if self._cb_nav_images.isChecked():
            # Image navigation mode — move between loaded images
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
        if self._cb_nav_images.isChecked():
            # Image navigation mode — move between loaded images
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

        nav_images = self._cb_nav_images.isChecked()
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

        # ── Filename → Prague time ────────────────────────────────────────
        ns_stem = extract_ns_from_stem(path.stem)
        if ns_stem is not None:
            dt_utc  = datetime.fromtimestamp(ns_stem / 1_000_000_000, tz=timezone.utc)
            dt_local = dt_utc.astimezone(PRAGUE) if PRAGUE else dt_utc
            name = dt_local.strftime("%H:%M:%S.%f")[:-3]
        else:
            name = path.name

        lines = []

        if not nav_images and csv_offset != 0 and base_idx is not None and csv_rows:
            # ── CSV navigation mode — show neighbouring row ───────────────
            target_csv_idx = base_idx + csv_offset
            if 0 <= target_csv_idx < len(csv_rows):
                row = csv_rows[target_csv_idx]
                parts = []
                for col in self._energy_selected_cols:
                    val   = _format_energy_value(col, row.values.get(col, "—"))
                    label = ENERGY_COLUMNS_DISPLAY.get(col, col)
                    parts.append(f"{label}={val}")
                ts = row.ts_dt.strftime("%H:%M:%S.%f")[:-3]
                lines.append(f"CSV {ts}  (outside set)\n  " + "  |  ".join(parts))
            else:
                lines.append("(no more CSV rows)")

        elif match is not None:
            # ── Matched image ─────────────────────────────────────────────
            img_ns = ns_stem or 0
            parts = []
            for col in self._energy_selected_cols:
                raw_val = match.values.get(col, "")
                if not raw_val or raw_val == "—":
                    # Fallback: search per_col for nearest value within 2s
                    raw_val = _find_closest_per_col_value(
                        per_col_for_entry, col, img_ns, tol_s=2.0)
                val   = _format_energy_value(col, raw_val)
                label = ENERGY_COLUMNS_DISPLAY.get(col, col)
                parts.append(f"{label}={val}")
            lines.append(f"📷 {name}\n  " + "  |  ".join(parts))

        else:
            # ── No match ──────────────────────────────────────────────────
            img_ns  = ns_stem or 0
            img_dt2 = datetime.fromtimestamp(img_ns / 1_000_000_000, tz=timezone.utc)
            img_dt2 = img_dt2.astimezone(PRAGUE).replace(tzinfo=None) if PRAGUE else img_dt2.replace(tzinfo=None)
            parts   = [f"📷 {name}:  No CSV match"]
            if before:
                ts   = before.ts_dt.strftime("%H:%M:%S.%f")[:-3]
                diff = _format_energy_diff_s(abs((img_dt2 - before.ts_dt).total_seconds()))
                vals = "  |  ".join(
                    f"{ENERGY_COLUMNS_DISPLAY.get(c,c)}={_format_energy_value(c, before.values.get(c,'—'))}"
                    for c in self._energy_selected_cols)
                parts.append(f"  before: {ts} (−{diff})\n    {vals}")
            if after:
                ts   = after.ts_dt.strftime("%H:%M:%S.%f")[:-3]
                diff = _format_energy_diff_s(abs((after.ts_dt - img_dt2).total_seconds()))
                vals = "  |  ".join(
                    f"{ENERGY_COLUMNS_DISPLAY.get(c,c)}={_format_energy_value(c, after.values.get(c,'—'))}"
                    for c in self._energy_selected_cols)
                parts.append(f"  after:  {ts} (+{diff})\n    {vals}")
            lines.append("\n".join(parts))

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
                wp = int(float(r.get(fieldmap["waveplate"])))
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

        # Immediately set default hour and start loading — don't wait for CSV.
        self._hour_cb.blockSignals(True)
        self._hour_cb.setCurrentIndex(self._DEFAULT_HOUR)
        self._hour_cb.blockSignals(False)
        self._schedule_autoload(50)

        # In background: try CSV auto-hour; if it differs from default, reload.
        self._ensure_ramping_root()
        self._auto_hour_sig = _AutoHourSignals()
        self._auto_hour_sig.log_msg.connect(self._log)
        self._auto_hour_sig.apply.connect(self._apply_auto_hour_ui)

        def worker():
            try:
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
        # Only reload if auto-hour changed from what we already loaded
        if hour_changed:
            self._schedule_autoload(50)

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
        # Container share name uses max(year, 2025) — years before 2025 are stored inside cpva-image-2025
        container_year = max(year, 2025)
        root = Path(IMAGES_ROOT_BASE) / f"cpva-image-{container_year}"
        return root / str(year) / str(dt.month) / str(dt.day) / str(dt.hour)

    def _log_selected_datetime_preview(self):
        """Log the effective target path — identical to original."""
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

        # Clear table immediately on main thread (instant)
        self._status_dot.setStyleSheet("color: gray; font-size: 12px;")
        saved_sel = self._capture_selection_state()
        self._checked.clear()
        self._table.blockSignals(True); self._table.setRowCount(0); self._table.blockSignals(False)
        self._row_to_path.clear(); self._load_gen += 1
        current_gen = self._load_gen; self.primary_files = []
        self._table.setHorizontalHeaderItem(1, QTableWidgetItem("Cam #"))
        self._table.setHorizontalHeaderItem(4, QTableWidgetItem("Label"))

        dt          = self._build_datetime()
        target_path = self._build_target_path(dt)
        self._log(f"Load -> effective_dt={dt.isoformat(sep=' ')} | target={target_path}")

        self._load_sig = _LoadSignals()
        self._load_sig.done.connect(self._on_load_done)
        self._load_sig.not_found.connect(self._on_load_not_found)
        self._load_sig.error.connect(self._on_load_error)
        self._load_sig.log_msg.connect(self._log)
        _sig = self._load_sig  # local ref — prevents GC if load_folders() called again

        def worker():
            try:
                # Collect camera folders from ALL hours in the selected day.
                # This mirrors Shot Finder behaviour so cameras active at any
                # hour of the day are visible regardless of the hour selector.
                year = dt.year
                container_year = max(year, 2025)
                root = Path(IMAGES_ROOT_BASE) / f"cpva-image-{container_year}"
                day_dir = root / str(year) / str(dt.month) / str(dt.day)

                seen: set[str] = set()
                all_entries: list[Path] = []
                found_any_hour = False

                for h in range(24):
                    hour_dir = day_dir / str(h)
                    try:
                        if not hour_dir.exists() or not hour_dir.is_dir():
                            continue
                    except Exception:
                        continue
                    found_any_hour = True
                    try:
                        for e in os.scandir(hour_dir):
                            if e.is_dir() and e.name not in seen:
                                seen.add(e.name)
                                all_entries.append(Path(e.path))
                    except Exception:
                        pass

                if not found_any_hour:
                    _sig.not_found.emit(target_path)
                    return

                subfolders = sorted(all_entries, key=lambda x: x.name.lower())
                _sig.log_msg.emit(f"LOAD: scanned all hours in {day_dir.name}, found {len(subfolders)} cameras")
                _sig.done.emit(subfolders, saved_sel, current_gen)
            except Exception as e:
                _sig.error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=worker, daemon=True).start()


    def _on_load_not_found(self, target_path: Path):
        if not self.isVisible():
            return
        self._log(f"Target folder not found: {target_path}")
        QMessageBox.warning(self, "Not found", f"Folder does not exist:\n{target_path}")
        self._refresh_selected_table()

    def _on_load_error(self, err: str):
        if not self.isVisible():
            return
        self._log(f"ERROR: {err}")
        QMessageBox.critical(self, "Error", err)

    def _on_load_done(self, subfolders: list[Path], saved_sel: dict[str, int], gen: int):
        """Called on main thread after background scan — fills the table."""
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
            self._table.blockSignals(True)
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
                qty        = int(saved_sel.get(p.name, 1))
                is_checked = p.name in saved_sel

                row = self._table.rowCount(); self._table.insertRow(row)
                self._table.setItem(row, 0, QTableWidgetItem("[x]" if is_checked else "[ ]"))
                self._table.setItem(row, 1, QTableWidgetItem(num))
                self._table.setItem(row, 2, QTableWidgetItem(str(qty)))
                self._table.setItem(row, 3, QTableWidgetItem(hz33))
                self._table.setItem(row, 4, QTableWidgetItem(label_display))
                self._row_to_path[row] = p
                self._checked[row]     = is_checked

            self._table.blockSignals(False)
            for r in range(self._table.rowCount()):
                if self._checked.get(r, False):
                    for c in range(self._table.columnCount()):
                        it = self._table.item(r, c)
                        if it:
                            f = it.font(); f.setBold(True); it.setFont(f)
            self._log(f"_on_load_done: table filled, calling refresh")
            self._refresh_master_checkbox()
            self._refresh_selected_table()
            QTimer.singleShot(0, self._fit_table_width)
            self._log(f"Subfolders loaded: {len(subfolders)}")
            if self._search_edit.text().strip():
                self._apply_search(self._search_edit.text())
            self._status_dot.setStyleSheet("color: green; font-size: 14px;")
        except Exception as e:
            self._table.blockSignals(False)
            self._log(f"_on_load_done ERROR: {type(e).__name__}: {e}")
            import traceback
            self._log(traceback.format_exc())


    # ── OPEN IN SLIDER / EXPLORER ─────────────────────────────────────────────
    def open_in_slider(self):
        """Directly open the first checked folder in the embedded Viewer (no subprocess)."""
        if not self._user_has_selected_day:
            QMessageBox.information(self, "Info", "Select a day first."); return
        if self._slider_ref is None or self._tab_widget is None:
            QMessageBox.information(self, "Info", "Image Slider not connected."); return
        jobs    = self._snapshot_collect_jobs()
        folders = [f for f, _ in jobs if f and f.exists() and f.is_dir()]
        if not folders:
            QMessageBox.information(self, "Info", "No cameras selected."); return
        if len(folders) > 1:
            self._log(f"SLIDER: {len(folders)} folders selected, opening FIRST: {folders[0].name}")
        self._tab_widget.setCurrentIndex(1)
        self._slider_ref.open_folder_path(folders[0])
        self._log(f"SLIDER: opened {folders[0].name}")

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
        for folder in folders:
            try:
                subprocess.Popen(["explorer", str(folder)])
                self._log(f"EXPLORER: {folder}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")

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

        Strategy:
          1. TotalPower query for this camera.
             - No active windows → camera was inactive; return status='inactive'
             - Active windows found → pick the nearest file inside the first window
             - PTM1 + SBW4 are fetched additively (best-energy timestamp used as
               target for file lookup; values stored in meta regardless).
             - No TotalPower channel derivable → fallback to blind scan.
          2. Blind hour-by-hour scan (only when TotalPower channel unavailable).

        Returns (path, real_hour, meta, status) where:
          status ∈ 'found' | 'inactive' | 'not_found' | 'cancelled'
          meta   = {'ptm1': float|None, 'sbw4': float|None}
        """
        def log(msg: str):
            if log_fn:
                log_fn(f"  {msg}")

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        _no_meta: dict = {"ptm1": None, "sbw4": None}

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

                # Pick a random timestamp inside the first active window and find
                # the nearest image file.  No additional PV queries needed.
                import random as _random
                w_start, w_end = windows[0]
                target_ns = _random.randint(w_start, w_end)
                dt_tgt = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
                if PRAGUE and not use_lab:
                    dt_tgt = dt_tgt.astimezone(PRAGUE)
                log(f"  target: {dt_tgt.strftime('%H:%M:%S')}  (random inside first window)")

                if is_cancelled():
                    return None, None, _no_meta, "cancelled"

                p, h = try_timestamp(target_ns)
                if is_cancelled():
                    return None, None, _no_meta, "cancelled"

                if p is not None:
                    return p, h, _no_meta, "found"
                return None, None, _no_meta, "not_found"

            except _FallbackToBlindScan:
                pass   # no active windows — continue to blind scan below
            except Exception as e:
                log(f"  TotalPower error: {e}")
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            if not _tp_no_windows:
                log("  TotalPower failed, falling back to blind scan")

        # ── Method 2: Blind hour-by-hour scan (no TotalPower channel) ─────────
        log(f"method2: blind scan h={start_hour_real}–{max_hour_real}")
        for real_h in range(start_hour_real, max_hour_real + 1):
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            folder_h = real_to_folder_h(real_h)
            dt_eff   = datetime(year, month, day_n, folder_h)
            cam_folder = self._build_target_path(dt_eff) / cam_name
            log(f"  blind h={real_h:02d}  {cam_folder}")
            t_exist = time.perf_counter()
            exists = bc(lambda cf=cam_folder: cf.exists(), cancelled)
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            if not exists:
                log(f"  not found  ({time.perf_counter()-t_exist:.2f}s)")
                continue
            t0 = time.perf_counter()
            found_file = bc(lambda cf=cam_folder: self._any_image_from_folder(cf),
                            cancelled)
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            log(f"  scan {time.perf_counter()-t0:.2f}s  →  {found_file.name if found_file else 'nothing'}")
            if found_file:
                return found_file, real_h, _no_meta, "found"

        return None, None, _no_meta, "not_found"

    def _on_multiday_search(self):
        """Open multi-day search setup dialog."""
        # Build camera list from checked rows in the main table
        all_cams = []
        for r in range(self._table.rowCount()):
            if not self._checked.get(r, False):
                continue
            folder = self._row_to_path.get(r)
            if folder is None:
                continue
            label_item = self._table.item(r, 4)
            cam_label = label_item.text() if label_item and label_item.text() else folder.name
            all_cams.append((folder.name, cam_label, folder))

        if not all_cams:
            QMessageBox.information(self, "Multi-day search",
                                    "No cameras checked in the table. Check at least one camera first."); return

        dlg = _MultiDaySetupDialog(all_cams, self._cal.selectedDate(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        cfg = dlg.get_config()
        if not cfg["cameras"]:
            QMessageBox.information(self, "Multi-day search", "Select at least one camera."); return
        if not cfg["days"]:
            QMessageBox.information(self, "Multi-day search", "No days in selected range."); return

        use_lab         = cfg["use_lab_time"]
        start_hour_real = cfg["start_hour"]
        max_hour_real   = cfg["max_hour"]

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
            total_entries = sum(len(v) for v in results.values())
            if total_entries == 0:
                QMessageBox.information(self, "Multi-day search", "No images found.")
                return
            # Send all found images to inline preview panel
            found_paths = sorted(
                [p for v in results.values() for _, _, p, _, _ in v if p is not None],
                key=lambda x: x.name)
            if found_paths:
                self._preview_set_files(found_paths, "Multi-day")
            # Also open the full multi-day window
            preview = MultiDayPreviewWindow(results, cfg["cameras"],
                                            use_lab, self)
            preview.open_in_finder_requested.connect(self._on_multiday_open_day)
            preview.show()

        _sig.done.connect(on_signal)
        threading.Thread(target=worker, daemon=True).start()
        prog_dlg.exec()

    def _get_csv_best_hour_for_day(self, day) -> int | None:
        """Return the best real Prague-time hour for a given date from Salvation CSV, or None."""
        try:
            if self.RAMPING_ROOT is None:
                return None
            import glob as _glob
            month_str = day.strftime("%Y%b")
            pattern = str(self.RAMPING_ROOT / f"dataof{month_str}_*.csv")
            files = _glob.glob(pattern)
            if not files:
                # Also try day pattern
                pattern2 = str(self.RAMPING_ROOT / f"dataof{day.year}{day.strftime('%b')}_{day.day:02d}.csv")
                files = _glob.glob(pattern2)
            if not files:
                return None
            target_date_str = day.strftime("%Y-%m-%d")
            best_hour = None
            best_count = 0
            hour_counts: dict[int, int] = {}
            for fpath in files:
                try:
                    with open(fpath, newline="", encoding="utf-8", errors="replace") as f:
                        reader = csv.reader(f)
                        for row in reader:
                            if not row:
                                continue
                            ts_str = row[0].strip() if row else ""
                            if not ts_str.startswith(target_date_str):
                                continue
                            try:
                                dt = datetime.fromisoformat(ts_str.replace(" ", "T"))
                                if use_lab := self._lab_time_cb.isChecked():
                                    h = dt.hour
                                else:
                                    if PRAGUE is not None:
                                        dt_p = dt.replace(tzinfo=timezone.utc).astimezone(PRAGUE)
                                        h = dt_p.hour
                                    else:
                                        h = dt.hour
                                hour_counts[h] = hour_counts.get(h, 0) + 1
                            except Exception:
                                continue
                except Exception:
                    continue
            if not hour_counts:
                return None
            # Pick hour with most rows, but apply tail-ratio check (same as auto-hour)
            sorted_hours = sorted(hour_counts.keys())
            if not sorted_hours:
                return None
            TAIL_RATIO = 0.15
            # Remove last hour if it looks like a partial segment
            if len(sorted_hours) >= 2:
                last_h = sorted_hours[-1]
                prev_h = sorted_hours[-2]
                if hour_counts[last_h] < hour_counts[prev_h] * TAIL_RATIO:
                    sorted_hours = sorted_hours[:-1]
            # Return the hour with most rows among remaining
            best_hour = max(sorted_hours, key=lambda h: hour_counts[h])
            return best_hour
        except Exception:
            return None

    def _on_multiday_open_day(self, day, cam_folder_name: str):
        """Called when user clicks a thumbnail to open that day in the main finder."""
        if not self.isVisible():
            return
        qdate = QDate(day.year, day.month, day.day)
        self._cal.setSelectedDate(qdate)
        self._cal.activated.emit(qdate)

    # ── COLLECT JOBS ──────────────────────────────────────────────────────────
    def _snapshot_collect_jobs(self) -> list[tuple[Path, int]]:
        jobs = []
        for r in range(self._table.rowCount()):
            if not self._checked.get(r, False): continue
            qty    = self._get_qty(r)
            folder = self._row_to_path.get(r)
            if folder is None: continue
            jobs.append((folder, max(0, qty)))
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
        window: int = DEFAULT_WINDOW,
        tol_kb: float = DEFAULT_TOL_KB,
        sample_step: int = DEFAULT_SAMPLE_STEP,
        sample_near: int = DEFAULT_SAMPLE_NEAR,
        debug: bool = False,
        debug_log=None,
    ) -> list[Path]:
        """Identical algorithm to original if.py."""
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
            return [folder / best_seg[-1][1]]

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

    def _apply_gradient_to_image(self, img: PilImage.Image, src_path: "Path | None" = None) -> PilImage.Image:
        """Apply currently selected gradient LUT."""
        name = self._gradient_cb.currentText()
        lut  = GRADIENTS.get(name)
        if lut is None: return img
        arr = np.array(img)
        if arr.ndim == 3: arr = arr.mean(axis=2)
        arr = arr.astype(np.float32)
        self._log(f"IMG range: min={arr.min():.0f} max={arr.max():.0f} dtype={img.mode} shape={arr.shape}")
        img_max_val = _read_img_max_value(src_path) if src_path is not None else None
        arr_px_max = float(arr.max())
        if img_max_val is not None and arr_px_max > 0:
            arr = img_max_val * arr / arr_px_max
        arr = np.clip(arr / 4095.0 * 255.0, 0, 255)
        return PilImage.fromarray(lut[arr.astype(np.uint8)].astype(np.uint8), mode="RGB")

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

        def mk_debug_log(folder_name: str):
            return lambda s: self._log_safe(f"{folder_name}: {s}")

        def worker(folder: Path, qty: int):
            chosen = self.select_images_from_folder(
                folder, qty,
                window=DEFAULT_WINDOW, tol_kb=DEFAULT_TOL_KB,
                sample_step=DEFAULT_SAMPLE_STEP, sample_near=DEFAULT_SAMPLE_NEAR,
                debug=True, debug_log=mk_debug_log(folder.name),
            )
            return folder, qty, chosen

        max_workers = min(8, len(jobs))
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
    def view_primary_files(self):
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
            # Use the label of the first row as cam_name
            cam_name = ""
            for row in range(self._table.rowCount()):
                if self._checked.get(row, False):
                    item = self._table.item(row, 4)
                    cam_name = item.text() if item else ""
                    break
            self._preview_set_files(files, cam_name)

            def after_energy(results: list):
                self._energy_results = results
                self._refresh_energy_info()
                if self._auto_open_cb.isChecked():
                    self._open_first_in_slider()

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
            dest = QFileDialog.getExistingDirectory(self, "Select destination folder")
            if not dest: return
            dest_path = Path(dest)
            copied = 0; skipped_already = 0; skipped_nomatch = 0; annotated = 0

            # Build energy lookup map if annotation is requested
            annotate = self._cb_annotate.isChecked()
            energy_map: dict[str, tuple] = {}   # path.name -> (match, before, after)
            if annotate:
                # Always do a fresh lookup — results may be stale or from different files
                for entry in self._lookup_energy_for_files(files):
                    path, match, before, after = entry[0], entry[1], entry[2], entry[3]
                    energy_map[str(path)] = (match, before, after)

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

                    grad_name = self._gradient_cb.currentText()

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
                                self._apply_gradient_to_image(PilImage.open(src)).save(tmp_path)
                                _annotate_image_with_energy(
                                    tmp_path, dst, match, before, after,
                                    img_ts_ns, self._energy_selected_cols)
                            finally:
                                try: tmp_path.unlink()
                                except: pass
                        else:
                            _annotate_image_with_energy(
                                src, dst, match, before, after,
                                img_ts_ns, self._energy_selected_cols)
                        annotated += 1
                    else:
                        if grad_name != "Grayscale":
                            try: self._apply_gradient_to_image(PilImage.open(src), src).save(dst)
                            except: shutil.copy2(src, dst)
                        else:
                            shutil.copy2(src, dst)

                    copied += 1
                except Exception as e:
                    self._log(f"SAVE ERROR: {src} -> {type(e).__name__}: {e}")

            msg = f"Copied {copied} files to:\n{dest_path}"
            if annotated:
                msg += f"\n- {annotated} files annotated with energy data"
            if skipped_already:
                msg += f"\n- {skipped_already} files already in final format (kept name)"
            if skipped_nomatch:
                msg += f"\n- {skipped_nomatch} files had no trailing ns timestamp (kept name)"
            QMessageBox.information(self, "Done", msg)
            if self._auto_open_cb.isChecked():
                self._open_first_in_slider()
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
        jobs  = self._snapshot_collect_jobs()
        total = sum(qty for _, qty in jobs)
        if total != 1:
            QMessageBox.information(self, "Memory",
                "Select exactly 1 image (qty=1 for one camera)."); return
        if self.primary_files and len(self.primary_files) == 1:
            self._log("MEMORY: reusing result from last View (no rescan)")
            self._do_save_to_memory_slot(self.primary_files[0], slot); return
        def after_collect(files):
            if not files: QMessageBox.information(self, "Memory", "No image found."); return
            self._do_save_to_memory_slot(files[0], slot)
        self._collect_primary_files_async(after_collect)

    def _send_to_workshop(self):
        """Collect primary files and send the first one to Workshop tab."""
        wk = getattr(self, "_workshop_ref", None)
        if wk is None:
            return

        def after_collect(files):
            if not files:
                QMessageBox.information(self, "Workshop", "No image selected."); return
            src = files[0]
            try:
                from PIL import Image as _PilImg
                import numpy as _np
                pil = _PilImg.open(str(src))
                if pil.mode in ("I", "I;16"):
                    arr_f = _np.array(pil, dtype=_np.float32)
                elif pil.mode in ("RGB", "RGBA"):
                    arr_f = _np.array(pil.convert("L"), dtype=_np.float32)
                else:
                    arr_f = _np.array(pil.convert("L"), dtype=_np.float32)

                img_max_val = _read_img_max_value(src)
                arr_px_max = float(arr_f.max())
                if img_max_val is not None and arr_px_max > 0:
                    arr_f = img_max_val * arr_f / arr_px_max
                arr8 = _np.clip(arr_f / 4095.0 * 255.0, 0, 255).astype(_np.uint8)

                cam_name = src.parent.name
                label = f"{cam_name}  |  {src.name}"
                wk.receive_image(arr8, label)
            except Exception as e:
                QMessageBox.warning(self, "Workshop", f"Could not send image:\n{e}")

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
                self._load_sig.error.emit(f"{type(e).__name__}: {e}")

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
        hint = QLabel("Click to set From, Shift+click to set To")
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

        _cal_view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if _cal_view:
            _cal_view.setItemDelegate(_WeekendDelegate(_cal_view))

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
        self._start_hour_sb.setRange(8, 20)
        self._start_hour_sb.setValue(10)
        self._start_hour_sb.setToolTip(
            "Fallback start hour (real Prague time) used when no CSV data found for the day.\n"
            "If CSV data exists, it overrides this.")
        hour_row.addWidget(self._start_hour_sb)
        hour_row.addSpacing(10)
        hour_row.addWidget(QLabel("Max:"))
        self._max_hour_sb = QSpinBox()
        self._max_hour_sb.setRange(8, 20)
        self._max_hour_sb.setValue(18)
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
        """Colour all selected days blue, clear everything else."""
        # Clear all formats first
        self._cal.setDateTextFormat(QDate(), QTextCharFormat())

        allowed_wd = {i for i, cb in enumerate(self._wd_checks) if cb.isChecked()}
        fmt_sel = QTextCharFormat()
        fmt_sel.setBackground(QColor("#3a6fcf"))
        fmt_sel.setForeground(QColor("#ffffff"))

        days = self._compute_days(allowed_wd)
        for d in days:
            qd = QDate(d.year, d.month, d.day)
            self._cal.setDateTextFormat(qd, fmt_sel)

        self._day_count_lbl.setText(f"{len(days)} days selected")

    def _compute_days(self, allowed_wd=None) -> list:
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

def _NoScrollCalendar():
    """Return a plain QCalendarWidget (no scroll noise)."""
    cal = QCalendarWidget()
    cal.setGridVisible(False)
    return cal


# ── MULTI-DAY PREVIEW WINDOW ──────────────────────────────────────────────────
class MultiDayPreviewWindow(QWidget):
    """Popup window showing found images grouped by camera, with hover preview,
    palette/brightness controls and Try-again progress."""

    open_in_finder_requested = Signal(object, str)   # (date, cam_folder_name)

    _COLS      = 4      # max thumbnails per row
    _MIN_THUMB = 120    # minimum thumbnail size

    # re-use LUT table from is_t (sibling file) if available
    try:
        import importlib.util as _ilu, pathlib as _pl
        _ist_path = _pl.Path(__file__).parent / "is_t.py"
        _spec = _ilu.spec_from_file_location("is_t", _ist_path)
        _ist_mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_ist_mod)
        _GRADIENTS: dict = _ist_mod.GRADIENTS
    except Exception:
        _GRADIENTS: dict = {"Grayscale": None}

    def __init__(self, results: dict, cameras: list, use_lab: bool = False, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Multi-day search results")
        self.resize(900, 680)
        self._results  = results
        self._cameras  = cameras
        self._use_lab  = use_lab
        self._selected: set[tuple]          = set()
        # (cam_name, date) → list of all QLabel instances (may appear in multiple tabs)
        self._thumb_widgets: dict[tuple, list] = {}
        self._thumb_paths:   dict[tuple, Path] = {}
        self._try_hour: dict[tuple, int]    = {}
        self._raw_cache: dict[Path, "np.ndarray"] = {}
        self._popup_win: "QWidget | None"   = None
        # (cam_name, date) → QLabel used as anchor for popup positioning
        self._popup_anchor: dict[tuple, QLabel] = {}
        # all TVs per key (day-tab + cam-tab both stored — needed for _refresh_all_thumbs)
        self._thumb_views_all: dict[tuple, list] = {}
        self._palette   = "Grayscale"
        self._brightness = 0
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # ── Internal state (init before any callbacks reference them) ─────────
        self._draw_circle  = False   # which shape-mode is active (at most one at a time)
        self._draw_square  = False
        self._draw_cross   = False
        self._rotation     = 0
        self._preview_enabled = True
        self._auto_bright  = False
        # _ThumbView widgets: key (cam_name, date) → _ThumbView
        self._thumb_views:    dict[tuple, "_ThumbView"] = {}
        self._thumb_camnames: dict[tuple, str] = {}
        # undo stack: list of dicts, each captures full display state before a change
        self._undo_stack: list[dict] = []

        # ─── Toolbar Row 1: Display ───────────────────────────────────────────
        sep_style = "color:#777; font-size:14px; padding:0 4px;"
        row1 = QHBoxLayout()

        # GROUP: Palette
        row1.addWidget(QLabel("Palette:"))
        self._palette_cb = QComboBox()
        names = list(self._GRADIENTS.keys())
        self._palette_cb.addItems(names)
        self._palette_cb.currentTextChanged.connect(self._on_palette_changed)
        row1.addWidget(self._palette_cb)

        sep1 = QLabel("|"); sep1.setStyleSheet(sep_style); row1.addWidget(sep1)

        # GROUP: Brightness
        row1.addWidget(QLabel("Brightness:"))
        self._bright_slider = QSlider(Qt.Orientation.Horizontal)
        self._bright_slider.setRange(-128, 128)
        self._bright_slider.setValue(0)
        self._bright_slider.setFixedWidth(110)
        self._bright_slider.valueChanged.connect(self._on_brightness_changed)
        row1.addWidget(self._bright_slider)
        self._bright_lbl = QLabel("0")
        self._bright_lbl.setFixedWidth(26)
        row1.addWidget(self._bright_lbl)
        self._btn_auto_bright = QPushButton("Auto Brightness")
        self._btn_auto_bright.setToolTip("Auto-stretch brightness per image (ignores manual slider)")
        self._btn_auto_bright.setCheckable(True)
        self._btn_auto_bright.toggled.connect(self._on_auto_bright_toggled)
        row1.addWidget(self._btn_auto_bright)

        sep2 = QLabel("|"); sep2.setStyleSheet(sep_style); row1.addWidget(sep2)

        # GROUP: Rotation
        btn_rot_cw  = QPushButton("↻ 90°")
        btn_rot_cw.setToolTip("Rotate all images 90° clockwise")
        btn_rot_cw.clicked.connect(lambda: self._rotate_all(+90))
        btn_rot_ccw = QPushButton("↺ 90°")
        btn_rot_ccw.setToolTip("Rotate all images 90° counter-clockwise")
        btn_rot_ccw.clicked.connect(lambda: self._rotate_all(-90))
        row1.addWidget(btn_rot_cw)
        row1.addWidget(btn_rot_ccw)

        sep3 = QLabel("|"); sep3.setStyleSheet(sep_style); row1.addWidget(sep3)

        # GROUP: Preview + Undo + Reset
        self._btn_preview_toggle = QPushButton("Preview ON")
        self._btn_preview_toggle.setToolTip("Toggle mouseover preview popup")
        self._btn_preview_toggle.setCheckable(True)
        self._btn_preview_toggle.setChecked(True)
        self._btn_preview_toggle.toggled.connect(self._on_preview_toggled)
        row1.addWidget(self._btn_preview_toggle)

        btn_undo = QPushButton("↩ Undo")
        btn_undo.setToolTip("Undo the last display change (brightness, rotation, overlay, palette)")
        btn_undo.clicked.connect(self._undo_last)
        row1.addWidget(btn_undo)

        btn_reset = QPushButton("Reset…")
        btn_reset.setToolTip("Reset all display settings to defaults")
        btn_reset.clicked.connect(self._reset_display)
        row1.addWidget(btn_reset)

        row1.addStretch()
        root.addLayout(row1)

        # ─── Toolbar Row 2: Overlay + Save + Try again ────────────────────────
        row2 = QHBoxLayout()

        # GROUP: Overlay shapes — drag to draw, move/resize via handles (like is_t ImageView)
        row2.addWidget(QLabel("Overlay:"))

        def _make_ov_btn(label: str, toggled_cb, color_cb) -> tuple:
            btn = QPushButton(label)
            btn.setToolTip(
                f"Activate {label} draw mode: drag on thumbnail to draw, "
                "drag handles to resize/move. Shift = keep proportional.")
            btn.setCheckable(True)
            btn.toggled.connect(toggled_cb)
            col_btn = QPushButton()
            col_btn.setFixedSize(16, 16)
            col_btn.setToolTip(f"{label} colour")
            col_btn.clicked.connect(color_cb)
            return btn, col_btn

        self._btn_draw_circle, self._col_circle_btn = _make_ov_btn(
            "Circle", self._on_circle_toggled, lambda: self._pick_shape_color("circle"))
        self._btn_draw_square, self._col_square_btn = _make_ov_btn(
            "Square", self._on_square_toggled, lambda: self._pick_shape_color("square"))
        self._btn_draw_cross,  self._col_cross_btn  = _make_ov_btn(
            "Cross",  self._on_cross_toggled,  lambda: self._pick_shape_color("cross"))

        # Default colours matching is_t.py
        self._circle_qcolor = QColor(255, 255, 0, 230)
        self._square_qcolor = QColor(0, 200, 255, 230)
        self._cross_qcolor  = QColor(0, 255, 0, 220)

        def _apply_col_style(btn, qcol):
            btn.setStyleSheet(
                f"background:{qcol.name()}; border:1px solid #888; border-radius:2px;")

        _apply_col_style(self._col_circle_btn, self._circle_qcolor)
        _apply_col_style(self._col_square_btn, self._square_qcolor)
        _apply_col_style(self._col_cross_btn,  self._cross_qcolor)

        for shape_btn, col_btn in (
            (self._btn_draw_circle, self._col_circle_btn),
            (self._btn_draw_square, self._col_square_btn),
            (self._btn_draw_cross,  self._col_cross_btn),
        ):
            row2.addWidget(shape_btn)
            row2.addWidget(col_btn)
            row2.addSpacing(2)

        btn_clear_ov = QPushButton("Clear overlays")
        btn_clear_ov.setToolTip("Remove all drawn overlays from all thumbnails")
        btn_clear_ov.clicked.connect(self._clear_all_overlays)
        row2.addWidget(btn_clear_ov)

        sep4 = QLabel("|"); sep4.setStyleSheet(sep_style); row2.addWidget(sep4)

        # GROUP: Selection label + Try again
        self._lbl_sel = QLabel("0 selected")
        self._lbl_sel.setStyleSheet("color:#888; font-size:11px;")
        row2.addWidget(self._lbl_sel)
        self._btn_try_again = QPushButton("Try again (next hour)")
        self._btn_try_again.setToolTip("For selected images, try the next available hour.")
        self._btn_try_again.clicked.connect(self._try_again_selected)
        row2.addWidget(self._btn_try_again)

        sep5 = QLabel("|"); sep5.setStyleSheet(sep_style); row2.addWidget(sep5)

        # GROUP: Save
        btn_save = QPushButton("Save…")
        btn_save.setToolTip("Open save dialog (choose: selected / all  ×  original / annotated)")
        btn_save.clicked.connect(self._open_save_dialog)
        row2.addWidget(btn_save)

        row2.addStretch()
        root.addLayout(row2)

        # ── Tab widget ────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        root.addWidget(self._tabs, 1)

        # Tab: View by day
        self._tab_day = self._make_scroll_tab()
        self._tabs.addTab(self._tab_day[0], "View by day")

        # Tab per camera (show if there is any entry, even inactive)
        self._cam_tabs: dict[str, tuple] = {}
        for folder_name, cam_label, _ in self._cameras:
            if not self._results.get(folder_name):
                continue
            t = self._make_scroll_tab()
            self._cam_tabs[folder_name] = t
            all_inactive = all(s == "inactive"
                               for _, _, _, _, s in self._results[folder_name])
            label = f"{cam_label} ✕" if all_inactive else cam_label
            self._tabs.addTab(t[0], label)

        # Populate after show so viewport widths are known
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._populate_day_tab()
        for fn in self._cam_tabs:
            self._populate_cam_tab(fn)

    def _make_scroll_tab(self) -> tuple:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setSpacing(8)
        grid.setContentsMargins(8, 8, 8, 8)
        for c in range(self._COLS):
            grid.setColumnStretch(c, 1)
        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return (outer, grid, scroll)

    # ── Thumbnail rendering ───────────────────────────────────────────────────
    def _current_scroll(self) -> "QScrollArea | None":
        """Return the QScrollArea for the current tab."""
        idx = self._tabs.currentIndex()
        if idx == 0:
            return self._tab_day[2]
        fn = list(self._cam_tabs.keys())
        cam_idx = idx - 1
        if 0 <= cam_idx < len(fn):
            return self._cam_tabs[fn[cam_idx]][2]
        return None

    def _thumb_size(self) -> int:
        """Compute thumbnail size so _COLS thumbnails fill the current viewport width."""
        scroll = self._current_scroll()
        if scroll is not None:
            vp_w = scroll.viewport().width()
        else:
            vp_w = self.width() - 20
        avail = max(vp_w, self._MIN_THUMB * self._COLS)
        return max(self._MIN_THUMB, (avail - (self._COLS - 1) * 8 - 16) // self._COLS)

    def _on_tab_changed(self, _idx: int):
        self._refresh_all_thumbs()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_all_thumbs()

    def _load_raw(self, path: Path) -> "np.ndarray | None":
        if path in self._raw_cache:
            return self._raw_cache[path]
        try:
            import numpy as _np
            img = PilImage.open(path)
            if img.mode in ("I", "I;16"):
                arr = _np.array(img, dtype=_np.float32)
            elif img.mode in ("RGB", "RGBA"):
                arr = _np.array(img.convert("L"), dtype=_np.float32)
            else:
                arr = _np.array(img.convert("L"), dtype=_np.float32)
            img_max_val = _read_img_max_value(path)
            arr_px_max = float(arr.max())
            if img_max_val is not None and arr_px_max > 0:
                arr = img_max_val * arr / arr_px_max
            arr8 = _np.clip(arr / 4095.0 * 255.0, 0, 255).astype(_np.uint8)
            self._raw_cache[path] = arr8
            return arr8
        except Exception:
            return None

    def _apply_display_effects(self, arr: "np.ndarray", cam_name: str = "") -> "PilImage.Image":
        """Apply brightness, palette, rotation and overlay to a uint8 array. Returns PIL RGB image."""
        import numpy as _np
        auto = getattr(self, '_auto_bright', False)
        if auto:
            mn, mx = arr.min(), arr.max()
            if mx > mn:
                arr = ((arr.astype(_np.float32) - mn) / (mx - mn) * 255).astype(_np.uint8)
        else:
            bv = self._brightness
            if bv != 0:
                arr = _np.clip(arr.astype(_np.int16) + bv, 0, 255).astype(_np.uint8)
        # Palette
        lut = self._GRADIENTS.get(self._palette)
        if lut is not None:
            rgb = lut[arr]
            img = PilImage.fromarray(rgb, "RGB")
        else:
            img = PilImage.fromarray(arr, "L").convert("RGB")
        # Rotation
        rot = getattr(self, '_rotation', 0)
        if rot:
            img = img.rotate(-rot, expand=True)   # PIL rotates CCW; we want CW
        # Overlay is drawn by _ThumbView via QPainter — not in the pixmap pipeline
        return img

    def _render_thumb(self, path: Path, size: int, cam_name: str = "") -> QPixmap:
        import numpy as _np
        arr = self._load_raw(path)
        if arr is None:
            pm = QPixmap(size, size)
            pm.fill(QColor("#333"))
            return pm
        img = self._apply_display_effects(arr, cam_name)
        img = img.resize((size, size), PilImage.LANCZOS)
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    def _render_popup(self, path: Path, popup_size: int, cam_name: str = "") -> "QPixmap | None":
        import numpy as _np
        arr = self._load_raw(path)
        if arr is None:
            return None
        img = self._apply_display_effects(arr, cam_name)
        img.thumbnail((popup_size, popup_size), PilImage.LANCZOS)
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    # ── Palette / brightness callbacks ────────────────────────────────────────
    def _on_palette_changed(self, name: str):
        self._push_undo()
        self._palette = name
        self._refresh_all_thumbs()

    def _on_brightness_changed(self, val: int):
        self._push_undo()
        self._brightness = val
        self._bright_lbl.setText(str(val))
        if getattr(self, '_auto_bright', False):
            self._btn_auto_bright.blockSignals(True)
            self._btn_auto_bright.setChecked(False)
            self._btn_auto_bright.setStyleSheet("")
            self._btn_auto_bright.blockSignals(False)
            self._auto_bright = False
        self._refresh_all_thumbs()

    def _on_auto_bright_toggled(self, on: bool):
        self._auto_bright = on
        self._btn_auto_bright.setStyleSheet(
            "background:#4a9eff; color:#fff; font-weight:bold;" if on else "")
        if on:
            self._bright_slider.setValue(0)
        self._refresh_all_thumbs()

    def _on_preview_toggled(self, on: bool):
        self._preview_enabled = on
        self._btn_preview_toggle.setText("Preview ON" if on else "Preview OFF")

    def _rotate_all(self, delta_deg: int):
        self._push_undo()
        self._rotation = (self._rotation + delta_deg) % 360
        self._raw_cache.clear()
        self._refresh_all_thumbs()

    # ── Independent overlay toggles ───────────────────────────────────────────
    def _on_circle_toggled(self, on: bool):
        self._push_undo()
        self._draw_circle = on
        self._btn_draw_circle.setStyleSheet(
            "background:#4a9eff; color:#fff; font-weight:bold;" if on else "")
        self._update_all_draw_modes()

    def _on_square_toggled(self, on: bool):
        self._push_undo()
        self._draw_square = on
        self._btn_draw_square.setStyleSheet(
            "background:#4a9eff; color:#fff; font-weight:bold;" if on else "")
        self._update_all_draw_modes()

    def _on_cross_toggled(self, on: bool):
        self._push_undo()
        self._draw_cross = on
        self._btn_draw_cross.setStyleSheet(
            "background:#4a9eff; color:#fff; font-weight:bold;" if on else "")
        self._update_all_draw_modes()

    def _pick_shape_color(self, shape: str):
        from PySide6.QtWidgets import QColorDialog
        cur = {"circle": self._circle_qcolor,
                "square": self._square_qcolor,
                "cross":  self._cross_qcolor}[shape]
        col = QColorDialog.getColor(cur, self, f"{shape.title()} colour")
        if not col.isValid():
            return
        self._push_undo()
        def _cs(btn, qcol):
            btn.setStyleSheet(
                f"background:{qcol.name()}; border:1px solid #888; border-radius:2px;")
        if shape == "circle":
            self._circle_qcolor = col
            _cs(self._col_circle_btn, col)
        elif shape == "square":
            self._square_qcolor = col
            _cs(self._col_square_btn, col)
        else:
            self._cross_qcolor = col
            _cs(self._col_cross_btn, col)
        self._apply_colors_to_views()

    def _apply_colors_to_views(self):
        for tvs in self._thumb_views_all.values():
            for tv in tvs:
                tv.circle_color = self._circle_qcolor
                tv.square_color = self._square_qcolor
                tv.cross_color  = self._cross_qcolor
                tv.update()

    def _clear_all_overlays(self):
        self._push_undo()
        for tvs in self._thumb_views_all.values():
            for tv in tvs:
                tv.circle_center_norm = None; tv.circle_rx_norm = None; tv.circle_ry_norm = None
                tv.square_rect_norm = None
                tv.cross_pos_norm = None
                tv.update()

    # ── Undo ──────────────────────────────────────────────────────────────────
    def _tv_overlay_state(self) -> dict:
        """Snapshot overlay state of every _ThumbView for undo."""
        state = {}
        for key, tv in self._thumb_views.items():
            state[key] = {
                "circle_center": tv.circle_center_norm,
                "circle_rx": tv.circle_rx_norm,
                "circle_ry": tv.circle_ry_norm,
                "square_rect": tv.square_rect_norm,
                "cross_pos": tv.cross_pos_norm,
            }
        return state

    def _display_state(self) -> dict:
        return {
            "palette":       self._palette,
            "brightness":    self._brightness,
            "auto_bright":   self._auto_bright,
            "rotation":      self._rotation,
            "draw_circle":   self._draw_circle,
            "draw_square":   self._draw_square,
            "draw_cross":    self._draw_cross,
            "circle_qcolor": QColor(self._circle_qcolor),
            "square_qcolor": QColor(self._square_qcolor),
            "cross_qcolor":  QColor(self._cross_qcolor),
            "tv_overlays":   self._tv_overlay_state(),
        }

    def _push_undo(self):
        self._undo_stack.append(self._display_state())
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def _undo_last(self):
        if not self._undo_stack:
            return
        state = self._undo_stack.pop()
        self._apply_display_state(state)

    def _apply_display_state(self, state: dict):
        """Restore full display state from a snapshot dict, update all widgets."""
        self._palette     = state["palette"]
        self._brightness  = state["brightness"]
        self._auto_bright = state["auto_bright"]
        self._rotation    = state["rotation"]
        self._draw_circle = state["draw_circle"]
        self._draw_square = state["draw_square"]
        self._draw_cross  = state["draw_cross"]
        self._circle_qcolor = state.get("circle_qcolor", self._circle_qcolor)
        self._square_qcolor = state.get("square_qcolor", self._square_qcolor)
        self._cross_qcolor  = state.get("cross_qcolor",  self._cross_qcolor)

        # Sync widgets without re-triggering push_undo
        self._palette_cb.blockSignals(True)
        self._palette_cb.setCurrentText(self._palette)
        self._palette_cb.blockSignals(False)

        self._bright_slider.blockSignals(True)
        self._bright_slider.setValue(self._brightness)
        self._bright_slider.blockSignals(False)
        self._bright_lbl.setText(str(self._brightness))

        self._btn_auto_bright.blockSignals(True)
        self._btn_auto_bright.setChecked(self._auto_bright)
        self._btn_auto_bright.setStyleSheet(
            "background:#4a9eff; color:#fff; font-weight:bold;" if self._auto_bright else "")
        self._btn_auto_bright.blockSignals(False)

        for btn, flag in (
            (self._btn_draw_circle, self._draw_circle),
            (self._btn_draw_square, self._draw_square),
            (self._btn_draw_cross,  self._draw_cross),
        ):
            btn.blockSignals(True)
            btn.setChecked(flag)
            btn.setStyleSheet(
                "background:#4a9eff; color:#fff; font-weight:bold;" if flag else "")
            btn.blockSignals(False)

        # Restore per-_ThumbView overlay shapes (sync all TVs per key)
        tv_ovs = state.get("tv_overlays", {})
        for key, tvs in self._thumb_views_all.items():
            ov = tv_ovs.get(key, {})
            for tv in tvs:
                tv.circle_center_norm = ov.get("circle_center")
                tv.circle_rx_norm     = ov.get("circle_rx")
                tv.circle_ry_norm     = ov.get("circle_ry")
                tv.square_rect_norm   = ov.get("square_rect")
                tv.cross_pos_norm     = ov.get("cross_pos")
                tv.update()

        self._apply_colors_to_views()
        self._update_all_draw_modes()
        self._raw_cache.clear()
        self._refresh_all_thumbs()

    # ── Reset ─────────────────────────────────────────────────────────────────
    def _reset_display(self):
        sel_count = len(self._selected)
        if sel_count > 0:
            msg = QMessageBox(self)
            msg.setWindowTitle("Reset display settings")
            msg.setText(
                "Reset all display settings?\n\n"
                "This will reset: palette, brightness, rotation, overlays.\n"
                "Original files are NOT modified.")
            btn_sel = msg.addButton(f"Reset selected ({sel_count})",
                                    QMessageBox.ButtonRole.AcceptRole)
            btn_all = msg.addButton("Reset all", QMessageBox.ButtonRole.DestructiveRole)
            msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_sel:
                self._push_undo()
                for key in self._selected:
                    tv = self._thumb_views.get(key)
                    if tv:
                        tv.circle_center_norm = None; tv.circle_rx_norm = None
                        tv.circle_ry_norm = None; tv.square_rect_norm = None
                        tv.cross_pos_norm = None; tv.update()
                return
            elif clicked != btn_all:
                return
        else:
            reply = QMessageBox.question(
                self, "Reset display settings",
                "Reset all display settings to defaults?\n\n"
                "This will reset: palette, brightness, rotation, overlays.\n"
                "Original files are NOT modified.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._push_undo()
        default = {
            "palette":       list(self._GRADIENTS.keys())[0],
            "brightness":    0,
            "auto_bright":   False,
            "rotation":      0,
            "draw_circle":   False,
            "draw_square":   False,
            "draw_cross":    False,
            "circle_qcolor": QColor(255, 255,   0, 230),
            "square_qcolor": QColor(  0, 200, 255, 230),
            "cross_qcolor":  QColor(  0, 255,   0, 220),
            "tv_overlays":   {},
        }
        self._apply_display_state(default)

    # ── Draw-mode helpers ─────────────────────────────────────────────────────
    def _active_draw_mode(self) -> str:
        """Return the currently active draw mode string for _ThumbView, or ''."""
        if self._draw_circle: return "circle"
        if self._draw_square: return "square"
        if self._draw_cross:  return "cross"
        return ""

    def _update_all_draw_modes(self):
        """Push the current draw-mode to every _ThumbView."""
        mode = self._active_draw_mode()
        for tvs in self._thumb_views_all.values():
            for tv in tvs:
                tv.set_draw_mode(mode)
                tv.show_circle = self._draw_circle
                tv.show_square = self._draw_square
                tv.show_cross  = self._draw_cross

    def _refresh_all_thumbs(self):
        """Re-render pixmaps (palette/brightness/rotation applied) and sync draw modes."""
        sz = self._thumb_size()
        rendered: dict[tuple, QPixmap] = {}
        for key, tvs in self._thumb_views_all.items():
            path = self._thumb_paths.get(key)
            if path is None:
                continue
            cn = self._thumb_camnames.get(key, "")
            if key not in rendered:
                rendered[key] = self._render_thumb(path, sz, cn)
            pm = rendered[key]
            for tv in tvs:
                tv.setFixedSize(sz, sz)
                tv.set_pixmap(pm)
        self._update_all_draw_modes()

    # ── Thumb cells ───────────────────────────────────────────────────────────
    def _make_thumb_cell(self, cam_name: str, date, hour: "int | None",
                         path: "Path | None", extra_label: str = "",
                         pv_text: str = "",
                         meta: "dict | None" = None,
                         status: str = "found") -> QWidget:
        sz  = self._thumb_size()
        key = (cam_name, date)

        cell = QWidget()
        cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        vl = QVBoxLayout(cell)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(3)

        if path is not None:
            tv = _ThumbView(self)
            tv.setFixedSize(sz, sz)
            tv.circle_color = self._circle_qcolor
            tv.square_color = self._square_qcolor
            tv.cross_color  = self._cross_qcolor
            tv.show_circle = self._draw_circle
            tv.show_square = self._draw_square
            tv.show_cross  = self._draw_cross
            tv.set_draw_mode(self._active_draw_mode())
            tv.set_selected(key in self._selected)   # restore selection state if key already known
            pm = self._render_thumb(path, sz, cam_name)
            tv.set_pixmap(pm)
            vl.addWidget(tv, 0, Qt.AlignmentFlag.AlignHCenter)

            # Track this view
            self._thumb_views[key]    = tv   # last registered wins (day+cam tabs share state)
            self._thumb_paths[key]    = path
            self._thumb_camnames[key] = cam_name
            # all TVs per key — needed so _refresh_all_thumbs updates day-tab and cam-tab
            if key not in self._thumb_views_all:
                self._thumb_views_all[key] = []
            self._thumb_views_all[key].append(tv)
            # legacy _thumb_widgets kept for popup_anchor compatibility
            if key not in self._thumb_widgets:
                self._thumb_widgets[key] = []
            self._thumb_widgets[key].append(tv)
            if key not in self._popup_anchor:
                self._popup_anchor[key] = tv

            # Signals
            def _on_hov_in(_path=path, _tv=tv, _key=key, _cn=cam_name):
                self._popup_anchor[_key] = _tv
                self._show_popup(_path, _tv, _cn)

            def _on_hov_out():
                self._hide_popup()

            def _on_click(_key=key):
                self._hide_popup()
                if _key in self._selected:
                    self._selected.discard(_key)
                else:
                    self._selected.add(_key)
                sel = _key in self._selected
                for w in self._thumb_widgets.get(_key, []):
                    if hasattr(w, "set_selected"):
                        w.set_selected(sel)
                self._lbl_sel.setText(f"{len(self._selected)} selected")

            def _on_dbl(_cam=cam_name, _date=date):
                self.open_in_finder_requested.emit(_date, _cam)

            def _on_right_click(_cam=cam_name, _date=date, _tv=tv):
                from PySide6.QtWidgets import QMenu
                menu = QMenu(self)
                act_try  = menu.addAction("↻ Try again (next hour)")
                act_pick = menu.addAction("📂 Pick image from folder…")
                chosen = menu.exec(QCursor.pos())
                if chosen == act_try:
                    self._try_again_single(_cam, _date)
                elif chosen == act_pick:
                    self._pick_image_single(_cam, _date)

            tv.hovered_in.connect(_on_hov_in)
            tv.hovered_out.connect(_on_hov_out)
            tv.clicked.connect(_on_click)
            tv.dbl_clicked.connect(_on_dbl)
            tv.right_clicked.connect(_on_right_click)
        else:
            # No image — placeholder same size as real thumbs
            placeholder = QLabel()
            placeholder.setFixedSize(sz, sz)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            reason = "Camera inactive" if status == "inactive" else "Not found"
            placeholder.setText(f"{reason}\n(right-click)")
            placeholder.setStyleSheet(
                "border: 1px solid #444; border-radius: 3px; "
                "background: #111; color: #fff; font-size: 13px; font-weight: bold;")
            placeholder.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

            def _ph_ctx(_cam=cam_name, _date=date):
                from PySide6.QtWidgets import QMenu
                menu = QMenu(self)
                act_try  = menu.addAction("↻ Try again (next hour)")
                act_pick = menu.addAction("📂 Pick image from folder…")
                chosen_act = menu.exec(QCursor.pos())
                if chosen_act == act_try:
                    self._try_again_single(_cam, _date)
                elif chosen_act == act_pick:
                    self._pick_image_single(_cam, _date)

            placeholder.customContextMenuRequested.connect(lambda _pos, fn=_ph_ctx: fn())
            vl.addWidget(placeholder, 0, Qt.AlignmentFlag.AlignHCenter)

        # ── Label: camera name + date + timestamp ─────────────────────────────
        date_str = date.strftime("%d.%m.%Y")
        lines: list[str] = []
        if extra_label:
            lines.append(extra_label)
        if path is not None:
            ns = extract_ns_from_stem(path.stem)
            if ns is not None:
                dt_utc = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                dt_disp = dt_utc.astimezone(PRAGUE) if (not self._use_lab and PRAGUE) else dt_utc
                ts_str = dt_disp.strftime("%H:%M:%S.") + f"{dt_disp.microsecond // 1000:03d}"
                lines.append(f"{date_str}  {ts_str}")
            else:
                lines.append(f"{date_str}  {hour:02d}:00" if hour is not None else date_str)
        else:
            lines.append(date_str)
        if pv_text:
            lines.append(pv_text)

        lbl_txt = QLabel("\n".join(lines))
        lbl_txt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_txt.setWordWrap(True)
        lbl_txt.setStyleSheet(
            "font-size:11px; font-weight:600; color:#111; background:#f0f0f0;"
            "padding:3px 6px; border-radius:3px;")
        vl.addWidget(lbl_txt)
        return cell

    def _search_by_time(self, cam_name: str, date, use_lab: bool):
        """Ask user for a time string and find the nearest image in that folder."""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Search by time",
            f"Enter time for  {cam_name}  {date.strftime('%d.%m.%Y')}\n"
            "Format: HH:MM:SS  (24-hour, Prague time unless lab-time is on)",
            text="")
        if not ok or not text.strip():
            return

        # Parse HH:MM:SS
        try:
            parts = [int(x) for x in text.strip().split(":")]
            if len(parts) == 2:
                h, m, s = parts[0], parts[1], 0
            elif len(parts) == 3:
                h, m, s = parts
            else:
                raise ValueError("bad format")
            if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
                raise ValueError("out of range")
        except (ValueError, TypeError):
            QMessageBox.warning(self, "Search by time",
                                "Invalid time format. Use HH:MM:SS (e.g. 10:58:56).")
            return

        # Build UTC nanosecond target
        year, month, day_n = date.year, date.month, date.day
        try:
            if not use_lab and PRAGUE is not None:
                dt_local = datetime(year, month, day_n, h, m, s, tzinfo=PRAGUE)
                dt_utc   = dt_local.astimezone(timezone.utc)
            else:
                dt_utc = datetime(year, month, day_n, h, m, s, tzinfo=timezone.utc)
        except ValueError as e:
            QMessageBox.warning(self, "Search by time", str(e))
            return

        target_ns = int(dt_utc.timestamp() * 1e9)

        # Determine folder hour
        if not use_lab and PRAGUE is not None:
            folder_h = h - int(datetime(year, month, day_n, h, tzinfo=PRAGUE)
                                .utcoffset().total_seconds() / 3600)
        else:
            folder_h = h

        parent_finder = self.parent()
        if parent_finder is None:
            return

        dt_eff   = datetime(year, month, day_n, folder_h)
        cam_folder = parent_finder._build_target_path(dt_eff) / cam_name

        if not cam_folder.exists():
            QMessageBox.warning(self, "Search by time",
                                f"Folder not found on archiver:\n{cam_folder}")
            return

        chosen = parent_finder._nearest_file_for_ns(cam_folder, target_ns)
        if chosen is None:
            QMessageBox.information(self, "Search by time",
                                    f"No image files found in:\n{cam_folder}")
            return

        key = (cam_name, date)
        self._update_thumb_result(cam_name, date, key, h, chosen)
        QMessageBox.information(self, "Search by time",
                                f"Found:\n{chosen.name}")

    # ── Hover popup ───────────────────────────────────────────────────────────
    def _show_popup(self, path: Path, anchor: "QLabel", cam_name: str = ""):
        if not getattr(self, '_preview_enabled', True):
            return
        self._hide_popup()
        sz = self._thumb_size()
        # Popup = 8× current thumbnail; clamp so it never exceeds 80% of screen
        screen = QApplication.primaryScreen().geometry()
        max_popup = min(screen.width(), screen.height()) * 4 // 5
        popup_size = min(int(sz * 8), max_popup)
        popup_size = max(popup_size, sz * 6)   # at least 6×
        pm = self._render_popup(path, popup_size, cam_name)
        if pm is None:
            return
        win = QWidget(None, Qt.WindowType.ToolTip |
                      Qt.WindowType.FramelessWindowHint |
                      Qt.WindowType.WindowStaysOnTopHint)
        win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        lbl = QLabel(win)
        lbl.setPixmap(pm)
        lbl.resize(pm.size())
        win.resize(pm.size())
        # Anchor to the image label: right edge by default, left edge if near screen right
        anchor_tl = anchor.mapToGlobal(anchor.rect().topLeft())
        anchor_tr = anchor.mapToGlobal(anchor.rect().topRight())
        # Try right side first
        x = anchor_tr.x() + 4
        y = anchor_tl.y()
        if x + pm.width() > screen.right():
            # Place on left side
            x = anchor_tl.x() - pm.width() - 4
        # Clamp vertically
        if y + pm.height() > screen.bottom():
            y = screen.bottom() - pm.height() - 4
        y = max(screen.top(), y)
        win.move(x, y)
        win.show()
        self._popup_win = win

    def _hide_popup(self):
        if self._popup_win is not None:
            try:
                self._popup_win.hide()
                self._popup_win.deleteLater()
            except Exception:
                pass
            self._popup_win = None

    def hideEvent(self, event):
        self._hide_popup()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._hide_popup()
        super().closeEvent(event)

    # ── PV annotation ─────────────────────────────────────────────────────────
    def _pv_text_for_path(self, path: Path) -> str:
        """Return a short PV annotation string for a given image path, or ''."""
        finder = self.parent()
        if finder is None:
            return ""
        # Only annotate if "attach PVs" is checked and columns are selected
        if not getattr(finder, "_cb_annotate", None):
            return ""
        if not finder._cb_annotate.isChecked():
            return ""
        cols = getattr(finder, "_energy_selected_cols", [])
        if not cols:
            return ""
        ns = extract_ns_from_stem(path.stem)
        if ns is None:
            return ""
        try:
            dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
            if PRAGUE:
                dt = dt.astimezone(PRAGUE).replace(tzinfo=None)
            else:
                dt = dt.replace(tzinfo=None)
            rows = finder._get_energy_rows_for_dt(dt)
            match, _, _ = _find_energy_match(rows, ns)
            if match is None:
                return ""
            parts = []
            for col in cols:
                raw = match.values.get(col, "")
                parts.append(f"{ENERGY_COLUMNS_DISPLAY.get(col, col)}: {_format_energy_value(col, raw)}")
            return "  ".join(parts)
        except Exception:
            return ""

    # ── Populate tabs ─────────────────────────────────────────────────────────
    def _populate_day_tab(self):
        grid = self._tab_day[1]
        all_days = sorted({date
                           for items in self._results.values()
                           for date, _, _, _, _ in items})
        row = 0
        for day in all_days:
            day_lbl = QLabel(day.strftime("  %A  %d.%m.%Y"))
            day_lbl.setStyleSheet(
                "font-weight:bold; font-size:12px; color:#ccc;"
                "background:#2a2a2a; padding:2px 4px; border-radius:3px;")
            grid.addWidget(day_lbl, row, 0, 1, self._COLS)
            row += 1
            col = 0
            for folder_name, cam_label, _ in self._cameras:
                for date, hour, path, meta, status in self._results.get(folder_name, []):
                    if date != day:
                        continue
                    pv = self._pv_text_for_path(path) if path else ""
                    cell = self._make_thumb_cell(folder_name, date, hour, path,
                                                 cam_label, pv, meta, status)
                    grid.addWidget(cell, row, col)
                    col += 1
                    if col >= self._COLS:
                        col = 0; row += 1
            if col > 0:
                row += 1

    def _populate_cam_tab(self, cam_name: str):
        _, grid, _ = self._cam_tabs[cam_name]
        col = 0; row = 0
        for date, hour, path, meta, status in self._results.get(cam_name, []):
            pv = self._pv_text_for_path(path) if path else ""
            cell = self._make_thumb_cell(cam_name, date, hour, path, "", pv, meta, status)
            grid.addWidget(cell, row, col)
            col += 1
            if col >= self._COLS:
                col = 0; row += 1

    # ── Save ──────────────────────────────────────────────────────────────────
    def _open_save_dialog(self):
        """Show a small 2×2 save dialog: rows = Original / Annotated, cols = Selected / All."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Save images")
        dlg.setFixedSize(340, 160)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        n_sel = len(self._selected)

        grid = QGridLayout()
        grid.setSpacing(8)

        # Header labels
        hdr_style = "font-weight:bold; font-size:11px; color:#555;"
        lbl_sel_hdr = QLabel(f"Selected ({n_sel})")
        lbl_sel_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_sel_hdr.setStyleSheet(hdr_style)
        lbl_all_hdr = QLabel("All")
        lbl_all_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_all_hdr.setStyleSheet(hdr_style)
        lbl_orig_hdr = QLabel("Original")
        lbl_orig_hdr.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_orig_hdr.setStyleSheet(hdr_style)
        lbl_ann_hdr = QLabel("Annotated")
        lbl_ann_hdr.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_ann_hdr.setStyleSheet(hdr_style)

        grid.addWidget(lbl_sel_hdr,  0, 1)
        grid.addWidget(lbl_all_hdr,  0, 2)
        grid.addWidget(lbl_orig_hdr, 1, 0)
        grid.addWidget(lbl_ann_hdr,  2, 0)

        btn_style = "QPushButton { padding: 6px 10px; }"

        def make_btn(label: str, fn):
            b = QPushButton(label)
            b.setStyleSheet(btn_style)
            b.clicked.connect(lambda: (dlg.accept(), fn()))
            return b

        grid.addWidget(make_btn("Save", lambda: self._save_selected(annotate=False)), 1, 1)
        grid.addWidget(make_btn("Save", lambda: self._save_all(annotate=False)),      1, 2)
        grid.addWidget(make_btn("Save", lambda: self._save_selected(annotate=True)),  2, 1)
        grid.addWidget(make_btn("Save", lambda: self._save_all(annotate=True)),       2, 2)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        outer.addLayout(grid)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        outer.addWidget(btn_cancel, 0, Qt.AlignmentFlag.AlignRight)

        # Disable selected-column buttons when nothing is selected
        if n_sel == 0:
            for row in (1, 2):
                item = grid.itemAtPosition(row, 1)
                if item and item.widget():
                    item.widget().setEnabled(False)
                    item.widget().setToolTip("No images selected")

        dlg.exec()

    def _bake_overlay_to_pil(self, img: "PilImage.Image",
                              tv: "_ThumbView") -> "PilImage.Image":
        """Draw the overlay shapes stored in tv onto a PIL image using normalised coords."""
        from PIL import ImageDraw as _ID
        w, h = img.size
        draw = _ID.Draw(img)

        def qcol_to_rgb(qc): return (qc.red(), qc.green(), qc.blue())

        if tv.show_circle and tv.circle_center_norm and tv.circle_rx_norm and tv.circle_ry_norm:
            cx = int(tv.circle_center_norm.x() * w)
            cy = int(tv.circle_center_norm.y() * h)
            rx = int(tv.circle_rx_norm * w)
            ry = int(tv.circle_ry_norm * h)
            col = qcol_to_rgb(tv.circle_color)
            draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                         outline=col, width=tv.circle_thick)

        if tv.show_square and tv.square_rect_norm:
            ln, tn, rn, bn = tv.square_rect_norm
            draw.rectangle([int(ln * w), int(tn * h), int(rn * w), int(bn * h)],
                           outline=qcol_to_rgb(tv.square_color), width=tv.square_thick)

        if tv.show_cross:
            if tv.cross_pos_norm:
                cx = int(tv.cross_pos_norm.x() * w)
                cy = int(tv.cross_pos_norm.y() * h)
            else:
                cx, cy = w // 2, h // 2
            half = tv.cross_size
            col = qcol_to_rgb(tv.cross_color)
            draw.line([cx - half, cy, cx + half, cy], fill=col, width=tv.cross_thickness)
            draw.line([cx, cy - half, cx, cy + half], fill=col, width=tv.cross_thickness)

        return img

    def _save_selected(self, annotate: bool = False):
        if not self._selected:
            QMessageBox.information(self, "Save", "No images selected."); return
        items = self._items_for_keys(self._selected)
        self._save_items(items, annotate=annotate)

    def _save_all(self, annotate: bool = False):
        items = []
        for cam_name, entries in self._results.items():
            for date, hour, path, meta, status in entries:
                if path is not None:
                    items.append((cam_name, path, meta))
        self._save_items(items, annotate=annotate)

    def _items_for_keys(self, keys: set) -> list:
        items = []
        for cam_name, date in keys:
            for d, _, path, meta, _ in self._results.get(cam_name, []):
                if d == date and path is not None:
                    items.append((cam_name, path, meta)); break
        return items

    def _paths_for_keys(self, keys: set) -> list:
        return [path for _, path, _ in self._items_for_keys(keys)]

    def _save_items(self, items: list, annotate: bool = False):
        """Save list of (cam_name, path, meta) items.
        annotate=False: copy original file as-is (no suffix change).
        annotate=True:  apply current display effects + annotation bar; add _annotated suffix."""
        if not items:
            QMessageBox.information(self, "Save", "Nothing to save."); return
        dst = QFileDialog.getExistingDirectory(self, "Save images to folder")
        if not dst:
            return
        dst_path = Path(dst)
        finder = self.parent()
        saved = 0; errors = []
        for cam_name, src, meta in items:
            try:
                if not annotate:
                    # Save original file as-is, same filename
                    shutil.copy2(str(src), str(dst_path / src.name))
                else:
                    # ── Build annotation text ─────────────────────────────────
                    ns = extract_ns_from_stem(src.stem)
                    ts_str = ""
                    if ns is not None:
                        dt_utc = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                        if PRAGUE:
                            ts_str = dt_utc.astimezone(PRAGUE).strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            ts_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
                    ann_parts = [cam_name, ts_str, f"Palette: {self._palette}"]
                    if finder is not None and ns is not None:
                        try:
                            cols = getattr(finder, "_energy_selected_cols", [])
                            if cols:
                                dt_csv = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                                if PRAGUE:
                                    dt_csv = dt_csv.astimezone(PRAGUE).replace(tzinfo=None)
                                else:
                                    dt_csv = dt_csv.replace(tzinfo=None)
                                rows = finder._get_energy_rows_for_dt(dt_csv)
                                match, _, _ = _find_energy_match(rows, ns)
                                if match is not None:
                                    for col in cols:
                                        raw = match.values.get(col, "")
                                        ann_parts.append(
                                            f"{ENERGY_COLUMNS_DISPLAY.get(col, col)}: "
                                            f"{_format_energy_value(col, raw)}")
                        except Exception:
                            pass
                    ann_text = "   |   ".join(p for p in ann_parts if p)

                    # ── Apply current display effects + overlay ───────────────
                    arr = self._load_raw(src)
                    if arr is None:
                        errors.append(f"{src.name}: could not read image"); continue
                    rendered_img = self._apply_display_effects(arr, cam_name)
                    # Bake overlay into the saved image using the _ThumbView's stored coords
                    key = next(
                        ((cn, d) for cn, entries in self._results.items()
                         for d, _, p, _, _ in entries
                         if p == src and cn == cam_name),
                        None)
                    tv = self._thumb_views.get(key) if key else None
                    if tv is not None:
                        rendered_img = self._bake_overlay_to_pil(rendered_img, tv)

                    # ── Destination filename: original stem + _annotated ───────
                    dst_file = dst_path / (src.stem + "_annotated" + src.suffix)
                    _write_annotated_from_pil(rendered_img, dst_file, ann_text)

                saved += 1
            except Exception as e:
                errors.append(f"{src.name}: {e}")
        msg = f"Saved {saved} image(s) to:\n{dst}"
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors[:5])
        QMessageBox.information(self, "Saved", msg)

    def _save_paths(self, paths: list):
        """Legacy: save by path list without annotation."""
        items = [("", p, {}) for p in paths]
        self._save_items(items, annotate=False)

    # ── Try again ─────────────────────────────────────────────────────────────
    def _try_again_selected(self):
        if not self._selected:
            QMessageBox.information(self, "Try again", "Select thumbnails first."); return
        parent_finder = self.parent()
        if parent_finder is None:
            return

        keys = list(self._selected)
        total = len(keys)

        # Show progress dialog
        prog = QDialog(self)
        prog.setWindowTitle("Trying next hour…")
        prog.setMinimumWidth(320)
        prog.setWindowFlags(prog.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        pv = QVBoxLayout(prog)
        plbl = QLabel("Searching…")
        plbl.setWordWrap(True)
        pbar = QProgressBar()
        pbar.setRange(0, total)
        pbar.setValue(0)
        pv.addWidget(plbl)
        pv.addWidget(pbar)
        prog.show()
        QApplication.processEvents()

        updated = []
        for idx, (cam_name, date) in enumerate(keys):
            plbl.setText(f"{cam_name}  {date.strftime('%d.%m.%Y')}")
            pbar.setValue(idx)
            QApplication.processEvents()

            current_hour = None
            for d, h, _p, _m, _s in self._results.get(cam_name, []):
                if d == date:
                    current_hour = h; break

            key = (cam_name, date)
            last_tried = self._try_hour.get(key, current_hour if current_hour is not None else -1)
            next_hour  = last_tried + 1

            found = False
            for real_h in range(next_hour, 24):
                if PRAGUE is not None:
                    dt_p = datetime(date.year, date.month, date.day, real_h, tzinfo=PRAGUE)
                    folder_h = real_h - int(dt_p.utcoffset().total_seconds() / 3600)
                else:
                    folder_h = real_h
                dt_eff = datetime(date.year, date.month, date.day, folder_h)
                hour_path = parent_finder._build_target_path(dt_eff)
                cam_folder = hour_path / cam_name
                if not cam_folder.exists():
                    continue
                chosen = parent_finder.select_images_from_folder(cam_folder, 1)
                if chosen:
                    self._try_hour[key] = real_h
                    self._update_thumb_result(cam_name, date, key, real_h, chosen[0])
                    updated.append(f"{cam_name} {date.strftime('%d.%m')}: hour {real_h:02d}:00")
                    found = True
                    break
            if not found:
                updated.append(f"{cam_name} {date.strftime('%d.%m')}: no more hours")

        prog.accept()
        if updated:
            QMessageBox.information(self, "Try again", "\n".join(updated))

    def _try_again_single(self, cam_name: str, date):
        """Try the next available hour for a single (cam_name, date) cell."""
        parent_finder = self.parent()
        if parent_finder is None:
            return
        key = (cam_name, date)
        current_hour = None
        for d, h, _p, _m, _s in self._results.get(cam_name, []):
            if d == date:
                current_hour = h; break
        last_tried = self._try_hour.get(key, current_hour if current_hour is not None else -1)
        next_hour  = last_tried + 1

        for real_h in range(next_hour, 24):
            if PRAGUE is not None:
                dt_p = datetime(date.year, date.month, date.day, real_h, tzinfo=PRAGUE)
                folder_h = real_h - int(dt_p.utcoffset().total_seconds() / 3600)
            else:
                folder_h = real_h
            dt_eff     = datetime(date.year, date.month, date.day, folder_h)
            hour_path  = parent_finder._build_target_path(dt_eff)
            cam_folder = hour_path / cam_name
            if not cam_folder.exists():
                continue
            chosen = parent_finder.select_images_from_folder(cam_folder, 1)
            if chosen:
                self._try_hour[key] = real_h
                self._update_thumb_result(cam_name, date, key, real_h, chosen[0])
                QMessageBox.information(
                    self, "Try again",
                    f"{cam_name}  {date.strftime('%d.%m.%Y')}: found hour {real_h:02d}:00\n{chosen[0].name}")
                return
        QMessageBox.information(
            self, "Try again",
            f"{cam_name}  {date.strftime('%d.%m.%Y')}: no more hours available.")

    def _pick_image_single(self, cam_name: str, date):
        """Let user browse and pick any image file for a single cell."""
        parent_finder = self.parent()
        if parent_finder is None:
            return
        key = (cam_name, date)

        # Suggest the folder currently associated with this cell (any hour)
        current_path = self._thumb_paths.get(key)
        start_dir = str(current_path.parent) if current_path else ""
        if not start_dir:
            # Fall back to first existing hour folder
            for real_h in range(0, 24):
                if PRAGUE is not None:
                    dt_p = datetime(date.year, date.month, date.day, real_h, tzinfo=PRAGUE)
                    folder_h = real_h - int(dt_p.utcoffset().total_seconds() / 3600)
                else:
                    folder_h = real_h
                dt_eff     = datetime(date.year, date.month, date.day, folder_h)
                cam_folder = parent_finder._build_target_path(dt_eff) / cam_name
                if cam_folder.exists():
                    start_dir = str(cam_folder); break

        fname, _ = QFileDialog.getOpenFileName(
            self,
            f"Pick image — {cam_name}  {date.strftime('%d.%m.%Y')}",
            start_dir,
            "Images (*.png *.tif *.tiff *.jpg *.jpeg *.bmp)")
        if not fname:
            return
        chosen = Path(fname)
        if not chosen.exists():
            return

        # Use hour 0 as placeholder (real hour is encoded in filename anyway)
        real_h = 0
        entries = self._results.get(cam_name, [])
        for d, h, _p, _m, _s in entries:
            if d == date:
                real_h = h; break
        self._update_thumb_result(cam_name, date, key, real_h, chosen)

    def _update_thumb_result(self, cam_name: str, date, key: tuple,
                             real_h: int, chosen: Path):
        """Update results dict, raw cache, pixmap and all TVs for a given key."""
        entries = self._results.get(cam_name, [])
        no_meta: dict = {"ptm1": None, "sbw4": None}
        for i, (d, _, _p, _m, _s2) in enumerate(entries):
            if d == date:
                entries[i] = (date, real_h, chosen, _m if _m else no_meta, "found"); break
        else:
            entries.append((date, real_h, chosen, no_meta, "found"))
            self._results.setdefault(cam_name, [])

        old_path = self._thumb_paths.get(key)
        if old_path and old_path in self._raw_cache:
            del self._raw_cache[old_path]
        self._thumb_paths[key] = chosen

        sz  = self._thumb_size()
        new_pm = self._render_thumb(chosen, sz, cam_name)
        for tv in self._thumb_views_all.get(key, []):
            tv.setFixedSize(sz, sz)
            tv.set_pixmap(new_pm)


# ── STANDALONE ENTRY POINT ────────────────────────────────────────────────────
def main():
    """Run Image Finder as a standalone window (without Image Slider)."""
    app = QApplication.instance() or QApplication(sys.argv)
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