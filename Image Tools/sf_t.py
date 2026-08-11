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
import ssl
import sys as _sys
import shutil
import subprocess
import tempfile
import atexit
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from PySide6.QtGui import QIcon

try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    PRAGUE = None

from PySide6.QtCore import Qt, QDate, QObject, Signal, QTimer, QEvent
from PySide6.QtGui import QColor, QTextCharFormat, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QLineEdit,
    QScrollArea, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCalendarWidget,
    QMessageBox, QMainWindow, QStyledItemDelegate, QSizePolicy,
    QProgressBar, QPlainTextEdit, QRadioButton, QButtonGroup,
    QSpinBox, QCheckBox, QDialog, QDialogButtonBox, QGroupBox,
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

SF_GRADIENTS: dict = {
    "Grayscale":       None,
    "Gradient":        _make_lut_sf([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
    "Hot":             _make_lut_sf([(0,(0,0,0)),(0.33,(255,0,0)),(0.66,(255,255,0)),(1,(255,255,255))]),
    "Binary":          _make_stepped_lut_sf([(0,(0,0,0)),(0.17,(255,0,0)),(0.33,(255,165,0)),(0.5,(255,255,0)),(0.67,(0,255,0)),(0.83,(0,200,255)),(0.92,(0,0,255)),(1,(255,255,255))]),
    "Black and White": _make_binary_lut_sf(),
    "Viridis":         _make_lut_sf([(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))]),
    "Plasma":          _make_lut_sf([(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))]),
    "Inferno":         _make_lut_sf([(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))]),
    "Jet":             _make_lut_sf([(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))]),
    "Turbo":           _make_lut_sf([(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))]),
}

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
IMAGES_ROOT_OPTIONS = {
    "Lab":    Path(r"//users-L3.tier0.lcs.local/cpva-image-2026"),
    "Office": Path(r"\\users-L3.tier0.lcs.local\cpva-image-2026"),
}
IMAGES_ROOT = IMAGES_ROOT_OPTIONS["Lab"]   # default; overridden by UI combo


def _images_root_for_year(images_root: Path, year: int) -> Path:
    """Return the images root with its share pointing at the given year.

    Each year lives in its own share (cpva-image-<year>). The Lab/Office root
    only fixes the host + slash style, so swap just the year in the share name
    (Path.parent cannot be used: on a UNC path the share is part of the anchor).
    """
    swapped, n = _re.subn(r"cpva-image-\d{4}", f"cpva-image-{year}", str(images_root), count=1)
    return Path(swapped) if n else images_root / f"cpva-image-{year}"

ENERGY_CSV_ROOT_OPTIONS = {
    "Lab":    r"//hapls-share.cs.eli-beams.eu/scratch/Salvation/2026_alldata",
    "Office": r"Z:\Salvation\2026_alldata",
}
# CSV fallback — same root / format as Image Finder
ENERGY_CSV_ROOT     = ENERGY_CSV_ROOT_OPTIONS["Lab"]
ENERGY_CSV_NAME_FMT = "dataof%Y%b_%d"   # e.g. dataof2026Mar_24
# Tolerance for closest-timestamp extra-column matching (seconds).
# PV channels (esp. Back_Ref / waveplate) are sampled sparsely, so a too-tight
# window made secondary/extra PVs show "—" even when valid data existed nearby.
EXTRA_COL_MATCH_TOL_S = 30.0
# Tolerance (ns) for matching a camera image filename timestamp to a shot.
# Widened from 10 s — per-day clock drift between archiver and camera filenames
# could exceed 10 s and blank the preview.
IMG_MATCH_TOL_NS = 30_000_000_000

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

# Camera channels (C03-013-PFM1NF:Exposure, …) are ~40 % of the archiver's "**"
# listing. They are matched last in the PV search so a query like "pcm" cannot
# be filled up entirely by camera channels before a single energy PV shows up.
_CAM_CHANNEL_RE = _re.compile(r"^C\d{2}-\d{2,3}-")

SBW4_TRANSMISSION        = 0.749
SBW4_WARNING_THRESHOLD_J = 0.5

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _read_img_max_value(path: Path) -> "float | None":
    """Read imgMaxValue from PNG tEXt metadata — physical maximum pixel value
    recorded by the camera (equivalent to Matlab imgMeta.OtherText{12,2}).
    Returns float or None if not found."""
    if path.suffix.lower() != ".png":
        return None
    try:
        from PIL import Image as _PilImg
        with _PilImg.open(str(path)) as pil:
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


def _cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                        timeout: float = CPVA_HTTP_TIMEOUT) -> "tuple[list[dict], str]":
    """Fetch archiver samples via the shared pooled client (keep-alive, retry).
    Returns (samples_list, url_used). Raises cpva.CpvaError."""
    url = f"{CPVA_BASE_URL}/samples?channelName={urllib.parse.quote(channel)}"
    return cpva.fetch_samples(channel, start_ns, end_ns, timeout=timeout), url


def _cpva_fetch_channels(pattern: str = "**",
                         timeout: float = CPVA_HTTP_TIMEOUT) -> "list[str]":
    """Return all archiver channel names matching `pattern` (default: all)."""
    return cpva.fetch_channels(pattern, timeout=timeout)


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


def _format_diff(col: str, diff_csv: float) -> str:
    """Format a |value − target| difference (CSV units) in the column's UI units."""
    if col == "sbw4":
        return f"{diff_csv * SBW4_TRANSMISSION:.4f} J"
    if col in MJ_COLUMNS:
        return f"{diff_csv * 1000:.2f} mJ"
    if col == "waveplate":
        return f"{diff_csv:.0f}"
    if col in PV_COLUMNS:
        return f"{diff_csv:.4f} J"
    return f"{diff_csv:.4g}"


def _find_best_match(rows: list[dict], col: str, target: float) -> dict | None:
    # NOTE: `target` is already in CSV units (callers pass target_csv, i.e.
    # the sbw4/0.749 and mJ conversions are applied upstream in _to_csv_units).
    # Do NOT re-convert here, or sbw4 would be divided by 0.749 twice.
    best = None
    best_diff = float("inf")
    for row in rows:
        raw = row.get(col, "")
        try:
            val = float(raw)
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


