# is_t.py — Image Finder (PySide6 port)

import math
import os
import re
import bisect
import shutil
import time
import argparse
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from collections import OrderedDict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
try:
    import matplotlib
    if hasattr(matplotlib, 'use'):
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.gridspec import GridSpec
    _MPL_OK = True
except Exception:
    plt = None
    FigureCanvas = None
    GridSpec = None
    _MPL_OK = False

from PySide6.QtCore import (
    Qt, QTimer, QRunnable, QThreadPool, QObject, Signal, QSize, QRect, QPoint, QPointF, QDate, QModelIndex
)
from PySide6.QtGui import (
    QPixmap, QImageReader, QPainter, QFontMetrics, QFont, QImage, QColor, QPen, QGuiApplication, QTextCharFormat
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QDoubleSpinBox, QScrollArea,
    QLabel, QSlider, QPushButton, QFileDialog, QMessageBox, QProgressBar,
    QComboBox, QCheckBox, QDialog, QCalendarWidget, QDialogButtonBox, QFileSystemModel,
    QSpinBox, QFrame, QSizePolicy, QStyledItemDelegate, QAbstractItemView, QTreeView, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

# ---------------- CONFIG ----------------
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
TZ_PRAGUE = ZoneInfo("Europe/Prague")
SLIDER_MAX = 1_000_000
SCRUB_INTERVAL_MS = 33
SCRUB_MAX_SIDE = 900
FAST_SCRUB_MAX_SIDE = 320
PLAY_MAX_SIDE_SLOW = 320
PLAY_MAX_SIDE_FAST = 240
CACHE_SIZE = 320
PREFETCH_RADIUS_IDLE = 6
PREFETCH_AHEAD_PLAY = 3
TICK_STEP_MINUTES = 10
PLAY_TICK_MS = 33
AXIS_TOLERANCE_S = 5 * 60
PLAY_EXACT_PCT_PER_S_THRESHOLD = 0.5
SAVE_RANGE_WARN_COUNT = 500

# ---------------- GRADIENTS ----------------
def _copy_metadata_into_png(src: Path, dst: Path, save_txt: bool = False):
    """Embed original PNG/TIFF metadata as PNG tEXt chunks in dst.
    Optionally also writes a sidecar .txt when save_txt=True.
    Safe to call from any thread."""
    try:
        from PIL import Image as _PilImg, PngImagePlugin as _PngP
        with _PilImg.open(src) as _src_img:
            info = dict(_src_img.info)
        if not info:
            return
        # Re-open dst (already saved PNG) and re-save with metadata embedded
        with _PilImg.open(dst) as _dst_img:
            png_info = _PngP.PngInfo()
            for k, v in info.items():
                try:
                    png_info.add_text(str(k), str(v))
                except Exception:
                    pass
            _dst_img.save(str(dst), pnginfo=png_info)
        if save_txt:
            txt_path = dst.with_suffix(".txt")
            lines = [f"# Metadata from: {src.name}", f"# Saved as: {dst.name}", ""]
            for k, v in info.items():
                lines.append(f"{k}: {v}")
            txt_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def _copy_metadata_into_png_bg(src: Path, dst: Path, save_txt: bool = False):
    """Same as _copy_metadata_into_png but dispatched to a daemon thread (non-blocking)."""
    import threading
    t = threading.Thread(target=_copy_metadata_into_png, args=(src, dst, save_txt), daemon=True)
    t.start()

# Keep old name as alias so existing call-sites in SaveRangeTask still compile
def _save_png_metadata_txt(src: Path, dst: Path):
    _copy_metadata_into_png(src, dst, save_txt=False)

def _make_lut(stops: list[tuple[float, tuple[int,int,int]]]) -> np.ndarray:
    """Interpoluje RGB LUT (256x3) ze seznamu (pozice 0–1, (r,g,b))."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]; t1, c1 = stops[j+1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                lut[i] = tuple(int(c0[k] + f*(c1[k]-c0[k])) for k in range(3))
                break
    return lut

def _make_binary_lut() -> np.ndarray:
    lut = np.zeros((256, 3), dtype=np.uint8)
    lut[128:] = 255
    return lut

def _make_stepped_lut(stops: list[tuple[float, tuple[int,int,int]]]) -> np.ndarray:
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        color = stops[-1][1]
        for j in range(len(stops) - 1):
            if t < stops[j+1][0]:
                color = stops[j][1]
                break
        lut[i] = color
    return lut

GRADIENTS: dict[str, np.ndarray | None] = {
    "Default":         None,  # index 0: show original image (no grayscale conversion, no LUT)
    "Grayscale":       None,  # index 1: force grayscale, no LUT
    "Gradient": _make_lut([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
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
GRADIENT_ID_DEFAULT   = 0  # show original colors, no grayscale conversion
GRADIENT_ID_GRAYSCALE = 1  # force grayscale

# Speed reference: always 1 hour, regardless of axis length
ONE_HOUR_NS = 3_600_000_000_000

# Circle calibration
CIRCLE_SEARCH_REGION = 0.05        # menší ořez — kruh je skoro celý obraz
CIRCLE_BRIGHT_PERCENTILE = 0.85    # nižší práh — měkký přechod
CIRCLE_R_MIN_FRAC = 0.30           # kruh nemůže být příliš malý
CIRCLE_R_MAX_FRAC = 0.70           # ale může být velký
CIRCLE_MIN_POINTS = 40             # méně bodů stačí pro měkký okraj
CIRCLE_MIN_DROP = 3.0              # měkký přechod = malý drop
CIRCLE_REFINE_DROP = 2.5
# Soft circle calibration (měkký přechod)
CIRCLE_SOFT_PERCENTILE = 0.20   # hledáme poloměr kde jas klesne na 20% maxima
CIRCLE_SOFT_MIN_R_FRAC = 0.15   # měkký kruh může být menší
CIRCLE_SOFT_MAX_R_FRAC = 0.80   # a větší

DEFAULT_OPEN_DIR  = r"\\users-L3.tier0.lcs.local\cpva-image-2026\2026"
DEFAULT_OPEN_ROOT = r"\\users-L3.tier0.lcs.local\cpva-image-2026"
DEFAULT_SAVE_DIR  = r"\\hapls-share.lcs.local\scratch"

_CHECKBOX_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
"""

# ---------------- DATA ----------------
@dataclass(frozen=True)
class Item:
    path: Path
    ts_ns: int


def parse_unix_ns_from_name(p: Path) -> int | None:
    s = p.stem
    for i in range(len(s) - 18):
        sub = s[i:i + 19]
        if sub.isdigit():
            ts_ns = int(sub)
            if 946684800_000_000_000 <= ts_ns <= 4102444800_000_000_000:
                return ts_ns
    return None


# ---------------- TIME HELPERS ----------------
def _dt_from_ns(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns // 1_000_000_000, tz=TZ_PRAGUE)

def fmt_hhmm_from_ns(ts_ns: int) -> str:
    return f"{_dt_from_ns(ts_ns):%H:%M}"

def fmt_hhmmss_ms_from_ns(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    dt  = datetime.fromtimestamp(sec, tz=TZ_PRAGUE)
    return f"{dt:%H:%M:%S}.{ms:03d}"

def fmt_prague_full_from_ns(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    dt  = datetime.fromtimestamp(sec, tz=TZ_PRAGUE)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{ms:03d}"

def prague_stamp_for_filename(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    dt  = datetime.fromtimestamp(sec, tz=TZ_PRAGUE)
    return f"{dt:%Y_%m_%d--%H_%M_%S}__{ms:03d}"

def replace_unix_ns_with_prague_in_filename(p: Path, ts_ns: int) -> str:
    stamp = prague_stamp_for_filename(ts_ns)
    new_stem, n = re.subn(r"(?<!\d)\d{19}(?!\d)", stamp, p.stem, count=1)
    if n == 0:
        new_stem = f"{p.stem}__{stamp}"
    return f"{new_stem}{p.suffix}"

def ns_from_dt(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)

def floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)

def axis_from_hour_folder_exact(folder: Path) -> tuple[int, int] | None:
    try:
        hh = int(folder.name)
        dd = int(folder.parent.name)
        mm = int(folder.parent.parent.name)
        yy = int(folder.parent.parent.parent.name)
        if not (0 <= hh <= 23):
            return None
        import datetime as _dt
        utc_start = _dt.datetime(yy, mm, dd, hh, 0, 0, tzinfo=_dt.timezone.utc)
        start = utc_start.astimezone(TZ_PRAGUE)
        return ns_from_dt(start), ns_from_dt(start + timedelta(hours=1))
    except Exception:
        return None

def axis_from_any_folder(folder: Path) -> tuple[int, int] | None:
    p = folder
    for _ in range(4):
        ax = axis_from_hour_folder_exact(p)
        if ax is not None:
            return ax
        p = p.parent
    return None

def folder_hour_from_prague_hour(prague_hour: int, date: datetime | None = None) -> int:
    """Folders are stored in UTC — convert Prague hour to UTC hour."""
    if date is None:
        date = datetime.now(TZ_PRAGUE)
    # Zjisti UTC offset pro daný den
    offset_hours = int(date.utcoffset().total_seconds() // 3600)
    return (prague_hour - offset_hours) % 24


# ---------------- TIFF SCALE READER ----------------
def _read_tiff_max_sample(path: Path) -> int | None:
    """
    Přečte MaxSampleValue (TIFF tag 281) z TIFF souboru přes PIL.
    Vrátí int nebo None pokud tag neexistuje / soubor není TIFF.
    Tato hodnota udává skutečný rozsah pixelů (např. 1023, 4095, 65535).
    """
    if path.suffix.lower() not in (".tif", ".tiff"):
        return None
    try:
        from PIL import Image as _PilImg
        with _PilImg.open(str(path)) as pil:
            tag_data = pil.tag_v2 if hasattr(pil, "tag_v2") else getattr(pil, "tag", {})
            val = tag_data.get(281)  # MaxSampleValue
            if val is None:
                return None
            if isinstance(val, (list, tuple)):
                val = val[0]
            return int(val)
    except Exception:
        return None

# ---------------- BRIGHTNESS ----------------
def _autostretch_gray(img: QImage, p_low: float = 0.1, p_high: float = 99.9) -> QImage:
    if img.isNull():
        return img
    if img.format() != QImage.Format.Format_Grayscale8:
        img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, img.bytesPerLine())[:, :w].copy()
    lo, hi = np.percentile(arr, [p_low, p_high])
    if hi <= lo + 2:
        return img
    stretched = np.clip((arr.astype(np.float32) - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    out = QImage(stretched.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)
    return out.copy()

def _apply_brightness_offset(img: QImage, offset: int) -> QImage:
    """Přidá konstantní offset jasu ke grayscale obrazu."""
    if offset == 0: return img
    if img.format() != QImage.Format.Format_Grayscale8:
        img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0: return img
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, img.bytesPerLine())[:, :w].copy()
    arr = np.clip(arr.astype(np.int16) + offset, 0, 255).astype(np.uint8)
    out = QImage(arr.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)
    return out.copy()

# ---------------- FAST IMAGE LOAD ----------------
def load_image_scaled(path: Path, max_side: int, brighten: bool, gradient_id: int = 0, brightness_offset: int = 0, ref_image: np.ndarray | None = None, sub_threshold: int = 0) -> QImage:
    r = QImageReader(str(path))
    r.setAutoTransform(True)
    sz = r.size()
    if sz.isValid():
        w, h = sz.width(), sz.height()
        if w > 0 and h > 0 and max_side > 0:
            scale = max(w, h) / max_side
            if scale > 1.0:
                r.setScaledSize(QSize(max(1, int(w / scale)), max(1, int(h / scale))))
    img = r.read()
    if img.isNull():
        return QImage()

    # "Default" = show original colors, skip grayscale conversion.
    # All other palettes convert to grayscale first (needed for LUT mapping).
    if gradient_id == GRADIENT_ID_DEFAULT:
        # 16-bit originals still need normalization to 8-bit for display, but keep RGB if present
        if img.format() in (QImage.Format.Format_Grayscale16, QImage.Format.Format_RGB16):
            ptr = img.bits()
            if hasattr(ptr, "setsize"):
                ptr.setsize(img.sizeInBytes())
            arr16 = np.frombuffer(ptr, dtype=np.uint16).reshape(img.height(), img.bytesPerLine() // 2)[:, :img.width()].copy()
            max_sample = _read_tiff_max_sample(path)
            mn = int(arr16.min())
            mx = max_sample if (max_sample is not None and max_sample > mn) else int(arr16.max())
            if mx > mn:
                arr8 = np.clip((arr16.astype(np.float32) - mn) / (mx - mn) * 255.0, 0, 255).astype(np.uint8)
            else:
                arr8 = np.zeros_like(arr16, dtype=np.uint8)
            img = QImage(arr8.tobytes(), img.width(), img.height(), img.width(), QImage.Format.Format_Grayscale8)
        # Return original (possibly RGB) image without forced grayscale conversion
        return img
    else:
        # All non-Default palettes: normalize 16-bit then convert to Grayscale8 for LUT processing
        if img.format() in (QImage.Format.Format_Grayscale16, QImage.Format.Format_RGB16):
            ptr = img.bits()
            if hasattr(ptr, "setsize"):
                ptr.setsize(img.sizeInBytes())
            arr16 = np.frombuffer(ptr, dtype=np.uint16).reshape(img.height(), img.bytesPerLine() // 2)[:, :img.width()].copy()
            max_sample = _read_tiff_max_sample(path)
            mn = int(arr16.min())
            mx = max_sample if (max_sample is not None and max_sample > mn) else int(arr16.max())
            if mx > mn:
                arr8 = np.clip((arr16.astype(np.float32) - mn) / (mx - mn) * 255.0, 0, 255).astype(np.uint8)
            else:
                arr8 = np.zeros_like(arr16, dtype=np.uint8)
            img = QImage(arr8.tobytes(), img.width(), img.height(), img.width(), QImage.Format.Format_Grayscale8)
        else:
            img = img.convertToFormat(QImage.Format.Format_Grayscale8)

    if ref_image is not None:
        ptr2 = img.bits()
        if hasattr(ptr2, "setsize"):
            ptr2.setsize(img.sizeInBytes())
        arr_cur = np.frombuffer(ptr2, dtype=np.uint8).reshape(
            img.height(), img.bytesPerLine())[:, :img.width()].copy().astype(np.float32)
        if ref_image.shape != arr_cur.shape:
            from PIL import Image as PilImage
            ref_pil = PilImage.fromarray(ref_image.astype(np.uint8))
            ref_pil = ref_pil.resize(
                (arr_cur.shape[1], arr_cur.shape[0]),
                PilImage.Resampling.BILINEAR)
            ref_arr = np.asarray(ref_pil, dtype=np.float32)
        else:
            ref_arr = ref_image.copy()
        diff = np.abs(arr_cur - ref_arr)
        if sub_threshold > 0:
            diff[diff < sub_threshold] = 0
        diff = np.clip(diff, 0, 255).astype(np.uint8)
        img = QImage(diff.tobytes(), img.width(), img.height(),
                     img.width(), QImage.Format.Format_Grayscale8)

    if brighten and ref_image is None:
        img = _autostretch_gray(img)

    if brightness_offset != 0:
        img = _apply_brightness_offset(img, brightness_offset)

    # gradient_id == GRADIENT_ID_GRAYSCALE (1): no LUT, already grayscale
    # gradient_id >= 2: apply color LUT
    lut = GRADIENTS[GRADIENT_NAMES[gradient_id]] if gradient_id >= 2 else None
    if lut is not None:
        img = _apply_lut(img, lut)
    return img


def _apply_lut(img: QImage, lut: np.ndarray) -> QImage:
    """Aplikuje RGB LUT na grayscale QImage, vrátí RGB32 QImage."""
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, img.bytesPerLine())[:, :w].copy()
    rgb = lut[arr]  # (h, w, 3)
    # Qt RGB32 = BGRA v paměti na little-endian
    bgra = np.zeros((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0] = rgb[:, :, 2]  # B
    bgra[:, :, 1] = rgb[:, :, 1]  # G
    bgra[:, :, 2] = rgb[:, :, 0]  # R
    bgra[:, :, 3] = 255
    out = QImage(bgra.tobytes(), w, h, w * 4, QImage.Format.Format_RGB32)
    return out.copy()

class PixCache:
    def __init__(self, max_items: int):
        self.max_items = max_items
        self._d: OrderedDict = OrderedDict()

    def get(self, key) -> QPixmap | None:
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def put(self, key, val: QPixmap):
        self._d[key] = val
        self._d.move_to_end(key)
        while len(self._d) > self.max_items:
            self._d.popitem(last=False)


# ---------------- ASYNC LOADER ----------------
class LoaderSignals(QObject):
    loaded = Signal(int, int, int, int, int, int, int, QImage)

class LoadTask(QRunnable):
    def __init__(self, gen, req_id, idx, path, max_side, brighten, gradient_id, signals, brightness_offset=0, ref_image=None, sub_threshold=0):
        super().__init__()
        self.gen = gen; self.req_id = req_id; self.idx = idx
        self.path = path; self.max_side = max_side; self.brighten = brighten
        self.gradient_id = gradient_id; self.brightness_offset = brightness_offset
        self.ref_image = ref_image; self.sub_threshold = sub_threshold
        self.signals = signals

    def run(self):
        img = load_image_scaled(self.path, self.max_side, bool(self.brighten), self.gradient_id, self.brightness_offset, self.ref_image, self.sub_threshold)
        try:
            self.signals.loaded.emit(self.gen, self.req_id, self.idx, self.max_side, self.brighten, self.gradient_id, self.brightness_offset, img)
        except RuntimeError:
            pass

# ---------------- BACKGROUND SCAN ----------------
class ScanSignals(QObject):
    status     = Signal(int, str)
    progress   = Signal(int, int, int, int, int)
    finished   = Signal(int, list)
    cancelled  = Signal(int)
    quick_item = Signal(int, object)   # (gen, Item) — newest found so far, emitted ASAP

class ScanTask(QRunnable):
    def __init__(self, gen, folders):
        super().__init__()
        self.gen = gen; self.folders = folders
        self.signals = ScanSignals()
        self._cancel = False

    def cancel(self): self._cancel = True

    def run(self):
        items = []; processed = 0; found = 0
        total_folders = len(self.folders)
        quick_emitted = False
        best_item: Item | None = None   # track newest item for quick_item signal
        try:
            for folder_i, folder in enumerate(self.folders, 1):
                if self._cancel:
                    self.signals.cancelled.emit(self.gen); return
                if not folder.exists() or not folder.is_dir():
                    continue
                self.signals.status.emit(self.gen, f"Scanning {folder_i}/{total_folders}: {folder}")
                with os.scandir(folder) as it:
                    for e in it:
                        if self._cancel:
                            self.signals.cancelled.emit(self.gen); return
                        if not e.is_file():
                            continue
                        processed += 1
                        if processed % 500 == 0:
                            self.signals.progress.emit(self.gen, folder_i, total_folders, processed, found)
                        p = Path(e.path)
                        if p.suffix.lower() not in IMG_EXT:
                            continue
                        ts_ns = parse_unix_ns_from_name(p)
                        if ts_ns is None:
                            continue
                        item = Item(p, ts_ns)
                        items.append(item)
                        found += 1
                        # Track the newest item seen so far
                        if best_item is None or ts_ns > best_item.ts_ns:
                            best_item = item
                        if found % 250 == 0:
                            self.signals.progress.emit(self.gen, folder_i, total_folders, processed, found)
                        # Emit quick_item after first 50 files found — enough for a good max
                        if not quick_emitted and found >= 50 and best_item is not None:
                            self.signals.quick_item.emit(self.gen, best_item)
                            quick_emitted = True
        except Exception:
            pass
        # Emit quick_item even if folder had <50 files
        if not quick_emitted and best_item is not None:
            self.signals.quick_item.emit(self.gen, best_item)
        self.signals.progress.emit(self.gen, total_folders, total_folders, processed, found)
        items.sort(key=lambda it: it.ts_ns)
        self.signals.finished.emit(self.gen, items)

class RefreshScanSignals(QObject):
    finished = Signal(int, list)

class RefreshScanTask(QRunnable):
    """Skenuje složky a vrátí VŠECHNY nalezené položky — merge udělá Viewer."""
    def __init__(self, gen, folders):
        super().__init__()
        self.gen = gen
        self.folders = folders
        self.signals = RefreshScanSignals()

    def run(self):
        items = []
        try:
            for folder in self.folders:
                if not folder.exists() or not folder.is_dir():
                    continue
                with os.scandir(folder) as it:
                    for e in it:
                        if not e.is_file():
                            continue
                        p = Path(e.path)
                        if p.suffix.lower() not in IMG_EXT:
                            continue
                        ts_ns = parse_unix_ns_from_name(p)
                        if ts_ns is None:
                            continue
                        items.append(Item(p, ts_ns))
        except Exception:
            pass
        items.sort(key=lambda it: it.ts_ns)
        self.signals.finished.emit(self.gen, items)

# ---------------- BACKGROUND SAVE ----------------
class SaveRangeSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(int, int)

class SaveRangeTask(QRunnable):
    def __init__(self, items, outp, name_fn, gradient_id=0, brighten=False, overlay_params=None):
        super().__init__()
        self.items = items; self.outp = outp; self.name_fn = name_fn
        self.gradient_id = gradient_id; self.brighten = brighten
        self.overlay_params = overlay_params  # dict or None
        self.signals = SaveRangeSignals()

    def _draw_overlay_on_pixmap(self, src_path):
        """Load image, draw overlay, return QPixmap. Returns None on failure."""
        p = self.overlay_params
        img = load_image_scaled(src_path, 9999, self.brighten, self.gradient_id)
        if img.isNull(): return None
        pix = QPixmap.fromImage(img)
        w, h = pix.width(), pix.height()
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if p.get('show_cross') and p.get('cross_pos_norm') is not None:
            cn = p['cross_pos_norm']
            cx = int(cn.x() * w); cy = int(cn.y() * h)
            sz = p.get('cross_size', 18)
            pen = QPen(p.get('cross_color', QColor(0, 255, 0)))
            pen.setWidth(max(p.get('cross_thick', 2), w // 500))
            painter.setPen(pen)
            painter.drawLine(cx - sz, cy, cx + sz, cy)
            painter.drawLine(cx, cy - sz, cx, cy + sz)
        if p.get('show_circle') and p.get('circle_center_norm') is not None:
            cn = p['circle_center_norm']
            cx = int(cn.x() * w); cy = int(cn.y() * h)
            if p.get('circle_rx_norm') is not None:
                rx = int(p['circle_rx_norm'] * w); ry = int(p['circle_ry_norm'] * h)
            else:
                r = int(p.get('circle_r_norm', 0.1) * min(w, h)); rx = ry = r
            pen = QPen(p.get('circle_color', QColor(255, 255, 0)))
            pen.setWidth(max(p.get('circle_thick', 2), w // 500))
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
        if p.get('show_square') and p.get('square_rect_norm') is not None:
            ln, tn, rn, bn = p['square_rect_norm']
            sx = int(ln * w); sy = int(tn * h)
            sw = int((rn - ln) * w); sh = int((bn - tn) * h)
            pen = QPen(p.get('square_color', QColor(0, 200, 255)))
            pen.setWidth(max(p.get('square_thick', 2), w // 500))
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(sx, sy, sw, sh)
        painter.end()
        return pix

    def run(self):
        n = 0; errors = 0; total = len(self.items)
        save_txt = getattr(self, 'save_txt', False)
        for done, it in enumerate(self.items, 1):
            dst = self.outp / self.name_fn(it)
            try:
                if self.gradient_id == GRADIENT_ID_DEFAULT and not self.brighten:
                    # Default — copy original + metadata
                    shutil.copy2(it.path, dst)
                    _copy_metadata_into_png(it.path, dst, save_txt=save_txt)
                else:
                    # Color palette / brightened — save as PNG with metadata
                    dst = dst.with_suffix('.png')
                    img = load_image_scaled(it.path, 9999, self.brighten, self.gradient_id)
                    if img.isNull():
                        errors += 1
                        self.signals.progress.emit(done, total, it.path.name)
                        continue
                    if not QPixmap.fromImage(img).save(str(dst)):
                        errors += 1
                        self.signals.progress.emit(done, total, it.path.name)
                        continue
                    _copy_metadata_into_png(it.path, dst, save_txt=save_txt)
                # Annotate version if overlay is requested
                if self.overlay_params:
                    ann_dst = dst.parent / f"{dst.stem}_annotate.png"
                    pix = self._draw_overlay_on_pixmap(it.path)
                    if pix is not None:
                        pix.save(str(ann_dst))
                        _copy_metadata_into_png(it.path, ann_dst, save_txt=save_txt)
                n += 1
            except Exception:
                errors += 1
            self.signals.progress.emit(done, total, it.path.name)
        self.signals.finished.emit(n, errors)

# ---------------- POINTING ANALYSIS ----------------
class PointingAnalysisSignals(QObject):
    progress = Signal(int, int)        # done, total
    finished = Signal(list)            # list of (ts_ns, cx_mm, cy_mm)
    cancelled = Signal()

class PointingAnalysisTask(QRunnable):
    def __init__(self, items, threshold, pixel_mm, signals):
        super().__init__()
        self.items = items
        self.threshold = threshold
        self.pixel_mm = pixel_mm
        self.signals = signals
        self._cancel = False

    def cancel(self): self._cancel = True

    @staticmethod
    def _process_one(item, threshold, pixel_mm):
        """Zpracuje jeden snímek — volá se z thread poolu."""
        try:
            from PIL import Image as PilImage
            pil_img = PilImage.open(str(item.path))
            w0, h0 = pil_img.size
            pil_img = pil_img.convert("I") if pil_img.mode in ("I", "I;16") else pil_img.convert("L")
            scale = max(w0, h0) / 100.0
            if scale > 1.0:
                new_w = max(1, int(w0 / scale))
                new_h = max(1, int(h0 / scale))
                pil_img = pil_img.resize((new_w, new_h), PilImage.Resampling.BILINEAR)
            arr = np.asarray(pil_img, dtype=np.float32).copy()
            w, h = arr.shape[1], arr.shape[0]
        except Exception:
            return None

        if w <= 0 or h <= 0: return None

        arr_max = arr.max()
        if arr_max <= 0: return None
        arr = arr / arr_max * 255.0

        # Estimate background from image edges (corners), not a single column
        bg = float(np.percentile(arr, 10))
        arr = np.clip(arr - bg, 0, None)

        # Filtruj snímky bez dat — peak musí být výrazně nad šumem
        if arr.max() < threshold:
            return None
        arr[arr < threshold] = 0

        irradiance = arr.sum()
        if irradiance < 1.0: return None

        xs = np.arange(w, dtype=np.float32)
        ys = np.arange(h, dtype=np.float32)
        cx_px = float(arr.sum(axis=0) @ xs) / irradiance
        cy_px = float(arr.sum(axis=1) @ ys) / irradiance

        # Centroid relative to image center (0,0 = image centre)
        cx_px -= w / 2.0
        cy_px -= h / 2.0

        # Scale back to original image pixels
        scale_x = w0 / w
        scale_y = h0 / h
        return (item.ts_ns, cx_px * scale_x, cy_px * scale_y)

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        total = len(self.items)
        step = max(1, total // 6000)

        indices = list(range(0, total, step))
        n_to_process = len(indices)
        sampled_items = [self.items[i] for i in indices]

        results_map = {}
        done_count = 0

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {
                executor.submit(
                    self._process_one, item, self.threshold, self.pixel_mm
                ): idx
                for idx, item in enumerate(sampled_items)
            }
            for future in as_completed(futures):
                if self._cancel:
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.signals.cancelled.emit()
                    return
                idx = futures[future]
                result = future.result()
                if result is not None:
                    results_map[idx] = result
                done_count += 1
                if done_count % 20 == 0 or done_count == n_to_process:
                    self.signals.progress.emit(done_count, n_to_process)

        # Seřaď podle původního pořadí
        results = [results_map[i] for i in sorted(results_map.keys())]
        self.signals.finished.emit(results)


class _SCHistogramWidget(QWidget):
    """
    Matplotlib-based histogram widget. Click or drag threshold line to set threshold.
    Emits threshold_changed(int).
    """
    threshold_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts:    "np.ndarray | None" = None
        self._bin_edges: "np.ndarray | None" = None   # len = len(counts)+1
        self._bit_depth: int   = 65535
        self._threshold: int   = 0
        self._lo: float        = 0.0
        self._hi: float        = 65535.0
        self._log_scale: bool  = True
        self._error_msg: "str | None" = None
        self._dragging:  bool  = False

        self.setMinimumSize(420, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        if _MPL_OK:
            self._fig    = plt.Figure(figsize=(5, 2.5), dpi=90, facecolor="#f3f3f3")
            self._canvas = FigureCanvas(self._fig)
            self._canvas.setParent(self)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self._canvas)
            self._canvas.mpl_connect("button_press_event",   self._on_mpl_press)
            self._canvas.mpl_connect("motion_notify_event",  self._on_mpl_motion)
            self._canvas.mpl_connect("button_release_event", self._on_mpl_release)
            self._vline = None   # threshold axvline handle
        else:
            self._canvas = None

    # ── public API ────────────────────────────────────────────────────────────
    def load_data(self, counts: "np.ndarray", bit_depth: int, threshold: int,
                  lo: float = 0.0, hi: float = -1.0,
                  bin_edges: "np.ndarray | None" = None):
        self._counts    = np.asarray(counts, dtype=np.float64).copy()
        self._bin_edges = np.asarray(bin_edges) if bin_edges is not None else None
        self._bit_depth = max(int(bit_depth), 1)
        self._threshold = max(0, min(int(threshold), self._bit_depth))
        self._lo = float(lo)
        self._hi = float(hi) if hi >= 0 else float(self._bit_depth)
        self._error_msg = None
        self._redraw()

    def set_error(self, msg: str):
        self._error_msg = msg
        self._counts = None
        self._redraw()

    def set_threshold(self, thr: int):
        self._threshold = max(0, min(int(thr), self._bit_depth))
        self._update_vline()

    def set_log_scale(self, on: bool):
        self._log_scale = on
        self._redraw()

    # ── matplotlib drawing ────────────────────────────────────────────────────
    def _redraw(self):
        if not _MPL_OK or self._canvas is None:
            return
        self._fig.clear()
        self._vline = None
        ax = self._fig.add_axes([0.10, 0.14, 0.86, 0.76])
        ax.set_facecolor("#ffffff")
        ax.tick_params(labelsize=8)

        if self._error_msg or self._counts is None:
            msg = self._error_msg or "Loading…"
            ax.text(0.5, 0.5, msg, ha="center", va="center",
                    transform=ax.transAxes, fontsize=9,
                    color="#cc0000" if self._error_msg else "#888888")
            self._canvas.draw_idle()
            return

        counts = self._counts
        n = len(counts)
        # Build bin centres for bar positions
        if self._bin_edges is not None and len(self._bin_edges) == n + 1:
            centres = (self._bin_edges[:-1] + self._bin_edges[1:]) / 2
            widths  = np.diff(self._bin_edges)
        else:
            span    = max(self._hi - self._lo, 1.0)
            bw      = span / n
            centres = self._lo + (np.arange(n) + 0.5) * bw
            widths  = np.full(n, bw)

        thr = float(self._threshold)
        colors = np.where(centres < thr, "#e09090", "#4a90d9")

        if self._log_scale:
            ax.set_yscale("log")
            # bar needs positive values — mask zeros
            mask = counts > 0
            if mask.any():
                ax.bar(centres[mask], counts[mask], width=widths[mask],
                       color=colors[mask], linewidth=0, align="center")
        else:
            ax.bar(centres, counts, width=widths, color=colors,
                   linewidth=0, align="center")

        # Use actual bin range for xlim so all bars are fully visible
        x_lo = float(centres[0]  - widths[0]  / 2)
        x_hi = float(centres[-1] + widths[-1] / 2)
        ax.set_xlim(x_lo, x_hi)
        ax.set_xlabel("Pixel intensity", fontsize=8)
        ax.set_ylabel("log count" if self._log_scale else "count", fontsize=8)

        self._vline = ax.axvline(thr, color="#cc0000", linewidth=1.5, zorder=5)
        ax.set_title(f"Threshold: {int(thr)}", fontsize=8, pad=2)
        self._canvas.draw_idle()

    def _update_vline(self):
        """Move threshold line without full redraw."""
        if not _MPL_OK or self._canvas is None or self._vline is None:
            self._redraw()
            return
        self._vline.set_xdata([float(self._threshold), float(self._threshold)])
        axes = self._fig.get_axes()
        if axes:
            axes[0].set_title(f"Threshold: {self._threshold}", fontsize=8, pad=2)
            # Re-color bars below/above threshold
            thr = float(self._threshold)
            for patch in axes[0].patches:
                cx = patch.get_x() + patch.get_width() / 2
                patch.set_facecolor("#e09090" if cx < thr else "#4a90d9")
        self._canvas.draw_idle()

    # ── mouse → threshold ─────────────────────────────────────────────────────
    def _axes_x_to_val(self, event) -> "int | None":
        if not _MPL_OK or self._canvas is None:
            return None
        axes = self._fig.get_axes()
        if not axes or event.inaxes is not axes[0]:
            return None
        val = int(round(float(event.xdata)))
        val = max(0, min(val, self._bit_depth))
        return val

    def _on_mpl_press(self, event):
        if event.button != 1:
            return
        val = self._axes_x_to_val(event)
        if val is not None:
            self._dragging = True
            self._set_thr(val)

    def _on_mpl_motion(self, event):
        if not self._dragging:
            return
        val = self._axes_x_to_val(event)
        if val is not None:
            self._set_thr(val)

    def _on_mpl_release(self, event):
        self._dragging = False

    def _set_thr(self, val: int):
        if val != self._threshold:
            self._threshold = val
            self._update_vline()
            self.threshold_changed.emit(val)


class _SCHistogramDialog(QDialog):
    """
    Popup dialog: shows a histogram for the current image,
    lets the user click/drag to set a threshold, previews the
    SC measurement in real time, and emits the accepted value.
    """
    threshold_accepted = Signal(int)
    threshold_preview  = Signal(int)   # debounced, for live SC recomputation

    # signals used to ferry results from background thread to main thread
    _sig_hist_ready = Signal(list, int, float, float, list)  # (counts, bit_depth, lo, hi, bin_edges)
    _sig_hist_error = Signal(str)

    def __init__(self, img_path: "Path", current_threshold: int,
                 bit_depth: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Threshold — Histogram")
        self.setMinimumSize(500, 300)
        self.resize(580, 340)

        # ── state ─────────────────────────────────────────────────────────────
        self._img_path  = img_path
        self._threshold = int(current_threshold)
        self._bit_depth = int(bit_depth)

        # ── layout ────────────────────────────────────────────────────────────
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # top row: status text  +  Log scale checkbox
        top = QHBoxLayout()
        self._info = QLabel("Loading histogram…")
        self._info.setStyleSheet("font-size: 10px; color: #555;")
        top.addWidget(self._info, 1)
        from PySide6.QtWidgets import QCheckBox
        self._log_cb = QCheckBox("Log scale")
        self._log_cb.setChecked(True)
        self._log_cb.setStyleSheet("font-size: 10px;")
        self._log_cb.toggled.connect(self._on_log_toggled)
        top.addWidget(self._log_cb)
        lay.addLayout(top)

        # histogram canvas
        self._hw = _SCHistogramWidget()
        # pre-configure bit_depth so the widget draws the threshold in the right
        # position even before the background worker finishes
        self._hw._bit_depth = self._bit_depth
        self._hw.set_threshold(self._threshold)
        self._hw.threshold_changed.connect(self._on_canvas_threshold)
        lay.addWidget(self._hw, 1)

        # spinbox row
        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Threshold:"))
        self._spin = QSpinBox()
        self._spin.setRange(0, self._bit_depth)
        self._spin.setValue(self._threshold)
        self._spin.setFixedWidth(90)
        self._spin.valueChanged.connect(self._on_spin_threshold)
        spin_row.addWidget(self._spin)
        spin_row.addStretch(1)
        lay.addLayout(spin_row)

        # buttons
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("Apply")
        btn_apply.setDefault(True)
        btn_apply.setStyleSheet(
            "QPushButton{background:#2d7dff;color:#fff;font-weight:700;"
            "border-radius:3px;padding:4px 18px;}"
            "QPushButton:hover{background:#1a6aee;}")
        btn_apply.clicked.connect(self._on_apply)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_apply)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)

        # wire cross-thread signals (must be done before thread starts)
        self._sig_hist_ready.connect(self._on_hist_ready)
        self._sig_hist_error.connect(self._on_hist_error)

        # start background worker
        import threading as _threading
        _threading.Thread(target=self._bg_load, daemon=True).start()

    # ── background worker ─────────────────────────────────────────────────────
    def _bg_load(self):
        try:
            from PIL import Image as _PIL
            img = _PIL.open(str(self._img_path))
            if img.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(img.convert("I"), dtype=np.float32)
            else:
                arr = np.asarray(img.convert("L"), dtype=np.float32)
            peak = float(arr.max())
            bd   = 65535 if peak > 255 else 255
            lo   = float(arr.min())
            hi   = float(peak)
            # Use 256 bins over the actual data range for good visual resolution
            span = max(hi - lo, 1.0)
            bins = np.linspace(lo, hi + 1, 257)
            counts, bin_edges = np.histogram(arr.ravel(), bins=bins)
            self._sig_hist_ready.emit(counts.tolist(), int(bd),
                                      float(lo), float(hi), bin_edges.tolist())
        except Exception as exc:
            self._sig_hist_error.emit(str(exc))

    # ── slots (main thread) ───────────────────────────────────────────────────
    def _on_hist_ready(self, counts_list: list, bd: int, lo: float, hi: float,
                       bin_edges_list: list):
        self._bit_depth = bd
        # update spin range first so setValue doesn't clamp
        self._spin.blockSignals(True)
        self._spin.setRange(0, bd)
        self._spin.setValue(self._threshold)
        self._spin.blockSignals(False)
        counts    = np.array(counts_list,    dtype=np.float64)
        bin_edges = np.array(bin_edges_list, dtype=np.float64) if bin_edges_list else None
        n_total = int(counts.sum())
        self._info.setText(
            f"{n_total:,} pixels  ·  {bd}-bit  ·  click or drag to set threshold")
        self._hw.load_data(counts, bd, self._threshold, lo, hi, bin_edges=bin_edges)

    def _on_hist_error(self, msg: str):
        self._info.setText(f"Could not load image: {msg}")
        self._hw.set_error(f"Could not load image:\n{msg}")

    def _on_log_toggled(self, on: bool):
        self._hw.set_log_scale(on)

    def _on_canvas_threshold(self, val: int):
        """Called when user drags the threshold line on the canvas."""
        self._threshold = val
        self._spin.blockSignals(True)
        self._spin.setValue(val)
        self._spin.blockSignals(False)
        self.threshold_preview.emit(val)

    def _on_spin_threshold(self, val: int):
        """Called when user edits the spinbox."""
        self._threshold = val
        self._hw.set_threshold(val)
        self.threshold_preview.emit(val)

    def _on_apply(self):
        self.threshold_accepted.emit(self._threshold)
        self.accept()


class _SCExclusionEditor(QDialog):
    """
    Modal dialog — user paints exclusion regions on the beam preview image.
    Accepts a QPixmap (the beam-mask preview) and returns a boolean numpy mask
    (True = excluded pixel) at the original image resolution.
    """
    exclusion_confirmed = Signal(object)   # np.ndarray bool mask, original resolution

    _DEFAULT_HOT_PCT = 99.0

    def __init__(self, preview_pm: "QPixmap", orig_shape: "tuple[int,int]",
                 img_path: "Path | None" = None, existing_mask: "np.ndarray | None" = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exclusion editor — left-drag: paint   right-drag: erase")
        self.setMinimumSize(1200, 1000)
        self._orig_h, self._orig_w = orig_shape
        self._orig_pm = preview_pm
        self._img_path = img_path
        self._existing_mask = existing_mask
        self._brush_size = 20

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── Single top toolbar with all controls ─────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Brush:"))
        self._brush_sb = QSpinBox()
        self._brush_sb.setRange(1, 400)
        self._brush_sb.setValue(self._brush_size)
        self._brush_sb.setFixedWidth(65)
        self._brush_sb.valueChanged.connect(self._on_brush_changed)
        toolbar.addWidget(self._brush_sb)

        btn_clear = QPushButton("⌫ Clear all")
        btn_clear.setToolTip("Remove all exclusion regions")
        btn_clear.clicked.connect(self._clear)
        toolbar.addWidget(btn_clear)

        toolbar.addSpacing(12)
        btn_auto = QPushButton("⚡ Auto-detect")
        btn_auto.setToolTip("Mark pixels above the threshold percentage of sensor max value")
        btn_auto.clicked.connect(self._auto_detect_hot)
        toolbar.addWidget(btn_auto)

        toolbar.addWidget(QLabel("Threshold:"))
        self._hot_pct_sb = QDoubleSpinBox()
        self._hot_pct_sb.setRange(1.0, 100.0)
        self._hot_pct_sb.setSingleStep(0.5)
        self._hot_pct_sb.setDecimals(1)
        self._hot_pct_sb.setValue(self._DEFAULT_HOT_PCT)
        self._hot_pct_sb.setSuffix(" %")
        self._hot_pct_sb.setFixedWidth(80)
        self._hot_pct_sb.setToolTip("Percentage of sensor max — pixels above this are marked as hotspots")
        toolbar.addWidget(self._hot_pct_sb)

        btn_reset_pct = QPushButton("↺")
        btn_reset_pct.setToolTip(f"Reset threshold to default ({self._DEFAULT_HOT_PCT:.0f} %)")
        btn_reset_pct.setFixedWidth(28)
        btn_reset_pct.clicked.connect(lambda: self._hot_pct_sb.setValue(self._DEFAULT_HOT_PCT))
        toolbar.addWidget(btn_reset_pct)

        toolbar.addSpacing(20)
        lbl_hint = QLabel("Left-drag: paint exclusion   ·   Right-drag: erase")
        lbl_hint.setStyleSheet("font-size: 11px; color: #555;")
        toolbar.addWidget(lbl_hint)

        toolbar.addStretch(1)

        btn_ok = QPushButton("✓ Apply")
        btn_ok.setDefault(False)
        btn_ok.setAutoDefault(False)
        btn_ok.setStyleSheet("QPushButton { font-weight: 700; padding: 4px 16px; }")
        btn_ok.clicked.connect(self._confirm)
        toolbar.addWidget(btn_ok)

        btn_cancel = QPushButton("✕ Cancel")
        btn_cancel.setDefault(False)
        btn_cancel.setAutoDefault(False)
        btn_cancel.clicked.connect(self.reject)
        toolbar.addWidget(btn_cancel)

        layout.addLayout(toolbar)

        # ── Canvas fills the rest ─────────────────────────────────────────
        self._canvas = _SCExclusionCanvas(preview_pm, parent=self)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._canvas, 1)

        self._brush_sb.valueChanged.connect(lambda v: setattr(self._canvas, '_brush_r', max(1, v // 2)))

        # Pre-load existing mask after the canvas is shown (needs display rect)
        if existing_mask is not None:
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(50, self._preload_existing_mask)

    def _on_brush_changed(self, v: int):
        self._canvas._brush_r = max(1, v // 2)

    def _clear(self):
        self._canvas.clear_mask()

    def _preload_existing_mask(self):
        """Load the existing mask into the canvas at display resolution."""
        if self._existing_mask is None:
            return
        from PIL import Image as _PIL
        ir = self._canvas._img_rect()
        disp_w, disp_h = ir.width(), ir.height()
        if disp_w <= 0 or disp_h <= 0:
            return
        self._canvas._ensure_mask(disp_w, disp_h)
        pil_mask = _PIL.fromarray(self._existing_mask.astype(np.uint8) * 255, mode="L")
        pil_mask = pil_mask.resize((disp_w, disp_h), _PIL.NEAREST)
        self._canvas._mask_arr = np.asarray(pil_mask) > 127
        self._canvas.update()

    def _auto_detect_hot(self):
        """Mark pixels above threshold % of sensor max value as excluded."""
        if self._img_path is None:
            return
        try:
            from PIL import Image as _PIL
            pil = _PIL.open(str(self._img_path))
            if pil.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(pil.convert("I"), dtype=np.float32)
            else:
                arr = np.asarray(pil.convert("L"), dtype=np.float32)
        except Exception:
            return

        # Determine bit depth from actual max value in image
        arr_max = float(arr.max())
        bit_depth = 65535.0 if arr_max > 255 else 255.0
        pct = self._hot_pct_sb.value() / 100.0
        thr_val = bit_depth * pct
        hot_mask_orig = arr >= thr_val   # H_orig × W_orig bool

        # Resize hot mask to display resolution and merge into canvas mask
        from PIL import Image as _PIL2
        ir = self._canvas._img_rect()
        disp_w, disp_h = ir.width(), ir.height()
        self._canvas._ensure_mask(disp_w, disp_h)

        pil_hot = _PIL2.fromarray(hot_mask_orig.astype(np.uint8) * 255, "L")
        pil_hot = pil_hot.resize((disp_w, disp_h), _PIL2.NEAREST)
        hot_disp = np.asarray(pil_hot) > 127

        self._canvas._mask_arr = hot_disp.copy()
        self._canvas.update()

    def _confirm(self):
        # Scale the display-resolution mask to original image resolution
        disp_mask = self._canvas.get_mask()   # H_disp × W_disp bool
        if disp_mask is None or not disp_mask.any():
            self.exclusion_confirmed.emit(None)
        else:
            from PIL import Image as _PIL
            # Resize mask to original resolution using nearest-neighbour
            pil_mask = _PIL.fromarray(disp_mask.astype(np.uint8) * 255, mode="L")
            pil_mask = pil_mask.resize((self._orig_w, self._orig_h), _PIL.NEAREST)
            orig_mask = np.asarray(pil_mask) > 127
            self.exclusion_confirmed.emit(orig_mask)
        self.accept()


class _SCExclusionCanvas(QWidget):
    """Canvas widget inside the exclusion editor — renders preview + painted mask."""

    def __init__(self, preview_pm: "QPixmap", parent=None):
        super().__init__(parent)
        self._preview_pm = preview_pm
        self._brush_r = 10
        self._painting = False
        self._erase    = False
        self._last_pos: "QPoint | None" = None
        # Mask stored at display resolution (updated on resize)
        self._disp_w = 0
        self._disp_h = 0
        self._mask_arr: "np.ndarray | None" = None   # bool H×W at display res
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _ensure_mask(self, w: int, h: int):
        if self._mask_arr is None or self._disp_w != w or self._disp_h != h:
            # Scale existing mask if we already have one
            if self._mask_arr is not None and self._mask_arr.any():
                from PIL import Image as _PIL
                pil = _PIL.fromarray(self._mask_arr.astype(np.uint8) * 255, "L")
                pil = pil.resize((w, h), _PIL.NEAREST)
                self._mask_arr = np.asarray(pil) > 127
            else:
                self._mask_arr = np.zeros((h, w), dtype=bool)
            self._disp_w = w
            self._disp_h = h

    def clear_mask(self):
        if self._mask_arr is not None:
            self._mask_arr[:] = False
        self.update()

    def get_mask(self) -> "np.ndarray | None":
        return self._mask_arr

    def _img_rect(self) -> "QRect":
        """Return the rect where the preview image is drawn (aspect-ratio fitted)."""
        pm = self._preview_pm
        w, h = self.width(), self.height()
        scale = min(w / pm.width(), h / pm.height())
        iw = int(pm.width()  * scale)
        ih = int(pm.height() * scale)
        x0 = (w - iw) // 2
        y0 = (h - ih) // 2
        from PySide6.QtCore import QRect
        return QRect(x0, y0, iw, ih)

    def _canvas_to_mask(self, pos: "QPoint") -> "tuple[int,int]":
        """Convert widget coords to mask array (col, row). Mask lives at ir resolution."""
        ir = self._img_rect()
        col = int((pos.x() - ir.x()))
        row = int((pos.y() - ir.y()))
        col = max(0, min(col, self._disp_w - 1))
        row = max(0, min(row, self._disp_h - 1))
        return col, row

    def _paint_circle(self, pos: "QPoint", erase: bool):
        ir = self._img_rect()
        # Mask is stored at display (ir) resolution — 1 mask pixel = 1 screen pixel
        self._ensure_mask(ir.width(), ir.height())
        col, row = self._canvas_to_mask(pos)
        rr = max(1, self._brush_r)
        y0 = max(0, row - rr); y1 = min(self._disp_h, row + rr + 1)
        x0 = max(0, col - rr); x1 = min(self._disp_w, col + rr + 1)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        circle = (yy - row)**2 + (xx - col)**2 <= rr**2
        self._mask_arr[y0:y1, x0:x1][circle] = not erase
        self.update()

    def mousePressEvent(self, event):
        self._painting = True
        self._erase = (event.button() == Qt.MouseButton.RightButton)
        self._last_pos = event.position().toPoint()
        self._paint_circle(self._last_pos, self._erase)

    def mouseMoveEvent(self, event):
        if not self._painting:
            return
        pos = event.position().toPoint()
        self._paint_circle(pos, self._erase)
        self._last_pos = pos

    def mouseReleaseEvent(self, event):
        self._painting = False

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111"))
        ir = self._img_rect()
        scaled = self._preview_pm.scaled(
            ir.width(), ir.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(ir.x(), ir.y(), scaled)

        # Draw exclusion mask as semi-transparent red overlay
        if self._mask_arr is not None and self._mask_arr.any():
            from PySide6.QtGui import QImage
            h, w = self._mask_arr.shape
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[self._mask_arr, 0] = 220
            rgba[self._mask_arr, 3] = 160   # semi-transparent red
            qi = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
            pm_overlay = QPixmap.fromImage(qi).scaled(
                ir.width(), ir.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation)
            p.drawPixmap(ir.x(), ir.y(), pm_overlay)

        # Draw brush cursor
        if self._last_pos is not None:
            p.setPen(QColor(255, 255, 0, 180))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self._last_pos, self._brush_r, self._brush_r)
        p.end()


class _SCValueLabel(QLabel):
    """Result value cell — double-click copies the text to clipboard."""
    def mouseDoubleClickEvent(self, event):
        txt = self.text()
        if txt and txt != "—":
            QApplication.clipboard().setText(txt)
            orig = self.styleSheet()
            self.setStyleSheet(orig.replace("background: #f5f5f5", "background: #b3e5fc")
                                   .replace("background: #e8f0fe", "background: #b3e5fc"))
            from PySide6.QtCore import QTimer
            QTimer.singleShot(300, lambda: self.setStyleSheet(orig))
        super().mouseDoubleClickEvent(event)


class _SCPreviewLabel(QLabel):
    """Compact preview in the left panel; shows full-size popup on mouse hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #111; border: 1px solid #444; border-radius: 2px;")
        self.setFixedHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._popup: "QLabel | None" = None

    def set_full_pixmap(self, pm: "QPixmap"):
        """Store full-res pixmap and update thumbnail."""
        self._full_pm = pm
        self._refresh_thumb()

    def _refresh_thumb(self):
        if not hasattr(self, "_full_pm") or self._full_pm is None:
            return
        scaled = self._full_pm.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_thumb()

    def enterEvent(self, event):
        if not hasattr(self, "_full_pm") or self._full_pm is None:
            return
        if self._popup is not None:
            self._popup.close()
        # Find the image view area to size the popup
        app = QApplication.instance()
        screen = app.primaryScreen().availableGeometry() if app else None

        pm = self._full_pm
        max_w = (screen.width()  * 3 // 4) if screen else 1200
        max_h = (screen.height() * 3 // 4) if screen else 800
        scaled = pm.scaled(max_w, max_h,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)

        popup = QLabel(None)
        popup.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        popup.setPixmap(scaled)
        popup.resize(scaled.width(), scaled.height())
        popup.setStyleSheet("background: #111; border: 2px solid #555;")

        # Position: center on screen
        if screen:
            x = screen.x() + (screen.width()  - scaled.width())  // 2
            y = screen.y() + (screen.height() - scaled.height()) // 2
            popup.move(x, y)

        popup.show()
        self._popup = popup

    def leaveEvent(self, event):
        if self._popup is not None:
            self._popup.close()
            self._popup = None

    def hideEvent(self, event):
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        super().hideEvent(event)


class _SCSignals(QObject):
    finished  = Signal(object)   # dict with results, or None on error
    preview   = Signal(object)   # QPixmap preview of mask

class _SCTask(QRunnable):
    """Compute Spatial Contrast for the current frame on a background thread."""
    def __init__(self, img_path: "Path", threshold: int, signals: "_SCSignals",
                 exclusion_mask: "np.ndarray | None" = None):
        super().__init__()
        self.setAutoDelete(True)
        self._path           = img_path
        self._threshold      = threshold
        self._signals        = signals
        self._exclusion_mask = exclusion_mask  # bool H×W, True = excluded

    @staticmethod
    def _make_preview_pixmap(arr_8bit: "np.ndarray", mask: "np.ndarray") -> "QPixmap":
        """Create an RGBA preview: beam region = original gray, background = dim red tint."""
        from PySide6.QtGui import QImage, QPixmap
        h, w = arr_8bit.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        # beam pixels: white-ish
        rgba[mask,  0] = arr_8bit[mask]
        rgba[mask,  1] = arr_8bit[mask]
        rgba[mask,  2] = arr_8bit[mask]
        rgba[mask,  3] = 255
        # background: dark red tint
        rgba[~mask, 0] = np.minimum(arr_8bit[~mask].astype(np.uint16) + 80, 255).astype(np.uint8)
        rgba[~mask, 1] = (arr_8bit[~mask] * 0.3).astype(np.uint8)
        rgba[~mask, 2] = (arr_8bit[~mask] * 0.3).astype(np.uint8)
        rgba[~mask, 3] = 255
        qi = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        return QPixmap.fromImage(qi)

    def run(self):
        try:
            from PIL import Image as _PIL
        except ImportError:
            self._signals.finished.emit(None)
            return
        try:
            pil = _PIL.open(str(self._path))
            # Keep full bit depth: 16-bit → int32 array, 8-bit → uint8
            if pil.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(pil.convert("I"), dtype=np.float32).copy()
            else:
                arr = np.asarray(pil.convert("L"), dtype=np.float32).copy()
        except Exception:
            self._signals.finished.emit(None)
            return

        if arr.size == 0:
            self._signals.finished.emit(None)
            return

        arr_max = float(arr.max())
        bit_depth = 65535.0 if arr_max > 255 else 255.0

        # Threshold is in raw pixel units (0–65535 for 16-bit, 0–255 for 8-bit)
        thr = float(self._threshold)
        mask = arr > thr

        # Fill holes so that dark regions INSIDE the beam boundary are included.
        # Strategy: closing (dilate→fill_holes→erode) ensures the beam edge is
        # connected before filling, so even beams with thin dark gaps get filled.
        try:
            from scipy.ndimage import binary_fill_holes, binary_dilation, binary_erosion
            # Closing radius — large enough to bridge typical interference gaps
            struct = np.ones((7, 7), dtype=bool)
            mask_closed = binary_dilation(mask, structure=struct)
            mask_closed = binary_fill_holes(mask_closed)
            mask_closed = binary_erosion(mask_closed, structure=struct)
            # Combine: original threshold mask OR the filled interior
            mask = mask | mask_closed
        except ImportError:
            pass  # scipy not available — threshold-only mask

        # Apply user-drawn exclusion regions
        n_excluded = 0
        if self._exclusion_mask is not None:
            excl = self._exclusion_mask
            # Resize exclusion mask to image resolution if needed
            if excl.shape != arr.shape:
                from PIL import Image as _PIL2
                pil_excl = _PIL2.fromarray(excl.astype(np.uint8) * 255, "L")
                pil_excl = pil_excl.resize((arr.shape[1], arr.shape[0]), _PIL2.NEAREST)
                excl = np.asarray(pil_excl) > 127
            n_excluded = int(excl.sum())
            mask = mask & ~excl

        # Pixels available for measurement (excluded zones are absent, not zero)
        n_valid = arr.size - n_excluded

        # For preview, normalise to 8-bit for display only
        arr_8bit = np.clip(arr / bit_depth * 255.0, 0, 255).astype(np.uint8)
        self._signals.preview.emit(self._make_preview_pixmap(arr_8bit, mask))

        beam = arr[mask]
        if beam.size == 0:
            self._signals.finished.emit({
                "error": "No pixels above threshold — lower the threshold.",
                "mean": None, "min": None, "max": None,
                "sc": None, "n_beam": 0, "n_total": n_valid,
                "threshold": self._threshold,
                "bit_depth": int(bit_depth),
            })
            return

        mean_val = float(beam.mean())
        min_val  = float(beam.min())
        max_val  = float(beam.max())
        sc       = max_val / mean_val if mean_val > 0 else float("inf")

        # Top-N pixel coordinates (y, x) sorted by descending intensity in original array
        h_full, w_full = arr.shape
        ys_all, xs_all = np.where(mask)
        if ys_all.size > 0:
            vals = arr[ys_all, xs_all]
            order = np.argsort(vals)[::-1]
            top_ys = ys_all[order].tolist()
            top_xs = xs_all[order].tolist()
        else:
            top_ys, top_xs = [], []

        self._signals.finished.emit({
            "error":      None,
            "mean":       mean_val,
            "min":        min_val,
            "max":        max_val,
            "sc":         sc,
            "n_beam":     int(mask.sum()),
            "n_total":    n_valid,
            "threshold":  self._threshold,
            "bit_depth":  int(bit_depth),
            "img_shape":  (h_full, w_full),
            "top_xs":     top_xs,
            "top_ys":     top_ys,
        })

    @staticmethod
    def otsu_threshold_raw(arr_raw: "np.ndarray", bit_depth: float) -> int:
        """Otsu's inter-class variance maximisation. Returns threshold in raw pixel units."""
        # Work on 8-bit projection for speed; rescale result back to raw units
        arr_8 = np.clip(arr_raw / bit_depth * 255.0, 0, 255).astype(np.uint8)
        hist, _ = np.histogram(arr_8.ravel(), bins=256, range=(0, 256))
        hist = hist.astype(np.float64)
        total = hist.sum()
        if total == 0:
            return int(bit_depth * 0.04)  # ~2600 for 16-bit
        sum_b, w_b, max_var, thr_8 = 0.0, 0.0, 0.0, 0
        sum_total = float(np.dot(np.arange(256, dtype=np.float64), hist))
        for t in range(256):
            w_b += hist[t]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * hist[t]
            mb = sum_b / w_b
            mf = (sum_total - sum_b) / w_f
            var = w_b * w_f * (mb - mf) ** 2
            if var > max_var:
                max_var = var
                thr_8 = t
        return int(round(thr_8 / 255.0 * bit_depth))


class PointingPanel(QWidget):
    """Inline panel se scatter+hist grafy pointing stability."""
    point_clicked  = Signal(int)    # index into stored arrays
    region_deleted = Signal()       # emitted after points deleted

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(350)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if _MPL_OK:
            self._fig = plt.figure(figsize=(10, 5))
            self._canvas = FigureCanvas(self._fig)
            self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        else:
            self._fig = None
            self._canvas = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if _MPL_OK and self._canvas is not None:
            lay.addWidget(self._canvas)
        self.setVisible(False)
        self._cx = None
        self._cy = None
        self._ts = None         # float64 for path coloring
        self._ts_int = None     # int64 for timestamp lookups
        self._mask = None       # bool array; False = deleted
        self._replay_ts: int | None = None   # if set, only show points with ts <= this
        self._show_path = False
        self._select_mode = False
        self._rect_selector = None
        self._hover_annot = None  # matplotlib annotation for hover tooltip
        if _MPL_OK and self._canvas is not None:
            self._canvas.mpl_connect("button_press_event", self._on_mpl_click)
            self._canvas.mpl_connect("motion_notify_event", self._on_mpl_hover)

    def plot(self, cx_urad, cy_urad, n_shots, ts_ns=None, ts_ns_int=None):
        if not _MPL_OK: return
        self._cx = cx_urad
        self._cy = cy_urad
        self._ts = ts_ns          # float64 for path coloring
        self._ts_int = ts_ns_int  # int64 for navigation
        self._mask = np.ones(len(cx_urad), dtype=bool)
        self._replay_ts = None    # reset replay on new data
        self._select_mode = False
        self._rect_selector = None
        self._draw()
        self.setVisible(True)

    def toggle_path(self):
        self._show_path = not self._show_path
        if self._cx is not None:
            self._draw()
        return self._show_path

    def toggle_select_mode(self):
        """Enable/disable rubber-band selection for point deletion."""
        if not _MPL_OK or self._cx is None:
            return False
        self._select_mode = not self._select_mode
        if self._select_mode:
            from matplotlib.widgets import RectangleSelector
            # We need ax_main — get the first axes in the figure
            axes = self._fig.get_axes()
            ax_main = axes[1] if len(axes) > 1 else (axes[0] if axes else None)
            if ax_main is None:
                self._select_mode = False
                return False
            self._rect_selector = RectangleSelector(
                ax_main, self._on_rect_selected,
                useblit=True, button=[1],
                minspanx=0, minspany=0,
                spancoords="data", interactive=True)
        else:
            if self._rect_selector is not None:
                self._rect_selector.set_active(False)
                self._rect_selector = None
        return self._select_mode

    def _on_rect_selected(self, eclick, erelease):
        """Delete points inside the rubber-band rectangle."""
        if self._cx is None or self._mask is None:
            return
        x0, x1 = sorted([eclick.xdata, erelease.xdata])
        y0, y1 = sorted([eclick.ydata, erelease.ydata])
        cx = self._cx[self._mask]
        cy = self._cy[self._mask]
        # Build indices into original arrays for currently-visible points
        vis_indices = np.where(self._mask)[0]
        inside = (cx >= x0) & (cx <= x1) & (cy >= y0) & (cy <= y1)
        self._mask[vis_indices[inside]] = False
        # Deactivate selector so user needs to re-enable for next selection
        if self._rect_selector is not None:
            self._rect_selector.set_active(False)
            self._rect_selector = None
        self._select_mode = False
        self._draw()
        self.region_deleted.emit()

    def restore_all_points(self):
        """Un-delete all previously deleted points."""
        if self._mask is not None:
            self._mask[:] = True
            self._draw()
            self.region_deleted.emit()

    def set_replay_ts(self, ts_ns: "int | None"):
        """Limit visible points to those with timestamp <= ts_ns (None = show all)."""
        if not _MPL_OK or self._cx is None:
            return
        changed = self._replay_ts != ts_ns
        self._replay_ts = ts_ns
        if changed:
            self._draw()

    def _on_mpl_click(self, event):
        """Click on scatter plot → emit point_clicked(index) for navigation."""
        if not _MPL_OK or self._cx is None or self._mask is None:
            return
        if self._select_mode:
            return  # let RectangleSelector handle it
        axes = self._fig.get_axes()
        ax_main = axes[1] if len(axes) > 1 else (axes[0] if axes else None)
        if ax_main is None or event.inaxes is not ax_main:
            return
        if event.xdata is None or event.ydata is None:
            return
        # Find nearest visible point in data coordinates
        cx_vis = self._cx[self._mask]
        cy_vis = self._cy[self._mask]
        vis_indices = np.where(self._mask)[0]
        if len(cx_vis) == 0:
            return
        # Normalize by axis range so x/y are equally weighted
        xlim = ax_main.get_xlim()
        ylim = ax_main.get_ylim()
        xrange = max(xlim[1] - xlim[0], 1e-12)
        yrange = max(ylim[1] - ylim[0], 1e-12)
        dx = (cx_vis - event.xdata) / xrange
        dy = (cy_vis - event.ydata) / yrange
        dist2 = dx * dx + dy * dy
        nearest_vis = int(np.argmin(dist2))
        # Only trigger if click is within 3% of axis range
        if dist2[nearest_vis] > 0.03 ** 2:
            return
        orig_idx = int(vis_indices[nearest_vis])
        self.point_clicked.emit(orig_idx)

    def _on_mpl_hover(self, event):
        """Show timestamp tooltip when hovering near a scatter point."""
        if not _MPL_OK or self._cx is None or self._mask is None or self._ts_int is None:
            return
        axes = self._fig.get_axes()
        ax_main = axes[1] if len(axes) > 1 else (axes[0] if axes else None)
        if ax_main is None:
            return
        # Apply both user mask and replay mask
        mask = self._mask.copy()
        if self._replay_ts is not None:
            mask &= (self._ts_int <= self._replay_ts)
        cx_vis = self._cx[mask]
        cy_vis = self._cy[mask]
        vis_indices = np.where(mask)[0]
        need_draw = False
        if event.inaxes is not ax_main or event.xdata is None or len(cx_vis) == 0:
            if self._hover_annot is not None:
                self._hover_annot.set_visible(False)
                self._hover_annot = None
                need_draw = True
        else:
            xlim = ax_main.get_xlim(); ylim = ax_main.get_ylim()
            xrange = max(xlim[1] - xlim[0], 1e-12)
            yrange = max(ylim[1] - ylim[0], 1e-12)
            dx = (cx_vis - event.xdata) / xrange
            dy = (cy_vis - event.ydata) / yrange
            dist2 = dx * dx + dy * dy
            nearest_vis = int(np.argmin(dist2))
            if dist2[nearest_vis] <= 0.025 ** 2:
                orig_idx = int(vis_indices[nearest_vis])
                ts_ns = int(self._ts_int[orig_idx])
                ts_str = fmt_prague_full_from_ns(ts_ns)
                px, py = float(self._cx[orig_idx]), float(self._cy[orig_idx])
                if self._hover_annot is None:
                    self._hover_annot = ax_main.annotate(
                        ts_str, xy=(px, py),
                        xytext=(10, 10), textcoords="offset points",
                        bbox=dict(boxstyle="round,pad=0.3", fc="#ffffcc", ec="#888", lw=0.8),
                        fontsize=8, zorder=10)
                else:
                    self._hover_annot.set_text(ts_str)
                    self._hover_annot.xy = (px, py)
                self._hover_annot.set_visible(True)
                need_draw = True
            else:
                if self._hover_annot is not None:
                    self._hover_annot.set_visible(False)
                    self._hover_annot = None
                    need_draw = True
        if need_draw:
            self._canvas.draw_idle()

    def _draw(self):
        if not _MPL_OK: return
        self._hover_annot = None  # annotation belongs to old axes — will be recreated
        self._fig.clear()
        self._render_to_fig(self._fig)
        self._canvas.draw()

    def _render_to_fig(self, fig):
        mask = self._mask.copy() if self._mask is not None else np.ones(len(self._cx), dtype=bool)
        if self._replay_ts is not None and self._ts_int is not None:
            mask &= (self._ts_int <= self._replay_ts)
        cx_urad = self._cx[mask]
        cy_urad = self._cy[mask]
        n_shots = len(cx_urad)
        if n_shots == 0:
            return
        x_std = float(np.std(cx_urad))
        y_std = float(np.std(cy_urad))
        ts_masked = self._ts[mask] if self._ts is not None else None

        if self._show_path and ts_masked is not None:
            fig.subplots_adjust(
                left=0.08, right=0.96, top=0.93, bottom=0.10,
                wspace=0.45, hspace=0.08)
            ax_histx = fig.add_axes([0.08, 0.72, 0.36, 0.18])
            ax_main  = fig.add_axes([0.08, 0.10, 0.36, 0.60], sharex=ax_histx)
            ax_histy = fig.add_axes([0.45, 0.10, 0.07, 0.60], sharey=ax_main)
            ax_path  = fig.add_axes([0.58, 0.10, 0.34, 0.80])
        else:
            fig.subplots_adjust(
                left=0.10, right=0.97, top=0.93, bottom=0.10,
                wspace=0.08, hspace=0.08)
            ax_histx = fig.add_axes([0.10, 0.72, 0.70, 0.18])
            ax_main  = fig.add_axes([0.10, 0.10, 0.70, 0.60], sharex=ax_histx)
            ax_histy = fig.add_axes([0.81, 0.10, 0.16, 0.60], sharey=ax_main)
            ax_path  = None

        n_deleted = int((~mask).sum())
        deleted_note = f"  [{n_deleted} deleted]" if n_deleted else ""

        # ── Scatter ──────────────────────────────────────────────
        ax_main.scatter(cx_urad, cy_urad, s=6, alpha=0.4, color="#2d7dff",
                        rasterized=True)
        ax_main.axhline(0, color="#aaa", linewidth=0.8, linestyle="--")
        ax_main.axvline(0, color="#aaa", linewidth=0.8, linestyle="--")
        ax_main.set_xlabel("X (px from centre)", fontsize=8)
        ax_main.set_ylabel("Y (px from centre, ↓ positive)", fontsize=8)
        ax_main.tick_params(labelsize=7)
        # Match image coordinates: Y increases downward, so positive = below centre
        ax_main.invert_yaxis()

        # ── Histogramy ───────────────────────────────────────────
        bins = min(32, max(8, n_shots // 10))
        ax_histx.hist(cx_urad, bins=bins, color="#2d7dff", alpha=0.7)
        ax_histy.hist(cy_urad, bins=bins, color="#2d7dff", alpha=0.7,
                      orientation="horizontal")
        ax_histx.tick_params(labelbottom=False, labelsize=7)
        ax_histy.tick_params(labelleft=False, labelsize=7)
        ax_histx.set_title(
            f"N={n_shots}  σX={x_std:.1f}  σY={y_std:.1f} px{deleted_note}",
            fontsize=8, pad=3)

        # ── Path ─────────────────────────────────────────────────
        if ax_path is not None and ts_masked is not None:
            from matplotlib.collections import LineCollection
            ts = ts_masked.astype(np.float64)
            t_norm = (ts - ts.min()) / max(float(ts.max() - ts.min()), 1.0)

            points = np.array([cx_urad, cy_urad]).T.reshape(-1, 1, 2)
            segments = np.concatenate([points[:-1], points[1:]], axis=1)
            lc = LineCollection(segments, cmap="coolwarm", alpha=0.7,
                                linewidth=1.0, rasterized=True)
            lc.set_array(t_norm[:-1])
            ax_path.add_collection(lc)

            ax_path.scatter(cx_urad[0],  cy_urad[0],  s=50, color="blue",
                            zorder=5, label="Start", marker="o")
            ax_path.scatter(cx_urad[-1], cy_urad[-1], s=50, color="red",
                            zorder=5, label="End",   marker="X")

            pad_x = max(x_std * 0.5, 0.05)
            pad_y = max(y_std * 0.5, 0.05)
            ax_path.set_xlim(cx_urad.min() - pad_x, cx_urad.max() + pad_x)
            ax_path.set_ylim(cy_urad.max() + pad_y, cy_urad.min() - pad_y)  # inverted: positive Y = down
            ax_path.set_xlabel("X (px from centre)", fontsize=8)
            ax_path.set_title("Beam path", fontsize=8, pad=3)
            ax_path.legend(fontsize=7, loc="upper right",
                           handlelength=1, borderpad=0.4)
            ax_path.axhline(0, color="#aaa", linewidth=0.8, linestyle="--")
            ax_path.axvline(0, color="#aaa", linewidth=0.8, linestyle="--")
            ax_path.tick_params(labelsize=7)

            sm = plt.cm.ScalarMappable(cmap="coolwarm",
                                        norm=plt.Normalize(0, 1))
            sm.set_array([])
            cbar = fig.colorbar(sm, ax=ax_path, fraction=0.06, pad=0.02)
            cbar.set_ticks([0, 0.5, 1])
            cbar.set_ticklabels(["Start", "Mid", "End"])
            cbar.ax.tick_params(labelsize=7)

    def save_figure(self, path: str):
        if not _MPL_OK: return
        import matplotlib.pyplot as _plt
        show_path = self._show_path and self._ts is not None

        if show_path:
            save_fig = _plt.figure(figsize=(16, 7))
        else:
            save_fig = _plt.figure(figsize=(10, 7))

        self._render_to_fig(save_fig)
        try:
            save_fig.tight_layout(pad=1.5)
        except Exception:
            pass
        save_fig.savefig(path, dpi=200, bbox_inches="tight")
        _plt.close(save_fig)

class WeekendDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        col = index.column()
        if col < 1:
            return
        # Zkus UserRole (aktuální měsíc)
        date = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(date, QDate) and date.isValid():
            if date.dayOfWeek() in (6, 7):
                option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
                option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))
            return
        # Fallback pro dny mimo měsíc — DisplayRole je string "1"–"31"
        # Spočítáme datum ze sloupce a aktuální stránky kalendáře
        # Nelze spolehlivě bez přístupu ke kalendáři, takže červeníme jen So/Ne sloupce
        # ALE pouze pokud locale má Po jako první den (ISO)
        # Bezpečnější fallback: zkontroluj DisplayRole text a sloupec
        # Qt ISO: col 1=Po,2=Út,3=St,4=Čt,5=Pá,6=So,7=Ne
        if col in (6, 7):
            option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
            option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))

def _hsep_dialog() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #ccc; margin: 2px 0;")
    return f

# ---------------- DATE PICKER DIALOG ----------------
class DatePickerDialog(QDialog):
    def __init__(self, start_folder=None, hour_from_init=None, hour_to_init=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pick date")
        self._camera_mode = False  # set to True by open_folder

        init_dt = datetime.now(TZ_PRAGUE)
        init_hour = init_dt.hour
        # Remember if caller passed explicit hours — if not, default to online mode
        _explicit_hours = (hour_from_init is not None or hour_to_init is not None)

        if start_folder is not None:
            ax = axis_from_any_folder(start_folder)
            if ax is not None:
                try:
                    dt0 = _dt_from_ns(ax[0])
                    init_dt = dt0; init_hour = dt0.hour
                except Exception:
                    pass

        if hour_from_init is None: hour_from_init = init_hour
        if hour_to_init is None:   hour_to_init   = init_hour

        self.cal = QCalendarWidget(self)
        self.cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        view = self.cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view:
            view.setItemDelegate(WeekendDelegate(view))
        self.cal.setSelectedDate(QDate(init_dt.year, init_dt.month, init_dt.day))
        self.cal.setGridVisible(True)
        self.cal.setNavigationBarVisible(False)

        hf = QTextCharFormat()
        hf = QTextCharFormat()
        hf.setForeground(QColor("#111111"))
        self.cal.setHeaderTextFormat(hf)

        wf = QTextCharFormat()
        wf.setForeground(QColor("#111111"))
        for day in [Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                    Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday]:
            self.cal.setWeekdayTextFormat(day, wf)

        wf_weekend = QTextCharFormat()
        wf_weekend.setForeground(QColor("#cc0000"))
        for day in [Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday]:
            self.cal.setWeekdayTextFormat(day, wf_weekend)

        self.cal.setStyleSheet("""
        QCalendarWidget QWidget { background: #f6f6f6; color: #111; }
        QCalendarWidget QAbstractItemView {
            background: #fcfcfc; color: #111;
            selection-background-color: #2d7dff; selection-color: #fff;
            alternate-background-color: #f2f2f2; gridline-color: #d8d8d8; }
        QCalendarWidget QTableView {
            background: #fcfcfc; alternate-background-color: #b5b5b5;
            selection-background-color: #2d7dff; selection-color: #fff;
            gridline-color: #d8d8d8; outline: 0; }
        QCalendarWidget QToolButton {
            background: #efefef; border: 1px solid #c8c8c8;
            padding: 4px 8px; border-radius: 4px; color: #111; }
        QCalendarWidget QSpinBox, QCalendarWidget QComboBox {
            background: #fff; border: 1px solid #c8c8c8; padding: 2px 6px; color: #111; }
        QCalendarWidget QAbstractItemView:enabled {
            color: #111; }
        QCalendarWidget QAbstractItemView:enabled {
            color: #cc0000; }
        """)

        self.month_cb = QComboBox(self)
        for m in range(1, 13):
            self.month_cb.addItem(datetime(2000, m, 1).strftime("%B").capitalize(), m)
        self.month_cb.setCurrentIndex(init_dt.month - 1)
        self.month_cb.setMinimumWidth(120)

        self.year_sb = QSpinBox(self)
        self.year_sb.setRange(2000, 2100); self.year_sb.setValue(init_dt.year); self.year_sb.setFixedWidth(80)

        self.hour_from = QSpinBox(self)
        self.hour_from.setRange(0, 23); self.hour_from.setValue(max(0, min(23, int(hour_from_init)))); self.hour_from.setFixedWidth(70)

        self.hour_to = QSpinBox(self)
        self.hour_to.setRange(0, 23); self.hour_to.setValue(max(0, min(23, int(hour_to_init)))); self.hour_to.setFixedWidth(70)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self._on_accept); btns.rejected.connect(self.reject)

        _cb_style = _CHECKBOX_STYLE + (
            "QCheckBox { font-size: 11px; font-weight: 700; color: #1a3a8f; }"
        )
        self.cb_now = QCheckBox("Now (online mode)")
        self.cb_now.setToolTip(
            "To hour = current hour. Viewer will automatically load new images as they arrive.")
        self.cb_now.setStyleSheet(_cb_style)
        self.cb_now.stateChanged.connect(self._on_now_changed)

        # ── Multi-day checkbox + To-date calendar ──────────────────────────────
        self.cb_multiday = QCheckBox("Multi-day (select end date below)")
        self.cb_multiday.setChecked(False)
        self.cb_multiday.setStyleSheet(_cb_style)

        self.cal_to = QCalendarWidget(self)
        self.cal_to.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        view2 = self.cal_to.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view2:
            view2.setItemDelegate(WeekendDelegate(view2))
        self.cal_to.setSelectedDate(QDate(init_dt.year, init_dt.month, init_dt.day))
        self.cal_to.setGridVisible(True)
        self.cal_to.setNavigationBarVisible(False)
        # Same styling as cal
        self.cal_to.setHeaderTextFormat(hf)
        for day in [Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                    Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday]:
            self.cal_to.setWeekdayTextFormat(day, wf)
        for day in [Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday]:
            self.cal_to.setWeekdayTextFormat(day, wf_weekend)
        self.cal_to.setStyleSheet(self.cal.styleSheet())
        self.cal_to.setVisible(False)
        self._lbl_cal_to = QLabel("End date:")
        self._lbl_cal_to.setVisible(False)

        self.cb_multiday.stateChanged.connect(self._on_multiday_toggled)

        btn_now = QPushButton("Now")
        btn_now.setToolTip("Set date and hour to current date and time")
        btn_now.setFixedWidth(48)
        btn_now.clicked.connect(self._go_to_now)

        top = QHBoxLayout()
        top.addWidget(QLabel("Year:")); top.addWidget(self.year_sb); top.addSpacing(10)
        top.addWidget(QLabel("Month:")); top.addWidget(self.month_cb); top.addSpacing(10)
        top.addWidget(QLabel("From hour:")); top.addWidget(self.hour_from); top.addSpacing(10)
        top.addWidget(QLabel("To hour:")); top.addWidget(self.hour_to)
        top.addSpacing(10); top.addWidget(btn_now)
        top.addSpacing(10); top.addWidget(self.cb_now); top.addStretch(1)

        self.cal.selectionChanged.connect(self._sync_controls_from_calendar)
        self.month_cb.currentIndexChanged.connect(self._on_year_month_changed)
        self.year_sb.valueChanged.connect(self._on_year_month_changed)
        self.hour_from.valueChanged.connect(self._on_hours_changed)
        self.hour_to.valueChanged.connect(self._on_hours_changed)
        self._sync_controls_from_calendar()
        # Spusť scan kamer hned při otevření dialogu
        self._cam_signals = _CamLoaderSignals()
        self._cam_signals.finished.connect(self._on_cameras_preloaded)
        self._preloaded: list[tuple[str, str]] = []
        self._load_cameras_bg()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Select date and hour range"))
        lay.addLayout(top)
        lay.addWidget(self.cb_multiday)
        lay.addWidget(QLabel("Start date:"))
        lay.addWidget(self.cal)
        lay.addWidget(self._lbl_cal_to)
        lay.addWidget(self.cal_to)
        lay.addWidget(btns)

        # Default: online mode on (current hour) when no explicit hours were passed
        if not _explicit_hours:
            self.cb_now.setChecked(True)

    @staticmethod
    def selected_folders_static(date, hour_from, hour_to, camera_folder: Path,
                                 extra_dates: "list | None" = None) -> list[Path]:
        """
        Return list of archiver folder Paths for a camera over a date+hour range.
        If extra_dates is given (list of date objects), include all those dates too.
        Hours are interpreted as Prague lab time (7–21 for multi-day).
        """
        cam_name = camera_folder.name
        all_dates = [date] if extra_dates is None else extra_dates
        out = []
        for dt_day in all_dates:
            y, m, day = dt_day.year, dt_day.month, dt_day.day
            for hh in range(hour_from, hour_to + 1):
                ref_dt = datetime(y, m, day, hh, 0, 0, tzinfo=TZ_PRAGUE)
                folder_hh = folder_hour_from_prague_hour(hh, ref_dt)
                out.append(Path(DEFAULT_OPEN_ROOT) / str(y) / str(m) / str(day) / str(folder_hh) / cam_name)
        return out

    def preloaded_cameras(self) -> list[tuple[str, str]]:
        return self._preloaded

    
    def _load_cameras_bg(self):
        import threading as _thr
        date_obj  = self.selected_date_obj()
        hour_from = self.hour_from.value()
        hour_to   = self.hour_to.value()
        signals   = self._cam_signals

        def worker():
            cameras: list[tuple[str, str]] = []
            try:
                base = Path(DEFAULT_OPEN_ROOT) / str(date_obj.year) / str(date_obj.month) / str(date_obj.day)
                for hh in range(hour_from, hour_to + 1):
                    ref_dt = datetime(date_obj.year, date_obj.month, date_obj.day,
                                    hh, 0, 0, tzinfo=TZ_PRAGUE)
                    folder_h = folder_hour_from_prague_hour(hh, ref_dt)
                    hour_dir = base / str(folder_h)
                    if hour_dir.exists() and hour_dir.is_dir():
                        try:
                            subs = sorted(
                                [p.name for p in hour_dir.iterdir() if p.is_dir()],
                                key=str.lower)
                            for name in subs:
                                m = re.match(r"^C\d{2}-(\d{2,3})-", name)
                                num = m.group(1) if m else ""
                                if not any(n == name for _, n in cameras):
                                    cameras.append((num, name))
                        except Exception:
                            continue
            except Exception:
                pass
            signals.finished.emit(cameras)

        _thr.Thread(target=worker, daemon=True).start()

    def _on_cameras_preloaded(self, cameras: list):
        self._preloaded = cameras

    def _reapply_weekend_format(self):
        wf_weekend = QTextCharFormat()
        wf_weekend.setForeground(QColor("#cc0000"))
        for day in [Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday]:
            self.cal.setWeekdayTextFormat(day, wf_weekend)

    def _on_multiday_toggled(self, state: int):
        multi = bool(state)
        self.cal_to.setVisible(multi)
        self._lbl_cal_to.setVisible(multi)
        if multi:
            # Fix hours to 7–21 lab-time when multi-day is on
            self.hour_from.setValue(7)
            self.hour_to.setValue(21)
            self.cb_now.setChecked(False)
            self.cb_now.setEnabled(False)
        else:
            self.cb_now.setEnabled(True)
        self.adjustSize()

    def is_multiday(self) -> bool:
        return self.cb_multiday.isChecked()

    def selected_date_range(self) -> "list[datetime.date]":
        """Return list of date objects from start to end (inclusive), when multiday."""
        from datetime import date as _date, timedelta as _td
        d1 = self.cal.selectedDate()
        d2 = self.cal_to.selectedDate()
        start = _date(d1.year(), d1.month(), d1.day())
        end   = _date(d2.year(), d2.month(), d2.day())
        if end < start:
            end = start
        days = []
        cur = start
        while cur <= end:
            days.append(cur)
            cur += _td(days=1)
        return days

    def _on_accept(self):
        if self.hour_from.value() > self.hour_to.value():
            QMessageBox.warning(self, "Chyba", '"Od" nesmí být větší než "Do".'); return
        if self.is_multiday():
            days = self.selected_date_range()
            if not days:
                QMessageBox.warning(self, "Chyba", "Vyber alespoň jeden den."); return
            if len(days) > 14:
                r = QMessageBox.question(self, "Multi-day",
                    f"Vybrán rozsah {len(days)} dní. Pokračovat?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r != QMessageBox.StandardButton.Yes:
                    return
        self.accept()

    def _go_to_now(self):
        now_dt = datetime.now(TZ_PRAGUE)
        self.cal.setSelectedDate(QDate(now_dt.year, now_dt.month, now_dt.day))
        self.hour_from.setValue(now_dt.hour)
        self.hour_to.setValue(now_dt.hour)
        self.cb_now.setChecked(True)

    def _on_now_changed(self, state: int):
        now_dt = datetime.now(TZ_PRAGUE)
        if state:
            self.hour_to.setValue(now_dt.hour)
            self.hour_to.setEnabled(False)
        else:
            self.hour_to.setEnabled(True)

    def is_online_mode(self) -> bool:
        return self.cb_now.isChecked()

    def _on_year_month_changed(self):
        year = int(self.year_sb.value()); month = int(self.month_cb.currentData())
        current = self.cal.selectedDate()
        day = min(current.day(), QDate(year, month, 1).daysInMonth())
        self.cal.setSelectedDate(QDate(year, month, day))

    def _sync_controls_from_calendar(self):
        d = self.cal.selectedDate()
        self.year_sb.blockSignals(True); self.month_cb.blockSignals(True)
        self.year_sb.setValue(d.year()); self.month_cb.setCurrentIndex(d.month() - 1)
        self.year_sb.blockSignals(False); self.month_cb.blockSignals(False)

    def _on_hours_changed(self):
        if self.hour_from.value() > self.hour_to.value():
            sender = self.sender()
            if sender is self.hour_from:
                self.hour_to.blockSignals(True); self.hour_to.setValue(self.hour_from.value()); self.hour_to.blockSignals(False)
            else:
                self.hour_from.blockSignals(True); self.hour_from.setValue(self.hour_to.value()); self.hour_from.blockSignals(False)

    def selected_hours(self) -> tuple[int, int]:
        return int(self.hour_from.value()), int(self.hour_to.value())

    def selected_date_obj(self):
        d = self.cal.selectedDate()
        return datetime(d.year(), d.month(), d.day(), tzinfo=TZ_PRAGUE).date()

    def selected_folders(self) -> list[Path]:
        d = self.cal.selectedDate()
        y, m, day = d.year(), d.month(), d.day()
        h0, h1 = self.selected_hours()
        out = []
        for hh in range(h0, h1 + 1):
            ref_dt = datetime(y, m, day, hh, 0, 0, tzinfo=TZ_PRAGUE)
            folder_hh = folder_hour_from_prague_hour(hh, ref_dt)
            out.append(Path(DEFAULT_OPEN_ROOT) / str(y) / str(m) / str(day) / str(folder_hh))
        return out

    def selected_axis(self) -> tuple[int, int]:
        if self.is_multiday():
            days = self.selected_date_range()
            h0, h1 = self.selected_hours()
            d_start = days[0];  d_end = days[-1]
            start = datetime(d_start.year, d_start.month, d_start.day,
                             h0, 0, 0, tzinfo=TZ_PRAGUE)
            end   = datetime(d_end.year,   d_end.month,   d_end.day,
                             h1 + 1, 0, 0, tzinfo=TZ_PRAGUE)
        else:
            d = self.cal.selectedDate()
            y, m, day = d.year(), d.month(), d.day()
            h0, h1 = self.selected_hours()
            start = datetime(y, m, day, h0, 0, 0, tzinfo=TZ_PRAGUE)
            end   = datetime(y, m, day, h1 + 1, 0, 0, tzinfo=TZ_PRAGUE)
        return ns_from_dt(start), ns_from_dt(end)

# ---------------- CAMERA PICKER DIALOG ----------------
# ---------------- CAMERA PICKER DIALOG ----------------
class _CamLoaderSignals(QObject):
    finished = Signal(list)

class CameraPickerDialog(QDialog):
    def __init__(self, date_obj, hour_from: int, hour_to: int,
                 last_cam_names: list[str], parent=None,
                 preloaded_cameras: list | None = None):
        super().__init__(parent)
        self.setWindowTitle("Select cameras")
        self.resize(420, 640)

        self._all_cam_data: list[tuple[str, str]] = []
        # Persistent selection — nezávislá na filtru
        self._selected_names: list[str] = list(last_cam_names) if last_cam_names else []
        self._date_obj = date_obj
        self._hour_from = hour_from
        self._hour_to = hour_to

        lay = QVBoxLayout(self)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search cameras…")
        self._search_edit.textChanged.connect(self._filter_cameras)
        lay.addWidget(self._search_edit)

        self._cam_list = QTableWidget(0, 2)
        self._cam_list.setHorizontalHeaderLabels(["#", "Camera"])
        self._cam_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._cam_list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._cam_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cam_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._cam_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cam_list.verticalHeader().setVisible(False)
        self._cam_list.cellClicked.connect(self._on_cam_clicked)
        lay.addWidget(self._cam_list, 1)

        self._status_lbl = QLabel("Loading cameras…")
        self._status_lbl.setStyleSheet("font-size: 10px; color: #555;")
        lay.addWidget(self._status_lbl)

        # Tabulka vybraných kamer (max 7 vybraných, z nichž se zobrazí první 4)
        sel_lbl = QLabel("Selected (first 4 shown, max 7):")
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
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self._signals = _CamLoaderSignals()
        self._signals.finished.connect(self._on_cameras_loaded)
        if preloaded_cameras is not None:
            QTimer.singleShot(0, lambda: self._on_cameras_loaded(preloaded_cameras))
        else:
            self._signals = _CamLoaderSignals()
            self._signals.finished.connect(self._on_cameras_loaded)
            self._load_cameras_async()

        self._refresh_sel_table()

    def _load_cameras_async(self):
        import threading as _thr
        date_obj  = self._date_obj
        hour_from = self._hour_from
        hour_to   = self._hour_to
        signals   = self._signals

        def worker():
            cameras: list[tuple[str, str]] = []
            try:
                base = Path(DEFAULT_OPEN_ROOT) / str(date_obj.year) / str(date_obj.month) / str(date_obj.day)
                for hh in range(hour_from, hour_to + 1):
                    ref_dt = datetime(date_obj.year, date_obj.month, date_obj.day,
                                      hh, 0, 0, tzinfo=TZ_PRAGUE)
                    folder_h = folder_hour_from_prague_hour(hh, ref_dt)
                    hour_dir = base / str(folder_h)
                    if hour_dir.exists() and hour_dir.is_dir():
                        try:
                            subs = sorted(
                                [p.name for p in hour_dir.iterdir() if p.is_dir()],
                                key=str.lower)
                            for name in subs:
                                m = re.match(r"^C\d{2}-(\d{2,3})-", name)
                                num = m.group(1) if m else ""
                                if not any(n == name for _, n in cameras):
                                    cameras.append((num, name))
                        except Exception:
                            continue
            except Exception:
                pass
            signals.finished.emit(cameras)

        _thr.Thread(target=worker, daemon=True).start()

    def _on_cameras_loaded(self, cameras: list):
        self._all_cam_data = cameras
        self._populate_cam_table(cameras)
        n = len(cameras)
        self._status_lbl.setText(f"{n} cameras available." if n else "No cameras found.")
        self._highlight_selected()

    def _populate_cam_table(self, cameras: list):
        self._cam_list.setRowCount(0)
        for num, name in cameras:
            r = self._cam_list.rowCount()
            self._cam_list.insertRow(r)
            self._cam_list.setItem(r, 0, QTableWidgetItem(num))
            self._cam_list.setItem(r, 1, QTableWidgetItem(name))
        self._highlight_selected()

    def _highlight_selected(self):
        """Zvýrazní vybrané kamery v hlavní tabulce."""
        for r in range(self._cam_list.rowCount()):
            item = self._cam_list.item(r, 1)
            if item is None:
                continue
            selected = item.text() in self._selected_names
            bg = QColor("#d0e8ff") if selected else QColor("#ffffff")
            for c in range(self._cam_list.columnCount()):
                cell = self._cam_list.item(r, c)
                if cell:
                    cell.setBackground(bg)

    def _on_cam_clicked(self, row: int, col: int):
        item = self._cam_list.item(row, 1)
        if item is None:
            return
        name = item.text()
        if name in self._selected_names:
            self._selected_names.remove(name)
        else:
            if len(self._selected_names) >= 7:
                QMessageBox.warning(self, "Too many cameras",
                                    "You can select at most 7 cameras (first 4 will be shown).")
                return
            self._selected_names.append(name)
        self._highlight_selected()
        self._refresh_sel_table()
        self._cam_list.clearSelection()

    def _refresh_sel_table(self):
        self._sel_table.setRowCount(0)
        for i, name in enumerate(self._selected_names):
            r = self._sel_table.rowCount()
            self._sel_table.insertRow(r)
            item = QTableWidgetItem(name)
            if i >= 4:
                # Extra cameras beyond first 4 — shown but won't be loaded
                item.setForeground(QColor("#999"))
                item.setToolTip("Will not be shown (only first 4 cameras are loaded)")
            self._sel_table.setItem(r, 0, item)
            btn = QPushButton("✕")
            btn.setFixedSize(24, 24)
            btn.setStyleSheet("font-size: 10px; padding: 0;")
            btn.clicked.connect(lambda checked, n=name: self._remove_selected(n))
            self._sel_table.setCellWidget(r, 1, btn)

    def _remove_selected(self, name: str):
        if name in self._selected_names:
            self._selected_names.remove(name)
        self._refresh_sel_table()
        self._highlight_selected()

    def _filter_cameras(self, text: str):
        q = text.strip().lower()
        filtered = [(num, name) for num, name in self._all_cam_data
                    if not q or q in name.lower() or q in num.lower()]
        self._populate_cam_table(filtered)

    def _on_accept(self):
        if not self._selected_names:
            QMessageBox.warning(self, "No camera", "Please select at least one camera.")
            return
        self.accept()

    def selected_camera_names(self) -> list[str]:
        # Return up to 4 for display; user can pre-select up to 7 and deselect back to 4
        return list(self._selected_names)[:4]

    def all_selected_camera_names(self) -> list[str]:
        return list(self._selected_names)

# ---------------- COMBOBOX ----------------
class PopupBelowComboBox(QComboBox):
    def showPopup(self):
        super().showPopup()
        view = self.view(); popup = view.window()
        if popup is None: return
        gpos = self.mapToGlobal(self.rect().bottomLeft())
        x, y = gpos.x(), gpos.y(); pw, ph = popup.width(), popup.height()
        screen = QGuiApplication.screenAt(self.mapToGlobal(self.rect().center())) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        if x + pw > geo.right(): x = max(geo.left(), geo.right() - pw)
        if x < geo.left(): x = geo.left()
        if y + ph > geo.bottom(): y = max(geo.top(), geo.bottom() - ph)
        popup.move(x, y)

    def wheelEvent(self, event):
        event.ignore()  # Ignoruj scroll kolečkem


# ---------------- CIRCLE FIT ----------------
def _fit_circle_kasa(points):
    n = len(points)
    if n < 30: return None
    sx = sy = sxx = syy = sxy = sxz = syz = sz = 0.0
    for x, y in points:
        xx, yy = x*x, y*y; z = xx+yy
        sx+=x; sy+=y; sxx+=xx; syy+=yy; sxy+=x*y; sz+=z; sxz+=x*z; syz+=y*z
    det = sxx*(syy*n-sy*sy) - sxy*(sxy*n-sy*sx) + sx*(sxy*sy-syy*sx)
    if abs(det) < 1e-9: return None
    def d3(a,b,c,d,e,f,g,h,i): return a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)
    a = d3(sxz,sxy,sx,syz,syy,sy,sz,sy,n)/det
    b = d3(sxx,sxz,sx,sxy,syz,sy,sx,sz,n)/det
    c = d3(sxx,sxy,sxz,sxy,syy,syz,sx,sy,sz)/det
    cx, cy = a/2.0, b/2.0
    r2 = c + (a*a+b*b)/4.0
    if r2 <= 2.0: return None
    return cx, cy, r2**0.5


# ---------------- IMAGE VIEW ----------------
class ImageView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pix: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self.bg_color: QColor = QColor("#f3f3f3")  # overrideable per-instance

        self.show_cross  = False
        self.cross_size  = 18
        self.cross_thickness = 2
        self.cross_pos_norm: QPointF | None = None
        self._draw_mode: str = ""
        self.energy_text: str = ""
        self.timestamp_text: str = ""   # shown as large white overlay in top-left
        self.cam_label_text: str = ""   # camera name label below image
        self.cam_ts_text: str = ""      # timestamp label below image
        self.cam_label_font_px: int = 12  # controlled by Label size spinbox

        self.show_circle = False
        self.circle_center_norm: QPointF | None = None
        self.circle_r_norm: float | None = None
        self.circle_rx_norm: float | None = None  # normalizováno přes šířku obrazu
        self.circle_ry_norm: float | None = None  # normalizováno přes výšku obrazu
        # draw state
        self._drag_start: QPointF | None = None
        self._drag_handle: str = ""   # "" | "move" | "n" | "s" | "e" | "w" | "nw" | "ne" | "sw" | "se"

        self.cross_color     = QColor(0, 255, 0, 220)
        self.circle_color    = QColor(255, 255, 0, 230)
        self.circle_thick    = 2
        self.square_color    = QColor(0, 200, 255, 230)
        self.square_thick    = 2

        self.show_square = False
        # stored as (left_norm, top_norm, right_norm, bottom_norm) — all in [0,1]
        self.square_rect_norm: tuple[float, float, float, float] | None = None

        # Top-N SC pixel markers: list of (nx, ny) normalized coords, or None
        self.sc_topn_points_norm: "list[tuple[float,float]] | None" = None

        # When True (default): labels are drawn as overlay inside the image.
        # When False: bottom space is reserved and labels drawn below the image.
        self.cam_label_use_overlay: bool = True

        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_pixmap(self, pm: QPixmap):
        self._pix = pm; self._scaled = None; self.update()

    def clear(self):
        self._pix = None; self._scaled = None; self.update()

    def _label_bar_h(self) -> int:
        """Height in pixels reserved for the label bar below the image (0 when overlay mode)."""
        if self.cam_label_use_overlay:
            return 0
        return max(8, self.cam_label_font_px) + 10

    def _ensure_scaled(self):
        if self._pix is None or self._pix.isNull():
            self._scaled = None; return
        lbh = self._label_bar_h()
        avail_h = max(10, self.height() - lbh)
        if self.width() <= 10 or avail_h <= 10:
            self._scaled = None; return
        target = QSize(self.width(), avail_h)
        if self._scaled is None or self._scaled.size() != target:
            self._scaled = self._pix.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

    def resizeEvent(self, event):
        super().resizeEvent(event); self._scaled = None; self.update()

    def set_draw_mode(self, mode: str):
        self._draw_mode = mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode else Qt.CursorShape.ArrowCursor)
        self.update()

    def _img_rect(self) -> QRect | None:
        self._ensure_scaled()
        if self._scaled is None or self._scaled.isNull():
            return None
        lbh = self._label_bar_h()
        avail_h = self.height() - lbh
        x0 = (self.width()  - self._scaled.width())  // 2
        y0 = (avail_h - self._scaled.height()) // 2
        return QRect(x0, y0, self._scaled.width(), self._scaled.height())

    def _handle_radius(self) -> int:
        return 7

    def _circle_handles(self, ir: QRect) -> dict:
        """Vrátí handlery pro ellipsu: střed + 4 okraje."""
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
        """Vrátí handlery pro obdélník: střed + 4 rohy + 4 hrany."""
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
        r = self._handle_radius() + 3
        for name, pt in handles.items():
            if abs(pos.x() - pt.x()) <= r and abs(pos.y() - pt.y()) <= r:
                return name
        return ""

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event); return
        ir = self._img_rect()
        if ir is None or ir.width() <= 0 or ir.height() <= 0:
            super().mousePressEvent(event); return
        pos = event.position()

        if self._draw_mode == "cross":
            self.cross_pos_norm = QPointF(
                max(0.0, min(1.0, (pos.x() - ir.left()) / ir.width())),
                max(0.0, min(1.0, (pos.y() - ir.top())  / ir.height()))
            )
            self.update(); return

        if self._draw_mode == "circle" and self.circle_center_norm is not None \
                and self.circle_rx_norm is not None:
            hit = self._hit_handle(pos, self._circle_handles(ir))
            if hit:
                self._drag_handle = hit
                self._drag_start = pos
                return
            # klik mimo handlery = začít kreslit nový
            self._drag_handle = "new"
            self._drag_start = pos
            return

        if self._draw_mode == "circle":
            self._drag_handle = "new"
            self._drag_start = pos
            return

        if self._draw_mode == "square" and self.square_rect_norm is not None:
            hit = self._hit_handle(pos, self._square_handles(ir))
            if hit:
                self._drag_handle = hit
                self._drag_start = pos
                return
            self._drag_handle = "new"
            self._drag_start = pos
            return

        if self._draw_mode == "square":
            self._drag_handle = "new"
            self._drag_start = pos
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        ir = self._img_rect()
        if ir is None or ir.width() <= 0 or ir.height() <= 0: return
        pos = event.position()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # kurzor podle handleru
        if self._draw_mode in ("circle", "square") and not self._drag_handle:
            handles = self._circle_handles(ir) if self._draw_mode == "circle" else self._square_handles(ir)
            hit = self._hit_handle(pos, handles)
            if hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif hit in ("n", "s"):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif hit in ("e", "w"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit in ("nw", "se"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in ("ne", "sw"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

        if not (event.buttons() & Qt.MouseButton.LeftButton): return
        if self._drag_start is None: return

        def clamp_norm(v): return max(0.0, min(1.0, v))
        def to_norm(px, py):
            return clamp_norm((px - ir.left()) / ir.width()), clamp_norm((py - ir.top()) / ir.height())

        # ── CIRCLE ──────────────────────────────────────────────────────
        if self._draw_mode == "circle":
            if self._drag_handle == "new":
                # tažení = střed je start, poloměr = vzdálenost
                x0, y0 = self._drag_start.x(), self._drag_start.y()
                dx = (pos.x() - x0) / ir.width()
                dy = (pos.y() - y0) / ir.height()
                if shift:
                    r = max(abs(dx), abs(dy))
                    rx_n, ry_n = r, r
                else:
                    rx_n, ry_n = abs(dx), abs(dy)
                self.circle_center_norm = QPointF(*to_norm(x0, y0))
                self.circle_rx_norm = rx_n
                self.circle_ry_norm = ry_n
                self.circle_r_norm  = max(rx_n, ry_n)

            elif self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                cx = clamp_norm(self.circle_center_norm.x() + dx)
                cy = clamp_norm(self.circle_center_norm.y() + dy)
                self.circle_center_norm = QPointF(cx, cy)

            elif self._drag_handle in ("e", "w"):
                cx_px = ir.left() + self.circle_center_norm.x() * ir.width()
                rx_n = abs(pos.x() - cx_px) / ir.width()
                if shift: self.circle_ry_norm = rx_n
                self.circle_rx_norm = rx_n
                self.circle_r_norm  = max(self.circle_rx_norm, self.circle_ry_norm)

            elif self._drag_handle in ("n", "s"):
                cy_px = ir.top() + self.circle_center_norm.y() * ir.height()
                ry_n = abs(pos.y() - cy_px) / ir.height()
                if shift: self.circle_rx_norm = ry_n
                self.circle_ry_norm = ry_n
                self.circle_r_norm  = max(self.circle_rx_norm, self.circle_ry_norm)

            self.update()

        # ── SQUARE ──────────────────────────────────────────────────────
        elif self._draw_mode == "square":
            if self._drag_handle == "new":
                # Střed je drag_start, roztahuje se symetricky na obě strany
                cx0, cy0 = to_norm(self._drag_start.x(), self._drag_start.y())
                nx1, ny1 = to_norm(pos.x(), pos.y())
                dx = abs(nx1 - cx0)
                dy = abs(ny1 - cy0)
                if shift:
                    side = max(dx, dy)
                    dx, dy = side, side
                self.square_rect_norm = (
                    max(0.0, cx0 - dx), max(0.0, cy0 - dy),
                    min(1.0, cx0 + dx), min(1.0, cy0 + dy)
                )

            elif self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                ln, tn, rn, bn = self.square_rect_norm
                w_ = rn - ln; h_ = bn - tn
                ln = clamp_norm(ln + dx); tn = clamp_norm(tn + dy)
                self.square_rect_norm = (ln, tn,
                    clamp_norm(ln + w_), clamp_norm(tn + h_))

            else:
                ln, tn, rn, bn = self.square_rect_norm
                h = self._drag_handle
                nx, ny = to_norm(pos.x(), pos.y())
                if "w" in h: ln = min(nx, rn - 0.01)
                if "e" in h: rn = max(nx, ln + 0.01)
                if "n" in h: tn = min(ny, bn - 0.01)
                if "s" in h: bn = max(ny, tn + 0.01)
                if shift:
                    # uniform scale od protějšího rohu
                    if h in ("se",): side = max(rn - ln, bn - tn); rn = ln + side; bn = tn + side
                    elif h in ("nw",): side = max(rn - ln, bn - tn); ln = rn - side; tn = bn - side
                    elif h in ("ne",): side = max(rn - ln, bn - tn); rn = ln + side; tn = bn - side
                    elif h in ("sw",): side = max(rn - ln, bn - tn); ln = rn - side; bn = tn + side
                self.square_rect_norm = (
                    clamp_norm(ln), clamp_norm(tn),
                    clamp_norm(rn), clamp_norm(bn)
                )

            self.update()

    def mouseReleaseEvent(self, event):
        self._drag_handle = ""
        self._drag_start = None
        super().mouseReleaseEvent(event)  

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg_color)
        if self._pix is None or self._pix.isNull():
            p.end(); return
        self._ensure_scaled()
        if self._scaled is None or self._scaled.isNull():
            p.end(); return

        pm = self._scaled
        lbh = self._label_bar_h()
        avail_h = self.height() - lbh
        x0 = (self.width()  - pm.width())  // 2
        y0 = (avail_h - pm.height()) // 2
        p.drawPixmap(x0, y0, pm)
        img_rect = QRect(x0, y0, pm.width(), pm.height())
        # Rámeček kolem obrázku
        border_pen = QPen(QColor(80, 80, 80, 160))
        border_pen.setWidth(1)
        p.setPen(border_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(img_rect)

        if self.show_cross:
            if self.cross_pos_norm is not None:
                cx = img_rect.left() + int(self.cross_pos_norm.x() * img_rect.width())
                cy = img_rect.top()  + int(self.cross_pos_norm.y() * img_rect.height())
            else:
                cx = img_rect.center().x(); cy = img_rect.center().y()
            pen = QPen(self.cross_color); pen.setWidth(self.cross_thickness); p.setPen(pen)
            p.drawLine(cx - self.cross_size, cy, cx + self.cross_size, cy)
            p.drawLine(cx, cy - self.cross_size, cx, cy + self.cross_size)

        if self.show_circle and self.circle_center_norm is not None:
            nx, ny = self.circle_center_norm.x(), self.circle_center_norm.y()
            cx = img_rect.left() + int(nx * img_rect.width())
            cy = img_rect.top()  + int(ny * img_rect.height())
            # použij rx/ry pokud jsou k dispozici (přesné), jinak fallback na r
            if self.circle_rx_norm is not None and self.circle_ry_norm is not None:
                rx = int(self.circle_rx_norm * img_rect.width())
                ry = int(self.circle_ry_norm * img_rect.height())
            else:
                rx = ry = int(self.circle_r_norm * min(img_rect.width(), img_rect.height()))
            pen = QPen(self.circle_color); pen.setWidth(self.circle_thick); p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
            if self._draw_mode == "circle":
                for pt in self._circle_handles(img_rect).values():
                    p.setPen(QPen(self.circle_color))
                    p.setBrush(QColor(self.circle_color.red(), self.circle_color.green(),
                                      self.circle_color.blue(), 120))
                    p.drawEllipse(int(pt.x()) - self._handle_radius(),
                                  int(pt.y()) - self._handle_radius(),
                                  self._handle_radius()*2, self._handle_radius()*2)

        if self.show_square and self.square_rect_norm is not None:
            # square_rect_norm = (left_norm, top_norm, right_norm, bottom_norm)
            ln, tn, rn, bn = self.square_rect_norm
            sx = img_rect.left() + int(ln * img_rect.width())
            sy = img_rect.top()  + int(tn * img_rect.height())
            sw = int((rn - ln) * img_rect.width())
            sh = int((bn - tn) * img_rect.height())
            pen = QPen(self.square_color); pen.setWidth(self.square_thick); p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(sx, sy, sw, sh)
            if self._draw_mode == "square":
                for pt in self._square_handles(img_rect).values():
                    p.setPen(QPen(self.square_color))
                    p.setBrush(QColor(self.square_color.red(), self.square_color.green(),
                                      self.square_color.blue(), 120))
                    p.drawEllipse(int(pt.x()) - self._handle_radius(),
                                  int(pt.y()) - self._handle_radius(),
                                  self._handle_radius()*2, self._handle_radius()*2)
        if self.energy_text and not self._pix.isNull():
            from PySide6.QtGui import QFont, QFontMetrics
            available_w = img_rect.width() - 20
            font = QFont()
            display_text = self.energy_text

            # Zkus vejít na jeden řádek
            fitted = False
            for fsize in range(22, 8, -1):
                font.setPixelSize(fsize)
                fm = QFontMetrics(font)
                if fm.horizontalAdvance(self.energy_text) <= available_w:
                    fitted = True
                    break

            if not fitted:
                # Rozděl na dva řádky podle " | "
                parts_split = self.energy_text.split("  |  ")
                mid = len(parts_split) // 2
                display_text = "  |  ".join(parts_split[:mid]) + "\n" + "  |  ".join(parts_split[mid:])
                font.setPixelSize(12)
                for fsize in range(18, 8, -1):
                    font.setPixelSize(fsize)
                    fm = QFontMetrics(font)
                    max_line = max(fm.horizontalAdvance(l) for l in display_text.split("\n"))
                    if max_line <= available_w:
                        break

            fm = QFontMetrics(font)
            line_count = display_text.count("\n") + 1
            bar_h = max(28, fm.height() * line_count + 12)
            bar_rect = QRect(img_rect.left(), img_rect.bottom() - bar_h,
                             img_rect.width(), bar_h)
            p.fillRect(bar_rect, QColor(255, 255, 255, 220))
            p.setFont(font)
            p.setPen(QColor(0, 0, 0))
            p.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter, display_text)

        # Camera name + timestamp labels.
        # overlay mode (multi-cam): drawn semi-transparent over the bottom of the image.
        # non-overlay mode (single-cam): drawn in the reserved strip below the image,
        #   spanning exactly the image width (x0 … x0+img_w).
        if (self.cam_label_text or self.cam_ts_text) and not self._pix.isNull():
            from PySide6.QtGui import QFont as _QFont
            _fpx = max(8, self.cam_label_font_px)
            lbl_h = _fpx + 10
            lbl_x = img_rect.left()
            lbl_w = img_rect.width()
            if self.cam_label_use_overlay:
                # Semi-transparent strip at the very bottom of the image rect
                lbl_y = img_rect.bottom() - lbl_h
                lbl_y = max(img_rect.top(), lbl_y)
            else:
                # Reserved strip directly below the image, same x/width as image
                lbl_y = img_rect.bottom() + 1
                # Clamp so it never goes below widget bottom
                lbl_y = min(lbl_y, self.height() - lbl_h)
                lbl_y = max(img_rect.bottom() + 1, lbl_y)
            name_w = lbl_w // 3
            ts_w = lbl_w - name_w
            font = _QFont(); font.setPixelSize(_fpx)
            p.setFont(font)
            if self.cam_label_text:
                name_rect = QRect(lbl_x, lbl_y, name_w, lbl_h)
                p.fillRect(name_rect, QColor(0x44, 0x44, 0x44, 220 if self.cam_label_use_overlay else 255))
                p.setPen(QColor(0xee, 0xee, 0xee))
                p.drawText(name_rect.adjusted(4, 0, -4, 0),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           self.cam_label_text)
            if self.cam_ts_text:
                ts_rect = QRect(lbl_x + name_w, lbl_y, ts_w, lbl_h)
                p.fillRect(ts_rect, QColor(0x33, 0x33, 0x33, 220 if self.cam_label_use_overlay else 255))
                p.setPen(QColor(0xff, 0xd5, 0x4f))
                ts_font = _QFont(); ts_font.setPixelSize(max(8, _fpx - 1))
                p.setFont(ts_font)
                p.drawText(ts_rect.adjusted(4, 0, -4, 0),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           self.cam_ts_text)

        # Spatial contrast top-N pixel markers
        if getattr(self, 'sc_topn_points_norm', None) and not self._pix.isNull():
            r = getattr(self, 'sc_topn_marker_radius', max(3, img_rect.width() // 150))
            thick = getattr(self, 'sc_topn_marker_thick', 2)
            p.setPen(QPen(QColor(255, 80, 0, 230), thick))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for nx, ny in self.sc_topn_points_norm:
                cx = img_rect.left() + int(nx * img_rect.width())
                cy = img_rect.top()  + int(ny * img_rect.height())
                p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        p.end()

    # ------------------------------------------------------------------ circle
    def calibrate_circle_from_pixmap(self) -> bool:
        if self._calibrate_circle_hard_impl():
            return True
        return self._calibrate_circle_soft()

    def _calibrate_circle_hard_impl(self) -> bool:
        if self._pix is None or self._pix.isNull(): return False
        pm = self._pix; w0, h0 = pm.width(), pm.height()    
        if w0 <= 0 or h0 <= 0: return False

        target = 700; scale = max(w0, h0) / target
        small = pm.toImage().scaled(int(w0/scale), int(h0/scale),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ) if scale > 1.0 else pm.toImage()

        g = small.convertToFormat(QImage.Format.Format_Grayscale8)
        g = _autostretch_gray(g, 0.2, 99.8)
        w, h = g.width(), g.height()
        if w < 100 or h < 100: return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"): ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy()
        
        # Centroid celého obrazu (bez ořezu) — robustnější pro velké kruhy
        x1 = int(w * CIRCLE_SEARCH_REGION); x2 = int(w * (1 - CIRCLE_SEARCH_REGION))
        y1 = int(h * CIRCLE_SEARCH_REGION); y2 = int(h * (1 - CIRCLE_SEARCH_REGION))

        region = arr[y1:y2, x1:x2]
        # Použij střední jas (50..95 percentil) jako váhy — ignoruje tmavé pozadí i přesycené skvrny
        lo = np.percentile(region, 30)
        hi = np.percentile(region, CIRCLE_BRIGHT_PERCENTILE * 100)
        clipped = np.clip(region.astype(float), lo, hi) - lo
        total_w = clipped.sum()
        if total_w < 1.0:
            cx0, cy0 = w * 0.5, h * 0.5
        else:
            ys_g, xs_g = np.mgrid[y1:y2, x1:x2]
            cx0 = float((clipped * xs_g).sum() / total_w)
            cy0 = float((clipped * ys_g).sum() / total_w)

        r_min = int(min(w, h) * CIRCLE_R_MIN_FRAC)
        r_max = int(min(w, h) * CIRCLE_R_MAX_FRAC)
        if r_max <= r_min + 10: return False

        def sample(px, py):
            ix, iy = int(round(px)), int(round(py))
            if ix < 0 or iy < 0 or ix >= w or iy >= h: return 0
            return int(arr[iy, ix])

        pts = []
        for deg in range(0, 360, 2):
            ang = math.radians(deg); ca, sa = math.cos(ang), math.sin(ang)
            prof, rr_list = [], []
            for rr in range(r_min, r_max + 1):
                x = cx0 + rr*ca; y = cy0 + rr*sa
                if x < 1 or y < 1 or x >= w-1 or y >= h-1: break
                prof.append(sample(x, y)); rr_list.append(rr)
            if len(prof) < 20: continue
            sm = [sum(prof[max(0,i-2):min(len(prof),i+3)]) / len(prof[max(0,i-2):min(len(prof),i+3)]) for i in range(len(prof))]
            best_i, best_drop = -1, 0.0
            for i in range(2, len(sm) - 3):
                inside  = (sm[i-2]+sm[i-1]+sm[i])/3.0
                outside = (sm[i+1]+sm[i+2]+sm[i+3])/3.0
                drop = inside - outside
                if drop > best_drop and inside > 80: best_drop = drop; best_i = i
            if best_i < 0 or best_drop < CIRCLE_MIN_DROP: continue
            rr = rr_list[best_i]; pts.append((float(cx0+rr*ca), float(cy0+rr*sa)))

        if len(pts) < CIRCLE_MIN_POINTS: return False
        fit = _fit_circle_kasa(pts)
        if fit is None: return False
        cx, cy, r = fit

        pts2 = []; band = max(8, int(r * 0.08))
        for deg in range(0, 360, 2):
            ang = math.radians(deg); ca, sa = math.cos(ang), math.sin(ang)
            best_rr, best_drop = None, 0.0
            for rr in range(max(5, int(r - band)), int(r + band) + 1):
                x = cx+rr*ca; y = cy+rr*sa
                if x < 2 or y < 2 or x >= w-2 or y >= h-2: continue
                inside  = (sample(cx+(rr-2)*ca,cy+(rr-2)*sa)+sample(cx+(rr-1)*ca,cy+(rr-1)*sa)+sample(cx+rr*ca,cy+rr*sa))/3.0
                outside = (sample(cx+rr*ca,cy+rr*sa)+sample(cx+(rr+1)*ca,cy+(rr+1)*sa)+sample(cx+(rr+2)*ca,cy+(rr+2)*sa))/3.0
                drop = inside - outside
                if drop > best_drop and inside > 80: best_drop = drop; best_rr = rr
            if best_rr is not None and best_drop >= CIRCLE_REFINE_DROP:
                pts2.append((float(cx+best_rr*ca), float(cy+best_rr*sa)))

        if len(pts2) >= CIRCLE_MIN_POINTS:
            fit2 = _fit_circle_kasa(pts2)
            if fit2 is not None: cx, cy, r = fit2

        if r < min(w,h)*CIRCLE_R_MIN_FRAC or r > min(w,h)*CIRCLE_R_MAX_FRAC: return False
        r *= 1.04  # soft edge kompenzace — hrana je měkká, fit leží těsně uvnitř
        self.circle_center_norm = QPointF(cx / w, cy / h)
        self.circle_r_norm  = r / min(w, h)
        self.circle_rx_norm = r / w
        self.circle_ry_norm = r / h
        return True

    def _calibrate_circle_hard(self) -> bool:
        return self._calibrate_circle_hard_impl()

    def _calibrate_circle_soft(self) -> bool:   
        """
        Alternativní kalibrace kruhu pro kamery s měkkým přechodem.
        Hledá poloměr kde průměrný jas klesne na CIRCLE_SOFT_PERCENTILE * maximum.
        """
        if self._pix is None or self._pix.isNull(): return False
        pm = self._pix; w0, h0 = pm.width(), pm.height()
        if w0 <= 0 or h0 <= 0: return False

        target = 700; scale = max(w0, h0) / target
        small = pm.toImage().scaled(int(w0/scale), int(h0/scale),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ) if scale > 1.0 else pm.toImage()

        g = small.convertToFormat(QImage.Format.Format_Grayscale8)
        g = _autostretch_gray(g, 0.2, 99.8)
        w, h = g.width(), g.height()
        if w < 100 or h < 100: return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"): ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy()

        # Těžiště jasu jako odhad středu
        arr_f = arr.astype(np.float32)
        total = arr_f.sum()
        if total < 1.0: return False
        ys, xs = np.mgrid[0:h, 0:w]
        cx0 = float((arr_f * xs).sum() / total)
        cy0 = float((arr_f * ys).sum() / total)

        # Radiální profil — průměrný jas v každém poloměru
        r_max = int(min(w, h) * CIRCLE_SOFT_MAX_R_FRAC)
        r_min = int(min(w, h) * CIRCLE_SOFT_MIN_R_FRAC)
        if r_max <= r_min + 10: return False

        # Vzorkuj radiální profil (průměr přes 360 úhlů)
        n_angles = 180
        angles = np.linspace(0, 2 * np.pi, n_angles, endpoint=False)
        cos_a = np.cos(angles); sin_a = np.sin(angles)

        profile = np.zeros(r_max + 1, dtype=np.float32)
        counts  = np.zeros(r_max + 1, dtype=np.int32)
        for rr in range(r_min, r_max + 1):
            xs_r = (cx0 + rr * cos_a).astype(int)
            ys_r = (cy0 + rr * sin_a).astype(int)
            mask = (xs_r >= 0) & (xs_r < w) & (ys_r >= 0) & (ys_r < h)
            if mask.sum() < 10: continue
            profile[rr] = arr[ys_r[mask], xs_r[mask]].mean()
            counts[rr] = 1

        valid = np.where(counts[r_min:r_max+1] > 0)[0] + r_min
        if len(valid) < 10: return False

        prof_valid = profile[valid]
        peak = prof_valid.max()
        if peak < 10: return False

        threshold = peak * CIRCLE_SOFT_PERCENTILE

        # Najdi první poloměr kde jas klesne pod threshold (zvenku dovnitř)
        # Hledáme přechod zprava (velký r) doleva
        edge_r = None
        for i in range(len(valid) - 1, -1, -1):
            if prof_valid[i] >= threshold:
                edge_r = valid[i]
                break

        if edge_r is None: return False
        if edge_r < r_min or edge_r > r_max: return False

        r = float(edge_r)
        self.circle_center_norm = QPointF(cx0 / w, cy0 / h)
        self.circle_r_norm  = r / min(w, h)
        self.circle_rx_norm = r / w
        self.circle_ry_norm = r / h
        return True
    
    def calibrate_cross_from_pixmap(self) -> bool:
        if self._pix is None or self._pix.isNull():
            return False
        pm = self._pix
        w0, h0 = pm.width(), pm.height()
        if w0 <= 0 or h0 <= 0:
            return False

        # Downscale pro rychlost
        target = 400
        scale = max(w0, h0) / target
        if scale > 1.0:
            small = pm.toImage().scaled(
                int(w0 / scale), int(h0 / scale),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            small = pm.toImage()

        g = small.convertToFormat(QImage.Format.Format_Grayscale8)
        g = _autostretch_gray(g, 1.0, 99.0)
        w, h = g.width(), g.height()
        if w < 10 or h < 10:
            return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy().astype(np.float32)

        # Těžiště intenzity (weighted centroid)
        total = arr.sum()
        if total < 1.0:
            return False

        ys, xs = np.mgrid[0:h, 0:w]
        cx = float((arr * xs).sum() / total)
        cy = float((arr * ys).sum() / total)

        self.cross_pos_norm = QPointF(cx / w, cy / h)
        return True

    # ------------------------------------------------------------------ square
    def calibrate_square_from_pixmap(self) -> bool:
        """
        Detects the bright rectangular region using gradient-based edge detection
        on row and column projections. Stores result as (left_norm, top_norm, right_norm, bottom_norm).
        """
        if self._pix is None or self._pix.isNull():
            return False

        pm = self._pix
        w0, h0 = pm.width(), pm.height()
        if w0 <= 0 or h0 <= 0:
            return False

        # Downscale for speed
        target = 700
        scale = max(w0, h0) / target
        if scale > 1.0:
            small_img = pm.toImage().scaled(
                int(w0 / scale), int(h0 / scale),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            small_img = pm.toImage()

        g = small_img.convertToFormat(QImage.Format.Format_Grayscale8)
        # Mild stretch so contrast is visible even in dark images
        g = _autostretch_gray(g, 1.0, 99.0)
        w, h = g.width(), g.height()
        if w < 50 or h < 50:
            return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy().astype(np.float32)

        # ---- smooth with a simple box kernel ----
        def smooth1d(a, k=7):
            kernel = np.ones(k, dtype=np.float32) / k
            return np.convolve(a, kernel, mode='same')

        # Row projection: mean brightness per row → find top/bottom edges
        row_proj = smooth1d(arr.mean(axis=1))
        # Col projection: mean brightness per col → find left/right edges
        col_proj = smooth1d(arr.mean(axis=0))

        def find_outer_edges(proj: np.ndarray, margin_frac: float = 0.04) -> tuple[int, int] | None:
            n = len(proj)
            margin = max(2, int(n * margin_frac))
            grad = np.gradient(proj)

            search_grad = grad[margin: n - margin]
            if len(search_grad) < 10:
                return None
            abs_grad = np.abs(search_grad)
            if abs_grad.max() < 0.5:
                return None

            # Peak rising (dark→bright) = největší kladný gradient
            rising_peak = int(np.argmax(search_grad)) + margin
            # Peak falling (bright→dark) = největší záporný gradient
            falling_peak = int(np.argmin(search_grad)) + margin

            if falling_peak <= rising_peak:
                return None
            if (falling_peak - rising_peak) < int(n * 0.15):
                return None

            # Kompenzace měkkého přechodu: posun hrany ven o půl šířky gradientu
            # Šířka = vzdálenost kde gradient > 50% peak hodnoty
            def edge_halfwidth(g_section, peak_sign):
                peak_val = g_section.max() if peak_sign > 0 else g_section.min()
                mask = (g_section * peak_sign) > abs(peak_val) * 0.5
                return max(1, int(mask.sum() / 2))

            hw_rise  = edge_halfwidth(search_grad[:rising_peak  - margin + 10], +1)
            hw_fall  = edge_halfwidth(search_grad[falling_peak  - margin - 10:], -1)

            left_edge  = max(margin, rising_peak  - hw_rise)
            right_edge = min(n - 1,  falling_peak + hw_fall)

            if right_edge <= left_edge:
                return None
            if (right_edge - left_edge) < int(n * 0.15):
                return None

            return left_edge, right_edge

        col_edges = find_outer_edges(col_proj)   # left, right  in pixel-x
        row_edges = find_outer_edges(row_proj)   # top,  bottom in pixel-y

        if col_edges is None or row_edges is None:
            return False

        left,  right  = col_edges
        top,   bottom = row_edges

        # Normalise to [0, 1] relative to full image size
        left_n   = left   / w
        right_n  = right  / w
        top_n    = top    / h
        bottom_n = bottom / h

        # Sanity checks
        if (right_n - left_n) < 0.10 or (bottom_n - top_n) < 0.10:
            return False
        if (right_n - left_n) > 0.99 or (bottom_n - top_n) > 0.99:
            return False

        self.square_rect_norm = (left_n, top_n, right_n, bottom_n)
        return True

# ---------------- MULTI CAMERA GRID ----------------
class CameraView(QWidget):
    """Jeden panel v multi-camera gridu — ImageView + label + výběr."""
    clicked = Signal(int)  # camera index

    def __init__(self, cam_index: int, cam_name: str, parent=None):
        super().__init__(parent)
        self.cam_index = cam_index
        self.cam_name  = cam_name
        self._selected = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        self.img_view = ImageView(self)
        self.img_view.bg_color = QColor("#222")
        lay.addWidget(self.img_view, 1)

        # Bottom row: camera name | timestamp
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(4)
        bottom_row.setContentsMargins(0, 0, 0, 0)

        self._name_lbl = QLabel(cam_name)
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._name_lbl.setStyleSheet(
            "font-size: 12px; color: #eee; background: #444; "
            "padding: 2px 4px; border-radius: 2px;")
        bottom_row.addWidget(self._name_lbl, 1)

        self._ts_lbl = QLabel("")
        self._ts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._ts_lbl.setStyleSheet(
            "font-size: 11px; color: #ffd54f; background: #333; "
            "padding: 2px 4px; border-radius: 2px;")
        bottom_row.addWidget(self._ts_lbl, 2)

        lay.addLayout(bottom_row)

        self._ref_lbl = QLabel("")
        self._ref_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ref_lbl.setStyleSheet(
            "font-size: 11px; color: #222; background: #c8e6c9; "
            "padding: 1px 4px; border-radius: 2px;")
        self._ref_lbl.hide()
        lay.addWidget(self._ref_lbl)

        self._update_border()

    def set_timestamp(self, text: str):
        self._ts_lbl.setText(text)

    def set_label_font_size(self, px: int):
        self._name_lbl.setStyleSheet(
            f"font-size: {px}px; color: #eee; background: #444; "
            "padding: 2px 4px; border-radius: 2px;")
        self._ts_lbl.setStyleSheet(
            f"font-size: {max(8, px - 1)}px; color: #ffd54f; background: #333; "
            "padding: 2px 4px; border-radius: 2px;")
        self._ref_lbl.setStyleSheet(
            f"font-size: {max(8, px - 1)}px; color: #222; background: #c8e6c9; "
            "padding: 1px 4px; border-radius: 2px;")

    def set_ref_status(self, text: str):
        if text:
            self._ref_lbl.setText(text)
            self._ref_lbl.show()
        else:
            self._ref_lbl.hide()

    def set_selected(self, sel: bool):
        self._selected = sel
        self._update_border()

    def _update_border(self):
        if self._selected:
            self.setStyleSheet(
                "CameraView { border: 3px solid #2d7dff; border-radius: 3px; background: #1a2a3a; }")
        else:
            self.setStyleSheet(
                "CameraView { border: 2px solid #555; border-radius: 3px; background: #222; }")

    def mousePressEvent(self, event):
        self.clicked.emit(self.cam_index)
        super().mousePressEvent(event)


class MultiCameraGrid(QWidget):
    """
    Grid zobrazení pro 2–4 kamery.
    Layout:
      - vertikální snímky (h > w): 4×1 (vedle sebe)
      - čtvercové / horizontální:  2×2
    Layout se volí automaticky: PDxM1 kamery → pravý sloupec (portrét), ostatní → levá strana (2×N).
    """
    camera_selected = Signal(int)  # index naposledy kliknuté kamery

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam_views: list[CameraView] = []
        self._selected_idx: int = 0          # naposledy kliknutá kamera
        self._selected_set: set[int] = set() # všechny aktuálně vybrané kamery
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._reg_container: "QWidget | None" = None  # sub-grid for regular cams in mixed layout
        self._overlay_store: dict[str, dict] = {}  # cam_name → overlay state

    @staticmethod
    def _save_iv_overlay(iv: "ImageView") -> dict:
        return {
            "show_cross":          iv.show_cross,
            "cross_pos_norm":      iv.cross_pos_norm,
            "show_circle":         iv.show_circle,
            "circle_center_norm":  iv.circle_center_norm,
            "circle_r_norm":       iv.circle_r_norm,
            "circle_rx_norm":      iv.circle_rx_norm,
            "circle_ry_norm":      iv.circle_ry_norm,
            "show_square":         iv.show_square,
            "square_rect_norm":    iv.square_rect_norm,
        }

    @staticmethod
    def _restore_iv_overlay(iv: "ImageView", state: dict):
        iv.show_cross         = state.get("show_cross", False)
        iv.cross_pos_norm     = state.get("cross_pos_norm")
        iv.show_circle        = state.get("show_circle", False)
        iv.circle_center_norm = state.get("circle_center_norm")
        iv.circle_r_norm      = state.get("circle_r_norm")
        iv.circle_rx_norm     = state.get("circle_rx_norm")
        iv.circle_ry_norm     = state.get("circle_ry_norm")
        iv.show_square        = state.get("show_square", False)
        iv.square_rect_norm   = state.get("square_rect_norm")

    def setup_cameras(self, cam_names: list[str]):
        """Vytvoří/překreslí kamery podle seznamu jmen."""
        # Ulož overlay stav stávajících kamer před zničením
        for cv in self._cam_views:
            self._overlay_store[cv.cam_name] = self._save_iv_overlay(cv.img_view)

        # Odstraň staré
        for cv in self._cam_views:
            cv.setParent(None)
        self._cam_views.clear()
        if self._reg_container is not None:
            self._reg_container.setParent(None)
            self._reg_container = None
        self._cam_names_list = list(cam_names)

        for i, name in enumerate(cam_names):
            cv = CameraView(i, name, self)
            cv.clicked.connect(self._on_cam_clicked)
            self._cam_views.append(cv)
            # Obnov overlay stav pokud ho máme uložený
            if name in self._overlay_store:
                self._restore_iv_overlay(cv.img_view, self._overlay_store[name])

        self._selected_idx = 0
        self._selected_set = {0} if self._cam_views else set()
        if self._cam_views:
            self._cam_views[0].set_selected(True)

        self._rebuild_grid()

    @staticmethod
    def _is_portrait_camera(name: str) -> bool:
        """PDX M1_DF kamery (PD1M1DF, PD2M1DF, ...) jsou portrétní (výška >> šířka).
        PDX M2 kamery (PD1M2NF, ...) jsou čtvercové — NESMÍ být označeny jako portrétní."""
        return bool(re.search(r"PD[1-4]M1.?DF", name, re.IGNORECASE))

    def _detect_orientation(self) -> str:
        """Zjisti orientaci — nejdříve podle jmen kamer, pak podle pixmapu."""
        # Pokud jakákoli kamera je PDXM1_DF → portrétní layout
        names = getattr(self, '_cam_names_list', [])
        if any(self._is_portrait_camera(n) for n in names):
            return "vertical"
        # Fallback: pixmapová detekce
        for cv in self._cam_views:
            pm = cv.img_view._pix
            if pm and not pm.isNull():
                return "vertical" if pm.height() > pm.width() else "square"
        return "square"

    @staticmethod
    def _is_pdxm1_cam(name: str) -> bool:
        """True only for M1 portrait cameras (PD[1-4]M1). M2 cameras (PD[1-4]M2) are square — excluded."""
        return bool(re.search(r'PD[1-4]M1(?!M2|\d)', name, re.IGNORECASE))

    def _rebuild_grid(self):
        # Remove all cam views and the reg container from the main grid
        for cv in self._cam_views:
            self._grid.removeWidget(cv)
        if self._reg_container is not None:
            self._grid.removeWidget(self._reg_container)
            self._reg_container.setParent(None)
            self._reg_container = None
        # Reset all stretches
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)

        n = len(self._cam_views)
        if n == 0:
            return

        # Split cameras into PDxM1 (tall portrait) and regular (square)
        pdxm1_views   = [cv for cv in self._cam_views if self._is_pdxm1_cam(cv.cam_name)]
        regular_views = [cv for cv in self._cam_views if not self._is_pdxm1_cam(cv.cam_name)]

        if not pdxm1_views:
            # All regular → 2×2 grid (or single row if ≤2)
            if n <= 2:
                for i, cv in enumerate(self._cam_views):
                    self._grid.addWidget(cv, 0, i)
                    self._grid.setColumnStretch(i, 1)
                self._grid.setRowStretch(0, 1)
            else:
                for i, cv in enumerate(self._cam_views):
                    r, c = divmod(i, 2)
                    if i == len(self._cam_views) - 1 and len(self._cam_views) % 2 == 1:
                        # Last camera in an odd row — span both columns
                        self._grid.addWidget(cv, r, 0, 1, 2)
                    else:
                        self._grid.addWidget(cv, r, c)
                self._grid.setColumnStretch(0, 1)
                self._grid.setColumnStretch(1, 1)
                self._grid.setRowStretch(0, 1)
                self._grid.setRowStretch(1, 1)
            return

        if not regular_views:
            # All PDxM1 → 1×N row
            for i, cv in enumerate(pdxm1_views):
                self._grid.addWidget(cv, 0, i)
                self._grid.setColumnStretch(i, 1)
            self._grid.setRowStretch(0, 1)
            return

        # ── Mixed layout: PDxM1 left, regular cameras right ──────────────────
        # PDxM1 cameras share the left portion (side by side, full height).
        # Regular cameras go into a sub-grid on the right:
        #   1 regular  → fills the right side entirely
        #   2 regular  → stacked 1×2 (vertical)
        #   3 regular  → 2×2 grid, top-left + top-right + bottom-left, bottom-right empty
        #   4 regular  → 2×2 grid fully filled
        n_pdx = len(pdxm1_views)
        n_reg = len(regular_views)

        # Build the regular-cameras sub-container
        reg_widget = QWidget(self)
        reg_widget.setStyleSheet("background: transparent;")
        reg_grid = QGridLayout(reg_widget)
        reg_grid.setContentsMargins(0, 0, 0, 0)
        reg_grid.setSpacing(4)

        if n_reg == 1:
            reg_grid.addWidget(regular_views[0], 0, 0)
            reg_grid.setRowStretch(0, 1)
            reg_grid.setColumnStretch(0, 1)
        elif n_reg == 2:
            for i, cv in enumerate(regular_views):
                reg_grid.addWidget(cv, i, 0)
                reg_grid.setRowStretch(i, 1)
            reg_grid.setColumnStretch(0, 1)
        else:
            # 3 or 4 regular → 2×2 grid; if only 3, last camera spans both columns, centered
            for i, cv in enumerate(regular_views):
                r, c = divmod(i, 2)
                if i == len(regular_views) - 1 and len(regular_views) % 2 == 1:
                    reg_grid.addWidget(cv, r, 0, 1, 2)
                else:
                    reg_grid.addWidget(cv, r, c)
            reg_grid.setColumnStretch(0, 1)
            reg_grid.setColumnStretch(1, 1)
            reg_grid.setRowStretch(0, 1)
            reg_grid.setRowStretch(1, 1)

        self._reg_container = reg_widget

        # Place PDxM1 cameras in columns 0..n_pdx-1, spanning full height
        # Each M1 column gets stretch 2 — narrow but not squished next to a 2-col regular grid
        for j, cv in enumerate(pdxm1_views):
            self._grid.addWidget(cv, 0, j, 1, 1)
            self._grid.setColumnStretch(j, 2)
        self._grid.setRowStretch(0, 1)

        # Place regular sub-grid in the next column
        # Regular side stretch: 2 columns × 2 = 4 so each regular cam ~matches one M1 col width
        reg_col = n_pdx
        self._grid.addWidget(reg_widget, 0, reg_col, 1, 1)
        self._grid.setColumnStretch(reg_col, max(4, n_reg * 2))

    def _on_cam_clicked(self, idx: int):
        # Toggle selection: klik přidá/odebere kameru z výběru, může být 0 vybraných
        if idx in self._selected_set:
            self._selected_set.discard(idx)
        else:
            self._selected_set.add(idx)
        self._selected_idx = idx
        for cv in self._cam_views:
            cv.set_selected(cv.cam_index in self._selected_set)
        self.camera_selected.emit(idx)

    def selected_cam_index(self) -> int:
        return self._selected_idx

    def selected_cam_indices(self) -> list[int]:
        """Vrátí seznam indexů všech vybraných kamer (sorted)."""
        return sorted(self._selected_set)

    def selected_img_view(self) -> ImageView | None:
        if 0 <= self._selected_idx < len(self._cam_views):
            return self._cam_views[self._selected_idx].img_view
        return None

    def get_img_view(self, idx: int) -> ImageView | None:
        if 0 <= idx < len(self._cam_views):
            return self._cam_views[idx].img_view
        return None

    def set_cam_timestamp(self, cam_idx: int, text: str):
        if 0 <= cam_idx < len(self._cam_views):
            self._cam_views[cam_idx].set_timestamp(text)

    def set_label_font_size(self, px: int):
        for cv in self._cam_views:
            cv.set_label_font_size(px)

    def set_cam_ref_status(self, cam_idx: int, text: str):
        if 0 <= cam_idx < len(self._cam_views):
            self._cam_views[cam_idx].set_ref_status(text)

    def cam_count(self) -> int:
        return len(self._cam_views)

# ---------------- TICK BAR ----------------
class TickBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.axis_min_ns = 0; self.axis_max_ns = 0
        self.step_minutes = TICK_STEP_MINUTES
        self.setMinimumHeight(56)
        self.mark_a_ns: int | None = None
        self.mark_b_ns: int | None = None
        self.cursor_ns: int | None = None
        self.left_offset: int = 0   # pixels reserved for slider label prefix (multi-cam)
        self.discrete_ticks: list[int] | None = None
        self.discrete_tick_labels: list[str] | None = None

    def set_axis(self, a, b): self.axis_min_ns = a; self.axis_max_ns = b; self.update()
    def set_marks(self, a, b): self.mark_a_ns = a; self.mark_b_ns = b; self.update()
    def set_cursor(self, t: "int | None"): self.cursor_ns = t; self.update()
    def set_left_offset(self, px: int): self.left_offset = px; self.update()

    def _axis_w(self) -> int:
        return max(1, self.width() - self.left_offset)

    def _x_from_ns(self, t) -> int:
        if self.axis_max_ns <= self.axis_min_ns: return self.left_offset
        frac = (t - self.axis_min_ns) / (self.axis_max_ns - self.axis_min_ns)
        return self.left_offset + int(round(max(0.0, min(1.0, frac)) * (self._axis_w() - 1)))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.axis_max_ns <= self.axis_min_ns: return
        w, h = self.width(), self.height()
        lo = self.left_offset          # pixel offset where the axis starts
        aw = max(1, w - lo)            # width of the axis area
        p = QPainter(self)
        fm = QFontMetrics(self.font())
        lh = fm.height()               # label height

        def _x(t) -> int:
            frac = (t - self.axis_min_ns) / (self.axis_max_ns - self.axis_min_ns)
            return lo + int(round(max(0.0, min(1.0, frac)) * (aw - 1)))

        # ── Discrete mode: single camera, show per-frame ticks ────────
        if self.discrete_ticks is not None and self.discrete_ticks:
            ticks = self.discrete_ticks
            n = len(ticks)
            min_label_w = fm.horizontalAdvance("2026-03-23 14:53:12") + 8
            max_visible = max(1, aw // min_label_w)
            step = max(1, (n + max_visible - 1) // max_visible)

            # Baseline
            p.setPen(QPen(QColor(160, 160, 160)))
            p.drawLine(lo, h // 2, w, h // 2)

            last_label_x = lo - 9999
            for i, t in enumerate(ticks):
                x = _x(t)
                # Minor tick for every frame
                pen = QPen(QColor(140, 140, 140, 160)); pen.setWidth(1); p.setPen(pen)
                p.drawLine(x, h // 2 - 3, x, h // 2 + 3)
                if i % step != 0:
                    continue
                # Major tick
                pen = QPen(QColor(80, 80, 80, 200)); pen.setWidth(1); p.setPen(pen)
                p.drawLine(x, h // 2 - 6, x, h // 2 + 6)
                if self.discrete_tick_labels and i < len(self.discrete_tick_labels):
                    label = self.discrete_tick_labels[i]
                else:
                    dt = _dt_from_ns(t)
                    label = f"{dt:%Y-%m-%d %H:%M:%S}"
                lw = fm.horizontalAdvance(label)
                lx = x - lw // 2
                if lx < last_label_x + 4:
                    continue
                lx = max(lo, min(w - lw, lx))
                lr = QRect(lx, h // 2 + 8, lw, lh)
                if lr.bottom() > h:
                    lr.moveTop(h // 2 - 8 - lh)
                p.fillRect(lr.adjusted(-2, 0, 2, 0), QColor(255, 255, 255, 200))
                p.setPen(QColor(0, 0, 0))
                p.drawText(lr, Qt.AlignmentFlag.AlignVCenter, label)
                last_label_x = lx + lw

            def _draw_mark_discrete(t, color):
                x = _x(t)
                pen = QPen(color); pen.setWidth(2); p.setPen(pen)
                p.drawLine(x, 0, x, h)
            if self.mark_a_ns is not None: _draw_mark_discrete(self.mark_a_ns, QColor(255, 0, 0, 230))
            if self.mark_b_ns is not None: _draw_mark_discrete(self.mark_b_ns, QColor(0, 0, 255, 230))

            if self.cursor_ns is not None:
                xc = _x(self.cursor_ns)
                pen = QPen(QColor(0, 120, 255, 230)); pen.setWidth(2); p.setPen(pen)
                p.drawLine(xc, 0, xc, h)
                label = fmt_hhmmss_ms_from_ns(self.cursor_ns)
                lw = fm.horizontalAdvance(label) + 6
                lx = max(lo, min(w - lw, xc - lw // 2))
                p.fillRect(lx, h - lh - 2, lw, lh + 2, QColor(0, 80, 200, 200))
                p.setPen(QColor(255, 255, 255))
                p.drawText(lx + 3, h - 2 - fm.descent(), label)
            p.end()
            return

        # ── Normal time axis ──────────────────────────────────────────
        span = self.axis_max_ns - self.axis_min_ns
        span_hours = span / ONE_HOUR_NS

        if span_hours >= 6:
            step_min = 60;  minor_min = 5
        elif span_hours >= 3:
            step_min = 30;  minor_min = 5
        elif span_hours >= 1.5:
            step_min = 15;  minor_min = 2
        elif span_hours >= 0.5:
            step_min = 10;  minor_min = 1
        else:
            step_min = 5;   minor_min = 1

        def _aligned_ticks(step_m: int) -> list[int]:
            start_dt = _dt_from_ns(self.axis_min_ns).replace(second=0, microsecond=0)
            rem = start_dt.minute % step_m
            if rem:
                start_dt = start_dt - timedelta(minutes=rem)
            end_dt = _dt_from_ns(self.axis_max_ns).replace(second=0, microsecond=0)
            result = []; dt = start_dt
            while dt <= end_dt + timedelta(minutes=step_m):
                result.append(ns_from_dt(dt)); dt += timedelta(minutes=step_m)
            return result

        major_ticks = _aligned_ticks(step_min)
        minor_ticks = _aligned_ticks(minor_min)
        major_set = set(major_ticks)

        # Layout (top-to-bottom):
        #   [lh+2]  tick labels
        #   [8px]   major tick stubs above baseline
        #   [1px]   baseline
        #   [4px]   minor tick stubs below baseline
        #   [lh+4]  cursor label at bottom
        cursor_label_h = lh + 4
        baseline_y = h - cursor_label_h - 1
        major_tick_top = baseline_y - 8
        minor_tick_bot = baseline_y + 4

        # Baseline — thick, clearly visible
        baseline_pen = QPen(QColor(100, 100, 100)); baseline_pen.setWidth(2)
        p.setPen(baseline_pen)
        p.drawLine(lo, baseline_y, w, baseline_y)

        # Minor ticks below baseline
        pen = QPen(QColor(160, 160, 160)); pen.setWidth(1); p.setPen(pen)
        for t in minor_ticks:
            if t in major_set: continue
            x = _x(t)
            if x < lo or x > w: continue
            p.drawLine(x, baseline_y, x, minor_tick_bot)

        # Major ticks above baseline + labels above them
        last_label_x = lo - 9999
        for t in major_ticks:
            x = _x(t)
            if x < lo or x > w: continue
            pen = QPen(QColor(60, 60, 60)); pen.setWidth(1); p.setPen(pen)
            p.drawLine(x, major_tick_top, x, baseline_y)
            txt = fmt_hhmm_from_ns(t)
            tw = fm.horizontalAdvance(txt)
            tx = x - tw // 2
            tx = max(lo, min(w - tw, tx))
            if tx < last_label_x + 4:
                continue
            label_y = major_tick_top - lh - 1
            if label_y < 0: label_y = 0
            p.setPen(QColor(0, 0, 0))
            p.drawText(QRect(tx, label_y, tw, lh), Qt.AlignmentFlag.AlignVCenter, txt)
            last_label_x = tx + tw

        # Midnight date labels at bottom
        midnight_dates: list[int] = []
        _d = _dt_from_ns(self.axis_min_ns).date()
        _d_end = _dt_from_ns(self.axis_max_ns).date()
        while _d <= _d_end:
            try:
                _mn_dt = datetime(_d.year, _d.month, _d.day, 0, 0, 0, tzinfo=TZ_PRAGUE)
            except Exception:
                _mn_dt = datetime(_d.year, _d.month, _d.day, 0, 0, 0)
            _mn_ns = int(_mn_dt.timestamp() * 1_000_000_000)
            if self.axis_min_ns <= _mn_ns <= self.axis_max_ns:
                midnight_dates.append(_mn_ns)
            _d += timedelta(days=1)

        if midnight_dates or span_hours > 20:
            date_font = QFont(self.font()); date_font.setBold(True)
            p.setFont(date_font)
            dfm = QFontMetrics(date_font)
            dlh = dfm.height()
            date_y = h - dlh - 1

            def _draw_date_label(ns_val: int, align_right=False):
                dt_val = _dt_from_ns(ns_val)
                lbl = dt_val.strftime("%d.%m")
                lw = dfm.horizontalAdvance(lbl)
                x_v = _x(ns_val)
                lx = (max(lo, x_v - lw) if align_right else min(w - lw, x_v))
                frac_v = (ns_val - self.axis_min_ns) / span
                if 0.0001 < frac_v < 0.9999:
                    sep_pen = QPen(QColor(40, 80, 180, 120)); sep_pen.setWidth(1)
                    p.setPen(sep_pen); p.drawLine(x_v, baseline_y, x_v, h)
                p.fillRect(QRect(lx, date_y, lw, dlh), QColor(230, 235, 255, 220))
                p.setPen(QColor(40, 80, 180))
                p.drawText(QRect(lx, date_y, lw, dlh), Qt.AlignmentFlag.AlignVCenter, lbl)

            _draw_date_label(self.axis_min_ns, align_right=False)
            _draw_date_label(self.axis_max_ns, align_right=True)
            for mn_ns in midnight_dates:
                _draw_date_label(mn_ns, align_right=False)
            p.setFont(self.font())

        # Marks (Set From / Set To) — drawn above baseline
        def draw_mark(t, color, nudge=0):
            x = _x(t)
            pen = QPen(color); pen.setWidth(2); p.setPen(pen); p.drawLine(x, 0, x, baseline_y)
            label = fmt_hhmmss_ms_from_ns(t); lw = fm.horizontalAdvance(label)
            lx = max(lo, min(w - lw, x - lw // 2 + nudge))
            lr = QRect(lx, baseline_y - lh - 2, lw, lh)
            p.fillRect(lr.adjusted(-4, 0, 4, 0), QColor(255, 255, 255, 210))
            p.setPen(color); p.drawText(lr, Qt.AlignmentFlag.AlignVCenter, label)

        if self.mark_a_ns is not None and self.mark_b_ns is not None:
            lw = fm.horizontalAdvance(fmt_hhmmss_ms_from_ns(self.mark_a_ns))
            overlap = (lw + 8) - abs(_x(self.mark_b_ns) - _x(self.mark_a_ns))
            if overlap > 0:
                draw_mark(self.mark_a_ns, QColor(255, 0, 0, 230), -(overlap // 2 + 2))
                draw_mark(self.mark_b_ns, QColor(0, 0, 255, 230),   overlap // 2 + 2)
            else:
                draw_mark(self.mark_a_ns, QColor(255, 0, 0, 230))
                draw_mark(self.mark_b_ns, QColor(0, 0, 255, 230))
        else:
            if self.mark_a_ns is not None: draw_mark(self.mark_a_ns, QColor(255, 0, 0, 230))
            if self.mark_b_ns is not None: draw_mark(self.mark_b_ns, QColor(0, 0, 255, 230))

        # Cursor line — blue, label at bottom
        if self.cursor_ns is not None:
            xc = _x(self.cursor_ns)
            pen = QPen(QColor(0, 120, 255, 230)); pen.setWidth(2); p.setPen(pen)
            p.drawLine(xc, 0, xc, h)
            label = fmt_hhmmss_ms_from_ns(self.cursor_ns)
            lw = fm.horizontalAdvance(label) + 6
            lx = max(lo, min(w - lw, xc - lw // 2))
            p.fillRect(lx, h - lh - 2, lw, lh + 2, QColor(0, 80, 200, 200))
            p.setPen(QColor(255, 255, 255))
            p.drawText(lx + 3, h - 2 - fm.descent(), label)
        p.end()

# ---------------- LAYOUT HELPERS ----------------
def _hsep() -> QFrame:
    f = QFrame(); f.setFrameShape(QFrame.Shape.HLine); f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #ccc; margin: 2px 0;"); return f

def _group_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size: 10px; color: #777; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; padding-top: 2px;")
    return lbl

class _DirItem:
    """Lazy node pro stromový model složek."""
    def __init__(self, path: Path, parent=None):
        self.path = path
        self.parent_item = parent
        self.children: list["_DirItem"] = []
        self.loaded = False

class _LazyDirModel(QObject):
    """Jednoduchý model pro QTreeView — načítá složky lazy."""
    from PySide6.QtCore import QAbstractItemModel, QModelIndex
    pass

from PySide6.QtCore import QAbstractItemModel, QModelIndex as _QModelIndex

class LazyDirModel(QAbstractItemModel):
    def __init__(self, root_path: Path, parent=None):
        super().__init__(parent)
        self._root = _DirItem(root_path)
        # Nenačítáme synchronně — lazy load při prvním rozbalení
        self._root.loaded = False

    def _load_children(self, item: _DirItem):
        if item.loaded: return
        item.loaded = True
        try:
            dirs = sorted(
                [p for p in item.path.iterdir() if p.is_dir()],
                key=lambda p: p.name.lower()
            )
            item.children = [_DirItem(d, item) for d in dirs]
        except Exception:
            item.children = []

    def _item_from_index(self, index: _QModelIndex) -> _DirItem:
        if not index.isValid():
            return self._root
        return index.internalPointer()

    def index(self, row, col, parent=_QModelIndex()):
        parent_item = self._item_from_index(parent)
        self._load_children(parent_item)
        if row < 0 or row >= len(parent_item.children):
            return _QModelIndex()
        child = parent_item.children[row]
        return self.createIndex(row, col, child)

    def parent(self, index=_QModelIndex()):
        if not index.isValid():
            return _QModelIndex()
        item = index.internalPointer()
        if item is None or item.parent_item is None:
            return _QModelIndex()
        p = item.parent_item
        if p.parent_item is None:
            return _QModelIndex()
        try:
            row = p.parent_item.children.index(p)
        except ValueError:
            return _QModelIndex()
        return self.createIndex(row, 0, p)

    def rowCount(self, parent=_QModelIndex()):
        item = self._item_from_index(parent)
        self._load_children(item)
        return len(item.children)

    def columnCount(self, parent=_QModelIndex()):
        return 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        item = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return item.path.name
        return None

    def hasChildren(self, parent=_QModelIndex()):
        item = self._item_from_index(parent)
        if not item.loaded:
            return True  # Optimisticky — ukáže šipku
        return len(item.children) > 0

    def filepath(self, index: _QModelIndex) -> str:
        if not index.isValid(): return str(self._root.path)
        return str(index.internalPointer().path)

    def index_for_path(self, path: Path) -> _QModelIndex:
        """Najde index pro danou cestu — postupně rozbalí strom."""
        try:
            rel = path.relative_to(self._root.path)
        except ValueError:
            return _QModelIndex()
        parts = rel.parts
        current_idx = _QModelIndex()
        current_item = self._root
        for part in parts:
            self._load_children(current_item)
            found = False
            for i, child in enumerate(current_item.children):
                if child.path.name.lower() == part.lower():
                    current_idx = self.createIndex(i, 0, child)
                    current_item = child
                    found = True
                    break
            if not found:
                return _QModelIndex()
        return current_idx


class FolderPickerDialog(QDialog):
    def __init__(self, start_path: str, title: str = "Select folder", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 450)
        self.selected_path: str = ""

        # Najdi rozumný root — 2 úrovně nad start_path
        try:
            sp = Path(start_path)
            root = sp.parent.parent if sp.parent.parent.exists() else sp.parent
        except Exception:
            root = Path(start_path) if start_path else Path(".")

        self._model = LazyDirModel(root)

        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(20)
        self._tree.setUniformRowHeights(True)

        # Naviguj na start_path
        if start_path:
            try:
                idx = self._model.index_for_path(Path(start_path))
                if idx.isValid():
                    self._tree.setCurrentIndex(idx)
                    self._tree.scrollTo(idx)
                    self._tree.expand(idx)
            except Exception:
                pass

        self._path_edit = QLineEdit(start_path)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._tree.clicked.connect(self._on_clicked)
        self._tree.doubleClicked.connect(self._on_double_clicked)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Path:"))
        lay.addWidget(self._path_edit)
        lay.addWidget(self._tree, 1)
        lay.addWidget(btns)

    def _on_clicked(self, index):
        self._path_edit.setText(self._model.filepath(index))

    def _on_double_clicked(self, index):
        self._tree.expand(index)
        self._path_edit.setText(self._model.filepath(index))

    def _on_path_entered(self):
        path = self._path_edit.text().strip()
        try:
            idx = self._model.index_for_path(Path(path))
            if idx.isValid():
                self._tree.setCurrentIndex(idx)
                self._tree.scrollTo(idx)
        except Exception:
            pass

    def _on_accept(self):
        self.selected_path = self._path_edit.text().strip()
        self.accept()

    @staticmethod
    def get_folder(start_path: str, title: str = "Select folder", parent=None) -> str:
        dlg = FolderPickerDialog(start_path, title, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected_path
        return ""
    
class _CamPollSignals(QObject):
    found = Signal(int, list, list)  # cam_i, new_items, new_folders


class _CamPollTask(QRunnable):
    """Scan one camera's folder list for new images since cutoff ts_ns.
    Also probes next UTC hour-folders for auto-discovery."""

    def __init__(self, cam_idx: int, folders: list, cutoff: int,
                 cam_name: str, signal: "_CamPollSignals"):
        super().__init__()
        self.setAutoDelete(True)
        self._cam_i    = cam_idx
        self._folders  = folders
        self._cutoff   = cutoff
        self._cam_name = cam_name
        self._sig      = signal

    def run(self):
        new_items: list = []
        for folder in self._folders:
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        if not e.is_file():
                            continue
                        p = Path(e.path)
                        if p.suffix.lower() not in IMG_EXT:
                            continue
                        ts_ns = parse_unix_ns_from_name(p)
                        if ts_ns is None or ts_ns <= self._cutoff:
                            continue
                        new_items.append(Item(p, ts_ns))
            except Exception:
                pass
        if new_items:
            new_items.sort(key=lambda x: x.ts_ns)

        # Auto-discover next UTC hour-folders
        new_folders: list = []
        if self._cam_name:
            known = set(self._folders)
            for folder in self._folders:
                try:
                    hour_dir = folder.parent
                    day_dir  = hour_dir.parent
                    try:
                        current_utc_hour = int(hour_dir.name)
                    except ValueError:
                        continue
                    for delta in range(1, 4):
                        next_h = (current_utc_hour + delta) % 24
                        if next_h < current_utc_hour and delta == 1:
                            try:
                                from datetime import date as _date, timedelta as _td
                                day_parts = (int(day_dir.parent.parent.name),
                                             int(day_dir.parent.name),
                                             int(day_dir.name))
                                next_day = _date(*day_parts) + _td(days=1)
                                candidate = (day_dir.parent.parent.parent
                                             / str(next_day.year)
                                             / str(next_day.month)
                                             / str(next_day.day)
                                             / str(next_h)
                                             / self._cam_name)
                            except Exception:
                                continue
                        else:
                            candidate = day_dir / str(next_h) / self._cam_name
                        if candidate in known or candidate in new_folders:
                            break
                        if candidate.exists() and candidate.is_dir():
                            new_folders.append(candidate)
                            known.add(candidate)
                            try:
                                with os.scandir(candidate) as it2:
                                    for e in it2:
                                        if not e.is_file():
                                            continue
                                        p = Path(e.path)
                                        if p.suffix.lower() not in IMG_EXT:
                                            continue
                                        ts_ns = parse_unix_ns_from_name(p)
                                        if ts_ns is None or ts_ns <= self._cutoff:
                                            continue
                                        new_items.append(Item(p, ts_ns))
                            except Exception:
                                pass
                        else:
                            break
                except Exception:
                    pass
            if new_items:
                new_items.sort(key=lambda x: x.ts_ns)

        self._sig.found.emit(self._cam_i, new_items, new_folders)


# ================================================================== PER-CAM SLIDER ROW

class _CamSliderRow(QWidget):
    """One row: [● Master radio] [Camera name label] [━━━━ slider ━━━━]"""
    master_chosen     = Signal(int)   # emitted when radio is checked; arg = cam index
    master_deselected = Signal(int)   # emitted when radio is unchecked; arg = cam index
    value_changed     = Signal(int, int)  # (cam_index, slider_value)
    pressed           = Signal(int)   # cam_index
    released          = Signal(int)   # cam_index

    def __init__(self, cam_idx: int, cam_name: str, parent=None):
        super().__init__(parent)
        self.cam_idx  = cam_idx
        self._is_master = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(4)

        from PySide6.QtWidgets import QRadioButton
        self._radio = QRadioButton()
        self._radio.setAutoExclusive(False)  # allow clicking checked radio to uncheck it
        self._radio.setToolTip("Set as master camera (sync others to this). Click again to deselect.")
        self._radio.setFixedWidth(16)
        self._radio.toggled.connect(self._on_radio_toggled)
        lay.addWidget(self._radio)

        self._lbl = QLabel(cam_name)
        self._lbl.setFixedWidth(90)
        self._lbl.setStyleSheet("font-size: 10px; color: #333;")
        lay.addWidget(self._lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(SLIDER_MAX)
        self._slider.setEnabled(False)
        self._slider.valueChanged.connect(lambda v: self.value_changed.emit(self.cam_idx, v))
        self._slider.sliderPressed.connect(lambda: self.pressed.emit(self.cam_idx))
        self._slider.sliderReleased.connect(lambda: self.released.emit(self.cam_idx))
        lay.addWidget(self._slider, 1)

    def set_master(self, yes: bool):
        self._is_master = yes
        self._radio.blockSignals(True)
        self._radio.setChecked(yes)
        self._radio.blockSignals(False)
        self._lbl.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #0055cc;" if yes
            else "font-size: 10px; color: #333;")

    def set_value(self, v: int):
        self._slider.blockSignals(True)
        self._slider.setValue(v)
        self._slider.blockSignals(False)

    def set_enabled(self, on: bool):
        self._slider.setEnabled(on)

    def value(self) -> int:
        return self._slider.value()

    def slider_x_in_parent(self) -> int:
        """X position of the slider's left edge relative to this row widget."""
        return self._slider.x()

    def _on_radio_toggled(self, checked: bool):
        if checked:
            self.master_chosen.emit(self.cam_idx)
        else:
            self.master_deselected.emit(self.cam_idx)


# ================================================================== UI
class Viewer(QWidget):
    def __init__(self):
        super().__init__()
        # Title is set by the parent window (main.py)
        # self.setWindowTitle("Image Slider")

        self._gen = 0
        self.items: list[Item] = []
        self.ts_list: list[int] = []
        self.axis_min_ns = 0; self.axis_max_ns = 0
        self.opened_folder: Path | None = None
        self.opened_folders: list[Path] = []
        self.axis_override: tuple[int, int] | None = None
        self.last_open_dir = Path(DEFAULT_OPEN_DIR)
        self._last_save_dir: Path = Path(DEFAULT_SAVE_DIR)

        now_dt = datetime.now(TZ_PRAGUE)
        self.last_pick_date = now_dt.date()
        self.last_pick_hour_from: "int | None" = None   # None = first open, default to live mode
        self.last_pick_hour_to:   "int | None" = None
        self.last_pick_axis_override: tuple[int, int] | None = None
        self.last_pick_cam_names: list[str] = []   # paměť vybraných kamer

        self.pending_slider = None
        self._is_playing = False
        self._is_scrubbing = False
        self._last_motion_counter = None
        self._last_target_idx = None
        self._last_motion_ips = 0.0
        self.play_time_ns: int | None = None
        self.target_idx:   int | None = None
        self.current_idx:  int | None = None
        self._display_load_key = None
        self._deferred_display = None
        self._play_frame_acc = 0.0
        self._discrete_mode = True   # slider skáče po indexech, ne po čase
        self._fake_ts_map = None
        self._real_ts_list: list[int] = []

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(PLAY_TICK_MS)
        self.play_timer.timeout.connect(self._autoplay_step)

        self._prefetch_debounce = QTimer(self)
        self._brightness_debounce = QTimer(self)
        self._brightness_debounce.setSingleShot(True)
        self._brightness_debounce.timeout.connect(self._apply_brightness_debounced)
        self._prefetch_debounce.setSingleShot(True)
        self._prefetch_debounce.timeout.connect(self._run_prefetch_after_idle)

        self._scrub_side = SCRUB_MAX_SIDE
        self.cache = PixCache(CACHE_SIZE)

        self.scan_pool = QThreadPool(self); self.scan_pool.setMaxThreadCount(4)
        # Separate pool for online polling — one thread per camera so they run in parallel
        self._poll_pool = QThreadPool(self); self._poll_pool.setMaxThreadCount(8)
        self.load_pool = QThreadPool(self); self.load_pool.setMaxThreadCount(3)
        self.analysis_pool = QThreadPool(self); self.analysis_pool.setMaxThreadCount(1)

        self.load_signals = LoaderSignals()
        self.load_signals.loaded.connect(self._on_loaded)

        self._display_req_id = 0
        self._inflight: set = set()
        self._want_display_req: dict = {}
        self._scan_task: ScanTask | None = None
        self._save_task: SaveRangeTask | None = None
        self._refresh_task: RefreshScanTask | None = None
        self.mark_a_ns: int | None = None
        self.mark_b_ns: int | None = None
        self._pointing_task: PointingAnalysisTask | None = None
        self._brightness_offset: int = 0  # -255 .. +255
        self._ref_image: np.ndarray | None = None  # reference frame pro subtraction
        self._sf_energy_map: dict[str, str] = {}  # filename -> energie ze Shot Finderu
        self._saved_timestamps: list[tuple[int, str]] = []  # (ts_ns, label)

        # ── Multi-camera state ───────────────────────────────────────────────
        self._cam_names:        list[str]         = []   # jména načtených kamer
        self._cam_folders:      list[Path]        = []   # jedna (první) složka per-camera (legacy)
        self._cam_folder_lists: list[list[Path]]  = []   # všechny složky per-camera (pro online poll)
        # Per-camera items, ts_list, cache, load_pools
        self._cam_items:   list[list]  = []        # list of list[Item]
        self._cam_ts:      list[list]  = []        # list of list[int]
        self._cam_caches:     list        = []        # list of PixCache
        self._cam_pools:      list        = []        # list of QThreadPool
        self._cam_signals:    list        = []        # list of LoaderSignals
        self._cam_ref_images: list        = []        # list of np.ndarray | None, per-camera subtraction reference

        # ── Online mode state ────────────────────────────────────────────────
        self._online_mode    = False
        self._online_timer   = QTimer(self)
        self._online_timer.setInterval(100)
        self._online_timer.timeout.connect(self._online_poll)
        self._auto_follow    = False   # sleduj nejnovější snímek
        self._online_blink_state = False
        self._online_last_new_ns = 0.0  # čas posledního nového snímku
        self._online_blink_timer = QTimer(self)
        self._online_blink_timer.setInterval(600)
        self._online_blink_timer.timeout.connect(self._on_online_blink)

        self._build_ui()

        # Overlay appearance settings
        self._overlay_cross_color   = QColor(0, 255, 0, 220)
        self._overlay_cross_thick   = 2
        self._overlay_cross_size    = 18
        self._overlay_circle_color  = QColor(255, 255, 0, 230)
        self._overlay_circle_thick  = 2
        self._overlay_square_color  = QColor(0, 200, 255, 230)
        self._overlay_square_thick  = 2

    # ---------------------------------------------------------------- build UI
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        self._root_layout = root

        # ═══════════════════════ LEFT PANEL ═══════════════════════
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(275)
        left_scroll.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollBar:vertical { width: 8px; }")

        left = QWidget()
        left.setMinimumWidth(255)
        left.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        llay = QVBoxLayout(left)
        llay.setContentsMargins(0, 0, 0, 0)
        llay.setSpacing(4)

        # ── Group 1: Settings ──────────────────────────────────────
        llay.addWidget(_group_label("Settings"))
        self.btn_date = QPushButton("Time window")
        self.btn_date.setToolTip("Select a date and hour range to load images from")
        self.btn_date.clicked.connect(self.open_by_date)
        self.btn_open = QPushButton("Camera")
        self.btn_open.setToolTip("Select a camera folder to load images from")
        self.btn_open.clicked.connect(self.open_folder)
        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_refresh.setToolTip("Reload new frames from the same folders without resetting position")
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.clicked.connect(self.refresh_folder)
        self._btn_auto_follow = QPushButton("⇢ Auto-follow")
        self._btn_auto_follow.setCheckable(True)
        self._btn_auto_follow.setEnabled(False)
        self._btn_auto_follow.setToolTip(
            "When enabled, slider jumps to newest image automatically.\n"
            "Disabled automatically when you move the slider.")
        self._btn_auto_follow.toggled.connect(self._on_auto_follow_toggled)
        row = QHBoxLayout(); row.addWidget(self.btn_open); row.addWidget(self.btn_date)
        llay.addLayout(row)
        row_ref_follow = QHBoxLayout()
        row_ref_follow.addWidget(self.btn_refresh)
        row_ref_follow.addWidget(self._btn_auto_follow)
        llay.addLayout(row_ref_follow)
        self.btn_set_a = QPushButton("Set From"); self.btn_set_a.setEnabled(False)
        self.btn_set_a.setToolTip("Set range start (From) to current position")
        self.btn_set_a.clicked.connect(self.set_mark_a)
        self.btn_set_b = QPushButton("Set To"); self.btn_set_b.setEnabled(False)
        self.btn_set_b.setToolTip("Set range end (To) to current position")
        self.btn_set_b.clicked.connect(self.set_mark_b)
        self.btn_clear_marks = QPushButton("Clear"); self.btn_clear_marks.setEnabled(False)
        self.btn_clear_marks.setToolTip("Clear From/To marks")
        self.btn_clear_marks.clicked.connect(self.clear_marks)
        row_marks = QHBoxLayout()
        row_marks.addWidget(self.btn_set_a); row_marks.addWidget(self.btn_set_b); row_marks.addWidget(self.btn_clear_marks)
        llay.addLayout(row_marks)
        llay.addWidget(_hsep())

        # ── Group 2: Save & Marks ──────────────────────────────────
        llay.addWidget(_group_label("Save & Marks"))
        self.btn_save = QPushButton("Save Image"); self.btn_save.setEnabled(False)
        self.btn_save.setToolTip("Save current image to disk")
        self.btn_save.clicked.connect(self.save_current)
        self.btn_save_range = QPushButton("Save Range"); self.btn_save_range.setEnabled(False)
        self.btn_save_range.setToolTip("Save all images between Set From and Set To marks")
        self.btn_save_range.clicked.connect(self.save_range)
        row2a = QHBoxLayout()
        row2a.addWidget(self.btn_save)
        row2a.addWidget(self.btn_save_range)
        llay.addLayout(row2a)
        row2c = QHBoxLayout()
        self.cb_save_overlay = QCheckBox("Save with overlay")
        self.cb_save_overlay.setToolTip("When saving, burn overlays (cross/circle/square) into the image")
        self.cb_save_overlay.setStyleSheet(_CHECKBOX_STYLE)
        row2c.addWidget(self.cb_save_overlay)
        self.save_around_n_sb = QSpinBox()
        self.save_around_n_sb.setRange(0, 10000)
        self.save_around_n_sb.setValue(0)
        self.save_around_n_sb.setFixedWidth(48)
        self.save_around_n_sb.setToolTip("Number of frames before and after current to save (0 = only current)")
        row2c.addWidget(self.save_around_n_sb)
        row2c.addWidget(QLabel("±"))
        llay.addLayout(row2c)
        self.cb_save_metadata_txt = QCheckBox("Save metadata .txt")
        self.cb_save_metadata_txt.setToolTip("Also write a sidecar .txt file with the original image metadata")
        self.cb_save_metadata_txt.setStyleSheet(_CHECKBOX_STYLE)
        llay.addWidget(self.cb_save_metadata_txt)

        llay.addWidget(_hsep())
        llay.addWidget(_group_label("Timestamps"))
        self.btn_save_ts = QPushButton("Save Timestamp")
        self.btn_save_ts.setToolTip("Save current timestamp for cross-camera lookup. You can save more timestamps.")
        self.btn_save_ts.setEnabled(False)
        self.btn_save_ts.clicked.connect(self._save_current_timestamp)
        self.btn_goto_ts = QPushButton("⇢ Go to Saved")
        self.btn_goto_ts.setToolTip("Jump to nearest frame matching a saved timestamp")
        self.btn_goto_ts.setEnabled(False)
        self.btn_goto_ts.clicked.connect(self._goto_saved_timestamp)
        self.btn_clear_ts = QPushButton("✕ Clear")
        self.btn_clear_ts.setToolTip("Clear all saved timestamps")
        self.btn_clear_ts.setEnabled(False)
        self.btn_clear_ts.clicked.connect(self._clear_timestamps)
        row_ts1 = QHBoxLayout()
        row_ts1.addWidget(self.btn_save_ts)
        row_ts1.addWidget(self.btn_goto_ts)
        row_ts1.addWidget(self.btn_clear_ts)
        llay.addLayout(row_ts1)
        self.lbl_ts_status = QLabel("No timestamps saved.")
        self.lbl_ts_status.setWordWrap(True)
        self.lbl_ts_status.setStyleSheet("font-size: 10px; color: #555;")
        llay.addWidget(self.lbl_ts_status)
        llay.addWidget(_hsep())

        # ── Group 3: Playback ──────────────────────────────────────
        llay.addWidget(_group_label("Playback  (speed = % of images / s)"))
        self.btn_prev = QPushButton("◀"); self.btn_prev.setToolTip("Previous image (←)"); self.btn_prev.setEnabled(False)
        self.btn_prev.setFixedWidth(32)
        self.btn_prev.clicked.connect(lambda: self.step_frame(-1))
        self.btn_next = QPushButton("▶"); self.btn_next.setToolTip("Next image (→)"); self.btn_next.setEnabled(False)
        self.btn_next.setFixedWidth(32)
        self.btn_next.clicked.connect(lambda: self.step_frame(+1))
        self.btn_play = QPushButton("Play"); self.btn_play.setEnabled(False)
        self.btn_play.setToolTip("Start playback")
        self.btn_play.clicked.connect(self.play)
        self.btn_stop = QPushButton("Stop"); self.btn_stop.setEnabled(False)
        self.btn_stop.setToolTip("Stop playback")
        self.btn_stop.clicked.connect(self.stop)
        row3a = QHBoxLayout()
        row3a.addWidget(self.btn_prev); row3a.addWidget(self.btn_next)
        row3a.addWidget(self.btn_play); row3a.addWidget(self.btn_stop)
        llay.addLayout(row3a)

        self.speed_cb = PopupBelowComboBox()
        self.speed_cb.setToolTip(
            "Speed = % of images per second.")
        self.speed_cb.setMaxVisibleItems(12)
        for label, val in [
            ("0.10 %/s", 0.10),
            ("0.25 %/s",  0.25),
            ("0.5 %/s",  0.5),
            ("1 %/s",  1.0),
            ("2 %/s",   2.0),
            ("5 %/s",   5.0),
            ("10 %/s",   10.0),
            ("15 %/s",   15.0),
            ("20 %/s",   20.0)
        ]:
            self.speed_cb.addItem(label, val)
        self.speed_cb.setCurrentIndex(3)  # default 1 %/s
        self.speed_cb.setStyleSheet(
            "QComboBox { padding: 3px 6px; background: #fff; border: 1px solid #ccc; border-radius: 4px; }"
            "QComboBox QAbstractItemView { background: #fff; }")
        row3b = QHBoxLayout(); row3b.addWidget(QLabel("Speed:")); row3b.addWidget(self.speed_cb, 1)
        llay.addLayout(row3b)
        llay.addWidget(_hsep())

# ── Group 4: Overlays + Draw (grid 3 sloupce) ─────────────
        llay.addWidget(_group_label("Overlays & Draw"))

        self.cb_cross  = QCheckBox("Cross");  self.cb_cross.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_cross.setToolTip("Show cross overlay on image")
        self.cb_circle = QCheckBox("Circle"); self.cb_circle.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_circle.setToolTip("Show circle overlay on image")
        self.cb_square = QCheckBox("Square"); self.cb_square.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_square.setToolTip("Show square overlay on image")
        self.cb_bright = QCheckBox("Auto brightness"); self.cb_bright.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_bright.setToolTip("Auto-stretch contrast for better visibility")

        self.cb_cross.stateChanged.connect(self._on_overlay_changed)
        self.cb_circle.stateChanged.connect(self._on_overlay_changed)
        self.cb_square.stateChanged.connect(self._on_overlay_changed)
        self.cb_bright.stateChanged.connect(self._on_brightness_changed)

        self.btn_draw_cross  = QPushButton("✚ Draw")
        self.btn_draw_circle = QPushButton("◯ Draw")
        self.btn_draw_square = QPushButton("◻ Draw")
        self.btn_draw_cross.setToolTip("Click on image to place cross")
        self.btn_draw_circle.setToolTip("Drag to draw ellipse, Shift = circle")
        self.btn_draw_square.setToolTip("Drag to draw rectangle, Shift = square")
        self.btn_draw_circle.clicked.connect(lambda: self._toggle_draw_mode("circle"))
        self.btn_draw_square.clicked.connect(lambda: self._toggle_draw_mode("square"))
        self.btn_draw_cross.clicked.connect(lambda: self._toggle_draw_mode("cross"))

        self.btn_cal_circle = QPushButton("◯ Cal"); self.btn_cal_circle.setEnabled(False)
        self.btn_cal_circle.setToolTip("Auto-detect circle in current frame")
        self.btn_cal_circle.clicked.connect(self.calibrate_circle)
        self.btn_cal_square = QPushButton("◻ Cal"); self.btn_cal_square.setEnabled(False)
        self.btn_cal_square.setToolTip("Auto-detect rectangle in current frame")
        self.btn_cal_square.clicked.connect(self.calibrate_square)

        # prázdný placeholder pro cross (cal neexistuje)
        _lbl_empty = QLabel("")

        self.btn_cal_cross = QPushButton("✚ Cal"); self.btn_cal_cross.setEnabled(False)
        self.btn_cal_cross.setToolTip("Auto-detect center of brightness (centroid)")
        self.btn_cal_cross.clicked.connect(self.calibrate_cross)

        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(3)
        #              col 0          col 1              col 2
        grid.addWidget(self.cb_cross,        0, 0)
        grid.addWidget(self.cb_circle,       0, 1)
        grid.addWidget(self.cb_square,       0, 2)
        grid.addWidget(self.btn_draw_cross,  1, 0)
        grid.addWidget(self.btn_draw_circle, 1, 1)
        grid.addWidget(self.btn_draw_square, 1, 2)
        grid.addWidget(_lbl_empty,           2, 0)
        grid.addWidget(self.btn_cal_circle,  2, 1)
        grid.addWidget(self.btn_cal_square,  2, 2)
        grid.addWidget(self.btn_cal_cross,   2, 0)
        llay.addLayout(grid)

        # ── Overlay settings ──────────────────────────────────────
        overlay_settings_row = QHBoxLayout()
        overlay_settings_row.setSpacing(4)
        btn_overlay_settings = QPushButton("⚙ Overlay settings")
        btn_overlay_settings.setToolTip("Set color, thickness and size of overlays")
        btn_overlay_settings.clicked.connect(self._open_overlay_settings)
        overlay_settings_row.addWidget(btn_overlay_settings, 1)
        btn_remove_all_overlays = QPushButton("✕ Remove selected")
        btn_remove_all_overlays.setToolTip("Remove all overlays from selected camera(s)")
        btn_remove_all_overlays.clicked.connect(self._remove_all_overlays)
        overlay_settings_row.addWidget(btn_remove_all_overlays, 1)
        llay.addLayout(overlay_settings_row)

        row_bright_grad = QHBoxLayout()
        row_bright_grad.addWidget(self.cb_bright)
        # brightness slider pod auto brightness
        self.gradient_cb = PopupBelowComboBox()
        self.gradient_cb.setToolTip("Color gradient for image display")
        for name in GRADIENT_NAMES:
            self.gradient_cb.addItem(name)
        self.gradient_cb.setCurrentIndex(2)  # default: Gradient (0=Default, 1=Grayscale, 2+=palettes)
        self.gradient_cb.setStyleSheet(
            "QComboBox { padding: 3px 6px; background: #fff; border: 1px solid #ccc; border-radius: 4px; }"
            "QComboBox QAbstractItemView { background: #fff; }")
        self.gradient_cb.currentIndexChanged.connect(self._on_gradient_changed)
        row_bright_grad.addWidget(self.gradient_cb, 1)
        llay.addLayout(row_bright_grad)
        row_sub = QHBoxLayout()
        self.cb_subtract = QCheckBox("Subtraction")
        self.cb_subtract.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_subtract.setToolTip("Show absolute difference from reference frame")
        self.cb_subtract.stateChanged.connect(self._on_subtract_changed)
        row_sub.addWidget(self.cb_subtract)
        self.btn_set_ref = QPushButton("Set ref")
        self.btn_set_ref.setFixedWidth(65)
        self.btn_set_ref.setEnabled(False)
        self.btn_set_ref.setToolTip("Set current frame as subtraction reference")
        self.btn_set_ref.clicked.connect(self._set_reference_frame)
        row_sub.addWidget(self.btn_set_ref)
        self.lbl_ref_status = QLabel("No ref.")
        self.lbl_ref_status.setStyleSheet("font-size: 9px; color: #555;")
        self.lbl_ref_status.setWordWrap(True)
        row_sub.addWidget(self.lbl_ref_status, 1)
        llay.addLayout(row_sub)
        row_sub_thr = QHBoxLayout()
        row_sub_thr.addWidget(QLabel("Diff threshold:"))
        self.sub_threshold_sb = QSpinBox()
        self.sub_threshold_sb.setRange(0, 255)
        self.sub_threshold_sb.setValue(0)
        self.sub_threshold_sb.setFixedWidth(55)
        self.sub_threshold_sb.setToolTip(
            "Pixels with |current − reference| below this value are shown as black.\n"
            "0 = show all differences (default).\n"
            "Useful for ignoring noise and tiny fluctuations.")
        self.sub_threshold_sb.valueChanged.connect(self._on_subtract_changed)
        row_sub_thr.addWidget(self.sub_threshold_sb)
        row_sub_thr.addStretch(1)
        llay.addLayout(row_sub_thr)
        row_bright_slider = QHBoxLayout()
        row_bright_slider.addWidget(QLabel("Brightness:"))
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(-255, 255)
        self.brightness_slider.setValue(0)
        self.brightness_slider.setToolTip("Manual brightness offset (-255 to +255)")
        self.brightness_slider.valueChanged.connect(self._on_brightness_slider_changed)
        row_bright_slider.addWidget(self.brightness_slider, 1)
        self.btn_brightness_reset = QPushButton("↺")
        self.btn_brightness_reset.setFixedWidth(28)
        self.btn_brightness_reset.setToolTip("Reset brightness")
        self.btn_brightness_reset.clicked.connect(self._reset_brightness_slider)
        row_bright_slider.addWidget(self.btn_brightness_reset)
        llay.addLayout(row_bright_slider)
        llay.addWidget(_hsep())

        # _online_dot and _online_lbl kept as non-visible widgets for backward compat
        self._online_dot = QLabel("●")
        self._online_dot.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_dot.hide()
        self._online_lbl = QLabel("Inactive")
        self._online_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self._online_lbl.hide()

        # ── Group: Multi-Camera Layout ─────────────────────────────
        llay.addWidget(_group_label("Camera Labels Settings"))

        row_cam_font = QHBoxLayout()
        row_cam_font.addWidget(QLabel("Label size:"))
        self._cam_label_size_sb = QSpinBox()
        self._cam_label_size_sb.setRange(8, 32)
        self._cam_label_size_sb.setValue(20)
        self._cam_label_size_sb.setSuffix(" px")
        self._cam_label_size_sb.valueChanged.connect(self._on_cam_label_size_changed)
        row_cam_font.addWidget(self._cam_label_size_sb)
        llay.addLayout(row_cam_font)
        llay.addWidget(_hsep())

        # ── Group: Image Analysis ─────────────────────────────────────
        llay.addWidget(_group_label("Image Analysis"))

        # ── Subgroup: Pointing Analysis ───────────────────────────────
        llay.addWidget(_group_label("  Pointing Analysis"))
        row_thr_mag = QHBoxLayout()
        row_thr_mag.addWidget(QLabel("Thr:"))
        self.pointing_threshold_sb = QSpinBox()
        self.pointing_threshold_sb.setRange(0, 65535)
        self.pointing_threshold_sb.setValue(50)
        self.pointing_threshold_sb.setFixedWidth(55)
        self.pointing_threshold_sb.setToolTip("Threshold — pixels below this value (0–255) are ignored")
        row_thr_mag.addWidget(self.pointing_threshold_sb)
        row_thr_mag.addSpacing(6)
        row_thr_mag.addWidget(QLabel("M:"))
        self.pointing_m_sb = QDoubleSpinBox()
        self.pointing_m_sb.setRange(0.001, 1000.0)
        self.pointing_m_sb.setValue(1.0)
        self.pointing_m_sb.setDecimals(3)
        self.pointing_m_sb.setFixedWidth(65)
        self.pointing_m_sb.setToolTip("Magnification — optical magnification factor of the camera setup")
        row_thr_mag.addWidget(self.pointing_m_sb)
        row_thr_mag.addStretch(1)
        llay.addLayout(row_thr_mag)

        self.btn_pointing = QPushButton("▶ Run Analysis")
        self.btn_pointing.setToolTip("Run pointing stability analysis on current images set by timestamps (Set From and Set To)")
        self.btn_pointing.setEnabled(False)
        self.btn_pointing.clicked.connect(self.run_pointing_analysis)
        self.btn_pointing_live = QPushButton("▶ Replay")
        self.btn_pointing_live.setToolTip(
            "Replay: step through pointing analysis results frame by frame.\n"
            "Shows each image + highlights its point on the graph.")
        self.btn_pointing_live.setCheckable(True)
        self.btn_pointing_live.setEnabled(False)
        self.btn_pointing_live.toggled.connect(self._on_pointing_live_toggled)
        self.btn_pointing_cancel = QPushButton("Cancel")
        self.btn_pointing_cancel.setToolTip("Cancel running analysis")
        self.btn_pointing_cancel.setVisible(False)
        self.btn_pointing_cancel.clicked.connect(self._cancel_pointing)
        row_pa = QHBoxLayout()
        row_pa.addWidget(self.btn_pointing)
        row_pa.addWidget(self.btn_pointing_live)
        row_pa.addWidget(self.btn_pointing_cancel)
        llay.addLayout(row_pa)

        # Replay speed control
        row_replay_speed = QHBoxLayout()
        row_replay_speed.addWidget(QLabel("Replay speed:"))
        self._pointing_replay_fps_sb = QSpinBox()
        self._pointing_replay_fps_sb.setRange(1, 100)
        self._pointing_replay_fps_sb.setValue(10)
        self._pointing_replay_fps_sb.setSuffix(" %")
        self._pointing_replay_fps_sb.setFixedWidth(70)
        self._pointing_replay_fps_sb.setToolTip(
            "Replay speed as % of max (100% = 200 fps, 10% = 20 fps, 1% = 2 fps)")
        row_replay_speed.addWidget(self._pointing_replay_fps_sb)
        row_replay_speed.addStretch(1)
        llay.addLayout(row_replay_speed)

        self.btn_pointing_save = QPushButton("💾 Save Plot")
        self.btn_pointing_save.setToolTip("Save pointing plot as PNG or PDF")
        self.btn_pointing_save.setEnabled(False)
        self.btn_pointing_save.clicked.connect(self._save_pointing_plot)
        self.btn_pointing_path = QPushButton("〰 Show Path")
        self.btn_pointing_path.setToolTip("Show/hide beam path trajectory colored by time")
        self.btn_pointing_path.setEnabled(False)
        self.btn_pointing_path.clicked.connect(self._toggle_pointing_path)
        row_pa2 = QHBoxLayout()
        row_pa2.addWidget(self.btn_pointing_save)
        row_pa2.addWidget(self.btn_pointing_path)
        llay.addLayout(row_pa2)
        self.btn_pointing_select = QPushButton("◻ Select & Delete")
        self.btn_pointing_select.setToolTip("Drag a rectangle on the scatter plot to delete those points")
        self.btn_pointing_select.setEnabled(False)
        self.btn_pointing_select.setCheckable(True)
        self.btn_pointing_select.clicked.connect(self._toggle_pointing_select)
        self.btn_pointing_restore = QPushButton("↺ Restore All")
        self.btn_pointing_restore.setToolTip("Restore all deleted points")
        self.btn_pointing_restore.setEnabled(False)
        self.btn_pointing_restore.clicked.connect(self._restore_pointing_points)
        row_pa3 = QHBoxLayout()
        row_pa3.addWidget(self.btn_pointing_select)
        row_pa3.addWidget(self.btn_pointing_restore)
        llay.addLayout(row_pa3)
        self.btn_pointing_close = QPushButton("✕ Close graph")
        self.btn_pointing_close.setEnabled(False)
        self.btn_pointing_close.setToolTip("Hide the pointing analysis graph")
        self.btn_pointing_close.clicked.connect(self._close_pointing_panel)
        llay.addWidget(self.btn_pointing_close)

        self.lbl_pointing_status = QLabel("")
        self.lbl_pointing_status.setWordWrap(True)
        self.lbl_pointing_status.setStyleSheet("font-size: 10px; color: #555;")
        llay.addWidget(self.lbl_pointing_status)

        llay.addWidget(_hsep())

        # ── Subgroup: Spatial Contrast ────────────────────────────────
        llay.addWidget(_group_label("  Spatial Contrast"))

        # Camera selector (visible only in multi-cam mode)
        sc_cam_row = QHBoxLayout()
        sc_cam_row.addWidget(QLabel("Camera:"))
        self._sc_cam_combo = QComboBox()
        self._sc_cam_combo.setToolTip("Select which camera to measure")
        sc_cam_row.addWidget(self._sc_cam_combo, 1)
        sc_cam_row_widget = QWidget()
        sc_cam_row_widget.setLayout(sc_cam_row)
        sc_cam_row_widget.setVisible(False)
        self._sc_cam_row_widget = sc_cam_row_widget
        llay.addWidget(sc_cam_row_widget)

        # Threshold row
        sc_thr_row = QHBoxLayout()
        sc_thr_row.addWidget(QLabel("Threshold:"))
        self._sc_threshold_sb = QSpinBox()
        self._sc_threshold_sb.setRange(0, 65535)
        self._sc_threshold_sb.setValue(2000)
        self._sc_threshold_sb.setSuffix("")
        self._sc_threshold_sb.setFixedWidth(80)
        self._sc_threshold_sb.setToolTip(
            "Pixels with raw intensity ≤ threshold are treated as background.\n"
            "Raise to include only the bright beam core; lower to include the full beam halo.\n"
            "16-bit images: range 0–65535. 8-bit images: range 0–255.")
        self._sc_threshold_sb.valueChanged.connect(self._on_sc_threshold_changed)
        sc_thr_row.addWidget(self._sc_threshold_sb)
        self._btn_sc_auto_thr = QPushButton("Auto")
        self._btn_sc_auto_thr.setFixedWidth(42)
        self._btn_sc_auto_thr.setToolTip(
            "Automatically set threshold using Otsu's method\n"
            "(separates background from beam)")
        self._btn_sc_auto_thr.setEnabled(False)
        self._btn_sc_auto_thr.clicked.connect(self._run_sc_auto_threshold)
        sc_thr_row.addWidget(self._btn_sc_auto_thr)
        self._btn_sc_hist = QPushButton("Manual")
        self._btn_sc_hist.setFixedWidth(58)
        self._btn_sc_hist.setToolTip(
            "Open histogram — click or drag to set threshold manually")
        self._btn_sc_hist.setEnabled(False)
        self._btn_sc_hist.clicked.connect(self._open_sc_histogram)
        sc_thr_row.addWidget(self._btn_sc_hist)
        sc_thr_row.addStretch(1)
        llay.addLayout(sc_thr_row)

        # Measure + Draw exclusions buttons
        sc_btn_row = QHBoxLayout()
        self._btn_sc_measure = QPushButton("▶ Measure")
        self._btn_sc_measure.setToolTip("Compute Spatial Contrast for the current frame")
        self._btn_sc_measure.clicked.connect(self._run_spatial_contrast)
        sc_btn_row.addWidget(self._btn_sc_measure, 2)
        self._btn_sc_draw = QPushButton("✏ Exclude")
        self._btn_sc_draw.setToolTip(
            "Open exclusion editor — draw regions to exclude from the measurement")
        self._btn_sc_draw.clicked.connect(self._open_sc_exclusion_editor)
        sc_btn_row.addWidget(self._btn_sc_draw, 1)
        llay.addLayout(sc_btn_row)

        # "Show top intensity pixels: N" — circle top-N highest-intensity pixels on the image
        sc_topn_row = QHBoxLayout()
        sc_topn_row.addWidget(QLabel("Show top intensity:"))
        self._sc_topn_sb = QSpinBox()
        self._sc_topn_sb.setRange(0, 9999)
        self._sc_topn_sb.setValue(0)
        self._sc_topn_sb.setFixedWidth(70)
        self._sc_topn_sb.setToolTip(
            "Circle the top-N highest-intensity pixels on the image after measuring.\n"
            "0 = disabled.")
        self._sc_topn_sb.valueChanged.connect(self._on_sc_topn_changed)
        sc_topn_row.addWidget(self._sc_topn_sb)
        sc_topn_row.addWidget(QLabel("px"))
        sc_topn_row.addStretch(1)
        llay.addLayout(sc_topn_row)

        # Marker appearance: radius + thickness
        sc_marker_row = QHBoxLayout()
        sc_marker_row.addWidget(QLabel("Marker radius:"))
        self._sc_marker_r_sb = QSpinBox()
        self._sc_marker_r_sb.setRange(1, 100)
        self._sc_marker_r_sb.setValue(5)
        self._sc_marker_r_sb.setFixedWidth(50)
        self._sc_marker_r_sb.setToolTip("Circle radius in pixels for top-intensity markers")
        self._sc_marker_r_sb.valueChanged.connect(self._on_sc_marker_style_changed)
        sc_marker_row.addWidget(self._sc_marker_r_sb)
        sc_marker_row.addWidget(QLabel("Thickness:"))
        self._sc_marker_thick_sb = QSpinBox()
        self._sc_marker_thick_sb.setRange(1, 20)
        self._sc_marker_thick_sb.setValue(2)
        self._sc_marker_thick_sb.setFixedWidth(45)
        self._sc_marker_thick_sb.setToolTip("Line thickness for top-intensity markers")
        self._sc_marker_thick_sb.valueChanged.connect(self._on_sc_marker_style_changed)
        sc_marker_row.addWidget(self._sc_marker_thick_sb)
        sc_marker_row.addStretch(1)
        llay.addLayout(sc_marker_row)

        self._sc_topn_points: "list[tuple[int,int]] | None" = None  # (x,y) pixel coords in full image

        self._sc_set_enabled(False)   # both buttons exist now — safe to call
        # Exclusion mask is stored per image path so it resets on image change
        self._sc_exclusion_mask: "np.ndarray | None" = None  # bool array, True = excluded
        self._sc_exclusion_path: "Path | None" = None        # path the mask belongs to

        # Results — compact 2-per-row layout
        _sc_grid = QGridLayout()
        _sc_grid.setSpacing(3)
        _sc_grid.setContentsMargins(0, 2, 0, 2)
        _sc_val_style = (
            "font-size: 11px; font-family: monospace; color: #111; "
            "background: #f5f5f5; border: 1px solid #ccc; "
            "border-radius: 2px; padding: 1px 4px;")
        _sc_lbl_style = "font-size: 11px; color: #444;"

        def _sc_cell(label: str, grid_row: int, grid_col: int) -> "_SCValueLabel":
            lbl = QLabel(label)
            lbl.setStyleSheet(_sc_lbl_style)
            val = _SCValueLabel("—")
            val.setStyleSheet(_sc_val_style)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val.setToolTip("Double-click to copy")
            _sc_grid.addWidget(lbl, grid_row, grid_col * 2)
            _sc_grid.addWidget(val, grid_row, grid_col * 2 + 1)
            _sc_grid.setColumnStretch(grid_col * 2 + 1, 1)
            return val

        # Row 0: Min  |  Max
        self._sc_val_min  = _sc_cell("Min:",  0, 0)
        self._sc_val_max  = _sc_cell("Max:",  0, 1)
        # Row 1: Mean  |  Pixel count
        self._sc_val_mean = _sc_cell("Mean:", 1, 0)
        self._sc_val_beam = _sc_cell("Pixels:", 1, 1)
        # Row 2: SC spanning full width
        sc_lbl_full = QLabel("SC (Max/Mean):")
        sc_lbl_full.setStyleSheet(_sc_lbl_style)
        self._sc_val_sc = _SCValueLabel("—")
        self._sc_val_sc.setStyleSheet(
            "font-size: 12px; font-weight: 700; font-family: monospace; color: #111; "
            "background: #e8f0fe; border: 1px solid #90a8e0; "
            "border-radius: 2px; padding: 1px 4px;")
        self._sc_val_sc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._sc_val_sc.setToolTip("Double-click to copy")
        _sc_grid.addWidget(sc_lbl_full,     2, 0)
        _sc_grid.addWidget(self._sc_val_sc, 2, 1, 1, 3)
        llay.addLayout(_sc_grid)

        self._sc_cam_lbl = QLabel("")
        self._sc_cam_lbl.setStyleSheet("font-size: 10px; color: #555;")
        llay.addWidget(self._sc_cam_lbl)

        self._sc_status_lbl = QLabel("")
        self._sc_status_lbl.setWordWrap(True)
        self._sc_status_lbl.setStyleSheet("font-size: 10px; color: #c00;")
        llay.addWidget(self._sc_status_lbl)

        # Preview label (shows beam mask overlay, hidden until first measurement)
        self._sc_preview_lbl = _SCPreviewLabel()
        self._sc_preview_lbl.hide()
        llay.addWidget(self._sc_preview_lbl)
        self._sc_preview_pixmap: "QPixmap | None" = None
        self._sc_task_running = False
        self._sc_pending      = False
        self._sc_topn_points: "list[tuple[int,int]]" = []
        self._sc_topn_img_shape: "tuple[int,int] | None" = None

        llay.addWidget(_hsep())

        # ── Info labels (definice — zobrazí se v ukotvené sekci nahoře) ───
        info_style = "font-size: 11px; color: #222; padding: 1px 0;"
        self.lbl_index          = QLabel("0 / 0")
        self.lbl_selected_range = QLabel("Range: —")
        self.lbl_filename       = QLabel("Filename: —")
        self.lbl_axis_time      = QLabel("Axis: —")
        self.lbl_prague_time    = QLabel("Prague Time: —")
        self.lbl_scan_progress  = QLabel("")
        for lbl in [self.lbl_index, self.lbl_selected_range, self.lbl_filename,
                    self.lbl_axis_time, self.lbl_prague_time, self.lbl_scan_progress]:
            lbl.setWordWrap(True)
            lbl.setStyleSheet(info_style)
        self.lbl_selected_range.setStyleSheet("font-size: 11px; font-weight: 700; color: #333; padding: 1px 0;")
        self.prog = QProgressBar(); self.prog.setVisible(False)
        self.prog.setRange(0, 0); self.prog.setTextVisible(False)
        self.btn_cancel_scan = QPushButton("Cancel scan"); self.btn_cancel_scan.setVisible(False)
        self.btn_cancel_scan.clicked.connect(self.cancel_scan)

        llay.addStretch(1)

        # ═══════════════════════ RIGHT PANEL ══════════════════════
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(4)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setEnabled(False)
        self.slider.setMinimum(0); self.slider.setMaximum(SLIDER_MAX)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)

        self.tickbar  = TickBar(self)

        rlay.addWidget(self.slider)

        # Per-camera sliders (hidden until multi-cam mode is active)
        self._per_cam_container = QWidget()
        self._per_cam_container.setVisible(False)
        _pcl = QVBoxLayout(self._per_cam_container)
        _pcl.setContentsMargins(0, 2, 0, 0)
        _pcl.setSpacing(1)
        self._per_cam_layout = _pcl
        self._per_cam_rows: list[_CamSliderRow] = []
        self._per_cam_master_idx: int = 0      # which camera is master
        self._per_cam_scrubbing_cam: int = -1  # which cam is being dragged (-1 = none)
        rlay.addWidget(self._per_cam_container)

        # Tickbar below sliders — cursor line ends here, at the bottom
        rlay.addWidget(self.tickbar)

        # Outer row container — script background color, holds camera + pointing panel
        _cam_row_widget = QWidget()
        _cam_row_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _cam_row_widget.setStyleSheet("background: #f3f3f3;")
        _cam_row_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._cam_row_widget = _cam_row_widget

        self._img_pointing_row = QHBoxLayout(_cam_row_widget)
        self._img_pointing_row.setSpacing(0)
        self._img_pointing_row.setContentsMargins(0, 0, 0, 0)

        # Single-cam: script-colored wrapper, img_view fills it.
        # Single-cam: image + label bar BELOW (not overlapping) the image.
        _single_wrapper = QWidget()
        _single_wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _single_wrapper.setStyleSheet("background: #f3f3f3;")
        _single_wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        _swl = QVBoxLayout(_single_wrapper)
        _swl.setContentsMargins(0, 0, 0, 0)
        _swl.setSpacing(0)

        self.img_view = ImageView(_single_wrapper)
        self.img_view.bg_color = QColor("#f3f3f3")
        self.img_view.cam_label_font_px = self._cam_label_size_sb.value()
        self.img_view.sc_topn_marker_radius = self._sc_marker_r_sb.value()
        self.img_view.sc_topn_marker_thick  = self._sc_marker_thick_sb.value()
        # Labels drawn below image (reserved strip), not overlapping the image
        self.img_view.cam_label_use_overlay = False
        _swl.addWidget(self.img_view, 1)

        self._single_cam_container = _single_wrapper
        self._img_pointing_row.addWidget(_single_wrapper, 1)
        self._single_wrapper = _single_wrapper

        # Multi-cam grid (skrytý dokud není >1 kamera)
        self._multi_grid = MultiCameraGrid(self)
        self._multi_grid.setVisible(False)
        self._multi_grid.camera_selected.connect(self._on_multicam_selected)
        self._img_pointing_row.addWidget(self._multi_grid, 1)

        self.pointing_panel = PointingPanel(self)
        self.pointing_panel.point_clicked.connect(self._on_pointing_point_clicked)
        self.pointing_panel.region_deleted.connect(self._on_pointing_region_deleted)
        self.pointing_panel.setVisible(False)
        self._img_pointing_row.addWidget(self.pointing_panel, 1)
        rlay.addWidget(_cam_row_widget, 1)

        left_scroll.setWidget(left)

        # Info panel ukotvený nad scroll area
        info_panel = QWidget()
        info_panel.setFixedWidth(275)
        info_panel.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        ilay = QVBoxLayout(info_panel)
        ilay.setContentsMargins(0, 4, 0, 4)
        ilay.setSpacing(2)
        info_title_row = QHBoxLayout()
        info_title_row.addWidget(_group_label("Info"))
        info_title_row.addStretch(1)
        self._online_dot_top = QLabel("●")
        self._online_dot_top.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_dot_top.setToolTip("Online mode indicator")
        info_title_row.addWidget(self._online_dot_top)
        ilay.addLayout(info_title_row)
        for lbl in [self.lbl_index, self.lbl_selected_range, self.lbl_filename,
                    self.lbl_axis_time, self.lbl_prague_time, self.lbl_scan_progress]:
            ilay.addWidget(lbl)
        ilay.addWidget(self.prog)
        ilay.addWidget(self.btn_cancel_scan)

        left_col = QWidget()
        left_col.setFixedWidth(275)
        lcol_lay = QVBoxLayout(left_col)
        lcol_lay.setContentsMargins(0, 0, 0, 0)
        lcol_lay.setSpacing(0)
        lcol_lay.addWidget(info_panel)
        lcol_lay.addWidget(left_scroll, 1)
        self._left_col = left_col
        self._right_panel = right

        root.addWidget(left_col)
        root.addWidget(right, 1)

        self._watcher_mode = False

        self.scrub_timer = QTimer(self)
        self.scrub_timer.setInterval(SCRUB_INTERVAL_MS)
        self.scrub_timer.timeout.connect(self._apply_scrub)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_sc_preview_pixmap'):
            self._update_sc_preview()

    # ================================================================ OPEN
    def _set_busy(self, busy: bool):
        for btn in [self.btn_open, self.btn_date, self.btn_refresh,
                    self.btn_set_a, self.btn_set_b, self.btn_clear_marks,
                    self.btn_save, self.btn_save_range, self.btn_play,
                    self.btn_prev, self.btn_next, self.btn_pointing,
                    self.slider, self.gradient_cb, self.speed_cb,
                    self.cb_bright, self.cb_subtract]:
            btn.setEnabled(not busy)

    def _toggle_draw_mode(self, mode: str):
        iv = self._active_img_view()
        iv.set_draw_mode("" if iv._draw_mode == mode else mode)
        if iv._draw_mode == "cross"  and not self.cb_cross.isChecked():
            self.cb_cross.setChecked(True)
        if iv._draw_mode == "circle" and not self.cb_circle.isChecked():
            self.cb_circle.setChecked(True)
        if iv._draw_mode == "square" and not self.cb_square.isChecked():
            self.cb_square.setChecked(True)
        self._refresh_draw_btns()

    def _refresh_draw_btns(self):
        iv = self._active_img_view()
        m = iv._draw_mode
        on  = "QPushButton { background: #2d7dff; color: #fff; font-weight: 700; border-radius: 4px; }"
        self.btn_draw_cross.setStyleSheet (on if m == "cross"  else "")
        self.btn_draw_circle.setStyleSheet(on if m == "circle" else "")
        self.btn_draw_square.setStyleSheet(on if m == "square" else "")

    def _remove_all_overlays(self):
        def _clear_iv(iv):
            iv.show_cross  = False; iv.cross_pos_norm  = None
            iv.show_circle = False; iv.circle_center_norm = None
            iv.circle_r_norm = None; iv.circle_rx_norm = None; iv.circle_ry_norm = None
            iv.show_square = False; iv.square_rect_norm = None
            iv.set_draw_mode("")
            iv.update()

        if self._is_multi_cam():
            for idx in self._multi_grid.selected_cam_indices():
                iv = self._multi_grid.get_img_view(idx)
                if iv is not None:
                    _clear_iv(iv)
        else:
            _clear_iv(self.img_view)

        self.cb_cross.setChecked(False)
        self.cb_circle.setChecked(False)
        self.cb_square.setChecked(False)
        self._refresh_draw_btns()

    def _open_overlay_settings(self):
        from PySide6.QtWidgets import (QDialog, QFormLayout, QSpinBox,
                                        QPushButton, QDialogButtonBox, QColorDialog)
        from PySide6.QtGui import QColor

        dlg = QDialog(self)
        dlg.setWindowTitle("Overlay Settings")
        dlg.setMinimumWidth(320)
        lay = QFormLayout(dlg)

        def make_color_btn(color: QColor) -> QPushButton:
            btn = QPushButton()
            btn.setFixedWidth(60)
            btn._color = QColor(color)
            btn.setStyleSheet(f"background: {color.name()}; border: 1px solid #888;")
            def pick():
                c = QColorDialog.getColor(btn._color, dlg, "Pick color",
                                          QColorDialog.ColorDialogOption.ShowAlphaChannel)
                if c.isValid():
                    btn._color = c
                    btn.setStyleSheet(f"background: {c.name()}; border: 1px solid #888;")
            btn.clicked.connect(pick)
            return btn

        # Cross
        cross_color_btn = make_color_btn(self._overlay_cross_color)
        cross_thick_sb = QSpinBox(); cross_thick_sb.setRange(1, 20)
        cross_thick_sb.setValue(self._overlay_cross_thick)
        cross_size_sb = QSpinBox(); cross_size_sb.setRange(4, 200)
        cross_size_sb.setValue(self._overlay_cross_size)

        # Zjisti rozměry aktuálního obrázku pro info label
        _img_w = _img_h = None
        if self.img_view._pix and not self.img_view._pix.isNull():
            _img_w = self.img_view._pix.width()
            _img_h = self.img_view._pix.height()

        lay.addRow("Cross color:", cross_color_btn)
        lay.addRow("Cross thickness:", cross_thick_sb)
        lay.addRow("Cross size (px):", cross_size_sb)
        if _img_w and _img_h:
            _cross_info = QLabel(
                f"Image: {_img_w}×{_img_h} px  |  "
                f"thickness max ~{_img_w // 100} px  |  "
                f"size max ~{min(_img_w, _img_h) // 2} px"
            )
            _cross_info.setStyleSheet("font-size: 10px; color: #666;")
            lay.addRow("", _cross_info)

        # Circle
        circle_color_btn = make_color_btn(self._overlay_circle_color)
        circle_thick_sb = QSpinBox(); circle_thick_sb.setRange(1, 20)
        circle_thick_sb.setValue(self._overlay_circle_thick)
        lay.addRow("Circle color:", circle_color_btn)
        lay.addRow("Circle thickness:", circle_thick_sb)

        # Square
        square_color_btn = make_color_btn(self._overlay_square_color)
        square_thick_sb = QSpinBox(); square_thick_sb.setRange(1, 20)
        square_thick_sb.setValue(self._overlay_square_thick)
        lay.addRow("Square color:", square_color_btn)
        lay.addRow("Square thickness:", square_thick_sb)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addRow(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._overlay_cross_color  = cross_color_btn._color
            self._overlay_cross_thick  = cross_thick_sb.value()
            self._overlay_cross_size   = cross_size_sb.value()
            self._overlay_circle_color = circle_color_btn._color
            self._overlay_circle_thick = circle_thick_sb.value()
            self._overlay_square_color = square_color_btn._color
            self._overlay_square_thick = square_thick_sb.value()
            # Propaguj do ImageView
            self._apply_overlay_settings()
            self.img_view.update()

    def _apply_overlay_settings(self):
        self.img_view.cross_size      = self._overlay_cross_size
        self.img_view.cross_thickness = self._overlay_cross_thick
        self.img_view.cross_color     = self._overlay_cross_color
        self.img_view.circle_color    = self._overlay_circle_color
        self.img_view.circle_thick    = self._overlay_circle_thick
        self.img_view.square_color    = self._overlay_square_color
        self.img_view.square_thick    = self._overlay_square_thick

    # ================================================================ MULTI-CAM HELPERS
    def _is_multi_cam(self) -> bool:
        return len(self._cam_names) > 1

    def _on_multicam_selected(self, idx: int):
        """Kamera v gridu byla vybrána kliknutím."""
        # Clear draw mode on ALL cameras except the newly selected one,
        # so draw mode never silently stays active on a background camera.
        for i, cv in enumerate(self._multi_grid._cam_views):
            if i != idx:
                cv.img_view.set_draw_mode("")

        # Update SC camera selector and clear stale preview from previous camera
        cam_name = self._cam_names[idx] if idx < len(self._cam_names) else ""
        if hasattr(self, '_sc_cam_lbl'):
            self._sc_cam_lbl.setText(f"Camera: {cam_name}" if cam_name else "")
        if hasattr(self, '_sc_cam_combo') and self._sc_cam_combo.count() > idx:
            self._sc_cam_combo.blockSignals(True)
            self._sc_cam_combo.setCurrentIndex(idx)
            self._sc_cam_combo.blockSignals(False)
        if hasattr(self, '_sc_preview_lbl'):
            self._sc_preview_lbl.hide()
            self._sc_preview_pixmap = None
        if hasattr(self, '_sc_exclusion_mask'):
            self._sc_exclusion_mask = None
            self._sc_exclusion_path = None

        if idx < len(self._cam_ref_images) and self._cam_ref_images[idx] is not None:
            cam_name = self._cam_names[idx] if idx < len(self._cam_names) else f"cam {idx}"
            self.lbl_ref_status.setText(f"Ref set: {cam_name}")
        else:
            self.lbl_ref_status.setText("No ref.")
        # Update draw mode checkboxes/buttons to reflect selected camera's state
        iv = self._multi_grid.selected_img_view()
        if iv is not None:
            self.cb_cross.blockSignals(True)
            self.cb_circle.blockSignals(True)
            self.cb_square.blockSignals(True)
            self.cb_cross.setChecked(iv.show_cross)
            self.cb_circle.setChecked(iv.show_circle)
            self.cb_square.setChecked(iv.show_square)
            self.cb_cross.blockSignals(False)
            self.cb_circle.blockSignals(False)
            self.cb_square.blockSignals(False)
            self._refresh_draw_btns()

    def _on_cam_label_size_changed(self, px: int):
        self._multi_grid.set_label_font_size(px)
        self.img_view.cam_label_font_px = px
        self.img_view._scaled = None  # force re-scale with new label bar height
        self.img_view.update()

    def _switch_to_multi_view(self):
        self._single_wrapper.setVisible(False)
        self.pointing_panel.setVisible(False)
        self._img_pointing_row.setStretch(self._img_pointing_row.indexOf(self._multi_grid), 1)
        self._multi_grid.setVisible(True)

    def _switch_to_single_view(self):
        self._single_wrapper.setVisible(True)
        self._multi_grid.setVisible(False)
        if hasattr(self, '_sc_cam_row_widget'):
            self._sc_cam_row_widget.setVisible(False)
        self._per_cam_container.setVisible(False)
        self.slider.setVisible(True)
        self.tickbar.setVisible(True)
        self.tickbar.set_cursor(None)

    # ── Per-camera sliders ────────────────────────────────────────────────────

    def _build_per_cam_sliders(self, cam_names: list[str]):
        """(Re)vytvoří řady per-camera sliderů. Voláno při každém setup_multi_cam."""
        # Odstraň staré řady
        for row in self._per_cam_rows:
            row.setParent(None)
        self._per_cam_rows.clear()

        for i, name in enumerate(cam_names):
            row = _CamSliderRow(i, name, self._per_cam_container)
            row.master_chosen.connect(self._on_per_cam_master_chosen)
            row.master_deselected.connect(self._on_per_cam_master_deselected)
            row.value_changed.connect(self._on_per_cam_value_changed)
            row.pressed.connect(self._on_per_cam_pressed)
            row.released.connect(self._on_per_cam_released)
            self._per_cam_layout.addWidget(row)
            self._per_cam_rows.append(row)

        # První kamera je defaultní master
        self._per_cam_master_idx = 0
        for i, row in enumerate(self._per_cam_rows):
            row.set_master(i == 0)
            row.set_enabled(True)

        self._per_cam_container.setVisible(True)

        # After layout is computed, align tickbar axis with slider track start
        def _update_tickbar_offset():
            if self._per_cam_rows:
                row0 = self._per_cam_rows[0]
                # Map slider left edge from row coords to tickbar coords
                row_in_tickbar = self.tickbar.mapFromGlobal(
                    row0.mapToGlobal(row0._slider.pos()))
                offset = max(0, row_in_tickbar.x())
                self.tickbar.set_left_offset(offset)
        QTimer.singleShot(0, _update_tickbar_offset)

    def _on_per_cam_master_chosen(self, cam_idx: int):
        """Uživatel klikl na radio tlačítko — přepne master na tuto kameru."""
        self._per_cam_master_idx = cam_idx
        for i, row in enumerate(self._per_cam_rows):
            row.set_master(i == cam_idx)
        # Sync slaves from new master's current position
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if cam_ts and cam_idx < len(self._per_cam_rows):
            row = self._per_cam_rows[cam_idx]
            t_raw = self._per_cam_slider_to_ts(cam_idx, row.value())
            frame_idx = max(0, bisect.bisect_right(cam_ts, t_raw) - 1)
            t_ns = cam_ts[frame_idx]
            self._per_cam_sync_slaves(cam_idx, t_ns)

    def _on_per_cam_master_deselected(self, cam_idx: int):
        """User unchecked the master radio — switch to independent mode (no master)."""
        self._per_cam_master_idx = -1
        for row in self._per_cam_rows:
            row.set_master(False)

    def _on_per_cam_pressed(self, cam_idx: int):
        self._per_cam_scrubbing_cam = cam_idx
        if self._is_playing:
            self.stop()

    def _on_per_cam_released(self, cam_idx: int):
        self._per_cam_scrubbing_cam = -1
        if not self._cam_items or cam_idx >= len(self._cam_items):
            return
        row = self._per_cam_rows[cam_idx]
        t_ns = self._per_cam_slider_to_ts(cam_idx, row.value())
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if cam_ts:
            frame_idx = max(0, bisect.bisect_right(cam_ts, t_ns) - 1)
            t_ns = cam_ts[frame_idx]
            snapped = self._per_cam_ts_to_slider(cam_idx, t_ns)
            row.set_value(snapped)
        self._per_cam_display_one(cam_idx, t_ns)
        if self._per_cam_master_idx >= 0 and cam_idx == self._per_cam_master_idx:
            self._per_cam_sync_slaves(cam_idx, t_ns)

    def _on_per_cam_value_changed(self, cam_idx: int, v: int):
        """Voláno při každém pohybu sliderů — time-based s bisect na nejbližší frame."""
        if not self._cam_items or cam_idx >= len(self._cam_items):
            return
        t_ns = self._per_cam_slider_to_ts(cam_idx, v)
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if cam_ts:
            frame_idx = max(0, bisect.bisect_right(cam_ts, t_ns) - 1)
            t_ns = cam_ts[frame_idx]
        self._per_cam_display_one(cam_idx, t_ns)
        if self._per_cam_master_idx >= 0 and cam_idx == self._per_cam_master_idx:
            self._per_cam_sync_slaves(cam_idx, t_ns)

    def _per_cam_ts_to_frame(self, cam_idx: int, ts_ns: int) -> int:
        """Vrátí index nejbližšího framu (v minulosti nebo přesně) pro daný timestamp."""
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if not cam_ts:
            return 0
        return max(0, bisect.bisect_right(cam_ts, ts_ns) - 1)

    def _per_cam_frame_to_slider(self, cam_idx: int, frame_idx: int) -> int:
        """Převede index snímku na slider hodnotu na společné časové ose."""
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if not cam_ts or frame_idx >= len(cam_ts):
            return 0
        return self._per_cam_ts_to_slider(cam_idx, cam_ts[frame_idx])

    def _per_cam_slider_to_ts(self, cam_idx: int, v: int) -> int:
        """Převede hodnotu slideru (na společné časové ose) na timestamp v ns."""
        ax_min = self.axis_min_ns
        ax_max = self.axis_max_ns
        if ax_max <= ax_min:
            # Fallback: použij rozsah dané kamery
            cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
            if not cam_ts:
                return 0
            ax_min, ax_max = cam_ts[0], cam_ts[-1]
            if ax_max <= ax_min:
                return ax_min
        return int(ax_min + v / SLIDER_MAX * (ax_max - ax_min))

    def _per_cam_ts_to_slider(self, cam_idx: int, ts_ns: int) -> int:
        """Převede timestamp na hodnotu slideru (na společné časové ose)."""
        ax_min = self.axis_min_ns
        ax_max = self.axis_max_ns
        if ax_max <= ax_min:
            cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
            if not cam_ts:
                return 0
            ax_min, ax_max = cam_ts[0], cam_ts[-1]
            if ax_max <= ax_min:
                return 0
        frac = (ts_ns - ax_min) / (ax_max - ax_min)
        return max(0, min(SLIDER_MAX, int(frac * SLIDER_MAX)))

    def _per_cam_display_one(self, cam_idx: int, t_ns: int):
        """Zobrazí frame pro jednu kameru na daném čase."""
        cam_items = self._cam_items[cam_idx] if cam_idx < len(self._cam_items) else []
        cam_ts    = self._cam_ts[cam_idx]    if cam_idx < len(self._cam_ts)    else []
        if not cam_items:
            return
        pos     = bisect.bisect_right(cam_ts, t_ns) - 1
        cam_idx_f = max(0, min(len(cam_items) - 1, pos))
        # Update info panel and tickbar cursor for master camera (or active scrub cam in independent mode)
        _is_info_cam = (cam_idx == self._per_cam_master_idx) or (
            self._per_cam_master_idx < 0 and cam_idx == self._per_cam_scrubbing_cam)
        if _is_info_cam:
            real_ts = cam_ts[cam_idx_f] if cam_ts else t_ns
            self.lbl_prague_time.setText(f"Prague: {fmt_prague_full_from_ns(real_ts)}")
            self.lbl_axis_time.setText(f"Axis: {fmt_hhmmss_ms_from_ns(real_ts)}")
            self.tickbar.set_cursor(real_ts)
        it      = cam_items[cam_idx_f]

        if hasattr(self, '_cam_current_idx') and cam_idx < len(self._cam_current_idx):
            self._cam_current_idx[cam_idx] = cam_idx_f

        n_cams = len(self._cam_items)
        max_side = 400 if n_cams >= 3 else (500 if n_cams == 2 else self._scrub_side)
        brighten    = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract    = self.cb_subtract.isChecked()
        ref = self._cam_ref_images[cam_idx] if (subtract and cam_idx < len(self._cam_ref_images)) else None
        sub_thr = self.sub_threshold_sb.value() if ref is not None else 0

        cache = self._cam_caches[cam_idx]
        key   = (cam_idx_f, max_side, brighten, gradient_id, self._brightness_offset,
                 id(ref) if ref is not None else None, sub_thr)
        cached = cache.get(key)
        if cached is not None and not cached.isNull():
            iv = self._multi_grid.get_img_view(cam_idx)
            if iv:
                iv.set_pixmap(cached)
            self._multi_grid.set_cam_timestamp(cam_idx, fmt_hhmmss_ms_from_ns(it.ts_ns))
            return

        pool = self._cam_pools[cam_idx]
        sig  = self._cam_signals[cam_idx]
        pool.start(LoadTask(
            self._gen, 0, cam_idx_f,
            it.path, max_side, brighten, gradient_id,
            sig, self._brightness_offset, ref, sub_thr))
        self._multi_grid.set_cam_timestamp(cam_idx, fmt_hhmmss_ms_from_ns(it.ts_ns))

    def _per_cam_sync_slaves(self, master_cam: int, master_ts_ns: int):
        """Synchronizuje slave kamery na nejbližší timestamp k master_ts_ns.
        Preferuje snímky v minulosti; povoluje max 100ms do budoucnosti."""
        _100MS = 220_000_000  # 0.22 s v ns
        for i, row in enumerate(self._per_cam_rows):
            if i == master_cam:
                continue
            cam_ts = self._cam_ts[i] if i < len(self._cam_ts) else []
            if not cam_ts:
                continue
            # bisect_right dá index prvního ts > master_ts_ns
            pos = bisect.bisect_right(cam_ts, master_ts_ns)
            # candidate vlevo (minulost nebo přesná shoda)
            pos_left  = max(0, pos - 1)
            # candidate vpravo (budoucnost)
            pos_right = min(len(cam_ts) - 1, pos)
            ts_left  = cam_ts[pos_left]
            ts_right = cam_ts[pos_right]
            # Vezmi pravý jen pokud je <= 100ms v budoucnosti a blíže než levý
            if (ts_right > master_ts_ns and
                    ts_right - master_ts_ns <= _100MS and
                    abs(ts_right - master_ts_ns) < abs(master_ts_ns - ts_left)):
                best_pos = pos_right
            else:
                best_pos = pos_left
            slave_ts = cam_ts[best_pos]
            sv = self._per_cam_ts_to_slider(i, slave_ts)
            row.set_value(sv)
            self._per_cam_display_one(i, slave_ts)

    def _per_cam_step(self, delta: int):
        """Posune master kameru o delta framů, ostatní synchronizuje."""
        master = self._per_cam_master_idx
        if master < 0 or master >= len(self._cam_items) or master >= len(self._cam_ts):
            return
        cam_ts = self._cam_ts[master]
        if not cam_ts:
            return
        row = self._per_cam_rows[master]
        cur_t = self._per_cam_slider_to_ts(master, row.value())
        cur_frame = max(0, bisect.bisect_right(cam_ts, cur_t) - 1)
        new_frame = max(0, min(len(cam_ts) - 1, cur_frame + delta))
        new_ts = cam_ts[new_frame]
        sv = self._per_cam_ts_to_slider(master, new_ts)
        row.set_value(sv)
        self._per_cam_display_one(master, new_ts)
        if self._per_cam_master_idx >= 0:
            self._per_cam_sync_slaves(master, new_ts)

    def _setup_multi_cam(self, cam_names: list[str], cam_folders: list[Path],
                         reset_items: bool = True,
                         cam_folder_lists: "list[list[Path]] | None" = None):
        """Inicializuje multi-camera stav."""
        self._cam_names   = cam_names
        self._cam_folders = cam_folders
        self._cam_folder_lists = cam_folder_lists if cam_folder_lists is not None else [[f] for f in cam_folders]
        n = len(cam_names)

        # Inicializuj per-camera struktury (přeskočit pokud data už jsou z předchozího scanu)
        if reset_items:
            self._cam_items       = [[] for _ in range(n)]
            self._cam_ts          = [[] for _ in range(n)]
            self._cam_poll_max_ts = [0] * n   # highest ts_ns seen per camera (for fast incremental poll)
        self._cam_caches       = [PixCache(80) for _ in range(n)]
        self._cam_ref_images   = [None] * n
        self._cam_current_idx  = [0] * n   # per-camera frame index currently displayed
        self._cam_pools      = []
        self._cam_signals    = []
        for i in range(n):
            pool = QThreadPool(self)
            pool.setMaxThreadCount(2)
            self._cam_pools.append(pool)
            sig = LoaderSignals()
            sig.loaded.connect(
                lambda gen, req, idx, ms, br, gid, bo, img, cam_i=i:
                    self._on_cam_loaded(cam_i, gen, req, idx, ms, br, gid, bo, img))
            self._cam_signals.append(sig)

        self._multi_grid.setup_cameras(cam_names)
        self._multi_grid.set_label_font_size(self._cam_label_size_sb.value())
        self._switch_to_multi_view()
        self._sc_set_enabled(True)
        # Populate the SC camera selector combo
        self._sc_cam_combo.blockSignals(True)
        self._sc_cam_combo.clear()
        for name in cam_names:
            self._sc_cam_combo.addItem(name)
        self._sc_cam_combo.setCurrentIndex(0)
        self._sc_cam_combo.blockSignals(False)
        self._sc_cam_row_widget.setVisible(True)
        # Build per-camera sliders (hidden global slider, show per-cam rows)
        self._build_per_cam_sliders(cam_names)
        self.slider.setVisible(False)
        self.tickbar.setVisible(True)
        self.tickbar.set_cursor(None)

    # ================================================================ ONLINE MODE
    def _on_auto_follow_toggled(self, checked: bool):
        self._auto_follow = checked
        if checked and not self._online_mode:
            self._start_online_mode()
        elif not checked and self._online_mode:
            self._stop_online_mode()
        if checked and self.items:
            last_idx = len(self.items) - 1
            if self._is_multi_cam():
                # Jump each per-cam slider to its own latest frame immediately
                for cam_i, row in enumerate(self._per_cam_rows):
                    cam_ts = self._cam_ts[cam_i] if cam_i < len(self._cam_ts) else []
                    if not cam_ts:
                        continue
                    latest_ts = cam_ts[-1]
                    sv = self._per_cam_ts_to_slider(cam_i, latest_ts)
                    row.set_value(sv)
                    self._per_cam_display_one(cam_i, latest_ts)
                master = self._per_cam_master_idx
                if master >= 0 and master < len(self._cam_ts) and self._cam_ts[master]:
                    self.tickbar.set_cursor(self._cam_ts[master][-1])
            else:
                self._display_exact_index(last_idx, self.items[last_idx].ts_ns, update_slider=True)

    def _start_online_mode(self):
        self._online_mode = True
        self._online_timer.start()
        self._btn_auto_follow.setEnabled(True)
        self._btn_auto_follow.blockSignals(True)
        self._btn_auto_follow.setChecked(True)
        self._btn_auto_follow.blockSignals(False)
        self._auto_follow = True
        self._online_lbl.setText("Online: ON")
        self._online_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self._online_blink_state = False
        self._online_last_new_ns = 0.0
        self._online_blink_timer.start()
        self._online_dot.setStyleSheet("font-size: 14px; color: #22cc22;")
        self._online_dot_top.setStyleSheet("font-size: 14px; color: #22cc22;")
        self.lbl_scan_progress.setText("Online mode: active")
        self._online_poll_running = False

    def _stop_online_mode(self):
        self._online_mode = False
        self._online_timer.stop()
        self._online_blink_timer.stop()
        self._btn_auto_follow.blockSignals(True)
        self._btn_auto_follow.setChecked(False)
        self._btn_auto_follow.blockSignals(False)
        self._auto_follow = False
        self._online_lbl.setText("Inactive")
        self._online_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self._online_dot.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_dot_top.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_poll_running = False

    def _on_online_blink(self):
        """Blikání kolečka online indikátoru: zelené bliká = OK, červené = problém."""
        self._online_blink_state = not self._online_blink_state
        # Pokud jsme déle než 10s bez nového snímku → varování (červená)
        stale = (self._online_last_new_ns > 0 and
                 time.time() - self._online_last_new_ns > 10.0)
        if stale:
            color = "#cc2222" if self._online_blink_state else "#660000"
        else:
            color = "#22cc22" if self._online_blink_state else "#116611"
        self._online_dot.setStyleSheet(f"font-size: 14px; color: {color};")
        self._online_dot_top.setStyleSheet(f"font-size: 14px; color: {color};")

    def _online_poll(self):
        """Voláno každých 300ms."""
        if self._is_multi_cam():
            if not self._cam_folder_lists and not self._cam_folders:
                return
            # Multi-cam: per-camera tasks run independently, no global gate
            self._online_poll_multi()
        else:
            if not self.opened_folders:
                return
            if getattr(self, '_online_poll_running', False):
                return
            self._online_poll_single_bg()

    def _online_poll_single_bg(self):
        """Spustí background scan pro single-camera — neblokuje UI."""
        self._online_poll_running = True
        folders = self.opened_folders[:]
        gen = self._gen
        # Use ts_ns cutoff instead of path set — much faster O(n) single pass
        cutoff_ns = self.ts_list[-1] if self.ts_list else 0

        def on_found(new_items):
            self._online_poll_running = False
            if gen != self._gen or not new_items:
                return
            new_items.sort(key=lambda x: x.ts_ns)
            # Append-only — items list is always sorted, new items are all newer
            self.items = self.items + new_items
            self.ts_list = self.ts_list + [it.ts_ns for it in new_items]

            # Rozšiř osu pokud nové snímky přesahují
            ts_max = self.ts_list[-1]
            if ts_max > self.axis_max_ns:
                dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
                span_hours = math.ceil(
                    (ts_max - ns_from_dt(dt0)) / ONE_HOUR_NS)
                self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_hours))
                self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)

            self._online_last_new_ns = time.time()
            self.lbl_scan_progress.setText("Online mode: active")
            self.lbl_index.setText(
                f"{(self.current_idx or 0) + 1} / {len(self.items)}")

            if self._auto_follow:
                last_idx = len(self.items) - 1
                self._display_exact_index(
                    last_idx, self.items[last_idx].ts_ns, update_slider=True)


        class _PollSignals2(QObject):
            found = Signal(list, list)  # (new_items, new_folders)

        sig2 = _PollSignals2(self)

        def on_found_with_folders(new_items, new_folders):
            # Register newly discovered hour-folders so future polls scan them too
            if new_folders:
                for nf in new_folders:
                    if nf not in self.opened_folders:
                        self.opened_folders.append(nf)
            on_found(new_items)

        sig2.found.connect(on_found_with_folders)

        class _PollTask(QRunnable):
            def __init__(self, folders, cutoff, signal):
                super().__init__()
                self._folders = folders
                self._cutoff  = cutoff
                self._sig = signal

            def run(self):
                new_items = []
                known = set(self._folders)
                for folder in self._folders:
                    try:
                        with os.scandir(folder) as it:
                            for e in it:
                                if not e.is_file():
                                    continue
                                p = Path(e.path)
                                if p.suffix.lower() not in IMG_EXT:
                                    continue
                                ts_ns = parse_unix_ns_from_name(p)
                                if ts_ns is None or ts_ns <= self._cutoff:
                                    continue
                                new_items.append(Item(p, ts_ns))
                    except Exception:
                        pass

                # Probe next UTC hour-folders (same logic as _CamPollTask)
                new_folders = []
                for folder in list(self._folders):
                    try:
                        cam_name = folder.name
                        hour_dir = folder.parent
                        day_dir  = hour_dir.parent
                        try:
                            current_utc_hour = int(hour_dir.name)
                        except ValueError:
                            continue
                        for delta in range(1, 4):
                            next_h = (current_utc_hour + delta) % 24
                            if next_h < current_utc_hour and delta == 1:
                                try:
                                    from datetime import date as _date, timedelta as _td
                                    day_parts = (int(day_dir.parent.parent.name),
                                                 int(day_dir.parent.name),
                                                 int(day_dir.name))
                                    next_day = _date(*day_parts) + _td(days=1)
                                    candidate = (day_dir.parent.parent.parent
                                                 / str(next_day.year)
                                                 / str(next_day.month)
                                                 / str(next_day.day)
                                                 / str(next_h)
                                                 / cam_name)
                                except Exception:
                                    continue
                            else:
                                candidate = day_dir / str(next_h) / cam_name
                            if candidate in known or candidate in new_folders:
                                break
                            if candidate.exists() and candidate.is_dir():
                                new_folders.append(candidate)
                                known.add(candidate)
                                try:
                                    with os.scandir(candidate) as it2:
                                        for e in it2:
                                            if not e.is_file():
                                                continue
                                            p = Path(e.path)
                                            if p.suffix.lower() not in IMG_EXT:
                                                continue
                                            ts_ns = parse_unix_ns_from_name(p)
                                            if ts_ns is None or ts_ns <= self._cutoff:
                                                continue
                                            new_items.append(Item(p, ts_ns))
                                except Exception:
                                    pass
                            else:
                                break
                    except Exception:
                        pass

                self._sig.found.emit(new_items, new_folders)

        task = _PollTask(folders, cutoff_ns, sig2)
        self.scan_pool.start(task)

    def _online_poll_multi(self):
        """Per-camera independent polling — each camera runs its own _CamPollTask in parallel."""
        gen = self._gen
        folder_lists = list(self._cam_folder_lists) if self._cam_folder_lists else [[f] for f in self._cam_folders]
        n_cams = len(folder_lists)
        if n_cams == 0:
            return

        if not hasattr(self, '_cam_poll_running') or len(self._cam_poll_running) != n_cams:
            self._cam_poll_running = [False] * n_cams
        if not hasattr(self, '_cam_poll_sigs') or len(self._cam_poll_sigs) != n_cams:
            self._cam_poll_sigs = [None] * n_cams

        poll_max_ts = getattr(self, '_cam_poll_max_ts', [0] * n_cams)

        for cam_i in range(n_cams):
            if self._cam_poll_running[cam_i]:
                continue  # this camera's previous task still running — skip tick

            folders  = list(folder_lists[cam_i])
            cutoff   = poll_max_ts[cam_i] if cam_i < len(poll_max_ts) else 0
            cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else ""

            sig = _CamPollSignals(self)
            self._cam_poll_sigs[cam_i] = sig  # keep reference so it isn't GC'd

            def make_callback(ci, g):
                def on_cam_found(cam_idx: int, new_items: list, new_folders: list):
                    self._cam_poll_running[cam_idx] = False
                    if g != self._gen:
                        return
                    if new_folders and cam_idx < len(self._cam_folder_lists):
                        for nf in new_folders:
                            if nf not in self._cam_folder_lists[cam_idx]:
                                self._cam_folder_lists[cam_idx].append(nf)
                    if not new_items or cam_idx >= len(self._cam_items):
                        return
                    self._cam_items[cam_idx].extend(new_items)
                    self._cam_ts[cam_idx].extend(it.ts_ns for it in new_items)
                    if cam_idx < len(self._cam_poll_max_ts):
                        self._cam_poll_max_ts[cam_idx] = self._cam_ts[cam_idx][-1]
                    self._online_last_new_ns = time.time()
                    self._extend_shared_timeline_from_cams()
                    cam_lines = "\n".join(
                        f"{self._cam_names[i] if i < len(self._cam_names) else f'cam{i}'} - IMG: {len(c)}"
                        for i, c in enumerate(self._cam_items)
                    )
                    self.lbl_scan_progress.setText(cam_lines)
                    self.lbl_index.setText(f"{(self.current_idx or 0) + 1} / {len(self.items)}")
                    if self._auto_follow:
                        # Auto-follow: show each camera's own latest frame independently
                        cam_ts_now = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
                        if cam_ts_now:
                            latest_frame = len(cam_ts_now) - 1
                            latest_ts = cam_ts_now[latest_frame]
                            self._per_cam_display_one(cam_idx, latest_ts)
                            # Update this camera's per-cam slider to latest position
                            if cam_idx < len(self._per_cam_rows):
                                sv = self._per_cam_ts_to_slider(cam_idx, latest_ts)
                                self._per_cam_rows[cam_idx].set_value(sv)
                            # Update tickbar cursor from master camera
                            if cam_idx == self._per_cam_master_idx:
                                self.tickbar.set_cursor(latest_ts)
                    else:
                        # Not following — just refresh this camera at current slider time
                        if self.items and self.current_idx is not None:
                            t_ns = self.items[self.current_idx].ts_ns
                            cam_ts_now = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
                            if cam_ts_now:
                                self._per_cam_display_one(cam_idx, t_ns)
                return on_cam_found

            sig.found.connect(make_callback(cam_i, gen))
            self._cam_poll_running[cam_i] = True
            self._poll_pool.start(_CamPollTask(cam_i, folders, cutoff, cam_name, sig))

    def _rebuild_shared_items_from_cams(self):
        """
        Builds a merged, sorted timeline from all cameras' timestamps.
        self.items / self.ts_list contain unique timestamps across all cameras —
        the slider moves through real time and each camera independently shows
        its latest frame with ts_ns <= current slider time.
        """
        if not self._cam_items:
            return
        all_items = []
        seen_ts: set[int] = set()
        for cam_items in self._cam_items:
            for it in cam_items:
                if it.ts_ns not in seen_ts:
                    seen_ts.add(it.ts_ns)
                    all_items.append(it)
        if not all_items:
            return
        all_items.sort(key=lambda it: it.ts_ns)
        self.items   = all_items
        self.ts_list = [it.ts_ns for it in self.items]

        # Rozšiř osu
        if self.ts_list:
            ts_max = self.ts_list[-1]
            if ts_max > self.axis_max_ns:
                dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
                span_h = math.ceil((ts_max - ns_from_dt(dt0)) / ONE_HOUR_NS)
                self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_h))
                self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)

        # Per-camera sorted timestamp arrays for fast bisect lookup
        self._cam_ts = [[it.ts_ns for it in cam_items] for cam_items in self._cam_items]

    def _extend_shared_timeline_from_cams(self):
        """
        Fast incremental update of the shared timeline during online polling.
        Only appends new timestamps — no full re-sort. Called after _cam_items
        have already been extended with strictly-newer items appended at the end.
        """
        if not self._cam_items or not self.ts_list:
            self._rebuild_shared_items_from_cams()
            return
        current_max_ts = self.ts_list[-1]
        new_items = []
        seen_ts: set[int] = set()
        for cam_items in self._cam_items:
            # Only look at the tail that is newer than current_max_ts
            ts_arr = [it.ts_ns for it in cam_items]
            start = bisect.bisect_right(ts_arr, current_max_ts)
            for it in cam_items[start:]:
                if it.ts_ns not in seen_ts:
                    seen_ts.add(it.ts_ns)
                    new_items.append(it)
        if not new_items:
            return
        new_items.sort(key=lambda it: it.ts_ns)
        self.items.extend(new_items)
        self.ts_list.extend(it.ts_ns for it in new_items)
        # Extend axis if needed
        ts_max = self.ts_list[-1]
        if ts_max > self.axis_max_ns:
            dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
            span_h = math.ceil((ts_max - ns_from_dt(dt0)) / ONE_HOUR_NS)
            self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_h))
            self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
            # After axis expansion, recalculate per-cam sliders: old integer values now
            # map to different timestamps on the wider axis. Jump each to its latest frame.
            if self._auto_follow and self._is_multi_cam():
                for cam_i, row in enumerate(self._per_cam_rows):
                    cam_ts = self._cam_ts[cam_i] if cam_i < len(self._cam_ts) else []
                    if not cam_ts:
                        continue
                    sv = self._per_cam_ts_to_slider(cam_i, cam_ts[-1])
                    row.set_value(sv)

    def open_folder(self):
        # Nejdřív zkontroluj že máme nastavené časové okno
        if self.last_pick_axis_override is None:
            QMessageBox.information(self, "Time window not set",
                "Please set a time window first using the 'Time window' button.")
            return

        # Počkej max 3s na přednahraná data
        import time as _time
        deadline = _time.time() + 3.0
        while not getattr(self, '_cameras_loaded', True) and _time.time() < deadline:
            QApplication.processEvents()
            _time.sleep(0.05)

        preloaded = getattr(self, '_preloaded_cameras', None)

        day_obj = self.last_pick_date
        dlg = CameraPickerDialog(
            day_obj,
            self.last_pick_hour_from,
            self.last_pick_hour_to,
            self.last_pick_cam_names,
            self,
            preloaded_cameras=getattr(self, '_preloaded_cameras', None))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        cam_names = dlg.selected_camera_names()
        if not cam_names:
            return

        # Ulož do paměti
        self.last_pick_cam_names = cam_names

        # Preserve online mode if already active; _pending_online_mode covers first launch
        online = self._online_mode or getattr(self, '_pending_online_mode', False)
        axis_override = self.last_pick_axis_override
        hour_from = self.last_pick_hour_from
        hour_to   = self.last_pick_hour_to

        # Sestav složky
        cam_folder_lists: list[list[Path]] = []
        for cam_name in cam_names:
            folders = DatePickerDialog.selected_folders_static(
                day_obj, hour_from, hour_to,
                Path(DEFAULT_OPEN_ROOT) / cam_name)
            cam_folders_with_cam = []
            for f in folders:
                cf = f / cam_name if not f.name == cam_name else f
                cam_folders_with_cam.append(cf)
            cam_folder_lists.append(cam_folders_with_cam)

        self.lbl_selected_range.setText(
            f"Range: {hour_from:02d}:00 – {hour_to:02d}:59")

        if len(cam_names) == 1:
            self._stop_online_mode()
            # Preserve overlay from multi-cam grid for this camera before switching to single
            grid_state = self._multi_grid._overlay_store.get(cam_names[0])
            if grid_state is None:
                # Current single-cam might be this camera — save its overlay too
                if self._cam_names and self._cam_names[0] == cam_names[0]:
                    grid_state = MultiCameraGrid._save_iv_overlay(self.img_view)
            self._switch_to_single_view()
            self._cam_names = [cam_names[0]]
            folders = cam_folder_lists[0]
            existing = [f for f in folders if f.exists() and f.is_dir()]
            if not existing:
                QMessageBox.warning(self, "Folder not found",
                    f"Camera folder '{cam_names[0]}' not found.")
                return
            self.last_open_dir = existing[0]
            if online:
                self._pending_online_mode = True
            # Pre-load grid_state into img_view so _start_scan saves and restores it
            if grid_state is not None:
                MultiCameraGrid._restore_iv_overlay(self.img_view, grid_state)
            self._start_scan(existing, axis_override=axis_override,
                             folder_label=str(existing[0]))
        else:
            self._stop_online_mode()
            # Save single-cam img_view overlay into multi-grid store before switching
            if self._cam_names and len(self._cam_names) == 1:
                self._multi_grid._overlay_store[self._cam_names[0]] = \
                    MultiCameraGrid._save_iv_overlay(self.img_view)
            self._start_multi_cam_scan(
                cam_names, cam_folder_lists,
                axis_override=axis_override,
                online=online)

    def _start_multi_cam_scan(
        self,
        cam_names: list[str],
        cam_folder_lists: list[list[Path]],
        axis_override,
        online: bool,
    ):
        """Spustí scan pro více kamer naráz."""
        self._setup_multi_cam(cam_names, [])
        self.axis_override = axis_override
        self._gen += 1
        gen = self._gen

        if self._scan_task is not None:
            self._scan_task.cancel()
            self._scan_task = None

        self._reset_ui_for_new_scan([], "Multi-camera")
        self._switch_to_multi_view()

        n_cams = len(cam_names)
        finished_count = [0]
        self._cam_items  = [[] for _ in range(n_cams)]
        self._cam_ts     = [[] for _ in range(n_cams)]
        # Uchovej všechny složky per-kamera (pro online poll)
        cam_all_folders: list[list[Path]] = [[] for _ in range(n_cams)]
        cam_first_folder: list[Path | None] = [None] * n_cams

        # Pomocné signály pro každou kameru — bezpečné přes Qt signal/slot
        class _CamScanSignals(QObject):
            done = Signal(int, list, list)  # cam_i, items, folders

        _signals = _CamScanSignals(self)

        def on_cam_scan_done(cam_i: int, items: list, folders: list):
            if gen != self._gen:
                return
            self._cam_items[cam_i] = items
            self._cam_ts[cam_i]    = [it.ts_ns for it in items]
            if hasattr(self, '_cam_poll_max_ts') and cam_i < len(self._cam_poll_max_ts):
                self._cam_poll_max_ts[cam_i] = items[-1].ts_ns if items else 0
            cam_all_folders[cam_i] = folders          # všechny složky
            cam_first_folder[cam_i] = folders[0] if folders else None
            finished_count[0] += 1
            self.lbl_scan_progress.setText(
                f"Scanned {finished_count[0]}/{n_cams} cameras…")
            if finished_count[0] == n_cams:
                first_folders = [f for f in cam_first_folder if f is not None]
                self._on_multi_scan_all_done(
                    gen, cam_names,
                    first_folders,
                    cam_all_folders,
                    axis_override, online)

        _signals.done.connect(on_cam_scan_done)

        for cam_i, (cam_name, folder_list) in enumerate(
                zip(cam_names, cam_folder_lists)):

            existing = [f for f in folder_list if f.exists() and f.is_dir()]
            if not existing:
                day_obj = self.last_pick_date
                h_from  = self.last_pick_hour_from
                h_to    = self.last_pick_hour_to
                for hh in range(h_from, h_to + 1):
                    ref_dt = datetime(day_obj.year, day_obj.month,
                                      day_obj.day, hh, 0, 0, tzinfo=TZ_PRAGUE)
                    folder_h = folder_hour_from_prague_hour(hh, ref_dt)
                    cf = (Path(DEFAULT_OPEN_ROOT)
                          / str(day_obj.year) / str(day_obj.month)
                          / str(day_obj.day) / str(folder_h) / cam_name)
                    if cf.exists() and cf.is_dir():
                        existing.append(cf)

            if not existing:
                _signals.done.emit(cam_i, [], [])
                continue

            # Každá kamera jako samostatný QRunnable — správně přes Qt thread pool
            class _CamScanTask(QRunnable):
                def __init__(self, ci, folders, sig):
                    super().__init__()
                    self._ci = ci
                    self._folders = folders
                    self._sig = sig

                def run(self):
                    items: list[Item] = []
                    for folder in self._folders:
                        try:
                            with os.scandir(folder) as it:
                                for e in it:
                                    if not e.is_file():
                                        continue
                                    p = Path(e.path)
                                    if p.suffix.lower() not in IMG_EXT:
                                        continue
                                    ts_ns = parse_unix_ns_from_name(p)
                                    if ts_ns is None:
                                        continue
                                    items.append(Item(p, ts_ns))
                        except Exception:
                            pass
                    items.sort(key=lambda x: x.ts_ns)
                    self._sig.done.emit(self._ci, items, self._folders)

            task = _CamScanTask(cam_i, existing, _signals)
            self.scan_pool.start(task)

    def _on_multi_scan_all_done(
        self, gen: int, cam_names: list[str],
        cam_folders: list[Path],
        cam_folder_lists: "list[list[Path]] | None",
        axis_override, online: bool
    ):
        if gen != self._gen:
            return

        self._cam_folders = cam_folders
        self._setup_multi_cam(cam_names, cam_folders, reset_items=False,
                              cam_folder_lists=cam_folder_lists)

        # Nastav items ze první kamery pro slider
        self._rebuild_shared_items_from_cams()

        if not self.items:
            self.lbl_filename.setText("No images found.")
            self.prog.setVisible(False)
            self.btn_cancel_scan.setVisible(False)
            return

        # Nastav osu
        ts_min, ts_max = self.ts_list[0], self.ts_list[-1]
        if axis_override is not None:
            self.axis_min_ns, self.axis_max_ns = axis_override
        else:
            folder_axis = None
            self.axis_min_ns, self.axis_max_ns = self._choose_axis(
                folder_axis, ts_min, ts_max)

        self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
        self._apply_marks_to_tickbar()

        # Povol ovládací prvky
        self.slider.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_save_range.setEnabled(True)
        self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_prev.setEnabled(True)
        self.btn_next.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.btn_set_a.setEnabled(True)
        self.btn_set_b.setEnabled(True)
        self.btn_clear_marks.setEnabled(True)
        self._btn_auto_follow.setEnabled(True)
        self.btn_set_ref.setEnabled(True)

        self.prog.setVisible(False)
        self.btn_cancel_scan.setVisible(False)
        cam_lines = "\n".join(
            f"{self._cam_names[i] if i < len(self._cam_names) else f'cam{i}'} - IMG: {len(c)}"
            for i, c in enumerate(self._cam_items)
        )
        self.lbl_scan_progress.setText(cam_lines)
        self.lbl_index.setText(f"1 / {len(self.items)}")

        # V online modu zobraz poslední snímek, jinak první
        start_idx = len(self.items) - 1 if online else 0
        self._display_multicam_index(start_idx, update_slider=True)

        if online:
            self._pending_online_mode = False
            self.lbl_scan_progress.setText("Online mode: active")
            self._start_online_mode()

    def open_by_date(self):
        dlg = DatePickerDialog(
            self.last_open_dir,
            self.last_pick_hour_from,
            self.last_pick_hour_to,
            self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        axis_override = dlg.selected_axis()
        hour_from, hour_to = dlg.selected_hours()
        online = dlg.is_online_mode()
        multiday = dlg.is_multiday()
        if online:
            self._pending_online_mode = True

        self.last_pick_date          = dlg.selected_date_obj()
        self.last_pick_hour_from     = hour_from
        self.last_pick_hour_to       = hour_to
        self.last_pick_axis_override = axis_override
        # Multi-day: store all selected dates
        self._last_pick_extra_dates: "list | None" = dlg.selected_date_range() if multiday else None

        if multiday:
            days = dlg.selected_date_range()
            d0, d1 = days[0], days[-1]
            self.lbl_selected_range.setText(
                f"Range: {d0.strftime('%d.%m')}–{d1.strftime('%d.%m')}  {hour_from:02d}–{hour_to:02d}h")
        else:
            self.lbl_selected_range.setText(f"Range: {hour_from:02d}:00 – {hour_to:02d}:59")

        # Okamžitě spusť scan kamer na pozadí (ze start date)
        self._preloaded_cameras: list[tuple[str, str]] = []
        self._cameras_loaded = False

        import threading as _thr
        date_obj  = self.last_pick_date
        hour_from_scan = hour_from
        hour_to_scan   = hour_to

        def worker():
            cameras: list[tuple[str, str]] = []
            try:
                base = Path(DEFAULT_OPEN_ROOT) / str(date_obj.year) / str(date_obj.month) / str(date_obj.day)
                for hh in range(hour_from_scan, hour_to_scan + 1):
                    ref_dt = datetime(date_obj.year, date_obj.month, date_obj.day,
                                      hh, 0, 0, tzinfo=TZ_PRAGUE)
                    folder_h = folder_hour_from_prague_hour(hh, ref_dt)
                    hour_dir = base / str(folder_h)
                    if hour_dir.exists() and hour_dir.is_dir():
                        try:
                            subs = sorted(
                                [p.name for p in hour_dir.iterdir() if p.is_dir()],
                                key=str.lower)
                            for name in subs:
                                m = re.match(r"^C\d{2}-(\d{2,3})-", name)
                                num = m.group(1) if m else ""
                                if not any(n == name for _, n in cameras):
                                    cameras.append((num, name))
                        except Exception:
                            continue
            except Exception:
                pass
            self._preloaded_cameras = cameras
            self._cameras_loaded = True

        _thr.Thread(target=worker, daemon=True).start()

        # Pokud máme zapamatované kamery, automaticky použij je
        if self.last_pick_cam_names:
            QTimer.singleShot(0, self._reload_with_last_cameras)

    def _reload_with_last_cameras(self):
        """Načte snímky s naposledy vybranými kamerami bez dotazu."""
        if not self.last_pick_cam_names:
            return
        # Počkej max 3s na přednahraná data
        import time as _time
        deadline = _time.time() + 3.0
        while not getattr(self, '_cameras_loaded', True) and _time.time() < deadline:
            QApplication.processEvents()
            _time.sleep(0.05)

        cam_names  = self.last_pick_cam_names
        day_obj    = self.last_pick_date
        hour_from  = self.last_pick_hour_from
        hour_to    = self.last_pick_hour_to
        axis_override = self.last_pick_axis_override

        extra_dates = getattr(self, '_last_pick_extra_dates', None)

        cam_folder_lists: list[list[Path]] = []
        for cam_name in cam_names:
            folders = DatePickerDialog.selected_folders_static(
                day_obj, hour_from, hour_to,
                Path(DEFAULT_OPEN_ROOT) / cam_name,
                extra_dates=extra_dates)
            cam_folder_lists.append([
                f / cam_name if not f.name == cam_name else f
                for f in folders])

        if extra_dates and len(extra_dates) > 1:
            d0, d1 = extra_dates[0], extra_dates[-1]
            self.lbl_selected_range.setText(
                f"Range: {d0.strftime('%d.%m')}–{d1.strftime('%d.%m')}  {hour_from:02d}–{hour_to:02d}h")
        else:
            self.lbl_selected_range.setText(
                f"Range: {hour_from:02d}:00 – {hour_to:02d}:59")

        if len(cam_names) == 1:
            self._stop_online_mode()
            # Preserve overlay from multi-cam grid for this camera before switching to single
            grid_state = self._multi_grid._overlay_store.get(cam_names[0])
            if grid_state is None and self._cam_names and self._cam_names[0] == cam_names[0]:
                grid_state = MultiCameraGrid._save_iv_overlay(self.img_view)
            self._switch_to_single_view()
            self._cam_names = [cam_names[0]]
            existing = [f for f in cam_folder_lists[0] if f.exists() and f.is_dir()]
            if not existing:
                QMessageBox.warning(self, "Folder not found",
                    f"Camera folder '{cam_names[0]}' not found.")
                return
            self.last_open_dir = existing[0]
            # Pre-load grid_state into img_view so _start_scan saves and restores it
            if grid_state is not None:
                MultiCameraGrid._restore_iv_overlay(self.img_view, grid_state)
            self._start_scan(existing, axis_override=axis_override,
                             folder_label=str(existing[0]))
        else:
            online_flag = self._online_mode or getattr(self, '_pending_online_mode', False)
            self._stop_online_mode()
            # Save single-cam img_view overlay into multi-grid store before switching
            if self._cam_names and len(self._cam_names) == 1:
                self._multi_grid._overlay_store[self._cam_names[0]] = \
                    MultiCameraGrid._save_iv_overlay(self.img_view)
            self._start_multi_cam_scan(
                cam_names, cam_folder_lists,
                axis_override=axis_override,
                online=online_flag)

    def auto_start_online(self):
        """
        Called once on first Slider tab activation.
        Opens the Time window dialog pre-configured for online mode / current hour,
        exactly as if the user clicked 'Time window' on a fresh start.
        """
        # If data is already loaded (e.g. launched with --folder arg), do nothing.
        if self.items or self._cam_items:
            return
        self.open_by_date()

    def open_folder_path(self, folder: Path):
        if not folder or not folder.exists() or not folder.is_dir():
            QMessageBox.warning(self, "Folder not found", f"Folder does not exist:\n{folder}"); return
        self.last_open_dir = folder
        ax = axis_from_any_folder(folder)
        self.lbl_selected_range.setText("Range: single folder")
        self._start_scan([folder], axis_override=ax, folder_label=str(folder))

    def open_file_list(self, files: list):
        """Load an explicit list of image Paths (e.g. from range search). No folder scan."""
        files = [Path(f) for f in files if Path(f).exists()]
        if not files:
            QMessageBox.information(self, "Range search", "No images found."); return
        items = []
        for p in files:
            ts = parse_unix_ns_from_name(p)
            if ts is None:
                continue
            items.append(Item(p, ts))
        items.sort(key=lambda it: it.ts_ns)
        if not items:
            QMessageBox.information(self, "Range search", "Could not parse timestamps from files."); return

        self._stop_online_mode()
        self._hard_reset_runtime()
        self.opened_folders = []
        self.opened_folder = None
        self._real_ts_list = []
        self._fake_ts_map = None
        self.tickbar.discrete_ticks = None
        self.tickbar.discrete_tick_labels = None
        self.ts_list = []; self.axis_min_ns = 0; self.axis_max_ns = 0
        self.current_idx = None
        self.lbl_prague_time.setText("Prague: —"); self.lbl_axis_time.setText("Axis: —")
        self.lbl_index.setText("0 / 0")
        self.cache = PixCache(CACHE_SIZE); self._display_req_id = 0
        self._inflight.clear(); self._want_display_req.clear()
        self.img_view.clear()
        for w in [self.slider, self.btn_save, self.btn_save_range,
                self.btn_play, self.btn_stop, self.btn_prev, self.btn_next, self.btn_set_a,
                self.btn_set_b, self.btn_clear_marks, self.btn_cal_circle, self.btn_cal_square,
                self.btn_cal_cross, self.btn_pointing, self.btn_save_ts, self.btn_goto_ts,
                self.btn_set_ref]:
            w.setEnabled(False)
        self.mark_a_ns = None; self.mark_b_ns = None; self.tickbar.set_marks(None, None)
        self.prog.setVisible(False); self.lbl_scan_progress.setText(""); self.btn_cancel_scan.setVisible(False)

        self.items = items
        n = len(items)
        self.ts_list = [it.ts_ns for it in items]
        ts_min, ts_max = self.ts_list[0], self.ts_list[-1]
        self._scrub_side = 500 if n >= 15000 else (600 if n >= 6000 else 900)
        self.lbl_filename.setText(f"File: range search — {n} images")
        self.lbl_selected_range.setText(f"Range: {n} images (multi-day)")

        # Always use discrete mode for multi-day file lists
        fake_ts = [i * SLIDER_MAX // max(n - 1, 1) for i in range(n)]
        self.axis_min_ns = 0
        self.axis_max_ns = SLIDER_MAX
        self._real_ts_list = self.ts_list[:]
        self._fake_ts_map = {self.ts_list[i]: fake_ts[i] for i in range(n)}
        self.ts_list = fake_ts
        self.items = [Item(it.path, fake_ts[i]) for i, it in enumerate(items)]
        self.tickbar.discrete_ticks = fake_ts
        self.tickbar.discrete_tick_labels = [
            f"{_dt_from_ns(ts):%Y-%m-%d %H:%M:%S}"
            for ts in self._real_ts_list
        ]
        self.axis_override = None
        self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
        self._apply_marks_to_tickbar()
        self.slider.setEnabled(True); self.btn_save.setEnabled(True); self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(False); self.btn_prev.setEnabled(True); self.btn_next.setEnabled(True)
        self.btn_set_a.setEnabled(True); self.btn_set_b.setEnabled(True)
        self.btn_clear_marks.setEnabled(True)
        self.btn_refresh.setEnabled(False)
        self._gen += 1
        self.slider.blockSignals(True); self.slider.setValue(0); self.slider.blockSignals(False)
        self.play_time_ns = self.axis_min_ns; self.target_idx = 0
        self._display_exact_index(0, self.axis_min_ns, update_slider=False)

    def refresh_folder(self):
        """Donačte nové snímky ze stejných složek, zachová pozici a overlay."""
        if self._is_multi_cam():
            if not self._cam_folders:
                return
            self._refresh_multi_cam()
            return
        if not self.opened_folders:
            return
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("⟳ …")
        self._refresh_gen = self._gen
        task = RefreshScanTask(self._gen, self.opened_folders)
        self._refresh_task = task
        task.signals.finished.connect(self._on_refresh_finished)
        self.scan_pool.start(task)

    def _refresh_multi_cam(self):
        """Donačte nové snímky pro všechny kamery v multi-cam módu."""
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("⟳ …")
        gen = self._gen
        folder_lists = list(self._cam_folder_lists) if self._cam_folder_lists else [[f] for f in self._cam_folders]
        poll_max_ts = list(getattr(self, '_cam_poll_max_ts', [0] * len(folder_lists)))

        class _RefreshSignals(QObject):
            done = Signal(list)

        sig = _RefreshSignals(self)

        def on_done(new_per_cam: list):
            self.btn_refresh.setText("⟳ Refresh")
            self.btn_refresh.setEnabled(True)
            if gen != self._gen:
                return
            any_new = False
            for cam_i, new_items in enumerate(new_per_cam):
                if not new_items or cam_i >= len(self._cam_items):
                    continue
                any_new = True
                self._cam_items[cam_i].extend(new_items)
                self._cam_ts[cam_i].extend(it.ts_ns for it in new_items)
                if hasattr(self, '_cam_poll_max_ts') and cam_i < len(self._cam_poll_max_ts):
                    self._cam_poll_max_ts[cam_i] = self._cam_ts[cam_i][-1]
            if not any_new:
                return
            self._extend_shared_timeline_from_cams()
            cam_lines = "\n".join(
                f"{self._cam_names[i] if i < len(self._cam_names) else f'cam{i}'} - IMG: {len(c)}"
                for i, c in enumerate(self._cam_items)
            )
            self.lbl_scan_progress.setText(cam_lines)
            self.lbl_index.setText(f"{(self.current_idx or 0) + 1} / {len(self.items)}")
            if self.current_idx is not None:
                self._display_multicam_index(self.current_idx, update_slider=True)

        sig.done.connect(on_done)

        class _RefreshTask(QRunnable):
            def __init__(self, folder_lists, poll_max_ts, signal):
                super().__init__()
                self._folder_lists = folder_lists
                self._poll_max_ts  = poll_max_ts
                self._sig = signal

            def run(self):
                result = []
                for cam_i, folders in enumerate(self._folder_lists):
                    cutoff = self._poll_max_ts[cam_i] if cam_i < len(self._poll_max_ts) else 0
                    new_items = []
                    for folder in folders:
                        try:
                            with os.scandir(folder) as it:
                                for e in it:
                                    if not e.is_file():
                                        continue
                                    p = Path(e.path)
                                    if p.suffix.lower() not in IMG_EXT:
                                        continue
                                    ts_ns = parse_unix_ns_from_name(p)
                                    if ts_ns is None or ts_ns <= cutoff:
                                        continue
                                    new_items.append(Item(p, ts_ns))
                        except Exception:
                            pass
                    new_items.sort(key=lambda x: x.ts_ns)
                    result.append(new_items)
                self._sig.done.emit(result)

        self.scan_pool.start(_RefreshTask(folder_lists, poll_max_ts, sig))

    def _on_refresh_finished(self, gen, new_items):
        self.btn_refresh.setText("⟳ Refresh")
        self.btn_refresh.setEnabled(True)
        self.btn_pointing.setEnabled(True)
        self.btn_pointing_live.setEnabled(True)
        self._sc_set_enabled(bool(self.items) or bool(self._cam_names))
        self.btn_save_ts.setEnabled(True)
        if self._saved_timestamps:
            self.btn_goto_ts.setEnabled(True)
            self.btn_clear_ts.setEnabled(True)
        if gen != self._gen or not new_items:
            return

        # Zjisti které položky jsou skutečně nové (podle path)
        existing_paths = {it.path for it in self.items}
        added = [it for it in new_items if it.path not in existing_paths]
        if not added:
            return

        # Merge a sort
        merged = sorted(self.items + added, key=lambda it: it.ts_ns)
        self.items = merged
        self.ts_list = [it.ts_ns for it in self.items]

        # Rozšiř osu pokud nové snímky přesahují
        if self.axis_override is not None:
            # osu řízenou uživatelem neměníme
            pass
        else:
            ts_max_new = self.ts_list[-1]
            if ts_max_new > self.axis_max_ns:
                dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
                span_hours = math.ceil(
                    (ts_max_new - ns_from_dt(dt0)) / ONE_HOUR_NS
                )
                self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_hours))
                self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)

        # Zachovej aktuální pozici — přepočítej slider
        if self.current_idx is not None:
            sv = self._time_to_slider_value(self.items[self.current_idx].ts_ns)
            self.slider.blockSignals(True)
            self.slider.setValue(sv)
            self.slider.blockSignals(False)

        n_added = len(added)
        if self._online_mode:
            self.lbl_scan_progress.setText(f"Online mode: +{n_added} new frame{'s' if n_added != 1 else ''}")
        else:
            self.lbl_scan_progress.setText(f"Refresh: +{n_added} new frames")
        self.lbl_index.setText(f"{(self.current_idx or 0) + 1} / {len(self.items)}")

    # ================================================================ OVERLAYS
    def _active_img_view(self) -> "ImageView":
        """Return the ImageView that overlay controls should act on."""
        if self._is_multi_cam():
            iv = self._multi_grid.selected_img_view()
            if iv is not None:
                return iv
        return self.img_view

    def _sync_overlay_checkboxes_from_iv(self, iv: "ImageView"):
        """Sync cb_cross/cb_circle/cb_square to match iv's current overlay state (no signal loops)."""
        self.cb_cross.blockSignals(True)
        self.cb_circle.blockSignals(True)
        self.cb_square.blockSignals(True)
        self.cb_cross.setChecked(iv.show_cross)
        self.cb_circle.setChecked(iv.show_circle)
        self.cb_square.setChecked(iv.show_square)
        self.cb_cross.blockSignals(False)
        self.cb_circle.blockSignals(False)
        self.cb_square.blockSignals(False)
        self._refresh_draw_btns()

    def _on_overlay_changed(self):
        iv = self._active_img_view()
        iv.show_cross  = self.cb_cross.isChecked()
        iv.show_circle = self.cb_circle.isChecked()
        iv.show_square = self.cb_square.isChecked()
        # If a checkbox was unchecked, clear draw mode for that shape
        if not self.cb_cross.isChecked()  and iv._draw_mode == "cross":
            iv.set_draw_mode("")
        if not self.cb_circle.isChecked() and iv._draw_mode == "circle":
            iv.set_draw_mode("")
        if not self.cb_square.isChecked() and iv._draw_mode == "square":
            iv.set_draw_mode("")
        self._refresh_draw_btns()
        iv.update()

    def calibrate_circle(self):
        iv = self._active_img_view()
        if iv._pix is None or iv._pix.isNull():
            QMessageBox.information(self, "Circle calibration", "Wait until an image is displayed."); return
        if not self.cb_circle.isChecked(): self.cb_circle.setChecked(True)
        ok = iv.calibrate_circle_from_pixmap()
        if not ok:
            QMessageBox.warning(self, "Circle calibration",
                "Could not detect a circle.\nTip: enable Auto brightness first."); return
        iv.show_circle = True; iv.update()

    def calibrate_cross(self):
        iv = self._active_img_view()
        if iv._pix is None or iv._pix.isNull():
            QMessageBox.information(self, "Cross calibration", "Wait until an image is displayed."); return
        if not self.cb_cross.isChecked():
            self.cb_cross.setChecked(True)
        ok = iv.calibrate_cross_from_pixmap()
        if not ok:
            QMessageBox.warning(self, "Cross calibration", "Could not compute centroid."); return
        iv.show_cross = True; iv.update()

    def calibrate_square(self):
        iv = self._active_img_view()
        if iv._pix is None or iv._pix.isNull():
            QMessageBox.information(self, "Square calibration", "Wait until an image is displayed."); return
        if not self.cb_square.isChecked(): self.cb_square.setChecked(True)
        ok = iv.calibrate_square_from_pixmap()
        if not ok:
            QMessageBox.warning(self, "Square calibration",
                "Could not detect a rectangle.\nTip: enable Auto brightness first."); return
        iv.show_square = True; iv.update()

    # ================================================================ BRIGHTNESS
    def _on_brightness_slider_changed(self, value):
        self._brightness_offset = value
        if not self.items or self.current_idx is None: return
        self.cache = PixCache(CACHE_SIZE)
        self._inflight.clear(); self._want_display_req.clear()
        if not self._brightness_debounce.isActive():
            self._brightness_debounce.start(60)

    def _reset_brightness_slider(self):
        self.brightness_slider.setValue(0)

    def _apply_brightness_debounced(self):
        if not self.items or self.current_idx is None: return
        if self._is_multi_cam():
            self._cam_caches = [PixCache(80) for _ in self._cam_caches]
            self._display_multicam_index(self.current_idx, update_slider=False)
            return
        idx = self.current_idx
        self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)

    def _load_raw_arr(self, path) -> "np.ndarray | None":
        """Load image as raw float32 grayscale array (no stretch/gradient)."""
        img = load_image_scaled(path, 99999, False, gradient_id=0, brightness_offset=0)
        if img.isNull():
            return None
        if img.format() != QImage.Format.Format_Grayscale8:
            img = img.convertToFormat(QImage.Format.Format_Grayscale8)
        ptr = img.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(img.sizeInBytes())
        return np.frombuffer(ptr, dtype=np.uint8).reshape(
            img.height(), img.bytesPerLine())[:, :img.width()].copy().astype(np.float32)

    def _set_reference_frame(self):
        if self.current_idx is None or not self.items: return

        if self._is_multi_cam():
            # Per-camera reference: store for all currently selected cameras
            selected_indices = self._multi_grid.selected_cam_indices()
            if not selected_indices:
                return
            # Referenční timestamp z aktuální pozice (shared items = kamera 0)
            ref_ts = self.items[self.current_idx].ts_ns if self.items and self.current_idx < len(self.items) else None
            set_names = []
            for cam_i in selected_indices:
                if cam_i >= len(self._cam_items) or not self._cam_items[cam_i]:
                    continue
                cam_items = self._cam_items[cam_i]
                # Najdi snímek s timestampem nejbližším ref_ts
                if ref_ts is not None:
                    cam_ts_arr = [it.ts_ns for it in cam_items]
                    import bisect
                    pos = bisect.bisect_left(cam_ts_arr, ref_ts)
                    if pos >= len(cam_items):
                        pos = len(cam_items) - 1
                    elif pos > 0 and abs(cam_ts_arr[pos-1] - ref_ts) < abs(cam_ts_arr[pos] - ref_ts):
                        pos -= 1
                    cam_idx = pos
                else:
                    cam_idx = min(self.current_idx, len(cam_items) - 1)
                it = cam_items[cam_idx]
                try:
                    arr = self._load_raw_arr(it.path)
                    if arr is None:
                        continue
                    self._cam_ref_images[cam_i] = arr
                    ts_str = fmt_prague_full_from_ns(it.ts_ns)
                    self._multi_grid.set_cam_ref_status(cam_i, f"Ref: {ts_str}")
                    self._cam_caches[cam_i] = PixCache(80)
                    cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else f"cam {cam_i}"
                    set_names.append(cam_name)
                except Exception:
                    pass
            if set_names:
                self.lbl_ref_status.setText(f"Ref: {', '.join(set_names)}")
            if self.current_idx is not None:
                self._display_multicam_index(self.current_idx, update_slider=False)
            return

        # Single-camera path
        it = self.items[self.current_idx]
        try:
            arr = self._load_raw_arr(it.path)
            if arr is None:
                QMessageBox.warning(self, "Reference", "Could not load reference image."); return
            self._ref_image = arr
            self.lbl_ref_status.setText(f"Ref: {fmt_prague_full_from_ns(it.ts_ns)}")
            self.cache = PixCache(CACHE_SIZE)
            self._inflight.clear(); self._want_display_req.clear()
            if self.current_idx is not None:
                self._display_exact_index(
                    self.current_idx, self.items[self.current_idx].ts_ns,
                    update_slider=True)
        except Exception as e:
            QMessageBox.warning(self, "Reference", f"Could not load reference: {e}")

    def _on_subtract_changed(self):
        if self._is_multi_cam():
            self._cam_caches = [PixCache(80) for _ in self._cam_caches]
            if self.current_idx is not None:
                self._display_multicam_index(self.current_idx, update_slider=False)
            return
        self.cache = PixCache(CACHE_SIZE)
        self._inflight.clear(); self._want_display_req.clear()
        if not self.items or self.current_idx is None: return
        self._display_exact_index(
            self.current_idx, self.items[self.current_idx].ts_ns,
            update_slider=True)

    def _on_brightness_changed(self):
        if not self.items or self.current_idx is None: return
        if self._is_multi_cam():
            self._cam_caches = [PixCache(80) for _ in self._cam_caches]
            self._display_multicam_index(self.current_idx, update_slider=False)
            return
        self.cache = PixCache(CACHE_SIZE)
        self._inflight.clear(); self._want_display_req.clear()
        idx = self.current_idx
        self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)

    def _on_gradient_changed(self):
        if not self.items or self.current_idx is None: return
        if self._is_multi_cam():
            self._cam_caches = [PixCache(80) for _ in self._cam_caches]
            self._display_multicam_index(self.current_idx, update_slider=False)
            return
        self.cache = PixCache(CACHE_SIZE)
        self._inflight.clear(); self._want_display_req.clear()
        idx = self.current_idx
        self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)

    # ================================================================ SPEED
    def _current_play_pct_per_s(self) -> float:
        try: return float(self.speed_cb.currentData())
        except: return 5.0

    def _play_is_exact(self) -> bool:
        return self._current_play_pct_per_s() <= PLAY_EXACT_PCT_PER_S_THRESHOLD

    def _reset_motion_tracking(self):
        self._last_motion_counter = None; self._last_target_idx = None; self._last_motion_ips = 0.0

    def _update_motion_speed(self, target_idx):
        now = time.perf_counter()
        if self._last_motion_counter is None or self._last_target_idx is None:
            self._last_motion_counter = now; self._last_target_idx = target_idx; self._last_motion_ips = 0.0; return
        dt = now - self._last_motion_counter
        if dt > 0: self._last_motion_ips = abs(target_idx - self._last_target_idx) / dt
        self._last_motion_counter = now; self._last_target_idx = target_idx

    def _adaptive_stride(self):
        ips = self._last_motion_ips
        if ips < 15:   return 1
        if ips < 40:   return 2
        if ips < 100:  return 3
        if ips < 250:  return 5
        if ips < 700:  return 10
        if ips < 1500: return 20
        return 40

    def _current_decode_side(self):
        ips = self._last_motion_ips
        if self._is_playing:
            return PLAY_MAX_SIDE_SLOW if (self._play_is_exact() and ips < 40) else PLAY_MAX_SIDE_FAST
        if self._is_scrubbing:
            return self._scrub_side if ips < 40 else FAST_SCRUB_MAX_SIDE
        return self._scrub_side

    # ================================================================ SLIDER <-> TIME
    def _slider_to_time_ns(self, v):
        if self.axis_max_ns <= self.axis_min_ns: return self.axis_min_ns
        return self.axis_min_ns + int((self.axis_max_ns - self.axis_min_ns) * (v / SLIDER_MAX))
    
    def _slider_to_index(self, v) -> int:
        if not self.items: return 0
        n = len(self.items)
        return max(0, min(n - 1, int(round(v / SLIDER_MAX * (n - 1)))))

    def _index_to_slider_value(self, idx) -> int:
        n = len(self.items)
        if n <= 1: return 0
        return max(0, min(SLIDER_MAX, int(idx / (n - 1) * SLIDER_MAX)))

    def _time_to_slider_value(self, t):
        if self.axis_max_ns <= self.axis_min_ns:
            return 0
        frac = (t - self.axis_min_ns) / (self.axis_max_ns - self.axis_min_ns)
        return max(0, min(SLIDER_MAX, int(frac * SLIDER_MAX)))

    def _time_to_nearest_index(self, t):
        i = bisect.bisect_left(self.ts_list, t)
        if i <= 0: return 0
        if i >= len(self.ts_list): return len(self.ts_list) - 1
        return i - 1 if (t - self.ts_list[i-1]) <= (self.ts_list[i] - t) else i

    def _set_info_for(self, idx, axis_time_ns):
        it = self.items[idx]
        if self._is_multi_cam() and self._cam_items:
            counts = " | ".join(
                f"{self._cam_names[i] if i < len(self._cam_names) else f'cam{i}'}: "
                f"{len(c)}"
                for i, c in enumerate(self._cam_items)
            )
            self.lbl_filename.setText(counts)
            self.lbl_index.setText(f"{idx+1} / {len(self.items)} (merged)")
        else:
            self.lbl_filename.setText(f"File: {it.path.name}")
            self.lbl_index.setText(f"{idx+1} / {len(self.items)}")
        self.lbl_axis_time.setText(f"Axis: {fmt_hhmmss_ms_from_ns(axis_time_ns)}")
        if self._real_ts_list and idx < len(self._real_ts_list):
            real_ts = self._real_ts_list[idx]
        else:
            real_ts = it.ts_ns
        energy_text = self._sf_energy_map.get(it.path.name, "")
        if energy_text:
            self.lbl_prague_time.setText(
                f"Prague: {fmt_prague_full_from_ns(real_ts)}\n{energy_text}")
        else:
            self.lbl_prague_time.setText(f"Prague: {fmt_prague_full_from_ns(real_ts)}")
        self.img_view.energy_text = energy_text
        self.img_view.update()

        # Update single-cam label (drawn by paintEvent in reserved strip below image)
        if not self._is_multi_cam():
            cam_name = self._cam_names[0] if self._cam_names else ""
            self.img_view.cam_label_text = cam_name
            self.img_view.cam_ts_text = fmt_prague_full_from_ns(real_ts)
            self.img_view.update()

        # Live replay: update pointing panel to show only points up to current timestamp.
        # Skip when navigating by clicking a graph point (would just redraw what's already shown).
        # Throttle during online auto-follow to avoid expensive mpl redraw on every frame.
        if (self.pointing_panel.isVisible() and self.pointing_panel._ts_int is not None
                and not getattr(self, '_pointing_nav_from_click', False)):
            if self._auto_follow:
                now_ms = int(time.time() * 1000)
                last = getattr(self, '_pointing_replay_last_ms', 0)
                if now_ms - last >= 500:
                    self._pointing_replay_last_ms = now_ms
                    self.pointing_panel.set_replay_ts(it.ts_ns)
            else:
                self.pointing_panel.set_replay_ts(it.ts_ns)

    # ================================================================ SCAN
    def _hard_reset_runtime(self):
        for t in [self.play_timer, self.scrub_timer, self._prefetch_debounce]:
            try:
                if t.isActive(): t.stop()
            except: pass
        self._is_playing = False; self._is_scrubbing = False; self.pending_slider = None
        self.play_time_ns = None; self.target_idx = None
        self._display_load_key = None; self._deferred_display = None
        self._reset_motion_tracking()

    def _reset_ui_for_new_scan(self, folders, folder_label):
        self._stop_online_mode()
        self._hard_reset_runtime()
        if hasattr(self, '_pointing_replay_timer') and self._pointing_replay_timer.isActive():
            self._pointing_replay_timer.stop()
        self.opened_folders = folders[:]; self.opened_folder = folders[0] if folders else None
        self.items = []; 
        self._real_ts_list = []
        self._fake_ts_map = None
        if hasattr(self, 'tickbar'):
            self.tickbar.discrete_ticks = None
            self.tickbar.discrete_tick_labels = None
        # _sf_energy_map se neresetuje zde — nastavuje ho sf.py před open_folder_path
        # self._sf_energy_map = {}
        self.ts_list = []; self.axis_min_ns = 0; self.axis_max_ns = 0
        self.current_idx = None
        self.lbl_prague_time.setText("Prague: —"); self.lbl_axis_time.setText("Axis: —")
        self.lbl_index.setText("0 / 0")
        self.cache = PixCache(CACHE_SIZE); self._display_req_id = 0
        self._inflight.clear(); self._want_display_req.clear()
        self.img_view.cam_label_text = ""
        self.img_view.cam_ts_text = ""
        self.img_view.clear()
        for w in [self.slider, self.btn_save, self.btn_save_range,
                self.btn_play, self.btn_stop, self.btn_prev, self.btn_next, self.btn_set_a,
                self.btn_set_b, self.btn_clear_marks, self.btn_cal_circle, self.btn_cal_square,
                self.btn_cal_cross, self.btn_pointing, self.btn_save_ts, self.btn_goto_ts,
                self.btn_set_ref]:
            w.setEnabled(False)
        # Preserve marks across rescan — they are revalidated against the new axis at scan-done time
        # (do not clear mark_a_ns / mark_b_ns here)
        self.tickbar.set_marks(None, None)
        fname = folders[0].name if len(folders) == 1 else (f"{folders[0].name} → {folders[-1].name}" if folders else "Multi-camera")
        self.lbl_filename.setText(f"File: {fname}  (scanning…)")
        self.lbl_prague_time.setText("Prague: —"); self.lbl_axis_time.setText("Axis: —")
        self.lbl_index.setText("0 / 0"); self.tickbar.set_axis(0, 0)
        self.prog.setVisible(True); self.prog.setRange(0, 0)
        self.lbl_scan_progress.setText("Scanning..."); self.btn_cancel_scan.setVisible(True)

    def _start_scan(self, folders, axis_override, folder_label):
        self._gen += 1; gen = self._gen
        if self._scan_task is not None:
            self._scan_task.cancel(); self._scan_task = None
        self.axis_override = axis_override
        # Uložit overlay stav single-cam img_view před resetem UI
        _saved_overlay = MultiCameraGrid._save_iv_overlay(self.img_view)
        self._reset_ui_for_new_scan(folders, folder_label)
        # Obnovit overlay stav po resetu (clear() vymaže obrázek, ne overlay data)
        MultiCameraGrid._restore_iv_overlay(self.img_view, _saved_overlay)
        task = ScanTask(gen, folders); self._scan_task = task
        task.signals.status.connect(self._on_scan_status)
        task.signals.progress.connect(self._on_scan_progress)
        task.signals.cancelled.connect(self._on_scan_cancelled)
        task.signals.quick_item.connect(self._on_scan_quick_item)
        task.signals.finished.connect(self._on_scan_finished)
        self.scan_pool.start(task)

    def cancel_scan(self):
        if self._scan_task is not None: self._scan_task.cancel()

    def _on_scan_quick_item(self, gen, item):
        """Show newest image found so far before the full scan finishes."""
        if gen != self._gen: return
        if self.current_idx is not None: return  # already displaying something
        # Minimal setup so we can decode and show this single item
        self.items = [item]
        self.ts_list = [item.ts_ns]
        self.current_idx = 0
        self._quick_item_shown = True
        self._display_req_id += 1
        req_id = self._display_req_id
        max_side = self._current_decode_side()
        brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        key = (0, max_side, brighten, gradient_id, self._brightness_offset, None, 0)
        self._want_display_req[key] = req_id
        self._inflight.add(key)
        task = LoadTask(gen, req_id, 0, item.path, max_side, brighten, gradient_id,
                        self.load_signals, self._brightness_offset)
        self.load_pool.start(task)

    def _on_scan_status(self, gen, text):
        if gen != self._gen: return
        self.lbl_filename.setText(text)

    def _on_scan_progress(self, gen, folder_i, total_folders, processed, found):
        if gen != self._gen: return
        self.lbl_scan_progress.setText(f"Folder {folder_i}/{total_folders} | Files: {processed} | Imgs: {found}")

    def _on_scan_cancelled(self, gen):
        if gen != self._gen: return
        self._scan_task = None; self.prog.setVisible(False)
        self.lbl_scan_progress.setText(""); self.btn_cancel_scan.setVisible(False)
        self.lbl_filename.setText("File: scan cancelled.")

    def _choose_axis(self, folder_axis, ts_min, ts_max):
        if folder_axis is not None:
            return folder_axis
        # Bez folder_axis — zkontroluj jestli je data span > 4h
        # Pokud ano (např. temp složka se snímky z různých dnů), použij
        # diskrétní osu přímo z timestampů (tight) bez zaokrouhlení na hodiny
        data_span_ns = ts_max - ts_min
        FOUR_HOURS_NS = 4 * 3_600_000_000_000
        if data_span_ns > FOUR_HOURS_NS:
            # Tight osa — malý padding kolem dat
            pad = max(data_span_ns // 20, 60_000_000_000)  # min 1 minuta
            return ts_min - pad, ts_max + pad
        # Normální případ — zaokrouhli na celé hodiny
        dt0 = floor_to_hour(_dt_from_ns(ts_min))
        dt1 = floor_to_hour(_dt_from_ns(ts_max))
        if dt1 == dt0:
            dt1 = dt0 + timedelta(hours=1)
        else:
            dt1 = dt1 + timedelta(hours=1)
        return ns_from_dt(dt0), ns_from_dt(dt1)

    def _on_scan_finished(self, gen, items):
        if gen != self._gen: return
        self._scan_task = None; self.prog.setVisible(False)
        self.lbl_scan_progress.setText(""); self.btn_cancel_scan.setVisible(False)
        self.items = items
        if not self.items:
            self.lbl_filename.setText("File: no images found."); self.tickbar.set_axis(0, 0); return
        self.ts_list = [it.ts_ns for it in self.items]
        ts_min, ts_max = self.ts_list[0], self.ts_list[-1]
        n = len(self.items)
        self._scrub_side = 500 if n >= 15000 else (600 if n >= 6000 else 900)
        if self.axis_override is not None:
            self.axis_min_ns, self.axis_max_ns = self.axis_override
            self.tickbar.discrete_ticks = None
            self.tickbar.discrete_tick_labels = None
        else:
            folder_axis = axis_from_any_folder(self.opened_folder) if self.opened_folder else None
            if folder_axis is not None:
                folder_span = folder_axis[1] - folder_axis[0]
                data_span   = ts_max - ts_min
                if n <= 100 and folder_span > data_span * 10:
                    folder_axis = None
            self.axis_min_ns, self.axis_max_ns = self._choose_axis(folder_axis, ts_min, ts_max)
            # Discrete mode: pokud snímků je málo a osa je příliš velká (různé dny),
            # přepni na index-based osu kde každý snímek má stejnou vzdálenost
            if self._discrete_mode and n <= 200:
                data_span_ns = ts_max - ts_min
                FOUR_HOURS_NS = 4 * 3_600_000_000_000
                if data_span_ns > FOUR_HOURS_NS:
                    fake_ts = [i * SLIDER_MAX // max(n - 1, 1) for i in range(n)]
                    self.axis_min_ns = 0
                    self.axis_max_ns = SLIDER_MAX
                    # Uložíme původní real timestampy pro display — fake jsou jen pro slider/osu
                    self._real_ts_list = self.ts_list[:]
                    self._fake_ts_map = {self.ts_list[i]: fake_ts[i] for i in range(n)}
                    self.ts_list = fake_ts
                    self.items = [Item(it.path, fake_ts[i]) for i, it in enumerate(self.items)]
                    # Ticky jsou na fake pozicích, ale labely jsou skutečné timestampy
                    self.tickbar.discrete_ticks = fake_ts
                    self.tickbar.discrete_tick_labels = [
                        f"{_dt_from_ns(ts):%Y-%m-%d %H:%M:%S}"
                        for ts in self._real_ts_list
                    ]
                    self._real_items_paths = [it.path for it in self.items]
                else:
                    self.tickbar.discrete_ticks = self.ts_list[:]
                    self._fake_ts_map = None
            else:
                self.tickbar.discrete_ticks = None
                self.tickbar.discrete_tick_labels = None
                self._fake_ts_map = None
        self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
        self._apply_marks_to_tickbar()
        self.slider.setEnabled(True); self.btn_save.setEnabled(True); self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(False); self.btn_prev.setEnabled(True); self.btn_next.setEnabled(True)
        self.btn_set_a.setEnabled(True); self.btn_set_b.setEnabled(True)
        self.btn_clear_marks.setEnabled(True); self.btn_cal_circle.setEnabled(True)
        self.btn_cal_square.setEnabled(True); self.btn_cal_cross.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.btn_pointing.setEnabled(True)
        self.btn_pointing_live.setEnabled(True)
        self._sc_set_enabled(True)
        self._btn_auto_follow.setEnabled(True)
        self.btn_set_ref.setEnabled(True)
        self._update_range_ui(); self._sync_overlay_checkboxes_from_iv(self.img_view); self.img_view.update()
        pending_online = getattr(self, '_pending_online_mode', False)
        if pending_online and self.items:
            last_idx = len(self.items) - 1
            sv = self._time_to_slider_value(self.items[last_idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
            self.play_time_ns = self.items[last_idx].ts_ns; self.target_idx = last_idx
            self._display_exact_index(last_idx, self.items[last_idx].ts_ns, update_slider=False)
        else:
            # If quick_item already showed the newest image, land on the newest (last) item
            # so the user sees the latest frame; otherwise start at index 0
            quick_showed = getattr(self, '_quick_item_shown', False)
            self._quick_item_shown = False
            if quick_showed:
                start_idx = len(self.items) - 1
                start_ts  = self.items[start_idx].ts_ns
                sv = self._time_to_slider_value(start_ts)
            else:
                start_idx = 0
                start_ts  = self.axis_min_ns
                sv = 0
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
            self.play_time_ns = start_ts; self.target_idx = start_idx
            self._display_exact_index(start_idx, start_ts, update_slider=False)
        self.btn_save_ts.setEnabled(True)
        self.btn_set_ref.setEnabled(True)
        if self._saved_timestamps:
            self.btn_goto_ts.setEnabled(True)
            self.btn_clear_ts.setEnabled(True)
            self.lbl_ts_status.setText(
                f"{len(self._saved_timestamps)} timestamp(s) saved.")
        if pending_online:
            self._pending_online_mode = False
            self._start_online_mode()
            self._btn_auto_follow.setChecked(True)
            if self.items:
                last_idx = len(self.items) - 1
                self._display_exact_index(last_idx, self.items[last_idx].ts_ns, update_slider=True)

    # ================================================================ SLIDER HANDLERS
    def _on_slider_pressed(self):
        self._is_scrubbing = True; self._prefetch_debounce.stop(); self._reset_motion_tracking()
        if self._is_playing: self.stop()
        self.pending_slider = self.slider.value()
        if not self.scrub_timer.isActive(): self.scrub_timer.start()

    def _on_slider_changed(self, v):
        if not self.items:
            self.pending_slider = v
            return
        t = self._slider_to_time_ns(v)
        idx = self._time_to_nearest_index(t)
        snapped = self._time_to_slider_value(self.items[idx].ts_ns)
        if snapped != v:
            self.slider.blockSignals(True)
            self.slider.setValue(snapped)
            self.slider.blockSignals(False)
        self.pending_slider = snapped

    def _apply_scrub(self):
        if self.pending_slider is None or not self.items: return
        t = self._slider_to_time_ns(self.pending_slider)
        idx = self._time_to_nearest_index(t)
        if idx == self.current_idx: return

        self._update_motion_speed(idx)

        # Multi-cam: each camera independently shows latest frame <= slider time
        if self._is_multi_cam():
            self.current_idx = idx
            self.target_idx = idx
            self.play_time_ns = self.items[idx].ts_ns
            self._set_info_for(idx, self.play_time_ns)
            self._display_multicam_index(idx, update_slider=False)
            return

        self.current_idx = idx
        self.target_idx = idx
        self.play_time_ns = self.items[idx].ts_ns
        self._set_info_for(idx, self.play_time_ns)

        max_side = FAST_SCRUB_MAX_SIDE if self._last_motion_ips >= 40 else self._scrub_side
        brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_image if (subtract and self._ref_image is not None) else None
        sub_thr = self.sub_threshold_sb.value() if (ref is not None) else 0
        key = (idx, max_side, brighten, gradient_id, self._brightness_offset, id(ref) if ref is not None else None, sub_thr)

        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            self.img_view.set_pixmap(cached)
            return

        self._display_req_id += 1
        self._want_display_req[key] = self._display_req_id

        if key not in self._inflight:
            self._inflight.add(key)
            self.load_pool.start(LoadTask(
                self._gen, self._display_req_id, idx,
                self.items[idx].path, max_side, brighten, gradient_id,
                self.load_signals, self._brightness_offset, ref, sub_thr))

    def _on_slider_released(self):
        if self.scrub_timer.isActive(): self.scrub_timer.stop()
        self._is_scrubbing = False
        # Pokud user posune slider, vypni auto-follow
        if self._auto_follow:
            self._btn_auto_follow.setChecked(False)
        if not self.items: return
        t = self._slider_to_time_ns(self.slider.value())
        idx = self._time_to_nearest_index(t)
        self._want_display_req.clear()
        if self._is_multi_cam():
            self._display_multicam_index(idx, update_slider=False)
        else:
            self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=False)
        self._schedule_prefetch_after_idle()
        self._reset_motion_tracking()

    def _schedule_prefetch_after_idle(self):
        if not self._is_playing: self._prefetch_debounce.start(140)

    def _run_prefetch_after_idle(self):
        if not self.items or self.current_idx is None: return
        if self._is_scrubbing or self._is_playing: return
        self._prefetch_idle(self.current_idx)

    # ================================================================ DISPLAY / LOADING
    def _load_or_cache(self, idx, max_side, brighten, req_id=0):
        gradient_id = self.gradient_cb.currentIndex()
        brightness_offset = self._brightness_offset
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_image if (subtract and self._ref_image is not None) else None
        sub_thr = self.sub_threshold_sb.value() if (ref is not None) else 0
        ref_id = id(ref) if ref is not None else None
        key = (idx, max_side, brighten, gradient_id, brightness_offset, ref_id, sub_thr)
        cached = self.cache.get(key)
        if cached is not None and not cached.isNull(): return cached
        if key not in self._inflight:
            self._inflight.add(key)
            self.load_pool.start(LoadTask(self._gen, req_id, idx, self.items[idx].path, max_side, brighten, gradient_id, self.load_signals, brightness_offset, ref, sub_thr))
        return None

    def _adaptive_index_step(self, target_idx):
        if self.current_idx is None: return target_idx
        delta = target_idx - self.current_idx
        if delta == 0: return target_idx
        stride = self._adaptive_stride()
        if self._play_is_exact() and (self._is_playing or self._is_scrubbing) and self._last_motion_ips < 30:
            stride = 1
        step = min(abs(delta), stride)
        return self.current_idx + (step if delta > 0 else -step)

    def _display_index(self, idx, axis_time_ns, update_slider):
        if not (0 <= idx < len(self.items)): return
        self.target_idx = idx; self.play_time_ns = axis_time_ns
        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._set_info_for(idx, axis_time_ns); self._request_display_target(idx, axis_time_ns, update_slider)

    def _display_exact_index(self, idx, axis_time_ns, update_slider):
        if not (0 <= idx < len(self.items)): return
        self.current_idx = idx; self.target_idx = idx; self.play_time_ns = axis_time_ns
        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._display_req_id += 1; rid = self._display_req_id
        self._set_info_for(idx, axis_time_ns); self._request_pixmap(idx, rid)

    def _display_multicam_at_time(self, time_ns: int, update_slider: bool = False):
        """Display for all cameras the latest frame with ts_ns <= time_ns."""
        if not self._is_multi_cam() or not self._cam_items:
            return
        if not self.items:
            return

        # Snap time_ns to nearest item in merged timeline
        idx = self._time_to_nearest_index(time_ns)
        idx = max(0, min(idx, len(self.items) - 1))
        self.current_idx = idx
        self.target_idx  = idx
        self.play_time_ns = self.items[idx].ts_ns

        if update_slider:
            sv = self._time_to_slider_value(self.play_time_ns)
            self.slider.blockSignals(True)
            self.slider.setValue(sv)
            self.slider.blockSignals(False)

        self._set_info_for(idx, self.play_time_ns)
        self._display_multicam_index(idx, update_slider=False)

    def _display_multicam_index(self, idx: int, update_slider: bool = False):
        """Display each camera's latest frame with ts_ns <= merged timeline ts at idx."""
        if not self._is_multi_cam() or not self._cam_items:
            return
        if not self.items:
            return

        idx = max(0, min(idx, len(self.items) - 1))
        self.current_idx = idx
        self.target_idx  = idx

        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True)
            self.slider.setValue(sv)
            self.slider.blockSignals(False)
            # Update per-cam sliders to match this timeline position
            t_ns = self.items[idx].ts_ns
            for cam_i, row in enumerate(self._per_cam_rows):
                cam_ts = self._cam_ts[cam_i] if cam_i < len(self._cam_ts) else []
                if not cam_ts:
                    continue
                frame_idx = max(0, bisect.bisect_right(cam_ts, t_ns) - 1)
                sv_cam = self._per_cam_ts_to_slider(cam_i, cam_ts[frame_idx])
                row.set_value(sv_cam)
            # Update tickbar cursor to master position (if a master is set)
            master = self._per_cam_master_idx
            if master >= 0 and master < len(self._cam_ts) and self._cam_ts[master]:
                cam_ts_m = self._cam_ts[master]
                fidx = max(0, bisect.bisect_right(cam_ts_m, t_ns) - 1)
                self.tickbar.set_cursor(cam_ts_m[fidx])

        self.play_time_ns = self.items[idx].ts_ns
        self._set_info_for(idx, self.play_time_ns)

        t_ns = self.items[idx].ts_ns
        brighten    = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract    = self.cb_subtract.isChecked()
        sub_thr     = self.sub_threshold_sb.value() if subtract else 0
        n_cams      = len(self._cam_items)

        for cam_i in range(n_cams):
            cam_items = self._cam_items[cam_i]
            if not cam_items:
                continue

            # Find latest frame in this camera with ts_ns <= t_ns
            cam_ts = self._cam_ts[cam_i] if (hasattr(self, '_cam_ts') and cam_i < len(self._cam_ts)) else None
            if cam_ts:
                pos = bisect.bisect_right(cam_ts, t_ns) - 1
                cam_idx = max(0, pos)
            else:
                cam_idx = min(idx, len(cam_items) - 1)
            it = cam_items[cam_idx]

            # Track which frame each camera is currently showing (for save)
            if hasattr(self, '_cam_current_idx') and cam_i < len(self._cam_current_idx):
                self._cam_current_idx[cam_i] = cam_idx

            # Per-camera reference
            ref = self._cam_ref_images[cam_i] if (subtract and cam_i < len(self._cam_ref_images)) else None
            ref_id = id(ref) if ref is not None else None
            effective_sub_thr = sub_thr if ref is not None else 0

            if n_cams >= 3:
                max_side = 400
            elif n_cams == 2:
                max_side = 500
            else:
                max_side = self._scrub_side

            boff = self._brightness_offset
            cache = self._cam_caches[cam_i]
            key = (cam_idx, max_side, brighten, gradient_id, boff, ref_id, effective_sub_thr)
            cached = cache.get(key)
            if cached is not None and not cached.isNull():
                iv = self._multi_grid.get_img_view(cam_i)
                if iv:
                    iv.set_pixmap(cached)
                self._multi_grid.set_cam_timestamp(cam_i, fmt_hhmmss_ms_from_ns(it.ts_ns))
                continue

            pool = self._cam_pools[cam_i]
            sig  = self._cam_signals[cam_i]
            self._display_req_id += 1
            pool.start(LoadTask(
                self._gen, self._display_req_id, cam_idx,
                it.path, max_side, brighten, gradient_id, sig, boff, ref, effective_sub_thr))

    def _on_cam_loaded(
        self, cam_i: int,
        gen: int, req_id: int, idx: int,
        max_side: int, brighten: int, gradient_id: int,
        brightness_offset: int, img: QImage
    ):
        """Callback pro načtený snímek jedné kamery v multi-cam módu."""
        if gen != self._gen or img.isNull():
            return
        # Reconstruct cache key using current per-camera ref (image already has subtraction baked in)
        subtract = self.cb_subtract.isChecked()
        ref = self._cam_ref_images[cam_i] if (subtract and cam_i < len(self._cam_ref_images)) else None
        ref_id = id(ref) if ref is not None else None
        sub_thr = self.sub_threshold_sb.value() if ref is not None else 0
        key = (idx, max_side, brighten, gradient_id, brightness_offset, ref_id, sub_thr)
        if cam_i < len(self._cam_caches):
            self._cam_caches[cam_i].put(key, QPixmap.fromImage(img))
        iv = self._multi_grid.get_img_view(cam_i)
        if iv:
            iv.set_pixmap(QPixmap.fromImage(img))
        if (cam_i < len(self._cam_items) and idx < len(self._cam_items[cam_i])):
            ts = self._cam_items[cam_i][idx].ts_ns
            self._multi_grid.set_cam_timestamp(cam_i, fmt_hhmmss_ms_from_ns(ts))

    def _request_display_target(self, idx, axis_time_ns, update_slider):
        max_side = self._current_decode_side(); brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_image if (subtract and self._ref_image is not None) else None
        sub_thr = self.sub_threshold_sb.value() if (ref is not None) else 0
        key = (idx, max_side, brighten, gradient_id, self._brightness_offset, id(ref) if ref is not None else None, sub_thr)
        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            self._display_req_id += 1; self.current_idx = idx
            self.img_view.set_pixmap(cached)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True); return
        if self._display_load_key is not None and self._display_load_key != key:
            self._deferred_display = (idx, axis_time_ns, update_slider); return
        self._display_req_id += 1; req_id = self._display_req_id
        self._want_display_req[key] = req_id; self._display_load_key = key
        pm = self._load_or_cache(idx, max_side, brighten, req_id)
        if pm is not None:
            self.current_idx = idx; self.img_view.set_pixmap(pm)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True)
            self._display_load_key = None

    def _drain_deferred_display(self):
        if self._display_load_key is not None or self._deferred_display is None: return
        idx, axis_time_ns, update_slider = self._deferred_display; self._deferred_display = None
        if not (0 <= idx < len(self.items)): return
        self.target_idx = idx; self.play_time_ns = axis_time_ns
        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._set_info_for(idx, axis_time_ns); self._request_display_target(idx, axis_time_ns, update_slider)

    def _request_pixmap(self, idx, req_id):
        max_side = self._current_decode_side(); brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_image if (subtract and self._ref_image is not None) else None
        sub_thr = self.sub_threshold_sb.value() if (ref is not None) else 0
        key = (idx, max_side, brighten, gradient_id, self._brightness_offset, id(ref) if ref is not None else None, sub_thr)
        self._want_display_req[key] = req_id
        pm = self._load_or_cache(idx, max_side, brighten, req_id)
        if pm is not None and self.target_idx == idx and req_id == self._display_req_id:
            self.current_idx = idx; self.img_view.set_pixmap(pm)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True)

    def _prefetch_idle(self, idx):
        if len(self._inflight) > 10: return
        max_side = self._scrub_side; brighten = 1 if self.cb_bright.isChecked() else 0
        for j in range(idx - PREFETCH_RADIUS_IDLE, idx + PREFETCH_RADIUS_IDLE + 1):
            if j != idx and 0 <= j < len(self.items):
                self._load_or_cache(j, max_side, brighten)

    def _prefetch_playish(self, idx):
        if self._last_motion_ips >= 120 or len(self._inflight) > 4: return
        max_side = self._current_decode_side(); brighten = 1 if self.cb_bright.isChecked() else 0
        ahead = 1 if self._last_motion_ips >= 40 else PREFETCH_AHEAD_PLAY
        for j in range(idx + 1, min(len(self.items), idx + 1 + ahead)):
            self._load_or_cache(j, max_side, brighten)

    def _on_loaded(self, gen, req_id, idx, max_side, brighten, gradient_id, brightness_offset, img):
        if gen != self._gen: return
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_image if (subtract and self._ref_image is not None) else None
        sub_thr = self.sub_threshold_sb.value() if (ref is not None) else 0
        key = (idx, max_side, brighten, gradient_id, brightness_offset, id(ref) if ref is not None else None, sub_thr)
        self._inflight.discard(key)
        if img.isNull():
            if self._display_load_key == key: self._display_load_key = None; self._drain_deferred_display()
            return
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            if self._display_load_key == key: self._display_load_key = None; self._drain_deferred_display()
            return
        self.cache.put(key, pm)
        want_req = self._want_display_req.pop(key, None)
        if want_req is not None:
            self.current_idx = idx
            self.img_view.set_pixmap(pm)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True)
            # Pokud auto-follow, ujistíme se že slider je na správné pozici
            if self._auto_follow and self._online_mode and self.items and idx < len(self.items):
                sv = self._time_to_slider_value(self.items[idx].ts_ns)
                self.slider.blockSignals(True)
                self.slider.setValue(sv)
                self.slider.blockSignals(False)
        if self._display_load_key == key: self._display_load_key = None; self._drain_deferred_display()

    # ================================================================ AUTOPLAY
    def play(self):
        if not self.items: return
        if self.mark_a_ns is not None:
            self.play_time_ns = self.mark_a_ns
            sv = self._time_to_slider_value(self.mark_a_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        else:
            self.play_time_ns = self._slider_to_time_ns(self.slider.value())
        self._is_playing = True; self._is_scrubbing = False; self._reset_motion_tracking()
        if self.current_idx is None:
            self._display_exact_index(self._time_to_nearest_index(self.play_time_ns), self.play_time_ns, False)
        self.btn_play.setEnabled(False); self.btn_stop.setEnabled(True); self.play_timer.start()

    def stop(self):
        self._is_playing = False
        if self.play_timer.isActive(): self.play_timer.stop()
        self.btn_play.setEnabled(bool(self.items)); self.btn_stop.setEnabled(False)
        if self.items and self.current_idx is not None:
            self._display_exact_index(self.current_idx, self.items[self.current_idx].ts_ns, True)
        self._schedule_prefetch_after_idle(); self._reset_motion_tracking()

    def _autoplay_step(self):
        if not self._is_playing or not self.items: return
        if self.current_idx is None: self.stop(); return

        n = len(self.items)
        pct = self._current_play_pct_per_s()

        self._play_frame_acc += (pct / 100.0) * n * (PLAY_TICK_MS / 1000.0)
        if self._play_frame_acc < 1.0:
            return

        # Kolik snímků máme přeskočit — max 50 (dostatečné i pro 20%/s na 3000 snímcích)
        skip = min(int(self._play_frame_acc), 50)
        self._play_frame_acc -= skip

        end_idx = n - 1
        if self.mark_b_ns is not None:
            end_idx = min(n - 1, self._time_to_nearest_index(self.mark_b_ns))
        new_idx = min(end_idx, self.current_idx + skip)
        if new_idx >= end_idx:
            self._play_show(new_idx)
            self.stop()
            return

        self._play_show(new_idx)

    def _play_show(self, new_idx: int):
        """Zobrazí snímek při přehrávání — z cache nebo spustí load."""
        self.current_idx = new_idx
        self.target_idx = new_idx
        self.play_time_ns = self.items[new_idx].ts_ns

        sv = self._time_to_slider_value(self.items[new_idx].ts_ns)
        self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._set_info_for(new_idx, self.play_time_ns)

        max_side = PLAY_MAX_SIDE_FAST
        brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_image if (subtract and self._ref_image is not None) else None
        sub_thr = self.sub_threshold_sb.value() if (ref is not None) else 0
        key = (new_idx, max_side, brighten, gradient_id, self._brightness_offset, id(ref) if ref is not None else None, sub_thr)

        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            self.img_view.set_pixmap(cached)
            return

        self._display_req_id += 1
        self._want_display_req[key] = self._display_req_id
        if key not in self._inflight:
            if len(self._inflight) < 6:
                self._inflight.add(key)
                self.load_pool.start(LoadTask(
                    self._gen, self._display_req_id, new_idx,
                    self.items[new_idx].path, max_side, brighten, gradient_id,
                    self.load_signals, self._brightness_offset, ref))

    # ================================================================ STEP FRAME
    def step_frame(self, delta_idx):
        if self._is_playing: self.stop()
        # Per-cam slider mode: step the master camera, sync slaves
        if self._is_multi_cam() and self._per_cam_rows:
            self._per_cam_step(delta_idx)
            return
        if not self.items: return
        j = (self.current_idx + delta_idx) if self.current_idx is not None \
            else self._time_to_nearest_index(self._slider_to_time_ns(self.slider.value()))
        j = max(0, min(len(self.items) - 1, j))
        if self._is_multi_cam():
            self._display_multicam_index(j, update_slider=True)
        else:
            self._display_exact_index(j, self.items[j].ts_ns, update_slider=True)
        self._schedule_prefetch_after_idle()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:  self.step_frame(-1); return
        if event.key() == Qt.Key.Key_Right: self.step_frame(+1); return
        if event.key() == Qt.Key.Key_F11:
            self._toggle_watcher_mode(); return
        if event.key() == Qt.Key.Key_Escape and self._watcher_mode:
            self._toggle_watcher_mode(); return
        super().keyPressEvent(event)

    def _toggle_watcher_mode(self):
        self._watcher_mode = not self._watcher_mode
        show = not self._watcher_mode
        # Show/hide left panel, slider, tickbar
        self._left_col.setVisible(show)
        # In multi-cam mode the global slider is replaced by per-cam sliders — don't restore it
        in_multi = self._is_multi_cam() and bool(self._per_cam_rows)
        self.slider.setVisible(show and not in_multi)
        self.tickbar.setVisible(show and not in_multi)
        self._per_cam_container.setVisible(show and in_multi)
        # Hide/show tab bar and status bar (they live in the main window)
        win = self.window()
        from PySide6.QtWidgets import QTabWidget, QStatusBar
        tab_w = win.findChild(QTabWidget)
        if tab_w is not None:
            tab_w.tabBar().setVisible(show)
        sb = win.findChild(QStatusBar)
        if sb is not None:
            sb.setVisible(show)
        # In watcher mode remove all borders/margins so image fills the screen edge-to-edge
        if self._watcher_mode:
            self._root_layout.setContentsMargins(0, 0, 0, 0)
            self._root_layout.setSpacing(0)
            if tab_w is not None:
                tab_w.setStyleSheet("QTabWidget::pane { border: none; margin: 0; padding: 0; }")
            win.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.setContentsMargins(0, 0, 0, 0)
        else:
            self._root_layout.setContentsMargins(8, 8, 8, 8)
            self._root_layout.setSpacing(8)
            if tab_w is not None:
                tab_w.setStyleSheet("")   # restore global stylesheet
            win.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.setContentsMargins(0, 0, 0, 0)
        # Install/remove key event filter on the window so ESC works even when
        # a child widget has focus
        if self._watcher_mode:
            win.installEventFilter(self)
            win.showFullScreen()
        else:
            win.removeEventFilter(self)
            win.showMaximized()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if (self._watcher_mode
                and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape):
            self._toggle_watcher_mode()
            return True
        return super().eventFilter(obj, event)

    # ================================================================ TIMESTAMPS
    def _save_current_timestamp(self):
        if self.current_idx is None or not self.items: return
        ts_ns = self.items[self.current_idx].ts_ns
        label = fmt_prague_full_from_ns(ts_ns)

        # Zkontroluj duplicity (±10ms)
        for existing_ts, _ in self._saved_timestamps:
            if abs(existing_ts - ts_ns) < 10_000_000:
                self.lbl_ts_status.setText(f"Already saved: {label}")
                return

        self._saved_timestamps.append((ts_ns, label))
        self.btn_goto_ts.setEnabled(True)
        self.btn_clear_ts.setEnabled(True)
        n = len(self._saved_timestamps)
        self.lbl_ts_status.setText(
            f"{n} timestamp{'s' if n > 1 else ''} saved.\nLast: {label}")

    def _goto_saved_timestamp(self):
        if not self._saved_timestamps or not self.items: return

        if len(self._saved_timestamps) == 1:
            target_ts, label = self._saved_timestamps[0]
        else:
            # Vyber ze seznamu
            from PySide6.QtWidgets import QInputDialog
            labels = [f"{i+1}: {lbl}" for i, (_, lbl) in
                      enumerate(self._saved_timestamps)]
            choice, ok = QInputDialog.getItem(
                self, "Go to Timestamp",
                "Select saved timestamp:", labels, 0, False)
            if not ok: return
            idx = labels.index(choice)
            target_ts, label = self._saved_timestamps[idx]

        # Najdi nejbližší snímek
        best_idx = self._time_to_nearest_index(target_ts)
        best_ts = self.items[best_idx].ts_ns
        diff_ms = abs(best_ts - target_ts) / 1_000_000

        self._display_exact_index(best_idx, best_ts, update_slider=True)
        self.lbl_ts_status.setText(
            f"Jumped to: {fmt_prague_full_from_ns(best_ts)}\n"
            f"Target:    {label}\n"
            f"Δt = {diff_ms:.1f} ms")

    def _clear_timestamps(self):
        self._saved_timestamps.clear()
        self.btn_goto_ts.setEnabled(False)
        self.btn_clear_ts.setEnabled(False)
        self.lbl_ts_status.setText("No timestamps saved.")

    def run_pointing_analysis(self):
        if not self.items: return

        # Vyber snímky — mezi marky nebo všechny
        if self.mark_a_ns is not None and self.mark_b_ns is not None:
            a, b = min(self.mark_a_ns, self.mark_b_ns), max(self.mark_a_ns, self.mark_b_ns)
            i0 = bisect.bisect_left(self.ts_list, a)
            i1 = bisect.bisect_right(self.ts_list, b)
            items = self.items[i0:i1]
        else:
            items = self.items

        if not items:
            QMessageBox.information(self, "Pointing Analysis", "No images to analyse."); return

        M = self.pointing_m_sb.value()
        pixel_mm = 4.5e-3 * M
        threshold = self.pointing_threshold_sb.value()

        # Stop replay if it's running before starting new analysis
        if self.btn_pointing_live.isChecked():
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            self._stop_pointing_replay()

        self.btn_pointing.setEnabled(False)
        self.btn_pointing_cancel.setVisible(True)
        total = len(items)
        if total > 20000:
            step = total // 5000
        elif total > 5000:
            step = total // 3000
        elif total > 1000:
            step = total // 1000
        else:
            step = 1
        n_sampled = len(range(0, total, step))
        status = f"Analysing {n_sampled} / {total} frames"
        if step > 1:
            status += f" (every {step}th frame)"
        self.lbl_pointing_status.setText(status)

        signals = PointingAnalysisSignals()
        signals.progress.connect(self._on_pointing_progress)
        signals.finished.connect(self._on_pointing_finished)
        signals.cancelled.connect(self._on_pointing_cancelled)

        task = PointingAnalysisTask(items, threshold, pixel_mm, signals)
        self._pointing_task = task
        self._set_busy(True)
        self.analysis_pool.start(task)

    def _cancel_pointing(self):
        if self._pointing_task is not None:
            self._pointing_task.cancel()

    def _on_pointing_progress(self, done, total):
        self.lbl_pointing_status.setText(f"Analysing {done} / {total}…")

    def _on_pointing_cancelled(self):
        self._pointing_task = None
        self._set_busy(False)
        self.btn_pointing.setEnabled(bool(self.items))
        self._sc_set_enabled(bool(self.items) or bool(self._cam_names))
        self.btn_pointing_cancel.setVisible(False)
        self.lbl_pointing_status.setText("Cancelled.")

    def _on_pointing_finished(self, results):
        self._pointing_task = None
        self._set_busy(False)
        self.btn_pointing.setEnabled(bool(self.items))
        self.btn_pointing_live.setEnabled(bool(self.items))
        self._sc_set_enabled(bool(self.items) or bool(self._cam_names))
        self.btn_pointing_cancel.setVisible(False)

        if not results:
            self.lbl_pointing_status.setText("No results — try lowering the threshold."); return

        ts_arr  = np.array([r[0] for r in results], dtype=np.int64)
        # r[1], r[2] are centroid offsets from image centre in original pixels
        cx_px   = np.array([r[1] for r in results])
        cy_px   = np.array([r[2] for r in results])
        # Y axis: pixel Y grows downward, keep as-is so graph matches image orientation
        # (positive Y = beam below centre, negative Y = above centre)

        n = len(results)
        sx = float(np.std(cx_px))
        sy = float(np.std(cy_px))
        self.lbl_pointing_status.setText(
            f"{n} shots  σX={sx:.1f} px  σY={sy:.1f} px")

        self.pointing_panel.setVisible(True)
        self.pointing_panel.plot(cx_px, cy_px, n,
                                  ts_ns=ts_arr.astype(np.float64),
                                  ts_ns_int=ts_arr)
        self.btn_pointing_save.setEnabled(True)
        self.btn_pointing_path.setEnabled(True)
        self.btn_pointing_path.setText("〰 Show Path")
        self.btn_pointing_close.setEnabled(True)
        self.btn_pointing_select.setEnabled(True)
        self.btn_pointing_restore.setEnabled(False)

    def _on_pointing_live_toggled(self, checked: bool):
        if checked:
            self.btn_pointing_live.setText("⏹ Stop")
            self.btn_pointing_live.setStyleSheet(
                "background-color: #c00; color: white; font-weight: bold;")
            self._start_pointing_replay()
        else:
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            self._stop_pointing_replay()

    def _start_pointing_replay(self):
        """Start stepping through pointing analysis results frame by frame."""
        panel = self.pointing_panel
        if panel._ts_int is None or len(panel._ts_int) == 0:
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            return
        # Start from index 0 in the pointing results
        self._pointing_replay_idx = 0
        if not hasattr(self, '_pointing_replay_timer'):
            self._pointing_replay_timer = QTimer(self)
            self._pointing_replay_timer.timeout.connect(self._pointing_replay_step)
        fps = max(0.1, self._pointing_replay_fps_sb.value() / 100.0 * 200.0)
        self._pointing_replay_timer.start(int(1000 / fps))

    def _stop_pointing_replay(self):
        if hasattr(self, '_pointing_replay_timer'):
            self._pointing_replay_timer.stop()
        # Show all points again
        self.pointing_panel.set_replay_ts(None)

    def _pointing_replay_step(self):
        """Advance one step in the pointing replay."""
        panel = self.pointing_panel
        if panel._ts_int is None:
            self._stop_pointing_replay()
            return
        idx = getattr(self, '_pointing_replay_idx', 0)
        n = len(panel._ts_int)
        if idx >= n:
            # Replay finished — stop and reset button
            self._pointing_replay_timer.stop()
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            return
        ts_ns = int(panel._ts_int[idx])
        # Show image for this timestamp
        if self.items and self.ts_list is not None:
            img_idx = self._time_to_nearest_index(ts_ns)
            if 0 <= img_idx < len(self.items):
                self._pointing_nav_from_click = True
                self._display_exact_index(img_idx, self.items[img_idx].ts_ns, update_slider=True)
                self._pointing_nav_from_click = False
        # Update replay timestamp on panel to show points up to this one
        panel.set_replay_ts(ts_ns)
        self._pointing_replay_idx = idx + 1
        # Update timer interval in case fps changed
        fps = max(0.1, self._pointing_replay_fps_sb.value() / 100.0 * 200.0)
        self._pointing_replay_timer.setInterval(int(1000 / fps))

    def _save_pointing_plot(self):
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save pointing plot", "pointing_stability.png",
            "PNG Images (*.png);;PDF (*.pdf)")
        if not dst: return
        try:
            self.pointing_panel.save_figure(dst)
            QMessageBox.information(self, "Saved", f"Plot saved to:\n{dst}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _toggle_pointing_path(self):
        showing = self.pointing_panel.toggle_path()
        self.btn_pointing_path.setText(
            "〰 Hide Path" if showing else "〰 Show Path")

    def _close_pointing_panel(self):
        # Stop replay if running
        if self.btn_pointing_live.isChecked():
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            self._stop_pointing_replay()
        self.pointing_panel.setVisible(False)
        self.btn_pointing_close.setEnabled(False)
        self.btn_pointing_path.setEnabled(False)
        self.btn_pointing_save.setEnabled(False)
        self.btn_pointing_select.setEnabled(False)
        self.btn_pointing_restore.setEnabled(False)
        self.btn_pointing_path.setText("〰 Show Path")

    def _toggle_pointing_select(self, checked: bool):
        if checked:
            active = self.pointing_panel.toggle_select_mode()
            if not active:
                self.btn_pointing_select.setChecked(False)
        else:
            if self.pointing_panel._select_mode:
                self.pointing_panel.toggle_select_mode()

    def _on_pointing_region_deleted(self):
        self.btn_pointing_select.setChecked(False)
        # update status label with new N
        panel = self.pointing_panel
        if panel._mask is not None and panel._cx is not None:
            n = int(panel._mask.sum())
            cx_v = panel._cx[panel._mask]
            cy_v = panel._cy[panel._mask]
            sx = float(np.std(cx_v)) if n else 0.0
            sy = float(np.std(cy_v)) if n else 0.0
            self.lbl_pointing_status.setText(
                f"{n} shots  σX={sx:.2f} µrad  σY={sy:.2f} µrad")
        n_deleted = int((~panel._mask).sum()) if panel._mask is not None else 0
        self.btn_pointing_restore.setEnabled(n_deleted > 0)

    def _restore_pointing_points(self):
        self.pointing_panel.restore_all_points()

    # ── Spatial Contrast ─────────────────────────────────────────────────────

    def _sc_set_enabled(self, enabled: bool):
        self._btn_sc_measure.setEnabled(enabled)
        self._btn_sc_auto_thr.setEnabled(enabled)
        self._btn_sc_hist.setEnabled(enabled)
        self._btn_sc_draw.setEnabled(enabled)

    def _on_sc_threshold_changed(self, value: int):
        """Re-run preview immediately when threshold changes (if a preview exists)."""
        if self._sc_preview_pixmap is not None:
            if self._sc_task_running:
                self._sc_pending = True   # queue a re-run for when current task finishes
            else:
                self._run_spatial_contrast()

    def _run_sc_auto_threshold(self):
        """Compute Otsu threshold from the current image and update the spinbox."""
        if self._sc_task_running:
            return
        img_path = self._sc_current_image_path()
        if img_path is None:
            return
        try:
            from PIL import Image as _PIL
            pil = _PIL.open(str(img_path))
            if pil.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(pil.convert("I"), dtype=np.float32).copy()
            else:
                arr = np.asarray(pil.convert("L"), dtype=np.float32).copy()
            arr_max = float(arr.max())
            bit_depth = 65535.0 if arr_max > 255 else 255.0
            thr_raw = _SCTask.otsu_threshold_raw(arr, bit_depth)
            # Block signal to avoid triggering re-measurement during set
            self._sc_threshold_sb.blockSignals(True)
            self._sc_threshold_sb.setValue(thr_raw)
            self._sc_threshold_sb.blockSignals(False)
        except Exception:
            return
        # Now run measurement with the new threshold
        self._run_spatial_contrast()

    def _open_sc_histogram(self):
        """Open the manual-threshold histogram dialog for the current image."""
        img_path = self._sc_current_image_path()
        if img_path is None:
            return
        current_thr = self._sc_threshold_sb.value()
        try:
            from PIL import Image as _PIL
            pil = _PIL.open(str(img_path))
            arr_max = float(np.asarray(pil).max())
            bit_depth = 65535 if arr_max > 255 else 255
        except Exception:
            bit_depth = 65535
        dlg = _SCHistogramDialog(img_path, current_thr, bit_depth, self)

        # Debounce timer — re-runs SC 500 ms after last drag movement
        _debounce = QTimer(self)
        _debounce.setSingleShot(True)
        _debounce.setInterval(500)
        _pending_thr = [current_thr]

        def _on_preview(new_thr: int):
            _pending_thr[0] = new_thr
            self._sc_threshold_sb.blockSignals(True)
            self._sc_threshold_sb.setValue(new_thr)
            self._sc_threshold_sb.blockSignals(False)
            _debounce.start()   # reset timer on every change

        def _on_debounce_fire():
            if not self._sc_task_running:
                self._run_spatial_contrast()
            else:
                self._sc_pending = True

        _debounce.timeout.connect(_on_debounce_fire)
        dlg.threshold_preview.connect(_on_preview)

        def _on_accepted(new_thr: int):
            _debounce.stop()
            self._sc_threshold_sb.blockSignals(True)
            self._sc_threshold_sb.setValue(new_thr)
            self._sc_threshold_sb.blockSignals(False)
            self._run_spatial_contrast()

        dlg.threshold_accepted.connect(_on_accepted)
        dlg.exec()
        _debounce.stop()

    def _open_sc_exclusion_editor(self):
        """Open the exclusion-region painter dialog."""
        if self._sc_preview_pixmap is None:
            # Need a preview first — run measurement to generate one
            self._run_spatial_contrast()
            self._sc_status_lbl.setText("Run Measure first to generate a preview.")
            return
        img_path = self._sc_current_image_path()
        if img_path is None:
            return
        # Determine original image size
        try:
            from PIL import Image as _PIL
            with _PIL.open(str(img_path)) as _p:
                orig_w, orig_h = _p.size
        except Exception:
            orig_w, orig_h = self._sc_preview_pixmap.width(), self._sc_preview_pixmap.height()

        existing = self._sc_exclusion_mask if self._sc_exclusion_path == img_path else None
        dlg = _SCExclusionEditor(self._sc_preview_pixmap, (orig_h, orig_w),
                                  img_path=img_path, existing_mask=existing, parent=self)
        dlg.exclusion_confirmed.connect(self._on_sc_exclusion_set)
        dlg.exec()

    def _on_sc_exclusion_set(self, mask: "np.ndarray | None"):
        self._sc_exclusion_mask = mask
        self._sc_exclusion_path = self._sc_current_image_path()  # tie mask to current image
        if mask is not None:
            n = int(mask.sum())
            self._sc_status_lbl.setText(f"Exclusion: {n:,} px masked — click Measure to recompute.")
        else:
            self._sc_status_lbl.setText("Exclusion cleared.")
        # Auto re-run measurement with new exclusion
        self._run_spatial_contrast()

    def _sc_cam_idx(self) -> int:
        """Return the camera index to use for SC measurement (from combo or grid selection)."""
        if (hasattr(self, '_sc_cam_row_widget') and self._sc_cam_row_widget.isVisible()
                and hasattr(self, '_sc_cam_combo') and self._sc_cam_combo.count() > 0):
            return self._sc_cam_combo.currentIndex()
        return self._multi_grid.selected_cam_index()

    def _sc_current_image_path(self) -> "Path | None":
        """Return the Path of the image currently shown in the selected camera."""
        if self._cam_items:
            idx = self._sc_cam_idx()
            items = self._cam_items[idx] if idx < len(self._cam_items) else []
            if not items:
                return None
            # Prefer the frame that matches current slider time for this camera
            if self.play_time_ns is not None and hasattr(self, '_cam_ts') and idx < len(self._cam_ts):
                cam_ts = self._cam_ts[idx]
                pos = bisect.bisect_right(cam_ts, self.play_time_ns) - 1
                cur_i = max(0, pos)
            elif hasattr(self, '_cam_current_idx') and idx < len(self._cam_current_idx):
                cur_i = self._cam_current_idx[idx]
            else:
                cur_i = len(items) - 1  # fall back to latest
            cur_i = max(0, min(cur_i, len(items) - 1))
            return items[cur_i].path
        else:
            if not self.items:
                return None
            if self.current_idx is not None:
                cur_i = max(0, min(self.current_idx, len(self.items) - 1))
            else:
                cur_i = 0
            return self.items[cur_i].path

    def _sc_current_cam_name(self) -> str:
        """Return the name of the camera currently used for SC measurement."""
        if self._cam_items:
            idx = self._sc_cam_idx()
            if idx < len(self._cam_names):
                return self._cam_names[idx]
        return ""

    def _run_spatial_contrast(self):
        if self._sc_task_running:
            self._sc_pending = True   # will re-run with latest image once current finishes
            return
        self._sc_pending = False
        img_path = self._sc_current_image_path()
        if img_path is None:
            self._sc_status_lbl.setText("No image loaded.")
            return
        self._sc_active_cam_name = self._sc_current_cam_name()
        if self._sc_active_cam_name:
            self._sc_cam_lbl.setText(f"Camera: {self._sc_active_cam_name}")

        # Reset exclusion mask when image changes
        if img_path != self._sc_exclusion_path:
            self._sc_exclusion_mask = None
            self._sc_exclusion_path = None

        threshold = self._sc_threshold_sb.value()

        self._sc_task_running = True
        self._sc_set_enabled(False)
        self._sc_status_lbl.setText("Measuring…")

        sig = _SCSignals()
        sig.preview.connect(self._on_sc_preview)
        sig.finished.connect(self._on_sc_finished)
        task = _SCTask(img_path, threshold, sig, exclusion_mask=self._sc_exclusion_mask)
        self.scan_pool.start(task)

    def _on_sc_preview(self, pm: "QPixmap"):
        self._sc_preview_pixmap = pm
        self._sc_preview_lbl.show()
        self._sc_preview_lbl.set_full_pixmap(pm)

    def _update_sc_preview(self):
        if self._sc_preview_pixmap is not None:
            self._sc_preview_lbl.set_full_pixmap(self._sc_preview_pixmap)

    def _on_sc_finished(self, result: dict):
        self._sc_task_running = False
        self._sc_set_enabled(True)
        # If Measure was clicked (or threshold moved) while task was running, re-run now
        if getattr(self, '_sc_pending', False):
            self._sc_pending = False
            self._run_spatial_contrast()
            return

        if result is None:
            self._sc_status_lbl.setText("Error: could not open image.")
            return

        err = result.get("error")
        if err:
            self._sc_status_lbl.setText(err)
            self._sc_val_mean.setText("—")
            self._sc_val_min.setText("—")
            self._sc_val_max.setText("—")
            self._sc_val_sc.setText("—")
            self._sc_val_beam.setText("—")
            return

        bd = result.get("bit_depth", 255)
        cam_lbl = getattr(self, '_sc_active_cam_name', '')
        status = f"{cam_lbl}  ({bd}-bit)" if cam_lbl else f"({bd}-bit)"
        self._sc_status_lbl.setText(status)
        self._sc_val_mean.setText(f"{result['mean']:.1f}")
        self._sc_val_min.setText(f"{result['min']:.1f}")
        self._sc_val_max.setText(f"{result['max']:.1f}")

        sc = result["sc"]
        self._sc_val_sc.setText(f"{sc:.4f}" if sc != float("inf") else "∞")

        n_beam  = result["n_beam"]
        n_total = result["n_total"]
        pct = 100.0 * n_beam / n_total if n_total > 0 else 0.0
        self._sc_val_beam.setText(f"{n_beam:,} ({pct:.1f}%)")

        # Store top-N data and trigger overlay repaint
        self._sc_topn_points = list(zip(
            result.get("top_xs", []),
            result.get("top_ys", [])
        ))
        self._sc_topn_img_shape = result.get("img_shape", None)
        self._update_sc_topn_overlay()

    def _on_sc_topn_changed(self, _val):
        self._update_sc_topn_overlay()

    def _on_sc_marker_style_changed(self, _val=None):
        iv = getattr(self, 'img_view', None)
        if iv is None:
            return
        iv.sc_topn_marker_radius = self._sc_marker_r_sb.value()
        iv.sc_topn_marker_thick  = self._sc_marker_thick_sb.value()
        iv.update()

    def _update_sc_topn_overlay(self):
        """Draw top-N intensity pixel markers on img_view."""
        n = self._sc_topn_sb.value()
        pts = getattr(self, '_sc_topn_points', None)
        shape = getattr(self, '_sc_topn_img_shape', None)
        iv = self.img_view
        if n == 0 or not pts or shape is None:
            iv.sc_topn_points_norm = None
        else:
            h, w = shape
            if w > 0 and h > 0:
                iv.sc_topn_points_norm = [
                    (px / w, py / h) for px, py in pts[:n]
                ]
            else:
                iv.sc_topn_points_norm = None
        iv.update()

    def _on_pointing_point_clicked(self, orig_idx: int):
        """Navigate slider to the timestamp of the clicked pointing point.
        Does NOT update the pointing graph replay — only the image changes."""
        panel = self.pointing_panel
        if panel._ts_int is None or orig_idx >= len(panel._ts_int):
            return
        ts_ns = int(panel._ts_int[orig_idx])
        if not self.items or self.ts_list is None:
            return
        idx = self._time_to_nearest_index(ts_ns)
        if 0 <= idx < len(self.items):
            self._pointing_nav_from_click = True
            self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)
            self._pointing_nav_from_click = False

    def _current_master_ts_ns(self) -> int | None:
        """Return current timestamp of the master camera (single-cam or multi-cam)."""
        if self._is_multi_cam():
            master = self._per_cam_master_idx
            if master >= 0 and master < len(self._cam_ts) and self._cam_ts[master]:
                row = self._per_cam_rows[master] if master < len(self._per_cam_rows) else None
                if row is None:
                    return None
                v = row.value()
                t_raw = self._per_cam_slider_to_ts(master, v)
                frame_idx = max(0, bisect.bisect_right(self._cam_ts[master], t_raw) - 1)
                return self._cam_ts[master][frame_idx]
            return None
        if not self.items or self.current_idx is None:
            return None
        return self.items[self.current_idx].ts_ns

    def set_mark_a(self):
        ts = self._current_master_ts_ns()
        if ts is None: return
        self.mark_a_ns = ts
        self.tickbar.set_marks(self.mark_a_ns, self.mark_b_ns); self._update_range_ui()

    def set_mark_b(self):
        ts = self._current_master_ts_ns()
        if ts is None: return
        self.mark_b_ns = ts
        self.tickbar.set_marks(self.mark_a_ns, self.mark_b_ns); self._update_range_ui()

    def clear_marks(self):
        self.mark_a_ns = None; self.mark_b_ns = None
        self.tickbar.set_marks(None, None); self._update_range_ui()

    def _apply_marks_to_tickbar(self):
        """Validate stored marks against the current axis and update the tickbar.
        Marks outside the new axis are cleared so stale out-of-range marks don't show."""
        ax_min, ax_max = self.axis_min_ns, self.axis_max_ns
        if ax_max > ax_min:
            if self.mark_a_ns is not None and not (ax_min <= self.mark_a_ns <= ax_max):
                self.mark_a_ns = None
            if self.mark_b_ns is not None and not (ax_min <= self.mark_b_ns <= ax_max):
                self.mark_b_ns = None
        self.tickbar.set_marks(self.mark_a_ns, self.mark_b_ns)

    def _update_range_ui(self):
        ok = (self.mark_a_ns is not None) and (self.mark_b_ns is not None) and bool(self.items)
        self.btn_save_range.setEnabled(ok)

    # ================================================================ SAVE HELPERS
    def _show_copy_progress(self, total, text):
        self.prog.setVisible(True); self.prog.setRange(0, max(1, total)); self.prog.setValue(0)
        self.lbl_filename.setText(text)
        for w in [self.btn_open, self.btn_date, self.btn_save, self.btn_save_range,
                  self.btn_play, self.btn_stop, self.btn_prev, self.btn_next,
                  self.btn_set_a, self.btn_set_b, self.btn_clear_marks, self.btn_cal_cross]:
            w.setEnabled(False)

    def _hide_copy_progress(self):
        self.prog.setVisible(False); self.prog.setRange(0, 0)
        has = bool(self.items)
        self.btn_open.setEnabled(True); self.btn_date.setEnabled(True)
        self.btn_save.setEnabled(has and self.current_idx is not None)
        self.btn_play.setEnabled(has); self.btn_stop.setEnabled(False)
        self.btn_prev.setEnabled(has); self.btn_next.setEnabled(has)
        self.btn_set_a.setEnabled(has); self.btn_set_b.setEnabled(has)
        self.btn_clear_marks.setEnabled(has); self._update_range_ui()
        if self.current_idx is not None and self.items:
            self.lbl_filename.setText(f"File: {self.items[self.current_idx].path.name}")
        else:
            self.lbl_filename.setText("File: —")

    def _dst_name_with_prague_time(self, it):
        return replace_unix_ns_with_prague_in_filename(it.path, it.ts_ns)

    def save_around_current(self):
        if self.current_idx is None or not self.items: return
        if self._is_multi_cam():
            self._save_multicam_current(); return
        n = self.save_around_n_sb.value()
        i0 = max(0, self.current_idx - n)
        i1 = min(len(self.items) - 1, self.current_idx + n)
        total = i1 - i0 + 1

        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir))
        if not out_dir: return
        self._last_save_dir = Path(out_dir)
        self._show_copy_progress(total, f"Saving {total} frames around current…")
        task = SaveRangeTask(
            self.items[i0:i1+1], Path(out_dir), self._dst_name_with_prague_time,
            gradient_id=self.gradient_cb.currentIndex(),
            brighten=self.cb_bright.isChecked()
        )
        self._save_task = task
        a = self.items[i0].ts_ns
        b = self.items[i1].ts_ns
        task.signals.progress.connect(self._on_save_progress)
        task.signals.finished.connect(lambda s, e: self._on_save_finished(s, e, a, b))
        self.scan_pool.start(task)

    def save_current_with_overlay(self):
        """Uloží aktuální snímek s nakresleným overlayem (cross/circle/square)."""
        if self.current_idx is None or not self.items: return
        if self._is_multi_cam():
            self._save_multicam_current(); return
        if self.img_view._pix is None or self.img_view._pix.isNull():
            QMessageBox.information(self, "Save with overlay",
                "No image displayed."); return

        it = self.items[self.current_idx]
        stem = self._dst_name_with_prague_time(it)
        stem_no_ext = Path(stem).stem
        suggested = str(self._last_save_dir / f"{stem_no_ext}_overlay.png")
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save image with overlay", suggested, "PNG Images (*.png)")
        if not dst: return
        self._last_save_dir = Path(dst).parent

        # Vezmi aktuální pixmapu a nakresli overlay
        pix = self.img_view._pix.copy()
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = pix.width(), pix.height()

        if self.img_view.show_cross:
            if self.img_view.cross_pos_norm is not None:
                cx = int(self.img_view.cross_pos_norm.x() * w)
                cy = int(self.img_view.cross_pos_norm.y() * h)
            else:
                cx, cy = w // 2, h // 2
            sz = self._overlay_cross_size
            pen = QPen(self._overlay_cross_color); pen.setWidth(max(self._overlay_cross_thick, w // 500))
            painter.setPen(pen)
            sz = self._overlay_cross_size
            painter.drawLine(cx - sz, cy, cx + sz, cy)
            painter.drawLine(cx, cy - sz, cx, cy + sz)

        if self.img_view.show_circle and self.img_view.circle_center_norm is not None:
            cx = int(self.img_view.circle_center_norm.x() * w)
            cy = int(self.img_view.circle_center_norm.y() * h)
            if self.img_view.circle_rx_norm is not None:
                rx = int(self.img_view.circle_rx_norm * w)
                ry = int(self.img_view.circle_ry_norm * h)
            else:
                r = int(self.img_view.circle_r_norm * min(w, h))
                rx = ry = r
            pen = QPen(self._overlay_circle_color); pen.setWidth(max(self._overlay_circle_thick, w // 500))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)

        if self.img_view.show_square and self.img_view.square_rect_norm is not None:
            ln, tn, rn, bn = self.img_view.square_rect_norm
            sx = int(ln * w); sy = int(tn * h)
            sw = int((rn - ln) * w); sh = int((bn - tn) * h)
            pen = QPen(self._overlay_square_color); pen.setWidth(max(self._overlay_square_thick, w // 500))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(sx, sy, sw, sh)
        if self.img_view.energy_text:
            from PySide6.QtGui import QFont
            bar_h = max(28, int(h * 0.045))
            bar_rect = QRect(0, h - bar_h, w, bar_h)
            painter.fillRect(bar_rect, QColor(255, 255, 255, 220))
            font = QFont()
            font.setPixelSize(max(10, bar_h - 12))
            painter.setFont(font)
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter,
                             self.img_view.energy_text)
        painter.end()

        energy_text = self._sf_energy_map.get(it.path.name, "")
        if energy_text:
            try:
                from PIL import Image as _PilImg, ImageDraw as _PilDraw, ImageFont as _PilFont
                import tempfile as _tf
                with _tf.NamedTemporaryFile(suffix=".png", delete=False) as _tmp:
                    _tmp_path = Path(_tmp.name)
                pix.save(str(_tmp_path))
                _img = _PilImg.open(_tmp_path)
                _bar_h = 40
                _new = _PilImg.new("RGB", (_img.width, _img.height + _bar_h), (255, 255, 255))
                _new.paste(_img.convert("RGB"), (0, 0))
                _draw = _PilDraw.Draw(_new)
                try:
                    _font = _PilFont.truetype("DejaVuSans.ttf", 14)
                except Exception:
                    _font = _PilFont.load_default()
                _draw.text((8, _img.height + 8), energy_text, fill=(0, 0, 0), font=_font)
                _new.save(dst)
                _tmp_path.unlink(missing_ok=True)
            except Exception as _e:
                if not pix.save(dst):
                    QMessageBox.critical(self, "Save failed", f"Could not save to {dst}")
                    return
        else:
            if not pix.save(dst):
                QMessageBox.critical(self, "Save failed", f"Could not save to {dst}")
                return
        _copy_metadata_into_png_bg(it.path, Path(dst),
                                   save_txt=self.cb_save_metadata_txt.isChecked())
        QMessageBox.information(self, "Saved",
            f"Saved with overlay.\nPrague Time: {fmt_prague_full_from_ns(it.ts_ns)}")

    def _get_cam_frame_for_save(self, cam_i: int):
        """Return (it, iv, cam_name) for a camera, or None if unavailable."""
        if cam_i >= len(self._cam_items):
            return None
        cam_items = self._cam_items[cam_i]
        if not cam_items:
            return None
        cam_idx = 0
        if hasattr(self, '_cam_current_idx') and cam_i < len(self._cam_current_idx):
            cam_idx = min(self._cam_current_idx[cam_i], len(cam_items) - 1)
        it = cam_items[cam_idx]
        iv = self._multi_grid.get_img_view(cam_i)
        cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else f"cam{cam_i}"
        return it, iv, cam_name

    def _render_cam_frame(self, it, iv, cam_name: str, out_dir: Path,
                          gradient_id: int, brighten: bool,
                          save_metadata_txt: bool = False) -> str | None:
        """Render and save one camera frame into out_dir. Returns error string or None."""
        has_overlay = iv is not None and (
            iv.show_cross or iv.show_circle or iv.show_square or bool(iv.energy_text))
        stem = Path(self._dst_name_with_prague_time(it)).stem
        ann_suffix = "_annotate" if has_overlay else ""

        if gradient_id == GRADIENT_ID_DEFAULT and not has_overlay:
            dst = out_dir / f"{stem}_{cam_name}{it.path.suffix}"
            try:
                shutil.copy2(it.path, dst)
            except Exception as e:
                return str(e)
        else:
            dst = out_dir / f"{stem}_{cam_name}{ann_suffix}.png"
            if has_overlay and iv is not None and iv._pix is not None and not iv._pix.isNull():
                pix = iv._pix.copy()
                painter = QPainter(pix)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                w, h = pix.width(), pix.height()
                if iv.show_cross:
                    cx = int(iv.cross_pos_norm.x() * w) if iv.cross_pos_norm else w // 2
                    cy = int(iv.cross_pos_norm.y() * h) if iv.cross_pos_norm else h // 2
                    sz = self._overlay_cross_size
                    pen = QPen(self._overlay_cross_color)
                    pen.setWidth(max(self._overlay_cross_thick, w // 500))
                    painter.setPen(pen)
                    painter.drawLine(cx - sz, cy, cx + sz, cy)
                    painter.drawLine(cx, cy - sz, cx, cy + sz)
                if iv.show_circle and iv.circle_center_norm is not None:
                    cx = int(iv.circle_center_norm.x() * w)
                    cy = int(iv.circle_center_norm.y() * h)
                    if iv.circle_rx_norm is not None:
                        rx = int(iv.circle_rx_norm * w); ry = int(iv.circle_ry_norm * h)
                    else:
                        r = int(iv.circle_r_norm * min(w, h)); rx = ry = r
                    pen = QPen(self._overlay_circle_color)
                    pen.setWidth(max(self._overlay_circle_thick, w // 500))
                    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
                if iv.show_square and iv.square_rect_norm is not None:
                    ln, tn, rn, bn = iv.square_rect_norm
                    sx = int(ln * w); sy = int(tn * h)
                    sw = int((rn - ln) * w); sh = int((bn - tn) * h)
                    pen = QPen(self._overlay_square_color)
                    pen.setWidth(max(self._overlay_square_thick, w // 500))
                    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(sx, sy, sw, sh)
                painter.end()
                if not pix.save(str(dst)):
                    return f"Could not save {dst.name}"
            else:
                img = load_image_scaled(it.path, SCRUB_MAX_SIDE, brighten, gradient_id)
                if img.isNull():
                    return f"Could not load {it.path.name}"
                if not QPixmap.fromImage(img).save(str(dst)):
                    return f"Could not save {dst.name}"
            _copy_metadata_into_png_bg(it.path, dst, save_txt=save_metadata_txt)
        return None

    def _save_multicam_current(self):
        """Save current frame for each selected camera into a chosen folder."""
        if not self._cam_items:
            return
        selected = self._multi_grid.selected_cam_indices()
        cam_indices = selected if selected else list(range(len(self._cam_items)))
        # Collect frames first so we know what we're saving
        frames = []
        for cam_i in cam_indices:
            result = self._get_cam_frame_for_save(cam_i)
            if result:
                frames.append((cam_i, *result))
        if not frames:
            QMessageBox.information(self, "Save", "No frames to save.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir))
        if not out_dir:
            return
        out_path = Path(out_dir)
        self._last_save_dir = out_path
        gradient_id = self.gradient_cb.currentIndex()
        brighten = self.cb_bright.isChecked()
        save_txt = self.cb_save_metadata_txt.isChecked()
        errors = []
        saved = []
        for cam_i, it, iv, cam_name in frames:
            err = self._render_cam_frame(it, iv, cam_name, out_path, gradient_id, brighten,
                                         save_metadata_txt=save_txt)
            if err:
                errors.append(f"{cam_name}: {err}")
            else:
                saved.append(cam_name)
        msg = f"Saved {len(saved)} frame(s) to:\n{out_dir}"
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors)
        QMessageBox.information(self, "Saved", msg)

    def save_current(self):
        if self._is_multi_cam():
            self._save_multicam_current(); return
        if self.current_idx is None or not self.items: return
        n_around = self.save_around_n_sb.value()
        if n_around > 0:
            self.save_around_current(); return
        it = self.items[self.current_idx]
        gradient_id = self.gradient_cb.currentIndex()
        save_txt = self.cb_save_metadata_txt.isChecked()
        has_overlay = (self.cb_save_overlay.isChecked() or bool(self.img_view.energy_text)
                       or self.img_view.show_cross or self.img_view.show_circle
                       or self.img_view.show_square)

        if gradient_id == GRADIENT_ID_DEFAULT:
            suggested = str(self._last_save_dir / self._dst_name_with_prague_time(it))
            dst, _ = QFileDialog.getSaveFileName(self, "Save image", suggested, f"Images (*{it.path.suffix})")
            if not dst: return
            self._last_save_dir = Path(dst).parent
            try:
                shutil.copy2(it.path, Path(dst))
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e)); return
            _copy_metadata_into_png_bg(it.path, Path(dst), save_txt=save_txt)
        else:
            stem_no_ext = Path(self._dst_name_with_prague_time(it)).stem
            suggested = str(self._last_save_dir / f"{stem_no_ext}.png")
            dst, _ = QFileDialog.getSaveFileName(self, "Save image", suggested, "PNG Images (*.png)")
            if not dst: return
            self._last_save_dir = Path(dst).parent
            brighten = self.cb_bright.isChecked()
            img = load_image_scaled(it.path, SCRUB_MAX_SIDE, brighten, gradient_id)
            if img.isNull():
                QMessageBox.critical(self, "Save failed", "Could not load image."); return
            if not img.save(dst):
                QMessageBox.critical(self, "Save failed", f"Could not save to {dst}"); return
            _copy_metadata_into_png_bg(it.path, Path(dst), save_txt=save_txt)

        # Also save annotate version alongside original if any overlay is active
        if has_overlay:
            dst_p = Path(dst)
            ann_dst = dst_p.parent / f"{dst_p.stem}_annotate.png"
            # render overlay onto current pixmap
            if self.img_view._pix is not None and not self.img_view._pix.isNull():
                pix = self.img_view._pix.copy()
                painter = QPainter(pix)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                w, h = pix.width(), pix.height()
                if self.img_view.show_cross:
                    cx = int(self.img_view.cross_pos_norm.x() * w) if self.img_view.cross_pos_norm else w // 2
                    cy = int(self.img_view.cross_pos_norm.y() * h) if self.img_view.cross_pos_norm else h // 2
                    sz = self._overlay_cross_size
                    pen = QPen(self._overlay_cross_color); pen.setWidth(max(self._overlay_cross_thick, w // 500))
                    painter.setPen(pen)
                    painter.drawLine(cx - sz, cy, cx + sz, cy)
                    painter.drawLine(cx, cy - sz, cx, cy + sz)
                if self.img_view.show_circle and self.img_view.circle_center_norm is not None:
                    cx = int(self.img_view.circle_center_norm.x() * w)
                    cy = int(self.img_view.circle_center_norm.y() * h)
                    if self.img_view.circle_rx_norm is not None:
                        rx = int(self.img_view.circle_rx_norm * w); ry = int(self.img_view.circle_ry_norm * h)
                    else:
                        r = int(self.img_view.circle_r_norm * min(w, h)); rx = ry = r
                    pen = QPen(self._overlay_circle_color); pen.setWidth(max(self._overlay_circle_thick, w // 500))
                    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
                if self.img_view.show_square and self.img_view.square_rect_norm is not None:
                    ln, tn, rn, bn = self.img_view.square_rect_norm
                    sx = int(ln * w); sy = int(tn * h)
                    sw = int((rn - ln) * w); sh = int((bn - tn) * h)
                    pen = QPen(self._overlay_square_color); pen.setWidth(max(self._overlay_square_thick, w // 500))
                    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(sx, sy, sw, sh)
                if self.img_view.energy_text:
                    from PySide6.QtGui import QFont as _QFont
                    bar_h = max(28, int(h * 0.045))
                    bar_rect = QRect(0, h - bar_h, w, bar_h)
                    painter.fillRect(bar_rect, QColor(255, 255, 255, 220))
                    font = _QFont(); font.setPixelSize(max(10, bar_h - 12))
                    painter.setFont(font); painter.setPen(QColor(0, 0, 0))
                    painter.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter, self.img_view.energy_text)
                painter.end()
                pix.save(str(ann_dst))
                _copy_metadata_into_png_bg(it.path, ann_dst, save_txt=save_txt)

        msg = f"Saved.\nPrague Time: {fmt_prague_full_from_ns(it.ts_ns)}"
        if has_overlay:
            msg += f"\n+ annotate: {Path(dst).parent / (Path(dst).stem + '_annotate.png')}"
        QMessageBox.information(self, "Saved", msg)

    def _save_multicam_range(self):
        """Save all frames in mark A–B range for each selected camera into a folder."""
        if self.mark_a_ns is None or self.mark_b_ns is None:
            QMessageBox.information(self, "Save range", "Set both From and To first."); return
        a, b = self.mark_a_ns, self.mark_b_ns
        if b < a: a, b = b, a
        selected = self._multi_grid.selected_cam_indices()
        cam_indices = selected if selected else list(range(len(self._cam_items)))
        # Count total frames across cameras for warning
        total = 0
        for cam_i in cam_indices:
            if cam_i >= len(self._cam_items): continue
            ts = self._cam_ts[cam_i] if (hasattr(self, '_cam_ts') and cam_i < len(self._cam_ts)) else []
            i0 = bisect.bisect_left(ts, a)
            i1 = bisect.bisect_right(ts, b)
            total += max(0, i1 - i0)
        if total == 0:
            QMessageBox.information(self, "Save range", "No frames inside From..To."); return
        if total >= SAVE_RANGE_WARN_COUNT:
            reply = QMessageBox.warning(self, "Large range",
                f"{total} files selected across {len(cam_indices)} camera(s).\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes: return
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir))
        if not out_dir: return
        out_path = Path(out_dir)
        self._last_save_dir = out_path
        gradient_id = self.gradient_cb.currentIndex()
        brighten = self.cb_bright.isChecked()
        save_txt = self.cb_save_metadata_txt.isChecked()
        saved_total = 0
        for cam_i in cam_indices:
            if cam_i >= len(self._cam_items): continue
            cam_items = self._cam_items[cam_i]
            ts = self._cam_ts[cam_i] if (hasattr(self, '_cam_ts') and cam_i < len(self._cam_ts)) else [it.ts_ns for it in cam_items]
            cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else f"cam{cam_i}"
            i0 = bisect.bisect_left(ts, a)
            i1 = bisect.bisect_right(ts, b)
            for it in cam_items[i0:i1]:
                err = self._render_cam_frame(it, None, cam_name, out_path, gradient_id, brighten,
                                             save_metadata_txt=save_txt)
                if err is None:
                    saved_total += 1
        QMessageBox.information(self, "Saved", f"Saved {saved_total} frame(s) to:\n{out_dir}")

    def save_range(self):
        if not self.items or self.mark_a_ns is None or self.mark_b_ns is None:
            QMessageBox.information(self, "Save range", "Set both From and To first."); return
        if self._is_multi_cam():
            self._save_multicam_range(); return
        a, b = self.mark_a_ns, self.mark_b_ns
        if b < a: a, b = b, a
        i0 = max(0, bisect.bisect_left(self.ts_list, a))
        i1 = min(len(self.items) - 1, bisect.bisect_right(self.ts_list, b) - 1)
        if i0 > i1: QMessageBox.information(self, "Save range", "No frames inside From..To."); return
        total = i1 - i0 + 1
        if total >= SAVE_RANGE_WARN_COUNT:
            reply = QMessageBox.warning(self, "Large range",
                f"{total} files selected.\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes: return
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir))
        if not out_dir: return
        self._last_save_dir = Path(out_dir)
        self._show_copy_progress(total, "Saving range…")
        iv = self.img_view
        has_overlay = (self.cb_save_overlay.isChecked()
                       or iv.show_cross or iv.show_circle or iv.show_square)
        overlay_params = None
        if has_overlay:
            overlay_params = {
                'show_cross': iv.show_cross,
                'cross_pos_norm': iv.cross_pos_norm,
                'cross_size': self._overlay_cross_size,
                'cross_color': self._overlay_cross_color,
                'cross_thick': self._overlay_cross_thick,
                'show_circle': iv.show_circle,
                'circle_center_norm': iv.circle_center_norm,
                'circle_rx_norm': iv.circle_rx_norm,
                'circle_ry_norm': getattr(iv, 'circle_ry_norm', None),
                'circle_r_norm': getattr(iv, 'circle_r_norm', 0.1),
                'circle_color': self._overlay_circle_color,
                'circle_thick': self._overlay_circle_thick,
                'show_square': iv.show_square,
                'square_rect_norm': iv.square_rect_norm,
                'square_color': self._overlay_square_color,
                'square_thick': self._overlay_square_thick,
            }
        task = SaveRangeTask(
            self.items[i0:i1+1], Path(out_dir), self._dst_name_with_prague_time,
            gradient_id=self.gradient_cb.currentIndex(),
            brighten=self.cb_bright.isChecked(),
            overlay_params=overlay_params,
        )
        task.save_txt = self.cb_save_metadata_txt.isChecked()
        self._save_task = task
        task.signals.progress.connect(self._on_save_progress)
        task.signals.finished.connect(lambda s, e: self._on_save_finished(s, e, a, b))
        self.scan_pool.start(task)

    def _on_save_progress(self, done, total, filename):
        self.prog.setValue(done); self.lbl_filename.setText(f"Saving {done}/{total}  |  {filename}")

    def _on_save_finished(self, saved, errors, a, b):
        self._save_task = None; self._hide_copy_progress()
        QMessageBox.information(self, "Save range",
            f"Saved {saved} files.\nErrors: {errors}\n\n"
            f"From: {fmt_prague_full_from_ns(a)}\nTo: {fmt_prague_full_from_ns(b)}")


# ================================================================== MAIN
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=None)
    args = ap.parse_args()

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("""
    QWidget  { background: #f3f3f3; color: #111; }
    QLabel   { background: transparent; }
    QPushButton { padding: 5px 8px; }
    QComboBox   { padding: 3px 6px; }
    QProgressBar { background: #fff; }
    QToolTip { background: #ffffcc; color: #111; border: 1px solid #aaa; padding: 4px; }
    """)

    w = Viewer()
    w.setWindowTitle("Image Slider")
    w.resize(1200, 620)
    w.show()

    if args.folder:
        w.open_folder_path(Path(args.folder))

    app.exec()