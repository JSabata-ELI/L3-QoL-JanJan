"""
sf_t.py — Shot Finder

Hledá snímky v CPVA archivu podle hodnoty procesní proměnné (PV).
Data jsou načítána přímo z CPVA archiveru přes HTTP API (bez CSV souborů).
Pro každý vybraný den najde nejbližší záznam k zadané cílové hodnotě
a zobrazí odpovídající složku v Image Slideru.

Run standalone:   python sf.py
Embed in tabs:    via importlib in main.py
"""

import bisect
import csv
import json
import os
import re as _re
import sys as _sys
import shutil
import subprocess
import tempfile
import atexit
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from PySide6.QtGui import QIcon

try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    PRAGUE = None

from PySide6.QtCore import Qt, QDate, QObject, Signal, QTimer, QEvent
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QLineEdit,
    QScrollArea, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCalendarWidget,
    QMessageBox, QMainWindow, QStyledItemDelegate, QSizePolicy,
    QProgressBar, QPlainTextEdit, QRadioButton, QButtonGroup,
    QSpinBox, QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QSlider,
    QFileDialog, QMenu, QInputDialog, QSplitter,
)

# ── GRADIENTS (kopie z is_t.py) ───────────────────────────────────────────────
import numpy as _np_grad

def _make_lut_sf(stops):
    lut = _np_grad.zeros((256, 3), dtype=_np_grad.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]; t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                lut[i] = tuple(int(c0[k] + f * (c1[k] - c0[k])) for k in range(3))
                break
    return lut

def _make_binary_lut_sf():
    lut = _np_grad.zeros((256, 3), dtype=_np_grad.uint8)
    lut[128:] = 255
    return lut

def _make_stepped_lut_sf(stops):
    lut = _np_grad.zeros((256, 3), dtype=_np_grad.uint8)
    for i in range(256):
        t = i / 255.0
        color = stops[-1][1]
        for j in range(len(stops) - 1):
            if t < stops[j + 1][0]:
                color = stops[j][1]
                break
        lut[i] = color
    return lut

# NI Vision "Binary", measured off the real viewer — same table and rule as in is_t.py,
# where how it was measured is written down. 15 colours, black below the first band, one
# colour per 1024 stored 16-bit units. No white and no grey anywhere in the cycle.
_NI_BINARY_CYCLE_SF = [
    (255,0,0), (0,255,0), (0,0,255), (255,255,0), (255,0,255), (0,255,255),
    (255,127,0), (255,0,127), (127,255,0), (127,0,255), (0,127,255), (0,255,127),
    (255,127,127), (127,255,127), (127,127,255),
]

def _make_ni_binary_lut_sf():
    """The NI Binary rule as a 256-entry LUT over the ABSOLUTE 8-bit scale.

    This tab renders from 8-bit images, so unlike the Image Slider it cannot take the
    exact 16-bit path: one code is 257 stored units against a 1024-unit band."""
    table = _np_grad.array([(0, 0, 0)] + _NI_BINARY_CYCLE_SF, dtype=_np_grad.uint8)
    band = (_np_grad.arange(256, dtype=_np_grad.int64) * 257) // 1024
    return table[_np_grad.where(band <= 0, 0, (band - 1) % 15 + 1)]

# "False Colors" (same definition as in is_t.py): dark blue → violet → purple →
# magenta → pink → white, with the stops crowded at the bottom so faint detail gets
# most of the colour range.
_FALSE_COLORS_STOPS_SF = [
    (0.00, (0,0,0)), (0.03, (25,0,70)), (0.07, (45,0,120)), (0.12, (70,0,160)),
    (0.20, (100,0,180)), (0.30, (130,5,185)), (0.42, (160,20,180)),
    (0.55, (190,40,175)), (0.68, (215,70,170)), (0.80, (235,105,170)),
    (0.90, (247,150,185)), (0.96, (252,200,215)), (1.00, (255,255,255)),
]

# "Rainbow" (same definition as in is_t.py): the NI Vision palette — blue to red with
# a prominent green middle, 0 black and 255 white.
_RAINBOW_STOPS_SF = [
    (0.00, (0,0,0)), (0.04, (0,0,200)), (0.14, (0,40,255)), (0.26, (0,150,255)),
    (0.36, (0,230,180)), (0.46, (0,255,80)), (0.56, (90,255,0)), (0.66, (190,255,0)),
    (0.76, (255,220,0)), (0.86, (255,120,0)), (0.94, (255,0,0)), (1.00, (255,255,255)),
]

SF_GRADIENTS: dict = {
    "Grayscale":       None,
    "Gradient":        _make_lut_sf([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
    "Binary":          _make_ni_binary_lut_sf(),
    "False Colors":    _make_lut_sf(_FALSE_COLORS_STOPS_SF),
    "Rainbow":         _make_lut_sf(_RAINBOW_STOPS_SF),
    # Red / yellow lowered, pale yellow added, white kept at the top; see is_t.py.
    "Hot":             _make_lut_sf([(0,(0,0,0)),(0.27,(255,0,0)),(0.53,(255,255,0)),(0.78,(255,255,190)),(1,(255,255,255))]),
    "Black and White": _make_binary_lut_sf(),
    "Viridis":         _make_lut_sf([(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))]),
    "Plasma":          _make_lut_sf([(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))]),
    "Inferno":         _make_lut_sf([(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))]),
    "Jet":             _make_lut_sf([(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))]),
    "Turbo":           _make_lut_sf([(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))]),
}

# Palettes mapped onto the frame's own p0.5..p99.5 window — see is_t.ADAPTIVE_PALETTES.
ADAPTIVE_PALETTES_SF = frozenset({"False Colors"})
# Cyclic palettes are ABSOLUTE: NI's Binary bands are fixed and do not follow the frame,
# so no stretch may run first (see is_t.CYCLIC_PALETTES).
CYCLIC_PALETTES_SF = frozenset({"Binary"})