def _format_value(col: str, raw: str) -> str:
    try:
        v = float(raw)
        if col in MJ_COLUMNS:
            return f"{v * 1000:.2f} mJ"
        if col == "sbw4":
            return f"{v * SBW4_TRANSMISSION:.4f} J"
        if col == "waveplate":
            return f"{v:.0f}"
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

# ── CALENDAR DELEGATE ─────────────────────────────────────────────────────────

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
        self._noscroll_installed = set()

    def _install_on_all_children(self):
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
        event.accept()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.accept()
            return True
        return super().eventFilter(obj, event)


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


# ── RESULT DATA ───────────────────────────────────────────────────────────────

class _DayResult:
    def __init__(self, day: date, best_row: dict, col: str,
                 actual, diff, target_csv: float, hour_folder,
                 rows_in_tol: "list | None" = None,
                 per_col: "dict | None" = None,
                 search_cols: "list | None" = None,
                 extra_cols: "list | None" = None,
                 criteria_csv: "list | None" = None,
                 cam: "str | None" = None,
                 col_meta: "dict | None" = None,
                 img_path=None):
        self.day          = day
        self.best_row     = best_row
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
        if best_row.get("_ns") is not None:
            self.ts_ns = int(best_row["_ns"])
        else:
            dt_obj: datetime = best_row.get("_dt")
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

        def _make_cal(init_date: datetime) -> _NoScrollCalendar:
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
            cal.setStyleSheet("""
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
            """)
            cal.setSelectedDate(QDate(init_date.year, init_date.month, init_date.day))
            return cal

        # Start section
        grp_start = QGroupBox("Start point")
        start_lay = QVBoxLayout(grp_start)
        self._cal_start = _make_cal(start_dt)
        self._hour_start = QSpinBox()
        self._hour_start.setRange(0, 23)
        self._hour_start.setValue(start_dt.hour)
        self._hour_start.setFixedWidth(70)
        hr_start_row = QHBoxLayout()
        hr_start_row.addWidget(QLabel("Hour:"))
        hr_start_row.addWidget(self._hour_start)
        hr_start_row.addStretch(1)
        start_lay.addWidget(self._cal_start)
        start_lay.addLayout(hr_start_row)

        # End section
        grp_end = QGroupBox("End point")
        end_lay = QVBoxLayout(grp_end)
        self._cal_end = _make_cal(end_dt)
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
        end_lay.addWidget(self._cal_end)
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
        self._cal_end.setSelectedDate(QDate(now.year, now.month, now.day))
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
        self._day_results: list[_DayResult] = []

        self._all_cameras: list[tuple[str, str]] = []
        self._selected_cameras: list[tuple[str, str]] = []
        self._active_cam: str | None = None

        self._images_root: Path = IMAGES_ROOT_OPTIONS["Lab"]
        self._energy_csv_root: str = ENERGY_CSV_ROOT_OPTIONS["Lab"]
        self._pv_cfg: list[dict] = []
        self._pv_rows: list[dict] = []

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
        for name in ("_cam_dropdown", "_pv_dropdown"):
            dd = getattr(self, name, None)
            if dd is not None:
                dd.hide()
        for name in ("_cam_search", "_pv_search"):
            le = getattr(self, name, None)
            if le is not None:
                le.clear()

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
            return
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
                    self._gradient_cb, self._btn_time_window]:
            btn.setEnabled(not busy)
    
    def _on_csv_source_changed(self, name: str):
        self._energy_csv_root = ENERGY_CSV_ROOT_OPTIONS.get(name, ENERGY_CSV_ROOT_OPTIONS["Lab"])
        self._images_root = IMAGES_ROOT_OPTIONS.get(name, IMAGES_ROOT_OPTIONS["Lab"])
        self._log(f"Source -> {name}: images={self._images_root}  csv={self._energy_csv_root}")
        self._all_cameras = []
        if hasattr(self, "_cam_status_lbl"):
            self._cam_status_lbl.setText("No cameras loaded.")
        self._load_cameras()

    def _on_source_changed(self, name: str):
        self._images_root = IMAGES_ROOT_OPTIONS.get(name, IMAGES_ROOT_OPTIONS["Lab"])
        self._log(f"Images source -> {name}: {self._images_root}")
        self._all_cameras = []
        if hasattr(self, "_cam_status_lbl"):
            self._cam_status_lbl.setText("No cameras loaded.")
        self._load_cameras()

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

        # Time window + Source
        ll.addWidget(_group_label("Time window & source"))
        tw_row = QHBoxLayout()
        self._btn_time_window = QPushButton("📅  Time window")
        self._btn_time_window.setToolTip("Pick start and end date/hour for the search")
        self._btn_time_window.clicked.connect(self._open_time_window)
        tw_row.addWidget(self._btn_time_window)
        # Keep self._source_combo as hidden attribute for backward compat
        self._source_combo = QComboBox()
        for name in IMAGES_ROOT_OPTIONS:
            self._source_combo.addItem(name)
        self._source_combo.setVisible(False)
        # Primary visible combo — drives both images root and CSV root
        self._csv_source_combo = _NoScrollComboBox()
        self._csv_source_combo.setToolTip(
            "Root path for camera images AND energy CSV files\n"
            "(Lab = network share, Office = mapped drive Z:)")
        for name in ENERGY_CSV_ROOT_OPTIONS:
            self._csv_source_combo.addItem(name)
        self._csv_source_combo.currentTextChanged.connect(self._on_csv_source_changed)
        tw_row.addWidget(self._csv_source_combo)
        ll.addLayout(tw_row)

        self._date_info_lbl = QLabel("")
        self._date_info_lbl.setStyleSheet("font-size: 10px; color: #555;")
        ll.addWidget(self._date_info_lbl)
        ll.addWidget(_hsep())

        # ── PV search state ───────────────────────────────────────────────
        # ONE ordered list of picked PVs. Each entry carries its own "filter"
        # flag: checked = the search filters on it (target ± tolerance),
        # unchecked = its value is only displayed on the results/images.
        self._pv_cfg: list[dict] = []             # [{col, target, tol, filter}]
        self._pv_rows: list[dict] = []            # widgets, parallel to _pv_cfg
        self._custom_labels: dict[str, str] = {}  # custom col → display label
        self._all_pv_channels: list[str] = []     # fetched archiver channels
        self._chan_loading = False
        self._pv_suggestions: list[tuple[str, str]] = []  # (display, col_key)
        self._rebuild_pv_suggestions()

        # PV selection — one search box for every PV, presets and archiver alike
        ll.addWidget(_group_label("PVs"))
        self._pv_search = QLineEdit()
        self._pv_search.setPlaceholderText("search PV to add… (e.g. SBW4, Energy)")
        self._pv_search.setToolTip(
            "Add any archiver PV. Tick a PV to search by it (target ± tolerance);\n"
            "leave it unticked to only show its value in the results and on the images.")
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
        ll.addWidget(_hsep())

        # Camera selection
        ll.addWidget(_group_label("Cameras"))
        self._cam_search = QLineEdit()
        self._cam_search.setPlaceholderText("search cameras… (e.g. PT)")
        self._cam_search.textEdited.connect(self._on_cam_search_changed)
        ll.addWidget(self._cam_search)

        # Dropdown — Tool window místo Popup (nezabírá focus)
        self._cam_dropdown = QTableWidget(0, 2)
        self._cam_dropdown.setHorizontalHeaderLabels(["#", "Camera"])
        self._cam_dropdown.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._cam_dropdown.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._cam_dropdown.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cam_dropdown.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cam_dropdown.verticalHeader().setVisible(False)
        self._cam_dropdown.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        self._cam_dropdown.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cam_dropdown.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._cam_dropdown.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._cam_dropdown.clicked.connect(self._on_cam_dropdown_clicked)
        self._cam_dropdown.setStyleSheet(
            "QTableWidget { border: 1px solid #2d7dff; background: #fff; }"
            "QTableWidget::item:selected { background: #2d7dff; color: #fff; }")

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
        ll.addWidget(_hsep())

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

        grad_row = QHBoxLayout()
        grad_row.addWidget(QLabel("Gradient:"))
        self._gradient_cb = _NoScrollComboBox()
        GRADIENT_NAMES = [
            "Grayscale", "Gradient", "Hot", "Binary", "Black and White",
            "Viridis", "Plasma", "Inferno", "Jet", "Turbo"
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

        ll.addWidget(_hsep())

        # Log
        ll.addWidget(_group_label("Log"))
        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setMaximumHeight(140)
        self._log_box.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        self._log_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        ll.addWidget(self._log_box)

        ll.addStretch(1)
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
        rl.addLayout(hdr_row)

        # Results table — 7 sloupců
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "Date", "Prague Time", "PV", "Value", "Δ from target", "Status", "Folder"
        ])
        hh = self._table.horizontalHeader()
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
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_table_double_clicked)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        rl.addWidget(self._table, 1)

        root_layout.addWidget(left_scroll)
        root_layout.addWidget(rw, 1)

        # Preview panel
        self._preview_widget = _PreviewWidget()
        self._preview_widget.setMinimumHeight(200)
        root_layout.addWidget(self._preview_widget, 1)

        # Init
        self._pv_cfg = [{"col": "sbw4", "target": 10.0, "tol": 0.0, "filter": True}]
        self._rebuild_pv_rows()
        self._update_date_info()
        QTimer.singleShot(300, self._load_cameras)
        QTimer.singleShot(400, self._fetch_channel_list)

    def _setup_calendar(self, cal: _NoScrollCalendar):
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
        cal.setStyleSheet("""
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
        """)

    # ── EVENTS ────────────────────────────────────────────────────────────────

    def _on_date_changed(self):
        self._update_date_info()
        self._load_cameras()

    # ── PV column helpers ─────────────────────────────────────────────────
    def _col_label(self, col: str) -> str:
        return PV_COLUMNS.get(col) or self._custom_labels.get(col, col)

    def _col_short(self, col: str) -> str:
        return self._col_label(col).split(" [")[0]

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

    @staticmethod
    def _split_query(text: str) -> "list[str]":
        """Query text → lowercase tokens. Spaces, commas and '*' all separate,
        so "023 l3", "023,l3" and "*023**l3*" are the same query: every token
        must appear somewhere in the name (implicit wildcards between them)."""
        return [t for t in _re.split(r"[\s,;*]+", text.strip().lower()) if t]

    @staticmethod
    def _tokens_in_order(hay: str, tokens: "list[str]") -> bool:
        """True when every token occurs in `hay` in the order typed."""
        pos = 0
        for t in tokens:
            i = hay.find(t, pos)
            if i < 0:
                return False
            pos = i + len(t)
        return True

    @classmethod
    def _rank_pv_match(cls, disp: str, key: str, q) -> "int | None":
        """Sort weight of one suggestion against query `q` (lower = better), or
        None when it doesn't match at all. `q` may be raw text or a token list
        from _split_query.

        Multi-token queries are AND-matched: "023 l3" keeps only names holding
        both "023" and "l3" anywhere, i.e. what "*023*l3*" would mean — without
        having to type the stars. Tokens found in the typed order rank above the
        same tokens scrambled, so nothing is hidden by guessing the order wrong.

        Ranking exists because the archiver's "**" listing is ~9700 channels, of
        which ~3700 are camera channels (C03-013-PFM1NF:Gain …). Taking the first
        N raw substring hits therefore returned nothing but cameras for queries
        like "pcm" — the PV the user was after was hit #150. Field-name and
        prefix hits now win, and camera channels sink below everything else.
        """
        tokens = q if isinstance(q, (list, tuple)) else cls._split_query(q)
        if not tokens:
            return None
        d, k = disp.lower(), key.lower()
        field = k.rsplit(":", 1)[-1]
        worst = 0
        total = 0
        for t in tokens:
            if t in (d, k):
                s = 0
            elif field == t:
                s = 1
            elif field.startswith(t) or k.startswith(t) or d.startswith(t):
                s = 2
            elif t in field:
                s = 3
            elif t in d or t in k:
                s = 4
            else:
                return None          # AND semantics: one missing token = no hit
            worst = max(worst, s)
            total += s
        # Weakest token decides the tier; the sum only breaks ties, so a name
        # that matches every token well beats one that barely matches any.
        score = worst * 10 + min(total, 9)
        if len(tokens) > 1 and not (cls._tokens_in_order(k, tokens)
                                    or cls._tokens_in_order(d, tokens)):
            score += 5
        if _CAM_CHANNEL_RE.match(key):
            # Relative order is preserved when EVERY hit is a camera, so an
            # explicitly camera-targeted query is unaffected.
            score += 100
        return score

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
        if not scored:
            if self._chan_loading:
                # Without this the box looks empty-and-final while the ~9700
                # channel list is still downloading; _on_channels_loaded re-runs
                # the query when it lands.
                dropdown.insertRow(0)
                hint = QTableWidgetItem("loading PV list…")
                hint.setForeground(QColor("#777"))
                dropdown.setItem(0, 0, hint)
                row_h = max(22, dropdown.verticalHeader().defaultSectionSize())
                pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
                dropdown.setGeometry(pos.x(), pos.y(),
                                     max(300, anchor.width() + 20), row_h + 6)
                dropdown.show()
                dropdown.raise_()
                QTimer.singleShot(0, anchor.setFocus)
            return
        scored.sort(key=lambda t: (t[0], t[1]))
        shown = scored[:self._PV_DROPDOWN_MAX]
        for _s, _i, disp, key in shown:
            r = dropdown.rowCount()
            dropdown.insertRow(r)
            it = QTableWidgetItem(disp)
            it.setData(Qt.ItemDataRole.UserRole, key)
            dropdown.setItem(r, 0, it)
        if len(scored) > len(shown):
            r = dropdown.rowCount()
            dropdown.insertRow(r)
            # No UserRole → _on_pv_dropdown_clicked ignores this hint row.
            more = QTableWidgetItem(
                f"… {len(scored) - len(shown)} more matches — refine the search")
            more.setForeground(QColor("#777"))
            dropdown.setItem(r, 0, more)
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

    def _register_col(self, col: str) -> str:
        col = (col or "").strip()
        if col and col not in PV_COLUMNS and col not in self._custom_labels:
            self._custom_labels[col] = col   # arbitrary channel; label == name
        return col

    def _add_pv_col(self, col: str, filter_by: bool = True):
        """Add a PV to the list. New PVs are filter PVs by default — that is what
        the search box is normally used for; untick the row to only show it."""
        col = self._register_col(col)
        if not col or any(c["col"] == col for c in self._pv_cfg):
            return
        self._sync_pv_cfg_from_rows()
        self._pv_cfg.append({"col": col, "target": 10.0, "tol": 0.0,
                             "filter": bool(filter_by)})
        self._rebuild_pv_rows()

    def _remove_pv_col(self, col: str):
        self._sync_pv_cfg_from_rows()
        self._pv_cfg = [c for c in self._pv_cfg if c["col"] != col]
        self._rebuild_pv_rows()

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
            chk.setToolTip(
                f"{full_label}\n"
                "Ticked: search filters on this PV (target ± tolerance).\n"
                "Unticked: value is only shown in the results and on the images.")
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
            t_sb.setToolTip(f"Target value for {full_label}")
            tgt_l.addWidget(t_sb)
            pm_lbl = QLabel("±")
            pm_lbl.setStyleSheet("font-size: 10px;")
            tgt_l.addWidget(pm_lbl)
            tol_sb = QDoubleSpinBox()
            tol_sb.setRange(0.0, 1e9)
            tol_sb.setDecimals(3)
            tol_sb.setValue(cfg.get("tol", 0.0))
            tol_sb.setFixedWidth(50)
            tol_sb.setToolTip(f"Tolerance around the target for {full_label}")
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

    def _on_selection_changed(self):
        # Sending works with no selection too (then it sends every result), so the
        # button must not go dead the moment the selection is cleared.
        self._btn_open_slider.setEnabled(
            self._slider_ref is not None and self._tab_widget is not None
            and bool(self._day_results))

        # Preview při kliknutí na řádek
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not rows or not self._day_results:
            return
        r = rows[0]
        if r >= len(self._day_results):
            return
        dr = self._day_results[r]
        cam = dr.cam or self._active_cam
        if not cam or dr.hour_folder is None:
            return
        dt_obj = dr.best_row.get("_dt")
        if dt_obj is None:
            return
        ts_ns_direct = dr.best_row.get("_ns")
        self._preview_gen += 1
        gen = self._preview_gen
        gradient_name = self._gradient_cb.currentText()

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
            self._load_and_show_preview(img, energy_text, gen, gradient_name)

        threading.Thread(target=_resolve_and_load, daemon=True).start()

    def _load_and_show_preview(self, img_path: Path, energy_text: str, gen: int, gradient_name: str = ""):
        """Background thread: load and process image into QImage; QPixmap conversion on main thread."""
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

            img_max_val = _read_img_max_value(img_path)
            arr_px_max = float(arr_f.max())
            if img_max_val is not None and arr_px_max > 0:
                arr_f = img_max_val * arr_f / arr_px_max
            arr = _np.clip(arr_f / 4095.0 * 255.0, 0, 255).astype(_np.uint8)

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
                rgb = lut[arr]
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
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if rows:
            self._on_selection_changed()

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
        days = self._selected_days()
        if not days:
            return
        day = days[0]
        images_root = self._images_root
        if hasattr(self, "_cam_status_lbl"):
            self._cam_status_lbl.setText("Loading cameras…")

        def worker():
            seen: set[str] = set()
            cameras: list[str] = []
            base = _images_root_for_year(images_root, day.year) / str(day.year) / str(day.month) / str(day.day)

            def _scan_hour(h: int) -> list[str]:
                hour_dir = base / str(h)
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

            try:
                with ThreadPoolExecutor(max_workers=12) as _hex:
                    _futs = [_hex.submit(_scan_hour, h) for h in range(24)]
                    for _fut in as_completed(_futs):
                        try:
                            for _name in _fut.result():
                                if _name not in seen:
                                    seen.add(_name)
                                    cameras.append(_name)
                        except Exception:
                            pass
            except Exception as exc:
                self._sig_cam.log_msg.emit(f"Camera load error: {exc}")
            cameras.sort(key=str.lower)
            self._sig_cam.finished.emit(cameras)

        self._sig_cam = _CamLoadSignals()
        self._sig_cam.finished.connect(self._on_cameras_loaded)
        self._sig_cam.log_msg.connect(self._log)
        threading.Thread(target=worker, daemon=True).start()

    def _on_cameras_loaded(self, camera_names: list):
        self._all_cameras = []
        for name in camera_names:
            m = _re.match(r"^C\d{2}-(\d{2,3})-", name)
            num = m.group(1) if m else ""
            self._all_cameras.append((num, name))
        n = len(self._all_cameras)
        self._cam_status_lbl.setText(f"{n} cameras available." if n else "No cameras found.")

    def _on_cam_search_changed(self, text: str):
        """textEdited — volá se jen při skutečném psaní, ne programaticky.

        Cameras are matched against the archiver's image folder names for the
        selected day (self._all_cameras), NOT against the PV channel list. Same
        multi-token rule as the PV box: "023 nf" needs both parts, any order."""
        tokens = self._split_query(text)
        self._cam_dropdown.hide()
        self._cam_dropdown.setRowCount(0)
        if not tokens or not self._all_cameras:
            return
        matches = [(num, name) for num, name in self._all_cameras
                   if all(t in name.lower() or t in num.lower() for t in tokens)]
        if not matches:
            return
        for num, name in matches[:30]:
            r = self._cam_dropdown.rowCount()
            self._cam_dropdown.insertRow(r)
            self._cam_dropdown.setItem(r, 0, QTableWidgetItem(num))
            self._cam_dropdown.setItem(r, 1, QTableWidgetItem(name))

        n_rows = min(len(matches), 30)
        row_h = max(24, self._cam_dropdown.verticalHeader().defaultSectionSize())
        header_h = self._cam_dropdown.horizontalHeader().height()
        popup_h = min(n_rows * row_h + header_h + 6, 400)
        popup_w = max(320, self._cam_search.width() + 20)

        pos = self._cam_search.mapToGlobal(self._cam_search.rect().bottomLeft())
        self._cam_dropdown.setGeometry(pos.x(), pos.y(), popup_w, popup_h)
        self._cam_dropdown.show()
        self._cam_dropdown.raise_()
        # Tool window nezabírá focus — textfield zůstane aktivní
        QTimer.singleShot(0, self._cam_search.setFocus)

    def _on_cam_dropdown_clicked(self, index):
        r = index.row()
        num_item  = self._cam_dropdown.item(r, 0)
        name_item = self._cam_dropdown.item(r, 1)
        if not name_item:
            return
        num  = num_item.text() if num_item else ""
        name = name_item.text()
        self._cam_dropdown.hide()
        self._cam_search.clear()
        if any(n == name for _, n in self._selected_cameras):
            return
        self._selected_cameras.append((num, name))
        row = self._cam_selected.rowCount()
        self._cam_selected.insertRow(row)
        self._cam_selected.setItem(row, 0, QTableWidgetItem(num))
        self._cam_selected.setItem(row, 1, QTableWidgetItem(name))
        self._cam_selected.selectRow(row)
        self._active_cam = name

    def _on_cam_selected_clicked(self, index):
        r = index.row()
        item = self._cam_selected.item(r, 1)
        if item:
            self._active_cam = item.text()

    def _on_cam_remove(self):
        r = self._cam_selected.currentRow()
        if r < 0:
            return
        self._cam_selected.removeRow(r)
        if r < len(self._selected_cameras):
            self._selected_cameras.pop(r)
        if self._cam_selected.rowCount() > 0:
            self._cam_selected.selectRow(0)
            item = self._cam_selected.item(0, 1)
            self._active_cam = item.text() if item else None
        else:
            self._active_cam = None

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

        search_cols = [c["col"] for c in criteria]
        col = search_cols[0]  # primary column
        extra_cols = [c for c in self._show_cols() if c not in search_cols]

        # Convert UI-unit criteria to CSV units for each column
        def _to_csv_units(c_col, c_val):
            if c_col == "sbw4":
                return c_val / SBW4_TRANSMISSION
            elif c_col in MJ_COLUMNS:
                return c_val / 1000.0
            return c_val

        criteria_csv = []
        for crit in criteria:
            criteria_csv.append({
                "col":        crit["col"],
                "target_csv": _to_csv_units(crit["col"], crit["target"]),
                "tol_csv":    _to_csv_units(crit["col"], crit["tol"]),
                "target_ui":  crit["target"],
                "tol_ui":     crit["tol"],
            })

        self._day_results = []
        self._table.setRowCount(0)
        self._btn_open_slider.setEnabled(False)
        self._set_busy(True)
        self._prog.setVisible(True)
        self._prog.setRange(0, len(days))
        self._prog.setValue(0)
        self._search_running = True
        self._result_lbl.setText(f"Searching {len(days)} days…")

        self._sig = _SearchSignals()
        self._sig.progress.connect(self._prog.setValue)
        self._sig.result.connect(self._on_day_result)
        self._sig.done.connect(self._on_search_done)
        self._sig.log_msg.connect(self._log)

        cam = self._active_cam
        images_root  = self._images_root
        csv_root     = self._energy_csv_root

        _emit_log = self._sig.log_msg.emit

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
                        _emit_log(f"{day}: no data (API + CSV) — see lines above for details")
                        self._sig.result.emit(None)
                        self._sig.progress.emit(i + 1)
                        continue

                    self._sig.log_msg.emit(f"{day}: {len(rows)} samples")

                    primary_crit = criteria_csv[0]
                    day_col      = primary_crit["col"]
                    target_csv   = primary_crit["target_csv"]
                    day_tol_csv  = primary_crit["tol_csv"]

                    # Rows in tolerance: primary col within tol, AND all other criteria match
                    rows_in_tol = []
                    for row in rows:
                        raw_primary = row.get(day_col, "")
                        try:
                            v_primary = float(raw_primary)
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
                                v_sec = float(raw_sec)
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
                                    v = float(raw)
                                    total += abs(v - ct) / max(abs(ct), 1e-9)
                                except Exception:
                                    total += 1e6
                            return total
                        best = min(rows_in_tol, key=_norm_dist)

                    if best is None:
                        self._sig.log_msg.emit(f"{day}: no matching column found")
                        self._sig.result.emit(None)
                        self._sig.progress.emit(i + 1)
                        continue

                    raw_best = best.get(day_col, "")
                    try:
                        actual_best = float(raw_best)
                    except Exception:
                        actual_best = None

                    diff_best = abs(actual_best - target_csv) if actual_best is not None else None

                    # Hourová složka
                    dt_obj = best.get("_dt")
                    hour_folder = None
                    if dt_obj is not None:
                        hour_utc = _folder_hour_from_prague(dt_obj.hour, day)
                        hour_folder = _find_hour_folder(day, hour_utc, images_root=images_root)

                    # Resolve camera folder + matched image here (worker thread) —
                    # keeps SMB probing off the UI thread and lets the Folder cell
                    # select the exact file in Explorer.
                    best_ns = best.get("_ns")
                    folder_path = None
                    img_path = None
                    if hour_folder is not None:
                        if cam:
                            cam_folder = hour_folder / cam
                            if not cam_folder.exists():
                                try:
                                    for sub in hour_folder.iterdir():
                                        if sub.is_dir() and sub.name.lower() == cam.lower():
                                            cam_folder = sub
                                            break
                                except Exception:
                                    pass
                            try:
                                if cam_folder.exists() and cam_folder.is_dir():
                                    folder_path = cam_folder
                                    if dt_obj is not None:
                                        img_path = _find_image_for_ts(
                                            cam_folder, dt_obj, ts_ns_override=best_ns)
                            except Exception:
                                pass
                        if folder_path is None:
                            folder_path = hour_folder

                    # Display values for every searched + also-show PV at the best
                    # shot — resolved HERE so the UI thread never hits the network
                    # and the table shows exactly what the search matched on.
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

                    result = {
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
                        "cam":          cam,
                        "extra_cols":   extra_cols,
                        "search_cols":  search_cols,
                        "per_col":      per_col,
                        "col_meta":     col_meta,
                        "criteria_csv": criteria_csv,
                        "display_vals": display_vals,
                    }
                    self._sig.log_msg.emit(
                        f"{day}: best={_format_value(day_col, raw_best)} "
                        f"in_tol={len(rows_in_tol)}")
                    self._sig.result.emit(result)

                except Exception as e:
                    self._sig.log_msg.emit(f"{day}: error — {e}")
                    self._sig.result.emit(None)

                self._sig.progress.emit(i + 1)

            self._sig.done.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_day_result(self, result):
        if result is None:
            return

        day: date         = result["day"]
        best_row: dict    = result["best_row"]
        rows_in_tol: list = result["rows_in_tol"]
        col: str          = result["col"]
        hour_folder       = result["hour_folder"]
        cam               = result["cam"]
        dt_obj: datetime = best_row.get("_dt")
        if dt_obj is None:
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
            img_path=result.get("img_path"))
        self._day_results.append(dr)

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
                v = float(raw)
            except (ValueError, TypeError):
                return None, (cpva.PV_TEXT_ERROR if state == "error"
                              else cpva.PV_TEXT_NOT_FOUND)
            d = abs(v - target_csv)
            if cc == "sbw4":
                return d * SBW4_TRANSMISSION, f"{d * SBW4_TRANSMISSION:.4f} J"
            if cc in MJ_COLUMNS:
                return d * 1000, f"{d * 1000:.2f} mJ"
            if cc == "waveplate":
                return d, f"{d:.0f}"
            if cc in PV_COLUMNS:
                return d, f"{d:.4f} J"
            return d, f"{d:.4g}"

        # PV / Δ columns keep one line per search PV; the Value cell is a single
        # combined line with EVERY searched PV (+ also-show extras).
        pv_lines, val_parts, diff_lines, off_pvs = [], [], [], []
        for crit in criteria_csv_res:
            cc = crit["col"]
            short = self._col_short(cc)
            pv_lines.append(short)
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
        if off_pvs:
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

        r = self._table.rowCount()
        self._table.insertRow(r)
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
            self._table.setItem(r, c, item)
        # Row height scales with the number of stacked lines
        n_lines = max(len(pv_lines), len(diff_lines), 1)
        if n_lines > 1:
            self._table.setRowHeight(r, 18 * n_lines + 8)

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
        """Double-click na řádek — zobraz všechny shoty daného dne v dialogu."""
        r = index.row()
        if r < 0 or r >= len(self._day_results):
            return
        dr = self._day_results[r]

        col = dr.col
        rows_in_tol = dr.rows_in_tol
        target_csv = dr.target_csv

        if not rows_in_tol:
            QMessageBox.information(self, "No shots",
                f"No shots within tolerance for {dr.day}.")
            return

        # Dialog se seznamem shotů
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Shots in range — {dr.day}")
        dlg.resize(600, 300)
        lay = QVBoxLayout(dlg)

        lbl = QLabel(f"{len(rows_in_tol)} shot(s) in range on {dr.day}:")
        lay.addWidget(lbl)

        # Columns come from the SEARCH-TIME state stored on dr — the left panel
        # may have changed since this search ran and must not affect old rows.
        criteria = dr.criteria_csv or [{"col": col, "target_csv": target_csv}]
        extra_cols_dlg = [c for c in dr.extra_cols
                          if c not in {cr["col"] for cr in criteria}]

        headers = ["Prague Time"]
        for cr in criteria:
            short = self._col_short(cr["col"])
            headers += [short, f"Δ {short}"]
        headers += [self._col_short(c) for c in extra_cols_dlg]

        tbl = QTableWidget(0, len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        for h_idx in range(len(headers)):
            tbl.horizontalHeader().setSectionResizeMode(
                h_idx, QHeaderView.ResizeMode.ResizeToContents)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        def _row_col_value(row, cc, row_ns):
            raw = row.get(cc, "")
            if raw:
                return raw, "ok"
            return _lookup_col_value(dr.per_col, dr.col_meta, cc, row_ns,
                                     tol_s=EXTRA_COL_MATCH_TOL_S,
                                     allow_network=False)

        for row in rows_in_tol:
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
                    diff_str = _format_diff(cc, abs(float(raw_cc) - cr["target_csv"]))
                except (ValueError, TypeError):
                    diff_str = (cpva.PV_TEXT_ERROR if state_cc == "error"
                                else cpva.PV_TEXT_NOT_FOUND)
                tbl.setItem(r2, c_idx + 1, QTableWidgetItem(diff_str))
                c_idx += 2
            for ec in extra_cols_dlg:
                raw_ec, state_ec = _row_col_value(row, ec, row_ns)
                tbl.setItem(r2, c_idx, QTableWidgetItem(
                    _format_value_state(ec, raw_ec, state_ec)))
                c_idx += 1

        lay.addWidget(tbl, 1)

        cam_folder_ref = [None]
        # Zjisti cam_folder pro preview — camera captured at search time
        cam = dr.cam or self._active_cam
        if cam and dr.hour_folder is not None:
            cf = dr.hour_folder / cam
            if not cf.exists():
                try:
                    for sub in dr.hour_folder.iterdir():
                        if sub.is_dir() and sub.name.lower() == cam.lower():
                            cf = sub
                            break
                except Exception:
                    pass
            if cf.exists():
                cam_folder_ref[0] = cf

        def _on_dlg_row_selected():
            sel = tbl.selectedIndexes()
            if not sel or cam_folder_ref[0] is None:
                return
            i = sel[0].row()
            if i >= len(rows_in_tol):
                return
            row = rows_in_tol[i]
            dt_obj2 = row.get("_dt")
            if dt_obj2 is None:
                return
            img2 = _find_image_for_ts(cam_folder_ref[0], dt_obj2,
                                       ts_ns_override=row.get("_ns"))
            if img2:
                # Full multi-PV caption (search + also-show), same as main table
                energy2 = self._build_energy_text(dr, row, row.get("_ns"))
                _gen2 = self._preview_gen + 1
                self._preview_gen = _gen2
                _gname2 = self._gradient_cb.currentText()
                threading.Thread(
                    target=lambda p=img2, e=energy2, g=_gen2, gn=_gname2: self._load_and_show_preview(p, e, g, gn),
                    daemon=True).start()

        tbl.selectionModel().selectionChanged.connect(_on_dlg_row_selected)

        btn_row = QHBoxLayout()
        btn_open = QPushButton("▶ Open selected in Slider")
        btn_open.setStyleSheet(
            "QPushButton { background: #2d7dff; color: #fff; font-weight: 700; "
            "border-radius: 4px; padding: 5px 10px; }"
            "QPushButton:hover { background: #1a6aee; }")
        btn_save_img = QPushButton("Save image")
        btn_close = QPushButton("Close")
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_save_img)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

        btn_close.clicked.connect(dlg.reject)

        def save_selected_image():
            sel = tbl.selectedIndexes()
            if not sel:
                QMessageBox.information(dlg, "No selection", "Vyberte řádek v tabulce.")
                return
            i = sel[0].row()
            if i >= len(rows_in_tol):
                return
            if cam_folder_ref[0] is None:
                QMessageBox.warning(dlg, "No camera", "Složka kamery není dostupná.")
                return
            row_s = rows_in_tol[i]
            dt_obj_s = row_s.get("_dt")
            if dt_obj_s is None:
                QMessageBox.warning(dlg, "No timestamp", "Řádek nemá časové razítko.")
                return
            src = _find_image_for_ts(cam_folder_ref[0], dt_obj_s,
                                      ts_ns_override=row_s.get("_ns"))
            if src is None:
                QMessageBox.warning(dlg, "Image not found", "Obrázek nebyl nalezen.")
                return
            initial_dir = str(self._last_save_dir) if self._last_save_dir else str(Path.home())
            dst, _ = QFileDialog.getSaveFileName(
                dlg, "Uložit obrázek", str(Path(initial_dir) / src.name),
                "Images (*.png *.jpg *.tif *.tiff *.bmp);;All files (*)",
                options=QFileDialog.Option.DontUseNativeDialog)
            if not dst:
                return
            dst_path = Path(dst)
            self._last_save_dir = dst_path.parent
            try:
                import shutil as _sh
                _sh.copy2(src, dst_path)
            except Exception as ex:
                QMessageBox.critical(dlg, "Chyba", f"Kopírování selhalo:\n{ex}")

        btn_save_img.clicked.connect(save_selected_image)

        def open_selected():
            selected_rows = tbl.selectedIndexes()
            if not selected_rows:
                sel_rows_in_tol = rows_in_tol
            else:
                idxs = sorted(set(i.row() for i in selected_rows))
                sel_rows_in_tol = [rows_in_tol[i] for i in idxs]

            cam = dr.cam or self._active_cam
            if not cam:
                QMessageBox.warning(dlg, "No camera", "Select a camera first.")
                return

            hour_folder = dr.hour_folder
            if hour_folder is None:
                QMessageBox.warning(dlg, "No folder", "Hour folder not found.")
                return

            cam_folder = hour_folder / cam
            if not cam_folder.exists():
                try:
                    for sub in hour_folder.iterdir():
                        if sub.is_dir() and sub.name.lower() == cam.lower():
                            cam_folder = sub
                            break
                except Exception:
                    pass

            if not cam_folder.exists():
                QMessageBox.warning(dlg, "Camera not found",
                    f"Camera folder '{cam}' not found.")
                return

            files = []
            for row in sel_rows_in_tol:
                dt_obj = row.get("_dt")
                if dt_obj is None:
                    continue
                img = _find_image_for_ts(cam_folder, dt_obj,
                                         ts_ns_override=row.get("_ns"))
                if img:
                    files.append((img, row))

            if not files:
                QMessageBox.warning(dlg, "No images", "No matching images found.")
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
                QMessageBox.warning(dlg, "Copy failed", "No images copied.")
                return

            dlg.accept()
            if self._tab_widget:
                self._tab_widget.setCurrentIndex(1)
            if self._slider_ref:
                recv = getattr(self._slider_ref, "receive_external_folder", None)
                if callable(recv):
                    recv(temp_path, energy_map=energy_map, discrete=True,
                         cam_name=cam)
                else:
                    self._slider_ref._sf_energy_map = energy_map
                    self._slider_ref.open_folder_path(temp_path)
            if prev_temp_dir:
                try:
                    shutil.rmtree(prev_temp_dir, ignore_errors=True)
                except Exception:
                    pass

        btn_open.clicked.connect(open_selected)
        dlg.exec()

    def _on_search_done(self):
        self._search_running = False
        self._set_busy(False)
        self._prog.setVisible(False)
        n = self._table.rowCount()
        self._result_lbl.setText(f"Results: {n} day(s) matched")
        self._log(f"Search done — {n} results")
        # _set_busy(False) re-enables every control, so the send button must be
        # re-evaluated: nothing to send when the search found nothing.
        self._btn_open_slider.setEnabled(n > 0 and self._slider_ref is not None
                                         and self._tab_widget is not None)
        if n > 0:
            self._btn_save_results.setEnabled(True)
            self._btn_send_workshop.setEnabled(True)
            self._btn_send_workshop.setVisible(True)

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

        # Jeden soubor za každý den
        files_to_copy: list[Path] = []
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
            # Fast path: the search worker already resolved the matched image
            if dr.img_path is not None:
                files_to_copy.append(Path(dr.img_path))
                self._log(f"{dr.day}: ✓ {Path(dr.img_path).name}")
                continue

            if dr.hour_folder is None:
                self._log(f"{dr.day}: no hour folder, skipping")
                continue

            dr_cam = dr.cam or cam
            if not dr_cam:
                self._log(f"{dr.day}: no camera for this result, skipping")
                continue
            cam_folder = dr.hour_folder / dr_cam
            if not cam_folder.exists():
                try:
                    for sub in dr.hour_folder.iterdir():
                        if sub.is_dir() and sub.name.lower() == dr_cam.lower():
                            cam_folder = sub
                            break
                except Exception:
                    pass

            if not cam_folder.exists():
                self._log(f"{dr.day}: camera {dr_cam} not found")
                continue

            dt_obj = dr.best_row.get("_dt")
            if dt_obj is None:
                continue

            img = _find_image_for_ts(cam_folder, dt_obj,
                                     ts_ns_override=dr.best_row.get("_ns"))
            if img is not None:
                files_to_copy.append(img)
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

        copied = 0
        copied_files: list[tuple[Path, Path, int]] = []  # (src, dst, dr_index)
        for i, src in enumerate(files_to_copy):
            try:
                dst = temp_path / src.name
                if dst.exists():
                    dst = temp_path / f"{src.stem}_{copied}{src.suffix}"
                shutil.copy2(src, dst)
                copied_files.append((src, dst, i))
                copied += 1
            except Exception as e:
                self._log(f"Copy error {src.name}: {e}")

        if copied == 0:
            QMessageBox.warning(self, "Copy failed", "Could not copy images.")
            return

        self._log(f"Copied {copied} images → {self._temp_dir}")

        # Sestav energy map — filename -> text pro zobrazení v slideru.
        # PVs come from each result's search-time state (dr), not the live panel.
        energy_map: dict[str, str] = {}
        for src, dst, i in copied_files:
            if i >= len(results_to_open):
                continue
            dr = results_to_open[i]
            best_ns = dr.best_row.get("_ns")
            energy_map[dst.name] = self._build_energy_text(dr, dr.best_row, best_ns)

        # Hand over through receive_external_folder: it clears whatever the Slider
        # was set to (multi-cam grid, live mode, subtraction reference, focus mode,
        # a previous energy map) so the images always land. Falls back to the plain
        # entry point when running against an older is_t.py.
        send_cam = next((dr.cam for dr in results_to_open if dr.cam), cam)
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

        cam = self._active_cam
        if not cam:
            QMessageBox.warning(self, "No camera selected",
                "Please select a camera first.")
            return

        from PySide6.QtWidgets import QFileDialog
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
                cam_folder = dr.hour_folder / dr_cam
                if not cam_folder.exists():
                    try:
                        for sub in dr.hour_folder.iterdir():
                            if sub.is_dir() and sub.name.lower() == dr_cam.lower():
                                cam_folder = sub
                                break
                    except Exception:
                        pass

                if not cam_folder.exists():
                    self._log(f"{dr.day}: camera {dr_cam} not found")
                    errors += 1
                    continue

                img = _find_image_for_ts(cam_folder, dt_obj,
                                         ts_ns_override=dr.best_row.get("_ns"))
            if img is None:
                self._log(f"{dr.day}: no image near {dt_obj.strftime('%H:%M:%S')}")
                errors += 1
                continue

            # Název souboru: camera_label + Prague timestamp + PV value
            val_str = _format_value(dr.col, dr.best_row.get(dr.col, "")).replace(" ", "").replace("/", "-")
            short = PV_COLUMNS.get(dr.col, dr.col).split(" [")[0]
            _cam_label = _clean_cam_for_filename(cam)
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

                # Load via PIL to preserve 16-bit and apply imgMaxValue normalization
                try:
                    _pil_raw = _PilImg.open(str(img))
                    if _pil_raw.mode in ("I", "I;16"):
                        _arr_f = _np.array(_pil_raw, dtype=_np.float32)
                    elif _pil_raw.mode in ("RGB", "RGBA"):
                        _arr_f = _np.array(_pil_raw.convert("L"), dtype=_np.float32)
                    else:
                        _arr_f = _np.array(_pil_raw.convert("L"), dtype=_np.float32)
                    _img_max_val = _read_img_max_value(img)
                    _arr_px_max = float(_arr_f.max())
                    if _img_max_val is not None and _arr_px_max > 0:
                        _arr_f = _img_max_val * _arr_f / _arr_px_max
                    arr = _np.clip(_arr_f / 4095.0 * 255.0, 0, 255).astype(_np.uint8)
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
                    rgb = lut[arr]
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

            img_max_val = _read_img_max_value(img_path)
            arr_px_max = float(arr_f.max())
            if img_max_val is not None and arr_px_max > 0:
                arr_f = img_max_val * arr_f / arr_px_max
            arr8 = _np.clip(arr_f / 4095.0 * 255.0, 0, 255).astype(_np.uint8)

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