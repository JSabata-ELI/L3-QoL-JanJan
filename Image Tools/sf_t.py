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
import tempfile
import atexit
import threading
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
    QSpinBox, QCheckBox,
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

# ── CONFIG ────────────────────────────────────────────────────────────────────
IMAGES_ROOT = Path(r"//users-L3.tier0.lcs.local/cpva-image-2026")

# CSV fallback — same root / format as Image Finder
ENERGY_CSV_ROOT     = r"//hapls-share.cs.eli-beams.eu/scratch/Salvation/2026_alldata"
ENERGY_CSV_NAME_FMT = "dataof%Y%b_%d"   # e.g. dataof2026Mar_24
# Tolerance for closest-timestamp extra-column matching (seconds)
EXTRA_COL_MATCH_TOL_S = 5.0

# ── CPVA ARCHIVER API ─────────────────────────────────────────────────────────
CPVA_BASE_URL     = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_HTTP_TIMEOUT = 15.0   # seconds per channel request

# Maps PV column name → CPVA archiver channel name.
# Channels not listed here (waveplate, Back_Ref) have no archiver channel
# and will return no data.
CPVA_CHANNEL_MAP: dict[str, str] = {
    "ptm1":     "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "pcm2":     "HAPLS-ENER_IN_PCM2_LT7_DIAG2:Energy",
    "pcm4":     "HAPLS-ENER_IN_PCM4_LT7_DIAG2:Energy",
    "pap1":     "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "sbw4":     "HAPLS-ENER_IN_SBW4_LT7_DIAG2:Energy",
}

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

SBW4_TRANSMISSION        = 0.749
SBW4_WARNING_THRESHOLD_J = 0.5

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _cpva_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                        timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    """Fetch archiver samples for one channel over [start_ns, end_ns]."""
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    })
    url = f"{CPVA_BASE_URL}/samples?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_cpva_ssl_ctx()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_csv_for_day(day: date, cols: "list[str]") -> "tuple[list[dict], dict[str, list[dict]]]":
    """
    Load energy data from daily CSV file (same format as Image Finder).
    Returns (merged_rows, per_col_rows) where per_col_rows maps col → sorted list of
    {"_dt": datetime, "_ns": int|None, col: str_value} dicts.
    Returns ([], {}) if file not found or unreadable.
    """
    ref_dt = datetime(day.year, day.month, day.day)
    fname  = ref_dt.strftime(ENERGY_CSV_NAME_FMT) + ".csv"
    csv_path = Path(ENERGY_CSV_ROOT) / fname
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


def _load_api_for_day(day: date, cols: "list[str]") -> "tuple[list[dict], dict[str, list[dict]]]":
    """
    Query the CPVA archiver for all requested PV columns over the full day.
    Falls back to CSV if API returns no data for a column.

    Returns (merged_rows, per_col_rows) where:
      - merged_rows: list of row dicts merged by timestamp across all channels
      - per_col_rows: dict mapping col → sorted list of single-col row dicts
        (used for closest-timestamp extra-column matching)

    Each row dict has:
      "_dt" : datetime (Prague-naive)
      "_ns" : int (UTC nanoseconds)
      "<col>": str value
    """
    if PRAGUE is None:
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end   = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        day_start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=PRAGUE)
        day_end   = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=PRAGUE)

    start_ns = int(day_start.timestamp() * 1_000_000_000)
    end_ns   = int(day_end.timestamp()   * 1_000_000_000)

    # Per-column sample lists (API first, CSV fallback per column)
    per_col: dict[str, list[dict]] = {}

    for col in cols:
        channel = CPVA_CHANNEL_MAP.get(col)
        col_rows: list[dict] = []
        if channel is not None:
            try:
                samples = _cpva_fetch_samples(channel, start_ns, end_ns)
                if isinstance(samples, list):
                    for s in samples:
                        t_ns = s.get("time")
                        if t_ns is None:
                            continue
                        val = s.get("value")
                        if isinstance(val, list):
                            val = val[0] if len(val) == 1 else None
                        if val is None:
                            continue
                        t_ns = int(t_ns)
                        if PRAGUE is not None:
                            dt_local = datetime.fromtimestamp(
                                t_ns / 1e9, tz=timezone.utc).astimezone(PRAGUE).replace(tzinfo=None)
                        else:
                            dt_local = datetime.utcfromtimestamp(t_ns / 1e9)
                        col_rows.append({"_dt": dt_local, "_ns": t_ns, col: str(val)})
                    col_rows.sort(key=lambda r: r["_dt"])
            except Exception:
                pass
        # CSV fallback when API returned nothing for this column
        if not col_rows:
            _, csv_per = _load_csv_for_day(day, [col])
            col_rows = csv_per.get(col, [])
        if col_rows:
            per_col[col] = col_rows

    # Merge all per-col rows into a single list keyed by _ns
    by_ts: dict[int, dict] = {}
    for col, col_rows in per_col.items():
        for r in col_rows:
            t_ns = r["_ns"]
            if t_ns not in by_ts:
                by_ts[t_ns] = {"_dt": r["_dt"], "_ns": t_ns}
            by_ts[t_ns][col] = r[col]

    merged = [by_ts[k] for k in sorted(by_ts)]
    return merged, per_col