def _lut_pixels_sf(lut, arr, name: str):
    """RGB pixels for `arr` under `lut`.

    The adaptive palettes are spread over p0.5..p99.5; everything else, cyclic palettes
    included, is the raw absolute scale."""
    if name not in ADAPTIVE_PALETTES_SF:
        return lut[arr]
    s = arr if arr.size <= 250_000 else _np_grad.ravel(arr)[::(arr.size // 250_000) | 1]
    lo = float(_np_grad.percentile(s, 0.5))
    hi = float(_np_grad.percentile(s, 99.5))
    if hi <= lo:
        lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        return lut[arr]
    scaled = _np_grad.clip((arr.astype(_np_grad.float32) - lo) * (255.0 / (hi - lo)),
                           0, 255).astype(_np_grad.uint8)
    return lut[scaled]

_CHECKBOX_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
"""

_PV_NAME_FONT_PX = 10          # keep in sync with _CHECKBOX_STYLE_SM's font-size
_CHECKBOX_STYLE_SM = _CHECKBOX_STYLE + f"QCheckBox {{ font-size: {_PV_NAME_FONT_PX}px; }}"

# ── CONFIG ────────────────────────────────────────────────────────────────────
# One archive, one path. A Lab / Office switch used to sit on the panel, but both of
# its entries named the SAME share (only the slash style differed), the images are
# reached the same way from the lab and from the office, and the CSV fallback below is
# dormant — so the switch could only ever be set wrong, never usefully.
IMAGES_ROOT = Path(r"//users-L3.tier0.lcs.local/cpva-image-2026")


def _images_root_for_year(images_root: Path, year: int) -> Path:
    """Return the images root with its share pointing at the given year.

    Each year lives in its own share (cpva-image-<year>). The Lab/Office root
    only fixes the host + slash style, so swap just the year in the share name
    (Path.parent cannot be used: on a UNC path the share is part of the anchor).
    """
    swapped, n = _re.subn(r"cpva-image-\d{4}", f"cpva-image-{year}", str(images_root), count=1)
    return Path(swapped) if n else images_root / f"cpva-image-{year}"

# Salvation is NOT running (as of 2026-08-19), so no new daily CSV is written and
# this fallback yields nothing for recent days — energy comes from the CPVA archiver
# alone. Kept wired up on purpose: historical days still have their CSV, and the
# fallback costs nothing until the archiver answers a day with no samples.
# CSV fallback — same root / format as Image Finder
ENERGY_CSV_ROOT     = r"//hapls-share.cs.eli-beams.eu/scratch/Salvation/2026_alldata"
ENERGY_CSV_NAME_FMT = "dataof%Y%b_%d"   # e.g. dataof2026Mar_24
# Tolerance for closest-timestamp extra-column matching (seconds).
# PV channels (esp. Back_Ref / waveplate) are sampled sparsely, so a too-tight
# window made secondary/extra PVs show "—" even when valid data existed nearby.
EXTRA_COL_MATCH_TOL_S = 30.0
# Tolerance (ns) for matching a camera image filename timestamp to a shot.
# Widened from 10 s — per-day clock drift between archiver and camera filenames
# could exceed 10 s and blank the preview.
IMG_MATCH_TOL_NS = 30_000_000_000
# How far the camera scan goes into the picked Time window. The camera list is the
# union over the days it scans, newest first, and it stops once this many days have
# actually answered with folders -- see _load_cameras for why one day is not enough.
CAM_SCAN_MAX_DAYS       = 10
CAM_SCAN_DAYS_WITH_DATA = 2

_CAM_IMG_MARK_RE = _re.compile(r"[-_]+IMG(?=$|[-_])", _re.IGNORECASE)
_CAM_CONTAINER_RE = _re.compile(r"^C\d{2}[-_]", _re.IGNORECASE)

def _clean_cam_for_filename(cam: str) -> str:
    """Camera token as it should appear in a saved file name:
    'C03-040-PFM13NF-_-IMG' -> '040-PFM13NF'.

    The '-IMG' marker and the leading container code carry no information for the
    person looking at the file. Cameras without a 'Cxx-' prefix keep whatever
    they have."""
    s = _CAM_IMG_MARK_RE.sub("", cam).strip("-_")
    return _CAM_CONTAINER_RE.sub("", s, count=1).strip("-_")

# ── CPVA ARCHIVER API ─────────────────────────────────────────────────────────
def _import_cpva_client():
    """Load the shared CPVA client (sibling cpva_client.py). Reuses an
    already-loaded instance so every tool (and re-exec'd module copy) shares
    one connection pool and one day cache."""
    import importlib.util as _ilu
    mod = _sys.modules.get("cpva_client")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "cpva_client.py"
    spec = _ilu.spec_from_file_location("cpva_client", p)
    mod = _ilu.module_from_spec(spec)
    _sys.modules["cpva_client"] = mod   # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


cpva = _import_cpva_client()


def _import_img_scale():
    """Load the shared intensity-scale helper (sibling img_scale.py) the same way
    as cpva_client: one instance per process, registered before exec."""
    import importlib.util as _ilu
    mod = _sys.modules.get("img_scale")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "img_scale.py"
    spec = _ilu.spec_from_file_location("img_scale", p)
    mod = _ilu.module_from_spec(spec)
    _sys.modules["img_scale"] = mod     # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


img_scale = _import_img_scale()

CPVA_BASE_URL     = cpva.CPVA_BASE_URL
CPVA_HTTP_TIMEOUT = 15.0   # seconds per channel request

# Maps PV column name → CPVA archiver channel name.
CPVA_CHANNEL_MAP: dict[str, str] = cpva.CHANNEL_MAP

_SLIDER_MOD = None


def _get_slider_module():
    """Borrow helpers (GRADIENTS, _copy_metadata_into_png, …) from the Image
    Slider module WITHOUT re-executing 13k lines of is_t.py on every use —
    prefer the instance main.py already loaded, else load once and cache."""
    global _SLIDER_MOD
    mod = _sys.modules.get("image_slider")
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

PV_COLUMNS: dict[str, str] = {
    "sbw4":      "SBW4 [J]",
    "ptm1":      "PTM1 [J]",
    "pcm2":      "PCM2 [J]",
    "pcm4":      "PCM4 [J]",
    "pap1":      "PAP1 [J]",
    "Back_Ref":  "Back Ref [J]",
    "waveplate": "Waveplate",
}

MJ_COLUMNS = {"Back_Ref", "pap1"}

# PV-search ranking lives in cpva_client so the Shot Finder and the Image Slider
# picker cannot drift apart (see cpva.rank_pv_match).
_CAM_CHANNEL_RE = cpva.CAM_CHANNEL_RE

SBW4_WARNING_THRESHOLD_J = 0.5

# NO column is converted here. SBW4 used to be multiplied by 0.749 (the L3 compressor
# transmission) everywhere this tab printed a value, which made it the only place where
# "SBW4" did not mean the channel's own reading. A picked PV reports what the archiver
# holds — nothing else. Anything derived from a PV belongs in the Slider's PV picker,
# which has named entries ("Compressed SBW4") and formulas for it.

# ── HELPERS ───────────────────────────────────────────────────────────────────

# The `MaxValue` tEXt reader that used to live here is gone: its only callers were the
# three render paths below, and they do not need it. It is the frame's PEAK in raw
# counts (not the sensor's range — it reads 4095 only because a saturated 12-bit frame's
# peak IS 4095), it identified the tag by POSITION (tEXt chunk 12, the Matlab
# `imgMeta.OtherText{12,2}` idiom), and it cost one extra open of a file on the share
# per rendered frame. `img_scale.read_max_value` is the name-based reader if the number
# is ever wanted again.


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


def _bc_value_label(text: str, tooltip: str) -> QLabel:
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
    here, so the Shot Finder cannot drift from the Finder or the Slider.

    Absolute unless Auto contrast is ticked — see img_scale.render_u8. `gamma` is in
    slider units and bends the absolute curve without costing comparability;
    `contrast` / `offset` are the manual pair applied on top. What this replaced was
    `MaxValue * arr / arr.max()` then `/4095`: right only for 12-bit cameras, near-black
    on the 6–11 bit diode cameras, and all-white when the tEXt chunk was missing."""
    if full_scale is None:
        full_scale = img_scale.FULL_SCALE_16
    return img_scale.render_u8(arr, auto, full_scale, gamma, contrast, offset, out)


def _full_scale_for_mode(mode: str, path=None, info: dict = None) -> float:
    """Absolute range of a decoded frame, from the PIL MODE — never from arr.max(): a
    genuinely dark 16-bit frame can hold nothing above 255 and would be brightened
    257× by a value-based guess.

    With `path` and the open image's `.info`, a 16-bit frame goes on its CAMERA's
    reference range instead of a flat 65535 — the archiver's stretch factor is a bracket
    of each frame's own peak, so a camera sitting on a power of two otherwise doubles and
    halves in brightness between frames (see img_scale)."""
    if mode not in ("I", "I;16"):
        return 255.0
    if path is None:
        return img_scale.FULL_SCALE_16
    return img_scale.full_scale_for_pil(path, info or {}, mode)


def _cpva_fetch_channels(pattern: str = "**",
                         timeout: float = CPVA_HTTP_TIMEOUT) -> "list[str]":
    """Return all archiver channel names matching `pattern` (default: all).
    Cached process-wide, so opening a second picker costs nothing."""
    return cpva.fetch_channels_cached(pattern, timeout=timeout)


def _load_csv_for_day(day: date, cols: "list[str]",
                      csv_root: "str | None" = None) -> "tuple[list[dict], dict[str, list[dict]]]":
    """
    Load energy data from daily CSV file (same format as Image Finder).
    Returns ([], {}) if file not found or unreadable.
    """
    root = csv_root if csv_root is not None else ENERGY_CSV_ROOT
    ref_dt = datetime(day.year, day.month, day.day)
    fname  = ref_dt.strftime(ENERGY_CSV_NAME_FMT) + ".csv"
    csv_path = Path(root) / fname
    merged: list[dict] = []
    per_col: dict[str, list[dict]] = {}
    try:
        raw = csv_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return merged, per_col
    if not raw.strip():
        return merged, per_col
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=[",", ";", "\t"])
        delim = dialect.delimiter
    except Exception:
        delim = ","
    reader = csv.DictReader(raw.splitlines(), delimiter=delim)
    if reader.fieldnames is None:
        return merged, per_col
    by_ts: dict[str, dict] = {}
    for r in reader:
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
        # Compute UTC ns from Prague-local datetime
        if PRAGUE is not None:
            dt_aware = dt.replace(tzinfo=PRAGUE)
            t_ns = int(dt_aware.timestamp() * 1_000_000_000)
        else:
            t_ns = int((dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000)
        row_dict: dict = {"_dt": dt, "_ns": t_ns}
        for col in cols:
            if col in row_clean:
                row_dict[col] = row_clean[col]
        by_ts[ts_str] = row_dict
    for row_dict in sorted(by_ts.values(), key=lambda r: r["_dt"]):
        merged.append(row_dict)
        for col in cols:
            if col in row_dict:
                per_col.setdefault(col, []).append(row_dict)
    return merged, per_col


def _load_api_for_day(day: date, cols: "list[str]",
                      log=None,
                      csv_root: "str | None" = None
                      ) -> "tuple[list[dict], dict[str, list[dict]], dict[str, dict]]":
    """
    Query the CPVA archiver for all requested PV columns over the full day.
    Falls back to CSV only when the API answered successfully with no samples —
    an API fetch FAILURE must not be papered over with possibly-zero CSV values
    (that silent source mixing produced alternating real/0 values day to day).

    Returns (merged_rows, per_col_rows, col_meta) where:
      - merged_rows: list of row dicts merged by timestamp across all channels
      - per_col_rows: dict mapping col → sorted list of single-col row dicts
        (used for closest-timestamp extra-column matching)
      - col_meta: col → {"source": "api"|"csv"|"none", "status": "ok"|"stale"|"error"|"empty"}

    Each row dict has:
      "_dt" : datetime (Prague-naive)
      "_ns" : int (UTC nanoseconds)
      "<col>": str value
    """
    def _log(msg):
        if log is not None:
            log(msg)

    # Per-column sample lists (API first, CSV fallback per column)
    per_col: dict[str, list[dict]] = {}
    col_meta: dict[str, dict] = {}

    def _fetch_one_col(col: str) -> "tuple[str, list[dict], dict]":
        # Preset cols map to a friendly channel; arbitrary cols ARE the channel.
        channel = CPVA_CHANNEL_MAP.get(col, col)
        col_rows: list[dict] = []
        meta = {"source": "none", "status": "empty"}
        if channel:
            date_key = day.strftime("%Y-%m-%d")
            # Shared day cache: repeat searches over the same days are served
            # from memory; the ".value" channel-suffix retry happens inside.
            res = cpva.get_day(channel, date_key, timeout=CPVA_HTTP_TIMEOUT)
            meta["status"] = res.status
            if res.status == "error":
                _log(f"  API {col} ({channel}) FETCH FAILED — shown as ERR "
                     f"(no CSV fallback: mixing sources hides the outage)")
                return col, [], meta
            if res.status == "stale":
                _log(f"  API {col} ({channel}): fetch failed, using "
                     f"{len(res.samples)} samples from {res.age_s:.0f}s ago")
            for t_ns, v in res.samples:
                if PRAGUE is not None:
                    dt_local = datetime.fromtimestamp(
                        t_ns / 1e9, tz=timezone.utc).astimezone(PRAGUE).replace(tzinfo=None)
                else:
                    dt_local = datetime.utcfromtimestamp(t_ns / 1e9)
                col_rows.append({"_dt": dt_local, "_ns": t_ns, col: str(v)})
            if col_rows:
                meta["source"] = "api"
                _log(f"  API {col} ({channel}): {len(col_rows)} samples")
        else:
            _log(f"  {col}: no CPVA channel mapping, trying CSV only")
        if not col_rows:
            _, csv_per = _load_csv_for_day(day, [col], csv_root=csv_root)
            col_rows = csv_per.get(col, [])
            if col_rows:
                meta["source"] = "csv"
                meta["status"] = "ok"
                _log(f"  CSV fallback {col}: {len(col_rows)} rows")
            else:
                _log(f"  CSV fallback {col}: no data")
        return col, col_rows, meta

    with ThreadPoolExecutor(max_workers=max(1, len(cols))) as _aex:
        _col_futs = {_aex.submit(_fetch_one_col, c): c for c in cols}
        for _fut in as_completed(_col_futs):
            try:
                _col, _col_rows, _meta = _fut.result()
                col_meta[_col] = _meta
                if _col_rows:
                    per_col[_col] = _col_rows
            except Exception as exc:
                _log(f"  col fetch ERROR: {type(exc).__name__}: {exc}")

    for c in cols:
        col_meta.setdefault(c, {"source": "none", "status": "error"})

    # Merge all per-col rows into a single list keyed by _ns
    by_ts: dict[int, dict] = {}
    for col, col_rows in per_col.items():
        for r in col_rows:
            t_ns = r["_ns"]
            if t_ns not in by_ts:
                by_ts[t_ns] = {"_dt": r["_dt"], "_ns": t_ns}
            by_ts[t_ns][col] = r[col]

    merged = [by_ts[k] for k in sorted(by_ts)]
    counts = ", ".join(f"{c}={len(per_col.get(c, []))}" for c in cols)
    _log(f"  {day}: merged {len(merged)} rows; samples per col: {counts}")
    for c in cols:
        if not per_col.get(c):
            st = col_meta[c]["status"]
            _log(f"  ⚠ {day}: NO data for '{c}' "
                 f"({'fetch FAILED' if st == 'error' else 'API+CSV both empty'})")
    return merged, per_col, col_meta


def _find_closest_col_value(per_col: "dict[str, list[dict]]", col: str,
                             target_ns: int, tol_s: float = EXTRA_COL_MATCH_TOL_S,
                             log=None) -> str:
    """Compat wrapper around _lookup_col_value — formatted-ish raw value or "—"."""
    raw, state = _lookup_col_value(per_col, {}, col, target_ns, tol_s=tol_s,
                                   allow_network=False, log=log)
    return raw if state == "ok" else "—"


def _lookup_col_value(per_col: "dict[str, list[dict]]", col_meta: "dict[str, dict]",
                      col: str, target_ns: int,
                      tol_s: float = EXTRA_COL_MATCH_TOL_S,
                      allow_network: bool = True,
                      log=None) -> "tuple[str, str]":
    """
    Resolve the value of `col` at target_ns. Returns (raw_value, state):
      state "ok"        — sample within tol_s (or slow-PV look-back hit); raw is valid
      state "error"     — the fetch for this column FAILED (display "ERR")
      state "not_found" — data loaded fine but no sample matches (display "n/a")

    Slow PVs (waveplate — archived on-change, so the last sample can be days
    old) fall back to the archiver's last-at-or-before lookup. Fast energy PVs
    never do: a value from a different shot minutes away would be wrong.
    allow_network=False (UI thread) still serves look-back cache hits.
    """
    meta = col_meta.get(col, {})
    rows = per_col.get(col)
    best = None
    best_diff = float("inf")
    if rows:
        ts_list = [r["_ns"] for r in rows]
        idx = bisect.bisect_left(ts_list, target_ns)
        for i in [idx - 1, idx]:
            if 0 <= i < len(rows):
                diff = abs(rows[i]["_ns"] - target_ns)
                if diff < best_diff:
                    best_diff = diff
                    best = rows[i]
        tol_ns = int(tol_s * 1_000_000_000)
        if best is not None and best_diff <= tol_ns:
            return best.get(col, ""), "ok"

    # No sample in window — slow PVs look back for the last known value.
    channel = CPVA_CHANNEL_MAP.get(col, col)
    if channel in cpva.FORWARD_CHANNELS:
        res = cpva.value_at_or_before(channel, int(target_ns),
                                      timeout=CPVA_HTTP_TIMEOUT,
                                      network_ok=allow_network)
        if res.value is not None:
            if log is not None:
                log(f"  '{col}': look-back hit (last change "
                    f"{(target_ns - (res.ts_ns or target_ns)) / 1e9 / 3600:.1f} h before)")
            return str(res.value), "ok"
        if res.status == "error":
            return "", "error"

    if not rows and meta.get("status") == "error":
        return "", "error"
    if log is not None:
        if best is not None:
            log(f"  closest '{col}': nearest sample {best_diff/1e9:.1f}s away "
                f"(> {tol_s:.0f}s window) → n/a")
        else:
            log(f"  closest '{col}': no samples loaded → n/a")
    return "", "not_found"


def _format_value_state(col: str, raw: str, state: str) -> str:
    """Tri-state display: real value (incl. genuine 0) / "ERR" / "n/a"."""
    if state == "error":
        return cpva.PV_TEXT_ERROR
    if state != "ok" or raw == "":
        return cpva.PV_TEXT_NOT_FOUND
    return _format_value(col, raw)


def _quantize_col(col: str, v: float) -> float:
    """Snap a column's value onto its channel value grid, if it has one.

    Only the waveplate does today (multiples of 1000). Applied BEFORE any
    arithmetic, not just before display: a mid-move readback of 350 437 otherwise
    produces a Δ of 437 against a target of 350 000, i.e. a difference between a
    real setting and a position that never existed.

    EVERY comparison in this tab goes through here — matching, the tolerance filter and
    the displayed Δ alike. When the filter alone skipped it, a day could be headed
    "✓ match, Δ 0" while the shot list behind it said "no shots within tolerance": the
    same reading counted as 350 000 in one place and as 350 437 in the other."""
    return _quantize_col_ex(col, v)[0]


def _quantize_col_ex(col: str, v: float) -> "tuple[float, bool]":
    """(snapped value, was it already on the grid). An off-grid reading is the motor
    caught mid-travel; it is still reported as the position it was heading to, but the
    flag lets the display mark it so it cannot be mistaken for a settled one."""
    return cpva.quantize(CPVA_CHANNEL_MAP.get(col, col), v)


def _diff_ui_value(col: str, diff_csv: float) -> float:
    """|value − target| in the units the row PRINTS. The tolerance is compared against
    this, so it has to follow _format_diff exactly — they used to be two copies of the
    same arithmetic and a column could be marked off-target while its own Δ cell said
    it was inside."""
    return diff_csv * 1000 if col in MJ_COLUMNS else diff_csv


def _format_diff(col: str, diff_csv: float) -> str:
    """Format a |value − target| difference (CSV units) in the column's UI units."""
    d = _diff_ui_value(col, diff_csv)
    if col in MJ_COLUMNS:
        return f"{d:.2f} mJ"
    if col == "waveplate":
        return f"{d:.0f}"
    if col in PV_COLUMNS:
        return f"{d:.4f} J"
    return f"{d:.4g}"


def _find_best_match(rows: list[dict], col: str, target: float) -> dict | None:
    # NOTE: `target` is already in CSV units (callers pass target_csv, i.e. the mJ
    # conversion is applied upstream in _to_csv_units). Do NOT re-convert here.
    best = None
    best_diff = float("inf")
    for row in rows:
        raw = row.get(col, "")
        try:
            val = _quantize_col(col, float(raw))
        except (ValueError, TypeError):
            continue
        diff = abs(val - target)
        if diff < best_diff:
            best_diff = diff
            best = row
    return best


def _folder_hour_from_prague(prague_hour: int, ref_date: date) -> int:
    if PRAGUE is not None:
        dt_p = datetime(ref_date.year, ref_date.month, ref_date.day,
                        prague_hour, 0, 0, tzinfo=PRAGUE)
        offset_h = int(dt_p.utcoffset().total_seconds() / 3600)
        return (prague_hour - offset_h) % 24
    return (prague_hour - 1) % 24


def _day_image_folder(day: date, images_root: "Path | None" = None) -> Path | None:
    """The day's folder on the image share, or None when the day was never archived.

    Worth its own answer: "the whole day is missing" and "that HOUR is missing" look the
    same from _find_hour_folder, and telling an operator "no images for this day" about a
    day that has pictures from 08:00 to 18:00 is simply wrong."""
    root = images_root if images_root is not None else IMAGES_ROOT
    base = (_images_root_for_year(root, day.year)
            / str(day.year) / str(day.month) / str(day.day))
    try:
        return base if base.is_dir() else None
    except Exception:
        return None


def _find_hour_folder(day: date, hour_utc: int,
                      images_root: "Path | None" = None) -> Path | None:
    root = images_root if images_root is not None else IMAGES_ROOT
    base = _images_root_for_year(root, day.year) / str(day.year) / str(day.month) / str(day.day)
    for delta in [0, -1, 1, -2, 2]:
        h = (hour_utc + delta) % 24
        candidate = base / str(h)
        try:
            if candidate.exists() and candidate.is_dir():
                return candidate
        except Exception:
            pass
    return None


def _resolve_cam_folder(hour_folder: "Path | None", cam: str) -> Path | None:
    """Camera sub-folder inside an hour folder, matched case-insensitively."""
    if hour_folder is None or not cam:
        return None
    try:
        cf = hour_folder / cam
        if cf.exists() and cf.is_dir():
            return cf
        for sub in hour_folder.iterdir():
            if sub.is_dir() and sub.name.lower() == cam.lower():
                return sub
    except Exception:
        pass
    return None


def _find_image_for_ts(cam_folder: Path, ts_dt: datetime,
                       ts_ns_override: "int | None" = None) -> Path | None:
    """Najde nejbližší obrázkový soubor k timestampu (max 5s tolerance).

    ts_ns_override: if provided, use it directly (API rows carry exact UTC ns).
    ts_dt: Prague-naive datetime used as fallback when ts_ns_override is None.
    """
    if not cam_folder.exists():
        return None

    if ts_ns_override is not None:
        ts_ns_target = ts_ns_override
    elif PRAGUE is not None:
        ts_aware = ts_dt.replace(tzinfo=PRAGUE)
        ts_ns_target = int(ts_aware.timestamp() * 1_000_000_000)
    else:
        from datetime import timezone as _tz
        ts_aware = ts_dt.replace(tzinfo=_tz(timedelta(hours=1)))
        ts_ns_target = int(ts_aware.timestamp() * 1_000_000_000)

    IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
    best_file = None
    best_diff = float("inf")

    try:
        with os.scandir(cam_folder) as it:
            for e in it:
                if not e.is_file():
                    continue
                p = Path(e.name)
                if p.suffix.lower() not in IMG_EXT:
                    continue
                stem = p.stem
                for i in range(len(stem) - 18):
                    sub = stem[i:i + 19]
                    if sub.isdigit():
                        ts_ns = int(sub)
                        if 946684800_000_000_000 <= ts_ns <= 4102444800_000_000_000:
                            diff = abs(ts_ns - ts_ns_target)
                            if diff < best_diff:
                                best_diff = diff
                                best_file = cam_folder / e.name
                            break
    except Exception:
        pass

    if best_file is not None and best_diff < IMG_MATCH_TOL_NS:
        return best_file
    return None


def _find_image_in_day(day: date, cam: str, dt_obj: datetime, ts_ns: "int | None",
                       hour_cache: dict, images_root: "Path | None" = None,
                       day_dir: "Path | None" = None
                       ) -> "tuple[Path | None, Path | None]":
    """(matched image, camera folder it was looked for in) for ONE shot.

    The single image resolver for this tab. A shot is looked for in the hour folder its
    Prague time falls in AND in the neighbouring hours, because the archive rolls a frame
    over the hour edge and because a camera is not necessarily recording every hour of
    the day (2026-08-17 held 92 cameras, only 27 of them in every hour). The 30 s window
    inside _find_image_for_ts is what actually decides the match — a neighbouring hour
    can only ever contribute a frame from just across the boundary.

    hour_cache maps hour → camera folder, so resolving many shots of one day does not
    re-probe the share. Call from a worker thread: os.scandir over SMB blocks ~150 ms."""
    if dt_obj is None or not cam:
        return None, None
    hour_utc = _folder_hour_from_prague(dt_obj.hour, day)
    if day_dir is None:
        hf = _find_hour_folder(day, hour_utc, images_root=images_root)
        if hf is None:
            return None, None
        day_dir = hf.parent
    first_folder = None
    for delta in (0, -1, 1, -2, 2):
        h = (hour_utc + delta) % 24
        if h in hour_cache:
            cam_folder = hour_cache[h]
        else:
            cam_folder = _resolve_cam_folder(day_dir / str(h), cam)
            hour_cache[h] = cam_folder
        if cam_folder is None:
            continue
        if first_folder is None:
            first_folder = cam_folder
        img = _find_image_for_ts(cam_folder, dt_obj, ts_ns_override=ts_ns)
        if img is not None:
            return img, cam_folder
    return None, first_folder


def _image_problem(path: "Path | None") -> "str | None":
    """None when `path` is a picture worth offering, else why it is not.

    A row that points at a file nobody can look at is the same disappointment as a row
    that points at nothing, so an empty file and an all-zero frame count as "no image"
    (the operator asked for this explicitly). Reads the file once, cheapest test first."""
    if path is None:
        return f"no image within {IMG_MATCH_TOL_NS / 1e9:.0f} s of the shot"
    try:
        if path.stat().st_size == 0:
            return "image file is empty"
    except OSError as exc:
        return f"image file unreadable ({type(exc).__name__})"
    try:
        from PIL import Image as _PilImg
        import numpy as _np
        with _PilImg.open(str(path)) as im:
            arr = _np.asarray(im)
    except Exception as exc:
        return f"image cannot be decoded ({type(exc).__name__})"
    if arr.size == 0 or not bool(arr.max() > 0):
        return "image is blank (all zero)"
    return None


def _format_value(col: str, raw: str) -> str:
    try:
        v = float(raw)
        if col == "waveplate":
            # Snap onto the 1000-count grid: the waveplate is only ever commanded to
            # whole multiples of 1000, so any other reading is the motor caught
            # mid-travel. "%.0f" alone still printed a position it was never set to.
            # A snapped-from-off-grid value is prefixed "~": it reads as the setting it
            # belongs to, and still admits the shot was taken while it was moving.
            v_q, exact = _quantize_col_ex(col, v)
            return f"{'' if exact else '~'}{v_q:.0f}"
        if col in MJ_COLUMNS:
            return f"{v * 1000:.2f} mJ"
        if col in PV_COLUMNS:          # known energy preset
            return f"{v:.4f} J"
        return f"{v:.4g}"             # arbitrary channel — raw, no unit assumption
    except (ValueError, TypeError):
        return raw or "—"


# ── SIGNALS ───────────────────────────────────────────────────────────────────

class _SearchSignals(QObject):
    result   = Signal(object)
    done     = Signal()
    log_msg  = Signal(str)
    progress = Signal(int)


class _CamLoadSignals(QObject):
    finished = Signal(list)
    log_msg  = Signal(str)

class _PreviewSignals(QObject):
    show    = Signal(object, str, int)  # (QImage | None, energy_text, gen)
    log_msg = Signal(str)

class _ChannelSignals(QObject):
    loaded = Signal(list)  # archiver channel names

class _NoScrollComboBox(QComboBox):
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
    "time":    "#2f6fd0",   # blue
    "pv":      "#1a9e9e",   # teal
    "cameras": "#2e9e5b",   # green
    "search":  "#c0392b",   # red
    "display": "#7a4fc0",   # purple
    "log":     "#5a5f8f",   # slate
}


def _section_cls():
    return _get_slider_module().CollapsibleSection


# ── RESULT DATA ───────────────────────────────────────────────────────────────

class _DayResult:
    # status of one day in the results table:
    #   "ok"       — a shot AND a usable picture were found
    #   "no_data"  — the archiver gave nothing to search (outage, or no samples)
    #   "no_image" — a shot was found but no picture goes with it
    # Every searched day produces one of these, so a day can never quietly vanish from
    # the table; only "ok" days can be previewed, saved or sent to the Slider.
    def __init__(self, day: date, best_row: "dict | None", col: str,
                 actual, diff, target_csv: float, hour_folder,
                 rows_in_tol: "list | None" = None,
                 per_col: "dict | None" = None,
                 search_cols: "list | None" = None,
                 extra_cols: "list | None" = None,
                 criteria_csv: "list | None" = None,
                 cam: "str | None" = None,
                 col_meta: "dict | None" = None,
                 img_path=None,
                 status: str = "ok",
                 reason: str = ""):
        self.day          = day
        self.best_row     = best_row if best_row is not None else {}
        self.status       = status
        self.reason       = reason
        self.col          = col
        self.actual       = actual
        self.diff         = diff
        self.target_csv   = target_csv
        self.hour_folder  = hour_folder
        self.rows_in_tol  = rows_in_tol or []
        self.per_col      = per_col or {}
        # Search-time state, persisted so later UI (double-click dialog, save,
        # open-in-slider) reflects what was actually searched — not whatever
        # the left panel happens to show now.
        self.search_cols  = search_cols or [col]
        self.extra_cols   = extra_cols or []
        self.criteria_csv = criteria_csv or []
        self.cam          = cam
        self.col_meta     = col_meta or {}
        self.img_path     = img_path   # matched image file (resolved in worker)
        # Prefer exact UTC ns from API rows; fall back to Prague-naive datetime
        if self.best_row.get("_ns") is not None:
            self.ts_ns = int(self.best_row["_ns"])
        else:
            dt_obj: datetime = self.best_row.get("_dt")
            if dt_obj is not None and PRAGUE is not None:
                ts_aware = dt_obj.replace(tzinfo=PRAGUE)
                self.ts_ns = int(ts_aware.timestamp() * 1_000_000_000)
            else:
                self.ts_ns = None


class _PreviewWidget(QWidget):
    """Zobrazuje obrázek vycentrovaný bez černých pásů."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm: QPixmap | None = None
        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor(26, 26, 26))
        self.setPalette(p)

    def set_pixmap(self, pm):
        self._pm = pm
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(243, 243, 243))
        if self._pm is None or self._pm.isNull():
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No preview")
            p.end()
            return
        scaled = self._pm.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        p.end()

# ── TIME WINDOW DIALOG ────────────────────────────────────────────────────────

class _TimeWindowDialog(QDialog):
    """Pick start and end datetime (date + hour) for Shot Finder."""

    def __init__(self, start_dt: datetime | None = None, end_dt: datetime | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Time window")

        now = datetime.now(PRAGUE) if PRAGUE else datetime.now()
        if start_dt is None:
            start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if end_dt is None:
            end_dt = now

        def _make_cal(init_date: datetime) -> "tuple[QFrame, QCalendarWidget]":
            """One calendar in the house style — literally the Image Slider's widget
            (Monday first, gray day-name header, red weekends, white cells, month
            button + year box instead of Qt's own nav bar), so a day looks and clicks
            the same in every tab.

            The Slider's delegate paints the selection itself, so the picked day has
            to be handed to it — Qt's own highlight is stripped from every other cell.
            Returns (frame_to_add, cal); the frame carries the header and nav row."""
            frame, cal = _get_slider_module()._make_multiselect_calendar(
                QDate(init_date.year, init_date.month, init_date.day))
            cal.setMinimumWidth(238)

            def _paint_one(d: QDate):
                dele = getattr(cal, "_wk_delegate", None)
                if dele is not None:
                    dele.set_selected([d])
                    dele.set_focus_date(d)

            cal.clicked.connect(_paint_one)
            cal._paint_one = _paint_one      # so "Now" can repaint after jumping
            _paint_one(cal.selectedDate())
            return frame, cal

        # Start section
        grp_start = QGroupBox("Start point")
        start_lay = QVBoxLayout(grp_start)
        self._cal_start_frame, self._cal_start = _make_cal(start_dt)
        self._hour_start = QSpinBox()
        self._hour_start.setRange(0, 23)
        self._hour_start.setValue(start_dt.hour)
        self._hour_start.setFixedWidth(70)
        hr_start_row = QHBoxLayout()
        hr_start_row.addWidget(QLabel("Hour:"))
        hr_start_row.addWidget(self._hour_start)
        hr_start_row.addStretch(1)
        start_lay.addWidget(self._cal_start_frame)
        start_lay.addLayout(hr_start_row)

        # End section
        grp_end = QGroupBox("End point")
        end_lay = QVBoxLayout(grp_end)
        self._cal_end_frame, self._cal_end = _make_cal(end_dt)
        self._hour_end = QSpinBox()
        self._hour_end.setRange(0, 23)
        self._hour_end.setValue(end_dt.hour)
        self._hour_end.setFixedWidth(70)
        hr_end_row = QHBoxLayout()
        hr_end_row.addWidget(QLabel("Hour:"))
        hr_end_row.addWidget(self._hour_end)
        btn_now = QPushButton("Now")
        btn_now.setFixedWidth(48)
        btn_now.clicked.connect(self._go_to_now)
        hr_end_row.addWidget(btn_now)
        hr_end_row.addStretch(1)
        end_lay.addWidget(self._cal_end_frame)
        end_lay.addLayout(hr_end_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        cals_row = QHBoxLayout()
        cals_row.addWidget(grp_start)
        cals_row.addWidget(grp_end)

        main_lay = QVBoxLayout(self)
        main_lay.addLayout(cals_row)
        main_lay.addWidget(btns)

    def _go_to_now(self):
        now = datetime.now(PRAGUE) if PRAGUE else datetime.now()
        qd = QDate(now.year, now.month, now.day)
        self._cal_end.setSelectedDate(qd)
        self._cal_end.setCurrentPage(qd.year(), qd.month())
        # setSelectedDate does not go through clicked(), so repaint by hand or the
        # blue day stays on the day the dialog opened on.
        paint = getattr(self._cal_end, "_paint_one", None)
        if paint is not None:
            paint(qd)
        self._hour_end.setValue(now.hour)

    def _on_accept(self):
        start = self._selected_start()
        end = self._selected_end()
        if end < start:
            QMessageBox.warning(self, "Invalid range", "End must be after start.")
            return
        self.accept()

    def _selected_start(self) -> datetime:
        d = self._cal_start.selectedDate()
        tz = PRAGUE if PRAGUE else timezone.utc
        return datetime(d.year(), d.month(), d.day(), self._hour_start.value(), 0, 0, tzinfo=tz)

    def _selected_end(self) -> datetime:
        d = self._cal_end.selectedDate()
        tz = PRAGUE if PRAGUE else timezone.utc
        return datetime(d.year(), d.month(), d.day(), self._hour_end.value(), 59, 59, tzinfo=tz)

    def selected_range(self) -> tuple[datetime, datetime]:
        return self._selected_start(), self._selected_end()

    def selected_days(self) -> list[date]:
        start = self._selected_start().date()
        end = self._selected_end().date()
        days = []
        cur = start
        while cur <= end:
            days.append(cur)
            cur += timedelta(days=1)
        return days


# ── MAIN WIDGET ───────────────────────────────────────────────────────────────

class ShotFinderWidget(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self._slider_ref = None
        self._tab_widget = None

        self._search_running = False
        # Results live per camera tab — see _rebuild_result_tabs. `_day_results` and
        # `_table` are read-only views onto whichever tab is in front.
        self._cam_order: "list[str | None]" = [None]
        self._cam_tables: "dict[str | None, QTableWidget]" = {}
        self._cam_results: "dict[str | None, list[_DayResult]]" = {}

        # The day expanded under the results table (see _build_day_panel). Everything
        # it needs is captured when the day is opened, so a later change of the left
        # panel or of the camera list cannot re-aim it.
        self._day_dr = None
        self._day_rows: "list[dict]" = []
        self._day_cam: "str | None" = None
        self._day_hour_cache: dict = {}
        self._day_split_sized = False

        self._all_cameras: list[tuple[str, str]] = []
        self._selected_cameras: list[tuple[str, str]] = []
        self._active_cam: str | None = None

        self._images_root: Path = IMAGES_ROOT
        self._energy_csv_root: str = ENERGY_CSV_ROOT
        # The picked PVs, the names given to them and their scale factors are
        # restored HERE, before _build_ui — which must therefore not clear them.
        self._pv_cfg: list[dict] = []
        self._pv_rows: list[dict] = []
        self._custom_labels: dict[str, str] = {}   # col → name to display
        self._load_pv_state()

        _now = datetime.now(PRAGUE) if PRAGUE else datetime.now()
        self._tw_start: datetime = _now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._tw_end: datetime = _now

        self._temp_dir: str | None = None
        self._preview_pixmap_orig = None
        self._preview_gen = 0
        self._preview_sig = _PreviewSignals()
        self._current_preview_path: "Path | None" = None
        self._preview_sig.show.connect(self._on_preview_ready)
        self._preview_sig.log_msg.connect(self._log)
        atexit.register(self._cleanup_temp)
        self._last_save_dir: "Path | None" = None

        self._build_ui()

    def hideEvent(self, event):
        super().hideEvent(event)
        # Floating Tool-window dropdowns stay on top of every other tab unless
        # they are explicitly hidden with the widget.
        for name in ("_pv_dropdown",):
            dd = getattr(self, name, None)
            if dd is not None:
                dd.hide()
        for name in ("_pv_search",):
            le = getattr(self, name, None)
            if le is not None:
                le.clear()
        # Target / tolerance live in the spin boxes until something asks for them.
        self._sync_pv_cfg_from_rows()
        self._save_pv_state()

    def _cleanup_temp(self):
        if self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # _PreviewWidget se překresluje sám přes paintEvent — nic navíc nepotřebujeme

    def _on_preview_ready(self, out_img, energy_text: str, gen: int):
        """Main-thread slot — receives processed QImage from background thread,
        converts to QPixmap and paints energy overlay here (QPixmap requires main thread)."""
        if gen != self._preview_gen:
            return
        if out_img is None:
            self._preview_pixmap_orig = None
            self._preview_widget.set_pixmap(None)
            self._scale_note_lbl.setText("")
            return
        self._scale_note_lbl.setText(getattr(self, "_scale_note", ""))
        # Park the greyed-out sliders on what the Auto passes actually applied to this
        # frame — a value you cannot name is a value you cannot reproduce.
        self._park_auto_bc(getattr(self, "_bc_applied", None))
        from PySide6.QtGui import QPixmap, QPainter, QFont, QColor, QFontMetrics
        from PySide6.QtCore import QRect
        pm = QPixmap.fromImage(out_img)
        if energy_text:
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
                display_text = "  |  ".join(parts_split[:mid]) + "\n" + "  |  ".join(parts_split[mid:])
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
            painter.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter, display_text)
            painter.end()
            pm = combined
        self._preview_pixmap_orig = pm
        self._rescale_preview()

    # ── LOGGING ───────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        for btn in [self._btn_search, self._btn_open_slider, self._btn_save_results,
                    self._gradient_cb, self._btn_time_window,
                    self._btn_clear_results]:
            btn.setEnabled(not busy)
    
    def _log(self, msg: str):
        if hasattr(self, "_log_box"):
            self._log_box.appendPlainText(str(msg))
            sb = self._log_box.verticalScrollBar()
            sb.setValue(sb.maximum())

    # ── UI BUILD ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        # ════ LEFT PANEL ═════════════════════════════════════════════════════
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(280)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet(
            "QScrollArea{background:transparent;}QScrollBar:vertical{width:8px;}")

        lw = QWidget()
        lw.setMinimumWidth(260)
        ll = QVBoxLayout(lw)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(4)
        # The panel layout the groups themselves sit in. `ll` below is re-pointed at
        # each group's body in turn, so everything after a group banner lands inside
        # that group.
        panel_lay = ll

        # ── Collapsible groups ───────────────────────────────────────────────
        # Same groups, same colours and the same remembered open/closed state as the
        # Image Slider, Workshop and Image Finder panels. Which groups are open is
        # kept in this tab's own state file next to the PV list.
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

        _exp_row = QHBoxLayout()
        _exp_row.setSpacing(4)
        _btn_exp = QPushButton("Expand all")
        _btn_exp.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_exp.clicked.connect(lambda: self._set_all_sections(True))
        _btn_col = QPushButton("Collapse all")
        _btn_col.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_col.clicked.connect(lambda: self._set_all_sections(False))
        _exp_row.addWidget(_btn_exp)
        _exp_row.addWidget(_btn_col)
        panel_lay.addLayout(_exp_row)

        s_time = _add_section("time",    "Source",               True)
        s_pv   = _add_section("pv",      "PVs",                  True)
        s_cam  = _add_section("cameras", "Cameras",              True)
        s_srch = _add_section("search",  "Search & Results",     True)
        s_disp = _add_section("display", "Image / Display",      False)
        s_log  = _add_section("log",     "Log",                  False)

        # ══════════════════ Group: SOURCE ════════════════════════════════════
        # What the search runs over: WHEN, and WHICH cameras. Two buttons, the way
        # the Image Slider has it. The Lab / Office picker that used to sit here is
        # gone — see IMAGES_ROOT.
        ll = s_time.body_layout
        self._btn_time_window = QPushButton("📅  Time window")
        self._btn_time_window.setToolTip("Pick start and end date/hour for the search")
        self._btn_time_window.clicked.connect(self._open_time_window)
        ll.addWidget(self._btn_time_window)

        self._btn_cameras = QPushButton("📷  Cameras…")
        self._btn_cameras.setToolTip(
            "Choose which cameras to search — the same picker (and the same saved "
            "presets) as the Image Slider.\n"
            "The list is the cameras found in the picked time window.")
        self._btn_cameras.clicked.connect(self._open_camera_picker)
        ll.addWidget(self._btn_cameras)

        self._date_info_lbl = QLabel("")
        self._date_info_lbl.setStyleSheet("font-size: 10px; color: #555;")
        ll.addWidget(self._date_info_lbl)

        # ── PV search state ───────────────────────────────────────────────
        # ONE ordered list of picked PVs. Each entry carries its own "filter"
        # flag: checked = the search filters on it (target ± tolerance),
        # unchecked = its value is only displayed on the results/images.
        # _pv_cfg [{col, target, tol, filter, scale}] and _custom_labels are set up
        # (and restored from the saved state) in __init__ — clearing them here would
        # throw the operator's PV list away on every restart.
        self._pv_rows: list[dict] = []            # widgets, parallel to _pv_cfg
        self._all_pv_channels: list[str] = []     # fetched archiver channels
        self._chan_loading = False
        self._pv_suggestions: list[tuple[str, str]] = []  # (display, col_key)
        self._rebuild_pv_suggestions()

        # ══════════════════ Group: PVs ══════════════════════════════════════
        # PV selection — one search box for every PV, presets and archiver alike
        ll = s_pv.body_layout
        self._pv_search = QLineEdit()
        self._pv_search.setPlaceholderText("search PV, or type a channel name…")
        self._pv_search.setToolTip(
            "Add any archiver PV: type fragments to search the channel list, or type a\n"
            "channel name in full and take the ➕ row (or press Enter) to add it as it\n"
            "stands — that also works for a PV the fetched list does not carry.\n"
            "Tick a PV to search by it (target ± tolerance); leave it unticked to only\n"
            "show its value in the results and on the images.\n"
            "Right-click a PV row to rename it, give it a scale factor, copy its\n"
            "archiver channel name or remove it.")
        self._pv_search.textEdited.connect(self._on_pv_search_changed)
        self._pv_search.returnPressed.connect(self._on_pv_search_return)
        ll.addWidget(self._pv_search)
        self._pv_dropdown = self._make_pv_dropdown(self._on_pv_dropdown_clicked)

        # One dynamic row per picked PV
        self._pv_container = QWidget()
        self._pv_container_layout = QVBoxLayout(self._pv_container)
        self._pv_container_layout.setContentsMargins(0, 0, 0, 0)
        self._pv_container_layout.setSpacing(2)
        ll.addWidget(self._pv_container)

        # Hidden legacy spinboxes — kept so existing code that references them still works
        self._target_sb = QDoubleSpinBox()
        self._target_sb.setRange(-1e9, 1e9)
        self._target_sb.setDecimals(3)
        self._target_sb.setValue(10.0)
        self._target_sb.setVisible(False)
        self._unit_lbl = QLabel("J")
        self._unit_lbl.setVisible(False)
        self._pm_lbl = QLabel("±")
        self._pm_lbl.setVisible(False)
        self._tol_sb = QDoubleSpinBox()
        self._tol_sb.setRange(0.0, 1e9)
        self._tol_sb.setDecimals(3)
        self._tol_sb.setValue(SBW4_WARNING_THRESHOLD_J)
        self._tol_sb.setVisible(False)
        self._tol_unit_lbl = QLabel("J")
        self._tol_unit_lbl.setVisible(False)

        # ══════════════════ Group: CAMERAS ══════════════════════════════════
        # The search box + floating suggestion list that used to sit here are what
        # the Cameras… button now opens; the panel keeps only the picked cameras.
        ll = s_cam.body_layout
        ll.addWidget(QLabel("Selected cameras:"))
        self._cam_selected = QTableWidget(0, 2)
        self._cam_selected.setHorizontalHeaderLabels(["#", "Camera"])
        self._cam_selected.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._cam_selected.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._cam_selected.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cam_selected.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cam_selected.setMinimumHeight(100)
        self._cam_selected.setMaximumHeight(300)
        self._cam_selected.verticalHeader().setDefaultSectionSize(22)
        self._cam_selected.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._cam_selected.verticalHeader().setVisible(False)
        self._cam_selected.clicked.connect(self._on_cam_selected_clicked)
        ll.addWidget(self._cam_selected)

        btn_cam_remove = QPushButton("✕ Remove selected camera")
        btn_cam_remove.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_cam_remove.clicked.connect(self._on_cam_remove)
        ll.addWidget(btn_cam_remove)

        self._cam_status_lbl = QLabel("No cameras loaded.")
        self._cam_status_lbl.setStyleSheet("font-size: 10px; color: #555;")
        ll.addWidget(self._cam_status_lbl)

        # ══════════════════ Group: SEARCH & RESULTS ═════════════════════════
        ll = s_srch.body_layout

        # Search button
        self._btn_search = QPushButton("🔍  Search")
        self._btn_search.setFixedHeight(32)
        self._btn_search.setStyleSheet(
            "QPushButton { background: #2d7dff; color: #fff; font-weight: 700; "
            "border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background: #1a6aee; }"
            "QPushButton:disabled { background: #aaa; }")
        self._btn_search.clicked.connect(self._start_search)
        ll.addWidget(self._btn_search)

        self._prog = QProgressBar()
        self._prog.setVisible(False)
        self._prog.setTextVisible(False)
        self._prog.setRange(0, 0)
        ll.addWidget(self._prog)

        # Open in Slider — hned pod Search
        self._btn_open_slider = QPushButton("➤  Send to Image Slider")
        self._btn_open_slider.setEnabled(False)
        self._btn_open_slider.setVisible(False)
        self._btn_open_slider.setToolTip(
            "Send the matched images (selected rows, or all when nothing is selected)\n"
            "to the Image Slider — whatever the Slider currently shows is replaced.")
        self._btn_open_slider.clicked.connect(self._open_in_slider)
        ll.addWidget(self._btn_open_slider)

        self._btn_save_results = QPushButton("💾  Save images")
        self._btn_save_results.setEnabled(False)
        self._btn_save_results.setToolTip(
            "Save matched images to a selected folder.")
        self._btn_save_results.clicked.connect(self._save_results)
        ll.addWidget(self._btn_save_results)

        self._btn_send_workshop = QPushButton("➤ Workshop")
        self._btn_send_workshop.setEnabled(False)
        self._btn_send_workshop.setVisible(False)
        self._btn_send_workshop.setToolTip("Send currently previewed image to Workshop tab for editing")
        self._btn_send_workshop.clicked.connect(self._send_to_workshop)
        ll.addWidget(self._btn_send_workshop)

        # ══════════════════ Group: IMAGE / DISPLAY ══════════════════════════
        ll = s_disp.body_layout

        grad_row = QHBoxLayout()
        grad_row.addWidget(QLabel("Gradient:"))
        self._gradient_cb = _NoScrollComboBox()
        GRADIENT_NAMES = [
            "Grayscale", "Gradient", "Binary", "False Colors", "Rainbow", "Hot",
            "Black and White", "Viridis", "Plasma", "Inferno", "Jet", "Turbo"
        ]
        for name in GRADIENT_NAMES:
            self._gradient_cb.addItem(name)
        self._gradient_cb.setCurrentText("Gradient")
        self._gradient_cb.setStyleSheet(
            "QComboBox { padding: 3px 6px; background: #fff; "
            "border: 1px solid #ccc; border-radius: 4px; }")
        self._gradient_cb.currentIndexChanged.connect(self._on_gradient_changed)
        grad_row.addWidget(self._gradient_cb, 1)
        ll.addLayout(grad_row)

        # Contrast / Brightness / Gamma — the Image Slider's three-row block, control
        # for control. Moving a slider re-renders the previewed frame, which is a read
        # off the share, so the three of them share one debounce instead of firing on
        # every pixel of a drag.
        self._bc_debounce = QTimer(self)
        self._bc_debounce.setSingleShot(True)
        self._bc_debounce.setInterval(120)
        self._bc_debounce.timeout.connect(self._reshow_preview)

        # CONTRAST. Its Auto box is the percentile auto-stretch this tab used to show as
        # a separate "Auto stretch" checkbox — the same operation, now sitting on the row
        # it overrides, the way the Slider has always had it. Absolute scale stays the
        # default (see img_scale): one palette colour = one intensity, so frames and
        # cameras are comparable. Auto buys legibility on the dim cameras and pays with
        # that comparability, which is why it is a visible switch and off by default.
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

        # What the displayed intensities MEAN — peak in raw counts, how much of the
        # camera's full scale that is, the sensor depth, and which mapping produced the
        # picture. A dark frame is then readable as "3 % of full scale" instead of as a
        # broken render.
        self._scale_note_lbl = QLabel("")
        self._scale_note_lbl.setStyleSheet(
            "font-size: 10px; color: #666; padding: 1px 0; background: transparent;")
        self._scale_note_lbl.setWordWrap(True)
        ll.addWidget(self._scale_note_lbl)

        # ══════════════════ Group: LOG ══════════════════════════════════════
        ll = s_log.body_layout
        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setMaximumHeight(140)
        self._log_box.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        self._log_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        ll.addWidget(self._log_box)

        panel_lay.addStretch(1)
        left_scroll.setWidget(lw)

        # ════ RIGHT PANEL ═════════════════════════════════════════════════════
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        hdr_row = QHBoxLayout()
        self._result_lbl = QLabel("Results")
        self._result_lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        hdr_row.addWidget(self._result_lbl)
        hdr_row.addStretch(1)
        self._btn_clear_results = QPushButton("🗑  Clear table")
        self._btn_clear_results.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        self._btn_clear_results.setToolTip(
            "Empty the results table and drop its camera tabs.\n"
            "Time window, PV list and camera selection are kept.")
        self._btn_clear_results.clicked.connect(self._clear_results)
        hdr_row.addWidget(self._btn_clear_results)
        rl.addLayout(hdr_row)

        # One results tab per searched camera, each holding that camera's day rows.
        # With a single camera the tab bar is hidden, so the common case looks exactly
        # like the plain table it replaced.
        self._results_tabs = QTabWidget()
        self._results_tabs.setDocumentMode(True)
        self._results_tabs.currentChanged.connect(self._on_result_tab_changed)

        # Day rows on top, the shots of ONE opened day in a shorter table underneath.
        # Both feed the SAME preview on the right — the opened day used to be a modal
        # dialog carrying its own second copy of the picture, which covered the results.
        self._results_split = QSplitter(Qt.Orientation.Vertical)
        self._results_split.setChildrenCollapsible(False)
        self._results_split.addWidget(self._results_tabs)
        self._day_panel = self._build_day_panel()
        self._results_split.addWidget(self._day_panel)
        self._results_split.setStretchFactor(0, 3)
        self._results_split.setStretchFactor(1, 2)
        self._day_panel.setVisible(False)
        rl.addWidget(self._results_split, 1)
        self._rebuild_result_tabs([None])

        root_layout.addWidget(left_scroll)
        root_layout.addWidget(rw, 1)

        # Preview panel
        self._preview_widget = _PreviewWidget()
        self._preview_widget.setMinimumHeight(200)
        root_layout.addWidget(self._preview_widget, 1)

        # Init — SBW4 is what a FIRST run starts with; a list restored from the saved
        # state wins over it. This line used to assign unconditionally, which quietly
        # threw the restored list away at the end of the build.
        if not self._pv_cfg:
            self._pv_cfg = [{"col": "sbw4", "target": 10.0, "tol": 0.0,
                             "filter": True}]
        self._rebuild_pv_rows()
        self._update_date_info()
        QTimer.singleShot(300, self._load_cameras)
        QTimer.singleShot(400, self._fetch_channel_list)

    # ── RESULT TABS (one per searched camera) ─────────────────────────────────
    #
    # A search runs over every camera in "Selected cameras", and each gets its own tab
    # holding a row per searched day. The rest of the tab works on whichever one is in
    # front: `_table` and `_day_results` resolve to the visible tab, so preview, save,
    # send-to-Slider and the shot dialog need no notion of tabs at all — and the row
    # index → result index alignment they rely on stays inside one camera.

    # ── OPENED DAY (shots of one day, under the results table) ────────────────

    def _build_day_panel(self) -> QWidget:
        """The shot list of ONE opened day, shown under the day table.

        The picture of the picked shot goes to the preview on the right — the same place
        a day row uses — so opening a day never hides the results."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(3)

        hdr = QHBoxLayout()
        self._day_panel_lbl = QLabel("")
        self._day_panel_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        hdr.addWidget(self._day_panel_lbl)
        hdr.addStretch(1)
        self._btn_day_open = QPushButton("▶ Open selected in Slider")
        self._btn_day_open.setStyleSheet(
            "QPushButton { background: #2d7dff; color: #fff; font-weight: 700; "
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; }"
            "QPushButton:hover { background: #1a6aee; }")
        self._btn_day_open.clicked.connect(self._day_open_in_slider)
        hdr.addWidget(self._btn_day_open)
        self._btn_day_save_img = QPushButton("Save image")
        self._btn_day_save_img.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        self._btn_day_save_img.clicked.connect(self._day_save_image)
        hdr.addWidget(self._btn_day_save_img)
        btn_close = QPushButton("✕")
        btn_close.setToolTip("Close the shot list")
        btn_close.setFixedWidth(24)
        btn_close.setStyleSheet("font-size: 11px; padding: 2px 0;")
        btn_close.clicked.connect(self._hide_day_panel)
        hdr.addWidget(btn_close)
        lay.addLayout(hdr)

        self._day_table = QTableWidget(0, 1)
        self._day_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._day_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._day_table.setAlternatingRowColors(True)
        self._day_table.verticalHeader().setVisible(False)
        self._day_table.selectionModel().selectionChanged.connect(self._show_day_shot)
        lay.addWidget(self._day_table, 1)
        return w

    def _hide_day_panel(self, *_a):
        """Close the shot list and forget the day it belonged to."""
        self._day_panel.setVisible(False)     # before clearing: the clear fires a
        self._day_table.setRowCount(0)        # selection change that must be ignored
        self._day_dr = None
        self._day_rows = []
        self._day_cam = None
        self._day_hour_cache = {}

    def _show_day_panel(self, dr):
        self._day_dr = dr
        self._day_rows = list(dr.rows_in_tol)
        # Camera of the SEARCH, not of the live list — the rows were found with it.
        self._day_cam = dr.cam or self._active_cam
        self._day_hour_cache = {}
        self._day_panel_lbl.setText(
            f"{dr.day} — {len(self._day_rows)} shot(s) in range"
            + (f"   ({self._day_cam})" if self._day_cam else ""))
        self._fill_day_table(dr)
        self._day_panel.setVisible(True)
        # First open gives the day rows the bigger half instead of an even split;
        # after that the user's own drag of the divider is kept.
        if not self._day_split_sized:
            h = max(self._results_split.height(), 240)
            self._results_split.setSizes([int(h * 0.6), int(h * 0.4)])
            self._day_split_sized = True
        if self._day_table.rowCount():
            self._day_table.selectRow(0)

    def _fill_day_table(self, dr):
        """Columns come from the SEARCH-TIME state stored on dr — the left panel may
        have changed since this search ran and must not affect old rows."""
        criteria = dr.criteria_csv or [{"col": dr.col, "target_csv": dr.target_csv}]
        extra_cols = [c for c in dr.extra_cols
                      if c not in {cr["col"] for cr in criteria}]

        headers = ["Prague Time"]
        # Column headers carry the short label only; the archiver channel goes in
        # the header tooltip so "SBW4" can be told apart from a HAPLS- twin.
        header_tips = [""]
        for cr in criteria:
            short = self._col_short(cr["col"])
            headers += [short, f"Δ {short}"]
            chan = self._col_channel(cr["col"])
            header_tips += [chan, chan]
        for c in extra_cols:
            headers.append(self._col_short(c))
            header_tips.append(self._col_channel(c))

        tbl = self._day_table
        tbl.setRowCount(0)
        tbl.setColumnCount(len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        for h_idx, tip in enumerate(header_tips):
            hi = tbl.horizontalHeaderItem(h_idx)
            if hi is not None and tip:
                hi.setToolTip(tip)
        hh = tbl.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for h_idx in range(len(headers)):
            hh.setSectionResizeMode(h_idx, QHeaderView.ResizeMode.ResizeToContents)

        def _row_col_value(row, cc, row_ns):
            raw = row.get(cc, "")
            if raw:
                return raw, "ok"
            return _lookup_col_value(dr.per_col, dr.col_meta, cc, row_ns,
                                     tol_s=EXTRA_COL_MATCH_TOL_S,
                                     allow_network=False)

        for row in self._day_rows:
            dt_obj = row.get("_dt")
            row_ns = row.get("_ns", 0)
            ts_str = dt_obj.strftime("%H:%M:%S.%f")[:-3] if dt_obj else "?"
            r2 = tbl.rowCount()
            tbl.insertRow(r2)
            tbl.setItem(r2, 0, QTableWidgetItem(ts_str))
            c_idx = 1
            for cr in criteria:
                cc = cr["col"]
                raw_cc, state_cc = _row_col_value(row, cc, row_ns)
                tbl.setItem(r2, c_idx, QTableWidgetItem(
                    _format_value_state(cc, raw_cc, state_cc)))
                try:
                    diff_str = _format_diff(
                        cc, abs(_quantize_col(cc, float(raw_cc)) - cr["target_csv"]))
                except (ValueError, TypeError):
                    diff_str = (cpva.PV_TEXT_ERROR if state_cc == "error"
                                else cpva.PV_TEXT_NOT_FOUND)
                tbl.setItem(r2, c_idx + 1, QTableWidgetItem(diff_str))
                c_idx += 2
            for ec in extra_cols:
                raw_ec, state_ec = _row_col_value(row, ec, row_ns)
                tbl.setItem(r2, c_idx, QTableWidgetItem(
                    _format_value_state(ec, raw_ec, state_ec)))
                c_idx += 1

    def _day_selected_rows(self) -> "list[dict]":
        idxs = sorted(set(i.row() for i in self._day_table.selectedIndexes()))
        return [self._day_rows[i] for i in idxs if i < len(self._day_rows)]

    def _show_day_shot(self, *_a):
        """Picked shot of the opened day → the panel preview on the right."""
        dr = self._day_dr
        # isHidden(), not isVisible(): the tab itself is invisible whenever another
        # Image Tools tab is in front, and a redraw asked for then still belongs to the
        # opened day.
        if dr is None or self._day_panel.isHidden():
            return
        rows = self._day_selected_rows()
        if not rows:
            return
        row = rows[0]
        dt_obj = row.get("_dt")
        if dt_obj is None:
            return
        cam = self._day_cam
        # Full multi-PV caption (search + also-show), same as a day row
        energy = self._build_energy_text(dr, row, row.get("_ns"))
        self._preview_gen += 1
        gen = self._preview_gen
        gname = self._gradient_cb.currentText()
        auto, gam, con, off = self._bc_args()
        ns = row.get("_ns")
        cache = self._day_hour_cache
        if not cam:
            self._log("No camera for these results — nothing to preview.")
            self._current_preview_path = None
            self._on_preview_ready(None, "", gen)
            return

        def _resolve_and_load():
            # Hour folders are resolved per shot on a worker thread: the shots in range
            # span the whole day, and probing an SMB folder on the UI thread freezes it.
            img = self._find_image_for_shot(dr, cam, dt_obj, ns, cache)
            if gen != self._preview_gen:
                return
            if img is None:
                self._preview_sig.log_msg.emit(
                    f"⚠ no image within {IMG_MATCH_TOL_NS/1e9:.0f}s for "
                    f"{dt_obj:%Y-%m-%d %H:%M:%S} ({cam})")
                self._preview_sig.show.emit(None, "", gen)
                return
            self._current_preview_path = img
            self._load_and_show_preview(img, energy, gen, gname, auto, gam, con, off)

        threading.Thread(target=_resolve_and_load, daemon=True).start()

    def _day_save_image(self):
        dr = self._day_dr
        if dr is None:
            return
        rows = self._day_selected_rows()
        if not rows:
            QMessageBox.information(self, "No selection", "Select a shot in the list.")
            return
        cam = self._day_cam
        if not cam:
            QMessageBox.warning(self, "No camera", "No camera folder for these results.")
            return
        dt_obj = rows[0].get("_dt")
        if dt_obj is None:
            QMessageBox.warning(self, "No timestamp", "This shot has no timestamp.")
            return
        src = self._find_image_for_shot(dr, cam, dt_obj, rows[0].get("_ns"),
                                        self._day_hour_cache)
        if src is None:
            QMessageBox.warning(self, "Image not found", "No image found for this shot.")
            return
        initial_dir = str(self._last_save_dir) if self._last_save_dir else str(Path.home())
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save image", str(Path(initial_dir) / src.name),
            "Images (*.png *.jpg *.tif *.tiff *.bmp);;All files (*)",
            options=QFileDialog.Option.DontUseNativeDialog)
        if not dst:
            return
        dst_path = Path(dst)
        self._last_save_dir = dst_path.parent
        try:
            shutil.copy2(src, dst_path)
        except Exception as ex:
            QMessageBox.critical(self, "Error", f"Copy failed:\n{ex}")

    def _day_open_in_slider(self):
        """Send the picked shots (or the whole day when nothing is picked) to the
        Image Slider tab as a discrete folder."""
        dr = self._day_dr
        if dr is None:
            return
        sel_rows = self._day_selected_rows() or list(self._day_rows)
        cam = self._day_cam
        if not cam:
            QMessageBox.warning(self, "No camera", "Select a camera first.")
            return

        files = []
        for row in sel_rows:
            dt_obj = row.get("_dt")
            if dt_obj is None:
                continue
            # Per-shot lookup: the shots in range span the whole day, not just
            # the hour folder of the day's best shot.
            img = self._find_image_for_shot(dr, cam, dt_obj, row.get("_ns"),
                                            self._day_hour_cache)
            if img:
                files.append((img, row))

        if not files:
            QMessageBox.warning(self, "No images", "No matching images found.")
            return

        prev_temp_dir = self._temp_dir
        self._temp_dir = tempfile.mkdtemp(prefix="SF_slider_")
        temp_path = Path(self._temp_dir)

        copied = 0
        energy_map: dict[str, str] = {}
        for src, row in files:
            try:
                dst = temp_path / src.name
                if dst.exists():
                    dst = temp_path / f"{src.stem}_{copied}{src.suffix}"
                shutil.copy2(src, dst)
                energy_map[dst.name] = self._build_energy_text(
                    dr, row, row.get("_ns"))
                copied += 1
            except Exception as e:
                self._log(f"Copy error: {e}")

        if copied == 0:
            QMessageBox.warning(self, "Copy failed", "No images copied.")
            return

        if self._tab_widget:
            self._tab_widget.setCurrentIndex(1)
        if self._slider_ref:
            recv = getattr(self._slider_ref, "receive_external_folder", None)
            if callable(recv):
                recv(temp_path, energy_map=energy_map, discrete=True, cam_name=cam)
            else:
                self._slider_ref._sf_energy_map = energy_map
                self._slider_ref.open_folder_path(temp_path)
        if prev_temp_dir:
            try:
                shutil.rmtree(prev_temp_dir, ignore_errors=True)
            except Exception:
                pass

    def _make_results_table(self) -> QTableWidget:
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels([
            "Date", "Prague Time", "PV", "Value", "Δ from target", "Status", "Folder"
        ])
        hh = table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for c, mode in enumerate([
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.Stretch,
        ]):
            hh.setSectionResizeMode(c, mode)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.selectionModel().selectionChanged.connect(self._on_main_selection_changed)
        table.doubleClicked.connect(self._on_table_double_clicked)
        table.cellClicked.connect(self._on_table_cell_clicked)
        return table

    def _rebuild_result_tabs(self, cams: "list"):
        """One empty tab per camera of the search about to run (None = no camera)."""
        # The opened day belongs to rows that are about to be thrown away.
        self._hide_day_panel()
        self._results_tabs.blockSignals(True)
        while self._results_tabs.count():
            w = self._results_tabs.widget(0)
            self._results_tabs.removeTab(0)
            # Cut the old table loose first: it is destroyed later by the event loop,
            # and a selection signal arriving from a table that no longer has a tab
            # would be answered with the NEW tab's rows.
            try:
                w.selectionModel().selectionChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            w.deleteLater()
        self._cam_tables = {}
        self._cam_results = {}
        self._cam_order = list(cams) if cams else [None]
        for cam in self._cam_order:
            table = self._make_results_table()
            self._cam_tables[cam] = table
            self._cam_results[cam] = []
            label = _clean_cam_for_filename(cam) if cam else "no camera"
            idx = self._results_tabs.addTab(table, label)
            self._results_tabs.setTabToolTip(idx, cam or "No camera selected")
        self._results_tabs.tabBar().setVisible(len(self._cam_order) > 1)
        self._results_tabs.setCurrentIndex(0)
        self._results_tabs.blockSignals(False)

    def _clear_results(self):
        """Empty the results table and go back to a single, camera-less tab.

        A finished search leaves one tab per camera it ran with, and every export
        button, the preview and the shot dialog read the tab in front -- so the only way
        back to a clean table used to be running another search."""
        if self._search_running:
            return
        self._rebuild_result_tabs([None])
        self._result_lbl.setText("Results")
        # Drop the picture with the rows it belonged to, or it keeps describing a shot
        # that is no longer in the table.
        self._preview_gen += 1
        self._current_preview_path = None
        self._on_preview_ready(None, "", self._preview_gen)
        self._sync_export_buttons()
        self._log("Results table cleared.")

    def _current_cam_key(self):
        """The camera whose tab is in front."""
        idx = self._results_tabs.currentIndex()
        if 0 <= idx < len(self._cam_order):
            return self._cam_order[idx]
        return self._cam_order[0] if self._cam_order else None

    @property
    def _table(self) -> QTableWidget:
        return self._cam_tables[self._current_cam_key()]

    @property
    def _day_results(self) -> "list[_DayResult]":
        return self._cam_results[self._current_cam_key()]

    def _all_results(self) -> "list[_DayResult]":
        return [dr for cam in self._cam_order for dr in self._cam_results[cam]]

    def _on_result_tab_changed(self, _idx: int):
        # Another camera, another set of rows — the preview belongs to the row that is
        # selected HERE, and the send/save buttons to what THIS tab holds. The opened
        # day belongs to the tab we are leaving, so it goes with it.
        self._hide_day_panel()
        self._on_selection_changed()

    def _on_main_selection_changed(self):
        """A day row was picked in the results table.

        The shot list underneath belongs to the day that was opened, so picking another
        row (or clearing the selection) closes it instead of leaving it describing a day
        that is no longer selected."""
        self._hide_day_panel()
        self._on_selection_changed()

    def _refresh_preview(self):
        """Re-render what is on screen with the current display settings: the shot
        picked in the opened day list, otherwise the selected day row."""
        if not self._day_panel.isHidden() and self._day_table.selectedIndexes():
            self._show_day_shot()
            return
        if self._table.selectedIndexes():
            self._on_selection_changed()

    # ── EVENTS ────────────────────────────────────────────────────────────────

    def _on_date_changed(self):
        self._update_date_info()
        self._load_cameras()

    # ── PV column helpers ─────────────────────────────────────────────────
    def _col_label(self, col: str) -> str:
        """A name typed in the Rename dialog wins over the preset label — wanting to
        see a different name is the only reason that dialog would ever be used."""
        return self._custom_labels.get(col) or PV_COLUMNS.get(col, col)

    def _col_short(self, col: str) -> str:
        return self._col_label(col).split(" [")[0]

    def _col_channel(self, col: str) -> str:
        """The archiver channel the column actually reads.

        Preset labels ("SBW4 [J]") hide which beamline the channel belongs to —
        SBW4 is L3-SBW4-PM311:Energy, PTM1 is a HAPLS- name — so every place the
        user points at a PV must be able to show this name.  Arbitrary typed-in
        channels are their own name."""
        return CPVA_CHANNEL_MAP.get(col, col)

    def _col_unit(self, col: str) -> str:
        if col in MJ_COLUMNS:
            return "mJ"
        if col == "waveplate":
            return "—"
        if col in PV_COLUMNS:
            return "J"
        return ""   # arbitrary channel — no assumed unit

    # ── PV search bar / suggestions ───────────────────────────────────────
    def _rebuild_pv_suggestions(self):
        """Build the (display, col_key) suggestion list: presets first, then
        any fetched archiver channels not already covered by a preset."""
        seen_channels = set()
        sugg: list[tuple[str, str]] = []
        for col, label in PV_COLUMNS.items():
            sugg.append((label, col))
            ch = CPVA_CHANNEL_MAP.get(col)
            if ch:
                seen_channels.add(ch)
        for ch in self._all_pv_channels:
            if ch in seen_channels:
                continue
            sugg.append((ch, ch))
        self._pv_suggestions = sugg

    def _fetch_channel_list(self):
        if self._all_pv_channels or self._chan_loading:
            return
        self._chan_loading = True
        sig = self._chan_sig = _ChannelSignals()
        sig.loaded.connect(self._on_channels_loaded)

        def worker():
            try:
                chans = _cpva_fetch_channels("**")
            except Exception as exc:
                self._chan_err = f"{type(exc).__name__}: {exc}"
                chans = []
            sig.loaded.emit(chans)

        self._chan_err = ""
        threading.Thread(target=worker, daemon=True).start()

    def _on_channels_loaded(self, chans: list):
        self._chan_loading = False
        self._all_pv_channels = list(chans)
        self._rebuild_pv_suggestions()
        if chans:
            self._log(f"PV channels available: {len(chans)}")
            # A query typed while the list was downloading matched presets only;
            # redo it now that all channels are known.
            if self._pv_search.text().strip():
                self._on_pv_search_changed(self._pv_search.text())
        else:
            self._log(f"PV channel list unavailable ({self._chan_err or 'empty'}); "
                      "presets + free-typed channels still work.")

    def _make_pv_dropdown(self, callback):
        """Floating, focus-free filtered list — same pattern as the camera search."""
        dd = QTableWidget(0, 1)
        dd.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        dd.horizontalHeader().setVisible(False)
        dd.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        dd.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        dd.verticalHeader().setVisible(False)
        dd.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        dd.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dd.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        dd.clicked.connect(callback)
        dd.setStyleSheet(
            "QTableWidget { border: 1px solid #2d7dff; background: #fff; }"
            "QTableWidget::item:selected { background: #2d7dff; color: #fff; }")
        return dd

    _PV_DROPDOWN_MAX = 200

    # Query parsing + ranking are shared with the Image Slider's PV picker; these
    # stay as thin aliases so the existing call sites read unchanged.
    _split_query     = staticmethod(cpva.split_query)
    _tokens_in_order = staticmethod(cpva.tokens_in_order)
    _rank_pv_match   = staticmethod(cpva.rank_pv_match)

    def _populate_pv_dropdown(self, text, dropdown, anchor, exclude):
        tokens = self._split_query(text)
        dropdown.hide()
        dropdown.setRowCount(0)
        if not tokens:
            return
        # The startup fetch may have failed or may still be in flight — retry so
        # the search isn't stuck on presets for the rest of the session.
        if not self._all_pv_channels:
            self._fetch_channel_list()
        scored = []
        for i, (disp, key) in enumerate(self._pv_suggestions):
            if key in exclude:
                continue
            s = self._rank_pv_match(disp, key, tokens)
            if s is not None:
                scored.append((s, i, disp, key))
        scored.sort(key=lambda t: (t[0], t[1]))
        shown = scored[:self._PV_DROPDOWN_MAX]

        def _hint_row(txt: str) -> QTableWidgetItem:
            r = dropdown.rowCount()
            dropdown.insertRow(r)
            it = QTableWidgetItem(txt)
            it.setForeground(QColor("#777"))
            dropdown.setItem(r, 0, it)
            return it

        for _s, _i, disp, key in shown:
            r = dropdown.rowCount()
            dropdown.insertRow(r)
            it = QTableWidgetItem(disp)
            # Presets are listed under a short label ("SBW4 [J]") that does not
            # say which beamline the channel is on — show the archiver name.
            chan = self._col_channel(key)
            if chan != disp:
                it.setToolTip(chan)
            it.setData(Qt.ItemDataRole.UserRole, key)
            dropdown.setItem(r, 0, it)
        if len(scored) > len(shown):
            # No UserRole → _on_pv_dropdown_clicked ignores this hint row.
            _hint_row(f"… {len(scored) - len(shown)} more matches — refine the search")

        # "Add by name": a single bare word is offered as an archiver channel name of
        # its own, so a PV the fetched list does not carry — or any PV at all while the
        # list is down — can still be added. Same rule Enter follows: "023 l3" is a
        # query, not a name, and is not offered.
        literal = (text or "").strip()
        if (len(tokens) != 1 or tokens[0] != literal.lower() or literal in exclude
                or any(k == literal for _s, _i, _d, k in shown)):
            literal = ""
        if literal:
            it = _hint_row(f'➕  add "{literal}" as an archiver channel name')
            it.setForeground(QColor("#1a6aee"))
            it.setToolTip("Takes the typed text as the channel name itself — for a PV "
                          "that is not in the fetched list.")
            it.setData(Qt.ItemDataRole.UserRole, literal)

        if not dropdown.rowCount():
            if not self._chan_loading:
                return
            # Without this the box looks empty-and-final while the ~9700 channel
            # list is still downloading; _on_channels_loaded re-runs the query when
            # it lands.
            _hint_row("loading PV list…")
        row_h = max(22, dropdown.verticalHeader().defaultSectionSize())
        popup_h = min(dropdown.rowCount() * row_h + 6, 360)
        popup_w = max(300, anchor.width() + 20)
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        dropdown.setGeometry(pos.x(), pos.y(), popup_w, popup_h)
        dropdown.show()
        dropdown.raise_()
        QTimer.singleShot(0, anchor.setFocus)

    def _on_pv_search_changed(self, text: str):
        self._populate_pv_dropdown(text, self._pv_dropdown, self._pv_search,
                                   {c["col"] for c in self._pv_cfg})

    def _on_pv_dropdown_clicked(self, index):
        it = self._pv_dropdown.item(index.row(), 0)
        if it is None:
            return
        key = it.data(Qt.ItemDataRole.UserRole)
        if not key:
            return          # the "… N more matches" hint row
        self._pv_dropdown.hide()
        self._pv_search.clear()
        self._add_pv_col(key)

    def _best_pv_match(self, tokens) -> "str | None":
        """Channel key of the top-ranked suggestion for `tokens`, or None."""
        exclude = {c["col"] for c in self._pv_cfg}
        best = None
        for i, (disp, key) in enumerate(self._pv_suggestions):
            if key in exclude:
                continue
            s = self._rank_pv_match(disp, key, tokens)
            if s is not None and (best is None or (s, i) < best[0]):
                best = ((s, i), key)
        return best[1] if best else None

    def _on_pv_search_return(self):
        txt = self._pv_search.text().strip()
        if not txt:
            return
        tokens = self._split_query(txt)
        if not tokens:
            return
        # A single bare word is still taken literally, so a channel that is not
        # in the fetched list can be typed in by hand. "023 l3" / "*023*l3*" is a
        # query though — Enter takes its best match instead of registering a PV
        # by that name, which could never return data.
        if len(tokens) != 1 or tokens[0] != txt.lower():
            hit = self._best_pv_match(tokens)
            if hit is None:
                return
            txt = hit
        self._pv_dropdown.hide()
        self._pv_search.clear()
        self._add_pv_col(txt)

    def _preset_for_channel(self, channel: str) -> "str | None":
        """The preset column that already reads `channel`, if there is one. A channel
        name typed in full must not become a second column reading exactly what a
        preset reads — two rows, one number, and only one of them carrying the
        preset's unit."""
        return next((c for c in PV_COLUMNS
                     if CPVA_CHANNEL_MAP.get(c, c) == channel), None)

    def _register_col(self, col: str) -> str:
        col = (col or "").strip()
        preset = self._preset_for_channel(col)
        if preset is not None:
            return preset
        if col and col not in PV_COLUMNS and col not in self._custom_labels:
            self._custom_labels[col] = col   # arbitrary channel; label == name
        return col

    def _add_pv_col(self, col: str, filter_by: bool = True):
        """Add a PV to the list. New PVs are filter PVs by default — that is what
        the search box is normally used for; untick the row to only show it."""
        col = self._register_col(col)
        if not col:
            return
        if any(c["col"] == col for c in self._pv_cfg):
            # Silence here read as "the click did nothing" — most often after typing
            # a channel name that a preset already covers.
            self._log(f"{self._col_short(col)} is already in the PV list.")
            return
        self._sync_pv_cfg_from_rows()
        self._pv_cfg.append({"col": col, "target": 10.0, "tol": 0.0,
                             "filter": bool(filter_by)})
        self._rebuild_pv_rows()
        self._save_pv_state()
        chan = self._col_channel(col)
        short = self._col_short(col)
        self._log(f"PV added: {short}" + (f"  ({chan})" if chan != short else ""))

    def _remove_pv_col(self, col: str):
        self._sync_pv_cfg_from_rows()
        self._pv_cfg = [c for c in self._pv_cfg if c["col"] != col]
        self._rebuild_pv_rows()
        self._save_pv_state()

    # ── one PV's name and channel (right-click on its row) ─────────────────
    def _on_pv_row_menu(self, col: str, pos):
        w = self.sender()
        menu = QMenu(self)
        act_ren = menu.addAction("Rename…")
        act_cpy = menu.addAction("Copy archiver channel name")
        menu.addSeparator()
        act_del = menu.addAction("Remove")
        chosen = menu.exec((w if w is not None else self).mapToGlobal(pos))
        if chosen is act_ren:
            self._rename_pv_col(col)
        elif chosen is act_cpy:
            QApplication.clipboard().setText(self._col_channel(col))
            self._log(f"Copied to clipboard: {self._col_channel(col)}")
        elif chosen is act_del:
            self._remove_pv_col(col)

    def _rename_pv_col(self, col: str):
        """Give a PV the name the operator wants to READ — a channel added by name is
        called "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy", which is unreadable in a 280 px
        row and over a picture. Display only: the channel stays the identity
        everywhere, so a rename cannot repoint a column at different data."""
        txt, ok = QInputDialog.getText(
            self, "Rename PV", f"Name shown for\n{self._col_channel(col)}",
            QLineEdit.EchoMode.Normal, self._col_label(col))
        if not ok:
            return
        txt = txt.strip()
        self._sync_pv_cfg_from_rows()
        if txt and txt != PV_COLUMNS.get(col, col):
            self._custom_labels[col] = txt
        elif col in PV_COLUMNS:
            self._custom_labels.pop(col, None)     # back to the preset label
        else:
            self._custom_labels[col] = col         # a channel is its own name
        self._rebuild_pv_rows()
        self._save_pv_state()

    # ── saved PV list ─────────────────────────────────────────────────────
    # The PV list is the one thing in this tab that is typed rather than picked off
    # what is already on screen, so it is the one thing worth surviving a restart —
    # the same reason the Slider persists its PV selection.
    _UI_STATE_PATH = (Path(os.environ.get("APPDATA", Path.home()))
                      / "ELI_ImageTools" / "shotfinder_ui_state.json")

    def _read_ui_state_file(self) -> dict:
        try:
            data = json.loads(self._UI_STATE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_ui_state_file(self, updates: dict):
        """Merge into the state file rather than replacing it. The PV list and the
        open/closed panel groups share one document, so a writer that rewrites the whole
        thing silently throws away the other one's half."""
        data = self._read_ui_state_file()
        data.update(updates)
        try:
            self._UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._UI_STATE_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

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

    def _load_pv_state(self):
        # The whole state document, kept so the panel can ask it which groups were
        # left open while it is being built.
        self._ui_state = self._read_ui_state_file()
        data = self._ui_state
        if not data:
            return                  # no state yet, or unreadable — start empty
        labels = data.get("pv_labels")
        if isinstance(labels, dict):
            for k, v in labels.items():
                if isinstance(k, str) and isinstance(v, str) and v.strip():
                    self._custom_labels[k] = v.strip()

        def _num(entry, key, default):
            try:
                return float(entry.get(key, default))
            except (TypeError, ValueError):
                return default

        for e in (data.get("pv_cfg") or []):
            if not isinstance(e, dict):
                continue
            col = str(e.get("col", "")).strip()
            if not col or any(c["col"] == col for c in self._pv_cfg):
                continue
            if col not in PV_COLUMNS:
                self._custom_labels.setdefault(col, col)
            # Only these four keys are read back: a state file written by the version
            # that had a per-PV "scale" must not resurrect it as a hidden factor.
            self._pv_cfg.append({"col":    col,
                                 "target": _num(e, "target", 10.0),
                                 "tol":    _num(e, "tol", 0.0),
                                 "filter": bool(e.get("filter", True))})

    def _save_pv_state(self):
        # A remembered list is a convenience, never a must — _write_ui_state_file
        # swallows a failed write.
        self._write_ui_state_file(
            {"pv_cfg": [dict(c) for c in self._pv_cfg],
             # Only names that differ from the PV's own: storing a label equal to
             # the preset label would freeze today's wording into the saved state.
             "pv_labels": {k: v for k, v in self._custom_labels.items()
                           if v and v != k and v != PV_COLUMNS.get(k)}})

    def _make_remove_btn(self, slot) -> QPushButton:
        btn = QPushButton("✕")
        btn.setFixedSize(26, 26)
        btn.setToolTip("Remove")
        btn.setStyleSheet(
            "QPushButton { color: #cc0000; font-weight: 700; font-size: 15px; "
            "border: none; padding: 0; }"
            "QPushButton:hover { background: #ffd6d6; border-radius: 3px; }")
        btn.clicked.connect(slot)
        return btn

    def _sync_pv_cfg_from_rows(self):
        """Copy live widget values back into _pv_cfg (the single source of truth)."""
        for r in getattr(self, "_pv_rows", []):
            cfg = r["cfg"]
            cfg["target"] = r["target_sb"].value()
            cfg["tol"]    = r["tol_sb"].value()
            cfg["filter"] = r["chk"].isChecked()

    # Text budget for the PV name inside one row, in px: with the target/tol
    # spinboxes shown the name gets what is left of the 280 px panel; unticked
    # rows hide them and the name may run wide. Longer names are elided in the
    # middle — the full archiver name stays in the tooltip.
    _PV_NAME_W_FILTER = 68
    _PV_NAME_W_SHOW   = 186

    def _rebuild_pv_rows(self):
        """Rebuild one single-line row per entry in self._pv_cfg:

            [✓] PV NAME   T:<target> ±<tol> <unit>  ✕

        The tick marks a PV the search filters on; unticked hides the target/tol
        spinboxes (the name then gets their space) and the PV is only reported in
        the results table + image caption."""
        from PySide6.QtGui import QFontMetrics

        while self._pv_container_layout.count():
            item = self._pv_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self._pv_rows = []
        for cfg in self._pv_cfg:
            col = cfg["col"]
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(2)

            chk = QCheckBox()
            chk.setStyleSheet(_CHECKBOX_STYLE_SM)
            chk.setChecked(bool(cfg["filter"]))
            full_label = self._col_label(col)
            short_label = self._col_short(col)
            # Elide against the font the checkbox is actually PAINTED with:
            # _CHECKBOX_STYLE_SM sets font-size:10px, and a stylesheet font wins
            # over the widget font, so QFontMetrics(chk.font()) measured a larger
            # font than the label renders in and cut names far shorter than needed.
            name_font = chk.font()
            name_font.setPixelSize(_PV_NAME_FONT_PX)
            fm = QFontMetrics(name_font)
            channel = self._col_channel(col)
            chan_line = (f"\nArchiver channel: {channel}"
                         if channel != full_label else "")
            chk.setToolTip(
                f"{full_label}{chan_line}\n"
                "Ticked: search filters on this PV (target ± tolerance).\n"
                "Unticked: value is only shown in the results and on the images.\n"
                "Right-click: rename, copy channel name, remove.")
            # The menu is hooked to the tick box as well as to the row: the box covers
            # most of the row, and a policy set on the parent alone never sees the
            # child's context-menu event.
            for _w in (row_w, chk):
                _w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                _w.customContextMenuRequested.connect(
                    lambda pos, c=col: self._on_pv_row_menu(c, pos))
            row_l.addWidget(chk)
            row_l.addStretch(1)

            tgt_w = QWidget()
            tgt_l = QHBoxLayout(tgt_w)
            tgt_l.setContentsMargins(0, 0, 0, 0)
            tgt_l.setSpacing(2)
            t_lbl = QLabel("T:")
            t_lbl.setStyleSheet("font-size: 10px;")
            tgt_l.addWidget(t_lbl)
            t_sb = QDoubleSpinBox()
            t_sb.setRange(-1e9, 1e9)
            t_sb.setDecimals(3)
            t_sb.setValue(cfg.get("target", 10.0))
            t_sb.setFixedWidth(56)
            t_sb.setToolTip(f"Target value for {full_label}\n{channel}")
            tgt_l.addWidget(t_sb)
            pm_lbl = QLabel("±")
            pm_lbl.setStyleSheet("font-size: 10px;")
            tgt_l.addWidget(pm_lbl)
            tol_sb = QDoubleSpinBox()
            tol_sb.setRange(0.0, 1e9)
            tol_sb.setDecimals(3)
            tol_sb.setValue(cfg.get("tol", 0.0))
            tol_sb.setFixedWidth(50)
            tol_sb.setToolTip(
                f"Tolerance around the target for {full_label}\n{channel}")
            tgt_l.addWidget(tol_sb)
            unit = self._col_unit(col)
            if unit:
                u_lbl = QLabel(unit)
                u_lbl.setStyleSheet("font-size: 10px;")
                tgt_l.addWidget(u_lbl)
            row_l.addWidget(tgt_w)
            row_l.addWidget(self._make_remove_btn(
                lambda _=False, c=col: self._remove_pv_col(c)))

            def _apply_filter_state(checked, _cfg=cfg, _tw=tgt_w, _chk=chk,
                                    _fm=fm, _txt=short_label):
                _cfg["filter"] = bool(checked)
                _tw.setVisible(bool(checked))
                budget = (self._PV_NAME_W_FILTER if checked
                          else self._PV_NAME_W_SHOW)
                _chk.setText(_fm.elidedText(
                    _txt, Qt.TextElideMode.ElideMiddle, budget))

            chk.toggled.connect(_apply_filter_state)
            _apply_filter_state(chk.isChecked())

            self._pv_container_layout.addWidget(row_w)
            self._pv_rows.append({"cfg": cfg, "col": col, "chk": chk,
                                  "target_sb": t_sb, "tol_sb": tol_sb})

    def _filter_cols(self) -> "list[str]":
        """PVs the search filters on (ticked), in pick order."""
        self._sync_pv_cfg_from_rows()
        return [c["col"] for c in self._pv_cfg if c["filter"]]

    def _show_cols(self) -> "list[str]":
        """PVs that are only displayed (unticked), in pick order."""
        self._sync_pv_cfg_from_rows()
        return [c["col"] for c in self._pv_cfg if not c["filter"]]

    def _get_criteria(self) -> "list[dict]":
        """Current filter criteria as [{col, target, tol}, …]."""
        self._sync_pv_cfg_from_rows()
        return [{"col": c["col"], "target": c["target"], "tol": c["tol"]}
                for c in self._pv_cfg if c["filter"]]

    def _update_date_info(self):
        days = self._selected_days()
        n = len(days)
        fmt = "%Y-%m-%d %H:%M"
        start_s = self._tw_start.strftime(fmt) if hasattr(self._tw_start, "strftime") else "?"
        end_s   = self._tw_end.strftime(fmt)   if hasattr(self._tw_end,   "strftime") else "?"
        if n == 0:
            self._date_info_lbl.setText(f"⚠ End before start\n{start_s}\n→ {end_s}")
        elif n == 1:
            self._date_info_lbl.setText(f"1 day  {start_s}\n→ {end_s}")
        else:
            self._date_info_lbl.setText(f"{n} days  {start_s}\n→ {end_s}")

    def _build_energy_text(self, dr, row: dict, row_ns) -> str:
        """Multi-PV preview caption: search PVs + 'also show' PVs at this shot.
        Used by both the main-table preview and the per-shot dialog preview so
        an expanded shot keeps the same backreflection / extra-PV info.
        Columns come from dr (search-time state), not the live left panel."""
        search_cols = list(dr.search_cols)
        extra_cols = [c for c in dr.extra_cols if c not in search_cols]
        parts = []
        for sc in search_cols + extra_cols:
            raw, state = row.get(sc, ""), "ok"
            if not raw and row_ns is not None:
                raw, state = _lookup_col_value(dr.per_col, dr.col_meta, sc, row_ns,
                                               tol_s=EXTRA_COL_MATCH_TOL_S,
                                               allow_network=False)
            parts.append(f"{self._col_short(sc)}: {_format_value_state(sc, raw, state)}")
        return "  |  ".join(parts)

    def _find_image_for_shot(self, dr, cam: str, dt_obj, ts_ns,
                             hour_cache: dict) -> Path | None:
        """Image belonging to ONE shot of a day result — see _find_image_in_day.

        dr.hour_folder is the hour of that day's best shot only, while the shots in range
        are spread over the whole day, so the day folder is taken from it and the hour is
        resolved per shot. Using the SEARCH-TIME folder also means a later change of the
        image source cannot send old rows to another share."""
        day_dir = dr.hour_folder.parent if dr.hour_folder is not None else None
        return _find_image_in_day(dr.day, cam, dt_obj, ts_ns, hour_cache,
                                  images_root=self._images_root, day_dir=day_dir)[0]

    def _sync_export_buttons(self):
        """Send / save / Workshop act on the tab in front, so they follow ITS rows.

        Sending works with no selection too (it then sends every row of the tab), so
        they must not go dead the moment a selection is cleared — only when the tab
        holds no picture at all."""
        has_img = any(dr.status == "ok" for dr in self._day_results)
        self._btn_open_slider.setEnabled(
            has_img and self._slider_ref is not None and self._tab_widget is not None)
        self._btn_save_results.setEnabled(has_img)
        self._btn_send_workshop.setEnabled(has_img)
        self._btn_send_workshop.setVisible(has_img)

    def _on_selection_changed(self):
        self._sync_export_buttons()

        # Preview při kliknutí na řádek
        def _clear_preview():
            # Leaving the last picture up makes a row (or a camera tab) with no image
            # of its own look like it has one.
            self._preview_gen += 1
            self._current_preview_path = None
            self._on_preview_ready(None, "", self._preview_gen)

        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not rows or not self._day_results:
            _clear_preview()
            return
        r = rows[0]
        if r >= len(self._day_results):
            _clear_preview()
            return
        dr = self._day_results[r]
        cam = dr.cam or self._active_cam
        dt_obj = dr.best_row.get("_dt")
        if dr.status != "ok" or not cam or dr.hour_folder is None or dt_obj is None:
            _clear_preview()
            return
        ts_ns_direct = dr.best_row.get("_ns")
        self._preview_gen += 1
        gen = self._preview_gen
        gradient_name = self._gradient_cb.currentText()
        auto, gamma, contrast, offset = self._bc_args()

        def _resolve_and_load():
            # Folder probing + _find_image_for_ts (os.scandir over SMB) used to
            # run on the UI thread — a row click froze the GUI on a slow share.
            if dr.img_path is not None:
                img = Path(dr.img_path)
            else:
                cam_folder = dr.hour_folder / cam
                if not cam_folder.exists():
                    try:
                        for sub in dr.hour_folder.iterdir():
                            if sub.is_dir() and sub.name.lower() == cam.lower():
                                cam_folder = sub
                                break
                    except Exception:
                        return
                img = _find_image_for_ts(cam_folder, dt_obj, ts_ns_override=ts_ns_direct)
            if gen != self._preview_gen:
                return
            if img is None:
                self._preview_sig.log_msg.emit(
                    f"⚠ no image within {IMG_MATCH_TOL_NS/1e9:.0f}s in "
                    f"{cam_folder} for {dt_obj}")
                self._preview_sig.show.emit(None, "", gen)
                return
            energy_text = self._build_energy_text(dr, dr.best_row, ts_ns_direct)
            self._current_preview_path = img
            self._load_and_show_preview(img, energy_text, gen, gradient_name, auto,
                                        gamma, contrast, offset)

        threading.Thread(target=_resolve_and_load, daemon=True).start()

    def _load_and_show_preview(self, img_path: Path, energy_text: str, gen: int,
                               gradient_name: str = "", auto: bool = False,
                               gamma=None, contrast: int = 0, offset: int = 0):
        """Background thread: load and process image into QImage; QPixmap conversion on main thread.

        The display settings are passed in, not read from the widgets: this runs on a
        worker thread (see _bc_args)."""
        if gen != self._preview_gen:
            return
        try:
            from PySide6.QtGui import QImage
            from PIL import Image as _PilImg
            import numpy as _np

            pil = _PilImg.open(str(img_path))
            if pil.mode in ("I", "I;16"):
                arr_f = _np.array(pil, dtype=_np.float32)
            elif pil.mode in ("RGB", "RGBA"):
                arr_f = _np.array(pil.convert("L"), dtype=_np.float32)
            else:
                arr_f = _np.array(pil.convert("L"), dtype=_np.float32)

            full_scale = _full_scale_for_mode(pil.mode, img_path, pil.info)
            # `bc_out` comes back with what the Auto passes actually applied, so the
            # greyed-out sliders can be parked on it and Auto gamma costs no second
            # median pass over the frame.
            bc_out: dict = {}
            arr = _render_u8(arr_f, auto, full_scale, gamma, contrast, offset, bc_out)
            self._bc_applied = bc_out
            g_applied = (bc_out.get("gamma")
                         if (not auto and img_scale.is_auto_gamma(gamma)) else None)
            # pil.info, not a second open of the same file: a share read is 130–160 ms.
            # Branch on the MODE, not on full_scale: a 16-bit frame shown on its camera's
            # reference range has a full_scale of its own and is not an 8-bit source.
            self._scale_note = (
                img_scale.meta_from_info(pil.info, arr_f).scale_note(
                    auto, gamma, g_applied,
                    img_scale.current_reference_bits(img_scale.camera_from_path(img_path)),
                    contrast, offset)
                if pil.mode in ("I", "I;16")
                else f"8-bit source  ·  {'auto stretch' if auto else 'absolute scale'}")

            w, h = arr.shape[1], arr.shape[0]

            try:
                _is_mod = _sys.modules.get("image_slider")
                if _is_mod and hasattr(_is_mod, "GRADIENTS"):
                    lut = _is_mod.GRADIENTS.get(gradient_name)
                else:
                    lut = SF_GRADIENTS.get(gradient_name)
            except Exception:
                lut = SF_GRADIENTS.get(gradient_name, None)

            if lut is not None:
                rgb = _lut_pixels_sf(lut, arr, gradient_name)
                out_img = QImage(rgb.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
            else:
                out_img = QImage(arr.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)

            if gen != self._preview_gen:
                return
            self._preview_sig.show.emit(out_img, energy_text, gen)
        except Exception as exc:
            self._preview_sig.log_msg.emit(
                f"⚠ preview load failed for {Path(img_path).name}: "
                f"{type(exc).__name__}: {exc}")
            self._preview_sig.show.emit(None, energy_text, gen)

    def _rescale_preview(self):
        if self._preview_pixmap_orig is None or self._preview_pixmap_orig.isNull():
            return
        self._preview_widget.set_pixmap(self._preview_pixmap_orig)

    def _on_gradient_changed(self):
        self._refresh_preview()

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
        passed into the loader; never read a widget from a worker.

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

    def _reshow_preview(self):
        self._refresh_preview()

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
        self._reshow_preview()

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
        self._reshow_preview()

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
        self._reshow_preview()

    # ── DATE HELPERS ──────────────────────────────────────────────────────────

    def _qdate_to_date(self, qd: QDate) -> date:
        return date(qd.year(), qd.month(), qd.day())

    def _open_time_window(self):
        dlg = _TimeWindowDialog(self._tw_start, self._tw_end, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._tw_start, self._tw_end = dlg.selected_range()
            self._on_date_changed()

    def _selected_days(self) -> list[date]:
        d_from = self._tw_start.date() if hasattr(self._tw_start, "date") else self._tw_start
        d_to   = self._tw_end.date()   if hasattr(self._tw_end,   "date") else self._tw_end
        if d_to < d_from:
            return []
        days = []
        d = d_from
        while d <= d_to:
            days.append(d)
            d += timedelta(days=1)
        return days

    # ── CAMERA LOADING ────────────────────────────────────────────────────────

    def _load_cameras(self):
        """Camera list for the picked Time window: the union over the days in it.

        Only the FIRST day of the range used to be scanned. A long range that begins on
        a weekend, a shutdown day or any day the archiver wrote nothing then gave "No
        cameras found", and the camera search box matched nothing at all -- the range was
        perfectly searchable, but no camera could be added to it. Days are scanned newest
        first (the ones most likely to hold data) and the scan stops once
        CAM_SCAN_DAYS_WITH_DATA of them have answered, so a 48-day range costs no more
        share traffic than a couple of days."""
        days = self._selected_days()
        if not days:
            return
        scan_days = list(reversed(days))[:CAM_SCAN_MAX_DAYS]
        images_root = self._images_root
        # A range change while a scan is in flight must not be answered by the old scan.
        self._cam_load_gen = getattr(self, "_cam_load_gen", 0) + 1
        gen = self._cam_load_gen
        self._cam_scan_days = len(scan_days)
        self._cam_loading = True
        # What this scan covers, so a retry cannot re-walk a window that already
        # answered (see _open_camera_picker).
        self._cam_scan_pending_key = (str(images_root), scan_days[0], scan_days[-1])
        if hasattr(self, "_cam_status_lbl"):
            self._cam_status_lbl.setText("Loading cameras…")

        sig = self._sig_cam = _CamLoadSignals()
        sig.finished.connect(self._on_cameras_loaded)
        sig.log_msg.connect(self._log)

        def worker():
            seen: set[str] = set()
            cameras: list[str] = []
            days_with_data = 0

            def _scan_hour(hour_dir) -> list[str]:
                try:
                    if not hour_dir.exists() or not hour_dir.is_dir():
                        return []
                except Exception:
                    return []
                names: list[str] = []
                try:
                    for e in os.scandir(hour_dir):
                        if e.is_dir():
                            names.append(e.name)
                except Exception:
                    pass
                return names

            for day in scan_days:
                if gen != self._cam_load_gen:
                    return                      # superseded -- its answer is stale
                base = (_images_root_for_year(images_root, day.year)
                        / str(day.year) / str(day.month) / str(day.day))
                found_here = 0
                try:
                    with ThreadPoolExecutor(max_workers=12) as _hex:
                        _futs = [_hex.submit(_scan_hour, base / str(h))
                                 for h in range(24)]
                        for _fut in as_completed(_futs):
                            try:
                                for _name in _fut.result():
                                    found_here += 1
                                    if _name not in seen:
                                        seen.add(_name)
                                        cameras.append(_name)
                            except Exception:
                                pass
                except Exception as exc:
                    sig.log_msg.emit(f"Camera load error ({day}): {exc}")
                if found_here:
                    days_with_data += 1
                    sig.log_msg.emit(f"Cameras from {day}: {found_here} folder(s), "
                                     f"{len(cameras)} distinct so far")
                    if days_with_data >= CAM_SCAN_DAYS_WITH_DATA:
                        break
            if gen != self._cam_load_gen:
                return
            cameras.sort(key=str.lower)
            sig.finished.emit(cameras)

        threading.Thread(target=worker, daemon=True).start()

    def _cam_scan_key(self):
        """Identity of the camera scan the current settings call for."""
        days = self._selected_days()
        if not days:
            return None
        scan_days = list(reversed(days))[:CAM_SCAN_MAX_DAYS]
        return (str(self._images_root), scan_days[0], scan_days[-1])

    def _on_cameras_loaded(self, camera_names: list):
        self._cam_loading = False
        self._cam_scanned_key = getattr(self, "_cam_scan_pending_key", None)
        self._all_cameras = []
        for name in camera_names:
            m = _re.match(r"^C\d{2}-(\d{2,3})-", name)
            num = m.group(1) if m else ""
            self._all_cameras.append((num, name))
        n = len(self._all_cameras)
        if n:
            self._cam_status_lbl.setText(f"{n} cameras available.")
        else:
            self._cam_status_lbl.setText(
                f"No cameras found (last {getattr(self, '_cam_scan_days', 1)} "
                "day(s) of the range).")
        self._sync_cameras_button()

    # ── CAMERA PICKER ─────────────────────────────────────────────────────────
    def _cam_num_for(self, name: str) -> str:
        for num, n in self._all_cameras:
            if n == name:
                return num
        m = _re.match(r"^C\d{2}-(\d{2,3})-", name)
        return m.group(1) if m else ""

    def _open_camera_picker(self):
        """The Cameras… button — the Image Slider's own picker, over the cameras
        found in the picked time window. Presets are shared with the Slider, so a
        camera set saved in one tab is offered in the other."""
        # A scan may never have run for this range (or have failed): let the dialog
        # do its own, so the list is never silently empty.
        if (not self._all_cameras and not getattr(self, "_cam_loading", False)
                and getattr(self, "_cam_scanned_key", None) != self._cam_scan_key()):
            self._load_cameras()

        days = self._selected_days()
        day_obj = days[-1] if days else (datetime.now(PRAGUE) if PRAGUE else datetime.now()).date()
        slider = _get_slider_module()
        dlg = slider.CameraPickerDialog(
            day_obj, 0, 23,
            [n for _num, n in self._selected_cameras],
            parent=self,
            preloaded_cameras=list(self._all_cameras))
        # The Slider configures its multi-camera grid from this dialog; the Shot
        # Finder has no grid, so that button would lead nowhere.
        btn_layout = getattr(dlg, "_btn_layout", None)
        if btn_layout is not None:
            btn_layout.setVisible(False)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._set_selected_cameras(dlg.selected_camera_names())
        self._log(f"Cameras: {len(self._selected_cameras)} selected")

    def _set_selected_cameras(self, names: list):
        """Replace the picked cameras (and the list that shows them)."""
        seen: set = set()
        picked: "list[tuple[str, str]]" = []
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            picked.append((self._cam_num_for(name), name))
        self._selected_cameras = picked
        self._cam_selected.setRowCount(0)
        for num, name in picked:
            r = self._cam_selected.rowCount()
            self._cam_selected.insertRow(r)
            self._cam_selected.setItem(r, 0, QTableWidgetItem(num))
            self._cam_selected.setItem(r, 1, QTableWidgetItem(name))
        if picked:
            self._cam_selected.selectRow(0)
            self._active_cam = picked[0][1]
        else:
            self._active_cam = None
        self._sync_cameras_button()

    def _sync_cameras_button(self):
        n = len(self._selected_cameras)
        total = len(self._all_cameras)
        self._btn_cameras.setText(
            f"📷  Cameras…  ({n}/{total})" if total else "📷  Cameras…")

    def _on_cam_selected_clicked(self, index):
        r = index.row()
        item = self._cam_selected.item(r, 1)
        if item:
            self._active_cam = item.text()

    def _on_cam_remove(self):
        r = self._cam_selected.currentRow()
        if r < 0 or r >= len(self._selected_cameras):
            return
        names = [n for _num, n in self._selected_cameras]
        del names[r]
        self._set_selected_cameras(names)

    # ── SEARCH ────────────────────────────────────────────────────────────────

    def _start_search(self):
        if self._search_running:
            return

        days = self._selected_days()
        if not days:
            QMessageBox.warning(self, "Date range", "From date must be ≤ To date.")
            return

        # Read the ticked (filter) PVs from the unified PV list
        criteria = self._get_criteria()
        if not criteria:
            QMessageBox.warning(self, "No search PV",
                "Tick at least one PV to search by.\n\n"
                "Unticked PVs are only displayed — they are not filtered on.")
            return

        # The list a search was actually run with is the one worth having back.
        self._save_pv_state()

        search_cols = [c["col"] for c in criteria]
        col = search_cols[0]  # primary column
        extra_cols = [c for c in self._show_cols() if c not in search_cols]

        # Convert UI-unit criteria to CSV units for each column — the exact inverse
        # of _format_value / _format_diff, or a target typed in the units the table
        # prints would be compared against something else.
        def _to_csv_units(c_col, c_val):
            return c_val / 1000.0 if c_col in MJ_COLUMNS else c_val

        criteria_csv = []
        for crit in criteria:
            criteria_csv.append({
                "col":        crit["col"],
                "target_csv": _to_csv_units(crit["col"], crit["target"]),
                "tol_csv":    _to_csv_units(crit["col"], crit["tol"]),
                "target_ui":  crit["target"],
                "tol_ui":     crit["tol"],
            })

        # Every camera in the list is searched, each into its own results tab. The list
        # is the input; _active_cam is only which of its rows is highlighted, and used
        # to be the ONLY camera a search ever looked at.
        cams: "list[str | None]" = [name for _num, name in self._selected_cameras]
        if not cams:
            cams = [self._active_cam] if self._active_cam else [None]
        self._rebuild_result_tabs(cams)

        self._btn_open_slider.setEnabled(False)
        self._set_busy(True)
        self._prog.setVisible(True)
        self._prog.setRange(0, len(days))
        self._prog.setValue(0)
        self._search_running = True
        self._result_lbl.setText(
            f"Searching {len(days)} days…"
            + (f" × {len(cams)} cameras" if len(cams) > 1 else ""))

        self._sig = _SearchSignals()
        self._sig.progress.connect(self._prog.setValue)
        self._sig.result.connect(self._on_day_result)
        self._sig.done.connect(self._on_search_done)
        self._sig.log_msg.connect(self._log)

        images_root  = self._images_root
        csv_root     = self._energy_csv_root

        # The hours of the Time window, not just its dates. They used to be collected,
        # displayed and then thrown away, so "today 14:00 → 16:00" searched from midnight.
        # The end is inclusive to the whole second the dialog shows (it picks :59:59),
        # or every sample in the final second of the window would be thrown away.
        tw_start_ns = int(self._tw_start.timestamp() * 1_000_000_000)
        tw_end_ns   = int(self._tw_end.timestamp() * 1_000_000_000) + 999_999_999

        _emit_log = self._sig.log_msg.emit

        def _fail_days(day, reason: str) -> None:
            """A day that produced no searchable result still gets a row — in EVERY
            camera tab, because the archiver data it failed on is the same for all of
            them. Dropping such a day is what made a whole search look empty."""
            for _cam in cams:
                self._sig.result.emit(
                    {"day": day, "status": "no_data", "reason": reason,
                     "search_cols": search_cols, "extra_cols": extra_cols,
                     "criteria_csv": criteria_csv, "cam": _cam})

        def worker():
            all_cols = list(search_cols) + [
                c for c in extra_cols if c not in search_cols]
            # Pre-warm the shared day cache: all (channel × day) fetches run in
            # parallel through the connection pool, so the sequential per-day
            # loop below is served from memory instead of days × cols × RTT.
            try:
                _chans = [CPVA_CHANNEL_MAP.get(c, c) for c in all_cols]
                _dkeys = [d.strftime("%Y-%m-%d") for d in days]
                if _chans and _dkeys:
                    _emit_log(f"Pre-warming {len(_chans)}×{len(_dkeys)} channel-days…")
                    cpva.warm_days(_chans, _dkeys, timeout=CPVA_HTTP_TIMEOUT)
            except Exception:
                pass
            for i, day in enumerate(days):
                try:
                    _emit_log(f"{day}: querying API+CSV for cols={all_cols}")
                    rows, per_col, col_meta = _load_api_for_day(
                        day, all_cols, log=_emit_log, csv_root=csv_root)
                    if not rows:
                        st = col_meta.get(search_cols[0], {}).get("status")
                        why = ("archiver did not answer for this day"
                               if st == "error" else "no samples archived for this PV")
                        _emit_log(f"{day}: no data (API + CSV) — {why}")
                        _fail_days(day, why)
                        self._sig.progress.emit(i + 1)
                        continue

                    # Clip the candidate shots to the picked hours. per_col is left whole
                    # on purpose: it only serves value look-ups around a shot, and a PV
                    # sampled just outside the window still describes a shot inside it.
                    n_all = len(rows)
                    rows = [r for r in rows
                            if tw_start_ns <= r.get("_ns", 0) <= tw_end_ns]
                    if not rows:
                        why = "no samples inside the chosen hours"
                        _emit_log(f"{day}: {n_all} samples, none in the time window")
                        _fail_days(day, why)
                        self._sig.progress.emit(i + 1)
                        continue

                    self._sig.log_msg.emit(
                        f"{day}: {len(rows)} samples"
                        + (f" (of {n_all}, rest outside the time window)"
                           if len(rows) != n_all else ""))

                    primary_crit = criteria_csv[0]
                    day_col      = primary_crit["col"]
                    target_csv   = primary_crit["target_csv"]
                    day_tol_csv  = primary_crit["tol_csv"]

                    # Rows in tolerance: primary col within tol, AND all other criteria
                    # match. Values go through _quantize_col first — the same snap the
                    # displayed value and Δ use, or a row could be excluded here on a
                    # number the table never shows.
                    rows_in_tol = []
                    for row in rows:
                        raw_primary = row.get(day_col, "")
                        try:
                            v_primary = _quantize_col(day_col, float(raw_primary))
                        except Exception:
                            continue
                        if abs(v_primary - target_csv) > day_tol_csv:
                            continue
                        # Check all secondary criteria using per-col closest-timestamp lookup
                        ok = True
                        row_ns = row.get("_ns", 0)
                        for sec in criteria_csv[1:]:
                            sec_col     = sec["col"]
                            sec_t_csv   = sec["target_csv"]
                            sec_tol_csv = sec["tol_csv"]
                            # Try the merged row first
                            raw_sec = row.get(sec_col, "")
                            if not raw_sec:
                                # Fallback: per-col closest-timestamp (same window
                                # the display uses — a shot must never qualify on
                                # one value and show a different one)
                                raw_sec, _sec_state = _lookup_col_value(
                                    per_col, col_meta, sec_col, row_ns,
                                    tol_s=EXTRA_COL_MATCH_TOL_S)
                            try:
                                v_sec = _quantize_col(sec_col, float(raw_sec))
                            except Exception:
                                ok = False
                                break
                            if abs(v_sec - sec_t_csv) > sec_tol_csv:
                                ok = False
                                break
                        if ok:
                            rows_in_tol.append(row)

                    if not rows_in_tol:
                        # No rows match all criteria — find closest to primary target
                        best = _find_best_match(rows, day_col, target_csv)
                    else:
                        # Best = row with minimum sum of normalized distances across all criteria
                        def _norm_dist(row, _crit_csv=criteria_csv, _pc=per_col,
                                       _cm=col_meta):
                            total = 0.0
                            rn = row.get("_ns", 0)
                            for crit in _crit_csv:
                                cc = crit["col"]; ct = crit["target_csv"]
                                raw = row.get(cc, "")
                                if not raw:
                                    raw, _st = _lookup_col_value(
                                        _pc, _cm, cc, rn,
                                        tol_s=EXTRA_COL_MATCH_TOL_S)
                                try:
                                    v = _quantize_col(cc, float(raw))
                                    total += abs(v - ct) / max(abs(ct), 1e-9)
                                except Exception:
                                    total += 1e6
                            return total
                        best = min(rows_in_tol, key=_norm_dist)

                    if best is None:
                        why = f"'{self._col_short(day_col)}' has no numeric values here"
                        self._sig.log_msg.emit(f"{day}: {why}")
                        _fail_days(day, why)
                        self._sig.progress.emit(i + 1)
                        continue

                    raw_best = best.get(day_col, "")
                    try:
                        actual_best = _quantize_col(day_col, float(raw_best))
                    except Exception:
                        actual_best = None

                    diff_best = abs(actual_best - target_csv) if actual_best is not None else None

                    # Hourová složka
                    dt_obj = best.get("_dt")
                    hour_folder = None
                    day_dir = _day_image_folder(day, images_root)
                    if dt_obj is not None and day_dir is not None:
                        hour_utc = _folder_hour_from_prague(dt_obj.hour, day)
                        hour_folder = _find_hour_folder(day, hour_utc, images_root=images_root)

                    # Display values for every searched + also-show PV at the best
                    # shot — resolved HERE so the UI thread never hits the network
                    # and the table shows exactly what the search matched on. The PVs
                    # do not depend on the camera, so this is done once for all of them.
                    best_ns = best.get("_ns")
                    display_vals: dict = {}
                    if best_ns is not None:
                        for cc in dict.fromkeys(search_cols + extra_cols):
                            raw_cc = best.get(cc, "")
                            if raw_cc:
                                display_vals[cc] = (raw_cc, "ok")
                            else:
                                display_vals[cc] = _lookup_col_value(
                                    per_col, col_meta, cc, best_ns,
                                    tol_s=EXTRA_COL_MATCH_TOL_S, log=_emit_log)

                    self._sig.log_msg.emit(
                        f"{day}: best={_format_value(day_col, raw_best)} "
                        f"in_tol={len(rows_in_tol)}")

                    # One row per camera: same shot, its own picture. Resolved here on
                    # the worker thread — SMB probing must stay off the UI thread — with
                    # the same resolver every other place in this tab uses, so the
                    # neighbouring hours are tried here too.
                    for _cam in cams:
                        folder_path = None
                        img_path = None
                        if not _cam:
                            img_problem = "no camera selected"
                        elif day_dir is None:
                            img_problem = "no images archived for this day"
                        else:
                            img_path, cam_folder = _find_image_in_day(
                                day, _cam, dt_obj, best_ns, {},
                                images_root=images_root, day_dir=day_dir)
                            folder_path = cam_folder
                            if cam_folder is None:
                                # Nothing of this camera in the shot's hour or its
                                # neighbours — the day HAS pictures, this moment does not.
                                img_problem = ("nothing was recorded at this time of day"
                                               if hour_folder is None
                                               else "this camera was not recording then")
                            else:
                                img_problem = _image_problem(img_path)
                            if img_problem is not None:
                                img_path = None
                        if folder_path is None:
                            folder_path = hour_folder
                        if img_problem is not None:
                            _emit_log(f"{day} {_cam or '(no camera)'}: {img_problem} "
                                      f"({dt_obj.strftime('%H:%M:%S') if dt_obj else '?'})")

                        self._sig.result.emit({
                            "day":          day,
                            "best_row":     best,
                            "rows_in_tol":  rows_in_tol,
                            "col":          day_col,
                            "actual":       actual_best,
                            "diff":         diff_best,
                            "target_csv":   target_csv,
                            "hour_folder":  hour_folder,
                            "folder_path":  folder_path,
                            "img_path":     img_path,
                            "cam":          _cam,
                            "extra_cols":   extra_cols,
                            "search_cols":  search_cols,
                            "per_col":      per_col,
                            "col_meta":     col_meta,
                            "criteria_csv": criteria_csv,
                            "display_vals": display_vals,
                            # A shot without a picture is still a result worth showing
                            # (the values are real), it just cannot be sent on.
                            "status":       "ok" if img_problem is None else "no_image",
                            "reason":       img_problem or "",
                        })

                except Exception as e:
                    self._sig.log_msg.emit(f"{day}: error — {e}")
                    _fail_days(day, f"search failed: {type(e).__name__}: {e}")

                self._sig.progress.emit(i + 1)

            self._sig.done.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _tab_for(self, cam) -> "tuple[QTableWidget, list]":
        """(table, results) of the tab a result belongs to.

        Keyed by the camera the search ran with — never by which tab is in front, which
        can be any of them while a multi-camera search is still filling them in."""
        if cam not in self._cam_tables:
            # A result for a camera no tab was built for should not exist; land it in
            # the first tab rather than dropping it on the floor.
            cam = self._cam_order[0] if self._cam_order else None
        return self._cam_tables[cam], self._cam_results[cam]

    def _add_failed_row(self, result: dict, day: date, col: str, reason: str):
        """A red row for a day that yielded nothing searchable.

        Every day of the range gets a row in every camera's tab, always. Emitting
        nothing is what turned a search over one busy day (the archiver refuses a whole
        day above ~110k samples) into an empty table with no explanation anywhere but
        the Log box."""
        criteria = result.get("criteria_csv") or []
        pv_str = "\n".join(self._col_short(c["col"]) for c in criteria) \
            or self._col_short(col)
        table, results = self._tab_for(result.get("cam"))
        dr = _DayResult(
            day=day, best_row=None, col=col, actual=None, diff=None,
            target_csv=0.0, hour_folder=None,
            search_cols=result.get("search_cols"),
            extra_cols=result.get("extra_cols"),
            criteria_csv=criteria, cam=result.get("cam"),
            status="no_data", reason=reason)
        # Appended like any other result: within a tab the table row index IS the index
        # into its result list, and every consumer of that list skips non-"ok" entries.
        results.append(dr)

        r = table.rowCount()
        table.insertRow(r)
        row_bg = QColor("#f8d7da")
        for c, text in enumerate([day.strftime("%Y-%m-%d"), "—", pv_str, "—", "—",
                                  "✕ " + reason, ""]):
            item = QTableWidgetItem(text)
            item.setBackground(row_bg)
            if c == 5:
                item.setForeground(QColor("#a11"))
                item.setToolTip(reason)
            table.setItem(r, c, item)
        n_lines = max(pv_str.count("\n") + 1, 1)
        if n_lines > 1:
            table.setRowHeight(r, 18 * n_lines + 8)

    def _on_day_result(self, result):
        if result is None:
            return

        day: date         = result["day"]
        status: str       = result.get("status", "ok")
        best_row: dict    = result.get("best_row") or {}
        rows_in_tol: list = result.get("rows_in_tol") or []
        col: str          = result.get("col") or (result.get("search_cols") or [""])[0]
        hour_folder       = result.get("hour_folder")
        cam               = result.get("cam")
        dt_obj: datetime = best_row.get("_dt")
        if status == "no_data" or dt_obj is None:
            self._add_failed_row(result, day, col,
                                 result.get("reason") or "nothing found for this day")
            return

        per_col = result.get("per_col", {})
        dr = _DayResult(
            day=day, best_row=best_row, col=col,
            actual=result["actual"], diff=result["diff"],
            target_csv=result["target_csv"], hour_folder=hour_folder,
            rows_in_tol=rows_in_tol, per_col=per_col,
            search_cols=result.get("search_cols"),
            extra_cols=result.get("extra_cols"),
            criteria_csv=result.get("criteria_csv"),
            cam=cam,
            col_meta=result.get("col_meta"),
            img_path=result.get("img_path"),
            status=status, reason=result.get("reason", ""))
        table, results = self._tab_for(cam)
        results.append(dr)

        # Folder + matched image were resolved in the worker (SMB off UI thread)
        folder_path = result.get("folder_path")
        img_path    = result.get("img_path")
        folder_str  = str(folder_path) if folder_path else "Not found"

        prague_str = dt_obj.strftime("%H:%M:%S.%f")[:-3]
        best_ns    = best_row.get("_ns")

        criteria_csv_res = result.get("criteria_csv", [])
        if not criteria_csv_res:
            criteria_csv_res = [{"col": col, "target_csv": result["target_csv"],
                                 "tol_ui": self._tol_sb.value()}]

        display_vals = result.get("display_vals", {})

        def _val_at(cc):
            """(raw, state) at the best shot — precomputed in the worker."""
            hit = display_vals.get(cc)
            if hit is not None:
                return hit
            raw = best_row.get(cc, "")
            return (raw, "ok") if raw else ("", "not_found")

        def _diff_ui(cc, target_csv):
            """Return (diff_in_ui_units, formatted_str)."""
            raw, state = _val_at(cc)
            try:
                v = _quantize_col(cc, float(raw))
            except (ValueError, TypeError):
                return None, (cpva.PV_TEXT_ERROR if state == "error"
                              else cpva.PV_TEXT_NOT_FOUND)
            d = abs(v - target_csv)
            return _diff_ui_value(cc, d), _format_diff(cc, d)

        # PV / Δ columns keep one line per search PV; the Value cell is a single
        # combined line with EVERY searched PV (+ also-show extras).
        pv_lines, val_parts, diff_lines, off_pvs = [], [], [], []
        pv_tips = []
        for crit in criteria_csv_res:
            cc = crit["col"]
            short = self._col_short(cc)
            pv_lines.append(short)
            # The cell has room for the short name only — the channel it was read
            # from goes in the tooltip.
            pv_tips.append(f"{short} — {self._col_channel(cc)}")
            raw_cc, state_cc = _val_at(cc)
            val_parts.append(f"{short}: {_format_value_state(cc, raw_cc, state_cc)}")
            dval, dstr = _diff_ui(cc, crit["target_csv"])
            diff_lines.append(f"{short}: {dstr}")
            if dval is not None and dval > crit.get("tol_ui", 0.0):
                off_pvs.append(f"{short} off {dstr}")

        # 'Also show' PVs appended to the Value cell
        extra_cols = result.get("extra_cols", [])
        for ec in extra_cols:
            raw_ec, state_ec = _val_at(ec)
            val_parts.append(f"{self._col_short(ec)}: {_format_value_state(ec, raw_ec, state_ec)}")

        pv_str   = "\n".join(pv_lines)
        val_str  = " | ".join(val_parts)
        diff_str = "\n".join(diff_lines)

        n_tol = len(rows_in_tol)
        if status == "no_image":
            # The numbers are real, the picture is not there — red, because the point of
            # this tab is the picture. Any PV-off note is kept, the row still explains
            # what was matched.
            status_str   = "✕ " + "; ".join(
                [result.get("reason") or "no image"] + off_pvs)
            status_color = QColor("#a11")
            row_bg       = QColor("#f8d7da")
        elif off_pvs:
            status_str   = "⚠ " + "; ".join(off_pvs)
            status_color = QColor("#856404")
            row_bg       = QColor("#fff3cd")
        elif n_tol > 1:
            status_str   = f"✓ {n_tol} shots in range"
            status_color = QColor("#155724")
            row_bg       = QColor("#d4edda")
        else:
            status_str   = "✓ match"
            status_color = QColor("#155724")
            row_bg       = None

        r = table.rowCount()
        table.insertRow(r)
        cells = [
            day.strftime("%Y-%m-%d"),
            prague_str,
            pv_str,
            val_str,
            diff_str,
            status_str,
            folder_str,
        ]
        for c, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if row_bg:
                item.setBackground(row_bg)
            if c == 2 and pv_tips:
                item.setToolTip("\n".join(pv_tips))
            if c == 5:
                item.setForeground(status_color)
            if c == 6:
                if folder_path is not None:
                    item.setData(Qt.ItemDataRole.UserRole, str(folder_path))
                    if img_path is not None:
                        item.setData(Qt.ItemDataRole.UserRole + 1, str(img_path))
                        item.setToolTip("Click to open the folder with the image selected")
                    else:
                        item.setToolTip("Click to open this folder in Explorer "
                                        "(matched image not found)")
                    item.setForeground(QColor("#2d7dff"))
                else:
                    item.setForeground(QColor("#cc0000"))
                    item.setToolTip("Image folder not found on the share")
            table.setItem(r, c, item)
        # Row height scales with the number of stacked lines
        n_lines = max(len(pv_lines), len(diff_lines), 1)
        if n_lines > 1:
            table.setRowHeight(r, 18 * n_lines + 8)

    def _on_table_cell_clicked(self, row: int, col: int):
        """Click the Folder cell (col 6): open Explorer with the matched image
        selected; fall back to opening the folder when the image is unknown."""
        if col != 6:
            return
        item = self._table.item(row, col)
        if item is None:
            return
        folder = item.data(Qt.ItemDataRole.UserRole)
        img = item.data(Qt.ItemDataRole.UserRole + 1)
        if not folder and not img:
            self._log("EXPLORER: no folder resolved for this row")
            return
        try:
            if img:
                # /select, and the path must stay ONE argument (comma included)
                subprocess.Popen(f'explorer /select,"{img}"')
                self._log(f"EXPLORER: select {img}")
            else:
                subprocess.Popen(["explorer", str(folder)])
                self._log(f"EXPLORER: {folder} (image not resolved — opening folder)")
        except Exception as e:
            QMessageBox.critical(self, "Error",
                                 f"Could not open folder:\n{type(e).__name__}: {e}")

    def _on_table_double_clicked(self, index):
        """Double-click a day row — list that day's shots in the panel underneath.

        The picked shot is drawn in the preview on the right, where the day row's own
        picture already appears; this used to be a modal dialog carrying its own copy of
        the table and of the picture."""
        r = index.row()
        if r < 0 or r >= len(self._day_results):
            return
        dr = self._day_results[r]
        if dr.status == "no_data":
            QMessageBox.information(self, "Nothing to show", f"{dr.day}: {dr.reason}.")
            return
        if not dr.rows_in_tol:
            QMessageBox.information(self, "No shots",
                                    f"No shots within tolerance for {dr.day}.")
            return
        self._show_day_panel(dr)

    def _on_search_done(self):
        self._search_running = False
        self._set_busy(False)
        self._prog.setVisible(False)
        # Counted over every camera tab, not just the visible one: a search that filled
        # three tabs must not report only the one in front.
        all_results = self._all_results()
        n = len(all_results)
        # Rows that produced a picture vs rows that are only a red explanation.
        # Reporting the row count alone would call a table full of failures a success.
        n_ok = sum(1 for dr in all_results if dr.status == "ok")
        n_cams = len(self._cam_order)
        self._result_lbl.setText(
            f"Results: {n_ok} with an image"
            + (f", {n - n_ok} without" if n > n_ok else "")
            + (f"  ({n_cams} cameras)" if n_cams > 1 else ""))
        self._log(f"Search done — {n} row(s), {n_ok} with an image")
        # _set_busy(False) re-enables every control, so the export buttons must be
        # re-evaluated afterwards: they act on the tab in front, not on the whole search.
        self._sync_export_buttons()

    # ── OPEN IN SLIDER ────────────────────────────────────────────────────────

    def _open_in_slider(self):
        if self._slider_ref is None or self._tab_widget is None:
            QMessageBox.information(self, "Image Slider",
                "Image Slider is not connected. Run via main.py.")
            return

        if not self._day_results:
            return

        # The camera picked at SEARCH time (dr.cam) is what the results belong to;
        # self._active_cam is only a fallback for results that carry none. Sending
        # must not fail just because the camera list was edited after the search.
        cam = self._active_cam

        # One file per day, each paired with the result it came from. The pairing is
        # carried, not recomputed from a position: indexing results_to_open by the
        # index into this list put every caption after a skipped day on the wrong
        # picture.
        files_to_copy: list[tuple[Path, _DayResult]] = []
        # Použij jen vybrané řádky, nebo všechny pokud nic není vybráno
        selected_rows = sorted(set(
            idx.row() for idx in self._table.selectedIndexes()
        ))
        if selected_rows:
            results_to_open = [self._day_results[r] for r in selected_rows
                               if r < len(self._day_results)]
        else:
            results_to_open = self._day_results

        for dr in results_to_open:
            if dr.status != "ok":
                self._log(f"{dr.day}: {dr.reason or 'nothing found'}, skipping")
                continue

            # Fast path: the search worker already resolved the matched image
            if dr.img_path is not None:
                files_to_copy.append((Path(dr.img_path), dr))
                self._log(f"{dr.day}: ✓ {Path(dr.img_path).name}")
                continue

            if dr.hour_folder is None:
                self._log(f"{dr.day}: no hour folder, skipping")
                continue

            dr_cam = dr.cam or cam
            if not dr_cam:
                self._log(f"{dr.day}: no camera for this result, skipping")
                continue

            dt_obj = dr.best_row.get("_dt")
            if dt_obj is None:
                continue

            img = self._find_image_for_shot(dr, dr_cam, dt_obj,
                                            dr.best_row.get("_ns"), {})
            if img is not None:
                files_to_copy.append((img, dr))
                self._log(f"{dr.day}: ✓ {img.name}")
            else:
                self._log(f"{dr.day}: no image near {dt_obj.strftime('%H:%M:%S')}")

        if not files_to_copy:
            QMessageBox.warning(self, "No images found",
                "Could not find matching images.\n\n"
                "Make sure the camera is correct and the data exists.")
            return

        # Fresh temp folder per send. The PREVIOUS one is deleted only after the
        # Slider has been pointed at the new one (below) — deleting it up front
        # yanked the files out from under a Slider still showing the last send.
        prev_temp_dir = self._temp_dir
        self._temp_dir = tempfile.mkdtemp(prefix="SF_slider_")
        temp_path = Path(self._temp_dir)

        # Sestav energy map — filename -> text pro zobrazení v slideru.
        # PVs come from the result the file actually came from (dr), never from a
        # position in some other list, and never from the live left panel.
        copied = 0
        energy_map: dict[str, str] = {}
        for src, dr in files_to_copy:
            try:
                dst = temp_path / src.name
                if dst.exists():
                    dst = temp_path / f"{src.stem}_{copied}{src.suffix}"
                shutil.copy2(src, dst)
                energy_map[dst.name] = self._build_energy_text(
                    dr, dr.best_row, dr.best_row.get("_ns"))
                copied += 1
            except Exception as e:
                self._log(f"Copy error {src.name}: {e}")

        if copied == 0:
            QMessageBox.warning(self, "Copy failed", "Could not copy images.")
            return

        self._log(f"Copied {copied} images → {self._temp_dir}")

        # Hand over through receive_external_folder: it clears whatever the Slider
        # was set to (multi-cam grid, live mode, subtraction reference, focus mode,
        # a previous energy map) so the images always land. Falls back to the plain
        # entry point when running against an older is_t.py.
        send_cam = next((dr.cam for _src, dr in files_to_copy if dr.cam), cam)
        self._tab_widget.setCurrentIndex(1)
        recv = getattr(self._slider_ref, "receive_external_folder", None)
        if callable(recv):
            ok = recv(temp_path, energy_map=energy_map, discrete=True,
                      cam_name=send_cam)
            if not ok:
                QMessageBox.warning(self, "Image Slider",
                    f"Slider refused the folder:\n{temp_path}")
                return
        else:
            self._slider_ref._discrete_mode = True
            self._slider_ref._sf_energy_map = energy_map
            self._slider_ref.open_folder_path(temp_path)
        self._log(f"Sent {copied} image(s) to Image Slider")

        if prev_temp_dir:
            try:
                shutil.rmtree(prev_temp_dir, ignore_errors=True)
            except Exception:
                pass

    def _save_results(self):
        if not self._day_results:
            return

        # The camera these rows were SEARCHED with (this tab's), not whichever row of
        # the camera list happens to be highlighted now: saving used to refuse outright
        # once the camera was removed from the list, and to name the files after a
        # different camera than the one in the picture as soon as another was clicked.
        cam = self._current_cam_key() or self._active_cam
        if not any(dr.status == "ok" for dr in self._day_results):
            QMessageBox.warning(self, "Nothing to save",
                "No row in this tab has an image.")
            return

        initial_dir = str(self._last_save_dir) if self._last_save_dir else str(Path.home())
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", initial_dir)
        if not out_dir:
            return
        self._last_save_dir = Path(out_dir)
        out_path = Path(out_dir)

        selected_rows = sorted(set(
            idx.row() for idx in self._table.selectedIndexes()
        ))
        if selected_rows:
            results_to_save = [self._day_results[r] for r in selected_rows
                               if r < len(self._day_results)]
        else:
            results_to_save = self._day_results

        copied = 0
        errors = 0
        for dr in results_to_save:
            if dr.status != "ok":
                self._log(f"{dr.day}: {dr.reason or 'nothing found'}, skipping")
                errors += 1
                continue
            dt_obj = dr.best_row.get("_dt")
            if dt_obj is None:
                errors += 1
                continue

            # Fast path: the search worker already resolved the matched image
            img = Path(dr.img_path) if dr.img_path is not None else None
            if img is None:
                if dr.hour_folder is None:
                    self._log(f"{dr.day}: no hour folder, skipping")
                    errors += 1
                    continue

                dr_cam = dr.cam or cam
                if not dr_cam:
                    self._log(f"{dr.day}: no camera for this result, skipping")
                    errors += 1
                    continue
                img = self._find_image_for_shot(dr, dr_cam, dt_obj,
                                                dr.best_row.get("_ns"), {})
            if img is None:
                self._log(f"{dr.day}: no image near {dt_obj.strftime('%H:%M:%S')}")
                errors += 1
                continue

            # Název souboru: camera_label + Prague timestamp + PV value
            val_str = _format_value(dr.col, dr.best_row.get(dr.col, "")).replace(" ", "").replace("/", "-")
            short = PV_COLUMNS.get(dr.col, dr.col).split(" [")[0]
            # The camera of THIS row — the picture came from it.
            _cam_label = _clean_cam_for_filename(dr.cam or cam or "")
            _ns_best = dr.best_row.get("_ns")
            if _ns_best is not None and PRAGUE is not None:
                _ts_sec = _ns_best // 1_000_000_000
                _ts_ms = (_ns_best % 1_000_000_000) // 1_000_000
                _dt_pr = datetime.fromtimestamp(_ts_sec, tz=PRAGUE)
                _prague_stamp = _dt_pr.strftime("%Y-%m-%d_%H-%M-%S-") + f"{_ts_ms:03d}"
            else:
                _dt_prague = dt_obj.astimezone(PRAGUE) if PRAGUE is not None else dt_obj
                _prague_stamp = _dt_prague.strftime("%Y-%m-%d_%H-%M-%S-000")
            dst_name = f"{_cam_label}_{_prague_stamp}_{short}_{val_str}.png"
            dst = out_path / dst_name

            try:
                # Sestav energy text pro anotaci — from search-time state (dr)
                _best_ns_save = dr.best_row.get("_ns")
                energy_text = self._build_energy_text(dr, dr.best_row, _best_ns_save)

                # Ulož s anotací jako PNG
                dst = dst.with_suffix(".png")
                from PIL import Image as _PilImg, ImageDraw as _PilDraw, ImageFont as _PilFont
                import numpy as _np
                from PySide6.QtGui import QImage
                from PySide6.QtCore import QSize

                # Load via PIL to preserve 16 bits, then render on the same scale as the
                # preview — a saved frame must look like what was on screen.
                try:
                    _pil_raw = _PilImg.open(str(img))
                    if _pil_raw.mode in ("I", "I;16"):
                        _arr_f = _np.array(_pil_raw, dtype=_np.float32)
                    elif _pil_raw.mode in ("RGB", "RGBA"):
                        _arr_f = _np.array(_pil_raw.convert("L"), dtype=_np.float32)
                    else:
                        _arr_f = _np.array(_pil_raw.convert("L"), dtype=_np.float32)
                    _s_auto, _s_gam, _s_con, _s_off = self._bc_args()
                    arr = _render_u8(_arr_f, _s_auto,
                                     _full_scale_for_mode(_pil_raw.mode, img,
                                                          _pil_raw.info),
                                     _s_gam, _s_con, _s_off)
                    w, h = arr.shape[1], arr.shape[0]
                except Exception:
                    shutil.copy2(img, dst)
                    copied += 1
                    continue
                grad_name = self._gradient_cb.currentText()
                GRADIENTS_SF = {
                    "Grayscale": None,
                    "Gradient": _np.array([[int(c) for c in stop] for stop in [
                        [0,0,0],[255,0,0],[255,200,0],[255,255,0],[0,255,0],[0,220,255],[255,255,255],[255,255,255]
                    ]]),
                }
                # Použij stejné LUT jako is.py
                try:
                    _is_mod = _sys.modules.get("image_slider")
                    if _is_mod and hasattr(_is_mod, "GRADIENTS"):
                        lut = _is_mod.GRADIENTS.get(grad_name)
                    else:
                        lut = SF_GRADIENTS.get(grad_name)
                except Exception:
                    lut = SF_GRADIENTS.get(grad_name, None)

                if lut is not None:
                    rgb = _lut_pixels_sf(lut, arr, grad_name)
                    pil_img = _PilImg.fromarray(rgb, mode="RGB")
                else:
                    pil_img = _PilImg.fromarray(arr).convert("RGB")

                # Anotační bar — dynamický počet řádků
                from PIL import ImageDraw as _PilDraw2
                _tmp_draw2 = _PilDraw.Draw(_PilImg.new("RGB", (1, 1)))
                parts_list2 = energy_text.split("  |  ")
                _start_fsize = 20

                chosen_font2 = None
                display_lines2 = [energy_text]
                for fsize2 in range(_start_fsize, 7, -1):
                    _f2 = None
                    for _fname2 in (
                        "C:/Windows/Fonts/arial.ttf",
                        "C:/Windows/Fonts/segoeui.ttf",
                        "C:/Windows/Fonts/calibri.ttf",
                        "DejaVuSans.ttf",
                    ):
                        try:
                            _f2 = _PilFont.truetype(_fname2, fsize2)
                            break
                        except Exception:
                            continue
                    if _f2 is None:
                        _f2 = _PilFont.load_default()

                    try:
                        bb = _tmp_draw2.textbbox((0, 0), energy_text, font=_f2)
                        if (bb[2] - bb[0]) <= pil_img.width - 20:
                            chosen_font2 = _f2
                            display_lines2 = [energy_text]
                            break
                    except Exception:
                        pass

                    fitted2 = False
                    for n_lines2 in range(2, len(parts_list2) + 1):
                        chunk2 = max(1, len(parts_list2) // n_lines2)
                        lines2 = []
                        for i2 in range(0, len(parts_list2), chunk2):
                            lines2.append("  |  ".join(parts_list2[i2:i2 + chunk2]))
                        max_w2 = 0
                        try:
                            for line2 in lines2:
                                bb2 = _tmp_draw2.textbbox((0, 0), line2, font=_f2)
                                max_w2 = max(max_w2, bb2[2] - bb2[0])
                        except Exception:
                            max_w2 = pil_img.width
                        if max_w2 <= pil_img.width - 20:
                            chosen_font2 = _f2
                            display_lines2 = lines2
                            fitted2 = True
                            break
                    if fitted2:
                        break

                if chosen_font2 is None:
                    try:
                        chosen_font2 = _PilFont.truetype("C:/Windows/Fonts/arial.ttf", 8)
                    except Exception:
                        chosen_font2 = _PilFont.load_default()

                try:
                    bb_line = _tmp_draw2.textbbox((0, 0), "Ag", font=chosen_font2)
                    line_h2 = bb_line[3] - bb_line[1]
                except Exception:
                    line_h2 = 14
                padding2 = 8
                bar_h2 = max(30, line_h2 * len(display_lines2) + padding2 * (len(display_lines2) + 1))

                bar2 = _PilImg.new("RGB", (pil_img.width, bar_h2), (255, 255, 255))
                draw2 = _PilDraw.Draw(bar2)
                total_text_h2 = line_h2 * len(display_lines2) + padding2 * (len(display_lines2) - 1)
                y2 = (bar_h2 - total_text_h2) // 2
                for line2 in display_lines2:
                    try:
                        bb2 = draw2.textbbox((0, 0), line2, font=chosen_font2)
                        tw2 = bb2[2] - bb2[0]
                    except Exception:
                        tw2 = 0
                    x2 = max(8, (pil_img.width - tw2) // 2)
                    draw2.text((x2, y2), line2, fill=(0, 0, 0), font=chosen_font2)
                    y2 += line_h2 + padding2

                combined = _PilImg.new("RGB", (pil_img.width, pil_img.height + bar_h2))
                combined.paste(pil_img, (0, 0))
                combined.paste(bar2, (0, pil_img.height))
                combined.save(dst)
                try:
                    _get_slider_module()._copy_metadata_into_png(img, dst, save_txt=False)
                except Exception:
                    pass
                copied += 1
                self._log(f"{dr.day}: saved {dst.name}")
            except Exception as e:
                self._log(f"{dr.day}: copy error {e}")
                errors += 1

        QMessageBox.information(self, "Save images",
            f"Saved: {copied}\nErrors: {errors}\n\nFolder: {out_path}")

    def _send_to_workshop(self):
        """Send currently previewed image to Workshop tab."""
        wk = getattr(self, "_workshop_ref", None)
        if wk is None:
            return
        img_path = getattr(self, "_current_preview_path", None)
        if img_path is None or not img_path.exists():
            QMessageBox.information(self, "Workshop",
                "No preview image available. Select a row first."); return
        try:
            from PIL import Image as _PilImg
            import numpy as _np
            pil = _PilImg.open(str(img_path))
            if pil.mode in ("I", "I;16"):
                arr_f = _np.array(pil, dtype=_np.float32)
            elif pil.mode in ("RGB", "RGBA"):
                arr_f = _np.array(pil.convert("L"), dtype=_np.float32)
            else:
                arr_f = _np.array(pil.convert("L"), dtype=_np.float32)

            # The Workshop measures and builds its histogram from the picture it is
            # given, so it gets the plain absolute mapping — never this tab's Auto
            # passes, Contrast, Brightness or Gamma, which would hand it a display curve
            # instead of data.
            arr8 = _render_u8(arr_f, False,
                              _full_scale_for_mode(pil.mode, img_path, pil.info), None)

            cam_name = _re.sub(r"[-_]+IMG$", "", img_path.parent.name, flags=_re.IGNORECASE).rstrip("-_")
            label = f"{cam_name}  |  {img_path.name}"
            wk.receive_image(arr8, label, source_path=img_path)
        except Exception as e:
            QMessageBox.warning(self, "Workshop", f"Could not send image:\n{e}")


# ── STANDALONE ENTRY POINT ────────────────────────────────────────────────────

def main():
    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget     { background: #f3f3f3; color: #111; }
        QLabel      { background: transparent; }
        QPushButton { padding: 5px 8px; }
        QComboBox   { padding: 3px 6px; }
    """)
    win = QMainWindow()
    if getattr(_sys, "frozen", False):
        win.setWindowTitle(Path(_sys.executable).stem)
    else:
        win.setWindowTitle("Shot Finder")
    screen = QApplication.primaryScreen().availableGeometry()
    win.resize(min(1100, screen.width()), min(700, screen.height()))
    win.move(screen.left(), screen.top())
    try:
        if getattr(_sys, "frozen", False):
            _base = Path(_sys.executable).resolve().parent
        else:
            _base = Path(__file__).resolve().parent
        _icon_path = _base / "icon.ico"
        if _icon_path.exists():
            win.setWindowIcon(QIcon(str(_icon_path)))
    except Exception:
        pass
    widget = ShotFinderWidget()
    win.setCentralWidget(widget)
    win.show()
    _sys.exit(app.exec())


if __name__ == "__main__":
    main()