def _find_closest_col_value(per_col: "dict[str, list[dict]]", col: str,
                             target_ns: int, tol_s: float = EXTRA_COL_MATCH_TOL_S) -> str:
    """
    Find the closest-timestamp value for `col` within tol_s seconds of target_ns.
    Returns formatted value string or "—" if no match.
    """
    rows = per_col.get(col)
    if not rows:
        return "—"
    ts_list = [r["_ns"] for r in rows]
    idx = bisect.bisect_left(ts_list, target_ns)
    best = None
    best_diff = float("inf")
    for i in [idx - 1, idx]:
        if 0 <= i < len(rows):
            diff = abs(rows[i]["_ns"] - target_ns)
            if diff < best_diff:
                best_diff = diff
                best = rows[i]
    tol_ns = int(tol_s * 1_000_000_000)
    if best is not None and best_diff <= tol_ns:
        return best.get(col, "—")
    return "—"


def _find_best_match(rows: list[dict], col: str, target: float) -> dict | None:
    best = None
    best_diff = float("inf")
    target_csv = target / SBW4_TRANSMISSION if col == "sbw4" else target
    for row in rows:
        raw = row.get(col, "")
        try:
            val = float(raw)
        except (ValueError, TypeError):
            continue
        diff = abs(val - target_csv)
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


def _find_hour_folder(day: date, hour_utc: int) -> Path | None:
    base = IMAGES_ROOT / str(day.year) / str(day.month) / str(day.day)
    for delta in [0, -1, 1, -2, 2]:
        h = (hour_utc + delta) % 24
        candidate = base / str(h)
        if candidate.exists() and candidate.is_dir():
            return candidate
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

    if best_file is not None and best_diff < 5_000_000_000:
        return best_file
    return None


def _format_value(col: str, raw: str) -> str:
    try:
        v = float(raw)
        if col in MJ_COLUMNS:
            return f"{v * 1000:.2f} mJ"
        if col == "sbw4":
            return f"{v * SBW4_TRANSMISSION:.4f} J"
        return f"{v:.4f} J" if col != "waveplate" else f"{v:.0f}"
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
    show = Signal(object, str, int)  # (Path, energy_text, gen)

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
                 per_col: "dict | None" = None):
        self.day          = day
        self.best_row     = best_row
        self.col          = col
        self.actual       = actual
        self.diff         = diff
        self.target_csv   = target_csv
        self.hour_folder  = hour_folder
        self.rows_in_tol  = rows_in_tol or []
        self.per_col      = per_col or {}
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

        self._temp_dir: str | None = None
        self._preview_pixmap_orig = None
        self._preview_gen = 0
        self._preview_sig = _PreviewSignals()
        self._preview_sig.show.connect(self._show_preview)
        atexit.register(self._cleanup_temp)

        self._build_ui()

    def _cleanup_temp(self):
        if self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # _PreviewWidget se překresluje sám přes paintEvent — nic navíc nepotřebujeme

    def _show_preview(self, img_path: Path, energy_text: str = "", gen: int = 0):
        """Zobraz náhled obrázku v preview panelu."""
        if gen != 0 and gen != self._preview_gen:
            return
        try:
            from PySide6.QtGui import QImageReader, QImage
            import numpy as _np

            reader = QImageReader(str(img_path))
            reader.setAutoTransform(True)
            qimg = reader.read()
            if qimg.isNull():
                self._preview_widget.set_pixmap(None)
                return

            if qimg.format() != QImage.Format.Format_Grayscale8:
                qimg = qimg.convertToFormat(QImage.Format.Format_Grayscale8)

            w, h = qimg.width(), qimg.height()
            ptr = qimg.bits()
            if hasattr(ptr, "setsize"):
                ptr.setsize(qimg.sizeInBytes())
            arr = _np.frombuffer(ptr, dtype=_np.uint8).reshape(
                h, qimg.bytesPerLine())[:, :w].copy()

            # Autostretch
            lo, hi = _np.percentile(arr, [0.1, 99.9])
            if hi > lo + 2:
                arr = _np.clip(
                    (arr.astype(_np.float32) - lo) / (hi - lo) * 255.0,
                    0, 255).astype(_np.uint8)

            # Aplikuj gradient
            try:
                _is_mod = _sys.modules.get("image_slider")
                if _is_mod and hasattr(_is_mod, "GRADIENTS"):
                    lut = _is_mod.GRADIENTS.get(self._gradient_cb.currentText())
                else:
                    lut = SF_GRADIENTS.get(self._gradient_cb.currentText())
            except Exception:
                lut = SF_GRADIENTS.get(self._gradient_cb.currentText(), None)

            if lut is not None:
                rgb = lut[arr]
                out_img = QImage(rgb.tobytes(), w, h, w * 3,
                                 QImage.Format.Format_RGB888)
            else:
                out_img = QImage(arr.tobytes(), w, h, w,
                                 QImage.Format.Format_Grayscale8)

            from PySide6.QtGui import QPixmap, QPainter, QFont, QColor
            from PySide6.QtCore import QRect
            pm = QPixmap.fromImage(out_img)

            if energy_text:
                from PySide6.QtGui import QPixmap, QPainter, QFont, QColor, QFontMetrics
                from PySide6.QtCore import QRect
                available_w = pm.width() - 20
                font = QFont()
                display_text = energy_text

                # Zkus vejít na jeden řádek
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

            from PySide6.QtGui import QPixmap
            self._preview_pixmap_orig = pm
            self._rescale_preview()
            return

        except Exception:
            self._preview_widget.set_pixmap(None)

    # ── LOGGING ───────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        for btn in [self._btn_search, self._btn_open_slider, self._btn_save_results,
                    self._gradient_cb, self._cal_from, self._cal_to]:
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

        # Date range
        ll.addWidget(_group_label("Date range"))
        ll.addWidget(QLabel("From:"))
        self._cal_from = _NoScrollCalendar()
        self._setup_calendar(self._cal_from)
        self._cal_from.selectionChanged.connect(self._on_date_changed)
        ll.addWidget(self._cal_from)

        ll.addWidget(QLabel("To:"))
        self._cal_to = _NoScrollCalendar()
        self._setup_calendar(self._cal_to)
        self._cal_to.selectionChanged.connect(self._on_date_changed)
        ll.addWidget(self._cal_to)

        self._date_info_lbl = QLabel("")
        self._date_info_lbl.setStyleSheet("font-size: 10px; color: #555;")
        ll.addWidget(self._date_info_lbl)
        ll.addWidget(_hsep())

        # PV selector
        ll.addWidget(_group_label("Search criteria"))
        pv_grid = QGridLayout()
        pv_grid.setSpacing(2)
        self._pv_buttons: dict[str, QCheckBox] = {}
        for i, (col, label) in enumerate(PV_COLUMNS.items()):
            short = label.split(" [")[0]
            cb = QCheckBox(short)
            cb.setToolTip(label)
            cb.setStyleSheet("QCheckBox { font-size: 10px; padding: 1px; }")
            self._pv_buttons[col] = cb
            pv_grid.addWidget(cb, i // 3, i % 3)
            cb.stateChanged.connect(lambda state, c=col: self._on_pv_changed_rb(c, bool(state)))
        list(self._pv_buttons.values())[0].setChecked(True)
        ll.addLayout(pv_grid)

        ll.addWidget(QLabel("Also show:"))
        self._extra_pv_checks: dict[str, QCheckBox] = {}
        extra_grid = QGridLayout()
        extra_grid.setSpacing(2)
        for i, (col, label) in enumerate(PV_COLUMNS.items()):
            short = label.split(" [")[0]
            cb = QCheckBox(short)
            cb.setStyleSheet("QCheckBox { font-size: 10px; padding: 1px; }")
            cb.setToolTip(f"Also show {label} in results")
            self._extra_pv_checks[col] = cb
            extra_grid.addWidget(cb, i // 3, i % 3)
        ll.addLayout(extra_grid)

        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel("Target:"))
        self._target_sb = QDoubleSpinBox()
        self._target_sb.setRange(-1e9, 1e9)
        self._target_sb.setDecimals(3)
        self._target_sb.setValue(10.0)
        self._target_sb.setFixedWidth(72)
        crit_row.addWidget(self._target_sb)
        self._unit_lbl = QLabel("J")
        crit_row.addWidget(self._unit_lbl)
        self._pm_lbl = QLabel("±")
        crit_row.addWidget(self._pm_lbl)
        self._tol_sb = QDoubleSpinBox()
        self._tol_sb.setRange(0.001, 100.0)
        self._tol_sb.setDecimals(2)
        self._tol_sb.setValue(SBW4_WARNING_THRESHOLD_J)
        self._tol_sb.setFixedWidth(58)
        self._tol_sb.setToolTip("Tolerance ± J")
        crit_row.addWidget(self._tol_sb)
        self._tol_unit_lbl = QLabel("J")
        crit_row.addWidget(self._tol_unit_lbl)
        ll.addLayout(crit_row)
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
        self._btn_open_slider = QPushButton("▶  Open in Image Slider")
        self._btn_open_slider.setEnabled(False)
        self._btn_open_slider.setVisible(False)
        self._btn_open_slider.setToolTip(
            "Copy one matched image per day to temp folder and open in Slider.")
        self._btn_open_slider.clicked.connect(self._open_in_slider)
        ll.addWidget(self._btn_open_slider)

        self._btn_save_results = QPushButton("💾  Save images")
        self._btn_save_results.setEnabled(False)
        self._btn_save_results.setToolTip(
            "Save matched images to a selected folder.")
        self._btn_save_results.clicked.connect(self._save_results)
        ll.addWidget(self._btn_save_results)

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
        rl.addWidget(self._table, 1)

        root_layout.addWidget(left_scroll)
        root_layout.addWidget(rw, 1)

        # Preview panel
        self._preview_widget = _PreviewWidget()
        self._preview_widget.setMinimumHeight(200)
        root_layout.addWidget(self._preview_widget, 1)

        # Init
        self._on_pv_changed_rb("sbw4", True)
        self._update_date_info()
        QTimer.singleShot(300, self._load_cameras)

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

    def _on_pv_changed_rb(self, col: str, checked: bool):
        if not hasattr(self, "_unit_lbl"):
            return
        # Zjisti všechny aktuálně vybrané sloupce
        selected = [c for c, cb in self._pv_buttons.items() if cb.isChecked()]
        if not selected:
            return
        # Jednotky ukazuj podle prvního vybraného
        first = selected[0]
        if first in MJ_COLUMNS:
            self._unit_lbl.setText("mJ")
            self._tol_unit_lbl.setText("mJ")
        elif first == "waveplate":
            self._unit_lbl.setText("—")
            self._tol_unit_lbl.setText("—")
        else:
            self._unit_lbl.setText("J")
            self._tol_unit_lbl.setText("J")

    def _update_date_info(self):
        days = self._selected_days()
        n = len(days)
        if n == 0:
            self._date_info_lbl.setText("⚠ From > To")
        elif n == 1:
            self._date_info_lbl.setText("1 day selected")
        else:
            self._date_info_lbl.setText(f"{n} days selected")

    def _on_selection_changed(self):
        has_sel = bool(self._table.selectedItems())
        self._btn_open_slider.setEnabled(
            has_sel and self._slider_ref is not None and self._tab_widget is not None
            and bool(self._day_results))

        # Preview při kliknutí na řádek
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not rows or not self._day_results:
            return
        r = rows[0]
        if r >= len(self._day_results):
            return
        dr = self._day_results[r]
        cam = self._active_cam
        if not cam or dr.hour_folder is None:
            return
        cam_folder = dr.hour_folder / cam
        if not cam_folder.exists():
            try:
                for sub in dr.hour_folder.iterdir():
                    if sub.is_dir() and sub.name.lower() == cam.lower():
                        cam_folder = sub
                        break
            except Exception:
                return
        dt_obj = dr.best_row.get("_dt")
        if dt_obj is None:
            return
        ts_ns_direct = dr.best_row.get("_ns")
        img = _find_image_for_ts(cam_folder, dt_obj, ts_ns_override=ts_ns_direct)
        if img is None:
            self._preview_widget.set_pixmap(None)
            return

        col_search = next(
            (c for c, cb in self._pv_buttons.items() if cb.isChecked()), "sbw4")
        extra_cols = [c for c, cb in self._extra_pv_checks.items()
                      if cb.isChecked() and c != col_search]
        parts = []
        val_main = _format_value(dr.col, dr.best_row.get(dr.col, ""))
        short_main = PV_COLUMNS.get(dr.col, dr.col).split(" [")[0]
        parts.append(f"{short_main}: {val_main}")
        if ts_ns_direct is not None:
            for ec in extra_cols:
                raw_ec = _find_closest_col_value(dr.per_col, ec, ts_ns_direct)
                ev = _format_value(ec, raw_ec)
                short = PV_COLUMNS.get(ec, ec).split(" [")[0]
                parts.append(f"{short}: {ev}")
        energy_text = "  |  ".join(parts)

        self._preview_gen += 1
        gen = self._preview_gen
        threading.Thread(
            target=lambda: self._load_and_show_preview(img, energy_text, gen),
            daemon=True).start()

    def _load_and_show_preview(self, img_path: Path, energy_text: str, gen: int):
        if gen != self._preview_gen:
            return
        self._preview_sig.show.emit(img_path, energy_text, gen)

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

    def _selected_days(self) -> list[date]:
        d_from = self._qdate_to_date(self._cal_from.selectedDate())
        d_to   = self._qdate_to_date(self._cal_to.selectedDate())
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
        if hasattr(self, "_cam_status_lbl"):
            self._cam_status_lbl.setText("Loading cameras…")

        def worker():
            cameras = []
            base = IMAGES_ROOT / str(day.year) / str(day.month) / str(day.day)
            try:
                for h in range(8, 21):
                    hour_dir = base / str(h)
                    if hour_dir.exists() and hour_dir.is_dir():
                        try:
                            subs = sorted(
                                [p.name for p in hour_dir.iterdir() if p.is_dir()],
                                key=str.lower)
                            if subs:
                                cameras = subs
                                break
                        except Exception:
                            continue
            except Exception as e:
                self._sig_cam.log_msg.emit(f"Camera load error: {e}")
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
        """textEdited — volá se jen při skutečném psaní, ne programaticky."""
        q = text.strip().lower()
        self._cam_dropdown.hide()
        self._cam_dropdown.setRowCount(0)
        if not q or not self._all_cameras:
            return
        matches = [(num, name) for num, name in self._all_cameras
                   if q in name.lower() or q in num.lower()]
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

        search_cols = [c for c, cb in self._pv_buttons.items() if cb.isChecked()]
        if not search_cols:
            search_cols = ["sbw4"]
        col = search_cols[0]  # primární pro jednotky
        extra_cols = [c for c, cb in self._extra_pv_checks.items()
                      if cb.isChecked() and c not in search_cols]
        target_ui = self._target_sb.value()
        tolerance = self._tol_sb.value()
        target_for_search = target_ui / 1000.0 if col in MJ_COLUMNS else target_ui

        # Tolerance v CSV jednotkách
        if col == "sbw4":
            tol_csv = tolerance / SBW4_TRANSMISSION
        elif col in MJ_COLUMNS:
            tol_csv = tolerance / 1000.0
        else:
            tol_csv = tolerance

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

        def worker():
            for i, day in enumerate(days):
                try:
                    all_cols = list(search_cols) + [
                        c for c in extra_cols if c not in search_cols]
                    rows, per_col = _load_api_for_day(day, all_cols)
                    if not rows:
                        self._sig.log_msg.emit(f"{day}: no data (API + CSV)")
                        self._sig.result.emit(None)
                        self._sig.progress.emit(i + 1)
                        continue

                    self._sig.log_msg.emit(f"{day}: {len(rows)} samples")

                    # Hledej nejbližší match pro každý vybraný sloupec
                    # a vyber ten s nejmenším relativním rozdílem
                    best = None
                    best_col = col
                    best_diff_rel = float("inf")
                    for sc in search_cols:
                        t_sc = target_for_search / SBW4_TRANSMISSION if sc == "sbw4" else (
                            target_ui / 1000.0 if sc in MJ_COLUMNS else target_ui)
                        b = _find_best_match(rows, sc, t_sc)
                        if b is None:
                            continue
                        raw_b = b.get(sc, "")
                        try:
                            v_b = float(raw_b)
                            t_csv_b = t_sc / SBW4_TRANSMISSION if sc == "sbw4" else t_sc
                            diff_rel = abs(v_b - t_csv_b) / max(abs(t_csv_b), 1e-9)
                        except Exception:
                            diff_rel = float("inf")
                        if diff_rel < best_diff_rel:
                            best_diff_rel = diff_rel
                            best = b
                            best_col = sc
                    day_col = best_col  # local — never mutate outer col
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

                    target_csv = target_for_search / SBW4_TRANSMISSION if day_col == "sbw4" else target_for_search
                    diff_best = abs(actual_best - target_csv) if actual_best is not None else None

                    # Tolerance pro tento den a sloupec
                    if day_col == "sbw4":
                        day_tol_csv = tolerance / SBW4_TRANSMISSION
                    elif day_col in MJ_COLUMNS:
                        day_tol_csv = tolerance / 1000.0
                    else:
                        day_tol_csv = tolerance

                    # Všechny řádky v toleranci
                    rows_in_tol = []
                    for row in rows:
                        raw = row.get(day_col, "")
                        try:
                            v = float(raw)
                        except Exception:
                            continue
                        if abs(v - target_csv) <= day_tol_csv:
                            rows_in_tol.append(row)

                    # Hourová složka
                    dt_obj = best.get("_dt")
                    hour_folder = None
                    if dt_obj is not None:
                        hour_utc = _folder_hour_from_prague(dt_obj.hour, day)
                        hour_folder = _find_hour_folder(day, hour_utc)

                    result = {
                        "day":         day,
                        "best_row":    best,
                        "rows_in_tol": rows_in_tol,
                        "col":         day_col,
                        "actual":      actual_best,
                        "diff":        diff_best,
                        "target_csv":  target_csv,
                        "hour_folder": hour_folder,
                        "cam":         cam,
                        "extra_cols":  extra_cols,
                        "search_cols": search_cols,
                        "per_col":     per_col,
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
            rows_in_tol=rows_in_tol, per_col=per_col)
        self._day_results.append(dr)

        # Folder path
        folder_path = None
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
                if cam_folder.exists() and cam_folder.is_dir():
                    folder_path = cam_folder
            if folder_path is None:
                folder_path = hour_folder

        folder_str = str(folder_path) if folder_path else "Not found"

        raw_val    = best_row.get(col, "")
        val_str    = _format_value(col, raw_val)
        # Extra columns — closest-timestamp match
        extra_cols = result.get("extra_cols", [])
        best_ns = best_row.get("_ns")
        if extra_cols and best_ns is not None:
            extra_parts = []
            for ec in extra_cols:
                raw_ec = _find_closest_col_value(per_col, ec, best_ns)
                ev = _format_value(ec, raw_ec)
                short = PV_COLUMNS.get(ec, ec).split(" [")[0]
                extra_parts.append(f"{short}: {ev}")
            val_str += "\n" + "  |  ".join(extra_parts)
        prague_str = dt_obj.strftime("%H:%M:%S.%f")[:-3]
        diff       = result["diff"]

        # diff v user jednotkách
        if col == "sbw4" and diff is not None:
            diff_actual = diff * SBW4_TRANSMISSION
            diff_str = f"{diff_actual:.4f} J"
            warn = diff_actual > self._tol_sb.value()
        elif col in MJ_COLUMNS and diff is not None:
            diff_str = f"{diff * 1000:.2f} mJ"
            warn = diff * 1000 > self._tol_sb.value()
        elif diff is not None:
            diff_str = f"{diff:.4f}"
            warn = diff > self._tol_sb.value()
        else:
            diff_str = "—"
            warn = False

        n_tol = len(rows_in_tol)
        if warn:
            status_str   = f"⚠ outside ±{self._tol_sb.value():.2f}J"
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
            PV_COLUMNS.get(col, col),
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
            if folder_path is None and c == 6:
                item.setForeground(QColor("#cc0000"))
            self._table.setItem(r, c, item)
            if extra_cols:
                self._table.setRowHeight(r, 36)

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

        lbl = QLabel(f"{len(rows_in_tol)} shot(s) within ±{self._tol_sb.value():.2f} J on {dr.day}:")
        lay.addWidget(lbl)

        tbl = QTableWidget(0, 3)
        tbl.setHorizontalHeaderLabels(["Prague Time", "Value", "Δ from target"])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        for row in rows_in_tol:
            dt_obj = row.get("_dt")
            raw_val = row.get(col, "")
            ts_str = dt_obj.strftime("%H:%M:%S.%f")[:-3] if dt_obj else "?"
            val_str = _format_value(col, raw_val)
            try:
                v = float(raw_val)
                diff = abs(v - target_csv)
                if col == "sbw4":
                    diff_str = f"{diff * SBW4_TRANSMISSION:.4f} J"
                elif col in MJ_COLUMNS:
                    diff_str = f"{diff * 1000:.2f} mJ"
                else:
                    diff_str = f"{diff:.4f}"
            except Exception:
                diff_str = "—"
            r2 = tbl.rowCount()
            tbl.insertRow(r2)
            tbl.setItem(r2, 0, QTableWidgetItem(ts_str))
            tbl.setItem(r2, 1, QTableWidgetItem(val_str))
            tbl.setItem(r2, 2, QTableWidgetItem(diff_str))

        lay.addWidget(tbl, 1)

        cam_folder_ref = [None]
        # Zjisti cam_folder pro preview
        cam = self._active_cam
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
                raw2 = row.get(col, "")
                val2 = _format_value(col, raw2)
                short2 = PV_COLUMNS.get(col, col).split(" [")[0]
                energy2 = f"{short2}: {val2}"
                _gen2 = self._preview_gen + 1
                self._preview_gen = _gen2
                threading.Thread(
                    target=lambda p=img2, e=energy2, g=_gen2: self._load_and_show_preview(p, e, g),
                    daemon=True).start()

        tbl.selectionModel().selectionChanged.connect(_on_dlg_row_selected)

        btn_row = QHBoxLayout()
        btn_open = QPushButton("▶ Open selected in Slider")
        btn_open.setStyleSheet(
            "QPushButton { background: #2d7dff; color: #fff; font-weight: 700; "
            "border-radius: 4px; padding: 5px 10px; }"
            "QPushButton:hover { background: #1a6aee; }")
        btn_close = QPushButton("Close")
        btn_row.addWidget(btn_open)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

        btn_close.clicked.connect(dlg.reject)

        def open_selected():
            selected_rows = tbl.selectedIndexes()
            if not selected_rows:
                sel_rows_in_tol = rows_in_tol
            else:
                idxs = sorted(set(i.row() for i in selected_rows))
                sel_rows_in_tol = [rows_in_tol[i] for i in idxs]

            cam = self._active_cam
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
                    files.append(img)

            if not files:
                QMessageBox.warning(dlg, "No images", "No matching images found.")
                return

            if self._temp_dir is not None:
                try:
                    shutil.rmtree(self._temp_dir, ignore_errors=True)
                except Exception:
                    pass
            self._temp_dir = tempfile.mkdtemp(prefix="SF_slider_")
            temp_path = Path(self._temp_dir)

            copied = 0
            for src in files:
                try:
                    dst = temp_path / src.name
                    if dst.exists():
                        dst = temp_path / f"{src.stem}_{copied}{src.suffix}"
                    shutil.copy2(src, dst)
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
                self._slider_ref.open_folder_path(temp_path)

        btn_open.clicked.connect(open_selected)
        dlg.exec()

    def _on_search_done(self):
        self._search_running = False
        self._set_busy(False)
        self._prog.setVisible(False)
        n = self._table.rowCount()
        self._result_lbl.setText(f"Results: {n} day(s) matched")
        self._log(f"Search done — {n} results")
        if n > 0 and self._slider_ref is not None:
            self._btn_open_slider.setEnabled(True)
        if n > 0:
            self._btn_save_results.setEnabled(True)

    # ── OPEN IN SLIDER ────────────────────────────────────────────────────────

    def _open_in_slider(self):
        if self._slider_ref is None or self._tab_widget is None:
            QMessageBox.information(self, "Image Slider",
                "Image Slider is not connected. Run via main.py.")
            return

        if not self._day_results:
            return

        cam = self._active_cam
        if not cam:
            QMessageBox.warning(self, "No camera selected",
                "Please select a camera first.")
            return

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
            if dr.hour_folder is None:
                self._log(f"{dr.day}: no hour folder, skipping")
                continue

            cam_folder = dr.hour_folder / cam
            if not cam_folder.exists():
                try:
                    for sub in dr.hour_folder.iterdir():
                        if sub.is_dir() and sub.name.lower() == cam.lower():
                            cam_folder = sub
                            break
                except Exception:
                    pass

            if not cam_folder.exists():
                self._log(f"{dr.day}: camera {cam} not found")
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

        # Kopíruj do temp složky
        if self._temp_dir is not None:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass
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

        # Sestav energy map — filename -> text pro zobrazení v slideru
        energy_map: dict[str, str] = {}
        col_search = next((c for c, cb in self._pv_buttons.items() if cb.isChecked()), "sbw4")
        extra_cols = [c for c, cb in self._extra_pv_checks.items()
                      if cb.isChecked() and c != col_search]

        for src, dst, i in copied_files:
            if i >= len(results_to_open):
                continue
            dr = results_to_open[i]
            best_ns = dr.best_row.get("_ns")
            parts = []
            val_main = _format_value(dr.col, dr.best_row.get(dr.col, ""))
            short_main = PV_COLUMNS.get(dr.col, dr.col).split(" [")[0]
            parts.append(f"{short_main}: {val_main}")
            if best_ns is not None:
                for ec in extra_cols:
                    raw_ec = _find_closest_col_value(dr.per_col, ec, best_ns)
                    ev = _format_value(ec, raw_ec)
                    short = PV_COLUMNS.get(ec, ec).split(" [")[0]
                    parts.append(f"{short}: {ev}")
            energy_map[dst.name] = "  |  ".join(parts)

        self._tab_widget.setCurrentIndex(1)
        self._slider_ref._discrete_mode = True
        self._slider_ref.open_folder_path(temp_path)

        # Předej energy map AFTER open_folder_path — reset se už stalo
        from PySide6.QtCore import QTimer
        def _set_map():
            self._slider_ref._sf_energy_map = energy_map
            # Obnov zobrazení aktuálního snímku s energií
            if self._slider_ref.current_idx is not None and self._slider_ref.items:
                idx = self._slider_ref.current_idx
                self._slider_ref._set_info_for(idx, self._slider_ref.items[idx].ts_ns)
                self._slider_ref.img_view.update()
        QTimer.singleShot(500, _set_map)

    def _save_results(self):
        if not self._day_results:
            return

        cam = self._active_cam
        if not cam:
            QMessageBox.warning(self, "No camera selected",
                "Please select a camera first.")
            return

        from PySide6.QtWidgets import QFileDialog
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder")
        if not out_dir:
            return
        out_path = Path(out_dir)

        selected_rows = sorted(set(
            idx.row() for idx in self._table.selectedIndexes()
        ))
        if selected_rows:
            results_to_save = [self._day_results[r] for r in selected_rows
                               if r < len(self._day_results)]
        else:
            results_to_save = self._day_results

        col_search = next((c for c, cb in self._pv_buttons.items() if cb.isChecked()), "sbw4")
        extra_cols = [c for c, cb in self._extra_pv_checks.items()
                      if cb.isChecked() and c != col_search]

        copied = 0
        errors = 0
        for dr in results_to_save:
            if dr.hour_folder is None:
                self._log(f"{dr.day}: no hour folder, skipping")
                errors += 1
                continue

            cam_folder = dr.hour_folder / cam
            if not cam_folder.exists():
                try:
                    for sub in dr.hour_folder.iterdir():
                        if sub.is_dir() and sub.name.lower() == cam.lower():
                            cam_folder = sub
                            break
                except Exception:
                    pass

            if not cam_folder.exists():
                self._log(f"{dr.day}: camera {cam} not found")
                errors += 1
                continue

            dt_obj = dr.best_row.get("_dt")
            if dt_obj is None:
                errors += 1
                continue

            img = _find_image_for_ts(cam_folder, dt_obj,
                                     ts_ns_override=dr.best_row.get("_ns"))
            if img is None:
                self._log(f"{dr.day}: no image near {dt_obj.strftime('%H:%M:%S')}")
                errors += 1
                continue

            # Název souboru: datum + čas + hodnota PV
            val_str = _format_value(dr.col, dr.best_row.get(dr.col, "")).replace(" ", "").replace("/", "-")
            short = PV_COLUMNS.get(dr.col, dr.col).split(" [")[0]
            dst_name = f"{dr.day}_{dt_obj.strftime('%H-%M-%S')}_{short}_{val_str}{img.suffix}"
            dst = out_path / dst_name

            try:
                # Sestav energy text pro anotaci
                parts = []
                val_main = _format_value(dr.col, dr.best_row.get(dr.col, ""))
                short_main = PV_COLUMNS.get(dr.col, dr.col).split(" [")[0]
                parts.append(f"{short_main}: {val_main}")
                for ec in extra_cols:
                    ev = _format_value(ec, dr.best_row.get(ec, ""))
                    short = PV_COLUMNS.get(ec, ec).split(" [")[0]
                    parts.append(f"{short}: {ev}")
                energy_text = "  |  ".join(parts)

                # Ulož s anotací jako PNG
                dst = dst.with_suffix(".png")
                from PIL import Image as _PilImg, ImageDraw as _PilDraw, ImageFont as _PilFont
                import numpy as _np
                from PySide6.QtGui import QImage
                from PySide6.QtCore import QSize

                # Načti přes QImageReader — správně zpracuje 16-bit
                from PySide6.QtGui import QImageReader
                reader = QImageReader(str(img))
                reader.setAutoTransform(True)
                qimg = reader.read()
                if qimg.isNull():
                    shutil.copy2(img, dst)
                    copied += 1
                    continue

                # Konvertuj na grayscale 8-bit s autostretch
                if qimg.format() != QImage.Format.Format_Grayscale8:
                    qimg = qimg.convertToFormat(QImage.Format.Format_Grayscale8)
                w, h = qimg.width(), qimg.height()
                ptr = qimg.bits()
                if hasattr(ptr, "setsize"):
                    ptr.setsize(qimg.sizeInBytes())
                arr = _np.frombuffer(ptr, dtype=_np.uint8).reshape(h, qimg.bytesPerLine())[:, :w].copy()
                # Autostretch
                lo, hi = _np.percentile(arr, [0.1, 99.9])
                if hi > lo + 2:
                    arr = _np.clip((arr.astype(_np.float32) - lo) / (hi - lo) * 255.0, 0, 255).astype(_np.uint8)
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
                copied += 1
                self._log(f"{dr.day}: saved {dst.name}")
            except Exception as e:
                self._log(f"{dr.day}: copy error {e}")
                errors += 1

        QMessageBox.information(self, "Save images",
            f"Saved: {copied}\nErrors: {errors}\n\nFolder: {out_path}")


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